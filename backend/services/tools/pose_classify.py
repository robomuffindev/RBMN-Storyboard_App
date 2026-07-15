"""Pose Organizer helpers: classify a pose file's type from its content,
auto-tag a pose from its keypoint geometry, and compute a dedup hash.

Kept dependency-light: cv2/numpy when available, PIL fallback, and pure-Python
for the geometry/dedup logic (never raises).
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

try:
    import cv2  # type: ignore
    import numpy as np  # type: ignore
    _HAVE_CV2 = True
except Exception:
    cv2 = None  # type: ignore
    np = None  # type: ignore
    _HAVE_CV2 = False

try:
    from PIL import Image  # type: ignore
    _HAVE_PIL = True
except Exception:
    Image = None  # type: ignore
    _HAVE_PIL = False

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
JSON_EXTS = {".json"}


def image_type_heuristic(path: str | Path) -> str:
    """Guess an image pose-file's kind WITHOUT a vision model:
    ``openpose_img`` (black bg + thin saturated colored lines),
    ``depth`` (near-grayscale), or ``natural`` (mannequin render / photo / art).
    Returns "natural" when it can't tell."""
    p = Path(path)
    try:
        if _HAVE_CV2:
            img = cv2.imread(str(p))
            if img is None:
                return "natural"
            img = cv2.resize(img, (128, 128), interpolation=cv2.INTER_AREA)
            b, g, r = img[:, :, 0].astype("int16"), img[:, :, 1].astype("int16"), img[:, :, 2].astype("int16")
            maxc = np.maximum(np.maximum(r, g), b)
            minc = np.minimum(np.minimum(r, g), b)
            sat = (maxc - minc)
            black_ratio = float((maxc < 30).mean())
            gray_ratio = float((sat < 12).mean())
            colored_ratio = float((sat > 60).mean())
        elif _HAVE_PIL:
            im = Image.open(p).convert("RGB").resize((128, 128))
            px = list(im.getdata())
            n = len(px) or 1
            black = sum(1 for (rr, gg, bb) in px if max(rr, gg, bb) < 30)
            gray = sum(1 for (rr, gg, bb) in px if (max(rr, gg, bb) - min(rr, gg, bb)) < 12)
            colored = sum(1 for (rr, gg, bb) in px if (max(rr, gg, bb) - min(rr, gg, bb)) > 60)
            black_ratio, gray_ratio, colored_ratio = black / n, gray / n, colored / n
        else:
            return "natural"
    except Exception as e:
        logger.debug(f"pose_classify: heuristic failed for {p}: {e}")
        return "natural"

    # OpenPose skeleton: mostly black canvas with a small set of saturated lines.
    if black_ratio > 0.55 and 0.005 < colored_ratio < 0.35:
        return "openpose_img"
    # Depth / normal / lineart maps read as near-grayscale.
    if gray_ratio > 0.9:
        return "depth"
    return "natural"


def classify_path(path: str | Path) -> str:
    """Coarse type from extension + (for images) the heuristic. JSON keypoint
    files → "keypoints"; images → openpose_img / depth / natural."""
    p = Path(path)
    ext = p.suffix.lower()
    if ext in JSON_EXTS:
        return "keypoints"
    if ext in IMAGE_EXTS:
        return image_type_heuristic(p)
    return "unknown"


# ── Geometry auto-tags ──────────────────────────────────────────────────────
def _y(j: dict, name: str) -> Optional[float]:
    v = j.get(name)
    return float(v[1]) if v else None


def _avg(*vals) -> Optional[float]:
    xs = [v for v in vals if v is not None]
    return sum(xs) / len(xs) if xs else None


def auto_tags_from_joints(j: dict) -> list[str]:
    """Cheap, robust tags derived from keypoint geometry (canvas y grows DOWN).
    Covers orientation, posture, and arm position — enough to make thousands of
    imported poses browsable without a vision pass."""
    tags: list[str] = []
    if not j:
        return tags

    has_le, has_re = "l_eye" in j, "r_eye" in j
    has_lear, has_rear = "l_ear" in j, "r_ear" in j
    if has_le and has_re:
        tags.append("front")
    elif has_le != has_re:
        tags.append("profile")
    elif (has_lear or has_rear) and not (has_le or has_re):
        tags.append("back")
    else:
        tags.append("three-quarter")

    hip = _avg(_y(j, "l_hip"), _y(j, "r_hip"))
    knee = _avg(_y(j, "l_knee"), _y(j, "r_knee"))
    ankle = _avg(_y(j, "l_ankle"), _y(j, "r_ankle"))
    shoulder = _avg(_y(j, "l_shoulder"), _y(j, "r_shoulder"))

    if hip is not None and ankle is not None and shoulder is not None:
        torso = max(abs(hip - shoulder), 1.0)
        leg_span = ankle - hip
        if leg_span < torso * 0.6:
            tags.append("sitting")
        elif leg_span < torso * 1.2:
            tags.append("crouching")
        else:
            tags.append("standing")
        # Lying: torso roughly horizontal (shoulders and hips at similar y AND
        # ankles not far below hips).
        if abs(hip - shoulder) < torso * 0.35 and leg_span < torso * 0.8:
            tags.append("lying")

    # Arms up/down from wrists vs shoulders.
    lw, rw = _y(j, "l_wrist"), _y(j, "r_wrist")
    if shoulder is not None:
        ups = sum(1 for w in (lw, rw) if w is not None and w < shoulder - 20)
        if ups == 2:
            tags.append("both-arms-raised")
        elif ups == 1:
            tags.append("one-arm-raised")
        else:
            tags.append("arms-down")

    return tags


def dedup_hash(j: dict, gx: int = 12, gy: int = 32) -> str:
    """Quantized shape hash: normalize joints to their bbox then bin to a
    gx×gy grid so near-identical poses collapse. Empty for <6 joints."""
    if not j or len(j) < 6:
        return ""
    xs = [float(v[0]) for v in j.values()]
    ys = [float(v[1]) for v in j.values()]
    minx, maxx, miny, maxy = min(xs), max(xs), min(ys), max(ys)
    bw = max(maxx - minx, 1e-3)
    bh = max(maxy - miny, 1e-3)
    import hashlib
    parts = []
    for name in sorted(j.keys()):
        x, y = float(j[name][0]), float(j[name][1])
        cx = int((x - minx) / bw * (gx - 1))
        cy = int((y - miny) / bh * (gy - 1))
        parts.append(f"{name}:{cx},{cy}")
    return hashlib.md5("|".join(parts).encode()).hexdigest()[:16]
