# LLM Agent Instructions — Robomuffin Idea Factory project text data

## Mode: `narration_images`

You are editing the text data of a **Narration Images** project — a Ken-Burns-style slideshow built from a spoken-narration audio track and per-scene still images. There is NO video clip generation in this mode. Anything video-related in this document is for context only.

You will receive a JSON payload that follows the schema described below. **Do not invent new top-level keys.** Edit only the field values; preserve every existing key and structure exactly. Return the same JSON object back, edited.

---

## Output rules

1. Return valid JSON (no markdown fences in the output, no trailing prose).
2. Preserve `rbmn_format_version`, `project_mode`, and `project_name` exactly. Do not change them.
3. Preserve `order_index` on scenes and `order` on chapters — these are identifiers. Do not reorder, renumber, add, or remove scenes or chapters. Timing is fixed.
4. You may freely edit text content in `concept`, `chapters[].description`, `chapters[].character_focus`, `chapters[].style_notes`, every text field on every scene, and `lyrics_source.initial_text`.
5. Reference characters by their `name` string only. Names match case-insensitively. You may add new characters; you may not delete them.
6. Reference chapters from scenes by `chapter_order` (the chapter's integer `order`).
7. **Video-only fields (`video_prompt`, `video_mode`, `lipsync_enabled`, `vocals_only_for_lipsync`, `transition_in`, `transition_out`, `last_frame_*`) are not exported in this mode and will be ignored if you add them.** Do not invent them.

---

## Schema reference

### Top level

| Field | Type | Editable | Notes |
|---|---|---|---|
| `rbmn_format_version` | `"1.0"` | ❌ | Match exactly. |
| `project_mode` | `"narration_images"` | ❌ | Match exactly. |
| `project_name` | string | ❌ | Display label. |
| `concept` | object | ✅ | See below. |
| `resolution` | object | optional | Image render dimensions only. `video_resolution_*` is irrelevant in this mode (no video). |
| `characters` | array of `{name, description}` | ✅ | Add freely. |
| `chapters` | array | ✅ (text fields) | See below. |
| `scenes` | array | ✅ (text fields) | See below. |
| `lyrics_source` | object | ✅ | The source script. |
| `_meta` | object | ❌ | Read-only. |

### `concept`

| Field | Description |
|---|---|
| `production_title` | Title of the narration / production. |
| `concept_text` | 2–4 sentence overall concept. Frame this as a slideshow companion to spoken content. Do NOT use "song" or "lyrics" framing. |
| `style_text` | 1–3 sentence visual style description. |
| `image_direction` | Preset key (e.g. `photorealistic`, `cinematic`, `documentary_painterly`, `cartoon`, `anime`, `sketch`, `custom`). |
| `custom_image_direction` | Free-text when `image_direction == "custom"`. |
| `global_color_override` | Palette enforcement key. |
| `custom_color_palette` | Free-text custom palette. |

### `resolution`

Only `image_resolution_*` and the unified `resolution_*` matter for narration_images. `0` falls back to the unified value.

### `characters[]`

`name` + `description`. Descriptions are 1–3 sentences of renderable physical detail (age, build, clothing, hair, demeanor).

### `chapters[]`

| Field | Description |
|---|---|
| `order` | 1-indexed playback order. Do NOT change. |
| `name` | Chapter title. |
| `color` | Hex color shown on the timeline. |
| `description` | 2–4 sentences describing what happens in this chapter. Used for per-scene prompt generation. |
| `character_focus` | Array of character names. |
| `style_notes` | 1–2 sentences of chapter-specific visual notes. |
| `depth`, `parent_chapter_order` | Nesting metadata. Do NOT change. |
| `start_time` / `end_time` | Seconds. Do NOT change. |

### `scenes[]`

| Field | Description |
|---|---|
| `order_index` | 0-indexed scene order. Do NOT change. |
| `name` | Optional scene label. |
| `start_time` / `end_time` | Seconds. Do NOT change. |
| `chapter_order` | Which chapter this scene belongs to. |
| `narration_text` | The actual narration spoken during this scene. Read-only. |
| `image_prompt` | The prompt sent to the image generator (Klein / Z-Image). ~40–120 words. Front-load the most important visual elements. **This is the deliverable** for narration_images — the whole project is a sequence of these stills. |
| `negative_prompt` | What to avoid. |
| `flow_idea` | 1–2 sentence storyboard idea. Drives prompt enhancement. |
| `character_refs_first` | Character names appearing in this image. |
| `two_pass_enabled` | `true` when characters are referenced; Pass 1 paints the scene with Z-Image, Pass 2 composites characters with Klein. Set `true` only when `character_refs_first` is non-empty. |
| `color_override`, `custom_color_palette` | Per-scene palette overrides. |
| `image_movement` | Ken Burns motion applied to the still image at export time. Object shape: `{ "effect": "zoom_in_center", "intensity": 30, "easing": "ease_in_out" }`. Effects: `zoom_in_center`, `zoom_out_center`, `pan_left`, `pan_right`, `pan_up`, `pan_down`, `zoom_in_left`, `zoom_in_right`, `zoom_out_left`, `zoom_out_right`. Intensity 0–100. `null` = no movement. |
| `use_story_flow` | Whether prompt enhancement should incorporate `flow_idea`. Usually `true`. |
| `override_resolution`, `width`, `height` | Per-scene resolution override. |

### `lyrics_source`

The full source script the narrator reads.

---

## How to do good work in narration_images mode

1. **Treat the image as the deliverable.** Every still is on screen for several seconds while the narrator talks. It must reward holding the eye. Aim for paintings, not snapshots.

2. **Composition over motion.** This is not a video pipeline. Don't write motion verbs (`pans across`, `tracks toward`) in `image_prompt`. Describe the frame as a held composition.

3. **Use `image_movement` for Ken Burns.** Pick a subtle motion (`zoom_in_center`, `pan_right`) at low intensity (20–40) for most scenes. Reserve stronger motion for moments that benefit from it. Leave `image_movement: null` for scenes that should sit still (portraits with strong eye contact, sacred or reverent moments).

4. **Narration drives content.** Each scene's `image_prompt` must visualize what the narrator is saying at that moment. The `narration_text` is your ground truth.

5. **Be specific and concrete.** Replace abstract mood words with named objects, places, periods, actions.

6. **Honor period and culture.** When narration references a historical era, geography, or religion, every prop, garment, and architectural detail must fit. Do not contemporize.

7. **Natural exposure.** Avoid stacking superlatives like `ultra-bright, brilliant, luminous, glowing, sun-drenched, dazzling`. Prefer specific motivated light sources and explicit shadows.

8. **Variety across consecutive scenes.** Vary location, time of day, atmosphere, camera shot size and angle. Don't set every scene in the same place.

9. **Character references.** Use `character_refs_first` only when that character is supposed to appear. Empty list = no characters (b-roll, environment). When you put a name, also set `two_pass_enabled: true`.

10. **Don't touch identifiers.** `order_index`, `order`, `start_time`, `end_time`, `chapter_order`, `parent_chapter_order`, `depth`, `rbmn_format_version`, `project_mode`, `project_name` are read-only.

---

## Common patterns

**Establishing shot**

```json
{
  "image_prompt": "Wide shot of a windswept moor at first light, low stone wall snaking into mist, a single rook on a fencepost. Cool blue-grey palette, natural exposure, painterly composition.",
  "character_refs_first": [],
  "two_pass_enabled": false,
  "image_movement": { "effect": "zoom_in_center", "intensity": 35, "easing": "ease_in_out" }
}
```

**Character scene**

```json
{
  "image_prompt": "Medium close-up of Pierre standing in a stone doorway, soft north-window light on one cheek, leather pouch in hand, hesitant posture. Background out of focus.",
  "character_refs_first": ["Pierre"],
  "two_pass_enabled": true,
  "image_movement": { "effect": "pan_right", "intensity": 25, "easing": "ease_in_out" }
}
```

**Quiet portrait — hold still**

```json
{
  "image_prompt": "Close-up of Master Henri's hands on a half-dressed sandstone block, leather apron, soft afternoon light, sandstone dust on his knuckles.",
  "character_refs_first": ["Master Henri"],
  "two_pass_enabled": true,
  "image_movement": null
}
```

---

That's the contract. The whole project is a sequence of stills set against narration — treat each one as a single picture that has to earn its place on screen.
