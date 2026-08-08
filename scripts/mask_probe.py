"""The crop check just failed a picture I can see is correct.  Which model is wrong?

0006 is an extreme close-up: his shirt fills the bottom fifth of the frame, edge
to edge. `subject.box()` reports the subject stops 9% above the bottom edge, so
v1.246's rule failed it for "clear space below him". The space is his shirt.

That is a false positive from `u2net`, which segments the SALIENT OBJECT and
loses a sunlit beige shirt against a warm brick wall. `u2net_human_seg` is
trained on people specifically. This runs both over the same images and prints
where they disagree, so the choice is made on numbers rather than on which one
sounds better.

It also dumps the masks next to the images (`scripts\\_diag\\masks\\`) so the
disagreements can be LOOKED at, which is how the last three instrument bugs were
actually caught.

RUN
    scripts\\mask_probe.py --ds dorian-v1-b1966f
    scripts\\mask_probe.py --ds dorian-v1-b1966f --ids 0003,0006,0015,0016,0025
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

MODELS = ("u2net", "u2net_human_seg")


def measure(sess, blob: bytes):
    """y2 (lowest subject row, 0-1), coverage, and whether it reaches the edge."""
    import io
    import numpy as np
    from PIL import Image
    from rembg import remove
    cut = remove(Image.open(io.BytesIO(blob)).convert("RGB"), session=sess)
    a = np.array(cut)[:, :, 3]
    m = a > 128
    if not m.any():
        return None
    rows = np.where(m.any(axis=1))[0]
    h = m.shape[0]
    return {"y1": round(float(rows[0]) / h, 4),
            "y2": round(float(rows[-1] + 1) / h, 4),
            "coverage": round(float(m.mean()), 4),
            "bottom_band": round(float(m[int(h * 0.95):].mean()), 4)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ds", required=True)
    ap.add_argument("--ids", default="")
    ap.add_argument("--dump", action="store_true", help="write the masks out to look at")
    a = ap.parse_args()

    from rembg import new_session

    from backend.api.lora import _item_path, _read_ds
    from backend.services import subject as _subj

    ds = _read_ds(a.ds)
    want = {x.strip() for x in a.ids.split(",") if x.strip()}
    rows = [it for it in ds["items"]
            if (not want or it["id"] in want) and _item_path(a.ds, it["id"]).exists()]
    if not rows:
        print("no matching rendered images")
        return 1

    sess = {m: new_session(m) for m in MODELS}
    print(f"{len(rows)} image(s), {len(MODELS)} models\n")
    print(f"{'id':<6} {'framing':<9} " + " ".join(f"{m:>22}" for m in MODELS) + "   verdicts")
    print("-" * 100)
    out, flips = [], []
    for it in rows:
        fp = _item_path(a.ds, it["id"])
        blob = fp.read_bytes()
        cells, verd = [], []
        rec = {"id": it["id"], "framing": it.get("framing")}
        for m in MODELS:
            r = measure(sess[m], blob)
            rec[m] = r
            if not r:
                cells.append(f"{'no mask':>22}")
                verd.append("-")
                continue
            cells.append(f"  y2={r['y2']:.3f} cov={r['coverage']:.2f}")
            # The same rule subject.py applies, evaluated on THIS model's box.
            bx = {"coverage": r["coverage"], "dominance": 1.0,
                  "y1": r["y1"], "y2": r["y2"],
                  "touches_bottom": r["y2"] >= 1.0 - _subj.EDGE_TOL,
                  "touches_top": r["y1"] <= _subj.EDGE_TOL,
                  "trustworthy": True, "body_h_ratio": r["y2"] - r["y1"]}
            ok, _why = _subj.crop_verdict(it.get("framing"), bx)
            verd.append("OK" if ok is not False else "WRONG")
        rec["verdicts"] = verd
        out.append(rec)
        if len(set(verd)) > 1:
            flips.append(rec)
        print(f"{it['id']:<6} {str(it.get('framing')):<9} " + " ".join(cells)
              + "   " + " / ".join(verd))

    print("\n=== where the two models disagree ===")
    if not flips:
        print("  nowhere — the model choice does not explain the failures")
    for r in flips:
        print(f"  {r['id']} ({r['framing']}): " + " / ".join(
            f"{m} y2={r[m]['y2']:.3f} -> {v}" for m, v in zip(MODELS, r["verdicts"])))

    for m in MODELS:
        w = sum(1 for r in out if r["verdicts"][MODELS.index(m)] == "WRONG")
        print(f"  {m:<18} fails {w} of {len(out)}")

    p = Path(__file__).resolve().parent / "_diag" / "mask_probe.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(out, indent=2), "utf-8")
    print(f"\nwrote {p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
