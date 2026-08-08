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
    # v1.199.86: preserve_volume (dual-quaternion skinning) is now OPT-IN and
    # DEFAULTS OFF.  It was added in v1.199.71 for the "candy-wrapper" collapse and
    # noted then as having no visible effect -- but DQ skinning normalises by the
    # SUM OF WEIGHTS, so any vertex the auto-rig left unweighted divides by ~0 and
    # is flung thousands of units.  That is the shape of the 19000x / 3769x
    # maxstretch on arm poses (rest is fine because rotation is identity there),
    # and it survived a full re-rig, which a bind-shear problem would not.
    # v1.199.97: WELD DEGENERATE GEOMETRY. Measured cause, not a guess -- the
    # stretch audit named the dominant bone at BOTH ends of every exploding edge
    # and they are the SAME bone (LeftArm<->LeftArm 2841x, RightUpLeg<->RightUpLeg
    # 1781x). A rigid rotation cannot separate two vertices driven by one bone, so
    # the tearing is not weight bleed between limbs. What those edges DO share is
    # a near-zero rest length: 0.00047, 0.00475, 0.00539 in a mesh ~2 units tall,
    # where the average edge is ~0.01. They are duplicate/degenerate vertices from
    # the Hunyuan3D reconstruction, and near-coincident verts that land in even
    # slightly different weight blends fly apart under deformation. Welding them
    # removes the defect at the source; ~10x below the average edge so real detail
    # is untouched.
    # MEASURED: this mesh is ~200 world units tall (CLAY_DISP bbox extent 200.14),
    # not ~2 as assumed in v97 -- so a fixed 0.001 weld was 0.0005% of body height
    # and merged exactly ONE vertex. Threshold must scale with the mesh.
    _wrel = float(job.get("weld_rel", 0.0005) or 0.0)
    _weld = float(job.get("weld_dist") or 0.0)
    if _weld <= 0.0 and _wrel > 0.0:
        try:
            _lo = [1e30] * 3; _hi = [-1e30] * 3
            for _m in meshes:
                for _v in _m.data.vertices:
                    for _k in range(3):
                        _lo[_k] = min(_lo[_k], _v.co[_k]); _hi[_k] = max(_hi[_k], _v.co[_k])
            _ext = max(_hi[_k] - _lo[_k] for _k in range(3))
            _weld = _wrel * _ext
            print(f"CLAY_LOG mesh extent={_ext:.3f} -> weld_dist={_weld:.5f} "
                  f"(weld_rel={_wrel})", flush=True)
        except Exception:  # noqa: BLE001
            _weld = 0.0
    if _weld > 0.0:
        try:
            import bmesh as _bm
            for _m in meshes:
                _b = _bm.new()
                _b.from_mesh(_m.data)
                _before = len(_b.verts)
                _bm.ops.remove_doubles(_b, verts=_b.verts[:], dist=_weld)
                _after = len(_b.verts)
                _b.to_mesh(_m.data)
                _b.free()
                _m.data.update()
                print(f"CLAY_LOG weld {_m.name}: {_before} -> {_after} verts "
                      f"({_before - _after} merged at dist={_weld})", flush=True)
        except Exception as _we:  # noqa: BLE001
            print(f"CLAY_LOG weld skipped ({_we})", flush=True)

    # v1.199.98: optional RE-SKIN. MIA's weights are the suspect that survives
    # every other explanation; Blender's own heat-diffusion automatic weights are
    # a well-tested alternative over the SAME skeleton, so this is an A/B of the
    # weights alone with nothing else changed. Falls back silently to MIA's
    # weights if bone-heat fails (it does on non-manifold meshes), and says so.
    # v1.199.103: DEFAULT BACK TO OFF. The re-skin measured 17x lower peak stretch
    # (v99) but it (a) broke the object's world transform, (b) changed the weight
    # distribution drastically -- arm-dominant verts fell 49,251 -> 10,707, which
    # suggests bone-heat did a poor job on parts of this mesh -- and (c) does NOT
    # address the measured root cause, which is arms buried inside the torso.
    # Too many variables at once. Available via clay_reskin="blender" for a clean
    # A/B once abduction is proven.
    if str(job.get("reskin", "off") or "").strip().lower() == "voxel":
        try:
            import time as _t
            sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
            import voxel_skin
            _t0 = _t.time()
            for _m in meshes:
                _bn, _packed = voxel_skin.compute(_m, arm, res=int(job.get("voxel_res", 110)),
                                                  log=lambda x: print("CLAY_LOG " + x, flush=True))
                if _bn is None:
                    raise RuntimeError("voxelization produced no solid")
                voxel_skin.apply_to_object(_m, _bn, _packed)
            bpy.context.view_layer.update()
            print(f"CLAY_LOG reskin=voxel OK in {_t.time()-_t0:.1f}s", flush=True)
        except Exception as _ve:  # noqa: BLE001
            print(f"CLAY_LOG reskin=voxel FAILED ({_ve}) -- keeping MIA weights", flush=True)
    if str(job.get("reskin", "off") or "").strip().lower() in ("blender", "heat", "auto"):
        try:
            # v1.199.112: a skinned FBX parents its meshes TO THE ARMATURE. Setting
            # `.parent = None` directly drops that relationship WITHOUT compensating,
            # so the object jumps by the old parent-inverse -- here the importer's
            # -90 X rotation. keep_transform=True then faithfully preserved the
            # already-wrong transform, and every re-skinned render came out viewed
            # from UNDERNEATH the figure (Lorenzo spotted it: "like a person
            # standing on glass photographed from below"). I had read those renders
            # as a destroyed mesh and written the re-skin off; the mesh was fine,
            # the camera was under it. Restore the world matrix across the unparent.
            for _m in meshes:
                for _md in list(_m.modifiers):
                    if _md.type == "ARMATURE":
                        _m.modifiers.remove(_md)
                _m.vertex_groups.clear()
                _mw_keep = _m.matrix_world.copy()
                _m.parent = None
                _m.matrix_world = _mw_keep
            bpy.ops.object.select_all(action="DESELECT")
            for _m in meshes:
                _m.select_set(True)
            arm.select_set(True)
            bpy.context.view_layer.objects.active = arm
            bpy.context.view_layer.update()
            _mw_before = {_m.name: _m.matrix_world.copy() for _m in meshes}
            # keep_transform=True is the documented way to preserve the object's
            # world matrix across a re-parent. Setting matrix_world afterwards did
            # NOT take (ortho_scale stayed at the local-space 262.634).
            bpy.ops.object.parent_set(type="ARMATURE_AUTO", keep_transform=True)
            # BUG FOUND (v1.199.102): parent_set REPARENTS the mesh to the
            # armature and rebuilds its world transform, discarding the FBX
            # importer's 0.01 scale that used to live on the object. Symptom:
            # uniform ortho_scale jumped 2.202 -> 262.634 (i.e. framing switched
            # from world units to local) and the figure rendered tiny --
            # background 60.8% -> 87.9% of frame. The v1.199.99 percentile bbox
            # was treating this symptom. Restore the transform explicitly.
            for _m in meshes:
                if _m.name in _mw_before:
                    _m.matrix_world = _mw_before[_m.name]
            bpy.context.view_layer.update()
            _vg = sum(len(_m.vertex_groups) for _m in meshes)
            _vgb = sum(1 for _m in meshes for vg in _m.vertex_groups
                       if vg.name in {b.name for b in arm.data.bones})
            _bb = [1e30, 1e30, 1e30, -1e30, -1e30, -1e30]
            for _m in meshes:
                for _v in _m.data.vertices:
                    _w = _m.matrix_world @ _v.co
                    for _k in range(3):
                        _bb[_k] = min(_bb[_k], _w[_k]); _bb[3 + _k] = max(_bb[3 + _k], _w[_k])
            _ext = [_bb[3] - _bb[0], _bb[4] - _bb[1], _bb[5] - _bb[2]]
            _up = "XYZ"[max(range(3), key=lambda k: _ext[k])]
            print(f"CLAY_LOG reskin world bbox extents X={_ext[0]:.2f} Y={_ext[1]:.2f} "
                  f"Z={_ext[2]:.2f} -> tallest axis {_up} (expect Z for an upright figure)",
                  flush=True)
            print(f"CLAY_LOG reskin=blender heat weights OK "
                  f"(vertex_groups={_vg} matching_bones={_vgb}, world transform restored)",
                  flush=True)
        except Exception as _re:  # noqa: BLE001
            print(f"CLAY_LOG reskin=blender FAILED ({_re}) -- keeping MIA weights", flush=True)

    _pv = bool(job.get("preserve_volume", False))
    _orphans = 0
    for _m in meshes:
        for _md in _m.modifiers:
            if _md.type == "ARMATURE":
                _md.use_deform_preserve_volume = _pv
    # v1.199.95: CORRECTIVE SMOOTH after the armature deform. The auto-rig's
    # weights at the armpit are noisy where the arm surface nearly touches the
    # torso, so a big arm rotation shreds the upper arm into thin sheets that
    # vanish in the render -- the "hands are up but the arms are missing" result.
    # This modifier exists for exactly that failure: it relaxes deformation
    # artefacts while leaving the rest pose untouched, with no re-rig required.
    _cs = float(job.get("corrective_smooth", 1.0) or 0.0)
    _csi = int(job.get("corrective_smooth_iters", 20) or 0)
    if _cs > 0.0 and _csi > 0:
        for _m in meshes:
            try:
                _sm = _m.modifiers.new(name="rbmn_corrective", type="CORRECTIVE_SMOOTH")
                _sm.smooth_type = "LENGTH_WEIGHTED"
                _sm.factor = _cs
                _sm.iterations = _csi
                _sm.use_only_smooth = False
            except Exception as _cse:  # noqa: BLE001
                print(f"CLAY_LOG corrective smooth unavailable ({_cse})", flush=True)
                break
    print(f"CLAY_LOG corrective_smooth={_cs} iters={_csi}", flush=True)
    # v1.199.86: ORPHAN-WEIGHT REPAIR -- the actual cause of the arm blow-up.
    # Make-It-Animatable leaves a slice of vertices with NO armature weight at all
    # (4818 of ~241k on Duke). Under any rotation those verts stay pinned at their
    # bind position while every neighbour swings away with the bone, so the edges
    # between them stretch by the full travel of the limb -- an arm rotating 120
    # degrees over a ~0.001 edge is exactly the 2841x / 3769x maxstretch we kept
    # measuring. Rest looks perfect because rotation is identity there, and a
    # re-rig does not help because MIA reproduces the same gap every time.
    # Fix: give each orphan the weights of its nearest WEIGHTED vertex (the same
    # nearest-bone fallback pose_render already does for the mannequin). Cheap,
    # deterministic, and it needs no rig-side change -- so it stays automatable.
    _fix_orphans = bool(job.get("fix_orphan_weights", True))
    _repaired = 0
    try:
        from mathutils.kdtree import KDTree
        _bone_names = {b.name for b in arm.data.bones}
        for _m in meshes:
            _dg = {vg.index for vg in _m.vertex_groups if vg.name in _bone_names}
            if not _dg:
                continue
            _weighted, _orph = [], []
            for _v in _m.data.vertices:
                (_weighted if any((g.group in _dg and g.weight > 1e-6)
                                  for g in _v.groups) else _orph).append(_v.index)
            _orphans += len(_orph)
            if not (_fix_orphans and _orph and _weighted):
                continue
            _kd = KDTree(len(_weighted))
            for _i in _weighted:
                _kd.insert(_m.data.vertices[_i].co, _i)
            _kd.balance()
            _groups = _m.vertex_groups
            for _oi in _orph:
                _co, _si, _ = _kd.find(_m.data.vertices[_oi].co)
                for g in _m.data.vertices[_si].groups:
                    if g.group in _dg and g.weight > 1e-6:
                        _groups[g.group].add([_oi], g.weight, "REPLACE")
                _repaired += 1
    except Exception as _oe:  # noqa: BLE001
        print(f"CLAY_LOG orphan repair skipped ({_oe})", flush=True)
        _orphans = -1
    print(f"CLAY_LOG preserve_volume={_pv} unweighted_verts={_orphans} "
          f"orphans_repaired={_repaired}", flush=True)
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

    # v1.199.83: DEPTH MODE.  A clay render carries the character's real volume
    # but Klein reads it as a soft *reference*, so the pose LoRA's body prior
    # still wins.  A DEPTH MAP of the same rigged mesh carries pose AND volume
    # AND height in the one channel the RefControl depth LoRA is trained to obey.
    render_mode = str(job.get("render_mode") or "shaded").strip().lower()
    want_depth = render_mode == "depth"

    # camera + render settings (Workbench clay)
    sc = bpy.context.scene
    sc.render.engine = "BLENDER_WORKBENCH"
    sc.display.shading.light = "STUDIO"
    sc.display.shading.color_type = "SINGLE"
    sc.display.shading.single_color = (0.72, 0.72, 0.72)
    sc.display.shading.show_cavity = True
    sc.render.film_transparent = not want_depth
    sc.render.resolution_x = int(job.get("width") or 832)
    sc.render.resolution_y = int(job.get("height") or 1216)
    sc.render.image_settings.file_format = "PNG"
    sc.render.image_settings.color_mode = "BW" if want_depth else "RGBA"
    if want_depth:
        # 16-bit: the bbox near/far mapping is CORRECT but conservative -- the
        # visible surface only occupies the front slice of the body's depth
        # extent, so an 8-bit render leaves the whole figure squeezed into the
        # top ~25 levels (production refs came out near-uniform white). We keep
        # the safe bbox mapping here and re-normalise over the VISIBLE pixels in
        # pose_clay; 16 bits means that re-stretch has no banding to give away.
        sc.render.image_settings.color_depth = "16"
    depth_range = None
    if want_depth:
        # The view transform MUST be Standard: Filmic/AgX would push our linear
        # 0..1 depth through a film curve and the map would no longer be metric.
        try:
            sc.view_settings.view_transform = "Standard"
            sc.view_settings.look = "None"
            sc.view_settings.exposure = 0.0
            sc.view_settings.gamma = 1.0
        except Exception:  # noqa: BLE001
            pass
        sc.view_layers[0].use_pass_z = True
        sc.use_nodes = True
        nt = sc.node_tree
        for _n in list(nt.nodes):
            nt.nodes.remove(_n)
        _rl = nt.nodes.new("CompositorNodeRLayers")
        _mr = nt.nodes.new("CompositorNodeMapRange")
        _iv = nt.nodes.new("CompositorNodeInvert")
        _cp = nt.nodes.new("CompositorNodeComposite")
        _mr.use_clamp = True
        _mr.inputs["To Min"].default_value = 0.0
        _mr.inputs["To Max"].default_value = 1.0
        _zsock = _rl.outputs.get("Depth") or _rl.outputs.get("Z")
        if _zsock is None:
            print("clay_driver: no Z pass output on this engine", file=sys.stderr, flush=True)
            return 4
        nt.links.new(_zsock, _mr.inputs["Value"])
        nt.links.new(_mr.outputs[0], _iv.inputs["Color"])
        nt.links.new(_iv.outputs[0], _cp.inputs["Image"])
        # near -> 0 -> inverted to WHITE; far and the 1e10 background -> clamped
        # to 1 -> inverted to BLACK.  Same convention as pose_render's depth and
        # as DepthAnythingV2, which is what the RefControl depth LoRA saw.
        depth_range = _mr
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

    # v1.199.100: ARM ABDUCTION. The pose library's rotations were authored for
    # an average-width mannequin; on a much wider body the same "arms at the
    # side" rotation puts the arm INSIDE the torso. Swinging the upper arms
    # outward by a fixed extra angle restores the clearance the pose author
    # assumed. Sign convention matches the pose data itself (upperarm_l raises
    # with a NEGATIVE z, upperarm_r with a positive one). 0 = off.
    _abduct = float(job.get("arm_abduct_deg", 0.0) or 0.0)
    _auto_abduct = bool(job.get("auto_abduct", True))
    _abduct_margin = float(job.get("abduct_margin", 1.06) or 1.06)
    _abduct_max = float(job.get("abduct_max_deg", 60.0) or 60.0)
    _abduct_now = {}          # mh bone -> signed degrees, set by the two-pass below

    # v1.199.101: TORSO WIDTH PROFILE, built once from the REST mesh. World axes
    # here are X = width, Y = depth, Z = height (camera sits at -Y looking +Y,
    # Z up). Half-width per height bin, 97th percentile so a stray vertex cannot
    # inflate it. This is what the arms have to clear.
    # Bone-role sets derived from OUR OWN MakeHuman->Mixamo map rather than by
    # string-matching Mixamo names. Guessing at names is what produced
    # "torso_verts=0" while 22 groups existed: the classifier was looking for
    # substrings that the actual dominant groups do not contain.
    _ARM_MH = ("upperarm", "lowerarm", "hand", "clavicle", "shoulder")
    _TORSO_MH = ("spine", "pelvis", "neck", "hips", "root", "chest", "torso")
    ARM_BONES = {v for k, v in mapped.items() if any(t in k.lower() for t in _ARM_MH)}
    SHOULDER_BONES = {v for k, v in mapped.items()
                      if any(t in k.lower() for t in ("clavicle", "shoulder"))}
    ARM_BONES = ARM_BONES - SHOULDER_BONES
    TORSO_BONES = {v for k, v in mapped.items() if any(t in k.lower() for t in _TORSO_MH)}
    print(f"CLAY_LOG bone roles: arm={sorted(ARM_BONES)} torso={sorted(TORSO_BONES)}", flush=True)
    print(f"CLAY_LOG mh->mix map: {sorted(mapped.items())}", flush=True)

    _torso_prof = None
    try:
        import numpy as _np
        _bn_all = {b.name for b in arm.data.bones}
        _pts = []
        for _m in meshes:
            _dgi = {vg.index: vg.name for vg in _m.vertex_groups if vg.name in _bn_all}
            # v1.199.104: build in ARMATURE space, not world. modelRotation yaws
            # the whole body at pose time, so a profile measured in world axes
            # stops describing the body the moment a pose turns it -- which is
            # exactly why abduction helped the front pose (78.8 -> 51.9%) and made
            # the YAWED pose worse (66.8 -> 79.0%): it was pushing the arm along a
            # world axis that no longer pointed sideways on the character.
            # Armature space is Y-up here (logged: "armature up axis: Y").
            _mw = arm.matrix_world.inverted() @ _m.matrix_world
            for _v in _m.data.vertices:
                _gs = [(g.weight, g.group) for g in _v.groups if g.group in _dgi]
                if not _gs:
                    continue
                _nm = _dgi[max(_gs)[1]]
                if _nm in TORSO_BONES:
                    _w = _mw @ _v.co
                    _pts.append((_w.x, _w.y))       # lateral X, height Y
        print(f"CLAY_LOG torso profile: collected {len(_pts)} torso vertices", flush=True)
        if len(_pts) <= 200:
            _hist = {}
            for _m in meshes:
                _dgi2 = {vg.index: vg.name for vg in _m.vertex_groups}
                for _v in _m.data.vertices:
                    _gs2 = [(g.weight, g.group) for g in _v.groups]
                    if _gs2:
                        _hist[_dgi2.get(max(_gs2)[1], "?")] = _hist.get(
                            _dgi2.get(max(_gs2)[1], "?"), 0) + 1
            _top = sorted(_hist.items(), key=lambda kv: -kv[1])[:14]
            print(f"CLAY_LOG dominant-group histogram: {_top}", flush=True)
        if len(_pts) > 200:
            _P = _np.asarray(_pts)
            _zmin, _zmax = _P[:, 1].min(), _P[:, 1].max()
            _nb = 40
            _edges = _np.linspace(_zmin, _zmax, _nb + 1)
            _idx = _np.clip(_np.digitize(_P[:, 1], _edges) - 1, 0, _nb - 1)
            _hw = _np.zeros(_nb)
            for _b in range(_nb):
                _sel = _P[_idx == _b, 0]
                _hw[_b] = _np.percentile(_np.abs(_sel), 97) if _sel.size >= 5 else 0.0
            # forward-fill empty bins so lookups never hit a hole
            for _b in range(1, _nb):
                if _hw[_b] == 0.0:
                    _hw[_b] = _hw[_b - 1]
            _torso_prof = (_zmin, _zmax, _hw)
            print(f"CLAY_LOG torso half-width profile (armature Y): [{_zmin:.1f},{_zmax:.1f}] "
                  f"max={_hw.max():.2f} median={_np.median(_hw[_hw > 0]):.2f}", flush=True)
    except Exception as _tpe:  # noqa: BLE001
        print(f"CLAY_LOG torso profile unavailable ({_tpe})", flush=True)

    def _half_width_at(y):
        if not _torso_prof:
            return None
        _zmin, _zmax, _hw = _torso_prof
        if _zmax - _zmin < 1e-6:
            return None
        t = (y - _zmin) / (_zmax - _zmin)
        i = int(min(max(t, 0.0), 0.999) * len(_hw))
        return float(_hw[i])

    def _needed_abduction():
        """Extra outward angle (deg) per side so the arm clears the torso.

        MEASURED cause (v1.199.100): 78.8% of arm vertices land INSIDE the torso
        on library poses, because the pose library stores bone angles authored
        for an average-width mannequin. Computed from the character's own torso
        profile, so it is zero for a slim build and only grows as far as this
        body actually needs -- no slider, nothing per-character to set.
        """
        out = {}
        if not _torso_prof:
            return out
        import math as _m2
        for mh in ("upperarm_l", "upperarm_r"):
            mix = mapped.get(mh)
            if not mix:
                continue
            pb = arm.pose.bones.get(mix)
            if pb is None:
                continue
            S = pb.head          # armature space -- immune to modelRotation
            need = 0.0
            # v1.199.107: burial only happens when the arm hangs DOWN alongside a
            # wide torso. Once the elbow is at or above shoulder height the arm is
            # clear by construction, and the penetration metric over-reports there
            # because the deltoid legitimately overlaps the torso's own bbox at
            # that height. Correcting it anyway drove the arms-up pose to the
            # 60deg clamp and tore the arms into ribbons -- visible in the render,
            # invisible in the number. Gate on elbow elevation.
            _elb = None
            for _c in (pb.children_recursive[:1] or [pb]):
                _elb = _c.tail
            if _elb is not None and (_elb.y - S.y) > -0.05 * abs(S.y or 1.0):
                continue
            for child in (pb, *(pb.children_recursive[:2] if pb.children_recursive else ())):
                E = child.tail
                r = ((E.x - S.x) ** 2 + (E.y - S.y) ** 2) ** 0.5
                if r < 1e-6:
                    continue
                want = (_half_width_at(E.y) or 0.0) * _abduct_margin
                have = abs(E.x)
                if have >= want:
                    continue
                # rotate about the shoulder in the frontal plane until |x| == want
                # v1.199.106: solve the rotation with atan2 + BOTH asin branches.
                # asin alone only covers [-90,90], so for an arm RAISED above
                # horizontal it cannot tell "swing further out" from "swing back
                # across the body" and returns the opposite direction. Measured:
                # the yawed pose (arm low) improved 66.8 -> 39.8% while arms-up
                # (arm high) flipped sign and worsened 78.8 -> 80.0%. Same code,
                # opposite outcomes, split exactly on arm elevation.
                vx = E.x - S.x
                vy = E.y - S.y
                phi = _m2.atan2(vx, vy)          # 0 = straight up, +ve toward +X
                tgt_x = (want if E.x >= 0 else -want) - S.x
                if abs(tgt_x) > r:
                    continue                      # cannot reach: skip, do not clamp
                a1 = _m2.asin(max(-1.0, min(1.0, tgt_x / r)))
                best = None
                for cand in (a1, _m2.pi - a1):
                    dd = (cand - phi + _m2.pi) % (2.0 * _m2.pi) - _m2.pi
                    if best is None or abs(dd) < abs(best):
                        best = dd
                # NEGATE: phi = atan2(x, y) measures from +Y, but
                # Matrix.Rotation(t, 'Z') is standard right-handed
                # (x' = x cos t - y sin t), which increases atan2(y, x) and so
                # DECREASES atan2(x, y). Unit-tested both ways over arm-down,
                # arm-raised, both sides and arm-forward: negated passes 6/6,
                # un-negated 1/6 -- and un-negated is what shipped in v105, which
                # is why arms-up flipped sign and got worse.
                d = -_m2.degrees(best)
                if abs(d) > abs(need):
                    need = d
            if abs(need) > 0.01:
                out[mh] = max(-_abduct_max, min(_abduct_max, need))
        return out

    def apply_pose(pose):
        bones_deg = (pose or {}).get("bones") or {}
        if _abduct:
            bones_deg = dict(bones_deg)
            for _bn, _sgn in (("upperarm_l", -1.0), ("upperarm_r", 1.0)):
                _r = list(bones_deg.get(_bn) or [0.0, 0.0, 0.0])
                while len(_r) < 3:
                    _r.append(0.0)
                _r[2] = float(_r[2]) + _sgn * _abduct
                bones_deg[_bn] = _r
        # v1.199.70: apply each mapped bone's rotation in the BONE's OWN local
        # rest frame and let Blender do the FK -- instead of the old hand-rolled
        # world-axis / translation-only accumulation, which ignored each bone's
        # rest orientation.  That approximation held for the near-VERTICAL
        # legs/spine but sheared the near-HORIZONTAL Mixamo arm bones (broken even
        # at rest).  Rw is the world-axis (viewer) rotation; conjugating by the
        # bone's armature-space rest orientation Q yields the LOCAL rotation whose
        # world effect equals Rw:  Rl = Q^-1 @ Rw @ Q.  At rest every basis is
        # identity, so pose 0 is a pure Blender rest render (validates the bind).
        for name in order:
            pb = arm.pose.bones.get(name)
            if pb is not None:
                pb.matrix_basis = Matrix.Identity(4)
        for mh, mix_name in mapped.items():
            r = bones_deg.get(mh)
            _extra = _abduct_now.get(mh, 0.0)
            _has_rot = (isinstance(r, (list, tuple)) and len(r) >= 3
                        and any(abs(float(x)) > 1e-4 for x in r[:3]))
            # v1.199.105: do NOT skip a bone that has no pose rotation but DOES
            # need abduction -- an arm hanging straight down is exactly the case
            # that ends up inside a wide torso.
            if not _has_rot and abs(_extra) < 1e-4:
                continue
            pb = arm.pose.bones.get(mix_name)
            if pb is None:
                continue
            Q = rest[mix_name].to_quaternion().to_matrix()      # 3x3 armature-space rest orientation
            if _has_rot:
                Rw = (C @ rot_xyz(float(r[0]) * D2R, float(r[1]) * D2R,
                                  float(r[2]) * D2R) @ C_inv).to_3x3()
            else:
                Rw = Matrix.Identity(3)
            if abs(_extra) > 1e-4:
                # ABDUCTION AS A POST-ROTATION in armature space about the FORWARD
                # axis (Z; X is lateral, Y is up), so it swings the arm in the
                # frontal plane regardless of how the pose has already rotated it.
                # Injecting the angle into the bone's euler Z instead (v101-104)
                # composed it INSIDE the pose's own rotation, so for an arm already
                # flexed forward it stopped being abduction at all -- which is why
                # the yawed pose got WORSE (66.8 -> 75.8%) while the front-facing
                # one improved.
                Rw = Matrix.Rotation(_extra * D2R, 3, "Z") @ Rw
            Rl = Q.inverted() @ Rw @ Q
            pb.matrix_basis = Rl.to_4x4()
        # model rotation (character turn) composes ON TOP of the importer's
        # object transforms, in WORLD axes
        mr = (pose or {}).get("modelRotation") or [0, 0, 0]
        MR = Matrix.Identity(4)
        if any(abs(float(x)) > 0.01 for x in mr[:3]):
            MR = Cw @ rot_xyz(float(mr[0]) * D2R, float(mr[1]) * D2R,
                              float(mr[2]) * D2R) @ Cw_inv
        for o in roots:
            o.matrix_world = MR @ base_mw[o.name]
        bpy.context.view_layer.update()
        if _auto_abduct and not getattr(apply_pose, "_second", False):
            corr = _needed_abduction()
            if corr:
                apply_pose._second = True
                try:
                    _abduct_now.clear()
                    _abduct_now.update(corr)
                    print("CLAY_LOG auto-abduct " + " ".join(
                        f"{k}{v:+.1f}deg" for k, v in corr.items()), flush=True)
                    apply_pose(pose)
                finally:
                    apply_pose._second = False
                    _abduct_now.clear()

    # ============ DEV: IK CLEARANCE PASS (sandbox prototype) ==================
    # Replaces auto-abduction with a general mechanism: after FK, measure arm
    # penetration against a BVH of the NON-ARM body, then IK-solve each arm
    # chain to a target pushed OUT of the body along the local surface normal.
    # Lateral AND anterior clearance in one mechanism, scale-free (margins are
    # fractions of body height), no per-character knobs.
    from mathutils.bvhtree import BVHTree
    from mathutils import Vector

    ARM_SIDE = {"l": ("upperarm_l", "lowerarm_l", "hand_l"),
                "r": ("upperarm_r", "lowerarm_r", "hand_r")}

    def _body_bvh_and_height():
        """BVH of the posed NON-ARM body (world space) + body height."""
        dg = bpy.context.evaluated_depsgraph_get()
        polys = []
        hmin, hmax = 1e30, -1e30
        for m in meshes:
            ev = m.evaluated_get(dg)
            me = ev.to_mesh()
            dgi = {vg.index: vg.name for vg in m.vertex_groups}
            vdom = []
            for v in me.vertices:
                gs = [(g.weight, g.group) for g in v.groups]
                vdom.append(dgi.get(max(gs)[1], "") if gs else "")
            mw = ev.matrix_world
            wco = [mw @ v.co for v in me.vertices]
            for w in wco:
                hmin = min(hmin, w.z); hmax = max(hmax, w.z)
            for poly in me.polygons:
                vs = list(poly.vertices)
                arm_n = sum(1 for vi in vs if vdom[vi] in ARM_BONES)
                if arm_n * 2 <= len(vs):          # face NOT arm-dominated
                    polys.append([wco[vi].copy() for vi in vs])
            ev.to_mesh_clear()
        verts = []
        faces = []
        for poly in polys:
            base = len(verts)
            verts.extend(poly)
            faces.append(list(range(base, base + len(poly))))
        if not faces:
            return None, 1.0
        return BVHTree.FromPolygons(verts, faces), max(hmax - hmin, 1e-3)

    def _chain_violation(side, bvh, margin):
        """Worst penetration depth (world units) over samples along the arm.
        Returns (worst_depth, outward_normal_at_worst, worst_point, worst_bone_mh)."""
        worst = 0.0
        wdir = Vector((0, 0, 0))
        wpos = None
        wbone = None
        aw = arm.matrix_world
        for mh in ARM_SIDE[side]:
            mix = mapped.get(mh)
            pb = arm.pose.bones.get(mix) if mix else None
            if pb is None:
                continue
            h = aw @ pb.head
            t = aw @ pb.tail
            for k in range(1, 7):
                pt = h.lerp(t, k / 6.0)
                hit = bvh.find_nearest(pt)
                if hit is None or hit[0] is None:
                    continue
                co, nrm, _i, _d = hit
                sd = (pt - co).dot(nrm)          # signed: <0 = inside body
                depth = margin - sd              # how far short of clearance
                if sd < margin and depth > worst:
                    worst = depth
                    wdir = nrm.copy()
                    wpos = pt.copy()
                    wbone = mh
        return worst, wdir, wpos, wbone

    def ik_clearance(log_tag=""):
        bvh, height = _body_bvh_and_height()
        if bvh is None:
            print("CLAY_IK no torso faces -- skipped", flush=True)
            return
        margin = 0.015 * height                   # clearance margin
        aw = arm.matrix_world
        aw_inv = aw.inverted()
        for side in ("l", "r"):
            hand_pb = arm.pose.bones.get(mapped.get(ARM_SIDE[side][2]) or "")
            if hand_pb is None:
                continue
            viol0, vdir, vpos, vbone = _chain_violation(side, bvh, margin)
            if viol0 <= margin * 0.3:
                continue
            # TRAVEL-CAPPED incremental push: total travel bounded by the
            # measured violation depth (a push deeper than the burial is by
            # definition overshoot -- iteration 1 dragged wings of armpit
            # webbing across the frame by walking 18 fixed steps).
            max_push = viol0 * 2.5 + margin
            n_steps = 12
            step = max_push / n_steps
            tgt = bpy.data.objects.new(f"ik_tgt_{side}", None)
            sc.collection.objects.link(tgt)
            hand_w = (aw @ hand_pb.tail).copy()
            con = hand_pb.constraints.new("IK")
            con.target = tgt
            # violation in the upperarm needs the shoulder in the chain;
            # forearm/hand-only violations keep the upper arm where the pose
            # author put it (chain 2 distorts the intent far less).
            # chain 3 ALWAYS: an arm buried along its length (lateral flank
            # OR anterior belly) needs the shoulder to give; chain 2 just folds
            # the forearm inside the body (measured: pose 0 went 54.6 -> 62.9%).
            con.chain_count = 3
            tgt.location = hand_w
            trace = [(viol0, hand_w.copy())]
            travelled = 0.0
            cur = hand_w.copy()
            out = vdir.normalized() if vdir.length > 1e-6 else Vector((1 if side == "l" else -1, 0, 0))
            for _i in range(n_steps):
                cur = cur + out * step
                travelled += step
                tgt.location = cur
                bpy.context.view_layer.update()
                viol, vdir2, _p, _b = _chain_violation(side, bvh, margin)
                trace.append((viol, cur.copy()))
                if viol <= margin * 0.5:
                    break
                if vdir2.length > 1e-6:
                    out = vdir2.normalized()
            # with voxel skinning the flank no longer follows the arm, so
            # travel is cheap again: settle on the BEST violation found.
            bestv = min(v for v, _c in trace)
            pick = next(c for v, c in trace if v <= bestv + 1e-12)
            tgt.location = pick
            bpy.context.view_layer.update()
            violF, _d, _p, _b = _chain_violation(side, bvh, margin)
            print(f"CLAY_IK{log_tag} {side}: viol {viol0:.3f} -> {violF:.3f} "
                  f"(margin {margin:.3f} chain {con.chain_count} push {travelled:.3f}/{max_push:.3f})", flush=True)
            # bake: copy the IK result into the bones' matrices, drop constraint
            posed = {}
            dgx = bpy.context.evaluated_depsgraph_get()
            for mh in ARM_SIDE[side]:
                mix = mapped.get(mh)
                pb2 = arm.pose.bones.get(mix) if mix else None
                if pb2 is not None:
                    posed[mix] = pb2.matrix.copy()
            hand_pb.constraints.remove(con)
            bpy.data.objects.remove(tgt, do_unlink=True)
            for mix, mat in posed.items():
                arm.pose.bones[mix].matrix = mat
                bpy.context.view_layer.update()
    # ============ END DEV IK CLEARANCE ========================================

    # v1.199.72: SMEAR REMOVAL.  The auto-rig can't cleanly deform the armpit/
    # shoulder webbing under big arm rotations -- those faces stretch into thin
    # sheets ("planks") that Klein paints as extra limbs.  Preserve-volume can't
    # fix stretched TOPOLOGY, so per pose we bake the posed mesh into a temp
    # object and DROP any face with an edge stretched past STRETCH_TH x its rest
    # length.  The solid body/belly/legs are untouched; only the smear webbing
    # goes, leaving a small clean gap Klein fills naturally.
    STRETCH_TH = float(job.get("smear_stretch") or 2.0)
    # fraction of a pose's faces an island must reach to survive the cull
    # (0 disables the cull entirely).  Depth mode also seals the resulting holes.
    audit = bool(job.get("audit"))
    min_island = float(job.get("min_island", 0.01) or 0.0)
    fill_holes = bool(job.get("fill_holes", want_depth))
    # rest VERTEX positions keyed by vertex index (preserved 1:1 through the
    # armature deform, unlike edge order which the evaluated mesh reshuffles).
    rest_co = {}
    for m in meshes:
        rest_co[m.name] = [v.co.copy() for v in m.data.vertices]
    try:
        for m in meshes:
            _el = [e.calc_length() for e in m.data.edges] if hasattr(m.data, "edges") else []
            _el = [(m.data.vertices[e.vertices[0]].co
                    - m.data.vertices[e.vertices[1]].co).length for e in m.data.edges]
            if _el:
                _el.sort()
                _tiny = sum(1 for x in _el if x < 0.002)
                print(f"CLAY_LOG rest edges {m.name}: n={len(_el)} min={_el[0]:.6f} "
                      f"p50={_el[len(_el)//2]:.5f} under_0.002={_tiny}", flush=True)
    except Exception:  # noqa: BLE001
        pass

    def build_clean_temps():
        import bmesh
        dg = bpy.context.evaluated_depsgraph_get()
        temps = []
        dropped = 0
        maxratio = 0.0
        nedges_checked = 0
        for m in meshes:
            m.hide_render = True
            ev = m.evaluated_get(dg)
            me = ev.to_mesh()
            bm = bmesh.new()
            bm.from_mesh(me)
            bm.verts.ensure_lookup_table(); bm.edges.ensure_lookup_table()
            rco = rest_co.get(m.name) or []
            nrc = len(rco)
            drop = set()
            worst = []
            _bn_audit = {b.name for b in arm.data.bones}
            _dg_audit = {vg.index for vg in m.vertex_groups if vg.name in _bn_audit}
            for e in bm.edges:
                i0 = e.verts[0].index
                i1 = e.verts[1].index
                if i0 < nrc and i1 < nrc:
                    rl = (rco[i0] - rco[i1]).length
                    if rl > 1e-6:
                        nedges_checked += 1
                        ratio = (e.verts[0].co - e.verts[1].co).length / rl
                        if ratio > maxratio:
                            maxratio = ratio
                        if audit and ratio > 20.0:
                            worst.append((ratio, rl, i0, i1))
                        if ratio > STRETCH_TH:
                            for f in e.link_faces:
                                drop.add(f)
            if audit:
                # PENETRATION TEST (Lorenzo's hypothesis, 2026-07-27): the pose
                # library stores bone rotations authored against an AVERAGE-width
                # mannequin. On a much wider body an arm rotated to "hang at the
                # side" can end up geometrically INSIDE the torso -- occluded, so
                # it disappears from the depth silhouette. That is a completely
                # different failure from bad skinning (which re-skinning fixes)
                # and would survive it. Measured here, not assumed: how many
                # ARM-dominant vertices land inside the torso's own cross-section
                # at their own height. Local axes from the measured bbox:
                # Y = height (~200), X = width (~105), Z = depth (~78).
                try:
                    _names2 = {vg.index: vg.name for vg in m.vertex_groups}
                    def _grp(vi):
                        gs = [(g.weight, g.group) for g in m.data.vertices[vi].groups
                              if g.group in _dg_audit]
                        return _names2.get(max(gs)[1], "") if gs else ""
                    _armv, _torv = [], []
                    for _v in bm.verts:
                        if _v.index >= nrc:
                            continue
                        nm = _grp(_v.index)
                        # skip the deltoid cap: those verts sit inside the torso's
                        # own cross-section by anatomy, not by error, and they were
                        # inflating every arm reading.
                        if nm in SHOULDER_BONES:
                            continue
                        # bmesh coords here are MESH-LOCAL (the world bake happens
                        # later), so height is Y and lateral is X -- same frame the
                        # torso profile now uses. Grading penetration in world axes
                        # while correcting in armature axes would score the fix
                        # against a different geometry than the one it changed.
                        if nm in ARM_BONES:
                            _armv.append((_v.co[0], _v.co[1], _v.co[2]))
                        elif nm in TORSO_BONES:
                            _torv.append((_v.co[0], _v.co[1], _v.co[2]))
                    if not (_armv and _torv):
                        print(f"CLAY_PENETRATION skipped: arm_verts={len(_armv)} "
                              f"torso_verts={len(_torv)} deform_groups={len(_dg_audit)}",
                              flush=True)
                    if _armv and _torv:
                        import numpy as _np
                        A = _np.asarray(_armv); T = _np.asarray(_torv)
                        band = 3.0
                        inside = 0
                        # bucket the torso by height once, then test each arm vert
                        order = _np.argsort(T[:, 1]); T = T[order]
                        ys = T[:, 1]
                        for ax, ay, az in A:
                            lo = _np.searchsorted(ys, ay - band)
                            hi = _np.searchsorted(ys, ay + band)
                            if hi - lo < 8:
                                continue
                            sl = T[lo:hi]
                            if (sl[:, 0].min() < ax < sl[:, 0].max()
                                    and sl[:, 2].min() < az < sl[:, 2].max()):
                                inside += 1
                        pct = 100.0 * inside / max(len(A), 1)
                        print(f"CLAY_PENETRATION arm_verts={len(A)} inside_torso={inside} "
                              f"({pct:.1f}%)", flush=True)
                except Exception as _pe:  # noqa: BLE001
                    print(f"CLAY_PENETRATION failed ({_pe})", flush=True)
                # ABSOLUTE displacement in world units. Every ratio-based metric in
                # this file has misled us at least once; "how far did the vertex
                # actually move, and is it inside the body's own bbox" cannot.
                try:
                    _mn = [1e9, 1e9, 1e9]; _mx = [-1e9, -1e9, -1e9]
                    _maxd = 0.0; _maxi = -1
                    for _v in bm.verts:
                        for _k in range(3):
                            _mn[_k] = min(_mn[_k], _v.co[_k]); _mx[_k] = max(_mx[_k], _v.co[_k])
                        if _v.index < nrc:
                            _d = (_v.co - rco[_v.index]).length
                            if _d > _maxd:
                                _maxd = _d; _maxi = _v.index
                    _ext = max(_mx[_k] - _mn[_k] for _k in range(3))
                    print(f"CLAY_DISP max_vert_move={_maxd:.4f} at vert {_maxi} | "
                          f"posed bbox extent={_ext:.4f} | "
                          f"bbox=({_mn[0]:.2f},{_mn[1]:.2f},{_mn[2]:.2f})-"
                          f"({_mx[0]:.2f},{_mx[1]:.2f},{_mx[2]:.2f})", flush=True)
                except Exception as _de:  # noqa: BLE001
                    print(f"CLAY_DISP failed ({_de})", flush=True)
            if audit and worst:
                # Name the bones that own the most-stretched edges. If they are
                # arm-vs-torso pairs the problem is armpit weight bleed; if both
                # ends are the same bone it is something else entirely. Guessing
                # between those two has cost us three wrong diagnoses.
                worst.sort(reverse=True)
                _names = {vg.index: vg.name for vg in m.vertex_groups}
                def _dom(vi):
                    try:
                        gs = [(g.weight, g.group) for g in m.data.vertices[vi].groups
                              if g.group in _dg_audit]
                        return _names.get(max(gs)[1], "?") if gs else "UNWEIGHTED"
                    except Exception:  # noqa: BLE001
                        return "?"
                for _r, _rl, _i0, _i1 in worst[:6]:
                    print(f"CLAY_STRETCH ratio={_r:.0f} restlen={_rl:.5f} "
                          f"{_dom(_i0)} <-> {_dom(_i1)}", flush=True)
            if drop:
                dropped += len(drop)
                bmesh.ops.delete(bm, geom=list(drop), context="FACES")
            # v1.199.72: after cutting the smear webbing, keep ONLY the largest
            # connected island (the body) -- drops the little floating hand/limb
            # fragments that get detached and would read as extra limbs.
            bm.faces.ensure_lookup_table()
            if len(bm.faces) > 1 and min_island > 0.0:
                seen = set(); comps = []
                for f0 in bm.faces:
                    if f0 in seen:
                        continue
                    stack = [f0]; comp = []; seen.add(f0)
                    while stack:
                        cf = stack.pop(); comp.append(cf)
                        for e in cf.edges:
                            for nf in e.link_faces:
                                if nf not in seen:
                                    seen.add(nf); stack.append(nf)
                    comps.append(comp)
                if len(comps) > 1:
                    # v1.199.83: keep EVERY island above min_island (fraction of
                    # the pose's faces), not only the largest.  Keeping only the
                    # largest is what amputated the head and both arms whenever
                    # the smear cut isolated them from the torso -- the headless
                    # armless blob that broke the v1.199.82 skeleton attempt.
                    comps.sort(key=len, reverse=True)
                    _tot = sum(len(c) for c in comps)
                    _keep_n = max(1, int(_tot * min_island))
                    frag = [f for c in comps[1:] if len(c) < _keep_n for f in c]
                    if frag:
                        dropped += len(frag)
                        bmesh.ops.delete(bm, geom=frag, context="FACES")
            if fill_holes:
                # A depth map must not have holes punched through the body where
                # the smear webbing was cut -- the background value would read as
                # "infinitely far" mid-torso.  Cheap for the handful of small
                # boundaries the smear cut leaves.
                try:
                    bm.edges.ensure_lookup_table()
                    _bnd = [e for e in bm.edges if len(e.link_faces) == 1]
                    if _bnd:
                        bmesh.ops.holes_fill(bm, edges=_bnd, sides=0)
                except Exception:  # noqa: BLE001
                    pass
            bmesh.ops.transform(bm, matrix=ev.matrix_world, verts=bm.verts)
            nm = bpy.data.meshes.new(m.name + "_clean")
            bm.to_mesh(nm); bm.free()
            ev.to_mesh_clear()
            ob = bpy.data.objects.new(m.name + "_clean", nm)
            sc.collection.objects.link(ob)
            temps.append(ob)
        return temps, dropped, maxratio, nedges_checked

    def remove_temps(temps):
        for ob in temps:
            me = ob.data
            bpy.data.objects.remove(ob, do_unlink=True)
            try:
                bpy.data.meshes.remove(me)
            except Exception:  # noqa: BLE001
                pass
        for m in meshes:
            m.hide_render = False

    def bbox_of(objs):
        """Framing bounds from the 0.5/99.5 PERCENTILE, not absolute min/max.

        MEASURED: a single vertex can still be flung tens of units (max_vert_move
        37.7 on a 200-unit body even after re-skinning). With absolute min/max
        that one vertex inflates the bbox, the uniform ortho scale zooms out to
        contain it, and the character renders tiny -- observed as background
        going 66.5% -> 92.5% of frame. Percentile bounds make the framing immune
        to a handful of strays while being identical for a clean mesh.
        """
        pts = [v.co for ob in objs for v in ob.data.vertices]
        if not pts:
            return mathutils.Vector((0, 0, 0)), mathutils.Vector((0, 0, 0))
        mn = [0.0] * 3; mx = [0.0] * 3
        n = len(pts)
        lo_i = max(0, int(n * 0.005)); hi_i = min(n - 1, int(n * 0.995))
        for k in range(3):
            col = sorted(p[k] for p in pts)
            mn[k] = col[lo_i]; mx[k] = col[hi_i]
        return mathutils.Vector(mn), mathutils.Vector(mx)

    # v1.175.1: UNIFORM framing -- pass 1 measures the CLEANED mesh of every pose
    # so one ortho scale keeps the character the same size; pass 2 renders.
    cam_data.sensor_fit = "VERTICAL" if sc.render.resolution_y >= sc.render.resolution_x else "HORIZONTAL"
    boxes = []
    need_scale = 0.01
    for pose in poses:
        apply_pose(pose)
        if job.get("ik_clearance"):
            ik_clearance("f")
        temps, _d, _mr, _ne = build_clean_temps()
        mn, mx = bbox_of(temps)
        remove_temps(temps)
        boxes.append((mn, mx))
        ext_x = max(mx.x - mn.x, 0.01)
        ext_z = max(mx.z - mn.z, 0.01)
        if cam_data.sensor_fit == "VERTICAL":
            need_scale = max(need_scale, ext_z * 1.10, ext_x * 1.10 / max(aspect, 0.01))
        else:
            need_scale = max(need_scale, ext_x * 1.10, ext_z * 1.10 * aspect)
    cam_data.ortho_scale = need_scale
    print(f"CLAY_LOG uniform ortho_scale {need_scale:.3f} (smear_th={STRETCH_TH} "
          f"mode={render_mode} min_island={min_island} fill_holes={fill_holes})", flush=True)

    for pi, pose in enumerate(poses):
        apply_pose(pose)
        if job.get("ik_clearance"):
            ik_clearance("")
        temps, dropped, maxratio, nedges = build_clean_temps()
        mn, mx = boxes[pi]
        center = (mn + mx) / 2.0
        depth = max(mx.y - mn.y, 0.01)
        cam.location = (center.x, mn.y - depth * 2.0 - 1.0, center.z)
        cam_data.clip_end = depth * 8.0 + 20.0
        if depth_range is not None:
            # ORTHO camera looking +Y: the Z pass is the distance along view, so
            # the body spans exactly [cam->mn.y, cam->mx.y].  Using the real bbox
            # (rather than a percentile) makes the normalisation exact.
            depth_range.inputs["From Min"].default_value = depth * 2.0 + 1.0
            depth_range.inputs["From Max"].default_value = depth * 3.0 + 1.0
        out = os.path.join(out_dir, f"pose_{pi:03d}.png")
        sc.render.filepath = out
        bpy.ops.render.render(write_still=True)
        remove_temps(temps)
        print(f"CLAY_POSE {pi + 1}/{len(poses)} dropped={dropped} maxstretch={maxratio:.2f} edges={nedges}", flush=True)

    if want_depth:
        # Blender exits with 0xC0000005 if the compositor tree is still live when
        # the process tears down. Harmless (every PNG is already on disk and
        # CLAY_DONE is printed, which is what pose_clay checks) but it makes the
        # return code lie, so unhook it first.
        try:
            sc.use_nodes = False
        except Exception:  # noqa: BLE001
            pass
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
