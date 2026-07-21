# VNCCS Native Mode

*Added v1.50.0 (2026-07-09). Phases 1-2 (reuse layer + Creator) shipped; UNTESTED on a live host.*

A Character Studio mode that drives the **real VNCCS character pipeline** on a pinned ComfyUI
worker and catalogs the outputs in our system. It is a **thin app over VNCCS** — every worker and
the Tools box already run the VNCCS + vnccs-utils custom nodes, which register ~80 `/vnccs/*` HTTP
routes and serve their own web UI, so we reuse that instead of reimplementing the pipeline.

It is completely separate from the existing Character-Studio "engine" mode (qwen/klein).

## How it works

1. **Pin a host.** VNCCS stores characters on the local disk of whichever instance runs it, so all
   VNCCS-Native work pins to one host: a URL set in Settings (`studio_vnccs_host`), else the first
   healthy `vnccs`-capable worker.
2. **Interactive data is proxied.** Character/costume/emotion lists, the LLM wizards, the HF pose
   library, previews and model/LoRA lists are served by the host — our backend relays to them
   (`/api/studio/vnccs/*`, whitelisted to `/vnccs/*` paths only).
3. **Generation submits the meganode graph.** We vendor the real VNCCS Step graphs
   (`workflows/vnccs/STEP1_CREATOR|STEP1_CLONER|STEP2_CLOTHES|STEP3_EMOTIONS.json`), convert them to
   ComfyUI `/prompt` API format using the worker's `/object_info` (faithful `graphToPrompt`, no
   guessing at widget names), inject our form values into the `widget_data`/`node_state` JSON, and
   tap the generator's image outputs with `SaveImage` so results return through `/history`.
4. **Results are ingested into our system.** The tapped sheet / per-pose sprites / face crops /
   upscale are downloaded and filed into the hidden Character-Studio project's asset store, and
   recorded on a `StudioCharacter` (link under `manifest["vnccs"]` — no schema change).

## Using it

Character Studio → **✨ VNCCS Native** (or go to `/studio/vnccs`). Six tabs share one
generate → poll → ingest engine:

1. **⚙ Settings** → set the VNCCS host URL (blank = auto-pick a vnccs-capable worker) and, optionally,
   an edit-model override. Save.
2. **Create** — the VNCCS tag-sheet form (name + sex/age/race/skin/hair/eyes/face/body/details/
   aesthetics/nsfw/background) → generate a character.
3. **Cloner** — upload reference photos, name the character → clone from them (emits clothed + nude).
4. **Clothes** — pick an existing character, name a costume, fill the 5 slots (top/bottom/head/face/
   shoes) → re-dress across poses (ClothesCore).
5. **Emotions** — pick a character + costumes, multi-select from the host's emotion catalog → per
   (costume × emotion) FaceDetailer re-render (EmotionCore).
6. **Pose Studio** — a 3D poseable rig (camera / IK-FK / body sliders) → author a pose and save it to
   the shared pose library.
7. **Library** — every ingested VNCCS character; pick a project and **Link** to copy its images in as
   scene-usable CHARACTER references.

Every generation is assembled + submitted to the host, polled live, and the results are filed into
your asset store + cataloged on a `StudioCharacter` (link under `manifest["vnccs"]`, outputs keyed
`"{step}/{label}"` so re-running a step never clobbers a prior one).

## API surface

| Endpoint | Purpose |
|---|---|
| `GET/PUT /api/studio/vnccs/host` | read / pin the host + Control-Center settings |
| `GET /api/studio/vnccs/context-lists` | models/loras/samplers (settings screen) |
| `GET /api/studio/vnccs/{characters,emotions,pose-library}` | host catalogs |
| `ANY /api/studio/vnccs/r/{subpath}` | generic whitelisted relay (JSON/binary) — wizards, sam3d, etc. |
| `POST /api/studio/vnccs/generate/{step}` | assemble + submit a Step graph → `{prompt_id, tap_map}` |
| `GET /api/studio/vnccs/result/{prompt_id}` | poll `/history` → output images |
| `GET /api/studio/vnccs/view` | proxy a generated image from the host |
| `POST /api/studio/vnccs/ingest` | download tapped outputs → asset store + Studio catalog |
| `POST /api/studio/vnccs/upload` | upload a reference image to the host (Cloner) |
| `GET /api/studio/vnccs/catalog` | list ingested VNCCS characters |
| `POST /api/studio/vnccs/link` | copy a character's images into a project as CHARACTER refs |

