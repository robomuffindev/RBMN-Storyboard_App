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

// Vision-scan garment reference image(s) -> costume slot fields (v1.160)
export const wizardGarmentAnalyze = (images: Array<{ name: string; subfolder?: string; type?: string }>) =>
  fetch(`${BASE}/wizard/garment-analyze`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ images }),
  }).then(j<{ source: string; fields: Record<string, unknown>;
              vision: Array<{ name: string; description: string }> }>);

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
  base_set?: boolean;   // legacy: false = front view only; true = 4-view set
  base_mode?: 'single' | 'set' | 'mesh';  // v1.176: single | 4-view | 🧊 mesh-ready
  cleanup?: string;
  klein_steps?: number;
  canvas_w?: number;    // per-character base canvas (wins over global default)
  canvas_h?: number;
}) =>
  fetch(`${BASE}/preview`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  }).then(j<{ image: string; version?: { character_id: string; version: BaseVersionT; count: number; active: string } | null }>);

// v1.180: live/parallel/cancellable base-SET generation. Front-anchored — the
// front renders first, then right/left/back derive from it across workers.
export interface BaseSetViewT {
  view: string;
  state: 'pending' | 'rendering' | 'done' | 'error' | 'skipped';
  host?: string | null; error?: string | null; ready?: boolean;
}
export interface BaseSetStatusT {
  run_id: string; status: 'running' | 'done' | 'cancelled' | 'error';
  character: string; base_mode: string; error?: string | null;
  version?: { character_id: string; version: BaseVersionT; count: number; active: string } | null;
  views: BaseSetViewT[];
}
export const startBaseSet = (body: {
  character_name: string; character_info: Record<string, unknown>;
  base_mode: 'set' | 'mesh'; engine?: string;
  cloner_images?: Array<Record<string, unknown>> | null;
  nsfw?: boolean; background?: string; face_kind?: string; style_custom?: string;
  base_clothing?: string; canvas_w?: number; canvas_h?: number; seed?: number;
  use_active_base?: boolean;   // v1.181: anchor the set on the approved base image
  derive_method?: string;      // v1.189: 'reference' (default) | 'matchpose'
}) =>
  fetch(`${BASE}/base-set/start`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  }).then(j<{ run_id: string; views: string[]; mode: string }>);
export const baseSetStatus = (runId: string) =>
  fetch(`${BASE}/base-set/status/${encodeURIComponent(runId)}`).then(j<BaseSetStatusT>);
export const cancelBaseSet = (runId: string) =>
  fetch(`${BASE}/base-set/cancel/${encodeURIComponent(runId)}`, { method: 'POST' }).then(j<{ ok: boolean }>);
export const baseSetImageUrl = (runId: string, idx: number) =>
  `${BASE}/base-set/image/${encodeURIComponent(runId)}/${idx}`;
export const regenBaseSetView = (runId: string, idx: number) =>
  fetch(`${BASE}/base-set/regen/${encodeURIComponent(runId)}/${idx}`, { method: 'POST' }).then(j<{ ok: boolean }>);
export const saveBaseSet = (runId: string) =>
  fetch(`${BASE}/base-set/save/${encodeURIComponent(runId)}`, { method: 'POST' })
    .then(j<{ ok: boolean; version?: { character_id: string; version: BaseVersionT; count: number; active: string } | null }>);

export interface CostumeVersionT extends BaseVersionT { costume_info?: Record<string, string>; }

export const generateCostumePreview = (body: {
  character_name: string; costume_name: string; costume_info: Record<string, string>;
  background?: string; sprite_index?: number | null; host?: string | null;
}) =>
  fetch(`${BASE}/costume-preview`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  }).then(j<{ image: string; host: string;
              version?: { character_id: string; costume: string; version: CostumeVersionT; count: number; active: string } | null }>);

// Qwen (VNCCS-replica) create previews (v1.168): the suite's exact
// CharacterCreatorV2 t2i base render / cloner collage pipeline, app-side.
export const generateQwenCreatePreview = (body: {
  character_name: string; character_info: Record<string, unknown>; nsfw?: boolean;
  background?: string; mode?: string; steps?: number; cfg?: number;
  seed?: number; negative?: string; host?: string;
}) =>
  fetch(`${BASE}/create/qwen-preview`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  }).then(j<{ image: string; host: string; engine: string; seed: number; t2i_mode: string;
              version?: { character_id: string; version: BaseVersionT; active: string } | null }>);

