
# ══ outfits ══════════════════════════════════════════════════════════════════
# A character LoRA trained on ONE outfit fuses the clothes into the trigger --
# the module docstring has warned about this since v1.209 ("all-bikini dataset
# -> every render is a bikini") while the code shipped a single `outfit` string
# for the whole set.  v1.216 makes outfits a SET.
#
# Two kinds, doing two different jobs:
#   named    -- his actual story wardrobe.  What the LoRA has to render well.
#   variety  -- looks that exist purely so clothing stays DETACHABLE from
#               identity.  Without them even three outfits can fuse, because
#               nothing in the data demonstrates that clothing is independent
#               of the person.
NAMED_SHARE = 0.60               # of the image budget; the rest goes to variety
IMAGES_PER_OUTFIT = 13           # what the auto-sized set allows each outfit

# How much of an outfit a shot can actually SHOW.  Same rule that fixed the
# back-view expression bug: never let a caption or a prompt name something the
# image cannot contain.  An extreme close-up of a face shows no clothing at all,
# so naming one there teaches a false association.
_OUTFIT_VIS = {"face": "none", "headshot": "short", "upper": "full", "full": "full"}


def _suggested_count(n_outfits: int) -> int:
    """Set size scales with the wardrobe -- splitting a fixed 40 across eight
    outfits leaves five images each, which is too thin for any of them to hold."""
    return max(24, min(int(round(max(1, n_outfits) * IMAGES_PER_OUTFIT)), 120))


def _norm_outfits(ds: dict) -> List[dict]:
    """The outfit list, migrating the pre-v1.216 single string.  A dataset built
    before this still plans and captions exactly as it did."""
    raw = ds.get("outfits")
    if raw is None:
        legacy = (ds.get("outfit") or "").strip()
        raw = [{"id": "o1", "name": "outfit", "desc": legacy, "kind": "named"}] if legacy else []
    out: List[dict] = []
    for i, o in enumerate(raw or []):
        if isinstance(o, str):
            o = {"desc": o}
        desc = str(o.get("desc") or "").strip().rstrip(".")
        if not desc:
            continue
        out.append({"id": str(o.get("id") or f"o{i + 1}"),
                    "name": str(o.get("name") or "").strip() or f"outfit {i + 1}",
                    "desc": desc,
                    "kind": "variety" if o.get("kind") == "variety" else "named",
                    "ref_id": (str(o.get("ref_id")).strip() or None) if o.get("ref_id") else None})
    return out


def _outfit_counts(n_rows: int, outfits: List[dict]) -> List[int]:
    """60/40 named/variety by default, largest-remainder so the total is EXACT.
    With only one kind present it degrades to an even split of that kind."""
    if not outfits:
        return []
    named = [i for i, o in enumerate(outfits) if o["kind"] == "named"]
    variety = [i for i, o in enumerate(outfits) if o["kind"] == "variety"]
    share = [0.0] * len(outfits)
    if named and variety:
        for i in named:
            share[i] = NAMED_SHARE / len(named)
        for i in variety:
            share[i] = (1.0 - NAMED_SHARE) / len(variety)
    else:
        for i in range(len(outfits)):
            share[i] = 1.0 / len(outfits)
    raw = [n_rows * s for s in share]
    cnt = [int(x) for x in raw]
    left = n_rows - sum(cnt)
    for i in sorted(range(len(raw)), key=lambda k: -(raw[k] - int(raw[k])))[:left]:
        cnt[i] += 1
    return cnt


