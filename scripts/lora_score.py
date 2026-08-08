"""Score the already-rendered LoRA test grid. Split from lora_test.py so a
dead backend doesn't cost a re-render: images are on disk, this only scores."""
from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "_diag" / "lora_test"
APP = "http://127.0.0.1:8899"
CHAR = "dorian"
ORDER = ["trig_default_10", "trig_default_08", "trig_suit_10",
         "trig_suit_08", "notrig_10", "control_nolora"]


def jget(url, timeout=60.0):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def bget(url, timeout=300.0):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return r.read()


def main() -> int:
    global OUT, CHAR
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--char", default=CHAR)
    a = ap.parse_args()
    CHAR = a.char
    OUT = ROOT / "_diag" / f"lora_test_{CHAR}"
    if not OUT.exists():
        OUT = ROOT / "_diag" / "lora_test"      # dorian's original grid location
    from backend.services import likeness as lk
    if not lk.available():
        print(f"ArcFace unavailable: {lk.health().get('error')}")
        return 2
    ch = jget(f"{APP}/api/klein3/characters/{CHAR}")
    embs, labels = [], []
    for r in (ch.get("refs") or []):
        tag = str(r.get("tag") or "").lower()
        if tag not in ("front", "face"):
            continue
        fp = OUT / f"_ref_{tag}_{r['id']}.png"
        fp.write_bytes(bget(APP + r["url"]))
        e = lk.embed(fp)
        if e is not None:
            embs.append(e)
            labels.append(f"{tag} reference")
    if not embs:
        print("no usable reference face")
        return 1
    print(f"baselines: {', '.join(labels)}\n")
    print(f"{'variant':<18} {'likeness':>9}  verdict")
    print("-" * 44)
    rows = []
    for key in ORDER:
        fp = OUT / f"{key}.png"
        if not fp.exists():
            print(f"{key:<18}   missing")
            continue
        s = lk.score(fp, embs)
        v = lk.verdict(s)[0] if s is not None else "no face"
        rows.append({"variant": key, "score": None if s is None else round(s, 4),
                     "verdict": v})
        bar = "#" * int((s or 0) * 40)
        sv = f"{s:.4f}" if s is not None else "  --  "
        print(f"{key:<18} {sv:>9}  {v:<12} {bar}")
    (OUT / "scores.json").write_text(json.dumps(rows, indent=2), "utf-8")
    print(f"\nwrote {OUT / 'scores.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
