# Chapters, Sub-Chapters, Shortcodes & Series — Blueprint v1

**Status:** DESIGN — review and approve before implementation.
**Author:** This session. **Date:** 2026-05-31.
**Scope:** RBMN Storyboard App, narration mode primary, music mode where it makes sense.

---

## 1. The Big Picture

The app today treats a project as a flat list of scenes on one timeline. That works for a 3-minute song. It falls over for a 45-minute audiobook chapter or a multi-episode series:

- LLM context windows can't hold the whole project at once.
- The user wants to focus-work on one section without scrolling through 300 scenes.
- Re-exporting a full long video to tweak one part is wasteful.
- Assets generated for "Episode 2" should be findable, taggable, and reusable in "Episode 7" of the same series.

This blueprint introduces four concepts to fix all of that at once:

| Concept | One-line definition |
|---|---|
| **Chapter** | Named umbrella over a contiguous range of scenes, derived from `# heading` markers in the script OR auto-split by scene count. Drill-downable, exportable, LLM-batchable. |
| **Sub-Chapter** | A Chapter nested under another Chapter, up to 3 levels deep. Same behavior, smaller scope. |
| **Shortcode** | A short, stable, human-readable identifier for every asset in the system (`a3f9-img-0047`). Searchable, copy-pasteable, migration-safe. |
| **Series** *(Phase 3 — deferred)* | Optional grouping of projects ("Season 1 episodes"). Shared character pool, cross-project asset reuse. |

---

## 2. Data Model Changes

### 2.1 New table: `chapter`

```python
class Chapter(SQLModel, table=True):
    id: UUID = Field(primary_key=True, default_factory=uuid4)
    project_id: UUID = Field(foreign_key="project.id", index=True)
    parent_chapter_id: Optional[UUID] = Field(foreign_key="chapter.id", default=None, index=True)
    order_index: int                       # within parent (or project if top-level)
    depth: int = 0                         # 0 = top-level, 1 = sub, 2 = sub-sub; cap at 2
    name: str = ""                         # "Chapter 1: The Beginning"
    short_code: str = Field(index=True)    # "a3f9-ch-01" — see Shortcodes section
    color: str = "#7c3aed"                 # hex, for timeline overlay
    auto_generated: bool = True            # True if from auto-split, False if from `# header`
    source: str = "auto"                   # "auto" | "script_header" | "manual"
    script_offset_start: Optional[int] = None  # char offset into Lyrics.initial_text
    script_offset_end: Optional[int] = None
    start_time: float = 0.0                # derived from member scenes
    end_time: float = 0.0                  # derived from member scenes
    tags: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    metadata: dict = Field(default_factory=dict, sa_column=Column(JSON))
    created_at: datetime
    updated_at: datetime
```

**Why parent_chapter_id instead of two separate tables?** Self-reference keeps the tree flexible (chapter → sub → sub-sub) without table proliferation. Depth cap at 2 keeps UI sane.

### 2.2 Scene gets a `chapter_id`

```python
class Scene(SQLModel, table=True):
    # ...existing fields...
    chapter_id: Optional[UUID] = Field(foreign_key="chapter.id", default=None, index=True)
```

Always points to the **leaf** chapter (most specific). Walk up via `parent_chapter_id` for ancestry.

### 2.3 Asset gets a `short_code`

```python
class Asset(SQLModel, table=True):
    # ...existing fields...
    short_code: str = Field(index=True, unique=True)   # "a3f9-img-0047"
    tags: list[str] = Field(default_factory=list, sa_column=Column(JSON))
```

### 2.4 AppSettings: LLM batch limits

```python
llm_chapter_scene_limit_cloud: int = 25      # OpenAI / Anthropic / Gemini
llm_chapter_scene_limit_ollama: int = 12     # local Ollama — typically smaller context
chapter_auto_split_threshold: int = 25       # scenes per auto-chapter
chapter_max_depth: int = 2                   # 0..2 = 3 levels
```

### 2.5 Migration approach (no Alembic — manual SQL like existing changes)

`init_db()` in `backend/database/database.py` already does idempotent `ALTER TABLE` patches. Add a `0XX_chapters.py` patch module that:

1. Creates `chapter` table if not exists
2. `ALTER TABLE scene ADD COLUMN chapter_id` if missing
3. `ALTER TABLE asset ADD COLUMN short_code, tags` if missing
4. Backfills `short_code` for existing assets (`{project_prefix}-{type}-{seq}`)
5. For each project, runs **first-time auto-chaptering** (creates one default chapter covering all scenes — user can re-split later)
6. Adds new AppSettings columns with defaults

The patch is fully idempotent — safe to run repeatedly.

---

## 3. The Chapter Resolution Pipeline

When does a project's chapter tree get computed? Three triggers:

1. **Script changes** (Lyrics.initial_text update) → re-parse headers, reconcile chapters
2. **Scene order changes** (reorder, split, merge) → re-bind scenes to chapters by time overlap
3. **Manual request** (`POST /chapters/reparse`) → forced regenerate

### 3.1 Script header parser

```
Input:  initial_text from Lyrics
Output: list[ScriptHeader{ depth: 0..2, name: str, char_offset: int, word_index_in_clean: int }]
        clean_text: str  (the script with header lines stripped — what gets aligned to audio)

