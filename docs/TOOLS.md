# Tools Section — Pose & Expression Libraries + Generate Sample

*Status as of v1.44.0 (2026-07-08). All UNTESTED on a live worker; built + adversarially audited only.*

The **Tools** section (Home → Tools, route `/tools`) is a project-independent, reusable
asset-library system for building and organizing pose and expression references that the
Character Studio and scene generators consume. Everything is global (not tied to a project)
and stored under `<project_dir>/_libraries/`.

## Views

- **Pose Organizer** — scan a server folder path or an uploaded `.zip` of pose files; each file
  is auto-classified (keypoints / openpose image / depth / natural), OpenPose keypoints are
  converted to the canonical 18-joint schema, geometry-based tags are auto-assigned, near-duplicates
  are deduped, and the reviewed candidates are committed to the Pose Library. Also **Extract from
  Images**: run DWPose on arbitrary photos/art to pull real editable keypoints.
- **Pose Library** — browse by category / tag / search, view in a lightbox, render HD grey-mannequin
  thumbnails on a worker, push selected poses to the Character Studio pose picker, delete, and
  export / import portable pose packs. Poses are stored canonically as **18-joint keypoints**, so a
  single pose re-renders to any control format (OpenPose skeleton or mannequin) on demand.
- **Expression Library** — reusable facial expressions stored as **name + natural-language prompt**
  (the form the emotion engines consume) with optional reference image, category, and tags. Seed the
  library from the bundled 157-emotion VNCCS catalog, add your own, and edit prompts inline.
- **Image Workshop** (1.199.0+) — a free-form model playground with one shared, persistent gallery
  (freestyle / character-gen prompts, reference images, category tags). It is the same panel exposed
  in the Character Studio header and at `/image-workshop`; see **`docs/IMAGE_WORKSHOP.md`** for the
  full guide.

## Generate Sample (1.44.0)

Instead of always sourcing a reference elsewhere, each of the three views has a **Generate Sample**
button that leverages your own image models to produce candidate references:

1. Enter a **prompt**, pick a **model** (Z-Image Turbo / Krea 2 Turbo / Anima / Klein), a **count**
   (1–8), and a size. The **Isolate subject** toggle (default on) auto-appends the right framing
   directives — full-body on a plain background for poses, head-and-shoulders for expressions —
   plus matching negatives. (The negative-prompt field only affects Anima; the other models have no
   negative-conditioning node.)
2. Results stream into a **grid gallery**; click any tile to view it large in a lightbox.
3. **Multi-select** the ones you want, add a **category, name, and tags**, and commit:
   - **Poses** run the chosen image(s) through **DWPose** on a worker to extract real, editable
     keypoints (auto-tagged + deduped), stored canonically like every other pose.
   - **Expressions** store the chosen crop as the entry's reference image plus a natural-language
     prompt (defaults to your generation prompt).

**Worker requirements:** generation runs on any healthy image worker; **Klein** needs the `klein`
capability, and committing a **pose** needs a **DWPose**-capable worker (`comfyui_controlnet_aux`,
`DWPreprocessor`). The modal warns when a DWPose worker is offline.

## API (`/api/tools`)

Pose Organizer / Library / Expression Library endpoints (scan, commit, list, facets, thumbnail,
control, patch, delete, to-presets, export, import, hd-thumbnails, extract) plus the Generate Sample
endpoints:

| Method & path | Purpose |
|---|---|
| `POST /api/tools/sample/generate` | Start a background generation. Body: `{kind:'pose'\|'expression', prompt, model, count, width, height, seed?, negative?, isolate}`. Returns `{gen_id, total, kind, model}`. |
| `GET /api/tools/sample/{gen_id}` | Poll status: `{status:'running'\|'done'\|'error', done, total, images:[{id,url}], prompt, error}`. |
| `GET /api/tools/sample/{gen_id}/image/{name}` | Serve a generated candidate image. |
| `POST /api/tools/sample/{gen_id}/commit` | Commit selected images. Body: `{kind, image_ids:[], category, name?, tags:[], natural_prompt?, detect_hands?, detect_face?}`. Poses → DWPose keypoints → Pose Library; expressions → reference crop → Expression Library. |
| `GET /api/tools/expression-library/{id}/thumbnail` | Serve an expression's reference image. |

## Storage / DB

`_libraries/pose/{thumbs,sources}`, `_libraries/expression/{thumbs,refs}`, `_libraries/_scans`
(includes transient `_gen_<id>` dirs per generation). Tables: `pose_library`, `expression_library`,
`library_scan` (created by `create_all`; no migration needed — `reference_image_rel` /
`source_image_rel` already exist).

*Frontend:* `frontend/src/components/Tools/` (`ToolsPage`, `PoseOrganizerView`, `PoseLibraryView`,
`ExpressionLibraryView`, `SampleGenerateModal`, `toolsApi.ts`). *Backend:* `backend/api/tools.py`,
`backend/services/tools/pose_classify.py`, `backend/services/character_studio/pose_renderer.py`.
