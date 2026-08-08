"""Offline mock test for v1.207 prompt composition + identity boost (klein3.py).

No worker, no LLM: the composer and the boost picker are extracted from source
and exercised directly, plus source-level assertions on the two generators.
"""
import ast, re, sys, tempfile
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


ns = {"Any": object, "List": list, "Optional": object, "Tuple": tuple, "Path": Path}
exec("from __future__ import annotations\n\n" + "\n\n".join(
    grab(K2, {"_pose_desc", "_clean_pose_desc", "_POSE_STYLE", "_BUILD_STRONG", "_BUILD_ADJACENT", "_SUBJECT_NOUNS", "_SUBJECT_SWAP"})), ns)

tmp = Path(tempfile.mkdtemp())
ns.update({"_cdir": lambda slug: tmp / slug,
           "_refs_by_tag": lambda c, tag: [r for r in c.get("refs", []) if r.get("tag") == tag]})
exec("from __future__ import annotations\n\n" + "\n\n".join(grab(
    K3, {"_compose_prompt", "_body_words", "_identity_boost_path", "_GEN_PROMPT",
         "_POSE_DIAGRAM_NOTE", "_POSE_TEXT_BRIEF", "_POSE_TEXT_FULL", "_POSE_CONTACT", "_BODY_LOCK",
         "_BOOST_NOTE"})), ns)
compose = ns["_compose_prompt"]
body_words = ns["_body_words"]
boost_path = ns["_identity_boost_path"]
LOCK = ns["_BODY_LOCK"]
BRIEF = ns["_POSE_TEXT_BRIEF"]
FULL = ns["_POSE_TEXT_FULL"]
BOOST = ns["_BOOST_NOTE"]
STYLE = ns["_POSE_STYLE"]

fails = []


def check(label, cond, extra=""):
    print(("  PASS  " if cond else "  FAIL  ") + label + ("" if cond else f"  <- {extra}"))
    if not cond:
        fails.append(label)


POSE_WORDS = "standing facing the camera with both hands on the hips, elbows out"
CHAR = {"name": "Duke", "fields": {"body": "very heavy, large belly, thick arms",
                                   "height": "short, about 5 foot 6"}}
POSE = {"id": "p1", "name": "Hands on hips", "source": "generated",
        "prompt": STYLE.format(pose=POSE_WORDS)}
UPLOAD = {"id": "p2", "name": "Skeleton", "source": "upload", "prompt": ""}

# ── composition order ────────────────────────────────────────────────────
p = compose(CHAR, POSE)
check("default: pose words present", POSE_WORDS in p, p[-120:])
check("default: body lock present", LOCK.strip() in p)
check("default: LOCK IS LAST (freshest clause)", p.rstrip().endswith(LOCK.strip()), p[-90:])
check("default: build words present", "very heavy" in p and "5 foot 6" in p)
check("default: brief mode omits the long reconciliation paragraph", FULL.strip() not in p)
check("default: no style words leaked from the pose prompt",
      "mannequin" not in p.lower() and "seamless" not in p.lower())
check("default: no image-3 note when not boosted", BOOST.strip() not in p)

p_full = compose(CHAR, POSE, pose_text="full")
check("full: adds the reconciliation paragraph", FULL.strip() in p_full)
check("full: lock still last", p_full.rstrip().endswith(LOCK.strip()))
check("full: longer than brief", len(p_full) > len(p))

p_off = compose(CHAR, POSE, pose_text="off")
check("off: no pose words", POSE_WORDS not in p_off)
check("off: lock still applied", LOCK.strip() in p_off)

check("unknown mode falls back to brief",
      POSE_WORDS in compose(CHAR, POSE, pose_text="") )

# ── switches ─────────────────────────────────────────────────────────────
p_nolock = compose(CHAR, POSE, body_lock=False)
check("body_lock off: clause gone", LOCK.strip() not in p_nolock)
check("body_lock off: pose words remain", POSE_WORDS in p_nolock)

