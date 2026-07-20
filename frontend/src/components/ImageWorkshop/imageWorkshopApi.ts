/**
 * Image Workshop — fetch helpers (base '/api/image-workshop').
 *
 * A free-form model playground with one shared, persistent gallery. Generation
 * rides the same ComfyUI workers the rest of the app uses. The character-mode
 * wizard reuses the VNCCS host/Ollama character wizard endpoint.
 */
const BASE = '/api/image-workshop';

async function j<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let detail = `${res.status}`;
    try { detail = (await res.json())?.detail || detail; } catch { /* ignore */ }
    throw new Error(detail);
  }
  if (res.status === 204) return undefined as unknown as T;
  return res.json() as Promise<T>;
}

export interface WsModelT {
  value: string;
  label: string;
  refs: number;       // max reference images (0 = text-to-image only)
  note: string;
  online: boolean;
}

export interface WsRefT {
  source: 'gallery' | 'upload';
  id: string;
  url: string;
  name?: string;
}

export interface WsGenImageT { id: string; url: string; seed?: number | null; }
export interface WsGenStatusT {
  gen_id: string;
  status: 'running' | 'done' | 'error';
  done: number;
  total: number;
  model: string;
  prompt: string;
  images: WsGenImageT[];
  error?: string | null;
}

export interface WsGalleryItemT {
  id: string;
  url: string;
  prompt: string;
  model: string;
  mode: string;
  seed?: number | null;
  width?: number | null;
  height?: number | null;
  fields?: Record<string, unknown> | null;
  negative?: string;
  tags?: string[];
  created_at?: string | null;
}

export interface WsGenBody {
  mode: 'freestyle' | 'character';
  model: string;
  prompt?: string;
  name?: string;
  fields?: Record<string, unknown>;
  negative?: string;
  count: number;
  width: number;
  height: number;
  seed?: number | null;
  references?: Array<{ source: string; id: string }>;
}

export const workshopApi = {
  models: () => fetch(`${BASE}/models`).then(j<{ models: WsModelT[] }>),

  generate: (body: WsGenBody) =>
    fetch(`${BASE}/generate`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
    }).then(j<{ gen_id: string; total: number; model: string; prompt: string; seed: number }>),

  genStatus: (gid: string) => fetch(`${BASE}/gen/${gid}`).then(j<WsGenStatusT>),

  uploadRef: async (file: File): Promise<WsRefT> => {
    const fd = new FormData();
    fd.append('file', file);
    return j<WsRefT>(await fetch(`${BASE}/upload`, { method: 'POST', body: fd }));
  },

  save: (gen_id: string, image_ids: string[], tags: string[] = []) =>
    fetch(`${BASE}/save`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ gen_id, image_ids, tags }),
    }).then(j<{ saved: WsGalleryItemT[]; count: number }>),

  gallery: (p: { offset?: number; limit?: number; q?: string; model?: string; tag?: string } = {}) => {
    const qs = new URLSearchParams();
    if (p.offset) qs.set('offset', String(p.offset));
    if (p.limit) qs.set('limit', String(p.limit));
    if (p.q) qs.set('q', p.q);
    if (p.model) qs.set('model', p.model);
    if (p.tag) qs.set('tag', p.tag);
    return fetch(`${BASE}/gallery?${qs.toString()}`).then(j<{ total: number; items: WsGalleryItemT[]; all_tags: string[] }>);
  },

  del: (ids: string[]) =>
    fetch(`${BASE}/gallery/delete`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ids }),
    }).then(j<{ deleted: number }>),

  setTags: (ids: string[], tags: string[]) =>
    fetch(`${BASE}/gallery/tags`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ids, tags }),
    }).then(j<{ updated: number; tags: string[] }>),

  // Character-mode wizard: reuse the VNCCS host / Ollama character wizard.
  wizardCharacter: (description: string) =>
    fetch(`/api/studio/vnccs/wizard/character`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ description, backend: 'auto' }),
    }).then(j<{ source: string; fields: Record<string, unknown> }>),

  // Vision "describe this reference": the vision LLM lives on the VNCCS host, so
  // the reference bytes are re-uploaded there, then clone-analyze scans them and
  // returns creator-style fields (sex/hair/eyes/body/outfit details, …).
  uploadToVnccs: async (file: File): Promise<{ name: string; subfolder: string; type: string }> => {
    const fd = new FormData();
    fd.append('file', file);
    return j<{ name: string; subfolder: string; type: string }>(
      await fetch(`/api/studio/vnccs/upload`, { method: 'POST', body: fd }));
  },
  cloneAnalyze: (image: { name: string; subfolder?: string; type?: string }) =>
    fetch(`/api/studio/vnccs/wizard/clone-analyze`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ image, images: [image], backend: 'auto' }),
    }).then(j<{ source: string; fields: Record<string, unknown> }>),
};
