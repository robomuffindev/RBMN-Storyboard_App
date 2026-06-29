# Changelog

## [1.18.0] - 2026-06-28

### Added — Editable Prompt tab (full manual control)

The scene Prompt tab is now an editor, not just a read-only view — for scenes that are hard to represent visually you can hand-write every prompt.

- **Editable First Frame / Last Frame / Video prompts** (plus the two-pass Pass 1 / Pass 2 prompts when present), each with its own **Save** button. Edited fields highlight amber and show "unsaved" until saved; Save persists via the coherent scene-update path (backend + React Query cache + store).
- **Import** button: load a JSON made outside the app to populate the fields. Accepts a flat shape (`{first_frame_prompt, last_frame_prompt, video_prompt, two_pass_scene_prompt, two_pass_composite_prompt}`) or the **Download Prompts JSON** export shape (`{first_frame:{prompt}, …}`). Imported values fill the fields for review, then you Save each.
- The exact strings sent to ComfyUI (Final Submitted Image/Last-Frame/Video, Two-Pass Original) remain below as **read-only** diagnostics under a "What was actually sent to ComfyUI" divider.


## [1.17.1] - 2026-06-28

### Changed — First/last-frame prompts tuned for LTX 2.3 image-to-video

Per LTX 2.3's own image-to-video guidance, the source (first frame) image should show the scene's STARTING moment — the video step animates the motion from it — and an overloaded first frame produces busier, worse video. Our first-frame prompts were cramming the full scene/action into the still.

- **First frame = opening moment.** For animated scenes (music_video / narration_video), the enhance context now instructs the model to depict the calm starting state — the key subject(s), setting, and lighting as the shot opens, BEFORE the action — and NOT to pack in every action/character/element the video will reveal (the video prompt handles those). Applies to both auto-gen and manual Enhance, across all first-pass generators (Z-Image / Krea 2 / Klein). Standalone stills (narration_images) keep depicting the full scene.
- Softened the shared image prompt's "all actions MUST appear" rule and added the video-first-frame role to `IMAGE_SYSTEM_PROMPT`, `Z_IMAGE_SYSTEM_PROMPT`, and `KREA2_IMAGE_SYSTEM_PROMPT`.
- **Last frame = clean end keyframe.** `LAST_FRAME_IMAGE_SYSTEM_PROMPT` now notes the last frame is the keyframe the video resolves to — depict one clean endpoint, not a packed montage; keep it as uncluttered as the first frame, with the video prompt carrying the motion in between.


## [1.17.0] - 2026-06-28

### Added — Ideogram structured-prompt improvements + prompt-tab tooling

- **Enhance builds the structured caption.** When Ideogram (structured-JSON) mode is on for a Krea 2 scene, the main image **Enhance** now also builds/refreshes the structured caption from the freshly enhanced prose — one click gives you the curated, positioned prompt, ready to hand-edit in the JSON Prompt editor. (Previously Enhance only produced prose; the caption was built separately or at render.)
- **References carry their Ideogram layout.** A generated image's structured caption is now stored on the asset (`meta.ideogram_caption`). When that image is later used as a reference, its authored element **layout/positioning** is fed into the prompt context — combined with the vision-model description — so the model respects the fuller composition, not just a generic description.
- **Reference vision-scan audit panel** on the Image tab: each reference shows a clickable thumbnail of the exact image scanned next to the vision model's description (and an "ideogram" badge when the image was composed with structured prompting), so you can audit what the vision model sees.
- **"Download Prompts JSON"** button on the Image tab: exports a troubleshooting JSON of the scene's first-frame / last-frame / video prompts, the exact strings submitted to ComfyUI, models, resolution, seed, and resolved references. Ideogram-mode frames include the **full structured caption** (the actual positioned layout sent to ComfyUI), clearly marked — not just prose. New endpoint: `GET /generate/prompts-export`.

### Fixed

- Restored a pre-existing truncation in `pyproject.toml` (the `[tool.setuptools.packages.find]` section and ruff `select` line had been cut off by an earlier edit).


## [1.16.0] - 2026-06-28

### Changed — Last Frame generation is now cast-aware (introduce characters at the end)

Reworked how the Last Frame (FF/LF mode) image is generated so it respects who is actually in the scene and can introduce a character who was not in the first frame (e.g. a second character enters by the end), giving the video model a real reference instead of an invented look.

- **Scene-aware LF references.** Auto-gen now attaches the Last-Frame tab's selected character reference images (or, when nothing is picked, the characters the story flow names) — previously it attached only "extras", so the video model hallucinated any character that entered at the end.
- **Explicit cast in the prompt context.** The LF enhancer is now told exactly which characters are present at the end, that no one else is in frame, and which character ENTERS who was not in the first frame — and it only claims reference images are attached when they actually are. The First Frame prompt is passed in explicitly for continuity.
- **System prompt allows cast changes.** `LAST_FRAME_IMAGE_SYSTEM_PROMPT` no longer forces "keep all characters identical to the first frame"; a character may exit or a referenced character may enter by the end, but the model must never invent anyone not in the cast list.
- **First Frame attached by default.** The chosen first-frame image is now used as a Klein reference (slot 1) for the last frame by default for tight continuity (dispatch-time, resolved when the FF is ready). The per-scene "Don't reference first frame image" toggle now defaults OFF (attach); flip it on for a freer end-point.

### Added — Vision-model activity indicator

The reference-image vision model (Ollama) was already wired into every enhance path but had no visible signal. Now there is one:

- A live **vision activity tracker** per project (count of reference images described + cache hits, the model, and a last-activity message), exposed at `GET /generate/vision-activity` and merged into the sequential auto-gen status.
- INFO logging whenever the vision model describes a reference image.
- A small **eye badge** on the running auto-gen button showing how many reference images the vision model has described this run (hover for the model + last message).


## [1.15.0] - 2026-06-28

### Added — Krea 2 "Ultra" V2 workflows + SFW/NSFW mode

Replaced the Krea 2 Turbo workflows with the tuned V2 ("Ultra") graphs and added an SFW/NSFW switch.

- **Four workflow files** now ship in `workflows/`: `KREA2_TURBO_T2I.json` (SFW) / `KREA2_TURBO_T2I_NSFW.json` and `KREA2_IDEOGRAM_T2I.json` (SFW) / `KREA2_IDEOGRAM_T2I_NSFW.json`. The V2 graphs drop the separate `ConditioningKrea2Rebalance` node, route the prompt through `RBG_Smart_Seed_Variance` directly, and sharpen at 0.75.
- **NSFW variants** insert the `ComfyUI-Krea2T-Enhancer` node (`capitan01R`) on the model path (`enabled: true`, `strength: 1.0`), which patches the Krea2 text-fusion path and bypasses the model's built-in safety checker. SFW variants omit the node entirely (safety checker active).
- **Settings → Single Image Generator → Krea 2**: new **"SFW mode (model safety checker on)"** toggle (default ON). When OFF, the dispatcher loads the NSFW workflow for both plain and Ideogram modes, falling back to the SFW file if the NSFW one is missing.
- New `krea2_sfw_mode` app setting (DB column + migration + API schemas).

### Fixed

- `prepare_krea2_ideogram_workflow` now sets the `EmptyLatentImage` width/height (the actual render resolution) so Ideogram-mode renders follow the scene resolution instead of being pinned at the workflow's baked 1920×1080.

### Notes

- The V2 install `.bat` also offers a `krea2_turbo_lora_rank_64_bf16` LoRA; per the user it is intentionally **not** used here (the Power Lora Loader stays empty).
- The `KREA2V2/` source-export folder is gitignored.


## [1.14.2] - 2026-06-28

### Fixed — AAF parser validated against a real ElevenLabs export

Tested the importer against a real ~238MB ElevenLabs Dubbing Studio AAF and fixed two issues it surfaced (the parser now produces 377 clean, contiguous scenes from that file):

