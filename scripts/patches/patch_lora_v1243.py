"""v1.243 — the framing check calibrates itself from the dataset it is judging.

v1.242's bands came from one character on one set of canvas sizes, so character
two would have started failing good images and it would have looked like a
render problem.  Now every image is judged against the median face height of its
OWN shot type in its OWN dataset.

  * `POST /datasets/{id}/angles` measures every face, calibrates from those
    measurements, then judges — and STORES the calibration on the dataset as
    `framing_cal` so the QC pass can use the same numbers instead of guessing.
  * QC reads `framing_cal` when it is there.  When it is not (a dataset never
    put through `/angles`), it falls back to the one-character bands and the
    per-image note says so out loud.
  * The dataset-level check — do the four shot types come out in the right
    order — is reported once, in `framing_cal.warnings`, because no per-image
    verdict can tell you that your shot types are not actually different shots.
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


# ── 1. QC uses the stored calibration ────────────────────────────────────────
rep('''                _fok, _fwhy = _like.framing_verdict(item.get("framing"),
                                                    item.get("angle"), _pv)''',
    '''                _fok, _fwhy = _like.framing_verdict(item.get("framing"),
                                                    item.get("angle"), _pv,
                                                    _framing_cal)''',
    "qc uses cal")

rep('''    ref_png = None
    """Vision QC over the given images, one thread per Ollama server.  Blocking."""''',
    '''    ref_png = None
    """Vision QC over the given images, one thread per Ollama server.  Blocking."""
    # v1.243: the dataset's own framing calibration, read ONCE. A per-image
    # thread cannot compute this — it needs every image's face height — so a
    # dataset that has never been through `/angles` falls back to the
    # one-character default bands, and each note says which was used.
    try:
        _framing_cal = (_read_ds(ds_id) or {}).get("framing_cal") or None
    except Exception:  # noqa: BLE001 — QC must not die for want of a calibration
        _framing_cal = None''',
    "qc reads cal")

# ── 2. the measuring route calibrates before it judges ───────────────────────
rep('''    def _work() -> Dict[str, Any]:
        # v1.220's lesson: every bit of this is CPU-bound and must stay off the
        # event loop or the whole app stops answering while it runs.
        rows: List[Dict[str, Any]] = []
        for it in ds.get("items", []):
            fp = _item_path(ds_id, it["id"])
            if not fp.exists():
                continue
            pv = _like.pose(fp)
            aok, why = _like.angle_verdict(it.get("angle"), pv)
            fok, fwhy = _like.framing_verdict(it.get("framing"), it.get("angle"), pv)
            q = it.get("qc")''',
    '''    def _work() -> Dict[str, Any]:
        # v1.220's lesson: every bit of this is CPU-bound and must stay off the
        # event loop or the whole app stops answering while it runs.
        #
        # v1.243: TWO passes. The first measures every face; the second judges
        # against what the first found. Framing cannot be judged one image at a
        # time without an absolute threshold, and an absolute threshold is
        # exactly the thing tuned to one character. `pose` is cached on
        # (path, mtime, size), so the second pass costs nothing.
        poses: Dict[str, Any] = {}
        for it in ds.get("items", []):
            fp = _item_path(ds_id, it["id"])
            if fp.exists():
                poses[it["id"]] = _like.pose(fp)
        cal = _like.framing_calibrate(
            [(it.get("framing"), (poses.get(it["id"]) or {}).get("face_h_ratio"))
             for it in ds.get("items", [])
             if poses.get(it["id"]) and (poses[it["id"]] or {}).get("face_h_ratio")])

        rows: List[Dict[str, Any]] = []
        for it in ds.get("items", []):
            if it["id"] not in poses:
                continue
            pv = poses[it["id"]]
            aok, why = _like.angle_verdict(it.get("angle"), pv)
            fok, fwhy = _like.framing_verdict(it.get("framing"), it.get("angle"), pv, cal)
            q = it.get("qc")''',
    "route calibrates")

rep('''        return {"rows": rows}''',
    '''        return {"rows": rows, "cal": cal}''',
    "route returns cal")

rep('''        cur = _read_ds(ds_id)
        by = {r["id"]: r for r in res["rows"]}''',
    '''        cur = _read_ds(ds_id)
        # Stored so the QC pass judges framing by the same numbers rather than
        # falling back to bands calibrated on a different character.
        cur["framing_cal"] = res["cal"]
        by = {r["id"]: r for r in res["rows"]}''',
    "store cal")

rep('''    fhealth = _like.framing_health()''',
    '''    fhealth = _like.framing_health()
    cal = res["cal"]''',
    "cal in scope")

rep('''            "framing_bands": fhealth["bands"], "face_cy_max": fhealth["face_cy_max"],
            "flags": _flag_summary(ds_after)}''',
    '''            "framing_cal": cal,
            "framing_method": fhealth["method"],
            "framing_fallback_bands": fhealth["fallback_bands"],
            "face_cy_max": fhealth["face_cy_max"],
            "flags": _flag_summary(ds_after)}''',
    "report cal")

assert src != orig
P.write_text(src, "utf-8")
print(f"patched {P}  ({len(orig)} -> {len(src)} bytes)")
