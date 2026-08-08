"""v1.205.0 — DOMINANT ANGLE in the Klein 3.0 panel (Klein3Panel.tsx).

Per-pose angle (front/back/left/right), angle filter chips, bulk angle setter,
and 🧭 angle-matched identity in the generate box (with a pre-run prediction of
WHICH base image will be used).  Also teaches the LLM import guide the field.

Exact-match patcher: every replacement asserts exactly ONE occurrence.
Usage: python patch_klein3panel_v1205.py <path-to-Klein3Panel.tsx>
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


# ── 1. types ──────────────────────────────────────────────────────────────
rep(
    """interface PoseT { id: string; name: string; category: string; set?: string; tags?: string[]; prompt: string; source: string; url: string; seed?: number | null; has_image?: boolean }""",
    """interface PoseT { id: string; name: string; category: string; set?: string; tags?: string[]; view?: string; prompt: string; source: string; url: string; seed?: number | null; has_image?: boolean }""",
    "PoseT.view",
)
rep(
    """  pose?: string | null; pose_id?: string | null; created_at?: string | null;
}""",
    """  pose?: string | null; pose_id?: string | null; created_at?: string | null;
  pose_view?: string | null; identity_source?: string | null;
}
const POSE_VIEWS = ['front', 'back', 'left', 'right'];
const VIEW_ICON: Record<string, string> = { front: '🙂', back: '🔙', left: '⬅️', right: '➡️' };""",
    "GenT + view constants",
)

# ── 2. LLM guide doc: teach the view field ───────────────────────────────
rep(
    """{
  "name": "Short unique pose name",
  "prompt": "description of the BODY POSE only",
  "category": "One word group like Standing, Seated, Action, Gesture, Combat, Emotional"
}""",
    """{
  "name": "Short unique pose name",
  "prompt": "description of the BODY POSE only",
  "category": "One word group like Standing, Seated, Action, Gesture, Combat, Emotional",
  "view": "front | back | left | right"
}""",
    "LLM doc JSON shape",
)
rep(
    '''Rules for "name": unique within the set, 2-4 words, human-scannable.
Rules for "category": reuse the same few categories across the set.''',
    '''Rules for "name": unique within the set, 2-4 words, human-scannable.
Rules for "category": reuse the same few categories across the set.

Rules for "view" — the DOMINANT ANGLE (important, do not skip):
- Which side of the BODY the camera mostly sees in this pose, as one of exactly
  four values: "front", "back", "left", "right".
- "left" means the viewer sees the person's LEFT side; "right" the right side.
- The tool pairs each pose with the matching reference image of the character
  (its front / back / left / right view). A side pose paired with a front
  reference loses likeness — that is the whole reason this field exists.
- Judge it by the CHEST and HIPS, not the head: a body facing the camera with
  the head turned is still "front".
- Roughly: body turned less than ~45 degrees from camera = front; 45-135 = left
  or right; more than ~135 = back.
- If a pose genuinely does not favour a side, omit the field or leave it empty
  rather than guessing — never write "side", "profile", "three-quarter" or a
  number.
- Make sure the "prompt" AGREES with "view": if view is "back", the prompt must
  say the person is turned away from the camera.''',
    "LLM doc view rules",
)
rep(
    """Header row: name,prompt,category
Quote any field containing commas. Same content rules as JSON.""",
    """Header row: name,prompt,category,view
Quote any field containing commas. Same content rules as JSON.""",
    "LLM doc CSV header",
)
rep(
    """[
  {"name": "Hero landing", "prompt": "crouching low with one fist and one knee touching the ground, other arm swept back, head raised looking forward", "category": "Action"},
  {"name": "Casual lean", "prompt": "leaning one shoulder against a plain wall, ankles crossed, arms folded loosely, head turned toward the camera", "category": "Standing"},
  {"name": "Floor read", "prompt": "sitting on the ground with legs crossed, leaning forward, both hands resting near the ankles, head tilted down", "category": "Seated"}
]""",
    """[
  {"name": "Hero landing", "prompt": "crouching low with one fist and one knee touching the ground, other arm swept back, head raised looking forward", "category": "Action", "view": "front"},
  {"name": "Casual lean", "prompt": "leaning the left shoulder against a plain wall, body turned side-on so the viewer sees the left side, ankles crossed, arms folded loosely", "category": "Standing", "view": "left"},
  {"name": "Shoulder check", "prompt": "standing turned away from the camera, weight on the right leg, looking back over the left shoulder, arms relaxed at the sides", "category": "Turned", "view": "back"}
]""",
    "LLM doc example",
)

# ── 3. state ─────────────────────────────────────────────────────────────
rep(
    """  const [newTag, setNewTag] = useState('');""",
    """  const [newTag, setNewTag] = useState('');
  const [editView, setEditView] = useState('');             // per-pose DOMINANT ANGLE
  const [bulkView, setBulkView] = useState('');             // bulk angle setter
  const [viewSel, setViewSel] = useState<string[]>([]);     // modal angle filter
  const [matchAngle, setMatchAngle] = useState(true);       // angle-matched identity""",
    "state",
)

