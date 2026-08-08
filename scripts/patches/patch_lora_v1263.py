"""v1.263 (lora side) — use the garment clause, and stop overwriting a hand edit.

The caption reuse from v1.261 pasted the whole observed sentence, which names
the background the plan had already named. `garment_clause` keeps only what is
worn (measured on all 40 real descriptions: 40 trimmed, 0 emptied).

It also only replaces a caption_extra that a MACHINE wrote — empty, or exactly
the raw sentence a previous run pasted in. A caption_extra typed by hand is left
alone, which the v1.261 form would have clobbered on the second run.
"""
import sys
from pathlib import Path

P = Path(sys.argv[1] if len(sys.argv) > 1 else "backend/api/lora.py")
src = P.read_text("utf-8")
orig = src


def rep(old, new, why):
    global src
    n = src.count(old)
    assert n == 1, f"{why}: expected 1 match, found {n}"
    src = src.replace(old, new, 1)


rep('''    reused = 0
    for it in targets:
        if not (it.get("caption_extra") or "").strip() and (it.get("seen_clothing") or "").strip():
            it["caption_extra"] = it["seen_clothing"][:200]
            reused += 1''',
    '''    reused = 0
    for it in targets:
        seen = (it.get("seen_clothing") or "").strip()
        if not seen:
            continue
        cur_x = (it.get("caption_extra") or "").strip()
        # Replace only what a machine wrote: empty, the raw sentence a previous
        # run pasted in, or the clause already derived from it. Anything else is
        # a hand edit and outranks this.
        if cur_x and cur_x != seen and cur_x != _ward.garment_clause(seen):
            continue
        worn = _ward.garment_clause(seen)
        if worn:
            it["caption_extra"] = worn[:200]
            reused += 1''',
    "garment clause reuse")

assert src != orig
P.write_text(src, "utf-8")
print(f"patched {P}  ({len(orig)} -> {len(src)} bytes)")
