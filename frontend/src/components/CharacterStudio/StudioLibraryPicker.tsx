/**
 * StudioLibraryPicker — pick items from the Tools Pose Library or Expression
 * Library inside the Character Studio pose / emotion tabs.
 *  - kind="pose": returns selected pose-library ids (caller sends to presets).
 *  - kind="expression": returns selected {name, natural_prompt} items.
 */
import { useEffect, useMemo, useState } from 'react';
import { createPortal } from 'react-dom';
import { X, Check, Loader2, Search } from 'lucide-react';
import { toolsApi, PoseItemT, ExprItemT } from '../Tools/toolsApi';

interface Props {
  kind: 'pose' | 'expression';
  onClose: () => void;
  onConfirm: (payload: { poseIds?: string[]; expressions?: { name: string; natural_prompt: string }[] }) => Promise<void> | void;
}

export default function StudioLibraryPicker({ kind, onClose, onConfirm }: Props) {
  const isPose = kind === 'pose';
  const [poses, setPoses] = useState<PoseItemT[]>([]);
  const [exprs, setExprs] = useState<ExprItemT[]>([]);
  const [loading, setLoading] = useState(true);
  const [q, setQ] = useState('');
  const [category, setCategory] = useState('');
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let cancel = false;
    setLoading(true);
    (async () => {
      try {
        if (isPose) {
          const r = await toolsApi.poseList({ q: q || undefined, category: category || undefined, limit: 300 });
          if (!cancel) setPoses(r.items || []);
        } else {
          const r = await toolsApi.exprList({ q: q || undefined, category: category || undefined });
          if (!cancel) setExprs(r.items || []);
        }
      } catch (e: any) {
        if (!cancel) setErr(e?.message || 'Load failed');
      } finally {
        if (!cancel) setLoading(false);
      }
    })();
    return () => { cancel = true; };
  }, [isPose, q, category]);

  const categories = useMemo(() => {
    const src = isPose ? poses.map((p) => p.category) : exprs.map((e) => e.category);
    return Array.from(new Set(src.filter(Boolean)));
  }, [isPose, poses, exprs]);

  const toggle = (id: string) => setSelected((s) => {
    const n = new Set(s); n.has(id) ? n.delete(id) : n.add(id); return n;
  });

  const confirm = async () => {
    if (!selected.size) return;
    setBusy(true); setErr(null);
    try {
      if (isPose) {
        await onConfirm({ poseIds: [...selected] });
      } else {
        const items = exprs.filter((e) => selected.has(e.id))
          .map((e) => ({ name: e.name, natural_prompt: e.natural_prompt || e.name }));
        await onConfirm({ expressions: items });
      }
      onClose();
    } catch (e: any) {
      setErr(e?.message || 'Failed');
    } finally {
      setBusy(false);
    }
  };

  return createPortal(
    <div className="fixed inset-0 z-[9994] bg-black/80 flex items-center justify-center p-4" onClick={onClose}>
      <div className="bg-gray-900 rounded-xl border border-gray-700 w-full max-w-3xl max-h-[88vh] flex flex-col overflow-hidden" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center gap-2 px-4 py-3 border-b border-gray-700">
          <h3 className="font-semibold text-gray-100">{isPose ? 'Pose Library' : 'Expression Library'}</h3>
          <button onClick={onClose} className="ml-auto p-1.5 rounded hover:bg-gray-800 text-gray-400"><X className="w-5 h-5" /></button>
        </div>

        <div className="flex items-center gap-2 px-4 py-2 border-b border-gray-800">
          <div className="relative flex-1">
            <Search className="w-4 h-4 text-gray-500 absolute left-2 top-2.5" />
            <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search…"
              className="w-full pl-8 pr-2 py-2 rounded-md bg-gray-800 border border-gray-700 text-sm text-gray-100" />
          </div>
          {categories.length > 0 && (
            <select value={category} onChange={(e) => setCategory(e.target.value)}
              className="rounded-md bg-gray-800 border border-gray-700 px-2 py-2 text-sm text-gray-100">
              <option value="">All categories</option>
              {categories.map((c) => <option key={c} value={c}>{c}</option>)}
            </select>
          )}
        </div>

        <div className="flex-1 overflow-y-auto p-3">
          {loading ? (
            <div className="flex items-center justify-center py-16 text-gray-400"><Loader2 className="w-5 h-5 animate-spin mr-2" /> Loading…</div>
          ) : isPose ? (
            <div className="grid grid-cols-3 sm:grid-cols-4 md:grid-cols-6 gap-2">
              {poses.map((p) => {
                const sel = selected.has(p.id);
                return (
                  <button key={p.id} onClick={() => toggle(p.id)}
                    className={`relative aspect-[2/3] rounded-lg overflow-hidden border-2 ${sel ? 'border-indigo-400' : 'border-gray-700'} bg-gray-950`}>
                    {p.has_thumb && <img src={toolsApi.poseThumbUrl(p.id)} className="w-full h-full object-contain" alt={p.name} />}
                    <span className="absolute bottom-0 inset-x-0 bg-black/70 text-[9px] text-gray-200 px-1 py-0.5 truncate">{p.name || p.category}</span>
                    {sel && <span className="absolute top-1 right-1 w-5 h-5 rounded-full bg-indigo-500 flex items-center justify-center"><Check className="w-3.5 h-3.5 text-white" /></span>}
                  </button>
                );
              })}
              {!poses.length && <div className="col-span-full text-center text-gray-500 py-10 text-sm">No poses in the library. Add some in Tools → Pose Library.</div>}
            </div>
          ) : (
            <div className="flex flex-col gap-1">
              {exprs.map((e) => {
                const sel = selected.has(e.id);
                return (
                  <button key={e.id} onClick={() => toggle(e.id)}
                    className={`flex items-center gap-3 rounded-md px-3 py-2 border text-left ${sel ? 'border-indigo-500 bg-indigo-950/20' : 'border-gray-800 bg-gray-900'}`}>
                    <span className={`w-4 h-4 rounded border shrink-0 flex items-center justify-center ${sel ? 'bg-indigo-600 border-indigo-500' : 'border-gray-600'}`}>{sel && <Check className="w-3 h-3 text-white" />}</span>
                    {e.has_thumb && <img src={toolsApi.exprThumbUrl(e.id)} className="w-9 h-9 rounded object-cover border border-gray-700" alt="" />}
                    <span className="w-40 shrink-0"><span className="block text-sm text-gray-200 truncate">{e.name}</span><span className="block text-[10px] text-gray-500">{e.category}</span></span>
                    <span className="flex-1 text-xs text-gray-400 truncate">{e.natural_prompt}</span>
                  </button>
                );
              })}
              {!exprs.length && <div className="text-center text-gray-500 py-10 text-sm">No expressions in the library. Add some in Tools → Expression Library.</div>}
            </div>
          )}
        </div>

        <div className="border-t border-gray-700 p-3">
          {err && <div className="text-xs text-red-400 mb-2">{err}</div>}
          <button onClick={confirm} disabled={busy || !selected.size}
            className="w-full py-2.5 rounded-lg bg-indigo-600 hover:bg-indigo-500 font-semibold disabled:opacity-50 flex items-center justify-center gap-2">
            {busy ? <Loader2 className="w-4 h-4 animate-spin" /> : <Check className="w-4 h-4" />}
            {isPose ? `Add ${selected.size || ''} to poses` : `Generate ${selected.size || ''} expression${selected.size === 1 ? '' : 's'}`}
          </button>
        </div>
      </div>
    </div>,
    document.body,
  );
}
