"""Offline test for v1.216 — outfit sets.

The important half is DISTRIBUTION. A wardrobe list that is right and a deal
that clumps produces a LoRA that learned "that outfit means a full body shot" —
the same failure the angle deal had in v1.209.1. So this measures the actual
spread rather than trusting the round-robin.
"""
import ast
import sys
from collections import Counter, defaultdict
from pathlib import Path

SRC = Path(sys.argv[1] if len(sys.argv) > 1 else "backend/api/lora.py").read_text("utf-8")

ns = {"Any": object, "List": list, "Dict": dict, "Optional": object}
WANT = {"_build_plan", "_spread", "_by_key", "_caption", "_render_prompt",
        "_norm_outfits", "_outfit_counts", "_deal_outfits", "_outfit_for",
        "_outfit_short", "_outfit_text", "_suggested_count", "_parse_wardrobe",
        "_clean_garment_desc", "_qc_prompt"}
CONST = {"FRAMINGS", "ANGLES", "EXPRESSIONS", "POSES", "LIGHTING", "BACKGROUNDS",
         "_POSELESS", "_QUALITY", "_BACK_OK", "_ANGLE_MIX", "FRAMING_PRESETS", "_BACK_EVERY",
         "_OUTFIT_VIS", "NAMED_SHARE", "IMAGES_PER_OUTFIT", "_GARMENT_WORDS",
         "_IDENTITY_LINE", "_FRAMING_NOTE"}
chunks = []
for node in ast.parse(SRC).body:
    if isinstance(node, ast.FunctionDef) and node.name in WANT:
        node.decorator_list = []
        chunks.append(ast.unparse(node))
    if isinstance(node, ast.Assign) and any(getattr(t, "id", "") in CONST for t in node.targets):
        chunks.append(ast.unparse(node))
exec("from __future__ import annotations\nimport json\nimport re\n\n" + "\n\n".join(chunks), ns)

fails = []


def check(label, cond, extra=""):
    print(("  PASS  " if cond else "  FAIL  ") + label + ("" if cond else f"  <- {extra}"))
    if not cond:
        fails.append(label)


norm, counts, deal = ns["_norm_outfits"], ns["_outfit_counts"], ns["_deal_outfits"]
plan_of, caption, prompt = ns["_build_plan"], ns["_caption"], ns["_render_prompt"]

WARDROBE = [
    {"name": "Ranger kit", "desc": "a brown leather jacket, a green flannel shirt and dark jeans",
     "kind": "named", "ref_id": "ref-a"},
    {"name": "Town clothes", "desc": "a cream linen shirt and brown corduroy trousers",
     "kind": "named"},
    {"name": "Winter gear", "desc": "a charcoal wool overcoat and black boots", "kind": "named"},
    {"name": "Look 1", "desc": "a navy hoodie and grey joggers", "kind": "variety"},
    {"name": "Look 2", "desc": "a white t-shirt and blue jeans", "kind": "variety"},
    {"name": "Look 3", "desc": "a black suit and a white dress shirt", "kind": "variety"},
    {"name": "Look 4", "desc": "a red rain jacket and khaki trousers", "kind": "variety"},
    {"name": "Look 5", "desc": "a striped rugby shirt and dark shorts", "kind": "variety"},
]
DS = {"id": "duke-v1", "name": "Duke v1", "char_name": "Duke", "trigger": "rbmnduke",
      "class_token": "man", "target": "krea2", "outfit": "", "outfits": WARDROBE}

# ── 1. normalisation + legacy migration ─────────────────────────────────────
o = norm(DS)
check("norm: keeps every outfit", len(o) == 8, len(o))
check("norm: assigns stable ids", [x["id"] for x in o][:3] == ["o1", "o2", "o3"], o[0])
check("norm: keeps the garment ref", o[0]["ref_id"] == "ref-a", o[0])
check("norm: defaults kind to named", norm({"outfits": [{"desc": "a blue coat"}]})[0]["kind"]
      == "named")
check("norm: accepts a bare string entry",
      norm({"outfits": ["a blue coat"]})[0]["desc"] == "a blue coat")
