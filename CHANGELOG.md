# Changelog

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
- **Chunked Export Assembly** — Exports now render in batches of 25 scenes, producing intermediate chunk files that are concatenated at the end. Each chunk is viewable immediately via a gallery in the export modal. Boundary transitions (including AI transition clips) are correctly handled between chunks
- **Resumable Exports** — If an export fails mid-way, a manifest JSON is saved with all completed work. The new "Resume" button re-starts the export and automatically skips already-rendered clips (clip-level checkpointing)
- **Export Cancel** — Running exports can be cancelled via a "Cancel Export" button. Uses both asyncio.Task.cancel() and a flag checked between chunks for reliable cancellation
- **Chunk Gallery with Lightbox** — Completed chunks appear as clickable cards under the export progress bar. Click any chunk to open a full-screen video lightbox for immediate preview. Gallery persists across done, failed, and cancelled states
- **Export Phase Display** — Export modal now shows the current pipeline phase (Rendering clips, Merging chunks, Final assembly, Post-processing) and chunk progress (e.g., "Chunk 3 / 8")
- **Color Override** — Per-scene and project-wide color palette enforcement for image and video generation. 10 preset modes: Full Color, Black & White / Noir, High Contrast B&W, Sepia Tone, Monochrome Blue, Monochrome Red, Desaturated / Muted, Vintage Film, Neon Cyberpunk, and Custom Palette (free-text). Three-layer enforcement: (1) LLM context injection with MANDATORY priority prefix in both image and video enhance pipelines, (2) Strengthened color palette enforcement rules in all 7 system prompts, (3) Belt-and-suspenders keyword suffix injection in dispatcher before ComfyUI submission. Per-scene override in SceneEditor Image tab with fallback to project-wide default in Concept panel
- **5-Reference Klein Workflow** — Increased maximum reference images from 4 to 5 for Flux Klein 9B runs. New `KLEIN_EDIT_ULTRA_WORKFLOW_5REF.json` workflow with 5th ReferenceLatent chain link. Updated `_auto_workflow_type()`, dispatcher `klein_map`, `prepare_klein_workflow()`, Asset Generator modal (5 ref slots), and ReferenceSelector (first frame: 5 max, last frame: 4 max)

### Fixed
- **FFmpeg loudnorm crash on silent audio** — Export no longer crashes when audio normalization encounters silent or near-silent audio (measured_I=-inf). Detects -inf or <-70 LUFS values and skips normalization, copying the file unchanged

## [1.4.5] - 2026-05-28

### Added
- **Seed in Preview Popup** — Generation seed is now displayed in the image lightbox footer bar, the inline image gallery info bar, and the video gallery info bar. Shown in purple monospace font for easy visibility when browsing generation history

### Changed
- **Negative Prompt Disabled for Unsupported Workflows** — Klein and Z-Image Turbo workflows have no negative prompt node. Scene-level negative prompt textarea is now disabled with an explanatory note. Settings page "Global Negative Prompt (Image Generation)" field is disabled with a note that current image workflows don't support it. The global video negative prompt remains active (used by LTX Sequencer via LTXDirector). Dispatcher correctly ignores negative prompts for Klein/Z-Image and only passes them to Sequencer workflows

## [1.4.4] - 2026-05-28

### Fixed
- **Bug #1: Z-Image Turbo bypassed for ref workflows** — When Z-Image Turbo was selected as the single image generator, scenes with character references still used Klein T2I instead of Z-Image. Extracted `_try_zimage_redirect()` helper, added logging when Klein is forced due to refs, and added fallback re-check when all refs fail to resolve (effective_workflow == klein_t2i)
- **Bug #2: "Ignore Previous Scene Image as Reference" not persistent** — Toggle was reset by auto-gen modes and overwritten by React Query cache races. Fixed auto-gen to preserve existing `ignore_prev_scene_ref` values instead of blindly resetting to False, and added `queryClient.setQueryData()` sync in the frontend toggle handler to prevent stale cache overwrites
- **Bug #3: All image prompts start with "The subject"** — LLM few-shot examples in IMAGE_SYSTEM_PROMPT, NARRATION_IMAGE_SYSTEM_PROMPT, and TWO_PASS_COMPOSITE_SYSTEM_PROMPT all used "The subject..." as their example openers, creating a strong anchoring pattern. Diversified all examples to lead with environment, lighting, or action instead. Added explicit "VARY YOUR OPENING" instructions and color palette preservation rules to all three system prompts

## [1.4.3] - 2026-05-27

### Added
- **Subtitle Bold Option** — Bold toggle added to subtitle style settings (font-weight: bold). Persisted to `project.settings.subtitle_style.bold` and applied in both SubtitleOverlay preview and FFmpeg ASS burn-in
- **Subtitle Style Save Button** — Explicit Save button in subtitle settings panel persists font/size/color/position/outline/bold to project settings via API

