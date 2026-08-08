"""v1.228 — full-body shots are cropped 58% of the time.

MEASURED, from his dump — cropping is not spread across the set, it is one
framing:

    face        8 shots   0 cropped (  0%)   1024x1024
    headshot    8 shots   0 cropped (  0%)    896x1152
    upper      12 shots   1 cropped (  8%)    896x1152
    full       12 shots   7 cropped ( 58%)    832x1216   <--

And it accounts for most of what is left of `framing_off` (13): "part of the
subject that this shot type needs is cut off by the frame edge", six times, all
on `full` rows.

Two causes, both fixed here.

1. THE CANVAS IS TOO SHORT.  832x1216 is 1:1.46.  A standing figure with any
   headroom or footroom needs closer to 1:1.75, and every other framing in the
   table is already shaped for what it holds.  768x1344 is 1:1.75, is a standard
   bucket, and has 3% FEWER pixels — so it is not a quality or speed trade.

2. THE PROMPT ASKS FOR THE SUBJECT, NOT THE MARGINS.  "head to feet inside the
   frame, standing on the ground" describes the person; it never asks for space
   around him, so the model composes him flush to the edges and any drift crops.
   Now names the margins explicitly — and affirmatively, because Klein has no
   negative conditioning and runs at cfg=1, so "not cropped" would inject
   cropped.
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


rep('"a full body photograph of him, head to feet inside the frame, standing on the ground",',
    '''"a full body photograph of him, his whole figure from the top of his head to "
     "the soles of his shoes inside the frame, standing on the ground, with clear "
     "empty space above his head and below his feet, the camera far enough back "
     "that his whole body sits comfortably within the picture",''',
    "full: ask for the margins, not just the subject")

rep("(832, 1216)", "(768, 1344)", "full: 1:1.46 -> 1:1.75")

# The same failure applies to a waist-up shot, just far less often (1 of 12).
rep('"a medium photograph of him from the waist up",',
    '''"a medium photograph of him from the waist up, with clear empty space above "
     "his head and his arms inside the frame",''',
    "upper: same margin wording")

assert src != orig
P.write_text(src, "utf-8")
print(f"patched {P}  ({len(orig)} -> {len(src)} bytes)")
