"""Offline mock test for v1.208 — affirmative prompts, build-word scrubbing,
body-matched mannequins.  No worker, no LLM.
"""
import ast, re, sys, tempfile
from pathlib import Path

K2 = Path(sys.argv[1] if len(sys.argv) > 1 else "backend/api/klein2.py").read_text("utf-8")
K3 = Path(sys.argv[2] if len(sys.argv) > 2 else "backend/api/klein3.py").read_text("utf-8")


def grab(srctext, names):
    out = []
    for node in ast.parse(srctext).body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names:
            node.decorator_list = []
            out.append(ast.unparse(node))
        if isinstance(node, ast.Assign) and any(getattr(t, "id", "") in names for t in node.targets):
            out.append(ast.unparse(node))
    return out


ns = {"Any": object, "List": list, "Optional": object, "Tuple": tuple, "Path": Path}
exec("from __future__ import annotations\n\n" + "\n\n".join(grab(
    K2, {"_pose_desc", "_clean_pose_desc", "_POSE_STYLE", "_BUILD_STRONG", "_BUILD_ADJACENT",
         "_SUBJECT_NOUNS", "_SUBJECT_SWAP"})), ns)
clean = ns["_clean_pose_desc"]
STYLE = ns["_POSE_STYLE"]

tmp = Path(tempfile.mkdtemp())
ns.update({"_cdir": lambda slug: tmp / slug,
           "_refs_by_tag": lambda c, tag: [r for r in c.get("refs", []) if r.get("tag") == tag]})
exec("from __future__ import annotations\n\n" + "\n\n".join(grab(
    K3, {"_compose_prompt", "_body_words", "_GEN_PROMPT", "_POSE_DIAGRAM_NOTE",
         "_POSE_TEXT_BRIEF", "_POSE_TEXT_FULL", "_POSE_CONTACT", "_BODY_LOCK", "_BOOST_NOTE",
         "_BODYFIT_NOTE", "_POSEFIT_PROMPT", "_posefit_path"})), ns)
compose = ns["_compose_prompt"]

fails = []


def check(label, cond, extra=""):
    print(("  PASS  " if cond else "  FAIL  ") + label + ("" if cond else f"  <- {extra}"))
    if not cond:
        fails.append(label)


# ── 1. NO NEGATIONS anywhere in the fixed prompt fragments ───────────────
# Klein has no negative-prompt node and runs at cfg=1: a "do NOT" clause only
# feeds the forbidden word to the text encoder.
NEG = re.compile(r"\b(not|never|avoid|ignore|without|nor|n't)\b", re.I)
for name in ("_GEN_PROMPT", "_POSE_TEXT_BRIEF", "_POSE_TEXT_FULL", "_BODY_LOCK",
             "_BOOST_NOTE", "_BODYFIT_NOTE", "_POSE_DIAGRAM_NOTE"):
    txt = ns[name]
    hit = NEG.search(txt)
    check(f"{name} is negation-free", hit is None, hit.group(0) if hit else "")

check("_POSEFIT_PROMPT is negation-free (except the mannequin styling)",
      not re.search(r"\b(not|never|avoid|ignore)\b", ns["_POSEFIT_PROMPT"], re.I),
      ns["_POSEFIT_PROMPT"][:80])

# ── 2. image 2's body is NAMED, not hidden behind category words ────────
g = ns["_GEN_PROMPT"].lower()
for word in ("build", "weight", "height", "limb thickness", "proportions"):
    check(f"opener names image 2's '{word}'", word in g)
check("opener avoids the loaded word 'slimness' (cfg=1: any mention is conditioning)",
      "slim" not in g, g)
check("opener says image 2 supplies the POSE only", "pose only" in g)
check("opener names joint angles", "joint angles" in g)
check("old category-word exclusion is gone",
      "appearance, material, style" not in g)

# ── 3. build words are scrubbed from the description ────────────────────
cases = [
    ("a slim gray mannequin standing with both hands on the hips, elbows out",
     ["slim", "mannequin"], ["hands on the hips", "elbows out"]),
    ("The athletic figure stands with feet apart, arms crossed over the chest",
     ["athletic"], ["feet apart", "arms crossed"]),
    ("a tall thin man kneeling on the right knee, left hand on the left thigh",
     ["tall", "thin"], ["kneeling on the right knee", "left thigh"]),
    ("a muscular, well-proportioned male figure leaning back on both hands",
     ["muscular", "well-proportioned"], ["leaning back on both hands"]),
]
for txt, gone, kept in cases:
    out = clean(txt)
    for w in gone:
        check(f"scrub removes '{w}'", w.lower() not in out.lower(), out)
    for k in kept:
        check(f"scrub keeps '{k}'", k in out, out)

