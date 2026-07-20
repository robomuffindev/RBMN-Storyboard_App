# CHARACTER 3D PLAN — Tier 1 execution plan (v2, 2026-07-18)

Goal: a **character-shaped, riggable 3D mannequin** per character so pose
references carry the character's exact body (and face geometry) instead of the
generic parametric mannequin — the strongest body-consistency lever short of a
character LoRA, and the ONLY pose-reference path that works for non-humanoids
(furries / hybrids / monsters, which the humanoid mannequin and DWPose skeleton
cannot represent).

Untextured by design: the texture stage doesn't fit 16GB VRAM, Klein repaints
appearance anyway, and Klein reference latents transfer STYLE — a clean clay
render is a pure geometry signal (a textured CGI character would leak style
worse than the old mannequin did).

---

## What to install on the ComfyUI workers  <- START HERE

Everything generation-side runs through ComfyUI. One worker (the pinned .224)
is enough for the 3D steps — they're once-per-character, not per-pose.

1. **Update ComfyUI to current master** — Hunyuan3D-2 mesh generation is BUILT
   IN (comfy_extras/nodes_hunyuan3d.py): `ImageOnlyCheckpointLoader`,
   `CLIPVisionEncode`, `Hunyuan3Dv2Conditioning` / `Hunyuan3Dv2ConditioningMultiView`,
   `EmptyLatentHunyuan3Dv2`, `ModelSamplingAuraFlow`, `VAEDecodeHunyuan3D`,
   `VoxelToMesh`, `SaveGLB`. No custom node needed for the mesh.
2. **Download ONE checkpoint** into `ComfyUI/models/checkpoints/`:
   - `hunyuan3d-dit-v2-mv_fp16.safetensors` (4.93 GB) —
     https://huggingface.co/Comfy-Org/hunyuan3D_2.0_repackaged
     (multiview: we feed the base set's front/left/right/back views when the
     character has them; front-only also works)
   - optional, max fidelity: `hunyuan_3d_v2.1.safetensors` (7.37 GB) from
     Comfy-Org/hunyuan3D_2.1_repackaged — same nodes, steps 30 / cfg 5.
   VRAM: shape-only ~= 6 GB — comfortable on the 4060 Ti 16GB.
3. **Install the ComfyUI-UniRig custom node** (auto-rigging INSIDE ComfyUI):
   https://github.com/PozzettiAndrea/ComfyUI-UniRig (ComfyUI-Manager registry
   name `comfyui-unirig`). Its installer bundles the painful Windows deps
   (flash-attn / spconv / torch-scatter / bpy) as prebuilt packages and is
   CI-tested on Windows + windows_portable. Checkpoints (~2.9 GB) auto-download
   to `ComfyUI/models/unirig/`. Nodes we use:
   `UniRigLoadMesh -> UniRigLoadModel -> UniRigAutoRig` (one shot: skeleton +
   skin weights + FBX out). `skeleton_template`:
   - `"mixamo"` -> humanoid skeleton with Mixamo bone names (our retarget target)
   - `"articulationxl"` -> ARBITRARY skeletons — this is the furry/monster path
   UniRig needs ~8 GB VRAM; runs after the shape step, not concurrently.
   (Fallback if the installer misbehaves: UniRig CLI in WSL2 — documented in
   the repo; keep as plan B only.)
