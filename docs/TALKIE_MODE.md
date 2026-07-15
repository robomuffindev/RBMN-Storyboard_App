# Talkie Mode — Talking-Head Lip-Sync

*Added v1.45.0 (2026-07-08). `lipsync_ltx` works out of the box; the three dedicated engines need a
worker with the node pack + a workflow export (see below). UNTESTED on a live worker.*

Talkie is a project mode for **stationary talking-head videos**: you upload **one portrait** and a
**narration**, the app segments the narration into scenes, and each scene renders that portrait
lip-syncing the scene's audio. Scenes let you chunk long narration into manageable clips and
regenerate any one independently; assembly concatenates them into the final video.

It reuses the whole narration pipeline (Whisper/AAF segmentation, per-scene audio slicing, subtitles,
`assemble_narration_video`) — Talkie is *narration-like* for segmentation/audio/export but
*video-producing* (each scene gets a `chosen_video_path`, never forced image-only).

## Using it

1. **Create a project** with mode **Talkie (Lip-Sync)**.
2. Click **Talkie Setup** in the toolbar → **upload the portrait** (front-facing, mouth unobstructed,
   good lighting) and **pick the lip-sync engine**.
3. Upload the **narration** and **Analyze** it (same as narration modes) → scenes appear.
4. Generate the per-scene videos (per scene or via auto-gen). Every video job in a Talkie project is
   routed to the chosen engine with the portrait injected as the source image — you don't generate
   per-scene images.
5. **Export** → `assemble_narration_video` concatenates the clips (+ subtitles if enabled).

## Engines (capability-routed, per worker)

| Engine | Look | Install |
|---|---|---|
| **lipsync_ltx** (default) | Natural head motion; one pass via LTX-2.3's audio VAE | **None** — reuses your LTX-2.3 |
| **lipsync_latentsync** | Best-looking *stationary* head, 512², sharp lips | `ComfyUI-LatentSyncWrapper` + model, and `workflows/LIPSYNC_LATENTSYNC.json` |
| **lipsync_musetalk** | Fastest, *truly stationary* (mouth-only inpaint) | MuseTalk ComfyUI nodes + model, and `workflows/LIPSYNC_MUSETALK.json` |
| **lipsync_sonic** | Expressive, emotion-carrying motion | Sonic ComfyUI nodes + model, and `workflows/LIPSYNC_SONIC.json` |

Worker capability is auto-detected from node lists (`latentsync` / `musetalk` / `sonic`). Selecting a
dedicated engine without its workflow file raises a clear error until you add it.

**VRAM guidance:** `lipsync_ltx` and `lipsync_sonic` are heavy (video/SVD models). For low-VRAM workers pick **`lipsync_musetalk`** (~4 GB); `lipsync_latentsync` needs ~8-12 GB with optimizations (~20 GB comfortable). The engine is per-project, so nobody is blocked — low-end workers just choose MuseTalk.

## Providing a dedicated-engine workflow

Export your tested ComfyUI graph for the engine and save it to the named file in `workflows/`. The
dispatcher fills it via `prepare_lipsync_workflow`, which is **defensive and title-based** — give the
load nodes these conventional titles and it wires the portrait, audio, size, and seed for every render
(anything it can't find is left at your JSON's value):

- Portrait image node title: **"Load Image"** (also matches `LOAD IMAGE` / `LoadImage` / `Portrait` / `Source Image`), input `image`.
- Narration audio node title: **"Load Audio"** (also `LoadAudio` / `Audio`), input `audio`.
- Size: nodes titled **"WIDTH"** / **"HEIGHT"** (or `Width`/`Height`), input `value`.
- Seed: **"RandomNoise"** / **"KSampler"** / **"Seed"** / **"SonicSampler"**, input `noise_seed` or `seed`.
- Duration (optional): **"Duration"** (seconds) or **"Video Length"** (frames) / **"Framerate"**.

The final node should save a single video (VHS/SaveVideo) so the dispatcher downloads it as the scene's
`chosen_video_path`.

## Internals

