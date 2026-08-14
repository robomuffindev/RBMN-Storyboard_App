/**
 * ⚡⚡ Autogen — build a character from a description or photos, as far as you want.
 *
 * WHY THIS IS ONE SCREEN AND NOT A WIZARD
 * ---------------------------------------
 * Every stage here is optional and every stage feeds the next, so what you are
 * really choosing is HOW FAR ALONG THE SAME CHAIN to go. A wizard would hide
 * that: you would answer six screens without ever seeing that turning on the
 * LoRA is what makes the dataset run. One screen, one chain, with the cost of
 * your choices updating underneath it.
 *
 * TWO MODES, ONE FORM. Batch is the same form repeated — a list of characters
 * that run one after another — because anything you can ask for once you will
 * eventually want for ten.
 */
import React, { useEffect, useState } from 'react';

const BASE = '/api/autogen';

/* ── types ──────────────────────────────────────────────────────────────── */
export interface ClothingSpecT {
  name: string; description: string; ref_ids: string[]; wearer: string;
}
export interface SpecT {
  name: string;
  description: string;
  ref_ids: string[];
  do_base: boolean; do_views: boolean; do_clothing: boolean;
  do_charsheet: boolean; do_dataset: boolean; do_lora: boolean;
  clothing: ClothingSpecT[];
  clothing_auto_count: number;
  candidates: number;
  dataset_total: number;
  class_token: string;
  stop_on_bad_base: boolean;
}
interface EstRowT { stage: string; renders: number; note: string }
interface EstT {
  stages: EstRowT[]; renders: number; seconds: number; human: string; caveat: string;
}

export const blankSpec = (): SpecT => ({
  name: '', description: '', ref_ids: [],
  do_base: true, do_views: true, do_clothing: false,
  do_charsheet: false, do_dataset: false, do_lora: false,
  clothing: [], clothing_auto_count: 0,
  candidates: 4, dataset_total: 40, class_token: 'person',
  stop_on_bad_base: true,
});

/* ── styling, matching the rest of the studio ───────────────────────────── */
const card: React.CSSProperties = {
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
  ...btn, background: 'transparent', border: '1px solid #2a2f3a',
  color: '#cbd2dc', fontWeight: 400,
};
const btnSm: React.CSSProperties = { ...btnGhost, padding: '3px 8px', fontSize: 12 };
const hint: React.CSSProperties = { color: '#8d97a5', fontSize: 12 };
const lbl: React.CSSProperties = { fontSize: 11, color: '#8d97a5', display: 'block', marginBottom: 3 };

/** A stage toggle that also says what it depends on. The dependency is the
 *  point: turning on the LoRA turns on the dataset, and being told that is
 *  better than silently paying for it. */
function Stage({ on, set, icon, name, note, forcedBy }: {
  on: boolean; set: (v: boolean) => void; icon: string; name: string;
  note: string; forcedBy?: string;
}): React.ReactElement {
  const forced = Boolean(forcedBy);
  return (
    <label style={{
      display: 'flex', gap: 8, alignItems: 'flex-start', padding: '7px 9px',
      border: `1px solid ${on || forced ? '#3b82f6' : '#2a2f3a'}`,
      background: on || forced ? 'rgba(59,130,246,0.08)' : 'transparent',
      borderRadius: 8, cursor: forced ? 'not-allowed' : 'pointer',
      opacity: forced ? 0.85 : 1,
    }}>
      <input type="checkbox" checked={on || forced} disabled={forced}
             onChange={(e) => set(e.target.checked)} style={{ marginTop: 3 }} />
      <span>
        <span style={{ fontSize: 13, color: '#e6e9ee' }}>{icon} {name}</span>
        <span style={{ ...hint, display: 'block' }}>
          {forced ? `required by ${forcedBy}` : note}
        </span>
      </span>
    </label>
  );
}

