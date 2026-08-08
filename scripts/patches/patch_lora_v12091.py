"""v1.209.1 — target-aware trainer configs: Krea 2 is NOT Flux (verified).

Checked online AND against his own workflows, which agree:
Krea 2 is a from-scratch 12.9B DiT by Krea AI with a **Qwen3-VL 4B text encoder**
and the **Qwen-Image VAE** — `workflows/KREA2_*.json` load exactly
`krea2_turbo_mxfp8.safetensors` + `qwen3vl_4b_fp8_scaled.safetensors` +
`qwen_image_vae.safetensors`.  So `is_flux: true` in the exported config was
wrong, and ComfyUI-FluxTrainer is the wrong trainer for this target.

What that changes:
  * the ai-toolkit YAML is now written per TARGET; flux/sdxl keep their blocks,
    krea2 gets a Krea-2 block with the verified companions and an explicit
    "paste ai-toolkit's own model block here" marker rather than a guessed
    `arch:` string — naming a field I could not verify would be exactly the
    guessing his rules forbid;
  * the kohya toml is marked flux/sdxl-only (FluxTrainer cannot train Krea 2);
  * the notes carry the Raw-vs-Turbo rule (official Krea LoRAs train on RAW and
    apply to TURBO; ostris publishes `krea2_turbo_training_adapter`, a
    de-distillation layer that lets you train ON Turbo and drop the adapter at
    inference) and the measured ~22 GB VRAM figure — his workers are 16 GB.

Exact-match patcher: every replacement asserts exactly ONE occurrence.
Usage: python patch_lora_v12091.py <path-to-lora.py>
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


# ── 1. per-target model block ────────────────────────────────────────────
rep(
    '''def _aitoolkit_yaml(ds: dict, n: int, resolution: List[int]) -> str:''',
    '''# Verified 2026-08-04 (web + his own workflows/KREA2_*.json, which load
# krea2_turbo_mxfp8 + qwen3vl_4b_fp8_scaled + qwen_image_vae):
#   Krea 2 = a from-scratch 12.9B DiT by Krea AI. NOT Flux, NOT Qwen-Image —
#   it only borrows Qwen3-VL as text encoder and the Qwen-Image VAE.
#   RAW is the un-distilled checkpoint you train on; TURBO is the distilled one
#   you generate with (8 steps, CFG off), and Krea's own LoRAs are trained on
#   Raw then applied to Turbo. ostris/krea2_turbo_training_adapter is a
#   de-distillation adapter that lets you train directly ON Turbo and drop the
#   adapter at inference.
#   Trainer: ostris/ai-toolkit (or HF diffusers). ComfyUI-FluxTrainer is
#   Flux/kohya only and cannot train this.
_MODEL_BLOCKS = {
    "flux": """      model:
        name_or_path: "black-forest-labs/FLUX.1-dev"
        is_flux: true
        quantize: true""",
    "sdxl": """      model:
        name_or_path: "stabilityai/stable-diffusion-xl-base-1.0"
        is_xl: true""",
    "krea2": """      model:
        # ⚠ Krea 2 is its own 12.9B DiT (Qwen3-VL 4B text encoder + Qwen-Image
        # VAE) — it is NOT Flux, so no is_flux flag belongs here.
        # PASTE ai-toolkit's own Krea 2 model block below (its UI writes one) —
        # the arch key is deliberately left blank rather than guessed.
        name_or_path: "PUT_YOUR_KREA2_CHECKPOINT_HERE"   # RAW to train on
        # Training ON Turbo instead? add ostris/krea2_turbo_training_adapter
        # (de-distillation layer; remove it at inference).
        quantize: true""",
    "other": """      model:
        name_or_path: "PUT_YOUR_BASE_MODEL_HERE"
        quantize: true""",
}


def _aitoolkit_yaml(ds: dict, n: int, resolution: List[int]) -> str:''',
    "_MODEL_BLOCKS",
)

rep(
    '''      model:
        name_or_path: "PUT_YOUR_BASE_MODEL_HERE"
        is_flux: true
        quantize: true
      sample:''',
    '''{_MODEL_BLOCKS.get(ds.get("target"), _MODEL_BLOCKS["other"])}
      sample:''',
    "yaml uses the target block",
)

rep(
    '''    res = ", ".join(str(r) for r in resolution)
    return f"""# ai-toolkit config — edit model.name_or_path and the folder path, then:''',
    '''    res = ", ".join(str(r) for r in resolution)
    return f"""# ai-toolkit config for target: {ds.get('target')}
# Edit the model block and the folder path, then:''',
    "yaml header names the target",
)

# ── 2. kohya toml is flux/sdxl only ─────────────────────────────────────
rep(
    '''    return f"""# kohya / ComfyUI-FluxTrainer dataset config''',
    '''    return f"""# kohya / ComfyUI-FluxTrainer dataset config — FLUX and SDXL only.
# Krea 2 cannot be trained by FluxTrainer (it is a separate 12.9B DiT with a
# Qwen3-VL text encoder and the Qwen-Image VAE); use dataset_aitoolkit.yaml.''',
    "toml scope note",
)

# ── 3. the notes carry the target-specific truth ────────────────────────
rep(
    '''Target model noted for this set: **{ds.get('target')}**. Train the LoRA against the SAME base
checkpoint you will generate with — a LoRA trained on a different base drifts.
"""''',
    '''Target model noted for this set: **{ds.get('target')}**. Train the LoRA against the SAME base
checkpoint you will generate with — a LoRA trained on a different base drifts.

{_target_notes(ds.get('target'))}"""''',
    "notes call the target section",
)

rep(
    '''def _aitoolkit_yaml(ds: dict, n: int, resolution: List[int]) -> str:
    trig = ds.get("trigger", "sks")''',
    '''def _target_notes(target: str) -> str:
    if target == "krea2":
        return """## Krea 2 specifics (verified 2026-08-04)

Krea 2 is its OWN 12.9B diffusion transformer — not Flux, not Qwen-Image. It borrows a
**Qwen3-VL 4B text encoder** and the **Qwen-Image VAE** (which is exactly what the app's
`workflows/KREA2_*.json` load). Two consequences:

- **Trainer:** ostris/ai-toolkit (or HF diffusers). ComfyUI-FluxTrainer / kohya cannot train
  it — `dataset_kohya.toml` in this zip is for a Flux or SDXL target only.
- **Raw vs Turbo:** RAW is the un-distilled checkpoint meant for fine-tuning; TURBO is the
  distilled one you generate with (8 steps, CFG off). Krea's own LoRAs are trained on Raw and
  applied to Turbo. To train directly on Turbo instead, add ostris'
  `krea2_turbo_training_adapter` — a de-distillation layer you remove at inference — otherwise
  the distillation degrades as you train.
- **VRAM:** a community Krea 2 Turbo LoRA run reported ~22 GB and ~6 hours for 20 images.
  Plan for a >=24 GB card; the 16 GB workers in this app's fleet are likely under that bar for
  training even though they render fine.
"""
    if target == "flux":
        return """## FLUX specifics
Either trainer works: ai-toolkit with the config in this zip, or ComfyUI-FluxTrainer with
`dataset_kohya.toml` (TrainDatasetGeneralConfig -> TrainDatasetAdd -> InitFluxLoRATraining ->
FluxTrainLoop -> FluxTrainSave).
"""
    return ""


def _aitoolkit_yaml(ds: dict, n: int, resolution: List[int]) -> str:
    trig = ds.get("trigger", "sks")''',
    "_target_notes",
)

assert src != orig
p.write_text(src, "utf-8")
print(f"patched {p} ({len(orig)} -> {len(src)} bytes)")
