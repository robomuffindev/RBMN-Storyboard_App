/**
 * 🎼 Score a story (v1.277.16) — the ARC PAIRING screen.
 *
 *   world + story → ✨ plan → CUES ON PAPER (editable) → 🎬 render → 📥 import
 *
 * The seconds column is the point of the whole lane: the plan is normalised so
 * the cues sum to the target, and each cue renders at EXACTLY its length. Cues
 * are never rendered twice — a cue already queued/running is skipped, and the
 * backend claims it under a lock before the job starts.
 *
 * Live status per cue (the standing rule): what, WHERE, how long — the worker
 * that took it, the ticking elapsed, and the engine's own detail line.
 */
import { useCallback, useEffect, useRef, useState } from 'react';

const B = '/api/audio-lab/score';

async function jj<T>(r: Response): Promise<T> {
  if (!r.ok) {
    let d = ''; try { d = (await r.json()).detail || ''; } catch { /* */ }
    throw new Error(d || `${r.status}`);
  }
  return r.json() as Promise<T>;
}
const post = <T,>(p: string, body?: unknown) =>
  fetch(B + p, { method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body) }).then(r => jj<T>(r));

type CueT = {
  i: number; name: string; seconds: number; caption: string; lyrics: string;
  beat?: string; job_id?: string; status?: string; worker?: string;
  detail?: string; error?: string | null; elapsed_s?: number; file?: string;
  adjusted_by?: number;
};
type ScoreT = {
  id: string; title: string; engine: string; instrumental?: boolean;
  world_name?: string; story_title?: string; total_seconds: number;
  cues: CueT[];
  progress?: { cues: number; done: number; error: number; running: number;
               seconds_total: number; seconds_done: number };
  started?: unknown[]; failed?: { cue: number; error: string }[];
};
type StoryT = { id: string; title: string; story_type: string };
type TextT = { id: string; title: string; kind: string; chars: number; story_id: string };
type WorldT = { id: string; name: string; stories: StoryT[]; texts: TextT[] };
type ProjT = { id: string; name: string };
type ScoreRowT = { id: string; title: string; engine: string; cues: number; created_at: string };

const inputCls =
  'w-full bg-gray-900 border border-gray-700 rounded px-2 py-1.5 text-sm text-gray-100 ' +
  'focus:border-emerald-600 focus:outline-none placeholder-gray-600';
const btnCls =
  'px-3 py-1.5 rounded text-sm font-medium bg-gray-800 hover:bg-gray-700 text-gray-100 ' +
  'disabled:opacity-40 disabled:cursor-not-allowed transition-colors';
const btnGo =
  'px-3 py-1.5 rounded text-sm font-medium bg-emerald-700 hover:bg-emerald-600 text-white ' +
  'disabled:opacity-40 disabled:cursor-not-allowed transition-colors';

type EngOptT = { k: string; label: string; hint: string; ready: boolean };

