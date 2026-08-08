"""v1.213.0 — read the Fizgig source, take what it proves.

He handed over Fizgig-master.zip.  Read it (not the marketing page — the code
and `docs/CLI.md`), and three things are now settled facts rather than guesses:

1. **Fizgig is FULLY HEADLESS.**  "the GUI is a front-end that builds these exact
   commands and runs them as subprocesses" — `src/fizgig/scripts/krea2_train.py`,
   `krea2_cache_latents.py`, `krea2_cache_text.py`, all argparse.  That settles
   the in-app training backend question: it is drivable exactly like our worker
   jobs, and it carries the intelligent-trainer features musubi does not have.

2. **Its VRAM ladder is explicit**, and so is the rule that beats it:
   *"Swapping is the slow path (4.4× the time, 4× the CPU): quantise first, and
   only swap when even NF4 will not fit."*  16 GB → `--blocks_to_swap 20` on the
   fp8 path, or `--quantize_4bit` (NF4, ~5.6 GB resident, swap forced off).

3. **Two dataset rules from its docs that we were getting wrong or missing:**
   - *"if the subject isn't actually recognizable in a shot (back of head,
     extreme distance), consider leaving the trigger out of that caption"* —
     our back rows were carrying the trigger.  Fixed: back rows drop it.
   - *"Likeness at 0.25 MP: face crops are what make it work… a face crop gives
     about 40× the face area for the model to learn from"* — which is exactly
     why the face-heavy preset exists; now stated with the mechanism.

And one thing we can give BACK: `fizgig_look_scores.json` drives
`--warmup_look_outliers` (unusual angles ease in at ×0.4 LR instead of fighting
the forming identity), and the docs say *"There's no headless generator for it
yet — run the Look Filter once in the GUI."*  Our QC already compares every
image to the character's reference, so we can write that file ourselves.  Schema
and cutoff formula lifted from the source, not guessed:
    {"baselines": [...], "cutoff": max(median - 1.5*IQR, 0.25) | null,
     "scores": {"<stem without extension>": 0.0-1.0}}

Exact-match patcher: every replacement asserts exactly ONE occurrence.
Usage: python patch_lora_v1213.py <path-to-lora.py>
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


# ── 1. back rows drop the trigger (Fizgig's caption rule) ────────────────
rep(
    '''    trig = (ds.get("trigger") or "sks").strip()
    cls = (ds.get("class_token") or "person").strip()
    head = (f"{trig} {cls}" if trigger_literal else f"[trigger] {cls}").strip()''',
    '''    trig = (ds.get("trigger") or "sks").strip()
    cls = (ds.get("class_token") or "person").strip()
    # Fizgig's caption rule, from real runs: "if the subject isn't actually
    # recognizable in a shot (back of head, extreme distance), consider leaving
    # the trigger out of that caption".  A back shot shows no face, so binding
    # the trigger to it teaches the trigger a back of a head.
    if item.get("angle") == "back":
        head = cls
    else:
        head = (f"{trig} {cls}" if trigger_literal else f"[trigger] {cls}").strip()''',
    "no trigger on back rows",
)

# ── 2. QC returns a similarity SCORE, not just a boolean ────────────────
rep(
    '''  "same_person": true/false   — is the person in image 2 the same individual as image 1
                                (face, hair, and especially BODY BUILD and stature)?
  "identity_note": "short phrase"  — if they differ, say how (for example "much slimmer",
                                "different face shape", "younger", "different hair").''',
    '''  "same_person": true/false   — is the person in image 2 the same individual as image 1
                                (face, hair, and especially BODY BUILD and stature)?
  "identity_score": 0.0-1.0   — how close the likeness is. 1.0 = indistinguishable,
                                0.7 = clearly him with small drift, 0.4 = related but off,
                                0.0 = a different person.
  "identity_note": "short phrase"  — if they differ, say how (for example "much slimmer",
                                "different face shape", "younger", "different hair").''',
    "identity_score in the prompt",
)
rep(
    '''                if ref_png:
                    flags["same_person"] = bool(data.get("same_person", True))''',
    '''                if ref_png:
                    flags["same_person"] = bool(data.get("same_person", True))
                    try:                 # 0-1 likeness, for the look-outlier file
                        flags["identity_score"] = max(0.0, min(1.0, float(
                            data.get("identity_score", 1.0 if flags["same_person"] else 0.0))))
                    except (TypeError, ValueError):
                        flags["identity_score"] = 1.0 if flags["same_person"] else 0.0''',
    "identity_score captured",
)

# ── 3. the Fizgig dataset config + command sheet ─────────────────────────
rep(
    '''@router.post("/datasets/{ds_id}/export")''',
    '''def _fizgig_toml(ds: dict, resolution: List[int]) -> str:
    """Fizgig's dataset TOML (docs/CLI.md).  Same lineage as musubi's, with
    `num_repeats` and `bucket_no_upscale` in [general]."""
    res = max(resolution)
    return f"""# Fizgig dataset config (shootthesound/Fizgig, docs/CLI.md).
