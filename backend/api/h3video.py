"""🎬 MiniMax H3 Video Lab (v1.275.0) — every H3 mode the ultra workflows expose.

Programmatic API-format graphs distilled from Lorenzo's two tempworkflows/
MINIMAX_H3_ULTRA*.json (graph-UI format, gitignored). One backend builder per
mode, rendered DIRECTLY on a chosen worker box (ComfyUI :8188), because the
boxes hold the H3 models and the render is long — jobs are tracked here and
polled by the UI.

Modes (mirrors the ultra workflow's section groups):
  t2v         MiniMaxH3ImageToVideo with no frames        (TEXT TO VIDEO group)
  i2v         + first_frame                               (IMAGE TO VIDEO group)
  first_last  + first_frame + last_frame                  (LAST FRAME group on)
  last_frame  + last_frame only
  ref2v       MiniMaxH3ReferenceToVideo — up to 9 ref images, 3 ref videos
              (each optionally contributing its soundtrack), 3 standalone
              audios                                      (REFERENCES TO VIDEO)

Speed options (exactly the workflow's toggle groups):
  turbo=True     Turbo path: the Lightx2v 8-step v1.0 turbo lora @1.0,
                 euler + beta/8 steps (distilled AT 8 NFE — v1.277.8).
  turbo=False    res_multistep + simple/20 steps (the non-turbo workflow).
  spectrum=True  SPECTRUM SPEED ENHANCER subgraph (SigmaShift 12.19/3.0 →
                 SpectrumApplyMiniMaxH3) — quality may suffer; default OFF.
  PathchSageAttentionKJ groups are NEVER emitted: every box already launches
  with --use-sage-attention (the workflow notes say patch OR flag, not both).

Facts measured from the boxes' /object_info (2026-08-09, all 3 identical):
  ref_images ≤ 9, ref_videos ≤ 3 (2-15 s @24 fps), ref_video_audios ≤ 3,
  ref_audios ≤ 3, ref_image_size ∈ {match, max} ('max' = 2048px identity
  fidelity, several times slower). length: min 5, step 17, trained ~124-362
  frames (~5-15 s). Frame formula (ComfyMath in the workflow):
  f = max(5, round(sec*24)); f += (5 - f%17) % 17.

Default output: 720p (0.9 MP → 1280×736 @16:9 — the workflow note's marked
row). ⬆ Upscale stage: the LTX 2.3 VIDEO ENHANCER UPSCALER distilled from
tempworkflows/LTX-2-3_ULTRA_WORKFLOW-V3.json — lanczos to max side, tiled VAE
encode, 3-step ManualSigmas refine (0.909→0) on the 22B GGUF with the ic
detailer (0.9) + distilled (0.6) loras, source latents as guiding latents,
spatio-temporal tiled decode, source audio passed through at source fps.
"""
from __future__ import annotations

import io
import json
import logging
import mimetypes
import threading
import time
import urllib.parse
import urllib.request
import uuid
from math import sqrt
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import (APIRouter, Depends, File, Form, HTTPException, Request,
                     UploadFile)
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings as cfg
from backend.database.database import get_session

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/h3", tags=["h3video"])

# import-time roots (standing rule: cfg.project_dir is DB-overridden later)
_H3_DIR = Path(cfg.project_dir) / "_libraries" / "h3video"
_UP_DIR = _H3_DIR / "uploads"
_VID_DIR = _H3_DIR / "videos"
_JOBS_FP = _H3_DIR / "jobs.json"
_DOCS_DIR = Path(__file__).resolve().parents[2] / "docs"

_JOBS: Dict[str, dict] = {}
_THREADS: Dict[str, threading.Thread] = {}
_LOCK = threading.Lock()

# ── model files (verified present on all 3 boxes via /object_info) ──────────
UNET_FL2VA = "minimax_h3_fl2va_pruned_int8_convrot.safetensors"
UNET_REF2VA = "minimax_h3_ref2va_pruned_int8_convrot.safetensors"
CLIP_QWEN = "qwen3vl_32b_minimax_h3_int8_convrot.safetensors"
VAE_VIDEO = "minimax_h3_video_vae_fp16.safetensors"
VAE_AUDIO = "minimax_h3_audio_vae_fp32.safetensors"
# v1.277.8 — Lightx2v/ModelTC Turbo v1.0 (2026-08-11), DISTILLED AT 8 NFE —
# made for exactly the 8 steps the turbo path samples. Replaces the v0.1-era
# 4-step ckpt500 preview that was being run at 8 steps. Installed on all three
# boxes via scripts/install_h3_turbo_v1.py (verified in each ComfyUI listing).
LORA_TURBO = "minimax_h3_fl2v_turbo_8step_v1.0_comfyui_bf16.safetensors"
LORA_TURBO_OLD = "minimax_h3_turbo_4step_ckpt500_comfyui_pruned.safetensors"

# LTX 2.3 upscale stage (tempworkflows/LTX-2-3_ULTRA_WORKFLOW-V3.json, the
# VIDEO ENHANCER UPSCALER group — all files verified on the boxes 2026-08-09)
LTX_UNET_GGUF = "ltx-2.3-22b-dev-Q8_0.gguf"
LTX_CLIP_1 = "gemma_3_12B_it_fp4_mixed.safetensors"
LTX_CLIP_2 = "ltx-2.3_text_projection_bf16.safetensors"
LTX_VAE_VIDEO = "LTX23_video_vae_bf16.safetensors"
LTX_LORA_DETAILER = ("ltx-2-19b-ic-lora-detailer.safetensors", 0.9)
LTX_LORA_DISTILL = ("ltx-2.3-22b-distilled-lora-384-1.1.safetensors", 0.6)
LTX_UPSCALE_SIGMAS = "0.909375, 0.725, 0.421875, 0.0"

MAX_REF_IMAGES, MAX_REF_VIDEOS, MAX_REF_AUDIOS = 9, 3, 3

# 16:9 rows straight from the workflow's Size Settings Reference table
_TABLE_169 = {0.4: (864, 480), 0.9: (1280, 736), 2.0: (1920, 1088)}
RES_PRESETS = {"480p": 0.4, "720p": 0.9, "1080p": 2.0}


