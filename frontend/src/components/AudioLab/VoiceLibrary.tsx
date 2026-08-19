/**
 * 🎤 THE VOICE LIBRARY (v1.277.38) — save a voice, cut it to size, look it up.
 *
 *   ✂  a long upload is TRIMMED here (the cap is 12 s because ComfyUI-F5-TTS
 *      hard-cuts its reference there, mid-word).
 *   🎤 saved voices in a DROPDOWN, not a wall of chips.
 *   🪪 a details view: made-on, source clip, every render this voice produced,
 *      and which projects and STORIES those landed in.
 *
 * **The order changed in .38, and it matters.** The panel used to demand the
 * transcript BEFORE the upload — i.e. asked him to transcribe a window that did
 * not exist yet, since the clip is cut server-side from whatever he picks. Now:
 * pick the file → it uploads (with progress) → the cut clip appears with a
 * player → he listens and types the words spoken IN THAT WINDOW. A voice with
 * no transcript is saved and clearly marked; `tts/generate` refuses it with the
 * reason, because in F5 the transcript is the ALIGNMENT, not a label.
 *
 * ⚠ Upload uses XMLHttpRequest, not fetch: fetch cannot report upload progress,
 * and a 40 MB sample with no feedback looks exactly like a dead button — which
 * is precisely what he reported.
 */
import { useCallback, useEffect, useRef, useState } from 'react';

const B = '/api/audio-lab';

async function jj<T>(r: Response): Promise<T> {
  if (!r.ok) {
    let d = ''; try { d = (await r.json()).detail || ''; } catch { /* */ }
    throw new Error(d || `${r.status}`);
  }
  return r.json() as Promise<T>;
}

export type VoiceT = {
  id: string; name: string; transcript: string; at?: string;
  ext?: string; notes?: string;
  clip_seconds?: number; source_seconds?: number; source_filename?: string;
  source_bytes?: number; clip_bytes?: number; has_source?: boolean;
  over_cap?: boolean; needs_transcript?: boolean; ready?: boolean;
  trim?: { start: number; seconds: number; auto?: boolean } | null;
};
type UseT = {
  kind: string; project?: string; project_id?: string; story?: string;
  world?: string; story_id?: string; world_id?: string; at?: string;
};
type DetailT = VoiceT & {
  renders?: { id: string; label?: string; status?: string; at?: string;
    elapsed_s?: number; file?: string; used_in?: UseT[] }[];
  render_count?: number; projects?: UseT[]; stories?: UseT[]; cap_seconds?: number;
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

const secs = (n?: number) => (n ? `${Number(n).toFixed(1)}s` : '—');
type PresetT = { id: string; label: string; note: string };
const round2 = (n: number) => Math.round(n * 100) / 100;
const nudgeCls =
  'px-1.5 py-1 rounded text-xs bg-gray-800 hover:bg-gray-700 text-gray-300 ' +
  'border border-gray-700 disabled:opacity-40';

/** POST a FormData with real upload progress. fetch() cannot do this. */
function upload<T>(url: string, fd: FormData, onPct: (p: number) => void): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open('POST', url);
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) onPct(Math.round((e.loaded / e.total) * 100));
    };
    xhr.onload = () => {
      let body: any = {};
      try { body = JSON.parse(xhr.responseText || '{}'); } catch { /* */ }
      if (xhr.status >= 200 && xhr.status < 300) resolve(body as T);
      else reject(new Error(body?.detail || `${xhr.status}`));
    };
    xhr.onerror = () => reject(new Error('the upload failed (network)'));
    xhr.send(fd);
  });
}

