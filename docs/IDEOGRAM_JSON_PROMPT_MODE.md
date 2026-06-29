# Structured JSON Prompt Mode (Ideogram 4 / Krea 2) — Research & Groundwork

> Status: **research only — not implemented.** This document is the full
> groundwork for a future "JSON Prompt Mode" that gives precise control over
> composition, spatial positioning (bounding boxes), in-image text, and color
> palettes, with both an LLM that writes the JSON optimally and an interactive
> visual builder (like the ComfyUI KJ node, but built into our app).

---

## 1. What this is

[Ideogram 4](https://github.com/ideogram-oss/ideogram4) is an open image model
**trained exclusively on structured JSON "captions"** (passed as a string).
Plain text works, but a JSON object matching the caption schema gives
"significantly better results, especially for controllability, spatial layout,
and style fidelity." It's the model's native language.

Kijai's **"Ideogram 4 Prompt Builder KJ"** node (in
[ComfyUI-KJNodes](https://github.com/kijai/ComfyUI-KJNodes), file
`nodes/ideogram4_nodes.py`, node class `Ideogram4PromptBuilderKJ`) is a **visual
editor** for that JSON: you drag rectangles on a 1000×1000 canvas to create
regions, set each region's type (object / text), description, literal text, and
color palette; set background + style fields as widgets; and the node **outputs
the assembled caption JSON string**. The **V2** update keeps the exact same
inputs/outputs but adds freehand tools (brush, polyline, line, rectangle,
ellipse) — each drawn shape is auto-outlined into its own bounding box.

That output string is just a prompt — so it can be fed to **any** model's text
encoder. Ideogram 4 is the native target; **Krea 2 reportedly also responds well
to this positional JSON** (plausible because Krea 2's text encoder is the
LLM-style **Qwen3‑VL**, which parses structured/positional language far better
than a CLIP encoder). Treat Krea 2 support as empirically-good-but-unofficial —
something to A/B test, not a guarantee.

### Why the user wants it

Manual JSON authoring is painful; the ComfyUI node's canvas helps but is clunky.
The goals are: (a) an **in-app visual builder** for manual region work, and
(b) an **LLM that emits optimal JSON automatically** when the mode is on and a
compatible model is selected — giving "much greater prompting ability with
positioning."

---

## 2. The exact JSON caption schema

Three top-level fields:

1. `high_level_description` — optional string, strongly recommended (1–2 sentences).
2. `style_description` — optional object (style / lighting / medium / palette).
3. `compositional_deconstruction` — **required** object (`background` + `elements`).

**Key order is strict** (the model was trained on a consistent order; the
official `CaptionVerifier` warns on unknown/missing/out-of-order keys). Encode
with `separators=(",", ":")` and `ensure_ascii=False`.

### `style_description` — exactly one of `photo` / `art_style`

| Caption type | Required key order |
|---|---|
| Photo (uses `photo`) | `aesthetics`, `lighting`, `photo`, `medium`, `color_palette` |
| Non-photo (uses `art_style`) | `aesthetics`, `lighting`, `medium`, `art_style`, `color_palette` |

- `aesthetics`, `lighting`, `medium` required when `style_description` present.
- `medium`: `"photograph"`, `"illustration"`, `"3d_render"`, `"painting"`, `"graphic_design"`, …
- `color_palette`: optional, **must be last**; up to **16** uppercase `#RRGGBB`.

### `compositional_deconstruction` — `background` then `elements`

Each element's key order is fixed by type:

| Type | Required key order |
|---|---|
| `"obj"` | `type`, `bbox`, `desc`, `color_palette` |
| `"text"` | `type`, `bbox`, `text`, `desc`, `color_palette` |

- `bbox` (optional but recommended): **`[y_min, x_min, y_max, x_max]`** in
  **normalized 0–1000** coordinates, **origin top-left**.
- `text` (text type only): the literal string to render (supports `\n`).
- `desc`: detailed visual description (for text, describe font/weight/style).
- `color_palette` (optional): up to **5** uppercase `#RRGGBB` per element.

### Bounding-box positioning recipes (0–1000 grid, [y_min, x_min, y_max, x_max])

- Headline across the **top third**: y ≈ 50–300, x ≈ 100–900.
- Subject in the **lower center**: y ≈ 500–950, x ≈ 250–750.
- Full-frame element: `[0, 0, 1000, 1000]`.
- Rough placement is fine — the model handles small imprecision gracefully.

### Color rules

- Uppercase `#RRGGBB` only (`#FF6B35`, never `#ff6b35` or `#fff`).
- Include background + contrast (highlight & shadow) colors to steer lighting.

### Canonical example (photo, with text + bboxes)

```json
{
  "high_level_description": "A medium-shot photograph of a barista pouring latte art in a cozy cafe.",
  "style_description": {
    "aesthetics": "warm, inviting, shallow focus",
    "lighting": "soft window light, gentle morning glow",
    "photo": "50mm, f/1.8, shallow depth of field",
    "medium": "photograph",
    "color_palette": ["#3B2A1F", "#C9A27E", "#F2E8DC", "#6B4F3A", "#1A1A1A"]
  },
  "compositional_deconstruction": {
    "background": "A cozy cafe interior, warm wood tones, blurred shelves and hanging lights behind the counter.",
    "elements": [
      {"type": "obj", "bbox": [250, 300, 900, 720], "desc": "A barista in an apron tilting a steel pitcher, pouring a leaf pattern into a white ceramic cup."},
      {"type": "text", "bbox": [60, 120, 180, 880], "text": "MORNING BREW", "desc": "Bold cream sans-serif title across the top, slight letter spacing."}
    ]
  }
}
```

(The official guide also ships a full F1-poster example with ~16 precisely-placed
text elements — proof of how granular the typography control gets.)

---

## 3. How the LLM should write it (the "magic prompt" pattern)

Ideogram's own pipeline expands a casual plain-text idea into a full JSON caption
with an LLM — they call it **Magic Prompt**, and crucially **their system prompts
are open source** (`src/ideogram4/magic_prompt_system_prompts/` in the
ideogram4 repo). They tested with **Claude Opus**. There's also an open-source
**`CaptionVerifier`** that flags unknown/missing/out-of-order keys.

Implication for us: we don't have to invent the prompting from scratch. We can
base our system prompt on Ideogram's open-source magic-prompt prompt (adapted to
our scene/narration context), and optionally port `CaptionVerifier`'s checks to
validate/repair the LLM output before dispatch.