def _deal_outfits(rows, outfits: List[dict]) -> List[Optional[str]]:
    """Spread each outfit across the shot list, honouring its exact share.

    `rows` is the grouping key of every planned row — the planner passes
    (framing, angle) — or a bare count when there is nothing to group by.

    Two measured failures shaped this, both caught by the offline suite rather
    than by a training run:

    1. A plain round-robin down `rows` clumped by FRAMING. `rows` is built
       grouped (every face row, then every headshot, ...), so the variety
       outfits ran out part-way through the waist-up block and five of eight
       never got a single full-body shot — a LoRA that learns "the navy hoodie
       means a waist-up photograph".
    2. Allocating per framing group fixed that but clumped by ANGLE instead: an
       outfit lands every len(outfits) rows while the angle rotates every
       len(_ANGLE_MIX) rows, and those share a factor. One outfit came out 67%
       a single angle — trained as "the red rain jacket, seen from the left".

    So the fill is greedy over (framing, angle) CELLS, rarest cell first, and
    each slot goes to whichever outfit is furthest behind on that angle, then on
    that framing.  Rarest-first matters: proportional allocation quietly hands
    small outfits their images out of the BIG cells, because that is where the
    slots are — so the rare angles end up belonging to the outfits with the most
    images.  Filling the scarce cells while every outfit still has budget is
    what stops that."""
    rows = ["_"] * rows if isinstance(rows, int) else list(rows)
    n = len(rows)
    if not outfits or n <= 0:
        return [None] * max(0, n)
    ids = [o["id"] for o in outfits]
    remaining = dict(zip(ids, _outfit_counts(n, outfits)))

    groups: Dict[Any, List[int]] = {}
    for i, key in enumerate(rows):
        groups.setdefault(key, []).append(i)

    have_ang: Dict[str, Dict[Any, int]] = {i: {} for i in ids}
    have_fr: Dict[str, Dict[Any, int]] = {i: {} for i in ids}
    seq: List[Optional[str]] = [None] * n
    # rarest cell first — see the docstring; this is the half that fixes angles
    for key, idxs in sorted(groups.items(), key=lambda kv: (len(kv[1]), str(kv[0]))):
        fr, ang = key if isinstance(key, tuple) else (key, None)
        for i_row in idxs:
            pick = max(ids, key=lambda o: (remaining[o] > 0,
                                           -have_ang[o].get(ang, 0),
                                           -have_fr[o].get(fr, 0),
                                           remaining[o],
                                           ids.index(o) * -1))
            seq[i_row] = pick
            remaining[pick] -= 1
            have_ang[pick][ang] = have_ang[pick].get(ang, 0) + 1
            have_fr[pick][fr] = have_fr[pick].get(fr, 0) + 1
    return seq


def _outfit_for(ds: dict, item: dict) -> Optional[dict]:
    outs = _norm_outfits(ds)
    if not outs:
        return None
    oid = item.get("outfit")
    if not oid:
        # A row PLANNED before v1.216 carries no outfit id, and back then the
        # single `outfit` string applied to every row.  Without this a legacy
        # dataset silently drops its outfit from every caption and prompt --
        # caught by the v1.209 suite, not by this version's own tests, which is
        # the entire argument for keeping the old ones runnable.
        return outs[0] if len(outs) == 1 else None
    return next((o for o in outs if o["id"] == oid), None)


def _outfit_short(desc: str) -> str:
    """The first named garment only -- what a head-and-shoulders shot can show.
    Splits on the same separators a wardrobe description uses."""
    for sep in (",", " and ", " with ", " over "):
        if sep in desc:
            return desc.split(sep)[0].strip()
    return desc.strip()


def _outfit_text(ds: dict, item: dict) -> str:
    """The outfit phrase for THIS shot, or '' when the shot cannot show one."""
    o = _outfit_for(ds, item)
    if not o:
        return ""
    vis = _OUTFIT_VIS.get(item.get("framing") or "", "full")
    if vis == "none":
        return ""
    return _outfit_short(o["desc"]) if vis == "short" else o["desc"]


_WARDROBE_SYSTEM = (
    "You are a costume designer preparing a photo shoot. You answer with JSON only.")

