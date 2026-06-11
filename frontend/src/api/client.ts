import axios, { AxiosInstance } from 'axios';
import type {
  Project,
  Scene,
  SongSection,
  Asset,
  Job,
  AppSettings,
  ExportStatus,
  ExportScanResult,
  AudioAnalysisResult,
  WorkflowConfig,
  WorkflowFieldMapping,
  RunPodPodStatus,
  GpuStatus,
  BatchItemConfig,
  BatchRunStatus,
  PersistentBatchRunSummary,
  PersistentBatchRunDetail,
} from '@/types/index';

const api: AxiosInstance = axios.create({
  baseURL: '/api',
  headers: { 'Content-Type': 'application/json' },
  timeout: 60000,  // 60s default timeout — prevents requests from hanging forever
});

// ===== Projects =====
export const createProject = (data: { name: string; mode: string; settings?: Record<string, unknown> }) =>
  api.post<Project>('/projects', data);

export const getProjects = () => api.get<Project[]>('/projects');

export const getProject = (id: string) => api.get<Project>(`/projects/${id}`);

export const updateProject = (id: string, data: Partial<Project>) =>
  api.put<Project>(`/projects/${id}`, data);

export const deleteProject = (id: string) => api.delete(`/projects/${id}`);

export const duplicateProject = (id: string) =>
  api.post<Project>(`/projects/${id}/duplicate`);

/** Deep-copy a narration_images project as a new narration_video project. */
export const convertToNarrationVideo = (id: string) =>
  api.post<Project>(`/projects/${id}/convert-to-narration-video`);

/** Export all editable text data for a project as a JSON payload. */
export const exportProjectText = (id: string) =>
  api.get<any>(`/projects/${id}/text-export`);

/** Apply a text-data JSON payload back to a project. */
export const importProjectText = (
  id: string,
  body: { json_payload: any; import_mode: 'override' | 'fill_missing'; accept_mode_mismatch?: boolean },
) =>
  api.post<{ ok: boolean; stats: Record<string, number> }>(
    `/projects/${id}/text-import`,
    body,
  );

// ===== Scenes =====
export const getScenes = (projectId: string) =>
  api.get<Scene[]>(`/projects/${projectId}/scenes`);

export const createScene = (projectId: string, data: Partial<Scene>) =>
  api.post<Scene>(`/projects/${projectId}/scenes`, data);

export const getScene = (projectId: string, sceneId: string) =>
  api.get<Scene>(`/projects/${projectId}/scenes/${sceneId}`);

export const updateScene = (projectId: string, sceneId: string, data: Partial<Scene>) =>
  api.put<Scene>(`/projects/${projectId}/scenes/${sceneId}`, data);

export type SceneMergeTarget = 'previous' | 'next' | 'gap';
export const deleteScene = (
  projectId: string,
  sceneId: string,
  opts: { merge_target?: SceneMergeTarget } = {},
) =>
  api.delete(`/projects/${projectId}/scenes/${sceneId}`, {
    data: { merge_target: opts.merge_target || 'previous' },
  });

export const reorderScenes = (projectId: string, order: { scene_id: string; order_index: number }[]) =>
  api.put(`/projects/${projectId}/scenes/reorder`, order);

export const cleanupScenes = (projectId: string) =>
  api.post<{ total_before: number; total_after: number; removed: number; message: string }>(
    `/projects/${projectId}/scenes/cleanup`
  );

export const setSceneStems = (projectId: string, sceneId: string, data: {
  vocals: boolean; drums: boolean; bass: boolean; other: boolean;
}) =>
  api.post(`/projects/${projectId}/scenes/${sceneId}/stems`, data);

export const getPrevSceneLastFrame = (projectId: string, sceneId: string) =>
  api.get<{ image_path: string | null }>(`/projects/${projectId}/scenes/${sceneId}/prev-last-frame`);

export const getSceneVersions = (projectId: string, sceneId: string) =>
  api.get(`/projects/${projectId}/scenes/${sceneId}/versions`);

export const deleteSceneVersion = (projectId: string, sceneId: string, versionId: string) =>
  api.delete(`/projects/${projectId}/scenes/${sceneId}/versions/${versionId}`);

