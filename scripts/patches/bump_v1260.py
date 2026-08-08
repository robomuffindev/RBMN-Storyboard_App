"""v1.260 bump: VERSION, pyproject, CHANGELOG."""
import sys
from pathlib import Path

ROOT = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
OLD, NEW = "1.259.0", "1.260.0"

v = ROOT / "VERSION"
assert v.read_text("utf-8").strip() == OLD, v.read_text("utf-8")
v.write_text(NEW + "\n", "utf-8")

p = ROOT / "pyproject.toml"
s = p.read_text("utf-8")
assert s.count(f'version = "{OLD}"') == 1
p.write_text(s.replace(f'version = "{OLD}"', f'version = "{NEW}"', 1), "utf-8")

ENTRY = """## v1.260.0 -- a training set rendered a nude image and nothing noticed (2026-08-06)

Found by reading a caption Fizgig wrote. Its auto-recaptioner described row 0011 of dorian-v1 as
*"a man standing shirtless in a narrow street"*. I pulled the image. He is bare-chested, in a
street, in a set meant to teach a clothed character. Our own caption for that row said:

    "a close-up portrait, head and shoulders of rbmndorianv man, facing the camera,
     with a slight smile, in front of a city street, warm indoor lamp light."

The caption says nothing about clothing, so nothing contradicted anything. The image went into
training with no description of the most obvious thing in it.

**Root cause.** dorian-v1 has `base_mode: null` and `outfits: null`. A null `base_mode` fell
through to the CHARACTER's setting, which is `auto` -- v1.217's "newest version of that view
wins". dorian's newest front base is the STRIPPED one, made by the Strip SET tool. So every row
that resolved to a base rather than a tagged reference started from a nude image, and with no
outfit defined the render prompt said nothing about clothes either. 22 of 40 rows happened to use
a tagged reference and came out dressed; the rest were a coin flip.

v1.217 built the dressed/stripped toggle for exactly this and defaulted it to the character's
setting. For a CHARACTER that is right -- the Klein 3.0 panel is where stripping is chosen. For a
TRAINING SET it is not: a LoRA learns whatever is in the pixels, and "sometimes nude" is not a
thing anyone asked this dataset to teach.

**The fix.** `_ds_base_mode(ds)` -- a dataset with no explicit `base_mode` resolves to **dressed**.
`stripped` and `auto` remain available and are recorded when chosen. All five dataset-side
`_base_for_view` call sites now route through it, including the tworef slot-2 side base, which I
missed on the first pass and found by grepping every call site instead of trusting the four I
remembered.

**And it is now visible before a render, not after a training run.**
`GET /api/lora/datasets/{id}/identity-preview` says, per view, which file will be used, what its
label is, whether it looks stripped, and warns when a stripped base meets an empty wardrobe. The
same warnings are folded into the plan response via `_plan_warnings_identity`.

**What this does NOT fix.** Nothing measures whether a rendered person is dressed. `outfit_ok` is
the vision model and the vision model is not trusted (v1.241). This closes the CONFIGURATION hole
that caused it. The detection hole is still open and is recorded as such.

"""

c = ROOT / "CHANGELOG.md"
s = c.read_text("utf-8")
assert not s.startswith("## v1.260")
c.write_text(ENTRY + s, "utf-8")
print(f"bumped {OLD} -> {NEW}, changelog {len(ENTRY)} bytes prepended")
