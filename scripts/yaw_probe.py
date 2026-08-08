"""Measure which way the man is actually FACING, with a number.

WHY
    The vision model (qwen2.5vl:7b) has been the only judge of "is this the
    right angle", and against my own eyes on 12 full-body images it got the
    three-quarter rows 1 of 5 right -- it passed all three rows that genuinely
    came out front-facing and failed the one that was correctly turned.  Every
    angle measurement taken so far (the "14/14 three-quarter miss", the
    "tq_base=front halves it to 6/12") was read off that instrument.

    InsightFace's buffalo_l bundle -- already installed for ArcFace identity --
    ships `landmark_3d_68`, which fits a 3D face model and yields head pose.
    If `face.pose` is populated, we get YAW IN DEGREES: an objective, repeatable
    answer to the one question the LLM cannot hold steady.

    This script does not change anything.  It measures, prints, and writes JSON.

WHAT IT PROVES
    Ground truth from looking at the pictures (dorian-v1, full-body rows):
        0029 three_quarter_right  TURNED
        0039 three_quarter_right  TURNED
        0032 three_quarter_left   front-facing  (WRONG)
        0035 three_quarter_right  front-facing  (WRONG)
        0037 three_quarter_left   front-facing  (WRONG)
        0033 profile_left / 0036 profile_right  correct profiles
        0030 0031 0034 0040 front                correct fronts
    If yaw separates those groups, yaw replaces the LLM for angle QC.
    If it does not, we have learned that too, and cheaply.

RUN
    scripts\\yaw_probe.bat
    scripts\\yaw_probe.bat --framing full
"""
from __future__ import annotations

import argparse
import io
import json
import sys
import urllib.request
from pathlib import Path

HOST = "http://127.0.0.1:8899"


def api(path: str):
    with urllib.request.urlopen(HOST + path, timeout=60) as r:
        return json.loads(r.read().decode("utf-8"))


