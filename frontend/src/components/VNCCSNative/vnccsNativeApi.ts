/**
 * VNCCS Native mode — fetch helpers (base '/api/studio/vnccs').
 *
 * Thin client over our proxy router, which relays to the pinned VNCCS host
 * worker's /vnccs/* routes and drives the VNCCS meganode graphs.  Self-contained
 * fetch (same-origin) matching the Character Studio P2 API style.
 */

const BASE = '/api/studio/vnccs';

async function j<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let detail = `${res.status}`;
    try { detail = (await res.json())?.detail || detail; } catch { /* ignore */ }
    throw new Error(detail);
  }
  return res.json() as Promise<T>;
}

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------
export interface HostInfoT {
  host: string | null;
  configured: string | null;
  online: boolean;
  settings: Record<string, unknown>;
}

export interface ContextListsT {
  checkpoints?: string[];
  diffusion_models?: string[];
  text_encoders?: string[];
  vae?: string[];
  samplers?: string[];
  schedulers?: string[];
  loras?: string[];
  characters?: string[];
  [k: string]: unknown;
}

export type VNCCSStepT = 'creator' | 'cloner' | 'clothes' | 'emotions';

export interface VNCCSCharacterInfoT {
  sex?: string;
  age?: number;
  race?: string;
  skin_color?: string;
  hair?: string;
  eyes?: string;
  face?: string;
  body?: string;
  height?: string;
  additional_details?: string;
  aesthetics?: string;
  negative_prompt?: string;
  nsfw?: boolean;
  // explicit mannequin build overrides (0..100); unset = auto-derive from `body`
  body_weight?: number;
  body_muscle?: number;
  body_height?: number;
  body_breast?: number;
  [k: string]: unknown;
}

export interface GenerateRespT {
  prompt_id: string;
  host: string;
  step: VNCCSStepT;
  tap_map: Record<string, string>;
}

export interface ResultImageT {
  node_id: string;
  filename: string;
  subfolder: string;
  type: string;
}

export interface ResultRespT {
  status: string; // pending | running | completed | success | error
  images: ResultImageT[];
}

export interface IngestRespT {
  character_id: string;
  ref: string;
  step: string;
  outputs: Record<string, string[]>;
  hero_asset_id: string | null;
  project_id: string;
}

// ---------------------------------------------------------------------------
// Host / settings
// ---------------------------------------------------------------------------
export const getHost = () => fetch(`${BASE}/host`).then(j<HostInfoT>);

export const setHost = (host: string | null, settings?: Record<string, unknown>) =>
  fetch(`${BASE}/host`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ host, settings }),
  }).then(j<HostInfoT>);

export const getContextLists = () => fetch(`${BASE}/context-lists`).then(j<ContextListsT>);

export const getVnccsCharacters = () => fetch(`${BASE}/characters`).then(j<unknown>);

export const getEmotions = () => fetch(`${BASE}/emotions`).then(j<unknown>);

export const getPoseLibrary = (full = false) =>
  fetch(`${BASE}/pose-library${full ? '?full=true' : ''}`).then(j<unknown>);

// Generic relay to any whitelisted /vnccs/* route (e.g. the LLM wizards).
export const relay = (subpath: string, init?: RequestInit) =>
  fetch(`${BASE}/r/${subpath}`, init);

export const characterWizard = (payload: Record<string, unknown>) =>
  relay('character_wizard', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  }).then((r) => j<Record<string, unknown>>(r));

// ---------------------------------------------------------------------------
// LLM wizards (backend endpoints: host wizard first, Ollama fallback with the
// verbatim VNCCS prompts). `source` tells which backend produced the fields.
// ---------------------------------------------------------------------------
export interface VisionScanT { name: string; role?: string; description: string; }
export interface WizardResultT { source: 'host' | 'ollama'; fields: Record<string, unknown>; analyzed?: number; vision?: VisionScanT[]; }

export const wizardCharacter = (description: string, backend: string = 'auto') =>
  fetch(`${BASE}/wizard/character`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ description, backend }),
  }).then(j<WizardResultT>);

export const wizardClothes = (description: string, backend: string = 'auto') =>
  fetch(`${BASE}/wizard/clothes`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ description, backend }),
  }).then(j<WizardResultT>);

export const wizardCloneAnalyze = (
  image: Record<string, unknown>, backend: string = 'auto',
  images?: Array<Record<string, unknown>>,
) =>
  fetch(`${BASE}/wizard/clone-analyze`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ image, images, backend }),
  }).then(j<WizardResultT>);

