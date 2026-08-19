/**
 * 📖 CHAPTERS — the rung between an arc and a video (v1.277.46).
 *
 * His brief: *"The chapter should work off the story beats and tell those parts
 * in more detail. A chapter can be a single narration and that makes it easier
 * to keep the full media generations like video smaller per chapter rather than
 * trying to jam everything in at once."*
 *
 *     STORY   → prose + arcs        the spine
 *     CHAPTER → one arc, at length  ⭐ ONE CHAPTER = ONE VIDEO PROJECT
 *     BEAT    → a slice of it       the project's timeline chapters
 *
 * ⭐ The LLM writes ONE CHAPTER AT A TIME (his call) — smaller context, better
 * prose, and you can edit between them. ✨ Outline is the only whole-story call
 * and it writes titles and summaries only, never narration.
 *
 * ⚠ This panel owns its own fetch of `/chapters` rather than reading the world
 * object. A chapter's narration can be tens of thousands of words; putting it
 * in the world payload every screen already loads would make the Cast tab slow
 * for a reason nobody would find.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
// 🎙 speak the chapter / audition / keep, and 🎬 turn it into a project. Its
// own file: it owns a poll, a modal and a persisted config, none of which the
// chapter list cares about.
import ChapterVoicePanel from './ChapterVoicePanel';

/** ⭐ HIS CALL (2026-08-18): 3 minutes was "too small" — a chapter is a FULL
 *  telling. 10 min ≈ 1500 words, split across the beats and written one call
 *  at a time. Per-chapter `target_minutes` still overrides it. */
const DEFAULT_MIN = 10;

const inputCls =
  'w-full bg-gray-900 border border-gray-700 rounded px-2 py-1.5 text-sm text-gray-100 ' +
  'focus:border-amber-600 focus:outline-none placeholder-gray-600';
const btnCls =
  'px-3 py-1.5 rounded text-sm font-medium bg-gray-800 hover:bg-gray-700 text-gray-100 ' +
  'disabled:opacity-40 disabled:cursor-not-allowed transition-colors';
const btnAmber =
  'px-3 py-1.5 rounded text-sm font-medium bg-amber-700 hover:bg-amber-600 text-white ' +
  'disabled:opacity-40 disabled:cursor-not-allowed transition-colors';

async function j<T>(r: Response): Promise<T> {
  if (!r.ok) {
    let d = '';
    try { d = (await r.json()).detail || ''; } catch { d = await r.text().catch(() => ''); }
    throw new Error(d || `${r.status}`);
  }
  return r.json();
}
const B = '/api/storyworld';
const get = <T,>(p: string) => fetch(B + p).then(r => j<T>(r));
const post = <T,>(p: string, body?: unknown) =>
  fetch(B + p, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  }).then(r => j<T>(r));

export type BeatT = {
  id: string; i: number; title: string; summary: string; mood: string;
  characters: string[]; locations: string[];
};
export type ChapterT = {
  id: string; i: number; title: string; arc_id: string; arc_title?: string;
  summary: string; mood: string; notes: string;
  characters: string[]; locations: string[];
  beats: BeatT[]; beat_count: number;
  narration: string; narration_words: number; est_minutes: number;
  recorded_seconds: number; has_narration: boolean;
  target_minutes: number;
  narration_files?: Record<string, {
    id: string; filename: string; ext: string; bytes: number; seconds: number;
  }>;
  updated_at?: string;
};
type NarrJobT = {
  status?: string; stage?: string; detail?: string; total?: number; done?: number;
  current?: string; elapsed_s?: number; provider?: string; model?: string;
  host?: string; error?: string; words_so_far?: number; target_words?: number;
  words?: number; est_minutes?: number; paragraphs?: number; chapter?: string;
  stage_times?: Record<string, number>;
  log?: { t: number; stage: string; detail: string }[];
};
type ListT = {
  chapters: ChapterT[];
  arcs: { id: string; title: string }[];
  totals: { chapters: number; written: number; words: number;
            est_minutes: number; recorded: number };
  story_narration_words: number;
};