def _frames(seconds: float) -> int:
    f = max(5, round(float(seconds) * 24))
    return f + (5 - (f % 17)) % 17


def _ltx_frames(n: int) -> int:
    """What the LTX 2.3 upscaler will hand back for an n-frame clip.

    LTX's VAE compresses time by 8, so it can only represent frame counts of
    the form 8k+1 and floors to the largest one that fits. Measured 2026-08-09:
    a 124-frame H3 render came back as 121 (= 8*15+1), losing 3 frames off the
    TAIL along with the matching slice of audio."""
    return 8 * ((max(1, int(n)) - 1) // 8) + 1


def _frames_for_upscale(target: int) -> int:
    """The H3-legal frame count to RENDER so that, after the upscaler floors it
    to 8k+1, at least `target` frames survive.

    H3 wants f%17==5 and LTX wants f=8k+1; the two agree only every 136 frames
    (73, 209, 345, ...), so snapping the user's duration to the shared lattice
    would mean 5 seconds simply does not exist. Instead: render one H3 step
    longer as needed and trim back afterwards. This is already the house
    pattern — `video_tail` + `trim_video()` do exactly this for LTX overshoot
    elsewhere in the app."""
    f = int(target)
    for _ in range(64):                      # 64 * 17 frames is ~45s of video
        if _ltx_frames(f) >= target:
            return f
        f += 17
    return f


def _dims(preset: str, aspect: str) -> tuple[int, int]:
    mp = RES_PRESETS.get(preset, 0.9)
    if aspect == "16:9" and mp in _TABLE_169:
        return _TABLE_169[mp]
    if aspect == "9:16" and mp in _TABLE_169:
        w, h = _TABLE_169[mp]
        return h, w
    ar = {"16:9": 16 / 9, "9:16": 9 / 16, "1:1": 1.0}.get(aspect, 16 / 9)
    px = mp * 1_000_000
    w = int(round(sqrt(px * ar) / 32)) * 32
    h = int(round((px / max(w, 32)) / 32)) * 32
    return max(w, 32), max(h, 32)


def _dims_from_image(fp: Path, preset: str) -> tuple[int, int]:
    """IMAGE SIZE group behaviour: scale the first frame's aspect to the
    target megapixels, multiples of 32 (never trusting the raw file dims)."""
    from PIL import Image
    with Image.open(fp) as im:
        iw, ih = im.size
    mp = RES_PRESETS.get(preset, 0.9)
    px = mp * 1_000_000
    w = int(round(sqrt(px * iw / max(ih, 1)) / 32)) * 32
    h = int(round((px / max(w, 32)) / 32)) * 32
    return max(w, 32), max(h, 32)


# ── tiny HTTP helpers (same shape as forge's direct-host path) ───────────────
def _jget(url: str, timeout: float = 30.0):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def _jpost(url: str, body: dict, timeout: float = 120.0):
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"),
                                 method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def _upload_to_box(host: str, filename: str, data: bytes) -> str:
    """POST /upload/image (ComfyUI stores ANY file type into input/ — VHS and
    LoadAudio read from the same folder). Returns the stored name."""
    boundary = f"----rbmn{uuid.uuid4().hex}"
    ctype = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    body = io.BytesIO()
    for name, val in (("overwrite", "true"), ("type", "input")):
        body.write((f"--{boundary}\r\nContent-Disposition: form-data; "
                    f"name=\"{name}\"\r\n\r\n{val}\r\n").encode())
    body.write((f"--{boundary}\r\nContent-Disposition: form-data; "
                f"name=\"image\"; filename=\"{filename}\"\r\n"
                f"Content-Type: {ctype}\r\n\r\n").encode())
    body.write(data)
    body.write(f"\r\n--{boundary}--\r\n".encode())
    req = urllib.request.Request(
        f"http://{host}:8188/upload/image", data=body.getvalue(), method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    with urllib.request.urlopen(req, timeout=300) as r:
        out = json.loads(r.read().decode("utf-8", "replace"))
    return out.get("name") or filename


# ── uploads store ────────────────────────────────────────────────────────────
def _up_meta_fp() -> Path:
    return _UP_DIR / "uploads.json"


def _uploads() -> Dict[str, dict]:
    if _up_meta_fp().exists():
        try:
            return json.loads(_up_meta_fp().read_text("utf-8"))
        except Exception:  # noqa: BLE001
            pass
    return {}


def _uploads_save(d: Dict[str, dict]) -> None:
    _UP_DIR.mkdir(parents=True, exist_ok=True)
    _up_meta_fp().write_text(json.dumps(d, indent=1), "utf-8")


def _upload_path(fid: str, meta: dict) -> Path:
    return _UP_DIR / f"{fid}{meta.get('ext') or ''}"


# ── jobs store ───────────────────────────────────────────────────────────────
def _jobs_load() -> None:
    if _JOBS_FP.exists():
        try:
            _JOBS.update(json.loads(_JOBS_FP.read_text("utf-8")))
        except Exception:  # noqa: BLE001
            pass


def _jobs_save() -> None:
    _H3_DIR.mkdir(parents=True, exist_ok=True)
    with _LOCK:
        _JOBS_FP.write_text(json.dumps(_JOBS, indent=1), "utf-8")


_jobs_load()


# ── workers (the Settings worker registry; ComfyUI side, port 8188) ─────────
def _workers() -> List[dict]:
    from backend.api.lora_train import _helpers_list
    return [{"id": h.get("id"), "name": h.get("name") or h.get("id"),
             "host": h.get("host"), "is_trainer": bool(h.get("is_trainer"))}
            for h in _helpers_list() if h.get("host")]


def _worker(wid: Optional[str]) -> dict:
    ws = _workers()
    if not ws:
        raise HTTPException(409, "No workers in Settings → Worker Helpers")
    if wid:
        for w in ws:
            if w["id"] == wid:
                return w
        raise HTTPException(404, f"worker '{wid}' not in the registry")
    return next((w for w in ws if w["is_trainer"]), ws[0])


# ── graph builder ────────────────────────────────────────────────────────────
def _spectrum_nodes(g: dict, model_ref: list) -> list:
    """SPECTRUM SPEED ENHANCER subgraph, instance values from the ultra
    workflow (shift 12.191/3.0, blend .5, degree 4, warmup 5)."""
    g["6"] = {"class_type": "MiniMaxH3SigmaShift",
              "inputs": {"model": model_ref,
                         "shift_video": 12.191111450195313, "shift_audio": 3.0},
              "_meta": {"title": "SPECTRUM sigma shift"}}
    g["7"] = {"class_type": "SpectrumApplyMiniMaxH3",
              "inputs": {"model": ["6", 0], "enabled": True,
                         "blend_weight": 0.5, "degree": 4, "ridge_lambda": 0.1,
                         "window_size": 2.0, "flex_window": 0.75,
                         "warmup_steps": 5, "tail_actual_steps": 1,
                         "max_history": 8, "debug": False,
                         "history_storage": "system_ram",
                         "bootstrap_first_forecast": True,
                         "anchor_residual_feedback": False,
                         "selective_rollback_correction": False,
                         "offline_smoothing_replay": True,
                         "audio_blend_weight": 0.0},
              "_meta": {"title": "SPECTRUM speed enhancer"}}
    return ["7", 0]


def _build_graph(mode: str, prompt: str, w: int, h: int, frames: int,
                 seed: int, turbo: bool, spectrum: bool,
                 first_name: Optional[str], last_name: Optional[str],
                 ref_imgs: List[str], ref_vids: List[dict],
                 ref_auds: List[str], ref_image_size: str,
                 prefix: str, draft: bool = False) -> dict:
    g: dict = {}
    unet = UNET_REF2VA if mode == "ref2v" else UNET_FL2VA
    g["1"] = {"class_type": "UNETLoader",
              "inputs": {"unet_name": unet, "weight_dtype": "default"},
              "_meta": {"title": "H3 UNET"}}
    g["2"] = {"class_type": "CLIPLoader",
              "inputs": {"clip_name": CLIP_QWEN, "type": "minimax",
                         "device": "default"},
              "_meta": {"title": "H3 QWEN3-VL text encoder"}}
    g["3"] = {"class_type": "VAELoader", "inputs": {"vae_name": VAE_VIDEO}}
    g["4"] = {"class_type": "VAELoader", "inputs": {"vae_name": VAE_AUDIO}}
    model_ref: list = ["1", 0]
    if turbo:
        g["5"] = {"class_type": "LoraLoaderModelOnly",
                  "inputs": {"lora_name": LORA_TURBO, "strength_model": 1.0,
                             "model": model_ref},
                  "_meta": {"title": "H3 turbo 8-step v1.0 lora"}}
        model_ref = ["5", 0]
    if spectrum:
        model_ref = _spectrum_nodes(g, model_ref)

    g["8"] = {"class_type": "KSamplerSelect",
              "inputs": {"sampler_name": "euler" if turbo else "res_multistep"}}
    # 🏃 draft (v1.277.9): the v1.0 8-step lora is documented to also run at 4
    # steps — roughly half the sampling time for a rougher look. Testing knob,
    # only meaningful on the turbo path.
    g["9"] = {"class_type": "BasicScheduler",
              "inputs": {"model": model_ref,
                         "scheduler": "beta" if turbo else "simple",
                         "steps": (4 if draft else 8) if turbo else 20,
                         "denoise": 1.0}}
    g["10"] = {"class_type": "RandomNoise", "inputs": {"noise_seed": seed}}

    if mode == "ref2v":
        cond: dict = {"clip": ["2", 0], "vae": ["3", 0], "audio_vae": ["4", 0],
                      "prompt": prompt, "width": w, "height": h,
                      "length": frames, "ref_image_size": ref_image_size}
        for i, name in enumerate(ref_imgs[:MAX_REF_IMAGES]):
            nid = str(30 + i)
            g[nid] = {"class_type": "LoadImage", "inputs": {"image": name},
                      "_meta": {"title": f"REFERENCE IMAGE {i}"}}
            cond[f"ref_images.ref_image_{i}"] = [nid, 0]
        for i, rv in enumerate(ref_vids[:MAX_REF_VIDEOS]):
            nid = str(40 + i)
            g[nid] = {"class_type": "VHS_LoadVideo",
                      "inputs": {"video": rv["name"], "force_rate": 0,
                                 "custom_width": 0, "custom_height": 0,
                                 "frame_load_cap": 0, "skip_first_frames": 0,
                                 "select_every_nth": 1, "format": "AnimateDiff"},
                      "_meta": {"title": f"REFERENCE VIDEO {i}"}}
            cond[f"ref_videos.ref_video_{i}"] = [nid, 0]
            if rv.get("use_audio"):
                cond[f"ref_video_audios.ref_video_audio_{i}"] = [nid, 2]
        for i, name in enumerate(ref_auds[:MAX_REF_AUDIOS]):
            nid = str(50 + i)
            g[nid] = {"class_type": "LoadAudio", "inputs": {"audio": name},
                      "_meta": {"title": f"REFERENCE AUDIO {i}"}}
            cond[f"ref_audios.ref_audio_{i}"] = [nid, 0]
        g["11"] = {"class_type": "MiniMaxH3ReferenceToVideo", "inputs": cond,
                   "_meta": {"title": "H3 REFERENCES conditioning"}}
    else:
        cond = {"clip": ["2", 0], "vae": ["3", 0], "prompt": prompt,
                "width": w, "height": h, "length": frames}
        if first_name:
            g["20"] = {"class_type": "LoadImage",
                       "inputs": {"image": first_name},
                       "_meta": {"title": "FIRST FRAME"}}
            cond["first_frame"] = ["20", 0]
        if last_name:
            g["21"] = {"class_type": "LoadImage",
                       "inputs": {"image": last_name},
                       "_meta": {"title": "LAST FRAME"}}
            g["22"] = {"class_type": "ImageScaleToTotalPixels",
                       "inputs": {"image": ["21", 0],
                                  "upscale_method": "nearest-exact",
                                  "megapixels": 1.0, "resolution_steps": 32},
                       "_meta": {"title": "LAST FRAME scale (workflow's)"}}
            cond["last_frame"] = ["22", 0]
        g["11"] = {"class_type": "MiniMaxH3ImageToVideo", "inputs": cond,
                   "_meta": {"title": "H3 TEXT/IMAGE/FIRST-LAST conditioning"}}

    g["12"] = {"class_type": "BasicGuider",
               "inputs": {"model": model_ref, "conditioning": ["11", 0]}}
    g["13"] = {"class_type": "SamplerCustomAdvanced",
               "inputs": {"noise": ["10", 0], "guider": ["12", 0],
                          "sampler": ["8", 0], "sigmas": ["9", 0],
                          "latent_image": ["11", 1]}}
    g["14"] = {"class_type": "VAEDecode",
               "inputs": {"samples": ["13", 0], "vae": ["3", 0]}}
    g["15"] = {"class_type": "VAEDecodeAudio",
               "inputs": {"samples": ["13", 0], "vae": ["4", 0]}}
    g["16"] = {"class_type": "VHS_VideoCombine",
               "inputs": {"images": ["14", 0], "audio": ["15", 0],
                          "frame_rate": 24, "loop_count": 0,
                          "filename_prefix": f"RBMN-H3/{prefix}",
                          "format": "video/h264-mp4", "pix_fmt": "yuv420p",
                          "crf": 19, "save_metadata": True,
                          "trim_to_audio": False, "pingpong": False,
                          "save_output": True},
               "_meta": {"title": "H3 combine 24fps"}}
    return g


def _build_upscale_graph(video_name: str, largest: int, prompt: str = "") -> dict:
    """The LTX 2.3 VIDEO ENHANCER UPSCALER, verbatim: lanczos to `largest` max
    side → tiled VAE encode → 3-step ManualSigmas refine on the 22B GGUF with
    detailer(0.9)+distilled(0.6) loras, the source latents feeding BOTH
    `latents` and `optional_guiding_latents` (guiding_strength 1.0) → spatio-
    temporal tiled decode → recombine at the source fps with the source audio."""
    g: dict = {}
    g["1"] = {"class_type": "VHS_LoadVideo",
              "inputs": {"video": video_name, "force_rate": 0,
                         "custom_width": 0, "custom_height": 0,
                         "frame_load_cap": 0, "skip_first_frames": 0,
                         "select_every_nth": 1, "format": "LTXV"},
              "_meta": {"title": "source video"}}
    g["2"] = {"class_type": "UnetLoaderGGUF",
              "inputs": {"unet_name": LTX_UNET_GGUF},
              "_meta": {"title": "LTX 2.3 22B GGUF"}}
    g["3"] = {"class_type": "DualCLIPLoader",
              "inputs": {"clip_name1": LTX_CLIP_1, "clip_name2": LTX_CLIP_2,
                         "type": "ltxv", "device": "default"}}
    g["4"] = {"class_type": "VAELoaderKJ",
              "inputs": {"vae_name": LTX_VAE_VIDEO, "device": "main_device",
                         "weight_dtype": "bf16"}}
    g["5"] = {"class_type": "LoraLoaderModelOnly",
              "inputs": {"lora_name": LTX_LORA_DETAILER[0],
                         "strength_model": LTX_LORA_DETAILER[1],
                         "model": ["2", 0]},
              "_meta": {"title": "ic detailer 0.9"}}
    g["6"] = {"class_type": "LoraLoaderModelOnly",
              "inputs": {"lora_name": LTX_LORA_DISTILL[0],
                         "strength_model": LTX_LORA_DISTILL[1],
                         "model": ["5", 0]},
              "_meta": {"title": "distilled 384 0.6"}}
    g["7"] = {"class_type": "VHS_VideoInfo",
              "inputs": {"video_info": ["1", 3]}}
    g["8"] = {"class_type": "ImageScaleToMaxDimension",
              "inputs": {"image": ["1", 0], "upscale_method": "lanczos",
                         "largest_size": largest}}
    g["9"] = {"class_type": "VAEEncodeTiled",
              "inputs": {"pixels": ["8", 0], "vae": ["4", 0],
                         "tile_size": 512, "overlap": 64,
                         "temporal_size": 500, "temporal_overlap": 4}}
    g["10"] = {"class_type": "CLIPTextEncode",
               "inputs": {"clip": ["3", 0], "text": prompt or ""}}
    g["11"] = {"class_type": "LTXVConditioning",
               "inputs": {"positive": ["10", 0], "negative": ["10", 0],
                          "frame_rate": ["7", 0]}}
    g["12"] = {"class_type": "CFGGuider",
               "inputs": {"model": ["6", 0], "positive": ["11", 0],
                          "negative": ["11", 1], "cfg": 1.0}}
    g["13"] = {"class_type": "RandomNoise", "inputs": {"noise_seed": 0}}
    g["14"] = {"class_type": "KSamplerSelect",
               "inputs": {"sampler_name": "euler"}}
    g["15"] = {"class_type": "ManualSigmas",
               "inputs": {"sigmas": LTX_UPSCALE_SIGMAS}}
    g["16"] = {"class_type": "LTXVLoopingSampler",
               "inputs": {"model": ["6", 0], "vae": ["4", 0],
                          "noise": ["13", 0], "sampler": ["14", 0],
                          "sigmas": ["15", 0], "guider": ["12", 0],
                          "latents": ["9", 0],
                          "optional_guiding_latents": ["9", 0],
                          "temporal_tile_size": 56, "temporal_overlap": 24,
                          "guiding_strength": 1.0,
                          "temporal_overlap_cond_strength": 0.5,
                          "cond_image_strength": 1.0,
                          "horizontal_tiles": 1, "vertical_tiles": 1,
                          "spatial_overlap": 1,
                          # v1.275.3: /object_info calls these four OPTIONAL, but
                          # LTXVLoopingSampler.sample() takes adain_factor as a
                          # positional arg with no default — omitting it raised
                          # "missing 1 required positional argument" AT RUNTIME,
                          # after validation passed. The source workflow's own
                          # widget values are 0 / 0 / 1000 / "0"; send all four.
                          "adain_factor": 0.0,
                          "guiding_start_step": 0,
                          "guiding_end_step": 1000,
                          "optional_cond_image_indices": "0"}}
    g["17"] = {"class_type": "LTXVSpatioTemporalTiledVAEDecode",
               "inputs": {"vae": ["4", 0], "latents": ["16", 0],
                          "spatial_tiles": 4, "spatial_overlap": 4,
                          "temporal_tile_length": 48, "temporal_overlap": 8,
                          "last_frame_fix": False, "working_device": "auto",
                          "working_dtype": "auto"}}
    g["18"] = {"class_type": "VHS_VideoCombine",
               "inputs": {"images": ["17", 0], "audio": ["1", 2],
                          "frame_rate": ["7", 0], "loop_count": 0,
                          "filename_prefix": "RBMN-H3/MiMx-UP",
                          "format": "video/h264-mp4", "pix_fmt": "yuv420p",
                          "crf": 19, "save_metadata": True,
                          "trim_to_audio": False, "pingpong": False,
                          "save_output": True},
               "_meta": {"title": "upscaled combine"}}
    return g


def _trim_to_frames(fp: Path, want: Optional[int]) -> Optional[dict]:
    """Cut a finished mp4 back to exactly `want` frames, audio included.

    v1.275.7. The other half of `_frames_for_upscale`: we deliberately rendered
    long so the upscaler's 8k+1 flooring could not eat the tail, and this puts
    the clip back to the length that was actually asked for. Re-encodes rather
    than stream-copying because a frame-exact cut cannot land on a keyframe by
    luck. Returns None when there is nothing to do, and NEVER raises — a clip
    that failed to trim is still a clip, and losing it to a tidying step would
    be the worst possible trade."""
    if not want or not fp.exists():
        return None
    try:
        import subprocess
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-count_frames", "-show_entries", "stream=nb_read_frames,r_frame_rate",
             "-of", "json", str(fp)],
            capture_output=True, text=True, timeout=300)
        info = json.loads(probe.stdout or "{}").get("streams", [{}])[0]
        have = int(info.get("nb_read_frames") or 0)
        num, _, den = (info.get("r_frame_rate") or "24/1").partition("/")
        fps = float(num) / float(den or 1)
        if have <= int(want) or fps <= 0:
            return {"trimmed": False, "frames": have, "wanted": int(want),
                    "note": "nothing to cut"}
        dur = int(want) / fps
        out = fp.with_suffix(".trim.mp4")
        r = subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-i", str(fp),
             "-frames:v", str(int(want)), "-t", f"{dur:.6f}",
             "-c:v", "libx264", "-crf", "17", "-pix_fmt", "yuv420p",
             "-c:a", "aac", "-b:a", "192k", str(out)],
            capture_output=True, text=True, timeout=1800)
        if r.returncode != 0 or not out.exists():
            logger.warning("h3 trim failed (%s), keeping the untrimmed clip: %s",
                           r.returncode, (r.stderr or "")[-400:])
            return {"trimmed": False, "frames": have, "wanted": int(want),
                    "error": (r.stderr or "")[-200:]}
        out.replace(fp)
        return {"trimmed": True, "from_frames": have, "frames": int(want),
                "fps": round(fps, 3), "duration_s": round(dur, 3)}
    except Exception as e:  # noqa: BLE001 — tidying must never destroy a render
        logger.warning("h3 trim skipped: %s", e)
        return {"trimmed": False, "error": str(e)[:200]}