// ---------------------------------------------------------------------------
// Preview / pose defaults / character save (Create-tab staged flow)
// ---------------------------------------------------------------------------
export interface BaseVersionT { id: string; asset_id: string; url: string; created_at: string; views?: { view: string; asset_id: string; url: string }[]; gen_meta?: Record<string, string | number | boolean>; }

export const generatePreview = (body: {
  character_name: string; character_info: VNCCSCharacterInfoT;
  nsfw?: boolean; background?: string; gen_settings?: Record<string, unknown> | null;
  engine?: string;
  base_clothing?: string;
  face_kind?: string;
  // Clone-tab preview: uploaded reference images — Klein renders the default
  // pose from these through the full identity chain instead of T2I.
  cloner_images?: Array<Record<string, unknown>> | null;
  base_set?: boolean;   // false = front view only (default); true = 4-view set
  cleanup?: string;
  klein_steps?: number;
  canvas_w?: number;    // per-character base canvas (wins over global default)
  canvas_h?: number;
}) =>
  fetch(`${BASE}/preview`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  }).then(j<{ image: string; version?: { character_id: string; version: BaseVersionT; count: number; active: string } | null }>);

export interface CostumeVersionT extends BaseVersionT { costume_info?: Record<string, string>; }

export const generateCostumePreview = (body: {
  character_name: string; costume_name: string; costume_info: Record<string, string>;
  background?: string; sprite_index?: number | null; host?: string | null;
}) =>
  fetch(`${BASE}/costume-preview`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  }).then(j<{ image: string; host: string;
              version?: { character_id: string; costume: string; version: CostumeVersionT; count: number; active: string } | null }>);

// Klein clothing preview: DRESS the character's active base render (description
// slots and/or a garment reference image) instead of a VNCCS pose sprite.
export const generateKleinClothesPreview = (body: {
  character_name: string; costume_name: string; costume_info: Record<string, string>;
  garment_ref?: { name: string; subfolder?: string; type?: string } | null;
  background?: string; strength?: number; base_version_id?: string; view?: string;
  face_refine?: boolean; host?: string;
}) =>
  fetch(`${BASE}/clothes/klein-preview`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  }).then(j<{ image: string; host: string; engine: string;
              views?: Array<{ view: string; image: string }>;
              version?: { character_id: string; costume: string; version: CostumeVersionT; count: number; active: string } | null }>);

export const saveCostumeInfo = (body: { character_name: string; costume: string; costume_info: Record<string, string> }) =>
  fetch(`${BASE}/costume-info`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  }).then(j<{ character_id: string; costume: string; ok: boolean }>);

export const setActiveCostume = (characterId: string, costume: string, versionId: string) =>
  fetch(`${BASE}/character/${characterId}/costume-active`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ costume, version_id: versionId }),
  }).then(j<{ active: string; costume: string }>);

export const setActiveBase = (characterId: string, versionId: string) =>
  fetch(`${BASE}/character/${characterId}/base-active`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ version_id: versionId }),
  }).then(j<{ active: string }>);

export interface PoseDefaultT {
  index: number; name: string; prompt: string;
  pose: Record<string, unknown>; thumb: string | null;
}
export const getPoseDefaults = () =>
  fetch(`${BASE}/pose-defaults?thumbs=true`).then(j<{ poses: PoseDefaultT[]; max_pose_set: number }>);

export const saveCharacter = (body: {
  name: string; character_info: VNCCSCharacterInfoT;
  gen_settings?: Record<string, unknown> | null; story_id?: string | null;
  create_mode?: 'new' | 'clone'; clone_refs?: UploadRefT[];
  variant?: 'native' | 'klein';
  canvas_w?: number | null;   // per-character Klein canvas width (persisted)
}) =>
  fetch(`${BASE}/character/save`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  }).then(j<{ character_id: string; name: string }>);

// Per-pose sprite preview served by the host (existing characters).
export const posePreviewUrl = (character: string, index: number, costume?: string, host?: string) =>
  `${BASE}/r/get_character_pose_preview?character=${encodeURIComponent(character)}` +
  `&index=${index}${costume ? `&costume=${encodeURIComponent(costume)}` : ''}` +
  `${host ? `&_vnccs_host=${encodeURIComponent(host)}` : ''}&_=${index}`;

export const relayJson = <T,>(subpath: string) => relay(subpath).then((r) => j<T>(r));

// Emotion tile face image bundled with the node (same URL the node UI uses).
export const emotionImageUrl = (safeName: string) =>
  `${BASE}/r/get_emotion_image?name=${encodeURIComponent(safeName)}&v=webp`;

