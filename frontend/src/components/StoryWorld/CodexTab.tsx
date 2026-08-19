/**
 * 📚 THE CODEX — the world's cheat sheet, and every character's history.
 *
 * His brief: *"a Codex tab to the world that always gets updated when things
 * change so we can almost have a cheat sheet for the world. Maybe also a codex
 * page for characters … to keep track of events and major things that have
 * happened to them for situations where we want to create a continuous series
 * … an option on the story tab to re-calculate codex."*
 *
 * ⭐ **CANON ONLY** (his call). Every entry is derived from something written —
 * a world field, a story, a chapter's narration, a cast sheet, a location — and
 * shows the sources it came from. Nothing here is invented, which is what makes
 * it safe to build a continuing series on.
 *
 * ⭐ **A recalc never eats what you wrote.** ✍ hand-written entries and 📌
 * pinned ones survive every run, and the screen labels them so you can see it
 * is true rather than trusting the docs.
 *
 * ⭐ **It knows when it is behind without asking a model.** The backend hashes
 * the canon; `stale` comes back from a plain GET, so the 🔴 badge costs nothing.
 */
import { useCallback, useEffect, useRef, useState } from 'react';

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

type EntryT = {
  id: string; kind: string; name: string; body: string; tags: string[];
  sources: string[]; story_ids: string[]; manual: boolean; pinned: boolean;
  updated_at: string;
};
type CharRowT = {
  id: string; name: string; role: string; events: number; has_codex: boolean;
  codex: {
    summary?: string; state?: string; state_pinned?: boolean;
    events?: { id: string; title: string; body: string; sources: string[];
               manual?: boolean; pinned?: boolean }[];
    relationships?: { id: string; who: string; body: string; sources: string[] }[];
  };
};
type StaleT = {
  world: boolean; stories: { id: string; title: string }[];
  characters: { id: string; name: string }[]; any: boolean; count: number;
};
type RunT = {
  id: string; at: string; seconds: number; provider: string; model: string;
  host: string; stories_read: number; stories_skipped: number;
  characters_read: number; characters_skipped: number; added: number;
  updated: number; kept: number; total: number; forced: boolean;
  stage_times?: Record<string, number>;
};
type CodexT = {
  entries: EntryT[]; characters: CharRowT[];
  kinds: { key: string; label: string; hint: string }[];
  by_kind: Record<string, number>;
  generated_at: string; updated_at: string; runs: RunT[];
  manual: number; pinned: number; stale: StaleT;
};
type JobT = {
  status?: string; stage?: string; detail?: string; total?: number; done?: number;
  current?: string; elapsed_s?: number; provider?: string; model?: string;
  host?: string; scope?: string; error?: string;
  stage_times?: Record<string, number>;
  skipped?: { stories: string[]; characters: string[] };
  log?: { t: number; stage: string; detail: string }[];
};

const VERBOSE_KEY = 'rbmn_codex_verbose';

