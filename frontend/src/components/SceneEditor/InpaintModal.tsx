/**
 * InpaintModal — Klein mask-paint inpainting of a rendered image.
 *
 * Draw a mask over the displayed image (like ComfyUI's mask editor), write a
 * prompt, and optionally guide it with a reference image (upload / project asset
 * / character) — whole or a cropped selection. On Generate we bake the mask into
 * the source's alpha channel (clipspace convention: painted = transparent →
 * inpaint there), upload it + the reference, and enqueue a `klein_inpaint` job.
 * The result comes back as a new image version on the scene.
 */
import { useState, useEffect, useRef, useCallback } from 'react';
import { createPortal } from 'react-dom';
import {
  X, Brush, Eraser, Trash2, Wand2, Upload, Crop, Loader2, Check,
} from 'lucide-react';
import { useAssetPicker } from '../AssetManager/AssetPickerModal';
import { uploadAsset, inpaintImage, getSceneVersions } from '@/api/client';
import type { Asset } from '@/types/index';

interface InpaintCharacter { name?: string; image_path?: string }

interface InpaintModalProps {
  projectId: string;
  sceneId: string;
  imageUrl: string;
  assets: Asset[];
  characters: InpaintCharacter[];
  onClose: () => void;
  onSaveAsPreview?: (outputPath: string) => void | Promise<void>;
  onComplete?: () => void;
}

type RefMode = 'none' | 'image';
interface RefImage { url: string; assetId?: string; file?: File }

function dataURLToBlob(canvas: HTMLCanvasElement): Promise<Blob> {
  return new Promise((res, rej) => canvas.toBlob((b) => (b ? res(b) : rej(new Error('toBlob failed'))), 'image/png'));
}

