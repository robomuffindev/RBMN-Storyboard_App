# PuLID / face-identity path — notes & deferred work

_Last verified: 2026-07-16 (v1.127.0). Applies to VNCCS Klein Hybrid: base
preview (`build_klein_refbase_graph`) and pose runs (`build_klein_pose_graph`)._

## TL;DR — current status: WORKING

PuLID identity injection is **functioning** on the reference-driven base. The
face likeness on the base comes from three cooperating things: the
ReferenceLatentPlus body/face reference latents, PuLID (identity adapter), and
FaceDetailer (post-render face refine). None of these is currently broken.

## Reading the worker log correctly (this bit us)

The ComfyUI-PuLID-Flux2 node prints, on success:

```
🟢 PuLID Flux2 | KLEIN_9B | strength=1.40 | face=0
```

`face=0` is the **face _index_** it used (`face_index`, default 0 = the largest
detected face) — it is **NOT** a count of faces. Detection FAILURE prints a
different line and returns early:

```
⚠️ [PuLID] AUCUN VISAGE → retour sans modification
```

So: **`face=0` + no `AUCUN VISAGE` == a face WAS detected and identity WAS
applied.** Do not read `face=0` as "zero faces." (We did, once, and chased a
non-bug.)

## What actually fixed detection (v1.126, Option A)

Earlier, detection genuinely failed (`AUCUN VISAGE`) because we fed PuLID the
**app-side crop** (`face_file`). Without cv2 installed on the app host that crop
was a blind heuristic upper-center box, and InsightFace found no face in it.

`build_klein_refbase_graph` now takes `pulid_image` and feeds PuLID the **full
face-role reference image** (`pulid_image=_face_name` from the clone-preview
branch) — InsightFace runs its own detect+align on the full image. This matches
`build_klein_pose_graph`, which already fed the full identity image. That change
is what got detection working. The app-side crop is still used for the
face-detail reference latent (`face_enc`), not for PuLID.

## App-side face detection requires opencv (cv2)

`backend/services/character_studio/faces.py` gates all detection on `_HAVE_CV2`.
If `cv2` (opencv-python) is not installed in the **app venv**, `crop_face`
returns `None` and every identity crop falls back to the heuristic box —
degrading the face-detail latent (and, pre-Option-A, PuLID too).

- Requirement added to `requirements.txt`: `opencv-python`.
- Installed on the app host 2026-07-16 (`pip install opencv-python`).
- Verify: `python -c "import cv2; print(cv2.__version__, hasattr(cv2,'FaceDetectorYN'))"`
  in the app venv. YuNet model auto-downloads on first crop (needs internet).

## Upscaled references ARE used

`cloneRefsForGen()` (frontend) substitutes the enhanced/upscaled version of each
reference whenever **Enhance is on** and an enhanced entry exists — this flows
through to the backend `names`, so `_face_name` (hence PuLID + the face-detail
latent) uses the **upscaled** image when available. If upscaled refs aren't
being used, check that the Enhance toggle is on. (Note: the small "clone run
will use originals" UI hint is misleading — the run uses upscaled whenever
Enhance is on, regardless of the original/upscaled *view* toggle.)

---

## DEFERRED TWEAK — RGB→BGR in the PuLID node (optional, NOT deployed)

**Status: known, understood, intentionally NOT applied. Revisit only if we want
to squeeze more identity fidelity.**

### The finding
`ComfyUI-PuLID-Flux2/pulid_flux2.py`, `ApplyPuLIDFlux2.apply()` (~line 399):

```python
img_np = (image[0].numpy() * 255).astype(np.uint8)   # RGB (ComfyUI order)
faces = face_analysis.get(img_np)                    # InsightFace expects BGR
```

InsightFace/antelopev2 is trained on **BGR** (cv2 convention). Feeding **RGB**
still *detects* a face (SCRFD is robust to the channel swap), but the identity
**embedding** (`face.embedding`, which carries the likeness) is computed on
swapped red/blue channels → a systematically-off identity. The EVA-CLIP path a
few lines down correctly uses RGB, so only the InsightFace call is affected.

### Verified with `pulid_face_probe.py`
Running antelopev2 standalone in the worker's python on MA5's references:
- proper **BGR uint8** → `faces = 1`, det_score ~0.82–0.86 (strong)
- **RGB uint8** (what the node feeds) → `faces = 1` (detects, but embedding wrong)
- **float 0-1** → `faces = 0` (sanity: normalized floats defeat it)

So detection is NOT the problem; the embedding *channel order* is a latent
correctness nit. Expected likeness impact: real but likely **modest** (face
embeddings are dominated by geometry). A/B before committing to maintain it.

### The patch (preserved so we can re-apply / fork)
Replace the two lines above with:

```python
img_np = (image[0].numpy() * 255).astype(np.uint8)
# InsightFace/antelopev2 is trained on BGR; ComfyUI IMAGE is RGB. Feed BGR for a
# correct identity embedding; fall back to RGB so detection can't regress.
try:
    import cv2 as _cv2
    _img_if = _cv2.cvtColor(img_np, _cv2.COLOR_RGB2BGR)
except Exception:
    _img_if = img_np[..., ::-1].copy()
faces = face_analysis.get(_img_if)
if not faces:
    faces = face_analysis.get(img_np)
```

(The delivered `pulid_flux2_PATCHED.py` also adds a `det_score` log line.)

### Why it's deferred
It's a hand-edit to a **third-party node**, so a `git pull` / ComfyUI-Manager
update overwrites it — **not update-safe**. Options if we revisit:
1. Re-patch each worker's node, keep `pulid_flux2.py.bak`, re-apply after updates.
2. **Report upstream** to the node author (the real fix — ships in the node).
3. **Fork** ComfyUI-PuLID-Flux2 and run our own node (most control, most upkeep).

Decision (2026-07-16): leave as-is; base results are good. Node lives only on the
**workers** (separate machines, `E:\ComfyMaster\...\custom_nodes\ComfyUI-PuLID-Flux2`),
not in this app repo.

---

## `pulid_face_probe.py` — the diagnostic tool

A read-only probe that runs the worker's own InsightFace + antelopev2 standalone
on reference images, to isolate "node glue" vs "the image" for any future
`face=0`/likeness question. MA5's paths are baked in; pass image paths to
override. **Run with the worker's python** (the app venv has no insightface):

```
E:\ComfyMaster\V1\ComfyUI_windows_portable\python_embeded\python.exe pulid_face_probe.py
```

Reads: `PROPER faces>=1` → InsightFace fine, look at the node; `faces=0` → look at
the image/scale; `float/RGB differ` → normalization/channel order.

The probe and the node copies are kept as **local scratch** (gitignored:
`/pulid_*.py`), not committed — this note preserves the knowledge.
