"""RBMN SAM3D nodes — headless bridge for the RBMN Storyboard App.

Drop this folder into your ComfyUI `custom_nodes/` (next to the VNCCS suite).
It exposes two nodes, both reusing the SAM 3D Body model already on this worker:

  • RBMN SAM3D Proportions  — IMAGE -> JSON of the person's body proportions.
  • RBMN SAM3D Body Views   — IMAGE -> clean rendered views (front/side/back) of the
                              person's RECONSTRUCTED BODY with their detected shape.
                              This is the node's own clean "pose image", headless. Use
                              these as the Klein pose reference so the body matches.

Why this exists: the VNCCS Pose Studio auto-fit + render run in the browser viewer, so
they can't be driven headlessly by an app submitting a prompt. These nodes reproduce the
same result headlessly using the vendored `vnccs_sam3d` package (LoadSAM3DBodyModel +
SAM3DBodyProcessToJson + SAM3DBodyRenderFromPoseAndBodyPresetJson).
"""
import json
import os
import sys
import traceback

# --- SAM3D shape axes (first 9 shape components -> VNCCS body-preset axes) ---------
# From vnccs_sam3d: shape_params[i] = axis[i] * SHAPE_NORM[i] * SHAPE_SIGN[i]
_SHAPE_NORM = (1.00, 2.78, 4.42, 8.74, 10.82, 11.70, 13.39, 13.83, 16.62)
_SHAPE_SIGN = (+1, -1, -1, +1, -1, +1, -1, +1, +1)
_BODY_AXES = ("fat", "muscle", "fat_muscle", "limb_girth", "limb_muscle",
              "limb_fat", "chest_shoulder", "waist_hip", "thigh_calf")
# SAM3D joint indices (from vnccs_sam3d/pose_import.py known_joint_names)
_J = {"thigh_l": 2, "calf_l": 3, "foot_l": 4, "spine_01": 35,
      "upperarm_l": 75, "lowerarm_l": 76, "hand_l": 78, "neck_01": 110, "head": 113}
# camera yaw per view label (turntable around the subject)
_VIEW_YAW = {"front": 0.0, "right": 90.0, "left": -90.0, "back": 180.0,
             "front_right": 45.0, "front_left": -45.0, "back_right": 135.0, "back_left": -135.0}


def _locate_vnccs_sam3d():
    try:
        import vnccs_sam3d  # already importable
        return vnccs_sam3d
    except Exception:
        pass
    here = os.path.dirname(os.path.abspath(__file__))
    custom_nodes = os.path.dirname(here)
    for name in (os.listdir(custom_nodes) if os.path.isdir(custom_nodes) else []):
        d = os.path.join(custom_nodes, name)
        if os.path.isdir(d) and os.path.isdir(os.path.join(d, "vnccs_sam3d")):
            if d not in sys.path:
                sys.path.insert(0, d)
            try:
                import importlib
                return importlib.import_module("vnccs_sam3d")
            except Exception:
                continue
    raise ImportError("Could not locate the `vnccs_sam3d` package. Install the VNCCS suite "
                      "on this worker, or place this node folder in the same custom_nodes dir.")


def _extract_coords(mhr_out):
    import numpy as np
    for t in mhr_out[1:]:
        try:
            if t.ndim == 3 and t.shape[-1] == 3 and t.shape[-2] != 3 and t.shape[-2] <= 200:
                c = t.detach().cpu().numpy()
                return np.asarray(c[0] if c.ndim == 3 else c, dtype=float)
        except Exception:
            continue
    return None


def _compute_joints(model):
    """Return (personalized_rest_joints, canonical_rest_joints) via mhr_forward."""
    import torch
    from vnccs_sam3d.processing.process import _load_sam3d_model, _to_batched_tensor
    loaded = _load_sam3d_model(model)
    mhr = loaded["model"].head_pose
    dev = torch.device(loaded["device"])
    z3 = torch.zeros((1, 3), dtype=torch.float32, device=dev)
    rb = torch.zeros((1, 133), dtype=torch.float32, device=dev)
    rh = torch.zeros((1, 108), dtype=torch.float32, device=dev)
    ex = torch.zeros((1, mhr.num_face_comps), dtype=torch.float32, device=dev)
    zsh = torch.zeros((1, mhr.num_shape_comps), dtype=torch.float32, device=dev)
    zsc = torch.zeros((1, mhr.num_scale_comps), dtype=torch.float32, device=dev)
    return mhr, dev, z3, rb, rh, ex, zsh, zsc


