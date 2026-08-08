"""Which checkpoint actually looks most like him?  ArcFace, not opinion.

The first training run's loss said epoch 27 was best (0.0282) and the final
epoch worse (0.0367), so I told Lorenzo to use 27. Then I looked at the two
previews and could not tell them apart. Loss is a training statistic; it is not
a measure of likeness, and I had quietly treated it as one.

This scores every per-epoch PREVIEW against the character's own references with
the same ArcFace path the dataset QC uses. It answers the question the loss
curve only gestures at: at which epoch does the model most look like him, and
does it drift afterwards.

Everything is CPU. Previews and references come over HTTP -- the helper for the
previews, the app's own API for the reference list -- so nothing has to be
copied by hand.

RUN
    scripts\\checkpoint_score.py --run 20260806-003544-e0f7 --char dorian
    scripts\\checkpoint_score.py --run <id> --char <slug> --prompt 00
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

APP = "http://127.0.0.1:8899"


def jget(url: str, timeout: float = 120.0):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def bget(url: str, timeout: float = 300.0) -> bytes:
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return r.read()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--helper", default="http://192.168.12.202:8765")
    ap.add_argument("--token", default="49ae12e57c0949158b2efb4edfb0ac49")
    ap.add_argument("--run", required=True)
    ap.add_argument("--char", required=True)
    ap.add_argument("--prompt", default="00", help="which sample prompt index to score")
    a = ap.parse_args()

    from backend.services import likeness as lk
    if not lk.available():
        print(f"ArcFace unavailable: {lk.health().get('error')}")
        return 2

    tmp = Path(tempfile.mkdtemp(prefix="ckpt_"))

    # ── baselines: the character's OWN references, never its renders ─────
    ch = jget(f"{APP}/api/klein3/characters/{a.char}")
    embs, labels = [], []
    for r in (ch.get("refs") or []):
        tag = str(r.get("tag") or "").lower()
        if tag not in ("front", "face"):
            continue
        fp = tmp / f"ref_{tag}_{r['id']}.png"
        fp.write_bytes(bget(APP + r["url"]))
        e = lk.embed(fp)
        if e is not None:
            embs.append(e)
            labels.append(f"{tag} reference")
    if not embs and ch.get("active_base_url"):
        fp = tmp / "ref_base.png"
        fp.write_bytes(bget(APP + ch["active_base_url"]))
        e = lk.embed(fp)
        if e is not None:
            embs.append(e)
            labels.append("active base")
    if not embs:
        print(f"no usable reference with a detectable face for '{a.char}'")
        return 1
    print(f"baselines: {', '.join(labels)}")

    # ── the previews ─────────────────────────────────────────────────────
    info = jget(f"{a.helper}/runs/{a.run}?token={a.token}&kind=image")
    # v1.265: Fizgig's output folder is keyed on the DATASET, so every run of the
    # same dataset shares it. Scoring the union of two runs produced epochs 1-40
    # for a 22-epoch run, with 12-22 listed twice, and recommended a checkpoint
    # that may have come from the dataset this run exists to replace.
    t0s, t1s = str(info.get("started") or ""), str(info.get("finished") or "")
    shots, stale = [], 0
    for x in info.get("artifacts") or []:
        m = re.search(r"_e(\d{6})_(\d\d)_", x["name"])
        if not m or m.group(2) != a.prompt:
            continue
        mod = str(x.get("modified") or "")
        # Inclusive window, string compare on ISO timestamps of the same shape.
        # A preview is written DURING the run by definition.
        if t0s and mod and (mod < t0s or (t1s and mod > t1s)):
            stale += 1
            continue
        shots.append((int(m.group(1)), x["name"]))
    shots.sort()
    if stale:
        print(f"  IGNORED {stale} preview(s) outside this run's window "
              f"({t0s} -> {t1s}) — another run wrote into the same output folder.")
        print(f"  Weights in that folder are mixed too: any checkpoint numbered "
              f"above {shots[-1][0] if shots else 0} is from the OTHER run.\n")
    if len(shots) != len({e for e, _ in shots}):
        print("  WARNING: duplicate epoch numbers survived the filter — the two "
              "runs overlap in time. Trust nothing below.\n")
    if not shots:
        print(f"no previews for prompt {a.prompt}")
        return 1
    print(f"scoring {len(shots)} previews (prompt {a.prompt}) from run {a.run}\n")

    rows = []
    print(f"{'epoch':>6} {'likeness':>9}  {'verdict':<12}")
    print("-" * 34)
    for ep, name in shots:
        fp = tmp / f"e{ep:03d}.png"
        try:
            fp.write_bytes(bget(f"{a.helper}/runs/{a.run}/artifacts/{name}?token={a.token}"))
        except Exception as e:  # noqa: BLE001
            print(f"{ep:>6}  fetch failed: {e}")
            continue
        s = lk.score(fp, embs)
        v = lk.verdict(s)[0] if s is not None else "no face"
        rows.append({"epoch": ep, "score": None if s is None else round(s, 4),
                     "verdict": v, "file": name})
        bar = "#" * int((s or 0) * 50)
        print(f"{ep:>6} {('%.4f' % s) if s is not None else '   -   ':>9}  {v:<12} {bar}")
        try:
            fp.unlink()
        except OSError:
            pass

    scored = [r for r in rows if r["score"] is not None]
    if scored:
        best = max(scored, key=lambda r: r["score"])
        last = scored[-1]
        first = scored[0]
        print(f"\n  first  epoch {first['epoch']:>3}  {first['score']:.4f}")
        print(f"  BEST   epoch {best['epoch']:>3}  {best['score']:.4f}")
        print(f"  last   epoch {last['epoch']:>3}  {last['score']:.4f}")
        tail = [r["score"] for r in scored[-8:]]
        if len(tail) >= 4:
            drift = max(tail) - min(tail)
            print(f"  last 8 epochs span {drift:.4f} — "
                  f"{'flat, any of them will do' if drift < 0.04 else 'still moving'}")
        if best["epoch"] != last["epoch"] and best["score"] - last["score"] > 0.03:
            print(f"\n  Use epoch {best['epoch']}: it beats the final checkpoint by "
                  f"{best['score'] - last['score']:.4f}.")
        else:
            print("\n  No meaningful likeness difference between the best and the "
                  "final checkpoint. Pick on the pictures, not the number.")

    out = Path(__file__).resolve().parent / "_diag" / "checkpoint_scores.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"run": a.run, "char": a.char, "prompt": a.prompt,
                               "baselines": labels, "rows": rows}, indent=2), "utf-8")
    print(f"\nwrote {out}")
    print("Tell Claude 'checkpoint scores are ready'.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
