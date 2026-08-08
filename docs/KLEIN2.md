# Klein 2.0 — statue-reference posing

**v1.200.0 (2026-08-01).** A separate lane next to the classic Klein pipeline (which is
untouched). Instead of depth/normal control maps, Klein gets two ordinary reference images:

1. **Image 1 — identity at the exact angle.** The character's 3D mesh, fully **textured into a
   "statue"** (TRELLIS.2 multi-view texturing from the real turnaround views), shown in a
   rotatable viewer. You orbit to precisely the angle the shot needs and hit **📸 Use this
   angle** — that snapshot is the identity reference.
2. **Image 2 — the pose, as a picture.** From **Pose Library 2.0**: plain images of poses.
   Generated poses store their **prompt** (view / edit / regenerate them in the library);
   pose photos can be uploaded directly. The default set renders as neutral gray mannequins so
   no clothing/face can bleed into your character.

Then a baked role-assignment prompt: *"the person from image 1, in the exact pose from image 2 …
photorealistic skin and fabric"* (+ optional photoreal front base as image 3 for face likeness,
which also fights the statue-material look). Poses that were impossible in the old rotation-only
system (sitting, crouching) are just images here.

## Where things live

- UI: **Create area → 🚀 Klein 2.0 tab** (Klein page only).
- Backend: `backend/api/klein2.py`, prefix `/api/klein2` (self-contained; classic pipeline untouched).
- Statue: `<project_dir>/mesh3d/<char_id>/statue.glb` + `statue.json` (+ `statue_graph.json`,
  the exact graph sent — for diagnosis).
- Pose library 2.0: `<project_dir>/_libraries/klein2/poses/`.
- Generation batches (incl. the exact refs each run was given): `<project_dir>/_libraries/klein2/_gen/`.

## Preflight — ALWAYS check before spending runs

`GET /api/klein2/health` (browser is fine) reports, per worker: reachability, whether the
TRELLIS.2 nodes are installed, and — when they are — the **exact wiring plan** the statue graph
would use (which texture class, how mesh + views + save get connected). Generation needs a
klein-capable worker (same as the Image Workshop); statue texturing needs the TRELLIS.2 nodes on
at least one worker.

## Worker setup — ONE SCRIPT

