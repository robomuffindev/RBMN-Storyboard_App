/**
 * 🎬 Video Lab (v1.275.0) — MiniMax H3, every mode the ultra workflows expose.
 *
 * T2V / I2V / First+Last / Last-frame / References→Video (≤9 images, ≤3
 * videos each optionally lending its soundtrack, ≤3 audios). 720p default,
 * turbo lora path on by default, SPECTRUM extra speedup opt-in (quality may
 * suffer). Prompts follow the canonical H3 spec — 🧠 Draft asks the app's
 * Ollama with the verbatim spec from docs/MINIMAX_H3_PROMPTING.md.
 */
import React, { useCallback, useEffect, useRef, useState } from 'react';

import CharacterImagePicker from './CharacterImagePicker';

const BASE = '/api/h3';

const box: React.CSSProperties = {
  background: '#12151b', border: '1px solid #2a2f3a', borderRadius: 10, padding: 12,
};
const hint: React.CSSProperties = { color: '#8d97a5', fontSize: 12 };
const lbl: React.CSSProperties = { color: '#aeb6c2', fontSize: 12, fontWeight: 600 };
const inp: React.CSSProperties = {
  background: '#0e1116', border: '1px solid #2a2f3a', borderRadius: 6,
  color: '#e6e9ee', padding: '6px 8px', fontSize: 13,
};
const btn: React.CSSProperties = {
  background: '#3b82f6', border: 'none', borderRadius: 6, color: '#fff',
  padding: '8px 14px', fontSize: 13, fontWeight: 600, cursor: 'pointer',
};
const btnSm: React.CSSProperties = {
  background: 'transparent', border: '1px solid #2a2f3a', borderRadius: 6,
  color: '#cbd2dc', padding: '3px 8px', fontSize: 11, cursor: 'pointer',
};
const pill = (on: boolean): React.CSSProperties => ({
  ...btnSm, padding: '6px 12px', fontSize: 12,
  background: on ? '#3b82f6' : 'transparent',
  color: on ? '#fff' : '#cbd2dc', borderColor: on ? '#3b82f6' : '#2a2f3a',
});

/** seconds → "47s" / "3m 12s" / "1h 04m" — render times reads at a glance */
const fmtDur = (s?: number): string => {
  if (!s || s <= 0) return '';
  if (s < 60) return `${Math.round(s)}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m ${String(Math.round(s % 60)).padStart(2, '0')}s`;
  return `${Math.floor(s / 3600)}h ${String(Math.floor((s % 3600) / 60)).padStart(2, '0')}m`;
};

interface WorkerT { id: string; name: string; host: string; is_trainer: boolean }
interface ModeT { key: string; name: string }
interface UpT { id: string; kind: string; orig?: string }
interface RefVidT { up: UpT; use_audio: boolean }
interface JobT {
  id: string; mode: string; label: string; prompt: string; worker: string;
  width: number; height: number; frames: number; duration_s: number;
  turbo: boolean; draft?: boolean; spectrum: boolean; status: string; error?: string | null;
  elapsed_s: number; at: string;
  refs?: { first: boolean; last: boolean; images: number; videos: number; audios: number };
}

