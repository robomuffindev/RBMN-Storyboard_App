#!/usr/bin/env python3
"""Test whether MIA --use-normal skinning cleans the arm smearing on EXTREME poses.

The no-rest bind is clean at rest, but big arm rotations (arms-down bind -> arms
up/crossed pose) still smear the auto-rig weights into fans that Klein paints as
planks.  --use-normal uses surface normals for skinning ("helps close limbs").
This re-rigs Duke's character.glb with --no-rest --use-normal and renders the
FAILURE regime (arms raised / crossed) so we can compare vs the current rig.
Output -> <repo>/_diag/usenormal_test/.  A few minutes on CPU.

Run:  runtime\\mia\\venv\\Scripts\\python.exe tools\\usenormal_test.py [character]
(default Duke) -- or double-click usenormal_test.bat.
"""
from __future__ import annotations
import json, os, sqlite3, subprocess, sys, tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "_diag" / "usenormal_test"
MIA_DIR = REPO / "backend" / "services" / "character_studio" / "vnccs_native" / "mia_local"
DRIVER = MIA_DIR / "driver.py"
CLAY = MIA_DIR / "clay_driver.py"
MODELS = REPO / "runtime" / "mia" / "models"
CHAR = (sys.argv[1].strip() if len(sys.argv) > 1 and sys.argv[1].strip() else "Duke")

# EXTREME arm poses = the real failure regime, plus rest + a moderate one for reference.
BATTERY = [
    ("00_rest",             {}),
    ("01_arm_l_up_90",      {"upperarm_l": [0, 0, -90]}),
    ("02_arm_l_up_130",     {"upperarm_l": [0, 0, -130]}),
    ("03_both_arms_up",     {"upperarm_l": [0, 0, -130], "upperarm_r": [0, 0, 130]}),
    ("04_arms_crossed",     {"upperarm_l": [0, 0, -35], "lowerarm_l": [0, -110, 0],
                             "upperarm_r": [0, 0, 35], "lowerarm_r": [0, 110, 0]}),
    ("05_elbow_l_110",      {"lowerarm_l": [0, -110, 0]}),
]


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
        except Exception:  # noqa: BLE001
            pass
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


def render(fbx: Path, tag: str, report: dict):
    job = {"fbx": str(fbx),
           "poses": [{"bones": b, "modelRotation": [0, 0, 0]} for _, b in BATTERY],
           "width": 832, "height": 1216, "out_dir": str(OUT)}
    with tempfile.TemporaryDirectory(prefix="unt_") as td:
        jp = Path(td) / "job.json"
        jp.write_text(json.dumps(job), encoding="utf-8")
        cr = subprocess.run([sys.executable, str(CLAY), "--job", str(jp)],
                            capture_output=True, text=True, timeout=900, cwd=str(MIA_DIR))
        report[f"clay_rc_{tag}"] = cr.returncode
        report[f"clay_pose_lines_{tag}"] = [ln for ln in (cr.stdout or "").splitlines()
                                            if "CLAY_POSE" in ln or "CLAY_LOG" in ln]
    for i, (label, _) in enumerate(BATTERY):
        src = OUT / f"pose_{i:03d}.png"
        if src.exists():
            src.rename(OUT / f"{tag}_{label}.png")


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    for old in OUT.glob("*.png"):
        try:
            old.unlink()
        except Exception:  # noqa: BLE001
            pass
    report = {"character": CHAR}
    glb = find_character_glb(CHAR)
    report["character_glb"] = str(glb) if glb else None
    if not glb:
        print("ERROR: no character.glb for", CHAR)
        (OUT / "usenormal_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        return
    fbx = OUT / "rigged_norest_usenormal.fbx"
    print(f"usenormal_test: {CHAR}  glb={glb}")
    if fbx.exists():
        print("STEP 1/2  reusing existing use-normal rig (fast re-render with current clay_driver)...")
        report["rerig_rc"] = "skipped (reused)"
        render(fbx, "usenormal", report)
        report["rendered"] = sorted(p.name for p in OUT.glob("*.png"))
        (OUT / "usenormal_report.json").write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
        print("rendered:", report.get("rendered")); print("wrote ->", OUT); print("Tell Claude: usenormal done")
        return
    print("STEP 1/2  re-rigging with --no-rest --use-normal (loads ~2GB + inference; be patient)...")
    rr = subprocess.run(
        [sys.executable, str(DRIVER), "--mesh", str(glb), "--out", str(fbx),
         "--models-dir", str(MODELS), "--device", "auto", "--no-rest", "--use-normal"],
        capture_output=True, text=True, timeout=2400, cwd=str(MIA_DIR))
    report["rerig_rc"] = rr.returncode
    report["rerig_tail"] = (rr.stdout or "").strip().splitlines()[-12:]
    if not fbx.exists():
        print("ERROR: re-rig produced no fbx. tail:")
        print("\n".join(report["rerig_tail"]))
        (OUT / "usenormal_report.json").write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
        return
    print("STEP 2/2  rendering the extreme-arm battery on the USE-NORMAL rig...")
    render(fbx, "usenormal", report)
    report["rendered"] = sorted(p.name for p in OUT.glob("*.png"))
    (OUT / "usenormal_report.json").write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print("rendered:", report.get("rendered"))
    print("wrote ->", OUT)
    print("Tell Claude: usenormal done")


if __name__ == "__main__":
    main()