# ── job runner ───────────────────────────────────────────────────────────────
def _set(jid: str, **kw) -> None:
    with _LOCK:
        _JOBS.get(jid, {}).update(kw)
    _jobs_save()


def _run_job(jid: str) -> None:
    j = _JOBS[jid]
    host = j["host"]
    base = f"http://{host}:8188"
    try:
        r = _jpost(f"{base}/prompt", {"prompt": j.pop("_graph")})
        if r.get("error") or r.get("node_errors"):
            raise RuntimeError("box rejected the graph: " + json.dumps(
                {k: r.get(k) for k in ("error", "node_errors")})[:800])
        pid = r.get("prompt_id")
        if not pid:
            raise RuntimeError("box returned no prompt_id")
        _set(jid, prompt_id=pid, status="running")
        t0 = time.time()
        timeout = float(j.get("timeout_s") or 5400)
        while time.time() - t0 < timeout:
            time.sleep(5.0)
            try:
                h = _jget(f"{base}/history/{pid}", timeout=20).get(pid)
            except Exception:  # noqa: BLE001
                continue
            _set(jid, elapsed_s=round(time.time() - t0, 1))
            if not h:
                continue
            st = h.get("status") or {}
            if st.get("status_str") == "error":
                raise RuntimeError("render error: "
                                   + json.dumps(st.get("messages"))[:800])
            vids = [v for o in (h.get("outputs") or {}).values()
                    for v in (o.get("gifs") or o.get("videos") or [])]
            if vids:
                _set(jid, status="downloading")
                v = vids[-1]
                url = (f"{base}/view?filename={urllib.parse.quote(v['filename'])}"
                       f"&subfolder={urllib.parse.quote(v.get('subfolder') or '')}"
                       f"&type={v.get('type') or 'output'}")
                _VID_DIR.mkdir(parents=True, exist_ok=True)
                dest = _VID_DIR / f"{jid}.mp4"
                with urllib.request.urlopen(url, timeout=600) as resp:
                    dest.write_bytes(resp.read())
                trimmed = _trim_to_frames(dest, j.get("trim_to_frames"))
                _set(jid, status="done", video=dest.name,
                     box_file=v["filename"], trimmed=trimmed,
                     elapsed_s=round(time.time() - t0, 1))
                return
        raise TimeoutError(f"no result after {int(timeout)}s")
    except Exception as e:  # noqa: BLE001
        logger.exception("h3 job %s failed", jid)
        _set(jid, status="error", error=str(e)[:1000])
    finally:
        _THREADS.pop(jid, None)