export const uploadSceneMedia = (
  projectId: string,
  sceneId: string,
  file: File,
  mediaType: 'image' | 'video',
  frameType: 'first' | 'last' = 'first',
) => {
  const formData = new FormData();
  formData.append('file', file);
  formData.append('media_type', mediaType);
  formData.append('frame_type', frameType);
  return api.post(`/projects/${projectId}/scenes/${sceneId}/upload`, formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
  });
};

// ===== Assets =====
export const uploadAsset = (projectId: string, formData: FormData) =>
  api.post<Asset>(`/projects/${projectId}/assets/upload`, formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
  });

export const getAssets = (projectId: string, assetType?: string) =>
  api.get<Asset[]>(`/projects/${projectId}/assets`, {
    params: assetType ? { asset_type: assetType } : undefined,
  });

export const getAsset = (projectId: string, assetId: string) =>
  api.get<Asset>(`/projects/${projectId}/assets/${assetId}`);

export const updateAsset = (projectId: string, assetId: string, data: Partial<Asset>) =>
  api.put<Asset>(`/projects/${projectId}/assets/${assetId}`, data);

export const deleteAsset = (projectId: string, assetId: string) =>
  api.delete(`/projects/${projectId}/assets/${assetId}`);

export const bulkDeleteAssets = (projectId: string, assetIds: string[]) =>
  api.post<{ deleted: number; errors: string[] }>(`/projects/${projectId}/assets/bulk-delete`, { asset_ids: assetIds });

export const getAssetFileUrl = (projectId: string, assetId: string) =>
  `/api/projects/${projectId}/assets/${assetId}/file`;

// ===== Generation =====
export const generateImage = (projectId: string, data: {
  scene_id: string;
  workflow_type?: string;
  workflow_config_id?: string; // for custom workflows
  prompt: string;
  width?: number;
  height?: number;
  seed?: number;
  reference_asset_ids?: string[];
  frame_type?: 'first' | 'last';
  two_pass?: boolean;
}) =>
  api.post<{ job_id: string }>(`/projects/${projectId}/generate/image`, data);

export const rerunPass2 = (projectId: string, data: {
  scene_id: string;
  seed?: number;
}) =>
  api.post<{ id: string }>(`/projects/${projectId}/generate/rerun-pass2`, data);

export const generateVideo = (projectId: string, data: {
  scene_id: string;
  workflow_type?: string;
  workflow_config_id?: string;
  prompt: string;
  width?: number;
  height?: number;
  duration?: number;
  framerate?: number;
  seed?: number;
  first_frame_asset_id?: string;
  last_frame_asset_id?: string;
  audio_asset_id?: string;
  skip_audio_mux?: boolean;
}) =>
  api.post<{ job_id: string }>(`/projects/${projectId}/generate/video`, data);

export const enhancePrompt = (projectId: string, data: {
  prompt: string;
  context?: string;
  provider?: string;
  is_video?: boolean;
  frame_type?: 'first' | 'last';
}) =>
  api.post<{ enhanced_prompt: string }>(`/projects/${projectId}/generate/enhance-prompt`, data);

export const batchGenerate = (projectId: string, data: {
  jobs: Array<{
    scene_id: string;
    job_type: 'image' | 'video';
    workflow_type: string;
    prompt: string;
    [key: string]: any;
  }>;
}) =>
  api.post<{ job_ids: string[] }>(`/projects/${projectId}/generate/batch`, data);

export const autoGenerate = (projectId: string, mode: string) =>
  api.post<{
    jobs_created: number;
    job_ids: string[];
    enhanced_count: number;
    skipped_count: number;
  }>(`/projects/${projectId}/generate/auto`, { mode }, { timeout: 300000 });

// Sequential auto-generation (processes scenes one at a time)
export const startSequentialAutoGen = (
  projectId: string,
  mode: string,
  overrideFullSet: boolean = false,
  vocalsOnlyAudio: boolean = false,
  skipAudioMux: boolean = false,
  twoPass: boolean = false,
  useStoryFlow: boolean = true,
  lipsyncEnabled: boolean = true,
  vocalsOnlyForLipsync: boolean = false,
  chapterId?: string,
  skipExistingPrompts: boolean = false,
) =>
  api.post<{
    status: string;
    mode?: string;
    total_scenes: number;
    completed_scenes: number;
    current_scene_name?: string;
    current_step?: string;
    error?: string;
  }>(`/projects/${projectId}/generate/auto-sequential`, {
    mode,
    override_full_set: overrideFullSet,
    vocals_only_audio: vocalsOnlyAudio,
    skip_audio_mux: skipAudioMux,
    two_pass: twoPass,
    use_story_flow: useStoryFlow,
    lipsync_enabled: lipsyncEnabled,
    vocals_only_for_lipsync: vocalsOnlyForLipsync,
    chapter_id: chapterId ?? null,
    skip_existing_prompts: skipExistingPrompts,
  });

