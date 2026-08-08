"""v1.208.0 — body-matched pose mannequins in the panel (Klein3Panel.tsx).

Adds the 🧍 pose-source control (library | body-matched), a "Fit poses to his
body" button with live per-pose worker status, a side-by-side preview of the
original vs fitted mannequin, and shows the scrubbed pose text in the preview.

Exact-match patcher: every replacement asserts exactly ONE occurrence.
Usage: python patch_klein3panel_v1208.py <path-to-Klein3Panel.tsx>
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


# ── 1. types + state ──────────────────────────────────────────────────────
rep(
    """interface PreviewT {
  prompt: string; words: number; pose?: string; pose_view?: string;
  identity_source?: string; refs?: string[]; identity_boost?: boolean;
}""",
    """interface PreviewT {
  prompt: string; words: number; pose?: string; pose_view?: string;
  identity_source?: string; refs?: string[]; identity_boost?: boolean;
  pose_source?: string; pose_desc_clean?: string;
}
interface PoseFitT { pose_ids: string[]; count: number; job?: JobT | null }""",
    "PreviewT + PoseFitT",
)
rep(
    """  const [previewBusy, setPreviewBusy] = useState(false);""",
    """  const [previewBusy, setPreviewBusy] = useState(false);
  const [poseSource, setPoseSource] = useState<'library' | 'bodyfit'>('library');
  const [fit, setFit] = useState<PoseFitT | null>(null);   // body-matched mannequins""",
    "state",
)

# ── 2. loaders + the fit action ──────────────────────────────────────────
rep(
    """  // Everything that shapes the prompt — sent identically to /generate,""",
    """  const loadFit = async () => {
    if (!slug) { setFit(null); return; }
    try {
      setFit(await j<PoseFitT>(await fetch(`${BASE}/characters/${slug}/posefit`)));
    } catch { setFit(null); }
  };
  useEffect(() => { void loadFit(); }, [slug]);   // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (fit?.job?.status !== 'running') return;
    const t = setInterval(() => { void loadFit(); }, 4000);
    return () => clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fit?.job?.status]);

  // His idea: reshape the pose mannequin to HIS build first, cache it, then
  // render against that — image 2 stops carrying a competing body.
  const fitPoses = async (scope: 'one' | 'selection', overwrite = false) => {
    setGenErr('');
    const payload: Record<string, unknown> = { overwrite, match_angle: matchAngle };
    if (scope === 'one' && poseId) payload.pose_ids = [poseId];
    else if (selectedSet) payload.category = selectedSet;
    else if (selectedTags.length) payload.tags = selectedTags;
    else if (poseId) payload.pose_ids = [poseId];
    try {
      const r = await j<{ started: boolean; total?: number; note?: string }>(
        await fetch(`${BASE}/characters/${slug}/posefit`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        }));
      if (!r.started) { setGenErr(r.note || 'nothing to fit'); return; }
      setPoseSource('bodyfit');
      await loadFit();
    } catch (e) { setGenErr((e as Error).message); }
  };

  // Everything that shapes the prompt — sent identically to /generate,""",
    "loadFit + fitPoses",
)
rep(
    """    body_words: bodyWords,
    identity_boost: identityBoost,
  });""",
    """    body_words: bodyWords,
    identity_boost: identityBoost,
    pose_source: poseSource,
  });""",
    "promptOpts pose_source",
)

