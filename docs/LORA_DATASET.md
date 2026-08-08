# 🎓 LoRA Dataset Gen

**v1.213.1 (2026-08-04).** The fifth mode, beside Create / Clothes / Emotions / Pose Library.
Turns a Klein 3.0 character into a **training-ready LoRA dataset**: a planned shot list,
Klein-rendered images, written captions, a vision-model QC pass, a review gallery, and a zip a
trainer eats directly.

Backend `backend/api/lora.py` (`/api/lora`) · UI `LoraPanel.tsx` · storage
`<project_dir>/_libraries/lora/datasets/<id>/` (`dataset.json` · `images/` · `exports/`).

---

## Why the dataset looks the way it does

These are researched rules, not preferences. Sources at the bottom.

| Rule | What we do |
|---|---|
| 30–100 images for a character; variety is what buys flexibility, and a narrow dataset bakes its narrowness in (an all-bikini set makes a bikini LoRA) | default **40**, slider 16–120 |
| Cover framing, angle, expression, pose, lighting, background | the planner spreads all six |
| ≥1024 px; trainers bucket by aspect ratio so mixed shapes are fine | 1024², 896×1152, 832×1216 by shot type |
| **Caption what VARIES, never the identity** | captions carry trigger + class + shot, angle, expression, pose, clothing, background, lighting — and say nothing about his face, hair or build |
| Rare trigger token + class word so the class prior helps | `rbmnduke man` |
| Captions live beside the image as `<same name>.txt` | that is exactly what ai-toolkit and kohya/FluxTrainer read |
| ai-toolkit substitutes its `trigger_word` for `[trigger]` | export offers literal **or** placeholder captions |

**The captioning rule is the whole mechanism.** Anything a caption names becomes a knob the
trainer can turn later; anything it leaves out is absorbed into the trigger word. Caption his
face and the LoRA learns "a face" it can be talked out of. Leave it out and the trigger owns it.

## The plan

Framing is allocated by weight — **face 20 % · head+shoulders 20 % · waist-up 30 % · full body
30 %** — and every other attribute is dealt in strict rotation so nothing clumps (an angle that
appears twice as often is trained twice as hard). The planner is deterministic: the same count
gives the same plan, so a re-plan is diffable and already-rendered rows survive.

Angles are **weighted, not even** (v1.210.1): a character LoRA lives on face-bearing data, so
the deal order is front-heavy and `back` lands on about **1 row in 10** rather than 1 in 6.

Three rules the planner enforces on its own:

- a **face crop carries no pose** — captioning a pose the crop cannot show teaches a word the
  image can never satisfy;
- **only body shots may face away** (`upper` / `full`). A `headshot` angled to the back is a
  close-up of the back of a head: it cannot pass a face check and teaches nothing. A face or
  headshot row that draws `back` swaps with a body row, so the counts stay as dealt;
- a **back row carries no expression**, and its render prompt asks for **no face**. This was a
  real bug: the prompt used to read *"a close-up portrait … seen from directly behind … with a
  thoughtful expression. His face … exactly the ones in image 1"* while image 1 was the BACK
  base with no face in it. Asked for an expression it cannot show and a face it cannot see, the
  model invents one and turns the body toward the camera — front-facing renders off the back
  base. Back rows now name hair, build and clothing instead, state that the camera sees the back
  of his head, and the QC prompt tells the checker the face is hidden by design.

Vocabulary: 4 framings · 6 angles (front, ¾ left/right, both profiles, back) · 9 expressions ·
8 poses · 8 lighting setups · 8 backgrounds. `GET /api/lora/recipe` returns it, and the UI
renders its pickers from that, so the two can never drift.

## Rendering

Each row is one Klein edit against the character's **angle-matched base view**
(`_base_for_view`, the same picker Klein 3.0 uses), fanned across every klein-capable worker
with live per-image worker/status. Close-ups additionally get the `face`-tagged reference as a
second image. Prompts are affirmative throughout — Klein has no negative-prompt node and runs
at cfg 1, see `feedback_klein_prompt_no_negatives` in project memory.

> Fill in missing views in 🎯 Klein 3.0 first (🧭 Generate missing views). A back shot rendered
> off a front-only character is a guess, and it will teach the LoRA that guess.

