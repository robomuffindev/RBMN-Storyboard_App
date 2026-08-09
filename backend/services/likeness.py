"""ArcFace identity scoring — the objective half of dataset QC.

v1.218.0 (2026-08-05)

WHY THIS EXISTS
    v1.212 added an identity check by asking a vision LLM "is this the same
    person, score 0-1".  v1.213 then wrote those numbers into
    `fizgig_look_scores.json` using Fizgig's cutoff formula --
    `max(median - 1.5*IQR, 0.25)` -- WITHOUT checking that our numbers were on
    their scale.  They are not.  That 0.25 is an ArcFace cosine value; an LLM
    asked to rate identity 0-1 clusters at 0.85-0.95 for anything it likes.  So
    the floor was unreachable, the IQR fence barely moved on a tight cluster,
    and the file we shipped was very nearly inert.

    A vision LLM is genuinely good at "is the framing right, is the expression
    right, are there artifacts, is he wearing the right coat".  It is weak at
    "is this the same face".  A face-recognition model is excellent at the
    second and useless for the first.  So both run, and each answers only what
    it is good at.

MEASURED, NOT ASSUMED (buffalo_l, CPU, 2026-08-05, insightface 1.0.1)
    Against insightface's own bundled sample images:
      * DIFFERENT people -- 15 pairs from one group photo:
            min -0.083 · median +0.026 · max +0.213
            0 of 15 cleared Fizgig's 0.25 "different person" floor.
      * SAME face, varied capture (downscale 40%, brightness x0.6 and x1.5,
        contrast x0.5, greyscale, mirrored, rotated 12 deg):
            worst +0.915, best +1.000
    Those same-person numbers are transformations of ONE photograph, so they are
    an upper bound.  Fizgig's stated 0.30-0.70 band is for genuinely different
    photographs -- different pose, light, expression, age.  Our datasets sit in
    between: every image is rendered from the same base, so scores should land
    HIGH.  That is a prediction, not a measurement -- `POST /datasets/{id}
    /likeness` exists to check it on real data before anyone trusts a threshold.

Everything here is CPU-only and lazy: no GPU contention with a render or a
training run, and an app that never scores a face never loads a model.
"""
from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ── Fizgig's bands (lora_trainer_gui.py `_ff_verdict`), quoted ───────────────
#   "Thresholds are ArcFace cosine-sim conventions: same person across varied
#    photos usually lands 0.30-0.70 vs a single baseline; a different person
#    rarely clears 0.25."
ARC_DIFFERENT = 0.25     # below this it is not him -- hard flag
ARC_BORDERLINE = 0.30    # 0.25-0.45 is "same person territory, but drifting"
ARC_MATCH = 0.45         # at or above: solid match

_LOCK = threading.Lock()
_APP: Any = None
_STATE: Dict[str, Any] = {"tried": False, "error": None}
_CACHE: Dict[Tuple[str, float, int], Optional[Any]] = {}
_POSE_CACHE: Dict[Tuple[str, float, int], Optional[Dict[str, Any]]] = {}
_CACHE_MAX = 4096

# ── v1.234: head yaw bands, measured not guessed ─────────────────────────────
# Degrees of head yaw.  NEGATIVE = his nose toward the LEFT edge of the picture,
# established from 8 profile renders (left -60.9..-78.3, right +56.5..+82.5).
# The lower profile bound is 50 rather than 56 so a slightly under-turned
# profile is not called a miss; the three-quarter floor is 20 because that is
# where the measured front cluster ends and a turn a HUMAN calls a turn begins.
#
# That last floor is the one judgement call here, so it is stated plainly.  I
# looked at five three-quarter renders BEFORE measuring them, and the split
# lands between them: yaw -18.7, -17.7 and +4.3 all read as "facing front" to
# the eye; +21.3 reads as turned.  20 is the only number consistent with that,
# and the ground truth behind it is FIVE images, not fifty.  The band is for
# spotting regressions; the MEDIAN yaw per variant is the real metric, and it
# does not move when this line does.
ANGLE_BANDS: Dict[str, Tuple[Optional[float], Optional[float]]] = {
    "front": (-15.0, 15.0),
    "three_quarter_left": (-55.0, -20.0),
    "three_quarter_right": (20.0, 55.0),
    "profile_left": (None, -50.0),
    "profile_right": (50.0, None),
    # "back" is deliberately absent: the correct measurement for a back shot is
    # NO FACE, which is an absence, not a number.  See `angle_verdict`.
}
# v1.238: det is a WEAK floor, not the guard.  The v1.234 floor of 0.65 was the
# wrong lesson from the right observation — see this version's docstring.  0.50
# only excludes detections too faint to be a face at all.
DET_MIN = 0.50

