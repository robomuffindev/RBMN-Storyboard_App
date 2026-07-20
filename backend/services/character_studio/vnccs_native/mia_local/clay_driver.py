"""RBMN clay pose renderer (v1.175) -- runs INSIDE the MIA venv (has bpy 4.3).

Applies VNCCS pose-library poses to a character's MIA-rigged Mixamo FBX and
renders untextured CLAY captures -- pose references with the character's REAL
body shape (the whole point of the Tier-1 3D pipeline).

    python clay_driver.py --job job.json
    job.json: {"fbx": path, "poses": [{"bones": {mh_bone: [rx,ry,rz]deg},
               "modelRotation": [rx,ry,rz]deg}], "width": W, "height": H,
               "out_dir": dir}
    -> out_dir/pose_000.png ... (RGBA, transparent bg; caller composites)

Pose math mirrors pose_render._apply_pose (the VNCCS three.js viewer):
rotations are Euler XYZ (Rx@Ry@Rz) in WORLD-AXIS-ALIGNED per-bone frames --
NOT bone-local frames.  Per bone we want deformation D = W @ T(-head_rest),
with W = W_parent @ T(head_rel) @ R.  In Blender: pose_bone.matrix =
D @ rest_matrix.  The viewer's axes are Y-up; Blender is Z-up, so rotation
matrices are conjugated by the axis swap C (viewer->Blender) while
translations come from the ACTUAL rig's rest heads (real proportions).
Progress protocol: CLAY_POSE i/n, CLAY_DONE n on stdout.
"""
import argparse
import json
import math
import os
import sys
import traceback

