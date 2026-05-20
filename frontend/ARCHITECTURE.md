# Frontend Architecture

## Component Hierarchy

```
App
├── ProjectList (/)
│   └── Project Cards Grid
│       └── New Project Modal
└── AppLayout (/project/:id)
    ├── Toolbar
    │   ├── Project Name & Mode Badge
    │   ├── View Mode Toggle (Sections/Scenes)
    │   ├── Export Button
    │   └── Settings Link
    ├── Left Panel (64px width)
    │   ├── Scene List OR Asset Manager (tab toggle)
    │   └── SceneList / AssetManager
    ├── Middle/Right Panels (flex-1)
    │   ├── VideoPreview (flex-1)
    │   └── SceneEditor (flex-1)
    │       ├── Image Tab
    │       │   ├── Image Prompt Textarea
    │       │   ├── Negative Prompt Textarea
    │       │   ├── Width/Height Inputs
    │       │   ├── Seed Input
    │       │   ├── Enhance Prompt Button
    │       │   └── Generate Image Button
    │       │       └── Generation History Navigation
    │       ├── Video Tab
    │       │   ├── Video Prompt Textarea
    │       │   ├── Duration/Framerate Inputs
    │       │   └── Generate Video Button
    │       ├── Stems Tab
    │       │   ├── Vocals Checkbox
    │       │   ├── Drums Checkbox
    │       │   ├── Bass Checkbox
    │       │   ├── Other Checkbox
    │       │   └── Preview Mix Button
    │       └── Lyrics Tab
    │           └── Lyrics Display
    ├── Generation Panel (w-80)
    │   ├── Active Jobs Section
    │   ├── Completed Jobs Section
    │   ├── Failed Jobs Section
    │   └── Job Cards
    │       ├── Status Icon + Progress Bar
    │       ├── Cancel Button (for active)
    │       └── Retry Button (for failed)
    └── Timeline (h-64)
        ├── Playback Controls
        │   ├── Play/Pause Button
        │   ├── Time Scrubber
        │   ├── Current/Total Time Display
        │   ├── Zoom Controls
        ├── Waveform Display
        │   └── WaveSurfer Instance
        ├── Section Markers Overlay
        │   └── Color-coded Section Labels
        └── Active Scene/Section Info
```

## State Management (Zustand)

```typescript
AppState {
  // Project data
  currentProject: Project | null
  scenes: Scene[]
  sections: SongSection[]
  assets: Asset[]
  jobs: Job[]
  
  // UI state
  activeScene: Scene | null
  playbackPosition: number
  isPlaying: boolean
  viewMode: 'sections' | 'scenes'
  
  // Actions
  setCurrentProject()
  setScenes()
  setSections()
  setAssets()
  addJob()
  updateJob()
  removeJob()
  setActiveScene()
  setPlaybackPosition()
  togglePlay()
  setViewMode()
}
```

## Data Flow

### Project Loading
1. User navigates to `/project/:id`
2. AppLayout queries `getProject()` via React Query
3. Project loaded → `setCurrentProject()`
4. Queries `getScenes()`, `getSections()`, `getAssets()`
5. Data synced to Zustand store

### Timeline Interaction
1. User clicks section/scene marker on timeline
2. `setActiveScene()` called
3. SceneEditor displays active scene data
4. WaveformDisplay updates cursor position

### Generation Flow
1. User fills prompt + parameters in SceneEditor
2. Clicks "Generate Image/Video"
3. `generateImage()` or `generateVideo()` API call
4. Job returned with ID
5. `addJob()` adds to store
6. Job updates via SSE → `subscribeToJobEvents()`
7. `updateJob()` syncs real-time status
8. GenerationPanel displays progress

### Export Flow
1. User clicks "Export" button
2. Export modal opens (format, quality selection)
3. Calls `exportVideo()` with project ID
4. Job created and tracked like generation
5. User can monitor progress in Generation Panel