- **Composition discovery** — pyaaf2's `content.toplevel()` returns *nothing* for ElevenLabs AAFs, so the parser now falls back to scanning all mobs for the `CompositionMob` (then any mob with a Sound sequence). Without this, real imports failed with "no top-level composition".
- **Scene names** — ElevenLabs AAF clips are named generically ("Render") and the track name is uniform, so scenes now fall back to clean "Scene 1…N" names instead of repeating a meaningless label. (The dialogue text lives in ElevenLabs' separate CSV export, not the AAF.)
- Added `sampleaaf/` and `*.aaf` to `.gitignore` so large/personal AAF files are never committed.


## [1.14.1] - 2026-06-28

### Fixed — AAF import + manual scene editing (post-audit hardening)

An independent audit of 1.14.0 found no blockers; these robustness fixes were applied:

- **Manually-added / split scenes are now bound to a chapter.** `create_scene` assigns the new scene to the chapter whose time range contains it (deepest match, else the last chapter) and extends that chapter — so manual add and split no longer leave a scene with `chapter_id=NULL` that chapter-scoped Auto-Gen / Export / Story Flow would skip. Add/Split also refresh the Chapters view.
- **New scenes are clamped to the audio length.** `create_scene` now clamps a scene's end to the master audio's duration (in addition to the min/max bounds), preventing a manually-added scene from extending past the audio and slicing a silent tail.
- **AAF import no longer leaks the old audio file.** When you upload replacement audio during import, the previous music file is removed from disk (not just its DB row).
- **AAF import validates the AAF first** (before any scene/audio change) and **surfaces a clear warning** if chapters couldn't be rebuilt (so you can run "Re-derive Chapters").


## [1.14.0] - 2026-06-27

### Added — Import ElevenLabs AAF timeline + manual timeline editing

**AAF import.** The project 3-dots menu has a new **Import AAF (ElevenLabs)** option. It parses an ElevenLabs Dubbing Studio AAF (binary, via `pyaaf2`) into scene boundaries and **replaces** the project's scenes with that timeline, then slices audio per scene and rebuilds chapters — mirroring Suggest Timeline. The dialog lets you **use the project's existing audio or upload a new file** (sliced to the new boundaries). Clip cut points become scene cuts; clip names become scene names. (Requires `pyaaf2` in the backend env; a clear message tells you to `pip install pyaaf2` if it's missing.)

**Manual timeline editing (power users).** You can now build/adjust timelines by hand without the auto flows:

- **Add Scene** button in the timeline toolbar — appends a new blank scene.
- **Numeric Start/End entry** per scene (Scene → Tools tab) — type exact times; the scene's audio re-slices to match.
- Plus the existing **Split at playhead**, **Delete** (with merge), and **drag scene boundaries** — all confirmed working and unchanged.

None of this affects the existing auto pipeline (audio analysis, SRT/lyrics, Suggest Timeline) — they remain the default. See `docs/TIMELINE_EDITING.md`.


## [1.13.0] - 2026-06-27

### Added — Klein inpaint (mask-paint editing of rendered images)

Review a generated image and fix/replace part of it by painting a mask — like ComfyUI's mask editor, right in the app.

- **Inpaint button** in the image lightbox (where you review a generated image full-size). Opens a full **InpaintModal**.
- **Mask painting** over the displayed image: brush (size slider), eraser, clear. The mask is baked into the source's alpha channel (ComfyUI clipspace convention) at full resolution.
- **Prompt** for what should appear in the masked area.
- **Reference (optional)** to place a specific object/character into the masked area: **upload** an image, pick from **project assets**, or pick from your **characters list** — and optionally **crop a region** of the reference to use just a part of it. With no reference, it inpaints from the source image + prompt alone.
- **Result** comes back as a new image **version** on the scene; review it and **Save as scene preview**, or inpaint again.
- **Backend**: `workflows/KLEIN_INPAINT.json` (FLUX.2 Klein), `prepare_klein_inpaint_workflow`, a `klein_inpaint` dispatch route (source + reference uploaded as LoadImage files; result composited back over only the masked region), and a `POST /generate/inpaint` endpoint.


## [1.12.5] - 2026-06-27

### Fixed — First-pass image prompts optimized per model (no more blown-out fluff)

Researched each model's official + community prompting guidance and reworked our LLM system prompts so each one is written for the model that actually renders — concise, and free of the quality-booster spam that was causing the blown-out look.

- **New `Z_IMAGE_SYSTEM_PROMPT`** (Tongyi Z-Image Turbo): structured camera-direction prose, no reference language, "negatives" written positively, motivated lighting — and an explicit ban on booster terms (`masterpiece/8k/HDR/ultra-contrast`), which on Z-Image directly cause highlight clipping/oversaturation. **Fixes a real bug**: no-reference Z-Image renders were being enhanced with the *Klein reference* prompt.
- **New `QWEN_EDIT_SYSTEM_PROMPT`** (Qwen-Image-Edit): imperative edit instructions, `image 1/2/3` roles, quoted literal text.
- **`IMAGE_SYSTEM_PROMPT` (Klein / FLUX.1) de-fluffed**: ~30–90 words, edit-instruction phrasing with `image 1/2` references (say what *changes/combines*, don't re-describe), graceful no-reference handling, no boosters/weight-syntax, no character names, lighting-first.
- **Krea 2 / two-pass-base / last-frame / narration** prompts tightened (word counts trimmed, booster spam banned). Krea 2 in particular was trained to *remove* the AI look, so booster words are counter-productive.
- **Routing fix**: the manual Enhance now picks the prompt by what will render — no references → first-pass generator (Z-Image/Krea 2); with references → Klein. (Auto-gen uses the shared, now-graceful Klein image prompt; per-model auto-gen routing is a documented follow-up.)

See `docs/MODEL_PROMPTING.md` for the per-model rules + sources.


## [1.12.4] - 2026-06-27

### Fixed — Klein two-pass (Pass 2) prompts: edit instructions, not T2I descriptions

The Pass-2 (Klein composite) LLM prompt was being written like a from-scratch T2I description — long, blown-out paragraphs that re-described everything Klein already sees in the reference images, and that echoed character **names** the edit model can't possibly use. Reworked to treat Klein as the **edit model it is**:

- The Pass-2 system prompt now asks for a **short edit instruction** (~20–60 words): Image 1 = the finished base scene (keep its exact lighting/exposure/palette, don't darken or restyle), Image 2+ = the character(s) to insert. It explicitly forbids re-describing what the images already show.
- **No more character names.** The dispatcher no longer feeds character *names* into the composite context — characters are referenced only as "Image 2", "Image 3", … by appearance. A hard "never use a name / proper noun" rule is in the system prompt too (names are wasted, misleading tokens for an image model).
- The composite is now **seeded with a concise edit instruction** instead of the entire base-scene prose, so the LLM stops re-describing the environment.
- Single-pass Klein image prompts also stop emitting character names (reference subjects by image position instead).


## [1.12.3] - 2026-06-27

### Added — Player: follow-playback scene selection + prev/next scene

- The main stage now **selects the scene under the playhead while playing**, so if you spot a problem you can just pause and that scene is already open in the editor to fix — no clicking around the timeline.
- Added **Previous scene** / **Next scene** buttons to the main player controls (alongside play/seek/fullscreen). Previous jumps to the current scene's start when you're more than ~0.5s in, otherwise to the previous scene; Next jumps to the next scene's start. Both also select the target scene. (The Timeline toolbar already had equivalent prev/next-section skip buttons; this brings them to the player bar.)


## [1.12.2] - 2026-06-27

### Added — Main stage: full-screen toggle + player controls

The main preview stage (the canvas at the top-centre that plays the timeline) now has its own controls overlay: a **play/pause** button, a **seek** scrubber, current/total **time**, and a **full-screen toggle**. Controls fade in on hover (and stay visible while paused or in full-screen). Full-screen uses the browser Fullscreen API on the stage; everything stays wired to the same timeline playback state, so it's in sync with the timeline transport.


## [1.12.1] - 2026-06-27

### Added — LTX Director: Retake/editing + High-Quality two-stage

- **Retake / edit an existing clip** — in the Director editor, enable Retake to re-generate a chosen span (start + length) of an existing video with a new prompt and strength, keeping the rest. Source video = this scene's current video, or pick/upload one. Wires the node's `retakeMode`/`retakeVideo`/`retakeStart`/`retakeLength`/`retakePrompt`/`retakeStrength` and flips `LTXDirectorGuide.retake_mode`; the source video is uploaded with the timeline files.
- **Quality toggle** — Standard (single-stage, fast) vs **High (2× upscale)**, a two-stage workflow (`LTX_DIRECTOR_HQ.json`) that adds an `LTXVLatentUpsampler` 2× spatial upscale + refine pass and tiled VAE decode (sharper, low-VRAM friendly). Selected per scene; the dispatcher routes to the HQ workflow when set (falls back to single-stage if the file is absent).

### Fixed (LTX Director audit)

- Motion-track guides now resolve their `asset_id` to a file (`videoFile`/`imageFile`) so they're uploaded and actually reach the node.
- Fixed an autosave feedback loop in the Director editor (debounced save no longer re-fires on parent re-render).
- "Auto-size from keyframes" is now honored — the editor defaults to pinned project dims, and an explicit auto (0) is passed through instead of always falling back to project dims.
- Project **text export/import now preserves advanced per-scene config** — Director Mode timelines, LLM instructions, and vision / JSON-prompt toggles survive a round-trip (carried under a per-scene `advanced_params` block). Previously these were silently dropped.

### Notes

- Auto-gen / batch video generation intentionally ignores Director Mode and produces a standard LTX video; regenerating a Director-enabled scene via auto-gen overwrites its result (the saved Director config is preserved, just not used by auto-gen).


## [1.12.0] - 2026-06-27

### Added — LTX Director Mode (per-scene timeline editor)

A full-screen timeline editor on the Video tab that drives the v2.0.0 LTXDirector ComfyUI node, grafted onto our existing LTX stack (GGUF unet + distilled LoRA + gemma DualCLIP + KJ VAEs + VHS output). Enable it per scene and it replaces the normal video options with direct timeline control.

- **Video tab toggle** "Enable LTX Director Mode" — greys out the normal video controls and reveals an "Open Director Timeline" button + an inline Generate.
- **Full-screen editor** (`LTXDirectorModal`): zoomable timeline with frames/seconds display and three lanes —
  - **Prompt Relay** — time-segmented prompts (draggable/resizable blocks); each conditions its own span of the clip while a **global prompt** anchors what's constant. Per-segment epsilon transition control.
  - **Keyframes** — image guides from project assets, uploads, or the **previous scene's last frame** (one-click "Continue from previous scene"); each pinned at a frame with a strength slider; drag to reposition.
  - **Audio** — defaults to the scene's audio (conditioning / lip-sync), overridable by picking or uploading an audio asset; or let LTX generate its own.
  - **Motion track** (advanced) + output controls (pin size / resize method / keyframe CRF).
- **Saves on every edit** to `scene.parameters.ltx_director` (reopen any time); **Generate** enqueues an `ltx_director` video job to the batch like normal.
- **Backend:** new `ltx_director` workflow_type → `workflows/LTX_DIRECTOR.json` (validated API export on our stack), `prepare_ltx_director_workflow`, dispatch route that builds `timeline_data` + Prompt-Relay strings from the scene config and resolves keyframe/audio assets, plus a timeline-file uploader. Gated on the workflow file existing.


## [1.11.0] - 2026-06-21

### Added — Vision model (Ollama) to describe reference images for the prompt LLM

Reference images now get described by a local vision model and the description is fed to the prompt-enhancer LLM, so it understands what a reference image actually shows — more reliable than the source prompt alone, and the only signal for images imported from outside the app.

- **Settings → LLM:** a new Vision section under the existing Ollama config (reuses the same Ollama server pool). Global **"Enable Vision Descriptions for Reference Images"** toggle + a **Vision Model** selector with a Refresh button (lists models from the Ollama pool via `/settings/ollama/vision/models`). Recommended **qwen2.5vl:7b** (best caption accuracy; faster: qwen2.5vl:3b / moondream; higher quality: llama3.2-vision:11b).
- **Per-image override** on each Image tab (Project default / On / Off), shown when a vision model is configured. Saved on the scene and overrides the global setting.
- **Auto-gen** honors it: the image and video auto-gen enhance contexts describe the scene's selected reference images. A toggle is also surfaced in the Auto-Gen panel's advanced options.
- The description is **cached on the asset** (`asset.meta.vision_description`) so each image is described at most once; the call is a single low-temperature Ollama `/api/chat` request with a tight factual-caption prompt. Everything degrades gracefully (no model / unreachable → plain enhance).
- Schema: `ollama_vision_model`, `ollama_vision_available_models`, `vision_enabled` on app_settings (+ migration); new `backend/services/llm/vision.py`; manual + auto-gen enhance injection.

## [1.10.2] - 2026-06-21

### Added — "Include LLM Instruction" for prompt enhancement

A per-scene custom instruction you can hand the LLM to keep it on track when Enhance drifts from what you want.

- A compact button (pencil icon) sits next to the **Enhance** button on both the **image** and **video** tabs. Click it to open a small lightbox showing the current prompt plus a box for your direction (e.g. "keep her seated", "wide shot only", "no text"). It's saved on the scene and reused every Enhance until cleared.
- The button **lights up amber with a dot when an instruction is set**, so you can see at a glance that one is active.
- The instruction is injected as the **highest-priority** line of the enhance context (it overrides other guidance on conflict). Stored separately for image (`llm_instruction_image`) and video (`llm_instruction_video`).
- **Auto-gen honors it too:** the same per-scene instruction is prepended to the auto-generation enhance context for both image and video, so the LLM stays on track during batch runs — not just manual Enhance.

## [1.10.1] - 2026-06-21

### Improved — Last Frame image generation (distinct end-point + first-frame reference control)

Last Frame renders were coming out too similar to the First Frame. Two changes:

- **Stronger Last Frame prompting:** `LAST_FRAME_IMAGE_SYSTEM_PROMPT` now leads with how to *derive* the last frame — read the First Frame prompt **and** the scene's story flow, then advance the action to a CLEARLY DIFFERENT moment (subject position/pose/action/expression and/or camera framing) rather than restating the first frame. The First-Frame-image reference is now treated as optional (rely on the First Frame prompt for continuity when it isn't attached). The Enhance call also injects the scene's story flow + an explicit "distinct end-state" directive into the Last Frame context.
- **New per-scene toggle "Don't reference first frame image"** (under "Reference: First frame set" on the Last Frame tab), **ON by default**. On = the last frame is generated freely from the prompt + character refs (no pixel over-anchoring to the first frame — the prior behavior). Off = the chosen first-frame image is prepended as Klein reference slot 1 for tight visual continuity (the workflow auto-bumps a ref tier). Character reference selections continue to apply to the last frame as before.

## [1.10.0] - 2026-06-21

### Added — Ideogram Prompting Mode (Krea 2 structured-JSON captions)

Opt-in mode that prompts Krea 2 with the Ideogram-4 structured caption format — positional bounding boxes + per-element color palettes — for precise composition control, instead of plain natural language. OFF by default; only engages when the first-pass model is Krea 2.

- **Concept tab:** global "Ideogram Prompting Mode" toggle (stored in project.settings.json_prompt_mode).
- **Image tab (Krea 2 only):** per-scene override (Project default / On / Off) plus a **JSON Prompt** button opening a simple editor — view/edit the caption, **✨ Generate with AI** (drafts it from the scene prompt), and an **Instructions** panel.
- **Auto-gen honors the setting:** the dispatcher checks the effective mode (scene override ▸ project default) and, when on, builds/loads a structured caption and routes to a Krea 2 workflow with the Ideogram Prompt Builder node — so auto-gen needs no special handling.
- **LLM prompting:** new `JSON_PROMPT_SYSTEM_PROMPT` teaches any LLM the format (coordinate system, layered decomposition, color rules, palette-override priority) + `normalize_ideogram_caption` validator (uppercase hex, clamped 0-1 coords, palette caps). Captions are cached on the scene; manual edits are respected. Graceful fallback to plain Krea 2 if the caption can't be built.
- `prepare_krea2_ideogram_workflow` populates the Ideogram4PromptBuilderKJ node (x/y/w/h fractions → the node converts to Ideogram bbox), leaving all tuned sampler/variance/model settings untouched. New `POST /generate/json-prompt` endpoint. Workflow `KREA2_IDEOGRAM_T2I.json` registered when present. Full design in `docs/IDEOGRAM_JSON_PROMPT_MODE.md`.

### Fixed — Pass-2 rerun crashed with "Unknown workflow type: klein_6ref"

The "Re-run Pass 2" path gathered ALL project characters with no cap (1 base + 5 chars = klein_6ref, which doesn't exist — Klein ships up to 5REF). Now it uses the scene's selected characters (image_refs_first.characterIndices, the single source of truth), caps at 3 (matching the auto-chain), and hard-clamps the workflow to klein_5ref.

## [1.9.3] - 2026-06-21

### Fixed — two-pass Pass-1 mislabeled "Z-Image Turbo" when actually rendered by Krea 2

The image was rendering correctly on Krea 2 (confirmed in logs: `Redirecting to Krea 2 Turbo (two-pass Pass 1...)` → `Krea2_*.png`), but the UI labeled the Pass-1 image "Z-Image Turbo". Root cause: the two-pass base re-enhances its prompt and rebuilds the workflow; the first-pass redirect's in-memory `workflow_type="krea2_turbo"` mutation didn't survive the rebuild + session refresh, so the asset recorded `klein_t2i`, which the frontend maps to "Z-Image Turbo".

- **Backend** (`dispatcher.py`): when an asset's resolved `workflow_type` is `klein_t2i`, record the configured first-pass generator (`krea2_turbo`/`z_image_turbo`) instead — since `klein_t2i` is always redirected to it. Fixes the stored model on all new renders.
- **Frontend** (`SceneEditor.tsx`): the Pass-1 base label treats `klein_t2i`/missing as the configured first-pass generator, so already-generated images also label correctly.
- **Hardening** (`workflow.py`): `prepare_krea2_workflow` now coerces width/height/seed defensively so a null value can never raise and cause a silent Z-Image fallback.

> Existing two-pass images keep their stored value but now label correctly via the frontend fix; regenerate to also correct the stored metadata.

## [1.9.2] - 2026-06-21

### Fixed — two-pass base re-enhanced prompt silently dropped; Krea 2 first-pass diagnostic

- **Two-pass base double-build bug** (`dispatcher.py`): for two-pass scenes, Pass 1 re-enhances the prompt to scene-only, then rebuilt the workflow. But the first build had already redirected `workflow_type` away from `klein_t2i` (to `z_image_turbo`/`krea2_turbo`), so the rebuild raised "Unknown workflow type" — caught and ignored — which silently discarded the re-enhanced scene-only prompt (Pass 1 ran with the original, character-laden prompt). Fixed by resetting `workflow_type` to `klein_t2i` before the rebuild so the first-pass redirect re-runs (same model choice) with the re-enhanced prompt. Affected both Z-Image and Krea 2 first passes.
- **New `tools/diag_krea2.py`**: pinpoints why a first pass still renders as Z-Image when Krea 2 is selected — checks the saved `single_image_generator`, the presence of `KREA2_TURBO_T2I.json`, and the on-disk VERSION (to confirm the backend was restarted on the new code).

> Note: the "first pass shows Z-Image" label is driven by the dispatcher's first-pass redirect, which chooses Krea 2 only when `single_image_generator == 'krea2_turbo'` AND `KREA2_TURBO_T2I.json` exists. If either is false at runtime — or the backend is still running pre-1.9.0 code — it falls back to Z-Image. Run `tools/diag_krea2.py` to see which.

## [1.9.1] - 2026-06-21

### Added — Chapter scope picker for Auto-Gen (All / Single / Multiple)

The Auto-Generate panel now has the same chapter scope selector as the Export screen, so you can run auto-gen on the whole project, one chapter, or several specific chapters — instead of only "the chapter you're currently viewing" vs "everything."

- Reuses the export `ChapterPicker` (All / Single / Multiple). Defaults to the chapter you're currently viewing (Single) if any, else the whole project (All) — so existing behavior is preserved.
- **Multiple** runs the selected chapters **sequentially**, one scoped pass per chapter in timeline order: each chapter gets its own scoped story-flow pre-step + auto-gen run, and the next only starts after the current one finishes (polls the auto-gen status endpoint for completion). No changes to the batch pipeline internals.
- Cancel stops the whole queue between chapters. Per-chapter failures are collected and reported without aborting the rest of the queue.
- Picker only appears for projects that have chapters; single/all paths are unchanged.

## [1.9.0] - 2026-06-21

### Added — Krea 2 Turbo as an optional first-pass image generator

Full integrated, **gated** support for Krea 2 Turbo as an alternative to Z-Image for no-reference (first-pass) text-to-image. Krea 2 is first-pass only (not an edit model) — character compositing (Pass 2) still always uses FLUX.2 Klein. **Nothing changes for existing users:** the default remains Z-Image, and Krea 2 only activates once a tested `KREA2_TURBO_T2I.json` is present (otherwise it logs a notice and falls back to Z-Image).

- **Settings:** new "Krea 2 Turbo" option under Single Image Generator, plus a "Krea 2 Model File" picker (`krea2_turbo_fp8.safetensors` for RTX 40xx/older, `krea2_turbo_mxfp8.safetensors` for RTX 50xx Blackwell). New `krea2_model_name` setting (schema + migration + API serialization).
- **Dispatcher:** the `klein_t2i` first-pass redirect now resolves the selected generator — Z-Image (default) or Krea 2 — and is gated on the workflow file existing. Sets the real `workflow_type` (`krea2_turbo`/`z_image_turbo`) for correct worker capability matching and UI labels.
- **Workflow prep:** new `prepare_krea2_workflow()` — tolerant node resolution (title with class-type fallbacks) so it works with whatever tested JSON is supplied; overrides the diffusion model to the chosen fp8/mxfp8 file.
- **Prompting:** dedicated `KREA2_IMAGE_SYSTEM_PROMPT` with Krea 2-specific rules (natural-language prose, no quality-booster/tag spam, no weight syntax, lighting/material-led, concise). The enhancer auto-uses it when Krea 2 is selected. Other models' prompts are untouched.
- **UI labels:** scene model badges and the generation queue badge recognize Krea 2 Turbo; the predicted 0-ref label follows the setting.
- **Docs:** new `docs/KREA2_GUIDE.md` — model variants + fp8/mxfp8 (50xx vs older), download locations, ComfyUI settings (8 steps, CFG 0–1, er_sde, simple), prompting best practices, and an activation checklist.

## [1.8.31] - 2026-06-21

### Fixed — SQLite WAL never shrank (parked at ~4 MB with "nothing to commit")

Power-user report: the `-wal` file grows to ~4 MB and stays there even when there's nothing left to commit. Diagnosed as expected-but-untidy SQLite behavior, not corruption:

- WAL mode was enabled but no explicit checkpoint was ever forced. SQLite's automatic checkpoint runs in **PASSIVE** mode at the `wal_autocheckpoint` threshold (default 1000 pages × 4 KB page size = **~4 MB**, exactly the size observed). PASSIVE folds committed frames back into the main `.db` but **never truncates** the `-wal` file, so it parks at ~4 MB. The data is already committed ("nothing transferable") — the file just isn't reclaimed.
- The WAL capping at 4 MB (rather than growing without bound) confirms checkpoints were succeeding, so there was **no leaked/long-lived reader** blocking checkpointing.

Fixes (`backend/database/database.py`, `backend/main.py`):
- New `checkpoint_wal(mode="TRUNCATE")` helper runs `PRAGMA wal_checkpoint(TRUNCATE)` to fold frames in **and** shrink the `-wal` to 0 bytes.
- `cleanup_db()` now TRUNCATE-checkpoints before `engine.dispose()`, so a clean shutdown leaves a 0-byte `-wal`.
- New `periodic_wal_checkpoint()` background loop (every 5 min) TRUNCATE-checkpoints during long sessions, reclaiming disk after big write bursts (auto-gen / batch). Cancelled on shutdown.
- `wal_autocheckpoint=1000` is now set explicitly with a comment documenting the 4 MB relationship.

## [1.8.30] - 2026-06-18

### Improved — Pass-2 Klein compositing preserves the base scene (anti-darkening)

Two-pass character compositing was re-grading and darkening scenes instead of just inserting characters. Root cause: the Klein "Edit Ultra" workflow generates from an EMPTY latent conditioned on reference latents, so it regenerates the whole frame (using the base scene only as a reference) and drifts in exposure/palette — Klein also blends lighting from the character reference photos.

Prompt-level hardening (the structural fix is a workflow change — see note):
- **Always-on base-preservation anchor at dispatch** (`dispatcher.py`): every Pass-2 composite prompt now gets a strong instruction appended at the very end (where Klein weighs tokens most) to keep the first reference image's exact lighting, exposure, brightness, contrast, color grade, palette and composition, and to insert ONLY the characters — "do not darken, dim, desaturate, re-grade or restyle." Previously this only happened when a color override was active.
- **Stronger anti-darkening in the Pass-2 system prompt** (`prompt_enhancer.py`): the top rule now explicitly calls out Klein's tendency to darken/re-grade and requires the prompt to lock the base image's exact brightness and exposure.

> **Note (workflow-level fix):** the most complete fix is to run Pass 2 as img2img — feed the base scene as the *init latent* with denoise ~0.5–0.7 instead of `EmptyFlux2LatentImage` — so Klein preserves the base pixels and only paints the characters in. That requires editing `KLEIN_EDIT_ULTRA_WORKFLOW_*REF.json` and testing in ComfyUI; can be added as a tunable per Lorenzo's preference.

## [1.8.29] - 2026-06-18

### Fixed — Workflow label now shows Z-Image for no-reference renders

The Image tab displayed "FLUX Klein – Text to Image" for a 0-reference scene, even though the backend always redirects `klein_t2i` to Z-Image Turbo at dispatch (Klein is only ever used to composite character refs in Pass 2). The label was misleading — the actual render was already Z-Image. Fixed three frontend label spots to show **Z-Image Turbo** for `klein_t2i`: `labelWorkflow()`, the per-scene model label (no longer gated on the `single_image_generator` setting), and the computed-workflow display. Post-render history already showed the correct model.

## [1.8.28] - 2026-06-18

### Added — Auto-pick character references on Enhance + robust auto-gen selection

Completed the character-selection model so the right references are chosen automatically without ever overriding a deliberate choice. The rule everywhere: **a scene's `image_refs_first.characterIndices` is authoritative; auto-pick only fills it in when it's absent (never set).**

- **Enhance/Generate now auto-pick when a scene has no explicit selection.** The frontend re-enabled `autoSelectCharactersForScene`, but gated on "no explicit selection yet": it matches the scene's flow/prompt/narration text against the character roster (full name or first-name, word-boundary aware), selects up to 3, and persists them — so enhancing a fresh scene picks the correct refs. An explicit selection (manual picks, a deliberate empty `[]`, or what auto-gen saved) is respected verbatim and never overridden.
- **Auto-gen auto-selects in the image phase too, not just at flow time.** Both the windowed and sequential auto-gen paths now run the same server-side character pick (`_select_scene_characters_from_flow` over flow + prompt + narration, cap 3) for any scene without an explicit selection, and persist the result to `image_refs_first` so the Image tab shows exactly what was used. This makes auto-gen's selection reliable even when story flow already existed (previously the pick only ran inside `_ensure_video_flow` during fresh flow generation).

Net: run Auto Gen → each scene gets its most-relevant characters chosen, persisted, and visible. Manually enhance a brand-new scene → it picks the right characters for you. Deselect characters on a scene → that choice sticks and nothing re-adds them.

## [1.8.27] - 2026-06-18

### Fixed — Deep audit of image-gen: no-ref renders use Z-Image, characters only when selected

Two linked regressions: a scene with no characters selected still rendered with characters, and no-reference renders ran on Klein instead of Z-Image. Root cause was a chain of "default to the first N project characters" fallbacks (one of them added in 1.8.26) that injected characters a scene never asked for — which made the workflow `klein_Nref` (Klein-with-refs) instead of `klein_t2i`, so it never redirected to Z-Image.

Audited the whole path and made the scene's `image_refs_first.characterIndices` the strict single source of truth:

- **Removed every "default first-N characters" fallback** — in the `/generate-image` two-pass resolver, and both the windowed and sequential auto-gen paths. No selection (empty OR absent) now means **no characters**. Auto-gen still auto-selects via the server-side LLM character pick (which persists the choice and shows it on the Image tab); scenes the LLM names no character for stay character-free.
- **Auto-gen seeds an explicit empty selection** (`characterIndices: []`) when a scene has none, so the Image tab reflects "no characters" instead of silently pulling project characters in.
- **Frontend `autoSelectCharactersForScene` disabled** — it used to re-add characters mentioned in the flow/prompt text on every Enhance/Generate, overriding the visible selection. The saved selection is now authoritative everywhere.
- **`klein_t2i` always redirects to Z-Image Turbo** in the dispatcher (regardless of the `single_image_generator` preference). Klein is only ever used to composite character references (Pass 2); a zero-reference text-to-image render must always be Z-Image. Two-pass Pass 1 was already forced to Z-Image; this extends it to all no-reference renders.

Net: remove all characters from a scene → it renders single-pass on Z-Image with no characters; select 1–3 → Klein Pass 2 composites exactly those. The Image-tab selection is precisely what gets used and is saved for re-render/troubleshooting.

## [1.8.26] - 2026-06-18

### Fixed — Scene character selection is now the single source of truth (auto-gen used hidden refs)

A scene with no characters selected on the Image tab could still render with characters (a "4-reference" composite), and the picker didn't reflect what auto-gen actually used.

- **`/generate-image` two-pass fallback no longer resolves ALL project characters.** When a generation request carries no explicit refs, it now resolves characters from the scene's `image_refs_first.characterIndices` (an explicit empty list = "no characters here"), and when the field is absent it defaults to the first 3 AND persists that — so the Image tab always reflects exactly what Pass 2 composited.
- **Character caps raised 2 → 3** across the auto-gen paths (`_resolve_character_asset_ids(..., max_chars=3)`, the first-N defaults, and the seeded `characterIndices`) so a scene's saved 3-character pick is actually honored end-to-end (slot 1 = base image, up to 3 character refs).
- **Frontend refreshes scenes when auto-gen finishes** (`invalidateQueries(['scenes', id])`), so the Image-tab selections update to match what auto-gen persisted instead of showing stale empty state.

Net: the characters shown selected on each frame's Image tab are exactly the references used in the second pass, and that selection is saved for re-render / troubleshooting.

## [1.8.25] - 2026-06-18

### Fixed — Manual scene character selections now stick (were overridden by auto-select)

On the Image tab, manually selecting/deselecting characters didn't survive clicking **Enhance** or **Generate**. Cause: `autoSelectCharactersForScene()` ran on every Enhance/Generate, re-added any character whose name appeared in the flow/prompt/lyrics text, and saved that over the user's picks — so deselecting a character that's mentioned in the text silently reverted.

Fix (`frontend/src/components/SceneEditor/`):
- The reference picker's `onChange` now marks the frame as **manually edited** (`image_refs_first_manual` / `image_refs_last_manual` in scene params, persisted via the cache-coherent `updateSceneAndSync`).
- `autoSelectCharactersForScene()` short-circuits and returns the current selection unchanged once that manual flag is set — so auto-select can seed an initial suggestion, but never overrides a user's explicit choice afterward.
- Raised the per-frame character cap from 2 to **3** in both the `ReferenceSelector` UI and the auto-select logic, matching 1.8.24 (slot 1 stays the base scene image; up to 3 character refs).

Now: pick/deselect characters on a scene, hit Enhance or Generate, and your selection is exactly what's used and saved.

## [1.8.24] - 2026-06-18

### Changed — Per-scene character cap is 3 (not 2)

Per Lorenzo: the Klein composite daisy-chains references (slot 1 = the base scene image, then character refs), and up to **3 characters** gives great results while more starts "off-roading". Raised the per-scene character selection cap from 2 to 3 (`_select_scene_characters_from_flow(..., cap=3)`), updated the story-flow prompt to state the 3-character limit, and lowered the dispatcher's hard ceiling `MAX_CHARS_IN_COMPOSITE` from 4 to 3 so no composite ever exceeds 3 character refs. "Fewer is better when only one or two truly matter" is kept in the prompt so scenes aren't crowded.

## [1.8.23] - 2026-06-18

### Added — LLM picks the 2 most important characters per scene (was: first 2)

Scene images previously defaulted each scene's character refs to the **first 2 project characters** positionally (`characters[:2]`), regardless of who the scene is actually about. The story-flow LLM is now told the **3-character limit** (slot 1 is reserved for the base scene image; up to 3 character refs) and instructed to reference only the **1–3 most important characters by name** for each scene; `_ensure_video_flow` then derives `image_refs_first.characterIndices` from the characters the flow actually named (matched by name / first token, in order of appearance, capped at 3). Falls back to the existing default only when the flow names no character, and never overrides an explicit manual selection. New helper `_select_scene_characters_from_flow` in `backend/api/generation.py`.

Result: a scene about "the Rabbit and the Fox" gets the Rabbit and Fox refs — not whoever happens to be characters #1 and #2. Re-run Auto Gen / regenerate Story Flow to apply to existing scenes.

## [1.8.22] - 2026-06-18

### Fixed — SRT upload no longer blocks on Whisper; re-anchor moved into Process Audio

The 1.8.20/1.8.21 SRT re-anchor ran Whisper **synchronously inside the SRT upload**. With a ComfyUI Whisper backend (multi-minute, ~52-min budget) the upload HTTP request blocked and the frontend errored — and it could spawn a second Whisper run alongside an in-flight Process Audio pass.

Reworked so the timing re-anchor happens where Whisper already runs — **Process Audio** — and SRT upload stays instant:

- **`upload_srt` is now fast and never runs Whisper.** If the project already has Whisper word timing (from a prior Process Audio run) it maps the SRT spelling + cue grouping onto it instantly; otherwise it just stores the SRT and logs that Process Audio will re-anchor it.
- **`analyze_audio` (Process Audio) now re-anchors instead of discarding Whisper timing.** Previously, re-analyzing a project that had an SRT *kept the SRT's drifting timestamps* and threw away the fresh Whisper timing. It now combines them via `retime_srt_words_to_audio`: SRT words + cue blocks (correct spelling) with Whisper's audio-accurate timing.

Workflow (matches the intent): **Upload SRT (instant) → Process Audio (Whisper runs once, re-anchors) → Suggest Timeline.** Result: SRT spelling + cue grouping with audio-accurate cut points, and no upload timeouts.

## [1.8.21] - 2026-06-18

### Changed — SRT re-anchor now reuses the existing Whisper pass (robust)

Refined the 1.8.20 SRT-timing fix after confirming the real use case: the SRT is needed for correct ElevenLabs **spelling** (Whisper garbles words), while Whisper provides accurate **timing** from the audio. The combination — SRT words + Whisper timing — is exactly what `retime_srt_words_to_audio` produces.

`upload_srt` now sources the timing in priority order: (1) **reuse Whisper words already stored on the project** from a prior Process Audio run — no re-transcription, and no dependency on locating the audio file at upload time; (2) fall back to a fresh Whisper pass on the audio (with hardened path resolution that tries both `project_dir/<id>/rel` and `project_dir/rel`). This removes the audio-path fragility that caused the re-anchor to silently no-op on projects whose media lives on a different drive than the database.

Deterministic workflow: **Process Audio (Whisper) → Upload SRT → Suggest Timeline.** Result: clean SRT spelling + cue grouping, with audio-accurate cut points. Verified by simulation (Whisper "tortis" → SRT "tortoise" spelling kept, blocks kept, timing error 0.000s vs audio).

> Requires the backend to be restarted on this version. Note: this surfaced a separate environment issue — a project whose **database is on C: but media on D:** (leftover from the old broken `change_project_dir`); consolidating those onto one `project_dir` is recommended.

## [1.8.20] - 2026-06-18

### Fixed — SRT timings re-anchored to the actual audio (root cause of narration drift)

Diagnosed the long-standing "scenes cut earlier and earlier, dialogue still playing after the cut" problem to its true root cause — and it was NOT the segmenter. Using a Whisper pass over the real audio of a 13-minute narration (`1-The_Song_Beneath_the_Stump_V6`), the SRT-derived scene boundaries were shown to be a perfect match to the SRT (0 bleed) but drift progressively against the actual audio: **39 of 48 scenes ended mid-word** when measured against real speech, with the offset growing to ~10s by the end. The cause is that ElevenLabs (and similar) **SRT word timestamps drift from the rendered audio** on long files — the text and cue grouping are correct, but the times are not. Because the segmenter faithfully placed cuts on the SRT times, every prior segmentation fix reproduced the bad input timing.

Fix: when an SRT is uploaded, the app now **re-anchors the SRT's timing to the audio**. New `retime_srt_words_to_audio()` in `backend/services/audio/text_align.py` runs a difflib sequence alignment between the SRT word stream and a Whisper pass over the real audio, then keeps the SRT's word strings AND cue (`block`) grouping while transferring Whisper's audio-accurate `start`/`end` onto each word (interpolating words Whisper missed, distributing time across mismatched runs, enforcing monotonic order). `upload_srt` runs this automatically (Whisper on the actual audio, `skip_demucs=True`), and it is fully best-effort: any failure, low SRT/Whisper similarity (<30%), or a project with `disable_whisper` set falls back to the SRT's own timings so upload never breaks.

Result: you keep clean ElevenLabs SRT wording AND cue grouping, but scene boundaries now sit on audio-accurate times. Verified by simulation (SRT drift 0.36s mean → 0.000s after re-anchor; text + blocks preserved; monotonic; graceful bail on mismatched audio and on Whisper-missing words).

> **To fix an existing project:** re-upload its SRT (this triggers the audio re-anchor), then re-run **Suggest Timeline**. Note the SRT upload now runs Whisper, so it takes ~30–90s instead of being instant.

### Added — `tools/diag_timeline.py` audio reality check

The timeline diagnostic now auto-targets the most recently edited project and adds an "audio reality check": it locates the project's audio file, ffprobes its true duration, and (for SRT projects) compares cue times to ffmpeg-detected speech onsets — the tooling that surfaced the SRT-vs-audio drift. Also reports per-scene bleed and drift-growth.

## [1.8.19] - 2026-06-18

### Added — Structure-first narration segmentation (two-phase)

Reworked `_dp_segment_narration` so scene cuts follow the narration's real structure instead of only chasing an even length. Per Lorenzo's two-phase model:

- **Phase 1 — adaptive major-silence anchors.** The segmenter measures this narration's own inter-phrase pauses and flags the ones that are clearly larger than typical (≥ 3× the median pause, floor 1.5s — adaptive so it works for tight SRT cues and loose Whisper timing alike). These structural pauses become near-inviolable scene boundaries via a heavy DP span penalty (a scene that would swallow a major silence is charged 50 cost units, dwarfing every normal term). The penalty is finite, so honouring an anchor can never push the DP into the fixed-slice fallback.
- **Phase 2 — even fill.** The existing duration-balancing DP fills each chunk between anchors, keeping scenes as consistent as possible within the project's min/max (e.g. 8–20s).
- **Standalone silence scenes.** A pause at least as long as the scene minimum (e.g. an instrumental break or deliberate beat) is carved into its OWN scene rather than split 50/50 between neighbours. Guarded so a long pause can never shave an adjacent scene below 60% of the minimum.

This is additive: projects with no major silences (e.g. uniform-cue SRT like the verified V6) segment exactly as before — even-filled, midpoint boundaries, no spurious silence scenes. The win shows up on long narrations and anywhere a scene previously spanned a clear topic-change pause.

Verified by simulation: uniform SRT → unchanged even fill (no silence scenes); SRT with a 12s instrumental gap → exactly one carved ~12s silence scene; Whisper with structural pauses → cuts anchored on the pauses, all scenes within 8–20s, no uniform-10s collapse.

> **To apply:** re-run **Suggest Timeline** on narration projects (segmentation only changes on regeneration; Process Audio just re-snaps existing scenes).

### Note — uniform-10s projects are stale, not a live bug

Confirmed the fixed-slice fallback is unreachable in current code: running the real segmenter on Judges-scale Whisper data (1,200 words) yields 60+ distinct scene lengths, never uniform 10s. Projects still showing a uniform 10s grid (e.g. *The Book of Judges*, *Bacon Is For Liars V2*) were segmented by an older build and never re-segmented — Process Audio only re-snaps, it doesn't re-run segmentation. Re-running Suggest Timeline regenerates them correctly. Added `tools/diag_timeline.py` to inspect any project's SRT-cue map, timing source, and per-scene boundary alignment.

## [1.8.18] - 2026-06-18

### Fixed — SRT cue times are now the authoritative scene-segmentation map (no more cumulative drift)

Lorenzo re-ran *Process Audio* with an SRT loaded and saw narration scenes start aligned but progressively cut earlier and earlier before each pause toward the end of the timeline — even though the SRT carries exact per-cue start/end times, so segmentation should be a pure lookup.

Root cause in `backend/api/timeline.py::_group_words_into_sentences` (the phrase grouper that feeds `_dp_segment_narration` / Suggest Timeline): when a pasted script (`initial_text`) was present, it always ran the fuzzy `_match_words_to_lyrics_lines` "monotonic multi-word anchor" matcher to map script lines onto word timestamps — **even when the words came from an SRT.** That matcher interpolates missed anchors by time and tolerates matches up to 20s away, so its small per-line errors accumulate down a long script. The previous 1.8.17 fix corrected boundary *placement* between phrase groups, but the phrase *groups* themselves were still built by the drifting matcher, so the SRT's exact map was never actually used.

Fix: `_group_words_into_sentences` now checks for SRT `block` indices first. When present, it groups one phrase per cue directly from the block map (each cue's words already carry exact SRT start/end) and returns immediately — the fuzzy matcher is bypassed entirely. Scene boundaries become an exact lookup against the cue times, identical for every project regardless of length. Whisper-only projects (no `block`) fall through to the unchanged lyrics/punctuation grouping.

Verified by simulation: 40 SRT cues with varied inter-cue pauses produce boundaries that land at the exact midpoint of every cue gap with **zero cumulative error** (error does not grow with cue index) and zero dialogue bleed.

> **To apply:** re-run **Suggest Timeline** on the project. *Process Audio* only re-snaps existing scene boundaries (it preserves the scene count and your manual edits); regenerating the segmentation from the corrected cue map requires a Suggest Timeline pass.

## [1.8.17] - 2026-06-18

A maintenance + narration-precision release: a critical truncation repair in the settings API, three code-hygiene cleanups, and a precise rework of how narration scene boundaries and subtitle cues are timed so video stays locked to the audio through to the end.

### Fixed — Truncated `change_project_dir` endpoint (data-loss risk)

`backend/api/settings.py` had shipped truncated: the `change_project_dir` endpoint ended mid-statement at `settings.project_dir = str` — no closing call, no `commit`, no `return`. It parsed clean only because `str` is a valid builtin reference, so neither `ast.parse` nor `tsc` ever flagged it (the Edit-tool Windows-mount truncation pattern from the handover notes). With *move data* enabled the endpoint physically `shutil.move`d all project data to the new folder, then failed to persist the new path — leaving the DB pointing at the now-empty old directory, and returning no body despite declaring `response_model=ChangeProjectDirResponse`. Completed the statement to persist (`str(new_path)`), `session.add` / `commit` / `refresh`, log, and return the declared model — matching the commit pattern already used elsewhere in the file. Restart-to-apply behavior is by design and already surfaced in the UI.

### Fixed — Narration scene cuts now split pauses evenly and never clip dialogue

Lorenzo reported that the ends of some scene dialogues were still being heard after the visual had already cut to the next scene, even though that dialogue belonged to the scene's lyrics — and that the mismatch compounded toward the end of long narrations. The live preview is audio-driven (it shows whichever scene owns the current master-audio position), so the bleed meant a scene's `end_time` was genuinely landing before its dialogue finished.

Reworked the boundary builder in `backend/api/timeline.py::_dp_segment_narration`:

- **Inter-phrase pauses are now split evenly between the two adjacent scenes.** The boundary is placed at the midpoint of the gap (`(prev_end + start) / 2`), so a 1.0s pause leaves 0.5s tailing the current scene and 0.5s leading into the next (previously a >0.6s gap was biased to `next_word_start − 0.3`). Per Lorenzo's spec.
- **Anti-bleed guarantee.** The phrase end used for the cut is now the MAX word-end across the whole phrase rather than the last word's recorded `.end`, so the boundary always sits after every spoken word even when Whisper under-shoots a word's end. The midpoint placement then guarantees `boundary > prev_end`, so dialogue can never overflow the cut.
- **Intro/outro pauses are never split.** Scene 1 now always starts at `0.0` (was `first_word − 0.3`, which could shift the entire timeline out of sync by the intro length), owning the intro silence; the final boundary stays at `total_duration` so the outro stays whole with the last scene. Long deliberate lead-in/lead-out pauses are left intact because they belong to a single scene only.

Verified by standalone simulation: a 1.0s pause splits to the exact midpoint, a 0.4s pause splits to its midpoint, intro/outro are preserved, and every interior boundary lands at or after its phrase's max word-end (zero bleed).

### Fixed — Burned-in subtitle cues now honor the SRT and break at pauses

`backend/services/video/ffmpeg.py::generate_ass_subtitles` previously re-grouped words by a fixed gap+word-count heuristic, ignoring the SRT `block` index — so exported captions could diverge from the uploaded SRT (and from the live preview, which groups strictly by block). When words carry SRT block indices that cue grouping is now AUTHORITATIVE: exactly one subtitle line per block, matching the SRT and the preview. Whisper-sourced words (no block) keep pause-based breaking (>0.3s gap, or 8 words) so captions track the spoken rhythm and clear during silences.

> **Applying the narration fixes:** existing projects must re-run **Suggest Timeline** to regenerate boundaries — the change is in boundary *generation*, not a migration of existing scenes.

### Changed — Code hygiene cleanups (no behavior change)

- **Removed deprecated `asyncio.get_event_loop()`** at both call sites in `settings.py` (folder picker + change-dir), swapped to `await asyncio.to_thread(fn)`, matching the pattern already used elsewhere in the file. Avoids the Python 3.12+ `DeprecationWarning`.
- **Production console stripping.** `frontend/vite.config.ts` now uses the `defineConfig(({ mode }) => ...)` form with `esbuild: { drop: mode === 'production' ? ['console', 'debugger'] : [] }`. Because the app always runs `vite build` and serves `dist/`, all `console.*` statements are stripped from the shipped bundle while `vite` dev keeps them for diagnostics.
- **Removed stale artifacts** — `frontend/dist_new/`, an empty top-level `New folder/`, and ~19 leftover `frontend/vite.config.ts.timestamp-*.mjs` Vite temp files.

### Verification

Backend `ast.parse` clean across all 59 files; `py_compile` clean on every touched/related file; truncation marker + abrupt-EOF + bare-builtin-assignment sweeps all clean; frontend `tsc --noEmit` clean; boundary and subtitle-grouping logic confirmed via standalone simulations.

## [1.8.16] - 2026-06-17

This release covers a large surface — batch-mode parity with all features added in 1.8.x, server priority routing, global project context for LLM prompts, a long-running narration-timing drift fix, several UI state-refresh fixes, and the Generation Queue scene-name persistence.

### Fixed — Narration scenes cut before previous rhyme finishes speaking

Lorenzo reported that final-exported narration videos cut to the next scene while the previous scene's words were still being heard in the audio. The drift was visible enough to make the rhyming structure feel "off."

Root cause traced to two places in `backend/api/timeline.py::_dp_segment_narration`:

- **DP main path silent rejection** — the inner loop's `if dur > max_dur: break` rejected any single phrase group whose span exceeded `video_max_duration`. When that happened to ANY phrase, the entire DP gave up and fell through to the natural-break fallback (which has its own drift problem).
- **Natural-break fallback's flat clamp** — `elif snapped - pos > max_dur: snapped = pos + max_dur` capped a scene's `end_time` at a fixed offset regardless of where the phrase's words actually ended. The next scene's `start_time = pos = snapped` inherited the clamped value. The master narration audio plays continuously across the assembly, so any words past the clamped boundary overflow into the next scene's visual window.

Two-part fix:

- **Pre-split overlong phrase groups before the DP runs.** New block at the top of `_dp_segment_narration` walks every phrase group; if `last_word.end - first_word.start > max_dur`, recursively splits at the largest internal inter-word gap (the speaker's natural breath/pause). Each sub-phrase fits and the DP main path always finds a solution. Logs a `WARNING` with the original vs. final group count whenever splits happen.
- **Phrase-respecting clamp in the fallback.** Replaced the flat `snapped = pos + max_dur` with a search for the LAST natural break in `[pos+min_dur, pos+max_dur]` — SRT cue end or Whisper word-gap candidate. When no candidate exists (genuinely impossible-to-satisfy constraint — e.g. a single 15s word), a loud `WARNING` logs the situation and accepts the flat clamp as a last resort, telling the user how to fix it (raise `video_max_duration` in Settings or split the phrase).

Runtime smoke test with a deliberately overlong 25s phrase containing a 15s unsplitable word produced four scenes, none exceeding `max_dur`, the unsplitable region called out with a `WARNING` rather than silent drift.

### Fixed — Auto-Gen modal stuck on completed state until page refresh

Closing the Auto-Gen modal after a completed run kept the local `autoGenStatus = 'done'` plus `sessionHasStarted = true` latch from 1.8.4. The next click on Auto Gen re-opened the modal directly on the completion screen — no path back to the setup form without a full page refresh.

Fix in `AppLayout.tsx::AutoGenerateModal.onClose`: when status is terminal (`done`/`completed`/`failed`/`cancelled`) at close time, reset the entire chunk of auto-gen state (`autoGenStatus → 'idle'`, mode, completed/total counters, step text, scene name, batch run ID, minimized + dismissed flags) and clear the polling interval. Next open shows the setup form fresh. The active-run branch (running/pending) still goes to minimized state as before.

### Fixed — SRT upload didn't refresh the SRT-loaded indicator or Disable Whisper toggle

Both the "SRT loaded" chip on the Audio tab and the new "Disable Whisper Detection" toggle (1.8.15) gate off `lyricsData.source === 'srt'`. After SRT upload, the handler did `setQueryData(['lyrics', projectId], srtData)` — but if the backend's upload-SRT response omitted the `source` field, the cache was written with that field undefined and the UI stayed in "no SRT" state until a full page refresh.

Fix in `AudioSetup.tsx::onChange` (SRT input):

- **Force `srtData.source = 'srt'`** before writing to the cache so the indicator flips green and the toggle un-greys immediately.
- **Backfill `cue_count`** from the words' `block` set or `srt_blocks.length` when missing, so the chip's "N cues · M words" text is accurate.
- **Also `invalidateQueries(['lyrics', projectId])`** so subsequent consumers (Concept tab, scene boundary audit) refetch authoritative state from `GET /lyrics`.

### Added — Per-server priority for ComfyUI worker selection

Mixed-speed render farms now route jobs in priority order. Lower priority number = picked first when idle; among workers with equal `in_flight` load, the lower-priority-number worker wins. Once a high-priority server saturates, idle lower-priority workers pick up the overflow automatically — the "use fast server first, fall back to slow servers when fast is busy" behavior users wanted.

- `backend/services/comfyui/dispatcher.py::ComfyWorker.priority: int = 100` — new field, range 0-1000.
- `apply_user_caps` reads `caps_config["priority"]` and clamps; ignores non-numeric input rather than resetting.
- `select_worker` sort key changed from `(in_flight, -last_check)` to `(in_flight, priority, -last_check)`. `in_flight` stays primary so a busy high-priority worker yields to an idle low-priority one — the fallback path.
- Settings UI: small `PRIO` number input on every server row, default 100, step 10, clamped client-side.
- Persistence: lives in `AppSettings.comfyui_server_caps[url]["priority"]` — same JSON shape as the image/video toggles, so the existing settings PUT handler persists it without backend changes.

Runtime-tested with three workers (fast prio=10, mid=50, slow=100): Job 1 → fast, Job 2 (fast busy) → mid, Job 3 (fast+mid busy) → slow. Exactly the expected fallback ladder.

### Added — Global Project Context (Concept tab)

A new "Enable Global Project Context" section on the Concept tab lets the user specify environmental context (time of day, season, weather, custom free-text) that's injected into every LLM enhance call as a MANDATORY context. **OFF by default** — users must explicitly toggle it on, matching the user's spec.

- `ConceptData` gained 5 fields: `global_context_enabled`, `global_context_time_of_day`, `global_context_season`, `global_context_weather`, `global_context_custom`. All persist independently on `Project.settings` so the user can pre-fill the dropdowns and only flip the toggle when ready.
- 11 time-of-day presets (dawn → midnight, including golden hour, twilight), 6 seasons (spring/summer/fall/winter/monsoon/dry_season), 14 weather conditions (sunny → dust storm, including fog, mist, thunderstorm).
- New `resolve_global_context(settings)` helper in `backend/api/concept.py` translates enum keys into rich LLM-facing phrasing (e.g. `morning` → `"morning (clear bright daylight, fresh feel, soft shadows)"`) and returns `""` when disabled or empty.
- LLM injection in `backend/api/generation.py::_build_auto_enhance_context` — when enabled, the resolved string gets injected as `⚠️ MANDATORY GLOBAL PROJECT CONTEXT (applies to EVERY scene unless explicitly overridden by per-scene direction)`. Same enforcement level as the per-project color override.

### Added — Batch mode now exposes everything added since it was last revisited

Audit caught 6 batch-mode gaps. All closed:

- **SRT upload per item.** New `/api/batch/upload-srt` endpoint mirrors `upload-audio`. `BatchItemConfig.srt_upload_path` carries it through; at audio-analyze time the pipeline parses the SRT (`AudioAnalyzer._parse_srt_to_words`) and substitutes its words for Whisper output as the authoritative timing source.
- **Disable Whisper toggle.** `BatchItemConfig.disable_whisper`. When true AND an SRT is attached, the pipeline passes `whisper_mode="skip"` to `analyze_full`. When the toggle is on without an SRT, a `WARNING` logs and Whisper runs anyway.
- **`narration_images` render type.** Previously only `narration_video` was selectable. Three-radio option now.
- **Model-generated audio (LTX 2.3 AV-native).** `BatchItemConfig.enable_model_audio` — writes `Concept.enable_model_audio = True` after concept generation so every I2V uses the AV-native workflow.
- **Color scheme override.** `BatchItemConfig.color_scheme` (free-text) — written to `Project.settings.color_scheme` at project create AND re-applied after `base-on-lyrics` so the LLM doesn't wipe it.
- **Image post-process filter.** `BatchItemConfig.image_filter` (`none` / `grayscale` / `bw` / `sepia`) — written to `Project.settings.image_filter` for the export FFmpeg post-process.

Frontend: `BatchItemAddModal` got an SRT file picker (narration-mode-only, with X-to-clear), Disable Whisper checkbox (greyed until SRT loaded), narration_images radio, Color Scheme input, Image Filter dropdown, and Model-Generated Audio checkbox.

### Added — Generation Queue scene names persist + are clickable

Two complementary improvements:

- **Clickable scene chips.** Clicking "Scene 79" in any job card on the Generation Queue selects that scene in the timeline, seeks the playhead, and switches the SceneEditor tabbed panel — same pattern Story Flow scene titles use (`AppLayout.tsx::goToSceneInTimeline`). Hover state, keyboard focus ring, `e.stopPropagation()` so the parent card's cancel/retry/delete still work.
- **Scene name persistence on done/deleted-scene jobs.** `JobResponse` now carries an optional `scene_name` field, bulk-resolved server-side via a single `SELECT id, name FROM scenes WHERE id IN (...)` query at every list / get / retry endpoint. If the scene has since been deleted, the response falls back to `job.parameters["scene_name"]`. Frontend `JobCard` prefers `job.scene_name` over the local `scenes` array lookup, so the chip remains visible (in non-clickable gray) even after scene deletion or project switching. Zero schema migration — `Scene` row stays the source of truth, with a `parameters` fallback path for future snapshot writes.

### Files (1.8.16)

- `backend/api/timeline.py` — DP pre-split overlong phrase groups; phrase-respecting clamp in fallback.
- `backend/api/jobs.py` — `JobResponse.scene_name` field + bulk resolver in list_jobs / get_job / retry_job.
- `backend/services/comfyui/dispatcher.py` — `ComfyWorker.priority`; `apply_user_caps` reads it; `select_worker` sort key updated.
- `backend/api/concept.py` — 5 `global_context_*` fields; `resolve_global_context` helper; `GLOBAL_CONTEXT_*` preset dicts.
- `backend/api/generation.py` — global context injection in `_build_auto_enhance_context`.
- `backend/api/batch.py` — `BatchItemConfig` 6 new fields; `/upload-srt` endpoint; SRT word substitution + `whisper_mode="skip"` plumbing; project settings seeding at create + re-apply after `base-on-lyrics`; `Concept.enable_model_audio` set post-concept.
- `frontend/src/components/Layout/AppLayout.tsx` — Auto-Gen modal close-time reset for terminal states.
- `frontend/src/components/AudioSetup/AudioSetup.tsx` — SRT upload cache write injects `source='srt'` + invalidates lyrics query.
- `frontend/src/components/Settings/SettingsPage.tsx` — PRIO number input per ComfyUI server row.
- `frontend/src/components/ConceptPanel/ConceptPanel.tsx` — Global Project Context UI block (enable checkbox + 3 dropdowns + custom textarea).
- `frontend/src/components/BatchMode/BatchItemAddModal.tsx` — SRT picker, Disable Whisper, narration_images radio, Color Scheme, Image Filter, Model Audio checkbox.
- `frontend/src/components/GenerationPanel/GenerationPanel.tsx` — clickable scene chip + `job.scene_name` preference.
- `frontend/src/api/client.ts` — `uploadBatchSrt` helper; ConceptData GET/PUT types extended with `global_context_*`.
- `frontend/src/types/index.ts` — `BatchItemConfig` types extended; `Job.scene_name` added.
- `VERSION`, `pyproject.toml`, `backend/main.py` — bumped to 1.8.16.

## [1.8.15] - 2026-06-15

### Fixed — Doubled chapters after Reprocess Audio (long-running root-cause hunt)

Lorenzo reported that re-uploading audio + SRT + clicking "Reprocess Audio" produced doubled chapters. Three earlier rounds of attempted fixes (NULL/empty source backfill, widened SQL filter, nuclear pre-clean wiping every non-manual row, verify+raw-DELETE escape hatch) cleaned the rebuild path itself but the doubling kept showing up in the UI.

The real bug was uncovered with a new chapter diagnostic (`tools/diag_chapters.py`) that dumped the raw chapters table. The state was:

| Name | Source | Time |
|------|--------|------|
| Chapter 1 | manual | 0–190 |
| Chapter 2 | manual | 190–380 |
| Chapter 2 | **auto** | 194.4–407.3 |
| Chapter 3 | manual | 380–580 |
| Chapter 3 | **auto** | 407.3–608.8 |
| Chapter 4 | manual | 580–763.6 |
| Chapter 4 | **auto** | 608.8–809.8 |

Root cause: `_create_auto_chapters` blindly numbered new chapters starting from 1 regardless of any pre-existing manual rows. When a project had manual chapters that covered most of the timeline but didn't extend to the audio end, the auto-creator filled the tail with rows named "Chapter 2", "Chapter 3", "Chapter 4" at slightly different time ranges. The dedup (which now keys on `(name, depth, parent, start_time_rounded_to_0.1s)`) correctly did NOT collapse them — they're structurally distinct rows referring to distinct timeline positions — but to the user they looked like duplicates because the names matched.

Fix in two places:

- `backend/services/chapters/builder.py::_create_auto_chapters` — at the top, after auto-row cleanup, check for existing `source='manual'` rows. If present: extend the last manual chapter's `end_time` to the project's audio end so tail scenes bind to it, then return without creating any auto rows. Respects the user's manual structure as the source of truth.
- `backend/main.py` lifespan — new auto-vs-manual collision sweep on every boot. Finds `(name, depth, parent)` collisions where one row has `source='auto'` and another has `source='manual'` in the same project. Unbinds scenes from the auto row, re-parents any sub-chapters, raw-DELETEs the auto row via `exec_driver_sql`, then calls `bind_scenes_to_chapters_by_time` to rebind the orphan scenes to the surviving manual sibling. Self-heals existing DBs on first restart.

### Added — Defense-in-depth chapter integrity

Several safeguards built during the 1.8.14 → 1.8.15 hunt remain as belt-and-suspenders:

- **Per-project `asyncio.Lock` around `rebuild_chapters`**. Prevents concurrent rebuilds (e.g. analyze_audio's onSuccess firing suggestTimeline twice in quick succession) from both passing the nuclear pre-clean and inserting N rows each. Second caller waits for the first to commit; logs a `WARNING` so the concurrency is visible.
- **In-line auto-dedup at end of `rebuild_chapters`**. After the build + bind + auto-split, walks every chapter row and groups by `(name, depth, parent_chapter_id, start_time_bucket=0.1s)`. Any duplicate cluster collapses to its oldest row (by `created_at`) via raw `exec_driver_sql`. Critically, after the DELETE, re-runs `bind_scenes_to_chapters_by_time` so scenes that lost their chapter_id get rebound to the survivor — without this rebind a dedup left orphan scenes (the regression Lorenzo saw with "4 scenes not in chapter 4").
- **Standalone `deduplicate_project_chapters(session, project_id)` helper** in `backend.services.chapters`. Same dedup + rebind logic, callable from anywhere. Used by the startup sweep and available for manual recovery.
- **Verify-and-raw-DELETE escape hatch** after the nuclear pre-clean. If a session-level `DELETE FROM chapters` reports `rowcount=N` but the next SELECT in the same session still sees rows (SQLite-async transaction-visibility edge case), the function drops down to the raw connection and forces the DELETE through.

### Added — "Disable Whisper Detection (SRT Required)" toggle

For projects with an authoritative SRT (ElevenLabs, Aivo, etc.), Whisper transcription is now optional. Toggle lives on the Audio tab under the Upload SRT button. When enabled AND an SRT is loaded, the next "Reprocess Audio" skips Whisper entirely and uses the SRT cues directly as the narration timing source.

- Backend `analyze_audio` reads `Project.settings.disable_whisper`, verifies an SRT is loaded (presence of `block` keys in `Lyrics.words`), and passes `whisper_mode="skip"` to `analyze_full`. Falls back to running Whisper if the toggle is on but no SRT is loaded.
- `analyze_full` honors `whisper_mode == "skip"` by returning an empty transcription list, skipping the meaningful-words check, and letting the SRT path upstream substitute its words into the analysis result.
- Frontend `AudioSetup` adds a labelled checkbox below the Upload SRT button. Disabled (greyed out) until an SRT is loaded — surfaces what the toggle requires before the user wastes a click on it. Persists to `Project.settings.disable_whisper` via `updateProject` and syncs the local Zustand store immediately so the checkbox state survives a re-render.

Saves the 30–90s Whisper pass on each Reprocess and avoids Whisper-vs-SRT timing conflicts entirely when the user has the authoritative timing source.

### Added — Clickable scene names in the Generation Queue

Mirroring the Story Flow scene-title navigation pattern (`AppLayout.tsx::goToSceneInTimeline`): clicking "Scene 79" in the Generation Queue now selects that scene in the timeline, seeks the playhead to its start, and the SceneEditor's tabbed info panel switches to show that scene's data.

- `frontend/src/components/GenerationPanel/GenerationPanel.tsx` — `getSceneName` replaced with `getSceneForJob` which returns the full Scene object. The chip is now a `<button>` with `setActiveScene + setPlaybackPosition + setIsPlaying(false)`, hover state (purple underline), keyboard focus ring, and tooltip. `e.stopPropagation()` keeps the parent card's cancel/retry/delete buttons working. Falls back to non-clickable gray text if the scene was deleted but the job still references it.

### Added — `tools/diag_chapters.py` debug script

Standalone diagnostic that dumps the chapters table for the most recently-updated project (or any project ID passed as an argument). Prints:

- Project mode + lyrics state (`initial_text` length, `# header` line count, words count, SRT block count, unique blocks).
- Full chapter table with name, depth, parent, source, time range, scene count, ID prefix.
- Duplicate groups under the dedup key `(name, depth, parent, start_time_bucket)`.
- Scenes not bound to any chapter (orphans), with their times and order_index.
- Total scene count.

Usage: `python tools/diag_chapters.py > chapters.md`. Paste the output anywhere a chapter symptom needs to be diagnosed — the snapshot replaces several rounds of "what's actually in the DB?" guesswork.

### Files

- `backend/services/chapters/builder.py` — per-project `asyncio.Lock` + `_get_rebuild_lock`; `rebuild_chapters` delegates to `_rebuild_chapters_locked` under the lock; manual-respect short-circuit at top of `_create_auto_chapters`; in-line auto-dedup with time-bucket key + post-dedup rebind; standalone `deduplicate_project_chapters` helper.
- `backend/services/chapters/__init__.py` — exports `deduplicate_project_chapters`.
- `backend/main.py` — chapter dedup sweep + auto-vs-manual collision sweep in lifespan.
- `backend/api/timeline.py` — `disable_whisper` gate in `analyze_audio`; substitutes SRT words when Whisper is skipped.
- `backend/services/audio/analysis.py` — `analyze_full` honors `whisper_mode="skip"`.
- `frontend/src/components/AudioSetup/AudioSetup.tsx` — Disable Whisper toggle UI; persists via `updateProject`.
- `frontend/src/components/GenerationPanel/GenerationPanel.tsx` — clickable scene-name chip.
- `tools/diag_chapters.py` (new) — chapter integrity diagnostic.
- `VERSION`, `pyproject.toml`, `backend/main.py` — bumped to 1.8.15.

## [1.8.14] - 2026-06-14

### Fixed — Narration / video duration drift ("everything defaults to 10 seconds")

Lorenzo reported that in narration modes scenes "default to 10 seconds which doesn't work with the narration. It also slowly makes it so the narration and concepts in the generated videos and images no longer make sense with each other."

Root cause was three silent 10-second fallbacks compounding with stale scene boundaries after Whisper / SRT re-analysis:

- **`GenerateVideoRequest.duration` and `AutoGenerateRequest.duration`** defaulted to `10.0` when the frontend omitted the field — whatever the scene's actual `end_time - start_time` was got silently overridden. Both fields are now `Optional[float] = None`; absent values resolve server-side via the new `_resolve_video_duration(session, scene, requested)` helper that returns the scene's actual range, then clamps to `AppSettings.video_min_duration` / `video_max_duration`.
- **Dispatcher silent fallback** at `dispatcher.py` `params.get("duration", 10.0)` and the LTX-sequencer `params.get("duration", 5.0)` now log a `WARNING` when the field is missing or non-positive, and fall back to the project's `video_min_duration` (not the hardcoded 10/5). Surfaces upstream bugs instead of papering over them.
- **No min/max clamp on manual scene create/edit.** `POST /scenes` and `PATCH /scenes` now run every start/end pair through `_clamp_scene_duration(...)`, which reads `AppSettings.video_min_duration` / `video_max_duration` and trims/pads `end_time` to fit. Start time is never moved (would change which Whisper words land in the scene — too destructive). Each clamp logs a warning so the user can see what happened in `diag.md`.

### Added — Stale-boundary detector + auto-resync on narration source change

The "narration and concepts drift apart" symptom was caused by Whisper re-transcribing the narration audio (or a fresh SRT upload arriving) without ever re-running Suggest Timeline. The DB kept the original scene boundaries from the first pass; the new Whisper / SRT word timing was different; `_get_scene_lyrics` then sliced the wrong words into each scene; the LLM prompt enhancer received the wrong narration text; the generated images / videos drifted away from what's actually being said.

New module `backend/services/scene_boundaries.py`:

- `source_label(words)` — detects whether `Lyrics.words` came from Whisper or SRT (SRT-parsed words carry a `block` integer).
- `cue_ranges(words)` — for SRT sources, groups words by `block` so the resync can snap to actual cue boundaries.
- `natural_break_points(words)` — SRT → every cue start/end; Whisper → every >300ms inter-word gap.
- `closest_break(boundary, breaks)` — returns nearest natural break point + distance.
- `audit_scene_boundaries(scenes, words, min, max)` — full per-scene report: out-of-bounds duration, distance from nearest natural break, snap suggestion when SRT is present.
- `needs_auto_resync(audit)` — heuristic: SRT mismatch ALWAYS triggers (cues are authoritative); Whisper triggers when stale fraction ≥30%.

Auto-resync fires from two places in `backend/api/timeline.py`:

- After `analyze_audio` (`POST /api/projects/{id}/analyze`) commits the new Lyrics row — for narration projects, audits boundaries against the new Whisper words and snaps each scene's start/end to the closest phrase boundary when stale.
- After `upload_srt` (`POST /api/projects/{id}/upload_srt`) commits the parsed cues — always snaps narration-mode scenes to cue boundaries (SRT > Whisper precision).

The resync is gated to `narration_video` / `narration_images` projects — music video mode uses LLM-picked cuts and is left alone. Failure is non-fatal: a snap error never breaks the analyze / upload endpoint.

### Added — `/scenes/audit-boundaries` debug endpoint

`GET /api/projects/{id}/scenes/audit-boundaries` returns the same audit dict the auto-resync uses. Per-scene `duration_status` (`ok` / `below_min` / `above_max`), `start_drift_s` / `end_drift_s` against the nearest natural break, and `snap_suggestion` when SRT is present. Used by `diag.py` and any future "fix boundaries" UI; safe to hit from the Settings page to see how many scenes have drifted.

### Why SRT is preferred over Whisper

SRT files come from authoritative sources (ElevenLabs, Aivo, etc.) — the cue boundaries are the *intended* phrasing of the narration, not a probabilistic Whisper guess. When `Lyrics.words` carry the `block` key, every downstream consumer (audit, resync, scene slicing) treats those as ground truth and aligns boundaries to cue starts/ends. Whisper-sourced words still get audited, but with a higher stale-fraction threshold (30%) since Whisper phrase boundaries are noisier and a few manually-tuned scenes shouldn't trigger a wholesale resync.

### Files

- `backend/api/generation.py` — `GenerateVideoRequest.duration` / `AutoGenerateRequest.duration` → `Optional[float] = None`; `_resolve_video_duration` helper at top of module; consumers in `generate_video` (`/generate-video`) and `generate_asset` (`/asset`) updated.
- `backend/services/jobs/dispatcher.py` — sequencer path + i2v path both log + clamp when `duration` is missing/non-positive; falls back to `AppSettings.video_min_duration`.
- `backend/api/scenes.py` — new `_clamp_scene_duration` helper; `POST /scenes` and `PATCH /scenes` both call it; new `GET /api/projects/{id}/scenes/audit-boundaries` endpoint.
- `backend/services/scene_boundaries.py` (new) — SRT-vs-Whisper detection + audit + resync heuristic primitives.
- `backend/api/timeline.py` — `_maybe_resync_scene_boundaries` helper; auto-resync hooks in `analyze_audio` + `upload_srt`.

### Notes

- Old projects with already-drifted scenes need one re-upload of audio (or SRT) to trigger the resync. After that, drift can't accumulate again.
- The dispatcher `WARNING` log line is the canary for any remaining silent-fallback bug. If you see `[<job_id>] Video job has non-positive / missing duration` after this release, that's a caller that needs fixing — please report.

## [1.8.13] - 2026-06-14

### Fixed — Auto-gen stuck at N/M with "status flipped to None"

User reported Auto-Gen sitting at "13 / 77 scenes" forever even though ComfyUI workers were still completing jobs. Backend log showed the windowed-batch poll loop logging `"Windowed batch: exiting poll loop — status flipped to None"` mid-run.

Root cause: a previous Auto-Gen run's 30-minute `_evict_seq_auto_job(pid)` eviction task fired during the current run and popped the live tracking dict entry. The poll loop's status check then returned `None` (not `"running"`) and exited; the queue/dispatcher kept processing in-flight jobs (giving the illusion of partial progress) but no new Pass 1 jobs got submitted.

Fix in `backend/api/generation.py`:

- Stamped a per-run `_evict_id` UUID on `_seq_auto_jobs[pid]` at run start (both `start_sequential_auto_gen` and `_resume_sequential_auto_gen`).
- `_schedule_eviction(pid, evict_id)` passes the token into the eviction coroutine.
- `_evict_seq_auto_job(pid, evict_id)` only pops when (a) the entry still exists, (b) `entry["_evict_id"] == evict_id` (same run we scheduled cleanup for), AND (c) the entry is in a terminal state.
- Stale evictions log `"Eviction for {pid} skipped — entry replaced by newer run"` instead of silently killing the live run.

## [1.8.12] - 2026-06-08

### Added — Scene delete dialog with merge-target selection

User feedback: *"I think we def may want to think through what happens when you scene delete and ask the user if they want the time and lyric to move to the previous or next scene."*

Before: clicking Delete on a scene fired a browser `window.confirm` and the AppLayout client did a quick "expand neighbor + delete" sequence in two API calls. No preview of what was about to be deleted, no choice of where the time slot should go, no re-numbering of `order_index`, no re-slicing of the absorbing scene's audio, no audit log on the absorbing scene.

Now: a proper `SceneDeleteModal` opens with three radio choices.

| Choice | Behavior |
|---|---|
| **Add to previous scene** (default) | `prev.end_time = deleted.end_time` — the previous scene absorbs the deleted time range. Lyrics that fell in the range are picked up automatically (Whisper words are time-anchored, not scene-anchored). |
| **Add to next scene** | `next.start_time = deleted.start_time` — the next scene extends backward to cover the range. |
| **Just delete (leave a gap)** | No neighbor changes. Export pipeline renders the gap as a silent freeze-frame on the previous scene's last frame. |

First/last scene edge cases auto-disable the invalid option. Solo-scene case offers only "leave a gap" with a warning that the project will be scene-less.

### Backend — atomic delete + merge in a single endpoint

`backend/api/scenes.py` `DELETE /api/projects/{pid}/scenes/{sid}`:

- New optional JSON body: `{"merge_target": "previous" | "next" | "gap"}` — defaults to `"previous"` so callers that don't send a body get the same merge semantics as before.
- Loads the project's scenes ordered by `order_index`, finds the deleted scene's neighbors.
- Edge auto-fallback: `previous` on the first scene falls through to `next`; `next` on the last scene falls through to `previous`; solo scene drops to `gap`.
- When merging, the absorbing scene's `start_time`/`end_time` updates and gets two scene-parameter flags so the UI can show "this scene was extended":
  - `extended_via_delete = true`
  - `extended_at = [{from_scene_id, from_scene_name, absorbed_seconds}, ...]` (rolling last 10 entries)
- Best-effort audio re-slice: ffmpeg subprocess cuts a fresh per-scene WAV at the new time range and updates `parameters.audio_clip_path`. Failure is non-fatal — logs a warning and the delete still succeeds; the user can re-run audio analysis to regenerate the clip.
- Cascade delete via the Scene model relationships handles TimelinePosition, StemSelection, GenerationHistory, Job rows automatically.
- After delete, `order_index` is re-numbered on the remaining scenes so the sequence is contiguous (0, 1, 2, …) — no gaps in the index even after multiple deletes.
- All in one DB transaction — the merge, re-slice, delete, and re-number commit together so a failure rolls back atomically.

### Frontend

- **`frontend/src/api/client.ts`** — `deleteScene(projectId, sceneId, opts?: { merge_target })` with new `SceneMergeTarget` type. Defaults to `"previous"` so existing call sites without the new arg keep working.
- **`frontend/src/components/SceneEditor/SceneDeleteModal.tsx`** — new component. Shows the scene's time range + duration, a lyric/narration preview for the deleted span (so the user can see what's about to be absorbed), three radio options with live previews of the resulting neighbor durations, and a note about the asset library + video-mismatch caveat.
- **`frontend/src/components/Layout/AppLayout.tsx`** — `handleDeleteScene` now opens the modal instead of calling `window.confirm`. New `handleDeleteSceneConfirm` callback fires the single backend call with the chosen `merge_target`, then invalidates `['scenes', id]`, `['lyrics', id]`, and `['chapters', id]` so React Query refetches everything that could have been affected by the merge.

### Mode-agnostic

Works the same in `music_video`, `narration_video`, and `narration_images` — the merge logic only touches `start_time`/`end_time` and the per-scene audio clip. No mode-specific branches.

### Verified

- `backend/api/scenes.py` parses OK; one `delete_scene` async def, one `_reslice_audio_for_scene` helper (no duplicates).
- Frontend TypeScript compiles clean.
- The 3 cascade relationships on Scene (TimelinePosition, StemSelection, GenerationHistory, Job) handle child-row cleanup automatically — no manual DELETE statements needed.
- Backward-compat: callers that don't send a body (and there's exactly one — `AppLayout.tsx`, which now always sends the body via the new modal) default to `merge_target="previous"` which matches the prior behavior.
- Solo-scene delete handled (modal shows "leave a gap" with a warning; backend's `effective_target` falls through to `gap` regardless of input).
- First-scene + last-scene edge cases handled both in modal (radio disable) and backend (auto-fallback).

### Changed

- VERSION → 1.8.12. `pyproject.toml`, `backend/main.py` FastAPI version updated.

---

# Changelog

## [1.8.11] - 2026-06-08

### Fixed — Chapter backfill failed for 4 projects at every startup

User reported log spam at backend boot:
```
WARNING: Backfill default chapter for project ... skipped:
(sqlite3.IntegrityError) NOT NULL constraint failed: chapters.description
```
…repeated for 4 different projects, every single startup. The user's affected projects had **no default chapter row at all** — broken chapter UI, no chapter scope filtering, no chapter-scoped Auto-Gen / Export for those projects.

**Root cause** — the startup migration at `backend/services/shortcode.py:311-329` builds the default "Chapter 1" row with an INSERT that omits `description`, `character_focus`, and `style_notes`. These columns were added in 1.8.0 as part of the Chapter Direction Panel and the migration was `ALTER TABLE chapters ADD COLUMN description TEXT DEFAULT ''`. On some users' DBs the column ended up `NOT NULL` without an effective runtime default — likely because the column was first created by a `SQLModel.metadata.create_all` against a fresh DB on a version that already had the field in the model, where SQLModel translated `Field(default="")` to `NOT NULL` but the SQLite engine didn't always honor the Python-side default for inserts that omit the column. Result: `IntegrityError` every time the backfill ran, every startup forever.

**Fix** — `backend/services/shortcode.py` — INSERT now includes the three fields explicitly:
```sql
INSERT INTO chapters (... description, character_focus, style_notes, ...)
VALUES (... '', '[]', '', ...)
```
Works regardless of which schema variant the user's DB has. The 4 projects that have been failing for some time will now get their default chapter created on next backend start, unblocking their chapter UI.

### Diagnostic — what the user's log was telling us

For each affected project, the user was seeing:
```
WARNING: Backfill default chapter for project <pid> skipped:
(sqlite3.IntegrityError) NOT NULL constraint failed: chapters.description
```
Translation: "Backend started. Tried to create a default Chapter 1 for project X. SQLite refused because the description column requires a value and we didn't provide one. Skipping — leaving project X without a chapter." Repeated every boot because the migration tries again every time. The fix makes the INSERT explicit so it succeeds.

The other warnings/errors in the user's log were **expected and harmless**:
- `Worker http://127.0.0.1:8188: Failed to connect ...` — local ComfyUI not running, fine if the user has remote workers (they do: `192.168.68.117:8188` + `192.168.68.106:8188` both connected successfully).
- `Orphan sweep: no stale jobs (>1h old) found` — orphan sweep ran clean, nothing to do.
- `Demucs GPU: CUDA available (NVIDIA GeForce RTX 5070)` — GPU detected, good.

### Verified

- `backend/services/shortcode.py` parses OK.
- INSERT now contains `description, character_focus, style_notes` columns + empty defaults.
- Existing INSERTs that already work (fresh DBs with the column defaults applied) continue to work — adding explicit values is always safe.
- VERSION → 1.8.11. `pyproject.toml`, `backend/main.py` FastAPI version updated.

---

# Changelog

## [1.8.10] - 2026-06-07

### Fixed — Two-pass silently downgraded to single-pass with no characters

User reported: "toggled two-pass off, then back on, now scenes generate with nobody in them." Root cause traced through the pipeline:

1. **SceneEditor** sends generate request with `two_pass: true` and `reference_asset_ids: []` (no per-scene character selection).
2. **`backend/api/generation.py` `/generate-image`** falls back to concept characters: iterates `project.settings["characters"]`, looks up each character's `image_path` against `Asset.rel_path` with **strict equality**. Any subtle mismatch (leading slash, project_id prefix variant, whitespace) → lookup fails → `char_ref_ids` stays empty → `_two_pass_effective = False` → downgrades to single-pass.
3. **Pass 1 job** is created as single-pass `klein_t2i` with no refs. The scene image generates without characters.
4. **No log** told the user WHY two-pass downgraded — looked like the toggle just stopped working.

This is a long-standing latent bug that the user's toggle-off-then-on sequence happened to expose. Same lookup pattern existed in `_resolve_character_asset_ids` used by auto-gen, so auto-gen could hit it too.

### Fix — Forgiving 3-tier character asset lookup

Both lookup sites (`/generate-image` concept fallback + `_resolve_character_asset_ids` for auto-gen) now try in order:

1. **Exact `rel_path == image_path`** (fast path, matches frontend's primary lookup)
2. **Suffix match `rel_path LIKE '%image_path'`** (forgives leading slashes, project_id prefix variants, path encoding differences — matches frontend's `endsWith()` fallback)
3. **Basename-only match `rel_path LIKE '%filename'`** (last resort — if the path structure changed entirely but the filename is intact)

If ALL characters fail to resolve, a **clear warning is logged** so the user can see why two-pass downgraded:

```
Two-pass requested but ALL N character image_path lookups failed.
Paths tried: [...]. Either the characters have no image_path, the
Assets were deleted, or the rel_path doesn't match. Two-pass will
downgrade to single-pass and the image will have no characters.
```

### Verified

- `backend/api/generation.py` parses OK.
- Both code paths affected: `/generate-image` per-scene endpoint AND `_resolve_character_asset_ids` (auto-gen).
- Frontend lookup semantics now match backend so refs survive the request boundary.
- VERSION → 1.8.10. `pyproject.toml`, `backend/main.py` FastAPI version updated.

### Notes

- Existing scenes where the asset resolves successfully via exact match are completely unaffected (fast path unchanged).
- If a user's character's image_path is genuinely missing from Assets (e.g., the asset was deleted), the warning surfaces that fact instead of silently producing a no-character image.
- Combined with the 1.8.9 "respect per-scene characterIndices" change, the system now correctly handles all four combinations: scene-explicit refs, project-default refs, no refs at all, AND the edge case where concept characters have slightly mismatched image_paths.

---

## [1.8.9] - 2026-06-07

### Fixed — Auto-gen no longer force-overrides per-scene character selection

User reported that two-pass was firing on scenes that shouldn't need it. Root cause: auto-gen Phase 1 was unconditionally writing `image_refs_first.characterIndices = [0, 1]` (first 2 project chars) onto every scene before computing refs. This overwrote any per-scene character selection the user had made (including the legitimate "no characters on this scene" case = empty list), so every scene ended up with refs → every scene got two-pass → 2 image renders per scene whether the user wanted that or not.

**`backend/api/generation.py`** in TWO auto-gen paths (`_run_windowed_batch` Phase 1 + the sequential auto-gen loop):

- **Reads existing `image_refs_first.characterIndices` first.** If the user has an explicit per-scene selection — including an empty list meaning "no characters on this scene" — use ONLY those characters.
- **Falls back to "first 2 project chars" only when the field is absent** (truly new scene with no prior selection). Brand-new scenes still get a sensible default.
- **Stops overwriting `image_refs_first` on scenes that already have one.** The auto-gen no longer touches the field unless it's missing.

End-to-end effect:

| Scene state | Old behavior | New behavior |
|---|---|---|
| User selected 1 character on scene | overwritten to use first 2 → two-pass with 2 chars | uses 1 char → two-pass with 1 char ✓ |
| User selected NO characters (empty list) | overwritten to use first 2 → two-pass | NO chars → no refs → **single-pass** ✓ |
| Brand-new scene, no selection | uses first 2 → two-pass | uses first 2 → two-pass (unchanged) |
| No project characters configured | empty refs → single-pass (unchanged) | empty refs → single-pass (unchanged) |

This is the rule the user asked for: **"two-pass runs if the scene has references; if the scene has no references, no two-pass — regardless of the modal checkbox."** The two-pass checkbox is now an UPPER bound (turn it OFF to disable two-pass entirely), not an override that forces refs to appear.

The downstream short-circuits in `_apply_two_pass_to_job_params` (`if not two_pass or not ref_ids: return params`) were already correct — the bug was that ref_ids was always non-empty due to the unconditional overwrite. Both layers now agree.

### Changed

- VERSION → 1.8.9. `pyproject.toml`, `backend/main.py` FastAPI version updated.

### Verified

- Backend Python parses OK; frontend TypeScript compiles clean.
- Three patches applied: windowed-batch resolution, sequential-path resolution, both characterIndices override sites guarded with `"image_refs_first" not in scene_params`.

---

## [1.8.8] - 2026-06-07

### Fixed — User-reported regressions

**Project deletion failed with `sqlalche.me/e/20/gkpj` (IntegrityError)** — the GlobalCharacter table I added in 1.8.6 declares `source_project_id` with `ondelete="SET NULL"`, but `SQLModel.metadata.create_all` only creates MISSING tables, never alters existing ones. Users whose DB was created before that fix had the old constraint shape and FK enforcement blocked the project cascade delete.

- **`backend/api/projects.py` `delete_project`** now pre-nulls `source_project_id` on every GlobalCharacter row referencing the project, BEFORE running the cascade delete. The cached `source_project_name` on each library entry preserves attribution after the project is gone (matches the "copy semantics — library entry outlives source project" design from 1.8.6). Works regardless of which DB schema variant the user is on.

**Two "Enable Model-Generated Audio" checkboxes on the Concept tab** — leftover from the AV-native checkbox patch being re-applied during an Edit-tool truncation repair earlier in this session. ConceptPanel had two complete blocks; one had stale wording ("scenes whose Video tab opt in..." — outdated since the master toggle is now the source of truth).

- **`frontend/src/components/ConceptPanel/ConceptPanel.tsx`** — removed the older duplicate block (2,329 chars). Single canonical Enable Model-Generated Audio toggle remains.

**Full Pipeline Single Image autogen silently generated 2 images per scene** — the AutoGenerate modal's `twoPass` checkbox defaulted to ON, so every scene with character refs got Pass 1 (base) + Pass 2 (composite). User got 2× the rendering work without asking for it. Same default in BatchItemAddModal.

- **`frontend/src/components/Layout/AppLayout.tsx:3125`** — `useState(true)` → `useState(false)`.
- **`frontend/src/components/BatchMode/BatchItemAddModal.tsx:20`** — same change. Two-pass is now strictly opt-in.

**"Drew an image without making a prompt for it first"** — auto-gen Phase 1's empty-prompt fallback chain was `scene.prompt or f"Scene {scene.order_index + 1}"`. When LLM enhance failed (timeout, misconfig, etc.) AND the scene had no prompt, the literal string `"Scene 7"` was sent to Klein and produced garbage. The `flow_idea` field generated earlier by `_ensure_video_flow` was being IGNORED in this fallback path.

- **`backend/api/generation.py` `_run_windowed_batch` Phase 1** — new fallback chain:
  1. `scene.prompt` (user-edited or successfully enhanced)
  2. `scene.parameters.flow_idea` (from story flow generation)
  3. SKIP the scene with a clear warning + status update if only the literal `"Scene N"` placeholder remains.

  When skipped, the status text shows `"skipped {scene_name} (no prompt / flow idea, LLM enhance failed)"` so the user knows exactly why. Re-running after fixing LLM config or writing a manual prompt picks the scene up cleanly. Saves the user from a wasted render that would have produced an image of "Scene 7" rendered literally.

### Changed

- VERSION → 1.8.8. `pyproject.toml`, `backend/main.py` FastAPI version updated.

### Verified

- All backend Python files parse OK.
- Frontend TypeScript compiles with zero new errors.
- Concept tab now has exactly 1 `Enable Model-Generated Audio` occurrence (was 2).
- Both `twoPass` defaults flipped to false; comment in source explains the opt-in rationale.
- Empty-prompt skip path in generation.py has `_prompt_is_placeholder` guard + `flow_idea` fallback (4 mentions in code).

---

## [1.8.7] - 2026-06-07

### Fixed — Auto-gen drain loop waited 30 min for ghost jobs

User reported "auto-gen ran one video successfully then stopped, status panel kept polling." Root cause traced to the post-Phase-2 drain loop: it polled the DB for any `PENDING`/`RUNNING` jobs on the in-batch scene IDs and waited them out — but had no time filter, so it would happily wait on orphaned jobs from PREVIOUS sessions whose ComfyUI workers were long gone. The drain would only give up after the 30-min `batch_timeout` fired.

- **`backend/api/generation.py` `_run_windowed_batch` drain query** now filters by `Job.created_at >= run_started_at`, so the drain only waits on jobs THIS run created. Two-pass composites and transition clips spawned during the main loop pass the filter; pre-existing orphans don't.
- **`backend/main.py` lifespan startup** now sweeps PENDING/RUNNING jobs older than 1 hour and marks them FAILED. `recover_running_jobs()` still handles fresh-restart reconnect (keeps RUNNING-with-prompt_id alive); the new sweep only cleans up stale orphans that recover left behind.

### Fixed — Image movement override discarded user's "static" choice

User changed scenes from `zoom_in_center` to "static" in the UI; the change persisted to the DB; export still rendered Ken Burns. This wasn't a cache bug — even a force-recreate produced movement.

- **`backend/api/export.py`** lines 607 + 641: removed the `"effect": effect if effect != "none" else "zoom_in_center"` override that was silently replacing the user's "none" choice with a default.
- **`backend/services/video/assembly.py`** `to_common()`: when effect is "none" / empty, sets `effect = "static"` (new value) instead of coercing to `zoom_in_center` with `intensity=0` (which still ran zoompan and could produce subtle motion).
- **`backend/services/video/ffmpeg.py`** `apply_kenburns()`: new static early-return path emits a clean image-to-video clip with NO zoompan filter — just `scale + pad + setsar + format` held for `duration` seconds, with explicit `-frames:v` for frame-exact splice timing.

### Fixed — Project-wide "Enable Model-Generated Audio" toggle now actually project-wide

User flipped the master AV-native toggle on the Concept tab, expecting every video in the project to use it. The dispatcher was requiring BOTH the project gate AND the per-scene checkbox.

- **`backend/services/jobs/dispatcher.py`** `_build_workflow` AV-native routing: changed `if _scene_av and _proj_av` → `if _proj_av or _scene_av`. Master toggle is now the single source of truth; per-scene checkbox is a secondary opt-in when the master is off.
- **`frontend/src/components/ConceptPanel/ConceptPanel.tsx`**: updated copy ("every I2V video render in this project will use AV-native") and added explanatory hint about per-scene fallback.
- **`frontend/src/components/SceneEditor/SceneEditor.tsx`** Video tab: per-scene checkbox now renders in a purple highlighted box with a `🔒 forced ON by project setting` badge when the master is on. Tooltip explains the gate hierarchy.

### Fixed — Auxiliary saveConcept calls were dropping 5 critical settings

User reported settings "reverting" intermittently. Root cause: ConceptPanel had FIVE saveConcept call sites but only TWO included the full payload. The other three (auto-save after adding character, auto-save after editing character, auto-save after generating character image) omitted `global_color_override`, `custom_color_palette`, `global_image_color_filter`, `enable_model_audio`, `model_audio_volume`. Every character-related auto-save silently flipped the master AV-native toggle back to OFF and wiped color palette.

- **`frontend/src/components/ConceptPanel/ConceptPanel.tsx`**: all 3 incomplete saveConcept call sites now include the 5 missing fields.
- **`frontend/src/api/client.ts`** `saveConcept` type extended to include the new fields.

### Fixed — Export cache key was missing color filter + per-scene dims

User changed a per-scene color filter; export reused the stale concat.mp4 because the cache key didn't hash `color_filter`. Same bug pattern as the "static" override.

- **`backend/services/video/assembly.py`** `_video_cache_key()`: scene payload now includes `cf` (color filter) + `iw`/`ih` (per-scene image dimensions). Changing any of these now correctly invalidates the cache and forces a fresh render.

### Fixed — Multiple silent-failure paths from the deep audit

**BLOCKING**

- **Pass 2 commit rollback** (`dispatcher.py` `_download_and_save_outputs`): when `_create_two_pass_composite_job` raises after Pass 1 is already saved, scene now gets `two_pass_composite_failed=true` flag + truncated error so the UI can surface a "Pass 2 failed — retry?" affordance. Previously Pass 1 would show as completed with no indication that Pass 2 never ran.
- **Whisper empty transcription raises instead of swallowing** (`backend/services/audio/analysis.py`): when both the full audio AND the vocal-stem fallback produce no meaningful words, the code now raises `RuntimeError` with actionable text. Previously it silently set `transcription = []` and the export would produce zero subtitles with no error.

**HIGH**

- **LLM calls wrapped in `asyncio.wait_for(timeout=180)`** in `backend/api/concept.py` (5 sites) + `backend/api/timeline.py` (1 site). Stalled LLM HTTP requests can no longer hang the request task forever.
- **Demucs timeout scales with audio duration** (`analysis.py:365`): `_demucs_timeout = max(1800, int(audio_dur * 2))`. A 2-hour narration on CPU now gets 4 hours of timeout instead of failing at 30 minutes.
- **Dispatch-time parameter validation** (`dispatcher.py` `_build_builtin_workflow`): raises `ValueError("Dispatch refused: ...")` BEFORE sending to ComfyUI when `width/height/duration <= 0`. Stops the silent 0-frame video / corrupt image output. Excludes `ltx_transition` (auto-derives dims).
- **Audio-only remix duration check** (`assembly.py` `_load_cached_concat` site): when `audio_only_remix=True`, the cached concat's actual duration is probed via ffprobe and the cache is dropped if `abs(actual - expected) > 0.5s`. Protects against interrupted-write manifest mismatches.
- **ConceptPanel unsaved-edits guard** (`ConceptPanel.tsx:213`): `useEffect([conceptData, dirty])` now only re-hydrates local state when `!dirty`. Background refetches (library import, etc.) no longer silently wipe in-progress user edits.

### Notes

- VERSION → 1.8.7. `pyproject.toml`, `backend/main.py` FastAPI version updated.
- Backend Python files all parse OK; frontend TypeScript compiles with zero new errors.
- All fixes are net-positive correctness with no behavior change for the happy path — they only kick in on the edge cases that previously failed silently.

---

## [1.8.6] - 2026-06-06

### Added — Global Character Library (reusable across projects)

For users building a series of related content (multiple music videos with the same protagonist, episodic narrations with recurring characters, etc.), characters can now be saved to a project-independent library and re-imported into any other project.

- **"💾 Save As Asset" button** on the Character Edit modal (footer). Click → opens a small dialog where you optionally add comma-separated tags ("protagonist", "noir", "fantasy"), then "Save to Library". The character's main image, description, prompt, all reference images, and the source project's name are copied into the global library folder so the entry is fully portable. Disabled when the character has no main image yet.
- **"🎭 Library" button** next to the Concept tab's "Add" character button. Opens a browse modal showing every saved character as a thumbnail grid with name, tags, and source-project attribution. Filter bar: name/description search + clickable tag chips ("All" + every distinct tag). When multiple source projects are represented, a left sidebar groups counts by project.
- **"+ Add to project"** on each library card copies the character into the current project's `settings.characters` list. **Copy semantics** — once imported, the project copy is fully independent: editing it does NOT touch the library entry, and updating the library entry does NOT push changes into projects that already imported it. Matches how stock-photo / clipart libraries work; least surprising for users.
- **Storage layout** — `{project_dir}/_global_characters/{id}/` holds the main image, plus `refs/` subfolder for reference images. The leading underscore prevents collision with user-named projects. Moving your `project_dir` brings the library along automatically.
- **Source project attribution** — `source_project_id` (FK, nullable) + `source_project_name` (cached at save time). If the source project is deleted, the library entry keeps the cached name so attribution survives.

### Added — backend API surface

`/api/global-characters`:
- `POST` — create from a payload (name, description, image_path, prompt, refs, tags, source_project_id). Copies files into the library folder.
- `GET` — list with `?search=` / `?tag=` / `?source_project_id=` filters.
- `GET /tags` — distinct tag list, sorted (for tag chip picker).
- `GET /{id}` — detail.
- `PUT /{id}` — update name / description / tags only.
- `DELETE /{id}` — removes DB row, folder, and version history. Does NOT affect projects that already imported the character.
- `GET /{id}/versions` — list version snapshots (frontend version-history UI is a follow-up).
- `POST /{id}/import` — copy into a target project. Returns the new `character_index` so the UI can scroll to the imported entry.

### Added — DB tables (auto-created on next backend start)

- `global_characters` — id (UUID PK), name (indexed), description, image_path, last_prompt, reference_images (JSON list), tags (JSON list), source_project_id (FK → projects.id, nullable, indexed), source_project_name, created_at, updated_at.
- `global_character_versions` — id (UUID PK), global_character_id (FK indexed), image_path, prompt, reference_images (JSON list), note, created_at. Populated when a library entry is regenerated (future "regenerate from library" flow).

No migration needed — `SQLModel.metadata.create_all` adds the new tables idempotently on first startup after upgrade. Existing data is untouched.

### Frontend

- `frontend/src/api/client.ts` — new `GlobalCharacter` + `GlobalCharacterCreate` types; client methods for list/create/delete/import + tag list.
- `frontend/src/components/ConceptPanel/CharacterCreatorModal.tsx` — Save As Asset button + tag-input sub-dialog.
- `frontend/src/components/ConceptPanel/GlobalCharacterLibraryModal.tsx` — new browse + import modal (search, tag filter, project group sidebar, grid with thumbnail/name/tags/source, + Add to project / 🗑 delete buttons).
- `frontend/src/components/ConceptPanel/ConceptPanel.tsx` — wires the 🎭 Library button + renders the modal.

### Notes — what's NOT in this cut

These were deliberately deferred to keep the v1 contained:
- **Version-history UI** in the browse modal (backend stores versions; the modal doesn't expose them yet).
- **"Regenerate from library"** — re-create variations using the saved prompt + refs.
- **Re-sync** — push an updated library entry into a project that previously imported it.
- **Folders** on top of tags — current organization is tag-based + auto-recorded source project.

The DB schema already supports versioning + attribution, so adding the UI later is purely frontend work.

### Changed

- VERSION → 1.8.6, `pyproject.toml`, `backend/main.py` FastAPI version updated.

---

## [1.8.5] - 2026-06-06

### Added — Model-Generated Audio (LTX 2.3 AV-native)

LTX 2.3 has a native AV-latent pipeline that produces audio (speech / SFX / ambient) in the same forward pass as the video — but only when the audio input is left unconditioned. Until now we always conditioned with the project's narration / backing audio, which trains the model toward lipsync but throws away the generative audio path entirely. New feature lets scenes opt into the unconditioned path so the model fills in its own sound.

- **New ComfyUI workflow** `workflows/LTX-2-3_AV_NATIVE.json` — derived from the I2V workflow with the audio-input chain surgically removed (`LoadAudio` / `LTXVAudioVAEEncode` / `SetLatentNoiseMask` / `TrimAudioDuration` / its int-to-float helper all dropped, 53 nodes total). The audio_latent switch now hardwires the empty-latent path so the sampler denoises audio from pure noise; the output audio switch hardwires the model-decoded path so the VHS_VideoCombine mux uses what the model produced. The "Audio - Video Duration" int constant is repurposed as the user-controllable "Video Duration (seconds)" since there's no input audio to derive it from.
- **Registration** in `backend/services/comfyui/defaults.py` as workflow_type `ltx_av_native` (name "LTX 2.3 - AV Native (model generates audio)"). Routed through the existing capabilities map (`{"ltx"}`) and model-requirements map (resolved to `video_model_type`, same as every other LTX flavor).
- **Dispatcher routing** in `_build_workflow.` When the project has `enable_model_audio` AND the scene's parameters say `use_model_audio`, any `ltx_i2v` job is auto-swapped to `ltx_av_native` and `skip_audio_mux=True` is forced. The swap happens at dispatch time rather than at submission time, so every code path that creates an `ltx_i2v` job (interactive Video tab, Auto-Gen, Batch Mode) gets AV-native routing for free without touching the submission sites.
- **Post-download audio extraction** in `_download_and_save_outputs`. When the completed video came from `ltx_av_native`, we ffprobe for an audio stream and (if present) ffmpeg-extract it to a sidecar WAV (48 kHz / 16-bit PCM / stereo) at `<video>.model_audio.wav`. The relative path is stored on `scene.parameters.chosen_model_audio_path` so the mixer can later route the channel independently of the muxed MP4. New helper `extract_audio_track()` in `backend/services/video/ffmpeg.py` does the probe + extraction with conservative fallbacks (empty / tiny WAVs return False so the assembler knows the scene has nothing to layer in).
- **Concept tab UI** — new "Enable Model-Generated Audio (LTX 2.3 AV-native)" checkbox + "Model Audio Mixer Volume" range slider (0–2×, 0.05 step) in `ConceptPanel.tsx`. Hidden when the global toggle is off so users don't get confused about why the per-scene checkbox is doing nothing. Saves through `concept.py` `ConceptData` fields `enable_model_audio: bool` and `model_audio_volume: float` (clamped to 0..2 server-side).
- **Per-scene Video tab UI** — new "Let model generate its own audio" checkbox in `SceneEditor.tsx` Video tab. Disabled (greyed + tooltip "Enable Model-Generated Audio on the Concept tab first") when the project gate is off, so the dependency is discoverable from the scene editor without having to navigate back.
- **Scene playback** of any AV-native scene immediately reflects the model audio in the per-scene preview because it's baked into the MP4 (no mixer plumbing required for single-scene preview). The full-export mixer integration that respects `model_audio_volume` is staged in but not wired into the assembly pipeline yet — follow-up to use `chosen_model_audio_path` as a 4th channel layered on top of the narration + backing mix.

### Changed

- README + VERSION bumped to 1.8.5. `pyproject.toml`, `backend/main.py` FastAPI version updated.

### Notes

- The AV-native model needs LTX 2.3's `LTX23_audio_vae_bf16.safetensors` VAE installed on your ComfyUI server (same file the existing I2V workflow uses for audio decoding — already required by your current setup).
- Workflow does NOT apply lipsync — there's no input audio to sync to. The "Lipsync" toggle on Video tab is independent and only affects non-AV-native jobs.
- Narration-images mode hides the per-scene checkbox (video gen is disabled in that mode entirely).

---

## [1.8.4] - 2026-06-06

### Fixed — auto-gen reliability + observability (the big one)

Most reported "auto-gen stuck doing nothing" reports tracked to **three independent silent-failure paths**, all now caught:

- **Phase 1 FF image failure used to kill the entire run.** A single first-frame image timing out or failing in Phase 1 set `_seq_auto_jobs[pid].status = "failed"` and `return`ed, killing a 23-scene run because of one bad scene. Now logs `SKIPPING this scene and continuing with the rest of the batch`, records the failure in BatchRun's error log, and `continue`s to the next scene so the other 22 still process. (`backend/api/generation.py` `_run_windowed_batch` Phase 1 FF wait path)
- **`_ensure_video_flow` LLM calls had no timeout.** Run pre-step that generates story-flow ideas could hang indefinitely if the LLM provider stalled, leaving the modal frozen at `current_step = "starting"` and `0/N` with no log activity. Each `_call_llm` invocation (single-shot + each concurrent batch) now wrapped in `asyncio.wait_for(..., timeout=180.0)`; the outer call gets a 10-minute backstop. Status text now updates to `"checking story flow ideas..."` then `"generating story flow ideas for N scenes (LLM)..."` before the LLM work so the user can see the step is active. Timeout falls through to raw prompts so Phase 1 always reaches scene gen.
- **Phase 2 main loop exited if `active_jobs` briefly empties.** Loop condition was `while active_jobs and elapsed < timeout`. Between a completed job and the refill attempt, `active_jobs` could go to 0 momentarily; if anything (transient DB lock) caused the refill to fail, the loop terminated. Loop now: `while (active_jobs or next_to_submit < total_eligible) and elapsed < batch_timeout` — exits ONLY when all eligible submitted AND nothing in flight.

### Added — diagnostic logging for every wait path

Silence in the log used to be indistinguishable from "running fine but slow." Now every wait point has a heartbeat:

- **Phase 1 per-scene log line** `Phase 1 [N/M]: 'scene_name' (elapsed=Xs total)` on every iteration entry
- **`_wait_for_job` heartbeat** every 30s: `_wait_for_job heartbeat: job=<uuid> status=PENDING|RUNNING elapsed=Xs/Ys`
- **Phase 2 main-loop heartbeat** every 20s: `Windowed batch heartbeat: tick=N, active=X, submitted=Y/Z, done=W, elapsed=Ts`
- **Phase 2 START log** at handoff: `Windowed batch Phase 2 START: mode=X, eligible=N`
- **Status text updated at every transition** so the modal shows what we're waiting on, e.g. `"waiting for FF image of Scene 4 (scene 4/23)"`, `"dispatching (N scenes ready, submitting first batch...)"`, `"generating (X active, Y/Z complete)"`

### Fixed — multi-fault tolerance throughout the dispatch pipeline

`_submit_next` increments `next_to_submit` BEFORE the DB write. Failures used to leak this counter — the failed eligible entry was permanently SKIPPED. All four sites now roll back on exception:

- **Initial fill** (`for _ in range(window_size)`) — on `_submit_next` exception, decrement `next_to_submit` and continue trying the next slot
- **Main-loop top-up** — tracks `_topup_failures_this_tick`, tolerates up to 3 failures per tick with 0.5s backoff, rolls back `next_to_submit` on each failure
- **Rescue pass** (runs when main loop exits with un-submitted entries) — tolerates up to 5 cumulative failures with 1s backoff, rolls back on each
- **Self-healing top-up** runs UNCONDITIONALLY every 2-second tick (no status-running gate) — `len(active_jobs) < window_size` is enough to trigger another refill attempt

### Changed — audio normalization target -16 → -14 LUFS

`backend/services/video/ffmpeg.py` `normalize_audio()` default target. Old -16 LUFS = broadcast/film standard; sounded "super quiet" vs every streaming platform (Spotify, YouTube, Apple Music, TikTok all use -14). Voice-heavy programs suffered extra because integrated loudness drops further with pause gaps. Both code paths (post-assembly for music_video, in-assembly for narration_video) now hit -14 LUFS when "Normalize audio" is enabled. True-peak ceiling of -1.5 dBTP unchanged.

### Added — FFmpeg image color filter (B&W / Grayscale / Sepia)

Independent of the LLM Color Override (which steers the prompt). This filter runs FFmpeg over the generated image AFTER the model produces it for a deterministic pixel transform.

- **Concept tab** — new "Force Color Filter on Generated Images (FFmpeg)" dropdown: `Off / Black & White (high contrast) / Grayscale (desaturated) / Sepia Tone`. Off by default.
- **Per-scene Image tab** — same dropdown with `Inherit from project (Off/B&W/etc.)` as default; explicit `Off` overrides project default for one scene.
- **Backend** — `apply_image_color_filter(input, output, mode)` in `backend/services/video/ffmpeg.py` (B&W = `hue=s=0,eq=contrast=1.25`, Grayscale = `hue=s=0`, Sepia = standard ImageMagick matrix). Tempfile + atomic move so in-place is safe. Called from `backend/services/jobs/dispatcher.py` after every image download.

### Fixed — character edit persistence + asset picker

- **Choose from asset library OR upload** added to the character image source (was: only Klein generation). Single "🖼️ Choose Asset / Upload" button right under Generate opens the asset picker with both tabs. Picked asset goes through the same `setActiveMutation` as "Set as Active" on a generated version.
- **Description + prompt + reference images persist** across save/close. `CharacterModel` Pydantic in `backend/api/concept.py` had only `name/description/image_path` — Pydantic silently stripped `last_prompt` and `reference_images` on every save. Added both as optional fields; modal hydrates them on mount; `handleSaveAndClose` passes them back through `onSave`. Reopen a character and the prompt + reference list are exactly as you left them.

### Fixed — color override + scene navigation

- **Scene Editor "Default Color Palette" inheritance label** showed "(no project default set)" even after saving on Concept tab. Cause: ConceptPanel only invalidated `['concept', projectId]` query but Scene Editor reads from `['project', projectId]` (`currentProject.settings`). All six save-related invalidations now invalidate both queries — Scene Editor's inheritance label updates immediately after any concept save.
- **Scenes panel** — clicking a scene title now navigates the Timeline to that scene's start position + sets it active + pauses playback. Title is its own button with hover state and tooltip `"Go to {scene name} in the timeline"`. Whole row still works for users who don't notice.

### Added — generation queue model + phase chips

Each in-flight job item in the Generation Queue panel now shows up to three header chips:
- **Pass 1/2 badge** (blue) — when `two_pass_phase` is set, with tooltip explaining each phase
- **Model badge** (color-coded) — `Z-Image Turbo`, `Klein 9B · 3REF`, `LTX 2.3 · I2V`, etc., derived from `job.parameters.workflow_type` (ground truth after Pass-1 Z-Image redirects). Raw workflow_type in tooltip.
- **Existing worker badge** + scene name unchanged

### Added — batch screen live active-jobs panel

`backend/api/batch_runs.py` `BatchRunDetail` response now includes `active_jobs[]` — live snapshot of every RUNNING job in the project with per-job progress %, current ComfyUI node, worker URL, scene name, two-pass phase, and workflow_type. Dispatcher writes into in-memory `_live_job_progress` dict on every WebSocket progress event; cleared on `mark_done`/`mark_failed`. Batch detail screen renders an "Active workers (N)" panel under current_step with progress bars updating live. 5-minute LTX renders no longer look "stuck" — you see the percentage climb.

### Added — persistent auto-gen status across browser refresh

`/auto-sequential/status` now falls back to the most recent `BatchRun` row for the project when the in-memory `_seq_auto_jobs` dict is empty (eviction, backend restart, etc.). Reload the project page mid-run and the status pill + modal both repopulate. The DB read only fires when in-memory has nothing — the polling hot path during active runs stays DB-free.

### Fixed — SQLite "database is locked" contention during auto-gen

`/auto-sequential/status` endpoint now reads from in-memory dict only (no DB read on the polling hot path). Was opening a session + doing a SELECT on projects every poll; under 3-second polling × heavy auto-gen writes the polling SELECTs starved the dispatcher writes for up to 60 seconds. Frontend polling also bumped 3 s → 5 s.

### Added — Klein workflow reverted to Turbo/distilled params (style preservation)

User-supplied known-good 4REF workflow surfaced five drifted values in all five `KLEIN_EDIT_ULTRA_WORKFLOW_{1..5}REF.json` files. Reverted:
- `Flux2Scheduler.steps` 20 → **4**
- `CFGGuider.cfg` 5 → **1**
- `ImageScaleToTotalPixels.upscale_method` `lanczos` → **`nearest-exact`**

At CFG=5 + 20 steps Klein follows the text prompt aggressively and drifts from references — exactly the "Pass 2 overtakes the style" symptom users reported. Turbo config (4 steps, CFG=1) is what Pass 2 character compositing needs.

### Added — "Use Existing Prompts — Just Render" auto-gen toggle

Advanced option in the Auto-Gen modal. When ON, scenes with a non-placeholder prompt are NOT re-enhanced — auto-gen renders them with the existing text. Blank scenes still get a fresh enhancement. Useful for re-runs after you've curated prompts manually — saves LLM tokens and preserves your edits. Backend threads `skip_existing_prompts` through 14 enhance call sites with a shared `_should_enhance(skip_existing, current)` helper.

### Fixed — klein_6ref crash on Pass 2

Klein ships 1REF through 5REF workflows only. Scene image always claims slot 1, so character refs are now clamped at 4 (klein_5ref max). Extras dropped with a warning showing which IDs got cut. Also fixed `_apply_two_pass_to_job_params` to only stash **character** refs (not scene "extras" like location/prop refs) into `two_pass_character_ref_ids` — extras were getting mis-classified as characters and counted toward the ref limit.

### Changed — story flow generation batching threshold 20 → 10

`backend/api/concept.py` flow-gen now batches anything over 10 scenes concurrently instead of doing one big synchronous LLM call. A single 20-scene OpenAI call routinely takes 60-90 seconds and exceeded the frontend's 60s axios timeout. Three concurrent batches of 10 finish in ~25-35s. Frontend `generateVideoFlow` also got a `timeout: 300000` (5 min) safety cap.

### Added — per-character last_prompt + reference_images persistence

Already covered in character edit section but worth restating: characters now save their generation context across sessions, so editing-and-regenerating doesn't require re-typing.

---

## [1.8.3] - 2026-06-04

### Added

#### Per-worker model assignment (multiselect under Image / Video checkboxes)
- **Settings → ComfyUI Servers** now lets each worker be restricted to a specific subset of models — useful when one machine runs Klein but another runs LTX, or you keep a "fast" T2I box separate from a "slow but accurate" 2-pass composite box. Below each Images/Video checkbox is a chip multiselect with an **ALL** option (default) plus every preset from the Generation Models section (`flux2_klein_dev_9b`, `flux1_dev`, `z_image`, `qwen_edit`, `z_image_turbo` on the image side; `ltx_2.3`, `wan_2.2` on the video side, plus any custom model names you've set). When ALL is active the worker accepts every model in its enabled category; selecting one or more chips constrains routing to only those models. Toggling the category checkbox OFF hides its multiselect entirely
- **Backend wiring** — `comfyui_server_caps` JSON now stores `{url: {image, video, image_models[], video_models[]}}`. Shared helper `apply_user_caps(worker, caps_config)` lives in `backend/services/comfyui/dispatcher.py` and is used both at startup (`main.py` lifespan) and on Settings save (`api/settings.py` resync), so the on-disk JSON, the dispatcher's `ComfyWorker.capabilities`, and `ComfyWorker.models` always agree. An empty `image_models` / `video_models` list = ALL (worker.models stays empty so `select_worker` treats it as unconstrained — existing semantics preserved)
- **Dispatch-time routing** — `JobDispatcher._get_required_models(workflow_type, app_settings)` now resolves the workflow_type family to the user-facing model the user has selected on the Settings screen: Klein workflows → `AppSettings.image_model_type`, Z-Image redirects → `AppSettings.single_image_generator`, LTX workflows → `AppSettings.video_model_type`. AppSettings is read once per dispatch from the same async session; on the rare DB-unavailable path the dispatcher falls back to the historical FLUX/LTX markers so no job is ever blocked. Custom model strings the user types into the Generation Models section are honored end-to-end

#### Per-job-type resolution split (image vs video)
- **Concept tab — new "Image Generation Size" and "Video Generation Size" controls** under the existing Desired Resolution picker. Image jobs (Klein / Z-Image) and video jobs (LTX 2.3) can now render at different resolutions. The unified Desired Resolution remains the master default; both per-type fields are 0 / blank by default, falling through to it. Rationale: Klein composites need larger images for cleaner Pass 2 character compositing, while LTX video benefits from smaller per-frame sizes and is usually upscaled after generation
- **Backend wiring** — `backend/api/concept.py` `ConceptData` model + GET/PUT extended with `image_resolution_width/height` and `video_resolution_width/height`. `backend/api/generation.py` `_run_sequential_auto_gen` resolves `img_w/img_h/vid_w/vid_h` at the top, passes them through `_run_windowed_batch`, and every per-scene IMAGE job uses `img_w/img_h` while every per-scene VIDEO job uses `vid_w/vid_h`. Character autogen in `concept.py` also picks up the image-resolution split
- **Frontend wiring** — `frontend/src/components/ConceptPanel/ConceptPanel.tsx` exposes both fields with placeholder hints showing the current unified value. `frontend/src/api/client.ts` types both `getConcept` return and `saveConcept` arg extended

#### Project Text Data Import / Export
- **New 3-dot menu item "📤 Import / Export Project Text Details"** available on all project modes. Opens a two-tab modal:
  - **Export tab** — pretty-printed JSON of every editable text field in the project: concept (title, concept, style, image direction, color palette), characters (names + descriptions), chapters (descriptions, character focus, style notes, nesting), scenes (timing, transcribed text, image prompt, video prompt, story flow idea, character references by name, transitions, image movement, per-scene resolution override), resolution settings, source script / lyrics initial text. Buttons: **Copy to Clipboard**, **Download .json**
  - **Import tab** — paste / upload JSON. Radio toggle for **Override all matching fields** vs **Fill only missing fields**. Optional **Accept project-mode mismatch** checkbox. Per-stat result panel after apply (chapters touched / scenes touched / characters added / characters updated / video fields dropped / scenes skipped out of range)
- **Footer links on the modal** — **📄 Download example JSON for this mode** + **📖 View LLM instructions for this mode**. Both auto-target the current project's mode so users get the right file with one click
- **Backend service** `backend/services/project_text_io.py` — pure logic: `build_export(project, session)` and `apply_import(project, session, payload, mode, accept_mode_mismatch)`. Mode-aware (drops video-only fields for narration_images), character lookup by name (case-insensitive), chapter lookup by integer `order`, scene lookup by `order_index`. Validates schema version. Round-trip safe: `override_resolution`/`width`/`height` per-scene resolution overrides persist through export → edit → import
- **Backend endpoints** `backend/api/projects.py` — `GET /api/projects/{id}/text-export`, `POST /api/projects/{id}/text-import`
- **Static assets** bundled in `frontend/public/`:
  - `examples/narration_video.json`, `examples/narration_images.json`, `examples/music_video.json` — fully-filled 1–2 chapter, 2 scene example projects per mode (so an agent has a real template to pattern-match)
  - `docs/narration_video_llm_instructions.md`, `docs/narration_images_llm_instructions.md`, `docs/music_video_llm_instructions.md` — per-mode agent contracts: complete schema table, output rules, common patterns, do's-and-don'ts, mode-specific guidance (period accuracy for narration, lyric-literal visualization for music_video, etc.). Drag the right file into an LLM and it knows what to do
- **Per-scene `narration_text` / `lyrics_text` populated from Whisper words** — the export now extracts the transcribed words that overlap each scene's time range so the LLM agent sees the ground-truth spoken content per scene, not just the full script

### Changed

#### Image generation quality
- **Pass 2 composite context now anchors to style settings** (`backend/services/jobs/dispatcher.py` `_build_two_pass_composite_prompt`). The Klein composite prompt builder now folds `project.settings.image_direction` (or `custom_image_direction`) and per-scene `color_override` (with global fallback) into the LLM context. Previously these style anchors were missing, so the LLM drifted to generic "cinematic, vivid" descriptors that Klein rendered as overexposed composites — visibly washed out / "super bright" in user reports. The same `MANDATORY COLOR PALETTE OVERRIDE` directive used by single-pass image enhance now fires for Pass 2 too
- **`TWO_PASS_BASE_SYSTEM_PROMPT` updated for Z-Image Turbo** (`backend/services/llm/prompt_enhancer.py`). The prompt opened with "You are an expert at writing prompts for FLUX.2 Klein 9B" — but with the always-Z-Image-for-Pass-1 rule from 1.8.1, Pass 1 actually runs Z-Image. Updated:
  - Opening identifies Z-Image Turbo as the Pass 1 model and explains Pass 2 will composite characters via Klein
  - New `EXPOSURE / DYNAMIC RANGE` section explicitly forbids stacking "ultra-bright, brilliant, luminous, glowing, radiant, sun-drenched, dazzling, blazing" superlatives that push Z-Image into highlight clipping
  - Requires natural / balanced lighting unless the script explicitly calls for extreme brightness; "Shadows, depth, and contrast are essential"
  - Prefers specific motivated light sources ("a single window at dusk", "candlelight", "overcast soft-box") over generic "bright" descriptors
  - Music-video-only wording removed so the same prompt works correctly for narration_video and narration_images Pass 1 without losing music_video behavior

### Fixed
- **Pass 2 character composites no longer "overtake" the base scene style — KLEIN REF workflows reverted to Turbo/distilled config** (`workflows/KLEIN_EDIT_ULTRA_WORKFLOW_{1..5}REF.json`). Comparing the shipped JSONs against a user-supplied known-good 4REF workflow surfaced FIVE drifted values, all in the same direction: the workflows were running the standard Klein config (`steps=20, cfg=5, upscale_method=lanczos`) when they should be running the distilled Klein config (`steps=4, cfg=1, upscale_method=nearest-exact`). At CFG=5 the LLM-enhanced text prompt has heavy classifier-free guidance pull that OVERRULES the reference image colors and composition; at CFG=1 the model leans on the references for color/lighting/style. Combined with 5× more sampler iterations (drift) and lanczos blurring ref colors during the latent prep, the output composite was a fresh rendering of the prompt rather than a character insert into the base scene. All five REF workflows now match Turbo config end-to-end. Klein Text2Image was already on the distilled path (steps=4, cfg=1, lenovo LoRA on) — left untouched
- **Pass 2 character ref list could exceed Klein's 5REF ceiling** (`backend/services/jobs/dispatcher.py` `_create_two_pass_composite_job`). When auto-gen scenes carried >4 character references in `two_pass_character_ref_ids` (e.g. project had many characters auto-resolved from concept data), the dispatcher built `workflow_type = f"klein_{count}ref"` and the build failed with `Unknown workflow type: klein_6ref`. Now clamped at `MAX_CHARS_IN_COMPOSITE = 4` (scene image always claims slot 1 → klein_5ref is the ceiling). Extras dropped with a warning so the dropped IDs show up in the log
- **Auto-gen was carrying scene "extras" into Pass 2 as if they were characters** (`backend/api/generation.py` `_apply_two_pass_to_job_params`). The FF picker allows up to 3 extra reference images (locations, props, style refs) in addition to up to 2 character refs. Every auto-gen callsite was doing `ref_ids = char_asset_ids + extra_ref_ids` then stashing the WHOLE list as `two_pass_character_ref_ids`. Result: 2 chars + 3 extras = 5 refs → Pass 2 = 1 scene + 5 = klein_6ref crash, AND non-character image colors blending into the composite. Helper now accepts a `character_only_ids` kwarg; all 7 auto-gen callsites pass `char_asset_ids` / `seq_char_aids` (the character-only list already computed one line earlier). Extras are intentionally dropped in two-pass mode — they had no correct slot anyway since Pass 1 runs Z-Image (no refs) and Pass 2 is for character compositing only
- **Pass 2 brightness / "washed out" regression on narration_video** — root caused to missing style anchors in the composite context (Issue #1 above) and Z-Image's response to Klein-style verbose prompts (Issue #2). Both addressed. Music_video Pass 2 also benefits since the same fixes apply
- **Pass 2 Klein composite overtook the base scene's color/style (B&W noir → color leak)** — three-layer fix because Klein at CFG=5 blends color signals from BOTH the scene ref and the (usually full-color) character refs. Workflow params are NOT the cause (1REF/2REF/3REF Klein workflows all use identical steps=20/CFG=5/euler with the LoRA OFF — verified) — the bug lives in the prompt-side instructions:
  - **`TWO_PASS_COMPOSITE_SYSTEM_PROMPT` rewritten** (`backend/services/llm/prompt_enhancer.py`): leads with an explicit "ABSOLUTE TOP RULE — PRESERVE THE BASE SCENE STYLE" block stating the first reference is the AUTHORITATIVE VISUAL BASELINE. Character references are now described as IDENTITY and POSE only — their colors, skin tones, and lighting must be ignored and re-rendered to match the first image. Added a "CHARACTER DESCRIPTION COLOR FILTER" section that tells the LLM to translate character color cues ("brown leather jacket", "blue eyes") through any active palette override. Length cap raised 150→180 to make room for the explicit style-lock language Klein needs
  - **Pass 2 LLM context restructured** (`backend/services/jobs/dispatcher.py` `_build_two_pass_composite_prompt`): the style-preservation contract now leads the context list (before the base prompt, before character descriptions) so the LLM treats the first ref's palette as ground truth before it even sees the scene details. Each character ref description now explicitly says "IGNORE the lighting, color cast, skin tone, and clothing colors of the reference photo — re-render the character under the FIRST reference image's lighting and palette." When a color override is active, the directive gets an extra Pass-2-specific tail: "the character reference photos may be in full color, but the final composite MUST use ONLY the palette above — re-render the characters' skin, clothing, hair, and eyes in this palette only"
  - **Dispatch-time color suffix strengthened for Pass 2 only** (`backend/services/jobs/dispatcher.py` color injection block): when `two_pass_phase == "composite"` and a color override is set, the appended suffix gets an additional trailing clause restating that the entire composite must match the first reference image palette and that the model must ignore colors from the character reference photos. Lands near the end of the prompt where Klein weighs the latest tokens most heavily. Single-pass and Pass 1 paths unchanged
- **Default Color Palette on Concept tab did not save** — `ConceptData` Pydantic model in `backend/api/concept.py` was missing `global_color_override` and `custom_color_palette` fields, so the frontend's value was silently dropped on PUT and never returned on GET. Added both fields to the model, the GET response, and the PUT settings write (with empty-string-clears-key semantics so an unset palette falls through to no override)
- **Per-scene Color Override now defaults to "None — use project Default Color Palette"** in `frontend/src/components/SceneEditor/SceneEditor.tsx`. Previously the picker showed a real palette as the "default" even when no scene override was set, creating the illusion that the project-level palette was being ignored. Selecting "None" deletes the per-scene `color_override` + `custom_color_palette` keys so generation falls through cleanly to the project's `global_color_override`. The active project default is shown inline in the dropdown label
- **Auto-gen "complete" status no longer flips while Pass 2 composites are still rendering** (`backend/api/generation.py` `_run_windowed_batch`). Two-pass jobs spawn a NEW Pass 2 Job row from `dispatcher._create_two_pass_composite_job` AFTER Pass 1 finishes; that new row was never tracked in the windowed batch's `active_jobs` dict, so when the last Pass 1 left the dict the batch declared "complete" while the final composites were still running on workers. The batch now drains: after the main loop ends, it polls the DB for any PENDING/RUNNING jobs scoped to this batch's scene IDs and waits them out, surfacing `current_step = "finishing follow-on jobs (N remaining: X × composite, Y × image)"` so the UI shows what's happening. Per-job cap on the drain matches the main `batch_timeout` so a wedged worker can't pin the status forever

---

## [1.8.2] - 2026-06-03

### Added

#### Narration Images mode lock (six layers of defense)
The Narration Images project mode (Ken Burns slideshow output) now strictly enforces image-only behavior across the entire pipeline, fixing the case where a project could accumulate video artifacts and then show them in preview / export despite the mode setting:

- **Export assembly** (`backend/api/export.py` `_build_scene_dicts`): when `project.mode == "narration_images"`, every scene's `scene_source_type` is forced to `"image"` and `chosen_video_path` is nulled before assembly. Any leftover videos on scenes are ignored. Log line: `Project is in narration_images mode — forcing scene_source_type='image' on every scene for this export`
- **Auto-Gen sequential** (`POST /auto-sequential`): rejects video-producing modes (`all_video_*`, `missing_videos_*`) with a 400 when project is narration_images. Allowed modes: `all_images`, `missing_images_independent`
- **Auto-Gen legacy** (`POST /auto`): rejects video-touching enhanced modes (`enhanced_all`, `enhanced_missing`) with a 400. Allowed modes: `all_images`, `empty_only`
- **Story Flow auto-gen** (`_ensure_video_flow`): skips entirely for narration_images projects — the system prompt is video direction ("camera movement, action, mood, composition") which would only waste tokens for a Ken Burns slideshow. Image enhancement falls back to the scene's narration text + concept block, so the skip degrades nothing
- **Single-scene enhance** (`POST /enhance-prompt`): rejects `is_video=True` for narration_images projects with a clear error message
- **Live preview** (`frontend/src/components/VideoPreview/VideoPreview.tsx`): forces `sourceType = 'image'` and nulls the video URL when project is narration_images. Hitting Play on the timeline now correctly shows the still image with Ken Burns / movement, even if `chosen_video_path` is still stored on the scene from before the lock. Also skips the next-video preload
- **Auto-Gen modal UI** (`AppLayout.tsx`): filters `AUTO_GEN_OPTIONS` to image-only modes and defaults to `missing_images_independent`. Video modes don't even appear in the picker

**Narration Video mode is untouched.** Every guard branches on `project.mode == "narration_images"` specifically; `narration_video` continues to use the full video pipeline including LTX Director and video-prompt enhancement.

#### Pre-flight guards for video generation
- **No-start-image guard** (`POST /generate/video`): if the requested workflow type needs a first frame (everything except `ltx_v2v_extend` / `ltx_seq_v2v`) and the request has no `first_frame_asset_id` AND the scene has no `chosen_image_path` AND no `use_prev_lf_as_ff` flag, returns a 400 with a clear message. Previously the job got created, ComfyUI received a workflow pointing at a missing image (logged as 404 by the worker), then reported "completed" with nothing rendered. Now the job is never created; the dispatcher never wastes a worker slot
- **Frontend pre-flight** in `SceneEditor.tsx` `generateVideoMutation`: checks the same conditions client-side and pops an `alert()` before any network call. Throws to short-circuit the mutation cleanly
- **Mutation `onError` surfacing**: video-gen mutation now reads `err.response.data.detail` and pops it in an alert. Whether the rejection came from the client guard or the server guard (or any other failure), the user sees the actual error message instead of dying silently

### Fixed
- **VideoPreview no longer plays leftover videos in narration_images projects** — see live preview bullet above
- **Auto-Gen modal no longer offers video modes when project is narration_images** — the picker is filtered client-side so the user can't even pick a video mode

---

## [1.8.1] - 2026-06-02

### Added

#### Image generation transparency
- **Model indicator badge on the Image tab** — under the two-pass toggle, a `Will render with:` row predicts which model the backend will actually use given the current ref count + two-pass toggle + global `single_image_generator` setting. Single-pass shows one chip (Klein N-ref / Klein T2I / Z-Image Turbo); two-pass shows `Pass 1: Z-Image Turbo → Pass 2: Klein N-ref`. When two-pass is on but zero refs are selected, an amber `⚠ no refs — single-pass` chip appears so you know exactly why the backend will downgrade
- **Per-pass model label on lightboxes** — the main image lightbox reads `version.parameters.workflow_type` (ground truth from `GenerationHistory.parameters`) and pins a blue chip top-left showing the actual model that produced the preview, with `· Pass 2` appended when applicable. The "View Original" Pass 1 lightbox resolves the base asset's `meta.workflow_type` and labels accordingly so any model deviation is immediately visible

### Changed

#### Image generation guards (logic correctness)
- **Pass 1 is now ALWAYS Z-Image Turbo** — the dispatcher's `_try_zimage_redirect` short-circuits the `AppSettings.single_image_generator` check when `two_pass_phase == "base"`. Rationale: Pass 1 paints the scene with no refs; Klein would benefit from refs it never receives. Log line says `Redirecting to Z-Image Turbo (two-pass Pass 1 (forced — characters added in Pass 2)...)`. Independent of the user's global setting
- **Two-pass downgrades to single-pass when no refs are resolvable** — `POST /generate/image` now checks request `reference_asset_ids` + concept-character fallback. If both are empty, downgrades to single-pass at the API layer with `Downgrading to single-pass.` log. Mirrors the existing `_apply_two_pass_to_job_params` guard so the manual Generate button matches auto-gen behavior. Stops wasted Pass 1 runs followed by silently-skipped Pass 2
- **Transition picker label** — "None (Hard Cut)" → **"None (Use Per Scene Preference)"** in both the Export modal (`AppLayout.tsx`) and Settings (`SettingsPage.tsx`). When global is `none`, assembly correctly falls through to each scene's `transition_in`/`transition_out` — the new label tells the truth

#### Persistence semantics
- **Reference picker auto-saves on every change** — `ReferenceSelector` `onChange` now goes through the cache-coherent `updateSceneAndSync` helper (backend + React Query cache + Zustand in one shot). Previously, picker state only persisted on Generate click; if the user removed a character ref and navigated away, the next two-pass run could still use Klein with the stale ref ID
- **Transition selectors auto-save on every change** — both transition `<select>` handlers now use `updateSceneAndSync` instead of the raw `updateScene` + `updateSceneInStore` pair. The raw path skipped React Query, letting the AppLayout cache-mirror revert the change on next refetch
- **Export cache nested by scope** — `.export_cache/<cache_key>/concat.mp4` (was `.export_cache/concat.mp4`). Before: exporting chapter A, then chapter B, then chapter A again forced a full re-render of A because B's save overwrote A's slot. Now each scope (full project, each chapter, each chapter subset) keeps its own durable cache. `force_recreate` still wipes the entire root

### Fixed

#### Chapter scope leaks
- **Background auto-gen task re-fetched all project scenes** — `_run_sequential_auto_gen` accepted `chapter_id=None` and unconditionally re-built the scene list from `select(Scene).where(project_id)`, so even though the request handler scoped to 23 chapter scenes the bg task processed all 328. Fix: handler now passes `chapter_id=req.chapter_id` into the task; task branches on `chapter_id is not None` in all three scene queries (initial load, flow-gen scope, post-flow re-read) using `scenes_in_chapter_tree`
- **Story Flow pre-step ignored chapter scope** — `AppLayout.handleQueue` called `generateVideoFlow(projectId)` without `chapter_id` when `useStoryFlow` was on, regenerating ideas for all 328 scenes before scoping the actual gen to 23. Fix: forwards `chapterScope.chapterId` AND inspects in-scope scenes — if every one already has `flow_idea`, skips the pre-step entirely (no LLM cost, no overwriting user edits). Console logs `[AutoGen] Skipping flow gen — all 23 chapter scenes already have flow_idea`
- **`POST /auto` was not chapter-aware** — older auto-generate endpoint (separate from `/auto-sequential`) did `select(Scene).where(project_id)` with no chapter scope support. Added `chapter_id: Optional[UUID]` to `AutoGenerateRequest` + the same `scenes_in_chapter_tree` branch the sequential path uses

#### Backend silent breakage
- **`name 'json' is not defined` on chapter-scoped exports** — `backend/services/video/assembly.py` called `json.dumps`/`json.loads` (cache key + manifest read/write) without importing `json`. The cache path was never exercised in single-project mode; chapter exports hit it and the export job crashed with NameError. Added `import json`
- **Audio-only-remix cache silently disabled** — `_save_concat_to_cache` called `datetime.utcnow().isoformat()` for the manifest's `saved_at` field but `datetime` was never imported. The call was wrapped in try/except so exports didn't crash, but the manifest was never written, which means `_load_cached_concat` always returned None and the audio-only-remix feature was effectively non-functional. Added `from datetime import datetime`

### Backend audit summary
Surrounding-areas audit found no other backend file using a stdlib module without importing it. The export pipeline's chapter_selection flow (frontend → `ExportRequest.chapter_selection` → `_resolve_chapter_scope` → `_build_scene_dicts(chapter_ids=...)`) is end-to-end correct. Per-scene transition override IS honored when global is `none` (`assembly.py:1131-1138`) so the new "Use Per Scene Preference" label is truthful.

---

## [1.8.0] - 2026-06-02

### Added

#### Narration Chapters — long-form workflow
- **Chapter model** — new `chapter` table with `parent_chapter_id` self-reference for sub-chapters (up to 3 depth levels). Every chapter carries `name`, `short_code` (e.g. `a3f9-ch-01`), `color`, `tags`, `description`, `character_focus` list, and `style_notes`. Scenes get a `chapter_id` FK; assets and scenes get human-readable `short_code` columns
- **Auto-chapter pipeline** — Markdown `# Heading` / `## Heading` markers in the narration script become chapters automatically. Without headers, the project is auto-split by scene count (`chapter_auto_split_threshold` in Settings, default 25). Oversized chapters auto-split into sub-chapters at natural pause boundaries
- **Suggest Timeline auto-builds chapters** — every successful Suggest Timeline run also runs the chapter resolver, so chapters appear immediately on the timeline overlay and in the Chapters tab. Backend logs `[SuggestTimeline] Auto-built N chapter(s) from M scenes`
- **Chapter REST API** — `GET /api/projects/{pid}/chapters/` (tree), `POST /reparse`, `PATCH /{cid}` (rename/recolor/retag/description/character_focus/style_notes), `POST /{cid}/split`, `POST /{cid}/merge_with_next`, `POST /{cid}/generate-description` (LLM), `POST /{cid}/preview-llm-batches`, plus universal `GET /api/shortcode/{code}` resolver
- **Chapter scope banner** — when the URL is `/project/:id/c/:short_code`, a banner appears at the top of the editor with chapter name + color + scene count + time range + prev/next chapter buttons + back-to-project link. Description, character chips, and style notes are editable inline with Save / ✨ Generate buttons
- **Chapter Direction panel** (Chapters tab) — every chapter renders as a card with inline description textarea, character chips, style notes, and per-card ✨ Generate description + 🎬 Generate Story Flow buttons. Top toolbar has a **✨ Generate ALL** batch button (sequential with progress bar) plus the existing Re-parse
- **Chapter-scoped Timeline** — drilling into a chapter narrows the Timeline scene list to that chapter's subtree. Zustand `chapterScope` slice (sceneIds Set + start/end time) is the single source of truth
- **Chapter-scoped Export** — Export modal opened from a chapter view defaults to `mode: 'single'` with the active chapter pre-selected. Backend `ExportRequest.chapter_selection` filters scenes, slices `master_audio` with FFmpeg, shifts backing tracks + subtitle word timestamps so the output is a self-contained MP4 starting at 0:00. Output filename includes the chapter shortcode
- **Chapter-scoped Story Flow** — `POST /concept/flow/generate?chapter_id={cid}` scopes per-scene flow generation to one chapter and folds the chapter's description, character_focus, and style_notes into the LLM concept block
- **Chapter overlay on timeline** — colored bars row above the waveform, one per chapter (with sub-chapter row when nested). Click to drill in
- **LLM batching limits** — Settings → Chapter Batching exposes `llm_chapter_scene_limit_cloud` (default 25) and `llm_chapter_scene_limit_ollama` (default 12). The resolver respects chapter boundaries when batching
- **Shortcode system** — every asset, scene, and chapter gets a stable `{project_prefix}-{type}-{seq}` identifier (e.g. `a3f9-img-0047`, `a3f9-sce-005`, `a3f9-ch-01`). Universal `/s/{code}` URL redirects to the right entity. Backfill migration assigns codes to existing rows
- **Auto-chapter on initial backfill** — projects without any chapters get one default "Chapter 1" umbrella created at startup so the rest of the chapter pipeline always has something to bind to

#### Subtitle reconciliation
- **Whisper-to-canonical alignment** — `backend/services/audio/text_align.py` reconciles Whisper word strings against the user's pasted ElevenLabs script using `difflib.SequenceMatcher` opcodes. Whisper timestamps (audio-accurate) are preserved; word strings get replaced with canonical tokens, hallucinations dropped, missed words interpolated. Bails out cleanly if similarity < 30%. Applied at export time so existing projects benefit without re-transcribing

#### Whisper / Demucs optimizations
- **`skip_demucs=True` in narration modes** — analyze-audio + batch pipelines now skip Demucs entirely when project mode is `narration_*`. Stems dict points at the original audio for downstream consumers. Saves ~30 min/item and avoids phase artifacts on pure-speech audio
- **Audio-duration-scaled Whisper timeouts** — ComfyUI Whisper poll budget now scales `max(20 min, 4× audio length, 30 min floor)` capped at 6 h. Batch wait_for scales similarly capped at 8 h. Up-front log shows the chosen budget. Queue position from `/queue` is surfaced every 30 s during the poll so wedged-vs-running is distinguishable
- **Whisper heartbeat** — local transcribe path logs an estimated runtime up-front (e.g. "audio=3600s; estimated ~7200s on cpu") then emits a heartbeat every 60 s while `model.transcribe()` blocks. No more silent multi-hour waits

#### Transition handling
- **Global override semantics** — Export "Transition" picker now means: `none` → defer to per-scene `transition_in`/`transition_out`; anything else → override all boundaries uniformly. Per-scene transitions are now actually forwarded from `Scene.parameters` to the assembler (previously stripped silently)

#### Debug
- **`/api/debug/chapters/{project_id}`** — compact JSON snapshot of chapter state for a project: parsed headers, clean-text word count, scene-to-chapter binding stats, unbound scenes, and current settings
- **`tools/diag.py --chapters PROJECT_ID`** — CLI wrapper that prints the snapshot as markdown

### Changed
- **`Suggest Timeline` response** — DP-narration scene-creation block fixed (`Scene.order_index` instead of non-existent `scene_index`; required `prompt` field filled). Response shape unified between LLM and DP paths
- **OpenAI param fallback** — chapter description endpoint detects newer model families (gpt-4.1+, gpt-5, o-series, chatgpt-*) and uses `max_completion_tokens` automatically; on `BadRequestError` it retries once with the other token-param style so model aliases the heuristic misses still succeed

### Fixed
- **Chapter URL singular/plural mismatch** — chapter components were navigating to `/projects/:pid/c/:shortcode` (plural) while the route is `/project/:pid/c/:shortcode` (singular). Every chapter click was hitting the catch-all `*` route and redirecting to `/`. Fixed in `ChapterOverlay`, `ChapterTree`, `ChapterBreadcrumb`, `ChapterScopeBanner`, and backend `shortcode.py` redirect URLs
- **Chapter description fields not in GET response** — `ChapterTreeNode` dataclass was defined before the description fields were added, so the list endpoint dropped them silently. Now part of the dataclass + populated by `build_chapter_tree_response`
- **FK violation on chapter re-build** — `_create_auto_chapters` was deleting parent chapters before their sub-chapters and scenes-pointing-at-them, causing SQLite FK rejection mid-transaction. Rewrote to unbind scenes first, then DELETE chapters depth-DESC via raw SQL with project_id.hex (SQLite stores UUIDs without dashes). Same fix in the headers path
- **PendingRollbackError on Suggest Timeline after chapter failure** — pre-capture `scene_ids` before chapter rebuild so a chapter-build error can't poison the session's lazy-load of `sc.id` in the response
- **`_auto_slice_scene_audio` NameError** — Suggest Timeline now calls the actual helper `_slice_audio_for_scenes`, wrapped in try/except so a slice failure doesn't lose the scenes
- **Chapter tab blank on re-run** — frontend wasn't refetching the chapter tree after Suggest Timeline. Window event `rbmn:chapters:invalidate` dispatched from Timeline / AudioSetup → AppLayout listener refetches. Also reloads on Chapters-tab click
- **Stems-only export status** — backend now marks the Job DONE + populates `_export_progress` with `status="done"` + the stems list before returning, so the frontend transitions out of "Exporting…"
- **Single Download button hidden on stems-only success** — per-stem download cards are the right action
- **`ProjectMode` NameError** in `timeline.py` analyze-audio endpoint — added to the top-level import block

### Documentation
- `BLUEPRINT_CHAPTERS_v1.md` — design doc for the chapter system (kept in repo as historical record of the design decisions and Phase 1.5/2/3 punch list)
- This CHANGELOG section
- README narration-mode section (next)

---

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.7.0] - 2026-05-31

### Added

#### Export — re-export controls and stems
- **Audio-only re-mix** — After every successful export, the silent concatenated video is saved to `{output_dir}/.export_cache/concat.mp4` along with a manifest hashing the video-affecting params (scenes, dimensions, transitions, color match, CRF). On the next export, if the hash matches, the entire clip-rendering and chunk-merge phases are skipped — only the audio mix, mux, optional stems, and optional subtitles run. Use case: change narration volume / backing track levels / fades / normalization without re-rendering hours of video. Export modal has a "Audio-only re-mix" checkbox that requires the cache to exist (errors loudly if not), and auto-disables when "Force full recreate" is on
- **Export audio stems** — New checkbox in the Export modal that ALSO writes per-channel WAVs to `{output_dir}/stems/`: `narration.wav` (narration with master volume), `backing_mix.wav` (all backing tracks mixed), and `backing_NN_name.wav` for each individual backing track separately. 48 kHz 16-bit PCM — drop straight into a DAW for outside-the-app remixing
- **Stems-only export** — Skip ALL video rendering entirely and just produce the audio stem WAVs. Use case: you already have the exported video and want to grab stems later for separate mixing. Output appears in `{output_dir}/stems/` with `narration.wav` + `backing_mix.wav` + one `backing_NN_name.wav` per backing track. Runs in seconds since no clip rendering or muxing is involved
- **Force full recreate** — New checkbox that wipes the export cache before starting, guaranteeing a fresh render. Available in both narration and music modes. Mutually exclusive with audio-only re-mix
- **Cache invalidation** — The cache key covers everything that affects the silent video (scene paths/durations/transitions, dimensions, FPS, CRF, color match), so changing any of those triggers a fresh render. Audio params (volumes, fades, normalize, subtitles) are deliberately excluded — they're applied after the cached concat is reused

#### Batch processing reliability (B-1 through B-14)
- **B-1: Idle-race guard** — Auto-gen kickoff POST is now required to succeed (raises on non-200). The poll loop tracks `saw_running` and only treats `status="idle"` as terminal after first confirming the run actually started. Previously the batch could falsely "complete" with zero work done if the kickoff failed
- **B-2: 2-hour poll deadlines** — Both image and video step poll loops now have hard deadlines. A wedged auto-gen can no longer hang a batch item indefinitely
- **B-3: Exhaustive video mode map** — `video_mode_map` now includes `fflf` (FF/LF chaining). Unknown values raise instead of silently demoting to single-image mode. Same treatment for image mode (`missing` / `all_with_refs`)
- **B-4: Orphan project cleanup** — Failed items that haven't generated anything yet are now best-effort cleaned up (project row + directory removed) instead of being left as junk in the project list
- **B-6: Skip base-on-lyrics when user supplied direction + style** — Saves an LLM call when both fields are already filled in
- **B-7: Whisper 1-hour timeout** — Audio analysis wrapped in `asyncio.wait_for`. A wedged Whisper can no longer hang the item indefinitely (Demucs already had a 30-min subprocess timeout)
- **B-9: Lyrics retry uses fresh session** — Avoids gotcha #9's corrupted-session pattern and preserves `initial_text` (previously dropped on retry)
- **B-10: BatchItemAddModal expanded** — UI now exposes Image Mode (Missing only / All with prev-scene refs), FF/LF video chaining, Lipsync-aware prompts, Vocals-only audio, and Override-regenerate-full-set
- **B-12: Staging cleanup on success only** — Retries can find the staged audio file again
- **B-14: Surfaced auto-character failures** — Now appear as warning entries in the BatchRun activity feed

#### Debugging tools
- **`GET /api/debug/snapshot`** — Returns JSON of in-memory batch runs, in-memory auto-gen runs, ComfyUI worker stats, job queue depth + running + failed jobs, and recent WARNING/ERROR log entries. Query params: `?log_lines=N&log_grep=substring`
- **`GET /api/debug/log/tail?lines=N&level=ERROR&grep=substring`** — Filtered tail of `rbmn.log` returning structured entries (each message capped at 500 chars)
- **`tools/diag.py`** — CLI helper that hits the snapshot endpoint and prints a compact markdown summary. Use `python tools/diag.py > diag.md` to capture the current backend state instead of pasting raw log files. Supports `--logs N`, `--grep TERM`, `--json`, `--tail`, `--host` overrides

### Fixed

- **Active image/video set delay** — Setting a chosen image/video as active on a scene didn't stick — leave and come back and it would still show as inactive until a later DB refresh caught up. Root cause: `updateScene` PUT + `updateSceneInStore` Zustand update without updating the React Query cache that AppLayout mirrors back into Zustand on every change. The stale cache eventually overwrote the fresh Zustand state. Fix: added `updateSceneAndSync` helper in `SceneEditor.tsx` that updates backend + React Query cache + Zustand atomically, applied to all 24 scene-update call sites. Defensive `flag_modified(scene, "parameters")` added to backend `update_scene` to also guarantee persistence even if SQLAlchemy's MutableDict detection has edge cases. Same fix applied to `useJobEvents.ts` SSE reconnect path
- **Auto Gen modal "Full Pipeline" did nothing** — The modal exposed `enhanced_all` / `enhanced_missing` / `empty_only` modes but the backend `_run_sequential_auto_gen` only handled the 6 modes `all_images` / `missing_images_independent` / `all_video_*` / `missing_videos_single`. Picking "Full Pipeline (All)" hit no branch and the function fell off the end marking complete with zero work. Fix: replaced modal options with the 6 actually-supported modes (`all_video_fflf` is the new default), added `Override — regenerate full set` toggle, added backend `_VALID_MODES` guard that fails loudly on unknown modes
- **Auto Gen "status window disappears then selection comes back"** — Timeline toolbar's Auto Gen button opened a duplicate legacy modal that wasn't wired to the bottom-of-screen `AutoGenStatusBar`. Local state was lost on every unmount, so the user would see the selection screen instead of progress. Fix: lifted `autoGenOpen` into the Zustand store so the Timeline button opens the same modal as the header button, removed the legacy modal entirely
- **WebSocket completion detection too slow (450s+ delay)** — `crystools.monitor` (every 1-2s) and `progress_state` messages kept `ws.recv()` from ever timing out, blocking the history-poll fallback that lives inside the WebSocketTimeoutException handler. Added a wall-clock history poll inside the recv-success branch that runs every 10s once progress hits 100%, regardless of message flow
- **PUT `/scenes/reorder` and GET `/assets/generated` shadowed** — Named routes were registered AFTER `/{scene_id}` and `/{asset_id}` so FastAPI parsed the literal strings as UUIDs and returned 422. Reordered the route declarations
- **`JobResponse` class name collision** — Same class name in `api/jobs.py` and `api/export.py` overwrote OpenAPI schema. Renamed `export.py`'s class to `ExportJobResponse`
- **`Scene.workflow_snapshot` and `Job.prompt_id` silently dropped** — Pydantic response models didn't include fields the DB model had. Fields added to `SceneResponse` and `JobResponse`
- **Demucs could hang forever** — `Popen` + `process.wait()` had no timeout. Wrapped with `wait(timeout=1800)` + `kill()` on `TimeoutExpired`
- **`/api/files/*` path-traversal** — `startswith` lacked a separator boundary guard. Replaced with `relative_to()`
- **Asset upload read whole file into memory** — Replaced with streaming 1 MB chunks + incremental SHA256 + hard 2 GB cap (returns 413 over limit)
- **`asyncio.create_task` fire-and-forget GC risk** — The auto-gen pipeline and ~15 batch-run DB-update tasks were vulnerable to event-loop weak-ref GC. Added `backend/utils/background.py` with a `track()` helper that holds strong references and logs exceptions; replaced all fire-and-forget calls in `api/generation.py`, `api/batch.py`, `api/batch_runs.py`, `api/export.py`
- **Restart cancelled in-flight ComfyUI prompts** — `recover_running_jobs` cancelled ALL RUNNING jobs unconditionally. Now jobs with a live `prompt_id`+`worker_url` are left in RUNNING; the dispatcher's startup reconnects via the existing retry fast-path so expensive LTX renders survive backend restarts
- **Worker `in_flight` counter drift on retry** — The retry fast-path skipped `select_worker(reserve=True)` but `stream_and_wait`'s `finally` always decremented `in_flight`. Counter drifted toward zero, leading to over-scheduling busy workers. Now the retry path explicitly increments `in_flight` to match the decrement
- **`cancel_job` allow-list included DONE/FAILED** — A stale cancel could flip a DONE job to CANCELLED. Restricted to PENDING/RUNNING with 409 otherwise
- **`mux_audio` in narration export bypassed `_run_ffmpeg`** — Raw `subprocess.run(..., timeout=120)` left truncated muxed files on timeout. Now raises on `TimeoutExpired` and cleans up partial output on non-zero return code
- **`datetime.now()` (local TZ) in `api/timeline.py` and `services/llm/prompt_enhancer.py`** — Replaced with `datetime.utcnow()` for consistency with frontend's Z-normalization
- **`BackingTrack` missing cascade delete** — Deleting a project with backing tracks raised a FK violation. Added `cascade_delete=True` relationship and `ondelete="CASCADE"` on the column
- **`update_project` didn't bump `updated_at`** — Project list sorted by `updated_at DESC` didn't reflect edits. Now bumps the timestamp on commit
- **`color_correction_enabled` reset on every startup** — Migration ran an unguarded UPDATE that overwrote the user's choice every boot. Guarded with a sentinel column `_mig_color_default_off` so it only runs once. Same fix for `_mig_transition_none`
- **`requests.get(...)` in test-whisper endpoints blocked the event loop** — Six sites wrapped in `asyncio.to_thread`
- **INTConstant duration truncation in custom workflows** — `prepare_workflow_from_config` (the path for user-uploaded WorkflowConfig templates) didn't apply `math.ceil(duration)` like the 6 hardcoded workflow builders do. Added an INTConstant truncation guard so custom-workflow uploads that map `duration` to an INTConstant node don't re-trigger the floor-on-write bug
- **Klein image generation rejected by every worker** — Two compounding bugs: (1) `discover_capabilities` only scanned `CheckpointLoaderSimple`, missing GGUF-quantized Klein models loaded via `UnetLoaderGGUF`/`UNETLoader`; (2) `worker.capabilities = user_caps` in `main.py` and `api/settings.py` REPLACED auto-discovered caps (including `inpaint` and `upscale`). Fixed: added GGUF unet loader scan in `discover_capabilities`, and changed user caps to MERGE (preserve auto-discovered) with explicit add/discard for klein/ltx based on the user's image/video checkboxes
- **15+ frontend components subscribed to the whole Zustand store** — Every SSE `job_progress` event re-rendered huge subtrees. Converted to per-field selectors across `AppLayout.tsx`, `Timeline.tsx`, `SceneEditor.tsx`, `WaveformDisplay.tsx`, `useBackingTrackPlayer.ts`, `VideoFlowPanel.tsx`, `AssetManager.tsx`, `AssetManageModal.tsx`, `AudioSetup.tsx`, `GenerationPanel.tsx`, `SectionMarkers.tsx`, `VideoPreview.tsx`, `ReferenceSelector.tsx`, `CharacterCreatorModal.tsx`, `BatchPreviewPIP.tsx`
- **9 frontend timestamp sites missing Z-normalization** — Backend sends `datetime.utcnow().isoformat()` without a Z suffix; JavaScript was interpreting these as local time. New `frontend/src/utils/time.ts` helper (`parseBackendDate`, `parseBackendMs`) wired into GenerationPanel, AssetManageModal, AssetGeneratorModal, SettingsPage, BatchesDashboard, AppLayout (formatDate), ProjectList, SceneEditor (5 sites), useJobEvents

### Documentation
- README "Required Custom Nodes" expanded with 7 packs the shipped workflows actually need: ComfyUI-Detail-Daemon, ComfyUI_essentials, ComfyUI-TTPlanet, ResizeImagesByLongerEdge, TrimAudioDuration, ComfySwitchNode
- README "Environment Variables" expanded with Ollama (`OLLAMA_BASE_URL`, `OLLAMA_URLS`, `OLLAMA_MODEL`), bind controls (`APP_HOST`, `APP_PORT`), and performance vars (`RBMN_PARALLEL_CLIPS`, `RBMN_TMPFS_DIR`, `RBMN_TMPFS_MIN_FREE`)
- README version line removed (points at `VERSION` + CHANGELOG instead of hard-coding 1.4.0)
- `pyproject.toml` and `backend/main.py` versions synced to track `VERSION`

## [1.6.3] - 2026-05-31

### Added
- **Batch pipeline per-step checkpointing** — Every stage of the batch render pipeline (project creation, audio analysis, timeline suggestion, concept generation, character generation, video flow, image gen, video gen) now saves a checkpoint to the database after completing. On resume, the pipeline reads the last completed step and skips directly to where it left off. Previously, any failure (LLM timeout, worker unavailable, etc.) required restarting the entire pipeline from scratch including expensive audio analysis and Whisper transcription
- **Batch retry endpoint** — `POST /api/batch/{batch_id}/retry` re-launches failed batch items using the checkpoint/resume system. Resets failed items to pending, sets batch status back to running, and re-calls `_process_single_item` with the existing `batch_run_id` so completed steps are skipped automatically
- **Proper batch failure status** — Batch runs now report `"failed"` status when ALL items fail, instead of always saying `"done"`. Partial success (some items done, some failed) still shows `"done"` with per-item error details

### Fixed
- **Z-Image Turbo crash on workers without Klein** — When `single_image_generator` is set to Z-Image Turbo, the dispatcher correctly redirects `klein_t2i` jobs to Z-Image, but the worker capability check happened BEFORE the redirect, demanding `klein` capability that LTX-only workers don't have. Fixed by updating `params["workflow_type"]` inside the redirect itself so worker selection picks up the correct (empty) capability set
- **Workers with empty model sets rejected for LTX jobs** — `select_worker` required `{"LTX"}` model tag but workers had empty model sets. Fixed: workers with no models declared now pass the model filter when they match on capability
- **Missing sequencer workflow types in capability/model maps** — `ltx_seq_i2v`, `ltx_seq_fflf`, `ltx_seq_v2v` and `klein_5ref` were missing from the dispatcher's capability and model maps, causing wrong worker selection
- **Image worker count hardcoded to Klein** — `_count_capable_workers` required `{"klein"}` for image jobs, but Z-Image Turbo needs no special capability. Changed to `set()` so all healthy workers are counted for the parallel dispatch window
- **BatchRun marked COMPLETED when all jobs failed** — `_run_windowed_batch` now sets `BatchRunStatus.FAILED` when `total_succeeded == 0` and `total_failed > 0`
- **Progress count dropped at end of windowed batch** — `completed_scenes` was set to `total_succeeded` only, ignoring failures. Changed to `total_succeeded + total_failed` so the progress reflects all processed scenes
- **Batch pipeline LLM timeouts** — Increased timeouts for all LLM-dependent batch steps (suggest-timeline, base-on-lyrics, character autogenerate, video flow generate) from 120-300s to 600s (10 minutes) to handle slower LLM providers and longer songs

## [1.6.2] - 2026-05-30

### Fixed
- **Export crash destroys hours of rendered clips** — When export failed at a post-rendering step (subtitle burn-in, audio mux, normalization), the error handler cleaned up the entire working directory including all rendered clips and the merged video. This made the existing resume detection useless — it could detect previous work but there was never any to find. Now the working directory is preserved on failure so the next export attempt can reuse already-rendered clips (per-clip duration validation) and skip directly past chunk merge (merged-video detection). Turns a multi-hour re-render into a ~30 second retry. Fixed in both music and narration assembly pipelines

## [1.6.1] - 2026-05-30

### Fixed
- **Narration export audio volume drop** — FFmpeg's `amix` filter divides each input's volume by N (number of inputs) by default, causing massive volume loss when mixing narration + backing tracks. Added `normalize=0` to preserve the original gain of each track. Previously, a 3-track mix would reduce each track to ~33% of its configured volume
- **Volume boost not applied during export** — Narration and backing track volume filters only applied when volume was `< 1.0`, silently ignoring boost values `> 1.0`. Fixed condition to use `abs(volume - 1.0) > 1e-6` so both attenuation and amplification are applied correctly
- **Subtitle burn-in crash on Windows paths** — FFmpeg `ass` filter parsed the colon in Windows drive letters (e.g., `D:`) as a filter option separator, causing `Unable to parse option value` errors. Both backslash-escaping (`\:`) and single-quote wrapping failed on Windows FFmpeg builds. Fixed by setting FFmpeg's working directory (`cwd`) to the ASS file's parent folder and referencing only the basename in the filter — the filter sees `ass=subtitles.ass` with no path, no drive letter, no colon to escape
- **Video prompt enhancer ignores scene sequence during auto-gen** — In all auto-gen modes (single, FF/LF, missing videos), the LLM prompt enhancer had no knowledge that consecutive scenes were related, causing wild visual shifts between scenes. Root cause: `use_prev_scene_last_frame` was always set to `False` during auto-gen, which gated out the entire continuity context block. Three fixes: (1) Removed the `use_prev_lf` gate — continuity context now fires whenever `prev_scene` is provided; (2) Added two tiers of continuity language: "SHOT EXTENSION (CRITICAL)" for FF/LF mode (same shot, camera still rolling) vs "NARRATIVE CONTINUITY" for sequential mode (different frame, maintain visual coherence); (3) `missing_videos_single` mode now passes the previous scene instead of `None`

## [1.6.0] - 2026-05-29

### Added
- **Single-pass FFmpeg filter graphs** — Clip normalization (scale+pad+setsar), duration padding (tpad), fade in/out, and color correction are now chained into ONE FFmpeg call per clip. Previously required 3-5 separate decode→encode cycles per scene, each with full quality degradation. New `process_clip_single_pass()` and `process_image_single_pass()` functions in ffmpeg.py
- **Parallel clip processing** — Independent clips are now rendered in parallel using `ThreadPoolExecutor` (FFmpeg subprocesses release the GIL, giving true parallelism without ProcessPoolExecutor serialization overhead). Default 4 workers, configurable via `RBMN_PARALLEL_CLIPS` env var. Both music and narration assembly pipelines use the parallel path
- **Stream-copy concat** — When no transitions are needed, clips are concatenated with `-f concat -c copy` (zero re-encode). Automatic fallback to filter concat if format mismatch is detected. New `concat_clips_copy()` function
- **FFmpeg threading flags** — All FFmpeg invocations now auto-inject `-threads 0 -filter_threads 4 -filter_complex_threads 4` for better CPU utilization across decode, filter, and encode stages
- **Pre-computed transition compensation** — Transition overlap padding is now calculated BEFORE clip creation and folded into the single-pass FFmpeg call, eliminating a separate re-render loop that previously added an extra decode→encode cycle per clip
- **Tmpfs intermediate files** — Export pipeline automatically uses `/dev/shm` (Linux tmpfs) for intermediate clip files when available and has sufficient free space (512 MB minimum). Eliminates disk I/O bottleneck for temp files. Configurable via `RBMN_TMPFS_DIR` and `RBMN_TMPFS_MIN_FREE` env vars. Falls back to output directory subdirectory on Windows/macOS or when tmpfs is unavailable
- **Ken Burns 8x upscale** — Image scenes now use zoompan at 8x resolution then downscale for higher quality Ken Burns effects, integrated into the single-pass pipeline

### Fixed
- **Frame-exact duration limiting** — `process_image_single_pass` now uses `-frames:v` (frame count) instead of `-t` (time-based) for precise output duration, eliminating off-by-one frame issues at non-integer framerates (e.g. 29.97fps)
- **Frame-exact fade timing** — All fade in/out effects in single-pass functions now use `start_frame`/`nb_frames` instead of `st`/`d` (time-based), ensuring fades align exactly to frame boundaries regardless of framerate
- **Dead cleanup code removed** — Removed stale cleanup paths targeting `output_dir` for `_colormatch/` and `concat_list.txt` that were unreachable after tmpfs migration (`_cleanup_tmpfs_dir` already handles full cleanup)
- **Dead imports removed** — Removed unused `apply_kenburns`, `apply_fade_in`, `apply_fade_out` imports from assembly.py (logic folded into single-pass functions)
- **Concat fallback parameters** — `concat_clips_copy` now forwards `fps` and `crf` to the filter-based concat fallback instead of using FFmpeg defaults
- **Chunk files written to tmpfs** — `_chunked_transition_merge` wrote chunk files to `output_dir` which receives `work_dir` (tmpfs) from callers. After `_cleanup_tmpfs_dir` runs, chunk download URLs became 404s. Added separate `chunk_output_dir` parameter so chunks are written to the durable project exports directory
- **Dead variable and redundant import** — Removed unused `all_indices` variable and redundant inline `import shutil as _shutil` (module-level `shutil` already imported)
- **V2V join-and-split crash** — `b_stem` was only assigned inside `if not output_b_path:` but used unconditionally on the next line to build `joined_path`. When dispatcher passes an explicit `output_b_path` (which it always does), `b_stem` was undefined → `NameError` crash. Moved assignment before the conditional
- **ffprobe double `-show_entries` flag** — `get_video_stream_duration` passed two separate `-show_entries` flags; ffprobe only honors the last one, so stream duration was silently ignored and the function always returned container/format duration. Merged into single `-show_entries stream=duration:format=duration`
- **Dead `zoom_inc`/`zoom_dec` variables** — Removed unused computed variables from `apply_kenburns` (leftover from pre-easing implementation)
- **Narration inline imports consolidated** — Moved `mix_audio_tracks`, `normalize_audio`, `generate_ass_subtitles`, `burn_subtitles` from inline imports inside try blocks to top-level imports in assembly.py. Removed redundant `get_media_info` re-import
- **Export crash from corrupt LTX audio** — LTX-generated clips contain garbage AAC streams (51 channels, invalid band types) that crash FFmpeg during decode with "Error reinitializing filters!" Added `-an` to all video-processing functions that don't need audio: `concat_clips`, `apply_transition`, `extract_frame` (both seek paths), `trim_video`, and `concat_clips_copy`. These intermediate operations never need audio — the master audio track is muxed at the very end by `mux_audio()`
- **Export crash from truncated clip reuse (moov atom not found)** — When FFmpeg crashes mid-encode, the moov atom (MP4 index) is never written, leaving a truncated file on disk. The resume logic in `_execute_clip_task` checked only `size > 0`, so truncated files passed and were reused in subsequent exports, causing `moov atom not found` errors. Two fixes: (1) Resume check now validates clips with `get_media_info()` — if duration is 0 or the file can't be probed, it's deleted and re-rendered. (2) `_run_ffmpeg` now deletes partial output files on both crash and timeout, preventing corrupt files from accumulating on disk

### Performance
- Export speed improvement: ~3-5x faster for typical 20-scene projects due to elimination of redundant decode→encode cycles and parallel processing
- Disk I/O reduction: tmpfs support eliminates SSD/HDD writes for intermediate files on Linux systems

## [1.5.3] - 2026-05-29

### Fixed
- **Ollama multi-server failover** — Fixed broken round-robin failover where the try/except block was outside the for loop, preventing retry on connection errors. Now correctly tries each server in rotation and continues on `ConnectionError`/`OSError`/`TimeoutError`
- **Auto-gen memory leak** — `_seq_auto_jobs` tracking dict entries are now evicted after 5 minutes via background asyncio task, preventing unbounded growth across multiple auto-gen runs
- **Assembly temp file cleanup** — Both `assemble_music_video` and `assemble_narration_video` now properly clean up intermediate files on all exit paths (success, error, cancellation) using try/except/else pattern
- **Zustand jobs array unbounded growth** — `addJob()` and `updateJob()` now call `pruneJobs()` (which was defined but never wired in). Caps at 200 entries, evicting oldest terminal jobs first
- **Auto-gen elapsed timer never resets** — Timer now clears `startTime` when the backend reaches a terminal state (done/failed/cancelled), ensuring the next run starts fresh instead of counting from the previous run's start time
- **Dispatcher null worker crash** — `submit_job()` auto-select path now raises a clear `ValueError` instead of crashing on `None.url` when no capable workers are available
- **Deduplicated `_group_words_into_sentences`** — Removed ~200-line divergent copy in `generation.py`; now imports the canonical version from `timeline.py` which includes section header and parenthetical line filtering

### Improved
- **Chunk gallery UX** — All four chunk gallery states (during export, done, failed, cancelled) now show rich 2-column cards with video thumbnail preview, play overlay on hover, file size display, and per-chunk download button. Lightbox overlay includes a header bar with download button

## [1.5.2] - 2026-05-28

### Fixed
- **Export quality mismatch** — Frontend sends "draft"/"standard"/"high" but backend CRF map expected "lossless"/"highest"/"high"/"medium"/"low". Only "high" mapped correctly; "draft" and "standard" silently fell back to CRF 16 (high quality). Now correctly maps: draft → CRF 26, standard → CRF 20, high → CRF 16
- **Stale clips on re-export** — Fresh exports no longer silently reuse leftover `clip_*.mp4` and `chunk_*.mp4` files from previous runs. Old artifacts are cleaned up before rendering begins
- **Export progress memory leak** — `_export_progress` dict entries for completed/failed/cancelled exports are now evicted after 10 minutes via a background asyncio task

## [1.5.1] - 2026-05-28

### Added
- **Export Crash Recovery** — New "Recover & Resume Export" button in the export modal. On modal open, scans the project's exports/ directory for leftover clip and chunk files from a crashed export (e.g., power loss, app crash). Shows a recovery banner with the count and size of recoverable artifacts. Clicking it starts a new export that skips already-rendered clips (idempotent checkpoint). Works even without a manifest — falls back to project defaults for export parameters
- **Incremental Manifest Saves** — Export manifest JSON (`export_manifest.json`) is now written after every chunk completes, not just at the end. This means crash recovery has chunk-level state on disk even if the app was killed mid-export
- **Export Scan Endpoint** — `GET /export/scan` returns a lightweight summary of what's on disk (clip count, chunk count, sizes, manifest status) without triggering a recovery
- **Export Recover Endpoint** — `POST /export/recover` scans the disk, rebuilds progress state from files + partial manifest, and starts a new export that leverages existing clips

### Fixed
- **Chunk download URL path** — Fixed chunk gallery download URLs using a non-existent `exports/chunks/` subdirectory instead of the correct `exports/` directory. Affected both the `on_chunk_complete` callback (live gallery during export) and the `_scan_export_dir` recovery scanner. Chunk lightbox previews now load correctly

## [1.5.0] - 2026-05-28

### Added
- **Chunked Export Assembly** — Exports now render in 