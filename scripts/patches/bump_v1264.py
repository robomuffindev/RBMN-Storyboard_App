"""v1.264 bump."""
import sys
from pathlib import Path

ROOT = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
OLD, NEW = "1.263.0", "1.264.0"

v = ROOT / "VERSION"
assert v.read_text("utf-8").strip() == OLD, v.read_text("utf-8")
v.write_text(NEW + "\n", "utf-8")

p = ROOT / "pyproject.toml"
s = p.read_text("utf-8")
assert s.count(f'version = "{OLD}"') == 1
p.write_text(s.replace(f'version = "{OLD}"', f'version = "{NEW}"', 1), "utf-8")

ENTRY = """## v1.264.0 -- the crop check was failing correct pictures (2026-08-06)

Backend only. One word changed; the measurement behind it is the point.

After the wardrobe repair I re-ran a full QC pass so every row would be measured by every
instrument for the first time. Five images failed on crop. I pulled 0006 -- an extreme close-up
whose shirt fills the bottom fifth of the frame, edge to edge -- and `subject.box()` said the
subject stops 9% above the bottom edge. **The "clear space below him" is his shirt.**

`u2net` segments the SALIENT OBJECT. A sunlit beige shirt against a warm brick wall is not
salient, so the mask clipped it. `u2net_human_seg` is trained on people.

Measured, all 40 images, both models, the same rule (`scripts\\mask_probe.py`):

    u2net             fails 5 of 40    0003 0006 0015 0016 0025 -- every one a false failure
    u2net_human_seg   fails 0 of 40

    the two agree on every other row, including all 12 full-body rows, whose y2 stays at
    0.89-0.97 -- so the bottom-edge separator between `upper` and `full`, which is the entire
    reason the mask is here, is unchanged.

v1.246 validated the RULE on redv1's twenty images, and the rule was right both times. The
instrument underneath it was the part that had never been compared against anything -- the same
shape of gap as v1.213 (LLM confidence read as an ArcFace cosine) and v1.259 (loss read as
likeness). A rule can only be as good as what measures its inputs, and "it passed on the images I
had" is not the same as "it is right".

Two of those five (0015, 0025) had already been re-rendered six times each by the repair loop,
chasing a defect that was never in the picture.

Same model size (176MB), same CPU path, same speed.

"""

c = ROOT / "CHANGELOG.md"
s = c.read_text("utf-8")
assert not s.startswith("## v1.264")
c.write_text(ENTRY + s, "utf-8")
print(f"bumped {OLD} -> {NEW}")