## API Integration Points

### REST Endpoints
- `GET /api/projects` - List all projects
- `POST /api/projects` - Create project
- `GET /api/projects/:id` - Get project details
- `PATCH /api/projects/:id` - Update project
- `DELETE /api/projects/:id` - Delete project
- `GET /api/projects/:id/scenes` - Get project scenes
- `POST /api/projects/:id/scenes` - Create scene
- `PATCH /api/projects/:id/scenes/:sceneId` - Update scene
- `DELETE /api/projects/:id/scenes/:sceneId` - Delete scene
- `POST /api/projects/:id/scenes/:sceneId/generate-image` - Generate image
- `POST /api/projects/:id/scenes/:sceneId/generate-video` - Generate video
- `POST /api/projects/:id/assets` - Upload asset (multipart)
- `GET /api/projects/:id/assets` - Get project assets
- `DELETE /api/projects/:id/assets/:assetId` - Delete asset
- `POST /api/llm/enhance-prompt` - Enhance prompt with LLM
- `GET /api/projects/:id/sections` - Get song sections
- `POST /api/settings/test-comfyui` - Test ComfyUI connection
- `POST /api/settings/test-whisper` - Test Whisper connection
- `POST /api/settings/test-llm` - Test LLM API connection
- `GET /api/settings` - Get app settings
- `PATCH /api/settings` - Update app settings
- `GET /api/jobs` - Get job list
- `POST /api/jobs/:id/cancel` - Cancel job
- `POST /api/jobs/:id/retry` - Retry failed job
- `POST /api/projects/:id/export` - Export video

### Server-Sent Events
- `GET /api/jobs/stream` - Real-time job updates
  - Event: `job:update` - Emits Job object with updated status/progress

## Styling Strategy

### Dark Theme Base
- `bg-gray-950` - Darkest backgrounds
- `bg-gray-900` - Primary background
- `bg-gray-800` - Secondary/hover backgrounds
- `text-gray-100` - Primary text
- `text-gray-400` - Secondary text

### Section Colors
- Intro: `bg-section-intro` (#3b82f6 blue)
- Verse: `bg-section-verse` (#10b981 green)
- Chorus: `bg-section-chorus` (#f97316 orange)
- Bridge: `bg-section-bridge` (#a855f7 purple)
- Outro: `bg-section-outro` (#ef4444 red)

### Component Classes
- `.btn-primary` - Blue primary buttons
- `.btn-secondary` - Gray secondary buttons
- `.btn-danger` - Red danger buttons
- `.btn-ghost` - Transparent ghost buttons
- `.card` - Card container (gray-900 border)
- `.input-field` - Form inputs (gray-800 background)

## Responsive Behavior

The layout uses CSS Grid and Flexbox:
- Top toolbar: Full width, fixed height
- Main content: 4-panel grid (left panel, center, right panel, generation panel)
- Timeline: Full width at bottom, fixed height
- All panels scroll independently

## Performance Optimizations

1. **React Query**: Caches data, prevents redundant requests
2. **Zustand**: Minimal re-renders via selector functions
3. **Component Splitting**: Each major feature is isolated
4. **Lazy Imports**: Route-based code splitting via React Router
5. **WaveSurfer Optimization**: Single instance per project, manual cleanup
6. **Tailwind**: Purged unused styles in production

## Error Handling

- API errors logged to console
- Mutations show loading/error states
- Job failures display error messages in GenerationPanel
- Test connection buttons show success/failure indicators
- Form validation on required fields

## Future Enhancements

- [ ] Keyboard shortcuts for timeline navigation
- [ ] Drag-and-drop scene reordering
- [ ] Multi-select for batch operations
- [ ] Undo/redo history
- [ ] Keyboard shortcuts modal
- [ ] Dark/light theme toggle
- [ ] Responsive mobile layout
- [ ] Scene templates/presets
- [ ] Prompt history/suggestions
