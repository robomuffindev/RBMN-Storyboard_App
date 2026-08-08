"""v1.261 bump: VERSION, pyproject, CHANGELOG."""
import sys
from pathlib import Path

ROOT = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
OLD, NEW = "1.260.0", "1.261.0"

v = ROOT / "VERSION"
assert v.read_text("utf-8").strip() == OLD, v.read_text("utf-8")
v.write_text(NEW + "\n", "utf-8")

p = ROOT / "pyproject.toml"
s = p.read_text("utf-8")
assert s.count(f'version = "{OLD}"') == 1
p.write_text(s.replace(f'version = "{OLD}"', f'version = "{NEW}"', 1), "utf-8")

ENTRY = """## v1.261.0 -- twelve of the forty were in their underwear (2026-08-06)

Backend only: a new service module, one new route, an export gate, and a shorter default
training run. No UI yet.

v1.260 closed the configuration hole and said plainly that nothing measured whether a rendered
person was actually dressed. So I measured it. All 40 rendered images of dorian-v1, described
TWICE by `qwen2.5vl:7b` (`scripts\\caption_probe.py`, non-destructive):

    self-agreement (Jaccard over content words):  median 0.786   3 of 40 below 0.40
    rows reporting bare skin:                     12 of 40
    confirmed by eye:                              8 of 8   (0010 0013 0014 0016
                                                              0017 0020 0023 0028)
                                                   0 false positives

**Twelve of the forty images that trained the shipped LoRA are of a man in his underwear, and
not one of our captions mentions it.** The weights carry "shirtless in grey boxer briefs" as part
of what `rbmndorianv` means. That is a defect in the model, not just in the folder. The likeness
number was fine -- 0.76 ArcFace -- because ArcFace looks at the face and nothing was looking
anywhere else.

**Why this vision model is trusted here and was not for framing.** v1.241 withdrew its framing
answer at 0-for-12. Framing is a geometric judgement about the edges of the picture. Naming
visible clothing is a description task, which is what these models do well -- and it was measured
before it was believed, to the same standard that got framing thrown out.

**What ships**

* `backend/services/wardrobe.py` -- the vocabulary and the verdict, pure text, no GPU or network,
  so it is testable. Word-boundary matching, so "briefcase" is not briefs. `CONTEXT_OK` keeps a
  swimming shot from failing. A description that names no clothing at all is **unmeasured**, never
  a pass.
* `POST /api/lora/datasets/{id}/wardrobe-check` -- two passes per image; a row is bare if EITHER
  says so, because a false positive costs one re-render and a false negative costs a training run.
  A bare row is marked `qc.ok = false` with a plain-English issue, which puts it in front of the
  repair loop that already exists -- and with v1.260 resolving datasets to the dressed base,
  repairing it now actually fixes it. Clearing the condition un-flags the row, so repair converges.
* `_flag_summary` counts `bare_skin` / `wardrobe_measured` / `wardrobe_unmeasured`, and lists
  `wardrobe` under `not_checked` until the check has actually run.
* Export excludes a bare row **even with `include_flagged` on**. `include_flagged` means "ship the
  near misses"; it should never have quietly also meant "ship the nudes". `include_bare` is a
  separate, deliberate choice.
* Captions reuse the description the wardrobe check already paid for (`seen_clothing`) instead of
  making a second vision call -- so a caption says "grey boxer briefs" on the rows where that is
  the truth.

**And the epoch count.** v1.259 measured likeness per epoch: it plateaus at epoch 21 of 40 on a
40-image set, and the last eight epochs span 0.028. The heuristic was `n * 1.2` capped at 40,
which asked for 40 epochs on 40 images -- about three hours of GPU past the point where the number
stopped moving. `_epochs_for(n)` now targets ~900 image-steps: **23 epochs on a 40-image set.**
The cap stays at 40 and the floor at 15 on purpose -- 900 steps is measured on ONE set size, so a
20-image set still gets 40 epochs until someone measures one. Every epoch is saved;
`scripts\\checkpoint_score.py` can end a run early on evidence rather than on arithmetic.

**Still open.** dorian-v1 itself is not fixed by this -- the twelve rows have to be re-rendered and
the LoRA retrained. redv1 is unaffected: `scripts\\base_probe.py` shows that character was never
stripped, so `auto` and `dressed` resolve to the same file for all four views.

"""

c = ROOT / "CHANGELOG.md"
s = c.read_text("utf-8")
assert not s.startswith("## v1.261")
c.write_text(ENTRY + s, "utf-8")
print(f"bumped {OLD} -> {NEW}")
