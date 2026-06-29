# Krea 2 Turbo — Usage, Prompting & Integration Guide

Krea 2 is an aesthetic-first, 12-billion-parameter open-source text-to-image
diffusion model from Krea AI. In this app it is supported as an **optional
first-pass (single-image) generator** — an alternative to Z-Image Turbo for
no-reference text-to-image renders. It is **first-pass only**: Krea 2 is not an
edit/reference model, so it never replaces FLUX.2 Klein for character
compositing (Pass 2). When a scene has character references, the app still uses
Klein.

> Status: fully integrated and selectable in Settings. As of v1.15.0 the four
> tuned V2 workflows ship in `workflows/` (SFW/NSFW × plain/Ideogram). Selecting
> Krea 2 uses them directly; if a workflow file is ever missing it safely falls
> back (NSFW → SFW file, or Krea 2 → Z-Image Turbo). The model + node files below
> must be installed on each ComfyUI server.

---

## 1. Which model to download (fp8 vs mxfp8)

Krea 2 ships as two functional variants. Use **Turbo** for production — it is an
8-step distilled checkpoint built for fast, high-quality generation. (The
**Raw/Base** checkpoint is a foundation model for training LoRAs, *not* for
direct inference — skip it unless you are training.)

Within Turbo, pick the quantization that matches the GPU on each generation
server:

| File | Format | Use on |
|------|--------|--------|
| `krea2_turbo_mxfp8.safetensors` | MXFP8 | **RTX 50-series (Blackwell)** cards |
| `krea2_turbo_fp8.safetensors` | FP8 | **RTX 40-series and older** (40xx / 30xx / 20xx) — anything that is *not* 50xx |

The difference is purely the quantization format the two GPU generations
accelerate natively: **mxfp8 is tuned for the 50xx Blackwell tensor cores**, and
**fp8 is the tensorwise format that runs best on 40xx and older**. They produce
equivalent images; the only reason to choose one over the other is the card.
Set the active file per deployment in **Settings → Single Image Generator → Krea
2 Model File** (defaults to `krea2_turbo_fp8.safetensors`).

> Because each ComfyUI server may have a different GPU, make sure the file named
> in the setting actually exists in that server's `models/diffusion_models/`
> folder. If your fleet is mixed (some 50xx, some older), standardize on one
> filename per server and keep the setting matched to the majority, or run
> per-server overrides.

### Required support files (all variants)

| File | Folder |
|------|--------|
| `krea2_turbo_fp8.safetensors` **or** `krea2_turbo_mxfp8.safetensors` | `ComfyUI/models/diffusion_models/` |
| `qwen3vl_4b_fp8_scaled.safetensors` (or `qwen3vl_4b_bf16.safetensors`) — text encoder | `ComfyUI/models/text_encoders/` |
| `qwen_image_vae.safetensors` — VAE | `ComfyUI/models/vae/` |

> Note: Krea 2's text encoder is **Qwen3-VL 4B** — a *different* file from the
> encoders this app already uses (Klein's `qwen_3_8b...`, Z-Image's `qwen_3_4b`).
> Copy it in; it does not replace the others.

### Download locations

- Official ComfyUI repacks (recommended): <https://huggingface.co/Comfy-Org/Krea-2/tree/main> — `diffusion_models/`, `text_encoders/`, `vae/`.
- Krea 2 open-source landing page: <https://www.krea.ai/krea-2-open-source>
- Krea 2 technical report: <https://www.krea.ai/blog/krea-2-technical-report>

### Custom nodes (for the ComfyUI workflow)

The Krea 2 community installer (`KREA2_ULTRA-MODELS-NODES_INSTALL-V2.bat`) adds
these. rgthree, KJNodes and ComfyUI-Manager are already used by the app's other
workflows; the Krea 2-specific ones are:

- `ComfyUI-Krea2T-Enhancer` (`capitan01R`) — **required for NSFW mode.** Patches
  the Krea2 text-fusion path on the model line; with `enabled: true` it also
  bypasses the model's built-in safety checker. Only the NSFW workflows use it.
