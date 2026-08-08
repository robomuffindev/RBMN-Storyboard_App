"""Offline mock test for v1.205 DOMINANT ANGLE logic — no FastAPI, no worker.

Extracts _norm_view/_view_from_name (klein2.py) and _base_for_view (klein3.py)
from source and exercises them against a temp character/base layout.
"""
import ast, sys, tempfile
from pathlib import Path

K2 = Path(sys.argv[1] if len(sys.argv) > 1 else "backend/api/klein2.py").read_text("utf-8")
K3 = Path(sys.argv[2] if len(sys.argv) > 2 else "backend/api/klein3.py").read_text("utf-8")


def grab(srctext, names):
    tree = ast.parse(srctext)
    out = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names:
            node.decorator_list = []
            out.append(ast.unparse(node))
        if isinstance(node, ast.Assign) and any(
                getattr(t, "id", "") in names for t in node.targets):
            out.append(ast.unparse(node))
    return out


ns = {"Any": object, "List": list, "Optional": object, "Tuple": tuple, "Path": Path}
exec("from __future__ import annotations\n\n" + "\n\n".join(
    grab(K2, {"_norm_view", "_view_from_name", "POSE_VIEWS", "_VIEW_WORDS"})), ns)
norm_view = ns["_norm_view"]
view_from_name = ns["_view_from_name"]

tmp = Path(tempfile.mkdtemp())
ns3 = {"Any": object, "List": list, "Optional": object, "Tuple": tuple, "Path": Path,
       "VIEW_TAGS": ["front", "back", "left", "right"],
       "_cdir": lambda slug: tmp / slug,
       "_refs_by_tag": lambda c, tag: [r for r in c.get("refs", []) if r.get("tag") == tag],
       "_active_base_path": lambda slug, c: (
           (tmp / slug / "base" / f"{(c.get('base') or {}).get('active')}.png")
           if (c.get("base") or {}).get("active") and
           (tmp / slug / "base" / f"{(c.get('base') or {}).get('active')}.png").exists() else None)}
exec("from __future__ import annotations\n\n" + "\n\n".join(grab(K3, {"_base_for_view", "_base_mode", "_ver_dressed", "_BASE_MODES"})), ns3)
base_for_view = ns3["_base_for_view"]

fails = []


def check(label, cond, extra=""):
    print(("  PASS  " if cond else "  FAIL  ") + label + ("" if cond else f"  <- {extra}"))
    if not cond:
        fails.append(label)


# ── _norm_view: words ─────────────────────────────────────────────────────
for raw, want in [
    ("front", "front"), ("Front", "front"), ("FRONTAL", "front"), ("facing camera", "front"),
    ("back", "back"), ("rear", "back"), ("from behind", "back"), ("posterior", "back"),
    ("left", "left"), ("left side", "left"), ("profile left", "left"), ("L", "left"),
    ("right", "right"), ("right_side", "right"), ("3/4 right", "right"),
    ("three quarter left", "left"), ("turned back left", "left"),
    ("", ""), (None, ""), ("   ", ""),
    ("side", ""), ("profile", ""), ("diagonal", ""), ("banana", ""),
]:
    got = norm_view(raw)
    check(f"norm_view({raw!r}) -> {want!r}", got == want, got)

# ── _norm_view: degrees (front 0 / right +90 / left -90 / back 180) ──────
for raw, want in [
    ("0", "front"), ("30", "front"), ("45", "front"), ("-45", "front"),
    ("90", "right"), ("60", "right"), ("134", "right"),
    ("-90", "left"), ("-120", "left"), ("270", "left"),
    ("180", "back"), ("-180", "back"), ("200", "back"), ("-124", "left"),
]:
    got = norm_view(raw)
    check(f"norm_view({raw}deg) -> {want}", got == want, got)

# the pose from the verified clay-lane result: -124 deg picked the LEFT base
check("verified case: -124 deg -> left", norm_view(-124) == "left", norm_view(-124))

# ── _view_from_name ──────────────────────────────────────────────────────
for raw, want in [
    ("pose_back_03", "back"), ("openpose-left-12", "left"), ("Front view 2", "front"),
    ("dwpose_right_arm_up", "right"), ("hero_landing", ""), ("side_profile", ""),
    ("backflip", ""),          # word-boundary only: 'backflip' is not 'back'
]:
    got = view_from_name(raw)
    check(f"view_from_name({raw!r}) -> {want!r}", got == want, got)

# ── _base_for_view priority ──────────────────────────────────────────────
slug = "duke"
(tmp / slug / "base").mkdir(parents=True)
(tmp / slug / "refs").mkdir(parents=True)
for f in ("v_front", "v_left", "v_left_up", "v_active"):
    (tmp / slug / "base" / f"{f}.png").write_bytes(b"x")
for f in ("r_left", "r_back"):
    (tmp / slug / "refs" / f"{f}.png").write_bytes(b"x")

char = {
    "base": {"active": "v_active", "versions": [
        {"id": "v_front", "kind": "stripped_underwear", "view": "front"},
        {"id": "v_left", "kind": "stripped_underwear", "view": "left"},
        {"id": "v_left_up", "kind": "upscaled", "view": "left"},
        {"id": "v_active", "kind": "upscaled", "view": "front"},
    ]},
    "refs": [{"id": "r_left", "tag": "left"}, {"id": "r_back", "tag": "back"}],
}

fp, lbl = base_for_view(slug, char, "left")
check("base_for_view: upscaled beats stripped for the same view", fp.name == "v_left_up.png", (fp, lbl))
check("base_for_view: label names the source", "left base (upscaled)" == lbl, lbl)

fp, lbl = base_for_view(slug, char, "front")
check("base_for_view: front resolves to a front version", fp.name in ("v_active.png", "v_front.png"), (fp, lbl))

fp, lbl = base_for_view(slug, char, "back")
check("base_for_view: falls back to a TAGGED REF when no base version", fp.name == "r_back.png", (fp, lbl))
check("base_for_view: ref label", lbl == "back reference", lbl)

fp, lbl = base_for_view(slug, char, "right")
check("base_for_view: unknown view -> active base", fp.name == "v_active.png", (fp, lbl))
check("base_for_view: says WHY it fell back", lbl == "active base (no right view yet)", lbl)

fp, lbl = base_for_view(slug, char, "")
check("base_for_view: no view -> active base", fp.name == "v_active.png" and lbl == "active base", (fp, lbl))

fp, lbl = base_for_view(slug, char, "sideways")
check("base_for_view: junk view -> active base", fp.name == "v_active.png", (fp, lbl))

# missing file on disk must not be picked
(tmp / slug / "base" / "v_left_up.png").unlink()
fp, lbl = base_for_view(slug, char, "left")
check("base_for_view: skips versions whose file is gone", fp.name == "v_left.png", (fp, lbl))

# character with nothing at all
empty = {"base": {"active": None, "versions": []}, "refs": []}
fp, lbl = base_for_view(slug, empty, "front")
check("base_for_view: nothing available -> (None,'none')", fp is None and lbl == "none", (fp, lbl))

print()
print(f"{'ALL PASS' if not fails else str(len(fails)) + ' FAILURES: ' + ', '.join(fails)}")
sys.exit(1 if fails else 0)
