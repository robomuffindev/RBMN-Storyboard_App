> **UPDATED v1.23.0 — official LTX schema.** The structured object now follows
> LTX's official JSON prompting format (ltx.io/blog/json-prompting-for-video-image-generation):
> top-level `scene` (description / lighting / atmosphere / color_palette /
> preserve_from_input_image), `subject` (type / description / action / position),
> `camera` (shot_type / angle / movement — always filled; "static" is explicit),
> and `duration` (seconds — paces the motion, one action beat per 2-3s).
> Legacy five-section objects stored by v1.22.0 are converted automatically on
> load (`normalize_video_json`), so existing scenes keep working; re-generate
> with ✨ to author natively in the official shape. Dispatch constraints
> (SFW / style / colour) are injected as `scene.style_constraints`. The lazy
> dispatch build now uses the FULL video enhance context, so autogen-built
> JSON prompts match editor-built quality. The schema below this banner
> documents the legacy v1.22.0 shape.

# Video JSON Prompt Mode (LTX)

**Status:** shipped in v1.22.0. Opt-in. Off by default.

## What it does

Instead of sending LTX a prose paragraph, Video JSON mode sends a **structured JSON
object as the prompt text**. LTX 2.3 parses the named fields (camera, timed action,
negatives, etc.) with higher fidelity than free prose, which gives much tighter control
over camera behavior, action timing, and motion — the same approach as the community
example that worked well.

This is *not* what LTX Director does. Director uses multi-segment **prose** relay across
keyframes; Video JSON is a single structured object describing one i2v/fflf/v2v clip.
Director has its own dispatch path and is unaffected by this toggle.

## The schema

```json
{
  "setting_environment": {
    "location": "...",
    "lighting": { "type": "...", "quality": "...", "contrast": "..." },
    "preserve_from_input_image": ["overall composition", "subject placement", "lighting direction", "color palette", "background geometry"],
    "environment_motion": ["..."],
    "color_palette": ["..."]
  },
  "subject_action": {
    "subject": { "description": "...", "starting_position": "...", "ending_position": "..." },
    "action_sequence": [
      { "time": "0s-3s", "action": "..." },
      { "time": "3s-6s", "action": "..." }
    ],
    "motion_characteristics": ["realistic body mechanics", "stable subject identity", "..."]
  },
  "camera_movement": {
    "camera_style": "...",
    "movement": "...",
    "framing": { "composition": "...", "lens": "...", "camera_height": "..." },
    "composition_behavior": ["..."],
    "forbidden_camera_behavior": ["no panning", "no handheld shake", "..."]
  },
  "visual_style_mood": { "style": ["..."], "image_characteristics": { "...": "..." }, "mood": ["..."] },
  "motion_timing_cues": {
    "duration_seconds": 10,
    "motion_intensity": "low-to-moderate",
    "animation_behavior": ["smooth temporal continuity", "stable architectural persistence", "..."],
    "negative_cues": ["no crowds appearing", "no rapid camera shake", "..."]
  }
}
```

`normalize_video_json` is lenient: it accepts a fenced ```json block, a bare JSON string,
or a dict; ensures the five canonical top-level sections exist; and raises only when the
object is empty (so a malformed/empty LLM reply falls back to prose rather than shipping
garbage).

## How to use it

1. **Concept tab → "Video JSON Prompt Mode"** (project-wide), or override per scene.
2. On a scene's **Prompt tab**, the green "Video JSON Prompt" panel appears when the mode
   is on. Click **✨ Generate with AI** to build the object from the scene's video context
   (it's told the real scene duration so the timing cues and `duration_seconds` line up),
   edit any field, and **Save Video JSON**.
3. Generate the video as usual. At dispatch the stored object is serialized and sent to LTX
   in place of the prose prompt.

`preserve_from_input_image` auto-fills from the scene's first frame when left empty, so
image-to-video keeps the established composition, subject placement, lighting, palette, and
background geometry.

## Where it lives

- **Backend**
  - `prompt_enhancer.py`: `VIDEO_JSON_SYSTEM_PROMPT`, `normalize_video_json(obj)`.
  - `concept.py`: `ConceptData.video_json_mode` (field + `from_settings` read + settings write).
  - `generation.py`: `POST /generate/video-json` (`build_video_json`), `_video_json_effective`,
    export adds `video_json_mode` + `video_json`.
  - `dispatcher.py`: `_build_or_get_video_json(job)` (stored wins → else build from prose +
    cache; auto-fills `preserve_from_input_image`); injected just before the prose prompt is
    captured/sent to `prepare_ltx_workflow` for the standard i2v/fflf/v2v paths.
- **Frontend**
  - `client.ts`: `buildVideoJson`, `saveConcept` type `video_json_mode?`.
  - `ConceptPanel.tsx`: project toggle.
  - `SceneEditor.tsx`: per-scene Prompt-tab editor (generate / edit / save).

## Off by default

When the toggle is off, nothing changes — the prose video prompt is sent exactly as before.
