"""v1.218 — VERSION, pyproject, requirements, CHANGELOG, docs."""
import sys
from pathlib import Path

ROOT = Path(sys.argv[1] if len(sys.argv) > 1 else ".")

v = ROOT / "VERSION"
assert v.read_text("utf-8").strip() == "1.217.0", v.read_text("utf-8")
v.write_text("1.218.0\n", "utf-8")

pp = ROOT / "pyproject.toml"
s = pp.read_text("utf-8")
assert s.count('version = "1.217.0"') == 1
s = s.replace('version = "1.217.0"', 'version = "1.218.0"', 1)
DEP = '''    # ArcFace identity scoring (v1.218). CPU-only and OPTIONAL: lora.py degrades
    # to the vision model's own identity judgement when these are absent, and
    # says so via /api/lora/likeness-health. buffalo_l (~300MB) auto-downloads
    # on first use. numpy/Pillow/opencv above are its heavy deps and already here.
    "insightface>=0.7",
    "onnxruntime>=1.17",
'''
assert '"opencv-python>=4.9",\n' in s
s = s.replace('"opencv-python>=4.9",\n', '"opencv-python>=4.9",\n' + DEP, 1)
pp.write_text(s, "utf-8")

rq = ROOT / "requirements.txt"
r = rq.read_text("utf-8")
assert "insightface" not in r
assert "opencv-python\n" in r
rq.write_text(r.replace("opencv-python\n",
                        "opencv-python\n# ArcFace identity scoring (v1.218) — optional, CPU-only\n"
                        "insightface>=0.7\nonnxruntime>=1.17\n", 1), "utf-8")

ENTRY = '''## v1.218.0 -- real ArcFace identity scores (2026-08-05)

He asked whether the identity matching he remembered from the other repos was something we had
or still had to build. Checking turned up **a bug in what I shipped in v1.213.**

Both reference projects score identity with **InsightFace/ArcFace embeddings**. Fizgig's Look
Consistency Filter averages each image against the centroid of THREE baselines ("one photo's
framing bias can't dominate the score"), and its own code documents the scale: *"same person
across varied photos usually lands 0.30-0.70 vs a single baseline; a different person rarely
clears 0.25."* lora-dataset-studio does the same -- "InsightFace identity scoring drops
off-identity shots before they poison training".

**We had the mechanism and the wrong units.** v1.212 asked a vision LLM "same person? score
0-1". v1.213 then piped those numbers into `fizgig_look_scores.json` using Fizgig's cutoff
`max(median - 1.5*IQR, 0.25)` -- where 0.25 is an ArcFace COSINE. An LLM rating identity 0-1
clusters at 0.85-0.95 for anything it likes, so the floor was unreachable, the IQR fence barely
moved on a tight cluster, and **the file we have been shipping was very nearly inert**:
`--warmup_look_outliers` had almost nothing to warm up. I matched their formula and inferred
their units instead of measuring them.

`backend/services/likeness.py` (NEW) -- buffalo_l on CPU, lazy-loaded, embedding cache keyed on
(path, mtime, size). `ctx_id=-1` is deliberate: identity scoring must never queue behind a
render or take VRAM from a training run.

**MEASURED, not assumed** (buffalo_l, insightface 1.0.1, against their own bundled samples):
  * DIFFERENT people, 15 pairs from one group photo:
        min -0.083 · median +0.026 · max +0.213 -> **0 of 15 cleared the 0.25 floor**
  * SAME face, varied capture (downscale 40%, brightness x0.6/x1.5, contrast, greyscale,
    mirrored, rotated): worst **+0.915**, best +1.000
  * three baselines genuinely average -- a wrong baseline drags a score down, and the result is
    not merely the minimum
  * a faceless image scores **None**, never 0.0
Those same-person figures transform ONE photograph, so they are an upper bound; Fizgig's
0.30-0.70 is for genuinely different photographs. Our renders all come off one base, so scores
should land HIGH -- **a prediction, not a measurement**, which is why the new route exists.

- Both checkers now run and each answers only what it is good at. The vision model keeps
  framing, angle, expression, artifacts, crop and outfit. **ArcFace supplies the number**, and
  only an ArcFace number may enter `fizgig_look_scores.json`.
- The LLM's score is retained as `identity_score_llm` for comparison -- expect the two to
  disagree on some images in both directions.
- **Only the 0.25 different-person floor FAILS an image.** "Borderline" is surfaced as an issue
  and left to him: discarding a drifting-but-recognisable render costs a re-render for no
  certain gain. `no face` is counted, never flagged -- correct for a back shot, and Fizgig never
  auto-excludes an unscoreable row either.
- Baselines come from the CHARACTER's references (front base honouring v1.217's base_mode, then
  face/left/right tags), never from the dataset's own renders -- scoring images against
  themselves produces a beautiful number that means nothing.
- `POST /datasets/{id}/likeness` rescores a whole set on CPU with no vision model and no worker,
  returning the distribution, band counts, the cutoff, the five worst images, and a **sanity
  line** that calls out a median above 0.90 (suspicious baselines) or below 0.30 (wrong
  character). This is the measurement v1.213 should have made.
- `GET /likeness-health` reports availability; a missing dependency is a documented degraded
  mode, not a failure.

Verified: `test_v1218.py` runs the real model over insightface's bundled images and asserts the
shipped constants against observed behaviour -- it does not take my word for the bands. On a host
without insightface it verifies the degraded path instead and says which half it skipped (that is
what happens in the device VM; the measured half ran in the cloud sandbox). All six lora suites
pass on the live file, md5 d57a03ff305abb44b748f883d4f45916.

Two suites needed honest updates rather than plumbing: `test_v1213` asserted "a boolean-only
verdict still yields a score", which **was the bug** -- it now asserts the opposite. pyflakes also
caught the likeness issue-line referencing `issues` above its own definition, pre-ship.

Deps: `insightface` + `onnxruntime` added to pyproject and requirements as OPTIONAL. numpy,
Pillow and opencv-python were already present -- they are the heavy half.

'''
cl = ROOT / "CHANGELOG.md"
s = cl.read_text("utf-8")
assert s.startswith("## v1.217.0"), s[:40]
cl.write_text(ENTRY + s, "utf-8")

