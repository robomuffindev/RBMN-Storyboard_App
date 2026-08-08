#!/usr/bin/env python3
"""RBMN diagnostics dumper -- bridges the gap for a cloud Cowork session.

The project DATA (mesh3d rigs, assets, logs) lives at the project_dir set on the
Settings screen (e.g. D:\\RBMN-Projects), while the DB itself stays at the fixed
default ~/RBMN-Projects/RBMN.db.  A fresh `import backend.config` only sees the
.env default (C:), because the D: override is applied at the app's live startup
(backend/main.py) from app_settings.project_dir in the DB.

So this tool: finds the DB, reads app_settings.project_dir the way the running
app does, and copies the diagnostic bits from the REAL data dir into
<repo>/_diag/ -- which the Cowork session can read.

Run from the repo root:  venv\\Scripts\\python.exe tools\\rbmn_diag.py
(or double-click diag.bat).  Optional: pass the data dir explicitly:
   venv\\Scripts\\python.exe tools\\rbmn_diag.py D:\\RBMN-Projects
Stdlib only.  Never raises on missing pieces.
"""
from __future__ import annotations
import json, os, sqlite3, shutil, sys, traceback
from pathlib import Path
from datetime import datetime

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "_diag"


def default_project_dir() -> Path:
    env = os.environ.get("PROJECT_DIR")
    if env:
        return Path(os.path.expanduser(env))
    envf = REPO / ".env"
    if envf.exists():
        for line in envf.read_text(encoding="utf-8", errors="ignore").splitlines():
            if line.strip().upper().startswith("PROJECT_DIR"):
                v = line.split("=", 1)[1].strip().strip('"').strip("'")
                return Path(os.path.expanduser(v))
    return Path(os.path.expanduser("~/RBMN-Projects"))


def load_json_maybe(v):
    if v is None:
        return None
    if isinstance(v, (dict, list)):
        return v
    try:
        return json.loads(v)
    except Exception:  # noqa: BLE001
        return v


def find_db(hint: Path | None) -> Path | None:
    """The DB is normally at the fixed default ~/RBMN-Projects/RBMN.db, but if the
    user moved data it could be under the chosen data dir.  Try, in order:
    default, the CLI hint, ~/RBMN-Projects."""
    cands = []
    dflt = default_project_dir()
    cands.append(dflt / "RBMN.db")
    if hint:
        cands.append(hint / "RBMN.db")
    cands.append(Path(os.path.expanduser("~/RBMN-Projects")) / "RBMN.db")
    seen = set()
    for c in cands:
        cs = str(c)
        if cs in seen:
            continue
        seen.add(cs)
        if c.exists():
            return c
    return None


def dump_db(db: Path, report: dict) -> Path | None:
    """Dump global studio settings + characters; return the app-configured data dir."""
    report["db_path"] = str(db)
    report["db_exists"] = db.exists()
    data_dir_from_db = None
    if not db.exists():
        return None
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
        con.row_factory = sqlite3.Row
        cur = con.cursor()
        # ---- app_settings: the project_dir override + global studio settings ----
        try:
            row = cur.execute("SELECT project_dir, studio_vnccs_host, studio_vnccs_settings "
                              "FROM app_settings LIMIT 1").fetchone()
            if row:
                data_dir_from_db = (row["project_dir"] or "").strip() or None
                report["settings_project_dir"] = data_dir_from_db
                report["studio_vnccs_host"] = row["studio_vnccs_host"]
                svs = load_json_maybe(row["studio_vnccs_settings"])
                (OUT / "studio_vnccs_settings.json").write_text(
                    json.dumps(svs, indent=2, default=str), encoding="utf-8")
                if isinstance(svs, dict):
                    keys = ["klein_pose_source", "klein_base_set", "klein_lock_base",
                            "klein_pose_steps", "klein_pose_cleanup", "klein_pose_ref_end",
                            "klein_pose_body_match", "klein_pose_face_refine", "klein_pose_lora",
                            "klein_cleanup", "klein_autofit_proportions"]
                    report["pose_settings"] = {k: svs.get(k) for k in keys if k in svs}
                    report["ALL_studio_settings_keys"] = sorted(svs.keys())
        except Exception as e:  # noqa: BLE001
            report["app_settings_error"] = f"{e}"
        # ---- characters ----
        try:
            rows = cur.execute("SELECT id, name, kind, manifest FROM studio_characters").fetchall()
            chars = []
            for r in rows:
                man = load_json_maybe(r["manifest"]) or {}
                vn = (man.get("vnccs") or {}) if isinstance(man, dict) else {}
                safe = "".join(c for c in str(r["name"]) if c.isalnum() or c in "._- ") or "char"
                (OUT / f"manifest_{safe}.json").write_text(
                    json.dumps(man, indent=2, default=str), encoding="utf-8")
                chars.append({"id": str(r["id"]), "name": r["name"], "kind": r["kind"],
                              "mesh3d": vn.get("mesh3d") if isinstance(vn, dict) else None,
                              "active_base": vn.get("active_base") if isinstance(vn, dict) else None})
            report["characters"] = chars
        except Exception as e:  # noqa: BLE001
            report["characters_error"] = f"{e}"
        con.close()
    except Exception as e:  # noqa: BLE001
        report["db_error"] = f"{e}\n{traceback.format_exc()}"
    return Path(os.path.expanduser(data_dir_from_db)) if data_dir_from_db else None


