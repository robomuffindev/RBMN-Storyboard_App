/**
 * 📚 Character Image Picker (v1.277.2) — pick any image belonging to any
 * character, WITH a preview, before it is used as a reference.
 *
 * His ask: "when selecting a reference in video mode, I want to be able to
 * choose one of our characters and select which character sheet to use and
 * have a preview... same with our dataset images and base views."
 *
 * 📍 v1.277.22 — LOCATIONS are pickable here too. A reference can be a PLACE
 * as easily as a person, and a location's 🪪 sheet is exactly the same kind of
 * artefact as a character sheet: several views in one image, which is what a
 * model needs to hold a place consistent across shots.
 *
 * Sources per character (followed by source, per the unified adapter):
 *   k3 → 🪪 character sheets (incl. per-outfit) · 🧭 views & refs (incl.
 *        outfit views, active base) · 🎓 dataset renders
 *   db → the VNCCS catalog's images
 *
 * Generic on purpose: onPick(url, name) hands back a same-origin image URL;
 * the caller decides what to do with it (the Video Lab fetches the blob and
 * re-uploads it as an H3 upload).
 */
import React, { useCallback, useEffect, useState } from 'react';

const modalBg: React.CSSProperties = {
  position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.72)', zIndex: 60,
  display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 18,
};
const modal: React.CSSProperties = {
  background: '#0b0e13', border: '1px solid #2a2f3a', borderRadius: 12,
  width: 'min(1100px, 96vw)', height: 'min(720px, 92vh)', display: 'flex',
  flexDirection: 'column', padding: 12, gap: 10,
};
const inp: React.CSSProperties = {
  background: '#0e1116', border: '1px solid #2a2f3a', borderRadius: 6,
  color: '#e6e9ee', padding: '6px 8px', fontSize: 13,
};
const btn: React.CSSProperties = {
  background: '#3b82f6', border: 'none', borderRadius: 6, color: '#fff',
  padding: '7px 12px', fontSize: 13, cursor: 'pointer', fontWeight: 600,
};
const btnGhost: React.CSSProperties = {
  ...btn, background: 'transparent', border: '1px solid #2a2f3a',
  color: '#cbd2dc', fontWeight: 400,
};
const hint: React.CSSProperties = { color: '#8d97a5', fontSize: 12 };
const tabBtn = (on: boolean): React.CSSProperties => ({
  background: on ? '#1d4ed8' : 'transparent',
  border: `1px solid ${on ? '#3b82f6' : '#2a2f3a'}`, borderRadius: 999,
  color: on ? '#fff' : '#cbd2dc', padding: '3px 10px', fontSize: 12, cursor: 'pointer',
});

interface UniCharT {
  ref: string; source: 'k3' | 'db'; id?: string; slug?: string; name: string;
  thumb?: string | null; sheets?: number; ref_count?: number;
  datasets?: { id: string }[];
}
interface PickT { url: string; name: string; group: string; label: string }
interface LocImgT { id: string; url: string; kind: string; label: string; active?: boolean }
interface LocGroupT { world_id: string; world: string; id: string; name: string;
                      kind: string; images: LocImgT[] }

async function jj<T>(r: Response): Promise<T> {
  if (!r.ok) throw new Error(String(r.status));
  return r.json() as Promise<T>;
}

