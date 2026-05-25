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

  // Batch preview PIP
  lastCompletedAsset: LastCompletedAsset | null;
  batchPreviewVisible: boolean;
  batchPreviewEnabled: boolean;   // user toggle — when true, PIP auto-shows on job completion

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

  // Batch preview
  setLastCompletedAsset: (asset: LastCompletedAsset | null) => void;
  setBatchPreviewVisible: (visible: boolean) => void;
  setBatchPreviewEnabled: (enabled: boolean) => void;

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
  lastCompletedAsset: null,
  batchPreviewVisible: false,
  batchPreviewEnabled: false,

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
  setLastCompletedAsset: (asset) => set((state) => ({
    lastCompletedAsset: asset,
    batchPreviewVisible: asset !== null && state.batchPreviewEnabled,
  })),
  setBatchPreviewVisible: (visible) => set({ batchPreviewVisible: visible }),
  setBatchPreviewEnabled: (enabled) => set({ batchPreviewEnabled: enabled }),

  // Jobs
  addJob: (job) => set((s) => ({ jobs: [...s.jobs, job] })),
  updateJob: (jobId, updates) =>
    set((s) => ({
      jobs: s.jobs.map((j) => (j.id === jobId ? { ...j, ...updates } : j)),
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