Header syntax (Markdown-style):
    # Heading           → depth 0
    ## Heading          → depth 1
    ### Heading         → depth 2
    #### + → ignored (over the depth cap)

Edge: header line that's ALSO part of narration audio? We assume headers are
silent markers — they don't appear in audio. If user wants narrated chapter
intro, they should write it as regular text. Headers are stripped from the
canonical text BEFORE word-alignment to Whisper output.
```

### 3.2 Chapter builder

```
Input:  ScriptHeaders + project's reconciled Lyrics.words (after text_align)
Output: tree of Chapter rows + scene → chapter_id mapping

Algorithm:
1. If headers exist:
   For each header H:
       H.start_time = lyrics.words[H.word_index_in_clean].start  (or interpolated)
   Build chapter tree from headers respecting depth.
   Last chapter's end_time = max(scenes.end_time).
2. If NO headers:
   Auto-split into chapters of N scenes (N = chapter_auto_split_threshold).
   Use song-section boundaries as preferred split points if available.
   Auto-name "Chapter 1", "Chapter 2", ...
3. Post-pass — auto-split oversized chapters:
   For each chapter at depth < chapter_max_depth:
       If member_scenes > llm_chapter_scene_limit_cloud:
           Split into N sub-chapters of ≤ limit scenes each.
4. For each scene, assign chapter_id = leaf chapter whose time range contains scene.start_time.
```

### 3.3 Script edit reconciliation

When user edits the script:
1. Diff old headers vs new headers (match by `(depth, normalized_name)`)
2. Matched → keep the existing Chapter row (preserves color, tags, custom name edits)
3. New → create new Chapter
4. Removed → mark as `orphan=True`; if it has no manual edits, delete; if it does, ask user

This keeps user customization from disappearing on every script tweak.

---

## 4. The Shortcode System

### 4.1 Format

```
{project_prefix}-{type}-{seq}

project_prefix = first 4 hex chars of project UUID
type           = 2-3 letter code:
                 img | vid | aud | ref | chr | clo | itm | plc | mus | nar | sce | ch | bt
seq            = 4-digit zero-padded sequence number, per (project, type), starts at 1
```

**Examples:**
- `a3f9-img-0047` = 47th image in project `a3f9...`
- `a3f9-vid-0012` = 12th video
- `a3f9-ch-01`    = 1st chapter (2-digit since chapters are rarer)
- `a3f9-sce-005`  = 5th scene (3-digit since most projects have hundreds)

**Why this format?**
- 4-char project prefix lets you grep across all assets from a project anywhere on disk
- Type code is human-readable
- Sequence is stable forever — once assigned, never changes
- Lexicographically sortable within type
- Fits filename limits, copy-pasteable, screenshot-friendly

### 4.2 Allocation

Per-project counter table: `ShortcodeCounter(project_id, type, next_seq)`. Atomic increment on every new entity. Backfill migration assigns codes to existing rows in creation order.

### 4.3 Where shortcodes apply

| Entity | Has shortcode? | Use case |
|---|---|---|
| Asset | ✅ | Reference in prompts, filename, exports |
| Scene | ✅ | URL routing, log readability |
| Chapter | ✅ | URL routing, export filename |
| Job | ❌ | Internal — keep UUID |
| Project | Prefix only | The 4-char prefix IS the project's identity |

### 4.4 Search & navigation

- `GET /api/shortcode/{code}` → returns the entity (asset / scene / chapter)
- Frontend command bar (Ctrl-K) searches shortcodes
- Asset cards display shortcode in small font under the thumbnail

---

## 5. Backend Architecture

### 5.1 New modules

```
backend/services/chapters/
    parser.py        — script header parsing
    builder.py       — derive chapter tree from script + scenes
    auto_split.py    — break oversized chapters into sub-chapters
    resolver.py      — assign scene → chapter_id; resolve LLM batch boundaries
    shortcode.py     — allocate + look up shortcodes

