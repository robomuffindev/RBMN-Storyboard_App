"""How TIGHT is the crop, as a number, so framing stops being the LLM's opinion.

WHY
    A QC pass over the twelve full-body rows just flagged ALL TWELVE as "not a
    full body shot, head to feet".  I have opened those images.  Every one of
    them shows the whole man, feet and margins included.  Twelve out of twelve
    wrong is not a marginal instrument, and it is the same failure shape that
    took angle away from the vision model in v1.234: it fails toward complaining.

    Framing is measurable.  A face crop and a full-body shot differ by how much
    of the frame the face occupies, and InsightFace already returns the face
    box for every image we score for identity.

WHAT IT MEASURES, per image
    face_h_ratio   face box height / image height   -- how tight the crop is
    face_w_ratio   face box width  / image width
    head_top       top of the face box / image height   -- headroom
    face_cy        vertical centre of the face / image height
    aspect         image width / height

    Plus the planned framing and the vision model's verdict, side by side.

WHAT IT IS FOR
    Nothing here decides anything.  It prints the distribution grouped by the
    planned framing so the bands can be CALIBRATED on real images instead of
    guessed -- the same order of operations that made the yaw bands hold up.

    Note honestly what this can and cannot answer.  "How tight is the crop" it
    answers directly.  "Are his feet inside the frame" it does not: that needs a
    person mask (rembg), and if the face ratio turns out not to separate the
    four framings cleanly, a mask is the next instrument rather than a tighter
    threshold on this one.

RUN
    scripts\\framing_probe.bat
"""
from __future__ import annotations

import argparse
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
    ap.add_argument("--max", type=int, default=200)
    a = ap.parse_args()

    try:
        import cv2
        import numpy as np
        from insightface.app import FaceAnalysis
    except Exception as e:  # noqa: BLE001
        print(f"MISSING DEPENDENCY: {type(e).__name__}: {e}")
        print("Run this with the venv the backend uses.")
        return 2

    print("loading buffalo_l on CPU ...", flush=True)
    app = FaceAnalysis(name="buffalo_l", providers=["CPUExecutionProvider"])
    app.prepare(ctx_id=-1, det_size=(640, 640))

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

    items = [it for it in ds["items"] if it.get("has_image")][: a.max]
    rows = []
    print("%5s %-9s %-20s %8s %8s %8s %8s  %-9s %s"
          % ("id", "framing", "angle", "face_h%", "face_w%", "head_top", "face_cy",
             "size", "llm_framing_ok"))
    print("-" * 100)
    for it in items:
        try:
            buf = np.frombuffer(fetch(it["url"]), dtype=np.uint8)
            img = cv2.imdecode(buf, cv2.IMREAD_COLOR)
        except Exception as e:  # noqa: BLE001
            print(f"{it['id']:>5} FETCH FAILED: {e}")
            continue
        if img is None:
            print(f"{it['id']:>5} DECODE FAILED")
            continue
        H, W = img.shape[:2]
        faces = app.get(img)
        rec = {"id": it["id"], "framing": it.get("framing"), "angle": it.get("angle"),
               "w": W, "h": H, "faces": len(faces),
               "face_h_ratio": None, "face_w_ratio": None,
               "head_top": None, "face_cy": None,
               "llm_framing_ok": (it.get("qc") or {}).get("framing_ok"),
               "llm_cropped": (it.get("qc") or {}).get("cropped_badly")}
        if faces:
            f = max(faces, key=lambda x: (x.bbox[2] - x.bbox[0]) * (x.bbox[3] - x.bbox[1]))
            x1, y1, x2, y2 = (float(v) for v in f.bbox)
            rec["face_h_ratio"] = round((y2 - y1) / H, 4)
            rec["face_w_ratio"] = round((x2 - x1) / W, 4)
            rec["head_top"] = round(y1 / H, 4)
            rec["face_cy"] = round(((y1 + y2) / 2) / H, 4)
        rows.append(rec)

        def _p(v):
            return ("%7.2f%%" % (v * 100)) if v is not None else "       -"

        print("%5s %-9s %-20s %s %s %s %s  %-9s %s"
              % (rec["id"], str(rec["framing"]), str(rec["angle"]),
                 _p(rec["face_h_ratio"]), _p(rec["face_w_ratio"]),
                 _p(rec["head_top"]), _p(rec["face_cy"]),
                 f"{W}x{H}", rec["llm_framing_ok"]))

    out = Path(__file__).resolve().parent / "_diag" / "framing.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"dataset": ds_id, "rows": rows}, indent=2), "utf-8")

    print("\n=== face height as a share of image height, by planned framing ===")
    by = {}
    for r in rows:
        by.setdefault(str(r["framing"]), []).append(r)
    for fr in sorted(by):
        vals = sorted(x["face_h_ratio"] for x in by[fr] if x["face_h_ratio"] is not None)
        n_noface = sum(1 for x in by[fr] if x["face_h_ratio"] is None)
        if vals:
            print("  %-9s n=%-3d  min %6.2f%%  median %6.2f%%  max %6.2f%%   no face: %d"
                  % (fr, len(by[fr]), vals[0] * 100, vals[len(vals) // 2] * 100,
                     vals[-1] * 100, n_noface))
        else:
            print("  %-9s n=%-3d  no measurable face in any of them" % (fr, len(by[fr])))

    print("\n=== do the four framings SEPARATE? ===")
    order = ["face", "headshot", "upper", "full"]
    spans = []
    for fr in order:
        vals = sorted(x["face_h_ratio"] for x in by.get(fr, [])
                      if x["face_h_ratio"] is not None)
        if vals:
            spans.append((fr, vals[0], vals[-1]))
    ok = True
    for i in range(len(spans) - 1):
        a_, b_ = spans[i], spans[i + 1]
        gap = a_[1] - b_[2]        # tighter framing should sit ENTIRELY above
        flag = "clear gap" if gap > 0 else "OVERLAP"
        if gap <= 0:
            ok = False
        print("  %-9s %5.2f-%5.2f%%   vs   %-9s %5.2f-%5.2f%%   -> %s"
              % (a_[0], a_[1] * 100, a_[2] * 100, b_[0], b_[1] * 100, b_[2] * 100, flag))
    print("\n  " + ("face height alone separates the framings — bands can be set from this."
                    if ok else
                    "face height alone does NOT separate them. A person mask (rembg) is the "
                    "next instrument, not a tighter threshold on this one."))
    print(f"\nwrote {out}")
    print("Tell Claude 'framing probe is done'.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
