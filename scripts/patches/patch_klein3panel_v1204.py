"""v1.204.0 — pose TAG editing + move/copy between SETS (frontend, Klein3Panel.tsx).

Exact-match patcher: every replacement asserts exactly ONE occurrence.
Usage: python patch_klein3panel_v1204.py <path-to-Klein3Panel.tsx>
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


# ── 1. state ───────────────────────────────────────────────────────────────
rep(
    """  const [tagsSel, setTagsSel] = useState<string[]>([]);     // modal tag filter""",
    """  const [tagsSel, setTagsSel] = useState<string[]>([]);     // modal tag filter
  const [selMode, setSelMode] = useState(false);            // multi-select in the grid
  const [selIds, setSelIds] = useState<string[]>([]);
  const [bulkSet, setBulkSet] = useState('');               // bulk move/copy target SET
  const [bulkTag, setBulkTag] = useState('');               // bulk tag add/remove
  const [editSet, setEditSet] = useState('');               // per-pose SET (editor)
  const [editTags, setEditTags] = useState<string[]>([]);   // per-pose TAGS (editor)
  const [newTag, setNewTag] = useState('');""",
    "state",
)

# ── 2. editor open helper + save-meta + bulk actions (after savePose) ──────
rep(
    """  const createPose = async () => {""",
    """  const openPoseEditor = (p: PoseT) => {
    setEditPose(p); setEditPrompt(p.prompt);
    setEditSet(p.set || p.category || ''); setEditTags([...(p.tags || [])]); setNewTag('');
  };
  // SET = container (move), TAGS = metadata (filters) — saved without touching the prompt,
  // so uploaded/promptless poses can be organised too.
  const saveMeta = async () => {
    if (!editPose) return;
    setPoseBusy(true); setPoseMsg('');
    try {
      await j(await fetch(`${POSE_BASE}/poses/${editPose.id}/update`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ set: editSet || undefined, tags: editTags }),
      }));
      await loadPoses();
      setPoseMsg(`✓ “${editPose.name}” → set “${editSet}”${editTags.length ? ` · tags: ${editTags.join(', ')}` : ' · no tags'}`);
      setEditPose(null);
    } catch (e) { setPoseMsg((e as Error).message); }
    setPoseBusy(false);
  };
  const addEditTag = () => {
    const t = newTag.trim();
    if (!t) return;
    setEditTags((ts) => ts.some((x) => x.toLowerCase() === t.toLowerCase()) || ts.length >= 8 ? ts : [...ts, t]);
    setNewTag('');
  };
  const toggleSel = (id: string) =>
    setSelIds((ids) => ids.includes(id) ? ids.filter((x) => x !== id) : [...ids, id]);
  const bulkMove = async (copy: boolean) => {
    if (!selIds.length || !bulkSet) return;
    setPoseBusy(true); setPoseMsg('');
    try {
      const r = await j<{ moved: number; copied: number; missing: number }>(
        await fetch(`${POSE_BASE}/poses/bulk-move`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ ids: selIds, set: bulkSet, copy }),
        }));
      setSelIds([]); await loadPoses();
      setPoseMsg(`✓ ${copy ? `copied ${r.copied}` : `moved ${r.moved}`} pose(s) → set “${bulkSet}”${r.missing ? ` (${r.missing} missing)` : ''}`);
    } catch (e) { setPoseMsg((e as Error).message); }
    setPoseBusy(false);
  };
  const bulkTagOp = async (op: 'add' | 'remove') => {
    const t = bulkTag.trim();
    if (!selIds.length || !t) return;
    setPoseBusy(true); setPoseMsg('');
    try {
      const r = await j<{ updated: number }>(await fetch(`${POSE_BASE}/poses/bulk-tags`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ids: selIds, [op]: [t] }),
      }));
      setBulkTag(''); await loadPoses();
      setPoseMsg(`✓ tag “${t}” ${op === 'add' ? 'added to' : 'removed from'} ${r.updated} pose(s)`);
    } catch (e) { setPoseMsg((e as Error).message); }
    setPoseBusy(false);
  };
  const bulkDelete = async () => {
    if (!selIds.length) return;
    if (!window.confirm(`Delete ${selIds.length} pose(s) and their images?`)) return;
    setPoseBusy(true); setPoseMsg('');
    try {
      const r = await j<{ deleted: number }>(await fetch(`${POSE_BASE}/poses/bulk-delete`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ids: selIds }),
      }));
      if (selIds.includes(poseId)) setPoseId('');
      setSelIds([]); setEditPose(null); await loadPoses();
      setPoseMsg(`✓ deleted ${r.deleted} pose(s)`);
    } catch (e) { setPoseMsg((e as Error).message); }
    setPoseBusy(false);
  };
  const createPose = async () => {""",
    "meta + bulk actions",
)

