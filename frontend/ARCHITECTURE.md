# Frontend Architecture

This document covers data flow, state coherence, and the most important patterns. For the component tree and file layout see [`README.md`](README.md). For the user-facing feature list see the project root [`README.md`](../README.md).

## State boundary

The frontend has two pieces of state and they have to stay in sync.

### React Query (`@tanstack/react-query`)

Holds **server state**: project, scenes, sections, assets, settings, batch runs, persistent BatchRuns. Owned by `AppLayout.tsx` via `useQuery({ queryKey: ['scenes', id], queryFn: getScenes })` and similar. It's the source of truth when the page loads or refocuses.

### Zustand (`@/store`)

Holds **UI state and a mirror of the most-used server data** for fast component access without going through React Query selectors. Fields: `currentProject`, `scenes`, `sections`, `assets`, `jobs`, `activeScene`, `playbackPosition`, `isPlaying`, `narrationVolume`, `backingMasterVolume`, `lastCompletedAsset`, `batchPreviewVisible`, `autoGenOpen`, mixer volumes.

### The mirror

`AppLayout.tsx` has:

```ts
useEffect(() => { setScenes(stableScenes as Scene[]); }, [stableScenes, setScenes]);
```

This pushes React Query data into Zustand on every change. **One-way: React Query → Zustand.** If you only write to Zustand, the next time the mirror fires (re-render, navigate-away-and-back, focus refetch) it overwrites your local write with the stale React Query value.

### The pattern: `updateSceneAndSync`

Every scene mutation must update all three: backend, React Query cache, Zustand. `SceneEditor.tsx` exports a helper:

```ts
const updateSceneAndSync = useCallback(
  async (sceneId: string, patch: { parameters?: any; [k: string]: any }) => {
    if (!currentProject) return;
    await updateScene(currentProject.id, sceneId, patch);
    queryClient.setQueryData(['scenes', currentProject.id], (old: any) =>
      Array.isArray(old)
        ? old.map((sc: any) => (sc.id === sceneId ? { ...sc, ...patch } : sc))
        : old
    );
    useAppStore.getState().updateSceneInStore(sceneId, patch);
  },
  [currentProject, queryClient]
);
```

All 24 scene-write call sites in `SceneEditor.tsx` go through this helper. **Never** call `updateScene` + `updateSceneInStore` as a 2-line pair without also calling `setQueryData` — the cache will go stale and the user's "Set Active Image" will revert when they navigate away and back. Same pattern in `useJobEvents.ts` SSE reconnect handler.

For non-scene data (assets, sections, lyrics) the same principle applies: any direct Zustand setter that isn't matched by a React Query update will eventually get overwritten by the mirror. Prefer invalidating the query, or call both.

## Zustand selectors, not whole-store destructure

Components subscribe to the store with **per-field selectors**:

```ts
// ✅ Re-renders only when scenes change
const scenes = useAppStore(s => s.scenes);

// ❌ Re-renders on ANY store change
const { scenes, jobs, activeScene } = useAppStore();
```

The second pattern was used everywhere up through 1.6.x and caused re-render storms during SSE batches (every `job_progress` event re-rendered the entire AppLayout tree). 15+ components were converted to per-field selectors in 1.7.0. Linters won't catch the whole-store destructure — when adding a new component, copy the pattern from a sibling.

`useAppStore.getState()` and `useAppStore.setState(...)` are intentional non-reactive escape hatches (used in callbacks where you just need to read or write once without subscribing).

## Routing

```
/                            ProjectList
/project/:id                  AppLayout (main editor)
/batches                      BatchesDashboard
/batches/:batchId             BatchRunDetail
/settings                     SettingsPage
```

Defined in `App.tsx`. AppLayout is the largest surface — it owns scenes/sections/assets queries, the Export modal, the Auto Gen modal, the Asset Generator modal, the Timeline, SceneEditor, ConceptPanel, VideoFlowPanel, BackingTrackTimeline, VideoPreview, and GenerationPanel.

