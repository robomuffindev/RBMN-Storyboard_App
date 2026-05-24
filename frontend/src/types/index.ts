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
  // RunPod integration
  runpod_enabled?: boolean;
  runpod_api_key?: string;
  runpod_idle_timeout?: number;
  runpod_pods?: RunPodPodConfig[];
  // Single image generator
  single_image_generator?: string;
  // Distilled LoRA
  use_distilled_lora?: boolean;
  distilled_lora_name?: string;
  // Project directory
  project_dir?: string;
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

export interface ExportStatus {
  job_id: string;
  status: string;
  progress_percent: number;
  current_step: string;
  output_path?: string;
  download_url?: string;
  error?: string;
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

// ── Batch Mode ──────────────────────────────────────────────────────

export interface BatchItemConfig {
  id: string; // client-side UUID for tracking
  audio_filename: string;
  audio_upload_path: string;
  audioFile?: File; // kept client-side for display
  lyrics_text: string;
  project_name: string;
  concept_direction: string;
  style_text: string;
  render_type: 'music_video' | 'narration_video';
  video_mode: 'i2v' | 'v2v';
  two_pass: boolean;
  use_story_flow: boolean;
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
