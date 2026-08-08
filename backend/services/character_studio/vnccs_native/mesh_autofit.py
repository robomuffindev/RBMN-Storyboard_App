"""Image -> pose-mannequin proportions (SAM 3D Body auto-fit).

The RBMN_SAM3D_Proportions worker node reconstructs a character's body from ONE
reference image and returns PERSONALIZED rest-pose 3D joint coordinates.  This
module turns those joints into the pose-mannequin `mesh` proportion block (the
per-limb `*_length` sliders + coarse weight/muscle), so `pose_render` renders the
mannequin with the RIGHT proportions and Klein stops copying a generic body.

Method (self-consistent with pose_render's bone-length scaling):
  person_frac[seg] = seg_len / standing_height          (from SAM3D rest joints)
  our_frac[seg]    = our_mannequin_seg / our_height      (from the neutral rig)
  target_scale     = person_frac / our_frac
  slider           = clamp(target_scale - 0.5, 0, 1)     (inverse of _length_slider_to_scale)
Segments are averaged L/R (symmetric mannequin; less detector noise).
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# SAM3D joint-name pairs whose distance is each segment's length.
_SEG_JOINTS = {
    "upper_arm": (("upperarm_l", "lowerarm_l"), ("upperarm_r", "lowerarm_r")),
    "forearm":   (("lowerarm_l", "hand_l"), ("lowerarm_r", "hand_r")),
    "thigh":     (("thigh_l", "calf_l"), ("thigh_r", "calf_r")),
    "shin":      (("calf_l", "foot_l"), ("calf_r", "foot_r")),
    "spine":     (("spine_01", "neck_01"),),
}
# segment -> the mesh `*_length` keys it drives (L/R share the averaged value)
_SEG_MESH_KEYS = {
    "upper_arm": ("upper_arm_l_length", "upper_arm_r_length"),
    "forearm":   ("forearm_l_length", "forearm_r_length"),
    "thigh":     ("thigh_l_length", "thigh_r_length"),
    "shin":      ("shin_l_length", "shin_r_length"),
    "spine":     ("spine_length",),
}
# first 9 SAM3D shape components -> VNCCS body axes (from vnccs_sam3d pose_import):
#   shape_params[i] = axis[i] * shape_norm[i] * shape_sign[i]  ->  axis = param/(norm*sign)
_SHAPE_NORM = (1.00, 2.78, 4.42, 8.74, 10.82, 11.70, 13.39, 13.83, 16.62)
_SHAPE_SIGN = (+1, -1, -1, +1, -1, +1, -1, +1, +1)


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


# v1.199.76: PER-SEGMENT clamps. Arm elongation is denied hard (1.10x forearms
# rendered claw-armed mannequins -> Klein extra limbs), but LEG SHORTENING is
# real signal: SAM3D consistently measures stocky characters' shins ~0.85-0.90x,
# and clamping that away (v1.199.75's flat +-7%) was exactly the "legs too
# long" complaint. Spine may shorten moderately too.
_SEG_CLAMP = {"upper_arm": (0.45, 0.55), "forearm": (0.45, 0.55),
              "thigh": (0.36, 0.55), "shin": (0.36, 0.55), "spine": (0.42, 0.55)}


def _slider_from_scale(scale: float, lo: float = 0.43, hi: float = 0.57) -> float:
    """Inverse of pose_render._length_slider_to_scale (scale = 0.5 + slider),
    clamped to the caller's per-segment bounds."""
    return round(_clamp(scale - 0.5, lo, hi), 3)


def _dist(a, b) -> float:
    return ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2) ** 0.5


def person_segment_fracs(data: Dict[str, Any]) -> Optional[Dict[str, float]]:
    """From the SAM3D proportions JSON -> {seg: length/standing_height}."""
    names = data.get("joint_names")
    coords = data.get("rest_joint_coords")
    if not names or not coords or len(names) != len(coords):
        return None
    idx = {n: i for i, n in enumerate(names)}

    def P(name):
        i = idx.get(name)
        return coords[i] if i is not None and i < len(coords) else None

    def seg_len(pairs):
        vals = []
        for a, b in pairs:
            pa, pb = P(a), P(b)
            if pa is not None and pb is not None:
                vals.append(_dist(pa, pb))
        return sum(vals) / len(vals) if vals else None

    head, fl, fr = P("head"), P("foot_l"), P("foot_r")
    if head is None or (fl is None and fr is None):
        return None
    foot_y = ((fl[1] if fl else fr[1]) + (fr[1] if fr else fl[1])) / 2.0
    height = abs(head[1] - foot_y)
    if height < 1e-6:
        return None
    out = {}
    for seg, pairs in _SEG_JOINTS.items():
        L = seg_len(pairs)
        if L is not None:
            out[seg] = L / height
    return out or None


