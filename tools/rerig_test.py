#!/usr/bin/env python3
"""Re-rig a character's mesh with reset_to_rest=OFF and render the pose battery.

Root cause of the sheared clay arms: MIA rigs with reset_to_rest=True, which
force-reposes the mesh from its natural stance to a T-pose during rigging and
smears the arms into the BIND (visible even at rest, so the clay pose math was
never the culprit).  This proves the fix WITHOUT touching the app: re-rig the
already-generated character.glb with --no-rest (keep input pose), then render
rest + isolated single-bone poses on the NEW fbx.

Run:  runtime\\mia\\venv\\Scripts\\python.exe tools\\rerig_test.py [character]
(default Duke) -- or double-click rerig_test.bat.  Takes a few minutes (CPU MIA
loads ~2GB models).  Output -> <repo>/_diag/rerig_test/.
"""
from __future__ import annotations
import json, os, sqlite3, subprocess, sys, tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "_diag" / "rerig_test"
MIA_DIR = REPO / "backend" / "services" / "character_studio" / "vnccs_native" / "mia_local"
DRIVER = MIA_DIR / "driver.py"
CLAY = MIA_DIR / "clay_driver.py"
MODELS = REPO / "runtime" / "mia" / "models"
CHAR = (sys.argv[1].strip() if len(sys.argv) > 1 and sys.argv[1].strip() else "Duke")


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


def find_character_glb(character: str):
    dflt = default_project_dir()
    db = dflt / "RBMN.db"
    data_dir = dflt
    if db.exists():
        try:
            con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
            row = con.execute("SELECT project_dir FROM app_settings LIMIT 1").fetchone()
            con.close()
            if row and row[0]:
                data_dir = Path(os.path.expanduser(row[0]))
        except Exception as e:  # noqa: BLE001
            print("WARN: app_settings.project_dir read failed:", e)
    root = data_dir / "mesh3d"
    if not root.is_dir():
        return None
    named, any_glb = [], []
    for d in root.iterdir():
        glb = d / "character.glb"
        if not glb.exists():
            continue
        any_glb.append(glb)
        try:
            meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            meta = {}
        if str(meta.get("character_name") or "").strip().lower() == character.lower():
            named.append(glb)
    if named:
        return named[0]
    return any_glb[0] if len(any_glb) == 1 else None


BATTERY = [
    ("00_rest",           {}),
    ("01_upperarm_l_15",  {"upperarm_l": [0, 0, -15]}),
    ("02_upperarm_l_45",  {"upperarm_l": [0, 0, -45]}),
    ("03_upperarm_l_90",  {"upperarm_l": [0, 0, -90]}),
    ("04_lowerarm_l_90",  {"lowerarm_l": [0, -90, 0]}),
    ("05_thigh_l_15",     {"thigh_l": [15, 0, 0]}),
    ("06_thigh_l_45",     {"thigh_l": [45, 0, 0]}),
    ("07_thigh_l_90",     {"thigh_l": [90, 0, 0]}),
]


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    for old in OUT.glob("*.png"):
        try:
            old.unlink()
        except Exception:  # noqa: BLE001
            pass
    report = {"character": CHAR, "reset_to_rest": False}
    glb = find_character_glb(CHAR)
    report["character_glb"] = str(glb) if glb else None
    if not glb:
        print("ERROR: no character.glb found for", CHAR)
        (OUT / "rerig_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        return
    fbx = OUT / "rigged_norest.fbx"
    print(f"rerig_test: character={CHAR}  glb={glb}")
    print("STEP 1/2  re-rigging with --no-rest (keep input pose)... this loads ~2GB + runs inference; be patient")
    rr = subprocess.run(
        [sys.executable, str(DRIVER), "--mesh", str(glb), "--out", str(fbx),
         "--models-dir", str(MODELS), "--device", "auto", "--no-rest"],
        capture_output=True, text=True, timeout=2400, cwd=str(MIA_DIR))
    report["rerig_rc"] = rr.returncode
    report["rerig_stdout_tail"] = (rr.stdout or "").strip().splitlines()[-15:]
    report["rerig_stderr_tail"] = (rr.stderr or "").strip().splitlines()[-15:]
    if not fbx.exists():
        print("ERROR: re-rig produced no fbx. tail:")
        print("\n".join(report["rerig_stdout_tail"]))
        (OUT / "rerig_report.json").write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
        return
    print("STEP 2/2  rendering pose battery on the NO-REST rig...")
    job = {"fbx": str(fbx),
           "poses": [{"bones": b, "modelRotation": [0, 0, 0]} for _, b in BATTERY],
           "width": 832, "height": 1216, "out_dir": str(OUT)}
    with tempfile.TemporaryDirectory(prefix="rerig_") as td:
        jp = Path(td) / "job.json"
        jp.write_text(json.dumps(job), encoding="utf-8")
        cr = subprocess.run([sys.executable, str(CLAY), "--job", str(jp)],
                            capture_output=True, text=True, timeout=900, cwd=str(MIA_DIR))
        report["clay_rc"] = cr.returncode
        report["clay_stdout_tail"] = (cr.stdout or "").strip().splitlines()[-10:]
    for i, (label, _) in enumerate(BATTERY):
        src = OUT / f"pose_{i:03d}.png"
        if src.exists():
            src.rename(OUT / f"{label}.png")
    report["rendered"] = sorted(p.name for p in OUT.glob("*.png"))
    (OUT / "rerig_report.json").write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print("rerig_rc", report.get("rerig_rc"), " clay_rc", report.get("clay_rc"))
    print("rendered:", report.get("rendered"))
    print("wrote ->", OUT)
    print("Tell Claude: rerig done")


if __name__ == "__main__":
    main()
