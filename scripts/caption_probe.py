"""Is the vision model good enough to caption training images?

The same model was 0-for-12 on framing (v1.241) and its answer was withdrawn
rather than trusted. But framing is a GEOMETRIC judgement and this is a
DESCRIPTION task, which is a different competence. So the question gets measured
instead of assumed, twice, before a single caption is rewritten.

WHAT IT MEASURES
    Each image is described TWICE, in separate calls. If the model cannot agree
    with itself about what a man is wearing, it cannot be trusted to tell the
    trainer. Agreement is scored on the CONTENT words of the two replies
    (Jaccard over nouns/adjectives, stopwords dropped), because two honest
    descriptions of the same shirt rarely use identical wording.

    It also flags any row where either pass reports bare skin -- the failure that
    put a bare-chested image into dorian-v1 with a caption that never mentioned
    it.

NON-DESTRUCTIVE. Reads the dataset, writes nothing back. The report lands in
scripts\\_diag\\caption_probe.json so the actual sentences can be read.

RUN
    scripts\\caption_probe.py --ds dorian-v1-b1966f
    scripts\\caption_probe.py --ds redv1-bca382 --limit 8
"""
from __future__ import annotations

import argparse
import base64
import json
import re
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

SYSTEM = ("You write short factual captions for image-model training data. You report only "
          "clothing, background and lighting that you can see.")
PROMPT = """Look at this photograph and list ONLY:
1. the clothing and accessories the person is wearing,
2. what is visible in the background.

Reply as one short comma-separated phrase, for example:
"a navy blue hoodie and jeans, a brick wall behind him"

Write nothing about the person's face, hair, body build, height, weight, age or sex —
those belong to the character itself and are deliberately left out of these captions."""

STOP = set("a an the and or of in on at with his her its is are was were he she they "
           "him them behind front background wearing wears visible there this that "
           "some very man woman person image photo picture shot".split())

# Words that mean the caption is reporting exposed skin. A training set for a
# clothed character should contain none of these, and dorian-v1 contained one.
BARE = ("shirtless", "bare-chested", "bare chest", "topless", "nude", "naked",
        "underwear", "briefs", "boxers", "no shirt", "bare torso", "undressed")


def words(s: str) -> set:
    return {w for w in re.findall(r"[a-z]{3,}", (s or "").lower()) if w not in STOP}


def jaccard(a: set, b: set) -> float:
    return len(a & b) / len(a | b) if (a or b) else 1.0


def describe(host: str, model: str, blob: bytes, temp: float) -> str:
    body = json.dumps({
        "model": model,
        "messages": [{"role": "system", "content": SYSTEM},
                     {"role": "user", "content": PROMPT,
                      "images": [base64.b64encode(blob).decode("ascii")]}],
        "stream": False,
        "options": {"temperature": temp},
    }).encode("utf-8")
    req = urllib.request.Request(f"{host}/api/chat", data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=300) as r:
        out = json.loads(r.read().decode("utf-8", "replace"))
    return ((out.get("message") or {}).get("content") or "").strip().strip('"').split("\n")[0]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ds", required=True)
    ap.add_argument("--host", default="http://192.168.12.176:11434")
    ap.add_argument("--model", default="qwen2.5vl:7b")
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()

    from backend.api.lora import _item_path, _read_ds

    ds = _read_ds(a.ds)
    rows = [it for it in ds["items"] if _item_path(a.ds, it["id"]).exists()]
    if a.limit:
        rows = rows[:a.limit]
    if not rows:
        print(f"no rendered images in {a.ds}")
        return 1
    print(f"{a.ds}: describing {len(rows)} image(s) TWICE on {a.model}\n"
          f"one server, strictly sequential — this takes a few minutes\n")

    out, bare, fails = [], [], 0
    print(f"{'#':>3} {'agree':>6}  caption (pass A)")
    print("-" * 100)
    for n, it in enumerate(rows, 1):
        fp = _item_path(a.ds, it["id"])
        blob = fp.read_bytes()
        try:
            # Two passes at the SAME low temperature the caption route uses.
            # Different answers here are the model's own instability, not a
            # sampling artefact I introduced.
            pa = describe(a.host, a.model, blob, 0.2)
            pb = describe(a.host, a.model, blob, 0.2)
        except Exception as e:  # noqa: BLE001
            print(f"{n:>3}  FAILED  {type(e).__name__}: {e}")
            fails += 1
            continue
        ag = jaccard(words(pa), words(pb))
        low = (pa + " " + pb).lower()
        hit = [w for w in BARE if w in low]
        rec = {"n": n, "id": it["id"], "file": fp.name, "framing": it.get("framing"),
               "angle": it.get("angle"), "agreement": round(ag, 3),
               "pass_a": pa, "pass_b": pb, "bare": hit,
               "our_caption": it.get("caption") or ""}
        out.append(rec)
        if hit:
            bare.append(rec)
        mark = "  <-- BARE SKIN" if hit else ""
        print(f"{n:>3} {ag:>6.2f}  {pa[:78]}{mark}")

    if out:
        ags = sorted(r["agreement"] for r in out)
        med = ags[len(ags) // 2]
        print(f"\n=== self-agreement across two passes ===")
        print(f"  median {med:.3f}   min {ags[0]:.3f}   max {ags[-1]:.3f}")
        print(f"  rows below 0.40: {sum(1 for x in ags if x < 0.40)} of {len(ags)}")
        if med >= 0.55:
            print("  The model reproduces its own description. Usable as caption input.")
        elif med >= 0.40:
            print("  Partial agreement — usable for CLOTHING NOUNS only, not whole "
                  "sentences. Extract garments, discard prose.")
        else:
            print("  The model does not agree with itself. Do NOT feed this to the "
                  "trainer — this is the v1.241 failure again.")
    if bare:
        print(f"\n=== rows reporting BARE SKIN ({len(bare)}) ===")
        for r in bare:
            print(f"  {r['file']}  ({r['framing']}/{r['angle']})  {r['bare']}")
            print(f"      seen: {r['pass_a'][:90]}")
            print(f"      ours: {r['our_caption'][:90]}")
        print("  Look at these images before believing or disbelieving the model.")
    else:
        print("\n  no row in either pass reported bare skin")
    if fails:
        print(f"\n  {fails} row(s) failed outright")

    p = Path(__file__).resolve().parent / "_diag" / f"caption_probe_{a.ds}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"ds": a.ds, "model": a.model, "rows": out}, indent=2), "utf-8")
    print(f"\nwrote {p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
