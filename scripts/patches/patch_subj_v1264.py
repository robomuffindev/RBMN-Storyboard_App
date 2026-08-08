"""v1.264 — the crop check was failing pictures that are correct.  Wrong model.

BACKEND ONLY.

After the wardrobe repair, a full QC pass failed five images on crop. I looked at
0006: an extreme close-up whose shirt fills the bottom fifth of the frame, edge
to edge. `subject.box()` said the subject stops 9% above the bottom edge. The
"clear space below him" is his shirt.

`u2net` segments the SALIENT OBJECT. A sunlit beige shirt against a warm brick
wall is not salient, so the mask clipped it. That is a property of the model, not
of the rule.

MEASURED, all 40 images of dorian-v1, both models, same rule
(`scripts\\mask_probe.py`):

    u2net              fails 5 of 40   (0003 0006 0015 0016 0025 — all false)
    u2net_human_seg    fails 0 of 40

    and the two agree on every other row, including all 12 full-body rows, whose
    y2 stays at 0.89-0.97 — so the bottom-edge separator between `upper` and
    `full`, which is the whole reason the mask is here, is unchanged.

v1.246 validated the RULE on redv1's twenty images and the rule was right. The
instrument under it was the part that had never been compared against anything.

Cost: `u2net_human_seg` is the same size (176MB), same CPU path, same speed.
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


rep('''            _SESSION = new_session("u2net")''',
    '''            # v1.264: NOT "u2net". That model segments the salient object and
            # loses a sunlit beige shirt against a warm brick wall, which failed
            # five correct images of dorian-v1 for "clear space below him" where
            # the space was his shirt. Measured, both models, all 40 images,
            # same rule: u2net 5 false failures, u2net_human_seg 0 — and they
            # agree on every full-body row, so the bottom-edge separator that
            # this mask exists for is unchanged. See scripts\\\\mask_probe.py.
            _SESSION = new_session(MODEL)''',
    "model swap")

rep('''_SESSION: Any = None''',
    '''# The segmentation model. Person-specific on purpose — see v1.264 below.
MODEL = "u2net_human_seg"

_SESSION: Any = None''',
    "model const")

rep('''    * `rembg` (u2net) segments the salient subject, not "a person".''',
    '''    * `rembg` (u2net_human_seg since v1.264) segments a PERSON. The plain
      `u2net` it used before segments the salient object, not "a person".''',
    "docstring")

assert src != orig
P.write_text(src, "utf-8")
print(f"patched {P}  ({len(orig)} -> {len(src)} bytes)")
