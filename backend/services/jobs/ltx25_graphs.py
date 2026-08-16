"""LTX 2.5 API-format graph builder (v1.277.15).

Derived link-by-link from the OFFICIAL bundled templates on the updated workers
(`/templates/video_ltx2_5_t2v.json` / `video_ltx2_5_i2v.json`) — the template set
is UI-format subgraphs, so this module reproduces the exact netlist in API form.

The 2.5 distilled pipeline is TWO-PASS:
  pass 1 (base): half-resolution AV latent, 8-step ManualSigmas, euler_ancestral,
                 LTXVDualCFGGuider(video_cfg=1, audio_cfg=1)
  pass 2 (refine): spatial-upsampler x2 back to full res, 3-step ManualSigmas
                   (0.85 → 0), fixed seed 42 noise (as shipped in the template)
Audio rides along the whole way (Concat/Separate AV latent), decoded by the
audio VAE into the final CreateVideo mux.

Model-name notes (measured, not assumed):
- We stage the UNGATED mirror's files: the video VAE is
  `ltx-2.5-video-vae-conv-bf16.safetensors` (the template names the gated
  repo's `ltx-2.5-video-vae-bf16.safetensors` — same weights, "conv" repack
  that pairs with the int8-convrot transformer).
- Text encoder is the single-file `gemma4-12b-with-proj` int8 via ONE
  CLIPLoader type "ltxv" (2.3 needed DualCLIPLoader + separate projection).
- The template's gemma4_e2b prompt-enhancer branch (TextGenerateLTX2Prompt)
  is intentionally OMITTED: we draft prompts spec-verbatim, never the enhancer
  (same policy as the H3 lane).
- The i2v resize (template: ResizeImageMaskNode, a V3 dynamic-combo node that
  is awkward to drive in API format) is replaced with core
  ImageScaleToTotalPixels at an equivalent pixel budget.
"""
from __future__ import annotations

from typing import Optional

TRANSFORMER = "ltx-2.5-22b-distilled-transformer-comfy-int8-convrot.safetensors"
TEXT_ENCODER = "gemma4-12b-with-proj-ltx-2.5-comfy-int8-convrot.safetensors"
VIDEO_VAE = "ltx-2.5-video-vae-conv-bf16.safetensors"
AUDIO_VAE = "ltx-2.5-audio-vae-bf16.safetensors"
SPATIAL_UPSCALER = "ltx-2.5-latent-spatial-upscaler-x2-bf16-1.0.safetensors"

NEGATIVE_DEFAULT = "pc game, console game, video game, cartoon, childish, ugly"
SIGMAS_BASE = "1.0, 0.99375, 0.9875, 0.98125, 0.975, 0.909375, 0.725, 0.421875, 0.0"
SIGMAS_REFINE = "0.85, 0.7250, 0.4219, 0.0"


