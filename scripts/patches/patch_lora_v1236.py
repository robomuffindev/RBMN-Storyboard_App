"""v1.236 — `halfway` becomes the default, because it won on the pictures too.

MEASURED (16 three-quarter rows rendered once per wording, head yaw)

    wording   in band 20-55   median |yaw|   max |yaw|   wrong way
    degrees        3 of 16         15.6         41.0         5
    tworef         4 of 16         11.4         31.7         1
    halfway       13 of 16         32.1         43.3         0
    frame         15 of 16         43.4         53.9         0

`frame` counts highest and is NOT the pick.  Two reasons, and the second is the
one that decides it:

  * Its median 43.4 and max 53.9 sit against the TOP of the band.  The dataset
    already carries 8 profile images at 56-82 degrees; the hole this whole
    exercise exists to fill is 22-56.  `halfway` lands at 25-43 and fills it.
    Counted on a textbook three-quarter window of 25-45 degrees rather than on
    the generous pass/fail band, it is halfway 13, frame 9, tworef 4, degrees 3.
  * I looked at them.  At -54 and +53 he is craning his neck to look sideways
    while his shoulders stay square — a near-profile face on a front-facing
    body.  At -35 and +28 it reads as an ordinary three-quarter portrait.
    Training on the first teaches a head-to-body relationship that is wrong.

Also worth saying plainly: in BOTH winning wordings the BODY stays square to the
camera and only the HEAD turns.  Head yaw is what a face LoRA learns from, so
this is the thing that needed fixing — but "three-quarter" here now means a
turned head, not a turned body.  Turning the body is a separate change and has
not been attempted.

And a caution that applies to every single-run number in this changelog: the
CONTROL was re-rendered with the same wording it had in v1.234 and moved from
7 of 16 to 3 of 16, wrong-way 4 to 5.  Different seeds, same prompt.  Run-to-run
noise on 16 images is worth about +/-4, which is why `frame` at 15 and `halfway`
at 13 are not meaningfully apart, and why the median and the pictures decided
this rather than the count.

`tworef` is the interesting failure: giving Klein the front base AND the side
base pulled it toward FRONT (median 11.4, four rows under 1 degree).  With two
references it averages them instead of interpolating between them.  Kept as an
option, recorded as a dead end.
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


rep('''TQ_DEFAULT = "degrees"''',
    '''# v1.236: measured, on 64 renders.  A dataset that never sets this now gets the
# wording that landed 13 of 16 three-quarter rows in a textbook 25-45 degree
# turn, against 3 of 16 for the sentence this replaces.  An explicit
# `tq_wording` in a dataset's options still wins — nothing already chosen moves.
TQ_DEFAULT = "halfway"

# The window a three-quarter turn SHOULD land in, as opposed to the wider band
# QC passes on.  Used for judging wordings against each other: a variant that
# pushes everything to 54 degrees scores well on a 20-55 band and is producing
# near-profiles.  Not used to fail an image.
TQ_TARGET = (25.0, 45.0)''',
    "default")

rep('''    return {"wording": w, "reads": sample,''',
    '''    return {"wording": w, "reads": sample, "default": TQ_DEFAULT,
            "target_window": list(TQ_TARGET),''',
    "route reports the target")

assert src != orig
P.write_text(src, "utf-8")
print(f"patched {P}  ({len(orig)} -> {len(src)} bytes)")
