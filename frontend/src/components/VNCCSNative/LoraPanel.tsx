/**
 * 🎓 LoRA Dataset Gen — the fifth mode (v1.209.0).
 *
 * Pick a Klein 3.0 character, plan a researched shot list, render it across all
 * workers, caption it, run a vision QC pass, review every image + caption in a
 * gallery, then export a training-ready zip.
 */
import React, { useCallback, useEffect, useState } from 'react';

const BASE = '/api/lora';
const K3 = '/api/klein3';

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
const btnSm: React.CSSProperties = { ...btnGhost, padding: '4px 8px', fontSize: 12 };
const label: React.CSSProperties = { fontSize: 11, color: '#8d97a5', display: 'block', marginBottom: 3 };
const hint: React.CSSProperties = { color: '#8d97a5', fontSize: 12 };
const okTxt: React.CSSProperties = { color: '#5ee08a', fontSize: 12 };
const errTxt: React.CSSProperties = { color: '#ff8a8a', fontSize: 12 };
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

interface CharT { slug: string; name: string; ref_count: number; has_base: boolean; missing_views: string[] }
interface OutfitT { id?: string; name: string; desc: string; kind: 'named' | 'variety'; ref_id?: string | null }
interface CharRefT { id: string; tag: string; name: string; url?: string }
interface QcT {
  ok: boolean; issues?: string[]; angle_ok?: boolean;
  yaw?: number | null; angle_method?: string; angle_note?: string;
  framing_ok?: boolean; framing_method?: string; framing_note?: string;
  face_h_ratio?: number | null;
  expression_ok?: boolean; one_person?: boolean; face_clear?: boolean;
  artifacts?: boolean; server?: string;
}
interface ItemT {
  id: string; framing: string; angle: string; expression: string; pose?: string | null;
  lighting: string; background: string; status: string; caption: string;
  caption_extra?: string; qc?: QcT | null; url: string; has_image: boolean;
  width: number; height: number; identity?: string; keep?: boolean; attempts?: number;
}
interface RunT {
  status: string; kind?: string; done: number; total: number; detail?: string;
  error?: string | null; tasks?: Record<string, { worker?: string | null; server?: string | null; status: string }>;
  workers?: string[]; round?: number; rounds?: number; phase?: string;
  history?: Array<{ round: number; rendered: number; flagged: number | null }>;
  summary?: FlagsT;
}
/** Mirrors the dict built in `backend/api/lora.py::_flag_summary`.
 *
 *  ⚠ v1.276.41 — this was missing eight keys the backend has always sent, and
 *  `arcface_scored` was already being READ in the panel, so `tsc` failed on it.
 *  The build ships `vite build` with no typecheck, so it never surfaced. If you
 *  add a counter to that dict, add it here. */
interface FlagsT {
  flagged: number; checked: number; artifacts: number;
  angle_off: number; angle_measured?: number; angle_unmeasured?: number;
  framing_off?: number; framing_measured?: number; framing_unmeasured?: number;
  crop_off?: number; crop_measured?: number; crop_unmeasured?: number;
  bare_skin?: number; wardrobe_measured?: number; wardrobe_unmeasured?: number;
  outfit_off?: number; arcface_scored?: number; no_face?: number;
  back_low_likeness?: number;
  expression_off: number;
  not_checked?: string[]; unreliable?: string[];
  not_one_person: number; face_unclear: number; identity_off: number; stuck: number;
  top_issues?: Record<string, number>;
}
interface DatasetT {
  id: string; name: string; char_slug: string; char_name: string; trigger: string;
  class_token: string; target: string; outfit: string; created_at: string;
  items: ItemT[]; run?: RunT | null; exports?: string[];
  flags?: FlagsT; max_attempts?: number;
}
interface DsSummaryT {
  id: string; name: string; char_name?: string; char_slug: string; trigger: string;
  target: string; created_at: string; total: number; rendered: number; captioned: number;
  flagged: number; exports: string[];
}
interface RecipeT {
  framings: Array<{ key: string; weight: number; caption: string; size: number[] }>;
  angles: Array<{ key: string; caption: string; base_view: string }>;
  expressions: Array<{ key: string; caption: string }>;
  poses: Array<{ key: string; caption: string }>;
  lighting: Array<{ key: string; caption: string }>;
  backgrounds: Array<{ key: string; caption: string }>;
}

