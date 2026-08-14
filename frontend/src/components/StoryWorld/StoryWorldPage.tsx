/**
 * 🌍 Story / World Builder (v1.277.0) — the narrative layer above characters.
 *
 * One WORLD holds the setting sheet, any number of STORIES, a shared CAST and
 * TEXTS (lyrics / narrations / scripts). Every field is LLM-enhanceable — one
 * field, one section, or the whole thing from a seed idea (Big Bang). The cast
 * board holds PAPER characters until you submit them to the ⚡ Autogen serial
 * queue at whatever level you choose (details → base → views → clothing →
 * sheet → dataset → LoRA per item).
 *
 * Server-driven vocab: field lists come from GET /api/storyworld/meta, so a
 * new backend field shows up here with zero frontend edits.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import AutogenBoard from '@/components/CharacterStudio/AutogenBoard';
import useLightbox from '@/components/shared/useLightbox';

const B = '/api/storyworld';
const LLM_KEY = 'rbmn_world_llm';           // persisted per-browser default pick

async function j<T>(r: Response): Promise<T> {
  if (!r.ok) {
    let d = '';
    try { d = (await r.json()).detail || ''; } catch { d = await r.text().catch(() => ''); }
    throw new Error(d || `${r.status}`);
  }
  return r.json();
}
const get = <T,>(p: string) => fetch(B + p).then(r => j<T>(r));
const post = <T,>(p: string, body?: unknown) =>
  fetch(B + p, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  }).then(r => j<T>(r));

// ── types (mirror backend/api/storyworld.py) ─────────────────────────────────
type FieldMetaT = { key: string; label: string; hint: string };
type MetaT = {
  world_fields: FieldMetaT[]; story_fields: FieldMetaT[]; story_types: string[];
  cast_field_keys: string[]; cast_lore_fields: FieldMetaT[];
  text_kinds: string[]; importance: string[]; levels: string[];
  style_presets?: { key: string; label: string; prompt: string }[];
  sample_models?: string[];
};
type StyleT = {
  preset?: string; custom_text?: string; ref_id?: string; ref_description?: string;
};
type SampleT = { id: string; url: string; prompt: string; model: string; worker?: string };
type StyleJobT = {
  status?: string; total?: number; done?: number; error?: string | null;
  elapsed_s?: number; workers?: string[]; log?: { t: number; detail: string }[];
};
type LlmOptT = { provider: string; configured: boolean; models: string[]; default_model: string };
type LlmsT = { default_provider: string; options: LlmOptT[] };
type LlmPickT = { provider: string; model: string };
type WorldLightT = {
  id: string; name: string; logline: string; stories: number; cast: number;
  texts: number; project_ids: string[]; updated_at: string;
};
type StoryT = { id: string; title: string; story_type: string; fields: Record<string, string> };
type OutfitT = { name: string; description: string };
type MemberT = {
  id: string; name: string; role: string; importance: string;
  fields: Record<string, string>; lore: Record<string, string>;
  outfits: OutfitT[]; story_ids: string[]; status: string;
  char_slug: string; autogen_job_id: string; last_error?: string;
  updated_at?: string;
};
type TextT = { id: string; kind: string; title: string; body: string; story_id: string };
type WorldT = {
  id: string; name: string; world: Record<string, string>; stories: StoryT[];
  cast: MemberT[]; texts: TextT[]; project_ids: string[]; llm: LlmPickT;
  style?: StyleT;
};
type ProjT = { id: string; name: string; mode: string };
type CastStatusT = {
  cast: Record<string, { status: string; char_slug: string; last_error?: string }>;
  jobs: Record<string, { stage: string; detail: string; error?: string; elapsed_s?: number }>;
};
type EstimateRowT = { cast_id: string; name: string; renders: number; seconds: number; human: string };

// ── tiny shared UI bits ──────────────────────────────────────────────────────
const inputCls =
  'w-full bg-gray-900 border border-gray-700 rounded px-2 py-1.5 text-sm text-gray-100 ' +
  'focus:border-amber-600 focus:outline-none placeholder-gray-600';
const btnCls =
  'px-3 py-1.5 rounded text-sm font-medium bg-gray-800 hover:bg-gray-700 text-gray-100 ' +
  'disabled:opacity-40 disabled:cursor-not-allowed transition-colors';
const btnAmber =
  'px-3 py-1.5 rounded text-sm font-medium bg-amber-700 hover:bg-amber-600 text-white ' +
  'disabled:opacity-40 disabled:cursor-not-allowed transition-colors';

function Msg({ m }: { m: string }) {
  if (!m) return null;
  const bad = m.startsWith('⚠');
  return <span className={`text-xs ${bad ? 'text-amber-400' : 'text-gray-400'}`}>{m}</span>;
}

/** One enhanceable textarea: saves on blur, ✨ improves the single field.
 *  ⚠ onEnhance receives the LIVE textarea value — clicking ✨ blurs the field,
 *  and the blur-save may not have landed server-side when the enhance call
 *  reads the world, so the backend takes `current` from the client. */
function Field({ meta, value, busy, onSave, onEnhance, rows = 2 }: {
  meta: FieldMetaT; value: string; busy: boolean; rows?: number;
  onSave: (v: string) => void; onEnhance: (current: string) => void;
}) {
  const [v, setV] = useState(value);
  const [dirty, setDirty] = useState(false);
  // adopt external updates (LLM fill, reload) only when the user isn't mid-edit
  useEffect(() => { if (!dirty) setV(value); }, [value, dirty]);
  return (
    <div>
      <div className="flex items-center justify-between mb-0.5">
        <label className="text-xs font-semibold text-gray-300">{meta.label}</label>
        <button className="text-xs text-amber-400 hover:text-amber-300 disabled:opacity-40"
          title={`LLM: improve just "${meta.label}"`} disabled={busy}
          onClick={() => { setDirty(false); onEnhance(v); }}>{busy ? '⏳' : '✨'}</button>
      </div>
      <textarea className={inputCls} rows={rows} value={v} placeholder={meta.hint}
        onChange={e => { setV(e.target.value); setDirty(true); }}
        onBlur={() => { if (v !== value) onSave(v); setDirty(false); }} />
    </div>
  );
}

/** The per-task brain picker. '' provider = whatever Settings resolves. */
function LlmPicker({ llms, pick, onPick }: {
  llms: LlmsT | null; pick: LlmPickT; onPick: (p: LlmPickT) => void;
}) {
  if (!llms) return null;
  const opt = llms.options.find(o => o.provider === pick.provider);
  return (
    <span className="inline-flex items-center gap-1">
      <span className="text-xs text-gray-500">🧠</span>
      <select className="bg-gray-900 border border-gray-700 rounded px-1 py-1 text-xs text-gray-200"
        value={pick.provider}
        onChange={e => onPick({ provider: e.target.value, model: '' })}>
        <option value="">app default ({llms.default_provider || 'auto'})</option>
        {llms.options.filter(o => o.configured).map(o => (
          <option key={o.provider} value={o.provider}>{o.provider}</option>
        ))}
      </select>
      {pick.provider && (opt?.models.length ? (
        <select className="bg-gray-900 border border-gray-700 rounded px-1 py-1 text-xs text-gray-200"
          value={pick.model} onChange={e => onPick({ ...pick, model: e.target.value })}>
          <option value="">{opt.default_model || 'default'}</option>
          {opt.models.map(m => <option key={m} value={m}>{m}</option>)}
        </select>
      ) : (
        <input className="bg-gray-900 border border-gray-700 rounded px-1 py-1 text-xs text-gray-200 w-36"
          placeholder={opt?.default_model || 'model (default)'} value={pick.model}
          onChange={e => onPick({ ...pick, model: e.target.value })} />
      ))}
    </span>
  );
}

