# Klein Hybrid Mode — Qwen→Klein Swap Map

Where the VNCCS pipeline uses Qwen, which parts depend on Qwen-only LoRAs with
no Klein equivalent, and where Klein can slot in with little friction.
Compiled 2026-07-11 from the vendored `vnccs/` + `vnccs-utils/` sources.

## Status: what Klein Hybrid already does (v1.74.0; face-consistency wave v1.77.0)

**v1.77.0–1.79.0 face-consistency wave:** pose runs add a close-up identity
FACE-CROP reference (app-side YuNet/Haar) with same-person binding prompts;
emotion runs are crop-and-stitch (face-context box sampled at ~1MP, anchored
to the ACTIVE base version's face crop, composited back in-graph);
PuLID-Flux2 auto-detected per worker and patched into both graphs
(settings: klein_pulid / _file / _strength / _provider — the picker prefers
klein/9b-named weights and the newest version suffix). v1.78.0 adds a
LOW-DENOISE FaceDetailer face-refine pass on every pose sprite (auto-detected
Impact-Pack; inputs filtered against the worker's actual FaceDetailer schema;
klein_face_refine / _denoise 0.40 / _steps), unique per-chunk upload names
(fix: parallel chunks on shared ComfyUI input folders overwrote each other's
pose captures → duplicate pose sets), and the Clone-tab ✨ Generate Preview
(one default pose through the full identity chain, filed as a base version).
v1.79.0 surfaces the settings in the ⚙ Settings panel, fixes the NATIVE clone
preview (real CharacterCloner limited to one pose instead of the Anima
checkpoint preview), labels + routes characters by mode variant
(native/klein), and adds thumbnails + a ★ hero picker.
Verification endpoint: GET /api/studio/vnccs/klein-status.
Klein multi-ref method audited: chained ReferenceLatent (daisy-chain) is the
official/native mechanism (grid/stitch rejected — loses "image N" indexing);
main-app workflow templates are untouched by this entire wave.


**Pose generation runs on Klein 9B.** In Klein mode, "Generate Poses" (Create
and Clone) submits our flattening of the official
`vnccs-utils/workflows/VNCCS_Utils Pose Studio Klein9b.json` reference:

- Models: `flux-2-klein-9b-fp8` UNET + `qwen_3_8b` flux2 CLIP + `flux2-vae`
  (basename-resolved per worker; overridable via studio settings keys
  `klein_unet` / `klein_clip` / `klein_vae` / `klein_pose_lora`).
- **`VNCCS_PoseStudioKlein9b_V1.safetensors`** — VNCCS's OFFICIAL Klein pose
  LoRA, applied model-only, strength 1. Distributed via the VNCCS Model
  Manager repo **`MIUProject/VNCCS_PoseStudio_Klein`** (each worker needs it
  downloaded once; the run errors with that instruction if missing).
- Per pose: app-rendered pose capture (our three.js-parity renderer) scaled to
  1MP → reference latent 1; identity image → reference latent 2; empty Flux2
  latent at the pose image's size; Flux2Scheduler 4 steps, euler, cfg 1.
- Identity resolution: ACTIVE base version → newest cataloged final sprite →
  first clone reference. Prompt: "Apply the pose from image 1 to the character
  from image 2 …" + pose prompt + solid background.
- Multi-worker fan-out splits poses across workers like Qwen runs; outputs
  ingest as `creator/sprites` / `cloner/sprites` ("Base poses (Klein)" in the
  library) with full run-recipe tracking (poses × seed, ↻ Load).
- Template of the generated graph: `workflows/vnccs/KLEIN_POSES_TEMPLATE.json`.

**Known gaps in the Klein pose path (deliberate v1):** no upscaler / BG-remove
stage yet (raw Klein output with prompted solid background), and Klein runs do
NOT populate the VNCCS worker-side character store — so the Qwen clothes /
emotions meganodes cannot chain off them. Those steps need their own Klein
treatment (below) or a store-writer.

## The Qwen usage map

| Stage | Qwen mechanism | Qwen-only LoRA? | Klein path | Friction |
|---|---|---|---|---|
| Base preview (`preview_generate`) | Illustrious/Anima checkpoint (NOT QIE) | no | app already has Klein T2I workflows — swap is straightforward | **LOW** |
| Pose generation | QIE 2511 + `VNCCS_QIE2511_PoseStudio_ART_V5.9.5` | yes, but **official Klein twin exists** (`VNCCS_PoseStudioKlein9b_V1`) | ✅ DONE (v1.74.0) | **LOW — done** |
| Clone identity | QIE conditioned on a source-image GRID (no LoRA for identity itself) | no | Klein does multi-reference natively ("image 1/2/3") — grid trick may be unnecessary, possibly better | **LOW-MED** |
| Clothes (dress across poses) | QIE + `VNCCS_QIE2511_ClothesCore-RC3.x` | **yes — no Klein equivalent** | Klein 2-ref edit ("dress the character from image 1 in the outfit from image 2") — works but untrained for this; expect drift | **HIGH** |
| Remove clothes (clone naked branch) | QIE + ClothesCore, prompt "Dress character: White underwear" | **yes — no Klein equivalent** | Klein edit instruction can attempt it; quality unknown | **HIGH** |
| Emotions | FaceDetailer + `VNCCS_QIE2511_EmotionCore-RC1` | **yes — no Klein equivalent** | engine mode already has Klein face-masked inpaint (YuNet/Haar mask → Klein inpaint) — port that path | **MEDIUM** |
| Costume preview (`clothes_preview`) | ClothesDesigner (QIE + ClothesCore) host route | yes (ClothesCore) | would need an app-side Klein single-sprite dress preview | **HIGH** (same as clothes) |
| Wizards / clone analyze | Qwen2.5-VL **language** model (not image gen) | n/a | engine-agnostic — keep as-is | none |
| Upscale (SeedVR/GAN), BG remove (RMBG) | model-agnostic helper stages | no | reuse for Klein outputs (add to the Klein pose graph or post-process) | **LOW** |
| Utils extras (Multiple-Angles camera LoRA, QwenDetailer emotion) | QIE-specific LoRAs | yes | no Klein twins published | HIGH |

## Suggested order of attack

1. ✅ Poses (done — official LoRA support).
2. **BG-remove/upscale for Klein pose outputs** — bolt RMBG + optional GAN
   onto the Klein graph tail; makes Klein sprites drop-in equals of Qwen ones.
3. **Klein base preview** — reuse the app's Klein T2I for "Generate Character"
   in Klein mode (identity source quality drives everything downstream).
4. **Clone identity via native multi-ref** — feed 2-4 references directly
   instead of the grid; measure against Qwen clone.
5. **Emotions via Klein face inpaint** — port the engine-mode YuNet-mask path.
6. **Clothes last** — hardest (ClothesCore has no Klein twin). Options:
   prompt-only Klein 2-ref dressing, or keep clothes on Qwen (hybrid pipeline:
   Klein poses re-registered into the VNCCS store so ClothesCore can run).

## Watch-outs

- The VNCCS store dependency: Qwen clothes/emotions read sprites from the
  worker's character folders. Any stage moved to Klein breaks that chain until
  the replacement exists (or we write Klein outputs into the store layout).
- The Klein pose LoRA needs the vnccs-utils **Model Manager** download per
  worker (`MIUProject/VNCCS_PoseStudio_Klein`); our error message says exactly
  that when it's missing.
- Reference order matters in the Klein graph: pose = reference 1, identity =
  reference 2; both the positive AND negative conditioning carry the reference
  chain (node parity).


---

## Refbase "base from references" wave (1.114 -> 1.125, 2026-07-15) -- UNTESTED-until-live

The Klein Hybrid clone base preview was reworked to build the body from the reference
PHOTOS instead of a pose mannequin, plus a full de-clothing + face + realism toolchain.
All in `klein_poses.py` (graphs/resolvers), `vnccs_native.py` (clone-preview refbase
branch `_run_klein_clone_preview` + `/base/restyle`), `wizards.py`, and
`VNCCSNativePage.tsx` (base-preview controls; all auto-save).

- **1.114 refbase base** -- `build_klein_refbase_graph`: whole-person ReferenceLatentPlus
  channel (clothes mask ON = lock torso/chest/hip shape) from up to 4 body/full refs,
  empty init latent, neutral pose from prompt, face crop + RMBG. NO mannequin.
- **1.115** expanded `KLEIN_STRIP_NEGATIVE` with garment words.
- **1.116/1.117** PuLID source -> face crop; late-step body-ref release
  `klein_refbase_ref_end` (default 0.85) = "Strip release" control (Hold..0.65). Lower
  strips harder but carries less likeness.
- **1.118** FaceDetailer refine on the base (`face_refine` param + `_face_refine_node`,
  decode->refine->rmbg). base-local `klein_base_face_refine` + denoise/steps overrides,
  falling back to the gear globals `klein_face_refine_denoise/_steps`.
- **1.120** SAM3 article cleanup (`resolve_sam3_cleanup` + `_inject_sam3_cleanup`):
  `easy sam3ImageSegmentation` masks articles by TEXT -> GrowMaskWithBlur -> Flux2
  inpaint (SetLatentNoiseMask + SamplerCustomAdvanced) -> ImageCompositeMasked (only
  masked regions change). `klein_sam_cleanup` / `_prompt` / `_threshold`.
- **1.121** base "Global" buttons show the live value; global Refine steps in the gear panel.
- **1.122** `_options` reads BOTH object_info shapes -- classic `[[opt],cfg]` AND newer
  `["COMBO",{"options":[...]}]` (SAM3 loader uses the latter; model is `sam3-fp16.safetensors`).
- **1.123/1.124** anime2real-semi realism LoRA REMOVED from generation (kept the base
  render predictable) -> stacked in `build_klein_restyle_graph` (photoreal Switch Style,
  off the rendered active base -> new active base). "Use realism LoRA" checkbox
  (`use_realism_lora`) for realistic targets.
- **1.125** clone-analyze synthesis emits `worn_articles`; the frontend prefills the SAM
  "Articles to remove" box after analyze (works whether SAM cleanup is on or off).

**Reality checks:** PuLID is a NO-OP for Lorenzo's refs (`AUCUN VISAGE`) -- FaceDetailer
(ultralytics) is the base-face path. Body match confirmed good; strip / jewelry / face
still being tuned live. The whole wave is UNTESTED-until-Lorenzo-runs.
