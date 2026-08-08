"""v1.263 — the caption said the park twice.

The wardrobe pass answers TWO questions in one sentence, because that is the
prompt v1.219 wrote for enrichment: clothing AND background. The template
caption already names the background from the plan, so reusing the whole
sentence produced:

    "...in front of a park, flat overcast light, a light gray t-shirt,
     green grassy park with trees in background."

The park is in there twice, in two different wordings. Whatever a caption names
becomes a knob the trainer can turn, and naming the same thing twice in two
vocabularies is the sloppiest possible way to turn it.

`garment_clause` keeps only the clauses that name something worn. Everything
else — the background, the light, the "behind him" — is dropped, because the
plan already said it and said it consistently.
"""
import sys
from pathlib import Path

P = Path(sys.argv[1] if len(sys.argv) > 1 else "backend/services/wardrobe.py")
src = P.read_text("utf-8")
orig = src


def rep(old, new, why):
    global src
    n = src.count(old)
    assert n == 1, f"{why}: expected 1 match, found {n}"
    src = src.replace(old, new, 1)


rep('''def summarise(rows''',
    '''# Clauses that describe the SCENE. Even when a clause also happens to contain a
# garment word ("a man in a red shirt against a brick wall"), a clause led by one
# of these is the background talking.
_SCENE_LEAD = ("background", "backdrop", "behind", "in the back", "wall behind",
               "visible in", "setting", "scene")


def garment_clause(text: str) -> str:
    """Keep only what is WORN.

    v1.263. The description answers two questions in one sentence and the plan
    already owns one of them, so reusing the whole thing named the background
    twice in two vocabularies. Splitting on `;` and `,` is crude and correct
    here: these sentences are comma-separated lists by construction — the prompt
    asks for exactly that."""
    import re as _re
    out = []
    for clause in _re.split(r"[;,]", text or ""):
        c = clause.strip()
        if not c:
            continue
        low = " " + c.lower() + " "
        if any(w in low for w in _SCENE_LEAD):
            continue
        if _hits(c, GARMENT_HINTS) or _hits(c, BARE_WORDS):
            out.append(c)
    return ", ".join(out)


def summarise(rows''',
    "garment_clause")

assert src != orig
P.write_text(src, "utf-8")
print(f"patched {P}  ({len(orig)} -> {len(src)} bytes)")
