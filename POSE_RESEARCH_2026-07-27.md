# Pose System — Deep Research & Gap Analysis (2026-07-27)

Deep dive into our pipeline + fresh outside research (ecosystem state as of today).
Everything here is either **(measured in our repo)**, **(sourced, with URL)**, or clearly
marked **PROPOSAL — unverified**. Nothing below claims to be fixed.

---

## TL;DR — what we've been missing

1. **A `refcontrol` NORMAL-map LoRA now exists** — same author (thedeoxen), same family,
   same feeding mechanism as the depth LoRA we already run. Normals are the textbook fix
   for our exact remaining defect: an arm tucked against the torso is nearly invisible in a
   depth map (almost no tonal separation — we measured this and half-fixed it with 16-bit +
   percentile renorm) but produces a **sharp surface-orientation discontinuity in a normal
   map**. This is a drop-in A/B: render a normal pass from the same posed mesh, swap one
   LoRA. https://huggingface.co/thedeoxen/refcontrol-FLUX.2-klein-9B-reference-normal-lora
2. **Our depth run releases the control signal at 85%.** `klein_pose_ref_end` (default
   0.85) is applied unconditionally — including in depth mode (API:1652–1661). So the ONLY
   spatial binding we have is deliberately dropped for the last 15% of denoising, which is
   exactly when fine limb boundaries resolve. This was a sensible knob for the advisory
   mannequin ref; for depth it may be actively causing the tucked-arm merge.
   One-variable A/B via `worker_run.bat --set ref_end=1.0` (or equivalent) is cheap.
3. **There is still no ControlNet for klein 9B** (confirmed current: BFL official = none;
   alibaba-pai Fun-Controlnet-Union = FLUX.2-dev-32B only, confirmed not-klein in their own
   discussion; DiffSynth has a klein-**4B**-only research ControlNet, no ComfyUI). Our
   depth-LoRA architecture is the community mainline, not a workaround. We were not wrong.
4. **The anterior-clearance problem has no off-the-shelf solution anywhere** — the entire
   retargeting literature (R2ET, MeshRet, STaR, SIGGRAPH-2026 KAIST) is Mixamo-skeleton-bound
   research code with 15–19% residual penetration. The practical answer is a small custom
   Blender solver (BVHTree clearance + temporary IK), which is a direct generalization of
   the auto-abduction we already shipped. ~few hundred lines, milliseconds per pose.
5. **User pose photos → 3D is a solved, already-bundled problem**: Meta's SAM 3D Body
   (we vendor its code in vnccs-utils, and the workers already run `RBMN_SAM3D_Proportions`)
   outputs per-joint 3D rotations. Route: user pose image → SAM3D Body → joint rotations →
   **our existing clay path** → depth/normal render → generate. That keeps every pose,
   library or user-supplied, flowing through the same body-correct mesh pipeline.
6. **MatchingPose LoRA** (nhathoangfoto, Apache-2.0, klein-9B, trigger `matchingpose9b` —
   a trigger our code already knows about, KP trigger prefixes) is the #2 community pose
   solution: converts a pose photo into a faceless mannequin anchor, then transfers.
   Worth one A/B, but its anchor is average-bodied — likely the same body-agnostic trap we
   already measured. Low priority.

---

## 1. Where we actually are (measured, from the code deep-dive)

The full node-by-node map is in the session notes; the essentials:

- Our Klein pose graph is a faithful superset of the VNCCS author's own Klein9b sample:
  scale→VAEEncode→`ReferenceLatent`(pose first, identities after) on pos+neg,
  CFGGuider+Flux2Scheduler+SamplerCustomAdvanced. The author's sample has NO extra spatial
  binding either — cfg 1, 4 steps, prompt "Draw character from image2". Depth mode swaps to
  refcontrol depth LoRA + undistilled base + cfg 5/20 steps. All consistent with what we
  root-caused: references are advisory in flux2; the LoRA is what makes image-1 binding.
- **User-supplied pose images are not accepted anywhere in the Klein pose path today.**
  `pose_set` is always bone-rotation dicts. The only image→pose machinery in the repo:
  (a) upstream `VNCCS_PoseStudio` has a `pose_image` → SAM3D → pose-JSON path (browser UI
  only), (b) a dead OpenPose module (`pose_renderer.py` BODY_25/COCO-18 → joints converter,
  imported by nothing in the pose path), (c) `klein_pose_source=sam3d` which only does
  turnaround view derivation.