def _build_body_preset(model, pose_json_str):
    """Detected shape (from pose_json) -> body_preset_json {body_params, bone_lengths}.
    body_params come from shape_params[0..8]; bone_lengths from personalized/canonical
    joint-length ratios (both from mhr_forward)."""
    import torch
    import numpy as np
    from vnccs_sam3d.processing.process import _to_batched_tensor
    try:
        data = json.loads(pose_json_str)
    except Exception:
        data = {}
    sp = data.get("shape_params") or []
    body_params = {}
    if isinstance(sp, list) and len(sp) >= 9:
        for i, k in enumerate(_BODY_AXES):
            try:
                body_params[k] = round(float(sp[i]) / (_SHAPE_NORM[i] * _SHAPE_SIGN[i]), 4)
            except Exception:
                body_params[k] = 0.0

    bone_lengths = {}
    try:
        mhr, dev, z3, rb, rh, ex, zsh, zsc = _compute_joints(model)
        dsh = _to_batched_tensor(data.get("shape_params"), dev, width=mhr.num_shape_comps)
        dsc = _to_batched_tensor(data.get("scale_params"), dev, width=mhr.num_scale_comps)
        if dsh is None:
            dsh = zsh
        if dsc is None:
            dsc = zsc

        def _joints(sh, sc):
            with torch.no_grad():
                out = mhr.mhr_forward(global_trans=z3, global_rot=z3, body_pose_params=rb,
                                      hand_pose_params=rh, scale_params=sc, shape_params=sh,
                                      expr_params=ex, return_joint_coords=True)
            return _extract_coords(out)

        P = _joints(dsh, dsc)
        C = _joints(zsh, zsc)
        if P is not None and C is not None:
            def d(A, a, b):
                return float(np.linalg.norm(A[_J[a]] - A[_J[b]]))

            def ratio(a, b):
                cc = d(C, a, b)
                return (d(P, a, b) / cc) if cc > 1e-6 else 1.0

            def clamp(v):
                return round(max(0.6, min(1.6, v)), 3)
            arm = (ratio("upperarm_l", "lowerarm_l") + ratio("lowerarm_l", "hand_l")) / 2.0
            leg = (ratio("thigh_l", "calf_l") + ratio("calf_l", "foot_l")) / 2.0
            bone_lengths = {"arm": clamp(arm), "leg": clamp(leg),
                            "torso": clamp(ratio("spine_01", "neck_01")),
                            "neck": clamp(ratio("neck_01", "head"))}
    except Exception as exc:  # noqa: BLE001
        print("[RBMN SAM3D Body Views] bone_lengths derivation skipped: %s" % exc)

    return json.dumps({"body_params": body_params, "bone_lengths": bone_lengths})


def _compute_proportions(image):
    """(unchanged) proportions JSON for the parametric-mannequin auto-fit path."""
    _locate_vnccs_sam3d()
    import torch
    from vnccs_sam3d.processing.load_model import LoadSAM3DBodyModel
    from vnccs_sam3d.processing.process import (
        SAM3DBodyProcessToJson, _load_sam3d_model, _to_batched_tensor)
    model = LoadSAM3DBodyModel().load_model("Auto")[0]
    pose_json = SAM3DBodyProcessToJson().process_to_json(
        model=model, image=image, bbox_threshold=0.8, inference_type="full")[0]
    pose_data = json.loads(pose_json)
    loaded = _load_sam3d_model(model)
    device = torch.device(loaded["device"])
    mhr = loaded["model"].head_pose
    z3 = torch.zeros((1, 3), dtype=torch.float32, device=device)
    rb = torch.zeros((1, 133), dtype=torch.float32, device=device)
    rh = torch.zeros((1, 108), dtype=torch.float32, device=device)
    ex = torch.zeros((1, mhr.num_face_comps), dtype=torch.float32, device=device)
    zsh = torch.zeros((1, mhr.num_shape_comps), dtype=torch.float32, device=device)
    zsc = torch.zeros((1, mhr.num_scale_comps), dtype=torch.float32, device=device)
    dsh = _to_batched_tensor(pose_data.get("shape_params"), device, width=mhr.num_shape_comps)
    if dsh is None:
        dsh = zsh
    dsc = _to_batched_tensor(pose_data.get("scale_params"), device, width=mhr.num_scale_comps)
    if dsc is None:
        dsc = zsc
    with torch.no_grad():
        pers = mhr.mhr_forward(global_trans=z3, global_rot=z3, body_pose_params=rb,
                               hand_pose_params=rh, scale_params=dsc, shape_params=dsh,
                               expr_params=ex, return_joint_coords=True)
        canon = mhr.mhr_forward(global_trans=z3, global_rot=z3, body_pose_params=rb,
                                hand_pose_params=rh, scale_params=zsc, shape_params=zsh,
                                expr_params=ex, return_joint_coords=True)
    pj = _extract_coords(pers)
    cj = _extract_coords(canon)
    known = {1: "pelvis", 2: "thigh_l", 3: "calf_l", 4: "foot_l", 18: "thigh_r",
             19: "calf_r", 20: "foot_r", 35: "spine_01", 36: "spine_02", 37: "spine_03",
             38: "clavicle_r", 39: "upperarm_r", 40: "lowerarm_r", 42: "hand_r",
             74: "clavicle_l", 75: "upperarm_l", 76: "lowerarm_l", 78: "hand_l",
             110: "neck_01", 113: "head"}
    n = pj.shape[0] if pj is not None else 0
    return {"ok": True, "joint_names": [known.get(i, "joint_%03d" % i) for i in range(n)],
            "rest_joint_coords": pj.tolist() if pj is not None else None,
            "canonical_joint_coords": cj.tolist() if cj is not None else None,
            "shape_params": pose_data.get("shape_params"),
            "scale_params": pose_data.get("scale_params"), "num_joints": n}