export const relayPost = <T,>(subpath: string, body: Record<string, unknown>) =>
  relay(subpath, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  }).then((r) => j<T>(r));

// Pose-library repositories (HF pose packs on the host — must be downloaded once).
export interface PoseRepoT {
  repo_id: string; title?: string; description?: string;
  enabled?: boolean; builtin?: boolean; pose_count?: number;
}
export const getPoseRepositories = () =>
  relayJson<{ repositories: PoseRepoT[]; local_repository?: Record<string, unknown> }>('pose_library/repositories');
export const togglePoseRepository = (repo_id: string, enabled: boolean) =>
  relayPost<Record<string, unknown>>('pose_library/repositories/toggle', { repo_id, enabled });
export const refreshPoseRepositories = (repo_id?: string) =>
  relayPost<Record<string, unknown>>('pose_library/repositories/refresh', repo_id ? { repo_id } : {});

export const poseLibraryPreviewUrl = (name: string, repository?: string, category?: string) =>
  `${BASE}/r/pose_library/preview/${encodeURIComponent(name)}` +
  `?repository=${encodeURIComponent(repository || '')}&category=${encodeURIComponent(category || '')}`;

export const getPoseFromLibrary = (name: string, repository?: string, category?: string) =>
  relayJson<{ pose?: Record<string, unknown> }>(
    `pose_library/get/${encodeURIComponent(name)}?repository=${encodeURIComponent(repository || '')}&category=${encodeURIComponent(category || '')}`);

// ---------------------------------------------------------------------------
// Generation lifecycle: generate -> poll result -> ingest
// ---------------------------------------------------------------------------
export interface GenerateBody {
  character_name: string;
  character_info: VNCCSCharacterInfoT;
  gen_settings?: Record<string, unknown> | null;
  control_center?: Record<string, unknown> | null;
  generator_overrides?: Record<string, unknown> | null;
  nsfw?: boolean;
  background?: string;
  // clothes (step 2)
  costume_name?: string;
  costume_info?: Record<string, string>;
  clone_image?: Record<string, unknown> | null;
  clone_sam_prompt?: string;
  // emotions (step 3)
  costumes?: string[];
  emotions?: string[];
  generation_model?: string;
  prompt_style?: string;
  // cloner (step 1 clone)
  cloner_images?: Array<Record<string, unknown>>;
  // pose selection (creator / cloner / clothes)
  pose_set?: Array<Record<string, unknown>>;
  pose_names?: string[];
  // 'klein' = Klein 9B graphs (Klein Hybrid mode); default = VNCCS meganodes
  engine?: string;
  // Klein base outfit per run: 'strip' (underwear/nude base) | 'keep' (clone clothing)
  base_clothing?: string;
  // Character render type: 'auto'|'realistic'|'anime'|'3d' (drives PuLID)
  face_kind?: string;
  // Pose consistency: true = lock every pose to the approved base render
  lock_base?: boolean;
  cleanup?: string;       // 'off' | 'gentle' | 'strong'
  klein_steps?: number;   // sampler steps (default 6)
  // per-character output canvas (Klein base + poses) — wins over the global
  // klein_canvas_width so a round/wide character can use a wider frame
  canvas_w?: number;
  canvas_h?: number;
  // consistent skin/lighting across a pose set (shared seed + colour-lock prompt)
  consistent_skin?: boolean;
}