export default function VideoLabPanel(): React.ReactElement {
  const [ov, setOv] = useState<any>(null);
  const [mode, setMode] = useState('t2v');
  const [workerId, setWorkerId] = useState('');
  const [prompt, setPrompt] = useState('');
  const [idea, setIdea] = useState('');
  const [dialogue, setDialogue] = useState('');
  const [drafting, setDrafting] = useState(false);
  const [duration, setDuration] = useState(5);
  const [resolution, setResolution] = useState('720p');
  const [aspect, setAspect] = useState('16:9');
  const [sizeFromImage, setSizeFromImage] = useState(true);
  const [turbo, setTurbo] = useState(true);
  // "draftMode" because `draft` is already the 🧠 prompt-drafting function
  const [draftMode, setDraftMode] = useState(false);
  const [spectrum, setSpectrum] = useState(false);
  // v1.275.11: default ON. H3 rounds frames to f%17==5, LTX's upscaler can only
  // carry f=8k+1 and FLOORS -- so a 124f clip comes back 121f, losing the tail
  // and the matching slice of audio. Rendering one H3 step long costs 17 frames
  // (~0.7s) and makes the trim take slack instead. Frames cannot be recovered
  // after the fact, so this is cheap insurance, not a preference.
  const [planUpscale, setPlanUpscale] = useState(true);
  const [refImageSize, setRefImageSize] = useState('match');
  const [firstUp, setFirstUp] = useState<UpT | null>(null);
  const [lastUp, setLastUp] = useState<UpT | null>(null);
  const [refImgs, setRefImgs] = useState<UpT[]>([]);
  const [refVids, setRefVids] = useState<RefVidT[]>([]);
  const [refAuds, setRefAuds] = useState<UpT[]>([]);
  const [jobs, setJobs] = useState<JobT[]>([]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');
  const fileRef = useRef<HTMLInputElement | null>(null);
  const pendingRef = useRef<{ kind: string; slot: string } | null>(null);
  // 📚 v1.277.2 — which slot the character-image picker is filling, or null
  const [charPickSlot, setCharPickSlot] = useState<string | null>(null);
  // 🤖 v1.277.11 — the copyable prompting-agent instructions for external LLMs
  const [llmPrompt, setLlmPrompt] = useState<string | null>(null);
  const [llmCopied, setLlmCopied] = useState(false);

  const loadOv = useCallback(async () => {
    try {
      const r = await fetch(`${BASE}/overview`);
      const j = await r.json();
      setOv(j);
      if (!workerId && j.workers?.length) {
        const tr = j.workers.find((w: WorkerT) => w.is_trainer);
        setWorkerId((tr || j.workers[0]).id);
      }
    } catch (e) { setErr(String((e as Error).message || e)); }
  }, [workerId]);
  const loadJobs = useCallback(async () => {
    try {
      const r = await fetch(`${BASE}/jobs`);
      const j = await r.json();
      setJobs(j.jobs || []);
    } catch { /* ignore */ }
  }, []);
  useEffect(() => { void loadOv(); void loadJobs(); }, [loadOv, loadJobs]);
  useEffect(() => {
    const active = jobs.some((j) => ['queued', 'running', 'downloading'].includes(j.status));
    const t = window.setInterval(loadJobs, active ? 5000 : 30000);
    return () => window.clearInterval(t);
  }, [jobs, loadJobs]);

  const pickFile = (kind: string, slot: string) => {
    pendingRef.current = { kind, slot };
    if (fileRef.current) {
      fileRef.current.accept =
        kind === 'image' ? 'image/*' : kind === 'video' ? 'video/*' : 'audio/*';
      fileRef.current.click();
    }
  };

  const onFile = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files && e.target.files[0];
    const p = pendingRef.current;
    e.target.value = '';
    if (!f || !p) return;
    setErr('');
    const fd = new FormData();
    fd.append('file', f);
    fd.append('kind', p.kind);
    try {
      const r = await fetch(`${BASE}/upload`, { method: 'POST', body: fd });
      const j = await r.json();
      if (!r.ok) throw new Error(j.detail || `${r.status}`);
      const up: UpT = { id: j.id, kind: j.kind, orig: j.orig };
      if (p.slot === 'first') setFirstUp(up);
      else if (p.slot === 'last') setLastUp(up);
      else if (p.slot === 'refimg') setRefImgs((v) => [...v, up]);
      else if (p.slot === 'refvid') setRefVids((v) => [...v, { up, use_audio: false }]);
      else if (p.slot === 'refaud') setRefAuds((v) => [...v, up]);
    } catch (ex) { setErr(String((ex as Error).message || ex)); }
  };

  /** 📚 a picked character image → fetched as a blob → registered as an H3
   *  upload → routed into the same slot the file picker would fill. The
   *  backend only accepts upload ids, so re-uploading the bytes is the
   *  clean same-origin bridge (no new backend surface needed). */
  const useCharImage = async (url: string, name: string) => {
    const slot = charPickSlot;
    setCharPickSlot(null);
    if (!slot) return;
    setErr('');
    try {
      const resp = await fetch(url);
      if (!resp.ok) throw new Error(`image fetch ${resp.status}`);
      const blob = await resp.blob();
      const fd = new FormData();
      fd.append('file', new File([blob], name || 'character.png',
        { type: blob.type || 'image/png' }));
      fd.append('kind', 'image');
      const r = await fetch(`${BASE}/upload`, { method: 'POST', body: fd });
      const j = await r.json();
      if (!r.ok) throw new Error(j.detail || `${r.status}`);
      const up: UpT = { id: j.id, kind: j.kind, orig: name || j.orig };
      if (slot === 'first') setFirstUp(up);
      else if (slot === 'last') setLastUp(up);
      else if (slot === 'refimg') setRefImgs((v) => [...v, up]);
    } catch (ex) { setErr(String((ex as Error).message || ex)); }
  };

  const draft = async () => {
    if (!idea.trim()) { setErr('Give 🧠 Draft an idea first.'); return; }
    setDrafting(true); setErr('');
    try {
      const refsNote = mode === 'ref2v'
        ? `${refImgs.length} image ref(s), ${refVids.length} video ref(s)` +
          `${refVids.some((v) => v.use_audio) ? ' (some with soundtrack)' : ''}, ` +
          `${refAuds.length} audio ref(s)`
        : '';
      const r = await fetch(`${BASE}/draft-prompt`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ mode, idea, duration_s: duration, dialogue, refs_note: refsNote }),
      });
      const j = await r.json();
      if (!r.ok) throw new Error(j.detail || `${r.status}`);
      setPrompt(j.prompt || '');
    } catch (ex) { setErr(String((ex as Error).message || ex)); }
    setDrafting(false);
  };

  const generate = async () => {
    setBusy(true); setErr('');
    try {
      const body = {
        mode, prompt, worker_id: workerId || null, duration_s: duration,
        resolution, aspect, size_from_image: sizeFromImage,
        turbo, draft: draftMode, spectrum, plan_upscale: planUpscale, ref_image_size: refImageSize,
        first_frame: firstUp?.id || null, last_frame: lastUp?.id || null,
        ref_images: refImgs.map((u) => u.id),
        ref_videos: refVids.map((v) => ({ id: v.up.id, use_audio: v.use_audio })),
        ref_audios: refAuds.map((u) => u.id),
        label: '',
      };
      const r = await fetch(`${BASE}/generate`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      const j = await r.json();
      if (!r.ok) throw new Error(j.detail || `${r.status}`);
      void loadJobs();
    } catch (ex) { setErr(String((ex as Error).message || ex)); }
    setBusy(false);
  };

  const delJob = async (id: string) => {
    await fetch(`${BASE}/jobs/${id}`, { method: 'DELETE' });
    void loadJobs();
  };

  const [upSize, setUpSize] = useState(1920);
  const upscale = async (id: string) => {
    setErr('');
    try {
      const r = await fetch(`${BASE}/jobs/${id}/upscale`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ largest_size: upSize }),
      });
      const j = await r.json();
      if (!r.ok) throw new Error(j.detail || `${r.status}`);
      void loadJobs();
    } catch (ex) { setErr(String((ex as Error).message || ex)); }
  };

  const needsFirst = mode === 'i2v' || mode === 'first_last';
  const needsLast = mode === 'first_last' || mode === 'last_frame';
  const caps = ov?.caps || { ref_images: 9, ref_videos: 3, ref_audios: 3 };

  const UpChip = ({ up, onKill, extra }: { up: UpT; onKill: () => void; extra?: React.ReactNode }) => (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6,
                   background: '#0e1116', border: '1px solid #2a2f3a',
                   borderRadius: 6, padding: '3px 8px', fontSize: 11, color: '#cbd2dc' }}>
      {up.kind === 'image'
        ? <img src={`${BASE}/uploads/${up.id}/file`} alt=""
               style={{ width: 26, height: 26, objectFit: 'cover', borderRadius: 4 }} />
        : <span>{up.kind === 'video' ? '🎞' : '🎵'}</span>}
      <span style={{ maxWidth: 140, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
        {up.orig || up.id}
      </span>
      {extra}
      <button style={{ ...btnSm, padding: '0 5px', color: '#ff8a8a' }} onClick={onKill}>✕</button>
    </span>
  );

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      <input ref={fileRef} type="file" style={{ display: 'none' }} onChange={onFile} />

      <div style={{ ...box, display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
        <h3 style={{ margin: 0, fontSize: 15 }}>🎬 Video Lab — MiniMax H3</h3>
        <span style={hint}>
          720p by default · sage attention already on via the .bat · finished renders take ⬆ Upscale (LTX 2.3 enhancer).
        </span>
        <div style={{ flex: 1 }} />
        <button style={btnSm}
                title="the full MiniMax H3 prompting-agent instructions — copy them into ChatGPT/Claude/your own LLM to write video prompts outside the app"
                onClick={() => void (async () => {
                  try {
                    const r = await fetch(`${BASE}/llm-prompt`);
                    const j = await r.json();
                    if (!r.ok) throw new Error(j.detail || `${r.status}`);
                    setLlmCopied(false);
                    setLlmPrompt(j.prompt || '');
                  } catch (ex) { setErr(String((ex as Error).message || ex)); }
                })()}>
          🤖 Prompt for LLMs
        </button>
      </div>
      {err && <div style={{ ...box, color: '#ff8a8a', fontSize: 12 }}>{err}</div>}

      <div style={{ ...box, display: 'flex', gap: 6, flexWrap: 'wrap' }}>
        {(ov?.modes || []).map((m: ModeT) => (
          <button key={m.key} style={pill(mode === m.key)} onClick={() => setMode(m.key)}>{m.name}</button>
        ))}
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'minmax(340px, 1.2fr) minmax(300px, 1fr)', gap: 12 }}>
        {/* left: prompt */}
        <div style={{ ...box, display: 'flex', flexDirection: 'column', gap: 8 }}>
          <span style={lbl}>H3 prompt (official spec format)</span>
          <textarea value={prompt} onChange={(e) => setPrompt(e.target.value)}
                    placeholder={'integrated_multimodal_description: [Shot 1] ...\n\noverall_soundscape: ...\n\nnon_diegetic_music: N/A'}
                    style={{ ...inp, minHeight: 220, fontFamily: 'monospace', fontSize: 12, resize: 'vertical' }} />
          <div style={{ display: 'flex', gap: 6, alignItems: 'center', flexWrap: 'wrap' }}>
            <input value={idea} onChange={(e) => setIdea(e.target.value)}
                   placeholder="…or describe the idea and let the LLM draft it"
                   style={{ ...inp, flex: 1, minWidth: 180 }} />
            <button style={btnSm} disabled={drafting} onClick={draft}>
              {drafting ? '⏳ drafting…' : '🧠 Draft prompt'}
            </button>
          </div>
          <input value={dialogue} onChange={(e) => setDialogue(e.target.value)}
                 placeholder="exact dialogue to preserve word-for-word (optional)"
                 style={inp} />
        </div>

        {/* right: settings + references */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          <div style={{ ...box, display: 'flex', flexDirection: 'column', gap: 8 }}>
            <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
              <span style={lbl}>Worker</span>
              <select value={workerId} onChange={(e) => setWorkerId(e.target.value)} style={inp}>
                {(ov?.workers || []).map((w: WorkerT) => (
                  <option key={w.id} value={w.id}>{w.is_trainer ? '⭐ ' : ''}{w.name} ({w.host})</option>
                ))}
              </select>
            </div>
            <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
              <span style={lbl}>Duration</span>
              <input type="number" min={2} max={15} step={0.5} value={duration}
                     onChange={(e) => setDuration(Number(e.target.value) || 5)}
                     style={{ ...inp, width: 70 }} />
              <span style={hint}>s (H3 trained ~5–15 s)</span>
            </div>
            <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
              <span style={lbl}>Size</span>
              <select value={resolution} onChange={(e) => setResolution(e.target.value)} style={inp}>
                {(ov?.resolutions || ['480p', '720p', '1080p']).map((r: string) => (
                  <option key={r} value={r}>{r}{r === '720p' ? ' (default)' : ''}</option>
                ))}
              </select>
              {needsFirst && (
                <label style={{ ...hint, display: 'flex', gap: 4, alignItems: 'center' }}>
                  <input type="checkbox" checked={sizeFromImage}
                         onChange={(e) => setSizeFromImage(e.target.checked)} />
                  aspect from image
                </label>
              )}
              {(!needsFirst || !sizeFromImage) && (
                <select value={aspect} onChange={(e) => setAspect(e.target.value)} style={inp}>
                  {(ov?.aspects || ['16:9', '9:16', '1:1']).map((a: string) => (
                    <option key={a} value={a}>{a}</option>
                  ))}
                </select>
              )}
            </div>
            <div style={{ display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap' }}>
              <label style={{ ...hint, display: 'flex', gap: 4, alignItems: 'center' }}>
                <input type="checkbox" checked={turbo} onChange={(e) => setTurbo(e.target.checked)} />
                ⚡ Turbo lora (8-step)
              </label>
              {turbo && (
                <label style={{ ...hint, display: 'flex', gap: 4, alignItems: 'center' }}
                       title="the v1.0 turbo lora also runs at 4 steps — roughly half the sampling time, rougher look. For testing an idea, not the final take.">
                  <input type="checkbox" checked={draftMode} onChange={(e) => setDraftMode(e.target.checked)} />
                  🏃 Draft (4-step, ~half the time)
                </label>
              )}
              <label style={{ ...hint, display: 'flex', gap: 4, alignItems: 'center' }}>
                <input type="checkbox" checked={spectrum} onChange={(e) => setSpectrum(e.target.checked)} />
                🌀 SPECTRUM speedup <span style={{ color: '#e0b05e' }}>⚠ quality may suffer</span>
              </label>
              <label style={{ ...hint, display: 'flex', gap: 4, alignItems: 'center' }}
                     title={'H3 frame counts (f%17==5) and the LTX upscaler (f=8k+1) disagree, and '
                            + 'the upscaler FLOORS -- a 124-frame clip comes back 121, losing the '
                            + 'tail and its audio. This renders one H3 step longer so the trim '
                            + 'takes slack instead. Costs ~17 frames of render; nothing when the '
                            + 'count is already on the shared lattice (73, 209, 345...). '
                            + 'Upscaling itself is still a separate manual button.'}>
                <input type="checkbox" checked={planUpscale}
                       onChange={(e) => setPlanUpscale(e.target.checked)} />
                ⬆ Upscale-safe length
                <span style={{ color: '#8a8a8a' }}>(+17f, keeps ⬆ lossless)</span>
              </label>
            </div>
          </div>

          {(needsFirst || needsLast || mode === 'ref2v') && (
            <div style={{ ...box, display: 'flex', flexDirection: 'column', gap: 8 }}>
              <span style={lbl}>References</span>
              {needsFirst && (
                <div style={{ display: 'flex', gap: 6, alignItems: 'center', flexWrap: 'wrap' }}>
                  <span style={hint}>First frame:</span>
                  {firstUp
                    ? <UpChip up={firstUp} onKill={() => setFirstUp(null)} />
                    : <>
                        <button style={btnSm} onClick={() => pickFile('image', 'first')}>➕ image</button>
                        <button style={btnSm} title="pick a character sheet, view or dataset render — with a preview"
                                onClick={() => setCharPickSlot('first')}>📚 character</button>
                      </>}
                </div>
              )}
              {needsLast && (
                <div style={{ display: 'flex', gap: 6, alignItems: 'center', flexWrap: 'wrap' }}>
                  <span style={hint}>Last frame:</span>
                  {lastUp
                    ? <UpChip up={lastUp} onKill={() => setLastUp(null)} />
                    : <>
                        <button style={btnSm} onClick={() => pickFile('image', 'last')}>➕ image</button>
                        <button style={btnSm} title="pick a character sheet, view or dataset render — with a preview"
                                onClick={() => setCharPickSlot('last')}>📚 character</button>
                      </>}
                </div>
              )}
              {mode === 'ref2v' && (
                <>
                  <div style={{ display: 'flex', gap: 6, alignItems: 'center', flexWrap: 'wrap' }}>
                    <span style={hint}>Images ({refImgs.length}/{caps.ref_images}):</span>
                    {refImgs.map((u, i) => (
                      <UpChip key={u.id} up={u}
                              onKill={() => setRefImgs((v) => v.filter((_, k) => k !== i))} />
                    ))}
                    {refImgs.length < caps.ref_images && (
                      <>
                        <button style={btnSm} onClick={() => pickFile('image', 'refimg')}>➕</button>
                        <button style={btnSm} title="pick a character sheet, view or dataset render — with a preview"
                                onClick={() => setCharPickSlot('refimg')}>📚</button>
                      </>
                    )}
                  </div>
                  <div style={{ display: 'flex', gap: 6, alignItems: 'center', flexWrap: 'wrap' }}>
                    <span style={hint}>Videos ({refVids.length}/{caps.ref_videos}, 2–15 s):</span>
                    {refVids.map((v, i) => (
                      <UpChip key={v.up.id} up={v.up}
                              onKill={() => setRefVids((x) => x.filter((_, k) => k !== i))}
                              extra={
                                <label style={{ ...hint, display: 'flex', gap: 3, alignItems: 'center' }}>
                                  <input type="checkbox" checked={v.use_audio}
                                         onChange={(e) => setRefVids((x) => x.map((y, k) =>
                                           k === i ? { ...y, use_audio: e.target.checked } : y))} />
                                  🔊
                                </label>
                              } />
                    ))}
                    {refVids.length < caps.ref_videos && (
                      <button style={btnSm} onClick={() => pickFile('video', 'refvid')}>➕</button>
                    )}
                  </div>
                  <div style={{ display: 'flex', gap: 6, alignItems: 'center', flexWrap: 'wrap' }}>
                    <span style={hint}>Audios ({refAuds.length}/{caps.ref_audios}):</span>
                    {refAuds.map((u, i) => (
                      <UpChip key={u.id} up={u}
                              onKill={() => setRefAuds((v) => v.filter((_, k) => k !== i))} />
                    ))}
                    {refAuds.length < caps.ref_audios && (
                      <button style={btnSm} onClick={() => pickFile('audio', 'refaud')}>➕</button>
                    )}
                  </div>
                  <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
                    <span style={hint}>Ref image fidelity:</span>
                    <select value={refImageSize} onChange={(e) => setRefImageSize(e.target.value)} style={inp}>
                      <option value="match">match (fast)</option>
                      <option value="max">max (best identity, much slower)</option>
                    </select>
                  </div>
                  <span style={hint}>
                    🔊 on a video = also use its soundtrack as an audio reference.
                    Label things in the prompt: &lt;Subject 1&gt;, &lt;Picture 1&gt;, &lt;Video 1&gt;, &lt;Audio 1&gt;.
                  </span>
                </>
              )}
            </div>
          )}

          <button style={{ ...btn, opacity: busy ? 0.6 : 1 }} disabled={busy} onClick={generate}>
            {busy ? '⏳ submitting…' : '🎬 Generate video'}
          </button>
        </div>
      </div>

      {/* jobs */}
      <div style={{ ...box, display: 'flex', flexDirection: 'column', gap: 10 }}>
        <span style={lbl}>Renders</span>
        {!jobs.length && <span style={hint}>Nothing yet — hit 🎬 Generate video.</span>}
        {jobs.map((j) => (
          <div key={j.id} style={{ border: '1px solid #2a2f3a', borderRadius: 8, padding: 10,
                                   display: 'flex', flexDirection: 'column', gap: 6 }}>
            <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
              <b style={{ color: '#e6e9ee', fontSize: 13 }}>{j.label}</b>
              <span style={hint}>{j.mode} · {j.width}×{j.height} · {j.duration_s}s ({j.frames}f)
                · {j.turbo ? (j.draft ? '🏃draft 4-step' : '⚡turbo') : '20-step'}{j.spectrum ? ' · 🌀spectrum' : ''} · {j.worker}</span>
              <span style={{
                fontSize: 12, fontWeight: 700,
                color: j.status === 'done' ? '#5ee08a'
                  : j.status === 'error' ? '#ff8a8a' : '#9cc2ff',
              }}>
                {/* the run TIME is part of the record (the standing rule) —
                    live while rendering, final once done, kept for benchmarks */}
                {j.status === 'done' ? `✓ done${j.elapsed_s ? ` · took ${fmtDur(j.elapsed_s)}` : ''}`
                  : j.status === 'error' ? `✕ error${j.elapsed_s ? ` · after ${fmtDur(j.elapsed_s)}` : ''}`
                    : `⏳ ${j.status} ${fmtDur(j.elapsed_s)}`}
              </span>
              <div style={{ flex: 1 }} />
              {j.status === 'done' && (
                <a href={`${BASE}/media/${j.id}?download=1`} style={{ ...btnSm, textDecoration: 'none' }}>⬇ mp4</a>
              )}
              {j.status === 'done' && j.mode !== 'upscale' && (
                <span style={{ display: 'inline-flex', gap: 4, alignItems: 'center' }}>
                  <button style={btnSm} onClick={() => upscale(j.id)}>⬆ Upscale (LTX 2.3)</button>
                  <select value={upSize} onChange={(e) => setUpSize(Number(e.target.value))}
                          style={{ ...inp, padding: '2px 4px', fontSize: 11 }}>
                    <option value={1280}>→720p</option>
                    <option value={1920}>→1080p</option>
                    <option value={2560}>→1440p</option>
                  </select>
                </span>
              )}
              <button style={{ ...btnSm, color: '#ff8a8a' }} onClick={() => delJob(j.id)}>🗑</button>
            </div>
            {j.status === 'error' && <span style={{ color: '#ff8a8a', fontSize: 12 }}>{j.error}</span>}
            {j.status === 'done' && (
              <video controls preload="metadata" src={`${BASE}/media/${j.id}`}
                     style={{ maxWidth: 640, width: '100%', borderRadius: 6, background: '#000' }} />
            )}
            <details>
              <summary style={{ ...hint, cursor: 'pointer' }}>prompt</summary>
              <pre style={{ ...hint, whiteSpace: 'pre-wrap', margin: 0 }}>{j.prompt}</pre>
            </details>
          </div>
        ))}
      </div>

      {charPickSlot && (
        <CharacterImagePicker onPick={(url, name) => void useCharImage(url, name)}
                              onClose={() => setCharPickSlot(null)} />
      )}

      {llmPrompt !== null && (
        <div style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.72)',
                      zIndex: 60, display: 'flex', alignItems: 'center',
                      justifyContent: 'center', padding: 18 }}
             onClick={() => setLlmPrompt(null)}>
          <div style={{ background: '#0b0e13', border: '1px solid #2a2f3a',
                        borderRadius: 12, width: 'min(860px, 96vw)',
                        height: 'min(720px, 92vh)', display: 'flex',
                        flexDirection: 'column', padding: 14, gap: 10 }}
               onClick={(e) => e.stopPropagation()}>
            <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
              <b style={{ color: '#e6e9ee', fontSize: 14 }}>🤖 MiniMax H3 — prompt for your own LLM</b>
              <span style={hint}>paste this as the system prompt / first message, then describe your video</span>
              <div style={{ flex: 1 }} />
              <button style={{ ...btn, background: llmCopied ? '#166534' : '#3b82f6' }}
                      onClick={() => void (async () => {
                        try {
                          await navigator.clipboard.writeText(llmPrompt);
                          setLlmCopied(true);
                        } catch {
                          // clipboard API can be denied — fall back to select-all
                          const el = document.getElementById('llm-prompt-pre');
                          if (el) {
                            const rng = document.createRange();
                            rng.selectNodeContents(el);
                            const sel = window.getSelection();
                            sel?.removeAllRanges(); sel?.addRange(rng);
                          }
                        }
                      })()}>
                {llmCopied ? '✓ Copied' : '📋 Copy all'}
              </button>
              <button style={btnSm} onClick={() => setLlmPrompt(null)}>✕</button>
            </div>
            <pre id="llm-prompt-pre"
                 style={{ flex: 1, overflow: 'auto', margin: 0, padding: 12,
                          background: '#0e1116', border: '1px solid #2a2f3a',
                          borderRadius: 8, color: '#cbd2dc', fontSize: 12,
                          whiteSpace: 'pre-wrap', userSelect: 'text' }}>
              {llmPrompt}
            </pre>
          </div>
        </div>
      )}
    </div>
  );
}