export default function InpaintModal({
  projectId, sceneId, imageUrl, assets, characters, onClose, onSaveAsPreview, onComplete,
}: InpaintModalProps) {
  const imgRef = useRef<HTMLImageElement>(null);
  const maskRef = useRef<HTMLCanvasElement>(null);
  const [natural, setNatural] = useState<{ w: number; h: number } | null>(null);

  const [brush, setBrush] = useState(40);
  const [erasing, setErasing] = useState(false);
  const [prompt, setPrompt] = useState('');
  const [hasMask, setHasMask] = useState(false);

  // Reference
  const [refMode, setRefMode] = useState<RefMode>('none');
  const [refImage, setRefImage] = useState<RefImage | null>(null);
  const [cropOn, setCropOn] = useState(false);
  const [crop, setCrop] = useState<{ x: number; y: number; w: number; h: number } | null>(null);
  const refImgRef = useRef<HTMLImageElement>(null);

  const [stage, setStage] = useState<'edit' | 'generating' | 'result'>('edit');
  const [resultPath, setResultPath] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // ── Size the mask canvas to the image's natural resolution on load ──
  const onImgLoad = useCallback(() => {
    const im = imgRef.current; if (!im) return;
    const w = im.naturalWidth || im.width;
    const h = im.naturalHeight || im.height;
    setNatural({ w, h });
    const c = maskRef.current;
    if (c) { c.width = w; c.height = h; }
  }, []);

  // ── Mask painting ──
  const drawing = useRef(false);
  const toCanvasXY = (e: React.PointerEvent) => {
    const c = maskRef.current!; const r = c.getBoundingClientRect();
    return { x: ((e.clientX - r.left) / r.width) * c.width, y: ((e.clientY - r.top) / r.height) * c.height };
  };
  const paint = (e: React.PointerEvent) => {
    const c = maskRef.current; if (!c) return;
    const ctx = c.getContext('2d'); if (!ctx) return;
    const { x, y } = toCanvasXY(e);
    const scale = c.width / (c.getBoundingClientRect().width || c.width);
    ctx.globalCompositeOperation = erasing ? 'destination-out' : 'source-over';
    ctx.fillStyle = 'rgba(239,68,68,0.55)';
    ctx.beginPath();
    ctx.arc(x, y, Math.max(2, (brush * scale) / 2), 0, Math.PI * 2);
    ctx.fill();
    setHasMask(true);
  };
  const onMaskDown = (e: React.PointerEvent) => { e.preventDefault(); drawing.current = true; (e.target as HTMLElement).setPointerCapture?.(e.pointerId); paint(e); };
  const onMaskMove = (e: React.PointerEvent) => { if (drawing.current) paint(e); };
  const onMaskUp = (e: React.PointerEvent) => { drawing.current = false; (e.target as HTMLElement).releasePointerCapture?.(e.pointerId); };
  const clearMask = () => {
    const c = maskRef.current; if (!c) return;
    c.getContext('2d')?.clearRect(0, 0, c.width, c.height);
    setHasMask(false);
  };

  // ── Reference picker (upload / from assets) ──
  const onRefAsset = useCallback((a: Asset) => {
    setRefImage({ url: `/api/projects/${a.project_id}/assets/${a.id}/file`, assetId: a.id });
    setRefMode('image'); setCrop(null);
  }, []);
  const onRefUpload = useCallback((file: File) => {
    setRefImage({ url: URL.createObjectURL(file), file });
    setRefMode('image'); setCrop(null);
  }, []);
  const refPicker = useAssetPicker({
    assets, onFileUpload: onRefUpload, onAssetSelect: onRefAsset,
    accept: 'image/*', imagesOnly: true, title: 'Inpaint reference image',
  });
  const pickCharacter = (rel?: string) => {
    if (!rel) { setRefImage(null); setRefMode('none'); return; }
    setRefImage({ url: `/api/files/${rel}` }); setRefMode('image'); setCrop(null);
  };

  // ── Crop drag on the reference preview ──
  const cropDrag = useRef<{ x: number; y: number } | null>(null);
  const refXY = (e: React.PointerEvent) => {
    const im = refImgRef.current!; const r = im.getBoundingClientRect();
    const nx = (e.clientX - r.left) / r.width, ny = (e.clientY - r.top) / r.height;
    return { x: Math.min(1, Math.max(0, nx)), y: Math.min(1, Math.max(0, ny)) };
  };
  const onCropDown = (e: React.PointerEvent) => { if (!cropOn) return; e.preventDefault(); cropDrag.current = refXY(e); setCrop(null); };
  const onCropMove = (e: React.PointerEvent) => {
    if (!cropOn || !cropDrag.current) return;
    const a = cropDrag.current; const b = refXY(e);
    setCrop({ x: Math.min(a.x, b.x), y: Math.min(a.y, b.y), w: Math.abs(b.x - a.x), h: Math.abs(b.y - a.y) });
  };
  const onCropUp = () => { cropDrag.current = null; };

  // ── Build the RGBA masked source (mask in alpha; painted = transparent) ──
  const buildMaskedSource = useCallback(async (): Promise<Blob> => {
    const w = natural?.w || imgRef.current?.naturalWidth || 0;
    const h = natural?.h || imgRef.current?.naturalHeight || 0;
    if (!w || !h) throw new Error('source image not ready');
    const off = document.createElement('canvas'); off.width = w; off.height = h;
    const octx = off.getContext('2d')!;
    // draw a fresh copy of the source at natural size
    const src = new Image(); src.src = imageUrl;
    await new Promise((r, j) => { src.onload = r; src.onerror = j; });
    octx.drawImage(src, 0, 0, w, h);
    const out = octx.getImageData(0, 0, w, h);
    const mctx = maskRef.current!.getContext('2d')!;
    const md = mctx.getImageData(0, 0, w, h).data;
    for (let i = 0; i < md.length; i += 4) {
      // painted (mask) pixel -> make source transparent so LoadImage MASK = 1 there
      if (md[i + 3] > 10) out.data[i + 3] = 0;
    }
    octx.putImageData(out, 0, 0);
    return dataURLToBlob(off);
  }, [natural, imageUrl]);

  // ── Build the reference blob (whole or cropped) ──
  const buildReferenceBlob = useCallback(async (): Promise<Blob | null> => {
    if (refMode !== 'image' || !refImage) return null;
    const im = new Image(); im.crossOrigin = 'anonymous'; im.src = refImage.url;
    await new Promise((r, j) => { im.onload = r; im.onerror = j; });
    const nw = im.naturalWidth, nh = im.naturalHeight;
    let sx = 0, sy = 0, sw = nw, sh = nh;
    if (cropOn && crop && crop.w > 0.02 && crop.h > 0.02) {
      sx = Math.round(crop.x * nw); sy = Math.round(crop.y * nh);
      sw = Math.round(crop.w * nw); sh = Math.round(crop.h * nh);
    }
    const c = document.createElement('canvas'); c.width = sw; c.height = sh;
    c.getContext('2d')!.drawImage(im, sx, sy, sw, sh, 0, 0, sw, sh);
    return dataURLToBlob(c);
  }, [refMode, refImage, cropOn, crop]);

  const uploadBlob = async (blob: Blob, name: string, assetType: string): Promise<string> => {
    const fd = new FormData();
    fd.append('file', new File([blob], name, { type: 'image/png' }));
    fd.append('asset_type', assetType);
    const res = await uploadAsset(projectId, fd);
    return res.data.id;
  };

  // ── Generate ──
  const handleGenerate = async () => {
    setError(null);
    if (!hasMask) { setError('Paint a mask over the area to change first.'); return; }
    setStage('generating');
    try {
      const before = await getSceneVersions(projectId, sceneId).then((r) => (r.data || []).length).catch(() => 0);

      const maskedBlob = await buildMaskedSource();
      const sourceMaskedId = await uploadBlob(maskedBlob, `inpaint_source_${Date.now()}.png`, 'reference');

      let referenceAssetId: string | undefined;
      if (refMode === 'image' && refImage) {
        if (refImage.assetId && !(cropOn && crop)) {
          referenceAssetId = refImage.assetId;            // existing asset, whole image
        } else {
          const refBlob = await buildReferenceBlob();
          if (refBlob) referenceAssetId = await uploadBlob(refBlob, `inpaint_ref_${Date.now()}.png`, 'reference');
        }
      }

      await inpaintImage(projectId, {
        scene_id: sceneId,
        source_masked_asset_id: sourceMaskedId,
        reference_asset_id: referenceAssetId,
        prompt: prompt.trim(),
      });

      // Poll for the new version
      const deadline = Date.now() + 5 * 60 * 1000;
      while (Date.now() < deadline) {
        await new Promise((r) => setTimeout(r, 2500));
        const versions = await getSceneVersions(projectId, sceneId).then((r) => r.data || []).catch(() => []);
        if (versions.length > before) {
          const newest = versions[0] as any;   // newest first
          const out = newest?.output_path;
          if (out) { setResultPath(out); setStage('result'); onComplete?.(); return; }
        }
      }
      setError('Inpaint timed out — check the Generation Queue; the version may still appear.');
      setStage('edit');
    } catch (e: any) {
      console.error(e);
      setError(e?.response?.data?.detail || e?.message || 'Inpaint failed.');
      setStage('edit');
    }
  };

  // object-url cleanup
  useEffect(() => () => { if (refImage?.file && refImage.url.startsWith('blob:')) URL.revokeObjectURL(refImage.url); }, [refImage]);

  const charsWithImg = characters.filter((c) => c.image_path);

  return createPortal(
    <div style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.85)', zIndex: 9995, display: 'flex' }}
      onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="m-2 sm:m-4 flex-1 min-h-0 flex flex-col bg-gray-950 border border-gray-700 rounded-xl overflow-hidden shadow-2xl">
        {/* Header */}
        <div className="flex items-center justify-between px-4 py-2.5 border-b border-gray-800 bg-gray-900">
          <div className="flex items-center gap-2 text-sm font-bold text-gray-100"><Brush size={16} className="text-pink-400" /> Inpaint (mask-paint edit)</div>
          <button onClick={onClose} className="p-1.5 rounded bg-gray-800 hover:bg-gray-700 text-gray-300"><X size={16} /></button>
        </div>

        <div className="flex-1 min-h-0 flex flex-col lg:flex-row">
          {/* ── Canvas / result ── */}
          <div className="flex-1 min-h-0 bg-black/40 flex items-center justify-center p-3 overflow-auto">
            {stage === 'result' && resultPath ? (
              <div className="flex flex-col items-center gap-3">
                <img src={`/api/files/${resultPath}`} alt="result" className="max-w-full max-h-[70vh] object-contain rounded border border-gray-700" />
                <div className="text-[12px] text-emerald-400">Inpaint complete — saved as a new version.</div>
              </div>
            ) : (
              <div className="relative inline-block max-w-full">
                <img ref={imgRef} src={imageUrl} alt="source" onLoad={onImgLoad}
                  className="max-w-full max-h-[72vh] object-contain block select-none pointer-events-none" />
                <canvas ref={maskRef}
                  onPointerDown={onMaskDown} onPointerMove={onMaskMove} onPointerUp={onMaskUp}
                  className="absolute inset-0 w-full h-full cursor-crosshair"
                  style={{ touchAction: 'none' }} />
                {stage === 'generating' && (
                  <div className="absolute inset-0 flex items-center justify-center bg-black/60 text-gray-200 text-sm gap-2">
                    <Loader2 size={18} className="animate-spin" /> Inpainting…
                  </div>
                )}
              </div>
            )}
          </div>

          {/* ── Controls ── */}
          <div className="w-full lg:w-80 shrink-0 border-t lg:border-t-0 lg:border-l border-gray-800 bg-gray-900/60 p-3 space-y-3 overflow-y-auto">
            {stage === 'result' ? (
              <div className="space-y-2">
                <button
                  onClick={async () => { if (resultPath && onSaveAsPreview) { await onSaveAsPreview(resultPath); setSaved(true); } }}
                  disabled={saved}
                  className="w-full px-3 py-2 rounded bg-emerald-600 hover:bg-emerald-500 text-white text-sm font-medium flex items-center justify-center gap-1.5 disabled:opacity-60">
                  {saved ? <><Check size={15} /> Saved as preview</> : 'Save as scene preview'}
                </button>
                <button onClick={() => { setStage('edit'); setResultPath(null); setSaved(false); }}
                  className="w-full px-3 py-2 rounded bg-gray-800 hover:bg-gray-700 text-gray-200 text-sm">Inpaint again</button>
                <button onClick={onClose} className="w-full px-3 py-2 rounded bg-gray-800 hover:bg-gray-700 text-gray-300 text-sm">Close</button>
              </div>
            ) : (
              <>
                {/* Brush tools */}
                <div>
                  <div className="text-[11px] font-semibold text-gray-400 mb-1">Mask brush</div>
                  <div className="flex items-center gap-2">
                    <button onClick={() => setErasing(false)} className={`px-2 py-1.5 rounded text-[11px] flex items-center gap-1 ${!erasing ? 'bg-pink-600 text-white' : 'bg-gray-800 text-gray-300'}`}><Brush size={13} /> Paint</button>
                    <button onClick={() => setErasing(true)} className={`px-2 py-1.5 rounded text-[11px] flex items-center gap-1 ${erasing ? 'bg-pink-600 text-white' : 'bg-gray-800 text-gray-300'}`}><Eraser size={13} /> Erase</button>
                    <button onClick={clearMask} className="ml-auto px-2 py-1.5 rounded text-[11px] bg-gray-800 hover:bg-gray-700 text-gray-300 flex items-center gap-1"><Trash2 size={12} /> Clear</button>
                  </div>
                  <div className="flex items-center gap-2 mt-2">
                    <span className="text-[10px] text-gray-500">Size</span>
                    <input type="range" min={6} max={160} value={brush} onChange={(e) => setBrush(parseInt(e.target.value))} className="flex-1" />
                    <span className="text-[10px] text-gray-400 w-7 text-right tabular-nums">{brush}</span>
                  </div>
                  <p className="text-[10px] text-gray-500 mt-1">Paint over the area you want regenerated.</p>
                </div>

                {/* Prompt */}
                <div>
                  <label className="text-[11px] font-semibold text-gray-400">Prompt (what should be in the masked area)</label>
                  <textarea value={prompt} onChange={(e) => setPrompt(e.target.value)}
                    placeholder="e.g. a small brass key on the table; or describe the fix you want"
                    className="w-full h-20 mt-1 px-2 py-1.5 bg-gray-950 border border-gray-700 rounded text-xs text-gray-100 resize-y focus:outline-none focus:border-pink-500" />
                </div>

                {/* Reference */}
                <div className="p-2.5 bg-gray-900 border border-gray-800 rounded space-y-2">
                  <div className="text-[11px] font-semibold text-gray-300">Reference (optional)</div>
                  <p className="text-[10px] text-gray-500">Add an object/character to place into the masked area — or leave blank to inpaint from the image + prompt alone.</p>
                  <div className="flex flex-wrap gap-1.5">
                    <button onClick={() => { setRefMode('none'); setRefImage(null); }} className={`px-2 py-1 rounded text-[11px] ${refMode === 'none' ? 'bg-pink-600 text-white' : 'bg-gray-800 text-gray-300 hover:bg-gray-700'}`}>None</button>
                    <button onClick={() => refPicker.openPicker()} className="px-2 py-1 rounded text-[11px] bg-gray-800 text-gray-300 hover:bg-gray-700 flex items-center gap-1"><Upload size={12} /> Upload / asset</button>
                  </div>
                  {charsWithImg.length > 0 && (
                    <select onChange={(e) => pickCharacter(e.target.value || undefined)} value={''}
                      className="w-full px-2 py-1 bg-gray-950 border border-gray-700 rounded text-[11px] text-gray-200">
                      <option value="">Pick a character…</option>
                      {charsWithImg.map((c, i) => <option key={i} value={c.image_path}>{c.name || `Character ${i + 1}`}</option>)}
                    </select>
                  )}
                  {refMode === 'image' && refImage && (
                    <div className="space-y-1.5">
                      <label className="flex items-center gap-1.5 text-[10px] text-gray-400"><input type="checkbox" checked={cropOn} onChange={(e) => { setCropOn(e.target.checked); if (!e.target.checked) setCrop(null); }} /><Crop size={11} /> Crop a region of the reference</label>
                      <div className="relative inline-block max-w-full" onPointerDown={onCropDown} onPointerMove={onCropMove} onPointerUp={onCropUp} style={{ touchAction: 'none' }}>
                        <img ref={refImgRef} src={refImage.url} alt="ref" className="max-w-full max-h-40 object-contain rounded border border-gray-700 block select-none" />
                        {cropOn && crop && crop.w > 0 && (
                          <div className="absolute border-2 border-pink-400 bg-pink-400/20 pointer-events-none"
                            style={{ left: `${crop.x * 100}%`, top: `${crop.y * 100}%`, width: `${crop.w * 100}%`, height: `${crop.h * 100}%` }} />
                        )}
                      </div>
                      {cropOn && <p className="text-[10px] text-gray-500">Drag on the image to select the part to use.</p>}
                    </div>
                  )}
                </div>

                {error && <div className="text-[11px] text-red-400">{error}</div>}

                <button onClick={handleGenerate} disabled={stage !== 'edit'}
                  className="w-full px-3 py-2 rounded bg-pink-600 hover:bg-pink-500 text-white text-sm font-medium flex items-center justify-center gap-1.5 disabled:opacity-50">
                  <Wand2 size={15} /> Generate inpaint
                </button>
              </>
            )}
          </div>
        </div>
      </div>
      <refPicker.PickerModals />
    </div>,
    document.body,
  );
}
