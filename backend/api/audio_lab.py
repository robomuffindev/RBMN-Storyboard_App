"""🎧 Audio Lab (v1.277.19) — music + narration, locally, on the fleet.

MUSIC — FOUR engines (`MUSIC_ENGINES`), all verified live on all three boxes:
  * `ace15` — ACE-Step 1.5 turbo AIO. ~15 s for a 20 s track: the sketching
    engine, and the DEFAULT by his call.
  * `ace15_sft` / `ace15_base` — the XL quality models, 50 steps at **cfg 3**
    (NOT ComfyUI's 7/6 — see the block at ACE_XL). ~50 s for a 20 s track;
    `sft` is the song finetune and the take-you-keep.
  * `minimax3` — MiniMax Music 3, ~3× real time (the step count is NOT the
    cost — ~46 s of it is fixed), spectrally the brightest of the four.
  🆚 `music/compare` renders one prompt on every picked engine, round-robin
  across the boxes, same seed, all loudness-matched.
  🎛 Prompts are SHAPED per engine (`prompt_shape.py`) — ACE wants tempo/key
  OUT of the caption, MM3 wants them IN. What changed is published on the job.
  🔊 Every MUSIC track is normalised to -14 LUFS / -1 dBTP (needs ffmpeg on
  the app host; without it the job says so and the audio is left as rendered).

NARRATION (TTS) — engine `f5tts` via the ComfyUI-F5-TTS custom node
(research: F5-TTS is the open-weights quality leader; cloning = ONE clean
reference WAV of **at most 12 s** — the node hard-cuts at 12,000 ms, mid-word —
plus its EXACT transcript, which also sets the chunk size for long text). Long texts are split on blank
lines and rendered chunk by chunk with a configurable silence between
paragraphs (his pause control), then concatenated with ffmpeg. If the node
isn't installed the lane says exactly what to run (scripts/install_audio.py).

Every generation follows the STANDING RULE: live, expandable, verbose status
(what, where, how long), recorded to disk as benchmarking data.

Storage: <project_dir>/_libraries/audio_lab/  (import-time anchored).
"""
from __future__ import annotations

import contextlib
import json
import logging
import re
import threading
import time
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from backend.config import settings as cfg

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/audio-lab", tags=["audio-lab"])

_ROOT = Path(cfg.project_dir) / "_libraries" / "audio_lab"
_TRACK_DIR = _ROOT / "tracks"
_VOICE_DIR = _ROOT / "voices"
_JOBS_FP = _ROOT / "jobs.json"
_LOCK = threading.Lock()
_JOBS: Dict[str, dict] = {}
_OBJ_INFO_CACHE: Dict[str, dict] = {}     # host -> object_info (trimmed)

ACE_CKPT = "ace_step_1.5_turbo_aio.safetensors"
# 🎚 v1.277.17 — the QUALITY lanes. The AIO above is the TURBO checkpoint (8
# steps, cfg 1, the small 4.8 GB DiT): the speed model, and what Lorenzo was
# hearing. These are ComfyUI's own base/sft templates — a bigger XL DiT and a
# 50-step recipe, staged as split files with their own encoders + VAE.
# ⚠⚠ THE CFG HERE IS *NOT* ComfyUI's TEMPLATE VALUE, ON PURPOSE (v1.277.18).
# The bundled templates ship cfg 7 (sft) / 6 (base) and those are the settings
# that produce the "garbled and compressed" output of ComfyUI issue #12322 —
# reproduced here by ear on 2026-08-16 ("mixing issues and minor artifacts" at
# 7, "much much worse" at 6). Upstream's own default is 5, practitioners run
# 1-1.5, and cfg 3 is what HE approved ("really really good"). cfg on this
# model trades brightness against artifacts: measured energy above 8 kHz was
# 0.85% at cfg 7 · 0.55% at cfg 3 · 0.19% at cfg 1.5. Do not "restore" 7/6.
ACE_XL = {
    "ace15_base": ("acestep_v1.5_xl_base_bf16.safetensors", 50, 3.0),
    "ace15_sft":  ("acestep_v1.5_xl_sft_bf16.safetensors", 50, 3.0),
}
ACE_CLIP1 = "qwen_0.6b_ace15.safetensors"
ACE_CLIP2 = "qwen_4b_ace15.safetensors"
ACE_VAE = "ace_1.5_vae.safetensors"
MUSIC_ENGINES = ("ace15", "ace15_base", "ace15_sft", "minimax3")
MM3_DIT = "minimax_music3_dit_int8_convrot.safetensors"
MM3_TE = "minimax_music3_text_encoder_pruned_int8_convrot.safetensors"
MM3_VAE = "minimax_music3_dav.safetensors"


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _jobs_load() -> None:
    global _JOBS
    try:
        _JOBS = json.loads(_JOBS_FP.read_text("utf-8"))
    except Exception:                                            # noqa: BLE001
        _JOBS = {}


def _jobs_save() -> None:
    _ROOT.mkdir(parents=True, exist_ok=True)
    tmp = _JOBS_FP.with_name(f"jobs.{uuid.uuid4().hex[:6]}.tmp")
    slim = {k: {x: y for x, y in v.items() if not x.startswith("_")}
            for k, v in _JOBS.items()}
    tmp.write_text(json.dumps(slim, indent=1), "utf-8")
    tmp.replace(_JOBS_FP)


_jobs_load()


def _jget(url: str, timeout: float = 30.0):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode())


def _jpost(url: str, body: dict, timeout: float = 60.0):
    req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"},
                                 method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        # ⚠ str(HTTPError) carries NO body, and the body IS the answer
        # (the v1.276.49 lesson, relearned here on this module's first render)
        detail = e.read().decode("utf-8", "replace")[:600]
        raise RuntimeError(f"HTTP {e.code} from {url.split('/prompt')[0]}: "
                           f"{detail}") from None


def _hosts() -> List[dict]:
    from backend.api.lora_train import _helpers_list
    return _helpers_list()


def _node_info(host: str, node: str) -> Optional[dict]:
    """The node's input spec from the worker's OWN object_info — defaults come
    from the box, not from guesses (the widget-order trap)."""
    key = f"{host}:{node}"
    if key in _OBJ_INFO_CACHE:
        return _OBJ_INFO_CACHE[key] or None
    try:
        d = _jget(f"http://{host}:8188/object_info/{urllib.parse.quote(node)}",
                  timeout=20)
        _OBJ_INFO_CACHE[key] = d.get(node) or {}
    except Exception:                                            # noqa: BLE001
        _OBJ_INFO_CACHE[key] = {}
    return _OBJ_INFO_CACHE[key] or None


def _inputs_with_defaults(host: str, node: str, values: Dict[str, Any]) -> Dict[str, Any]:
    """values + the node's own defaults for everything not set."""
    spec = _node_info(host, node) or {}
    out = dict(values)
    for section in ("required", "optional"):
        for name, defn in ((spec.get("input") or {}).get(section) or {}).items():
            if name in out:
                continue
            if isinstance(defn, (list, tuple)) and len(defn) > 1 and \
                    isinstance(defn[1], dict) and "default" in defn[1]:
                out[name] = defn[1]["default"]
            elif isinstance(defn, (list, tuple)) and defn and \
                    isinstance(defn[0], list) and defn[0]:
                # old-style COMBO: choices list in slot 0, no "default" key
                out[name] = defn[0][0]
            elif isinstance(defn, (list, tuple)) and defn and defn[0] == "COMBO" and \
                    len(defn) > 1 and isinstance(defn[1], dict) and defn[1].get("options"):
                # new-style COMBO (V3 schema): ["COMBO", {"options": [...]}] —
                # timesignature/keyscale bit us on the first ACE render
                out[name] = defn[1]["options"][0]
            elif section == "required" and isinstance(defn, (list, tuple)) and defn:
                # last-resort type fallback so validation never sees a hole
                out[name] = {"STRING": "", "BOOLEAN": False, "INT": 0,
                             "FLOAT": 0.0}.get(defn[0], None) if isinstance(defn[0], str) else None
                if out[name] is None:
                    out.pop(name, None)
    return out


def _model_present(host: str, route: str, fname: str) -> bool:
    try:
        got = _jget(f"http://{host}:8188/models/{route}", timeout=15)
        return any(fname in str(x) for x in (got or []))
    except Exception:                                            # noqa: BLE001
        return False


def _ace_xl_status(host: str, engine: str, ace_node: bool) -> dict:
    """A quality lane needs FOUR files, not one — and saying which is missing
    is the difference between 'not ready' and a five-minute hunt."""
    unet = ACE_XL[engine][0]
    parts = {unet: _model_present(host, "diffusion_models", unet),
             ACE_CLIP1: _model_present(host, "text_encoders", ACE_CLIP1),
             ACE_CLIP2: _model_present(host, "text_encoders", ACE_CLIP2),
             ACE_VAE: _model_present(host, "vae", ACE_VAE)}
    missing = [k for k, v in parts.items() if not v]
    return {"nodes": ace_node, "model": not missing,
            "ready": ace_node and not missing,
            "note": "" if not missing else
            "missing " + ", ".join(missing)
            + " — run scripts/install_ace_quality.py"}


def _engine_status(host: str) -> dict:
    """What THIS worker can do — nodes AND models, both from the box itself."""
    ace_node = _node_info(host, "TextEncodeAceStepAudio1.5") is not None
    mm3_node = any(_node_info(host, n) is not None
                   for n in ("MiniMaxMusic3TextEncode", "TextEncodeMiniMaxMusic3",
                             "MiniMaxMusic3", "MiniMaxMusicTextEncode"))
    f5_node = any(_node_info(host, n) is not None
                  for n in ("F5TTSAudio", "F5TTSAudioInputs", "F5TTSCreate"))
    # 🗣 Chatterbox needs BOTH halves of the pair — the engine node that carries
    # the settings and the unified node that actually speaks. Probing only one
    # would report ready on a half-loaded pack.
    cb_node = (_node_info(host, CB_ENGINE) is not None
               and _node_info(host, CB_SPEAK) is not None)
    return {
        "ace15": {"nodes": ace_node,
                  "model": _model_present(host, "checkpoints", ACE_CKPT),
                  "ready": ace_node and _model_present(host, "checkpoints", ACE_CKPT)},
        "ace15_base": _ace_xl_status(host, "ace15_base", ace_node),
        "ace15_sft": _ace_xl_status(host, "ace15_sft", ace_node),
        "minimax3": {"nodes": mm3_node,
                     "model": _model_present(host, "diffusion_models", MM3_DIT),
                     "ready": mm3_node and _model_present(host, "diffusion_models", MM3_DIT),
                     "note": "" if mm3_node else
                     "ComfyUI predates MiniMax Music 3 (2026-08-13) — update ComfyUI on this box"},
        "f5tts": {"nodes": f5_node, "model": f5_node,
                  "ready": f5_node,
                  # ⚠ THE LICENCE IS PART OF THE STATUS. He intends to give this
                  # app to the public; F5-TTS is CC-BY-NC 4.0, so shipping it as
                  # the default would put a non-commercial restriction on every
                  # person who uses it. Say so where the engine is chosen.
                  "licence": "CC-BY-NC 4.0 — NON-COMMERCIAL",
                  "note": "" if f5_node else
                  "install the ComfyUI-F5-TTS node: python scripts/install_audio.py"},
        "chatterbox": {"nodes": cb_node, "model": cb_node, "ready": cb_node,
                       "licence": "MIT — commercial use OK",
                       "note": "" if cb_node else
                       "install it: python scripts/install_chatterbox.py "
                       "--host <box>, then restart that box's ComfyUI"},
    }


# ── prompt hygiene ───────────────────────────────────────────────────────────
_BPM_RE = re.compile(r"\b(\d{2,3})\s*(?:bpm|beats per minute)\b", re.I)
_KEY_RE = re.compile(r"\b(?:in\s+)?([A-G](?:\s?#|\s?b|♯|♭)?)\s+(major|minor)\b", re.I)
_SIG_RE = re.compile(r"\b(\d)\s*/\s*(\d)\s*(?:time|meter)?\b")


def _split_meta_from_tags(tags: str, bpm: int = 0, keyscale: str = "") -> tuple:
    """Pull tempo/key/meter OUT of the caption and into the metadata fields.

    ⭐ Upstream is explicit: *"Don't write tempo, BPM, key and other metadata
    information in Caption. These should be set through dedicated metadata
    parameters."* ComfyUI's own bundled ACE templates violate this (their
    captions literally say "at 72 BPM in 4/4 time" AND set the widgets), and so
    did every prompt this repo sent until v1.277.18 — the caption said "90 bpm"
    while the widget defaulted to 120, i.e. the model was told two tempos.
    Returns (clean_tags, bpm, keyscale) with anything found used only when the
    caller did not pass an explicit value."""
    t = tags or ""
    m = _BPM_RE.search(t)
    if m and not bpm:
        try:
            bpm = int(m.group(1))
        except ValueError:
            pass
    k = _KEY_RE.search(t)
    if k and not keyscale:
        keyscale = f"{k.group(1).replace(' ', '')} {k.group(2).lower()}"
    t = _SIG_RE.sub(" ", _KEY_RE.sub(" ", _BPM_RE.sub(" ", t)))
    t = re.sub(r"\s*,\s*,+", ", ", t)
    t = re.sub(r"\s{2,}", " ", t).strip(" ,;")
    return t, bpm, keyscale