export const getSequentialAutoGenStatus = (projectId: string) =>
  api.get<{
    status: string;
    mode?: string;
    total_scenes: number;
    completed_scenes: number;
    current_scene_name?: string;
    current_step?: string;
    error?: string;
    batch_run_id?: string;
  }>(`/projects/${projectId}/generate/auto-sequential/status`);

export const cancelSequentialAutoGen = (projectId: string) =>
  api.post<{
    status: string;
  }>(`/projects/${projectId}/generate/auto-sequential/cancel`);

// ===== Timeline & Audio =====
export const analyzeAudio = (projectId: string, formData: FormData) =>
  api.post<AudioAnalysisResult>(`/projects/${projectId}/timeline/analyze`, formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
    timeout: 0,  // No timeout — Demucs stem separation can take 20+ minutes on CPU
  });

export const getSections = (projectId: string) =>
  api.get<SongSection[]>(`/projects/${projectId}/timeline/sections`);

export const updateSection = (projectId: string, sectionId: string, data: Partial<SongSection>) =>
  api.put(`/projects/${projectId}/timeline/sections/${sectionId}`, data);

export const getLyrics = (projectId: string) =>
  api.get<{ text: string; words: any[]; language: string; initial_text: string }>(`/projects/${projectId}/timeline/lyrics`);

export const saveLyricsText = (projectId: string, initialText: string) =>
  api.put<{ text: string; words: any[]; language: string; initial_text: string }>(
    `/projects/${projectId}/timeline/lyrics/text`,
    { initial_text: initialText }
  );

export const rerunWhisper = (projectId: string) =>
  api.post<{ text: string; words: any[] }>(`/projects/${projectId}/timeline/rerun-whisper`, {}, { timeout: 0 });

export const createScenesFromSections = (projectId: string) =>
  api.post<Scene[]>(`/projects/${projectId}/timeline/scenes-from-sections`);

export const getSceneStems = (projectId: string, sceneId: string) =>
  api.get(`/projects/${projectId}/timeline/stems/${sceneId}`);

export const mixStems = (projectId: string, sceneId: string) =>
  api.post<{ mix_path: string }>(`/projects/${projectId}/timeline/stems/${sceneId}/mix`);

export const getWaveformPeaks = (projectId: string) =>
  api.get<{ peaks: number[] }>(`/projects/${projectId}/timeline/waveform-peaks`);

export const suggestTimeline = (projectId: string) =>
  api.post<{ created_count: number; scene_ids: string[]; message: string }>(
    `/projects/${projectId}/timeline/suggest-timeline`, {}, { timeout: 180000 }
  );

export const sliceSceneAudio = (projectId: string) =>
  api.post<{ sliced_count: number; message: string }>(
    `/projects/${projectId}/timeline/slice-audio`
  );

export const sliceSingleSceneAudio = (projectId: string, sceneId: string) =>
  api.post<{ audio_clip_path: string; message: string }>(
    `/projects/${projectId}/timeline/slice-audio/${sceneId}`
  );

export const colorCorrectSceneVideo = (projectId: string, sceneId: string) =>
  api.post<{ corrected: boolean; message: string; video_path?: string }>(
    `/projects/${projectId}/scenes/${sceneId}/color-correct`
  );

export const retrimScene = (projectId: string, sceneId: string) =>
  api.post<{ success: boolean; message: string; video_path?: string }>(
    `/projects/${projectId}/scenes/${sceneId}/retrim`
  );

export const retrimAllScenes = (projectId: string) =>
  api.post<{ status: string; total_scenes: number; processed: number; skipped: number; errors: number; details: string[] }>(
    `/projects/${projectId}/retrim-all`
  );

