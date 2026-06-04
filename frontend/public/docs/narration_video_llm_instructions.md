# LLM Agent Instructions — Robomuffin Idea Factory project text data

## Mode: `narration_video`

You are editing the text data of a **Narration Video** project — a video built from a spoken-narration audio track (audiobook, sermon, documentary VO, etc.), with image and video clips generated per scene to illustrate the spoken content.

You will receive a JSON payload that follows the schema described below. **Do not invent new top-level keys.** Edit only the field values; preserve every existing key and structure exactly. Return the same JSON object back, edited.

---

## Output rules

1. Return valid JSON (no markdown fences in the output, no trailing prose).
2. Preserve `rbmn_format_version`, `project_mode`, and `project_name` exactly. Do not change them.
3. Preserve `order_index` on scenes and `order` on chapters — these are identifiers. Do not reorder, renumber, add, or remove scenes or chapters. (Timing is fixed in the project.)
4. You may freely edit text content in `concept`, `chapters[].description`, `chapters[].character_focus`, `chapters[].style_notes`, every text field on every scene, and `lyrics_source.initial_text`.
5. Reference characters by their `name` string only. Names match case-insensitively against the `characters` array. You may add new characters (give them a `name` and `description`); you may not delete them.
6. Reference chapters from scenes by `chapter_order` (the chapter's integer `order`).
7. Sub-chapters reference their parent by `parent_chapter_order`.
8. **Do not** include UUIDs, file paths, or asset IDs — those don't appear in this schema. Anything that looks like a path or ID is not yours to touch.

---

## Schema reference

### Top level

| Field | Type | Editable | Notes |
|---|---|---|---|
| `rbmn_format_version` | `"1.0"` | ❌ | Schema version. Match exactly. |
| `project_mode` | `"narration_video"` | ❌ | Match exactly. |
| `project_name` | string | ❌ | Display label. |
| `concept` | object | ✅ | See below. |
| `resolution` | object | optional | Image / video render dimensions. Touch only if asked. |
| `characters` | array of `{name, description}` | ✅ | Add freely; preserve existing entries. |
| `chapters` | array | ✅ (text fields only) | See below. |
| `scenes` | array | ✅ (text fields only) | See below. |
| `lyrics_source` | object | ✅ | The source script the narrator reads. |
| `_meta` | object | ❌ | Read-only export metadata. |

### `concept`

| Field | Description |
|---|---|
| `production_title` | Title of the narration / production. |
| `concept_text` | 2–4 sentence overall concept. Describe the visual story, mood, narrative arc. Do NOT use "song" or "lyrics" framing — this is spoken-word content. |
| `style_text` | 1–3 sentence visual style description (palette, cinematography, era, materials). |
| `image_direction` | Preset art-direction key (e.g. `photorealistic`, `cinematic`, `documentary_painterly`, `cartoon`, `anime`, `sketch`). May be `""` or `"custom"`. |
| `custom_image_direction` | Free-text when `image_direction == "custom"`. |
| `global_color_override` | Palette enforcement key (e.g. `full_color`, `black_and_white`, `sepia`, `noir`, `custom`). |
| `custom_color_palette` | Free-text palette when `global_color_override == "custom"`. |

### `resolution` (optional — leave alone unless asked)

`resolution_width` / `resolution_height` are the unified defaults. `image_resolution_width` / `image_resolution_height` override for Klein/Z-Image image jobs; `video_resolution_width` / `video_resolution_height` override for LTX video jobs. `0` means "fall back to the unified value".

### `characters[]`

Each entry has just `name` and `description`. Descriptions should be physical/character details an image generator can render: age, build, clothing, hair, distinguishing features, demeanor. Keep them 1–3 sentences. Do not describe their image path.

### `chapters[]`

Each entry:

| Field | Description |
|---|---|
| `order` | 1-indexed playback order. Do NOT change. |
| `name` | Human-readable chapter title. |
| `color` | Hex color shown on the timeline (e.g. `#7c3aed`). |
| `description` | 2–4 sentences describing what happens in this chapter. Used as creative direction for per-scene prompt generation. |
| `character_focus` | Array of character names featured in this chapter. Match the `characters[].name` strings. |
| `style_notes` | 1–2 sentences of chapter-specific visual notes (lighting, mood, palette). |
| `depth` | `0` for top-level, `1` for sub-chapter. Do NOT change. |
| `parent_chapter_order` | Parent chapter's `order` when `depth > 0`. Do NOT change. |
| `start_time` / `end_time` | Seconds. Do NOT change. |

### `scenes[]`

Each entry:

| Field | Description |
|---|---|
| `order_index` | 0-indexed scene order. Do NOT change. |
| `name` | Optional scene label. |
| `start_time` / `end_time` | Seconds. Do NOT change. |
| `chapter_order` | Which chapter this scene belongs to (matches `chapters[].order`). Do NOT change. |
| `narration_text` | The actual narration spoken during this scene's time range (transcribed). Read-only context — do NOT invent or rewrite. |
| `image_prompt` | The prompt sent to the image generator (Klein / Z-Image) for the first frame of this scene. ~40–120 words. Front-load the most important visual elements. |
| `negative_prompt` | What to avoid. Keep short. Examples: `modern clothing, watermark, text on signs, contemporary tools`. |
| `flow_idea` | 1–2 sentence storyboard idea describing what visually happens in this scene. Drives prompt-enhancement context. |
| `character_refs_first` | Array of character names that should appear in the first frame. Matches `characters[].name`. Triggers two-pass character compositing when set. |
| `character_refs_last` | Array of character names that should appear in the last frame (for FF/LF video mode). |
| `two_pass_enabled` | `true` when Pass 1 paints the scene with Z-Image and Pass 2 composites characters with Klein. Set `true` only when `character_refs_first` has at least one entry. |
| `color_override` | Per-scene palette override; `null` falls back to project-level. |
| `custom_color_palette` | Per-scene custom palette text when `color_override == "custom"`. |
| `video_prompt` | The prompt sent to LTX 2.3 for video generation. Describe motion, camera move, action. ~30–80 words. Should describe what HAPPENS over the clip's duration. |
| `last_frame_prompt` / `last_frame_negative_prompt` | Only used in FF/LF video mode. Otherwise leave empty. |
| `video_mode` | One of `"single"` (one image driving I2V), `"v2v_extend"` (continuation from previous scene's video tail), `"ff_lf"` (first-frame + last-frame keyframes). |
| `lipsync_enabled` | Whether to lipsync the speaker(s) in this scene to the narration audio. Default `true` for narration_video. |
| `vocals_only_for_lipsync` | Use only the vocal stem for lipsync (always effectively `true` for narration, where the audio IS the voice). |
| `transition_in` / `transition_out` | `null`, or `{ "type": "crossfade", "duration": 0.5 }`. Other types: `dissolve`, `fade_from_black`, `fade_to_black`, `fade_from_white`, `fade_to_white`, `wipe_left`, `wipe_right`, `wipe_up`, `wipe_down`, `slide_left`, `slide_right`. Duration in seconds. |
| `image_movement` | Ken Burns effect — usually `null` in video mode since the video itself moves. |
| `use_story_flow` | Whether prompt enhancement should incorporate `flow_idea`. Usually `true`. |
| `override_resolution` / `width` / `height` | Per-scene resolution override. Leave `null` / `false` unless intentionally set. |

### `lyrics_source`

The full pasted source script. `initial_text` is the canonical text the narrator reads. You may edit / tidy it; the app uses it to reconcile Whisper transcription artifacts at export time.

---

## How to do good work in narration_video mode

1. **Treat narration as the spine.** Each scene's `flow_idea`, `image_prompt`, and `video_prompt` must visualize what the narrator is saying at that moment. The `narration_text` is your ground truth.

2. **Be specific and concrete.** Replace abstract mood words with named objects, places, periods, actions. "A wooden bench under a leaning maple at dusk" beats "a peaceful scene".

3. **Honor period and culture.** When the narration references a historical era, geography, or religion, every prop, garment, and architectural detail in your prompts must fit. Do not contemporize.

4. **Natural exposure.** When writing `image_prompt`, avoid stacking superlatives (`ultra-bright, brilliant, luminous, glowing, sun-drenched, dazzling`). The image generator interprets these as overexposure. Prefer specific motivated light sources ("a single oil lamp", "north-facing window light", "overcast sky") and explicitly mention shadows / depth / contrast.

5. **Scene-to-scene variety.** Vary location, time of day, atmosphere, camera shot size, and angle across consecutive scenes. Do not set every scene in the same place with only camera tweaks.

6. **Character references.** Put character names in `character_refs_first` only when that character is supposed to appear in the frame. Empty list = no characters (use for establishing shots, b-roll, environment-only scenes). When you put a name, also set `two_pass_enabled: true`.

7. **Video prompt = what happens.** `image_prompt` describes the static composition; `video_prompt` describes the motion across the clip's duration. Keep camera moves slow and motivated for narration content (slow dolly push-in, hold, tilt up to reveal). Avoid music-video editing energy.

8. **Chapters group meaning.** Use `chapters[].description` to set the creative direction for the whole arc; use `style_notes` for the visual register (palette, lighting, register).

9. **Lipsync.** Default to `lipsync_enabled: true` on scenes where a speaker is plausibly in frame and could be the narrator. For pure b-roll / environment shots without a speaker, set to `false`.

10. **Don't touch identifiers.** `order_index`, `order`, `start_time`, `end_time`, `chapter_order`, `parent_chapter_order`, `depth`, `rbmn_format_version`, `project_mode`, `project_name` are all read-only. Edit only text and arrays of text.

---

## Common patterns

**Establishing shot (no characters)**

```json
{
  "image_prompt": "Wide shot of a windswept moor at first light, low stone wall snaking into mist, a single rook on a fencepost. Cool blue-grey palette, natural exposure, painterly composition.",
  "character_refs_first": [],
  "two_pass_enabled": false,
  "video_prompt": "Camera holds. Mist drifts slowly right to left. The rook turns its head."
}
```

**Character scene (single character)**

```json
{
  "image_prompt": "Medium close-up of Pierre standing in a stone doorway, soft north-window light on one cheek, leather pouch in hand, hesitant posture. Background out of focus.",
  "character_refs_first": ["Pierre"],
  "two_pass_enabled": true,
  "video_prompt": "Pierre takes one step forward, glances down at the pouch, then up at the master's stool. Camera handheld, intimate distance."
}
```

**Dialogue-style scene**

```json
{
  "image_prompt": "Two-shot: Pierre on the left in profile, Master Henri on the right facing him across a workbench littered with chisels and a half-dressed sandstone block. Warm afternoon light from a high window. Both visible from waist up.",
  "character_refs_first": ["Pierre", "Master Henri"],
  "two_pass_enabled": true,
  "lipsync_enabled": true
}
```

---

That's the contract. Stay inside the schema, honor the identifiers, write specific concrete prompts, and treat the narration text as the spine of every decision.