# ── graphs ───────────────────────────────────────────────────────────────────
def _ace_xl_graph(host: str, engine: str, tags: str, lyrics: str,
                  seconds: float, seed: int, bpm: int, keyscale: str,
                  language: str, prefix: str, steps: int = 0,
                  cfg: float = 0.0, timesignature: str = "",
                  sampler: str = "euler",
                  scheduler: str = "simple", shift: float = 3.0,
                  audio_codes: Optional[bool] = None,
                  apg: Optional[dict] = None, flac: bool = False) -> dict:
    """ACE-Step 1.5 **XL** (base/sft) — ComfyUI's own quality templates.

    Same netlist as the template's, with the AIO checkpoint split into its
    three real parts: UNETLoader + DualCLIPLoader("ace") + VAELoader. Sampling
    is euler/simple at AuraFlow shift 3 and 50 steps — but **cfg comes from
    ACE_XL and is 3.0, NOT the template's 7/6** (see the block at ACE_XL: 7/6
    is the garbled-output setting of ComfyUI issue #12322). ⚠ Unlike the turbo model, cfg here is REAL guidance —
    dropping it to 1 makes this model sound like the turbo one."""
    unet, dsteps, dcfg = ACE_XL[engine]
    tags, bpm, keyscale = _split_meta_from_tags(tags, bpm, keyscale)
    enc = _inputs_with_defaults(host, "TextEncodeAceStepAudio1.5", {
        "clip": ["2", 0], "tags": tags, "lyrics": lyrics or "[instrumental]",
        "seed": seed, "duration": float(seconds),
        "timesignature": timesignature or "4",
        **({"bpm": int(bpm)} if bpm else {}),
        **({"keyscale": keyscale} if keyscale else {}),
        **({"language": language} if language else {}),
    })
    if audio_codes is not None and "generate_audio_codes" in enc:
        enc["generate_audio_codes"] = bool(audio_codes)
    g = {
        "1": {"class_type": "UNETLoader",
              "inputs": {"unet_name": unet, "weight_dtype": "default"}},
        "2": {"class_type": "DualCLIPLoader",
              "inputs": {"clip_name1": ACE_CLIP1, "clip_name2": ACE_CLIP2,
                         "type": "ace", "device": "default"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": ACE_VAE}},
        "4": {"class_type": "TextEncodeAceStepAudio1.5", "inputs": enc},
        "5": {"class_type": "ModelSamplingAuraFlow",
              "inputs": {"model": ["1", 0], "shift": float(shift or 3.0)}},
        "6": {"class_type": "EmptyAceStep1.5LatentAudio",
              "inputs": {"seconds": float(seconds), "batch_size": 1}},
        "7": {"class_type": "ConditioningZeroOut",
              "inputs": {"conditioning": ["4", 0]}},
        "8": {"class_type": "KSampler",
              "inputs": {"model": ["5", 0], "positive": ["4", 0],
                         "negative": ["7", 0], "latent_image": ["6", 0],
                         "seed": seed, "steps": int(steps or dsteps),
                         "cfg": float(cfg or dcfg),
                         "sampler_name": sampler, "scheduler": scheduler,
                         "denoise": 1.0}},
        "9": {"class_type": "VAEDecodeAudio",
              "inputs": {"samples": ["8", 0], "vae": ["3", 0]}},
    }
    # ⚠ shift=0 means BYPASS ModelSamplingAuraFlow (a recipe some practitioners
    # use); the sampler then reads the raw model, so rewire rather than pass 0.
    if not shift:
        g["8"]["inputs"]["model"] = ["1", 0]
        g.pop("5")
    # 🅰 Adaptive Projected Guidance clamps ‖cond−uncond‖, which is what makes
    # a high cfg survivable on this model. ⚠ At cfg 1 ComfyUI skips uncond
    # entirely and APG is a NO-OP — it only means anything above cfg 1.
    if apg:
        src = "5" if "5" in g else "1"
        # ⚠ the class is "APG" on these workers — the display name is
        # "Adaptive Projected Guidance", and asking object_info for the display
        # name returns a cheerful empty {} rather than an error.
        g["11"] = {"class_type": "APG",
                   "inputs": {"model": [src, 0],
                              "eta": float(apg.get("eta", 1.0)),
                              "norm_threshold": float(apg.get("norm", 0.0)),
                              "momentum": float(apg.get("momentum", 0.0))}}
        g["8"]["inputs"]["model"] = ["11", 0]
    g["10"] = ({"class_type": "SaveAudio",
                "inputs": {"audio": ["9", 0], "filename_prefix": prefix}}
               if flac else
               {"class_type": "SaveAudioMP3",
                "inputs": {"audio": ["9", 0], "filename_prefix": prefix,
                           "quality": "V0"}})
    return g


def _ace_graph(host: str, tags: str, lyrics: str, seconds: float, seed: int,
               bpm: int, keyscale: str, language: str, prefix: str,
               flac: bool = False, timesignature: str = "") -> dict:
    tags, bpm, keyscale = _split_meta_from_tags(tags, bpm, keyscale)
    enc = _inputs_with_defaults(host, "TextEncodeAceStepAudio1.5", {
        "clip": ["1", 1], "tags": tags, "lyrics": lyrics or "[instrumental]",
        "seed": seed, "duration": float(seconds),
        # ⚠ the combo default lands on "2" — force common time unless the
        # caption asked for something else (which the shaper extracts).
        "timesignature": timesignature or "4",
        **({"bpm": int(bpm)} if bpm else {}),
        **({"keyscale": keyscale} if keyscale else {}),
        **({"language": language} if language else {}),
    })
    return {
        "1": {"class_type": "CheckpointLoaderSimple",
              "inputs": {"ckpt_name": ACE_CKPT}},
        "2": {"class_type": "TextEncodeAceStepAudio1.5", "inputs": enc},
        "3": {"class_type": "ModelSamplingAuraFlow",
              "inputs": {"model": ["1", 0], "shift": 3}},
        "4": {"class_type": "EmptyAceStep1.5LatentAudio",
              "inputs": {"seconds": float(seconds), "batch_size": 1}},
        "5": {"class_type": "ConditioningZeroOut",
              "inputs": {"conditioning": ["2", 0]}},
        "6": {"class_type": "KSampler",
              "inputs": {"model": ["3", 0], "positive": ["2", 0],
                         "negative": ["5", 0], "latent_image": ["4", 0],
                         "seed": seed, "steps": 8, "cfg": 1.0,
                         "sampler_name": "euler", "scheduler": "simple",
                         "denoise": 1.0}},
        "7": {"class_type": "VAEDecodeAudio",
              "inputs": {"samples": ["6", 0], "vae": ["1", 2]}},
        # ⚠ judge a MIX on a lossless file: comparing an mp3 against a FLAC and
        # calling the difference a model defect is a methodology bug.
        "8": ({"class_type": "SaveAudio",
               "inputs": {"audio": ["7", 0], "filename_prefix": prefix}}
              if flac else
              {"class_type": "SaveAudioMP3",
               "inputs": {"audio": ["7", 0], "filename_prefix": prefix,
                          "quality": "V0"}}),
    }


def _mm3_graph(host: str, caption: str, lyrics: str, seconds: float,
               seed: int, prefix: str) -> dict:
    """MiniMax Music 3 — from the official template's subgraph (2026-08-16),
    inputs verified/filled against this worker's object_info. int8 DiT +
    TILED audio VAE decode: the low-VRAM path for 16GB boxes."""
    enc = _inputs_with_defaults(host, "MiniMaxMusic3TextEncode", {
        "clip": ["2", 0], "caption": caption,
        "lyrics": lyrics or "[instrumental]",
        "seed": seed, "max_duration": float(seconds)})
    return {
        "1": {"class_type": "UNETLoader",
              "inputs": {"unet_name": MM3_DIT, "weight_dtype": "default"}},
        "2": {"class_type": "CLIPLoader",
              "inputs": {"clip_name": MM3_TE, "type": "minimax",
                         "device": "default"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": MM3_VAE}},
        "4": {"class_type": "MiniMaxMusic3TextEncode", "inputs": enc},
        "5": {"class_type": "EmptyMiniMaxMusic3LatentAudio",
              "inputs": {"seconds": ["4", 1], "batch_size": 1}},
        "6": {"class_type": "ConditioningZeroOut",
              "inputs": {"conditioning": ["4", 0]}},
        "7": {"class_type": "KSampler",
              "inputs": {"model": ["1", 0], "positive": ["4", 0],
                         "negative": ["6", 0], "latent_image": ["5", 0],
                         "seed": seed, "steps": 30, "cfg": 1.7,
                         "sampler_name": "euler", "scheduler": "simple",
                         "denoise": 1.0}},
        "8": {"class_type": "VAEDecodeAudioTiled",
              "inputs": {"samples": ["7", 0], "vae": ["3", 0],
                         "tile_size": 1536, "overlap": 64}},
        "9": {"class_type": "SaveAudioMP3",
              "inputs": {"audio": ["8", 0], "filename_prefix": prefix,
                         "quality": "V0"}},
    }


def _run_graph_job(jid: str, host: str, graph: dict, out_ext: str = ".mp3",
                   timeout_s: float = 1800.0, stem: str = "") -> Path:
    """Submit → poll history → download the audio output. Blocking; thread.

    ⚠⚠ `stem` EXISTS BECAUSE ITS ABSENCE WAS A BUG (2026-08-18). The download
    was named `<jid><ext>` — the JOB's id, not the CHUNK's — so every chunk of
    a multi-part narration overwrote the previous one, and `parts` ended up
    holding N references to the SAME file: the last chunk, repeated N times.
    Single-chunk renders were fine, which is why it survived until pause tags
    made multi-chunk the normal case. **His report was "sounds the same every
    time", and it was literally true.**"""
    st = _JOBS[jid]
    base = f"http://{host}:8188"
    r = _jpost(f"{base}/prompt", {"prompt": graph})
    if r.get("error") or r.get("node_errors"):
        raise RuntimeError("worker rejected the graph: "
                           + json.dumps({k: r.get(k) for k in
                                         ("error", "node_errors")})[:400])
    pid = r.get("prompt_id")
    if not pid:
        raise RuntimeError("no prompt_id from the worker")
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        time.sleep(2.0)
        st["elapsed_s"] = round(time.time() - st["_t0"], 1)
        try:
            h = _jget(f"{base}/history/{pid}", timeout=20).get(pid)
        except Exception:                                        # noqa: BLE001
            continue
        if not h:
            continue
        hs = h.get("status") or {}
        if hs.get("status_str") == "error":
            msgs = [m for m in (hs.get("messages") or [])
                    if m and m[0] == "execution_error"]
            raise RuntimeError(f"execution error: {json.dumps(msgs)[:400]}")
        outs = h.get("outputs") or {}
        for node_out in outs.values():
            for key in ("audio", "audios", "gifs", "files"):
                for f in (node_out.get(key) or []):
                    fn = f.get("filename")
                    if not fn:
                        continue
                    sub = f.get("subfolder", "")
                    q = urllib.parse.urlencode(
                        {"filename": fn, "subfolder": sub,
                         "type": f.get("type", "output")})
                    with urllib.request.urlopen(f"{base}/view?{q}",
                                                timeout=300) as resp:
                        data = resp.read()
                    _TRACK_DIR.mkdir(parents=True, exist_ok=True)
                    ext = Path(fn).suffix or out_ext
                    fp = _TRACK_DIR / f"{stem or jid}{ext}"
                    fp.write_bytes(data)
                    return fp
    raise TimeoutError(f"no audio after {int(timeout_s)}s")


def _normalise(fp: Path, lufs: float = -14.0, peak: float = -1.0) -> str:
    """Two-pass EBU R128 gain to a fixed loudness. Returns a note, never raises.

    ⭐ The reference ACE-Step pipeline normalises (upstream recommends a -1 dB
    peak); ComfyUI's graph is `VAEDecodeAudio → Save` with nothing in between.
    Without this, two cues in one project sit at different levels and the
    louder one always sounds 'better' — which also made our own A/B tests
    partly a loudness test (turbo measured -12.9 LUFS against the XL renders'
    -14.5, and the quieter ones were called 'muffled').

    ⚠ TWO passes on purpose: ffmpeg's single-pass `loudnorm` is a DYNAMIC
    processor — it would change the mix while measuring it."""
    import shutil
    import subprocess
    if not shutil.which("ffmpeg"):
        return "ffmpeg not found — not normalised"
    try:
        r = subprocess.run(["ffmpeg", "-v", "info", "-i", str(fp), "-af",
                            "loudnorm=print_format=json", "-f", "null", "-"],
                           capture_output=True, text=True, errors="replace",
                           timeout=300)
        txt = (r.stderr or "") + (r.stdout or "")
        # match by KEY: ComfyUI writes the whole prompt JSON into the file's
        # metadata, so a brace-to-brace regex grabs the wrong block.
        blocks = re.findall(r"\{[^{}]*\"input_i\"[^{}]*\}", txt, re.S)
        if not blocks:
            return "loudness not measurable — left as rendered"
        m = json.loads(blocks[-1])
        gain = lufs - float(m["input_i"])
        tp = float(m.get("input_tp", -1.0)) + gain
        if tp > peak:
            gain -= (tp - peak)
        if abs(gain) < 0.05:
            return "already at target loudness"
        tmp = fp.with_name(f"{fp.stem}.norm{fp.suffix}")
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(fp), "-af",
                        f"volume={gain:.2f}dB", str(tmp)], check=True,
                       timeout=300)
        tmp.replace(fp)
        return f"normalised {gain:+.2f} dB → {lufs:.0f} LUFS"
    except Exception as e:                                       # noqa: BLE001
        return f"not normalised ({type(e).__name__})"


def _log(jid: str, msg: str) -> None:
    st = _JOBS.get(jid) or {}
    st.setdefault("log", []).append(
        {"t": round(time.time() - st.get("_t0", time.time()), 1), "detail": msg})
    st["detail"] = msg


# ── music ────────────────────────────────────────────────────────────────────
class MusicIn(BaseModel):
    engine: str = "ace15"            # ace15 | ace15_base | ace15_sft | minimax3
    tags: str = ""                   # ace: style tags · mm3: structured caption
    lyrics: str = ""                 # [tagged] lyrics; empty = instrumental
    seconds: float = 60.0            # EXACT length (the story-arc pairing)
    bpm: int = 0                     # 0 = model's choice
    keyscale: str = ""               # e.g. "E minor"
    timesignature: str = ""          # "" = 4 (the shaper reads it off the caption)
    language: str = "en"
    seed: Optional[int] = None
    host: str = ""                   # '' = first ready worker
    label: str = ""
    steps: int = 0                   # 0 = the engine's official default
    cfg: float = 0.0                 # 0 = the engine's official default
    normalize: bool = True           # EBU R128 to -14 LUFS / -1 dBTP


def pick_music_host(engine: str, host: str = "") -> str:
    """The first worker READY for this engine (or the named one, checked).

    ⚠ Raises 409 with each box's status rather than a bare "no worker" — the
    .48 rule: an error that does not name the machine costs four probes."""
    hosts = [host] if host else [h["host"] for h in _hosts()]
    ready = next((h for h in hosts
                  if _engine_status(h).get(engine, {}).get("ready")), None)
    if not ready:
        sts = {h: _engine_status(h).get(engine, {}) for h in hosts}
        raise HTTPException(409, f"no worker is ready for {engine}: "
                                 + json.dumps(sts)[:400])
    return ready


def enqueue_music(*, engine: str, tags: str, lyrics: str = "",
                  seconds: float = 60.0, bpm: int = 0, keyscale: str = "",
                  timesignature: str = "", language: str = "en",
                  seed: Optional[int] = None,
                  host: str = "", label: str = "", steps: int = 0,
                  cfg: float = 0.0, normalize: bool = True,
                  meta: Optional[dict] = None) -> dict:
    """Register + start ONE music render. IN-PROCESS on purpose.

    ⭐ The 🎼 score lane calls THIS, never `POST /music/generate` over HTTP:
    self-calling this app's own API from an async route is the v1.276.41
    deadlock class, and it is designed out here rather than guarded against."""
    if engine not in MUSIC_ENGINES:
        raise HTTPException(400, "engine must be one of " + ", ".join(MUSIC_ENGINES))
    if not (tags or "").strip():
        raise HTTPException(400, "describe the music (tags/caption)")
    seconds = max(5.0, min(300.0, float(seconds)))
    ready = pick_music_host(engine, host)

    # 🎛 v1.277.19 — PROJECT the brief onto THIS engine's fields. ACE wants the
    # tempo/key OUT of the caption (its tokenizer injects a `# Metas` block);
    # MM3 has no metadata widgets at all and wants them IN, in its own
    # three-section layout. Same brief, opposite shapes — see prompt_shape.py.
    from backend.api.prompt_shape import shape as _shape
    raw_tags, raw_lyrics = tags, lyrics
    sh = _shape(engine, tags, lyrics, seconds, bpm, keyscale, timesignature)
    tags, lyrics = sh["tags"], sh["lyrics"]
    bpm = int(sh.get("bpm") or bpm or 0)
    keyscale = sh.get("keyscale") or keyscale
    # ⚠ the METER has to be consumed too. Stripping "3/4 time" out of the
    # caption and then hard-coding the widget to 4 does not MOVE the metadata,
    # it DELETES it — the exact failure this shaping exists to prevent.
    timesignature = sh.get("timesignature") or timesignature
    shape_notes = sh.get("notes") or []

    seed = seed if seed is not None else int(time.time() * 1000) % 2**31
    jid = uuid.uuid4().hex[:10]
    st = {"id": jid, "kind": "music", "engine": engine,
          "label": label or (tags[:60] + "…"),
          "tags": tags, "lyrics_len": len(lyrics or ""),
          "seconds": seconds, "seed": seed, "worker": ready,
          "status": "queued", "detail": "submitting", "error": None,
          "elapsed_s": 0, "log": [], "at": _now(), "_t0": time.time()}
    if shape_notes:
        # a reshaped prompt is never a SILENT rewrite — the job says what moved
        st["prompt_notes"] = shape_notes
        st["raw_tags"] = raw_tags[:600]
        st["raw_lyrics_len"] = len(raw_lyrics or "")
    if meta:
        st["meta"] = meta
    with _LOCK:
        _JOBS[jid] = st
        _jobs_save()

    def _run():
        try:
            st["status"] = "running"
            if engine == "minimax3":
                _log(jid, f"building MiniMax Music 3 graph on {ready}")
                g = _mm3_graph(ready, tags, lyrics, seconds, seed,
                               f"RBMN-AUDIO/mm3_{jid}")
                if steps:
                    g["7"]["inputs"]["steps"] = int(steps)
                if cfg:
                    g["7"]["inputs"]["cfg"] = float(cfg)
            elif engine in ACE_XL:
                n, ds, dc = ACE_XL[engine]
                _log(jid, f"building ACE-Step XL ({engine.split('_')[-1]}) graph "
                          f"on {ready} — {int(steps or ds)} steps, "
                          f"cfg {float(cfg or dc)}")
                g = _ace_xl_graph(ready, engine, tags, lyrics, seconds, seed,
                                  bpm, keyscale, language,
                                  f"RBMN-AUDIO/acexl_{jid}", steps, cfg,
                                  timesignature=timesignature)
            else:
                _log(jid, f"building ACE-Step turbo graph on {ready}")
                g = _ace_graph(ready, tags, lyrics, seconds, seed,
                               bpm, keyscale, language,
                               f"RBMN-AUDIO/ace_{jid}",
                               timesignature=timesignature)
                if steps:
                    g["6"]["inputs"]["steps"] = int(steps)
                if cfg:
                    g["6"]["inputs"]["cfg"] = float(cfg)
            _log(jid, f"rendering {seconds:.0f}s of music on {ready}")
            fp = _run_graph_job(jid, ready, g)
            if normalize:
                note = _normalise(fp)
                st["loudness"] = note
                _log(jid, note)
            st["file"] = fp.name
            st["status"] = "done"
            st["elapsed_s"] = round(time.time() - st["_t0"], 1)
            _log(jid, f"done in {st['elapsed_s']}s → {fp.name}")
        except Exception as e:                                   # noqa: BLE001
            st["status"] = "error"
            st["error"] = f"{type(e).__name__}: {e}"
            st["elapsed_s"] = round(time.time() - st["_t0"], 1)
        finally:
            with _LOCK:
                _jobs_save()

    threading.Thread(target=_run, daemon=True, name=f"audio-{jid}").start()
    return {"started": True, "id": jid, "worker": ready}


@router.post("/music/generate")
async def music_generate(body: MusicIn):
    return enqueue_music(engine=body.engine, tags=body.tags,
                         lyrics=body.lyrics, seconds=body.seconds,
                         bpm=body.bpm, keyscale=body.keyscale,
                         timesignature=body.timesignature,
                         language=body.language, seed=body.seed,
                         host=body.host, label=body.label,
                         steps=body.steps, cfg=body.cfg,
                         normalize=body.normalize)


# ── 🎤 THE VOICE LIBRARY (v1.277.37) ─────────────────────────────────────────
# His ask: *"if I upload a long audio sample, more than what we need for a
# voice clone, give an option to cut it to the needed size. Also make sure we
# save voices to use later… and see a generated voice's details."*
#
# **THE CAP IS 12 SECONDS AND IT IS NOT ADVISORY.** ComfyUI-F5-TTS hard-cuts
# the reference at 12,000 ms — MID-WORD. Feed it 90 s and it silently uses the
# first 12 and clones from a fragment whose transcript no longer matches the
# audio, which is the single worst thing you can do to F5: the transcript is
# not a label, it is the ALIGNMENT. So a long upload is not rejected and it is
# not silently truncated either — the SOURCE is kept whole, a CLIP is cut from
# it, and every trim re-cuts from the source so repeated edits never compound.
#
# Layout (both files, per voice):
#     voices/<id>.<ext>          the CLIP — the only thing F5 ever sees
#     voices/<id>_source.<ext>   the original upload, kept for re-trimming
# ⚠ `<id>.*` must keep matching ONLY the clip — that glob is how the render
# path finds the reference. `_source` in the stem is what keeps them apart.
_VOICES_FP = _VOICE_DIR / "voices.json"
F5_REF_CAP_S = 12.0
# ⚠ The UI used to say 5-15 s, which is what F5's own docs say; the NODE's
# limit is 12. Where the two disagree, the node wins — it is the thing running.
VOICE_GUIDE = (
    "Name the voice, then pick ANY length of clean speech — one speaker, no "
    "music, no room echo. It uploads, a 6-12 s reference is cut from it, and "
    "then you listen to that cut and type the words spoken IN IT. The "
    "transcript is the ALIGNMENT, not a label: if it does not match the clip, "
    "the clone drifts — which is why it is asked for after the cut, not before."
)


def _voices_raw() -> List[dict]:
    try:
        return json.loads(_VOICES_FP.read_text("utf-8"))
    except Exception:                                            # noqa: BLE001
        return []


def _clip_fp(v: dict) -> Optional[Path]:
    return next(iter(sorted(_VOICE_DIR.glob(f"{v['id']}.*"))), None)


def _source_fp(v: dict) -> Optional[Path]:
    return next(iter(sorted(_VOICE_DIR.glob(f"{v['id']}_source.*"))), None)


def _voice_norm(v: dict) -> dict:
    """Fill in what a pre-.37 record never stored, WITHOUT rewriting the file.

    Voices added before the library existed have no clip/source split and no
    measured duration. Measuring on read is cheap (ffprobe on a ≤12 s file) and
    it means an old voice shows up in the new details view with real numbers
    instead of blanks."""
    v = dict(v)
    clip, src = _clip_fp(v), _source_fp(v)
    v["has_source"] = bool(src)
    if "clip_seconds" not in v:
        v["clip_seconds"] = _probe_seconds(clip) if clip else 0.0
    if src and "source_seconds" not in v:
        v["source_seconds"] = _probe_seconds(src)
    v.setdefault("source_seconds", v.get("clip_seconds") or 0.0)
    v.setdefault("trim", None)
    v["over_cap"] = float(v.get("clip_seconds") or 0) > F5_REF_CAP_S + 0.25
    v["clip_bytes"] = clip.stat().st_size if clip else 0
    # ⚠ a voice with no transcript is USABLE as a saved clip and UNUSABLE as a
    # clone — the two states must be told apart on screen, not conflated
    v["needs_transcript"] = not str(v.get("transcript") or "").strip()

    # ⭐⭐⭐ DOES THE TRANSCRIPT DESCRIBE **THIS CLIP**? This is the single most
    # damaging way a clone goes wrong, and nothing checked it.
    #
    # F5 derives the whole generation's duration from the reference PAIR:
    #     duration = ref_len + (ref_len / ref_text_len) * gen_text_len
    # i.e. it measures characters-per-second FROM YOUR REFERENCE. Paste the
    # transcript of a 60-second export while we auto-trim the clip to 12 s and
    # the implied rate is 5x too fast — so **every chunk is allocated far too
    # few mel frames and the model crams the words in.** The result is a clone
    # that sounds *close* in timbre and MUMBLED — "very close but much lower
    # quality… harder to understand words", which is exactly what he reported.
    #
    # ⚠ We cannot know the true words, so this is a PLAUSIBILITY test, not a
    # transcription check: speech runs ~11-19 characters/second. Outside a
    # generous 5-30 band something is wrong, and the direction tells you which:
    # too many chars ⇒ the transcript covers more audio than the clip holds
    # (the auto-trim case, and the one that mumbles).
    _secs = float(v.get("clip_seconds") or 0.0)
    _chars = len(str(v.get("transcript") or "").strip())
    v["cps"] = round(_chars / _secs, 1) if (_secs > 0.4 and _chars) else 0.0
    v["transcript_warning"] = ""
    if v["cps"]:
        if v["cps"] > 30:
            v["transcript_warning"] = (
                f"the transcript is {_chars} characters for {_secs:.1f}s of "
                f"audio ({v['cps']:.0f}/s — speech is ~11-19/s). It probably "
                f"describes MORE than this clip contains — F5 will then rush "
                f"every render and the words will slur. Trim the transcript to "
                f"exactly what the clip says.")
        elif v["cps"] < 5:
            v["transcript_warning"] = (
                f"the transcript is only {_chars} characters for {_secs:.1f}s "
                f"({v['cps']:.1f}/s). If it does not cover the whole clip, F5 "
                f"will drawl every render.")
    # ⚠ a warning, never a block: he may have a legitimately fast or slow
    # reference, and refusing on a heuristic would be worse than saying so.
    # ⚠⚠ READINESS IS PER ENGINE, because the engines want different things.
    # `ready` used to mean "F5 can use this" and the UI greyed a voice out on
    # it — which would have hidden every transcript-less voice from CHATTERBOX,
    # the one engine that does not need a transcript. Keep `ready` meaning what
    # it always meant (F5) so nothing downstream silently changes, and publish
    # the per-engine truth beside it.
    v["ready_f5"] = bool(clip) and not v["needs_transcript"] and not v["over_cap"]
    # 🗣 zero-shot: a clip is the whole requirement. No transcript, no 12 s cap.
    v["ready_chatterbox"] = bool(clip)
    v["ready_kokoro"] = bool((v.get("kokoro") or {}).get("preset"))
    v["ready"] = v["ready_f5"]
    # what a voice can do RIGHT NOW, for the picker
    v["engines"] = [e for e, ok in (("chatterbox", v["ready_chatterbox"]),
                                    ("f5tts", v["ready_f5"]),
                                    ("kokoro", v["ready_kokoro"])) if ok]
    return v


def _voices() -> List[dict]:
    return [_voice_norm(v) for v in _voices_raw()]


def _voices_save(v: List[dict]) -> None:
    _VOICE_DIR.mkdir(parents=True, exist_ok=True)
    # strip the derived keys — they are recomputed on read, and a stale
    # `over_cap` on disk would outlive the trim that fixed it
    keep = [{k: x for k, x in row.items()
             if k not in ("over_cap", "has_source", "clip_bytes", "cps", "engines",
                          "ready_f5", "ready_chatterbox", "ready_kokoro",
                          "transcript_warning",
                          "needs_transcript", "ready", "summary", "next")}
            for row in v]
    _VOICES_FP.write_text(json.dumps(keep, indent=1), "utf-8")


def _probe_seconds(fp: Optional[Path]) -> float:
    """Duration via ffprobe — 0.0 when it is missing. 0 means UNKNOWN here,
    never "empty": without ffprobe every trim decision is the user's."""
    import shutil
    import subprocess
    if not fp or not fp.exists() or not shutil.which("ffprobe"):
        return 0.0
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                            "format=duration", "-of", "default=nw=1:nk=1",
                            str(fp)], capture_output=True, text=True,
                           timeout=60)
        return round(float((r.stdout or "0").strip() or 0), 2)
    except Exception:                                            # noqa: BLE001
        return 0.0


