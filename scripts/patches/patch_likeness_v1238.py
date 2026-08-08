"""v1.238 — the confidence guard was rejecting good measurements.

`auto` landed 13 of 16 in the target window.  One of the three that did not was
not a render failure at all:

    0007  three_quarter_left  yaw -40.9  keypoints -1.461  det 0.646  -> UNMEASURED

Both measures agree — sign and magnitude — and -40.9 is a textbook three-quarter
turn.  It was thrown away for a detector score three thousandths under the 0.65
floor I set in v1.234.

That floor was the wrong lesson drawn from the right observation.  The one image
whose measurements genuinely could not be trusted was:

    0021  front  yaw +13.6  keypoints +3.567  det 0.571

and what made it untrustworthy was not the det score — it was that the two
measures said different things: +13.6 degrees is nearly front, while a keypoint
ratio of 3.567 is what a full profile looks like.  det 0.571 was a coincidence
sitting next to the real signal.

MEASURED — yaw divided by keypoint ratio, over every face detected so far:

    profiles      10.4  18.8  18.9  19.2  21.6  22.4  23.6  25.0
    three-quarter 24.3  26.0  28.0  30.4  37.7  39.2  39.6  40.4  40.7  40.7
                  40.8  44.1  44.3  47.1  48.5  52.2
    0021           3.8   <- the only value outside 8..70, by a wide margin

So the guard becomes the CONSISTENCY of the two measures, with det demoted to a
weak floor.  0021 is still rejected, for the right reason.  0007 and the earlier
0007 (yaw +24.2, det 0.646, ratio 30.9) are now measured, because there was
never anything wrong with them.

The ratio is only meaningful once the head is actually turned — near zero yaw
both numbers are near zero and their ratio is noise — so below 8 degrees the
check is skipped and only the det floor applies.
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


rep('''# Below this detector confidence the 3D fit is not worth arguing with.  Measured:
# the single front image whose yaw disagreed with the keypoint estimate (yaw
# +13.6 vs a keypoint ratio of 3.57, which elsewhere means a full profile)
# carries det_score 0.57; every image the two measures agreed on scores >= 0.65.
DET_MIN = 0.65''',
    '''# v1.238: det is a WEAK floor, not the guard.  The v1.234 floor of 0.65 was the
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
RATIO_FLOOR_DEG = 8.0''',
    "det floor + ratio fence")

rep('''    Two ways it is not: a weak detection, or the 3D fit and the keypoint
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
    return True''',
    '''    v1.238: the test is whether the two independent measures AGREE, not how
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
    return RATIO_MIN <= ratio <= RATIO_MAX''',
    "guard")

rep('''    if not angle_confident(pv):
        return None, (f"face found but the pose fit is not trustworthy "
                      f"(det {pv.get('det_score')}, yaw {pv.get('yaw')}, "
                      f"keypoints {pv.get('kps_yaw')})")''',
    '''    if not angle_confident(pv):
        # Say WHICH check failed.  "not trustworthy" with three numbers after it
        # made me tune the wrong one for four versions.
        _y, _k = pv.get("yaw"), pv.get("kps_yaw")
        if (pv.get("det_score") or 0.0) < DET_MIN:
            _why = f"the face is too faint to measure (det {pv.get('det_score')})"
        elif _k is not None and _y is not None and abs(_y) > RATIO_FLOOR_DEG \\
                and (_y > 0) != (_k > 0):
            _why = (f"the 3D fit and the keypoints point opposite ways "
                    f"(yaw {_y}, keypoints {_k})")
        else:
            _r = (abs(_y) / abs(_k)) if (_k and abs(_k) > 1e-6) else None
            _why = (f"the 3D fit and the keypoints disagree about how far he is "
                    f"turned (yaw {_y}, keypoints {_k}"
                    + (f", ratio {_r:.1f} outside {RATIO_MIN:.0f}-{RATIO_MAX:.0f}"
                       if _r is not None else "") + ")")
        return None, f"face found but {_why}"''',
    "say which check failed")

rep('''    return {"available": ok, "modules": mods,
            "bands": {k: list(v) for k, v in ANGLE_BANDS.items()},
            "det_min": DET_MIN,''',
    '''    return {"available": ok, "modules": mods,
            "bands": {k: list(v) for k, v in ANGLE_BANDS.items()},
            "det_min": DET_MIN, "ratio_fence": [RATIO_MIN, RATIO_MAX],''',
    "health")

assert src != orig
P.write_text(src, "utf-8")
print(f"patched {P}  ({len(orig)} -> {len(src)} bytes)")