// ===== Settings =====
export const getSettings = () => api.get<AppSettings>('/settings');

export const updateSettings = (data: Partial<AppSettings>) =>
  api.put<AppSettings>('/settings', data);

export const testComfyUI = (url: string) =>
  api.post<{ success: boolean; message: string; system_stats?: any }>('/settings/test-comfyui', { url });

export const testWhisper = () =>
  api.post<{ success: boolean; message: string }>('/settings/test-whisper');

export const testLLM = (data: { provider: string; api_key: string; model: string }) =>
  api.post<{ success: boolean; message: string }>('/settings/test-llm', data);

export const refreshOllamaModels = (baseUrl?: string) =>
  api.post<{ success: boolean; models: string[]; message: string }>('/settings/ollama/models', { base_url: baseUrl });

export const testOllamaSingle = (url: string) =>
  api.post<{ success: boolean; message: string }>('/settings/ollama/test-single', { url });

export const exportSettings = () =>
  api.get('/settings/export', { responseType: 'json' });

export const importSettings = (file: File) => {
  const form = new FormData();
  form.append('file', file);
  return api.post<AppSettings>('/settings/import', form, {
    headers: { 'Content-Type': 'multipart/form-data' },
  });
};

export const getBuiltinPrompt = (modelName: string, promptType: 'image' | 'video') =>
  api.get<{ model_name: string; prompt_type: string; prompt: string }>(
    '/settings/builtin-prompt',
    { params: { model_name: modelName, prompt_type: promptType } }
  );

// ===== Project Directory =====
export const browseDirectory = () =>
  api.post<{ success: boolean; path: string | null; message: string }>('/settings/browse-directory');

export const changeProjectDir = (newPath: string, moveData: boolean) =>
  api.post<{ success: boolean; message: string; old_path: string; new_path: string }>(
    '/settings/change-project-dir',
    { new_path: newPath, move_data: moveData }
  );

// ===== Workflows =====
export const getWorkflowConfigs = () =>
  api.get<WorkflowConfig[]>('/workflows');

export const getWorkflowConfig = (id: string) =>
  api.get<WorkflowConfig>(`/workflows/${id}`);

export const uploadWorkflow = (formData: FormData) =>
  api.post<WorkflowConfig>('/workflows/upload', formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
  });

export const updateWorkflowMappings = (id: string, mappings: WorkflowFieldMapping[]) =>
  api.put<WorkflowConfig>(`/workflows/${id}/mappings`, { field_mappings: mappings });

export const deleteWorkflow = (id: string) =>
  api.delete(`/workflows/${id}`);

export const introspectWorkflow = (formData: FormData) =>
  api.post<{ fields: WorkflowFieldMapping[]; node_count: number; detected_type: string }>(
    '/workflows/introspect', formData, { headers: { 'Content-Type': 'multipart/form-data' } }
  );

// ===== Concept & Video Flow =====
export const getConcept = (projectId: string) =>
  api.get<{
    song_title: string;
    concept_text: string;
    style_text: string;
    image_direction: string;
    custom_image_direction: string;
    characters: Array<{
      name: string;
      description: string;
      image_path: string | null;
      // Persisted across save/close so the Character Edit modal
      // hydrates the last prompt + reference image list on reopen.
      last_prompt?: string;
      reference_images?: Array<{ asset_id: string; image_path: string; description: string }>;
    }>;
    resolution_width: number;
    resolution_height: number;
    image_resolution_width?: number;
    image_resolution_height?: number;
    video_resolution_width?: number;
    video_resolution_height?: number;
    project_fps: number;
    global_seed_enabled: boolean;
    global_seed: number;
    use_transition_lora: boolean;
    transition_lora_strength: number;
    random_ken_burns: boolean;
    ken_burns_allowed_effects: string[];
    global_color_override: string;
    custom_color_palette: string;
    global_image_color_filter?: string;  // "" | "bw" | "grayscale" | "sepia"
  }>(
    `/projects/${projectId}/concept`
  );