def fetch(url: str) -> bytes:
    with urllib.request.urlopen(HOST + url, timeout=120) as r:
        return r.read()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--id", default="")
    ap.add_argument("--framing", default="")
    ap.add_argument("--max", type=int, default=200)
    a = ap.parse_args()

    try:
        import cv2
        import numpy as np
        from insightface.app import FaceAnalysis
    except Exception as e:  # noqa: BLE001
        print(f"MISSING DEPENDENCY: {type(e).__name__}: {e}")
        print("This needs the same venv the backend runs in "
              "(insightface, onnxruntime, opencv-python).")
        return 2

    print("loading buffalo_l on CPU ...", flush=True)
    app = FaceAnalysis(name="buffalo_l", providers=["CPUExecutionProvider"])
    app.prepare(ctx_id=-1, det_size=(640, 640))
    mods = sorted(getattr(app, "models", {}).keys())
    print(f"  modules: {', '.join(mods)}")
    has_pose = "landmark_3d_68" in mods
    print(f"  landmark_3d_68 present: {has_pose}"
          f"{'' if has_pose else '   -> face.pose will be absent; falling back to keypoints'}")

    # v1.245: the NEWEST dataset, not the one with the most images. Picking by
    # "rendered" silently kept measuring a 40-image set while a new character
    # was the thing being tested.
    _all = api("/api/lora/datasets")["datasets"]     # API returns newest first
    ds_id = a.id or _all[0]["id"]
    _info = next((d for d in _all if d["id"] == ds_id), {})
    ds = api(f"/api/lora/datasets/{ds_id}")
    print(f"DATASET: {_info.get('name')}   character: {_info.get('char_slug')}")
    print(f"  id {ds_id} - {_info.get('rendered')} of {_info.get('total')} rendered, "
          f"created {_info.get('created_at')}")
    if len(_all) > 1 and not a.id:
        print(f"  (newest of {len(_all)} datasets - pass --id to pick another)")
    print()

    items = [it for it in ds["items"] if it.get("has_image")]
    if a.framing:
        items = [it for it in items if it.get("framing") == a.framing]
    items = items[: a.max]
    if not items:
        print("no rendered images matched.")
        return 0

    rows = []
    print(f"{'id':>5} {'planned angle':<20} {'yaw':>8} {'pitch':>7} {'roll':>7} "
          f"{'kps-yaw':>8} {'det':>5}  llm_angle_ok")
    print("-" * 82)
    for it in items:
        try:
            buf = np.frombuffer(fetch(it["url"]), dtype=np.uint8)
            img = cv2.imdecode(buf, cv2.IMREAD_COLOR)
        except Exception as e:  # noqa: BLE001
            print(f"{it['id']:>5} {it.get('angle',''):<20}  FETCH FAILED: {e}")
            continue
        if img is None:
            print(f"{it['id']:>5} {it.get('angle',''):<20}  DECODE FAILED")
            continue
        faces = app.get(img)
        rec = {"id": it["id"], "framing": it.get("framing"), "angle": it.get("angle"),
               "llm_angle_ok": (it.get("qc") or {}).get("angle_ok"),
               "faces": len(faces), "yaw": None, "pitch": None, "roll": None,
               "kps_yaw": None, "det_score": None}
        if faces:
            f = max(faces, key=lambda x: (x.bbox[2] - x.bbox[0]) * (x.bbox[3] - x.bbox[1]))
            rec["det_score"] = round(float(getattr(f, "det_score", 0.0)), 3)
            pose = getattr(f, "pose", None)
            if pose is not None:
                rec["pitch"] = round(float(pose[0]), 1)
                rec["yaw"] = round(float(pose[1]), 1)
                rec["roll"] = round(float(pose[2]), 1)
            # Independent, model-free estimate from the 5 detector keypoints:
            # where the nose sits between the two eyes.  0 = centred, +/-1 = at
            # an eye.  It cannot give degrees, but it CANNOT be absent, and it
            # is a check on the pose number rather than a substitute for it.
            kps = getattr(f, "kps", None)
            if kps is not None and len(kps) >= 3:
                le, re_, nose = kps[0], kps[1], kps[2]
                span = float(re_[0] - le[0])
                if abs(span) > 1e-3:
                    mid = (float(le[0]) + float(re_[0])) / 2.0
                    rec["kps_yaw"] = round((float(nose[0]) - mid) / (span / 2.0), 3)
        rows.append(rec)

        def _f(v, w, p):
            return ("%*.*f" % (w, p, v)) if v is not None else ("-".rjust(w))

        print("%5s %-20s %s %s %s %s %s  %s" % (
            rec["id"], str(rec["angle"]),
            _f(rec["yaw"], 8, 1), _f(rec["pitch"], 7, 1), _f(rec["roll"], 7, 1),
            _f(rec["kps_yaw"], 8, 3), _f(rec["det_score"], 5, 2),
            rec["llm_angle_ok"]))

    out = Path(__file__).resolve().parent / "_diag" / "yaw.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"dataset": ds_id, "has_pose_module": has_pose,
                               "rows": rows}, indent=2), "utf-8")
    print(f"\nwrote {out}")

    by = {}
    for r in rows:
        by.setdefault(r["angle"], []).append(r)
    print("\n=== yaw by planned angle ===")
    for ang in sorted(by):
        vals = [r["yaw"] for r in by[ang] if r["yaw"] is not None]
        ks = [r["kps_yaw"] for r in by[ang] if r["kps_yaw"] is not None]
        print(f"  {ang:<20} n={len(by[ang]):<3} "
              f"yaw {('min %6.1f  med %6.1f  max %6.1f' % (min(vals), sorted(vals)[len(vals)//2], max(vals))) if vals else 'none measured'}"
              f"   kps {('%.2f..%.2f' % (min(ks), max(ks))) if ks else '-'}")
    print("\nTell Claude 'yaw is done'.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
