import { create } from 'zustand';
import type { Project, Scene, SongSection, Asset, Job, WorkflowConfig } from '@/types/index';

export interface LastCompletedAsset {
  sceneId: string;
  sceneName: string;
  sceneIndex: number;
  jobType: 'image' | 'video';
  projectId: string;
  assetUrl: string | null;     // URL to the generated asset
  prompt: string | null;       // prompt snippet used
  completedAt: number;         // Date.now() timestamp
  elapsedMs: number;           // job duration
}

interface AppState {
  // Data
  currentProject: Project | null;
  scenes: Scene[];
  sections: SongSection[];
  assets: Asset[];
  jobs: Job[];
  workflows: WorkflowConfig[];

  // UI state
  activeScene: Scene | null;
  playbackPosition: number;
  isPlaying: boolean;
  viewMode: 'sections' | 'scenes';
  selectedSectionId: string | null;
  scenesLocked: boolean;

  // Live mixer volumes (0-1 linear, synced from mixer UI for real-time playback control)
  narrationVolume: number;
  backingMasterVolume: number;

  // Batch preview PIP
  lastCompletedAsset: LastCompletedAsset | null;
  batchPreviewVisible: boolean;
  batchPreviewEnabled: boolean;   // user toggle — when true, PIP auto-shows on job completion

  // Auto-gen modal visibility — lifted to the store so both the header
  // button (AppLayout) and the Timeline toolbar button open the SAME
  // modal, which is the one wired to the bottom-of-screen status bar.
  autoGenOpen: boolean;

  // Actions
  setProject: (project: Project | null) => void;
  setScenes: (scenes: Scene[]) => void;
  setSections: (sections: SongSection[]) => void;
  setAssets: (assets: Asset[]) => void;
  setWorkflows: (workflows: WorkflowConfig[]) => void;

  setActiveScene: (scene: Scene | null) => void;
  setPlaybackPosition: (pos: number) => void;
  togglePlay: () => void;
  setIsPlaying: (playing: boolean) => void;
  setViewMode: (mode: 'sections' | 'scenes') => void;
  setSelectedSectionId: (id: string | null) => void;
  setScenesLocked: (locked: boolean) => void;

  // Live mixer
  setNarrationVolume: (vol: number) => void;
  setBackingMasterVolume: (vol: number) => void;

  // Batch preview
  setLastCompletedAsset: (asset: LastCompletedAsset | null) => void;
  setBatchPreviewVisible: (visible: boolean) => void;
  setBatchPreviewEnabled: (enabled: boolean) => void;

  // Auto-gen modal
  setAutoGenOpen: (open: boolean) => void;

  // Job management
  addJob: (job: Job) => void;
  updateJob: (jobId: string, updates: Partial<Job>) => void;
  removeJob: (jobId: string) => void;
  setJobs: (jobs: Job[]) => void;

  // Scene updates
  updateSceneInStore: (sceneId: string, updates: Partial<Scene>) => void;
  addScene: (scene: Scene) => void;
  removeScene: (sceneId: string) => void;

  // Asset updates
  addAsset: (asset: Asset) => void;
  removeAsset: (assetId: string) => void;
}

const MAX_JOBS = 200;
const TERMINAL_STATUSES = new Set(['done', 'failed', 'cancelled']);

/** Trim the jobs array to MAX_JOBS, removing oldest terminal jobs first. */
function pruneJobs(jobs: Job[]): Job[] {
  if (jobs.length <= MAX_JOBS) return jobs;

  // Partition into in-progress and terminal
  const inProgress: Job[] = [];
  const terminal: Job[] = [];
  for (const j of jobs) {
    if (TERMINAL_STATUSES.has(j.status)) {
      terminal.push(j);
    } else {
      inProgress.push(j);
    }
  }

  // Sort terminal by created_at ascending (oldest first) so we can drop from the front
  terminal.sort((a, b) => a.created_at.localeCompare(b.created_at));

  // How many terminal jobs we can keep
  const terminalBudget = Math.max(0, MAX_JOBS - inProgress.length);
  const keptTerminal = terminal.slice(terminal.length - terminalBudget);

  // Recombine: in-progress first, then kept terminal (preserves rough ordering)
  return [...inProgress, ...keptTerminal];
}

export const useAppStore = create<AppState>((set) => ({
  // Initial state
  currentProject: null,
  scenes: [],
  sections: [],
  assets: [],
  jobs: [],
  workflows: [],

  activeScene: null,
  playbackPosition: 0,
  isPlaying: false,
  viewMode: 'scenes',
  selectedSectionId: null,
  scenesLocked: false,
  narrationVolume: 1.0,
  backingMasterVolume: 1.0,
  lastCompletedAsset: null,
  batchPreviewVisible: false,
  batchPreviewEnabled: false,
  autoGenOpen: false,

  // Setters
  setProject: (project) => set({ currentProject: project }),
  setScenes: (scenes) => set({ scenes }),
  setSections: (sections) => set({ sections }),
  setAssets: (assets) => set({ assets }),
  setWorkflows: (workflows) => set({ workflows }),

  setActiveScene: (scene) => set({ activeScene: scene }),
  setPlaybackPosition: (pos) => set({ playbackPosition: pos }),
  togglePlay: () => set((s) => ({ isPlaying: !s.isPlaying })),
  setIsPlaying: (playing) => set({ isPlaying: playing }),
  setViewMode: (mode) => set({ viewMode: mode }),
  setSelectedSectionId: (id) => set({ selectedSectionId: id }),
  setScenesLocked: (locked) => set({ scenesLocked: locked }),
  setNarrationVolume: (vol) => set({ narrationVolume: vol }),
  setBackingMasterVolume: (vol) => set({ backingMasterVolume: vol }),
  setLastCompletedAsset: (asset) => set((state) => ({
    lastCompletedAsset: asset,
    batchPreviewVisible: asset !== null && state.batchPreviewEnabled,
  })),
  setBatchPreviewVisible: (visible) => set({ batchPreviewVisible: visible }),
  setBatchPreviewEnabled: (enabled) => set({ batchPreviewEnabled: enabled }),
  setAutoGenOpen: (open) => set({ autoGenOpen: open }),

  // Jobs
  addJob: (job) => set((s) => ({ jobs: pruneJobs([...s.jobs, job]) })),
  updateJob: (jobId, updates) =>
    set((s) => ({
      jobs: pruneJobs(s.jobs.map((j) => (j.id === jobId ? { ...j, ...updates } : j))),
    })),
  removeJob: (jobId) => set((s) => ({ jobs: s.jobs.filter((j) => j.id !== jobId) })),
  setJobs: (jobs) => set({ jobs }),

  // Scenes
  updateSceneInStore: (sceneId, updates) =>
    set((s) => ({
      scenes: s.scenes.map((sc) => (sc.id === sceneId ? { ...sc, ...updates } : sc)),
      activeScene: s.activeScene?.id === sceneId ? { ...s.activeScene, ...updates } : s.activeScene,
    })),
  addScene: (scene) =>
    set((s) => ({ scenes: [...s.scenes, scene].sort((a, b) => a.order_index - b.order_index) })),
  removeScene: (sceneId) =>
    set((s) => ({
      scenes: s.scenes.filter((sc) => sc.id !== sceneId),
      activeScene: s.activeScene?.id === sceneId ? null : s.activeScene,
    })),

  // Assets
  addAsset: (asset) => set((s) => ({ assets: [...s.assets, asset] })),
  removeAsset: (assetId) => set((s) => ({ assets: s.assets.filter((a) => a.id !== assetId) })),
}));
