"""v1.234 — angle is decided by a measured number, not by the vision model.

The evidence is in the likeness patch's docstring: over 40 rendered images the
vision model's `angle_ok` is noise on three-quarter-left (its OK group and its
MISS group sit at the same yaw) and ANTI-correlated on three-quarter-right (it
failed +36.8, +30.8 and +27.7 and passed +4.3 and -6.1).  It disagreed with the
measurement on 16 of 40.

So:
  * QC computes head yaw and decides `angle_ok` from it.  The vision model's
    answer is kept as `angle_ok_llm` — visible, comparable, no longer in charge.
  * An UNMEASURED angle (no face, weak fit, no pose model) stays `True`.  Not
    because it passed, but because falling back to a verdict now known to be
    noise is worse than admitting the row was not measured.  `angle_method`
    says which of the three happened, every time.
  * `POST /datasets/{id}/angles` scores the whole dataset from head pose alone.
    No Ollama, no GPU, no re-render: seconds, on CPU, whenever he wants it.
    That is the loop the three-quarter wording experiment runs in.
  * `_flag_summary` gains `angle_unmeasured` so "measured and fine" and "never
    measured" stop sharing a bucket.
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


# ── 1. QC decides angle from yaw ─────────────────────────────────────────────
rep('''                arc = _like.score(_item_path(ds_id, iid), baselines) if baselines else None''',
    '''                # v1.234: head yaw, measured.  Runs on the same already-loaded
                # CPU model as the identity score, so it costs one extra face
                # detection and no GPU at all.
                _pv = _like.pose(_item_path(ds_id, iid))
                _aok, _awhy = _like.angle_verdict(item.get("angle"), _pv)
                flags["angle_ok_llm"] = flags["angle_ok"]
                flags["angle_note"] = _awhy
                flags["yaw"] = None if not _pv else _pv.get("yaw")
                flags["yaw_detail"] = _pv
                if _aok is None:
                    # Unmeasured is not failed.  The vision model's answer is
                    # known noise on exactly these rows, so it does not get to
                    # stand in for a measurement that did not happen.
                    flags["angle_ok"] = True
                    flags["angle_method"] = "unmeasured"
                else:
                    flags["angle_ok"] = bool(_aok)
                    flags["angle_method"] = "head-yaw"
                arc = _like.score(_item_path(ds_id, iid), baselines) if baselines else None''',
    "qc: yaw")

rep('''                issues = [str(x)[:120] for x in (data.get("issues") or [])][:6]''',
    '''                issues = [str(x)[:120] for x in (data.get("issues") or [])][:6]
                if _aok is False:
                    issues = [f"angle: {_awhy}"] + issues''',
    "qc: angle issue")

# ── 2. the summary distinguishes unmeasured from fine ────────────────────────
rep('''           "not_one_person": 0, "face_unclear": 0, "identity_off": 0,
           "outfit_off": 0, "stuck": 0, "arcface_scored": 0, "no_face": 0,
           "back_low_likeness": 0, "top_issues": {}}''',
    '''           "not_one_person": 0, "face_unclear": 0, "identity_off": 0,
           "outfit_off": 0, "stuck": 0, "arcface_scored": 0, "no_face": 0,
           "back_low_likeness": 0, "angle_measured": 0, "angle_unmeasured": 0,
           "top_issues": {}}''',
    "summary keys")

rep('''        if q.get("angle_ok") is False:
            out["angle_off"] += 1''',
    '''        if q.get("angle_ok") is False:
            out["angle_off"] += 1
        # v1.234: "measured and correct" and "never measured" both used to read
        # as a pass.  They are different facts and are now counted apart.
        if q.get("angle_method") == "head-yaw":
            out["angle_measured"] += 1
        elif q.get("angle_method") == "unmeasured":
            out["angle_unmeasured"] += 1''',
    "summary counts")

# ── 3. a GPU-free whole-dataset angle pass ───────────────────────────────────
rep('''@router.post("/datasets/{ds_id}/export")''',
    '''@router.post("/datasets/{ds_id}/angles")
async def dataset_angles(ds_id: str):
    """Re-measure every rendered image's ANGLE from head pose.

    v1.234.  No vision model, no ComfyUI, no GPU — one CPU face detection per
    image against a model that is already resident.  A 40-image dataset is
    seconds, against minutes for a QC pass through a single Ollama server, so
    the wording experiments can be scored as fast as they render.

    Writes `angle_ok` / `yaw` / `angle_method` into each `qc` block and leaves
    every other field alone.  A row that has never been QC'd gets a `qc` block
    holding only the angle facts, which is honest: that is all we know."""
    ds = _read_ds(ds_id)
    import asyncio as _aio
    if not await _aio.to_thread(_like.available):
        raise HTTPException(503, "head pose is unavailable — "
                                 "`pip install insightface onnxruntime` on the app host. "
                                 f"({_like.health().get('error')})")
    health = await _aio.to_thread(_like.angle_health)
    if not health.get("available"):
        raise HTTPException(503, health.get("error") or "landmark_3d_68 not loaded")

    def _work() -> Dict[str, Any]:
        # v1.220's lesson: every bit of this is CPU-bound and must stay off the
        # event loop or the whole app stops answering while it runs.
        rows: List[Dict[str, Any]] = []
        for it in ds.get("items", []):
            fp = _item_path(ds_id, it["id"])
            if not fp.exists():
                continue
            pv = _like.pose(fp)
            aok, why = _like.angle_verdict(it.get("angle"), pv)
            q = it.get("qc")
            if not isinstance(q, dict):
                q = {}
                it["qc"] = q
            q["yaw"] = None if not pv else pv.get("yaw")
            q["yaw_detail"] = pv
            q["angle_note"] = why
            if aok is None:
                q["angle_ok"] = True
                q["angle_method"] = "unmeasured"
            else:
                q["angle_ok"] = bool(aok)
                q["angle_method"] = "head-yaw"
            rows.append({"id": it["id"], "angle": it.get("angle"),
                         "framing": it.get("framing"),
                         "yaw": q["yaw"], "ok": aok, "note": why,
                         "det_score": None if not pv else pv.get("det_score"),
                         "kps_yaw": None if not pv else pv.get("kps_yaw")})
        return {"rows": rows}

    res = await _aio.to_thread(_work)
    with _DS_WRITE_LOCK:
        # Re-read under the lock and copy the angle fields across, so a render
        # or QC finishing mid-pass is not clobbered.  v1.223 is why.
        cur = _read_ds(ds_id)
        by = {r["id"]: r for r in res["rows"]}
        for it in cur.get("items", []):
            r = by.get(it["id"])
            if not r:
                continue
            src_q = next((x.get("qc") or {} for x in ds["items"] if x["id"] == it["id"]), {})
            q = it.get("qc")
            if not isinstance(q, dict):
                q = {}
                it["qc"] = q
            for k in ("yaw", "yaw_detail", "angle_note", "angle_ok", "angle_method"):
                if k in src_q:
                    q[k] = src_q[k]
        _write_ds(cur)
        ds_after = cur

    rows = res["rows"]
    by_angle: Dict[str, Any] = {}
    for r in rows:
        b = by_angle.setdefault(str(r["angle"]), {"n": 0, "ok": 0, "miss": 0,
                                                  "unmeasured": 0, "yaws": []})
        b["n"] += 1
        b["ok" if r["ok"] is True else ("miss" if r["ok"] is False else "unmeasured")] += 1
        if r["yaw"] is not None:
            b["yaws"].append(float(r["yaw"]))
    for b in by_angle.values():
        ys = sorted(b.pop("yaws"))
        # The median is the number that survives a bad fit or two, so it is the
        # one to compare wording variants on.
        b["yaw_median"] = None if not ys else round(ys[len(ys) // 2], 1)
        b["yaw_min"] = None if not ys else round(ys[0], 1)
        b["yaw_max"] = None if not ys else round(ys[-1], 1)
    logger.info("lora angles[%s]: measured %d image(s)", ds_id, len(rows))
    return {"measured": len(rows), "by_angle": by_angle, "rows": rows,
            "bands": health["bands"], "sign": health["sign"],
            "flags": _flag_summary(ds_after)}


@router.post("/datasets/{ds_id}/export")''',
    "angles route")

assert src != orig
P.write_text(src, "utf-8")
print(f"patched {P}  ({len(orig)} -> {len(src)} bytes)")
