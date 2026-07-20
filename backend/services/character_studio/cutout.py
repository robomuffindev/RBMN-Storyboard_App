"""CPU background-removal fallback for Character Studio P2.

``studio_rmbg2`` (the ComfyUI RMBG2 node graph) is the primary path and
requires a VNCCS-capable worker.  When no such worker is online, ``process``
endpoints fall back to running this CPU path inline (synchronously) so the
user isn't blocked entirely.

Two tiers, both import-guarded and non-fatal:
1. ``rembg`` (U2Net) if importable — good quality, NOT added to
   pyproject.toml (optional/BYO install per spec).
2. A crude PIL corner-sampled-background chroma-distance cutout — always
   available (PIL is a hard dependency), rough but functional as a last
   resort so ``cutout_cpu`` never simply fails without producing anything.

``cutout_cpu`` never raises: it returns ``(True, None)`` on success or
``(False, "reason string")`` on failure so callers can report *why* instead
of crashing a request/orchestration stage.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

try:
    from PIL import Image
    _HAVE_PIL = True
except Exception:  # pragma: no cover
    Image = None  # type: ignore
    _HAVE_PIL = False


def _rembg_cutout(image_path: Path, out_path: Path) -> bool:
    try:
        import rembg  # type: ignore
    except Exception:
        return False
    try:
        data = image_path.read_bytes()
        out_bytes = rembg.remove(data)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(out_bytes)
        return True
    except Exception as e:
        logger.warning(f"cutout: rembg failed, falling back: {e}")
        return False


def _chroma_distance_cutout(image_path: Path, out_path: Path,
                             threshold: int = 36) -> bool:
    """Very crude last-resort cutout: sample the four corner pixels as the
    presumed background color, then make every pixel within ``threshold``
    Euclidean RGB distance of that average transparent.  Works acceptably
    for plain-background studio renders (which is what Character Studio
    base renders are, by design); not a real matting algorithm.
    """
    if not _HAVE_PIL:
        return False
    try:
        with Image.open(image_path) as im:
            im = im.convert("RGBA")
            w, h = im.size
            px = im.load()
            corners = [px[0, 0], px[w - 1, 0], px[0, h - 1], px[w - 1, h - 1]]
            bg = tuple(sum(c[i] for c in corners) / len(corners) for i in range(3))

            out = Image.new("RGBA", (w, h))
            out_px = out.load()
            for y in range(h):
                for x in range(w):
                    r, g, b, a = px[x, y]
                    dist = ((r - bg[0]) ** 2 + (g - bg[1]) ** 2 + (b - bg[2]) ** 2) ** 0.5
                    out_px[x, y] = (r, g, b, 0) if dist < threshold else (r, g, b, a)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out.save(out_path, format="PNG")
        return True
    except Exception as e:
        logger.warning(f"cutout: chroma-distance fallback failed: {e}")
        return False


def chroma_key_cutout(image_path: str | Path, out_path: str | Path,
                      bg_color: Optional[tuple] = None,
                      inner: float = 60.0, outer: float = 135.0,
                      despill: bool = True) -> tuple[bool, Optional[str]]:
    """Vectorized chroma key for KNOWN solid-color studio renders (numpy).

    For a character rendered on a flat green/blue field this is much cleaner
    than rembg: rembg is a general subject-matting net that leaves a colored
    edge halo and can keep shadow-tinted background, whereas a real chroma key
    keys on the actual background COLOR.  We:
      1. sample the background color as the MEDIAN of the image's border ring
         (robust to a stray edge pixel; or use ``bg_color`` if given),
      2. make pixels within ``inner`` RGB distance fully transparent and ramp
         alpha up to opaque by ``outer`` -- the soft band gives a clean
         anti-aliased edge AND removes shadow-darkened background (a shadowed
         green is still within ~outer of the pure green),
      3. despill the dominant background channel on the transition band so no
         green/blue fringe survives around the silhouette.

    Never raises: returns ``(True, note)`` / ``(False, reason)``.  Falls back to
    ``cutout_cpu`` at the call site when numpy/PIL are unavailable.
    """
    try:
        import numpy as np  # noqa: PLC0415
    except Exception as e:  # pragma: no cover
        return False, f"chroma_key needs numpy ({e})"
    if not _HAVE_PIL:
        return False, "chroma_key needs PIL"
    image_path = Path(image_path)
    out_path = Path(out_path)
    if not image_path.exists():
        return False, f"source image not found: {image_path}"
    try:
        with Image.open(image_path) as _im:
            im = _im.convert("RGBA")
            im.load()
        arr = np.asarray(im).astype(np.float32)
        rgb = arr[..., :3]
        h, w = rgb.shape[:2]
        if h < 2 or w < 2:
            return False, "image too small to key"
        if bg_color is None:
            ring = np.concatenate([rgb[0, :, :], rgb[-1, :, :],
                                   rgb[:, 0, :], rgb[:, -1, :]], axis=0)
            bg = np.median(ring, axis=0)
        else:
            bg = np.asarray(bg_color[:3], dtype=np.float32)
        lo = float(inner)
        hi = max(lo + 1.0, float(outer))
        dist = np.sqrt(((rgb - bg) ** 2).sum(axis=-1))
        ramp = np.clip((dist - lo) / (hi - lo), 0.0, 1.0)     # 0 = background
        out = arr.copy()
        out[..., 3] = arr[..., 3] * ramp
        if despill:
            ch = int(np.argmax(bg))                            # green or blue field
            o1, o2 = [c for c in range(3) if c != ch]
            cap = np.maximum(out[..., o1], out[..., o2])
            band = (ramp < 1.0) & (out[..., ch] > cap)         # only the keyed edge
            out[..., ch] = np.where(band, cap, out[..., ch])
        Image.fromarray(np.clip(out, 0, 255).astype("uint8"), "RGBA").save(out_path, "PNG")
        transp = float((out[..., 3] < 8).mean())
        opq = out[..., 3] >= 200
        luma = (float((0.299 * out[..., 0] + 0.587 * out[..., 1]
                       + 0.114 * out[..., 2])[opq].mean()) if bool(opq.any()) else 0.0)
        return True, (f"chroma-key ok: bg={bg.astype(int).tolist()}, "
                      f"transparent={transp * 100:.0f}%, subject_luma={luma:.0f}")
    except Exception as e:  # noqa: BLE001
        return False, f"chroma_key failed: {e}"


def _estimate_bg_rgb(arr):
    """Background colour = median of the image border ring (robust to a stray
    edge pixel).  ``arr`` is an HxWx3 numpy array."""
    import numpy as np
    ring = np.concatenate([arr[0, :, :], arr[-1, :, :], arr[:, 0, :], arr[:, -1, :]], axis=0)
    return np.median(ring, axis=0)


def normalize_base_set(images, bg_rgb=None, pad_frac=0.06, thresh=60.0):
    """Tighten + uniformly frame a set of base views (front/left/right/back).

    Each render leaves a lot of empty background (wasted space, inconsistent
    framing).  For every image we crop to the subject (non-background) bounding
    box + a uniform padding, then scale all crops to a common height and centre
    them on one shared green canvas, so the whole set reads as a consistent
    character sheet.  Returns a list of PNG bytes aligned with ``images``.
    Never raises: on any failure an image passes through unchanged.
    """
    try:
        import numpy as np
        from io import BytesIO
    except Exception:  # pragma: no cover
        return list(images)
    if not _HAVE_PIL:
        return list(images)
    # v1.187.2: preserve transparency. When a view already has a real alpha channel
    # (cut-out mannequin on a transparent background) we crop to the alpha bbox, scale
    # UNIFORMLY (never distort the aspect), and pad to the common frame with TRANSPARENT
    # space -- adding blank margin at the sides, not stretching the figure. Only the
    # legacy solid-background path falls back to an RGB fill.
    crops = []
    for data in images:
        try:
            src = Image.open(BytesIO(data))
            has_alpha = ("A" in src.getbands())
            if has_alpha:
                im = src.convert("RGBA")
                alpha = np.asarray(im)[:, :, 3]
                ys, xs = np.where(alpha > 16)
                bg = None                       # transparent-pad marker
            else:
                im = src.convert("RGB")
                arr = np.asarray(im).astype(np.float32)
                bg = (np.asarray(bg_rgb, dtype=np.float32) if bg_rgb is not None
                      else _estimate_bg_rgb(arr))
                dist = np.sqrt(((arr - bg) ** 2).sum(-1))
                ys, xs = np.where(dist > float(thresh))
            if len(xs) == 0:
                crops.append((im, bg))
                continue
            x0, x1 = int(xs.min()), int(xs.max())
            y0, y1 = int(ys.min()), int(ys.max())
            pad = int(max(y1 - y0, x1 - x0) * float(pad_frac))
            x0 = max(0, x0 - pad); y0 = max(0, y0 - pad)
            x1 = min(im.width, x1 + pad); y1 = min(im.height, y1 + pad)
            crops.append((im.crop((x0, y0, x1, y1)), bg))
        except Exception:  # noqa: BLE001
            try:
                crops.append((Image.open(BytesIO(data)).convert("RGBA"), None))
            except Exception:  # noqa: BLE001
                return list(images)
    if not crops:
        return list(images)
    target_h = max(c.height for c, _ in crops)
    scaled = []
    for c, bg in crops:
        s = target_h / c.height                 # uniform scale => aspect preserved
        nw = max(1, round(c.width * s))
        scaled.append((c.resize((nw, target_h), Image.LANCZOS), bg))
    target_w = max(c.width for c, _ in scaled)
    out = []
    for c, bg in scaled:
        if bg is None:
            # transparent frame: pad the sides with blank (0,0,0,0) space
            canvas = Image.new("RGBA", (target_w, target_h), (0, 0, 0, 0))
            src = c if c.mode == "RGBA" else c.convert("RGBA")
            canvas.paste(src, ((target_w - c.width) // 2, 0), src)
        else:
            fill = tuple(int(x) for x in bg)
            canvas = Image.new("RGB", (target_w, target_h), fill)
            canvas.paste(c, ((target_w - c.width) // 2, 0))
        b = BytesIO(); canvas.save(b, "PNG"); out.append(b.getvalue())
    return out


def rembg_cutout(image_path: str | Path, out_path: str | Path) -> tuple[bool, Optional[str]]:
    """Subject-based background removal via rembg (U2Net), when installed.

    Background-INDEPENDENT: unlike the colour chroma key it does not care whether
    the figure fills the frame or the backdrop is a clean uniform colour, so it is
    the most robust option for full-body pose sprites (a frame-filling figure
    contaminates the chroma key's border-ring background sample and can leave the
    character semi-transparent/dark).  Returns (False, note) when rembg is not
    importable so the caller can fall back to the chroma key / crude cutout.
    Install on the app host with:  pip install rembg --break-system-packages
    """
    image_path = Path(image_path)
    out_path = Path(out_path)
    if not image_path.exists():
        return False, f"source image not found: {image_path}"
    if _rembg_cutout(image_path, out_path):
        return True, "rembg (subject segmentation)"
    return False, "rembg not installed"


def cutout_cpu(image_path: str | Path, out_path: str | Path) -> tuple[bool, Optional[str]]:
    """Best-effort CPU background removal.  Always produces *some* output on
    success; returns ``(False, reason)`` on total failure — never raises."""
    image_path = Path(image_path)
    out_path = Path(out_path)
    if not image_path.exists():
        return False, f"source image not found: {image_path}"

    if _rembg_cutout(image_path, out_path):
        return True, None
    if _chroma_distance_cutout(image_path, out_path):
        return True, "used crude chroma-distance fallback (install 'rembg' for better quality)"
    return False, "no cutout method available (rembg not installed and PIL fallback failed)"