# The guard that actually works: do the two independent measures AGREE?
# `yaw / kps_yaw` over every face measured to date lands between 10.4 and 52.2,
# across profiles and three-quarters alike.  The one image whose 3D fit was
# demonstrably wrong sits at 3.8.  The 8..70 fence is that gap, with room.
RATIO_MIN, RATIO_MAX = 8.0, 70.0
# Below this much yaw both numbers are near zero and their ratio is noise, so
# the consistency check is skipped and only the det floor applies.
RATIO_FLOOR_DEG = 8.0

# ── v1.242: framing, as the share of the image height the face occupies ──────
# (low, high] in fractions of image height.  Measured ranges, then the geometric
# midpoint of each gap:  face 64.4-71.4 · headshot 29.6-51.0 · upper 14.4-23.0 ·
# full 9.2-12.2.  The upper/full fence has only 2.2 points of air between the
# two clusters — the tightest of the three, and the one to re-check first if a
# new character or a new canvas size ever moves these.
FRAMING_BANDS: Dict[str, Tuple[Optional[float], Optional[float]]] = {
    "face": (0.57, None),
    "headshot": (0.26, 0.57),
    "upper": (0.132, 0.26),
    "full": (None, 0.132),
}
# No shot type we generate puts the face in the bottom of the frame.  Measured
# across 36 detected faces: 16.6% to 49.7%, and then one at 86.6% whose pose fit
# was rejected for disagreeing with its own keypoints.  The image was the
# problem, not the fit.
FACE_CY_MAX = 0.60

# ── v1.243: relative calibration ─────────────────────────────────────────────
# How far an image may sit from the median face height of its OWN shot type in
# its OWN dataset, as a ratio either way.  Measured: the widest real deviation
# across 36 faces is 1.59x (one headshot); a full-body row rendering as a
# headshot is 4.1x out.  2.0 sits in that gap.
FRAMING_REL_TOL = 2.0
# Below this many measurable images in a shot type, its median is not worth
# trusting and the absolute bands are used instead.
FRAMING_MIN_SAMPLES = 4
# Tightest to widest.  Their MEDIANS must come out in this order in any dataset
# whose shot types are actually different shots.
FRAMING_ORDER = ("face", "headshot", "upper", "full")


def _app():
    """Load buffalo_l once, on first use.  A missing dependency is a DEGRADED
    mode, never an exception: dataset QC still works without it, just without an
    objective likeness number."""
    global _APP
    if _APP is not None or _STATE["tried"]:
        return _APP
    with _LOCK:
        if _APP is not None or _STATE["tried"]:
            return _APP
        _STATE["tried"] = True
        try:
            from insightface.app import FaceAnalysis
            app = FaceAnalysis(name="buffalo_l", providers=["CPUExecutionProvider"])
            # ctx_id=-1 is CPU.  Deliberate: this must never queue behind a
            # render or steal VRAM from a training run.
            app.prepare(ctx_id=-1, det_size=(640, 640))
            _APP = app
            logger.info("likeness: buffalo_l ready (CPU)")
        except Exception as e:  # noqa: BLE001
            _STATE["error"] = f"{type(e).__name__}: {e}"
            logger.warning("likeness: unavailable (%s) — QC falls back to the "
                           "vision model's own identity judgement", _STATE["error"])
    return _APP


def available() -> bool:
    return _app() is not None