- Enum `ProjectMode.TALKIE` (`backend/database/models.py`); added to the narration-like mode gates
  (timeline resync/snap, concept narration prompt, `assemble_narration_video`, batch skip-demucs) but
  **not** the image-only gates.
- Dispatcher (`backend/services/jobs/dispatcher.py`): a **Talkie routing** block in `_build_workflow`
  overrides any video `workflow_type` to `settings["talkie_engine"]` (default `lipsync_ltx`) and sets
  `first_frame_asset_id` = `settings["portrait_asset_id"]`. `lipsync_ltx` reuses the LTX i2v JSON;
  the three dedicated engines dispatch through `prepare_lipsync_workflow`. Caps in `_get_required_caps`;
  detection in `backend/services/comfyui/dispatcher.py`.
- Config endpoint `PUT /api/projects/{id}/talkie-config`; frontend `TalkieSetupModal`
  (`frontend/src/components/Talkie/`). No DB migration (config lives in `project.settings`).

## Worker prep — nodes + models to install per engine

Install on a GPU ComfyUI worker, then the app auto-detects the capability. `lipsync_ltx` needs nothing
new. For the three dedicated engines you also export your working graph to the `workflows/LIPSYNC_*.json`
file named above.

### LTX-2.3 (lipsync_ltx) — already installed
Uses your existing LTX-2.3 image+audio path. No new nodes or models.

### LatentSync 1.6 (lipsync_latentsync)
- **Node:** `ComfyUI-LatentSyncWrapper` (ShmuelRonen) → `custom_nodes/`, then `pip install -r requirements.txt` (+ system `ffmpeg`). Python deps: diffusers>=0.32.2, transformers, huggingface-hub, omegaconf, einops, opencv-python, mediapipe, face-alignment, decord, ffmpeg-python, soundfile, DeepCache.
- **Models:** LatentSync 1.6 checkpoint + the VAE (`checkpoints/vae/diffusion_pytorch_model.safetensors` + `config.json`) and Whisper (for audio embeddings) — per the repo's model instructions (HF/Google-Drive bundle).
- **VRAM:** ~20 GB is comfortable, but it runs on **~8-12 GB** with xFormers + 512² + batch size 1
  (slower). Best-looking stationary result, and the heaviest of the four engines.

### MuseTalk 1.5 (lipsync_musetalk)
- **Node:** a MuseTalk ComfyUI wrapper (e.g. `ComfyUI-MuseTalk_FSH` / `chaojie/ComfyUI-MuseTalk`) → `custom_nodes/`, `pip install -r requirements.txt`, then `openmim` install of `mmengine, mmcv, mmdet, mmpose`.
- **Models** (under the node's `models/`): `musetalk` (musetalk.json + pytorch_model.bin), `sd-vae-ft-mse` (config.json + diffusion_pytorch_model.bin), `whisper` (tiny.pt), `dwpose` (dw-ll_ucoco_384.pth), `face-parse-bisent` (79999_iter.pth + resnet18-5c106cde.pth).
- **VRAM:** runs on ~**4 GB** (tested on an RTX 3050 Ti laptop). Fastest; truly stationary (mouth-only); the low-VRAM pick. Good for long narrations.

### Sonic (lipsync_sonic)
- **Node:** `ComfyUI_Sonic` (smthemex) → `custom_nodes/`, `pip install -r requirements.txt`.
- **Models — `ComfyUI/models/sonic/`:** `audio2bucket.pth`, `audio2token.pth`, `unet.pth`, `yoloface_v5m.pt`, a `whisper-tiny/` folder (config.json + model.safetensors + preprocessor_config.json), and a `RIFE/` folder (flownet.pkl).
- **Plus `ComfyUI/models/checkpoints/`:** `svd_xt.safetensors` (or `svd_xt_1_1.safetensors`) — Sonic is SVD-based.
- Most expressive (carries emotion), not stationary, slowest.

After installing, export a working graph for the engine (LoadImage + LoadAudio + engine node + a single
video save), give the load nodes the conventional titles above, and save it to the matching
`workflows/LIPSYNC_*.json`.
