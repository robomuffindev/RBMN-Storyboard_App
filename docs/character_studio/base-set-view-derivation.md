# Base-set view derivation — Klein turnaround LoRA + Qwen mesh turnaround

How the VNCCS Klein character studio turns a small set of references (typically a
face, a front, and maybe one side) into a consistent multi-view / mesh-ready base
set, and the options for improving the hardest part: rotating the character to
right / left / back views.

## TL;DR — the working mesh path (as of v1.198): 🧊 Qwen Mesh turnaround

The path Lorenzo validated ("did a great job") is: clone/create a base in **🟣 Qwen
(VNCCS) mode**, then in the pose picker click **🧊 Mesh turnaround** and generate.
That produces clean front/right/left/back A-pose views — the input a 3D mesh wants —
because Qwen renders side/back poses well.

- Frontend `addMeshTurnaround()` clones default pose 0 for valid bone NAMES but
  ZEROES every bone rotation → the mannequin's neutral rest A-pose (no library
  stance, arms clear), and sets per-view `modelRotation` (front 0, right 90, left
  -90, back 180). It replaces the whole selection with those 4 poses.
- The 3D pose renderer (`pose_render.render_pose_captures`) rotates the mannequin by
  `modelRotation` (`_apply_pose` applies the Euler matrix); the Qwen pose set
  (`_qwen_submit` → `build_qwen_pose_set_graph`) transfers the character onto each
  rotated mannequin via the QIE2511 PoseStudio LoRA.
- Gotcha fixed in v1.198: cloning pose 0 WITH its bones made every view strike that
  stance (hand over chest) and lean on the profiles — zeroing the bones fixed both.
- Gotcha fixed in v1.197.2: `_qwen_submit` uploaded pose captures as raw data-URL
  strings → worker `LoadImage` failed. All capture uploads now `decode_capture()`.

The MatchingPose / turnaround-LoRA material below is the Klein-side alternative and
is opt-in / de-prioritised; the Qwen mesh turnaround is the current default path.

## Current approach (Klein / Flux.2 9B)

The base-set runner (`backend/api/vnccs_native.py::_base_set_run`) anchors on the
approved front (or a freshly rendered front), then derives the other views:

- **Tagged real reference photo** (angle chip): if the user tags a side/back photo
  with its angle, that view is a HARD STRIP of the real photo — most accurate.
  (`strip_hard=True` → drops the clothing reference mask so pants/shoes come off.)
- **Rotation (no real photo)**: the view is a reference-EDIT of the front — the
  front is held as a reference latent and the prompt asks for the turned view.
  The front reference is eased (lower strength, earlier release) so the body can
  actually turn, and the negative suppresses "front-facing / same pose".

Rotating a flat front a full 90° is inherently hard; a real tagged photo always
beats a rotation guess.

## Turnaround LoRA slot (v1.188)

A trained character-turnaround / multi-view LoRA can be stacked on the DERIVED
views so they rotate properly instead of copying the front. This is the trained
alternative to hand-easing reference weights.

- Setting keys (studio settings):
  - `klein_base_turnaround_lora` = `on` | `off`
  - `klein_base_turnaround_lora_name` = exact filename (blank = auto-match a file
    whose name contains turnaround / multi-view / sprite / 4-view / rotation)
  - `klein_base_turnaround_lora_strength` = float (default 1.0, clamp 0.1–1.5)
  - `klein_base_turnaround_lora_trigger` = optional activation text prepended to the
    START of the derived-view prompt (e.g. `matchingpose9b`). Blank = triggerless.
- UI: Character Studio → Klein → "Identity & consistency" → **Turnaround LoRA
  (base sets)**.
- Behavior: resolved via `klein_poses.resolve_turnaround_lora()`, stacked in
  `build_klein_refbase_graph` as a `LoraLoaderModelOnly` after the Consistency
  LoRA. Applied ONLY on `_derive` views (right/left/back + mesh A-pose); the front
  anchor is never touched. No-op until the file is present on the worker AND the
  toggle is on.
- Compatibility rule: the LoRA MUST be trained for Klein **9B** (our engine). A
  `FLUX.2-dev` LoRA (e.g. lovis93 Multi-Angles) or a Klein **4B** LoRA (e.g. fal
  spritesheet) will NOT load on 9B — shape mismatch.
- Worker install: drop the `.safetensors` into `ComfyUI/models/loras/` on every
  worker that renders base sets. A plain stacked LoRA needs no extra node; only the
  Zovetry workflow route needs `ComfyUI-EditUtils`.

### Commercial-clean route (chosen) — nhathoangfoto (Jett Huang), Apache 2.0