def _first_sound(fp: Path) -> float:
    """Where speech actually starts, via ffmpeg's silencedetect.

    A long take usually opens with room tone or a breath, and a reference clip
    that begins in silence spends part of its 12 s budget on nothing. This only
    SUGGESTS a start — the cut stays his call."""
    import shutil
    import subprocess
    if not shutil.which("ffmpeg"):
        return 0.0
    try:
        r = subprocess.run(
            ["ffmpeg", "-hide_banner", "-i", str(fp), "-af",
             "silencedetect=noise=-35dB:d=0.4", "-f", "null", "-"],
            capture_output=True, text=True, timeout=120)
        txt = (r.stderr or "")
        # a leading silence is reported as silence_start: 0 … silence_end: X
        m = re.search(r"silence_start:\s*0(?:\.0+)?\s", txt)
        if not m:
            return 0.0
        m2 = re.search(r"silence_end:\s*([0-9.]+)", txt[m.start():])
        return round(max(0.0, float(m2.group(1)) - 0.15), 2) if m2 else 0.0
    except Exception:                                            # noqa: BLE001
        return 0.0


def _cut(src: Path, dest: Path, start: float, seconds: float) -> None:
    """ffmpeg-cut `src` into `dest` — mono 24 kHz WAV, which is what F5 wants
    and what makes the clip a known quantity instead of whatever was uploaded."""
    import shutil
    import subprocess
    if not shutil.which("ffmpeg"):
        raise HTTPException(
            503, "trimming needs ffmpeg on the app host, and it is not "
                 "installed — upload a sample already cut to ≤12 s instead")
    dest.unlink(missing_ok=True)
    r = subprocess.run(
        ["ffmpeg", "-y", "-ss", f"{max(0.0, start):.3f}", "-i", str(src),
         "-t", f"{max(0.2, seconds):.3f}", "-ac", "1", "-ar", "24000",
         "-c:a", "pcm_s16le", str(dest)],
        capture_output=True, text=True, timeout=300)
    if r.returncode != 0 or not dest.exists():
        raise HTTPException(500, "ffmpeg could not cut that file: "
                                 + (r.stderr or "")[-300:])


@router.get("/tts/voices")
async def voices():
    return {"voices": _voices(), "cap_seconds": F5_REF_CAP_S,
            "cloning_guide": VOICE_GUIDE}


@router.post("/tts/voices")
async def add_voice(name: str = Form(...), transcript: str = Form(""),
                    file: UploadFile = File(...),
                    trim_start: float = Form(-1.0),
                    trim_seconds: float = Form(0.0)):
    """Add a voice. A long upload is TRIMMED, never truncated behind your back.

    `trim_start` < 0 means "find it for me" — the first non-silent moment.
    `trim_seconds` 0 means "as much as allowed" (the 12 s cap).
    The whole upload is kept as the SOURCE so the window can be moved later
    without asking for the file again.

    ⭐ **The transcript is OPTIONAL here, on purpose (v1.277.38).** It must be
    the words spoken in the CLIP, and the clip does not exist until this call
    has cut it — so demanding it up front asked him to transcribe a window he
    had not heard. A voice without one is saved, flagged `needs_transcript`,
    and refused by `tts/generate` with a message that says why."""
    if not name.strip():
        raise HTTPException(400, "the voice needs a name")
    data = await file.read()
    if len(data) < 8_000:
        raise HTTPException(400, "that file is too small to be a usable "
                                 "reference — aim for 6-12s of clean speech")
    vid = uuid.uuid4().hex[:8]
    _VOICE_DIR.mkdir(parents=True, exist_ok=True)
    ext = (Path(file.filename or "ref.wav").suffix or ".wav").lower()
    src = _VOICE_DIR / f"{vid}_source{ext}"
    src.write_bytes(data)
    total = _probe_seconds(src)

    rec = {"id": vid, "name": name.strip(), "ext": ext,
           "transcript": (transcript or "").strip(), "at": _now(),
           "source_filename": file.filename or f"ref{ext}",
           "source_seconds": total, "source_bytes": len(data)}

    needs_cut = (total > F5_REF_CAP_S + 0.25) or trim_start >= 0 or trim_seconds > 0
    if needs_cut and total > 0:
        start = _first_sound(src) if trim_start < 0 else max(0.0, trim_start)
        span = trim_seconds if trim_seconds > 0 else F5_REF_CAP_S
        span = min(span, F5_REF_CAP_S, max(0.2, total - start))
        clip = _VOICE_DIR / f"{vid}.wav"
        _cut(src, clip, start, span)
        rec["ext"] = ".wav"
        rec["trim"] = {"start": round(start, 2), "seconds": round(span, 2),
                       "at": _now(), "auto": trim_start < 0}
        rec["clip_seconds"] = _probe_seconds(clip)
    else:
        # short enough already — keep the bytes he gave us, untouched
        (_VOICE_DIR / f"{vid}{ext}").write_bytes(data)
        rec["clip_seconds"] = total

    v = _voices_raw()
    v.append(rec)
    _voices_save(v)
    out = _voice_norm(rec)
    # a receipt in plain words — "it uploaded" is not the same claim as "this
    # is what I did with it", and only the second one lets him check the work
    if rec.get("trim"):
        out["summary"] = (
            f"Uploaded {rec['source_filename']} ({len(data) / 1048576:.1f} MB, "
            f"{total:.1f}s) and cut a {out['clip_seconds']:.1f}s reference "
            f"starting at {rec['trim']['start']:.1f}s"
            + (" (found the first speech automatically)"
               if rec['trim'].get('auto') else "") + ".")
    else:
        out["summary"] = (f"Uploaded {rec['source_filename']} "
                          f"({len(data) / 1048576:.1f} MB, "
                          f"{out['clip_seconds']:.1f}s) — already within the "
                          f"{F5_REF_CAP_S:.0f}s cap, kept as-is.")
    out["next"] = ("▶ Listen to the clip, then type the words spoken IN IT. "
                   "F5 aligns audio to transcript, so it must match this "
                   "window — not the whole take.")
    return out


