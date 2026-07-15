"""2D skeleton pose renderer for Character Studio P2 (Pose Studio).

Ported from the vendored VNCCS reference (``vnccs/pose_utils/*.py``): an
18-joint OpenPose-style skeleton (BODY_25 subset, no mid_hip), a 512x1536
portrait canvas with the figure filling ~85% of the frame, and a schematic
renderer (soft body-part ovals + bone lines + joint dots) that is easy for
Qwen-Image-Edit / Klein to read as a pose control image.

Design: docs/CHARACTER_STUDIO.md (Phase 2).

Prefers ``cv2`` (OpenCV) exactly like the VNCCS original.  If OpenCV isn't
installed, falls back to a pure-PIL line/ellipse renderer so importing this
module (and listing presets / building thumbnails from cache) never crashes
just because opencv-python-headless isn't present yet.
"""
from __future__ import annotations

import json
import logging
import math
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

try:
    import cv2  # type: ignore
    import numpy as np  # type: ignore
    _HAVE_CV2 = True
except Exception as _cv2_err:  # pragma: no cover - environment dependent
    cv2 = None  # type: ignore
    np = None  # type: ignore
    _HAVE_CV2 = False
    logger.warning(f"pose_renderer: cv2 not available ({_cv2_err}); using PIL fallback renderer")

try:
    from PIL import Image, ImageDraw
    _HAVE_PIL = True
except Exception:  # pragma: no cover
    Image = None  # type: ignore
    ImageDraw = None  # type: ignore
    _HAVE_PIL = False


# ── Skeleton definition (vnccs/pose_utils/skeleton_512x1536.py) ────────────
CANVAS_WIDTH = 512
CANVAS_HEIGHT = 1536

DEFAULT_SKELETON: dict[str, tuple[float, float]] = {
    # EXACT parity with vnccs/pose_utils/skeleton_512x1536.py (the convention the
    # VNCCS PoseStudio LoRA was trained on). Only used as a whole-skeleton
    # fallback + editor seed; the bundled pose presets carry their own joints.
    "nose": (256, 200),
    "neck": (256, 280),
    "r_shoulder": (320, 320),
    "r_elbow": (350, 520),
    "r_wrist": (360, 720),
    "l_shoulder": (192, 320),
    "l_elbow": (162, 520),
    "l_wrist": (152, 720),
    "r_hip": (290, 720),
    "r_knee": (295, 1020),
    "r_ankle": (300, 1320),
    "l_hip": (222, 720),
    "l_knee": (217, 1020),
    "l_ankle": (212, 1320),
    "r_eye": (270, 185),
    "l_eye": (242, 185),
    "r_ear": (285, 195),
    "l_ear": (227, 195),
}

BONE_CONNECTIONS: list[tuple[str, str]] = [
    ("nose", "neck"),
    ("neck", "r_shoulder"),
    ("r_shoulder", "r_elbow"),
    ("r_elbow", "r_wrist"),
    ("neck", "l_shoulder"),
    ("l_shoulder", "l_elbow"),
    ("l_elbow", "l_wrist"),
    ("neck", "r_hip"),
    ("neck", "l_hip"),
    ("r_hip", "r_knee"),
    ("r_knee", "r_ankle"),
    ("l_hip", "l_knee"),
    ("l_knee", "l_ankle"),
    ("nose", "r_eye"),
    ("r_eye", "r_ear"),
    ("nose", "l_eye"),
    ("l_eye", "l_ear"),
]

BODY_PARTS: list[dict[str, Any]] = [
    {"name": "head", "joints": ["nose", "neck"], "width": 110, "color": "#FFE5D9"},
    {"name": "torso_right", "joints": ["neck", "r_hip"], "width": 165, "color": "#FFE5D9"},
    {"name": "torso_left", "joints": ["neck", "l_hip"], "width": 165, "color": "#FFE5D9"},
    {"name": "hip_band", "joints": ["r_hip", "l_hip"], "width": 190, "color": "#FFE5D9"},
    {"name": "r_upper_arm", "joints": ["r_shoulder", "r_elbow"], "width": 55, "color": "#FFE5D9"},
    {"name": "r_forearm", "joints": ["r_elbow", "r_wrist"], "width": 45, "color": "#FFE5D9"},
    {"name": "l_upper_arm", "joints": ["l_shoulder", "l_elbow"], "width": 55, "color": "#FFE5D9"},
    {"name": "l_forearm", "joints": ["l_elbow", "l_wrist"], "width": 45, "color": "#FFE5D9"},
    {"name": "r_thigh", "joints": ["r_hip", "r_knee"], "width": 95, "color": "#FFE5D9"},
    {"name": "r_calf", "joints": ["r_knee", "r_ankle"], "width": 75, "color": "#FFE5D9"},
    {"name": "l_thigh", "joints": ["l_hip", "l_knee"], "width": 95, "color": "#FFE5D9"},
    {"name": "l_calf", "joints": ["l_knee", "l_ankle"], "width": 75, "color": "#FFE5D9"},
]

_DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "character_studio"
_PRESETS_PATH = _DATA_DIR / "pose_presets.json"

# Friendly display names for the bundled presets, assigned positionally when
# the catalog JSON doesn't carry its own id/name (the vendored VNCCS catalog
# is a bare list of joint dicts).
_DEFAULT_PRESET_NAMES = [
    "Standing neutral", "Walking", "Sitting", "Arms crossed", "Waving",
    "Action pose", "Hands on hips", "Looking over shoulder", "Reaching up",
    "Relaxed lean", "Pointing", "Cheerful pose",
]


def _hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    hex_color = hex_color.lstrip("#")
    return tuple(int(hex_color[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


# ── Preset catalog loading / normalization ─────────────────────────────────
def _load_presets_raw() -> dict[str, Any]:
    try:
        return json.loads(_PRESETS_PATH.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning(f"pose_renderer: could not load {_PRESETS_PATH}: {e}")
        return {"canvas": {"width": CANVAS_WIDTH, "height": CANVAS_HEIGHT}, "poses": []}


def _normalized_presets() -> list[dict[str, Any]]:
    """Return presets as ``[{id, name, joints}, ...]``.

    Handles both shapes: a fully-authored catalog (each entry already has
    id/name/joints) and the bundled VNCCS-derived catalog (a bare list of
    joint dicts, one per pose, with no id/name) — the latter gets
    positional ids/names assigned on the fly.
    """
    raw = _load_presets_raw()
    poses = raw.get("poses") or []
    out: list[dict[str, Any]] = []
    for i, p in enumerate(poses):
        if isinstance(p, dict) and "joints" in p:
            pid = p.get("id") or f"pose_{i+1}"
            name = p.get("name") or (
                _DEFAULT_PRESET_NAMES[i] if i < len(_DEFAULT_PRESET_NAMES) else f"Pose {i+1}"
            )
            out.append({"id": pid, "name": name, "joints": p["joints"]})
        elif isinstance(p, dict):
            # Bare joints dict (no wrapper) — the shipped pose_presets.json shape.
            pid = f"pose_{i+1}"
            name = _DEFAULT_PRESET_NAMES[i] if i < len(_DEFAULT_PRESET_NAMES) else f"Pose {i+1}"
            out.append({"id": pid, "name": name, "joints": p})
    return out


def list_pose_presets() -> list[dict[str, str]]:
    """Return ``[{id, name}, ...]`` for every bundled pose preset."""
    return [{"id": p["id"], "name": p["name"]} for p in _normalized_presets()]


def get_pose_preset(pose_id: str) -> Optional[dict[str, Any]]:
    for p in _normalized_presets():
        if p["id"] == pose_id:
            return p
    return None


def _resolve_joints(pose_or_preset_id) -> tuple[dict[str, tuple[float, float]], str]:
    """Accept either a preset id (str) or a raw joints dict; return
    (joints, label) with joints coerced to ``{name: (x, y)}``."""
    if isinstance(pose_or_preset_id, str):
        preset = get_pose_preset(pose_or_preset_id)
        if not preset:
            raise ValueError(f"Unknown pose preset id: {pose_or_preset_id}")
        joints_raw = preset["joints"]
        label = preset["name"]
    elif isinstance(pose_or_preset_id, dict):
        joints_raw = pose_or_preset_id.get("joints", pose_or_preset_id)
        label = pose_or_preset_id.get("name", "custom")
    else:
        raise ValueError("pose_or_preset_id must be a preset id string or a joints dict")

    joints: dict[str, tuple[float, float]] = {}
    for name, xy in (joints_raw or {}).items():
        try:
            joints[name] = (float(xy[0]), float(xy[1]))
        except (TypeError, ValueError, IndexError):
            continue
    if not joints:
        joints = dict(DEFAULT_SKELETON)
    return joints, label


# ── cv2 renderer (primary path — matches VNCCS visual output) ─────────────
def _render_schematic_cv2(joints: dict[str, tuple[float, float]],
                           width: int = CANVAS_WIDTH, height: int = CANVAS_HEIGHT,
                           show_skeleton: bool = False):
    img = np.zeros((height, width, 4), dtype=np.uint8)

    def as_point(pt):
        try:
            return (int(round(pt[0])), int(round(pt[1])))
        except Exception:
            return None

    def draw_ellipse_between(p1, p2, w, color, alpha=220):
        a, b = as_point(p1), as_point(p2)
        if a is None or b is None:
            return
        cx, cy = (a[0] + b[0]) // 2, (a[1] + b[1]) // 2
        length = int(math.hypot(b[0] - a[0], b[1] - a[1]))
        angle = math.degrees(math.atan2(b[1] - a[1], b[0] - a[0]))
        rgba = (*color, alpha) if len(color) == 3 else color
        cv2.ellipse(img, (cx, cy), (max(w // 2, 4), max(length // 2, 4)), angle, 0, 360, rgba, -1)

    for part in BODY_PARTS:
        names = part["joints"]
        if len(names) == 2:
            j1, j2 = names
            if j1 in joints and j2 in joints:
                draw_ellipse_between(joints[j1], joints[j2], part["width"], _hex_to_rgb(part["color"]))

    # Skeleton overlay (bones + joint dots) is OFF by default: it made the
    # figure read as a wireframe once the light body ovals were composited.
    # The body-part ovals alone give the mannequin look VNCCS shows.
    if show_skeleton:
        for j1, j2 in BONE_CONNECTIONS:
            if j1 in joints and j2 in joints:
                p1, p2 = as_point(joints[j1]), as_point(joints[j2])
                if p1 and p2:
                    cv2.line(img, p1, p2, (60, 60, 60, 255), 2, cv2.LINE_AA)
        for name, xy in joints.items():
            p = as_point(xy)
            if p:
                cv2.circle(img, p, 6, (255, 100, 100, 255), -1, cv2.LINE_AA)
                cv2.circle(img, p, 6, (180, 50, 50, 255), 1, cv2.LINE_AA)

    return img  # RGBA numpy array


def _save_rgba_cv2(img_rgba, out_path: Path, bg: tuple[int, int, int] = (120, 124, 130)) -> None:
    """Composite the RGBA schematic over a plain background and save as PNG.

    A flat background (not transparent) makes the pose image behave as a
    normal control/reference photo for Qwen-Image-Edit / Klein rather than
    requiring alpha-aware consumers.
    """
    h, w = img_rgba.shape[:2]
    canvas = np.full((h, w, 3), bg, dtype=np.uint8)
    rgb = img_rgba[:, :, :3]
    alpha = (img_rgba[:, :, 3:4].astype(np.float32)) / 255.0
    composited = (rgb.astype(np.float32) * alpha + canvas.astype(np.float32) * (1 - alpha))
    out = composited.astype(np.uint8)
    # cv2 expects BGR
    out_bgr = cv2.cvtColor(out, cv2.COLOR_RGB2BGR)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out_path), out_bgr)


# ── PIL fallback renderer (no cv2 available) ───────────────────────────────
def _render_and_save_pil(joints: dict[str, tuple[float, float]], out_path: Path,
                          width: int = CANVAS_WIDTH, height: int = CANVAS_HEIGHT,
                          bg: tuple[int, int, int] = (120, 124, 130),
                          show_skeleton: bool = False) -> None:
    if not _HAVE_PIL:
        raise RuntimeError(
            "pose_renderer: neither cv2 nor PIL is available — cannot render pose images"
        )
    img = Image.new("RGB", (width, height), bg)
    draw = ImageDraw.Draw(img, "RGBA")

    def pt(name):
        xy = joints.get(name)
        if xy is None:
            return None
        return (int(round(xy[0])), int(round(xy[1])))

    # Soft body-part strokes (approximated as thick lines — no true ellipse
    # rotation without numpy, but visually adequate as a pose control image).
    for part in BODY_PARTS:
        names = part["joints"]
        if len(names) == 2:
            p1, p2 = pt(names[0]), pt(names[1])
            if p1 and p2:
                draw.line([p1, p2], fill=(*_hex_to_rgb(part["color"]), 220), width=max(part["width"] // 2, 8))

    if show_skeleton:
        for j1, j2 in BONE_CONNECTIONS:
            p1, p2 = pt(j1), pt(j2)
            if p1 and p2:
                draw.line([p1, p2], fill=(60, 60, 60, 255), width=3)
        for name in joints:
            p = pt(name)
            if p:
                r = 6
                draw.ellipse([p[0] - r, p[1] - r, p[0] + r, p[1] + r], fill=(255, 100, 100, 255),
                             outline=(180, 50, 50, 255))

    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path, format="PNG")


# ── Public API ──────────────────────────────────────────────────────────────
# ── OpenPose colored-skeleton renderer (control image for pose LoRAs) ───────
# Standard OpenPose 18-color palette (ported from vnccs/pose_utils/bone_colors).
_OP_BONE_COLORS: dict = {
    ("nose", "neck"): (0, 0, 255),
    ("neck", "r_shoulder"): (255, 0, 0),
    ("r_shoulder", "r_elbow"): (255, 170, 0),
    ("r_elbow", "r_wrist"): (255, 255, 0),
    ("neck", "l_shoulder"): (255, 85, 0),  # VNCCS FALLBACK_PALETTE[4]
    ("l_shoulder", "l_elbow"): (0, 255, 0),
    ("l_elbow", "l_wrist"): (0, 255, 85),
    ("neck", "r_hip"): (0, 255, 0),
    ("neck", "l_hip"): (0, 255, 170),
    ("r_hip", "r_knee"): (85, 255, 0),
    ("r_knee", "r_ankle"): (2, 153, 102),
    ("l_hip", "l_knee"): (0, 255, 255),
    ("l_knee", "l_ankle"): (0, 0, 255),
    ("nose", "r_eye"): (170, 0, 255),
    ("r_eye", "r_ear"): (255, 0, 170),
    ("nose", "l_eye"): (170, 0, 255),
    ("l_eye", "l_ear"): (255, 0, 170),
}
_OP_JOINT_COLORS: dict = {
    "nose": (0, 0, 255), "neck": (0, 0, 255),
    "r_eye": (170, 0, 255), "l_eye": (170, 0, 255),
    "r_ear": (255, 0, 170), "l_ear": (255, 0, 170),
    "r_shoulder": (255, 85, 0), "r_elbow": (255, 170, 0), "r_wrist": (255, 255, 0),
    "l_shoulder": (85, 255, 0), "l_elbow": (0, 255, 0), "l_wrist": (0, 255, 85),
    "r_hip": (0, 255, 170), "r_knee": (85, 255, 0), "r_ankle": (0, 255, 0),
    "l_hip": (0, 85, 255), "l_knee": (0, 255, 255), "l_ankle": (0, 170, 255),
}


def _render_openpose_cv2(joints: dict, width: int, height: int):
    """Colored OpenPose skeleton on a BLACK background (BGR uint8). This is the
    control image pose LoRAs (VNCCS QIE PoseStudio, Klein RefControl) expect."""
    img = np.zeros((height, width, 3), dtype=np.uint8)

    def as_pt(p):
        try:
            return (int(round(p[0])), int(round(p[1])))
        except Exception:
            return None

    for j1, j2 in BONE_CONNECTIONS:
        if j1 in joints and j2 in joints:
            p1, p2 = as_pt(joints[j1]), as_pt(joints[j2])
            if p1 and p2:
                rgb = _OP_BONE_COLORS.get((j1, j2)) or _OP_BONE_COLORS.get((j2, j1)) or (255, 255, 255)
                cv2.line(img, p1, p2, (rgb[2], rgb[1], rgb[0]), 3, cv2.LINE_AA)
    for name, xy in joints.items():
        p = as_pt(xy)
        if p:
            rgb = _OP_JOINT_COLORS.get(name, (255, 255, 255))
            cv2.circle(img, p, 4, (rgb[2], rgb[1], rgb[0]), -1, cv2.LINE_AA)
    return img


def _render_openpose_pil(joints: dict, out_path: Path, width: int, height: int) -> None:
    img = Image.new("RGB", (width, height), (0, 0, 0))
    draw = ImageDraw.Draw(img)

    def pt(name):
        xy = joints.get(name)
        if xy is None:
            return None
        return (int(round(xy[0])), int(round(xy[1])))

    for j1, j2 in BONE_CONNECTIONS:
        p1, p2 = pt(j1), pt(j2)
        if p1 and p2:
            rgb = _OP_BONE_COLORS.get((j1, j2)) or _OP_BONE_COLORS.get((j2, j1)) or (255, 255, 255)
            draw.line([p1, p2], fill=rgb, width=3)
    for name in joints:
        p = pt(name)
        if p:
            rgb = _OP_JOINT_COLORS.get(name, (255, 255, 255))
            draw.ellipse([p[0] - 4, p[1] - 4, p[0] + 4, p[1] + 4], fill=rgb)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path, format="PNG")


# ── OpenPose keypoint → VNCCS 18-joint conversion ──────────────────────────
_OP_BODY25 = [
    "nose", "neck", "r_shoulder", "r_elbow", "r_wrist", "l_shoulder", "l_elbow",
    "l_wrist", "mid_hip", "r_hip", "r_knee", "r_ankle", "l_hip", "l_knee",
    "l_ankle", "r_eye", "l_eye", "r_ear", "l_ear", "l_bigtoe", "l_smalltoe",
    "l_heel", "r_bigtoe", "r_smalltoe", "r_heel",
]
_OP_COCO18 = [
    "nose", "neck", "r_shoulder", "r_elbow", "r_wrist", "l_shoulder", "l_elbow",
    "l_wrist", "r_hip", "r_knee", "r_ankle", "l_hip", "l_knee", "l_ankle",
    "r_eye", "l_eye", "r_ear", "l_ear",
]
_VNCCS_JOINTS = set(DEFAULT_SKELETON.keys())


def openpose_keypoints_to_joints(flat, conf_thresh: float = 0.05, safe: float = 0.85):
    """Convert a flat OpenPose ``pose_keypoints_2d`` array (BODY_25 or COCO-18)
    to a VNCCS named-joint dict, scaled to fit the 512x1536 canvas (aspect
    preserved, centered). Returns None if too few valid joints."""
    try:
        flat = list(flat)
    except Exception:
        return None
    n = len(flat) // 3
    names = _OP_BODY25 if n >= 25 else (_OP_COCO18 if n >= 18 else None)
    if not names:
        return None
    pts: dict = {}
    for i, nm in enumerate(names):
        if nm not in _VNCCS_JOINTS:
            continue
        b = i * 3
        if b + 2 >= len(flat):
            break
        try:
            x = float(flat[b]); y = float(flat[b + 1])
            c = float(flat[b + 2]) if flat[b + 2] is not None else 1.0
        except Exception:
            continue
        if c < conf_thresh or (x == 0 and y == 0):
            continue
        pts[nm] = (x, y)
    if len(pts) < 6:
        return None
    xs = [p[0] for p in pts.values()]; ys = [p[1] for p in pts.values()]
    minx, maxx, miny, maxy = min(xs), max(xs), min(ys), max(ys)
    bw = max(maxx - minx, 1e-3); bh = max(maxy - miny, 1e-3)
    scale = min((CANVAS_WIDTH * safe) / bw, (CANVAS_HEIGHT * safe) / bh)
    cx, cy = CANVAS_WIDTH / 2.0, CANVAS_HEIGHT / 2.0
    ox, oy = (minx + maxx) / 2.0, (miny + maxy) / 2.0
    return {nm: [round(cx + (x - ox) * scale, 1), round(cy + (y - oy) * scale, 1)]
            for nm, (x, y) in pts.items()}


def openpose_obj_to_joints(obj):
    """Convert one parsed OpenPose JSON object to a VNCCS joint dict. Accepts
    the standard ``{"people":[{"pose_keypoints_2d":[...]}]}`` shape, a bare
    keypoints list, or an already-VNCCS ``{"joints":{...}}`` object."""
    if isinstance(obj, dict):
        if "people" in obj:
            best, best_n = None, -1
            for pp in (obj.get("people") or []):
                kp = pp.get("pose_keypoints_2d") or []
                cnt = sum(1 for i in range(2, len(kp), 3) if (kp[i] or 0) > 0.05)
                if cnt > best_n:
                    best_n, best = cnt, kp
            return openpose_keypoints_to_joints(best or [])
        if "pose_keypoints_2d" in obj:
            return openpose_keypoints_to_joints(obj.get("pose_keypoints_2d") or [])
        if isinstance(obj.get("joints"), dict):
            return {k: list(v) for k, v in obj["joints"].items() if k in _VNCCS_JOINTS}
    if isinstance(obj, list) and obj and isinstance(obj[0], (int, float)):
        return openpose_keypoints_to_joints(obj)
    return None


def _fit_joints_to_canvas(joints: dict, width: int, height: int,
                          native_w: int = CANVAS_WIDTH, native_h: int = CANVAS_HEIGHT) -> dict:
    """Scale joints authored in the native (512x1536) space to fit a width x
    height canvas, preserving aspect ratio and CENTERING the figure. Without
    this, a 512x1536-space skeleton drawn raw on a differently-sized canvas
    lands off-center (e.g. crammed to the left) — which the pose LoRAs then
    faithfully reproduce."""
    if not width or not height:
        return joints
    scale = min(width / float(native_w), height / float(native_h))
    ox = (width - native_w * scale) / 2.0
    oy = (height - native_h * scale) / 2.0
    out: dict = {}
    for name, xy in joints.items():
        try:
            out[name] = [xy[0] * scale + ox, xy[1] * scale + oy]
        except Exception:
            out[name] = xy
    return out


def render_pose(pose_or_preset_id, out_path: str | Path,
                 width: int = CANVAS_WIDTH, height: int = CANVAS_HEIGHT,
                 style: str = "mannequin") -> Path:
    """Render a pose (preset id or a raw joints dict) to a PNG at ``out_path``.

    style="mannequin" (default): schematic body ovals on a neutral gray
        backdrop — the browsable library look.
    style="openpose": the standard colored OpenPose skeleton on black — the
        control image pose LoRAs (VNCCS QIE PoseStudio / Klein RefControl)
        expect.

    Returns the resolved output path.
    """
    joints, _label = _resolve_joints(pose_or_preset_id)
    # Joints are authored in the native 512x1536 space; scale + center
    # them into the requested canvas so the figure is centered and keeps
    # its proportions at any target size.
    if width and height and (int(width) != CANVAS_WIDTH or int(height) != CANVAS_HEIGHT):
        joints = _fit_joints_to_canvas(joints, int(width), int(height))
    out_path = Path(out_path)
    if style == "openpose":
        if _HAVE_CV2:
            img = _render_openpose_cv2(joints, width, height)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            cv2.imwrite(str(out_path), img)
        else:
            _render_openpose_pil(joints, out_path, width, height)
        return out_path
    if _HAVE_CV2:
        img = _render_schematic_cv2(joints, width=width, height=height)
        _save_rgba_cv2(img, out_path)
    else:
        _render_and_save_pil(joints, out_path, width=width, height=height)
    return out_path


def render_preset_thumbnails(cache_dir: str | Path, size: int = 128) -> dict[str, Path]:
    """Render (or reuse cached) ~``size``px thumbnails for every bundled preset.

    Skips regeneration when a cached thumbnail already exists and is newer
    than the source catalog JSON.  Returns ``{preset_id: thumbnail_path}``.
    """
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    try:
        src_mtime = _PRESETS_PATH.stat().st_mtime
        # Also invalidate when the renderer itself changes (look/bg tweaks).
        src_mtime = max(src_mtime, Path(__file__).stat().st_mtime)
    except Exception:
        src_mtime = 0.0

    out: dict[str, Path] = {}
    for preset in _normalized_presets():
        pid = preset["id"]
        thumb_path = cache_dir / f"{pid}.png"
        if thumb_path.exists() and thumb_path.stat().st_mtime >= src_mtime:
            out[pid] = thumb_path
            continue
        # Render full-res then downscale — simplest path that works with
        # both the cv2 and PIL renderers.
        full_path = cache_dir / f"_full_{pid}.png"
        try:
            render_pose(preset, full_path)
            if _HAVE_PIL:
                with Image.open(full_path) as im:
                    ratio = size / max(im.width, im.height)
                    thumb = im.resize((max(1, int(im.width * ratio)), max(1, int(im.height * ratio))))
                    thumb.convert("RGB").save(thumb_path, format="PNG")
            else:
                # No PIL: fall back to using the full render as the "thumbnail"
                # (caller can still serve it; just larger than requested).
                thumb_path = full_path
            out[pid] = thumb_path
        except Exception as e:
            logger.warning(f"pose_renderer: thumbnail render failed for {pid}: {e}")
        finally:
            try:
                if full_path.exists() and full_path != thumb_path:
                    full_path.unlink()
            except Exception:
                pass
    return out
