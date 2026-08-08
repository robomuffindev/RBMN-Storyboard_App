"""v1.226 — "QC pass" looked like it did nothing, because it did nothing.

The button posted `{}`, so `overwrite` defaulted to false, and the route only
targets images with NO qc yet:

    targets = [... and (body.overwrite or not it.get("qc"))]
    if not targets: return {"started": False, "note": "nothing to check ..."}

On a set that has already been checked once that is every image, so the call
returned instantly having done nothing.  The explanation WAS returned — and
rendered at the very top of the page, hundreds of pixels above the button he
had just clicked, below the fold.  Silent no-op plus off-screen feedback.

Three fixes, in order of how much they matter:

  1. "QC pass" re-checks.  That is what the words mean, and it is what every
     other button in that bar already does (Caption all passes overwrite:true).
  2. Immediate feedback on the BUTTON.  There is a gap between the click and the
     progress banner appearing; the button now shows it is working straight away
     and every other action disables while one is in flight.
  3. Messages appear NEXT TO the action bar, not only at the top of the page.
     A response you cannot see is not feedback.
"""
import sys
from pathlib import Path

P = Path(sys.argv[1] if len(sys.argv) > 1 else "LoraPanel.tsx")
src = P.read_text("utf-8")
orig = src


def rep(old, new, why):
    global src
    n = src.count(old)
    assert n == 1, f"{why}: expected 1 match, found {n}"
    src = src.replace(old, new, 1)


# ── 1. in-flight state ─────────────────────────────────────────────────────
rep('''  const [autoSize, setAutoSize] = useState(true);''',
    '''  const [autoSize, setAutoSize] = useState(true);
  const [acting, setActing] = useState('');   // the path of the request in flight''',
    "acting state")

rep('''  const post = async (path: string, body?: unknown, note?: string) => {
    setErr(''); setMsg('');
    try {
      const r = await j<Record<string, unknown>>(await fetch(`${BASE}${path}`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body || {}),
      }));
      if ((r as { started?: boolean }).started === false) setMsg(String(r.note || 'nothing to do'));
      else if (note) setMsg(note);
      await refreshDs(); await load();
      return r;
    } catch (e) { setErr((e as Error).message); return null; }
  };''',
    '''  const post = async (path: string, body?: unknown, note?: string) => {
    setErr(''); setMsg(''); setActing(path);
    try {
      const r = await j<Record<string, unknown>>(await fetch(`${BASE}${path}`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body || {}),
      }));
      // A `started: false` reply is the server saying it had nothing to do. That
      // is the most confusing outcome there is, so it must be LOUD, not a line
      // at the top of the page.
      if ((r as { started?: boolean }).started === false) setMsg(`ℹ ${String(r.note || 'nothing to do')}`);
      else if (note) setMsg(note);
      await refreshDs(); await load();
      return r;
    } catch (e) { setErr((e as Error).message); return null; }
    finally { setActing(''); }
  };''',
    "post: track in-flight + louder no-op")

# ── 2. QC re-checks, and every action bar button reports itself ────────────
rep('''                  <button style={btnGhost} disabled={run?.status === 'running' || !health?.vision?.model}
                          title="Vision QC: framing, angle, expression, one person, artifacts, bad crops"
                          onClick={() => void post(`/datasets/${ds.id}/qc`, {}, '🔬 checking…')}>
                    🔬 QC pass
                  </button>''',
    '''                  <button style={btnGhost}
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
                  </button>''',
    "qc: overwrite + busy label, plus a likeness button")

rep('''                    🗒 Caption all
                  </button>''',
    '''                    {acting.endsWith('/caption') ? '⏳ captioning…' : '🗒 Caption all'}
                  </button>''',
    "caption: busy label")

# ── 3. feedback where the buttons are ──────────────────────────────────────
rep('''                {run?.status === 'running' && (
                  <p style={{ ...hint, margin: '8px 0 0' }}>''',
    '''                {/* v1.226: the reply used to render only at the top of the page,
                    which on a scrolled view is invisible. */}
                {(msg || err) && (
                  <p style={{ ...(err ? errTxt : okTxt), margin: '8px 0 0' }}>{err || msg}</p>
                )}
                {!!acting && run?.status !== 'running' && (
                  <p style={{ ...hint, margin: '8px 0 0' }}>⏳ sending request…</p>
                )}
                {run?.status === 'running' && (
                  <p style={{ ...hint, margin: '8px 0 0' }}>''',
    "inline feedback")

# ── 4. and the progress banner should say something even before the first
#      per-image result lands ───────────────────────────────────────────────
rep('''                    ⏳ {run.kind}{run.round ? ` round ${run.round}/${run.rounds} · ${run.phase}` : ''} {run.detail}''',
    '''                    ⏳ {run.kind}{run.round ? ` round ${run.round}/${run.rounds} · ${run.phase}` : ''}{' '}
                    {run.detail || 'starting…'}
                    {run.total ? ` (${run.done || 0}/${run.total})` : ''}''',
    "banner: never blank")

assert src != orig
P.write_text(src, "utf-8")
print(f"patched {P}  ({len(orig)} -> {len(src)} bytes)")
