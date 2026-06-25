// ===== Enums matching backend exactly =====
export type ProjectMode = 'music_video' | 'narration_images' | 'narration_video';
export type AssetType =
  | 'character'
  | 'clothing'
  | 'item'
  | 'place'
  | 'music'
  | 'narration'
  | 'generated_image'
  | 'generated_video'
  | 'reference';
export type SectionLabel = 'intro' | 'verse' | 'chorus' | 'bridge' | 'outro' | 'other';
export type JobStatus = 'pending' | 'running' | 'done' | 'failed' | 'retrying' | 'cancelled';
export type JobType = 'image' | 'video';

// Workflow types matching the available workflows
export type ImageWorkflowType =
  | 'klein_1ref'
  | 'klein_2ref'
  | 'klein_3ref'
  | 'klein_4ref'
  | 'klein_t2i'
  | 'custom';
export type VideoWorkflowType = 'ltx_fflf' | 'ltx_i2v' | 'ltx_v2v_extend' | 'custom';

// ===== Core Models =====

export interface Project {
  id: string;
  name: string;
  mode: ProjectMode;
  created_at: string;
  updated_at: string;
  settings: Record<string, any>;
  schema_version: number;
  // Derived
  scene_count?: number;
  asset_count?: number;
  project_path?: string;
}

export interface Scene {
  id: string;
  project_id: string;
  order_index: number;
  name: string;
  start_time: number;
  end_time: number;
  prompt: string;
  negative_prompt?: string;
  parameters: Record<string, any>;
  workflow_snapshot?: Record<string, any>;
  created_at: string;
  updated_at: string;
  // Chapter membership (leaf-most chapter when nested)
  chapter_id?: string | null;
  short_code?: string | null;
  // Relations
  stem_selection?: StemSelection;
  generation_history?: GenerationHistory[];
  timeline_positions?: TimelinePosition[];
}

export interface SongSection {
  id: string;
  project_id: string;
  label: SectionLabel;
  start_time: number;
  end_time: number;
  color?: string;
  created_at?: string;
}

export interface WordTimestamp {
  word: string;
  start: number;
  end: number;
  score?: number;
  block?: number;  // SRT block index — groups words into original subtitle lines
}

export interface SrtBlock {
  text: string;
  start: number;
  end: number;
}

export interface Asset {
  id: string;
  project_id: string;
  filename: string;
  rel_path: string;
  asset_type: AssetType;
  sha256?: string;
  duration_sec?: number;
  width?: number;
  height?: number;
  file_size?: number;
  meta: Record<string, any>;
  short_code?: string | null;
  tags?: string[];
  created_at: string;
}

export interface StemSelection {
  id?: string;
  scene_id: string;
  vocals: boolean;
  drums: boolean;
  bass: boolean;
  other: boolean;
}

export interface TimelinePosition {
  id: string;
  scene_id: string;
  asset_id: string;
  track: number;
  start_sec: number;
  end_sec: number;
  gain_db: number;
  effects: Record<string, any>;
}

export interface GenerationHistory {
  id: string;
  project_id: string;
  scene_id?: string;
  job_type: JobType;
  prompt_id?: string;
  workflow_json?: Record<string, any>;
  status: string;
  parameters: Record<string, any>;
  output_path?: string;
  error_message?: string;
  created_at: string;
  completed_at?: string;
}

export interface Job {
  id: string;
  project_id: string;
  scene_id?: string;
  // Snapshot of the referenced scene's name at response time.  Persists
  // visible in the Generation Queue chip even after the underlying Scene
  // is deleted or the user switches projects.  Backend bulk-resolves on
  // every /api/jobs list.
  scene_name?: string | null;
  job_type: JobType;
  status: JobStatus;
  priority: number;
  worker_url?: string;
  prompt_id?: string;
  parameters: Record<string, any>;
  result?: Record<string, any>;
  error?: string;
  created_at: string;
  started_at?: string;
  completed_at?: string;
  retry_count: number;
  // UI-only
  progress?: number;
  current_node?: string;
}

export interface SystemPromptOverrideEntry {
  text: string;
  enabled: boolean;
}