export default function CodexTab({ w, llmBody, note }: {
  w: { id: string; stories: { id: string; title: string }[] };
  llmBody: object; note: (m: string) => void;
}) {
  const [cx, setCx] = useState<CodexT | null>(null);
  const [job, setJob] = useState<JobT | null>(null);
  const [busy, setBusy] = useState(false);
  const [filter, setFilter] = useState('');
  const [kind, setKind] = useState('');
  const [scope, setScope] = useState('');       // '' = whole world
  const [force, setForce] = useState(false);
  const [view, setView] = useState<'world' | 'characters' | 'runs'>('world');
  const [openChar, setOpenChar] = useState('');
  const [editing, setEditing] = useState<Partial<EntryT> | null>(null);
  const [verbose, setVerbose] = useState(() => {
    try { return localStorage.getItem(VERBOSE_KEY) === '1'; } catch { return false; }
  });
  const timer = useRef<number | null>(null);

  const load = useCallback(async () => {
    try { setCx(await get<CodexT>(`/worlds/${w.id}/codex`)); }
    catch (e) { note(`⚠ ${e}`); }
  }, [w.id, note]);
  useEffect(() => { void load(); }, [load]);

  // ── live status. ⚠ The poll must keep running while `starting` too: a job
  // that has not reached `running` yet is exactly the moment the screen looks
  // most broken.
  const poll = useCallback(async () => {
    try {
      const r = await get<{ job: JobT }>(`/worlds/${w.id}/codex/job`);
      setJob(r.job);
      if (r.job?.status === 'starting' || r.job?.status === 'running') return true;
      return false;
    } catch { return false; }
  }, [w.id]);
  useEffect(() => {
    if (!busy) return;
    timer.current = window.setInterval(async () => {
      const alive = await poll();
      if (!alive) { setBusy(false); void load(); }
    }, 1500);
    return () => { if (timer.current) window.clearInterval(timer.current); };
  }, [busy, poll, load]);
  // ⚠⚠ ADOPT A RUN THAT WAS ALREADY GOING. `busy` used to be set only inside
  // recalc(), so arriving here from the Story tab's 📚 button — which tells you
  // to "watch it on the 📚 Codex tab" — painted "⏳ scan · 0/0 · 0s" and then
  // froze forever, and the finished entries never loaded. Same on any reload
  // mid-run. A status screen that stops updating is worse than none.
  useEffect(() => { void poll().then(alive => { if (alive) setBusy(true); }); }, [poll]);

  const recalc = async () => {
    try {
      const r = await post<{ provider: string; model: string; host: string }>(
        `/worlds/${w.id}/codex/recalc`,
        { force, story_id: scope, do_world: true, do_characters: true, ...llmBody });
      note(`♻ recalculating on ${r.provider}/${r.model} (${r.host})`);
      setBusy(true); void poll();
    } catch (e) { note(`⚠ ${e}`); }
  };

  const entries = (cx?.entries || []).filter(e => {
    if (kind && e.kind !== kind) return false;
    if (!filter.trim()) return true;
    const q = filter.toLowerCase();
    return e.name.toLowerCase().includes(q) || e.body.toLowerCase().includes(q)
      || (e.tags || []).some(t => t.toLowerCase().includes(q));
  });
  const stale = cx?.stale;
  const running = job?.status === 'starting' || job?.status === 'running';
  const pct = job?.total ? Math.round(100 * (job.done || 0) / job.total) : 0;

  return (
    <div className="space-y-3">
      {/* ── the recalc bar ────────────────────────────────────────────── */}
      <div className="border border-gray-800 rounded p-3">
        <div className="flex items-center gap-2 flex-wrap">
          <span className="text-sm font-semibold text-violet-300">📚 Codex</span>
          <span className="text-[11px] text-gray-500">
            the cheat sheet for this world — <b>only what is actually written</b> in your
            stories, chapters, cast and locations. Every entry shows where it came from.
          </span>
          <div className="flex-1" />
          {stale?.any ? (
            <span className="text-[11px] text-amber-400"
              title={[stale.world ? 'the world sheet' : '',
                ...stale.stories.map(s => s.title),
                ...stale.characters.map(c => c.name)].filter(Boolean).join(' · ')}>
              🔴 {stale.count} thing{stale.count === 1 ? '' : 's'} changed since the last recalc
            </span>
          ) : cx?.generated_at ? (
            <span className="text-[11px] text-emerald-400">✅ up to date</span>
          ) : null}
        </div>

        <div className="flex gap-2 items-end flex-wrap mt-2">
          <div>
            <label className="text-[10px] text-gray-500 block">scope</label>
            <select className={`${inputCls} w-56`} value={scope}
              onChange={e => setScope(e.target.value)}>
              <option value="">the whole world</option>
              {w.stories.map(s => (
                <option key={s.id} value={s.id}>just “{s.title}”</option>
              ))}
            </select>
          </div>
          <label className="flex items-center gap-1 text-[11px] text-gray-400 pb-2">
            <input type="checkbox" checked={force} onChange={e => setForce(e.target.checked)} />
            re-read everything
          </label>
          {/* disabled on `busy` too — `running` lags by one poll, and that
              window is exactly where the double-submit lived */}
          <button className={btnAmber} disabled={running || busy}
            onClick={() => void recalc()}>
            {running || busy ? '⏳ recalculating…' : '♻ Re-calculate codex'}</button>
          {running && (
            <button className={`${btnCls} text-red-300`}
              onClick={async () => { await post(`/worlds/${w.id}/codex/cancel`); }}>
              ⏹ Cancel</button>
          )}
          <label className="flex items-center gap-1 text-[11px] text-gray-500 pb-2 ml-auto">
            <input type="checkbox" checked={verbose}
              onChange={e => { setVerbose(e.target.checked);
                try { localStorage.setItem(VERBOSE_KEY, e.target.checked ? '1' : '0'); }
                catch { /* ok */ } }} />
            🔍 verbose
          </label>
        </div>
        <div className="text-[11px] text-gray-600 mt-1">
          Unchanged stories and characters are <b>skipped without an LLM call</b> — leave
          &quot;re-read everything&quot; off unless you have edited the prompts themselves.
        </div>

        {/* ── live status ─────────────────────────────────────────────── */}
        {job && job.status && job.status !== 'idle' && (
          <div className="mt-2 border-t border-gray-800 pt-2 text-xs">
            <div className="flex items-center gap-2 flex-wrap">
              <span className={
                job.status === 'done' ? 'text-emerald-400'
                  : job.status === 'error' ? 'text-red-400'
                    : job.status === 'cancelled' ? 'text-gray-400' : 'text-amber-300'}>
                {job.status === 'done' ? '✅' : job.status === 'error' ? '⚠'
                  : job.status === 'cancelled' ? '⏹' : '⏳'} {job.stage || job.status}
              </span>
              <span className="text-gray-300">{job.detail || ''}</span>
              <div className="flex-1" />
              {/* ⭐ WHERE it ran, recorded — the standing rule. */}
              <span className="text-gray-500">
                🧠 {job.provider}/{job.model}{job.host ? ` · ${job.host}` : ''}
              </span>
              {!!job.total && (
                <span className="text-gray-400">{job.done}/{job.total} ({pct}%)</span>
              )}
              <span className={running ? 'text-blue-300' : 'text-gray-500'}>
                ⏱ {job.elapsed_s ?? 0}s
              </span>
            </div>
            {!!job.total && (
              <div className="h-1 bg-gray-800 rounded mt-1 overflow-hidden">
                <div className="h-full bg-amber-600 transition-all" style={{ width: `${pct}%` }} />
              </div>
            )}
            {job.error && <div className="text-red-400 mt-1">⚠ {job.error}</div>}
            {verbose && (
              <div className="mt-2 space-y-1">
                {!!job.skipped && (
                  <div className="text-[11px] text-gray-500">
                    skipped as unchanged — stories: {job.skipped.stories.join(', ') || 'none'}
                    {' · '}characters: {job.skipped.characters.join(', ') || 'none'}
                  </div>
                )}
                {!!job.stage_times && !!Object.keys(job.stage_times).length && (
                  <div className="text-[11px] text-gray-500">
                    {Object.entries(job.stage_times)
                      .map(([k, v]) => `${k} ${v}s`).join(' · ')}
                  </div>
                )}
                <div className="max-h-48 overflow-y-auto border border-gray-800 rounded p-1
                                font-mono text-[10px] text-gray-400 space-y-0.5">
                  {(job.log || []).map((l, i) => (
                    <div key={i}>
                      <span className="text-gray-600">{l.t}s</span>{' '}
                      <span className="text-gray-600">[{l.stage}]</span> {l.detail}
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        )}
      </div>

      {/* ── view switch ──────────────────────────────────────────────── */}
      <div className="flex gap-1 flex-wrap items-center">
        {([['world', `🌍 World (${cx?.entries.length || 0})`],
           ['characters', `🎭 Characters (${cx?.characters.filter(c => c.has_codex).length || 0})`],
           ['runs', `⏱ Runs (${cx?.runs.length || 0})`]] as const).map(([k, label]) => (
          <button key={k}
            className={`px-3 py-1 rounded text-xs ${view === k
              ? 'bg-gray-800 text-amber-300' : 'text-gray-400 hover:text-gray-200'}`}
            onClick={() => setView(k)}>{label}</button>
        ))}
        <div className="flex-1" />
        {!!cx && (cx.manual + cx.pinned > 0) && (
          <span className="text-[11px] text-gray-500">
            ✍ {cx.manual} yours · 📌 {cx.pinned} pinned — a recalc keeps all of them
          </span>
        )}
      </div>

      {/* ── world entries ────────────────────────────────────────────── */}
      {view === 'world' && (
        <div className="space-y-2">
          <div className="flex gap-2 flex-wrap items-center">
            <input className={`${inputCls} flex-1 min-w-[12rem]`} value={filter}
              placeholder="search the codex…" onChange={e => setFilter(e.target.value)} />
            <select className={`${inputCls} w-40`} value={kind}
              onChange={e => setKind(e.target.value)}>
              <option value="">every kind</option>
              {(cx?.kinds || []).map(k => (
                <option key={k.key} value={k.key}>
                  {k.label}{cx?.by_kind[k.key] ? ` (${cx.by_kind[k.key]})` : ''}
                </option>
              ))}
            </select>
            <button className={btnCls} onClick={() => setEditing({ kind: 'concept' })}>
              ✍ Write an entry</button>
          </div>

          {!entries.length && (
            <div className="text-xs text-gray-600 p-4 border border-gray-800 rounded">
              {cx?.entries.length
                ? 'Nothing matches that filter.'
                : 'The codex is empty. Press ♻ Re-calculate — it reads your world sheet, '
                  + 'stories, chapters, cast and locations and writes down what they '
                  + 'establish. It never invents anything, so a thin world gives a thin codex.'}
            </div>
          )}

          {entries.map(e => (
            <div key={e.id} className="border border-gray-800 rounded p-2 text-xs">
              <div className="flex items-center gap-2 flex-wrap">
                <span className="text-[10px] px-1.5 py-0.5 rounded bg-gray-800 text-violet-300">
                  {e.kind}
                </span>
                <span className="font-semibold text-sm text-gray-100">{e.name}</span>
                {e.manual && (
                  <span className="text-[10px] text-emerald-400" title="you wrote this — a recalc never touches it">
                    ✍ yours</span>
                )}
                {!e.manual && e.pinned && (
                  <span className="text-[10px] text-amber-400" title="kept — a recalc will not rewrite it">
                    📌 pinned</span>
                )}
                <div className="flex-1" />
                {!e.manual && (
                  <button className={btnCls} title={e.pinned ? 'let recalc rewrite it' : 'keep this wording'}
                    onClick={async () => {
                      await post(`/worlds/${w.id}/codex/entry/${e.id}/pin?pinned=${!e.pinned}`);
                      void load();
                    }}>{e.pinned ? '📌 Unpin' : '📌 Pin'}</button>
                )}
                <button className={btnCls} onClick={() => setEditing(e)}>✎</button>
                <button className={`${btnCls} text-red-300`}
                  onClick={async () => {
                    if (!window.confirm(`Delete codex entry "${e.name}"?`)) return;
                    await post(`/worlds/${w.id}/codex/entry/${e.id}/delete`);
                    void load();
                  }}>🗑</button>
              </div>
              <div className="text-gray-300 mt-1 whitespace-pre-wrap">{e.body}</div>
              {!!(e.tags || []).length && (
                <div className="text-[10px] text-gray-600 mt-1">{e.tags.join(' · ')}</div>
              )}
              {!!(e.sources || []).length && (
                <details className="mt-1">
                  <summary className="text-[10px] text-gray-600 cursor-pointer">
                    📎 {e.sources.length} source{e.sources.length === 1 ? '' : 's'}
                  </summary>
                  <ul className="text-[10px] text-gray-500 ml-4 list-disc mt-0.5">
                    {e.sources.map((s, i) => <li key={i}>{s}</li>)}
                  </ul>
                </details>
              )}
            </div>
          ))}
        </div>
      )}

      {/* ── character pages ──────────────────────────────────────────── */}
      {view === 'characters' && (
        <div className="space-y-2">
          <div className="text-[11px] text-gray-500">
            What has happened <b>to</b> each character, in story order — the thing a sequel
            starts from. Hand-written events are kept through every recalc.
          </div>
          {!(cx?.characters || []).length && (
            <div className="text-xs text-gray-600">This world has no cast yet.</div>
          )}
          {(cx?.characters || []).map(c => {
            const isOpen = openChar === c.id;
            const k = c.codex || {};
            return (
              <div key={c.id} className="border border-gray-800 rounded">
                <button className="w-full text-left px-2 py-1.5 flex items-center gap-2 flex-wrap"
                  onClick={() => setOpenChar(isOpen ? '' : c.id)}>
                  <span className="text-sm font-semibold">{isOpen ? '▾' : '▸'} {c.name}</span>
                  {c.role && <span className="text-[10px] text-gray-500">{c.role}</span>}
                  {c.has_codex
                    ? <span className="text-[10px] text-sky-400">📚 {c.events} event(s)</span>
                    : <span className="text-[10px] text-gray-600">no codex page yet</span>}
                  {k.state_pinned && <span className="text-[10px] text-amber-400">📌 state pinned</span>}
                </button>
                {isOpen && (
                  <div className="border-t border-gray-800 p-2 text-xs space-y-2">
                    {k.summary && (
                      <div><span className="text-gray-500">Who they are — </span>
                        <span className="text-gray-300">{k.summary}</span></div>
                    )}
                    {k.state && (
                      <div className="border border-amber-900/60 rounded p-2 bg-amber-950/20">
                        <div className="text-[10px] text-amber-300 font-semibold mb-0.5">
                          WHERE THEY STAND NOW — a sequel starts here
                        </div>
                        <div className="text-gray-200">{k.state}</div>
                        <button className={`${btnCls} mt-1`}
                          onClick={async () => {
                            await post(`/worlds/${w.id}/codex/character/${c.id}/pin-state`
                              + `?pinned=${!k.state_pinned}`);
                            void load();
                          }}>{k.state_pinned ? '📌 Unpin state' : '📌 Pin state'}</button>
                      </div>
                    )}
                    {!!(k.events || []).length && (
                      <div>
                        <div className="text-[11px] font-semibold text-sky-300 mb-0.5">
                          What happened to them
                        </div>
                        <ol className="space-y-1 list-decimal ml-5">
                          {(k.events || []).map(ev => (
                            <li key={ev.id}>
                              <b className="text-gray-200">{ev.title}</b>
                              {ev.manual && <span className="text-[10px] text-emerald-400"> ✍ yours</span>}
                              <div className="text-gray-400">{ev.body}</div>
                              {!!(ev.sources || []).length && (
                                <div className="text-[10px] text-gray-600">
                                  📎 {ev.sources.join(' · ')}
                                </div>
                              )}
                              <button className="text-[10px] text-red-300 hover:text-red-200"
                                onClick={async () => {
                                  if (!window.confirm(`Delete event "${ev.title}"?`)) return;
                                  await post(`/worlds/${w.id}/codex/character/${c.id}/event/${ev.id}/delete`);
                                  void load();
                                }}>🗑 remove</button>
                            </li>
                          ))}
                        </ol>
                      </div>
                    )}
                    {!!(k.relationships || []).length && (
                      <div>
                        <div className="text-[11px] font-semibold text-sky-300 mb-0.5">Ties</div>
                        <ul className="ml-4 list-disc space-y-0.5">
                          {(k.relationships || []).map(r => (
                            <li key={r.id}>
                              <b className="text-gray-200">{r.who}</b>
                              <span className="text-gray-400"> — {r.body}</span>
                            </li>
                          ))}
                        </ul>
                      </div>
                    )}
                    {!c.has_codex && (
                      <div className="text-gray-600">
                        Nothing recorded yet. Press ♻ Re-calculate above — it reads the stories
                        this character is in and writes down only what they actually say
                        happened to them.
                      </div>
                    )}
                    <ManualEvent wid={w.id} cid={c.id} onDone={load} note={note} />
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}

      {/* ── the run record (benchmarking data) ───────────────────────── */}
      {view === 'runs' && (
        <div className="space-y-1 text-xs">
          <div className="text-[11px] text-gray-500">
            Every recalc is kept: how long it took, on which brain and host, and what changed.
          </div>
          {!(cx?.runs || []).length && <div className="text-gray-600">No runs yet.</div>}
          {(cx?.runs || []).map(r => (
            <div key={r.id} className="border border-gray-800 rounded p-2">
              <div className="flex gap-2 flex-wrap items-center">
                <span className="text-gray-300">{new Date(r.at).toLocaleString()}</span>
                <span className="text-blue-300">⏱ {r.seconds}s</span>
                <span className="text-gray-500">🧠 {r.provider}/{r.model}</span>
                {r.host && <span className="text-gray-600">{r.host}</span>}
                {r.forced && <span className="text-amber-400">forced</span>}
              </div>
              <div className="text-gray-500 mt-0.5">
                read {r.stories_read} story(ies) + {r.characters_read} character(s) ·
                skipped {r.stories_skipped}+{r.characters_skipped} unchanged ·
                <span className="text-emerald-400"> +{r.added} new</span>,
                <span className="text-sky-400"> {r.updated} updated</span>,
                <span className="text-amber-400"> {r.kept} of yours kept</span> ·
                {r.total} entries total
              </div>
              {!!r.stage_times && !!Object.keys(r.stage_times).length && (
                <div className="text-[10px] text-gray-600 mt-0.5">
                  {Object.entries(r.stage_times).map(([k, v]) => `${k} ${v}s`).join(' · ')}
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      {editing && (
        <EntryModal wid={w.id} entry={editing} kinds={cx?.kinds || []}
          onClose={() => setEditing(null)}
          onDone={() => { setEditing(null); void load(); }} note={note} />
      )}
    </div>
  );
}

/** ✍ a hand-written event on a character's page — `manual`, so it survives. */
function ManualEvent({ wid, cid, onDone, note }: {
  wid: string; cid: string; onDone: () => void; note: (m: string) => void;
}) {
  const [t, setT] = useState('');
  const [b, setB] = useState('');
  return (
    <div className="border-t border-gray-800 pt-2 flex gap-1 flex-wrap items-start">
      <input className={`${inputCls} w-48`} value={t} placeholder="add an event…"
        onChange={e => setT(e.target.value)} />
      <input className={`${inputCls} flex-1 min-w-[10rem]`} value={b}
        placeholder="what happened" onChange={e => setB(e.target.value)} />
      <button className={btnCls} disabled={!t.trim() || !b.trim()}
        onClick={async () => {
          try {
            await post(`/worlds/${wid}/codex/character/${cid}/event`,
              { title: t, body: b });
            setT(''); setB(''); onDone();
          } catch (e) { note(`⚠ ${e}`); }
        }}>＋ Add</button>
    </div>
  );
}

function EntryModal({ wid, entry, kinds, onClose, onDone, note }: {
  wid: string; entry: Partial<EntryT>;
  kinds: { key: string; label: string; hint: string }[];
  onClose: () => void; onDone: () => void; note: (m: string) => void;
}) {
  const [f, setF] = useState({
    id: entry.id || '', kind: entry.kind || 'concept', name: entry.name || '',
    body: entry.body || '', tags: (entry.tags || []).join(', '),
    sources: (entry.sources || []).join('\n'),
  });
  return (
    <div className="fixed inset-0 bg-black/70 flex items-center justify-center z-50 p-4"
      onClick={onClose}>
      <div className="bg-gray-950 border border-gray-700 rounded p-4 w-full max-w-lg space-y-2"
        onClick={e => e.stopPropagation()}>
        <div className="text-sm font-semibold text-violet-300">
          {f.id ? '✎ Edit codex entry' : '✍ Write a codex entry'}
        </div>
        <div className="text-[11px] text-gray-500">
          Anything you write here is <b>yours</b> — a recalc will never rewrite or delete it.
        </div>
        <div className="flex gap-1">
          <select className={`${inputCls} w-40`} value={f.kind}
            onChange={e => setF({ ...f, kind: e.target.value })}>
            {kinds.map(k => <option key={k.key} value={k.key}>{k.label}</option>)}
          </select>
          <input className={inputCls} value={f.name} placeholder="name"
            onChange={e => setF({ ...f, name: e.target.value })} />
        </div>
        <textarea className={inputCls} rows={5} value={f.body} placeholder="what it is"
          onChange={e => setF({ ...f, body: e.target.value })} />
        <input className={inputCls} value={f.tags} placeholder="tags, comma separated"
          onChange={e => setF({ ...f, tags: e.target.value })} />
        <textarea className={`${inputCls} text-xs`} rows={2} value={f.sources}
          placeholder="where this comes from (one per line) — optional"
          onChange={e => setF({ ...f, sources: e.target.value })} />
        <div className="flex gap-2 justify-end">
          <button className={btnCls} onClick={onClose}>Cancel</button>
          <button className={btnAmber} disabled={!f.name.trim() || !f.body.trim()}
            onClick={async () => {
              try {
                await post(`/worlds/${wid}/codex/entry`, {
                  id: f.id, kind: f.kind, name: f.name, body: f.body,
                  tags: f.tags.split(',').map(x => x.trim()).filter(Boolean),
                  sources: f.sources.split('\n').map(x => x.trim()).filter(Boolean),
                });
                onDone();
              } catch (e) { note(`⚠ ${e}`); }
            }}>💾 Save</button>
        </div>
      </div>
    </div>
  );
}
