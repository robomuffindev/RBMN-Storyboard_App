# RBMN Storyboard - Frontend

A professional React-based frontend for the RBMN AI music video / narration video creation tool.

## Features

- **Project Management**: Create, manage, and organize video projects
- **Timeline Editor**: Interactive waveform timeline with section markers
- **Scene Editor**: Edit prompts, parameters, and generation settings per scene
- **Asset Manager**: Upload and organize reference images, audio, and other assets
- **Generation Panel**: Real-time job queue tracking with SSE updates
- **Video Preview**: Integrated video player with playback controls
- **Settings**: Configure ComfyUI servers, Whisper, and LLM APIs
- **Dark Theme**: Professional dark UI designed for video editing workflows

## Tech Stack

- **React 18** - UI framework
- **React Router 6** - Client-side routing
- **Zustand** - Global state management
- **React Query** - Server state & data fetching
- **WaveSurfer.js** - Audio waveform visualization
- **Tailwind CSS** - Styling
- **TypeScript** - Type safety
- **Vite** - Build tool

## Installation

```bash
cd frontend
npm install
```

## Development

```bash
npm run dev
```

The app will start on `http://localhost:5173` with the API proxied to `http://localhost:8899`.

## Build

```bash
npm run build
```

Output goes to the `dist/` directory.

## Project Structure

```
src/
├── api/
│   └── client.ts          # Axios instance & API functions
├── components/
│   ├── Layout/            # Main layout components
│   ├── Timeline/          # Waveform & timeline editor
│   ├── SceneEditor/       # Scene editing UI
│   ├── AssetManager/      # Asset management
│   ├── VideoPreview/      # Video player
│   ├── GenerationPanel/   # Job queue display
│   └── Settings/          # Settings page
├── hooks/
│   └── useJobEvents.ts    # SSE subscription hook
├── store/
│   └── index.ts           # Zustand store
├── types/
│   └── index.ts           # TypeScript interfaces
├── App.tsx                # Main app routing
├── main.tsx               # React entry point
└── index.css              # Global styles
```

## API Integration

The frontend communicates with the backend via:
- **REST API** at `/api` (proxied in dev)
- **Server-Sent Events** at `/api/jobs/stream` for real-time job updates

See `src/api/client.ts` for all available endpoints.

## State Management

Global state is managed with Zustand in `src/store/index.ts`:
- Current project
- Scenes, sections, assets
- Active scene selection
- Playback position & state
- View mode (sections vs. scenes)
- Job queue

## Environment Variables

Create a `.env.local` file if needed:
```
VITE_API_URL=/api
```

## Type Safety

Full TypeScript support with interfaces matching the backend models in `src/types/index.ts`.
Run `npm run type-check` to validate types.
