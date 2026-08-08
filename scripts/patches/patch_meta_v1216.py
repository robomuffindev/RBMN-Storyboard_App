"""v1.216 — VERSION, pyproject, CHANGELOG, docs for outfit sets."""
import sys
from pathlib import Path

ROOT = Path(sys.argv[1] if len(sys.argv) > 1 else ".")

v = ROOT / "VERSION"
assert v.read_text("utf-8").strip() == "1.215.0", v.read_text("utf-8")
v.write_text("1.216.0\n", "utf-8")

pp = ROOT / "pyproject.toml"
s = pp.read_text("utf-8")
assert s.count('version = "1.215.0"') == 1
pp.write_text(s.replace('version = "1.215.0"', 'version = "1.216.0"', 1), "utf-8")

ENTRY = '''## v1.216.0 -- outfit SETS (2026-08-04)

His question: shouldn't a character be trained in multiple clothing styles? He remembered this
from older LoRA practice. He is right, and **our code was contradicting our own documentation**
-- the module docstring has warned since v1.209 that "a narrow dataset bakes its own narrowness
in (all-bikini dataset -> every render is a bikini)", while `outfit` was ONE string applied to
all 40 images. One outfit, never varied, gets absorbed into the trigger word along with the
face. So the clothes become part of the character.

Outfits are now a SET, of two kinds doing two different jobs:
- **named** -- his actual story wardrobe. What the LoRA has to render well.
- **variety** -- looks that exist purely so clothing stays DETACHABLE from identity. Without
  them even three outfits can fuse, because nothing in the data demonstrates that clothing is
  independent of the person.
His choices: **60/40 named/variety**, variety **proposed by the vision model from the
character's own reference** (returned for review, never auto-applied), and the set **sized from
the wardrobe** (~13 images per outfit -> 3 named + 5 variety = 104, floor 24, cap 120).

**Garment reference images work, and were nearly free.** `REF_TAGS` already had `outfit`, and
`_run_klein_edit_on` already loads up to 5 refs against `KLEIN_EDIT_ULTRA_WORKFLOW_{n}REF.json`
-- so base + face + garment fits with room spare. The constraint is the standing rule that
**Klein ignores category words**: "the clothing in image 3" produces whatever it likes. So a
garment ref is always paired with NAMED garments -- `POST /characters/{slug}/refs/{id}/garment`
runs the vision model over the image and returns "a red plaid flannel shirt, dark blue jeans
and brown leather boots", and `_clean_garment_desc` REJECTS an answer with no garment noun in
it rather than passing a useless phrase through. The prompt then says "He is wearing <named
garments>, the exact garments shown in image 3" -- the citation corroborates the naming, it
never replaces it. And the ref only occupies a slot when the shot can actually show clothes.

**Visibility, same rule that fixed the back-shot expression bug:** never let a caption or a
prompt name something the image cannot contain. face -> no outfit at all; headshot -> the first
garment only; waist-up and full -> the whole thing. QC gets an `outfit_ok` key, but only for
shots where an outfit is visible, defaulting TRUE so a model that omits it cannot fail an image.
A wrong outfit now flags, and `outfit_off` joins the breakdown.

**DISTRIBUTION -- and the offline suite earned its keep twice here.** The list being right is
only half of it; how the outfits are dealt is the other half, and the first two attempts were
measurably wrong:
1. A plain round-robin down the plan clumped by FRAMING. `rows` is built grouped by framing, so
   the variety outfits exhausted part-way through the waist-up block and **five of eight never
   received a single full-body shot** -- a LoRA that learns "the navy hoodie means a waist-up
   photograph".
2. Allocating per framing group fixed that and clumped by ANGLE instead: an outfit lands every
   len(outfits) rows while the angle rotates every len(_ANGLE_MIX) rows, and those share a
   factor -- **one outfit came out 67% a single angle**, trained as "the red rain jacket, seen
   from the left". Same class of bug as the v1.209.1 angle clumping.
The shipped fill is greedy over (framing x angle) CELLS, **rarest cell first**, each slot going
to whichever outfit is furthest behind on that angle and then that framing. Rarest-first is the
half that matters: proportional allocation quietly hands small outfits their images out of the
BIG cells, because that is where the slots are, so the rare angles end up belonging to the
outfits with the most images. Measured on 104 images / 8 outfits: **every outfit now spans all
four framings**, and worst angle over-representation fell 2.48x -> 1.86x with no outfit above
47.6% on any single angle. Named outfits skew front (they get the face-bearing shots), variety
skew three-quarter -- an acceptable trade, and stated here rather than discovered later.

**A real regression the OLD suite caught, not this version's own.** `_outfit_for` returned None
for a row planned before v1.216, because those rows carry no outfit id -- so a legacy dataset
silently dropped its outfit from every caption and every prompt. v1.216's tests missed it (they
build rows with ids); `test_v1209` failed immediately. A single outfit now falls through to
every un-tagged row, which is exactly what the pre-v1.216 semantics were. This is the whole
argument for keeping the old suites runnable.

Also fixed while testing: `_clean_garment_desc` matched garment words as SUBSTRINGS, so "an
outfit suitable for winter" passed the check -- "suitable" contains "suit". Word boundaries now.

New: `GET/PUT /datasets/{id}/outfits` (with the split and the visibility map),
`POST /characters/{slug}/wardrobe`, `POST /characters/{slug}/refs/{ref_id}/garment`.
`_suggested_count`, `NAMED_SHARE`, `IMAGES_PER_OUTFIT`, `_OUTFIT_VIS` are the knobs.

Verified: `test_v1216.py` (63 checks, distribution measured rather than assumed) plus
v1209/v1210/v1213/v1214 all pass on the live file, md5 069799884ef840fa5dd7be180c052e1a.

**Not yet built: the UI.** The wardrobe editor, the "suggest outfits" button and the garment-ref
picker in LoraPanel are the next step -- until then the outfit set is reachable only by passing
`outfits` to POST /datasets or /plan.

'''
cl = ROOT / "CHANGELOG.md"
s = cl.read_text("utf-8")
assert s.startswith("## v1.215.0"), s[:40]
cl.write_text(ENTRY + s, "utf-8")

