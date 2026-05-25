# Robomuffin Idea Factory

A local desktop application for creating AI-powered music videos and narration videos. Upload a song, analyze its structure, define your creative vision, and generate scene-by-scene AI images and videos — all synced to a visual timeline. Powered by ComfyUI remote servers for generation, with LLM-assisted prompt enhancement and creative direction.

![Robomuffin Idea Factory](Screenshots/robomuffin_idea_factory_screenshot.webp)

## Sample Output

These videos were generated entirely by the app using ComfyUI + LTX 2.3 video generation:

<a href="https://www.youtube.com/watch?v=jg3y52mkEXI">
  <img src="https://img.youtube.com/vi/jg3y52mkEXI/maxresdefault.jpg" alt="Sample Output - Latest" width="600"/>
</a>

<a href="https://www.youtube.com/watch?v=NAf-MVPxjJI">
  <img src="https://img.youtube.com/vi/NAf-MVPxjJI/maxresdefault.jpg" alt="Sample Output 2" width="600"/>
</a>

<a href="https://www.youtube.com/watch?v=ysumK--oPEI">
  <img src="https://img.youtube.com/vi/ysumK--oPEI/maxresdefault.jpg" alt="Sample Output 3" width="600"/>
</a>

<a href="https://www.youtube.com/watch?v=hmp0o6oHwH8">
  <img src="https://img.youtube.com/vi/hmp0o6oHwH8/maxresdefault.jpg" alt="Sample Output 4" width="600"/>
</a>

## Features

### Creative Pipeline
- **Audio Analysis** — Upload a song and automatically detect sections (intro, verse, chorus, bridge, outro), separate stems (vocals, drums, bass, other) via Demucs, and transcribe lyrics via Whisper (local, remote Gradio, or ComfyUI workflow) with automatic hallucination deduplication
- **Concept & Style** — Define your video's overall concept, visual style, and characters with reference images. "Base on Lyrics" lets an LLM generate your concept and style from the song's lyrics automatically
- **Video Flow** — LLM-generated per-scene storyboard ideas that describe camera movement, action, mood, and composition for each scene
- **Suggest Fresh Timeline** — LLM analyzes your lyrics, sections, and timing data to generate optimal scene boundaries with meaningful narrative breaks
- **Character Creator** — Built-in mini image generator for creating character reference images with version history, using the same reference image system as scene generation
- **LTXDirector Integration** — Full control over LTX Director video generation parameters: guide strength (keyframe conditioning), audio guidance (audio-to-video influence), stitch mode (smooth vs hard-cut prompt transitions), auto image description, and video negative prompt. All configurable in Settings
- **Scene Editor** — Tabbed editor with Image (First Frame / Last Frame sub-tabs), Video, Stems, Lyrics, Tools, Image Movement, and Prompt tabs per scene
- **Reference Image System** — Select up to 2 characters and upload additional reference images per scene. Workflow auto-selects based on reference count (0–4 images). Uses FLUX Klein "Image N" syntax for precise reference mapping
- **Two-Pass Image Generation** — Pass 1 generates the scene environment (no characters), Pass 2 composites characters into the scene using the Pass 1 output as a reference. Prevents character IP-Adapter from making all scenes look identical
- **Prompt Enhancement** — LLM-powered prompt enhancement with context awareness (model type, scene flow, camera action, character descriptions, reference images, lipsync state). Built-in system prompt registry with per-model overrides configurable in Settings. Video prompts are Director-aware with multi-segment support
- **Camera Action Presets** — 24 film-industry camera motions (pan, tilt, dolly, crane, orbit, steadicam, etc.) integrated into video prompt enhancement
- **Lipsync System** — Per-scene toggle that boosts audio_guidance to 0.7+ for better mouth-to-audio synchronization. Optional vocal stem isolation sends only the vocal track to the generator for cleaner sync signal. Default ON for new projects, configurable in Auto Gen modal and per-scene Video tab
- **Image Direction** — Control the overall visual style with presets (Photorealistic, Cinematic, Cartoon, Anime, Sketch, Watercolor, Oil Painting, 3D Render, Comic Book, Pixel Art, Abstract, Surreal) or custom free-text direction
- **Auto Generate** — Six intelligent modes: all images, all video (single frame), missing videos, all video (first/last frame chaining), all video (V2V extend for seamless transitions), and independent batch-parallel image generation
- **Image Movement (Ken Burns)** — Apply pan, zoom, and motion effects to still images during export
- **Export Transitions** — Automatic crossfade/dissolve transitions between clips with configurable duration and adjacent-clip color matching
- **Render Preview** — Quick 720p preview assembly before full export
- **Scene Locking** — Lock scene boundaries to prevent accidental changes. Persists across app restarts
- **Global Negative Prompt** — Set a negative prompt in Settings that applies to all image generation workflows. Per-scene negative prompts override the global when set. The effective negative prompt (global vs scene override) is displayed in each scene's Prompt tab after generation
- **Custom Workflow Management** — Upload your own ComfyUI workflow JSON files with auto-introspection and field mapping. Assign custom workflows per-server or globally, and select them from the Image/Video tab dropdowns
- **Asset Manager** — Browse and manage all project assets (characters, reference images, generated images/videos) with thumbnail grid view, lightbox preview, and direct-use-as-reference from the asset library
- **Live Batch Preview (PIP)** — Floating picture-in-picture overlay during batch processing shows the last generated image or video with scene name, elapsed time, prompt snippet, and IMAGE/VIDEO badge. Draggable (mouse + touch), resizable (small/medium/large), minimizable. Auto-positions to bottom-right corner
- **Mobile Responsive Layout** — Full mobile support lets you open the UI on your phone at `http://local-ip:8899` to monitor batch progress. Bottom navigation bar with panel/editor/queue tabs, collapsible sidebars, wrapping toolbars, and full-screen modals on small screens. Tablet breakpoint at 1024px
- **Settings Import/Export** — Export all app settings to JSON and import on another machine for easy configuration sharing
- **Project Directory** — Configure where project data is stored via Settings, with the option to move existing data to a new location
- **Edit Project Name** — Rename projects via the toolbar menu (display name only — files and directories unchanged)

