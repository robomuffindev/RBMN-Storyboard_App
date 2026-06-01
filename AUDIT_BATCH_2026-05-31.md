# Batch Processing Audit
**Date:** 2026-05-31
**Scope:** Project Batch Mode end-to-end (frontend → API → pipeline → auto-gen).
**Files inspected:** `backend/api/batch.py`, `backend/api/generation.py`, `frontend/src/components/Layout/ProjectList.tsx`.

## TL;DR

The batch pipeline's architecture is sound but it has **several real bugs that explain "tests had problems and failures"** — most importantly two race conditions that cause "false success" (the batch marks itself complete but no images/videos were actually generated), and two patterns that can hang batch items forever.

If you only fix three things, fix #B-1, #B-2, and #B-3 — they cover the failure modes that look like "ran fast and produced nothing" and "stuck forever."

---

## CRITICAL

### B-1 — "Idle = done" race causes silent false-completes
`backend/api/batch.py:1137, 1192`

The poll loops in step 7 (images) and step 8 (videos) treat `"idle"` as a terminal status:

```python
if gen_status in ("done", "failed", "cancelled", "idle"):
    break
```

`status="idle"` is what the backend returns when `_seq_auto_jobs.get(pid)` is `None` — either because the run hasn't started yet, OR because the entry was evicted 5 minutes after it finished. The first case is the practical problem:

1. Batch POSTs `/generate/auto-sequential` to kick off the run.
2. Backend's start-run endpoint is supposed to set `_seq_auto_jobs[pid] = {"status":"running",...}` BEFORE returning, but if the POST returns 500 / 4xx (e.g. project already mid-run, validation failure from our new `_VALID_MODES` guard, momentary backend error), the entry never gets populated.
3. Batch only checks `if resp.status_code != 200: logger.warning(...)` — it does **not** abort. It falls into the poll loop.
4. First poll comes back `status="idle"` → "terminal" → loop exits → "complete" → checkpoint advances → next step starts with **zero images/videos generated**.

**Fix:** Verify the kickoff actually succeeded before polling, and treat `"idle"` as a failure during the active poll:

```python
if resp.status_code != 200:
    raise RuntimeError(
        f"Auto-gen kickoff failed: {resp.status_code} {resp.text[:300]}"
    )

# Inside the poll loop, allow ONE idle-tolerant tick to catch the
# pre-run race; if we still see idle after that, it's a real problem.
saw_running = False
while True:
    ...
    if gen_status == "running":
        saw_running = True
    elif gen_status in ("done", "failed", "cancelled"):
        break
    elif gen_status == "idle":
        if saw_running:
            break  # legitimate eviction after a real run
        raise RuntimeError("Auto-gen never started — saw 'idle' before 'running'")
```

### B-2 — Poll loops have no timeout (can hang forever)
`backend/api/batch.py:1128–1148` (images), `1183–1203` (videos)

Both are `while True` with no maximum total time. If the auto-gen run wedges (workflow error not caught, ComfyUI unreachable, a single video takes 30+ min), the batch item is stuck indefinitely. The user sees "generating images: …" or "generating videos: …" forever and the only escape is killing the backend.

**Fix:** Add a hard cap, e.g. 2 hours per step, that raises so the item gets marked failed and the batch moves on:

```python
import time as _t
deadline = _t.monotonic() + 7200  # 2h ceiling
while True:
    if _t.monotonic() > deadline:
        raise RuntimeError("Auto-gen poll timed out after 2h")
    ...
```

### B-3 — `video_mode_map` silently demotes unknown modes to `all_video_single`
`backend/api/batch.py:1162–1166`

```python
video_mode_map = {"i2v": "all_video_single", "v2v": "all_video_v2v"}
video_gen_mode = video_mode_map.get(config.video_mode, "all_video_single")
```

- The map has only `i2v` and `v2v`.
- The `BatchItemConfig.video_mode` Pydantic field has no enum validation.
- There's no `fflf` key — so even if the UI eventually offered FF/LF chaining, batch wouldn't route it. (And if the user manages to send `"fflf"` via the JSON, it silently falls back to `all_video_single`.)

**Fix:** Make the map exhaustive and reject unknowns:

```python
video_mode_map = {
    "i2v": "all_video_single",
    "v2v": "all_video_v2v",
    "fflf": "all_video_fflf",
}
if config.video_mode not in video_mode_map:
    raise RuntimeError(f"Unknown video_mode {config.video_mode!r}")
video_gen_mode = video_mode_map[config.video_mode]
```

