/**
 * Klein 3.0 — pure Klein reference mode (v1.201.0). No 3D anywhere.
 *
 * Character = tagged 2D reference images + ONE active base image.
 *   - upload refs, tag them (front/back/left/right/face/outfit/other)
 *   - 🪄 Analyze a reference → fills the description fields (existing VNCCS
 *     vision wizard endpoints)
 *   - generate MISSING views with Klein N-ref edits (no 3D)
 *   - 👙 strip any ref (underwear or nude) → becomes the base
 *   - ⬆ GAN-upscale the active base (upscale becomes active)
 * Poses = Pose Library 2.0 (SHARED with Klein 2.0: prompt-generated with
 * stored/editable prompts, uploads incl. openpose/depth images).
 * Generate = image 1 (active base) + image 2 (pose) → Klein 2-ref edit.
 */
import React, { useCallback, useEffect, useState } from 'react';
import { ImageLightbox } from '../CharacterStudio/p2Shared';

const BASE = '/api/klein3';
const POSE_BASE = '/api/klein2';   // shared Pose Library 2.0

// ── styles (match the page) ─────────────────────────────────────────────────
const box: React.CSSProperties = { background: '#161a22', border: '1px solid #2a2f3a', borderRadius: 10, padding: 16 };
const label: React.CSSProperties = { fontSize: 12, color: '#9aa4b2', marginBottom: 4, display: 'block' };
const input: React.CSSProperties = {
  width: '100%', background: '#0e1116', border: '1px solid #2a2f3a', borderRadius: 6,
  color: '#e6e9ee', padding: '7px 9px', fontSize: 13, boxSizing: 'border-box',
};
const btn: React.CSSProperties = {
  background: '#3b82f6', color: '#fff', border: 'none', borderRadius: 6,
  padding: '8px 14px', fontSize: 13, cursor: 'pointer', fontWeight: 600,
};
const btnGhost: React.CSSProperties = { ...btn, background: 'transparent', border: '1px solid #2a2f3a', color: '#cbd2dc' };
const btnSm: React.CSSProperties = { ...btnGhost, padding: '4px 8px', fontSize: 12 };
const chip = (active: boolean): React.CSSProperties => ({
  ...btnSm, borderColor: active ? '#3b82f6' : '#2a2f3a', color: active ? '#9cc2ff' : '#cbd2dc',
});
const errTxt: React.CSSProperties = { color: '#ff8a8a', fontSize: 12 };
const okTxt: React.CSSProperties = { color: '#5ee08a', fontSize: 12 };
const hint: React.CSSProperties = { color: '#8d97a5', fontSize: 12 };

async function j<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let d = `${res.status}`;
    try { d = (await res.json())?.detail || d; } catch { /* ignore */ }
    throw new Error(d);
  }
  return res.json() as Promise<T>;
}

// ── types ───────────────────────────────────────────────────────────────────
interface RefT { id: string; tag: string; name: string; source: string; url: string }
interface BaseVerT { id: string; kind: string; url: string; view?: string; created_at?: string }
interface TaskT { worker?: string | null; status: string; error?: string | null }
interface JobT {
  status?: string; detail?: string; error?: string | null; done?: string[];
  worker?: string | null; tasks?: Record<string, TaskT>; workers?: string[];
}
interface WorkerT { url: string; healthy: boolean; klein: boolean; in_flight?: number | null }
interface CharT {
  slug: string; name: string; fields: Record<string, string>;
  ref_count: number; has_base: boolean; active_base_url?: string | null;
  missing_views: string[]; refs?: RefT[]; base_versions?: BaseVerT[];
  active_base?: string | null; jobs?: Record<string, JobT>; updated_at?: string;
  base_mode?: string;                       // auto | dressed | stripped (v1.217)
  base_sources?: Record<string, string>;    // view -> which image actually wins
}
interface PoseT { id: string; name: string; category: string; set?: string; tags?: string[]; prompt: string; source: string; url: string; seed?: number | null; has_image?: boolean }
interface SetInfoT { name: string; count: number; rendered: number; created_at?: string }
interface BatchRunT { status: string; done: number; total: number; tasks?: Record<string, { name: string; worker?: string | null; status: string }>; errors?: string[] }
interface GenT {
  gen_id: string; status: string; done: number; total: number; prompt: string;
  images: Array<{ id: string; url: string; seed?: number }>;
  refs: Array<{ name: string; url: string }>; error?: string | null;
  tasks?: Record<string, TaskT>; workers?: string[];
  pose?: string | null; pose_id?: string | null; created_at?: string | null;
}

// Downloadable instructions for ANY LLM to produce a valid pose-set import
// file — user says "I want a set of XYZ poses", pastes this doc, gets our format.
const POSE_IMPORT_LLM_DOC = `# Pose Set Generator — instructions for the assistant

You are generating a POSE SET import file for a character-image tool. The user
will tell you what kind of poses they want (theme, count, style of action).
Your ONLY output should be the file content described below — no commentary.

## Output format (JSON, preferred)

Output a single JSON array. Each element:

{
  "name": "Short unique pose name",
  "prompt": "description of the BODY POSE only",
  "category": "One word group like Standing, Seated, Action, Gesture, Combat, Emotional"
}

Rules for "prompt" — the most important part:
- Describe ONLY the body position: torso, arms, hands, legs, feet, head
  direction. Example: "crouching low on both feet, one fist planted on the
  ground, head raised looking forward"
- One single person, FULL BODY visible head to feet.
- Be explicit about limbs: which arm is raised, which knee is bent, where the
  hands are, where the person is looking.
- DO NOT describe: identity, face details, clothing, hair, props with brand
  names, backgrounds, scenery, lighting, camera settings, art style, quality
  words. The tool wraps your description in a standardized neutral-mannequin
  style prompt automatically — anything beyond the pose will conflict with it.
- Poses may use simple generic supports when needed: a plain chair, a plain
  wall, the ground. Nothing else.
- Prefer poses that read clearly in silhouette from a single camera viewpoint.

Rules for "name": unique within the set, 2-4 words, human-scannable.
Rules for "category": reuse the same few categories across the set.

## Count and variety

Default to 20 poses unless the user asks for a different count. Vary: stance
width, arm positions, head direction, kneeling/sitting/lying variants of the
theme, and at least a few side-on or turned poses.

## CSV alternative (only if the user asks for CSV)

Header row: name,prompt,category
Quote any field containing commas. Same content rules as JSON.

## Example output (3 poses, JSON)

[
  {"name": "Hero landing", "prompt": "crouching low with one fist and one knee touching the ground, other arm swept back, head raised looking forward", "category": "Action"},
  {"name": "Casual lean", "prompt": "leaning one shoulder against a plain wall, ankles crossed, arms folded loosely, head turned toward the camera", "category": "Standing"},
  {"name": "Floor read", "prompt": "sitting on the ground with legs crossed, leaning forward, both hands resting near the ankles, head tilted down", "category": "Seated"}
]
`;

const FIELDS: Array<[string, string]> = [
  ['age', 'Age'], ['sex', 'Sex'], ['race', 'Race'], ['skin_color', 'Skin color'],
  ['hair', 'Hair'], ['eyes', 'Eyes'], ['face', 'Face'], ['body', 'Body'],
  ['height', 'Height'], ['aesthetics', 'Aesthetics'], ['additional_details', 'Details'],
];
const TAGS = ['front', 'back', 'left', 'right', 'face', 'outfit', 'other'];
// the four the base picker actually resolves (v1.217 VIEW_TAGS)
const VIEWS = ['front', 'back', 'left', 'right'];

