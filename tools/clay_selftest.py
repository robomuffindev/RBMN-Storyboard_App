#!/usr/bin/env python3
"""Clay pose deformation self-test -- runs INSIDE the MIA venv (has bpy 4.3).

ONE run gives Claude everything to separate the two possible causes of the
broken clay pose references:
  (A) RIG MATH  -- clay_driver.py sends bones to wrong positions.  Signature:
      the mesh distorts even at a TINY rotation.
  (B) SKIN WEIGHTS -- MIA auto-rig bound vertices badly.  Signature: clean at
      small angles, tears/fans only at LARGE angles; weight_audit shows
      unweighted verts / crazy influence counts / vertex-group names that don't
      match the armature bones.

It (1) audits the rigged FBX's skin weights and (2) renders rest + isolated
single-bone poses at 15/45/90 deg by calling the REAL clay_driver.py, so the
renders reflect exactly what the app does.  Output -> <repo>/_diag/clay_selftest/.

Run:  runtime\\mia\\venv\\Scripts\\python.exe tools\\clay_selftest.py [character]
(default character = Duke).  Or just double-click clay_selftest.bat.
Stdlib + bpy only.
"""
from __future__ import annotations
import json, os, sqlite3, subprocess, sys, tempfile
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "_diag" / "clay_selftest"
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


def find_rigged_fbx(character: str):
    """Return (fbx_path, data_dir, char_dir_name) using the same resolution the app uses."""
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
            print("WARN: could not read app_settings.project_dir:", e)
    root = data_dir / "mesh3d"
    if not root.is_dir():
        return None, data_dir, None
    # match by meta.character_name, else the only rigged dir
    named, rigged = [], []
    for d in root.iterdir():
        fbx = d / "rigged.fbx"
        if not fbx.exists():
            continue
        rigged.append((d, fbx))
        try:
            meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            meta = {}
        if str(meta.get("character_name") or "").strip().lower() == character.lower():
            named.append((d, fbx))
    pick = named[0] if named else (rigged[0] if len(rigged) == 1 else None)
    if not pick:
        return None, data_dir, None
    return pick[1], data_dir, pick[0].name


def audit_weights(fbx: Path, report: dict):
    import bpy
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.fbx(filepath=str(fbx))
    arm = next((o for o in bpy.context.scene.objects if o.type == "ARMATURE"), None)
    meshes = [o for o in bpy.context.scene.objects if o.type == "MESH"]
    bone_names = sorted({b.name for b in arm.data.bones}) if arm else []
    report["armature_bones"] = bone_names
    report["armature_bone_count"] = len(bone_names)
    audits = []
    for m in meshes:
        vg = [g.name for g in m.vertex_groups]
        nverts = len(m.data.vertices)
        unweighted = 0
        infl_counts = []
        per_bone = defaultdict(float)
        maxinfl = 0
        for v in m.data.vertices:
            gs = [g for g in v.groups if g.weight > 1e-5]
            n = len(gs)
            infl_counts.append(n)
            maxinfl = max(maxinfl, n)
            if n == 0:
                unweighted += 1
            for g in gs:
                nm = m.vertex_groups[g.group].name if g.group < len(m.vertex_groups) else "?"
                per_bone[nm] += g.weight
        avg = sum(infl_counts) / max(1, len(infl_counts))
        vg_set = set(vg)
        bone_set = set(bone_names)
        top = sorted(per_bone.items(), key=lambda kv: kv[1], reverse=True)[:8]
        audits.append({
            "mesh": m.name,
            "verts": nverts,
            "vertex_groups": len(vg),
            "vertex_groups_matching_bones": len(vg_set & bone_set),
            "vertex_groups_NOT_matching_any_bone": sorted(vg_set - bone_set)[:20],
            "bones_with_no_vertex_group": sorted(bone_set - vg_set)[:20],
            "unweighted_verts": unweighted,
            "avg_influences_per_vert": round(avg, 3),
            "max_influences_per_vert": maxinfl,
            "top_bones_by_total_weight": [[k, round(x, 1)] for k, x in top],
        })
    report["weight_audit"] = audits