p_nowords = compose(CHAR, POSE, body_words=False)
check("body_words off: build words gone", "very heavy" not in p_nowords)
check("body_words off: lock remains last", p_nowords.rstrip().endswith(LOCK.strip()))

check("body_words: empty fields produce nothing", body_words({"fields": {}}) == "")
check("body_words: partial fields still work",
      "his build is stocky" in body_words({"fields": {"body": "stocky"}}))

p_extra = compose(CHAR, POSE, extra="wearing a red jacket, rainy street at night")
check("extra: user text included", "red jacket" in p_extra)
check("extra: LOCK STILL LAST (extra cannot outrank the body lock)",
      p_extra.rstrip().endswith(LOCK.strip()), p_extra[-100:])

p_boost = compose(CHAR, POSE, boosted=True)
check("boost: image-3 note added", BOOST.strip() in p_boost)
check("boost: note sits before the pose words",
      p_boost.index(BOOST.strip()) < p_boost.index(POSE_WORDS))

p_up = compose(CHAR, UPLOAD)
check("upload pose: diagram note applied", "pose diagram" in p_up)
check("upload pose without description: no empty 'in words' fragment",
      "The pose, in words:" not in p_up, p_up[-120:])

# ── the lock is AFFIRMATIVE (v1.208): it names what stays, never what to avoid.
# Klein has no negative-prompt node and runs at cfg=1, so a "do NOT be thinner"
# clause only feeds "thinner" to the text encoder.
low = LOCK.lower()
for word in ("belly", "waist", "shoulders", "limb thickness", "stature", "head-to-body"):
    check(f"lock names '{word}' positively", word in low, low)
for word in ("slim", "thinner", "taller", "idealized", "athletic", "do not"):
    check(f"lock is free of the loaded word '{word}'", word not in low, low)

# ── identity boost picker ───────────────────────────────────────────────
slug = "duke"
(tmp / slug / "base").mkdir(parents=True)
(tmp / slug / "refs").mkdir(parents=True)
for f in ("b_front", "b_left"):
    (tmp / slug / "base" / f"{f}.png").write_bytes(b"x")
for f in ("r_face", "r_front"):
    (tmp / slug / "refs" / f"{f}.png").write_bytes(b"x")
char2 = {"base": {"active": "b_front", "versions": [
    {"id": "b_front", "kind": "stripped_underwear", "view": "front"},
    {"id": "b_left", "kind": "stripped_underwear", "view": "left"}]},
    "refs": [{"id": "r_face", "tag": "face"}, {"id": "r_front", "tag": "front"}]}

fp = boost_path(slug, char2, tmp / slug / "base" / "b_left.png")
check("boost: front base chosen as the second identity", fp and fp.name == "b_front.png", fp)

fp = boost_path(slug, char2, tmp / slug / "base" / "b_front.png")
check("boost: never returns the image already in use", fp and fp.name != "b_front.png", fp)
check("boost: falls back to the face ref", fp and fp.name == "r_face.png", fp)

check("boost: nothing available -> None",
      boost_path(slug, {"base": {"versions": []}, "refs": []}, None) is None)

# ── source-level wiring ─────────────────────────────────────────────────
check("generate uses the composer", "_compose_prompt(c, pose," in K3)
check("generate-set uses the composer", "_compose_prompt(c, p," in K3)
check("preview endpoint exists", '@router.post("/preview-prompt")' in K3)
check("preview uses the SAME composer", K3.count("_compose_prompt(") == 4)  # def + 3 callers
check("legacy describe_pose still honoured",
      K3.count('if body.describe_pose else "off"') == 3)
check("3-ref path: boost ref appended to the job refs",
      "ref_identity2.png" in K3 and K3.count("ref_identity2.png") >= 5)
check("brief is the default in the composer + both generators + preview",
      K3.count('pose_text: str = "brief"') == 4, K3.count('pose_text: str = "brief"'))

print()
print(f"{'ALL PASS' if not fails else str(len(fails)) + ' FAILURES: ' + ', '.join(fails)}")
sys.exit(1 if fails else 0)
