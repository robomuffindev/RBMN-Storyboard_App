/**
 * 🎧 Audio Lab (v1.277.14) — a home-screen destination.
 *
 * 🎵 Music: ACE-Step 1.5 XL turbo (exact-length backing tracks, seconds-fast)
 *           + a MiniMax Music 3 slot that lights up when its nodes/models land.
 * 🎙 Narration: F5-TTS voice cloning (one clean 5-15s sample + its transcript),
 *           paragraph pauses, long-text chunking — the in-app ElevenLabs
 *           alternative.
 * Finished tracks play inline, download, or import straight into a project
 * as a MUSIC asset. Live status per job (the standing rule).
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';

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
type VoiceT = { id: string; name: string; transcript: string };
type JobT = {
  id: string; kind: string; engine: string; label: string; status: string;
  detail?: string; error?: string | null; elapsed_s?: number; worker?: string;
  seconds?: number; chunks?: number; file?: string; at?: string; voice?: string;
};
type ProjT = { id: string; name: string; mode: string };

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
  const [tab, setTab] = useState<'music' | 'tts'>('music');
  const [workers, setWorkers] = useState<WorkerT[]>([]);
  const [jobs, setJobs] = useState<JobT[]>([]);
  const [msg, setMsg] = useState('');
  const [projects, setProjects] = useState<ProjT[]>([]);
  const [importPick, setImportPick] = useState<Record<string, string>>({});

  // music form
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
  const [speed, setSpeed] = useState(1.0);
  const [newVoiceName, setNewVoiceName] = useState('');
  const [newVoiceTx, setNewVoiceTx] = useState('');
  const fileRef = useRef<HTMLInputElement | null>(null);

  const load = useCallback(async () => {
    try {
      const [ov, jl, vs, pr] = await Promise.all([
        fetch(`${B}/overview`).then(r => jj<{ workers: WorkerT[] }>(r)),
        fetch(`${B}/jobs`).then(r => jj<{ jobs: JobT[] }>(r)),
        fetch(`${B}/tts/voices`).then(r => jj<{ voices: VoiceT[]; cloning_guide: string }>(r)),
        fetch('/api/storyworld/projects').then(r => jj<{ projects: ProjT[] }>(r)).catch(() => ({ projects: [] })),
      ]);
      setWorkers(ov.workers || []);
      setJobs(jl.jobs || []);
      setVoicesList(vs.voices || []);
      setCloneGuide(vs.cloning_guide || '');
      setProjects(pr.projects || []);
      setVoiceId(v => v || (vs.voices?.[0]?.id ?? ''));
    } catch (e) { setMsg(`⚠ ${e}`); }
  }, []);
  useEffect(() => { void load(); }, [load]);
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
  const mm3Ready = eng('minimax3').some(e => e.ready);

  const genMusic = async () => {
    setBusy(true);
    try {
      await post(`/music/generate`, { engine: 'ace15', tags, lyrics, seconds, bpm, keyscale });
      setMsg('🎵 rendering — watch the jobs list');
      void load();
    } catch (e) { setMsg(`⚠ ${e}`); }
    setBusy(false);
  };
  const genTts = async () => {
    setBusy(true);
    try {
      const r = await post<{ chunks: number }>(`/tts/generate`,
        { voice_id: voiceId, text: ttsText, pause_ms: pauseMs, speed });
      setMsg(`🎙 rendering ${r.chunks} paragraph(s)`);
      void load();
    } catch (e) { setMsg(`⚠ ${e}`); }
    setBusy(false);
  };
  const addVoice = async (f: globalThis.File) => {
    if (!newVoiceName.trim() || !newVoiceTx.trim()) {
      setMsg('⚠ name the voice and paste the EXACT transcript of the sample first');
      return;
    }
    const fd = new FormData();
    fd.append('name', newVoiceName); fd.append('transcript', newVoiceTx);
    fd.append('file', f);
    try {
      await fetch(`${B}/tts/voices`, { method: 'POST', body: fd }).then(r => jj(r));
      setNewVoiceName(''); setNewVoiceTx(''); setMsg('voice added');
      void load();
    } catch (e) { setMsg(`⚠ ${e}`); }
  };

  return (
    <div className="min-h-screen bg-gray-950 text-gray-100">
      <input ref={fileRef} type="file" accept="audio/*" className="hidden"
             onChange={e => { const f = e.target.files?.[0]; e.target.value = ''; if (f) void addVoice(f); }} />
      <div className="flex items-center gap-3 px-5 py-3 border-b border-gray-800 flex-wrap">
        <button className="text-gray-400 hover:text-gray-200 text-sm" onClick={() => navigate('/')}>← Home</button>
        <h1 className="text-xl font-bold">🎧 Audio Lab</h1>
        <span className="text-xs text-gray-500">
          local music (ACE-Step 1.5) & narration with voice cloning (F5-TTS)
        </span>
        <span className="ml-auto text-xs text-gray-400">{msg}</span>
      </div>

      <div className="p-4">
        <div className="flex gap-2 mb-4">
          <button className={`px-3 py-1.5 rounded text-sm ${tab === 'music' ? 'bg-emerald-900/60 text-emerald-200 border border-emerald-700' : 'text-gray-400 hover:text-gray-200'}`}
                  onClick={() => setTab('music')}>🎵 Music</button>
          <button className={`px-3 py-1.5 rounded text-sm ${tab === 'tts' ? 'bg-emerald-900/60 text-emerald-200 border border-emerald-700' : 'text-gray-400 hover:text-gray-200'}`}
                  onClick={() => setTab('tts')}>🎙 Narration (TTS)</button>
        </div>

        {tab === 'music' && (
          <div className="max-w-3xl space-y-3">
            <div className="text-xs">
              <span className={aceReady ? 'text-green-300' : 'text-amber-400'}>
                ACE-Step 1.5: {aceReady ? '✅ ready' : (aceNote || '❌ run scripts/install_audio.py')}
              </span>
              <span className="ml-4 text-gray-500">
                MiniMax Music 3: {mm3Ready ? '✅ ready' : 'auto-detects when its ComfyUI nodes/models land'}
              </span>
            </div>
            <div>
              <label className="text-xs text-gray-400">Style / tags <span className="text-gray-600">— genre, mood, instrumentation, production; the more specific the better</span></label>
              <textarea className={inputCls} rows={3} value={tags}
                placeholder="dusty western americana, slow tempo, baritone guitar, sparse percussion, cinematic, melancholic…"
                onChange={e => setTags(e.target.value)} />
            </div>
            <div>
              <label className="text-xs text-gray-400">Lyrics <span className="text-gray-600">— [Intro] [Verse] [Chorus] tags control structure; empty = instrumental</span></label>
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
              <button className={btnGo} disabled={busy || !tags.trim() || !aceReady}
                      onClick={() => void genMusic()}>
                {busy ? '⏳' : '🎵 Generate track'}
              </button>
            </div>
          </div>
        )}

        {tab === 'tts' && (
          <div className="max-w-3xl space-y-3">
            <div className="text-xs">
              <span className={f5Ready ? 'text-green-300' : 'text-amber-400'}>
                F5-TTS: {f5Ready ? '✅ ready' : '❌ not installed — run scripts/install_audio.py, then restart ComfyUI on the workers'}
              </span>
            </div>
            <div className="border border-gray-800 rounded p-3">
              <div className="text-xs font-semibold text-emerald-300 mb-1">Voices</div>
              <div className="text-[11px] text-gray-500 mb-2">{cloneGuide}</div>
              <div className="flex gap-2 flex-wrap items-center mb-2">
                {voicesList.map(v => (
                  <span key={v.id}
                        className={`px-2 py-1 rounded text-xs border cursor-pointer ${voiceId === v.id ? 'border-emerald-500 text-emerald-200 bg-emerald-900/40' : 'border-gray-700 text-gray-300'}`}
                        onClick={() => setVoiceId(v.id)}>
                    🎤 {v.name}
                    <button className="ml-1 text-red-400" onClick={async (e) => {
                      e.stopPropagation();
                      if (!window.confirm(`Delete voice "${v.name}"?`)) return;
                      await post(`/tts/voices/${v.id}/delete`); void load();
                    }}>✕</button>
                  </span>
                ))}
                {!voicesList.length && <span className="text-xs text-gray-600">no voices yet — add one below</span>}
              </div>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
                <input className={inputCls} placeholder="voice name…" value={newVoiceName}
                       onChange={e => setNewVoiceName(e.target.value)} />
                <button className={btnCls} onClick={() => fileRef.current?.click()}
                        title="pick the 5-15s reference audio (WAV/MP3)">📎 pick sample & add</button>
                <textarea className={`${inputCls} md:col-span-2`} rows={2} value={newVoiceTx}
                          placeholder="the EXACT words spoken in the sample — word for word, punctuation included…"
                          onChange={e => setNewVoiceTx(e.target.value)} />
              </div>
            </div>
            <div>
              <label className="text-xs text-gray-400">Narration text <span className="text-gray-600">— blank lines = paragraph breaks; the pause below is inserted at each one</span></label>
              <textarea className={`${inputCls} font-mono`} rows={10} value={ttsText}
                        placeholder={'First paragraph…\n\nSecond paragraph — a pause is inserted between them.'}
                        onChange={e => setTtsText(e.target.value)} />
            </div>
            <div className="flex gap-3 items-end flex-wrap">
              <div>
                <label className="text-[11px] text-gray-500">pause between paragraphs (ms)</label>
                <input type="number" min={0} max={5000} step={100} className={`${inputCls} w-28`}
                       value={pauseMs} onChange={e => setPauseMs(Math.max(0, Number(e.target.value) || 0))} />
              </div>
              <div>
                <label className="text-[11px] text-gray-500">speed</label>
                <input type="number" min={0.5} max={2} step={0.05} className={`${inputCls} w-24`}
                       value={speed} onChange={e => setSpeed(Number(e.target.value) || 1)} />
              </div>
              <button className={btnGo} disabled={busy || !ttsText.trim() || !voiceId || !f5Ready}
                      onClick={() => void genTts()}>
                {busy ? '⏳' : '🎙 Generate narration'}
              </button>
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
