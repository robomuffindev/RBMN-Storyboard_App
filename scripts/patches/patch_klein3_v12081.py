"""v1.208.1 — hands land on the BODY PART, not the diagram's pixel position.

Lorenzo: "in some images his hand presses into his stomach instead of being on
his hips… like the pose is being taken too strictly and he isn't allowed to
place his hands more freely where they should be."  Exactly right: on a wider
body, hands-on-hips needs wider elbows and a longer reach than the mannequin's
arms travel, so copying the arm angles literally lands the hands on the belly.
The contact clause now ships in BRIEF as well as FULL and explicitly allows the
arm angles to adapt so the contact point is reached.

Exact-match patcher: every replacement asserts exactly ONE occurrence.
Usage: python patch_klein3_v12081.py <path-to-klein3.py>
"""
import sys
from pathlib import Path

p = Path(sys.argv[1])
src = p.read_text("utf-8")
orig = src


def rep(old: str, new: str, label: str) -> None:
    global src
    n = src.count(old)
    assert n == 1, f"{label}: expected 1 occurrence, found {n}"
    src = src.replace(old, new)
    print(f"  ok  {label}")


rep(
    '''_POSE_TEXT_BRIEF = " The pose, in words: {desc}."''',
    '''_POSE_TEXT_BRIEF = " The pose, in words: {desc}."
# v1.208.1: CONTACT beats geometry.  Image 2's arms are as long as image 2's
# body is wide; on a wider body the same arm angle puts the hand on the belly.
# Ships with brief AND full so it is always present when the pose is described.
_POSE_CONTACT = (
    " Where the pose puts a hand or a foot on the body, it lands on the named body part of HIS "
    "body: hands on the hips settle on his own hip bones at the sides of his waist, level with "
    "the top of his pelvis, with the fingers wrapping toward his back. His arms reach as far "
    "as they need to and his elbows swing as wide as they need to for his own width — the "
    "contact point is what matters, and the arm angle follows it."
)''',
    "_POSE_CONTACT",
)

rep(
    '''    if desc:
        prompt += _POSE_TEXT_BRIEF.format(desc=desc.rstrip(" ."))
        if mode == "full":
            prompt += _POSE_TEXT_FULL''',
    '''    if desc:
        prompt += _POSE_TEXT_BRIEF.format(desc=desc.rstrip(" ."))
        prompt += _POSE_CONTACT
        if mode == "full":
            prompt += _POSE_TEXT_FULL''',
    "contact clause in both modes",
)

assert src != orig
p.write_text(src, "utf-8")
print(f"patched {p} ({len(orig)} -> {len(src)} bytes)")