d = ROOT / "docs" / "LORA_DATASET.md"
s = d.read_text("utf-8")
DOC = '''
## Identity scoring (v1.218)

Two checkers run over every image, and each answers only what it is good at.

| | good at | supplies |
|---|---|---|
| vision LLM | framing, angle, expression, artifacts, crop, outfit | the shot-quality verdict |
| **ArcFace** (buffalo_l, CPU) | "is this the same face" | **the likeness number** |

A vision model asked to rate identity 0-1 clusters at 0.85-0.95 for anything it
likes. That is not a scale — it is an opinion with decimals. Only an ArcFace
cosine ever enters `fizgig_look_scores.json`; the LLM's number is kept as
`identity_score_llm` for comparison and nothing else.

### The bands

Fizgig's, unchanged, so a number means the same thing in both tools:

| score | verdict | what happens |
|---|---|---|
| ≥ 0.45 | match | nothing |
| 0.30 – 0.45 | borderline | surfaced as an issue, **not** failed |
| 0.25 – 0.30 | weak | surfaced as an issue, **not** failed |
| < 0.25 | not him | **flagged** — below the different-person floor |
| `None` | no face | counted, never flagged (correct for a back shot) |

Only the floor fails an image. Discarding a drifting-but-recognisable render
costs a re-render for no certain gain.

### Measured, not assumed

Against insightface's own bundled samples (buffalo_l, 1.0.1):

- **different people**, 15 pairs from one group photo: min −0.083, median +0.026,
  max **+0.213** — none cleared the 0.25 floor
- **same face**, varied capture (downscale, brightness, contrast, greyscale,
  mirrored, rotated): worst **+0.915**

Those same-person numbers transform one photograph, so they are an upper bound.
Fizgig's stated 0.30–0.70 is for genuinely different photographs. Our renders all
come off one base, so ours should land **high** — that is a prediction, and the
route below is how you check it rather than trusting it.

### Checking a real set

```
POST /datasets/{id}/likeness
```

CPU only — no vision model, no worker, no GPU. Rescores every image and returns
the distribution, band counts, the cutoff, the five worst images, and a
`sanity` line. Read that line first:

- *median above 0.90* — suspiciously high even for renders off one base. Check
  the baselines are the **character's references**, not images from this dataset.
- *median below 0.30* — the set doesn't resemble the baselines at all. Wrong
  character loaded.

### Baselines

Up to three, from the character's own references — the front base (honouring
v1.217's `base_mode`), then face / left / right tags. Fizgig averages three
deliberately: one photo's framing bias otherwise dominates, and every image that
happens to share its framing looks more like him than it is.

**Never from the dataset's own renders.** Scoring images against themselves
produces a beautiful number that means nothing.

A reference with no detectable face is skipped, not an error. If *no* reference
has a usable face, the route 409s rather than scoring against nothing.

### Availability

```
GET /api/lora/likeness-health
```

`pip install insightface onnxruntime` — CPU-only, and buffalo_l (~300MB)
auto-downloads on first use. numpy, Pillow and opencv-python were already
dependencies; they are the heavy half. When it is absent, QC still runs and falls
back to the vision model's identity judgement — but that judgement is
**deliberately not written to the trainer's file**, because it is not on the
right scale. That is the whole v1.213 bug.
'''
assert "## Identity scoring (v1.218)" not in s
d.write_text(s.rstrip() + "\n" + DOC, "utf-8")
print("VERSION 1.218.0 · pyproject · requirements · CHANGELOG · docs")
