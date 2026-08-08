"""v1.227 — the panel only polled if it ALREADY knew a run was going.

    useEffect(() => {
      if (ds?.run?.status !== 'running') return;   // <-- chicken and egg
      const t = setInterval(...)
    }, [ds?.run?.status, ...]);

The single refresh immediately after a POST was the ONLY chance to notice a run
had started.  Miss it once and the panel never polls again: no banner, no
progress, no error, forever — which is exactly what "it says QC started and I
can't tell if anything is happening" looks like.

And it was easy to miss, because `refreshDs` swallowed every failure:

    try { setDs(await j(...)) } catch { /* ignore */ }

A fetch that 500s or times out left the UI silently stale.

Fixes:
  * poll whenever a dataset is open, not only when a run is already known —
    3s while something is running, 6s when idle. One small GET.
  * surface refresh failures instead of eating them, with a visible "lost
    contact with the backend" state rather than a frozen screen.
  * a heartbeat next to the banner so "working" is distinguishable from "hung"
    even when `detail` has not moved yet.
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


rep('''  const refreshDs = useCallback(async () => {
    if (!ds) return;
    try { setDs(await j<DatasetT>(await fetch(`${BASE}/datasets/${ds.id}`))); } catch { /* ignore */ }
  }, [ds]);

  // live poll while a run is going (worker visibility, standing rule)
  useEffect(() => {
    if (ds?.run?.status !== 'running') return;
    const t = setInterval(() => { void refreshDs(); void load(); }, 3000);
    return () => clearInterval(t);
  }, [ds?.run?.status, refreshDs, load]);''',
    '''  // v1.227: depend on the ID, not the whole object. Keyed on `ds` this
  // callback changed identity on every poll, restarting the interval each time.
  const dsId = ds?.id;
  const refreshDs = useCallback(async () => {
    if (!dsId) return;
    try {
      setDs(await j<DatasetT>(await fetch(`${BASE}/datasets/${dsId}`)));
      setStale(0);
    } catch {
      // v1.227: this used to be swallowed entirely, so a backend that stopped
      // answering looked identical to one with nothing to report.
      setStale((n) => n + 1);
    }
  }, [dsId]);

  // v1.227: poll whenever a dataset is OPEN, not only once a run is already
  // known to be running. The old guard was circular — the single refresh after
  // a POST was the only chance to notice a run had started, and missing it left
  // the panel frozen with no banner and no error.
  useEffect(() => {
    if (!dsId) return;
    const running = ds?.run?.status === 'running';
    const t = setInterval(() => {
      void refreshDs();
      if (running) void load();
    }, running ? 3000 : 6000);
    return () => clearInterval(t);
  }, [dsId, ds?.run?.status, refreshDs, load]);

  // a visible tick, so "working" is distinguishable from "hung" even before
  // `detail` moves
  useEffect(() => {
    if (ds?.run?.status !== 'running') return;
    const t = setInterval(() => setBeat((b) => (b + 1) % 4), 700);
    return () => clearInterval(t);
  }, [ds?.run?.status]);''',
    "polling")

rep('''  const [acting, setActing] = useState('');   // the path of the request in flight''',
    '''  const [acting, setActing] = useState('');   // the path of the request in flight
  const [stale, setStale] = useState(0);     // consecutive failed refreshes
  const [beat, setBeat] = useState(0);       // heartbeat, so a live run looks live''',
    "state")

rep('''                {run?.status === 'running' && (
                  <p style={{ ...hint, margin: '8px 0 0' }}>
                    ⏳ {run.kind}{run.round ? ` round ${run.round}/${run.rounds} · ${run.phase}` : ''}{' '}''',
    '''                {stale > 1 && (
                  <p style={{ ...errTxt, margin: '8px 0 0' }}>
                    ⚠ lost contact with the backend ({stale} failed refreshes) — is run.bat still
                    up? Whatever was running may still be going.
                  </p>
                )}
                {run?.status === 'running' && (
                  <p style={{ ...hint, margin: '8px 0 0' }}>
                    {['|', '/', '-', '\\\\'][beat]}{' '}
                    ⏳ {run.kind}{run.round ? ` round ${run.round}/${run.rounds} · ${run.phase}` : ''}{' '}''',
    "banner: heartbeat + stale warning")

assert src != orig
P.write_text(src, "utf-8")
print(f"patched {P}  ({len(orig)} -> {len(src)} bytes)")
