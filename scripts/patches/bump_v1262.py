"""v1.262 bump."""
import sys
from pathlib import Path

ROOT = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
OLD, NEW = "1.261.0", "1.262.0"

v = ROOT / "VERSION"
assert v.read_text("utf-8").strip() == OLD, v.read_text("utf-8")
v.write_text(NEW + "\n", "utf-8")

p = ROOT / "pyproject.toml"
s = p.read_text("utf-8")
assert s.count(f'version = "{OLD}"') == 1
p.write_text(s.replace(f'version = "{OLD}"', f'version = "{NEW}"', 1), "utf-8")

ENTRY = """## v1.262.0 -- the check repair was about to erase (2026-08-06)

Backend only.

v1.261 shipped the wardrobe check as its own route and it flagged twelve rows of dorian-v1. Then
I read the repair loop before running it. Repair re-renders every flagged image and re-runs QC,
and QC **replaces `x["qc"]` wholesale**. So the sequence I was one command away from running was:

    wardrobe-check      ->  12 rows flagged "he is undressed"
    repair              ->  12 rows re-rendered (dressed, per v1.260)
    QC inside repair    ->  x["qc"] = {...}      <- the bare verdict is gone
    repair reports      ->  "0 still flagged"

...whether or not the re-render actually put clothes on him. A check a later step silently erases
is worse than no check, because it produces a clean number. This is the same failure shape as
v1.248 (the repair loop fixed angles and broke faces because it did not re-measure everything
each round) and I nearly shipped it again.

The two vision passes now run **inside `_qc_blocking`**, on the same worker thread against the
same already-loaded bytes, and `bare` participates in `ok` alongside `one_person`, `artifacts`,
`same_person`, `framing_ok` and `crop_ok`. `flags.get("bare") is not True` -- unmeasured never
fails an image, the same contract every other measured flag has. `seen_clothing` is written
outside `qc` so a re-check does not throw away the description the caption pass reuses.

Cost: two extra vision calls per image, about 4.4s per image (measured: 40 images in 175s on the
standalone route). All local, one server, strictly sequential.

The standalone `POST /wardrobe-check` route stays. It re-audits a set without paying for a full
QC pass, which is what you want on a dataset that is already checked.

"""

c = ROOT / "CHANGELOG.md"
s = c.read_text("utf-8")
assert not s.startswith("## v1.262")
c.write_text(ENTRY + s, "utf-8")
print(f"bumped {OLD} -> {NEW}")