`step` ∈ `creator | cloner | clothes | emotions`. All are wired end-to-end in the UI: **Create ·
Cloner · Clothes · Emotions · Pose Studio · Library** tabs. The Cloner uploads reference images
(`POST /upload` → host `/upload/image`) and clones from them; the Library lists cataloged characters
(`GET /catalog`) and links them into a project (`POST /link`, copying images as CHARACTER references).

## LLM Wizards (v1.54.0)

Three one-click helpers fill the forms from a plain-language idea, mirroring the
wizards in the VNCCS ComfyUI panel:

- **✨ Character Wizard** (Create tab) — describe the character idea; fills
  sex/age/race/skin/hair/eyes/face/body/details.
- **✨ Clothes Wizard** (Clothes tab) — describe the outfit idea; fills the five
  costume slots (top/bottom/head/face/shoes).
- **🔎 Analyze reference** (Cloner tab) — vision-describes the first uploaded
  reference and fills an editable character-info panel that is sent with the clone
  job (incl. aesthetics + NSFW flag).

Backend routing (`POST /api/studio/vnccs/wizard/{character|clothes|clone-analyze}`):
**host first, Ollama fallback.** The host path relays to the real VNCCS wizard
routes (`/vnccs/character_wizard`, `/vnccs/clothes_wizard`,
`/vnccs/cloner_auto_generate`) — the host loads a Qwen2.5-VL GGUF via
llama-cpp-python per request (first-ever call downloads ~5 GB), so results are
literally identical to the VNCCS panel. If that fails (llama-cpp missing, download
failure, timeout), the app falls back to its own Ollama (`ollama_model` for text,
`ollama_vision_model` for the cloner) using the VERBATIM VNCCS prompts — the
character wizard even pulls the same tag catalog from the host via `/vnccs/get_tags`
— so only the underlying LLM differs. The response reports `source: host|ollama`,
shown in the UI. Pass `backend: "host"|"ollama"` to force a path.

## Generation settings (v1.55.0)

The GUI meganodes (CharacterCreatorV2 / CharacterCloner / ClothesDesigner) declare their
`widget_data` as a HIDDEN input, so the UI→API converter drops it; the assembler now re-seeds
each meganode's original widget_data from the vendored graph before patching, so every step
runs with the graph's WORKING baseline (Creator: anima mode + `anima-base-v1.0` + Qwen
CLIP/VAE + turbo LoRA; Generator: full SeedVR upscaler + BG-remove config). Previously the
creator ran with an empty gen_settings → illustrious mode → "No Checkpoint selected".