export default function CharacterImagePicker({ onPick, onClose, k3Only }: {
  onPick: (url: string, name: string) => void; onClose: () => void;
  /** hide VNCCS-store characters — their catalog URLs cannot be resolved to
   *  disk paths by the H3 video-ref lane, so offering them there would show a
   *  ref on the card that the render silently drops */
  k3Only?: boolean;
}): React.ReactElement {
  const [mode, setMode] = useState<'chars' | 'locs'>('chars');
  const [locs, setLocs] = useState<LocGroupT[]>([]);
  const [curLoc, setCurLoc] = useState<LocGroupT | null>(null);
  const [chars, setChars] = useState<UniCharT[]>([]);
  const [q, setQ] = useState('');
  const [cur, setCur] = useState<UniCharT | null>(null);
  const [group, setGroup] = useState<'sheets' | 'views' | 'dataset'>('sheets');
  const [imgs, setImgs] = useState<PickT[]>([]);
  const [sel, setSel] = useState<PickT | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState('');

  useEffect(() => {
    (async () => {
      try {
        const r = await jj<{ characters: UniCharT[] }>(await fetch('/api/characters'));
        setChars(r.characters || []);
      } catch (e) { setErr(`could not load characters: ${e}`); }
      try {
        const r = await jj<{ locations: LocGroupT[] }>(
          await fetch('/api/storyworld/location-images'));
        setLocs(r.locations || []);
      } catch { /* worlds are optional — a fleet with no worlds is fine */ }
    })();
  }, []);

  const loadImages = useCallback(async (c: UniCharT, g: string) => {
    setLoading(true); setErr(''); setImgs([]); setSel(null);
    const out: PickT[] = [];
    try {
      if (c.source === 'db') {
        // VNCCS catalog — one flat gallery regardless of group
        interface CatImgT { url: string; label?: string }
        interface CatOutT { label?: string; images?: CatImgT[] }
        const r = await jj<{ outputs?: CatOutT[] }>(
          await fetch(`/api/studio/vnccs/catalog/${c.id}/images`));
        for (const o of r.outputs || []) {
          for (const im of o.images || []) {
            if (im.url) {
              out.push({ url: im.url, name: `${c.name}_${o.label || 'image'}.png`,
                group: 'views', label: o.label || 'image' });
            }
          }
        }
      } else if (g === 'sheets') {
        interface SheetT { url: string; file: string; outfit?: { name: string } | null; preset?: string }
        const r = await jj<{ sheets: SheetT[] }>(
          await fetch(`/api/charsheet/characters/${c.slug}/sheets`));
        for (const s of r.sheets || []) {
          out.push({ url: s.url, name: `${c.slug}_${s.file}`, group: 'sheets',
            label: s.outfit?.name ? `🧥 ${s.outfit.name}` : (s.preset || 'sheet') });
        }
      } else if (g === 'views') {
        interface RefT { id: string; tag: string; url?: string; rejected?: boolean;
          name?: string; outfit?: { name?: string; view?: string } | null }
        const r = await jj<{ refs?: RefT[]; active_base_url?: string }>(
          await fetch(`/api/klein3/characters/${c.slug}`));
        if (r.active_base_url) {
          out.push({ url: r.active_base_url, name: `${c.slug}_base.png`,
            group: 'views', label: '⭐ active base' });
        }
        for (const ref of r.refs || []) {
          if (ref.rejected || !ref.url) continue;
          const label = ref.tag === 'outfit'
            ? `🧥 ${ref.outfit?.name || 'outfit'} · ${ref.outfit?.view || ''}`
            : ref.tag;
          out.push({ url: ref.url, name: `${c.slug}_${ref.tag}_${ref.id}.png`,
            group: 'views', label });
        }
      } else {
        interface DsRowT { id: string; char_slug?: string }
        interface ItemT { id: string; url?: string; has_image?: boolean;
          framing?: string; angle?: string }
        const list = await jj<{ datasets: DsRowT[] }>(await fetch('/api/lora/datasets'));
        const mine = (list.datasets || []).filter((d) => d.char_slug === c.slug);
        for (const d of mine) {
          const ds = await jj<{ items?: ItemT[] }>(
            await fetch(`/api/lora/datasets/${d.id}`));
          for (const it of ds.items || []) {
            if (it.has_image && it.url) {
              out.push({ url: it.url, name: `${c.slug}_${d.id}_${it.id}.png`,
                group: 'dataset', label: `${it.framing || ''} ${it.angle || ''}`.trim() });
            }
          }
        }
      }
      setImgs(out);
      if (!out.length) setErr('nothing here for this character yet');
    } catch (e) { setErr(`could not load images: ${e}`); }
    setLoading(false);
  }, []);

  useEffect(() => { if (cur) void loadImages(cur, group); }, [cur, group, loadImages]);
  useEffect(() => {
    if (mode !== 'locs' || !curLoc) return;
    setImgs((curLoc.images || []).map((im) => ({
      url: im.url, name: `${curLoc.name.replace(/\s+/g, '_')}_${im.kind}_${im.id}.png`,
      group: im.kind, label: (im.kind === 'sheet' ? '🪪 sheet' : im.label)
        + (im.active ? ' ⭐' : '') })));
    setSel(null); setErr('');
  }, [mode, curLoc]);

  const filtered = chars.filter((c) =>
    (!k3Only || c.source === 'k3')
    && (!q.trim() || c.name.toLowerCase().includes(q.trim().toLowerCase())));

  return (
    <div style={modalBg} onClick={onClose}>
      <div style={modal} onClick={(e) => e.stopPropagation()}>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          <b style={{ color: '#e6e9ee', fontSize: 14 }}>📚 Pick a reference image</b>
          <span style={hint}>characters: sheets · views · dataset · 📍 locations: 🪪 sheet + plates
            — preview before you commit</span>
          <div style={{ flex: 1 }} />
          <button style={btnGhost} onClick={onClose}>✕</button>
        </div>

        <div style={{ display: 'flex', gap: 10, flex: 1, minHeight: 0 }}>
          {/* characters */}
          <div style={{ width: 210, display: 'flex', flexDirection: 'column', gap: 6,
                        overflowY: 'auto', flexShrink: 0 }}>
            <div style={{ display: 'flex', gap: 4 }}>
              <button style={tabBtn(mode === 'chars')}
                      onClick={() => { setMode('chars'); setCurLoc(null); }}>🎭 characters</button>
              <button style={tabBtn(mode === 'locs')}
                      onClick={() => { setMode('locs'); setCur(null); }}>📍 locations</button>
            </div>
            <input style={inp} placeholder="filter…" value={q}
                   onChange={(e) => setQ(e.target.value)} />
            {mode === 'locs' && locs
              .filter((l) => !q.trim() || l.name.toLowerCase().includes(q.trim().toLowerCase()))
              .map((l) => (
                <button key={`${l.world_id}:${l.id}`}
                  style={{ ...btnGhost, display: 'flex', gap: 8, alignItems: 'center',
                           justifyContent: 'flex-start', textAlign: 'left',
                           borderColor: curLoc?.id === l.id ? '#3b82f6' : '#2a2f3a' }}
                  onClick={() => setCurLoc(l)}>
                  <img src={l.images[0]?.url} alt="" style={{ width: 30, height: 30,
                       borderRadius: 4, objectFit: 'cover' }} />
                  <span style={{ overflow: 'hidden', textOverflow: 'ellipsis',
                                 whiteSpace: 'nowrap' }}>
                    {l.name}
                    <span style={{ ...hint, display: 'block', fontSize: 10 }}>
                      {l.world} · {l.images.length} img
                    </span>
                  </span>
                </button>
              ))}
            {mode === 'locs' && !locs.length && (
              <span style={hint}>no location plates yet — render them on /worlds → 📍 Locations</span>
            )}
            {mode === 'chars' && filtered.map((c) => (
              <button key={c.ref}
                style={{ ...btnGhost, display: 'flex', gap: 8, alignItems: 'center',
                         justifyContent: 'flex-start', textAlign: 'left',
                         borderColor: cur?.ref === c.ref ? '#3b82f6' : '#2a2f3a' }}
                onClick={() => { setCur(c); setGroup(c.source === 'db' ? 'views' : 'sheets'); }}>
                {c.thumb
                  ? <img src={c.thumb} alt="" style={{ width: 30, height: 30,
                      objectFit: 'cover', borderRadius: 4 }} />
                  : <span style={{ width: 30, textAlign: 'center' }}>👤</span>}
                <span style={{ overflow: 'hidden', textOverflow: 'ellipsis',
                               whiteSpace: 'nowrap', fontSize: 12 }}>{c.name}</span>
              </button>
            ))}
            {!filtered.length && <span style={hint}>no characters</span>}
          </div>

          {/* images */}
          <div style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column', gap: 8 }}>
            {(cur || curLoc) ? (
              <>
                {mode === 'locs' && curLoc && (
                  <span style={hint}>
                    📍 {curLoc.name} — 🪪 the SHEET first (several views in one image, the
                    reference a model holds a place consistent from), then the plates
                  </span>
                )}
                {mode === 'chars' && cur?.source === 'k3' && (
                  <div style={{ display: 'flex', gap: 6 }}>
                    <button style={tabBtn(group === 'sheets')}
                            onClick={() => setGroup('sheets')}>🪪 Sheets</button>
                    <button style={tabBtn(group === 'views')}
                            onClick={() => setGroup('views')}>🧭 Views & refs</button>
                    <button style={tabBtn(group === 'dataset')}
                            onClick={() => setGroup('dataset')}>🎓 Dataset</button>
                  </div>
                )}
                {err && <span style={{ ...hint, color: '#c9a227' }}>{err}</span>}
                {loading && <span style={hint}>loading…</span>}
                <div style={{ flex: 1, overflowY: 'auto', display: 'flex',
                              flexWrap: 'wrap', gap: 8, alignContent: 'flex-start' }}>
                  {imgs.map((im) => (
                    <div key={im.url} style={{ width: 110, cursor: 'pointer' }}
                         onClick={() => setSel(im)}>
                      <img src={im.url} alt={im.label} loading="lazy"
                           style={{ width: '100%', height: 110, objectFit: 'cover',
                                    borderRadius: 6, background: '#fff',
                                    border: sel?.url === im.url
                                      ? '2px solid #3b82f6' : '2px solid transparent' }} />
                      <div style={{ ...hint, fontSize: 10, whiteSpace: 'nowrap',
                                    overflow: 'hidden', textOverflow: 'ellipsis' }}
                           title={im.label}>{im.label}</div>
                    </div>
                  ))}
                </div>
              </>
            ) : <span style={hint}>pick a character on the left</span>}
          </div>

          {/* preview */}
          <div style={{ width: 300, flexShrink: 0, display: 'flex',
                        flexDirection: 'column', gap: 8 }}>
            {sel ? (
              <>
                <img src={sel.url} alt="preview"
                     style={{ width: '100%', maxHeight: 520, objectFit: 'contain',
                              borderRadius: 8, background: '#fff' }} />
                <span style={hint}>{sel.label}</span>
                <button style={btn} onClick={() => onPick(sel.url, sel.name)}>
                  ✅ Use this image
                </button>
              </>
            ) : <span style={hint}>click an image to preview it here</span>}
          </div>
        </div>
      </div>
    </div>
  );
}
