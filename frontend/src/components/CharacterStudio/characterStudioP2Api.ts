/**
 * Character Studio — Phase 2 shared types + fetch helpers.
 *
 * Companion to CharacterStudioPage.tsx (Phase 1). Kept in a separate file
 * per the P2 build plan so Phase 1 edits stay minimal. Mirrors the same
 * self-contained fetch-helper pattern (same-origin, base '/api/character-studio').
 *
 * All fields are defensive/optional-safe: the backend contract
 * (docs/CHARACTER_STUDIO_P2_API.md) is authoritative but responses may omit
 * fields depending on state, so every consumer should use optional chaining
 * and fallback rendering rather than assuming presence.
 */

export type EngineT = 'auto' | 'qwen' | 'klein';

export type UpscaleModeT = 'auto' | 'seedvr2' | 'gan';

export type JobStatusT = 'pending' | 'running' | 'done' | 'failed';

// ---------------------------------------------------------------------------
// Pose Studio
// ---------------------------------------------------------------------------

export interface PosePresetT {
  id: string;
  name: string;
  custom?: boolean;
  category?: string;
}

// ---------------------------------------------------------------------------
// Pose Editor (joints)
// ---------------------------------------------------------------------------

// Canvas is fixed at 512x1536; joints are keyed by OpenPose-style joint name,
// each value a [x, y] pixel coordinate pair in that canvas space.
export type PoseJointsT = Record<string, [number, number]>;

export interface PosePresetJointsT {
  id: string;
  name: string;
  joints: PoseJointsT;
}

export interface PosePresetCustomCreateResponseT {
  id: string;
  name: string;
}

export interface PoseSetEntryT {
  status: JobStatusT;
  job_id?: string;
  engine?: string;
  pose_asset_id?: string;
  name?: string;
  image_rel?: string;
  asset_id?: string;
  error?: string;
}

export interface PosesGenerateResponseT {
  created: string[];
  errors: string[];
  engine: string;
}

// ---------------------------------------------------------------------------
// Costumes
// ---------------------------------------------------------------------------

export interface CostumeFieldsT {
  top?: string;
  bottom?: string;
  head?: string;
  face?: string;
  shoes?: string;
  [key: string]: string | undefined;
}

export interface CostumeSpriteT {
  status: JobStatusT;
  job_id?: string;
  engine?: string;
  image_rel?: string;
  asset_id?: string;
  error?: string;
}

export interface CostumeT {
  id: string;
  name: string;
  fields?: CostumeFieldsT;
  prompt?: string;
  reference_asset_id?: string;
  sprites?: Record<string, CostumeSpriteT>;
}

export interface CostumeCreateResponseT {
  id: string;
  costume: CostumeT;
}

export interface CostumeGenerateResponseT {
  job_id: string;
  engine: string;
}

// ---------------------------------------------------------------------------
// Emotions
// ---------------------------------------------------------------------------

export interface EmotionCatalogEntryT {
  key: string;
  description?: string;
  safe_name: string;
  natural_prompt?: string;
  category?: string;
}

// GET /catalogs -> emotions is a dict of {category_label: [entries...]}
export type EmotionsCatalogT = Record<string, EmotionCatalogEntryT[]>;

// GET /catalogs -> outfits is a flat list of outfit aesthetic presets.
// `content` is a comma-separated tag string usable directly as prompt text.
export interface OutfitCatalogEntryT {
  name: string;
  content: string;
}

export interface EmotionEntryT {
  status: JobStatusT;
  job_id?: string;
  engine?: string;
  source?: string;
  costume_id?: string | null;
  image_rel?: string;
  asset_id?: string;
  face_crop_rel?: string;
  face_crop_asset_id?: string;
  error?: string;
}

export interface EmotionsGenerateResponseT {
  created: string[];
  errors: string[];
  engine: string;
}

// ---------------------------------------------------------------------------
// Process (cutout / upscale)
// ---------------------------------------------------------------------------

export interface ProcessStepResultT {
  status?: string;
  image_rel?: string;
  asset_id?: string;
  method?: string;
  note?: string;
  error?: string;
  job_id?: string;
}

export type ProcessedEntryT = Record<string, ProcessStepResultT>; // keyed by step name (cutout/upscale)

export interface ProcessResponseT {
  jobs: string[];
  inline_results: { ref: string; step: string; asset_id: string }[];
  errors: string[];
}

// ---------------------------------------------------------------------------
// Generate-All
// ---------------------------------------------------------------------------

