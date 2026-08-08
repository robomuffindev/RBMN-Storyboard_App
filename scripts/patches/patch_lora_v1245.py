"""v1.245 — the person mask lands, ADVISORY until it has been measured.

Two things ship here and they are deliberately different in status.

1. A REAL BUG, fixed.  Every diagnostic script picked its dataset with
   `Sort-Object rendered -Descending`, i.e. the one with the MOST images.  So
   when a second character's dataset was created and measured, the scripts
   silently kept measuring the 40-image dorian set, and the only sign was an id
   in one line of output.  A whole "character two" test result was actually
   character one, re-measured.  They now take the newest dataset (the API
   already returns newest-first), print its NAME and character, and offer
   `-ListDatasets`.

2. `backend/services/subject.py` — a person mask, and with it the crop check
   that face geometry cannot do.  The rule is a binary, not a threshold:

       full        must NOT touch the bottom edge.  Feet inside the frame is
                   exactly what "head to feet" means.
       everything  MUST touch the bottom edge.  Being cut off at the waist is
       else        what makes a waist-up shot a waist-up shot; one with clear
                   air beneath it is a full body rendered small.

   That also settles the `upper` vs `full` separation the 2x face-height fence
   explicitly could not (v1.243) — on a property that is not a matter of degree.

   **It is ADVISORY in this version.**  `crop_ok` is recorded, counted and
   shown, and it does NOT gate `ok`.  Every instrument this session that was
   trusted before it was measured turned out to be wrong, and this one has been
   run on exactly zero real images.  `scripts\\crop_probe.bat` measures it over a
   whole dataset; when the numbers are in it gets promoted, in its own version,
   with the numbers in the changelog.

   A missing `rembg` is a degraded mode, same contract as insightface: crop goes
   back to unchecked and `not_checked` says so.
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


rep('''from backend.services import likeness as _like''',
    '''from backend.services import likeness as _like
from backend.services import subject as _subj''',
    "import")

# ── QC records the crop verdict ──────────────────────────────────────────────
rep('''                flags["framing_note"] = _fwhy''',
    '''                # v1.245: crop, from a person mask. ADVISORY — recorded and
                # counted, but it does not gate `ok` until it has been measured
                # on real images. Every instrument trusted before measurement
                # this session turned out to be wrong.
                _bx = _subj.box(_item_path(ds_id, iid)) if _subj.available() else None
                _cok, _cwhy = _subj.crop_verdict(item.get("framing"), _bx)
                flags["crop_note"] = _cwhy
                flags["body_h_ratio"] = None if not _bx else _bx.get("body_h_ratio")
                flags["crop_ok"] = True if _cok is None else bool(_cok)
                flags["crop_method"] = "unmeasured" if _cok is None else "person-mask"
                flags["framing_note"] = _fwhy''',
    "qc crop")

rep('''                if _fok is False:
                    issues = [f"framing: {_fwhy}"] + issues''',
    '''                if _fok is False:
                    issues = [f"framing: {_fwhy}"] + issues
                if _cok is False:
                    issues = [f"crop (advisory): {_cwhy}"] + issues''',
    "qc crop issue")

# ── the summary counts it and stops claiming crop is unchecked ───────────────
rep('''           "framing_off": 0, "framing_measured": 0, "framing_unmeasured": 0,''',
    '''           "framing_off": 0, "framing_measured": 0, "framing_unmeasured": 0,
           "crop_off": 0, "crop_measured": 0, "crop_unmeasured": 0,''',
    "summary keys")

rep('''        if q.get("framing_method") == "face-height":
            out["framing_measured"] += 1
        elif q.get("framing_method") == "unmeasured":
            out["framing_unmeasured"] += 1''',
    '''        if q.get("framing_method") == "face-height":
            out["framing_measured"] += 1
        elif q.get("framing_method") == "unmeasured":
            out["framing_unmeasured"] += 1
        if q.get("crop_ok") is False:
            out["crop_off"] += 1
        if q.get("crop_method") == "person-mask":
            out["crop_measured"] += 1
        elif q.get("crop_method") == "unmeasured":
            out["crop_unmeasured"] += 1''',
    "summary counters")

rep('''    # v1.242: framing has an instrument now. Crop still does not — that needs a
    # person mask to answer honestly, and face geometry cannot.
    out["not_checked"] = ["crop"]
    out["unreliable"] = ["expression"]''',
    '''    # v1.245: crop has an instrument, but it is not trusted yet — measured on
    # zero real images at the time it shipped. It moves out of `unreliable` and
    # into `ok` in its own version, once the probe numbers exist.
    out["not_checked"] = [] if out["crop_measured"] else ["crop"]
    out["unreliable"] = ["expression"] + (["crop (advisory, not yet validated)"]
                                          if out["crop_measured"] else [])''',
    "not_checked")

# ── the measuring route does crop too ────────────────────────────────────────
rep('''            fok, fwhy = _like.framing_verdict(it.get("framing"), it.get("angle"), pv, cal)
            q = it.get("qc")''',
    '''            fok, fwhy = _like.framing_verdict(it.get("framing"), it.get("angle"), pv, cal)
            bx = _subj.box(_item_path(ds_id, it["id"])) if _have_mask else None
            cok, cwhy = _subj.crop_verdict(it.get("framing"), bx)
            q = it.get("qc")''',
    "route crop")

rep('''            rows.append({"id": it["id"], "angle": it.get("angle"),
                         "framing": it.get("framing"),
                         "yaw": q["yaw"], "ok": aok, "note": why,
                         "framing_ok": fok, "framing_note": fwhy,
                         "face_h_ratio": q["face_h_ratio"],''',
    '''            q["crop_note"] = cwhy
            q["body_h_ratio"] = None if not bx else bx.get("body_h_ratio")
            q["crop_ok"] = True if cok is None else bool(cok)
            q["crop_method"] = "unmeasured" if cok is None else "person-mask"
            rows.append({"id": it["id"], "angle": it.get("angle"),
                         "framing": it.get("framing"),
                         "yaw": q["yaw"], "ok": aok, "note": why,
                         "framing_ok": fok, "framing_note": fwhy,
                         "face_h_ratio": q["face_h_ratio"],
                         "crop_ok": cok, "crop_note": cwhy,
                         "body_h_ratio": q["body_h_ratio"],
                         "subject_box": bx,''',
    "route crop row")

rep('''        poses: Dict[str, Any] = {}''',
    '''        _have_mask = _subj.available()
        poses: Dict[str, Any] = {}''',
    "route mask availability")

rep('''            for k in ("yaw", "yaw_detail", "angle_note", "angle_ok", "angle_method",
                      "framing_ok", "framing_method", "framing_note", "face_h_ratio"):''',
    '''            for k in ("yaw", "yaw_detail", "angle_note", "angle_ok", "angle_method",
                      "framing_ok", "framing_method", "framing_note", "face_h_ratio",
                      "crop_ok", "crop_method", "crop_note", "body_h_ratio"):''',
    "route merge crop fields")

rep('''            "framing_cal": cal,''',
    '''            "framing_cal": cal,
            "by_crop": _subj.summarise(rows),
            "crop": {**_subj.health(),
                     "status": "ADVISORY — recorded and counted, does not fail an image "
                               "until it has been measured on real images "
                               "(scripts\\\\crop_probe.bat)"},''',
    "route crop summary")

assert src != orig
P.write_text(src, "utf-8")
print(f"patched {P}  ({len(orig)} -> {len(src)} bytes)")
