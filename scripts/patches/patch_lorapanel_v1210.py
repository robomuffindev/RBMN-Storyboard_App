"""v1.210.0 — re-render flagged + auto-repair loop in the panel (LoraPanel.tsx).

Adds: a flag BREAKDOWN line (so 15/40 can be read, not guessed), a
"🔁 Re-render flagged" button, and "♻️ Repair until clean" with a round picker,
live round/phase status, per-round history and a stuck-image warning.

Exact-match patcher: every replacement asserts exactly ONE occurrence.
Usage: python patch_lorapanel_v1210.py <path-to-LoraPanel.tsx>
"""
import sys
from pathlib import Path

p = Path(sys.argv[1])
src = p.read_text("utf-8")
orig = src


def rep(old: str, new: str, label: str) -> None:
    global src
    n = src.count(old)
    assert n == 1, f"{label}: expected 1 occurrence, found {n}"
    src = src.replace(old, new)
    print(f"  ok  {label}")


# ── 1. types ──────────────────────────────────────────────────────────────
rep(
    """interface RunT {
  status: string; kind?: string; done: number; total: number; detail?: string;
  error?: string | null; tasks?: Record<string, { worker?: string | null; server?: string | null; status: string }>;
  workers?: string[];
}""",
    """interface RunT {
  status: string; kind?: string; done: number; total: number; detail?: string;
  error?: string | null; tasks?: Record<string, { worker?: string | null; server?: string | null; status: string }>;
  workers?: string[]; round?: number; rounds?: number; phase?: string;
  history?: Array<{ round: number; rendered: number; flagged: number | null }>;
  summary?: FlagsT;
}
interface FlagsT {
  flagged: number; checked: number; artifacts: number; cropped_badly: number;
  framing_off: number; angle_off: number; expression_off: number;
  not_one_person: number; face_unclear: number; stuck: number;
  top_issues?: Record<string, number>;
}""",
    "RunT + FlagsT",
)
rep(
    """  items: ItemT[]; run?: RunT | null; exports?: string[];
}""",
    """  items: ItemT[]; run?: RunT | null; exports?: string[];
  flags?: FlagsT; max_attempts?: number;
}""",
    "DatasetT.flags",
)
rep(
    """  width: number; height: number; identity?: string; keep?: boolean;
}""",
    """  width: number; height: number; identity?: string; keep?: boolean; attempts?: number;
}""",
    "ItemT.attempts",
)

# ── 2. state ─────────────────────────────────────────────────────────────
rep(
    """  const [editId, setEditId] = useState('');""",
    """  const [rounds, setRounds] = useState(3);
  const [retryStuck, setRetryStuck] = useState(false);
  const [editId, setEditId] = useState('');""",
    "state",
)

# ── 3. the flag breakdown + repair controls ─────────────────────────────
rep(
    """                {run?.status === 'running' && (
                  <p style={{ ...hint, margin: '8px 0 0' }}>
                    ⏳ {run.kind} {run.detail}""",
    """                {counts.flagged > 0 && (
                  <div style={{ marginTop: 10, borderTop: '1px solid #2a2f3a', paddingTop: 10,
                                display: 'grid', gap: 6 }}>
                    <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
                      <b style={{ fontSize: 12, color: '#e0b36a' }}>⚠ {counts.flagged} flagged</b>
                      {ds.flags && (
                        <span style={hint}>
                          {[
                            ds.flags.artifacts ? `${ds.flags.artifacts} artifacts` : '',
                            ds.flags.cropped_badly ? `${ds.flags.cropped_badly} bad crop` : '',
                            ds.flags.framing_off ? `${ds.flags.framing_off} wrong framing` : '',
                            ds.flags.angle_off ? `${ds.flags.angle_off} wrong angle` : '',
                            ds.flags.expression_off ? `${ds.flags.expression_off} wrong expression` : '',
                            ds.flags.not_one_person ? `${ds.flags.not_one_person} not one person` : '',
                          ].filter(Boolean).join(' · ')}
                        </span>
                      )}
                    </div>
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
                {run?.status === 'running' && (
                  <p style={{ ...hint, margin: '8px 0 0' }}>
                    ⏳ {run.kind}{run.round ? ` round ${run.round}/${run.rounds} · ${run.phase}` : ''} {run.detail}""",
    "flag breakdown + repair controls",
)

# ── 4. per-round history + attempt badge ───────────────────────────────
rep(
    """                {run && run.status !== 'running' && run.error && <p style={errTxt}>{run.error}</p>}""",
    """                {!!run?.history?.length && (
                  <p style={{ ...hint, margin: '4px 0 0' }}>
                    {run.history.map((h) => `round ${h.round}: ${h.rendered} re-rendered${h.flagged === null ? '' : ` → ${h.flagged} flagged`}`).join('  ·  ')}
                  </p>
                )}
                {run && run.status !== 'running' && run.error && <p style={errTxt}>{run.error}</p>}""",
    "round history",
)
rep(
    """                          {it.identity && <span style={hint}>🧭 {it.identity}</span>}""",
    """                          {it.identity && <span style={hint}>🧭 {it.identity}</span>}
                          {!!it.attempts && it.attempts > 1 && (
                            <span style={{ ...hint, color: (it.attempts >= (ds.max_attempts || 3)) ? '#e0b36a' : '#8d97a5' }}
                                  title="how many times this row has been rendered">
                              ×{it.attempts}
                            </span>
                          )}""",
    "attempt badge",
)

assert src != orig
p.write_text(src, "utf-8")
print(f"patched {p} ({len(orig)} -> {len(src)} bytes)")
