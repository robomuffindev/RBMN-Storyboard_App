# RBMN Storyboard — Frontend

React 18 + TypeScript + Vite frontend for **Robomuffin Idea Factory**. Talks to the FastAPI backend at `/api` and consumes Server-Sent Events for live job progress.

For the user-facing feature list and screenshots, see the project root [`README.md`](../README.md). This file documents the frontend codebase itself.

## Tech stack

| Layer | Choice |
|---|---|
| Framework | React 18 |
| Routing | React Router 6 |
| Server state | React Query 5 (`@tanstack/react-query`) |
| Local state | Zustand 5 (per-field selectors — **not** whole-store destructure; see Architecture) |
| HTTP | Axios |
| Realtime | EventSource / SSE (`/api/jobs/stream`) |
| Waveform | wavesurfer.js 7 (audio loaded as ArrayBuffer via axios → blob URL — bypasses pywebview fetch restrictions) |
| Styling | TailwindCSS 3 + a small set of CSS variables for theme colors |
| Icons | lucide-react |
| Build | Vite 5 |

## Install + run

```bash
cd frontend
npm install
npm run dev          # Vite dev server on :5173, /api proxied to :8899
npm run build        # production bundle to dist/
npx tsc --noEmit     # type check
```

In production the backend serves the built `dist/` as a SPA mount, so you only need to run the backend (`python run.py`).

## Project layout

```
frontend/src/
├── api/
│   └── client.ts                          Axios instance + every backend endpoint
├── store/
│   └── index.ts                           Zustand store (per-field selectors)
├── hooks/
│   ├── useJobEvents.ts                    SSE subscription + reconnect with cache sync
│   └── useBackingTrackPlayer.ts           Backing-track audio engine for narration mode
├── utils/
│   ├── time.ts                            parseBackendDate / parseBackendMs (Z-normalize)
│   └── brokenImage.ts                     Img onError fallback
├── types/
│   └── index.ts                           TS interfaces mirroring backend Pydantic models
├── App.tsx                                Route definitions
├── main.tsx                               React entry + QueryClient + Router
└── components/
    ├── Layout/
    │   ├── AppLayout.tsx                  Main editor shell + Export modal + Auto Gen modal
    │   ├── HomePage.tsx                   Landing
    │   └── ProjectList.tsx                Project browser + Batch Mode entry
    ├── Timeline/
    │   ├── Timeline.tsx                   Toolbar + transport + zoom
    │   ├── WaveformDisplay.tsx            wavesurfer integration
    │   ├── TimelineOverlay.tsx            Scene boundary overlay
    │   └── SectionMarkers.tsx             Detected sections
    ├── SceneEditor/
    │   ├── SceneEditor.tsx                Tabbed editor (Image / Video / Stems / Lyrics / Tools / Image Movement / Prompt)
    │   └── ReferenceSelector.tsx          Character + extra ref image picker
    ├── ConceptPanel/
    │   ├── ConceptPanel.tsx               Song title, concept, style, image direction, characters
    │   └── CharacterCreatorModal.tsx      Mini image generator with version gallery
    ├── VideoFlowPanel/
    │   ├── VideoFlowPanel.tsx             Per-scene LLM-generated storyboard ideas
    │   └── FlowGenerationStatus.tsx       Progress indicator
    ├── VideoPreview/
    │   └── VideoPreview.tsx               Per-scene preview (FF/LF aware, subtitle overlay)
    ├── AssetManager/
    │   ├── AssetManager.tsx               Grid view + lightbox
    │   ├── AssetManageModal.tsx           Bulk-select + delete
    │   └── AssetPickerModal.tsx           Picker used by Asset Generator and Reference Selector
    ├── AssetGenerator/
    │   └── AssetGeneratorModal.tsx        Standalone image/video gen outside scenes
    ├── AudioSetup/
    │   └── AudioSetup.tsx                 Upload audio, Demucs, Whisper, sections, lyrics
    ├── BackingTrackTimeline/
    │   └── BackingTrackTimeline.tsx       Backing track bars below main timeline (narration mode)
    ├── GenerationPanel/
    │   └── GenerationPanel.tsx            Live job queue with cancel/retry/elapsed
    ├── BatchPreviewPIP/
    │   └── BatchPreviewPIP.tsx            Floating draggable PIP showing last completed asset
    ├── BatchMode/
    │   ├── AutoGenStatusBar.tsx           Bottom-of-screen status pill + minimize
    │   ├── BatchesDashboard.tsx           /batches list of persistent BatchRuns
    │   ├── BatchRunDetail.tsx             /batches/:id activity feed + per-scene status
    │   ├── BatchItemAddModal.tsx          Configure a batch item
    │   └── BatchQueuePanel.tsx            Queue display + run button
    ├── Settings/
    │   └── SettingsPage.tsx               Backend + GPU + ComfyUI + LLM + RunPod + LTXDirector
    └── ErrorBoundary.tsx
```

## API integration

`src/api/client.ts` exports one function per backend endpoint. Typical pattern:

```ts
export const updateScene = (projectId: string, sceneId: string, data: Partial<Scene>) =>
  api.put<Scene>(`/projects/${projectId}/scenes/${sceneId}`, data);
```

The Vite dev server proxies `/api/*` to `http://localhost:8899` so dev and production share the same URLs.

Real-time job progress comes through SSE at `/api/jobs/stream`. `useJobEvents.ts` opens the EventSource once, reconnects with exponential backoff, and after a reconnect refreshes scene data from REST (and crucially updates BOTH the Zustand store AND the React Query cache — see Architecture for why).

## Environment

No frontend `.env` required for dev — Vite's proxy handles `/api`. For custom backend ports / hosts, edit `vite.config.ts`.

## Where to look next

- [`ARCHITECTURE.md`](ARCHITECTURE.md) — component data flow, the Zustand + React Query coherence pattern, scene-update helper, render performance notes.
- [`SETUP.md`](SETUP.md) — install and dev workflow with troubleshooting.
- Project root [`CHANGELOG.md`](../CHANGELOG.md) — release notes including frontend-specific fixes.