backend/api/
    chapters.py      — REST endpoints (see 5.2)
    shortcodes.py    — single GET endpoint
```

### 5.2 REST endpoints

```
GET    /api/projects/{pid}/chapters
       → tree response: [{ chapter, sub_chapters: [...], scene_ids: [...] }, ...]

POST   /api/projects/{pid}/chapters/reparse
       → re-runs the chapter resolution pipeline; body: { force: bool }

PATCH  /api/projects/{pid}/chapters/{cid}
       → rename, set color, set tags, set custom name override

POST   /api/projects/{pid}/chapters/{cid}/split
       Body: { at_scene_id, new_name }
       → splits chapter into two siblings

POST   /api/projects/{pid}/chapters/{cid}/merge_with_next
       → merges with next sibling

POST   /api/projects/{pid}/chapters/{cid}/autogen
       Body: { mode, llm_provider, override_batch_size? }
       → runs Auto Gen scoped to this chapter only

GET    /api/shortcode/{code}
       → returns entity + redirect URL

GET    /api/projects/{pid}/chapters/{cid}/scenes
       → scenes in chapter, fully expanded

POST   /api/projects/{pid}/chapters/{cid}/preview-llm-batches
       → dry-run: returns how the LLM would batch (token estimate, batch count)
```

### 5.3 Auto-Gen + LLM batch routing

The current Auto Gen runs the whole project at once. After chapters land:

```
def run_autogen(project, scope: AutoGenScope):
    if scope.chapter_id:
        target_scenes = get_scenes_in_chapter_tree(scope.chapter_id)
    else:
        target_scenes = all_scenes(project)

    llm_provider = settings.default_llm_provider
    if llm_provider == "ollama":
        batch_limit = settings.llm_chapter_scene_limit_ollama
    else:
        batch_limit = settings.llm_chapter_scene_limit_cloud

    batches = split_into_batches(target_scenes, batch_limit)
    for batch in batches:
        call_llm(batch)  # one LLM round-trip per batch
```

Batches respect chapter boundaries (don't split a chapter across batches unless it's oversized — in which case it already became sub-chapters).

### 5.4 Export with chapter selection

`ExportRequest` gains:
```python
chapter_selection: Optional[ChapterSelection] = None

class ChapterSelection(BaseModel):
    mode: Literal["all", "single", "multiple"] = "all"
    chapter_ids: list[UUID] = []
```

In the export pipeline:
1. If `mode == "all"` → unchanged behavior
2. If `mode == "single"` or `"multiple"`:
   - Filter scenes to those in selected chapters (ordered by chapter order, then scene order)
   - Trim master audio to `[min(scene.start_time), max(scene.end_time)]`
   - Shift scene times so output starts at `0:00`
   - Trim backing tracks accordingly (or omit ones that don't overlap)
   - Output filename: `{project} - {chapter_short_code}.mp4` for single, `{project} - chapters_{ids}.mp4` for multiple
   - Persistent export cache keyed per chapter (so re-exporting one chapter is fast)

---

## 6. Frontend Architecture

### 6.1 Routing

```
/                                               HomePage
/projects                                       ProjectList
/projects/:pid                                  AppLayout (main timeline)
/projects/:pid/chapters/:cid                    AppLayout (chapter view)
/projects/:pid/assets                           AssetManager
/batches, /batches/:id                          (existing)
/series                                         (Phase 3)
/s/:shortcode                                   ShortcodeRedirect (universal)
```

The chapter view reuses **AppLayout** — same shell — but passes a `chapterFilter` context. Components that render scene lists check the filter.

### 6.2 New components

```
components/Chapters/
    ChapterOverlay.tsx          — colored bars row above timeline, click to drill
    ChapterTree.tsx             — sidebar tree (collapsed by default)
    ChapterBreadcrumb.tsx       — "Project > Chapter 1 > Sub-chapter 1.2"
    ChapterPicker.tsx           — used in Export modal for selection
    ChapterSettingsDrawer.tsx   — rename, color, tags, manual split
    ChapterNavMini.tsx          — prev/next-chapter buttons in chapter view
```

### 6.3 Timeline overlay layout

```
                 ┌─────────────────────────────────────────────────┐