export const generate = (step: VNCCSStepT, body: GenerateBody) =>
  fetch(`${BASE}/generate/${step}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  }).then(j<GenerateRespT>);

// Multi-worker fan-out: chunks are placed per the backend's sharding rules
// (creator/cloner split poses across all vnccs workers; clothes/emotions go to
// the workers recorded as holding the character's sprites).
export interface ParallelChunkT {
  prompt_id: string; host: string; tap_map: Record<string, string>;
  pose_names?: string[] | null;
  label: string; pose_count: number | null;
}
export const generateParallel = (step: VNCCSStepT, body: GenerateBody & { max_hosts?: number }) =>
  fetch(`${BASE}/generate-parallel/${step}`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  }).then(j<{ step: string; chunks: ParallelChunkT[]; errors: Array<{ host: string; error: string }>; seed?: number }>);

// ---------------------------------------------------------------------------
// Queue-based generation (Klein pose runs): enqueue one Job per chunk through
// the central Generation Queue, then watch job status.  Cancellable via
// cancelRun().  Backend ingests each chunk on completion (no client ingest).
// ---------------------------------------------------------------------------
export interface EnqueueRespT {
  run_id: string; job_ids: string[]; step: string;
  engine: string; chunk_count: number; seed?: number;
}
export const enqueueParallel = (step: VNCCSStepT, body: GenerateBody & { max_hosts?: number }) =>
  fetch(`${BASE}/generate-queue/${step}`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  }).then(j<EnqueueRespT>);

export interface JobLiteT {
  id: string;
  status: string;                 // pending | running | done | failed | cancelled | retrying
  worker_url?: string | null;
  error?: string | null;
  result?: Record<string, unknown> | null;
  parameters?: Record<string, unknown>;
}
export const getJob = (jobId: string) => fetch(`/api/jobs/${jobId}`).then(j<JobLiteT>);

// Cancel a whole run (all its chunk jobs) — interrupts running workers.
export const cancelRun = (runId: string) =>
  fetch(`/api/jobs/run/${encodeURIComponent(runId)}/cancel`, { method: 'POST' })
    .then(j<{ run_id: string; cancelled: number }>);

export const getVnccsHosts = () => fetch(`${BASE}/hosts`).then(j<{ hosts: string[] }>);

export const getResult = (promptId: string, host?: string) =>
  fetch(`${BASE}/result/${promptId}${host ? `?host=${encodeURIComponent(host)}` : ''}`).then(j<ResultRespT>);

export const ingest = (body: {
  prompt_id: string;
  host?: string;
  character_name: string;
  step: VNCCSStepT;
  tap_map: Record<string, string>;
  story_id?: string | null;
  costume?: string | null;
  emotions?: string[] | null;
  costumes?: string[] | null;
  seed?: number | null;
  pose_names?: string[] | null;
  pose_set?: Array<Record<string, unknown>> | null;
  postprocess?: string | null;
  chunk_pose_names?: string[] | null;
  engine?: string | null;
}) =>
  fetch(`${BASE}/ingest`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  }).then(j<IngestRespT>);

// A generated image URL (proxied from the host through our /view).
export const viewUrl = (img: ResultImageT, host?: string) =>
  `${BASE}/view?filename=${encodeURIComponent(img.filename)}` +
  `&subfolder=${encodeURIComponent(img.subfolder || '')}&type=${encodeURIComponent(img.type || 'output')}` +
  (host ? `&host=${encodeURIComponent(host)}` : '');

// Upload a reference image to the host (Cloner / clone-from-reference).
export type RefRole = 'face' | 'body' | 'full';
export interface UploadRefT {
  name: string; subfolder: string; type: string;
  role?: RefRole;            // UI-assigned: face -> face crop+PuLID, body -> masked body channel, full -> both
  suggested_role?: string;   // server auto-suggestion returned by /upload
  vision?: string;           // per-image "Vision Scan Data" (the vision model's description)
}
export const uploadReference = async (file: File): Promise<UploadRefT> => {
  const fd = new FormData();
  fd.append('file', file);
  const res = await fetch(`${BASE}/upload`, { method: 'POST', body: fd });
  return j<UploadRefT>(res);
};

// Reference enhancement: list the host's GAN upscale models, and upscale +
// sharpen ONE reference (returns a new uploaded ref to use in the clone run).
export const getUpscaleModels = () =>
  fetch(`${BASE}/upscale-models`).then(j<{ models: string[] }>);

export const enhanceReference = (body: {
  ref: UploadRefT; host?: string; method?: string; model?: string; sharpen?: string; max_side?: number;
}) =>
  fetch(`${BASE}/reference/enhance`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  }).then(j<UploadRefT & { method?: string; width?: number; height?: number; host?: string }>);

// Upscale the character's ACTIVE base render on a worker; saves the result as a
// new base version that becomes active (lock-base pose runs then use it).
export const enhanceBase = (body: {
  character_name: string; method?: string; model?: string; sharpen?: string; max_side?: number;
}) =>
  fetch(`${BASE}/base/enhance`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  }).then(j<{ version: BaseVersionT | null; method?: string; views?: number }>);

// Upscale one or more cataloged POSE sprites (whole set if many ids). Saves each
// as a new upscaled asset that preserves the original; always sources the
// original so repeated runs never stack upscale-on-upscale.
export const enhancePoses = (body: {
  character_name: string; asset_ids: string[];
  method?: string; model?: string; sharpen?: string; max_side?: number;
}) =>
  fetch(`${BASE}/poses/enhance`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  }).then(j<{ results: Array<{ src: string; original: string; asset_id: string; url: string; label: string; method: string; replaced?: number }>; count: number; failed: number }>);

// Switch Style: restyle the ACTIVE base render into a new art style (Klein
// reference-edit) and save it as a new active base version.
export const restyleBase = (body: {
  character_name: string; style?: string; style_custom?: string;
  style_ref?: { name: string; subfolder: string; type: string } | null; strength?: number;
  use_realism_lora?: boolean;
}) =>
  fetch(`${BASE}/base/restyle`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  }).then(j<{ version: BaseVersionT | null; style?: string; views?: number; host?: string }>);

// Catalog of ingested VNCCS Native characters + project-linking.
export interface CatalogItemT {
  character_id: string;
  name: string;
  story_id: string | null;
  ref: string | null;
  host: string | null;
  step: string | null;
  variant?: 'native' | 'klein' | null;   // which studio mode made this character
  hero_url?: string | null;              // thumbnail (chosen hero or active base)
  hero_asset_id: string | null;
  outputs: Record<string, number>;
  updated_at: string | null;
  form?: { character_info?: Record<string, unknown>; gen_settings?: Record<string, unknown> | null; canvas_w?: number | null } | null;
  hosts?: string[];
}
export const getCatalog = () => fetch(`${BASE}/catalog`).then(j<CatalogItemT[]>);

// Pick any cataloged image as the character's thumbnail (main screen + library).
export const setHeroImage = (characterId: string, assetId: string) =>
  fetch(`${BASE}/catalog/${characterId}/hero`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ asset_id: assetId }),
  }).then(j<{ ok: boolean; hero_asset_id: string; hero_url: string }>);

export interface CharacterImagesT {
  character_id: string; name: string;
  form?: { character_info?: Record<string, unknown>; gen_settings?: Record<string, unknown> | null; canvas_w?: number | null } | null;
  hosts: string[];
  outputs: Array<{ label: string; images: Array<{ asset_id: string; url: string; base_version?: string | null; costume?: string | null; pose_name?: string | null; upscaled?: boolean; upscale_source?: string | null }> }>;
  base_versions?: BaseVersionT[];
  active_base?: string | null;
  costumes?: Record<string, { versions?: CostumeVersionT[]; active?: string; costume_info?: Record<string, string> }>;
  create_mode?: 'new' | 'clone' | null;
  clone?: { character_info?: Record<string, unknown>; refs?: UploadRefT[] } | null;
  emotion_runs?: Array<{ emotions: string[]; costumes: string[]; seed?: number | null;
                         prompt_id?: string; host?: string; at?: string }>;
  pose_runs?: Array<{ step: string; costume?: string | null; pose_names: string[];
                      pose_set?: Array<Record<string, unknown>>; seed?: number | null;
                      prompt_id?: string; host?: string; at?: string }>;
}
export const getCharacterImages = (characterId: string) =>
  fetch(`${BASE}/catalog/${characterId}/images`).then(j<CharacterImagesT>);

export const deleteCatalogCharacter = (characterId: string, fromHosts: boolean) =>
  fetch(`${BASE}/catalog/${characterId}?from_hosts=${fromHosts}`, { method: 'DELETE' })
    .then(j<{ ok: boolean; assets_removed: number; hosts: Array<{ host: string; status: number | string; error?: string }> }>);

export const deleteCharacterImage = (characterId: string, assetId: string) =>
  fetch(`${BASE}/catalog/${characterId}/images/${assetId}`, { method: 'DELETE' })
    .then(j<{ ok: boolean; asset_id: string }>);

export interface ProjectLiteT { id: string; name: string; }
export const getProjects = () => fetch('/api/projects').then(j<ProjectLiteT[]>);

export const linkToProject = (body: { character_id: string; project_id: string; labels?: string[]; max_per_label?: number }) =>
  fetch(`${BASE}/link`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  }).then(j<{ created_asset_ids: string[]; character: string; project_id: string }>);

// Poll helper: resolve when the job is done (or reject on timeout/error).
export async function pollUntilDone(
  promptId: string,
  { intervalMs = 4000, timeoutMs = 60 * 60 * 1000, onTick, host }:
    { intervalMs?: number; timeoutMs?: number; onTick?: (r: ResultRespT) => void; host?: string } = {},
): Promise<ResultRespT> {
  const start = Date.now();
  // eslint-disable-next-line no-constant-condition
  while (true) {
    const r = await getResult(promptId, host);
    onTick?.(r);
    if (r.status === 'completed' || r.status === 'success' || r.status === 'error') return r;
    if (Date.now() - start > timeoutMs) throw new Error('Timed out waiting for VNCCS generation');
    await new Promise((res) => setTimeout(res, intervalMs));
  }
}
