"""🧩 Which CUSTOM NODE PACKS are installed on each box (and do they match)?

A node pack does not have to be USED to break a lane — it only has to be
IMPORTED. ComfyUI issue #12322 is the case in point: `ComfyUI_RyanOnTheInside`
monkey-patched ACE-Step 1.5's `forward` at import time, so base/sft rendered
garbled audio for people whose graphs never touched one of its nodes, and
ComfyUI-Manager could show it "uninstalled" while the folder was still on disk.

This reads each worker's own `/object_info` and groups the node classes by the
`python_module` that registered them, so the answer comes from the RUNNING
process rather than from a folder listing.

    python scripts\\comfy_nodepacks.py                 every box, side by side
    python scripts\\comfy_nodepacks.py --grep ryan     just the suspicious one

⭐ STDLIB ONLY — it reads the worker registry off disk rather than importing
the app (importing `backend.api.*` drags in FastAPI and the script then only
runs inside the venv).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.request
from pathlib import Path

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

REPO = Path(__file__).resolve().parent.parent


def _project_dir() -> Path:
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


def helpers() -> list:
    fp = _project_dir() / "_libraries" / "forge" / "settings.json"
    try:
        return json.loads(fp.read_text("utf-8")).get("helpers") or []
    except Exception as e:                                     # noqa: BLE001
        print(f"⚠ cannot read the worker registry at {fp}: {e}")
        return []


def packs(host: str) -> dict:
    url = f"http://{host}:8188/object_info"
    with urllib.request.urlopen(url, timeout=180) as r:
        info = json.loads(r.read().decode())
    out: dict = {}
    for name, spec in info.items():
        mod = (spec or {}).get("python_module") or "?"
        out.setdefault(mod, []).append(name)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--grep", default="", help="only packs whose name matches")
    ap.add_argument("--hosts", default="", help="comma list; default = registry")
    a = ap.parse_args()
    hosts = ([h.strip() for h in a.hosts.split(",") if h.strip()]
             or [h["host"] for h in helpers()])
    if not hosts:
        print("no workers found")
        return 1

    per: dict = {}
    for host in hosts:
        try:
            per[host] = packs(host)
        except Exception as e:                                 # noqa: BLE001
            print(f"⚠ {host}: {type(e).__name__}: {e}")
            per[host] = {}
    names = sorted({m for p in per.values() for m in p
                    if m.startswith("custom_nodes")
                    and (not a.grep or a.grep.lower() in m.lower())})
    if not names:
        print("no custom node packs matched" if a.grep
              else "no custom node packs registered on any box")
    w = max([len(n) for n in names] or [10])
    print(f"{'pack':<{w}}  " + "  ".join(f"{h.split('.')[-1]:>7}" for h in hosts))
    for n in names:
        cells = []
        for h in hosts:
            got = per[h].get(n)
            cells.append(f"{len(got):>7}" if got else "      —")
        # ⚠ a pack present on SOME boxes is the shape that produces "it works
        # on one worker and not the other" — flag it rather than leaving it to
        # be read off the columns.
        odd = "" if all(per[h].get(n) for h in hosts) else "   ⚠ not on every box"
        print(f"{n:<{w}}  " + "  ".join(cells) + odd)

    bad = [n for n in names if "ryanontheinside" in n.lower()]
    if bad:
        print("\n⚠⚠ ComfyUI_RyanOnTheInside is REGISTERED here. Versions before "
              "2026-02-15 monkey-patch ACE-Step 1.5's forward AT IMPORT TIME and "
              "garble base/sft output even when none of its nodes are in the "
              "graph (ComfyUI issue #12322). Update or remove the FOLDER, then "
              "restart ComfyUI — Manager can report it uninstalled while the "
              "folder is still there.")
    else:
        print("\n✅ ComfyUI_RyanOnTheInside is not registered on any box "
              "(the ACE-Step 1.5 import-time monkey-patch cannot apply).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