d = ROOT / "docs" / "LORA_DATASET.md"
s = d.read_text("utf-8")
DOC = '''
## Outfits (v1.216)

**One outfit across the whole set is a bug, not a default.** Anything a caption
never varies gets absorbed into the trigger word — so a single outfit trains the
clothes into the character. That is the "all-bikini dataset" failure this
document has warned about since v1.209.

Two kinds, doing two different jobs:

| kind | why it exists | share |
|---|---|---|
| `named` | the story wardrobe — what the LoRA has to render well | 60% |
| `variety` | proves clothing is INDEPENDENT of the person, so it stays controllable | 40% |

Drop the variety looks and you get a LoRA that is good at exactly your named
outfits and fights you on anything else.

Set size scales with the wardrobe — ~13 images per outfit (floor 24, cap 120).
3 named + 5 variety lands at 104. Splitting a fixed 40 across eight outfits
leaves five each, which is too thin for any of them to hold.

### Garment reference images

Klein loads up to 5 references, and `REF_TAGS` already has `outfit`. Tag a
reference image, then:

```
POST /characters/{slug}/refs/{ref_id}/garment
  -> {"desc": "a red plaid flannel shirt, dark blue jeans and brown leather boots"}
```

That naming step is **not optional**. Klein ignores category words, so "the
clothing in image 3" produces whatever it likes — the prompt has to name the
garments and cite the image as corroboration:

> He is wearing a red plaid flannel shirt, dark blue jeans and brown leather
> boots, the exact garments shown in image 3.

If the vision model answers with a category phrase ("casual wear"), the endpoint
**422s** rather than handing a useless string to the renderer.

### What each shot may say

| framing | outfit in prompt & caption |
|---|---|
| `face` | nothing — an extreme close-up shows no clothing |
| `headshot` | the first garment only (a collar or neckline is all that shows) |
| `upper` | the whole outfit |
| `full` | the whole outfit |

Same rule as expressions on back shots: never name what the image cannot
contain. A garment reference is also skipped on `face` rows, where it would only
compete with the identity references.

### Distribution

Outfits are dealt greedily over (framing x angle) **cells, rarest cell first**,
each slot going to whichever outfit is furthest behind on that angle then that
framing. This is not incidental — two simpler schemes were measurably wrong:

- round-robin down the plan → five of eight outfits got **no full-body shot at
  all** (the plan is grouped by framing, so small outfits ran out early)
- per-framing allocation → one outfit came out **67% a single angle** (the
  outfit cycle and the angle cycle share a factor)

Measured on 104 images / 8 outfits: every outfit spans all four framings, worst
angle over-representation 1.86x, nothing above 47.6% on one angle. Named outfits
skew front (they get the face-bearing shots); variety skew three-quarter.

`scripts/patches/test_v1216.py` measures all of this — if you change
`_ANGLE_MIX`, `FRAMING_PRESETS` or `NAMED_SHARE`, run it and read the numbers.

### API

```
GET  /datasets/{id}/outfits          split, visibility map, suggested size
PUT  /datasets/{id}/outfits          replace the wardrobe (does NOT re-plan)
POST /characters/{slug}/wardrobe     propose variety outfits — for REVIEW
POST /characters/{slug}/refs/{id}/garment    name the garments in a ref image
```

A dataset built before v1.216 keeps working: its single `outfit` string migrates
to one named outfit, and rows planned without an outfit id fall through to it.
'''
assert "## Outfits (v1.216)" not in s
d.write_text(s.rstrip() + "\n" + DOC, "utf-8")
print("VERSION 1.216.0 · pyproject · CHANGELOG · docs/LORA_DATASET.md")