export const saveConcept = (projectId: string, data: {
  song_title?: string;
  concept_text: string;
  style_text: string;
  image_direction?: string;
  custom_image_direction?: string;
  characters: Array<{
      name: string;
      description: string;
      image_path: string | null;
      // Persisted across save/close so the Character Edit modal
      // hydrates the last prompt + reference image list on reopen.
      last_prompt?: string;
      reference_images?: Array<{ asset_id: string; image_path: string; description: string }>;
    }>;
  resolution_width: number;
  resolution_height: number;
  image_resolution_width?: number;
  image_resolution_height?: number;
  video_resolution_width?: number;
  video_resolution_height?: number;
  project_fps: number;
  global_seed_enabled?: boolean;
  global_seed?: number;
  use_transition_lora?: boolean;
  transition_lora_strength?: number;
  random_ken_burns?: boolean;
  ken_burns_allowed_effects?: string[];
  global_color_override?: string;
  custom_color_palette?: string;
  global_image_color_filter?: string;  // "" | "bw" | "grayscale" | "sepia"
  enable_model_audio?: boolean;
  model_audio_volume?: number;
}) =>
  api.put(`/projects/${projectId}/concept`, data);

export const baseOnLyrics = (projectId: string, data: {
  song_title: string;
  concept_text: string;
  style_text: string;
}) =>
  api.post<{
    song_title: string;
    concept_text: string;
    style_text: string;
  }>(`/projects/${projectId}/concept/base-on-lyrics`, data, { timeout: 120000 });

export const autogenerateCharacters = (projectId: string) =>
  api.post<{
    characters: Array<{
      name: string;
      description: string;
      image_path: string | null;
      // Persisted across save/close so the Character Edit modal
      // hydrates the last prompt + reference image list on reopen.
      last_prompt?: string;
      reference_images?: Array<{ asset_id: string; image_path: string; description: string }>;
    }>;
    job_ids: string[];
    message: string;
  }>(`/projects/${projectId}/concept/characters/autogenerate`, {}, { timeout: 180000 });

export const getVideoFlow = (projectId: string) =>
  api.get<{ ideas: Array<{ scene_id: string; flow_idea: string }> }>(
    `/projects/${projectId}/concept/flow`
  );

export const generateVideoFlow = (projectId: string, chapterId?: string) =>
  api.post<{ ideas: Array<{ scene_id: string; flow_idea: string }> }>(
    `/projects/${projectId}/concept/flow/generate${chapterId ? `?chapter_id=${chapterId}` : ''}`,
    undefined,
    // Flow generation can call OpenAI/Anthropic for many scenes; even with
    // concurrent batching of 10 scenes per call, a large chapter can take
    // a couple minutes.  Default 60s axios timeout was leaving users with
    // "timeout of 60000ms exceeded" while the backend was still working.
    // 5 min cap is well above any reasonable LLM round-trip.
    { timeout: 300000 }
  );

export const getFlowProgress = (projectId: string) =>
  api.get<{
    status: string;
    total_scenes: number;
    total_batches: number;
    completed_batches: number;
    current_message: string | null;
    error: string | null;
    started_at: number | null;
  }>(`/projects/${projectId}/concept/flow/progress`);

export const updateSceneFlow = (projectId: string, sceneId: string, flowIdea: string) =>
  api.put(`/projects/${projectId}/concept/flow/${sceneId}`, { scene_id: sceneId, flow_idea: flowIdea });

export const generateCharacterImage = (projectId: string, data: {
  character_index: number;
  prompt_override?: string;
  width?: number;
  height?: number;
  workflow_type?: string;
  reference_asset_ids?: string[];
  seed?: number;
}) =>
  api.post<{ job_id: string }>(`/projects/${projectId}/concept/characters/generate`, data);

export const getCharacterVersions = (projectId: string, characterIndex: number) =>
  api.get<Array<{
    id: string;
    output_path: string | null;
    prompt: string;
    parameters: Record<string, any>;
    status: string;
    created_at: string | null;
  }>>(`/projects/${projectId}/concept/characters/${characterIndex}/versions`);

export const deleteCharacterVersion = (projectId: string, characterIndex: number, versionId: string) =>
  api.delete(`/projects/${projectId}/concept/characters/${characterIndex}/versions/${versionId}`);

export const setCharacterActiveImage = (projectId: string, characterIndex: number, outputPath: string) =>
  api.put(`/projects/${projectId}/concept/characters/${characterIndex}/active-image`, { output_path: outputPath });