---

## 4. How it would slot into THIS app

Our pipeline already has every hook this needs — JSON Prompt Mode is mostly a new
prompt format plus a UI, not new plumbing.

### 4a. Gating / settings
- New setting, e.g. `json_prompt_mode` (off by default), **only selectable when a
  compatible first-pass model is chosen** (`krea2_turbo`, or a future
  `ideogram4` generator). Mirror how `single_image_generator` gates behavior.
- When on + compatible model: the scene's prompt becomes the JSON caption string.

### 4b. LLM prompting (`backend/services/llm/prompt_enhancer.py`)
- Add a `JSON_PROMPT_SYSTEM_PROMPT` (Ideogram-schema-aware, adapted from their
  open-source magic-prompt prompt) and register it under the compatible model
  keys in `BUILTIN_SYSTEM_PROMPTS` (same pattern as the Krea 2 prompt).
- Enhance returns a **valid JSON caption** (strict key order, 0–1000 bboxes,
  uppercase hex). Run a port of `CaptionVerifier` to validate and auto-fix
  ordering before saving.
- Feed our scene context (lyrics/narration, characters, palette override,
  resolution → aspect) so the JSON reflects the actual scene + global palette.

### 4c. Dispatch / workflow injection (`dispatcher.py`, `workflow.py`)
- The JSON string is just the prompt. For Krea 2 it goes into the **MANUAL PROMPT
  primitive** exactly like today's `prepare_krea2_workflow` injection — **no
  workflow surgery needed**, the node graph is unchanged.
- For a future native **Ideogram 4** generator, add a `prepare_ideogram4_workflow`
  + workflow JSON (and the model files) the same way we added Krea 2.
- Two-pass: JSON mode would apply to **Pass 1** (scene composition). Pass 2
  (Klein character compositing) stays as-is — Klein uses its own ref system.

### 4d. Visual builder (frontend — the big new piece)
A React component (HTML5 canvas or SVG) that reproduces the KJ node's editor:
- A 1000×1000 (aspect-scaled) canvas; draw/move/resize **rectangle regions**
  (start with rects; freehand-→-bbox is a V2 nicety).
- Per-region panel: type (obj/text), `desc`, `text` (if text), `color_palette`
  swatches (≤5).
- Global panel: `high_level_description`, `style_description` (aesthetics,
  lighting, photo/art_style + medium), global `color_palette` (≤16).
