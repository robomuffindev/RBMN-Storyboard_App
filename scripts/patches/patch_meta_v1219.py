"""v1.219 — VERSION, pyproject, CHANGELOG for the audit fixes."""
import sys
from pathlib import Path

ROOT = Path(sys.argv[1] if len(sys.argv) > 1 else ".")

v = ROOT / "VERSION"
assert v.read_text("utf-8").strip() == "1.218.0", v.read_text("utf-8")
v.write_text("1.219.0\n", "utf-8")

pp = ROOT / "pyproject.toml"
s = pp.read_text("utf-8")
assert s.count('version = "1.218.0"') == 1
pp.write_text(s.replace('version = "1.218.0"', 'version = "1.219.0"', 1), "utf-8")

ENTRY = '''## v1.219.0 -- AUDIT: four defects, all mine (2026-08-05)

He asked me to audit my own claims after finding no UI for a feature I had described as done.
This is what the audit found. Every item is something I said or implied that was not true of the
running code.

### 1. The outfit feature was INERT through the API

`_build_plan` reads `opts["outfits"]`. Neither route put it there: create passed
`{**(body.options or {}), "preset": body.preset}` and re-plan passed `ds.get("options")`. So
**every row planned through the API came out with `outfit: None`** and the whole of v1.216 did
nothing. The dataset stored the wardrobe; the plan ignored it.

`test_v1216` passed the entire time because it called `_build_plan(104, {"outfits": WARDROBE})`
DIRECTLY -- a dict the route never constructs. **I tested the function and never the wiring**,
which is precisely the failure the suite existed to catch. `test_v1219` now drives the routes'
own option assembly and includes a regression proof that the old opts really did produce no
outfits.

Fix: `_plan_opts(ds)` -- ONE builder, used by both routes, so a new planner input cannot be
wired into one caller and silently missed in the other.

### 2. The set was never auto-sized

He chose "scale automatically with outfit count". `_suggested_count` was only ever RETURNED by
the outfits routes and never applied. With the UI's default 40 and eight outfits that is five
images each -- and measured, outfits then span only **2 of the 4 framings**, which is exactly
the clumping v1.216 claims to fix. The "every outfit spans all four framings" result I reported
was measured at 104 and is true there; it is **false at 40 and 60**, and 40 is what the UI sends.
So in practice the guarantee would not have held.

Fix: `count` is now `Optional` -- omitted means sized from the wardrobe. An explicit count still
wins, so the existing UI is unchanged. `_plan_warnings` says out loud when a wardrobe is spread
too thin, naming what goes wrong rather than just that the number is low.

### 3. The no-face counter was dead code

`identity_method` was set to `"arcface"` only when a score came back, while `_flag_summary`
counted `method == "arcface" AND score is None` -- unreachable by construction. "ArcFace ran and
found no face" (a back shot, correct) and "ArcFace never ran" (no model) looked identical.
Fix: the method records whether ArcFace RAN; a `None` score then means no face.

### 4. Same bug in the `/likeness` route

`q["identity_method"] = "arcface" if s is not None else "none"` -- same conflation, same fix.

### Also checked, and clean

- `backend/services/__init__.py` exists, and `likeness.py` imports only stdlib at module level,
  so `lora.py` imports fine with or without insightface. (Had this been wrong the app would not
  have started at all.)
- Every symbol in lora.py and likeness.py is reachable: 22 routed handlers, no unused functions
  or constants. klein3.py has one pre-existing unused helper, `_run_klein_edit_sync` (line 324),
  which predates this work.
- v1.217's `base_mode` IS applied -- stored on the dataset, honoured by `_render_jobs` and by the
  QC reference.
- v1.214's export additions are all written into the zip.

### Claims I overstated, corrected here rather than quietly

- I described the helper's test fixtures as "captured nvidia-smi / netstat output". They are
  **representative samples I wrote**, not captures from his machine. The formats are right; the
  data is synthetic.
- **The helper has never run on Windows.** All 60 checks ran on Linux. `netstat -ano` parsing,
  `taskkill /T`, `CREATE_NEW_PROCESS_GROUP`, `tasklist` and the `.bat` are untested against real
  Windows and should be treated as unproven until the `--probe` run.
- v1.214's runner was verified with `--dry-run` against a FAKE Fizgig checkout. Structurally
  sound; never executed against a real install.
- v1.216 and v1.217 shipped backend-only. I said so, but at the bottom of long messages rather
  than up front, which is how he came to look for a UI that was never built.

All seven lora suites pass on the live file, md5 af836a4621edd786d33e9b3873f1761b.

'''
cl = ROOT / "CHANGELOG.md"
s = cl.read_text("utf-8")
assert s.startswith("## v1.218.0"), s[:40]
cl.write_text(ENTRY + s, "utf-8")
print("VERSION 1.219.0 · pyproject · CHANGELOG")
