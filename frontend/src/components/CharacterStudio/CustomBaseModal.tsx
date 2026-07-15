/**
 * Create Custom Base Image (Advanced) — freehand generator: write a prompt,
 * add reference images, LLM-enhance the prompt, pick the first-pass model, and
 * generate. Result lands as a new base version and auto-activates.
 */
import { useState } from 'react';
import { X, Wand2, Upload, Loader2, Sparkles, Trash2 } from 'lucide-react';

function assetUrl(pid: string | null | undefined, aid: string | null | undefined) {
  if (!pid || !aid) return null;
  return `/api/projects/${pid}/assets/${aid}/file`;
}

export function CustomBaseModal({
  characterId,
  studioProjectId,
  defaultModel,
  onClose,
  onGenerated,
  api,
}: {
  characterId: string;
  studioProjectId: string | null | undefined;
  defaultModel: string;
  onClose: () => void;
  onGenerated: () => void;
  api: {
    enhanceBasePrompt: (id: string, data: { prompt: string; reference_asset_ids?: string[] }) => Promise<{ enhanced_prompt: string }>;
    generateBaseAdvanced: (id: string, data: { prompt: string; model?: string; reference_asset_ids?: string[]; control_asset_id?: string; lllite_name?: string; img2img_asset_id?: string; denoise?: number; negative?: string }) => Promise<{ job_id: string; prompt: string }>;
  };
}) {
  const [prompt, setPrompt] = useState('');
  const [model, setModel] = useState('');
  const [refs, setRefs] = useState<string[]>([]);
  const [controlAssetId, setControlAssetId] = useState<string>('');
  const [lllite, setLllite] = useState('anima-lllite-pose-1.safetensors');
  const [uploadingControl, setUploadingControl] = useState(false);
  const [img2imgAssetId, setImg2imgAssetId] = useState<string>('');
  const [uploadingI2I, setUploadingI2I] = useState(false);
  const [busy, setBusy] = useState(false);
  const [enhancing, setEnhancing] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const uploadRefs = async (fileList: File[]) => {
    if (!studioProjectId) return;
    setUploading(true);
    setError(null);
    try {
      for (const f of fileList) {
        const fd = new FormData();
        fd.append('asset_type', 'reference');
        fd.append('file', f);
        const res = await fetch(`/api/projects/${studioProjectId}/assets/upload`, { method: 'POST', body: fd });
        if (!res.ok) throw new Error(`Upload failed (${res.status})`);
        const a = await res.json();
        setRefs((prev) => (prev.length < 5 ? [...prev, a.id] : prev));
      }
    } catch (e: any) {
      setError(e.message);
    } finally {
      setUploading(false);
    }
  };

  const uploadControl = async (file: File) => {
    if (!studioProjectId) return;
    setUploadingControl(true);
    setError(null);
    try {
      const fd = new FormData();
      fd.append('asset_type', 'reference');
      fd.append('file', file);
      const res = await fetch(`/api/projects/${studioProjectId}/assets/upload`, { method: 'POST', body: fd });
      if (!res.ok) throw new Error(`Upload failed (${res.status})`);
      const a = await res.json();
      setControlAssetId(a.id);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setUploadingControl(false);
    }
  };

  const uploadI2I = async (file: File) => {
    if (!studioProjectId) return;
    setUploadingI2I(true);
    setError(null);
    try {
      const fd = new FormData();
      fd.append('asset_type', 'reference');
      fd.append('file', file);
      const res = await fetch(`/api/projects/${studioProjectId}/assets/upload`, { method: 'POST', body: fd });
      if (!res.ok) throw new Error(`Upload failed (${res.status})`);
      const a = await res.json();
      setImg2imgAssetId(a.id);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setUploadingI2I(false);
    }
  };

  const enhance = async () => {
    setEnhancing(true);
    setError(null);
    try {
      const res = await api.enhanceBasePrompt(characterId, { prompt, reference_asset_ids: refs });
      if (res.enhanced_prompt) setPrompt(res.enhanced_prompt);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setEnhancing(false);
    }
  };

  const generate = async () => {
    if (!prompt.trim() && refs.length === 0) {
      setError('Write a prompt and/or add reference images.');
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await api.generateBaseAdvanced(characterId, {
        prompt: prompt.trim(),
        model: !controlAssetId && refs.length === 0 ? model || undefined : undefined,
        reference_asset_ids: !controlAssetId && refs.length ? refs : undefined,
        control_asset_id: !img2imgAssetId && controlAssetId ? controlAssetId : undefined,
        lllite_name: !img2imgAssetId && controlAssetId ? lllite : undefined,
        img2img_asset_id: img2imgAssetId || undefined,
      });
      onGenerated();
      onClose();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="fixed inset-0 bg-black/70 z-[9990] flex items-center justify-center p-4" onClick={onClose}>
      <div className="bg-gray-900 border border-gray-800 rounded-lg p-5 w-full max-w-2xl max-h-[90vh] overflow-y-auto flex flex-col gap-4" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between">
          <h2 className="text-lg font-semibold flex items-center gap-2"><Wand2 size={18} className="text-indigo-400" /> Create Custom Base Image (Advanced)</h2>
          <button onClick={onClose} className="text-gray-500 hover:text-gray-300"><X size={18} /></button>
        </div>

        <label className="flex flex-col gap-1 text-sm">
          <span className="text-gray-400">Prompt</span>
          <textarea
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            rows={5}
            placeholder="Describe the base image freehand, or a rough brief — then hit Enhance to have the LLM build the optimal prompt from the character sheet."
            className="bg-gray-800 border border-gray-700 rounded-md px-3 py-2 text-gray-100 text-sm resize-y focus:outline-none focus:border-indigo-600"
          />
        </label>

        <div className="flex flex-wrap items-center gap-2">
          <button onClick={enhance} disabled={enhancing} className="px-3 py-1.5 bg-purple-700 hover:bg-purple-600 disabled:opacity-50 rounded-md text-sm font-medium flex items-center gap-2">
            {enhancing ? <Loader2 size={14} className="animate-spin" /> : <Sparkles size={14} />}
            Enhance with LLM
          </button>
          <span className="text-xs text-gray-500">Uses the character sheet (name, style, traits) as context.</span>
        </div>

        {/* Reference images */}
        <div className="flex flex-col gap-2">
          <div className="flex items-center gap-2">
            <span className="text-sm text-gray-400">Reference images ({refs.length}/5)</span>
            <label className="px-2.5 py-1 bg-gray-800 hover:bg-gray-700 border border-gray-700 rounded-md text-xs flex items-center gap-1.5 cursor-pointer">
              {uploading ? <Loader2 size={12} className="animate-spin" /> : <Upload size={12} />}
              Add
              <input type="file" accept="image/*" multiple className="hidden" onChange={(e) => { const fs = Array.from(e.target.files || []); if (fs.length) uploadRefs(fs); e.target.value = ''; }} />
            </label>
            <span className="text-xs text-gray-600">With references → Klein edit; none → text-to-image.</span>
          </div>
          {refs.length > 0 && (
            <div className="flex flex-wrap gap-2">
              {refs.map((rid) => (
                <div key={rid} className="relative w-16 h-16">
                  <img src={assetUrl(studioProjectId, rid) || ''} alt="ref" className="w-16 h-16 object-cover rounded border border-gray-700" />
                  <button onClick={() => setRefs((prev) => prev.filter((x) => x !== rid))} className="absolute -top-1.5 -right-1.5 bg-gray-900 border border-gray-700 rounded-full p-0.5 text-red-400 hover:text-red-300">
                    <Trash2 size={11} />
                  </button>
                </div>
              ))}
            </div>
          )}
        </div>

        <div className="flex flex-col gap-2 border border-gray-800 rounded-md p-3 bg-gray-800/30">
          <span className="text-sm text-gray-300">Anima ControlNet (LLLite) — optional</span>
          <span className="text-xs text-gray-500">
            Guide an Anima render with a control image (pose skeleton, depth map, etc.). When set,
            generation uses Anima + the chosen LLLite model and ignores the reference images/model above.
          </span>
          <div className="flex items-center gap-2 flex-wrap">
            <label className="px-3 py-1.5 bg-gray-800 hover:bg-gray-700 border border-gray-700 rounded-md text-sm flex items-center gap-2 cursor-pointer w-fit">
              {uploadingControl ? <Loader2 size={13} className="animate-spin" /> : <Upload size={13} />}
              {controlAssetId ? 'Replace' : 'Upload'} control image
              <input type="file" accept="image/*" className="hidden" onChange={(e) => { const f = e.target.files?.[0]; if (f) uploadControl(f); e.target.value = ''; }} />
            </label>
            {controlAssetId && (
              <>
                <img src={assetUrl(studioProjectId, controlAssetId) || ''} alt="control" className="w-14 h-14 object-cover rounded border border-gray-700" />
                <button onClick={() => setControlAssetId('')} className="text-xs text-red-400 hover:text-red-300">Remove</button>
                <select value={lllite} onChange={(e) => setLllite(e.target.value)} className="bg-gray-800 border border-gray-700 rounded-md px-2 py-1 text-sm">
                  <option value="anima-lllite-pose-1.safetensors">Pose (LLLite)</option>
                  <option value="anima-lllite-inpainting-v1.safetensors">Inpainting (LLLite)</option>
                </select>
              </>
            )}
          </div>
        </div>

        <div className="flex flex-col gap-1.5 p-2.5 bg-gray-900 border border-gray-800 rounded-md">
          <span className="text-xs text-gray-400">
            Anima img2img: transform a source image with your prompt (overrides refs/model/control).
          </span>
          <div className="flex items-center gap-2 flex-wrap">
            <label className="px-3 py-1.5 bg-gray-800 hover:bg-gray-700 border border-gray-700 rounded-md text-sm flex items-center gap-2 cursor-pointer w-fit">
              {uploadingI2I ? <Loader2 size={13} className="animate-spin" /> : <Upload size={13} />}
              {img2imgAssetId ? 'Replace' : 'Upload'} img2img source
              <input type="file" accept="image/*" className="hidden" onChange={(e) => { const f = e.target.files?.[0]; if (f) uploadI2I(f); e.target.value = ''; }} />
            </label>
            {img2imgAssetId && (
              <>
                <img src={assetUrl(studioProjectId, img2imgAssetId) || ''} alt="img2img" className="w-14 h-14 object-cover rounded border border-gray-700" />
                <button onClick={() => setImg2imgAssetId('')} className="text-xs text-red-400 hover:text-red-300">Remove</button>
              </>
            )}
          </div>
        </div>

        {!controlAssetId && !img2imgAssetId && refs.length === 0 && (
          <label className="flex flex-col gap-1 text-sm">
            <span className="text-gray-400">Model (text-to-image)</span>
            <select value={model} onChange={(e) => setModel(e.target.value)} className="bg-gray-800 border border-gray-700 rounded-md px-3 py-2 text-sm">
              <option value="">Use First Frame default ({defaultModel})</option>
              <option value="z_image_turbo">Z-Image Turbo</option>
              <option value="krea2_turbo">Krea 2 Turbo</option>
              <option value="anima">Anima (anime base)</option>
              <option value="flux2_klein_dev_9b">FLUX.2 Klein T2I</option>
            </select>
          </label>
        )}

        {error && <div className="text-sm text-red-400">{error}</div>}

        <div className="flex items-center gap-3">
          <button onClick={generate} disabled={busy} className="px-4 py-2 bg-indigo-700 hover:bg-indigo-600 disabled:opacity-50 rounded-md text-sm font-medium flex items-center gap-2">
            {busy && <Loader2 size={14} className="animate-spin" />}
            <Wand2 size={14} /> Generate Base
          </button>
          <span className="text-xs text-gray-600">Renders as a new version and becomes active when done.</span>
        </div>
      </div>
    </div>
  );
}
