"""Offline test for v1.219 — the audit fixes.

Every check here exists because a claim I made was not true of the running code.
The v1.216 suite passed while the feature was inert, because it called
`_build_plan` DIRECTLY with a dict the route never builds. So these tests drive
the ROUTES' own option assembly, not the function in isolation.
"""
import ast
import sys
from collections import defaultdict
from pathlib import Path

SRC_P = Path(sys.argv[1] if len(sys.argv) > 1 else "backend/api/lora.py")
SRC = SRC_P.read_text("utf-8")

ns = {"Any": object, "List": list, "Dict": dict, "Optional": object}
WANT = {"_build_plan", "_spread", "_by_key", "_plan_opts", "_plan_warnings",
        "_norm_outfits", "_outfit_counts", "_deal_outfits", "_outfit_for",
        "_outfit_short", "_outfit_text", "_suggested_count", "_caption", "_flag_summary"}
CONST = {"FRAMINGS", "ANGLES", "EXPRESSIONS", "POSES", "LIGHTING", "BACKGROUNDS",
         "_POSELESS", "_QUALITY", "_BACK_OK", "_ANGLE_MIX", "FRAMING_PRESETS", "_BACK_EVERY",
         "_OUTFIT_VIS", "NAMED_SHARE", "IMAGES_PER_OUTFIT", "MAX_ATTEMPTS"}
chunks = []
for node in ast.parse(SRC).body:
    if isinstance(node, ast.FunctionDef) and node.name in WANT:
        node.decorator_list = []
        chunks.append(ast.unparse(node))
    if isinstance(node, ast.Assign) and any(getattr(t, "id", "") in CONST for t in node.targets):
        chunks.append(ast.unparse(node))
exec("from __future__ import annotations\nimport json, re\n\n" + "\n\n".join(chunks), ns)

fails = []


def check(label, cond, extra=""):
    print(("  PASS  " if cond else "  FAIL  ") + label + ("" if cond else f"  <- {extra}"))
    if not cond:
        fails.append(label)


WARDROBE = [{"name": f"o{i}", "desc": f"a coat number {i} and boots",
             "kind": "named" if i < 3 else "variety"} for i in range(8)]

# ══════════════════════════════════════════════════════════════════════════
# 1. THE BIG ONE — outfits must survive the route's own option assembly
# ══════════════════════════════════════════════════════════════════════════
ds = {"outfits": ns["_norm_outfits"]({"outfits": WARDROBE}),
      "options": {"preset": "balanced"}, "preset": "balanced"}
opts = ns["_plan_opts"](ds)
check("plan_opts: carries the outfits (the bug: it did not)",
      len(opts.get("outfits") or []) == 8, opts.get("outfits"))
check("plan_opts: still carries the preset", opts.get("preset") == "balanced", opts)
check("plan_opts: still carries the other options",
      ns["_plan_opts"]({"options": {"angles": ["front"]}}).get("angles") == ["front"])
check("plan_opts: a preset stored only under options is still found",
      ns["_plan_opts"]({"options": {"preset": "face_heavy"}})["preset"] == "face_heavy")
check("plan_opts: no outfits -> empty list, never None",
      ns["_plan_opts"]({})["outfits"] == [])

plan = ns["_build_plan"](104, opts)
check("ROUTE PATH: every row gets an outfit (this was silently None)",
      all(r.get("outfit") for r in plan),
      sum(1 for r in plan if not r.get("outfit")))
check("ROUTE PATH: all eight outfits appear",
      len({r["outfit"] for r in plan}) == 8, len({r["outfit"] for r in plan}))

# the exact dict the OLD create route built — proof the bug was real
old_style = {**(ds.get("options") or {}), "preset": ds.get("preset")}
old_plan = ns["_build_plan"](104, old_style)
check("REGRESSION PROOF: the pre-v1.219 opts really did produce no outfits",
      all(r.get("outfit") is None for r in old_plan))

# ══════════════════════════════════════════════════════════════════════════
# 2. auto-sizing, and the guarantee it protects
# ══════════════════════════════════════════════════════════════════════════
check("size: 8 outfits suggests 104", ns["_suggested_count"](8) == 104)
check("create: an omitted count is sized from the wardrobe",
      "_suggested_count(len(outfits)) if outfits else 40" in SRC)
