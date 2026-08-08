"""v1.247 — a profile is scored against a PROFILE, not against a front view.

MEASURED (dorian-v1, 40 images, ArcFace against three frontal baselines)

    profile_left    0003 0.42 · 0023 0.33 · 0033 0.40
    profile_right   0036 0.33
    every other row: nothing below 0.45

Four of the eight profiles land under `ARC_MATCH`, and nothing else in the
dataset does.  That is not identity drift, it is geometry: a face turned 60-80
degrees away scores low against a frontal baseline for LOOKING SIDEWAYS.  It is
the same shape as the back-row problem fixed in v1.221, and the fix is the same
kind — stop comparing things that are not comparable.

The character already has left and right references.  `_likeness_baselines`
never used them for this: it collected front base, then face / left / right refs
in that order and stopped at THREE, so the right reference never made the cut at
all, and a right-profile render was scored against front + face + left.

WHAT CHANGES
    Baselines are built as VIEW SETS and each row is scored against the set that
    matches how it was shot:

        front · three_quarter_*   frontal set — front base + face reference
        profile_left              the left reference (the left base only if
                                  there is no tagged left reference)
        profile_right             the right reference, likewise
        back                      frontal set, and the score is still not an
                                  identity verdict (v1.221)

    Real tagged references are preferred over generated bases, because a
    generated base is a Klein render and scoring renders against renders is how
    you get a beautiful number that means nothing.

WHAT DOES NOT CHANGE, DELIBERATELY
    `ARC_MATCH` stays at 0.45.  Whether profile scores actually rise is a
    measurement, not a prediction, and moving a threshold in the same breath as
    changing what it measures is how v1.213 shipped an inert file.  Every row
    now records `identity_baseline` (which set) and `identity_baseline_n` (how
    many references it rests on), so the next `/likeness` run answers it.

    A side set usually holds ONE reference, against Fizgig's three-baseline
    averaging.  That is a real weakness — one photograph's framing bias can
    dominate — and `identity_baseline_n` is there so it is visible rather than
    assumed away.
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


# ── 1. view-matched baseline sets ────────────────────────────────────────────
rep('''def _identity_ref_png(ds: dict) -> Optional[bytes]:''',
    '''# v1.247: which baseline set a row's angle should be scored against.
_ANGLE_BASELINE = {
    "front": "front", "three_quarter_left": "front", "three_quarter_right": "front",
    "profile_left": "left", "profile_right": "right",
    "back": "front",     # scored, but never a verdict — see v1.221
}


def _baseline_sets(ds: dict) -> Dict[str, Tuple[List[Any], List[str]]]:
    """Reference embeddings grouped by VIEW, so a profile can be scored against
    a profile.

    A real tagged reference beats a generated base every time: a generated base
    is itself a Klein render, and scoring renders against renders produces a
    beautiful number that means nothing."""
    out: Dict[str, Tuple[List[Any], List[str]]] = {"front": ([], []),
                                                   "left": ([], []),
                                                   "right": ([], [])}
    try:
        char = _load_char(ds["char_slug"])
    except Exception:  # noqa: BLE001
        return out
    slug = ds["char_slug"]
    picks: Dict[str, List[Tuple[Optional[Path], str]]] = {"front": [], "left": [], "right": []}

    fp, lbl = _base_for_view(slug, char, "front", ds.get("base_mode"))
    picks["front"].append((fp, lbl))
    for tag in ("face",):
        refs = _refs_by_tag(char, tag)
        if refs:
            picks["front"].append((_cdir(slug) / "refs" / f"{refs[-1]['id']}.png",
                                   f"{tag} reference"))
    for side in ("left", "right"):
        refs = _refs_by_tag(char, side)
        if refs:
            picks[side].append((_cdir(slug) / "refs" / f"{refs[-1]['id']}.png",
                                f"{side} reference"))
        else:
            # No tagged side reference. The generated base is second best and
            # is labelled so the score can be read with that in mind.
            bp, blbl = _base_for_view(slug, char, side, ds.get("base_mode"))
            if bp:
                picks[side].append((bp, f"{blbl} (generated)"))

    for view, plist in picks.items():
        embs, labels, seen = [], [], set()
        for p, lbl in plist:
            if not p or str(p) in seen or not Path(p).exists():
                continue
            seen.add(str(p))
            e = _like.embed(p)
            if e is None:             # a reference with no detectable face is
                continue              # useless as a baseline, not an error
            embs.append(e)
            labels.append(lbl)
            if len(embs) >= 3:
                break
        out[view] = (embs, labels)
    return out


def _baselines_for(sets: Dict[str, Tuple[List[Any], List[str]]],
                   angle: Optional[str]) -> Tuple[List[Any], List[str], str]:
    """The right baselines for one row, falling back to frontal when a side has
    none.  The fallback is NAMED in the label so a geometry-penalised score is
    never mistaken for a clean one."""
    want = _ANGLE_BASELINE.get(str(angle or "").lower(), "front")
    embs, labels = sets.get(want, ([], []))
    if embs:
        return embs, labels, want
    embs, labels = sets.get("front", ([], []))
    return embs, labels, (f"front (no {want} reference)" if want != "front" else "front")


def _identity_ref_png(ds: dict) -> Optional[bytes]:''',
    "baseline sets")

# ── 2. QC scores against the matching set ────────────────────────────────────
rep('''                 st: dict, ref_png: Optional[bytes] = None,
                 baselines: Optional[List[Any]] = None) -> None:''',
    '''                 st: dict, ref_png: Optional[bytes] = None,
                 baselines: Optional[List[Any]] = None,
                 baseline_sets: Optional[Dict[str, Any]] = None) -> None:''',
    "qc signature")

rep('''                arc = _like.score(_item_path(ds_id, iid), baselines) if baselines else None''',
    '''                # v1.247: scored against the baselines that MATCH how this row
                # was shot. A profile against a frontal baseline scores low for
                # looking sideways, which is geometry and not identity.
                _bl, _bl_lbl, _bl_key = (
                    _baselines_for(baseline_sets, item.get("angle"))
                    if baseline_sets else (baselines or [], [], "front"))
                flags["identity_baseline"] = _bl_key
                flags["identity_baseline_n"] = len(_bl)
                flags["identity_baseline_labels"] = _bl_lbl
                arc = _like.score(_item_path(ds_id, iid), _bl) if _bl else None''',
    "qc scoring")

rep('''                flags["identity_method"] = ("arcface" if baselines
                                            else ("vision-llm" if ref_png else "none"))''',
    '''                flags["identity_method"] = ("arcface" if _bl
                                            else ("vision-llm" if ref_png else "none"))''',
    "qc method")

rep('''                if arc is None and not baselines:''',
    '''                if arc is None and not _bl:''',
    "qc note guard")

# ── 3. both callers build the sets ───────────────────────────────────────────
rep('''            _embs, _labels = _likeness_baselines(ds)''',
    '''            _sets = _baseline_sets(ds)
            _embs, _labels = _sets.get("front", ([], []))''',
    "caller 1")

rep('''                             _identity_ref_png(cur), _likeness_baselines(cur)[0])''',
    '''                             _identity_ref_png(cur), None, _baseline_sets(cur))''',
    "caller 2")

# ── 4. the likeness route too ────────────────────────────────────────────────
rep('''        embs, labels = _likeness_baselines(ds)
        if not embs:''',
    '''        sets = _baseline_sets(ds)
        embs, labels = sets.get("front", ([], []))
        if not embs:''',
    "route baselines")

rep('''            s = _like.score(fp, embs)
            scores[it["id"]] = None if s is None else round(s, 4)
            q = it.get("qc")
            if isinstance(q, dict):
                q["identity_score"] = scores[it["id"]]
                q["identity_method"] = "arcface"   # it ran; s is None only if no face''',
    '''            _bl, _bl_lbl, _bl_key = _baselines_for(sets, it.get("angle"))
            s = _like.score(fp, _bl) if _bl else None
            scores[it["id"]] = None if s is None else round(s, 4)
            by_view.setdefault(_bl_key, []).append(scores[it["id"]])
            q = it.get("qc")
            if isinstance(q, dict):
                q["identity_score"] = scores[it["id"]]
                q["identity_baseline"] = _bl_key
                q["identity_baseline_n"] = len(_bl)
                q["identity_baseline_labels"] = _bl_lbl
                q["identity_method"] = "arcface"   # it ran; s is None only if no face''',
    "route scoring")

rep('''        scores: Dict[str, Any] = {}
        changed = 0''',
    '''        scores: Dict[str, Any] = {}
        by_view: Dict[str, List[Any]] = {}
        changed = 0''',
    "route by_view")

rep('''        return scores, labels, changed''',
    '''        # v1.247: per baseline set, so "profiles score low" stops being a thing
        # you have to notice by reading forty rows.
        view_stats: Dict[str, Any] = {}
        for k, vals in by_view.items():
            v = sorted(x for x in vals if x is not None)
            view_stats[k] = {"n": len(vals), "scored": len(v),
                             "median": round(v[len(v) // 2], 4) if v else None,
                             "min": round(v[0], 4) if v else None,
                             "max": round(v[-1], 4) if v else None,
                             "below_match": sum(1 for x in v if x < _like.ARC_MATCH),
                             "baselines": len(sets.get(k.split(" ")[0], ([], []))[0])}
        return scores, labels, changed, view_stats''',
    "route stats")

rep('''    scores, labels, changed = await asyncio.to_thread(_work)''',
    '''    scores, labels, changed, view_stats = await asyncio.to_thread(_work)''',
    "route await")

rep('''    return {"baselines": labels, "scored": len(scores), "qc_updated": changed,''',
    '''    return {"baselines": labels, "by_baseline": view_stats,
            "baseline_note": ("v1.247: profiles are scored against the profile reference, "
                              "not against a front view. A set resting on ONE reference "
                              "has that photograph's framing bias in it — "
                              "`baselines` per view says how many."),
            "scored": len(scores), "qc_updated": changed,''',
    "route return")


# ── 5. the old builder becomes a shim, so there is ONE implementation ────────
rep('''def _likeness_baselines(ds: dict) -> Tuple[List[Any], List[str]]:
    """Up to THREE reference embeddings, and what they were.''',
    '''def _likeness_baselines(ds: dict) -> Tuple[List[Any], List[str]]:
    """The FRONTAL baseline set. Superseded by `_baseline_sets` in v1.247 and
    kept as a shim so there is exactly one implementation — two functions
    building baselines slightly differently is how a profile ended up scored
    against a left reference and a face crop.

    Up to THREE reference embeddings, and what they were.''',
    "shim docstring")

rep('''    embs, labels = [], []
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
    return embs, labels''',
    '''    return _baseline_sets(ds).get("front", ([], []))''',
    "shim body")

assert src != orig
P.write_text(src, "utf-8")
print(f"patched {P}  ({len(orig)} -> {len(src)} bytes)")
