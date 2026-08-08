"""v1.219 — AUDIT FIXES.  Four defects, all mine, found by auditing my own claims.

1. **THE OUTFIT FEATURE WAS INERT.**  `_build_plan` reads `opts["outfits"]`, and
   NEITHER route put it there: create passed `{**options, "preset": ...}` and
   re-plan passed `ds["options"]`.  So every planned row got `outfit: None` and
   the whole of v1.216 did nothing through the API.  test_v1216 passed because
   it called `_build_plan(104, {"outfits": WARDROBE})` DIRECTLY — it tested the
   function and never the wiring, which is the failure the test existed to catch.

2. **The set was never auto-sized.**  He chose "scale automatically with outfit
   count"; `_suggested_count` was only ever *returned* by the outfits routes and
   never applied.  With the UI's default 40 and eight outfits that is five images
   each — and measured, outfits then span only 2 of 4 framings, which is exactly
   the clumping v1.216 claims to fix.  It holds at 104; it does not at 40.

3. **The no-face counter was dead code.**  `identity_method` was set to
   "arcface" only when a score came back, and `_flag_summary` counted
   `method == "arcface" AND score is None` — unreachable by construction.
   "ArcFace ran and found no face" and "ArcFace never ran" are different facts
   and must be distinguishable.

4. Same in the `/likeness` route.
"""
import sys
from pathlib import Path

P = Path(sys.argv[1] if len(sys.argv) > 1 else "backend/api/lora.py")
src = P.read_text("utf-8")
orig = src


def rep(old, new, why):
    global src
    n = src.count(old)
    assert n == 1, f"{why}: expected 1 match, found {n}"
    src = src.replace(old, new, 1)


# ── 1. one place that builds the planner's options, used by BOTH routes ─────
rep('''def _by_key(seq, key):''',
    '''def _plan_opts(ds: dict) -> dict:
    """The options `_build_plan` actually needs, assembled in ONE place.

    v1.219: both routes used to build this inline and both forgot `outfits`, so
    every row planned through the API came out with no outfit at all.  A single
    builder means a new planner input cannot be wired into one caller and
    silently missed in the other."""
    return {**(ds.get("options") or {}),
            "preset": ds.get("preset") or (ds.get("options") or {}).get("preset") or "balanced",
            "outfits": ds.get("outfits") or []}


def _plan_warnings(count: int, outfits: List[dict]) -> List[str]:
    """Said out loud, not silently absorbed.  A wardrobe spread too thin is the
    exact failure v1.216 exists to prevent, and it is invisible until training."""
    out: List[str] = []
    n = len(outfits or [])
    if n >= 2:
        per = count / n
        if per < 8:
            out.append(f"{n} outfits over {count} images is ~{per:.0f} each — measured, some "
                       f"outfits then appear in only 2 of the 4 framings, which trains "
                       f"'that outfit means that shot type'. {_suggested_count(n)} is the "
                       f"sized-for-this-wardrobe count.")
        elif per < 12:
            out.append(f"{n} outfits over {count} images is ~{per:.0f} each — workable, but "
                       f"{_suggested_count(n)} gives every outfit a full spread of framings.")
    return out


def _by_key(seq, key):''',
    "plan opts builder")

# ── 2. create: pass the outfits, and auto-size when no count was given ──────
rep('''class DatasetIn(BaseModel):
    name: str
    char_slug: str
    trigger: str = ""
    class_token: str = "man"
    target: str = "krea2"            # krea2 | flux | sdxl — affects the README/config
    count: int = 40''',
    '''class DatasetIn(BaseModel):
    name: str
    char_slug: str
    trigger: str = ""
    class_token: str = "man"
    target: str = "krea2"            # krea2 | flux | sdxl — affects the README/config
    # None = size it from the wardrobe (v1.219). An explicit number always wins;
    # the pre-v1.219 UI always sent one, so its behaviour is unchanged.
    count: Optional[int] = None''',
    "DatasetIn: optional count")

rep('''        "options": {**(body.options or {}), "preset": body.preset},
        "preset": body.preset, "created_at": _now(),
        "items": _build_plan(body.count, {**(body.options or {}), "preset": body.preset}),
    }
    _write_ds(ds)
    logger.info("lora dataset created: %s (%d planned images)", ds_id, len(ds["items"]))
    return _public(ds)''',
    '''        "options": {**(body.options or {}), "preset": body.preset},
        "preset": body.preset, "created_at": _now(),
    }
    outfits = ds["outfits"]
    count = body.count if body.count is not None else (
        _suggested_count(len(outfits)) if outfits else 40)
    count = max(8, min(int(count), 120))
    ds["count"] = count
    # v1.219: `outfits` NEVER reached the planner — the whole wardrobe feature
    # was inert through this route.  _plan_opts is the single builder now.
    ds["items"] = _build_plan(count, _plan_opts(ds))
    _write_ds(ds)
    logger.info("lora dataset created: %s (%d planned images, %d outfits)",
                ds_id, len(ds["items"]), len(outfits))
    out = _public(ds)
    out["warnings"] = _plan_warnings(count, outfits)
    return out''',
    "create: wire outfits + auto-size")

# ── 3. re-plan: same wiring, same auto-size ─────────────────────────────────
rep('''    count = body.count if body.count is not None else len(ds.get("items", []))
    old = {it["id"]: it for it in ds.get("items", [])}
    fresh = _build_plan(count, ds.get("options") or {})''',
    '''    if body.count is not None:
        count = int(body.count)
    elif body.outfits is not None and ds.get("outfits"):
        # the wardrobe just changed and no count was given — size it to fit
        count = _suggested_count(len(ds["outfits"]))
    else:
        count = len(ds.get("items", [])) or 40
    count = max(8, min(count, 120))
    ds["count"] = count
    old = {it["id"]: it for it in ds.get("items", [])}
    fresh = _build_plan(count, _plan_opts(ds))''',
    "replan: wire outfits + auto-size")

rep('''    ds["items"] = fresh
    _write_ds(ds)
    return _public(ds)''',
    '''    ds["items"] = fresh
    _write_ds(ds)
    out = _public(ds)
    out["warnings"] = _plan_warnings(count, ds.get("outfits") or [])
    return out''',
    "replan: surface warnings")

# ── 4. "ArcFace found no face" is not "ArcFace never ran" ───────────────────
rep('''                flags["identity_method"] = ("arcface" if arc is not None
                                            else ("vision-llm" if ref_png else "none"))''',
    '''                # v1.219: keyed on whether ArcFace RAN, not on whether it found
                # a face.  The old form made `arcface` imply a score, so the
                # no-face counter in _flag_summary was unreachable by
                # construction — a back shot and a missing model looked alike.
                flags["identity_method"] = ("arcface" if baselines
                                            else ("vision-llm" if ref_png else "none"))''',
    "qc: identity_method")

rep('''            q["identity_method"] = "arcface" if s is not None else "none"''',
    '''            q["identity_method"] = "arcface"      # it ran; s is None only if no face''',
    "likeness route: identity_method")

assert src != orig
P.write_text(src, "utf-8")
print(f"patched {P}  ({len(orig)} -> {len(src)} bytes)")
