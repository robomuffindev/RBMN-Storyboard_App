#!/usr/bin/env python3
"""Re-rig every existing 3D-body character in place with the no-rest fix.

The v1.199.70 fix makes NEW rigs clean, but characters already rigged with the
old reset_to_rest bind (sheared arms) need a one-time re-rig.  This re-rigs each
<data_dir>/mesh3d/<char>/character.glb with --no-rest and REPLACES rigged.fbx
(backing up the old one to rigged_prev_reset.fbx).  No app needed; the clay pose
path reads rigged.fbx fresh, so posing picks up the new bind immediately.

Run:  runtime\\mia\\venv\\Scripts\\python.exe tools\\rerig_all.py
(or double-click rerig_all.bat).  Each character takes a couple minutes on CPU.
"""
from __future__ import annotations
import json, os, sqlite3, subprocess, sys, time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
MIA_DIR = REPO / "backend" / "services" / "character_studio" / "vnccs_native" / "mia_local"
DRIVER = MIA_DIR / "driver.py"
MODELS = REPO / "runtime" / "mia" / "models"


def default_project_dir() -> Path:
    env = os.environ.get("PROJECT_DIR")
    if env:
        return Path(os.path.expanduser(env))
    envf = REPO / ".env"
    if envf.exists():
        for line in envf.read_text(encoding="utf-8", errors="ignore").splitlines():
            if line.strip().upper().startswith("PROJECT_DIR"):
                return Path(os.path.expanduser(line.split("=", 1)[1].strip().strip('"').strip("'")))
    return Path(os.path.expanduser("~/RBMN-Projects"))


def data_dir() -> Path:
    dflt = default_project_dir()
    db = dflt / "RBMN.db"
    if db.exists():
        try:
            con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
            row = con.execute("SELECT project_dir FROM app_settings LIMIT 1").fetchone()
            con.close()
            if row and row[0]:
                return Path(os.path.expanduser(row[0]))
        except Exception as e:  # noqa: BLE001
            print("WARN: app_settings.project_dir read failed:", e)
    return dflt


def main():
    root = data_dir() / "mesh3d"
    print("data mesh3d root:", root)
    if not root.is_dir():
        print("ERROR: no mesh3d dir")
        return
    dirs = [d for d in sorted(root.iterdir()) if d.is_dir() and (d / "character.glb").exists()]
    if not dirs:
        print("No characters with character.glb found.")
        return
    print(f"Found {len(dirs)} character mesh(es) to re-rig:")
    for d in dirs:
        try:
            nm = json.loads((d / "meta.json").read_text(encoding="utf-8")).get("character_name")
        except Exception:  # noqa: BLE001
            nm = None
        print(f"  - {nm or d.name}")
    ok, fail = 0, 0
    for d in dirs:
        glb = d / "character.glb"
        try:
            meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            meta = {}
        nm = meta.get("character_name") or d.name
        tmp = d / "_rigged_norest_tmp.fbx"
        print(f"\n=== re-rigging {nm} (--no-rest) ===", flush=True)
        t0 = time.time()
        r = subprocess.run(
            [sys.executable, str(DRIVER), "--mesh", str(glb), "--out", str(tmp),
             "--models-dir", str(MODELS), "--device", "auto", "--no-rest", "--use-normal"],
            capture_output=True, text=True, timeout=2400, cwd=str(MIA_DIR))
        if not tmp.exists():
            fail += 1
            print(f"  FAILED (rc={r.returncode}). tail:")
            print("   " + "\n   ".join((r.stdout or "").strip().splitlines()[-8:]))
            continue
        # back up the old (sheared) rig, then swap in the clean one
        rigged = d / "rigged.fbx"
        if rigged.exists():
            bak = d / "rigged_prev_reset.fbx"
            try:
                if bak.exists():
                    bak.unlink()
                rigged.rename(bak)
            except Exception as e:  # noqa: BLE001
                print("  WARN: could not back up old rig:", e)
        tmp.rename(rigged)
        meta["rigged"] = True
        meta["rig_no_rest"] = True
        meta["rig_use_normal"] = True
        meta["rig_engine"] = meta.get("rig_engine") or "mia"
        try:
            (d / "meta.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass
        ok += 1
        print(f"  OK -> {rigged}  ({time.time()-t0:.0f}s)")
    print(f"\nDONE. re-rigged {ok} ok, {fail} failed.")
    print("Old sheared rigs backed up as rigged_prev_reset.fbx in each dir.")
    print("Now: Klein Pose tab -> 'Use 3D body' ON -> poses render on the clean body.")
    print("Tell Claude: rerig all done")


if __name__ == "__main__":
    main()
