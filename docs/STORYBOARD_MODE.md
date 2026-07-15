# Storyboard Mode

*Added v1.42.0 (2026-07-08). UNTESTED on a live worker; built + audited only.*

A full-window, ComfyUI-style zoomable/pannable canvas for working on a project's scenes visually —
auditioning imagery against audio, comparing scenes side by side, and regenerating scene frames.
It is a live **reflection of the timeline data**: everything created here (images, versions, active
selections) is the same underlying scene data the main editor uses — there is no separate storage
and no new backend.

## Entry point

A **Storyboard Mode** button in the per-project toolbar (top of `/project/:id`) opens the canvas at
`/project/:id/storyboard`.

## Canvas

- **Wheel to zoom** (anchored on the cursor), **drag to pan**, plus zoom in / out / reset controls.
- Scenes lay out **left-to-right in order** with arrows between them.
- Interactive controls (frame thumbnails, audio buttons) don't start a pan — the pan layer ignores
  anything marked `data-sb-interactive`.

## Scene card

Each scene shows:

- **First Frame** and **Last Frame** image slots (the scene's active `chosen_image_path` /
  `chosen_last_frame_path`), each with a **version-count badge**.
- The **scene name** and its **lyric / narration text** (`scene.parameters.lyrics`, with a
  word-timing fallback derived from the project lyrics).
- A **play / pause** button for the scene's sliced audio (`scene.parameters.audio_clip_path`); only
  one scene plays at a time.
- A live **"Rendering"** badge while a generation job for that scene is in flight.

## Regen modal

Tapping a frame opens a modal with:

- A large **preview** with prev/next cycling through that frame's versions.
- A **version strip** with the **active-state selector** used across the app — *Set Active* writes
  `chosen_image_path` / `chosen_last_frame_path` via `updateScene`; per-version delete.
- A **First / Last frame toggle** (locked while a render is in flight).
- A generate form: editable **prompt** + **Enhance**, the shared **ReferenceSelector**, a
  **workflow / custom-model** dropdown, **seed**, and **two-pass** — dispatched through the existing
  `POST /projects/:id/generate/image` endpoint (`frame_type` = first/last). A 120s safety timeout
  clears the in-flight state if a render fails.

## Live updates

`useJobEvents()` (mounted globally in `App.tsx`) streams job events into the Zustand store, so the
page derives per-scene "rendering" state from `store.jobs` and invalidates the `['scenes']` /
`['scene-versions']` React-Query caches when a job for the project finishes — thumbnails and version
counts refresh without a manual reload.

*Files:* `frontend/src/components/Storyboard/` — `StoryboardPage.tsx`, `SceneCard.tsx`,
`StoryboardSceneModal.tsx`, `useZoomPan.ts`.