- `ComfyUI-RBG-SmartSeedVariance` (`RamonGuthrie`) — `RBG_Smart_Seed_Variance`,
  used by all four V2 workflows on the conditioning line.
- `ComfyUI_essentials` — common dependency (may already be present).

> The installer also offers a `krea2_turbo_lora_rank_64_bf16` LoRA. This app does
> **not** use it (the Power Lora Loader is left empty), so it is optional.

---

### V2 "Ultra" workflows + SFW / NSFW mode (v1.15.0)

The app ships **four** Krea 2 workflows and picks one per render based on the
SFW toggle and whether Ideogram JSON mode is on:

| File | Mode | Safety checker |
|---|---|---|
| `KREA2_TURBO_T2I.json` | plain prompt | **SFW** (active) |
| `KREA2_TURBO_T2I_NSFW.json` | plain prompt | NSFW (bypassed) |
| `KREA2_IDEOGRAM_T2I.json` | Ideogram JSON | **SFW** (active) |
| `KREA2_IDEOGRAM_T2I_NSFW.json` | Ideogram JSON | NSFW (bypassed) |

The NSFW files insert the `ComfyUI-Krea2T-Enhancer` node between the Power Lora
Loader and the KSampler (`enabled: true`, `strength: 1.0`). The SFW files leave
the model line going straight to the sampler. Toggle in **Settings → Single
Image Generator → Krea 2 → "SFW mode (model safety checker on)"** (default ON).
If a NSFW file is missing, the dispatcher logs a warning and falls back to the
matching SFW file.

---

## 2. Recommended ComfyUI sampler settings

For **Krea 2 Turbo** (what this app uses):

| Setting | Value |
|---------|-------|
| Steps | **8** |
| CFG | **0–1 (effectively disabled)** |
| Sampler | **er_sde** |
| Scheduler | **simple** |
| Resolution | 1024–2048 (start ~1024 square, scale up once composition locks) |

(For reference, Raw/Base would use ~52 steps at CFG 3.5 — not used here.)

Because CFG is ~1, **Krea 2 has no usable negative prompt** — exactly like
Z-Image and Klein in this app. The app already strips negative prompts for
first-pass models, so nothing to configure.

---

## 3. Prompting Krea 2 — best practices

Krea 2 prompts **differently** from Klein and FLUX, and getting this right is the
single biggest quality lever. Krea was trained on short, conversational,
natural-language "user captions" and is tuned to prioritize visual harmony,
motivated lighting, and material realism over literal prompt adherence.

**Do:**

- **Write natural prose**, the way you'd brief a photographer — one flowing
  description, not a comma-separated keyword pile.
- **Lead with the subject and action**, then the setting, then the **lighting**,
  then mood, then medium/style.
- **Name a motivated light source** (window at dusk, neon sign, overcast sky, a
  single candle) and concrete **materials/textures**. Lighting and materials are
  Krea 2's strongest dimension.
- **Keep it focused.** A concise, evocative description outperforms an
  over-stuffed one — Krea 2 favors aesthetic coherence over exhaustive detail.

**Don't:**

- **No quality-booster spam** (`masterpiece, 8k, ultra-detailed, hyperrealistic,
  trending on artstation, award-winning, best quality`). These *degrade* Krea 2 —
  it already targets high aesthetic quality, and the tags read as noise.
- **No attention-weight syntax** like `(word:1.3)` or `[word]`. Krea 2 ignores it.
- **No superlative stacking** (`ultra bright, blazing, radiant, glowing`) — it
  pushes the model toward highlight clipping.
- **No reference/edit phrasing** ("Image 1", "the subject from the first image").
  Krea 2 is single-pass, text-only.

**Before / after example:**

- ❌ *Klein/SDXL-style:* `a knight, masterpiece, 8k, ultra detailed, dramatic lighting, (cinematic:1.3), trending on artstation, best quality, sharp focus`
- ✅ *Krea 2-style:* `A weary knight rests against a mossy stone wall at dusk, last light from a narrow window catching the scratched steel of his pauldron, dust hanging in the cool air, muted earthy palette, quiet and contemplative.`