chapter row      │ ▓▓▓▓ Chapter 1 ▓▓▓ │ ▓▓ Chapter 2 ▓ │ ▓▓ Ch 3 ▓▓ │
                 ├─────────────────────────────────────────────────┤
sub-chapter row  │ ░░ 1.1 ░░│░░ 1.2 ░░│ (none)         │ ░░ 3.1 ░░ │
                 ├─────────────────────────────────────────────────┤
section row      │ intro │ verse │ chorus │ verse │ chorus │ outro │  (existing)
                 ├─────────────────────────────────────────────────┤
waveform         │ ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~ │  (existing)
                 ├─────────────────────────────────────────────────┤
scenes           │ S1 │ S2 │ S3 │ S4 │ S5 │ S6 │ S7 │ S8 │ S9 │... │  (existing)
                 └─────────────────────────────────────────────────┘
```

Click chapter bar → navigate to `/projects/:pid/chapters/:cid`.
Click scene label → navigate to scene's leaf chapter view (per user request).

### 6.4 Chapter view behavior

The chapter view shows EXACTLY the same UI as the main project view — Timeline, SceneEditor, VideoPreview, ConceptPanel, GenerationPanel, BackingTrackTimeline — but **filtered to the chapter's scenes**.

- Timeline shows only the chapter's portion (relative time: chapter start = 0:00, or absolute time as toggle)
- Waveform shows the chapter's audio segment
- Scene scroll/select restricted to chapter scenes
- Playhead behaves the same — main stage VideoPreview shows the current scene's preview
- Auto Gen Modal pre-fills "this chapter" scope
- Breadcrumb at the top: `Project > Chapter 1`

When user clicks "back to project" or the breadcrumb root, playhead position is preserved.

### 6.5 Export modal — chapter selection UI

In the existing export modal "Re-export options" accordion, add a new field above it:

```
┌─ Scope ────────────────────────────────────────┐
│ ○ Entire video (default)                       │
│ ○ Single chapter         [Chapter 1 ▼]         │
│ ○ Multiple chapters      [+ Add chapter]       │
│                          [Chapter 1] [×]       │
│                          [Chapter 3] [×]       │
└────────────────────────────────────────────────┘
```

Selected chapters render in timeline-order regardless of selection order.

### 6.6 Settings page additions

A new **LLM Batching** section in Settings:

```
LLM Batching
  Max scenes per cloud LLM call    [25 ▼]   (1..100)
  Max scenes per Ollama call       [12 ▼]   (1..50)
  Chapter auto-split threshold     [25 ▼]   (5..200)
  Max chapter depth                [2  ▼]   (1..3)
```

Tooltips explain that Ollama typically has smaller context and the limits should be lower.

---

## 7. UX Flows — Walkthroughs

### 7.1 New project with `# headers` in script

1. User pastes script with `# Chapter 1`, `# Chapter 2` headers into Lyrics.
2. Backend strips headers from canonical text, runs Whisper on audio.
3. `text_align` reconciles Whisper words to canonical (the existing fix).
4. Chapter builder maps each `# header` to its word-index → time → start scene.
5. Chapters created; timeline shows colored bars.
6. User clicks Chapter 1 label → drilled into chapter view.
7. Hits "Auto Gen" → modal pre-targets this chapter; LLM batched into ≤25 scenes per call.

### 7.2 Existing project with 200 scenes, no headers

1. After migration, user opens project; sees one big "Auto Chapter" covering all scenes.
2. User opens Settings → confirms `chapter_auto_split_threshold = 25`.
3. User clicks "Re-parse chapters" → 200 scenes split into 8 auto-chapters of 25 each.
4. Each chapter named "Chapter 1", "Chapter 2", ... (user can rename via settings drawer).
5. User can now LLM-generate one chapter at a time.

### 7.3 Renaming a chapter

