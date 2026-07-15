# Character Studio — Phase 2 API Contract

Companion to `docs/CHARACTER_STUDIO.md` (Phase 1 design). This document
covers only the **P2 additions**: Pose Studio, Costumes, Emotions,
Process (cutout/upscale), Generate-All orchestration, Preflight, and the
two LLM Wizards. All Phase 1 endpoints (`stories`, `characters` CRUD,
`generate-base`, `generate-shots`, `status`, `datasets`, `push-to-project`)
are unchanged and still live at the same paths.

Base path for every route below: `/api/character-studio`.

All P2 generation endpoints follow the same async-job pattern as Phase 1:
the endpoint creates one or more `Job` rows in the hidden Character Studio
project and returns immediately with `job_id`(s); the frontend polls
`GET /characters/{id}/status` (extended in P2 — see below) until the
relevant manifest entry's `status` becomes `"done"` or `"failed"`.

## Engine parameter (all generation endpoints)

Every P2 generation endpoint takes `engine: "auto" | "qwen" | "klein"`:

- `"qwen"` — VNCCS-quality Qwen-Image-Edit-2511 graphs. Requires a ComfyUI
  worker currently advertising the `vnccs` capability (auto-detected when
  the worker exposes the `VNCCS_QWEN_Encoder` node). If none is online and
  the caller explicitly asked for `qwen`, the endpoint returns **HTTP 409**
  with an actionable `detail` message — the frontend should surface this
  and suggest `engine="klein"` or starting a VNCCS worker.
- `"klein"` — existing Klein edit-ref workflows. Always available (no
  extra worker deps), lower fidelity for pose-exact/clothes-swap edits.
- `"auto"` (default) — picks `"qwen"` if a vnccs worker is online, else
  falls back to `"klein"` silently (no error).

The resolved engine is always echoed back in the response as `"engine"` so
the UI can show which path actually ran.

---

## 1. Pose Studio

### `GET /pose-presets`
List bundled pose presets (rendered from `backend/data/character_studio/pose_presets.json`,
a 12-pose 2D-skeleton catalog: standing neutral, walking, sitting, arms
crossed, waving, action pose, hands on hips, looking over shoulder,
reaching up, relaxed lean, pointing, cheerful pose).

**Response:** `{"presets": [{"id": "pose_1", "name": "Standing neutral"}, ...]}`

### `GET /pose-presets/{preset_id}/thumbnail`
Returns a cached ~128px PNG thumbnail (`image/png`). Thumbnails are
rendered lazily on first request and cached under
`<project_dir>/_character_studio_cache/pose_thumbs/`; regenerated only if
the source catalog JSON is newer than the cached file.

### `POST /characters/{id}/poses/generate`
**Body:**
```json
{"preset_ids": ["pose_1", "pose_5"], "engine": "auto"}
```
Renders each requested skeleton preset to a control-image PNG, registers it
as an Asset, then dispatches one generation job per pose (pose-conditioned
render of the character in that pose). Requires the character to already
have a base render (400 otherwise).

**Response:**
```json
{"created": ["pose_1", "pose_5"], "errors": [], "engine": "qwen"}
```
`errors` is a list of `"<preset_id>: <reason>"` strings for any preset that
failed to queue (unknown id, render failure) — a partial success is normal,
not an exception.

**Manifest shape** (`character.manifest["pose_sets"]`):
```json
{
  "pose_1": {
    "status": "pending|running|done|failed",
    "job_id": "uuid",
    "engine": "qwen",
    "pose_asset_id": "uuid",
    "name": "Standing neutral",
    "image_rel": "relative/path.png",   // present once done
    "asset_id": "uuid",                  // present once done
    "error": "..."                        // present once failed
  }
}
```

---

## 2. Costumes

Costumes are stored directly on the character's manifest (no separate DB
table — same "manifest JSON is the source of truth" pattern as
`shot_plan`/`shots` in Phase 1).

### `POST /characters/{id}/costumes`
**Body:** `{"name": "Winter coat", "fields": {"top": "...", "bottom": "...", "head": "...", "face": "...", "shoes": "..."}, "prompt": "optional free-text addendum"}`

