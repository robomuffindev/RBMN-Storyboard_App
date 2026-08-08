"""v1.241 UI — the panel stops reporting a check that no longer exists, and
starts showing the measurement that replaced it.

The backend dropped `framing_ok` and `cropped_badly` (0 for 12 on images
verified by eye, twice).  Left alone, the panel would quietly render "0 bad
crop", a `cropped (0)` filter chip and a `cropped` column that can never be
anything but zero — which reads as "no crop problems" when the truth is "crop is
not checked at all".  That is the exact failure mode of a silent gap.

So:
  * the crop filter and the bad-crop / wrong-framing chips go
  * a plain line says what IS measured and what is NOT, in the summary bar
  * every image shows its measured YAW next to the QC badge — the number has
    existed since v1.234 and has been visible only in a script
  * the shot-type breakdown swaps its dead `cropped` column for `angle off`,
    which is measured and can actually change
"""
import sys
from pathlib import Path

P = Path(sys.argv[1] if len(sys.argv) > 1
         else "frontend/src/components/VNCCSNative/LoraPanel.tsx")
src = P.read_text("utf-8")
orig = src


def rep(old, new, why):
    global src
    n = src.count(old)
    assert n == 1, f"{why}: expected 1 match, found {n}"
    src = src.replace(old, new, 1)


# ── types ────────────────────────────────────────────────────────────────────
rep('''  ok: boolean; issues?: string[]; framing_ok?: boolean; angle_ok?: boolean;''',
    '''  ok: boolean; issues?: string[]; angle_ok?: boolean;
  yaw?: number | null; angle_method?: string; angle_note?: string;''',
    "item qc type")

rep('''  artifacts?: boolean; cropped_badly?: boolean; server?: string;''',
    '''  artifacts?: boolean; server?: string;''',
    "item qc type 2")

rep('''  flagged: number; checked: number; artifacts: number; cropped_badly: number;
  framing_off: number; angle_off: number; expression_off: number;''',
    '''  flagged: number; checked: number; artifacts: number;
  angle_off: number; angle_measured?: number; angle_unmeasured?: number;
  expression_off: number;
  not_checked?: string[]; unreliable?: string[];''',
    "flags type")

# ── filters ──────────────────────────────────────────────────────────────────
rep('''    return filter === 'all' ? true
      : filter === 'missing' ? !it.has_image
        : filter === 'flagged' ? it.qc?.ok === false
          : filter === 'cropped' ? it.qc?.cropped_badly === true
            : !(it.caption || '').trim();''',
    '''    return filter === 'all' ? true
      : filter === 'missing' ? !it.has_image
        : filter === 'flagged' ? it.qc?.ok === false
          // v1.241: was 'cropped', which the backend no longer measures. Wrong
          // ANGLE is measured, so that is what you can now filter to.
          : filter === 'angle' ? it.qc?.angle_ok === false
            : !(it.caption || '').trim();''',
    "filter")

rep('''                  {(['all', 'missing', 'flagged', 'cropped', 'nocap'] as const).map((f) => (''',
    '''                  {(['all', 'missing', 'flagged', 'angle', 'nocap'] as const).map((f) => (''',
    "filter chips list")

rep('''                            : f === 'cropped' ? `cropped (${(ds?.items || []).filter((i) => i.qc?.cropped_badly).length})`''',
    '''                            : f === 'angle' ? `wrong angle (${(ds?.items || []).filter((i) => i.qc?.angle_ok === false).length})`''',
    "filter chip label")

# ── per-shot-type breakdown ──────────────────────────────────────────────────
rep('''      flagged: g.filter((i) => i.qc?.ok === false).length,
      cropped: g.filter((i) => i.qc?.cropped_badly).length,''',
    '''      flagged: g.filter((i) => i.qc?.ok === false).length,
      angleOff: g.filter((i) => i.qc?.angle_ok === false).length,''',
    "byFraming")

