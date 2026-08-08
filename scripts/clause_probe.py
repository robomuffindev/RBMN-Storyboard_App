"""What does `garment_clause` actually do to the 40 real descriptions?

Before it changes a caption, print every before/after and the ones it empties.
An empty result is the failure mode that matters: it means the caption loses the
clothing it was supposed to gain.

RUN
    scripts\\clause_probe.py --ds dorian-v1-b1966f
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ds", required=True)
    a = ap.parse_args()

    import importlib.util as u
    spec = u.spec_from_file_location(
        "w", Path(__file__).resolve().parents[1] / "backend" / "services" / "wardrobe.py")
    w = u.module_from_spec(spec)
    spec.loader.exec_module(w)

    from backend.api.lora import _read_ds

    ds = _read_ds(a.ds)
    empty, kept, unchanged = [], 0, 0
    for it in ds["items"]:
        seen = (it.get("seen_clothing") or "").strip()
        if not seen:
            continue
        g = w.garment_clause(seen)
        if not g:
            empty.append((it["id"], seen))
        elif g.strip() == seen.strip():
            unchanged += 1
        else:
            kept += 1
        print(f"{it['id']}")
        print(f"   was: {seen}")
        print(f"   now: {g or '(EMPTY)'}")
    print(f"\ntrimmed {kept}, unchanged {unchanged}, EMPTIED {len(empty)}")
    for iid, s in empty:
        print(f"  {iid}: nothing worn found in -> {s}")
    if empty:
        print("\nAn emptied row is a caption that loses its clothing. Fix the rule "
              "before shipping it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
