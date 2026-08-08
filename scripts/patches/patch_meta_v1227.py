"""v1.227 — VERSION, pyproject, CHANGELOG."""
import sys
from pathlib import Path
ROOT = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
v = ROOT / "VERSION"
assert v.read_text("utf-8").strip() == "1.226.0", v.read_text("utf-8")
v.write_text("1.227.0\n", "utf-8")
pp = ROOT / "pyproject.toml"
s = pp.read_text("utf-8")
assert s.count('version = "1.226.0"') == 1
pp.write_text(s.replace('version = "1.226.0"', 'version = "1.227.0"', 1), "utf-8")
ENTRY = '''## v1.227.0 -- the panel only polled if it ALREADY knew a run was going (2026-08-05)

    useEffect(() => {
      if (ds?.run?.status !== 'running') return;   // <- circular
      const t = setInterval(...)
    }, [ds?.run?.status, refreshDs, load]);

The single refresh immediately after a POST was the ONLY chance to notice a run had started.
Miss it once and the panel never polled again: no banner, no progress, no error, indefinitely.
That is precisely what "it says QC started and I can't tell if anything is happening" looks like.

It was easy to miss, because `refreshDs` swallowed every failure --
`try { setDs(await j(...)) } catch { /* ignore */ }` -- so a backend that stopped answering was
indistinguishable from one with nothing to report. And `refreshDs` was keyed on the whole `ds`
object, so it changed identity on every poll and restarted the interval each tick.

  * Polls whenever a dataset is OPEN, not only once a run is known: 3s while running, 6s idle.
  * `refreshDs` keyed on the dataset ID, so the interval is stable.
  * Failed refreshes are counted and shown ("lost contact with the backend"), not eaten.
  * A heartbeat spinner next to the banner, so "working" is distinguishable from "hung" even
    before `detail` moves -- which matters when one Ollama server checks 40 images one at a time.

`scripts/watch_run.ps1` reads the same state straight from the API, so "is it running or is it
hung" can be answered without trusting the UI at all. It also reports how many vision servers
exist, since one server means a strictly sequential pass measured in minutes.

'''
cl = ROOT / "CHANGELOG.md"
s = cl.read_text("utf-8")
assert s.startswith("## v1.226.0"), s[:40]
cl.write_text(ENTRY + s, "utf-8")
print("VERSION 1.227.0 · pyproject · CHANGELOG")
