"""v1.211.0 — ship the VERIFIED 12–16 GB Krea 2 training recipe with the dataset.

Researched 2026-08-04 after he said people are training Krea 2 on 12–16 GB.  They
are, and it flips the trainer choice:

  * **kohya-ss/musubi-tuner has official (experimental) Krea 2 support** —
    `krea2_train_network.py`, `networks.lora_krea2`, plus dedicated
    `krea2_cache_latents.py` / `krea2_cache_text_encoder_outputs.py`.
  * Pre-caching latents AND text-encoder outputs is what makes it fit: the ~8 GB
    Qwen3-VL encoder leaves the training loop entirely.
  * `--fp8_base --fp8_scaled` (K2 accepts SCALED fp8 only — plain fp8 is
    rejected) + `--blocks_to_swap N` (max 26 of 28) + `--gradient_checkpointing`.
  * MEASURED: RTX 3060 12 GB — peak ~10.5 GB, 7.2–7.8 s/step at 512², rank 16,
    blocks_to_swap 22.  RTX 4070 12 GB — rank 32, blocks_to_swap 26, ~2 h for
    2000 steps, 48 GB system RAM.
  * ai-toolkit (the v1.209 recommendation) is the HEAVIER route: ~18–20 GB at
    768 for LoKr, and the Krea2Trainer wrapper targets 24 GB.  So for a 16 GB
    fleet, musubi is the one that fits.
  * Train on RAW; musubi does not train Turbo (`--turbo_dit` is for sampling and
    is incompatible with block swap).

So the zip now carries `dataset_musubi.toml` (musubi's own format, which is NOT
the kohya one) and `train_krea2_musubi.txt` — the three commands with the flags
already set for his tier, plus the block-swap ladder and the system-RAM warning.

Exact-match patcher: every replacement asserts exactly ONE occurrence.
Usage: python patch_lora_v1211.py <path-to-lora.py>
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


# ── 1. the musubi dataset config + command sheet ─────────────────────────
rep(
    '''@router.post("/datasets/{ds_id}/export")''',
    '''def _musubi_toml(ds: dict, resolution: List[int]) -> str:
    """musubi-tuner's dataset config — a DIFFERENT format from the kohya one
    (image_directory / cache_directory, and resolution lives in [general])."""
    res = max(resolution)
    return f"""# musubi-tuner dataset config for Krea 2 (kohya-ss/musubi-tuner).
# Point image_directory at the extracted images/ folder of this zip.
[general]
resolution = [{res}, {res}]
caption_extension = ".txt"
batch_size = 1
enable_bucket = true          # this set mixes aspect ratios on purpose
bucket_no_upscale = false

[[datasets]]
image_directory = "./images"
cache_directory = "./cache/{ds.get('id')}"
num_repeats = 1
"""


def _musubi_commands(ds: dict, n: int, resolution: List[int]) -> str:
    """The three commands, with the low-VRAM flags already set.

    Every number here comes from a run someone actually reported (sources in
    docs/LORA_DATASET.md) — the block-swap ladder is the one knob to move if it
    OOMs or if there is VRAM left over."""
    res = max(resolution)
    steps = max(1000, min(n * 40, 2500))
    return f"""# ── Krea 2 LoRA — musubi-tuner (kohya-ss), the route that fits 12–16 GB ──
#
# Verified numbers people have reported:
#   RTX 3060 12 GB : peak ~10.5 GB, 7.2–7.8 s/step @512, rank 16, swap 22
#   RTX 4070 12 GB : rank 32, swap 26, ~2 h for 2000 steps, 48 GB system RAM
#
# ⚠ SYSTEM RAM, not just VRAM: block swap parks transformer blocks in CPU RAM.
#   Budget 32–64 GB. This is the requirement people miss.
# ⚠ Train on RAW. musubi does not train Turbo — you train Raw, you generate with
#   Turbo, and the LoRA transfers.
# ⚠ K2 needs SCALED fp8: --fp8_base AND --fp8_scaled together. Plain fp8 is
#   rejected on purpose (norm casting).
#
# Models needed:
#   DiT   : krea/Krea-2-Raw            -> models/krea2/raw/raw.safetensors
#   TE    : Comfy-Org qwen3vl_4b_bf16.safetensors   (the SINGLE file, not a dir)
#   VAE   : Comfy-Org qwen_image_vae.safetensors    (you already have this one)

# 1) cache the image latents (VAE leaves the training loop)
python src/musubi_tuner/krea2_cache_latents.py \\
  --dataset_config dataset_musubi.toml \\
  --vae /path/to/qwen_image_vae.safetensors \\
  --batch_size 1 --skip_existing

# 2) cache the text-encoder outputs — THIS is what makes it fit; the ~8 GB
#    Qwen3-VL encoder is never loaded during training
python src/musubi_tuner/krea2_cache_text_encoder_outputs.py \\
  --dataset_config dataset_musubi.toml \\
  --text_encoder /path/to/qwen3vl_4b_bf16.safetensors \\
  --batch_size 1 --skip_existing

