# Engine-Based Character Studio — Feature Inventory

Reference list for building new VNCCS-Native-derived modes (Klein Hybrid and
beyond). This is everything the ORIGINAL engine-based Character Studio method
(v1.27.0 → v1.49.0, the `/studio` editor) was planned to do and did do — so new
modes can pick these up deliberately instead of rediscovering them.

Status legend: ✅ built (in the old editor, untested-to-lightly-tested),
📋 planned but never built (P3).

---

## 1. What the engine-based method was designed to do

### Organization & data model
- ✅ Stories → characters organization for series reuse; per-story default art style.
- ✅ Character "tag sheet" (VNCCS-style character_info) as the single source of truth.
- ✅ Character kinds: **person AND item/prop** (item mode had its own 11-shot plan).
- ✅ All generation through the normal app job queue (hidden system project,
  one scene per character) — progress, retries, and queue tooling for free.
- ✅ App-side state: every image is an app Asset from birth (nothing lives only
  on a worker's disk).

### Base image
- ✅ Multi-model base render: **z_image / Krea2 / Anima / Klein** per render
  (dropdown), first-pass redirect rules per model.
- ✅ **Art-style registry**: anime / semi-realistic / photorealistic / 3d_render /
  comic / storybook / custom-verbatim — threads into base prompt AND dataset captions.
- ✅ Upload-your-own image as base; **Restyle** (re-render base in another style);
  **Custom Base Advanced** (custom prompt + reference images + LLM instruction +
  model choice + control image).
- ✅ Base versions with set-active.

### Poses
- ✅ 2D pose editor (drag joints, SVG, 18-joint OpenPose skeleton).
- ✅ Pose imports: VNCCS poseset JSON, raw OpenPose JSON (BODY_25/COCO-18
  auto-detected), and **direct PNG OpenPose control images** (bulk zip import —
  built for "thousands of pose PNGs").
- ✅ Pose render at target resolution with fit/centering; exact VNCCS skeleton
  color/thickness parity.
- ✅ **Klein RefControl Pose LoRA** path (refs=[pose, identity], LoRA slot flip)
  alongside the Qwen/QIE PoseStudio-LoRA path.
- ✅ Tools→Studio bridge: pick poses from the app-wide Pose Library.

### Costumes
- ✅ Costume CRUD + generate via ClothesCore (Qwen) or Klein 2-ref edit.
- ✅ Outfit catalog (629 aesthetics) as a searchable datalist feeding the prompt.

### Emotions / expressions
- ✅ 157-emotion catalog + **custom expressions** from the Expression Library.
- ✅ THREE engines per emotion run: Qwen QIE EmotionCore, Klein face-masked
  inpaint (YuNet/Haar CPU face detect → RGBA mask), FaceDetailer (VNCCS-exact
  params) — per-tab engine override with availability badges.
- ✅ Face crops extracted per emotion (for datasets).

### Post-processing
- ✅ Background cutout: RMBG2 (worker) with rembg/chroma-key local fallback.
- ✅ Upscale: GAN (any worker) or **SeedVR2 premium** with auto fallback
  (auto → seedvr2-if-online-else-gan) usable on ANY image as a step.

### Orchestration & safety
- ✅ **Generate-All orchestrator**: base → poses → costumes → emotions →
  process, per-stage checkpoints in the manifest, skip-and-record errors.
- ✅ Pre-flight validators (worker caps, models present, base exists) with
  actionable warnings before a run.
- ✅ Per-character **SFW/NSFW** toggle (+ Krea2 SFW-mode override parity).
- ✅ Identity-lock prompt suffix on pose/costume/emotion/shot edits
  (eye-color/identity drift control).

### Wizards
- ✅ Character Wizard (plain words → tag sheet, text LLM).
- ✅ Clone-from-image (vision describe → tag extract, incl. nsfw/aesthetics/skin
  tone) — surfaced on the edit page too.

### THE BIG ONE — LoRA dataset generation (the original reason it exists)
- ✅ Default 15-shot plan (4 full-body angles, 3 portraits, 2 upper, 3
  expressions, action pose, 2 bg/lighting) + 11-shot item plan; ≥30% non-front
  angles rule baked in.
- ✅ Auto-captioning on the Ollama vision pool: booru-tag captions AND
  natural-prose captions, trigger-word-first enforcement, constant-traits
  pruning (traits fused into the trigger are NOT tagged), per-model-family
  quality prefixes (Illustrious / NoobAI / Pony branches).
- ✅ Caption review/edit UI before export.
- ✅ Dual export: **kohya** (N_trigger-class folders + TOML + README) and
  **ai-toolkit** (flat images + config.yaml skeleton + README), zip download.
- ✅ Face-crop gallery feeding datasets.

### Reuse & integration
- ✅ **Push-to-project**: copies base + preferred angle shots into a project's
  cast (settings.characters entry with extra_images multi-angle refs, Asset
  rows created) — the bridge from Studio to actual video projects.
- ✅ Global character library interop.
- ✅ Tools section: Pose Organizer (DWPose scan/extract/commit), Pose Library
  (Klein render/browse/export), Expression Library, Generate Sample
  (multi-model sample generator with isolate directives + commit-to-library).

### 📋 Planned, never built (P3)
- 📋 Direct trainer execution (launch kohya / ai-toolkit from the app).
- 📋 LTX IC-LoRA **motion** datasets (video LoRA data).
- 📋 3D pose editor (2D shipped instead; VNCCS Pose Studio now covers 3D in Native mode).
- 📋 Training handoff / job tracking.

---

## 2. EXTRAS the engine method has over VNCCS Native (gap list for new modes)

These do not exist in VNCCS Native mode today — the checklist of candidates
when you fork Native into a new mode:

1. **LoRA dataset pipeline** — shot plans, vision captioning (booru+natural,
   trigger-first, constant-trait pruning), caption review, kohya + ai-toolkit
   exports. Native generates sprites but has no dataset story.
2. **Model freedom for the base** — z_image/Krea2/Anima/Klein per render,
   Restyle, Custom Base Advanced (refs + LLM instruction + control image),
   upload-as-base. Native's base is whatever checkpoint the VNCCS gen_settings
   select, always through the VNCCS preview/creator path.
3. **Per-stage engine choice** — Qwen vs Klein vs FaceDetailer per pose /
   costume / emotion run with capability badges and auto-fallback. Native is
   locked to the VNCCS meganode pipeline (QIE + task LoRAs).
4. **Art-style registry** threading one style choice through base prompts AND
   captions; per-story default style.
5. **App-side assets from birth** — every render is an Asset immediately;
   Native's sprites live on worker disks and only ingested outputs are assets
   (that worker-locality is what caused the shard/mannequin/reference bugs).
6. **Pose import breadth** — OpenPose JSON (BODY_25/COCO), direct PNG control
   images in bulk, the 2D joint editor, and the Tools Pose Library bridge.
   Native has the node's 12 defaults + HF pose packs + Pose Studio 3D.
7. **Custom expressions** (Expression Library, natural-prompt entries) —
   Native only offers the host's emotion catalog (incl. its custom-emotion
   route, but no app-side library integration).
