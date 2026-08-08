# Klein DEPTH pose control (v1.199.83 → 113)

How Klein pose sets are produced, why the earlier approach could not work, and what is still open.
Companion to `HANDOVER_PROMPT.md` (status/next steps) and `CHANGELOG.md` (per-version detail).

## The problem this replaced

FLUX.2 klein has **no ControlNet**. The pose image was therefore attached as a `ReferenceLatent` — the
same channel as the identity references — which is advisory conditioning with no tie to the output
latent grid. The rendered body was a negotiation between pose ref, identity refs, pose-LoRA prior and
prompt, and the prior won. Every knob (ref-release, LoRA strength, clay vs mannequin, smear cleanup,
framing, height anchoring) was downstream of that and could not fix it.

Compounding it, `refcontrol_v2_poses.safetensors` is trained on DWPose **skeletons**. A skeleton
carries zero volume, and the author states it works best when the pose map already matches the
reference's proportions. Heavy or short characters could never survive that path.

## The pipeline now

```
rigged.fbx (MIA)
  └─ weld duplicate verts            scale-relative (weld_rel = 0.0005 × mesh extent)
  └─ re-skin                          Blender heat weights over MIA's skeleton (clay_reskin)
  └─ apply pose + AUTO-ABDUCTION      clearance from the character's own torso width profile
  └─ corrective smooth                relaxes residual deformation artefacts
  └─ Blender Z pass → 16-bit          compositor MapRange + Invert, view transform = Standard
  └─ percentile re-normalise          1/99 over eroded interior → near=white / far=dark / bg=0
        ↓
  RefControl DEPTH LoRA @ 1.0 + flux-2-klein-base-9b-fp8, cfg 5, 20 steps, trigger "refcontrol"
  identity/likeness from the SAM3D turnaround references
```

A depth map carries **pose + volume + height + silhouette** in one channel. That is the property a
skeleton lacks and the reason this path holds a heavy build.

## Settings

| Key | Default | Notes |
|---|---|---|
| `klein_pose_input` | `mannequin` | set `depth` to enable this path |
| `klein_pose_structure_lock` | off | img2img latent init + `SplitSigmasDenoise`; hard silhouette lock, built and graph-tested but unused |
| `clay_reskin` | `blender` | `off` reverts to MIA's weights |
| `clay_auto_abduct` | on | per-character arm clearance |
| `clay_arm_abduct` | 0 | manual additive override, degrees |
| `clay_weld_rel` | 0.0005 | fraction of mesh extent |
| `clay_corrective_smooth` / `_iters` | 1.0 / 20 | Corrective Smooth modifier |
| `clay_smear_stretch` | 1e6 (keep all) | cutting faces punches holes; a void is worse than a fringe |
| `clay_preserve_volume` | off | DQ skinning measured worse |

Depth mode auto-selects the LoRA, the undistilled base checkpoint, cfg 5 and 20 steps, and
auto-enables the 3D body when a rigged mesh exists. Nothing is per-character.

## Auto-abduction

The pose library stores **bone rotations authored for an average-width mannequin**. Bone angles are not
body-aware: on a wide torso, "arms at the side" places the upper arm inside the chest. Measured across
the 12 baseline poses, **47–91% of arm vertices land inside the torso**.

The driver builds a torso half-width profile once from the rest mesh (40 height bins, 97th percentile
of |x| per bin) in **armature space** (Y-up), reads elbow/wrist positions off the armature per pose,
and applies the clearance as a **post-rotation about the armature forward axis** — never by injecting
into the bone's euler, which composes inside the pose's own rotation and stops being abduction.
Gated on the elbow being below shoulder height; above that the arm is clear by construction.

## Measuring

`pose_audit.bat [character]` is the tool that matters: it scores every library pose for penetration
and peak stretch, three ways, and writes a labelled contact sheet plus `recommended_poses`.

**Judge by the contact sheet, not the scalars.** In this work a metric improved 10–15× while the render
got worse, and `maxstretch` (a ratio against rest edge length gated at 1e-6) produced three separate
wrong diagnoses. `CLAY_DISP` reports absolute displacement and does not have that failure mode.

## Known limits

- **Anterior clearance is unsolved.** Poses folding the forearm across the belly cannot be helped by
  lateral abduction; the obstruction is in front of the arm. Needs shoulder-flexion clearance.
- **An arm tucked close to the torso still merges** in the final render even when present in the depth
  reference.
- The **parametric mannequin cannot substitute for the scan** — measured at 48–59% of a heavy
  character's width even maxed out.
- Face likeness under cfg 5 has not been tuned; first lever is `klein_pose_pulid = on @ 1.0`.


---

# Addendum v1.199.115-121 (2026-07-27/28): Normal mode, the welded-mesh root cause, T-pose canonicalization

**Read HANDOVER_PROMPT.md for the full causal chain.** Summary:

- **Pose input = NORMAL** (v115): camera-space surface normals (Workbench `check_normal+y`
  matcap, DSINE convention) driving `flux2_klein_9b_refcontrol_normal.safetensors` -- same
  recipe as depth otherwise. VERIFIED on real runs: keeps the tucked arm that depth loses
  (an arm against the torso is near-invisible in depth but a hard orientation discontinuity
  in normals). Recommended default for pose sets on well-cleared (audited) poses.
- **The deepest root cause of the arm failures is the MESH, not the model chain**: the Hunyuan
  scan topologically WELDS arms to the torso wherever they touched in the source images (SDF
  reconstruction fuses all contact). Measured with the ray-cast gap test (`gap_test.bat`).
  Posing fused geometry stretches membranes; no rig/skinning/IK can fix topology.
- **Fix = pose canonicalization** (the industry-standard step: redraw the character in T-pose
  BEFORE meshing). v120: the mesh-ready set re-poses the turnaround FRONT to a bone-driven
  T-pose, then derives the other views from it (`klein_mesh_tpose=off` disables). Status at
  handover: implemented, NOT yet field-verified.
- **Free geodesic voxel skinning + IK clearance prototypes** (measured working in the cloud
  sandbox) live in `prototypes/pose_lab/` -- to be wired into `mia_local` once a mesh passes
  the gap test. App users must never need a paid tool.
