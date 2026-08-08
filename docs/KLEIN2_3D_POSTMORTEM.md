# Klein 2.0 statue (3D) — postmortem, PINNED 2026-08-02

Status: **pinned, not deleted.** All code remains live behind the 🚀 Klein 2.0 tab and works
end-to-end. Lorenzo: "i still think there is a use for the 3d stuff we've made but i will
figure out for what later." This doc is the re-entry map.

## What we built (all functional)

- TRELLIS.2 on 16GB workers: FP8, low_vram, sdpa, tiled decoder; one-script worker installer
  (`scripts/worker/install_trellis2.bat`) that handles every gotcha we hit (broken Manager
  registry entry, open3d having no cp313 wheels, nested `Torch2100/CUDA 13.1` wheel folder,
  missing python include/libs for triton JIT, the reconviagen folder pack-bug, resumable
  model download/repair).
- Two statue lanes in `backend/api/klein2.py`: **texture** (paint the Hunyuan rig mesh) and
  **generate** (TRELLIS builds its own geometry+texture from the 4-view turnaround), both as
  defensively auto-built graphs from `/object_info` (survives pack node-name drift), with the
  view-preprocess stage (rembg + crop) the pack requires.
- Rotatable three.js statue viewer with environment/IBL, hi-DPI, deterministic 832×1216
  📸 angle snapshot.

## What went wrong, in order (each measured, all fixed except the last)

1. Painting the **Hunyuan-2 rig mesh** → welded hands, mushy face. Hunyuan fuses close
   geometry; no texture can fix missing geometry. (→ switched to TRELLIS-generated geometry.)
2. Feeding **raw plate renders** into the generators → holes, wrong proportions, misplaced
   limbs. TRELLIS requires background-removed, object-cropped views (its own example
   workflows always preprocess). Fixed with `Trellis2PreProcessImage` wiring.
3. After preprocessing: structure GOOD (proportions, 3D hair, separated fingers) but **face
   and hands stayed soft/weird**. Root cause is information-theoretic: in a full-body
   turnaround the face is ~100px, and full-body conditioning at ~1024 leaves almost no face
   pixels. Upscaling sources helps only marginally — the conditioning budget is the ceiling.
4. Final upscaled-source attempt (2026-08-02) came out **worse — shiny chrome, texture
   missing entirely**. NOT diagnosed (pinned before root-causing). Candidates for whoever
   reopens this: the 1536_cascade High preset changing behavior; the upscaled (larger)
   sources interacting with preprocess max_size 2048; a texture-slat stage failure silently
   producing untextured output; metallic/roughness defaults in the ovoxel bake. The exact
   graph of that run is in `mesh3d/<char>/statue_graph.json` — start there.
5. Arm angle in statues mirrors arm angle in the T-pose set — TRELLIS reconstructs what the
   views show; view consistency IS the quality dial.

## Why we pinned it

The likeness ceiling on 16GB hardware (FP8, capped conditioning) was below the bar for an
identity reference, and our own measured data cuts the other way: nearest-2D-view identity
refs scored 0.744 → 0.901 on a −124° pose (Klein 1.0 lane, verified). Klein 3.0 (pure 2D
reference mode) is the successor path. Hunyuan3D-2.1 paint was researched twice and is a
hard NO on 16GB (21GB is activations, not weights; no quantized paint checkpoint exists;
core ComfyUI never wired paint into its memory manager).

## Where 3D still plausibly earns its keep (future use ideas)

- Camera **elevation** shots (low hero angle, top-down): the 2D turnaround has zero elevation
  data; even a mediocre statue knows the silhouette from above/below.
- Blocking/composition previews (statue as a posable stand-in in a scene mock).
- Consistent-scale multi-character lineups.
- If beefier workers (≥24GB) arrive: bf16 model, 4096 bake, 1536_cascade all become safe,
  and the whole quality ceiling lifts — re-test before assuming the 16GB verdict still holds.
- The rigged FBX + clay lane (Klein 1.0's depth/normal pose control) is untouched by any of
  this and still works as before.

## Re-entry checklist

1. `/api/klein2/health` still preflights workers (trellis plan + model_loader).
2. Re-run a generate-mode statue; diagnose issue #4 above via its `statue_graph.json`.
3. Check the pack's changelog — visualbruno ships texture-quality improvements monthly
   (1536 res and multi-view texturing both landed Feb 2026).
4. The A/B that was never run: same pose+prompt, identity = statue snapshot vs nearest 2D
   view, at a canonical angle AND an elevation angle. That test decides 3D's role with data.
