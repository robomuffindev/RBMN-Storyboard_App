# Quick Start Guide

## Prerequisites
- Node.js 18+ and npm (or pnpm/yarn)
- Backend running on `localhost:8899`

## Installation

```bash
cd frontend
npm install
```

## Development Server

```bash
npm run dev
```

Opens at `http://localhost:5173` with hot reload enabled.

API calls are proxied to `http://localhost:8899` via Vite dev server.

## Build for Production

```bash
npm run build
```

Output in `dist/` directory ready to deploy.

## Type Checking

```bash
npm run type-check
```

Validates TypeScript without emitting files.

## Project Features

### Pages
- **ProjectList** (`/`) - Browse and create projects
- **AppLayout** (`/project/:id`) - Main editor with all tools
- **SettingsPage** (`/settings`) - Configure backend connections

### Key Components
- **Timeline** - Waveform editor with section markers
- **SceneEditor** - Edit image/video prompts per scene
- **AssetManager** - Upload and organize assets
- **GenerationPanel** - Real-time job queue
- **VideoPreview** - Built-in video player
- **SettingsPage** - API & server configuration

### State & Data
- **Zustand Store** - Global project, scenes, jobs state
- **React Query** - Server state, caching, sync
- **Axios** - Type-safe API client
- **Server-Sent Events** - Real-time job updates

## File Structure

```
frontend/
├── package.json                    # Dependencies & scripts
├── vite.config.ts                  # Vite build config (API proxy)
├── tsconfig.json                   # TypeScript config with @ alias
├── tailwind.config.js              # Tailwind theme (dark + section colors)
├── postcss.config.js               # PostCSS plugins
├── index.html                      # HTML entry (dark theme)
├── src/
│   ├── main.tsx                    # React entry + providers
│   ├── App.tsx                     # Route definitions
│   ├── index.css                   # Tailwind imports + base styles
│   ├── types/
│   │   └── index.ts                # TypeScript interfaces (matches backend)
│   ├── api/
│   │   └── client.ts               # Axios instance + endpoint functions
│   ├── store/
│   │   └── index.ts                # Zustand state management
│   ├── hooks/
│   │   └── useJobEvents.ts         # SSE subscription hook
│   └── components/
│       ├── Layout/
│       │   ├── AppLayout.tsx       # Main editor layout
│       │   └── ProjectList.tsx     # Project browser
│       ├── Timeline/
│       │   ├── Timeline.tsx        # Timeline container
│       │   ├── WaveformDisplay.tsx # WaveSurfer wrapper
│       │   └── SectionMarkers.tsx  # Section overlay
│       ├── SceneEditor/
│       │   └── SceneEditor.tsx     # Image/video/stems/lyrics tabs
│       ├── AssetManager/
│       │   └── AssetManager.tsx    # Asset upload & grid
│       ├── VideoPreview/
│       │   └── VideoPreview.tsx    # Video player
│       ├── GenerationPanel/
│       │   └── GenerationPanel.tsx # Job queue display
│       └── Settings/
│           └── SettingsPage.tsx    # API configuration
├── README.md                       # Feature overview
├── ARCHITECTURE.md                 # Design & data flow
└── SETUP.md                        # This file
```

## Development Workflow

1. **Start backend** - `python main.py` (port 8899)
2. **Start frontend** - `npm run dev`
3. **Edit components** - Changes hot-reload instantly
4. **Test APIs** - Use browser DevTools Network tab
5. **Build for production** - `npm run build`

## Troubleshooting

### API calls fail with 404
- Ensure backend is running on `localhost:8899`
- Check Vite proxy config in `vite.config.ts`
- Browser console shows full error details

### Waveform not displaying
- Audio URL must be accessible (cors-enabled)
- Check WaveSurfer config in `WaveformDisplay.tsx`
- Browser console shows load errors

### TypeScript errors
- Run `npm run type-check` to validate
- Check `tsconfig.json` for include/exclude paths
- Restart IDE TypeScript server

### Tailwind styles not applying
- Ensure `src/**/*.tsx` is in `content` in `tailwind.config.js`
- Rebuild CSS: delete cache and restart dev server
- Check class names match (no typos)

## Dependencies Overview

| Package | Purpose |
|---------|---------|
| react@18 | UI framework |
| react-router-dom@6 | Client routing |
| zustand@5 | State management |
| @tanstack/react-query@5 | Server state & caching |
| axios@1 | HTTP client |
| wavesurfer.js@7 | Audio visualization |
| tailwindcss@3 | Styling |
| lucide-react@0.383 | Icons |
| typescript@5 | Type safety |
| vite@5 | Build tool |

## Environment Setup

No `.env` files required for dev (Vite dev proxy handles `/api`).

For production deployment:
1. Update `vite.config.ts` proxy target if needed
2. Build: `npm run build`
3. Serve `dist/` folder (or deploy to static host)
4. Ensure backend is accessible to clients

## Next Steps

- Review `ARCHITECTURE.md` for component design
- Check `src/api/client.ts` for all backend endpoints
- Explore `src/store/index.ts` for state patterns
- Test with mock data using the UI
- Connect to real backend when ready

Happy building!