/* ── one character's form ───────────────────────────────────────────────── */
export function SpecForm({ spec, set, onRemove, index }: {
  spec: SpecT; set: (s: SpecT) => void; onRemove?: () => void; index?: number;
}): React.ReactElement {
  const [upBusy, setUpBusy] = useState(false);
  const patch = (p: Partial<SpecT>) => set({ ...spec, ...p });

  const upload = async (files: FileList | null, into: 'character' | number) => {
    if (!files?.length) return;
    setUpBusy(true);
    try {
      const ids: string[] = [];
      for (const f of Array.from(files)) {
        const fd = new FormData();
        fd.append('file', f);
        fd.append('kind', into === 'character' ? 'character' : 'costume');
        const r = await fetch(`${BASE}/refs`, { method: 'POST', body: fd });
        if (r.ok) { const j = await r.json(); if (j.id) ids.push(j.id); }
      }
      if (into === 'character') patch({ ref_ids: [...spec.ref_ids, ...ids] });
      else {
        const cl = [...spec.clothing];
        cl[into] = { ...cl[into], ref_ids: [...(cl[into].ref_ids || []), ...ids] };
        patch({ clothing: cl });
      }
    } finally { setUpBusy(false); }
  };

  const addOutfit = () => patch({
    clothing: [...spec.clothing, { name: '', description: '', ref_ids: [], wearer: '' }],
  });

  return (
    <div style={{ ...card, marginBottom: 10 }}>
      <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 8 }}>
        <b style={{ fontSize: 13, color: '#e6e9ee' }}>
          {typeof index === 'number' ? `${index + 1}. ` : ''}Character
        </b>
        <div style={{ flex: 1 }} />
        {onRemove && <button style={btnSm} onClick={onRemove}>🗑 remove</button>}
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
        <div>
          <label style={lbl}>Name</label>
          <input style={input} value={spec.name} placeholder="e.g. Marla Quint"
                 onChange={(e) => patch({ name: e.target.value })} />
        </div>
        <div>
          <label style={lbl}>Class token (for the LoRA caption)</label>
          <input style={input} value={spec.class_token}
                 onChange={(e) => patch({ class_token: e.target.value })} />
        </div>
      </div>

      {/* ── where the character comes from ─────────────────────────────── */}
      <div style={{ marginTop: 10 }}>
        <label style={lbl}>
          Reference photos — if you have them
        </label>
        <div style={{ display: 'flex', gap: 6, alignItems: 'center', flexWrap: 'wrap' }}>
          <label style={{ ...btnSm, display: 'inline-block' }}>
            {upBusy ? 'uploading…' : '＋ add photos'}
            <input type="file" accept="image/*" multiple hidden
                   onChange={(e) => void upload(e.target.files, 'character')} />
          </label>
          {spec.ref_ids.map((id) => (
            <span key={id} style={{ position: 'relative' }}>
              <img src={`${BASE}/refs/${id}/image`} alt="" width={44} height={44}
                   style={{ objectFit: 'cover', borderRadius: 6, border: '1px solid #2a2f3a' }} />
              <button style={{ ...btnSm, padding: '0 4px', position: 'absolute', top: -6, right: -6 }}
                      onClick={() => patch({ ref_ids: spec.ref_ids.filter((x) => x !== id) })}>×</button>
            </span>
          ))}
          {!spec.ref_ids.length && <span style={hint}>none — it will be generated from the description below</span>}
        </div>
      </div>

      <div style={{ marginTop: 10 }}>
        <label style={lbl}>
          Description {spec.ref_ids.length ? '(adds detail to the description fields)'
                                           : '— REQUIRED when there are no photos'}
        </label>
        <textarea style={{ ...input, minHeight: 62, fontFamily: 'inherit' }}
                  value={spec.description}
                  placeholder="a 30 year old woman, dark curly hair to the shoulders, freckles, athletic build, olive canvas jacket, black jeans, brown boots"
                  onChange={(e) => patch({ description: e.target.value })} />
        <span style={hint}>
          ⚠ Say what IS there, never what is not — “no hat” puts a hat on. Avoid
          character or franchise names: they drag that character’s whole costume in.
        </span>
      </div>

      {/* ── stages ──────────────────────────────────────────────────────── */}
      <div style={{ marginTop: 12 }}>
        <label style={lbl}>Generate — the chain stops after the last one you tick</label>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 6 }}>
          <Stage on={spec.do_base} set={(v) => patch({ do_base: v })} icon="🧬"
                 name="Base character" note="front reference + active base" />
          <Stage on={spec.do_views} set={(v) => patch({ do_views: v })} icon="🧭"
                 name="Base views" note="front / back / left / right, verified" />
          <Stage on={spec.do_clothing} set={(v) => patch({ do_clothing: v })} icon="👗"
                 name="Clothing" note="design, adopt and wear the outfits below" />
          <Stage on={spec.do_charsheet} set={(v) => patch({ do_charsheet: v })} icon="🪪"
                 name="Character sheet" note="free — composited, no renders" />
          <Stage on={spec.do_dataset} set={(v) => patch({ do_dataset: v })} icon="🎓"
                 name="LoRA dataset" note="render, caption, QC, repair"
                 forcedBy={spec.do_lora ? 'the LoRA' : undefined} />
          <Stage on={spec.do_lora} set={(v) => patch({ do_lora: v })} icon="🚀"
                 name="Train the LoRA" note="export → train → score → install" />
        </div>
      </div>

      {/* ── clothing detail ─────────────────────────────────────────────── */}
      {spec.do_clothing && (
        <div style={{ marginTop: 10, paddingTop: 10, borderTop: '1px solid #2a2f3a' }}>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            <b style={{ fontSize: 12, color: '#e6e9ee' }}>👗 Outfits</b>
            <span style={hint}>describe a set, or let it invent some, or both</span>
            <div style={{ flex: 1 }} />
            <span style={hint}>invent</span>
            <input type="number" min={0} max={8} style={{ ...input, width: 56 }}
                   value={spec.clothing_auto_count}
                   onChange={(e) => patch({ clothing_auto_count: Number(e.target.value) || 0 })} />
            <button style={btnSm} onClick={addOutfit}>＋ describe one</button>
          </div>
          {spec.clothing.map((c, i) => (
            <div key={i} style={{ marginTop: 8, padding: 8, border: '1px solid #2a2f3a', borderRadius: 8 }}>
              <div style={{ display: 'flex', gap: 8 }}>
                <input style={{ ...input, width: 180 }} placeholder="outfit name" value={c.name}
                       onChange={(e) => {
                         const cl = [...spec.clothing]; cl[i] = { ...c, name: e.target.value };
                         patch({ clothing: cl });
                       }} />
                <input style={input} placeholder="a heavy olive canvas coat with brass buckles, dark trousers, tall brown boots"
                       value={c.description}
                       onChange={(e) => {
                         const cl = [...spec.clothing]; cl[i] = { ...c, description: e.target.value };
                         patch({ clothing: cl });
                       }} />
                <button style={btnSm} onClick={() => patch({
                  clothing: spec.clothing.filter((_, n) => n !== i),
                })}>×</button>
              </div>
              <div style={{ display: 'flex', gap: 6, alignItems: 'center', marginTop: 6 }}>
                <label style={{ ...btnSm, display: 'inline-block' }}>
                  ＋ clothing photo (optional)
                  <input type="file" accept="image/*" multiple hidden
                         onChange={(e) => void upload(e.target.files, i)} />
                </label>
                {(c.ref_ids || []).map((id) => (
                  <img key={id} src={`${BASE}/refs/${id}/image`} alt="" width={34} height={34}
                       style={{ objectFit: 'cover', borderRadius: 5, border: '1px solid #2a2f3a' }} />
                ))}
                <span style={hint}>
                  with a photo the garment is read OFF the image, so you needn’t describe it twice
                </span>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* ── knobs ───────────────────────────────────────────────────────── */}
      <div style={{ marginTop: 10, display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
        {!spec.ref_ids.length && (
          <span>
            <span style={hint}>candidates </span>
            <input type="number" min={1} max={8} style={{ ...input, width: 56, display: 'inline-block' }}
                   value={spec.candidates}
                   onChange={(e) => patch({ candidates: Number(e.target.value) || 4 })} />
          </span>
        )}
        {(spec.do_dataset || spec.do_lora) && (
          <span>
            <span style={hint}>dataset images </span>
            <input type="number" min={8} max={120} style={{ ...input, width: 68, display: 'inline-block' }}
                   value={spec.dataset_total}
                   onChange={(e) => patch({ dataset_total: Number(e.target.value) || 40 })} />
          </span>
        )}
        <label style={{ ...hint, display: 'flex', gap: 5, alignItems: 'center', cursor: 'pointer' }}>
          <input type="checkbox" checked={spec.stop_on_bad_base}
                 onChange={(e) => patch({ stop_on_bad_base: e.target.checked })} />
          stop if the base views fail their check (free, and they poison everything downstream)
        </label>
      </div>
    </div>
  );
}

/* ── the cost preview ───────────────────────────────────────────────────── */
function Estimate({ specs }: { specs: SpecT[] }): React.ReactElement | null {
  const [est, setEst] = useState<EstT[]>([]);
  const key = JSON.stringify(specs.map((s) => [
    s.ref_ids.length, s.do_base, s.do_views, s.do_clothing, s.do_charsheet,
    s.do_dataset, s.do_lora, s.clothing.length, s.clothing_auto_count,
    s.candidates, s.dataset_total,
  ]));
  useEffect(() => {
    let stop = false;
    void (async () => {
      const out: EstT[] = [];
      for (const s of specs) {
        try {
          const r = await fetch(`${BASE}/estimate`, {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(s),
          });
          if (r.ok) out.push(await r.json());
        } catch { /* the estimate is a courtesy, never a blocker */ }
      }
      if (!stop) setEst(out);
    })();
    return () => { stop = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);
  if (!est.length) return null;
  const renders = est.reduce((n, e) => n + (e.renders || 0), 0);
  const secs = est.reduce((n, e) => n + (e.seconds || 0), 0);
  const human = secs < 90 ? `${secs}s` : secs < 5400 ? `~${Math.round(secs / 60)} min`
    : `~${(secs / 3600).toFixed(1)} h`;
  return (
    <div style={{ ...card, borderColor: '#3b82f6' }}>
      <b style={{ fontSize: 13, color: '#e6e9ee' }}>💰 What this will cost</b>
      <div style={{ marginTop: 6, fontSize: 13, color: '#e6e9ee' }}>
        <b>{renders}</b> render{renders === 1 ? '' : 's'} across{' '}
        <b>{est.length}</b> character{est.length === 1 ? '' : 's'} · roughly <b>{human}</b>
      </div>
      <div style={{ marginTop: 6 }}>
        {(est[0]?.stages || []).map((row) => (
          <div key={row.stage} style={{ display: 'flex', gap: 8, fontSize: 12 }}>
            <span style={{ width: 84, color: '#cbd2dc' }}>{row.stage}</span>
            <span style={{ width: 46, color: row.renders ? '#e6e9ee' : '#5ee08a' }}>
              {row.renders || 'free'}
            </span>
            <span style={hint}>{row.note}</span>
          </div>
        ))}
        {est.length > 1 && <div style={hint}>(per-stage rows shown for the first character)</div>}
      </div>
      <div style={{ ...hint, marginTop: 6 }}>{est[0]?.caveat}</div>
    </div>
  );
}

/* ── the modal ──────────────────────────────────────────────────────────── */
export default function AutogenModal({ onClose, onQueued }: {
  onClose: () => void; onQueued?: () => void;
}): React.ReactElement {
  const [mode, setMode] = useState<'one' | 'batch'>('one');
  const [specs, setSpecs] = useState<SpecT[]>([blankSpec()]);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState('');

  const list = mode === 'one' ? specs.slice(0, 1) : specs;

  const errText = async (r: Response): Promise<string> => {
    const raw = await r.text().catch(() => '');
    try { return String(JSON.parse(raw)?.detail || raw); }
    catch { return `HTTP ${r.status} — ${(raw || 'no body').slice(0, 200)}`; }
  };

  const go = async () => {
    setBusy(true); setMsg('');
    try {
      const r = mode === 'one'
        ? await fetch(`${BASE}/run`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(list[0]),
        })
        : await fetch(`${BASE}/batch`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ characters: list, label: `batch of ${list.length}` }),
        });
      if (!r.ok) { setMsg(`❌ ${await errText(r)}`); return; }
      const j = await r.json();
      setMsg(`⚡ queued ${j.jobs?.length ?? 1} — watch the ⚡ Autogen board`);
      onQueued?.();
      window.setTimeout(onClose, 900);
    } catch (e) { setMsg(`❌ ${String((e as Error).message || e)}`); }
    finally { setBusy(false); }
  };

  return (
    <div style={{
      position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.72)', zIndex: 60,
      display: 'flex', alignItems: 'flex-start', justifyContent: 'center',
      overflow: 'auto', padding: '24px 12px',
    }} onClick={onClose}>
      <div style={{
        background: '#0b0e13', border: '1px solid #2a2f3a', borderRadius: 12,
        padding: 16, width: 860, maxWidth: '96vw',
      }} onClick={(e) => e.stopPropagation()}>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 4 }}>
          <h2 style={{ fontSize: 17, margin: 0, color: '#e6e9ee' }}>⚡ Autogen character</h2>
          <div style={{ flex: 1 }} />
          <button style={mode === 'one' ? btn : btnGhost} onClick={() => setMode('one')}>one</button>
          <button style={mode === 'batch' ? btn : btnGhost} onClick={() => setMode('batch')}>
            batch{specs.length > 1 ? ` (${specs.length})` : ''}
          </button>
        </div>
        <p style={{ ...hint, marginTop: 0 }}>
          Photos or a description in, and it runs the chain as far as you tick:
          base → views → clothing → sheet → dataset → LoRA. Batch runs characters
          strictly one after another, so they never fight over the same GPUs.
        </p>

        {list.map((s, i) => (
          <SpecForm key={i} spec={s} index={mode === 'batch' ? i : undefined}
                    set={(n) => setSpecs(specs.map((x, k) => (k === i ? n : x)))}
                    onRemove={mode === 'batch' && specs.length > 1
                      ? () => setSpecs(specs.filter((_, k) => k !== i)) : undefined} />
        ))}

        {mode === 'batch' && (
          <button style={{ ...btnGhost, marginBottom: 10 }}
                  onClick={() => setSpecs([...specs, blankSpec()])}>
            ＋ another character
          </button>
        )}

        <Estimate specs={list} />

        {msg && <p style={{ ...hint, color: msg.startsWith('❌') ? '#ff8a8a' : '#5ee08a' }}>{msg}</p>}

        <div style={{ display: 'flex', gap: 8, marginTop: 12 }}>
          <div style={{ flex: 1 }} />
          <button style={btnGhost} onClick={onClose}>cancel</button>
          <button style={btn} disabled={busy || !list.every((s) => s.name.trim())}
                  onClick={() => void go()}>
            {busy ? '⏳ queueing…' : mode === 'one' ? '⚡ Run it' : `⚡ Queue ${list.length}`}
          </button>
        </div>
      </div>
    </div>
  );
}
