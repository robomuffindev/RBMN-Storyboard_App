# 🌍 The Story / World Builder — reference

**Status:** CURRENT as of **v1.277.50** (2026-08-19).
**Code:** `backend/api/storyworld.py` · `backend/api/storychapters.py` ·
`backend/api/storycodex.py` · `backend/services/story_context.py` ·
`backend/services/chapters/from_story.py` · `frontend/src/components/StoryWorld/`
**Runbook:** `docs/OPERATIONS.md` §7 has the route list. This doc is the *shape* — what the
things are, how they nest, and which rules are load-bearing.

---

## ⚠⚠ READ THIS FIRST: FOUR things are called "chapter"

This is the single most expensive confusion in the codebase. Before you touch anything with
"chapter" in the name, work out which of these you are holding:

| # | Name | Lives in | What it is |
|---|------|----------|------------|
| 1 | **`Chapter` rows** | SQL, `backend/services/chapters/` | A **project's timeline segments**, timed against the audio's detected sections. This is what the Chapters tab inside a project shows. |
| 2 | **Story `arcs`** | `story["arcs"]`, world JSON | The **spine** of a story. Short on purpose. No durations — time comes from the audio. |
| 3 | **Story `chapters`** | `story["chapters"]`, world JSON | ⭐ **NEW in v1.277.46.** One arc **told at length**, with its own full narration and recording. **A story chapter IS one video project.** |
| 4 | **A chapter's `beats`** | `chapter["beats"]` | Slices of a story chapter. These become (1) when a project pulls. |

`docs/TIMELINE_EDITING.md`, `docs/CLI_TOOLS.md`, the README's *"Narration Chapters"* section
and `BLUEPRINT_CHAPTERS_v1.md` all say "chapters" meaning **(1)**.

---

## The ladder

```
WORLD                       the setting sheet, cast, locations, texts, 🎨 style, 📚 codex
 └── STORY                  prose (8 fields) + story_type
      ├── ARCS              the spine: title · summary · mood · characters · locations
      └── CHAPTERS          ⭐ one arc told at length  =  ONE VIDEO PROJECT
           ├── narration    the full spoken script for this chapter
           ├── recording    its own audio · aaf · srt
           └── BEATS        → the project's timeline Chapter rows
```

**Many chapters per arc** (his call, 2026-08-18). Nothing enforces a count: a long, eventful
arc can be told over three chapters and a short one over one. Arcs stay the spine; **chapters
are the unit you render.**

⭐ **A BEAT IS ARC-SHAPED ON PURPOSE.** `storyworld._clean_arcs()` normalises both to
`{id, i, title, summary, mood, characters, locations}`, which is why
`create_chapters_from_arcs(session, pid, chapter["beats"])` needed no new code and
`story_context.arc_context()` keeps matching on `arc_id`. **Do not let a beat grow a field an
arc does not have** without adding it to `_ARC_FIELDS` too.

---

## A world

`_libraries/storyworld/worlds/<wid>.json`, one file per world, read-modify-write under a
module `threading.Lock`. Field vocabulary is **server-driven** (`GET /api/storyworld/meta`),
so adding a field is a one-line backend change.

```
{ id, name, world:{13 fields}, stories:[…], cast:[…], locations:[…], texts:[…],
  style:{preset, custom_text, ref_id, ref_description}, codex:{…},
  project_ids:[…], llm:{provider, model} }
```

**Every mutation MERGES.** An absent key means *leave it*, never *clear it* — klein3's
`/fields` REPLACE bite is designed out here.

**Tabs on `/worlds`** (`StoryWorldPage.tsx`):
🌍 World · 📖 Stories · 🎭 Cast · 📍 Locations · **📚 Codex** · 📝 Texts · 🔗 Projects.

---

## 📖 Stories, arcs and chapters

### Arcs
`POST …/stories/{sid}/structure` writes them from the prose (max 24). Big Bang structures
every story it creates. ⚠ Big Bang runs BEFORE the cast exists, so its arcs come back with
empty `characters` — re-Structure once the cast is there. Arcs are editable on the Stories tab
(reorder / add / delete). ⚠ An arcs-only save posts `{arcs}` alone, which is why
`StoryIn.story_type` is `Optional` — defaulting it silently converted narration stories.

