/**
 * 🎙 Speak a chapter, audition it, keep the take — and turn it into a project.
 *
 * His ask (2026-08-18):
 *   *"add an option to the chapter area to generate the TTS, audition it,
 *    select the voice we want to use and any settings like the speed and
 *    pacing option."*
 *   *"Once we have a chapter and its Narration Audio and associated files can
 *    we have a button in the chapter area to create a project, which asks us
 *    what kind of project we want to create… Make it so it requires all the
 *    narration files needed to do this before it starts and warn the user if
 *    they don't have them."*
 *
 * ⭐ AUDITION FIRST, KEEP SECOND. A render lands in the Audio Lab's job board
 * and is played HERE; nothing touches the chapter until ✅ Keep. That gap is
 * the feature — a take that overwrites the chapter the moment it finishes
 * cannot be compared against the one before it.
 *
 * ⭐⭐ KEEPING WRITES THE SRT TOO. The render measures every sentence as it
 * joins them, so the subtitles are exact and free. That is also what makes the
 * 🎬 project button's gate reachable: his rule is text + audio + SRT, all three.
 *
 * ⚠ >1.0 pace = SLOWER on BOTH engines. F5's node is inverted (measured) and
 * Kokoro's is inverted in code to match, so the one label means one thing.
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
const btnGreen =
  'px-3 py-1.5 rounded text-sm font-medium bg-emerald-700 hover:bg-emerald-600 text-white ' +
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
const get = <T,>(p: string) => fetch(p).then(r => j<T>(r));
const post = <T,>(p: string, body?: unknown) =>
  fetch(p, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  }).then(r => j<T>(r));

type VoiceT = {
  id: string; name: string; ready: boolean; needs_transcript: boolean;
  over_cap: boolean; clip_seconds?: number; kokoro: boolean;
  /** ⚠ readiness is PER ENGINE — a transcript-less voice is unusable by F5
   *  and perfectly fine for Chatterbox, so one flag would hide voices from
   *  the engine that actually works. */
  engines?: string[];
  /** the transcript/clip plausibility check — the likeliest cause of a
   *  muddy F5 clone (F5 derives its whole duration from that pair) */
  transcript_warning?: string;
  cps?: number;
};
type OptsT = {
  voices: VoiceT[];
  engines: Record<string, { ready: boolean; note?: string; where: string;
                            licence?: string }>;
  words: number; has_narration: boolean; est_minutes: number;
  current: Record<string, { filename: string; seconds?: number; cues?: number }>;
};

/** ⭐ CHATTERBOX FIRST — it is MIT, so it is the engine this app can hand to
 *  the public. F5 is CC-BY-NC: shipping it as the default would put a
 *  non-commercial restriction on everyone who ever uses this. */
const ENGINES: { key: string; label: string; blurb: string }[] = [
  { key: 'chatterbox', label: '🗣 Chatterbox',
    blurb: 'clones from the clip alone — no transcript, no 12s cap' },
  { key: 'f5tts', label: '🎤 F5',
    blurb: 'needs the clip’s exact transcript' },
  { key: 'kokoro', label: '🎨 Kokoro',
    blurb: 'factory voices only, instant, no GPU' },
];
type TtsJobT = {
  id: string; status?: string; detail?: string; elapsed_s?: number;
  seconds?: number; chunks?: number; file?: string; error?: string;
  cues?: unknown[]; cue_drift_s?: number; voice?: string; engine?: string;
  pace?: number; pace_mode?: string;
};
type ReadyT = {
  ready: boolean; blocking: string[]; warnings: string[];
  have: Record<string, boolean | number>; cast: number;
  audio_minutes: number; words: number;
  modes: { key: string; label: string; hint: string }[];
};

const PACE_KEY = 'rbmn_chapter_tts';