# ── the summary line ─────────────────────────────────────────────────────────
rep('''                            ds.flags.artifacts ? `${ds.flags.artifacts} artifacts` : '',
                            ds.flags.cropped_badly ? `${ds.flags.cropped_badly} bad crop` : '',
                            ds.flags.framing_off ? `${ds.flags.framing_off} wrong framing` : '',
                            ds.flags.angle_off ? `${ds.flags.angle_off} wrong angle` : '',''',
    '''                            ds.flags.artifacts ? `${ds.flags.artifacts} artifacts` : '',
                            ds.flags.angle_off ? `${ds.flags.angle_off} wrong angle` : '',''',
    "summary chips")

rep('''                    {!!Object.keys(ds.flags?.top_issues || {}).length && (''',
    '''                    {/* v1.241: a summary with nothing in it must not read as a
                        clean dataset. What is measured, and what is not, said
                        out loud — framing and crop have no instrument yet and
                        the vision model was 0 for 12 on them. */}
                    {!!ds.flags && (
                      <span style={hint}>
                        measured: identity (ArcFace{ds.flags.arcface_scored ? ` ×${ds.flags.arcface_scored}` : ''})
                        · angle (head yaw{typeof ds.flags.angle_measured === 'number'
                          ? ` ×${ds.flags.angle_measured}${ds.flags.angle_unmeasured
                            ? `, ${ds.flags.angle_unmeasured} unmeasurable` : ''}` : ''})
                        · one person · artifacts
                        {!!ds.flags.not_checked?.length && (
                          <b style={{ color: '#e0b36a' }}>
                            {'  ·  NOT checked: ' + ds.flags.not_checked.join(', ')}
                          </b>
                        )}
                        {!!ds.flags.unreliable?.length && (
                          <span style={{ color: '#8d97a5' }}>
                            {'  ·  unreliable: ' + ds.flags.unreliable.join(', ')}
                          </span>
                        )}
                      </span>
                    )}
                    {!!Object.keys(ds.flags?.top_issues || {}).length && (''',
    "what is measured line")

# ── the per-image yaw badge ──────────────────────────────────────────────────
rep('''                          <div style={{ flex: 1 }} />
                          {it.qc && (
                            <span style={it.qc.ok ? okTxt : errTxt}
                                  title={(it.qc.issues || []).join(' · ') || 'checked'}>
                              {it.qc.ok ? '✓ QC' : `⚠ ${(it.qc.issues || ['flagged'])[0]}`}
                            </span>
                          )}''',
    '''                          <div style={{ flex: 1 }} />
                          {/* v1.241: the measured angle, on the image. It has
                              existed since v1.234 and lived only in a script. */}
                          {it.qc?.angle_method && (
                            <span style={it.qc.angle_ok === false ? errTxt
                              : it.qc.angle_method === 'unmeasured' ? hint : okTxt}
                                  title={it.qc.angle_note || ''}>
                              {it.qc.angle_method === 'unmeasured'
                                ? '📐 not measurable'
                                : `📐 ${(it.qc.yaw as number) > 0 ? '+' : ''}${it.qc.yaw}°`}
                            </span>
                          )}
                          {it.qc && (
                            <span style={it.qc.ok ? okTxt : errTxt}
                                  title={(it.qc.issues || []).join(' · ') || 'checked'}>
                              {it.qc.ok ? '✓ QC' : `⚠ ${(it.qc.issues || ['flagged'])[0]}`}
                            </span>
                          )}''',
    "yaw badge")


rep("""  const [filter, setFilter] = useState<'all' | 'missing' | 'flagged' | 'nocap' | 'cropped'>('all');""",
    """  const [filter, setFilter] = useState<'all' | 'missing' | 'flagged' | 'nocap' | 'angle'>('all');""",
    "filter state union")

rep("""                            title={`${r.rendered}/${r.n} rendered · ${r.flagged} flagged · ${r.cropped} cropped`}""",
    """                            title={`${r.rendered}/${r.n} rendered · ${r.flagged} flagged · ${r.angleOff} wrong angle`}""",
    "shot-type tooltip")

rep(r"""                      {r.cropped ? <span style={{ color: '#e0b36a' }}> ✂{r.cropped}</span> : null}""",
    r"""                      {r.angleOff ? <span style={{ color: '#e0b36a' }}> 📐{r.angleOff}</span> : null}""",
    "shot-type badge")

assert src != orig
P.write_text(src, "utf-8")
print(f"patched {P}  ({len(orig)} -> {len(src)} bytes)")