def _reconcile(j: dict) -> None:
    """A backend restart orphans running jobs — one history check settles them."""
    jid = j["id"]
    if j.get("status") not in ("queued", "running", "downloading"):
        return
    if jid in _THREADS:
        return
    pid, host = j.get("prompt_id"), j.get("host")
    if not pid or not host:
        _set(jid, status="error", error="backend restarted before submission")
        return
    try:
        h = _jget(f"http://{host}:8188/history/{pid}", timeout=10).get(pid)
        if h:
            vids = [v for o in (h.get("outputs") or {}).values()
                    for v in (o.get("gifs") or o.get("videos") or [])]
            if vids:
                v = vids[-1]
                url = (f"http://{host}:8188/view?"
                       f"filename={urllib.parse.quote(v['filename'])}"
                       f"&subfolder={urllib.parse.quote(v.get('subfolder') or '')}"
                       f"&type={v.get('type') or 'output'}")
                _VID_DIR.mkdir(parents=True, exist_ok=True)
                dest = _VID_DIR / f"{jid}.mp4"
                with urllib.request.urlopen(url, timeout=600) as resp:
                    dest.write_bytes(resp.read())
                _set(jid, status="done", video=dest.name,
                     box_file=v["filename"])
                return
            st = h.get("status") or {}
            if st.get("status_str") == "error":
                _set(jid, status="error",
                     error=json.dumps(st.get("messages"))[:500])
                return
        # still queued/running on the box — leave it, UI keeps polling
    except Exception:  # noqa: BLE001
        pass


