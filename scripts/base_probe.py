"""Which file does each base_mode actually resolve to?

v1.260 changed a DEFAULT, and a default is only worth anything if it changes
which bytes get fed to the renderer. This prints, per view, the file `auto`
picks and the file `dressed` picks, so the claim "the stripped base was winning"
is a measurement rather than a story about a code path.

RUN
    scripts\\base_probe.py --char dorian
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
    ap.add_argument("--char", required=True)
    a = ap.parse_args()

    from backend.api.klein3 import _load as _load_char, _base_for_view

    char = _load_char(a.char)
    print(f"character {a.char}   char-level base_mode: "
          f"{char.get('base_mode') or '(unset)'}\n")
    print(f"{'view':<7} {'mode':<9} {'file':<26} {'label'}")
    print("-" * 78)
    diff = []
    for view in ("front", "left", "right", "back"):
        picks = {}
        for mode in ("auto", "dressed", "stripped"):
            try:
                fp, lbl = _base_for_view(a.char, char, view, mode)
            except Exception as e:  # noqa: BLE001
                fp, lbl = None, f"{type(e).__name__}: {e}"
            picks[mode] = (fp, lbl)
            print(f"{view:<7} {mode:<9} {(fp.name if fp else '-'):<26} {lbl}")
        if picks["auto"][0] != picks["dressed"][0]:
            diff.append(view)
        print()
    if diff:
        print(f"AUTO and DRESSED disagree on: {', '.join(diff)}")
        print("Every row rendering those views under the old default started from "
              "a different image than it does now.")
    else:
        print("auto and dressed resolve to the same file for every view — on THIS "
              "character the default change is a no-op, and the fix matters only "
              "for characters that have been stripped.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