class RBMN_SAM3D_Proportions:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"image": ("IMAGE",)}}
    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("proportions_json",)
    FUNCTION = "run"
    CATEGORY = "RBMN"
    OUTPUT_NODE = True

    def run(self, image):
        try:
            img = image[:1] if hasattr(image, "__getitem__") else image
            result = _compute_proportions(img)
        except Exception as exc:  # noqa: BLE001
            result = {"ok": False, "error": str(exc), "trace": traceback.format_exc()[-1500:]}
        js = json.dumps(result, ensure_ascii=False, indent=2)
        try:
            import folder_paths
            out_dir = folder_paths.get_output_directory()
        except Exception:
            out_dir = os.getcwd()
        try:
            with open(os.path.join(out_dir, "rbmn_sam3d_proportions.json"), "w", encoding="utf-8") as fh:
                fh.write(js)
        except Exception:
            pass
        return {"ui": {"text": [js]}, "result": (js,)}


def _render_full_body_views(model, pose_json_str, labels, width, height):
    """Render the FULL SAM3D reconstruction (all 45 shape comps + scale, detected pose)
    from camera yaws -> list of RGB uint8 [H,W,3].  Bypasses the preset render node,
    which ZEROES shape[9:] + scale (a generic body); here the character's TRUE body is
    reproduced by feeding the full detected shape into mhr_forward + the software renderer."""
    import numpy as np
    import torch
    from vnccs_sam3d.processing.process import (
        _load_sam3d_model, _to_batched_tensor, _render_mesh_software)
    data = json.loads(pose_json_str) if isinstance(pose_json_str, str) else (pose_json_str or {})
    loaded = _load_sam3d_model(model)
    mhr = loaded["model"].head_pose
    dev = torch.device(loaded["device"])
    z3 = torch.zeros((1, 3), dtype=torch.float32, device=dev)
    ex = torch.zeros((1, mhr.num_face_comps), dtype=torch.float32, device=dev)

    def _bt(key, w, default_zeros):
        t = _to_batched_tensor(data.get(key), dev, width=w)
        return t if t is not None else default_zeros
    # REST/upright pose (zero body pose) for a clean, non-leaning base turnaround.
    # The character's detected posture (from the photo) makes him lean forward, which
    # is wrong for a base; SHAPE (fat/height/proportions) is independent of pose.
    bp = torch.zeros((1, 133), dtype=torch.float32, device=dev)
    hp = torch.zeros((1, 108), dtype=torch.float32, device=dev)
    sh = _bt("shape_params", mhr.num_shape_comps,
             torch.zeros((1, mhr.num_shape_comps), dtype=torch.float32, device=dev))
    sc = _bt("scale_params", mhr.num_scale_comps,
             torch.zeros((1, mhr.num_scale_comps), dtype=torch.float32, device=dev))
    with torch.no_grad():
        out = mhr.mhr_forward(global_trans=z3, global_rot=z3, body_pose_params=bp,
                              hand_pose_params=hp, scale_params=sc, shape_params=sh,
                              expr_params=ex, return_joint_coords=True)
    verts0 = out[0].detach().cpu().numpy()
    if verts0.ndim == 3:
        verts0 = verts0[0]
    verts0 = np.asarray(verts0, dtype=np.float32)
    faces = mhr.faces.detach().cpu().numpy().astype(np.int32)
    W, H = int(width), int(height)
    focal = max(W, H) * 1.2
    imgs = []
    for lab in labels:
        yaw = float(np.deg2rad(_VIEW_YAW.get(lab, 0.0)))
        v = verts0.copy()
        O = np.array([(v[:, 0].min() + v[:, 0].max()) * 0.5,
                      (v[:, 1].min() + v[:, 1].max()) * 0.5,
                      (v[:, 2].min() + v[:, 2].max()) * 0.5], dtype=np.float32)
        cy_, sy_ = np.cos(yaw), np.sin(yaw)
        R = np.array([[cy_, 0.0, -sy_], [0.0, 1.0, 0.0], [sy_, 0.0, cy_]], dtype=np.float32)
        v = (v - O) @ R.T + O
        mins = v.min(axis=0)
        maxs = v.max(axis=0)
        cx = float((mins[0] + maxs[0]) * 0.5)
        cyc = float((mins[1] + maxs[1]) * 0.5)
        cz = float((mins[2] + maxs[2]) * 0.5)
        w_ext = float(maxs[0] - mins[0])
        h_ext = float(maxs[1] - mins[1])
        MARGIN = 0.9
        cam_z = max(cz + h_ext * focal / (MARGIN * H), cz + w_ext * focal / (MARGIN * W), 0.5)
        camera = np.array([-cx, cyc, cam_z], dtype=np.float32)
        bg = np.zeros((H, W, 3), dtype=np.uint8)
        bgr = _render_mesh_software(v, faces, camera, focal, bg)
        imgs.append(np.ascontiguousarray(bgr[:, :, ::-1]))  # BGR -> RGB
    return imgs