export default function LoraPanel() {
  const [chars, setChars] = useState<CharT[]>([]);
  const [recipe, setRecipe] = useState<RecipeT | null>(null);
  const [list, setList] = useState<DsSummaryT[]>([]);
  const [ds, setDs] = useState<DatasetT | null>(null);
  const [msg, setMsg] = useState('');
  const [err, setErr] = useState('');
  const [busy, setBusy] = useState(false);
  const [lightbox, setLightbox] = useState('');
  const [health, setHealth] = useState<{ klein_workers?: Array<{ url: string }>; vision?: { servers?: number; model?: string; error?: string } } | null>(null);

  // new-dataset form
  const [nName, setNName] = useState('');
  const [nChar, setNChar] = useState('');
  const [nTrigger, setNTrigger] = useState('');
  const [nClass, setNClass] = useState('man');
  const [nTarget, setNTarget] = useState('krea2');
  const [nCount, setNCount] = useState(40);
  const [nOutfit] = useState('');   // no setter wired up yet — read-only default
  const [wardrobe, setWardrobe] = useState<OutfitT[]>([]);
  const [charRefs, setCharRefs] = useState<CharRefT[]>([]);
  const [wbBusy, setWbBusy] = useState('');
  const [nBaseMode, setNBaseMode] = useState<'' | 'dressed' | 'stripped' | 'auto'>('');
  const [autoSize, setAutoSize] = useState(true);
  const [acting, setActing] = useState('');   // the path of the request in flight
  const [stale, setStale] = useState(0);     // consecutive failed refreshes
  const [beat, setBeat] = useState(0);       // heartbeat, so a live run looks live
  const [lastRun, setLastRun] = useState('');  // what the previous run actually did
  const [live, setLive] = useState(false);     // is `msg` describing something in flight?
  const [nPreset, setNPreset] = useState<'balanced' | 'face_heavy'>('balanced');

  // gallery filters
  const [filter, setFilter] = useState<'all' | 'missing' | 'flagged' | 'nocap' | 'angle'>('all');
  const [fFraming, setFFraming] = useState('');   // '' = any shot type
  const [fAngle, setFAngle] = useState('');       // '' = any angle
  const [rounds, setRounds] = useState(3);
  const [retryStuck, setRetryStuck] = useState(false);
  const [editId, setEditId] = useState('');
  const [editCap, setEditCap] = useState('');

  const load = useCallback(async () => {
    try {
      const [c, l, r] = await Promise.all([
        j<{ characters: CharT[] }>(await fetch(`${K3}/characters`)),
        j<{ datasets: DsSummaryT[] }>(await fetch(`${BASE}/datasets`)),
        j<RecipeT>(await fetch(`${BASE}/recipe`)),
      ]);
      setChars(c.characters || []);
      setList(l.datasets || []);
      setRecipe(r);
      if (!nChar && (c.characters || []).length) setNChar(c.characters[0].slug);
    } catch (e) { setErr((e as Error).message); }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const loadHealth = useCallback(async () => {
    try { setHealth(await j(await fetch(`${BASE}/health`))); } catch { setHealth(null); }
  }, []);

  useEffect(() => { void load(); void loadHealth(); }, [load, loadHealth]);

  const openDs = async (id: string) => {
    setErr(''); setMsg('');
    try { setDs(await j<DatasetT>(await fetch(`${BASE}/datasets/${id}`))); }
    catch (e) { setErr((e as Error).message); }
  };
  // v1.227: depend on the ID, not the whole object. Keyed on `ds` this
  // callback changed identity on every poll, restarting the interval each time.
  const dsId = ds?.id;
  const refreshDs = useCallback(async () => {
    if (!dsId) return;
    try {
      setDs(await j<DatasetT>(await fetch(`${BASE}/datasets/${dsId}`)));
      setStale(0);
    } catch {
      // v1.227: this used to be swallowed entirely, so a backend that stopped
      // answering looked identical to one with nothing to report.
      setStale((n) => n + 1);
    }
  }, [dsId]);

  // v1.227: poll whenever a dataset is OPEN, not only once a run is already
  // known to be running. The old guard was circular — the single refresh after
  // a POST was the only chance to notice a run had started, and missing it left
  // the panel frozen with no banner and no error.
  useEffect(() => {
    if (!dsId) return;
    const running = ds?.run?.status === 'running';
    const t = setInterval(() => {
      void refreshDs();
      if (running) void load();
    }, running ? 3000 : 6000);
    return () => clearInterval(t);
  }, [dsId, ds?.run?.status, refreshDs, load]);

  // a visible tick, so "working" is distinguishable from "hung" even before
  // `detail` moves
  useEffect(() => {
    if (ds?.run?.status !== 'running') return;
    const t = setInterval(() => setBeat((b) => (b + 1) % 4), 700);
    return () => clearInterval(t);
  }, [ds?.run?.status]);

  // v1.230: report the TRANSITION. Without this the click-time message
  // ("checking 12") stayed on screen after the banner vanished, which is
  // indistinguishable from a hung run.
  const prevRun = React.useRef<string | undefined>(undefined);
  useEffect(() => {
    const st = ds?.run?.status;
    const was = prevRun.current;
    prevRun.current = st;
    if (was !== 'running' || st === 'running' || !ds) return;
    const kind = ds.run?.kind || 'run';
    const flagged = (ds.items || []).filter((i) => i.qc?.ok === false).length;
    const checked = (ds.items || []).filter((i) => i.qc).length;
    const rendered = (ds.items || []).filter((i) => i.has_image).length;
    const bits = kind === 'qc'
      ? `${checked} checked · ${flagged} flagged`
      : `${rendered}/${(ds.items || []).length} rendered${flagged ? ` · ${flagged} flagged` : ''}`;
    const err = ds.run?.error;
    setLive(false);
    setLastRun(`${err ? '⚠' : '✓'} ${kind} finished — ${bits}${err ? ` — ${err}` : ''}`);
    setMsg('');
  }, [ds?.run?.status, ds]);

  // The garment picker needs the character's own reference images. Loaded when
  // the character changes, not per render.
  useEffect(() => {
    if (!nChar) { setCharRefs([]); return; }
    void (async () => {
      try {
        const c = await j<{ refs?: CharRefT[] }>(await fetch(`/api/klein3/characters/${nChar}`));
        setCharRefs(c.refs || []);
      } catch { setCharRefs([]); }
    })();
  }, [nChar]);

  // ~13 images per outfit is what the backend sizes to; mirror it so the number
  // on screen is the number that will be used.
  const sizedCount = Math.max(24, Math.min(Math.round(Math.max(1, wardrobe.length) * 13), 120));
  const effCount = wardrobe.length && autoSize ? sizedCount : nCount;
  const namedN = wardrobe.filter((o) => o.kind === 'named').length;
  const varietyN = wardrobe.length - namedN;
  const perOutfit = wardrobe.length ? Math.round(effCount / wardrobe.length) : 0;

  const addOutfit = (kind: 'named' | 'variety') =>
    setWardrobe((w) => [...w, { name: kind === 'named' ? `outfit ${w.length + 1}` : `look ${w.length + 1}`,
                                desc: '', kind }]);
  const setOutfit = (i: number, patch: Partial<OutfitT>) =>
    setWardrobe((w) => w.map((o, k) => (k === i ? { ...o, ...patch } : o)));
  const delOutfit = (i: number) => setWardrobe((w) => w.filter((_, k) => k !== i));

  const suggestWardrobe = async () => {
    if (!nChar) return;
    setWbBusy('suggest'); setErr('');
    try {
      const r = await j<{ character_type: string; outfits: OutfitT[] }>(
        await fetch(`/api/lora/characters/${nChar}/wardrobe`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ count: 5 }),
        }));
      // appended for REVIEW, never applied silently
      setWardrobe((w) => [...w, ...r.outfits.map((o) => ({ ...o, kind: 'variety' as const }))]);
      if (r.character_type) setMsg(`read as: ${r.character_type} — edit anything that is wrong`);
    } catch (e) { setErr((e as Error).message); }
    finally { setWbBusy(''); }
  };

  // Klein ignores category words, so a garment reference is useless without
  // NAMED garments. This is what produces the name.
  const nameGarment = async (i: number, refId: string) => {
    setOutfit(i, { ref_id: refId || null });
    if (!refId || !nChar) return;
    setWbBusy(`ref${i}`); setErr('');
    try {
      const r = await j<{ desc: string }>(
        await fetch(`/api/lora/characters/${nChar}/refs/${refId}/garment`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}',
        }));
      setOutfit(i, { desc: r.desc, ref_id: refId });
    } catch (e) { setErr((e as Error).message); }
    finally { setWbBusy(''); }
  };

  const createDs = async () => {
    if (!nName.trim() || !nChar) return;
    setBusy(true); setErr(''); setMsg('');
    try {
      const d = await j<DatasetT>(await fetch(`${BASE}/datasets`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: nName.trim(), char_slug: nChar, trigger: nTrigger.trim(),
          class_token: nClass.trim() || 'person', target: nTarget,
          count: wardrobe.length && autoSize ? null : nCount,
          outfit: nOutfit.trim(), preset: nPreset,
          outfits: wardrobe.filter((o) => o.desc.trim()),
          base_mode: nBaseMode || null,
        }),
      }));
      setDs(d); setNName(''); await load();
      setMsg(`✓ planned ${d.items.length} images — review the plan, then render`);
    } catch (e) { setErr((e as Error).message); }
    setBusy(false);
  };

  const post = async (path: string, body?: unknown, note?: string) => {
    setErr(''); setMsg(''); setActing(path);
    try {
      const r = await j<Record<string, unknown>>(await fetch(`${BASE}${path}`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body || {}),
      }));
      // A `started: false` reply is the server saying it had nothing to do. That
      // is the most confusing outcome there is, so it must be LOUD, not a line
      // at the top of the page.
      if ((r as { started?: boolean }).started === false) { setMsg(`ℹ ${String(r.note || 'nothing to do')}`); setLive(false); }
      else if (note) { setMsg(note); setLive(true); }
      await refreshDs(); await load();
      return r;
    } catch (e) { setErr((e as Error).message); return null; }
    finally { setActing(''); }
  };

  // v1.229: state AND shape. Every finding so far has been indexed on framing or
  // angle, so those had to become things you can act on, not just read about.
  const shown = (ds?.items || []).filter((it) => {
    if (fFraming && it.framing !== fFraming) return false;
    if (fAngle && it.angle !== fAngle) return false;
    return filter === 'all' ? true
      : filter === 'missing' ? !it.has_image
        : filter === 'flagged' ? it.qc?.ok === false
          // v1.241: was 'cropped', which the backend no longer measures. Wrong
          // ANGLE is measured, so that is what you can now filter to.
          : filter === 'angle' ? it.qc?.angle_ok === false
            : !(it.caption || '').trim();
  });
  const shownIds = shown.map((i) => i.id);

  // per shot type, so a problem that lives in ONE framing is visible in the app
  // rather than only in a dump — 58% of full-body shots were cropped and the UI
  // had no way to show it.
  const byFraming = ['face', 'headshot', 'upper', 'full'].map((f) => {
    const g = (ds?.items || []).filter((i) => i.framing === f);
    return {
      key: f, n: g.length,
      rendered: g.filter((i) => i.has_image).length,
      flagged: g.filter((i) => i.qc?.ok === false).length,
      angleOff: g.filter((i) => i.qc?.angle_ok === false).length,
    };
  }).filter((r) => r.n);

  const counts = {
    total: ds?.items.length || 0,
    rendered: (ds?.items || []).filter((i) => i.has_image).length,
    captioned: (ds?.items || []).filter((i) => (i.caption || '').trim()).length,
    checked: (ds?.items || []).filter((i) => i.qc).length,
    flagged: (ds?.items || []).filter((i) => i.qc?.ok === false).length,
  };
  const run = ds?.run;
  const char = chars.find((c) => c.slug === (ds?.char_slug || nChar));

  return (
    <div style={{ display: 'grid', gap: 14 }}>
      <AutogenBox chars={chars} />
      <div style={box}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
          <h3 style={{ margin: 0, fontSize: 15 }}>🎓 LoRA Dataset Gen</h3>
          <span style={hint}>
            plan → render → caption → QC → export. Captions name only what VARIES; his face and
            build stay uncaptioned so they bind to the trigger word.
          </span>
          <div style={{ flex: 1 }} />
          <span style={hint}>
            {health?.klein_workers?.length ? `${health.klein_workers.length} klein worker(s)` : 'no klein worker'}
            {' · '}
            {health?.vision?.model ? `vision: ${health.vision.model}` : 'vision model unset'}
          </span>
          <button style={btnSm} onClick={() => { void load(); void loadHealth(); }}>↻</button>
        </div>
      </div>

      {err && <p style={errTxt}>{err}</p>}
      {msg && <p style={okTxt}>{msg}</p>}

      <div style={{ display: 'grid', gridTemplateColumns: 'minmax(280px, 360px) 1fr', gap: 14, alignItems: 'start' }}>
        {/* ── left: new dataset + list ─────────────────────────────────── */}
        <div style={{ display: 'grid', gap: 14 }}>
          <div style={box}>
            <b style={{ fontSize: 13, color: '#e6e9ee' }}>➕ New dataset</b>
            <div style={{ display: 'grid', gap: 6, marginTop: 8 }}>
              <div>
                <label style={label}>Name</label>
                <input style={input} value={nName} onChange={(e) => setNName(e.target.value)}
                       placeholder="Duke v1" />
              </div>
              <div>
                <label style={label}>Character (Klein 3.0)</label>
                <select style={input} value={nChar} onChange={(e) => setNChar(e.target.value)}>
                  {chars.map((c) => (
                    <option key={c.slug} value={c.slug}>
                      {c.name} — {c.ref_count} refs{c.has_base ? '' : ' (no base yet)'}
                    </option>
                  ))}
                </select>
              </div>
              {char && char.missing_views.length > 0 && (
                <p style={{ ...hint, margin: 0, color: '#e0b36a' }}>
                  ⚠ missing views: {char.missing_views.join(', ')} — fill them in 🎯 Klein 3.0
                  (🧭 Generate missing views) so side and back shots use a real reference.
                </p>
              )}
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 6 }}>
                <div>
                  <label style={label}>Trigger word</label>
                  <input style={input} value={nTrigger} onChange={(e) => setNTrigger(e.target.value)}
                         placeholder="auto (rare token)" />
                </div>
                <div>
                  <label style={label}>Class</label>
                  <input style={input} value={nClass} onChange={(e) => setNClass(e.target.value)}
                         placeholder="man / woman / person" />
                </div>
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 6 }}>
                <div>
                  <label style={label}>Target model</label>
                  <select style={input} value={nTarget} onChange={(e) => setNTarget(e.target.value)}>
                    <option value="krea2">Krea 2</option>
                    <option value="flux">FLUX.1</option>
                    <option value="sdxl">SDXL</option>
                    <option value="other">other</option>
                  </select>
                </div>
                <div>
                  <label style={label}>
                    Images ({wardrobe.length && autoSize ? `${sizedCount} — sized from the wardrobe` : nCount})
                  </label>
                  <input type="range" min={16} max={120} step={4} value={effCount}
                         style={{ width: '100%' }} disabled={!!wardrobe.length && autoSize}
                         onChange={(e) => setNCount(Number(e.target.value))} />
                </div>
              </div>
              <div>
                <label style={label}>Shot mix</label>
                <select style={input} value={nPreset}
                        onChange={(e) => setNPreset(e.target.value as 'balanced' | 'face_heavy')}>
                  <option value="balanced">balanced — 20/20/30/30 face·bust·waist-up·full body</option>
                  <option value="face_heavy">face-heavy — ~45/25/15/15, best for likeness</option>
                </select>
                <p style={{ ...hint, margin: '3px 0 0' }}>
                  Face-heavy mirrors the ratio the dedicated dataset tools aim at (roughly
                  12 face / 6 bust / 6 body / 1 back). More face data buys likeness; fewer body
                  shots costs some full-body flexibility. Worth running one of each.
                </p>
              </div>
              <div>
                <label style={label}>Identity source</label>
                <select style={input} value={nBaseMode}
                        onChange={(e) => setNBaseMode(e.target.value as '' | 'dressed' | 'stripped' | 'auto')}>
                  <option value="">use the character's setting</option>
                  <option value="dressed">🧥 dressed — his own clothes from your references</option>
                  <option value="stripped">👙 stripped — the stripped base set</option>
                  <option value="auto">🤖 auto — newest version of each view</option>
                </select>
                <p style={{ ...hint, margin: '3px 0 0' }}>
                  Dressed skips the strip step entirely, which is one less edit per view and one
                  less source of drift. Set the default per character in Klein 3.0.
                </p>
              </div>

              <div style={{ border: '1px solid #2a2f3a', borderRadius: 8, padding: 10, display: 'grid', gap: 8 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                  <b style={{ fontSize: 13, color: '#e6e9ee' }}>👔 Wardrobe</b>
                  <div style={{ flex: 1 }} />
                  <button style={btnSm} onClick={() => addOutfit('named')}>+ named</button>
                  <button style={btnSm} onClick={() => addOutfit('variety')}>+ variety</button>
                  <button style={btnSm} disabled={!nChar || wbBusy === 'suggest'}
                          onClick={() => void suggestWardrobe()}>
                    {wbBusy === 'suggest' ? '⏳ reading…' : '🎨 Suggest variety'}
                  </button>
                </div>
                <p style={{ ...hint, margin: 0 }}>
                  One outfit across the whole set gets absorbed into the trigger word — the
                  clothes become part of the character. <b>Named</b> outfits are your story
                  wardrobe; <b>variety</b> outfits exist to prove clothing is independent of
                  him, which is what keeps it controllable. Leave this empty to use whatever
                  his base images already wear.
                </p>

                {wardrobe.map((o, i) => (
                  <div key={i} style={{ display: 'grid', gap: 4, borderTop: '1px solid #222831', paddingTop: 6 }}>
                    <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
                      <select style={{ ...input, width: 92 }} value={o.kind}
                              onChange={(e) => setOutfit(i, { kind: e.target.value as 'named' | 'variety' })}>
                        <option value="named">named</option>
                        <option value="variety">variety</option>
                      </select>
                      <input style={{ ...input, width: 140 }} value={o.name}
                             placeholder="Ranger kit"
                             onChange={(e) => setOutfit(i, { name: e.target.value })} />
                      <select style={{ ...input, width: 150 }} value={o.ref_id || ''}
                              disabled={wbBusy === `ref${i}`}
                              onChange={(e) => void nameGarment(i, e.target.value)}>
                        <option value="">no reference image</option>
                        {charRefs.map((r) => (
                          <option key={r.id} value={r.id}>
                            {r.tag === 'outfit' ? '👔 ' : ''}{r.tag}: {r.name.slice(0, 16)}
                          </option>
                        ))}
                      </select>
                      <div style={{ flex: 1 }} />
                      <button style={{ ...btnSm, color: '#ff8a8a' }} onClick={() => delOutfit(i)}>🗑</button>
                    </div>
                    <input style={input} value={o.desc}
                           placeholder="NAME each garment and colour — a brown leather jacket, a green flannel shirt and dark jeans"
                           onChange={(e) => setOutfit(i, { desc: e.target.value })} />
                    {wbBusy === `ref${i}` && <span style={hint}>reading the garments…</span>}
                  </div>
                ))}

                {wardrobe.length > 0 && (
                  <div style={{ borderTop: '1px solid #222831', paddingTop: 6, display: 'grid', gap: 3 }}>
                    <label style={{ ...label, display: 'flex', alignItems: 'center', gap: 6 }}>
                      <input type="checkbox" checked={autoSize}
                             onChange={(e) => setAutoSize(e.target.checked)} />
                      size the set from the wardrobe ({sizedCount} images)
                    </label>
                    <span style={{ ...hint, color: perOutfit < 8 ? '#e0b36a' : '#8d97a5' }}>
                      {namedN} named · {varietyN} variety → {effCount} images, about {perOutfit} each
                      {perOutfit < 8 && ' — measured, below ~8 each some outfits only appear in 2 of the 4 shot types, which trains "that outfit means that shot"'}
                    </span>
                    <span style={hint}>
                      Named outfits take 60% of the images between them, variety the other 40%.
                      A face close-up never names an outfit; a head-and-shoulders names only the
                      first garment.
                    </span>
                  </div>
                )}
              </div>
              <button style={btn} disabled={busy || !nName.trim() || !nChar} onClick={createDs}>
                {busy ? 'Planning…' : '📋 Plan dataset'}
              </button>
              <p style={{ ...hint, margin: 0 }}>
                Planning costs nothing — it only writes the shot list. 30–100 images is the
                researched range; 40 is a good first pass.
              </p>
            </div>
          </div>

          <div style={box}>
            <b style={{ fontSize: 13, color: '#e6e9ee' }}>📚 Datasets ({list.length})</b>
            <div style={{ display: 'grid', gap: 6, marginTop: 8 }}>
              {list.map((d) => (
                <div key={d.id}
                     style={{ border: `1px solid ${ds?.id === d.id ? '#3b82f6' : '#2a2f3a'}`,
                              borderRadius: 8, padding: 8, cursor: 'pointer' }}
                     onClick={() => void openDs(d.id)}>
                  <div style={{ fontSize: 13, color: '#e6e9ee' }}>{d.name}</div>
                  <div style={hint}>
                    {d.char_name || d.char_slug} · <code>{d.trigger}</code> · {d.target}
                  </div>
                  <div style={hint}>
                    {d.rendered}/{d.total} rendered · {d.captioned} captioned
                    {d.flagged ? ` · ⚠ ${d.flagged} flagged` : ''}
                    {d.exports.length ? ` · 📦 ${d.exports.length}` : ''}
                  </div>
                </div>
              ))}
              {!list.length && <p style={hint}>No datasets yet.</p>}
            </div>
          </div>
        </div>

        {/* ── right: the open dataset ──────────────────────────────────── */}
        <div style={{ display: 'grid', gap: 14, minWidth: 0 }}>
          {!ds ? (
            <div style={box}>
              <p style={hint}>
                Pick a dataset on the left, or plan a new one. The plan spreads shots across
                framing (face / head+shoulders / waist-up / full body), all six angles,
                expressions, poses, lighting and backgrounds — variety is what makes a LoRA
                flexible instead of locked to one look.
              </p>
              {recipe && (
                <p style={{ ...hint, marginTop: 8 }}>
                  Vocabulary: {recipe.framings.length} framings · {recipe.angles.length} angles ·
                  {' '}{recipe.expressions.length} expressions · {recipe.poses.length} poses ·
                  {' '}{recipe.lighting.length} lighting setups · {recipe.backgrounds.length} backgrounds.
                </p>
              )}
            </div>
          ) : (
            <>
              <div style={box}>
                <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
                  <b style={{ fontSize: 14, color: '#e6e9ee' }}>{ds.name}</b>
                  <span style={hint}>
                    {ds.char_name} · trigger <code style={{ color: '#9cc2ff' }}>{ds.trigger} {ds.class_token}</code> · {ds.target}
                  </span>
                  <div style={{ flex: 1 }} />
                  <span style={hint}>
                    {counts.rendered}/{counts.total} rendered · {counts.captioned} captioned ·
                    {' '}{counts.checked} checked{counts.flagged ? ` · ⚠ ${counts.flagged} flagged` : ''}
                  </span>
                </div>
                <div style={{ display: 'flex', gap: 6, marginTop: 10, flexWrap: 'wrap' }}>
                  <button style={btn} disabled={run?.status === 'running'}
                          onClick={() => void post(`/datasets/${ds.id}/generate`, {}, '🎨 rendering…')}>
                    🎨 Render missing ({counts.total - counts.rendered})
                  </button>
                  <button style={btnGhost} disabled={run?.status === 'running'}
                          title="Compose captions from the plan — consistent wording across the set"
                          onClick={() => void post(`/datasets/${ds.id}/caption`, { overwrite: true }, '✓ captions written')}>
                    {acting.endsWith('/caption') ? '⏳ captioning…' : '🗒 Caption all'}
                  </button>
                  <button style={btnGhost} disabled={run?.status === 'running' || !health?.vision?.model}
                          title="Ask the vision model what each image actually shows (clothing + background) and fold it into the captions"
                          onClick={() => void post(`/datasets/${ds.id}/caption`, { overwrite: true, enrich: true }, '✓ captions enriched from the images')}>
                    🔍 Caption + vision detail
                  </button>
                  <button style={btnGhost}
                          disabled={!!acting || run?.status === 'running' || !health?.vision?.model}
                          title="Vision QC: framing, angle, expression, one person, artifacts, bad crops.
Re-checks every rendered image — before v1.226 this skipped anything already checked, which on a
finished set meant it silently did nothing."
                          onClick={() => void post(`/datasets/${ds.id}/qc`, { overwrite: true },
                                                   '🔬 QC started — progress below')}>
                    {acting.endsWith('/qc') ? '⏳ starting QC…' : '🔬 QC pass'}
                  </button>
                  <button style={btnGhost}
                          disabled={!!acting || run?.status === 'running'}
                          title="ArcFace likeness only — CPU, no vision model, no worker, no GPU"
                          onClick={() => void post(`/datasets/${ds.id}/likeness`, {},
                                                   '🧬 likeness scored')}>
                    {acting.endsWith('/likeness') ? '⏳ scoring…' : '🧬 Likeness'}
                  </button>
                  <div style={{ flex: 1 }} />
                  <button style={btnSm} onClick={() => void refreshDs()}>↻</button>
                  <button style={{ ...btnSm, color: '#ff8a8a' }}
                          onClick={async () => {
                            if (!window.confirm(`Delete dataset "${ds.name}" and all its images?`)) return;
                            await post(`/datasets/${ds.id}/delete`);
                            setDs(null); await load();
                          }}>🗑</button>
                </div>
                {counts.flagged > 0 && (
                  <div style={{ marginTop: 10, borderTop: '1px solid #2a2f3a', paddingTop: 10,
                                display: 'grid', gap: 6 }}>
                    <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
                      <b style={{ fontSize: 12, color: '#e0b36a' }}>⚠ {counts.flagged} flagged</b>
                      {!!ds.flags?.identity_off && (
                        <span style={{ ...errTxt }} title="The vision model compared each render against his reference: these are a different person (usually a different build)">
                          🚫 {ds.flags.identity_off} off-identity
                        </span>
                      )}
                      {ds.flags && (
                        <span style={hint}>
                          {[
                            ds.flags.artifacts ? `${ds.flags.artifacts} artifacts` : '',
                            ds.flags.angle_off ? `${ds.flags.angle_off} wrong angle` : '',
                            ds.flags.framing_off ? `${ds.flags.framing_off} wrong shot type` : '',
                            ds.flags.expression_off ? `${ds.flags.expression_off} wrong expression` : '',
                            ds.flags.not_one_person ? `${ds.flags.not_one_person} not one person` : '',
                            ds.flags.identity_off ? `${ds.flags.identity_off} not him` : '',
                          ].filter(Boolean).join(' · ')}
                        </span>
                      )}
                    </div>
                    {/* v1.241: a summary with nothing in it must not read as a
                        clean dataset. What is measured, and what is not, said
                        out loud — framing and crop have no instrument yet and
                        the vision model was 0 for 12 on them. */}
                    {!!ds.flags && (
                      <span style={hint}>
                        measured: identity (ArcFace{ds.flags.arcface_scored ? ` ×${ds.flags.arcface_scored}` : ''})
                        · angle (head yaw{typeof ds.flags.angle_measured === 'number'
                          ? ` ×${ds.flags.angle_measured}${ds.flags.angle_unmeasured
                            ? `, ${ds.flags.angle_unmeasured} unmeasurable` : ''}` : ''})
                        · shot type (face height{typeof ds.flags.framing_measured === 'number'
                          ? ` ×${ds.flags.framing_measured}${ds.flags.framing_unmeasured
                            ? `, ${ds.flags.framing_unmeasured} back rows n/a` : ''}` : ''})
                        · one person · artifacts
                        {!!ds.flags.not_checked?.length && (
                          <b style={{ color: '#e0b36a' }}>
                            {'  ·  NOT checked: ' + ds.flags.not_checked.join(', ')}
                          </b>
                        )}
                        {!!ds.flags.unreliable?.length && (
                          <span style={{ color: '#8d97a5' }}>
                            {'  ·  unreliable: ' + ds.flags.unreliable.join(', ')}
                          </span>
                        )}
                      </span>
                    )}
                    {!!Object.keys(ds.flags?.top_issues || {}).length && (
                      <span style={hint}>
                        most common: {Object.entries(ds.flags?.top_issues || {})
                          .map(([k, n]) => `${k} ×${n}`).join('  ·  ')}
                      </span>
                    )}
                    <div style={{ display: 'flex', gap: 6, alignItems: 'center', flexWrap: 'wrap' }}>
                      <button style={btnGhost} disabled={run?.status === 'running'}
                              title="Re-render every flagged image once, with fresh seeds"
                              onClick={() => void post(`/datasets/${ds.id}/repair`,
                                                       { rounds: 1, qc_after: false, include_stuck: retryStuck },
                                                       '🔁 re-rendering the flagged images…')}>
                        🔁 Re-render flagged ({counts.flagged})
                      </button>
                      <button style={btn} disabled={run?.status === 'running' || !health?.vision?.model}
                              title="Loop: re-render the flagged images, re-check them, repeat until nothing is flagged or the round cap is reached"
                              onClick={() => void post(`/datasets/${ds.id}/repair`,
                                                       { rounds, qc_after: true, include_stuck: retryStuck },
                                                       '♻️ repairing until clean…')}>
                        ♻️ Repair until clean
                      </button>
                      <span style={hint}>max</span>
                      <select style={{ ...input, width: 'auto', fontSize: 12, padding: '4px 6px' }}
                              value={String(rounds)} onChange={(e) => setRounds(Number(e.target.value))}>
                        {[1, 2, 3, 4, 5, 6].map((n) => <option key={n} value={n}>{n} round{n > 1 ? 's' : ''}</option>)}
                      </select>
                      <label style={{ ...hint, display: 'inline-flex', gap: 4, alignItems: 'center', cursor: 'pointer' }}
                             title={`An image that failed ${ds.max_attempts || 3} renders is parked as stuck — tick this to try it again anyway`}>
                        <input type="checkbox" checked={retryStuck} onChange={(e) => setRetryStuck(e.target.checked)} />
                        retry stuck
                      </label>
                    </div>
                    {!!ds.flags?.stuck && (
                      <span style={{ ...hint, color: '#e0b36a' }}>
                        {ds.flags.stuck} image(s) hit the {ds.max_attempts || 3}-render limit — that
                        usually means the plan row itself is hard (a face crop asked for a full-body
                        pose, say). Edit or delete those rather than re-rolling them.
                      </span>
                    )}
                  </div>
                )}
                {/* v1.226: the reply used to render only at the top of the page,
                    which on a scrolled view is invisible. */}
                {(msg || err) && (
                  <p style={{ ...(err ? errTxt : okTxt), margin: '8px 0 0' }}>
                    {/* a message about something IN FLIGHT must look in-flight, or
                        it reads as a result once the banner disappears */}
                    {!err && live ? `${['|', '/', '-', '\\'][beat]} ` : ''}{err || msg}
                  </p>
                )}
                {!!lastRun && !msg && !err && run?.status !== 'running' && (
                  <p style={{ ...hint, margin: '8px 0 0' }}>{lastRun}</p>
                )}
                {!!acting && run?.status !== 'running' && (
                  <p style={{ ...hint, margin: '8px 0 0' }}>⏳ sending request…</p>
                )}
                {stale > 1 && (
                  <p style={{ ...errTxt, margin: '8px 0 0' }}>
                    ⚠ lost contact with the backend ({stale} failed refreshes) — is run.bat still
                    up? Whatever was running may still be going.
                  </p>
                )}
                {run?.status === 'running' && (
                  <p style={{ ...hint, margin: '8px 0 0' }}>
                    {['|', '/', '-', '\\'][beat]}{' '}
                    ⏳ {run.kind}{run.round ? ` round ${run.round}/${run.rounds} · ${run.phase}` : ''}{' '}
                    {run.detail || 'starting…'}
                    {run.total ? ` (${run.done || 0}/${run.total})` : ''}
                    {run.tasks ? '  ·  ' + Object.entries(run.tasks)
                      .filter(([, t]) => t.status === 'running')
                      .map(([k, t]) => `#${k} @ ${t.worker || t.server || '…'}`).join('  ·  ') : ''}
                  </p>
                )}
                {!!run?.history?.length && (
                  <p style={{ ...hint, margin: '4px 0 0' }}>
                    {run.history.map((h) => `round ${h.round}: ${h.rendered} re-rendered${h.flagged === null ? '' : ` → ${h.flagged} flagged`}`).join('  ·  ')}
                  </p>
                )}
                {run && run.status !== 'running' && run.error && <p style={errTxt}>{run.error}</p>}
              </div>

              {/* export */}
              <div style={box}>
                <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
                  <b style={{ fontSize: 13, color: '#e6e9ee' }}>📦 Export</b>
                  <span style={hint}>
                    images + <code>.txt</code> captions + ai-toolkit yaml + kohya toml + manifest + notes
                  </span>
                  <div style={{ flex: 1 }} />
                  <button style={btnGhost} disabled={!counts.rendered}
                          onClick={() => void post(`/datasets/${ds.id}/export`, { trigger_mode: 'literal' }, '✓ zip built')}>
                    📦 Build zip
                  </button>
                  <button style={btnSm} disabled={!counts.rendered}
                          title="ai-toolkit substitutes its configured trigger_word for [trigger] at train time"
                          onClick={() => void post(`/datasets/${ds.id}/export`, { trigger_mode: 'placeholder' }, '✓ zip built with [trigger]')}>
                    📦 …with [trigger]
                  </button>
                </div>
                {!!(ds.exports || []).length && (
                  <div style={{ display: 'grid', gap: 4, marginTop: 8 }}>
                    {(ds.exports || []).map((f) => (
                      <a key={f} href={`${BASE}/datasets/${ds.id}/exports/${f}`}
                         style={{ ...hint, color: '#9cc2ff' }} download>⬇ {f}</a>
                    ))}
                  </div>
                )}
                {counts.flagged > 0 && (
                  <p style={{ ...hint, margin: '6px 0 0', color: '#e0b36a' }}>
                    ⚠ {counts.flagged} QC-flagged image(s) are held back from the zip — fix or
                    regenerate them, or delete them from the set.
                  </p>
                )}
              </div>

              {/* train on worker (v1.271) */}
              <TrainBox dsId={ds.id} />

              {/* gallery */}
              <div style={box}>
                <div style={{ display: 'flex', gap: 6, alignItems: 'center', flexWrap: 'wrap' }}>
                  <b style={{ fontSize: 13, color: '#e6e9ee' }}>🖼 Images &amp; captions</b>
                  <div style={{ flex: 1 }} />
                  {(['all', 'missing', 'flagged', 'angle', 'nocap'] as const).map((f) => (
                    <button key={f} style={chip(filter === f)} onClick={() => setFilter(f)}>
                      {f === 'all' ? `all (${counts.total})`
                        : f === 'missing' ? `not rendered (${counts.total - counts.rendered})`
                          : f === 'flagged' ? `flagged (${counts.flagged})`
                            : f === 'angle' ? `wrong angle (${(ds?.items || []).filter((i) => i.qc?.angle_ok === false).length})`
                              : `no caption (${counts.total - counts.captioned})`}
                    </button>
                  ))}
                </div>

                <div style={{ display: 'flex', gap: 6, alignItems: 'center', flexWrap: 'wrap', marginTop: 8 }}>
                  <span style={hint}>shot type</span>
                  <button style={chip(!fFraming)} onClick={() => setFFraming('')}>any</button>
                  {byFraming.map((r) => (
                    <button key={r.key} style={chip(fFraming === r.key)}
                            title={`${r.rendered}/${r.n} rendered · ${r.flagged} flagged · ${r.angleOff} wrong angle`}
                            onClick={() => setFFraming(fFraming === r.key ? '' : r.key)}>
                      {r.key} ({r.n})
                      {r.angleOff ? <span style={{ color: '#e0b36a' }}> 📐{r.angleOff}</span> : null}
                      {r.flagged ? <span style={{ color: '#ff8a8a' }}> ⚠{r.flagged}</span> : null}
                    </button>
                  ))}
                  <span style={{ ...hint, marginLeft: 8 }}>angle</span>
                  <button style={chip(!fAngle)} onClick={() => setFAngle('')}>any</button>
                  {Array.from(new Set((ds?.items || []).map((i) => i.angle))).map((a) => {
                    const g = (ds?.items || []).filter((i) => i.angle === a);
                    const miss = g.filter((i) => i.qc && i.qc.angle_ok === false).length;
                    return (
                      <button key={a} style={chip(fAngle === a)}
                              title={`${g.length} planned · ${miss} wrong angle`}
                              onClick={() => setFAngle(fAngle === a ? '' : a)}>
                        {a.replace('three_quarter', '3/4').replace('profile', 'prof')} ({g.length})
                        {miss ? <span style={{ color: '#ff8a8a' }}> ⚠{miss}</span> : null}
                      </button>
                    );
                  })}
                </div>

                {/* v1.229: act on the current selection. Before this every bulk
                    button hit the whole dataset, so "re-render the full-body
                    rows" had no path through the UI. */}
                {(fFraming || fAngle || filter !== 'all') && (
                  <div style={{ display: 'flex', gap: 6, alignItems: 'center', flexWrap: 'wrap',
                                marginTop: 8, borderTop: '1px solid #222831', paddingTop: 8 }}>
                    <b style={{ fontSize: 12, color: '#e0b36a' }}>{shown.length} shown</b>
                    <button style={btnSm} disabled={!!acting || run?.status === 'running' || !shown.length}
                            title="Re-render exactly these images. Everything else is left alone."
                            onClick={() => void post(`/datasets/${ds.id}/generate`,
                                                     { item_ids: shownIds, overwrite: true },
                                                     `🎨 re-rendering ${shown.length}`)}>
                      🎨 Re-render these ({shown.length})
                    </button>
                    <button style={btnSm}
                            disabled={!!acting || run?.status === 'running' || !shown.length || !health?.vision?.model}
                            onClick={() => void post(`/datasets/${ds.id}/qc`,
                                                     { item_ids: shownIds, overwrite: true },
                                                     `🔬 checking ${shown.length}`)}>
                      🔬 QC these ({shown.length})
                    </button>
                    <button style={btnSm} disabled={!!acting || run?.status === 'running' || !shown.length}
                            onClick={() => void post(`/datasets/${ds.id}/caption`,
                                                     { item_ids: shownIds, overwrite: true },
                                                     `🗒 captioned ${shown.length}`)}>
                      🗒 Caption these ({shown.length})
                    </button>
                    <button style={btnSm} onClick={() => { setFFraming(''); setFAngle(''); setFilter('all'); }}>
                      clear filters
                    </button>
                  </div>
                )}
                <div style={{ display: 'grid', gap: 8, marginTop: 10 }}>
                  {shown.map((it) => (
                    <div key={it.id}
                         style={{ display: 'flex', gap: 10, border: `1px solid ${it.qc?.ok === false ? '#7f1d1d' : '#2a2f3a'}`,
                                  borderRadius: 8, padding: 8 }}>
                      <div style={{ width: 96, flexShrink: 0 }}>
                        {it.has_image ? (
                          <img src={`${it.url}?t=${it.status}`} alt={it.id}
                               style={{ width: '100%', borderRadius: 6, cursor: 'zoom-in' }}
                               onClick={() => setLightbox(it.url)} />
                        ) : (
                          <div style={{ width: '100%', aspectRatio: `${it.width}/${it.height}`,
                                        border: '1px dashed #3a4150', borderRadius: 6, display: 'flex',
                                        alignItems: 'center', justifyContent: 'center', fontSize: 10,
                                        color: '#8d97a5' }}>planned</div>
                        )}
                      </div>
                      <div style={{ flex: 1, minWidth: 0, display: 'grid', gap: 4 }}>
                        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center' }}>
                          <b style={{ fontSize: 12, color: '#e6e9ee' }}>#{it.id}</b>
                          <span style={hint}>
                            {it.framing} · {it.angle} · {it.expression}
                            {it.pose ? ` · ${it.pose}` : ''} · {it.lighting} · {it.background}
                          </span>
                          {it.identity && <span style={hint}>🧭 {it.identity}</span>}
                          {!!it.attempts && it.attempts > 1 && (
                            <span style={{ ...hint, color: (it.attempts >= (ds.max_attempts || 3)) ? '#e0b36a' : '#8d97a5' }}
                                  title="how many times this row has been rendered">
                              ×{it.attempts}
                            </span>
                          )}
                          <div style={{ flex: 1 }} />
                          {/* v1.241: the measured angle, on the image. It has
                              existed since v1.234 and lived only in a script. */}
                          {it.qc?.angle_method && (
                            <span style={it.qc.angle_ok === false ? errTxt
                              : it.qc.angle_method === 'unmeasured' ? hint : okTxt}
                                  title={it.qc.angle_note || ''}>
                              {it.qc.angle_method === 'unmeasured'
                                ? '📐 not measurable'
                                : `📐 ${(it.qc.yaw as number) > 0 ? '+' : ''}${it.qc.yaw}°`}
                            </span>
                          )}
                          {/* v1.242: the measured shot type. A face crop with no
                              face, or a face sitting at the bottom of the frame,
                              is now visible here instead of passing silently. */}
                          {it.qc?.framing_method === 'face-height' && (
                            <span style={it.qc.framing_ok === false ? errTxt : okTxt}
                                  title={it.qc.framing_note || ''}>
                              {`🖼 ${((it.qc.face_h_ratio || 0) * 100).toFixed(0)}%`}
                            </span>
                          )}
                          {it.qc && (
                            <span style={it.qc.ok ? okTxt : errTxt}
                                  title={(it.qc.issues || []).join(' · ') || 'checked'}>
                              {it.qc.ok ? '✓ QC' : `⚠ ${(it.qc.issues || ['flagged'])[0]}`}
                            </span>
                          )}
                        </div>
                        {editId === it.id ? (
                          <>
                            <textarea style={{ ...input, minHeight: 54, fontSize: 12 }} value={editCap}
                                      onChange={(e) => setEditCap(e.target.value)} />
                            <div style={{ display: 'flex', gap: 6 }}>
                              <button style={btnSm}
                                      onClick={async () => {
                                        await post(`/datasets/${ds.id}/items/${it.id}/update`, { caption: editCap });
                                        setEditId('');
                                      }}>💾 Save caption</button>
                              <button style={btnSm} onClick={() => setEditId('')}>cancel</button>
                            </div>
                          </>
                        ) : (
                          <div style={{ fontSize: 12, color: (it.caption || '').trim() ? '#cbd2dc' : '#5c6472' }}>
                            {(it.caption || '').trim() || 'no caption yet'}
                          </div>
                        )}
                        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                          <button style={btnSm} onClick={() => { setEditId(it.id); setEditCap(it.caption || ''); }}>
                            ✏️ caption
                          </button>
                          <button style={btnSm} disabled={run?.status === 'running'}
                                  onClick={() => void post(`/datasets/${ds.id}/generate`,
                                                           { item_ids: [it.id], overwrite: true }, '🎨 re-rendering…')}>
                            🔁 re-render
                          </button>
                          <button style={btnSm} disabled={run?.status === 'running' || !it.has_image || !health?.vision?.model}
                                  onClick={() => void post(`/datasets/${ds.id}/qc`,
                                                           { item_ids: [it.id], overwrite: true }, '🔬 checking…')}>
                            🔬 check
                          </button>
                          <div style={{ flex: 1 }} />
                          <button style={{ ...btnSm, color: '#ff8a8a' }}
                                  onClick={() => void post(`/datasets/${ds.id}/items/${it.id}/delete`, {}, '✓ removed')}>
                            🗑
                          </button>
                        </div>
                      </div>
                    </div>
                  ))}
                  {!shown.length && <p style={hint}>Nothing in this filter.</p>}
                </div>
              </div>
            </>
          )}
        </div>
      </div>

      {lightbox && (
        <div onClick={() => setLightbox('')}
             style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,.85)', zIndex: 60,
                      display: 'flex', alignItems: 'center', justifyContent: 'center', cursor: 'zoom-out' }}>
          <img src={lightbox} alt="" style={{ maxWidth: '92vw', maxHeight: '92vh', borderRadius: 8 }} />
        </div>
      )}
    </div>
  );
}


