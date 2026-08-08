"""v1.242 — framing gets an instrument, and it immediately finds two bad images.

MEASURED (`framing_probe`, 40 images, face-box height as a share of image height)

    face      n=8    64.43% .. 71.38%   median 68.37%
    headshot  n=8    29.55% .. 50.99%   median 47.04%
    upper     n=12   14.36% .. 23.01%   median 19.92%
    full      n=12    9.16% .. 12.18%   median 11.54%

Four shot types, four non-overlapping ranges, in the right order.  The gaps:

    face   64.43   vs  headshot 50.99   ->  13.4 points
    headshot 29.55 vs  upper    23.01   ->   6.5 points
    upper  14.36   vs  full     12.18   ->   2.2 points

So the bands are the geometric midpoints of those gaps — 57%, 26%, 13.2% — which
is the right kind of midpoint for a quantity spanning 7x.  The upper/full fence
is the tight one and is stated as such: n=24 either side, 2.2 points of air.

AND THE PROBE FOUND TWO REAL DEFECTS the vision model never mentioned, in the
two rows it had been calling "unmeasurable":

    0021  upper, front   head_top 75.10%, face_cy 86.61%
          His face is in the BOTTOM SIXTH of the frame. Every other image in the
          dataset puts it between 16.6% and 49.7%. That is why its 3D pose fit
          disagreed with its keypoints — the fit was not wrong, the image was.

    0001  face, front    no face detected at all
          On a 1024x1024 FACE CROP, where the other seven rows all carry a face
          filling 64-71% of the height. A face crop with no face in it is not an
          unmeasurable image, it is a broken one.

So two more checks, both from geometry already in hand:
  * a face below 60% of the frame height is wrong for every shot type we make
  * no detectable face on a NON-BACK row is a failure, not an absence.  Back
    rows are the only place a missing face is correct, and all four of ours are
    back rows.
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


rep('''RATIO_FLOOR_DEG = 8.0''',
    '''RATIO_FLOOR_DEG = 8.0

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
FACE_CY_MAX = 0.60''',
    "framing bands")

rep('''            out = {"faces": len(faces), "yaw": None, "pitch": None, "roll": None,
                   "kps_yaw": None,
                   "det_score": round(float(getattr(f, "det_score", 0.0)), 3)}''',
    '''            out = {"faces": len(faces), "yaw": None, "pitch": None, "roll": None,
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
            except Exception:  # noqa: BLE001 — geometry is a bonus, never fatal
                pass''',
    "bbox geometry")

rep('''def angle_health() -> Dict[str, Any]:''',
    '''def framing_verdict(framing_key: str, angle_key: str,
                    pv: Optional[Dict[str, Any]]) -> Tuple[Optional[bool], str]:
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
    band = FRAMING_BANDS.get(fkey)
    if band is None:
        return None, f"no band defined for shot type '{fkey}'"
    lo, hi = band
    if (lo is None or r > lo) and (hi is None or r <= hi):
        return True, f"face fills {r * 100:.1f}% of the height, right for {fkey}"
    # Say what it looks like instead, which is the actionable half.
    looks = next((k for k, (a, b) in FRAMING_BANDS.items()
                  if (a is None or r > a) and (b is None or r <= b)), None)
    return False, (f"face fills {r * 100:.1f}% of the height; {fkey} wants "
                   f"{_framing_text(fkey)}"
                   + (f", this looks like a {looks} shot" if looks else ""))


def _framing_text(key: str) -> str:
    lo, hi = FRAMING_BANDS[key]
    if lo is None:
        return f"{hi * 100:.1f}% or less"
    if hi is None:
        return f"more than {lo * 100:.1f}%"
    return f"{lo * 100:.1f}-{hi * 100:.1f}%"


def framing_health() -> Dict[str, Any]:
    return {"available": available(),
            "bands": {k: [v[0], v[1]] for k, v in FRAMING_BANDS.items()},
            "face_cy_max": FACE_CY_MAX,
            "measures": "face box height as a share of image height",
            "note": ("Calibrated on 40 images of one character: face 64.4-71.4%, "
                     "headshot 29.6-51.0%, upper 14.4-23.0%, full 9.2-12.2%. The "
                     "upper/full fence has 2.2 points of air and is the one to "
                     "re-check first on a new character or canvas size.")}


def angle_health() -> Dict[str, Any]:''',
    "framing verdict")

assert src != orig
P.write_text(src, "utf-8")
print(f"patched {P}  ({len(orig)} -> {len(src)} bytes)")