check("create: an EXPLICIT count still wins",
      "count = body.count if body.count is not None else" in SRC)
check("create: count is stored on the dataset", 'ds["count"] = count' in SRC)
check("create: DatasetIn.count is optional now", "count: Optional[int] = None" in SRC)
check("replan: a wardrobe change with no count re-sizes",
      "elif body.outfits is not None and ds.get(\"outfits\"):" in SRC)
check("both routes use the single builder — create AND re-plan",
      SRC.count("_build_plan(count, _plan_opts(ds))") == 2, 
      SRC.count("_build_plan(count, _plan_opts(ds))"))
check("no route builds planner options inline any more",
      '_build_plan(body.count, {**' not in SRC and '_build_plan(count, ds.get("options")' not in SRC)

# the measured claim, at both sizes
for count, expect_all_four in ((104, True), (40, False)):
    p = ns["_build_plan"](count, opts)
    by = defaultdict(set)
    for r in p:
        by[r["outfit"]].add(r["framing"])
    allfour = min(len(v) for v in by.values()) == 4
    check(f"MEASURED: at {count} images / 8 outfits, all-four-framings is {expect_all_four}",
          allfour == expect_all_four, {k: len(v) for k, v in by.items()})

w = ns["_plan_warnings"](40, ns["_norm_outfits"]({"outfits": WARDROBE}))
check("warning: 8 outfits over 40 images is called out, not absorbed", len(w) == 1, w)
check("warning: it names the sized count", "104" in w[0], w)
check("warning: it says WHAT goes wrong, not just that it is low",
      "2 of the 4 framings" in w[0], w)
check("warning: a comfortable set warns about nothing",
      ns["_plan_warnings"](104, ns["_norm_outfits"]({"outfits": WARDROBE})) == [])
check("warning: a mid set gets the softer note",
      len(ns["_plan_warnings"](80, ns["_norm_outfits"]({"outfits": WARDROBE}))) == 1)
check("warning: one outfit never warns",
      ns["_plan_warnings"](40, ns["_norm_outfits"]({"outfits": WARDROBE[:1]})) == [])
check("warning: no wardrobe never warns", ns["_plan_warnings"](40, []) == [])
check("routes surface the warnings", SRC.count('out["warnings"] = _plan_warnings(') == 2)

# ══════════════════════════════════════════════════════════════════════════
# 3. "ArcFace ran and found no face" != "ArcFace never ran"
# ══════════════════════════════════════════════════════════════════════════
check("identity_method keys on whether ArcFace RAN",
      'flags["identity_method"] = ("arcface" if baselines' in SRC)
check("…so a no-face row is still marked arcface",
      "it ran; s is None only if no face" in SRC)
fs = ns["_flag_summary"]({"items": [
    {"id": "1", "qc": {"ok": True, "identity_method": "arcface", "identity_score": 0.6}},
    {"id": "2", "qc": {"ok": True, "identity_method": "arcface", "identity_score": None}},
    {"id": "3", "qc": {"ok": True, "identity_method": "vision-llm"}},
]})
check("summary: the no-face counter is REACHABLE now (it was dead code)",
      fs["no_face"] == 1, fs["no_face"])
check("summary: arcface coverage counts both scored and no-face rows",
      fs["arcface_scored"] == 2, fs["arcface_scored"])
check("summary: a vision-llm row counts as neither",
      fs["arcface_scored"] == 2 and fs["no_face"] == 1)

# ══════════════════════════════════════════════════════════════════════════
# 4. nothing else regressed
# ══════════════════════════════════════════════════════════════════════════
p40 = ns["_build_plan"](40, ns["_plan_opts"]({}))
check("no wardrobe: plans exactly as before", len(p40) == 40
      and all(r.get("outfit") is None for r in p40))
check("no wardrobe: framings still all present",
      {r["framing"] for r in p40} == {f[0] for f in ns["FRAMINGS"]})
check("count is clamped at both ends", "max(8, min(int(count), 120))" in SRC
      and "max(8, min(count, 120))" in SRC)

print()
print(f"{'ALL PASS' if not fails else str(len(fails)) + ' FAILURES: ' + ', '.join(fails)}")
sys.exit(1 if fails else 0)
