# Character Studio — Design & Phasing

**Goal:** a first-class app section for creating characters, organizing them by **Story**
(reusable across projects — series with recurring casts), and producing **idiot-proof LoRA
training datasets** (correct folders, captions, trigger words) for characters and items.

**Reference:** VNCCS 3.0 (ComfyUI Visual Novel Character Creation Suite) — analyzed in full
(node code + workflows + catalogs). We adopt its ideas and its data catalogs, NOT its runtime:
VNCCS stores character state on the ComfyUI host's disk, its workflows are browser-coupled
UI-format graphs (Pose Studio literally renders in the browser), and its dataset step has no
captioning. We own all state app-side and dispatch through our normal job system.

## Decisions (Lorenzo, 2026-07-05)

1. **Engine: Hybrid.** Phase 1 uses ONLY our existing models/workflows (zero new worker deps).
   Phase 2 adds VNCCS-quality upgrades as OUR OWN thin API graphs built from VNCCS's reusable
   atomic nodes (VNCCS_QWEN_Encoder + Qwen-Image-Edit-2511 + pose/clothes/emotion core LoRAs,
   RMBG2/ChromaKey) on the VNCCS-equipped worker — state still app-side.
2. **Dataset formats: both**, per-export choice — kohya/SDXL (booru tags, `N_trigger class`
   folders, TOML) and ai-toolkit/FLUX-family (flat folder, natural-language captions,
   `trigger_word` config.yaml).
3. **Captioning: existing Ollama vision pool** (qwen2.5-vl) with purpose-built templates per
   style; trigger placement + prune-constant-traits rules baked in; manual review/edit UI.
4. **Phase 1 = Creator + Library/Stories + Datasets + push-to-project.**
   Clothes / emotions / pose studio = Phase 2.

## Architecture

- **DB:** `stories`, `studio_characters` (tags JSON from the VNCCS catalog schema, trigger word,
  class word, story binding, manifest of generated shots), `studio_datasets` (target, captions
  JSON, status, export paths).
- **Compute:** a hidden system project (`settings.studio_system=true`, filtered from the
  project list) with one scene per character. All generation runs through the normal Job
  queue/dispatcher — base renders via the first-pass generator (Z-Image/Krea2; Klein when refs
  are attached), **shot variations via Klein edit refs** ("Image 1" instructions per shot:
  angle / expression / framing / background), which is the documented community pattern for
  LoRA dataset generation from a single character. Scene-per-character means the existing
  lightbox, versions, and inpaint tooling keep working on studio renders.
- **Files:** `project_dir/_character_studio/<char_id>/` for datasets + manifest; renders live
  as normal assets of the hidden project.
- **Shot plan:** editable JSON derived from research consensus — angles (front / 3-4 L+R /
  profile / back), expressions (neutral + smile + N picks from the 157-emotion catalog),
  framings ≈ portrait-heavy with full-body minority, background/lighting variation flags.
  Item/object mode swaps expressions for context/state variation.