export const generateQwenClonePreview = (body: {
  character_name: string; cloner_images: Array<Record<string, unknown>>;
  character_info?: Record<string, unknown>; background?: string;
  base_clothing?: string; undress_prompt?: string; seed?: number;
  target_size?: number; ref_weight?: number; host?: string;
}) =>
  fetch(`${BASE}/create/qwen-clone-preview`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  }).then(j<{ image: string; host: string; engine: string; seed: number;
              version?: { character_id: string; version: BaseVersionT; active: string } | null }>);

// Qwen (VNCCS-replica) clothing preview (v1.167): the suite's ClothesDesigner
// Pass A rebuilt app-side -- Qwen-Image-Edit-2511 + VNCCS ClothesCore LoRA.
export const generateQwenClothesPreview = (body: {
  character_name: string; costume_name: string; costume_info: Record<string, string>;
  garment_ref?: { name: string; subfolder?: string; type?: string } | null;
  background?: string; base_version_id?: string; pose_asset_id?: string | null;
  seed?: number; steps?: number; cfg?: number; clothes_lora_strength?: number;
  target_size?: number; use_saved_garment?: boolean; host?: string;
}) =>
  fetch(`${BASE}/clothes/qwen-preview`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  }).then(j<{ image: string; host: string; engine: string; seed: number;
              version?: { character_id: string; costume: string; version: CostumeVersionT; count: number; active: string } | null }>);

// Klein clothing preview: DRESS the character's active base render (description
// slots and/or a garment reference image) instead of a VNCCS pose sprite.
export const generateKleinClothesPreview = (body: {
  character_name: string; costume_name: string; costume_info: Record<string, string>;
  garment_ref?: { name: string; subfolder?: string; type?: string } | null;
  background?: string; strength?: number; base_version_id?: string; view?: string;
  face_refine?: boolean; host?: string; pose_asset_id?: string | null;
  steps?: number; guidance?: number; ref_end?: number; negative?: string;
  consistency?: boolean; identity_lock?: boolean; clean_garment?: boolean;
  use_saved_garment?: boolean;
}) =>
  fetch(`${BASE}/clothes/klein-preview`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  }).then(j<{ image: string; host: string; engine: string;
              views?: Array<{ view: string; image: string }>;
              version?: { character_id: string; costume: string; version: CostumeVersionT; count: number; active: string } | null }>);

// Virtual try-on (v1.157): dress a person image in 1-3 garment reference photos
// via the fal flux-klein-tryon LoRA. Chain result_ref back as person_ref to layer.
export const kleinTryOn = (body: {
  character_name: string; costume_name?: string | null;
  garments: Array<{ ref: { name: string; subfolder?: string; type?: string }; desc?: string; slot?: string }>;
  person_asset_id?: string | null;
  person_ref?: { name: string; subfolder?: string; type?: string } | null;
  person_desc?: string; steps?: number; guidance?: number; host?: string;
  clean_garments?: boolean;
}) =>
  fetch(`${BASE}/clothes/tryon`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  }).then(j<{ image: string; result_ref: { name: string; subfolder: string; type: string };
              version?: { character_id: string; costume: string; version: CostumeVersionT; count: number; active: string } | null;
              host: string; engine: string }>);

// Edit-Image (v1.158): SAM3 segment pick-list + masked Klein inpaint.
export const baseSegment = (body: {
  character_name: string; prompt: string; threshold?: number;
  base_version_id?: string | null; costume_name?: string | null; host?: string;
}) =>
  fetch(`${BASE}/base/segment`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  }).then(j<{ segments: string[]; target_id: string; host: string }>);

export const baseInpaint = (body: {
  character_name: string; mask_b64: string; prompt: string; negative?: string;
  steps?: number; guidance?: number; grow?: number; blur?: number;
  refs?: Array<{ name: string; subfolder?: string; type?: string }>;
  base_version_id?: string | null; costume_name?: string | null; host?: string;
}) =>
  fetch(`${BASE}/base/inpaint`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  }).then(j<{ image: string; target_id: string; host: string;
              version?: { character_id: string; version: { id: string; url: string }; count: number; active: string }
                | { character_id: string; costume: string; version: CostumeVersionT; count: number; active: string } | null }>);

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
  settings_overrides?: Record<string, unknown>;
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

// Saved outfit reference images (v1.199.5): persist a costume's outfit photo
// app-side so it survives reloads / worker restarts and can be re-rendered later.
const _garmentBase = (characterId: string, costume: string) =>
  `${BASE}/clothes/garment/${encodeURIComponent(characterId)}/${encodeURIComponent(costume)}`;
