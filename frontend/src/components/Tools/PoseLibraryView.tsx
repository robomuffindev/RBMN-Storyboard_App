/**
 * Pose Library — browse committed poses by category/tag/search, view in a
 * lightbox, push selected poses to the Character Studio pose picker, delete,
 * and export/import portable pose packs.
 */
import { useEffect, useState, useCallback, useRef } from 'react';
import { Search, Trash2, Send, Download, Upload, Loader2, Check, Sparkles } from 'lucide-react';
import { ImageLightbox } from '../CharacterStudio/p2Shared';
import { toolsApi, PoseItemT, FacetsT } from './toolsApi';
import SampleGenerateModal from './SampleGenerateModal';

export function PoseLibraryView() {
  const [items, setItems] = useState<PoseItemT[]>([]);
  const [total, setTotal] = useState(0);
  const [facets, setFacets] = useState<FacetsT | null>(null);
  const [category, setCategory] = useState('');
  const [tag, setTag] = useState('');
  const [q, setQ] = useState('');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [lightbox, setLightbox] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [caps, setCaps] = useState<{ dwpose: boolean; klein: boolean } | null>(null);
  const [genOpen, setGenOpen] = useState(false);
  const importRef = useRef<HTMLInputElement | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [res, f] = await Promise.all([
        toolsApi.poseList({ category, tag, q, limit: 300 }),
        toolsApi.poseFacets(),
      ]);
      setItems(res.items);
      setTotal(res.total);
      setFacets(f);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, [category, tag, q]);

  useEffect(() => {
    const t = setTimeout(load, 200);
    return () => clearTimeout(t);
  }, [load]);

  useEffect(() => {
    toolsApi.capabilities().then(setCaps).catch(() => setCaps({ dwpose: false, klein: false }));
  }, []);

  const hdThumbs = async () => {
    if (!selected.size) return;
    if (!window.confirm(`Render HD mannequin thumbnails for ${selected.size} pose(s)? This runs on a Klein GPU worker and can take a while.`)) return;
    setBusy(true);
    try {
      const res = await toolsApi.poseHdThumbnails(Array.from(selected));
      alert(`Rendered ${res.rendered} HD thumbnail(s).${res.errors.length ? ` ${res.errors.length} failed.` : ''}`);
      await load();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  const toggle = (id: string) =>
    setSelected((prev) => {
      const n = new Set(prev);
      n.has(id) ? n.delete(id) : n.add(id);
      return n;
    });

  const toPresets = async () => {
    if (!selected.size) return;
    setBusy(true);
    try {
      const res = await toolsApi.poseToPresets(Array.from(selected));
      alert(`Added ${res.added} pose(s) to the Character Studio pose picker.`);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  const del = async () => {
    if (!selected.size || !window.confirm(`Delete ${selected.size} pose(s) from the library?`)) return;
    setBusy(true);
    try {
      await toolsApi.poseDelete(Array.from(selected));
      setSelected(new Set());
      await load();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-2">
        <div className="relative">
          <Search size={14} className="absolute left-2 top-2.5 text-gray-500" />
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search name/tags" className="bg-gray-800 border border-gray-700 rounded-md pl-7 pr-3 py-1.5 text-sm" />
        </div>
        <select value={category} onChange={(e) => setCategory(e.target.value)} className="bg-gray-800 border border-gray-700 rounded-md px-3 py-1.5 text-sm">
          <option value="">All categories ({facets?.total ?? 0})</option>
          {facets?.categories.map((c) => (
            <option key={c.name} value={c.name}>{c.name} ({c.count})</option>
          ))}
        </select>
        <select value={tag} onChange={(e) => setTag(e.target.value)} className="bg-gray-800 border border-gray-700 rounded-md px-3 py-1.5 text-sm">
          <option value="">All tags</option>
          {facets?.tags?.slice(0, 60).map((t) => (
            <option key={t.name} value={t.name}>{t.name} ({t.count})</option>
          ))}
        </select>
        <div className="ml-auto flex items-center gap-2">
          <button onClick={() => setGenOpen(true)} className="px-3 py-1.5 bg-indigo-600 hover:bg-indigo-500 rounded-md text-sm font-medium flex items-center gap-2"><Sparkles size={14} /> Generate Sample</button>
          <button onClick={toPresets} disabled={busy || !selected.size} className="px-3 py-1.5 bg-teal-700 hover:bg-teal-600 disabled:opacity-50 rounded-md text-sm font-medium flex items-center gap-2">
            <Send size={14} /> Send {selected.size || ''} to Pose picker
          </button>
          <button
            onClick={hdThumbs}
            disabled={busy || !selected.size || (caps !== null && !caps.klein)}
            title={caps && !caps.klein ? 'No Klein worker online' : 'Render clean HD grey-mannequin thumbnails on a GPU worker'}
            className="px-3 py-1.5 bg-gray-800 hover:bg-gray-700 border border-gray-700 disabled:opacity-50 rounded-md text-sm flex items-center gap-2"
          >
            <Sparkles size={14} /> HD thumbnails
          </button>
          <button onClick={del} disabled={busy || !selected.size} className="px-3 py-1.5 bg-gray-800 hover:bg-gray-700 border border-gray-700 disabled:opacity-50 rounded-md text-sm text-red-300 flex items-center gap-2">
            <Trash2 size={14} /> Delete
          </button>
          <a href={toolsApi.poseExportUrl()} className="px-3 py-1.5 bg-gray-800 hover:bg-gray-700 border border-gray-700 rounded-md text-sm flex items-center gap-2">
            <Download size={14} /> Export
          </a>
          <input ref={importRef} type="file" accept=".zip" className="hidden" onChange={async (e) => {
            const f = e.target.files?.[0];
            e.target.value = '';
            if (!f) return;
            setBusy(true);
            try { const r = await toolsApi.poseImport(f); alert(`Imported ${r.imported} poses.`); await load(); }
            catch (err: any) { setError(err.message); } finally { setBusy(false); }
          }} />
          <button onClick={() => importRef.current?.click()} disabled={busy} className="px-3 py-1.5 bg-gray-800 hover:bg-gray-700 border border-gray-700 rounded-md text-sm flex items-center gap-2">
            <Upload size={14} /> Import pack
          </button>
        </div>
      </div>

      {error && <div className="text-sm text-red-400">{error}</div>}
      {loading ? (
        <div className="flex items-center gap-2 text-sm text-gray-400"><Loader2 size={14} className="animate-spin" /> Loading…</div>
      ) : items.length === 0 ? (
        <div className="text-sm text-gray-500 bg-gray-900 border border-gray-800 rounded-lg p-8 text-center">
          No poses yet. Use the <b>Pose Organizer</b> tab to scan a folder or zip, or import a pose pack.
        </div>
      ) : (
        <>
          <div className="text-xs text-gray-500">{total} pose{total === 1 ? '' : 's'}{selected.size ? ` · ${selected.size} selected` : ''}</div>
          <div className="grid grid-cols-3 sm:grid-cols-4 md:grid-cols-6 lg:grid-cols-8 gap-2">
            {items.map((p) => (
              <div
                key={p.id}
                onClick={() => toggle(p.id)}
                title={`${p.name}\n${(p.tags || []).join(', ')}`}
                className={`relative bg-gray-800 border rounded-md overflow-hidden cursor-pointer ${
                  selected.has(p.id) ? 'border-teal-500 ring-1 ring-teal-500/50' : 'border-gray-700 hover:border-gray-600'
                }`}
              >
                <div className="aspect-[2/3] flex items-center justify-center bg-gray-900">
                  <img src={toolsApi.poseThumbUrl(p.id)} alt={p.name} className="w-full h-full object-contain" />
                </div>
                <button
                  onClick={(e) => { e.stopPropagation(); setLightbox(toolsApi.poseThumbUrl(p.id)); }}
                  className="absolute top-1 right-1 bg-black/60 hover:bg-black/80 rounded px-1 text-[10px] text-gray-200"
                >
                  view
                </button>
                <div className="px-1 py-0.5 text-[10px] text-gray-400 truncate">{p.name}</div>
                {selected.has(p.id) && <span className="absolute bottom-5 right-1 bg-teal-600 rounded-full p-0.5"><Check size={10} /></span>}
              </div>
            ))}
          </div>
        </>
      )}

      {lightbox && <ImageLightbox url={lightbox} onClose={() => setLightbox(null)} />}
      {genOpen && <SampleGenerateModal kind="pose" dwposeAvailable={!!caps?.dwpose} onClose={() => setGenOpen(false)} onCommitted={() => load()} />}
    </div>
  );
}