def render_battery(fbx: Path, report: dict):
    poses = [
        ("00_rest",           {}),
        ("01_upperarm_l_15",  {"upperarm_l": [0, 0, -15]}),
        ("02_upperarm_l_45",  {"upperarm_l": [0, 0, -45]}),
        ("03_upperarm_l_90",  {"upperarm_l": [0, 0, -90]}),
        ("04_lowerarm_l_90",  {"lowerarm_l": [0, -90, 0]}),
        ("05_thigh_l_15",     {"thigh_l": [15, 0, 0]}),
        ("06_thigh_l_45",     {"thigh_l": [45, 0, 0]}),
        ("07_thigh_l_90",     {"thigh_l": [90, 0, 0]}),
    ]
    report["battery"] = [p[0] for p in poses]
    job = {
        "fbx": str(fbx),
        "poses": [{"bones": b, "modelRotation": [0, 0, 0]} for _, b in poses],
        "width": 832, "height": 1216, "out_dir": str(OUT),
    }
    with tempfile.TemporaryDirectory(prefix="clay_selftest_") as td:
        jp = Path(td) / "job.json"
        jp.write_text(json.dumps(job), encoding="utf-8")
        driver = REPO / "backend" / "services" / "character_studio" / "vnccs_native" / "mia_local" / "clay_driver.py"
        r = subprocess.run([sys.executable, str(driver), "--job", str(jp)],
                           capture_output=True, text=True, timeout=900,
                           cwd=str(driver.parent))
        report["clay_driver_rc"] = r.returncode
        report["clay_driver_stdout_tail"] = (r.stdout or "").strip().splitlines()[-12:]
        report["clay_driver_stderr_tail"] = (r.stderr or "").strip().splitlines()[-12:]
    # rename pose_000.png -> labelled names so the images are self-describing
    for i, (label, _) in enumerate(poses):
        src = OUT / f"pose_{i:03d}.png"
        if src.exists():
            src.rename(OUT / f"{label}.png")
    report["rendered"] = sorted(p.name for p in OUT.glob("*.png"))


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    for old in OUT.glob("*.png"):
        try:
            old.unlink()
        except Exception:  # noqa: BLE001
            pass
    report = {"character": CHAR}
    fbx, data_dir, char_dir = find_rigged_fbx(CHAR)
    report["data_dir"] = str(data_dir)
    report["rigged_fbx"] = str(fbx) if fbx else None
    report["char_dir"] = char_dir
    if not fbx:
        report["ERROR"] = f"no rigged.fbx found for '{CHAR}' under {data_dir}/mesh3d"
        (OUT / "selftest_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print("ERROR:", report["ERROR"])
        return
    print(f"clay_selftest: character={CHAR}  fbx={fbx}")
    # renders FIRST (isolated subprocess), then weight audit (this process' bpy)
    try:
        render_battery(fbx, report)
        print("renders rc:", report.get("clay_driver_rc"), "->", report.get("rendered"))
    except Exception as e:  # noqa: BLE001
        report["render_error"] = str(e)
        print("render error:", e)
    try:
        audit_weights(fbx, report)
        wa = report.get("weight_audit") or []
        for a in wa:
            print(f"weights[{a['mesh']}]: verts={a['verts']} groups={a['vertex_groups']} "
                  f"matching_bones={a['vertex_groups_matching_bones']} "
                  f"unweighted={a['unweighted_verts']} "
                  f"avg_infl={a['avg_influences_per_vert']} max_infl={a['max_influences_per_vert']}")
    except Exception as e:  # noqa: BLE001
        report["weight_audit_error"] = str(e)
        print("weight audit error:", e)
    (OUT / "selftest_report.json").write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print("wrote ->", OUT)
    print("Tell Claude: selftest done")


if __name__ == "__main__":
    main()