### Technical Highlights
- **Multi-server ComfyUI** — Concurrent dispatch across multiple remote ComfyUI instances with capability-based routing and worker reservation
- **LTXDirector Multi-Segment Prompts** — Video prompts can contain multiple segments separated by line breaks, each becoming a sequential temporal segment in the video. LLM prompt enhancer is Director-aware and generates single or multi-segment prompts based on scene content
- **V2V Extending** — Image-based conditioning from previous scene's last frame for seamless scene-to-scene transitions
- **AI Transition Clips** — LTX Transition LoRA generates short transition videos between scenes
- **Lipsync Audio Boost** — Per-scene lipsync toggle boosts Director audio_guidance from base level to 0.7+ for mouth-to-audio sync. Optional vocal stem isolation filters non-vocal audio before sending to generator
- **GPU Hardware Acceleration** — Auto-detects GPU encoders (NVIDIA NVENC, AMD AMF/VAAPI, Intel QSV) for FFmpeg and CUDA for Demucs
- **Color Correction** — Automatic per-channel RGB color matching with skip thresholds to avoid unnecessary re-encodes
- **RunPod Integration** — Optional serverless GPU pod management with auto-spindown
- **Real-time Progress** — SSE pub/sub broadcaster streams progress from ComfyUI to all connected frontends
- **Live Batch Preview** — Floating PIP overlay streams the latest generated asset during batch processing via SSE events, with scene info and elapsed time
- **Mobile Responsive** — CSS media queries at 768px/1024px breakpoints with mobile bottom nav bar, panel toggling, and toolbar wrapping for phone/tablet monitoring
- **Desktop Native** — pywebview wraps the app in a native window (browser mode also available)

## ComfyUI Server Setup

Your remote ComfyUI server(s) need the following models and custom nodes installed. The app sends workflow API calls to these servers — it does not run ComfyUI locally.

### Required Models

Place these in the appropriate directories on your ComfyUI server(s):

#### Edit Model — Reference-Based Image Generation (FLUX.2 Klein 9B)