class VoiceTrimIn(BaseModel):
    start: float = 0.0
    seconds: float = F5_REF_CAP_S
    transcript: Optional[str] = None      # the words in the NEW window


@router.post("/tts/voices/{vid}/trim")
async def trim_voice(vid: str, body: VoiceTrimIn):
    """Re-cut the clip from the SOURCE — never from the current clip.

    Cutting a cut is how you end up with 3 s of reference after four small
    adjustments, each of which looked reasonable on its own."""
    v = _voices_raw()
    row = next((x for x in v if x["id"] == vid), None)
    if not row:
        raise HTTPException(404, "no such voice")
    src = _source_fp(row)
    if not src:
        raise HTTPException(
            409, "this voice has no stored source — it was added before the "
                 "library kept one. Re-add it from the original file to trim.")
    total = _probe_seconds(src)
    start = max(0.0, body.start)
    span = min(max(0.2, body.seconds), F5_REF_CAP_S,
               max(0.2, (total - start) if total else body.seconds))
    for fp in _VOICE_DIR.glob(f"{vid}.*"):
        fp.unlink(missing_ok=True)
    clip = _VOICE_DIR / f"{vid}.wav"
    _cut(src, clip, start, span)
    row["ext"] = ".wav"
    row["clip_seconds"] = _probe_seconds(clip)
    row["trim"] = {"start": round(start, 2), "seconds": round(span, 2),
                   "at": _now(), "auto": False}
    if body.transcript and body.transcript.strip():
        row["transcript"] = body.transcript.strip()
    _voices_save(v)
    out = _voice_norm(row)
    out["note"] = ("clip re-cut from the source. ⚠ The transcript must be the "
                   "words spoken in THIS window."
                   if not body.transcript else "clip and transcript updated")
    return out


class VoiceEditIn(BaseModel):
    name: Optional[str] = None
    transcript: Optional[str] = None
    notes: Optional[str] = None


@router.post("/tts/voices/{vid}/update")
async def update_voice(vid: str, body: VoiceEditIn):
    v = _voices_raw()
    row = next((x for x in v if x["id"] == vid), None)
    if not row:
        raise HTTPException(404, "no such voice")
    for k in ("name", "transcript", "notes"):
        val = getattr(body, k)
        if val is not None and str(val).strip():
            row[k] = str(val).strip()
    _voices_save(v)
    return _voice_norm(row)


@router.get("/tts/voices/{vid}/audio")
async def voice_audio(vid: str, which: str = "clip", download: bool = False):
    """Hear the reference — the CLIP is what F5 gets, the SOURCE is the whole
    upload. Auditioning the clip is the only way to catch a window that ends
    mid-word."""
    row = next((x for x in _voices_raw() if x["id"] == vid), None)
    if not row:
        raise HTTPException(404, "no such voice")
    fp = _source_fp(row) if which == "source" else _clip_fp(row)
    if not fp or not fp.exists():
        raise HTTPException(404, f"no {which} audio for that voice")
    mt = {".mp3": "audio/mpeg", ".wav": "audio/wav", ".flac": "audio/flac",
          ".m4a": "audio/mp4", ".ogg": "audio/ogg"}.get(fp.suffix.lower(),
                                                        "audio/wav")
    if download:
        return FileResponse(str(fp), media_type=mt, filename=fp.name)
    return FileResponse(str(fp), media_type=mt)


@router.get("/tts/voices/{vid}")
async def voice_detail(vid: str):
    """🪪 Everything this voice has ever done.

    His ask, verbatim: *"see when it was made, its source audio, the outputs
    using the voice, stories it was used on"* — so the answer is assembled from
    the job board (every render tagged with this voice) and from the `used_in`
    breadcrumbs the import routes leave behind. It doubles as a way to listen
    to only the tracks this voice made.

    ⚠ Jobs are matched on `voice_id`, with a NAME fallback for renders made
    before .37 tagged the id. Renaming a voice therefore orphans its old
    renders — which is exactly why the id is written now."""
    row = next((x for x in _voices_raw() if x["id"] == vid), None)
    if not row:
        raise HTTPException(404, "no such voice")
    out = _voice_norm(row)
    jobs, projects, stories = [], {}, {}
    for j in sorted(_JOBS.values(), key=lambda x: x.get("at") or "",
                    reverse=True):
        if j.get("kind") != "tts":
            continue
        if not (j.get("voice_id") == vid
                or (not j.get("voice_id") and j.get("voice") == row.get("name"))):
            continue
        jobs.append({"id": j.get("id"), "label": j.get("label"),
                     "status": j.get("status"), "at": j.get("at"),
                     "elapsed_s": j.get("elapsed_s"),
                     "seconds": j.get("seconds"),
                     "chunks": j.get("chunks"), "worker": j.get("worker"),
                     "file": j.get("file"),
                     "used_in": j.get("used_in") or []})
        for u in (j.get("used_in") or []):
            if u.get("kind") == "project":
                projects[u.get("project_id")] = u
            elif u.get("kind") == "story":
                stories[f"{u.get('world_id')}/{u.get('story_id')}"] = u
    out["renders"] = jobs
    out["render_count"] = len(jobs)
    out["projects"] = list(projects.values())
    out["stories"] = list(stories.values())
    out["cap_seconds"] = F5_REF_CAP_S
    return out


@router.post("/tts/voices/{vid}/delete")
async def del_voice(vid: str):
    v = [x for x in _voices_raw() if x["id"] != vid]
    _voices_save(v)
    for fp in list(_VOICE_DIR.glob(f"{vid}.*")) + list(
            _VOICE_DIR.glob(f"{vid}_source.*")):
        fp.unlink(missing_ok=True)
    return {"ok": True}


# ── 🎨 KOKORO — VOICES WITHOUT A REFERENCE (v1.277.43) ───────────────────────
# His question: *"is there any way to generate voices without a reference?"*
# Kokoro-82M ships ~54 built-in speakers and can BLEND two of them into a voice
# that exists nowhere else. F5 cannot do that — it needs a clip to clone.
#
# ⭐⭐ IT RUNS HERE, ON THE APP HOST, NOT ON THE FLEET — and that was measured,
# not preferred. The ComfyUI node route died on the workers' **Python 3.13**:
# `misaki` and `numpy==1.26.4` have no 3.13 wheels (`pip --only-binary=:all:`
# said so outright), which is the same wall the Geeky node's README warns about.
# The app venv is 3.11, Kokoro is 82M params and real-time on CPU, and the job
# is ten seconds of audio — so the GPU boxes were never the right place.
# ⚠ First call downloads the model from HF (~330 MB) and costs ~60 s; after
# that a 10 s sample is a couple of seconds.
#
# **The point is not Kokoro as an engine — it is Kokoro as a VOICE FACTORY.**
# Render a sample with a preset (or a blend), save it into the voice library,
# and F5 clones it from there. The transcript is then EXACT BY CONSTRUCTION,
# which removes the single most common cause of a drifting clone: a reference
# whose words do not match its audio.
KOKORO_PRESETS = [
    # (id, label, note) — the English set, which is what narration needs
    ("af_heart", "Heart ❤️ · US female", "warm, friendly — the default narrator"),
    ("af_bella", "Bella 🔥 · US female", "energetic, dynamic"),
    ("af_nicole", "Nicole 🎧 · US female", "clear, professional"),
    ("af_aoede", "Aoede 🎵 · US female", "musical, expressive"),
    ("af_kore", "Kore · US female", "balanced, versatile"),
    ("af_sarah", "Sarah · US female", "neutral, calm"),
    ("af_nova", "Nova ⭐ · US female", "bright, modern"),
    ("af_sky", "Sky ☁️ · US female", "soft, gentle"),
    ("af_alloy", "Alloy · US female", "professional, authoritative"),
    ("af_jessica", "Jessica · US female", "friendly, approachable"),
    ("af_river", "River 🌊 · US female", "flowing — long-form narration"),
    ("am_michael", "Michael · US male", "deep, authoritative — documentary"),
    ("am_fenrir", "Fenrir 🐺 · US male", "strong, bold"),
    ("am_puck", "Puck 🎭 · US male", "playful, character-driven"),
    ("am_echo", "Echo 🔊 · US male", "clear, resonant"),
    ("am_eric", "Eric · US male", "reliable, professional"),
    ("am_liam", "Liam · US male", "modern, relatable"),
    ("am_onyx", "Onyx 💎 · US male", "rich, deep, elegant"),
    ("am_adam", "Adam · US male", "classic, dependable"),
    ("am_santa", "Santa 🎅 · US male", "warm, jolly"),
    ("bf_emma", "Emma · UK female", "refined, elegant"),
    ("bf_isabella", "Isabella · UK female", "professional, articulate"),
    ("bf_alice", "Alice 📚 · UK female", "storytelling, engaging"),
    ("bf_lily", "Lily 🌸 · UK female", "gentle, pleasant"),
    ("bm_george", "George · UK male", "authoritative, commanding"),
    ("bm_fable", "Fable 📖 · UK male", "narrative — audiobooks"),
    ("bm_lewis", "Lewis · UK male", "reliable, clear"),
    ("bm_daniel", "Daniel · UK male", "modern, professional"),
]
#: what a factory voice says when he does not supply his own words. Chosen for
#: PHONETIC COVERAGE, not meaning: a reference clip that never voices an 'sh'
#: or a hard 'g' clones badly on words that do.
KOKORO_SAMPLE = (
    "The rail camp woke slowly, and the whistle carried a long way across the "
    "cold valley floor. Nobody spoke much; there was too much to do before "
    "the light changed."
)
_KOKORO_PIPE = None


def _kokoro_pipe():
    """One pipeline, loaded once — it holds the model in RAM."""
    global _KOKORO_PIPE
    if _KOKORO_PIPE is None:
        from kokoro import KPipeline
        _KOKORO_PIPE = KPipeline(lang_code="a", repo_id="hexgrad/Kokoro-82M")
    return _KOKORO_PIPE


def kokoro_available() -> tuple:
    try:
        import kokoro                                            # noqa: F401
        return True, ""
    except Exception as e:                                       # noqa: BLE001
        return False, (f"{type(e).__name__}: {e} — install it into the APP "
                       f"venv: venv\\Scripts\\python -m pip install "
                       f"--only-binary=:all: kokoro misaki[en]")


def kokoro_render(text: str, voice: str, voice_b: str = "", blend: float = 1.0,
                  speed: float = 1.0) -> Path:
    """Speak `text` with a preset, or with a BLEND of two presets.

    ⚠ Blending happens on the voice TENSORS, not on the audio: mixing two
    finished takes gives you two people talking at once, while mixing the
    embeddings gives you one person who does not exist."""
    import numpy as np
    import soundfile as sf
    pipe = _kokoro_pipe()
    v = pipe.load_voice(voice)
    if voice_b and 0.0 <= blend < 1.0:
        vb = pipe.load_voice(voice_b)
        v = v * float(blend) + vb * (1.0 - float(blend))
    parts = [np.asarray(a) for _gs, _ps, a in pipe(text, voice=v, speed=speed)]
    if not parts:
        raise RuntimeError("kokoro produced no audio")
    wav = np.concatenate(parts)
    _VOICE_DIR.mkdir(parents=True, exist_ok=True)
    fp = _VOICE_DIR / f"_kokoro_{uuid.uuid4().hex[:8]}.wav"
    sf.write(str(fp), wav, 24000)
    return fp


@router.get("/tts/kokoro/presets")
async def kokoro_presets():
    ok, note = kokoro_available()
    return {"ready": ok, "note": note,
            "presets": [{"id": i, "label": l, "note": n}
                        for i, l, n in KOKORO_PRESETS],
            "sample_text": KOKORO_SAMPLE}


#: auditioning is a browsing activity — the same preset gets clicked five times
#: while comparing, so a preview is CACHED on disk and answered instantly the
#: second time. Keyed by everything that changes the sound.
_PREVIEW_DIR = _VOICE_DIR / "_previews"
PREVIEW_LINE = "Every hour the wind shifted, and the light changed with it."


@router.get("/tts/kokoro/preview")
async def kokoro_preview(preset: str, preset_b: str = "", blend: float = 1.0,
                         speed: float = 1.0, text: str = ""):
    """▶ Hear a speaker before committing to it — including a BLEND.

    His words: *"im looking for a particular sounding voice and it seems all we
    can do with this is merge voices"*. A dropdown of 28 names is not a way to
    choose a voice; hearing them is."""
    ok, note = kokoro_available()
    if not ok:
        raise HTTPException(503, note)
    if preset not in {p[0] for p in KOKORO_PRESETS}:
        raise HTTPException(400, f"unknown preset {preset!r}")
    line = (text or PREVIEW_LINE).strip()[:400]
    key = hashlib_md5(f"{preset}|{preset_b}|{blend:.2f}|{speed:.2f}|{line}")
    _PREVIEW_DIR.mkdir(parents=True, exist_ok=True)
    fp = _PREVIEW_DIR / f"{key}.wav"
    if not fp.exists():
        import asyncio
        src = await asyncio.to_thread(kokoro_render, line, preset, preset_b,
                                      blend, speed)
        src.replace(fp)
    return FileResponse(str(fp), media_type="audio/wav")


def hashlib_md5(text: str) -> str:
    import hashlib
    return hashlib.md5(text.encode("utf-8")).hexdigest()[:16]


class KokoroVoiceIn(BaseModel):
    name: str
    preset: str
    preset_b: str = ""               # blend partner (optional)
    blend: float = 1.0               # 1.0 = all preset, 0.5 = even mix
    text: str = ""                   # what the reference clip should say
    speed: float = 1.0
    save: bool = True                # add it to the voice library