def build_ltx25_graph(kind: str, prompt: str, *,
                      width: int = 1280, height: int = 720,
                      seconds: float = 5.0, fps: int = 24,
                      seed: int = 0,
                      negative: str = NEGATIVE_DEFAULT,
                      image_name: Optional[str] = None,
                      i2v_strength: float = 0.7,
                      filename_prefix: str = "video/LTX25") -> dict:
    """kind: 't2v' or 'i2v' (i2v requires image_name, already uploaded to the
    worker's input folder). Returns an API-format graph ready for POST /prompt."""
    if kind not in ("t2v", "i2v"):
        raise ValueError(f"unknown LTX 2.5 graph kind: {kind}")
    if kind == "i2v" and not image_name:
        raise ValueError("i2v needs image_name")

    width, height = (max(32, (int(x) // 32) * 32) for x in (width, height))
    frames = int(round(float(seconds) * int(fps))) + 1

    g = {
        # ── loaders ──
        "10": {"class_type": "UNETLoader",
               "inputs": {"unet_name": TRANSFORMER, "weight_dtype": "default"}},
        "11": {"class_type": "CLIPLoader",
               "inputs": {"clip_name": TEXT_ENCODER, "type": "ltxv",
                          "device": "default"}},
        "12": {"class_type": "VAELoader", "inputs": {"vae_name": VIDEO_VAE}},
        "13": {"class_type": "VAELoader", "inputs": {"vae_name": AUDIO_VAE}},
        "14": {"class_type": "LatentUpscaleModelLoader",
               "inputs": {"model_name": SPATIAL_UPSCALER}},
        # ── conditioning ──
        "20": {"class_type": "CLIPTextEncode",
               "inputs": {"clip": ["11", 0], "text": prompt}},
        "21": {"class_type": "CLIPTextEncode",
               "inputs": {"clip": ["11", 0], "text": negative}},
        "22": {"class_type": "LTXVConditioning",
               "inputs": {"positive": ["20", 0], "negative": ["21", 0],
                          "frame_rate": float(fps)}},
        # ── guiders / samplers / sigmas ──
        "30": {"class_type": "LTXVDualCFGGuider",
               "inputs": {"model": ["10", 0], "positive": ["22", 0],
                          "negative": ["22", 1], "video_cfg": 1.0,
                          "audio_cfg": 1.0}},
        "31": {"class_type": "LTXVDualCFGGuider",
               "inputs": {"model": ["10", 0], "positive": ["22", 0],
                          "negative": ["22", 1], "video_cfg": 1.0,
                          "audio_cfg": 1.0}},
        "32": {"class_type": "KSamplerSelect",
               "inputs": {"sampler_name": "euler_ancestral"}},
        "33": {"class_type": "ManualSigmas", "inputs": {"sigmas": SIGMAS_BASE}},
        "34": {"class_type": "ManualSigmas", "inputs": {"sigmas": SIGMAS_REFINE}},
        "35": {"class_type": "RandomNoise",
               "inputs": {"noise_seed": int(seed)}},
        "36": {"class_type": "RandomNoise", "inputs": {"noise_seed": 42}},
        # ── pass 1: half-res base ──
        "40": {"class_type": "EmptyLTXVLatentVideo",
               "inputs": {"width": width // 2, "height": height // 2,
                          "length": frames, "batch_size": 1}},
        "41": {"class_type": "LTXVEmptyLatentAudio",
               "inputs": {"frames_number": frames, "frame_rate": float(fps),
                          "batch_size": 1, "audio_vae": ["13", 0]}},
        # base video latent source is patched below for i2v
        "43": {"class_type": "LTXVConcatAVLatent",
               "inputs": {"video_latent": ["40", 0], "audio_latent": ["41", 0]}},
        "44": {"class_type": "SamplerCustomAdvanced",
               "inputs": {"noise": ["35", 0], "guider": ["30", 0],
                          "sampler": ["32", 0], "sigmas": ["33", 0],
                          "latent_image": ["43", 0]}},
        "45": {"class_type": "LTXVSeparateAVLatent",
               "inputs": {"av_latent": ["44", 0]}},
        # ── pass 2: upsample x2 + refine ──
        "50": {"class_type": "LTXVLatentUpsampler",
               "inputs": {"samples": ["45", 0], "upscale_model": ["14", 0],
                          "vae": ["12", 0]}},
        # refine video latent source is patched below for i2v
        "52": {"class_type": "LTXVConcatAVLatent",
               "inputs": {"video_latent": ["50", 0], "audio_latent": ["45", 1]}},
        "53": {"class_type": "SamplerCustomAdvanced",
               "inputs": {"noise": ["36", 0], "guider": ["31", 0],
                          "sampler": ["32", 0], "sigmas": ["34", 0],
                          "latent_image": ["52", 0]}},
        "54": {"class_type": "LTXVSeparateAVLatent",
               "inputs": {"av_latent": ["53", 0]}},
        # ── decode + mux ──
        "60": {"class_type": "VAEDecodeTiled",
               "inputs": {"samples": ["54", 0], "vae": ["12", 0],
                          "tile_size": 512, "overlap": 64,
                          "temporal_size": 64, "temporal_overlap": 16}},
        "61": {"class_type": "LTXVAudioVAEDecode",
               "inputs": {"samples": ["54", 1], "audio_vae": ["13", 0]}},
        "62": {"class_type": "CreateVideo",
               "inputs": {"images": ["60", 0], "audio": ["61", 0],
                          "fps": float(fps), "bit_depth": 8}},
        "63": {"class_type": "SaveVideo",
               "inputs": {"video": ["62", 0], "filename_prefix": filename_prefix,
                          "format": "auto", "codec": "auto"}},
    }

    if kind == "i2v":
        pixel_budget = round((1536 * 1536 * (width / max(width, height))
                              * (height / max(width, height))) / 1_000_000, 2)
        g["70"] = {"class_type": "LoadImage", "inputs": {"image": image_name}}
        g["71"] = {"class_type": "ImageScaleToTotalPixels",
                   "inputs": {"image": ["70", 0], "upscale_method": "lanczos",
                              "megapixels": max(0.25, pixel_budget)}}
        g["72"] = {"class_type": "LTXVPreprocess",
                   "inputs": {"image": ["71", 0], "img_compression": 18}}
        # base pass: in-place conditioner at i2v_strength on the empty latent
        g["73"] = {"class_type": "LTXVImgToVideoInplace",
                   "inputs": {"vae": ["12", 0], "image": ["72", 0],
                              "latent": ["40", 0],
                              "strength": float(i2v_strength), "bypass": False}}
        g["43"]["inputs"]["video_latent"] = ["73", 0]
        # refine pass: full-strength in-place on the upsampled latent
        g["74"] = {"class_type": "LTXVImgToVideoInplace",
                   "inputs": {"vae": ["12", 0], "image": ["72", 0],
                              "latent": ["50", 0], "strength": 1.0,
                              "bypass": False}}
        g["52"]["inputs"]["video_latent"] = ["74", 0]

    return g
