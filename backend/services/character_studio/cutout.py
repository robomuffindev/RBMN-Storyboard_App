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