1. User clicks chapter bar → opens ChapterSettingsDrawer
2. Edits name "Chapter 1" → "The Awakening"
3. Save → `Chapter.name` updated, `Chapter.source` set to "manual" (so future script re-parse doesn't overwrite the custom name)

### 7.4 Re-exporting a chapter after editing

1. User in Chapter 1 view, regenerates Scene 5's image and Scene 8's video.
2. Opens Export modal.
3. Selects "Single chapter > Chapter 1".
4. Picks output settings.
5. Backend renders ONLY Chapter 1's scenes — output file: `MyProject - a3f9-ch-01.mp4`
6. Persistent cache for Chapter 1 is hit; only Scenes 5 and 8's clips re-render.

### 7.5 Multi-chapter cherry-pick export

1. User wants a "highlights" video of Chapters 2, 5, and 7.
2. Export modal → "Multiple chapters" → picks all three.
3. Backend concatenates Chapter 2, then 5, then 7's scenes in timeline order (within each).
4. Master audio is `concat(audio_ch2, audio_ch5, audio_ch7)` with respect to original chapter time ranges.
5. Output: `MyProject - chapters_2_5_7.mp4`.

---

## 8. Edge Cases & Conflicts

| Case | Resolution |
|---|---|
| Script has `# heading` but no audio yet | Chapters created with `start_time/end_time = null`; show as "pending audio analysis"; timeline overlay grey/striped. |
| Audio doesn't match script (low alignment ratio) | Existing `text_align` bail kicks in; chapters fall back to auto-split by scene count. Warning shown. |
| Chapter spans only partial scene (header lands mid-scene) | Boundary snaps to the **closest** scene boundary (start or end whichever is nearer). User can manually move via split UI. |
| Very deep nesting (`####`+) | Cap at depth 2; deeper headers downgrade to depth 2 with a warning. |
| Empty chapter (no scenes inside) | Hide from timeline overlay; still listed in tree as "Empty" with explanation. |
| User reorders scenes across chapter boundaries | Affected scenes' `chapter_id` recomputed; chapter `start_time/end_time` updated. |
| User deletes the only scene in a chapter | Chapter marked `is_empty=True`; user can delete or leave for future scenes. |
| Header named same as existing chapter on re-parse | Match by `(depth, normalized_name)` — preserves user customization. |
| Project has 1000+ scenes | All chapter operations are batched and async; UI shows progress. Frontend uses windowed rendering for the chapter list. |
| User switches LLM from cloud to Ollama mid-batch | New batch uses the new limit; in-flight batch keeps its original limit. |

---

## 9. Phasing — What Ships When

### Phase 0: Foundation (no user-visible change)

1. New `chapter` table + Scene.chapter_id + Asset.short_code + Asset.tags columns.
2. Migration script with backfill (one default chapter per project, shortcodes for all existing assets).
3. ShortcodeCounter table + allocation service.
4. AppSettings columns for LLM batching limits.
5. `GET /api/shortcode/{code}` endpoint.

**Ships:** backend-only, no UI. Tested in isolation.

### Phase 1: Chapter MVP

1. Script header parser.
2. Chapter builder + auto-split.
3. REST endpoints for chapters.
4. Frontend: ChapterOverlay on timeline, ChapterTree in left rail, ChapterBreadcrumb.
5. Chapter drill-down route + filtered view.
6. Export modal: single-chapter selection.
7. Settings: LLM batch limit fields.
8. Auto-Gen scope respects active chapter context.

**Ships:** Users can split projects into chapters, drill in, auto-gen by chapter, export by chapter.

### Phase 2: Sub-chapters + multi-chapter export + tags

1. Sub-chapters (parent_chapter_id) — UI nesting in tree + overlay.
2. Multi-chapter export.
3. Tags on Asset and Chapter — searchable in Asset Manager and command bar.
4. Manual chapter split/merge UI.
5. Command bar (Ctrl-K) with shortcode/tag/name search.
6. Per-chapter export cache.

### Phase 3: Series

1. New `series` table.
2. `Project.series_id` FK.
3. `/series` index page.
4. Cross-project asset picker — show character assets from any project in the same series.
5. Series-level export (concatenate episodes).

---

## 10. Open Questions for You

Mark answers inline; we'll iterate the blueprint.

| # | Question | Default if no answer |
|---|---|---|
| Q1 | **Header syntax** — stick with Markdown `#`, or allow `[Chapter 1]` or `=== Chapter 1 ===` too? | Markdown `#` only. |
| Q2 | **Default cloud batch limit** — 25 scenes feels right for GPT-4o/Sonnet; agree? | 25 |
| Q3 | **Default Ollama batch limit** — 12 to be safe for Llama 8B context? Higher for 70B? | 12 (we can autotune later) |
| Q4 | **Depth cap** — 3 levels (chapter → sub → sub-sub) or stop at 2? | 3 (depth 0..2) |
| Q5 | **Chapter view URL** — `/projects/:pid/chapters/:cid` (UUID) vs `/projects/:pid/c/:short_code` (shortcode like `a3f9-ch-01`)? | Use shortcode — prettier, copy-pasteable. |
| Q6 | **Manual chapter creation** — should users be able to create chapters WITHOUT script headers (drag handles in timeline)? | Yes, in Phase 2. |
| Q7 | **Reorder chapters** — should reordering a chapter physically reorder its scenes in the project, or refuse? | Reorder scenes too. |
| Q8 | **Multi-chapter export** — render as one continuous MP4 or output multiple files (one per chapter)? | Single MP4 (single-file is simpler; multiple-file is Phase 2). |
| Q9 | **Tags** — free-text or constrained vocabulary? | Free-text with autocomplete from existing tags. |
| Q10 | **Series** — Phase 3 is "later" — weeks or months? | Decide after Phase 1/2 ship. |
| Q11 | **Existing asset backfill** — shortcode order = creation order (created_at ASC)? Or by file modified time? | created_at ASC. |
| Q12 | **Chapter color palette** — fixed cycle (purple, blue, green, amber, pink, ...) or auto-generated from name hash? | Fixed cycle of 8, deterministic by chapter index. |

---

## 11. What Will NOT Be Included

To keep scope honest:

- ❌ Chapter-level audio tracks (each chapter has its own music). The project's audio is one continuous file; chapters just slice into it.
- ❌ Per-chapter resolution / fps overrides. One export config per project.
- ❌ Cross-chapter scene linking (Scene 5 of Chapter 1 also appears in Chapter 3). Scenes belong to exactly one chapter.
- ❌ Branching narratives (chapters with multiple downstream chapters). Linear order only.

---

## 12. Risk Register

| Risk | Mitigation |
|---|---|
| Migration backfill fails on a large project, leaving inconsistent state | Migration is wrapped in a transaction; failure rolls back. Idempotent re-run safe. |
| User edits chapter name, then edits script, re-parse wipes name | Chapter.source="manual" flag prevents auto-overwrite. |
| LLM batch limit too small → too many round trips → slow | Per-batch progress shown; user can raise the limit. |
| Chapter view shows wrong scenes because filter is stale | React Query invalidates chapter scene list on every relevant mutation (already-established cache coherence pattern). |
| Shortcode collision between projects | Project prefix (first 4 hex of UUID) — collision risk = 1/65536 across all projects, acceptable. We can detect and re-hash if hit. |
| Multi-chapter export audio splicing artifacts at boundaries | Apply 50ms crossfade at chapter boundaries; document in README. |

---

## 13. Acceptance Criteria

The feature is "done" when:

1. A project with `# headers` in the script automatically gets chapter overlays on its timeline within 2 seconds of script save.
2. A project with 100 scenes and no headers gets auto-chaptered into 4 chapters of 25 scenes after one click.
3. Clicking any scene label opens the chapter view scoped to that scene's parent chapter.
4. Playing the chapter view plays only the chapter's audio + visuals in the main stage, with subtitles scoped to chapter words.
5. Auto-Gen from chapter view runs LLM calls in batches of ≤ the configured limit.
6. Exporting a single chapter produces a valid MP4 of just that chapter's content, with filename including the chapter shortcode.
7. Exporting multiple chapters produces a single MP4 of their concatenated content.
8. Every Asset has a unique shortcode; searching `a3f9-img-0047` in the command bar opens it.
9. Renaming a chapter, then editing the script, preserves the rename.
10. All existing music-mode projects continue working with one default "Chapter 1" covering everything.

---

## 14. Implementation Order (once blueprint is approved)

```
Day 1-2   Foundation: tables, columns, migration, shortcode service.
Day 3-4   Script parser + chapter builder + auto-split logic.
Day 5     REST endpoints for chapters.
Day 6-7   Frontend ChapterOverlay + ChapterTree + drill-down route.
Day 8     Chapter view filtering pass-through.
Day 9     Export chapter selection (single).
Day 10    Settings UI + Auto-Gen chapter scope.
Day 11    Multi-chapter export.
Day 12    Tags + command bar (Phase 2 starts).
Day 13    Manual split/merge UI.
Day 14    Polish + acceptance tests + docs + CHANGELOG entry.
```

(Phase 3 — Series — separate planning doc later.)

---

## 15. What I Need From You Before Coding

1. Answer (or "ok defaults") the questions in Section 10.
2. Confirm the data model in Section 2 — anything to add/remove/rename?
3. Approve the URL routing in Section 6.1.
4. Confirm shortcode format in Section 4.1 — `a3f9-img-0047`, comfortable?
5. Pick whether **Phase 0 alone** can ship first (foundation, no UI), or whether we should bundle Phases 0+1 into one release.

Reply with answers and I'll start with Phase 0 and check in for review before Phase 1.