# ── 3. header: ☑ select toggle (works on All too, so poses can be filed) ──
rep(
    """          <b style={{ fontSize: 14, color: '#e6e9ee' }}>{catFilter ? `📦 ${catFilter}` : '🌐 All poses'}</b>
          <div style={{ flex: 1 }} />""",
    """          <b style={{ fontSize: 14, color: '#e6e9ee' }}>{catFilter ? `📦 ${catFilter}` : '🌐 All poses'}</b>
          <button style={chip(selMode)} title="Multi-select poses to move/copy between sets, tag or delete"
                  onClick={() => { setSelMode((m) => !m); setSelIds([]); }}>
            {selMode ? '☑ selecting' : '☑ Select'}
          </button>
          <div style={{ flex: 1 }} />""",
    "select-mode toggle",
)

# ── 4. bulk bar + shared tag datalist, above the grid ─────────────────────
rep(
    """        {poseMsg && <p style={poseMsg.startsWith('✓') ? okTxt : errTxt}>{poseMsg}</p>}
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(84px, 1fr))', gap: 6, maxHeight: 260, overflowY: 'auto' }}>""",
    """        {poseMsg && <p style={poseMsg.startsWith('✓') ? okTxt : errTxt}>{poseMsg}</p>}
        <datalist id="k3-all-tags">{allTags.map((t) => <option key={t} value={t} />)}</datalist>
        {selMode && (
          <div style={{ border: '1px solid #3b82f6', borderRadius: 8, padding: 8, display: 'flex',
                        gap: 6, flexWrap: 'wrap', alignItems: 'center', background: '#0e1116' }}>
            <b style={{ fontSize: 12, color: '#e6e9ee' }}>☑ {selIds.length} selected</b>
            <button style={btnSm} onClick={() => setSelIds(shownPoses.map((p) => p.id))}>
              all shown ({shownPoses.length})
            </button>
            <button style={btnSm} disabled={!selIds.length} onClick={() => setSelIds([])}>clear</button>
            <span style={{ ...hint, marginLeft: 8 }}>📦 set:</span>
            <select style={{ ...input, width: 'auto', fontSize: 12, padding: '4px 6px' }}
                    value={bulkSet} onChange={(e) => setBulkSet(e.target.value)}>
              <option value="">— target set —</option>
              {setsInfo.map((si) => <option key={si.name} value={si.name}>{si.name}</option>)}
            </select>
            <button style={btnSm} disabled={poseBusy || !selIds.length || !bulkSet}
                    title="Move the selected poses INTO this set (a pose lives in exactly one set)"
                    onClick={() => void bulkMove(false)}>➡ Move</button>
            <button style={btnSm} disabled={poseBusy || !selIds.length || !bulkSet}
                    title="Copy the selected poses (record + image) into this set, keeping the originals"
                    onClick={() => void bulkMove(true)}>⧉ Copy</button>
            <span style={{ ...hint, marginLeft: 8 }}>🏷 tag:</span>
            <input list="k3-all-tags" style={{ ...input, width: 130, fontSize: 12, padding: '4px 6px' }}
                   placeholder="tag name" value={bulkTag} onChange={(e) => setBulkTag(e.target.value)}
                   onKeyDown={(e) => { if (e.key === 'Enter') void bulkTagOp('add'); }} />
            <button style={btnSm} disabled={poseBusy || !selIds.length || !bulkTag.trim()}
                    onClick={() => void bulkTagOp('add')}>＋ add</button>
            <button style={btnSm} disabled={poseBusy || !selIds.length || !bulkTag.trim()}
                    onClick={() => void bulkTagOp('remove')}>− remove</button>
            <div style={{ flex: 1 }} />
            <button style={{ ...btnSm, color: '#ff8a8a' }} disabled={poseBusy || !selIds.length}
                    onClick={() => void bulkDelete()}>🗑 delete</button>
          </div>
        )}
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(84px, 1fr))', gap: 6, maxHeight: 260, overflowY: 'auto' }}>""",
    "bulk bar",
)

