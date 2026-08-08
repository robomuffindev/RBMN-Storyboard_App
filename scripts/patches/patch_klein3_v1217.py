"""v1.217 — DRESSED bases: strip becomes a choice, not a step.

His ask: "use our reference base and not a stripped version... in a lot of cases
we can just use our references we uploaded and the generated missing angles."

He is right, and stripping every time costs twice: an extra Klein edit per view,
AND the drift that edit introduces.  When the shot does not need a clothing
change, the uploaded reference IS the better identity image.

Two real bugs found on the way in, both squarely in the path of this feature:

  1. `ref_copy` base versions NEVER RECORDED A VIEW.  `_base_for_view` filters
     versions on `(v.get("view") or "") == view`, so a reference copied into the
     base set could never be matched to an angle — it was reachable only as the
     active base.  That is exactly the "use my uploaded reference" path.

  2. `upscaled` versions LOSE THEIR PROVENANCE.  The record keeps the view but
     not what it was upscaled FROM, and `_base_for_view` prefers upscaled first
     — so a dressed run would happily pick an upscale of a stripped image.
"""
import sys
from pathlib import Path

P = Path(sys.argv[1] if len(sys.argv) > 1 else "backend/api/klein3.py")
src = P.read_text("utf-8")
orig = src


def rep(old, new, why):
    global src
    n = src.count(old)
    assert n == 1, f"{why}: expected 1 match, found {n}"
    src = src.replace(old, new, 1)


# ── 1. mode helpers + a view-aware, provenance-aware picker ─────────────────
rep('''def _base_for_view(slug: str, c: dict, view: str) -> Tuple[Optional[Path], str]:
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
    return fp, ("active base" if not view else f"active base (no {view} view yet)")''',
    '''# ── Base MODE (v1.217) ───────────────────────────────────────────────────────
# "stripped" is a choice, not a stage.  Stripping costs an extra Klein edit per
# view AND introduces its own drift, so when a shot does not need the clothing
# replaced, the uploaded reference (or a generated missing view, which lands as
# a tagged ref) is the better identity image.
_BASE_MODES = ("auto", "dressed", "stripped")


def _ver_dressed(v: dict) -> Optional[bool]:
    """True = clothed, False = stripped, None = genuinely unknown.

    None is not a failure mode to paper over: base versions written before
    v1.217 recorded no provenance on an upscale, so claiming either way would be
    a guess.  Callers rank known matches first and fall back to unknown rather
    than dropping a whole tier — the same lesson as the v1.205 `ups or vers` bug,
    where an empty preferred tier skipped every candidate behind it."""
    kind = str(v.get("kind") or "")
    if kind.startswith("stripped_"):
        return False
    if kind == "ref_copy":
        return True
    if kind == "upscaled":
        from_kind = str(v.get("from_kind") or "")
        if from_kind.startswith("stripped_"):
            return False
        if from_kind:
            return True
        return None                      # pre-v1.217 upscale: unknowable
    return None


def _base_mode(c: dict, override: Optional[str] = None) -> str:
    """Per-request override wins, else the character's default, else auto."""
    for cand in (override, ((c.get("base") or {}).get("mode"))):
        m = str(cand or "").strip().lower()
        if m in _BASE_MODES:
            return m
    return "auto"


def _base_for_view(slug: str, c: dict, view: str,
                   mode: Optional[str] = None) -> Tuple[Optional[Path], str]:
    """Identity image for a pose's DOMINANT ANGLE (v1.205, mode-aware v1.217).

    Priority: an UPSCALED base version of that view -> any base version of that
    view -> a reference image tagged with that view -> the active base.  The
    second value LABELS which source won, so the job line, the gallery and the
    log all say which identity actually ran (never infer the code path).

    `mode` filters that list:
      dressed  -- clothed sources only.  Skips stripped versions and upscales OF
                  stripped versions; the tagged-reference tier is inherently
                  dressed, so a character with no dressed base still works off
                  his uploads and generated views with no strip run at all.
      stripped -- prefers stripped versions and upscales of them.
      auto     -- pre-v1.217 behaviour: newest of that view wins."""
    view = (view or "").strip().lower()
    mode = _base_mode(c, mode)
    want = {"dressed": True, "stripped": False}.get(mode)
    if view in VIEW_TAGS:
        vers = [v for v in ((c.get("base") or {}).get("versions") or [])
                if (v.get("view") or "") == view]
        ups = [v for v in vers if v.get("kind") == "upscaled"]
        # upscaled first (newest), then the rest of that view — a missing file
        # must fall through to the next candidate, not skip the whole tier
        ordered = list(reversed(ups)) + list(reversed([v for v in vers if v not in ups]))
        if want is not None:
            # exact matches first, then unknown-provenance, then never the
            # opposite kind — a dressed run must not silently use a nude base.
            ordered = ([v for v in ordered if _ver_dressed(v) is want]
                       + [v for v in ordered if _ver_dressed(v) is None])
        for pick in ordered:
            fp = _cdir(slug) / "base" / f"{pick['id']}.png"
            if fp.exists():
                known = _ver_dressed(pick)
                tag = "" if known is want or want is None else " · provenance unknown"
                return fp, f"{view} base ({pick.get('kind', 'base')}{tag})"
        for r in reversed(_refs_by_tag(c, view)):
            fp = _cdir(slug) / "refs" / f"{r['id']}.png"
            if fp.exists():
                # A reference is always clothed. In stripped mode that is a
                # fallback, not the request — say so instead of implying a strip.
                note = " · dressed fallback" if mode == "stripped" else ""
                gen = " (generated)" if r.get("source") == "generated" else ""
                return fp, f"{view} reference{gen}{note}"
    fp = _active_base_path(slug, c)
    if not fp:
        return None, "none"
    return fp, ("active base" if not view else f"active base (no {view} view yet)")''',
    "base: mode-aware picker")