# ── 4. editor open + saveMeta + bulk angle ───────────────────────────────
rep(
    """    setEditSet(p.set || p.category || ''); setEditTags([...(p.tags || [])]); setNewTag('');""",
    """    setEditSet(p.set || p.category || ''); setEditTags([...(p.tags || [])]); setNewTag('');
    setEditView(p.view || '');""",
    "openPoseEditor view",
)
rep(
    """        body: JSON.stringify({ set: editSet || undefined, tags: editTags }),""",
    """        body: JSON.stringify({ set: editSet || undefined, tags: editTags, view: editView }),""",
    "saveMeta view",
)
rep(
    """      setPoseMsg(`✓ “${editPose.name}” → set “${editSet}”${editTags.length ? ` · tags: ${editTags.join(', ')}` : ' · no tags'}`);""",
    """      setPoseMsg(`✓ “${editPose.name}” → set “${editSet}”${editTags.length ? ` · tags: ${editTags.join(', ')}` : ' · no tags'} · angle: ${editView || 'any'}`);""",
    "saveMeta message",
)
rep(
    """  const bulkDelete = async () => {""",
    """  const bulkViewSet = async (v: string) => {
    if (!selIds.length) return;
    setPoseBusy(true); setPoseMsg('');
    try {
      const r = await j<{ updated: number }>(await fetch(`${POSE_BASE}/poses/bulk-view`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ids: selIds, view: v }),
      }));
      await loadPoses();
      setPoseMsg(`✓ dominant angle ${v ? `“${v}”` : 'cleared'} on ${r.updated} pose(s)`);
    } catch (e) { setPoseMsg((e as Error).message); }
    setPoseBusy(false);
  };
  const bulkDelete = async () => {""",
    "bulkViewSet",
)

# ── 5. filters: angle chips + shownPoses filter ─────────────────────────
rep(
    """  const shownPoses = poses
    .filter((p) => !catFilter || (p.set || p.category) === catFilter)
    .filter((p) => !tagsSel.length || (p.tags || []).some((t) => tagsSel.includes(t)));""",
    """  const shownPoses = poses
    .filter((p) => !catFilter || (p.set || p.category) === catFilter)
    .filter((p) => !tagsSel.length || (p.tags || []).some((t) => tagsSel.includes(t)))
    .filter((p) => !viewSel.length || viewSel.includes(p.view || 'unset'));
  // Mirror of the backend's _base_for_view priority, so the panel can say WHICH
  // identity image a run will use BEFORE spending it.
  const identityFor = (view?: string | null): string => {
    const v = (view || '').trim();
    if (!v) return 'active base';
    const vers = (cur?.base_versions || []).filter((bv) => (bv.view || '') === v);
    const ups = vers.filter((bv) => bv.kind === 'upscaled');
    if (ups.length) return `${v} base (upscaled)`;
    if (vers.length) return `${v} base (${vers[vers.length - 1].kind})`;
    if ((cur?.refs || []).some((r) => r.tag === v)) return `${v} reference`;
    return `active base — no ${v} view yet`;
  };""",
    "shownPoses + identityFor",
)

