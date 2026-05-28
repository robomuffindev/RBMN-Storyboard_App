# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