## Real-time updates (SSE)

`useJobEvents.ts` mounts once at the app level and:

1. Opens an EventSource on `/api/jobs/stream`.
2. Translates job events into Zustand store updates (`addJob`, `updateJob`).
3. On reconnect (after backend restart, network blip, etc.), refetches scenes from REST AND writes the result to both Zustand and the React Query cache. Without the cache write, the AppLayout mirror would shortly overwrite the fresh data with the stale cache.
4. Maintains exponential backoff with jitter.
5. Empty dependency array — the EventSource is created once. The hook uses `useAppStore.getState()` / `getQueryClient` from refs for all access, never via the dependency array.

If `useJobEvents` had a Zustand value in its `useEffect` deps, the EventSource would tear down and reconnect on every state update. That bug is documented in `feedback_comfyui_gotchas.md` (gotcha #4) and resolved by the empty deps pattern.

## Timestamp handling

The backend emits timestamps as `datetime.utcnow().isoformat()` — **without** a `Z` suffix. JavaScript's `new Date(ts)` interprets non-Z strings as local time, which makes every "elapsed" / "x seconds ago" computation wrong by the user's TZ offset.

`src/utils/time.ts` exports `parseBackendDate(ts)` and `parseBackendMs(ts)` that append `Z` if missing before parsing. **Always** use these for backend timestamps. Direct `new Date(backendField)` is a bug.

## Export modal

`AppLayout.tsx` contains the Export modal inline (search for `function ExportModal`). The interesting bits:

- **Re-export options block**: checkboxes for "Audio-only re-mix", "Export audio stems", "Stems only", "Force full recreate". `audioOnlyRemix` and `forceRecreate` are mutually exclusive — toggling `forceRecreate` clears `audioOnlyRemix` in the same setState.
- **Recovery banner**: shown when `/export/scan` finds recoverable artifacts on disk from a previous run.
- **Chunk gallery**: chunk video cards appear as the backend reports each finished chunk via the export progress poll.

The payload to `POST /api/projects/{id}/export` differs between music mode and narration mode. Narration mode includes subtitle settings, backing track mix params, audio normalization, and all four export flags. Music mode includes only `force_recreate` and `stems_only`.

## Wavesurfer + pywebview

`WaveformDisplay.tsx` loads audio with axios as an `ArrayBuffer`, converts to a blob URL, and feeds wavesurfer the blob URL instead of the raw API URL. Some pywebview / browser-extension setups block `fetch()` calls but allow XHR/axios; the blob-URL workaround avoids the block.

## Build

- Vite proxies `/api/*` to the backend in dev (`vite.config.ts`).
- Production build outputs to `frontend/dist/`. The backend's static-file mount serves this directly when running `python run.py`.
- TS strict mode is on. `npx tsc --noEmit` runs in CI / pre-commit.

## Things that have bitten us

- **Whole-store destructure**: `const { x, y, z } = useAppStore()` subscribes to the entire store. Use `const x = useAppStore(s => s.x)`.
- **Bare `updateScene` + `updateSceneInStore` pair**: skip the React Query cache write at your peril. Use `updateSceneAndSync`.
- **`new Date(backendTs)` without `Z` normalize**: every relative-time display is wrong by your local TZ offset. Use `parseBackendDate` / `parseBackendMs`.
- **Zustand deps in `useEffect` for `useJobEvents`**: EventSource tears down and reconnects on every state change. Use empty `[]` and `useAppStore.getState()`.
- **EventSource over a long-lived window**: `ConnectionResetError` after hours on Windows. `useJobEvents` detects reconnect and refreshes scene data; on long runs without a reconnect, F5 also works.
- **Auto Gen modal duplication**: there used to be two AutoGenModals (Timeline + AppLayout). Now there's one in AppLayout, opened via the Zustand `autoGenOpen` flag. The Timeline button toggles the same flag.

See `feedback_comfyui_gotchas.md` and `feedback_cache_coherence.md` in the project memory for the long version.
