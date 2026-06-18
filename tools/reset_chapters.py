#!/usr/bin/env python3
"""reset_chapters.py — clear a project's chapters so they rebuild clean.

Removes stale/manual chapters (and unbinds scenes) for a project. After
running, reopen the project or click Suggest Timeline and the app rebuilds
a single clean chapter level from the current scenes.

Usage:
    python tools/reset_chapters.py                      # most-recent project, DRY RUN
    python tools/reset_chapters.py --apply              # actually delete
    python tools/reset_chapters.py --project v6 --apply
    python tools/reset_chapters.py --db "C:\\path\\RBMN.db" --apply
"""
from __future__ import annotations
import argparse, sqlite3, sys
from pathlib import Path


def find_db(explicit):
    cands = []
    if explicit: cands.append(Path(explicit).expanduser())
    cands.append(Path("~/RBMN-Projects/RBMN.db").expanduser())
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        from backend.config import settings as _s
        cands.append(Path(str(_s.db_path)))
    except Exception:
        pass
    for c in cands:
        if c.exists(): return c
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=None)
    ap.add_argument("--project", default=None)
    ap.add_argument("--apply", action="store_true", help="actually delete (default is dry-run)")
    args = ap.parse_args()
    db = find_db(args.db)
    if not db:
        print("ERROR: RBMN.db not found. Pass --db PATH."); return 2
    con = sqlite3.connect(str(db)); con.row_factory = sqlite3.Row; cur = con.cursor()
    print(f"DB: {db}")

    cur.execute("SELECT id,name,updated_at FROM projects ORDER BY updated_at DESC")
    projs = cur.fetchall()
    if not projs: print("no projects"); return 2
    target = projs[0]
    if args.project:
        q = args.project.lower().replace("-","").replace("_","")
        for p in projs:
            if q in str(p["name"]).lower().replace("-","").replace("_","") or q in str(p["id"]).lower().replace("-",""):
                target = p; break
    pid = target["id"]
    print(f"Project: {target['name']}  (id={pid})")

    cur.execute("SELECT id,name,depth,source,start_time,end_time FROM chapters WHERE project_id=? ORDER BY depth,order_index", (pid,))
    rows = cur.fetchall()
    print(f"\nChapters currently on this project: {len(rows)}")
    for r in rows:
        print(f"  d={r['depth']} src={r['source']:8s} {str(r['name'])[:32]:32s} t={float(r['start_time'] or 0):.1f}-{float(r['end_time'] or 0):.1f} id={str(r['id'])[:8]}")
    cur.execute("SELECT COUNT(*) c FROM scenes WHERE project_id=? AND chapter_id IS NOT NULL", (pid,))
    bound = cur.fetchone()["c"]
    print(f"scenes bound to a chapter: {bound}")

    if not args.apply:
        print("\nDRY RUN — nothing changed. Re-run with --apply to delete all chapters above")
        print("and unbind their scenes. The app rebuilds clean chapters on next open / Suggest Timeline.")
        con.close(); return 0

    # Apply: unbind scenes, delete chapters deepest-first (FK-safe).
    cur.execute("UPDATE scenes SET chapter_id = NULL WHERE project_id=?", (pid,))
    cur.execute("SELECT MAX(depth) m FROM chapters WHERE project_id=?", (pid,))
    maxd = (cur.fetchone()["m"]) or 0
    for d in range(int(maxd), -1, -1):
        cur.execute("DELETE FROM chapters WHERE project_id=? AND depth=?", (pid, d))
    con.commit()
    cur.execute("SELECT COUNT(*) c FROM chapters WHERE project_id=?", (pid,))
    left = cur.fetchone()["c"]
    print(f"\nAPPLIED: deleted chapters (remaining={left}), unbound scenes.")
    print("Now REOPEN the project (or click Suggest Timeline) — the app will build one clean chapter level.")
    con.close(); return 0


if __name__ == "__main__":
    raise SystemExit(main())