export interface GenerateAllIncludeT {
  shots?: boolean;
  costume_ids?: string[];
  emotions?: string[];
  cutout?: boolean;
  upscale?: boolean;
  upscale_mode?: UpscaleModeT;
}

export interface GenerateAllStateT {
  status: string; // 'running' | 'done' | 'failed' (defensive: treat unknown as non-terminal unless matches known terminal set)
  stage?: string;
  errors?: string[];
}

// ---------------------------------------------------------------------------
// Preflight
// ---------------------------------------------------------------------------

export interface PreflightResponseT {
  ok: boolean;
  engine_resolved?: string;
  warnings?: string[];
  seedvr2_online?: boolean;
  gan_upscale_online?: boolean;
  facedetailer_online?: boolean;
  klein_online?: boolean;
  qwen_online?: boolean;
  impact_online?: boolean;
}

// ---------------------------------------------------------------------------
// Wizards
// ---------------------------------------------------------------------------

export interface WizardCharacterInfoT {
  sex?: string;
  age?: string | number;
  race?: string;
  skin_color?: string;
  body?: string;
  face?: string;
  hair?: string;
  eyes?: string;
  additional_details?: string;
  aesthetics?: string;
  nsfw?: boolean;
  [key: string]: any;
}

export interface WizardCharacterResponseT {
  character_info: WizardCharacterInfoT;
  vision_description?: string;
}

// ---------------------------------------------------------------------------
// Extended /status (P2 fields layered onto Phase 1's CharacterStatusT)
// ---------------------------------------------------------------------------

export interface CharacterStatusP2T {
  pose_sets?: Record<string, PoseSetEntryT>;
  costumes?: Record<string, CostumeT>;
  emotions?: Record<string, EmotionEntryT>;
  processed?: Record<string, ProcessedEntryT>;
  generate_all?: GenerateAllStateT;
}

// ---------------------------------------------------------------------------
// Fetch helpers (mirrors CharacterStudioPage.tsx's apiFetch exactly)
// ---------------------------------------------------------------------------

const BASE = '/api/character-studio';

async function apiFetch<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  });
  if (!res.ok) {
    let detail = '';
    try {
      const body = await res.json();
      detail = body?.detail || JSON.stringify(body);
    } catch {
      detail = res.statusText;
    }
    throw new Error(detail || `Request failed (${res.status})`);
  }
  if (res.status === 204) return undefined as unknown as T;
  const text = await res.text();
  return text ? (JSON.parse(text) as T) : (undefined as unknown as T);
}

