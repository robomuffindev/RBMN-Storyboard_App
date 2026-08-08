"""Headless pre-render of VNCCS Pose Studio captures.

WHY THIS EXISTS: `VNCCS_PoseStudio.generate` has two render paths.  In the
ComfyUI panel the BROWSER renders the pose mannequins (three.js) and sends
them along as ``captured_images`` (CSR path).  Submitted headless — our case —
the node falls back to a Python software renderer, and that fallback has an
upstream bug: applying a non-zero ``modelRotation`` (all rear/side-view poses)
runs ``np.dot(posed, rot.T)`` where ``rot`` comes from ``matrix.rotz(...)``
which returns ``np.matrix``; the vertex array silently becomes a matrix, and
the screen projection then dies with
``could not broadcast input array from shape (19158,3) into shape (19158,)``.

Fix: WE act like the panel.  This module is a faithful port of the node's own
fallback renderer (same MakeHuman solve → FK skinning → flat-shaded PIL
render) with ``np.asarray`` guards on every matrix product, run in OUR backend
against the vendored ``vnccs-utils/CharacterData``.  ``assemble_step`` injects
the rendered PNGs into ``pose_data["captured_images"]`` so the node takes its
well-tested CSR path and the broken fallback never executes.

Degrades gracefully: if the vendored CharacterData tree is missing or anything
fails, ``render_pose_captures`` returns ``None`` and the graph is submitted
unchanged (i.e. current behaviour — which works for poses with zero rotation).
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import logging
import os
import sys
import threading
import types
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_LOCK = threading.Lock()
_STATE: Dict[str, Any] = {"loaded": False, "ok": False}
_CACHE: Dict[str, List[str]] = {}          # params-hash -> captures
_CACHE_MAX = 8

# Node-side CSR guardrails (vnccs-utils pose_studio.py) — stay well inside them.
_MAX_COUNT = 16
# v1.199.6: reserve headroom above the head (and a small margin below the feet) so
# HATS / headwear / tall hair have canvas to render into when Qwen dresses/poses the
# character — the mannequin capture defines the output canvas (image1), so framing
# the head at the very top edge left hats nowhere to go. Applied uniformly to every
# pose in a set so the character stays a constant size. Replaces the dress-time pad.
_TOP_HEADROOM = 0.14
_BOTTOM_MARGIN = 0.04
# body only: the node fallback also includes eye/teeth helper groups, but those
# sit INSIDE the head and a painter's-algorithm renderer has no depth buffer, so
# they z-fight through and scribble dark streaks across the face — which made the
# QIE pose transfer read the mannequin as facing AWAY. The three.js viewer hides
# them naturally with a real depth buffer.
_VALID_FACE_GROUPS = {"body"}


def _chardata_root() -> Optional[Path]:
    env = os.environ.get("RBMN_VNCCS_CHARDATA")
    if env:
        p = Path(env)
        return p if p.is_dir() else None
    repo = Path(__file__).resolve().parents[4]
    p = repo / "vnccs-utils" / "CharacterData"
    return p if p.is_dir() else None


def _ensure_loaded() -> bool:
    """Load MakeHuman base mesh / targets / skeleton once (≈6 s). Never raises."""
    if _STATE["loaded"]:
        return _STATE["ok"]
    with _LOCK:
        if _STATE["loaded"]:
            return _STATE["ok"]
        try:
            root = _chardata_root()
            if root is None:
                logger.info("vnccs pose_render: CharacterData not found — headless "
                            "pose pre-render disabled (worker fallback will be used)")
                return False
            parent = str(root.parent)
            if parent not in sys.path:
                sys.path.insert(0, parent)
            from CharacterData.obj_loader import load_obj           # noqa: PLC0415
            from CharacterData.mh_parser import TargetParser, HumanSolver  # noqa: PLC0415
            from CharacterData.mh_skeleton import Skeleton          # noqa: PLC0415
            from CharacterData import matrix as mh_matrix           # noqa: PLC0415

            mh = root / "makehuman"
            base_obj = mh / "makehuman" / "data" / "3dobjs" / "base.obj"
            if not base_obj.exists():
                base_obj = mh / "data" / "3dobjs" / "base.obj"
            base_mesh = load_obj(str(base_obj))
            parser = TargetParser(str(mh))
            targets = parser.scan_targets()
            skel_path = mh / "makehuman" / "data" / "rigs" / "game_engine.mhskel"
            if not skel_path.exists():
                skel_path = mh / "makehuman" / "data" / "rigs" / "default.mhskel"
            skeleton = Skeleton()
            skeleton.fromFile(str(skel_path), base_mesh)

            import numpy as np                                       # noqa: PLC0415
            faces = []
            if getattr(base_mesh, "face_groups", None):
                for i, group in enumerate(base_mesh.face_groups):
                    if group.strip() in _VALID_FACE_GROUPS:
                        faces.append(base_mesh.faces[i])

            _STATE.update({
                "ok": True, "np": np, "matrix": mh_matrix,
                "base_mesh": base_mesh, "targets": targets,
                "skeleton": skeleton, "solver": HumanSolver(), "faces": faces,
            })
            logger.info(f"vnccs pose_render: MakeHuman data loaded "
                        f"({len(targets)} targets, {len(faces)} faces)")
            return True
        except Exception as e:  # noqa: BLE001 — feature is best-effort
            logger.warning(f"vnccs pose_render: load failed ({e}) — pre-render disabled")
            return False
        finally:
            # v1.199.123: FIRST-USE RACE.  "loaded" used to be set BEFORE the ~6s
            # MakeHuman load, while the fast path above returns on "loaded" WITHOUT
            # taking the lock -- so any thread arriving during the load saw
            # loaded=True / ok=False and reported "pose renderer unavailable".
            # Measured 2026-07-28 16:51: a 4-way parallel mesh turnaround right
            # after a restart lost the LEFT and RIGHT views at 16:51:47, and
            # "MakeHuman data loaded" only printed at 16:51:48.9 -- the two views
            # that failed were simply the ones that asked while the loader ran.
            # Latent since the beginning; it only surfaced now because v122 moved
            # base sets back onto this renderer (normal mode had been using
            # pose_clay).  Setting the flag in `finally` makes late callers block
            # on _LOCK until the load truly finishes, while a FAILED load still
            # latches (no repeated 6s retries).
            _STATE["loaded"] = True


def _solve_base_verts(mesh: Dict[str, Any]):
    np = _STATE["np"]; solver = _STATE["solver"]
    age = float(mesh.get("age", 25.0))
    mh_age = max(0.0, min(1.0, (age - 1.0) / 89.0))
    factors = solver.calculate_factors(
        mh_age, mesh.get("gender", 0.5), mesh.get("weight", 0.5),
        mesh.get("muscle", 0.5), mesh.get("height", 0.5),
        mesh.get("breast_size", 0.5), mesh.get("firmness", 0.5),
        mesh.get("penis_len", mesh.get("genital_size", 0.5)),
        mesh.get("penis_circ", 0.5), mesh.get("penis_test", 0.5))
    return np.asarray(solver.solve_mesh(_STATE["base_mesh"], _STATE["targets"], factors))


def _euler_xyz_matrix(np, rx: float, ry: float, rz: float):
    """three.js Euler order 'XYZ' (bone.rotation.set): R = Rx(x) @ Ry(y) @ Rz(z)."""
    cx, sx = np.cos(rx), np.sin(rx)
    cy, sy = np.cos(ry), np.sin(ry)
    cz, sz = np.cos(rz), np.sin(rz)
    Rx = np.array([[1, 0, 0], [0, cx, -sx], [0, sx, cx]], dtype=np.float64)
    Ry = np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]], dtype=np.float64)
    Rz = np.array([[cz, -sz, 0], [sz, cz, 0], [0, 0, 1]], dtype=np.float64)
    return Rx @ Ry @ Rz


# --- Per-limb / spine bone-length scaling (image-fit proportions) -----------
# Ground truth: web/vnccs_pose_studio_core.js `_lengthSliderToScale` +
# `_boneLengthChildrenForGroup` + `updateBoneLengthScale`.  A 0..1 slider maps to a
# segment scale of clamp(0.5+v, 0.25, 2.0) (0.5 == neutral 1.0x), applied by scaling
# the CHILD bone's rest offset -- in our FK+LBS that is just scaling the bone's `rel`
# vector, so the existing skinning stretches the limb.  Our MakeHuman default.mhskel
# splits each segment into sub-bones, so a group scales EVERY sub-bone spanning the
# segment (their offsets sum to the segment vector).
# NOTE: our runtime skeleton is RETARGETED to a 52-bone MHR rig (thigh_l/calf_l/
# upperarm_l/lowerarm_l/hand_l/spine_0N ...), the SAME names the JS viewer uses, so
# this mirrors vnccs_pose_studio_core.js `_boneLengthChildrenForGroup` exactly:
# scale the CHILD bone's offset (the segment leading INTO it lengthens).
_LENGTH_GROUP_BONES: Dict[str, List[str]] = {
    "upper_arm_l": ["lowerarm_l"],
    "upper_arm_r": ["lowerarm_r"],
    "forearm_l":   ["hand_l"],
    "forearm_r":   ["hand_r"],
    "thigh_l":     ["calf_l"],
    "thigh_r":     ["calf_r"],
    "shin_l":      ["foot_l"],
    "shin_r":      ["foot_r"],
    "spine":       ["spine_02", "spine_03"],
}
_LENGTH_MESH_KEYS = {
    "upper_arm_l_length": "upper_arm_l", "upper_arm_r_length": "upper_arm_r",
    "forearm_l_length": "forearm_l", "forearm_r_length": "forearm_r",
    "thigh_l_length": "thigh_l", "thigh_r_length": "thigh_r",
    "shin_l_length": "shin_l", "shin_r_length": "shin_r",
    "spine_length": "spine",
}


def _length_slider_to_scale(v: float) -> float:
    """0..1 slider -> segment scale, matching the VNCCS viewer (0.5 == neutral 1.0x)."""
    try:
        val = float(v)
    except Exception:  # noqa: BLE001
        val = 0.5
    return max(0.25, min(2.0, 0.5 + val))


def _compute_bone_length_scales(mesh: Dict[str, Any]) -> Dict[str, float]:
    """Map the mesh proportion sliders to {bone_name: scale} for _apply_pose.  Only
    non-neutral sliders contribute; absent/0.5 -> no entry (scale stays 1.0)."""
    out: Dict[str, float] = {}
    if not isinstance(mesh, dict):
        return out
    for key, group in _LENGTH_MESH_KEYS.items():
        val = mesh.get(key)
        if val is None:
            continue
        try:
            fv = float(val)
        except Exception:  # noqa: BLE001
            continue
        if abs(fv - 0.5) < 1e-6:
            continue
        scale = _length_slider_to_scale(fv)
        for bone in _LENGTH_GROUP_BONES.get(group, ()):
            out[bone] = scale
    return out


# Isotropic mesh-size scales (girth/size, NOT length).  Ground truth: the viewer's
# updateHeadScale/updateArmScale/updateHandScale/updateFootScale set `bone.scale`
# directly from the slider (def 1.0, range 0.5..2.0).  In our 52-bone rig the whole
# head+face is skinned to `head`, so we scale each affected bone's weighted verts
# about that bone's POSED joint, blended by weight (no neck/wrist seam).
_SIZE_MESH_KEYS: Dict[str, List[str]] = {
    "head_size": ["head"],
    "arm_size":  ["upperarm_l", "upperarm_r"],
    "hand_size": ["hand_l", "hand_r"],
    "foot_size": ["foot_l", "foot_r"],
}


def _compute_size_scales(mesh: Dict[str, Any]) -> Dict[str, float]:
    """Map head/arm/hand/foot size sliders to {bone_name: scale}; 1.0 -> no entry."""
    out: Dict[str, float] = {}
    if not isinstance(mesh, dict):
        return out
    for key, bones in _SIZE_MESH_KEYS.items():
        val = mesh.get(key)
        if val is None:
            continue
        try:
            fv = float(val)
        except Exception:  # noqa: BLE001
            continue
        if abs(fv - 1.0) < 1e-6:
            continue
        fv = max(0.25, min(3.0, fv))
        for bone in bones:
            out[bone] = fv
    return out


def _apply_pose(base_verts, bones_data: Dict[str, Any], model_rotation,
                length_scales: Optional[Dict[str, float]] = None,
                size_scales: Optional[Dict[str, float]] = None,
                return_joints: bool = False) -> Any:
    """FK + LBS exactly like the VNCCS three.js viewer (PoseViewerCore.setPose).

    NOT the node's Python fallback math: that fallback is doubly wrong upstream —
    it composes Euler angles Rz*Ry*Rx (three.js uses XYZ order = Rx*Ry*Rz) and
    applies them in MakeHuman's BONE-ALIGNED rest frames, while the viewer builds
    its bones with translation-only, world-axis-aligned local frames
    (bone.position = head offset, rest rotation identity).  Using the fallback
    math collapses every pose to a near-A-pose.  Here:
      local(bone)  = T(head_rel) @ EulerXYZ(rot)
      world(bone)  = world(parent) @ local(bone)
      skin(bone)   = world(bone) @ T(-head_abs)         # bind = pure translation
    """
    np = _STATE["np"]
    deg2rad = np.pi / 180.0
    skel = _STATE["skeleton"].copy()
    skel.updateJointPositions(types.SimpleNamespace(vertices=base_verts))

    world: Dict[str, Any] = {}
    skin: Dict[str, Any] = {}
    for bone in skel.boneslist:            # breadth-first: parents before children
        head_abs = np.asarray(bone.headPos, dtype=np.float64)
        parent = getattr(bone, "parent", None)
        rel = head_abs - (np.asarray(parent.headPos, dtype=np.float64) if parent else 0.0)
        if length_scales:
            _ls = length_scales.get(bone.name)
            if _ls is not None:
                rel = rel * _ls
        L = np.identity(4, dtype=np.float64)
        L[:3, 3] = rel
        rot_deg = (bones_data or {}).get(bone.name)
        if isinstance(rot_deg, (list, tuple)) and len(rot_deg) >= 3:
            L[:3, :3] = _euler_xyz_matrix(
                np, rot_deg[0] * deg2rad, rot_deg[1] * deg2rad, rot_deg[2] * deg2rad)
        W = (world[parent.name] @ L) if (parent and parent.name in world) else L
        world[bone.name] = W
        inv_bind = np.identity(4, dtype=np.float64)
        inv_bind[:3, 3] = -head_abs
        skin[bone.name] = W @ inv_bind

    skinned = np.zeros_like(base_verts)
    verts4 = np.hstack([base_verts, np.ones((len(base_verts), 1), dtype=np.float32)])
    wsum = np.zeros(len(base_verts), dtype=np.float64)
    has_weights = False
    if skel.vertexWeights:
        has_weights = True
        for bname, (indices, weights) in skel.vertexWeights.data.items():
            mat = skin.get(bname)
            if mat is None or len(indices) == 0:
                continue
            w = np.asarray(weights)
            v = np.asarray(np.dot(verts4[indices], mat.T))
            skinned[indices] += (v[:, :3] * w[:, np.newaxis]).astype(skinned.dtype)
            wsum[np.asarray(indices)] += w
    if not has_weights:
        skinned = base_verts.copy()
    else:
        # MakeHuman's cleaned weight map leaves ~350 face vertices (eyelid/eye
        # area) with ZERO weight and ~3.5k with partial sums — zero-weight verts
        # collapse to the origin and drag long slivers across the face (which
        # also made the QIE pose transfer misread the head). Normalize partial
        # sums and bind orphan vertices to their nearest bone.
        nz = wsum > 1e-6
        norm = (nz & (np.abs(wsum - 1.0) > 1e-3))
        if norm.any():
            skinned[norm] = skinned[norm] / wsum[norm, np.newaxis]
        miss = ~nz
        if miss.any():
            heads = np.asarray([b.headPos for b in skel.boneslist], dtype=np.float64)
            names = [b.name for b in skel.boneslist]
            mv = base_verts[miss]
            d = ((mv[:, None, :] - heads[None, :, :]) ** 2).sum(-1)
            nearest = d.argmin(1)
            v4 = verts4[miss]
            out = np.empty((int(miss.sum()), 3), dtype=skinned.dtype)
            for bi in np.unique(nearest):
                mat = skin.get(names[int(bi)])
                rows = nearest == bi
                if mat is None:
                    out[rows] = mv[rows]
                else:
                    out[rows] = np.asarray(np.dot(v4[rows], mat.T))[:, :3]
            skinned[miss] = out

    # Isotropic size scaling (head/arm/hand/foot girth) about each bone's posed
    # joint, blended by that bone's skin weight so there's no seam at the neck/wrist.
    if size_scales:
        for _bn, _sc in size_scales.items():
            _wd = skel.vertexWeights.data.get(_bn) if skel.vertexWeights else None
            _Wb = world.get(_bn)
            if not _wd or _Wb is None:
                continue
            _idx = np.asarray(_wd[0])
            if len(_idx) == 0:
                continue
            _w = np.asarray(_wd[1], dtype=np.float64)
            _joint = _Wb[:3, 3]
            skinned[_idx] += ((_w * (float(_sc) - 1.0))[:, None]
                              * (skinned[_idx].astype(np.float64) - _joint)).astype(skinned.dtype)

    posed = skinned
    mr = model_rotation if isinstance(model_rotation, (list, tuple)) and len(model_rotation) >= 3 else [0, 0, 0]
    rx, ry, rz = (float(mr[0]), float(mr[1]), float(mr[2]))
    if abs(rx) > 0.01 or abs(ry) > 0.01 or abs(rz) > 0.01:
        # viewer: skinnedMesh.rotation.set(...) — same XYZ order, about the origin;
        # the projection recentres afterwards so the pivot choice washes out.
        rot = _euler_xyz_matrix(np, rx * deg2rad, ry * deg2rad, rz * deg2rad)
        posed = np.asarray(posed @ rot.T)
    if return_joints:
        # v1.199.138: posed joint positions, same modelRotation applied, so callers
        # can ask geometric questions about the POSED body (e.g. which way it faces)
        # instead of guessing from the pose blob.  `world[b][:3,3]` is the joint.
        _R = None
        if abs(rx) > 0.01 or abs(ry) > 0.01 or abs(rz) > 0.01:
            _R = _euler_xyz_matrix(np, rx * deg2rad, ry * deg2rad, rz * deg2rad)
        _j = {}
        for _bn, _W in world.items():
            _pt = np.asarray(_W[:3, 3], dtype=np.float64)
            _j[_bn] = np.asarray(_pt @ _R.T) if _R is not None else _pt
        return posed, _j
    return posed


def _body_vert_indices():
    """Vertex indices that belong to the visible 'body' face group. MakeHuman's
    vertex array also contains helper geometry (joint cubes etc.) that the
    skeleton derives joint positions from — those must NOT be displaced."""
    np = _STATE["np"]
    cached = _STATE.get("body_vert_idx")
    if cached is not None:
        return cached
    s: set = set()
    for face in _STATE["faces"]:
        for it in face:
            s.add(int(it[0]) if isinstance(it, (list, tuple)) else int(it))
    idx = np.asarray(sorted(s), dtype=np.int64)
    _STATE["body_vert_idx"] = idx
    return idx


# Directional-belly shape constants (model units; torso spans pelvis y≈-1.5 to
# spine_03 y≈+0.33, full body ≈13.6 tall). Tuned visually via tools/mannequin_test.
_BELLY_ANT = 1.30      # anterior (+Z) push at belly=1.0, at the gut's apex
_BELLY_SAG = 0.40      # downward (-Y) sag at belly=1.0 (weighted toward gut front)
_BELLY_LAT = 0.14      # lateral x widening fraction at belly=1.0 (love handles)
_BELLY_UP_BLEND = 0.70  # how far the bulge blends up toward the chest (0..1)


def _apply_width(base_verts, width: float):
    """v1.199.132: LATERAL width morph -- the axis the mannequin never had.

    MEASURED (device, no worker): MakeHuman's `weight` modifier is nearly useless
    as a width lever -- driving it 0.5 -> 1.0 moves the FRONT silhouette from
    0.149 to 0.161 width/height at the chest and 0.172 to 0.180 at the hips, ~5-8%.
    `belly` only displaces along the ANTERIOR axis, so it adds depth and shows up
    in the front view as a single bulge band (0.355 at y=0.42) with nothing either
    side of it. Against Duke's scan (0.256 / 0.290 / 0.268 at y=0.34 / 0.50 / 0.66)
    the maxed-out mannequin sits at 0.161 / 0.198 / 0.180 -- 63-68% -- which is why
    mesh_fit pinned BOTH its parameters at their ceilings (weight 1.00, belly 1.50)
    and still could not converge. No amount of the existing sliders fixes that: the
    parametric body has no width axis at all.

    This scales the body laterally about the mid-sagittal plane. Verts inside the
    torso envelope scale; verts beyond it (the arms) TRANSLATE by the boundary's
    displacement instead, so the torso and thighs get wider while arm length and
    arm girth stay exactly as they were -- critical in a T-pose, where scaling X
    globally would stretch the arms along their own axis. The head is faded out
    (head_size is its own slider and a widened skull is never wanted).

    width = 1.0 is an exact no-op, so every existing caller is unaffected.
    """
    np = _STATE["np"]
    if abs(float(width) - 1.0) < 1e-6:
        return base_verts
    v = np.array(base_verts, dtype=np.float64, copy=True)
    idx = _body_vert_indices()
    bv = v[idx]
    x, y = bv[:, 0], bv[:, 1]

    skel = _STATE["skeleton"].copy()
    skel.updateJointPositions(types.SimpleNamespace(vertices=base_verts))
    j = {b.name: np.asarray(b.headPos, dtype=np.float64) for b in skel.boneslist}
    # torso envelope: half-width at the shoulder joint; arms live beyond it
    x_lim = float(abs(j["upperarm_l"][0]))
    if not np.isfinite(x_lim) or x_lim <= 1e-6:
        x_lim = 1.6
    y_neck = float(j["neck_01"][1]) if "neck_01" in j else float(j["head"][1])
    y_top = y_neck + 0.9                     # fully faded out above this (skull)

    k = float(width) - 1.0
    # vertical falloff: full effect at/below the neck, smoothly to zero at the skull
    t = np.clip((y - y_neck) / max(y_top - y_neck, 1e-6), 0.0, 1.0)
    fade = 1.0 - (t * t * (3.0 - 2.0 * t))   # smoothstep
    inside = np.abs(x) <= x_lim
    dx = np.where(inside, x * k * fade, np.sign(x) * x_lim * k * fade)
    bv[:, 0] = x + dx
    v[idx] = bv
    return v


def _apply_depth(base_verts, depth: float):
    """v1.199.135: ANTERIOR-POSTERIOR depth morph -- the third missing axis.

    MEASURED 2026-07-29 against the turnaround images that FEED the T-pose re-pose
    (they carry the character's real build; the re-pose output does not).  Duke's
    own SIDE view reads 0.291 / 0.324 / 0.339 / 0.343 width/height at y =
    0.30 / 0.38 / 0.42 / 0.50; the production mannequin renders 0.252 / 0.153 /
    0.190 / 0.173 -- 59%.  Driving `belly` from 1.5 to 4.5 only reaches 80% AND
    puts it all in one low bulge (0.198 at y=0.38 vs a target of 0.324), because
    `belly` is a hanging gut, not torso depth: this body is deep through the CHEST
    as well.  So depth needs its own uniform axis, exactly as width did in v132.

    Scales the body along the anterior axis about the mid-coronal plane.  Arm verts
    are excluded by their own skin weight (a deeper torso must not thicken the
    arms), blended so there is no seam at the shoulder; the head is faded out with
    the same smoothstep `_apply_width` uses.

    depth = 1.0 is an exact no-op, so every existing caller is unaffected.
    """
    np = _STATE["np"]
    if abs(float(depth) - 1.0) < 1e-6:
        return base_verts
    v = np.array(base_verts, dtype=np.float64, copy=True)
    idx = _body_vert_indices()
    bv = v[idx]
    y, z = bv[:, 1], bv[:, 2]

    skel = _STATE["skeleton"].copy()
    skel.updateJointPositions(types.SimpleNamespace(vertices=base_verts))
    j = {b.name: np.asarray(b.headPos, dtype=np.float64) for b in skel.boneslist}
    y_neck = float(j["neck_01"][1]) if "neck_01" in j else float(j["head"][1])
    y_top = y_neck + 0.9

    # arm weight per vertex, so the scale fades out along the arm chain
    armw = np.zeros(len(base_verts), dtype=np.float64)
    try:
        vw = skel.vertexWeights
        for bname, (indices, weights) in (vw.data.items() if vw else ()):
            bn = str(bname).lower()
            if bn.startswith(("upperarm", "lowerarm", "hand")) or "clavicle" in bn:
                armw[np.asarray(indices)] += np.asarray(weights, dtype=np.float64)
    except Exception:  # noqa: BLE001
        pass
    armw = np.clip(armw[idx], 0.0, 1.0)

    t = np.clip((y - y_neck) / max(y_top - y_neck, 1e-6), 0.0, 1.0)
    fade = 1.0 - (t * t * (3.0 - 2.0 * t))               # smoothstep, head -> 0
    k = (float(depth) - 1.0) * fade * (1.0 - armw)
    bv[:, 2] = z * (1.0 + k)
    v[idx] = bv
    return v


def _apply_belly(base_verts, belly: float):
    """Displace lower-torso front verts into a natural forward-hanging gut.

    Anterior axis at rest is +Z (mannequin faces the camera at modelRotation 0).
    All falloffs are smooth (cosine / smoothstep) so the gut blends into chest,
    flanks and groin with no crease. Only 'body' face-group verts move — helper
    joint-cube verts (which define joint positions) are untouched, so the
    skeleton, arms and legs are exactly as before.
    """
    np = _STATE["np"]
    v = np.array(base_verts, dtype=np.float64, copy=True)
    idx = _body_vert_indices()
    bv = v[idx]
    x, y, z = bv[:, 0], bv[:, 1], bv[:, 2]

    # --- landmarks from the rest skeleton (proportional -> survives morphs) ---
    skel = _STATE["skeleton"].copy()
    skel.updateJointPositions(types.SimpleNamespace(vertices=base_verts))
    j = {b.name: np.asarray(b.headPos, dtype=np.float64) for b in skel.boneslist}
    y_pelvis = float(j["pelvis"][1])          # ≈ -1.52
    y_top = float(j["spine_03"][1])           # sternum-ish, gut fades out here
    y_bot = y_pelvis - 1.15                   # fades into the groin
    y_peak = float(j["spine_01"][1]) - 0.15   # navel level (gut apex)
    z_back = float(j["spine_01"][2])          # spine plane: back verts don't move
    z_front = z.max()                          # current torso front
    x_half = 1.9                               # torso half-width-ish for roundness

    # --- vertical falloff: asymmetric cosine bump peaked at y_peak -----------
    wy = np.zeros_like(y)
    up = (y >= y_peak) & (y <= y_top)
    dn = (y < y_peak) & (y >= y_bot)
    wy[up] = 0.5 + 0.5 * np.cos(np.pi * (y[up] - y_peak) / max(y_top - y_peak, 1e-6))
    wy[dn] = 0.5 + 0.5 * np.cos(np.pi * (y_peak - y[dn]) / max(y_peak - y_bot, 1e-6))
    # let a fraction of the bulge blend up the chest so it's a belly, not a shelf
    wy[up] *= _BELLY_UP_BLEND + (1.0 - _BELLY_UP_BLEND) * (
        0.5 + 0.5 * np.cos(np.pi * (y[up] - y_peak) / max(y_top - y_peak, 1e-6)))

    # --- frontness: 0 at the spine plane, 1 at the current front -------------
    fz = np.clip((z - z_back) / max(z_front - z_back, 1e-6), 0.0, 1.0) ** 0.75

    # --- lateral roundness: full push at centre, fading to the flanks --------
    wx = np.clip(1.0 - (np.abs(x) / x_half) ** 2, 0.0, 1.0)

    w = wy * fz * wx
    # anterior push (+Z) — the gut
    bv[:, 2] += belly * _BELLY_ANT * w
    # downward sag (-Y), strongest on the front face of the gut
    bv[:, 1] -= belly * _BELLY_SAG * w * fz
    # slight lateral widening (love handles) — about the midline, side-weighted
    bv[:, 0] *= 1.0 + belly * _BELLY_LAT * wy * (1.0 - 0.5 * fz)

    v[idx] = bv
    return v.astype(base_verts.dtype, copy=False)


# --------------------------------------------------------------------------
# NODE-FAITHFUL capture camera + z-buffer rasterizer (v1.199.79)
#
# The VNCCS Pose Studio panel renders with a FIXED PerspectiveCamera (fov 30,
# distance 45, aimed at the mesh bbox center, cam_zoom scaling the frustum,
# cam_offset panning, cam_yaw/cam_pitch orbiting) through a real WebGL depth
# buffer.  The pose LoRA was trained on THOSE captures.  Our old headless
# stand-in was orthographic + fit-to-frame + painter's-algorithm, which (a)
# cancelled height/size slider effects back out (short characters rendered as
# tall as everyone else) and (b) garbled interpenetrating limbs (Klein copied
# the garble as extra/missing limbs).  This path replicates the node's camera
# math 1:1 and rasterizes with a per-pixel z-buffer, so a short mannequin is
# genuinely short on screen and overlapping limbs occlude correctly.
# --------------------------------------------------------------------------
_NODE_FOV_DEG = 30.0
_NODE_DIST = 45.0


def _node_camera(np, center, offset_x: float, offset_y: float,
                 yaw_deg: float, pitch_deg: float):
    """Camera position + orthonormal basis, exactly like updateCaptureCamera:
    target = meshCenter - offset; camera = target + Euler(pitch,yaw,'YXZ')*(0,0,45)."""
    target = np.asarray([center[0] - float(offset_x or 0.0),
                         center[1] - float(offset_y or 0.0),
                         center[2]], dtype=np.float64)
    yaw = np.deg2rad(float(yaw_deg or 0.0))
    pitch = np.deg2rad(float(pitch_deg or 0.0))
    off = np.asarray([0.0, 0.0, _NODE_DIST], dtype=np.float64)
    # three.js Euler 'YXZ': v' = Ry(yaw) @ Rx(pitch) @ v
    cx, sx = np.cos(pitch), np.sin(pitch)
    cy, sy = np.cos(yaw), np.sin(yaw)
    Rx = np.array([[1, 0, 0], [0, cx, -sx], [0, sx, cx]])
    Ry = np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]])
    campos = target + (Ry @ Rx @ off)
    fwd = target - campos
    fwd = fwd / np.linalg.norm(fwd)
    upw = np.asarray([0.0, 1.0, 0.0])
    right = np.cross(fwd, upw)
    rn = np.linalg.norm(right)
    if rn < 1e-9:
        right = np.asarray([1.0, 0.0, 0.0]); rn = 1.0
    right = right / rn
    up = np.cross(right, fwd)
    return campos, right, up, fwd


def _node_project(np, verts, width: int, height: int, zoom: float,
                  campos, right, up, fwd):
    """Perspective-project world verts -> (px, py, depth). three.js vertical-fov
    convention: zoom divides the frustum (multiplies screen scale)."""
    rel = verts - campos
    d = rel @ fwd                       # distance along view dir (front > 0)
    d = np.maximum(d, 1e-6)
    tanv = np.tan(np.deg2rad(_NODE_FOV_DEG) / 2.0)
    aspect = float(width) / float(height)
    sx = (rel @ right) / (d * tanv * aspect) * float(zoom)
    sy = (rel @ up) / (d * tanv) * float(zoom)
    px = (sx + 1.0) * 0.5 * width
    py = (1.0 - sy) * 0.5 * height
    return px, py, d


def _render_pose_node(posed, width: int, height: int, bg_color, lights,
                      zoom: float, offset_x: float, offset_y: float,
                      yaw_deg: float, pitch_deg: float, center,
                      depth: bool = False):
    """Fixed-camera z-buffer render of one posed mesh (node-faithful).

    ``depth=True`` (v1.199.83) returns a DEPTH MAP instead of the clay shading:
    the z-buffer this renderer already computes, normalised per image to the
    DepthAnythingV2 convention the RefControl depth LoRA was trained on --
    NEAR = white (255), FAR = dark, background = pure black (0).  A depth map
    carries pose AND VOLUME AND HEIGHT in one signal, which a DWPose skeleton
    fundamentally cannot; this is what lets a heavy/short character survive the
    trip through Klein instead of being redrawn on the LoRA's tall-lean prior."""
    np = _STATE["np"]
    from PIL import Image  # noqa: PLC0415
    W, H = int(width), int(height)
    campos, right, up, fwd = _node_camera(np, center, offset_x, offset_y,
                                          yaw_deg, pitch_deg)
    posed = np.asarray(posed, dtype=np.float64)
    px, py, dep = _node_project(np, posed, W, H, zoom, campos, right, up, fwd)

    # same light aggregation as the classic renderer
    main_light_dir = np.array([0.5, 0.8, 1.0]); main_light_int = 0.7; ambient_int = 0.3
    for l in lights or []:
        lt = l.get("type", "ambient")
        if lt == "ambient":
            ambient_int = max(0.2, min(0.6, l.get("intensity", 1.0) * 0.4))
        elif lt in ("directional", "point"):
            dvec = np.array([l.get("x", 0), l.get("y", 10), l.get("z", 10)], dtype=float)
            mag = np.linalg.norm(dvec)
            if mag > 0.001:
                main_light_dir = dvec / mag
            main_light_int = min(1.2, l.get("intensity", 1.0) * 0.8)
            break
    mld = main_light_dir / max(np.linalg.norm(main_light_dir), 1e-9)
    # v1.199.81: camera-following FILL light. The single fixed model-space key
    # light left the far side of yawed/rotated poses pitch black -- big dark
    # blobs that read as a second body and that Klein reproduced. Key stays
    # world-fixed; the fill tied to the view direction guarantees every
    # camera-facing surface is readably lit (matches the WebGL viewer's look).
    view_dir = -fwd
    base_color = np.array([212, 165, 116], dtype=np.float64)

    # triangulate (quads -> 2 tris), keeping vertex indices
    tris = []
    for face in _STATE["faces"]:
        vi = [it[0] if isinstance(it, (list, tuple)) else it for it in face]
        if len(vi) < 3 or any(v >= len(posed) for v in vi):
            continue
        tris.append((vi[0], vi[1], vi[2]))
        if len(vi) >= 4:
            tris.append((vi[0], vi[2], vi[3]))
    tri = np.asarray(tris, dtype=np.int64)

    # world-space normals + backface cull against the actual view direction
    p0, p1, p2 = posed[tri[:, 0]], posed[tri[:, 1]], posed[tri[:, 2]]
    n = np.cross(p1 - p0, p2 - p0)
    nl = np.linalg.norm(n, axis=1)
    ok = nl > 1e-12
    n[ok] = n[ok] / nl[ok, None]
    fc = (p0 + p1 + p2) / 3.0
    frontfacing = ((fc - campos) * n).sum(1) < 0.0   # normal toward camera
    keep = ok & frontfacing
    tri = tri[keep]; n = n[keep]

    inten = np.clip(ambient_int + np.maximum(0.0, n @ mld) * main_light_int
                    + np.maximum(0.0, n @ view_dir) * 0.35, 0.0, 1.0)
    cols = np.clip(base_color[None, :] * inten[:, None], 0, 255).astype(np.uint8)

    zbuf = np.full((H, W), np.inf, dtype=np.float64)
    img = np.empty((H, W, 3), dtype=np.uint8)
    img[:, :] = np.asarray(bg_color, dtype=np.uint8)

    tx = px[tri]; ty = py[tri]; tz = dep[tri]
    xmin = np.clip(np.floor(tx.min(1)).astype(np.int64), 0, W - 1)
    xmax = np.clip(np.ceil(tx.max(1)).astype(np.int64), 0, W - 1)
    ymin = np.clip(np.floor(ty.min(1)).astype(np.int64), 0, H - 1)
    ymax = np.clip(np.ceil(ty.max(1)).astype(np.int64), 0, H - 1)
    onscreen = (tx.max(1) >= 0) & (tx.min(1) < W) & (ty.max(1) >= 0) & (ty.min(1) < H)

    for i in np.nonzero(onscreen)[0]:
        x0, x1 = xmin[i], xmax[i]
        y0, y1 = ymin[i], ymax[i]
        if x1 < x0 or y1 < y0:
            continue
        ax, ay = tx[i, 0], ty[i, 0]
        bx, by = tx[i, 1], ty[i, 1]
        cx2, cy2 = tx[i, 2], ty[i, 2]
        det = (by - cy2) * (ax - cx2) + (cx2 - bx) * (ay - cy2)
        if abs(det) < 1e-9:
            continue
        gx = np.arange(x0, x1 + 1, dtype=np.float64) + 0.5
        gy = (np.arange(y0, y1 + 1, dtype=np.float64) + 0.5)[:, None]
        w0 = ((by - cy2) * (gx - cx2) + (cx2 - bx) * (gy - cy2)) / det
        w1 = ((cy2 - ay) * (gx - cx2) + (ax - cx2) * (gy - cy2)) / det
        w2 = 1.0 - w0 - w1
        inside = (w0 >= 0) & (w1 >= 0) & (w2 >= 0)
        if not inside.any():
            continue
        z = w0 * tz[i, 0] + w1 * tz[i, 1] + w2 * tz[i, 2]
        sub = zbuf[y0:y1 + 1, x0:x1 + 1]
        upd = inside & (z < sub)
        if not upd.any():
            continue
        sub[upd] = z[upd]
        img[y0:y1 + 1, x0:x1 + 1][upd] = cols[i]
    if depth:
        # Per-image normalisation over the COVERED pixels only (matches how
        # DepthAnythingV2 emits relative inverse depth).  The body's farthest
        # pixel is pinned to 1 rather than 0 so that pure black stays a
        # background-only value and the silhouette edge is unambiguous.
        finite = np.isfinite(zbuf)
        dimg = np.zeros((H, W), dtype=np.uint8)
        if finite.any():
            zv = zbuf[finite]
            # Robust range: a handful of outlier pixels (nostril interior, a limb
            # tucked far behind the torso) otherwise stretch the span and flatten
            # the whole body to a near-uniform grey.  1st/99th percentile then
            # clamp keeps the readable contrast DepthAnythingV2 produces.
            znear = float(np.percentile(zv, 1.0))
            zfar = float(np.percentile(zv, 99.0))
            if zfar - znear < 1e-9:
                dimg[finite] = 255
            else:
                rel = np.clip((zfar - zbuf[finite]) / (zfar - znear), 0.0, 1.0)
                dimg[finite] = np.clip(1.0 + rel * 254.0, 1, 255).astype(np.uint8)
        return Image.fromarray(np.repeat(dimg[:, :, None], 3, axis=2), "RGB")
    return Image.fromarray(img, "RGB")


def _render_pose(posed, width: int, height: int, bg_color, lights, cam_zoom: float,
                 silhouette: bool = False, fixed_scale: Optional[float] = None,
                 headroom: float = 0.0, bottom_align: bool = False):
    """Port of _render_mesh/_render_flat_shaded (+ optional cam_zoom honor).

    ``silhouette=True`` renders solid-black-on-background figures — the look of
    the VNCCS Pose Manager thumbnails in the node UI.  ``fixed_scale`` (set by
    ``render_pose_captures``) applies ONE world->pixel scale to every pose in the
    set so the character renders at a CONSTANT size (VNCCS-style uniform framing);
    without it each pose auto-fits, which drifts the character's height."""
    np = _STATE["np"]
    from PIL import Image, ImageDraw  # noqa: PLC0415

    W, H = int(width), int(height)
    img = Image.new("RGB", (W, H), tuple(bg_color))
    draw = ImageDraw.Draw(img)

    # centre on the figure's BBOX centre (stable across poses + rotation) rather
    # than the vertex mean (which drifts when limbs move / mass shifts).
    _mn = posed[:, :2].min(axis=0)
    _mx = posed[:, :2].max(axis=0)
    cx, cy = float((_mn[0] + _mx[0]) / 2.0), float((_mn[1] + _mx[1]) / 2.0)
    if fixed_scale is not None and fixed_scale > 0:
        scale = float(fixed_scale)
    else:
        # legacy per-pose auto-fit (fallback if no set-wide scale was provided)
        ext_x = max(float(np.abs(posed[:, 0] - cx).max()), 0.001)
        ext_y = max(float(np.abs(posed[:, 1] - cy).max()), 0.001)
        scale = min(W * 0.46 / ext_x, H * 0.46 / ext_y)
        try:
            z = float(cam_zoom)
            if 0.2 <= z <= 4.0:
                scale *= z
        except Exception:  # noqa: BLE001
            pass
        scale = min(scale, W * 0.48 / ext_x, H * 0.48 / ext_y)
    vs = np.zeros((len(posed), 2))
    vs[:, 0] = (posed[:, 0] - cx) * scale + W / 2
    vs[:, 1] = H / 2 - (posed[:, 1] - cy) * scale

    # v1.199.6: bias the figure DOWN so its head sits `headroom` below the top edge
    # (space for hats), unless that would push the feet past the bottom — then
    # bottom-align. Uniform scale keeps size constant; this only moves it vertically.
    if bottom_align:
        # v1.199.76 height-anchored framing: feet on a shared floor line; a short
        # character's head simply sits lower (real height differences survive).
        bot_y = float(vs[:, 1].max())
        vs[:, 1] = vs[:, 1] + (H * 0.98 - bot_y)
    elif headroom and headroom > 0:
        top_y = float(vs[:, 1].min()); bot_y = float(vs[:, 1].max())
        shift = (headroom * H) - top_y
        max_bot = H * 0.98
        if bot_y + shift > max_bot:
            shift = max_bot - bot_y
        vs[:, 1] = vs[:, 1] + shift

    # Lighting (same aggregation as the node)
    main_light_dir = np.array([0.5, 0.8, 1.0]); main_light_int = 0.7; ambient_int = 0.3
    for l in lights or []:
        lt = l.get("type", "ambient")
        if lt == "ambient":
            ambient_int = max(0.2, min(0.6, l.get("intensity", 1.0) * 0.4))
        elif lt in ("directional", "point"):
            d = np.array([l.get("x", 0), l.get("y", 10), l.get("z", 10)], dtype=float)
            mag = np.linalg.norm(d)
            if mag > 0.001:
                main_light_dir = d / mag
            main_light_int = min(1.2, l.get("intensity", 1.0) * 0.8)
            break
    base_color = np.array([212, 165, 116])

    if silhouette:
        # flat black fill — no lighting, no depth sorting needed
        for face in _STATE["faces"]:
            if len(face) < 3:
                continue
            vi = [it[0] if isinstance(it, (list, tuple)) else it for it in face]
            if any(v >= len(posed) for v in vi):
                continue
            points = [(vs[v][0], vs[v][1]) for v in vi[:4]]
            if len(points) >= 3:
                draw.polygon(points, fill=(12, 12, 14))
        return img

    face_data = []
    for face in _STATE["faces"]:
        if len(face) < 3:
            continue
        vi = [it[0] if isinstance(it, (list, tuple)) else it for it in face]
        if any(v >= len(posed) for v in vi):
            continue
        z_avg = np.mean([posed[v][2] for v in vi[:3]])
        p0, p1, p2 = posed[vi[0]], posed[vi[1]], posed[vi[2]]
        normal = np.cross(p1 - p0, p2 - p0)
        nl = np.linalg.norm(normal)
        if nl < 1e-8:
            continue
        normal = normal / nl
        if normal[2] <= 0.0:
            continue  # backface cull — viewer at +Z; kills see-through artifacts
        intensity = min(1.0, ambient_int + max(0.0, float(np.dot(normal, main_light_dir))) * main_light_int)
        color = tuple(int(c) for c in np.clip((base_color * intensity).astype(int), 0, 255))
        face_data.append((z_avg, vi, color))
    face_data.sort(key=lambda x: x[0])
    for _, vi, color in face_data:
        points = [(vs[v][0], vs[v][1]) for v in vi[:4]]
        if len(points) >= 3:
            draw.polygon(points, fill=color)
    return img


def body_facing_deg(pose_data: Dict[str, Any]) -> Optional[float]:
    """Which way the POSED body faces, in degrees.  0 = chest toward the camera,
    +90 / -90 = the two profiles, 180 = back.  None if the renderer cannot load.

    v1.199.138.  Written because two earlier answers to this question were wrong:
      * `modelRotation` is NOT the body's turn for library poses -- clay_driver
        reads it as DEGREES and the library stores ~-0.9 there, i.e. no turn at
        all.  The turn lives in the SPINE/HIP bone rotations.
      * averaging the normal map's nx/nz over the torso reads ~0 deg on a body
        that is visibly twisted, because a big convex belly averages toward the
        camera whatever the yaw.  Discarded.
    So take it from the skeleton: the shoulder axis of the POSED figure, which is
    exact, unit-free and independent of how the pose was authored.
    """
    if not _ensure_loaded():
        return None
    np = _STATE["np"]
    try:
        poses = (pose_data or {}).get("poses") or [{}]
        pose = poses[0] or {}
        mesh = (pose_data or {}).get("mesh", {}) or {}
        base_verts = _solve_base_verts(mesh)
        _, joints = _apply_pose(base_verts, pose.get("bones") or {},
                                pose.get("modelRotation"),
                                _compute_bone_length_scales(mesh),
                                None, return_joints=True)
        l, r = joints.get("upperarm_l"), joints.get("upperarm_r")
        if l is None or r is None:
            return None
        ax = np.asarray(r, float) - np.asarray(l, float)   # left shoulder -> right
        ax[1] = 0.0                                        # project to the ground plane
        n = float(np.linalg.norm(ax))
        if n < 1e-6:
            return None
        ax = ax / n
        # forward = shoulder-axis x up.  CALIBRATED, not assumed: the first sign
        # I tried read -180 on the rest pose and -90 on modelRotation [0,90,0].
        # This one reads 0 / +90 / -90 / 180 for rest / right / left / back.
        fx, fz = ax[2], -ax[0]
        return float(np.degrees(np.arctan2(fx, fz)))
    except Exception:  # noqa: BLE001
        return None


def render_pose_captures(pose_data: Dict[str, Any],
                         silhouette: bool = False) -> Optional[List[str]]:
    """Render every pose in a VNCCS pose_data blob to base64 PNGs. Never raises."""
    try:
        if not isinstance(pose_data, dict):
            return None
        poses = pose_data.get("poses") or [{}]
        if not poses or len(poses) > _MAX_COUNT:
            return None
        if not _ensure_loaded():
            return None

        export = pose_data.get("export", {}) or {}
        width = int(export.get("view_width", export.get("view_size", 512)))
        height = int(export.get("view_height", export.get("view_size", 512)))
        bg = export.get("bg_color", [40, 40, 40])
        bg = [int(c) for c in bg[:3]] if isinstance(bg, (list, tuple)) else [40, 40, 40]
        cam_zoom = export.get("cam_zoom", 1.0)
        # v1.199.13: per-costume headroom override ("Headwear room" slider) so tall
        # hats / headdresses have canvas above the head. Falls back to the default.
        try:
            hr = float(export.get("top_headroom", _TOP_HEADROOM))
        except Exception:  # noqa: BLE001
            hr = _TOP_HEADROOM
        hr = max(0.0, min(0.45, hr))
        lights = pose_data.get("lights", [])
        mesh = pose_data.get("mesh", {}) or {}

        ha = bool(export.get("height_anchor"))
        nc = bool(export.get("node_camera"))
        # v1.199.83: "depth" renders a true depth map from the SAME node-faithful
        # camera (implies node_camera -- a depth map has no meaning without a
        # fixed perspective projection).
        rmode = str(export.get("render_mode") or "shaded").strip().lower()
        if rmode == "depth":
            nc = True
        _cam = {k: export.get(k, 0) for k in
                ("cam_offset_x", "cam_offset_y", "cam_yaw_deg", "cam_pitch_deg")}
        key = hashlib.sha256(json.dumps(
            {"m": mesh, "p": poses, "w": width, "h": height, "bg": bg,
             "z": cam_zoom, "l": lights, "sil": silhouette, "hr": hr, "ha": ha,
             "nc": nc, "cam": _cam, "rm": rmode},
            sort_keys=True, default=str,
        ).encode()).hexdigest()
        hit = _CACHE.get(key)
        if hit:
            return list(hit)

        base_verts = _solve_base_verts(mesh)
        _len_scales = _compute_bone_length_scales(mesh)
        _size_scales = _compute_size_scales(mesh)
        # PROCEDURAL BELLY (directional, v1.199.73): MakeHuman's weight morph tops
        # out at mildly chubby, so for very heavy characters we displace the
        # lower-torso BASE verts along the ANTERIOR axis (+Z at rest — verified by
        # toe direction, torso z-skew, and the renderer's viewer-at-+Z cull), with
        # slight downward sag and a small lateral widening, so it reads as a
        # hanging gut instead of the flying-saucer disc the earlier isotropic
        # bone-scale produced. Applied to base verts BEFORE skinning so the gut
        # follows the torso naturally through every pose; helper/joint-cube verts
        # are excluded so joint positions stay put. mesh["belly"] is an ADD amount
        # (0 = none; ~0.6-1.2 = big gut).
        try:
            _belly = float(mesh.get("belly", 0) or 0)
        except Exception:
            _belly = 0.0
        if _belly > 0:
            base_verts = _apply_belly(base_verts, _belly)
        # v1.199.132: lateral width (see _apply_width). 1.0 = exact no-op.
        try:
            _bw = float(mesh.get("body_width", 1.0) or 1.0)
        except Exception:  # noqa: BLE001
            _bw = 1.0
        if abs(_bw - 1.0) > 1e-6:
            base_verts = _apply_width(base_verts, _bw)
        # v1.199.135: anterior depth (see _apply_depth). 1.0 = exact no-op.
        try:
            _bd = float(mesh.get("body_depth", 1.0) or 1.0)
        except Exception:  # noqa: BLE001
            _bd = 1.0
        if abs(_bd - 1.0) > 1e-6:
            base_verts = _apply_depth(base_verts, _bd)

        # UNIFORM framing (VNCCS-style): ONE world->pixel scale for the whole set
        # so every pose renders the character at the SAME size.  Target: a standing
        # figure fills ~90% of the frame height (derived from the character's true
        # standing height, NOT each pose's own extent).  Then clamp so the widest /
        # tallest pose in THIS set still fits — applied uniformly to all, so the
        # character's height stays consistent pose-to-pose (fixes the drift that
        # per-pose auto-fit caused).
        if _len_scales or _size_scales:
            _rest_scaled = _apply_pose(base_verts, {}, [0, 0, 0], _len_scales, _size_scales)
            ref_ext_y = max(float(_rest_scaled[:, 1].max() - _rest_scaled[:, 1].min()), 0.001)
        else:
            ref_ext_y = max(float(base_verts[:, 1].max() - base_verts[:, 1].min()), 0.001)
        if ha:
            # v1.199.76 HEIGHT-ANCHORED framing: fix the world->pixel scale to a
            # NEUTRAL-height (0.5) version of this character instead of its own
            # standing height, so a short character genuinely renders shorter in
            # frame (bottom-aligned: everyone's feet share the floor line).
            try:
                _averts = _solve_base_verts({**mesh, "height": 0.5})
                if _belly > 0:
                    _averts = _apply_belly(_averts, _belly)
                if _len_scales or _size_scales:
                    _averts = _apply_pose(_averts, {}, [0, 0, 0], _len_scales, _size_scales)
                _a_ext = max(float(_averts[:, 1].max() - _averts[:, 1].min()), 0.001)
                # v1.199.78: cap the anchored shrink at ~10%. The VNCCS pose LoRA
                # is trained on a FULL-FRAME mannequin; a pose figure rendered much
                # smaller than the identity reference goes out-of-distribution and
                # Klein reconciles the two scales as a GHOST FIGURE ("person inside
                # the person"). Shortness still reads via proportions + up to 10%
                # smaller frame fill.
                ref_ext_y = min(_a_ext, ref_ext_y * 1.10)
            except Exception:  # noqa: BLE001
                pass
        z = 1.0
        try:
            _zc = float(cam_zoom)
            if 0.2 <= _zc <= 4.0:
                z = _zc
        except Exception:  # noqa: BLE001
            pass
        posed_list = []
        for pose in poses:
            pose = pose if isinstance(pose, dict) else {}
            posed_list.append(_apply_pose(base_verts, pose.get("bones", {}),
                                          pose.get("modelRotation", [0, 0, 0]), _len_scales, _size_scales))
        captures: List[str] = []
        if nc and not silhouette:
            # NODE-FAITHFUL camera: fixed perspective (fov 30, dist 45) at the
            # rest-mesh bbox center, honoring the blob's zoom/offset/yaw/pitch.
            # No fit-to-frame: short renders short, wide renders wide -- exactly
            # what the pose LoRA was trained on.  Safety: if the widest/tallest
            # pose would spill past ~96% of the canvas, zoom the whole SET out
            # uniformly (mimics the user zooming out in the panel).
            _np = _STATE["np"]
            _ctr = (base_verts.min(0) + base_verts.max(0)) / 2.0
            _ox = export.get("cam_offset_x", 0) or 0
            _oy = export.get("cam_offset_y", 0) or 0
            _yaw = export.get("cam_yaw_deg", 0) or 0
            _pit = export.get("cam_pitch_deg", 0) or 0
            _zoom = z
            _cp, _rt, _upv, _fw = _node_camera(_np, _ctr, _ox, _oy, _yaw, _pit)
            _over = 1.0
            for posed in posed_list:
                _px, _py, _d = _node_project(_np, _np.asarray(posed, dtype=_np.float64),
                                             width, height, _zoom, _cp, _rt, _upv, _fw)
                _exw = max(float(_px.max() - _px.min()), 1.0)
                _exh = max(float(_py.max() - _py.min()), 1.0)
                _over = max(_over, _exw / (0.96 * width), _exh / (0.96 * height))
            if _over > 1.0:
                _zoom = _zoom / _over
            for posed in posed_list:
                img = _render_pose_node(posed, width, height, bg, lights, _zoom,
                                        _ox, _oy, _yaw, _pit, _ctr,
                                        depth=(rmode == "depth"))
                buf = io.BytesIO()
                img.save(buf, format="PNG")
                captures.append("data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii"))
        else:
            # classic orthographic path (Qwen flows, silhouettes, thumbnails)
            # reserve headroom (top) + a small margin (bottom) so hats/tall hair fit.
            uniform_scale = height * (1.0 - hr - _BOTTOM_MARGIN) / ref_ext_y * z
            for posed in posed_list:
                _mn = posed[:, :2].min(axis=0)
                _mx = posed[:, :2].max(axis=0)
                hx = max(float((_mx[0] - _mn[0]) / 2.0), 0.001)
                hy = max(float((_mx[1] - _mn[1]) / 2.0), 0.001)
                uniform_scale = min(uniform_scale, width * 0.48 / hx, height * 0.48 / hy)
            for posed in posed_list:
                img = _render_pose(posed, width, height, bg, lights, cam_zoom,
                                   silhouette=silhouette, fixed_scale=uniform_scale,
                                   headroom=hr, bottom_align=ha)
                buf = io.BytesIO()
                img.save(buf, format="PNG")
                captures.append("data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii"))

        if len(_CACHE) >= _CACHE_MAX:
            _CACHE.pop(next(iter(_CACHE)))
        _CACHE[key] = list(captures)
        logger.info(f"vnccs pose_render: pre-rendered {len(captures)} pose capture(s) "
                    f"at {width}x{height} (mode={rmode})")
        return captures
    except Exception as e:  # noqa: BLE001 — feature is best-effort
        logger.warning(f"vnccs pose_render: pre-render failed ({e}) — submitting without captures")
        return None