untouched = "standing with both hands on the hips, elbows out"
check("scrub leaves a clean description byte-identical", clean(untouched) == untouched, clean(untouched))
check("scrub keeps pose-relevant 'weight on the front foot'",
      "weight on the front foot" in clean("crouching low, weight on the front foot"))
check("scrub handles empty input", clean("") == "" and clean(None) == "")

# ── 4. the composed prompt carries no build words from the pose ─────────
CHAR = {"fields": {"body": "very heavy, large belly, thick arms", "height": "short, about 5 foot 6"}}
DIRTY = {"source": "generated",
         "prompt": STYLE.format(pose="a slim athletic mannequin standing with hands on the hips")}
p = compose(CHAR, DIRTY)
seg = p[p.index("The pose, in words:"):p.index("Remember his physique")]
check("pose-text segment drops 'slim'", "slim" not in seg.lower(), seg)
check("pose-text segment drops 'athletic'", "athletic" not in seg.lower(), seg)
check("no build-valence words anywhere in the prompt",
      not any(w in p.lower() for w in ("slim", "athletic", "muscular", "thinner", "idealized")), p[:200])
check("composed prompt keeps HIS build words", "very heavy" in p)
check("composed prompt has no do-NOT guards", NEG.search(p.replace("Image 2's", "")) is None,
      (NEG.search(p) or [""])[0] if NEG.search(p) else "")

# ── 5. body-matched mannequin ──────────────────────────────────────────
pf = ns["_POSEFIT_PROMPT"].lower()
check("posefit: mannequin is image 1, person is image 2",
      "image 1 is a plain gray mannequin" in pf and "image 2 shows a real person" in pf)
for w in ("weight", "belly", "waist", "hips", "limb thickness", "stature"):
    check(f"posefit names '{w}'", w in pf)
check("posefit holds the pose", "the pose stays exactly as it is in image 1" in pf)
check("posefit keeps it a mannequin", "gray 3d mannequin" in pf)
check("posefit keeps the framing", "camera framing" in pf)

p_fit = compose(CHAR, DIRTY, bodyfit=True)
check("bodyfit note added when the fitted mannequin is used",
      ns["_BODYFIT_NOTE"].strip() in p_fit)
check("bodyfit note absent otherwise", ns["_BODYFIT_NOTE"].strip() not in p)
# v1.208.1 — contact beats geometry (his hand landed on the belly, not the hip)
check("contact clause ships in BRIEF mode too", ns["_POSE_CONTACT"].strip() in p, p[-300:])
check("contact clause lets the arm angle adapt", "the arm angle follows it" in ns["_POSE_CONTACT"])
check("contact clause names the hip bones + pelvis",
      "hip bones" in ns["_POSE_CONTACT"] and "pelvis" in ns["_POSE_CONTACT"])
check("contact clause is negation-free", NEG.search(ns["_POSE_CONTACT"]) is None)
check("body lock still terminal with bodyfit on",
      p_fit.rstrip().endswith(ns["_BODY_LOCK"].strip()))

check("posefit path is per character + pose",
      str(ns["_posefit_path"]("duke", "p1")).endswith("duke/posefit/p1.png".replace("/", __import__("os").sep)))

# ── 6. wiring ──────────────────────────────────────────────────────────
check("posefit route exists", '@router.post("/characters/{slug}/posefit")' in K3)
check("posefit image route exists", '/posefit/{pose_id}/image' in K3)
check("posefit list route exists", '@router.get("/characters/{slug}/posefit")' in K3)
check("posefit fans across workers", "_parallel_klein_edits(disp, jobs, on_result, st)" in K3)
check("posefit skips already-fitted poses unless overwrite", "if not body.overwrite:" in K3)
check("posefit caps a run at 40", "targets = targets[:40]" in K3)
check("generate can use the fitted mannequin",
      'use_fit = body.pose_source == "bodyfit" and fitted.exists()' in K3)
check("generate-set can use it too", "p_use_fit = body.pose_source" in K3)
check("pose_source recorded on both gens + preview",
      K3.count('"pose_source": "bodyfit" if') == 3, K3.count('"pose_source": "bodyfit" if'))
check("preview reports the cleaned description", '"pose_desc_clean"' in K3)
check("all three models default to the library pose",
      K3.count('pose_source: str = "library"') == 3, K3.count('pose_source: str = "library"'))
check("bodyfit falls back silently when no fitted image exists",
      "and fitted.exists()" in K3 and "and p_fit.exists()" in K3)

print()
print(f"{'ALL PASS' if not fails else str(len(fails)) + ' FAILURES: ' + ', '.join(fails)}")
sys.exit(1 if fails else 0)