**Response:** `{"id": "<costume_id>", "costume": {...}}`

### `PATCH /characters/{id}/costumes/{costume_id}`
Same body shape as create; partial updates merge onto the existing entry.

### `DELETE /characters/{id}/costumes/{costume_id}`

### `POST /characters/{id}/costumes/{costume_id}/generate`
**Body:** `{"engine": "auto"}`

Builds the costume prompt from `fields` + `prompt`, dispatches a single
job against the character's base render (identity). Requires a base render.

**Response:** `{"job_id": "uuid", "engine": "klein"}`

**Manifest shape** (`character.manifest["costumes"]`):
```json
{
  "<costume_id>": {
    "id": "<costume_id>",
    "name": "Winter coat",
    "fields": {"top": "...", "bottom": "...", "head": "...", "face": "...", "shoes": "..."},
    "prompt": "...",
    "sprites": {
      "base": {
        "status": "pending|running|done|failed",
        "job_id": "uuid",
        "engine": "qwen",
        "image_rel": "...",   // once done
        "asset_id": "uuid",   // once done
        "error": "..."         // once failed
      }
    }
  }
}
```
(Only one sprite key, `"base"`, exists in this P2 build — the costume is
rendered once, on the base identity. Multi-angle costume sprites are a
natural P3 extension using the same `sprites` dict with more keys.)

---

## 3. Emotions

### `GET /catalogs`
(Existing Phase 1 endpoint — already returns `emotions` from
`backend/data/character_studio/emotions.json`.) The emotion catalog is a
dict of `{category_label: [{"key", "description", "safe_name", "natural_prompt"}, ...]}`
with 8 categories / ~150 entries. **`safe_name`** is the identifier to pass
to `/emotions/generate`.

### `POST /characters/{id}/emotions/generate`
**Body:**
```json
{
  "emotions": ["angry", "smile", "surprised"],
  "costume_id": null,
  "source": "base",
  "engine": "auto"
}
```
- `source`: `"base"` (default) or a shot id / costume sprite key
  (`costume_id` selects which costume's sprite to source from when
  `source` refers to a costume-produced image).
- For `engine="klein"`, each emotion additionally requires face detection
  to succeed on the source image (builds a face-masked RGBA for
  `klein_inpaint`). If no face is detected, that emotion is skipped and
  reported in `errors` — **it does not abort the batch**.

**Response:** `{"created": ["angry", "smile"], "errors": ["surprised: no face detected..."], "engine": "klein"}`

**Manifest shape** (`character.manifest["emotions"]`):
```json
{
  "angry": {
    "status": "pending|running|done|failed",
    "job_id": "uuid",
    "engine": "klein",
    "source": "base",
    "costume_id": null,
    "image_rel": "...",         // once done
    "asset_id": "uuid",         // once done
    "face_crop_rel": "...",     // once done — auto-cropped face-only image
    "error": "..."               // once failed
  }
}
```
The face crop (`face_crop_rel`) is produced as a **side effect** of the
`/status` reconciler (and the generate-all orchestrator) the first time it
observes the emotion job transition to `done` — no extra call needed.

---

## 4. Process (cutout / upscale)

