"""v1.248 — an export can refuse images that are not him enough.

MEASURED, and this is the finding that matters most today.

`redv1`, 20 images, after the v1.247 profile fix:

    front baselines   n=16  scored 14  median 0.5102  min 0.1905  6 below match
    left              n=2              median 0.5090  min 0.2312  1 below
    right             n=2              median 0.4687  min 0.4598  0 below

    bands: 11 match · 3 borderline · 4 NOT HIM · 2 no face

**Four images of twenty score 0.19-0.23 — below ArcFace's different-person
floor.** dorian-v1, by comparison, has a minimum of 0.4068 across 36 scored
faces and nothing under the floor at all.

Those four are already excluded from an export, because `same_person` gates `ok`
and the export skips flagged rows. The pipeline caught them. But two of them —
0012 and 0019 — were rows the ANGLE repair loop had just re-rendered, which
means the repair fixed the angle and broke the face, and nothing noticed until a
separate likeness run went looking. That is fixed in `scripts\\repair.ps1`, which
re-measures every property each round.

The v1.247 profile fix, measured on dorian:

    profile_left   0.33/0.40/0.42  ->  median 0.5231, min 0.4313
    profile_right  0.33            ->  median 0.6223, min 0.4910, none below match

Four rows under the match line became two, both marginal, and the right side
cleared entirely. Geometry, exactly as predicted, and now measured rather than
argued.

WHAT SHIPS HERE
    `ExportIn.min_likeness` — an optional floor. Default None, which keeps
    today's behaviour: the `ok` gate already excludes anything under 0.25.

    It is optional ON PURPOSE. A blanket floor at `ARC_MATCH` would drop
    dorian's profile rows at 0.4313 and 0.4410, which the v1.247 measurement
    says are fine — they are profiles, scored against one profile reference, and
    a single-reference baseline carries that photograph's framing bias. Picking
    one number for every view would trade four bad images for two good ones.

    So the export REPORTS instead of deciding: every response now carries the
    likeness distribution of what it shipped and what a floor would have cost,
    and `excluded` says why each skipped image was skipped rather than leaving a
    silent gap between "40 rendered" and "36 exported".
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


rep('''class ExportIn(BaseModel):
    trigger_mode: str = "literal"    # literal | placeholder ([trigger] for ai-toolkit)
    include_flagged: bool = False    # ship images QC flagged as bad
    resolution: Optional[List[int]] = None''',
    '''class ExportIn(BaseModel):
    trigger_mode: str = "literal"    # literal | placeholder ([trigger] for ai-toolkit)
    include_flagged: bool = False    # ship images QC flagged as bad
    resolution: Optional[List[int]] = None
    # v1.248: an ArcFace floor, off by default. `ok` already excludes anything
    # under ARC_DIFFERENT (0.25); this is for asking a stricter question.
    # Deliberately NOT defaulted to ARC_MATCH — measured, dorian's profile rows
    # sit at 0.4313 and 0.4410 and are fine, so one number for every view would
    # trade good images for bad ones.
    min_likeness: Optional[float] = None''',
    "export input")

rep('''    picked = []
    for it in ds["items"]:
        fp = _item_path(ds_id, it["id"])
        if not fp.exists() or it.get("keep") is False:
            continue
        if not body.include_flagged and (it.get("qc") or {}).get("ok") is False:
            continue
        picked.append((it, fp))
    if not picked:
        raise HTTPException(409, "no images to export — render some first "
                                 "(or tick include-flagged)")''',
    '''    picked = []
    # v1.248: WHY each image was left out, so the gap between "40 rendered" and
    # "36 exported" is never something you have to work out for yourself.
    excluded: List[Dict[str, Any]] = []
    floor = body.min_likeness
    for it in ds["items"]:
        fp = _item_path(ds_id, it["id"])
        if not fp.exists():
            excluded.append({"id": it["id"], "why": "never rendered"})
            continue
        if it.get("keep") is False:
            excluded.append({"id": it["id"], "why": "marked not-kept by hand"})
            continue
        q = it.get("qc") or {}
        if not body.include_flagged and q.get("ok") is False:
            excluded.append({"id": it["id"], "why": "QC flagged",
                             "issues": (q.get("issues") or [])[:2],
                             "identity_score": q.get("identity_score")})
            continue
        s = q.get("identity_score")
        if floor is not None and isinstance(s, (int, float)) and s < float(floor):
            excluded.append({"id": it["id"],
                             "why": f"likeness {s:.3f} below the {float(floor):.2f} floor",
                             "identity_score": s,
                             "identity_baseline": q.get("identity_baseline")})
            continue
        picked.append((it, fp))
    if not picked:
        raise HTTPException(409, "no images to export — render some first "
                                 "(or tick include-flagged, or lower min_likeness)")''',
    "export selection")

rep('''    logger.info("lora export[%s]: %s (%d images)", ds_id, zip_name, len(picked))''',
    '''    # What actually went in, and what a stricter floor would have cost. Reported
    # rather than decided: a blanket floor at ARC_MATCH drops dorian's profile
    # rows at 0.4313 and 0.4410, which are fine.
    _shipped = [(it.get("qc") or {}).get("identity_score") for it, _ in picked]
    _sv = sorted(x for x in _shipped if isinstance(x, (int, float)))
    likeness = {
        "shipped": len(picked),
        "scored": len(_sv),
        "no_face": sum(1 for x in _shipped if x is None),
        "median": round(_sv[len(_sv) // 2], 4) if _sv else None,
        "min": round(_sv[0], 4) if _sv else None,
        "max": round(_sv[-1], 4) if _sv else None,
        "below_match": sum(1 for x in _sv if x < _like.ARC_MATCH),
        "floor_applied": floor,
        "would_drop": {f"{t:.2f}": sum(1 for x in _sv if x < t)
                       for t in (0.30, 0.40, 0.45, 0.50)},
        "note": ("`min_likeness` is off by default. A blanket floor at "
                 f"{_like.ARC_MATCH} drops correctly-rendered PROFILE rows, which "
                 "score lower against a single profile reference. Read "
                 "`would_drop` against `by_baseline` from /likeness before "
                 "setting one."),
    }
    logger.info("lora export[%s]: %s (%d images, %d excluded)",
                ds_id, zip_name, len(picked), len(excluded))''',
    "export stats")

rep('''    return {"file": zip_name, "images": len(picked),
            "url": f"/api/lora/datasets/{ds_id}/exports/{zip_name}",
            "skipped_flagged": sum(1 for it in ds["items"]
                                   if (it.get("qc") or {}).get("ok") is False)
            if not body.include_flagged else 0}''',
    '''    return {"file": zip_name, "images": len(picked),
            "url": f"/api/lora/datasets/{ds_id}/exports/{zip_name}",
            "likeness": likeness,
            "excluded": excluded[:60],
            "excluded_total": len(excluded),
            "skipped_flagged": sum(1 for it in ds["items"]
                                   if (it.get("qc") or {}).get("ok") is False)
            if not body.include_flagged else 0}''',
    "export response")

assert src != orig
P.write_text(src, "utf-8")
print(f"patched {P}  ({len(orig)} -> {len(src)} bytes)")
