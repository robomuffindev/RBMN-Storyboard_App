# LLM Agent Instructions — Robomuffin Idea Factory project text data

## Mode: `music_video`

You are editing the text data of a **Music Video** project — a video built from a music audio track with per-scene image and video clips generated to illustrate the song's lyrics and emotional arc.

You will receive a JSON payload that follows the schema described below. **Do not invent new top-level keys.** Edit only the field values; preserve every existing key and structure exactly. Return the same JSON object back, edited.

---

## Output rules

1. Return valid JSON (no markdown fences in the output, no trailing prose).
2. Preserve `rbmn_format_version`, `project_mode`, and `project_name` exactly. Do not change them.
3. Preserve `order_index` on scenes and `order` on chapters — these are identifiers. Do not reorder, renumber, add, or remove scenes or chapters. Timing is fixed by the audio.
4. You may freely edit text content in `concept`, `chapters[].description`, `chapters[].character_focus`, `chapters[].style_notes`, every text field on every scene, and `lyrics_source.initial_text`.
5. Reference characters by their `name` string only. Names match case-insensitively. You may add new characters; you may not delete them.
6. Reference chapters from scenes by `chapter_order` (the chapter's integer `order`).
7. **Do not** include UUIDs, file paths, or asset IDs.

---

## Schema reference

### Top level

| Field | Type | Editable | Notes |
|---|---|---|---|
| `rbmn_format_version` | `"1.0"` | ❌ | Match exactly. |
| `project_mode` | `"music_video"` | ❌ | Match exactly. |
| `project_name` | string | ❌ | Display label. |
| `concept` | object | ✅ | See below. |
| `resolution` | object | optional | Image / video render dimensions. |
| `characters` | array of `{name, description}` | ✅ | Add freely. |
| `chapters` | array | ✅ (text fields) | Sections of the song (verses, choruses, bridge, etc.). |
| `scenes` | array | ✅ (text fields) | See below. |
| `lyrics_source` | object | ✅ | The song lyrics. |
| `_meta` | object | ❌ | Read-only. |

### `concept`

| Field | Description |
|---|---|
| `song_title` | Song title. (Note: in music_video mode this key is literally `song_title`; in narration modes it appears as `production_title`.) |
| `concept_text` | 2–4 sentence overall video concept. Describe the visual story arc, mood, narrative pulled from the lyrics. |
| `style_text` | 1–3 sentence visual style description (palette, cinematography, era, mood). |
| `image_direction` | Preset key (e.g. `photorealistic`, `cinematic`, `cartoon`, `anime`, `sketch`, `custom`). |
| `custom_image_direction` | Free-text when `image_direction == "custom"`. |
| `global_color_override` | Palette enforcement key. |
| `custom_color_palette` | Free-text custom palette. |

### `resolution`

`resolution_width` / `resolution_height` are the unified defaults. `image_resolution_*` overrides for Klein/Z-Image image jobs; `video_resolution_*` overrides for LTX video jobs. `0` means "fall back to unified".

### `characters[]`

`name` + `description`. Descriptions are 1–3 sentences of renderable physical detail (age, build, signature clothing, hair, distinguishing features, performance demeanor).

**Character lifecycle on import (important):**

- `image_path` is intentionally **never** exported. Character reference images are project-internal files and aren't portable.
- Adding a **new** character creates it with `image_path: null`. The user must generate the reference image in-app (Concept tab → "Auto-generate Characters" or per-character ✨ button) before any scene can two-pass composite them.
- Updating an **existing** character's description preserves their existing `image_path`. The image is NOT auto-regenerated — the user decides when. If your description change is major (different outfit, new look), flag it in your import notes so they know to remake the reference.
- `name` is the join key (case-insensitive, whitespace-trimmed). Rename via import is unsupported (would orphan the existing image and break scene references); the user must rename in-app.
- Deletion via import is unsupported — omitting a character does not remove it.
- For two-pass to fire on a scene, every name in `character_refs_first` / `character_refs_last` must exist in `characters[]` AND have a non-null `image_path` in the project. The first is your job; the second is the user's post-import step.

### `chapters[]`

In music_video mode, chapters typically correspond to song sections (Verse 1, Chorus, Bridge, etc.). Editable fields are the same as the other modes: `description`, `character_focus`, `style_notes`, `name`, `color`. `order`, `depth`, `parent_chapter_order`, `start_time`, `end_time` are read-only.

### `scenes[]`

| Field | Description |
|---|---|
| `order_index` | 0-indexed scene order. Do NOT change. |
| `name` | Optional scene label. |
| `start_time` / `end_time` | Seconds. Do NOT change. |
| `chapter_order` | Which chapter (song section) this scene belongs to. |
| `lyrics_text` | The lyrics sung during this scene's time range (transcribed and time-aligned). Read-only. **In music_video mode this field is named `lyrics_text`** (not `narration_text`). |
| `image_prompt` | Prompt sent to the image generator. ~40–120 words. Front-load the most important visual elements. |
| `negative_prompt` | What to avoid. |
| `flow_idea` | 1–2 sentence storyboard idea. |
| `character_refs_first` | Character names in the first frame. |
| `character_refs_last` | Character names in the last frame (FF/LF mode). |
| `two_pass_enabled` | `true` when characters are referenced. |
| `color_override`, `custom_color_palette` | Per-scene palette. |
| `video_prompt` | Prompt sent to LTX 2.3 for video generation. ~30–80 words. Describe what HAPPENS over the clip's duration. |
| `last_frame_prompt`, `last_frame_negative_prompt` | Only used in FF/LF mode. |
| `video_mode` | `"single"` / `"v2v_extend"` / `"ff_lf"`. |
| `lipsync_enabled` | Whether to lipsync vocalists in this scene to the vocal stem. |
| `vocals_only_for_lipsync` | Send only the vocal stem (drums/bass/other stripped) for lipsync. Usually `true` when lipsync is on, so the model isn't confused by instrumentation. |
| `transition_in`, `transition_out` | `null` or `{ "type": "crossfade", "duration": 0.5 }`. |
| `image_movement` | Ken Burns — usually `null` since the video itself moves. |
| `use_story_flow` | Whether prompt enhancement incorporates `flow_idea`. Usually `true`. |
| `override_resolution`, `width`, `height` | Per-scene resolution override. |

### `lyrics_source`

The full song lyrics. Section markers in brackets (`[Verse 1]`, `[Chorus]`, etc.) are preserved.

---

## How to do good work in music_video mode

1. **Lyrics are the primary creative driver.** Each scene's `flow_idea`, `image_prompt`, and `video_prompt` must visualize what is being sung at that moment. The `lyrics_text` is your ground truth.

2. **Concrete imagery, not vague mood.** If the lyric mentions a red car, a broken mirror, dancing in the rain — those must appear. Don't abstract them into atmosphere.

3. **Translate metaphors visually.** "Heart on fire" can be glowing embers around a character's chest. "Drowning in sorrow" can be a character submerged in dark water. Make the abstract concrete.

4. **Instrumental sections.** When `lyrics_text` is empty, lean on the surrounding lyrical context, the chapter's `description`, and the song's overall arc to create transitional or atmospheric visuals that bridge the next sung lines.

5. **Natural exposure.** Avoid stacking superlatives (`ultra-bright, brilliant, luminous, glowing, sun-drenched, dazzling`). Music video aesthetics can still be high-energy without overexposing the image. Prefer specific motivated light sources and intentional contrast.

6. **Scene-to-scene variety.** Vary location, time of day, atmosphere, camera shot size, and angle. Music videos thrive on visual rhythm — alternate wide/close, movement/static, warm/cool.

7. **Camera direction continuity.** When adjacent scenes share a transition (e.g. scene B starts from scene A's final frame in FF/LF mode), avoid abrupt direction reversals — if scene A ends with a leftward pan, scene B should continue the motion or settle, not pan right.

8. **Character references.** Use `character_refs_first` when that character is in the frame. Empty list = no characters. When you put a name, also set `two_pass_enabled: true`.

9. **Video prompt = motion.** `image_prompt` describes the static composition; `video_prompt` describes what moves across the clip's duration. Music videos can afford energetic camera moves — but match them to the song's energy at that moment.

10. **Lipsync.** Default `lipsync_enabled: true` for scenes featuring a vocalist plausibly in frame. Set `vocals_only_for_lipsync: true` so the lipsync model isn't confused by drums and bass.

11. **Don't touch identifiers.** `order_index`, `order`, `start_time`, `end_time`, `chapter_order`, `parent_chapter_order`, `depth`, `rbmn_format_version`, `project_mode`, `project_name` are read-only.

---

## Common patterns

**Chorus moment with vocalist**

```json
{
  "image_prompt": "Medium shot of Maya facing camera in a wet alley, neon sign blooming in soft focus behind her, cream coat half-open, gold hoops catching the cyan reflection. Eye contact. 35mm, shallow depth of field, intentional cyan and amber color blocking.",
  "character_refs_first": ["Maya"],
  "two_pass_enabled": true,
  "video_prompt": "Maya holds eye contact, slow lift of her chin on the downbeat. Camera handheld, breathing slightly. Sodium street light flickers once.",
  "lipsync_enabled": true,
  "vocals_only_for_lipsync": true
}
```

**Instrumental break / atmospheric scene**

```json
{
  "image_prompt": "Wide shot of rain-streaked taxi window at night, blurred neon storefronts streaking past. No characters. Saturated cyan and amber palette, motion blur, intimate POV from the back seat.",
  "character_refs_first": [],
  "two_pass_enabled": false,
  "video_prompt": "Continuous horizontal motion blur left to right as the taxi moves down the street. Rain droplets on glass twitch with each bump. No camera movement of its own.",
  "lipsync_enabled": false
}
```

**Lyric-literal scene**

The lyric is "push the door, feel the weight":

```json
{
  "image_prompt": "Medium close-up of Maya leaning her shoulder into a heavy brass-handled door, breath visible in the cold air. Hallway light spills across her face.",
  "character_refs_first": ["Maya"],
  "two_pass_enabled": true,
  "video_prompt": "Maya pushes the door open one quarter of the way, pauses, then leans her full weight into it."
}
```

---

That's the contract. Treat the lyrics as the spine of every decision, write concrete imagery, vary the visual rhythm to match the song's energy, and honor the identifiers.
