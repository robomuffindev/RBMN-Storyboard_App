#!/usr/bin/env python3
"""diag_audio.py — Why is the timeline waveform / audio missing?

Checks whether the project's audio (music / narration) Asset row exists and
whether its file is actually on disk — testing BOTH the C: and D: project_dir
roots to expose the known DB-vs-media drive split.

Writes ./audio_diag.md (also prints). Stdlib only.

Usage:
    python tools/diag_audio.py                      # most-recently-edited project
    python tools/diag_audio.py --project 55703d4e   # by id prefix or name
    python tools/diag_audio.py --db PATH
"""
from __future__ import annotations
import argparse, json, sqlite3, sys
from pathlib import Path


def find_db(explicit):
    cands = []
    if explicit:
        cands.append(Path(explicit).expanduser())
    cands.append(Path("~/RBMN-Projects/RBMN.db").expanduser())
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        from backend.config import settings as _s
        cands.append(Path(str(_s.db_path)))
    except Exception:
        pass
    for c in cands:
        if c.exists():
            return c
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=None)
    ap.add_argument("--project", default=None, help="id prefix or name substring")
    ap.add_argument("--out", default="audio_diag.md")
    args = ap.parse_args()

    db = find_db(args.db)
    if not db:
        print("ERROR: could not locate RBMN.db. Pass --db PATH.")
        return 2
    con = sqlite3.connect(str(db))
    con.row_factory = sqlite3.Row
    cur = con.cursor()

    # project_dir override (the media root the running app uses)
    dir_override = None
    try:
        r = cur.execute("SELECT project_dir FROM app_settings LIMIT 1").fetchone()
        dir_override = (r["project_dir"] if r else None) or None
    except Exception:
        pass

    # candidate media roots to test (override + both common drives)
    roots = []
    def add_root(p):
        if p:
            pp = Path(p)
            if pp not in roots:
                roots.append(pp)
    add_root(dir_override)
    add_root(r"C:\Users\hexum\RBMN-Projects")
    add_root(r"D:\RBMN-Projects")
    add_root(str(Path("~/RBMN-Projects").expanduser()))

    # pick project
    cur.execute("SELECT id,name,mode,updated_at FROM projects ORDER BY updated_at DESC")
    projs = cur.fetchall()
    if not projs:
        print("no projects")
        return 2
    target = None
    if args.project:
        q = args.project.lower()
        for p in projs:
            if p["id"].lower().startswith(q) or q in (p["name"] or "").lower():
                target = p
                break
        if not target:
            print(f"no project matching {args.project!r}")
            return 2
    else:
        target = projs[0]

    pid = target["id"]
    L = ["# Audio / Waveform Diagnostic", ""]
    L.append(f"- DB: `{db}`")
    L.append(f"- app_settings.project_dir (override): `{dir_override}`")
    L.append(f"- Project: **{target['name']}**  (`{pid}`)  mode=`{target['mode']}`")
    L.append("")

    # all assets for this project
    cur.execute(
        "SELECT id,asset_type,filename,rel_path,file_size FROM assets WHERE project_id=?",
        (pid,),
    )
    assets = cur.fetchall()
    by_type = {}
    for a in assets:
        by_type.setdefault(a["asset_type"], 0)
        by_type[a["asset_type"]] += 1
    L.append("## Asset counts by type")
    if by_type:
        for t, n in sorted(by_type.items()):
            L.append(f"- `{t}`: {n}")
    else:
        L.append("- (none)")
    L.append("")

    audio = [a for a in assets if (a["asset_type"] or "").lower() in ("music", "narration")]
    L.append(f"## Audio assets (music / narration): {len(audio)}")
    if not audio:
        L.append("")
        L.append("**No music/narration asset row exists for this project.**")
        L.append("The waveform looks for an `asset_type == 'music'` asset and finds")
        L.append("none — so it shows the empty placeholder and nothing plays.")
        L.append("Fix: re-upload the audio on the Audio tab (Process Audio).")
    for a in audio:
        L.append("")
        L.append(f"### `{a['asset_type']}` — {a['filename']}")
        L.append(f"- asset id: `{a['id']}`")
        L.append(f"- rel_path: `{a['rel_path']}`")
        L.append(f"- db file_size: {a['file_size']}")
        rel = a["rel_path"] or ""
        # mirror backend resolution: if rel starts with pid -> root/rel else root/pid/rel
        starts_pid = rel.startswith(pid + "/") or rel.startswith(pid + "\\")
        L.append("- on-disk check across candidate media roots:")
        found_any = False
        for root in roots:
            cand = (root / rel) if starts_pid else (root / pid / rel)
            ok = cand.exists()
            sz = cand.stat().st_size if ok else "-"
            mark = "✅ FOUND" if ok else "❌ missing"
            if ok:
                found_any = True
            L.append(f"    - {mark}  `{cand}`  (size={sz})")
        served_root = roots[0] if roots else None
        served = (served_root / rel) if (served_root and starts_pid) else ((served_root / pid / rel) if served_root else None)
        L.append(f"- **App serves from:** `{served}`")
        if served is not None and not served.exists():
            # is it present on a DIFFERENT root?
            elsewhere = [str((rt / rel) if starts_pid else (rt / pid / rel)) for rt in roots
                         if ((rt / rel) if starts_pid else (rt / pid / rel)).exists()]
            if elsewhere:
                L.append("")
                L.append("- ⚠️ **DRIVE SPLIT CONFIRMED:** the file exists, but NOT under the")
                L.append("  drive the app serves from. It's at:")
                for e in elsewhere:
                    L.append(f"    - `{e}`")
                L.append("  Fix: copy/move the project's `assets/audio/` (and any other")
                L.append("  uploaded files) onto the served media root, OR point")
                L.append("  Settings → project_dir at the drive that actually has the files.")
            elif not found_any:
                L.append("- ❌ File not found on ANY candidate root — it was deleted/moved.")

    out = Path(args.out)
    out.write_text("\n".join(L), encoding="utf-8")
    print("\n".join(L))
    print(f"\n[wrote {out.resolve()}]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