# ── 3. the control row ───────────────────────────────────────────────────
rep(
    """              <div style={{ flex: 1 }} />
              <button style={btnSm} disabled={previewBusy || (!poseId && !selectedSet && !selectedTags.length)}
                      title="Show the exact prompt and reference list this run would use — costs nothing"
                      onClick={() => void loadPreview()}>
                {previewBusy ? '🔎 …' : '🔎 Preview prompt'}
              </button>
            </div>""",
    """              <div style={{ flex: 1 }} />
              <button style={btnSm} disabled={previewBusy || (!poseId && !selectedSet && !selectedTags.length)}
                      title="Show the exact prompt and reference list this run would use — costs nothing"
                      onClick={() => void loadPreview()}>
                {previewBusy ? '🔎 …' : '🔎 Preview prompt'}
              </button>
            </div>
            <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
              <label style={{ ...label, margin: 0 }}>🧍 Pose image</label>
              <select style={{ ...input, width: 'auto', fontSize: 12, padding: '4px 6px' }}
                      value={poseSource} onChange={(e) => setPoseSource(e.target.value as 'library' | 'bodyfit')}>
                <option value="library">from the library (as generated)</option>
                <option value="bodyfit">body-matched to him ({fit?.count || 0} ready)</option>
              </select>
              <button style={btnSm}
                      disabled={fit?.job?.status === 'running' || (!poseId && !selectedSet && !selectedTags.length)}
                      title="Reshape the pose mannequin to HIS proportions first (one render per pose, cached and reused). Image 2 then carries his build instead of the mannequin's."
                      onClick={() => void fitPoses(poseId && !selectedSet && !selectedTags.length ? 'one' : 'selection')}>
                {fit?.job?.status === 'running' ? `🧍 ${fit.job.detail || '…'}` : '🧍 Fit pose(s) to his body'}
              </button>
              {poseSource === 'bodyfit' && poseId && !(fit?.pose_ids || []).includes(poseId) && (
                <span style={{ ...hint, color: '#e0b36a' }}>this pose has no fitted mannequin yet — it will use the library image</span>
              )}
              {fit?.job?.status === 'running' && fit.job.tasks && (
                <span style={hint}>
                  {Object.entries(fit.job.tasks).filter(([, t]) => t.status === 'running')
                    .map(([, t]) => `@ ${t.worker || '…'} ⏳`).join(' · ')}
                </span>
              )}
              {fit?.job?.error && <span style={errTxt}>{fit.job.error}</span>}
            </div>
            {poseId && (fit?.pose_ids || []).includes(poseId) && (
              <div style={{ display: 'flex', gap: 8, alignItems: 'flex-start' }}>
                {selPose && (
                  <div style={{ width: 84 }}>
                    <div style={hint}>library</div>
                    <img src={selPose.url} alt="pose" style={{ width: '100%', borderRadius: 5, cursor: 'zoom-in' }}
                         onClick={() => setLightbox(selPose.url)} />
                  </div>
                )}
                <div style={{ width: 84 }}>
                  <div style={{ ...hint, color: '#9cc2ff' }}>🧍 his build</div>
                  <img src={`${BASE}/characters/${slug}/posefit/${poseId}/image`} alt="fitted"
                       style={{ width: '100%', borderRadius: 5, border: '1px solid #3b82f6', cursor: 'zoom-in' }}
                       onClick={() => setLightbox(`${BASE}/characters/${slug}/posefit/${poseId}/image`)} />
                </div>
                <div style={{ display: 'grid', gap: 4 }}>
                  <span style={hint}>Compare the two: the fitted mannequin should hold the same pose with his
                    proportions. If it looks wrong, re-fit it.</span>
                  <div style={{ display: 'flex', gap: 6 }}>
                    <button style={btnSm} disabled={fit?.job?.status === 'running'}
                            onClick={() => void fitPoses('one', true)}>🔁 re-fit this pose</button>
                    <button style={{ ...btnSm, color: '#ff8a8a' }}
                            onClick={async () => {
                              await fetch(`${BASE}/characters/${slug}/posefit/${poseId}/delete`, { method: 'POST' });
                              await loadFit();
                            }}>🗑</button>
                  </div>
                </div>
              </div>
            )}""",
    "pose-source control + compare",
)

# ── 4. preview shows the scrubbed text + which pose image ───────────────
rep(
    """                  <span style={hint}>{preview.words} words · {(preview.refs || []).join(' · ')}</span>""",
    """                  <span style={hint}>{preview.words} words · {(preview.refs || []).join(' · ')}</span>
                  {preview.pose_desc_clean ? (
                    <span style={{ ...hint, color: '#c4b5fd' }} title="build words are stripped out before the pose text is sent">
                      🗒 sent as: “{preview.pose_desc_clean}”
                    </span>
                  ) : null}""",
    "preview cleaned text",
)

# ── 5. live line reports which pose image ran ──────────────────────────
rep(
    """                {gen.identity_boost ? '  ·  👥 3-ref' : ''}""",
    """                {gen.identity_boost ? '  ·  👥 3-ref' : ''}
                {gen.pose_source === 'bodyfit' ? '  ·  🧍 body-matched pose' : ''}""",
    "live pose_source marker",
)
rep(
    """  pose_view?: string | null; identity_source?: string | null; pose_desc?: string | null;
  identity_boost?: boolean; pose_text_mode?: string | null;
}""",
    """  pose_view?: string | null; identity_source?: string | null; pose_desc?: string | null;
  identity_boost?: boolean; pose_text_mode?: string | null; pose_source?: string | null;
}""",
    "GenT.pose_source",
)

assert src != orig
p.write_text(src, "utf-8")
print(f"patched {p} ({len(orig)} -> {len(src)} bytes)")