export interface AppSettings {
  id: number;
  comfyui_urls: string[];
  comfyui_server_caps?: Record<string, { image?: boolean; video?: boolean }>;
  whisper_mode: 'local' | 'remote' | 'comfyui';
  whisper_remote_url?: string;
  whisper_comfyui_url?: string;
  whisper_model: string;
  whisper_language: string;
  openai_api_key?: string;
  openai_model?: string;
  anthropic_api_key?: string;
  anthropic_model?: string;
  gemini_api_key?: string;
  gemini_model?: string;
  image_model_type?: string;
  video_model_type?: string;
  ltx_model_gguf?: string;
  image_system_prompt_overrides?: Record<string, SystemPromptOverrideEntry>;
  video_system_prompt_overrides?: Record<string, SystemPromptOverrideEntry>;
  default_llm_provider?: string;
  image_prompt_guidance?: Record<string, string>;
  video_prompt_guidance?: Record<string, string>;
  video_fps?: number;
  video_max_duration?: number;
  video_min_duration?: number;
  video_tail?: number;
  color_correction_enabled?: boolean;
  restrict_explicit_content?: boolean;
  global_negative_prompt?: string;
  // Export transition settings
  export_transition_type?: string;
  export_transition_duration?: number;
  export_color_match_clips?: boolean;
  export_lfff_trim_enabled?: boolean;
  // Network access — LAN/WAN
  network_access?: boolean;
  // App port
  app_port?: number;
  // RunPod integration
  runpod_enabled?: boolean;
  runpod_api_key?: string;
  runpod_idle_timeout?: number;
  runpod_pods?: RunPodPodConfig[];
  // Single image generator
  single_image_generator?: string;
  krea2_model_name?: string;
  // Distilled LoRA
  use_distilled_lora?: boolean;
  distilled_lora_name?: string;
  // Project directory
  project_dir?: string;
  // LTXDirector video generation settings
  director_guide_strength?: number;
  director_audio_guidance?: number;
  director_stitch?: boolean;
  director_auto_image_desc?: boolean;
  global_video_negative_prompt?: string;
  // GPU acceleration
  gpu_acceleration_enabled?: boolean;
  // FFmpeg threading
  ffmpeg_threads?: number;
  ffmpeg_filter_threads?: number;
  // Ollama (local LLM)
  ollama_base_url?: string;
  ollama_urls?: string[];
  ollama_model?: string;
  ollama_available_models?: string[];
  // ── Chapters / LLM batching ──
  llm_chapter_scene_limit_cloud?: number;
  llm_chapter_scene_limit_ollama?: number;
  chapter_auto_split_threshold?: number;
  chapter_max_depth?: number;
}

export interface GpuStatus {
  ffmpeg: {
    encoder: string;
    decoder: string;
    gpu_type: string;
    using_gpu: boolean;
  };
  demucs: {
    device: string;
    gpu_name: string;
    using_gpu: boolean;
  };
}

export interface RunPodPodConfig {
  pod_id: string;
  label: string;
  service_type: 'image' | 'video' | 'llm' | 'whisper';
  gpu_type_id: string;
  gpu_count: number;
  template_id: string;
  api_port: number;
  enabled: boolean;
}

export interface RunPodPodStatus {
  pod_id: string;
  label: string;
  service_type: string;
  state: 'unknown' | 'stopped' | 'starting' | 'running' | 'stopping' | 'exited' | 'error';
  url: string | null;
  gpu_type: string;
  uptime_seconds: number;
  cost_per_hr: number;
  error: string;
}

// ===== Workflow Management =====

export interface WorkflowFieldMapping {
  node_title: string;
  input_name: string;
  field_type: 'prompt' | 'negative_prompt' | 'image' | 'width' | 'height' | 'seed' | 'audio' | 'duration' | 'framerate' | 'first_frame' | 'last_frame' | 'other';
  description?: string;
  default_value?: any;
}

export interface WorkflowConfig {
  id: string;
  name: string;
  workflow_type: 'image' | 'video';
  description?: string;
  is_default: boolean;
  server_url?: string; // null = global, set = per-server
  workflow_json: Record<string, any>;
  field_mappings: WorkflowFieldMapping[];
  created_at: string;
  updated_at: string;
}