## Captions

Composed from the plan — the plan already knows what each image was *asked* to be, so wording
stays identical across the set, which is what a trainer wants. **🔍 Caption + vision detail**
additionally asks the vision model what clothing and background it can actually see and folds
that in (and it is told, explicitly, to say nothing about face, hair, build, age or sex).

## Shot mix presets (v1.212)

- **balanced** (default) — 20 / 20 / 30 / 30 across face · head+shoulders · waist-up · full body.
  More body data, more full-body flexibility.
- **face-heavy** — ~45 / 25 / 15 / 15, mirroring the ratio the dedicated dataset tools aim at
  (roughly 12 face / 6 bust / 6 body / 1 back), and fewer back rows. More face data buys
  likeness; fewer body shots costs some full-body flexibility.

Two independent projects converge on face-heavy for character likeness, so it ships as a
**preset rather than a silent change** — build one of each and compare the trained result.

## QC

**🔬 QC pass** asks the vision model, per image: is the framing right, the angle right, the
expression right, is there exactly one person, is the face clear, are there artifacts (deformed
hands, extra limbs, melted features), is it badly cropped — **and, since v1.212, is it still
him**. The character's front reference goes into the same call as image 1 and the render as
image 2, and the model is asked to judge build and stature as carefully as the face ("a slimmer
or taller version of him is a different person for this purpose"). An off-identity image is the
one failure a character LoRA cannot survive — it teaches the trigger word the wrong face — so it
flags like any other defect and the repair loop re-renders it.
Credit where due: both [lora-dataset-studio](https://github.com/perfectgf/lora-dataset-studio)
(InsightFace similarity with green/orange triage) and
[Fizgig](https://github.com/shootthesound/Fizgig) (Look Consistency Filter against three
references) had this before we did. InsightFace would be more precise; it also needs Visual
Studio Build Tools on Windows, and the vision model is already wired here. Answers are JSON, stored per image,
and anything that fails is flagged in the gallery **and held out of the export** unless you
explicitly include flagged images. Runs one thread per configured Ollama server.

## Fixing what QC flags

His first pass flagged 15 of 40, so the panel now **reads** the flags instead of just counting
them: a breakdown by cause (artifacts · bad crop · wrong framing · wrong angle · wrong
expression · not one person) plus the most common issue phrases. That split is the diagnosis —
**artifacts and bad crops mean the renders need work; framing/angle/expression misses usually
mean the checker is stricter than the shot list.**

Two buttons:

- **🔁 Re-render flagged** — one pass over every flagged image with fresh seeds, no re-check.
- **♻️ Repair until clean** — the loop: re-render the flagged images → re-check them → repeat
  until nothing is flagged or the round cap (1–6, default 3) is reached. Per-round history is
  shown live (`round 1: 15 re-rendered → 6 flagged · round 2: 6 → 2`).

Two brakes, because this spends renders while nobody is watching:

- the **round cap**, plus an early exit the moment the set comes back clean;
- a per-image **attempt counter** (`MAX_ATTEMPTS = 3`). An image that fails three renders is a
  bad plan row rather than bad luck, so it is parked as **stuck**, reported separately, and left
  out of further rounds unless you tick *retry stuck*. Tiles show `×N` once a row has been
  rendered more than once.

`POST /api/lora/datasets/{id}/repair` {rounds, qc_after, include_stuck}. Render and QC live in
shared helpers, so `/generate`, `/qc` and `/repair` cannot drift apart.

## What the Fizgig source taught us (v1.213)

He supplied `Fizgig-master.zip`. Reading the code and `docs/CLI.md` settled three things:

- **Fizgig is fully headless** — *"the GUI is a front-end that builds these exact commands and
  runs them as subprocesses"*. `krea2_cache_latents.py` / `krea2_cache_text.py` /
  `krea2_train.py`, all argparse. That makes it drivable from our dispatcher exactly like a
  worker job, and it carries the intelligent-trainer features nothing else has for Krea 2.
- **Quantise before you swap.** Their docs: *"Swapping is the slow path (4.4× the time, 4× the
  CPU): quantise first, and only swap when even NF4 will not fit."* `--quantize_4bit` puts the
  frozen base at ~5.6 GB with swap OFF. The fp8 ladder is 32 GB → 0 · 24 GB → 12 · **16 GB → 20**
  · 10–14 GB → 26.
- **Two caption rules we were getting wrong or missing:**
  - *"if the subject isn't actually recognizable in a shot (back of head, extreme distance),
    consider leaving the trigger out of that caption"* — **back rows now drop the trigger** and
    keep only the class word. Binding the trigger to the back of a head teaches it the back of a
    head.
  - *"Likeness at 0.25 MP: face crops are what make it work… a face crop gives about 40× the
    face area for the model to learn from."* That is the mechanism behind the **face-heavy**
    preset — at training resolution a face inside a full-body shot reaches the model as roughly
    10×10 latent pixels.

### We generate their look-outlier file

`--warmup_look_outliers` eases unusual angles in at ×0.4 LR instead of letting them fight the
forming identity — and their docs say *"There's no headless generator for it yet — run the Look
Filter once in the GUI."* Our QC already compares every image against the character's reference,
so a krea2 export now writes `images/fizgig_look_scores.json` itself, using their exact schema
and cutoff formula (read out of `lora_trainer_gui.py`'s writer and `krea2/trainer.py`'s reader):

```json
{"baselines": ["…"], "cutoff": max(median - 1.5*IQR, 0.25) | null,
 "scores": {"<stem without extension>": 0.0-1.0}}
```

The QC pass now returns an `identity_score` (0–1) alongside the boolean, which is what fills it.

## Export

`📦 Build zip` produces:

```
images/<name>_0001.png + <name>_0001.txt   ← the pair every trainer reads
dataset_aitoolkit.yaml                     ← ostris/ai-toolkit config, trigger_word wired
dataset_kohya.toml                         ← kohya / ComfyUI-FluxTrainer dataset config
manifest.json                              ← what each image was planned to be + its QC result
README.md                                  ← the captioning rule + suggested training settings
```

Suggested settings in the README scale with the set (~40 steps per image, clamped 1000–3000),
rank/alpha 16–32, lr 1e-4 adamw8bit, buckets 512/768/1024, caption dropout 0.05 — and the
warning that matters most: **train against the same base checkpoint you will generate with.**

## ⚠ Krea 2 is NOT Flux (verified 2026-08-04)

Checked online **and** against this repo's own `workflows/KREA2_*.json`, which agree:

> **Krea 2 is a from-scratch 12.9B diffusion transformer by Krea AI** — single-stream blocks,
> grouped-query + sigmoid-gated attention — that borrows a **Qwen3-VL 4B text encoder** and the
> **Qwen-Image VAE**. Our workflows load exactly `krea2_turbo_mxfp8.safetensors` +
> `qwen3vl_4b_fp8_scaled.safetensors` + `qwen_image_vae.safetensors`, which is that model, not
> a Flux one.

Consequences, all now baked into the export:

- **Trainer: kohya-ss/musubi-tuner** — it has official (experimental) Krea 2 support
  (`krea2_train_network.py`, `networks.lora_krea2`, plus `krea2_cache_latents.py` /
  `krea2_cache_text_encoder_outputs.py`) and it is **the route that fits 12–16 GB**. A krea2
  export now ships `dataset_musubi.toml` + `train_krea2_musubi.txt` ready to run.
  ostris/ai-toolkit also trains Krea 2 but is heavier (~18–20 GB at 768 for LoKr; its
  Krea2Trainer wrapper targets 24 GB). **ComfyUI-FluxTrainer cannot train Krea 2 at all** —
  `dataset_kohya.toml` is for a Flux or SDXL target only.
- **12–16 GB is real, and here is why it works.** Pre-caching the latents *and* the
  text-encoder outputs takes the ~8 GB Qwen3-VL encoder out of the training loop entirely;
  `--fp8_base --fp8_scaled` (K2 accepts **scaled** fp8 only) shrinks the DiT; `--blocks_to_swap N`
  parks up to 26 of the 28 transformer blocks in CPU RAM. Measured runs: **RTX 3060 12 GB →
  peak ~10.5 GB, 7.2–7.8 s/step at 512², rank 16, swap 22**; **RTX 4070 12 GB → rank 32,
  swap 26, ~2 h for 2000 steps**. ⚠ Budget **32–64 GB of SYSTEM RAM** — the swapped blocks live
  there, and that is the requirement people miss. Block swap trades GPU time for PCIe
  bandwidth, so step time is bounded by transfers, not compute.
- **Raw vs Turbo.** RAW is the un-distilled checkpoint intended for fine-tuning; TURBO is the
  distilled one you generate with (8 steps, CFG off). Krea's own LoRAs are trained on Raw and
  applied to Turbo. To train directly on Turbo, add ostris'
  [`krea2_turbo_training_adapter`](https://huggingface.co/ostris/krea2_turbo_training_adapter) —
  a de-distillation layer removed at inference — otherwise the distillation degrades as you train.
- **VRAM.** A community Krea 2 Turbo LoRA run reported **~22 GB and ~6 hours for 20 images**.
  This app's fleet is 16 GB: fine for rendering the dataset, likely under the bar for training
  it. Decide the training box before wiring the training step.
- The exported YAML leaves Krea 2's `arch` key **blank with a marker**, because that exact field
  name could not be verified — ai-toolkit's own UI writes the model block, so paste it in.
  Guessing a config key would be exactly the guessing the project's rules forbid.

## Next step (designed, not built) — ⚠ SUPERSEDED: built and validated; see "The training loop as it exists" at the end of this file

Training from inside the app on a remote worker:

- **For Krea 2 → ostris/ai-toolkit.** It runs its own job UI/CLI rather than ComfyUI, so it
  needs a small side-service on the training box; the YAML in the zip is the input.
- **For a Flux or SDXL target → ComfyUI-FluxTrainer** (kijai) — `TrainDatasetGeneralConfig` →
  `TrainDatasetAdd` → `InitFluxLoRATraining` → `FluxTrainLoop` → `FluxTrainSave`. That route
  fits our dispatcher exactly (just another queued workflow: worker choice, live status and
  cancel come free).

The zip stays valid for both so the route follows the target, not the tooling.

## Sources

- [Flux LoRA training guide (2026)](https://thefluxtrain.com/blog/noobs-guide-to-flux-lora-training/) — image counts, rank, steps, trigger word usage
- [Character LoRA creation guide](https://www.floyo.ai/character-lora-training-guide-workflows) — 30–100 images, angle/expression/lighting/background coverage, excluding deformed images
- [Structured captions for character LoRAs](https://www.rishidesai.org/posts/character-lora/) — caption template order, caption the variable / omit the constant
- [What exactly to caption for Flux LoRA training](https://www.pelayoarbues.com/literature-notes/articles/what-exactly-to-caption-for-flux-lora-training) — variation → flexibility, narrow datasets bake themselves in
- [ostris/ai-toolkit](https://github.com/ostris/ai-toolkit) + [example config](https://github.com/ostris/ai-toolkit/blob/main/config/examples/train_lora_flux_24gb.yaml) — dataset layout, `[trigger]`, config fields
- [ComfyUI-FluxTrainer](https://github.com/kijai/ComfyUI-FluxTrainer) / [InitFluxLoRATraining](https://comfyai.run/documentation/InitFluxLoRATraining) — the in-ComfyUI training route

## Handing the dataset to Fizgig (v1.214)

**You do not retype anything.** A krea2 export now contains its own runner.

```
duke-v1_40img_20260804.zip
  images/            *.png + *.txt captions + fizgig_look_scores.json
  dataset_fizgig.toml
  fizgig_run.py            <- the runner
  train_krea2_fizgig.bat   <- Windows: set FIZGIG= at the top, double-click
  train_krea2_fizgig.sh    <- Linux/RunPod: FIZGIG=/workspace/Fizgig ./...sh
  train_krea2_fizgig.txt   <- the same commands, for when you want to change one
  sample_prompts.txt  sample_ref.png
  dataset_musubi.toml  dataset_kohya.toml  dataset_aitoolkit.yaml
  manifest.json  README.md
```

Unzip it anywhere, keep the layout, point it at your Fizgig checkout:

```
python fizgig_run.py --fizgig C:/Fizgig --dry-run    # prints, runs nothing
python fizgig_run.py --fizgig C:/Fizgig              # cache -> cache -> train
```

Model paths come from that folder's `prefs.json` -- Fizgig's own downloader
(Preferences -> "Download models for me") writes absolute paths there under
`krea2_raw_dit`, `krea2_text_encoder`, `krea2_vae`, `krea2_turbo_dit`. Override
any of them with `--dit / --vae / --text-encoder / --turbo-dit`.

Useful flags: `--skip-cache` (latents/text already cached), `--quant nf4|int8|fp8`,
`--blocks-to-swap N` (fp8 only -- NF4 force-zeroes it), `--epochs N`, `--output-dir`.

### Why there is no "Train" button in the app yet — ⚠ PARTLY SUPERSEDED: the loop runs headless via the helper + agent (see end of file); the UI is still the open item

Fizgig has no HTTP API. Its only server-ish surface is `docker/` (a RunPod image
that serves the tkinter GUI over noVNC). Our dispatcher speaks ComfyUI, so Fizgig
cannot be queued as a workflow. Automating the last hop means one of:

| | what it costs | what it buys |
|---|---|---|
| Fizgig on the RBMN backend machine | ~32 GB weights + a venv on the app box; training occupies it | zero new moving parts -- `subprocess.run` from `lora.py` |
| a small agent on a 16 GB worker | one install script (the `install_trellis2.bat` pattern) | training on the worker that has the VRAM; live status in the app |
| shared folder + generated .bat | nothing | already works today; you press the button |

Row 3 is what v1.214 ships.

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


---

# The training loop as it exists (v1.259–v1.266.1) — TESTED END-TO-END

Everything below this line supersedes "Next step (designed, not built)" and "Why there is no
'Train' button in the app yet" above — the loop is BUILT and VALIDATED as scripts + helper
routes. What remains unbuilt is the in-app UI over it (and per standing rule 6, API-only is
not "done").

## The loop

    app: POST /api/lora/datasets/{id}/export
    agent upload: zip → helper POST /datasets/{id}         (~30MB, seconds on LAN)
    helper: POST /runs {"dataset": id}                     (hard-stops ComfyUI first)
    scripts\train_report.py --run <id>                     loss curve + adaptive-LR + recaption log
    scripts\checkpoint_score.py --run <id> --char <slug>   ArcFace on per-epoch previews
    scripts\fetch_pick.py --run <id> --epochs 14,16,22     eyeball the plateau
    helper: POST /runs/{id}/install-lora {"name": "<file>.safetensors"}
    helper: POST /comfy/start                              (network bat — see box facts)
    scripts\lora_test.py                                   6-render TURBO grid
    scripts\lora_score.py                                  ArcFace table + scores.json

All of it runs through the agent (`scripts/_agent/inbox/`); no step needs a person to carry a
file between machines.

## Picking a checkpoint: loss is not likeness (v1.259)

The first run's loss curve recommended epoch 27; ArcFace on the actual previews showed 27 was
the WORST checkpoint in the plateau and 30 the best. Likeness climbs steeply to ~epoch 7,
plateaus from ~12–21, and the last eight epochs of a 40-epoch run spanned 0.028. Consequences:
`_epochs_for(n)` targets ~900 image-steps (23 epochs on 40 images; floor 15, cap 40 — measured
on ONE set size, so a 20-image set still gets 40 until someone measures one), every epoch is
saved, and **checkpoints are picked by `checkpoint_score.py`, never by the loss printout.**
When the scores tie across the plateau (they did: 0.7684/0.7733/0.7594), the pictures decide;
when the pictures are indistinguishable (they were), the number decides.

## ⚠ The shared-output-folder trap (v1.265)

Fizgig keys `output_loras/` on the DATASET id, so every run of the same dataset writes into
the same folder. The first scoring pass of the dorian retrain scored 62 previews from TWO
different LoRAs. `checkpoint_score.py` now filters previews to the run's started→finished
window and prints what it ignored; **any checkpoint numbered above the run's epoch count is
the OLD run's file.** The helper's install-lora route enforces the same window and refuses
out-of-window files without `force: true`.

## Installing (helper v1.217)

`POST /runs/{id}/install-lora {"name", "dest_name"?, "force"?}` copies a `.safetensors` from
the run's output folder into ComfyUI's loras folder on the same box (`.part` + size check +
atomic rename; dest_name is flattened to its basename). Destination resolves: explicit
`comfy.loras_dir` config → `comfy.root`/ComfyUI/models/loras → `comfy.root`/models/loras →
the training box's known install as a blank-config fallback.

## The TURBO exam (v1.266) — what "validated" means

The LoRA trains on RAW but runs on TURBO, so the exam runs on TURBO, on the box it will be
used on. Six renders, one seed, each isolating one variable, ArcFace-scored against the
character's own front/face references (`scripts/lora_test.py` + `lora_score.py`; images land
in `scripts/_diag/lora_test/`):

| variant | prompt | LoRA | measures | dorian e16 |
|---|---|---|---|---|
| trig_default_10 | trigger, no outfit words | 1.0 | likeness + default wardrobe | **0.8118** |
| trig_default_08 | same | 0.8 | cost of backing off | 0.7330 |
| trig_suit_10 | trigger + navy suit | 1.0 | wardrobe controllable? | 0.7526 |
| trig_suit_08 | same | 0.8 | ditto | 0.6905 |
| notrig_10 | NO trigger | 1.0 | identity leak | 0.8127 |
| control_nolora | trigger, no LoRA | — | base-model floor | 0.1211 |

The control being a different man entirely is what makes the rest of the table meaningful.
TURBO likeness at 1.0 beat the RAW previews' best (0.7733). **Run at strength 1.0.**

## The two rules every consumer of these LoRAs must know

Both are consequences of captioning correctly ("caption what varies"), not defects:

1. **Always name the outfit in the prompt.** Every training caption names the visible
   clothing, so wardrobe belongs to the PROMPT, not the trigger. The trigger prompt with no
   clothing words rendered the character SHIRTLESS; asked for a navy suit he wore one, at
   0.75 likeness. An unclothed prompt is a request for skin.
2. **Unload the LoRA for shots the character is not in.** At strength 1.0 a 40-image LoRA
   owns the whole distribution: a no-trigger "a man" prompt still rendered HIM (0.8127, in
   the training tee).

## Training-box inference facts

RTX 4060 Ti 16GB ("ZOMAIN01") → **krea2_turbo_fp8**, not mxfp8 (Blackwell-only format). Its
ComfyUI has none of the ULTRA workflow's decorator custom nodes (RBG Smart Seed Variance,
rgthree Power Lora Loader / Any Switch, ImageSharpenKJ) — the test scripts strip to core
nodes, which is fine for measurement; install those packs if app-quality renders are wanted
there. The helper starts ComfyUI via `run_nvidia_gpu-LTX2-16GB.bat` (config
`comfy.start_cmd`) because the portable's default bat binds localhost-only and is unreachable
from the app machine.

## What remains

In-app UI over this loop (upload → run → live status → scores → install → exam) in the LoRA
panel; profile-baseline likeness scoring (profiles score low against frontal refs — confirm on
character two first); `upper`/`full` separation on the person mask. Shelf: bilingual captions,
masked training, per-image LR, Fizgig's Repair Studio / LoRA Royale / Extract.


## The standing recipe + the controlled experiment (v1.269.3, 2026-08-08)

Same character, same pipeline, only the dataset changed — redv1 re-planned from balanced-20
(4 face crops, identity median 0.534) to face_heavy-39 (median 0.5684):

                      dorian e16    redv1-v1 e36    redv1-v2 e21
    dataset           40 face_heavy  20 balanced     39 face_heavy
    dataset median    0.69           0.534           0.5684
    TURBO exam        0.8118         0.5677          0.6089

Likeness tracks the dataset column exactly, three for three. **The recipe, default for every
new character:** `face_heavy` 40 · universal face ref · dressed base · ONE targeted re-render
round on below-match rows (aggregate improves; individual rows are a coin flip — redv1's 0003,
a left-profile face crop, got worse twice: 0.266→0.240→0.181) · `min_likeness: 0.25` at export
(a stranger is worse than a gap) · ~23 epochs at 40 images · pick by ArcFace on previews ·
inference at strength 1.0, outfit always named, LoRA unloaded off-character.

Open measurement: three small-set runs ended still climbing — ~900 image-steps is a floor,
not a ceiling, for sets under 40. Measure before touching `_epochs_for`.