def dump_mesh3d(data_dir: Path, report: dict):
    root = data_dir / "mesh3d"
    report["mesh3d_root"] = str(root)
    report["mesh3d_exists"] = root.is_dir()
    if not root.is_dir():
        return
    entries = []
    for d in sorted([p for p in root.iterdir() if p.is_dir()]):
        info = {"dir": d.name}
        for fn in ("character.glb", "rigged.fbx"):
            f = d / fn
            info[fn] = f.stat().st_size if f.exists() else None
        meta = d / "meta.json"
        if meta.exists():
            info["meta"] = load_json_maybe(meta.read_text(encoding="utf-8", errors="ignore"))
        clay = d / "clay_last"
        if clay.is_dir():
            pngs = sorted(clay.glob("*.png"))[:40]
            info["clay_last_count"] = len(pngs)
            dest = OUT / f"clay_{d.name}"
            dest.mkdir(parents=True, exist_ok=True)
            for p in pngs:
                try:
                    shutil.copy2(p, dest / p.name)
                except Exception:  # noqa: BLE001
                    pass
        else:
            info["clay_last_count"] = 0
        entries.append(info)
    report["mesh3d"] = entries


def dump_logs(data_dir: Path, report: dict):
    logs = []
    for base in (data_dir, REPO):
        try:
            for p in base.rglob("*.log"):
                if "node_modules" in str(p) or "_diag" in str(p):
                    continue
                logs.append(p)
        except Exception:  # noqa: BLE001
            pass
    logs = sorted(set(logs), key=lambda p: p.stat().st_mtime, reverse=True)[:3]
    report["logs_found"] = [str(p) for p in logs]
    for i, p in enumerate(logs):
        try:
            lines = p.read_text(encoding="utf-8", errors="ignore").splitlines()[-500:]
            (OUT / f"log_tail_{i}.txt").write_text("\n".join(lines), encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass


def main():
    hint = None
    if len(sys.argv) > 1 and sys.argv[1].strip():
        hint = Path(os.path.expanduser(sys.argv[1].strip()))
    if OUT.exists():
        shutil.rmtree(OUT, ignore_errors=True)
    OUT.mkdir(parents=True, exist_ok=True)
    report = {"generated": datetime.utcnow().isoformat() + "Z", "repo": str(REPO),
              "default_project_dir": str(default_project_dir())}
    db = find_db(hint)
    data_dir_from_db = dump_db(db, report) if db else None
    # data dir precedence: explicit CLI hint > DB app_settings.project_dir > default
    data_dir = hint or data_dir_from_db or default_project_dir()
    report["data_dir_used"] = str(data_dir)
    report["data_dir_exists"] = data_dir.exists()
    dump_mesh3d(data_dir, report)
    dump_logs(data_dir, report)
    (OUT / "report.json").write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print("\n===== RBMN DIAG SUMMARY =====")
    print("db_path      :", report.get("db_path"), "" if report.get("db_exists") else "(MISSING)")
    print("settings dir :", report.get("settings_project_dir"), "(from app_settings.project_dir)")
    print("data_dir used:", report["data_dir_used"], "(exists)" if report["data_dir_exists"] else "(MISSING)")
    print("klein_pose_source :", (report.get("pose_settings") or {}).get("klein_pose_source", "<unset>"))
    print("characters   :", len(report.get("characters") or []))
    for m in report.get("mesh3d") or []:
        print(f"  mesh3d[{m['dir']}] glb={m.get('character.glb')} fbx={m.get('rigged.fbx')} "
              f"rigged={(m.get('meta') or {}).get('rigged')} clay_last={m.get('clay_last_count')}")
    print("wrote ->", OUT)
    print("Tell Claude: diag done")


if __name__ == "__main__":
    main()
