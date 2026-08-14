/**
 * 🪪 Character Sheet — the sixth mode (v1.268.0).
 *
 * Pick a Klein 3.0 character and build a single downloadable reference sheet
 * (turnaround + face row) for models that take a character sheet as reference
 * (MiniMax H3 and friends). Composited on the backend from the character's
 * identity-scored LoRA dataset renders, tagged refs and active base — no GPU,
 * no worker, no extra LoRA needed.
 *
 * v1.277.2 — 🧥 per-OUTFIT sheets: build a sheet from one outfit's five
 * rendered views (never the dataset), so the character can be referenced in a
 * specific attire. The "Previous sheets" grid is the sheet LIBRARY: every
 * generated sheet is stored, labelled, downloadable, usable as a reference.
 */
import React, { useCallback, useEffect, useState } from 'react';

import useLightbox from '../shared/useLightbox';
const BASE = '/api/charsheet';

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

interface CharT { slug: string; name: string; ref_count: number; has_base: boolean; dataset_images: number }
interface CellT { cell: string; source: string; identity_score: number | null }
interface SheetT {
  file: string; url: string; bytes?: number; preset?: string; labels?: boolean;
  size?: number[]; cells?: CellT[]; missing?: string[]; created_at?: string;
  outfit?: { name: string; variant: string } | null;
}
/** outfit option, from GET /api/klein3/characters/{slug}/outfits */
interface OutfitOptT { name: string; variant: string; label: string; views: number }

