import { useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { X, Upload, Loader2, Check, UserSquare2, Info } from 'lucide-react';
import { uploadAsset, setTalkieConfig, getAssetFileUrl } from '@/api/client';

const ENGINES = [
  { value: 'lipsync_ltx', label: 'LTX-2.3 (ready)', note: 'Uses your existing LTX-2.3. Natural head motion. No install needed.' },
  { value: 'lipsync_latentsync', label: 'LatentSync 1.6', note: 'Best-looking stationary head. Needs a LatentSync worker + workflows/LIPSYNC_LATENTSYNC.json.' },
  { value: 'lipsync_musetalk', label: 'MuseTalk 1.5', note: 'Fastest, truly stationary (mouth-only). Needs a MuseTalk worker + workflows/LIPSYNC_MUSETALK.json.' },
  { value: 'lipsync_sonic', label: 'Sonic', note: 'Expressive, emotion-carrying motion. Needs a Sonic worker + workflows/LIPSYNC_SONIC.json.' },
];

interface Props {
  projectId: string;
  currentPortraitAssetId?: string | null;
  currentEngine?: string;
  onClose: () => void;
  onSaved: (portraitAssetId: string | null, engine: string) => void;
}

export default function TalkieSetupModal({ projectId, currentPortraitAssetId, currentEngine, onClose, onSaved }: Props) {
  const [portraitId, setPortraitId] = useState<string | null>(currentPortraitAssetId || null);
  const [engine, setEngine] = useState<string>(currentEngine || 'lipsync_ltx');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  const uploadPortrait = async (file: File) => {
    setBusy(true); setErr(null);
    try {
      const fd = new FormData();
      fd.append('file', file);
      fd.append('asset_type', 'reference');
      const res = await uploadAsset(projectId, fd);
      const asset = Array.isArray(res.data) ? res.data[0] : res.data;
      const aid = (asset as { id?: string })?.id;
      if (aid) {
        setPortraitId(aid);
        await setTalkieConfig(projectId, { portrait_asset_id: aid });
      }
    } catch (e: any) {
      setErr(e?.response?.data?.detail || e?.message || 'Upload failed');
    } finally { setBusy(false); }
  };

  const save = async () => {
    setBusy(true); setErr(null);
    try {
      const res = await setTalkieConfig(projectId, { portrait_asset_id: portraitId, talkie_engine: engine });
      onSaved(res.data.portrait_asset_id, res.data.talkie_engine);
      onClose();
    } catch (e: any) {
      setErr(e?.response?.data?.detail || e?.message || 'Save failed');
    } finally { setBusy(false); }
  };

  const selEngine = ENGINES.find((e) => e.value === engine);

  return createPortal(
    <div className="fixed inset-0 z-[9994] bg-black/80 flex items-center justify-center p-4" onClick={onClose}>
      <div className="bg-gray-900 rounded-xl border border-gray-700 w-full max-w-lg max-h-[92vh] flex flex-col overflow-hidden" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center gap-2 px-4 py-3 border-b border-gray-700">
          <UserSquare2 className="w-5 h-5 text-indigo-400" />
          <h3 className="font-semibold text-gray-100">Talkie Setup</h3>
          <button onClick={onClose} className="ml-auto p-1.5 rounded hover:bg-gray-800 text-gray-400"><X className="w-5 h-5" /></button>
        </div>

        <div className="flex-1 overflow-y-auto p-4 space-y-4">
          {/* Portrait */}
          <div>
            <label className="text-xs font-medium text-gray-400 mb-1.5 block">Source portrait (lip-synced in every scene)</label>
            <div className="flex items-center gap-3">
              <div className="w-24 h-24 rounded-lg border border-gray-700 bg-gray-950 overflow-hidden flex items-center justify-center flex-shrink-0">
                {portraitId ? (
                  <img src={getAssetFileUrl(projectId, portraitId)} className="w-full h-full object-cover" alt="portrait" />
                ) : (
                  <UserSquare2 className="w-8 h-8 text-gray-700" />
                )}
              </div>
              <div className="flex-1">
                <input ref={fileRef} type="file" accept="image/*" className="hidden"
                  onChange={(e) => { const f = e.target.files?.[0]; if (f) uploadPortrait(f); e.target.value = ''; }} />
                <button onClick={() => fileRef.current?.click()} disabled={busy}
                  className="flex items-center gap-2 px-3 py-2 rounded-lg bg-gray-800 hover:bg-gray-700 text-sm font-medium disabled:opacity-50">
                  {busy ? <Loader2 className="w-4 h-4 animate-spin" /> : <Upload className="w-4 h-4" />}
                  {portraitId ? 'Replace portrait' : 'Upload portrait'}
                </button>
                <p className="text-[11px] text-gray-500 mt-1.5">Front-facing, mouth unobstructed, good lighting works best.</p>
              </div>
            </div>
          </div>

          {/* Engine */}
          <div>
            <label className="text-xs font-medium text-gray-400 mb-1.5 block">Lip-sync engine</label>
            <select value={engine} onChange={(e) => setEngine(e.target.value)}
              className="w-full rounded-md bg-gray-800 border border-gray-700 px-3 py-2 text-sm text-gray-100">
              {ENGINES.map((e) => <option key={e.value} value={e.value}>{e.label}</option>)}
            </select>
            {selEngine && (
              <p className="text-[11px] text-gray-500 mt-1.5 flex items-start gap-1.5">
                <Info className="w-3.5 h-3.5 mt-0.5 flex-shrink-0" /> {selEngine.note}
              </p>
            )}
          </div>

          {err && <div className="text-xs text-red-400 bg-red-950/40 rounded px-2 py-1">{err}</div>}
        </div>

        <div className="border-t border-gray-700 p-3">
          <button onClick={save} disabled={busy || !portraitId}
            className="w-full py-2.5 rounded-lg bg-indigo-600 hover:bg-indigo-500 font-semibold disabled:opacity-50 flex items-center justify-center gap-2">
            {busy ? <Loader2 className="w-4 h-4 animate-spin" /> : <Check className="w-4 h-4" />}
            Save Talkie setup
          </button>
          {!portraitId && <p className="text-[11px] text-amber-400 mt-1.5 text-center">Upload a portrait to continue.</p>}
        </div>
      </div>
    </div>,
    document.body,
  );
}
