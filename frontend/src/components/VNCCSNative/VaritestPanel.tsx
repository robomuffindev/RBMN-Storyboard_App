// v1.171 -- Settings Variation Test (Debug Options).
// Setup lightbox (test type + poses + axes + run budget) -> background run with
// live progress -> results grid (4/row, 👍/👎 on tiles and in the built-in
// lightbox) -> report (per-axis scores + suggestions, exportable as .md).
// Every run persists server-side under <project>/varitests/<id>/ so past runs
// stay reviewable.
import React, { useEffect, useRef, useState } from 'react';
import * as api from './vnccsNativeApi';

const OVERLAY: React.CSSProperties = {
  position: 'fixed', inset: 0, background: 'rgba(5,8,14,0.82)', zIndex: 300,
  display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 20,
};
const PANEL: React.CSSProperties = {
  background: '#12161d', border: '1px solid #33507e', borderRadius: 12,
  width: 'min(1180px, 96vw)', maxHeight: '92vh', overflowY: 'auto', padding: 18,
  color: '#e6e9ee', boxShadow: '0 0 30px rgba(59,130,246,0.25)',
};
const btn: React.CSSProperties = {
  background: '#3b82f6', color: '#fff', border: 'none', borderRadius: 6,
  padding: '8px 14px', fontSize: 13, cursor: 'pointer', fontWeight: 600,
};
const ghost: React.CSSProperties = { ...btn, background: 'transparent', border: '1px solid #2a2f3a', color: '#cbd2dc' };
const h3: React.CSSProperties = { margin: '0 0 10px', fontSize: 16 };
const hint: React.CSSProperties = { fontSize: 12, color: '#a8b2c0', margin: '4px 0' };
const chip = (on: boolean): React.CSSProperties => ({
  padding: '5px 10px', fontSize: 12, borderRadius: 6, cursor: 'pointer',
  border: `1px solid ${on ? '#3b82f6' : '#2a2f3a'}`,
  background: on ? '#1d2740' : '#0e1116', color: on ? '#cfe0ff' : '#9aa4b2',
});

// Curated axes per test family -- small value lists spanning the impactful
// dials (baseline is always run in addition).
const POSE_AXES: Array<{ key: string; label: string; values: unknown[] }> = [
  { key: 'klein_pose_steps', label: 'Pose steps', values: [8, 12, 16] },
  { key: 'klein_pose_lora_strength', label: 'Pose LoRA strength', values: ['0.6', '0.8', '1.0'] },
  { key: 'klein_pose_cleanup', label: 'Cleanup', values: ['off', 'gentle'] },
  { key: 'klein_pose_ref_end', label: 'Pose ref release', values: ['1', '0.85', '0.7'] },
  { key: 'klein_pose_input', label: 'Pose input (mannequin/skeleton/depth/normal)', values: ['', 'skeleton', 'depth', 'normal'] },
  { key: 'klein_consistency_lora', label: 'Consistency stack', values: ['on', 'off'] },
  { key: 'klein_pose_face_refine', label: 'Pose face refine', values: ['on', 'off'] },
];
const BASE_AXES: Array<{ key: string; label: string; values: unknown[] }> = [
  { key: 'klein_steps', label: 'Base steps', values: [6, 10, 14] },
  { key: 'klein_face_refine', label: 'Face refine', values: ['auto', 'off'] },
  { key: 'klein_face_refine_denoise', label: 'Face refine denoise', values: ['0.35', '0.55'] },
  { key: 'klein_pulid', label: 'PuLID', values: ['on', 'off'] },
  { key: 'klein_pulid_strength', label: 'PuLID strength', values: ['1.0', '1.4'] },
  { key: 'klein_cleanup', label: 'Cleanup', values: ['off', 'gentle'] },
];

export type VtCtxT = {
  characterName: string;
  characterInfo: Record<string, unknown>;
  clonerImages: Array<Record<string, unknown>> | null;
  background: string;
  faceKind?: string;
  styleCustom?: string;
  baseClothing?: string;
  canvasW?: number | null;
  nsfw?: boolean;
  poseOptions: Array<{ id: string; name: string; pose: Record<string, unknown>; thumb?: string }>;
  defaultType: 'base_new' | 'base_clone' | 'pose_set';
};

