"""v1.206.0 — pose DESCRIPTIONS in the Klein 3.0 panel (Klein3Panel.tsx).

Editor gains the description (editable, with 🔍 Describe via the vision LLM),
the modal gains a "🔍 Describe missing (N)" pass with live per-server status and
an auto-describe toggle for pack imports, and the generate box shows exactly
what pose text will be sent — plus a switch to turn it off.

Exact-match patcher: every replacement asserts exactly ONE occurrence.
Usage: python patch_klein3panel_v1206.py <path-to-Klein3Panel.tsx>
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
    """interface PoseT { id: string; name: string; category: string; set?: string; tags?: string[]; view?: string; prompt: string; source: string; url: string; seed?: number | null; has_image?: boolean }""",
    """interface PoseT { id: string; name: string; category: string; set?: string; tags?: string[]; view?: string; desc?: string; desc_source?: string; prompt: string; source: string; url: string; seed?: number | null; has_image?: boolean }
interface DescRunT { status: string; done: number; total: number; servers?: string[]; errors?: string[]; tasks?: Record<string, { name: string; server?: string | null; status: string }> }""",
    "PoseT.desc + DescRunT",
)
rep(
    """  pose_view?: string | null; identity_source?: string | null;
}""",
    """  pose_view?: string | null; identity_source?: string | null; pose_desc?: string | null;
}""",
    "GenT.pose_desc",
)

# ── 2. state ─────────────────────────────────────────────────────────────
rep(
    """  const [matchAngle, setMatchAngle] = useState(true);       // angle-matched identity""",
    """  const [matchAngle, setMatchAngle] = useState(true);       // angle-matched identity
  const [describePose, setDescribePose] = useState(true);   // send the pose in WORDS
  const [editDesc, setEditDesc] = useState('');             // per-pose description
  const [descRun, setDescRun] = useState<DescRunT | null>(null);
  const [autoDescribe, setAutoDescribe] = useState(true);   // describe imported pack images""",
    "state",
)

# ── 3. loadPoses picks up desc_run ───────────────────────────────────────
rep(
    """      const r = await j<{ poses: PoseT[]; categories: string[]; sets?: SetInfoT[]; tags?: string[]; seed_run: any; batch_run: BatchRunT | null }>(""",
    """      const r = await j<{ poses: PoseT[]; categories: string[]; sets?: SetInfoT[]; tags?: string[]; seed_run: any; batch_run: BatchRunT | null; desc_run?: DescRunT | null }>(""",
    "loadPoses response type",
)
rep(
    """      setBatchRun(r.batch_run && r.batch_run.status ? r.batch_run : null);""",
    """      setBatchRun(r.batch_run && r.batch_run.status ? r.batch_run : null);
      setDescRun(r.desc_run && r.desc_run.status ? r.desc_run : null);""",
    "loadPoses desc_run",
)

# ── 4. editor open + saveMeta + describe actions ────────────────────────
rep(
    """    setEditView(p.view || '');""",
    """    setEditView(p.view || ''); setEditDesc(p.desc || '');""",
    "openPoseEditor desc",
)
rep(
    """        body: JSON.stringify({ set: editSet || undefined, tags: editTags, view: editView }),""",
    """        body: JSON.stringify({ set: editSet || undefined, tags: editTags, view: editView,
                              desc: editDesc }),""",
    "saveMeta desc",
)
rep(
    """  const bulkViewSet = async (v: string) => {""",
    """  // Vision-LLM describe pass — ids (selection / one pose) or the open set.
  const describePoses = async (ids?: string[], overwrite = false) => {
    setPoseMsg('');
    try {
      const r = await j<{ started: boolean; total?: number; servers?: number; note?: string }>(
        await fetch(`${POSE_BASE}/poses/describe`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(ids?.length ? { ids, overwrite }
                                           : { category: catFilter || null, overwrite }),
        }));
      if (!r.started) { setPoseMsg(r.note || 'nothing to describe'); return; }
      setDescRun({ status: 'running', done: 0, total: r.total || 0 });
      setPoseMsg(`🔍 describing ${r.total} pose(s) across ${r.servers || 1} LLM server(s)…`);
      await loadPoses();
    } catch (e) { setPoseMsg((e as Error).message); }
  };
  const bulkViewSet = async (v: string) => {""",
    "describePoses action",
)

# ── 5. poll while a describe pass runs (reuse the pose poll) ────────────
rep(
    """  const bulkDelete = async () => {""",
    """  useEffect(() => {
    if (descRun?.status !== 'running') return;
    const t = setInterval(() => { void loadPoses(); }, 4000);
    return () => clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [descRun?.status]);

  const bulkDelete = async () => {""",
    "describe poll",
)