// ═════════════════════════════════════════════════════════════════════════════
export default function StoryWorldPage() {
  const navigate = useNavigate();
  const [meta, setMeta] = useState<MetaT | null>(null);
  const [llms, setLlms] = useState<LlmsT | null>(null);
  const [worlds, setWorlds] = useState<WorldLightT[]>([]);
  const [wid, setWid] = useState('');
  const [w, setW] = useState<WorldT | null>(null);
  const [tab, setTab] = useState<'world' | 'stories' | 'cast' | 'texts' | 'projects'>('world');
  const [msg, setMsg] = useState('');
  const [busyKeys, setBusyKeys] = useState<Record<string, boolean>>({});
  const [newName, setNewName] = useState('');
  const [showBigBang, setShowBigBang] = useState(false);
  const [pick, setPick] = useState<LlmPickT>(() => {
    try { return JSON.parse(localStorage.getItem(LLM_KEY) || '') as LlmPickT; }
    catch { return { provider: '', model: '' }; }
  });
  const setPickP = (p: LlmPickT) => {
    setPick(p);
    try { localStorage.setItem(LLM_KEY, JSON.stringify(p)); } catch { /* ok */ }
  };
  const llmBody = pick.provider ? { llm: pick } : {};

  const note = (m: string) => { setMsg(m); };
  const busy = (k: string, on: boolean) =>
    setBusyKeys(prev => ({ ...prev, [k]: on }));

  const loadWorlds = useCallback(async () => {
    try { setWorlds((await get<{ worlds: WorldLightT[] }>('/worlds')).worlds); }
    catch (e) { note(`⚠ ${e}`); }
  }, []);
  const loadWorld = useCallback(async (id: string) => {
    if (!id) { setW(null); return; }
    try { setW(await get<WorldT>(`/worlds/${id}`)); }
    catch (e) { note(`⚠ ${e}`); }
  }, []);

  useEffect(() => {
    get<MetaT>('/meta').then(setMeta).catch(e => note(`⚠ ${e}`));
    get<LlmsT>('/llms').then(setLlms).catch(() => setLlms(null));
    loadWorlds();
  }, [loadWorlds]);
  useEffect(() => { loadWorld(wid); }, [wid, loadWorld]);

  const createWorld = async () => {
    const name = newName.trim();
    if (!name) return;
    try {
      const nw = await post<WorldT>('/worlds', { name });
      setNewName('');
      await loadWorlds();
      setWid(nw.id);
      note(`created "${name}"`);
    } catch (e) { note(`⚠ ${e}`); }
  };

  return (
    <div className="min-h-screen bg-gray-950 text-gray-100 flex flex-col">
      {/* header */}
      <div className="flex items-center gap-3 px-5 py-3 border-b border-gray-800 flex-wrap">
        <button className="text-gray-400 hover:text-gray-200 text-sm" onClick={() => navigate('/')}>← Home</button>
        <h1 className="text-xl font-bold">🌍 Story / World Builder</h1>
        <span className="text-xs text-gray-500 hidden md:inline">
          worlds hold stories, a shared cast, and your lyrics & narrations
        </span>
        <div className="ml-auto flex items-center gap-2">
          <LlmPicker llms={llms} pick={pick} onPick={setPickP} />
          <Msg m={msg} />
        </div>
      </div>

      <div className="flex flex-1 min-h-0">
        {/* worlds sidebar */}
        <div className="w-60 border-r border-gray-800 p-3 flex flex-col gap-2 overflow-y-auto shrink-0">
          <div className="flex gap-1">
            <input className={inputCls} placeholder="new world name…" value={newName}
              onChange={e => setNewName(e.target.value)}
              onKeyDown={e => { if (e.key === 'Enter') createWorld(); }} />
            <button className={btnAmber} onClick={createWorld} disabled={!newName.trim()}>＋</button>
          </div>
          {worlds.map(x => (
            <button key={x.id}
              className={`text-left rounded px-2 py-2 border ${x.id === wid
                ? 'border-amber-600 bg-gray-900' : 'border-gray-800 hover:border-gray-600'}`}
              onClick={() => setWid(x.id)}>
              <div className="text-sm font-semibold truncate">{x.name}</div>
              <div className="text-xs text-gray-500 truncate">{x.logline || '—'}</div>
              <div className="text-[11px] text-gray-600">
                {x.stories} stories · {x.cast} cast · {x.texts} texts
              </div>
            </button>
          ))}
          {!worlds.length && (
            <div className="text-xs text-gray-600 mt-2">
              No worlds yet. Name one above — or create it, then press ⚡ Big Bang and
              hand it a one-line idea.
            </div>
          )}
        </div>

        {/* main */}
        <div className="flex-1 min-w-0 overflow-y-auto">
          {!w ? (
            <div className="p-10 text-gray-500">Select or create a world on the left.</div>
          ) : (
            <div className="p-4">
              {/* world header */}
              <div className="flex items-center gap-2 flex-wrap mb-3">
                <input
                  className="bg-transparent border-b border-gray-700 focus:border-amber-600 focus:outline-none text-lg font-bold px-1"
                  defaultValue={w.name} key={w.id}
                  onBlur={async e => {
                    const v = e.target.value.trim();
                    if (v && v !== w.name) {
                      try { await post(`/worlds/${w.id}/rename`, { name: v }); loadWorlds(); }
                      catch (err) { note(`⚠ ${err}`); }
                    }
                  }} />
                <button className={btnAmber} onClick={() => setShowBigBang(true)}
                  title="one idea in → world sheet + stories + cast out">⚡ Big Bang</button>
                <button className={`${btnCls} ml-auto text-red-300`}
                  onClick={async () => {
                    if (!window.confirm(`Delete world "${w.name}"? Generated characters stay in the library.`)) return;
                    await post(`/worlds/${w.id}/delete`);
                    setWid(''); setW(null); loadWorlds();
                  }}>🗑 Delete world</button>
              </div>

              {/* tabs */}
              <div className="flex gap-1 mb-4 flex-wrap">
                {([['world', '🌍 World'], ['stories', `📖 Stories (${w.stories.length})`],
                   ['cast', `🎭 Cast (${w.cast.length})`], ['texts', `📝 Texts (${w.texts.length})`],
                   ['projects', `🔗 Projects (${w.project_ids.length})`]] as const).map(([k, label]) => (
                  <button key={k}
                    className={`px-3 py-1.5 rounded-t text-sm ${tab === k
                      ? 'bg-gray-900 border border-b-0 border-gray-700 text-amber-300'
                      : 'text-gray-400 hover:text-gray-200'}`}
                    onClick={() => setTab(k)}>{label}</button>
                ))}
              </div>

              {meta && tab === 'world' && (
                <WorldTab w={w} meta={meta} llmBody={llmBody} busyKeys={busyKeys}
                  busyFn={busy} note={note} reload={() => loadWorld(w.id)} />
              )}
              {meta && tab === 'stories' && (
                <StoriesTab w={w} meta={meta} llmBody={llmBody} busyKeys={busyKeys}
                  busyFn={busy} note={note} reload={() => loadWorld(w.id)} />
              )}
              {meta && tab === 'cast' && (
                <CastTab w={w} meta={meta} llmBody={llmBody} busyKeys={busyKeys}
                  busyFn={busy} note={note} reload={() => loadWorld(w.id)} />
              )}
              {meta && tab === 'texts' && (
                <TextsTab w={w} meta={meta} note={note} reload={() => loadWorld(w.id)} />
              )}
              {tab === 'projects' && (
                <ProjectsTab w={w} note={note} reload={() => loadWorld(w.id)} />
              )}
            </div>
          )}
        </div>
      </div>

      {showBigBang && w && (
        <BigBangModal wid={w.id} llmBody={llmBody} onClose={() => setShowBigBang(false)}
          onDone={(steps) => {
            setShowBigBang(false);
            note(steps.join(' · '));
            loadWorld(w.id); loadWorlds();
          }} />
      )}
    </div>
  );
}