Copy `scripts\worker\install_trellis2.bat` + `install_trellis2.py` to the worker's ComfyUI
portable root (the folder with `python_embeded\`) and run the `.bat`. That's the whole
install: node pack (git or ZIP), requirements (open3d auto-skipped where unbuildable), the
right native wheels for that machine's python+torch (nested CUDA sets included),
triton-windows + the python include/libs bundle portable builds lack, the reconviagen
folder fix, and a resumable pre-download/REPAIR of all ~9.6GB of models (TRELLIS.2-4B-FP8,
DINOv3, TRELLIS-image-large decoder). It's idempotent — re-running repairs partial states
(interrupted downloads) and re-verifies everything. `--no-models` skips the model download.
Then restart ComfyUI and check `/api/klein2/health`. Every fix below was discovered on a real
worker and is baked into the script; the details are reference, not steps you perform.

## Worker setup details (reference) — 16GB VRAM edition (researched 2026-08-01)

**Verdict: TRELLIS.2 mesh texturing fits 16GB cards** — texturing loads only ~3.3 GB of
weights in FP8 and never runs the heavy shape-gen/dual-contouring stages. The one killer is
the pack's default `texture_size=4096` (~16 GB for the UV bake alone) — **the backend
auto-clamps it to 2048** and forces FP8 + `low_vram=True` + `backend=sdpa` in the graphs it
builds. Key nodes: `Trellis2LoadModel`, `Trellis2LoadMesh`, `Trellis2MeshTexturingMultiView`
(front required + back/left/right — matches our turnaround exactly).

1. **Node pack:** `git clone https://github.com/visualbruno/ComfyUI-Trellis2` into
   `custom_nodes/` (or ComfyUI Manager). With **ComfyUI's own python**:
   `pip install -r requirements.txt`, then the **five bundled wheels** for your torch version
   from `wheels/Windows/Torch<ver>/`: `cumesh`, `nvdiffrast`, `nvdiffrec_render`,
   `flex_gemm`, `o_voxel` (wheel sets exist for Torch 2.7.0 / 2.8.0 / 2.10.0, py3.11–3.13;
   the author's tested combo is **Win11 + Python 3.11 + Torch 2.7.0+cu128**).
   Also `pip install triton-windows` — the pack imports triton but doesn't list it.
   Skip flash-attn (no wheel shipped); we use the `sdpa` backend.
2. **Models — all auto-download on first run** (pre-seed if the worker is offline):
   - `visualbruno/TRELLIS.2-4B-FP8` → `ComfyUI/models/visualbruno/TRELLIS.2-4B-FP8/` (**8.1 GB**;
     the 16 GB-friendly variant — skip the 16.2 GB bf16 `microsoft/TRELLIS.2-4B` unless A/B-ing)
   - DINOv3 encoder → `ComfyUI/models/facebook/dinov3-vitl16-pretrain-lvd1689m/` (**1.2 GB**;
     auto-pulled from visualbruno's UNGATED mirror — no Meta license dance needed)
   - `microsoft/TRELLIS-image-large` `ckpts/ss_dec_conv3d_16l8_fp16.safetensors` (**148 MB**)
   - rembg u2net cache (~176 MB)
3. **Restart ComfyUI**, then open `/api/klein2/health` — the worker should list
   `trellis_classes` and a `trellis_plan` (incl. `model_loader`) with no error.
4. Known limits: texturing **re-unwraps UVs** (fine — we only render snapshots) and the
   multi-view node takes the 4 canonical views (front/back/left/right — exactly our base set).

**16GB fallback if TRELLIS misbehaves:** Hunyuan3D-**2.0** paint via
`kijai/ComfyUI-Hunyuan3DWrapper` (Tencent's own figure: 16 GB for shape+texture *combined*;
paint alone less; models auto-download, ~10 GB). Hunyuan3D-**2.1** PBR paint needs ≥21 GB —
not for these workers.

Nothing new to install for generation: Klein 9B **fp8** (the standard 16 GB config, what the
workers already run) and the existing `KLEIN_EDIT_ULTRA_WORKFLOW_*` graphs cover the pose
library and the 2–3-ref generate. The M2 pose-file lane (SDXL + ControlNet OpenPose,
~8–10 GB peak) also fits 16 GB when we get there.

**Escape hatch (recommended once TRELLIS is installed):** if the auto-built graph ever
mis-wires (the pack's node names drift between versions), export a working texture workflow
from the ComfyUI UI in **API format** and save it as `workflows/KLEIN2_TRELLIS2_TEXTURE.json`
with placeholders `%MESH%`, `%VIEW_FRONT%`, `%VIEW_LEFT%`, `%VIEW_BACK%`, `%VIEW_RIGHT%`,
`%PREFIX%` where the mesh filename, view image filenames and output prefix go. When that file
exists it is used verbatim instead of the auto-builder.

Frontend viewer needs one npm dep (already added to `package.json`): in `frontend/` run
`npm install` once.

## Milestone plan (per the agreed staging)

- **M1 (this build):** tab + statue texturing + viewer/snapshot + Pose Library 2.0 + 2–3-ref
  generate. Untextured clay shows in the viewer until the statue is generated, so the whole
  loop is testable before TRELLIS is even installed.
- **M2 (next):** pose-FILE import — OpenPose JSON / DWpose / depth rendered to a neutral gray
  mannequin image via SDXL ControlNet OpenPose (broadest pose-file support), landing in the
  same library. LoRA-assist lanes (thedeoxen RefControl-pose, MatchingPose+Mannequin — both
  Apache-2.0) stay in the back pocket if pure 2-ref prompting needs hardening.

## Verification discipline

Nothing here is "verified working" until measured: the statue by opening `statue.glb`; a
generation by comparing the output against `ref_identity.png` / `ref_pose.png` in its `_gen`
folder (the UI shows the exact refs under every batch). `statue_graph.json` +
`/api/klein2/health` answer "what would/did actually run" without spending a worker cycle.
