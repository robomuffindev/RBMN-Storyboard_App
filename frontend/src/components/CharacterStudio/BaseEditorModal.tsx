/**
 * Base image editor — versions + restyle. Opened from the Sheet tab's Base
 * Render section when a base exists. Pick which stored version is the active
 * base, view any version in a lightbox, and restyle the current base with the
 * Klein edit model (character style / custom / reference image / project style)
 * to produce a new version.
 */
import { useEffect, useState } from 'react';
import { X, Check, Loader2, Wand2, Upload } from 'lucide-react';
import { StyleSelect, DEFAULT_STYLE } from './characterStudioStyles';

interface BaseVersionT {
  asset_id: string;
  image_rel?: string;
  source?: string;
  style?: string;
  created_at?: string | null;
}

function assetUrl(pid: string | null | undefined, aid: string | null | undefined) {
  if (!pid || !aid) return null;
  return `/api/projects/${pid}/assets/${aid}/file`;
}

type RestyleSource = 'character' | 'custom' | 'reference' | 'project';

export function BaseEditorModal({
  characterId,
  studioProjectId,
  versions,
  activeAssetId,
  characterStyle,
  onClose,
  onChanged,
  api,
}: {
  characterId: string;
  studioProjectId: string | null | undefined;
  versions: BaseVersionT[];
  activeAssetId: string | null | undefined;
  characterStyle: string;
  onClose: () => void;
  onChanged: () => void;
  api: {
    setActiveBase: (id: string, assetId: string) => Promise<any>;
    restyleBase: (id: string, data: { style_key?: string; reference_asset_id?: string; project_id?: string; extra?: string }) => Promise<any>;
  };
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [lightbox, setLightbox] = useState<string | null>(null);

  const [source, setSource] = useState<RestyleSource>('character');
  const [customStyle, setCustomStyle] = useState(characterStyle || DEFAULT_STYLE);
  const [extra, setExtra] = useState('');
  const [projects, setProjects] = useState<{ id: string; name: string }[]>([]);
  const [projectId, setProjectId] = useState('');
  const [refAssetId, setRefAssetId] = useState<string | null>(null);
  const [refUrl, setRefUrl] = useState<string | null>(null);
  const [uploadingRef, setUploadingRef] = useState(false);

  useEffect(() => {
    if (source === 'project' && projects.length === 0) {
      fetch('/api/projects')
        .then((r) => (r.ok ? r.json() : []))
        .then((rows) => setProjects((rows || []).map((p: any) => ({ id: p.id, name: p.name }))))
        .catch(() => {});
    }
  }, [source, projects.length]);

  const setActive = async (assetId: string) => {
    if (assetId === activeAssetId) return;
    setBusy(true);
    setError(null);
    try {
      await api.setActiveBase(characterId, assetId);
      onChanged();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  const uploadRef = async (file: File) => {
    if (!studioProjectId) return;
    setUploadingRef(true);
    setError(null);
    try {
      const fd = new FormData();
      fd.append('asset_type', 'reference');
      fd.append('file', file);
      const res = await fetch(`/api/projects/${studioProjectId}/assets/upload`, { method: 'POST', body: fd });
      if (!res.ok) throw new Error(`Upload failed (${res.status})`);
      const asset = await res.json();
      setRefAssetId(asset.id);
      setRefUrl(assetUrl(studioProjectId, asset.id));
    } catch (e: any) {
      setError(e.message);
    } finally {
      setUploadingRef(false);
    }
  };

  const restyle = async () => {
    setBusy(true);
    setError(null);
    try {
      const data: { style_key?: string; reference_asset_id?: string; project_id?: string; extra?: string } = {
        extra: extra.trim() || undefined,
      };
      if (source === 'reference') {
        if (!refAssetId) throw new Error('Upload a reference image first.');
        data.reference_asset_id = refAssetId;
      } else if (source === 'project') {
        if (!projectId) throw new Error('Pick a project.');
        data.project_id = projectId;
      } else if (source === 'character') {
        data.style_key = characterStyle || DEFAULT_STYLE;
      } else {
        data.style_key = customStyle;
      }
      await api.restyleBase(characterId, data);
      onChanged();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  const activeUrl = assetUrl(studioProjectId, activeAssetId);

  return (
    <div className="fixed inset-0 bg-black/70 z-[9990] flex items-center justify-center p-4" onClick={onClose}>
      <div className="bg-gray-900 border border-gray-800 rounded-lg p-5 w-full max-w-4xl max-h-[90vh] overflow-y-auto flex flex-col gap-4" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between">
          <h2 className="text-lg font-semibold flex items-center gap-2"><Wand2 size={18} className="text-indigo-400" /> Edit Base Image</h2>
          <button onClick={onClose} className="text-gray-500 hover:text-gray-300"><X size={18} /></button>
        </div>

        <div className="flex flex-col md:flex-row gap-5">
          {/* Active + versions */}
          <div className="flex-1 flex flex-col gap-3">
            <div className="aspect-square bg-gray-800 border border-gray-700 rounded-lg overflow-hidden flex items-center justify-center max-w-sm">
              {activeUrl ? (
                <img src={activeUrl} alt="Active base" onClick={() => setLightbox(activeUrl)} title="Click to enlarge" className="w-full h-full object-contain cursor-zoom-in" />
              ) : (
                <span className="text-gray-600 text-sm">No base</span>
              )}
            </div>
            <div className="text-xs text-gray-400">Versions ({versions.length}) — click to make active</div>
            <div className="grid grid-cols-4 sm:grid-cols-5 gap-2">
              {versions.map((v) => {
                const url = assetUrl(studioProjectId, v.asset_id);
                const isActive = v.asset_id === activeAssetId;
                return (
                  <div
                    key={v.asset_id}
                    onClick={() => setActive(v.asset_id)}
                    title={`${v.source || ''}${v.style ? ' · ' + v.style : ''}`}
                    className={`relative aspect-square bg-gray-800 border rounded overflow-hidden cursor-pointer ${isActive ? 'border-indigo-500 ring-1 ring-indigo-500/50' : 'border-gray-700 hover:border-gray-600'}`}
                  >
                    {url && <img src={url} alt={v.source} className="w-full h-full object-cover" />}
                    {isActive && <span className="absolute top-0.5 right-0.5 bg-indigo-600 rounded-full p-0.5"><Check size={9} /></span>}
                    {v.source && <span className="absolute bottom-0 left-0 right-0 bg-black/60 text-[8px] text-gray-300 px-1 truncate">{v.source}</span>}
                  </div>
                );
              })}
            </div>
          </div>

          {/* Restyle */}
          <div className="w-full md:w-72 shrink-0 flex flex-col gap-3 border-t md:border-t-0 md:border-l border-gray-800 md:pl-5 pt-4 md:pt-0">
            <div className="text-sm font-semibold text-gray-300">Restyle base (edit model)</div>
            <div className="text-xs text-gray-500">Redraws the current base in a new style, keeping the character, pose &amp; composition. Adds a new version.</div>
            <label className="flex flex-col gap-1 text-sm">
              <span className="text-gray-400">Style source</span>
              <select value={source} onChange={(e) => setSource(e.target.value as RestyleSource)} className="bg-gray-800 border border-gray-700 rounded-md px-3 py-2 text-sm">
                <option value="character">Character style ({characterStyle || DEFAULT_STYLE})</option>
                <option value="custom">Custom style</option>
                <option value="reference">Reference image</option>
                <option value="project">Match a project's style</option>
              </select>
            </label>

            {source === 'custom' && <StyleSelect value={customStyle} onChange={setCustomStyle} label="Style" />}

            {source === 'project' && (
              <label className="flex flex-col gap-1 text-sm">
                <span className="text-gray-400">Project</span>
                <select value={projectId} onChange={(e) => setProjectId(e.target.value)} className="bg-gray-800 border border-gray-700 rounded-md px-3 py-2 text-sm">
                  <option value="">Select…</option>
                  {projects.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
                </select>
              </label>
            )}

            {source === 'reference' && (
              <div className="flex flex-col gap-2">
                <label className="px-3 py-2 bg-gray-800 hover:bg-gray-700 border border-gray-700 rounded-md text-sm flex items-center gap-2 cursor-pointer w-fit">
                  {uploadingRef ? <Loader2 size={14} className="animate-spin" /> : <Upload size={14} />}
                  Upload reference
                  <input type="file" accept="image/*" className="hidden" onChange={(e) => { const f = e.target.files?.[0]; if (f) uploadRef(f); e.target.value = ''; }} />
                </label>
                {refUrl && <img src={refUrl} alt="style ref" className="w-24 h-24 object-cover rounded border border-gray-700" />}
              </div>
            )}

            <label className="flex flex-col gap-1 text-sm">
              <span className="text-gray-400">Extra instructions (optional)</span>
              <input value={extra} onChange={(e) => setExtra(e.target.value)} placeholder="e.g. warmer palette" className="bg-gray-800 border border-gray-700 rounded-md px-3 py-2 text-sm" />
            </label>

            {error && <div className="text-sm text-red-400">{error}</div>}
            <button onClick={restyle} disabled={busy} className="px-4 py-2 bg-indigo-700 hover:bg-indigo-600 disabled:opacity-50 rounded-md text-sm font-medium flex items-center gap-2 self-start">
              {busy && <Loader2 size={14} className="animate-spin" />}
              <Wand2 size={14} /> Restyle
            </button>
            <div className="text-[10px] text-gray-600">The restyled image renders as a new version and becomes active when done — watch the Versions grid.</div>
          </div>
        </div>
      </div>

      {lightbox && (
        <div className="fixed inset-0 bg-black/80 z-[9995] flex items-center justify-center p-6" onClick={() => setLightbox(null)}>
          <button onClick={() => setLightbox(null)} className="absolute top-4 right-4 text-gray-300 hover:text-white"><X size={24} /></button>
          <img src={lightbox} alt="" onClick={(e) => e.stopPropagation()} className="max-w-full max-h-full object-contain rounded-lg" />
        </div>
      )}
    </div>
  );
}