# ── API ──────────────────────────────────────────────────────────────────────
@router.get("/overview")
async def overview():
    return {
        "workers": _workers(),
        "modes": [
            {"key": "t2v", "name": "📝 Text → Video"},
            {"key": "i2v", "name": "🖼 Image → Video"},
            {"key": "first_last", "name": "🎞 First + Last frame"},
            {"key": "last_frame", "name": "🎯 Last frame only"},
            {"key": "ref2v", "name": "🧩 References → Video"},
        ],
        "caps": {"ref_images": MAX_REF_IMAGES, "ref_videos": MAX_REF_VIDEOS,
                 "ref_audios": MAX_REF_AUDIOS,
                 "duration": {"min": 2, "max": 15, "default": 5},
                 "trained_frames": [124, 362]},
        "resolutions": list(RES_PRESETS.keys()),
        "aspects": ["16:9", "9:16", "1:1"],
        "defaults": {"resolution": "720p", "aspect": "16:9", "turbo": True,
                     "spectrum": False, "ref_image_size": "match",
                     "plan_upscale": True},
        "notes": {
            "sage": "Sage attention is ON via --use-sage-attention in every "
                    "box's .bat — the workflow's PATCH SAGE groups stay off.",
            "spectrum": "SPECTRUM speed enhancer: extra speedup on top of "
                        "turbo, quality may suffer.",
            "plan_upscale": "ON by default. H3 rounds frames to f%17==5 and "
                            "the LTX upscaler can only carry f=8k+1, flooring "
                            "to fit — so a 124-frame clip returns 121, losing "
                            "the tail and its audio. This renders one H3 step "
                            "longer (+17 frames, ~0.7s; nothing when already on "
                            "the shared lattice 73/209/345) and the upscale is "
                            "trimmed back to the exact length. Frames cannot be "
                            "recovered after the render — running the upscale "
                            "itself is still a separate manual action.",
            "upscale": "Renders default to 720p; ⬆ Upscale re-details a "
                       "finished clip with the LTX 2.3 enhancer (22B GGUF + "
                       "detailer lora, 3-step refine) up to a chosen max side "
                       "(1920 = 1080p)."},
    }


