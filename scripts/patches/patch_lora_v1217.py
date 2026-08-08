"""v1.217 — the LoRA dataset chooses its identity source too.

A dataset that renders his real wardrobe should start from the DRESSED base:
stripping is an extra Klein edit per view with its own drift, and if the shot
never needed a clothing change that drift bought nothing.
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


rep('''    outfits: Optional[List[dict]] = None   # [{name, desc, kind: named|variety, ref_id}]
    options: Optional[dict] = None   # subset filters for angles/expressions/…''',
    '''    outfits: Optional[List[dict]] = None   # [{name, desc, kind: named|variety, ref_id}]
    base_mode: Optional[str] = None  # auto | dressed | stripped (None = character default)
    options: Optional[dict] = None   # subset filters for angles/expressions/…''',
    "DatasetIn: base_mode")

rep('''        "outfits": _norm_outfits({"outfits": body.outfits, "outfit": body.outfit}),''',
    '''        "outfits": _norm_outfits({"outfits": body.outfits, "outfit": body.outfit}),
        "base_mode": (body.base_mode or None),''',
    "create: store base_mode")

rep('''        base, src_label = _base_for_view(ds["char_slug"], char, ang[3])''',
    '''        # v1.217: a dataset that renders his own wardrobe wants the DRESSED
        # base — stripping is an extra edit per view whose drift buys nothing
        # when the shot never needed the clothing replaced.
        base, src_label = _base_for_view(ds["char_slug"], char, ang[3],
                                         ds.get("base_mode"))''',
    "jobs: honour base_mode")

rep('''        fp, _lbl = _base_for_view(ds["char_slug"], char, "front")''',
    '''        # QC compares against the SAME identity source the renders used, or it
        # would flag his real clothes as "not him" whenever the modes disagree.
        fp, _lbl = _base_for_view(ds["char_slug"], char, "front", ds.get("base_mode"))''',
    "qc ref: honour base_mode")

assert src != orig
P.write_text(src, "utf-8")
print(f"patched {P}  ({len(orig)} -> {len(src)} bytes)")