// ── 🎨 visual style card ─────────────────────────────────────────────────────
function StyleCard({ w, meta, llmBody, note, reload }: {
  w: WorldT; meta: MetaT; llmBody: object; note: (m: string) => void; reload: () => void;
}) {
  const s: StyleT = w.style || {};
  const presets = meta.style_presets || [];
  const models = meta.sample_models || ['krea2'];
  const [custom, setCustom] = useState(s.custom_text || '');
  const [refDesc, setRefDesc] = useState(s.ref_description || '');
  const [model, setModel] = useState('krea2');
  const [count, setCount] = useState(4);
  const [dir, setDir] = useState('');
  const [scanning, setScanning] = useState(false);
  const [samples, setSamples] = useState<SampleT[]>([]);
  const [job, setJob] = useState<StyleJobT>({});
  const [showLog, setShowLog] = useState(false);
  const fileRef = useRef<HTMLInputElement | null>(null);
  useEffect(() => { setCustom(s.custom_text || ''); setRefDesc(s.ref_description || ''); }, [w.id]);  // eslint-disable-line react-hooks/exhaustive-deps

  const loadSamples = useCallback(async () => {
    try { setSamples((await get<{ samples: SampleT[] }>(`/worlds/${w.id}/style/samples`)).samples); }
    catch { /* fine */ }
  }, [w.id]);
  useEffect(() => { void loadSamples(); }, [loadSamples]);

  // 📊 the standing rule: a live, expandable, verbose status for any render.
  // ⚠ Fetched ON MOUNT too — the first version only polled while a LOCAL flag
  // was set, so navigating away and back hid a failed run entirely ("nothing
  // is showing up" with no error anywhere, 2026-08-14). The server's job state
  // is the truth; adopt it whenever the card appears.
  const running = job.status === 'running' || job.status === 'starting';
  useEffect(() => {
    let stop = false;
    const tick = async () => {
      try {
        const r = await get<{ job: StyleJobT }>(`/worlds/${w.id}/style/job`);
        if (stop) return;
        setJob(r.job || {});
      } catch { /* transient */ }
    };
    void tick();                       // on mount: recover a running/failed job
    if (!running) return () => { stop = true; };
    const iv = window.setInterval(async () => {
      await tick();
    }, 2500);
    return () => { stop = true; window.clearInterval(iv); };
  }, [running, w.id]);
  // reload the gallery when a run reaches a terminal state
  useEffect(() => {
    if (job.status === 'done' || job.status === 'error') void loadSamples();
  }, [job.status, loadSamples]);

  const saveStyle = async (preset?: string) => {
    try {
      const r = await post<{ style_text: string }>(`/worlds/${w.id}/style`,
        { preset: preset ?? (s.preset || ''), custom_text: custom });
      note(r.style_text ? `style: ${r.style_text.slice(0, 90)}…` : 'style cleared');
      reload();
    } catch (e) { note(`⚠ ${e}`); }
  };
  const uploadRef = async (f: globalThis.File) => {
    setScanning(true);
    try {
      const fd = new FormData();
      fd.append('file', f);
      const r = await fetch(`${B}/worlds/${w.id}/style/ref`, { method: 'POST', body: fd });
      const jj = await r.json();
      if (!r.ok) throw new Error(jj.detail || `${r.status}`);
      setRefDesc(jj.description || '');
      note(jj.scan_error ? `⚠ ${jj.scan_error}` : 'style reference scanned');
      reload();
    } catch (e) { note(`⚠ ${e}`); }
    setScanning(false);
  };
  const gen = async () => {
    try {
      setJob({ status: 'starting', total: count, done: 0 });
      await post(`/worlds/${w.id}/style/samples`,
        { count, model, direction: dir, ...llmBody });
      note('🎨 rendering style samples — watch the status below');
    } catch (e) { setJob({}); note(`⚠ ${e}`); }
  };

  return (
    <div className="border border-gray-800 rounded p-3 mb-4 bg-gray-900/40">
      <input ref={fileRef} type="file" accept="image/*" className="hidden"
             onChange={e => { const f = e.target.files?.[0]; e.target.value = ''; if (f) void uploadRef(f); }} />
      <div className="flex items-center gap-2 flex-wrap">
        <span className="text-xs font-semibold text-amber-300">🎨 Visual style</span>
        <select className={`${inputCls} w-auto`} value={s.preset || ''}
                onChange={e => void saveStyle(e.target.value)}>
          <option value="">— none —</option>
          {presets.map(p => <option key={p.key} value={p.key}>{p.label}</option>)}
        </select>
        <input className={`${inputCls} flex-1 min-w-[180px]`} value={custom}
               placeholder="custom style text (used alone or on top of the preset)…"
               onChange={e => setCustom(e.target.value)}
               onBlur={() => void saveStyle()} />
        <button className={btnCls} disabled={scanning}
                title="upload 1 image of YOUR style — the vision model describes the style (not the content) and future generations follow it"
                onClick={() => fileRef.current?.click()}>
          {scanning ? '⏳ scanning…' : '🖼 Style ref'}
        </button>
        {s.ref_id && (
          <img src={`${B}/style/refs/${s.ref_id}/image`} alt="style ref"
               className="h-10 rounded border border-gray-700" title="the style reference" />
        )}
      </div>
      {refDesc && (
        <div className="text-[11px] text-gray-400 mt-1">
          <span className="text-gray-500">scanned style:</span> {refDesc}
        </div>
      )}
      <div className="flex items-center gap-2 mt-2 flex-wrap">
        <span className="text-[11px] text-gray-500">samples:</span>
        <input type="number" min={1} max={8} className={`${inputCls} w-16`} value={count}
               onChange={e => setCount(Math.max(1, Math.min(8, Number(e.target.value) || 4)))} />
        <select className={`${inputCls} w-auto`} value={model}
                onChange={e => setModel(e.target.value)}
                title={s.ref_id ? 'a style ref is set — samples render via klein citing it; this model is used only without a ref' : 'the t2i model for the samples'}>
          {models.map(m => <option key={m} value={m}>{m}</option>)}
        </select>
        <input className={`${inputCls} max-w-xs`} value={dir}
               placeholder="optional: what the samples should show…"
               onChange={e => setDir(e.target.value)} />
        <button className={btnAmber} disabled={running} onClick={() => void gen()}>
          {running ? '⏳ rendering…' : '🎨 Generate style samples'}
        </button>
      </div>
      {(running || job.status === 'error') && (
        <div className="mt-2 text-xs">
          <span className={job.status === 'error' ? 'text-red-400' : 'text-blue-300'}>
            {job.status === 'error' ? `❌ ${job.error}` :
              `⏳ ${job.done || 0}/${job.total || count} rendered · ${Math.round(job.elapsed_s || 0)}s` +
              (job.workers?.length ? ` · on ${job.workers.join(', ')}` : '')}
          </span>
          <button className="ml-2 text-gray-500 hover:text-gray-300"
                  onClick={() => setShowLog(v => !v)}>{showLog ? '▾ log' : '▸ log'}</button>
          {showLog && (
            <div className="mt-1 bg-gray-950 border border-gray-800 rounded p-2 max-h-32 overflow-y-auto font-mono text-[10px] text-gray-400">
              {(job.log || []).slice().reverse().map((l, i) => (
                <div key={i}>{l.t}s — {l.detail}</div>
              ))}
            </div>
          )}
        </div>
      )}
      {!!samples.length && (
        <div className="flex flex-wrap gap-2 mt-3">
          {samples.map(sm => (
            <div key={sm.id} className="w-40">
              <a href={`${B}${sm.url.replace('/api/storyworld', '')}?download=1`} target="_blank" rel="noreferrer">
                <img src={sm.url} alt={sm.prompt} title={`${sm.prompt}\n(${sm.model}${sm.worker ? ` on ${sm.worker}` : ''})`}
                     className="w-full rounded border border-gray-800" />
              </a>
              <div className="flex items-center gap-1">
                <div className="text-[10px] text-gray-600 truncate flex-1" title={sm.prompt}>{sm.prompt}</div>
                <button className="text-[10px] text-red-400" title="delete"
                        onClick={async () => { await post(`/worlds/${w.id}/style/samples/${sm.id}/delete`); void loadSamples(); }}>🗑</button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ── 🌍 World tab ─────────────────────────────────────────────────────────────
function WorldTab({ w, meta, llmBody, busyKeys, busyFn, note, reload }: {
  w: WorldT; meta: MetaT; llmBody: object; busyKeys: Record<string, boolean>;
  busyFn: (k: string, on: boolean) => void; note: (m: string) => void; reload: () => void;
}) {
  const [dir, setDir] = useState('');
  const saveField = async (key: string, v: string) => {
    try { await post(`/worlds/${w.id}/world`, { fields: { [key]: v } }); reload(); }
    catch (e) { note(`⚠ ${e}`); }
  };
  const enhanceOne = async (key: string, current: string) => {
    busyFn(`wf.${key}`, true);
    try {
      await post(`/worlds/${w.id}/enhance/field`,
        { section: 'world', field: key, direction: dir, current, ...llmBody });
      reload();
    } catch (e) { note(`⚠ ${e}`); } finally { busyFn(`wf.${key}`, false); }
  };
  const enhanceAll = async (mode: 'fill' | 'overwrite') => {
    if (mode === 'overwrite' &&
        !window.confirm('Overwrite EVERY world field with fresh LLM text?')) return;
    busyFn('world.all', true);
    note('🧠 thinking about the world…');
    try {
      const r = await post<{ changed: string[]; note?: string }>(
        `/worlds/${w.id}/enhance/world`, { mode, direction: dir, ...llmBody });
      note(r.note || `filled: ${r.changed.join(', ') || 'nothing new'}`);
      reload();
    } catch (e) { note(`⚠ ${e}`); } finally { busyFn('world.all', false); }
  };
  const long = new Set(['history', 'notes', 'rules', 'factions', 'locations']);
  return (
    <div>
      <StyleCard w={w} meta={meta} llmBody={llmBody} note={note} reload={reload} />
      <div className="flex items-center gap-2 mb-3 flex-wrap">
        <input className={`${inputCls} max-w-md`} value={dir}
          placeholder="optional direction: 'lean into 80s retro-future'…"
          onChange={e => setDir(e.target.value)} />
        <button className={btnAmber} disabled={busyKeys['world.all']}
          onClick={() => enhanceAll('fill')}>
          {busyKeys['world.all'] ? '⏳ thinking…' : '✨ Fill empty'}</button>
        <button className={btnCls} disabled={busyKeys['world.all']}
          onClick={() => enhanceAll('overwrite')}>✨ Overwrite all</button>
        <span className="text-xs text-gray-600">fields save when you click away · ✨ improves one field</span>
      </div>
      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3">
        {meta.world_fields.map(f => (
          <Field key={f.key} meta={f} value={w.world[f.key] || ''}
            rows={long.has(f.key) ? 4 : 2}
            busy={!!busyKeys[`wf.${f.key}`]}
            onSave={v => saveField(f.key, v)}
            onEnhance={cur => enhanceOne(f.key, cur)} />
        ))}
      </div>
    </div>
  );
}

// ── 📖 Stories tab ───────────────────────────────────────────────────────────
function StoriesTab({ w, meta, llmBody, busyKeys, busyFn, note, reload }: {
  w: WorldT; meta: MetaT; llmBody: object; busyKeys: Record<string, boolean>;
  busyFn: (k: string, on: boolean) => void; note: (m: string) => void; reload: () => void;
}) {
  const [sid, setSid] = useState(w.stories[0]?.id || '');
  const [title, setTitle] = useState('');
  const [dir, setDir] = useState('');
  useEffect(() => {
    if (!w.stories.find(s => s.id === sid)) setSid(w.stories[0]?.id || '');
  }, [w.stories, sid]);
  const st = w.stories.find(s => s.id === sid);

  const addStory = async () => {
    const t = title.trim();
    if (!t) return;
    try {
      const ns = await post<StoryT>(`/worlds/${w.id}/stories`, { title: t });
      setTitle(''); reload(); setSid(ns.id);
    } catch (e) { note(`⚠ ${e}`); }
  };
  const save = async (patch: object) => {
    if (!st) return;
    try { await post(`/worlds/${w.id}/stories/${st.id}`, patch); reload(); }
    catch (e) { note(`⚠ ${e}`); }
  };
  const enhanceAll = async (mode: 'fill' | 'overwrite') => {
    if (!st) return;
    if (mode === 'overwrite' &&
        !window.confirm(`Overwrite every field of "${st.title}"?`)) return;
    busyFn('story.all', true);
    note('🧠 developing the story…');
    try {
      const r = await post<{ changed: string[]; note?: string }>(
        `/worlds/${w.id}/enhance/story/${st.id}`, { mode, direction: dir, ...llmBody });
      note(r.note || `filled: ${r.changed.join(', ') || 'nothing new'}`);
      reload();
    } catch (e) { note(`⚠ ${e}`); } finally { busyFn('story.all', false); }
  };
  const enhanceOne = async (key: string, current: string) => {
    if (!st) return;
    busyFn(`sf.${key}`, true);
    try {
      await post(`/worlds/${w.id}/enhance/field`,
        { section: `story:${st.id}`, field: key, direction: dir, current, ...llmBody });
      reload();
    } catch (e) { note(`⚠ ${e}`); } finally { busyFn(`sf.${key}`, false); }
  };
  const long = new Set(['synopsis', 'beats']);
  return (
    <div className="flex gap-4">
      <div className="w-56 shrink-0 flex flex-col gap-2">
        <div className="flex gap-1">
          <input className={inputCls} placeholder="new story title…" value={title}
            onChange={e => setTitle(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter') addStory(); }} />
          <button className={btnAmber} onClick={addStory} disabled={!title.trim()}>＋</button>
        </div>
        {w.stories.map(s => (
          <button key={s.id}
            className={`text-left rounded px-2 py-1.5 border text-sm ${s.id === sid
              ? 'border-amber-600 bg-gray-900' : 'border-gray-800 hover:border-gray-600'}`}
            onClick={() => setSid(s.id)}>
            <div className="font-semibold truncate">{s.title}</div>
            <div className="text-[11px] text-gray-500">{s.story_type.replace('_', ' ')}</div>
          </button>
        ))}
        {!w.stories.length &&
          <div className="text-xs text-gray-600">No stories yet — a world can hold many.</div>}
      </div>
      {st ? (
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 mb-3 flex-wrap">
            <select className={`${inputCls} w-auto`} value={st.story_type}
              onChange={e => save({ story_type: e.target.value })}>
              {meta.story_types.map(t => <option key={t} value={t}>{t.replace('_', ' ')}</option>)}
            </select>
            <input className={`${inputCls} max-w-xs`} value={dir}
              placeholder="optional direction for the LLM…"
              onChange={e => setDir(e.target.value)} />
            <button className={btnAmber} disabled={busyKeys['story.all']}
              onClick={() => enhanceAll('fill')}>
              {busyKeys['story.all'] ? '⏳ thinking…' : '✨ Fill empty'}</button>
            <button className={btnCls} disabled={busyKeys['story.all']}
              onClick={() => enhanceAll('overwrite')}>✨ Overwrite all</button>
            <button className={`${btnCls} ml-auto text-red-300`}
              onClick={async () => {
                if (!window.confirm(`Delete story "${st.title}"? Its texts stay, unlinked.`)) return;
                await post(`/worlds/${w.id}/stories/${st.id}/delete`); reload();
              }}>🗑</button>
          </div>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            {meta.story_fields.map(f => (
              <Field key={f.key} meta={f} value={st.fields[f.key] || ''}
                rows={long.has(f.key) ? 6 : 2}
                busy={!!busyKeys[`sf.${f.key}`]}
                onSave={v => save({ fields: { [f.key]: v } })}
                onEnhance={cur => enhanceOne(f.key, cur)} />
            ))}
          </div>
        </div>
      ) : <div className="text-gray-500 p-6">Add a story on the left.</div>}
    </div>
  );
}

// ── 🎭 Cast tab ──────────────────────────────────────────────────────────────
const LEVEL_LABEL: Record<string, string> = {
  details: '📇 Details only (free — no renders)',
  base: '🧬 + Base image',
  views: '🧭 + Base views (~2 min)',
  clothing: '👗 + Clothing (~15 min)',
  sheet: '🪪 + Character sheet (~30 min)',
  dataset: '🎓 + LoRA dataset (~32 min)',
  lora: '🚀 + Trained LoRA (~7 h each!)',
};

function CastTab({ w, meta, llmBody, busyKeys, busyFn, note, reload }: {
  w: WorldT; meta: MetaT; llmBody: object; busyKeys: Record<string, boolean>;
  busyFn: (k: string, on: boolean) => void; note: (m: string) => void; reload: () => void;
}) {
  const navigate = useNavigate();
  const lb = useLightbox();
  const [sel, setSel] = useState<Record<string, boolean>>({});
  const [level, setLevel] = useState('views');
  const [editing, setEditing] = useState<MemberT | null>(null);
  const [showGen, setShowGen] = useState(false);
  const [est, setEst] = useState<{ rows: EstimateRowT[]; total: number } | null>(null);
  const [status, setStatus] = useState<CastStatusT | null>(null);
  const anySubmitted = w.cast.some(c => c.status === 'submitted');
  const reloadRef = useRef(reload);
  reloadRef.current = reload;

  // poll the join while anything is in the queue. Reload the world only when a
  // member's STATUS actually changed — "any member ever generated" kept every
  // later batch re-fetching the whole world every 5s for hours, and an error
  // transition never triggered a reload at all (reviewer finding #5).
  const castStatuses = w.cast.map(c => `${c.id}:${c.status}`).join(',');
  useEffect(() => {
    if (!anySubmitted) return;
    let stop = false;
    const tick = async () => {
      try {
        const s = await get<CastStatusT>(`/worlds/${w.id}/cast/status`);
        if (stop) return;
        setStatus(s);
        const changed = w.cast.some(c =>
          s.cast[c.id] && s.cast[c.id].status !== c.status);
        if (changed) reloadRef.current();
      } catch { /* transient */ }
    };
    tick();
    const iv = window.setInterval(tick, 5000);
    return () => { stop = true; window.clearInterval(iv); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [w.id, anySubmitted, castStatuses]);

  const selected = w.cast.filter(c => sel[c.id]);
  const submitTargets = selected.length ? selected : w.cast.filter(c => c.status === 'paper');

  const estimate = async () => {
    try {
      const r = await post<{ estimate: EstimateRowT[]; total_seconds: number }>(
        `/worlds/${w.id}/cast/submit`,
        { cast_ids: submitTargets.map(c => c.id), level, estimate_only: true });
      setEst({ rows: r.estimate, total: r.total_seconds });
    } catch (e) { note(`⚠ ${e}`); }
  };
  const submit = async () => {
    const n = submitTargets.length;
    if (!n) { note('⚠ nobody on paper to submit'); return; }
    const human = est ? ` (~${Math.round(est.total / 60)} min total)` : '';
    if (!window.confirm(`Submit ${n} character(s) at "${level}"${human}? They run one after another on the autogen queue.`)) return;
    busyFn('cast.submit', true);
    try {
      const r = await post<{ queue: number }>(`/worlds/${w.id}/cast/submit`,
        { cast_ids: submitTargets.map(c => c.id), level });
      note(`queued ${n} — queue depth ${r.queue}. Watch it here or on the Studio board.`);
      setSel({}); setEst(null); reload();
    } catch (e) { note(`⚠ ${e}`); } finally { busyFn('cast.submit', false); }
  };
  const enhanceMember = async (m: MemberT) => {
    busyFn(`cm.${m.id}`, true);
    try {
      const r = await post<{ changed: string[]; note?: string }>(
        `/worlds/${w.id}/enhance/cast/${m.id}`, { mode: 'fill', ...llmBody });
      note(r.note || `${m.name}: filled ${r.changed.length} fields`);
      reload();
    } catch (e) { note(`⚠ ${e}`); } finally { busyFn(`cm.${m.id}`, false); }
  };

  const chip = (c: MemberT) => {
    const js = status?.jobs[c.id];
    if (c.status === 'generated')
      return <button className="text-[11px] px-1.5 py-0.5 rounded bg-green-900 text-green-300"
        title="open in the studio"
        onClick={() => {
          try { localStorage.setItem('rbmn_focus_char', c.char_slug); } catch { /* ok */ }
          navigate('/studio/vnccs-klein?tab=klein3');
        }}>✅ {c.char_slug}</button>;
    if (c.status === 'submitted')
      return <span className="text-[11px] px-1.5 py-0.5 rounded bg-blue-900 text-blue-300">
        ⏳ {js ? `${js.stage}${js.detail ? ` — ${js.detail}` : ''}` : 'queued'}</span>;
    return <span className="text-[11px] px-1.5 py-0.5 rounded bg-gray-800 text-gray-400">
      📄 paper{c.last_error ? ` · last try failed` : ''}</span>;
  };

  return (
    <div>
      <div className="flex items-center gap-2 mb-3 flex-wrap">
        <button className={btnCls} onClick={() => setEditing({
          id: '', name: '', role: '', importance: 'support', fields: {}, lore: {},
          outfits: [], story_ids: [], status: 'paper', char_slug: '', autogen_job_id: '',
        })}>＋ Add by hand</button>
        <button className={btnAmber} onClick={() => setShowGen(true)}>🎭 Generate cast (LLM)</button>
        <span className="mx-2 text-gray-700">|</span>
        <select className={`${inputCls} w-auto`} value={level} onChange={e => { setLevel(e.target.value); setEst(null); }}>
          {meta.levels.map(l => <option key={l} value={l}>{LEVEL_LABEL[l] || l}</option>)}
        </select>
        <button className={btnCls} onClick={estimate} disabled={!submitTargets.length}>⏱ Estimate</button>
        <button className={btnAmber} onClick={submit}
          disabled={!!busyKeys['cast.submit'] || !submitTargets.length}>
          🚀 Submit {selected.length ? `${selected.length} selected` : `all paper (${submitTargets.length})`}
        </button>
        {est && <span className="text-xs text-gray-400">
          ~{Math.round(est.total / 60)} min total · {est.rows.reduce((a, r) => a + r.renders, 0)} renders</span>}
      </div>
      {level === 'lora' && (
        <div className="text-xs text-amber-400 mb-2">
          ⚠ LoRA training runs PER character inside its chain — roughly 7 hours each. {submitTargets.length} selected ⇒ ~{submitTargets.length * 7} h. Untick people you're still iterating on.
        </div>
      )}
      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3">
        {w.cast.map(c => {
          // thumbnail: the generated character's active base (mtime-free URL —
          // updated_at busts the cache when the queue finishes a character)
          const thumb = c.char_slug
            ? `/api/klein3/characters/${c.char_slug}/base/active/image?v=${encodeURIComponent(c.updated_at || '')}`
            : '';
          return (
          <div key={c.id} className={`rounded border p-3 ${sel[c.id]
            ? 'border-amber-600 bg-gray-900' : 'border-gray-800 bg-gray-900/50'}`}>
            <div className="flex items-start gap-2">
              {thumb ? (
                <img src={thumb} alt={c.name}
                  className="w-14 h-20 object-cover rounded border border-gray-700 cursor-zoom-in shrink-0 bg-gray-800"
                  title="click to zoom + pan"
                  onError={e => { (e.target as HTMLImageElement).style.display = 'none'; }}
                  onClick={() => lb.open(thumb, 0, `${c.name}${c.role ? ` — ${c.role}` : ''}`)} />
              ) : (
                <div className="w-14 h-20 rounded border border-gray-800 bg-gray-900 flex items-center justify-center text-2xl shrink-0"
                  title="no image yet — generate the character to get one">📄</div>
              )}
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                  {/* generated members stay selectable — that IS the regenerate path */}
                  <input type="checkbox" checked={!!sel[c.id]} disabled={c.status === 'submitted'}
                    title={c.status === 'generated' ? 'select to REGENERATE this character' : ''}
                    onChange={e => { setSel(p => ({ ...p, [c.id]: e.target.checked })); setEst(null); }} />
                  <span className="font-semibold truncate">{c.name}</span>
                  <span className="text-[11px] text-gray-500">{c.importance}</span>
                  <span className="ml-auto">{chip(c)}</span>
                </div>
                <div className="text-xs text-gray-400 mt-1 line-clamp-2">{c.role || '—'}</div>
              </div>
            </div>
            <div className="text-[11px] text-gray-600 mt-1">
              {Object.keys(c.fields).length}/{meta.cast_field_keys.length} looks
              · {Object.keys(c.lore).length}/{meta.cast_lore_fields.length} lore
              · {c.outfits.length} outfits
            </div>
            <div className="flex gap-1 mt-2">
              <button className={btnCls} onClick={() => setEditing(c)}>✏ Edit</button>
              <button className={btnCls} disabled={!!busyKeys[`cm.${c.id}`]}
                title="LLM fills every empty appearance + lore field"
                onClick={() => enhanceMember(c)}>{busyKeys[`cm.${c.id}`] ? '⏳' : '✨ Fill'}</button>
              {c.status === 'generated' && (
                <button className={btnCls} disabled={!!busyKeys['cast.submit']}
                  title={`re-run this character through the queue at "${level}" — a new version of their base/views/etc; edits to the paper details apply`}
                  onClick={async () => {
                    if (!window.confirm(`Regenerate "${c.name}" at "${level}"? The character keeps its slug — this renders a NEW version of its images.`)) return;
                    busyFn('cast.submit', true);
                    try {
                      const r = await post<{ queue: number }>(`/worlds/${w.id}/cast/submit`,
                        { cast_ids: [c.id], level });
                      note(`↻ ${c.name} queued — queue depth ${r.queue}`);
                      reload();
                    } catch (e) { note(`⚠ ${e}`); } finally { busyFn('cast.submit', false); }
                  }}>↻</button>
              )}
              <button className={`${btnCls} ml-auto text-red-300`} onClick={async () => {
                if (!window.confirm(`Remove "${c.name}" from the cast? A generated character stays in the library.`)) return;
                await post(`/worlds/${w.id}/cast/${c.id}/delete`); reload();
              }}>🗑</button>
            </div>
          </div>
          );
        })}
      </div>
      {!w.cast.length && (
        <div className="text-gray-500 text-sm mt-4">
          No cast yet. 🎭 Generate cast reads the world + a story and proposes who the
          story needs — or add people by hand.
        </div>
      )}
      {/* ⚡ the LIVE board — his standing rule: any generation must have an
          expandable, verbose status (what's rendering, WHERE, for how long),
          and the job files it reads persist as benchmarking data. This is the
          same board as /studio (v1.276.46), so the verbose toggle, per-stage
          durations, workers and full log all apply to submissions from here. */}
      {w.cast.some(c => c.autogen_job_id) && (
        <div className="mt-6">
          <div className="text-xs font-semibold text-amber-300 mb-2">
            ⚡ Generation board <span className="text-gray-500 font-normal">
              — expand a row (▸) for the stage chain, per-stage durations, workers and the
              full log; the verbose toggle persists</span>
          </div>
          <AutogenBoard />
        </div>
      )}
      {editing && (
        <MemberModal w={w} meta={meta} member={editing} note={note}
          onClose={() => setEditing(null)}
          onSaved={() => { setEditing(null); reload(); }} />
      )}
      {showGen && (
        <CastGenModal w={w} llmBody={llmBody} note={note}
          onClose={() => setShowGen(false)}
          onDone={(n) => { setShowGen(false); note(`proposed ${n} characters`); reload(); }} />
      )}
      {lb.node}
    </div>
  );
}

// ── member edit modal ────────────────────────────────────────────────────────
function MemberModal({ w, meta, member, note, onClose, onSaved }: {
  w: WorldT; meta: MetaT; member: MemberT; note: (m: string) => void;
  onClose: () => void; onSaved: () => void;
}) {
  const isNew = !member.id;
  const [m, setM] = useState<MemberT>({ ...member, outfits: [...member.outfits] });
  const setF = (k: string, v: string) => setM(p => ({ ...p, fields: { ...p.fields, [k]: v } }));
  const setL = (k: string, v: string) => setM(p => ({ ...p, lore: { ...p.lore, [k]: v } }));
  const save = async () => {
    if (!m.name.trim()) { note('⚠ the character needs a name'); return; }
    try {
      const body = { name: m.name, role: m.role, importance: m.importance,
        fields: m.fields, lore: m.lore, outfits: m.outfits, story_ids: m.story_ids };
      if (isNew) await post(`/worlds/${w.id}/cast`, body);
      else await post(`/worlds/${w.id}/cast/${m.id}`, body);
      onSaved();
    } catch (e) { note(`⚠ ${e}`); }
  };
  return (
    <div className="fixed inset-0 bg-black/70 z-50 flex items-center justify-center p-4"
      onClick={onClose}>
      <div className="bg-gray-950 border border-gray-700 rounded-lg w-full max-w-3xl max-h-[90vh] overflow-y-auto p-4"
        onClick={e => e.stopPropagation()}>
        <div className="flex items-center gap-2 mb-3">
          <h3 className="font-bold">{isNew ? '＋ New cast member' : `✏ ${member.name}`}</h3>
          <button className={`${btnCls} ml-auto`} onClick={onClose}>✕</button>
        </div>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-2 mb-3">
          <input className={inputCls} placeholder="name" value={m.name}
            onChange={e => setM(p => ({ ...p, name: e.target.value }))} />
          <input className={inputCls} placeholder="role in the story" value={m.role}
            onChange={e => setM(p => ({ ...p, role: e.target.value }))} />
          <select className={inputCls} value={m.importance}
            onChange={e => setM(p => ({ ...p, importance: e.target.value }))}>
            {meta.importance.map(i => <option key={i} value={i}>{i}</option>)}
          </select>
        </div>
        <div className="text-xs font-semibold text-amber-300 mb-1">Appearance (drives the renders)</div>
        <div className="grid grid-cols-2 md:grid-cols-3 gap-2 mb-3">
          {meta.cast_field_keys.map(k => (
            <div key={k}>
              <label className="text-[11px] text-gray-500">{k.replace('_', ' ')}</label>
              <input className={inputCls} value={m.fields[k] || ''}
                onChange={e => setF(k, e.target.value)} />
            </div>
          ))}
        </div>
        <div className="text-xs font-semibold text-amber-300 mb-1">Lore (drives the story)</div>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-2 mb-3">
          {meta.cast_lore_fields.map(f => (
            <div key={f.key}>
              <label className="text-[11px] text-gray-500">{f.label}</label>
              <textarea className={inputCls} rows={2} placeholder={f.hint}
                value={m.lore[f.key] || ''} onChange={e => setL(f.key, e.target.value)} />
            </div>
          ))}
        </div>
        <div className="text-xs font-semibold text-amber-300 mb-1">
          Outfits <span className="text-gray-500 font-normal">(used by the 👗 clothing stage — garments only, no franchise names)</span>
        </div>
        {m.outfits.map((o, i) => (
          <div key={i} className="flex gap-2 mb-1">
            <input className={`${inputCls} w-40`} placeholder="name" value={o.name}
              onChange={e => setM(p => {
                const oo = [...p.outfits]; oo[i] = { ...oo[i], name: e.target.value };
                return { ...p, outfits: oo };
              })} />
            <input className={inputCls} placeholder="a grey hooded windbreaker, black cargo trousers…"
              value={o.description}
              onChange={e => setM(p => {
                const oo = [...p.outfits]; oo[i] = { ...oo[i], description: e.target.value };
                return { ...p, outfits: oo };
              })} />
            <button className={btnCls} onClick={() =>
              setM(p => ({ ...p, outfits: p.outfits.filter((_, x) => x !== i) }))}>✕</button>
          </div>
        ))}
        <button className={btnCls} onClick={() =>
          setM(p => ({ ...p, outfits: [...p.outfits, { name: '', description: '' }] }))}>＋ outfit</button>
        {w.stories.length > 0 && (
          <div className="mt-3">
            <div className="text-xs font-semibold text-amber-300 mb-1">Appears in</div>
            <div className="flex gap-2 flex-wrap">
              {w.stories.map(s => (
                <label key={s.id} className="text-xs text-gray-300 flex items-center gap-1">
                  <input type="checkbox" checked={m.story_ids.includes(s.id)}
                    onChange={e => setM(p => ({
                      ...p,
                      story_ids: e.target.checked
                        ? [...p.story_ids, s.id]
                        : p.story_ids.filter(x => x !== s.id),
                    }))} />
                  {s.title}
                </label>
              ))}
            </div>
          </div>
        )}
        <div className="flex gap-2 mt-4">
          <button className={btnAmber} onClick={save}>💾 Save</button>
          <button className={btnCls} onClick={onClose}>Cancel</button>
        </div>
      </div>
    </div>
  );
}

// ── 🎭 cast generation modal ─────────────────────────────────────────────────
function CastGenModal({ w, llmBody, note, onClose, onDone }: {
  w: WorldT; llmBody: object; note: (m: string) => void;
  onClose: () => void; onDone: (n: number) => void;
}) {
  const [storyId, setStoryId] = useState('');
  const [max, setMax] = useState(8);
  const [dir, setDir] = useState('');
  const [running, setRunning] = useState(false);
  const go = async () => {
    setRunning(true);
    try {
      const r = await post<{ made: MemberT[]; skipped_existing: string[] }>(
        `/worlds/${w.id}/cast/generate`,
        { story_id: storyId, max_count: max, direction: dir, ...llmBody });
      onDone(r.made.length);
    } catch (e) { note(`⚠ ${e}`); setRunning(false); }
  };
  return (
    <div className="fixed inset-0 bg-black/70 z-50 flex items-center justify-center p-4" onClick={onClose}>
      <div className="bg-gray-950 border border-gray-700 rounded-lg w-full max-w-md p-4"
        onClick={e => e.stopPropagation()}>
        <h3 className="font-bold mb-1">🎭 Generate the cast</h3>
        <p className="text-xs text-gray-500 mb-3">
          The LLM reads the world (and a story, if picked) and proposes the characters it
          actually needs — up to your cap, fewer if fewer will do. Nothing renders; you
          review the board first.
        </p>
        <label className="text-xs text-gray-400">For</label>
        <select className={`${inputCls} mb-2`} value={storyId} onChange={e => setStoryId(e.target.value)}>
          <option value="">the world as a whole</option>
          {w.stories.map(s => <option key={s.id} value={s.id}>{s.title}</option>)}
        </select>
        <label className="text-xs text-gray-400">At most</label>
        <input type="number" min={1} max={20} className={`${inputCls} mb-2`} value={max}
          onChange={e => setMax(Math.max(1, Math.min(20, Number(e.target.value) || 8)))} />
        <label className="text-xs text-gray-400">Direction (optional)</label>
        <input className={`${inputCls} mb-3`} value={dir}
          placeholder="'mostly women', 'one comic-relief sidekick'…"
          onChange={e => setDir(e.target.value)} />
        <div className="flex gap-2">
          <button className={btnAmber} disabled={running} onClick={go}>
            {running ? '⏳ casting…' : '🎭 Propose the cast'}</button>
          <button className={btnCls} onClick={onClose} disabled={running}>Cancel</button>
        </div>
      </div>
    </div>
  );
}

// ── ⚡ Big Bang modal ────────────────────────────────────────────────────────
function BigBangModal({ wid, llmBody, onClose, onDone }: {
  wid: string; llmBody: object; onClose: () => void; onDone: (steps: string[]) => void;
}) {
  const [idea, setIdea] = useState('');
  const [stories, setStories] = useState(1);
  const [stype, setStype] = useState('music_video');
  const [maxCast, setMaxCast] = useState(8);
  const [running, setRunning] = useState(false);
  const [err, setErr] = useState('');
  const go = async () => {
    if (!idea.trim()) return;
    setRunning(true); setErr('');
    try {
      const r = await post<{ steps: string[] }>(`/worlds/${wid}/bigbang`,
        { idea, stories, story_type: stype, max_cast: maxCast, ...llmBody });
      onDone(r.steps);
    } catch (e) { setErr(`${e}`); setRunning(false); }
  };
  return (
    <div className="fixed inset-0 bg-black/70 z-50 flex items-center justify-center p-4"
      onClick={() => !running && onClose()}>
      <div className="bg-gray-950 border border-gray-700 rounded-lg w-full max-w-lg p-4"
        onClick={e => e.stopPropagation()}>
        <h3 className="font-bold mb-1">⚡ Big Bang</h3>
        <p className="text-xs text-gray-500 mb-3">
          One idea in — a filled world sheet, {stories || 'no'} stor{stories === 1 ? 'y' : 'ies'} and
          a proposed cast out. FILL semantics: nothing you already typed is overwritten.
          Three LLM passes — with local Ollama give it a minute or three.
        </p>
        <textarea className={`${inputCls} mb-2`} rows={3} value={idea}
          placeholder="small or huge: 'a lighthouse keeper who bottles storms' or three paragraphs of notes…"
          onChange={e => setIdea(e.target.value)} />
        <div className="grid grid-cols-3 gap-2 mb-3">
          <div>
            <label className="text-[11px] text-gray-500">stories</label>
            <input type="number" min={0} max={5} className={inputCls} value={stories}
              onChange={e => setStories(Math.max(0, Math.min(5, Number(e.target.value) || 0)))} />
          </div>
          <div>
            <label className="text-[11px] text-gray-500">type</label>
            <select className={inputCls} value={stype} onChange={e => setStype(e.target.value)}>
              {['music_video', 'narration', 'short_film', 'series', 'other'].map(t =>
                <option key={t} value={t}>{t.replace('_', ' ')}</option>)}
            </select>
          </div>
          <div>
            <label className="text-[11px] text-gray-500">max cast</label>
            <input type="number" min={1} max={20} className={inputCls} value={maxCast}
              onChange={e => setMaxCast(Math.max(1, Math.min(20, Number(e.target.value) || 8)))} />
          </div>
        </div>
        {err && <div className="text-xs text-amber-400 mb-2">⚠ {err}</div>}
        <div className="flex gap-2">
          <button className={btnAmber} disabled={running || !idea.trim()} onClick={go}>
            {running ? '⏳ building the universe… (this is the slow one)' : '⚡ Go'}</button>
          <button className={btnCls} onClick={onClose} disabled={running}>Cancel</button>
        </div>
      </div>
    </div>
  );
}

// ── 📝 Texts tab ─────────────────────────────────────────────────────────────
function TextsTab({ w, meta, note, reload }: {
  w: WorldT; meta: MetaT; note: (m: string) => void; reload: () => void;
}) {
  const [tid, setTid] = useState(w.texts[0]?.id || '');
  const [title, setTitle] = useState('');
  const [body, setBody] = useState('');
  const [savedAt, setSavedAt] = useState('');
  useEffect(() => {
    if (!w.texts.find(t => t.id === tid)) setTid(w.texts[0]?.id || '');
  }, [w.texts, tid]);
  const t = w.texts.find(x => x.id === tid);
  const [dirty, setDirty] = useState(false);
  // re-sync the editor on selection change OR external update — but never over
  // an unsaved local edit (reviewer finding #7: a stale local body silently
  // reverted an externally-updated text when a dropdown was changed)
  useEffect(() => {
    if (!dirty) setBody(t?.body || '');
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tid, t?.body]);
  useEffect(() => { setDirty(false); }, [tid]);

  const add = async () => {
    const ti = title.trim();
    if (!ti) return;
    try {
      const nt = await post<TextT>(`/worlds/${w.id}/texts`, { title: ti, kind: 'lyrics' });
      setTitle(''); reload(); setTid(nt.id);
    } catch (e) { note(`⚠ ${e}`); }
  };
  const save = async (patch: Partial<TextT>) => {
    if (!t) return;
    try {
      // send the local body only when the user actually edited it — otherwise
      // send the server's copy so a kind/story change can't revert the text
      await post(`/worlds/${w.id}/texts/${t.id}`,
        { kind: t.kind, title: t.title, body: dirty ? body : t.body,
          story_id: t.story_id, ...patch });
      setDirty(false);
      setSavedAt(new Date().toLocaleTimeString());
      reload();
    } catch (e) { note(`⚠ ${e}`); }
  };
  return (
    <div className="flex gap-4">
      <div className="w-56 shrink-0 flex flex-col gap-2">
        <div className="flex gap-1">
          <input className={inputCls} placeholder="new text title…" value={title}
            onChange={e => setTitle(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter') add(); }} />
          <button className={btnAmber} onClick={add} disabled={!title.trim()}>＋</button>
        </div>
        {w.texts.map(x => (
          <button key={x.id}
            className={`text-left rounded px-2 py-1.5 border text-sm ${x.id === tid
              ? 'border-amber-600 bg-gray-900' : 'border-gray-800 hover:border-gray-600'}`}
            onClick={() => setTid(x.id)}>
            <div className="font-semibold truncate">{x.title}</div>
            <div className="text-[11px] text-gray-500">
              {x.kind}{x.story_id ? ` · ${w.stories.find(s => s.id === x.story_id)?.title || '?'}` : ''}
            </div>
          </button>
        ))}
        {!w.texts.length && (
          <div className="text-xs text-gray-600">
            Paste lyrics, narrations or scripts you wrote outside the app — projects will be
            able to pull from here.
          </div>
        )}
      </div>
      {t ? (
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 mb-2 flex-wrap">
            <select className={`${inputCls} w-auto`} value={t.kind}
              onChange={e => save({ kind: e.target.value })}>
              {meta.text_kinds.map(k => <option key={k} value={k}>{k}</option>)}
            </select>
            <select className={`${inputCls} w-auto`} value={t.story_id}
              onChange={e => save({ story_id: e.target.value })}>
              <option value="">no story link</option>
              {w.stories.map(s => <option key={s.id} value={s.id}>{s.title}</option>)}
            </select>
            <button className={btnAmber} onClick={() => save({})}>💾 Save</button>
            {savedAt && <span className="text-xs text-gray-500">saved {savedAt}</span>}
            <button className={`${btnCls} ml-auto text-red-300`} onClick={async () => {
              if (!window.confirm(`Delete "${t.title}"?`)) return;
              await post(`/worlds/${w.id}/texts/${t.id}/delete`); reload();
            }}>🗑</button>
          </div>
          <textarea className={`${inputCls} font-mono`} rows={22} value={body}
            placeholder="paste the lyrics / narration / script here…"
            onChange={e => { setBody(e.target.value); setDirty(true); }}
            onBlur={() => { if (dirty && body !== t.body) save({}); }} />
        </div>
      ) : <div className="text-gray-500 p-6">Add a text on the left.</div>}
    </div>
  );
}

// ── 🔗 Projects tab ──────────────────────────────────────────────────────────
function ProjectsTab({ w, note, reload }: {
  w: WorldT; note: (m: string) => void; reload: () => void;
}) {
  const [projects, setProjects] = useState<ProjT[]>([]);
  const [pid, setPid] = useState('');
  useEffect(() => {
    get<{ projects: ProjT[] }>('/projects')
      .then(r => setProjects(r.projects)).catch(e => note(`⚠ ${e}`));
  }, [note]);
  const attached = projects.filter(p => w.project_ids.includes(p.id));
  const free = projects.filter(p => !w.project_ids.includes(p.id));
  const link = async (projectId: string, attach: boolean) => {
    try {
      await post(`/worlds/${w.id}/projects`, { project_id: projectId, attach });
      setPid(''); reload();
    } catch (e) { note(`⚠ ${e}`); }
  };
  return (
    <div className="max-w-xl">
      <p className="text-xs text-gray-500 mb-3">
        Associate this world with projects. Projects will grow the ability to pull
        characters, story details and texts straight from here.
      </p>
      <div className="flex gap-2 mb-4">
        <select className={inputCls} value={pid} onChange={e => setPid(e.target.value)}>
          <option value="">pick a project…</option>
          {free.map(p => <option key={p.id} value={p.id}>{p.name} ({p.mode})</option>)}
        </select>
        <button className={btnAmber} disabled={!pid} onClick={() => link(pid, true)}>🔗 Attach</button>
      </div>
      {attached.map(p => (
        <div key={p.id} className="flex items-center gap-2 border border-gray-800 rounded px-3 py-2 mb-2">
          <span className="text-sm">{p.name}</span>
          <span className="text-xs text-gray-500">{p.mode}</span>
          <button className={`${btnCls} ml-auto`} onClick={() => link(p.id, false)}>unlink</button>
        </div>
      ))}
      {!attached.length && <div className="text-xs text-gray-600">Nothing attached yet.</div>}
    </div>
  );
}