export interface ComfyUIServer {
  url: string;
  name?: string;
  healthy?: boolean;
  gpu_info?: string;
  vram_free?: number;
  capabilities?: string[];
  models?: string[];
  custom_workflows?: WorkflowConfig[];
}

export interface ExportConfig {
  format: 'mp4' | 'webm';
  width: number;
  height: number;
  fps: number;
  quality: 'draft' | 'standard' | 'high';
}

export interface ExportChunk {
  index: number;
  path: string;
  download_url: string;
  scenes: string;        // e.g. "1-25"
  size_mb: number;
  status: string;
}

export interface ExportStatus {
  job_id: string;
  status: string;
  progress_percent: number;
  current_step: string;
  output_path?: string;
  download_url?: string;
  error?: string;
  chunks?: ExportChunk[];
  total_chunks?: number;
  current_chunk?: number;
  phase?: string;  // "clips" | "chunks" | "final" | "post" | "cancelled" | "done"
  stems?: Array<{ filename: string; size_mb: number; download_url: string }>;
}

export interface ExportScanResult {
  has_manifest: boolean;
  manifest_status?: string;
  manifest_params?: Record<string, any>;
  clip_count: number;
  chunk_count: number;
  chunks: ExportChunk[];
  total_clips_size_mb: number;
  total_chunks_size_mb: number;
  recoverable: boolean;
  export_running: boolean;
  last_updated?: string;
}

// ===== Audio Analysis Results =====

export interface AudioAnalysisResult {
  sections: SongSection[];
  lyrics?: {
    text: string;
    words: WordTimestamp[];
    language: string;
  };
  bpm?: number;
  beats?: number[];
  stems: {
    vocals: string;
    drums: string;
    bass: string;
    other: string;
  };
}

// ── Narration Mode ─────────────────────────────────────────────────

export interface BackingTrack {
  id: string;
  project_id: string;
  filename: string;
  rel_path: string;
  order_index: number;
  start_time: number;
  end_time: number;
  trim_start: number;
  trim_end: number;
  volume_db: number;
  fade_in_sec: number;
  fade_out_sec: number;
}

export interface SubtitleSettings {
  subtitles_enabled: boolean;
  subtitle_font: string;
  subtitle_size: number;
  subtitle_color: string;
  subtitle_position: string;
  subtitle_outline: number;
}

// ── Batch Mode ──────────────────────────────────────────────────────

export interface BatchItemConfig {
  id: string; // client-side UUID for tracking
  audio_filename: string;
  audio_upload_path: string;
  audioFile?: File; // kept client-side for display
  // Per-item SRT — authoritative narration timing source for narration
  // modes.  Empty string when no SRT attached.  srt_filename is shown
  // in the UI; srt_upload_path is the backend staging path.
  srt_filename?: string;
  srt_upload_path?: string;
  srtFile?: File; // kept client-side for display
  // Skip Whisper for this item (requires SRT attached)
  disable_whisper?: boolean;
  lyrics_text: string;
  project_name: string;
  concept_direction: string;
  style_text: string;
  // Per-item color scheme override (e.g. "warm sunset", "black and white", "neon")
  color_scheme?: string;
  // Enable LTX 2.3 AV-native model audio for this item's videos
  enable_model_audio?: boolean;
  // Image post-process filter (none | grayscale | bw | sepia)
  image_filter?: 'none' | 'grayscale' | 'bw' | 'sepia';
  render_type: 'music_video' | 'narration_video' | 'narration_images';
  video_mode: 'i2v' | 'v2v' | 'fflf';
  image_mode: 'missing' | 'all_with_refs';
  two_pass: boolean;
  use_story_flow: boolean;
  auto_characters: boolean;
  lipsync_enabled: boolean;
  vocals_only_for_lipsync: boolean;
  override_full_set: boolean;
}

export interface BatchItemStatus {
  index: number;
  project_name: string;
  project_id: string | null;
  status: 'pending' | 'running' | 'done' | 'failed';
  current_step: string;
  error: string | null;
}

export interface BatchRunStatus {
  batch_id: string;
  status: 'idle' | 'running' | 'done' | 'failed' | 'cancelled';
  total_items: number;
  completed_items: number;
  current_item_index: number;
  items: BatchItemStatus[];
}

