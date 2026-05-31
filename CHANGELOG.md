# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