# 3) train
accelerate launch --num_cpu_threads_per_process 1 --mixed_precision bf16 \\
  src/musubi_tuner/krea2_train_network.py \\
  --dit /path/to/raw.safetensors \\
  --vae /path/to/qwen_image_vae.safetensors \\
  --dataset_config dataset_musubi.toml \\
  --sdpa --mixed_precision bf16 \\
  --timestep_sampling krea2_shift --weighting_scheme none \\
  --optimizer_type adamw8bit --learning_rate 1e-4 \\
  --gradient_checkpointing \\
  --network_module networks.lora_krea2 --network_dim 32 --network_alpha 32 \\
  --fp8_base --fp8_scaled \\
  --blocks_to_swap 20 --block_swap_h2d_only --block_swap_ring_size 2 \\
  --max_data_loader_n_workers 2 --persistent_data_loader_workers \\
  --max_train_steps {steps} --save_every_n_steps 250 --seed 42 \\
  --output_dir outputs/{ds.get('id')} --output_name {ds.get('id')}

# ── the one knob: --blocks_to_swap (max 26 of 28) ────────────────────────
#   OOM?            raise it (20 -> 22 -> 24 -> 26) and/or drop resolution
#   VRAM to spare?  lower it — every swapped block costs CPU<->GPU bandwidth,
#                   which is what makes the step time, not the GPU
#   Still tight?    add --gradient_checkpointing_cpu_offload, then --split_attn,
#                   then train at {min(res, 768)} instead of {res}
#
# Timestep sampling: this set mixes aspect ratios, so krea2_shift (resolution
# aware) is correct. Training at ONE fixed size instead? use
#   --timestep_sampling shift --discrete_flow_shift 2.5
#
# Steps: {steps} for {n} images (~40/image). Likeness usually arrives between
# 500 and 1500; past ~3000 it overfits. Save every 250 and pick by eye rather
# than taking the last checkpoint.
#
# Inference: load the LoRA onto Krea 2 TURBO at strength 0.8–1.2.
"""


@router.post("/datasets/{ds_id}/export")''',
    "musubi toml + commands",
)

# ── 2. put them in the zip for a krea2 target ───────────────────────────
rep(
    '''        z.writestr("dataset_aitoolkit.yaml", _aitoolkit_yaml(ds, len(picked), resolution))
        z.writestr("dataset_kohya.toml", _kohya_toml(ds, resolution))''',
    '''        z.writestr("dataset_aitoolkit.yaml", _aitoolkit_yaml(ds, len(picked), resolution))
        z.writestr("dataset_kohya.toml", _kohya_toml(ds, resolution))
        if ds.get("target") == "krea2":
            # musubi-tuner is the route that fits 12-16 GB, and its dataset
            # config is NOT the kohya one — ship both plus the command sheet
            z.writestr("dataset_musubi.toml", _musubi_toml(ds, resolution))
            z.writestr("train_krea2_musubi.txt",
                       _musubi_commands(ds, len(picked), resolution))''',
    "zip carries the musubi files",
)

# ── 3. the notes lead with the route that actually fits ─────────────────
rep(
    '''    if target == "krea2":
        return """## Krea 2 specifics (verified 2026-08-04)''',
    '''    if target == "krea2":
        return """## Krea 2 specifics (verified 2026-08-04)

**Trainer: kohya-ss/musubi-tuner** — it has official (experimental) Krea 2 support
(`krea2_train_network.py`, `networks.lora_krea2`) and it is the route that fits a 12–16 GB
card. `dataset_musubi.toml` and `train_krea2_musubi.txt` in this zip are ready to run.
ai-toolkit also trains Krea 2 but is heavier (~18–20 GB at 768 for LoKr; its Krea2Trainer
wrapper targets 24 GB) — use it if you have the card. ComfyUI-FluxTrainer cannot train Krea 2
at all, so `dataset_kohya.toml` here is for a Flux/SDXL target only.

Reported low-VRAM runs: **RTX 3060 12 GB — peak ~10.5 GB, 7.2–7.8 s/step at 512², rank 16,
blocks_to_swap 22**; RTX 4070 12 GB — rank 32, blocks_to_swap 26, ~2 h for 2000 steps.
What makes it fit: pre-cached latents + pre-cached text-encoder outputs (the ~8 GB Qwen3-VL
encoder never enters the training loop), `--fp8_base --fp8_scaled` (K2 accepts SCALED fp8
only), block swap, gradient checkpointing. **Budget 32–64 GB of SYSTEM RAM** — swapped blocks
live there, and that is the requirement people miss.
''',
    "notes lead with musubi",
)
rep(
    '''- **VRAM:** a community Krea 2 Turbo LoRA run reported ~22 GB and ~6 hours for 20 images.
  Plan for a >=24 GB card; the 16 GB workers in this app's fleet are likely under that bar for
  training even though they render fine.
"""''',
    '''- **VRAM:** ~22 GB / ~6 h for 20 images on ai-toolkit + the Turbo adapter, versus ~10.5 GB on
  musubi-tuner with fp8 + block swap. A 16 GB card is comfortable on the musubi route and short
  on the ai-toolkit one.
"""''',
    "VRAM note corrected",
)

assert src != orig
p.write_text(src, "utf-8")
print(f"patched {p} ({len(orig)} -> {len(src)} bytes)")
