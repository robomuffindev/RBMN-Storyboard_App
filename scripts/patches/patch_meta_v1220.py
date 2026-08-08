"""v1.220 — VERSION, pyproject, CHANGELOG."""
import sys
from pathlib import Path

ROOT = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
v = ROOT / "VERSION"
assert v.read_text("utf-8").strip() == "1.219.0", v.read_text("utf-8")
v.write_text("1.220.0\n", "utf-8")
pp = ROOT / "pyproject.toml"
s = pp.read_text("utf-8")
assert s.count('version = "1.219.0"') == 1
pp.write_text(s.replace('version = "1.219.0"', 'version = "1.220.0"', 1), "utf-8")

ENTRY = '''## v1.220.0 -- ArcFace was freezing the whole app (2026-08-05)

Caught while he was mid-run on the `/likeness` command I gave him. Three `async def` routes did
CPU work -- and, on first use, a ~300MB model download -- INLINE. FastAPI runs async handlers on
the event loop, so each one froze **the entire app**, not just its own request.

  * `/datasets/{id}/likeness` -- the scoring loop and the distribution pass.
  * `/datasets/{id}/qc` -- `_likeness_baselines()` ran BEFORE the work was handed to a thread, so
    the model load happened on the loop even though the QC pass itself did not. **My own
    blocking-call scan missed this**: it looked for `_like.*` calls directly (this goes through a
    helper) and the route contains a `_spawn`, so it read as already-threaded. The second scan
    keyed on the helper name too.
  * `/likeness-health` -- `health()` calls `available()` calls `_app()`. A health check that
    downloads 300MB before answering is not a health check.

All three now use `asyncio.to_thread`, the pattern the caption/enrich path in this same file
already used. A fresh scan reports no async route left touching `_like.*` or
`_likeness_baselines` on the loop.

Two practical consequences worth recording: on PowerShell 5.1 `Invoke-RestMethod` times out at
~100s, so the first `/likeness` call could error client-side while the server carried on and
saved its results -- re-running returned instantly from cache. And `run.py` sets
`reload=False`, so patching lora.py under a live process is safe; it takes effect on restart.

All seven lora suites pass, md5 14cd80c4990dd6513194f63ae7232baa.

'''
cl = ROOT / "CHANGELOG.md"
s = cl.read_text("utf-8")
assert s.startswith("## v1.219.0"), s[:40]
cl.write_text(ENTRY + s, "utf-8")
print("VERSION 1.220.0 · pyproject · CHANGELOG")