Also add `fflf` to the frontend `BatchItemAddModal` mode picker so the user can actually pick it.

### B-4 — Failed items leave orphan projects in the DB and on disk
`backend/api/batch.py:478-498`

When `_process_single_item` raises mid-pipeline, the `except` only marks the item failed. The project row, the audio file, the stems, the partial generation — all persist. After a batch with 5 failed items, the user has 5 broken projects to clean up manually. No mention of this in the UI.

**Fix (smallest):** Add a comment on the item state describing what was created so the user can manually delete. **Fix (better):** Best-effort `DELETE /api/projects/{project_id}` on failure UNLESS the item got past `_STEP_IMAGES_GENERATED`, since by then there's expensive work to preserve.

---

## HIGH

### B-5 — Polling exits on `_check_cancelled` *after* the poll fetch
`backend/api/batch.py:1128–1148, 1183–1203`

`_check_cancelled` runs at the top of each iteration, but the actual `client.get(...)` doesn't have cancel awareness. If the auto-gen is stuck in a 20-minute LTX render, the user can press "Cancel" — but the batch will sit for up to ≈ the auto-gen timeout (450s in the current `stream_and_wait` worst case) before re-checking cancellation. Combined with B-2 there's no upper bound.

**Fix:** wrap `client.get` in `asyncio.wait_for(..., timeout=10)` and tighten the inner cancel check.

### B-6 — Concept generation always calls LLM even when user supplied direction + style
`backend/api/batch.py:1014-1022`

`base-on-lyrics` is invoked unconditionally. The user-provided `concept_direction` / `style_text` later overwrite the LLM result. So if your batch has user-supplied concept text, you still pay for an LLM call (and depend on it succeeding) for no functional benefit.

**Fix:** Skip the base-on-lyrics call when both `config.concept_direction` and `config.style_text` are populated.

### B-7 — Whisper / Demucs in `to_thread` with no item-level timeout
`backend/api/batch.py:827-836`

`analyzer.analyze_full` runs in a thread. Demucs has the 30-min subprocess timeout we added earlier, but Whisper does not. A wedged Whisper means the batch item silently sits in "analyzing audio" for hours.

**Fix:** Wrap in `asyncio.wait_for(asyncio.to_thread(...), timeout=3600)` (1h cap).

### B-8 — Race-prone module-level dict, single shared `_batch_runs`
`backend/api/batch.py:46, 221, 230, 312`

`_batch_runs` is a plain `dict` mutated from multiple places. Starting two batches in quick succession (uncommon but possible) or hitting `cancel`/`retry` while the pipeline is iterating writes from the API thread while the background task reads/writes from another. SQLite serializes the DB side, but the in-memory state has no lock.

**Fix:** Use a single `asyncio.Lock` or `dict[str, asyncio.Lock]` keyed by `batch_id` around state mutations. Low priority because batch is one-at-a-time in practice.

### B-9 — Lyrics save fallback drops `initial_text`
`backend/api/batch.py:938-959`

The except branch retries saving lyrics but constructs a `Lyrics(...)` WITHOUT the `initial_text` field. If `initial_text` is what caused the original failure (e.g. a long unicode user input that violated some constraint), the retry "works" but the user's intended lyrics seed is silently dropped. We log a warning but don't surface it.