### Chapters — `backend/api/storychapters.py`

```
GET  …/stories/{sid}/chapters            full (carries the prose)
GET  …/stories/{sid}/chapters?brief=1    ⭐ the PICKER shape — titles + counts only
POST …/stories/{sid}/chapters            create by hand
POST …/stories/{sid}/chapters/generate   ✨ Outline — titles + summaries ONLY
POST …/stories/{sid}/chapters/reorder
POST …/stories/{sid}/chapters/{cid}/narration        ✍ START the write (a JOB)
GET  …/stories/{sid}/chapters/{cid}/narration/job    live status
POST …/stories/{sid}/chapters/{cid}/narration/cancel
POST …/stories/{sid}/chapters/{cid}/beats       split an existing narration
GET  …/stories/{sid}/chapters/{cid}/tts/options      🎙 voices · engines · readiness
POST …/stories/{sid}/chapters/{cid}/tts              🎙 render a take (audition)
POST …/stories/{sid}/chapters/{cid}/tts/keep         ✅ keep it → audio + srt slots
GET  …/stories/{sid}/chapters/{cid}/project-readiness   🎬 can it become a project
POST …/stories/{sid}/chapters/{cid}/create-project      🎬 make one
POST …/stories/{sid}/chapters/{cid}/delete
POST|GET|POST …/chapters/{cid}/file/{slot}[/delete]   audio · aaf · srt
POST …/stories/{sid}/chapters/{cid}      ⚠ the parameterised update — DECLARED LAST
```

⭐ **The LLM writes ONE CHAPTER AT A TIME** (his call). ✨ Outline is the only route that sees
the story whole and it writes **no narration** — smaller context, better prose, and you can
edit between chapters instead of after a whole book.

### ✍ The narration is written BEAT BY BEAT (v1.277.47)

Length is a **WORD BUDGET** (`minutes × 150`), never a duration request — models honour a word
count and ignore *"about ten minutes"*. Default **10 minutes ≈ 1500 words**; the chapter's own
`target_minutes` overrides it.

⭐⭐ **ONE MODEL CALL PER BEAT, and that is the point.** Asking a model for 1500 words in one
response gets ~400: it paces itself against its own sense of *"an answer"*, not against the
budget. Raising the number alone does not work — the SHAPE has to change. Each call gets its
share of the budget plus **the tail of the previous beat**, so the prose continues instead of
restarting (the failure mode of every multi-call narrative: the same character introduced three
times and the weather changing). This is the story lane's arc-by-arc lesson, one rung down.

Because it is N calls and takes minutes it is a **JOB**: `POST …/narration` starts it,
`GET …/narration/job` polls (stage · which beat · running word count · WHERE · elapsed · log),
`POST …/narration/cancel` stops it after the current beat. The panel **adopts a run already in
progress** after a reload.

⚠⚠ **`_beat_groups` returns a PARTITION, and it has to.** The first version gave the first N
beats a budget and **zeroed the rest** — a 24-beat chapter at 1500 words narrated beats 1-12
and **silently never told 13-24**, with a green job and a plausible word count. When the budget
is thin relative to the beat count, consecutive beats are **GROUPED** (fewer, fatter calls
instead of a string of 60-word stubs); every beat lands in exactly one group.

⚠ Beats are **input, not output**, when they already exist: writing a narration reuses them and
must never replace structure a project has already pulled and timed against. `…/narration` only
derives beats when the chapter has none.

### 🎙 Speaking a chapter (v1.277.48)

    GET  …/chapters/{cid}/tts/options     voices · engines · readiness · word count
    POST …/chapters/{cid}/tts             render a take → an Audio-Lab job id
    POST …/chapters/{cid}/tts/keep        keep it → writes the audio AND srt slots

All of the machinery — the voice library, F5 vs Kokoro, pause tagging, pacing, the render
queue — is `audio_lab.py`'s, called **in-process**. What lives on the chapter is the opinion:
which text, and where the take belongs.

⭐ **Audition first, keep second.** A render lands in the Audio Lab's job board and is played in
the chapter panel; **nothing touches the chapter until ✅ Keep**. That gap is the feature — a
take that overwrites the chapter the moment it finishes cannot be compared with the one before.