@router.post("/upload")
async def upload(file: UploadFile = File(...), kind: str = Form("image")):
    if kind not in ("image", "video", "audio"):
        raise HTTPException(400, "kind must be image|video|audio")
    data = await file.read()
    if not data:
        raise HTTPException(400, "empty file")
    ext = Path(file.filename or "x").suffix.lower() or \
        {"image": ".png", "video": ".mp4", "audio": ".mp3"}[kind]
    fid = uuid.uuid4().hex[:12]
    ups = _uploads()
    meta = {"kind": kind, "ext": ext, "orig": file.filename,
            "bytes": len(data), "at": time.strftime("%Y-%m-%d %H:%M:%S")}
    _UP_DIR.mkdir(parents=True, exist_ok=True)
    (_UP_DIR / f"{fid}{ext}").write_bytes(data)
    ups[fid] = meta
    _uploads_save(ups)
    return {"id": fid, **meta}


@router.get("/uploads/{fid}/file")
async def upload_file(fid: str):
    meta = _uploads().get(fid)
    if not meta:
        raise HTTPException(404, "unknown upload")
    fp = _upload_path(fid, meta)
    if not fp.exists():
        raise HTTPException(404, "file vanished")
    return FileResponse(fp, filename=meta.get("orig") or fp.name)


class RefVideoIn(BaseModel):
    id: str
    use_audio: bool = False


class GenerateIn(BaseModel):
    mode: str                              # t2v|i2v|first_last|last_frame|ref2v
    prompt: str
    worker_id: Optional[str] = None
    duration_s: float = 5.0
    resolution: str = "720p"               # 480p|720p|1080p
    aspect: str = "16:9"                   # 16:9|9:16|1:1 (i2v: image wins)
    size_from_image: bool = True           # i2v/first_last: derive dims from 1st frame
    turbo: bool = True
    draft: bool = False                    # 🏃 turbo lora at 4 steps — ~half the
                                           # sampling time, rougher look; for
                                           # TESTING an idea, not the final take
    spectrum: bool = False
    seed: Optional[int] = None
    first_frame: Optional[str] = None      # upload id
    last_frame: Optional[str] = None       # upload id
    ref_images: List[str] = []             # upload ids (≤9)
    ref_videos: List[RefVideoIn] = []      # (≤3)
    ref_audios: List[str] = []             # upload ids (≤3)
    ref_image_size: str = "match"          # match|max
    label: str = ""
    plan_upscale: bool = True              # v1.275.11 — DEFAULT ON (Lorenzo's
                                           # call). Renders one H3 step long so
                                           # the LTX upscaler's 8k+1 flooring
                                           # eats slack instead of the tail,
                                           # then trims the upscale back to the
                                           # exact length. Costs 17 frames
                                           # (~0.7s) and nothing when already on
                                           # the lattice; the alternative is
                                           # discovering frames are missing
                                           # AFTER the render, when they cannot
                                           # be recovered. Upscaling itself is
                                           # still a separate manual action.