- The pose library (worker-side) stores **bone rotations only** (+cosmetic preview) — no
  images, no joint positions. Body-agnostic by construction; our auto-abduction +
  heat re-skin is the compensation layer, applied at render time in `clay_driver.py`.
- Housekeeping items surfaced by the audit (none urgent, all cheap):
  - `FaceDetailer` refine always runs at cfg 1.0/euler even under the depth recipe (cfg 5) —
    the refine pass ignores the negative in depth runs (KP:635–637).
  - `_klein_ref_disk_get/_put` caches reference photos keyed by upload NAME, not content
    hash (API:897–924) — a re-upload of a different image under the same name serves stale bytes.
  - Dead unreachable second implementation inside `_klein_autofit_mesh` (API:877–889).
  - `mesh_fit` import at API:1083 — module wasn't in the staged dir; if it's genuinely absent
    the 3D-scan body fit silently no-ops (worth one `dir` check).
  - Our default CLIP is `qwen_3_8b_fp8mixed_abliterated`; the author ships plain
    `qwen_3_8b_fp8mixed`. Basename resolution will NOT match one to the other on a worker
    that has only one of them.

## 2. Ecosystem findings (sourced)

**ControlNet status (July 27, 2026):** klein 9B has none, and none is coming from BFL —
their 2026 releases are API-only tools. BFL's own June 2026 LoRA blog documents the
sanctioned path to spatial control: **control-conditioned edit LoRAs** trained on
klein-base with paired control images (ostris/ai-toolkit `control_path:`). RefControl and
MatchingPose are exactly this. https://huggingface.co/blog/black-forest-labs/flux-2-klein-lora

**The RefControl family grew.** Full current lineup (thedeoxen, trained on klein-base-9B,
trigger `refcontrol`, weight 0.8–1.0, base ~guidance 4–5): depth (ours), **normal (NEW —
DSINE-convention normals)**, canny, **lineart**, pose (skeleton, the one that can't carry
mass), 4B depth, + a manga-colorization one. Krita AI Diffusion is discussing native
integration. https://huggingface.co/thedeoxen

**Evidence that normal+depth beats depth alone for limb separation:** Champ (ECCV 2024)
conditions human generation on SMPL-rendered depth + normal + part-segmentation + skeleton
precisely because depth under-specifies contact regions; their ablations show each geometry
channel contributes (skeleton-only SSIM 0.672 → full 0.773). An arm pressed against the
belly is a near-zero signal in depth but a hard discontinuity in normals.
https://arxiv.org/abs/2403.14781

**Pose-from-image:** SAM 3D Body (CVPR 2026) is current SOTA (beats HMR2.0/NLF/CameraHMR),
outputs Momentum Human Rig per-joint rotations, Apache-2.0 rig format with SMPL conversion,
commercial-clean, and we already bundle its code. For openpose/dwpose skeleton→3D:
MocapNET (OpenPose JSON → BVH rotations, CPU real-time; institute license — verify) or
MotionBERT (Apache) + smplfitter. **Depth-image → pose is a literature dead end** — nothing
modern exists; if a user hands us a depth map, the right move is to use it DIRECTLY as the
control image (renorm → refcontrol depth), not to reconstruct a pose from it.

**Alternative generators:** Qwen-Image-Edit-2511 (Apache-2.0, InstantX Union ControlNet
with real depth/pose control, strong consistency, FP8 on 24GB) is the one credible
challenger — and we already run Qwen for emotions. Wan VACE works but demonstrably slims
heavy bodies when driven by skeletons (use depth if ever tried). The FLUX.1 consistency
stack (UNO/DreamO/InstantCharacter/etc.) has no FLUX.2 ports. Also note: **klein 9B is
FLUX Non-Commercial; only klein 4B is Apache-2.0** — relevant whenever RBMN output goes
commercial (Qwen-Edit is Apache; worth keeping the Qwen lane healthy).

**Nobody has our mesh-driven dataset pipeline.** Community one-click LoRA-dataset tools
(lora-dataset-studio, dataset-maker workflows) are generator-side variation + InsightFace
auto-culling (~0.30 cosine threshold) + Florence-2/JoyCaption captioning + ai-toolkit
training (20–60 images, <1h on a 4090). Those surrounding pieces are worth borrowing once
posing is solid.

---

## 3. Recommended plan (priority-ordered, every step measurable)

