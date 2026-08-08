"""v1.212.0 — composition preset picker + identity flag in the panel."""
import sys
from pathlib import Path

p = Path(sys.argv[1]); src = p.read_text("utf-8"); orig = src


def rep(old, new, label):
    global src
    n = src.count(old)
    assert n == 1, f"{label}: expected 1, found {n}"
    src = src.replace(old, new); print("  ok ", label)


rep("""  not_one_person: number; face_unclear: number; stuck: number;""",
    """  not_one_person: number; face_unclear: number; identity_off: number; stuck: number;""",
    "FlagsT.identity_off")
rep("""  const [nOutfit, setNOutfit] = useState('');""",
    """  const [nOutfit, setNOutfit] = useState('');
  const [nPreset, setNPreset] = useState<'balanced' | 'face_heavy'>('balanced');""",
    "preset state")
rep("""          class_token: nClass.trim() || 'person', target: nTarget, count: nCount,
          outfit: nOutfit.trim(),""",
    """          class_token: nClass.trim() || 'person', target: nTarget, count: nCount,
          outfit: nOutfit.trim(), preset: nPreset,""",
    "create sends the preset")
rep("""              <div>
                <label style={label}>Fixed outfit (optional — blank keeps his base clothing)</label>""",
    """              <div>
                <label style={label}>Shot mix</label>
                <select style={input} value={nPreset}
                        onChange={(e) => setNPreset(e.target.value as 'balanced' | 'face_heavy')}>
                  <option value="balanced">balanced — 20/20/30/30 face·bust·waist-up·full body</option>
                  <option value="face_heavy">face-heavy — ~45/25/15/15, best for likeness</option>
                </select>
                <p style={{ ...hint, margin: '3px 0 0' }}>
                  Face-heavy mirrors the ratio the dedicated dataset tools aim at (roughly
                  12 face / 6 bust / 6 body / 1 back). More face data buys likeness; fewer body
                  shots costs some full-body flexibility. Worth running one of each.
                </p>
              </div>
              <div>
                <label style={label}>Fixed outfit (optional — blank keeps his base clothing)</label>""",
    "preset picker")
rep("""                            ds.flags.not_one_person ? `${ds.flags.not_one_person} not one person` : '',""",
    """                            ds.flags.not_one_person ? `${ds.flags.not_one_person} not one person` : '',
                            ds.flags.identity_off ? `${ds.flags.identity_off} not him` : '',""",
    "identity in the breakdown")
rep("""                      <b style={{ fontSize: 12, color: '#e0b36a' }}>⚠ {counts.flagged} flagged</b>""",
    """                      <b style={{ fontSize: 12, color: '#e0b36a' }}>⚠ {counts.flagged} flagged</b>
                      {!!ds.flags?.identity_off && (
                        <span style={{ ...errTxt }} title="The vision model compared each render against his reference: these are a different person (usually a different build)">
                          🚫 {ds.flags.identity_off} off-identity
                        </span>
                      )}""",
    "identity callout")
assert src != orig
p.write_text(src, "utf-8"); print(f"patched {p}")