⭐⭐ **Keeping writes the SRT too**, from the render's own cues. The chapter also gains `cues`,
so a project can build scenes from the exact numbers without re-reading the subtitle file.

⚠ **>1.0 pace = SLOWER on both engines.** F5's node is inverted (measured: 0.8 → 4.86 s ·
1.2 → 7.28 s) and Kokoro's is inverted *in code* to match, so the one label means one thing.
`stretch` (default) renders at native speed then time-stretches with pitch preserved — asking a
vocoder to fill a longer duration is what made slow takes sound bad.

### 🔒 THE BOUNDARY GUARANTEE — read this before touching the cue path

**The precision does not come from the timing maths. It comes from file topology and from a
rule.** Both halves are required; v1.277.48 shipped the first and forgot the second.

**1. Topology — why a cut can never land inside a word.** Each sentence is rendered as its own
file (`audio_lab.py`'s chunk loop). Every part is normalised to identical PCM (`_CANON`) before
the join, so ffmpeg's concat demuxer is sample-exact, and the silence between them is generated
at `-t {ms/1000:.3f}`, which at 24 kHz is always a whole number of samples. A cue's
`[start, end]` is the interval that file occupies. **There is no alignment step, no transcript,
no estimator** — a boundary cannot fall inside a word because the word is in a different file.
An AAF has the same property for the same reason (a clip start is a clip start); Whisper and
SRT do not, which is the whole story of v1.8.14 → v1.8.22.

**2. The rule — `timeline.authoritative_timeline()`.** ⭐⭐ **The AAF's real advantage in
production was never mostly its arithmetic; it was that importing one made the timeline
UNTOUCHABLE.** Three gates — Whisper/SRT resync, scenes-from-sections, Suggest Timeline —
refuse while a timeline is authoritative. Both sources qualify:

    audio_source == "aaf"           an AAF clip start   (integer edit units)
    scene_source == "chapter_cues"  a TTS sentence edge (integer samples)

⚠⚠ **ONE predicate, every gate.** The three sites each used to inline
`settings.get("audio_source") == "aaf"` — which is exactly how a second authoritative source
ends up enforced in two places out of three. `/detach-aaf` releases either; the Audio tab shows
a 🔒 banner so a refusal reads as the guarantee working, not as a broken button.
⚠ It deliberately does **not** gate manual edits (`PUT /scenes/{id}`, split, delete-merge).

**3. Integer samples, never accumulated seconds.** `_probe_seconds` rounds to 2 dp; summing 80
of those is a **random walk** whose error grows with position — ~30 ms typical, 400 ms worst.
That is the v1.8.20 bug (*"39 of 48 scenes ended mid-word … growing to ~10s by the end"*), and
v1.277.48 had re-introduced it. `_pcm_frames` reads integer frame counts from the WAV headers
and divides **once**, the same discipline `import_aaf` uses with edit units.
⚠ Cues are scaled by the tempo **actually applied**, not the requested `pace` (the filter
string is 4 dp and `atempo` clamps); `pace` is validated to [0.5, 2.0] for the same reason.
⚠ The sanity check tests monotonicity across **every** cue — a random walk is most likely to be
back near zero at the end, which is precisely where a last-cue-only check looks.

**Measured, 6 minutes / 70 cues, decoded from the audio** (`scripts/cue_precision_verify.py`):
last cue vs end of file **0.00 ms** · every start a whole sample (**worst fraction 0.000000**) ·
**energy in the gaps vs speech: max 0.000** — 0 of 69 gaps contain speech · drift first third →
last third **0.0000 → 0.0000 ms** · gap lengths 0 of 69 off · SRT vs cues **0.00 ms** · AAF
round-trip **0.0 s** with 70/70 names kept.

⚠ **What is NOT covered:** an uploaded human recording. It has no segment boundaries to
inherit, so it still needs an AAF or a hand-checked SRT — the guarantee is about narration
*this app rendered*.

### 📝 The SRT is free — and the AAF writer that goes with it

The TTS renders **sentence by sentence** and joins the parts with measured silence, so the
offsets already existed; the join's own verification line was probing every part's duration and
throwing the running total away. `_concat_with_pauses` fills a `spans` out-parameter, the job
carries `cues` (`start`·`end`·spoken text), and `GET /api/audio-lab/jobs/{jid}/srt` writes it.

⚠ **Two traps:** the **single-chunk fast path** bypasses the normalise loop (a one-paragraph
narration would get an empty cue list), and **`_stretch` runs after the concat**, so cues are
scaled by `pace` at capture time — one clock, not two.

🎬 **THERE IS AN AAF WRITER** — `backend/services/export_aaf.py` (v1.277.49). Timeline-only,
one Sound clip per sentence, **`edit_rate` = the audio SAMPLE RATE** so every cut point is an
exact sample; a 25/30 fps edit rate would quantise every boundary and re-introduce the ±20 ms
error this lane exists to avoid. A kept take writes **audio · srt · aaf**, so the set is
complete and the file opens in an NLE.

⚠ Traps paid for: a `SourceMob` will not serialise without an `EssenceDescription` — use a
**`MasterMob`** per clip (which is also the real ElevenLabs topology); gaps must be `Filler`
components or everything after the first pause shifts.
⭐ **Every export is read back through our OWN `parse_aaf_clips` before it is kept.** If the
round-trip does not recover every boundary the file is deleted rather than handed over looking
authoritative. *"It wrote a file"* is not evidence.

⚠ **Inside the app the scenes are still built from the cues directly**, not from the AAF:
`clips_to_scenes` cuts on clip STARTS and discards the ENDS, so a round-trip would throw away
information we already have. The AAF is for interchange — and because it is the artefact he
trusts, which is a legitimate reason on its own.

### 🎬 Chapter → project (v1.277.48)

    GET  …/chapters/{cid}/project-readiness    can it, and if not exactly why
    POST …/chapters/{cid}/create-project       mode · engine · scenes_from_cues · merge floor

⚠⚠ **✍ WRITING TEXT RENDERS NOTHING.** `…/narration` writes words; `…/tts` renders audio; only
**✅ Keep** produces files — and it produces **all three at once**: audio + SRT + **AAF**. So
"regenerate the narration" does NOT give you a new AAF; render a take and keep it.

⭐⭐ **AND A TAKE KNOWS WHICH WORDS IT SPOKE.** Keep stamps the audio with `spoke_words` + a hash
of the narration. Rewrite the chapter afterwards and `project-readiness` **blocks** with both
counts — otherwise text + audio + SRT would all be present, the gate would pass, and the scenes
would be cut to sentences no longer in the script, named with words nobody will hear. ⚠ Takes
kept before v1.277.50 carry no stamp and WARN rather than block.

⭐⭐ **THE GATE IS narration text + audio + SRT, ALL THREE** (his call — the strict option).
Missing beats or an untagged cast are **warnings**, not blockers: they make a worse project,
not an impossible one. `force` exists on the API and is deliberately not a button — a project
created half-set-up looks finished and is not, and the failure surfaces hours later as scenes
that do not match the words.

⭐ **The AAF is copied into the project** (`story_aaf_asset_id`) and is there for your NLE —
but the scenes are built from the **cues**, not by re-importing it. Same numbers (the AAF is
written FROM those cues and round-trips at 0.0 s), and the cue path additionally keeps the clip
ENDS, which `clips_to_scenes` discards. ⚠ You do **not** need to press Import AAF afterwards;
doing so would rebuild the same boundaries and REPLACE the scene list.

What it does, in order: create (mode: narration video · images · talkie · music video) → link
to world→story→**chapter** → pull concept, style, cast, script, the narration files and the
beats as timeline chapters → **build scenes from the cues** → re-time those chapters onto the
new scenes → slice per-scene audio. Records `settings["scene_source"] = "chapter_cues"` —
⚠ **not** `audio_source = "aaf"`, which would switch on the AAF resync gates and the
"superseded" UI for a file that does not exist.

### ⚠⚠ PARAGRAPHS — never use `sw._flat()` on prose

*"Paragraphs matter in TTS"* (his words). They are where a reader breathes and where this app's
pause-tagger writes its `[pause]` tags, so a single block is not merely ugly — it is a narration
with no breaths in it.

**`sw._flat()` joins a list with `", "`.** A model answering
`"narration": ["First paragraph…", "Second paragraph…"]` therefore came back **comma-welded
into one block**. `_flat` is right for FIELDS (a mood, a title, a one-line summary) and
destructive on PROSE.

- **`_prose(v)`** — joins a list/dict of paragraphs with a **blank line**. Use this.
- **`_paragraphize(t)`** — then GUARANTEES the result: drops any heading the model sneaked in,
  promotes single newlines to paragraph breaks, and splits a long blob on sentence ends into
  groups of four. ⭐ The prompt asks for paragraphs twice and the code enforces them anyway —
  a requirement is not a request.

⚠ **`est_minutes` is ARITHMETIC** (words ÷ 150). **`recorded_seconds` is a MEASUREMENT**
(ffprobe, at upload). They are named differently on purpose.

⚠ Rewriting a narration would mint NEW beat ids and dangle every pulled project's `arc_id`;
`_keep_beat_ids()` carries them over positionally.

⚠ **Route order is load-bearing.** `/chapters/generate` and `/chapters/{cid}` are the same
SHAPE; the literals are declared first or they arrive as `cid="generate"`.

### Narration: TWO levels, on purpose

| | Where | What it is |
|---|---|---|
| **Story narration** | a world TEXT of kind `narration`, `## Arc` headers | the whole-story / trailer version |
| **Chapter narration** | `chapter["narration"]` | ⭐ the real per-video script |

His call (2026-08-18): **keep both.** A chapter-scoped pull prefers the chapter's;
`lyrics_text_id` overrides either. `storyworld.spoken_only()` strips `## ` headers before
anything a reader or Whisper sees.

### Recordings
Story files → `_libraries/storyworld/narration_audio/{wid}/`.
Chapter files → `_libraries/storyworld/chapter_audio/{wid}/`.
Three slots each (`audio` · `aaf` · `srt`); a second upload replaces and deletes the old file.
`delete_story` and `delete_chapter` both clean up — collect the metadata **inside** the lock,
unlink **after** the write lands.

---

## 🔗 The project link — a chapter is one video

`PUT /api/projects/{id}/story-link {world_id, story_id, chapter_id, attach}` → two-way
(`project.settings` **and** `world["project_ids"]`).

    settings["world_id"]      the world
    settings["story_id"]      the story        (optional)
    settings["chapter_id"]    ⭐ the chapter   (optional; requires story_id)

**Selecting a chapter narrows EVERYTHING:**

| | story-wide | chapter-scoped |
|---|---|---|
| script | the story's narration | **the chapter's** narration |
| audio/aaf/srt | the story's files | **the chapter's** files (falls back to the story's) |
| timeline chapters | the story's **arcs** | ⭐ **the chapter's BEATS** |
| cast pull | story-scoped | narrowed to the chapter's named characters |
| `settings["song_title"]` | the story title | **the chapter title** (or every video in the series gets one name) |

⚠ That last row is **`settings["song_title"]`, not `Project.name`** — nothing here renames the
project row. `story_context.effective()` prefers the chapter title whenever the project is
linked; `pull_from_story` writes it only when `concept=true` (which defaults to **off**) and
only if `song_title` is not already set.

⚠ **Changing the story clears the chapter** — a `chapter_id` belonging to the old story would
resolve to nothing. ⚠ A `chapter_id` that no longer resolves is **reported** by
`get_story_link` (`chapter_missing`), by `pull_from_story` and by `story_context.resolve` —
silently widening to the whole story is a 40-minute video where a 4-minute one was asked for.

⭐ **The scope comes from the LINK, not from a pull flag.** There is deliberately no
`chapter: bool` on `PullFromStoryIn`: a project that is a chapter's video is that all the way
down, and a per-part override would let it be half one thing and half another.

### Derived, not copied
`backend/services/story_context.py` — `resolve(settings)` returns

    linked · world · story · chapter · chapter_missing · arcs · arcs_are_beats
    concept_text · style_text · characters · overrides

⚠⚠ **`arcs` holds the CHAPTER'S BEATS when a chapter is selected** — everything downstream
reads `arcs` and needed no change, which is exactly why `arcs_are_beats` travels beside it. A
reader that does not know which rung it is holding will label the wrong one.
`settings["story_overrides"]` pins a single field. Editing the world updates the project
(his call over copy-on-link).

### ⚠⚠ `source` IS MUTABLE. PROVENANCE IS NOT.

`backend/api/chapters.py` sets a project chapter's `source = "manual"` the moment it is
renamed, split, merged or re-described (`:214 / :306 / :353 / :597`). So **"is this a story
chapter?" cannot be asked as `source == "story"`.**

`chapter_metadata["from_story"]` is written once by `create_chapters_from_arcs` and never
changes. `from_story.is_from_story(ch)` is the one predicate; three places ask it:

- the pull's DELETE — else a re-pull skips every chapter he edited and builds duplicates
  beside them (the 1.8.15 **doubled-chapter** signature, from a new direction)
- `_rebuild_chapters_locked`'s producer short-circuit — else renaming one chapter re-enables
  the auto producer and grows a second competing set
- `retime_story_chapters` — else his edited chapter keeps the times it was born with

⚠ Separately, `source='story'` **and `'manual'`** are both PRESERVED sources in
`_rebuild_chapters_locked`'s five delete predicates, so an edited story chapter still survives
a rebuild. Preservation and provenance are different questions.

---

## 📚 The codex — `backend/api/storycodex.py`

The world's cheat sheet and every character's history, for building a **continuing series**.

    codex["entries"]        faction · rule · place · item · term · event · concept
    codex["characters"]     per cast member: summary · STATE LINE · events · relationships
    codex["hashes"]         the canon digests that make a recalc incremental
    codex["runs"]           the last 12 recalcs, as benchmarking data

Both are injected into every generator via `story_context.resolve()` →
`codex_brief()` + `character_brief()`, as *"established canon — treat as already true, never
contradict it"*. The **state line** is the payoff: *where this character stands now, what a
sequel starts from.*

### The four rules

1. ⭐ **CANON ONLY** (his call). Every entry derives from something WRITTEN — a world field, a
   story, a chapter's narration, a cast sheet, a location — and carries `sources` naming it.
   Sourceless entries are dropped at the merge. *A codex that invents lore will eventually
   contradict a story you write later, and you will have no way to tell which half was real.*
2. ⭐⭐ **A RECALC NEVER EATS WHAT YOU WROTE.** `manual` (you wrote it) and `pinned` (the model
   wrote it, you kept it) both survive, through **one** predicate: `_keep()`. Every path a
   RECALC can take goes through it, and the 400-entry cap truncates the **generated** slice
   only. (Your own `POST …/codex/entry/{eid}/delete` deletes by id and asks nothing — a
   deliberate delete is not a recalc.)
3. ⭐ **INCREMENTAL.** A canon hash per story and per character; unchanged material is skipped
   with **no LLM call** and says so. `stale` comes back from a plain `GET /codex`, so the 🔴
   *"N things changed"* badge costs nothing.
4. ⭐ **LIVE VERBOSE STATUS** (the standing rule): stage · what · **WHERE** (provider/model/
   host) · elapsed · per-stage durations · a change log — and the run recorded.

**Ollama by default** (his preference — this is the most token-hungry lane in the app).
`CodexRecalcIn.llm` overrides per run; the world's `llm` pick wins over the default.

### ⚠⚠⚠ SCOPE MUST BE STATED, NEVER INFERRED FROM AN EMPTY LIST

`_merge_entries` originally read *"no stories in scope"* as *"everything is in scope"*. A
recalc where only a CHARACTER changed therefore **deleted the entire generated codex**, and a
story-scoped run (which is exactly what the Story tab's 📚 button sends) deleted every
world-level entry — permanently, because the world hash was still stored. Silent, with ✅ *"up
to date"* showing afterwards. `did_world` is an explicit parameter now, and `story_ids == []`
means world-level.

### Other traps already paid for

- **An entry named "Unknown" poisoned a world file past repair.** `_flat` maps
  `unknown / none / n/a / nothing / -` to `""`, so `_entry()` returned `None` *after* the
  route's own name check passed, `None` was appended and saved, and every later read raised.
- **Double-submit**: the route checked the job map and then `await`ed `_llm_cfg`. Claim under
  the guard, revert on failure.
- **A scan-time hash stored after a minutes-long LLM call** marks an edit made *during* the
  run as already-read. Re-hash under the merge lock; anything that moved stays stale and is
  reported as `drifted`.
- **`GET /codex` must stay cheap** — it runs on every tab load. `_char_hash()` composes from
  pre-computed story digests instead of re-serialising every chapter's narration per cast
  member.

⚙ **The LLM config is resolved in the ROUTE and the tuple handed to a plain thread.**
`_llm_cfg` needs the async session; `concept._call_llm` is blocking. A session inside the
thread is the self-HTTP-from-async deadlock class wearing a different hat.

---

## 🎭 Cast · 📍 Locations · 📝 Texts · 🎨 Style

- **Cast** — klein3's 11 appearance keys + 8 lore fields + outfits, tagged to stories via
  `story_ids`. `story_cast()` falls back to the whole cast when NOBODY is tagged (a one-story
  world never needs tagging). `cast/submit` builds AutogenSpecs and calls autogen's `_enqueue`
  **in-process**. See `docs/KLEIN3.md`.
- **Locations** — six text fields + rendered plates + an auto-composited 2048px sheet.
  ⚠⚠ *"no people"* PUTS PEOPLE IN EVERY PLATE — cfg-1 lanes have no negative prompt, so name
  what IS there: *"a deserted, unoccupied place… a clean environment plate"*.
- **Texts** — lyrics / narration / script / poem / notes, optionally linked to a story.
- **Style** — preset + custom text + a style REFERENCE image (vision describes the **style**,
  never the content). ⭐ A style reference and a subject reference are NOT interchangeable.
  Injected into every LLM context for that world.

---

## Free verification — no renders, no LLM, no GPU

    python scripts/storyworld_smoke.py         44 checks — worlds, cast, submit, projects
    python scripts/story_chapters_smoke.py     89 free  — chapters, codex, chapter link,
                                                          paragraphs, the beat call plan
    python scripts/story_chapters_smoke.py --live   +7  — writes ONE real narration and
                                                          MEASURES what reached disk
    python scripts/story_audio_smoke.py         9 checks — the story recording slots
    python scripts/cue_precision_verify.py     20 checks — ⭐⭐ THE BOUNDARY GUARANTEE,
                                                          decoded from the AUDIO itself
    python scripts/chapter_voice_probe.py     34 checks — ⭐ THE WHOLE CHAIN, live:
                                                          chapter → spoken take → cues →
                                                          SRT → project with scenes

`story_chapters_smoke.py` loads `_merge_entries`, `_prose`, `_paragraphize` and `_beat_groups`
**by path** (operator scripts are stdlib-only) and asserts their invariants directly.

⭐ **`--live` exists because a green unit test on the helpers is not evidence about the
artifact.** The free half proves `_paragraphize` *can* split a blob; the live half proves what
the model actually wrote to disk has paragraphs in it and hits the budget.
Measured on v1.277.47: **717 words against a 600 target, 13 paragraphs, 27 s, 2 calls.**

`chapter_voice_probe.py` is the same principle for the voice lane, on Kokoro (app host, no GPU,
free). Measured on v1.277.48: **26.48 s of speech · 6 cues · last cue 26.47 s vs a 26.48 s file
(0.01 s drift) · gaps 0.35/0.7/0.35/0.7/0.35 s exactly as asked · 4 scenes ending at 26.47 s,
each named with the words spoken in it.**

⭐ **Count the assertions in the SOURCE, not the PASS lines in the output** — a grep that
matches its own `ALL PASS` footer inflates by exactly one, silently, every time.

---

## Standing rules for anyone editing this lane

1. **Parameterised routes are declared LAST.** `/cast/{cid}` swallowed `/cast/submit` and
   `/cast/generate` on this module's first smoke run — seven failures, one cause.
2. **Every mutation MERGES.** Absent ≠ empty.
3. **Read → (slow LLM) → RE-READ under the lock → write.** Never write back the snapshot you
   took before the model call, and re-assert any guard you checked against it.
4. **Never hold `_LOCK` across an `await`**, a subprocess or a network call. `_save` retries
   under it, so its worst case is a whole-server stall.
5. **Collect file metadata inside the lock; unlink after the write lands.**
6. **`dict(project.settings)` is SHALLOW.** Deep-copy before mutating a nested dict, and
   RE-READ the row to verify rather than trusting the response.
7. **A green route is not a green FEATURE.** `story_context.resolve` resolved the chapter
   perfectly and `/story-context` simply did not forward it.