_WARDROBE_PROMPT = """Look at this person and propose a wardrobe for a photo shoot.

First decide what KIND of character this is from what you can see — their apparent era,
setting and register (modern casual, business, outdoor/rugged, athletic, fantasy, historical,
uniformed, and so on). Then propose {n} outfits that a person of that kind would plausibly
wear, all clearly different from each other and from what they have on now.

Answer with JSON only, exactly these keys:
{{"character_type": "a short phrase",
  "outfits": [{{"name": "two or three words", "desc": "the garments, named"}}, ...]}}

RULES for "desc", these matter:
* NAME every garment and its colour — "a charcoal wool overcoat, a cream cable-knit jumper
  and dark grey trousers". Category words alone ("warm clothes", "casual wear", "an outfit")
  are useless to the image model and will be ignored.
* Describe CLOTHING only. Say nothing about their face, hair, body, build or age.
* Each outfit must be head-to-toe: top, bottom, and footwear where it would show.
* No accessories that obscure the face (no masks, no full helmets, no heavy sunglasses)."""


def _parse_wardrobe(raw: str, n: int) -> dict:
    """Tolerant parse — a vision model wrapping JSON in prose is the norm."""
    txt = (raw or "").strip()
    if "```" in txt:
        txt = txt.split("```")[1].lstrip("json").strip() if txt.count("```") >= 2 else txt
    i, j = txt.find("{"), txt.rfind("}")
    if i < 0 or j <= i:
        return {"character_type": "", "outfits": []}
    try:
        data = json.loads(txt[i:j + 1])
    except (json.JSONDecodeError, ValueError):
        return {"character_type": "", "outfits": []}
    outs = []
    for k, o in enumerate(data.get("outfits") or []):
        if isinstance(o, str):
            o = {"desc": o}
        desc = str(o.get("desc") or "").strip().rstrip(".")
        if not desc:
            continue
        outs.append({"id": f"v{k + 1}",
                     "name": str(o.get("name") or "").strip() or f"look {k + 1}",
                     "desc": desc, "kind": "variety", "ref_id": None})
    return {"character_type": str(data.get("character_type") or "").strip()[:80],
            "outfits": outs[:n]}


_GARMENT_SYSTEM = (
    "You describe clothing for an image model. You name garments and colours, nothing else.")

_GARMENT_PROMPT = """Describe ONLY the clothing in this image, as a single phrase.

NAME each garment and its colour, in the order top, bottom, footwear —
for example "a red plaid flannel shirt, dark blue jeans and brown leather boots".

Say nothing about the person wearing it: not their face, hair, body, build, age or pose.
Say nothing about the background. Do not use category words on their own ("casual wear",
"an outfit", "clothing") — an image model ignores them. If a garment is not visible in the
image, leave it out rather than inventing it.

Answer with the phrase only, no preamble and no full stop."""


def _clean_garment_desc(raw: str) -> str:
    """Strip the preamble a chat model adds and reject a non-answer."""
    t = " ".join((raw or "").split()).strip().strip('"').rstrip(".")
    for lead in ("the clothing is ", "the person is wearing ", "this image shows ",
                 "the garments are ", "wearing ", "the outfit is "):
        if t.lower().startswith(lead):
            t = t[len(lead):]
    t = t.strip().strip('"').rstrip(".")
    # A description with no garment noun in it is worse than none: Klein ignores
    # category words, so "casual clothing" would silently produce whatever it likes.
    # Word boundaries, not substrings: "an outfit suitable for winter" contains
    # "suit" and sailed through the first version of this check — which is
    # precisely the category-only answer it exists to reject.
    words = set(re.findall(r"[a-z-]+", t.lower()))
    if len(t) < 8 or not (words & set(_GARMENT_WORDS)):
        return ""
    return t[:240]


_GARMENT_WORDS = (
    "shirt", "tee", "t-shirt", "blouse", "top", "jumper", "sweater", "sweatshirt", "hoodie",
    "jacket", "coat", "blazer", "waistcoat", "vest", "cardigan", "dress", "gown", "robe",
    "tunic", "trousers", "pants", "jeans", "shorts", "skirt", "leggings", "chinos",
    "overalls", "boots", "shoes", "trainers", "sneakers", "sandals", "suit", "uniform",
    "armour", "armor", "cloak", "scarf", "apron", "kilt", "poncho")
