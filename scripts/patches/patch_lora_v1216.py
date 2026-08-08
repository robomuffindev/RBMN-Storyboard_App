"""v1.216 — outfit SETS.

Our own module docstring has warned since v1.209 that a narrow dataset bakes its
narrowness in ("all-bikini dataset -> every render is a bikini") — while the code
shipped ONE `outfit` string for the whole set, which is exactly that failure.
This makes outfits a set: named (his story wardrobe) + variety (what keeps
clothing detachable from identity), dealt in strict rotation so no outfit owns a
framing, named only where the shot can show them, and optionally driven by a
garment REFERENCE IMAGE — Klein already loads up to 5 refs and REF_TAGS already
has 'outfit'.
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


# ── 0. `re` is new to this module (garment word-boundary matching) ──────────
rep("""import json
import logging
import random""",
    """import json
import logging
import random
import re""",
    "import re")

# ── 1. the outfit machinery, before the planner that uses it ────────────────
BLOCK = (Path(__file__).resolve().parent / "v1216_block.py").read_text("utf-8")
ANCHOR = "\n\ndef _build_plan("
assert src.count(ANCHOR) == 1
src = src.replace(ANCHOR, "\n" + BLOCK + "\ndef _build_plan(", 1)

# ── 2. the planner deals outfits like it deals angles ───────────────────────
rep('''    plan: List[dict] = []
    for i, fr in enumerate(rows):''',
    '''    # Outfits are dealt across the whole shot list, NOT in blocks: `rows` is
    # grouped by framing, so a block deal would hand one outfit every full-body
    # and another every close-up, and the LoRA would learn the pairing.
    outfits = _norm_outfits({"outfits": opts.get("outfits")})
    o_seq = _deal_outfits([(fr[0], seq[i][0]) for i, fr in enumerate(rows)],
                          outfits)

    plan: List[dict] = []
    for i, fr in enumerate(rows):''',
    "planner: deal outfits")

rep('''            "status": "planned", "caption": "", "qc": None,
            "width": fr[4][0], "height": fr[4][1],
        })''',
    '''            "outfit": o_seq[i] if i < len(o_seq) else None,
            "status": "planned", "caption": "", "qc": None,
            "width": fr[4][0], "height": fr[4][1],
        })''',
    "planner: record the outfit on the row")

# ── 3. the render prompt names the outfit, and points at its reference ──────
rep('''def _render_prompt(ds: dict, item: dict) -> str:''',
    '''def _render_prompt(ds: dict, item: dict, garment_idx: Optional[int] = None) -> str:''',
    "prompt: accept a garment reference index")

rep('''    if (ds.get("outfit") or "").strip():
        bits.append(f"He is wearing {ds['outfit'].strip()}.")
    bits.append(_QUALITY + ".")''',
    '''    worn = _outfit_text(ds, item)
    if worn:
        # Naming the image index is the half that makes a garment reference work.
        # Klein ignores category words ("the clothing in image 3"), so the
        # garments are NAMED and the image is cited as corroboration, never as a
        # substitute for naming them.
        bits.append(f"He is wearing {worn}, the exact garments shown in image "
                    f"{garment_idx}." if garment_idx else f"He is wearing {worn}.")
    bits.append(_QUALITY + ".")''',
    "prompt: outfit clause")

# ── 4. the caption names it only where the shot can show it ─────────────────
rep('''    if (ds.get("outfit") or "").strip():
        parts.append(f"wearing {ds['outfit'].strip()}")''',
    '''    # An extreme close-up of a face shows no clothing; naming an outfit there
    # teaches a false association, exactly like an expression on a back shot.
    worn = _outfit_text(ds, item)
    if worn:
        parts.append(f"wearing {worn}")''',
    "caption: outfit clause")

# ── 5. the garment reference goes in as an extra Klein ref ──────────────────
rep('''        refs = [str(base)]
        face = _refs_by_tag(char, "face")
        if it["framing"] in ("face", "headshot") and face:
            fp = _cdir(ds["char_slug"]) / "refs" / f"{face[-1]['id']}.png"
            if fp.exists():
                refs.append(str(fp))     # close-ups get the face reference too
        jobs.append({"key": it["id"], "prompt": _render_prompt(ds, it), "refs": refs,''',
    '''        refs = [str(base)]
        face = _refs_by_tag(char, "face")
        if it["framing"] in ("face", "headshot") and face:
            fp = _cdir(ds["char_slug"]) / "refs" / f"{face[-1]['id']}.png"
            if fp.exists():
                refs.append(str(fp))     # close-ups get the face reference too
        # A garment reference only earns its slot when the shot can show the
        # clothes — on a face crop it would just compete with the identity refs.
        # (_run_klein_edit_on loads up to 5 and picks the matching NREF workflow.)
        g_idx = None
        o = _outfit_for(ds, it)
        if o and o.get("ref_id") and _OUTFIT_VIS.get(it["framing"], "full") != "none":
            gp = _cdir(ds["char_slug"]) / "refs" / f"{o['ref_id']}.png"
            if gp.exists() and len(refs) < 5:
                refs.append(str(gp))
                g_idx = len(refs)        # 1-based: the prompt says "image N"
        jobs.append({"key": it["id"], "prompt": _render_prompt(ds, it, g_idx), "refs": refs,''',
    "jobs: garment reference")

# ── 6. QC checks the outfit when it is visible ──────────────────────────────
rep('''def _qc_prompt(item: dict, with_identity: bool = False) -> str:
    fr = _by_key(FRAMINGS, item["framing"])
    ang = _by_key(ANGLES, item["angle"])''',
    '''def _qc_prompt(item: dict, with_identity: bool = False, outfit: str = "") -> str:
    fr = _by_key(FRAMINGS, item["framing"])
    ang = _by_key(ANGLES, item["angle"])
    # Only asked about when the shot can show it — otherwise every face crop
    # would be flagged for an outfit it was never meant to contain.
    o_line = (f'\\nHe was also supposed to be wearing {outfit}. Add a key '
              f'"outfit_ok": true/false — false only if he is clearly wearing '
              f'something else.' if outfit else "")''',
    "qc: accept the outfit")

rep('''  "cropped_badly": true/false, "issues": ["short phrase", ...]}}

Set "angle_ok" false only if the person is facing the camera rather than away from it.''',
    '''  "cropped_badly": true/false, "issues": ["short phrase", ...]}}{o_line}

Set "angle_ok" false only if the person is facing the camera rather than away from it.''',
    "qc: back-shot outfit line")

rep('''off by the frame edge. List every problem you see in "issues"."""''',
    '''off by the frame edge. List every problem you see in "issues".{o_line}"""''',
    "qc: normal-shot outfit line")

rep('''                    _qc_prompt(item, with_identity=bool(ref_png)), imgs, 0.1, 180.0, True)''',
    '''                    _qc_prompt(item, with_identity=bool(ref_png),
                               outfit=_outfit_text(cur, item)), imgs, 0.1, 180.0, True)''',
    "qc: pass the shot's outfit")

rep('''                flags = {k: bool(data.get(k)) for k in
                         ("framing_ok", "angle_ok", "expression_ok", "one_person",
                          "face_clear", "artifacts", "cropped_badly")}''',
    '''                flags = {k: bool(data.get(k)) for k in
                         ("framing_ok", "angle_ok", "expression_ok", "one_person",
                          "face_clear", "artifacts", "cropped_badly")}
                # default TRUE: a model that omitted the key must not fail the
                # image, and a shot with no visible outfit was never asked.
                flags["outfit_ok"] = bool(data.get("outfit_ok", True))''',
    "qc: record outfit_ok")

# ── 7. a wrong outfit fails the image, and the breakdown says so ────────────
rep("""                ok = (flags["framing_ok"] and flags["one_person"]
                      and not flags["artifacts"] and not flags["cropped_badly"]
                      and flags.get("same_person", True))""",
    """                ok = (flags["framing_ok"] and flags["one_person"]
                      and not flags["artifacts"] and not flags["cropped_badly"]
                      and flags.get("same_person", True)
                      and flags.get("outfit_ok", True))""",
    "qc: a wrong outfit is a flag")

rep("""           "not_one_person": 0, "face_unclear": 0, "identity_off": 0,
           "stuck": 0, "top_issues": {}}""",
    """           "not_one_person": 0, "face_unclear": 0, "identity_off": 0,
           "outfit_off": 0, "stuck": 0, "top_issues": {}}""",
    "summary: outfit_off key")

rep("""        if q.get("same_person") is False:
            out["identity_off"] += 1""",
    """        if q.get("same_person") is False:
            out["identity_off"] += 1
        if q.get("outfit_ok") is False:
            out["outfit_off"] += 1""",
    "summary: count outfit misses")

# ── 8. models + routes ──────────────────────────────────────────────────────
rep('''    outfit: str = ""                 # one outfit for the whole set, or blank = as-is
    options: Optional[dict] = None   # subset filters for angles/expressions/…


class PlanIn(BaseModel):
    count: Optional[int] = None
    outfit: Optional[str] = None
    options: Optional[dict] = None''',
    '''    outfit: str = ""                 # legacy single outfit; migrated to outfits[]
    outfits: Optional[List[dict]] = None   # [{name, desc, kind: named|variety, ref_id}]
    options: Optional[dict] = None   # subset filters for angles/expressions/…


class PlanIn(BaseModel):
    count: Optional[int] = None
    outfit: Optional[str] = None
    outfits: Optional[List[dict]] = None
    options: Optional[dict] = None


class OutfitsIn(BaseModel):
    outfits: List[dict] = []
    resize: bool = False             # also re-suggest the set size from the wardrobe


class WardrobeIn(BaseModel):
    count: int = 5''',
    "models: outfits")

rep('''        "target": body.target, "outfit": body.outfit.strip(),''',
    '''        "target": body.target, "outfit": body.outfit.strip(),
        "outfits": _norm_outfits({"outfits": body.outfits, "outfit": body.outfit}),''',
    "create: store the outfit set")

rep('''    if body.outfit is not None:
        ds["outfit"] = body.outfit.strip()''',
    '''    if body.outfit is not None:
        ds["outfit"] = body.outfit.strip()
    if body.outfits is not None:
        ds["outfits"] = _norm_outfits({"outfits": body.outfits})''',
    "plan: accept an outfit set")

# ── 9. routes: edit the wardrobe, propose one, name a garment image ─────────
ROUTES = '''

@router.get("/datasets/{ds_id}/outfits")
async def outfits_get(ds_id: str):
    """The wardrobe, plus how the image budget would be split across it."""
    ds = _read_ds(ds_id)
    outs = _norm_outfits(ds)
    n = len(ds.get("items") or []) or _suggested_count(len(outs))
    return {"outfits": outs,
            "named": sum(1 for o in outs if o["kind"] == "named"),
            "variety": sum(1 for o in outs if o["kind"] == "variety"),
            "named_share": NAMED_SHARE,
            "suggested_count": _suggested_count(len(outs)),
            "images_per_outfit": IMAGES_PER_OUTFIT,
            "split": dict(zip([o["id"] for o in outs], _outfit_counts(n, outs))),
            "visibility": _OUTFIT_VIS}


@router.put("/datasets/{ds_id}/outfits")
async def outfits_put(ds_id: str, body: OutfitsIn):
    """Replace the wardrobe.  Does NOT re-plan on its own — changing outfits
    after images exist would silently invalidate them, so the caller re-plans
    deliberately."""
    ds = _read_ds(ds_id)
    ds["outfits"] = _norm_outfits({"outfits": body.outfits})
    _write_ds(ds)
    rendered = sum(1 for it in ds.get("items", []) if it.get("status") == "done")
    return {"outfits": ds["outfits"],
            "suggested_count": _suggested_count(len(ds["outfits"])),
            "note": (f"{rendered} image(s) are already rendered against the old wardrobe — "
                     "re-plan and re-render to apply this") if rendered else ""}


@router.post("/characters/{slug}/wardrobe")
async def wardrobe_suggest(slug: str, body: WardrobeIn,
                           session: AsyncSession = Depends(get_session)):
    """Propose variety outfits from the character's own reference.

    Returned for REVIEW, never applied: the whole value is that he can see the
    garments named before any of them cost a render."""
    char = _load_char(slug)
    fp, _lbl = _base_for_view(slug, char, "front")
    if not fp or not fp.exists():
        raise HTTPException(409, "this character has no front base image yet — strip or tag "
                                 "one in Klein 3.0 first")
    from backend.api.vnccs_native import _ollama_cfg
    from backend.services.character_studio.vnccs_native import wizards as _wiz
    urls, _t, vision_model = await _ollama_cfg(session)
    if not urls or not vision_model:
        raise HTTPException(503, "Ollama vision model is not configured "
                                 "(Settings -> Ollama vision model).")
    import asyncio
    n = max(2, min(int(body.count or 5), 10))
    raw = await asyncio.to_thread(
        _wiz.ollama_chat_sync, urls, vision_model, _WARDROBE_SYSTEM,
        _WARDROBE_PROMPT.format(n=n), [_wiz.image_bytes_to_b64(fp.read_bytes())],
        0.4, 150.0, True)
    out = _parse_wardrobe(raw or "", n)
    if not out["outfits"]:
        raise HTTPException(422, "the vision model returned no usable wardrobe — try again, "
                                 "or add the outfits by hand")
    logger.info("lora wardrobe[%s]: %s -> %d outfits", slug,
                out["character_type"] or "?", len(out["outfits"]))
    return out


@router.post("/characters/{slug}/refs/{ref_id}/garment")
async def garment_describe(slug: str, ref_id: str,
                           session: AsyncSession = Depends(get_session)):
    """Name the garments in a tagged reference image.

    This is not a convenience.  Klein ignores category words, so "the clothing
    in image 3" produces whatever it likes — a garment reference only works when
    the prompt NAMES what is in it, and this is what produces that name."""
    fp = _cdir(slug) / "refs" / f"{ref_id}.png"
    if not fp.exists():
        raise HTTPException(404, "no such reference image")
    from backend.api.vnccs_native import _ollama_cfg
    from backend.services.character_studio.vnccs_native import wizards as _wiz
    urls, _t, vision_model = await _ollama_cfg(session)
    if not urls or not vision_model:
        raise HTTPException(503, "Ollama vision model is not configured "
                                 "(Settings -> Ollama vision model).")
    import asyncio
    raw = await asyncio.to_thread(
        _wiz.ollama_chat_sync, urls, vision_model, _GARMENT_SYSTEM, _GARMENT_PROMPT,
        [_wiz.image_bytes_to_b64(fp.read_bytes())], 0.2, 120.0, False)
    desc = _clean_garment_desc(raw or "")
    if not desc:
        raise HTTPException(422, "the vision model did not name any garments in that image "
                                 "— describe the outfit by hand instead (name each garment "
                                 "and its colour; category words alone are ignored)")
    return {"desc": desc, "ref_id": ref_id}

'''
A2 = '\n\n@router.post("/datasets/{ds_id}/export")'
assert src.count(A2) == 1
src = src.replace(A2, ROUTES + A2.lstrip("\n"), 1)

# ── 10. version ─────────────────────────────────────────────────────────────
rep('"""LoRA Dataset Generator — 🎓 the fifth mode (v1.214.0).',
    '"""LoRA Dataset Generator — 🎓 the fifth mode (v1.216.0).',
    "module version")

assert src != orig
P.write_text(src, "utf-8")
print(f"patched {P}  ({len(orig)} -> {len(src)} bytes)")