export default function CharacterSheetPanel(): React.ReactElement {
  const [chars, setChars] = useState<CharT[]>([]);
  const lb = useLightbox();
  const [slug, setSlug] = useState(() => {
    try {
      const f = window.localStorage.getItem('rbmn_focus_char') || '';
      window.localStorage.removeItem('rbmn_focus_char');
      return f;
    } catch { return ''; }
  });
  const [preset, setPreset] = useState<'standard' | 'turnaround' | 'outfit'>('standard');
  const [outfits, setOutfits] = useState<OutfitOptT[]>([]);
  // JSON [name, variant] — outfit names contain spaces, so no string splitting
  const [outfitKey, setOutfitKey] = useState('');
  const [labels, setLabels] = useState(false);
  const [width, setWidth] = useState<'full' | '2048'>('full');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');
  const [latest, setLatest] = useState<SheetT | null>(null);
  const [sheets, setSheets] = useState<SheetT[]>([]);

  const loadChars = useCallback(async () => {
    try {
      const r = await j<{ characters: CharT[] }>(await fetch(`${BASE}/characters`));
      setChars(r.characters);
      if (r.characters.length && !slug) setSlug(r.characters[0].slug);
    } catch (e) { setErr(String((e as Error).message || e)); }
  }, [slug]);

  const loadSheets = useCallback(async (s: string) => {
    if (!s) { setSheets([]); return; }
    try {
      const r = await j<{ sheets: SheetT[] }>(await fetch(`${BASE}/characters/${s}/sheets`));
      setSheets(r.sheets);
    } catch { setSheets([]); }
  }, []);

  useEffect(() => { void loadChars(); }, [loadChars]);
  useEffect(() => { setLatest(null); void loadSheets(slug); }, [slug, loadSheets]);

  // 🧥 the character's rendered outfits, for per-outfit sheets
  useEffect(() => {
    if (!slug) { setOutfits([]); return; }
    let stop = false;
    (async () => {
      try {
        interface OVarT { variant: string; label: string; views: Record<string, unknown> }
        interface OT { name: string; variants: OVarT[] }
        const r = await j<{ outfits: OT[] }>(
          await fetch(`/api/klein3/characters/${slug}/outfits`));
        if (stop) return;
        const opts: OutfitOptT[] = [];
        for (const o of r.outfits || []) {
          for (const v of o.variants || []) {
            const n = Object.keys(v.views || {}).length;
            if (n > 0) {
              opts.push({
                name: o.name, variant: v.variant,
                label: `${o.name}${v.variant ? ` · ${v.label}` : ''} (${n} views)`,
                views: n,
              });
            }
          }
        }
        setOutfits(opts);
        setOutfitKey((k) => opts.some((o) => JSON.stringify([o.name, o.variant]) === k)
          ? k : (opts[0] ? JSON.stringify([opts[0].name, opts[0].variant]) : ''));
      } catch { if (!stop) setOutfits([]); }
    })();
    return () => { stop = true; };
  }, [slug]);

  const generate = async () => {
    if (!slug) return;
    setBusy(true); setErr('');
    try {
      const body: Record<string, unknown> = { slug, preset, labels };
      if (width === '2048') body.width = 2048;
      if (preset === 'outfit') {
        try {
          const [n, v] = JSON.parse(outfitKey) as [string, string];
          body.outfit_name = n || '';
          body.outfit_variant = v || '';
        } catch { setErr('pick an outfit first'); setBusy(false); return; }
      }
      const r = await j<SheetT>(await fetch(`${BASE}/generate`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      }));
      setLatest(r);
      void loadSheets(slug);
    } catch (e) { setErr(String((e as Error).message || e)); }
    setBusy(false);
  };

  const del = async (fname: string) => {
    try {
      await fetch(`${BASE}/characters/${slug}/sheets/${fname}/delete`, { method: 'POST' });
      if (latest?.file === fname) setLatest(null);
      void loadSheets(slug);
    } catch { /* ignore */ }
  };

  const cur = chars.find((c) => c.slug === slug);
  const shown = latest || sheets[0] || null;

  return (
    <div style={{ display: 'grid', gridTemplateColumns: '320px 1fr', gap: 12 }}>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
        <div style={box}>
          <h3 style={{ margin: '0 0 8px', fontSize: 15 }}>🪪 Character Sheet</h3>
          <div style={hint}>
            One reference image with a full turnaround and face row — for models that accept a
            character sheet (MiniMax H3 etc.). Built from this character&apos;s best
            identity-scored dataset renders and refs. No worker, no LoRA needed.
          </div>
        </div>

        <div style={box}>
          <span style={label}>Character</span>
          <select style={input} value={slug} onChange={(e) => setSlug(e.target.value)}>
            {chars.map((c) => (
              <option key={c.slug} value={c.slug}>
                {c.name} ({c.dataset_images} dataset imgs · {c.ref_count} refs)
              </option>
            ))}
          </select>
          {cur && cur.dataset_images === 0 && (
            <div style={{ ...hint, marginTop: 6 }}>
              ⚠ No rendered dataset images — the sheet will use refs/base only. For a stronger
              sheet, render a LoRA dataset for this character first (🎓 tab).
            </div>
          )}

          <div style={{ marginTop: 10 }}>
            <span style={label}>Layout</span>
            <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
              <button style={chip(preset === 'standard')} onClick={() => setPreset('standard')}>
                Standard (8 cells)
              </button>
              <button style={chip(preset === 'turnaround')} onClick={() => setPreset('turnaround')}>
                Turnaround (4)
              </button>
              <button style={{ ...chip(preset === 'outfit'),
                               opacity: outfits.length ? 1 : 0.5 }}
                      title="a sheet built from ONE outfit's rendered views — reference the character in a specific attire"
                      disabled={!outfits.length}
                      onClick={() => setPreset('outfit')}>
                🧥 Outfit ({outfits.length ? `${outfits.length} available` : 'none rendered'})
              </button>
            </div>
            {preset === 'outfit' && (
              <div style={{ marginTop: 8 }}>
                <span style={label}>Outfit</span>
                <select style={input} value={outfitKey}
                        onChange={(e) => setOutfitKey(e.target.value)}>
                  {outfits.map((o) => (
                    <option key={`${o.name}|${o.variant}`}
                            value={JSON.stringify([o.name, o.variant])}>
                      {o.label}
                    </option>
                  ))}
                </select>
                <div style={{ ...hint, marginTop: 4 }}>
                  Uses only this outfit&apos;s rendered views — never the dataset — so the
                  attire on the sheet is exactly this outfit.
                </div>
              </div>
            )}
          </div>

          <div style={{ marginTop: 10, display: 'flex', gap: 6 }}>
            <button style={chip(!labels)} onClick={() => setLabels(false)}>
              No text (model reference)
            </button>
            <button style={chip(labels)} onClick={() => setLabels(true)}>
              Labeled (human reference)
            </button>
          </div>
          <div style={{ ...hint, marginTop: 4 }}>
            Text on a sheet can leak into generations — keep &ldquo;No text&rdquo; when the
            sheet is a model input.
          </div>

          <div style={{ marginTop: 10 }}>
            <span style={label}>Output size</span>
            <div style={{ display: 'flex', gap: 6 }}>
              <button style={chip(width === 'full')} onClick={() => setWidth('full')}>
                Full (~3200px)
              </button>
              <button style={chip(width === '2048')} onClick={() => setWidth('2048')}>
                2048px wide
              </button>
            </div>
          </div>

          <button style={{ ...btn, width: '100%', marginTop: 12, opacity: busy || !slug ? 0.6 : 1 }}
                  disabled={busy || !slug} onClick={() => void generate()}>
            {busy ? 'Building…' : '🪪 Build sheet'}
          </button>
          {err && <div style={{ ...errTxt, marginTop: 6 }}>{err}</div>}
        </div>

        {shown?.cells && (
          <div style={box}>
            <span style={label}>Cell sources{shown.file ? ` — ${shown.file}` : ''}</span>
            {shown.cells.map((c) => (
              <div key={c.cell} style={{ fontSize: 12, color: '#cbd2dc', padding: '2px 0' }}>
                <b>{c.cell}</b> ← {c.source}
                {c.identity_score != null && (
                  <span style={{ color: '#5ee08a' }}> ({c.identity_score.toFixed(3)})</span>
                )}
              </div>
            ))}
            {!!shown.missing?.length && (
              <div style={{ ...errTxt, marginTop: 4 }}>missing: {shown.missing.join(', ')}</div>
            )}
          </div>
        )}
      </div>

      <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
        <div style={box}>
          {shown ? (
            <>
              <div style={{ display: 'flex', gap: 8, marginBottom: 8, alignItems: 'center' }}>
                <span style={hint}>
                  {shown.outfit?.name ? `🧥 ${shown.outfit.name}` : ''} {shown.file}
                </span>
                <div style={{ flex: 1 }} />
                <a href={`${shown.url}?download=1`} download>
                  <button style={btn}>📥 Download PNG</button>
                </a>
              </div>
              <img src={`${shown.url}?t=${shown.file}`} alt="character sheet"
                   title="Click to view large (zoom + pan) — sheets are dense, zoom in on the face row"
                   onClick={() => lb.open(`${shown.url}?t=${shown.file}`, 0, shown.file)}
                   style={{ width: '100%', borderRadius: 8, background: '#fff', cursor: 'zoom-in' }} />
            </>
          ) : (
            <div style={hint}>Pick a character and hit 🪪 Build sheet.</div>
          )}
        </div>

        {sheets.length > 0 && (
          <div style={box}>
            <span style={label}>Sheet library — every generated sheet, downloadable</span>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
              {sheets.map((s) => (
                <div key={s.file} style={{ width: 180 }}>
                  <img src={s.url} alt={s.file}
                       style={{ width: '100%', borderRadius: 6, background: '#fff', cursor: 'pointer' }}
                       onClick={() => setLatest(s)} />
                  <div style={{ ...hint, fontSize: 10, marginTop: 2, whiteSpace: 'nowrap',
                                overflow: 'hidden', textOverflow: 'ellipsis' }}
                       title={s.file}>
                    {s.outfit?.name
                      ? `🧥 ${s.outfit.name}${s.outfit.variant ? ` · ${s.outfit.variant}` : ''}`
                      : (s.preset || 'sheet')}
                  </div>
                  <div style={{ display: 'flex', gap: 4, marginTop: 2 }}>
                    <a href={`${s.url}?download=1`} download style={{ flex: 1 }}>
                      <button style={{ ...btnGhost, width: '100%', padding: '3px 6px', fontSize: 11 }}>📥</button>
                    </a>
                    <button style={{ ...btnGhost, padding: '3px 6px', fontSize: 11 }}
                            onClick={() => void del(s.file)}>🗑</button>
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
      {lb.node}
    </div>
  );
}