def our_neutral_fracs(mesh: Dict[str, Any]) -> Optional[Dict[str, float]]:
    """OUR mannequin's neutral segment fractions for THIS mesh (coarse sliders applied,
    all `*_length` neutral).  Computed from the live pose_render skeleton so the
    length auto-fit references the same body the coarse sliders produce."""
    try:
        import types
        import numpy as np
        from . import pose_render as pr
        if not pr._ensure_loaded():
            return None
        base = dict(mesh or {})
        for k in ("upper_arm_l_length", "upper_arm_r_length", "forearm_l_length",
                  "forearm_r_length", "thigh_l_length", "thigh_r_length",
                  "shin_l_length", "shin_r_length", "spine_length"):
            base.pop(k, None)
        bv = pr._solve_base_verts(base)
        skel = pr._STATE["skeleton"].copy()
        skel.updateJointPositions(types.SimpleNamespace(vertices=bv))
        J = {b.name: np.asarray(b.headPos, dtype=float) for b in skel.boneslist}

        def d(a, b):
            return float(np.linalg.norm(J[a] - J[b]))

        height = abs(float(J["head"][1] - J["foot_l"][1]))
        if height < 1e-6:
            return None
        return {
            "upper_arm": d("upperarm_l", "lowerarm_l") / height,
            "forearm":   d("lowerarm_l", "hand_l") / height,
            "thigh":     d("thigh_l", "calf_l") / height,
            "shin":      d("calf_l", "foot_l") / height,
            "spine":     d("spine_01", "neck_01") / height,
        }
    except Exception as exc:  # noqa: BLE001
        logger.info("mesh_autofit: our_neutral_fracs failed: %s", exc)
        return None


def shape_params_to_coarse(data: Dict[str, Any]) -> Dict[str, float]:
    """First SAM3D shape axes -> coarse mannequin sliders (0..1).  fat->weight,
    muscle->muscle.  Best-effort; empty dict if params absent."""
    sp = data.get("shape_params")
    out: Dict[str, float] = {}
    if not isinstance(sp, list) or len(sp) < 2:
        return out
    try:
        fat_axis = float(sp[0]) / (_SHAPE_NORM[0] * _SHAPE_SIGN[0])
        muscle_axis = float(sp[1]) / (_SHAPE_NORM[1] * _SHAPE_SIGN[1])
        out["weight"] = round(_clamp(0.5 + 0.5 * fat_axis, 0.0, 1.0), 3)
        out["muscle"] = round(_clamp(0.5 + 0.5 * muscle_axis, 0.0, 1.0), 3)
        # v1.199.74: a heavy fat-axis implies a real gut -- drive the DIRECTIONAL
        # belly too (pose_render._apply_belly). Zero at/below average weight.
        out["belly"] = round(_clamp(1.7 * (out["weight"] - 0.55), 0.0, 1.3), 3)
    except Exception:  # noqa: BLE001
        return {}
    return out


def head_size_from_sam3d(data: Dict[str, Any]) -> Optional[float]:
    """head_size (isotropic) from head-to-body ratio.  Uses SAM3D joints for BOTH the
    person (rest) and the average body (canonical) and returns person/canonical, so the
    joint-vs-mesh measurement bias cancels (our mannequin neutral == the average head).
    Clamped conservatively -- head size is noisier than limb lengths."""
    names = data.get("joint_names")
    rest = data.get("rest_joint_coords")
    canon = data.get("canonical_joint_coords")
    if not names or not rest or not canon:
        return None
    idx = {n: i for i, n in enumerate(names)}
    hi = idx.get("head")
    ni = idx.get("neck_01")
    fl = idx.get("foot_l")
    fr = idx.get("foot_r")
    if hi is None or ni is None or (fl is None and fr is None):
        return None

    def head_frac(coords):
        try:
            neck = coords[ni][1]
            foot = ((coords[fl][1] if fl is not None else coords[fr][1])
                    + (coords[fr][1] if fr is not None else coords[fl][1])) / 2.0
            height = abs(coords[hi][1] - foot)
            if height < 1e-6:
                return None
            top = max(c[1] for c in coords[hi:])  # head + face landmarks -> crown
            return (top - neck) / height
        except Exception:  # noqa: BLE001
            return None

    pf = head_frac(rest)
    cf = head_frac(canon)
    if not pf or not cf or cf < 1e-6:
        return None
    return round(_clamp(pf / cf, 0.8, 1.3), 3)