- Live two-way binding to the JSON: edit boxes → JSON; **paste JSON → boxes**
  (the exact feature Ideogram users requested in KJNodes issue #651).
- "✨ Generate with AI" button → calls the enhancer to fill the JSON, which then
  renders as draggable regions the user can tweak. This is the dream workflow:
  LLM drafts, human nudges.
- Output is stored on the scene as the prompt (and a parsed structure for re-edit).

### 4e. Data model
- Store both the **raw JSON string** (what's dispatched) and the **parsed object**
  (for the builder to re-open/edit) on the scene parameters, e.g.
  `json_prompt` + `json_prompt_struct`. Keep them in sync like we do elsewhere.

---

## 5. Compatibility notes & open questions (for the user to decide later)

- **Krea 2 + JSON**: reported to work; not officially trained on it. Needs A/B
  testing on your fleet. If Krea 2 ignores bboxes, the JSON still works as a rich
  descriptive prompt (positions become descriptive hints). Worth measuring.
- **Native Ideogram 4** is the gold standard for this format. Adding it as a
  generator (like Krea 2) would be the highest-fidelity path — it needs its own
  model files + workflow. Decision: do we want Ideogram 4 as a generator too, or
  start with Krea 2 + JSON only?
- **Aspect ratio**: bboxes are normalized 0–1000 regardless of output size, so
  our resolution settings map cleanly; we should pass the aspect to the LLM so it
  places elements sensibly.
- **Two-pass interaction**: confirm whether you want character refs (Pass 2) to
  still run when JSON mode places characters via `obj` regions, or whether JSON
  mode is single-pass only for character scenes.
- **Validation strictness**: port `CaptionVerifier` (warn-and-fix) vs. trust the
  LLM. Recommend port — it's cheap and prevents silent quality loss from
  key-order drift.

---

## 6. Build order when we implement (suggested)

1. Backend: `JSON_PROMPT_SYSTEM_PROMPT` + registry + a `CaptionVerifier` port +
   `json_prompt_mode` setting + serialization. (Headless, testable.)
2. Dispatch: route the JSON string into the Krea 2 MANUAL PROMPT node when mode
   is on. (Reuses existing injection.)
3. Frontend: the visual builder component + JSON⇄regions binding + "Generate with
   AI". (The largest piece; can ship after 1–2 so the LLM path works first.)
4. Optional: native Ideogram 4 generator (model files + workflow) for max
   fidelity.

---

## 7. EXACT node field mapping (from the supplied workflow)

The supplied `KREA2_ULTRA_WORKFLOW_with_Idogram_prompt_node.json` shows the node
wired into the Krea 2 graph. This is the ground truth for how we drive it.

**Wiring:** node `14` `Ideogram4PromptBuilderKJ` → output 0 → `Any Switch
(rgthree)` (`78:72`) input **`any_01`**; the MANUAL PROMPT primitive (`143`) is on
**`any_02`**. The Any Switch returns the **first non-empty** input, so `any_01`
(the Ideogram builder) **wins when populated**, else it falls back to the manual
prompt. Everything downstream (CLIP encode → Krea2 Rebalance → KSampler) is
identical to the plain Krea 2 workflow — so JSON mode is purely "fill node 14."

**Node 14 inputs (the real widget names — note they differ from raw Ideogram):**

| Node 14 field | Type | Notes |
|---|---|---|
| `width`, `height` | int | output dims (1920×1088 in the sample) |
| `high_level_description` | string | 1–2 sentence summary |
| `background` | string | environment, subject-free |
| `style` | enum | `"photo"` (or art mode) — selects which detail field applies |
| `style.photo` | string | camera/lens/framing (used when `style="photo"`). Art mode uses the corresponding `style.art_style` field (confirm exact name when building). |
| `aesthetics` | string | mood keywords |
| `lighting` | string | light sources/quality |
| `medium` | enum | `"photograph"`, `"illustration"`, `"3d_render"`, `"painting"`, `"graphic_design"` |
| `style_palette_data` | **JSON string** | `["#RRGGBB", ...]` up to 16, UPPERCASE |
| `elements_data` | **JSON string** | array of element objects (below) |
| `bg_brightness` | int | node-specific bg brightness (sample=25); not part of the Ideogram schema |
| `import_mode` | enum | `"when empty"` — imports `elements_data` onto the canvas |
| `output_format` | enum | `"compact"` |
| `coord_mode` | enum | `"normalized"` — **input coords are 0–1 fractions** |
| `bbox_order` | enum | `"yx"` — node emits Ideogram `[y_min,x_min,y_max,x_max]` |

**Element object in `elements_data`** (the natural canvas format — the node
converts to Ideogram `[y,x,y,x]` 0–1000 on output):

```json
{"type":"obj","text":"","desc":"...","palette":["#RRGGBB"],"x":0.30,"y":0.12,"w":0.40,"h":0.80}
```

- `x,y` = **top-left corner** of the box (0–1). `w,h` = width/height (0–1).
- `type`: `"obj"` (with `text":""`) or `"text"` (with the literal string in `text`).
- `palette`: up to 5 UPPERCASE hex for that element.

**Conversion the node does:** `y_min=y·1000, x_min=x·1000, y_max=(y+h)·1000,
x_max=(x+w)·1000`. So our LLM and visual builder both work in easy **x/y/w/h
fractions** and the node handles the Ideogram math.

> Implementation: in JSON mode, `prepare_krea2_workflow` (or an ideogram variant)
> populates node 14's fields from our caption object and serializes
> `style_palette_data` / `elements_data` with `json.dumps`. The Any Switch then
> uses node 14 automatically. Leave node 14 fields empty (or remove it) for plain
> Krea 2 mode so the MANUAL PROMPT path is used.

---

## 8. LLM Prompting Method (ready to use)

The LLMs do **not** know this format — the system prompt must fully teach it. This
is the production-ready method; gate it so it's used **only when JSON Prompt Mode
is ON and a compatible model is selected** (`krea2_turbo` / future `ideogram4`).
Drop the system prompt below into `prompt_enhancer.py` as
`JSON_PROMPT_SYSTEM_PROMPT` and register it under those model keys.

### 8a. LLM output contract (maps 1:1 to node 14)

The LLM returns ONLY this object; the dispatcher maps it to node 14:

```json
{
  "high_level_description": "string",
  "background": "string",
  "style": "photo" | "art",
  "style_detail": "string (camera/lens if photo; art-style if art)",
  "aesthetics": "string",
  "lighting": "string",
  "medium": "photograph|illustration|3d_render|painting|graphic_design",
  "style_palette": ["#RRGGBB", "... up to 16"],
  "elements": [
    {"type":"obj","desc":"string","palette":["#RRGGBB"],"x":0.0,"y":0.0,"w":0.0,"h":0.0},
    {"type":"text","text":"LITERAL","desc":"string","palette":["#RRGGBB"],"x":0.0,"y":0.0,"w":0.0,"h":0.0}
  ]
}
```

Mapping → node 14: `style_detail`→`style.photo` (photo) or `style.art_style`
(art); `style_palette`→`style_palette_data` (json.dumps); `elements`→
`elements_data` (json.dumps, obj elements get `"text":""`); set
`coord_mode:"normalized"`, `bbox_order:"yx"`, `output_format:"compact"`,
`import_mode:"when empty"`, width/height from scene resolution.

### 8b. System prompt (JSON_PROMPT_SYSTEM_PROMPT)

```
You are an expert visual director writing STRUCTURED JSON CAPTIONS for an
Ideogram-4-style image model (used here with Krea 2 or Ideogram 4). These models
read a structured LAYOUT — a global summary, a style block, a background, and a
list of spatially-placed ELEMENTS, each with its own bounding box and color
palette — giving precise control over WHERE things sit and which colors dominate.
The model does not know this format by instinct, so you MUST emit a clean,
complete structure every time.

OUTPUT CONTRACT — output ONLY this JSON object (no prose, no markdown fences):
{
  "high_level_description": "<1-2 sentence summary of the whole image>",
  "background": "<the environment/setting, described as if the main subject were absent>",
  "style": "photo" | "art",
  "style_detail": "<if photo: camera/lens/framing e.g. '85mm portrait lens, 16:9, shallow depth of field'; if art: the art style e.g. 'flat vector illustration, bold outlines'>",
  "aesthetics": "<mood / aesthetic keywords>",
  "lighting": "<light sources, direction, quality>",
  "medium": "photograph" | "illustration" | "3d_render" | "painting" | "graphic_design",
  "style_palette": ["#RRGGBB", ...],
  "elements": [
    {"type":"obj","desc":"<what it is + appearance>","palette":["#RRGGBB", ...],"x":0.0,"y":0.0,"w":0.0,"h":0.0},
    {"type":"text","text":"<EXACT words to render>","desc":"<font / weight / placement>","palette":["#RRGGBB", ...],"x":0.0,"y":0.0,"w":0.0,"h":0.0}
  ]
}

COORDINATE SYSTEM (critical):
- The frame is normalized 0.0-1.0. Origin (0,0) is the TOP-LEFT corner.
- x,y = the TOP-LEFT corner of the element box. w,h = its width and height.
- The box spans x..x+w horizontally and y..y+h vertically. Keep x+w <= 1 and y+h <= 1.
- Placement guide: top band y~0.05-0.30; vertical center y~0.35-0.65; lower y~0.55-0.95.
  Left x~0.05-0.35; center x~0.33-0.66; right x~0.60-0.95. Full frame = x:0,y:0,w:1,h:1.
- Rough placement is fine; the model tolerates small imprecision. Boxes MAY overlap
  (e.g. a face box nested inside a person box).

DECOMPOSE THE SCENE (this layering is what produces maximum quality):
1. background: describe the setting WITHOUT the main subject.
2. Add elements from largest/most-important to smallest detail:
   - the main subject (full body / main object) as one obj with a box,
   - then KEY sub-details nested inside it (face, hair, a garment, a held prop),
   - then supporting scene elements (furniture, foreground props),
   - finally ONE full-frame obj (x:0,y:0,w:1,h:1) describing the overall mood/finish
     to unify the image.
3. Give EACH element its own palette (up to 5 hex) drawn from the global palette.

STYLE BLOCK:
- Photographic scene: "style":"photo", a camera/lens "style_detail", "medium":"photograph".
- Non-photographic scene: "style":"art", an art-style "style_detail", and the matching
  "medium" ("illustration" / "3d_render" / "painting" / "graphic_design").

COLOR RULES:
- All hex UPPERCASE #RRGGBB (never lowercase or #abc shorthand).
- style_palette: up to 16 colors for the whole image — INCLUDE the background tone and
  BOTH highlight and shadow tones.
- Per-element palette: up to 5 colors that fit that element, consistent with the global palette.
- If a COLOR PALETTE OVERRIDE is provided in the context, it takes ABSOLUTE priority:
  build style_palette AND every element palette ONLY from those colors (e.g. "black and
  white" -> only #000000-#FFFFFF greys; introduce no other hue anywhere).

TEXT:
- Add a "text" element ONLY when the scene explicitly calls for words in the image
  (a title, sign, label). Put the EXACT words in "text" and describe the typography in "desc".
- For narration / music-video scenes that should have NO on-image text, add NO text elements.

CONTENT RULES:
- The scene lyrics/narration and storyboard input are your PRIMARY direction: include the
  SPECIFIC objects, actions and setting they describe; never substitute a generic scene.
- Two-pass character scenes: describe the scene and leave appropriate space/boxes for
  characters; their identity is composited later, so describe placement/pose generically
  here unless a specific name is given.
- Each scene must be visually DISTINCT from the others in the production.
- Output ONLY the JSON object.
```

### 8c. Worked example (the sample workflow's forest-stump scene → contract)

```json
{
  "high_level_description": "An extreme-wide dawn view inside an ancient root-tangled forest, centered on a cracked old stump with a swollen rounded bump, lit by cool silver-green morning light.",
  "background": "A misty primeval forest at dawn; twisted sycamore branches arch overhead, soft shafts of cool silver-green light filter through low mist, damp mossy ground and tangled roots recede into deep focus.",
  "style": "photo",
  "style_detail": "24mm wide lens, slow crane-down composition through branches, deep focus, high dynamic range, cinematic stop-motion atmosphere",
  "aesthetics": "gothic fantasy, mythical, hushed, atmospheric, painterly realism",
  "lighting": "cool dawn light in soft shafts through mist, balanced shadows, gentle rim light on bark and moss",
  "medium": "photograph",
  "style_palette": ["#1E2A24","#3A4A3F","#6B7A66","#9FAE92","#C8D2BC","#5A4A3A","#2A2420","#0E1310"],
  "elements": [
    {"type":"obj","desc":"A cracked old tree stump with a swollen rounded bump and dark carved bark, exposed roots gripping wet loam like skeletal hands.","palette":["#5A4A3A","#2A2420","#3A4A3F","#9FAE92"],"x":0.34,"y":0.42,"w":0.32,"h":0.50},
    {"type":"obj","desc":"A small solemn empty clearing beside the stump, left open for a figure to be composited later; soft moss and slick wet leaves.","palette":["#3A4A3F","#6B7A66","#C8D2BC","#2A2420"],"x":0.60,"y":0.55,"w":0.28,"h":0.40},
    {"type":"obj","desc":"Twisted sycamore branches arching across the top of the frame, filtering cool silver-green light through low mist.","palette":["#2A2420","#3A4A3F","#9FAE92","#C8D2BC"],"x":0.00,"y":0.00,"w":1.00,"h":0.35},
    {"type":"obj","desc":"The full forest scene unified by deep focus, drifting dawn mist, pale lichen, tiny mushrooms and tangled roots — a hushed mythical mood.","palette":["#1E2A24","#6B7A66","#5A4A3A","#0E1310"],"x":0.00,"y":0.00,"w":1.00,"h":1.00}
  ]
}
```

(No text elements — the scene specifies no on-image text. Note the layered
decomposition: background → stump → reserved character space → canopy →
full-frame mood, each with its own coherent sub-palette.)

### 8d. Gating + validation (when implemented)

- Select this system prompt in the enhancer ONLY when `json_prompt_mode` is ON
  AND `single_image_generator` is compatible (`krea2_turbo` / `ideogram4`).
  Otherwise use the normal Krea 2 / Klein / Z-Image prompt — other models would
  be confused by JSON output.
- Validate the LLM output before dispatch: parse JSON; coerce hex to UPPERCASE;
  clamp x,y,w,h to 0–1 and ensure x+w≤1, y+h≤1; enforce palette caps (16 global /
  5 per element); drop unknown keys. On parse failure, retry once, then fall back
  to the plain natural-language prompt so a scene never fails to render.
- Pass the project's palette override + aspect ratio into the LLM context so the
  caption respects them.

---

## Sources

- Ideogram 4 official prompting guide / JSON caption schema — <https://github.com/ideogram-oss/ideogram4/blob/main/docs/prompting.md>
- ComfyUI Ideogram 4.0 partner-node tutorial (structured JSON, builder note) — <https://docs.comfy.org/tutorials/partner-nodes/ideogram/ideogram-v4>
- ComfyUI-KJNodes `Ideogram4PromptBuilderKJ` source — <https://github.com/kijai/ComfyUI-KJNodes/blob/main/nodes/ideogram4_nodes.py>
- KJNodes issue #651 (paste-JSON-to-canvas request) — <https://github.com/kijai/ComfyUI-KJNodes/issues/651>
- KJNodes issue #667 (V2 freehand drawing tools) — <https://github.com/kijai/ComfyUI-KJNodes/issues/667>
- Runware structured-prompts guide — <https://runware.ai/docs/models/ideogram-4-0/guides/structured-prompts>
- JSON caption schema (DeepWiki) — <https://deepwiki.com/ideogram-oss/ideogram4/4.1-json-caption-schema>
- Civitai "Ideogram 4 + KJ Prompt Builder Visual Composition Director" workflow — <https://civitai.com/models/2681872>

## v1.17.0 — Enhance builds the caption, references carry layout, prompt export

- **Enhance also builds the structured caption.** When Ideogram mode is effective for a Krea 2 scene, clicking **Enhance** on the first frame now also calls `build_json_prompt` with the freshly enhanced prose and stores the structured caption on `scene.parameters.json_prompt` — so the curated, positioned prompt is ready immediately (open the JSON Prompt editor to view/hand-edit). Prose still drives the human-readable prompt + the at-render fallback.
- **Generated images carry their caption.** The dispatcher stashes the caption into the job params on the Ideogram redirect and writes it to the generated asset's `meta.ideogram_caption`. When that image is later used as a reference, `_build_ref_layout_block` / `_summarize_caption_layout` turn the stored elements into a compact `[vertical horizontal]` layout summary that is added to the enhance context **combined with** the vision description (independent of the vision toggle).
- **Reference vision-scan audit panel** (Image tab): clickable thumbnail of each reference + its `meta.vision_description`, with an `ideogram` badge for structured-prompt images.
- **Download Prompts JSON** (`GET /generate/prompts-export`): full troubleshooting export — first/last/video prose, the exact `submitted_to_comfy` strings, models, resolution, seed, resolved references, and for Ideogram frames the FULL structured caption (`format: ideogram_structured_caption_v1`) marked as what's sent to ComfyUI.
