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
        _STATE["loaded"] = True
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


def _apply_pose(base_verts, bones_data: Dict[str, Any], model_rotation) -> Any:
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

    posed = skinned
    mr = model_rotation if isinstance(model_rotation, (list, tuple)) and len(model_rotation) >= 3 else [0, 0, 0]
    rx, ry, rz = (float(mr[0]), float(mr[1]), float(mr[2]))
    if abs(rx) > 0.01 or abs(ry) > 0.01 or abs(rz) > 0.01:
        # viewer: skinnedMesh.rotation.set(...) — same XYZ order, about the origin;
        # the projection recentres afterwards so the pivot choice washes out.
        rot = _euler_xyz_matrix(np, rx * deg2rad, ry * deg2rad, rz * deg2rad)
        posed = np.asarray(posed @ rot.T)
    return posed


def _render_pose(posed, width: int, height: int, bg_color, lights, cam_zoom: float,
                 silhouette: bool = False, fixed_scale: Optional[float] = None):
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
        lights = pose_data.get("lights", [])
        mesh = pose_data.get("mesh", {}) or {}

        key = hashlib.sha256(json.dumps(
            {"m": mesh, "p": poses, "w": width, "h": height, "bg": bg,
             "z": cam_zoom, "l": lights, "sil": silhouette}, sort_keys=True, default=str,
        ).encode()).hexdigest()
        hit = _CACHE.get(key)
        if hit:
            return list(hit)

        base_verts = _solve_base_verts(mesh)

        # UNIFORM framing (VNCCS-style): ONE world->pixel scale for the whole set
        # so every pose renders the character at the SAME size.  Target: a standing
        # figure fills ~90% of the frame height (derived from the character's true
        # standing height, NOT each pose's own extent).  Then clamp so the widest /
        # tallest pose in THIS set still fits — applied uniformly to all, so the
        # character's height stays consistent pose-to-pose (fixes the drift that
        # per-pose auto-fit caused).
        ref_ext_y = max(float(base_verts[:, 1].max() - base_verts[:, 1].min()), 0.001)
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
                                          pose.get("modelRotation", [0, 0, 0])))
        uniform_scale = height * 0.90 / ref_ext_y * z
        for posed in posed_list:
            _mn = posed[:, :2].min(axis=0)
            _mx = posed[:, :2].max(axis=0)
            hx = max(float((_mx[0] - _mn[0]) / 2.0), 0.001)
            hy = max(float((_mx[1] - _mn[1]) / 2.0), 0.001)
            uniform_scale = min(uniform_scale, width * 0.48 / hx, height * 0.48 / hy)

        captures: List[str] = []
        for posed in posed_list:
            img = _render_pose(posed, width, height, bg, lights, cam_zoom,
                               silhouette=silhouette, fixed_scale=uniform_scale)
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            captures.append("data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii"))

        if len(_CACHE) >= _CACHE_MAX:
            _CACHE.pop(next(iter(_CACHE)))
        _CACHE[key] = list(captures)
        logger.info(f"vnccs pose_render: pre-rendered {len(captures)} pose capture(s) "
                    f"at {width}x{height}")
        return captures
    except Exception as e:  # noqa: BLE001 — feature is best-effort
        logger.warning(f"vnccs pose_render: pre-render failed ({e}) — submitting without captures")
        return None