def health() -> Dict[str, Any]:
    ok = available()
    return {"available": ok,
            "model": "buffalo_l (ArcFace w600k_r50, CPU)" if ok else None,
            "error": None if ok else (_STATE.get("error") or "not loaded"),
            "install": None if ok else "pip install insightface onnxruntime",
            "bands": {"different_below": ARC_DIFFERENT, "borderline_below": ARC_MATCH,
                      "match_at_or_above": ARC_MATCH},
            "note": ("A missing model is a degraded mode, not a failure — QC still runs and "
                     "falls back to the vision model's own identity judgement, which is "
                     "subjective and must NOT be written into fizgig_look_scores.json.")}


def embed(path: str | Path) -> Optional[Any]:
    """L2-normalised 512-d ArcFace embedding of the LARGEST face, or None.

    None means "no face found", which is a real and correct answer for a back
    shot -- it must never be conflated with a score of 0.0, which means "a
    different person".  Fizgig makes the same distinction and never auto-excludes
    an unscoreable row."""
    p = Path(path)
    try:
        st = p.stat()
        key = (str(p), st.st_mtime, st.st_size)
    except OSError:
        return None
    if key in _CACHE:
        return _CACHE[key]
    app = _app()
    if app is None:
        return None
    try:
        import cv2
        img = cv2.imread(str(p))
        if img is None:
            return None
        faces = app.get(img)
        if not faces:
            out = None
        else:
            big = max(faces, key=lambda f: (f.bbox[2] - f.bbox[0]) * (f.bbox[3] - f.bbox[1]))
            out = getattr(big, "normed_embedding", None)
    except Exception as e:  # noqa: BLE001
        logger.warning("likeness: embed failed for %s: %s", p.name, e)
        out = None
    with _LOCK:
        if len(_CACHE) >= _CACHE_MAX:
            _CACHE.clear()
        _CACHE[key] = out
    return out


def pose(path: str | Path) -> Optional[Dict[str, Any]]:
    """Head pose of the LARGEST face, or None when there is no face.

    None is a real answer, not a failure — a back shot has no face, and that is
    exactly how a back shot is verified.  Callers must not conflate it with a
    yaw of 0.

    `kps_yaw` is a second, independent estimate taken straight from the
    detector's five keypoints: how far the nose sits from the midpoint of the
    two eyes, as a fraction of half the eye span.  It needs no 3D model, so it
    cannot fail in the same way `pose` can, which is the point of carrying it.
    """
    p = Path(path)
    try:
        st = p.stat()
        key = (str(p), st.st_mtime, st.st_size)
    except OSError:
        return None
    if key in _POSE_CACHE:
        return _POSE_CACHE[key]
    app = _app()
    if app is None:
        return None
    out: Optional[Dict[str, Any]] = None
    try:
        import cv2
        img = cv2.imread(str(p))
        if img is None:
            return None
        faces = app.get(img)
        if faces:
            f = max(faces, key=lambda x: (x.bbox[2] - x.bbox[0]) * (x.bbox[3] - x.bbox[1]))
            out = {"faces": len(faces), "yaw": None, "pitch": None, "roll": None,
                   "kps_yaw": None,
                   "det_score": round(float(getattr(f, "det_score", 0.0)), 3)}
            # v1.242: where the face sits and how big it is. Free — the box is
            # already in hand — and it is the whole basis of the framing check.
            try:
                _h, _w = img.shape[:2]
                _x1, _y1, _x2, _y2 = (float(v) for v in f.bbox)
                out["img_w"], out["img_h"] = int(_w), int(_h)
                out["face_h_ratio"] = round((_y2 - _y1) / _h, 4)
                out["face_w_ratio"] = round((_x2 - _x1) / _w, 4)
                out["head_top"] = round(_y1 / _h, 4)
                out["face_cy"] = round(((_y1 + _y2) / 2) / _h, 4)
                # v1.275.7: the horizontal centre was the one number missing, so
                # nothing could CROP to the face — only describe it. The face
                # crop anchor (klein3 `_face_crop_ref`) needs it.
                out["face_cx"] = round(((_x1 + _x2) / 2) / _w, 4)
            except Exception:  # noqa: BLE001 — geometry is a bonus, never fatal
                pass
            pv = getattr(f, "pose", None)
            if pv is not None:
                out["pitch"] = round(float(pv[0]), 1)
                out["yaw"] = round(float(pv[1]), 1)
                out["roll"] = round(float(pv[2]), 1)
            kps = getattr(f, "kps", None)
            if kps is not None and len(kps) >= 3:
                le, re_, nose = kps[0], kps[1], kps[2]
                span = float(re_[0]) - float(le[0])
                if abs(span) > 1e-3:
                    mid = (float(le[0]) + float(re_[0])) / 2.0
                    out["kps_yaw"] = round((float(nose[0]) - mid) / (span / 2.0), 3)
    except Exception as e:  # noqa: BLE001
        logger.warning("likeness: pose failed for %s: %s", p.name, e)
        out = None
    with _LOCK:
        if len(_POSE_CACHE) >= _CACHE_MAX:
            _POSE_CACHE.clear()
        _POSE_CACHE[key] = out
    return out


