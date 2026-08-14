/**
 * ⚡ Autogen board — what is queued, what is running, how long it has taken.
 *
 * A batch you cannot watch is a batch you cannot trust. Everything here is read
 * from the job state FILES, so it survives a backend restart and tells you the
 * truth after one: a run interrupted mid-dataset comes back on the queue at the
 * stage it reached, and this is where you see that happen.
 *
 * TWO LEVELS OF DETAIL, because they answer different questions.
 *   compact — "is it moving, and roughly where is it": one line per job.
 *   verbose — "what exactly is it doing and what has it already done": the
 *             stage chain with a duration on every completed stage, the full
 *             timestamped log, the workers it used, and what it produced.
 * The toggle persists, because whichever one you want you will want it again.
 *
 * ⚠ ELAPSED TIME COMES FROM THE SERVER. A client-side stopwatch is wrong the
 * moment you reload, close the tab, or the backend restarts — and this pipeline
 * runs for hours precisely when you are not watching. The server sends
 * `elapsed_s`; this only animates BETWEEN polls so the clock doesn't visibly
 * stutter, and re-syncs on every poll.
 */
import React, { useCallback, useEffect, useState } from 'react';

const BASE = '/api/autogen';
const VERBOSE_KEY = 'rbmn_autogen_verbose';

interface LogT { at?: string; t?: number; stage?: string; detail?: string; tick?: boolean }
interface JobT {
  id: string; batch?: string; label?: string; name?: string; slug?: string;
  stage?: string; detail?: string; error?: string | null;
  created_at?: string; updated_at?: string;
  dataset?: string | null; trigger?: string | null; installed?: string | null;
  completed?: string[]; active?: boolean; queued?: boolean;
  estimate?: { renders?: number; human?: string };
  elapsed_s?: number; stage_times?: Record<string, number>;
  stage_started_at?: string; log_lines?: number;
  best_score?: number | null; installed_epoch?: number | null;
  best_epoch?: number | null; install_note?: string | null;
}
interface JobFullT extends JobT {
  log?: LogT[]; log_total?: number; stage_elapsed_s?: number;
  workers_used?: string[];
  installed_epoch?: number | null; best_epoch?: number | null;
  install_note?: string | null; best_score?: number | null; epochs_scored?: number;
  spec?: Record<string, unknown>;
  gate?: { checked?: number; failed?: number; bad?: Array<{ view?: string; why?: string }> };
  candidates?: Array<{ image_id?: string; score?: number; why?: string }>;
  costumes?: Array<{ name?: string; costume_id?: string }>;
  views_job?: Record<string, unknown>;
  charsheet?: { url?: string; missing?: string[] };
  dataset_flags?: Record<string, number>;
  refs?: Array<{ id?: string; tag?: string }>;
}

const card: React.CSSProperties = {
  background: '#12151b', border: '1px solid #2a2f3a', borderRadius: 10, padding: 12,
};
const btnSm: React.CSSProperties = {
  background: 'transparent', border: '1px solid #2a2f3a', borderRadius: 6,
  color: '#cbd2dc', padding: '3px 8px', fontSize: 12, cursor: 'pointer',
};
const hint: React.CSSProperties = { color: '#8d97a5', fontSize: 12 };
const mono: React.CSSProperties = {
  fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace', fontSize: 11,
};

const STAGE_ORDER = ['character', 'base', 'views', 'gate', 'clothing',
  'dataset', 'charsheet', 'lora'];
const TERMINAL = ['done', 'error', 'cancelled'];

const colour = (stage?: string): string =>
  stage === 'error' ? '#ff8a8a'
    : stage === 'done' ? '#5ee08a'
      : stage === 'cancelled' ? '#c9a227'
        : stage === 'queued' ? '#8d97a5' : '#9cc2ff';

/** Durations read at a glance: seconds under a minute, then m/h. */
const dur = (s?: number): string => {
  if (s === undefined || s === null) return '';
  if (s < 60) return `${Math.round(s)}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m ${Math.round(s % 60)}s`;
  return `${Math.floor(s / 3600)}h ${Math.round((s % 3600) / 60)}m`;
};