export const generateTransition = (projectId: string, data: {
  scene_a_id: string;
  scene_b_id: string;
  prompt?: string;
  width?: number;
  height?: number;
  duration?: number;
  framerate?: number;
  seed?: number;
  transition_lora_strength?: number;
}) =>
  api.post<{ id: string; project_id: string; scene_id: string; job_type: string; status: string }>(
    `/projects/${projectId}/generate/transition`, data, { timeout: 120000 }
  );

// ===== Jobs =====
export const getJobs = (projectId?: string, status?: string) =>
  api.get<Job[]>('/jobs', { params: { project_id: projectId, status } });

export const getJob = (jobId: string) => api.get<Job>(`/jobs/${jobId}`);

export const cancelJob = (jobId: string) => api.post(`/jobs/${jobId}/cancel`);

export const retryJob = (jobId: string) => api.post<Job>(`/jobs/${jobId}/retry`);

export const purgeJobs = () => api.post<{ purged: number; message: string }>('/jobs/purge');

export const deleteJob = (jobId: string) => api.delete(`/jobs/${jobId}`);

// SSE for real-time job progress
export const subscribeToJobEvents = (
  onUpdate: (event: { type: string; job?: Job; progress?: number; node?: string; error?: string }) => void,
) => {
  const eventSource = new EventSource('/api/jobs/stream');

  eventSource.onmessage = (event) => {
    try {
      const data = JSON.parse(event.data);
      onUpdate(data);
    } catch (err) {
      console.error('Failed to parse SSE event:', err);
    }
  };

  eventSource.addEventListener('job_started', (e) => {
    try { onUpdate({ type: 'job_started', ...JSON.parse((e as MessageEvent).data) }); } catch {}
  });

  eventSource.addEventListener('job_progress', (e) => {
    try { onUpdate({ type: 'job_progress', ...JSON.parse((e as MessageEvent).data) }); } catch {}
  });

  eventSource.addEventListener('job_completed', (e) => {
    try { onUpdate({ type: 'job_completed', ...JSON.parse((e as MessageEvent).data) }); } catch {}
  });

  eventSource.addEventListener('job_failed', (e) => {
    try { onUpdate({ type: 'job_failed', ...JSON.parse((e as MessageEvent).data) }); } catch {}
  });

  eventSource.onerror = () => {
    console.warn('SSE connection lost, will auto-reconnect...');
  };

  return () => eventSource.close();
};

// ===== Export =====
export const exportVideo = (projectId: string, data: {
  format?: string;
  width?: number;
  height?: number;
  fps?: number;
  quality?: string;
  transition_type?: string;
  transition_duration?: number;
  color_match_clips?: boolean;
  random_ken_burns?: boolean | null;
  ken_burns_allowed_effects?: string[] | null;
  subtitles_enabled?: boolean;
  subtitle_font?: string;
  subtitle_size?: number;
  subtitle_color?: string;
  subtitle_position?: string;
  subtitle_outline?: number;
  normalize_audio?: boolean;
}) =>
  api.post<{ job_id: string }>(`/projects/${projectId}/export`, data);

export const getExportStatus = (projectId: string) =>
  api.get<ExportStatus>(`/projects/${projectId}/export/status`);

export const cancelExport = (projectId: string) =>
  api.post<{ status: string; message: string }>(`/projects/${projectId}/export/cancel`);

export const resumeExport = (projectId: string) =>
  api.post<{ job_id: string }>(`/projects/${projectId}/export/resume`);

export const scanExport = (projectId: string) =>
  api.get<ExportScanResult>(`/projects/${projectId}/export/scan`);

export const recoverExport = (projectId: string) =>
  api.post<{ job_id: string }>(`/projects/${projectId}/export/recover`);

// ===== Export Gallery =====
export interface ExportFileInfo {
  filename: string;
  size_mb: number;
  created_at: string;
  download_url: string;
}

export const listExports = (projectId: string) =>
  api.get<ExportFileInfo[]>(`/projects/${projectId}/export/gallery`);

export const deleteExportFile = (projectId: string, filename: string) =>
  api.delete(`/projects/${projectId}/export/gallery/${encodeURIComponent(filename)}`);

