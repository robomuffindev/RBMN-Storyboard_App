"""v1.263 bump."""
import sys
from pathlib import Path

ROOT = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
OLD, NEW = "1.262.0", "1.263.0"

v = ROOT / "VERSION"
assert v.read_text("utf-8").strip() == OLD, v.read_text("utf-8")
v.write_text(NEW + "\n", "utf-8")

p = ROOT / "pyproject.toml"
s = p.read_text("utf-8")
assert s.count(f'version = "{OLD}"') == 1
p.write_text(s.replace(f'version = "{OLD}"', f'version = "{NEW}"', 1), "utf-8")

ENTRY = """## v1.263.0 -- the caption named the park twice (2026-08-06)

Backend only.

v1.261 reused the observed description verbatim, and the description answers two questions
because that is what the enrichment prompt asks for -- clothing AND background. The plan already
owns the background, so the captions came out like this:

    "...in front of a park, flat overcast light, a light gray t-shirt,
     green grassy park with trees in background."

The park is in there twice, in two vocabularies. Whatever a caption names becomes a knob the
trainer can turn; naming one thing twice in two wordings is the sloppiest way to turn it.

`wardrobe.garment_clause()` keeps only the clauses that name something worn and drops the scene.
Measured on all 40 real descriptions from dorian-v1 before it shipped
(`scripts\\clause_probe.py`): **40 trimmed, 0 emptied.** An emptied row would be a caption that
loses the clothing it was supposed to gain, which is the failure that mattered.

The reuse also stopped clobbering hand edits: it now replaces `caption_extra` only when it is
empty, the raw sentence a previous run pasted in, or the clause already derived from it.

**Where dorian-v1 stands after v1.260-v1.263**

    bare rows        12 of 40  ->  0 of 40      (re-rendered, 2 repair rounds)
    flagged          12        ->  1            (0016, a crop complaint, not clothing)
    likeness median  0.6502    ->  0.6902
    below match      3         ->  2
    captions         template  ->  template + the garments actually visible

0017 is the one to look at: the old render was a slimmer stranger in grey boxer briefs, because
the stripped base was a different body. The new one is him, in the stained beige t-shirt, from
behind. The likeness gain and the wardrobe fix are the same fix.

"""

c = ROOT / "CHANGELOG.md"
s = c.read_text("utf-8")
assert not s.startswith("## v1.263")
c.write_text(ENTRY + s, "utf-8")
print(f"bumped {OLD} -> {NEW}")