### Fixed
- **SRT Subtitle Display Broken** — SRT subtitles would show the first line, then a random line, then nothing. Root cause: SQLAlchemy JSON column mutation detection. When `upload_srt` updated `existing_lyrics.words`, SQLAlchemy didn't detect the change to the JSON column, leaving old Whisper words (without `block` fields) in the DB. Fixed with `flag_modified()` from SQLAlchemy ORM. Additionally, the frontend SRT upload handler now uses the upload response data directly via `queryClient.setQueryData()` instead of `invalidateQueries()`, eliminating race conditions where refetch might return stale data
- **Subtitle Style Settings Not Persisting on Refresh** — `ProjectResponse` was missing the `settings` field, causing all `project.settings` (including subtitle styles) to reset to defaults on page load. Fixed by adding `settings` to the Pydantic response model
- **SRT Parser Windows Line Endings** — SRT files with `\r\n` line endings failed to parse. Added `\r\n` → `\n` normalization at the top of `_parse_srt_to_words()` plus UTF-8 BOM stripping and encoding fallback (utf-8-sig → utf-8 → latin-1) in the upload endpoint
- **Audio Mixer Volumes Not Restoring on Refresh** — Saved narration/backing track volume levels were lost on page refresh due to missing settings field in ProjectResponse (same root cause as subtitle styles above)

## [1.4.2] - 2026-05-27

### Added
- **Ken Burns on Export Modal** — Random Ken Burns toggle and effect subset picker now available on the Export modal, with override fields (`random_ken_burns`, `ken_burns_allowed_effects`) passed through `ExportRequest` → `_build_scene_dicts()` pipeline. Export overrides take priority over project.settings
- **Ken Burns on Auto Gen Modal** — Random Ken Burns toggle and effect subset picker on the Auto Gen modal. Settings are persisted to `project.settings` before auto-gen starts so the export pipeline picks them up
- **Per-Scene GGUF Model Override** — LTX Model (GGUF) dropdown on the Video tab allows per-scene model variant selection (Q8_0, Q6_K, Q5_K_S). Saved to `scene.parameters.ltx_model_gguf` and read by the dispatcher before falling back to global `AppSettings.ltx_model_gguf`. Works for both Sequencer and standard LTX workflow paths

### Fixed
- **Narration Images missing Transitions/Movement tabs** — `narration_images` mode was hiding transitions and movement (Ken Burns) tabs. Now only hides `video` and `stems` tabs, keeping transitions and movement visible since these are most useful for image narration projects
- **Per-scene GGUF override lost on settings read failure** — If the sequencer settings DB read fails, the dispatcher now preserves the per-scene GGUF override instead of resetting to `None`
- **Auto Gen Ken Burns not persisted** — `handleStart` in Timeline.tsx now saves Ken Burns settings to `project.settings` via `updateProject()` before kicking off auto-gen, so the export pipeline picks them up correctly

## [1.4.1] - 2026-05-27

### Added
- **Ollama Multi-Server Pool** — Configure multiple Ollama server URLs with automatic round-robin dispatch across them during batch prompt generation. Similar to ComfyUI worker pool distribution. Frontend shows server count badge with "round-robin" label
- **Ollama Local LLM Integration** — Full Ollama provider support: model listing across all servers, test connection scanning all URLs, OpenAI-compatible `/v1/chat/completions` API, optimized prompts for local models with shorter/simpler system instructions

### Fixed
- **Settings Export/Import Missing Fields** — `SettingsExportData` was missing 15+ fields added in recent releases: Ollama settings (base_url, urls, model), LTXDirector settings (guide_strength, audio_guidance, stitch, auto_image_desc, global_video_negative_prompt), video_fps, video_min_duration, global_negative_prompt, export_lfff_trim_enabled, image/video_prompt_guidance, gpu_acceleration_enabled. Also fixed `app_port` being passed to the export constructor without being declared in the schema (would crash the export endpoint)
- **Startup crash on existing databases** — `sqlite3.OperationalError: no such column: app_settings.ollama_base_url` fixed by adding `ALTER TABLE` migrations for all four Ollama columns in `init_db()`

## [1.4.0] - 2026-05-26