export default function ChapterVoicePanel({ wid, sid, cid, note, onChanged }: {
  wid: string; sid: string; cid: string;
  note: (m: string) => void; onChanged: () => void;
}) {
  const base = `${B}/worlds/${wid}/stories/${sid}/chapters/${cid}`;
  const [opts, setOpts] = useState<OptsT | null>(null);
  const [rdy, setRdy] = useState<ReadyT | null>(null);
  const [job, setJob] = useState<TtsJobT | null>(null);
  const [jid, setJid] = useState('');
  const [busy, setBusy] = useState(false);
  const [showProj, setShowProj] = useState(false);
  const timer = useRef<number | null>(null);
  // persisted per browser: he will use the same voice and pace for a whole book
  const [cfg, setCfg] = useState(() => {
    try {
      return {
        voice_id: '', engine: 'chatterbox', pace: 1.0, pace_mode: 'stretch',
        pause_ms: 600, sentence_pause_ms: 0, auto_tag: true,
        exaggeration: 0.5, temperature: 0.8, cfg_weight: 0.5,
        ...JSON.parse(localStorage.getItem(PACE_KEY) || '{}'),
      };
    } catch {
      return { voice_id: '', engine: 'chatterbox', pace: 1.0,
               pace_mode: 'stretch', pause_ms: 600, sentence_pause_ms: 0,
               auto_tag: true, exaggeration: 0.5, temperature: 0.8,
               cfg_weight: 0.5 };
    }
  });
  const setC = (p: Partial<typeof cfg>) => {
    const n = { ...cfg, ...p };
    setCfg(n);
    try { localStorage.setItem(PACE_KEY, JSON.stringify(n)); } catch { /* ok */ }
  };

  const load = useCallback(async () => {
    try {
      const [o, r] = await Promise.all([
        get<OptsT>(`${base}/tts/options`),
        get<ReadyT>(`${base}/project-readiness`),
      ]);
      setOpts(o); setRdy(r);
    } catch (e) { note(`⚠ ${e}`); }
  }, [base, note]);
  useEffect(() => { void load(); }, [load]);

  // ⚠ polls while `queued` too — the moment before a job starts running is
  // exactly when a silent screen looks broken.
  useEffect(() => {
    if (!jid) return;
    const tick = async () => {
      try {
        const r = await get<TtsJobT>(`/api/audio-lab/jobs/${jid}`);
        setJob(r);
        if (r.status === 'queued' || r.status === 'running') return;
        if (timer.current) window.clearInterval(timer.current);
        setBusy(false);
        if (r.status === 'done') {
          note(`🎙 ${r.seconds}s spoken · ${(r.cues || []).length} cues — audition it below`);
        } else { note(`⚠ ${r.error || r.status}`); }
      } catch { /* a blip is not a failure */ }
    };
    void tick();
    timer.current = window.setInterval(tick, 2000);
    return () => { if (timer.current) window.clearInterval(timer.current); };
  }, [jid, note]);

  const speak = async () => {
    if (!cfg.voice_id) { note('⚠ pick a voice first'); return; }
    setBusy(true); setJob(null);
    try {
      const r = await post<{ id: string; worker: string; chunks: number }>(
        `${base}/tts`, cfg);
      setJid(r.id);
      note(`🎙 speaking ${r.chunks} chunk(s) on ${r.worker}`);
    } catch (e) { note(`⚠ ${e}`); setBusy(false); }
  };

  const keep = async () => {
    try {
      const r = await post<{ kept: string[]; cues: number; notes: string[] }>(
        `${base}/tts/keep`, { job_id: jid, with_srt: true });
      note(`✅ kept: ${r.kept.join(' + ')}${r.cues ? ` · ${r.cues} cues` : ''}`
        + (r.notes.length ? ` — ${r.notes.join('; ')}` : ''));
      await load(); onChanged();
    } catch (e) { note(`⚠ ${e}`); }
  };

  const cur = opts?.current || {};
  const v = (opts?.voices || []).find(x => x.id === cfg.voice_id);
  const engineReady = opts?.engines?.[cfg.engine]?.ready;
  const done = job?.status === 'done';

  return (
    <div className="border-t border-gray-800 pt-2 space-y-2">
      <div className="flex items-center gap-2 flex-wrap">
        <span className="text-[11px] font-semibold text-fuchsia-300">🎙 Speak this chapter</span>
        <span className="text-[10px] text-gray-600">
          {opts?.words || 0} words ≈ {opts?.est_minutes || 0} min of narration
        </span>
        <div className="flex-1" />
        {cur.audio && (
          <span className="text-[10px] text-emerald-400">
            ✅ kept: {Math.round(cur.audio.seconds || 0)}s
            {cur.srt ? ` + SRT (${cur.srt.cues || '?'} cues)` : ' — no SRT yet'}
          </span>
        )}
      </div>

      {!opts?.has_narration ? (
        <div className="text-[11px] text-gray-600">
          Write the narration first — there is nothing to speak.
        </div>
      ) : (
        <>
          {/* ── engine chips: licence is part of the choice ─────────────── */}
          <div className="flex gap-1 flex-wrap items-center">
            {ENGINES.map(e => {
              const st = opts?.engines?.[e.key];
              const on = cfg.engine === e.key;
              const usable = st?.ready !== false;
              return (
                <button key={e.key}
                  className={`px-2 py-1 rounded text-xs border text-left ${on
                    ? 'border-amber-600 bg-amber-950/30 text-amber-200'
                    : usable ? 'border-gray-700 text-gray-300 hover:border-gray-500'
                      : 'border-gray-800 text-gray-600'}`}
                  title={`${st?.licence || ''}${st?.note ? ' — ' + st.note : ''}`}
                  onClick={() => setC({ engine: e.key })}>
                  {e.label}
                  {!usable && <span className="text-red-400"> ✕</span>}
                  <span className="block text-[9px] opacity-70">
                    {st?.licence?.includes('NON-COMMERCIAL')
                      ? '⚠ non-commercial' : e.blurb}
                  </span>
                </button>
              );
            })}
          </div>
          {cfg.engine === 'f5tts' && (
            <div className="text-[10px] text-amber-400">
              ⚠ <b>F5-TTS is CC-BY-NC 4.0 — non-commercial.</b> Fine for your own
              tests; if this app goes out to other people, that restriction
              travels with it. 🗣 Chatterbox is MIT and needs no transcript.
            </div>
          )}

          <div className="flex gap-1 flex-wrap items-end">
            <div>
              <label className="text-[10px] text-gray-500 block">voice</label>
              <select className={`${inputCls} w-52`} value={cfg.voice_id}
                onChange={e => setC({ voice_id: e.target.value })}>
                <option value="">— pick a voice —</option>
                {(opts?.voices || []).map(x => {
                  // ⚠ per-ENGINE: `ready` alone would grey out every
                  // transcript-less voice even under Chatterbox, which does
                  // not need one.
                  const ok = (x.engines || []).includes(cfg.engine);
                  const why = cfg.engine === 'f5tts'
                    ? (x.needs_transcript ? ' (needs transcript)'
                      : x.over_cap ? ' (clip too long for F5)' : '')
                    : cfg.engine === 'kokoro' && !x.kokoro
                      ? ' (recorded — Kokoro cannot clone)' : '';
                  return (
                    <option key={x.id} value={x.id} disabled={!ok}>
                      {x.kokoro ? '🎨 ' : '🎤 '}{x.name}{why}
                    </option>
                  );
                })}
              </select>
            </div>
            <div>
              <label className="text-[10px] text-gray-500 block"
                title="ONE control, and >1.0 is SLOWER on both engines">
                pace ({cfg.pace > 1 ? 'slower' : cfg.pace < 1 ? 'faster' : 'native'})
              </label>
              <input type="number" min={0.5} max={2} step={0.05} value={cfg.pace}
                className={`${inputCls} w-20`}
                onChange={e => setC({ pace: Number(e.target.value) || 1 })} />
            </div>
            <div>
              <label className="text-[10px] text-gray-500 block">how</label>
              <select className={`${inputCls} w-36`} value={cfg.pace_mode}
                onChange={e => setC({ pace_mode: e.target.value })}>
                <option value="stretch">stretch (quality)</option>
                <option value="model">model speed</option>
              </select>
            </div>
            <div>
              <label className="text-[10px] text-gray-500 block">¶ pause ms</label>
              <input type="number" min={0} max={5000} step={50} value={cfg.pause_ms}
                className={`${inputCls} w-20`}
                onChange={e => setC({ pause_ms: Number(e.target.value) || 0 })} />
            </div>
            <div>
              <label className="text-[10px] text-gray-500 block">. pause ms</label>
              <input type="number" min={0} max={3000} step={25}
                value={cfg.sentence_pause_ms} className={`${inputCls} w-20`}
                onChange={e => setC({ sentence_pause_ms: Number(e.target.value) || 0 })} />
            </div>
            <label className="flex items-center gap-1 text-[10px] text-gray-400 pb-2">
              <input type="checkbox" checked={cfg.auto_tag}
                onChange={e => setC({ auto_tag: e.target.checked })} />
              🪄 auto pauses
            </label>
            <button className={btnAmber} disabled={busy || !cfg.voice_id}
              onClick={() => void speak()}>
              {busy ? '⏳ speaking…' : '🎙 Render a take'}</button>
          </div>

          {/* 🗣 Chatterbox-only dials — the CHARACTER controls */}
          {cfg.engine === 'chatterbox' && (
            <div className="flex gap-2 items-end flex-wrap border border-gray-800 rounded p-2">
              <div>
                <label className="text-[10px] text-gray-500 block"
                  title="0.25-2.0 — how theatrical. An old western narrator wants this above 0.5.">
                  exaggeration
                </label>
                <input type="number" min={0.25} max={2} step={0.05}
                  value={cfg.exaggeration} className={`${inputCls} w-20`}
                  onChange={e => setC({ exaggeration: Number(e.target.value) || 0.5 })} />
              </div>
              <div>
                <label className="text-[10px] text-gray-500 block"
                  title="0.05-5.0 — randomness. Lower is steadier across a long narration.">
                  temperature
                </label>
                <input type="number" min={0.05} max={5} step={0.05}
                  value={cfg.temperature} className={`${inputCls} w-20`}
                  onChange={e => setC({ temperature: Number(e.target.value) || 0.8 })} />
              </div>
              <div>
                <label className="text-[10px] text-gray-500 block"
                  title="0.0-1.0 — how hard it holds the reference. >0 follows the clip's accent; 0 ignores it.">
                  cfg weight
                </label>
                <input type="number" min={0} max={1} step={0.05}
                  value={cfg.cfg_weight} className={`${inputCls} w-20`}
                  onChange={e => setC({ cfg_weight: Number(e.target.value) || 0 })} />
              </div>
              <span className="text-[10px] text-gray-600 pb-2 flex-1 min-w-[12rem]">
                ⭐ <b>exaggeration</b> is the character dial — nudge it up for an
                old western narrator. Lower <b>temperature</b> for a steadier
                read over a long chapter.
              </span>
            </div>
          )}

          {/* ⭐ the transcript/clip warning — F5's worst failure mode */}
          {cfg.engine === 'f5tts' && !!v?.transcript_warning && (
            <div className="text-[10px] text-amber-400 border border-amber-900/60 rounded p-2">
              ⚠ <b>{v.name}</b>: {v.transcript_warning}
            </div>
          )}

          <div className="text-[10px] text-gray-600">
            ⚠ <b>&gt;1.0 pace = SLOWER</b> on both engines. <i>stretch</i> renders at the
            model&apos;s native speed then time-stretches with pitch preserved — asking a
            vocoder to fill a longer duration is what made slow takes sound bad.
            {engineReady === false && (
              <span className="text-amber-400">
                {' '}· ⚠ {cfg.engine} is not ready: {opts?.engines?.[cfg.engine]?.note || 'no worker has it'}
              </span>
            )}
          </div>

          {job && (
            <div className="border border-gray-800 rounded p-2 text-[11px] space-y-1">
              <div className="flex items-center gap-2 flex-wrap">
                <span className={done ? 'text-emerald-400'
                  : job.status === 'error' ? 'text-red-400' : 'text-amber-300'}>
                  {done ? '✅' : job.status === 'error' ? '⚠' : '⏳'} {job.status}
                </span>
                <span className="text-gray-300">{job.detail}</span>
                <div className="flex-1" />
                {!!job.seconds && <span className="text-gray-400">{job.seconds}s spoken</span>}
                <span className="text-blue-300">⏱ {Math.round(job.elapsed_s || 0)}s</span>
              </div>
              {job.error && <div className="text-red-400">{job.error}</div>}
              {done && (
                <>
                  <audio controls className="w-full h-9"
                    src={`/api/audio-lab/media/${jid}`} />
                  <div className="flex items-center gap-2 flex-wrap">
                    <span className="text-gray-500">
                      {(job.cues || []).length} cues measured
                      {job.cue_drift_s !== undefined && job.cue_drift_s > 0.6
                        ? ` · ⚠ they disagree with the audio by ${job.cue_drift_s}s`
                        : ' · they match the audio'}
                    </span>
                    <a className="text-blue-300"
                      href={`/api/audio-lab/jobs/${jid}/srt`}>⬇ SRT</a>
                    <div className="flex-1" />
                    <button className={btnGreen} onClick={() => void keep()}>
                      ✅ Keep this take (audio + SRT)</button>
                  </div>
                  <div className="text-gray-600">
                    Nothing is written to the chapter until you press Keep — render
                    another with a different voice or pace and compare first.
                  </div>
                </>
              )}
            </div>
          )}
        </>
      )}

      {/* ── 🎬 make a project out of it ─────────────────────────────────── */}
      <div className="border-t border-gray-800 pt-2">
        <div className="flex items-center gap-2 flex-wrap">
          <span className="text-[11px] font-semibold text-sky-300">🎬 Make a project</span>
          {rdy?.ready ? (
            <span className="text-[10px] text-emerald-400">
              ready · {rdy.words} words · {rdy.audio_minutes} min · {rdy.cast} cast
            </span>
          ) : (
            <span className="text-[10px] text-amber-400">
              {rdy?.blocking.length || 0} thing(s) missing
            </span>
          )}
          <div className="flex-1" />
          <button className={btnCls} disabled={!rdy?.ready}
            title={rdy?.ready ? '' : (rdy?.blocking || []).join(' · ')}
            onClick={() => setShowProj(true)}>🎬 Create a project…</button>
        </div>
        {!rdy?.ready && !!(rdy?.blocking || []).length && (
          <ul className="text-[10px] text-amber-400 mt-1 ml-4 list-disc">
            {rdy!.blocking.map((b, i) => <li key={i}>{b}</li>)}
          </ul>
        )}
        {!!(rdy?.warnings || []).length && (
          <ul className="text-[10px] text-gray-500 mt-1 ml-4 list-disc">
            {rdy!.warnings.map((b, i) => <li key={i}>{b}</li>)}
          </ul>
        )}
      </div>

      {showProj && rdy && (
        <MakeProjectModal base={base} rdy={rdy} note={note}
          onClose={() => setShowProj(false)} />
      )}
    </div>
  );
}

