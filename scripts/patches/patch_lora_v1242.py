"""v1.242 — framing is measured, and the two "unmeasurable" rows turn out broken.

Replayed over the 40 real images, the face-height bands give 34 correct, 2
wrong, 4 unmeasured — and the 4 unmeasured are exactly the 4 back rows, where
having no face is the correct answer rather than a gap.

The 2 wrong are the two rows every previous instrument called unmeasurable:

    0001  a FACE CROP with no detectable face
    0021  an upper shot with his face 87% of the way down the frame

Neither is a measurement gap.  Both are broken images, and both are now
failures the repair loop can act on.

Framing therefore goes back into `ok`.  v1.232 made it advisory and v1.241
removed it, both times because the vision model could not do it; the objection
was never to the check, it was to the instrument.  A shot type that renders as
the wrong shot type is a real training defect — a "full body" row that is
actually a headshot teaches the trigger word the wrong composition.

`POST /datasets/{id}/angles` now measures both from the same face detection —
one pass, no extra cost, still no GPU and no Ollama — and reports `by_framing`
alongside `by_angle`.  `not_checked` drops "framing" because it now is.
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


# ── 1. QC measures framing too ───────────────────────────────────────────────
rep('''                _pv = _like.pose(_item_path(ds_id, iid))
                _aok, _awhy = _like.angle_verdict(item.get("angle"), _pv)''',
    '''                _pv = _like.pose(_item_path(ds_id, iid))
                _aok, _awhy = _like.angle_verdict(item.get("angle"), _pv)
                _fok, _fwhy = _like.framing_verdict(item.get("framing"),
                                                    item.get("angle"), _pv)
                # v1.242: framing is measured from the same face box. It was
                # advisory in v1.232 and gone in v1.241 because the VISION MODEL
                # could not do it; the objection was never to the check.
                flags["framing_note"] = _fwhy
                flags["face_h_ratio"] = None if not _pv else _pv.get("face_h_ratio")
                if _fok is None:
                    flags["framing_ok"] = True
                    flags["framing_method"] = "unmeasured"
                else:
                    flags["framing_ok"] = bool(_fok)
                    flags["framing_method"] = "face-height"''',
    "qc framing")

rep('''                if _aok is False:
                    issues = [f"angle: {_awhy}"] + issues''',
    '''                if _aok is False:
                    issues = [f"angle: {_awhy}"] + issues
                if _fok is False:
                    issues = [f"framing: {_fwhy}"] + issues''',
    "qc framing issue")

rep('''                flags["framing_checked"] = False''', '''''', "drop the placeholder")

rep('''                # v1.232 made framing and crop advisory; v1.241 removed them
                # outright. What remains are the checks that have held up.
                ok = (flags["one_person"]
                      and not flags["artifacts"]
                      and flags.get("same_person", True)
                      and flags.get("outfit_ok", True))''',
    '''                # v1.242: framing is back in, because it is MEASURED now.
                # An unmeasured framing (a back row) defaults True and never
                # fails an image, same contract as angle.
                ok = (flags["one_person"]
                      and not flags["artifacts"]
                      and flags.get("same_person", True)
                      and flags.get("outfit_ok", True)
                      and flags.get("framing_ok", True))''',
    "ok includes framing")

# ── 2. the summary counts it ─────────────────────────────────────────────────
rep('''    out = {"flagged": 0, "checked": 0, "artifacts": 0,
           "angle_off": 0, "expression_off": 0,''',
    '''    out = {"flagged": 0, "checked": 0, "artifacts": 0,
           "angle_off": 0, "expression_off": 0,
           "framing_off": 0, "framing_measured": 0, "framing_unmeasured": 0,''',
    "summary keys")

rep('''        if q.get("angle_method") == "head-yaw":
            out["angle_measured"] += 1
        elif q.get("angle_method") == "unmeasured":
            out["angle_unmeasured"] += 1''',
    '''        if q.get("angle_method") == "head-yaw":
            out["angle_measured"] += 1
        elif q.get("angle_method") == "unmeasured":
            out["angle_unmeasured"] += 1
        if q.get("framing_ok") is False:
            out["framing_off"] += 1
        if q.get("framing_method") == "face-height":
            out["framing_measured"] += 1
        elif q.get("framing_method") == "unmeasured":
            out["framing_unmeasured"] += 1''',
    "summary counters")

rep('''    out["not_checked"] = ["framing", "crop"]''',
    '''    # v1.242: framing has an instrument now. Crop still does not — that needs a
    # person mask to answer honestly, and face geometry cannot.
    out["not_checked"] = ["crop"]''',
    "not_checked")

# ── 3. the measuring route does both ─────────────────────────────────────────
rep('''            pv = _like.pose(fp)
            aok, why = _like.angle_verdict(it.get("angle"), pv)
            q = it.get("qc")''',
    '''            pv = _like.pose(fp)
            aok, why = _like.angle_verdict(it.get("angle"), pv)
            fok, fwhy = _like.framing_verdict(it.get("framing"), it.get("angle"), pv)
            q = it.get("qc")''',
    "route: measure framing")

rep('''            if aok is None:
                q["angle_ok"] = True
                q["angle_method"] = "unmeasured"
            else:
                q["angle_ok"] = bool(aok)
                q["angle_method"] = "head-yaw"
            rows.append({"id": it["id"], "angle": it.get("angle"),
                         "framing": it.get("framing"),
                         "yaw": q["yaw"], "ok": aok, "note": why,
                         "det_score": None if not pv else pv.get("det_score"),
                         "kps_yaw": None if not pv else pv.get("kps_yaw")})''',
    '''            if aok is None:
                q["angle_ok"] = True
                q["angle_method"] = "unmeasured"
            else:
                q["angle_ok"] = bool(aok)
                q["angle_method"] = "head-yaw"
            q["framing_note"] = fwhy
            q["face_h_ratio"] = None if not pv else pv.get("face_h_ratio")
            if fok is None:
                q["framing_ok"] = True
                q["framing_method"] = "unmeasured"
            else:
                q["framing_ok"] = bool(fok)
                q["framing_method"] = "face-height"
            rows.append({"id": it["id"], "angle": it.get("angle"),
                         "framing": it.get("framing"),
                         "yaw": q["yaw"], "ok": aok, "note": why,
                         "framing_ok": fok, "framing_note": fwhy,
                         "face_h_ratio": q["face_h_ratio"],
                         "det_score": None if not pv else pv.get("det_score"),
                         "kps_yaw": None if not pv else pv.get("kps_yaw")})''',
    "route: record framing")

rep('''            for k in ("yaw", "yaw_detail", "angle_note", "angle_ok", "angle_method"):''',
    '''            for k in ("yaw", "yaw_detail", "angle_note", "angle_ok", "angle_method",
                      "framing_ok", "framing_method", "framing_note", "face_h_ratio"):''',
    "route: merge framing fields")

rep('''    logger.info("lora angles[%s]: measured %d image(s)", ds_id, len(rows))
    return {"measured": len(rows), "by_angle": by_angle, "rows": rows,
            "bands": health["bands"], "sign": health["sign"],
            "flags": _flag_summary(ds_after)}''',
    '''    by_framing: Dict[str, Any] = {}
    for r in rows:
        b = by_framing.setdefault(str(r["framing"]), {"n": 0, "ok": 0, "miss": 0,
                                                      "unmeasured": 0, "ratios": []})
        b["n"] += 1
        b["ok" if r["framing_ok"] is True
          else ("miss" if r["framing_ok"] is False else "unmeasured")] += 1
        if r.get("face_h_ratio") is not None:
            b["ratios"].append(float(r["face_h_ratio"]))
    for b in by_framing.values():
        rs = sorted(b.pop("ratios"))
        b["face_h_median"] = None if not rs else round(rs[len(rs) // 2], 4)
        b["face_h_min"] = None if not rs else round(rs[0], 4)
        b["face_h_max"] = None if not rs else round(rs[-1], 4)
    fhealth = _like.framing_health()
    logger.info("lora angles[%s]: measured %d image(s)", ds_id, len(rows))
    return {"measured": len(rows), "by_angle": by_angle, "by_framing": by_framing,
            "rows": rows,
            "bands": health["bands"], "sign": health["sign"],
            "framing_bands": fhealth["bands"], "face_cy_max": fhealth["face_cy_max"],
            "flags": _flag_summary(ds_after)}''',
    "route: by_framing")

assert src != orig
P.write_text(src, "utf-8")
print(f"patched {P}  ({len(orig)} -> {len(src)} bytes)")
