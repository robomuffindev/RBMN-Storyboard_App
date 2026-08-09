"""k3_face_audit.py -- measure whether a Klein 3.0 view SET actually holds one face.

v1.275.4.  The 🙂 face anchor (v1.275.2) is a claim: "lead every view job's refs
with a zoomed face close-up and the faces stop drifting."  A claim is not a
measurement.  This scores it.

For a character it walks every ref in char.json and reports, per ref:

  arc_vs_front   ArcFace cosine against the FRONT reference (the uploaded truth)
  arc_vs_face    ArcFace cosine against the `face` anchor ref, when one exists
  yaw            head yaw in degrees from landmark_3d_68 (NEGATIVE = nose toward
                 the LEFT edge of the image) + the agreement confidence
  det            the detector's face-box score, so "no face found" is visible as
                 itself instead of masquerading as a bad likeness

Back rows are expected to score near zero -- there is no face in them.  That is
not a failure and the report says so rather than leaving a red number lying
around for someone to over-read (the v1.213 / v1.259 / v1.264 shape: a real
number on the wrong scale).

    python scripts\\k3_face_audit.py --char clonejoan
    python scripts\\k3_face_audit.py --char clonejoan --json

Runs on the app machine's venv, CPU-only, no GPU, no worker, costs nothing.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.services import likeness          # noqa: E402
from backend.api.klein3 import _K3_ROOT        # noqa: E402  (import-time const)


def _fmt(v, nd=4):
    return "  --  " if v is None else f"{v:.{nd}f}"


def audit(slug: str) -> dict:
    cdir = _K3_ROOT / slug
    cj = cdir / "char.json"
    if not cj.exists():
        raise SystemExit(f"no character at {cdir}")
    c = json.loads(cj.read_text("utf-8"))
    refs = c.get("refs", [])

    def path_of(r):
        return cdir / "refs" / f"{r.get('id')}.png"

    # Baselines must be the SAME ones the backend uses, or this report describes
    # a pipeline nobody is running.  Front = the uploaded truth (prefer an
    # upload; a generated front is itself a claim under test).  Face = the
    # BEST-scoring face-tagged ref, matching v1.275.4b's reuse rule -- NOT the
    # newest, which is what the first cut of this script read and which pointed
    # at a 0.3926 anchor while the backend was using a 0.4660 one.
    fronts = [r for r in refs if r.get("tag") == "front"]
    front = next((r for r in fronts if r.get("source") == "upload"),
                 fronts[-1] if fronts else None)
    if front is None:
        raise SystemExit("character has no front reference -- nothing to score against")
    e_front = likeness.embed(path_of(front))

    face, e_face, _best = None, None, None
    for r in [x for x in refs if x.get("tag") == "face"]:
        e = likeness.embed(path_of(r))
        s = (likeness.cosine(e, e_front)
             if (e is not None and e_front is not None) else None)
        if face is None or (s is not None and (_best is None or s > _best)):
            face, e_face, _best = r, e, s

    rows = []
    for r in refs:
        p = path_of(r)
        row = {"id": r.get("id"), "tag": r.get("tag"),
               "source": r.get("source"), "name": r.get("name"),
               "created_at": r.get("created_at"),
               "exists": p.exists(), "arc_vs_front": None,
               "arc_vs_face": None, "yaw": None, "kps_yaw": None,
               "yaw_ok": None, "det": None, "face_h": None, "note": ""}
        if not p.exists():
            row["note"] = "FILE MISSING"
            rows.append(row)
            continue
        e = likeness.embed(p)
        if e is None:
            row["note"] = ("no face detected -- expected for a back view"
                           if r.get("tag") == "back" else "NO FACE DETECTED")
        else:
            if e_front is not None:
                row["arc_vs_front"] = likeness.cosine(e, e_front)
            if e_face is not None:
                row["arc_vs_face"] = likeness.cosine(e, e_face)
        pv = likeness.pose(p)
        if pv:
            row["yaw"] = pv.get("yaw")
            row["kps_yaw"] = pv.get("kps_yaw")
            row["yaw_ok"] = likeness.angle_confident(pv)
            row["det"] = pv.get("det_score")
            row["face_h"] = pv.get("face_h_ratio")
        rows.append(row)

    scored = [r["arc_vs_front"] for r in rows
              if r["arc_vs_front"] is not None and r["tag"] not in ("front", "back")]
    summary = {
        "slug": slug,
        "front_ref": front.get("id"),
        "face_anchor": (face or {}).get("id"),
        "scored_rows": len(scored),
        "min_vs_front": min(scored) if scored else None,
        "mean_vs_front": (sum(scored) / len(scored)) if scored else None,
        "bands": {"different": likeness.ARC_DIFFERENT,
                  "borderline": likeness.ARC_BORDERLINE,
                  "match": likeness.ARC_MATCH},
    }
    return {"summary": summary, "rows": rows,
            "likeness_health": likeness.health()}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--char", required=True)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    out = audit(a.char)
    if a.json:
        print(json.dumps(out, indent=2))
        return 0

    h = out["likeness_health"]
    # likeness.health() reports {"available": bool, ...} -- there is no "status"
    # key, and reading one that does not exist made the first run of this script
    # cry DEGRADED over a perfectly good measurement.  Validate the instrument.
    print(f"likeness: available={h.get('available')} "
          f"model={h.get('model')} {h.get('error') or ''}")
    if not h.get("available"):
        print("  !! measurement is DEGRADED -- numbers below are not trustworthy")
    s = out["summary"]
    print(f"\ncharacter {s['slug']}   front={s['front_ref']}   "
          f"face anchor={s['face_anchor'] or 'NONE'}")
    print(f"bands: different<{s['bands']['different']}  "
          f"borderline<{s['bands']['borderline']}  match>={s['bands']['match']}\n")
    print(f"{'tag':<7}{'source':<11}{'vs front':>10}{'vs face':>10}"
          f"{'yaw':>8}{'kpsyaw':>8}{'ok':>4}{'det':>6}{'faceH':>7}  note")
    print("-" * 88)
    for r in out["rows"]:
        yaw = "   --" if r["yaw"] is None else f"{r['yaw']:+.1f}"
        ky = "   --" if r["kps_yaw"] is None else f"{r['kps_yaw']:+.2f}"
        ok = "-" if r["yaw_ok"] is None else ("Y" if r["yaw_ok"] else "n")
        print(f"  {r['id']}  {str(r['created_at'])[:19]}")
        print(f"{str(r['tag']):<7}{str(r['source']):<11}"
              f"{_fmt(r['arc_vs_front']):>10}{_fmt(r['arc_vs_face']):>10}"
              f"{yaw:>8}{ky:>8}{ok:>4}{_fmt(r['det'], 2):>6}"
              f"{_fmt(r['face_h'], 3):>7}  {r['note']}")
    print("-" * 88)
    if s["scored_rows"]:
        print(f"side/face rows scored: {s['scored_rows']}   "
              f"min {s['min_vs_front']:.4f}   mean {s['mean_vs_front']:.4f}")
        if s["min_vs_front"] < likeness.ARC_BORDERLINE:
            print("VERDICT: at least one view is DRIFTING off the front reference.")
        elif s["min_vs_front"] < likeness.ARC_MATCH:
            print("VERDICT: same-person territory, not a solid match on every row.")
        else:
            print("VERDICT: every scored view is a solid match to the front ref.")
        print("NOTE: profiles score low against a FRONTAL baseline by construction "
              "(§1 open item) -- read the spread across rows, not one number.")
    else:
        print("no scorable rows -- nothing measured.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
