"""Is the subject actually inside the frame?  Measured with a person mask.

WHY
    Face geometry answers "how tight is the crop" and cannot answer the question
    that matters for a full-body shot: are his feet in the picture.  A face box
    says nothing about feet.  It also cannot separate `upper` from `full` — their
    face-height medians sit 1.7x apart against a within-type spread of 1.6x.

    A person mask answers both, with a binary rather than a threshold:

        full        must NOT touch the bottom edge
        everything  MUST touch the bottom edge -- being cut off at the waist is
        else        what makes a waist-up shot a waist-up shot

WHAT THIS IS FOR
    Nothing here decides anything, and the backend does not fail an image on
    crop yet.  This prints, per shot type, how many land each way and how tall
    the subject is in the frame, so the check can be VALIDATED on real images
    before it is trusted.  Every instrument this session that was trusted before
    it was measured turned out to be wrong.

    The number to look at is `body_h` per shot type: if full-body rows sit high
    (80-95% of the frame) and waist-up rows differ, the mask has separated the
    two shot types that face height could not.

RUN
    scripts\\crop_probe.bat
    scripts\\crop_probe.bat --id <dataset-id>
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
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

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    try:
        from backend.services import subject as subj
    except Exception as e:  # noqa: BLE001
        print(f"could not import the subject module: {type(e).__name__}: {e}")
        return 2
    h = subj.health()
    if not h["available"]:
        print(f"rembg is not available: {h['error']}")
        print(f"install it with:  {h['install']}")
        print("(it is a CPU model, about 176MB on first use, and never touches the GPU)")
        return 2
    print(f"person mask ready: {h['model']}")
    print(f"  edge tolerance {h['edge_tolerance'] * 100:.1f}% of the frame")
    print(f"  must be cut off at the bottom: "
          f"{', '.join(k for k, v in h['cut_at_bottom'].items() if v)}")
    print(f"  must NOT be: {', '.join(k for k, v in h['cut_at_bottom'].items() if not v)}")

    _all = api("/api/lora/datasets")["datasets"]      # newest first
    ds_id = a.id or _all[0]["id"]
    info = next((d for d in _all if d["id"] == ds_id), {})
    ds = api(f"/api/lora/datasets/{ds_id}")
    print(f"\nDATASET: {info.get('name')}   character: {info.get('char_slug')}")
    print(f"  id {ds_id} - {info.get('rendered')} of {info.get('total')} rendered")
    if len(_all) > 1 and not a.id:
        print(f"  (newest of {len(_all)} datasets - pass --id to pick another)")

    items = [it for it in ds["items"] if it.get("has_image")][: a.max]
    if not items:
        print("\nnothing rendered.")
        return 0

    rows = []
    tmp = Path(tempfile.mkdtemp(prefix="crop_probe_"))
    print(f"\n{'id':>5} {'framing':<9} {'angle':<20} {'body_h':>7} {'top':>7} {'bot':>7} "
          f"{'cover':>7} {'dom':>6}  edges          verdict")
    print("-" * 108)
    for it in items:
        fp = tmp / f"{it['id']}.png"
        try:
            fp.write_bytes(fetch(it["url"]))
        except Exception as e:  # noqa: BLE001
            print(f"{it['id']:>5} FETCH FAILED: {e}")
            continue
        bx = subj.box(fp)
        ok, why = subj.crop_verdict(it.get("framing"), bx)
        rec = {"id": it["id"], "framing": it.get("framing"), "angle": it.get("angle"),
               "crop_ok": ok, "note": why,
               "body_h_ratio": None if not bx else bx.get("body_h_ratio"),
               "coverage": None if not bx else bx.get("coverage"),
               "dominance": None if not bx else bx.get("dominance"),
               "trustworthy": None if not bx else bx.get("trustworthy"),
               "y1": None if not bx else bx.get("y1"),
               "y2": None if not bx else bx.get("y2"),
               "touches_top": None if not bx else bx.get("touches_top"),
               "touches_bottom": None if not bx else bx.get("touches_bottom")}
        rows.append(rec)

        def _p(v):
            return ("%6.1f%%" % (v * 100)) if v is not None else "      -"

        edges = ""
        if bx:
            edges = ("T" if bx["touches_top"] else "-") + ("B" if bx["touches_bottom"] else "-")
        verdict = "OK" if ok is True else ("WRONG" if ok is False else "unmeasured")
        print("%5s %-9s %-20s %s %s %s %s %s  %-14s %s"
              % (rec["id"], str(rec["framing"]), str(rec["angle"]),
                 _p(rec["body_h_ratio"]), _p(rec["y1"]), _p(rec["y2"]),
                 _p(rec["coverage"]),
                 ("%5.2f" % rec["dominance"]) if rec["dominance"] is not None else "    -",
                 edges, verdict))
        try:
            fp.unlink()
        except OSError:
            pass

    out = Path(__file__).resolve().parent / "_diag" / "crop.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"dataset": ds_id, "health": h, "rows": rows}, indent=2),
                   "utf-8")

    print("\n=== by shot type ===")
    summ = subj.summarise(rows)
    for k in ("face", "headshot", "upper", "full"):
        if k not in summ:
            continue
        b = summ[k]
        med = f"{b['body_h_median'] * 100:.1f}%" if b["body_h_median"] is not None else "-"
        rng = (f"{b['body_h_min'] * 100:.0f}-{b['body_h_max'] * 100:.0f}%"
               if b["body_h_min"] is not None else "-")
        print(f"  {k:<9} n={b['n']:<3} ok {b['ok']:<3} wrong {b['miss']:<3} "
              f"unmeasured {b['unmeasured']:<3}  subject height median {med}  range {rng}")

    print("\n=== does the mask separate upper from full? ===")
    u, f = summ.get("upper", {}), summ.get("full", {})
    if u.get("body_h_median") and f.get("body_h_median"):
        print(f"  upper subject height {u['body_h_min'] * 100:.0f}-{u['body_h_max'] * 100:.0f}%"
              f"   vs   full {f['body_h_min'] * 100:.0f}-{f['body_h_max'] * 100:.0f}%")
        print("  (the real separator is the BOTTOM EDGE, not the height — upper shots "
              "should read 'B', full shots should not)")
    else:
        print("  not enough measured rows in both to compare")

    wrong = [r for r in rows if r["crop_ok"] is False]
    unm = [r for r in rows if r["crop_ok"] is None]
    print(f"\n{len(wrong)} wrong, {len(unm)} unmeasured, "
          f"{len(rows) - len(wrong) - len(unm)} correct")
    for r in wrong:
        print(f"  WRONG      {r['id']}  {r['framing']:<9} {r['note']}")
    for r in unm:
        print(f"  unmeasured {r['id']}  {r['framing']:<9} {r['note']}")

    print(f"\nwrote {out}")
    print("Crop is ADVISORY in the backend - it does not fail any image yet.")
    print("Tell Claude 'crop probe is done'.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
