"""Offline test for v1.221 — the two findings from his first real ArcFace scan."""
import ast, sys
from pathlib import Path
SRC = Path(sys.argv[1] if len(sys.argv) > 1 else "backend/api/lora.py").read_text("utf-8")
ns = {"Any": object, "List": list, "Dict": dict, "Optional": object}
WANT = {"_base_view_for", "_flag_summary", "_by_key"}
CONST = {"_TQ_ANGLES", "ANGLES", "FRAMINGS", "MAX_ATTEMPTS"}
chunks = []
for n in ast.parse(SRC).body:
    if isinstance(n, ast.FunctionDef) and n.name in WANT:
        n.decorator_list = []; chunks.append(ast.unparse(n))
    if isinstance(n, ast.Assign) and any(getattr(t, "id", "") in CONST for t in n.targets):
        chunks.append(ast.unparse(n))
class _L:
    ARC_DIFFERENT = 0.25
exec("from __future__ import annotations\n\n" + "\n\n".join(chunks), ns)
ns["_like"] = _L
fails = []
def check(l, c, e=""):
    print(("  PASS  " if c else "  FAIL  ") + l + ("" if c else f"  <- {e}")); fails.append(l) if not c else None

bv = ns["_base_view_for"]
check("tq: default 'side' keeps the pre-v1.221 mapping",
      bv("three_quarter_left", "left", "side") == "left"
      and bv("three_quarter_right", "right", "side") == "right")
check("tq: 'front' redirects BOTH three-quarter angles to the front base",
      bv("three_quarter_left", "left", "front") == "front"
      and bv("three_quarter_right", "right", "front") == "front")
check("tq: profiles are NEVER redirected (their base already matches, 17-43% miss)",
      bv("profile_left", "left", "front") == "left"
      and bv("profile_right", "right", "front") == "right")
check("tq: front and back are untouched",
      bv("front", "front", "front") == "front" and bv("back", "back", "front") == "back")
check("tq: a missing/garbage setting falls back to the planned view",
      bv("three_quarter_left", "left", "") == "left"
      and bv("three_quarter_left", "left", None) == "left"
      and bv("three_quarter_left", "left", "banana") == "left")
check("tq: case-insensitive", bv("three_quarter_left", "left", "FRONT") == "front")
check("tq: _TQ_ANGLES matches the real ANGLES keys",
      set(ns["_TQ_ANGLES"]) < {a[0] for a in ns["ANGLES"]}, ns["_TQ_ANGLES"])

check("jobs: the selector is actually used", "_base_view_for(it[\"angle\"], ang[3]," in SRC)
check("jobs: it reads tq_base off the dataset options",
      '(ds.get("options") or {}).get("tq_base", "side")' in SRC)

# ── back-row exemption ──────────────────────────────────────────────────────
check("qc: a back row is exempt from identity FAILURE",
      'flags["same_person"] = (True if _is_back' in SRC)
check("qc: and it is recorded WHY, not silently",
      'flags["identity_scored_against_front"] = not _is_back' in SRC)
check("qc: the issue line says a back shot is not an identity verdict",
      "not an identity verdict" in SRC)
check("qc: the score is STILL kept (the warm-up wants it)",
      'flags["identity_score"] = None if arc is None else round(arc, 4)' in SRC)
check("likeness route: same exemption, so the two paths cannot disagree",
      'q["same_person"] = True if _is_back else s >= _like.ARC_DIFFERENT' in SRC)

fs = ns["_flag_summary"]({"items": [
    {"id": "1", "qc": {"ok": True, "identity_method": "arcface", "identity_score": 0.12,
                       "identity_scored_against_front": False}},
    {"id": "2", "qc": {"ok": False, "identity_method": "arcface", "identity_score": 0.12,
                       "identity_scored_against_front": True, "same_person": False}},
    {"id": "3", "qc": {"ok": True, "identity_method": "arcface", "identity_score": 0.55,
                       "identity_scored_against_front": True}},
]})
check("summary: a low BACK score counts as back_low_likeness, not identity_off",
      fs["back_low_likeness"] == 1 and fs["identity_off"] == 1, (fs["back_low_likeness"], fs["identity_off"]))
check("summary: a healthy row counts as neither", fs["arcface_scored"] == 3)
print()
print(f"{'ALL PASS' if not fails else str(len(fails)) + ' FAILURES: ' + ', '.join(fails)}")
sys.exit(1 if fails else 0)
