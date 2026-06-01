# Frontend Setup

For the codebase tour see [`README.md`](README.md). For data-flow / state patterns see [`ARCHITECTURE.md`](ARCHITECTURE.md).

## Prerequisites

- Node.js 18+ and npm
- A running backend on `localhost:8899` (or whatever `APP_PORT` is set to)

## Install

```bash
cd frontend
npm install
```

## Dev server

```bash
npm run dev
```

Vite starts on `http://localhost:5173` with hot reload. All `/api/*` requests are proxied to the backend (configured in `vite.config.ts`).

## Type check

```bash
npx tsc --noEmit
# or
npm run type-check
```

CI runs this; it should always be clean before committing.

## Production build

```bash
npm run build
```

Output lands in `frontend/dist/`. The backend serves this folder via its static-file mount, so for the packaged experience you only need to run `python run.py` from the project root after building.

## Common tasks

### Add a new backend endpoint

1. Add the Pydantic model + route in `backend/api/...`.
2. Add a matching function in `src/api/client.ts`.
3. If it returns a new shape, add an interface in `src/types/index.ts`.
4. Consume from the component with `useQuery` or `useMutation` — and remember to `setQueryData` after mutations if any other view depends on the same query.

### Add a new scene field

1. Add the field to the DB model in `backend/database/models.py`.
2. Make sure the Pydantic response model (`SceneResponse`) exposes it — silently-dropped fields are documented as gotcha #54.
3. Add to the TS `Scene` interface.
4. In SceneEditor (or wherever sets it), use `updateSceneAndSync(activeScene.id, { parameters: newParams })` — never the 2-line `updateScene + updateSceneInStore` pair without a React Query update.

### Add a new Zustand state field

1. Add to `AppState` interface in `src/store/index.ts`.
2. Add to the `create<AppState>(...)` initializer.
3. Add a setter if mutable.
4. Consume from components with **per-field selectors** (`useAppStore(s => s.fieldName)`), not whole-store destructure.

## Troubleshooting

### TS errors after pulling

Run `npm install` — types in `@/types` may have changed and need a refresh.

### "Cannot read property X of undefined" in console

Almost always a backend Pydantic response model missing a field the frontend reads. Check `backend/api/...py` response_model definitions vs `frontend/src/types/index.ts`. Cross-reference with gotcha #54 (`workflow_snapshot`) and #5 (`JobResponse` name collision).

### Wavesurfer doesn't load audio

Some pywebview configs block `fetch()`. We load via axios + blob URL — make sure you're using `WaveformDisplay`'s pattern, not raw wavesurfer.

### Live elapsed timer is off by hours

The display is using `new Date(backendTs).getTime()` somewhere. Switch to `parseBackendMs(backendTs) ?? Date.now()` from `@/utils/time`.

### "I set an active image and it didn't stick"

The handler is calling `updateScene` + `updateSceneInStore` without `setQueryData`. The AppLayout React-Query→Zustand mirror is overwriting your write with the stale cache. Convert to `updateSceneAndSync` — see `feedback_cache_coherence.md`.

### Auto Gen modal vanishes mid-run

Used to be a duplicate-modal bug (Timeline modal vs AppLayout modal). Fixed in 1.7.0; if you see it again, check that `useAppStore.setAutoGenOpen` is being used (not a local `useState`) and that `AppLayout` is rendering the modal based on the store flag.

### Backend feels slow during batches

Look for components subscribing to the whole Zustand store (`const { x, y } = useAppStore()`). Convert to per-field selectors. The 1.7.0 refactor converted 15 components; new components might re-introduce the pattern.

## Recommended editor setup

- **VS Code** with the official TypeScript / Tailwind extensions.
- **Editor config**: respect the existing `.editorconfig` (LF line endings preferred — the Edit tool has had bugs with CRLF on this mount, so don't introduce them).
- **ESLint / Prettier**: configs exist; run `npm run lint` if added.

## Where to find things

| You want to... | Look here |
|---|---|
| Add an export option | `AppLayout.tsx` → `function ExportModal` + `ExportRequest` type + backend `api/export.py` |
| Add an Auto Gen mode | `AppLayout.tsx` → `AUTO_GEN_OPTIONS` + backend `_VALID_MODES` in `api/generation.py` + handler branch in `_run_sequential_auto_gen` |
| Add a batch field | `BatchItemAddModal.tsx` + `BatchItemConfig` in `types/index.ts` + backend `BatchItemConfig` in `api/batch.py` + payload forward in `_process_single_item` |
| Add a debug endpoint | `backend/api/debug.py` (registered in `backend/main.py`) + optionally a flag in `tools/diag.py` |
| Find a known bug pattern | Project memory: `feedback_comfyui_gotchas.md`, `feedback_cache_coherence.md`, `feedback_v2v_join_split_fix.md` |
