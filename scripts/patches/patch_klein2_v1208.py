"""v1.208.0 — keep BUILD words out of the pose description (klein2.py).

The pose text we inject can itself carry body-shape words ("a slim figure
standing…", "the mannequin…"), which pull the render toward that build.  The
description is now scrubbed of physique words before it is used, and the vision
describe pass is told never to produce them in the first place.

Exact-match patcher: every replacement asserts exactly ONE occurrence.
Usage: python patch_klein2_v1208.py <path-to-klein2.py>
"""
import sys
from pathlib import Path

p = Path(sys.argv[1])
src = p.read_text("utf-8")
orig = src


def rep(old: str, new: str, label: str) -> None:
    global src
    n = src.count(old)
    assert n == 1, f"{label}: expected 1 occurrence, found {n}"
    src = src.replace(old, new)
    print(f"  ok  {label}")


# ── 1. the scrubber ───────────────────────────────────────────────────────
rep(
    '''POSE_DESC_SYSTEM = (''',
    '''# Physique words that must never ride along with a POSE description — they
# describe a BODY, and the body must come from the character reference alone.
# STRONG ones are removed anywhere; the rest only when they sit right in front
# of a subject noun ("a thin figure" -> "a figure", but "a thin gap" survives).
_BUILD_STRONG = ("slim", "slender", "skinny", "athletic", "muscular", "lean", "toned",
                 "buff", "ripped", "chiseled", "overweight", "chubby", "stocky",
                 "well-proportioned", "well proportioned", "average build", "curvy",
                 "petite", "lanky", "statuesque", "svelte", "shapely")
_BUILD_ADJACENT = ("thin", "tall", "short", "big", "small", "large", "heavy", "thick",
                   "broad", "narrow", "young", "old", "male", "female")
_SUBJECT_NOUNS = ("figure", "man", "woman", "person", "mannequin", "dummy", "model",
                  "body", "male", "female", "subject", "frame", "physique", "build",
                  "silhouette", "character")
_SUBJECT_SWAP = {"mannequin": "person", "dummy": "person", "statue": "person",
                 "stick figure": "figure", "skeleton": "figure"}


def _clean_pose_desc(text: str) -> str:
    """Strip BUILD words out of a pose description (v1.208).

    A pose description should say where the limbs are, nothing about the shape
    of the body carrying them.  Returns the cleaned text — the stored
    description is left untouched, so the editor still shows what was written."""
    import re as _re
    s = str(text or "").strip()
    if not s:
        return ""
    for word, into in _SUBJECT_SWAP.items():
        s = _re.sub(rf"\\b{_re.escape(word)}\\b", into, s, flags=_re.I)
    for w in _BUILD_STRONG:
        s = _re.sub(rf"\\b{_re.escape(w)}\\b,?\\s*", "", s, flags=_re.I)
    nouns = "|".join(_SUBJECT_NOUNS)
    for w in _BUILD_ADJACENT:
        s = _re.sub(rf"\\b{_re.escape(w)}\\s+(?=({nouns})\\b)", "", s, flags=_re.I)
    s = _re.sub(r"\\s{2,}", " ", s)
    s = _re.sub(r"\\s+([,.;])", r"\\1", s)
    s = _re.sub(r"(^|[.;]\\s*)(a|an|the)\\s+([,.;])", r"\\1", s, flags=_re.I)
    s = _re.sub(r",\\s*,", ",", s)
    return s.strip(" ,;").strip()


POSE_DESC_SYSTEM = (''',
    "_clean_pose_desc",
)

# ── 2. the vision pass must not produce build words either ───────────────
rep(
    '''thigh, the ground, a wall). Do not mention identity, sex, clothing, colours,
materials, background, lighting, camera or art style.>''',
    '''thigh, the ground, a wall). Write only about POSITION. Words about the body's
SHAPE are forbidden: never write slim, slender, thin, athletic, muscular, lean,
heavy, tall, short, big, small, young, old, male, female, or any other word
about build, weight, height, age or sex. Never mention identity, clothing,
colours, materials, background, lighting, camera or art style.>''',
    "describe prompt forbids build words",
)

# ── 3. scrub what the vision model returns, before it is stored ──────────
rep(
    '''    desc = desc.strip().strip('"').strip()
    if len(desc) > 600:''',
    '''    desc = _clean_pose_desc(desc.strip().strip('"'))
    if len(desc) > 600:''',
    "scrub the stored LLM description",
)

assert src != orig
p.write_text(src, "utf-8")
print(f"patched {p} ({len(orig)} -> {len(src)} bytes)")
