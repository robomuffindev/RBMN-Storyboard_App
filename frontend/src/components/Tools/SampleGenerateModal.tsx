import { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import {
  X, Sparkles, Loader2, Check, Wand2, ImageOff, RotateCcw,
} from 'lucide-react';
import { toolsApi, type SampleGenStatusT } from './toolsApi';
import { ImageLightbox } from '@/components/CharacterStudio/p2Shared';
import { handleImgError } from '@/utils/brokenImage';

const MODELS = [
  { value: 'z_image', label: 'Z-Image Turbo' },
  { value: 'krea2', label: 'Krea 2 Turbo' },
  { value: 'anima', label: 'Anima (anime)' },
  { value: 'klein', label: 'Klein' },
];

interface Props {
  kind: 'pose' | 'expression';
  dwposeAvailable?: boolean;
  onClose: () => void;
  onCommitted: () => void;
}

export default function SampleGenerateModal({ kind, dwposeAvailable, onClose, onCommitted }: Props) {
  const isPose = kind === 'pose';
  const [prompt, setPrompt] = useState('');
  const [model, setModel] = useState('z_image');
  const [count, setCount] = useState(4);
  const [width, setWidth] = useState(isPose ? 768 : 768);
  const [height, setHeight] = useState(isPose ? 1152 : 896);
  const [negative, setNegative] = useState('');
  const [isolate, setIsolate] = useState(true);
  const [seed, setSeed] = useState('');

  const [phase, setPhase] = useState<'form' | 'running' | 'review'>('form');
  const [genId, setGenId] = useState<string | null>(null);
  const [status, setStatus] = useState<SampleGenStatusT | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [lightbox, setLightbox] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  // Commit fields
  const [category, setCategory] = useState('');
  const [name, setName] = useState('');
  const [tags, setTags] = useState('');
  const [naturalPrompt, setNaturalPrompt] = useState('');
  const [detectHands, setDetectHands] = useState(true);
  const [detectFace, setDetectFace] = useState(false);
  const [committing, setCommitting] = useState(false);

  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  useEffect(() => () => { if (pollRef.current) clearInterval(pollRef.current); }, []);

  const startGen = async () => {
    setErr(null);
    if (!prompt.trim()) { setErr('Enter a prompt first.'); return; }
    try {
      const res = await toolsApi.sampleGenerate({
        kind, prompt: prompt.trim(), model, count,
        width, height, seed: seed ? parseInt(seed) : undefined,
        negative: negative.trim(), isolate,
      });
      setGenId(res.gen_id);
      setPhase('running');
      setSelected(new Set());
      if (pollRef.current) clearInterval(pollRef.current);
      pollRef.current = setInterval(async () => {
        try {
          const st = await toolsApi.sampleStatus(res.gen_id);
          setStatus(st);
          if (st.status !== 'running') {
            if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; }
            setPhase('review');
            if (!naturalPrompt) setNaturalPrompt(st.prompt || '');
          }
        } catch (e: any) {
          setErr(e.message);
          if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; }
        }
      }, 2000);
    } catch (e: any) {
      setErr(e.message);
    }
  };

  const toggleSel = (id: string) => setSelected((s) => {
    const n = new Set(s); n.has(id) ? n.delete(id) : n.add(id); return n;
  });

  const commit = async () => {
    if (!genId || selected.size === 0) return;
    setCommitting(true); setErr(null);
    try {
      const res = await toolsApi.sampleCommit(genId, {
        kind, image_ids: [...selected],
        category: category.trim(), name: name.trim(),
        tags: tags.split(',').map((t) => t.trim()).filter(Boolean),
        natural_prompt: naturalPrompt.trim(),
        detect_hands: detectHands, detect_face: detectFace,
      });
      if (res.added > 0) {
        onCommitted();
        onClose();
      } else {
        setErr(res.errors?.join('; ') || 'Nothing was added — no keypoints detected?');
      }
    } catch (e: any) {
      setErr(e.message);
    } finally {
      setCommitting(false);
    }
  };

  const backToForm = () => {
    if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; }
    setPhase('form'); setStatus(null); setGenId(null); setSelected(new Set());
  };
  const images = status?.images || [];

  return createPortal(
    <div className="fixed inset-0 z-[9994] bg-black/80 flex items-center justify-center p-4" onClick={onClose}>
      <div className="bg-gray-900 rounded-xl border border-gray-700 w-full max-w-4xl max-h-[92vh] flex flex-col overflow-hidden" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center gap-2 px-4 py-3 border-b border-gray-700">
          <Wand2 className="w-5 h-5 text-indigo-400" />
          <h3 className="font-semibold text-gray-100">Generate {isPose ? 'Pose' : 'Expression'} Sample</h3>
          <button onClick={onClose} className="ml-auto p-1.5 rounded hover:bg-gray-800 text-gray-400"><X className="w-5 h-5" /></button>
        </div>

        <div className="flex-1 min-h-0 overflow-y-auto p-4 space-y-4">
          {/* Generation form (always visible; collapses when reviewing) */}
          {phase === 'form' && (
            <>
              <div>
                <label className="text-xs font-medium text-gray-400 mb-1 block">Prompt</label>
                <textarea value={prompt} onChange={(e) => setPrompt(e.target.value)} rows={3}
                  placeholder={isPose ? 'e.g. a person kneeling on one knee, arm raised' : 'e.g. a joyful laughing expression, eyes closed'}
                  className="w-full rounded-md bg-gray-800 border border-gray-700 px-3 py-2 text-sm text-gray-100 resize-none" />
                <label className="flex items-center gap-2 text-xs text-gray-400 mt-2">
                  <input type="checkbox" checked={isolate} onChange={(e) => setIsolate(e.target.checked)} />
                  Isolate subject (plain background, {isPose ? 'full body framing' : 'head & shoulders'}) — recommended
                </label>
              </div>

              <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
                <div>
                  <label className="text-xs font-medium text-gray-400 mb-1 block">Model</label>
                  <select value={model} onChange={(e) => setModel(e.target.value)}
                    className="w-full rounded-md bg-gray-800 border border-gray-700 px-2 py-2 text-sm text-gray-100">
                    {MODELS.map((m) => <option key={m.value} value={m.value}>{m.label}</option>)}
                  </select>
                </div>
                <div>
                  <label className="text-xs font-medium text-gray-400 mb-1 block">Count</label>
                  <select value={count} onChange={(e) => setCount(parseInt(e.target.value))}
                    className="w-full rounded-md bg-gray-800 border border-gray-700 px-2 py-2 text-sm text-gray-100">
                    {[1, 2, 4, 6, 8].map((n) => <option key={n} value={n}>{n}</option>)}
                  </select>
                </div>
                <div>
                  <label className="text-xs font-medium text-gray-400 mb-1 block">Width</label>
                  <input value={width} onChange={(e) => setWidth(parseInt(e.target.value) || 0)} inputMode="numeric"
                    className="w-full rounded-md bg-gray-800 border border-gray-700 px-2 py-2 text-sm text-gray-100" />
                </div>
                <div>
                  <label className="text-xs font-medium text-gray-400 mb-1 block">Height</label>
                  <input value={height} onChange={(e) => setHeight(parseInt(e.target.value) || 0)} inputMode="numeric"
                    className="w-full rounded-md bg-gray-800 border border-gray-700 px-2 py-2 text-sm text-gray-100" />
                </div>
              </div>

              <details className="text-sm">
                <summary className="cursor-pointer text-gray-400 text-xs">Advanced (negative prompt, seed)</summary>
                <div className="grid grid-cols-1 sm:grid-cols-[1fr_auto] gap-3 mt-2">
                  <input value={negative} onChange={(e) => setNegative(e.target.value)} placeholder="Negative prompt (Anima only — optional)"
                    className="rounded-md bg-gray-800 border border-gray-700 px-2 py-2 text-sm text-gray-100" />
                  <input value={seed} onChange={(e) => setSeed(e.target.value.replace(/[^0-9]/g, ''))} placeholder="seed"
                    className="w-28 rounded-md bg-gray-800 border border-gray-700 px-2 py-2 text-sm text-gray-100" />
                </div>
              </details>

              {isPose && !dwposeAvailable && (
                <div className="text-xs text-amber-400 bg-amber-950/30 rounded px-3 py-2">
                  Note: adding a generated pose to the library needs a DWPose-capable worker (comfyui_controlnet_aux) online to extract keypoints.
                </div>
              )}
              {err && <div className="text-xs text-red-400">{err}</div>}

              <button onClick={startGen}
                className="w-full py-3 rounded-lg bg-indigo-600 hover:bg-indigo-700 font-semibold flex items-center justify-center gap-2">
                <Sparkles className="w-5 h-5" /> Generate {count} sample{count > 1 ? 's' : ''}
              </button>
            </>
          )}

          {/* Running / review */}
          {(phase === 'running' || phase === 'review') && (
            <>
              <div className="flex items-center gap-2 text-sm">
                {phase === 'running' ? <Loader2 className="w-4 h-4 animate-spin text-indigo-400" /> : <Check className="w-4 h-4 text-emerald-400" />}
                <span className="text-gray-300">{status?.done || 0}/{status?.total || count} generated</span>
                <button onClick={backToForm} className="ml-auto flex items-center gap-1 text-xs text-gray-400 hover:text-gray-200">
                  <RotateCcw className="w-3.5 h-3.5" /> New batch
                </button>
              </div>

              {images.length === 0 && phase === 'running' && (
                <div className="text-center text-gray-500 py-10 text-sm">Rendering on your worker…</div>
              )}

              <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 gap-2">
                {images.map((im) => {
                  const sel = selected.has(im.id);
                  return (
                    <div key={im.id} className="relative">
                      <button onClick={() => setLightbox(im.url)}
                        className={`block w-full aspect-square rounded-lg overflow-hidden border-2 ${sel ? 'border-indigo-400' : 'border-gray-700'}`}>
                        <img src={im.url} onError={handleImgError} className="w-full h-full object-cover" alt="" />
                      </button>
                      <button onClick={() => toggleSel(im.id)}
                        className={`absolute top-1.5 left-1.5 w-6 h-6 rounded-md flex items-center justify-center ${sel ? 'bg-indigo-500 text-white' : 'bg-black/60 text-gray-300'}`}
                        title={sel ? 'Selected' : 'Select'}>
                        {sel ? <Check className="w-4 h-4" /> : <span className="w-3 h-3 rounded-sm border border-current" />}
                      </button>
                    </div>
                  );
                })}
                {images.length === 0 && phase === 'review' && (
                  <div className="col-span-full text-center text-gray-500 py-8 text-sm flex flex-col items-center">
                    <ImageOff className="w-6 h-6 mb-2 opacity-50" /> No images produced.
                    {status?.error && <span className="text-red-400 mt-1">{status.error}</span>}
                  </div>
                )}
              </div>
              {status?.error && images.length > 0 && <div className="text-xs text-amber-400">Some failed: {status.error}</div>}
            </>
          )}
        </div>

        {/* Commit bar */}
        {phase === 'review' && images.length > 0 && (
          <div className="border-t border-gray-700 p-3 space-y-2 bg-gray-900">
            <div className="grid grid-cols-2 sm:grid-cols-3 gap-2">
              <input value={category} onChange={(e) => setCategory(e.target.value)} placeholder={isPose ? 'Category (e.g. Standing)' : 'Category (e.g. Happy)'}
                className="rounded-md bg-gray-800 border border-gray-700 px-2 py-2 text-sm text-gray-100" />
              <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Name (optional)"
                className="rounded-md bg-gray-800 border border-gray-700 px-2 py-2 text-sm text-gray-100" />
              <input value={tags} onChange={(e) => setTags(e.target.value)} placeholder="tags, comma, separated"
                className="rounded-md bg-gray-800 border border-gray-700 px-2 py-2 text-sm text-gray-100" />
            </div>
            {!isPose && (
              <input value={naturalPrompt} onChange={(e) => setNaturalPrompt(e.target.value)} placeholder="Natural-language expression prompt (used by emotion engines)"
                className="w-full rounded-md bg-gray-800 border border-gray-700 px-2 py-2 text-sm text-gray-100" />
            )}
            {isPose && (
              <div className="flex gap-4 text-xs text-gray-400">
                <label className="flex items-center gap-1.5"><input type="checkbox" checked={detectHands} onChange={(e) => setDetectHands(e.target.checked)} /> Hands</label>
                <label className="flex items-center gap-1.5"><input type="checkbox" checked={detectFace} onChange={(e) => setDetectFace(e.target.checked)} /> Face</label>
              </div>
            )}
            {err && <div className="text-xs text-red-400">{err}</div>}
            <button onClick={commit} disabled={committing || selected.size === 0}
              className="w-full py-2.5 rounded-lg bg-emerald-600 hover:bg-emerald-500 font-semibold disabled:opacity-50 flex items-center justify-center gap-2">
              {committing ? <Loader2 className="w-4 h-4 animate-spin" /> : <Check className="w-4 h-4" />}
              Add {selected.size || ''} to {isPose ? 'Pose' : 'Expression'} Library
            </button>
          </div>
        )}
      </div>

      {lightbox && <ImageLightbox url={lightbox} onClose={() => setLightbox(null)} />}
    </div>,
    document.body,
  );
}