4. **App machine: Blender** for posing + clay rendering (NOT on workers —
   it's cheap CPU work). Install Blender 4.x, set `BLENDER_PATH` in `.env`;
   the app drives it headless (`blender --background --factory-startup
   --python pose_clay.py`). FBX/glTF importers are built in; the Workbench
   engine's matcap render IS the clay render and needs no GPU.
   (If the app venv ever moves to Python 3.11, `pip install bpy==4.2` runs
   in-process instead — same code.)

## Pipeline (per character: steps 1-2 ONCE; step 3 per pose, cheap)

1. **Mesh** (worker, ~1 min): active base render (+ the 3 extra views when the
   base was made with "full 4-view set") -> CLIPVisionEncode per view ->
   Hunyuan3Dv2ConditioningMultiView -> KSampler (20 steps, cfg 7.5,
   euler/normal, ModelSamplingAuraFlow shift 1, latent res 3072) ->
   VAEDecodeHunyuan3D (chunks 8000, octree 256) -> VoxelToMesh (surface net,
   0.6) -> SaveGLB -> app fetches the GLB.
2. **Rig** (worker, ~1-2 min): GLB -> UniRigAutoRig (mixamo template for
   humanoids; articulationxl toggle for creatures) -> rigged FBX -> app fetches
   and files BOTH in the character catalog (manifest.vnccs.mesh3d =
   {glb_asset_id, fbx_asset_id, template, created}).
3. **Pose + clay render** (app-side Blender, seconds, CPU): our pose dicts are
   already 3D bone rotations for the VNCCS mannequin rig -> a shipped
   mannequin->Mixamo bone mapping retargets them onto the rigged FBX ->
   Workbench matcap render at the pose canvas size -> PNG pose capture.
   Image references keep working too: DWPose keypoints -> (later) HybrIK/HMR
   lift -> same retarget path.
4. **Generation**: new Pose input option "3D character" (and a third chip in
   Simple mode) — the clay renders replace the mannequin captures as image 1.
   EVERYTHING downstream is unchanged: pose-ref release, LoRA picker, style
   guard, identity chain, ingest. The style guard matters MORE here (clay is
   neutral but still CGI — release at default 0.85 stays on).

## Implementation phases (app code)

- **B1 — mesh+rig generation**: `char_mesh.py` module (qwen_clothes.py
  pattern): `build_hunyuan3d_graph()` + `build_unirig_graph()` + model
  resolution from /object_info (readable missing-file errors). Endpoint
  `POST /mesh3d/generate` (character_name, use_views, template) -> background
  task -> files GLB+FBX as catalog assets. UI: "Generate 3D body" button under
  the base preview (Create tab) with a LiveBanner + a 3D status chip.
- **B2 — retarget + clay render**: `pose_clay.py` (Blender script) +
  `mannequin_mixamo_map.py` (bone table) + `render_pose_clay()` service that
  mirrors `pose_render.render_pose_captures()`'s signature so `_klein_submit`
  can swap sources transparently.
- **B3 — integration**: `klein_pose_input='mesh3d'` handled in `_klein_submit`
  (clay captures instead of mannequin captures; DWPose skeleton conversion
  still available on top); Simple-mode third chip "3D character (needs a
  generated 3D body)"; disabled state with hint when no mesh exists.
- **B4 — validation**: Variation Test axis `klein_pose_input`
  ['', 'skeleton', 'mesh3d'] on 2 characters — the report tells us if the
  clay reference beats the mannequin before we polish further.

## Known limits (honest)

- Pose RETARGETING targets the Mixamo humanoid template. Non-humanoids get
  RIGGED fine (articulationxl) but our humanoid pose library doesn't map onto
  a quadruped/monster skeleton — creature POSING lands in Tier 2 (pose-from-
  image via the rig, or per-creature pose presets). Tier 1 still gives
  creatures a correct STANDING/neutral clay reference, which already beats a
  human mannequin for body consistency.
- Face geometry rides free (the mesh comes from the base render), but tiny
  faces at mannequin distance carry limited detail — the identity chain
  remains the face authority.
- Hunyuan3D 2.5 is API-only (never open-sourced); 2.0/2.1 are the local
  ceiling today.

## Tier 2 (later)

Pose-from-image for arbitrary rigs (HybrIK/HMR lift + IK), per-creature pose
preset authoring on the rig, in-app 3D pose editor (three.js viewer exists in
PoseStudio3D.tsx as a starting point), optional textured preview when >16GB
workers arrive.