/* ── v1.271: in-app training + autogen ──────────────────────────────────── */
function TrainBox({ dsId }: { dsId: string }): React.ReactElement {
  const [st, setSt] = useState<Record<string, unknown>>({});
  const [msg, setMsg] = useState('');
  useEffect(() => {
    const load = async () => {
      try {
        const r = await fetch(`${BASE}/datasets/${dsId}/train/status`);
        if (r.ok) setSt(await r.json());
      } catch { /* ignore */ }
    };
    void load();
    const t = window.setInterval(load, 15000);
    return () => window.clearInterval(t);
  }, [dsId]);
  const start = async () => {
    setMsg('');
    try {
      const r = await fetch(`${BASE}/datasets/${dsId}/train`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}',
      });
      if (!r.ok) { setMsg((await r.json()).detail || `${r.status}`); return; }
      setMsg('started — export → upload → train (hours) → score → install, all automatic');
    } catch (e) { setMsg(String((e as Error).message || e)); }
  };
  const stage = String(st.stage || 'idle');
  return (
    <div style={box}>
      <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
        <b style={{ fontSize: 13, color: '#e6e9ee' }}>🚀 Train LoRA on worker</b>
        <span style={hint}>
          export → upload → Fizgig → ArcFace checkpoint pick → install into ComfyUI —
          the finished LoRA appears in 🧬 Text 2 Image's Krea 2 picker.
        </span>
        <div style={{ flex: 1 }} />
        <button style={btnGhost} disabled={stage !== 'idle' && Boolean(st.active)}
                onClick={() => void start()}>
          {st.active ? '⏳ running…' : '🚀 Train'}
        </button>
      </div>
      {stage !== 'idle' && (
        <p style={{ ...hint, margin: '6px 0 0',
                    color: stage === 'error' ? '#ff8a8a' : stage === 'done' ? '#5ee08a' : '#9cc2ff' }}>
          {stage}: {String(st.detail || '')}
          {typeof st.installed === 'string' && st.installed ? ` → ${st.installed}` : ''}
        </p>
      )}
      {msg && <p style={{ ...hint, margin: '4px 0 0' }}>{msg}</p>}
    </div>
  );
}

