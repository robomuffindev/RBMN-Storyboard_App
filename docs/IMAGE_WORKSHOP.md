# Image Workshop — free-form model playground + shared gallery

*Written at v1.199.2 (2026-07-20); **rendering updated in v1.276.45** — see "Worker
selection" below. Built + syntax-verified; final render validation is on a live LAN worker
(Lorenzo's) — see the caveats at the end.*

The **Image Workshop** is a project-independent place to experiment with any of
our image models outside a single character's flow: type a freestyle prompt (or
fill the creator-style character fields), optionally feed reference images, pick
a model, generate a batch, review the grid, and **save the keepers into one
shared, persistent gallery**. Saved images can be tagged, searched, downloaded,
deleted, or fed back in as references.

## Where to find it (two entry points, same panel)

The whole feature is one reusable component (`ImageWorkshopPanel`) surfaced two
ways, so the gallery is shared no matter how you open it:

- **🎨 Image Workshop** button in the **Character Studio** header (top-right, next
  to ⚙ Settings) — opens a full-screen **lightbox**.
- **Image Workshop** tab under **Tools** on the main project screen (Home → Tools),
  plus a standalone route at **`/image-workshop`**.

## Generate

- **Mode — Freestyle prompt:** type anything; it goes to the model verbatim.
- **Mode — Character gen:** the same creator-style slots used elsewhere
  (name / sex / age / race / skin / hair / eyes / face / body / height /
  aesthetic / details), composed into a prompt. A **Describe → auto-fill** wizard
  turns a sentence into filled fields (reuses `/api/studio/vnccs/wizard/character`
  — the VNCCS host wizard, Ollama fallback). An optional "extra prompt" is appended.
- **Models:** Z-Image Turbo, Krea 2 Turbo, Anima (anime; supports a negative
  prompt), Klein 9B, and Qwen-Image-Edit. The picker shows each model's live
  **online/offline** state and how many references it accepts.
- **Settings:** count (1–8), aspect presets + explicit width/height, 🔒 **seed
  lock** / 🔀 random, and (Anima only) a negative prompt.
- **Review grid:** each result can be selected, opened full-screen, or downloaded.
  **Save to gallery** persists the selected images.

## Reference images

Reference-capable models accept references uploaded from disk **or** picked from
the gallery:

- **Klein 9B** — 1–5 references, routed through `KLEIN_EDIT_ULTRA_WORKFLOW_{n}REF`.
- **Qwen-Image-Edit** — 1–2 references, routed through `STUDIO_QIE_EDIT`.
- **Z-Image / Krea 2 / Anima** — text-to-image only (references shown as N/A).

Each reference thumbnail has:

- ✕ to remove it,
- 🪄 **Describe** — vision-scans the image and fills the **Character-gen fields**
  (flips the panel to Character mode). Because the vision model lives on the VNCCS
  host, the reference bytes are re-uploaded there (`/api/studio/vnccs/upload`) and
  scanned via `clone-analyze`.

Any generated or saved image can be sent back in as a reference with **Use as
reference** (auto-switches to a reference-capable model if the current one is
text-only).

## 🏷 Category tags

Optional tags categorize what an image **is**. Presets — **Character · Pose ·
Item · SceneBG · Outfit · Face · Style · Prop** — plus free-text custom tags.

- **At save time:** the review Save bar has a "Tags (optional)" chip row; the
  selection is applied to every image saved in that batch.
- **In the gallery:** tags show on each tile; a 🏷 button opens a per-image editor
  (same presets + custom) to add/remove tags after the fact; a **filter chip row**
  (distinct tags, most-used first) narrows the grid; tags are searched alongside
  the prompt text.
- Normalisation: tags are trimmed, de-duped case-insensitively, and capped at 12
  tags × 40 chars per image. Images saved before tagging simply have none until
  you add them.

## API (`/api/image-workshop`)