export const p2Api = {
  // Pose Studio
  listPosePresets: () => apiFetch<{ presets: PosePresetT[] }>('/pose-presets'),
  posePresetThumbnailUrl: (presetId: string) => `${BASE}/pose-presets/${presetId}/thumbnail`,
  generatePoses: (characterId: string, data: { preset_ids: string[]; engine: EngineT }) =>
    apiFetch<PosesGenerateResponseT>(`/characters/${characterId}/poses/generate`, {
      method: 'POST',
      body: JSON.stringify(data),
    }),
  getPosePresetJoints: (presetId: string) =>
    apiFetch<PosePresetJointsT>(`/pose-presets/joints/${presetId}`),
  previewPosePreset: async (joints: PoseJointsT): Promise<Blob> => {
    const res = await fetch(`${BASE}/pose-presets/preview`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ joints }),
    });
    if (!res.ok) {
      let detail = '';
      try {
        const body = await res.json();
        detail = body?.detail || JSON.stringify(body);
      } catch {
        detail = res.statusText;
      }
      throw new Error(detail || `Request failed (${res.status})`);
    }
    return res.blob();
  },
  createCustomPosePreset: (data: { name: string; joints: PoseJointsT }) =>
    apiFetch<PosePresetCustomCreateResponseT>('/pose-presets/custom', {
      method: 'POST',
      body: JSON.stringify(data),
    }),
  deleteCustomPosePreset: (presetId: string) =>
    apiFetch<void>(`/pose-presets/custom/${presetId}`, { method: 'DELETE' }),
  importPoses: (payload: { poseset?: any; poses?: any[]; category?: string }) =>
    apiFetch<{ imported: number; ids: string[] }>('/pose-presets/import', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  importOpenpose: async (file: File, category: string): Promise<{ imported: number; category: string }> => {
    const fd = new FormData();
    fd.append('file', file);
    fd.append('category', category);
    const res = await fetch(`${BASE}/pose-presets/import-openpose`, { method: 'POST', body: fd });
    if (!res.ok) {
      let detail = res.statusText;
      try {
        detail = (await res.json()).detail || detail;
      } catch {
        /* ignore */
      }
      throw new Error(detail || `Import failed (${res.status})`);
    }
    return res.json();
  },

  importPoseImages: async (file: File, category: string): Promise<{ imported: number }> => {
    const fd = new FormData();
    fd.append('file', file);
    fd.append('category', category);
    const res = await fetch(`${BASE}/pose-presets/import-images`, { method: 'POST', body: fd });
    if (!res.ok) {
      let detail = res.statusText;
      try { detail = (await res.json()).detail || detail; } catch { /* ignore */ }
      throw new Error(detail || `Import failed (${res.status})`);
    }
    return res.json();
  },

  // Costumes
  createCostume: (
    characterId: string,
    data: { name: string; fields: CostumeFieldsT; prompt?: string; reference_asset_id?: string | null }
  ) =>
    apiFetch<CostumeCreateResponseT>(`/characters/${characterId}/costumes`, {
      method: 'POST',
      body: JSON.stringify(data),
    }),
  updateCostume: (
    characterId: string,
    costumeId: string,
    data: { name?: string; fields?: CostumeFieldsT; prompt?: string; reference_asset_id?: string | null }
  ) =>
    apiFetch<CostumeCreateResponseT>(`/characters/${characterId}/costumes/${costumeId}`, {
      method: 'PATCH',
      body: JSON.stringify(data),
    }),
  deleteCostume: (characterId: string, costumeId: string) =>
    apiFetch<void>(`/characters/${characterId}/costumes/${costumeId}`, { method: 'DELETE' }),
  generateCostume: (characterId: string, costumeId: string, data: { engine: EngineT }) =>
    apiFetch<CostumeGenerateResponseT>(`/characters/${characterId}/costumes/${costumeId}/generate`, {
      method: 'POST',
      body: JSON.stringify(data),
    }),

  // Emotions
  getCatalogsRaw: () =>
    apiFetch<{ emotions?: EmotionsCatalogT | EmotionCatalogEntryT[]; outfits?: OutfitCatalogEntryT[] }>('/catalogs'),
  generateEmotions: (
    characterId: string,
    data: { emotions?: string[]; custom_expressions?: { name: string; natural_prompt: string }[]; costume_id?: string | null; source?: string; engine: EngineT }
  ) =>
    apiFetch<EmotionsGenerateResponseT>(`/characters/${characterId}/emotions/generate`, {
      method: 'POST',
      body: JSON.stringify(data),
    }),

  // Process
  processImages: (
    characterId: string,
    data: {
      image_refs: string[];
      steps: { cutout?: boolean; upscale?: boolean };
      upscale_mode?: UpscaleModeT;
      engine: EngineT;
    }
  ) =>
    apiFetch<ProcessResponseT>(`/characters/${characterId}/process`, {
      method: 'POST',
      body: JSON.stringify(data),
    }),

  // Generate-All
  generateAll: (characterId: string, data: { engine: EngineT; include: GenerateAllIncludeT }) =>
    apiFetch<{ ok: boolean; status: string }>(`/characters/${characterId}/generate-all`, {
      method: 'POST',
      body: JSON.stringify(data),
    }),

  // Preflight
  preflight: (characterId: string, engine: EngineT) =>
    apiFetch<PreflightResponseT>(`/characters/${characterId}/preflight?engine=${engine}`),

  // Wizards
  wizardCharacter: (description: string, style?: string) =>
    apiFetch<WizardCharacterResponseT>('/wizards/character', {
      method: 'POST',
      body: JSON.stringify({ description, style: style || undefined }),
    }),
  wizardClone: (assetId: string, style?: string) =>
    apiFetch<WizardCharacterResponseT>('/wizards/clone', {
      method: 'POST',
      body: JSON.stringify({ asset_id: assetId, style: style || undefined }),
    }),
};

// Terminal-state helpers — the doc's wording is "done"/"failed" for generate_all
// and "done"/"failed" for per-item entries (pose/costume/emotion/process), but
// since the contract also uses the word "error" loosely in prose, we treat any
// of these as terminal to be safe against minor backend wording drift.
export const TERMINAL_JOB_STATUSES = new Set(['done', 'failed', 'error', 'completed']);
export const TERMINAL_GENERATE_ALL_STATUSES = new Set(['done', 'failed', 'error', 'completed']);

export function isTerminalStatus(status: string | undefined | null): boolean {
  if (!status) return false;
  return TERMINAL_JOB_STATUSES.has(status);
}

export function isTerminalGenerateAllStatus(status: string | undefined | null): boolean {
  if (!status) return false;
  return TERMINAL_GENERATE_ALL_STATUSES.has(status);
}