/** "2m 14s" from a backend timestamp.
 *
 *  ⚠ The backend writes `time.strftime("%Y-%m-%dT%H:%M:%S")` — LOCAL time with
 *  NO timezone suffix. `new Date()` on a bare string like that treats it as
 *  local, which is right here and would be an hours-off lie if the backend
 *  ever started writing UTC. If a clock reads absurdly, this is why. */
function elapsedSince(ts: string): string {
  const t0 = new Date(ts).getTime();
  if (!Number.isFinite(t0)) return '';
  const s = Math.max(0, (Date.now() - t0) / 1000);
  if (s < 60) return `${Math.round(s)}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m ${Math.round(s % 60)}s`;
  return `${Math.floor(s / 3600)}h ${Math.round((s % 3600) / 60)}m`;
}

function AutogenBox({ chars }: { chars: CharT[] }): React.ReactElement {
  const [slug, setSlug] = useState('');
  const [mode, setMode] = useState<'dominant' | 'flexible'>('dominant');
  const [dsOnly, setDsOnly] = useState(false);
  const [st, setSt] = useState<Record<string, unknown>>({});
  const [msg, setMsg] = useState('');
  // v1.276.41 — the button had NO busy state, so the whole gap between the
  // click and the first status tick looked like nothing happening. It was
  // worse than that: the request was deadlocking the server for 60s.
  const [busy, setBusy] = useState(false);
  const [tick, setTick] = useState(0);        // forces an immediate re-poll
  useEffect(() => {
    if (!slug) { setSt({}); return; }
    let stop = false;
    const load = async () => {
      try {
        const r = await fetch(`${BASE}/autogen/${slug}/status`);
        if (r.ok && !stop) setSt(await r.json());
      } catch { /* ignore — a poll failure is not worth a message */ }
    };
    void load();
    // 15s was too slow to feel connected to a button press. 4s while it is
    // running, 15s when it is idle — the poll is a tiny local read.
    const t = window.setInterval(load, 4000);
    return () => { stop = true; window.clearInterval(t); };
  }, [slug, tick]);

  /** Read an error body that may not be JSON.
   *
   *  ⚠ This is the bug Lorenzo saw as `Unexpected token 'I', "Internal S"...
   *  is not valid JSON`. The old code called `r.json()` on a FAILED response;
   *  an unhandled server exception returns the plain text `Internal Server
   *  Error`, so the error path threw its own error and buried the real one. */
  const errText = async (r: Response): Promise<string> => {
    const raw = await r.text().catch(() => '');
    try {
      const j = JSON.parse(raw);
      return String(j?.detail || raw || `HTTP ${r.status}`);
    } catch {
      return `HTTP ${r.status} — ${(raw || 'no response body').slice(0, 200)}`;
    }
  };

  const go = async () => {
    if (!slug || busy) return;
    setMsg('⏳ starting…');
    setBusy(true);
    try {
      const r = await fetch(`${BASE}/autogen`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ char_slug: slug, outfit_mode: mode, dataset_only: dsOnly }),
      });
      if (!r.ok) { setMsg(`❌ ${await errText(r)}`); return; }
      setMsg('⚡ running — views → dataset → render → QC → fix rounds → export' +
             (dsOnly ? '' : ' → train → install') + '. Walk away; status updates here.');
      setTick((n) => n + 1);            // show a stage now, not in 15 seconds
    } catch (e) {
      setMsg(`❌ ${String((e as Error).message || e)}`);
    } finally {
      setBusy(false);
    }
  };
  const stage = String(st.stage || '');
  return (
    <div style={{ ...box, border: '1px solid #3b82f6' }}>
      <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
        <b style={{ fontSize: 13, color: '#e6e9ee' }}>⚡ Autogen — one button, whole recipe</b>
        <span style={hint}>
          From a character with a front reference: missing views → face_heavy-40 dataset →
          QC + auto-fix rounds → export → train → installed LoRA.
        </span>
      </div>
      <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap', marginTop: 8 }}>
        <select style={{ ...input, width: 220 }} value={slug} onChange={(e) => setSlug(e.target.value)}>
          <option value="">— pick character —</option>
          {chars.map((c) => <option key={c.slug} value={c.slug}>{c.name}</option>)}
        </select>
        <button style={chip(mode === 'dominant')} onClick={() => setMode('dominant')}
                title="Their base outfit appears throughout — it stays strongly tied to the character.">
          👕 Signature outfit
        </button>
        <button style={chip(mode === 'flexible')} onClick={() => setMode('flexible')}
                title="The vision model proposes outfit variations that get mixed into the set, so wardrobe stays promptable rather than baked in.">
          👗 Wardrobe variations
        </button>
        <button style={chip(dsOnly)} onClick={() => setDsOnly(!dsOnly)}
                title="Stop after the dataset — review it, then hit 🚀 Train yourself.">
          {dsOnly ? '☑' : '☐'} dataset only
        </button>
        <div style={{ flex: 1 }} />
        <button style={btn} disabled={!slug || busy || Boolean(st.active)} onClick={() => void go()}>
          {busy ? '⏳ starting…' : st.active ? '⏳ running…' : '⚡ Autogen'}
        </button>
      </div>
      {stage && (
        <p style={{ ...hint, margin: '6px 0 0',
                    color: stage === 'error' ? '#ff8a8a' : stage === 'done' ? '#5ee08a' : '#9cc2ff' }}>
          {stage}: {String(st.detail || '')}
          {typeof st.dataset === 'string' && st.dataset ? ` (dataset ${st.dataset})` : ''}
          {/* ⏱ how long it has been going. `started_at` is written by the
              route BEFORE the thread starts, so this is honest even for a run
              that has not reported a stage yet — and it survives a page
              reload, which a client-side stopwatch would not. */}
          {typeof st.started_at === 'string' && st.started_at ? (
            <span style={{ color: '#8d97a5' }}>
              {'  ⏱ '}{elapsedSince(String(st.started_at))}
            </span>
          ) : null}
          {Boolean(st.active) && stage !== 'error' && stage !== 'done' ? ' …' : ''}
        </p>
      )}
      {/* A running pipeline with nothing written yet is still running. Saying
          so beats an empty box, which is what "no indication anything is
          happening" actually looked like. */}
      {!stage && Boolean(st.active) && (
        <p style={{ ...hint, margin: '6px 0 0', color: '#9cc2ff' }}>
          running — waiting for the first stage to report…
        </p>
      )}
      {msg && <p style={{ ...hint, margin: '4px 0 0' }}>{msg}</p>}
    </div>
  );
}