def angle_confident(pv: Optional[Dict[str, Any]]) -> bool:
    """Is this pose worth acting on?

    v1.238: the test is whether the two independent measures AGREE, not how
    confident the detector was.  A pose is rejected when the face is too faint
    to be a face, when the 3D fit and the keypoints point OPPOSITE ways, or when
    they disagree about HOW FAR the head is turned.  The last of those is what
    actually caught the one bad fit in 56 images; the det floor caught it by
    coincidence and threw away a good measurement doing it."""
    if not pv or pv.get("yaw") is None:
        return False
    if (pv.get("det_score") or 0.0) < DET_MIN:
        return False
    y, k = pv.get("yaw"), pv.get("kps_yaw")
    if k is None or abs(y) <= RATIO_FLOOR_DEG:
        # Nothing to cross-check against, or too near front for the ratio to
        # mean anything.  Not a reason to reject.
        return True
    if (y > 0) != (k > 0):
        return False
    if abs(k) < 1e-6:
        return False
    ratio = abs(y) / abs(k)
    return RATIO_MIN <= ratio <= RATIO_MAX


def angle_verdict(angle_key: str, pv: Optional[Dict[str, Any]]
                  ) -> Tuple[Optional[bool], str]:
    """(ok, human sentence) for a planned angle against a measured pose.

    `None` for ok means UNMEASURED — no face, no pose model, or a fit not worth
    trusting.  It must never be rendered as a failure; an unmeasured row is one
    a human should look at, not one the repair loop should re-render."""
    key = str(angle_key or "").lower()
    if key == "back":
        # The whole verification is the absence.  A face found on a back row
        # means he turned round, which is the one way a back shot fails.
        if pv is None:
            return True, "no face found — correct for a back shot"
        return False, (f"a face is visible (yaw {pv.get('yaw')}), so he is not "
                       f"facing away from the camera")
    if pv is None:
        return None, "no face found, so the angle could not be measured"
    if not angle_confident(pv):
        # Say WHICH check failed.  "not trustworthy" with three numbers after it
        # made me tune the wrong one for four versions.
        _y, _k = pv.get("yaw"), pv.get("kps_yaw")
        if (pv.get("det_score") or 0.0) < DET_MIN:
            _why = f"the face is too faint to measure (det {pv.get('det_score')})"
        elif _k is not None and _y is not None and abs(_y) > RATIO_FLOOR_DEG \
                and (_y > 0) != (_k > 0):
            _why = (f"the 3D fit and the keypoints point opposite ways "
                    f"(yaw {_y}, keypoints {_k})")
        else:
            _r = (abs(_y) / abs(_k)) if (_k and abs(_k) > 1e-6) else None
            _why = (f"the 3D fit and the keypoints disagree about how far he is "
                    f"turned (yaw {_y}, keypoints {_k}"
                    + (f", ratio {_r:.1f} outside {RATIO_MIN:.0f}-{RATIO_MAX:.0f}"
                       if _r is not None else "") + ")")
        return None, f"face found but {_why}"
    band = ANGLE_BANDS.get(key)
    if band is None:
        return None, f"no band defined for angle '{key}'"
    lo, hi = band
    y = float(pv["yaw"])
    if (lo is None or y >= lo) and (hi is None or y <= hi):
        return True, f"yaw {y:+.1f} deg, inside {_band_text(key)}"
    return False, f"yaw {y:+.1f} deg, wanted {_band_text(key)}"


