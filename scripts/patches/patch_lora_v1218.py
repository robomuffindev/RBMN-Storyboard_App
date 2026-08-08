"""v1.218 — real ArcFace identity scores, replacing the LLM's guess.

Fixes the v1.213 units bug: `fizgig_look_scores.json` was fed vision-LLM scores
(which cluster 0.85-0.95) through Fizgig's cutoff formula and its 0.25 floor
(which is an ArcFace cosine value), so the fence almost never fired and the file
was close to inert.

The vision model keeps everything it is good at — framing, angle, expression,
artifacts, crop, outfit. It stops being asked to judge identity numerically.
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


# ── 1. import + baseline builder ────────────────────────────────────────────
rep('''from backend.config import settings as cfg''',
    '''from backend.config import settings as cfg
from backend.services import likeness as _like''',
    "import likeness")

rep('''def _identity_ref_png(ds: dict) -> Optional[bytes]:''',
    '''def _likeness_baselines(ds: dict) -> Tuple[List[Any], List[str]]:
    """Up to THREE reference embeddings, and what they were.

    Fizgig averages three on purpose — "one photo\\'s framing bias can\\'t
    dominate the score". A single front base makes every front-framed render
    look more like him than it is.

    Deliberately drawn from the CHARACTER\\'s own references, never from this
    dataset\\'s renders: scoring images against themselves would produce a
    beautiful number that means nothing."""
    embs, labels = [], []
    try:
        char = _load_char(ds["char_slug"])
    except Exception:  # noqa: BLE001
        return [], []
    picks: List[Tuple[Optional[Path], str]] = []
    fp, lbl = _base_for_view(ds["char_slug"], char, "front", ds.get("base_mode"))
    picks.append((fp, lbl))
    for tag in ("face", "left", "right"):
        refs = _refs_by_tag(char, tag)
        if refs:
            picks.append((_cdir(ds["char_slug"]) / "refs" / f"{refs[-1][\'id\']}.png",
                          f"{tag} reference"))
    seen = set()
    for p, lbl in picks:
        if not p or str(p) in seen or not Path(p).exists():
            continue
        seen.add(str(p))
        e = _like.embed(p)
        if e is None:                 # a reference with no detectable face is
            continue                  # useless as a baseline, not an error
        embs.append(e)
        labels.append(lbl)
        if len(embs) >= 3:
            break
    return embs, labels


def _identity_ref_png(ds: dict) -> Optional[bytes]:''',
    "baselines")

# ── 2. QC: ArcFace supplies the number, the LLM keeps everything else ───────
rep('''                if ref_png:
                    flags["same_person"] = bool(data.get("same_person", True))
                    try:                 # 0-1 likeness, for the look-outlier file
                        flags["identity_score"] = max(0.0, min(1.0, float(
                            data.get("identity_score", 1.0 if flags["same_person"] else 0.0))))
                    except (TypeError, ValueError):
                        flags["identity_score"] = 1.0 if flags["same_person"] else 0.0''',
    '''                if ref_png:
                    flags["same_person"] = bool(data.get("same_person", True))
                    try:                 # kept for comparison, NOT for the trainer
                        flags["identity_score_llm"] = max(0.0, min(1.0, float(
                            data.get("identity_score", 1.0 if flags["same_person"] else 0.0))))
                    except (TypeError, ValueError):
                        flags["identity_score_llm"] = 1.0 if flags["same_person"] else 0.0
                # v1.218: the NUMBER comes from ArcFace, never from the LLM.
                # A vision model rating identity 0-1 clusters at 0.85-0.95 and
                # is not on the scale Fizgig's cutoff expects.
                arc = _like.score(_item_path(ds_id, iid), baselines) if baselines else None
                flags["identity_score"] = None if arc is None else round(arc, 4)
                flags["identity_method"] = ("arcface" if arc is not None
                                            else ("vision-llm" if ref_png else "none"))
                if arc is not None:
                    flags["identity_verdict"] = _like.verdict(arc)[0]
                    # Only the different-person floor FAILS an image. "Borderline"
                    # is surfaced and left to him — throwing away a drifting-but-
                    # recognisable render costs a re-render for no certain gain.
                    flags["same_person"] = arc >= _like.ARC_DIFFERENT''',
    "qc: arcface score")

rep("""                issues = [str(x)[:120] for x in (data.get("issues") or [])][:6]""",
    """                issues = [str(x)[:120] for x in (data.get("issues") or [])][:6]
                # After `issues` exists — the first cut of this referenced it
                # above its own definition and pyflakes caught it pre-ship.
                if arc is not None and arc < _like.ARC_MATCH:
                    issues = [f"likeness {flags['identity_verdict']} ({arc:.2f})"] + issues""",
    "qc: likeness issue line, after `issues` exists")

rep('''def _qc_blocking(ds_id: str, item_ids: List[str], urls: List[str], vision_model: str,
                 st: dict, ref_png: Optional[bytes] = None) -> None:''',
    '''def _qc_blocking(ds_id: str, item_ids: List[str], urls: List[str], vision_model: str,
                 st: dict, ref_png: Optional[bytes] = None,
                 baselines: Optional[List[Any]] = None) -> None:''',
    "qc: accept baselines")

# both QC callers build the baselines once, not per image
rep("""from typing import Any, Dict, List, Optional""",
    """from typing import Any, Dict, List, Optional, Tuple""",
    "typing: Tuple")

rep("""    ref_png = _identity_ref_png(ds)

    def _run():
        try:
            _qc_blocking(ds_id, targets, list(urls), vision_model, st, ref_png)""",
    """    ref_png = _identity_ref_png(ds)
    # Built ONCE: loading buffalo_l and embedding three references per image
    # would cost more than the vision call it rides alongside.
    _embs, _labels = _likeness_baselines(ds)
    if _labels:
        ds["likeness_baselines"] = _labels
        _write_ds(ds)

    def _run():
        try:
            _qc_blocking(ds_id, targets, list(urls), vision_model, st, ref_png, _embs)""",
    "qc route: pass baselines")

rep("""                _qc_blocking(ds_id, [it["id"] for it in items], urls, vision_model, st,
                             _identity_ref_png(cur))""",
    """                _qc_blocking(ds_id, [it["id"] for it in items], urls, vision_model, st,
                             _identity_ref_png(cur), _likeness_baselines(cur)[0])""",
    "repair loop: pass baselines")

# ── 3. the look-scores file now carries the right units ─────────────────────
rep('''    scores: Dict[str, Any] = {}
    for it, _fp in picked:
        q = it.get("qc") or {}
        s = q.get("identity_score")
        if s is None and "same_person" in q:
            s = 1.0 if q.get("same_person") else 0.0
        scores[stems[it["id"]]] = None if s is None else round(float(s), 4)
    vals = sorted(v for v in scores.values() if isinstance(v, (int, float)))
    cutoff = None
    if len(vals) >= 4:
        n = len(vals)
        med, q1, q3 = vals[n // 2], vals[n // 4], vals[(3 * n) // 4]
        cutoff = max(med - 1.5 * (q3 - q1), 0.25)
    return {"baselines": [f"{ds.get('char_name')} (Klein 3.0 front base)"],
            "cutoff": cutoff, "scores": scores}''',
    '''    scores: Dict[str, Any] = {}
    for it, _fp in picked:
        q = it.get("qc") or {}
        s = q.get("identity_score")
        # v1.218: ONLY an ArcFace cosine may go in this file. The trainer's
        # cutoff has a 0.25 floor in ArcFace units; a vision-LLM score is on a
        # different scale entirely, and feeding it one made the fence inert.
        if q.get("identity_method") != "arcface":
            s = None
        scores[stems[it["id"]]] = None if s is None else round(float(s), 4)
    return {"baselines": (ds.get("likeness_baselines")
                          or [f"{ds.get('char_name')} references"]),
            "cutoff": _like.cutoff([v for v in scores.values()
                                    if isinstance(v, (int, float))]),
            "scores": scores}''',
    "look scores: arcface only")

# ── 4. a CPU-only rescore + distribution route ──────────────────────────────
rep('''@router.post("/datasets/{ds_id}/export")''',
    '''@router.post("/datasets/{ds_id}/likeness")
async def dataset_likeness(ds_id: str):
    """Score every rendered image against the character\\'s references, ArcFace
    only — no vision model, no worker, no GPU.

    This is the measurement that v1.213 should have made before trusting a
    threshold. Run it on a real set and read `distribution.sanity` BEFORE
    anyone tunes a number against these scores."""
    ds = _read_ds(ds_id)
    if not _like.available():
        raise HTTPException(503, "ArcFace scoring is unavailable — "
                                 "`pip install insightface onnxruntime` on the app host. "
                                 f"({_like.health().get('error')})")
    embs, labels = _likeness_baselines(ds)
    if not embs:
        raise HTTPException(409, "no usable baseline — this character needs at least one "
                                 "reference with a detectable face (a front base or a face "
                                 "tag). Back-only references cannot be scored.")
    scores: Dict[str, Any] = {}
    changed = 0
    for it in ds.get("items", []):
        fp = _item_path(ds_id, it["id"])
        if not fp.exists():
            continue
        s = _like.score(fp, embs)
        scores[it["id"]] = None if s is None else round(s, 4)
        q = it.get("qc")
        if isinstance(q, dict):
            q["identity_score"] = scores[it["id"]]
            q["identity_method"] = "arcface" if s is not None else "none"
            if s is not None:
                q["identity_verdict"] = _like.verdict(s)[0]
                q["same_person"] = s >= _like.ARC_DIFFERENT
                q["ok"] = bool(q.get("framing_ok") and q.get("one_person")
                               and not q.get("artifacts") and not q.get("cropped_badly")
                               and q.get("outfit_ok", True) and q["same_person"])
            changed += 1
    ds["likeness_baselines"] = labels
    _write_ds(ds)
    logger.info("lora likeness[%s]: %d scored against %d baseline(s)",
                ds_id, len(scores), len(embs))
    return {"baselines": labels, "scored": len(scores), "qc_updated": changed,
            "distribution": _like.distribution(scores),
            "flags": _flag_summary(ds),
            "bands": {"match": _like.ARC_MATCH, "borderline": _like.ARC_BORDERLINE,
                      "different_person_floor": _like.ARC_DIFFERENT}}


@router.post("/datasets/{ds_id}/export")''',
    "route: likeness rescore")

# ── 5. health reports it ────────────────────────────────────────────────────
rep('''@router.get("/health")''',
    '''@router.get("/likeness-health")
async def likeness_health():
    """Whether objective identity scoring is available on this host."""
    return _like.health()


@router.get("/health")''',
    "route: likeness health")

# ── 6. the breakdown distinguishes the two methods ──────────────────────────
rep('''           "outfit_off": 0, "stuck": 0, "top_issues": {}}''',
    '''           "outfit_off": 0, "stuck": 0, "arcface_scored": 0, "no_face": 0,
           "top_issues": {}}''',
    "summary: arcface keys")

rep('''        if q.get("outfit_ok") is False:
            out["outfit_off"] += 1''',
    '''        if q.get("outfit_ok") is False:
            out["outfit_off"] += 1
        if q.get("identity_method") == "arcface":
            out["arcface_scored"] += 1
        # No detectable face is a CORRECT outcome for a back shot — counted, not
        # flagged, exactly as Fizgig never auto-excludes an unscoreable row.
        if q.get("identity_method") == "arcface" and q.get("identity_score") is None:
            out["no_face"] += 1''',
    "summary: count arcface coverage")

assert src != orig
P.write_text(src, "utf-8")
print(f"patched {P}  ({len(orig)} -> {len(src)} bytes)")
