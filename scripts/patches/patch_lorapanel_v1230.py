"""v1.230 — "checking 12" never went away, so a finished run looked hung.

`post()` sets a message and NOTHING ever clears it. When the run ends the
progress banner disappears (it is gated on `status === 'running'`) and all that
is left on screen is the message written at the moment of the click:

    🔬 checking 12

A past-tense fact rendered in the present tense, with the live indicator gone.
Indistinguishable from stuck — which is exactly how he read it, twice.

  * Watch the run's status TRANSITION and replace the in-flight note with what
    actually happened: "✓ QC finished — 12 checked, 3 flagged".
  * Keep a persistent "last run" line, so there is always an answer to "is
    something happening, and what happened last?" instead of a message whose age
    is unknowable.
  * Mark the in-flight note as in-flight (a spinner), so it can never again be
    mistaken for a result.
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


rep('''  const [beat, setBeat] = useState(0);       // heartbeat, so a live run looks live''',
    '''  const [beat, setBeat] = useState(0);       // heartbeat, so a live run looks live
  const [lastRun, setLastRun] = useState('');  // what the previous run actually did
  const [live, setLive] = useState(false);     // is `msg` describing something in flight?''',
    "state")

rep('''      if ((r as { started?: boolean }).started === false) setMsg(`ℹ ${String(r.note || 'nothing to do')}`);
      else if (note) setMsg(note);''',
    '''      if ((r as { started?: boolean }).started === false) { setMsg(`ℹ ${String(r.note || 'nothing to do')}`); setLive(false); }
      else if (note) { setMsg(note); setLive(true); }''',
    "post: mark in-flight messages")

rep('''  // a visible tick, so "working" is distinguishable from "hung" even before
  // `detail` moves
  useEffect(() => {
    if (ds?.run?.status !== 'running') return;
    const t = setInterval(() => setBeat((b) => (b + 1) % 4), 700);
    return () => clearInterval(t);
  }, [ds?.run?.status]);''',
    '''  // a visible tick, so "working" is distinguishable from "hung" even before
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
  }, [ds?.run?.status, ds]);''',
    "transition reporter")

rep('''                {(msg || err) && (
                  <p style={{ ...(err ? errTxt : okTxt), margin: '8px 0 0' }}>{err || msg}</p>
                )}''',
    '''                {(msg || err) && (
                  <p style={{ ...(err ? errTxt : okTxt), margin: '8px 0 0' }}>
                    {/* a message about something IN FLIGHT must look in-flight, or
                        it reads as a result once the banner disappears */}
                    {!err && live ? `${['|', '/', '-', '\\\\'][beat]} ` : ''}{err || msg}
                  </p>
                )}
                {!!lastRun && !msg && !err && run?.status !== 'running' && (
                  <p style={{ ...hint, margin: '8px 0 0' }}>{lastRun}</p>
                )}''',
    "render: in-flight vs result")

assert src != orig
P.write_text(src, "utf-8")
print(f"patched {P}  ({len(orig)} -> {len(src)} bytes)")