// ===== Preview Render =====
export const renderPreview = (projectId: string) =>
  api.post<{ job_id: string; status: string; preview_path?: string; preview_url?: string; error?: string }>(
    `/projects/${projectId}/export/preview`
  );

export const getPreviewStatus = (projectId: string) =>
  api.get<{ job_id: string; status: string; preview_path?: string; preview_url?: string; error?: string }>(
    `/projects/${projectId}/export/preview`
  );

// ===== RunPod =====
export const testRunPod = (apiKey: string) =>
  api.post<{ success: boolean; message: string; pods?: string[] }>('/settings/runpod/test', { api_key: apiKey });

export const getRunPodStatus = () =>
  api.get<{ enabled: boolean; pods: RunPodPodStatus[] }>('/settings/runpod/status');

export const startRunPodPod = (podId: string) =>
  api.post<{ pod_id: string; state: string; error: string }>('/settings/runpod/start', { pod_id: podId });

export const stopRunPodPod = (podId: string) =>
  api.post<{ pod_id: string; state: string; error: string }>('/settings/runpod/stop', { pod_id: podId });

// ===== GPU Status =====
export const getGpuStatus = () =>
  api.get<GpuStatus>('/settings/gpu-status');

export const redetectGpu = () =>
  api.post<GpuStatus>('/settings/gpu-status/redetect');

// ── Batch Mode ──────────────────────────────────────────────────────

export const uploadBatchAudio = (file: File) => {
  const formData = new FormData();
  formData.append('file', file);
  return api.post<{ upload_path: string; filename: string }>('/batch/upload-audio', formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
    timeout: 0,
  });
};

export const startBatchRun = (items: Omit<BatchItemConfig, 'id' | 'audioFile'>[]) =>
  api.post<BatchRunStatus>('/batch/run', { items });

export const getBatchStatus = (batchId: string) =>
  api.get<BatchRunStatus>(`/batch/${batchId}/status`);

export const cancelBatch = (batchId: string) =>
  api.post<BatchRunStatus>(`/batch/${batchId}/cancel`);

export const cleanBatchStaging = () =>
  api.delete('/batch/staging');

export const getActiveBatches = () =>
  api.get<Array<BatchRunStatus & { items: Array<{ batch_run_id?: string }> }>>('/batch/active');

// ── Persistent Batch Runs (Auto Gen) ──────────────────────────────

export const listPersistentBatchRuns = (projectId?: string) =>
  api.get<PersistentBatchRunSummary[]>('/batch-runs', {
    params: projectId ? { project_id: projectId } : undefined,
  });

export const getPersistentBatchRun = (batchRunId: string) =>
  api.get<PersistentBatchRunDetail>(`/batch-runs/${batchRunId}`);

export const resumePersistentBatchRun = (batchRunId: string) =>
  api.post<{ status: string; batch_run_id: string }>(`/batch-runs/${batchRunId}/resume`);

export const deletePersistentBatchRun = (batchRunId: string) =>
  api.delete<{ status: string; batch_run_id: string }>(`/batch-runs/${batchRunId}`);

export const deletePersistentBatchRunsBulk = (status?: 'completed' | 'failed') =>
  api.delete<{ status: string; deleted_count: number }>('/batch-runs', {
    params: status ? { status } : undefined,
  });

// ── Narration Mode ──────────────────────────────────────────────────

export const uploadSrt = (projectId: string, file: File) => {
  const formData = new FormData();
  formData.append('file', file);
  return api.post(`/projects/${projectId}/timeline/upload-srt`, formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
  });
};

export const getBackingTracks = (projectId: string) =>
  api.get(`/projects/${projectId}/backing-tracks`);

export const uploadBackingTrack = (projectId: string, file: File) => {
  const formData = new FormData();
  formData.append('file', file);
  return api.post(`/projects/${projectId}/backing-tracks`, formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
  });
};

export const updateBackingTrack = (projectId: string, trackId: string, data: any) =>
  api.patch(`/projects/${projectId}/backing-tracks/${trackId}`, data);

export const deleteBackingTrack = (projectId: string, trackId: string) =>
  api.delete(`/projects/${projectId}/backing-tracks/${trackId}`);

// ===== Asset Generator =====
export const generateAsset = (projectId: string, data: any) =>
  api.post(`/projects/${projectId}/generate/asset`, data);