def _fuse_body_json(front_json_str, side_json_str, side_weight):
    """Blend the FRONT reconstruction with a SIDE-PROFILE reconstruction.

    A single front image can only GUESS depth (how far the belly/back project) and
    can under-read true height, which is what leaves clones looking taller & thinner
    than the real person.  A side profile MEASURES that depth and height directly.
    We run SAM3D on both images and blend their shape_params + scale_params per
    component: fused = front + side_weight*(side - front).  side_weight in [0,1]
    (0 = front only, 1 = side only; 0.5 = split the difference).  Pose is irrelevant
    here (the render zeroes it), so only shape+scale are fused."""
    try:
        f = json.loads(front_json_str) if isinstance(front_json_str, str) else dict(front_json_str or {})
    except Exception:
        return front_json_str
    try:
        s = json.loads(side_json_str) if isinstance(side_json_str, str) else dict(side_json_str or {})
    except Exception:
        return f
    w = max(0.0, min(1.0, float(side_weight)))

    def _blend(a, b):
        if not isinstance(a, list) or not a:
            return b if isinstance(b, list) and b else a
        if not isinstance(b, list) or not b:
            return a
        n = max(len(a), len(b))
        out = []
        for i in range(n):
            av = float(a[i]) if i < len(a) and a[i] is not None else 0.0
            bv = float(b[i]) if i < len(b) and b[i] is not None else av
            out.append(av + w * (bv - av))
        return out

    fused = dict(f)
    fused["shape_params"] = _blend(f.get("shape_params"), s.get("shape_params"))
    fused["scale_params"] = _blend(f.get("scale_params"), s.get("scale_params"))
    return fused