- **Captions:** per-image, both styles cached on the dataset row; SDXL quality prefix branches
  by target family (Illustrious / NoobAI / Pony `score_N`); constant traits (from the
  character's tag sheet) are pruned from captions so they fuse into the trigger word.
- **Exports:** `datasets/<id>/kohya/...` + `dataset_config.toml`, `datasets/<id>/ai-toolkit/...`
  + `config.yaml`, one zip; README.txt in each explains drop-in usage.
- **Catalogs:** `backend/data/character_studio/` — character_tags.json, outfits.json,
  emotions.json (157), pose_presets.json — imported from VNCCS (their catalog JSONs, ours to
  serve; the vnccs/ reference folder itself stays gitignored).

## Phases (status as of v1.44.0, 2026-07-08)

> The Studio pose/emotion system is the ancestor of the standalone **Tools** section
> (Pose Library + Expression Library + **Generate Sample**) added in 1.34.0/1.44.0 —
> see `docs/TOOLS.md`. Poses committed there are usable on Studio characters via the
> pose picker.

- **P1 (SHIPPED 1.27.0):** Stories CRUD, character sheet (tag builder), base render, shot-plan
  generation via Klein refs, dataset builder (dual-style captions + review/edit + kohya/ai-toolkit
  exports + zip), push-to-project, Studio UI at `/studio`.
- **P2 (SHIPPED 1.28.0–1.30.0):** dual-engine stages — `qwen` (our thin API graphs from VNCCS
  atomic nodes: QIE-2511 GGUF + Lightning + pose/clothes/emotion core LoRAs; worker auto-detected
  via the `vnccs` capability) and `klein` (existing klein_2ref/1ref/inpaint; zero new deps).
  Pose sets (bundled presets + 2D drag-joints pose editor w/ custom presets), costumes (outfit
  catalog suggestions), emotions with THREE engines (qwen edit / klein face-mask inpaint /
  **FaceDetailer face-crop re-render** — VNCCS's exact recipe, `impact`+`vnccs` caps), cutout
  (RMBG2 worker or CPU rembg/chroma fallback), upscale (GAN any-worker / SeedVR2 via `seedvr2`
  cap), Generate All orchestrator w/ per-stage checkpoints, preflight validator, prose→tags +
  clone-from-image wizards. Deep audit (v1.29.1): reports in `diagnostics/audit_studio_*.md`;
  key invariant fixed there: **any file copied into a project as a character image MUST get an
  Asset row** or generation attaches zero refs.
- **P2.1 (SHIPPED 1.30.1–1.33.0):** post-first-test fixes + real Klein pose transfer.
  Base-render controls (per-render model dropdown, lightbox, upload-image-as-base, live status +
  reliable preview refresh); selectable **art style** per character/story (anime/realistic/3D/etc.,
  threaded into base prompt + wizard + captions); tag-sheet auto-fill on the edit page + **clone
  from reference image**; click-to-lightbox on every studio thumbnail; graceful failure + Qwen
  guidance on Klein poses/emotions; **anime face heuristic** fallback for Klein emotions;
  identity-lock prompts (reduce eye-color/identity drift); **pose library import** (VNCCS poseset
  + OpenPose keypoint files → categorized presets); and the **Klein RefControl Pose LoRA** path so
  Klein does true pose transfer without a VNCCS worker. Also fixed: pose/costume `width=0` dispatch
  refusal, PATCH-character wiping fields, emotions-from-costume source lookup, probe-warning noise.

- **P3 (open):** direct trainer execution (ai-toolkit/kohya launch from the app), LTX IC-LoRA
  motion/turntable datasets, 3D pose editor (2D shipped), FaceDetailer graph auto-adaptation to
  Impact-Pack version drift.

## Engines & worker capabilities (quick reference)

| Stage | klein (any worker) | qwen (`vnccs` cap) | other |
|---|---|---|---|
| Base render | first-pass gen (Z-Image/Krea2) | — | |
| Shots | klein_1ref edits | — | |
| Poses | klein_2ref + OpenPose skeleton + **RefControl Pose LoRA** (`refcontrol_v2_poses.safetensors`) | studio_qie_edit + PoseStudio LoRA | |
| Costumes | klein_1ref | studio_qie_edit + ClothesCore | |
| Emotions | klein_inpaint + CPU face mask | studio_qie_edit + EmotionCore | studio_facedetailer (`impact`+`vnccs`) |
| Cutout | CPU rembg/chroma | studio_rmbg2 | |
| Upscale | — | — | studio_upscale (`upscale`) / studio_seedvr2 (`seedvr2`) |

Capabilities auto-detect from each worker's node list on connect (VNCCS_QWEN_Encoder→vnccs,
FaceDetailer→impact, SeedVR2VideoUpscaler→seedvr2). Default QIE GGUF filename in the graphs:
`qwen-image-edit-2511-Q5_0.gguf` (override per job via `qie_model_gguf`).

## Pose control images (1.33.0)

Pose CONTROL images (fed to the model) render as the standard **colored OpenPose skeleton on
black** — what both the VNCCS QIE PoseStudio LoRA and the Klein RefControl Pose LoRA consume.
The **mannequin schematic** (peach body ovals on gray) is now used only for the browsable library
thumbnails. `render_pose(..., style="openpose"|"mannequin")` in `pose_renderer.py`.

Pose-file ingestion (`pose_renderer.openpose_*`): OpenPose keypoint JSON (`pose_keypoints_2d`,
auto-detects **BODY_25** vs **COCO-18**) is remapped to the VNCCS 18-joint schema and scaled into
the 512×1536 canvas (aspect preserved, centered). Endpoints: `POST /pose-presets/import` (VNCCS
poseset JSON) and `POST /pose-presets/import-openpose` (multipart: one `.json`, an array, or a
`.zip` of thousands). Both land as categorized custom presets; the Poses tab has Import buttons +
a category filter.

## Klein RefControl Pose LoRA path (1.33.0)

Klein poses now do real pose transfer via `thedeoxen/refcontrol-FLUX.2-klein-9B-reference-pose-lora`:
- Image 1 = OpenPose skeleton (control), Image 2 = identity reference (order swapped vs the old weak
  path). Trigger prompt: `apply pose from image 1 with reference from image 2`.
- The LoRA is enabled in the Klein workflow's existing rgthree **Power Lora Loader** node (`lora_1`
  slot) at dispatch time via `prepare_klein_workflow(pose_lora=..., pose_lora_strength=0.9)`.