function MakeProjectModal({ base, rdy, note, onClose }: {
  base: string; rdy: ReadyT; note: (m: string) => void; onClose: () => void;
}) {
  const [mode, setMode] = useState('narration_video');
  const [engine, setEngine] = useState('ltx_2.3');
  const [merge, setMerge] = useState(8);
  const [fromCues, setFromCues] = useState(true);
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState<{ project_id: string; steps: string[];
                                     scenes: number } | null>(null);
  return (
    <div className="fixed inset-0 bg-black/70 flex items-center justify-center z-50 p-4"
      onClick={onClose}>
      <div className="bg-gray-950 border border-gray-700 rounded p-4 w-full max-w-lg space-y-3"
        onClick={e => e.stopPropagation()}>
        <div className="text-sm font-semibold text-sky-300">
          🎬 Create a project from this chapter
        </div>
        {done ? (
          <>
            <div className="text-xs text-emerald-400">
              ✅ Created — {done.scenes} scene(s) built from the narration&apos;s own cues.
            </div>
            <ul className="text-[11px] text-gray-400 ml-4 list-disc space-y-0.5">
              {done.steps.map((s, i) => <li key={i}>{s}</li>)}
            </ul>
            <div className="flex gap-2 justify-end">
              <button className={btnCls} onClick={onClose}>Close</button>
              <a className={btnAmber} href={`/project/${done.project_id}`}>
                Open the project →</a>
            </div>
          </>
        ) : (
          <>
            <div>
              <div className="text-[11px] text-gray-400 mb-1">What kind of project?</div>
              <div className="space-y-1">
                {rdy.modes.map(m => (
                  <label key={m.key}
                    className={`flex items-start gap-2 text-xs p-2 rounded border cursor-pointer ${
                      mode === m.key ? 'border-sky-600 bg-sky-950/30'
                        : 'border-gray-800 hover:border-gray-600'}`}>
                    <input type="radio" className="mt-0.5" checked={mode === m.key}
                      onChange={() => setMode(m.key)} />
                    <span>
                      <b>{m.label}</b>
                      <span className="block text-[10px] text-gray-500">{m.hint}</span>
                    </span>
                  </label>
                ))}
              </div>
            </div>
            <div className="flex gap-2 items-end flex-wrap">
              <div>
                <label className="text-[10px] text-gray-500 block">video engine</label>
                <select className={`${inputCls} w-40`} value={engine}
                  onChange={e => setEngine(e.target.value)}>
                  <option value="ltx_2.3">LTX 2.3</option>
                  <option value="minimax_h3">MiniMax H3</option>
                </select>
              </div>
              <div>
                <label className="text-[10px] text-gray-500 block"
                  title="cues are per sentence — merge cut points closer than this">
                  merge cuts &lt; (s)
                </label>
                <input type="number" min={0} max={60} step={1} value={merge}
                  className={`${inputCls} w-20`}
                  onChange={e => setMerge(Number(e.target.value) || 0)} />
              </div>
              <label className="flex items-center gap-1 text-[11px] text-gray-400 pb-2">
                <input type="checkbox" checked={fromCues}
                  onChange={e => setFromCues(e.target.checked)} />
                build scenes from the cues
              </label>
            </div>
            <div className="text-[10px] text-gray-500">
              The narration&apos;s own measured cue times become the scene cuts — the same
              per-sentence boundaries an AAF carries, but with the words attached and
              nothing re-transcribed. 0 = one scene per sentence.
            </div>
            {!!rdy.warnings.length && (
              <div className="text-[11px] text-amber-400">
                ⚠ {rdy.warnings.join(' · ')}
              </div>
            )}
            <div className="flex gap-2 justify-end">
              <button className={btnCls} onClick={onClose}>Cancel</button>
              <button className={btnAmber} disabled={busy}
                onClick={async () => {
                  setBusy(true);
                  try {
                    const r = await post<{ project_id: string; steps: string[];
                                           scenes: number }>(
                      `${base}/create-project`,
                      { mode, video_engine: engine, scenes_from_cues: fromCues,
                        min_scene_seconds: merge });
                    setDone(r);
                    note(`🎬 project created — ${r.scenes} scenes`);
                  } catch (e) { note(`⚠ ${e}`); } finally { setBusy(false); }
                }}>{busy ? '⏳ building…' : '🎬 Create'}</button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