**Fix:** Identify why the original save fails (probably constraint violation or transaction corruption — gotcha #9 in feedback memory) and apply `flag_modified` or a fresh session instead of the silent stripped-retry.

### B-10 — `BatchItemAddModal` UI doesn't expose FF/LF or override-full-set
Inspected `ProjectList.tsx` plus the `BatchItemConfig` shape. Configurable fields are:

- `audio_filename`, `audio_upload_path` (auto)
- `lyrics_text`, `project_name`, `concept_direction`, `style_text`
- `render_type` (music_video / narration_video)
- `video_mode` (i2v / v2v)
- `two_pass`, `use_story_flow`, `auto_characters`

Missing things present in the Auto Gen modal:
- FF/LF chaining (the most common music-video mode)
- `lipsync_enabled` / `vocals_only_for_lipsync`
- `override_full_set` (always treated as missing-only currently)
- Image mode is hardcoded to `missing_images_independent` — user can't pick `all_images` with prev-scene refs

That's why batch results feel less customizable than single-project Auto Gen.

---

## MEDIUM

### B-11 — Project-name auto-derivation strips suffixes case-sensitively
`backend/api/batch.py:204-207`

`name.lower().endswith(suffix)` is checked, but the slice is on the unlowered name — fine. The list is `("_master", "_final", "_mixed")`. Misses common variants: `Master.mp3` (capitalized, no underscore), `My Song - Final Mix.wav`, etc. Cosmetic.

### B-12 — Staging cleanup leaves orphan files when item fails before step 2
`backend/api/batch.py:511-521`

The `finally`-style cleanup runs in the per-item block, but if the item raises before the project copy, the source upload still gets unlinked. Then on retry, the resume can't find the audio. This bites the retry path harder than the success path.

**Fix:** Only unlink staging file once the item is `done`. Move cleanup into the success path.

### B-13 — Concept POST `json={}` may not match endpoint expectations
`backend/api/batch.py:1016`

`base-on-lyrics` is POSTed with `json={}` — empty body. If the endpoint expects optional fields it'll work; if it requires anything, returns 422 and batch logs a warning then continues with no concept. Verify by reading `api/concept.py base_on_lyrics` handler.

### B-14 — Auto-characters returns warning on failure but loop continues
`backend/api/batch.py:1072-1077`

If character generation fails (LLM quota, bad config), the warning is logged but no scenes have characters — downstream image generation produces character-free scenes. User has no idea the auto-character step failed.

**Fix:** Either propagate the failure or surface in step_log with type="warning" so it shows in the Activity Feed.

### B-15 — `httpx.AsyncClient` timeout of 30s for kickoff POST is too tight
`backend/api/batch.py:1111, 1168`

The kickoff itself is fast (just creates a job), so 30s is plenty. But if backend is loading models / cold-start, the POST can stall. Then the batch raises a timeout — fine. Not a real bug, but consistency: other steps use 600s.

### B-16 — Pydantic field-name collision: `BatchRunStatus` (batch.py) vs `BatchRunStatus` enum (models.py)
Already aliased as `BatchRunStatusEnum`, but the OpenAPI schema will show two `BatchRunStatus` entries — one Pydantic class, one enum. Cosmetic for now (no runtime collision since they're in separate modules), but the swagger UI will be confusing.

---

## LOW / Observations

- `asyncio.create_task(_run_batch_pipeline(batch_id))` at line 229 stores the task in `_batch_runs[batch_id]["task"]` — strong-ref preserved, no GC risk. ✓
- All `asyncio.create_task` calls for fire-and-forget DB updates were converted to `_track_task` in the prior session. ✓
- `audio_dest` is correctly recovered on resume in step 2's skip branch (line 763). ✓
- The modes the batch sends to `_run_sequential_auto_gen` are all valid backend modes: `missing_images_independent`, `all_video_single`, `all_video_v2v`. They will NOT hit the `_VALID_MODES` guard we added earlier. ✓
- `_run_sequential_auto_gen` is a windowed-mode for all three of these → fast parallel dispatch. ✓
- Frontend polls every 3s; cleanup on unmount is correct. ✓

---

## Correlation with "had problems and failures"

The failure modes most likely to match what you observed:

1. **"Batch said complete but no images/videos generated"** → B-1 (idle race) and B-3 (mode demote). The kickoff POST may have been failing while batch ignored it.
2. **"Item ran for an hour and never finished"** → B-2 (no poll timeout) compounded with B-5 (cancel doesn't interrupt).
3. **"Failed items left junk projects in my list"** → B-4 (no cleanup).
4. **"Retried but resume picked up wrong state"** → B-12 (staging file unlinked too eagerly).

---

## Suggested fix order

| Order | Fix | Effort | Impact |
|---|---|---|---|
| 1 | B-1 idle race + verify kickoff success | 15 min | Eliminates silent false-completes |
| 2 | B-2 poll timeout (2h cap per step) | 5 min | No more infinite hangs |
| 3 | B-3 exhaustive video_mode_map + fflf | 5 min | Restore FF/LF in batch |
| 4 | B-4 best-effort project cleanup on failure | 20 min | No more orphan projects |
| 5 | B-7 Whisper timeout via wait_for | 5 min | No more wedged audio analysis |
| 6 | B-10 expose remaining options in BatchItemAddModal | 30 min | Restore configurability |
| 7 | B-9 lyrics save fallback investigation | 30 min | Stop dropping user lyrics |

Items 1–3 cover the "silently broke" scenarios. Items 4–7 are quality-of-life cleanups.
