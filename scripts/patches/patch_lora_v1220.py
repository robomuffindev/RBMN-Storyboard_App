"""v1.220 — stop ArcFace blocking the event loop.

Three async routes did CPU work (and, on first use, a ~300MB model download)
INLINE. FastAPI runs `async def` handlers on the event loop, so every one of
them froze the WHOLE app — not just its own request — for the duration.

  * `/datasets/{id}/likeness`  — the scoring loop itself.
  * `/datasets/{id}/qc`        — `_likeness_baselines()` ran before the work was
                                 handed to a thread, so the model load happened
                                 on the loop even though the QC pass did not.
                                 My blocking-call scan MISSED this one: it looked
                                 for `_like.*` calls directly and this goes
                                 through a helper, and the route contains a
                                 `_spawn` so it looked already-threaded.
  * `/likeness-health`         — `health()` calls `available()` calls `_app()`.
                                 A health check that downloads 300MB before
                                 answering is not a health check.

`asyncio.to_thread` throughout, which is the pattern the caption/enrich path in
this same file already uses.
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


# ── 1. the scoring loop goes to a thread ────────────────────────────────────
rep('''    embs, labels = _likeness_baselines(ds)
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
            q["identity_method"] = "arcface"      # it ran; s is None only if no face
            if s is not None:
                q["identity_verdict"] = _like.verdict(s)[0]
                q["same_person"] = s >= _like.ARC_DIFFERENT
                q["ok"] = bool(q.get("framing_ok") and q.get("one_person")
                               and not q.get("artifacts") and not q.get("cropped_badly")
                               and q.get("outfit_ok", True) and q["same_person"])
            changed += 1
    ds["likeness_baselines"] = labels
    _write_ds(ds)''',
    '''    import asyncio

    def _work() -> Tuple[Dict[str, Any], List[str], int]:
        """Everything CPU-bound, on a worker thread.

        v1.220: this used to run inline in an `async def`, which pins the event
        loop — the whole app froze until it finished, and on first use that
        included downloading buffalo_l (~300MB)."""
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
                q["identity_method"] = "arcface"   # it ran; s is None only if no face
                if s is not None:
                    q["identity_verdict"] = _like.verdict(s)[0]
                    q["same_person"] = s >= _like.ARC_DIFFERENT
                    q["ok"] = bool(q.get("framing_ok") and q.get("one_person")
                                   and not q.get("artifacts") and not q.get("cropped_badly")
                                   and q.get("outfit_ok", True) and q["same_person"])
                changed += 1
        return scores, labels, changed

    scores, labels, changed = await asyncio.to_thread(_work)
    ds["likeness_baselines"] = labels
    _write_ds(ds)''',
    "likeness: to_thread")

rep('''    return {"baselines": labels, "scored": len(scores), "qc_updated": changed,
            "distribution": _like.distribution(scores),''',
    '''    return {"baselines": labels, "scored": len(scores), "qc_updated": changed,
            "distribution": await asyncio.to_thread(_like.distribution, scores),''',
    "likeness: distribution off the loop too")

# ── 2. availability must not be checked ON the loop either ─────────────────
rep('''    if not _like.available():
        raise HTTPException(503, "ArcFace scoring is unavailable — "''',
    '''    import asyncio as _aio
    # available() loads the model on first call. On the loop that is a minutes-
    # long freeze before we have even started.
    if not await _aio.to_thread(_like.available):
        raise HTTPException(503, "ArcFace scoring is unavailable — "''',
    "likeness: availability check off the loop")

# ── 3. QC built its baselines on the loop ──────────────────────────────────
rep('''    ref_png = _identity_ref_png(ds)
    # Built ONCE: loading buffalo_l and embedding three references per image
    # would cost more than the vision call it rides alongside.
    _embs, _labels = _likeness_baselines(ds)
    if _labels:
        ds["likeness_baselines"] = _labels
        _write_ds(ds)

    def _run():
        try:
            _qc_blocking(ds_id, targets, list(urls), vision_model, st, ref_png, _embs)''',
    '''    ref_png = _identity_ref_png(ds)

    def _run():
        try:
            # Built ONCE per run, and INSIDE the thread: embedding three
            # references (and, first time, downloading the model) on the event
            # loop froze the whole app before the QC pass even started.
            _embs, _labels = _likeness_baselines(ds)
            if _labels:
                cur0 = _read_ds(ds_id)
                cur0["likeness_baselines"] = _labels
                _write_ds(cur0)
            _qc_blocking(ds_id, targets, list(urls), vision_model, st, ref_png, _embs)''',
    "qc: baselines inside the worker thread")

# ── 3b. `embs` is now local to the worker; labels is 1:1 with it ───────────
rep("""                ds_id, len(scores), len(embs))""",
    """                ds_id, len(scores), len(labels))""",
    "likeness: log the baseline count from labels")

# ── 4. the health route ────────────────────────────────────────────────────
rep('''async def likeness_health():
    """Whether objective identity scoring is available on this host."""
    return _like.health()''',
    '''async def likeness_health():
    """Whether objective identity scoring is available on this host.

    Threaded: `health()` loads the model to answer honestly, and the first call
    downloads ~300MB. A health check must never pin the event loop."""
    import asyncio
    return await asyncio.to_thread(_like.health)''',
    "health: to_thread")

assert src != orig
P.write_text(src, "utf-8")
print(f"patched {P}  ({len(orig)} -> {len(src)} bytes)")
