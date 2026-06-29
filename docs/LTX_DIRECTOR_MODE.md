# LTX Director Mode — Research & Design Groundwork

> Status: **SHIPPED (1.12.1).** Backend + full-screen editor + Retake + High-Quality two-stage, audited. This documents what the
> WhatDreamsCost **LTX Director** node actually does (from its source), corrects
> a few assumptions, and lays out how to bring it into this app as a per-scene
> "Director Mode" with a full-screen timeline editor.

Sources: [WhatDreamsCost-ComfyUI repo](https://github.com/WhatDreamsCost/WhatDreamsCost-ComfyUI),
[`ltx_director.py`](https://github.com/WhatDreamsCost/WhatDreamsCost-ComfyUI/blob/main/ltx_director.py),
[Prompt Relay](https://gordonchen19.github.io/Prompt-Relay/).

---

## 1. What the node actually is (from the source)

`LTXDirector` is a **single ComfyUI node** that turns a visual timeline into the
conditioning + latents + keyframe guides + audio for one LTX 2.3 generation. It
is the successor to LTX Sequencer / Multi-Image-Loader and integrates **Prompt
Relay**. The timeline editor is a web (JS) extension that writes its state into
the node's `timeline_data` and a few derived widgets.

### The node's real inputs (what we'd populate)
- `model`, `clip`, optional `audio_vae`, optional `optional_latent`.
- `global_prompt` — conditions the **entire** video (persistent characters/scene).
- `duration_frames` / `duration_seconds` — timeline length (pixel-space scale).
- `timeline_data` (JSON) — the editor state: `segments` (image/text, each with
  `start`, `length`, `type`, `imageFile`/`imageB64`, `prompt`) and `audioSegments`
  (each with `start`, `trimStart`, `length`, `audioFile`/`audioB64`).
- `local_prompts` — `|`-separated, one prompt **per timeline segment** (auto-filled from the editor).
- `segment_lengths` — comma-separated pixel-frame lengths per segment.
- `guide_strength` — comma-separated strengths for the image keyframes.
- `use_custom_audio` (bool) — ON = condition on the timeline audio; OFF = let LTX generate its own audio.
- `epsilon` — prompt-relay transition sharpness (0.001 = hard cuts; ~0.5 = soft blends).
- `frame_rate`, `display_mode` (frames/seconds), `custom_width`/`custom_height`,
  `resize_method` (maintain AR / crop / pad / stretch), `divisible_by` (32 for LTX),
  `img_compression` (H.264 CRF artefacts on guide images so they match generation).

### The node's outputs (downstream wiring)
- `model` (patched with prompt-relay attention masks), `positive` (scheduled conditioning),
- `video_latent` (auto-sized empty LTXV latent), `audio_latent` (empty, or custom-audio-encoded with a noise mask),
- `guide_data` (GUIDE_DATA = the keyframe images + their `insert_frames` + `strengths`) → feeds an LTXV add-guide node,
- `frame_rate`, `combined_audio` (the assembled timeline audio for muxing/preview).

---

## 2. The feature you didn't name — and it's the heart of it: **Prompt Relay**

The single most powerful thing this node does is **temporal prompt scheduling**.
Instead of one prompt for the whole clip, you place **text segments along the
timeline**, each with its own prompt, and the node builds a *scheduled
conditioning + attention mask* so **different prompts drive different parts of
the same generation**:

> 0.0–2.0s: "she walks into the dim room" → 2.0–4.0s: "she sits at the table" →
> 4.0–6.0s: "she looks up and smiles."

`global_prompt` anchors what's constant (the character, the place, the style);
the per-segment `local_prompts` drive what *changes over time*. `epsilon`
controls how hard/soft the transition between segments is. This is what makes it
a "director" — you're directing the action across the clip, not just describing a
single moment. Any UI we build must expose this (per-segment prompt blocks on the
timeline), or we lose the main value.

---

## 3. Corrections / refinements to the plan

Your understanding is ~90% right. Refinements:

- **Audio = conditioning, not just muxing.** Turning on custom audio makes the
  *video react to / sync with* the audio (lip-sync, beat timing) — the model is
  conditioned on it. So "use the section's audio by default" is great for a music
  video: load the scene's audio slice as the timeline audio so the motion syncs.
  Uploading replaces it (e.g. an isolated vocal for tighter lip-sync). If custom
  audio is OFF, LTX 2.3 generates its own audio. (This is separate from the app's
  existing final-export audio mux, which still applies.)
- **Scene length:** the timeline duration should be the scene's **actual duration**
  (`end_time − start_time`), clamped to the project's min/max video-duration
  settings. One caveat: a single LTX generation has a practical max length; very
  long scenes still need the app's V2V-extend/split. So Director Mode targets one
  scene-length generation; multi-pass extension stays in the existing pipeline.
- **Previous-scene continuation = a keyframe at `start=0`.** "Use the previous
  scene's last frame to continue" maps exactly to placing that image as the
  **opening keyframe** of this scene's timeline (and you can add middle/last
  keyframes for a shot sequence). Keyframes carry a **position** (which frame) and
  a **strength** (how strongly they pin the picture) — both editable.
- **Image inputs = keyframes (guides), any number.** Assets, freshly-created
  assets, character refs, and previous-scene frames are all just images dropped on
  the timeline at a chosen frame, with a strength. This replaces the FF/LF picker
  while Director Mode is on.
- **Video editing / extension** (load a clip, trim/split/extend with
  prompts+keyframes+audio) is real but is a **bigger, separate workflow** than the
  app's per-scene "generate one clip" model. Recommend phasing it after the
  generative timeline lands (it overlaps the app's existing V2V-extend).

---

## 4. How it maps into this app

This mirrors the proven Krea2 / Ideogram pattern: the user supplies a tested
ComfyUI workflow that contains the node; we populate the node's widgets from a
per-scene config; the dispatcher routes to it; Generate adds it to the batch queue.

- **Per-scene config** (`scene.parameters.ltx_director`): timeline state —
  segments (text prompts + lengths), keyframes (asset_id/path + frame position +
  strength + resize), audio (use scene audio | uploaded asset + trims), global
  prompt, epsilon, fps, dims. Saved on every edit so the editor reopens to the
  same state.
- **Backend**: `prepare_ltx_director_workflow(workflow_path, director_cfg, scene, …)`
  populates the `LTXDirector` node — builds `timeline_data` JSON (segments +
  audioSegments with imageB64/audioB64 or imageFile/audioFile from our assets),
  plus `global_prompt`, `local_prompts`, `segment_lengths`, `guide_strength`,
  `duration_frames`, `use_custom_audio`, dims, fps. Resolve our asset rel_paths →
  files for the node (base64 or copy into ComfyUI input dir).
- **Dispatch**: a new `workflow_type` (e.g. `ltx_director`) routed to
  `LTX_DIRECTOR.json`, gated on the file existing (falls back if absent). Reuses
  the existing LTX model/gguf/lora settings, audio mux, and AV-native plumbing.
- **Batch / Generate**: the video tab's Generate (in Director Mode) builds the
  director job and enqueues it exactly like a normal video job.

---

## 5. Frontend — full-screen timeline editor (the big piece)

- **Video tab toggle**: "Enable LTX Director Mode" → greys out the normal video
  controls (camera action, FF/LF, single video prompt) and reveals an **"Open
  Director" button**.
- **Full-screen, resizable editor** (its own overlay, not embedded): a horizontal
  **timeline ruler** (scene duration, frames/seconds toggle) with lanes for:
  1. **Prompt segments** — draggable/resizable blocks, each with a prompt (Prompt Relay).
  2. **Keyframe images** — drop assets / create asset / pick previous-scene frame;
     each pinned at a frame with a strength slider + resize/compression.
  3. **Audio** — default = the scene's audio slice (auto-loaded), or upload/replace;
     trim/position; the custom-audio toggle.
  4. **Global prompt** + **epsilon/transition** + **dims/fps** controls.
- **Save on change** → `scene.parameters.ltx_director`; reopen restores it.
- **Generate** → enqueue to the batch and run like normal.

---

## 6. Suggested phasing

1. **Backend scaffold** — config schema on the scene, `prepare_ltx_director_workflow`,
   dispatch routing (gated on the workflow file), Generate→batch. (User supplies a
   tested `LTX_DIRECTOR.json`.)
2. **Editor MVP** — timeline with **prompt-relay segments + keyframe images
   (assets / prev-scene frame) + scene-audio default**; save/reopen; Generate.
3. **Full parity** — per-keyframe strength/resize/compression, epsilon UI, custom
   audio upload+trim lanes, frames/seconds, multi-audio compositing.
4. **Video input / editing** — load/trim/extend an existing scene clip (overlaps
   the existing V2V-extend; design alongside it).

---

## Open scope questions (answer before Phase 1)
- Will you supply a **tested `LTX_DIRECTOR.json`** workflow (node installed), like
  you did for Krea2/Ideogram, for me to populate? (Strongly recommended.)
- **Audio default:** condition the video on the scene's audio by default
  (lip-sync/sync), with upload-to-override? (Recommended.)
- **Video-editing/extension**: include now, or land the generative timeline first?

---

## 7. v2.0.0 node reality (from the example workflow) — updates the above

Fetching the actual `LTX_Director_2_Workflow_Hotfix.json` showed the shipped node
is **v2.0.0** and richer than the source skim implied. Corrections/additions:

**Three timeline tracks, not one.** The node's `timeline_data` carries
`segments` (main: image keyframes + text/prompt-relay), **`motionSegments`**
(a motion track), and `audioSegments`. Matching toggles: `mainTrackEnabled`,
`motionTrackEnabled`, `audioTrackEnabled`, plus `use_custom_motion`,
`use_custom_audio`, `inpaint_audio`, `override_audio`.

**The "editing capabilities" you remembered = Retake mode.** `timeline_data`
has `retakeMode`, `retakeVideo`, `retakeStart`, `retakeLength`, `retakePrompt`,
`retakeStrength`, `retake_global_prompt`. This loads an existing video and
re-generates a chosen span (start+length) with a new prompt/strength — i.e.
in-place editing / partial re-roll / extension of a clip. This is our Phase 4 and
overlaps the app's existing V2V-extend.

**New outputs + two companion nodes.** The node now also outputs
`motion_guide_data` (MOTION_GUIDE_DATA). Downstream it pairs with:
- **`LTXDirectorGuide`** — injects the keyframe images + motion guides into the
  latent/conditioning (this is where guide strengths actually apply), and
- **`LTXDirectorCropGuides`** — crops the guide regions back out after sampling.
The example wires a full **two-stage LTX 2.3 pipeline**: Stage 1 (base sample) →
Stage 2 (LTXVLatentUpsampler 2× spatial upscale + refine) → AV-latent
separate → video VAE decode + audio VAE decode → CreateVideo. Model stack in the
example is the newer **22B distilled fp8** (`UNETLoader` + `DualCLIPLoader` gemma
+ tiny `VAELoaderKJ` for preview + separate video/audio VAEs).

**Two LTXDirector node generations exist in our app already.** Our current
`prepare_sequencer_workflow` (`ltx_seq_*`, `LTX_SEQ_*.json`) drives an **older v1
LTXDirector** whose inputs are `text`, `negative_prompt`, `audio_guidance`,
`stitch`, `image_description`, `segments`. The **v2.0.0** node's inputs are
different: `global_prompt`, `local_prompts` (`|`-sep), `segment_lengths`,
`guide_strength` (comma-sep), `timeline_data`, `use_custom_audio`, `epsilon`,
`duration_frames`/`duration_seconds`, `custom_width/height`, `resize_method`,
`divisible_by`, `img_compression`. So Director Mode targets a **new** workflow +
prepare fn; it does not reuse the v1 sequencer path.

## 8. Concrete integration map (this app)

- **Workflow file:** `workflows/LTX_DIRECTOR.json` (API format, like every other
  workflow here). Faithfully converted from the v2.0.0 example. **Draft — needs
  validation in your ComfyUI and model-filename alignment** (the example's
  `ltx-2.3-22b-distilled…fp8`, `gemma_3_12B…`, `taeltx2_3`, audio/video VAE names
  must match what's installed; easiest is to re-export "Save (API Format)" from
  your working graph and drop it in).
- **workflow_type:** `ltx_director` → added to `dispatcher.py` `ltx_map`, gated on
  the file existing (falls back to normal video if absent).
- **prepare fn:** `prepare_ltx_director_workflow(...)` in
  `services/comfyui/workflow.py` — mutates the v2.0.0 `LTXDirector` node by title:
  `timeline_data` (segments+motion+audio), `global_prompt`, `local_prompts`,
  `segment_lengths`, `guide_strength`, `use_custom_audio`, `epsilon`,
  `duration_frames`/`duration_seconds`, dims, fps. Robust (warns + continues if a
  node/field is missing).
- **Per-scene state:** `scene.parameters.ltx_director` — a JSON blob that *is* the
  editor's timeline_data plus our resolution metadata (keyframe `asset_id`s,
  audio source = scene-slice|asset, global prompt, epsilon, fps, dims, track
  toggles, retake config). No DB migration (scene.parameters is already JSON).
- **Asset plumbing:** keyframe `asset_id`s and the audio asset are uploaded to the
  ComfyUI input dir at dispatch (existing `_upload_workflow_files` path); their
  basenames go into `timeline_data` as `imageFile`/`audioFile`. Scene-audio
  default = the project audio sliced to the scene's `[start,end]`.
- **Generate:** the Video tab's Generate (Director Mode on) enqueues an
  `ltx_director` job to the batch like any other video job.

## 9. Revised phasing (with v2.0.0)

1. **Backend scaffold (this pass):** `LTX_DIRECTOR.json` draft, `prepare_ltx_director_workflow`,
   `ltx_director` dispatch route (file-gated), `scene.parameters.ltx_director` schema.
2. **Editor MVP:** full-screen timeline — main track (prompt-relay text segments +
   image keyframes incl. previous-scene frame) + scene-audio default; save/reopen; Generate.
3. **Full parity:** per-keyframe strength/resize/compression, epsilon UI, custom
   audio upload+trim, frames/seconds, **motion track**.
4. **Retake / editing:** load an existing scene clip + re-generate a span
   (retakeMode), aligned with the existing V2V-extend.

---

## 10. VALIDATED against a real API export (no longer a draft)

You provided a tested **API-format export** (`LTX_DIRECTOR_apiexp.json`) built on
our stack. It matched the authored draft almost exactly — same 23-node
single-stage graph, same GGUF/LoRA/CLIP/VAE/`VHS_VideoCombine`. `workflows/LTX_DIRECTOR.json`
is now **that validated export**. Two things it corrected:

- **`global_prompt` is NOT a node widget.** It lives inside `timeline_data`
  (`{"global_prompt": "..."}`). The prepare fn now folds the param into
  `timeline_data` instead of setting a widget. (Node 130's confirmed widget set:
  start/end/duration second+frame, `timeline_data`, `local_prompts`,
  `segment_lengths`, `epsilon`, `guide_strength`, `use_custom_audio`,
  `use_custom_motion`, `inpaint_audio`, `frame_rate`, `display_mode`,
  `custom_width/height`, `resize_method`, `divisible_by`, `img_compression`,
  `override_audio`, `timeline_ui` + the `model`/`clip`/`audio_vae` links.)
- **`LTXDirectorGuide`'s real widgets** are `ic_lora_name`, `ic_lora_strength`,
  `scale_by`, `upscale_method`, `image_attention_strength`, `crop`,
  `auto_snap_ic_grid`, `use_tiled_encode`, `tile_size`, `tile_overlap`,
  `retake_mode` — not the `strength`/`interpolation` I'd guessed. The validated
  workflow carries these; the prepare fn doesn't touch this node, so it's correct.
  Notable: it supports an **IC-LoRA** (in-context image LoRA) and a
  `retake_mode` flag that pairs with the timeline's `retakeMode` for editing.

Verified: the prepared `LTXDirector` inputs are a strict **subset** of the
validated export's keys (no unexpected widget), `global_prompt` folds into
`timeline_data`, Prompt-Relay strings + keyframe/audio resolution + GGUF/LoRA/seed
all populate. Backend scaffold is now wired to a known-good graph.

### Community reference: `LTX2.3 DIRECTOR GGUF 12GB.json`
A low-VRAM **two-stage** build (42 nodes): adds `LTXVLatentUpsampler` +
`LatentUpscaleModelLoader` (2× spatial upscale), `VAEDecodeTiled` +
`LTXVChunkFeedForward` (low-VRAM decode), duplicate guide/sampler/crop stages, and
`LTXVCropGuides` (core) + `CreateVideo`/`SaveVideo` output. Good blueprint for a
future **"High quality / low-VRAM" toggle** (Phase 3+), but our single-stage graph
is the default. Not needed for the current scaffold.


---

## 11. SHIPPED (1.12.0) — frontend timeline editor

The full feature is implemented end-to-end (tsc clean; full frontend→dispatch→
workflow simulation passes 16/16 assertions).

- **`frontend/.../SceneEditor/LTXDirectorModal.tsx`** — full-screen, zoomable
  timeline editor. Lanes: Prompt Relay (drag/resize text segments), Keyframes
  (image guides from assets/uploads/previous-scene last frame, drag + strength),
  Audio (scene default / pick / upload + custom-audio toggle), Motion (advanced).
  Global prompt, epsilon, CRF, resize, output-size pin. Frames/seconds + zoom.
  Reuses `useAssetPicker` (upload-or-choose) for image/audio/motion. Autosaves
  (debounced) to `scene.parameters.ltx_director`; Generate persists then enqueues.
- **`SceneEditor.tsx`** — "Enable LTX Director Mode" toggle near the top of the
  Video tab greys out the normal video controls (`opacity-40 pointer-events-none`)
  and reveals "Open Director Timeline" + inline Generate. `generateVideoMutation`
  skips the start-image pre-flight and forces `workflow_type='ltx_director'` when
  the scene has Director Mode on (and never sends a `workflow_config_id` then).
- **Types** — `LtxDirectorConfig` + segment/motion/retake types in `types/index.ts`;
  `'ltx_director'` added to `VideoWorkflowType`.
- **Dispatcher** — now prefers `cfg.duration_seconds` (the editor's scene-duration)
  for the timeline frame count so generation matches what was edited.

Field contract (editor → dispatch) verified: image segs carry `asset_id`
(resolved to `imageFile`) or `imageFile` (previous-scene `rel_path`); text segs
carry `prompt`; `local_prompts`/`segment_lengths`/`guide_strength` are derived in
order; audio defaults to the scene slice unless `audio_source==='asset'`.

**Not yet built (future):** Retake/editing UI (data contract + backend passthrough
exist), and a high-quality/low-VRAM two-stage workflow toggle (community ref).

---

## 12. SHIPPED (1.12.1) — Retake/editing + High-Quality two-stage

Both previously-deferred phases are now implemented and verified (tsc clean;
prepare/HQ/retake simulation passes).

**Retake / in-place editing.** The editor has a "Retake / edit an existing clip"
panel: enable it, choose a source video (this scene's current video, or pick /
upload one), set the span (`start` + `length` with a bar preview), a retake
prompt, and strength. Saved to `scene.parameters.ltx_director.retake`. At dispatch
this fills the node's `retakeMode` / `retakeVideo` / `retakeStart` / `retakeLength`
/ `retakePrompt` / `retakeStrength` in `timeline_data`, flips
`LTXDirectorGuide.retake_mode` (new `retake_mode` arg on
`prepare_ltx_director_workflow`), and the source video is uploaded with the other
timeline files. Retake only triggers when a source video is set (safe no-op
otherwise).

**Quality toggle (two-stage HQ).** A per-scene Standard / **High (2× upscale)**
selector → `scene.parameters.ltx_director.quality`. When `hq`, the dispatcher
routes to **`workflows/LTX_DIRECTOR_HQ.json`** — the same single-stage graph plus
a Stage 2: `LTXVLatentUpsampler` (2× spatial, `ltx-2.3-spatial-upscaler-x2-1.1`)
→ a second `LTXDirectorGuide` + `CFGGuider` + refine `SamplerCustomAdvanced`
(4 steps, denoise 0.42) → `LTXDirectorCropGuides` → **`VAEDecodeTiled`** (low-VRAM)
→ VHS. 33 nodes, built on our stack with the validated `LTXDirectorGuide` widget
names; `prepare_ltx_director_workflow` populates it unchanged (same node titles).
Falls back to single-stage if the HQ file is absent.

> `LTX_DIRECTOR_HQ.json` is a **draft** like the original — validate in your
> ComfyUI. The only nodes not in your validated single-stage export are
> `LTXVLatentUpsampler` (no widgets), `LatentUpscaleModelLoader` (`model_name` —
> confirm the upscaler filename), and `VAEDecodeTiled` (`tile_size`/`overlap`/
> `temporal_size`/`temporal_overlap` — comfy-core defaults). Easiest validation:
> load it once, or export your own two-stage and drop it in.


---

## 13. Post-build audit (1.12.1)

Full audit: every backend file compiles (`compileall`), all changed files
AST-parse, frontend `tsc` is clean (strict + no-unused), both director workflow
JSONs validate, and no truncation (all file tails intact). An independent logic
review of the dispatch branch, uploader, prepare fn, and editor flagged three
issues, all fixed + re-verified:

- **Motion guides had no file.** Motion segments carried only `asset_id` and were
  passed through verbatim, so the uploader (which rewrites `videoFile`/`imageFile`/
  `audioFile`) never uploaded them. Fixed: the dispatcher now resolves each motion
  segment's `asset_id` → path and assigns `videoFile` (clips) or `imageFile`
  (stills); a bare `rel_path` becomes `videoFile`.
- **Autosave loop.** The editor's debounced autosave listed the parent's inline
  `onSaveConfig` in its deps; each save re-rendered the parent → new callback
  identity → effect re-fired → save again. Fixed with a ref (`onSaveRef`); effect
  deps are now `[cfg, display, fps]`.
- **"Auto-size from keyframes" was a no-op.** Output dims always fell back to the
  project resolution. Fixed: the editor defaults to *pinned* project dims (so the
  toggle is meaningful), and the dispatcher honors an explicit `0` (auto) while
  only falling back to project dims when the cfg omits the key (e.g. Generate
  without opening the editor).

Reviewed clean (no change needed): HQ workflow selection, retake passthrough +
`retake_mode`, `global_prompt` folding, `_try` node tolerance, the generate path
(`directorOn` skips the start-image guard + forces `workflow_type`), the greyed
wrapper, and image/prev-frame resolution.


---

## 14. Deep integration pass (1.12.1)

An independent sweep of subsystems we didn't directly modify confirmed the
feature is clean across: video-job completion / post-processing (workflow-agnostic,
flag-gated by `skip_audio_mux`), final export / assembly (treats the Director
output as any video), workflow registry + capability/model maps (`ltx_director`
registered, both files discovered, graceful fallback if absent), and **no
regression** to normal (non-Director) video generation (the greyed wrapper and
`directorOn` logic are inert when Director Mode is off).

One real gap was found and fixed: **project text export/import** serialized only a
whitelist of scene fields, so `scene.parameters.ltx_director` (and the session's
other per-scene configs) were dropped on a round-trip. Fixed with a per-scene
`advanced_params` passthrough in `backend/services/project_text_io.py` (export +
import), preserving Director Mode, LLM instructions, and vision / JSON-prompt
toggles. Verified by a round-trip simulation.

Known/intended limitation: **auto-gen / batch** video generation ignores Director
Mode (produces a standard LTX clip); Director Mode is a deliberate per-scene manual
override.
