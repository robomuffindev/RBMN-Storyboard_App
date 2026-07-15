# Mobile Mode

*Added v1.43.0 (2026-07-08). UNTESTED on a live worker; built + audited only.*

A dedicated, **touch-first** app for phone and tablet, built to work on projects from your phone over
the LAN. It hits the same backend as the desktop app (the axios base is the relative `/api`, so it
works as-is over the network), so all data stays in sync.

> **Not** the same as the CSS "Mobile Responsive Layout" (the desktop UI reflowed at 768/1024px
> breakpoints). Mobile Mode is a separate, purpose-built route tree with large tap targets and a
> bottom tab bar.

## Entry point

A **MOBILE MODE** card on the home page opens the app at `/mobile`. From your phone, browse to your
machine's LAN address (e.g. `http://<local-ip>:8899/mobile`).

## Screens (routes)

| Route | Screen |
|---|---|
| `/mobile` | **Projects** — tappable project list. |
| `/mobile/p/:id` | **Overview** — scene/asset counts, quick-nav tiles (incl. Storyboard), and an **Auto-Generate** bottom sheet (all 7 pipeline modes) with a live progress bar, current scene/step, Stop, and a link into batch details. |
| `/mobile/p/:id/scenes` | **Scenes** — per-scene cards with First/Last frame thumbnails (+version count), lyric/narration text, and audio playback; tapping a frame opens the **reused Storyboard regen modal** (versions, active selector, prompt/refs/model/seed/two-pass). |
| `/mobile/p/:id/characters` | **Cast** — create/edit characters (name + description), generate their base image (polled), version strip with set-active + delete, and character delete. |
| `/mobile/p/:id/queue` | **Queue** — live generation jobs (progress + cancel/retry/delete) read from the global SSE store, inline auto-gen status, and batch-run cards. |
| `/mobile/batch/:batchRunId` | **Batch detail** — live progress, per-worker `active_jobs` render %, latest asset preview, activity feed, error log, and resume/cancel. |

Every project screen shares a **bottom tab bar** (Overview / Scenes / Cast / Queue) and a sticky
header with a back button. Safe-area insets are respected.

## Notes / gotchas

- The **Cast** save path does a **full-object** `saveConcept` spread (`{...concept}` + per-character
  `{...c}`) so it never wipes the project's other concept settings or per-character provenance —
  `saveConcept` is a full replace on the backend.
- Character generation uses `generateCharacterImage` (`prompt_override`), polled for new versions, with
  a 120s safety timeout so a failed render can't leave the button stuck.
- The Queue seeds jobs via `getJobs(projectId)` and then reads the live `store.jobs` populated by the
  global `useJobEvents()` SSE; the Scenes grid invalidates `['scenes']` when a project job completes so
  thumbnails refresh live.

*Files:* `frontend/src/components/Mobile/` — `MobileShell.tsx`, `MobileSheet.tsx`, `useProjectData.ts`,
`MobileProjects.tsx`, `MobileProject.tsx`, `MobileScenes.tsx`, `MobileCharacters.tsx`, `MobileQueue.tsx`,
`MobileBatchDetail.tsx`.