# ── 6. angle filter row in the modal (next to the tag chips) ────────────
rep(
    """        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center' }}>
          {!catFilter && <span style={hint}>open a set on the left to import or create poses ·</span>}""",
    """        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center' }}>
          <span style={hint}>🧭 angle:</span>
          {[...POSE_VIEWS, 'unset'].map((v) => {
            const n = poses.filter((pp) => (pp.view || 'unset') === v
              && (!catFilter || (pp.set || pp.category) === catFilter)).length;
            return (
              <button key={v} style={chip(viewSel.includes(v))} disabled={!n}
                      title={v === 'unset' ? 'poses with no dominant angle set' : `poses facing ${v}`}
                      onClick={() => setViewSel((vs) => vs.includes(v) ? vs.filter((x) => x !== v) : [...vs, v])}>
                {v === 'unset' ? '—' : VIEW_ICON[v]} {v} ({n})
              </button>
            );
          })}
          {viewSel.length > 0 && <button style={btnSm} onClick={() => setViewSel([])}>clear</button>}
        </div>
        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center' }}>
          {!catFilter && <span style={hint}>open a set on the left to import or create poses ·</span>}""",
    "angle filter chips",
)

# ── 7. bulk bar: angle setter ───────────────────────────────────────────
rep(
    """            <div style={{ flex: 1 }} />
            <button style={{ ...btnSm, color: '#ff8a8a' }} disabled={poseBusy || !selIds.length}
                    onClick={() => void bulkDelete()}>🗑 delete</button>""",
    """            <span style={{ ...hint, marginLeft: 8 }}>🧭 angle:</span>
            <select style={{ ...input, width: 'auto', fontSize: 12, padding: '4px 6px' }}
                    value={bulkView} onChange={(e) => setBulkView(e.target.value)}>
              <option value="">— clear —</option>
              {POSE_VIEWS.map((v) => <option key={v} value={v}>{v}</option>)}
            </select>
            <button style={btnSm} disabled={poseBusy || !selIds.length}
                    title="Set (or clear) the dominant angle on every selected pose"
                    onClick={() => void bulkViewSet(bulkView)}>🧭 Set angle</button>
            <div style={{ flex: 1 }} />
            <button style={{ ...btnSm, color: '#ff8a8a' }} disabled={poseBusy || !selIds.length}
                    onClick={() => void bulkDelete()}>🗑 delete</button>""",
    "bulk angle setter",
)

# ── 8. tile badge ───────────────────────────────────────────────────────
rep(
    """              {!!(p.tags || []).length && (
                <div style={{ fontSize: 9, color: '#8d97a5', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}
                     title={(p.tags || []).join(', ')}>🏷 {(p.tags || []).join(' · ')}</div>
              )}""",
    """              <div style={{ fontSize: 9, color: p.view ? '#9cc2ff' : '#5c6472', overflow: 'hidden',
                            textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}
                   title={p.view ? `dominant angle: ${p.view} — pairs with the ${p.view} base view`
                                 : 'no dominant angle — will use the active base'}>
                {p.view ? `${VIEW_ICON[p.view] || '🧭'} ${p.view}` : '🧭 —'}
              </div>
              {!!(p.tags || []).length && (
                <div style={{ fontSize: 9, color: '#8d97a5', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}
                     title={(p.tags || []).join(', ')}>🏷 {(p.tags || []).join(' · ')}</div>
              )}""",
    "tile angle badge",
)

# ── 9. editor: angle dropdown ───────────────────────────────────────────
rep(
    """              <div style={{ flex: 1 }} />
              <button style={btnSm} disabled={poseBusy} title="Save the SET (move) and TAGS — leaves the prompt/image alone"
                      onClick={() => void saveMeta()}>💾 Save set + tags</button>""",
    """              <span style={{ ...hint, marginLeft: 6 }}>🧭 angle</span>
              <select style={{ ...input, width: 'auto', fontSize: 12, padding: '4px 6px' }}
                      title="Which side of the body the camera sees — picks the matching base view as identity"
                      value={editView} onChange={(e) => setEditView(e.target.value)}>
                <option value="">— any (active base) —</option>
                {POSE_VIEWS.map((v) => <option key={v} value={v}>{v}</option>)}
              </select>
              {editView && <span style={hint}>→ {identityFor(editView)}</span>}
              <div style={{ flex: 1 }} />
              <button style={btnSm} disabled={poseBusy} title="Save the SET (move), TAGS and ANGLE — leaves the prompt/image alone"
                      onClick={() => void saveMeta()}>💾 Save set · tags · angle</button>""",
    "editor angle dropdown",
)

