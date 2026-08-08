#!/usr/bin/env python3
"""WELD / GAP TEST (v1.199.121) -- is this character's rigged mesh poseable at all?

Measures whether the scan TOPOLOGICALLY WELDS the arms to the torso -- the deepest
root cause of the pose saga (see HANDOVER_PROMPT.md sect.2-3 and project memory
`project_welded_arms_root_cause.md`). Posing fused geometry stretches membranes
("wings"); no rig, skinning or IK can fix topology. Run this after every mesh
regeneration BEFORE rigging poses on it.

Method: rays from sample points inside each arm toward the torso axis; the air gap
is the distance between the "exit arm" and "enter torso" surface crossings. A gap
below ~0.005 world units means the surfaces are fused there. A healthy T/A-pose
mesh has ZERO welded samples on the upper arms.

Run:  gap_test.bat [character]        (default Duke)
Then tell Claude: "gap test done".
"""
from __future__ import annotations
import json, os, sqlite3, subprocess, sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------- bpy payload
def _bpy_main(fbx_path: str) -> int:
    import bpy  # noqa: PLC0415 -- only importable inside the MIA venv
    import bmesh
    import statistics
    from mathutils import Vector
    from mathutils.bvhtree import BVHTree

    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.fbx(filepath=fbx_path)
    arm = next((o for o in bpy.context.scene.objects if o.type == "ARMATURE"), None)
    m = next((o for o in bpy.context.scene.objects if o.type == "MESH"), None)
    if arm is None or m is None:
        print("GAP_ERROR no armature/mesh in FBX")
        return 2
    bm = bmesh.new()
    bm.from_mesh(m.data)
    bmesh.ops.transform(bm, matrix=m.matrix_world, verts=bm.verts[:])
    bvh = BVHTree.FromBMesh(bm)
    bm.free()
    amw = arm.matrix_world

    def seg(name):
        b = arm.data.bones.get(name) or arm.data.bones.get(name.split(":")[-1])
        return (amw @ b.head_local, amw @ b.tail_local) if b else None

    hips = seg("mixamorig:Hips")
    spine2 = seg("mixamorig:Spine2")
    if not (hips and spine2):
        print("GAP_ERROR no Hips/Spine2 bones (not a Mixamo-style rig?)")
        return 2
    axis_p = (hips[0] + spine2[1]) / 2

    import math
    for nm in ("mixamorig:LeftArm", "mixamorig:RightArm"):
        sg = seg(nm)
        if sg:
            d = (sg[1] - sg[0]).normalized()
            ang = math.degrees(math.asin(max(-1.0, min(1.0, abs(d.z)))))
            print(f"GAP_BONE {nm.split(':')[-1]} {90 - ang:.0f}deg_from_vertical")

    verdict_bad = 0
    verdict_armpit = 0
    for side, names in (("L", ("mixamorig:LeftArm", "mixamorig:LeftForeArm", "mixamorig:LeftHand")),
                        ("R", ("mixamorig:RightArm", "mixamorig:RightForeArm", "mixamorig:RightHand"))):
        gaps = []
        for nm in names:
            sg = seg(nm)
            if sg is None:
                continue
            h, t = sg
            for k in range(1, 6):
                p = h.lerp(t, k / 5.0)
                d = Vector((axis_p.x - p.x, 0.0, axis_p.z - p.z))
                if d.length < 1e-6:
                    continue
                d.normalize()
                o = p.copy()
                crossings = []
                for _ in range(16):
                    hit = bvh.ray_cast(o, d, 0.8)
                    if hit[0] is None:
                        break
                    crossings.append((hit[0] - p).length)
                    o = hit[0] + d * 1e-4
                # v1.199.129: record WHERE along the bone (t=0 shoulder .. 1 elbow).
                # "4 welded samples" means two completely different things depending
                # on position: at t<=0.4 on the upper arm it is the ARMPIT, which on
                # a heavy body is a genuinely closed crevice even in a real T-pose
                # (flesh touches flesh, so SDF fusion there is faithful, not a
                # defect) AND it barely moves relative to the torso when posing.
                # At t>=0.6 the arm is glued to the FLANK down its length, which is
                # what tears membranes. The old report could not tell them apart.
                gaps.append((nm.split(":")[-1], k / 5.0,
                             crossings[1] - crossings[0] if len(crossings) >= 2 else 0.0))
        welded = [(n, t) for n, t, gp in gaps if gp < 0.005]
        upper = [(n, t) for n, t in welded if n.endswith("Arm") and "Fore" not in n]
        deep = [(n, t) for n, t in upper if t >= 0.6]
        open_g = [gp for _n, _t, gp in gaps if gp >= 0.005]
        med = f"{statistics.median(open_g):.3f}" if open_g else "n/a"
        print(f"GAP_{side} welded={len(welded)}/{len(gaps)} upper_arm_welded={len(upper)} "
              f"of_those_beyond_armpit={len(deep)} open={len(open_g)} median_gap={med}")
        print(f"GAP_{side}_SAMPLES " + " ".join(
            f"{n}@{t:.1f}={gp:.3f}" for n, t, gp in gaps
            if n.endswith("Arm") and "Fore" not in n))
        verdict_bad += len(deep)
        verdict_armpit += len(upper) - len(deep)
    if verdict_bad:
        print("GAP_VERDICT WELDED -- fusion reaches BEYOND the armpit (t>=0.6): the arm is "
              "bonded to the flank, posing will stretch membranes. Regenerate from a "
              "cleaner T-pose set.")
    elif verdict_armpit:
        print("GAP_VERDICT ARMPIT-ONLY -- the upper arm is free from t>=0.6 down; the only "
              "fusion is in the armpit crevice, which on this body is closed in real life "
              "too and barely moves when posing. Proceed, but watch the shoulder on the "
              "first pose battery.")
    else:
        print("GAP_VERDICT CLEAN upper arms -- proceed: rig, voxel skinning, pose battery")
    return 0