Overrides: Settings → "Character generation" (mode anima/illustrious, base model from the
host's lists, steps/cfg/sampler/scheduler, seed — blank seed = randomize each run). Saved
under `studio_vnccs_settings.gen_settings`; merged into the Creator/Cloner widget_data AND
the Emotions node's `generation_settings`. The merge writes both top-level keys and the
active `mode_settings` profile (VNCCS applies the profile LAST, so profile values would
otherwise shadow overrides). The baseline's fixed template seed is reset to 0 (=random)
unless you pin one.

## Headless pose captures (v1.55.1)

`VNCCS_PoseStudio` renders its mannequins in the BROWSER when driven from the ComfyUI panel
(`captured_images` CSR path). Submitted headless, the node falls back to a Python software
renderer with an upstream bug: any pose with a non-zero `modelRotation` (rear/side views)
converts the vertex array to `np.matrix` and crashes with
`could not broadcast input array from shape (19158,3) into shape (19158,)`.

Our assembler now acts like the panel: `vnccs_native/pose_render.py` (a faithful port of the
node's fallback renderer with `np.asarray` guards) pre-renders every pose against the vendored
`vnccs-utils/CharacterData` and injects the PNGs into `pose_data["captured_images"]`, so the
node takes its CSR path and the broken fallback never runs. ~17 s on the first generate
(MakeHuman data load + 12 renders), cached afterwards. Best-effort: if `vnccs-utils/` is not
present next to the app, the graph is submitted unchanged (and the worker's fallback may crash
on rotated poses — the alternative fix is patching `np.asarray` into the worker's
`vnccs-utils/nodes/pose_studio.py` `_apply_pose` model-rotation branch).

**Headroom (v1.199.13):** the capture reserves `_TOP_HEADROOM` (default 14%) of blank space
above the head so hats/tall hair have canvas to render into; overridable per run via
`pose_data['export']['top_headroom']` (clamped 0.0–0.45). Driven by the **Headwear room**
setting — see the addendum below.

## Staged creation flow (v1.56.0)

The Create tab now mirrors the VNCCS panel's intended workflow instead of firing the whole
12-pose pipeline at once:

1. **✨ Generate Character** — renders ONE default-pose image via the host's
   `/vnccs/preview_generate` (the panel's "Generate Preview"): fast, no sprites, no upscale.
   Iterate on the form until the character looks right.
2. **💾 Save** — persists the form (character_info + generation settings) onto our
   StudioCharacter catalog (`manifest["vnccs"]["form"]`); reload any time via Library →
   "Load into Create".
3. **Poses** — the 12 default VNCCS poses are shown with app-rendered thumbnails
   (`GET /api/studio/vnccs/pose-defaults`); toggle any subset, and add extra poses from the
   host Pose Library (full pose data). Selection caps at 16 (node CSR limit).
4. **Generate Poses** — runs the full Step-1 pipeline for exactly the chosen poses
   (`pose_set` on `POST /generate/{step}` replaces the Pose Studio pose list; the app-side
   capture pre-render follows the selection).

Cloner and Clothes reuse the same pose selection; Clothes and Emotions show the character's
existing pose sprites in a preview strip (host `get_character_pose_preview` + `_meta`, with a
costume switcher on Emotions). Emotions offers the host's costume list as checkboxes
(`get_character_costumes`) so all selected emotions render for every selected clothing set.

**Upscaler controls** (Create / Cloner / Clothes): SeedVR (default) / GAN / Off + upscale
resolution + pose target size — sent as `generator_overrides` and MERGED into the generator's
widget_data (`upscaler.mode`, `upscaler.resolution`, `pose_generation.target_size`), so the
rest of the vendored upscaler/BG-remove config is preserved. Off/GAN make runs dramatically
faster than SeedVR. Poll timeout raised to 60 min with elapsed-time status.

## Multi-worker fan-out (v1.58.0)

Repetitive generation is chunked across every reachable vnccs-capable worker
(`GET /api/studio/vnccs/hosts`, pool workers with the `vnccs` capability + the pinned host).
`POST /generate-parallel/{step}` places chunks according to VNCCS's storage locality (sprites
live on the disk of whichever worker runs the graph):

- **creator / cloner** — the selected pose set is split round-robin across all workers; every
  worker that runs a chunk is recorded on the character (`manifest["vnccs"]["hosts"]`).
- **clothes** — chunks go only to recorded hosts (each holds the character's base sprite
  locally); the selected poses are split across them.
- **emotions** — the same request goes to every recorded host; each worker FaceDetails only the
  costume sprites on its own disk, so the work splits naturally.

The UI shows a ⚡ toggle when more than one worker is online (default on), an overall progress
bar (chunks completed), and a per-chunk status row (worker · chunk size · running/filing/done/
error · live image count). Each chunk is ingested into the catalog as it finishes;
`/result/{prompt_id}?host=` and `/view?host=` poll and proxy chunk-specific workers. A worker
that fails to accept a chunk is skipped with a warning; the run continues on the rest.

## Files

- Backend: `backend/services/character_studio/vnccs_native/{client,host,graph,workflows,ingest,catalog}.py`,
  `backend/api/vnccs_native.py`, vendored VNCCS step graphs in `workflows/vnccs/`.
- Frontend: `frontend/src/components/VNCCSNative/{vnccsNativeApi.ts,VNCCSNativePage.tsx,PoseStudio3D.tsx}`;
  vendored pose-studio engine in `frontend/public/vnccs-pose/` (Three.js `PoseViewerCore` + import modules
  + morph worker, patched to fetch `morph_data.bin` through the proxy).

## Status & remaining polish

Feature-complete across all six tabs (Create · Cloner · Clothes · Emotions · Pose Studio · Library),
built + audited (2 parallel read-only audits, fix-wave v1.53.1) but **UNTESTED on a live worker**.

Remaining is polish/verification only, not new features:
- A **live-host smoke test** — especially the Pose Studio, which fetches `morph_data.bin` through the
  proxy and relies on the browser serving the static ES-module/worker files (the one part not
  offline-verifiable).
- **Fuller Pose Studio controls** — a per-joint FK gizmo panel and wiring the vendored
  Mixamo/OpenPose import + SAM3D-from-image modules to buttons.
- Confirming VNCCS's exact emotion-key strings against a live host.

---

## Addendum — 1.54.0 → 1.79.0 (staged flow, parallel fan-out, Klein Hybrid, face consistency)

The mode has evolved substantially since this spec was written; CHANGELOG.md is
authoritative. Summary:

- **1.54.0 LLM wizards** — /wizard/character|clothes|clone-analyze: host-first
  (VNCCS's own Qwen2.5-VL prompts, verbatim) with automatic Ollama fallback.
- **1.56.0+ staged flow** — synchronous base preview (host `preview_generate`)
  → base VERSIONS (`manifest.vnccs.base_versions`, latest = active; pose runs
  link to the ACTIVE version) → pose-subset generation; costume previews +
  costume versions with prompt snapshots; outfit gallery; seed control
  (`_roll_seed` — ComfyUI input-caching means a fixed seed deliberately
  returns cached images instantly).
- **1.58.0+ multi-worker fan-out** — `/generate-parallel/{step}`: poses split
  round-robin across vnccs-capable workers; clothes/emotions go only to hosts
  recorded as holding the character's sprites (`manifest.vnccs.hosts`); cloner
  reference images replicate to every chunk host. Sprite/costume lists are
  PER-WORKER — always union across recorded∩online workers.
- **1.70–1.73** — New/Clone sub-tabs with persisted create_mode + clone blob;
  mode dialog (Native | Klein Hybrid); character/image deletes (optionally
  worker-side); emotion run recipes (↻ reload any past run incl. seed);
  pose-regeneration REPLACES older images of the same pose (1.76.0); the 3D
  Pose Studio tab was retired in favor of a top-level Pose Library tab.
- **1.74.0+ Klein Hybrid** (`/studio/vnccs-klein`, `GenerateIn.engine='klein'`)
  — poses on Klein 9B + `VNCCS_PoseStudioKlein9b_V1` LoRA (dual reference
  latents, app-rendered captures), Klein T2I base preview, clone via native
  multi-ref, face-inpaint emotions. Klein runs do NOT populate the VNCCS
  worker-side character store (Qwen clothes/emotions can't chain off them).
- **1.77–1.79 face-consistency wave** — see docs/KLEIN_MODE_PLAN.md: face-crop
  identity references, crop-and-stitch emotions anchored to the active base
  version, auto PuLID-Flux2, low-denoise FaceDetailer refine, unique per-chunk
  upload names, clone previews (Klein chain / native 1-pose Cloner), mode
  variant labels + routing (`manifest.vnccs.variant`), thumbnails + ★ hero
  picker (`POST /catalog/{id}/hero`), ⚙ Settings "Klein face consistency"
  section, `GET /klein-status` readiness report.

- **1.114-1.125 refbase "base from references" wave** -- the Klein Hybrid clone base
  preview now builds the body from the reference photos (whole-person ReferenceLatentPlus
  channel, no mannequin), with a tunable **Strip release**, a **FaceDetailer refine on
  the base**, an optional **SAM3 article cleanup** (segment leftover jewelry/clothing by
  name -> inpaint to skin; article list auto-fills from clone-analyze), and a post-hoc
  **photoreal Switch Style** that stacks the anime2real-semi LoRA off the rendered active
  base (realism kept OUT of generation for predictability). Full addendum in
  docs/KLEIN_MODE_PLAN.md. UNTESTED-until-live.

## Addendum — Headwear room (v1.199.13 → v1.199.14, user-confirmed)

Tall headwear (stovepipe hats, feathered headdresses, tall hair) could clip at the top edge
in the Qwen (VNCCS-replica) studio. Root cause: the QIE dress/pose step reproduces the
reference image (image1) at denoise=1, so the hat grows UPWARD from where the head sits —
the blank space above the head is a hard ceiling on hat height. (Real VNCCS clips
big-enough headwear too; this is a bounded "within reason" limit, not a pure bug.)

The **Headwear room** control (Qwen studio, next to Reference strength) reserves that space
per costume: **14% (default) / 22% tall hat / 28% stovepipe / 34% headdress**. Persists as
the `qwen_headwear_room` setting; higher = more room above the head, figure sits a little
smaller in frame. Set above ~8% it exceeds VNCCS's own fixed `computeModelFitZoom(margin=0.08)`.
Two levers, because the hat appears at two stages:

- **Pose captures (Pass B)** — `pose_render.render_pose_captures` honors a per-run
  `pose_data['export']['top_headroom']` (fallback `_TOP_HEADROOM` 0.14, clamped 0.0–0.45),
  applied to the uniform fill scale + the figure down-bias. Set from the setting in
  `_qwen_submit` (pose set) and `create_qwen_clone_preview` (clone base).
- **Costume preview / dress (Pass A)** — `clothes_qwen_preview` dresses the EXISTING base and
  never calls the capture renderer, so it has its own lever:
  `qwen_clothes.pad_base_to_headroom(data, target)` measures the base figure's top margin and
  pads blank space above it (base's own corner colour, so it still keys out) up to the target
  — only ADDS when short, never crops/distorts, idempotent. NOTE: figure detection MUST use
  float32; int16 overflows on a green background (225² > 32767) and silently no-ops.

Scope: clone base, all pose sets, and the costume preview/dress. NOT yet the brand-new
(non-clone) t2i character base (`create_qwen_preview`), which frames from its text prompt
rather than a mannequin capture — extend with `pad_base_to_headroom` if a fresh base ever
needs tall hats. Backend change → `run.bat` restart; frontend → refresh.

## Addendum — Emotions engine, Qwen queue, live results, run persistence (v1.199.15 → v1.199.22)

**Emotions tab now has a 🧪 Klein / 🟣 Qwen engine toggle** (`vnccs_emotions_engine`), mirroring
the Clothes tab.
- **Qwen emotions** = an app-side replica of VNCCS's `VNCCS_QWEN_Detailer`
  "QwenDetailer_ChangeEmotion" workflow (`vnccs-utils/workflows/`): Ultralytics face bbox →
  QIE face edit ("Change emotion to X") → stitch, on our standard Qwen loaders. Builder:
  `qwen_clothes.build_qwen_emotion_graph`. EmotionCore LoRA optional/off by default
  (`qwen_emotion_lora_on`). The installed node's `color_match_method` must be `kornia_reinhard`
  (the example workflow's `mvgd` is a newer build).
- **Klein emotions** = the existing crop-and-stitch face inpaint (`build_klein_emotion_graph`).

**Set/base selector:** sourced from the APP CATALOG (`getCharacterImages.costumes`) + a
selectable **Base**, NOT the worker `get_character_costumes` (which is empty for app-catalog
characters). The character-preview strip uses `CatalogPoseStrip` (catalog sprites), not the
worker-query `MannequinStrip`.

**Engine-tagging:** `ingest.py` tags every output asset and each base/costume VERSION with the
engine that produced it (normalized klein|qwen; untagged legacy). Pickers filter by the active
toggle — matching + untagged legacy (untagged shows under BOTH engines). `get_character_images`
exposes `engine` per output.

**Qwen emotions run through the Generation QUEUE** (`workflow_type: studio_pose_qwen`): cancel +
retry + worker threading like native/Klein. `generate_queue` chunks sprites (`_qwen_emotion_workitems`)
into jobs; the dispatcher's `_process_studio_pose_job` `studio_pose_qwen` branch builds+submits via
`_qwen_emotion_submit_one` and reuses the generic monitor + `ingest_result` + cancel. One sprite
per job (`qwen_emotions_per_job` default 1) so results appear fast. (Qwen pose sets/clothes still
use the direct path — routing them through the queue too is a pending follow-up.)

**Live results + run persistence:** the emotion results gallery renders DURING a run (not gated on
`!busy`), so images stream in per completed job. Run status persists PER CHARACTER in localStorage
(a map keyed by character, not one key) and re-attaches for whichever character you view — on tab/
character change and after a browser close — with a supersede token so switching stops the old poll
loop. Queue across characters, walk away, and return to live status.