# ── 2. BUG: a ref copied into the base set carried no view ──────────────────
rep('''    base["versions"].append({"id": vid, "kind": "ref_copy", "source_ref": r["id"],
                             "created_at": _now()})''',
    '''    # v1.217 BUG FIX: the view was never recorded, so `_base_for_view` — which
    # filters on `(v.get("view") or "") == view` — could never match a ref copy
    # to an angle.  It was reachable only as the active base, which is precisely
    # the "use my uploaded reference instead of a stripped one" path.
    base["versions"].append({"id": vid, "kind": "ref_copy", "source_ref": r["id"],
                             "view": str(r.get("tag") or "").strip().lower(),
                             "created_at": _now()})''',
    "ref_copy: record the view")

# ── 3. BUG: an upscale forgot what it was made from ─────────────────────────
rep('''            _src_view = next((v.get("view", "") for v in base.get("versions", [])
                              if v.get("id") == _act), "")
            base["versions"].append({"id": vid, "kind": "upscaled", "view": _src_view,
                                     "created_at": _now()})''',
    '''            _src = next((v for v in base.get("versions", [])
                         if v.get("id") == _act), {})
            _src_view = _src.get("view", "")
            # v1.217 BUG FIX: the record kept the view but not what it was
            # upscaled FROM — and `_base_for_view` prefers upscaled first, so a
            # dressed run would happily pick an upscale of a stripped image.
            base["versions"].append({"id": vid, "kind": "upscaled", "view": _src_view,
                                     "from_kind": str(_src.get("kind") or ""),
                                     "from_id": _act,
                                     "created_at": _now()})''',
    "upscaled: record provenance")

# ── 4. the mode reaches every caller ────────────────────────────────────────
rep('''    pose_source: str = "library"     # library | bodyfit (his body-matched mannequin)


@router.post("/generate")''',
    '''    pose_source: str = "library"     # library | bodyfit (his body-matched mannequin)
    base_mode: Optional[str] = None  # auto | dressed | stripped (None = character default)


@router.post("/generate")''',
    "GenerateIn: base_mode")

rep('''    identity_boost: bool = False
    pose_source: str = "library"     # library | bodyfit


@router.post("/generate-set")''',
    '''    identity_boost: bool = False
    pose_source: str = "library"     # library | bodyfit
    base_mode: Optional[str] = None  # auto | dressed | stripped


@router.post("/generate-set")''',
    "GenerateSetIn: base_mode")

src = src.replace("_base_for_view(body.slug, c, pose_view)",
                  "_base_for_view(body.slug, c, pose_view, body.base_mode)")
src = src.replace("_base_for_view(body.slug, c, p_view)",
                  "_base_for_view(body.slug, c, p_view, body.base_mode)")

# ── 5. a route to set the character's default, and expose it ────────────────
rep('''class UpscaleIn(BaseModel):''',
    '''class BaseModeIn(BaseModel):
    mode: str = "auto"               # auto | dressed | stripped


@router.put("/characters/{slug}/base-mode")
async def base_mode_set(slug: str, body: BaseModeIn):
    """The character's DEFAULT identity source.  `dressed` means his own clothes
    from the references; nothing has to be stripped for him to be usable."""
    mode = str(body.mode or "").strip().lower()
    if mode not in _BASE_MODES:
        raise HTTPException(400, f"mode must be one of {', '.join(_BASE_MODES)}")
    c = _load(slug)
    c.setdefault("base", {"versions": [], "active": None})["mode"] = mode
    _save(slug, c)
    # Show what each view WOULD resolve to under this mode — the point of the
    # toggle is that he can see the consequence before spending a render.
    resolved = {}
    for v in VIEW_TAGS:
        fp, label = _base_for_view(slug, c, v, mode)
        resolved[v] = {"found": bool(fp), "source": label}
    return {"mode": mode, "resolves_to": resolved}


class UpscaleIn(BaseModel):''',
    "route: base-mode")

rep('''        out["active_base"] = base.get("active")''',
    '''        out["active_base"] = base.get("active")
        out["base_mode"] = _base_mode(c)
        out["base_sources"] = {v: _base_for_view(slug, c, v)[1] for v in VIEW_TAGS}''',
    "public: expose the mode and what each view resolves to")

assert src != orig
P.write_text(src, "utf-8")
print(f"patched {P}  ({len(orig)} -> {len(src)} bytes)")
