# Changelog

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
