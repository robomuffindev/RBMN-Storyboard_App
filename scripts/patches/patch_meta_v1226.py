"""v1.226 — VERSION, pyproject, CHANGELOG."""
import sys
from pathlib import Path
ROOT = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
v = ROOT / "VERSION"
assert v.read_text("utf-8").strip() == "1.225.0", v.read_text("utf-8")
v.write_text("1.226.0\n", "utf-8")
pp = ROOT / "pyproject.toml"
s = pp.read_text("utf-8")
assert s.count('version = "1.225.0"') == 1
pp.write_text(s.replace('version = "1.225.0"', 'version = "1.226.0"', 1), "utf-8")
ENTRY = '''## v1.226.0 -- "QC pass" looked like it did nothing, because it did nothing (2026-08-05)

The button posted `{}`, so `overwrite` defaulted to false, and the route only targets images with
no qc yet:

    targets = [... and (body.overwrite or not it.get("qc"))]
    if not targets: return {"started": False, "note": "nothing to check ..."}

On a set already checked once that is EVERY image, so the call returned instantly having done
nothing. The explanation was returned -- and rendered at the very top of the page, hundreds of
pixels above the button, below the fold on a scrolled view. A silent no-op with off-screen
feedback.

  1. **QC pass re-checks.** That is what the words mean, and what every other button in that bar
     already did (Caption all has always passed `overwrite: true`).
  2. **Feedback on the button itself.** An `acting` state disables the bar and swaps the label the
     moment the request leaves, closing the gap before the progress banner appears.
  3. **Messages render next to the action bar**, not only at the page header. A reply you cannot
     see is not feedback. `started: false` is now prefixed and shown inline, because "I had
     nothing to do" is the single most confusing outcome to receive silently.
  4. The progress banner never renders blank -- it shows "starting..." and `done/total` before the
     first per-image result lands.

Also added a **Likeness** button to the same bar: ArcFace scoring is CPU-only, needs no vision
model and no worker, and until now was reachable only from a script.

'''
cl = ROOT / "CHANGELOG.md"
s = cl.read_text("utf-8")
assert s.startswith("## v1.225.0"), s[:40]
cl.write_text(ENTRY + s, "utf-8")
print("VERSION 1.226.0 · pyproject · CHANGELOG")
