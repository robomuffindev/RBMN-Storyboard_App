// Tools API helpers (same-origin).
const BASE = '/api/tools';

async function j<T>(path: string, opts?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: opts?.body && !(opts.body instanceof FormData) ? { 'Content-Type': 'application/json' } : undefined,
    ...opts,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      detail = (await res.json()).detail || detail;
    } catch {
      /* ignore */
    }
    throw new Error(detail || `Request failed (${res.status})`);
  }
  if (res.status === 204) return undefined as unknown as T;
  return res.json();
}

export interface PoseItemT {
  id: string;
  name: string;
  category: string;
  tags: string[];
  source_type: string;
  has_thumb: boolean;
  has_joints: boolean;
  created_at?: string | null;
}
export interface FacetsT {
  total: number;
  categories: { name: string; count: number }[];
  tags?: { name: string; count: number }[];
}
export interface ScanCandidateT {
  cand_id: string;
  name: string;
  source_type: string;
  has_joints: boolean;
  thumb: string;
  sample: string;
  auto_tags: string[];
  dedup_hash: string;
  duplicate: boolean;
}
export interface ScanStatusT {
  scan_id: string;
  status: string;
  summary: Record<string, number>;
  total: number;
  candidates: ScanCandidateT[];
}
export interface ExprItemT {
  id: string;
  name: string;
  category: string;
  tags: string[];
  natural_prompt: string;
  has_thumb: boolean;
  source_type: string;
}

export interface SampleGenImage { id: string; url: string; }
export interface SampleGenStatusT {
  gen_id: string;
  status: 'running' | 'done' | 'error';
  done: number;
  total: number;
  kind: 'pose' | 'expression';
  model: string;
  prompt: string;
  images: SampleGenImage[];
  error?: string | null;
}

export const toolsApi = {
  // Pose organizer
  poseScan: async (opts: { folder?: string; file?: File; run_vision?: boolean }) => {
    const fd = new FormData();
    if (opts.file) fd.append('file', opts.file);
    if (opts.folder) fd.append('folder', opts.folder);
    fd.append('run_vision', String(!!opts.run_vision));
    return j<{ scan_id: string; status: string; files: number }>('/pose-organizer/scan', { method: 'POST', body: fd });
  },
  poseScanStatus: (scanId: string, offset = 0, limit = 240) =>
    j<ScanStatusT>(`/pose-organizer/scan/${scanId}?offset=${offset}&limit=${limit}`),
  poseScanThumbUrl: (scanId: string, name: string) => `${BASE}/pose-organizer/scan/${scanId}/thumb/${encodeURIComponent(name)}`,
  poseCommit: (scanId: string, body: { cand_ids?: string[] | null; category?: string; extra_tags?: string[]; include_duplicates?: boolean }) =>
    j<{ added: number }>(`/pose-organizer/scan/${scanId}/commit`, { method: 'POST', body: JSON.stringify(body) }),

  // Pose library
  poseList: (p: { category?: string; tag?: string; q?: string; offset?: number; limit?: number }) => {
    const qs = new URLSearchParams();
    if (p.category) qs.set('category', p.category);
    if (p.tag) qs.set('tag', p.tag);
    if (p.q) qs.set('q', p.q);
    qs.set('offset', String(p.offset ?? 0));
    qs.set('limit', String(p.limit ?? 120));
    return j<{ total: number; items: PoseItemT[] }>(`/pose-library?${qs.toString()}`);
  },
  poseFacets: () => j<FacetsT>('/pose-library/facets'),
  poseThumbUrl: (id: string) => `${BASE}/pose-library/${id}/thumbnail`,
  poseControlUrl: (id: string, style: 'openpose' | 'mannequin') => `${BASE}/pose-library/${id}/control?style=${style}`,
  posePatch: (id: string, body: { name?: string; category?: string; tags?: string[] }) =>
    j<{ ok: boolean }>(`/pose-library/${id}`, { method: 'PATCH', body: JSON.stringify(body) }),
  poseDelete: (ids: string[]) => j<{ deleted: number }>('/pose-library/delete', { method: 'POST', body: JSON.stringify({ ids }) }),
  poseToPresets: (ids: string[]) => j<{ added: number }>('/pose-library/to-presets', { method: 'POST', body: JSON.stringify({ ids }) }),
  poseImport: async (file: File) => {
    const fd = new FormData();
    fd.append('file', file);
    return j<{ imported: number }>('/pose-library/import', { method: 'POST', body: fd });
  },
  poseExportUrl: () => `${BASE}/pose-library/export`,

  // Worker-backed
  capabilities: () => j<{ dwpose: boolean; klein: boolean }>('/capabilities'),
  poseExtract: async (fileList: File[], opts: { category?: string; detect_hands?: boolean; detect_face?: boolean }) => {
    const fd = new FormData();
    fileList.forEach((f) => fd.append('files', f));
    fd.append('category', opts.category || 'Extracted');
    fd.append('detect_hands', String(opts.detect_hands ?? true));
    fd.append('detect_face', String(opts.detect_face ?? false));
    return j<{ extracted: number; errors: string[] }>('/pose-organizer/extract', { method: 'POST', body: fd });
  },
  poseHdThumbnails: (ids: string[]) =>
    j<{ rendered: number; errors: string[] }>('/pose-library/hd-thumbnails', { method: 'POST', body: JSON.stringify({ ids }) }),

  // Expression library
  exprImportCatalog: () => j<{ imported: number }>('/expression-library/import-catalog', { method: 'POST' }),
  exprAdd: (body: { name: string; category?: string; natural_prompt?: string; tags?: string[] }) =>
    j<ExprItemT>('/expression-library', { method: 'POST', body: JSON.stringify(body) }),
  exprList: (p: { category?: string; q?: string }) => {
    const qs = new URLSearchParams();
    if (p.category) qs.set('category', p.category);
    if (p.q) qs.set('q', p.q);
    return j<{ total: number; items: ExprItemT[] }>(`/expression-library?${qs.toString()}`);
  },
  exprFacets: () => j<FacetsT>('/expression-library/facets'),
  exprPatch: (id: string, body: { name?: string; category?: string; natural_prompt?: string; tags?: string[] }) =>
    j<{ ok: boolean }>(`/expression-library/${id}`, { method: 'PATCH', body: JSON.stringify(body) }),
  exprDelete: (ids: string[]) => j<{ deleted: number }>('/expression-library/delete', { method: 'POST', body: JSON.stringify({ ids }) }),
  exprThumbUrl: (id: string) => `${BASE}/expression-library/${id}/thumbnail`,

  // Sample generation (poses / expressions from our own models)
  sampleGenerate: (body: {
    kind: 'pose' | 'expression'; prompt: string; model: string; count: number;
    width?: number; height?: number; seed?: number; negative?: string; isolate?: boolean;
  }) => j<{ gen_id: string; total: number; kind: string; model: string }>(
    '/sample/generate', { method: 'POST', body: JSON.stringify(body) }),
  sampleStatus: (genId: string) => j<SampleGenStatusT>(`/sample/${genId}`),
  sampleImageUrl: (genId: string, name: string) => `${BASE}/sample/${genId}/image/${name}`,
  sampleCommit: (genId: string, body: {
    kind: 'pose' | 'expression'; image_ids: string[]; category?: string; name?: string;
    tags?: string[]; natural_prompt?: string; detect_hands?: boolean; detect_face?: boolean;
  }) => j<{ added: number; errors: string[] }>(`/sample/${genId}/commit`, { method: 'POST', body: JSON.stringify(body) }),
};
