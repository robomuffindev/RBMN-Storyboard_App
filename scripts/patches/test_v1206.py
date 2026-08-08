"""Offline mock test for v1.206 pose-description logic — no LLM, no worker.

Covers the wrapper-stripping (_pose_desc), the vision-reply parser
(_parse_pose_desc) and the prompt assembly in klein3 (that the pose text is
injected, and suppressed when describe_pose is off).
"""
import ast, re, sys
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
        if isinstance(node, ast.Assign) and any(getattr(t, "id", "") in names for t in node.targets):
            out.append(ast.unparse(node))
    return out


ns = {"Any": object, "List": list, "Optional": object, "Tuple": tuple}
exec("from __future__ import annotations\n\n" + "\n\n".join(grab(
    K2, {"_pose_desc", "_clean_pose_desc", "_parse_pose_desc", "_POSE_STYLE", "_norm_view",
         "_VIEW_WORDS", "POSE_VIEWS", "_BUILD_STRONG", "_BUILD_ADJACENT", "_SUBJECT_NOUNS",
         "_SUBJECT_SWAP"})), ns)
pose_desc = ns["_pose_desc"]
parse = ns["_parse_pose_desc"]
STYLE = ns["_POSE_STYLE"]

fails = []


def check(label, cond, extra=""):
    print(("  PASS  " if cond else "  FAIL  ") + label + ("" if cond else f"  <- {extra}"))
    if not cond:
        fails.append(label)


# ── _pose_desc: recover the description from a wrapped prompt ─────────────
POSE = "standing facing the camera with both hands on the hips, elbows out"
wrapped = STYLE.format(pose=POSE)
got = pose_desc({"prompt": wrapped})
check("desc: mannequin wrapper stripped", got == POSE, got)
check("desc: no style words leak", not any(w in got.lower() for w in
      ("mannequin", "background", "studio", "lighting", "gray")), got)

check("desc: stored desc wins over prompt",
      pose_desc({"prompt": wrapped, "desc": "custom words"}) == "custom words")
check("desc: raw prompt passes through",
      pose_desc({"prompt": "arms out wide, feet apart"}) == "arms out wide, feet apart")
check("desc: image-only pose has none", pose_desc({"prompt": "", "source": "upload"}) == "")
check("desc: blank stored desc falls back to prompt",
      pose_desc({"prompt": wrapped, "desc": "   "}) == POSE)
check("desc: partial/odd prompt is not mangled",
      pose_desc({"prompt": "full body studio photograph of a neutral"})
      == "full body studio photograph of a neutral")

# ── _parse_pose_desc: the vision reply ───────────────────────────────────
d, f = parse("POSE: kneeling on the right knee, left hand on the left thigh.\nFACING: right")
check("parse: POSE line", d == "kneeling on the right knee, left hand on the left thigh.", d)
check("parse: FACING normalised", f == "right", f)

d, f = parse("POSE: standing, arms crossed\nFACING: three-quarter left")
check("parse: fuzzy facing -> left", f == "left", f)

d, f = parse("POSE: seated cross-legged\nFACING: side")
check("parse: ambiguous facing -> empty (never guess)", f == "", f)

d, f = parse("The figure stands with both arms raised overhead.")
check("parse: prose without the format still yields a description",
      d == "The figure stands with both arms raised overhead.", d)
check("parse: prose without FACING -> empty", f == "", f)

d, f = parse("")
check("parse: empty reply -> ('','')", (d, f) == ("", ""))

long_reply = "POSE: " + ("word " * 400)
d, _ = parse(long_reply)
check("parse: long reply truncated", len(d) <= 601, len(d))

d, f = parse("pose: lying on the back, knees bent\nfacing: FRONT")
check("parse: case-insensitive labels", d == "lying on the back, knees bent" and f == "front", (d, f))

# ── klein3 prompt assembly (v1.207 split the note into BRIEF/FULL/LOCK) ──
def const(name):
    m = re.search(name + r' = \(\n(.*?)\n\)\n', K3, re.S)
    if m:
        return "".join(re.findall(r'"([^"]*)"', m.group(1)))
    m = re.search(name + r' = "([^"]*)"', K3)
    return m.group(1) if m else ""

BRIEF, FULL, LOCK = const("_POSE_TEXT_BRIEF"), const("_POSE_TEXT_FULL"), const("_BODY_LOCK")
check("brief note has the {desc} slot", "{desc}" in BRIEF, BRIEF)
check("full note lands limbs on HIS landmarks (affirmative since v1.208)",
      "hip bones" in FULL and "own thigh" in FULL, FULL)
check("full note keeps image-2 as the pose authority", "from image 2" in FULL)
check("body lock holds the character's own body (affirmative form)",
      "the body from image 1" in LOCK, LOCK)

gen_src = K3[K3.index("async def generate("):K3.index("class PreviewIn")]
check("generate: pose text gated by the mode",
      '_pose_desc(pose) if mode in ("brief", "full") else ""' in gen_src)
check("generate: prompt built by the shared composer", "_compose_prompt(c, pose," in gen_src)
check("generate: description saved on the record", '"pose_desc": pose_text' in gen_src)

set_src = K3[K3.index("async def generate_set("):]
check("generate-set: per-pose text", '_pose_desc(p) if p_mode in ("brief", "full")' in set_src)
check("generate-set: shared composer", "_compose_prompt(c, p," in set_src)
check("all three request models still default to sending text",
      K3.count("describe_pose: bool = True") == 3, K3.count("describe_pose: bool = True"))
check("diagram note still applied to uploads", "_POSE_DIAGRAM_NOTE" in K3)

print()
print(f"{'ALL PASS' if not fails else str(len(fails)) + ' FAILURES: ' + ', '.join(fails)}")
sys.exit(1 if fails else 0)