# ── 6. modal header button + live line ─────────────────────────────────
rep(
    """          {(() => {
            const missing = poses.filter((p) => p.prompt && p.has_image === false""",
    """          {(() => {
            const noDesc = poses.filter((p) => p.has_image !== false && !(p.desc || '').trim()
              && (!catFilter || (p.set || p.category) === catFilter)).length;
            return noDesc > 0 ? (
              <button style={{ ...btnSm, borderColor: '#8b5cf6', color: '#c4b5fd' }}
                      disabled={descRun?.status === 'running'}
                      title={'Ask the vision LLM to describe these pose IMAGES in words.\\nThe description is sent with the render so limbs land on the right body parts,\\nand an empty dominant angle gets filled in at the same time.'}
                      onClick={() => void describePoses()}>
                {descRun?.status === 'running'
                  ? `🔍 ${descRun.done}/${descRun.total}…`
                  : `🔍 Describe missing (${noDesc})`}
              </button>
            ) : null;
          })()}
          <label style={{ ...hint, display: 'inline-flex', gap: 4, alignItems: 'center', cursor: 'pointer' }}
                 title="After importing a pack of pose images, describe them with the vision LLM automatically">
            <input type="checkbox" checked={autoDescribe} onChange={(e) => setAutoDescribe(e.target.checked)} />
            auto-describe imports
          </label>
          {(() => {
            const missing = poses.filter((p) => p.prompt && p.has_image === false""",
    "describe-missing button",
)
rep(
    """        {batchRun?.status === 'running' && batchRun.tasks && (""",
    """        {descRun?.status === 'running' && (
          <p style={{ ...hint, margin: 0 }}>
            🔍 {descRun.done}/{descRun.total}
            {descRun.tasks ? '  ·  ' + (Object.values(descRun.tasks)
              .filter((t) => t.status === 'running')
              .map((t) => `${t.name}${t.server ? ` @ ${t.server}` : ''} ⏳`).join('  ·  ') || 'queueing…') : ''}
          </p>
        )}
        {descRun && descRun.status !== 'running' && !!descRun.errors?.length && (
          <p style={errTxt}>describe: {descRun.errors.slice(0, 2).join('; ')}</p>
        )}
        {batchRun?.status === 'running' && batchRun.tasks && (""",
    "describe live line",
)

# ── 7. pack import can chain into a describe pass ──────────────────────
rep(
    """      setPoseMsg(`✓ pack: ${r.imported} poses imported${r.skipped ? `, ${r.skipped} dupes skipped` : ''}${r.errors?.length ? ` — ${r.errors.length} errors (${r.errors[0]})` : ''}`);""",
    """      setPoseMsg(`✓ pack: ${r.imported} poses imported${r.skipped ? `, ${r.skipped} dupes skipped` : ''}${r.errors?.length ? ` — ${r.errors.length} errors (${r.errors[0]})` : ''}`);
      if (autoDescribe && r.imported > 0) {
        const ids = (r.poses || []).map((pp) => pp.id);
        if (ids.length) await describePoses(ids);
      }""",
    "pack auto-describe",
)
rep(
    """      const r = await j<{ imported: number; skipped: number; errors: string[] }>(
        await fetch(`${POSE_BASE}/poses/import-pack`, { method: 'POST', body: fd }));""",
    """      const r = await j<{ imported: number; skipped: number; errors: string[]; poses?: PoseT[] }>(
        await fetch(`${POSE_BASE}/poses/import-pack`, { method: 'POST', body: fd }));""",
    "pack import type",
)

# ── 8. uploaded single pose: offer the same ────────────────────────────
rep(
    """      await j(await fetch(`${POSE_BASE}/poses/upload`, { method: 'POST', body: fd }));
      await loadPoses(); setPoseMsg('✓ uploaded (openpose/depth/photo all fine)');""",
    """      const up = await j<PoseT>(await fetch(`${POSE_BASE}/poses/upload`, { method: 'POST', body: fd }));
      await loadPoses(); setPoseMsg('✓ uploaded (openpose/depth/photo all fine)');
      if (autoDescribe && up?.id) await describePoses([up.id]);""",
    "upload auto-describe",
)

