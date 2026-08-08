"""Offline test for v1.217 — dressed vs stripped bases.

Builds a real character folder on disk (base versions + reference images) and
asks _base_for_view what it would pick, per mode. The two bugs this version
fixes were both invisible to a source-level grep, so this exercises the picker.
"""
import ast
import shutil
import sys
import tempfile
from pathlib import Path

K = Path(sys.argv[1] if len(sys.argv) > 1 else "backend/api/klein3.py").read_text("utf-8")
L = Path(sys.argv[2] if len(sys.argv) > 2 else "backend/api/lora.py").read_text("utf-8")

TMP = Path(tempfile.mkdtemp(prefix="k3mode-"))
ns = {"Any": object, "List": list, "Dict": dict, "Optional": object, "Tuple": tuple,
      "Path": Path}
WANT = {"_base_for_view", "_ver_dressed", "_base_mode", "_refs_by_tag"}
CONST = {"VIEW_TAGS", "REF_TAGS", "_BASE_MODES"}
chunks = []
for node in ast.parse(K).body:
    if isinstance(node, ast.FunctionDef) and node.name in WANT:
        node.decorator_list = []
        chunks.append(ast.unparse(node))
    if isinstance(node, ast.Assign) and any(getattr(t, "id", "") in CONST for t in node.targets):
        chunks.append(ast.unparse(node))
# the picker needs two collaborators; stub them onto the temp folder
pre = f'''
from pathlib import Path
_ROOT = Path(r"{TMP}")
def _cdir(slug):
    return _ROOT / slug
def _active_base_path(slug, c):
    a = (c.get("base") or {{}}).get("active")
    if not a:
        return None
    p = _cdir(slug) / "base" / (a + ".png")
    return p if p.exists() else None
'''
exec("from __future__ import annotations\n" + pre + "\n" + "\n\n".join(chunks), ns)

fails = []


def check(label, cond, extra=""):
    print(("  PASS  " if cond else "  FAIL  ") + label + ("" if cond else f"  <- {extra}"))
    if not cond:
        fails.append(label)


bfv, dressed_of = ns["_base_for_view"], ns["_ver_dressed"]

# ── a character on disk ─────────────────────────────────────────────────────
SLUG = "duke"
(TMP / SLUG / "base").mkdir(parents=True)
(TMP / SLUG / "refs").mkdir(parents=True)


def put(kind, name):
    (TMP / SLUG / kind / f"{name}.png").write_bytes(b"\x89PNG")
    return name


CHAR = {"base": {"versions": [], "active": None}, "refs": []}


def ver(vid, **kw):
    put("base", vid)
    CHAR["base"]["versions"].append({"id": vid, "created_at": "t", **kw})


def ref(rid, tag, **kw):
    put("refs", rid)
    CHAR["refs"].append({"id": rid, "tag": tag, "name": rid, **kw})


ref("r-front", "front")
ref("r-left", "left")
ref("r-back", "back", source="generated")
ver("b-front-copy", kind="ref_copy", view="front", source_ref="r-front")
ver("b-front-strip", kind="stripped_underwear", view="front")
ver("b-left-strip", kind="stripped_nude", view="left")
CHAR["base"]["active"] = "b-front-strip"

# ── 1. provenance classification ────────────────────────────────────────────
check("provenance: a stripped version is not dressed",
      dressed_of({"kind": "stripped_underwear"}) is False)
check("provenance: a nude strip is not dressed", dressed_of({"kind": "stripped_nude"}) is False)
check("provenance: a ref copy is dressed", dressed_of({"kind": "ref_copy"}) is True)
check("provenance: an upscale of a strip is NOT dressed",
      dressed_of({"kind": "upscaled", "from_kind": "stripped_nude"}) is False)
check("provenance: an upscale of a ref copy IS dressed",
      dressed_of({"kind": "upscaled", "from_kind": "ref_copy"}) is True)
check("provenance: a PRE-v1.217 upscale is unknown, not guessed",
      dressed_of({"kind": "upscaled"}) is None)

# ── 2. mode resolution ──────────────────────────────────────────────────────
bm = ns["_base_mode"]
check("mode: defaults to auto", bm({}) == "auto")
check("mode: character default is used", bm({"base": {"mode": "dressed"}}) == "dressed")
check("mode: a request override beats the character default",
      bm({"base": {"mode": "dressed"}}, "stripped") == "stripped")
check("mode: junk falls back to the character default",
      bm({"base": {"mode": "dressed"}}, "banana") == "dressed")
check("mode: junk everywhere falls back to auto", bm({}, "banana") == "auto")

