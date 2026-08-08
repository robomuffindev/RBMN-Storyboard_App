/**
 * 🧬 Text 2 Image — the master initial character creation mode (v1.269.0).
 *
 * Name the character FIRST (resumable), generate candidates on a chosen model,
 * iterate with Klein edit instructions, keep everything in a master gallery,
 * write the character's lore, and promote the finished image as the FRONT
 * reference + base — from where every other mode (Create views, Clothes,
 * Poses, LoRA, Character Sheet) takes over.
 */
import React, { useCallback, useEffect, useRef, useState } from 'react';

const BASE = '/api/forge';

const box: React.CSSProperties = {
  background: '#12151b', border: '1px solid #2a2f3a', borderRadius: 10, padding: 12,
};
const input: React.CSSProperties = {
  width: '100%', background: '#0e1116', border: '1px solid #2a2f3a', borderRadius: 6,
  color: '#e6e9ee', padding: '6px 8px', fontSize: 13, boxSizing: 'border-box',
};
const btn: React.CSSProperties = {
  background: '#3b82f6', border: 'none', borderRadius: 6, color: '#fff',
  padding: '7px 12px', fontSize: 13, cursor: 'pointer', fontWeight: 600,
};
const btnGhost: React.CSSProperties = {
  ...btn, background: 'transparent', border: '1px solid #2a2f3a', color: '#cbd2dc', fontWeight: 400,
};
const label: React.CSSProperties = { fontSize: 11, color: '#8d97a5', display: 'block', marginBottom: 3 };
const hint: React.CSSProperties = { color: '#8d97a5', fontSize: 12 };
const errTxt: React.CSSProperties = { color: '#ff8a8a', fontSize: 12 };
const okTxt: React.CSSProperties = { color: '#5ee08a', fontSize: 12 };
const chip = (on: boolean): React.CSSProperties => ({
  background: on ? '#1d4ed8' : '#0e1116', border: `1px solid ${on ? '#3b82f6' : '#2a2f3a'}`,
  borderRadius: 999, color: on ? '#fff' : '#cbd2dc', padding: '3px 9px', fontSize: 11,
  cursor: 'pointer',
});

async function j<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let d = '';
    try { d = (await res.json()).detail || ''; } catch { /* ignore */ }
    throw new Error(d || `${res.status} ${res.statusText}`);
  }
  return res.json() as Promise<T>;
}
const post = (url: string, body?: unknown) => fetch(url, {
  method: 'POST', headers: { 'Content-Type': 'application/json' },
  body: body === undefined ? undefined : JSON.stringify(body),
});

interface EngineT { key: string; name: string; supports_refs: boolean; max_refs: number; supports_lora?: boolean; note: string }
interface CharT {
  slug: string; name: string; ref_count: number; has_base: boolean; has_front: boolean;
  forge_images: number; lore_filled: boolean; updated_at?: string;
}
interface ImgT {
  id: string; url: string; kind: string; engine?: string; prompt: string;
  instruction?: string | null; parent?: string | null; seed: number;
  width: number; height: number; pose?: string; starred: boolean; created_at: string;
}
interface TaskT { worker: string | null; status: string; error: string | null }
interface RunT {
  status: string; kind?: string; total?: number; done?: number; error?: string | null;
  tasks?: Record<string, TaskT>; workers?: string[]; images?: { id: string }[];
}
interface LoreT { [k: string]: string | string[] }

