"""v1.222 — VERSION, pyproject, CHANGELOG."""
import sys
from pathlib import Path
ROOT = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
v = ROOT / "VERSION"
assert v.read_text("utf-8").strip() == "1.221.0", v.read_text("utf-8")
v.write_text("1.222.0\n", "utf-8")
pp = ROOT / "pyproject.toml"
s = pp.read_text("utf-8")
assert s.count('version = "1.221.0"') == 1
pp.write_text(s.replace('version = "1.221.0"', 'version = "1.222.0"', 1), "utf-8")

ENTRY = '''## v1.222.0 -- a re-plan destroyed 33 rendered images (2026-08-05)

**What happened.** `ab_tq_base.ps1` set `options.tq_base` by POSTing the dataset's existing
options back with one key added. `dataset_plan` REPLACED `ds["options"]` wholesale, `preset` did
not survive the round-trip, and `_plan_opts` fell back to `"balanced"`. His dataset was
`face_heavy`.

face_heavy vs balanced at 40 images share **exactly 7 of 40 slots** -- which is precisely the
"images still rendered after re-plan: 7 of 40" he saw. 33 rendered images were deleted, and the
A/B then measured the wrong rows: of the 14 ids it re-rendered, only 4 were still three-quarter.
**The reported "14 misses -> 5" is not a valid comparison and has been withdrawn.**

The script was the trigger. The route was the loaded gun.

### Three fixes

1. **`options` MERGES instead of replacing.** Omitting a key now means "leave it alone", which
   is what every caller already assumed it meant.
2. **`preset` is sticky.** It lives both at `ds["preset"]` and inside `options`; whichever the
   caller supplies wins, and if neither does, the stored one survives. It can no longer silently
   revert to "balanced".
3. **A destructive re-plan is REFUSED.** `_plan_impact` computes what would be discarded before
   anything is written; if rendered images would be lost the route 409s with the count, the
   angle changes, and the likely cause, and requires `force: true`. Deleting GPU time should
   require saying so out loud.

Plus `POST /datasets/{id}/plan-preview` -- read-only, writes nothing, deletes nothing. The only
way to find out what a re-plan would do used to be to do it.

`test_v1222.py` reproduces the incident exactly: 40 images planned face_heavy, all rendered, a
caller sends options back without `preset` -> asserts 33 discarded / 7 kept, then asserts the fix
brings it to 0 discarded / 40 kept. Nine lora suites pass, md5 f0a0e96471af8ed284d38d8511f7bc36.

### Still unanswered

Whether `tq_base=front` actually fixes three-quarter angles. The evidence so far is suggestive
but contaminated: of the 4 rows that remained three-quarter, 2 of 4 passed (against 0 of 14
before), and identity on the re-rendered set rose 0.477 -> 0.646 because most moved to the front
base. `scripts/repair_dataset.ps1` restores the intended preset and re-runs it cleanly.

'''
cl = ROOT / "CHANGELOG.md"
s = cl.read_text("utf-8")
assert s.startswith("## v1.221.0"), s[:40]
cl.write_text(ENTRY + s, "utf-8")
print("VERSION 1.222.0 · pyproject · CHANGELOG")