function Detail({ jid }: { jid: string }): React.ReactElement {
  const [j, setJ] = useState<JobFullT | null>(null);
  const [all, setAll] = useState(false);

  useEffect(() => {
    let stop = false;
    const load = async () => {
      try {
        const r = await fetch(`${BASE}/jobs/${jid}?log=${all ? -1 : 60}`);
        if (r.ok && !stop) setJ(await r.json());
      } catch { /* a failed poll is not worth a message */ }
    };
    void load();
    const t = window.setInterval(load, 4000);
    return () => { stop = true; window.clearInterval(t); };
  }, [jid, all]);

  if (!j) return <div style={{ ...hint, padding: '6px 0' }}>loading…</div>;
  const times = j.stage_times || {};
  const log = j.log || [];

  const row = (k: string, v: React.ReactNode) => (
    <div style={{ display: 'flex', gap: 8, fontSize: 12 }}>
      <span style={{ width: 96, color: '#8d97a5', flexShrink: 0 }}>{k}</span>
      <span style={{ color: '#cbd2dc', wordBreak: 'break-word' }}>{v}</span>
    </div>
  );

  return (
    <div style={{ marginTop: 8, paddingTop: 8, borderTop: '1px dashed #2a2f3a' }}>
      {/* the chain, with a duration on everything that finished */}
      <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap', marginBottom: 8 }}>
        {STAGE_ORDER.map((s) => {
          const done = (j.completed || []).includes(s);
          const now = s === j.stage;
          if (!done && !now) return null;
          const t = now && j.active ? j.stage_elapsed_s : times[s];
          return (
            <span key={s} style={{
              fontSize: 10, padding: '2px 7px', borderRadius: 999,
              border: `1px solid ${done ? '#2f6b45' : '#3b82f6'}`,
              color: done ? '#5ee08a' : '#9cc2ff',
            }}>
              {done ? '✓' : '⏳'} {s}{t !== undefined ? ` · ${dur(t)}` : ''}
            </span>
          );
        })}
      </div>

      <div style={{ display: 'flex', flexDirection: 'column', gap: 2, marginBottom: 8 }}>
        {j.slug && row('character', j.slug)}
        {j.dataset && row('dataset', <span style={mono}>{j.dataset}</span>)}
        {j.trigger && row('trigger', <span style={mono}>{j.trigger}</span>)}
        {j.installed && row('LoRA', <span style={{ color: '#5ee08a' }}>{j.installed}</span>)}
        {/* ⚠ v1.276.52 — the EPOCH STORY, not just the filename. The one thing
            worth knowing about a trained LoRA is whether the epoch you got is
            the epoch that scored best; `install_note` appears ONLY when they
            differ, so its absence is the good news. */}
        {j.best_score !== undefined && j.best_score !== null && row('likeness',
          <>
            <b style={{ color: (j.best_score || 0) >= 0.45 ? '#5ee08a' : '#c9a227' }}>
              {Number(j.best_score).toFixed(4)}
            </b>
            {j.epochs_scored ? <span style={hint}>  · best of {j.epochs_scored} epochs</span> : null}
            {(j.best_score || 0) < 0.45
              ? <span style={hint}>  · below the 0.45 match band</span> : null}
          </>)}
        {j.installed_epoch !== undefined && j.installed_epoch !== null && row('epoch',
          j.installed_epoch === j.best_epoch
            ? <span>{j.installed_epoch} <span style={hint}>(the best-scoring epoch)</span></span>
            : <span style={{ color: '#c9a227' }}>
                {j.installed_epoch} — substituted, epoch {j.best_epoch} scored best
              </span>)}
        {j.install_note && row('note',
          <span style={{ color: '#c9a227' }}>{j.install_note}</span>)}
        {j.gate && row('base gate',
          `${j.gate.checked ?? '?'} checked, ${j.gate.failed ?? 0} failed`
          + (j.gate.bad?.length ? ` — ${j.gate.bad.map((b) => `${b.view}: ${b.why}`).join('; ')}` : ''))}
        {!!j.candidates?.length && row('candidates',
          j.candidates.map((c) => `${String(c.image_id).slice(0, 6)} ${c.score}`).join(' · '))}
        {!!j.costumes?.length && row('outfits', j.costumes.map((c) => c.name).join(', '))}
        {j.charsheet?.url && row('sheet',
          <a href={j.charsheet.url} target="_blank" rel="noreferrer"
             style={{ color: '#9cc2ff' }}>open</a>)}
        {j.dataset_flags && row('QC flags',
          `${j.dataset_flags.flagged ?? 0} flagged of ${j.dataset_flags.checked ?? 0}`)}
        {j.estimate?.renders !== undefined && row('estimated',
          `${j.estimate.renders} renders / ${j.estimate.human}`)}
        {/* v1.277.1 — WHERE it rendered, persisted on the job state so a
            finished run still answers it (benchmarking data, not just live) */}
        {!!j.workers_used?.length && row('workers',
          <span style={mono}>
            {j.workers_used.map((h) => h.replace(/^https?:\/\//, '')).join(' · ')}
          </span>)}
      </div>

      {/* the log — the actual answer to "what is it doing" */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
        <span style={{ ...hint, color: '#cbd2dc' }}>log</span>
        <span style={hint}>{j.log_total ?? log.length} entries</span>
        <div style={{ flex: 1 }} />
        {(j.log_total || 0) > log.length && (
          <button style={btnSm} onClick={() => setAll(true)}>show all</button>
        )}
      </div>
      <div style={{
        maxHeight: 240, overflow: 'auto', background: '#0b0e13',
        border: '1px solid #2a2f3a', borderRadius: 6, padding: '6px 8px',
      }}>
        {log.length === 0 && <div style={hint}>nothing logged yet</div>}
        {log.slice().reverse().map((l, i) => (
          <div key={i} style={{ ...mono, display: 'flex', gap: 8, lineHeight: 1.6 }}>
            <span style={{ color: '#5b6472', flexShrink: 0, width: 52, textAlign: 'right' }}>
              {l.t !== undefined ? dur(l.t) : ''}
            </span>
            {/* ticks are progress within a stage; transitions are the stage
                itself — dimmer vs coloured so the shape of the run reads at a
                glance instead of being a wall of identical lines */}
            <span style={{ color: l.tick ? '#5b6472' : colour(l.stage),
                           flexShrink: 0, width: 78 }}>
              {l.stage}
            </span>
            <span style={{ color: l.tick ? '#8d97a5' : '#cbd2dc' }}>{l.detail}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

export default function AutogenBoard({ compact }: { compact?: boolean }): React.ReactElement | null {
  const [jobs, setJobs] = useState<JobT[]>([]);
  const [queue, setQueue] = useState<string[]>([]);
  const [paused, setPaused] = useState(false);
  const [msg, setMsg] = useState('');
  const [open, setOpen] = useState<Record<string, boolean>>({});
  const [verbose, setVerbose] = useState<boolean>(() => {
    try { return window.localStorage.getItem(VERBOSE_KEY) === '1'; } catch { return false; }
  });
  // ⚠ animates the clock BETWEEN polls only; every poll re-syncs to the
  // server's number, so a paused tab or a restart cannot drift it.
  const [tick, setTick] = useState(0);
  const [syncedAt, setSyncedAt] = useState(() => Date.now());

  const load = useCallback(async () => {
    try {
      const r = await fetch(`${BASE}/jobs?limit=60`);
      if (!r.ok) return;
      const j = await r.json();
      setJobs(j.jobs || []);
      setQueue(j.queue || []);
      setPaused(!!j.paused);
      setSyncedAt(Date.now());
    } catch { /* a failed poll is not worth a message */ }
  }, []);

  useEffect(() => {
    void load();
    const t = window.setInterval(load, 4000);
    return () => window.clearInterval(t);
  }, [load]);

  useEffect(() => {
    const t = window.setInterval(() => setTick((n) => n + 1), 1000);
    return () => window.clearInterval(t);
  }, []);

  const setV = (v: boolean) => {
    setVerbose(v);
    try { window.localStorage.setItem(VERBOSE_KEY, v ? '1' : '0'); } catch { /* fine */ }
  };

  const act = async (jid: string, what: 'cancel' | 'retry' | 'delete') => {
    setMsg('');
    try {
      const r = await fetch(`${BASE}/jobs/${jid}/${what}`, { method: 'POST' });
      const raw = await r.text();
      let detail = raw;
      try { const p = JSON.parse(raw); detail = p?.detail || p?.note || raw; } catch { /* text */ }
      if (!r.ok) setMsg(`❌ ${detail}`);
      else if (detail && what !== 'delete') setMsg(String(detail));
      void load();
    } catch (e) { setMsg(`❌ ${String((e as Error).message || e)}`); }
  };

  if (!jobs.length) return compact ? null : (
    <div style={card}>
      <b style={{ fontSize: 13, color: '#e6e9ee' }}>⚡ Autogen</b>
      <p style={{ ...hint, margin: '6px 0 0' }}>
        Nothing queued. Use ＋ New Character → ⚡ Autogen to build one from a
        description or photos.
      </p>
    </div>
  );

  const running = jobs.filter((j) => j.active);
  // seconds since the last successful poll — added to the server's elapsed so
  // the clock moves smoothly, then discarded on the next sync. `tick` is only
  // here to force the re-render; the value it produces is never trusted.
  void tick;
  const since = (Date.now() - syncedAt) / 1000;

  return (
    <div style={card}>
      <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
        <b style={{ fontSize: 13, color: '#e6e9ee' }}>⚡ Autogen</b>
        <span style={hint}>
          {running.length ? `${running.length} running` : 'idle'}
          {queue.length ? ` · ${queue.length} waiting` : ''}
        </span>
        {paused && (
          <span style={{ fontSize: 11, padding: '2px 8px', borderRadius: 999,
                         border: '1px solid #c9a227', color: '#c9a227' }}
                title="the running job finishes; nothing new starts until you resume">
            ⏸ paused{running.length ? ' — finishing the current job' : ''}
          </span>
        )}
        <div style={{ flex: 1 }} />
        {/* ⏸ v1.277.2 — survives a restart: pause, reboot the app, come back,
            resume — the batch is exactly where you left it */}
        <button style={{ ...btnSm, borderColor: paused ? '#2f6b45' : '#c9a227',
                         color: paused ? '#5ee08a' : '#c9a227' }}
          title={paused
            ? 'let the queue continue'
            : 'finish the current job, then hold everything — safe across a reboot'}
          onClick={() => void (async () => {
            await fetch(`${BASE}/queue/pause`, {
              method: 'POST', headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ paused: !paused }),
            });
            void load();
          })()}>
          {paused ? '▶ resume queue' : '⏸ pause queue'}
        </button>
        <label style={{ ...hint, display: 'flex', gap: 5, alignItems: 'center', cursor: 'pointer' }}
               title="Show the stage chain with timings, the full log, and everything the run produced">
          <input type="checkbox" checked={verbose} onChange={(e) => setV(e.target.checked)} />
          🔍 verbose
        </label>
        {queue.length > 0 && (
          <button style={btnSm} onClick={() => void (async () => {
            await fetch(`${BASE}/queue/clear`, { method: 'POST' }); void load();
          })()}>clear the queue</button>
        )}
      </div>
      {msg && <p style={{ ...hint, color: msg.startsWith('❌') ? '#ff8a8a' : '#9cc2ff' }}>{msg}</p>}

      <div style={{ marginTop: 8, display: 'flex', flexDirection: 'column', gap: 6 }}>
        {jobs.slice(0, compact ? 4 : 60).map((j) => {
          const done = new Set(j.completed || []);
          const terminal = TERMINAL.includes(String(j.stage));
          // live clock: server value + the seconds since we last synced
          const live = (j.elapsed_s || 0) + (j.active ? since : 0);
          const show = verbose || open[j.id];
          return (
            <div key={j.id} style={{
              border: '1px solid #2a2f3a', borderRadius: 8, padding: '7px 9px',
              background: j.active ? 'rgba(59,130,246,0.07)' : 'transparent',
            }}>
              <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
                <button style={{ ...btnSm, border: 'none', padding: '0 4px' }}
                        title={show ? 'collapse' : 'expand'}
                        onClick={() => setOpen({ ...open, [j.id]: !show })}>
                  {show ? '▾' : '▸'}
                </button>
                <b style={{ fontSize: 13, color: '#e6e9ee' }}>{j.name || j.slug}</b>
                <span style={{ fontSize: 12, color: colour(j.stage) }}>
                  {j.active ? '⏳ ' : ''}{j.stage}{j.detail ? `: ${j.detail}` : ''}
                </span>
                <div style={{ flex: 1 }} />
                {/* ⏱ the elapsed clock — ticks while running, freezes when done */}
                {(j.elapsed_s || j.active) ? (
                  <span style={{ ...hint, ...mono, color: j.active ? '#9cc2ff' : '#8d97a5' }}
                        title={j.active ? 'running' : 'total run time'}>
                    ⏱ {dur(live)}
                  </span>
                ) : null}
                {j.queued && !j.active && <span style={hint}>queued</span>}
                {/* the collapsed row shows the likeness score, because "did the
                    7 hours produce a good LoRA" should not need a click */}
                {j.best_score !== undefined && j.best_score !== null && (
                  <span style={{ ...hint, ...mono,
                                 color: (j.best_score || 0) >= 0.45 ? '#5ee08a' : '#c9a227' }}
                        title={`best of ${j.best_epoch ?? '?'} — 0.45 is the match band`}>
                    ◎ {Number(j.best_score).toFixed(3)}
                  </span>
                )}
                {j.install_note && (
                  <span style={{ ...hint, color: '#c9a227' }} title={j.install_note}>⚠ epoch</span>
                )}
                {j.installed && <span style={{ ...hint, color: '#5ee08a' }}>🚀 {j.installed}</span>}
                {!terminal && <button style={btnSm} onClick={() => void act(j.id, 'cancel')}>⏹ stop</button>}
                {terminal && j.stage !== 'done' && (
                  <button style={btnSm} onClick={() => void act(j.id, 'retry')}>↻ retry</button>
                )}
                {terminal && <button style={btnSm} onClick={() => void act(j.id, 'delete')}>🗑</button>}
              </div>

              {/* compact chain — one glance at how far it got */}
              {!show && (
                <div style={{ display: 'flex', gap: 4, marginTop: 5, flexWrap: 'wrap' }}>
                  {STAGE_ORDER.filter((s) => done.has(s) || s === j.stage).map((s) => (
                    <span key={s} style={{
                      fontSize: 10, padding: '1px 6px', borderRadius: 999,
                      border: `1px solid ${done.has(s) ? '#2f6b45' : '#2a2f3a'}`,
                      color: done.has(s) ? '#5ee08a' : '#9cc2ff',
                    }}>
                      {done.has(s) ? '✓' : '…'} {s}
                      {j.stage_times?.[s] !== undefined ? ` ${dur(j.stage_times[s])}` : ''}
                    </span>
                  ))}
                  {j.estimate?.renders ? (
                    <span style={{ ...hint, fontSize: 10 }}>
                      est. {j.estimate.renders} renders / {j.estimate.human}
                    </span>
                  ) : null}
                </div>
              )}

              {show && <Detail jid={j.id} />}

              {j.error && (
                <div style={{ fontSize: 12, color: '#ff8a8a', marginTop: 4 }}>{j.error}</div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