@router.post("/tts/kokoro/create")
async def kokoro_create(body: KokoroVoiceIn):
    """🎨 Invent a voice, no recording required.

    Renders a reference clip with a Kokoro preset (or a blend of two) and files
    it in the voice library like any other voice — with the transcript already
    filled in, because we chose the words."""
    ok, note = kokoro_available()
    if not ok:
        raise HTTPException(503, note)
    if not body.name.strip():
        raise HTTPException(400, "the voice needs a name")
    ids = {p[0] for p in KOKORO_PRESETS}
    if body.preset not in ids:
        raise HTTPException(400, f"unknown preset {body.preset!r}")
    if body.preset_b and body.preset_b not in ids:
        raise HTTPException(400, f"unknown blend preset {body.preset_b!r}")
    text = (body.text or KOKORO_SAMPLE).strip()
    t0 = time.time()
    try:
        src = await asyncio_to_thread(kokoro_render, text, body.preset,
                                      body.preset_b, body.blend, body.speed)
    except Exception as e:                                       # noqa: BLE001
        raise HTTPException(500, f"kokoro failed: {type(e).__name__}: {e}")
    total = _probe_seconds(src)
    if not body.save:
        src.unlink(missing_ok=True)
        return {"seconds": total, "elapsed_s": round(time.time() - t0, 1)}

    vid = uuid.uuid4().hex[:8]
    dest_src = _VOICE_DIR / f"{vid}_source.wav"
    src.replace(dest_src)
    clip = _VOICE_DIR / f"{vid}.wav"
    span = min(F5_REF_CAP_S, max(0.5, total))
    if total > F5_REF_CAP_S + 0.25:
        # ⚠ trim to a SENTENCE end, not to the cap: a reference that stops
        # mid-word is the thing this whole lane exists to avoid, and here we
        # KNOW the words, so we can cut where the sentence does.
        _cut(dest_src, clip, 0.0, span)
        spoken = _fit_transcript(text, span / total)
    else:
        _cut(dest_src, clip, 0.0, total)
        spoken = text
    rec = {"id": vid, "name": body.name.strip(), "ext": ".wav",
           "transcript": spoken, "at": _now(),
           "source_filename": f"kokoro:{body.preset}"
                              + (f"+{body.preset_b}@{body.blend}" if body.preset_b else ""),
           "source_seconds": total, "source_bytes": dest_src.stat().st_size,
           "clip_seconds": _probe_seconds(clip),
           "kokoro": {"preset": body.preset, "preset_b": body.preset_b,
                      "blend": body.blend, "speed": body.speed},
           "trim": {"start": 0.0, "seconds": round(span, 2), "at": _now(),
                    "auto": True}}
    v = _voices_raw()
    v.append(rec)
    _voices_save(v)
    out = _voice_norm(rec)
    out["summary"] = (
        f"🎨 Made '{rec['name']}' from {body.preset}"
        + (f" blended {int(body.blend * 100)}/{int((1 - body.blend) * 100)} with "
           f"{body.preset_b}" if body.preset_b else "")
        + f" — {out['clip_seconds']:.1f}s reference in "
          f"{time.time() - t0:.0f}s, transcript already filled in.")
    out["elapsed_s"] = round(time.time() - t0, 1)
    return out


def _fit_transcript(text: str, ratio: float) -> str:
    """The words that fit in the kept fraction of the clip, cut at a sentence.

    ⚠ Approximate BY DESIGN: an over-long transcript skews F5's duration
    estimate (it derives seconds-per-character from this ratio), so it is
    better to lose a sentence than to claim words the clip never says."""
    keep = max(1, int(len(text) * max(0.1, min(1.0, ratio))))
    head = text[:keep]
    cut = max(head.rfind("."), head.rfind("!"), head.rfind("?"))
    return (head[:cut + 1] if cut > 20 else head).strip()


async def asyncio_to_thread(fn, *a, **kw):
    import asyncio
    return await asyncio.to_thread(fn, *a, **kw)


class TtsIn(BaseModel):
    voice_id: str
    # 🗣 WHICH engine speaks. `f5tts` clones the voice's reference clip on a
    # worker; `kokoro` speaks directly on the app host with the preset the
    # voice was MADE from — instant, no GPU, no clone step. Only a factory
    # voice can use `kokoro`, because only it carries a preset.
    engine: str = "f5tts"            # f5tts | kokoro
    text: str
    pause_ms: int = 600              # silence between paragraphs (blank lines)
    # 🐢 ONE pace control, >1.0 = SLOWER, and TWO ways to deliver it:
    #
    #   "stretch" (DEFAULT) — render at the model's native pace, then
    #       time-stretch the AUDIO with rubberband. F5's quality is best at its
    #       own speed; asking it to fill a longer duration is what made his
    #       slow takes "really bad". A vocoder is not a tape machine.
    #   "model" — the old path: hand the pace to the node's `speed` input.
    #       Kept so the two are comparable on the same line, not on faith.
    #
    # ⚠⚠ The NODE's `speed` is INVERTED and that is MEASURED (2026-08-18, .163,
    # one sentence, same seed): 0.8 → 4.86 s · 1.0 → 6.07 s · 1.2 → 7.28 s.
    # >1.0 is SLOWER there too, which is the opposite of upstream F5 (where
    # speed divides the predicted duration). Do not "fix" that by inverting it
    # in code — the value goes to the node untouched and the LABEL carries the
    # meaning.
    pace: float = 1.0
    pace_mode: str = "stretch"       # stretch | model
    speed: float = 1.0               # legacy alias for pace in "model" mode
    # 🫁 a gap at every sentence end, which slows the READ without touching the
    # voice — the pause is silence, so it cannot introduce artifacts at all
    sentence_pause_ms: int = 0
    seed: Optional[int] = None
    host: str = ""
    label: str = ""

    # 🗣 CHATTERBOX (v1.277.51) — MIT, so it is the engine this app can ship to
    # the public. Zero-shot: it clones from the CLIP ALONE and needs no
    # reference transcript, which removes F5's worst failure mode outright.
    # Every default below is the node's own, read off the box.
    cb_language: str = "English"
    #: 0.25-2.0. The dial for CHARACTER — an old western narrator wants this
    #: above 0.5. Above ~1.0 it gets theatrical.
    exaggeration: float = 0.5
    #: 0.05-5.0. Randomness. Lower is steadier across a long narration.
    temperature: float = 0.8
    #: 0.0-1.0. How hard it holds to the reference. >0 follows the clip's
    #: accent and delivery; 0 ignores the accent and uses learned patterns.
    cfg_weight: float = 0.5
    #: ⚠ see CB_CRASH_TEMPLATE — the node's default pads short segments with
    #: the WORD "hmm", which our sentence-level chunking hits constantly.
    cb_crash_template: str = ""


@router.post("/tts/generate")
async def tts_generate(body: TtsIn):
    voice = next((v for v in _voices() if v["id"] == body.voice_id), None)
    if not voice:
        raise HTTPException(404, "no such voice — add one first (a 6-12s "
                                 "clean sample + its exact transcript)")
    if not body.text.strip():
        raise HTTPException(400, "give it text to speak")
    engine = (body.engine or "chatterbox").lower()
    # ⭐⭐ THESE TWO GATES ARE **F5-SPECIFIC**, and saying so is the point.
    # Chatterbox is ZERO-SHOT: it clones from the clip alone, needs no
    # transcript, and has no 12 s hard cut — so refusing a voice on F5's rules
    # would lock him out of the engine that fixes the problem. A gate copied to
    # an engine it does not describe is how a feature arrives already broken.
    if engine == "f5tts":
        if voice.get("needs_transcript"):
            raise HTTPException(
                400, f"'{voice['name']}' has no transcript yet. Open 🪪 Details, "
                     f"play the reference clip, and type the exact words spoken "
                     f"in it — F5 ALIGNS the audio to those words, so it cannot "
                     f"clone without them. (🗣 Chatterbox needs no transcript.)")
        if voice.get("over_cap"):
            # 🎤 refuse rather than let the node cut it mid-word behind his back
            raise HTTPException(
                409, f"'{voice['name']}' has a {voice.get('clip_seconds')}s "
                     f"reference clip and F5 hard-cuts at {F5_REF_CAP_S:.0f}s — "
                     f"trim it first (🎤 the voice's ✂ Trim), or the clone is "
                     f"built from a fragment your transcript does not match. "
                     f"(🗣 Chatterbox has no such cap.)")
    if engine not in ("f5tts", "kokoro", "chatterbox"):
        raise HTTPException(400, "engine must be chatterbox, f5tts or kokoro")
    if engine == "chatterbox":
        # ⭐ ZERO-SHOT: no reference transcript needed. So the checks above that
        # refuse a voice for `needs_transcript` do not apply — but they run
        # before this point, so they are relaxed for this engine there.
        if not (0.25 <= body.exaggeration <= 2.0):
            raise HTTPException(400, "exaggeration must be 0.25-2.0")
        if not (0.05 <= body.temperature <= 5.0):
            raise HTTPException(400, "temperature must be 0.05-5.0")
        if not (0.0 <= body.cfg_weight <= 1.0):
            raise HTTPException(400, "cfg_weight must be 0.0-1.0")
        if (body.cb_crash_template or "") and "{seg}" not in body.cb_crash_template:
            # ⚠ without the placeholder the node would speak the template and
            # DROP the actual line — a silent content loss, not an error.
            raise HTTPException(400, "cb_crash_template must contain {seg}")
    kk = voice.get("kokoro") or {}
    if engine == "kokoro" and not kk.get("preset"):
        raise HTTPException(
            400, f"'{voice['name']}' is a RECORDED voice — Kokoro cannot clone "
                 f"a recording, it only speaks its own built-in speakers. Use "
                 f"F5 for this voice, or make a 🎨 factory voice to use Kokoro.")
    if engine == "kokoro":
        ok, note = kokoro_available()
        if not ok:
            raise HTTPException(503, note)
    hosts = [h["host"] for h in _hosts()]
    if body.host:
        hosts = [body.host]
    ready = ("app host (kokoro)" if engine == "kokoro" else
             next((h for h in hosts if _engine_status(h)[engine]["ready"]), None))
    if not ready:
        raise HTTPException(
            409,
            "Chatterbox is not installed on any worker yet — run: "
            "python scripts/install_chatterbox.py --host <box>, restart that "
            "box's ComfyUI, then --check. (⚠ install ONE box first.)"
            if engine == "chatterbox" else
            "F5-TTS is not installed on any worker yet — run: "
            "python scripts/install_audio.py (installs the ComfyUI-F5-TTS node "
            "on every box; models auto-download on first use)")
    # chunk on blank lines — his pause control between paragraphs/sections —
    # and, when asked, on SENTENCES too, so a full stop can carry its own gap.
    # `gaps[i]` is the silence AFTER chunk i, so one pass produces both.
    pace = float(body.pace if body.pace and body.pace != 1.0 else body.speed or 1.0)
    mode = (body.pace_mode or "stretch").lower()
    if mode not in ("stretch", "model"):
        raise HTTPException(400, "pace_mode must be 'stretch' or 'model'")
    # ⚠ VALIDATE THE PACE. `atempo` clamps to [0.5, 2.0] while `pace` allowed
    # [0.25, 4.0], so pace=3.0 on a box without rubberband stretched the audio
    # ×2 and would have scaled the cues ×3 — a 50% cue error from a value the
    # UI never offers. Refuse it rather than silently disagreeing with the file.
    if not (0.5 <= pace <= 2.0):
        raise HTTPException(400, f"pace must be between 0.5 and 2.0 (got "
                                 f"{pace}) — beyond that the stretch filter "
                                 f"clamps and the cue times would no longer "
                                 f"describe the audio")
    chunks, gaps = plan_chunks(body.text, body.pause_ms, body.sentence_pause_ms)
    if not chunks:
        raise HTTPException(400, "there is nothing to speak once the pause tags "
                                 "are removed")
    gaps = gaps[:-1] if gaps else []          # no trailing silence on the end
    seed = body.seed if body.seed is not None else int(time.time()) % 2**31
    jid = uuid.uuid4().hex[:10]
    st = {"id": jid, "kind": "tts", "engine": engine,
          "label": body.label or (body.text[:60] + "…"),
          # ⚠ the ID, not just the name — the details view matches on it, and
          # a renamed voice would otherwise orphan every render it ever made
          "voice": voice["name"], "voice_id": voice["id"],
          "chunks": len(chunks),
          "pause_ms": body.pause_ms,
          "sentence_pause_ms": body.sentence_pause_ms,
          "pace": pace, "pace_mode": mode, "worker": ready,
          "status": "queued", "detail": "submitting", "error": None,
          "elapsed_s": 0, "log": [], "at": _now(), "_t0": time.time()}
    with _LOCK:
        _JOBS[jid] = st
        _jobs_save()

    def _run():
        try:
            st["status"] = "running"
            parts: List[Path] = []
            base = ""
            up_name = ""
            if engine in ("f5tts", "chatterbox"):
                base = f"http://{ready}:8188"
                # upload the reference once — both worker engines clone from
                # the same clip; only F5 additionally needs its transcript.
                ref_fp = next(_VOICE_DIR.glob(f"{voice['id']}.*"))
                up_name = _upload_input(base, ref_fp)
            for i, chunk in enumerate(chunks):
                _log(jid, f"chunk {i + 1}/{len(chunks)} on {ready}")
                st["elapsed_s"] = round(time.time() - st["_t0"], 1)
                if engine == "kokoro":
                    # ⚠ the node's `speed` is inverted; kokoro's is NOT — here
                    # >1.0 is genuinely faster, so a pace of 1.2 must become
                    # 1/1.2. Two engines, two conventions, one label.
                    parts.append(kokoro_render(
                        chunk, kk["preset"], kk.get("preset_b") or "",
                        float(kk.get("blend") or 1.0),
                        (1.0 / pace) if (mode == "model" and pace) else 1.0))
                    continue
                if engine == "chatterbox":
                    # ⚠ Chatterbox has NO speed input at all — pace is always
                    # the post-render stretch. `pace_mode="model"` is silently
                    # meaningless here, so it is refused up front rather than
                    # pretending to have done something.
                    g = _chatterbox_graph(
                        ready, up_name, chunk, seed + i,
                        f"RBMN-AUDIO/tts_{jid}_{i}",
                        language=body.cb_language,
                        exaggeration=body.exaggeration,
                        temperature=body.temperature,
                        cfg_weight=body.cfg_weight,
                        crash_template=body.cb_crash_template or CB_CRASH_TEMPLATE)
                    parts.append(_run_graph_job(jid, ready, g, ".wav",
                                                timeout_s=900,
                                                stem=f"{jid}_p{i:03d}"))
                    continue
                # in "stretch" mode the model always runs at its own pace —
                # that is the whole point; the clock moves afterwards
                g = _f5_graph(ready, up_name, voice["transcript"], chunk,
                              seed + i, pace if mode == "model" else 1.0,
                              f"RBMN-AUDIO/tts_{jid}_{i}")
                parts.append(_run_graph_job(jid, ready, g, ".wav",
                                            timeout_s=900,
                                            stem=f"{jid}_p{i:03d}"))
            _log(jid, f"concatenating: {len(parts)} piece(s), gaps "
                      f"{gaps[:6]}{'…' if len(gaps) > 6 else ''} ms")
            spans: List[List[float]] = []
            out = _concat_with_pauses(parts, gaps or body.pause_ms, jid, spans)
            if mode == "stretch" and abs(pace - 1.0) >= 0.01:
                _log(jid, f"pacing ×{pace} after the fact (pitch preserved)")
                out, how, applied = _stretch(out, pace, jid)
                st["paced_with"] = how
                if how:
                    _log(jid, f"paced with {how}")
                # ⚠⚠ THE STRETCH MOVES EVERY CUE. `_stretch` runs at
                # tempo = 1/pace over the WHOLE joined file, so a cue captured
                # before it is wrong by exactly that factor. Scaling here (not
                # at read time) means the stored cues are always in the same
                # clock as the file beside them — one truth, not two.
                # ⭐ Scale by the tempo ACTUALLY APPLIED (`1/applied`), not by
                # the requested `pace`: the filter string is 4 dp and `atempo`
                # additionally clamps, so those are not the same number.
                k = 1.0 / applied if applied else 1.0
                spans = [[s * k, e * k] for s, e in spans]
                st["pace_applied"] = round(k, 6)
            # ⭐ THE CUE LIST: what was said, and exactly when. Free — these are
            # measurements of the parts we just joined, not a transcription of
            # our own output. `chunks` is the SPOKEN text (pause tags already
            # stripped), so a cue never contains a tag nobody said.
            # 6 dp = microseconds. Enough that the rounding is far below one
            # sample at any rate we use, and not so much that the JSON is noise.
            st["cues"] = [{"i": i, "start": round(s, 6), "end": round(e, 6),
                           "text": chunks[i]}
                          for i, (s, e) in enumerate(spans) if i < len(chunks)]
            # 🔊 LEVEL. ⭐⭐ F5 SCALES ITS OUTPUT BACK DOWN TO THE REFERENCE
            # CLIP'S OWN RMS (`utils_infer.py`: `if rms < target_rms: wave *=
            # rms / target_rms`). So a quiet 12-second reference produces a
            # narration 6-10 dB under an ElevenLabs master — and this project
            # has already learned, in writing, that quieter reads as
            # **"muffled"** (`_normalise`'s own docstring: turbo at -12.9 LUFS
            # beat -14.5 renders and the quieter ones "were called muffled").
            # He reported exactly that word about his clone. Music was
            # normalised from the start; narration never was.
            # ⚠ -16 LUFS, not music's -14: broadcast speech sits lower, and a
            # narration bed has to leave room for the score under it.
            # ⚠ A pure gain change cannot move a cue — but `st["seconds"]` is
            # probed AFTER this, so if it ever did, the cue check below catches
            # it rather than trusting that it did not.
            note = _normalise(out, lufs=-16.0, peak=-1.5)
            st["loudness"] = note
            _log(jid, f"🔊 {note}")
            for p in parts:
                if p != out:
                    p.unlink(missing_ok=True)
            st["file"] = out.name
            # ⏱ the OUTPUT length, measured — a narration lane that does not
            # record how long it spoke cannot answer "did `speed` do anything?",
            # and the render board was showing chunk counts instead of duration
            st["seconds"] = _probe_seconds(out)
            st["status"] = "done"
            st["elapsed_s"] = round(time.time() - st["_t0"], 1)
            # ⭐ MEASURE, DO NOT INFER: the last cue should land on the end of
            # the file. If it does not, the cue list is lying about a real file
            # and every scene built from it would be wrong — say so loudly
            # rather than shipping plausible nonsense.
            # ⚠⚠ CHECKING ONLY THE LAST CUE IS THE WEAKEST POSSIBLE CHECK.
            # When the timing was a running sum of rounded seconds the error
            # was a RANDOM WALK, and a random walk is most likely to be back
            # near zero at the end — so the last cue is exactly where a drift
            # bug hides. The maths is exact now, but the check is written to
            # catch the failure it would have missed: monotonicity across
            # EVERY cue, plus the end-of-file agreement.
            if st["cues"]:
                cs = st["cues"]
                bad = [c["i"] for i, c in enumerate(cs)
                       if c["end"] < c["start"]
                       or (i + 1 < len(cs) and cs[i + 1]["start"] < c["end"] - 1e-6)]
                drift = abs(cs[-1]["end"] - (st["seconds"] or 0))
                st["cue_drift_s"] = round(drift, 3)
                st["cue_monotonic"] = not bad
                if bad:
                    _log(jid, f"⚠⚠ cues {bad[:6]} run backwards or overlap — "
                              f"do not build scenes from them")
                if drift > 0.6:
                    _log(jid, f"⚠⚠ the cues disagree with the file by "
                              f"{drift:.2f}s — do not build scenes from them")
            _log(jid, f"done in {st['elapsed_s']}s → {out.name} "
                      f"({st['seconds']}s of speech, {len(st['cues'])} cues)")
        except Exception as e:                                   # noqa: BLE001
            st["status"] = "error"
            st["error"] = f"{type(e).__name__}: {e}"
            st["elapsed_s"] = round(time.time() - st["_t0"], 1)
        finally:
            with _LOCK:
                _jobs_save()

    threading.Thread(target=_run, daemon=True, name=f"tts-{jid}").start()
    return {"started": True, "id": jid, "worker": ready, "chunks": len(chunks)}


