# Timeline editing — full feature map

This is the complete set of ways to build and edit a project's scene timeline.
Scenes are time-ordered rows (`start_time`, `end_time`, `order_index`, `name`)
under a project; chapters group contiguous scenes. Auto and manual flows
coexist — auto is the default, manual is always available, and neither breaks the
other.

## Automatic (the default for most users)
- **Audio analysis** (`/timeline/analyze`) — upload/select audio → sections, lyrics
  (Whisper) or SRT cues, BPM, optional Demucs stems.
- **SRT upload** (`/timeline/upload-srt`) — ElevenLabs/other SRT cues become the
  narration timing (re-anchored to the audio). Pairs with the "Disable Whisper"
  toggle for SRT-only projects.
- **Suggest Timeline** (`/timeline/suggest-timeline`) — generates the whole scene
  list from lyrics/sections (DP segmentation for narration; LLM for music video),
  then slices audio per scene and rebuilds chapters. **Replaces** all scenes.

## Manual (power users)
- **Add Scene** — toolbar button in the Timeline; appends a new blank scene at the
  end (`POST /scenes`). The new scene is clamped to the audio length and **bound to a
  chapter** (so chapter-scoped Auto-Gen/Export include it). Build from scratch or extend.
- **Split at playhead** — Timeline "Split" button cuts the scene under the playhead
  into two (updates the original's end + creates a new scene for the remainder).
- **Numeric Start/End entry** — Scene → **Tools** tab: type exact start/end seconds
  and hit **Set** (`PUT /scenes/{id}`); the scene's audio re-slices to match.
- **Drag scene boundaries** — drag the divider between two scenes on the Timeline to
  move their shared cut (`onBoundaryDrag` → `PUT /scenes/{id}`).
- **Delete scene** — robust delete with a merge strategy (previous / next / gap),
  chapter-aware, re-indexes order (`DELETE /scenes/{id}`).
- **Reorder / cleanup** — `PUT /scenes/reorder` (set order_index) and
  `POST /scenes/cleanup` (de-dupe + re-index) exist for fixing up a timeline.
- **Lock Scenes** — `project.settings.scenes_locked` prevents auto-resync from
  changing boundaries while you hand-edit.

> Note on "drag to reorder": scenes are **time-ordered**, so repositioning a scene
> means changing its times — done via boundary-drag and numeric Start/End entry
> (which re-sort naturally by time). A free-form "drag the block to a new index"
> independent of time isn't offered because it would desync `order_index` from the
> timeline; adjust times instead.

## Import an ElevenLabs AAF (1.14.0)
Project **3-dots menu → Import AAF (ElevenLabs)**:
1. Parses the AAF (`pyaaf2`) audio timeline → clip cut points → scene boundaries.
2. **Replaces** all scenes (clear confirm in the dialog).
3. Audio: use the project's existing audio, or upload a new file in the dialog —
   then sliced per scene.
4. Rebuilds chapters. Same post-processing as Suggest Timeline.

`backend/services/import_aaf.py` (`parse_aaf_to_scenes` + the pure, tested
`clips_to_scenes`). **Validated on a real ~238MB ElevenLabs AAF** → 377 contiguous
scenes. Note: ElevenLabs AAF clips are unnamed (generic "Render"), so scenes import
as "Scene 1…N" with correct timings; the AAF's embedded audio is NOT used — you
provide audio in the import dialog; dialogue text is in ElevenLabs' CSV export, not
the AAF. The endpoint is `POST /timeline/import-aaf` and requires `pyaaf2` in the
backend env (`pip install pyaaf2`); it returns a clear message if it's missing.
ElevenLabs Dubbing Studio also exports a **CSV** (speaker, start, end,
transcription) which is even simpler to parse — a natural future addition if users
prefer it.

## Safety
- Imports/Suggest **replace** scenes (with confirmation); manual ops are additive.
- Audio slicing and chapter rebuild are best-effort (logged, non-fatal).
- Manual delete/add are chapter-aware and re-index `order_index` contiguously.
