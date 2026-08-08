# prototypes/pose_lab -- rescued from the 2026-07-27/28 cloud-sandbox dev loop

These files are WORKING PROTOTYPES, measured against Duke's real rigged.fbx in the cloud
sandbox. They are NOT wired into production yet -- the wiring order is deliberate (see
HANDOVER_PROMPT.md sect.5): a genuinely arms-clear (non-welded) mesh must pass gap_test first,
then these ship into backend/services/character_studio/vnccs_native/mia_local/.

## voxel_skin.py -- FREE geodesic voxel skinning (replaces the $30 addon idea)
Dionne & de Lasa 2013 "geodesic voxel binding", pure numpy (+scipy for weight smoothing,
gracefully skipped if absent). Ray-parity solid fill -> per-bone geodesic BFS through the mesh
INTERIOR -> relative-falloff weights (power 4, cutoff 2.0) -> 15-iter Laplacian smoothing ->
top-4 prune. Includes the FAT-TORSO-SEEDS trick (spine/hips/neck/pelvis seeds dilated ~7% of
grid res) so torso bones win the flank where welded contact would let arm bones leak in.
Measured: 4s for 241k verts, zero orphan weights, rest pose renders perfectly (maxstretch 1.00),
torso stays solid under arm rotations (Blender bone-heat could not do this). API:
    bone_names, packed = voxel_skin.compute(mesh_obj, armature_obj)
    voxel_skin.apply_to_object(mesh_obj, bone_names, packed)

## clay_ik_dev.py -- clay_driver.py + two additions (dev copy, based on v1.199.115 driver)
1. `job["reskin"] = "voxel"` -> uses voxel_skin (import path: same directory).
2. `job["ik_clearance"] = true` -> BVH-based arm-clearance pass after FK: samples arm-bone
   segments against a BVH of the non-arm body, then chain-3 IK pushes the hand target along the
   worst-violation surface normal (travel-capped). Handles lateral AND anterior (arm-across-
   belly) burial in one mechanism. Measured best result: penetration 54.6%->14.3% and
   18.5%->12.8% on the two worst library poses -- but on a WELDED mesh the big arm travel
   stretches membranes, which is why the weld must be fixed first.
Diff against the production clay_driver.py to port (search "DEV:" and "voxel").

## Cloud sandbox recipe (no device/worker time needed)
    uv venv bpyenv -p 3.11 && uv pip install -p bpyenv/bin/python bpy numpy scipy pillow
    # bpy 5.0.1 reproduced device penetration numbers bit-for-bit (prod Blender is 4.3)
    # EGL errors on stderr are harmless; Workbench renders fine headless.
Attach a rigged.fbx to the chat; drive clay_ik_dev.py with a job.json (schema = what
pose_clay.py builds; poses come from workflows/vnccs/STEP1_CREATOR.json widgets_values).
modelRotation [0,yaw,0] gives free multi-angle views. The ray-gap weld test lives in
tools/gap_test.py (also runnable in the sandbox).