// ── Persistent Batch Runs (Auto Gen) ──────────────────────────────
export type PersistentBatchRunStatus = 'pending' | 'running' | 'completed' | 'failed' | 'cancelled' | 'paused';

export interface PersistentBatchRunSummary {
  id: string;
  project_id: string;
  project_name: string;
  mode: string;
  status: PersistentBatchRunStatus;
  total_scenes: number;
  completed_scenes: number;
  current_scene_name: string | null;
  current_step: string | null;
  error_count: number;
  started_at: string | null;
  completed_at: string | null;
  elapsed_ms: number;
  last_asset_url: string | null;
  last_asset_scene_name: string | null;
  created_at: string | null;
}

export interface BatchRunStepEntry {
  step: string;
  scene_name?: string;
  timestamp: string;
  asset_url?: string | null;
  worker_url?: string | null;
  type: 'scene_start' | 'scene_prepare' | 'scene_complete' | 'scene_failed' | 'step_change' | 'info';
}

/** Snapshot of one currently-running job, embedded in BatchRun detail
 *  so the batch screen can show live render % without a separate SSE
 *  channel.  Empty when nothing is in flight. */
export interface ActiveJobInfo {
  job_id: string;
  job_type: string;          // "image" | "video"
  scene_id?: string | null;
  scene_name?: string | null;
  worker_url?: string | null;
  workflow_type?: string | null;
  progress_percent: number;  // 0–100, from ComfyUI WS progress
  current_node?: string | null;
  started_at?: string | null;
  two_pass_phase?: string | null;  // "base" | "composite" | null
}

export interface PersistentBatchRunDetail extends PersistentBatchRunSummary {
  scene_results: Record<string, any>;
  error_log: Array<{ scene_id?: string; scene_name?: string; step?: string; error: string; timestamp?: string }>;
  run_settings: Record<string, any>;
  step_log: BatchRunStepEntry[];
  /** Live snapshot of in-flight jobs in the same project.  Populated
   *  on every detail fetch so the batch screen's normal poll picks up
   *  per-job render progress. */
  active_jobs?: ActiveJobInfo[];
}

// ── Chapters ───────────────────────────────────────────────────────

/** Chapter source — how the row was created. */
export type ChapterSource = 'auto' | 'script_header' | 'manual';

/** Flat Chapter row (matches backend.database.models.Chapter). */
export interface Chapter {
  id: string;
  project_id: string;
  parent_chapter_id: string | null;
  order_index: number;
  depth: number;            // 0 = top-level, 1 = sub, 2 = sub-sub
  name: string;
  short_code: string;
  color: string;
  auto_generated: boolean;
  source: ChapterSource;
  start_time: number;
  end_time: number;
  tags: string[];
  description: string;
  character_focus: string[];
  style_notes: string;
  scene_count?: number;
  scene_ids?: string[];
}

/** Recursive chapter tree node returned by GET /api/projects/:pid/chapters. */
export interface ChapterTreeNode extends Chapter {
  children: ChapterTreeNode[];
}

/** Top-level response shape for the chapter tree endpoint. */
export interface ChapterTreeResponse {
  project_id: string;
  chapter_count: number;
  chapters: ChapterTreeNode[];
  rebuild_method?: string;
}

/** Patch body for PATCH /api/projects/:pid/chapters/:cid. */
export interface ChapterUpdate {
  name?: string;
  color?: string;
  tags?: string[];
  metadata_patch?: Record<string, any>;
  description?: string;
  character_focus?: string[];
  style_notes?: string;
}

/** Export modal scope selector value. */
export interface ChapterSelection {
  mode: 'all' | 'single' | 'multiple';
  chapter_ids: string[];
}

/** LLM batch preview response — how a chapter would be sent to the LLM. */
export interface LLMBatchPreview {
  chapter: string;          // short_code
  provider: 'cloud' | 'ollama';
  limit: number;
  total_scenes: number;
  batch_count: number;
  batches: Array<{
    index: number;
    scene_count: number;
    scene_ids: string[];
    start_time: number;
    end_time: number;
  }>;
}

/** Universal shortcode lookup result. */
export interface ShortcodeResolution {
  kind: 'asset' | 'scene' | 'chapter';
  id: string;
  project_id: string;
  chapter_id?: string | null;
  shortcode: string;
  frontend_route: string;
}

