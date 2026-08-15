/**
 * UnifiedCharacterGrid — the Character Studio front door (v1.276.0).
 *
 * Two things were wrong with the old /studio grid and they had the same root:
 *
 *  1. It listed `studio_characters` DB rows only, so a character made in
 *     Klein 3.0 — which lives on disk under _libraries/klein3/chars/<slug>/ and
 *     never gets a DB row — simply did not appear. Lorenzo made one and could
 *     not find it. Not a filter bug: two disjoint stores, joined nowhere.
 *  2. Each tile showed a portrait and a name, so the page could tell you a
 *     character EXISTED but nothing about what state it was in.
 *
 * This grid reads `/api/characters`, the unified adapter, so every mode's
 * characters appear side by side with a badge saying which made them. And it
 * borrows the 🏠 Studio Hub's card design, which Lorenzo singled out as the
 * thing that works: one card = one character = its whole pipeline, with a
 * tri-state checklist carrying real numbers rather than a progress bar you have
 * to interpret. Chips read "views 3/4 (missing left, back)", not "75%".
 *
 * Cards are capability-aware. A Klein 3.0 character offers views / dataset /
 * sheet / outfits; a VNCCS character offers poses / costumes / emotions. Rather
 * than showing every button and letting some of them fail, each card offers
 * only what its source can actually do.
 */
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import useLightbox from '../shared/useLightbox';

export interface UnifiedCharT {
  ref: string;
  source: 'k3' | 'db';
  source_label: string;
  id: string;
  slug: string | null;
  name: string;
  thumb: string | null;
  updated_at?: string | null;
  ref_count: number;
  has_base: boolean;
  has_front: boolean;
  missing_views: string[];
  sheets: number;
  lore_filled: boolean;
  datasets: Array<{
    id?: string; total?: number; rendered?: number; flagged?: number;
    trigger?: string; train_stage?: string; installed_lora?: string;
  }>;
  installed_loras: string[];
  capabilities: string[];
  story_id?: string | null;
}

const card: React.CSSProperties = {
  background: '#12151b', border: '1px solid #2a2f3a', borderRadius: 10, padding: 12,
};
const hint: React.CSSProperties = { fontSize: 11, color: '#8d97a5' };
const chipBase: React.CSSProperties = {
  fontSize: 11, padding: '1px 7px', borderRadius: 10, width: 'fit-content',
  border: '1px solid transparent', whiteSpace: 'nowrap', overflow: 'hidden',
  textOverflow: 'ellipsis', maxWidth: '100%',
};
const okChip: React.CSSProperties = { ...chipBase, color: '#5ee08a', borderColor: '#1e4d31' };
const noChip: React.CSSProperties = { ...chipBase, color: '#8d97a5', borderColor: '#2a2f3a' };
const busyChip: React.CSSProperties = { ...chipBase, color: '#9cc2ff', borderColor: '#1f3a63' };
const btnSm: React.CSSProperties = {
  padding: '3px 8px', fontSize: 11, borderRadius: 6, cursor: 'pointer',
  background: '#1a1f28', border: '1px solid #2a2f3a', color: '#cbd2dc',
  fontFamily: 'inherit',
};

/** The Studio Hub's checklist chip: ✓ done · ⏳ working · ○ not yet. */
function Check({ ok, busy, label }: { ok: boolean; busy?: boolean; label: string }) {
  return (
    <span style={busy ? busyChip : ok ? okChip : noChip} title={label}>
      {busy ? '⏳' : ok ? '✓' : '○'} {label}
    </span>
  );
}

export const FOCUS_KEY = 'rbmn_focus_char';