# MakeHuman game_engine rig -> Mixamo (fingers intentionally omitted:
# MIA rigs with no_fingers merge them into the hand)
MH2MIX = {
    "pelvis": "Hips", "spine_01": "Spine", "spine_02": "Spine1",
    "spine_03": "Spine2", "neck_01": "Neck", "head": "Head",
    "clavicle_l": "LeftShoulder", "upperarm_l": "LeftArm",
    "lowerarm_l": "LeftForeArm", "hand_l": "LeftHand",
    "clavicle_r": "RightShoulder", "upperarm_r": "RightArm",
    "lowerarm_r": "RightForeArm", "hand_r": "RightHand",
    "thigh_l": "LeftUpLeg", "calf_l": "LeftLeg", "foot_l": "LeftFoot",
    "ball_l": "LeftToeBase",
    "thigh_r": "RightUpLeg", "calf_r": "RightLeg", "foot_r": "RightFoot",
    "ball_r": "RightToeBase",
    "Root": "Hips", "root": "Hips",
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--job", required=True)
    args = ap.parse_args()
    with open(args.job, encoding="utf-8") as f:
        job = json.load(f)

    import bpy
    from mathutils import Matrix

    def rot_xyz(rx, ry, rz):
        return (Matrix.Rotation(rx, 4, "X") @ Matrix.Rotation(ry, 4, "Y")
                @ Matrix.Rotation(rz, 4, "Z"))

    D2R = math.pi / 180.0

    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.fbx(filepath=job["fbx"])
    arm = next((o for o in bpy.context.scene.objects if o.type == "ARMATURE"), None)
    if arm is None:
        print("clay_driver: no armature in FBX", file=sys.stderr, flush=True)
        return 3
    meshes = [o for o in bpy.context.scene.objects if o.type == "MESH"]
    # v1.175.1: PRESERVE importer object transforms (FBX often parks a -90X
    # rotation and/or 0.01 scale on the objects); modelRotation composes on top.
    roots = [o for o in [arm, *meshes] if o.parent is None]
    base_mw = {o.name: o.matrix_world.copy() for o in roots}

    # bone name prefix handling ("mixamorig:Hips" / "mixamorig_Hips" / "Hips")
    bone_names = {b.name for b in arm.data.bones}

    def find_bone(mix: str):
        for cand in (f"mixamorig:{mix}", f"mixamorig_{mix}", mix):
            if cand in bone_names:
                return cand
        low = mix.lower()
        return next((n for n in bone_names if n.lower().endswith(low)), None)

    mapped = {}
    for mh, mix in MH2MIX.items():
        b = find_bone(mix)
        if b:
            mapped[mh] = b
    print(f"CLAY_LOG mapped {len(mapped)} bones", flush=True)

    rest = {b.name: b.matrix_local.copy() for b in arm.data.bones}
    parents = {b.name: (b.parent.name if b.parent else None) for b in arm.data.bones}
    order = [b.name for b in arm.data.bones]  # Blender lists parents first

    # v1.175.1: the pose library's rotations are authored in the VIEWER's
    # Y-up world axes. Whether they need converting depends on the ARMATURE
    # SPACE the FBX imported with (importers vary: some bake Z-up into the
    # data, some keep Y-up and park a -90X on the object). Detect from the
    # rig itself: the Hips->Head direction in rest armature space IS "up".
    _hips = find_bone("Hips")
    _head = find_bone("Head")
    up_is_y = True
    if _hips and _head:
        v = rest[_head].translation - rest[_hips].translation
        up_is_y = abs(v.y) >= abs(v.z)
    if up_is_y:
        C = Matrix.Identity(4)      # armature space already matches the viewer
        C_inv = Matrix.Identity(4)
    else:
        C = Matrix.Rotation(math.pi / 2.0, 4, "X")   # viewer Y-up -> Z-up
        C_inv = C.inverted()
    print(f"CLAY_LOG armature up axis: {'Y (no conversion)' if up_is_y else 'Z (converting)'}", flush=True)

    # camera + render settings (Workbench clay)
    sc = bpy.context.scene
    sc.render.engine = "BLENDER_WORKBENCH"
    sc.display.shading.light = "STUDIO"
    sc.display.shading.color_type = "SINGLE"
    sc.display.shading.single_color = (0.72, 0.72, 0.72)
    sc.display.shading.show_cavity = True
    sc.render.film_transparent = True
    sc.render.resolution_x = int(job.get("width") or 832)
    sc.render.resolution_y = int(job.get("height") or 1216)
    sc.render.image_settings.file_format = "PNG"
    sc.render.image_settings.color_mode = "RGBA"
    cam_data = bpy.data.cameras.new("clay_cam")
    cam_data.type = "ORTHO"
    cam = bpy.data.objects.new("clay_cam", cam_data)
    sc.collection.objects.link(cam)
    sc.camera = cam
    cam.rotation_euler = (math.pi / 2.0, 0.0, 0.0)   # at -Y looking +Y, Z up

    out_dir = job["out_dir"]
    os.makedirs(out_dir, exist_ok=True)
    poses = job.get("poses") or []
    aspect = sc.render.resolution_x / max(1, sc.render.resolution_y)

    import mathutils
    Cw = Matrix.Rotation(math.pi / 2.0, 4, "X")   # viewer Y-up -> WORLD Z-up
    Cw_inv = Cw.inverted()

    def apply_pose(pose):
        bones_deg = (pose or {}).get("bones") or {}
        rot_by_bone = {}
        for mh, mix_name in mapped.items():
            r = bones_deg.get(mh)
            if isinstance(r, (list, tuple)) and len(r) >= 3 and any(abs(float(x)) > 1e-4 for x in r[:3]):
                R = rot_xyz(float(r[0]) * D2R, float(r[1]) * D2R, float(r[2]) * D2R)
                rot_by_bone[mix_name] = C @ R @ C_inv
        world = {}
        deform = {}
        for name in order:
            head = rest[name].translation
            par = parents[name]
            rel = head - (rest[par].translation if par else head * 0)
            L = Matrix.Translation(rel) @ rot_by_bone.get(name, Matrix.Identity(4))
            W = (world[par] @ L) if par else L
            world[name] = W
            deform[name] = W @ Matrix.Translation(-head)
        # model rotation (character turn) composes ON TOP of the importer's
        # object transforms, in WORLD axes
        mr = (pose or {}).get("modelRotation") or [0, 0, 0]
        MR = Matrix.Identity(4)
        if any(abs(float(x)) > 0.01 for x in mr[:3]):
            MR = Cw @ rot_xyz(float(mr[0]) * D2R, float(mr[1]) * D2R,
                              float(mr[2]) * D2R) @ Cw_inv
        for o in roots:
            o.matrix_world = MR @ base_mw[o.name]
        for name in order:                      # parents first
            pb = arm.pose.bones.get(name)
            if pb is None:
                continue
            pb.matrix = deform[name] @ rest[name]
            bpy.context.view_layer.update()

    def world_bbox():
        dg = bpy.context.evaluated_depsgraph_get()
        mn = mathutils.Vector((1e9, 1e9, 1e9))
        mx = mathutils.Vector((-1e9, -1e9, -1e9))
        for m in meshes:
            me = m.evaluated_get(dg)
            for v in me.to_mesh().vertices:
                w = me.matrix_world @ v.co
                mn = mathutils.Vector(map(min, mn, w))
                mx = mathutils.Vector(map(max, mx, w))
            me.to_mesh_clear()
        return mn, mx

    # v1.175.1: UNIFORM framing (VNCCS-style) -- pass 1 measures every pose so
    # ONE ortho scale keeps the character the same size across the whole set;
    # pass 2 renders (recentring per pose). ortho_scale is pinned to the
    # vertical sensor (portrait frames) so tall figures can never overflow.
    cam_data.sensor_fit = "VERTICAL" if sc.render.resolution_y >= sc.render.resolution_x else "HORIZONTAL"
    boxes = []
    need_scale = 0.01
    for pose in poses:
        apply_pose(pose)
        mn, mx = world_bbox()
        boxes.append((mn, mx))
        ext_x = max(mx.x - mn.x, 0.01)
        ext_z = max(mx.z - mn.z, 0.01)
        if cam_data.sensor_fit == "VERTICAL":
            # ortho_scale spans HEIGHT; width coverage = scale * aspect
            need_scale = max(need_scale, ext_z * 1.10, ext_x * 1.10 / max(aspect, 0.01))
        else:
            need_scale = max(need_scale, ext_x * 1.10, ext_z * 1.10 * aspect)
    cam_data.ortho_scale = need_scale
    print(f"CLAY_LOG uniform ortho_scale {need_scale:.3f}", flush=True)

    for pi, pose in enumerate(poses):
        apply_pose(pose)
        mn, mx = boxes[pi]
        center = (mn + mx) / 2.0
        depth = max(mx.y - mn.y, 0.01)
        cam.location = (center.x, mn.y - depth * 2.0 - 1.0, center.z)
        cam_data.clip_end = depth * 8.0 + 20.0
        out = os.path.join(out_dir, f"pose_{pi:03d}.png")
        sc.render.filepath = out
        bpy.ops.render.render(write_still=True)
        print(f"CLAY_POSE {pi + 1}/{len(poses)}", flush=True)

    print(f"CLAY_DONE {len(poses)}", flush=True)
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(0)  # skip bpy teardown (see driver.py)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        sys.exit(2)
