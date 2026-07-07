"""CPU face detection for Character Studio P2 (emotion / Klein-inpaint path).

Two consumers:
- ``build_face_masked_rgba``: makes the face region TRANSPARENT in the alpha
  channel of an RGBA copy of the source image, for the ``klein_inpaint``
  workflow (source_masked_asset_id) — the emotion-edit region.
- ``crop_face``: crops a face-only image for dataset/manifest use (recorded
  in ``character.manifest["emotions"][key]["face_crop_rel"]``).

Detection order (best available wins, never raises):
1. cv2.FaceDetectorYN (YuNet) if the ONNX model file exists locally at
   ``backend/data/character_studio/face_detection_yunet_2023mar.onnx``
   (not bundled — this repo does not ship binary model weights; drop the
   official OpenCV Zoo file there to enable it).
2. cv2 Haar cascade (``cv2.data.haarcascades`` — ships with opencv-python).
3. None — callers must handle a ``None`` bbox gracefully (e.g. skip the
   mask-based edit, or fall back to a centered generic crop).
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
except Exception as _e:  # pragma: no cover
    cv2 = None  # type: ignore
    np = None  # type: ignore
    _HAVE_CV2 = False
    logger.warning(f"faces: cv2 not available ({_e}) — face detection disabled")

try:
    from PIL import Image
    _HAVE_PIL = True
except Exception:  # pragma: no cover
    Image = None  # type: ignore
    _HAVE_PIL = False

_YUNET_MODEL_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "data" / "character_studio" / "face_detection_yunet_2023mar.onnx"
)


_YUNET_URL = (
    "https://github.com/opencv/opencv_zoo/raw/main/models/"
    "face_detection_yunet/face_detection_yunet_2023mar.onnx"
)
_yunet_download_attempted = False


def _ensure_yunet_model() -> bool:
    """Auto-download the YuNet onnx (~345KB, Apache-2.0, official opencv_zoo)
    on first use.  Runs on the app host at runtime; on any failure we log once
    and the caller falls through to the Haar cascade."""
    global _yunet_download_attempted
    if _YUNET_MODEL_PATH.exists():
        return True
    if _yunet_download_attempted:
        return False
    _yunet_download_attempted = True
    try:
        import httpx
        logger.info(f"faces: downloading YuNet face model from opencv_zoo → {_YUNET_MODEL_PATH}")
        r = httpx.get(_YUNET_URL, follow_redirects=True, timeout=60.0)
        r.raise_for_status()
        if len(r.content) < 100_000:
            raise ValueError(f"unexpectedly small download ({len(r.content)} bytes)")
        _YUNET_MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = _YUNET_MODEL_PATH.with_suffix(".onnx.part")
        tmp.write_bytes(r.content)
        tmp.replace(_YUNET_MODEL_PATH)
        logger.info("faces: YuNet model downloaded OK")
        return True
    except Exception as e:
        logger.warning(f"faces: YuNet download failed ({e}) — using Haar cascade fallback")
        return False


def _detect_face_bbox_yunet(bgr) -> Optional[tuple[int, int, int, int]]:
    if not _ensure_yunet_model():
        return None
    try:
        h, w = bgr.shape[:2]
        detector = cv2.FaceDetectorYN.create(
            str(_YUNET_MODEL_PATH), "", (w, h), score_threshold=0.7,
        )
        detector.setInputSize((w, h))
        _, faces = detector.detect(bgr)
        if faces is None or len(faces) == 0:
            return None
        # Largest face by area
        best = max(faces, key=lambda f: float(f[2]) * float(f[3]))
        x, y, fw, fh = best[0], best[1], best[2], best[3]
        return (int(x), int(y), int(fw), int(fh))
    except Exception as e:
        logger.warning(f"faces: YuNet detection failed: {e}")
        return None


def _detect_face_bbox_haar(bgr) -> Optional[tuple[int, int, int, int]]:
    try:
        cascade_path = Path(cv2.data.haarcascades) / "haarcascade_frontalface_default.xml"
        if not cascade_path.exists():
            return None
        cascade = cv2.CascadeClassifier(str(cascade_path))
        gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
        faces = cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(48, 48))
        if faces is None or len(faces) == 0:
            return None
        # Largest face
        x, y, fw, fh = max(faces, key=lambda f: f[2] * f[3])
        return (int(x), int(y), int(fw), int(fh))
    except Exception as e:
        logger.warning(f"faces: Haar cascade detection failed: {e}")
        return None


def detect_face_bbox(image_path: str | Path) -> Optional[dict]:
    """Detect the largest face in ``image_path``.

    Returns ``{"x", "y", "w", "h", "method"}`` in source-image pixel
    coordinates, or ``None`` if detection is unavailable or found nothing.
    Never raises.
    """
    if not _HAVE_CV2:
        return None
    p = Path(image_path)
    if not p.exists():
        return None
    try:
        bgr = cv2.imread(str(p))
        if bgr is None:
            return None
    except Exception as e:
        logger.warning(f"faces: could not read {p}: {e}")
        return None

    bbox = _detect_face_bbox_yunet(bgr)
    method = "yunet"
    if bbox is None:
        bbox = _detect_face_bbox_haar(bgr)
        method = "haar"
    if bbox is None:
        return None
    x, y, w, h = bbox
    return {"x": x, "y": y, "w": w, "h": h, "method": method}


def _expand_bbox(bbox: dict, expand_pct: float, img_w: int, img_h: int) -> dict:
    x, y, w, h = bbox["x"], bbox["y"], bbox["w"], bbox["h"]
    ex = int(w * expand_pct)
    ey = int(h * expand_pct)
    nx = max(0, x - ex)
    ny = max(0, y - ey)
    nx2 = min(img_w, x + w + ex)
    ny2 = min(img_h, y + h + ey)
    return {"x": nx, "y": ny, "w": nx2 - nx, "h": ny2 - ny}


def build_face_masked_rgba(image_path: str | Path, out_path: str | Path,
                            expand_pct: float = 0.25, feather_px: int = 12) -> Optional[dict]:
    """Write an RGBA copy of ``image_path`` with the (expanded) face rect made
    TRANSPARENT in alpha — the ``klein_inpaint`` source_masked convention
    (painted/inpaint region = transparent).  A soft feathered edge avoids a
    hard rectangular seam in the composited result.

    Returns the (expanded) bbox dict, or ``None`` if no face was detected
    (caller should treat this as "cannot use the klein emotion inpaint path
    for this image" and fall back / surface a warning — never crashes).
    """
    if not _HAVE_PIL:
        logger.warning("faces: PIL not available — cannot build masked RGBA")
        return None
    p = Path(image_path)
    if not p.exists():
        return None
    bbox = detect_face_bbox(p)
    if not bbox:
        return None

    try:
        with Image.open(p) as im:
            im = im.convert("RGBA")
            w, h = im.size
            exp = _expand_bbox(bbox, expand_pct, w, h)

            alpha = im.split()[-1].copy()
            # Paint the region opaque->transparent with a feathered edge.
            if _HAVE_CV2 and np is not None:
                mask = np.array(alpha)
                mask[:] = 255
                x0, y0, bw, bh = exp["x"], exp["y"], exp["w"], exp["h"]
                x1, y1 = x0 + bw, y0 + bh
                mask[y0:y1, x0:x1] = 0
                if feather_px > 0:
                    mask = cv2.GaussianBlur(mask, (0, 0), sigmaX=feather_px)
                from PIL import Image as _PILImage
                alpha = _PILImage.fromarray(mask)
            else:
                from PIL import ImageDraw
                draw = ImageDraw.Draw(alpha)
                draw.rectangle(
                    [exp["x"], exp["y"], exp["x"] + exp["w"], exp["y"] + exp["h"]], fill=0
                )

            im.putalpha(alpha)
            out_path = Path(out_path)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            im.save(out_path, format="PNG")
    except Exception as e:
        logger.warning(f"faces: build_face_masked_rgba failed for {p}: {e}")
        return None

    return {**exp, "method": bbox["method"], "source_bbox": bbox}


def crop_face(image_path: str | Path, out_path: str | Path,
              expand_pct: float = 0.6) -> Optional[dict]:
    """Crop the largest detected face (expanded) out of ``image_path`` and
    save it to ``out_path``.  Returns the crop bbox dict, or ``None`` if no
    face was detected."""
    if not _HAVE_PIL:
        return None
    p = Path(image_path)
    if not p.exists():
        return None
    bbox = detect_face_bbox(p)
    if not bbox:
        return None
    try:
        with Image.open(p) as im:
            im = im.convert("RGB")
            w, h = im.size
            exp = _expand_bbox(bbox, expand_pct, w, h)
            cropped = im.crop((exp["x"], exp["y"], exp["x"] + exp["w"], exp["y"] + exp["h"]))
            out_path = Path(out_path)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            cropped.save(out_path, format="PNG")
    except Exception as e:
        logger.warning(f"faces: crop_face failed for {p}: {e}")
        return None
    return {**exp, "method": bbox["method"]}
