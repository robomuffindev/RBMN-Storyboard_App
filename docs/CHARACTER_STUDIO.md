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

## Phases (status as of v1.30.0, 2026-07-06)

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
- **P3 (open):** direct trainer execution (ai-toolkit/kohya launch from the app), LTX IC-LoRA
  motion/turntable datasets, 3D pose editor (2D shipped), FaceDetailer graph auto-adaptation to
  Impact-Pack version drift.

## Engines & worker capabilities (quick reference)

| Stage | klein (any worker) | qwen (`vnccs` cap) | other |
|---|---|---|---|
| Base render | first-pass gen (Z-Image/Krea2) | — | |
| Shots | klein_1ref edits | — | |
| Poses | klein_2ref + skeleton ref | studio_qie_edit + PoseStudio LoRA | |
| Costumes | klein_1ref | studio_qie_edit + ClothesCore | |
| Emotions | klein_inpaint + CPU face mask | studio_qie_edit + EmotionCore | studio_facedetailer (`impact`+`vnccs`) |
| Cutout | CPU rembg/chroma | studio_rmbg2 | |
| Upscale | — | — | studio_upscale (`upscale`) / studio_seedvr2 (`seedvr2`) |

Capabilities auto-detect from each worker's node list on connect (VNCCS_QWEN_Encoder→vnccs,
FaceDetailer→impact, SeedVR2VideoUpscaler→seedvr2). Default QIE GGUF filename in the graphs:
`qwen-image-edit-2511-Q5_0.gguf` (override per job via `qie_model_gguf`).