export default function VoiceLibrary({
  voices, voiceId, setVoiceId, cap, guide, reload, setMsg,
}: {
  voices: VoiceT[];
  voiceId: string;
  setVoiceId: (v: string) => void;
  cap: number;
  guide: string;
  reload: () => void | Promise<void>;
  setMsg: (s: string) => void;
}) {
  const fileRef = useRef<HTMLInputElement>(null);
  const txRef = useRef<HTMLTextAreaElement>(null);
  const [name, setName] = useState('');
  const [start, setStart] = useState<number | ''>('');   // '' = find it for me
  const [span, setSpan] = useState<number>(cap);
  const [busy, setBusy] = useState(false);
  const [pct, setPct] = useState(0);
  const [note, setNote] = useState('');                  // inline receipt / error
  const [noteBad, setNoteBad] = useState(false);
  const [open, setOpen] = useState(false);               // details drawer
  const [detail, setDetail] = useState<DetailT | null>(null);
  const [trimStart, setTrimStart] = useState(0);
  const [trimSpan, setTrimSpan] = useState(cap);
  const [tx, setTx] = useState('');                      // transcript editor
  const [txDirty, setTxDirty] = useState(false);
  // 🔁 cache-buster: the clip keeps its NAME across a re-trim, so without this
  // the <audio> element happily replays the pre-trim bytes and the window looks
  // like it never changed
  const [rev, setRev] = useState(0);
  // 🎨 the voice FACTORY — presets that need no recording at all
  const [presets, setPresets] = useState<PresetT[]>([]);
  const [kReady, setKReady] = useState<boolean | null>(null);
  const [kNote, setKNote] = useState('');
  const [kOpen, setKOpen] = useState(false);
  const [kName, setKName] = useState('');
  const [kPreset, setKPreset] = useState('af_heart');
  const [kBlendOn, setKBlendOn] = useState(false);
  const [kPresetB, setKPresetB] = useState('am_michael');
  const [kBlend, setKBlend] = useState(0.6);
  const [kText, setKText] = useState('');
  const [kAudition, setKAudition] = useState('');   // the preview URL playing
  const [kAll, setKAll] = useState(false);          // the full audition list

  /** ▶ hear a speaker (or the current blend) — cached server-side, so the
   *  second click on the same voice is instant. */
  const auditionUrl = (id: string, withBlend = false) => {
    const q = new URLSearchParams({ preset: id });
    if (withBlend && kBlendOn) {
      q.set('preset_b', kPresetB); q.set('blend', String(kBlend));
    }
    return `${B}/tts/kokoro/preview?${q.toString()}`;
  };

  useEffect(() => {
    fetch(`${B}/tts/kokoro/presets`)
      .then(r => jj<{ ready: boolean; note: string; presets: PresetT[]; sample_text: string }>(r))
      .then(d => {
        setPresets(d.presets || []); setKReady(d.ready); setKNote(d.note || '');
        setKText(t => t || d.sample_text || '');
      })
      .catch(() => setKReady(false));
  }, []);

  const createFromPreset = async () => {
    if (!kName.trim()) { say('⚠ name the voice first', true); return; }
    setBusy(true);
    say(`🎨 rendering a reference with ${kPreset}${kBlendOn ? ` + ${kPresetB}` : ''}… `
      + '(the first one also downloads the model, ~1 min)');
    try {
      const v = await fetch(`${B}/tts/kokoro/create`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: kName, preset: kPreset,
          preset_b: kBlendOn ? kPresetB : '', blend: kBlendOn ? kBlend : 1.0,
          text: kText }),
      }).then(r => jj<VoiceT & { summary?: string }>(r));
      say(`✅ ${v.summary || 'created'}`);
      setKName('');
      await reload();
      setVoiceId(v.id);
      setOpen(true);
    } catch (e) { say(`⚠ ${e}`, true); }
    setBusy(false);
  };

  const current = voices.find(v => v.id === voiceId) || null;

  const loadDetail = useCallback(async (vid: string) => {
    if (!vid) { setDetail(null); return; }
    try {
      const d = await fetch(`${B}/tts/voices/${vid}`).then(r => jj<DetailT>(r));
      setDetail(d);
      setTrimStart(d.trim?.start ?? 0);
      setTrimSpan(d.trim?.seconds ?? Math.min(cap, d.clip_seconds || cap));
      setTx(d.transcript || '');
      setTxDirty(false);
    } catch (e) { setMsg(`⚠ ${e}`); }
  }, [cap, setMsg]);

  useEffect(() => { if (open && voiceId) void loadDetail(voiceId); },
    [open, voiceId, loadDetail, rev]);

  const say = (s: string, bad = false) => { setNote(s); setNoteBad(bad); };

  // ── the trim window, edited three equivalent ways ────────────────────────
  // start / end / length are one window; editing any of them has to keep the
  // other two honest, or the numbers on screen stop describing the audio.
  // ⚠ Steps are 0.05s deliberately: 0.5s is wider than a syllable, which is
  // exactly how a reference ends up cut through the middle of a word.
  const srcLen = detail?.source_seconds || 0;
  const clampSpan = (v: number) =>
    Math.max(0.2, Math.min(cap, srcLen ? Math.min(v, srcLen - trimStart) : v));
  const setStartClamped = (v: number) => {
    const s = Math.max(0, srcLen ? Math.min(v, Math.max(0, srcLen - 0.2)) : v);
    setTrimStart(round2(s));
    setTrimSpan(round2(Math.max(0.2, Math.min(trimSpan, cap,
      srcLen ? srcLen - s : trimSpan))));
  };
  const setSpanClamped = (v: number) => setTrimSpan(round2(clampSpan(v)));
  const setEnd = (v: number) => setTrimSpan(round2(clampSpan(v - trimStart)));
  const nudgeStart = (d: number) => setStartClamped(round2(trimStart + d));
  const nudgeEnd = (d: number) => setEnd(round2(trimStart + trimSpan + d));

  const add = async (f: globalThis.File) => {
    if (!name.trim()) {
      say('⚠ give the voice a name first — then pick the sample.', true);
      return;
    }
    setBusy(true); setPct(0);
    say(`⏳ uploading ${f.name} (${(f.size / 1048576).toFixed(1)} MB)…`);
    const fd = new FormData();
    fd.append('name', name); fd.append('transcript', '');
    fd.append('file', f);
    fd.append('trim_start', String(start === '' ? -1 : start));
    fd.append('trim_seconds', String(span || 0));
    try {
      const v = await upload<VoiceT & { summary?: string; next?: string }>(
        `${B}/tts/voices`, fd,
        p => { setPct(p); if (p >= 100) say(`⏳ ${f.name} sent — cutting the reference…`); });
      say(`✅ ${v.summary || 'added'} ${v.next || ''}`);
      setName('');
      await reload();
      setVoiceId(v.id);
      setOpen(true);                       // the clip + transcript box, right here
      setTimeout(() => txRef.current?.focus(), 300);
    } catch (e) {
      say(`⚠ ${e}`, true);
      setMsg(`⚠ ${e}`);
    }
    setBusy(false); setPct(0);
  };

  const saveTx = async () => {
    if (!voiceId) return;
    setBusy(true);
    try {
      await fetch(`${B}/tts/voices/${voiceId}/update`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ transcript: tx }),
      }).then(r => jj(r));
      say('✅ transcript saved — this voice is ready to speak');
      setTxDirty(false);
      await reload();
      await loadDetail(voiceId);
    } catch (e) { say(`⚠ ${e}`, true); }
    setBusy(false);
  };

  const retrim = async () => {
    if (!voiceId) return;
    setBusy(true);
    try {
      const r = await fetch(`${B}/tts/voices/${voiceId}/trim`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ start: round2(trimStart), seconds: round2(trimSpan) }),
      }).then(x => jj<VoiceT>(x));
      say(`✂ re-cut: ${secs(r.clip_seconds)} from ${trimStart.toFixed(1)}s — `
        + 'play it and check the transcript still matches this window');
      setRev(n => n + 1);
      await reload();
    } catch (e) { say(`⚠ ${e}`, true); }
    setBusy(false);
  };

  return (
    <div className="border border-gray-800 rounded p-3">
      <input ref={fileRef} type="file" accept="audio/*" className="hidden"
             onChange={e => { const f = e.target.files?.[0]; e.target.value = ''; if (f) void add(f); }} />

      {/* ── pick ─────────────────────────────────────────────── */}
      <div className="flex items-center gap-2 flex-wrap">
        <span className="text-xs font-semibold text-emerald-300">🎤 Voice</span>
        <select className="bg-gray-900 border border-gray-700 rounded px-2 py-1.5 text-sm text-gray-100 min-w-[18rem]"
                value={voiceId} onChange={e => setVoiceId(e.target.value)}>
          <option value="">— pick a saved voice —</option>
          {voices.map(v => (
            <option key={v.id} value={v.id}>
              {v.needs_transcript ? '⚠ ' : ''}{v.name}
              {v.clip_seconds ? ` · ${secs(v.clip_seconds)}` : ''}
              {v.needs_transcript ? ' · needs a transcript' : ''}
              {v.over_cap ? ' · too long' : ''}
            </option>
          ))}
        </select>
        <button className={btnCls} disabled={!voiceId}
                onClick={() => setOpen(o => !o)}>
          {open ? '▾ Hide details' : '🪪 Details, trim & transcript'}
        </button>
        {current?.needs_transcript && (
          <span className="text-[11px] text-amber-400">
            ⚠ no transcript yet — open Details, play the clip, type what is said
          </span>
        )}
        {current?.over_cap && (
          <span className="text-[11px] text-amber-400">
            ⚠ {secs(current.clip_seconds)} reference — F5 cuts at {cap}s, mid-word. Trim it.
          </span>
        )}
      </div>

      {/* inline status — right where the action is, not in a corner */}
      {(note || busy) && (
        <div className={`mt-2 text-[11px] rounded border px-2.5 py-2 ${
          noteBad ? 'border-red-800 bg-red-950/40 text-red-200'
                  : 'border-emerald-900 bg-emerald-950/30 text-emerald-200'}`}>
          {note}
          {busy && pct > 0 && pct < 100 && (
            <div className="mt-1.5 h-1.5 bg-gray-800 rounded overflow-hidden">
              <div className="h-full bg-emerald-500 transition-all"
                   style={{ width: `${pct}%` }} />
            </div>
          )}
        </div>
      )}

      {/* ── details / trim / transcript ───────────────────────── */}
      {open && detail && (
        <div className="mt-3 border border-gray-800 rounded p-3 bg-gray-900/40 space-y-3">
          <div className="flex flex-wrap gap-x-5 gap-y-1 text-[11px] text-gray-400">
            <span>made <b className="text-gray-300">{detail.at || '—'}</b></span>
            <span>reference <b className="text-gray-300">{secs(detail.clip_seconds)}</b></span>
            <span>source <b className="text-gray-300">{secs(detail.source_seconds)}</b>
              {detail.source_filename ? ` · ${detail.source_filename}` : ''}</span>
            <span>renders <b className="text-gray-300">{detail.render_count ?? 0}</b></span>
          </div>

          {/* ▶ the clip, then the words — in that order, deliberately */}
          <div className="border border-emerald-900/60 rounded p-2.5 bg-gray-950/40">
            <div className="text-[11px] text-emerald-300 font-semibold mb-1">
              ▶ 1. Listen to the reference F5 will actually hear
              {detail.trim && (
                <span className="text-gray-500 font-normal">
                  {' '}· cut from {detail.trim.start.toFixed(1)}s, {secs(detail.clip_seconds)} long
                </span>
              )}
            </div>
            <audio controls className="w-full h-8"
                   src={`${B}/tts/voices/${detail.id}/audio?which=clip&r=${rev}`} />
            <div className="text-[11px] text-emerald-300 font-semibold mt-3 mb-1">
              ✍ 2. Type the words spoken in THAT clip — exactly
            </div>
            <textarea ref={txRef} className={`${inputCls} font-mono`} rows={3} value={tx}
                      placeholder="word for word, punctuation included — this is what F5 aligns the audio to"
                      onChange={e => { setTx(e.target.value); setTxDirty(true); }} />
            <div className="flex items-center gap-2 mt-1.5">
              <button className={btnGo} disabled={busy || !txDirty || !tx.trim()}
                      onClick={() => void saveTx()}>
                {busy ? '⏳' : '💾 Save transcript'}
              </button>
              {detail.needs_transcript && (
                <span className="text-[11px] text-amber-400">
                  this voice cannot render until this is filled in
                </span>
              )}
              {!detail.needs_transcript && !txDirty && (
                <span className="text-[11px] text-gray-500">saved</span>
              )}
            </div>
          </div>

          {detail.has_source && (
            <div>
              <div className="text-[11px] text-gray-500 mb-1">
                ▶ the full upload, for reference ({secs(detail.source_seconds)})
              </div>
              <audio controls className="w-full h-8"
                     src={`${B}/tts/voices/${detail.id}/audio?which=source`} />
            </div>
          )}

          {/* ✂ re-cut, always from the untouched source */}
          {detail.has_source ? (
            <div className="border-t border-gray-800 pt-3">
              <div className="text-xs font-semibold text-emerald-300 mb-1">✂ Move the window</div>
              <div className="text-[11px] text-gray-500 mb-2">
                Cut a new window out of the original upload — always from the
                source, so adjusting twice does not shrink it twice. Max {cap}s.
                Nudge in 0.05s steps so the cut lands between words, not through
                one. Re-cut, play it, then fix the transcript above if the words
                changed.
              </div>
              <div className="flex gap-3 items-end flex-wrap">
                <div>
                  <label className="text-[11px] text-gray-500">start (s)</label>
                  <div className="flex items-center gap-1">
                    <button className={nudgeCls} disabled={busy}
                            onClick={() => nudgeStart(-0.05)} title="−0.05s">◀</button>
                    <input type="number" min={0} step={0.05} className={`${inputCls} w-24`}
                           value={trimStart}
                           onChange={e => setStartClamped(Number(e.target.value) || 0)} />
                    <button className={nudgeCls} disabled={busy}
                            onClick={() => nudgeStart(0.05)} title="+0.05s">▶</button>
                  </div>
                </div>
                <div>
                  <label className="text-[11px] text-gray-500">end (s)</label>
                  <div className="flex items-center gap-1">
                    <button className={nudgeCls} disabled={busy}
                            onClick={() => nudgeEnd(-0.05)} title="−0.05s">◀</button>
                    <input type="number" min={0} step={0.05} className={`${inputCls} w-24`}
                           value={round2(trimStart + trimSpan)}
                           onChange={e => setEnd(Number(e.target.value) || 0)} />
                    <button className={nudgeCls} disabled={busy}
                            onClick={() => nudgeEnd(0.05)} title="+0.05s">▶</button>
                  </div>
                </div>
                <div>
                  <label className="text-[11px] text-gray-500">length (s)</label>
                  <input type="number" min={0.5} max={cap} step={0.05} className={`${inputCls} w-24`}
                         value={trimSpan}
                         onChange={e => setSpanClamped(Number(e.target.value) || 0)} />
                </div>
                <button className={btnCls} disabled={busy} onClick={() => void retrim()}>
                  {busy ? '⏳' : '✂ Re-cut'}
                </button>
              </div>
              <div className="text-[11px] text-gray-500 mt-1.5">
                window <b className="text-gray-300">
                  {trimStart.toFixed(2)}s → {round2(trimStart + trimSpan).toFixed(2)}s
                </b> · {trimSpan.toFixed(2)}s of {secs(detail.source_seconds)}
                {trimSpan >= cap - 0.001 && (
                  <span className="text-amber-400/90"> · at the {cap}s ceiling —
                    move the start to move the end</span>
                )}
              </div>
            </div>
          ) : (
            <div className="text-[11px] text-amber-400/80 border-t border-gray-800 pt-3">
              No stored source for this voice (added before the library kept
              one) — re-add it from the original file to be able to trim.
            </div>
          )}

          {/* 🪪 what this voice has done */}
          <div className="border-t border-gray-800 pt-3">
            <div className="text-xs font-semibold text-emerald-300 mb-1">
              🪪 Made with this voice
            </div>
            {!detail.renders?.length && (
              <div className="text-[11px] text-gray-600">nothing rendered yet</div>
            )}
            <div className="space-y-1">
              {(detail.renders || []).slice(0, 12).map(r => (
                <div key={r.id} className="text-[11px] text-gray-400 flex flex-wrap items-center gap-2">
                  <span className={r.status === 'done' ? 'text-green-400' : 'text-gray-500'}>
                    {r.status === 'done' ? '✅' : r.status === 'error' ? '❌' : '⏳'}
                  </span>
                  <span className="text-gray-300 truncate max-w-[22rem]">{r.label || r.id}</span>
                  <span className="text-gray-600">{r.at}</span>
                  {r.file && (
                    <audio controls className="h-6" src={`${B}/media/${r.id}`} />
                  )}
                  {(r.used_in || []).map((u, i) => (
                    <span key={i} className="px-1.5 py-0.5 rounded bg-gray-800 border border-gray-700">
                      {u.kind === 'story' ? `🌍 ${u.story}` : `🎬 ${u.project}`}
                    </span>
                  ))}
                </div>
              ))}
            </div>
            {(detail.stories?.length || detail.projects?.length) ? (
              <div className="text-[11px] text-gray-500 mt-2">
                used in {detail.projects?.length || 0} project(s)
                {' · '}{detail.stories?.length || 0} story/stories
              </div>
            ) : null}
          </div>

          <div className="flex gap-2">
            <button className="px-2 py-1 rounded text-xs border border-red-800 text-red-300 hover:bg-red-950/50"
                    onClick={async () => {
                      if (!window.confirm(`Delete voice "${detail.name}"? Its renders stay.`)) return;
                      await fetch(`${B}/tts/voices/${detail.id}/delete`, { method: 'POST' });
                      setVoiceId(''); setOpen(false); say('voice deleted'); await reload();
                    }}>🗑 Delete voice</button>
          </div>
        </div>
      )}

      {/* ── 🎨 invent one, no recording ───────────────────────── */}
      <div className="mt-3 border-t border-gray-800 pt-3">
        <div className="flex items-center gap-2 flex-wrap">
          <button className={btnCls} onClick={() => setKOpen(o => !o)}>
            🎨 {kOpen ? 'Hide' : 'Create a voice from scratch'}
          </button>
          <span className="text-[11px] text-gray-500">
            no recording needed — {presets.length || 54} built-in speakers you can also blend
          </span>
          {kReady === false && (
            <span className="text-[11px] text-amber-400">⚠ not installed here</span>
          )}
        </div>
        {kOpen && (
          <div className="mt-2 border border-gray-800 rounded p-3 bg-gray-900/40 space-y-2">
            {kReady === false ? (
              <div className="text-[11px] text-amber-400">{kNote}</div>
            ) : (
              <>
                <div className="text-[11px] text-gray-500">
                  Picks a built-in speaker, speaks a reference line with it, and
                  files it as a normal voice — <b>with the transcript already
                  correct</b>, because we chose the words. F5 then clones it like
                  any other reference.
                </div>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
                  <div>
                    <label className="text-[11px] text-gray-500">name</label>
                    <input className={inputCls} placeholder="what to call this voice…"
                           value={kName} disabled={busy}
                           onChange={e => setKName(e.target.value)} />
                  </div>
                  <div>
                    <label className="text-[11px] text-gray-500">speaker</label>
                    <div className="flex gap-1 items-center">
                      <select className={inputCls} value={kPreset} disabled={busy}
                              onChange={e => { setKPreset(e.target.value); setKAudition(''); }}>
                        {presets.map(p => (
                          <option key={p.id} value={p.id}>{p.label} — {p.note}</option>
                        ))}
                      </select>
                      <button className={btnCls} title="hear this speaker"
                              onClick={() => setKAudition(auditionUrl(kPreset))}>▶</button>
                    </div>
                  </div>
                </div>
                <label className="flex items-center gap-2 text-xs text-gray-300">
                  <input type="checkbox" checked={kBlendOn} disabled={busy}
                         onChange={e => setKBlendOn(e.target.checked)} />
                  🔀 blend with a second speaker — makes a voice that exists nowhere else
                </label>
                {kBlendOn && (
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
                    <div>
                      <label className="text-[11px] text-gray-500">second speaker</label>
                      <select className={inputCls} value={kPresetB} disabled={busy}
                              onChange={e => setKPresetB(e.target.value)}>
                        {presets.map(p => (
                          <option key={p.id} value={p.id}>{p.label}</option>
                        ))}
                      </select>
                    </div>
                    <div>
                      <label className="text-[11px] text-gray-500">
                        mix — {Math.round(kBlend * 100)}% first / {Math.round((1 - kBlend) * 100)}% second
                      </label>
                      <input type="range" min={0} max={1} step={0.05} className="w-full"
                             value={kBlend} disabled={busy}
                             onChange={e => setKBlend(Number(e.target.value))} />
                    </div>
                  </div>
                )}
                {/* ▶ the audition player — one element, whatever was clicked last */}
                <div className="flex items-center gap-2 flex-wrap">
                  <button className={btnCls}
                          onClick={() => setKAudition(auditionUrl(kPreset, true))}>
                    ▶ Hear {kBlendOn ? 'the blend' : 'this speaker'}
                  </button>
                  <button className={btnCls} onClick={() => setKAll(a => !a)}>
                    {kAll ? '▾ Hide the list' : '🎧 Audition all speakers'}
                  </button>
                  {kAudition && (
                    <audio controls autoPlay className="h-8 flex-1 min-w-[14rem]"
                           src={kAudition} />
                  )}
                </div>
                <div className="text-[11px] text-gray-600">
                  The first play of any speaker renders it (a second or two); after
                  that it is cached and instant. Blends are auditioned too — that is
                  where the voices stop sounding stock.
                </div>

                {kAll && (
                  <div className="max-h-64 overflow-y-auto border border-gray-800 rounded">
                    {presets.map(p => (
                      <div key={p.id}
                           className={`flex items-center gap-2 px-2 py-1 text-xs border-b border-gray-800/60 ${
                             kPreset === p.id ? 'bg-emerald-900/30' : ''}`}>
                        <button className={nudgeCls} title="hear it"
                                onClick={() => setKAudition(auditionUrl(p.id))}>▶</button>
                        <button className="text-left flex-1 text-gray-200 hover:text-emerald-300"
                                onClick={() => { setKPreset(p.id); setKAudition(auditionUrl(p.id)); }}>
                          {p.label}
                          <span className="text-gray-600"> — {p.note}</span>
                        </button>
                        {kBlendOn && (
                          <button className={nudgeCls} title="use as the blend partner"
                                  onClick={() => { setKPresetB(p.id);
                                    setKAudition(auditionUrl(kPreset, true)); }}>🔀</button>
                        )}
                      </div>
                    ))}
                  </div>
                )}

                <div>
                  <label className="text-[11px] text-gray-500">
                    the reference line — kept as the transcript, so pick words with
                    a good spread of sounds
                  </label>
                  <textarea className={inputCls} rows={3} value={kText} disabled={busy}
                            onChange={e => setKText(e.target.value)} />
                </div>
                <button className={btnGo} disabled={busy || !kName.trim()}
                        onClick={() => void createFromPreset()}>
                  {busy ? '⏳ rendering…' : '🎨 Create the voice'}
                </button>
              </>
            )}
          </div>
        )}
      </div>

      {/* ── add ──────────────────────────────────────────────── */}
      <div className="mt-3 border-t border-gray-800 pt-3">
        <div className="text-xs font-semibold text-emerald-300 mb-1">➕ Add a voice</div>
        <div className="text-[11px] text-gray-500 mb-2">{guide}</div>
        <div className="flex gap-2 items-end flex-wrap">
          <div className="flex-1 min-w-[12rem]">
            <label className="text-[11px] text-gray-500">name</label>
            <input className={inputCls} placeholder="whose voice is this…" value={name}
                   disabled={busy}
                   onChange={e => setName(e.target.value)} />
          </div>
          <div>
            <label className="text-[11px] text-gray-500">cut from (s)</label>
            <input type="number" min={0} step={0.1} className={`${inputCls} w-24`}
                   placeholder="auto" value={start} disabled={busy}
                   onChange={e => setStart(e.target.value === '' ? '' : Math.max(0, Number(e.target.value) || 0))} />
          </div>
          <div>
            <label className="text-[11px] text-gray-500">length (s)</label>
            <input type="number" min={0.5} max={cap} step={0.5} className={`${inputCls} w-24`}
                   value={span} disabled={busy}
                   onChange={e => setSpan(Math.min(cap, Number(e.target.value) || cap))} />
          </div>
          <button className={btnGo} disabled={busy || !name.trim()}
                  onClick={() => fileRef.current?.click()}
                  title="pick the reference audio — any length, it gets cut to the window">
            {busy ? (pct > 0 && pct < 100 ? `⏳ uploading ${pct}%` : '⏳ working…')
                  : '📎 Pick a sample & SAVE the voice'}
          </button>
        </div>
        <div className="text-[11px] text-gray-600 mt-1.5">
          There is no separate Save button here on purpose: <b>picking the file
          saves the voice</b> (it uploads, gets cut, and appears in the dropdown
          above). <b>The transcript comes after</b>, once you have heard the cut
          — it has its own 💾 Save in Details. Leave <b>cut from</b> blank and the first
          non-silent moment is found for you; anything longer than {cap}s is cut
          automatically, and the whole upload is kept so the window can move later.
        </div>
      </div>
    </div>
  );
}