# ══════════════════════════════════════════════════════════════════════════
# 📝 SRT — written from the cues the render already measured
# ══════════════════════════════════════════════════════════════════════════
def _srt_time(t: float) -> str:
    """`HH:MM:SS,mmm` — SRT uses a COMMA before the milliseconds, not a dot.
    A dot parses as WebVTT and silently yields zero-length cues in some
    readers, including ours."""
    t = max(0.0, float(t))
    h, rem = divmod(int(t), 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d},{int(round((t - int(t)) * 1000)):03d}"


def cues_to_srt(cues: List[dict]) -> str:
    """A standard SRT from a cue list.

    ⚠ Cues are CLAMPED so no cue ends after the next one starts and none is
    zero-length: a reader that hits an inverted or empty cue drops it, and a
    dropped cue is a scene with no words rather than a visible error.
    ⚠ Trailing newline: some parsers ignore the final cue without it."""
    out, n = [], 0
    for i, c in enumerate(cues):
        text = " ".join(str(c.get("text") or "").split())
        if not text:
            continue
        start = float(c.get("start") or 0.0)
        end = float(c.get("end") or 0.0)
        nxt = float(cues[i + 1].get("start")) if i + 1 < len(cues) else None
        if nxt is not None and end > nxt:
            end = nxt
        if end <= start:
            end = start + 0.2
        n += 1
        out.append(f"{n}\n{_srt_time(start)} --> {_srt_time(end)}\n{text}\n")
    return "\n".join(out) + "\n"


@router.get("/jobs/{jid}/srt")
async def job_srt(jid: str, download: bool = True):
    """📝 The SRT for a rendered narration — timings MEASURED at render, never
    transcribed back out of our own audio."""
    st = _JOBS.get(jid)
    if not st:
        raise HTTPException(404, f"job {jid!r} not found")
    cues = st.get("cues") or []
    if not cues:
        raise HTTPException(409, "this render has no cue list — it predates "
                                 "v1.277.48, or it failed before the join")
    body = cues_to_srt(cues)
    from fastapi.responses import Response
    # ⚠⚠ AN HTTP HEADER IS LATIN-1. A voice called "🎨 Factory test" put an
    # emoji straight into Content-Disposition and the whole route 500'd —
    # the SRT itself was perfect, the filename killed it. Strip to ASCII and
    # keep the real name in the body's own cue text, where it belongs.
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_",
                  (st.get("voice") or "narration")).strip("_") or "narration"
    name = f"{stem[:60]}_{jid}.srt"
    return Response(
        content=body, media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{name}"'}
        if download else {})


def _upload_input(base: str, fp: Path) -> str:
    bound = uuid.uuid4().hex
    b = ("--" + bound).encode()
    payload = (b + b"\r\n"
               + f'Content-Disposition: form-data; name="image"; '
                 f'filename="{fp.name}"\r\n'.encode()
               + b"Content-Type: application/octet-stream\r\n\r\n"
               + fp.read_bytes() + b"\r\n" + b + b"--\r\n")
    req = urllib.request.Request(f"{base}/upload/image", data=payload,
                                 method="POST")
    req.add_header("Content-Type", f"multipart/form-data; boundary={bound}")
    with urllib.request.urlopen(req, timeout=300) as r:
        return json.loads(r.read().decode()).get("name") or fp.name


# ══════════════════════════════════════════════════════════════════════════
# 🗣 CHATTERBOX — the commercially-usable clone engine (v1.277.51)
# ══════════════════════════════════════════════════════════════════════════
#
# His call, 2026-08-19: *"lets use chatterbox… F5 is neat but I hate the
# non-commercial stance. its trash in 2026."* — and it matters more than taste,
# because he intends to *"give it to the public to express themselves and tell
# their stories."* **F5-TTS is CC-BY-NC 4.0**; making it the default would put a
# non-commercial restriction on everyone who ever uses this app. **Chatterbox is
# MIT** (Resemble AI), and was preferred over ElevenLabs 65.3% to 24.5% in blind
# listening tests.
#
# ⚠ F5 IS NOT REMOVED — existing voices and every take already rendered
# reference it. It stays selectable and is LABELLED non-commercial.
#
# Node names and every default below were READ OFF THE BOX
# (`_diag/tts_suite_object_info.json`), never guessed. The pack is
# `diodiogod/TTS-Audio-Suite`, which also carries IndexTTS-2, Higgs, VibeVoice
# and RVC — all now installed and available later without another fleet install.
# ⭐ VibeVoice is built for LONG-FORM EXPRESSIVE NARRATION, which is his stated
# main use case; worth trying once Chatterbox is proven.
CB_ENGINE = "ChatterBoxEngineNode"      # ⚙ settings → TTS_ENGINE
CB_SPEAK = "UnifiedTTSTextNode"         # 🎤 does the speaking → AUDIO

#: ⚠⚠ THE TRAP IN THIS NODE, and my own wrong guess about it.
#: `crash_protection_template` defaults to **"hmm ,, {seg} hmm ,,"** — it wraps
#: SHORT segments to stop Chatterbox crashing, and the padding is TEXT, so it
#: ends up in the audio. Our chapter lane chunks per SENTENCE, so short
#: segments are the COMMON case here, not the rare one.
#:
#: ⭐⭐ MEASURED, and it corrected me. `scripts/chatterbox_probe.py` renders the
#: same two words under each template on one seed:
#:
#:     "{seg}"                 1.28s   ← nothing added
#:     "hmm ,, {seg} hmm ,,"   1.72s   (+0.44s, +34%)  the node default
#:     ",, {seg} ,,"           2.28s   (+1.00s, +78%)  my first guess
#:
#: I assumed commas were the safe pad because they are not words. They are
#: PAUSES, and they added a full second of dead air to every short line —
#: WORSE than the default I was trying to improve on. **Padding is text either
#: way, and either way it lands INSIDE the cue**, so a scene built from that
#: cue opens or closes on something nobody wrote.
#: ⚠ The template exists to stop crashes on tiny inputs; measured, "Yes."
#: renders fine at 0.88s with no padding at all. If that ever changes, put the
#: node's own default back — it is the tested one — and re-measure.
CB_CRASH_TEMPLATE = "{seg}"


def _chatterbox_graph(host: str, ref_name: str, text: str, seed: int,
                      prefix: str, *, language: str = "English",
                      exaggeration: float = 0.5, temperature: float = 0.8,
                      cfg_weight: float = 0.5,
                      crash_template: str = CB_CRASH_TEMPLATE) -> dict:
    """LoadAudio → ChatterBoxEngine → UnifiedTTSText → SaveAudio.

    ⭐ NO REFERENCE TRANSCRIPT. Chatterbox is zero-shot: it takes the voice from
    the audio alone. That removes the single most damaging failure mode of the
    F5 lane — a transcript that does not match the trimmed clip, which corrupts
    F5's duration maths and makes every render mumble.

    ⚠ `enable_chunking` is left ON with `silence_between_chunks_ms = 0` and
    `concatenate`. We already chunk per sentence, so the node rarely needs to;
    but a long paragraph (auto-tag off) must not fail, and zero added silence
    means the returned file is contiguous — which is what makes our cue for it
    exact. **Do not let this node insert silence: our timeline owns the gaps.**
    """
    eng = _inputs_with_defaults(host, CB_ENGINE, {
        "language": language,
        "device": "auto",
        "exaggeration": float(exaggeration),
        "temperature": float(temperature),
        "cfg_weight": float(cfg_weight),
        "crash_protection_template": crash_template,
    })
    speak = _inputs_with_defaults(host, CB_SPEAK, {
        "TTS_engine": ["2", 0],
        "text": text,
        "narrator_voice": "none",       # we supply the audio directly instead
        "seed": int(seed) & 0xFFFFFFFF,
        "opt_narrator": ["1", 0],       # the uploaded reference clip
        "enable_chunking": True,
        "max_chars_per_chunk": 400,
        "chunk_combination_method": "concatenate",
        "silence_between_chunks_ms": 0,
        "enable_audio_cache": False,    # ⚠ a cache would return a PRIOR take
        "batch_size": 0,                # sequential — deterministic ordering
    })
    return {
        "1": {"class_type": "LoadAudio", "inputs": {"audio": ref_name}},
        "2": {"class_type": CB_ENGINE, "inputs": eng},
        "3": {"class_type": CB_SPEAK, "inputs": speak},
        "4": {"class_type": "SaveAudio",
              "inputs": {"audio": ["3", 0], "filename_prefix": prefix}},
    }


def _f5_graph(host: str, ref_name: str, ref_text: str, text: str, seed: int,
              speed: float, prefix: str) -> dict:
    """ComfyUI-F5-TTS graph — built against whichever of the node's known
    shapes this worker reports (the node has evolved; object_info decides)."""
    if _node_info(host, "F5TTSAudioInputs") is not None:
        inputs = _inputs_with_defaults(host, "F5TTSAudioInputs", {
            "sample_audio": ["1", 0], "sample_text": ref_text,
            "speech": text, "seed": seed, "speed": speed})
        return {
            "1": {"class_type": "LoadAudio", "inputs": {"audio": ref_name}},
            "2": {"class_type": "F5TTSAudioInputs", "inputs": inputs},
            "3": {"class_type": "SaveAudio",
                  "inputs": {"audio": ["2", 0], "filename_prefix": prefix}},
        }
    inputs = _inputs_with_defaults(host, "F5TTSAudio", {
        "sample": ref_name, "sample_text": ref_text, "speech": text,
        "seed": seed, "speed": speed})
    return {
        "2": {"class_type": "F5TTSAudio", "inputs": inputs},
        "3": {"class_type": "SaveAudio",
              "inputs": {"audio": ["2", 0], "filename_prefix": prefix}},
    }


_FILTERS: Optional[set] = None


def _ffmpeg_filters() -> set:
    """What this ffmpeg can actually do — asked once, not assumed.

    ⚠ `rubberband` is a BUILD option: the gyan.dev full build on the app host
    has it, a stock 'essentials' build does not. Falling back silently to
    `atempo` is fine; pretending rubberband is there is not."""
    global _FILTERS
    if _FILTERS is None:
        import subprocess
        try:
            r = subprocess.run(["ffmpeg", "-hide_banner", "-filters"],
                               capture_output=True, text=True, timeout=60)
            txt = (r.stdout or "") + (r.stderr or "")
            _FILTERS = {ln.split()[1] for ln in txt.splitlines()
                        if len(ln.split()) > 2 and ln.startswith(" ")}
        except Exception:                                        # noqa: BLE001
            _FILTERS = set()
    return _FILTERS


