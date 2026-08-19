"""🔎 Grep the backend log from HERE — the app already logged the answer.

`ComfyUIClient._make_request` logs the FULL 400 body before `raise_for_status`
destroys it, but the job record only keeps `400 Client Error`. So when a render
fails with a bare status, the real reason is already on disk — this finds it.

    python scripts\\log_grep.py --pattern "ComfyUI 400" --lines 60
    python scripts\\log_grep.py --pattern node_errors --tail 4000
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pattern", required=True)
    ap.add_argument("--lines", type=int, default=40, help="lines AFTER each hit")
    ap.add_argument("--tail", type=int, default=20000,
                    help="only search the last N lines of each log")
    ap.add_argument("--max", type=int, default=3, help="hits to show")
    a = ap.parse_args()
    # ⚠ the log lives at <repo>/logs/rbmn.log (main.py), NOT backend/logs —
    # looking in the wrong place returns "no logs found", which reads exactly
    # like "nothing was logged".
    logs = sorted(list((ROOT / "logs").glob("*.log*"))
                  + list((ROOT / "backend" / "logs").glob("*.log*")),
                  key=lambda p: p.stat().st_mtime, reverse=True)
    if not logs:
        print("no logs found")
        return 1
    shown = 0
    for fp in logs[:3]:
        try:
            rows = fp.read_text("utf-8", errors="replace").splitlines()[-a.tail:]
        except Exception as e:                                   # noqa: BLE001
            print(f"{fp.name}: unreadable ({e})")
            continue
        for i, line in enumerate(rows):
            if a.pattern.lower() in line.lower():
                print(f"\n=== {fp.name}:{i} ===")
                print("\n".join(rows[i:i + a.lines]))
                shown += 1
                if shown >= a.max:
                    return 0
    if not shown:
        print(f"no line matching {a.pattern!r} in the last {a.tail} lines")
    return 0


if __name__ == "__main__":
    sys.exit(main())