| File | Directory | Download |
|------|-----------|----------|
| `flux-2-klein-9b-Q8_0.gguf` | `models/unet/` | [Kijai/flux-2-klein-9b-gguf](https://huggingface.co/Kijai/flux-2-klein-9b-gguf) |
| `flux2-vae.safetensors` | `models/vae/` | [black-forest-labs/FLUX.1-dev](https://huggingface.co/black-forest-labs/FLUX.1-dev) |
| `qwen_3_8b_fp8mixed_abliterated.safetensors` | `models/clip/` | [Kijai/flux-2-klein-9b-gguf](https://huggingface.co/Kijai/flux-2-klein-9b-gguf) |

#### Single Image Generator — Text-to-Image (Z-Image Turbo)

Z-Image Turbo is a fast 6B-parameter text-to-image model using the S3-DiT architecture. It generates images in 8 sampling steps with no reference image support, making it ideal for two-pass base scene generation and character creation without references.

| File | Directory | Download |
|------|-----------|----------|
| `z_image_turbo_bf16.safetensors` | `models/diffusion_models/` | [Comfy-Org/z_image_turbo](https://huggingface.co/Comfy-Org/z_image_turbo/tree/main/split_files/diffusion_models) |
| `qwen_3_4b.safetensors` | `models/text_encoders/` | [Comfy-Org/z_image_turbo](https://huggingface.co/Comfy-Org/z_image_turbo/tree/main/split_files/text_encoders) |
| `ae.safetensors` | `models/vae/` | [Comfy-Org/z_image_turbo](https://huggingface.co/Comfy-Org/z_image_turbo/tree/main/split_files/vae) |

> **Tip:** Run `Download Models.bat` to download all Z-Image Turbo and Distilled LoRA models automatically.

#### Video Generation (LTX 2.3)

| File | Directory | Download |
|------|-----------|----------|
| `ltx-2.3-22b-dev-Q8_0.gguf` | `models/unet/` | [Kijai/ltx-video-gguf](https://huggingface.co/Kijai/ltx-video-gguf) (Q8_0 default; Q6_K and Q5_K_S also selectable in Settings) |
| `LTX23_video_vae_bf16.safetensors` | `models/vae/` | [Lightricks/LTX-Video](https://huggingface.co/Lightricks/LTX-Video) |
| `LTX23_audio_vae_bf16.safetensors` | `models/vae/` | [Lightricks/LTX-Video](https://huggingface.co/Lightricks/LTX-Video) |
| `ltx-2.3_text_projection_bf16.safetensors` | `models/clip/` | [Lightricks/LTX-Video](https://huggingface.co/Lightricks/LTX-Video) |
| `gemma_3_12B_it_fp4_mixed.safetensors` | `models/clip/` | [Kijai/gemma-3-12B-it_comfy](https://huggingface.co/Kijai/gemma-3-12B-it_comfy) |
| `ltx-2.3-spatial-upscaler-x2-1.0.safetensors` | `models/upscale_models/` | [Lightricks/LTX-Video](https://huggingface.co/Lightricks/LTX-Video) |

#### LoRAs — Image Generation (Flux Klein 9B)

| File | Directory | Download |
|------|-----------|----------|
| `lenovo_flux_klein9b.safetensors` | `models/loras/` | Required for T2I workflow |
| `nicegirls_flux_klein9b.safetensors` | `models/loras/` | Required for T2I workflow |
| `detail_slider_klein_9b_20260123_065513.safetensors` | `models/loras/` | Required for T2I workflow |
| `darkBeastFeb1826Latest_dbkBlitzV15.safetensors` | `models/loras/` | Required for T2I workflow |
| `anime2real-semi.safetensors` | `models/loras/` | Required for 1REF / 2REF / 3REF / 4REF workflows |

#### LoRAs — Video Generation (LTX 2.3)

| File | Directory | Download |
|------|-----------|----------|
| `ltx-2.3-22b-distilled-lora-384-1.1.safetensors` | `models/loras/` | [Lightricks/LTX-2.3](https://huggingface.co/Lightricks/LTX-2.3) (v1.1 — **DEFAULT**, rank-384, ~7.6GB — improved aesthetics and audio, 8 steps instead of 20+) |
| `ltx-2.3-22b-distilled-lora-384.safetensors` | `models/loras/` | [Lightricks/LTX-2.3](https://huggingface.co/Lightricks/LTX-2.3) (v1.0 — optional alternate, same architecture as v1.1) |
| `ltx-2-19b-ic-lora-detailer.safetensors` | `models/loras/` | Required for FF/LF, I2V, and V2V workflows |
| `Ltx2.3-Licon-VBVR-I2V-96000-R32.safetensors` | `models/loras/` | Required for FF/LF, I2V, and V2V workflows |
| `ltx2.3-transition.safetensors` | `models/loras/` | [valiantcat/LTX-2.3-Transition-LORA](https://huggingface.co/valiantcat/LTX-2.3-Transition-LORA) (required for AI transition clips) |

### Required Custom Nodes

Install these via ComfyUI Manager or clone into `custom_nodes/`:

| Custom Node Pack | Purpose | Install |
|-----------------|---------|---------|
| **ComfyUI-LTXVideo** | All LTX 2.3 video nodes (sampling, VAE, latent guides, audio) | [github.com/Lightricks/ComfyUI-LTXVideo](https://github.com/Lightricks/ComfyUI-LTXVideo) |
| **ComfyUI-GGUF** | GGUF model loading for Klein + LTX quantized models | [github.com/city96/ComfyUI-GGUF](https://github.com/city96/ComfyUI-GGUF) |
| **ComfyUI-VideoHelperSuite** | Video output combining (VHS_VideoCombine) | [github.com/Kosinkadink/ComfyUI-VideoHelperSuite](https://github.com/Kosinkadink/ComfyUI-VideoHelperSuite) |
| **ComfyUI-KJNodes** | Image resize, VAE loading, math expressions | [github.com/kijai/ComfyUI-KJNodes](https://github.com/kijai/ComfyUI-KJNodes) |
| **WhatDreamsCost-ComfyUI** | LTXDirector + LTXDirectorGuide nodes for frame-controlled video generation (Sequencer workflows) | [github.com/WhatDreamsCost/WhatDreamsCost-ComfyUI](https://github.com/WhatDreamsCost/WhatDreamsCost-ComfyUI) |
| **ComfyUI-Easy-Use** | GPU memory cleanup between video passes (prevents OOM) | [github.com/yolain/ComfyUI-Easy-Use](https://github.com/yolain/ComfyUI-Easy-Use) |
| **rgthree-comfy** | Power LoRA loader, image comparison | [github.com/rgthree/rgthree-comfy](https://github.com/rgthree/rgthree-comfy) |
| **ComfyUI-Custom-Scripts** | Math expressions, switch nodes | [github.com/pythongosssss/ComfyUI-Custom-Scripts](https://github.com/pythongosssss/ComfyUI-Custom-Scripts) |

#### Optional Custom Nodes

| Custom Node Pack | Purpose | Install |
|-----------------|---------|---------|
| **ComfyUI-Whisper** | Whisper transcription via ComfyUI (alternative to local/Gradio) | [github.com/yuvraj108c/ComfyUI-Whisper](https://github.com/yuvraj108c/ComfyUI-Whisper) |

> **Note:** The app auto-detects missing custom nodes on each ComfyUI server before job submission. Non-essential missing nodes (like display/debug nodes) are automatically removed and bypassed. Essential missing nodes will produce a clear error message telling you which pack to install.

## Architecture

```
┌──────────────────────────────────────────────────────┐
│  pywebview (native desktop window)                   │
│  ┌────────────────────────────────────────────────┐  │
│  │  React 18 + TypeScript + Vite                  │  │
│  │  TailwindCSS, Zustand, wavesurfer.js           │  │
│  └──────────────────┬─────────────────────────────┘  │
│                     │ HTTP / SSE                      │
│  ┌──────────────────▼─────────────────────────────┐  │
│  │  FastAPI (Python 3.11.x recommended)            │  │
│  │  SQLite (WAL mode) via SQLModel + aiosqlite    │  │
│  │  Job Queue → ComfyUI Dispatcher                │  │
│  └───────┬──────────────────────┬─────────────────┘  │
└──────────┼──────────────────────┼────────────────────┘
           │ HTTP + WebSocket     │ Gradio / HTTP
┌──────────▼──────────────┐  ┌───▼───────────────────┐
│  ComfyUI Remote Servers │  │  Whisper Server        │
│  • FLUX.2 Klein 9B (img)│  │  (Gradio / ComfyUI /   │
│  • LTX 2.3 (video)      │  │   local WhisperX)      │
│  • Whisper (optional)    │  │                        │
└─────────────────────────┘  └────────────────────────┘
```

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Desktop | pywebview 5.3+ |
| Frontend | React 18, TypeScript, Vite, TailwindCSS, Zustand, wavesurfer.js |
| Backend | FastAPI, SQLModel, aiosqlite, Pydantic v2 |
| AI Generation | ComfyUI (remote), FLUX.2 Klein 9B (images), LTX 2.3 (video) |
| Audio | Demucs (stems, GPU via PyTorch CUDA), Whisper (3 backends), librosa (sections) |
| Video Assembly | FFmpeg (GPU-accelerated via NVENC/AMF/VAAPI/QSV) |
| LLM | OpenAI (GPT-4o through GPT-5.5), Anthropic Claude (3.5 Sonnet through Opus 4.7), Google Gemini |

## Prerequisites

- **Python 3.10–3.12** (3.11.x recommended) — Uses `StrEnum` and async features requiring 3.10+. Python 3.13+ is **not supported** due to PyTorch/WhisperX compatibility
- **Node.js 18+** and **npm** — For building the React frontend
- **FFmpeg** — On system PATH. Auto-detects GPU encoders (NVENC, AMF, QSV)
- **At least one remote ComfyUI server** — With the models and nodes listed above installed
- **At least one LLM API key** (recommended) — OpenAI, Anthropic, or Gemini for prompt enhancement

## Installation

### 1. Clone and Set Up

```bash
git clone https://github.com/robomuffindev/RBMN-Storyboard_App.git
cd RBMN-Storyboard_App

# Python environment
python -m venv venv
source venv/bin/activate  # Linux/macOS
# venv\Scripts\activate   # Windows

# Optional: CUDA PyTorch for faster Demucs stem separation
pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu121

pip install -e ".[dev]"

# Frontend
cd frontend && npm install && npm run build && cd ..
```

### 2. Configure

```bash
cp .env.example .env   # Linux/macOS
# copy .env.example .env  # Windows
```

Edit `.env` with your ComfyUI server URL(s), Whisper settings, and LLM API keys.

### 3. Run

```bash
python run.py              # Desktop mode (pywebview)
python run.py --mode browser  # Browser mode
```

**Windows users** can also use the included batch scripts:
- `install.bat` — Full installation
- `run.bat` — Launch in desktop mode
- `Run_Browser_Mode.bat` — Launch in browser mode (opens `http://localhost:8899`)

### Fixing PyTorch CUDA (Existing Installs)

If you installed from an earlier version, your PyTorch may be CPU-only — local Whisper transcription and Demucs stem separation will run much slower (or fail silently). You can check by running:

```bash
python -c "import torch; print(torch.cuda.is_available())"
```

If it prints `False` and you have an NVIDIA GPU, run the included fix script:

```
fix-pytorch-cuda.bat
```

This auto-detects your GPU and CUDA version, uninstalls the CPU-only PyTorch, and reinstalls the correct CUDA build. New installs from `install.bat` will warn you if this is needed.

## Typical Workflow

1. **Create a project** — Choose Music Video, Narration (Moving Images), or Narration (Video) mode
2. **Upload audio** — Import your song or narration audio file
3. **Process audio** — Detect sections, separate stems, and transcribe lyrics
4. **Define concept** — Set song title, concept, style, characters, and image direction
5. **Suggest timeline** — Let the LLM create optimal scene boundaries from your lyrics
6. **Lock scenes** — Prevent accidental boundary changes
7. **Generate video flow** — LLM creates per-scene storyboard ideas
8. **Generate images** — Select character references, enhance prompts, generate first frames
9. **Generate videos** — Choose Single Image (I2V), First/Last Frame, or V2V Extend mode
10. **Preview and export** — Render preview, then export final video with transitions

## Development

```bash
# Backend (hot reload)
cd backend && uvicorn main:app --reload --port 8899

# Frontend (Vite HMR, separate terminal)
cd frontend && npm run dev

# TypeScript check
cd frontend && npx tsc --noEmit
```

## Environment Variables

See `.env.example` for the full list. Key variables:

| Variable | Description |
|----------|-------------|
| `COMFYUI_URLS` | Comma-separated remote ComfyUI server URLs |
| `WHISPER_MODE` | `local`, `remote` (Gradio), or `comfyui` |
| `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` / `GEMINI_API_KEY` | LLM API keys |
| `PROJECT_DIR` | Where project data is stored (default: `./project_data`) |

## Version

Current version: **1.0.0** — See [CHANGELOG.md](CHANGELOG.md) for release history.

## License

This project is proprietary. All rights reserved.
