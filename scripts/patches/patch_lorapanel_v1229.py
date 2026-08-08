"""v1.229 — you could see a subset, but you could not act on one.

The gallery filtered by all / missing / flagged / no-caption, and every bulk
button acted on the WHOLE dataset.  So "re-render the 12 full-body rows" — which
is exactly what testing the v1.228 crop fix needs — had no path through the UI
at all, and I kept answering that with a one-off script.

  * filter by SHOT TYPE and by ANGLE, not just by state.  Those are the two
    dimensions every finding so far has been indexed on; being unable to slice
    by them was the actual gap.
  * "act on what I am looking at" — re-render, QC and caption the current
    selection, with the count on the button so it is never ambiguous what is
    about to happen.
  * a live breakdown per shot type: rendered, flagged, cropped.  The 58%
    full-body cropping was visible in the raw data and invisible in the app.
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


# ── 1. two more filter dimensions ──────────────────────────────────────────
rep('''  const [filter, setFilter] = useState<'all' | 'missing' | 'flagged' | 'nocap'>('all');''',
    '''  const [filter, setFilter] = useState<'all' | 'missing' | 'flagged' | 'nocap' | 'cropped'>('all');
  const [fFraming, setFFraming] = useState('');   // '' = any shot type
  const [fAngle, setFAngle] = useState('');       // '' = any angle''',
    "filter state")

rep('''  const shown = (ds?.items || []).filter((it) =>
    filter === 'all' ? true
      : filter === 'missing' ? !it.has_image
        : filter === 'flagged' ? it.qc?.ok === false
          : !(it.caption || '').trim());''',
    '''  // v1.229: state AND shape. Every finding so far has been indexed on framing or
  // angle, so those had to become things you can act on, not just read about.
  const shown = (ds?.items || []).filter((it) => {
    if (fFraming && it.framing !== fFraming) return false;
    if (fAngle && it.angle !== fAngle) return false;
    return filter === 'all' ? true
      : filter === 'missing' ? !it.has_image
        : filter === 'flagged' ? it.qc?.ok === false
          : filter === 'cropped' ? it.qc?.cropped_badly === true
            : !(it.caption || '').trim();
  });
  const shownIds = shown.map((i) => i.id);

  // per shot type, so a problem that lives in ONE framing is visible in the app
  // rather than only in a dump — 58% of full-body shots were cropped and the UI
  // had no way to show it.
  const byFraming = ['face', 'headshot', 'upper', 'full'].map((f) => {
    const g = (ds?.items || []).filter((i) => i.framing === f);
    return {
      key: f, n: g.length,
      rendered: g.filter((i) => i.has_image).length,
      flagged: g.filter((i) => i.qc?.ok === false).length,
      cropped: g.filter((i) => i.qc?.cropped_badly).length,
    };
  }).filter((r) => r.n);''',
    "filter logic + breakdown")

# ── 2. the chips, and acting on what you can see ───────────────────────────
rep('''                  {(['all', 'missing', 'flagged', 'nocap'] as const).map((f) => (
                    <button key={f} style={chip(filter === f)} onClick={() => setFilter(f)}>
                      {f === 'all' ? `all (${counts.total})`
                        : f === 'missing' ? `not rendered (${counts.total - counts.rendered})`
                          : f === 'flagged' ? `flagged (${counts.flagged})`
                            : `no caption (${counts.total - counts.captioned})`}
                    </button>
                  ))}
                </div>''',
    '''                  {(['all', 'missing', 'flagged', 'cropped', 'nocap'] as const).map((f) => (
                    <button key={f} style={chip(filter === f)} onClick={() => setFilter(f)}>
                      {f === 'all' ? `all (${counts.total})`
                        : f === 'missing' ? `not rendered (${counts.total - counts.rendered})`
                          : f === 'flagged' ? `flagged (${counts.flagged})`
                            : f === 'cropped' ? `cropped (${(ds?.items || []).filter((i) => i.qc?.cropped_badly).length})`
                              : `no caption (${counts.total - counts.captioned})`}
                    </button>
                  ))}
                </div>

                <div style={{ display: 'flex', gap: 6, alignItems: 'center', flexWrap: 'wrap', marginTop: 8 }}>
                  <span style={hint}>shot type</span>
                  <button style={chip(!fFraming)} onClick={() => setFFraming('')}>any</button>
                  {byFraming.map((r) => (
                    <button key={r.key} style={chip(fFraming === r.key)}
                            title={`${r.rendered}/${r.n} rendered · ${r.flagged} flagged · ${r.cropped} cropped`}
                            onClick={() => setFFraming(fFraming === r.key ? '' : r.key)}>
                      {r.key} ({r.n})
                      {r.cropped ? <span style={{ color: '#e0b36a' }}> ✂{r.cropped}</span> : null}
                      {r.flagged ? <span style={{ color: '#ff8a8a' }}> ⚠{r.flagged}</span> : null}
                    </button>
                  ))}
                  <span style={{ ...hint, marginLeft: 8 }}>angle</span>
                  <button style={chip(!fAngle)} onClick={() => setFAngle('')}>any</button>
                  {Array.from(new Set((ds?.items || []).map((i) => i.angle))).map((a) => {
                    const g = (ds?.items || []).filter((i) => i.angle === a);
                    const miss = g.filter((i) => i.qc && i.qc.angle_ok === false).length;
                    return (
                      <button key={a} style={chip(fAngle === a)}
                              title={`${g.length} planned · ${miss} wrong angle`}
                              onClick={() => setFAngle(fAngle === a ? '' : a)}>
                        {a.replace('three_quarter', '3/4').replace('profile', 'prof')} ({g.length})
                        {miss ? <span style={{ color: '#ff8a8a' }}> ⚠{miss}</span> : null}
                      </button>
                    );
                  })}
                </div>

                {/* v1.229: act on the current selection. Before this every bulk
                    button hit the whole dataset, so "re-render the full-body
                    rows" had no path through the UI. */}
                {(fFraming || fAngle || filter !== 'all') && (
                  <div style={{ display: 'flex', gap: 6, alignItems: 'center', flexWrap: 'wrap',
                                marginTop: 8, borderTop: '1px solid #222831', paddingTop: 8 }}>
                    <b style={{ fontSize: 12, color: '#e0b36a' }}>{shown.length} shown</b>
                    <button style={btnSm} disabled={!!acting || run?.status === 'running' || !shown.length}
                            title="Re-render exactly these images. Everything else is left alone."
                            onClick={() => void post(`/datasets/${ds.id}/generate`,
                                                     { item_ids: shownIds, overwrite: true },
                                                     `🎨 re-rendering ${shown.length}`)}>
                      🎨 Re-render these ({shown.length})
                    </button>
                    <button style={btnSm}
                            disabled={!!acting || run?.status === 'running' || !shown.length || !health?.vision?.model}
                            onClick={() => void post(`/datasets/${ds.id}/qc`,
                                                     { item_ids: shownIds, overwrite: true },
                                                     `🔬 checking ${shown.length}`)}>
                      🔬 QC these ({shown.length})
                    </button>
                    <button style={btnSm} disabled={!!acting || run?.status === 'running' || !shown.length}
                            onClick={() => void post(`/datasets/${ds.id}/caption`,
                                                     { item_ids: shownIds, overwrite: true },
                                                     `🗒 captioned ${shown.length}`)}>
                      🗒 Caption these ({shown.length})
                    </button>
                    <button style={btnSm} onClick={() => { setFFraming(''); setFAngle(''); setFilter('all'); }}>
                      clear filters
                    </button>
                  </div>
                )}''',
    "chips + selection actions")

assert src != orig
P.write_text(src, "utf-8")
print(f"patched {P}  ({len(orig)} -> {len(src)} bytes)")