def sam3d_to_mesh_proportions(proportions_json, mesh: Dict[str, Any],
                              include_coarse: bool = False) -> Dict[str, float]:
    """MAIN ENTRY: SAM3D proportions JSON (str or dict) + the current mesh block ->
    a partial mesh dict of `*_length` sliders (and optionally weight/muscle) to merge
    into `mesh`.  Returns {} on any failure (caller keeps the existing mesh)."""
    try:
        data = json.loads(proportions_json) if isinstance(proportions_json, str) else proportions_json
    except Exception:  # noqa: BLE001
        return {}
    if not isinstance(data, dict) or not data.get("ok", True):
        return {}
    person = person_segment_fracs(data)
    ours = our_neutral_fracs(mesh)
    if not person or not ours:
        return {}
    result: Dict[str, float] = {}
    for seg, keys in _SEG_MESH_KEYS.items():
        if seg not in person or seg not in ours or ours[seg] < 1e-6:
            continue
        _lo, _hi = _SEG_CLAMP.get(seg, (0.43, 0.57))
        slider = _slider_from_scale(person[seg] / ours[seg], _lo, _hi)
        for k in keys:
            result[k] = slider
    hs = head_size_from_sam3d(data)
    if hs is not None and abs(hs - 1.0) > 0.02:  # skip near-neutral (noise)
        result["head_size"] = hs
    if include_coarse:
        result.update(shape_params_to_coarse(data))
    logger.info("mesh_autofit: derived %d proportion sliders from SAM3D", len(result))
    return result


# --------------------------------------------------------------------------
# Worker call + per-character cache (auto-fit on clone, cached & reused)
# --------------------------------------------------------------------------
import hashlib
import os
import time
from pathlib import Path

PROP_NODE_CLASS = "RBMN_SAM3D_Proportions"


def _safe(name: str) -> str:
    return "".join(c for c in str(name or "") if c.isalnum())[:32] or "char"


def _cache_dir() -> Path:
    env = os.environ.get("RBMN_AUTOFIT_CACHE")
    if env:
        return Path(env)
    repo = Path(__file__).resolve().parents[4]  # .../vnccs_native/mesh_autofit.py -> repo root
    return repo / "runtime" / "mesh_autofit"


def detect_proportions_via_worker(client, image_bytes: bytes, filename: str,
                                  node_class: str = PROP_NODE_CLASS,
                                  timeout_s: int = 300) -> Optional[Dict[str, Any]]:
    """Upload the reference to the worker, run the RBMN SAM3D Proportions node
    headlessly, and return its parsed JSON dict (or None if the node is missing,
    errors, or times out)."""
    try:
        up = client.upload_image(filename, image_bytes, "", True, 120)
        name = up.get("name", filename)
        graph = {
            "load": {"class_type": "LoadImage", "inputs": {"image": name}},
            "prop": {"class_type": node_class, "inputs": {"image": ["load", 0]}},
        }
        res = client.submit_prompt(graph, timeout=120)
    except Exception as exc:  # noqa: BLE001  (node not installed, worker down, ...)
        logger.info("mesh_autofit: proportions node submit failed: %s", exc)
        return None
    pid = res.get("prompt_id") if isinstance(res, dict) else None
    if not pid:
        return None
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            hist = client.get_history(pid, timeout=30)
        except Exception:  # noqa: BLE001
            hist = None
        entry = hist.get(pid) if isinstance(hist, dict) else None
        outs = (entry or {}).get("outputs") or {}
        for out in outs.values():
            txt = out.get("text")
            if txt:
                try:
                    val = txt[0]
                    return json.loads(val) if isinstance(val, str) else val
                except Exception:  # noqa: BLE001
                    return None
        status = ((entry or {}).get("status") or {})
        if status.get("status_str") == "error":
            logger.info("mesh_autofit: proportions node errored on the worker")
            return None
        time.sleep(2)
    logger.info("mesh_autofit: proportions node timed out after %ss", timeout_s)
    return None


