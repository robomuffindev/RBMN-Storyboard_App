"""v1.234 — head yaw, in degrees, so ANGLE stops being a matter of opinion.

WHY
    The vision model was the only judge of "is this the right angle".  Measured
    against 40 rendered images with objective head pose (below), its verdict is
    noise on three-quarter-left and ANTI-correlated on three-quarter-right:

        three_quarter_left   said OK  : yaw -22.1, -18.7, -17.7
                             said MISS: yaw -20.6, -20.1, -19.3, -8.1, +24.2
                             -> the two groups occupy the SAME angles

        three_quarter_right  said OK  : yaw  -6.1,  +4.3, +21.3   mean |yaw| 10.6
                             said MISS: yaw  -9.5,  -8.2, +27.7, +30.8, +36.8
                                                                  mean |yaw| 22.6
                             -> it failed the three best-turned images and
                                passed two that are dead front

    It disagreed with the measurement on 16 of 40.

MEASURED (buffalo_l `landmark_3d_68`, CPU, 40 images, dorian-v1-b1966f)
    The instrument validates itself on the rows whose angle is unambiguous:

        back           4 images   no face detected, 4 of 4     (correct: a back
                                  shot HAS no face -- that is the answer, not a
                                  failure to measure)
        front         12 images   yaw  +0.5 .. +5.4  (11 of 12; one outlier at
                                  +13.6 carries det_score 0.57 -- a bad fit)
        profile_left   4 images   yaw -60.9 .. -78.3
        profile_right  4 images   yaw +56.5 .. +82.5

    Sign is unambiguous and consistent: NEGATIVE yaw = his nose toward the LEFT
    edge of the picture.  A second, model-free estimate -- where the nose sits
    between the two detector eye keypoints -- tracks it across the whole range
    (front ~0.05, three-quarter ~0.4-1.0, profile 2.4-7.5) and never disagrees
    on sign.  Two independent measures, one conclusion.

WHAT THIS ADDS
    `pose(path)`          -- yaw / pitch / roll / kps_yaw / det_score, cached
    `angle_verdict(...)`  -- the band a planned angle has to land in
    `ANGLE_BANDS`         -- the bands themselves, in one place, as numbers

    Nothing here touches identity.  `pose` reuses the same loaded model and the
    same "a missing dependency is a degraded mode" contract as `embed`.
"""
import sys
from pathlib import Path

P = Path(sys.argv[1] if len(sys.argv) > 1 else "backend/services/likeness.py")
src = P.read_text("utf-8")
orig = src


def rep(old, new, why):
    global src
    n = src.count(old)
    assert n == 1, f"{why}: expected 1 match, found {n}"
    src = src.replace(old, new, 1)


rep('''_CACHE: Dict[Tuple[str, float, int], Optional[Any]] = {}
_CACHE_MAX = 4096''',
    '''_CACHE: Dict[Tuple[str, float, int], Optional[Any]] = {}
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
# Below this detector confidence the 3D fit is not worth arguing with.  Measured:
# the single front image whose yaw disagreed with the keypoint estimate (yaw
# +13.6 vs a keypoint ratio of 3.57, which elsewhere means a full profile)
# carries det_score 0.57; every image the two measures agreed on scores >= 0.65.
DET_MIN = 0.65''',
    "bands")

rep('''def cosine(a, b) -> Optional[float]:''',
    '''def pose(path: str | Path) -> Optional[Dict[str, Any]]:
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

    Two ways it is not: a weak detection, or the 3D fit and the keypoint
    estimate pointing opposite ways.  The second currently never fires on real
    data — every one of 36 detected faces agreed on sign — which is what makes
    it a guard rather than a source of noise."""
    if not pv or pv.get("yaw") is None:
        return False
    if (pv.get("det_score") or 0.0) < DET_MIN:
        return False
    y, k = pv.get("yaw"), pv.get("kps_yaw")
    if k is not None and abs(y) > 8 and (y > 0) != (k > 0):
        return False
    return True


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
        return None, (f"face found but the pose fit is not trustworthy "
                      f"(det {pv.get('det_score')}, yaw {pv.get('yaw')}, "
                      f"keypoints {pv.get('kps_yaw')})")
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


def angle_health() -> Dict[str, Any]:
    """Can angles be measured at all?  `landmark_3d_68` ships inside buffalo_l,
    but a trimmed install can lack it, and the honest answer then is 'no'."""
    app = _app()
    mods = sorted((getattr(app, "models", {}) or {}).keys()) if app else []
    ok = "landmark_3d_68" in mods
    return {"available": ok, "modules": mods,
            "bands": {k: list(v) for k, v in ANGLE_BANDS.items()},
            "det_min": DET_MIN,
            "sign": "negative yaw = his nose toward the LEFT edge of the picture",
            "error": None if ok else (
                "landmark_3d_68 not loaded — head pose unavailable, angle falls "
                "back to the vision model's subjective judgement")}


def cosine(a, b) -> Optional[float]:''',
    "pose + verdict")

assert src != orig
P.write_text(src, "utf-8")
print(f"patched {P}  ({len(orig)} -> {len(src)} bytes)")
