"""v1.207.0 — body-drift controls in the panel (Klein3Panel.tsx).

Replaces the single "send the description" checkbox with the real controls:
pose text off|brief|full, 🧍 body lock, 📋 his own build words, 👥 identity boost,
and a 🔎 Preview prompt button that shows the EXACT text (and reference list) the
run will use — free, no worker time.

Exact-match patcher: every replacement asserts exactly ONE occurrence.
Usage: python patch_klein3panel_v1207.py <path-to-Klein3Panel.tsx>
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


# ── 1. state ──────────────────────────────────────────────────────────────
rep(
    """  const [describePose, setDescribePose] = useState(true);   // send the pose in WORDS""",
    """  const [poseTextMode, setPoseTextMode] = useState<'off' | 'brief' | 'full'>('brief');
  const [bodyLock, setBodyLock] = useState(true);           // terminal "don't slim him" clause
  const [bodyWords, setBodyWords] = useState(true);         // his own build/height words
  const [identityBoost, setIdentityBoost] = useState(false);// 2nd identity image as image 3
  const [preview, setPreview] = useState<PreviewT | null>(null);
  const [previewBusy, setPreviewBusy] = useState(false);""",
    "state",
)
rep(
    """  pose_view?: string | null; identity_source?: string | null; pose_desc?: string | null;
}""",
    """  pose_view?: string | null; identity_source?: string | null; pose_desc?: string | null;
  identity_boost?: boolean; pose_text_mode?: string | null;
}
interface PreviewT {
  prompt: string; words: number; pose?: string; pose_view?: string;
  identity_source?: string; refs?: string[]; identity_boost?: boolean;
}""",
    "PreviewT",
)

# ── 2. the shared option payload + preview call ──────────────────────────
rep(
    """  const doGenerate = async () => {""",
    """  // Everything that shapes the prompt — sent identically to /generate,
  // /generate-set and /preview-prompt so the preview IS what runs.
  const promptOpts = () => ({
    match_angle: matchAngle,
    describe_pose: poseTextMode !== 'off',
    pose_text: poseTextMode,
    body_lock: bodyLock,
    body_words: bodyWords,
    identity_boost: identityBoost,
  });
  const loadPreview = async () => {
    setPreviewBusy(true); setGenErr('');
    try {
      const r = await j<PreviewT>(await fetch(`${BASE}/preview-prompt`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          slug, pose_id: poseId || null, category: selectedSet || null,
          tags: selectedTags.length ? selectedTags : null,
          prompt_extra: extra, ...promptOpts(),
        }),
      }));
      setPreview(r);
    } catch (e) { setGenErr((e as Error).message); setPreview(null); }
    setPreviewBusy(false);
  };

  const doGenerate = async () => {""",
    "promptOpts + loadPreview",
)

# ── 3. both generators send the full option set ─────────────────────────
rep(
    """          width: 832, height: 1216, seed: seed.trim() ? Number(seed.trim()) : null,
          match_angle: matchAngle, describe_pose: describePose,
        }),
      }));
      await loadCur(); await loadGens();""",
    """          width: 832, height: 1216, seed: seed.trim() ? Number(seed.trim()) : null,
          ...promptOpts(),
        }),
      }));
      await loadCur(); await loadGens();""",
    "generateSetRun opts",
)
rep(
    """          width: 832, height: 1216, seed: seed.trim() ? Number(seed.trim()) : null,
          match_angle: matchAngle, describe_pose: describePose,
        }),
      }));
      setGen({""",
    """          width: 832, height: 1216, seed: seed.trim() ? Number(seed.trim()) : null,
          ...promptOpts(),
        }),
      }));
      setGen({""",
    "doGenerate opts",
)

# ── 4. the control block ────────────────────────────────────────────────
rep(
    """            <label style={{ ...label, display: 'flex', gap: 6, alignItems: 'center', cursor: 'pointer' }}>
              <input type="checkbox" checked={describePose} onChange={(e) => setDescribePose(e.target.checked)} />
              🗒 Send the pose description with the image
            </label>
            {describePose && (""",
    """            <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
              <label style={{ ...label, margin: 0 }}>🗒 Pose text</label>
              <select style={{ ...input, width: 'auto', fontSize: 12, padding: '4px 6px' }}
                      value={poseTextMode} onChange={(e) => setPoseTextMode(e.target.value as 'off' | 'brief' | 'full')}>
                <option value="off">off — image only</option>
                <option value="brief">brief — the pose in one line (default)</option>
                <option value="full">full — + limb-placement reconciliation</option>
              </select>
              <label style={{ ...hint, display: 'inline-flex', gap: 4, alignItems: 'center', cursor: 'pointer' }}
                     title="Terminal clause: keep his weight, width, limb thickness, height and head-to-body ratio; do not slim, stretch or idealize him">
                <input type="checkbox" checked={bodyLock} onChange={(e) => setBodyLock(e.target.checked)} />
                🧍 lock body
              </label>
              <label style={{ ...hint, display: 'inline-flex', gap: 4, alignItems: 'center', cursor: 'pointer' }}
                     title="Insert his own build/height words from the description fields — naming the build holds it better than 'same as image 1'">
                <input type="checkbox" checked={bodyWords} onChange={(e) => setBodyWords(e.target.checked)} />
                📋 his build words
              </label>
              <label style={{ ...hint, display: 'inline-flex', gap: 4, alignItems: 'center', cursor: 'pointer' }}
                     title="Add a SECOND image of him (front base / face ref) as image 3 — 3-ref Klein, stronger identity, slightly slower">
                <input type="checkbox" checked={identityBoost} onChange={(e) => setIdentityBoost(e.target.checked)} />
                👥 identity boost
              </label>
              <div style={{ flex: 1 }} />
              <button style={btnSm} disabled={previewBusy || (!poseId && !selectedSet && !selectedTags.length)}
                      title="Show the exact prompt and reference list this run would use — costs nothing"
                      onClick={() => void loadPreview()}>
                {previewBusy ? '🔎 …' : '🔎 Preview prompt'}
              </button>
            </div>
            {preview && (
              <div style={{ border: '1px solid #2a2f3a', borderRadius: 6, padding: 8, display: 'grid', gap: 4 }}>
                <div style={{ display: 'flex', gap: 6, alignItems: 'center', flexWrap: 'wrap' }}>
                  <b style={{ fontSize: 12, color: '#e6e9ee' }}>🔎 {preview.pose}</b>
                  <span style={hint}>{preview.words} words · {(preview.refs || []).join(' · ')}</span>
                  <div style={{ flex: 1 }} />
                  <button style={btnSm} onClick={() => setPreview(null)}>close</button>
                </div>
                <textarea readOnly style={{ ...input, minHeight: 96, fontSize: 11, lineHeight: 1.4 }}
                          value={preview.prompt} />
              </div>
            )}
            {poseTextMode !== 'off' && (""",
    "control block",
)

# ── 6. live line reports the boost ──────────────────────────────────────
rep(
    """                {gen.identity_source ? `  ·  🧭 identity: ${gen.identity_source}` : ''}""",
    """                {gen.identity_source ? `  ·  🧭 identity: ${gen.identity_source}` : ''}
                {gen.identity_boost ? '  ·  👥 3-ref' : ''}""",
    "live boost marker",
)

assert src != orig
p.write_text(src, "utf-8")
print(f"patched {p} ({len(orig)} -> {len(src)} bytes)")