@router.post("/generate")
async def generate(body: GenerateIn):
    mode = body.mode
    if mode not in ("t2v", "i2v", "first_last", "last_frame", "ref2v"):
        raise HTTPException(400, f"unknown mode '{mode}'")
    if not body.prompt.strip():
        raise HTTPException(400, "prompt is required — H3 is prompt-driven "
                                 "(use 🧠 Draft to build one per the spec)")
    if mode in ("i2v", "first_last") and not body.first_frame:
        raise HTTPException(400, "this mode needs a first frame image")
    if mode in ("first_last", "last_frame") and not body.last_frame:
        raise HTTPException(400, "this mode needs a last frame image")
    if mode == "ref2v" and not (body.ref_images or body.ref_videos
                                or body.ref_audios):
        raise HTTPException(400, "references mode needs at least one reference")
    if len(body.ref_images) > MAX_REF_IMAGES:
        raise HTTPException(400, f"max {MAX_REF_IMAGES} reference images")
    if len(body.ref_videos) > MAX_REF_VIDEOS:
        raise HTTPException(400, f"max {MAX_REF_VIDEOS} reference videos")
    if len(body.ref_audios) > MAX_REF_AUDIOS:
        raise HTTPException(400, f"max {MAX_REF_AUDIOS} reference audios")

    w = _worker(body.worker_id)
    ups = _uploads()

    def _need(fid: str, kinds: tuple) -> Path:
        meta = ups.get(fid)
        if not meta:
            raise HTTPException(404, f"upload '{fid}' not found")
        if meta["kind"] not in kinds:
            raise HTTPException(400, f"upload '{fid}' is a {meta['kind']}, "
                                     f"expected {'/'.join(kinds)}")
        fp = _upload_path(fid, meta)
        if not fp.exists():
            raise HTTPException(404, f"upload '{fid}' file vanished")
        return fp

    # push every referenced file to the box's ComfyUI input/ first
    def _push(fid: str, kinds: tuple) -> str:
        fp = _need(fid, kinds)
        return _upload_to_box(w["host"], fp.name, fp.read_bytes())

    first_name = _push(body.first_frame, ("image",)) if body.first_frame else None
    last_name = _push(body.last_frame, ("image",)) if body.last_frame else None
    ref_imgs = [_push(f, ("image",)) for f in body.ref_images]
    ref_vids = [{"name": _push(rv.id, ("video",)), "use_audio": rv.use_audio}
                for rv in body.ref_videos]
    ref_auds = [_push(f, ("audio",)) for f in body.ref_audios]

    # resolution: 720p default; i2v modes can inherit the first frame's aspect
    if mode in ("i2v", "first_last") and body.size_from_image and body.first_frame:
        gw, gh = _dims_from_image(_need(body.first_frame, ("image",)),
                                  body.resolution)
    else:
        gw, gh = _dims(body.resolution, body.aspect)
    want_frames = _frames(max(1.0, min(150.0, body.duration_s)))
    # v1.275.7: with an upscale planned, render one H3 step long so the LTX
    # 8k+1 flooring eats slack instead of the tail. Costs 17 frames (~0.7s of
    # render) and nothing at all when the count is already on the shared
    # lattice (73, 209, 345, ...).
    frames = _frames_for_upscale(want_frames) if body.plan_upscale else want_frames
    seed = body.seed if body.seed is not None else int(time.time()) % 2**31

    jid = uuid.uuid4().hex[:12]
    prefix = {"t2v": "MiMx-TXT", "i2v": "MiMx-IMG", "first_last": "MiMx-FL",
              "last_frame": "MiMx-LF", "ref2v": "MiMx-REF"}[mode]
    graph = _build_graph(mode, body.prompt, gw, gh, frames, seed,
                         body.turbo, body.spectrum, first_name, last_name,
                         ref_imgs, ref_vids, ref_auds,
                         body.ref_image_size, prefix,
                         draft=bool(body.draft))
    job = {"id": jid, "mode": mode, "label": body.label or prefix,
           "prompt": body.prompt, "host": w["host"], "worker": w["name"],
           "width": gw, "height": gh, "frames": frames,
           "duration_s": round(frames / 24, 2), "seed": seed,
           "turbo": body.turbo, "spectrum": body.spectrum,
           "draft": bool(body.draft and body.turbo),
           "refs": {"first": bool(first_name), "last": bool(last_name),
                    "images": len(ref_imgs), "videos": len(ref_vids),
                    "audios": len(ref_auds)},
           "status": "queued", "error": None, "elapsed_s": 0,
           "plan_upscale": bool(body.plan_upscale),
           "target_frames": want_frames,
           "at": time.strftime("%Y-%m-%d %H:%M:%S"), "_graph": graph}
    if body.plan_upscale and frames != want_frames:
        job["label"] = f"{job['label']} (+{frames - want_frames}f for upscale)"
    with _LOCK:
        _JOBS[jid] = job
    _jobs_save()
    t = threading.Thread(target=_run_job, args=(jid,), daemon=True,
                         name=f"h3-{jid}")
    _THREADS[jid] = t
    t.start()
    return {"ok": True, "job": {k: v for k, v in job.items() if k != "_graph"}}


class UpscaleIn(BaseModel):
    largest_size: int = 1920               # max side after upscale (1920 = 1080p)
    worker_id: Optional[str] = None        # default: the source job's box
    prompt: str = ""                       # optional guidance text (usually empty)