### Added
- **Narration Images Mode** — New project type for narration-driven still image slideshows. UI hides Video/Stems/Transitions tabs, forces image source type, and applies Ken Burns effects on export
- **Narration Videos Mode** — New project type for narration-driven video generation. Full video pipeline with speech-optimized LLM prompts for documentary/storytelling content
- **SRT Upload** — Upload .srt subtitle files (e.g., from ElevenLabs) as an alternative to Whisper transcription. Parses SRT into word-level timestamps and upserts into the lyrics system
- **Subtitle Burn-In** — ASS subtitle generation from word timestamps with configurable font, size, color, position, and outline. FFmpeg `ass=` filter burns subtitles into final export
- **Subtitle Preview** — Live subtitle overlay in VideoPreview component synced to playback position
- **Backing Track Timeline** — New timeline area below the main scene timeline for adding background music/audio tracks. Drag-drop or upload audio files, colored track bars, inline volume sliders, delete controls
- **Audio Mixer** — Per-track volume control (dB) for backing tracks with FFmpeg `amix` complex filter graph mixing during export
- **Audio Normalization** — Optional two-pass FFmpeg `loudnorm` normalization (target -16 LUFS) during export for consistent audio levels
- **Narration LLM Prompts** — Dedicated `NARRATION_IMAGE_SYSTEM_PROMPT` and `NARRATION_VIDEO_SYSTEM_PROMPT` with documentary/storytelling focus. Auto-selected when project mode is narration
- **Narration Export Pipeline** — Full export assembly for narration modes with transitions (xfade), CRF quality, color matching, AI transition clips, backing track mixing, subtitle burn-in, and audio normalization
- **Export Subtitle Controls** — Narration export modal includes subtitle toggle, font/size/color/position/outline settings, and normalize audio checkbox
- **Auto Gen Modal Minimize** — Full Set auto-generation modal can now be minimized to a floating status pill, allowing navigation during long generation runs
- **Random Ken Burns Effects** — Project-level setting (narration_images only) to randomize Ken Burns effects during export/preview. Configurable effect pool with 16 effects and per-effect checkbox filtering. Random intensity (30–70) and ease_in_out easing applied at `_build_scene_dicts()` time so both export and render preview share the same logic. Manual per-scene effects always take priority over random assignment

## [1.3.3] - 2026-05-26

### Fixed
- **PowerLoraLoader class_type mismatch** — `_update_power_lora_distilled()` checked for `"PowerLoraLoader"` but the actual ComfyUI node class_type is `"Power Lora Loader (rgthree)"` (with spaces), so the function silently did nothing and every workflow always sent the hardcoded v1.1 LoRA regardless of user settings. Fixed string match to cover both formats. Also resolves OOM errors during "Generate All Missing – Use Previous Frame" auto-gen caused by LoRA cache thrashing

## [1.3.2] - 2026-05-26

### Added
- **Per-Scene Lyrics Override** — Override button in the Lyrics tab opens an editable textarea with the scene's auto-detected lyrics. Save persists the override to scene parameters; Reset clears back to auto-detected. Yellow "Overridden" badge indicates manually edited scenes

### Fixed
- **Distilled LoRA not applied to non-Sequencer LTX workflows** — Standard I2V, FF/LF, V2V, and Transition workflows had the distilled LoRA filename hardcoded in `PowerLoraLoader` nodes. The Settings selector only worked for Sequencer workflows. Added `_update_power_lora_distilled()` helper that dynamically patches all `PowerLoraLoader` nodes, and wired the setting through the dispatcher for all LTX workflow paths

## [1.3.1] - 2026-05-26

### Fixed
- **NVENC detection on Blackwell GPUs (RTX 5070+)** — Encoder capability test used 64×64 frames, below Blackwell's minimum encode size (~145×49). Bumped test resolution to 256×256, safe for all NVENC generations back to Kepler
- **Instrumental track suggest_timeline 500 error** — The entire `valid_cuts` builder was inside `if word_timestamps:`, so instrumental tracks (no lyrics) got zero cut points. Added `else` branch that seeds cut points from section boundaries and fills long gaps with evenly-spaced instrumental splits
- **LLM JSON parse failure on prose responses** — When the LLM returns reasoning text instead of raw JSON (common with zero cut points), the parser now attempts to extract a `[{…}]` array from within the prose before raising a 500
- **Auto Gen modal not minimizable** — Modal can now be minimized during long generation runs
- **gpu_acceleration_enabled migration** — Added idempotent `ALTER TABLE` migration so the column is created safely on first run without errors on subsequent starts

### Added
- **Ko-fi support button** — Added Ko-fi donation link to README

## [1.3.0] - 2026-05-25

### Added
- **Batch Mode System** — Queue multiple audio files with per-item configuration (render type, video mode, two-pass, story flow, auto characters) and run them as a batch pipeline
- **Persistent Batch Runs (Auto Gen Dashboard)** — All Auto Gen runs are persisted to the database with full tracking. New `/batches` dashboard shows all runs with status cards, progress bars, and filtering
- **Batch Run Detail View** — Click any batch run card to see per-scene progress, live activity feed with step-by-step logging, image/video lightbox, and error details
- **Live Activity Feed** — Real-time step log during batch processing with scene names, worker IPs, asset thumbnails, and timestamps
- **Auto Character Generation in Batch** — Optional checkbox to auto-generate characters during batch processing using the concept/lyrics-based character autogeneration pipeline
- **Concept Data Persistence in Batch** — Batch pipeline now reads and persists song_title, concept_text, and style_text from the base-on-lyrics LLM response to project settings
- **Live Elapsed Timers** — Running batch cards on dashboard show live-ticking elapsed time computed from `started_at`; completed batches show final elapsed time
- **Video Thumbnails on Dashboard** — Batch run cards display the last generated asset (image or video) as a thumbnail with video poster frame support via `<video preload="metadata">`
- **Persistent Auto Gen Status Bar** — Active Auto Gen runs show a status bar in the project view with progress, current step, and link to batch detail