check("norm: drops blank descriptions",
      norm({"outfits": [{"desc": "  "}, {"desc": "a blue coat"}]}) .__len__() == 1)
legacy = norm({"outfit": "a red shirt and jeans"})
check("norm: MIGRATES the pre-v1.216 single string",
      len(legacy) == 1 and legacy[0]["desc"] == "a red shirt and jeans"
      and legacy[0]["kind"] == "named", legacy)
check("norm: a dataset with neither is simply outfit-less", norm({}) == [])

# ── 2. set size scales with the wardrobe ────────────────────────────────────
sz = ns["_suggested_count"]
check("size: 3 named + 5 variety -> ~104", sz(8) == 104, sz(8))
check("size: never below 24 for a single outfit", sz(1) == 24, sz(1))
check("size: capped at 120", sz(40) == 120, sz(40))
check("size: monotonic", all(sz(i) <= sz(i + 1) for i in range(1, 20)))

# ── 3. the 60/40 split is EXACT ─────────────────────────────────────────────
c = counts(104, norm(DS))
check("split: totals exactly the row count", sum(c) == 104, (c, sum(c)))
named_total = sum(c[:3])
check("split: named take 60% (+-1 from rounding)", abs(named_total - 62) <= 2,
      (named_total, c))
check("split: the three named are even", max(c[:3]) - min(c[:3]) <= 1, c[:3])
check("split: the five variety are even", max(c[3:]) - min(c[3:]) <= 1, c[3:])
only_named = counts(40, norm({"outfits": WARDROBE[:3]}))
check("split: with no variety it is an even split of the named",
      sum(only_named) == 40 and max(only_named) - min(only_named) <= 1, only_named)
only_var = counts(40, norm({"outfits": WARDROBE[3:]}))
check("split: with no named it is an even split of the variety",
      sum(only_var) == 40 and max(only_var) - min(only_var) <= 1, only_var)
check("split: an empty wardrobe is an empty split", counts(40, []) == [])

# ── 4. the deal honours the counts AND does not clump ───────────────────────
outs = norm(DS)
seq = deal(104, outs)
check("deal: one outfit per row", len(seq) == 104 and all(seq))
got = Counter(seq)
want = dict(zip([x["id"] for x in outs], counts(104, outs)))
check("deal: every outfit gets exactly its share", dict(got) == want, (dict(got), want))
runs = max(len(list(g)) for g in __import__("itertools").groupby(seq))
check("deal: no outfit ever runs 3+ rows in a row", runs <= 2, runs)
check("deal: an empty wardrobe deals None", deal(10, []) == [None] * 10)
check("deal: zero rows is empty", deal(0, outs) == [])

# ── 5. the real planner — measured, not assumed ─────────────────────────────
p = plan_of(104, {"outfits": WARDROBE})
check("plan: every row carries an outfit", all(r.get("outfit") for r in p), )
check("plan: the plan is the requested size", len(p) == 104)

by_out = defaultdict(Counter)
ang_of = defaultdict(Counter)
for r in p:
    by_out[r["outfit"]][r["framing"]] += 1
    ang_of[r["outfit"]][r["angle"]] += 1
thin = {k: dict(v) for k, v in by_out.items() if len(v) < 3}
check("plan: NO outfit is confined to fewer than 3 of the 4 framings", not thin, thin)
allfour = sum(1 for v in by_out.values() if len(v) == 4)
check("plan: most outfits span all four framings", allfour >= 6, (allfour, len(by_out)))
worst = max((max(v.values()) / sum(v.values()), k) for k, v in ang_of.items())
check("plan: no outfit is dominated by one angle (<50%)", worst[0] < 0.5, worst)
pairs = Counter((r["outfit"], r["angle"]) for r in p)
check("plan: outfit x angle does not lock in step (no pair over 25%)",
      max(pairs.values()) / len(p) < 0.25, pairs.most_common(3))

# a legacy dataset must plan exactly as before
p_legacy = plan_of(40, {})
check("plan: no wardrobe -> outfit is None on every row",
      all(r.get("outfit") is None for r in p_legacy))