# ── 9. tile badge for "has words" ──────────────────────────────────────
rep(
    """              {!!(p.tags || []).length && (""",
    """              {(p.desc || '').trim() ? (
                <div style={{ fontSize: 9, color: '#c4b5fd', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}
                     title={p.desc || ''}>🗒 {p.desc}</div>
              ) : p.has_image !== false ? (
                <div style={{ fontSize: 9, color: '#5c6472' }} title="no pose description — hit 🔍 Describe">🗒 —</div>
              ) : null}
              {!!(p.tags || []).length && (""",
    "tile desc badge",
)

# ── 10. editor: the description itself ─────────────────────────────────
rep(
    """            {editPose.prompt || editPose.source === 'generated' ? (""",
    """            <div style={{ display: 'grid', gap: 4 }}>
              <div style={{ display: 'flex', gap: 6, alignItems: 'center', flexWrap: 'wrap' }}>
                <span style={hint}>🗒 pose description (sent WITH the pose image at render time)</span>
                {editPose.desc_source && <span style={hint}>· from {editPose.desc_source}</span>}
                <div style={{ flex: 1 }} />
                <button style={btnSm} disabled={poseBusy || descRun?.status === 'running' || editPose.has_image === false}
                        title="Ask the vision LLM to describe this pose image (overwrites the current text)"
                        onClick={() => void describePoses([editPose.id], true)}>🔍 Describe with LLM</button>
              </div>
              <textarea style={{ ...input, minHeight: 48, fontSize: 12 }} value={editDesc}
                        onChange={(e) => setEditDesc(e.target.value)}
                        placeholder="e.g. standing with both hands on the hips, elbows out, weight on the right leg" />
            </div>
            {editPose.prompt || editPose.source === 'generated' ? (""",
    "editor description",
)

# ── 11. generate box: the switch + what will be sent ──────────────────
rep(
    """            <p style={{ ...hint, margin: 0 }}>
              {!matchAngle""",
    """            <label style={{ ...label, display: 'flex', gap: 6, alignItems: 'center', cursor: 'pointer' }}>
              <input type="checkbox" checked={describePose} onChange={(e) => setDescribePose(e.target.checked)} />
              🗒 Send the pose description with the image
            </label>
            {describePose && (
              <p style={{ ...hint, margin: 0 }}>
                {selectedSet || selectedTags.length
                  ? (() => {
                      const inSel = selectedSet
                        ? poses.filter((pp) => (pp.set || pp.category) === selectedSet && pp.has_image !== false)
                        : poses.filter((pp) => pp.has_image !== false && (pp.tags || []).some((t) => selectedTags.includes(t)));
                      const withText = inSel.filter((pp) => (pp.desc || '').trim()).length;
                      return `🗒 ${withText}/${inSel.length} poses have a description${withText < inSel.length ? ' — 🔍 Describe missing in the library to cover the rest' : ''}`;
                    })()
                  : selPose
                    ? ((selPose.desc || '').trim()
                        ? `🗒 “${(selPose.desc || '').slice(0, 140)}${(selPose.desc || '').length > 140 ? '…' : ''}”`
                        : '🗒 this pose has no description — 🔍 Describe it in the library for better limb placement')
                    : ''}
              </p>
            )}
            <p style={{ ...hint, margin: 0 }}>
              {!matchAngle""",
    "generate box describe switch",
)
rep(
    """          match_angle: matchAngle,
        }),
      }));
      await loadCur(); await loadGens();""",
    """          match_angle: matchAngle, describe_pose: describePose,
        }),
      }));
      await loadCur(); await loadGens();""",
    "generateSetRun describe_pose",
)
rep(
    """          match_angle: matchAngle,
        }),
      }));
      setGen({""",
    """          match_angle: matchAngle, describe_pose: describePose,
        }),
      }));
      setGen({""",
    "doGenerate describe_pose",
)


# ── 12. LLM guide: the prompt doubles as the render description ────────
rep(
    """Rules for "view" — the DOMINANT ANGLE (important, do not skip):""",
    """Your "prompt" text is used TWICE: it renders the pose mannequin image, and it
is also sent to the final render as the written description of the pose. That
second use is what keeps limbs correct on bodies whose build differs from the
mannequin's (a heavy character's "hands on hips" must not become hands on the
belly). So name the body landmarks each hand and foot touches — "hands resting
on the hip bones at the sides of the waist", "right foot flat on the ground",
"left palm flat on the chest" — rather than vague placement.

Rules for "view" — the DOMINANT ANGLE (important, do not skip):""",
    "LLM doc: prompt is also the render description",
)

assert src != orig
p.write_text(src, "utf-8")
print(f"patched {p} ({len(orig)} -> {len(src)} bytes)")