### Fixed
- Video autoplay disabled on batch detail screen — videos are present but require manual play
- Broken video thumbnails on Batches dashboard when last asset is a video (now uses `<video>` tag with `#t=0.1` fragment)
- Erratic elapsed timer on batch detail screen — now computed from `started_at` instead of stale `elapsed_ms` field
- Live activity feed stopping auto-refresh after terminal state — added 5-second delayed poll stop
- "Just now" showing on all activity feed timestamps — fixed UTC timestamp parsing (append 'Z' suffix for correct JS Date parsing)
- Batch-created projects missing song_title and concept text — batch pipeline now persists base-on-lyrics response

## [1.2.0] - 2026-05-24

### Added
- **LTXDirector Full Integration** — Settings UI for guide strength, audio guidance, stitch mode, auto image description, and video negative prompt. All parameters wired through dispatcher to ComfyUI workflow
- **Per-Scene Lipsync System** — Toggle in Video tab (default ON) that boosts audio_guidance to 0.7+ for mouth-to-audio sync. Optional vocal stem isolation, wired into Auto Gen modal and manual generation
- **Lipsync Default for New Projects** — "Enable Lipsync by Default" checkbox on New Project screen
- **VIDEO_SYSTEM_PROMPT Rewrite** — Full LTXDirector awareness with multi-segment prompt documentation, keyframe image guidance, audio-reactive tips, and negative prompt delegation
- **Image/Video Deletion Auto-Fallback** — Deleting chosen image/video auto-selects next available version
- **Live Batch Preview PIP** — Floating draggable overlay during batch processing showing last generated asset with scene info
- **Mobile Responsive Layout** — Full mobile support with bottom nav bar, panel toggling, and tablet breakpoint

### Fixed
- SceneEditor handler operation order (API-first pattern)
- Lipsync default mismatch in manual generate_video
- Missing vocals_only_for_lipsync in windowed batch mode

## [1.1.0] - 2026-05-23

### Added
- **Three-Model Architecture** — Klein 9B (edit/reference), Z-Image Turbo (fast T2I), LTX 2.3 (video) with automatic workflow routing
- **Distilled LoRA v1.1** — Updated to improved aesthetics and audio quality, 8-step generation
- **LAN/WAN Network Access** — Settings toggle to allow remote access to the app
- **Custom Port Setting** — Configurable app port in Settings

### Fixed
- Klein workflow CFG, steps, and upscale method audited against official templates
- Per-scene transition persistence

## [1.0.0] - 2026-05-19

### Added
- Full AI music video / narration video pipeline: audio analysis, image generation, video generation, export
- ComfyUI integration with FLUX Klein 9B (images) and LTX 2.3 (video) via remote API
- Multi-server ComfyUI worker pool with capability routing and least-loaded dispatch
- First Frame / Last Frame image generation with per-frame references and prompts
- Video-to-Video (V2V) extending with image-based conditioning for scene continuity
- AI Transition clips via LTX Transition LoRA between scenes
- Two-pass image generation: scene composition (Pass 1) then character insertion (Pass 2)
- LLM-powered prompt enhancement (OpenAI, Anthropic, Gemini) with model-specific system prompts
- Video Flow: LLM-generated per-scene storyboard ideas with location diversity
- Concept panel: song title, concept text, style, characters, image direction presets
- Character Creator with reference images, generation, version gallery
- Auto Generate modes: sequential per-scene, parallel batch, V2V extend, missing-only
- Whisper transcription (local WhisperX, remote Gradio, ComfyUI workflow)
- Demucs stem separation with GPU acceleration
- Audio section detection via librosa novelty analysis
- Suggest Fresh Timeline with phrase-aware boundary snapping
- Scene locking to prevent accidental boundary changes
- Per-channel color correction with FFmpeg colorchannelmixer
- Adjacent-clip color matching for export assembly
- GPU-accelerated FFmpeg encoding and decoding (NVIDIA, AMD, Intel)
- Export with crossfade transitions, Ken Burns effects, quality CRF control
- Render Preview for quick 720p assembly
- RunPod serverless GPU pod management
- Settings import/export
- Seed control: global seed, per-frame overrides
- pywebview desktop wrapper with native window
