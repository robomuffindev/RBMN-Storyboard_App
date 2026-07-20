/**
 * Image Workshop — a free-form model playground with one shared, persistent
 * gallery. Two generation modes (Freestyle prompt / Character-gen fields with an
 * LLM wizard auto-fill), reference images (upload OR pick from the gallery), the
 * full model suite, a review grid, and save / download / use-as-reference.
 *
 * Self-contained + responsive: renders to fill whatever container hosts it (the
 * Character Studio lightbox OR the Tools standalone page). Built mobile-first —
 * single-column stacks, large tap targets, full-screen image viewer on phones.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import {
  Sparkles, Loader2, Check, X, Upload, Image as ImageIcon, Wand2, Trash2,
  Download, Repeat, Lock, Unlock, Shuffle, ChevronDown, Plus, RefreshCw, Tag,
} from 'lucide-react';
import {
  workshopApi, type WsModelT, type WsRefT, type WsGenStatusT, type WsGalleryItemT,
} from './imageWorkshopApi';

// Fill React's lack of a real useCallback import guard (esbuild-safe alias).
const useCb = useCallback;

// ── Character fields ─────────────────────────────────────────────────────────
const CHAR_FIELDS: { key: string; label: string; placeholder: string; wide?: boolean }[] = [
  { key: 'name', label: 'Name', placeholder: 'optional' },
  { key: 'sex', label: 'Sex', placeholder: 'female / male / …' },
  { key: 'age', label: 'Age', placeholder: '27' },
  { key: 'race', label: 'Race / ethnicity', placeholder: 'e.g. Nordic' },
  { key: 'skin_color', label: 'Skin', placeholder: 'fair, warm undertone' },
  { key: 'hair', label: 'Hair', placeholder: 'long wavy auburn' },
  { key: 'eyes', label: 'Eyes', placeholder: 'green' },
  { key: 'face', label: 'Face', placeholder: 'soft round face, freckles' },
  { key: 'body', label: 'Body', placeholder: 'curvy, full figure' },
  { key: 'height', label: 'Height', placeholder: 'average' },
  { key: 'aesthetics', label: 'Style / aesthetic', placeholder: 'photoreal, cinematic', wide: true },
  { key: 'additional_details', label: 'Additional details', placeholder: 'anything else', wide: true },
];

// ── Styles ───────────────────────────────────────────────────────────────────
const C = {
  panel: '#e6e9ee', box: '#161a20', boxBorder: '#2a2f3a', field: '#0f1318',
  accent: '#7c9dff', accentBg: '#22314f', ok: '#4ade80', danger: '#ff6b6b',
};
const inputStyle: React.CSSProperties = {
  width: '100%', background: C.field, border: `1px solid ${C.boxBorder}`, color: C.panel,
  borderRadius: 8, padding: '9px 10px', fontSize: 13.5, outline: 'none', boxSizing: 'border-box',
};
const labelStyle: React.CSSProperties = { fontSize: 11.5, fontWeight: 600, color: '#9aa4b2', marginBottom: 4, display: 'block' };
const btnPrimary: React.CSSProperties = {
  background: '#3b6ef0', color: '#fff', border: 'none', borderRadius: 10, padding: '12px 16px',
  fontSize: 14.5, fontWeight: 700, cursor: 'pointer', display: 'flex', alignItems: 'center',
  justifyContent: 'center', gap: 8, width: '100%',
};
const btnGhost: React.CSSProperties = {
  background: '#1c2230', color: '#cdd5e0', border: `1px solid ${C.boxBorder}`, borderRadius: 8,
  padding: '8px 12px', fontSize: 12.5, fontWeight: 600, cursor: 'pointer', display: 'inline-flex',
  alignItems: 'center', gap: 6,
};
const chip = (active: boolean): React.CSSProperties => ({
  padding: '8px 14px', borderRadius: 999, fontSize: 13, fontWeight: 600, cursor: 'pointer',
  border: `1px solid ${active ? C.accent : C.boxBorder}`, background: active ? C.accentBg : 'transparent',
  color: active ? '#dbe4ff' : '#9aa4b2', whiteSpace: 'nowrap',
});

// Quick category tags — click to toggle; custom tags can be typed too.
const PRESET_TAGS = ['Character', 'Pose', 'Item', 'SceneBG', 'Outfit', 'Face', 'Style', 'Prop'];

const ASPECTS: { label: string; w: number; h: number }[] = [
  { label: 'Portrait 2:3', w: 832, h: 1216 },
  { label: 'Square 1:1', w: 1024, h: 1024 },
  { label: 'Landscape 3:2', w: 1216, h: 832 },
  { label: 'Tall 9:16', w: 768, h: 1344 },
  { label: 'Wide 16:9', w: 1344, h: 768 },
];

type Tab = 'generate' | 'gallery';

interface Props { seedReferences?: WsRefT[]; }

export default function ImageWorkshopPanel({ seedReferences }: Props) {
  const [tab, setTab] = useState<Tab>('generate');

  // Generation form
  const [mode, setMode] = useState<'freestyle' | 'character'>('freestyle');
  const [model, setModel] = useState('z_image');
  const [models, setModels] = useState<WsModelT[]>([]);
  const [prompt, setPrompt] = useState('');
  const [charName, setCharName] = useState('');
  const [fields, setFields] = useState<Record<string, string>>({});
  const [negative, setNegative] = useState('');
  const [count, setCount] = useState(4);
  const [dims, setDims] = useState<{ w: number; h: number }>({ w: 832, h: 1216 });
  const [seedLocked, setSeedLocked] = useState(false);
  const [seed, setSeed] = useState('');
  const [refs, setRefs] = useState<WsRefT[]>(seedReferences || []);

  // Wizard
  const [wizText, setWizText] = useState('');
  const [wizBusy, setWizBusy] = useState(false);
  const [wizMsg, setWizMsg] = useState('');

  // Generation run
  const [phase, setPhase] = useState<'form' | 'running' | 'review'>('form');
  const [genId, setGenId] = useState<string | null>(null);
  const [status, setStatus] = useState<WsGenStatusT | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [err, setErr] = useState<string | null>(null);
  const [savedNote, setSavedNote] = useState('');
  const [saveTags, setSaveTags] = useState<string[]>([]);   // tags applied to the next save
  const [customTag, setCustomTag] = useState('');           // free-text tag entry

  // Gallery
  const [gallery, setGallery] = useState<WsGalleryItemT[]>([]);
  const [galTotal, setGalTotal] = useState(0);
  const [galQ, setGalQ] = useState('');
  const [galBusy, setGalBusy] = useState(false);
  const [pickForRef, setPickForRef] = useState(false);   // gallery overlay in "pick a reference" mode
  const [viewer, setViewer] = useState<string | null>(null);
  const [allTags, setAllTags] = useState<string[]>([]);  // distinct tags across the gallery
  const [tagFilter, setTagFilter] = useState('');        // active gallery tag filter
  const [editTags, setEditTags] = useState<{ id: string; tags: string[] } | null>(null); // per-item tag editor

  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const fileInput = useRef<HTMLInputElement | null>(null);

  const curModel = models.find((m) => m.value === model);
  const maxRefs = curModel?.refs ?? 0;

  // Load model list once
  useEffect(() => {
    workshopApi.models().then((r) => {
      setModels(r.models);
      // Default to first online t2i model if the current default is offline.
      const online = r.models.filter((m) => m.online);
      if (online.length && !online.some((m) => m.value === 'z_image')) setModel(online[0].value);
    }).catch(() => { /* worker maybe offline */ });
  }, []);

  const loadGallery = useCb(async (q = '', tag = '') => {
    setGalBusy(true);
    try {
      const r = await workshopApi.gallery({ q, tag, limit: 300 });
      setGallery(r.items); setGalTotal(r.total); setAllTags(r.all_tags || []);
    } catch { /* ignore */ } finally { setGalBusy(false); }
  }, []);

  useEffect(() => { loadGallery(); }, [loadGallery]);
  useEffect(() => () => { if (pollRef.current) clearInterval(pollRef.current); }, []);

  // ── Wizard ──────────────────────────────────────────────────────────────
  const runWizard = async () => {
    if (!wizText.trim()) { setWizMsg('Describe the character first.'); return; }
    setWizBusy(true); setWizMsg('');
    try {
      const r = await workshopApi.wizardCharacter(wizText.trim());
      const f = r.fields || {};
      const next: Record<string, string> = { ...fields };
      CHAR_FIELDS.forEach(({ key }) => {
        if (key === 'name') return;
        const v = (f as Record<string, unknown>)[key];
        if (v !== undefined && v !== null && String(v).trim()) next[key] = String(v);
      });
      setFields(next);
      if ((f as Record<string, unknown>).name && !charName) setCharName(String((f as Record<string, unknown>).name));
      setWizMsg(`✓ Fields filled by the ${r.source === 'host' ? 'VNCCS host' : 'app'} wizard — review & tweak.`);
    } catch (e: unknown) {
      setWizMsg(`Wizard failed: ${(e as Error).message}`);
    } finally { setWizBusy(false); }
  };

  // ── References ──────────────────────────────────────────────────────────
  const addUploadRef = async (files: FileList | null) => {
    if (!files || !files.length) return;
    for (const file of Array.from(files).slice(0, maxRefs || 5)) {
      try {
        const r = await workshopApi.uploadRef(file);
        setRefs((prev) => (prev.length >= (maxRefs || 5) ? prev : [...prev, r]));
      } catch (e: unknown) { setErr((e as Error).message); }
    }
  };
  const addGalleryRef = (it: WsGalleryItemT, cap?: number) => {
    const lim = cap ?? (maxRefs || 5);
    setRefs((prev) => {
      if (prev.some((r) => r.source === 'gallery' && r.id === it.id)) return prev;
      if (prev.length >= lim) return prev;
      return [...prev, { source: 'gallery', id: it.id, url: it.url }];
    });
    setPickForRef(false);
    setTab('generate');
  };

  // Browse-mode "use as reference": guarantee it lands even if the current model
  // is text-only (switch to a ref-capable model first; state batching means we
  // pass an explicit cap so the add isn't gated on the stale maxRefs).
  const useAsReference = (it: WsGalleryItemT) => {
    if (maxRefs === 0) {
      const refCapable = models.find((m) => m.refs > 0 && m.online) || models.find((m) => m.refs > 0);
      if (refCapable) setModel(refCapable.value);
      addGalleryRef(it, 5);
    } else {
      addGalleryRef(it);
    }
  };
  const removeRef = (i: number) => setRefs((prev) => prev.filter((_, idx) => idx !== i));

  // Vision-describe a reference image → fill the Character-gen fields (and flip
  // to Character mode so the results are visible). The vision LLM lives on the
  // VNCCS host, so the reference bytes are re-uploaded there for the scan.
  const [describeBusy, setDescribeBusy] = useState<string | null>(null);
  const describeRef = async (r: WsRefT) => {
    if (describeBusy) return;
    setDescribeBusy(r.id); setErr(null);
    try {
      const blob = await (await fetch(r.url)).blob();
      const file = new File([blob], `${r.id}.png`, { type: blob.type || 'image/png' });
      const up = await workshopApi.uploadToVnccs(file);
      const res = await workshopApi.cloneAnalyze(up);
      const f = res.fields || {};
      const next: Record<string, string> = { ...fields };
      CHAR_FIELDS.forEach(({ key }) => {
        if (key === 'name') return;
        const v = (f as Record<string, unknown>)[key];
        if (v !== undefined && v !== null && String(v).trim()) next[key] = String(v);
      });
      setFields(next);
      if ((f as Record<string, unknown>).name && !charName) setCharName(String((f as Record<string, unknown>).name));
      setMode('character');
      setWizMsg('✓ Character fields filled from the reference image — review & tweak.');
    } catch (e: unknown) {
      setErr(`Describe failed: ${(e as Error).message}`);
    } finally { setDescribeBusy(null); }
  };

  // ── Generate ────────────────────────────────────────────────────────────
  const startGen = async () => {
    setErr(null); setSavedNote('');
    const haveChar = mode === 'character' && (charName.trim() || Object.values(fields).some((v) => v.trim()));
    if (mode === 'freestyle' && !prompt.trim()) { setErr('Enter a prompt.'); return; }
    if (mode === 'character' && !haveChar) { setErr('Fill at least one character field (or use the wizard).'); return; }
    if (model === 'qie' && refs.length === 0) { setErr('Qwen-Image-Edit needs at least one reference image.'); return; }
    try {
      const res = await workshopApi.generate({
        mode, model, prompt: prompt.trim(), name: charName.trim(), fields,
        negative: negative.trim(), count, width: dims.w, height: dims.h,
        seed: seedLocked && seed ? parseInt(seed) : undefined,
        references: refs.map((r) => ({ source: r.source, id: r.id })),
      });
      setGenId(res.gen_id); setPhase('running'); setSelected(new Set()); setStatus(null);
      if (!seedLocked) setSeed(String(res.seed));
      if (pollRef.current) clearInterval(pollRef.current);
      pollRef.current = setInterval(async () => {
        try {
          const st = await workshopApi.genStatus(res.gen_id);
          setStatus(st);
          if (st.status !== 'running') {
            if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; }
            setPhase('review');
          }
        } catch (e: unknown) {
          setErr((e as Error).message);
          if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; }
        }
      }, 2000);
    } catch (e: unknown) { setErr((e as Error).message); }
  };

  const toggleSel = (id: string) => setSelected((s) => {
    const n = new Set(s); if (n.has(id)) n.delete(id); else n.add(id); return n;
  });

  const saveSelected = async () => {
    if (!genId || selected.size === 0) return;
    try {
      const r = await workshopApi.save(genId, [...selected], saveTags);
      setSavedNote(`✓ Saved ${r.count} to the gallery${saveTags.length ? ` · ${saveTags.join(', ')}` : ''}.`);
      setSelected(new Set());
      loadGallery(galQ, tagFilter);
    } catch (e: unknown) { setErr((e as Error).message); }
  };

  // Toggle a tag in a string[] (immutable helper for both save + edit).
  const toggleTag = (list: string[], t: string): string[] =>
    list.some((x) => x.toLowerCase() === t.toLowerCase())
      ? list.filter((x) => x.toLowerCase() !== t.toLowerCase())
      : [...list, t];

  const commitEditTags = async () => {
    if (!editTags) return;
    try {
      await workshopApi.setTags([editTags.id], editTags.tags);
      setGallery((g) => g.map((it) => it.id === editTags.id ? { ...it, tags: editTags.tags } : it));
      setEditTags(null);
      // Refresh the distinct-tag chip row (a new tag may have appeared/vanished).
      loadGallery(galQ, tagFilter);
    } catch (e: unknown) { setErr((e as Error).message); }
  };

  const dl = (url: string, name: string) => {
    const a = document.createElement('a'); a.href = url; a.download = name; a.click();
  };

  const deleteGalleryItem = async (id: string) => {
    try { await workshopApi.del([id]); setGallery((g) => g.filter((it) => it.id !== id)); setGalTotal((t) => Math.max(0, t - 1)); }
    catch (e: unknown) { setErr((e as Error).message); }
  };

  const genImages = status?.images || [];

  // ── Render helpers ────────────────────────────────────────────────────────
  // Preset + custom tag chips. `value` is the current tag list; `onChange`
  // receives the new list. Used by the Save bar and the per-item tag editor.
  const renderTagChips = (value: string[], onChange: (next: string[]) => void) => {
    const known = Array.from(new Set([...PRESET_TAGS, ...value, ...allTags]));
    const addCustom = () => {
      const t = customTag.trim();
      if (t && !value.some((x) => x.toLowerCase() === t.toLowerCase())) onChange([...value, t]);
      setCustomTag('');
    };
    return (
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, alignItems: 'center' }}>
        {known.map((t) => {
          const on = value.some((x) => x.toLowerCase() === t.toLowerCase());
          return (
            <button key={t} onClick={() => onChange(toggleTag(value, t))}
              style={{ ...chip(on), padding: '5px 11px', fontSize: 12 }}>
              {on && <Check size={11} style={{ verticalAlign: -1, marginRight: 3 }} />}{t}
            </button>
          );
        })}
        <input value={customTag} onChange={(e) => setCustomTag(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); addCustom(); } }}
          placeholder="+ custom tag"
          style={{ ...inputStyle, width: 130, padding: '6px 9px', fontSize: 12 }} />
        {customTag.trim() && <button style={{ ...btnGhost, padding: '5px 9px' }} onClick={addCustom}><Plus size={13} /></button>}
      </div>
    );
  };

  const modelPicker = (
    <div>
      <label style={labelStyle}>Model</label>
      <div style={{ position: 'relative' }}>
        <select value={model} onChange={(e) => setModel(e.target.value)}
          style={{ ...inputStyle, appearance: 'none', paddingRight: 30 }}>
          {models.map((m) => (
            <option key={m.value} value={m.value} disabled={!m.online}>
              {m.label}{m.refs > 0 ? ` · up to ${m.refs} ref${m.refs > 1 ? 's' : ''}` : ''}{m.online ? '' : ' (offline)'}
            </option>
          ))}
        </select>
        <ChevronDown size={15} style={{ position: 'absolute', right: 9, top: 11, pointerEvents: 'none', color: '#8a94a3' }} />
      </div>
      {curModel && <div style={{ fontSize: 11, color: '#7f8a99', marginTop: 4 }}>{curModel.note}</div>}
    </div>
  );

  const referencesBlock = (
    <div>
      <label style={labelStyle}>
        Reference images {maxRefs > 0 ? `(up to ${maxRefs})` : ''}
      </label>
      {maxRefs === 0 ? (
        <div style={{ fontSize: 11.5, color: '#7f8a99' }}>
          {curModel?.label || 'This model'} is text-to-image only — pick Klein or Qwen-Image-Edit to use references.
        </div>
      ) : (
        <>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginBottom: refs.length ? 8 : 0 }}>
            {refs.map((r, i) => (
              <div key={`${r.source}-${r.id}`} style={{ position: 'relative', width: 62, height: 62 }}>
                <img src={r.url} alt="" style={{ width: '100%', height: '100%', objectFit: 'cover', borderRadius: 8, border: `1px solid ${C.boxBorder}` }} />
                <button onClick={() => removeRef(i)} title="Remove"
                  style={{ position: 'absolute', top: -6, right: -6, background: '#c0392b', border: 'none', color: '#fff', borderRadius: 999, width: 20, height: 20, cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                  <X size={12} />
                </button>
                <button onClick={() => describeRef(r)} disabled={describeBusy !== null}
                  title="Describe this image with the vision LLM → fill the Character-gen fields"
                  style={{ position: 'absolute', bottom: -6, right: -6, background: '#3b4a7a', border: 'none', color: '#dbe4ff', borderRadius: 999, width: 20, height: 20, cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center', opacity: describeBusy !== null ? 0.5 : 1 }}>
                  {describeBusy === r.id ? <Loader2 size={11} className="spin" /> : <Wand2 size={11} />}
                </button>
              </div>
            ))}
          </div>
          {refs.length < maxRefs && (
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
              <button style={btnGhost} onClick={() => fileInput.current?.click()}><Upload size={14} /> Upload</button>
              <button style={btnGhost} onClick={() => { setPickForRef(true); setTab('gallery'); }}>
                <ImageIcon size={14} /> From gallery
              </button>
              <input ref={fileInput} type="file" accept="image/*" multiple hidden
                onChange={(e) => { addUploadRef(e.target.files); e.currentTarget.value = ''; }} />
            </div>
          )}
          {refs.length > 0 && (
            <div style={{ fontSize: 11, color: '#7f8a99', marginTop: 6 }}>
              Tap <Wand2 size={11} style={{ verticalAlign: -1 }} /> on a reference to describe it with the vision LLM and auto-fill the Character-gen fields.
            </div>
          )}
        </>
      )}
    </div>
  );

  // ── Main render ───────────────────────────────────────────────────────────
  return (
    <div style={{ color: C.panel, display: 'flex', flexDirection: 'column', gap: 14, width: '100%' }}>
      {/* Tabs */}
      <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
        <button style={chip(tab === 'generate')} onClick={() => { setTab('generate'); setPickForRef(false); }}>
          <Sparkles size={14} style={{ marginRight: 6, verticalAlign: -2 }} />Generate
        </button>
        <button style={chip(tab === 'gallery')} onClick={() => { setTab('gallery'); loadGallery(galQ); }}>
          <ImageIcon size={14} style={{ marginRight: 6, verticalAlign: -2 }} />Gallery{galTotal ? ` (${galTotal})` : ''}
        </button>
        {pickForRef && tab === 'gallery' && (
          <span style={{ fontSize: 12, color: C.accent, fontWeight: 600 }}>Tap an image to use it as a reference</span>
        )}
      </div>

      {/* ───────────── GENERATE ───────────── */}
      {tab === 'generate' && (
        <div style={{ display: 'grid', gap: 16, gridTemplateColumns: 'minmax(0,1fr)' }}
          className="iw-generate-grid">
          {/* Form column */}
          <div style={{ display: 'flex', flexDirection: 'column', gap: 14, minWidth: 0 }}>
            {/* Mode + model */}
            <div style={{ display: 'flex', gap: 8 }}>
              <button style={chip(mode === 'freestyle')} onClick={() => setMode('freestyle')}>Freestyle prompt</button>
              <button style={chip(mode === 'character')} onClick={() => setMode('character')}>Character gen</button>
            </div>
            {modelPicker}

            {mode === 'freestyle' ? (
              <div>
                <label style={labelStyle}>Prompt</label>
                <textarea value={prompt} onChange={(e) => setPrompt(e.target.value)} rows={4}
                  placeholder="Describe the image you want to generate…"
                  style={{ ...inputStyle, resize: 'vertical', minHeight: 90 }} />
              </div>
            ) : (
              <>
                {/* Wizard */}
                <div style={{ background: C.box, border: `1px solid ${C.boxBorder}`, borderRadius: 10, padding: 12 }}>
                  <label style={labelStyle}><Wand2 size={12} style={{ verticalAlign: -1, marginRight: 4 }} />Describe → auto-fill the fields</label>
                  <textarea value={wizText} onChange={(e) => setWizText(e.target.value)} rows={2}
                    placeholder="e.g. a curvy Nordic woman in her late 20s, auburn wavy hair, green eyes, freckles, cinematic photoreal"
                    style={{ ...inputStyle, resize: 'vertical' }} />
                  <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginTop: 8 }}>
                    <button style={{ ...btnGhost, borderColor: C.accent, color: '#dbe4ff' }} onClick={runWizard} disabled={wizBusy}>
                      {wizBusy ? <Loader2 size={14} className="spin" /> : <Wand2 size={14} />} Auto-fill
                    </button>
                    {wizMsg && <span style={{ fontSize: 11.5, color: wizMsg.startsWith('✓') ? C.ok : '#e0b34a' }}>{wizMsg}</span>}
                  </div>
                </div>
                {/* Fields */}
                <div style={{ display: 'grid', gap: 10, gridTemplateColumns: 'repeat(auto-fill,minmax(150px,1fr))' }}>
                  {CHAR_FIELDS.map((f) => (
                    <div key={f.key} style={{ gridColumn: f.wide ? '1 / -1' : 'auto' }}>
                      <label style={labelStyle}>{f.label}</label>
                      <input
                        value={f.key === 'name' ? charName : (fields[f.key] || '')}
                        onChange={(e) => f.key === 'name' ? setCharName(e.target.value) : setFields((p) => ({ ...p, [f.key]: e.target.value }))}
                        placeholder={f.placeholder} style={inputStyle} />
                    </div>
                  ))}
                </div>
                <div>
                  <label style={labelStyle}>Extra prompt (optional — appended to the composed description)</label>
                  <input value={prompt} onChange={(e) => setPrompt(e.target.value)} placeholder="e.g. standing in a sunlit studio" style={inputStyle} />
                </div>
              </>
            )}

            {referencesBlock}

            {/* Settings */}
            <div style={{ display: 'grid', gap: 10, gridTemplateColumns: 'repeat(auto-fill,minmax(120px,1fr))' }}>
              <div>
                <label style={labelStyle}>Count</label>
                <select value={count} onChange={(e) => setCount(parseInt(e.target.value))} style={inputStyle}>
                  {[1, 2, 3, 4, 6, 8].map((n) => <option key={n} value={n}>{n}</option>)}
                </select>
              </div>
              <div>
                <label style={labelStyle}>Aspect</label>
                <select value={`${dims.w}x${dims.h}`} onChange={(e) => { const a = ASPECTS.find((x) => `${x.w}x${x.h}` === e.target.value); if (a) setDims({ w: a.w, h: a.h }); }} style={inputStyle}>
                  {ASPECTS.map((a) => <option key={a.label} value={`${a.w}x${a.h}`}>{a.label}</option>)}
                  {!ASPECTS.some((a) => a.w === dims.w && a.h === dims.h) && <option value={`${dims.w}x${dims.h}`}>{dims.w}×{dims.h}</option>}
                </select>
              </div>
              <div>
                <label style={labelStyle}>Width</label>
                <input value={dims.w} inputMode="numeric" onChange={(e) => setDims((d) => ({ ...d, w: parseInt(e.target.value) || 0 }))} style={inputStyle} />
              </div>
              <div>
                <label style={labelStyle}>Height</label>
                <input value={dims.h} inputMode="numeric" onChange={(e) => setDims((d) => ({ ...d, h: parseInt(e.target.value) || 0 }))} style={inputStyle} />
              </div>
            </div>

            {/* Seed + negative */}
            <div style={{ display: 'flex', gap: 10, alignItems: 'flex-end', flexWrap: 'wrap' }}>
              <div style={{ flex: '1 1 140px' }}>
                <label style={labelStyle}>Seed</label>
                <div style={{ display: 'flex', gap: 6 }}>
                  <input value={seed} onChange={(e) => setSeed(e.target.value.replace(/[^0-9]/g, ''))} placeholder="random"
                    style={{ ...inputStyle, flex: 1 }} />
                  <button style={{ ...btnGhost, padding: '8px 10px' }} title={seedLocked ? 'Seed locked — reused every run' : 'Seed random each run'}
                    onClick={() => setSeedLocked((v) => !v)}>
                    {seedLocked ? <Lock size={14} /> : <Unlock size={14} />}
                  </button>
                  <button style={{ ...btnGhost, padding: '8px 10px' }} title="New random seed" onClick={() => { setSeed(''); setSeedLocked(false); }}>
                    <Shuffle size={14} />
                  </button>
                </div>
              </div>
              {model === 'anima' && (
                <div style={{ flex: '2 1 220px' }}>
                  <label style={labelStyle}>Negative (Anima)</label>
                  <input value={negative} onChange={(e) => setNegative(e.target.value)} placeholder="things to avoid" style={inputStyle} />
                </div>
              )}
            </div>

            {err && <div style={{ fontSize: 12.5, color: C.danger }}>{err}</div>}
            <button style={btnPrimary} onClick={startGen} disabled={phase === 'running'}>
              {phase === 'running' ? <Loader2 size={18} className="spin" /> : <Sparkles size={18} />}
              {phase === 'running' ? 'Generating…' : `Generate ${count} image${count > 1 ? 's' : ''}`}
            </button>
          </div>

          {/* Results column */}
          <div style={{ minWidth: 0 }}>
            {phase === 'form' && (
              <div style={{ border: `1px dashed ${C.boxBorder}`, borderRadius: 12, padding: 28, textAlign: 'center', color: '#6f7a89', fontSize: 13 }}>
                Your generated images will appear here. Select the keepers and save them to the shared gallery.
              </div>
            )}
            {(phase === 'running' || phase === 'review') && (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13 }}>
                  {phase === 'running' ? <Loader2 size={15} className="spin" /> : <Check size={15} color={C.ok} />}
                  <span>{status?.done || 0}/{status?.total || count} generated</span>
                  <button style={{ ...btnGhost, marginLeft: 'auto', padding: '5px 10px' }} onClick={() => { setPhase('form'); setStatus(null); setGenId(null); setSelected(new Set()); }}>
                    <RefreshCw size={13} /> New batch
                  </button>
                </div>
                <div style={{ display: 'grid', gap: 8, gridTemplateColumns: 'repeat(auto-fill,minmax(140px,1fr))' }}>
                  {genImages.map((im) => {
                    const sel = selected.has(im.id);
                    return (
                      <div key={im.id} style={{ position: 'relative' }}>
                        <img src={im.url} alt="" onClick={() => setViewer(im.url)}
                          style={{ width: '100%', aspectRatio: `${dims.w}/${dims.h}`, objectFit: 'cover', borderRadius: 10, border: `2px solid ${sel ? C.accent : C.boxBorder}`, cursor: 'zoom-in', display: 'block' }} />
                        <button onClick={() => toggleSel(im.id)} title={sel ? 'Selected' : 'Select'}
                          style={{ position: 'absolute', top: 6, left: 6, width: 26, height: 26, borderRadius: 7, border: 'none', cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center', background: sel ? '#3b6ef0' : 'rgba(0,0,0,0.6)', color: '#fff' }}>
                          {sel ? <Check size={15} /> : <Plus size={15} />}
                        </button>
                        <button onClick={() => dl(im.url, `workshop_${im.id}`)} title="Download"
                          style={{ position: 'absolute', top: 6, right: 6, width: 26, height: 26, borderRadius: 7, border: 'none', cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center', background: 'rgba(0,0,0,0.6)', color: '#fff' }}>
                          <Download size={14} />
                        </button>
                      </div>
                    );
                  })}
                </div>
                {phase === 'review' && genImages.length === 0 && (
                  <div style={{ textAlign: 'center', color: C.danger, fontSize: 12.5, padding: 16 }}>
                    No images produced.{status?.error ? ` ${status.error}` : ''}
                  </div>
                )}
                {status?.error && genImages.length > 0 && <div style={{ fontSize: 11.5, color: '#e0b34a' }}>Some failed: {status.error}</div>}
                {phase === 'review' && genImages.length > 0 && (
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                    <div>
                      <label style={{ ...labelStyle, display: 'flex', alignItems: 'center', gap: 4 }}>
                        <Tag size={11} /> Tags (optional) — categorize what these images are
                      </label>
                      {renderTagChips(saveTags, setSaveTags)}
                    </div>
                    <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
                      <button style={{ ...btnPrimary, width: 'auto', background: '#2f9e58' }} onClick={saveSelected} disabled={selected.size === 0}>
                        <Check size={16} /> Save {selected.size || ''} to gallery
                      </button>
                      {savedNote && <span style={{ fontSize: 12.5, color: C.ok }}>{savedNote}</span>}
                    </div>
                  </div>
                )}
              </div>
            )}
          </div>
        </div>
      )}

      {/* ───────────── GALLERY ───────────── */}
      {tab === 'gallery' && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
            <input value={galQ} onChange={(e) => setGalQ(e.target.value)} onKeyDown={(e) => { if (e.key === 'Enter') loadGallery(galQ); }}
              placeholder="Search prompts…" style={{ ...inputStyle, maxWidth: 280 }} />
            <button style={btnGhost} onClick={() => loadGallery(galQ)}>{galBusy ? <Loader2 size={14} className="spin" /> : <RefreshCw size={14} />} Refresh</button>
            {pickForRef && <button style={{ ...btnGhost, borderColor: C.danger, color: C.danger }} onClick={() => setPickForRef(false)}>Cancel pick</button>}
            <span style={{ fontSize: 12, color: '#7f8a99', marginLeft: 'auto' }}>{galTotal} saved</span>
          </div>
          {allTags.length > 0 && (
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, alignItems: 'center' }}>
              <button style={{ ...chip(tagFilter === ''), padding: '5px 11px', fontSize: 12 }}
                onClick={() => { setTagFilter(''); loadGallery(galQ, ''); }}>All</button>
              {allTags.map((t) => (
                <button key={t} style={{ ...chip(tagFilter.toLowerCase() === t.toLowerCase()), padding: '5px 11px', fontSize: 12 }}
                  onClick={() => { const nt = tagFilter.toLowerCase() === t.toLowerCase() ? '' : t; setTagFilter(nt); loadGallery(galQ, nt); }}>
                  {t}
                </button>
              ))}
            </div>
          )}
          {gallery.length === 0 ? (
            <div style={{ border: `1px dashed ${C.boxBorder}`, borderRadius: 12, padding: 30, textAlign: 'center', color: '#6f7a89', fontSize: 13 }}>
              Nothing saved yet. Generate some images and save the keepers here.
            </div>
          ) : (
            <div style={{ display: 'grid', gap: 8, gridTemplateColumns: 'repeat(auto-fill,minmax(130px,1fr))' }}>
              {gallery.map((it) => (
                <div key={it.id} style={{ position: 'relative' }}>
                  <img src={it.url} alt={it.prompt}
                    onClick={() => pickForRef ? addGalleryRef(it) : setViewer(it.url)}
                    title={it.prompt}
                    style={{ width: '100%', aspectRatio: '3/4', objectFit: 'cover', borderRadius: 10, border: `1px solid ${C.boxBorder}`, cursor: pickForRef ? 'copy' : 'zoom-in', display: 'block' }} />
                  <div style={{ position: 'absolute', bottom: 0, left: 0, right: 0, padding: '4px 6px', background: 'linear-gradient(transparent, rgba(0,0,0,0.8))', borderRadius: '0 0 10px 10px' }}>
                    {(it.tags && it.tags.length > 0) && (
                      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 3, marginBottom: 3 }}>
                        {it.tags.slice(0, 4).map((t) => (
                          <span key={t} style={{ fontSize: 9, fontWeight: 600, color: '#dbe4ff', background: 'rgba(59,74,122,0.9)', borderRadius: 5, padding: '1px 5px' }}>{t}</span>
                        ))}
                      </div>
                    )}
                    <div style={{ fontSize: 10, color: '#cfd6e0', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{it.model}</div>
                  </div>
                  {!pickForRef && (
                    <div style={{ position: 'absolute', top: 5, right: 5, display: 'flex', gap: 4 }}>
                      <button onClick={() => setEditTags({ id: it.id, tags: it.tags || [] })} title="Edit tags"
                        style={{ width: 24, height: 24, borderRadius: 6, border: 'none', cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center', background: 'rgba(0,0,0,0.6)', color: '#c9d6ff' }}>
                        <Tag size={13} />
                      </button>
                      <button onClick={() => useAsReference(it)}
                        title="Use as reference" style={{ width: 24, height: 24, borderRadius: 6, border: 'none', cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center', background: 'rgba(0,0,0,0.6)', color: '#fff' }}>
                        <Repeat size={13} />
                      </button>
                      <button onClick={() => dl(it.url, `workshop_${it.id}`)} title="Download"
                        style={{ width: 24, height: 24, borderRadius: 6, border: 'none', cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center', background: 'rgba(0,0,0,0.6)', color: '#fff' }}>
                        <Download size={13} />
                      </button>
                      <button onClick={() => deleteGalleryItem(it.id)} title="Delete"
                        style={{ width: 24, height: 24, borderRadius: 6, border: 'none', cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center', background: 'rgba(150,20,20,0.75)', color: '#fff' }}>
                        <Trash2 size={13} />
                      </button>
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Per-item tag editor */}
      {editTags && createPortal(
        <div onClick={() => setEditTags(null)}
          style={{ position: 'fixed', inset: 0, zIndex: 100001, background: 'rgba(0,0,0,0.8)', display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 16 }}>
          <div onClick={(e) => e.stopPropagation()}
            style={{ background: '#12161d', border: `1px solid ${C.boxBorder}`, borderRadius: 12, padding: 18, width: 'min(440px, 100%)', display: 'flex', flexDirection: 'column', gap: 12 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <Tag size={16} color={C.accent} />
              <b style={{ fontSize: 14 }}>Edit tags</b>
              <button onClick={() => setEditTags(null)} style={{ marginLeft: 'auto', background: 'transparent', border: 'none', color: '#9aa4b2', cursor: 'pointer' }}><X size={18} /></button>
            </div>
            {renderTagChips(editTags.tags, (next) => setEditTags((p) => p ? { ...p, tags: next } : p))}
            <button style={{ ...btnPrimary, background: '#3b6ef0' }} onClick={commitEditTags}><Check size={16} /> Save tags</button>
          </div>
        </div>, document.body)}

      {/* Full-image viewer */}
      {viewer && createPortal(
        <div onClick={() => setViewer(null)}
          style={{ position: 'fixed', inset: 0, zIndex: 100000, background: 'rgba(0,0,0,0.9)', display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 16 }}>
          <img src={viewer} alt="" style={{ maxWidth: '100%', maxHeight: '100%', objectFit: 'contain', borderRadius: 8 }} />
          <button onClick={() => setViewer(null)} style={{ position: 'absolute', top: 16, right: 16, background: 'rgba(0,0,0,0.6)', color: '#fff', border: 'none', borderRadius: 999, width: 40, height: 40, cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
            <X size={20} />
          </button>
        </div>, document.body)}

      <style>{`
        .spin { animation: iwspin 1s linear infinite; }
        @keyframes iwspin { to { transform: rotate(360deg); } }
        @media (min-width: 860px) {
          .iw-generate-grid { grid-template-columns: minmax(0, 1fr) minmax(0, 1fr) !important; align-items: start; }
        }
      `}</style>
    </div>
  );
}
