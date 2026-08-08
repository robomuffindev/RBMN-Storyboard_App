"""v1.221 — two findings from his first real ArcFace scan.

MEASURED, on 40 images of `dorian-v1`:

  identity by which BASE was used        angle misses, by planned angle
    front base   n=5   median 0.705        three_quarter_right   7/7   100%
    left base    n=14  median 0.477        three_quarter_left    7/7   100%
    right base   n=13  median 0.436        profile_left          3/7    43%
    back base    n=4   median 0.125        front                 2/7    29%
                                           back                  1/6    17%
                                           profile_right         1/6    17%

1. **Three-quarter rows fail 14 of 14, and the mapping says why.**  ANGLES sends
   `three_quarter_left` to the LEFT base and `three_quarter_right` to the RIGHT
   base — which are 90-degree PROFILES.  So every three-quarter row asks Klein to
   rotate a profile back to 45 degrees, and Klein preserves the reference's
   orientation: it lands on profile, and the checker correctly says "not a
   three-quarters view".  Profile rows, whose base already IS a profile, miss
   only 17-43%.  `tq_base` lets a three-quarter row start from the FRONT base
   instead — a 45-degree turn FROM front rather than 45 back from profile — and
   front is also the strongest identity base by a wide margin (0.705 vs 0.44).
   Default stays "side" so nothing changes under him; this is an A/B to run, not
   a conclusion to assume.

2. **Back rows were being failed for identity, and that is a false positive.**
   All three "not him" images were back-based.  The baselines are frontal (front
   base + face + a side), so a back shot's partial or invented face cannot be
   fairly compared against them — a low number there is GEOMETRY, not evidence
   the character is wrong.  Back rows keep their score (it is a real "how unusual
   is this look" signal, which is exactly what Fizgig's LR warm-up wants) but no
   longer FAIL the image on it.
"""
import sys
from pathlib import Path

P = Path(sys.argv[1] if len(sys.argv) > 1 else "backend/api/lora.py")
src = P.read_text("utf-8")
orig = src


def rep(old, new, why):
    global src
    n = src.count(old)
    assert n == 1, f"{why}: expected 1 match, found {n}"
    src = src.replace(old, new, 1)


# ── 1. three-quarter rows may start from the front base ────────────────────
rep('''def _plan_opts(ds: dict) -> dict:''',
    '''# v1.221: which base a THREE-QUARTER row starts from.
#   "side"  -- the 90-degree profile base for that side (pre-v1.221 behaviour)
#   "front" -- the front base, i.e. turn 45 degrees FROM front rather than 45
#              back from profile.  Measured motivation: 14/14 three-quarter rows
#              failed their angle check, while profile rows (whose base already
#              matches) missed only 17-43%.  Front is also the strongest identity
#              base measured (median 0.705 against 0.436-0.477 for the sides).
_TQ_ANGLES = ("three_quarter_left", "three_quarter_right")


def _base_view_for(angle_key: str, planned_view: str, tq_base: str) -> str:
    """The base VIEW a row should use.  Separated out so the choice is one
    testable function rather than an expression buried in the job builder."""
    if angle_key in _TQ_ANGLES and str(tq_base or "side").lower() == "front":
        return "front"
    return planned_view


def _plan_opts(ds: dict) -> dict:''',
    "tq base selector")

rep('''        base, src_label = _base_for_view(ds["char_slug"], char, ang[3],
                                         ds.get("base_mode"))''',
    '''        _view = _base_view_for(it["angle"], ang[3],
                               (ds.get("options") or {}).get("tq_base", "side"))
        base, src_label = _base_for_view(ds["char_slug"], char, _view,
                                         ds.get("base_mode"))''',
    "jobs: honour tq_base")

# ── 2. a back row's likeness is not an identity verdict ────────────────────
rep('''                if arc is not None:
                    flags["identity_verdict"] = _like.verdict(arc)[0]
                    # Only the different-person floor FAILS an image. "Borderline"
                    # is surfaced and left to him — throwing away a drifting-but-
                    # recognisable render costs a re-render for no certain gain.
                    flags["same_person"] = arc >= _like.ARC_DIFFERENT''',
    '''                if arc is not None:
                    flags["identity_verdict"] = _like.verdict(arc)[0]
                    # v1.221: a BACK row cannot be judged this way.  The
                    # baselines are frontal, so whatever sliver of face a back
                    # shot shows scores low by GEOMETRY — measured, all three
                    # "not him" images in his first real scan were back-based,
                    # median 0.125 against 0.44-0.71 everywhere else.  The score
                    # is kept (it is a real "unusual look" signal, and that is
                    # precisely what Fizgig's LR warm-up consumes) but it stops
                    # being a verdict on whether the character is right.
                    _is_back = item.get("angle") == "back"
                    flags["identity_scored_against_front"] = not _is_back
                    flags["same_person"] = (True if _is_back
                                            else arc >= _like.ARC_DIFFERENT)''',
    "qc: back rows exempt from identity failure")

rep('''                if arc is not None and arc < _like.ARC_MATCH:
                    issues = [f"likeness {flags['identity_verdict']} ({arc:.2f})"] + issues''',
    '''                if arc is not None and arc < _like.ARC_MATCH:
                    _tag = ("likeness {v} ({a:.2f})" if flags.get(
                        "identity_scored_against_front", True)
                        else "likeness {v} ({a:.2f}) — back shot, frontal baselines, "
                             "not an identity verdict")
                    issues = [_tag.format(v=flags["identity_verdict"], a=arc)] + issues''',
    "qc: label the back-row caveat")

# ── 3. the rescore route must agree with QC ────────────────────────────────
rep('''                if s is not None:
                    q["identity_verdict"] = _like.verdict(s)[0]
                    q["same_person"] = s >= _like.ARC_DIFFERENT''',
    '''                if s is not None:
                    q["identity_verdict"] = _like.verdict(s)[0]
                    _is_back = it.get("angle") == "back"
                    q["identity_scored_against_front"] = not _is_back
                    q["same_person"] = True if _is_back else s >= _like.ARC_DIFFERENT''',
    "likeness route: same back-row exemption")

# ── 4. the breakdown separates the two ─────────────────────────────────────
rep('''           "outfit_off": 0, "stuck": 0, "arcface_scored": 0, "no_face": 0,
           "top_issues": {}}''',
    '''           "outfit_off": 0, "stuck": 0, "arcface_scored": 0, "no_face": 0,
           "back_low_likeness": 0, "top_issues": {}}''',
    "summary: back-likeness key")

rep('''        if q.get("identity_method") == "arcface" and q.get("identity_score") is None:
            out["no_face"] += 1''',
    '''        if q.get("identity_method") == "arcface" and q.get("identity_score") is None:
            out["no_face"] += 1
        # Counted apart from identity_off: informative, never a failure.
        if (q.get("identity_scored_against_front") is False
                and isinstance(q.get("identity_score"), (int, float))
                and q["identity_score"] < _like.ARC_DIFFERENT):
            out["back_low_likeness"] += 1''',
    "summary: count back-row low likeness")

assert src != orig
P.write_text(src, "utf-8")
print(f"patched {P}  ({len(orig)} -> {len(src)} bytes)")