check("plan: and the rest of the plan is unchanged in shape",
      len(p_legacy) == 40 and {r["framing"] for r in p_legacy} == {f[0] for f in ns["FRAMINGS"]})

# ── 6. visibility — the rule that stops a false association ─────────────────
text = ns["_outfit_text"]
# back rows drop the trigger by design (v1.213), so pick forward-facing rows —
# otherwise this suite would "prove" the trigger is missing from a shot that is
# correctly missing it.
rows = {fr: next(r for r in p if r["framing"] == fr and r["angle"] != "back")
        for fr in ("face", "headshot", "upper", "full")}
check("visible: a face crop names NO outfit", text(DS, rows["face"]) == "",
      text(DS, rows["face"]))
hs = text(DS, rows["headshot"])
check("visible: a headshot names only the first garment",
      hs and "," not in hs and " and " not in hs, hs)
up = text(DS, rows["upper"])
check("visible: a waist-up names the whole outfit", "," in up or " and " in up, up)
check("visible: a full body names the whole outfit too",
      text(DS, rows["full"]) == _f if (_f := next(o["desc"] for o in outs
                                       if o["id"] == rows["full"]["outfit"])) else False,
      text(DS, rows["full"]))
short = ns["_outfit_short"]
check("short: splits on a comma", short("a red shirt, blue jeans") == "a red shirt")
check("short: splits on 'and'", short("a red shirt and blue jeans") == "a red shirt")
check("short: a single garment survives whole", short("a red shirt") == "a red shirt")

# ── 7. captions ─────────────────────────────────────────────────────────────
cf, ch, cu = (caption(DS, rows[k]) for k in ("face", "headshot", "upper"))
check("caption: a face crop carries no 'wearing'", "wearing" not in cf, cf)
check("caption: a headshot carries a short 'wearing'", "wearing" in ch and ch.count(",") <= 6, ch)
check("caption: a waist-up names the full outfit", "wearing" in cu, cu)
check("caption: the trigger is still there", "rbmnduke man" in cu, cu)
check("caption: a legacy single-outfit dataset still captions it",
      "wearing a red shirt" in caption({"trigger": "t", "class_token": "man",
                                        "outfit": "a red shirt and jeans"},
                                       dict(rows["upper"], outfit="o1")),
      caption({"trigger": "t", "class_token": "man", "outfit": "a red shirt and jeans"},
              dict(rows["upper"], outfit="o1")))

# ── 8. render prompts ───────────────────────────────────────────────────────
pr_face = prompt(DS, rows["face"])
pr_full = prompt(DS, rows["full"])
check("prompt: a face crop asks for no outfit", "wearing" not in pr_face, pr_face[-160:])
check("prompt: a body shot names the garments", "He is wearing" in pr_full, pr_full[-200:])
check("prompt: no garment ref -> no image citation", "shown in image" not in pr_full)
r_a = next(r for r in p if r["outfit"] == "o1" and r["framing"] == "full")
pr_ref = prompt(DS, r_a, 3)
check("prompt: with a ref it NAMES the garments AND cites the image",
      "brown leather jacket" in pr_ref and "shown in image 3" in pr_ref, pr_ref[-220:])
check("prompt: the citation never replaces the naming (Klein ignores category words)",
      "the clothing in image" not in pr_ref and "the outfit in image" not in pr_ref)
check("prompt: still has no negations (cfg=1, no negative conditioning)",
      " not " not in pr_full.lower() and "without" not in pr_full.lower()
      and "avoid" not in pr_full.lower(), pr_full)

# ── 9. wardrobe proposal parsing ────────────────────────────────────────────
pw = ns["_parse_wardrobe"]
GOOD = '''Here you go!
```json
{"character_type": "modern rugged outdoorsman",
 "outfits": [{"name": "Trail", "desc": "a green waxed jacket and brown boots"},
             {"name": "Town", "desc": "a grey sweater and dark jeans"}]}
```'''
w = pw(GOOD, 5)
check("wardrobe: parses JSON out of a fenced, prose-wrapped reply",
      len(w["outfits"]) == 2 and w["character_type"] == "modern rugged outdoorsman", w)