8. **Post-process on ANYTHING** — cutout/upscale (incl. SeedVR2) as standalone
   steps on any image; Native's upscaler runs only inside generation graphs.
9. **Generate-All orchestrator** with per-stage checkpoints + resumability.
10. **Pre-flight validation** (caps/models/base) before submitting.
11. **Push-to-project cast integration** with curated multi-angle
    extra_images. Native has project LINKING (copy assets) but not the curated
    cast-entry semantics.
12. **SFW/NSFW enforcement across engines** (incl. Krea2 SFW override).
13. **Item/prop mode** (non-character subjects with their own shot plan).
14. **Identity-lock suffix** on all edit prompts.

### For fairness — what Native has that the engine method lacked
(so new modes keep these too): multi-worker fan-out with shard tracking and
progress; base + costume VERSIONING with prompt snapshots; outfit gallery +
import-from-character; staged preview-first flow (cheap single-image audition
at every step); pose-set save/restore; VNCCS Pose Studio 3D; seed control with
true per-run randomization; host LLM wizards with Ollama fallback; self-healing
catalog.

---

*Compiled 2026-07-10 (v1.70.0) from docs/CHARACTER_STUDIO.md,
docs/CHARACTER_STUDIO_P2_API.md, docs/TOOLS.md, the v1.48–1.49 parity audits,
and the session history. The legacy editor code remains at /studio (engine
characters still open there); it is frozen, not fixed, per 2026-07-10 decision.*
