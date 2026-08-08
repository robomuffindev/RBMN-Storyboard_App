"""v1.222 — a re-plan must never silently destroy rendered images.

WHAT HAPPENED
    `ab_tq_base.ps1` set `options.tq_base` by POSTing the dataset's existing
    options back with one key added.  `dataset_plan` REPLACED `ds["options"]`
    wholesale, the `preset` did not survive the round-trip, and `_plan_opts`
    fell back to "balanced".  His dataset was `face_heavy`.

    face_heavy vs balanced at 40 images share **7 of 40 slots** — which is
    exactly the "images still rendered after re-plan: 7 of 40" he saw.  33
    rendered images were deleted, and the A/B measured the wrong rows.

    The script was the trigger.  The route was the loaded gun: a caller that
    omits one key silently changes the shot list and the route deletes every
    image whose slot moved, with no warning and no way to say no.

THREE FIXES
    1. `options` MERGES instead of replacing.  Omitting a key now means "leave
       it alone", which is what every caller already assumed.
    2. `preset` is sticky.  It lives both at `ds["preset"]` and inside options;
       whichever the caller supplies wins, and if neither does, the stored one
       survives.  It can no longer default to "balanced" behind your back.
    3. A re-plan that would DISCARD rendered images now refuses.  It returns 409
       with the count, what changed, and `force: true` to proceed.  Deleting GPU
       time should require saying so out loud.
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


# ── 1. a dry-run of what a re-plan would cost ──────────────────────────────
rep('''def _plan_warnings(count: int, outfits: List[dict]) -> List[str]:''',
    '''def _plan_impact(ds: dict, fresh: List[dict]) -> dict:
    """What a re-plan would DESTROY, computed before anything is written.

    v1.222: this did not exist, and the route deleted every image whose slot
    moved without saying so.  One omitted option key silently re-cut the whole
    shot list and 33 rendered images went with it."""
    KEYS = ("framing", "angle", "expression", "pose", "lighting", "background")
    old = {it["id"]: it for it in ds.get("items", [])}
    kept, lost = [], []
    for it in fresh:
        prev = old.get(it["id"])
        if prev and prev.get("status") == "done":
            (kept if all(prev.get(k) == it.get(k) for k in KEYS) else lost).append(it["id"])
    gone = [i for i, p in old.items()
            if p.get("status") == "done" and i not in {f["id"] for f in fresh}]
    changed = sorted({f"{old[i].get('angle')} -> {n.get('angle')}"
                      for i in lost for n in fresh if n["id"] == i
                      and old[i].get("angle") != n.get("angle")})
    return {"rendered_before": sum(1 for p in old.values() if p.get("status") == "done"),
            "kept": len(kept), "discarded": len(lost) + len(gone),
            "discarded_ids": sorted(lost + gone)[:40],
            "angle_changes": changed[:10]}


def _plan_warnings(count: int, outfits: List[dict]) -> List[str]:''',
    "plan impact")

# ── 2. merge options, keep the preset, and refuse a destructive re-plan ────
rep('''    if body.options is not None:
        ds["options"] = body.options''',
    '''    if body.options is not None:
        # v1.222: MERGE.  Replacing meant a caller that omitted `preset` silently
        # re-cut the entire shot list and the route then deleted every image
        # whose slot moved.  Omitting a key now means "leave it alone".
        ds["options"] = {**(ds.get("options") or {}), **(body.options or {})}
    # `preset` lives in two places; whichever the caller gave wins, and if
    # neither did, the STORED one survives rather than defaulting to balanced.
    _p = (body.options or {}).get("preset") if body.options else None
    if _p:
        ds["preset"] = _p
    elif ds.get("preset"):
        ds.setdefault("options", {})["preset"] = ds["preset"]''',
    "plan: merge options + sticky preset")

rep('''    old = {it["id"]: it for it in ds.get("items", [])}
    fresh = _build_plan(count, _plan_opts(ds))''',
    '''    old = {it["id"]: it for it in ds.get("items", [])}
    fresh = _build_plan(count, _plan_opts(ds))
    impact = _plan_impact(ds, fresh)
    if impact["discarded"] and not body.force:
        raise HTTPException(409, "this re-plan would DISCARD "
                                 f"{impact['discarded']} rendered image(s) "
                                 f"(keeping {impact['kept']} of "
                                 f"{impact['rendered_before']}). "
                                 + (f"Angles changing: {'; '.join(impact['angle_changes'])}. "
                                    if impact["angle_changes"] else "")
                                 + "That is real GPU time. Re-send with force=true if you "
                                   "mean it, or check that `preset` and `options` match what "
                                   "the dataset already had.")''',
    "plan: refuse to destroy without force")

rep('''class PlanIn(BaseModel):
    count: Optional[int] = None
    outfit: Optional[str] = None
    outfits: Optional[List[dict]] = None
    options: Optional[dict] = None''',
    '''class PlanIn(BaseModel):
    count: Optional[int] = None
    outfit: Optional[str] = None
    outfits: Optional[List[dict]] = None
    options: Optional[dict] = None
    force: bool = False              # required to discard rendered images''',
    "PlanIn: force")

rep('''    out = _public(ds)
    out["warnings"] = _plan_warnings(count, ds.get("outfits") or [])
    return out''',
    '''    out = _public(ds)
    out["warnings"] = _plan_warnings(count, ds.get("outfits") or [])
    out["impact"] = impact
    return out''',
    "plan: report the impact")

# ── 3. a read-only preview, so nothing has to be risked to find out ───────
rep('''@router.post("/datasets/{ds_id}/likeness")''',
    '''@router.post("/datasets/{ds_id}/plan-preview")
async def dataset_plan_preview(ds_id: str, body: PlanIn):
    """What a re-plan WOULD do.  Writes nothing, deletes nothing.

    v1.222: the only way to find out used to be to do it."""
    ds = _read_ds(ds_id)
    probe = json.loads(json.dumps(ds))
    if body.outfits is not None:
        probe["outfits"] = _norm_outfits({"outfits": body.outfits})
    if body.options is not None:
        probe["options"] = {**(probe.get("options") or {}), **(body.options or {})}
    _p = (body.options or {}).get("preset") if body.options else None
    if _p:
        probe["preset"] = _p
    count = int(body.count) if body.count is not None else (len(ds.get("items", [])) or 40)
    count = max(8, min(count, 120))
    fresh = _build_plan(count, _plan_opts(probe))
    return {"count": count, "preset": probe.get("preset"),
            "options": _plan_opts(probe),
            "impact": _plan_impact(ds, fresh),
            "warnings": _plan_warnings(count, probe.get("outfits") or [])}


@router.post("/datasets/{ds_id}/likeness")''',
    "route: plan preview")

assert src != orig
P.write_text(src, "utf-8")
print(f"patched {P}  ({len(orig)} -> {len(src)} bytes)")