export default function Klein3Panel() {
  // characters
  const [chars, setChars] = useState<CharT[]>([]);
  const [slug, setSlug] = useState('');
  const [cur, setCur] = useState<CharT | null>(null);
  const [newName, setNewName] = useState('');
  const [msg, setMsg] = useState('');
  const [fields, setFields] = useState<Record<string, string>>({});
  const [analyzing, setAnalyzing] = useState(false);

  // refs / strip / upscale
  const [upTag, setUpTag] = useState('front');
  const [baseMode, setBaseMode] = useState<'auto' | 'dressed' | 'stripped'>('auto');
  const [baseResolve, setBaseResolve] = useState<Record<string, { found: boolean; source: string }> | null>(null);
  const [baseBusy, setBaseBusy] = useState(false);
  const [stripMode, setStripMode] = useState<'underwear' | 'nude'>('underwear');
  const [stripSrc, setStripSrc] = useState('');       // ref id ('' = front default)
  const [busy, setBusy] = useState(false);

  // poses (shared library)
  const [poses, setPoses] = useState<PoseT[]>([]);
  const [cats, setCats] = useState<string[]>([]);
  const [catFilter, setCatFilter] = useState('');
  const [poseId, setPoseId] = useState('');
  const [editPose, setEditPose] = useState<PoseT | null>(null);
  const [editPrompt, setEditPrompt] = useState('');
  const [poseBusy, setPoseBusy] = useState(false);
  const [poseMsg, setPoseMsg] = useState('');
  const [seedRun, setSeedRun] = useState<{ status: string; done: number; total: number } | null>(null);
  const [batchRun, setBatchRun] = useState<BatchRunT | null>(null);
  const [newPoseOpen, setNewPoseOpen] = useState(false);
  const [npName, setNpName] = useState('');
  const [npDesc, setNpDesc] = useState('');

  // generate
  const [extra, setExtra] = useState('');
  const [count, setCount] = useState(2);
  const [seed, setSeed] = useState('');
  const [gen, setGen] = useState<GenT | null>(null);
  const [genErr, setGenErr] = useState('');
  const [workers, setWorkers] = useState<WorkerT[]>([]);
  const [lightbox, setLightbox] = useState('');
  const [libOpen, setLibOpen] = useState(false);
  const [selectedSet, setSelectedSet] = useState('');       // set-mode selection
  const [selectedTags, setSelectedTags] = useState<string[]>([]);  // tag-mode selection
  const [newSetName, setNewSetName] = useState('');
  const [setsInfo, setSetsInfo] = useState<SetInfoT[]>([]);
  const [allTags, setAllTags] = useState<string[]>([]);
  const [tagsSel, setTagsSel] = useState<string[]>([]);     // modal tag filter
  const [gens, setGens] = useState<GenT[]>([]);
  const [gensPoseOnly, setGensPoseOnly] = useState(false);

  const loadGens = useCallback(async (s?: string) => {
    const use = s || slug;
    if (!use) { setGens([]); return; }
    try {
      const r = await j<{ gens: GenT[] }>(await fetch(`${BASE}/characters/${use}/gens`));
      setGens(r.gens || []);
    } catch { /* gallery is best-effort */ }
  }, [slug]);

  const loadHealth = useCallback(async () => {
    try {
      const r = await j<{ workers: WorkerT[] }>(await fetch(`${BASE}/health`));
      setWorkers(r.workers || []);
    } catch { /* worker bar is best-effort */ }
  }, []);

  const loadChars = useCallback(async (keep?: string) => {
    try {
      const r = await j<{ characters: CharT[] }>(await fetch(`${BASE}/characters`));
      setChars(r.characters);
      const want = keep || slug;
      if (!want && r.characters.length) setSlug(r.characters[0].slug);
      else if (want && !r.characters.some((c) => c.slug === want)) setSlug(r.characters[0]?.slug || '');
    } catch (e) { setMsg(`characters: ${(e as Error).message}`); }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [slug]);

  const loadCur = useCallback(async () => {
    if (!slug) { setCur(null); return; }
    try {
      const r = await j<CharT>(await fetch(`${BASE}/characters/${slug}?t=${Date.now()}`));
      setCur(r);
      setFields((f) => (Object.keys(f).length ? f : { ...r.fields }));
    } catch { setCur(null); }
  }, [slug]);

  const loadPoses = useCallback(async () => {
    try {
      const r = await j<{ poses: PoseT[]; categories: string[]; sets?: SetInfoT[]; tags?: string[]; seed_run: any; batch_run: BatchRunT | null }>(
        await fetch(`${POSE_BASE}/poses`));
      setPoses(r.poses); setCats(r.categories);
      setSetsInfo(r.sets || []); setAllTags(r.tags || []);
      setSeedRun(r.seed_run && r.seed_run.status ? r.seed_run : null);
      setBatchRun(r.batch_run && r.batch_run.status ? r.batch_run : null);
    } catch (e) { setPoseMsg(`pose library: ${(e as Error).message}`); }
  }, []);

  useEffect(() => {
    void loadChars(); void loadPoses(); void loadHealth();
    const iv = window.setInterval(() => { void loadHealth(); }, 30000);
    return () => window.clearInterval(iv);
  }, []);           // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { setFields({}); void loadCur(); void loadGens(); }, [slug]);   // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {                          // refresh the gallery when a batch finishes
    if (gen && gen.status !== 'running') void loadGens();
  }, [gen?.status]);                         // eslint-disable-line react-hooks/exhaustive-deps
  const setJobStatus = cur?.jobs?.set?.status;
  useEffect(() => {                          // refresh gallery as set runs progress/finish
    if (!setJobStatus) return;
    void loadGens();
  }, [setJobStatus, cur?.jobs?.set?.detail]);   // eslint-disable-line react-hooks/exhaustive-deps

  // poll char jobs while any running
  const anyJobRunning = Object.values(cur?.jobs || {}).some((jb) => jb?.status === 'running');
  useEffect(() => {
    if (!anyJobRunning) return;
    const iv = window.setInterval(() => { void loadCur(); }, 3000);
    return () => window.clearInterval(iv);
  }, [anyJobRunning, loadCur]);
  useEffect(() => {
    if (seedRun?.status !== 'running' && batchRun?.status !== 'running') return;
    const iv = window.setInterval(() => { void loadPoses(); }, 3500);
    return () => window.clearInterval(iv);
  }, [seedRun?.status, batchRun?.status, loadPoses]);
  useEffect(() => {
    if (!gen || gen.status !== 'running') return;
    const iv = window.setInterval(async () => {
      try { setGen(await j<GenT>(await fetch(`${BASE}/gen/${gen.gen_id}`))); } catch { /* keep */ }
    }, 2500);
    return () => window.clearInterval(iv);
  }, [gen?.gen_id, gen?.status]);      // eslint-disable-line react-hooks/exhaustive-deps

  // ── character actions ─────────────────────────────────────────────────────
  const createChar = async () => {
    if (!newName.trim()) return;
    setBusy(true); setMsg('');
    try {
      const r = await j<CharT>(await fetch(`${BASE}/characters`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: newName.trim() }),
      }));
      setNewName(''); await loadChars(r.slug); setSlug(r.slug);
    } catch (e) { setMsg((e as Error).message); }
    setBusy(false);
  };

  const saveFields = async () => {
    setBusy(true); setMsg('');
    try {
      await j(await fetch(`${BASE}/characters/${slug}/fields`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ fields }),
      }));
      setMsg('✓ description saved');
    } catch (e) { setMsg((e as Error).message); }
    setBusy(false);
  };

  // Analyze: reuse the app's existing vision wizard (upload ref bytes to the
  // VNCCS host, clone-analyze returns creator-style fields).  Multi-image:
  // front + face + newest others, up to 4 refs in one analyze call.
  const analyzeRefs = async (only?: RefT) => {
    const all = cur?.refs || [];
    const pick = only ? [only]
      : [...all.filter((r) => r.tag === 'front'), ...all.filter((r) => r.tag === 'face'),
         ...all.filter((r) => r.tag !== 'front' && r.tag !== 'face')].slice(0, 4);
    if (!pick.length) { setMsg('upload references first'); return; }
    setAnalyzing(true); setMsg(`🪄 analyzing ${pick.length} reference${pick.length > 1 ? 's' : ''}…`);
    try {
      const ups: Array<{ name: string; subfolder: string; type: string }> = [];
      for (const r of pick) {
        const blob = await (await fetch(r.url)).blob();
        const fd = new FormData();
        fd.append('file', new File([blob], `${r.id}.png`, { type: 'image/png' }));
        ups.push(await j(await fetch('/api/studio/vnccs/upload', { method: 'POST', body: fd })));
      }
      const res = await j<{ fields: Record<string, unknown> }>(
        await fetch('/api/studio/vnccs/wizard/clone-analyze', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ image: ups[0], images: ups, backend: 'auto' }),
        }));
      const merged: Record<string, string> = { ...fields };
      for (const [k] of FIELDS) {
        const v = res.fields?.[k];
        if (v != null && String(v).trim()) merged[k] = String(v).trim();
      }
      setFields(merged);
      setMsg('✓ analyzed — review the fields, then 💾 Save');
    } catch (e) { setMsg(`analyze failed: ${(e as Error).message}`); }
    setAnalyzing(false);
  };

  // ── ref actions ───────────────────────────────────────────────────────────
  const uploadRef = async (file: File) => {
    setBusy(true); setMsg('');
    try {
      const fd = new FormData();
      fd.append('file', file); fd.append('tag', upTag);
      await j(await fetch(`${BASE}/characters/${slug}/refs`, { method: 'POST', body: fd }));
      await loadCur();
    } catch (e) { setMsg(`upload failed: ${(e as Error).message}`); }
    setBusy(false);
  };

  const setTag = async (rid: string, tag: string) => {
    try {
      await j(await fetch(`${BASE}/characters/${slug}/refs/${rid}/update`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ tag }),
      }));
      await loadCur();
    } catch (e) { setMsg((e as Error).message); }
  };

  const delRef = async (rid: string) => {
    try {
      await j(await fetch(`${BASE}/characters/${slug}/refs/${rid}/delete`, { method: 'POST' }));
      await loadCur();
    } catch (e) { setMsg((e as Error).message); }
  };

  const genViews = async () => {
    if (!cur?.missing_views.length) return;
    setMsg('');
    try {
      await j(await fetch(`${BASE}/characters/${slug}/views/generate`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ views: cur.missing_views }),
      }));
      await loadCur();
    } catch (e) { setMsg((e as Error).message); }
  };

  // v1.275.2: full re-run with a FRESH 🙂 face anchor — for when the set
  // exists but the faces drifted. Renders the close-up first, then all views.
  const regenViewsFaced = async () => {
    setMsg('');
    try {
      await j(await fetch(`${BASE}/characters/${slug}/views/generate`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ views: ['front', 'back', 'left', 'right'], regen_face: true }),
      }));
      await loadCur();
    } catch (e) { setMsg((e as Error).message); }
  };

  // v1.225: PUT returns `resolves_to` for every view, so the consequence of the
  // toggle is visible BEFORE a render is spent on it.
  const setMode = async (m: 'auto' | 'dressed' | 'stripped') => {
    setMsg(''); setBaseBusy(true); setBaseMode(m);
    try {
      const r = await j<{ mode: string; resolves_to: Record<string, { found: boolean; source: string }> }>(
        await fetch(`${BASE}/characters/${slug}/base-mode`, {
          method: 'PUT', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ mode: m }),
        }));
      setBaseResolve(r.resolves_to);
      await loadCur();
    } catch (e) { setMsg((e as Error).message); }
    finally { setBaseBusy(false); }
  };

  const doStrip = async () => {
    setMsg('');
    try {
      await j(await fetch(`${BASE}/characters/${slug}/strip`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ mode: stripMode, source_ref_id: stripSrc || null }),
      }));
      await loadCur();
    } catch (e) { setMsg((e as Error).message); }
  };

  useEffect(() => {
    if (cur?.base_mode) setBaseMode(cur.base_mode as 'auto' | 'dressed' | 'stripped');
    if (cur?.base_sources) {
      setBaseResolve(Object.fromEntries(Object.entries(cur.base_sources)
        .map(([v, s]) => [v, { found: !String(s).startsWith('active base (no'), source: String(s) }])));
    }
  }, [cur?.slug, cur?.base_mode, cur?.updated_at]);

  const doUpscale = async () => {
    setMsg('');
    try {
      await j(await fetch(`${BASE}/characters/${slug}/base/upscale`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}',
      }));
      await loadCur();
    } catch (e) { setMsg((e as Error).message); }
  };

  const activate = async (vid: string) => {
    try {
      await j(await fetch(`${BASE}/characters/${slug}/base/activate`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ version_id: vid }),
      }));
      await loadCur();
    } catch (e) { setMsg((e as Error).message); }
  };

  const delVersion = async (vid: string) => {
    try {
      await j(await fetch(`${BASE}/characters/${slug}/base/${vid}/delete`, { method: 'POST' }));
      await loadCur();
    } catch (e) { setMsg((e as Error).message); }
  };

  const regenVersion = async (v: BaseVerT) => {
    if (!v.view || !v.kind.startsWith('stripped_')) return;
    setMsg('');
    try {
      await j(await fetch(`${BASE}/characters/${slug}/strip`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ mode: v.kind.replace('stripped_', ''), view: v.view }),
      }));
      await loadCur();
    } catch (e) { setMsg((e as Error).message); }
  };

  const baseFromRef = async (rid: string) => {
    try {
      await j(await fetch(`${BASE}/characters/${slug}/base/from_ref`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ref_id: rid }),
      }));
      await loadCur();
    } catch (e) { setMsg((e as Error).message); }
  };

  // ── pose actions (shared Pose Library 2.0) ────────────────────────────────
  const savePose = async (regenerate: boolean) => {
    if (!editPose) return;
    setPoseBusy(true); setPoseMsg('');
    try {
      await j(await fetch(`${POSE_BASE}/poses/${editPose.id}/update`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ prompt: editPrompt, regenerate }),
      }));
      setEditPose(null); await loadPoses();
      setPoseMsg(regenerate ? '✓ saved + regenerated' : '✓ saved');
    } catch (e) { setPoseMsg((e as Error).message); }
    setPoseBusy(false);
  };
  const createPose = async () => {
    if (!npName.trim() || !npDesc.trim()) return;
    setPoseBusy(true); setPoseMsg('');
    try {
      await j(await fetch(`${POSE_BASE}/poses`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: npName.trim(), category: catFilter || 'Custom', prompt: npDesc.trim() }),
      }));
      setNewPoseOpen(false); setNpName(''); setNpDesc('');
      await loadPoses(); setPoseMsg('✓ pose generated');
    } catch (e) { setPoseMsg((e as Error).message); }
    setPoseBusy(false);
  };
  const uploadPose = async (file: File) => {
    setPoseBusy(true); setPoseMsg('');
    try {
      const fd = new FormData();
      fd.append('file', file);
      fd.append('name', file.name.replace(/\.[^.]+$/, ''));
      if (catFilter) fd.append('category', catFilter);
      await j(await fetch(`${POSE_BASE}/poses/upload`, { method: 'POST', body: fd }));
      await loadPoses(); setPoseMsg('✓ uploaded (openpose/depth/photo all fine)');
    } catch (e) { setPoseMsg((e as Error).message); }
    setPoseBusy(false);
  };
  const seedDefaults = async () => {
    setPoseMsg('');
    try {
      await j(await fetch(`${POSE_BASE}/poses/seed-defaults`, { method: 'POST' }));
      setSeedRun({ status: 'running', done: 0, total: 1 }); await loadPoses();
    } catch (e) { setPoseMsg((e as Error).message); }
  };
  const importPack = async (file: File) => {
    setPoseBusy(true); setPoseMsg('📦 importing pack…');
    try {
      const fd = new FormData();
      fd.append('file', file);
      fd.append('category', catFilter || file.name.replace(/\.(zip|json)$/i, '').slice(0, 24) || 'Pack');
      const r = await j<{ imported: number; skipped: number; errors: string[] }>(
        await fetch(`${POSE_BASE}/poses/import-pack`, { method: 'POST', body: fd }));
      await loadPoses();
      setPoseMsg(`✓ pack: ${r.imported} poses imported${r.skipped ? `, ${r.skipped} dupes skipped` : ''}${r.errors?.length ? ` — ${r.errors.length} errors (${r.errors[0]})` : ''}`);
    } catch (e) { setPoseMsg(`pack import failed: ${(e as Error).message}`); }
    setPoseBusy(false);
  };
  const importPoses = async (file: File) => {
    setPoseBusy(true); setPoseMsg('');
    try {
      const fd = new FormData();
      fd.append('file', file);
      if (catFilter) fd.append('category', catFilter);   // import INTO the open set
      const r = await j<{ imported: number; skipped: number }>(
        await fetch(`${POSE_BASE}/poses/import`, { method: 'POST', body: fd }));
      await loadPoses();
      setPoseMsg(`✓ imported ${r.imported} into ${catFilter ? `set “${catFilter}”` : 'their own sets'}${r.skipped ? `, skipped ${r.skipped} (dupes/invalid)` : ''} — hit 🎨 to render them`);
    } catch (e) { setPoseMsg(`import failed: ${(e as Error).message}`); }
    setPoseBusy(false);
  };
  const generateMissing = async () => {
    setPoseMsg('');
    try {
      const r = await j<{ started: boolean; total?: number; note?: string }>(
        await fetch(`${POSE_BASE}/poses/generate-missing`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(catFilter ? { category: catFilter } : {}),
        }));
      if (!r.started) setPoseMsg(r.note || 'nothing to generate');
      else setBatchRun({ status: 'running', done: 0, total: r.total || 0 });
      await loadPoses();
    } catch (e) { setPoseMsg((e as Error).message); }
  };
  const delPose = async (id: string) => {
    setPoseBusy(true);
    try {
      await j(await fetch(`${POSE_BASE}/poses/${id}/delete`, { method: 'POST' }));
      if (poseId === id) setPoseId('');
      setEditPose(null); await loadPoses();
    } catch (e) { setPoseMsg((e as Error).message); }
    setPoseBusy(false);
  };

  // ── set management ────────────────────────────────────────────────────────
  const createSet = async () => {
    const nm = newSetName.trim();
    if (!nm) return;
    try {
      await j(await fetch(`${POSE_BASE}/pose-sets`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ category: nm }),
      }));
      setNewSetName('');
      await loadPoses();
      setCatFilter(nm);          // open the new (empty) set's screen
      setPoseMsg(`✓ set “${nm}” created — import or create poses into it`);
    } catch (e) { setPoseMsg((e as Error).message); }
  };

  const renameSet = async () => {
    if (!catFilter) return;
    const nn = window.prompt('Rename set to:', catFilter);
    if (!nn || nn.trim() === catFilter) return;
    try {
      await j(await fetch(`${POSE_BASE}/poses/set-rename`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ category: catFilter, new: nn.trim() }),
      }));
      if (selectedSet === catFilter) setSelectedSet(nn.trim());
      setCatFilter(nn.trim());
      await loadPoses();
    } catch (e) { setPoseMsg((e as Error).message); }
  };
  const purgeImageless = async () => {
    if (!catFilter) return;
    const targets = poses.filter((p) => p.category === catFilter && p.has_image === false);
    if (!targets.length) { setPoseMsg('no image-less poses in this set'); return; }
    if (!window.confirm(`Remove ${targets.length} image-less pose(s) from "${catFilter}"?`)) return;
    setPoseBusy(true);
    for (const p of targets) {
      try { await j(await fetch(`${POSE_BASE}/poses/${p.id}/delete`, { method: 'POST' })); }
      catch { /* keep going */ }
    }
    setPoseBusy(false);
    await loadPoses();
    setPoseMsg(`✓ removed ${targets.length} image-less pose(s)`);
  };

  const deleteSet = async () => {
    if (!catFilter) return;
    if (!window.confirm(`Delete set "${catFilter}" and ALL its poses?`)) return;
    try {
      await j(await fetch(`${POSE_BASE}/poses/set-delete`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ category: catFilter }),
      }));
      if (selectedSet === catFilter) setSelectedSet('');
      setCatFilter('');
      await loadPoses();
    } catch (e) { setPoseMsg((e as Error).message); }
  };

  // ── generate ──────────────────────────────────────────────────────────────
  const generateSetRun = async () => {
    setGenErr('');
    if (!cur?.has_base) { setGenErr('No base yet — tag a front reference or strip one.'); return; }
    if (!selectedSet && !selectedTags.length) { setGenErr('Pick a set or tags in the Pose Library.'); return; }
    try {
      await j(await fetch(`${BASE}/generate-set`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          slug, category: selectedSet || null,
          tags: selectedTags.length ? selectedTags : null, prompt_extra: extra,
          width: 832, height: 1216, seed: seed.trim() ? Number(seed.trim()) : null,
        }),
      }));
      await loadCur(); await loadGens();
    } catch (e) { setGenErr((e as Error).message); }
  };

  const doGenerate = async () => {
    if (selectedSet || selectedTags.length) { await generateSetRun(); return; }
    setGenErr('');
    if (!cur?.has_base) { setGenErr('No base yet — tag a front reference or strip one.'); return; }
    if (!poseId) { setGenErr('Pick a pose in the Pose Library.'); return; }
    try {
      const r = await j<{ gen_id: string }>(await fetch(`${BASE}/generate`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          slug, pose_id: poseId, prompt_extra: extra, count,
          width: 832, height: 1216, seed: seed.trim() ? Number(seed.trim()) : null,
        }),
      }));
      setGen({ gen_id: r.gen_id, status: 'running', done: 0, total: count, prompt: '', images: [], refs: [] });
    } catch (e) { setGenErr((e as Error).message); }
  };

  const shownPoses = poses
    .filter((p) => !catFilter || (p.set || p.category) === catFilter)
    .filter((p) => !tagsSel.length || (p.tags || []).some((t) => tagsSel.includes(t)));
  const selPose = poses.find((p) => p.id === poseId) || null;
  const jobs = cur?.jobs || {};
  const taskIcon = (s: string) => s === 'done' ? '✓' : s === 'error' ? '✗' : s === 'running' ? '⏳' : '·';
  const jobLine = (k: string, jb?: JobT) => {
    if (!jb?.status) return null;
    let line = `${k}: ${jb.status}${jb.detail ? ` (${jb.detail})` : ''}${jb.worker ? ` @ ${jb.worker}` : ''}`;
    if (jb.tasks) {
      line += ' — ' + Object.entries(jb.tasks)
        .map(([key, t]) => `${key}${t.worker ? `@${t.worker}` : ''} ${taskIcon(t.status)}`).join(' · ');
    }
    if (jb.error) line += ` — ${jb.error}`;
    return line;
  };

  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'minmax(330px,1fr) minmax(380px,1.2fr) minmax(360px,1.1fr)', gap: 16, alignItems: 'start' }}>
      {/* v1.276.0 — WHO AM I WORKING ON. The character was only identifiable by
          reading the dropdown's current value, which is easy to lose track of
          once you are three columns deep in refs and poses. */}
      {slug && (
        <div style={{ gridColumn: '1 / -1', display: 'flex', alignItems: 'center', gap: 10,
                      background: '#161a22', border: '1px solid #2a2f3a', borderRadius: 8,
                      padding: '7px 12px', position: 'sticky', top: 0, zIndex: 5 }}>
          <span style={{ fontSize: 15, fontWeight: 700, color: '#e6e9ee' }}>
            🎯 {chars.find((c) => c.slug === slug)?.name || slug}
          </span>
          <span style={{ fontSize: 11, color: '#8d97a5' }}>
            Klein 3.0 · {chars.find((c) => c.slug === slug)?.ref_count ?? 0} refs
            {chars.find((c) => c.slug === slug)?.has_base ? ' · ✓ base' : ' · no base yet'}
          </span>
        </div>
      )}
      {/* ── column 1: character + description ── */}
      <div style={{ ...box, display: 'grid', gap: 10, alignContent: 'start' }}>
        <h3 style={{ margin: 0, fontSize: 15 }}>🎯 Character</h3>
        <div style={{ display: 'flex', gap: 6 }}>
          <select style={input} value={slug} onChange={(e) => setSlug(e.target.value)}>
            {chars.map((c) => <option key={c.slug} value={c.slug}>{c.name}{c.has_base ? ' — ✓ base' : ''}</option>)}
            {!chars.length && <option value="">(no characters yet)</option>}
          </select>
        </div>
        <div style={{ display: 'flex', gap: 6 }}>
          <input style={input} value={newName} onChange={(e) => setNewName(e.target.value)} placeholder="new character name" />
          <button style={btnGhost} disabled={busy || !newName.trim()} onClick={createChar}>➕</button>
        </div>
        {msg && <p style={msg.startsWith('✓') ? okTxt : errTxt}>{msg}</p>}
        {cur && (
          <>
            <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
              <b style={{ fontSize: 13, color: '#e6e9ee' }}>Description</b>
              <div style={{ flex: 1 }} />
              <button style={btnSm} disabled={busy} onClick={saveFields}>💾 Save</button>
            </div>
            <button style={{ ...btn, background: '#7c3aed' }} disabled={analyzing || !(cur.refs?.length)}
                    onClick={() => void analyzeRefs()}>
              {analyzing ? '🪄 Analyzing…' : '🪄 Analyze references (LLM) → fill description'}
            </button>
            <p style={{ ...hint, margin: 0 }}>
              Sends up to 4 refs (front + face first) to the vision LLM and fills the fields
              below — review + edit, then 💾 Save. The tiny 🪄 on a thumbnail analyzes just that one.
            </p>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 6 }}>
              {FIELDS.map(([k, lbl]) => (
                <div key={k} style={k === 'additional_details' || k === 'aesthetics' ? { gridColumn: '1 / -1' } : undefined}>
                  <label style={label}>{lbl}</label>
                  <input style={input} value={fields[k] || ''} onChange={(e) => setFields({ ...fields, [k]: e.target.value })} />
                </div>
              ))}
            </div>
            <div>
              <label style={label}>Active base (image 1 of every generation)</label>
              {cur.active_base_url
                ? <img src={`${cur.active_base_url}?t=${cur.updated_at || ''}`} alt="base"
                       style={{ width: 150, borderRadius: 6, border: '1px solid #2a2f3a', cursor: 'zoom-in' }}
                       onClick={() => setLightbox(`${cur.active_base_url}?t=${cur.updated_at || ''}`)} />
                : <p style={{ ...hint, margin: 0 }}>none yet — tag a front ref, or strip one below</p>}
              {cur.active_base_url && (
                <div style={{ marginTop: 6 }}>
                  <button style={btnSm} disabled={jobs.upscale?.status === 'running'} onClick={doUpscale}>
                    {jobs.upscale?.status === 'running' ? '⏳ Upscaling…' : '⬆ Upscale base (result becomes active)'}
                  </button>
                </div>
              )}
            </div>
            {(cur.base_versions?.length || 0) > 0 && (
              <div>
                <label style={label}>Base versions (click to activate)</label>
                <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                  {cur.base_versions!.map((v) => (
                    <div key={v.id} onClick={() => activate(v.id)} title={`${v.view ? v.view + ' · ' : ''}${v.kind} — click to use as image 1`}
                         style={{ cursor: 'pointer', border: `2px solid ${cur.active_base === v.id ? '#3b82f6' : '#2a2f3a'}`, borderRadius: 6, padding: 2 }}>
                      <img src={`${v.url}?t=${v.created_at || ''}`} alt={v.kind} style={{ width: 64, borderRadius: 4, display: 'block' }} />
                      <div style={{ fontSize: 10, color: cur.active_base === v.id ? '#9cc2ff' : '#8d97a5', textAlign: 'center' }}>
                        {v.view ? `${v.view} ` : ''}{v.kind.replace('stripped_', '👙')}
                      </div>
                      <div style={{ display: 'flex', gap: 2, justifyContent: 'center', marginTop: 2 }}>
                        {v.kind.startsWith('stripped_') && v.view && (
                          <button style={{ ...btnSm, padding: '1px 5px', fontSize: 10 }}
                                  title={`Regenerate this ${v.view} ${v.kind.replace('stripped_', '')} image (replaces this slot)`}
                                  disabled={jobs.strip?.status === 'running'}
                                  onClick={(e) => { e.stopPropagation(); void regenVersion(v); }}>🔁</button>
                        )}
                        <button style={{ ...btnSm, padding: '1px 5px', fontSize: 10, color: '#ff8a8a' }}
                                title="Delete this version"
                                onClick={(e) => { e.stopPropagation(); void delVersion(v.id); }}>🗑</button>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </>
        )}
      </div>

      {/* ── column 2: references + views + strip ── */}
      <div style={{ ...box, display: 'grid', gap: 10, alignContent: 'start' }}>
        <h3 style={{ margin: 0, fontSize: 15 }}>🖼 References</h3>
        <div style={{ display: 'flex', gap: 6, alignItems: 'center', flexWrap: 'wrap' }}>
          <select style={{ ...input, width: 'auto' }} value={upTag} onChange={(e) => setUpTag(e.target.value)}>
            {TAGS.map((t) => <option key={t} value={t}>{t}</option>)}
          </select>
          <label style={{ ...btnSm, display: 'inline-block' }}>
            ⬆ Upload reference
            <input type="file" accept="image/*" multiple style={{ display: 'none' }}
                   onChange={(e) => {
                     const files = Array.from(e.target.files || []);
                     e.target.value = '';
                     void (async () => { for (const f of files) await uploadRef(f); })();
                   }} />
          </label>
          {cur && cur.missing_views.length > 0 && (
            <button style={btnSm} disabled={jobs.views?.status === 'running'} onClick={genViews}>
              {jobs.views?.status === 'running'
                ? `⏳ Views ${jobs.views.detail || ''}`
                : `🧭 Generate missing views (${cur.missing_views.join(', ')})`}
            </button>
          )}
          {cur && cur.missing_views.length === 0 && (cur.ref_count || 0) > 0 && (
            <button style={btnSm} disabled={jobs.views?.status === 'running'}
                    title="Renders a zoomed face close-up FIRST, then regenerates all four views with it as the lead reference — use when the set's faces drifted."
                    onClick={regenViewsFaced}>
              {jobs.views?.status === 'running'
                ? `⏳ Views ${jobs.views.detail || ''}`
                : '🙂 Regenerate views (face-anchored)'}
            </button>
          )}
        </div>
        {['views', 'strip', 'upscale'].map((k) => {
          const l = jobLine(k, jobs[k]);
          return l && jobs[k]?.status !== 'done' ? <p key={k} style={jobs[k]?.status === 'error' ? errTxt : hint}>{l}</p> : null;
        })}
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(104px, 1fr))', gap: 8, maxHeight: 340, overflowY: 'auto' }}>
          {(cur?.refs || []).map((r) => (
            <div key={r.id} style={{ border: '1px solid #2a2f3a', borderRadius: 8, padding: 4, background: '#0e1116' }}>
              <img src={r.url} alt={r.name} style={{ width: '100%', borderRadius: 5, display: 'block', cursor: 'zoom-in' }}
                   onClick={() => setLightbox(r.url)} />
              <select style={{ ...input, padding: '2px 4px', fontSize: 11, marginTop: 3 }} value={r.tag}
                      onChange={(e) => void setTag(r.id, e.target.value)}>
                {TAGS.map((t) => <option key={t} value={t}>{t}</option>)}
              </select>
              <div style={{ display: 'flex', gap: 4, marginTop: 3 }}>
                <button style={{ ...btnSm, padding: '2px 5px', fontSize: 10 }} title="Fill description fields from this image"
                        disabled={analyzing} onClick={() => void analyzeRefs(r)}>🪄</button>
                <button style={{ ...btnSm, padding: '2px 5px', fontSize: 10 }} title="Use this image as the base directly"
                        onClick={() => void baseFromRef(r.id)}>⭐</button>
                <div style={{ flex: 1 }} />
                <button style={{ ...btnSm, padding: '2px 5px', fontSize: 10, color: '#ff8a8a' }} onClick={() => void delRef(r.id)}>🗑</button>
              </div>
            </div>
          ))}
          {!cur?.refs?.length && <p style={hint}>No references yet — upload some (tag the frontal one “front”).</p>}
        </div>

        <div style={{ border: '1px solid #2a2f3a', borderRadius: 8, padding: 10, display: 'grid', gap: 8 }}>
          <b style={{ fontSize: 13, color: '#e6e9ee' }}>🧥 Identity source — dressed or stripped</b>
          <p style={{ ...hint, margin: 0 }}>
            Which image every render starts from. <b>Dressed</b> uses your uploaded references and
            generated views, so his own clothes are kept and nothing has to be stripped.
            <b> Stripped</b> uses the stripped base set (needed when Klein must replace the
            clothing outright). Stripping is an extra edit per view and adds its own drift, so
            skip it when the shot never needed a clothing change.
          </p>
          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
            {([
              ['dressed', '🧥 Dressed', 'His own clothes, from your references'],
              ['stripped', '👙 Stripped', 'The stripped base set'],
              ['auto', '🤖 Auto', 'Newest version of each view (pre-v1.217 behaviour)'],
            ] as const).map(([m, label, tip]) => (
              <button key={m} title={tip}
                      style={baseMode === m ? { ...btn, background: '#2b6cb0' } : btnGhost}
                      disabled={!cur || baseBusy}
                      onClick={() => void setMode(m)}>{label}</button>
            ))}
            {baseBusy && <span style={{ ...hint, alignSelf: 'center' }}>working…</span>}
          </div>
          {baseResolve && (
            <div style={{ display: 'grid', gap: 2 }}>
              <span style={{ ...hint, margin: 0 }}>What each view would use right now:</span>
              {VIEWS.map((v) => {
                const r = baseResolve[v];
                if (!r) return null;
                const stripped = /stripped/.test(r.source);
                const fallback = /fallback|no .* view yet|active base/.test(r.source);
                return (
                  <div key={v} style={{ display: 'flex', gap: 6, fontSize: 11, fontFamily: 'monospace' }}>
                    <span style={{ color: '#8fa6bd', width: 46 }}>{v}</span>
                    <span style={{ color: fallback ? '#e0b36a' : stripped ? '#cbd2dc' : '#5ee08a' }}>
                      {r.source}
                    </span>
                  </div>
                );
              })}
              <span style={{ ...hint, margin: '2px 0 0' }}>
                Green = a real reference for that view. Amber = falling back, generate that view
                or tag a reference for it.
              </span>
            </div>
          )}
        </div>

        <div style={{ border: '1px solid #2a2f3a', borderRadius: 8, padding: 10, display: 'grid', gap: 8 }}>
          <b style={{ fontSize: 13, color: '#e6e9ee' }}>👙 Strip → base set</b>
          <p style={{ ...hint, margin: 0 }}>
            Strips the FULL standing set (newest ref of each tagged view — front/back/left/right)
            in parallel across the workers; identity, pose and framing kept, all footwear removed.
            The front result becomes the active base; click any version in the strip to use a
            different one for pose generation. Pick a single source below to strip just one.
          </p>
          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center' }}>
            <select style={{ ...input, width: 'auto' }} value={stripMode} onChange={(e) => setStripMode(e.target.value as 'underwear' | 'nude')}>
              <option value="underwear">Underwear</option>
              <option value="nude">Nude</option>
            </select>
            <select style={{ ...input, width: 'auto', maxWidth: 180 }} value={stripSrc} onChange={(e) => setStripSrc(e.target.value)}>
              <option value="">source: ALL tagged views (set)</option>
              {(cur?.refs || []).map((r) => <option key={r.id} value={r.id}>only {r.tag}: {r.name.slice(0, 18)}</option>)}
            </select>
            <button style={btn} disabled={!cur || jobs.strip?.status === 'running'} onClick={doStrip}>
              {jobs.strip?.status === 'running' ? '⏳ Stripping…' : stripSrc ? 'Strip selected ref' : 'Strip the whole set'}
            </button>
          </div>
        </div>
      </div>

      {/* ── column 3: poses + generate ── */}
      <div style={{ ...box, display: 'grid', gap: 10, alignContent: 'start', position: 'sticky', top: 12, maxHeight: 'calc(100vh - 24px)', overflowY: 'auto' }}>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          <h3 style={{ margin: 0, fontSize: 15 }}>🕺 Pose → 🚀 Generate</h3>
          <div style={{ flex: 1 }} />
          <button style={btnSm} onClick={() => void loadPoses()}>Refresh</button>
        </div>
        <p style={{ ...hint, margin: 0 }} title="Batches are threaded across every klein-capable worker">
          {workers.length
            ? <>workers: {workers.map((w) => (
                <span key={w.url} style={{ color: w.healthy ? (w.klein ? '#5ee08a' : '#cbd2dc') : '#ff8a8a', marginRight: 8 }}>
                  {w.url}{w.klein ? ' 🧪' : ''}{w.healthy ? '' : ' (down)'}{typeof w.in_flight === 'number' && w.in_flight > 0 ? ` ·${w.in_flight}` : ''}
                </span>
              ))}</>
            : 'workers: none detected'}
        </p>
        <button style={{ ...btn, background: '#334155' }} onClick={() => setLibOpen(true)}>
          🕺 Open Pose Library{poses.length ? ` (${poses.length} poses · ${cats.length} sets)` : ''}
        </button>
        {selectedSet && (
          <div style={{ border: '1px solid #3b82f6', borderRadius: 8, padding: 8, display: 'flex', gap: 8, alignItems: 'center' }}>
            <b style={{ fontSize: 12, color: '#9cc2ff' }}>📦 Set: {selectedSet}</b>
            <span style={hint}>
              {poses.filter((p) => (p.set || p.category) === selectedSet && p.has_image !== false).length} rendered poses — 🚀 runs the whole set (1 image per pose)
            </span>
            <div style={{ flex: 1 }} />
            <button style={btnSm} onClick={() => setSelectedSet('')}>✖</button>
          </div>
        )}
        {selectedTags.length > 0 && (
          <div style={{ border: '1px solid #a855f7', borderRadius: 8, padding: 8, display: 'flex', gap: 8, alignItems: 'center' }}>
            <b style={{ fontSize: 12, color: '#d8b4fe' }}>🏷 Tags: {selectedTags.join(' + ')}</b>
            <span style={hint}>
              {poses.filter((p) => p.has_image !== false && (p.tags || []).some((t) => selectedTags.includes(t))).length} rendered poses across all sets — 1 image per pose
            </span>
            <div style={{ flex: 1 }} />
            <button style={btnSm} onClick={() => setSelectedTags([])}>✖</button>
          </div>
        )}
        {jobs.set?.status && jobs.set.status !== 'done' && (
          <p style={jobs.set.status === 'error' ? errTxt : hint}>
            📦 set run: {jobs.set.status} {jobs.set.detail || ''}
            {jobs.set.tasks ? '  ·  ' + Object.entries(jobs.set.tasks).filter(([, t]) => t.status === 'running')
              .map(([k, t]) => `${poses.find((p) => p.id === k)?.name || k}${t.worker ? ` @ ${t.worker}` : ''} ⏳`).join(' · ') : ''}
            {jobs.set.error ? ` — ${jobs.set.error}` : ''}
          </p>
        )}
        {libOpen && (
        <div style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.75)', zIndex: 9990, display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 24 }}
             onClick={() => setLibOpen(false)}>
        <div style={{ ...box, width: 'min(1000px, 94vw)', maxHeight: '92vh', overflowY: 'auto', display: 'grid', gap: 10, alignContent: 'start' }}
             onClick={(e) => e.stopPropagation()}>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          <h3 style={{ margin: 0, fontSize: 15 }}>🕺 Pose Library <span style={hint}>— shared across all characters</span></h3>
          <div style={{ flex: 1 }} />
          <button style={btnSm} onClick={() => setLibOpen(false)}>✖ Close</button>
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: '210px 1fr', gap: 12, alignItems: 'start', minHeight: 320 }}>
          <div style={{ display: 'grid', gap: 6, alignContent: 'start' }}>
            <div style={{ display: 'flex', gap: 4 }}>
              <input style={{ ...input, fontSize: 12 }} placeholder="new set name" value={newSetName}
                     onChange={(e) => setNewSetName(e.target.value)}
                     onKeyDown={(e) => { if (e.key === 'Enter') void createSet(); }} />
              <button style={btnSm} disabled={!newSetName.trim()} onClick={() => void createSet()}>➕</button>
            </div>
            <button style={{ ...chip(!catFilter), textAlign: 'left' }} onClick={() => setCatFilter('')}>
              🌐 All poses ({poses.length})
            </button>
            {setsInfo.map((si) => (
              <button key={si.name} style={{ ...chip(catFilter === si.name), textAlign: 'left' }}
                      onClick={() => setCatFilter(si.name)}>
                📦 {si.name} <span style={{ opacity: 0.7 }}>({si.rendered}/{si.count})</span>
              </button>
            ))}
            {!setsInfo.length && <p style={hint}>No sets yet — create one above.</p>}
          </div>
          <div style={{ display: 'grid', gap: 10, alignContent: 'start', minWidth: 0 }}>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
          <b style={{ fontSize: 14, color: '#e6e9ee' }}>{catFilter ? `📦 ${catFilter}` : '🌐 All poses'}</b>
          <div style={{ flex: 1 }} />
          {catFilter && (
            <>
              <button style={{ ...btn, padding: '5px 10px' }}
                      onClick={() => { setSelectedSet(catFilter); setSelectedTags([]); setPoseId(''); setLibOpen(false); }}>
                ▶ Use set “{catFilter}”
              </button>
              <button style={btnSm} onClick={() => void renameSet()}>✏️ rename</button>
              <button style={btnSm} disabled={poseBusy} title="Remove poses in this set that have no rendered image"
                      onClick={() => void purgeImageless()}>🧹 purge image-less</button>
              <button style={{ ...btnSm, color: '#ff8a8a' }} onClick={() => void deleteSet()}>🗑 delete set</button>
            </>
          )}
        </div>
        {allTags.length > 0 && (
          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center' }}>
            <span style={hint}>tags:</span>
            {allTags.map((t) => (
              <button key={t} style={chip(tagsSel.includes(t))}
                      onClick={() => setTagsSel((ts) => ts.includes(t) ? ts.filter((x) => x !== t) : [...ts, t])}>
                {t}
              </button>
            ))}
            {tagsSel.length > 0 && (
              <>
                <button style={btnSm} onClick={() => setTagsSel([])}>clear</button>
                <button style={{ ...btn, padding: '4px 10px' }}
                        title="Generate every rendered pose carrying any of these tags — across ALL sets"
                        onClick={() => { setSelectedTags(tagsSel); setSelectedSet(''); setPoseId(''); setLibOpen(false); }}>
                  ▶ Use {poses.filter((p) => p.has_image !== false && (p.tags || []).some((t) => tagsSel.includes(t))).length} tagged poses
                </button>
              </>
            )}
          </div>
        )}
        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center' }}>
          {!catFilter && <span style={hint}>open a set on the left to import or create poses ·</span>}
          {catFilter && <button style={btnSm} onClick={() => setNewPoseOpen((o) => !o)}>➕ New (prompt)</button>}
          {catFilter && <label style={{ ...btnSm, display: 'inline-block' }}>
            ⬆ Upload (photo / openpose / depth)
            <input type="file" accept="image/*" style={{ display: 'none' }}
                   onChange={(e) => { const f = e.target.files?.[0]; if (f) void uploadPose(f); e.target.value = ''; }} />
          </label>}
          <button style={btnSm} disabled={seedRun?.status === 'running'} onClick={seedDefaults}
                  title="Generates the built-in starter poses into a 'Defaults' set">
            {seedRun?.status === 'running' ? `✨ ${seedRun.done}/${seedRun.total}…` : '✨ Defaults'}
          </button>
          {catFilter && <label style={{ ...btnSm, display: 'inline-block' }}
                 title={'Batch import pose definitions INTO this set.\nJSON: [{"name":"Hero landing","prompt":"crouched superhero landing...","category":"Action"}]\nRow category/tags become TAGS on each pose — the SET is this one.\nCSV headers: name,prompt[,category][,tags][,raw]'}>
            📥 Import poses (.json/.csv)
            <input type="file" accept=".json,.csv,application/json,text/csv" style={{ display: 'none' }}
                   onChange={(e) => { const f = e.target.files?.[0]; if (f) void importPoses(f); e.target.value = ''; }} />
          </label>}
          {catFilter && <label style={{ ...btnSm, display: 'inline-block' }}
                 title={'Import a pose PACK into this set:\n• .zip of control images — openpose skeletons, depth maps, DWpose renders\n• openpose keypoint .json files — rendered to skeleton images automatically\nPose names come from filenames.'}>
            📦 Import pack (.zip/openpose .json)
            <input type="file" accept=".zip,.json,application/zip,application/json" style={{ display: 'none' }}
                   onChange={(e) => { const f = e.target.files?.[0]; if (f) void importPack(f); e.target.value = ''; }} />
          </label>}
          <button style={btnSm}
                  title="Download instructions to hand any LLM: tell it what poses you want, paste this doc, get a ready-to-import file back"
                  onClick={() => {
                    const blob = new Blob([POSE_IMPORT_LLM_DOC], { type: 'text/markdown' });
                    const a = document.createElement('a');
                    a.href = URL.createObjectURL(blob);
                    a.download = 'pose_set_llm_instructions.md';
                    a.click();
                    URL.revokeObjectURL(a.href);
                  }}>📄 LLM guide</button>
          {(() => {
            const missing = poses.filter((p) => p.prompt && p.has_image === false
              && (!catFilter || (p.set || p.category) === catFilter)).length;
            return missing > 0 ? (
              <button style={{ ...btnSm, borderColor: '#3b82f6', color: '#9cc2ff' }}
                      disabled={batchRun?.status === 'running'} onClick={generateMissing}>
                {batchRun?.status === 'running' ? `🎨 ${batchRun.done}/${batchRun.total}…` : `🎨 Generate missing (${missing})`}
              </button>
            ) : null;
          })()}
        </div>
        {batchRun?.status === 'running' && batchRun.tasks && (
          <p style={{ ...hint, margin: 0 }}>
            {Object.values(batchRun.tasks).filter((t) => t.status === 'running')
              .map((t) => `${t.name}${t.worker ? ` @ ${t.worker}` : ''} ⏳`).join('  ·  ') || 'queueing…'}
          </p>
        )}
        {batchRun && batchRun.status !== 'running' && !!batchRun.errors?.length && (
          <p style={errTxt}>batch: {batchRun.errors.slice(0, 3).join('; ')}</p>
        )}
        {newPoseOpen && (
          <div style={{ border: '1px solid #2a2f3a', borderRadius: 8, padding: 8, display: 'grid', gap: 6 }}>
            <input style={input} value={npName} onChange={(e) => setNpName(e.target.value)} placeholder="pose name *" />
            <textarea style={{ ...input, minHeight: 48 }} value={npDesc} onChange={(e) => setNpDesc(e.target.value)}
                      placeholder="pose description (mannequin style added automatically)" />
            <button style={btn} disabled={poseBusy || !npName.trim() || !npDesc.trim()} onClick={createPose}>
              {poseBusy ? 'Generating…' : 'Generate pose image'}
            </button>
          </div>
        )}
        {poseMsg && <p style={poseMsg.startsWith('✓') ? okTxt : errTxt}>{poseMsg}</p>}
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(84px, 1fr))', gap: 6, maxHeight: 260, overflowY: 'auto' }}>
          {shownPoses.map((p) => (
            <div key={p.id}
                 style={{ border: `2px solid ${poseId === p.id ? '#3b82f6' : '#2a2f3a'}`, borderRadius: 8, padding: 3,
                          cursor: p.has_image === false ? 'default' : 'pointer', background: '#0e1116',
                          opacity: p.has_image === false ? 0.75 : 1 }}
                 onClick={() => { if (p.has_image !== false) { setPoseId(p.id); setSelectedSet(''); setSelectedTags([]); setLibOpen(false); } }}>
              {p.has_image === false
                ? <div style={{ width: '100%', aspectRatio: '832/1216', borderRadius: 5, border: '1px dashed #3a4150',
                                display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 10, color: '#8d97a5' }}>
                    no image yet
                  </div>
                : <img src={p.url} alt={p.name} style={{ width: '100%', borderRadius: 5, display: 'block' }} />}
              <div style={{ fontSize: 10, color: '#cbd2dc', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{p.name}</div>
              <button style={{ ...btnSm, padding: '1px 5px', fontSize: 10 }}
                      onClick={(e) => { e.stopPropagation(); setEditPose(p); setEditPrompt(p.prompt); }}>
                {p.prompt ? '✏️' : 'ℹ️'}
              </button>
            </div>
          ))}
          {!shownPoses.length && <p style={hint}>No poses — hit ✨ Defaults.</p>}
        </div>
        {editPose && (
          <div style={{ border: '1px solid #3b82f6', borderRadius: 8, padding: 8, display: 'grid', gap: 6 }}>
            <b style={{ fontSize: 12, color: '#e6e9ee' }}>✏️ {editPose.name} <span style={hint}>({editPose.source})</span></b>
            {editPose.prompt || editPose.source === 'generated' ? (
              <>
                <textarea style={{ ...input, minHeight: 70 }} value={editPrompt} onChange={(e) => setEditPrompt(e.target.value)} />
                <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                  <button style={btnSm} disabled={poseBusy} onClick={() => void savePose(false)}>💾 Save</button>
                  <button style={btnGhost} disabled={poseBusy} onClick={() => void savePose(true)}>🔁 Save + regenerate</button>
                  <div style={{ flex: 1 }} />
                  <button style={{ ...btnSm, color: '#ff8a8a' }} disabled={poseBusy} onClick={() => void delPose(editPose.id)}>🗑</button>
                  <button style={btnSm} onClick={() => setEditPose(null)}>Close</button>
                </div>
              </>
            ) : (
              <div style={{ display: 'flex', gap: 6 }}>
                <span style={hint}>Uploaded pose — no prompt.</span>
                <div style={{ flex: 1 }} />
                <button style={{ ...btnSm, color: '#ff8a8a' }} disabled={poseBusy} onClick={() => void delPose(editPose.id)}>🗑</button>
                <button style={btnSm} onClick={() => setEditPose(null)}>Close</button>
              </div>
            )}
          </div>
        )}
          </div>
        </div>
        </div>
        </div>
        )}

        <div style={{ borderTop: '1px solid #2a2f3a', paddingTop: 10, display: 'grid', gap: 8 }}>
          <div style={{ display: 'flex', gap: 8 }}>
            <div style={{ flex: 1 }}>
              <label style={label}>Image 1 — base</label>
              {cur?.active_base_url
                ? <img src={`${cur.active_base_url}?t=${cur.updated_at || ''}`} alt="base"
                       style={{ width: '100%', borderRadius: 6, border: '1px solid #2a2f3a', cursor: 'zoom-in' }}
                       onClick={() => setLightbox(`${cur.active_base_url}?t=${cur.updated_at || ''}`)} />
                : <p style={{ ...hint, margin: 0 }}>no base</p>}
            </div>
            <div style={{ flex: 1 }}>
              <label style={label}>Image 2 — {selectedSet ? 'pose SET' : selectedTags.length ? 'tagged poses' : 'pose'}</label>
              {selectedSet || selectedTags.length
                ? <div style={{ border: '1px dashed #3b82f6', borderRadius: 6, padding: 10, textAlign: 'center' }}>
                    <div style={{ fontSize: 22 }}>{selectedSet ? '📦' : '🏷'}</div>
                    <div style={{ fontSize: 12, color: '#9cc2ff' }}>{selectedSet || selectedTags.join(' + ')}</div>
                    <div style={hint}>{(selectedSet
                      ? poses.filter((p) => (p.set || p.category) === selectedSet && p.has_image !== false)
                      : poses.filter((p) => p.has_image !== false && (p.tags || []).some((t) => selectedTags.includes(t)))
                    ).length} poses · 1 image each</div>
                  </div>
                : selPose ? <img src={selPose.url} alt="pose"
                              style={{ width: '100%', borderRadius: 6, border: '1px solid #2a2f3a', cursor: 'zoom-in' }}
                              onClick={() => setLightbox(selPose.url)} />
                : <p style={{ ...hint, margin: 0 }}>pick a pose or set in the library</p>}
            </div>
          </div>
          <div>
            <label style={label}>Extra prompt (outfit, scene, lighting — optional)</label>
            <textarea style={{ ...input, minHeight: 48 }} value={extra} onChange={(e) => setExtra(e.target.value)}
                      placeholder="wearing the red jacket, on a rainy street at night" />
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
            <div><label style={label}>{selectedSet || selectedTags.length ? 'Images (1 per pose)' : 'Images'}</label>
              <select style={input} value={String(count)} disabled={!!selectedSet || selectedTags.length > 0}
                      onChange={(e) => setCount(Number(e.target.value))}>
                {[1, 2, 3, 4, 6, 8].map((n) => <option key={n} value={n}>{n}</option>)}
              </select></div>
            <div><label style={label}>Seed (blank = random)</label>
              <input style={input} value={seed} onChange={(e) => setSeed(e.target.value)} placeholder="random" /></div>
          </div>
          <button style={{ ...btn, opacity: cur?.has_base && (poseId || selectedSet || selectedTags.length) ? 1 : 0.5 }}
                  disabled={!cur?.has_base || (!poseId && !selectedSet && !selectedTags.length) || jobs.set?.status === 'running'}
                  onClick={doGenerate}>
            {selectedSet ? `🚀 Generate SET “${selectedSet}”`
              : selectedTags.length ? `🚀 Generate ${selectedTags.join('+')} poses` : '🚀 Generate'}
          </button>
          {genErr && <p style={errTxt}>{genErr}</p>}
          {gen && (
            <div style={{ display: 'grid', gap: 8 }}>
              <p style={{ ...hint, margin: 0 }}>
                {gen.status === 'running' ? `⏳ ${gen.done}/${gen.total}…`
                  : gen.status === 'done' ? `✓ done (${gen.images.length})` : `⚠ ${gen.error || 'error'}`}
                {gen.workers?.length ? `  ·  threaded across ${gen.workers.length} worker${gen.workers.length > 1 ? 's' : ''}` : ''}
              </p>
              {gen.tasks && gen.status === 'running' && (
                <p style={{ ...hint, margin: 0 }}>
                  {Object.entries(gen.tasks).map(([k, t]) =>
                    `img ${Number(k) + 1}${t.worker ? ` @ ${t.worker}` : ''} ${taskIcon(t.status)}`).join('  ·  ')}
                </p>
              )}
              {!!gen.refs.length && (
                <div style={{ display: 'flex', gap: 6 }}>
                  {gen.refs.map((r) => (
                    <img key={r.name} src={r.url} alt={r.name} title={r.name}
                         style={{ width: 50, borderRadius: 4, border: '1px solid #2a2f3a', cursor: 'zoom-in' }}
                         onClick={() => setLightbox(r.url)} />
                  ))}
                  <span style={{ ...hint, alignSelf: 'center' }}>← exact refs used</span>
                </div>
              )}
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
                {gen.images.map((im) => (
                  <img key={im.id} src={im.url} alt={im.id}
                       style={{ width: '100%', borderRadius: 6, border: '1px solid #2a2f3a', cursor: 'zoom-in' }}
                       onClick={() => setLightbox(im.url)} />
                ))}
              </div>
            </div>
          )}
        </div>

        <div style={{ borderTop: '1px solid #2a2f3a', paddingTop: 10, display: 'grid', gap: 8 }}>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            <b style={{ fontSize: 13, color: '#e6e9ee' }}>📚 Saved results</b>
            <button style={chip(gensPoseOnly)} onClick={() => setGensPoseOnly((o) => !o)}
                    title="Only show batches made with the currently selected pose">
              {gensPoseOnly ? 'this pose ✓' : 'this pose'}
            </button>
            <div style={{ flex: 1 }} />
            <button style={btnSm} onClick={() => void loadGens()}>Refresh</button>
          </div>
          {(() => {
            const shown = gens.filter((g) => g.gen_id !== gen?.gen_id)
              .filter((g) => !gensPoseOnly || !poseId || g.pose_id === poseId);
            if (!shown.length) return <p style={{ ...hint, margin: 0 }}>No saved batches{gensPoseOnly ? ' for this pose' : ''} yet — every 🚀 run is kept here, linked to its pose.</p>;
            return shown.map((g) => (
              <div key={g.gen_id} style={{ border: '1px solid #2a2f3a', borderRadius: 8, padding: 6, display: 'grid', gap: 4 }}>
                <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
                  <span style={{ fontSize: 11, color: '#cbd2dc' }}>🕺 {g.pose || 'pose'}</span>
                  <span style={{ ...hint, fontSize: 10 }}>{(g.created_at || '').slice(0, 16).replace('T', ' ')}</span>
                  {g.status !== 'done' && <span style={g.status === 'error' ? errTxt : hint}>{g.status}</span>}
                  <div style={{ flex: 1 }} />
                  {g.pose_id && (
                    <button style={{ ...btnSm, padding: '1px 6px', fontSize: 10 }} title="Select this batch's pose"
                            onClick={() => setPoseId(g.pose_id!)}>use pose</button>
                  )}
                  <button style={{ ...btnSm, padding: '1px 6px', fontSize: 10, color: '#ff8a8a' }}
                          onClick={async () => {
                            try { await j(await fetch(`${BASE}/gen/${g.gen_id}/delete`, { method: 'POST' })); await loadGens(); }
                            catch (e) { setGenErr((e as Error).message); }
                          }}>🗑</button>
                </div>
                <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
                  {g.refs.map((r) => (
                    <img key={r.name} src={r.url} alt={r.name} title={`ref: ${r.name}`}
                         style={{ width: 34, borderRadius: 3, border: '1px dashed #2a2f3a', cursor: 'zoom-in', opacity: 0.75 }}
                         onClick={() => setLightbox(r.url)} />
                  ))}
                  {g.images.map((im) => (
                    <img key={im.id} src={im.url} alt={im.id} title={`seed ${im.seed ?? '?'}`}
                         style={{ width: 64, borderRadius: 4, border: '1px solid #2a2f3a', cursor: 'zoom-in' }}
                         onClick={() => setLightbox(im.url)} />
                  ))}
                </div>
              </div>
            ));
          })()}
        </div>
      </div>
      {lightbox && <ImageLightbox url={lightbox} onClose={() => setLightbox('')} />}
    </div>
  );
}