def _band_text(key: str) -> str:
    lo, hi = ANGLE_BANDS[key]
    if lo is None:
        return f"{hi:+.0f} or further left"
    if hi is None:
        return f"{lo:+.0f} or further right"
    return f"{lo:+.0f} to {hi:+.0f}"


def framing_calibrate(samples: List[Tuple[str, float]]) -> Dict[str, Any]:
    """Turn one dataset's own measurements into that dataset's own bands.

    `samples` is [(framing_key, face_h_ratio), ...] for every image where a face
    was found.  Returns the median per shot type, how many it rests on, and any
    dataset-level problem worth saying out loud.

    v1.243: this exists so no threshold is tuned to one character.  A bigger
    head, a longer canvas or a different model shifts every absolute number; a
    median taken from the dataset being judged shifts with them."""
    groups: Dict[str, List[float]] = {}
    for key, ratio in samples:
        k = str(key or "").lower()
        if ratio is None:
            continue
        groups.setdefault(k, []).append(float(ratio))
    medians: Dict[str, float] = {}
    counts: Dict[str, int] = {}
    for k, vals in groups.items():
        v = sorted(vals)
        counts[k] = len(v)
        medians[k] = round(v[len(v) // 2], 4)

    warnings: List[str] = []
    present = [k for k in FRAMING_ORDER if k in medians]
    order_ok = True
    for a, b in zip(present, present[1:]):
        if medians[a] <= medians[b]:
            order_ok = False
            warnings.append(
                f"{a} shots have a SMALLER median face ({medians[a] * 100:.1f}%) than "
                f"{b} shots ({medians[b] * 100:.1f}%) — those two shot types are not "
                f"coming out as different shots")
    # How much air is between adjacent shot types.  Thin separation is not an
    # error, but it is the thing that breaks first on a new character.
    separation: Dict[str, float] = {}
    for a, b in zip(present, present[1:]):
        if medians[b] > 0:
            separation[f"{a}/{b}"] = round(medians[a] / medians[b], 2)
            if separation[f"{a}/{b}"] < 1.4:
                warnings.append(
                    f"{a} and {b} medians are only {separation[f'{a}/{b}']}x apart — "
                    f"too close to tell those two shot types apart by face size")
    thin = [k for k in present if counts[k] < FRAMING_MIN_SAMPLES]
    if thin:
        warnings.append(
            f"too few measurable images to calibrate {', '.join(thin)} "
            f"(need {FRAMING_MIN_SAMPLES}) — those fall back to the default bands, "
            f"which were calibrated on ONE character")
    return {"medians": medians, "n": counts, "order_ok": order_ok,
            "separation": separation, "warnings": warnings,
            "tolerance": FRAMING_REL_TOL, "min_samples": FRAMING_MIN_SAMPLES}


def framing_verdict(framing_key: str, angle_key: str,
                    pv: Optional[Dict[str, Any]],
                    cal: Optional[Dict[str, Any]] = None) -> Tuple[Optional[bool], str]:
    """(ok, human sentence) for a planned shot type against the measured face.

    `None` means UNMEASURED and must never be rendered as a failure — for
    framing that is exactly one situation: a BACK row, where there is correctly
    no face to measure against.

    A missing face anywhere else is a FAILURE, not an absence.  A face crop with
    no face in it is broken, and calling that "unmeasurable" is how it survived
    three QC passes."""
    fkey = str(framing_key or "").lower()
    is_back = str(angle_key or "").lower() == "back"
    if pv is None or pv.get("face_h_ratio") is None:
        if is_back:
            return None, "no face to measure — correct for a back shot"
        return False, ("no face found at all, on a shot that should show one")
    r = float(pv["face_h_ratio"])
    cy = pv.get("face_cy")
    if cy is not None and float(cy) > FACE_CY_MAX:
        return False, (f"his face sits {float(cy) * 100:.0f}% of the way down the "
                       f"frame — every correct image in the set is above "
                       f"{FACE_CY_MAX * 100:.0f}%")
    # v1.243: the dataset's own median first, so nothing is tuned to one
    # character.  The absolute bands are the fallback, not the rule.
    med = (cal or {}).get("medians", {}).get(fkey)
    n = (cal or {}).get("n", {}).get(fkey, 0)
    if med and n >= FRAMING_MIN_SAMPLES and r > 0:
        dev = max(r / med, med / r)
        if dev <= FRAMING_REL_TOL:
            return True, (f"face fills {r * 100:.1f}% of the height, {dev:.2f}x from the "
                          f"{med * 100:.1f}% median for {fkey} shots in this set")
        bigger = r > med
        return False, (f"face fills {r * 100:.1f}% of the height — {dev:.1f}x "
                       f"{'BIGGER' if bigger else 'SMALLER'} than the {med * 100:.1f}% "
                       f"median for {fkey} shots in this set, so this is not the shot "
                       f"type that was asked for")

    band = FRAMING_BANDS.get(fkey)
    if band is None:
        return None, f"no band defined for shot type '{fkey}'"
    lo, hi = band
    _note = " (default bands — too few images in this set to calibrate)"
    if (lo is None or r > lo) and (hi is None or r <= hi):
        return True, f"face fills {r * 100:.1f}% of the height, right for {fkey}{_note}"
    # Say what it looks like instead, which is the actionable half.
    looks = next((k for k, (a, b) in FRAMING_BANDS.items()
                  if (a is None or r > a) and (b is None or r <= b)), None)
    return False, (f"face fills {r * 100:.1f}% of the height; {fkey} wants "
                   f"{_framing_text(fkey)}"
                   + (f", this looks like a {looks} shot" if looks else "") + _note)


def _framing_text(key: str) -> str:
    lo, hi = FRAMING_BANDS[key]
    if lo is None:
        return f"{hi * 100:.1f}% or less"
    if hi is None:
        return f"more than {lo * 100:.1f}%"
    return f"{lo * 100:.1f}-{hi * 100:.1f}%"


def framing_health() -> Dict[str, Any]:
    return {"available": available(),
            "method": ("each image against the median face height of its own shot type "
                       "in its own dataset"),
            "tolerance": FRAMING_REL_TOL,
            "min_samples": FRAMING_MIN_SAMPLES,
            "absolute_only": ["no face on a non-back row",
                              f"face below {FACE_CY_MAX * 100:.0f}% of the frame height"],
            "fallback_bands": {k: [v[0], v[1]] for k, v in FRAMING_BANDS.items()},
            "face_cy_max": FACE_CY_MAX,
            "measures": "face box height as a share of image height",
            "note": ("v1.243: the fallback bands were calibrated on ONE character and "
                     "are used only for a shot type with fewer than "
                     f"{FRAMING_MIN_SAMPLES} measurable images. Everything else is "
                     "judged relative to the dataset it belongs to, so a different "
                     "head size or canvas needs no re-tuning. The 2x fence cannot "
                     "separate `upper` from `full` — their medians sit 1.7x apart — "
                     "which needs a person mask, the same instrument crop is waiting "
                     "on.")}


def angle_health() -> Dict[str, Any]:
    """Can angles be measured at all?  `landmark_3d_68` ships inside buffalo_l,
    but a trimmed install can lack it, and the honest answer then is 'no'."""
    app = _app()
    mods = sorted((getattr(app, "models", {}) or {}).keys()) if app else []
    ok = "landmark_3d_68" in mods
    return {"available": ok, "modules": mods,
            "bands": {k: list(v) for k, v in ANGLE_BANDS.items()},
            "det_min": DET_MIN, "ratio_fence": [RATIO_MIN, RATIO_MAX],
            "sign": "negative yaw = his nose toward the LEFT edge of the picture",
            "error": None if ok else (
                "landmark_3d_68 not loaded — head pose unavailable, angle falls "
                "back to the vision model's subjective judgement")}


def cosine(a, b) -> Optional[float]:
    if a is None or b is None:
        return None
    import numpy as np
    return float(np.dot(a, b))


def score(path: str | Path, baselines: List[Any]) -> Optional[float]:
    """Similarity to the CENTROID of the baselines, as the mean of the per-
    baseline cosines.

    Fizgig averages three deliberately: "one photo's framing bias can't dominate
    the score".  A single baseline makes every image that happens to share its
    framing look more like him than it is."""
    if not baselines:
        return None
    e = embed(path)
    if e is None:
        return None
    import numpy as np
    vals = [float(np.dot(b, e)) for b in baselines if b is not None]
    return float(np.mean(vals)) if vals else None


def verdict(s: Optional[float]) -> Tuple[str, str]:
    """Fizgig's own labels and wording, so a number means the same thing in both
    tools."""
    if s is None:
        return ("no face", "No face detected — can't be scored. Back shots are fine.")
    if s >= ARC_MATCH:
        return ("match", "Solid match to the baseline look.")
    if s >= ARC_BORDERLINE:
        return ("borderline", "Same person territory, but drifting — worth an eyeball.")
    if s >= ARC_DIFFERENT:
        return ("weak", "Weak match — likely off-look (synthetic drift).")
    return ("not him", "Below the different-person floor — this is not the character.")


def cutoff(values: List[float]) -> Optional[float]:
    """Fizgig's IQR fence, `max(median - 1.5*(q3-q1), 0.25)`.

    Now that the inputs are ArcFace cosines, the 0.25 floor means what its
    author intended.  Under v1.213 it was fed LLM scores and was unreachable."""
    vals = sorted(v for v in values if isinstance(v, (int, float)))
    if len(vals) < 4:                 # their >= 4 guard
        return None
    n = len(vals)
    med, q1, q3 = vals[n // 2], vals[n // 4], vals[(3 * n) // 4]
    return max(med - 1.5 * (q3 - q1), ARC_DIFFERENT)


def distribution(scores: Dict[str, Optional[float]]) -> Dict[str, Any]:
    """A readable summary of a whole set, for checking that the numbers land
    where they should BEFORE anyone tunes a threshold against them."""
    vals = sorted(v for v in scores.values() if isinstance(v, (int, float)))
    n = len(vals)
    bands = {"match": 0, "borderline": 0, "weak": 0, "not him": 0, "no face": 0}
    for s in scores.values():
        bands[verdict(s)[0]] += 1
    out: Dict[str, Any] = {
        "scored": n, "no_face": sum(1 for v in scores.values() if v is None),
        "bands": bands, "cutoff": cutoff(vals),
        "below_cutoff": [], "worst": [],
    }
    if n:
        out.update({"min": round(vals[0], 4), "max": round(vals[-1], 4),
                    "median": round(vals[n // 2], 4),
                    "mean": round(sum(vals) / n, 4)})
        c = out["cutoff"]
        if c is not None:
            out["below_cutoff"] = sorted(k for k, v in scores.items()
                                         if isinstance(v, (int, float)) and v < c)
        out["worst"] = [{"name": k, "score": round(v, 4), "verdict": verdict(v)[0]}
                        for k, v in sorted(
                            ((k, v) for k, v in scores.items() if isinstance(v, (int, float))),
                            key=lambda kv: kv[1])[:5]]
        # Whether the numbers look like ArcFace at all. A whole set above 0.9 is
        # not proof of a good dataset — it can equally mean the baselines came
        # from the same renders being scored.
        if vals[n // 2] >= 0.9:
            out["sanity"] = ("median above 0.90 — very high even for renders off one base. "
                             "Check the baselines are the CHARACTER's references and not "
                             "images from this dataset.")
        elif vals[n // 2] < ARC_BORDERLINE:
            out["sanity"] = ("median below 0.30 — the set as a whole does not resemble the "
                             "baselines. Check the right character's references are loaded.")
        else:
            out["sanity"] = "median is in the expected same-person range."
    return out