export default function UnifiedCharacterGrid(
  { onOpenDbCharacter }: { onOpenDbCharacter?: (id: string) => void },
) {
  const navigate = useNavigate();
  const lb = useLightbox();
  const [chars, setChars] = useState<UnifiedCharT[]>([]);
  const [counts, setCounts] = useState<Record<string, number>>({});
  const [err, setErr] = useState('');
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState<'all' | 'k3' | 'db'>('all');
  const [q, setQ] = useState('');

  const load = useCallback(async () => {
    try {
      const r = await fetch('/api/characters');
      if (!r.ok) throw new Error(await r.text());
      const j = await r.json();
      setChars(j.characters || []);
      setCounts(j.counts || {});
      setErr('');
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
    const t = window.setInterval(() => void load(), 20000);   // live, like the hub
    return () => window.clearInterval(t);
  }, [load]);

  /** Jump into a VNCCS/Klein tab with this character preselected. */
  const jump = (tab: string, c: UnifiedCharT) => {
    const key = c.slug || c.name;
    // v1.277.10: BOTH keys — focus is the one-shot jump, current is the
    // persistent studio-wide character every tab now defaults to
    try {
      window.localStorage.setItem(FOCUS_KEY, key);
      window.localStorage.setItem('rbmn_current_char', key);
    } catch { /* non-fatal */ }
    navigate(`/studio/vnccs-klein?tab=${tab}&char=${encodeURIComponent(key)}`);
  };

  const [busy, setBusy] = useState('');

  /** Delete a character, telling the truth about what actually goes.
   *
   *  The two stores delete differently and clean up different amounts, so the
   *  confirmation is built per source rather than being one generic "are you
   *  sure". Klein 3.0 rmtree's the character folder — refs, bases, outfits —
   *  but its LoRA datasets and character sheets live in OTHER libraries and
   *  survive as orphans. Saying "deleted" while leaving a 40-image dataset on
   *  disk would be a lie by omission. */
  const del = async (c: UnifiedCharT) => {
    const isK3 = c.source === 'k3';
    const orphans: string[] = [];
    if (isK3) {
      if (c.datasets?.length) orphans.push(`${c.datasets.length} LoRA dataset(s)`);
      if (c.sheets) orphans.push(`${c.sheets} character sheet(s)`);
    }
    const what = isK3
      ? `Delete "${c.name}"?\n\nThis permanently removes its references, base versions and `
        + `outfits from disk (${c.ref_count} reference(s)).`
        + (orphans.length
          ? `\n\nNOT removed: ${orphans.join(' and ')} — those live in other libraries `
            + `and will be left behind.`
          : '')
      : `Delete "${c.name}"?\n\nThis removes the character${
          c.capabilities?.includes('vnccs') ? ' and its library images' : ' and its datasets'}.`;
    if (!window.confirm(what)) return;

    setBusy(c.ref);
    try {
      if (isK3) {
        const r = await fetch(`/api/klein3/characters/${c.id}/delete`, { method: 'POST' });
        if (!r.ok) throw new Error(await r.text());
      } else if (c.capabilities?.includes('vnccs')) {
        // VNCCS characters ALSO have sprite folders on the worker boxes. That
        // is a separate, slower, irreversible thing — ask separately.
        const alsoHosts = window.confirm(
          'ALSO delete this character from the VNCCS worker boxes '
          + '(node-side sprites/config)?\n\nOK = workers too   ·   Cancel = keep worker files');
        const r = await fetch(
          `/api/studio/vnccs/catalog/${c.id}?from_hosts=${alsoHosts}`, { method: 'DELETE' });
        if (!r.ok) throw new Error(await r.text());
      } else {
        const r = await fetch(`/api/character-studio/characters/${c.id}`, { method: 'DELETE' });
        if (!r.ok) throw new Error(await r.text());
      }
      await load();
    } catch (e) {
      window.alert(`Delete failed: ${(e as Error).message}`);
    }
    setBusy('');
  };

  const shown = useMemo(() => {
    const needle = q.trim().toLowerCase();
    return chars
      .filter((c) => (filter === 'all' ? true : c.source === filter))
      .filter((c) => (!needle ? true : c.name.toLowerCase().includes(needle)));
  }, [chars, filter, q]);

  const tab = (k: 'all' | 'k3' | 'db', label: string, n?: number) => (
    <button
      key={k}
      onClick={() => setFilter(k)}
      style={{
        ...btnSm,
        background: filter === k ? '#243044' : '#1a1f28',
        color: filter === k ? '#e6e9ee' : '#cbd2dc',
        fontWeight: filter === k ? 700 : 400,
      }}
    >{label}{typeof n === 'number' ? ` (${n})` : ''}</button>
  );

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      <div style={{ ...card, display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
        <span style={hint}>
          Every character, from every mode — create → views → dataset → LoRA → sheet → lore.
        </span>
        <div style={{ flex: 1 }} />
        {tab('all', 'All', counts.total)}
        {tab('k3', '🎯 Klein 3.0', counts.k3)}
        {tab('db', '🟣 VNCCS', counts.db)}
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="Filter by name…"
          style={{
            background: '#0e1116', border: '1px solid #2a2f3a', borderRadius: 6,
            color: '#e6e9ee', padding: '4px 8px', fontSize: 12, width: 150,
          }}
        />
      </div>

      {err && <div style={{ ...card, color: '#ff8a8a', fontSize: 12 }}>{err}</div>}
      {loading && !chars.length && <div style={{ ...card, ...hint }}>Loading characters…</div>}

      <div style={{
        display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(340px, 1fr))', gap: 12,
      }}>
        {shown.map((c) => {
          const isK3 = c.source === 'k3';
          const views = 4 - (c.missing_views?.length || 0);
          const ds = c.datasets?.[0];
          const training = Boolean(ds?.train_stage
            && !['done', 'error', 'idle'].includes(String(ds.train_stage)));
          return (
            <div key={c.ref} style={card}>
              <div style={{ display: 'flex', gap: 10 }}>
                {c.thumb ? (
                  <img
                    src={c.thumb}
                    alt=""
                    title="Click to view large (zoom + pan)"
                    onClick={() => lb.open(c.thumb || '', 0, c.name)}
                    style={{
                      width: 84, height: 122, objectFit: 'cover', borderRadius: 6,
                      cursor: 'zoom-in', background: '#0e1116',
                    }}
                  />
                ) : (
                  <div style={{
                    width: 84, height: 122, borderRadius: 6, background: '#0e1116',
                    display: 'flex', alignItems: 'center', justifyContent: 'center',
                    color: '#4a5568', fontSize: 24,
                  }}>?</div>
                )}
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{
                    fontWeight: 700, color: '#e6e9ee', fontSize: 14,
                    overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                  }}>{c.name}</div>
                  <div style={{
                    ...hint, marginTop: 1, marginBottom: 5,
                    color: isK3 ? '#7fb2ff' : '#c69cff',
                  }}>{isK3 ? '🎯' : '🟣'} {c.source_label}</div>

                  <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
                    {isK3 ? (
                      <>
                        <Check ok={c.has_front}
                               label={c.has_front ? 'front reference' : 'no front reference yet'} />
                        <Check ok={views === 4}
                               label={`views ${views}/4${c.missing_views?.length
                                 ? ` (missing ${c.missing_views.join(', ')})` : ''}`} />
                        <Check ok={Boolean(ds && (ds.rendered || 0) >= (ds.total || 0) && (ds.total || 0) > 0)}
                               label={ds
                                 ? `dataset ${ds.rendered}/${ds.total}${ds.flagged ? ` · ⚠${ds.flagged}` : ''}`
                                 : 'no dataset'} />
                        <Check ok={c.installed_loras.length > 0} busy={training}
                               label={training
                                 ? `training: ${ds?.train_stage}`
                                 : c.installed_loras.length
                                   ? `LoRA ${c.installed_loras[c.installed_loras.length - 1]}`
                                   : 'no LoRA'} />
                        <Check ok={c.sheets > 0}
                               label={c.sheets ? `${c.sheets} character sheet(s)` : 'no character sheet'} />
                        <Check ok={c.lore_filled} label={c.lore_filled ? 'lore written' : 'no lore'} />
                      </>
                    ) : (
                      <>
                        <Check ok={c.has_base}
                               label={c.has_base ? 'base render' : 'no base render yet'} />
                        <Check ok={c.ref_count > 0}
                               label={c.ref_count ? `${c.ref_count} base version(s)` : 'no versions'} />
                        <Check ok={c.lore_filled}
                               label={c.lore_filled ? 'description written' : 'no description'} />
                      </>
                    )}
                  </div>
                </div>
              </div>

              <div style={{ display: 'flex', gap: 4, marginTop: 10, flexWrap: 'wrap' }}>
                {isK3 ? (
                  <>
                    <button style={btnSm} onClick={() => jump('text2image', c)}>🧬 Open</button>
                    {/* Klein 3.0 is not a tab — it is `create` + engine klein3.
                        Refs AND outfits both live there, so both point at it
                        rather than at the VNCCS Clothes tab, which cannot see
                        a Klein 3.0 character at all. */}
                    <button style={btnSm} onClick={() => jump('klein3', c)}>🧭 Views/Refs</button>
                    <button style={btnSm} onClick={() => jump('klein3', c)}>👗 Outfits</button>
                    <button style={btnSm} onClick={() => jump('lora', c)}>🎓 Dataset</button>
                    <button style={btnSm} onClick={() => jump('charsheet', c)}>🪪 Sheet</button>
                  </>
                ) : (
                  <>
                    <button style={btnSm}
                            onClick={() => navigate(`/studio/vnccs-klein?char=${encodeURIComponent(c.name)}`)}>
                      🟣 Open
                    </button>
                    <button style={btnSm}
                            onClick={() => onOpenDbCharacter?.(c.id)}>📋 Details</button>
                    <button style={btnSm} onClick={() => jump('clothes', c)}>👗 Clothes</button>
                    <button style={btnSm} onClick={() => jump('emotions', c)}>😊 Emotions</button>
                  </>
                )}
                <div style={{ flex: 1 }} />
                <button
                  style={{ ...btnSm, borderColor: '#4a2130', color: '#ff8a8a' }}
                  title="Delete this character"
                  disabled={busy === c.ref}
                  onClick={() => void del(c)}
                >{busy === c.ref ? '…' : '🗑'}</button>
              </div>

              {ds?.trigger && (
                <div style={{ ...hint, marginTop: 6 }}>
                  trigger: <b style={{ color: '#cbd2dc' }}>{ds.trigger}</b>
                </div>
              )}
            </div>
          );
        })}

        {!shown.length && !loading && (
          <div style={{ ...card, gridColumn: '1 / -1' }}>
            <span style={hint}>
              {chars.length
                ? 'No character matches that filter.'
                : 'No characters yet — start one in 🧬 Text 2 Image or 🎯 Klein 3.0.'}
            </span>
          </div>
        )}
      </div>
      {lb.node}
    </div>
  );
}