export default function VaritestPanel({ ctx, onClose }: { ctx: VtCtxT; onClose: () => void }) {
  const [view, setView] = useState<'setup' | 'progress' | 'results' | 'list' | 'report'>('setup');
  const [testType, setTestType] = useState<'base_new' | 'base_clone' | 'pose_set'>(ctx.defaultType);
  const [selPoses, setSelPoses] = useState<Set<string>>(new Set(ctx.poseOptions.slice(0, 2).map((p) => p.id)));
  const [axesOn, setAxesOn] = useState<Set<string>>(new Set(
    ctx.defaultType === 'pose_set'
      ? ['klein_pose_steps', 'klein_pose_lora_strength']
      : ['klein_steps', 'klein_face_refine_denoise']));
  const [maxRuns, setMaxRuns] = useState(12);
  const [sameSeed, setSameSeed] = useState(true);
  const [run, setRun] = useState<api.VtManifestT | null>(null);
  const [runs, setRuns] = useState<Array<{ id: string; created: string; test_type: string; character: string;
    status: string; progress: { done: number; total: number }; rated: number }>>([]);
  const [lightIdx, setLightIdx] = useState<number | null>(null);
  const [report, setReport] = useState<Awaited<ReturnType<typeof api.varitestReport>>['analysis'] | null>(null);
  const [err, setErr] = useState('');
  const [busy, setBusy] = useState(false);
  const pollRef = useRef<number | null>(null);

  const axes = testType === 'pose_set' ? POSE_AXES : BASE_AXES;
  const comboCount = axes.filter((a) => axesOn.has(a.key))
    .reduce((n, a) => n * a.values.length, 1);
  const variations = Math.min(comboCount, Math.max(1, maxRuns - 1)) + 1;
  const perVar = testType === 'pose_set' ? Math.max(1, selPoses.size) : 1;
  const totalImgs = variations * perVar;

  useEffect(() => () => { if (pollRef.current) window.clearInterval(pollRef.current); }, []);

  const pollRun = (id: string) => {
    if (pollRef.current) window.clearInterval(pollRef.current);
    const tick = async () => {
      try {
        const m = await api.varitestGet(id);
        setRun(m);
        if (m.status !== 'running' && pollRef.current) {
          window.clearInterval(pollRef.current);
          pollRef.current = null;
        }
      } catch { /* transient */ }
    };
    void tick();
    pollRef.current = window.setInterval(tick, 3000);
  };

  const start = async () => {
    setErr(''); setBusy(true);
    try {
      const chosenAxes: Record<string, unknown[]> = {};
      for (const a of axes) if (axesOn.has(a.key)) chosenAxes[a.key] = a.values;
      const poses = testType === 'pose_set'
        ? ctx.poseOptions.filter((p) => selPoses.has(p.id)) : [];
      const r = await api.varitestStart({
        character_name: ctx.characterName,
        test_type: testType,
        axes: chosenAxes,
        max_runs: maxRuns,
        same_seed: sameSeed,
        poses: poses.map((p) => p.pose),
        pose_names: poses.map((p) => p.name),
        character_info: ctx.characterInfo,
        cloner_images: ctx.clonerImages || undefined,
        nsfw: !!ctx.nsfw,
        background: ctx.background,
        face_kind: ctx.faceKind, style_custom: ctx.styleCustom,
        base_clothing: ctx.baseClothing, canvas_w: ctx.canvasW || undefined,
      });
      setView('progress');
      pollRun(r.id);
    } catch (e) { setErr(`Start failed: ${(e as Error).message}`); }
    finally { setBusy(false); }
  };

  const rate = async (index: number, rating: number) => {
    if (!run) return;
    const cur = run.items.find((it) => it.index === index)?.rating || 0;
    const next = cur === rating ? 0 : rating;   // click again to clear
    setRun((r) => r ? { ...r, items: r.items.map((it) => it.index === index ? { ...it, rating: next } : it) } : r);
    try { await api.varitestRate(run.id, index, next); } catch { /* best-effort */ }
  };

  const openReport = async () => {
    if (!run) return;
    setBusy(true);
    try { setReport((await api.varitestReport(run.id)).analysis); setView('report'); }
    catch (e) { setErr(`Report failed: ${(e as Error).message}`); }
    finally { setBusy(false); }
  };

  const ovLabel = (ov: Record<string, unknown>, baseline?: boolean) =>
    baseline ? 'BASELINE (current settings)'
      : Object.entries(ov).map(([k, v]) => `${k.replace('klein_', '').replace(/_/g, ' ')}=${v === '' ? 'def' : v}`).join(' · ') || '—';

  const thumbBtns = (it: api.VtItemT, big = false) => (
    <div style={{ display: 'flex', gap: 6, justifyContent: 'center', marginTop: 4 }}>
      <button style={{ ...ghost, padding: big ? '6px 16px' : '3px 12px', fontSize: big ? 15 : 13,
                       borderColor: it.rating > 0 ? '#166534' : '#2a2f3a',
                       background: it.rating > 0 ? 'rgba(22,101,52,0.35)' : 'transparent' }}
              onClick={(e) => { e.stopPropagation(); void rate(it.index, 1); }}>👍</button>
      <button style={{ ...ghost, padding: big ? '6px 16px' : '3px 12px', fontSize: big ? 15 : 13,
                       borderColor: it.rating < 0 ? '#b91c1c' : '#2a2f3a',
                       background: it.rating < 0 ? 'rgba(185,28,28,0.30)' : 'transparent' }}
              onClick={(e) => { e.stopPropagation(); void rate(it.index, -1); }}>👎</button>
    </div>
  );

  const items = (run?.items || []).filter((it) => it.file);
  const lightItem = lightIdx !== null ? items.find((it) => it.index === lightIdx) : null;

  return (
    <div style={OVERLAY} onClick={onClose}>
      <div style={PANEL} onClick={(e) => e.stopPropagation()}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 8 }}>
          <h3 style={{ ...h3, margin: 0, flex: 1 }}>🧪 Settings Variation Test</h3>
          {view !== 'setup' && <button style={ghost} onClick={() => { setView('setup'); }}>＋ New test</button>}
          <button style={ghost} onClick={async () => {
            try { setRuns((await api.varitestList()).runs); setView('list'); } catch { /* offline */ }
          }}>📜 Past runs</button>
          <button style={ghost} onClick={onClose}>✕ Close</button>
        </div>
        {err && <p style={{ color: '#ff8a8a', fontSize: 13 }}>⚠ {err}</p>}

        {view === 'setup' && (
          <div style={{ display: 'grid', gap: 14 }}>
            <p style={hint}>
              Renders a batch of images across setting variations (plus the BASELINE = your current settings),
              all with ONE shared seed so only the settings differ. Walk away, come back, 👍/👎 what you see,
              and the report tells you which dials moved quality which way. Runs save to the app's
              varitests folder with full generation info.
            </p>
            <div>
              <div style={{ fontWeight: 700, fontSize: 13, marginBottom: 5 }}>Test type</div>
              <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                {([['base_new', '🧍 Base — new character'], ['base_clone', '🧬 Base — clone'],
                   ['pose_set', '🕺 Pose set']] as const).map(([v, l]) => (
                  <span key={v} style={chip(testType === v)} onClick={() => {
                    setTestType(v);
                    setAxesOn(new Set(v === 'pose_set'
                      ? ['klein_pose_steps', 'klein_pose_lora_strength']
                      : ['klein_steps', 'klein_face_refine_denoise']));
                  }}>{l}</span>
                ))}
              </div>
              <p style={hint}>
                {testType === 'pose_set'
                  ? 'Each variation renders the selected poses through the real Klein pose pipeline (locked to the ACTIVE base).'
                  : 'Each variation renders the standard neutral BASE pose through the real preview pipeline — nothing is cataloged as a version.'}
              </p>
            </div>
            {testType === 'pose_set' && (
              <div>
                <div style={{ fontWeight: 700, fontSize: 13, marginBottom: 5 }}>
                  Poses ({selPoses.size} selected — 1–2 recommended; every pose multiplies the run count)
                </div>
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill,minmax(72px,1fr))', gap: 6, maxHeight: 220, overflowY: 'auto' }}>
                  {ctx.poseOptions.map((p) => {
                    const on = selPoses.has(p.id);
                    return (
                      <div key={p.id} onClick={() => setSelPoses((prev) => {
                          const n = new Set(prev); if (on) n.delete(p.id); else n.add(p.id); return n; })}
                           style={{ border: `2px solid ${on ? '#3b82f6' : '#2a2f3a'}`, borderRadius: 6,
                                    background: '#0e1116', padding: 3, textAlign: 'center', cursor: 'pointer',
                                    opacity: on ? 1 : 0.55 }}>
                        {p.thumb
                          ? <img src={p.thumb} alt={p.name} style={{ width: '100%', borderRadius: 4 }} />
                          : <div style={{ height: 70, display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 11, color: '#a8b2c0' }}>{p.name}</div>}
                        <div style={{ fontSize: 10, color: on ? '#cbd2dc' : '#6b7280' }}>{p.name}</div>
                      </div>
                    );
                  })}
                </div>
              </div>
            )}
            <div>
              <div style={{ fontWeight: 700, fontSize: 13, marginBottom: 5 }}>
                Settings to vary (each adds its listed values to the grid; keep it to 2–3 axes for a focused run)
              </div>
              <div style={{ display: 'grid', gap: 5 }}>
                {axes.map((a) => {
                  const on = axesOn.has(a.key);
                  return (
                    <label key={a.key} style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 12.5,
                                                color: on ? '#dbe6f5' : '#8a94a6', cursor: 'pointer' }}>
                      <input type="checkbox" checked={on} onChange={() => setAxesOn((prev) => {
                        const n = new Set(prev); if (on) n.delete(a.key); else n.add(a.key); return n; })} />
                      <b>{a.label}</b>
                      <span style={{ color: '#7f8a99' }}>[{a.values.map((v) => v === '' ? 'def' : String(v)).join(', ')}]</span>
                    </label>
                  );
                })}
              </div>
            </div>
            <div style={{ display: 'flex', gap: 14, alignItems: 'center', flexWrap: 'wrap' }}>
              <div>
                <div style={{ fontWeight: 700, fontSize: 13, marginBottom: 4 }}>Run budget</div>
                <div style={{ display: 'flex', gap: 5 }}>
                  {[8, 12, 16, 24].map((n) => (
                    <span key={n} style={chip(maxRuns === n)} onClick={() => setMaxRuns(n)}>{n}</span>
                  ))}
                </div>
              </div>
              <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12.5, color: '#dbe6f5' }}>
                <input type="checkbox" checked={sameSeed} onChange={(e) => setSameSeed(e.target.checked)} />
                🎲 One shared seed (recommended — isolates the settings)
              </label>
            </div>
            <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
              <button style={{ ...btn, opacity: busy ? 0.5 : 1 }} disabled={busy} onClick={start}>
                ▶ Run test — {variations} variation{variations === 1 ? '' : 's'} × {perVar} = {totalImgs} image{totalImgs === 1 ? '' : 's'}
              </button>
              <span style={hint}>grid of {comboCount} combos, sampled to fit the budget; baseline always included</span>
            </div>
          </div>
        )}

        {view === 'progress' && run && (() => {
          const pct = run.progress.total ? Math.round((run.progress.done / run.progress.total) * 100) : 0;
          return (
            <div style={{ display: 'grid', gap: 10 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                {run.status === 'running' ? (
                  <span style={{ width: 16, height: 16, borderRadius: '50%',
                                 border: '2px solid rgba(59,130,246,0.25)', borderTopColor: '#3b82f6',
                                 display: 'inline-block', animation: 'rbmnSpin 0.9s linear infinite' }} />
                ) : <span style={{ fontSize: 16 }}>{run.status === 'done' ? '✅' : '⚠'}</span>}
                <style>{'@keyframes rbmnSpin { to { transform: rotate(360deg); } }'}</style>
                <b style={{ fontSize: 14 }}>
                  {run.status === 'running' ? `Generating variations… ${run.progress.done}/${run.progress.total} · ${pct}%`
                    : run.status === 'done' ? `Done — ${run.progress.done} renders ready to review`
                    : `${run.status}${run.error ? ` — ${run.error}` : ''}`}
                </b>
              </div>
              <div style={{ height: 12, background: '#0a0d12', border: '1px solid #2a2f3a', borderRadius: 6, overflow: 'hidden' }}>
                <div style={{ height: '100%', width: `${pct}%`, transition: 'width 0.6s',
                              background: pct === 100 ? '#166534' : 'linear-gradient(90deg,#2563eb,#60a5fa)' }} />
              </div>
              <div style={{ display: 'flex', gap: 8 }}>
                {run.status === 'running' && (
                  <button style={{ ...ghost, color: '#ff8a8a', borderColor: '#7f1d1d' }}
                          onClick={() => { void api.varitestCancel(run.id); }}>■ Cancel</button>
                )}
                {(run.items || []).some((it) => it.file) && (
                  <button style={btn} onClick={() => setView('results')}>
                    {run.status === 'done' ? '🖼 Review results' : `🖼 Review ${items.length} so far`}
                  </button>
                )}
              </div>
              <p style={hint}>You can close this — the run continues server-side. Find it again under 📜 Past runs.</p>
            </div>
          );
        })()}

        {view === 'results' && run && (
          <div>
            <div style={{ display: 'flex', gap: 10, alignItems: 'center', marginBottom: 8, flexWrap: 'wrap' }}>
              <span style={{ fontSize: 12.5, color: '#a8b2c0' }}>
                {run.character} · {run.test_type} · {items.length} renders ·
                rated {items.filter((it) => it.rating).length} ·
                seed {run.same_seed ? run.seed : 'varied'}
              </span>
              <div style={{ flex: 1 }} />
              <button style={btn} onClick={openReport}>📊 Build report</button>
              <a style={{ ...ghost, textDecoration: 'none' }} href={api.varitestReportMdUrl(run.id)}
                 download={`varitest_${run.id}.md`}>⬇ Report .md</a>
            </div>
            <p style={hint}>👍 what looks good, 👎 what doesn't (click again to clear). You don't need to rate everything — a handful per value is enough for the report.</p>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 10 }}>
              {items.map((it) => (
                <div key={it.index} style={{ background: '#0e1116', border: `1px solid ${it.baseline ? '#34518a' : '#232936'}`,
                                             borderRadius: 8, padding: 6 }}>
                  <img src={api.varitestImageUrl(run.id, it.index)} alt={`#${it.index}`}
                       onClick={() => setLightIdx(it.index)}
                       style={{ width: '100%', borderRadius: 6, cursor: 'zoom-in', display: 'block', background: '#0a0d12' }} />
                  <div style={{ fontSize: 10.5, color: it.baseline ? '#8ab4ff' : '#8a94a6', marginTop: 4,
                                lineHeight: 1.35, minHeight: 26 }}>
                    #{it.index}{it.pose_name ? ` · ${it.pose_name}` : ''} — {ovLabel(it.overrides, it.baseline)}
                  </div>
                  {thumbBtns(it)}
                </div>
              ))}
            </div>
          </div>
        )}

        {view === 'report' && run && report && (
          <div style={{ display: 'grid', gap: 12 }}>
            <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
              <b>📊 Report — {report.rated}/{report.total} rated</b>
              <div style={{ flex: 1 }} />
              <a style={{ ...ghost, textDecoration: 'none' }} href={api.varitestReportMdUrl(run.id)}
                 download={`varitest_${run.id}.md`}>⬇ Export .md (for an LLM)</a>
              <button style={ghost} onClick={() => setView('results')}>← Back to images</button>
            </div>
            {report.suggestions.length ? (
              <div style={{ border: '1px solid #166534', background: 'rgba(22,101,52,0.12)', borderRadius: 8, padding: 10 }}>
                <b style={{ fontSize: 13 }}>Suggested settings</b>
                {report.suggestions.map((sg) => (
                  <div key={sg.setting} style={{ fontSize: 12.5, marginTop: 4 }}>
                    <b>{sg.setting}</b> → use <code>{String(sg.use)}</code>, avoid <code>{String(sg.avoid)}</code>
                    <span style={{ color: '#7f8a99' }}> ({sg.confidence})</span>
                  </div>
                ))}
              </div>
            ) : (
              <p style={hint}>Not enough rated data for suggestions yet — rate at least 2 images per value on the axes you care about, then rebuild.</p>
            )}
            {Object.entries(report.axis_tables).map(([key, rows]) => (
              <div key={key}>
                <b style={{ fontSize: 13 }}>{key}</b>
                <table style={{ width: '100%', fontSize: 12, borderCollapse: 'collapse', marginTop: 4 }}>
                  <thead><tr style={{ color: '#8a94a6', textAlign: 'left' }}>
                    <th style={{ padding: '3px 8px' }}>value</th><th>rated</th><th>👍</th><th>👎</th><th>score</th></tr></thead>
                  <tbody>
                    {rows.map((r, i) => (
                      <tr key={i} style={{ borderTop: '1px solid #232936',
                                           color: r.score !== null && r.score > 0 ? '#8fe6ae' : r.score !== null && r.score < 0 ? '#ffb4b4' : '#cbd2dc' }}>
                        <td style={{ padding: '3px 8px' }}>{r.value === '' ? 'def' : String(r.value)}</td>
                        <td>{r.rated}</td><td>{r.ups}</td><td>{r.downs}</td>
                        <td>{r.score !== null ? r.score.toFixed(2) : '—'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ))}
          </div>
        )}

        {view === 'list' && (
          <div style={{ display: 'grid', gap: 6 }}>
            {!runs.length && <p style={hint}>No past test runs yet.</p>}
            {runs.map((r) => (
              <div key={r.id} style={{ display: 'flex', alignItems: 'center', gap: 10, fontSize: 12.5,
                                       background: '#0e1116', border: '1px solid #232936', borderRadius: 8, padding: '8px 10px' }}>
                <span style={{ fontWeight: 700, color: '#8ab4ff' }}>{r.test_type}</span>
                <span>{r.character}</span>
                <span style={{ color: '#7f8a99' }}>{(r.created || '').slice(0, 16).replace('T', ' ')}</span>
                <span style={{ color: r.status === 'done' ? '#5ee08a' : r.status === 'running' ? '#8ab4ff' : '#ffb4b4' }}>
                  {r.status} · {r.progress?.done}/{r.progress?.total} · {r.rated} rated
                </span>
                <div style={{ flex: 1 }} />
                <button style={ghost} onClick={async () => {
                  try {
                    const m = await api.varitestGet(r.id);
                    setRun(m);
                    if (m.status === 'running') { setView('progress'); pollRun(r.id); }
                    else setView('results');
                  } catch { /* gone */ }
                }}>Open</button>
              </div>
            ))}
          </div>
        )}

        {lightItem && run && (
          <div style={{ position: 'fixed', inset: 0, background: 'rgba(3,5,9,0.93)', zIndex: 320,
                        display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: 10 }}
               onClick={() => setLightIdx(null)}>
            <img src={api.varitestImageUrl(run.id, lightItem.index)} alt="large"
                 onClick={(e) => e.stopPropagation()}
                 style={{ maxWidth: '86vw', maxHeight: '78vh', borderRadius: 8, border: '1px solid #2a2f3a' }} />
            <div onClick={(e) => e.stopPropagation()} style={{ textAlign: 'center' }}>
              <div style={{ fontSize: 12.5, color: '#cbd2dc', maxWidth: 800 }}>
                #{lightItem.index}{lightItem.pose_name ? ` · ${lightItem.pose_name}` : ''} — {ovLabel(lightItem.overrides, lightItem.baseline)}
                <span style={{ color: '#7f8a99' }}> · seed {lightItem.seed} · {lightItem.elapsed}s · {lightItem.host}</span>
              </div>
              {thumbBtns(lightItem, true)}
              <div style={{ display: 'flex', gap: 8, justifyContent: 'center', marginTop: 8 }}>
                <button style={ghost} onClick={() => {
                  const i = items.findIndex((x) => x.index === lightItem.index);
                  if (i > 0) setLightIdx(items[i - 1].index);
                }}>‹ Prev</button>
                <button style={ghost} onClick={() => {
                  const i = items.findIndex((x) => x.index === lightItem.index);
                  if (i < items.length - 1) setLightIdx(items[i + 1].index);
                }}>Next ›</button>
                <button style={ghost} onClick={() => setLightIdx(null)}>✕ Close</button>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
