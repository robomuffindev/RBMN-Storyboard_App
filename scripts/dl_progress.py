"""⬇ Are the helper downloads finished? — a straight answer, per box.

The helper's `/download/model` returns the moment a download is QUEUED, and a
`--check` only sees files that have LANDED, so a 19 GB stage looks identical to
a stalled one for an hour. This reads the actual download queues and prints
bytes / total / percent / rate / ETA.

    python scripts\\dl_progress.py                 once, through the running app
    python scripts\\dl_progress.py --watch         keep looking (bounded, see --for)
    python scripts\\dl_progress.py --direct        skip the app, ask the boxes

⭐ **STDLIB ONLY, on purpose.** It first asks the running app
(`/api/audio-lab/staging`), and only falls back to reading the worker registry
off disk. Importing `backend.api…` for one helper list drags in FastAPI, which
means it only runs inside the venv — and a diagnostic you cannot run from a
plain `python scripts\\…` prompt is a diagnostic you will not run.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

REPO = Path(__file__).resolve().parent.parent


def _project_dir() -> Path:
    """Same answer the app computes at import time: .env PROJECT_DIR, else the
    default. (Deliberately NOT the DB override — the modules that own these
    files anchor on the import-time constant.)"""
    v = os.environ.get("PROJECT_DIR") or ""
    if not v:
        try:
            for line in (REPO / ".env").read_text("utf-8").splitlines():
                m = re.match(r"\s*PROJECT_DIR\s*=\s*(.+?)\s*$", line, re.I)
                if m:
                    v = m.group(1).strip().strip('"').strip("'")
                    break
        except Exception:                                      # noqa: BLE001
            pass
    return Path(v or "~/RBMN-Projects").expanduser()


def helpers_from_disk() -> list:
    fp = _project_dir() / "_libraries" / "forge" / "settings.json"
    try:
        return json.loads(fp.read_text("utf-8")).get("helpers") or []
    except Exception as e:                                     # noqa: BLE001
        print(f"⚠ could not read the worker registry at {fp}: {e}")
        return []


def _get(url: str, timeout: float = 25.0):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode() or "{}")


def rows_via_app(app: str) -> list | None:
    try:
        d = _get(f"{app.rstrip('/')}/api/audio-lab/staging", timeout=60)
    except Exception:                                          # noqa: BLE001
        return None
    return d.get("downloads")


def rows_direct() -> list:
    out = []
    for h in helpers_from_disk():
        name = h.get("name") or h.get("host")
        url = (f"http://{h['host']}:{h.get('port', 8765)}/downloads"
               f"?token={urllib.parse.quote(h.get('token') or '')}")
        try:
            d = _get(url)
        except Exception as e:                                 # noqa: BLE001
            out.append({"name": name, "host": h.get("host"), "file": "",
                        "status": "unreachable", "error": f"{type(e).__name__}: {e}"})
            continue
        for r in (d if isinstance(d, list) else (d.get("downloads") or [])):
            got, tot = int(r.get("bytes") or 0), int(r.get("total") or 0)
            out.append({"name": name, "host": h.get("host"),
                        "file": Path(str(r.get("dest") or r.get("url") or "")).name,
                        "status": r.get("status") or "?", "bytes": got,
                        "total": tot, "error": r.get("error"),
                        "pct": round(100.0 * got / tot, 1) if tot else 0.0})
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--app", default="http://127.0.0.1:8899")
    ap.add_argument("--direct", action="store_true",
                    help="ask the boxes directly instead of the running app")
    ap.add_argument("--watch", action="store_true")
    ap.add_argument("--every", type=float, default=30.0)
    # ⚠ the agent KILLS a job at its timeout and writes no stdout at all, so an
    # unbounded --watch produces a result file with nothing in it. Bound it.
    ap.add_argument("--for", dest="minutes", type=float, default=2.0,
                    help="stop watching after N minutes (0 = never)")
    a = ap.parse_args()
    deadline = time.time() + a.minutes * 60 if a.minutes else 0.0
    prev: dict = {}

    while True:
        rows = None if a.direct else rows_via_app(a.app)
        via = "the app"
        if rows is None:
            rows = rows_direct()
            via = "the boxes directly"
        print(f"{time.strftime('%H:%M:%S')}   (via {via})")
        if not rows:
            print("  nothing queued on any box.")
            return 0
        active = 0
        for r in rows:
            name, fn = r.get("name") or r.get("host") or "?", r.get("file") or "?"
            got, tot = int(r.get("bytes") or 0), int(r.get("total") or 0)
            st = r.get("status") or "?"
            if r.get("error") and not tot:
                # name the FILE, not just the exception — a bare
                # ConnectionResetError reads like the box is down when it is
                # one stale failed record from an earlier stage.
                print(f"  {name:<14} ❌ {fn:<46} {r['error']}")
                continue
            pct = r.get("pct", (100.0 * got / tot) if tot else 0.0)
            rate = ""
            key = f"{name}:{fn}"
            if key in prev and st == "running":
                db, dt = got - prev[key][0], max(0.001, time.time() - prev[key][1])
                rate = (f"  {db / dt / 2**20:5.1f} MB/s  "
                        f"eta {(tot - got) / (db / dt) / 60:5.1f} min"
                        if db > 0 else "  ⚠ no progress since the last look")
            prev[key] = (got, time.time())
            mark = {"done": "✅", "running": "⬇", "error": "❌",
                    "unreachable": "⚠"}.get(st, "•")
            print(f"  {name:<14} {mark} {fn:<46} "
                  f"{got / 2**30:6.2f}/{tot / 2**30:6.2f} GB  {pct:5.1f}%{rate}"
                  + (f"  {r.get('error')}" if r.get("error") else ""))
            if st == "running":
                active += 1
        if not a.watch:
            print("\n" + (f"{active} download(s) still running."
                          if active else "nothing is downloading."))
            return 0
        if not active:
            print("\nnothing is downloading any more.")
            return 0
        if deadline and time.time() + a.every > deadline:
            print("\n(watch window over — re-run to keep looking)")
            return 0
        time.sleep(a.every)


if __name__ == "__main__":
    sys.exit(main())