check("wardrobe: every proposal is tagged variety",
      all(x["kind"] == "variety" for x in w["outfits"]))
check("wardrobe: ids do not collide with named ones",
      [x["id"] for x in w["outfits"]] == ["v1", "v2"])
check("wardrobe: respects the requested count",
      len(pw('{"outfits":[{"desc":"a"},{"desc":"b"},{"desc":"c"}]}', 2)["outfits"]) == 2)
check("wardrobe: a bare-string entry still works",
      pw('{"outfits":["a green coat"]}', 3)["outfits"][0]["desc"] == "a green coat")
check("wardrobe: unparseable -> empty, never a crash",
      pw("sorry, I cannot see the image", 5)["outfits"] == [])
check("wardrobe: broken JSON -> empty, never a crash",
      pw('{"outfits": [{"desc": ', 5)["outfits"] == [])

# ── 10. garment naming — the rejection is the point ─────────────────────────
cg = ns["_clean_garment_desc"]
check("garment: strips a chat preamble",
      cg("The person is wearing a red plaid flannel shirt and blue jeans.")
      == "a red plaid flannel shirt and blue jeans",
      cg("The person is wearing a red plaid flannel shirt and blue jeans."))
check("garment: strips quotes and trailing stops",
      cg('"a navy wool coat and black boots."') == "a navy wool coat and black boots")
check("garment: REJECTS a category-only answer (Klein would ignore it)",
      cg("casual clothing") == "", cg("casual clothing"))
check("garment: rejects 'an outfit'", cg("an outfit suitable for winter") == "",
      cg("an outfit suitable for winter"))
check("garment: rejects an empty answer", cg("") == "" and cg("   ") == "")
check("garment: keeps a real one with a garment noun",
      cg("a charcoal wool overcoat, a cream jumper and grey trousers").startswith("a charcoal"))
check("garment: collapses newlines rather than truncating at one",
      "\n" not in cg("a red shirt\nand blue jeans"))

# ── 11. QC ──────────────────────────────────────────────────────────────────
q = ns["_qc_prompt"]
qp = q(rows["upper"], "a navy hoodie and grey joggers")
check("qc: asks about the outfit when one is visible",
      "outfit_ok" in qp and "navy hoodie" in qp, qp[-260:])
check("qc: does NOT ask when the shot cannot show one", "outfit_ok" not in q(rows["face"]))
check("qc: a back shot with a visible outfit still gets asked",
      "outfit_ok" in q(dict(rows["full"], angle="back"), "a navy hoodie"))
check("qc: outfit_ok defaults TRUE when the model omits it",
      'data.get("outfit_ok", True)' in SRC)
check("qc: a wrong outfit fails the image", 'flags.get("outfit_ok", True))' in SRC)
check("summary: counts outfit misses", '"outfit_off"' in SRC
      and 'q.get("outfit_ok") is False' in SRC)

# ── 12. wiring ──────────────────────────────────────────────────────────────
check("jobs: a garment ref is only added when the shot can show it",
      '_OUTFIT_VIS.get(it["framing"], "full") != "none"' in SRC)
check("jobs: the ref index handed to the prompt is 1-based",
      "g_idx = len(refs)" in SRC)
check("jobs: respects Klein's 5-reference ceiling", "len(refs) < 5" in SRC)
for route in ('@router.get("/datasets/{ds_id}/outfits")',
              '@router.put("/datasets/{ds_id}/outfits")',
              '@router.post("/characters/{slug}/wardrobe")',
              '@router.post("/characters/{slug}/refs/{ref_id}/garment")'):
    check(f"route {route.split(chr(34))[1]}", route in SRC)
check("route: changing the wardrobe warns about already-rendered images",
      "are already rendered against the old wardrobe" in SRC)
check("route: the wardrobe proposal is returned for review, not applied",
      "never applied" in SRC)

print()
print(f"{'ALL PASS' if not fails else str(len(fails)) + ' FAILURES: ' + ', '.join(fails)}")
sys.exit(1 if fails else 0)