# ── 5. tiles: selectable in select mode + tag line ────────────────────────
rep(
    """            <div key={p.id}
                 style={{ border: `2px solid ${poseId === p.id ? '#3b82f6' : '#2a2f3a'}`, borderRadius: 8, padding: 3,
                          cursor: p.has_image === false ? 'default' : 'pointer', background: '#0e1116',
                          opacity: p.has_image === false ? 0.75 : 1 }}
                 onClick={() => { if (p.has_image !== false) { setPoseId(p.id); setSelectedSet(''); setSelectedTags([]); setLibOpen(false); } }}>""",
    """            <div key={p.id}
                 style={{ border: `2px solid ${selMode && selIds.includes(p.id) ? '#22c55e' : poseId === p.id ? '#3b82f6' : '#2a2f3a'}`,
                          borderRadius: 8, padding: 3,
                          cursor: selMode || p.has_image !== false ? 'pointer' : 'default', background: '#0e1116',
                          opacity: p.has_image === false && !selMode ? 0.75 : 1 }}
                 onClick={() => {
                   if (selMode) { toggleSel(p.id); return; }   // select mode: click = pick, image or not
                   if (p.has_image !== false) { setPoseId(p.id); setSelectedSet(''); setSelectedTags([]); setLibOpen(false); }
                 }}>""",
    "tile select mode",
)

rep(
    """              <div style={{ fontSize: 10, color: '#cbd2dc', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{p.name}</div>
              <button style={{ ...btnSm, padding: '1px 5px', fontSize: 10 }}
                      onClick={(e) => { e.stopPropagation(); setEditPose(p); setEditPrompt(p.prompt); }}>
                {p.prompt ? '✏️' : 'ℹ️'}
              </button>""",
    """              <div style={{ fontSize: 10, color: '#cbd2dc', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                {selMode ? (selIds.includes(p.id) ? '☑ ' : '☐ ') : ''}{p.name}
              </div>
              {!!(p.tags || []).length && (
                <div style={{ fontSize: 9, color: '#8d97a5', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}
                     title={(p.tags || []).join(', ')}>🏷 {(p.tags || []).join(' · ')}</div>
              )}
              <button style={{ ...btnSm, padding: '1px 5px', fontSize: 10 }}
                      onClick={(e) => { e.stopPropagation(); openPoseEditor(p); }}>
                {p.prompt ? '✏️' : 'ℹ️'}
              </button>""",
    "tile tags + editor open",
)

# ── 6. editor: SET dropdown + TAG chips, works for promptless poses too ──
rep(
    """            <b style={{ fontSize: 12, color: '#e6e9ee' }}>✏️ {editPose.name} <span style={hint}>({editPose.source})</span></b>
            {editPose.prompt || editPose.source === 'generated' ? (""",
    """            <b style={{ fontSize: 12, color: '#e6e9ee' }}>✏️ {editPose.name} <span style={hint}>({editPose.source})</span></b>
            <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center' }}>
              <span style={hint}>📦 set</span>
              <select style={{ ...input, width: 'auto', fontSize: 12, padding: '4px 6px' }}
                      value={editSet} onChange={(e) => setEditSet(e.target.value)}>
                {!setsInfo.some((si) => si.name === editSet) && editSet && <option value={editSet}>{editSet}</option>}
                {setsInfo.map((si) => <option key={si.name} value={si.name}>{si.name}</option>)}
              </select>
              <span style={{ ...hint, marginLeft: 6 }}>🏷 tags</span>
              {editTags.map((t) => (
                <span key={t} style={{ ...chip(true), display: 'inline-flex', gap: 4, alignItems: 'center' }}>
                  {t}
                  <span style={{ cursor: 'pointer', color: '#ff8a8a' }} title="remove tag"
                        onClick={() => setEditTags((ts) => ts.filter((x) => x !== t))}>✕</span>
                </span>
              ))}
              {!editTags.length && <span style={hint}>none</span>}
              <input list="k3-all-tags" style={{ ...input, width: 120, fontSize: 12, padding: '4px 6px' }}
                     placeholder={editTags.length >= 8 ? 'max 8 tags' : '+ tag'} disabled={editTags.length >= 8}
                     value={newTag} onChange={(e) => setNewTag(e.target.value)}
                     onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); addEditTag(); } }} />
              <button style={btnSm} disabled={!newTag.trim() || editTags.length >= 8} onClick={addEditTag}>＋</button>
              <div style={{ flex: 1 }} />
              <button style={btnSm} disabled={poseBusy} title="Save the SET (move) and TAGS — leaves the prompt/image alone"
                      onClick={() => void saveMeta()}>💾 Save set + tags</button>
            </div>
            {editPose.prompt || editPose.source === 'generated' ? (""",
    "editor set + tags",
)

assert src != orig
p.write_text(src, "utf-8")
print(f"patched {p} ({len(orig)} -> {len(src)} bytes)")