def _stretch(fp: Path, pace: float, jid: str) -> tuple:
    """Slow (or speed) finished audio by `pace`, keeping the PITCH.

    ⭐ Why this beats the model's own speed knob: F5 predicts a duration and
    then fills it, so a slow setting stretches the model's prosody while it
    generates — his verdict on those takes was "really bad". Stretching
    afterwards leaves the generation at its best and moves only the clock.

    rubberband is the good algorithm (formant-preserving, built for speech);
    `atempo` is the fallback everywhere and is decent inside ±15%. Returns
    (path, how) so the job can SAY which one ran."""
    import subprocess
    if abs(pace - 1.0) < 0.01:
        return fp, "", 1.0
    tempo = 1.0 / max(0.25, min(4.0, pace))       # pace>1 ⇒ tempo<1 ⇒ slower
    if "rubberband" in _ffmpeg_filters():
        # pitchq=quality + a formant-preserving stretch is what keeps a slowed
        # voice from sounding like a drunk tape
        applied = float(f"{tempo:.4f}")
        filt = f"rubberband=tempo={applied:.4f}:pitchq=quality:formant=preserved"
        how = "rubberband"
    else:
        applied = float(f"{max(0.5, min(2.0, tempo)):.4f}")
        filt = f"atempo={applied:.4f}"
        how = "atempo (rubberband not in this ffmpeg)"
    out = fp.with_name(f"{fp.stem}_paced{fp.suffix}")
    r = subprocess.run(["ffmpeg", "-y", "-i", str(fp), "-af", filt,
                        "-ar", "24000", str(out)],
                       capture_output=True, text=True, timeout=900)
    if r.returncode != 0 or not out.exists():
        _log(jid, f"⚠ pace stretch failed, keeping the raw render: "
                  f"{(r.stderr or '')[-200:]}")
        # ⭐ 1.0, not `applied` — the audio was NOT stretched, so the cues must
        # not be scaled either. Reporting the requested factor after a failed
        # stretch is how a cue list ends up describing a file that never existed.
        return fp, "", 1.0
    fp.unlink(missing_ok=True)
    out.rename(fp)
    # ⭐ Return the tempo ACTUALLY APPLIED, not the one requested. The filter
    # string is formatted to 4 dp, so ffmpeg stretches by `1/round(1/pace, 4)`
    # — scaling the cues by raw `pace` leaves a proportional error (measured
    # ~0.005%, ~15 ms over 6.5 min). Small, but it is the kind that grows with
    # length, and it costs one return value to remove. ⚠ `atempo` additionally
    # CLAMPS to [0.5, 2.0] while pace allows [0.25, 4.0]: without this the cues
    # would be scaled by a factor the audio was never stretched by.
    return fp, how, applied


def _sentences(text: str) -> List[str]:
    """Split a paragraph at sentence ends, keeping the punctuation.

    ⚠ Each piece becomes its OWN render, so prosody can step between them —
    that is the price of a real pause, and it is why this is opt-in."""
    parts = re.split(r"(?<=[.!?…])\s+", text.strip())
    return [p.strip() for p in parts if p.strip()]


# ── 🫁 PAUSE TAGS — silence you place yourself (v1.277.41) ───────────────────
# His finding, and it decided the design: **1.0 sounds the cleanest**, 1.15 reads
# more naturally. So do not slow the VOICE to get the pacing — put the time
# BETWEEN the words, where it costs nothing. Silence has no artifacts.
#
#     The whistle carried. [pause] Nobody spoke. [pause 900] Then the coffee.
#
#   [pause]        400 ms   ·  [beat]   600 ms
#   [breath]       200 ms   ·  [break]  400 ms   (same thing, SSML's name)
#   [pause 750]    750 ms — a bare number ≥20 is MILLISECONDS
#   [pause 1.5s]   1.5 s   — a unit, or a bare number <20, is SECONDS
#
# ⚠ The tag is REMOVED from what the model sees. Leaving it in makes F5 read the
# word "pause" out loud, which is the obvious failure and worth a test.
# ⚠ A tag splits the render there, so prosody can step across it — that is the
# price of real silence, and it is why this is explicit rather than automatic.
#: `!` PINS a tag: re-tagging will not touch it. Everything else at an
#: auto position is re-valued when the setting changes — see `auto_tag`.
_PAUSE_RE = re.compile(
    r"\[\s*(pause|break|beat|breath)(!?)\s*"
    r"(?:[ :=]\s*(\d+(?:\.\d+)?)\s*(ms|s)?)?\s*\]", re.I)
_PAUSE_DEFAULT = {"pause": 400, "break": 400, "beat": 600, "breath": 200}


def _tag_ms(word: str, num: Optional[str], unit: Optional[str]) -> int:
    if not num:
        return _PAUSE_DEFAULT.get(word.lower(), 400)
    v = float(num)
    u = (unit or "").lower()
    if u == "s" or (not u and v < 20):
        v *= 1000.0
    return int(max(0, min(10_000, v)))


def strip_pause_tags(text: str) -> str:
    """What the MODEL should see — the tags carry timing, never speech."""
    return re.sub(r"\s{2,}", " ", _PAUSE_RE.sub(" ", text or "")).strip()


def plan_chunks(text: str, pause_ms: int, sentence_pause_ms: int = 0) -> tuple:
    """Turn narration text into (chunks, gaps) — gaps[i] follows chunks[i].

    Three sources of silence, all landing in one list so the concat does a
    single pass: blank lines (paragraphs), [pause] tags, and optionally every
    sentence end. Pure and free of I/O so `scripts/pause_tag_smoke.py` can hold
    it to account without a GPU."""
    chunks: List[str] = []
    gaps: List[int] = []

    def _emit(piece: str, gap: int) -> None:
        piece = strip_pause_tags(piece)
        if not piece:
            # a tag on its own line still owes its silence — hand it to the
            # PREVIOUS chunk rather than dropping it
            if gap and gaps:
                gaps[-1] = gaps[-1] + gap
            return
        chunks.append(piece)
        gaps.append(gap)

    paras = [c.strip() for c in re.split(r"\n\s*\n", text or "") if c.strip()]
    for pi, para in enumerate(paras):
        # split on the tags first, keeping each tag's own duration
        segs: List[tuple] = []
        pos = 0
        for m in _PAUSE_RE.finditer(para):
            segs.append((para[pos:m.start()], _tag_ms(m.group(1), m.group(3),
                                                      m.group(4))))
            pos = m.end()
        segs.append((para[pos:], 0))
        for si, (seg, tag_gap) in enumerate(segs):
            last_seg = si == len(segs) - 1
            pieces = (_sentences(seg) if sentence_pause_ms > 0 else [seg])
            pieces = [x for x in pieces if x.strip()] or ([seg] if seg.strip() else [])
            if not pieces:
                # ⚠ a tag with no words around it (its own line, or two tags in
                # a row) STILL owes its silence — hand it to the chunk before.
                # The first version dropped it, and a pause that silently does
                # nothing is worse than one that errors.
                if tag_gap and gaps:
                    gaps[-1] += tag_gap
                continue
            for qi, piece in enumerate(pieces):
                last_piece = qi == len(pieces) - 1
                if not last_piece:
                    _emit(piece, int(sentence_pause_ms))
                elif not last_seg:
                    _emit(piece, tag_gap)
                else:
                    _emit(piece, pause_ms if pi < len(paras) - 1 else 0)
    if gaps:
        gaps[-1] = 0                      # never a trailing silence
    return chunks, gaps


# ── 🪄 AUTO-TAGGING — *"i dont think we ever want to do this by hand"* ────────
# So the tags get WRITTEN FOR HIM, into the text, where he can see and adjust
# them. Not a hidden setting: the point of a shortcode is that it is visible.
#
# The gaps are not uniform, because punctuation is not uniform. A full stop, an
# ellipsis and an em-dash ask for different amounts of air, and a comma asks for
# almost none — F5 already breathes at commas, and adding silence there is what
# makes a read sound chopped rather than measured.
_SENT_END = re.compile(r"(?<=[.!?])(?=\s)")
#: the same boundary, but as something with an END — `_sub_marked`
#: needs `m.end()`, and a lookahead-only pattern has nowhere to stand.
_SENT_END_M = re.compile(r"[.!?](?=\s)")
_ELLIPSIS = re.compile(r"(\.{3}|…)(?=\s)")
_DASH = re.compile(r"(—|--)(?=\s)")


def auto_tag(text: str, sentence_ms: int = 350, ellipsis_ms: int = 700,
             dash_ms: int = 450, retag: bool = True) -> str:
    """Insert [pause …] after sentence ends, ellipses and em-dashes.

    ⚠⚠ `retag` EXISTS BECAUSE IDEMPOTENCE WAS TOO GOOD (2026-08-18). The first
    version skipped any position that already carried a tag, so **changing the
    pause setting and pressing the button did nothing** — every value had to be
    retyped by hand, which is the exact chore this feature exists to remove.
    An auto position is RE-VALUED now; `[pause! 1200]` is PINNED and left alone.

    ⚠⚠ EXISTING TAGS ARE MASKED OUT BEFORE SCANNING, and that is not tidiness.
    `[pause! 1200]` contains a `!` followed by a space, so the sentence-end rule
    matched INSIDE the tag and inserted a second one in the middle of it —
    output like `[pause! 1200] [pause 900] 1200]`. Any scanner that looks for
    punctuation has to be blind to the markup first."""
    if not (text or "").strip():
        return text or ""

    def _one_para(para: str) -> str:
        # 1. lift every existing tag out, leaving an opaque sentinel
        slots: list = []          # [{"text": "[pause 900]", "pinned": bool}]

        def _mask(m):
            slots.append({"text": m.group(0), "pinned": m.group(2) == "!"})
            return f"\ue000{len(slots) - 1}\ue001"

        masked = _PAUSE_RE.sub(_mask, para)

        # 2. place or re-value at each auto position.
        # ⚠⚠ A NEW tag is inserted as a SENTINEL, never as literal text. The
        # first attempt wrote "[pause 700]" straight into the string, and the
        # NEXT pass's punctuation scan then read the `.` of the ellipsis it had
        # just handled and stacked a second tag on top:
        #     "He waited... [pause 350] [pause 1200] [pause 700] Then he left."
        # Each pass has to be blind to the markup — including the markup the
        # previous pass just produced.
        def _apply(pattern, ms, work: str) -> str:
            out, pos = [], 0
            for m in pattern.finditer(work):
                out.append(work[pos:m.end()])
                rest = work[m.end():]
                sent = re.match(r"\s*\ue000(\d+)\ue001", rest)
                if sent:
                    idx = int(sent.group(1))
                    # ⚠ FIRST rule wins within one call. The passes run most-
                    # specific first, and `...` ends in a `.`, so the sentence
                    # rule would otherwise re-value the tag the ELLIPSIS rule
                    # had just set — an ellipsis silently became 350 ms again.
                    if not slots[idx]["pinned"] and not slots[idx].get("set"):
                        slots[idx]["text"] = f"[pause {ms}]"
                        slots[idx]["set"] = True
                    out.append(rest[:sent.end()])
                    pos = m.end() + sent.end()
                    continue
                slots.append({"text": f"[pause {ms}]", "pinned": False,
                              "set": True})
                out.append(f" \ue000{len(slots) - 1}\ue001")
                pos = m.end()
            out.append(work[pos:])
            return "".join(out)

        # most specific first — an ellipsis must not also read as a full stop
        work = _apply(_ELLIPSIS, ellipsis_ms, masked)
        work = _apply(_DASH, dash_ms, work)
        work = _apply(_SENT_END_M, sentence_ms, work)

        # 3. put them all back
        return re.sub(r"\ue000(\d+)\ue001",
                      lambda m: slots[int(m.group(1))]["text"], work)

    out = [p if (not p.strip() or p.startswith("\n")) else _one_para(p)
           for p in re.split(r"(\n\s*\n)", text)]
    joined = re.sub(r"[ \t]{2,}", " ", "".join(out))
    # never leave a tag dangling at the very end — silence after the last word
    # is just a longer file
    return re.sub(r"\s*" + _PAUSE_RE.pattern + r"\s*$", "", joined,
                  flags=re.I).rstrip()


class AutoTagIn(BaseModel):
    text: str
    sentence_ms: int = 350
    ellipsis_ms: int = 700
    dash_ms: int = 450
    retag: bool = True               # re-value existing auto tags





@router.post("/tts/auto-tag")
async def tts_auto_tag(body: AutoTagIn):
    """🪄 Write the pause tags into the narration text.

    Returns the tagged text for the editor to show — deliberately NOT applied
    invisibly at render time, because a pause you cannot see is a pause you
    cannot fix."""
    tagged = auto_tag(body.text, body.sentence_ms, body.ellipsis_ms,
                      body.dash_ms, body.retag)
    added = len(_PAUSE_RE.findall(tagged)) - len(_PAUSE_RE.findall(body.text))
    chunks, gaps = plan_chunks(tagged, 900, 0)
    return {"text": tagged, "added": max(0, added),
            "chunks": len(chunks),
            "silence_s": round(sum(gaps) / 1000.0, 2)}


#: everything gets converted to THIS before joining. The value is not sacred;
#: having ONE value is.
_CANON = ("-ac", "1", "-ar", "24000", "-c:a", "pcm_s16le")


def _canonical(src: Path, dest: Path) -> Path:
    """Decode anything to one uniform PCM WAV.

    ⚠⚠ THIS IS THE BUG THAT ATE THE PAUSES (2026-08-18). ComfyUI's SaveAudio
    hands back **FLAC** (`tts_..._00001.flac`), we stored it under a `.wav`
    name, and then fed those files to ffmpeg's **concat DEMUXER** alongside a
    real PCM silence. The demuxer requires every input to share a codec: it
    took its parameters from the first (FLAC) input and dropped the PCM silence
    — **while exiting 0**. Every pause vanished, no error anywhere, and the
    planner's unit tests stayed green because they only ever tested the PLAN.
    A file extension is a claim, not a format.
    """
    import subprocess
    r = subprocess.run(["ffmpeg", "-y", "-i", str(src), *_CANON, str(dest)],
                       capture_output=True, text=True, timeout=600)
    if r.returncode != 0 or not dest.exists():
        raise RuntimeError(f"could not normalise {src.name}: "
                           f"{(r.stderr or '')[-300:]}")
    return dest


def _pcm_frames(paths: List[Path]) -> tuple:
    """`(sample_rate, [frame_count, …])` read from the WAV headers themselves.

    ⭐ THE POINT: a frame count is an INTEGER. Cue times built from integer
    sample positions divided ONCE by the rate are exact — there is nothing to
    accumulate. Times built by adding up `round(seconds, 2)` are a random walk.
    This is the same discipline `import_aaf.parse_aaf_clips` uses (integer edit
    units, one division at the end) and it is why the AAF timeline never drifts.

    ⚠ Every path here has already been through `_canonical`, so they are all
    `pcm_s16le / 1ch / 24000` and `wave` can read them without decoding. If a
    rate ever disagrees we fall back rather than silently mixing clocks."""
    import wave
    rate, out = 0, []
    for p in paths:
        try:
            with contextlib.closing(wave.open(str(p), "rb")) as wf:
                r, n = wf.getframerate(), wf.getnframes()
        except Exception as e:                                   # noqa: BLE001
            # not a readable WAV (should be impossible post-_canonical) — fall
            # back to the probe, and SAY SO rather than pretending it is exact
            logger.warning("cue timing: %s is not a readable WAV (%s) — "
                           "falling back to ffprobe for this part", p.name, e)
            r, n = 24000, int(round(_probe_seconds(p) * 24000))
        rate = rate or r
        if r != rate:
            logger.warning("cue timing: %s is %d Hz but the join is %d Hz — "
                           "rescaling its frame count", p.name, r, rate)
            n = int(round(n * rate / float(r or rate)))
        out.append(int(n))
    return (rate or 24000), out


