"""Where the PERSON is in the frame — the instrument crop has been waiting for.

v1.245.0 (2026-08-05)

WHY THIS EXISTS
    Face geometry (v1.242/v1.243) answers "how tight is the crop" and cannot
    answer the question that actually matters for a full-body shot: **are his
    feet inside the picture**.  A face box tells you nothing about feet.

    It also cannot reliably separate `upper` from `full` — measured, their
    face-height medians sit only 1.7x apart, against a within-type spread of
    1.6x.  Two problems, one missing instrument.

THE MEASUREMENT THAT ANSWERS BOTH
    A person segmentation gives the subject's bounding box, and the box's
    relationship to the frame EDGES is the whole answer:

        full        the subject must NOT touch the bottom edge.  Feet inside the
                    frame is exactly what "head to feet" means, and a body box
                    that runs off the bottom is a body that is cut off.
        upper       the subject MUST touch the bottom edge.  A waist-up shot is
                    cut off at the waist BY DESIGN; a waist-up shot with clear
                    air under it is not a waist-up shot, it is a full body
                    rendered small.
        headshot    same — must touch the bottom.
        face        same.

    That is a crisp binary the face box cannot produce, and it separates `upper`
    from `full` on a property that is not a matter of degree.

    Every shot type also wants the top of the head INSIDE the frame, which is a
    second edge test in the other direction.

HONEST LIMITS, STATED BEFORE THEY ARE DISCOVERED
    * `rembg` (u2net_human_seg since v1.264) segments a PERSON. The plain
      `u2net` it used before segments the salient object, not "a person".  On a busy
      background it can take in a chair or a doorway.  So the box is trusted
      only when the mask is a plausible single subject — coverage between 3%
      and 90% of the frame, and one dominant connected region.  Outside that it
      returns UNMEASURED, which never fails an image.
    * The subject box includes hair, clothing and anything held.  "Touches the
      bottom edge" on a full-body shot might be a long coat rather than a
      cropped foot.  It is still the right flag: a full-body row whose subject
      reaches the frame edge is worth a human's eye either way.
    * A missing `rembg` is a DEGRADED mode, exactly like a missing insightface —
      crop simply goes back to being unchecked, and the summary says so.

Everything here is CPU-only and lazily loaded: no GPU contention with a render
or a training run.
"""
from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_LOCK = threading.Lock()
# The segmentation model. Person-specific on purpose — see v1.264 below.
MODEL = "u2net_human_seg"

_SESSION: Any = None
_STATE: Dict[str, Any] = {"tried": False, "error": None}
_CACHE: Dict[Tuple[str, float, int], Optional[Dict[str, Any]]] = {}
_CACHE_MAX = 2048

# ── what counts as touching an edge ──────────────────────────────────────────
# A share of the image's own height/width, so it scales with the canvas.  1.5%
# of 1344px is 20px: close enough to the edge that a foot is being cut, far
# enough that antialiasing on a clean margin does not trip it.
EDGE_TOL = 0.015

# The mask has to look like ONE person for its box to mean anything.
MIN_COVERAGE = 0.03     # below this it found a detail, not a subject
MAX_COVERAGE = 0.90     # above this it segmented the background too
MIN_DOMINANCE = 0.75    # the largest region must be this much of the mask

# Which shot types are SUPPOSED to run off the bottom of the frame.
# `full` is the only one that is not — that is what "head to feet" means.
CUT_AT_BOTTOM = {"face": True, "headshot": True, "upper": True, "full": False}

# v1.246: and which are supposed to keep the top of the head INSIDE the frame.
# Measured on 20 real images: all four `face` rows and three of four `headshot`
# rows have the subject touching the top edge, because an extreme close-up fills
# the frame — that is what the shot IS, not a defect. The distinction between a
# correct tight crop and a sliced forehead is not one this instrument can make,
# so on those two shot types it does not pretend to.
CHECK_TOP = {"face": False, "headshot": False, "upper": True, "full": True}