export const listGeneratedAssets = (projectId: string) =>
  api.get(`/projects/${projectId}/assets/generated`);

export const assignAssetToScene = (projectId: string, sceneId: string, data: { asset_id: string; target: string }) =>
  api.post(`/projects/${projectId}/scenes/${sceneId}/assign-asset`, data);


// ===== Chapters =====
import type {
  ChapterTreeResponse,
  ChapterUpdate,
  LLMBatchPreview,
  ShortcodeResolution,
} from '../types';

/** Fetch the chapter tree for a project (top-level + nested). */
export const getChapters = (projectId: string) =>
  api.get<ChapterTreeResponse>(`/projects/${projectId}/chapters/`);

/** Re-derive chapters from the current script + scenes.
 *  Pass force_auto=true to ignore script headers and auto-split by count. */
export const reparseChapters = (projectId: string, forceAuto: boolean = false) =>
  api.post<ChapterTreeResponse>(`/projects/${projectId}/chapters/reparse`, {
    force_auto: forceAuto,
  });

/** Patch a chapter: rename, recolor, retag, manual metadata.
 *  Setting any field flips source to "manual" so the next reparse
 *  won't wipe the customization. */
export const updateChapter = (
  projectId: string,
  chapterId: string,
  patch: ChapterUpdate,
) => api.patch(`/projects/${projectId}/chapters/${chapterId}`, patch);

/** Split a chapter into two siblings at the given scene boundary. */
export const splitChapter = (
  projectId: string,
  chapterId: string,
  atSceneId: string,
  newName: string,
) =>
  api.post(`/projects/${projectId}/chapters/${chapterId}/split`, {
    at_scene_id: atSceneId,
    new_name: newName,
  });

/** Merge a chapter with its next sibling at the same depth. */
export const mergeChapterWithNext = (projectId: string, chapterId: string) =>
  api.post(`/projects/${projectId}/chapters/${chapterId}/merge_with_next`);

/** Dry-run: show how this chapter would be batched for LLM generation. */
export const previewLLMBatches = (
  projectId: string,
  chapterId: string,
  llmProvider?: 'cloud' | 'ollama',
) =>
  api.post<LLMBatchPreview>(
    `/projects/${projectId}/chapters/${chapterId}/preview-llm-batches`,
    { llm_provider: llmProvider ?? null },
  );

/** Generate a chapter description via LLM, optionally also inferring character_focus + style_notes (full=true). */
export const generateChapterDescription = (
  projectId: string,
  chapterId: string,
  full: boolean = true,
) =>
  api.post(
    `/projects/${projectId}/chapters/${chapterId}/generate-description`,
    { full },
    { timeout: 180000 },
  );

/** Resolve any shortcode (asset / scene / chapter) → entity + redirect. */
export const resolveShortcode = (code: string) =>
  api.get<ShortcodeResolution>(`/shortcode/${code}`);



// ─── Global Character Library ────────────────────────────────────────────

export interface GlobalCharacter {
  id: string;
  name: string;
  description: string;
  image_path: string;
  last_prompt: string;
  reference_images: string[];
  tags: string[];
  source_project_id: string | null;
  source_project_name: string;
  version_count: number;
  created_at: string;
  updated_at: string;
}

export interface GlobalCharacterCreate {
  name: string;
  description?: string;
  image_path?: string;
  last_prompt?: string;
  reference_images?: string[];
  tags?: string[];
  source_project_id?: string | null;
}

export const listGlobalCharacters = (params?: {
  search?: string;
  tag?: string;
  source_project_id?: string;
}) => api.get<GlobalCharacter[]>('/global-characters', { params });

export const listGlobalCharacterTags = () =>
  api.get<string[]>('/global-characters/tags');

export const createGlobalCharacter = (payload: GlobalCharacterCreate) =>
  api.post<GlobalCharacter>('/global-characters', payload);

export const deleteGlobalCharacter = (id: string) =>
  api.delete<{ ok: boolean }>(`/global-characters/${id}`);

export const importGlobalCharacterToProject = (
  id: string,
  projectId: string
) =>
  api.post<{ character_index: number; project_image_path: string }>(
    `/global-characters/${id}/import`,
    { project_id: projectId }
  );

export default api;
