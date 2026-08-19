/**
 * 🎧 Audio Lab (v1.277.14) — a home-screen destination.
 *
 * 🎵 Music: FOUR engines, pickable — ACE 1.5 turbo (default, fast), ACE 1.5 XL
 *           sft/base (50 steps at cfg 3 — our value, not ComfyUI's 7/6) and
 *           MiniMax Music 3. 🆚 Compare renders one prompt on several at once.
 *           ⚠ Until v1.277.16 this page hardcoded engine:'ace15', so a ready
 *           MM3 was reachable by API and NOT by screen — a correct API is not
 *           a correct screen.
 * 🎙 Narration: F5-TTS voice cloning (one clean sample of ≤12 s + its transcript),
 *           paragraph pauses, long-text chunking — the in-app ElevenLabs
 *           alternative.
 * Finished tracks play inline, download, or import straight into a project
 * as a MUSIC asset. Live status per job (the standing rule).
 */
import { useCallback, useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import ScorePanel from './ScorePanel';
import VoiceLibrary, { type VoiceT } from './VoiceLibrary';

const B = '/api/audio-lab';

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

type EngT = { nodes: boolean; model: boolean; ready: boolean; note?: string };
type WorkerT = { host: string; name?: string; engines: Record<string, EngT> };
type JobT = {
  id: string; kind: string; engine: string; label: string; status: string;
  detail?: string; error?: string | null; elapsed_s?: number; worker?: string;
  seconds?: number; chunks?: number; file?: string; at?: string; voice?: string;
};
type ProjT = { id: string; name: string; mode: string };
type StoryOptT = { id: string; title: string; world_id: string; world: string };
type DlT = {
  host: string; name: string; file: string; status: string;
  bytes?: number; total?: number; pct?: number; error?: string | null;
};

const inputCls =
  'w-full bg-gray-900 border border-gray-700 rounded px-2 py-1.5 text-sm text-gray-100 ' +
  'focus:border-emerald-600 focus:outline-none placeholder-gray-600';
const btnCls =
  'px-3 py-1.5 rounded text-sm font-medium bg-gray-800 hover:bg-gray-700 text-gray-100 ' +
  'disabled:opacity-40 disabled:cursor-not-allowed transition-colors';
const btnGo =
  'px-3 py-1.5 rounded text-sm font-medium bg-emerald-700 hover:bg-emerald-600 text-white ' +
  'disabled:opacity-40 disabled:cursor-not-allowed transition-colors';

export default function AudioLabPage() {
  const navigate = useNavigate();
  const [tab, setTab] = useState<'music' | 'score' | 'tts'>('music');
  const [workers, setWorkers] = useState<WorkerT[]>([]);
  const [jobs, setJobs] = useState<JobT[]>([]);
  const [msg, setMsg] = useState('');
  const [projects, setProjects] = useState<ProjT[]>([]);
  const [importPick, setImportPick] = useState<Record<string, string>>({});
  // 🌍 a TTS take can land on a STORY as its narration recording
  const [storyOpts, setStoryOpts] = useState<StoryOptT[]>([]);
  const [storyPick, setStoryPick] = useState<Record<string, string>>({});
  const [dls, setDls] = useState<DlT[]>([]);

  // music form
  const [engine, setEngine] = useState<string>('ace15');
  const [steps, setSteps] = useState(0);      // 0 = the engine's own default
  const [cfgScale, setCfgScale] = useState(0);
  const [showAdv, setShowAdv] = useState(false);
  const [tags, setTags] = useState('');
  const [lyrics, setLyrics] = useState('');
  const [seconds, setSeconds] = useState(60);
  const [bpm, setBpm] = useState(0);
  const [keyscale, setKeyscale] = useState('');
  const [busy, setBusy] = useState(false);

  // tts form
  const [voicesList, setVoicesList] = useState<VoiceT[]>([]);
  const [cloneGuide, setCloneGuide] = useState('');
  const [voiceId, setVoiceId] = useState('');
  const [ttsText, setTtsText] = useState('');
  const [pauseMs, setPauseMs] = useState(600);
  const [speed, setSpeed] = useState(1.0);          // 🐢 pace, >1 = slower
  const [paceMode, setPaceMode] = useState<'stretch' | 'model'>('stretch');
  // 🫁 pauses. ⭐ His call: never hand-typed — the 🪄 button WRITES the tags
  // into the text (visible, editable, idempotent), and auto-tagging runs on
  // the way to render unless he turns it off.
  const [autoTag, setAutoTag] = useState(true);
  const [sentPause, setSentPause] = useState(350);  // gap after every full stop
  const [voiceCap, setVoiceCap] = useState(12);
  // 🗣 who speaks: F5 clones the voice's clip on a worker; Kokoro speaks
  // directly here with the preset a 🎨 factory voice was made from.
  const [ttsEngine, setTtsEngine] = useState<'f5tts' | 'kokoro'>('f5tts');

  const load = useCallback(async () => {
    try {
      const [ov, jl, vs, pr, so] = await Promise.all([
        fetch(`${B}/overview`).then(r => jj<{ workers: WorkerT[] }>(r)),
        fetch(`${B}/jobs`).then(r => jj<{ jobs: JobT[] }>(r)),
        fetch(`${B}/tts/voices`).then(r => jj<{ voices: VoiceT[]; cloning_guide: string; cap_seconds: number }>(r)),
        fetch('/api/storyworld/projects').then(r => jj<{ projects: ProjT[] }>(r)).catch(() => ({ projects: [] })),
        fetch('/api/storyworld/stories').then(r => jj<{ stories: StoryOptT[] }>(r)).catch(() => ({ stories: [] })),
      ]);
      setWorkers(ov.workers || []);
      setJobs(jl.jobs || []);
      setVoicesList(vs.voices || []);
      setCloneGuide(vs.cloning_guide || '');
      if (vs.cap_seconds) setVoiceCap(vs.cap_seconds);
      setProjects(pr.projects || []);
      setStoryOpts(so.stories || []);
      setVoiceId(v => v || (vs.voices?.[0]?.id ?? ''));
    } catch (e) { setMsg(`⚠ ${e}`); }
  }, []);
  useEffect(() => { void load(); }, [load]);

  // ⬇ model staging — an engine chip only flips when a file has FULLY landed,
  // so without this a 19 GB download and a stalled one look identical.
  const loadDls = useCallback(() => {
    fetch(`${B}/staging`).then(r => jj<{ downloads: DlT[] }>(r))
      .then(d => setDls(d.downloads || [])).catch(() => { /* transient */ });
  }, []);
  useEffect(() => { loadDls(); }, [loadDls]);
  const dlActive = dls.some(d => d.status === 'running');
  useEffect(() => {
    const iv = window.setInterval(loadDls, dlActive ? 10000 : 60000);
    return () => window.clearInterval(iv);
  }, [loadDls, dlActive]);

  const anyRunning = jobs.some(j => ['queued', 'running'].includes(j.status));
  useEffect(() => {
    const iv = window.setInterval(() => {
      fetch(`${B}/jobs`).then(r => jj<{ jobs: JobT[] }>(r))
        .then(j => setJobs(j.jobs || [])).catch(() => { /* transient */ });
    }, anyRunning ? 3000 : 15000);
    return () => window.clearInterval(iv);
  }, [anyRunning]);

  const eng = (key: string) => workers.map(w => w.engines?.[key]).filter(Boolean);
  const aceReady = eng('ace15').some(e => e.ready);
  const aceNote = eng('ace15').some(e => e.nodes) && !aceReady
    ? 'nodes present — model still downloading (install_audio.py --check)' : '';
  const f5Ready = eng('f5tts').some(e => e.ready);
  const ready = (k: string) => eng(k).some(e => e.ready);
  const engNote = (k: string) => eng(k).map(e => e.note).find(n => n) || '';
  //: 🎚 v1.277.17 — FOUR music engines. `ace15` is the TURBO checkpoint (8
  //  steps, cfg 1 — fast draft); base/sft are ComfyUI's 50-step quality
  //  templates on the bigger XL model. The button gates on the SELECTED
  //  engine — gating everything on ACE is how a ready MM3 stayed unreachable.
  const ENGINES = [
    { k: 'ace15', label: '🎵 ACE 1.5 turbo', hint: 'the fast draft engine — 8 steps, ~1s of render per second of music' },
    { k: 'ace15_sft', label: '🎚 ACE 1.5 XL sft', hint: 'the QUALITY engine, finetuned for songs — 50 steps at cfg 3 (his approved recipe; ComfyUI ships 7, which garbles it)' },
    { k: 'ace15_base', label: '🎚 ACE 1.5 XL base', hint: 'the quality general model — 50 steps at cfg 3 (ComfyUI ships 6, which garbles it)' },
    { k: 'minimax3', label: '🎼 MiniMax Music 3', hint: 'caption + lyrics, ~2.5-3s of render per second of music (measured: the step count is NOT the cost)' },
  ];
  const engineReady = ready(engine);
  const isMM3 = engine === 'minimax3';

  const genMusic = async () => {
    setBusy(true);
    try {
      const adv = { ...(steps ? { steps } : {}), ...(cfgScale ? { cfg: cfgScale } : {}) };
      await post(`/music/generate`, isMM3
        ? { engine, tags, lyrics, seconds, ...adv }      // MM3: caption + lyrics
        : { engine, tags, lyrics, seconds, bpm, keyscale, ...adv });
      setMsg('🎵 rendering — watch the jobs list');
      void load();
    } catch (e) { setMsg(`⚠ ${e}`); }
    setBusy(false);
  };
  // 🆚 same prompt, several models, one per box — the models genuinely
  // disagree and which one wins depends on the piece, so this is a per-track
  // decision made by ear rather than a setting.
  const [cmpOn, setCmpOn] = useState(false);
  const [cmpPick, setCmpPick] = useState<string[]>(['ace15', 'ace15_sft', 'minimax3']);
  const compare = async () => {
    setBusy(true);
    try {
      const r = await post<{ started: unknown[]; skipped: string[] }>(
        `/music/compare`, {
          engines: cmpPick, tags, lyrics, seconds, bpm, keyscale,
          ...(steps ? { steps } : {}), ...(cfgScale ? { cfg: cfgScale } : {}),
        });
      setMsg(`🆚 ${r.started.length} versions rendering — same seed, same prompt,`
        + ` loudness-matched${r.skipped.length ? ` · skipped ${r.skipped.join(', ')}` : ''}`);
      void load();
    } catch (e) { setMsg(`⚠ ${e}`); }
    setBusy(false);
  };

  const applyAutoTag = async (silent = false) => {
    if (!ttsText.trim()) return ttsText;
    try {
      const r = await post<{ text: string; added: number; chunks: number; silence_s: number }>(
        // retag: re-value the tags this button wrote before, so changing the
        // setting is enough — he should never edit a number by hand
        '/tts/auto-tag', { text: ttsText, sentence_ms: sentPause, retag: true });
      setTtsText(r.text);
      if (!silent) {
        setMsg(r.added
          ? `🪄 added ${r.added} pause tag(s) — ${r.chunks} pieces, ${r.silence_s}s of silence`
          : '🪄 already tagged — nothing to add');
      }
      return r.text;
    } catch (e) { setMsg(`⚠ ${e}`); return ttsText; }
  };

  const genTts = async () => {
    setBusy(true);
    try {
      // ⭐ tag first, so what renders is exactly what he can see in the box
      if (autoTag) await applyAutoTag(true);
      const r = await post<{ chunks: number }>(`/tts/generate`,
        { voice_id: voiceId, text: ttsText, pause_ms: pauseMs,
          pace: speed, pace_mode: paceMode,
          // when auto-tagging is on, the tags carry the timing and the blunt
          // per-sentence rule must stand down — otherwise both fire and every
          // full stop gets its gap twice
          sentence_pause_ms: autoTag ? 0 : sentPause,
          engine: ttsEngine });
      setMsg(`🎙 rendering ${r.chunks} paragraph(s)`);
      void load();
    } catch (e) { setMsg(`⚠ ${e}`); }
    setBusy(false);
  };

  return (
    <div className="min-h-screen bg-gray-950 text-gray-100">
      <div className="flex items-center gap-3 px-5 py-3 border-b border-gray-800 flex-wrap">
        <button className="text-gray-400 hover:text-gray-200 text-sm" onClick={() => navigate('/')}>← Home</button>
        <h1 className="text-xl font-bold">🎧 Audio Lab</h1>
        <span className="text-xs text-gray-500">
          local music (ACE-Step 1.5 · MiniMax Music 3) & narration with voice cloning (F5-TTS)
        </span>
        <span className="ml-auto text-xs text-gray-400">{msg}</span>
      </div>

      <div className="p-4">
        <div className="flex gap-2 mb-4">
          <button className={`px-3 py-1.5 rounded text-sm ${tab === 'music' ? 'bg-emerald-900/60 text-emerald-200 border border-emerald-700' : 'text-gray-400 hover:text-gray-200'}`}
                  onClick={() => setTab('music')}>🎵 Music</button>
          <button className={`px-3 py-1.5 rounded text-sm ${tab === 'score' ? 'bg-emerald-900/60 text-emerald-200 border border-emerald-700' : 'text-gray-400 hover:text-gray-200'}`}
                  onClick={() => setTab('score')}>🎼 Score a story</button>
          <button className={`px-3 py-1.5 rounded text-sm ${tab === 'tts' ? 'bg-emerald-900/60 text-emerald-200 border border-emerald-700' : 'text-gray-400 hover:text-gray-200'}`}
                  onClick={() => setTab('tts')}>🎙 Narration (TTS)</button>
        </div>

        {tab === 'score' && (
          <ScorePanel engines={ENGINES.map(o => ({ ...o, ready: ready(o.k) }))} />
        )}

        {tab === 'music' && (
          <div className="max-w-3xl space-y-3">
            {/* 🎛 engine picker — each chip carries its own readiness */}
            <div className="flex gap-2 flex-wrap items-center">
              {ENGINES.map(o => (
                <button key={o.k} onClick={() => setEngine(o.k)}
                  title={ready(o.k) ? o.hint : (engNote(o.k) || o.hint)}
                  className={`px-3 py-1.5 rounded text-sm border transition-colors ${
                    engine === o.k
                      ? 'border-emerald-500 bg-emerald-900/40 text-emerald-100'
                      : 'border-gray-700 text-gray-400 hover:text-gray-200'}`}>
                  {o.label} <span className={ready(o.k) ? 'text-green-400' : 'text-amber-400'}>
                    {ready(o.k) ? '✅' : '❌'}</span>
                </button>
              ))}
            </div>
            {/* ⬇ staging strip — only when something is actually moving */}
            {dls.some(d => d.status === 'running') && (
              <div className="border border-gray-800 rounded p-2 space-y-1">
                <div className="text-[11px] text-emerald-300">
                  ⬇ staging models — engines light up as each file lands
                </div>
                {dls.filter(d => d.status === 'running').map(d => (
                  <div key={`${d.host}-${d.file}`} className="text-[11px] text-gray-400">
                    <div className="flex gap-2">
                      <span className="text-gray-500 w-28 truncate">{d.name}</span>
                      <span className="flex-1 truncate">{d.file}</span>
                      <span>{((d.bytes || 0) / 2 ** 30).toFixed(2)} / {((d.total || 0) / 2 ** 30).toFixed(2)} GB · {d.pct}%</span>
                    </div>
                    <div className="h-1 bg-gray-800 rounded mt-0.5">
                      <div className="h-1 bg-emerald-600 rounded"
                           style={{ width: `${Math.min(100, d.pct || 0)}%` }} />
                    </div>
                  </div>
                ))}
              </div>
            )}
            <div className="text-[11px] text-gray-500">
              {ENGINES.find(o => o.k === engine)?.hint}
              {!engineReady && (
                <span className="text-amber-400">
                  {' '}· {engNote(engine) || (aceNote || 'not staged on any box')}
                </span>
              )}
            </div>
            <div>
              <label className="text-xs text-gray-400">
                {isMM3 ? 'Caption' : 'Style / tags'}{' '}
                <span className="text-gray-600">— genre, mood, instrumentation, production; the more specific the better</span></label>
              <textarea className={inputCls} rows={3} value={tags}
                placeholder={isMM3
                  ? 'A slow cinematic western instrumental: baritone guitar, sparse percussion, distant harmonica, wide desert air…'
                  : 'dusty western americana, slow tempo, baritone guitar, sparse percussion, cinematic, melancholic…'}
                onChange={e => setTags(e.target.value)} />
            </div>
            <div>
              <label className="text-xs text-gray-400">Lyrics <span className="text-gray-600">— [Intro] [Verse] [Chorus] tags control structure; empty = instrumental{isMM3 ? ' (MM3 sends [instrumental] for you)' : ''}</span></label>
              <textarea className={`${inputCls} font-mono`} rows={8} value={lyrics}
                placeholder={'[Intro - Guitar]\n\n[Verse 1]\n…'}
                onChange={e => setLyrics(e.target.value)} />
            </div>
            <div className="flex gap-3 items-end flex-wrap">
              <div>
                <label className="text-[11px] text-gray-500">length (s) — exact, pair it to the story arc</label>
                <input type="number" min={5} max={300} className={`${inputCls} w-28`} value={seconds}
                       onChange={e => setSeconds(Math.max(5, Math.min(300, Number(e.target.value) || 60)))} />
              </div>
              {!isMM3 && (
                <>
                  <div>
                    <label className="text-[11px] text-gray-500">bpm (0 = auto)</label>
                    <input type="number" min={0} max={260} className={`${inputCls} w-24`} value={bpm}
                           onChange={e => setBpm(Math.max(0, Number(e.target.value) || 0))} />
                  </div>
                  <div>
                    <label className="text-[11px] text-gray-500">key (optional)</label>
                    <input className={`${inputCls} w-28`} value={keyscale} placeholder="E minor"
                           onChange={e => setKeyscale(e.target.value)} />
                  </div>
                </>
              )}
              <button className={btnGo} disabled={busy || !tags.trim() || !engineReady}
                      onClick={() => void genMusic()}>
                {busy ? '⏳' : `🎵 Generate track (${ENGINES.find(o => o.k === engine)?.label.replace(/^\S+\s/, '') || engine})`}
              </button>
              <button className={btnCls} disabled={busy || !tags.trim() || !cmpPick.length}
                      title="render this prompt on several models at once, one per box"
                      onClick={() => (cmpOn ? void compare() : setCmpOn(true))}>
                {cmpOn ? '🆚 Render all picked' : '🆚 Compare models'}
              </button>
              <button className="text-[11px] text-gray-500 hover:text-gray-300 pb-2"
                      onClick={() => setShowAdv(v => !v)}>
                {showAdv ? '▾' : '▸'} advanced
              </button>
            </div>
            {cmpOn && (
              <div className="border border-gray-800 rounded p-2 space-y-1">
                <div className="text-[11px] text-emerald-300">
                  🆚 compare — same prompt, same seed, fanned round-robin across the
                  boxes (pick more engines than boxes and two share one), every result
                  loudness-matched so the test is about the mix
                </div>
                <div className="flex gap-2 flex-wrap items-center">
                  {ENGINES.map(o => (
                    <label key={o.k}
                           className={`flex items-center gap-1 text-xs px-2 py-1 rounded border ${
                             cmpPick.includes(o.k) ? 'border-emerald-600 text-emerald-200'
                                                   : 'border-gray-700 text-gray-400'}`}>
                      <input type="checkbox" checked={cmpPick.includes(o.k)}
                             onChange={e => setCmpPick(p => e.target.checked
                               ? [...p, o.k] : p.filter(x => x !== o.k))} />
                      {o.label} <span className={ready(o.k) ? 'text-green-400' : 'text-amber-400'}>
                        {ready(o.k) ? '✅' : '❌'}</span>
                    </label>
                  ))}
                  <button className="text-[11px] text-gray-500 hover:text-gray-300"
                          onClick={() => setCmpOn(false)}>✕ close</button>
                </div>
              </div>
            )}
            {showAdv && (
              <div className="flex gap-3 items-end flex-wrap border border-gray-800 rounded p-2">
                <div>
                  <label className="text-[11px] text-gray-500">steps (0 = engine default)</label>
                  <input type="number" min={0} max={120} className={`${inputCls} w-28`} value={steps}
                         onChange={e => setSteps(Math.max(0, Math.min(120, Number(e.target.value) || 0)))} />
                </div>
                <div>
                  <label className="text-[11px] text-gray-500">cfg (0 = engine default)</label>
                  <input type="number" min={0} max={12} step={0.5} className={`${inputCls} w-24`} value={cfgScale}
                         onChange={e => setCfgScale(Math.max(0, Math.min(12, Number(e.target.value) || 0)))} />
                </div>
                <div className="text-[11px] text-gray-500 pb-2 max-w-lg">
                  Our defaults: turbo 8 steps/cfg 1 · XL base and XL sft 50 steps/<b>cfg 3</b> ·
                  MM3 30/1.7. ⚠ ComfyUI's own XL templates ship cfg 7/6, which is the
                  setting that garbles these models (issue #12322) — 3 is deliberate. <b>Measured 2026-08-16 (20s track, ZOAI3):</b> ACE 8→15.4s,
                  24→21.9s, 50→29.5s — steps are cheap here. MM3 30→61.9s, 18→58.1s, 4→48.6s —
                  <b> ~46s of MM3 is fixed cost</b>, so lowering its steps buys almost nothing.
                </div>
                {/* ⚠ the turbo checkpoint is DISTILLED for cfg 1 — Lorenzo's ear
                    confirmed it on 2026-08-16: cfg 3 = artifacts and a broken mix,
                    cfg 6 = noise with no vocal. Say so where the number is typed. */}
                {engine === 'ace15' && cfgScale > 1.5 && (
                  <div className="text-[11px] text-amber-400 max-w-lg">
                    ⚠ ACE turbo is DISTILLED for cfg 1. Verified by ear: cfg 3 brings
                    artifacts and a poor mix, cfg 6 collapses into noise with no vocal.
                    More steps alone are safe; raising cfg on this model is not — use the
                    XL sft / base engines for guidance above 1.
                  </div>
                )}
              </div>
            )}
          </div>
        )}

        {tab === 'tts' && (
          <div className="max-w-3xl space-y-3">
            <div className="text-xs">
              <span className={f5Ready ? 'text-green-300' : 'text-amber-400'}>
                F5-TTS: {f5Ready ? '✅ ready' : '❌ not installed — run scripts/install_audio.py, then restart ComfyUI on the workers'}
              </span>
            </div>
            <VoiceLibrary voices={voicesList} voiceId={voiceId} setVoiceId={setVoiceId}
                          cap={voiceCap} guide={cloneGuide} reload={load} setMsg={setMsg} />
            <div>
              <label className="text-xs text-gray-400">Narration text <span className="text-gray-600">— blank lines = paragraph breaks; the pause below is inserted at each one</span></label>
              <textarea className={`${inputCls} font-mono`} rows={10} value={ttsText}
                        placeholder={'First paragraph…\n\nSecond paragraph — a pause is inserted between them.\n\nWrite [pause], [pause 900], [beat] or [breath] anywhere you want air.'}
                        onChange={e => setTtsText(e.target.value)} />
              <div className="flex items-center gap-3 flex-wrap mt-1.5">
                <button className={btnCls} disabled={!ttsText.trim()}
                        onClick={() => void applyAutoTag()}
                        title="write [pause] tags after every sentence, ellipsis and em-dash">
                  🪄 Auto-tag the pauses
                </button>
                <label className="flex items-center gap-1.5 text-[11px] text-gray-400">
                  <input type="checkbox" checked={autoTag}
                         onChange={e => setAutoTag(e.target.checked)} />
                  tag automatically before every render
                </label>
                <span className="text-[11px] text-gray-600">
                  tags are stripped before the model sees them — they only buy silence
                </span>
              </div>
            </div>
            <div className="flex gap-3 items-end flex-wrap">
              <div>
                <label className="text-[11px] text-gray-500">pause between paragraphs (ms)</label>
                <input type="number" min={0} max={5000} step={100} className={`${inputCls} w-28`}
                       value={pauseMs} onChange={e => setPauseMs(Math.max(0, Number(e.target.value) || 0))} />
              </div>
              {/* 🫁 a gap after every full stop. It is SILENCE, so it cannot
                  introduce artifacts — the cheapest way to slow a read down.
                  ⚠ It renders each sentence separately, so prosody can step
                  between them; that is why it is off by default. */}
              <div>
                <label className="text-[11px] text-gray-500">🫁 pause after each period (ms)</label>
                <select className={`${inputCls} w-44`} value={sentPause}
                        onChange={e => {
                          const v = Number(e.target.value) || 0;
                          setSentPause(v);
                          // ⭐ re-tag NOW: the text on screen must match the
                          // setting, or the next render uses numbers he cannot see
                          if (v && /\[\s*(pause|beat|breath|break)/i.test(ttsText)) {
                            void post<{ text: string; added: number }>(
                              '/tts/auto-tag',
                              { text: ttsText, sentence_ms: v, retag: true })
                              .then(r => { setTtsText(r.text); setMsg(`🫁 pauses set to ${v}ms`); })
                              .catch(() => { /* the button still works */ });
                          }
                        }}>
                  <option value={0}>off — sentences run on</option>
                  <option value={350}>350 · natural (default)</option>
                  <option value={150}>150 · barely there</option>
                  <option value={250}>250 · natural</option>
                  <option value={400}>400 · deliberate</option>
                  <option value={600}>600 · storytelling</option>
                  <option value={900}>900 · very slow</option>
                </select>
              </div>
              {/* 🐢 PACE. ⚠⚠ The node's `speed` is INVERTED: >1.0 is SLOWER.
                  Measured on .163 with one sentence, same seed:
                  0.8 → 4.86s · 1.0 → 6.07s · 1.2 → 7.28s. Shipping a spinner
                  labelled "speed" meant anyone reaching for a slower read
                  turned it DOWN and got a faster one. */}
              <div>
                <label className="text-[11px] text-gray-500">
                  🐢 pace <span className="text-gray-600">— higher = slower</span>
                </label>
                <select className={`${inputCls} w-52`} value={speed}
                        onChange={e => setSpeed(Number(e.target.value) || 1)}>
                  <option value={0.8}>0.8 · noticeably faster</option>
                  <option value={0.9}>0.9 · a little faster</option>
                  <option value={1}>1.0 · as the reference speaks</option>
                  <option value={1.1}>1.1 · a little slower</option>
                  <option value={1.2}>1.2 · measured, narration pace</option>
                  <option value={1.3}>1.3 · slow and deliberate</option>
                  <option value={1.5}>1.5 · very slow</option>
                </select>
              </div>
              {/* HOW the pace is delivered — the difference he heard */}
              <div>
                <label className="text-[11px] text-gray-500">how</label>
                <select className={`${inputCls} w-56`} value={paceMode}
                        onChange={e => setPaceMode(e.target.value as 'stretch' | 'model')}>
                  <option value="stretch">stretch after (best quality)</option>
                  <option value="model">ask the model (artifacts)</option>
                </select>
              </div>
              <div>
                <label className="text-[11px] text-gray-500">engine</label>
                <select className={`${inputCls} w-56`} value={ttsEngine}
                        onChange={e => setTtsEngine(e.target.value as 'f5tts' | 'kokoro')}>
                  <option value="f5tts">F5 — clones this voice (GPU)</option>
                  <option value="kokoro">Kokoro — instant, 🎨 factory voices only</option>
                </select>
              </div>
              <button className={btnGo}
                      disabled={busy || !ttsText.trim() || !voiceId
                                || (ttsEngine === 'f5tts' && !f5Ready)}
                      onClick={() => void genTts()}>
                {busy ? '⏳' : '🎙 Generate narration'}
              </button>
            </div>
            <div className="text-[11px] text-gray-500 max-w-2xl space-y-1">
              <div>
                🐢 <b>Slower without the mush.</b> Asking the MODEL to slow down
                makes it stretch its own prosody while generating, which is why
                those takes sounded bad. <b>stretch after</b> renders at the
                model&apos;s native pace and then time-stretches the finished
                audio with rubberband (pitch and formants preserved) — same
                length, far cleaner. Keep <i>ask the model</i> only to compare.
              </div>
              <div>
                🫁 <b>Pauses cost nothing.</b> Silence cannot add artifacts, so
                tags are free quality-wise — often the only slowdown a narration
                needs. Measured on one paragraph: 10.58s clean → 11.60s with
                3 auto tags (1.05s of silence, landing exactly) → 13.34s with
                pace 1.15 on top.
              </div>
              <div>
                🗣 <b>Two engines, and they are not interchangeable.</b> <b>F5</b>
                clones the selected voice&apos;s reference clip on a worker —
                it is the only one that can sound like a specific person.
                <b> Kokoro</b> speaks on this machine with the built-in speaker a
                🎨 factory voice was made from: near-instant, perfectly
                consistent, no GPU — but it cannot clone a recording, so it is
                offered only for factory voices.
              </div>
              <div>
                ⭐ <b>The biggest lever is the reference clip.</b> F5 copies how
                your sample speaks. If 1.0 already sounds hurried, the reference
                is hurried — re-cut it in 🪪 Details on a calmer stretch and
                everything downstream slows down for free.
              </div>
            </div>
          </div>
        )}

        {/* jobs — the live board (standing rule: what, where, how long) */}
        <div className="mt-6 max-w-4xl">
          <div className="text-xs font-semibold text-emerald-300 mb-2">Renders</div>
          {!jobs.length && <div className="text-xs text-gray-600">nothing yet</div>}
          <div className="space-y-2">
            {jobs.map(j => (
              <div key={j.id} className="border border-gray-800 rounded p-3">
                <div className="flex items-center gap-2 flex-wrap text-sm">
                  <span>{j.kind === 'music' ? '🎵' : '🎙'}</span>
                  <b className="truncate max-w-xs">{j.label}</b>
                  <span className="text-xs text-gray-500">
                    {j.engine}{j.voice ? ` · ${j.voice}` : ''}{j.seconds ? ` · ${j.seconds}s` : ''}
                    {j.chunks ? ` · ${j.chunks} chunks` : ''} · {j.worker}
                  </span>
                  <span className={`text-xs font-bold ${j.status === 'done' ? 'text-green-300' : j.status === 'error' ? 'text-red-400' : 'text-blue-300'}`}>
                    {j.status === 'done' ? `✓ done · took ${Math.round(j.elapsed_s || 0)}s`
                      : j.status === 'error' ? '✕ error'
                        : `⏳ ${j.detail || j.status} ${Math.round(j.elapsed_s || 0)}s`}
                  </span>
                  <div className="flex-1" />
                  {j.status === 'done' && (
                    <>
                      <a href={`${B}/media/${j.id}?download=1`} className="text-xs text-blue-300">⬇</a>
                      <select className="bg-gray-900 border border-gray-700 rounded px-1 py-0.5 text-xs"
                              value={importPick[j.id] || ''}
                              onChange={e => setImportPick(p => ({ ...p, [j.id]: e.target.value }))}>
                        <option value="">→ project…</option>
                        {projects.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}
                      </select>
                      <button className={btnCls} disabled={!importPick[j.id]}
                              onClick={async () => {
                                try {
                                  await post(`/jobs/${j.id}/send-to-project`,
                                    { project_id: importPick[j.id], as_type: 'music' });
                                  setMsg('imported as a MUSIC asset — run audio analysis in the project');
                                } catch (e) { setMsg(`⚠ ${e}`); }
                              }}>📥 import</button>
                      {/* 🌍 a narration take belongs to a STORY, not only to a
                          project — and landing it here is what records which
                          stories a voice was used on */}
                      {j.kind === 'tts' && (
                        <>
                          <select className="bg-gray-900 border border-gray-700 rounded px-1 py-0.5 text-xs"
                                  value={storyPick[j.id] || ''}
                                  onChange={e => setStoryPick(p => ({ ...p, [j.id]: e.target.value }))}>
                            <option value="">→ story…</option>
                            {storyOpts.map(s => (
                              <option key={`${s.world_id}/${s.id}`} value={`${s.world_id}/${s.id}`}>
                                {s.world} · {s.title}
                              </option>
                            ))}
                          </select>
                          <button className={btnCls} disabled={!storyPick[j.id]}
                                  onClick={async () => {
                                    const [wid, sid] = (storyPick[j.id] || '').split('/');
                                    try {
                                      const r = await post<{ story: string }>(
                                        `/jobs/${j.id}/send-to-story`,
                                        { world_id: wid, story_id: sid, slot: 'audio' });
                                      setMsg(`🌍 it is now the narration recording on "${r.story}"`);
                                    } catch (e) { setMsg(`⚠ ${e}`); }
                                  }}>🌍 to story</button>
                        </>
                      )}
                    </>
                  )}
                  <button className="text-xs text-red-400" onClick={async () => {
                    await fetch(`${B}/jobs/${j.id}`, { method: 'DELETE' }); void load();
                  }}>🗑</button>
                </div>
                {j.status === 'error' && <div className="text-xs text-red-400 mt-1">{j.error}</div>}
                {j.status === 'done' && (
                  <audio controls preload="none" src={`${B}/media/${j.id}`}
                         className="w-full mt-2 h-9" />
                )}
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
