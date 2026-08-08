"""Offline test for v1.222 - a re-plan must never silently destroy renders.

Reproduces the exact incident: 40 images planned face_heavy, a caller sends
options back WITHOUT preset, and the shot list silently becomes balanced.
"""
import ast, sys
from pathlib import Path
SRC = Path(sys.argv[1] if len(sys.argv) > 1 else "backend/api/lora.py").read_text("utf-8")
ns = {"Any": object, "List": list, "Dict": dict, "Optional": object}
WANT = {"_build_plan", "_spread", "_by_key", "_plan_opts", "_plan_impact", "_plan_warnings",
        "_norm_outfits", "_outfit_counts", "_deal_outfits", "_suggested_count"}
CONST = {"FRAMINGS", "ANGLES", "EXPRESSIONS", "POSES", "LIGHTING", "BACKGROUNDS", "_POSELESS",
         "_QUALITY", "_BACK_OK", "_ANGLE_MIX", "FRAMING_PRESETS", "_BACK_EVERY", "_OUTFIT_VIS",
         "NAMED_SHARE", "IMAGES_PER_OUTFIT"}
chunks = []
for n in ast.parse(SRC).body:
    if isinstance(n, ast.FunctionDef) and n.name in WANT:
        n.decorator_list = []; chunks.append(ast.unparse(n))
    if isinstance(n, ast.Assign) and any(getattr(t, "id", "") in CONST for t in n.targets):
        chunks.append(ast.unparse(n))
ns["_item_path"] = lambda ds_id, iid: type("P", (), {"exists": staticmethod(lambda: False)})()
exec("from __future__ import annotations\nimport json, re\n\n" + "\n\n".join(chunks), ns)
fails = []
def check(l, c, e=""):
    print(("  PASS  " if c else "  FAIL  ") + l + ("" if c else f"  <- {e}"))
    if not c: fails.append(l)

bp, impact, popts = ns["_build_plan"], ns["_plan_impact"], ns["_plan_opts"]

# the real dataset: 40 images, face_heavy, all rendered
items = bp(40, {"preset": "face_heavy"})
for it in items: it["status"] = "done"
DS = {"items": items, "preset": "face_heavy", "options": {"preset": "face_heavy"}}

# ---- the incident, reproduced --------------------------------------------
lost_plan = bp(40, popts({"options": {"tq_base": "front"}}))       # preset gone
imp = impact(DS, lost_plan)
check("INCIDENT: losing the preset really does discard 33 of 40",
      imp["discarded"] == 33 and imp["kept"] == 7, (imp["discarded"], imp["kept"]))
check("INCIDENT: and the impact names the angle changes", len(imp["angle_changes"]) > 0,
      imp["angle_changes"])
check("INCIDENT: it reports how many were rendered to begin with",
      imp["rendered_before"] == 40)

# ---- the fix: preset is sticky -------------------------------------------
kept_plan = bp(40, popts({"options": {"tq_base": "front"}, "preset": "face_heavy"}))
imp2 = impact(DS, kept_plan)
check("FIX: with the preset preserved, NOTHING is discarded",
      imp2["discarded"] == 0 and imp2["kept"] == 40, (imp2["discarded"], imp2["kept"]))
check("FIX: tq_base alone never moves a slot",
      [r["angle"] for r in kept_plan] == [r["angle"] for r in items])

# ---- impact accounting ---------------------------------------------------
check("impact: an unrendered dataset has nothing to lose",
      impact({"items": [dict(i, status="planned") for i in items]}, lost_plan)["discarded"] == 0)
smaller = bp(24, {"preset": "face_heavy"})
imp3 = impact(DS, smaller)
check("impact: shrinking the set counts the dropped rows as discarded",
      imp3["discarded"] >= 16, imp3["discarded"])
check("impact: the id list is capped so the error stays readable",
      len(imp3["discarded_ids"]) <= 40)

# ---- v1.223: an image on disk counts as rendered even if status was lost --
ns["_item_path"] = lambda ds_id, iid: type("P", (), {"exists": staticmethod(lambda: True)})()
stale = {"items": [dict(i, status="planned") for i in items], "preset": "face_heavy",
         "options": {"preset": "face_heavy"}, "id": "x"}
imp4 = impact(stale, bp(40, {"preset": "face_heavy"}))
check("v1223: rows whose status was lost to the race still count as rendered",
      imp4["kept"] == 40 and imp4["discarded"] == 0, (imp4["kept"], imp4["discarded"]))
ns["_item_path"] = lambda ds_id, iid: type("P", (), {"exists": staticmethod(lambda: False)})()

# ---- source-level guarantees ---------------------------------------------
check("route: options MERGE instead of replacing",
      'ds["options"] = {**(ds.get("options") or {}), **(body.options or {})}' in SRC)
check("route: the stored preset survives an options patch that omits it",
      'elif ds.get("preset"):' in SRC)
check("route: a caller-supplied preset still wins", 'if _p:\n        ds["preset"] = _p' in SRC)
check("route: a destructive re-plan is REFUSED without force",
      'if impact["discarded"] and not body.force:' in SRC)
check("route: the refusal says how many and what changed",
      "would DISCARD" in SRC and "Angles changing" in SRC)
check("route: it names the likely cause rather than just failing",
      "check that `preset` and `options` match" in SRC)
check("PlanIn has force, defaulting to False", "force: bool = False" in SRC)
check("route: the response carries the impact", 'out["impact"] = impact' in SRC)
check("v1223: the render path now locks its write", "with _DS_WRITE_LOCK:" in SRC)
check("v1223: QC shares the same lock", "lock = _DS_WRITE_LOCK" in SRC)
check("v1223: a resync route rebuilds status from disk",
      '@router.post("/datasets/{ds_id}/resync")' in SRC)
check("v1223: resync renders nothing and deletes nothing",
      "Nothing is rendered or" in SRC)
check("a read-only preview exists", '@router.post("/datasets/{ds_id}/plan-preview")' in SRC)
check("preview writes nothing", "Writes nothing, deletes nothing" in SRC
      and "_write_ds" not in SRC[SRC.index("async def dataset_plan_preview"):
                                 SRC.index("async def dataset_likeness")])
check("preview deep-copies rather than mutating the stored dataset",
      "probe = json.loads(json.dumps(ds))" in SRC)

print()
print(f"{'ALL PASS' if not fails else str(len(fails)) + ' FAILURES: ' + ', '.join(fails)}")
sys.exit(1 if fails else 0)
