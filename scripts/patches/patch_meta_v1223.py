"""v1.223 — VERSION, pyproject, CHANGELOG."""
import sys
from pathlib import Path
ROOT = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
v = ROOT / "VERSION"
assert v.read_text("utf-8").strip() == "1.222.0", v.read_text("utf-8")
v.write_text("1.223.0\n", "utf-8")
pp = ROOT / "pyproject.toml"
s = pp.read_text("utf-8")
assert s.count('version = "1.222.0"') == 1
pp.write_text(s.replace('version = "1.222.0"', 'version = "1.223.0"', 1), "utf-8")

ENTRY = '''## v1.223.0 -- the render path had no write lock (2026-08-05)

**v1.222's explanation was wrong, and this is the real cause.** I said the preset fell back from
face_heavy to balanced. His original framing counts were face 8 / headshot 8 / upper 12 / full
12 -- which IS balanced at 40 images. The preset never changed.

`_qc_blocking` guards its read-modify-write with a lock. `_render_blocking` does the SAME
read-whole-file / mutate / write-whole-file, one thread per worker, and guarded nothing. When two
renders finish close together the second one's read predates the first one's write, and the first
update is silently discarded.

The PNG always survives -- `_save_png_bytes` runs before the read -- but everything recorded
ABOUT it can vanish:

  * `status = "done"`  -> a re-plan treats the row as never rendered and DELETES the file.
                          **This is how 40 images on disk reported as "7 of 40 rendered".**
  * `attempts`         -> MAX_ATTEMPTS under-counts, so a stuck image re-rolls past its cap.
  * `identity`         -> the "which base was used" column -- the data the three-quarter
                          finding was read from.
  * `caption`          -> auto-captions quietly missing on some rows.

Fixes: one module-level `_DS_WRITE_LOCK` around every worker-thread mutation, shared by render
and QC (dataset_repair interleaves both over the same file). `_plan_impact` and the re-plan now
**trust the filesystem over the status field**, so a row whose status was lost is never deleted.
And `POST /datasets/{id}/resync` rebuilds status from disk for datasets already scrambled --
renders nothing, deletes nothing.

Ten lora suites pass, md5 04cf775c2948475b9325b9cfe6af8a48.

'''
cl = ROOT / "CHANGELOG.md"
s = cl.read_text("utf-8")
assert s.startswith("## v1.222.0"), s[:40]
cl.write_text(ENTRY + s, "utf-8")
print("VERSION 1.223.0 · pyproject · CHANGELOG")