export default function ScorePanel({ engines }: { engines: EngOptT[] }) {
  const [worlds, setWorlds] = useState<WorldT[]>([]);
  const [projects, setProjects] = useState<ProjT[]>([]);
  const [saved, setSaved] = useState<ScoreRowT[]>([]);
  const [msg, setMsg] = useState('');
  const [busy, setBusy] = useState(false);

  // plan form
  const [wid, setWid] = useState('');
  const [sid, setSid] = useState('');
  const [textId, setTextId] = useState('');
  const [engine, setEngine] = useState<string>('ace15');
  const [cueCount, setCueCount] = useState(5);
  const [totalSeconds, setTotalSeconds] = useState(180);
  const [instrumental, setInstrumental] = useState(true);
  const [guidance, setGuidance] = useState('');

  // the score being worked on
  const [score, setScore] = useState<ScoreT | null>(null);
  const [projPick, setProjPick] = useState('');
  const dirty = useRef(false);

  const world = worlds.find(w => w.id === wid);
  const stories = world?.stories || [];
  const texts = (world?.texts || []).filter(t => !sid || !t.story_id || t.story_id === sid);

  const loadSources = useCallback(async () => {
    try {
      const d = await fetch(`${B}/sources`).then(r =>
        jj<{ worlds: WorldT[]; projects: ProjT[]; scores: ScoreRowT[] }>(r));
      setWorlds(d.worlds || []); setProjects(d.projects || []); setSaved(d.scores || []);
      setWid(v => v || (d.worlds?.[0]?.id ?? ''));
    } catch (e) { setMsg(`⚠ ${e}`); }
  }, []);
  useEffect(() => { void loadSources(); }, [loadSources]);
  // ⚠ keyed on the story IDS, not the array: `stories` is a fresh array every
  // render, so depending on it re-runs this effect on every render.
  const storyKey = stories.map(s => s.id).join(',');
  useEffect(() => {
    setSid(v => (storyKey.split(',').includes(v) ? v : (storyKey.split(',')[0] || '')));
  }, [storyKey]);

  // live poll while anything is in flight — but never clobber unsaved edits
  const anyLive = !!score?.cues?.some(c => ['queued', 'running', 'claimed'].includes(c.status || ''));
  useEffect(() => {
    if (!score?.id || !anyLive) return;
    const iv = window.setInterval(() => {
      fetch(`${B}/${score.id}`).then(r => jj<ScoreT>(r))
        .then(s => { if (!dirty.current) setScore(s); })
        .catch(() => { /* transient */ });
    }, 3000);
    return () => window.clearInterval(iv);
  }, [score?.id, anyLive]);

  const engineReady = !!engines.find(e => e.k === engine)?.ready;
  const per = cueCount > 0 ? Math.round(totalSeconds / cueCount) : 0;

  const doPlan = async () => {
    setBusy(true); setMsg('✨ the model is scoring the story…');
    try {
      const s = await post<ScoreT>('/plan', {
        world_id: wid, story_id: sid, text_id: textId, engine,
        cue_count: cueCount, total_seconds: totalSeconds,
        instrumental, guidance,
      });
      dirty.current = false; setScore(s); setMsg(`🎼 ${s.cues.length} cues on paper — edit, then render`);
      void loadSources();
    } catch (e) { setMsg(`⚠ ${e}`); }
    setBusy(false);
  };
  const openScore = async (id: string) => {
    try { const s = await fetch(`${B}/${id}`).then(r => jj<ScoreT>(r));
      dirty.current = false; setScore(s); setMsg(''); } catch (e) { setMsg(`⚠ ${e}`); }
  };
  const saveCues = async (keepTotal: boolean) => {
    if (!score) return;
    setBusy(true);
    try {
      const s = await post<ScoreT>(`/${score.id}/cues`, {
        cues: score.cues, engine: score.engine, title: score.title,
        total_seconds: keepTotal ? score.total_seconds : 0,
      });
      dirty.current = false; setScore(s); setMsg('saved');
    } catch (e) { setMsg(`⚠ ${e}`); }
    setBusy(false);
  };
  const doRender = async (only: number[], redo = false) => {
    if (!score) return;
    setBusy(true);
    try {
      if (dirty.current) await saveCues(false);
      const s = await post<ScoreT>(`/${score.id}/render`, { only, redo });
      setScore(s);
      const bad = (s.failed || []).length;
      setMsg(bad ? `⚠ ${bad} cue(s) failed to start — see below` : '🎬 rendering — fanned across the ready boxes');
    } catch (e) { setMsg(`⚠ ${e}`); }
    setBusy(false);
  };
  const doImport = async () => {
    if (!score || !projPick) return;
    setBusy(true);
    try {
      const r = await post<{ imported: unknown[]; skipped: number[] }>(
        `/${score.id}/import`, { project_id: projPick });
      setMsg(`📥 imported ${r.imported.length} cue(s)${r.skipped.length ? ` · ${r.skipped.length} not finished yet` : ''}`);
    } catch (e) { setMsg(`⚠ ${e}`); }
    setBusy(false);
  };

  const setCue = (i: number, patch: Partial<CueT>) => {
    dirty.current = true;
    setScore(s => s ? { ...s, cues: s.cues.map(c => c.i === i ? { ...c, ...patch } : c) } : s);
  };
  const sum = (score?.cues || []).reduce((a, c) => a + (Number(c.seconds) || 0), 0);

  return (
    <div className="max-w-5xl space-y-4">
      {/* ── the plan form ── */}
      <div className="border border-gray-800 rounded p-3 space-y-2">
        <div className="text-xs font-semibold text-emerald-300">
          ✨ Plan a score — the story decides how many cues and how long each one runs
        </div>
        <div className="flex gap-2 flex-wrap items-end">
          <div>
            <label className="text-[11px] text-gray-500">world</label>
            <select className={`${inputCls} w-56`} value={wid} onChange={e => setWid(e.target.value)}>
              {!worlds.length && <option value="">no worlds yet</option>}
              {worlds.map(w => <option key={w.id} value={w.id}>{w.name}</option>)}
            </select>
          </div>
          <div>
            <label className="text-[11px] text-gray-500">story</label>
            <select className={`${inputCls} w-56`} value={sid} onChange={e => setSid(e.target.value)}>
              {!stories.length && <option value="">no stories in this world</option>}
              {stories.map(s => <option key={s.id} value={s.id}>{s.title || s.id}</option>)}
            </select>
          </div>
          <div>
            <label className="text-[11px] text-gray-500">lyrics / text (optional)</label>
            <select className={`${inputCls} w-52`} value={textId} onChange={e => setTextId(e.target.value)}>
              <option value="">— none —</option>
              {texts.map(t => <option key={t.id} value={t.id}>{t.kind}: {t.title}</option>)}
            </select>
          </div>
        </div>
        <div className="flex gap-2 flex-wrap items-end">
          <div>
            <label className="text-[11px] text-gray-500">engine</label>
            <select className={`${inputCls} w-56`} value={engine}
                    onChange={e => setEngine(e.target.value)}
                    title={engines.find(o => o.k === engine)?.hint || ''}>
              {engines.map(o => (
                <option key={o.k} value={o.k}>{o.label} {o.ready ? '✅' : '❌'}</option>
              ))}
            </select>
          </div>
          <div>
            <label className="text-[11px] text-gray-500">cues</label>
            <input type="number" min={1} max={24} className={`${inputCls} w-20`} value={cueCount}
                   onChange={e => setCueCount(Math.max(1, Math.min(24, Number(e.target.value) || 1)))} />
          </div>
          <div>
            <label className="text-[11px] text-gray-500">total length (s)</label>
            <input type="number" min={5} max={7200} className={`${inputCls} w-28`} value={totalSeconds}
                   onChange={e => setTotalSeconds(Math.max(5, Number(e.target.value) || 5))} />
          </div>
          <div className="text-[11px] text-gray-500 pb-2">≈ {per}s per cue</div>
          <label className="flex items-center gap-1 text-xs text-gray-300 pb-2">
            <input type="checkbox" checked={instrumental}
                   onChange={e => setInstrumental(e.target.checked)} />
            instrumental
          </label>
          <button className={btnGo} disabled={busy || !wid || !sid || !engineReady}
                  onClick={() => void doPlan()}>
            {busy ? '⏳' : '✨ Plan the score'}
          </button>
        </div>
        <input className={inputCls} value={guidance} placeholder="direction (optional) — e.g. no drums until the third cue; keep it sparse and dry"
               onChange={e => setGuidance(e.target.value)} />
        {!engineReady && <div className="text-[11px] text-amber-400">
          {engines.find(o => o.k === engine)?.label || engine} is not ready on any box —
          the plan is free, but the render will not start.
        </div>}
        {!!saved.length && (
          <div className="flex gap-2 flex-wrap items-center text-xs pt-1">
            <span className="text-gray-500">saved scores:</span>
            {saved.slice(0, 12).map(s => (
              <button key={s.id} className="px-2 py-0.5 rounded border border-gray-700 text-gray-300 hover:text-white"
                      onClick={() => void openScore(s.id)}>
                {s.title} <span className="text-gray-600">· {s.cues}</span>
              </button>
            ))}
          </div>
        )}
        <div className="text-xs text-gray-400">{msg}</div>
      </div>

      {/* ── the cue sheet ── */}
      {score && (
        <div className="border border-gray-800 rounded p-3 space-y-3">
          <div className="flex items-center gap-2 flex-wrap">
            <input className={`${inputCls} w-72`} value={score.title}
                   onChange={e => { dirty.current = true; setScore(s => s ? { ...s, title: e.target.value } : s); }} />
            {/* the engine is a per-SCORE decision you can change after planning
                — the models disagree and which one suits a story is an ear
                question, not a setting decided once in the plan form */}
            <select className="bg-gray-900 border border-gray-700 rounded px-2 py-1 text-xs"
                    value={score.engine}
                    onChange={e => { dirty.current = true;
                      setScore(s => s ? { ...s, engine: e.target.value } : s); }}>
              {engines.map(o => (
                <option key={o.k} value={o.k}>{o.label} {o.ready ? '✅' : '❌'}</option>
              ))}
            </select>
            <span className="text-xs text-gray-500">
              {score.world_name}{score.story_title ? ` · ${score.story_title}` : ''}
              {score.instrumental ? ' · instrumental' : ' · with lyrics'}
            </span>
            <span className={`text-xs ${Math.abs(sum - score.total_seconds) < 0.5 ? 'text-gray-400' : 'text-amber-400'}`}>
              Σ {Math.round(sum)}s / target {Math.round(score.total_seconds)}s
            </span>
            {score.progress && (
              <span className="text-xs text-emerald-300">
                {score.progress.done}/{score.progress.cues} rendered
                {score.progress.running ? ` · ${score.progress.running} in flight` : ''}
                {score.progress.error ? ` · ${score.progress.error} error` : ''}
              </span>
            )}
            <div className="flex-1" />
            <button className={btnCls} disabled={busy} onClick={() => void saveCues(false)}>💾 Save</button>
            <button className={btnGo} disabled={busy || !engineReady}
                    onClick={() => void doRender([])}>🎬 Render unrendered</button>
            <button className={btnCls} disabled={busy}
                    onClick={() => void post(`/${score.id}/cancel`).then(() => openScore(score.id))}>⏹</button>
          </div>

          <div className="space-y-2">
            {score.cues.map(c => {
              const st = c.status || 'paper';
              const tone = st === 'done' ? 'text-green-300' : st === 'error' ? 'text-red-400'
                : st === 'paper' ? 'text-gray-500' : 'text-blue-300';
              return (
                <div key={c.i} className="border border-gray-800 rounded p-2 space-y-1">
                  <div className="flex gap-2 items-center flex-wrap">
                    <span className="text-xs text-gray-600 w-6">{c.i + 1}.</span>
                    <input className={`${inputCls} w-56`} value={c.name}
                           onChange={e => setCue(c.i, { name: e.target.value })} />
                    <input type="number" min={5} max={300} className={`${inputCls} w-20`} value={c.seconds}
                           onChange={e => setCue(c.i, { seconds: Number(e.target.value) || 5 })} />
                    <span className="text-[11px] text-gray-600">s</span>
                    <span className={`text-xs font-bold ${tone}`}>
                      {st === 'done' ? `✓ done · ${Math.round(c.elapsed_s || 0)}s`
                        : st === 'error' ? `✕ ${c.error || 'error'}`
                          : st === 'paper' ? '📄 paper'
                            : `⏳ ${c.detail || st} ${Math.round(c.elapsed_s || 0)}s`}
                    </span>
                    {c.worker && <span className="text-[11px] text-gray-500">@ {c.worker}</span>}
                    {typeof c.adjusted_by === 'number' && Math.abs(c.adjusted_by) >= 0.5 && (
                      <span className="text-[11px] text-amber-400" title="adjusted so the cues sum to the target">
                        {c.adjusted_by > 0 ? '+' : ''}{c.adjusted_by}s
                      </span>
                    )}
                    <div className="flex-1" />
                    <button className={btnCls} disabled={busy || !engineReady}
                            onClick={() => void doRender([c.i], true)}>
                      {c.job_id ? '↻' : '🎵'}
                    </button>
                  </div>
                  {c.beat && <div className="text-[11px] text-gray-500 pl-8">📖 {c.beat}</div>}
                  <textarea className={`${inputCls} text-xs`} rows={2} value={c.caption}
                            placeholder="the music description handed to the model…"
                            onChange={e => setCue(c.i, { caption: e.target.value })} />
                  {!score.instrumental && (
                    <textarea className={`${inputCls} text-xs font-mono`} rows={3} value={c.lyrics}
                              placeholder="[verse] …"
                              onChange={e => setCue(c.i, { lyrics: e.target.value })} />
                  )}
                  {c.status === 'done' && c.job_id && (
                    <audio controls preload="none" src={`/api/audio-lab/media/${c.job_id}`}
                           className="w-full h-9" />
                  )}
                </div>
              );
            })}
          </div>

          {!!score.failed?.length && (
            <div className="text-xs text-red-400">
              {score.failed.map(f => <div key={f.cue}>cue {f.cue + 1}: {f.error}</div>)}
            </div>
          )}

          <div className="flex gap-2 items-center flex-wrap border-t border-gray-800 pt-2">
            <span className="text-xs text-gray-500">import every finished cue →</span>
            <select className="bg-gray-900 border border-gray-700 rounded px-2 py-1 text-xs"
                    value={projPick} onChange={e => setProjPick(e.target.value)}>
              <option value="">project…</option>
              {projects.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}
            </select>
            <button className={btnCls} disabled={busy || !projPick} onClick={() => void doImport()}>
              📥 Import as MUSIC assets
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