@router.post("/jobs/{jid}/upscale")
async def upscale(jid: str, body: UpscaleIn):
    src = _JOBS.get(jid)
    if not src or src.get("status") != "done":
        raise HTTPException(404, "no finished video on this job to upscale")
    fp = _VID_DIR / f"{jid}.mp4"
    if not fp.exists():
        raise HTTPException(404, "video file vanished")
    if body.worker_id:
        w = _worker(body.worker_id)
        host, wname = w["host"], w["name"]
    else:
        host, wname = src["host"], src.get("worker") or src["host"]
    box_name = _upload_to_box(host, fp.name, fp.read_bytes())
    largest = max(512, min(3840, int(body.largest_size)))
    graph = _build_upscale_graph(box_name, largest, body.prompt)
    nid = uuid.uuid4().hex[:12]
    job = {"id": nid, "mode": "upscale",
           "label": f"⬆ {src.get('label') or jid} → {largest}px",
           "prompt": body.prompt or "(LTX 2.3 enhancer upscale)",
           "host": host, "worker": wname, "source_job": jid,
           "width": largest, "height": 0, "frames": src.get("frames"),
           "duration_s": src.get("duration_s"), "seed": 0,
           "turbo": False, "spectrum": False,
           "refs": {"first": False, "last": False, "images": 0,
                    "videos": 1, "audios": 0},
           "status": "queued", "error": None, "elapsed_s": 0,
           # v1.275.7: the source rendered long on purpose — trim the UPSCALE
           # back to the length that was actually asked for. Only set when the
           # source opted in, so nothing that rendered before this exists gets
           # silently shortened.
           "trim_to_frames": (src.get("target_frames")
                              if src.get("plan_upscale") else None),
           "timeout_s": 10800,
           "at": time.strftime("%Y-%m-%d %H:%M:%S"), "_graph": graph}
    with _LOCK:
        _JOBS[nid] = job
    _jobs_save()
    t = threading.Thread(target=_run_job, args=(nid,), daemon=True,
                         name=f"h3up-{nid}")
    _THREADS[nid] = t
    t.start()
    return {"ok": True, "job": {k: v for k, v in job.items() if k != "_graph"}}


@router.get("/jobs")
async def jobs():
    for j in list(_JOBS.values()):
        _reconcile(j)
    out = [{k: v for k, v in j.items() if k != "_graph"}
           for j in _JOBS.values()]
    out.sort(key=lambda j: j.get("at") or "", reverse=True)
    return {"jobs": out}


@router.get("/jobs/{jid}")
async def job_get(jid: str):
    j = _JOBS.get(jid)
    if not j:
        raise HTTPException(404, "unknown job")
    _reconcile(j)
    return {k: v for k, v in j.items() if k != "_graph"}


@router.delete("/jobs/{jid}")
async def job_delete(jid: str):
    j = _JOBS.pop(jid, None)
    if not j:
        raise HTTPException(404, "unknown job")
    _jobs_save()
    fp = _VID_DIR / f"{jid}.mp4"
    if fp.exists():
        try:
            fp.unlink()
        except Exception:  # noqa: BLE001
            pass
    return {"ok": True}


@router.get("/media/{jid}")
async def media(jid: str, download: int = 0):
    j = _JOBS.get(jid)
    if not j or j.get("status") != "done":
        raise HTTPException(404, "no finished video for this job")
    fp = _VID_DIR / f"{jid}.mp4"
    if not fp.exists():
        raise HTTPException(404, "video file vanished")
    headers = {}
    if download:
        headers["Content-Disposition"] = \
            f'attachment; filename="{j.get("label") or jid}.mp4"'
    return FileResponse(fp, media_type="video/mp4", headers=headers)


# ── 🧠 prompt drafting per the canonical spec (bypasses PromptEnhancer) ──────
class DraftIn(BaseModel):
    mode: str
    idea: str
    duration_s: float = 5.0
    dialogue: str = ""                      # preserved exactly if given
    refs_note: str = ""                     # ref2v: what each reference is


@router.get("/llm-prompt")
async def llm_prompt():
    """🤖 The full MiniMax H3 prompting-agent instructions, VERBATIM — for
    users to paste into their own LLM (ChatGPT, Claude, a local model) and
    write video prompts outside the app (v1.277.11). Served from
    docs/MINIMAX_H3_LLM_PROMPT.md so it can be updated without a code change."""
    fp = _DOCS_DIR / "MINIMAX_H3_LLM_PROMPT.md"
    try:
        return {"prompt": fp.read_text("utf-8")}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, f"prompt file unreadable: {e}")


def _agent_spec() -> str:
    fp = _DOCS_DIR / "MINIMAX_H3_PROMPTING.md"
    try:
        txt = fp.read_text("utf-8")
        start = txt.index("You are a specialized MiniMax H3")
        end = txt.find("## Part 3")
        return txt[start:end if end > start else len(txt)].strip()
    except Exception:  # noqa: BLE001
        return ("You are a specialized MiniMax H3 video-prompting agent. "
                "Produce a ready-to-use MiniMax H3 prompt in the official "
                "format for the requested mode. Output only the prompt.")


_MODE_HUMAN = {"t2v": "Text to Video", "i2v": "Image to Video",
               "first_last": "First and Last Frame to Video",
               "last_frame": "Last Frame to Video",
               "ref2v": "Full Reference Mode"}


@router.post("/draft-prompt")
async def draft_prompt(body: DraftIn,
                       session: AsyncSession = Depends(get_session)):
    from backend.api.character_studio import _ollama_chat_json, _app_settings
    s = await _app_settings(session)
    urls = (getattr(s, "ollama_urls", None) or
            ([s.ollama_base_url] if getattr(s, "ollama_base_url", None) else []))
    model = getattr(s, "ollama_model", None)
    if not urls or not model:
        raise HTTPException(409, "Ollama is not configured (Settings → LLM) — "
                                 "write the prompt manually per the spec.")
    user = (f"Mode: {_MODE_HUMAN.get(body.mode, body.mode)}\n"
            f"Video duration: {body.duration_s:.2f} seconds\n"
            f"Idea: {body.idea}\n")
    if body.dialogue.strip():
        user += (f"Exact dialogue to preserve word-for-word: "
                 f"{body.dialogue.strip()}\n")
    if body.refs_note.strip():
        user += f"References provided: {body.refs_note.strip()}\n"
    user += "Produce the final MiniMax H3 prompt now."
    import asyncio
    content = await asyncio.to_thread(
        _ollama_chat_json, urls, model, _agent_spec(), user, None, 300.0)
    if not content:
        raise HTTPException(502, "LLM did not answer — check the Ollama server")
    return {"prompt": content.strip()}