**Phase 0 — protect the known-good.** No changes to SAM3D turnaround / base / Hunyuan3D /
Qwen flows. Everything below is new settings values or new code behind flags, A/B'd with
the existing closed-loop tools (`worker_run.bat`, `pose_audit.bat`, contact sheets).

**Phase 1 — kill the tucked-arm merge (days, not weeks):**
- **1a. `ref_end` A/B in depth mode.** One variable: keep the depth/pose reference bound
  to 100% instead of releasing at 0.85. `worker_run.bat` sweep on a stored graph. If the
  merge is happening in the released tail, this alone may close it. Zero new code.
- **1b. Normal-map mode.** Add a normal pass render to `clay_driver` (same camera, same
  posed mesh; convert to DSINE camera-space convention the LoRA was trained on), fetch
  `flux2_klein_9b_refcontrol_normal.safetensors` to the workers, add
  `klein_pose_input=normal` reusing the whole depth-recipe machinery (same base UNET, same
  cfg/steps preset, same trigger). A/B depth vs normal vs **depth+normal composited**
  (e.g., normal RGB with depth in a fused render — experiment) on the recommended pose set.
- **1c. (cheap experiment) Freestyle intersection-line render** at low weight through the
  lineart/canny refcontrol LoRA as a boundary pin — Blender draws a crease line exactly
  where arm meets torso. Only if 1a/1b leave residue.

**Phase 2 — anterior clearance solver (the deferred precision path):**
Replace/extend auto-abduction with a general clearance pass in `clay_driver`:
posed mesh → BVHTree of the torso → sample clearance along each arm chain → if penetrating,
temporary IK constraint on the arm, push the IK target out along the local torso normal
(binary search to a margin), bake, remove constraint. This handles lateral AND anterior
(arm-across-belly) cases in one mechanism, on the character's own geometry — the same
philosophy as auto-abduction, generalized. Score with `pose_audit.bat`; goal is moving the
recommended set from 7/12 toward 12/12 and unlocking imported pose packs wholesale.

**Phase 3 — user-supplied pose inputs (the automation requirement):**
One ingestion front door, everything converging on the SAME clay path so body correctness
is automatic:
- **Pose photo** → SAM 3D Body (worker node; code already vendored) → joint rotations →
  map to our rig (Mixamo map already exists in `clay_driver`) → save as a library pose
  JSON → normal clay/depth flow. Auto-scored by `pose_audit` on import.
- **OpenPose/DWPose skeleton image** → parse keypoints (limb colors are deterministic; JSON
  accepted directly when available) → 2D→3D lift (MocapNET or MotionBERT) → same path.
  Mark beta; quality depends on the lift. Fallback: feed the skeleton straight to the
  refcontrol pose LoRA (our existing `skeleton` mode) with the documented no-body-mass caveat.
- **Depth-map image** → bypass the mesh entirely: renorm → refcontrol depth directly.
  Document that body mass then comes from the user's depth image, not from the character.
- Every import lands in the pose library as bone rotations + gets audited, so the library
  grows body-safely — exactly the agreed "curate and import" direction.

**Phase 4 — face likeness (only after the body is right, as agreed):**
`klein_pose_pulid = on @ 1.0` (the untried lever), then `klein_face_crop_ref = on`, then
fix the FaceDetailer cfg mismatch under the depth recipe. One variable at a time via
`worker_run.bat`.

**Phase 5 — the one-button orchestrator (the end-state Lorenzo described):**
"User provides reference images → system checks whether every image needed for the mesh set
exists → creates missing ones (known-good turnaround flow) → Hunyuan3D mesh → MIA rig +
heat re-skin → audited pose set → sprites / LoRA dataset." Nearly all stages exist; the
missing piece is a single idempotent `ensure_character_ready` chain that walks the ladder,
resumes wherever a character currently is, and never re-runs a confirmed-good stage. Then
bolt on the community dataset tail (captioning + InsightFace auto-cull) for LoRA sets.

---

## 4. Verification checklist before any of this is called done
- [ ] `worker_probe.bat` after fetching the normal (and lineart) LoRA — confirm on all 3 workers.
- [ ] Fresh app pose run (not worker_run replay) whenever clay code changes.
- [ ] Contact sheets opened and READ for every A/B (scalars have lied 4+ times).
- [ ] `pose_audit.bat` re-run after the clearance solver; compare recommended_poses count.
- [ ] The tucked-arm defect judged on the SAME poses that show it today (#4/#8/#9/#2 spread).
