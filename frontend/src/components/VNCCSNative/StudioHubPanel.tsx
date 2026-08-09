/**
 * 🏠 Studio Hub (v1.272.0) — every character's whole pipeline at a glance.
 *
 * One card per character: portrait, completeness checklist (front ref → views
 * → dataset → LoRA → sheet → lore), live train/autogen stage, and one-click
 * jumps into any tab with the character preselected (via the shared focus key).
 */
import React, { useCallback, useEffect, useState } from 'react';

const BASE = '/api/forge';
export const FOCUS_KEY = 'rbmn_focus_char';

const box: React.CSSProperties = {
  background: '#12151b', border: '1px solid #2a2f3a', borderRadius: 10, padding: 12,
};
const hint: React.CSSProperties = { color: '#8d97a5', fontSize: 12 };
const btnSm: React.CSSProperties = {
  background: 'transparent', border: '1px solid #2a2f3a', borderRadius: 6,
  color: '#cbd2dc', padding: '3px 8px', fontSize: 11, cursor: 'pointer',
};
const okChip: React.CSSProperties = { color: '#5ee08a', fontSize: 12 };
const noChip: React.CSSProperties = { color: '#8d97a5', fontSize: 12 };
const busyChip: React.CSSProperties = { color: '#9cc2ff', fontSize: 12 };

interface DsT {
  id: string; total: number; rendered: number; flagged: number; trigger: string;
  train_stage?: string; installed_lora?: string;
}
interface CharT {
  slug: string; name: string; thumb?: string | null; has_front: boolean;
  missing_views: string[]; ref_count: number; has_base: boolean;
  forge_images: number; datasets: DsT[]; installed_loras: string[];
  autogen: { stage?: string; detail?: string }; sheets: number; lore_filled: boolean;
}

export default function StudioHubPanel({ goTo }: { goTo: (tab: string, slug: string) => void }): React.ReactElement {
  const [chars, setChars] = useState<CharT[]>([]);
  const [err, setErr] = useState('');

  const load = useCallback(async () => {
    try {
      const r = await fetch(`${BASE}/studio-overview`);
      if (!r.ok) throw new Error(`${r.status}`);
      setChars((await r.json()).characters || []);
    } catch (e) { setErr(String((e as Error).message || e)); }
  }, []);
  useEffect(() => {
    void load();
    const t = window.setInterval(load, 20000);
    return () => window.clearInterval(t);
  }, [load]);

  const jump = (tab: string, slug: string) => {
    try { window.localStorage.setItem(FOCUS_KEY, slug); } catch { /* ignore */ }
    goTo(tab, slug);
  };

  const Check = ({ ok, busy, label }: { ok: boolean; busy?: boolean; label: string }) => (
    <span style={busy ? busyChip : ok ? okChip : noChip}>
      {busy ? '⏳' : ok ? '✓' : '○'} {label}
    </span>
  );

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      <div style={{ ...box, display: 'flex', alignItems: 'center', gap: 10 }}>
        <h3 style={{ margin: 0, fontSize: 15 }}>🏠 Character Studio</h3>
        <span style={hint}>
          The pipeline per character: create → views → dataset → LoRA → sheet → lore.
          Click a step to jump there with the character selected.
        </span>
        <div style={{ flex: 1 }} />
        <button style={{ ...btnSm, background: '#3b82f6', color: '#fff', border: 'none', fontWeight: 600 }}
                onClick={() => jump('text2image', '')}>
          ➕ New character
        </button>
      </div>
      {err && <div style={{ ...box, color: '#ff8a8a', fontSize: 12 }}>{err}</div>}

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(340px, 1fr))', gap: 12 }}>
        {chars.map((c) => {
          const views = 4 - (c.missing_views?.length || 0);
          const ds = c.datasets[0];
          const training = Boolean(ds?.train_stage && !['done', 'error', 'idle'].includes(ds.train_stage));
          const autoBusy = Boolean(c.autogen?.stage && !['done', 'error'].includes(String(c.autogen.stage)));
          return (
            <div key={c.slug} style={box}>
              <div style={{ display: 'flex', gap: 10 }}>
                {c.thumb
                  ? <img src={c.thumb} alt="" style={{ width: 84, height: 122, objectFit: 'cover', borderRadius: 6 }} />
                  : <div style={{ width: 84, height: 122, borderRadius: 6, background: '#0e1116',
                                  display: 'flex', alignItems: 'center', justifyContent: 'center',
                                  color: '#4a5568', fontSize: 24 }}>?</div>}
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontWeight: 700, color: '#e6e9ee', fontSize: 14 }}>{c.name}</div>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 2, marginTop: 6 }}>
                    <Check ok={c.has_front} label={c.has_front ? 'front reference' : 'no front reference yet'} />
                    <Check ok={views === 4} label={`views ${views}/4${c.missing_views?.length ? ` (missing ${c.missing_views.join(', ')})` : ''}`} />
                    <Check ok={Boolean(ds && ds.rendered >= ds.total && ds.total > 0)} busy={autoBusy}
                           label={autoBusy ? `autogen: ${c.autogen.stage} ${c.autogen.detail || ''}`.slice(0, 60)
                             : ds ? `dataset ${ds.rendered}/${ds.total}${ds.flagged ? ` · ⚠${ds.flagged}` : ''}` : 'no dataset'} />
                    <Check ok={c.installed_loras.length > 0} busy={training}
                           label={training ? `training: ${ds?.train_stage}`
                             : c.installed_loras.length ? `LoRA ${c.installed_loras[c.installed_loras.length - 1]}` : 'no LoRA'} />
                    <Check ok={c.sheets > 0} label={c.sheets ? `${c.sheets} character sheet(s)` : 'no character sheet'} />
                    <Check ok={c.lore_filled} label={c.lore_filled ? 'lore written' : 'no lore'} />
                  </div>
                </div>
              </div>
              <div style={{ display: 'flex', gap: 4, marginTop: 10, flexWrap: 'wrap' }}>
                <button style={btnSm} onClick={() => jump('text2image', c.slug)}>🧬 Open</button>
                <button style={btnSm} onClick={() => jump('create', c.slug)}>🧭 Views/Refs</button>
                <button style={btnSm} onClick={() => jump('lora', c.slug)}>🎓 Dataset{ds ? '' : ' / ⚡'}</button>
                <button style={btnSm} onClick={() => jump('charsheet', c.slug)}>🪪 Sheet</button>
                <button style={btnSm} onClick={() => jump('text2image', c.slug)}>📖 Lore</button>
              </div>
              {ds?.trigger && (
                <div style={{ ...hint, marginTop: 6 }}>trigger: <b style={{ color: '#cbd2dc' }}>{ds.trigger}</b></div>
              )}
            </div>
          );
        })}
        {!chars.length && !err && (
          <div style={{ ...box, gridColumn: '1 / -1' }}>
            <span style={hint}>No characters yet — hit ➕ New character to start one in 🧬 Text 2 Image.</span>
          </div>
        )}
      </div>
    </div>
  );
}
