"""v1.205.0 — POSE DOMINANT ANGLE (view) in the shared pose library (klein2.py).

A pose now records which way the body faces (front/back/left/right); Klein 3.0
uses it to hand the pose the matching base view as identity (the measured
view-aware-identity win: a -124 deg pose went 0.744 -> 0.901).

Exact-match patcher: every replacement asserts exactly ONE occurrence.
Usage: python patch_klein2_v1205.py <path-to-klein2.py>
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


# ── 1. _norm_view helper (next to _norm_tags) ──────────────────────────────
rep(
    '''def _read_poses() -> List[dict]:
    if not _POSE_INDEX.exists():''',
    '''# The four base views a character owns (front/back/left/right).  A pose's
# DOMINANT ANGLE says which of them should supply the identity reference —
# handing a side pose the front base is what cost consistency before
# (see the verified view-aware-identity result: -124 deg pose 0.744 -> 0.901).
POSE_VIEWS = ["front", "back", "left", "right"]

_VIEW_WORDS = {
    "front": "front", "frontal": "front", "forward": "front", "anterior": "front",
    "f": "front", "front facing": "front", "front-facing": "front", "facing camera": "front",
    "toward camera": "front", "towards camera": "front", "0": "front",
    "back": "back", "rear": "back", "behind": "back", "posterior": "back", "b": "back",
    "back facing": "back", "back-facing": "back", "from behind": "back", "180": "back",
    "left": "left", "left side": "left", "side left": "left", "left profile": "left",
    "profile left": "left", "l": "left", "-90": "left", "270": "left",
    "right": "right", "right side": "right", "side right": "right", "right profile": "right",
    "profile right": "right", "r": "right", "90": "right",
}


def _norm_view(value: Any) -> str:
    """Normalise a dominant-angle value to one of POSE_VIEWS, or '' (unknown).

    Accepts words ('front', 'rear', '3/4 left', 'profile right'), and degrees
    using the calibrated convention front 0 / right +90 / left -90 / back 180.
    Ties break toward FRONT (the front base carries the face, which is what
    identity needs most): |a| <= 45 front, 45 < |a| < 135 side, >= 135 back.
    Deliberately returns '' for ambiguous words like 'side' or 'profile' —
    guessing the wrong side costs more than leaving it unset."""
    if value is None:
        return ""
    s = str(value).strip().lower()
    if not s:
        return ""
    s = s.replace("_", " ").replace("/", " ").replace("°", "").strip()
    s = " ".join(s.split())
    if s in _VIEW_WORDS:
        return _VIEW_WORDS[s]
    # numeric degrees
    try:
        a = float(s)
    except ValueError:
        a = None
    if a is not None:
        a = ((a + 180.0) % 360.0) - 180.0        # -> (-180, 180]
        if abs(a) <= 45:
            return "front"
        if abs(a) >= 135:
            return "back"
        return "right" if a > 0 else "left"
    # phrases: "three quarter left", "turned back left", "back 3 4 right" …
    has_back = any(w in s for w in ("back", "rear", "behind", "posterior"))
    has_left = "left" in s
    has_right = "right" in s
    has_front = any(w in s for w in ("front", "frontal", "forward", "facing camera"))
    if has_left and not has_right:
        return "left"
    if has_right and not has_left:
        return "right"
    if has_back and not has_front:
        return "back"
    if has_front:
        return "front"
    return ""


def _view_from_name(name: str) -> str:
    """Dominant angle guessed from a pose/file NAME (pack imports: 'pose_back_03').
    Word-boundary matching only — never guesses from a bare 'side'."""
    toks = [t for t in str(name or "").lower()
            .replace("-", " ").replace("_", " ").replace(".", " ").split() if t]
    for t in toks:
        if t in ("front", "frontal", "f"):
            return "front"
        if t in ("back", "rear", "behind", "b"):
            return "back"
        if t in ("left", "l"):
            return "left"
        if t in ("right", "r"):
            return "right"
    return ""


def _read_poses() -> List[dict]:
    if not _POSE_INDEX.exists():''',
    "_norm_view helpers",
)

# ── 2. _pose_public exposes the view ───────────────────────────────────────
rep(
    '''    return {"id": it["id"], "name": it.get("name", ""),
            "set": it.get("set", "Custom"), "tags": it.get("tags", []),''',
    '''    return {"id": it["id"], "name": it.get("name", ""),
            "set": it.get("set", "Custom"), "tags": it.get("tags", []),
            "view": it.get("view", ""),          # DOMINANT ANGLE (v1.205)''',
    "_pose_public view",
)

# ── 3. GET /poses reports the view breakdown (for filters/UI) ─────────────
rep(
    '''    tags = sorted({str(t) for it in items for t in (it.get("tags") or []) if str(t).strip()})''',
    '''    tags = sorted({str(t) for it in items for t in (it.get("tags") or []) if str(t).strip()})
    view_counts = {v: sum(1 for it in items if (it.get("view") or "") == v)
                   for v in POSE_VIEWS}
    view_counts[""] = sum(1 for it in items if not (it.get("view") or ""))''',
    "poses_list view counts",
)
rep(
    '''    return {"poses": [_pose_public(it) for it in items],
            "sets": set_stats, "tags": tags,''',
    '''    return {"poses": [_pose_public(it) for it in items],
            "sets": set_stats, "tags": tags, "views": view_counts,''',
    "poses_list payload",
)

# ── 4. import: per-row dominant angle (+ form default) ────────────────────
rep(
    '''async def poses_import(file: UploadFile = File(...), raw_all: bool = Form(False),
                       category: Optional[str] = Form(None)):''',
    '''async def poses_import(file: UploadFile = File(...), raw_all: bool = Form(False),
                       category: Optional[str] = Form(None),
                       view: Optional[str] = Form(None)):''',
    "import signature",
)
rep(
    '''        raw = bool(raw_all) or str(r.get("raw", "")).strip().lower() in ("1", "true", "yes", "y")
        rec = {"id": uuid4().hex[:12], "name": name, "set": tgt, "category": tgt,
               "tags": tags[:8],''',
    '''        raw = bool(raw_all) or str(r.get("raw", "")).strip().lower() in ("1", "true", "yes", "y")
        # DOMINANT ANGLE: explicit row field wins, then the form default, then a
        # guess from the pose NAME (never from the prompt text — too noisy).
        rec_view = _norm_view(r.get("view") or r.get("angle") or r.get("dominant_angle")
                              or r.get("facing") or r.get("direction"))
        if not rec_view:
            rec_view = _norm_view(view) or _view_from_name(name)
        rec = {"id": uuid4().hex[:12], "name": name, "set": tgt, "category": tgt,
               "tags": tags[:8], "view": rec_view,''',
    "import row view",
)

# ── 5. pack import: view from filename (+ form default) ──────────────────
rep(
    '''async def poses_import_pack(file: UploadFile = File(...), category: Optional[str] = Form(None)):''',
    '''async def poses_import_pack(file: UploadFile = File(...), category: Optional[str] = Form(None),
                            view: Optional[str] = Form(None)):''',
    "pack signature",
)
rep(
    '''        rec = {"id": pid, "name": nm, "set": cat, "category": cat,
               "tags": [], "prompt": "",
               "source": "upload", "seed": None,''',
    '''        rec = {"id": pid, "name": nm, "set": cat, "category": cat,
               "tags": [], "prompt": "",
               "view": _view_from_name(name) or _norm_view(view),
               "source": "upload", "seed": None,''',
    "pack rec view",
)

# ── 6. create / upload / update carry the view ───────────────────────────
rep(
    '''    prompt: str                      # the pose description (style wrapper added unless raw)
    category: str = "Custom"''',
    '''    prompt: str                      # the pose description (style wrapper added unless raw)
    category: str = "Custom"
    view: Optional[str] = None       # DOMINANT ANGLE: front|back|left|right''',
    "PoseCreateIn view",
)
rep(
    '''    rec = {"id": pid, "name": body.name.strip(), "set": tgt, "category": tgt,
           "tags": [], "prompt": full, "source": "generated", "seed": seed,''',
    '''    rec = {"id": pid, "name": body.name.strip(), "set": tgt, "category": tgt,
           "tags": [], "view": _norm_view(body.view) or _view_from_name(body.name),
           "prompt": full, "source": "generated", "seed": seed,''',
    "pose_create view",
)
rep(
    '''async def pose_upload(file: UploadFile = File(...), name: str = Form("Uploaded pose"),
                      category: str = Form("Uploaded")):''',
    '''async def pose_upload(file: UploadFile = File(...), name: str = Form("Uploaded pose"),
                      category: str = Form("Uploaded"), view: Optional[str] = Form(None)):''',
    "pose_upload signature",
)
rep(
    '''    rec = {"id": pid, "name": name.strip() or "Uploaded pose",
           "set": tgt, "category": tgt, "tags": [], "prompt": "",''',
    '''    rec = {"id": pid, "name": name.strip() or "Uploaded pose",
           "set": tgt, "category": tgt, "tags": [], "prompt": "",
           "view": _norm_view(view) or _view_from_name(name),''',
    "pose_upload view",
)
rep(
    '''    set: Optional[str] = None            # move the pose into this SET (container)
    category: Optional[str] = None       # legacy alias for `set`
    tags: Optional[List[str]] = None     # replace the pose's TAGS (metadata)''',
    '''    set: Optional[str] = None            # move the pose into this SET (container)
    category: Optional[str] = None       # legacy alias for `set`
    tags: Optional[List[str]] = None     # replace the pose's TAGS (metadata)
    view: Optional[str] = None           # DOMINANT ANGLE ('' clears it)''',
    "PoseUpdateIn view",
)
rep(
    '''    if body.tags is not None:
        rec["tags"] = _norm_tags(body.tags)
    if body.prompt is not None:''',
    '''    if body.tags is not None:
        rec["tags"] = _norm_tags(body.tags)
    if body.view is not None:
        rec["view"] = _norm_view(body.view)      # '' clears the angle
    if body.prompt is not None:''',
    "pose_update view",
)

# ── 7. bulk-view route ───────────────────────────────────────────────────
rep(
    '''class PoseIdsIn(BaseModel):
    ids: List[str]''',
    '''class PoseViewIn(BaseModel):
    ids: List[str]
    view: str = ""                       # front|back|left|right, '' clears


@router.post("/poses/bulk-view")
async def poses_bulk_view(body: PoseViewIn):
    """Set the DOMINANT ANGLE on many poses at once ('' clears it)."""
    if not body.ids:
        raise HTTPException(400, "no poses selected")
    v = _norm_view(body.view)
    if body.view and not v:
        raise HTTPException(400, f"view must be one of {', '.join(POSE_VIEWS)} (or empty)")
    sel = set(body.ids)
    items = _read_poses()
    n = 0
    for it in items:
        if it.get("id") in sel:
            it["view"] = v
            it["updated_at"] = _now_iso()
            n += 1
    _write_poses(items)
    return {"updated": n, "view": v}


class PoseIdsIn(BaseModel):
    ids: List[str]''',
    "bulk-view route",
)

# ── 8. bulk-move / copy carry the view (dict copy already does; explicit) ──
rep(
    '''            dup.update({"id": nid, "name": name, "set": tgt, "category": tgt,
                        "tags": list(rec.get("tags") or []),''',
    '''            dup.update({"id": nid, "name": name, "set": tgt, "category": tgt,
                        "tags": list(rec.get("tags") or []), "view": rec.get("view", ""),''',
    "bulk copy view",
)

# ── 9. built-in defaults get their angles ────────────────────────────────
rep(
    '''_POSE_SETS_INDEX = _K2_POSES / "sets.json"''',
    '''# Dominant angle for the built-in starter poses (everything unlisted = front).
_DEFAULT_VIEWS = {
    "Looking over shoulder": "back",
    "Leaning on a wall": "left",
    "Fighting stance": "right",
    "Walking": "front",
}


_POSE_SETS_INDEX = _K2_POSES / "sets.json"''',
    "_DEFAULT_VIEWS",
)


# ── 10. seeded defaults carry their angle ────────────────────────────────
rep(
    '''                items.append({"id": pid, "name": nm, "set": "Defaults",
                              "category": "Defaults", "tags": [cat], "prompt": full,''',
    '''                items.append({"id": pid, "name": nm, "set": "Defaults",
                              "category": "Defaults", "tags": [cat], "prompt": full,
                              "view": _DEFAULT_VIEWS.get(nm, "front"),''',
    "defaults seeding view",
)

assert src != orig
p.write_text(src, "utf-8")
print(f"patched {p} ({len(orig)} -> {len(src)} bytes)")