| Method & path | Purpose |
|---|---|
| `GET /models` | Model catalog + live online state: `{models:[{value,label,refs,note,online}]}`. |
| `POST /generate` | Start a background batch. Body: `{mode:'freestyle'\|'character', model, prompt, name, fields, negative, count, width, height, seed?, references:[{source:'gallery'\|'upload', id}]}`. Returns `{gen_id, total, model, prompt, seed}`. |
| `GET /gen/{gid}` | Poll: `{status:'running'\|'done'\|'error', done, total, model, prompt, images:[{id,url,seed}], error}`. |
| `GET /gen/{gid}/image/{name}` | Serve an in-flight candidate. |
| `POST /upload` | Upload a reference image (multipart `file`). Returns `{id, source:'upload', url, name}`. |
| `GET /refs/{rid}/image` | Serve an uploaded reference. |
| `POST /save` | Save selected candidates to the gallery. Body: `{gen_id, image_ids:[], tags:[]}`. |
| `GET /gallery` | List. Query: `offset, limit, q, model, tag`. Returns `{total, items:[…], all_tags:[…]}`. |
| `GET /gallery/{gid}/image` | Serve a saved gallery image. |
| `POST /gallery/tags` | Replace an item's tags. Body: `{ids:[], tags:[]}`. |
| `POST /gallery/delete` | Delete. Body: `{ids:[]}`. |

Generation rides the same ComfyUI **dispatcher** and **workflow builders**
(`backend/services/comfyui/workflow.py`) the rest of the app uses — no new render
code. References are uploaded to the chosen worker fresh before each render
(ComfyUI uploads are per-worker). Worker selection: Klein needs the `klein`
capability, Qwen-Image-Edit the `vnccs` capability (best-effort, falls back to any
worker so a real error surfaces); the plain t2i models run on any healthy worker.

**⚡ A BATCH FANS ACROSS THE FLEET (v1.276.45).** It used to render N images SERIALLY on ONE
box: the loop called `select_worker` per image, and because these lanes submit straight to the
client instead of through `dispatcher.submit_job`, `in_flight` is permanently 0 and that sort
is a constant function — **asking per image in a loop pins rather than balances.** `_worker_pool()`
now returns every capable worker, images are assigned **round-robin up front** and rendered
concurrently via `asyncio.gather`. The run publishes `st["workers"]`.
⚠ `done` counts **COMPLETIONS**, not the loop index — with images finishing out of order, `i+1`
would have reported "6/6" while three were still rendering.

## Storage

Everything is global (not tied to a project), under
`<project_dir>/_libraries/workshop/`:

- `gallery/<id>.png` — saved gallery images; `gallery/index.json` — the metadata
  list (newest first: prompt, model, mode, seed, dims, fields, tags, created_at).
- `refs/<id>.png` — uploaded reference images (normalised to PNG).
- `_gen/_gen_<gid>/` — in-flight batches (`status.json` + `N.png`).

## Files

- **Backend:** `backend/api/image_workshop.py` (router `/api/image-workshop`);
  registered in `backend/main.py`.
- **Frontend:** `frontend/src/components/ImageWorkshop/` —
  `ImageWorkshopPanel.tsx` (reusable core), `ImageWorkshopLightbox.tsx`
  (Character Studio header), `ImageWorkshopPage.tsx` (`/image-workshop` +
  Tools tab), `imageWorkshopApi.ts`.
- **Wiring:** `App.tsx` (route), `Tools/ToolsPage.tsx` (tab),
  `VNCCSNative/VNCCSNativePage.tsx` (header button).

## Validation caveats (as of 1.199.2)

Built and syntax-checked; **not yet run on a live worker**. Two assumptions to
confirm on first real use: Qwen-Image-Edit selects a worker by the `vnccs`
capability, and the Klein `*REF` edit graphs expect `Reference N Image` nodes for
their references. Real ComfyUI node errors surface through the batch status if
either is off.