Klein 9B, commercial-OK. These are POSE-TRANSFER LoRAs, so they help identity-
preserving posing more than a pure front->side turnaround, and they are a TWO-STAGE
workflow, not a pure drop-in:

- `Flux.2-Klein-9B-MatchingPose` — trigger `matchingpose9b` at the start of the
  prompt; strength 0.9–1.1; steps 4 (distill) / 20 (base); guidance 1–4. **Requires
  a mannequin pose reference image** as a second input (real photos leak identity —
  use a clean faceless mannequin).
- `Flux.2-Klein-9B-Mannequin` — Stage 1: turns a real reference into a clean
  faceless mannequin in the target pose, which then feeds MatchingPose in Stage 2.

Status: BUILT as an opt-in base-set derivation method (v1.189). The base-set
control "How to turn the other views" chooses **Reference-edit** (default) or
**🧍 MatchingPose (mannequin)**. Setting key `klein_base_derive_method` =
`reference` | `matchpose`; per-run override via `BaseSetStartIn.derive_method`.

How the matchpose path works (reuses the PROVEN pose pipeline, no new render code):
- The base-set runner's derived views (right/left/back + mesh A-pose) call
  `_matchpose_derive_view`, which builds a neutral rest-stance pose (empty bones)
  at the view's `modelRotation` and runs `_klein_submit` — the same pose path the
  pose sets use — with:
  - the anchor/approved base as the **identity** (body-shape source; lock_base),
  - `mesh3d_pose=False` (v1.189.2) so it uses the **GENERIC mannequin built from the
    character's description ONLY** — never the character's existing rigged 3D clay
    body. Base-set generation is the INPUT that builds the mesh, so it must not
    depend on a previously-generated 3D asset (a stale rig renders a mangled clay
    capture that MatchingPose copies as a warped body),
  - the **MatchingPose LoRA** (resolved by `resolve_matchpose_lora`; its
    `matchingpose9b` trigger is auto-prepended by `_klein_submit`).
- Result: the mannequin/clay supplies the ORIENTATION, the approved base supplies
  the BODY + identity → an identity- and body-preserving rotation, which is the
  whole point of these sets (clean multi-view body → 3D mesh).
- Tagged real side/back photos still take priority (hard-strip). Reference-edit
  remains the default so nothing regresses; matchpose is A/B-selectable per set.
- Regenerate honors the set's method (matchpose re-rotates via the pose path).

Worker requirement: `Maching_Pose_9B_Rank256.safetensors` in `ComfyUI/models/loras/`
— ALREADY present on workers that run pose sets (it's the "MatchingPose (photoreal)"
Pose LoRA). No second LoRA needed: the mannequin/clay comes from PoseStudio, NOT the
nhathoangfoto Mannequin LoRA.

### Alternative (noted, not built) — Zovetry multi-view (Civitai)

Turnaround-specific for Klein 9B but ships as a workflow ZIP that pulls its own
separate 9B LoRA files, needs `ComfyUI-EditUtils`, and carries the FLUX.1 [dev]
NON-COMMERCIAL license. Further from a clean drop-in; keep as a fallback.

## Qwen route (NOT built — documented for later)

Considered and deferred. Revisit if the Klein turnaround LoRA route stalls.

- **Qwen-Image-Edit-2509** (and newer 2511) natively accepts 1–3 reference images
  in one pass ("Image 1/2/3"), so face + front + side is its designed use case.
- **dx8152/Qwen-Edit-2509-Multiple-angles** LoRA (same author as our Consistency
  LoRA): triggerless camera control — up/down/left/right, 45° rotations, top-down.
  Requires base `Qwen/Qwen-Image-Edit-2509`. dx8152 notes consistency was
  "unstable", improved by a Nov 2025 retrain.
- **Trade-off**: Qwen commits to the rotation better but drifts on facial identity
  (an "AI-polish" look) and is reliable only ~1 MP; Flux/Klein holds identity
  better. Flux.2 itself supports many reference images natively (Dev advertises up
  to 10).
- **If pursued**, best as a HYBRID stage, not a replacement: let Qwen 2509 +
  Multiple-angles produce the turned view, then run our existing Flux/PuLID
  face-refine pass to restore the identity Qwen loses. Would be a new alternate
  view-derivation backend behind an engine toggle.

Sources captured 2026-07: Zovetry Civitai multi-view LoRA; dx8152 HF
Qwen-Edit-2509-Multiple-angles; RunComfy qwen-edit-2509 multiple-angles;
MyAIForce Flux Kontext vs Qwen pose-transfer; Diffusion Doodles model rundown.