# Point image_directory at the extracted images/ folder; give every dataset its
# OWN cache_directory — a shared one can mix a previous dataset into the run.
[general]
resolution = [{res}, {res}]   # area target; buckets keep each image's own aspect
caption_extension = ".txt"
batch_size = 1
num_repeats = 1
enable_bucket = true
bucket_no_upscale = true

[[datasets]]
image_directory = "./images"
cache_directory = "./cache/{ds.get('id')}"
"""


def _fizgig_commands(ds: dict, n: int) -> str:
    """The three headless commands, with the flags Fizgig's own docs validate.

    Its GUI builds exactly these and runs them as subprocesses, so nothing here
    is a second-class path."""
    trig = ds.get("trigger", "sks")
    epochs = max(10, min(round(n * 1.2), 40))
    return f"""# ── Krea 2 LoRA — Fizgig headless (shootthesound/Fizgig) ─────────────────
#
# Fizgig's GUI just builds these commands, so the CLI is feature-complete:
# adaptive LR, the per-image loss watch, auto-recaptioning and the look-outlier
# warm-up are all available from a terminal.
#
# Models (Comfy-Org/Krea-2):
#   --dit           krea2_raw_bf16.safetensors        (RAW — the one you train)
#   --turbo_dit     krea2_turbo_fp8_scaled.safetensors (8-step previews)
#   --vae           qwen_image_vae.safetensors
#   --text_encoder  qwen3vl_4b_bf16.safetensors        (also the recaption vision model)

# 1) cache latents
python src/fizgig/scripts/krea2_cache_latents.py \\
  --dataset_config dataset_fizgig.toml --vae /models/qwen_image_vae.safetensors --skip_existing

# 2) cache text
python src/fizgig/scripts/krea2_cache_text.py \\
  --dataset_config dataset_fizgig.toml \\
  --text_encoder /models/qwen3vl_4b_bf16.safetensors --skip_existing

# 3) train — everything on
python src/fizgig/scripts/krea2_train.py \\
  --dataset_config dataset_fizgig.toml \\
  --dit /models/krea2_raw_bf16.safetensors \\
  --vae /models/qwen_image_vae.safetensors \\
  --text_encoder /models/qwen3vl_4b_bf16.safetensors \\
  --turbo_dit /models/krea2_turbo_fp8_scaled.safetensors \\
  --output_dir ./output_loras/{ds.get('id')} --output_name {ds.get('id')} \\
  --network_dim 16 --network_alpha 16 \\
  --max_train_epochs {epochs} --save_every_n_epochs 1 --save_state \\
  --keep_last_n_states 2 --seed 42 \\
  --quantize_4bit \\
  --adaptive_lr --adaptive_lr_min 5e-5 --adaptive_lr_max 4e-4 \\
  --log_per_image_loss --per_image_lr --auto_recaption \\
  --warmup_look_outliers --trigger_word {trig} \\
  --sample_prompts sample_prompts.txt --sample_every_n_epochs 1 \\
  --sample_width 1024 --sample_height 1024