- Filename comes from `app_settings.cs_klein_pose_lora` (default `refcontrol_v2_poses.safetensors`;
  set empty to disable → falls back to the weak identity-first 2-ref path).

## Requirements checklist — what each capability needs

App-side (already in deps): `opencv-python-headless` (pose render + face detect), YuNet ONNX
auto-downloads on first emotion run (Haar cascade fallback ships with opencv; anime falls back to a
heuristic face box).

Ollama (Settings → LLM / Vision — talks HTTP, NOT ComfyUI):
- **Text model** (`ollama_model`) — required for the Wizard / clone tag-sheet extraction.
- **Vision model** (`ollama_vision_model`) — required for dataset captioning + clone-from-image.

ComfyUI workers:
- **Klein base render / shots / costumes / poses / emotions** — any Klein worker (`klein` cap). ✅ works today.
- **Klein pose transfer** — needs `refcontrol_v2_poses.safetensors` in each Klein worker's
  `models/loras/` (FLUX.2 Klein Base 9B recommended; runs on distilled at lower fidelity). ✅ installed.
- **Qwen engine** (best pose/costume/emotion quality, no face-detect needed) — a VNCCS-equipped
  ComfyUI advertising `vnccs`: the `VNCCS_QWEN_Encoder` node, the QIE-2511 GGUF
  (`qwen-image-edit-2511-Q5_0.gguf` default, override via `qie_model_gguf`), and the three task LoRAs
  `VNCCS_QIE2511_PoseStudio_ART_V5.9.5`, `VNCCS_QIE2511_ClothesCore-RC3.x`,
  `VNCCS_QIE2511_EmotionCore-RC1`. Not present on the current worker pool.
- **FaceDetailer emotion engine** — Impact-Pack + Impact-Subpack (`impact` cap) **plus** the VNCCS
  models, and `bbox/face_yolov8m.pt` + `sam_vit_b_01ec64.pth`.
- **Premium upscale** — a SeedVR2 upscaler node (`SeedVR2VideoUpscaler` → `seedvr2` cap). Else GAN
  upscale on any `upscale`-capable worker.
- **Worker-side cutout** — an RMBG2 node; otherwise the app runs CPU rembg/chroma locally.

Caps auto-detect from each worker's node list on connect (VNCCS_QWEN_Encoder→vnccs,
FaceDetailer→impact, SeedVR2VideoUpscaler→seedvr2). The Studio detail header shows a live
engine-availability badge (Klein ✓ / Qwen / SeedVR2 / FaceDetailer).
