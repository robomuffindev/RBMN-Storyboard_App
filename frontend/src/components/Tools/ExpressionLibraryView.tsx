/**
 * Expression Library — reusable facial expressions stored as name + natural
 * prompt (+ category/tags). Seed from the bundled 157-emotion catalog, add
 * your own, edit prompts inline, filter/search, and delete.
 */
import { useEffect, useState, useCallback } from 'react';
import { Search, Trash2, Plus, Download, Loader2, Check, X, Sparkles } from 'lucide-react';
import { toolsApi, ExprItemT, FacetsT } from './toolsApi';
import SampleGenerateModal from './SampleGenerateModal';

export function ExpressionLibraryView() {
  const [items, setItems] = useState<ExprItemT[]>([]);
  const [facets, setFacets] = useState<FacetsT | null>(null);
  const [category, setCategory] = useState('');
  const [q, setQ] = useState('');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState(false);
  const [adding, setAdding] = useState(false);
  const [newName, setNewName] = useState('');
  const [newCat, setNewCat] = useState('Custom');
  const [newPrompt, setNewPrompt] = useState('');
  const [editId, setEditId] = useState<string | null>(null);
  const [editPrompt, setEditPrompt] = useState('');
  const [genOpen, setGenOpen] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [res, f] = await Promise.all([toolsApi.exprList({ category, q }), toolsApi.exprFacets()]);
      setItems(res.items);
      setFacets(f);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, [category, q]);

  useEffect(() => {
    const t = setTimeout(load, 200);
    return () => clearTimeout(t);
  }, [load]);

  const toggle = (id: string) =>
    setSelected((prev) => {
      const n = new Set(prev);
      n.has(id) ? n.delete(id) : n.add(id);
      return n;
    });

  const importCatalog = async () => {
    setBusy(true);
    try {
      const r = await toolsApi.exprImportCatalog();
      alert(`Imported ${r.imported} expressions from the bundled catalog.`);
      await load();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  const addOne = async () => {
    if (!newName.trim()) return;
    setBusy(true);
    try {
      await toolsApi.exprAdd({ name: newName.trim(), category: newCat.trim() || 'Custom', natural_prompt: newPrompt.trim() });
      setNewName('');
      setNewPrompt('');
      setAdding(false);
      await load();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  const saveEdit = async (id: string) => {
    try {
      await toolsApi.exprPatch(id, { natural_prompt: editPrompt });
      setEditId(null);
      await load();
    } catch (e: any) {
      setError(e.message);
    }
  };

  const del = async () => {
    if (!selected.size || !window.confirm(`Delete ${selected.size} expression(s)?`)) return;
    setBusy(true);
    try {
      await toolsApi.exprDelete(Array.from(selected));
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
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search name/prompt" className="bg-gray-800 border border-gray-700 rounded-md pl-7 pr-3 py-1.5 text-sm" />
        </div>
        <select value={category} onChange={(e) => setCategory(e.target.value)} className="bg-gray-800 border border-gray-700 rounded-md px-3 py-1.5 text-sm">
          <option value="">All categories ({facets?.total ?? 0})</option>
          {facets?.categories.map((c) => (
            <option key={c.name} value={c.name}>{c.name} ({c.count})</option>
          ))}
        </select>
        <div className="ml-auto flex items-center gap-2">
          <button onClick={() => setGenOpen(true)} className="px-3 py-1.5 bg-indigo-600 hover:bg-indigo-500 rounded-md text-sm font-medium flex items-center gap-2"><Sparkles size={14} /> Generate Sample</button>
          <button onClick={() => setAdding((v) => !v)} className="px-3 py-1.5 bg-teal-700 hover:bg-teal-600 rounded-md text-sm font-medium flex items-center gap-2"><Plus size={14} /> Add</button>
          <button onClick={importCatalog} disabled={busy} className="px-3 py-1.5 bg-gray-800 hover:bg-gray-700 border border-gray-700 rounded-md text-sm flex items-center gap-2"><Download size={14} /> Import 157 catalog</button>
          <button onClick={del} disabled={busy || !selected.size} className="px-3 py-1.5 bg-gray-800 hover:bg-gray-700 border border-gray-700 disabled:opacity-50 rounded-md text-sm text-red-300 flex items-center gap-2"><Trash2 size={14} /> Delete</button>
        </div>
      </div>

      {adding && (
        <div className="bg-gray-900 border border-gray-800 rounded-lg p-3 flex flex-wrap items-end gap-2">
          <label className="flex flex-col gap-1 text-sm"><span className="text-gray-400">Name</span>
            <input value={newName} onChange={(e) => setNewName(e.target.value)} className="bg-gray-800 border border-gray-700 rounded-md px-3 py-1.5 text-sm" /></label>
          <label className="flex flex-col gap-1 text-sm"><span className="text-gray-400">Category</span>
            <input value={newCat} onChange={(e) => setNewCat(e.target.value)} className="bg-gray-800 border border-gray-700 rounded-md px-3 py-1.5 text-sm" /></label>
          <label className="flex flex-col gap-1 text-sm flex-1 min-w-[260px]"><span className="text-gray-400">Natural prompt</span>
            <input value={newPrompt} onChange={(e) => setNewPrompt(e.target.value)} placeholder="a wide joyful smile, eyes crinkled" className="bg-gray-800 border border-gray-700 rounded-md px-3 py-1.5 text-sm" /></label>
          <button onClick={addOne} disabled={busy || !newName.trim()} className="px-3 py-1.5 bg-teal-700 hover:bg-teal-600 disabled:opacity-50 rounded-md text-sm font-medium">Save</button>
        </div>
      )}

      {error && <div className="text-sm text-red-400">{error}</div>}
      {loading ? (
        <div className="flex items-center gap-2 text-sm text-gray-400"><Loader2 size={14} className="animate-spin" /> Loading…</div>
      ) : items.length === 0 ? (
        <div className="text-sm text-gray-500 bg-gray-900 border border-gray-800 rounded-lg p-8 text-center">
          No expressions yet. Click <b>Import 157 catalog</b> to seed from the bundled emotion set, or <b>Add</b> your own.
        </div>
      ) : (
        <div className="flex flex-col gap-1">
          {items.map((e) => (
            <div key={e.id} className={`flex items-center gap-3 rounded-md px-3 py-2 border ${selected.has(e.id) ? 'border-teal-500 bg-teal-950/20' : 'border-gray-800 bg-gray-900'}`}>
              <button onClick={() => toggle(e.id)} className={`w-4 h-4 rounded border shrink-0 flex items-center justify-center ${selected.has(e.id) ? 'bg-teal-600 border-teal-500' : 'border-gray-600'}`}>
                {selected.has(e.id) && <Check size={11} />}
              </button>
              {e.has_thumb && (
                <img src={toolsApi.exprThumbUrl(e.id)} alt="" className="w-10 h-10 rounded object-cover shrink-0 border border-gray-700" />
              )}
              <div className="w-40 shrink-0">
                <div className="text-sm text-gray-200 truncate">{e.name}</div>
                <div className="text-[10px] text-gray-500">{e.category}</div>
              </div>
              {editId === e.id ? (
                <div className="flex-1 flex items-center gap-2">
                  <input value={editPrompt} onChange={(ev) => setEditPrompt(ev.target.value)} className="flex-1 bg-gray-800 border border-gray-700 rounded-md px-2 py-1 text-sm" />
                  <button onClick={() => saveEdit(e.id)} className="text-emerald-400"><Check size={15} /></button>
                  <button onClick={() => setEditId(null)} className="text-gray-500"><X size={15} /></button>
                </div>
              ) : (
                <div
                  className="flex-1 text-sm text-gray-400 truncate cursor-text"
                  title="Click to edit prompt"
                  onClick={() => { setEditId(e.id); setEditPrompt(e.natural_prompt); }}
                >
                  {e.natural_prompt || <span className="text-gray-600 italic">no prompt — click to add</span>}
                </div>
              )}
            </div>
          ))}
        </div>
      )}
      {genOpen && <SampleGenerateModal kind="expression" onClose={() => setGenOpen(false)} onCommitted={() => load()} />}
    </div>
  );
}
