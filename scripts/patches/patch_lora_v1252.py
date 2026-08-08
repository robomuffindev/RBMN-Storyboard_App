"""v1.252 — every shot type gets the face reference.  Measured, not argued.

`scripts\\faceref_test.ps1` on redv1's twelve `upper` and `full` rows, rendered
both ways, five and a half minutes, nobody at the keyboard:

    face_ref = CLOSEUPS (the old behaviour)
      all      n=10   median 0.431   min 0.247   below match 6   NOT HIM 2
      upper    n=5    median 0.538   min 0.253   below match 2   NOT HIM 0
      full     n=5    median 0.261   min 0.247   below match 4   NOT HIM 2

    face_ref = ALWAYS
      all      n=10   median 0.526   min 0.485   below match 0   NOT HIM 0
      upper    n=5    median 0.526   min 0.512   below match 0   NOT HIM 0
      full     n=5    median 0.515   min 0.485   below match 0   NOT HIM 0

    median +0.095 · below the match line 6 -> 0 · scored as a different
    person 2 -> 0 · worst score 0.247 -> 0.485

The `full` rows are the whole story: **median 0.261 to 0.515, nearly doubled.**
Those are the rows where the base reference shows the face at a twelfth of the
frame height and the model was given nothing else to work from. Hand it the face
reference and the drift stops.

Nothing is traded away: `upper` held its median and lost both of its
below-match rows, and no row got worse.

So `FACE_REF_DEFAULT` becomes `always`. `closeups` and `never` stay selectable —
a character with a bad face reference is better off without one, and `never`
says so honestly rather than making it impossible.

One cost, stated: an extra reference is an extra image in every Klein edit, so
`upper` and `full` rows move from the 1REF/2REF workflows to 2REF/3REF. The A/B
above measures the whole cost — 12 rows took 130 seconds with the face reference
against 115 without, across both workers. Fifteen seconds for six images that
stop being a different person.
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


rep('''# Measured motivation: redv1's three worst identity scores (0.19, 0.20, 0.21)
# are all `upper` or `full` rows, which are exactly the ones denied it, and its
# base reference is a wide full-body shot where the face is a twelfth of the
# height. Default unchanged until `scripts\\faceref_test.ps1` says otherwise.
FACE_REF_MODES = ("closeups", "always", "never")
FACE_REF_DEFAULT = "closeups"''',
    '''# v1.252: MEASURED on redv1's 12 upper+full rows, rendered both ways:
#
#   closeups   median 0.431   min 0.247   below match 6   NOT HIM 2
#   always     median 0.526   min 0.485   below match 0   NOT HIM 0
#
# `full` rows alone went from a median of 0.261 to 0.515 — those are the rows
# whose base shows the face at a twelfth of the frame height, so the model had
# nothing to work from. Nothing was traded away: `upper` held its median and
# lost both below-match rows, and no row got worse.
#
# `closeups` and `never` stay selectable — a character with a poor face
# reference is better off without one, and `never` says so honestly.
FACE_REF_MODES = ("closeups", "always", "never")
FACE_REF_DEFAULT = "always"''',
    "default -> always")

assert src != orig
P.write_text(src, "utf-8")
print(f"patched {P}  ({len(orig)} -> {len(src)} bytes)")