const POSES: { key: string; name: string }[] = [
  { key: 'fullbody_front', name: 'Full body · front' },
  { key: 'apose', name: 'A-pose' },
  { key: 'tpose', name: 'T-pose' },
  { key: 'portrait', name: 'Portrait' },
  { key: 'none', name: 'Free (prompt only)' },
];
const LORE_KEYS: { key: string; name: string; rows: number }[] = [
  { key: 'description', name: 'Overall description', rows: 3 },
  { key: 'backstory', name: 'Backstory', rows: 5 },
  { key: 'personality', name: 'Personality', rows: 3 },
  { key: 'motivations', name: 'Motivations', rows: 2 },
  { key: 'relationships', name: 'Relationships', rows: 2 },
  { key: 'voice', name: 'Voice / speech', rows: 2 },
  { key: 'story_role', name: 'Story role', rows: 1 },
  { key: 'occupation', name: 'Occupation', rows: 1 },
  { key: 'strengths', name: 'Strengths', rows: 1 },
  { key: 'flaws', name: 'Flaws', rows: 1 },
  { key: 'fears', name: 'Fears', rows: 1 },
  { key: 'arc', name: 'Character arc', rows: 2 },
  { key: 'notes', name: 'Notes', rows: 3 },
];

export default function Text2ImagePanel(): React.ReactElement {
  const [chars, setChars] = useState<CharT[]>([]);
  const [slug, setSlug] = useState('');            // '' = home screen
  const [engines, setEngines] = useState<EngineT[]>([]);
  const [err, setErr] = useState('');

  // home
  const [newName, setNewName] = useState('');
  // workspace
  const [wsTab, setWsTab] = useState<'generate' | 'gallery' | 'lore'>('generate');
  const [engine, setEngine] = useState('klein');
  const [prompt, setPrompt] = useState('');
  const [count, setCount] = useState(4);
  const [pose, setPose] = useState('fullbody_front');
  const [size, setSize] = useState<'832x1216' | '768x1344' | '1024x1024'>('832x1216');
  const [useFields, setUseFields] = useState(true);
  const [refIds, setRefIds] = useState<string[]>([]);
  const [loras, setLoras] = useState<string[]>([]);
  const [loraName, setLoraName] = useState('');
  const [loraStrength, setLoraStrength] = useState(1.0);
  const [krea2Host, setKrea2Host] = useState('');
  const [hostMsg, setHostMsg] = useState('');
  const [run, setRun] = useState<RunT | null>(null);
  const [gallery, setGallery] = useState<ImgT[]>([]);
  const [history, setHistory] = useState<string[]>([]);
  const [sel, setSel] = useState<ImgT | null>(null);
  const [instruction, setInstruction] = useState('');
  const [editCount, setEditCount] = useState(2);
  const [galFilter, setGalFilter] = useState<'all' | 'starred' | 'gen' | 'edit'>('all');
  const [promoted, setPromoted] = useState('');
  // lore
  const [lore, setLore] = useState<LoreT>({});
  const [fields, setFields] = useState<Record<string, string>>({});
  const [loreDir, setLoreDir] = useState('');
  const [loreBusy, setLoreBusy] = useState(false);
  const [loreMsg, setLoreMsg] = useState('');
  const pollRef = useRef<number | null>(null);

  const loadChars = useCallback(async () => {
    try {
      const r = await j<{ characters: CharT[] }>(await fetch(`${BASE}/characters`));
      setChars(r.characters);
    } catch (e) { setErr(String((e as Error).message || e)); }
  }, []);
  const loadEngines = useCallback(async () => {
    try {
      const r = await j<{ engines: EngineT[]; krea2_host?: string }>(await fetch(`${BASE}/engines`));
      setEngines(r.engines);
      if (r.krea2_host) setKrea2Host(r.krea2_host);
      if (r.engines.length && !r.engines.find((e) => e.key === engine)) setEngine(r.engines[0].key);
    } catch { /* ignore */ }
  }, [engine]);
  const loadLoras = useCallback(async () => {
    try {
      const r = await j<{ loras: string[] }>(await fetch(`${BASE}/loras`));
      setLoras(r.loras || []);
    } catch { setLoras([]); }
  }, []);
  const saveHost = async () => {
    setHostMsg('');
    try {
      await j(await fetch(`${BASE}/krea2-host`, {
        method: 'PUT', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ host: krea2Host }),
      }));
      setHostMsg('saved');
      void loadLoras();
    } catch (e) { setHostMsg(String((e as Error).message || e)); }
  };
  const loadGallery = useCallback(async (s: string) => {
    if (!s) return;
    try {
      const r = await j<{ images: ImgT[]; prompt_history: string[] }>(
        await fetch(`${BASE}/characters/${s}/gallery`));
      setGallery(r.images); setHistory(r.prompt_history || []);
    } catch { /* ignore */ }
  }, []);
  const loadLore = useCallback(async (s: string) => {
    if (!s) return;
    try {
      const r = await j<{ lore: LoreT; fields: Record<string, string> }>(
        await fetch(`${BASE}/characters/${s}/lore`));
      setLore(r.lore); setFields(r.fields || {});
    } catch { /* ignore */ }
  }, []);

  useEffect(() => { void loadChars(); void loadEngines(); }, [loadChars, loadEngines]);
  useEffect(() => { if (engine === 'krea2_turbo') void loadLoras(); }, [engine, loadLoras]);
  useEffect(() => {
    if (slug) { void loadGallery(slug); void loadLore(slug); setSel(null); setPromoted(''); setRun(null); }
  }, [slug, loadGallery, loadLore]);

  // live polling while a run is active
  useEffect(() => {
    if (pollRef.current) { window.clearInterval(pollRef.current); pollRef.current = null; }
    if (!slug) return;
    pollRef.current = window.setInterval(async () => {
      try {
        const st = await j<RunT>(await fetch(`${BASE}/characters/${slug}/status`));
        setRun(st.status === 'idle' ? null : st);
        if (st.status === 'done' || st.status === 'error') {
          void loadGallery(slug);
          if (st.status === 'done') setRun(null);
        }
      } catch { /* ignore */ }
    }, 2500);
    return () => { if (pollRef.current) window.clearInterval(pollRef.current); };
  }, [slug, loadGallery]);

  const createChar = async () => {
    if (!newName.trim()) return;
    setErr('');
    try {
      const r = await j<{ slug: string; resumed: boolean }>(
        await post(`${BASE}/characters`, { name: newName.trim() }));
      setNewName('');
      await loadChars();
      setSlug(r.slug);
    } catch (e) { setErr(String((e as Error).message || e)); }
  };

  const generate = async () => {
    if (!slug || !prompt.trim()) return;
    setErr(''); setPromoted('');
    const [w, h] = size.split('x').map(Number);
    try {
      await j(await post(`${BASE}/characters/${slug}/generate`, {
        engine, prompt: prompt.trim(), count, pose, width: w, height: h,
        ref_image_ids: refIds, use_fields: useFields,
        ...(engine === 'krea2_turbo' && loraName
          ? { lora_name: loraName, lora_strength: loraStrength } : {}),
      }));
      setRun({ status: 'running', total: count, done: 0 });
    } catch (e) { setErr(String((e as Error).message || e)); }
  };

  const iterate = async () => {
    if (!slug || !sel || !instruction.trim()) return;
    setErr(''); setPromoted('');
    try {
      await j(await post(`${BASE}/characters/${slug}/edit`, {
        image_id: sel.id, instruction: instruction.trim(), count: editCount,
      }));
      setInstruction('');
      setRun({ status: 'running', total: editCount, done: 0, kind: 'edit' });
    } catch (e) { setErr(String((e as Error).message || e)); }
  };

  const promote = async () => {
    if (!slug || !sel) return;
    setErr('');
    try {
      const r = await j<{ next: string }>(await post(`${BASE}/characters/${slug}/promote`,
        { image_id: sel.id, also_base: true }));
      setPromoted(r.next);
      void loadChars();
    } catch (e) { setErr(String((e as Error).message || e)); }
  };

  const star = async (img: ImgT) => {
    await post(`${BASE}/characters/${slug}/images/${img.id}/star`, { starred: !img.starred });
    void loadGallery(slug);
    if (sel?.id === img.id) setSel({ ...sel, starred: !img.starred });
  };
  const del = async (img: ImgT) => {
    await post(`${BASE}/characters/${slug}/images/${img.id}/delete`);
    if (sel?.id === img.id) setSel(null);
    void loadGallery(slug);
  };
  const saveLore = async () => {
    setLoreBusy(true); setLoreMsg('');
    try {
      await j(await fetch(`${BASE}/characters/${slug}/lore`, {
        method: 'PUT', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ lore }),
      }));
      setLoreMsg('Saved.');
    } catch (e) { setLoreMsg(String((e as Error).message || e)); }
    setLoreBusy(false);
  };
  const genLore = async (overwrite: boolean) => {
    setLoreBusy(true); setLoreMsg('');
    try {
      const r = await j<{ lore: LoreT; changed: string[] }>(
        await post(`${BASE}/characters/${slug}/lore/generate`,
          { direction: loreDir, overwrite }));
      setLore(r.lore);
      setLoreMsg(`✨ LLM filled: ${r.changed.join(', ') || 'nothing (all fields set — use Overwrite)'}`);
    } catch (e) { setLoreMsg(String((e as Error).message || e)); }
    setLoreBusy(false);
  };

  const eng = engines.find((e) => e.key === engine);
  const chain = sel ? gallery.filter((g) => g.parent === sel.id) : [];
  const parent = sel?.parent ? gallery.find((g) => g.id === sel.parent) : null;
  const filtered = gallery.filter((g) =>
    galFilter === 'all' ? true : galFilter === 'starred' ? g.starred : g.kind === galFilter);

  // ── home screen: name-first + resume ──────────────────────────────────────
  if (!slug) {
    return (
      <div style={{ maxWidth: 860, margin: '0 auto', display: 'flex', flexDirection: 'column', gap: 12 }}>
        <div style={box}>
          <h3 style={{ margin: '0 0 6px', fontSize: 16 }}>🧬 Text 2 Image — start a character</h3>
          <div style={hint}>
            The master creation area when you are NOT starting from reference images. Name the
            character first — everything you generate is saved under that name, so you can come
            back and keep working any time. Finish by promoting your favorite image as the front
            reference; the other modes (views, clothes, poses, LoRA, sheet) take it from there.
          </div>
          <div style={{ display: 'flex', gap: 8, marginTop: 10 }}>
            <input style={{ ...input, flex: 1 }} placeholder="Character name (e.g. Marla Vane)"
                   value={newName} onChange={(e) => setNewName(e.target.value)}
                   onKeyDown={(e) => { if (e.key === 'Enter') void createChar(); }} />
            <button style={btn} disabled={!newName.trim()} onClick={() => void createChar()}>
              ➕ Create &amp; open
            </button>
          </div>
          {err && <div style={{ ...errTxt, marginTop: 6 }}>{err}</div>}
        </div>
        <div style={box}>
          <span style={label}>Continue working on…</span>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(240px, 1fr))', gap: 8 }}>
            {chars.map((c) => (
              <div key={c.slug} style={{ ...box, background: '#0e1116', cursor: 'pointer' }}
                   onClick={() => setSlug(c.slug)}>
                <div style={{ fontWeight: 700, color: '#e6e9ee' }}>{c.name}</div>
                <div style={{ ...hint, marginTop: 4 }}>
                  {c.forge_images} generated · {c.ref_count} refs
                  {c.has_front ? ' · ✅ front ref' : ' · ⬜ no front ref yet'}
                  {c.lore_filled ? ' · 📖 lore' : ''}
                </div>
              </div>
            ))}
            {!chars.length && <div style={hint}>No characters yet — name one above.</div>}
          </div>
        </div>
      </div>
    );
  }

  // ── workspace ──────────────────────────────────────────────────────────────
  const cur = chars.find((c) => c.slug === slug);
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      <div style={{ ...box, display: 'flex', alignItems: 'center', gap: 10 }}>
        <button style={btnGhost} onClick={() => { setSlug(''); void loadChars(); }}>← All characters</button>
        <h3 style={{ margin: 0, fontSize: 16 }}>🧬 {cur?.name || slug}</h3>
        <span style={hint}>
          {cur?.has_front ? '✅ front reference set — other modes ready'
            : '1️⃣ Generate → 2️⃣ Iterate → 3️⃣ Use as front reference'}
        </span>
        <div style={{ flex: 1 }} />
        {(['generate', 'gallery', 'lore'] as const).map((t) => (
          <button key={t} style={chip(wsTab === t)} onClick={() => setWsTab(t)}>
            {t === 'generate' ? '🎨 Generate' : t === 'gallery' ? `🖼 Gallery (${gallery.length})` : '📖 Profile & Lore'}
          </button>
        ))}
      </div>

      {err && <div style={{ ...box, ...errTxt }}>{err}</div>}
      {promoted && <div style={{ ...box, ...okTxt }}>🏁 {promoted}</div>}

      {wsTab === 'generate' && (
        <div style={{ display: 'grid', gridTemplateColumns: '340px 1fr', gap: 12 }}>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            <div style={box}>
              <span style={label}>Model</span>
              <select style={input} value={engine} onChange={(e) => { setEngine(e.target.value); setRefIds([]); }}>
                {engines.map((e) => (
                  <option key={e.key} value={e.key}>{e.name}{e.supports_refs ? ' (refs ✓)' : ''}</option>
                ))}
              </select>
              {eng && <div style={{ ...hint, marginTop: 4 }}>{eng.note}</div>}

              {eng?.supports_lora && (
                <div style={{ marginTop: 10, padding: 8, background: '#0e1116', borderRadius: 6, border: '1px solid #2a2f3a' }}>
                  <span style={label}>🎓 Character LoRA (Krea 2 only)</span>
                  <select style={input} value={loraName} onChange={(e) => setLoraName(e.target.value)}>
                    <option value="">— none —</option>
                    {loras.map((l) => <option key={l} value={l}>{l}</option>)}
                  </select>
                  {loraName && (
                    <div style={{ display: 'flex', gap: 6, marginTop: 6, alignItems: 'center' }}>
                      <span style={hint}>strength:</span>
                      {[1.0, 0.8].map((v) => (
                        <button key={v} style={chip(loraStrength === v)} onClick={() => setLoraStrength(v)}>{v.toFixed(1)}</button>
                      ))}
                    </div>
                  )}
                  {loraName && (
                    <div style={{ ...hint, marginTop: 4 }}>
                      Include the character's trigger in your prompt (e.g. “rbmnredv1 woman …”)
                      and always name the outfit — an unclothed prompt renders skin.
                    </div>
                  )}
                  <div style={{ display: 'flex', gap: 6, marginTop: 8, alignItems: 'center' }}>
                    <span style={{ ...hint, whiteSpace: 'nowrap' }}>Krea 2 box IP:</span>
                    <input style={{ ...input, flex: 1 }} value={krea2Host}
                           onChange={(e) => setKrea2Host(e.target.value)} />
                    <button style={btnGhost} onClick={() => void saveHost()}>Save</button>
                    {hostMsg && <span style={hint}>{hostMsg}</span>}
                  </div>
                </div>
              )}

              <div style={{ marginTop: 10 }}>
                <span style={label}>Prompt (who is this character?)</span>
                <textarea style={{ ...input, minHeight: 84 }} value={prompt}
                          placeholder="a wiry middle-aged detective with silver-streaked hair, worn brown trench coat…"
                          onChange={(e) => setPrompt(e.target.value)} />
                {history.length > 0 && (
                  <select style={{ ...input, marginTop: 4 }} value=""
                          onChange={(e) => { if (e.target.value) setPrompt(e.target.value); }}>
                    <option value="">↩ reuse an earlier prompt…</option>
                    {history.slice().reverse().map((h, i) => (
                      <option key={i} value={h}>{h.slice(0, 90)}</option>
                    ))}
                  </select>
                )}
              </div>

              <div style={{ marginTop: 10, display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                {POSES.map((p) => (
                  <button key={p.key} style={chip(pose === p.key)} onClick={() => setPose(p.key)}>{p.name}</button>
                ))}
              </div>
              <div style={{ ...hint, marginTop: 4 }}>
                “Full body · front” locks head-to-feet framing facing the camera — the shape every
                downstream mode wants for a front reference.
              </div>

              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8, marginTop: 10 }}>
                <div>
                  <span style={label}>How many at once ({count})</span>
                  <input type="range" min={1} max={8} value={count} style={{ width: '100%' }}
                         onChange={(e) => setCount(Number(e.target.value))} />
                </div>
                <div>
                  <span style={label}>Size</span>
                  <select style={input} value={size} onChange={(e) => setSize(e.target.value as typeof size)}>
                    <option value="832x1216">832×1216 (portrait)</option>
                    <option value="768x1344">768×1344 (tall)</option>
                    <option value="1024x1024">1024×1024 (square)</option>
                  </select>
                </div>
              </div>

              <div style={{ marginTop: 10, display: 'flex', gap: 6 }}>
                <button style={chip(useFields)} onClick={() => setUseFields(!useFields)}>
                  {useFields ? '☑' : '☐'} include character field sheet in prompt
                </button>
              </div>

              {eng?.supports_refs && gallery.length > 0 && (
                <div style={{ marginTop: 10 }}>
                  <span style={label}>Reference images (up to {eng.max_refs} — click to toggle)</span>
                  <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                    {gallery.slice(0, 12).map((g) => (
                      <img key={g.id} src={g.url} alt="" title={g.prompt}
                           style={{ width: 52, height: 76, objectFit: 'cover', borderRadius: 4,
                                    cursor: 'pointer',
                                    border: refIds.includes(g.id) ? '2px solid #3b82f6' : '2px solid transparent' }}
                           onClick={() => setRefIds((ids) => ids.includes(g.id)
                             ? ids.filter((x) => x !== g.id)
                             : ids.length < (eng?.max_refs || 5) ? [...ids, g.id] : ids)} />
                    ))}
                  </div>
                </div>
              )}

              <button style={{ ...btn, width: '100%', marginTop: 12,
                               opacity: run?.status === 'running' || !prompt.trim() ? 0.6 : 1 }}
                      disabled={run?.status === 'running' || !prompt.trim()}
                      onClick={() => void generate()}>
                {run?.status === 'running' ? `Rendering… ${run.done ?? 0}/${run.total ?? count}` : `🎨 Generate ${count}`}
              </button>
            </div>

            {run && (
              <div style={box}>
                <span style={label}>Live run {run.kind === 'edit' ? '(edit)' : ''} — workers: {(run.workers || []).join(', ') || '…'}</span>
                {Object.entries(run.tasks || {}).map(([k, t]) => (
                  <div key={k} style={{ fontSize: 12, color: t.status === 'error' ? '#ff8a8a' : '#cbd2dc' }}>
                    #{Number(k) + 1} · {t.worker || '—'} · {t.status}{t.error ? ` — ${t.error.slice(0, 120)}` : ''}
                  </div>
                ))}
                {run.error && <div style={errTxt}>{run.error}</div>}
              </div>
            )}
          </div>

          <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            <div style={box}>
              <span style={label}>Latest results (newest first — click one to work on it)</span>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(150px, 1fr))', gap: 8 }}>
                {gallery.slice(0, 12).map((g) => (
                  <div key={g.id} style={{ position: 'relative' }}>
                    <img src={g.url} alt="" onClick={() => setSel(g)}
                         style={{ width: '100%', borderRadius: 6, cursor: 'pointer',
                                  border: sel?.id === g.id ? '2px solid #3b82f6' : '2px solid transparent' }} />
                    <div style={{ position: 'absolute', top: 4, left: 4, fontSize: 12 }}>
                      {g.starred ? '⭐' : ''}{g.kind === 'edit' ? ' ✏' : ''}
                    </div>
                  </div>
                ))}
                {!gallery.length && <div style={hint}>Nothing yet — hit 🎨 Generate.</div>}
              </div>
            </div>

            {sel && (
              <div style={{ ...box, display: 'grid', gridTemplateColumns: '260px 1fr', gap: 12 }}>
                <img src={sel.url} alt="" style={{ width: '100%', borderRadius: 8 }} />
                <div>
                  <div style={hint}>
                    {sel.kind === 'edit' ? `✏ edit of ${sel.parent}` : `🎨 ${sel.engine}`} · seed {sel.seed}
                    {parent && (
                      <> · <a style={{ color: '#7fb2ff', cursor: 'pointer' }}
                              onClick={() => setSel(parent)}>view parent</a></>
                    )}
                  </div>
                  {sel.instruction && <div style={{ ...hint, marginTop: 2 }}>“{sel.instruction}”</div>}

                  <div style={{ marginTop: 10 }}>
                    <span style={label}>✏ Keep editing this character (Klein edit — describe the change)</span>
                    <textarea style={{ ...input, minHeight: 56 }} value={instruction}
                              placeholder="change the hair to a short silver buzzcut / make him taller and broader / give her a scar over the left eyebrow…"
                              onChange={(e) => setInstruction(e.target.value)} />
                    <div style={{ display: 'flex', gap: 8, marginTop: 6, alignItems: 'center' }}>
                      <span style={hint}>variants:</span>
                      {[1, 2, 3, 4].map((n) => (
                        <button key={n} style={chip(editCount === n)} onClick={() => setEditCount(n)}>{n}</button>
                      ))}
                      <div style={{ flex: 1 }} />
                      <button style={btn} disabled={!instruction.trim() || run?.status === 'running'}
                              onClick={() => void iterate()}>✏ Apply edit</button>
                    </div>
                  </div>

                  {chain.length > 0 && (
                    <div style={{ marginTop: 8 }}>
                      <span style={label}>Edits made from this image</span>
                      <div style={{ display: 'flex', gap: 4 }}>
                        {chain.map((g) => (
                          <img key={g.id} src={g.url} alt="" style={{ width: 44, borderRadius: 4, cursor: 'pointer' }}
                               onClick={() => setSel(g)} />
                        ))}
                      </div>
                    </div>
                  )}

                  <div style={{ display: 'flex', gap: 6, marginTop: 12, flexWrap: 'wrap' }}>
                    <button style={btn} onClick={() => void promote()}>
                      🏁 Use as FRONT reference {cur?.has_front ? '(replace)' : ''}
                    </button>
                    <button style={btnGhost} onClick={() => void star(sel)}>{sel.starred ? '★ Unstar' : '⭐ Star'}</button>
                    <a href={`${sel.url}?download=1`} download>
                      <button style={btnGhost}>📥</button>
                    </a>
                    <button style={{ ...btnGhost, color: '#ff8a8a' }} onClick={() => void del(sel)}>🗑</button>
                  </div>
                  <div style={{ ...hint, marginTop: 6 }}>
                    🏁 copies this image into the character as the front reference AND the active
                    base. Then use the Create tab to 🧭 generate the missing views, and every other
                    mode is unlocked.
                  </div>
                </div>
              </div>
            )}
          </div>
        </div>
      )}

      {wsTab === 'gallery' && (
        <div style={box}>
          <div style={{ display: 'flex', gap: 6, marginBottom: 8 }}>
            {(['all', 'starred', 'gen', 'edit'] as const).map((f) => (
              <button key={f} style={chip(galFilter === f)} onClick={() => setGalFilter(f)}>
                {f === 'all' ? `All (${gallery.length})` : f === 'starred' ? '⭐ Starred' : f === 'gen' ? '🎨 Generations' : '✏ Edits'}
              </button>
            ))}
            <div style={{ flex: 1 }} />
            <span style={hint}>Everything is kept here until you delete it.</span>
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(140px, 1fr))', gap: 8 }}>
            {filtered.map((g) => (
              <div key={g.id}>
                <img src={g.url} alt="" title={g.instruction || g.prompt}
                     style={{ width: '100%', borderRadius: 6, cursor: 'pointer' }}
                     onClick={() => { setSel(g); setWsTab('generate'); }} />
                <div style={{ display: 'flex', gap: 4, marginTop: 2 }}>
                  <button style={{ ...btnGhost, flex: 1, padding: '2px 4px', fontSize: 11 }}
                          onClick={() => void star(g)}>{g.starred ? '★' : '☆'}</button>
                  <a href={`${g.url}?download=1`} download style={{ flex: 1 }}>
                    <button style={{ ...btnGhost, width: '100%', padding: '2px 4px', fontSize: 11 }}>📥</button>
                  </a>
                  <button style={{ ...btnGhost, flex: 1, padding: '2px 4px', fontSize: 11, color: '#ff8a8a' }}
                          onClick={() => void del(g)}>🗑</button>
                </div>
              </div>
            ))}
            {!filtered.length && <div style={hint}>Empty.</div>}
          </div>
        </div>
      )}

      {wsTab === 'lore' && (
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
          <div style={box}>
            <h4 style={{ margin: '0 0 6px', fontSize: 14 }}>📖 Profile &amp; Lore</h4>
            <div style={hint}>
              The character's story substrate — this is what the Story Builder mode will read.
              Fill by hand, or let the LLM draft from the physical sheet (it fills only EMPTY
              fields unless you overwrite).
            </div>
            <div style={{ display: 'flex', gap: 8, marginTop: 8 }}>
              <input style={{ ...input, flex: 1 }} value={loreDir} placeholder="optional direction: 'a tragic ex-cop turned street preacher'…"
                     onChange={(e) => setLoreDir(e.target.value)} />
              <button style={btnGhost} disabled={loreBusy} onClick={() => void genLore(false)}>✨ Fill empty</button>
              <button style={btnGhost} disabled={loreBusy} onClick={() => void genLore(true)}>✨ Overwrite all</button>
            </div>
            {loreMsg && <div style={{ ...hint, marginTop: 4 }}>{loreMsg}</div>}
            <div style={{ marginTop: 10 }}>
              {LORE_KEYS.map(({ key, name, rows }) => (
                <div key={key} style={{ marginBottom: 8 }}>
                  <span style={label}>{name}</span>
                  <textarea style={{ ...input, minHeight: rows * 20 }} rows={rows}
                            value={String(lore[key] ?? '')}
                            onChange={(e) => setLore({ ...lore, [key]: e.target.value })} />
                </div>
              ))}
              <div style={{ marginBottom: 8 }}>
                <span style={label}>Tags (comma-separated)</span>
                <input style={input}
                       value={Array.isArray(lore.tags) ? (lore.tags as string[]).join(', ') : String(lore.tags ?? '')}
                       onChange={(e) => setLore({ ...lore, tags: e.target.value.split(',').map((t) => t.trim()).filter(Boolean) })} />
              </div>
              <button style={btn} disabled={loreBusy} onClick={() => void saveLore()}>💾 Save lore</button>
            </div>
          </div>
          <div style={box}>
            <h4 style={{ margin: '0 0 6px', fontSize: 14 }}>Physical sheet (from the Create tab)</h4>
            <div style={hint}>
              These are the character's Klein 3.0 fields — edit them in the Create tab; the
              generate prompt here can include them automatically.
            </div>
            <div style={{ marginTop: 8 }}>
              {Object.entries(fields).filter(([, v]) => String(v || '').trim()).map(([k, v]) => (
                <div key={k} style={{ fontSize: 12, color: '#cbd2dc', padding: '2px 0' }}>
                  <b style={{ color: '#8d97a5' }}>{k.replace(/_/g, ' ')}:</b> {String(v)}
                </div>
              ))}
              {!Object.values(fields).some((v) => String(v || '').trim()) && (
                <div style={hint}>No fields yet — the 🪄 Analyze button in the Create tab can fill
                them from images, or type them there by hand.</div>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
