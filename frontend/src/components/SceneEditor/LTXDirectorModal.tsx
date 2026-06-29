/**
 * LTXDirectorModal — full-screen timeline editor for LTX Director Mode.
 *
 * Drives the v2.0.0 LTXDirector ComfyUI node via scene.parameters.ltx_director.
 * Three timeline lanes:
 *   1. Prompt Relay — text segments, each conditioning a time span of the clip.
 *   2. Keyframes    — image guides (assets / uploads / previous-scene frame) at a
 *                     frame with a strength.
 *   3. Audio        — defaults to the scene's audio (conditioning / lip-sync),
 *                     overridable by an asset or upload.
 *   (+ Motion track, advanced.)
 *
 * State autosaves to scene.parameters.ltx_director (via onSaveConfig); Generate
 * persists then enqueues an `ltx_director` video job through the parent.
 */
import { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import { createPortal } from 'react-dom';
import {
  X, Plus, Trash2, Film, Type as TypeIcon, Music, Wand2, Image as ImageIcon,
  ChevronDown, ChevronRight, ZoomIn, ZoomOut, Clock, Hash, Activity,
} from 'lucide-react';
import { useAssetPicker } from '../AssetManager/AssetPickerModal';
import { uploadAsset, getPrevSceneLastFrame } from '@/api/client';
import type {
  Asset, Scene, LtxDirectorConfig, LtxDirectorSegment,
  LtxDirectorImageSegment, LtxDirectorTextSegment, LtxDirectorMotionSegment,
} from '@/types/index';

interface LTXDirectorModalProps {
  projectId: string;
  scene: Scene;
  assets: Asset[];
  isFirstScene: boolean;
  durationSeconds: number;
  framerate: number;
  width: number;
  height: number;
  isGenerating?: boolean;
  onClose: () => void;
  onSaveConfig: (cfg: LtxDirectorConfig) => void | Promise<void>;
  onGenerate: () => void | Promise<void>;
}

const RESIZE_METHODS = ['maintain aspect ratio', 'stretch to fit', 'pad', 'crop'];

function uid(): string {
  try {
    if (typeof crypto !== 'undefined' && (crypto as any).randomUUID) return (crypto as any).randomUUID();
  } catch { /* ignore */ }
  return `s_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 8)}`;
}

function assetThumb(projectId: string, assetId?: string): string | null {
  return assetId ? `/api/projects/${projectId}/assets/${assetId}/file` : null;
}
function relThumb(relPath?: string): string | null {
  return relPath ? `/api/files/${relPath}` : null;
}

export default function LTXDirectorModal({
  projectId, scene, assets, isFirstScene,
  durationSeconds, framerate, width, height,
  isGenerating, onClose, onSaveConfig, onGenerate,
}: LTXDirectorModalProps) {
  const fps = Math.max(1, Math.round(framerate || 24));
  const totalFrames = Math.max(1, Math.round((durationSeconds || 5) * fps));

  // ── Build the initial config from the saved scene state (or sane defaults) ──
  const buildInitial = useCallback((): LtxDirectorConfig => {
    const saved = (scene.parameters?.ltx_director || {}) as Partial<LtxDirectorConfig>;
    return {
      enabled: true,
      global_prompt: saved.global_prompt ?? (scene.parameters?.video_prompt || scene.prompt || ''),
      epsilon: saved.epsilon ?? 0.001,
      use_custom_audio: saved.use_custom_audio ?? true,
      use_custom_motion: saved.use_custom_motion ?? false,
      audio_source: saved.audio_source ?? 'scene',
      audio_asset_id: saved.audio_asset_id,
      audio_rel_path: saved.audio_rel_path,
      frame_rate: fps,
      width: saved.width ?? width,
      height: saved.height ?? height,
      resize_method: saved.resize_method ?? 'maintain aspect ratio',
      img_compression: saved.img_compression ?? 18,
      display_mode: saved.display_mode ?? 'seconds',
      quality: saved.quality ?? 'standard',
      duration_seconds: durationSeconds,
      segments: (saved.segments as LtxDirectorSegment[]) ?? [],
      motionSegments: (saved.motionSegments as LtxDirectorMotionSegment[]) ?? [],
      retake: saved.retake,
    };
  }, [scene, fps, durationSeconds]);

  const [cfg, setCfg] = useState<LtxDirectorConfig>(buildInitial);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [pxPerFrame, setPxPerFrame] = useState<number>(() =>
    Math.min(14, Math.max(3, Math.round(900 / totalFrames))));
  const [display, setDisplay] = useState<'frames' | 'seconds'>(cfg.display_mode);
  const [showGlobals, setShowGlobals] = useState(true);
  const [showMotion, setShowMotion] = useState(false);
  const [showRetake, setShowRetake] = useState(false);
  const [saveState, setSaveState] = useState<'idle' | 'saving' | 'saved'>('idle');
  const [prevFramePath, setPrevFramePath] = useState<string | null>(null);

  const imageTargetRef = useRef<{ mode: 'add' | 'replace'; id?: string }>({ mode: 'add' });

  // ── Autosave (debounced) ──────────────────────────────────────────────────
  // onSaveConfig is read via a ref so a new inline parent callback identity does
  // NOT re-fire this effect (which would loop: save → store update → re-render →
  // new callback → save …).
  const onSaveRef = useRef(onSaveConfig);
  useEffect(() => { onSaveRef.current = onSaveConfig; }, [onSaveConfig]);
  const firstRun = useRef(true);
  useEffect(() => {
    if (firstRun.current) { firstRun.current = false; return; }
    setSaveState('saving');
    const t = setTimeout(async () => {
      try { await onSaveRef.current({ ...cfg, display_mode: display, frame_rate: fps }); setSaveState('saved'); }
      catch { setSaveState('idle'); }
    }, 450);
    return () => clearTimeout(t);
  }, [cfg, display, fps]);

  // ── Previous-scene last frame (continuation helper) ───────────────────────
  useEffect(() => {
    let alive = true;
    if (isFirstScene) { setPrevFramePath(null); return; }
    getPrevSceneLastFrame(projectId, scene.id)
      .then((r) => { if (alive) setPrevFramePath(r.data?.image_path || null); })
      .catch(() => { if (alive) setPrevFramePath(null); });
    return () => { alive = false; };
  }, [projectId, scene.id, isFirstScene]);

  const trackWidth = totalFrames * pxPerFrame;

  // ── Segment mutation helpers ──────────────────────────────────────────────
  const patchCfg = useCallback((p: Partial<LtxDirectorConfig>) => setCfg((c) => ({ ...c, ...p })), []);
  const updateSeg = useCallback((id: string, patch: Record<string, any>) => {
    setCfg((c) => ({ ...c, segments: c.segments.map((s) => (s.id === id ? { ...s, ...patch } as LtxDirectorSegment : s)) }));
  }, []);
  const removeSeg = useCallback((id: string) => {
    setCfg((c) => ({ ...c, segments: c.segments.filter((s) => s.id !== id) }));
    setSelectedId((sid) => (sid === id ? null : sid));
  }, []);

  const addTextSegment = useCallback(() => {
    const start = 0;
    const length = Math.min(Math.max(8, Math.round(totalFrames / 3)), totalFrames);
    const seg: LtxDirectorTextSegment = { type: 'text', id: uid(), prompt: '', frame: start, length };
    setCfg((c) => ({ ...c, segments: [...c.segments, seg] }));
    setSelectedId(seg.id);
  }, [totalFrames]);

  const addImageSegment = useCallback((opts: { asset_id?: string; imageFile?: string; rel_path?: string; label?: string }) => {
    const seg: LtxDirectorImageSegment = {
      type: 'image', id: uid(), frame: 0, length: 1, strength: 1.0, ...opts,
    };
    setCfg((c) => ({ ...c, segments: [...c.segments, seg] }));
    setSelectedId(seg.id);
  }, []);

  // ── Image picker (keyframes) ──────────────────────────────────────────────
  const onImageAsset = useCallback((asset: Asset) => {
    const t = imageTargetRef.current;
    if (t.mode === 'replace' && t.id) {
      updateSeg(t.id, { asset_id: asset.id, rel_path: asset.rel_path, imageFile: undefined, label: asset.filename } as Partial<LtxDirectorImageSegment>);
    } else {
      addImageSegment({ asset_id: asset.id, rel_path: asset.rel_path, label: asset.filename });
    }
  }, [updateSeg, addImageSegment]);

  const onImageUpload = useCallback(async (file: File) => {
    try {
      const fd = new FormData();
      fd.append('file', file);
      fd.append('asset_type', 'reference');
      const res = await uploadAsset(projectId, fd);
      const a = res.data;
      const t = imageTargetRef.current;
      if (t.mode === 'replace' && t.id) {
        updateSeg(t.id, { asset_id: a.id, rel_path: a.rel_path, imageFile: undefined, label: a.filename } as Partial<LtxDirectorImageSegment>);
      } else {
        addImageSegment({ asset_id: a.id, rel_path: a.rel_path, label: a.filename });
      }
    } catch (e) {
      alert('Image upload failed. See console for details.');
      console.error(e);
    }
  }, [projectId, updateSeg, addImageSegment]);

  const imagePicker = useAssetPicker({
    assets, onFileUpload: onImageUpload, onAssetSelect: onImageAsset,
    accept: 'image/*', imagesOnly: true, title: 'Add Keyframe Image',
  });

  const openImagePicker = (mode: 'add' | 'replace', id?: string) => {
    imageTargetRef.current = { mode, id };
    imagePicker.openPicker();
  };

  const usePrevFrameKeyframe = () => {
    if (!prevFramePath) return;
    const existing = assets.find((a) => a.rel_path === prevFramePath);
    addImageSegment(existing
      ? { asset_id: existing.id, rel_path: existing.rel_path, label: 'Previous scene (last frame)' }
      : { imageFile: prevFramePath, rel_path: prevFramePath, label: 'Previous scene (last frame)' });
  };

  // ── Audio picker ──────────────────────────────────────────────────────────
  const onAudioAsset = useCallback((asset: Asset) => {
    patchCfg({ audio_source: 'asset', audio_asset_id: asset.id, audio_rel_path: asset.rel_path, use_custom_audio: true });
  }, [patchCfg]);
  const onAudioUpload = useCallback(async (file: File) => {
    try {
      const fd = new FormData();
      fd.append('file', file);
      fd.append('asset_type', 'music');
      const res = await uploadAsset(projectId, fd);
      patchCfg({ audio_source: 'asset', audio_asset_id: res.data.id, audio_rel_path: res.data.rel_path, use_custom_audio: true });
    } catch (e) {
      alert('Audio upload failed. See console for details.');
      console.error(e);
    }
  }, [projectId, patchCfg]);
  const audioPicker = useAssetPicker({
    assets, onFileUpload: onAudioUpload, onAssetSelect: onAudioAsset,
    accept: 'audio/*', imagesOnly: false, title: 'Choose / Upload Audio',
  });

  // ── Motion picker (advanced) ──────────────────────────────────────────────
  const addMotionSegment = useCallback((opts: { asset_id?: string; rel_path?: string }) => {
    const seg: LtxDirectorMotionSegment = { id: uid(), frame: 0, length: totalFrames, strength: 1.0, ...opts };
    setCfg((c) => ({ ...c, motionSegments: [...(c.motionSegments || []), seg], use_custom_motion: true }));
  }, [totalFrames]);
  const motionPicker = useAssetPicker({
    assets, onFileUpload: async (file) => {
      try {
        const fd = new FormData(); fd.append('file', file); fd.append('asset_type', 'reference');
        const res = await uploadAsset(projectId, fd);
        addMotionSegment({ asset_id: res.data.id, rel_path: res.data.rel_path });
      } catch (e) { console.error(e); }
    },
    onAssetSelect: (a) => addMotionSegment({ asset_id: a.id, rel_path: a.rel_path }),
    accept: 'image/*,video/*', imagesOnly: false, title: 'Add Motion Guide',
  });

  // ── Retake / edit-an-existing-clip ────────────────────────────────────────
  const sceneVideoPath = ((scene.parameters as any)?.chosen_video_path as string | undefined) || undefined;
  const retake = cfg.retake;
  const setRetake = useCallback((patch: Record<string, any> | null) => {
    setCfg((c) => (patch === null
      ? { ...c, retake: undefined }
      : { ...c, retake: { start: 0, length: Math.round(((c.duration_seconds || 5) * (c.frame_rate || 24)) / 2), strength: 1, prompt: '', ...(c.retake || {}), ...patch } }));
  }, []);
  const videoPicker = useAssetPicker({
    assets,
    onFileUpload: async (file) => {
      try {
        const fd = new FormData(); fd.append('file', file); fd.append('asset_type', 'reference');
        const res = await uploadAsset(projectId, fd);
        setRetake({ video_asset_id: res.data.id, video: res.data.rel_path });
      } catch (e) { console.error(e); alert('Video upload failed.'); }
    },
    onAssetSelect: (a) => setRetake({ video_asset_id: a.id, video: a.rel_path }),
    accept: 'video/*', imagesOnly: false, title: 'Retake — source video',
  });

  // ── Drag / resize on the timeline ─────────────────────────────────────────
  const dragRef = useRef<{ id: string; kind: 'move' | 'resize'; startX: number; frame0: number; len0: number } | null>(null);
  const onSegPointerDown = (e: React.PointerEvent, seg: LtxDirectorSegment, kind: 'move' | 'resize') => {
    e.preventDefault(); e.stopPropagation();
    setSelectedId(seg.id);
    (e.target as HTMLElement).setPointerCapture?.(e.pointerId);
    dragRef.current = { id: seg.id, kind, startX: e.clientX, frame0: seg.frame, len0: seg.length };
  };
  const onSegPointerMove = (e: React.PointerEvent) => {
    const d = dragRef.current; if (!d) return;
    const df = Math.round((e.clientX - d.startX) / pxPerFrame);
    setCfg((c) => ({
      ...c,
      segments: c.segments.map((s) => {
        if (s.id !== d.id) return s;
        if (d.kind === 'move') {
          const maxStart = Math.max(0, totalFrames - s.length);
          return { ...s, frame: Math.min(Math.max(0, d.frame0 + df), maxStart) } as LtxDirectorSegment;
        }
        const maxLen = totalFrames - s.frame;
        return { ...s, length: Math.min(Math.max(1, d.len0 + df), Math.max(1, maxLen)) } as LtxDirectorSegment;
      }),
    }));
  };
  const onSegPointerUp = (e: React.PointerEvent) => {
    if (dragRef.current) { (e.target as HTMLElement).releasePointerCapture?.(e.pointerId); dragRef.current = null; }
  };

  // ── Derived: ordered text segments (for Prompt-Relay preview) ─────────────
  const textSegs = useMemo(
    () => cfg.segments.filter((s): s is LtxDirectorTextSegment => s.type === 'text').sort((a, b) => a.frame - b.frame),
    [cfg.segments]);
  const imageSegs = useMemo(
    () => cfg.segments.filter((s): s is LtxDirectorImageSegment => s.type === 'image'),
    [cfg.segments]);

  const fmt = useCallback((frame: number) => (
    display === 'seconds' ? `${(frame / fps).toFixed(2)}s` : `${frame}f`
  ), [display, fps]);

  const selected = cfg.segments.find((s) => s.id === selectedId) || null;

  // ── Ruler ticks ───────────────────────────────────────────────────────────
  const ticks = useMemo(() => {
    const out: { frame: number; label: string }[] = [];
    const stepSec = totalFrames / fps <= 8 ? 1 : totalFrames / fps <= 20 ? 2 : 5;
    const stepFrames = display === 'seconds' ? Math.round(stepSec * fps) : Math.max(1, Math.round(totalFrames / 12));
    for (let f = 0; f <= totalFrames; f += stepFrames) out.push({ frame: f, label: fmt(f) });
    return out;
  }, [totalFrames, fps, display, fmt]);

  const handleGenerate = async () => {
    setSaveState('saving');
    try { await onSaveConfig({ ...cfg, display_mode: display, frame_rate: fps }); setSaveState('saved'); } catch { /* ignore */ }
    await onGenerate();
  };

  const Lane = ({ label, icon, children }: { label: string; icon: React.ReactNode; children: React.ReactNode }) => (
    <div className="flex border-b border-gray-800">
      <div className="w-28 shrink-0 px-2 py-2 text-[11px] font-semibold text-gray-400 flex items-center gap-1.5 bg-gray-900/60 sticky left-0 z-10 border-r border-gray-800">
        {icon}{label}
      </div>
      <div className="relative" style={{ width: trackWidth, minHeight: 56 }}>{children}</div>
    </div>
  );

  return createPortal(
    <div
      style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.85)', zIndex: 9990, display: 'flex', flexDirection: 'column' }}
      onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}
    >
      <div className="m-2 sm:m-4 flex-1 min-h-0 flex flex-col bg-gray-950 border border-gray-700 rounded-xl overflow-hidden shadow-2xl">
        {/* ── Header ── */}
        <div className="flex items-center justify-between px-4 py-2.5 border-b border-gray-800 bg-gray-900">
          <div className="flex items-center gap-3 min-w-0">
            <Film size={18} className="text-blue-400 shrink-0" />
            <div className="min-w-0">
              <div className="text-sm font-bold text-gray-100 truncate">LTX Director — {scene.name || 'Scene'}</div>
              <div className="text-[11px] text-gray-500">
                {durationSeconds.toFixed(2)}s · {totalFrames} frames · {fps} fps · {width}×{height}
              </div>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <span className="text-[11px] text-gray-500 w-14 text-right">
              {saveState === 'saving' ? 'Saving…' : saveState === 'saved' ? 'Saved ✓' : ''}
            </span>
            <div className="flex rounded bg-gray-800 p-0.5">
              <button onClick={() => setDisplay('seconds')} className={`px-2 py-1 rounded text-[11px] flex items-center gap-1 ${display === 'seconds' ? 'bg-blue-600 text-white' : 'text-gray-400'}`}><Clock size={11} />s</button>
              <button onClick={() => setDisplay('frames')} className={`px-2 py-1 rounded text-[11px] flex items-center gap-1 ${display === 'frames' ? 'bg-blue-600 text-white' : 'text-gray-400'}`}><Hash size={11} />f</button>
            </div>
            <button onClick={() => setPxPerFrame((z) => Math.max(2, z - 2))} title="Zoom out" className="p-1.5 rounded bg-gray-800 hover:bg-gray-700 text-gray-300"><ZoomOut size={14} /></button>
            <button onClick={() => setPxPerFrame((z) => Math.min(40, z + 2))} title="Zoom in" className="p-1.5 rounded bg-gray-800 hover:bg-gray-700 text-gray-300"><ZoomIn size={14} /></button>
            <button onClick={onClose} className="p-1.5 rounded bg-gray-800 hover:bg-gray-700 text-gray-300"><X size={16} /></button>
          </div>
        </div>

        {/* ── Global settings ── */}
        <div className="border-b border-gray-800 bg-gray-900/50">
          <button onClick={() => setShowGlobals((v) => !v)} className="w-full px-4 py-2 flex items-center gap-2 text-xs font-semibold text-gray-300 hover:bg-gray-800/50">
            {showGlobals ? <ChevronDown size={14} /> : <ChevronRight size={14} />} Global settings (anchor prompt, audio, motion, output)
          </button>
          {showGlobals && (
            <div className="px-4 pb-3 grid grid-cols-1 lg:grid-cols-3 gap-3">
              {/* Global prompt */}
              <div className="lg:col-span-2">
                <label className="text-[11px] font-medium text-gray-400">Global prompt (constant anchor — characters, place, style)</label>
                <textarea
                  value={cfg.global_prompt}
                  onChange={(e) => patchCfg({ global_prompt: e.target.value })}
                  placeholder="e.g. a young woman with red hair in a dim noir apartment, rain on the window, 35mm film, cinematic"
                  className="w-full h-16 mt-1 px-2 py-1.5 bg-gray-950 border border-gray-700 rounded text-xs text-gray-100 resize-y focus:outline-none focus:border-blue-500"
                />
              </div>
              {/* Right column controls */}
              <div className="space-y-2">
                <div className="flex items-center justify-between gap-2">
                  <label className="text-[11px] text-gray-400">Transition sharpness (epsilon)</label>
                  <span className="text-[11px] text-gray-300 tabular-nums">{cfg.epsilon.toFixed(3)}</span>
                </div>
                <input type="range" min={0} max={0.5} step={0.001} value={cfg.epsilon}
                  onChange={(e) => patchCfg({ epsilon: parseFloat(e.target.value) })} className="w-full" />
                <div className="flex items-center justify-between gap-2">
                  <label className="text-[11px] text-gray-400">Keyframe compression (CRF)</label>
                  <span className="text-[11px] text-gray-300 tabular-nums">{cfg.img_compression}</span>
                </div>
                <input type="range" min={0} max={51} step={1} value={cfg.img_compression}
                  onChange={(e) => patchCfg({ img_compression: parseInt(e.target.value) })} className="w-full" />
              </div>

              {/* Audio */}
              <div className="lg:col-span-2 p-2.5 bg-gray-900 border border-gray-800 rounded">
                <div className="flex items-center gap-2 mb-2">
                  <Music size={13} className="text-emerald-400" />
                  <span className="text-[11px] font-semibold text-gray-300">Audio</span>
                  <label className="ml-auto flex items-center gap-1.5 text-[11px] text-gray-400">
                    <input type="checkbox" checked={cfg.use_custom_audio} onChange={(e) => patchCfg({ use_custom_audio: e.target.checked })} />
                    Condition video on this audio (sync / lip-sync)
                  </label>
                </div>
                <div className="flex flex-wrap items-center gap-2">
                  <button
                    onClick={() => patchCfg({ audio_source: 'scene', audio_asset_id: undefined, audio_rel_path: undefined })}
                    className={`px-2.5 py-1 rounded text-[11px] ${cfg.audio_source === 'scene' ? 'bg-emerald-600 text-white' : 'bg-gray-800 text-gray-300 hover:bg-gray-700'}`}
                  >Use scene audio (default)</button>
                  <button onClick={() => audioPicker.openPicker()}
                    className={`px-2.5 py-1 rounded text-[11px] ${cfg.audio_source === 'asset' ? 'bg-emerald-600 text-white' : 'bg-gray-800 text-gray-300 hover:bg-gray-700'}`}
                  >Pick / upload audio…</button>
                  {cfg.audio_source === 'asset' && cfg.audio_rel_path && (
                    <span className="text-[11px] text-gray-400 truncate max-w-[40%]" title={cfg.audio_rel_path}>{cfg.audio_rel_path.split('/').pop()}</span>
                  )}
                  {!cfg.use_custom_audio && <span className="text-[11px] text-amber-400">LTX will generate its own audio.</span>}
                </div>
              </div>

              {/* Output dims + resize */}
              <div className="space-y-2 p-2.5 bg-gray-900 border border-gray-800 rounded">
                <label className="flex items-center gap-1.5 text-[11px] text-gray-400">
                  <input type="checkbox" checked={!!(cfg.width && cfg.height)}
                    onChange={(e) => patchCfg(e.target.checked ? { width, height } : { width: 0, height: 0 })} />
                  Pin output size {cfg.width && cfg.height ? `(${cfg.width}×${cfg.height})` : '(auto from keyframes)'}
                </label>
                <select value={cfg.resize_method} onChange={(e) => patchCfg({ resize_method: e.target.value })}
                  className="w-full px-2 py-1 bg-gray-950 border border-gray-700 rounded text-[11px] text-gray-200">
                  {RESIZE_METHODS.map((m) => <option key={m} value={m}>{m}</option>)}
                </select>
                <label className="flex items-center gap-1.5 text-[11px] text-gray-400">
                  <input type="checkbox" checked={!!cfg.use_custom_motion} onChange={(e) => { patchCfg({ use_custom_motion: e.target.checked }); setShowMotion(e.target.checked); }} />
                  Enable motion track (advanced)
                </label>
                <div>
                  <label className="text-[11px] text-gray-400">Quality</label>
                  <div className="flex rounded bg-gray-800 p-0.5 mt-1">
                    <button onClick={() => patchCfg({ quality: 'standard' })} className={`flex-1 px-2 py-1 rounded text-[11px] ${(cfg.quality || 'standard') === 'standard' ? 'bg-blue-600 text-white' : 'text-gray-400'}`}>Standard</button>
                    <button onClick={() => patchCfg({ quality: 'hq' })} className={`flex-1 px-2 py-1 rounded text-[11px] ${cfg.quality === 'hq' ? 'bg-blue-600 text-white' : 'text-gray-400'}`}>High (2× upscale)</button>
                  </div>
                  <p className="text-[10px] text-gray-500 mt-1">{cfg.quality === 'hq' ? 'Two-stage 2× upscale + tiled decode (slower, sharper, low-VRAM friendly).' : 'Single-stage (fast).'}</p>
                </div>
              </div>
            </div>
          )}
        </div>

        {/* ── Retake / edit an existing clip ── */}
        <div className="border-b border-gray-800 bg-gray-900/40">
          <button onClick={() => setShowRetake((v) => !v)} className="w-full px-4 py-2 flex items-center gap-2 text-xs font-semibold text-gray-300 hover:bg-gray-800/50">
            {showRetake ? <ChevronDown size={14} /> : <ChevronRight size={14} />} Retake / edit an existing clip {retake ? <span className="ml-1 text-[10px] px-1.5 py-0.5 rounded bg-amber-700 text-white">ON</span> : null}
          </button>
          {showRetake && (
            <div className="px-4 pb-3 space-y-2">
              <label className="flex items-center gap-1.5 text-[11px] text-gray-300">
                <input type="checkbox" checked={!!retake} onChange={(e) => setRetake(e.target.checked ? {} : null)} />
                Enable retake — re-generate a span of an existing video with a new prompt (keeps the rest)
              </label>
              {retake && (
                <div className="grid grid-cols-1 lg:grid-cols-3 gap-3">
                  <div className="lg:col-span-1 space-y-1.5">
                    <div className="text-[10px] uppercase tracking-wide text-gray-500">Source video</div>
                    <div className="flex flex-wrap gap-2">
                      {sceneVideoPath && (
                        <button onClick={() => setRetake({ video: sceneVideoPath, video_asset_id: undefined })} className={`px-2 py-1 rounded text-[11px] ${retake.video === sceneVideoPath ? 'bg-amber-600 text-white' : 'bg-gray-800 text-gray-300 hover:bg-gray-700'}`}>Use this scene&apos;s video</button>
                      )}
                      <button onClick={() => videoPicker.openPicker()} className="px-2 py-1 rounded text-[11px] bg-gray-800 text-gray-300 hover:bg-gray-700">Pick / upload…</button>
                    </div>
                    {retake.video ? <div className="text-[10px] text-gray-400 truncate" title={retake.video}>{retake.video.split('/').pop()}</div> : <div className="text-[10px] text-amber-400">No source video selected.</div>}
                  </div>
                  <div className="grid grid-cols-2 gap-2 content-start">
                    <div>
                      <label className="text-[10px] text-gray-500">Start (frame)</label>
                      <input type="number" min={0} max={totalFrames} value={retake.start}
                        onChange={(e) => setRetake({ start: Math.min(Math.max(0, parseInt(e.target.value) || 0), totalFrames) })}
                        className="w-full px-2 py-1 bg-gray-950 border border-gray-700 rounded text-xs text-gray-100" />
                    </div>
                    <div>
                      <label className="text-[10px] text-gray-500">Length (frames)</label>
                      <input type="number" min={1} max={totalFrames} value={retake.length}
                        onChange={(e) => setRetake({ length: Math.min(Math.max(1, parseInt(e.target.value) || 1), totalFrames) })}
                        className="w-full px-2 py-1 bg-gray-950 border border-gray-700 rounded text-xs text-gray-100" />
                    </div>
                    <div className="col-span-2">
                      <div className="h-2 rounded bg-gray-800 relative overflow-hidden">
                        <div className="absolute top-0 h-full bg-amber-500/70" style={{ left: `${(retake.start / totalFrames) * 100}%`, width: `${(Math.min(retake.length, totalFrames - retake.start) / totalFrames) * 100}%` }} />
                      </div>
                      <div className="text-[10px] text-gray-500 mt-0.5">{fmt(retake.start)} → {fmt(Math.min(retake.start + retake.length, totalFrames))}</div>
                    </div>
                    <div className="col-span-2">
                      <label className="text-[10px] text-gray-500">Retake strength {(retake.strength ?? 1).toFixed(2)}</label>
                      <input type="range" min={0} max={1} step={0.05} value={retake.strength ?? 1}
                        onChange={(e) => setRetake({ strength: parseFloat(e.target.value) })} className="w-full" />
                    </div>
                  </div>
                  <div className="lg:col-span-1">
                    <label className="text-[10px] text-gray-500">Retake prompt (what the new span should show)</label>
                    <textarea value={retake.prompt || ''} onChange={(e) => setRetake({ prompt: e.target.value })}
                      placeholder="e.g. she turns her head toward the camera"
                      className="w-full h-[88px] mt-1 px-2 py-1.5 bg-gray-950 border border-gray-700 rounded text-xs text-gray-100 resize-y focus:outline-none focus:border-amber-500" />
                  </div>
                </div>
              )}
            </div>
          )}
        </div>

        {/* ── Toolbar ── */}
        <div className="px-4 py-2 flex flex-wrap items-center gap-2 border-b border-gray-800 bg-gray-900/40">
          <button onClick={addTextSegment} className="px-2.5 py-1.5 rounded text-[11px] bg-purple-700 hover:bg-purple-600 text-white flex items-center gap-1"><TypeIcon size={13} /> Add prompt segment</button>
          <button onClick={() => openImagePicker('add')} className="px-2.5 py-1.5 rounded text-[11px] bg-blue-700 hover:bg-blue-600 text-white flex items-center gap-1"><ImageIcon size={13} /> Add keyframe</button>
          {!isFirstScene && prevFramePath && (
            <button onClick={usePrevFrameKeyframe} className="px-2.5 py-1.5 rounded text-[11px] bg-gray-700 hover:bg-gray-600 text-gray-100 flex items-center gap-1" title="Add the previous scene's last frame as the opening keyframe (continuation)"><Plus size={12} /> Continue from previous scene</button>
          )}
          <span className="ml-auto text-[11px] text-gray-500">{textSegs.length} prompt · {imageSegs.length} keyframe{imageSegs.length === 1 ? '' : 's'}</span>
        </div>

        {/* ── Timeline ── */}
        <div className="flex-1 min-h-0 overflow-auto">
          <div style={{ width: trackWidth + 112 }}>
            {/* Ruler */}
            <div className="flex border-b border-gray-800 bg-gray-900 sticky top-0 z-20">
              <div className="w-28 shrink-0 border-r border-gray-800" />
              <div className="relative h-6" style={{ width: trackWidth }}>
                {ticks.map((t) => (
                  <div key={t.frame} className="absolute top-0 h-full border-l border-gray-700/70 text-[10px] text-gray-500 pl-1" style={{ left: t.frame * pxPerFrame }}>{t.label}</div>
                ))}
              </div>
            </div>

            {/* Prompt lane */}
            <Lane label="Prompt Relay" icon={<TypeIcon size={12} className="text-purple-400" />}>
              {textSegs.map((s) => (
                <div key={s.id}
                  onPointerMove={onSegPointerMove} onPointerUp={onSegPointerUp}
                  onPointerDown={(e) => onSegPointerDown(e, s, 'move')}
                  onClick={() => setSelectedId(s.id)}
                  className={`absolute top-1.5 h-11 rounded cursor-grab active:cursor-grabbing overflow-hidden border ${selectedId === s.id ? 'border-purple-300 ring-1 ring-purple-300' : 'border-purple-500/60'} bg-purple-700/70`}
                  style={{ left: s.frame * pxPerFrame, width: Math.max(18, s.length * pxPerFrame) }}
                  title={s.prompt || '(empty prompt)'}
                >
                  <div className="px-1.5 py-1 text-[10px] text-white/95 leading-tight line-clamp-2">{s.prompt || '(empty)'}</div>
                  <div onPointerDown={(e) => onSegPointerDown(e, s, 'resize')} className="absolute top-0 right-0 h-full w-2 cursor-ew-resize bg-purple-300/40" />
                </div>
              ))}
              {textSegs.length === 0 && <div className="absolute inset-0 flex items-center pl-2 text-[11px] text-gray-600">No prompt segments — the global prompt drives the whole clip.</div>}
            </Lane>

            {/* Keyframe lane */}
            <Lane label="Keyframes" icon={<ImageIcon size={12} className="text-blue-400" />}>
              {imageSegs.map((s) => {
                const src = assetThumb(projectId, s.asset_id) || relThumb(s.rel_path);
                return (
                  <div key={s.id}
                    onPointerMove={onSegPointerMove} onPointerUp={onSegPointerUp}
                    onPointerDown={(e) => onSegPointerDown(e, s, 'move')}
                    onClick={() => setSelectedId(s.id)}
                    className={`absolute top-1.5 h-11 rounded cursor-grab active:cursor-grabbing overflow-hidden border ${selectedId === s.id ? 'border-blue-300 ring-1 ring-blue-300' : 'border-blue-500/60'} bg-blue-900/70`}
                    style={{ left: s.frame * pxPerFrame, width: Math.max(34, s.length * pxPerFrame) }}
                    title={`${s.label || 'keyframe'} · strength ${s.strength.toFixed(2)} @ ${fmt(s.frame)}`}
                  >
                    {src
                      ? <img src={src} alt="" className="h-full w-full object-cover opacity-90" />
                      : <div className="h-full w-full flex items-center justify-center text-[9px] text-blue-200">img</div>}
                    <div className="absolute bottom-0 left-0 right-0 px-1 text-[9px] text-white bg-black/55 tabular-nums">{s.strength.toFixed(2)}</div>
                    <div onPointerDown={(e) => onSegPointerDown(e, s, 'resize')} className="absolute top-0 right-0 h-full w-2 cursor-ew-resize bg-blue-300/40" />
                  </div>
                );
              })}
              {imageSegs.length === 0 && <div className="absolute inset-0 flex items-center pl-2 text-[11px] text-gray-600">No keyframes — pure text-to-video. Add a keyframe (or previous-scene frame) to anchor the picture.</div>}
            </Lane>

            {/* Audio lane */}
            <Lane label="Audio" icon={<Music size={12} className="text-emerald-400" />}>
              <div className="absolute top-2 h-9 rounded bg-emerald-800/50 border border-emerald-600/50 flex items-center px-2 text-[10px] text-emerald-100" style={{ left: 0, width: trackWidth }}>
                {cfg.use_custom_audio
                  ? (cfg.audio_source === 'scene' ? 'Scene audio (auto-sliced to this scene)' : (cfg.audio_rel_path?.split('/').pop() || 'Selected audio'))
                  : 'Model-generated audio (no conditioning)'}
              </div>
            </Lane>

            {/* Motion lane (advanced) */}
            {(showMotion || (cfg.motionSegments && cfg.motionSegments.length > 0)) && (
              <Lane label="Motion" icon={<Activity size={12} className="text-pink-400" />}>
                {(cfg.motionSegments || []).map((m) => (
                  <div key={m.id} className="absolute top-2 h-9 rounded bg-pink-800/50 border border-pink-500/50 flex items-center px-2 text-[10px] text-pink-100 cursor-pointer"
                    style={{ left: m.frame * pxPerFrame, width: Math.max(40, m.length * pxPerFrame) }}
                    title={m.rel_path || 'motion guide'} onClick={() => setSelectedId(m.id)}>
                    motion {m.strength?.toFixed(2)}
                  </div>
                ))}
                <button onClick={() => motionPicker.openPicker()} className="absolute top-2 left-1 h-9 px-2 rounded bg-gray-800 hover:bg-gray-700 text-[10px] text-gray-200 flex items-center gap-1"><Plus size={11} /> guide</button>
              </Lane>
            )}
          </div>
        </div>

        {/* ── Selected-segment editor ── */}
        {selected && (
          <div className="border-t border-gray-800 bg-gray-900 px-4 py-3">
            {selected.type === 'text' ? (
              <div className="flex flex-col sm:flex-row gap-3">
                <div className="flex-1">
                  <label className="text-[11px] font-medium text-gray-400">Prompt for {fmt(selected.frame)} → {fmt(selected.frame + selected.length)}</label>
                  <textarea autoFocus value={selected.prompt}
                    onChange={(e) => updateSeg(selected.id, { prompt: e.target.value } as Partial<LtxDirectorTextSegment>)}
                    placeholder="What happens during this span — e.g. she stands and walks toward the window"
                    className="w-full h-16 mt-1 px-2 py-1.5 bg-gray-950 border border-gray-700 rounded text-xs text-gray-100 resize-y focus:outline-none focus:border-purple-500" />
                </div>
                <div className="flex sm:flex-col gap-2 sm:w-40">
                  <div className="flex-1">
                    <label className="text-[10px] text-gray-500">Start</label>
                    <input type="number" min={0} max={totalFrames} value={selected.frame}
                      onChange={(e) => updateSeg(selected.id, { frame: Math.min(Math.max(0, parseInt(e.target.value) || 0), totalFrames) })}
                      className="w-full px-2 py-1 bg-gray-950 border border-gray-700 rounded text-xs text-gray-100" />
                  </div>
                  <div className="flex-1">
                    <label className="text-[10px] text-gray-500">Length (frames)</label>
                    <input type="number" min={1} max={totalFrames} value={selected.length}
                      onChange={(e) => updateSeg(selected.id, { length: Math.min(Math.max(1, parseInt(e.target.value) || 1), totalFrames) })}
                      className="w-full px-2 py-1 bg-gray-950 border border-gray-700 rounded text-xs text-gray-100" />
                  </div>
                  <button onClick={() => removeSeg(selected.id)} className="px-2 py-1 rounded bg-red-900/70 hover:bg-red-800 text-red-200 text-[11px] flex items-center justify-center gap-1"><Trash2 size={12} /> Delete</button>
                </div>
              </div>
            ) : (
              <div className="flex gap-3 items-start">
                <div className="w-20 h-20 rounded overflow-hidden border border-gray-700 bg-gray-950 shrink-0">
                  {(assetThumb(projectId, (selected as LtxDirectorImageSegment).asset_id) || relThumb((selected as LtxDirectorImageSegment).rel_path))
                    ? <img src={assetThumb(projectId, (selected as LtxDirectorImageSegment).asset_id) || relThumb((selected as LtxDirectorImageSegment).rel_path) || ''} alt="" className="w-full h-full object-cover" />
                    : <div className="w-full h-full flex items-center justify-center text-[10px] text-gray-500">no img</div>}
                </div>
                <div className="flex-1 grid grid-cols-2 sm:grid-cols-4 gap-2 items-end">
                  <div className="col-span-2">
                    <label className="text-[10px] text-gray-500">Strength {(selected as LtxDirectorImageSegment).strength.toFixed(2)}</label>
                    <input type="range" min={0} max={1} step={0.05} value={(selected as LtxDirectorImageSegment).strength}
                      onChange={(e) => updateSeg(selected.id, { strength: parseFloat(e.target.value) } as Partial<LtxDirectorImageSegment>)} className="w-full" />
                  </div>
                  <div>
                    <label className="text-[10px] text-gray-500">Frame</label>
                    <input type="number" min={0} max={totalFrames} value={selected.frame}
                      onChange={(e) => updateSeg(selected.id, { frame: Math.min(Math.max(0, parseInt(e.target.value) || 0), totalFrames) })}
                      className="w-full px-2 py-1 bg-gray-950 border border-gray-700 rounded text-xs text-gray-100" />
                  </div>
                  <div>
                    <label className="text-[10px] text-gray-500">Hold (frames)</label>
                    <input type="number" min={1} max={totalFrames} value={selected.length}
                      onChange={(e) => updateSeg(selected.id, { length: Math.min(Math.max(1, parseInt(e.target.value) || 1), totalFrames) })}
                      className="w-full px-2 py-1 bg-gray-950 border border-gray-700 rounded text-xs text-gray-100" />
                  </div>
                  <button onClick={() => openImagePicker('replace', selected.id)} className="px-2 py-1 rounded bg-gray-700 hover:bg-gray-600 text-gray-100 text-[11px]">Replace…</button>
                  <button onClick={() => removeSeg(selected.id)} className="px-2 py-1 rounded bg-red-900/70 hover:bg-red-800 text-red-200 text-[11px] flex items-center justify-center gap-1"><Trash2 size={12} /> Delete</button>
                </div>
              </div>
            )}
          </div>
        )}

        {/* ── Footer ── */}
        <div className="flex items-center gap-2 px-4 py-2.5 border-t border-gray-800 bg-gray-900">
          <div className="text-[11px] text-gray-500">
            Prompt Relay: each segment conditions its own time span; the global prompt anchors the rest.
          </div>
          <div className="ml-auto flex items-center gap-2">
            <button onClick={onClose} className="px-3 py-1.5 rounded bg-gray-800 hover:bg-gray-700 text-gray-300 text-sm">Close</button>
            <button onClick={handleGenerate} disabled={isGenerating}
              className="px-4 py-1.5 rounded bg-blue-600 hover:bg-blue-500 text-white text-sm font-medium flex items-center gap-1.5 disabled:opacity-50">
              <Wand2 size={15} /> {isGenerating ? 'Generating…' : 'Generate → Queue'}
            </button>
          </div>
        </div>
      </div>

      {/* Pickers */}
      <imagePicker.PickerModals />
      <audioPicker.PickerModals />
      <motionPicker.PickerModals />
      <videoPicker.PickerModals />
    </div>,
    document.body,
  );
}