export default function ChaptersPanel({ wid, sid, llmBody, note }: {
  wid: string; sid: string; llmBody: object; note: (m: string) => void;
}) {
  const [data, setData] = useState<ListT | null>(null);
  const [open, setOpen] = useState('');            // the expanded chapter id
  const [busy, setBusy] = useState<Record<string, boolean>>({});
  const [dir, setDir] = useState('');
  const [count, setCount] = useState(0);
  // ⚠ tone is PER CHAPTER. One shared string meant typing a tone into chapter 3
  // changed it in every other chapter's box at the same time.
  const [tone, setTone] = useState<Record<string, string>>({});
  const [draft, setDraft] = useState<Record<string, string>>({});
  // ✍ the chapter currently being written, and its live job
  const [writing, setWriting] = useState('');
  const [njob, setNjob] = useState<NarrJobT | null>(null);
  const slotRef = useRef<HTMLInputElement | null>(null);
  const slotFor = useRef<{ cid: string; slot: string }>({ cid: '', slot: 'audio' });

  const bz = (k: string, on: boolean) => setBusy(p => ({ ...p, [k]: on }));
  /** ⚠ every mutation goes through this. A bare `await post()` inside an
   *  onClick is an unhandled rejection with nothing on screen — the user
   *  presses 🗑, nothing happens, and there is no error to report. */
  const act = async (fn: () => Promise<unknown>, ok = '') => {
    try { await fn(); if (ok) note(ok); await load(); }
    catch (e) { note(`⚠ ${e}`); }
  };
  const load = useCallback(async () => {
    if (!sid) { setData(null); return; }
    try { setData(await get<ListT>(`/worlds/${wid}/stories/${sid}/chapters`)); }
    catch (e) { note(`⚠ ${e}`); }
  }, [wid, sid, note]);
  useEffect(() => { void load(); }, [load]);
  useEffect(() => { setOpen(''); setDraft({}); setWriting(''); setNjob(null); }, [sid]);

  // ── ✍ the narration job's live status ────────────────────────────────────
  // ⚠ It polls while `starting` too — a job that has not reached `running` yet
  // is exactly the moment the screen looks most broken. And on finishing it
  // RELOADS, because the prose only exists on the server until then.
  useEffect(() => {
    if (!writing) return;
    let stop = false;
    const tick = async () => {
      try {
        const r = await get<{ job: NarrJobT }>(
          `/worlds/${wid}/stories/${sid}/chapters/${writing}/narration/job`);
        if (stop) return;
        setNjob(r.job);
        const s = r.job?.status;
        if (s === 'starting' || s === 'running') return;
        setWriting('');
        await load();
        if (s === 'done') {
          note(`✍ ${r.job.words} words ≈ ${r.job.est_minutes} min in `
            + `${r.job.paragraphs} paragraphs`);
        } else if (s === 'error') { note(`⚠ ${r.job.error}`); }
      } catch { /* keep polling — a blip is not a failure */ }
    };
    void tick();
    const h = window.setInterval(tick, 2000);
    return () => { stop = true; window.clearInterval(h); };
  }, [writing, wid, sid, load, note]);

  // ⭐ ADOPT A RUN THAT WAS ALREADY GOING — a reload mid-write, or a second tab.
  // Without this the job finishes on the server and the screen never notices,
  // which is the same freeze the Codex tab shipped with once already.
  const ids = (data?.chapters || []).map(c => c.id).join(',');
  useEffect(() => {
    if (!sid || !ids || writing) return;
    let stop = false;
    (async () => {
      for (const cid of ids.split(',')) {
        if (stop) return;
        try {
          const r = await get<{ job: NarrJobT }>(
            `/worlds/${wid}/stories/${sid}/chapters/${cid}/narration/job`);
          if (r.job?.status === 'starting' || r.job?.status === 'running') {
            if (!stop) { setNjob(r.job); setWriting(cid); setOpen(cid); }
            return;
          }
        } catch { /* ignore */ }
      }
    })();
    return () => { stop = true; };
  }, [wid, sid, ids, writing]);

  const chapters = data?.chapters || [];
  const arcs = data?.arcs || [];

  const outline = async (overwrite: boolean) => {
    bz('gen', true);
    note('📖 breaking the arcs into chapters…');
    try {
      const r = await post<{ chapters: ChapterT[]; note?: string }>(
        `/worlds/${wid}/stories/${sid}/chapters/generate`,
        { count, direction: dir, overwrite, ...llmBody });
      note(r.note || `📖 ${r.chapters.length} chapters — now write them one at a time`);
      await load();
    } catch (e) { note(`⚠ ${e}`); } finally { bz('gen', false); }
  };

  const patch = async (cid: string, body: object, quiet = false) => {
    try {
      await post(`/worlds/${wid}/stories/${sid}/chapters/${cid}`, body);
      if (!quiet) note('saved');
      await load();
    } catch (e) { note(`⚠ ${e}`); }
  };

  // ✍ writing is now a JOB — one model call PER BEAT, minutes long, so it gets
  // the live status contract rather than a spinner that might be dead.
  const write = async (c: ChapterT) => {
    // ⚠ a rewrite REPLACES prose that may have been hand-edited — ask first.
    const unsaved = (draft[c.id] ?? c.narration) !== c.narration;
    if (c.has_narration && !window.confirm(
      `Rewrite chapter ${c.i + 1}? The current narration will be replaced`
      + (unsaved ? ', including your unsaved edits in the box.' : '.'))) return;
    const mins = c.target_minutes || DEFAULT_MIN;
    try {
      const r = await post<{ target_words: number; provider: string; model: string;
                             beats: number }>(
        `/worlds/${wid}/stories/${sid}/chapters/${c.id}/narration`,
        { minutes: mins, tone: tone[c.id] || '', with_beats: true,
          overwrite: !!c.has_narration, ...llmBody });
      note(`✍ writing ~${r.target_words} words across ${r.beats || 'new'} beats `
        + `on ${r.provider}/${r.model}`);
      setWriting(c.id);
      // clear the stale draft so the finished text is what you see
      setDraft(d => { const n = { ...d }; delete n[c.id]; return n; });
    } catch (e) { note(`⚠ ${e}`); }
  };

  const splitBeats = async (c: ChapterT) => {
    bz(`b.${c.id}`, true);
    try {
      const r = await post<ChapterT>(
        `/worlds/${wid}/stories/${sid}/chapters/${c.id}/beats`, { ...llmBody });
      note(`🎬 ${r.beat_count} beats — these become the project's timeline chapters`);
      await load();
    } catch (e) { note(`⚠ ${e}`); } finally { bz(`b.${c.id}`, false); }
  };

  const move = (i: number, d: -1 | 1) => {
    const n = [...chapters];
    [n[i + d], n[i]] = [n[i], n[i + d]];
    return act(() => post(`/worlds/${wid}/stories/${sid}/chapters/reorder`,
      { order: n.map(c => c.id) }));
  };

  const upload = async (cid: string, slot: string, f: File) => {
    bz(`f.${cid}.${slot}`, true);
    try {
      const fd = new FormData(); fd.append('file', f);
      const r = await fetch(
        `${B}/worlds/${wid}/stories/${sid}/chapters/${cid}/file/${slot}`,
        { method: 'POST', body: fd });
      if (!r.ok) throw new Error((await r.json()).detail || r.status);
      const g = await r.json();
      note(`${slot}: ${g.file?.filename}`
        + (g.file?.seconds ? ` · ${Math.round(g.file.seconds)}s` : ''));
      await load();
    } catch (e) { note(`⚠ ${e}`); } finally { bz(`f.${cid}.${slot}`, false); }
  };

  const t = data?.totals;
  return (
    <div className="mt-4 border border-gray-800 rounded p-3">
      <input ref={slotRef} type="file" className="hidden"
        onChange={e => {
          const f = e.target.files?.[0]; e.target.value = '';
          if (f) void upload(slotFor.current.cid, slotFor.current.slot, f);
        }} />

      <div className="flex items-center gap-2 flex-wrap mb-2">
        <span className="text-sm font-semibold text-sky-300">📖 Chapters</span>
        <span className="text-[11px] text-gray-500">
          each chapter tells ONE arc at length and becomes <b>one video project</b> —
          its own narration, its own recording, its own beats
        </span>
        <div className="flex-1" />
        {t && !!t.chapters && (
          <span className="text-[11px] text-gray-400">
            {t.written}/{t.chapters} written · {t.words} words ≈ {t.est_minutes} min
            {t.recorded ? ` · ${t.recorded} recorded` : ''}
          </span>
        )}
      </div>

      {!arcs.length ? (
        <div className="text-xs text-gray-600">
          No arcs yet — chapters are told <i>from</i> arcs. Press ✨ Structure into arcs above
          first, so a chapter, its backing bed and the project&apos;s timeline all land on the
          same boundaries.
        </div>
      ) : (
        <>
          <div className="flex gap-2 items-end flex-wrap mb-2">
            <div>
              <label className="text-[10px] text-gray-500 block">how many</label>
              <input type="number" min={0} max={60} value={count} className={`${inputCls} w-20`}
                onChange={e => setCount(Math.max(0, Number(e.target.value) || 0))} />
            </div>
            <input className={`${inputCls} flex-1 min-w-[12rem]`} value={dir}
              placeholder="optional direction — e.g. 'end each chapter on a question'"
              onChange={e => setDir(e.target.value)} />
            <button className={btnAmber} disabled={!!busy.gen}
              onClick={() => void outline(false)}>
              {busy.gen ? '⏳ outlining…' : '✨ Outline chapters'}</button>
            {!!chapters.length && (
              <button className={btnCls} disabled={!!busy.gen}
                onClick={() => {
                  if (window.confirm('Replace every chapter, including any narration already written?'))
                    void outline(true);
                }}>♻ Re-outline</button>
            )}
            <span className="text-[10px] text-gray-600 pb-2">0 = let the model decide</span>
          </div>
          <div className="text-[11px] text-gray-600 mb-2">
            ✨ Outline writes titles and summaries only. The narration is written{' '}
            <b>one chapter at a time</b> — smaller context, better prose, and you can edit
            between them.
          </div>
        </>
      )}

      <div className="space-y-1">
        {chapters.map((c, i) => {
          const isOpen = open === c.id;
          const body = draft[c.id] ?? c.narration ?? '';
          const rec = (c.narration_files || {}).audio;
          // ⚠ the uncontrolled inputs below use defaultValue, which is read
          // ONCE per mount. Keying them on updated_at as well as the id remounts
          // them after a reload, so a server-side change (✍ Write sets
          // target_minutes; ✨ Outline rewrites titles) is actually visible.
          const k = `${c.id}:${c.updated_at || ''}`;
          return (
            <div key={c.id} className="border border-gray-800 rounded">
              {/* collapsed row */}
              <div className="flex items-center gap-2 px-2 py-1.5 text-xs flex-wrap">
                <span className="text-gray-600 w-5">{i + 1}.</span>
                <button className="font-semibold text-left text-sm text-gray-100 hover:text-amber-300 truncate max-w-[16rem]"
                  onClick={() => setOpen(isOpen ? '' : c.id)}>
                  {isOpen ? '▾' : '▸'} {c.title}
                </button>
                {c.arc_title && (
                  <span className="text-[10px] px-1.5 py-0.5 rounded bg-gray-800 text-gray-400">
                    🎬 {c.arc_title}
                  </span>
                )}
                {c.has_narration ? (
                  <span className="text-[10px] text-emerald-400">
                    ✍ {c.narration_words}w ≈ {c.est_minutes}m
                  </span>
                ) : <span className="text-[10px] text-gray-600">not written yet</span>}
                {!!c.beat_count && (
                  <span className="text-[10px] text-sky-400">🎬 {c.beat_count} beats</span>
                )}
                {!!c.recorded_seconds && (
                  <span className="text-[10px] text-amber-400">
                    🎙 {Math.floor(c.recorded_seconds / 60)}m {Math.round(c.recorded_seconds % 60)}s
                  </span>
                )}
                <div className="flex-1" />
                {writing === c.id ? (
                  <>
                    <span className="text-[10px] text-amber-300">
                      ⏳ {njob?.stage === 'beats' ? 'planning beats'
                        : `beat ${njob?.done ?? 0}/${njob?.total ?? '?'}`}
                      {njob?.words_so_far ? ` · ${njob.words_so_far} words` : ''}
                      {njob?.elapsed_s ? ` · ${Math.round(njob.elapsed_s)}s` : ''}
                    </span>
                    <button className={`${btnCls} text-red-300`}
                      onClick={() => void act(() => post(
                        `/worlds/${wid}/stories/${sid}/chapters/${c.id}/narration/cancel`))}>
                      ⏹</button>
                  </>
                ) : (
                  <button className={btnAmber} disabled={!!writing}
                    title={writing ? 'another chapter is being written' : ''}
                    onClick={() => void write(c)}>
                    {c.has_narration ? '♻ Rewrite' : '✍ Write narration'}</button>
                )}
                <button className={btnCls} title="move up" disabled={i === 0}
                  onClick={() => void move(i, -1)}>↑</button>
                <button className={btnCls} title="move down" disabled={i === chapters.length - 1}
                  onClick={() => void move(i, 1)}>↓</button>
                <button className={`${btnCls} text-red-300`} title="delete this chapter"
                  onClick={() => {
                    if (!window.confirm(`Delete chapter "${c.title}" and its narration?`)) return;
                    void act(() => post(
                      `/worlds/${wid}/stories/${sid}/chapters/${c.id}/delete`),
                      `deleted "${c.title}"`);
                  }}>🗑</button>
              </div>

              {isOpen && (
                <div className="border-t border-gray-800 p-2 space-y-2">
                  <div className="flex gap-1 flex-wrap">
                    <input key={`t${k}`} className={`${inputCls} flex-1 min-w-[10rem] font-semibold`}
                      defaultValue={c.title} placeholder="chapter title"
                      onBlur={e => { if (e.target.value !== c.title)
                        void patch(c.id, { title: e.target.value }); }} />
                    <select className={`${inputCls} w-48`} value={c.arc_id}
                      onChange={e => void patch(c.id, { arc_id: e.target.value })}>
                      <option value="">— no arc —</option>
                      {arcs.map(a => <option key={a.id} value={a.id}>🎬 {a.title}</option>)}
                    </select>
                    <input key={`m${k}`} className={`${inputCls} w-40`} defaultValue={c.mood}
                      placeholder="mood — drives its bed"
                      onBlur={e => { if (e.target.value !== c.mood)
                        void patch(c.id, { mood: e.target.value }); }} />
                    <div className="flex items-center gap-1">
                      <input key={`n${k}`} type="number" min={0.5} max={120} step={0.5}
                        className={`${inputCls} w-20`}
                        defaultValue={c.target_minutes || DEFAULT_MIN}
                        title="target minutes — a WORD BUDGET (× 150) split across the beats"
                        onBlur={e => void patch(c.id,
                          { target_minutes: Number(e.target.value) || DEFAULT_MIN }, true)} />
                      <span className="text-[10px] text-gray-500 whitespace-nowrap">
                        min ≈ {Math.round((c.target_minutes || DEFAULT_MIN) * 150)}w
                      </span>
                    </div>
                  </div>
                  <textarea key={`s${k}`} className={`${inputCls} text-xs`} rows={2}
                    defaultValue={c.summary}
                    placeholder="what happens in THIS chapter — 2-4 sentences"
                    onBlur={e => { if (e.target.value !== c.summary)
                      void patch(c.id, { summary: e.target.value }); }} />
                  <div className="flex gap-1 flex-wrap">
                    <input key={`c${k}`} className={`${inputCls} flex-1 min-w-[10rem]`}
                      defaultValue={(c.characters || []).join(', ')}
                      placeholder="characters present (narrows the project's cast pull)"
                      onBlur={e => void patch(c.id, {
                        characters: e.target.value.split(',').map(x => x.trim()).filter(Boolean),
                      }, true)} />
                    <input key={`l${k}`} className={`${inputCls} flex-1 min-w-[10rem]`}
                      defaultValue={(c.locations || []).join(', ')} placeholder="📍 locations"
                      onBlur={e => void patch(c.id, {
                        locations: e.target.value.split(',').map(x => x.trim()).filter(Boolean),
                      }, true)} />
                    <input className={`${inputCls} flex-1 min-w-[10rem]`}
                      value={tone[c.id] || ''}
                      placeholder="tone for the writer (optional)"
                      onChange={e => setTone(t => ({ ...t, [c.id]: e.target.value }))} />
                  </div>

                  {/* the narration itself */}
                  <div>
                    <div className="flex items-center gap-2 mb-1 flex-wrap">
                      <span className="text-[11px] font-semibold text-emerald-300">
                        ✍ This chapter&apos;s narration
                      </span>
                      <span className="text-[10px] text-gray-600">
                        the words a TTS reads · {c.narration_words} words ≈{' '}
                        {c.est_minutes} min ·{' '}
                        {/* ⭐ paragraphs are what a TTS breathes on, and where
                            the pause-tagger puts its pauses — so it is a number
                            worth showing, not an implementation detail. */}
                        {body.split(/\n\s*\n/).filter(p => p.trim()).length} paragraphs
                      </span>
                      <div className="flex-1" />
                      <button className={btnCls} disabled={!body}
                        onClick={() => { void navigator.clipboard.writeText(body);
                          note('copied — paste it into any TTS'); }}>📋 Copy</button>
                      <button className={btnCls}
                        disabled={(draft[c.id] ?? c.narration) === c.narration}
                        onClick={() => void patch(c.id, { narration: body })}>💾 Save</button>
                    </div>
                    {writing === c.id && (
                      <div className="mb-1 border border-amber-900/60 rounded p-2 bg-amber-950/20">
                        <div className="flex items-center gap-2 flex-wrap text-[11px]">
                          <span className="text-amber-300">⏳ {njob?.stage}</span>
                          <span className="text-gray-300">{njob?.detail}</span>
                          <div className="flex-1" />
                          <span className="text-gray-500">
                            🧠 {njob?.provider}/{njob?.model}
                            {njob?.host ? ` · ${njob.host}` : ''}
                          </span>
                          <span className="text-blue-300">
                            ⏱ {Math.round(njob?.elapsed_s || 0)}s
                          </span>
                        </div>
                        {!!njob?.total && (
                          <div className="h-1 bg-gray-800 rounded mt-1 overflow-hidden">
                            <div className="h-full bg-amber-600 transition-all"
                              style={{ width: `${Math.round(100 * (njob.done || 0) / njob.total)}%` }} />
                          </div>
                        )}
                        <div className="text-[10px] text-gray-500 mt-1">
                          {njob?.words_so_far || 0} of ~{njob?.target_words || 0} words ·
                          one model call per beat — the prose appears when every beat is in
                        </div>
                        <div className="max-h-24 overflow-y-auto mt-1 font-mono text-[10px] text-gray-400">
                          {(njob?.log || []).slice(-8).map((l, i) => (
                            <div key={i}><span className="text-gray-600">{l.t}s</span> {l.detail}</div>
                          ))}
                        </div>
                      </div>
                    )}
                    <textarea className={`${inputCls} font-mono text-xs`} rows={14} value={body}
                      placeholder="Write it here, or press ✍ Write narration above."
                      onChange={e => setDraft(d => ({ ...d, [c.id]: e.target.value }))} />
                  </div>

                  {/* beats — the project's timeline chapters */}
                  <div className="border-t border-gray-800 pt-2">
                    <div className="flex items-center gap-2 mb-1 flex-wrap">
                      <span className="text-[11px] font-semibold text-sky-300">🎬 Beats</span>
                      <span className="text-[10px] text-gray-600">
                        a linked project turns these into its timeline chapters, timed against
                        the detected audio sections
                      </span>
                      <div className="flex-1" />
                      <button className={btnCls} disabled={!!busy[`b.${c.id}`] || !c.has_narration}
                        title={c.has_narration ? '' : 'write the narration first'}
                        onClick={() => void splitBeats(c)}>
                        {busy[`b.${c.id}`] ? '⏳ splitting…' : '🎬 Split into beats'}</button>
                    </div>
                    {(c.beats || []).length ? (
                      <ol className="text-[11px] text-gray-400 space-y-0.5 list-decimal ml-5">
                        {c.beats.map(b => (
                          <li key={b.id}>
                            <b className="text-gray-300">{b.title}</b>
                            {b.summary ? ` — ${b.summary}` : ''}
                            {b.mood ? <span className="text-gray-600"> ({b.mood})</span> : null}
                          </li>
                        ))}
                      </ol>
                    ) : (
                      <div className="text-[11px] text-gray-600">
                        No beats yet — ✍ Write narration returns them, or split an
                        existing narration above.
                      </div>
                    )}
                  </div>

                  {/* 🎙 speak it · audition · keep · 🎬 make a project */}
                  <ChapterVoicePanel wid={wid} sid={sid} cid={c.id} note={note}
                    onChanged={() => void load()} />

                  {/* this chapter's own recording */}
                  <div className="border-t border-gray-800 pt-2 space-y-1">
                    <div className="text-[11px] text-gray-500">
                      🎙 <b>This chapter&apos;s</b> recording — written by ✅ Keep above, or
                      uploaded by hand here. A chapter is one video, so its take is the take
                      a linked project pulls; the story&apos;s files are only used when the
                      chapter has none.
                    </div>
                    {(['audio', 'aaf', 'srt'] as const).map(slot => {
                      const f = (c.narration_files || {})[slot];
                      const label = slot === 'audio' ? '🎧 Audio'
                        : slot === 'aaf' ? '🎬 AAF' : '📝 SRT';
                      return (
                        <div key={slot} className="flex gap-2 items-center flex-wrap">
                          <span className="text-[11px] w-20 text-gray-300">{label}</span>
                          <button className={btnCls} disabled={!!busy[`f.${c.id}.${slot}`]}
                            onClick={() => {
                              slotFor.current = { cid: c.id, slot };
                              if (slotRef.current) {
                                slotRef.current.accept = slot === 'audio'
                                  ? '.wav,.mp3,.m4a,.aac,.flac,.ogg,audio/*'
                                  : slot === 'aaf' ? '.aaf' : '.srt,.vtt';
                              }
                              setTimeout(() => slotRef.current?.click(), 0);
                            }}>
                            {busy[`f.${c.id}.${slot}`] ? '⏳' : (f ? '⬆ Replace' : '⬆ Upload')}
                          </button>
                          {f ? (
                            <>
                              <span className="text-[11px] text-gray-400 truncate max-w-xs">
                                {f.filename}
                                {f.seconds ? ` · ${Math.floor(f.seconds / 60)}m ${Math.round(f.seconds % 60)}s` : ''}
                              </span>
                              <a className="text-[11px] text-blue-300"
                                href={`${B}/worlds/${wid}/stories/${sid}/chapters/${c.id}/file/${slot}?download=1`}>⬇</a>
                              <button className={`${btnCls} text-red-300`}
                                onClick={() => {
                                  if (!window.confirm(`Delete this chapter's ${slot} file?`)) return;
                                  void act(() => post(
                                    `/worlds/${wid}/stories/${sid}/chapters/${c.id}/file/${slot}/delete`));
                                }}>🗑</button>
                            </>
                          ) : <span className="text-[11px] text-gray-600">none</span>}
                        </div>
                      );
                    })}
                    {rec && (
                      <audio controls preload="none" className="w-full h-9"
                        src={`${B}/worlds/${wid}/stories/${sid}/chapters/${c.id}/file/audio`} />
                    )}
                  </div>

                  <textarea key={`no${k}`} className={`${inputCls} text-xs`} rows={2}
                    defaultValue={c.notes}
                    placeholder="notes for the writer / the LLM (given to it when it writes this chapter)"
                    onBlur={e => { if (e.target.value !== c.notes)
                      void patch(c.id, { notes: e.target.value }, true); }} />
                </div>
              )}
            </div>
          );
        })}
        {!!arcs.length && (
          <button className={btnCls} onClick={() => void act(() =>
            post(`/worlds/${wid}/stories/${sid}/chapters`,
              { title: `Chapter ${chapters.length + 1}` }))}>
            ＋ Add a chapter by hand</button>
        )}
      </div>

      {!!chapters.length && (
        <div className="text-[11px] text-gray-600 mt-2">
          🔗 To render one: open the project, press 🌍 Story, pick this story <b>and this
          chapter</b>, then ⬇ Pull. The chapter&apos;s narration becomes the script, its
          recording becomes the audio, and its beats become the timeline.
        </div>
      )}
    </div>
  );
}