def _session():
    """Load u2net once, on first use.  A missing dependency is a DEGRADED mode,
    never an exception: dataset QC still works without it, just without a crop
    check."""
    global _SESSION
    if _SESSION is not None or _STATE["tried"]:
        return _SESSION
    with _LOCK:
        if _SESSION is not None or _STATE["tried"]:
            return _SESSION
        _STATE["tried"] = True
        try:
            from rembg import new_session
            # v1.264: NOT "u2net". That model segments the salient object and
            # loses a sunlit beige shirt against a warm brick wall, which failed
            # five correct images of dorian-v1 for "clear space below him" where
            # the space was his shirt. Measured, both models, all 40 images,
            # same rule: u2net 5 false failures, u2net_human_seg 0 — and they
            # agree on every full-body row, so the bottom-edge separator that
            # this mask exists for is unchanged. See scripts\\mask_probe.py.
            _SESSION = new_session(MODEL)
            logger.info("subject: rembg u2net ready (CPU)")
        except Exception as e:  # noqa: BLE001
            _STATE["error"] = f"{type(e).__name__}: {e}"
            logger.warning("subject: unavailable (%s) — crop stays unchecked",
                           _STATE["error"])
    return _SESSION


def available() -> bool:
    return _session() is not None


def health() -> Dict[str, Any]:
    ok = available()
    return {"available": ok,
            "model": "rembg u2net (CPU)" if ok else None,
            "error": None if ok else (_STATE.get("error") or "not loaded"),
            "install": None if ok else "pip install rembg onnxruntime",
            "edge_tolerance": EDGE_TOL,
            "cut_at_bottom": CUT_AT_BOTTOM,
            "check_top": CHECK_TOP,
            "note": ("A full-body shot must NOT touch the bottom edge — feet inside the "
                     "frame is what 'head to feet' means. Every other shot type MUST touch "
                     "it, because being cut off at the waist is what makes it a waist-up "
                     "shot. v1.246: the TOP edge is checked only on `upper` and `full` — "
                     "measured, a face crop and most headshots fill the frame top to "
                     "bottom, and that is the shot rather than a defect. A missing model "
                     "is a degraded mode: crop goes back to unchecked and the summary "
                     "says so.")}


def box(path: str | Path) -> Optional[Dict[str, Any]]:
    """The subject's bounding box as fractions of the frame, or None.

    None means "no trustworthy subject mask", which is a real answer and must
    never be treated as a failure."""
    p = Path(path)
    try:
        st = p.stat()
        key = (str(p), st.st_mtime, st.st_size)
    except OSError:
        return None
    if key in _CACHE:
        return _CACHE[key]
    sess = _session()
    if sess is None:
        return None
    out: Optional[Dict[str, Any]] = None
    try:
        import numpy as np
        from PIL import Image
        from rembg import remove
        img = Image.open(p).convert("RGB")
        W, H = img.size
        cut = remove(img, session=sess)
        alpha = np.array(cut.split()[-1])
        mask = alpha > 128
        cover = float(mask.mean())
        ys, xs = np.nonzero(mask)
        if ys.size == 0:
            out = None
        else:
            y1, y2 = int(ys.min()), int(ys.max())
            x1, x2 = int(xs.min()), int(xs.max())
            # Is this ONE subject? A mask scattered across several blobs is a
            # segmentation that found the furniture as well as the man, and its
            # bounding box means nothing.
            dominance = 1.0
            try:
                import cv2
                n, labels, stats, _ = cv2.connectedComponentsWithStats(
                    mask.astype("uint8"), connectivity=8)
                if n > 1:
                    areas = sorted(stats[1:, cv2.CC_STAT_AREA], reverse=True)
                    dominance = float(areas[0]) / float(sum(areas)) if areas else 1.0
            except Exception:  # noqa: BLE001 — the box still stands without it
                pass
            out = {
                "coverage": round(cover, 4),
                "dominance": round(dominance, 3),
                "x1": round(x1 / W, 4), "x2": round(x2 / W, 4),
                "y1": round(y1 / H, 4), "y2": round(y2 / H, 4),
                "body_h_ratio": round((y2 - y1 + 1) / H, 4),
                "body_w_ratio": round((x2 - x1 + 1) / W, 4),
                "touches_top": bool(y1 / H <= EDGE_TOL),
                "touches_bottom": bool(y2 / H >= 1.0 - EDGE_TOL),
                "touches_left": bool(x1 / W <= EDGE_TOL),
                "touches_right": bool(x2 / W >= 1.0 - EDGE_TOL),
                "img_w": W, "img_h": H,
            }
            out["trustworthy"] = bool(
                MIN_COVERAGE <= cover <= MAX_COVERAGE and dominance >= MIN_DOMINANCE)
    except Exception as e:  # noqa: BLE001
        logger.warning("subject: mask failed for %s: %s", p.name, e)
        out = None
    with _LOCK:
        if len(_CACHE) >= _CACHE_MAX:
            _CACHE.clear()
        _CACHE[key] = out
    return out