def get_autofit_mesh(client, character_name: str, image_bytes: Optional[bytes],
                     mesh: Dict[str, Any], include_coarse: bool = False) -> Dict[str, float]:
    """Cached per (character, reference-hash).  Returns the image-derived proportion
    block ({*_length ...}) for the character, running SAM3D once and reusing after.
    Returns {} when unavailable (no image / node absent / worker error)."""
    if not image_bytes:
        return {}
    try:
        digest = hashlib.sha256(bytes(image_bytes)).hexdigest()[:16]
    except Exception:  # noqa: BLE001
        return {}
    cdir = _cache_dir()
    # v1.199.74: cache name varies with include_coarse -- old limb-only caches
    # must not satisfy a coarse (weight/muscle/belly) request.
    cpath = cdir / ("%s_%s%s.json" % (_safe(character_name), digest,
                                      "_c3" if include_coarse else ""))
    if cpath.exists():
        try:
            cached = json.loads(cpath.read_text(encoding="utf-8"))
            if isinstance(cached, dict):
                return cached
        except Exception:  # noqa: BLE001
            pass
    data = detect_proportions_via_worker(
        client, image_bytes, "rbmn_autofit_%s.png" % _safe(character_name))
    if not data:
        return {}
    block = sam3d_to_mesh_proportions(data, mesh, include_coarse=include_coarse)
    if block:
        try:
            cdir.mkdir(parents=True, exist_ok=True)
            cpath.write_text(json.dumps(block), encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass
    return block


# --------------------------------------------------------------------------
# SAM3D body-view RENDER (the node's clean pose image, headless) — the real body
# --------------------------------------------------------------------------
BODYVIEWS_NODE_CLASS = "RBMN_SAM3D_BodyViews"


def render_sam3d_body_views(client, image_bytes, filename, views="front,right,left,back",
                            width=1024, height=1216, timeout_s=420,
                            side_bytes=None, side_filename="rbmn_sam3d_side_ref.png",
                            side_weight=0.5):
    """Reconstruct the character's body from ONE image and render clean views of it
    (front/side/back) via the RBMN_SAM3D_BodyViews worker node.  Returns a list of
    (label, png_bytes) in the requested order, or [] if the node is missing / errors.
    These are the Klein pose/body reference images that MATCH the character's real body."""
    labels = [v.strip().lower() for v in str(views).split(",") if v.strip()] or ["front"]
    try:
        up = client.upload_image(filename, image_bytes, "", True, 120)
        name = up.get("name", filename)
        view_inputs = {
            "image": ["load", 0], "views": ",".join(labels),
            "width": int(width), "height": int(height), "bbox_threshold": 0.8}
        graph = {
            "load": {"class_type": "LoadImage", "inputs": {"image": name}},
            "views": {"class_type": BODYVIEWS_NODE_CLASS, "inputs": view_inputs},
        }
        if side_bytes:
            try:
                sup = client.upload_image(side_filename, side_bytes, "", True, 120)
                sname = sup.get("name", side_filename)
                graph["load_side"] = {"class_type": "LoadImage", "inputs": {"image": sname}}
                view_inputs["side_image"] = ["load_side", 0]
                view_inputs["side_weight"] = float(side_weight)
                logger.info("mesh_autofit: SAM3D body-views fusing side profile (w=%.2f)", float(side_weight))
            except Exception as exc:  # noqa: BLE001
                logger.info("mesh_autofit: side upload failed, front only: %s", exc)
        res = client.submit_prompt(graph, timeout=120)
    except Exception as exc:  # noqa: BLE001
        logger.info("mesh_autofit: SAM3D body-views submit failed: %s", exc)
        return []
    pid = res.get("prompt_id") if isinstance(res, dict) else None
    if not pid:
        return []
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            hist = client.get_history(pid, timeout=30)
        except Exception:  # noqa: BLE001
            hist = None
        entry = hist.get(pid) if isinstance(hist, dict) else None
        outs = (entry or {}).get("outputs") or {}
        imgs = []
        for out in outs.values():
            imgs.extend(out.get("images") or [])
        if imgs:
            got = []
            for im in imgs:
                try:
                    data = client.view_image(im["filename"], im.get("subfolder", "") or "",
                                             im.get("type", "output") or "output", 120)
                    got.append(data)
                except Exception:  # noqa: BLE001
                    continue
            # align to requested view order by filename label when possible
            def _lab(im):
                fn = str(im.get("filename", "")).lower()
                for lb in labels:
                    if ("_view_%s." % lb) in fn:
                        return lb
                return None
            ordered = []
            by_lab = {}
            for im, data in zip(imgs, got):
                lb = _lab(im)
                if lb:
                    by_lab[lb] = data
            if len(by_lab) == len(labels):
                ordered = [(lb, by_lab[lb]) for lb in labels]
            else:
                ordered = list(zip(labels, got))[:len(got)]
            logger.info("mesh_autofit: SAM3D body-views rendered %d/%d", len(ordered), len(labels))
            return ordered
        status = ((entry or {}).get("status") or {})
        if status.get("status_str") == "error":
            logger.info("mesh_autofit: SAM3D body-views node errored on the worker")
            return []
        time.sleep(2)
    logger.info("mesh_autofit: SAM3D body-views timed out after %ss", timeout_s)
    return []
