"""v1.205.0 — angle-matched identity in Klein 3.0 generation (klein3.py).

A pose's DOMINANT ANGLE (front/back/left/right) now picks the identity image:
the matching base view instead of whatever happens to be active.  This is the
same mechanism that measured 0.744 -> 0.901 on a -124 deg pose in the clay lane.

Exact-match patcher: every replacement asserts exactly ONE occurrence.
Usage: python patch_klein3_v1205.py <path-to-klein3.py>
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


# ── 0. Tuple import (used by _base_for_view's return type) ────────────────
rep(
    """from typing import Any, Dict, List, Optional""",
    """from typing import Any, Dict, List, Optional, Tuple""",
    "typing import",
)

# ── 1. _base_for_view ─────────────────────────────────────────────────────
rep(
    '''def _identity_ref_paths(slug: str, c: dict, limit: int = 3) -> List[str]:''',
    '''def _base_for_view(slug: str, c: dict, view: str) -> Tuple[Optional[Path], str]:
    """Identity image for a pose's DOMINANT ANGLE (v1.205).

    Priority: an UPSCALED base version of that view -> any base version of that
    view -> a reference image tagged with that view -> the active base.  The
    second value LABELS which source won, so the job line, the gallery and the
    log all say which identity actually ran (never infer the code path)."""
    view = (view or "").strip().lower()
    if view in VIEW_TAGS:
        vers = [v for v in ((c.get("base") or {}).get("versions") or [])
                if (v.get("view") or "") == view]
        ups = [v for v in vers if v.get("kind") == "upscaled"]
        # upscaled first (newest), then the rest of that view — a missing file
        # must fall through to the next candidate, not skip the whole tier
        ordered = list(reversed(ups)) + list(reversed([v for v in vers if v not in ups]))
        for pick in ordered:
            fp = _cdir(slug) / "base" / f"{pick['id']}.png"
            if fp.exists():
                return fp, f"{view} base ({pick.get('kind', 'base')})"
        for r in reversed(_refs_by_tag(c, view)):
            fp = _cdir(slug) / "refs" / f"{r['id']}.png"
            if fp.exists():
                return fp, f"{view} reference"
    fp = _active_base_path(slug, c)
    if not fp:
        return None, "none"
    return fp, ("active base" if not view else f"active base (no {view} view yet)")


def _identity_ref_paths(slug: str, c: dict, limit: int = 3) -> List[str]:''',
    "_base_for_view",
)

# ── 2. upscaled versions inherit the view they were made from ────────────
rep(
    '''            base["versions"].append({"id": vid, "kind": "upscaled",
                                     "created_at": _now()})''',
    '''            # keep the view label so angle matching still works after upscaling
            _act = base.get("active")
            _src_view = next((v.get("view", "") for v in base.get("versions", [])
                              if v.get("id") == _act), "")
            base["versions"].append({"id": vid, "kind": "upscaled", "view": _src_view,
                                     "created_at": _now()})''',
    "upscale keeps view",
)

# ── 3. single generation: angle-matched identity ─────────────────────────
rep(
    '''    count: int = 2
    width: int = 832
    height: int = 1216
    seed: Optional[int] = None


@router.post("/generate")''',
    '''    count: int = 2
    width: int = 832
    height: int = 1216
    seed: Optional[int] = None
    match_angle: bool = True         # use the pose's DOMINANT ANGLE base as identity


@router.post("/generate")''',
    "GenerateIn.match_angle",
)
rep(
    '''    pose_fp = _K2_POSES / f"{body.pose_id}.png"
    if not pose_fp.exists():
        raise HTTPException(409, "pose image missing — regenerate it in the library")
''',
    '''    pose_fp = _K2_POSES / f"{body.pose_id}.png"
    if not pose_fp.exists():
        raise HTTPException(409, "pose image missing — regenerate it in the library")

    # v1.205: hand the pose the base view that faces the same way it does.
    pose_view = (pose.get("view") or "") if body.match_angle else ""
    ident, ident_src = _base_for_view(body.slug, c, pose_view)
    if ident:
        base = ident
''',
    "generate identity pick",
)
rep(
    '''    st = {"status": "running", "character": c.get("name", body.slug), "slug": body.slug,
          "pose": pose.get("name"), "pose_id": body.pose_id, "prompt": prompt,
          "total": count, "done": 0, "images": [], "error": None,''',
    '''    st = {"status": "running", "character": c.get("name", body.slug), "slug": body.slug,
          "pose": pose.get("name"), "pose_id": body.pose_id, "prompt": prompt,
          "pose_view": pose_view, "identity_source": ident_src,
          "total": count, "done": 0, "images": [], "error": None,''',
    "generate st identity",
)
rep(
    '''    logger.info("klein3 generate[%s]: %s pose=%s count=%d", gid, body.slug,
                pose.get("name"), count)
    return {"gen_id": gid, "total": count, "prompt": prompt, "seed": base_seed}''',
    '''    logger.info("klein3 generate[%s]: %s pose=%s view=%s identity=%s count=%d", gid,
                body.slug, pose.get("name"), pose_view or "-", ident_src, count)
    return {"gen_id": gid, "total": count, "prompt": prompt, "seed": base_seed,
            "pose_view": pose_view, "identity_source": ident_src}''',
    "generate log + return",
)

# ── 4. gallery/live payload carries it ───────────────────────────────────
rep(
    '''            "slug": st.get("slug"), "pose": st.get("pose"), "pose_id": st.get("pose_id"),
            "set": st.get("set"),''',
    '''            "slug": st.get("slug"), "pose": st.get("pose"), "pose_id": st.get("pose_id"),
            "set": st.get("set"), "pose_view": st.get("pose_view"),
            "identity_source": st.get("identity_source"),''',
    "_gen_public identity",
)

# ── 5. set/tag runs: per-pose angle matching ─────────────────────────────
rep(
    '''    prompt_extra: str = ""
    width: int = 832
    height: int = 1216
    seed: Optional[int] = None


@router.post("/generate-set")''',
    '''    prompt_extra: str = ""
    width: int = 832
    height: int = 1216
    seed: Optional[int] = None
    match_angle: bool = True         # per-pose DOMINANT ANGLE identity


@router.post("/generate-set")''',
    "GenerateSetIn.match_angle",
)
rep(
    '''    gen_map: Dict[str, tuple] = {}
    for i, p in enumerate(poses):
        gid = uuid4().hex[:12]
        gd = _gen_dir(gid)
        gd.mkdir(parents=True, exist_ok=True)
        shutil.copy2(base, gd / "ref_identity.png")''',
    '''    gen_map: Dict[str, tuple] = {}
    angle_used: Dict[str, int] = {}
    for i, p in enumerate(poses):
        gid = uuid4().hex[:12]
        gd = _gen_dir(gid)
        gd.mkdir(parents=True, exist_ok=True)
        # v1.205: each pose gets the base view matching ITS dominant angle
        p_view = (p.get("view") or "") if body.match_angle else ""
        p_base, p_src = _base_for_view(body.slug, c, p_view)
        angle_used[p_src] = angle_used.get(p_src, 0) + 1
        shutil.copy2(p_base or base, gd / "ref_identity.png")''',
    "generate-set identity pick",
)
rep(
    '''        gst = {"status": "running", "character": c.get("name", body.slug), "slug": body.slug,
               "pose": p.get("name"), "pose_id": p["id"], "set": label,''',
    '''        gst = {"status": "running", "character": c.get("name", body.slug), "slug": body.slug,
               "pose": p.get("name"), "pose_id": p["id"], "set": label,
               "pose_view": p_view, "identity_source": p_src,''',
    "generate-set gst identity",
)
rep(
    '''    st.clear()
    st.update({"status": "running", "detail": f"{label} 0/{len(poses)}",
               "error": None, "set": label, "total": len(poses)})''',
    '''    st.clear()
    st.update({"status": "running", "detail": f"{label} 0/{len(poses)}",
               "error": None, "set": label, "total": len(poses),
               "identities": angle_used})
    logger.info("klein3 generate-set[%s]: %s poses=%d identities=%s", body.slug, label,
                len(poses), angle_used)''',
    "generate-set identity summary",
)

assert src != orig
p.write_text(src, "utf-8")
print(f"patched {p} ({len(orig)} -> {len(src)} bytes)")
