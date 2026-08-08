"""v1.225 — the UI for v1.217's dressed/stripped base mode.

He went looking for this in Klein 3.0 and found nothing, because v1.217 shipped
backend-only and I buried that at the bottom of a long message.  This is the
control: a three-way toggle sitting directly above the Strip card, showing what
each of the four views WOULD resolve to before anything is rendered.
"""
import sys
from pathlib import Path

P = Path(sys.argv[1] if len(sys.argv) > 1 else "Klein3Panel.tsx")
src = P.read_text("utf-8")
orig = src


def rep(old, new, why):
    global src
    n = src.count(old)
    assert n == 1, f"{why}: expected 1 match, found {n}"
    src = src.replace(old, new, 1)


# ── 1. types ────────────────────────────────────────────────────────────────
rep('''  active_base?: string | null; jobs?: Record<string, JobT>; updated_at?: string;
}''',
    '''  active_base?: string | null; jobs?: Record<string, JobT>; updated_at?: string;
  base_mode?: string;                       // auto | dressed | stripped (v1.217)
  base_sources?: Record<string, string>;    // view -> which image actually wins
}''',
    "CharT: base mode fields")

# ── 2. state ────────────────────────────────────────────────────────────────
rep("const TAGS = ['front', 'back', 'left', 'right', 'face', 'outfit', 'other'];",
    "const TAGS = ['front', 'back', 'left', 'right', 'face', 'outfit', 'other'];\n"
    "// the four the base picker actually resolves (v1.217 VIEW_TAGS)\n"
    "const VIEWS = ['front', 'back', 'left', 'right'];",
    "VIEWS constant")

rep('''  const [stripMode, setStripMode] = useState<'underwear' | 'nude'>('underwear');''',
    '''  const [baseMode, setBaseMode] = useState<'auto' | 'dressed' | 'stripped'>('auto');
  const [baseResolve, setBaseResolve] = useState<Record<string, { found: boolean; source: string }> | null>(null);
  const [baseBusy, setBaseBusy] = useState(false);
  const [stripMode, setStripMode] = useState<'underwear' | 'nude'>('underwear');''',
    "state")

# ── 3. the call ─────────────────────────────────────────────────────────────
rep('''  const doStrip = async () => {''',
    '''  // v1.225: PUT returns `resolves_to` for every view, so the consequence of the
  // toggle is visible BEFORE a render is spent on it.
  const setMode = async (m: 'auto' | 'dressed' | 'stripped') => {
    setMsg(''); setBaseBusy(true); setBaseMode(m);
    try {
      const r = await j<{ mode: string; resolves_to: Record<string, { found: boolean; source: string }> }>(
        await fetch(`${BASE}/characters/${slug}/base-mode`, {
          method: 'PUT', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ mode: m }),
        }));
      setBaseResolve(r.resolves_to);
      await loadCur();
    } catch (e) { setMsg((e as Error).message); }
    finally { setBaseBusy(false); }
  };

  const doStrip = async () => {''',
    "setMode")

# ── 4. keep the toggle in sync with whatever the character says ─────────────
rep('''  const doUpscale = async () => {''',
    '''  useEffect(() => {
    if (cur?.base_mode) setBaseMode(cur.base_mode as 'auto' | 'dressed' | 'stripped');
    if (cur?.base_sources) {
      setBaseResolve(Object.fromEntries(Object.entries(cur.base_sources)
        .map(([v, s]) => [v, { found: !String(s).startsWith('active base (no'), source: String(s) }])));
    }
  }, [cur?.slug, cur?.base_mode, cur?.updated_at]);

  const doUpscale = async () => {''',
    "sync from character")

# ── 5. the card, directly above Strip ───────────────────────────────────────
rep('''        <div style={{ border: '1px solid #2a2f3a', borderRadius: 8, padding: 10, display: 'grid', gap: 8 }}>
          <b style={{ fontSize: 13, color: '#e6e9ee' }}>👙 Strip → base set</b>''',
    '''        <div style={{ border: '1px solid #2a2f3a', borderRadius: 8, padding: 10, display: 'grid', gap: 8 }}>
          <b style={{ fontSize: 13, color: '#e6e9ee' }}>🧥 Identity source — dressed or stripped</b>
          <p style={{ ...hint, margin: 0 }}>
            Which image every render starts from. <b>Dressed</b> uses your uploaded references and
            generated views, so his own clothes are kept and nothing has to be stripped.
            <b> Stripped</b> uses the stripped base set (needed when Klein must replace the
            clothing outright). Stripping is an extra edit per view and adds its own drift, so
            skip it when the shot never needed a clothing change.
          </p>
          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
            {([
              ['dressed', '🧥 Dressed', 'His own clothes, from your references'],
              ['stripped', '👙 Stripped', 'The stripped base set'],
              ['auto', '🤖 Auto', 'Newest version of each view (pre-v1.217 behaviour)'],
            ] as const).map(([m, label, tip]) => (
              <button key={m} title={tip}
                      style={baseMode === m ? { ...btn, background: '#2b6cb0' } : btnGhost}
                      disabled={!cur || baseBusy}
                      onClick={() => void setMode(m)}>{label}</button>
            ))}
            {baseBusy && <span style={{ ...hint, alignSelf: 'center' }}>working…</span>}
          </div>
          {baseResolve && (
            <div style={{ display: 'grid', gap: 2 }}>
              <span style={{ ...hint, margin: 0 }}>What each view would use right now:</span>
              {VIEWS.map((v) => {
                const r = baseResolve[v];
                if (!r) return null;
                const stripped = /stripped/.test(r.source);
                const fallback = /fallback|no .* view yet|active base/.test(r.source);
                return (
                  <div key={v} style={{ display: 'flex', gap: 6, fontSize: 11, fontFamily: 'monospace' }}>
                    <span style={{ color: '#8fa6bd', width: 46 }}>{v}</span>
                    <span style={{ color: fallback ? '#e0b36a' : stripped ? '#cbd2dc' : '#5ee08a' }}>
                      {r.source}
                    </span>
                  </div>
                );
              })}
              <span style={{ ...hint, margin: '2px 0 0' }}>
                Green = a real reference for that view. Amber = falling back, generate that view
                or tag a reference for it.
              </span>
            </div>
          )}
        </div>

        <div style={{ border: '1px solid #2a2f3a', borderRadius: 8, padding: 10, display: 'grid', gap: 8 }}>
          <b style={{ fontSize: 13, color: '#e6e9ee' }}>👙 Strip → base set</b>''',
    "the base-mode card")

assert src != orig
P.write_text(src, "utf-8")
print(f"patched {P}  ({len(orig)} -> {len(src)} bytes)")