export const saveGarmentRef = (characterId: string, costume: string,
                               ref: { name: string; subfolder?: string; type?: string }) =>
  fetch(`${_garmentBase(characterId, costume)}/save`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ref }),
  }).then(j<{ ok: boolean; url: string }>);
export const garmentRefMeta = (characterId: string, costume: string) =>
  fetch(`${_garmentBase(characterId, costume)}/meta`).then(j<{ exists: boolean; url: string | null }>);
export const garmentRefImageUrl = (characterId: string, costume: string) =>
  _garmentBase(characterId, costume);
export const deleteGarmentRef = (characterId: string, costume: string) =>
  fetch(_garmentBase(characterId, costume), { method: 'DELETE' }).then(j<{ ok: boolean }>);

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

// ---- v1.171 Settings Variation Test (Debug Options) ------------------------
export type VtItemT = {
  index: number; file: string | null; overrides: Record<string, unknown>;
  seed: number; host: string; elapsed: number; pose_name?: string | null;
  rating: number; error?: string | null; baseline?: boolean;
};
export type VtManifestT = {
  id: string; created: string; test_type: string; character: string;
  status: 'running' | 'done' | 'error' | 'cancelled'; error?: string | null;
  axes: Record<string, unknown[]>; same_seed: boolean; seed?: number | null;
  progress: { done: number; total: number }; items: VtItemT[];
  base_settings?: Record<string, unknown>;
};
export const varitestStart = (body: Record<string, unknown>) =>
  fetch(`${BASE}/varitest/start`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  }).then(j<{ id: string; total: number; variations: number }>);
export const varitestList = () =>
  fetch(`${BASE}/varitest/list`).then(j<{ runs: Array<{ id: string; created: string; test_type: string;
    character: string; status: string; progress: { done: number; total: number }; rated: number }> }>);
export const varitestGet = (id: string) =>
  fetch(`${BASE}/varitest/${encodeURIComponent(id)}`).then(j<VtManifestT>);
export const varitestRate = (id: string, index: number, rating: number) =>
  fetch(`${BASE}/varitest/${encodeURIComponent(id)}/rate`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ index, rating }),
  }).then(j<{ ok: boolean; rating: number }>);
export const varitestCancel = (id: string) =>
  fetch(`${BASE}/varitest/${encodeURIComponent(id)}/cancel`, { method: 'POST' }).then(j<{ ok: boolean }>);
export const varitestReport = (id: string) =>
  fetch(`${BASE}/varitest/${encodeURIComponent(id)}/report`).then(j<{ analysis: {
    rated: number; total: number;
    axis_tables: Record<string, Array<{ value: unknown; rated: number; ups: number; downs: number; score: number | null }>>;
    suggestions: Array<{ setting: string; use: unknown; avoid: unknown; confidence: string }>;
    liked: Array<{ index: number; pose?: string | null; overrides: Record<string, unknown>; baseline: boolean }>;
    disliked: Array<{ index: number; pose?: string | null; overrides: Record<string, unknown>; baseline: boolean }>;
  } }>);
export const varitestImageUrl = (id: string, idx: number) =>
  `${BASE}/varitest/${encodeURIComponent(id)}/image/${idx}`;
export const varitestReportMdUrl = (id: string) =>
  `${BASE}/varitest/${encodeURIComponent(id)}/report?fmt=md`;

// ---- v1.173 Tier-1 3D character body (Hunyuan3D mesh + UniRig rig) ---------
export type Mesh3dStatusT = {
  character: string;
  run: { status: string; phase: string; error?: string | null; host?: string; template?: string; detail?: string } | null;
  mesh3d: { template: string; created: string; rigged: boolean; checkpoint?: string; views?: string[]; rig_engine?: string | null; rig_error?: string | null } | null;
};
export const mesh3dGenerate = (body: { character_name: string; template?: string; use_views?: boolean; reuse_mesh?: boolean }) =>
  fetch(`${BASE}/mesh3d/generate`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  }).then(j<{ ok: boolean; character: string; template: string; views: string[]; checkpoint: string; rig_available?: boolean; rig_hint?: string | null }>);
export const mesh3dStatus = (character: string) =>
  fetch(`${BASE}/mesh3d/status/${encodeURIComponent(character)}`).then(j<Mesh3dStatusT>);
export const mesh3dFileUrl = (character: string, kind: 'glb' | 'fbx') =>
  `${BASE}/mesh3d/file/${encodeURIComponent(character)}/${kind}`;