def _concat_with_pauses(parts: List[Path], pause_ms, jid: str,
                        spans: Optional[List[List[float]]] = None) -> Path:
    """Join the rendered pieces, inserting silence between them.

    `pause_ms` is either ONE gap for every join or a LIST of per-join gaps —
    which is what pause tags need: 350 ms after a full stop, 900 ms between
    paragraphs, in a single pass.

    Every input is normalised to `_CANON` FIRST (see `_canonical`), which is
    what makes the silence survive. ⚠ Distinct gaps also need distinct silence
    FILES; reusing one name for two lengths gives every join the last one
    written.

    ⭐⭐ `spans` is an OUT-PARAMETER: pass a list and it is filled with
    `[start_s, end_s]` for every part, in order, on the joined timeline.

    **These offsets were always here and were always thrown away.** The line
    below that verifies the join already probes every part's duration and sums
    it; accumulating instead of summing is the whole difference between "we
    know when each sentence starts" and "we would have to run Whisper over our
    own output to find out". Every part is decoded to identical PCM by
    `_canonical` first, so the concat demuxer is sample-exact and these numbers
    are the truth, not an estimate.

    ⚠ The caller must scale them by `pace` when `pace_mode == "stretch"`, because
    `_stretch` runs AFTER this and moves the whole timeline.
    """
    import subprocess
    out = _TRACK_DIR / f"{jid}.wav"
    gaps = ([int(pause_ms)] * max(0, len(parts) - 1)
            if isinstance(pause_ms, (int, float))
            else [int(g) for g in pause_ms][:len(parts) - 1])
    while len(gaps) < len(parts) - 1:
        gaps.append(gaps[-1] if gaps else 0)

    if len(parts) == 1:
        # ⚠ still normalise: a single chunk used to be RENAMED, so a one-liner
        # render shipped FLAC bytes inside a .wav — playable by luck, and a
        # liability for anything downstream that trusts the extension.
        _canonical(parts[0], out)
        parts[0].unlink(missing_ok=True)
        # ⚠ THE FAST PATH NEEDS THE SPAN TOO. Forgetting it here is how a
        # one-paragraph narration ends up with an empty cue list and no SRT,
        # which nobody notices until the short one is the one that matters.
        if spans is not None:
            _r, _f = _pcm_frames([out])          # exact, like the join below
            spans.append([0.0, _f[0] / float(_r)])
        return out

    norm: List[Path] = []
    for i, fp in enumerate(parts):
        norm.append(_canonical(fp, _TRACK_DIR / f"{jid}_n{i}.wav"))
    sil_files = {}
    for ms in {max(0, g) for g in gaps}:
        if ms <= 0:
            continue
        fp = _TRACK_DIR / f"{jid}_sil{ms}.wav"
        subprocess.run(["ffmpeg", "-y", "-f", "lavfi",
                        "-i", "anullsrc=r=24000:cl=mono",
                        "-t", f"{ms / 1000.0:.3f}", *_CANON, str(fp)],
                       capture_output=True, check=True, timeout=300)
        sil_files[ms] = fp

    lines = []
    for i, fp in enumerate(norm):
        if i:
            g = max(0, gaps[i - 1])
            if g and g in sil_files:
                lines.append(f"file '{sil_files[g].name}'")
        lines.append(f"file '{fp.name}'")
    lst = _TRACK_DIR / f"{jid}_concat.txt"
    lst.write_text("\n".join(lines), "utf-8")
    r = subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0",
                        "-i", str(lst), *_CANON, str(out)],
                       capture_output=True, text=True, timeout=1800,
                       cwd=str(_TRACK_DIR))
    if r.returncode != 0 or not out.exists():
        raise RuntimeError(f"concat failed: {(r.stderr or '')[-400:]}")
    # ⭐ VERIFY, do not assume: the sum of the parts plus the gaps is a number
    # we can check, and it is exactly what silently went missing before.
    # ⭐⭐ …and the running total on the way to that sum IS the cue list.
    #
    # ⚠⚠⚠ COUNT SAMPLES, NEVER ACCUMULATE ROUNDED SECONDS. `_probe_seconds`
    # rounds to 2 dp, and accumulating 80 of those is a RANDOM WALK whose error
    # grows with position down the file — measured at ~30 ms typical, 70 ms p99,
    # 400 ms worst case. **That is precisely the bug class killed in v1.8.20**
    # ("39 of 48 scenes ended mid-word … the offset growing to ~10s by the end"),
    # and re-introducing it in a new lane would have been the same month over
    # again. Every part is already `_CANON` PCM at a known rate, so the sample
    # COUNT is an integer and the timeline is exact by construction — the same
    # property the AAF importer gets from accumulating integer edit units
    # (`import_aaf.py:137-149`) rather than seconds.
    rate, frames = _pcm_frames(norm)
    durs = [f / float(rate) for f in frames]
    if spans is not None:
        at = 0                                   # INTEGER samples, not seconds
        for i, f in enumerate(frames):
            spans.append([at / float(rate), (at + f) / float(rate)])
            at += f + (int(round(max(0, gaps[i]) * rate / 1000.0))
                       if i < len(gaps) else 0)
    want = sum(durs) + sum(gaps) / 1000.0
    got = _probe_seconds(out)
    if want and got and got < want - 0.35:
        _log(jid, f"⚠⚠ the join LOST time: {got:.2f}s out of an expected "
                  f"{want:.2f}s — the pauses are not in the file")
    lst.unlink(missing_ok=True)
    for fp in list(sil_files.values()) + norm:
        fp.unlink(missing_ok=True)
    return out


# ── shared: overview / jobs / media / import ─────────────────────────────────
@router.get("/overview")
async def overview():
    hosts = _hosts()
    return {"workers": [{"host": h["host"], "name": h.get("name"),
                         "engines": _engine_status(h["host"])}
                        for h in hosts],
            "voices": len(_voices())}


class CompareIn(MusicIn):
    engines: List[str] = []          # [] = every engine ready on the fleet


@router.post("/music/compare")
async def music_compare(body: CompareIn):
    """🆚 Render the SAME prompt on several engines at once, one per box.

    The models genuinely disagree — turbo is clean and fast, XL sft has more
    detail, MM3 is spectrally the brightest and ~3x real time — and which one
    wins depends on the piece, so the choice belongs to the ear, per track.

    ⚠ Boxes are assigned ROUND-ROBIN UP FRONT: `pick_music_host` returns the
    FIRST ready worker every time, so calling it per engine in a loop would pin
    every variant to one box and serialise the whole comparison (v1.276.45).
    ⚠ Every variant carries the SAME SEED — comparing two engines on two seeds
    compares seeds. And every one is loudness-normalised, because an unmatched
    A/B is partly a loudness test (measured: -12.9 vs -14.5 LUFS here).
    """
    want = [e for e in (body.engines or MUSIC_ENGINES) if e in MUSIC_ENGINES]
    ready = [e for e in want
             if any(_engine_status(h["host"]).get(e, {}).get("ready")
                    for h in _hosts())]
    if not ready:
        raise HTTPException(409, "none of those engines is ready on any box: "
                                 + ", ".join(want))
    boxes = [h["host"] for h in _hosts()]
    seed = body.seed if body.seed is not None else int(time.time()) % 2**31
    started, failed = [], []
    for n, eng in enumerate(ready):
        host = ""
        for cand in boxes[n % len(boxes):] + boxes[:n % len(boxes)]:
            if _engine_status(cand).get(eng, {}).get("ready"):
                host = cand
                break
        try:
            r = enqueue_music(engine=eng, tags=body.tags, lyrics=body.lyrics,
                              seconds=body.seconds, bpm=body.bpm,
                              keyscale=body.keyscale,
                              timesignature=body.timesignature,
                              language=body.language,
                              seed=seed, host=host,
                              label=f"🆚 {eng} · {(body.label or body.tags)[:40]}",
                              steps=body.steps, cfg=body.cfg,
                              normalize=body.normalize,
                              meta={"compare": True, "engine": eng, "seed": seed})
            started.append({"engine": eng, "job": r["id"], "worker": r["worker"]})
        except Exception as e:                                   # noqa: BLE001
            failed.append({"engine": eng, "error": f"{type(e).__name__}: {e}"})
    return {"started": started, "failed": failed, "seed": seed,
            "skipped": [e for e in want if e not in ready]}


@router.get("/staging")
async def staging():
    """⬇ What is still DOWNLOADING onto the boxes (v1.277.17).

    "I can't tell if they're done or not" — and the engine chips only flip when
    a file has fully landed, so a 19 GB stage looks identical to a stalled one
    for an hour. This reads each helper's own download queue.

    ⚠ `/downloads` needs the box's TOKEN (`/health` does not — it answers 200
    without the queue), and the calls are blocking urllib, so they run in a
    THREAD: blocking I/O inside an async route is the v1.276.41 class."""
    import asyncio

    def _one(h: dict) -> List[dict]:
        try:
            d = _jget(f"http://{h['host']}:{h.get('port', 8765)}/downloads"
                      f"?token={urllib.parse.quote(h['token'])}", timeout=20)
        except Exception as e:                                   # noqa: BLE001
            return [{"host": h["host"], "name": h.get("name") or h["host"],
                     "file": "", "status": "unreachable",
                     "error": f"{type(e).__name__}: {e}"}]
        rows = d if isinstance(d, list) else (d.get("downloads") or [])
        out = []
        for r in rows:
            got, tot = int(r.get("bytes") or 0), int(r.get("total") or 0)
            out.append({"host": h["host"], "name": h.get("name") or h["host"],
                        "file": Path(str(r.get("dest") or r.get("url") or "")).name,
                        "status": r.get("status") or "?",
                        "bytes": got, "total": tot,
                        "pct": round(100.0 * got / tot, 1) if tot else 0.0,
                        "error": r.get("error")})
        return out

    lists = await asyncio.gather(*[asyncio.to_thread(_one, h) for h in _hosts()])
    rows = [r for lst in lists for r in lst]
    return {"downloads": rows,
            "active": sum(1 for r in rows if r.get("status") == "running")}


@router.get("/jobs")
async def jobs():
    rows = sorted(_JOBS.values(), key=lambda j: j.get("at") or "",
                  reverse=True)
    out = []
    for j in rows[:80]:
        r = {k: v for k, v in j.items() if not k.startswith("_") and k != "log"}
        if j.get("status") == "running" and j.get("_t0"):
            r["elapsed_s"] = round(time.time() - j["_t0"], 1)
        r["log_lines"] = len(j.get("log") or [])
        out.append(r)
    return {"jobs": out}


@router.get("/jobs/{jid}")
async def job(jid: str):
    j = _JOBS.get(jid)
    if not j:
        raise HTTPException(404, "no such job")
    r = {k: v for k, v in j.items() if not k.startswith("_")}
    if j.get("status") == "running" and j.get("_t0"):
        r["elapsed_s"] = round(time.time() - j["_t0"], 1)
    return r


@router.get("/media/{jid}")
async def media(jid: str, download: bool = False):
    j = _JOBS.get(jid)
    if not j or not j.get("file"):
        raise HTTPException(404, "no audio for that job")
    fp = _TRACK_DIR / j["file"]
    if not fp.exists():
        raise HTTPException(404, "audio file missing on disk")
    mt = "audio/mpeg" if fp.suffix == ".mp3" else "audio/wav"
    if download:
        return FileResponse(str(fp), media_type=mt, filename=fp.name)
    return FileResponse(str(fp), media_type=mt)


@router.delete("/jobs/{jid}")
async def delete_job(jid: str):
    j = _JOBS.pop(jid, None)
    if j and j.get("file"):
        (_TRACK_DIR / j["file"]).unlink(missing_ok=True)
    with _LOCK:
        _jobs_save()
    return {"ok": True}


class ImportIn(BaseModel):
    project_id: str
    as_type: str = "music"           # music (backing track / master audio)


@router.post("/jobs/{jid}/send-to-project")
async def send_to_project(jid: str, body: ImportIn, request: Request):
    return await import_job_to_project(jid, body.project_id)


class ToStoryIn(BaseModel):
    world_id: str
    story_id: str
    slot: str = "audio"              # the story's narration AUDIO slot


@router.post("/jobs/{jid}/send-to-story")
async def send_to_story(jid: str, body: ToStoryIn):
    """🎙 Make this render the STORY's narration recording.

    Why here rather than "download, then upload on /worlds": that round trip is
    where the link between a voice and a story gets lost. Landing it directly
    is what makes *"which stories used this voice"* answerable at all — the
    breadcrumb is written on the job in the same breath as the file.

    ⚠ It REPLACES the story's existing narration audio, exactly as an upload
    there does, and the old file is deleted by that lane."""
    from backend.api import storyworld as sw
    j = _JOBS.get(jid)
    if not j or not j.get("file"):
        raise HTTPException(404, "no finished audio on that job")
    src = _TRACK_DIR / j["file"]
    if not src.exists():
        raise HTTPException(404, "the audio file is missing on disk")
    slot = body.slot if body.slot in sw._NARR_SLOTS else "audio"
    w = sw._load(body.world_id)
    story = sw._find(w.get("stories") or [], body.story_id, "story")
    aid = uuid.uuid4().hex[:10]
    d = sw._NARR_DIR / body.world_id
    d.mkdir(parents=True, exist_ok=True)
    fp = d / f"{aid}{src.suffix}"
    fp.write_bytes(src.read_bytes())
    meta = {"id": aid, "filename": f"{(j.get('voice') or 'tts')}_{jid}{src.suffix}",
            "ext": src.suffix, "bytes": fp.stat().st_size, "slot": slot,
            "playable": True, "seconds": _probe_seconds(fp),
            "uploaded_at": _now(),
            "from_audio_lab": {"job": jid, "voice": j.get("voice"),
                               "voice_id": j.get("voice_id"),
                               "engine": j.get("engine")}}
    old = None
    with sw._LOCK:
        w2 = sw._load(body.world_id)
        st2 = sw._find(w2.get("stories") or [], body.story_id, "story")
        files = sw._narr_files(st2)
        old = files.get(slot)
        files[slot] = meta
        st2["narration_files"] = files
        st2.pop("narration_audio", None)
        st2["updated_at"] = sw._now()
        sw._save(w2)
    if old and old.get("id") != aid:
        sw._slot_fp(body.world_id, old).unlink(missing_ok=True)
    with _LOCK:
        used = list(j.get("used_in") or [])
        used.append({"kind": "story", "world_id": body.world_id,
                     "story_id": body.story_id,
                     "story": story.get("title"), "world": w.get("name"),
                     "slot": slot, "at": _now()})
        j["used_in"] = used
        _jobs_save()
    return {"ok": True, "slot": slot, "file": meta,
            "story": story.get("title"),
            "note": "it is the story's narration recording now — pull it into "
                    "a linked project from the project's Audio tab"}


async def import_job_to_project(jid: str, project_id: str) -> dict:
    """Copy the finished track into a project as a MUSIC asset — from there
    the existing pipeline (analysis, scenes, mux, export) takes over.

    Shared with the 🎼 score lane, which imports a whole cue list at once."""
    import hashlib
    import shutil
    from backend.database.database import async_session
    from backend.database.models import Asset, AssetType, Project
    j = _JOBS.get(jid)
    if not j or not j.get("file"):
        raise HTTPException(404, "no finished audio on that job")
    src = _TRACK_DIR / j["file"]
    async with async_session() as session:
        proj = await session.get(Project, project_id)
        if not proj:
            raise HTTPException(404, "project not found")
        proj_dir = Path(cfg.project_dir) / str(proj.id)
        dest_dir = proj_dir / "assets" / "audio"
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / f"audiolab_{jid}{src.suffix}"
        shutil.copy2(src, dest)
        data = dest.read_bytes()
        rel = str(dest.relative_to(proj_dir))
        asset = Asset(project_id=proj.id, filename=dest.name, rel_path=rel,
                      asset_type=AssetType.MUSIC,
                      sha256=hashlib.sha256(data).hexdigest(),
                      file_size=len(data),
                      meta={"source": "audio_lab", "job": jid,
                            "engine": j.get("engine"),
                            "kind": j.get("kind"),
                            "voice_id": j.get("voice_id"),
                            "voice": j.get("voice")})
        session.add(asset)
        await session.commit()
        # 🪪 breadcrumb for the voice details view — "outputs using this voice"
        # is only answerable if the job records where it LANDED
        with _LOCK:
            used = list(j.get("used_in") or [])
            used.append({"kind": "project", "project_id": str(proj.id),
                         "project": proj.name, "rel_path": rel, "at": _now()})
            j["used_in"] = used
            _jobs_save()
        return {"asset_rel_path": rel,
                "note": "imported as a MUSIC asset — run audio analysis in "
                        "the project to build sections/scenes from it"}
