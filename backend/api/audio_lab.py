"""🎧 Audio Lab (v1.277.14) — music + narration, locally, on the fleet.

MUSIC — two engines, both native ComfyUI (researched 2026-08-15):
  * ACE-Step 1.5 XL turbo (`ace15`): AIO checkpoint, 4B DiT, full song in
    seconds on this fleet, EXACT duration control — the story-arc pairing he
    wants. Nodes verified PRESENT on the workers (object_info).
  * MiniMax Music 3 (`minimax3`): 8B+0.6B AR + Flow-VAE, ≤5 min songs,
    structured Caption + [tagged] Lyrics. Engine auto-detects its nodes; if
    the workers' ComfyUI predates 2026-08-13 it reports "needs update".

NARRATION (TTS) — engine `f5tts` via the ComfyUI-F5-TTS custom node
(research: F5-TTS is the open-weights quality leader; cloning = ONE clean
5-15s reference WAV + its exact transcript). Long texts are split on blank
lines and rendered chunk by chunk with a configurable silence between
paragraphs (his pause control), then concatenated with ffmpeg. If the node
isn't installed the lane says exactly what to run (scripts/install_audio.py).

Every generation follows the STANDING RULE: live, expandable, verbose status
(what, where, how long), recorded to disk as benchmarking data.

Storage: <project_dir>/_libraries/audio_lab/  (import-time anchored).
"""
from __future__ import annotations

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
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


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
    return out


def _model_present(host: str, route: str, fname: str) -> bool:
    try:
        got = _jget(f"http://{host}:8188/models/{route}", timeout=15)
        return any(fname in str(x) for x in (got or []))
    except Exception:                                            # noqa: BLE001
        return False


def _engine_status(host: str) -> dict:
    """What THIS worker can do — nodes AND models, both from the box itself."""
    ace_node = _node_info(host, "TextEncodeAceStepAudio1.5") is not None
    mm3_node = any(_node_info(host, n) is not None
                   for n in ("MiniMaxMusic3TextEncode", "TextEncodeMiniMaxMusic3",
                             "MiniMaxMusic3", "MiniMaxMusicTextEncode"))
    f5_node = any(_node_info(host, n) is not None
                  for n in ("F5TTSAudio", "F5TTSAudioInputs", "F5TTSCreate"))
    return {
        "ace15": {"nodes": ace_node,
                  "model": _model_present(host, "checkpoints", ACE_CKPT),
                  "ready": ace_node and _model_present(host, "checkpoints", ACE_CKPT)},
        "minimax3": {"nodes": mm3_node,
                     "model": _model_present(host, "diffusion_models", MM3_DIT),
                     "ready": mm3_node and _model_present(host, "diffusion_models", MM3_DIT),
                     "note": "" if mm3_node else
                     "ComfyUI predates MiniMax Music 3 (2026-08-13) — update ComfyUI on this box"},
        "f5tts": {"nodes": f5_node, "model": f5_node,
                  "ready": f5_node,
                  "note": "" if f5_node else
                  "install the ComfyUI-F5-TTS node: python scripts/install_audio.py"},
    }