# ── VRAM: quantise FIRST, swap only if you must ──────────────────────────
# Fizgig's docs are blunt about it: "Swapping is the slow path (4.4x the time,
# 4x the CPU): quantise first, and only swap when even NF4 will not fit."
#   --quantize_4bit    NF4 frozen base, ~5.6 GB resident, block swap forced OFF
#                      -> the right default on a 16 GB card
#   default fp8        ~14 GB resident; then the ladder is
#                        32 GB -> --blocks_to_swap 0     24 GB -> 12
#                        16 GB -> 20                     10-14 GB -> 26
#   --quant_int8 bf16  ~18.6 GB, needs 24 GB, but the FASTEST measured
#                      (0.637 s/it vs NF4 0.709 on a 5090) and ~7x more accurate
#                      than NF4 in forward error
#   --compile_blocks auto   ~2x faster steady-state on the INT8 path (needs
#                      triton; on Windows also the MSVC C++ Build Tools)
#
# ── the intelligence flags (Krea 2 only, and the reason to use Fizgig) ────
#   --log_per_image_loss  classifies every image each epoch (easy / suspect /
#                         stuck / exhausted / excluded) into
#                         loss_log/problem_images.json, and prints a PLATEAU
#                         BANNER with a best-checkpoint estimate — your
#                         "you're done" signal, instead of picking by eye
#   --per_image_lr        stuck images throttled x0.5 -> x0.25 -> x0.125,
#                         mined-out ones eased to x0.6, the healthy cohort x1.1
#   --auto_recaption      Qwen3-VL rewrites a stuck image's caption from what is
#                         actually visible, re-encodes the text cache, gives it a
#                         fresh start; 2 failures and it is excluded
#   --warmup_look_outliers  reads fizgig_look_scores.json (WE WRITE IT — see
#                         below) and eases unusual angles in at x0.4 -> x1.0
#
# fizgig_look_scores.json in this zip was generated from our own QC identity
# check (every image compared against the character's reference). Fizgig's docs
# say there is no headless generator for it — this is that file. Drop it in the
# images/ folder alongside the pictures.
#
# LoKr alternative: --network_type lokr --lokr_factor 8 (their validated
# default; "in our validation runs LoKR at factor 8 hit the highest likeness
# we've ever measured"). Costs ~20% step time.
#
# Pause is a file: create <output_dir>/.pause_requested and it saves state and
# exits cleanly at the next epoch boundary; --resume <state-dir> continues.
"""


def _look_scores(ds: dict, picked: List[tuple], stems: Dict[str, str]) -> dict:
    """`fizgig_look_scores.json` — schema and cutoff taken from Fizgig's source
    (`lora_trainer_gui.py` writer + `krea2/trainer.py` reader), not invented:
    keys are basenames WITHOUT extension, cutoff is the IQR fence
    `max(median - 1.5*(q3-q1), 0.25)`, and anything scoring below it gets the
    LR warm-up.  Our identity QC supplies the scores."""
    scores: Dict[str, Any] = {}
    for it, _fp in picked:
        q = it.get("qc") or {}
        s = q.get("identity_score")
        if s is None and "same_person" in q:
            s = 1.0 if q.get("same_person") else 0.0
        scores[stems[it["id"]]] = None if s is None else round(float(s), 4)
    vals = sorted(v for v in scores.values() if isinstance(v, (int, float)))
    cutoff = None
    if len(vals) >= 4:
        n = len(vals)
        med, q1, q3 = vals[n // 2], vals[n // 4], vals[(3 * n) // 4]
        cutoff = max(med - 1.5 * (q3 - q1), 0.25)
    return {"baselines": [f"{ds.get('char_name')} (Klein 3.0 front base)"],
            "cutoff": cutoff, "scores": scores}


@router.post("/datasets/{ds_id}/export")''',
    "fizgig toml + commands + look scores",
)

# ── 4. write them into the zip ──────────────────────────────────────────
rep(
    '''    manifest = []
    with zipfile.ZipFile(zp, "w", zipfile.ZIP_DEFLATED) as z:
        for n, (it, fp) in enumerate(picked, 1):
            stem = f"{_slugify(ds.get('name', 'ds'))[:24]}_{n:04d}"''',
    '''    manifest = []
    stems: Dict[str, str] = {}
    with zipfile.ZipFile(zp, "w", zipfile.ZIP_DEFLATED) as z:
        for n, (it, fp) in enumerate(picked, 1):
            stem = f"{_slugify(ds.get('name', 'ds'))[:24]}_{n:04d}"
            stems[it["id"]] = stem''',
    "collect the export stems",
)
rep(
    '''        if ds.get("target") == "krea2":
            # musubi-tuner is the route that fits 12-16 GB, and its dataset
            # config is NOT the kohya one — ship both plus the command sheet
            z.writestr("dataset_musubi.toml", _musubi_toml(ds, resolution))
            z.writestr("train_krea2_musubi.txt",
                       _musubi_commands(ds, len(picked), resolution))''',
    '''        if ds.get("target") == "krea2":
            # Two trainers can do Krea 2 on a small card; ship both, plus the
            # look-scores file that unlocks Fizgig's outlier warm-up headless.
            z.writestr("dataset_musubi.toml", _musubi_toml(ds, resolution))
            z.writestr("train_krea2_musubi.txt",
                       _musubi_commands(ds, len(picked), resolution))
            z.writestr("dataset_fizgig.toml", _fizgig_toml(ds, resolution))
            z.writestr("train_krea2_fizgig.txt", _fizgig_commands(ds, len(picked)))
            look = _look_scores(ds, picked, stems)
            z.writestr("images/fizgig_look_scores.json", json.dumps(look, indent=2))''',
    "zip carries the fizgig files",
)

# ── 5. the notes lead with what the source proved ───────────────────────
rep(
    '''**Trainer: kohya-ss/musubi-tuner** — it has official (experimental) Krea 2 support
(`krea2_train_network.py`, `networks.lora_krea2`) and it is the route that fits a 12–16 GB
card. `dataset_musubi.toml` and `train_krea2_musubi.txt` in this zip are ready to run.
ai-toolkit also trains Krea 2 but is heavier (~18–20 GB at 768 for LoKr; its Krea2Trainer
wrapper targets 24 GB) — use it if you have the card. ComfyUI-FluxTrainer cannot train Krea 2
at all, so `dataset_kohya.toml` here is for a Flux/SDXL target only.''',
    '''**Two trainers fit a small card, and both configs are in this zip.**

**Fizgig (shootthesound/Fizgig) — recommended.** Fully headless: its GUI only builds these
commands and runs them as subprocesses. `dataset_fizgig.toml` + `train_krea2_fizgig.txt` are
ready to run. `--quantize_4bit` puts the frozen base at ~5.6 GB with block swap OFF, and its
own docs are blunt about why that matters: *"Swapping is the slow path (4.4× the time, 4× the
CPU): quantise first, and only swap when even NF4 will not fit."* It also carries the features
nothing else has for Krea 2 — a per-image loss watch that classifies every image each epoch and
prints a **plateau banner with a best-checkpoint estimate**, per-image LR that throttles stuck
images, Qwen3-VL **auto-recaptioning** of images the loss convicts, and a **look-outlier
warm-up**. That last one reads `fizgig_look_scores.json`, which its docs say has no headless
generator — **so we write it**, from our own identity QC, into `images/`.

**kohya-ss/musubi-tuner** — official (experimental) Krea 2 support
(`krea2_train_network.py`, `networks.lora_krea2`); `dataset_musubi.toml` +
`train_krea2_musubi.txt`. Fits via fp8-scaled + block swap.

ai-toolkit also trains Krea 2 but is heavier (~18–20 GB at 768 for LoKr; its Krea2Trainer
wrapper targets 24 GB). ComfyUI-FluxTrainer cannot train Krea 2 at all, so `dataset_kohya.toml`
here is for a Flux/SDXL target only.''',
    "notes lead with fizgig",
)

assert src != orig
p.write_text(src, "utf-8")
print(f"patched {p} ({len(orig)} -> {len(src)} bytes)")