# ── 3. the picker, per mode ─────────────────────────────────────────────────
p, lbl = bfv(SLUG, CHAR, "front", "dressed")
check("dressed: front picks the REF COPY, never the strip", p.stem == "b-front-copy", (p, lbl))
check("dressed: and says which source won", "ref_copy" in lbl, lbl)
p, lbl = bfv(SLUG, CHAR, "front", "stripped")
check("stripped: front picks the stripped version", p.stem == "b-front-strip", (p, lbl))

# left has ONLY a stripped base and a dressed reference
p, lbl = bfv(SLUG, CHAR, "left", "dressed")
check("dressed: left falls through the stripped base to the REFERENCE",
      p.stem == "r-left", (p, lbl))
check("dressed: a dressed run never silently uses a nude base", "strip" not in lbl, lbl)
p, lbl = bfv(SLUG, CHAR, "left", "stripped")
check("stripped: left uses its stripped base", p.stem == "b-left-strip", (p, lbl))

# back has NO base version at all — only a generated reference
p, lbl = bfv(SLUG, CHAR, "back", "dressed")
check("dressed: back works off the generated view with no strip run at all",
      p.stem == "r-back", (p, lbl))
check("dressed: the label says the view was generated", "generated" in lbl, lbl)
p, lbl = bfv(SLUG, CHAR, "back", "stripped")
check("stripped: back falls back to the dressed reference…", p.stem == "r-back", (p, lbl))
check("…and LABELS it a fallback rather than implying a strip",
      "dressed fallback" in lbl, lbl)

# auto is the pre-v1.217 behaviour
p_auto, _ = bfv(SLUG, CHAR, "front", "auto")
check("auto: unchanged — newest of that view wins", p_auto.stem == "b-front-strip", p_auto)
check("auto: with no mode argument it is still auto",
      bfv(SLUG, CHAR, "front")[0].stem == "b-front-strip")
check("mode: the character default drives it with no override",
      bfv(SLUG, dict(CHAR, base=dict(CHAR["base"], mode="dressed")), "front")[0].stem
      == "b-front-copy")

# ── 4. never skip a whole tier (the v1.205 lesson) ──────────────────────────
CH2 = {"base": {"versions": [{"id": "gone", "kind": "ref_copy", "view": "front"}],
                "active": None}, "refs": CHAR["refs"]}
p, lbl = bfv(SLUG, CH2, "front", "dressed")
check("missing file: falls through to the reference instead of returning nothing",
      p is not None and p.stem == "r-front", (p, lbl))

CH3 = {"base": {"versions": [{"id": "b-front-copy", "kind": "upscaled", "view": "front"}],
                "active": None}, "refs": CHAR["refs"]}
p, lbl = bfv(SLUG, CH3, "front", "dressed")
check("unknown provenance is USED, not dropped (legacy upscales still work)",
      p.stem == "b-front-copy", (p, lbl))
check("…and is labelled as unknown so nobody infers it",
      "provenance unknown" in lbl, lbl)

check("no view at all -> the active base, labelled",
      bfv(SLUG, CHAR, "", "dressed")[1] == "active base")

# ── 5. the two bug fixes are actually written ───────────────────────────────
check("BUGFIX: a ref copy now records its view",
      '"view": str(r.get("tag") or "").strip().lower(),' in K)
check("BUGFIX: an upscale now records what it came from",
      '"from_kind": str(_src.get("kind") or ""),' in K and '"from_id": _act,' in K)

# ── 6. wiring ───────────────────────────────────────────────────────────────
check("generate accepts base_mode", "base_mode: Optional[str] = None" in K)
check("generate passes it through",
      "_base_for_view(body.slug, c, pose_view, body.base_mode)" in K)
check("generate-set passes it through",
      "_base_for_view(body.slug, c, p_view, body.base_mode)" in K)
check("a route sets the character default", '@router.put("/characters/{slug}/base-mode")' in K)
check("the route previews what each view WOULD resolve to", '"resolves_to": resolved' in K)
check("the character payload exposes the mode", '"base_mode"] = _base_mode(c)' in K)
check("the character payload exposes each view's source", '"base_sources"' in K)

check("lora: the dataset carries a base_mode", '"base_mode": (body.base_mode or None),' in L)
check("lora: renders honour it", 'ang[3],\n                                         ds.get("base_mode"))' in L)
check("lora: QC compares against the SAME source the renders used",
      '_base_for_view(ds["char_slug"], char, "front", ds.get("base_mode"))' in L)

shutil.rmtree(TMP, ignore_errors=True)
print()
print(f"{'ALL PASS' if not fails else str(len(fails)) + ' FAILURES: ' + ', '.join(fails)}")
sys.exit(1 if fails else 0)