# ── graphs ───────────────────────────────────────────────────────────────────
def _ace_graph(host: str, tags: str, lyrics: str, seconds: float, seed: int,
               bpm: int, keyscale: str, language: str, prefix: str) -> dict:
    enc = _inputs_with_defaults(host, "TextEncodeAceStepAudio1.5", {
        "clip": ["1", 1], "tags": tags, "lyrics": lyrics or "[instrumental]",
        "seed": seed, "duration": float(seconds),
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
        "8": {"class_type": "SaveAudioMP3",
              "inputs": {"audio": ["7", 0], "filename_prefix": prefix,
                         "quality": "V0"}},
    }


def _run_graph_job(jid: str, host: str, graph: dict, out_ext: str = ".mp3",
                   timeout_s: float = 1800.0) -> Path:
    """Submit → poll history → download the audio output. Blocking; thread."""
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
                    fp = _TRACK_DIR / f"{jid}{ext}"
                    fp.write_bytes(data)
                    return fp
    raise TimeoutError(f"no audio after {int(timeout_s)}s")


def _log(jid: str, msg: str) -> None:
    st = _JOBS.get(jid) or {}
    st.setdefault("log", []).append(
        {"t": round(time.time() - st.get("_t0", time.time()), 1), "detail": msg})
    st["detail"] = msg


# ── music ────────────────────────────────────────────────────────────────────
class MusicIn(BaseModel):
    engine: str = "ace15"            # ace15 | minimax3
    tags: str = ""                   # ace: style tags · mm3: structured caption
    lyrics: str = ""                 # [tagged] lyrics; empty = instrumental
    seconds: float = 60.0            # EXACT length (the story-arc pairing)
    bpm: int = 0                     # 0 = model's choice
    keyscale: str = ""               # e.g. "E minor"
    language: str = "en"
    seed: Optional[int] = None
    host: str = ""                   # '' = first ready worker
    label: str = ""


@router.post("/music/generate")
async def music_generate(body: MusicIn):
    if body.engine not in ("ace15", "minimax3"):
        raise HTTPException(400, "engine must be ace15 or minimax3")
    if not body.tags.strip():
        raise HTTPException(400, "describe the music (tags/caption)")
    seconds = max(5.0, min(300.0, float(body.seconds)))
    hosts = [h["host"] for h in _hosts()]
    if body.host:
        hosts = [body.host]
    ready = next((h for h in hosts
                  if _engine_status(h).get(body.engine, {}).get("ready")), None)
    if not ready:
        sts = {h: _engine_status(h).get(body.engine, {}) for h in hosts}
        raise HTTPException(409, f"no worker is ready for {body.engine}: "
                                 + json.dumps(sts)[:400])
    if body.engine == "minimax3":
        raise HTTPException(501, "MiniMax Music 3 nodes detected but the graph "
                                 "wiring lands after its first live export — "
                                 "use ace15 for now")
    seed = body.seed if body.seed is not None else int(time.time()) % 2**31
    jid = uuid.uuid4().hex[:10]
    st = {"id": jid, "kind": "music", "engine": body.engine,
          "label": body.label or (body.tags[:60] + "…"),
          "tags": body.tags, "lyrics_len": len(body.lyrics or ""),
          "seconds": seconds, "seed": seed, "worker": ready,
          "status": "queued", "detail": "submitting", "error": None,
          "elapsed_s": 0, "log": [], "at": _now(), "_t0": time.time()}
    with _LOCK:
        _JOBS[jid] = st
        _jobs_save()

    def _run():
        try:
            st["status"] = "running"
            _log(jid, f"building ACE-Step graph on {ready}")
            g = _ace_graph(ready, body.tags, body.lyrics, seconds, seed,
                           body.bpm, body.keyscale, body.language,
                           f"RBMN-AUDIO/ace_{jid}")
            _log(jid, f"rendering {seconds:.0f}s of music")
            fp = _run_graph_job(jid, ready, g)
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


# ── TTS / narration ──────────────────────────────────────────────────────────
_VOICES_FP = _VOICE_DIR / "voices.json"


def _voices() -> List[dict]:
    try:
        return json.loads(_VOICES_FP.read_text("utf-8"))
    except Exception:                                            # noqa: BLE001
        return []


def _voices_save(v: List[dict]) -> None:
    _VOICE_DIR.mkdir(parents=True, exist_ok=True)
    _VOICES_FP.write_text(json.dumps(v, indent=1), "utf-8")


@router.get("/tts/voices")
async def voices():
    return {"voices": _voices(),
            "cloning_guide": (
                "Voice cloning needs ONE clean reference: 5-15 seconds of the "
                "voice speaking naturally (WAV/MP3), no music, no room echo, "
                "one speaker, and the EXACT transcript of what is said. "
                "Longer is not better — clean and typical is.")}


@router.post("/tts/voices")
async def add_voice(name: str = Form(...), transcript: str = Form(...),
                    file: UploadFile = File(...)):
    if not name.strip() or not transcript.strip():
        raise HTTPException(400, "name and the exact transcript are required")
    data = await file.read()
    if len(data) < 20_000:
        raise HTTPException(400, "that file is too small to be a usable "
                                 "reference — aim for 5-15s of clean speech")
    vid = uuid.uuid4().hex[:8]
    _VOICE_DIR.mkdir(parents=True, exist_ok=True)
    ext = Path(file.filename or "ref.wav").suffix or ".wav"
    (_VOICE_DIR / f"{vid}{ext}").write_bytes(data)
    v = _voices()
    v.append({"id": vid, "name": name.strip(), "ext": ext,
              "transcript": transcript.strip(), "at": _now()})
    _voices_save(v)
    return v[-1]


@router.post("/tts/voices/{vid}/delete")
async def del_voice(vid: str):
    v = [x for x in _voices() if x["id"] != vid]
    _voices_save(v)
    for fp in _VOICE_DIR.glob(f"{vid}.*"):
        fp.unlink(missing_ok=True)
    return {"ok": True}


class TtsIn(BaseModel):
    voice_id: str
    text: str
    pause_ms: int = 600              # silence between paragraphs (blank lines)
    speed: float = 1.0
    seed: Optional[int] = None
    host: str = ""
    label: str = ""


@router.post("/tts/generate")
async def tts_generate(body: TtsIn):
    voice = next((v for v in _voices() if v["id"] == body.voice_id), None)
    if not voice:
        raise HTTPException(404, "no such voice — add one first (a 5-15s "
                                 "clean sample + its exact transcript)")
    if not body.text.strip():
        raise HTTPException(400, "give it text to speak")
    hosts = [h["host"] for h in _hosts()]
    if body.host:
        hosts = [body.host]
    ready = next((h for h in hosts if _engine_status(h)["f5tts"]["ready"]), None)
    if not ready:
        raise HTTPException(409, "F5-TTS is not installed on any worker yet — "
                                 "run: python scripts/install_audio.py "
                                 "(installs the ComfyUI-F5-TTS node on every "
                                 "box; models auto-download on first use)")
    # chunk on blank lines — his pause control between paragraphs/sections
    chunks = [c.strip() for c in re.split(r"\n\s*\n", body.text) if c.strip()]
    seed = body.seed if body.seed is not None else int(time.time()) % 2**31
    jid = uuid.uuid4().hex[:10]
    st = {"id": jid, "kind": "tts", "engine": "f5tts",
          "label": body.label or (body.text[:60] + "…"),
          "voice": voice["name"], "chunks": len(chunks),
          "pause_ms": body.pause_ms, "worker": ready,
          "status": "queued", "detail": "submitting", "error": None,
          "elapsed_s": 0, "log": [], "at": _now(), "_t0": time.time()}
    with _LOCK:
        _JOBS[jid] = st
        _jobs_save()

    def _run():
        try:
            st["status"] = "running"
            base = f"http://{ready}:8188"
            # upload the reference once
            ref_fp = next(_VOICE_DIR.glob(f"{voice['id']}.*"))
            up_name = _upload_input(base, ref_fp)
            parts: List[Path] = []
            for i, chunk in enumerate(chunks):
                _log(jid, f"chunk {i + 1}/{len(chunks)} on {ready}")
                st["elapsed_s"] = round(time.time() - st["_t0"], 1)
                g = _f5_graph(ready, up_name, voice["transcript"], chunk,
                              seed + i, body.speed, f"RBMN-AUDIO/tts_{jid}_{i}")
                parts.append(_run_graph_job(jid, ready, g, ".wav",
                                            timeout_s=900))
            _log(jid, "concatenating with paragraph pauses")
            out = _concat_with_pauses(parts, body.pause_ms, jid)
            for p in parts:
                if p != out:
                    p.unlink(missing_ok=True)
            st["file"] = out.name
            st["status"] = "done"
            st["elapsed_s"] = round(time.time() - st["_t0"], 1)
            _log(jid, f"done in {st['elapsed_s']}s → {out.name}")
        except Exception as e:                                   # noqa: BLE001
            st["status"] = "error"
            st["error"] = f"{type(e).__name__}: {e}"
            st["elapsed_s"] = round(time.time() - st["_t0"], 1)
        finally:
            with _LOCK:
                _jobs_save()

    threading.Thread(target=_run, daemon=True, name=f"tts-{jid}").start()
    return {"started": True, "id": jid, "worker": ready, "chunks": len(chunks)}


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


def _concat_with_pauses(parts: List[Path], pause_ms: int, jid: str) -> Path:
    import subprocess
    out = _TRACK_DIR / f"{jid}.wav"
    if len(parts) == 1 and pause_ms <= 0:
        parts[0].rename(out)
        return out
    if len(parts) == 1:
        parts[0].rename(out)
        return out
    lst = _TRACK_DIR / f"{jid}_concat.txt"
    silence = _TRACK_DIR / f"{jid}_sil.wav"
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi",
                    "-i", "anullsrc=r=24000:cl=mono",
                    "-t", str(max(0.05, pause_ms / 1000.0)), str(silence)],
                   capture_output=True, check=True)
    lines = []
    for i, p in enumerate(parts):
        if i:
            lines.append(f"file '{silence.name}'")
        lines.append(f"file '{p.name}'")
    lst.write_text("\n".join(lines), "utf-8")
    subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0",
                    "-i", str(lst), str(out)],
                   capture_output=True, check=True, cwd=str(_TRACK_DIR))
    lst.unlink(missing_ok=True)
    silence.unlink(missing_ok=True)
    return out


# ── shared: overview / jobs / media / import ─────────────────────────────────
@router.get("/overview")
async def overview():
    hosts = _hosts()
    return {"workers": [{"host": h["host"], "name": h.get("name"),
                         "engines": _engine_status(h["host"])}
                        for h in hosts],
            "voices": len(_voices())}


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
    """Copy the finished track into a project as a MUSIC asset — from there
    the existing pipeline (analysis, scenes, mux, export) takes over."""
    import hashlib
    import shutil
    from backend.database.database import async_session
    from backend.database.models import Asset, AssetType, Project
    j = _JOBS.get(jid)
    if not j or not j.get("file"):
        raise HTTPException(404, "no finished audio on that job")
    src = _TRACK_DIR / j["file"]
    async with async_session() as session:
        proj = await session.get(Project, body.project_id)
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
                            "kind": j.get("kind")})
        session.add(asset)
        await session.commit()
        return {"asset_rel_path": rel,
                "note": "imported as a MUSIC asset — run audio analysis in "
                        "the project to build sections/scenes from it"}
