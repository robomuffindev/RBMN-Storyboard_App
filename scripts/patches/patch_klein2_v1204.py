"""v1.204.0 — pose TAG editing + move/copy between SETS (backend, klein2.py).

Exact-match patcher: every replacement asserts exactly ONE occurrence.
Usage: python patch_klein2_v1204.py <path-to-klein2.py>
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


# ── 1. tag normalisation helper (right after _ensure_set) ───────────────────
rep(
    '''def _read_poses() -> List[dict]:
    if not _POSE_INDEX.exists():''',
    '''def _norm_tags(tags: Any) -> List[str]:
    """TAGS are pose METADATA (v1.203 model: sets are containers, tags are
    filters).  Accepts a list or a comma/semicolon-separated string; trims,
    de-dupes case-insensitively (first spelling wins), caps 8 tags x 32 chars."""
    if isinstance(tags, str):
        tags = tags.replace(";", ",").split(",")
    out: List[str] = []
    seen: set = set()
    for t in (tags or []):
        if t is None:
            continue
        s = str(t).strip()[:32]
        if not s or s.lower() in seen:
            continue
        seen.add(s.lower())
        out.append(s)
        if len(out) >= 8:
            break
    return out


def _read_poses() -> List[dict]:
    if not _POSE_INDEX.exists():''',
    "_norm_tags helper",
)

# ── 2. import route: cap/normalise tags through the same helper ────────────
rep(
    '''        tags: List[str] = []
        for src in (r.get("category"), r.get("tags")):
            for t in str(src or "").replace(";", ",").split(","):
                t = t.strip()
                if t and t not in tags:
                    tags.append(t)''',
    '''        tags = _norm_tags(f"{r.get('category') or ''},{r.get('tags') or ''}")''',
    "import route tag normalisation",
)

# ── 3. PoseUpdateIn gains set + tags ───────────────────────────────────────
rep(
    '''class PoseUpdateIn(BaseModel):
    name: Optional[str] = None
    category: Optional[str] = None
    prompt: Optional[str] = None
    regenerate: bool = False
    seed: Optional[int] = None''',
    '''class PoseUpdateIn(BaseModel):
    name: Optional[str] = None
    set: Optional[str] = None            # move the pose into this SET (container)
    category: Optional[str] = None       # legacy alias for `set`
    tags: Optional[List[str]] = None     # replace the pose's TAGS (metadata)
    prompt: Optional[str] = None
    regenerate: bool = False
    seed: Optional[int] = None''',
    "PoseUpdateIn fields",
)

# ── 4. pose_update: actually MOVE (set+category+registry) and set tags ─────
rep(
    '''    if body.name is not None:
        rec["name"] = body.name.strip() or rec["name"]
    if body.category is not None:
        rec["category"] = body.category.strip() or rec["category"]
    if body.prompt is not None:''',
    '''    if body.name is not None:
        rec["name"] = body.name.strip() or rec["name"]
    # v1.204: moving a pose means changing its SET — `category` is only the
    # legacy mirror, so writing it alone was a no-op (_read_poses re-mirrors it).
    _new_set = body.set if body.set is not None else body.category
    if _new_set is not None and str(_new_set).strip():
        _tgt = str(_new_set).strip()
        _ensure_set(_tgt)
        rec["set"] = _tgt
        rec["category"] = _tgt
    if body.tags is not None:
        rec["tags"] = _norm_tags(body.tags)
    if body.prompt is not None:''',
    "pose_update set/tags",
)

# ── 5. bulk routes (move/copy, tags, delete) before seed-defaults ─────────
rep(
    '''@router.post("/poses/seed-defaults")''',
    '''class PoseBulkMoveIn(BaseModel):
    ids: List[str]
    set: str                             # target SET (created if new)
    copy: bool = False                   # true = duplicate instead of move


@router.post("/poses/bulk-move")
async def poses_bulk_move(body: PoseBulkMoveIn):
    """Move (or COPY) poses into another SET.  A pose lives in exactly one set;
    copying duplicates the record AND its rendered image so both sets stay
    self-contained.  Name collisions inside the target set are disambiguated
    rather than dropped (per-set dupe scope, v1.202.1)."""
    tgt = (body.set or "").strip()
    if not tgt:
        raise HTTPException(400, "target set required")
    if not body.ids:
        raise HTTPException(400, "no poses selected")
    _ensure_set(tgt)
    items = _read_poses()
    by_id = {it.get("id"): it for it in items}
    taken = {str(it.get("name", "")).strip().lower()
             for it in items if (it.get("set") or "Custom") == tgt}
    moved = copied = missing = 0
    for pid in body.ids:
        rec = by_id.get(pid)
        if rec is None:
            missing += 1
            continue
        base_name = str(rec.get("name", "")).strip() or "Pose"
        name = base_name
        n = 2
        while name.lower() in taken:
            name = f"{base_name} ({n})"
            n += 1
        if body.copy:
            nid = uuid4().hex[:12]
            srcp = _K2_POSES / f"{pid}.png"
            if srcp.exists():
                try:
                    (_K2_POSES / f"{nid}.png").write_bytes(srcp.read_bytes())
                except Exception:  # noqa: BLE001
                    pass
            dup = dict(rec)
            dup.update({"id": nid, "name": name, "set": tgt, "category": tgt,
                        "tags": list(rec.get("tags") or []),
                        "created_at": _now_iso(), "updated_at": _now_iso()})
            items.append(dup)
            taken.add(name.lower())
            copied += 1
        else:
            if (rec.get("set") or "Custom") == tgt:
                continue                 # already there — not an error
            rec["name"] = name
            rec["set"] = tgt
            rec["category"] = tgt
            rec["updated_at"] = _now_iso()
            taken.add(name.lower())
            moved += 1
    _write_poses(items)
    return {"moved": moved, "copied": copied, "missing": missing, "set": tgt}


class PoseBulkTagsIn(BaseModel):
    ids: List[str]
    add: Optional[List[str]] = None
    remove: Optional[List[str]] = None
    replace: Optional[List[str]] = None  # wins over add/remove when present


@router.post("/poses/bulk-tags")
async def poses_bulk_tags(body: PoseBulkTagsIn):
    """Add / remove / replace TAGS on many poses at once (tags are filters —
    they never move a pose between sets)."""
    if not body.ids:
        raise HTTPException(400, "no poses selected")
    add = _norm_tags(body.add or [])
    rm = {t.lower() for t in _norm_tags(body.remove or [])}
    sel = set(body.ids)
    items = _read_poses()
    n = 0
    for it in items:
        if it.get("id") not in sel:
            continue
        if body.replace is not None:
            it["tags"] = _norm_tags(body.replace)
        else:
            cur = [t for t in (it.get("tags") or []) if str(t).lower() not in rm]
            it["tags"] = _norm_tags(cur + add)
        it["updated_at"] = _now_iso()
        n += 1
    _write_poses(items)
    return {"updated": n}


class PoseIdsIn(BaseModel):
    ids: List[str]


@router.post("/poses/bulk-delete")
async def poses_bulk_delete(body: PoseIdsIn):
    """Delete many poses (records + rendered images) in one call."""
    if not body.ids:
        raise HTTPException(400, "no poses selected")
    sel = set(body.ids)
    items = _read_poses()
    kept = []
    removed = 0
    for it in items:
        if it.get("id") in sel:
            try:
                (_K2_POSES / f"{it['id']}.png").unlink(missing_ok=True)
            except Exception:  # noqa: BLE001
                pass
            removed += 1
        else:
            kept.append(it)
    _write_poses(kept)
    return {"deleted": removed}


@router.post("/poses/seed-defaults")''',
    "bulk-move / bulk-tags / bulk-delete routes",
)

assert src != orig
p.write_text(src, "utf-8")
print(f"patched {p} ({len(orig)} -> {len(src)} bytes)")