The app handles this automatically: when Krea 2 is the selected first-pass
generator, the prompt enhancer uses a dedicated Krea 2 system prompt with these
rules baked in (see `KREA2_IMAGE_SYSTEM_PROMPT` in
`backend/services/llm/prompt_enhancer.py`). You can still override the system
prompt per model in Settings.

---

## 4. How Krea 2 is integrated in this app

- **Settings → Single Image Generator** has a **Krea 2 Turbo** option, plus a
  **Krea 2 Model File** picker (fp8 vs mxfp8).
- The dispatcher routes every no-reference text-to-image render (`klein_t2i`) to
  the selected first-pass generator. With Krea 2 selected, it loads
  `workflows/KREA2_TURBO_T2I.json` and overrides the diffusion model to your
  chosen file.
- **Gated activation:** Krea 2 only engages if `KREA2_TURBO_T2I.json` exists.
  Until you add your tested workflow, the app logs a one-line notice and falls
  back to Z-Image — nothing breaks.
- **Two-pass (character) scenes are unaffected** (verified): Pass 2 character
  compositing always uses Klein. If Krea 2 is selected, it paints the Pass-1
  base scene (no refs), and Klein composites the characters on top in Pass 2.

### Two-pass character compositing with Krea 2 (how it stays intact)

The character-swap pipeline is **model-agnostic for Pass 1** — it works the same
whether Pass 1 is Z-Image or Krea 2:

1. You select characters on a scene → `_apply_two_pass_to_job_params` sets
   `two_pass=true`, `two_pass_phase="base"`, stores the character ref IDs, and
   sets the Pass-1 workflow to `klein_t2i` (the no-reference placeholder).
2. At dispatch, `klein_t2i` is redirected to your selected first-pass generator
   (Krea 2 if selected and present, else Z-Image), which paints the base scene.
3. When Pass 1 finishes, the dispatcher auto-chains Pass 2. This trigger is gated
   only on `two_pass` + `two_pass_phase == "base"` — it does **not** look at which
   model painted Pass 1 — so Krea 2's output triggers Pass 2 exactly like
   Z-Image's would.
4. Pass 2 builds a `klein_Nref` job whose **slot 1 is the Pass-1 image** and
   slots 2+ are the character references, with the directed
   `TWO_PASS_COMPOSITE_SYSTEM_PROMPT` (palette/exposure preservation + character
   identity). Klein swaps the characters into the scene.

So selecting characters always produces a Klein second pass using the reference
system and the directed prompt — Krea 2 simply replaces Z-Image as the Pass-1
scene painter. (Confirmed by a full-tree code audit: no two-pass logic anywhere
keys on the Z-Image model.)
- The prompt enhancer applies Krea 2-specific prompting rules when Krea 2 is
  selected.

### Activation checklist (once your workflow is tested)

1. Copy the model files to each generation server (table in §1).
2. Restart/refresh ComfyUI on each server so it sees the new models + nodes.
3. Drop your tested Krea 2 workflows (`KREA2_TURBO_T2I.json` + the `_NSFW` /
   Ideogram variants) into the app's `workflows/` folder.
4. Restart the app (registers the workflow; the dispatcher will use it).
5. In **Settings → Single Image Generator**, choose **Krea 2 Turbo** and set the
   **Krea 2 Model File** to match each server's GPU.
6. Generate a no-reference scene and confirm the model badge reads **Krea 2
   Turbo**.

---

## Sources

- ComfyUI official Krea-2 tutorial — <https://docs.comfy.org/tutorials/image/krea/krea-2>
- Krea2 Raw/Base & Turbo model + settings overview — <https://www.stablediffusiontutorials.com/2026/06/krea2-base-turbo.html>
- Krea 2 (aesthetic-first model) overview — <https://morphic.com/resources/models/krea-2>
- Krea 2 technical report — <https://www.krea.ai/blog/krea-2-technical-report>
