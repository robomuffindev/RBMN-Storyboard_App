"""v1.246 — the top-edge rule was wrong for close shots.  The probe caught it.

MEASURED (`crop_probe`, redv1, 20 images)

    full      6 of 6 OK   subject 81-95% of the height, bottom edge at 94.9-97.4%
    upper     6 of 6 OK   all touch the bottom, tops at 3.6-19.0%
    headshot  1 of 4 OK   three "wrong": tops at 0.0%, 0.6%, 0.7%
    face      0 of 4 OK   all four "wrong": tops at 0.0%

Those seven are not defects.  A face crop is an extreme close-up FILLING the
frame — the subject covers 63-65% of the pixels and spans 100% of the height.
The top of the head running off the top of the picture is what that shot IS.
Same for a tight headshot: a crown grazing the frame edge is ordinary
portraiture, and 0005 passing at 2.9% while 0006 fails at 0.7% is a distinction
without a difference.

I wrote "every shot type wants the top of the head inside the frame" from an
armchair.  It is true for the two shot types where the whole body is the point
and false for the two where the face is.

    face, headshot   must touch the BOTTOM edge.  Top edge NOT CHECKED — filling
                     the frame is correct, and this instrument cannot tell a
                     correct tight crop from a sliced forehead.
    upper            must touch the bottom, must NOT touch the top.
    full             must NOT touch either.

With that rule the same 20 images read 20 of 20.

AND THE THING IT WAS BUILT FOR WORKS
    v1.243 recorded that face height could not separate `upper` from `full` —
    medians 1.7x apart against a 1.6x within-type spread.  Measured now:

        subject HEIGHT   upper 81-96%   vs   full 81-95%    — no separation
        bottom EDGE      upper 6 of 6 touching · full 0 of 6 touching — perfect

    The binary separates them exactly where the continuous measure could not,
    which is what it was for.

SO CROP IS PROMOTED
    It gates `ok` from here.  Not because the rule sounds right — the first
    version of this rule sounded right and was wrong for half the shot types —
    but because it has now been run against 20 real images across all four,
    disagreed with me, and was corrected by what the pictures actually contain.
"""
import sys
from pathlib import Path

P = Path(sys.argv[1] if len(sys.argv) > 1 else "backend/services/subject.py")
src = P.read_text("utf-8")
orig = src


def rep(old, new, why):
    global src
    n = src.count(old)
    assert n == 1, f"{why}: expected 1 match, found {n}"
    src = src.replace(old, new, 1)


rep('''# Which shot types are SUPPOSED to run off the bottom of the frame.
# `full` is the only one that is not — that is what "head to feet" means.
CUT_AT_BOTTOM = {"face": True, "headshot": True, "upper": True, "full": False}''',
    '''# Which shot types are SUPPOSED to run off the bottom of the frame.
# `full` is the only one that is not — that is what "head to feet" means.
CUT_AT_BOTTOM = {"face": True, "headshot": True, "upper": True, "full": False}

# v1.246: and which are supposed to keep the top of the head INSIDE the frame.
# Measured on 20 real images: all four `face` rows and three of four `headshot`
# rows have the subject touching the top edge, because an extreme close-up fills
# the frame — that is what the shot IS, not a defect. The distinction between a
# correct tight crop and a sliced forehead is not one this instrument can make,
# so on those two shot types it does not pretend to.
CHECK_TOP = {"face": False, "headshot": False, "upper": True, "full": True}''',
    "top rule")

rep('''    problems: List[str] = []
    if bx["touches_top"]:
        problems.append("the top of his head runs off the top of the frame")''',
    '''    problems: List[str] = []
    if CHECK_TOP.get(fkey, True) and bx["touches_top"]:
        problems.append("the top of his head runs off the top of the frame")''',
    "top check")

rep('''    if fkey == "full":
        return True, (f"whole subject inside the frame — {bx['body_h_ratio'] * 100:.0f}% "
                      f"of the height, {(1 - bx['y2']) * 100:.0f}% clear below his feet")
    return True, f"correctly cut off at the bottom for a {fkey} shot, head inside the frame"''',
    '''    if fkey == "full":
        return True, (f"whole subject inside the frame — {bx['body_h_ratio'] * 100:.0f}% "
                      f"of the height, {(1 - bx['y2']) * 100:.0f}% clear below his feet")
    if not CHECK_TOP.get(fkey, True):
        return True, (f"correctly cut off at the bottom for a {fkey} shot "
                      f"(a close-up filling the frame is correct, so the top edge is "
                      f"not checked here)")
    return True, f"correctly cut off at the bottom for a {fkey} shot, head inside the frame"''',
    "pass message")

rep('''            "edge_tolerance": EDGE_TOL,
            "cut_at_bottom": CUT_AT_BOTTOM,''',
    '''            "edge_tolerance": EDGE_TOL,
            "cut_at_bottom": CUT_AT_BOTTOM,
            "check_top": CHECK_TOP,''',
    "health")

rep('''            "note": ("A full-body shot must NOT touch the bottom edge — feet inside the "
                     "frame is what 'head to feet' means. Every other shot type MUST touch "
                     "it, because being cut off at the waist is what makes it a waist-up "
                     "shot. A missing model is a degraded mode: crop goes back to "
                     "unchecked and the summary says so.")}''',
    '''            "note": ("A full-body shot must NOT touch the bottom edge — feet inside the "
                     "frame is what 'head to feet' means. Every other shot type MUST touch "
                     "it, because being cut off at the waist is what makes it a waist-up "
                     "shot. v1.246: the TOP edge is checked only on `upper` and `full` — "
                     "measured, a face crop and most headshots fill the frame top to "
                     "bottom, and that is the shot rather than a defect. A missing model "
                     "is a degraded mode: crop goes back to unchecked and the summary "
                     "says so.")}''',
    "note")

assert src != orig
P.write_text(src, "utf-8")
print(f"patched {P}  ({len(orig)} -> {len(src)} bytes)")