# ------------------------------------------------------------- host bootstrap
def _data_dir() -> Path:
    home = Path(os.path.expanduser(os.environ.get("PROJECT_DIR") or "~/RBMN-Projects"))
    for db in (home / "RBMN.db", Path(os.path.expanduser("~/RBMN-Projects")) / "RBMN.db"):
        if not db.exists():
            continue
        try:
            con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
            row = con.execute("SELECT project_dir FROM app_settings LIMIT 1").fetchone()
            con.close()
            if row and row[0]:
                return Path(os.path.expanduser(str(row[0])))
        except Exception:  # noqa: BLE001
            pass
    return home


def _find_fbx(character: str):
    root = _data_dir() / "mesh3d"
    if not root.is_dir():
        return None
    named, unnamed = [], []
    for d in sorted(root.iterdir()):
        fbx = d / "rigged.fbx"
        if not fbx.exists():
            continue
        try:
            meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            meta = {}
        nm = str(meta.get("character_name") or "").strip()
        (named if nm.lower() == character.lower() else (unnamed if not nm else [])).append(fbx)
    if named:
        return named[0]
    return unnamed[0] if len(unnamed) == 1 else None


def main() -> int:
    if "--bpy" in sys.argv:
        return _bpy_main(sys.argv[sys.argv.index("--bpy") + 1])
    char = next((a for a in sys.argv[1:] if not a.startswith("-")), "Duke")
    fbx = _find_fbx(char)
    if fbx is None:
        print(f"ERROR: no rigged.fbx for {char} under {_data_dir() / 'mesh3d'}")
        return 1
    print(f"gap test: {fbx}")
    sys.path.insert(0, str(REPO))
    from backend.services.character_studio.vnccs_native import mia_rig  # noqa: PLC0415
    r = subprocess.run([str(mia_rig._venv_python()), str(Path(__file__).resolve()),
                        "--bpy", str(fbx)],
                       capture_output=True, text=True, timeout=600)
    out = (r.stdout or "") + ("\n" + r.stderr if r.returncode != 0 else "")
    for ln in out.splitlines():
        if ln.startswith("GAP_") or ln.startswith("ERROR"):
            print(" ", ln)
    print('\nTell Claude: "gap test done"')
    return 0 if "GAP_VERDICT CLEAN" in out else (0 if "GAP_VERDICT" in out else r.returncode)


if __name__ == "__main__":
    sys.exit(main())
