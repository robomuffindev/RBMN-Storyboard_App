"""v1.225 — the wardrobe editor and base-mode selector for the LoRA panel.

v1.216 (outfit sets) and v1.217 (dressed bases) both shipped backend-only, so
the only way to reach either was the API.  This is the UI:

  * a wardrobe list -- named (his story outfits) vs variety (what keeps clothing
    detachable), with the live 60/40 split and the sized-for-this-wardrobe count
  * "Suggest variety outfits", which reads the character's own reference and
    proposes named garments for REVIEW
  * a garment reference picker that runs the vision model to NAME the garments,
    because Klein ignores category words
  * the base-mode selector (dressed / stripped / auto)
  * the planner's warnings, shown where the count is chosen rather than after
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


# ── 1. types ────────────────────────────────────────────────────────────────
rep('''interface CharT { slug: string; name: string; ref_count: number; has_base: boolean; missing_views: string[] }''',
    '''interface CharT { slug: string; name: string; ref_count: number; has_base: boolean; missing_views: string[] }
interface OutfitT { id?: string; name: string; desc: string; kind: 'named' | 'variety'; ref_id?: string | null }
interface CharRefT { id: string; tag: string; name: string; url?: string }''',
    "types")

# ── 2. state ────────────────────────────────────────────────────────────────
rep('''  const [nOutfit, setNOutfit] = useState('');''',
    '''  const [nOutfit, setNOutfit] = useState('');
  const [wardrobe, setWardrobe] = useState<OutfitT[]>([]);
  const [charRefs, setCharRefs] = useState<CharRefT[]>([]);
  const [wbBusy, setWbBusy] = useState('');
  const [nBaseMode, setNBaseMode] = useState<'' | 'dressed' | 'stripped' | 'auto'>('');
  const [autoSize, setAutoSize] = useState(true);''',
    "state")

# ── 3. the character's references, for the garment picker ──────────────────
rep('''  const createDs = async () => {''',
    '''  // The garment picker needs the character's own reference images. Loaded when
  // the character changes, not per render.
  useEffect(() => {
    if (!nChar) { setCharRefs([]); return; }
    void (async () => {
      try {
        const c = await j<{ refs?: CharRefT[] }>(await fetch(`/api/klein3/characters/${nChar}`));
        setCharRefs(c.refs || []);
      } catch { setCharRefs([]); }
    })();
  }, [nChar]);

  // ~13 images per outfit is what the backend sizes to; mirror it so the number
  // on screen is the number that will be used.
  const sizedCount = Math.max(24, Math.min(Math.round(Math.max(1, wardrobe.length) * 13), 120));
  const effCount = wardrobe.length && autoSize ? sizedCount : nCount;
  const namedN = wardrobe.filter((o) => o.kind === 'named').length;
  const varietyN = wardrobe.length - namedN;
  const perOutfit = wardrobe.length ? Math.round(effCount / wardrobe.length) : 0;

  const addOutfit = (kind: 'named' | 'variety') =>
    setWardrobe((w) => [...w, { name: kind === 'named' ? `outfit ${w.length + 1}` : `look ${w.length + 1}`,
                                desc: '', kind }]);
  const setOutfit = (i: number, patch: Partial<OutfitT>) =>
    setWardrobe((w) => w.map((o, k) => (k === i ? { ...o, ...patch } : o)));
  const delOutfit = (i: number) => setWardrobe((w) => w.filter((_, k) => k !== i));

  const suggestWardrobe = async () => {
    if (!nChar) return;
    setWbBusy('suggest'); setErr('');
    try {
      const r = await j<{ character_type: string; outfits: OutfitT[] }>(
        await fetch(`/api/lora/characters/${nChar}/wardrobe`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ count: 5 }),
        }));
      // appended for REVIEW, never applied silently
      setWardrobe((w) => [...w, ...r.outfits.map((o) => ({ ...o, kind: 'variety' as const }))]);
      if (r.character_type) setMsg(`read as: ${r.character_type} — edit anything that is wrong`);
    } catch (e) { setErr((e as Error).message); }
    finally { setWbBusy(''); }
  };

  // Klein ignores category words, so a garment reference is useless without
  // NAMED garments. This is what produces the name.
  const nameGarment = async (i: number, refId: string) => {
    setOutfit(i, { ref_id: refId || null });
    if (!refId || !nChar) return;
    setWbBusy(`ref${i}`); setErr('');
    try {
      const r = await j<{ desc: string }>(
        await fetch(`/api/lora/characters/${nChar}/refs/${refId}/garment`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}',
        }));
      setOutfit(i, { desc: r.desc, ref_id: refId });
    } catch (e) { setErr((e as Error).message); }
    finally { setWbBusy(''); }
  };

  const createDs = async () => {''',
    "wardrobe logic")

# ── 4. send it ──────────────────────────────────────────────────────────────
rep('''          outfit: nOutfit.trim(), preset: nPreset,''',
    '''          outfit: nOutfit.trim(), preset: nPreset,
          outfits: wardrobe.filter((o) => o.desc.trim()),
          base_mode: nBaseMode || null,''',
    "create: send wardrobe + base mode")

rep('''          class_token: nClass.trim() || 'person', target: nTarget, count: nCount,''',
    '''          class_token: nClass.trim() || 'person', target: nTarget,
          count: wardrobe.length && autoSize ? null : nCount,''',
    "create: let the backend size it")

# ── 5. the UI, replacing the single fixed-outfit box ───────────────────────
rep('''              <div>
                <label style={label}>Fixed outfit (optional — blank keeps his base clothing)</label>
                <input style={input} value={nOutfit} onChange={(e) => setNOutfit(e.target.value)}
                       placeholder="a black t-shirt and jeans" />
              </div>''',
    '''              <div>
                <label style={label}>Identity source</label>
                <select style={input} value={nBaseMode}
                        onChange={(e) => setNBaseMode(e.target.value as '' | 'dressed' | 'stripped' | 'auto')}>
                  <option value="">use the character's setting</option>
                  <option value="dressed">🧥 dressed — his own clothes from your references</option>
                  <option value="stripped">👙 stripped — the stripped base set</option>
                  <option value="auto">🤖 auto — newest version of each view</option>
                </select>
                <p style={{ ...hint, margin: '3px 0 0' }}>
                  Dressed skips the strip step entirely, which is one less edit per view and one
                  less source of drift. Set the default per character in Klein 3.0.
                </p>
              </div>

              <div style={{ border: '1px solid #2a2f3a', borderRadius: 8, padding: 10, display: 'grid', gap: 8 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                  <b style={{ fontSize: 13, color: '#e6e9ee' }}>👔 Wardrobe</b>
                  <div style={{ flex: 1 }} />
                  <button style={btnSm} onClick={() => addOutfit('named')}>+ named</button>
                  <button style={btnSm} onClick={() => addOutfit('variety')}>+ variety</button>
                  <button style={btnSm} disabled={!nChar || wbBusy === 'suggest'}
                          onClick={() => void suggestWardrobe()}>
                    {wbBusy === 'suggest' ? '⏳ reading…' : '🎨 Suggest variety'}
                  </button>
                </div>
                <p style={{ ...hint, margin: 0 }}>
                  One outfit across the whole set gets absorbed into the trigger word — the
                  clothes become part of the character. <b>Named</b> outfits are your story
                  wardrobe; <b>variety</b> outfits exist to prove clothing is independent of
                  him, which is what keeps it controllable. Leave this empty to use whatever
                  his base images already wear.
                </p>

                {wardrobe.map((o, i) => (
                  <div key={i} style={{ display: 'grid', gap: 4, borderTop: '1px solid #222831', paddingTop: 6 }}>
                    <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
                      <select style={{ ...input, width: 92 }} value={o.kind}
                              onChange={(e) => setOutfit(i, { kind: e.target.value as 'named' | 'variety' })}>
                        <option value="named">named</option>
                        <option value="variety">variety</option>
                      </select>
                      <input style={{ ...input, width: 140 }} value={o.name}
                             placeholder="Ranger kit"
                             onChange={(e) => setOutfit(i, { name: e.target.value })} />
                      <select style={{ ...input, width: 150 }} value={o.ref_id || ''}
                              disabled={wbBusy === `ref${i}`}
                              onChange={(e) => void nameGarment(i, e.target.value)}>
                        <option value="">no reference image</option>
                        {charRefs.map((r) => (
                          <option key={r.id} value={r.id}>
                            {r.tag === 'outfit' ? '👔 ' : ''}{r.tag}: {r.name.slice(0, 16)}
                          </option>
                        ))}
                      </select>
                      <div style={{ flex: 1 }} />
                      <button style={{ ...btnSm, color: '#ff8a8a' }} onClick={() => delOutfit(i)}>🗑</button>
                    </div>
                    <input style={input} value={o.desc}
                           placeholder="NAME each garment and colour — a brown leather jacket, a green flannel shirt and dark jeans"
                           onChange={(e) => setOutfit(i, { desc: e.target.value })} />
                    {wbBusy === `ref${i}` && <span style={hint}>reading the garments…</span>}
                  </div>
                ))}

                {wardrobe.length > 0 && (
                  <div style={{ borderTop: '1px solid #222831', paddingTop: 6, display: 'grid', gap: 3 }}>
                    <label style={{ ...label, display: 'flex', alignItems: 'center', gap: 6 }}>
                      <input type="checkbox" checked={autoSize}
                             onChange={(e) => setAutoSize(e.target.checked)} />
                      size the set from the wardrobe ({sizedCount} images)
                    </label>
                    <span style={{ ...hint, color: perOutfit < 8 ? '#e0b36a' : '#8d97a5' }}>
                      {namedN} named · {varietyN} variety → {effCount} images, about {perOutfit} each
                      {perOutfit < 8 && ' — measured, below ~8 each some outfits only appear in 2 of the 4 shot types, which trains "that outfit means that shot"'}
                    </span>
                    <span style={hint}>
                      Named outfits take 60% of the images between them, variety the other 40%.
                      A face close-up never names an outfit; a head-and-shoulders names only the
                      first garment.
                    </span>
                  </div>
                )}
              </div>''',
    "the wardrobe editor")

# ── 6. the images slider follows the wardrobe ─────────────────────────────
rep('''                  <label style={label}>Images ({nCount})</label>''',
    '''                  <label style={label}>
                    Images ({wardrobe.length && autoSize ? `${sizedCount} — sized from the wardrobe` : nCount})
                  </label>''',
    "slider label")

rep('''                  <input type="range" min={16} max={120} step={4} value={nCount}
                         style={{ width: '100%' }}
                         onChange={(e) => setNCount(Number(e.target.value))} />''',
    '''                  <input type="range" min={16} max={120} step={4} value={effCount}
                         style={{ width: '100%' }} disabled={!!wardrobe.length && autoSize}
                         onChange={(e) => setNCount(Number(e.target.value))} />''',
    "slider value")

assert src != orig
P.write_text(src, "utf-8")
print(f"patched {P}  ({len(orig)} -> {len(src)} bytes)")