# ── 10. import tooltips mention the column ──────────────────────────────
rep(
    """                 title={'Batch import pose definitions INTO this set.\\nJSON: [{"name":"Hero landing","prompt":"crouched superhero landing...","category":"Action"}]\\nRow category/tags become TAGS on each pose — the SET is this one.\\nCSV headers: name,prompt[,category][,tags][,raw]'}>""",
    """                 title={'Batch import pose definitions INTO this set.\\nJSON: [{"name":"Hero landing","prompt":"crouched superhero landing...","category":"Action","view":"front"}]\\nRow category/tags become TAGS on each pose — the SET is this one.\\n"view" = DOMINANT ANGLE (front|back|left|right): pairs the pose with that base view.\\nCSV headers: name,prompt[,category][,tags][,view][,raw]'}>""",
    "import tooltip",
)
rep(
    """                 title={'Import a pose PACK into this set:\\n• .zip of control images — openpose skeletons, depth maps, DWpose renders\\n• openpose keypoint .json files — rendered to skeleton images automatically\\nPose names come from filenames.'}>""",
    """                 title={'Import a pose PACK into this set:\\n• .zip of control images — openpose skeletons, depth maps, DWpose renders\\n• openpose keypoint .json files — rendered to skeleton images automatically\\nPose names come from filenames; front/back/left/right in a filename sets the dominant angle.'}>""",
    "pack tooltip",
)

# ── 11. generate: match_angle + prediction line ────────────────────────
rep(
    """          slug, category: selectedSet || null,
          tags: selectedTags.length ? selectedTags : null, prompt_extra: extra,
          width: 832, height: 1216, seed: seed.trim() ? Number(seed.trim()) : null,""",
    """          slug, category: selectedSet || null,
          tags: selectedTags.length ? selectedTags : null, prompt_extra: extra,
          width: 832, height: 1216, seed: seed.trim() ? Number(seed.trim()) : null,
          match_angle: matchAngle,""",
    "generateSetRun match_angle",
)
rep(
    """          slug, pose_id: poseId, prompt_extra: extra, count,
          width: 832, height: 1216, seed: seed.trim() ? Number(seed.trim()) : null,""",
    """          slug, pose_id: poseId, prompt_extra: extra, count,
          width: 832, height: 1216, seed: seed.trim() ? Number(seed.trim()) : null,
          match_angle: matchAngle,""",
    "doGenerate match_angle",
)
rep(
    """          <div>
            <label style={label}>Extra prompt (outfit, scene, lighting — optional)</label>""",
    """          <div style={{ border: '1px solid #2a2f3a', borderRadius: 8, padding: 8, display: 'grid', gap: 4 }}>
            <label style={{ ...label, display: 'flex', gap: 6, alignItems: 'center', cursor: 'pointer' }}>
              <input type="checkbox" checked={matchAngle} onChange={(e) => setMatchAngle(e.target.checked)} />
              🧭 Match identity to the pose's dominant angle
            </label>
            <p style={{ ...hint, margin: 0 }}>
              {!matchAngle
                ? `off — every pose uses the active base (${identityFor('')})`
                : selectedSet || selectedTags.length
                  ? (() => {
                      const inSel = selectedSet
                        ? poses.filter((pp) => (pp.set || pp.category) === selectedSet && pp.has_image !== false)
                        : poses.filter((pp) => pp.has_image !== false && (pp.tags || []).some((t) => selectedTags.includes(t)));
                      const counts: Record<string, number> = {};
                      inSel.forEach((pp) => { const k = identityFor(pp.view); counts[k] = (counts[k] || 0) + 1; });
                      return Object.entries(counts).map(([k, n]) => `${n}× ${k}`).join('  ·  ') || 'no poses selected';
                    })()
                  : selPose
                    ? `pose angle: ${selPose.view || 'not set'} → identity: ${identityFor(selPose.view)}`
                    : 'pick a pose to see which base image it will use'}
            </p>
          </div>
          <div>
            <label style={label}>Extra prompt (outfit, scene, lighting — optional)</label>""",
    "match-angle control",
)

# ── 12. live batch + gallery show the identity that ran ────────────────
rep(
    """                {gen.workers?.length ? `  ·  threaded across ${gen.workers.length} worker${gen.workers.length > 1 ? 's' : ''}` : ''}
              </p>""",
    """                {gen.workers?.length ? `  ·  threaded across ${gen.workers.length} worker${gen.workers.length > 1 ? 's' : ''}` : ''}
                {gen.identity_source ? `  ·  🧭 identity: ${gen.identity_source}` : ''}
              </p>""",
    "live identity line",
)

assert src != orig
p.write_text(src, "utf-8")
print(f"patched {p} ({len(orig)} -> {len(src)} bytes)")