def crop_verdict(framing_key: str, bx: Optional[Dict[str, Any]]
                 ) -> Tuple[Optional[bool], str]:
    """(ok, human sentence) for whether the subject is cut off wrongly.

    `None` is UNMEASURED and never fails an image: no model, no mask, or a mask
    that does not look like one subject."""
    fkey = str(framing_key or "").lower()
    if bx is None:
        return None, "no subject mask — crop not checked"
    if not bx.get("trustworthy"):
        return None, (f"the subject mask does not look like one person "
                      f"(covers {bx['coverage'] * 100:.0f}% of the frame, largest region "
                      f"{bx['dominance'] * 100:.0f}% of it) — crop not checked")
    if fkey not in CUT_AT_BOTTOM:
        return None, f"no crop rule for shot type '{fkey}'"

    problems: List[str] = []
    if CHECK_TOP.get(fkey, True) and bx["touches_top"]:
        problems.append("the top of his head runs off the top of the frame")
    if CUT_AT_BOTTOM[fkey]:
        # A waist-up or closer shot is CUT OFF at the bottom by design.
        if not bx["touches_bottom"]:
            return False, (f"there is clear space below him ({(1 - bx['y2']) * 100:.0f}% "
                           f"of the frame), so this is not a {fkey} shot — a {fkey} shot "
                           f"is cut off at the bottom edge")
    else:
        if bx["touches_bottom"]:
            problems.append("his feet run off the bottom of the frame")
    if problems:
        return False, " and ".join(problems)
    if fkey == "full":
        return True, (f"whole subject inside the frame — {bx['body_h_ratio'] * 100:.0f}% "
                      f"of the height, {(1 - bx['y2']) * 100:.0f}% clear below his feet")
    if not CHECK_TOP.get(fkey, True):
        return True, (f"correctly cut off at the bottom for a {fkey} shot "
                      f"(a close-up filling the frame is correct, so the top edge is "
                      f"not checked here)")
    return True, f"correctly cut off at the bottom for a {fkey} shot, head inside the frame"


def summarise(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Per-shot-type body-box stats, for calibrating and for spotting drift.

    `body_h_ratio` is the second half of the upper/full separation problem: a
    full body and a waist-up shot occupy the frame differently even when their
    face sizes are close."""
    by: Dict[str, Any] = {}
    for r in rows:
        b = by.setdefault(str(r.get("framing")), {"n": 0, "ok": 0, "miss": 0,
                                                  "unmeasured": 0, "_h": []})
        b["n"] += 1
        v = r.get("crop_ok")
        b["ok" if v is True else ("miss" if v is False else "unmeasured")] += 1
        if r.get("body_h_ratio") is not None:
            b["_h"].append(float(r["body_h_ratio"]))
    for b in by.values():
        hs = sorted(b.pop("_h"))
        b["body_h_median"] = None if not hs else round(hs[len(hs) // 2], 4)
        b["body_h_min"] = None if not hs else round(hs[0], 4)
        b["body_h_max"] = None if not hs else round(hs[-1], 4)
    return by