class RBMN_SAM3D_BodyViews:
    """Reconstruct the body from ONE image and render clean views (front/side/back) of
    it WITH the FULL detected shape.  These are the Klein pose reference images."""

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "image": ("IMAGE",),
            "views": ("STRING", {"default": "front,right,left,back"}),
            "width": ("INT", {"default": 1024, "min": 256, "max": 4096}),
            "height": ("INT", {"default": 1216, "min": 256, "max": 4096}),
            "bbox_threshold": ("FLOAT", {"default": 0.8, "min": 0.0, "max": 1.0, "step": 0.05}),
        }, "optional": {
            # Optional SIDE-PROFILE reference: fuses real depth/height into the mesh so
            # the body stops coming out taller & thinner than the person.  Omit -> front only.
            "side_image": ("IMAGE",),
            "side_weight": ("FLOAT", {"default": 0.5, "min": 0.0, "max": 1.0, "step": 0.05}),
        }}
    RETURN_TYPES = ("IMAGE", "STRING")
    RETURN_NAMES = ("views", "body_preset_json")
    FUNCTION = "run"
    CATEGORY = "RBMN"
    OUTPUT_NODE = True

    def run(self, image, views="front,right,left,back", width=1024, height=1216,
            bbox_threshold=0.8, side_image=None, side_weight=0.5):
        try:
            _locate_vnccs_sam3d()
            import torch
            import numpy as np
            from PIL import Image
            from vnccs_sam3d.processing.load_model import LoadSAM3DBodyModel
            from vnccs_sam3d.processing.process import (
                SAM3DBodyProcessToJson, SAM3DBodyRenderFromPoseAndBodyPresetJson)

            model = LoadSAM3DBodyModel().load_model("Auto")[0]
            img = image[:1] if hasattr(image, "__getitem__") else image
            pose_json = SAM3DBodyProcessToJson().process_to_json(
                model=model, image=img, bbox_threshold=bbox_threshold, inference_type="full")[0]

            # Optional SIDE-PROFILE fusion: measure real depth/height and blend it in so
            # the reconstructed body matches the person instead of drifting taller & thinner.
            render_json = pose_json
            if side_image is not None:
                try:
                    simg = side_image[:1] if hasattr(side_image, "__getitem__") else side_image
                    side_json = SAM3DBodyProcessToJson().process_to_json(
                        model=model, image=simg, bbox_threshold=bbox_threshold, inference_type="full")[0]
                    render_json = _fuse_body_json(pose_json, side_json, side_weight)
                    print("[RBMN SAM3D Body Views] fused front+side shape (side_weight=%.2f)"
                          % float(side_weight))
                except Exception as exc:  # noqa: BLE001
                    print("[RBMN SAM3D Body Views] side fusion skipped (%s) -- front only" % exc)
                    render_json = pose_json
            preset = _build_body_preset(model, render_json)

            labels = [v.strip().lower() for v in str(views).split(",") if v.strip()]
            if not labels:
                labels = ["front"]
            try:
                import folder_paths
                out_dir = folder_paths.get_output_directory()
            except Exception:
                out_dir = os.getcwd()

            # FULL reconstruction (all 45 shape comps + scale) -> his ACTUAL body.
            rgb_views = _render_full_body_views(model, render_json, labels, int(width), int(height))
            tensors, saved = [], []
            for lab, rgb in zip(labels, rgb_views):
                tensors.append(torch.from_numpy(rgb.astype("float32") / 255.0).unsqueeze(0))
                try:
                    fn = "rbmn_sam3d_view_%s.png" % lab
                    Image.fromarray(rgb.astype("uint8")).save(os.path.join(out_dir, fn))
                    saved.append({"filename": fn, "subfolder": "", "type": "output"})
                except Exception as exc:  # noqa: BLE001
                    print("[RBMN SAM3D Body Views] save failed for %s: %s" % (lab, exc))

            batch = torch.cat(tensors, dim=0) if tensors else torch.zeros((1, int(height), int(width), 3))
            print("[RBMN SAM3D Body Views] rendered %d FULL-shape view(s)" % len(tensors))
            return {"ui": {"images": saved}, "result": (batch, preset)}
        except Exception as exc:  # noqa: BLE001
            print("[RBMN SAM3D Body Views] FAILED: %s\n%s" % (exc, traceback.format_exc()[-1500:]))
            import torch
            return {"ui": {"images": []},
                    "result": (torch.zeros((1, int(height), int(width), 3)),
                               json.dumps({"error": str(exc)}))}


NODE_CLASS_MAPPINGS = {
    "RBMN_SAM3D_Proportions": RBMN_SAM3D_Proportions,
    "RBMN_SAM3D_BodyViews": RBMN_SAM3D_BodyViews,
}
NODE_DISPLAY_NAME_MAPPINGS = {
    "RBMN_SAM3D_Proportions": "RBMN SAM3D Proportions",
    "RBMN_SAM3D_BodyViews": "RBMN SAM3D Body Views",
}