### `POST /characters/{id}/process`
**Body:**
```json
{
  "image_refs": ["base", "three_quarter_l", "costume:<id>", "emotion:angry"],
  "steps": {"cutout": true, "upscale": false},
  "engine": "auto"
}
```
`image_refs` accepts: `"base"`, any Phase-1 shot id, `"costume:<costume_id>"`
(resolves to that costume's `sprites.base`), or `"emotion:<key>"` (resolves
to that emotion's full render).

Behavior per step, per ref:
- **cutout**: if a `vnccs`-capable worker is online, dispatches a
  `studio_rmbg2` job (async — poll via `/status`, see below). If none is
  online, runs a **synchronous CPU fallback** (`rembg` if installed, else a
  crude corner-sampled chroma-distance cutout — always produces *some*
  RGBA result) and returns the result **inline** in the same response.
- **upscale**: only has a worker path (`studio_upscale`) — **no CPU
  fallback exists**. If no upscale-capable worker is online, that ref/step
  fails with an explanatory error; it does not block other refs/steps.

**Response:**
```json
{
  "jobs": ["uuid-of-async-job", ...],
  "inline_results": [{"ref": "base", "step": "cutout", "asset_id": "uuid"}],
  "errors": ["emotion:angry: cutout failed — ...", "base: upscale unavailable — no worker online (no CPU fallback)"]
}
```

**Manifest shape** (`character.manifest["processed"]`), keyed by the same
`image_refs` strings:
```json
{
  "base": {
    "cutout": {"status": "done", "image_rel": "...", "asset_id": "uuid", "method": "cpu_fallback", "note": "..."},
    "upscale": {"status": "failed", "error": "..."}
  }
}
```
For async (worker) jobs the entry starts as `{"status": "pending", "job_id": "uuid"}`
and is completed by the `/status` reconciler exactly like every other P2
stage.

---

## 5. Generate-All orchestrator

### `POST /characters/{id}/generate-all`
**Body:**
```json
{
  "engine": "auto",
  "include": {
    "shots": true,
    "costume_ids": ["<id1>", "<id2>"],
    "emotions": ["angry", "smile"],
    "cutout": false,
    "upscale": false
  }
}
```
Validates engine availability up front (409 if `engine="qwen"` requested
and unavailable), then immediately returns `{"ok": true, "status": "running"}`
and runs the pipeline as a background `asyncio.Task`:

**Stages, in order:** `base` (only if missing) → `shots` (if
`include.shots`) → `costumes` (for each id in `include.costume_ids`) →
`emotions` (for each key in `include.emotions`) → `process` (cutout/
upscale per `include.cutout`/`include.upscale`, applied to the base image).

Each stage/item runs sequentially and is polled internally (own 600s
per-image timeout, 5s interval) via a self-contained poller
(`_poll_job` in `character_studio.py` — no shared batch-pipeline poller
existed to reuse). **A failure in any single item never aborts the run** —
the exception (or timeout) is caught, appended to
`manifest["generate_all"]["errors"]`, and the loop continues to the next
item/stage.

**Manifest shape** (`character.manifest["generate_all"]`) — poll this via
`GET /characters/{id}/status`:
```json
{
  "status": "running|done|failed",
  "stage": "base|shots|costumes|emotions|process|done|queued",
  "errors": ["shots/profile_l: timed out after 600.0s", "emotion/angry: no face detected — skipped"]
}
```
The frontend should poll `/status` every few seconds while `status` is
`"running"` and stop once it is `"done"` or `"failed"`; `errors` accumulates
across the whole run and should be shown as a non-blocking warning list
even when the overall run finishes `"done"` (partial success is the
expected/normal outcome for a large batch).

---

## 6. Preflight

### `GET /characters/{id}/preflight?engine=auto`
Read-only checks before a UI enables the generate-all button / a stage
button. Never mutates state.

**Response:**
```json
{
  "ok": true,
  "engine_resolved": "qwen",
  "warnings": [
    "No base render yet — pose/costume/emotion stages need it first.",
    "Ollama vision is not fully configured (Settings → Vision) — dataset captioning and clone-from-image wizard will be degraded/unavailable.",
    "No upscale-capable worker online — upscale stage will be unavailable."
  ]
}
```
`ok=false` only when the requested `engine` is explicitly unavailable
(mirrors the 409 case elsewhere) — all other issues surface as
non-blocking `warnings`.

---

## 7. Wizards

### `POST /wizards/character`
**Body:** `{"description": "a stoic elf ranger with silver hair"}`

Calls the local Ollama text model (Settings → LLM: `ollama_urls` +
`ollama_model`) with a JSON-only system prompt adapted from the vendored
VNCCS `character_wizard` prompt (`vnccs/nodes/character_creator_v2.py`),
seeded with the bundled tag catalog so it prefers existing tags. Returns
409 if Ollama isn't configured.

**Response:**
```json
{
  "character_info": {
    "sex": "female", "age": 132, "race": "elf", "skin_color": "",
    "body": "slim build", "face": "sharp features",
    "hair": "silver_hair, long_hair", "eyes": "grey_eyes",
    "additional_details": "stoic expression, ranger gear references removed per no-clothing rule"
  }
}
```
This dict is a drop-in for `CharacterIn.character_info` (Phase 1's create/
update character body) or for merging into an existing character's
`character_info` field.

### `POST /wizards/clone`
**Body:** `{"asset_id": "<uuid>"}` — the asset must already exist (upload it
first via the normal `POST /api/projects/{project_id}/assets/upload`
endpoint against the **Character Studio project** — see
`GET /characters` response's `studio_project_id` field — then pass the
returned asset id here). This mirrors the existing `klein_inpaint`
mask-upload convention (pre-upload, then reference by id) rather than
accepting multipart directly on this endpoint.

Pipeline: Ollama **vision** model (Settings → Vision: `ollama_vision_model`)
describes the image factually (reusing
`backend.services.llm.vision.caption_image_sync` +
`DESCRIBE_PROMPT` — the same describer used for Phase 1 reference-image
descriptions) → that description is fed into the same JSON-extraction
prompt as `/wizards/character`. Returns 409 if vision or text Ollama isn't
configured, 502 if the vision/JSON step fails.

**Response:** `{"character_info": {...same shape as above...}, "vision_description": "..."}`

---

## Extended `/status` response (P2 fields)

`GET /characters/{id}/status` (Phase 1 endpoint, extended in place) now
also returns:

```json
{
  "base": {"image_rel": "...", "asset_id": "..."},
  "shots": {...},                 // unchanged from Phase 1
  "shot_plan": [...],             // unchanged from Phase 1
  "pose_sets": {...},             // see section 1
  "costumes": {...},              // see section 2
  "emotions": {...},              // see section 3
  "processed": {...},             // see section 4
  "generate_all": {...},          // see section 5
  "studio_project_id": "..."
}
```
All P2 job reconciliation (pending→running→done/failed, `image_rel`/
`asset_id` population, emotion face-crop side effect) happens inside this
one endpoint call, exactly like Phase 1's shot reconciliation — the
frontend only needs ONE poll loop per character screen.

---

## Dataset image selection (extended `include` semantics)

Phase 1's `POST /characters/{id}/datasets` `include: list[str] | null` now
additionally accepts these key namespaces (in addition to `"base"` and
raw shot ids):

- `"costume:<costume_id>"` — that costume's base sprite
- `"emotion:<emotion_key>"` — the full emotion render
- `"emotion_face:<emotion_key>"` — the cropped face-only image for that emotion

`include=null` (the default) still means "base + all Phase-1 shots only" —
P2 sprite/emotion images are **opt-in** for datasets (most LoRA datasets
don't want every costume/emotion variant mixed in automatically).

---

## Error handling summary

Every P2 generation endpoint validates engine availability and required
inputs (base render present, valid ids, face-detection success for
klein-engine emotions) **before** creating any Job row, raising
`HTTPException(400)` for bad input or `HTTPException(409)` for
engine-unavailable — with an actionable message naming the missing
capability/config and the suggested fix (e.g. "Use engine='klein' or start
a VNCCS-capable ComfyUI worker."). The `generate-all` orchestrator is the
one exception that intentionally swallows per-item/per-stage errors (by
design — see section 5) so one bad emotion/costume doesn't kill an
otherwise-successful batch.


---

## Addendum — v1.29.0 → v1.30.0 additions (post-audit; this section supersedes conflicts above)

- Pose presets: `GET /pose-presets` now includes custom presets (`{id:"custom_*", name, custom:true}`);
  `GET /pose-presets/joints/{id}` → `{id,name,joints}`; `POST /pose-presets/preview {joints}` → PNG;
  `POST /pose-presets/custom {name,joints}` / `DELETE /pose-presets/custom/{id}`. Custom ids are valid
  in `poses/generate.preset_ids`.
- `GET /catalogs` now returns `outfits: [{name, content}]` (629 aesthetics).
- `ProcessIn.upscale_mode: 'auto'|'seedvr2'|'gan'`; generate-all `include.upscale_mode` same values.
- Preflight response gained `seedvr2_online`, `gan_upscale_online`, `facedetailer_online`.
- Emotions: `engine` additionally accepts `'facedetailer'` (409 with actionable message when the
  worker lacks `impact`+`vnccs` caps). Canonical `source` for costume-based emotions is `'base'`
  with `costume_id` set (the costume id as `source` is tolerated).
- `/process` refs additionally accept `costume:<id>` and `emotion:<key>` prefixed forms.
- Emotion entries now include `face_crop_asset_id` (thumbnail-able via the assets file route).
- Audit fixes contract note: characters pushed to projects create Asset rows for all copied images.

## Addendum — v1.30.1 → v1.33.0 additions (supersedes conflicts above)

**Identity / style**
- `GET /styles` → `{ default, styles:[{value,label}] }` — canonical art-style presets. Custom
  free-text style values are accepted everywhere (used verbatim as the descriptor).
- `character_info.style` holds the art style; it threads into the base prompt, wizard system prompt,
  and caption subject. `POST /wizards/character` and `POST /wizards/clone` now accept optional `style`.
- Story `POST /stories` / `PATCH /stories/{id}` accept `default_style`; `GET /stories` returns it.
  New characters inherit the story's `default_style` when `character_info.style` is unset.
- **`PATCH /characters/{id}` now returns the full character object** (was `{ok:true}` — that wiped
  the edit form; fixed 1.31.1).

**Base render**
- `POST /characters/{id}/generate-base` accepts `model` (first-pass override: `z_image_turbo` /
  `krea2_turbo` / `flux2_klein_dev_9b`; empty = Settings default via `single_image_generator`).
- `POST /characters/{id}/set-base { asset_id }` — use an uploaded/existing image AS the base render
  (points the studio scene's `chosen_image_path` at it; NVCCS import style). Upload the file first
  via `POST /api/projects/{studio_project_id}/assets/upload`.
- Extended `/status`: `base` now includes `status` (`idle`/`pending`/`running`/`done`/`failed`) and
  `error`, so the UI shows real progress / failure instead of an endless spinner.

**Poses**
- `GET /pose-presets` entries now include `category` (built-ins = "Basic").
- `POST /pose-presets/import { poseset? , poses? , category }` — bulk-import a VNCCS poseset JSON
  or a flat pose list as categorized custom presets.
- `POST /pose-presets/import-openpose` (multipart: `file` = `.json`/array/`.zip`, `category`) —
  ingest raw **OpenPose keypoint** files (BODY_25 / COCO-18 auto-detected) → VNCCS 18-joint presets
  scaled to 512×1536.
- Pose CONTROL images now render as the colored **OpenPose skeleton on black**; the mannequin
  schematic is thumbnail-only.
- **Klein pose transfer** uses the RefControl Pose LoRA when `app_settings.cs_klein_pose_lora` is set
  (default `refcontrol_v2_poses.safetensors`): image 1 = skeleton, image 2 = identity, trigger
  `apply pose from image 1 with reference from image 2`. Empty setting → weak 2-ref fallback.

**Preflight / engines**
- `GET /characters/{id}/preflight` now also returns `klein_online`, `qwen_online`, `impact_online`
  (plus the existing `seedvr2_online`, `gan_upscale_online`, `facedetailer_online`). The detail
  header renders explicit Klein / Qwen (VNCCS) / FaceDetailer chips.

**Emotions**
- Klein emotions no longer hard-fail when no face is detected (anime/stylized): a heuristic
  upper-center face region is used for the `klein_inpaint` mask. Qwen still recommended.
- Emotions-from-costume resolves the source via the sprite's `asset_id` first (then `image_rel`),
  so a rendered costume is reliably found.
