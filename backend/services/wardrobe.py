"""Is the person in this picture actually wearing clothes?

v1.260 closed the CONFIGURATION hole that let a training set render off a
stripped base. It explicitly did not close the DETECTION hole: nothing measured
whether a rendered person was dressed, so twelve of dorian-v1's forty images
went into a training run in underwear with captions that never mentioned it.

This module is the vocabulary and the verdict. The looking is done by the vision
model in `lora.py`; everything here is pure text, so it can be tested without a
GPU, a network or a model.

WHY A VISION MODEL IS TRUSTED HERE AND NOT FOR FRAMING
    v1.241 withdrew the model's framing answer after it scored 0 of 12 against
    images verified by eye. Framing is a geometric judgement — "is this a full
    body shot" needs a sense of the frame edges the model does not have.
    Describing visible clothing is a naming task, which is what these models are
    actually good at, and it was measured before it was believed:

        40 images, described TWICE, dorian-v1, qwen2.5vl:7b
        self-agreement (Jaccard over content words): median 0.786, 3 of 40 < 0.40
        12 rows reported bare skin; 8 of 8 confirmed by eye, 0 false positives

    That is a different instrument from the one v1.241 threw out, and it is held
    to the same standard: measured, then used.

RECALL BEATS PRECISION HERE
    Two passes, and a row is BARE if EITHER pass says so. A false positive costs
    one re-render. A false negative costs a training run that teaches the trigger
    word a man in his underwear.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

# Phrases that mean exposed skin where a garment was expected. Matched against a
# lowercased description with word boundaries, so "briefs" does not fire on
# "briefcase" and "nude" does not fire on "denude".
BARE_WORDS: Tuple[str, ...] = (
    "shirtless", "bare-chested", "bare chested", "bare chest", "topless",
    "nude", "naked", "undressed", "unclothed", "bare torso", "bare skin",
    "no shirt", "no clothing", "no clothes", "not wearing", "without a shirt",
    "underwear", "undergarments", "briefs", "boxer shorts", "boxers",
    "boxer briefs", "y-fronts", "loincloth",
)

# Said of a swimming or bathing shot, where a bare chest is the correct
# wardrobe and not a failure. None of these appear in our shot list today; they
# are here so the check does not become a thing people switch off later.
CONTEXT_OK: Tuple[str, ...] = (
    "swimming", "swim trunks", "swimsuit", "swimming trunks", "board shorts",
    "at the beach", "in a pool", "in the pool", "in the shower", "bathing suit",
)

# A description with none of these and none of BARE_WORDS said nothing useful
# about clothing at all — an outcome that must not read as "dressed".
GARMENT_HINTS: Tuple[str, ...] = (
    "shirt", "t-shirt", "tshirt", "tee", "hoodie", "sweater", "sweatshirt",
    "jumper", "jacket", "coat", "blazer", "vest", "waistcoat", "cardigan",
    "top", "blouse", "dress", "robe", "gown", "uniform", "overalls",
    "trousers", "pants", "jeans", "shorts", "skirt", "leggings", "sweatpants",
    "tracksuit", "suit", "tie", "scarf", "hat", "cap", "shoes", "boots",
    "sandals", "sneakers", "socks", "apron", "poncho", "collar", "sleeve",
    "buttoned", "zippered", "flannel", "denim", "wearing",
)


def _hits(text: str, vocab: Sequence[str]) -> List[str]:
    low = " " + re.sub(r"[^a-z0-9 -]+", " ", (text or "").lower()) + " "
    out = []
    for w in vocab:
        # Word-boundary match on the phrase, so substrings never fire.
        if re.search(r"(?<![a-z])" + re.escape(w) + r"(?![a-z])", low):
            out.append(w)
    return out


def read(text: str) -> Dict[str, Any]:
    """One description -> what it says about clothing.

    `bare` is True/False/None: None means the description named no garment and
    no exposure, which is not a pass — it is an unmeasured row."""
    bare = _hits(text, BARE_WORDS)
    ok_ctx = _hits(text, CONTEXT_OK)
    garments = _hits(text, GARMENT_HINTS)
    if bare and not ok_ctx:
        return {"bare": True, "words": bare, "garments": garments,
                "why": f"the description says {', '.join(bare)}"}
    if bare and ok_ctx:
        return {"bare": False, "words": bare, "garments": garments,
                "why": f"bare skin, but {ok_ctx[0]} makes that the right wardrobe"}
    if garments:
        return {"bare": False, "words": [], "garments": garments,
                "why": f"names {len(garments)} garment word(s)"}
    return {"bare": None, "words": [], "garments": [],
            "why": "the description names no clothing at all — nothing measured"}


def verdict(passes: Sequence[str]) -> Dict[str, Any]:
    """Two (or more) descriptions of ONE image -> one answer.

    BARE if any pass says bare: a false positive costs a re-render, a false
    negative costs a training run."""
    reads = [read(p) for p in passes if (p or "").strip()]
    if not reads:
        return {"bare": None, "method": "unmeasured", "words": [],
                "why": "the vision model returned nothing"}
    hot = [r for r in reads if r["bare"] is True]
    if hot:
        return {"bare": True, "method": "vision-%dpass" % len(reads),
                "words": sorted({w for r in hot for w in r["words"]}),
                "agreed": len(hot) == len(reads),
                "why": hot[0]["why"] + ("" if len(hot) == len(reads)
                                        else " (one pass of %d)" % len(reads))}
    cold = [r for r in reads if r["bare"] is False]
    if cold:
        return {"bare": False, "method": "vision-%dpass" % len(reads),
                "words": [], "agreed": len(cold) == len(reads),
                "garments": sorted({g for r in cold for g in r["garments"]})[:8],
                "why": cold[0]["why"]}
    return {"bare": None, "method": "unmeasured", "words": [],
            "why": reads[0]["why"]}


# Clauses that describe the SCENE. Even when a clause also happens to contain a
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


def summarise(rows: Sequence[Optional[Dict[str, Any]]]) -> Dict[str, Any]:
    """Counters for the flag summary. `unmeasured` is reported, never hidden."""
    out = {"bare": 0, "dressed": 0, "unmeasured": 0, "disagreed": 0}
    for r in rows:
        if not r:
            out["unmeasured"] += 1
            continue
        if r.get("bare") is True:
            out["bare"] += 1
        elif r.get("bare") is False:
            out["dressed"] += 1
        else:
            out["unmeasured"] += 1
        if r.get("agreed") is False:
            out["disagreed"] += 1
    return out
