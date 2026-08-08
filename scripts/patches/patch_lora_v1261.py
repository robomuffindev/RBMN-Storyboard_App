"""v1.261 — the wardrobe check.  BACKEND ONLY (new route + export gate).

v1.260 stopped a dataset from RENDERING off a stripped base and said plainly
that the detection hole was still open. It was worse than one row.

MEASURED, on all 40 rendered images of dorian-v1, described TWICE by
qwen2.5vl:7b (scripts\\caption_probe.py):

    self-agreement (Jaccard, content words):  median 0.786   3 of 40 below 0.40
    rows reporting bare skin:                 12 of 40
    confirmed by eye:                          8 of 8   (0010 0013 0014 0016
                                                          0017 0020 0023 0028)

**Twelve of the forty images in the set that trained the shipped LoRA are of a
man in his underwear, and not one caption mentions it.** The trained weights
therefore carry "shirtless in grey boxer briefs" as part of what the trigger
word means. That is a defect in the model, not just in the folder.

WHY THE VISION MODEL IS TRUSTED HERE AND NOT FOR FRAMING
    v1.241 threw out this model's framing answer at 0-for-12. Framing is a
    geometric judgement about the edges of the picture. Naming visible clothing
    is a description task, and it was measured before it was believed — the
    numbers above. Same standard, different instrument, opposite result.

WHAT SHIPS
    backend/services/wardrobe.py   the vocabulary and the verdict, pure text
    POST /datasets/{id}/wardrobe-check   two vision passes per image, stores
                                         `seen_clothing` and a bare verdict, and
                                         FLAGS a bare row so the existing repair
                                         loop re-renders it (dressed, per v1.260)
    _flag_summary                  counts bare / dressed / unmeasured, and says
                                   "wardrobe" is not checked until it has been
    export                         refuses to ship a bare row unless asked
    captions                       reuse the stored description instead of
                                   making a second vision call

AND THE EPOCH COUNT
    v1.259 measured likeness per epoch: it plateaus at epoch 21 of 40 on a
    40-image set — about 840 image-steps — and the last eight epochs span 0.028.
    The heuristic was `n * 1.2` capped at 40, which asked for 40 epochs on a
    40-image set: roughly three hours of GPU past the point where the number
    stopped moving. It now targets ~900 image-steps. On 40 images that is 23
    epochs. On a 20-image set it still asks for 40, because 20 images is a set
    size nobody has measured yet and undertraining is not the cheaper mistake.
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


rep('''from backend.services import subject as _subj''',
    '''from backend.services import subject as _subj
from backend.services import wardrobe as _ward''',
    "import")

# ── the counters ─────────────────────────────────────────────────────────────
rep('''           "not_one_person": 0, "face_unclear": 0, "identity_off": 0,''',
    '''           "not_one_person": 0, "face_unclear": 0, "identity_off": 0,
           "bare_skin": 0, "wardrobe_measured": 0, "wardrobe_unmeasured": 0,''',
    "counters")

rep('''        if q.get("outfit_ok") is False:
            out["outfit_off"] += 1''',
    '''        if q.get("outfit_ok") is False:
            out["outfit_off"] += 1
        # v1.261. Distinct from `outfit_off`, which is the vision model judging a
        # garment against the plan and is not trusted. This is the narrower
        # question — is he wearing anything at all — measured twice per image.
        if q.get("bare") is True:
            out["bare_skin"] += 1
        if str(q.get("wardrobe_method") or "").startswith("vision-"):
            out["wardrobe_measured"] += 1
        elif q.get("wardrobe_method") == "unmeasured" or "bare" in q:
            out["wardrobe_unmeasured"] += 1''',
    "bare counters")

rep('''    out["not_checked"] = [] if out["crop_measured"] else ["crop"]
    out["unreliable"] = ["expression"]''',
    '''    out["not_checked"] = [] if out["crop_measured"] else ["crop"]
    # v1.261: a set nobody has run the wardrobe check on has not passed it.
    if not out["wardrobe_measured"]:
        out["not_checked"].append("wardrobe")
    out["unreliable"] = ["expression"]
    if out["bare_skin"]:
        out.setdefault("warnings", []).append(
            f"{out['bare_skin']} image(s) show the subject undressed or in underwear. "
            f"A LoRA learns whatever is in the pixels: train on these and the trigger "
            f"word carries bare skin with it. Re-render them (repair now picks them "
            f"up) or exclude them.")''',
    "not_checked wardrobe")

# ── the route ────────────────────────────────────────────────────────────────
rep('''@router.post("/datasets/{ds_id}/caption")''',
    '''@router.post("/datasets/{ds_id}/wardrobe-check")
async def dataset_wardrobe_check(ds_id: str, body: ItemsIn,
                                 session: AsyncSession = Depends(get_session)):
    """Look at every rendered image and answer one question: is he dressed?

    v1.261. Two passes per image at the same low temperature, and a row counts
    as bare if EITHER pass says so — a false positive costs one re-render, a
    false negative costs a training run.

    A bare row is marked `qc.ok = False` with a plain-English issue, which puts
    it in front of the repair loop that already exists. With v1.260 resolving
    datasets to the dressed base, repairing it actually fixes it.

    The description is kept on the row as `seen_clothing`, so the caption pass
    can use it without paying for a second look."""
    ds = _read_ds(ds_id)
    sel = set(body.item_ids or [])
    from backend.api.vnccs_native import _ollama_cfg
    from backend.services.character_studio.vnccs_native import wizards as _wiz
    urls, _t, vision_model = await _ollama_cfg(session)
    if not urls or not vision_model:
        raise HTTPException(503, "Ollama vision model is not configured "
                                 "(Settings -> Ollama vision model).")
    import asyncio
    checked = bare = failed = 0
    rows: List[Dict[str, Any]] = []
    for it in ds["items"]:
        if sel and it["id"] not in sel:
            continue
        fp = _item_path(ds_id, it["id"])
        if not fp.exists():
            continue
        blob = fp.read_bytes()
        passes = []
        for _ in range(2):
            try:
                out = await asyncio.to_thread(
                    _wiz.ollama_chat_sync, urls, vision_model, _ENRICH_SYSTEM,
                    _ENRICH_PROMPT, [_wiz.image_bytes_to_b64(blob)], 0.2, 120.0, False)
            except Exception as e:  # noqa: BLE001
                logger.warning("wardrobe[%s/%s] failed: %s", ds_id, it["id"], e)
                out = None
            if out and out.strip():
                passes.append(out.strip().strip('"').split("\\n")[0][:220])
        v = _ward.verdict(passes)
        q = it.setdefault("qc", {})
        q["bare"] = v["bare"]
        q["bare_words"] = v.get("words") or []
        q["wardrobe_method"] = v["method"]
        q["wardrobe_why"] = v["why"]
        if passes:
            it["seen_clothing"] = passes[0]
            checked += 1
        else:
            failed += 1
        if v["bare"] is True:
            bare += 1
            q["ok"] = False
            issue = f"he is undressed — {v['why']}"
            q["issues"] = [i for i in (q.get("issues") or []) if "undressed" not in i]
            q["issues"].insert(0, issue)
        else:
            # A row that was flagged ONLY for being undressed and now is not
            # must stop being flagged, or the repair loop chases it forever.
            olds = [i for i in (q.get("issues") or []) if "undressed" in i]
            if olds:
                q["issues"] = [i for i in q["issues"] if "undressed" not in i]
                if not q["issues"] and q.get("ok") is False:
                    q["ok"] = True
        rows.append({"id": it["id"], "framing": it.get("framing"),
                     "angle": it.get("angle"), **v,
                     "seen": passes[0] if passes else None})
    _write_ds(ds)
    logger.info("lora wardrobe[%s]: %d checked, %d bare, %d unreadable",
                ds_id, checked, bare, failed)
    return {"checked": checked, "bare": bare, "unreadable": failed,
            "summary": _ward.summarise([r for r in rows]),
            "rows": rows}


@router.post("/datasets/{ds_id}/caption")''',
    "wardrobe route")

# ── captions reuse what the wardrobe check already saw ───────────────────────
rep('''            if out and out.strip():
                it["caption_extra"] = out.strip().strip('"').split("\\n")[0][:200]
                enriched += 1''',
    '''            if out and out.strip():
                it["caption_extra"] = out.strip().strip('"').split("\\n")[0][:200]
                it["seen_clothing"] = it["caption_extra"]
                enriched += 1''',
    "enrich stores seen")

rep('''    for it in targets:
        it["caption"] = _caption(ds, it)
    _write_ds(ds)
    return {"captioned": len(targets), "enriched": enriched}''',
    '''    # v1.261: the wardrobe check already paid for a description of every image.
    # Reuse it rather than asking the model the same question twice — and it
    # means a caption says "grey boxer briefs" on the rows where that is the
    # truth, instead of silently omitting the most obvious thing in the frame.
    reused = 0
    for it in targets:
        if not (it.get("caption_extra") or "").strip() and (it.get("seen_clothing") or "").strip():
            it["caption_extra"] = it["seen_clothing"][:200]
            reused += 1
    for it in targets:
        it["caption"] = _caption(ds, it)
    _write_ds(ds)
    return {"captioned": len(targets), "enriched": enriched, "reused_seen": reused}''',
    "caption reuse")

# ── export will not ship a bare row by accident ──────────────────────────────
rep('''    include_flagged: bool = False    # ship images QC flagged as bad''',
    '''    include_flagged: bool = False    # ship images QC flagged as bad
    include_bare: bool = False       # v1.261: ship images where he is undressed''',
    "export flag")

rep('''        s = q.get("identity_score")
        if floor is not None and isinstance(s, (int, float)) and s < float(floor):''',
    '''        # v1.261: a bare row leaves the set even when include_flagged is on.
        # `include_flagged` means "ship the near misses"; it should never have
        # silently meant "ship the nudes".
        if q.get("bare") is True and not body.include_bare:
            excluded.append({"id": it["id"], "why": "subject is undressed",
                             "bare_words": q.get("bare_words") or []})
            continue
        s = q.get("identity_score")
        if floor is not None and isinstance(s, (int, float)) and s < float(floor):''',
    "export gate")

# ── epochs ───────────────────────────────────────────────────────────────────
rep('''def _fizgig_commands(ds: dict, n: int, mp: float = 1.05, rank: int = 16) -> str:''',
    '''def _epochs_for(n: int) -> int:
    """How long to train, from the one run that was actually measured.

    v1.259 scored every epoch's preview with ArcFace on a 40-image set:
    likeness climbs to about 0.74 by epoch 21 and the last eight epochs span
    0.028. Epoch 21 x 40 images is roughly 840 image-steps, so ~900 is the
    target and the old `n * 1.2` (which asked for 40 epochs on 40 images) was
    about three hours of GPU past the point where the number stopped moving.

    The floor stays high and the cap stays at 40 on purpose: 900 steps is
    measured on ONE set size, and undertraining is not the cheaper mistake. On a
    20-image set this still asks for 40 epochs, because no one has measured a
    20-image set. Every epoch is saved, so `scripts\\\\checkpoint_score.py` can end
    a run early on evidence rather than on this arithmetic."""
    return max(15, min(round(900 / max(1, n)), 40))


def _fizgig_commands(ds: dict, n: int, mp: float = 1.05, rank: int = 16) -> str:''',
    "epochs fn")

rep('''    trig = ds.get("trigger", "sks")
    epochs = max(10, min(round(n * 1.2), 40))''',
    '''    trig = ds.get("trigger", "sks")
    epochs = _epochs_for(n)''',
    "epochs commands")

rep('''    epochs = max(10, min(round(n * 1.2), 40))
    return (_RUNNER_SRC''',
    '''    epochs = _epochs_for(n)
    return (_RUNNER_SRC''',
    "epochs runner")

assert src != orig
P.write_text(src, "utf-8")
print(f"patched {P}  ({len(orig)} -> {len(src)} bytes)")
