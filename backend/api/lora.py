"""LoRA Dataset Generator — 🎓 the fifth mode (v1.216.0).

Turns a Klein 3.0 character (tagged references + stripped base set) into a
TRAINING-READY LoRA dataset: a planned shot list, Klein-rendered images, written
captions, a vision-model QC pass, and a zip a trainer can eat directly.

Why the plan looks the way it does (researched 2026-08-04, sources in
docs/LORA_DATASET.md — this is not guesswork):
  * 30-100 images for a character LoRA; variety is what buys flexibility, and a
    narrow dataset bakes its own narrowness in (all-bikini dataset -> every
    render is a bikini).  We default to 40 and allow 16-120.
  * Cover FRAMING (face / head+shoulders / waist-up / full body), ANGLE
    (front, 3/4, profile, back), EXPRESSION, POSE, LIGHTING and BACKGROUND.
    Locked angles or one lighting setup produce a LoRA that can only do that.
  * >= 1024px.  Trainers bucket by aspect ratio, so mixed shapes are fine; we
    render each framing at the aspect that suits it.
  * CAPTION WHAT VARIES, never the identity.  Anything you caption becomes
    something the model can be asked to change; anything you leave out gets
    absorbed into the trigger word.  So captions carry trigger + class + shot,
    angle, expression, pose, clothing, background, lighting — and say nothing
    about his face, hair colour or build.
  * The trigger is a rare token followed by the class ("rbmnduke man") so the
    class prior helps rather than fights.
  * Caption files are `<image stem>.txt` next to the image — the format both
    ai-toolkit and kohya/FluxTrainer read.  `[trigger]` is ai-toolkit's
    placeholder, so we can export either literal or placeholder captions.

Endpoints (prefix /api/lora):
  health                       workers + vision model preflight
  datasets                     list / create (creating only PLANS — no renders)
  datasets/{id}                read one (items, captions, QC, counts)
  datasets/{id}/plan           re-plan (count / options changed)
  datasets/{id}/generate       render planned items, fanned across ALL workers
  datasets/{id}/caption        write captions from the plan (+ vision enrichment)
  datasets/{id}/qc             vision QC pass: framing/angle/expression/artifacts
  datasets/{id}/items/...      per-item image, update caption, regenerate, delete
  datasets/{id}/export         build the training zip (images + txt + configs)
  datasets/{id}/exports/{f}    download it

Storage: <project_dir>/_libraries/lora/datasets/<ds_id>/
    dataset.json · images/<item>.png · exports/<name>.zip
"""
from __future__ import annotations

import json
import logging
import random
import re
import threading as _th
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings as cfg
from backend.services import likeness as _like
from backend.services import subject as _subj
from backend.services import wardrobe as _ward
from backend.database.database import get_session

from backend.api.klein3 import (          # the proven Klein 3.0 machinery
    _load as _load_char, _cdir, _base_for_view, _refs_by_tag, _dispatcher,
    _klein_worker, _parallel_klein_edits, _save_png_bytes, _spawn, _now,
    _slugify, VIEW_TAGS,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/lora", tags=["lora"])

_LORA_ROOT = Path(cfg.project_dir) / "_libraries" / "lora"
_DS_ROOT = _LORA_ROOT / "datasets"

_RUNS: Dict[str, dict] = {}       # ds_id -> live job state (worker/status per item)

# Every mutation of a dataset.json that happens from a worker thread goes through
# this.  v1.223: the render path had none, so concurrent completions clobbered
# each other's status/attempts/identity and a later re-plan deleted images it
# believed were never rendered.  Module-level, not per-call: `dataset_repair`
# interleaves render and QC passes over the SAME file.
_DS_WRITE_LOCK = _th.Lock()


# ══ the recipe ═══════════════════════════════════════════════════════════════
# (weight, caption phrase, render phrase, aspect)  — aspect: portrait|square|tall
FRAMINGS = [
    ("face", 0.20, "an extreme close-up face shot",
     "an extreme close-up photograph of his face filling the frame, head and neck only, "
     "sharp focus on the eyes", (1024, 1024)),
    ("headshot", 0.20, "a close-up portrait, head and shoulders",
     "a close-up portrait photograph, head and shoulders, chest up", (896, 1152)),
    ("upper", 0.30, "a medium shot from the waist up",
     "a medium photograph of him from the waist up, with clear empty space above "
     "his head and his arms inside the frame", (896, 1152)),
    ("full", 0.30, "a full body shot, head to feet",
     "a full body photograph of him, his whole figure from the top of his head to "
     "the soles of his shoes inside the frame, standing on the ground, with clear "
     "empty space above his head and below his feet, the camera far enough back "
     "that his whole body sits comfortably within the picture",
     (768, 1344)),
]
ANGLES = [
    ("front", "facing the camera", "facing the camera straight on", "front"),
    ("three_quarter_left", "turned three-quarters to his left",
     "his body turned about 45 degrees to his left, head toward the camera", "left"),
    ("profile_left", "in left profile", "in full left profile, side on to the camera", "left"),
    ("three_quarter_right", "turned three-quarters to his right",
     "his body turned about 45 degrees to his right, head toward the camera", "right"),
    ("profile_right", "in right profile", "in full right profile, side on to the camera", "right"),
    ("back", "seen from behind", "seen from directly behind, his back to the camera", "back"),
]
EXPRESSIONS = [
    ("neutral", "a neutral expression"), ("slight_smile", "a slight smile"),
    ("smiling", "smiling broadly"), ("serious", "a serious expression"),
    ("laughing", "laughing"), ("surprised", "a surprised expression"),
    ("angry", "an angry expression"), ("sad", "a sad expression"),
    ("thoughtful", "a thoughtful expression"),
]
POSES = [
    ("standing", "standing relaxed", "standing relaxed with his arms at his sides"),
    ("arms_crossed", "with his arms crossed", "standing with his arms crossed over his chest"),
    ("hands_hips", "with his hands on his hips",
     "standing with both hands resting on his own hips, elbows out"),
    ("walking", "walking", "walking toward the camera mid-stride"),
    ("sitting", "sitting on a chair", "sitting upright on a plain chair, hands on his thighs"),
    ("leaning", "leaning against a wall", "leaning one shoulder against a plain wall"),
    ("pockets", "with his hands in his pockets", "standing with both hands in his pockets"),
    ("gesturing", "gesturing while talking", "gesturing with one open hand while talking"),
]
LIGHTING = [
    ("studio_soft", "soft studio lighting", "soft even studio lighting"),
    ("daylight", "bright daylight", "bright natural daylight"),
    ("window", "window light", "soft directional window light from one side"),
    ("warm_indoor", "warm indoor lamp light", "warm indoor lamp light"),
    ("overcast", "flat overcast light", "flat soft overcast daylight"),
    ("moody", "dim moody lighting", "dim moody low-key lighting"),
    ("golden_hour", "golden hour sunlight", "warm golden hour sunlight from a low angle"),
    ("hard_side", "hard side light", "hard directional side light with defined shadows"),
]
BACKGROUNDS = [
    ("gray_studio", "a plain gray studio background", "a plain light gray studio backdrop"),
    ("white", "a plain white background", "a plain white seamless backdrop"),
    ("dark_studio", "a dark studio background", "a plain dark charcoal backdrop"),
    ("room", "an indoor room", "a simple indoor room with a plain wall"),
    ("street", "a city street", "a city street with blurred buildings behind him"),
    ("park", "a park", "a park with green trees blurred behind him"),
    ("office", "an office interior", "a plain modern office interior"),
    ("brick", "a brick wall", "an outdoor brick wall behind him"),
]

# Framing weights.  'balanced' is ours (full-body flexibility); 'face_heavy'
# mirrors lora-dataset-studio's 12 face / 6 bust / 6 body / 1 back target, which
# Fizgig's likeness tooling also leans toward — more face data buys likeness,
# fewer body shots costs some full-body flexibility.  A preset, not a silent
# change: run one of each and look.
FRAMING_PRESETS = {
    "balanced": {"face": 0.20, "headshot": 0.20, "upper": 0.30, "full": 0.30},
    "face_heavy": {"face": 0.44, "headshot": 0.24, "upper": 0.16, "full": 0.16},
}
# how often the camera may face away, per preset
_BACK_EVERY = {"balanced": 10, "face_heavy": 20}

# Face and headshot rows carry no body pose — captioning a pose the crop cannot
# show teaches the model a word it can never satisfy.
_POSELESS = {"face", "headshot"}

# Only shots that show the body may face away: a close-up of the back of a head
# is not a portrait, cannot pass a face check, and teaches nothing.
_BACK_OK = {"upper", "full"}

# Angle mix.  A character LoRA lives on face-bearing data, so the six angles are
# NOT dealt evenly: the rotation below is the deal order, and 'back' appears once
# per ten rows instead of one in six.
_ANGLE_MIX = ["front", "three_quarter_left", "profile_left", "front",
              "three_quarter_right", "profile_right", "three_quarter_left",
              "back", "three_quarter_right", "front"]

_QUALITY = ("photorealistic photograph, natural skin texture, sharp focus, high detail, "
            "realistic colour")


# ══ storage ══════════════════════════════════════════════════════════════════
def _ds_dir(ds_id: str) -> Path:
    if not ds_id or "/" in ds_id or "\\" in ds_id or ".." in ds_id:
        raise HTTPException(400, "bad dataset id")
    return _DS_ROOT / ds_id


def _read_ds(ds_id: str) -> dict:
    fp = _ds_dir(ds_id) / "dataset.json"
    if not fp.exists():
        raise HTTPException(404, "dataset not found")
    return json.loads(fp.read_text("utf-8"))


def _write_ds(ds: dict) -> None:
    d = _ds_dir(ds["id"])
    (d / "images").mkdir(parents=True, exist_ok=True)
    (d / "dataset.json").write_text(json.dumps(ds, indent=2), "utf-8")


def _item_path(ds_id: str, item_id: str) -> Path:
    if "/" in item_id or "\\" in item_id or ".." in item_id:
        raise HTTPException(400, "bad item id")
    return _ds_dir(ds_id) / "images" / f"{item_id}.png"


# ══ planning ═════════════════════════════════════════════════════════════════
def _spread(seq: List[Any], i: int) -> Any:
    """Deterministic round-robin — the same count always yields the same plan,
    so a re-plan is diffable instead of random."""
    return seq[i % len(seq)]


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

def _build_plan(count: int, opts: dict) -> List[dict]:
    """The shot list.  Framing is allocated by weight, everything else is spread
    evenly so no attribute clumps (an angle that appears twice as often teaches
    the model that angle twice as hard)."""
    count = max(8, min(int(count or 40), 120))
    angles = [a for a in ANGLES if a[0] in (opts.get("angles") or [a[0] for a in ANGLES])]
    exprs = [e for e in EXPRESSIONS if e[0] in (opts.get("expressions") or [e[0] for e in EXPRESSIONS])]
    poses = [p for p in POSES if p[0] in (opts.get("poses") or [p[0] for p in POSES])]
    lights = [l for l in LIGHTING if l[0] in (opts.get("lighting") or [l[0] for l in LIGHTING])]
    bgs = [b for b in BACKGROUNDS if b[0] in (opts.get("backgrounds") or [b[0] for b in BACKGROUNDS])]
    angles, exprs, poses = angles or ANGLES, exprs or EXPRESSIONS, poses or POSES
    lights, bgs = lights or LIGHTING, bgs or BACKGROUNDS

    preset = str(opts.get("preset") or "balanced")
    weights = FRAMING_PRESETS.get(preset, FRAMING_PRESETS["balanced"])
    # framing allocation by weight, largest-remainder so the total is exact
    raw = [(f, count * weights.get(f[0], f[1])) for f in FRAMINGS]
    alloc = {f[0]: int(n) for f, n in raw}
    left = count - sum(alloc.values())
    for f, n in sorted(raw, key=lambda x: -(x[1] - int(x[1])))[:left]:
        alloc[f[0]] += 1

    rows = [fr for fr in FRAMINGS for _ in range(alloc.get(fr[0], 0))]
    # Angles are dealt in strict rotation, so every angle appears within one of
    # every other — an angle that shows up twice as often is trained twice as
    # hard.  A face crop seen from behind teaches nothing, so any face row that
    # drew 'back' SWAPS with a body row: the counts stay exactly even.
    keyed = {a[0]: a for a in angles}
    mix = [keyed[k] for k in _ANGLE_MIX if k in keyed] or angles
    if _BACK_EVERY.get(preset, 10) > len(mix):        # thin the back rows out
        mix = mix + [k for k in mix if k[0] != "back"]
    seq = [mix[k % len(mix)] for k in range(len(rows))]
    # a face or headshot row may never face away — swap it with a body row so the
    # angle counts stay exactly as dealt
    for a_i, fr in enumerate(rows):
        if seq[a_i][0] == "back" and fr[0] not in _BACK_OK:
            for b_i, fr2 in enumerate(rows):
                if fr2[0] in _BACK_OK and seq[b_i][0] != "back":
                    seq[a_i], seq[b_i] = seq[b_i], seq[a_i]
                    break
            else:                      # nowhere to put it: face the front instead
                seq[a_i] = keyed.get("front", angles[0])

    # Outfits are dealt across the whole shot list, NOT in blocks: `rows` is
    # grouped by framing, so a block deal would hand one outfit every full-body
    # and another every close-up, and the LoRA would learn the pairing.
    outfits = _norm_outfits({"outfits": opts.get("outfits")})
    o_seq = _deal_outfits([(fr[0], seq[i][0]) for i, fr in enumerate(rows)],
                          outfits)

    plan: List[dict] = []
    for i, fr in enumerate(rows):
        # each attribute rotates on its own offset, so they do not lock in step
        # (every 'front' row also being 'neutral' would teach the pair, not each)
        plan.append({
            "id": f"{i + 1:04d}",
            "framing": fr[0], "angle": seq[i][0],
            # an expression is invisible from behind — no row should claim one
            "expression": None if seq[i][0] == "back" else _spread(exprs, i)[0],
            "lighting": _spread(lights, i + 1)[0],
            "background": _spread(bgs, i + 2)[0],
            "pose": None if fr[0] in _POSELESS else _spread(poses, i)[0],
            "outfit": o_seq[i] if i < len(o_seq) else None,
            "status": "planned", "caption": "", "qc": None,
            "width": fr[4][0], "height": fr[4][1],
        })
    return plan


# v1.221: which base a THREE-QUARTER row starts from.
#   "side"  -- the 90-degree profile base for that side (pre-v1.221 behaviour)
#   "front" -- the front base, i.e. turn 45 degrees FROM front rather than 45
#              back from profile.  Measured motivation: 14/14 three-quarter rows
#              failed their angle check, while profile rows (whose base already
#              matches) missed only 17-43%.  Front is also the strongest identity
#              base measured (median 0.705 against 0.436-0.477 for the sides).
_TQ_ANGLES = ("three_quarter_left", "three_quarter_right")


def _base_view_for(angle_key: str, planned_view: str, tq_base: str) -> str:
    """The base VIEW a row should use.  Separated out so the choice is one
    testable function rather than an expression buried in the job builder."""
    if angle_key in _TQ_ANGLES and str(tq_base or "side").lower() == "front":
        return "front"
    return planned_view


# v1.235: how a THREE-QUARTER row is asked for.  One dict so a variant is a
# string in the dataset options rather than an edit to the renderer.
#   {edge}  -> "left" or "right", the edge of the PICTURE his nose points at
#   {n}     -> the 1-based index of the side reference (tworef only)
TQ_WORDINGS = {
    "degrees": ("his body turned about 45 degrees to his {his_side}, "
                "head toward the camera"),
    "frame": ("his head and his body both turned toward the {edge} edge of the "
              "picture, his nose pointing toward the {edge} edge, both of his "
              "eyes still visible to the camera"),
    "halfway": ("his head and his body turned halfway between facing the camera "
                "and facing the {edge} edge of the picture, far enough that one "
                "of his ears is hidden and both of his eyes are still visible"),
    "tworef": ("his head and his body turned halfway between the way he stands "
               "in image 1 and the way he stands in image {n}, both of his eyes "
               "still visible to the camera"),
}
# v1.237: which fixed wording each direction gets under "auto".  Measured over
# 40 three-quarter renders per side; see this version's changelog entry.  Left
# needs the harder push, right overshoots if it gets one.
TQ_AUTO = {"three_quarter_left": "frame", "three_quarter_right": "halfway"}

# v1.236: measured, on 64 renders.  A dataset that never sets this now gets the
# wording that landed 13 of 16 three-quarter rows in a textbook 25-45 degree
# turn, against 3 of 16 for the sentence this replaces.  An explicit
# `tq_wording` in a dataset's options still wins — nothing already chosen moves.
TQ_DEFAULT = "auto"

# v1.249: which shot types get the character's FACE reference alongside the base.
#   closeups  face and headshot only — the behaviour every dataset so far used
#   always    every framing, including upper and full
#   never     none, for a character whose face reference is poor
# v1.252: MEASURED on redv1's 12 upper+full rows, rendered both ways:
#
#   closeups   median 0.431   min 0.247   below match 6   NOT HIM 2
#   always     median 0.526   min 0.485   below match 0   NOT HIM 0
#
# `full` rows alone went from a median of 0.261 to 0.515 — those are the rows
# whose base shows the face at a twelfth of the frame height, so the model had
# nothing to work from. Nothing was traded away: `upper` held its median and
# lost both below-match rows, and no row got worse.
#
# `closeups` and `never` stay selectable — a character with a poor face
# reference is better off without one, and `never` says so honestly.
FACE_REF_MODES = ("closeups", "always", "never")
FACE_REF_DEFAULT = "always"
_FACE_REF_FRAMINGS = {"closeups": ("face", "headshot"),
                      "always": ("face", "headshot", "upper", "full"),
                      "never": ()}


def _face_ref_mode(ds: dict) -> str:
    m = str((ds.get("options") or {}).get("face_ref") or FACE_REF_DEFAULT).lower()
    return m if m in FACE_REF_MODES else FACE_REF_DEFAULT


def _wants_face_ref(ds: dict, framing: Optional[str]) -> bool:
    return str(framing or "") in _FACE_REF_FRAMINGS[_face_ref_mode(ds)]

# The window a three-quarter turn SHOULD land in, as opposed to the wider band
# QC passes on.  Used for judging wordings against each other: a variant that
# pushes everything to 54 degrees scores well on a 20-55 band and is producing
# near-profiles.  Not used to fail an image.
TQ_TARGET = (25.0, 45.0)


def _tq_wording(ds: dict) -> str:
    """The option as CHOSEN — may be "auto", which is not itself a template."""
    w = str((ds.get("options") or {}).get("tq_wording") or TQ_DEFAULT).lower()
    return w if (w in TQ_WORDINGS or w == "auto") else TQ_DEFAULT


def _tq_mode(ds: dict, angle_key: Optional[str]) -> str:
    """The concrete wording THIS row renders with.

    v1.237: separated from `_tq_wording` because "auto" resolves differently per
    direction, and every caller that reasons about the wording — the prompt, the
    second-reference decision, the stamp recorded on the image — has to resolve
    it the same way.  One function, so they cannot drift apart."""
    w = _tq_wording(ds)
    if w != "auto":
        return w
    return TQ_AUTO.get(str(angle_key or ""), "halfway")


def _angle_text(ds: dict, item: dict, ang: Tuple, side_idx: Optional[int] = None) -> str:
    """The sentence describing which way he faces.

    Everything that is not a three-quarter row is returned untouched: front,
    profile and back already measure 4-of-4 and 10-of-10 correct, and the one
    sure way to lose that is to rewrite prompts that are working."""
    key = item.get("angle")
    if key not in _TQ_ANGLES:
        return ang[2]
    mode = _tq_mode(ds, key)
    edge = "left" if key == "three_quarter_left" else "right"
    if mode == "tworef" and not side_idx:
        # Asked for two references and only got one — say what he faces in
        # words rather than pointing at an image that is not there.
        mode = "halfway"
    return TQ_WORDINGS[mode].format(edge=edge, his_side=edge, n=side_idx or 2)


def _plan_opts(ds: dict) -> dict:
    """The options `_build_plan` actually needs, assembled in ONE place.

    v1.219: both routes used to build this inline and both forgot `outfits`, so
    every row planned through the API came out with no outfit at all.  A single
    builder means a new planner input cannot be wired into one caller and
    silently missed in the other."""
    return {**(ds.get("options") or {}),
            "preset": ds.get("preset") or (ds.get("options") or {}).get("preset") or "balanced",
            "outfits": ds.get("outfits") or []}


def _plan_impact(ds: dict, fresh: List[dict]) -> dict:
    """What a re-plan would DESTROY, computed before anything is written.

    v1.222: this did not exist, and the route deleted every image whose slot
    moved without saying so.  One omitted option key silently re-cut the whole
    shot list and 33 rendered images went with it."""
    KEYS = ("framing", "angle", "expression", "pose", "lighting", "background")
    old = {it["id"]: it for it in ds.get("items", [])}
    kept, lost = [], []
    for it in fresh:
        prev = old.get(it["id"])
        # v1.223: trust the FILE, not the status field.  Datasets written before
        # the lock have rows whose status was lost to the race even though the
        # image is right there on disk; going by status alone deletes them.
        if prev and (prev.get("status") == "done"
                     or _item_path(ds.get("id", ""), it["id"]).exists()):
            (kept if all(prev.get(k) == it.get(k) for k in KEYS) else lost).append(it["id"])
    gone = [i for i, p in old.items()
            if (p.get("status") == "done" or _item_path(ds.get("id", ""), i).exists())
            and i not in {f["id"] for f in fresh}]
    changed = sorted({f"{old[i].get('angle')} -> {n.get('angle')}"
                      for i in lost for n in fresh if n["id"] == i
                      and old[i].get("angle") != n.get("angle")})
    return {"rendered_before": sum(1 for p in old.values() if p.get("status") == "done"),
            "kept": len(kept), "discarded": len(lost) + len(gone),
            "discarded_ids": sorted(lost + gone)[:40],
            "angle_changes": changed[:10]}


def _plan_warnings_identity(ds: dict) -> List[str]:
    """v1.260: the identity warnings, folded into the plan response so a
    stripped base and an empty wardrobe are visible at planning time."""
    try:
        return list(_identity_preview(ds).get("warnings") or [])
    except Exception:  # noqa: BLE001 — a warning must never break a plan
        return []


def _plan_warnings(count: int, outfits: List[dict]) -> List[str]:
    """Said out loud, not silently absorbed.  A wardrobe spread too thin is the
    exact failure v1.216 exists to prevent, and it is invisible until training."""
    out: List[str] = []
    n = len(outfits or [])
    if n >= 2:
        per = count / n
        if per < 8:
            out.append(f"{n} outfits over {count} images is ~{per:.0f} each — measured, some "
                       f"outfits then appear in only 2 of the 4 framings, which trains "
                       f"'that outfit means that shot type'. {_suggested_count(n)} is the "
                       f"sized-for-this-wardrobe count.")
        elif per < 12:
            out.append(f"{n} outfits over {count} images is ~{per:.0f} each — workable, but "
                       f"{_suggested_count(n)} gives every outfit a full spread of framings.")
    return out


def _by_key(seq, key):
    return next((x for x in seq if x[0] == key), seq[0])


def _render_prompt(ds: dict, item: dict, garment_idx: Optional[int] = None,
                   side_idx: Optional[int] = None) -> str:
    """What Klein is asked to make.  Affirmative throughout — Klein has no
    negative conditioning and runs at cfg=1 (see [[klein-prompt-no-negatives]]),
    so this states what SHOULD be in frame and never what to avoid."""
    fr = _by_key(FRAMINGS, item["framing"])
    ang = _by_key(ANGLES, item["angle"])
    li = _by_key(LIGHTING, item["lighting"])
    bg = _by_key(BACKGROUNDS, item["background"])
    back = item["angle"] == "back"
    if back:
        # Image 1 is the BACK base: it holds no face, so the prompt asks for none.
        # Naming the face here is what produced front-facing renders off a back
        # reference — the model filled in the missing face and turned him round.
        bits = [
            f"The person from image 1, photographed as {fr[3]}, {ang[2]}. "
            "The camera sees the back of his head and his back; his face is turned away "
            "from the camera.",
            "His hairstyle, his hair colour, his skin, his clothing, his build, his weight, "
            "his height, his limb thickness and his proportions are exactly the ones in "
            "image 1.",
        ]
    else:
        ex = _by_key(EXPRESSIONS, item["expression"])
        bits = [
            f"The person from image 1, photographed as {fr[3]}, "
            f"{_angle_text(ds, item, ang, side_idx)}, with {ex[1]}.",
            "His face, his hairstyle, his skin, his build, his weight, his height, his limb "
            "thickness and his proportions are exactly the ones in image 1.",
        ]
    if item.get("pose"):
        bits.append(f"He is {_by_key(POSES, item['pose'])[2]}.")
    bits.append(f"Lighting: {li[2]}. Background: {bg[2]}.")
    worn = _outfit_text(ds, item)
    if worn:
        # Naming the image index is the half that makes a garment reference work.
        # Klein ignores category words ("the clothing in image 3"), so the
        # garments are NAMED and the image is cited as corroboration, never as a
        # substitute for naming them.
        bits.append(f"He is wearing {worn}, the exact garments shown in image "
                    f"{garment_idx}." if garment_idx else f"He is wearing {worn}.")
    bits.append(_QUALITY + ".")
    return " ".join(bits)


def _caption(ds: dict, item: dict, trigger_literal: bool = True) -> str:
    """Caption = trigger + class + everything that VARIES across the set.

    Deliberately silent about his face, hair, build and proportions: whatever a
    caption names becomes a knob the trainer can turn, and whatever it omits is
    absorbed into the trigger.  That is the whole mechanism behind a character
    LoRA that stays on-model."""
    trig = (ds.get("trigger") or "sks").strip()
    cls = (ds.get("class_token") or "person").strip()
    # Fizgig's caption rule, from real runs: "if the subject isn't actually
    # recognizable in a shot (back of head, extreme distance), consider leaving
    # the trigger out of that caption".  A back shot shows no face, so binding
    # the trigger to it teaches the trigger a back of a head.
    if item.get("angle") == "back":
        head = cls
    else:
        head = (f"{trig} {cls}" if trigger_literal else f"[trigger] {cls}").strip()
    fr = _by_key(FRAMINGS, item["framing"])
    ang = _by_key(ANGLES, item["angle"])
    li = _by_key(LIGHTING, item["lighting"])
    bg = _by_key(BACKGROUNDS, item["background"])
    parts = [f"{fr[2]} of {head}", ang[1]]
    # invisible from behind — and rows planned before v1.210.1 still carry one,
    # so the angle decides, not the field
    if item.get("expression") and item.get("angle") != "back":
        parts.append(f"with {_by_key(EXPRESSIONS, item['expression'])[1]}")
    if item.get("pose"):
        parts.append(_by_key(POSES, item["pose"])[1])
    # An extreme close-up of a face shows no clothing; naming an outfit there
    # teaches a false association, exactly like an expression on a back shot.
    worn = _outfit_text(ds, item)
    if worn:
        parts.append(f"wearing {worn}")
    parts.append(f"in front of {bg[1]}")
    parts.append(li[1])
    extra = (item.get("caption_extra") or "").strip().strip(",")
    if extra:
        parts.append(extra)
    return ", ".join(p for p in parts if p) + "."


# ══ models ═══════════════════════════════════════════════════════════════════
class DatasetIn(BaseModel):
    name: str
    char_slug: str
    trigger: str = ""
    class_token: str = "man"
    target: str = "krea2"            # krea2 | flux | sdxl — affects the README/config
    # None = size it from the wardrobe (v1.219). An explicit number always wins;
    # the pre-v1.219 UI always sent one, so its behaviour is unchanged.
    count: Optional[int] = None
    preset: str = "balanced"         # balanced | face_heavy (see FRAMING_PRESETS)
    outfit: str = ""                 # legacy single outfit; migrated to outfits[]
    outfits: Optional[List[dict]] = None   # [{name, desc, kind: named|variety, ref_id}]
    base_mode: Optional[str] = None  # auto | dressed | stripped (None = character default)
    options: Optional[dict] = None   # subset filters for angles/expressions/…


class PlanIn(BaseModel):
    count: Optional[int] = None
    outfit: Optional[str] = None
    outfits: Optional[List[dict]] = None
    options: Optional[dict] = None
    force: bool = False              # required to discard rendered images


class OutfitsIn(BaseModel):
    outfits: List[dict] = []
    resize: bool = False             # also re-suggest the set size from the wardrobe


class WardrobeIn(BaseModel):
    count: int = 5


class ItemsIn(BaseModel):
    item_ids: Optional[List[str]] = None
    overwrite: bool = False


class CaptionIn(BaseModel):
    item_ids: Optional[List[str]] = None
    overwrite: bool = False
    enrich: bool = False             # let the vision model add observed details


class ItemUpdateIn(BaseModel):
    caption: Optional[str] = None
    caption_extra: Optional[str] = None
    keep: Optional[bool] = None


class ExportIn(BaseModel):
    trigger_mode: str = "literal"    # literal | placeholder ([trigger] for ai-toolkit)
    include_flagged: bool = False    # ship images QC flagged as bad
    include_bare: bool = False       # v1.261: ship images where he is undressed
    resolution: Optional[List[int]] = None
    # v1.248: an ArcFace floor, off by default. `ok` already excludes anything
    # under ARC_DIFFERENT (0.25); this is for asking a stricter question.
    # Deliberately NOT defaulted to ARC_MATCH — measured, dorian's profile rows
    # sit at 0.4313 and 0.4410 and are fine, so one number for every view would
    # trade good images for bad ones.
    min_likeness: Optional[float] = None


MAX_ATTEMPTS = 3          # after this many renders an image is a plan problem
MAX_ROUNDS = 6            # hard ceiling on the auto-repair loop


def _flag_summary(ds: dict) -> dict:
    """WHY images are flagged, counted.

    v1.241: `framing_off` and `cropped_badly` are GONE — the vision model
    scored 0 of 12 on images verified by eye, twice, the second time after a
    prompt written specifically to fix it. Framing has no instrument yet and the
    summary says so in `not_checked` rather than implying a pass.

    `angle_off` is now MEASURED (head yaw, v1.234) and is the one advisory
    counter worth acting on. `expression_off` stays advisory and is listed in
    `unreliable`. `flagged` means: not him, more than one person, or a visible
    artifact."""
    out = {"flagged": 0, "checked": 0, "artifacts": 0,
           "angle_off": 0, "expression_off": 0,
           "framing_off": 0, "framing_measured": 0, "framing_unmeasured": 0,
           "crop_off": 0, "crop_measured": 0, "crop_unmeasured": 0,
           "not_one_person": 0, "face_unclear": 0, "identity_off": 0,
           "bare_skin": 0, "wardrobe_measured": 0, "wardrobe_unmeasured": 0,
           "outfit_off": 0, "stuck": 0, "arcface_scored": 0, "no_face": 0,
           "back_low_likeness": 0, "angle_measured": 0, "angle_unmeasured": 0,
           "top_issues": {}}
    for it in ds.get("items", []):
        q = it.get("qc") or {}
        if not q:
            continue
        out["checked"] += 1
        if q.get("ok") is False:
            out["flagged"] += 1
        if q.get("artifacts"):
            out["artifacts"] += 1
        if q.get("angle_ok") is False:
            out["angle_off"] += 1
        # v1.234: "measured and correct" and "never measured" both used to read
        # as a pass.  They are different facts and are now counted apart.
        if q.get("angle_method") == "head-yaw":
            out["angle_measured"] += 1
        elif q.get("angle_method") == "unmeasured":
            out["angle_unmeasured"] += 1
        if q.get("framing_ok") is False:
            out["framing_off"] += 1
        if q.get("framing_method") == "face-height":
            out["framing_measured"] += 1
        elif q.get("framing_method") == "unmeasured":
            out["framing_unmeasured"] += 1
        if q.get("crop_ok") is False:
            out["crop_off"] += 1
        if q.get("crop_method") == "person-mask":
            out["crop_measured"] += 1
        elif q.get("crop_method") == "unmeasured":
            out["crop_unmeasured"] += 1
        if q.get("expression_ok") is False:
            out["expression_off"] += 1
        if q.get("one_person") is False:
            out["not_one_person"] += 1
        if q.get("face_clear") is False:
            out["face_unclear"] += 1
        if q.get("same_person") is False:
            out["identity_off"] += 1
        if q.get("outfit_ok") is False:
            out["outfit_off"] += 1
        # v1.261. Distinct from `outfit_off`, which is the vision model judging a
        # garment against the plan and is not trusted. This is the narrower
        # question — is he wearing anything at all — measured twice per image.
        if q.get("bare") is True:
            out["bare_skin"] += 1
        if str(q.get("wardrobe_method") or "").startswith("vision-"):
            out["wardrobe_measured"] += 1
        elif q.get("wardrobe_method") == "unmeasured" or "bare" in q:
            out["wardrobe_unmeasured"] += 1
        if q.get("identity_method") == "arcface":
            out["arcface_scored"] += 1
        # No detectable face is a CORRECT outcome for a back shot — counted, not
        # flagged, exactly as Fizgig never auto-excludes an unscoreable row.
        if q.get("identity_method") == "arcface" and q.get("identity_score") is None:
            out["no_face"] += 1
        # Counted apart from identity_off: informative, never a failure.
        if (q.get("identity_scored_against_front") is False
                and isinstance(q.get("identity_score"), (int, float))
                and q["identity_score"] < _like.ARC_DIFFERENT):
            out["back_low_likeness"] += 1
        for phrase in (q.get("issues") or [])[:3]:
            key = str(phrase).strip().lower()[:60]
            if key:
                out["top_issues"][key] = out["top_issues"].get(key, 0) + 1
        if int(it.get("attempts") or 0) >= MAX_ATTEMPTS and q.get("ok") is False:
            out["stuck"] += 1
    out["top_issues"] = dict(sorted(out["top_issues"].items(),
                                    key=lambda kv: -kv[1])[:6])
    # v1.241: say what is NOT checked, so a clean summary is not read as a
    # clean dataset.  Framing has no instrument yet; expression has an
    # unreliable one.
    # v1.246: crop is measured and trusted — 20 of 20 on real images after the
    # probe corrected the rule. It only reports as unchecked when rembg is
    # genuinely absent, which is a real state and not a silent one.
    out["not_checked"] = [] if out["crop_measured"] else ["crop"]
    # v1.261: a set nobody has run the wardrobe check on has not passed it.
    if not out["wardrobe_measured"]:
        out["not_checked"].append("wardrobe")
    out["unreliable"] = ["expression"]
    if out["bare_skin"]:
        out.setdefault("warnings", []).append(
            f"{out['bare_skin']} image(s) show the subject undressed or in underwear. "
            f"A LoRA learns whatever is in the pixels: train on these and the trigger "
            f"word carries bare skin with it. Re-render them (repair now picks them "
            f"up) or exclude them.")
    # v1.249: redv1 exported 20 of 20 with `flagged: 0` while 7 of 18 scored
    # faces sat below ARC_MATCH and the minimum had only cleared the
    # different-person floor on its fifth draw. "Nothing flagged" read as "good
    # dataset". A set whose likeness is broadly weak now says so.
    _sv = sorted(s for s in ((it.get("qc") or {}).get("identity_score")
                             for it in ds.get("items", []))
                 if isinstance(s, (int, float)))
    if _sv:
        _below = sum(1 for s in _sv if s < _like.ARC_MATCH)
        out["likeness_median"] = round(_sv[len(_sv) // 2], 4)
        out["likeness_min"] = round(_sv[0], 4)
        out["below_match"] = _below
        if _below >= max(2, len(_sv) // 5):
            out.setdefault("warnings", []).append(
                f"{_below} of {len(_sv)} scored faces are below the {_like.ARC_MATCH} "
                f"match line (median {out['likeness_median']}, worst "
                f"{out['likeness_min']}). Nothing is flagged, because only "
                f"{_like.ARC_DIFFERENT} fails an image — but a training set this "
                f"far from its own reference will teach the trigger word a face "
                f"that drifts. Check the character's references before training.")
    return out


def _likeness_baselines(ds: dict) -> Tuple[List[Any], List[str]]:
    """The FRONTAL baseline set. Superseded by `_baseline_sets` in v1.247 and
    kept as a shim so there is exactly one implementation — two functions
    building baselines slightly differently is how a profile ended up scored
    against a left reference and a face crop.

    Up to THREE reference embeddings, and what they were.

    Fizgig averages three on purpose — "one photo\'s framing bias can\'t
    dominate the score". A single front base makes every front-framed render
    look more like him than it is.

    Deliberately drawn from the CHARACTER\'s own references, never from this
    dataset\'s renders: scoring images against themselves would produce a
    beautiful number that means nothing."""
    return _baseline_sets(ds).get("front", ([], []))


# v1.247: which baseline set a row's angle should be scored against.
_ANGLE_BASELINE = {
    "front": "front", "three_quarter_left": "front", "three_quarter_right": "front",
    "profile_left": "left", "profile_right": "right",
    "back": "front",     # scored, but never a verdict — see v1.221
}


def _ds_base_mode(ds: dict) -> str:
    """Which base a DATASET renders from.

    v1.260: an unset value used to fall through to the character's own mode,
    which is `auto` — newest version of a view wins. On a character whose newest
    front base came from the Strip SET tool, that silently rendered a training
    set off a nude image, and row 0011 of dorian-v1 went into training
    bare-chested.

    A character defaulting to `auto` is correct; the Klein 3.0 panel is where
    stripping is chosen. A TRAINING SET defaulting to it is not — a LoRA learns
    whatever is in the pixels. `stripped` and `auto` remain available and are
    recorded when chosen."""
    m = str(ds.get("base_mode") or "").strip().lower()
    return m if m in ("dressed", "stripped", "auto") else "dressed"


def _identity_preview(ds: dict) -> Dict[str, Any]:
    """Which image every view will actually start from, before anything renders.

    The whole class of bug this answers is "the render used a base nobody meant
    it to use", and it was previously only visible by reading `identity` on a
    finished row."""
    mode = _ds_base_mode(ds)
    out: Dict[str, Any] = {"base_mode": mode,
                           "base_mode_source": ("explicit" if ds.get("base_mode")
                                                else "default (v1.260: dressed)"),
                           "outfits": len(ds.get("outfits") or []),
                           "views": {}, "warnings": []}
    try:
        char = _load_char(ds["char_slug"])
    except Exception as e:  # noqa: BLE001
        out["warnings"].append(f"character not readable: {type(e).__name__}")
        return out
    wanted = sorted({_by_key(ANGLES, it["angle"])[3]
                     for it in ds.get("items", []) if it.get("angle")}) or ["front"]
    for view in wanted:
        try:
            fp, label = _base_for_view(ds["char_slug"], char, view, mode)
        except Exception as e:  # noqa: BLE001
            out["views"][view] = {"error": f"{type(e).__name__}: {e}"}
            continue
        lbl = str(label or "")
        # The label is the only honest signal here: `_base_for_view` writes
        # "stripped"/"nude" into it when that is what won.
        stripped = any(w in lbl.lower() for w in ("strip", "nude", "underwear"))
        out["views"][view] = {"file": fp.name if fp else None, "label": lbl,
                              "exists": bool(fp and fp.exists()),
                              "looks_stripped": stripped}
        if not fp:
            out["warnings"].append(f"{view}: no base or reference at all")
    if mode == "stripped" and not (ds.get("outfits") or []):
        out["warnings"].append(
            "base_mode is STRIPPED and no outfits are defined, so nothing in the render "
            "prompt says what he is wearing — every row will come out undressed. Define a "
            "wardrobe, or set base_mode to dressed.")
    if mode == "auto":
        out["warnings"].append(
            "base_mode is AUTO — the newest version of each view wins, which on a character "
            "with a stripped base means some rows render nude and some do not. This is what "
            "put a bare-chested image into dorian-v1. Choose dressed or stripped.")
    lose = [v for v, d in out["views"].items() if d.get("looks_stripped")]
    if lose and not (ds.get("outfits") or []):
        out["warnings"].append(
            f"the base for {', '.join(lose)} is a stripped version and no outfits are "
            f"defined — those rows will render undressed")
    return out


@router.get("/datasets/{ds_id}/identity-preview")
async def dataset_identity_preview(ds_id: str):
    """Which base every view will start from, and what is wrong with that.

    v1.260. Read-only, instant, and the thing that should be checked before a
    render rather than after a training run."""
    return _identity_preview(_read_ds(ds_id))


def _baseline_sets(ds: dict) -> Dict[str, Tuple[List[Any], List[str]]]:
    """Reference embeddings grouped by VIEW, so a profile can be scored against
    a profile.

    A real tagged reference beats a generated base every time: a generated base
    is itself a Klein render, and scoring renders against renders produces a
    beautiful number that means nothing."""
    out: Dict[str, Tuple[List[Any], List[str]]] = {"front": ([], []),
                                                   "left": ([], []),
                                                   "right": ([], [])}
    try:
        char = _load_char(ds["char_slug"])
    except Exception:  # noqa: BLE001
        return out
    slug = ds["char_slug"]
    picks: Dict[str, List[Tuple[Optional[Path], str]]] = {"front": [], "left": [], "right": []}

    fp, lbl = _base_for_view(slug, char, "front", _ds_base_mode(ds))
    picks["front"].append((fp, lbl))
    for tag in ("face",):
        refs = _refs_by_tag(char, tag)
        if refs:
            picks["front"].append((_cdir(slug) / "refs" / f"{refs[-1]['id']}.png",
                                   f"{tag} reference"))
    for side in ("left", "right"):
        refs = _refs_by_tag(char, side)
        if refs:
            picks[side].append((_cdir(slug) / "refs" / f"{refs[-1]['id']}.png",
                                f"{side} reference"))
        else:
            # No tagged side reference. The generated base is second best and
            # is labelled so the score can be read with that in mind.
            bp, blbl = _base_for_view(slug, char, side, _ds_base_mode(ds))
            if bp:
                picks[side].append((bp, f"{blbl} (generated)"))

    for view, plist in picks.items():
        embs, labels, seen = [], [], set()
        for p, lbl in plist:
            if not p or str(p) in seen or not Path(p).exists():
                continue
            seen.add(str(p))
            e = _like.embed(p)
            if e is None:             # a reference with no detectable face is
                continue              # useless as a baseline, not an error
            embs.append(e)
            labels.append(lbl)
            if len(embs) >= 3:
                break
        out[view] = (embs, labels)
    return out


def _baselines_for(sets: Dict[str, Tuple[List[Any], List[str]]],
                   angle: Optional[str]) -> Tuple[List[Any], List[str], str]:
    """The right baselines for one row, falling back to frontal when a side has
    none.  The fallback is NAMED in the label so a geometry-penalised score is
    never mistaken for a clean one."""
    want = _ANGLE_BASELINE.get(str(angle or "").lower(), "front")
    embs, labels = sets.get(want, ([], []))
    if embs:
        return embs, labels, want
    embs, labels = sets.get("front", ([], []))
    return embs, labels, (f"front (no {want} reference)" if want != "front" else "front")


def _identity_ref_png(ds: dict) -> Optional[bytes]:
    """The character's front reference, for the side-by-side identity check."""
    try:
        char = _load_char(ds["char_slug"])
        # QC compares against the SAME identity source the renders used, or it
        # would flag his real clothes as "not him" whenever the modes disagree.
        fp, _lbl = _base_for_view(ds["char_slug"], char, "front", _ds_base_mode(ds))
        return fp.read_bytes() if fp and fp.exists() else None
    except Exception:  # noqa: BLE001 — QC still works without it
        return None


def _flagged_ids(ds: dict, include_stuck: bool = False) -> List[str]:
    return [it["id"] for it in ds.get("items", [])
            if (it.get("qc") or {}).get("ok") is False
            and (include_stuck or int(it.get("attempts") or 0) < MAX_ATTEMPTS)]


def _render_jobs(ds: dict, char: dict, items: List[dict], seed0: int) -> List[dict]:
    jobs = []
    for n, it in enumerate(items):
        ang = _by_key(ANGLES, it["angle"])
        # v1.217: a dataset that renders his own wardrobe wants the DRESSED
        # base — stripping is an extra edit per view whose drift buys nothing
        # when the shot never needed the clothing replaced.
        _view = _base_view_for(it["angle"], ang[3],
                               (ds.get("options") or {}).get("tq_base", "side"))
        base, src_label = _base_for_view(ds["char_slug"], char, _view,
                                         _ds_base_mode(ds))
        if not base:
            raise HTTPException(409, "this character has no base image yet — strip or tag one "
                                     "in Klein 3.0 first")
        refs = [str(base)]
        # v1.235 "tworef": the side base rides along as image 2 so the prompt can
        # ask for halfway between two pictures instead of describing an angle.
        # Only for three-quarter rows, and only when the front base is the one
        # in slot 1 — otherwise there is no pair to be halfway between.
        side_idx = None
        if (it["angle"] in _TQ_ANGLES and _tq_mode(ds, it["angle"]) == "tworef"
                and _view == "front"):
            _sb, _ = _base_for_view(ds["char_slug"], char, ang[3], _ds_base_mode(ds))
            if _sb and Path(_sb).exists():
                refs.append(str(_sb))
                side_idx = len(refs)     # 1-based, matching the prompt text
        face = _refs_by_tag(char, "face")
        # v1.249: which framings get it is an OPTION now. Denying it to `upper`
        # and `full` is where redv1's identity collapsed — those rows see only a
        # wide base in which the face is a twelfth of the frame.
        if _wants_face_ref(ds, it["framing"]) and face:
            fp = _cdir(ds["char_slug"]) / "refs" / f"{face[-1]['id']}.png"
            if fp.exists():
                refs.append(str(fp))     # close-ups get the face reference too
        # A garment reference only earns its slot when the shot can show the
        # clothes — on a face crop it would just compete with the identity refs.
        # (_run_klein_edit_on loads up to 5 and picks the matching NREF workflow.)
        g_idx = None
        o = _outfit_for(ds, it)
        if o and o.get("ref_id") and _OUTFIT_VIS.get(it["framing"], "full") != "none":
            gp = _cdir(ds["char_slug"]) / "refs" / f"{o['ref_id']}.png"
            if gp.exists() and len(refs) < 5:
                refs.append(str(gp))
                g_idx = len(refs)        # 1-based: the prompt says "image N"
        jobs.append({"key": it["id"],
                     "prompt": _render_prompt(ds, it, g_idx, side_idx), "refs": refs,
                     "w": it.get("width", 896), "h": it.get("height", 1152),
                     "seed": seed0 + n, "identity": src_label,
                     # The RESOLVED wording, so an "auto" dataset still records
                     # which sentence actually made each image.
                     "tq_wording": (_tq_mode(ds, it["angle"])
                                    if it["angle"] in _TQ_ANGLES else None),
                     # Stamped so a dataset holding both variants can be scored.
                     "face_ref": _wants_face_ref(ds, it["framing"])})
    return jobs


def _render_blocking(ds_id: str, disp, jobs: List[dict], st: dict) -> None:
    """Fan the render jobs across every klein worker and land the results.
    Blocking — call it from a background thread."""
    def on_result(jb, data):
        _save_png_bytes(data, _item_path(ds_id, jb["key"]))
        # v1.223: this whole read-modify-write must be atomic.  Without the lock
        # a second worker finishing mid-sequence read the pre-update file and
        # wrote it back, discarding this image's status, attempts, identity and
        # caption — while the PNG stayed on disk, so nothing looked wrong until
        # a re-plan deleted every row it thought had never rendered.
        with _DS_WRITE_LOCK:
            cur = _read_ds(ds_id)
            for it in cur["items"]:
                if it["id"] == jb["key"]:
                    it["status"] = "done"
                    it["identity"] = jb.get("identity")
                    # v1.235: which wording rendered THIS image.  Without it a
                    # dataset holding two variants' output cannot be scored,
                    # and the A/B is unreadable the moment anything is re-run.
                    if jb.get("tq_wording"):
                        it["tq_wording"] = jb["tq_wording"]
                    it["face_ref_used"] = bool(jb.get("face_ref"))
                    it["seed"] = jb.get("seed")
                    it["attempts"] = int(it.get("attempts") or 0) + 1
                    if not (it.get("caption") or "").strip():
                        it["caption"] = _caption(cur, it)
            _write_ds(cur)
        st["done"] = sum(1 for t in st.get("tasks", {}).values()
                         if t.get("status") == "done") + 1
        st["detail"] = f"{st['done']}/{len(jobs)}"

    _parallel_klein_edits(disp, jobs, on_result, st)


def _qc_blocking(ds_id: str, item_ids: List[str], urls: List[str], vision_model: str,
                 st: dict, ref_png: Optional[bytes] = None,
                 baselines: Optional[List[Any]] = None,
                 baseline_sets: Optional[Dict[str, Any]] = None) -> None:
    # ref_png is accepted and IGNORED since v1.224 — kept so the two callers do
    # not need touching, and so an old caller cannot silently reintroduce it.
    ref_png = None
    """Vision QC over the given images, one thread per Ollama server.  Blocking."""
    # v1.243: the dataset's own framing calibration, read ONCE. A per-image
    # thread cannot compute this — it needs every image's face height — so a
    # dataset that has never been through `/angles` falls back to the
    # one-character default bands, and each note says which was used.
    try:
        _framing_cal = (_read_ds(ds_id) or {}).get("framing_cal") or None
    except Exception:  # noqa: BLE001 — QC must not die for want of a calibration
        _framing_cal = None
    import queue as _q
    import threading as _th
    from backend.services.character_studio.vnccs_native import wizards as _wiz
    qq: Any = _q.Queue()
    for pid in item_ids:
        qq.put(pid)
    lock = _DS_WRITE_LOCK        # shared: repair interleaves render and QC
    st.setdefault("tasks", {})

    def _worker(url: str):
        short = str(url).replace("http://", "").replace("https://", "").rstrip("/")
        while True:
            try:
                iid = qq.get_nowait()
            except Exception:  # noqa: BLE001
                return
            st["tasks"][iid] = {"server": short, "status": "running"}
            try:
                cur = _read_ds(ds_id)
                item = next(x for x in cur["items"] if x["id"] == iid)
                # v1.224: ONE image. Sending the character's reference alongside
                # made the model judge framing against the REFERENCE's framing —
                # 30 of 40 images failed framing on complaints like "body build
                # differs" and "body not visible", on face crops. ArcFace owns
                # identity now, so the reference buys nothing and costs that.
                imgs = [_wiz.image_bytes_to_b64(_item_path(ds_id, iid).read_bytes())]
                raw = _wiz.ollama_chat_sync(
                    [url], vision_model, _QC_SYSTEM,
                    _qc_prompt(item, outfit=_outfit_text(cur, item)),
                    imgs, 0.1, 180.0, True)
                data = json.loads(raw) if raw else {}
                # v1.241: `framing_ok` and `cropped_badly` are gone. Measured
                # 0 of 12 on images verified by eye, twice, and the second time
                # was AFTER a prompt written specifically to fix it. `angle_ok`
                # is no longer parsed either — head yaw owns it since v1.234 and
                # reading a second opinion in only invited it to win a race.
                flags = {k: bool(data.get(k)) for k in
                         ("expression_ok", "one_person", "face_clear", "artifacts")}

                # default TRUE: a model that omitted the key must not fail the
                # image, and a shot with no visible outfit was never asked.
                flags["outfit_ok"] = bool(data.get("outfit_ok", True))
                # v1.218/v1.224: the identity NUMBER comes from ArcFace, and the
                # vision model is no longer asked about identity at all.
                # A vision model rating identity 0-1 clusters at 0.85-0.95 and
                # is not on the scale Fizgig's cutoff expects.
                # v1.234: head yaw, measured.  Runs on the same already-loaded
                # CPU model as the identity score, so it costs one extra face
                # detection and no GPU at all.
                _pv = _like.pose(_item_path(ds_id, iid))
                _aok, _awhy = _like.angle_verdict(item.get("angle"), _pv)
                _fok, _fwhy = _like.framing_verdict(item.get("framing"),
                                                    item.get("angle"), _pv,
                                                    _framing_cal)
                # v1.242: framing is measured from the same face box. It was
                # advisory in v1.232 and gone in v1.241 because the VISION MODEL
                # could not do it; the objection was never to the check.
                # v1.246: crop, from a person mask, and it FAILS an image now.
                # v1.245 shipped it advisory on purpose; the probe then found
                # the rule wrong for close-ups (see subject.py) and the
                # corrected rule reads 20 of 20 on real images.
                _bx = _subj.box(_item_path(ds_id, iid)) if _subj.available() else None
                _cok, _cwhy = _subj.crop_verdict(item.get("framing"), _bx)
                flags["crop_note"] = _cwhy
                flags["body_h_ratio"] = None if not _bx else _bx.get("body_h_ratio")
                flags["crop_ok"] = True if _cok is None else bool(_cok)
                flags["crop_method"] = "unmeasured" if _cok is None else "person-mask"
                flags["framing_note"] = _fwhy
                flags["face_h_ratio"] = None if not _pv else _pv.get("face_h_ratio")
                if _fok is None:
                    flags["framing_ok"] = True
                    flags["framing_method"] = "unmeasured"
                else:
                    flags["framing_ok"] = bool(_fok)
                    flags["framing_method"] = "face-height"
                flags["angle_note"] = _awhy
                flags["yaw"] = None if not _pv else _pv.get("yaw")
                flags["yaw_detail"] = _pv
                if _aok is None:
                    # Unmeasured is not failed.  The vision model's answer is
                    # known noise on exactly these rows, so it does not get to
                    # stand in for a measurement that did not happen.
                    flags["angle_ok"] = True
                    flags["angle_method"] = "unmeasured"
                else:
                    flags["angle_ok"] = bool(_aok)
                    flags["angle_method"] = "head-yaw"
                # v1.247: scored against the baselines that MATCH how this row
                # was shot. A profile against a frontal baseline scores low for
                # looking sideways, which is geometry and not identity.
                _bl, _bl_lbl, _bl_key = (
                    _baselines_for(baseline_sets, item.get("angle"))
                    if baseline_sets else (baselines or [], [], "front"))
                flags["identity_baseline"] = _bl_key
                flags["identity_baseline_n"] = len(_bl)
                flags["identity_baseline_labels"] = _bl_lbl
                arc = _like.score(_item_path(ds_id, iid), _bl) if _bl else None
                flags["identity_score"] = None if arc is None else round(arc, 4)
                # v1.219: keyed on whether ArcFace RAN, not on whether it found
                # a face.  The old form made `arcface` imply a score, so the
                # no-face counter in _flag_summary was unreachable by
                # construction — a back shot and a missing model looked alike.
                flags["identity_method"] = ("arcface" if _bl
                                            else ("vision-llm" if ref_png else "none"))
                if arc is not None:
                    flags["identity_verdict"] = _like.verdict(arc)[0]
                    # v1.221: a BACK row cannot be judged this way.  The
                    # baselines are frontal, so whatever sliver of face a back
                    # shot shows scores low by GEOMETRY — measured, all three
                    # "not him" images in his first real scan were back-based,
                    # median 0.125 against 0.44-0.71 everywhere else.  The score
                    # is kept (it is a real "unusual look" signal, and that is
                    # precisely what Fizgig's LR warm-up consumes) but it stops
                    # being a verdict on whether the character is right.
                    _is_back = item.get("angle") == "back"
                    flags["identity_scored_against_front"] = not _is_back
                    flags["same_person"] = (True if _is_back
                                            else arc >= _like.ARC_DIFFERENT)
                # v1.262: is he dressed?  Two passes, and BARE if either says
                # so — a false positive costs one re-render, a false negative
                # costs a training run. This lives here rather than in its own
                # route because QC overwrites `qc` wholesale, so a verdict
                # written anywhere else is erased by the next repair round.
                _wpass = []
                for _ in range(2):
                    try:
                        _wt = _wiz.ollama_chat_sync([url], vision_model, _ENRICH_SYSTEM,
                                                    _ENRICH_PROMPT, imgs, 0.2, 120.0, False)
                    except Exception:  # noqa: BLE001 — QC must not die for this
                        _wt = None
                    if _wt and _wt.strip():
                        _wpass.append(_wt.strip().strip('"').split("\n")[0][:220])
                _wv = _ward.verdict(_wpass)
                _seen = _wpass[0] if _wpass else None
                flags["bare"] = _wv["bare"]
                flags["bare_words"] = _wv.get("words") or []
                flags["wardrobe_method"] = _wv["method"]
                flags["wardrobe_why"] = _wv["why"]
                issues = [str(x)[:120] for x in (data.get("issues") or [])][:6]
                if _wv["bare"] is True:
                    issues = [f"he is undressed — {_wv['why']}"] + issues
                if _aok is False:
                    issues = [f"angle: {_awhy}"] + issues
                if _fok is False:
                    issues = [f"framing: {_fwhy}"] + issues
                if _cok is False:
                    issues = [f"crop: {_cwhy}"] + issues
                # After `issues` exists — the first cut of this referenced it
                # above its own definition and pyflakes caught it pre-ship.
                if arc is None and not _bl:
                    # No objective scorer available. Say so rather than leaving a
                    # silent gap where an identity verdict used to be.
                    flags["identity_note"] = ("not checked — install insightface "
                                              "for objective identity scoring")
                if arc is not None and arc < _like.ARC_MATCH:
                    _tag = ("likeness {v} ({a:.2f})" if flags.get(
                        "identity_scored_against_front", True)
                        else "likeness {v} ({a:.2f}) — back shot, frontal baselines, "
                             "not an identity verdict")
                    issues = [_tag.format(v=flags["identity_verdict"], a=arc)] + issues
                # v1.242: framing is back in, because it is MEASURED now.
                # An unmeasured framing (a back row) defaults True and never
                # fails an image, same contract as angle.
                ok = (flags["one_person"]
                      and not flags["artifacts"]
                      and flags.get("same_person", True)
                      and flags.get("outfit_ok", True)
                      and flags.get("framing_ok", True)
                      and flags.get("crop_ok", True)
                      # v1.262. `is not True` on purpose: unmeasured never fails
                      # an image, the same contract as angle, framing and crop.
                      and flags.get("bare") is not True)
                with lock:
                    cur = _read_ds(ds_id)
                    for x in cur["items"]:
                        if x["id"] == iid:
                            x["qc"] = {"ok": ok, "checked_at": _now(),
                                       "server": short, **flags, "issues": issues}
                            # Kept OUTSIDE qc so a re-check does not throw away
                            # the description the caption pass reuses.
                            if _seen:
                                x["seen_clothing"] = _seen
                    _write_ds(cur)
                st["tasks"][iid]["status"] = "done"
            except Exception as e:  # noqa: BLE001
                st["tasks"][iid] = {"server": short, "status": "error", "error": str(e)[:160]}
                logger.warning("lora qc[%s/%s] failed: %s", ds_id, iid, e)
            st["done"] = st.get("done", 0) + 1
            st["detail"] = f"{st['done']}/{len(item_ids)}"

    threads = [_th.Thread(target=_worker, args=(u,), daemon=True) for u in urls]
    for t in threads:
        t.start()
    for t in threads:
        t.join()


# ══ routes: dataset CRUD ═════════════════════════════════════════════════════
@router.get("/likeness-health")
async def likeness_health():
    """Whether objective identity scoring is available on this host.

    Threaded: `health()` loads the model to answer honestly, and the first call
    downloads ~300MB. A health check must never pin the event loop."""
    import asyncio
    return await asyncio.to_thread(_like.health)


@router.get("/health")
async def health(request: Request, session: AsyncSession = Depends(get_session)):
    disp = _dispatcher(request)
    try:
        from backend.api.klein3 import _klein_workers_all
        workers = [{"url": u} for u, _c in _klein_workers_all(disp)]
    except Exception:  # noqa: BLE001
        workers = []
    vision = None
    try:
        from backend.api.vnccs_native import _ollama_cfg
        urls, _t, vision_model = await _ollama_cfg(session)
        vision = {"servers": len(urls or []), "model": vision_model}
    except Exception as e:  # noqa: BLE001
        vision = {"error": str(e)[:200]}
    return {"ok": True, "klein_workers": workers, "vision": vision,
            "datasets": len(list(_DS_ROOT.glob("*/dataset.json"))) if _DS_ROOT.exists() else 0,
            "root": str(_DS_ROOT)}


@router.get("/datasets")
async def datasets_list():
    out = []
    if _DS_ROOT.exists():
        for fp in sorted(_DS_ROOT.glob("*/dataset.json")):
            try:
                ds = json.loads(fp.read_text("utf-8"))
            except Exception:  # noqa: BLE001
                continue
            items = ds.get("items", [])
            out.append({
                "id": ds.get("id"), "name": ds.get("name"), "char_slug": ds.get("char_slug"),
                "trigger": ds.get("trigger"), "class_token": ds.get("class_token"),
                "target": ds.get("target"), "created_at": ds.get("created_at"),
                "total": len(items),
                "rendered": sum(1 for i in items if i.get("status") == "done"),
                "captioned": sum(1 for i in items if (i.get("caption") or "").strip()),
                "flagged": sum(1 for i in items if (i.get("qc") or {}).get("ok") is False),
                "exports": [f.name for f in sorted((fp.parent / "exports").glob("*.zip"))]
                if (fp.parent / "exports").exists() else [],
            })
    out.sort(key=lambda d: d.get("created_at") or "", reverse=True)
    return {"datasets": out}


@router.post("/datasets")
async def dataset_create(body: DatasetIn):
    char = _load_char(body.char_slug)          # 404s on an unknown character
    name = body.name.strip() or "dataset"
    ds_id = f"{_slugify(name)[:40]}-{uuid4().hex[:6]}"
    trigger = (body.trigger or "").strip() or f"rbmn{_slugify(name)[:8].replace('-', '')}"
    ds = {
        "id": ds_id, "name": name, "char_slug": body.char_slug,
        "char_name": char.get("name", body.char_slug),
        "trigger": trigger, "class_token": (body.class_token or "person").strip(),
        "target": body.target, "outfit": body.outfit.strip(),
        "outfits": _norm_outfits({"outfits": body.outfits, "outfit": body.outfit}),
        "base_mode": (body.base_mode or None),
        "options": {**(body.options or {}), "preset": body.preset},
        "preset": body.preset, "created_at": _now(),
    }
    outfits = ds["outfits"]
    count = body.count if body.count is not None else (
        _suggested_count(len(outfits)) if outfits else 40)
    count = max(8, min(int(count), 120))
    ds["count"] = count
    # v1.219: `outfits` NEVER reached the planner — the whole wardrobe feature
    # was inert through this route.  _plan_opts is the single builder now.
    ds["items"] = _build_plan(count, _plan_opts(ds))
    _write_ds(ds)
    logger.info("lora dataset created: %s (%d planned images, %d outfits)",
                ds_id, len(ds["items"]), len(outfits))
    out = _public(ds)
    out["warnings"] = _plan_warnings(count, outfits)
    return out


def _last_activity(ds: dict) -> dict:
    """What actually happened to this dataset, read from the DATA.

    v1.231: `_RUNS` is in-memory and does not survive a restart, so it could not
    answer "did my QC run?" — it could only answer "is one running in THIS
    process". Every checked item stamps `qc.checked_at`; that is the durable
    record and nothing was reading it."""
    from datetime import datetime, timezone
    stamps = [str((it.get("qc") or {}).get("checked_at") or "")
              for it in ds.get("items", []) if (it.get("qc") or {}).get("checked_at")]
    out: Dict[str, Any] = {"qc_last": max(stamps) if stamps else None,
                           "qc_count": len(stamps),
                           "rendered": sum(1 for it in ds.get("items", [])
                                           if it.get("status") == "done")}
    if stamps:
        try:
            t = datetime.fromisoformat(max(stamps))
            if t.tzinfo is None:
                t = t.replace(tzinfo=timezone.utc)
            out["qc_age_s"] = int((datetime.now(timezone.utc) - t).total_seconds())
        except (ValueError, TypeError):
            out["qc_age_s"] = None
    # how many of the most recent batch — a targeted run only re-stamps its own
    # rows, so "12 checked 40 seconds ago" is the shape of a subset QC pass
    if stamps:
        newest = max(stamps)[:16]          # to the minute
        out["qc_last_batch"] = sum(1 for s in stamps if s[:16] == newest)
    return out


def _public(ds: dict) -> dict:
    items = []
    for it in ds.get("items", []):
        items.append({**it, "url": f"/api/lora/datasets/{ds['id']}/items/{it['id']}/image",
                      "has_image": _item_path(ds["id"], it["id"]).exists()})
    return {**ds, "items": items, "run": _RUNS.get(ds["id"]) or None,
            "last_activity": _last_activity(ds),
            "flags": _flag_summary(ds), "max_attempts": MAX_ATTEMPTS,
            "exports": [f.name for f in sorted((_ds_dir(ds["id"]) / "exports").glob("*.zip"))]
            if (_ds_dir(ds["id"]) / "exports").exists() else []}


@router.get("/datasets/{ds_id}")
async def dataset_get(ds_id: str):
    return _public(_read_ds(ds_id))


@router.post("/datasets/{ds_id}/plan")
async def dataset_plan(ds_id: str, body: PlanIn):
    """Re-plan.  Rendered images survive when their slot still exists, so a small
    count change costs only the new rows."""
    ds = _read_ds(ds_id)
    if body.outfit is not None:
        ds["outfit"] = body.outfit.strip()
    if body.outfits is not None:
        ds["outfits"] = _norm_outfits({"outfits": body.outfits})
    if body.options is not None:
        # v1.222: MERGE.  Replacing meant a caller that omitted `preset` silently
        # re-cut the entire shot list and the route then deleted every image
        # whose slot moved.  Omitting a key now means "leave it alone".
        ds["options"] = {**(ds.get("options") or {}), **(body.options or {})}
    # `preset` lives in two places; whichever the caller gave wins, and if
    # neither did, the STORED one survives rather than defaulting to balanced.
    _p = (body.options or {}).get("preset") if body.options else None
    if _p:
        ds["preset"] = _p
    elif ds.get("preset"):
        ds.setdefault("options", {})["preset"] = ds["preset"]
    if body.count is not None:
        count = int(body.count)
    elif body.outfits is not None and ds.get("outfits"):
        # the wardrobe just changed and no count was given — size it to fit
        count = _suggested_count(len(ds["outfits"]))
    else:
        count = len(ds.get("items", [])) or 40
    count = max(8, min(count, 120))
    ds["count"] = count
    old = {it["id"]: it for it in ds.get("items", [])}
    fresh = _build_plan(count, _plan_opts(ds))
    impact = _plan_impact(ds, fresh)
    if impact["discarded"] and not body.force:
        raise HTTPException(409, "this re-plan would DISCARD "
                                 f"{impact['discarded']} rendered image(s) "
                                 f"(keeping {impact['kept']} of "
                                 f"{impact['rendered_before']}). "
                                 + (f"Angles changing: {'; '.join(impact['angle_changes'])}. "
                                    if impact["angle_changes"] else "")
                                 + "That is real GPU time. Re-send with force=true if you "
                                   "mean it, or check that `preset` and `options` match what "
                                   "the dataset already had.")
    for it in fresh:
        prev = old.get(it["id"])
        # same rule as _plan_impact: an image on disk counts as rendered even if
        # the race ate its status field
        if prev and (prev.get("status") == "done"
                     or _item_path(ds_id, it["id"]).exists()) and all(
                prev.get(k) == it.get(k) for k in ("framing", "angle", "expression", "pose",
                                                   "lighting", "background")):
            it.update({"status": "done", "caption": prev.get("caption", ""),
                       "qc": prev.get("qc"), "caption_extra": prev.get("caption_extra", "")})
    for it in fresh:                     # drop images whose slot changed
        if it.get("status") != "done":
            _item_path(ds_id, it["id"]).unlink(missing_ok=True)
    for stale in set(old) - {i["id"] for i in fresh}:
        _item_path(ds_id, stale).unlink(missing_ok=True)
    ds["items"] = fresh
    _write_ds(ds)
    out = _public(ds)
    out["warnings"] = _plan_warnings(count, ds.get("outfits") or [])
    out["impact"] = impact
    return out


@router.post("/datasets/{ds_id}/delete")
async def dataset_delete(ds_id: str):
    import shutil
    d = _ds_dir(ds_id)
    if not d.exists():
        raise HTTPException(404, "dataset not found")
    shutil.rmtree(d, ignore_errors=True)
    _RUNS.pop(ds_id, None)
    return {"deleted": ds_id}


# ══ generation ═══════════════════════════════════════════════════════════════
@router.post("/datasets/{ds_id}/generate")
async def dataset_generate(ds_id: str, body: ItemsIn, request: Request):
    """Render the planned images — one Klein edit per row, fanned across every
    klein-capable worker with live per-image worker/status."""
    ds = _read_ds(ds_id)
    if (_RUNS.get(ds_id) or {}).get("status") == "running":
        raise HTTPException(409, "a generation run is already going for this dataset")
    char = _load_char(ds["char_slug"])
    sel = set(body.item_ids or [])
    todo = [it for it in ds["items"]
            if (not sel or it["id"] in sel)
            and (body.overwrite or not _item_path(ds_id, it["id"]).exists())]
    if not todo:
        return {"started": False, "note": "every selected image is already rendered "
                                          "(tick overwrite to redo them)"}
    disp = _dispatcher(request)
    _wk, client = _klein_worker(disp)
    if not client:
        raise HTTPException(409, "No klein-capable worker online.")
    seed0 = random.randint(1, 2_000_000_000)
    jobs = _render_jobs(ds, char, todo, seed0)
    st = {"status": "running", "kind": "generate", "done": 0, "total": len(jobs),
          "detail": f"0/{len(jobs)}"}
    _RUNS[ds_id] = st

    def _run():
        try:
            _render_blocking(ds_id, disp, jobs, st)
            errs = [t.get("error") for t in (st.get("tasks") or {}).values()
                    if t.get("status") == "error" and t.get("error")]
            st["error"] = "; ".join(errs[:3]) if errs else None
            st["status"] = "done" if not errs else "done_with_errors"
        except Exception as e:  # noqa: BLE001
            st.update({"status": "error", "error": f"{type(e).__name__}: {e}"})

    _spawn(_run)
    logger.info("lora generate[%s]: %d image(s)", ds_id, len(jobs))
    return {"started": True, "total": len(jobs)}


# ══ captions ═════════════════════════════════════════════════════════════════
_ENRICH_SYSTEM = (
    "You write short factual captions for image-model training data. You report only "
    "clothing, background and lighting that you can see."
)
_ENRICH_PROMPT = """Look at this photograph and list ONLY:
1. the clothing and accessories the person is wearing,
2. what is visible in the background.

Reply as one short comma-separated phrase, for example:
"a navy blue hoodie and jeans, a brick wall behind him"

Write nothing about the person's face, hair, body build, height, weight, age or sex —
those belong to the character itself and are deliberately left out of these captions."""


@router.post("/datasets/{ds_id}/wardrobe-check")
async def dataset_wardrobe_check(ds_id: str, body: ItemsIn,
                                 session: AsyncSession = Depends(get_session)):
    """Look at every rendered image and answer one question: is he dressed?

    v1.261. Two passes per image at the same low temperature, and a row counts
    as bare if EITHER pass says so — a false positive costs one re-render, a
    false negative costs a training run.

    A bare row is marked `qc.ok = False` with a plain-English issue, which puts
    it in front of the repair loop that already exists. With v1.260 resolving
    datasets to the dressed base, repairing it actually fixes it.

    The description is kept on the row as `seen_clothing`, so the caption pass
    can use it without paying for a second look."""
    ds = _read_ds(ds_id)
    sel = set(body.item_ids or [])
    from backend.api.vnccs_native import _ollama_cfg
    from backend.services.character_studio.vnccs_native import wizards as _wiz
    urls, _t, vision_model = await _ollama_cfg(session)
    if not urls or not vision_model:
        raise HTTPException(503, "Ollama vision model is not configured "
                                 "(Settings -> Ollama vision model).")
    import asyncio
    checked = bare = failed = 0
    rows: List[Dict[str, Any]] = []
    for it in ds["items"]:
        if sel and it["id"] not in sel:
            continue
        fp = _item_path(ds_id, it["id"])
        if not fp.exists():
            continue
        blob = fp.read_bytes()
        passes = []
        for _ in range(2):
            try:
                out = await asyncio.to_thread(
                    _wiz.ollama_chat_sync, urls, vision_model, _ENRICH_SYSTEM,
                    _ENRICH_PROMPT, [_wiz.image_bytes_to_b64(blob)], 0.2, 120.0, False)
            except Exception as e:  # noqa: BLE001
                logger.warning("wardrobe[%s/%s] failed: %s", ds_id, it["id"], e)
                out = None
            if out and out.strip():
                passes.append(out.strip().strip('"').split("\n")[0][:220])
        v = _ward.verdict(passes)
        q = it.setdefault("qc", {})
        q["bare"] = v["bare"]
        q["bare_words"] = v.get("words") or []
        q["wardrobe_method"] = v["method"]
        q["wardrobe_why"] = v["why"]
        if passes:
            it["seen_clothing"] = passes[0]
            checked += 1
        else:
            failed += 1
        if v["bare"] is True:
            bare += 1
            q["ok"] = False
            issue = f"he is undressed — {v['why']}"
            q["issues"] = [i for i in (q.get("issues") or []) if "undressed" not in i]
            q["issues"].insert(0, issue)
        else:
            # A row that was flagged ONLY for being undressed and now is not
            # must stop being flagged, or the repair loop chases it forever.
            olds = [i for i in (q.get("issues") or []) if "undressed" in i]
            if olds:
                q["issues"] = [i for i in q["issues"] if "undressed" not in i]
                if not q["issues"] and q.get("ok") is False:
                    q["ok"] = True
        rows.append({"id": it["id"], "framing": it.get("framing"),
                     "angle": it.get("angle"), **v,
                     "seen": passes[0] if passes else None})
    _write_ds(ds)
    logger.info("lora wardrobe[%s]: %d checked, %d bare, %d unreadable",
                ds_id, checked, bare, failed)
    return {"checked": checked, "bare": bare, "unreadable": failed,
            "summary": _ward.summarise([r for r in rows]),
            "rows": rows}


@router.post("/datasets/{ds_id}/caption")
async def dataset_caption(ds_id: str, body: CaptionIn,
                          session: AsyncSession = Depends(get_session)):
    """Write the captions.

    The plan already KNOWS what each image was asked to be (framing, angle,
    expression, pose, lighting, background), so captions are composed from it —
    consistent wording across the whole set, which is what a trainer wants.
    `enrich` additionally asks the vision model for the clothing/background it
    can actually see, and appends that."""
    ds = _read_ds(ds_id)
    sel = set(body.item_ids or [])
    targets = [it for it in ds["items"]
               if (not sel or it["id"] in sel)
               and (body.overwrite or not (it.get("caption") or "").strip())]
    if not targets:
        return {"captioned": 0, "note": "every selected image already has a caption"}
    enriched = 0
    if body.enrich:
        from backend.api.vnccs_native import _ollama_cfg
        from backend.services.character_studio.vnccs_native import wizards as _wiz
        urls, _t, vision_model = await _ollama_cfg(session)
        if not urls or not vision_model:
            raise HTTPException(503, "Ollama vision model is not configured "
                                     "(Settings -> Ollama vision model).")
        import asyncio
        for it in targets:
            fp = _item_path(ds_id, it["id"])
            if not fp.exists():
                continue
            try:
                out = await asyncio.to_thread(
                    _wiz.ollama_chat_sync, urls, vision_model, _ENRICH_SYSTEM, _ENRICH_PROMPT,
                    [_wiz.image_bytes_to_b64(fp.read_bytes())], 0.2, 120.0, False)
            except Exception as e:  # noqa: BLE001
                logger.warning("lora enrich[%s/%s] failed: %s", ds_id, it["id"], e)
                out = None
            if out and out.strip():
                it["caption_extra"] = out.strip().strip('"').split("\n")[0][:200]
                it["seen_clothing"] = it["caption_extra"]
                enriched += 1
    # v1.261: the wardrobe check already paid for a description of every image.
    # Reuse it rather than asking the model the same question twice — and it
    # means a caption says "grey boxer briefs" on the rows where that is the
    # truth, instead of silently omitting the most obvious thing in the frame.
    reused = 0
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
            reused += 1
    for it in targets:
        it["caption"] = _caption(ds, it)
    _write_ds(ds)
    return {"captioned": len(targets), "enriched": enriched, "reused_seen": reused}


# ══ QC ═══════════════════════════════════════════════════════════════════════
_QC_SYSTEM = ("You are a strict quality checker for image-model training data. "
              "You answer with JSON only.")

# Both lora-dataset-studio (InsightFace similarity) and Fizgig (Look Consistency
# Filter) score every image against the character before training, and they are
# right to: an off-identity image teaches the trigger word the wrong face, which
# is the one failure a character LoRA cannot survive.  This runs in the SAME
# vision call — image 1 is the character's reference, image 2 is the render.
_IDENTITY_LINE = """
IMAGE 1 is the reference photograph of the character. IMAGE 2 is the generated image
being checked. Add these keys to your JSON:
  "same_person": true/false   — is the person in image 2 the same individual as image 1
                                (face, hair, and especially BODY BUILD and stature)?
  "identity_score": 0.0-1.0   — how close the likeness is. 1.0 = indistinguishable,
                                0.7 = clearly him with small drift, 0.4 = related but off,
                                0.0 = a different person.
  "identity_note": "short phrase"  — if they differ, say how (for example "much slimmer",
                                "different face shape", "younger", "different hair").
Judge build and proportions as carefully as the face: a slimmer or taller version of him
is a different person for this purpose."""


# v1.224: the shot type is the TARGET, not a defect. The model kept listing
# "extreme close-up shot only shows the face" as an ISSUE on a row whose whole
# purpose was to be an extreme close-up.
_FRAMING_NOTE = (
    "\n\nThis is the ONLY image. Judge it on its own — there is nothing to compare it "
    "against."
    "\n\nThe shot type and the angle above are context, NOT things to check. They are "
    "measured separately and you must not comment on them: say nothing about how close or "
    "far the camera is, whether the whole body is visible, whether anything is cut off by "
    "the edge, or which way the person is facing. A close-up showing no body is correct. "
    "Say nothing about the person's build, weight, height or proportions either — a single "
    "image cannot support that judgement."
    "\n\nAnswer only what you were asked for, and put nothing in \"issues\" that is not "
    "one of those things.")


def _qc_prompt(item: dict, outfit: str = "") -> str:
    fr = _by_key(FRAMINGS, item["framing"])
    ang = _by_key(ANGLES, item["angle"])
    # Only asked about when the shot can show it — otherwise every face crop
    # would be flagged for an outfit it was never meant to contain.
    o_line = (f'\nHe was also supposed to be wearing {outfit}. Add a key '
              f'"outfit_ok": true/false — false only if he is clearly wearing '
              f'something else.' if outfit else "")
    if item["angle"] == "back":
        # The face is hidden BY DESIGN here; without this the checker flags every
        # back shot for an unclear face and an unreadable expression.
        return f"""This image was generated for a character training set. It was supposed to be
{fr[2]}, {ang[1]} — the person's back to the camera, face deliberately hidden.

Answer with JSON only, exactly these keys:
{{"one_person": true/false, "artifacts": true/false,
  "issues": ["short phrase", ...]}}{o_line}

"artifacts" means deformed hands, extra or missing limbs, melted features or garbled text.
His face is hidden on purpose here; that is correct and is not a problem.""" \
            + _FRAMING_NOTE
    ex = _by_key(EXPRESSIONS, item["expression"])
    return f"""This image was generated for a character training set. It was supposed to be
{fr[2]}, {ang[1]}, with {ex[1]}.

Answer with JSON only, exactly these keys:
{{"expression_ok": true/false, "one_person": true/false, "face_clear": true/false,
  "artifacts": true/false, "issues": ["short phrase", ...]}}

"artifacts" means deformed hands, extra or missing limbs, melted features, garbled text or
similar defects. "expression_ok" is whether his face carries the expression named above.
List every problem you see in "issues".{o_line}""" + _FRAMING_NOTE


@router.post("/datasets/{ds_id}/qc")
async def dataset_qc(ds_id: str, body: ItemsIn, session: AsyncSession = Depends(get_session)):
    """Vision-model QC over the rendered images — framing / angle / expression
    fidelity plus anatomy and crop defects.  Everything that fails lands in the
    gallery with a flag so a bad image never reaches the trainer silently."""
    ds = _read_ds(ds_id)
    if (_RUNS.get(ds_id) or {}).get("status") == "running":
        raise HTTPException(409, "a run is already going for this dataset")
    from backend.api.vnccs_native import _ollama_cfg
    urls, _t, vision_model = await _ollama_cfg(session)
    if not urls or not vision_model:
        raise HTTPException(503, "Ollama vision model is not configured "
                                 "(Settings -> Ollama vision model).")
    sel = set(body.item_ids or [])
    targets = [it["id"] for it in ds["items"]
               if (not sel or it["id"] in sel)
               and _item_path(ds_id, it["id"]).exists()
               and (body.overwrite or not it.get("qc"))]
    if not targets:
        return {"started": False, "note": "nothing to check (rendered images with no QC yet)"}
    st = {"status": "running", "kind": "qc", "done": 0, "total": len(targets),
          "detail": f"0/{len(targets)}", "tasks": {}}
    _RUNS[ds_id] = st

    ref_png = _identity_ref_png(ds)

    def _run():
        try:
            # Built ONCE per run, and INSIDE the thread: embedding three
            # references (and, first time, downloading the model) on the event
            # loop froze the whole app before the QC pass even started.
            _sets = _baseline_sets(ds)
            _embs, _labels = _sets.get("front", ([], []))
            if _labels:
                cur0 = _read_ds(ds_id)
                cur0["likeness_baselines"] = _labels
                _write_ds(cur0)
            _qc_blocking(ds_id, targets, list(urls), vision_model, st, ref_png, _embs)
            st["status"] = "done"
        except Exception as e:  # noqa: BLE001
            st.update({"status": "error", "error": f"{type(e).__name__}: {e}"})

    _spawn(_run)
    return {"started": True, "total": len(targets), "servers": len(urls)}


class RepairIn(BaseModel):
    rounds: int = 3                  # render→QC cycles before stopping
    include_stuck: bool = False      # retry images that already hit MAX_ATTEMPTS
    qc_after: bool = True            # False = one re-render pass, no re-check


@router.post("/datasets/{ds_id}/repair")
async def dataset_repair(ds_id: str, body: RepairIn, request: Request,
                         session: AsyncSession = Depends(get_session)):
    """Re-render every FLAGGED image, re-check it, and repeat until the set is
    clean or the round cap is hit.

    Two brakes, because this spends renders while he is not watching: the round
    cap (<=6) and a per-image attempt counter — an image that fails three
    renders is a bad plan row, not bad luck, so it is parked as stuck and
    reported instead of re-rolled forever.  Each round re-seeds, otherwise a
    re-render reproduces the same picture."""
    ds = _read_ds(ds_id)
    if (_RUNS.get(ds_id) or {}).get("status") == "running":
        raise HTTPException(409, "a run is already going for this dataset")
    char = _load_char(ds["char_slug"])
    todo_ids = _flagged_ids(ds, body.include_stuck)
    if not todo_ids:
        return {"started": False, "note": "nothing is flagged"
                if not _flag_summary(ds)["stuck"]
                else "every remaining flag is on an image that already hit the attempt "
                     "limit — tick 'retry stuck' or fix those rows by hand"}
    disp = _dispatcher(request)
    _wk, client = _klein_worker(disp)
    if not client:
        raise HTTPException(409, "No klein-capable worker online.")
    urls: List[str] = []
    vision_model = None
    if body.qc_after:
        from backend.api.vnccs_native import _ollama_cfg
        u, _t, vision_model = await _ollama_cfg(session)
        urls = list(u or [])
        if not urls or not vision_model:
            raise HTTPException(503, "Ollama vision model is not configured — the repair loop "
                                     "needs it to re-check (or send qc_after=false).")
    rounds = max(1, min(int(body.rounds or 1), MAX_ROUNDS))
    st = {"status": "running", "kind": "repair", "round": 1, "rounds": rounds,
          "phase": "render", "done": 0, "total": len(todo_ids),
          "detail": f"round 1/{rounds} · re-rendering {len(todo_ids)}",
          "history": [], "tasks": {}}
    _RUNS[ds_id] = st

    def _run():
        try:
            ids = todo_ids
            for rnd in range(1, rounds + 1):
                cur = _read_ds(ds_id)
                items = [it for it in cur["items"] if it["id"] in set(ids)]
                if not items:
                    break
                st.update({"round": rnd, "phase": "render", "done": 0, "total": len(items),
                           "tasks": {}, "detail": f"round {rnd}/{rounds} · re-rendering {len(items)}"})
                jobs = _render_jobs(cur, char, items, random.randint(1, 2_000_000_000))
                _render_blocking(ds_id, disp, jobs, st)
                if not body.qc_after:
                    st["history"].append({"round": rnd, "rendered": len(items), "flagged": None})
                    break
                st.update({"phase": "qc", "done": 0, "total": len(items), "tasks": {},
                           "detail": f"round {rnd}/{rounds} · re-checking {len(items)}"})
                _qc_blocking(ds_id, [it["id"] for it in items], urls, vision_model, st,
                             _identity_ref_png(cur), None, _baseline_sets(cur))
                cur = _read_ds(ds_id)
                still = _flagged_ids(cur, body.include_stuck)
                st["history"].append({"round": rnd, "rendered": len(items),
                                      "flagged": len(still)})
                logger.info("lora repair[%s] round %d/%d: %d re-rendered, %d still flagged",
                            ds_id, rnd, rounds, len(items), len(still))
                if not still:
                    st["detail"] = f"clean after {rnd} round(s)"
                    break
                ids = still
                st["detail"] = f"round {rnd}/{rounds} done · {len(still)} still flagged"
            final = _flag_summary(_read_ds(ds_id))
            st["summary"] = final
            st["status"] = "done"
            if final["flagged"]:
                st["detail"] = (f"{final['flagged']} still flagged"
                                + (f" ({final['stuck']} at the attempt limit)"
                                   if final["stuck"] else ""))
        except Exception as e:  # noqa: BLE001
            st.update({"status": "error", "error": f"{type(e).__name__}: {e}"})

    _spawn(_run)
    logger.info("lora repair[%s]: %d flagged, up to %d round(s)", ds_id, len(todo_ids), rounds)
    return {"started": True, "total": len(todo_ids), "rounds": rounds}


# ══ per-item ═════════════════════════════════════════════════════════════════
@router.get("/datasets/{ds_id}/items/{item_id}/image")
async def item_image(ds_id: str, item_id: str):
    fp = _item_path(ds_id, item_id)
    if not fp.exists():
        raise HTTPException(404, "not rendered yet")
    return FileResponse(str(fp), media_type="image/png")


@router.post("/datasets/{ds_id}/items/{item_id}/update")
async def item_update(ds_id: str, item_id: str, body: ItemUpdateIn):
    ds = _read_ds(ds_id)
    it = next((x for x in ds["items"] if x["id"] == item_id), None)
    if it is None:
        raise HTTPException(404, "item not found")
    if body.caption is not None:
        it["caption"] = body.caption.strip()
    if body.caption_extra is not None:
        it["caption_extra"] = body.caption_extra.strip()
        it["caption"] = _caption(ds, it)
    if body.keep is not None:
        it["keep"] = bool(body.keep)
    _write_ds(ds)
    return {"updated": item_id, "caption": it.get("caption", "")}


@router.post("/datasets/{ds_id}/items/{item_id}/delete")
async def item_delete(ds_id: str, item_id: str):
    ds = _read_ds(ds_id)
    before = len(ds["items"])
    ds["items"] = [x for x in ds["items"] if x["id"] != item_id]
    if len(ds["items"]) == before:
        raise HTTPException(404, "item not found")
    _item_path(ds_id, item_id).unlink(missing_ok=True)
    _write_ds(ds)
    return {"deleted": item_id}


# ══ export ═══════════════════════════════════════════════════════════════════
def _training_notes(ds: dict, n_images: int) -> str:
    trig = ds.get("trigger", "sks")
    cls = ds.get("class_token", "person")
    steps = max(1000, min(n_images * 40, 3000))
    return f"""# {ds.get('name')} — LoRA training dataset

Character: {ds.get('char_name')}   ·   trigger: `{trig} {cls}`   ·   images: {n_images}
Built by RBMN Storyboard, LoRA Dataset Gen.

## What is in here
`images/` holds every image with its caption beside it as `<same name>.txt` — the layout
both ai-toolkit and kohya/FluxTrainer read directly. `dataset_aitoolkit.yaml` and
`dataset_kohya.toml` are ready-to-edit training configs. `manifest.json` records what each
image was planned to be, plus its QC result.

## How the captions are written
Every caption names ONLY what varies across the set: shot type, angle, expression, pose,
clothing, background and lighting. Nothing describes his face, hair or build — anything a
caption names becomes something the trainer treats as changeable, and anything it leaves out
is absorbed into the trigger `{trig}`. That is what makes the character come back on-model.

## Suggested starting settings
- steps: ~{steps} (roughly 40 per image, watch the samples and stop when likeness locks)
- rank / alpha: 16 (8-16 is where the Klein/Krea-scale trainers land; 32 only for a very
  distinctive face, and higher overfits)
- learning rate: 1e-4 (adamw8bit), batch size 1, gradient checkpointing on
- resolution buckets: 512 / 768 / 1024
- caption dropout: 0.05, shuffle tokens: off
- Sample every 250 steps with `{trig} {cls} standing in a park, full body` to watch likeness.

Target model noted for this set: **{ds.get('target')}**. Train the LoRA against the SAME base
checkpoint you will generate with — a LoRA trained on a different base drifts.

{_target_notes(ds.get('target'))}"""


# Verified 2026-08-04 (web + his own workflows/KREA2_*.json, which load
# krea2_turbo_mxfp8 + qwen3vl_4b_fp8_scaled + qwen_image_vae):
#   Krea 2 = a from-scratch 12.9B DiT by Krea AI. NOT Flux, NOT Qwen-Image —
#   it only borrows Qwen3-VL as text encoder and the Qwen-Image VAE.
#   RAW is the un-distilled checkpoint you train on; TURBO is the distilled one
#   you generate with (8 steps, CFG off), and Krea's own LoRAs are trained on
#   Raw then applied to Turbo. ostris/krea2_turbo_training_adapter is a
#   de-distillation adapter that lets you train directly ON Turbo and drop the
#   adapter at inference.
#   Trainer: ostris/ai-toolkit (or HF diffusers). ComfyUI-FluxTrainer is
#   Flux/kohya only and cannot train this.
_MODEL_BLOCKS = {
    "flux": """      model:
        name_or_path: "black-forest-labs/FLUX.1-dev"
        is_flux: true
        quantize: true""",
    "sdxl": """      model:
        name_or_path: "stabilityai/stable-diffusion-xl-base-1.0"
        is_xl: true""",
    "krea2": """      model:
        # ⚠ Krea 2 is its own 12.9B DiT (Qwen3-VL 4B text encoder + Qwen-Image
        # VAE) — it is NOT Flux, so no is_flux flag belongs here.
        # PASTE ai-toolkit's own Krea 2 model block below (its UI writes one) —
        # the arch key is deliberately left blank rather than guessed.
        name_or_path: "PUT_YOUR_KREA2_CHECKPOINT_HERE"   # RAW to train on
        # Training ON Turbo instead? add ostris/krea2_turbo_training_adapter
        # (de-distillation layer; remove it at inference).
        quantize: true""",
    "other": """      model:
        name_or_path: "PUT_YOUR_BASE_MODEL_HERE"
        quantize: true""",
}


def _target_notes(target: str) -> str:
    if target == "krea2":
        return """## Krea 2 specifics (verified 2026-08-04)

**Two trainers fit a small card, and both configs are in this zip.**

**Fizgig (shootthesound/Fizgig) — recommended.** Fully headless: its GUI only builds these
commands and runs them as subprocesses. `dataset_fizgig.toml` + `train_krea2_fizgig.txt` are
ready to run. `--quantize_4bit` puts the frozen base at ~5.6 GB with block swap OFF, and its
own docs are blunt about why that matters: *"Swapping is the slow path (4.4× the time, 4× the
CPU): quantise first, and only swap when even NF4 will not fit."* It also carries the features
nothing else has for Krea 2 — a per-image loss watch that classifies every image each epoch and
prints a **plateau banner with a best-checkpoint estimate**, per-image LR that throttles stuck
images, Qwen3-VL **auto-recaptioning** of images the loss convicts, and a **look-outlier
warm-up**. That last one reads `fizgig_look_scores.json`, which its docs say has no headless
generator — **so we write it**, from our own identity QC, into `images/`.

**kohya-ss/musubi-tuner** — official (experimental) Krea 2 support
(`krea2_train_network.py`, `networks.lora_krea2`); `dataset_musubi.toml` +
`train_krea2_musubi.txt`. Fits via fp8-scaled + block swap.

ai-toolkit also trains Krea 2 but is heavier (~18–20 GB at 768 for LoKr; its Krea2Trainer
wrapper targets 24 GB). ComfyUI-FluxTrainer cannot train Krea 2 at all, so `dataset_kohya.toml`
here is for a Flux/SDXL target only.

**Which card?** From Fizgig's measured planner (peaks are training-only; the budget is FREE
VRAM, not the number on the box):

| card | free | what runs |
|---|---|---|
| 12 GB | ~11 GB | NF4 does **not** fit (~13 GB needed) and NF4 cannot block-swap → fp8 + ~22 swapped blocks, the ~4× slower path. Workable, not pleasant. |
| **16 GB** | ~14.8 GB | **NF4 4-bit, no swap — the good tier.** ~0.70 s/it class. |
| 24 GB+ | ~22 GB | INT8 W8A8, no swap: fastest measured and ~7× more accurate than NF4. |

Measured on a 5090 (36 images @ 0.25 MP, batch 1): fp8 no-swap 0.85 s/it / 20.1 GB · fp8 swap-20
3.09 s/it / 12.3 GB · NF4 no-swap 0.70 s/it / 13.8 GB. **Resolution is not the VRAM lever** —
0.25 → 1.05 MP costs ~0.15 GB, while an extra batch image costs 2.4 GB.

Reported low-VRAM runs on the musubi route: **RTX 3060 12 GB — peak ~10.5 GB, 7.2–7.8 s/step at
512², rank 16, blocks_to_swap 22**; RTX 4070 12 GB — rank 32, blocks_to_swap 26, ~2 h for 2000
steps.
What makes it fit: pre-cached latents + pre-cached text-encoder outputs (the ~8 GB Qwen3-VL
encoder never enters the training loop), `--fp8_base --fp8_scaled` (K2 accepts SCALED fp8
only), block swap, gradient checkpointing. **Budget 32–64 GB of SYSTEM RAM** — swapped blocks
live there, and that is the requirement people miss.


Krea 2 is its OWN 12.9B diffusion transformer — not Flux, not Qwen-Image. It borrows a
**Qwen3-VL 4B text encoder** and the **Qwen-Image VAE** (which is exactly what the app's
`workflows/KREA2_*.json` load). Two consequences:

- **Trainer:** ostris/ai-toolkit (or HF diffusers). ComfyUI-FluxTrainer / kohya cannot train
  it — `dataset_kohya.toml` in this zip is for a Flux or SDXL target only.
- **Raw vs Turbo:** RAW is the un-distilled checkpoint meant for fine-tuning; TURBO is the
  distilled one you generate with (8 steps, CFG off). Krea's own LoRAs are trained on Raw and
  applied to Turbo. To train directly on Turbo instead, add ostris'
  `krea2_turbo_training_adapter` — a de-distillation layer you remove at inference — otherwise
  the distillation degrades as you train.
- **VRAM:** ~22 GB / ~6 h for 20 images on ai-toolkit + the Turbo adapter, versus ~10.5 GB on
  musubi-tuner with fp8 + block swap. A 16 GB card is comfortable on the musubi route and short
  on the ai-toolkit one.
"""
    if target == "flux":
        return """## FLUX specifics
Either trainer works: ai-toolkit with the config in this zip, or ComfyUI-FluxTrainer with
`dataset_kohya.toml` (TrainDatasetGeneralConfig -> TrainDatasetAdd -> InitFluxLoRATraining ->
FluxTrainLoop -> FluxTrainSave).
"""
    return ""


def _aitoolkit_yaml(ds: dict, n: int, resolution: List[int]) -> str:
    trig = ds.get("trigger", "sks")
    steps = max(1000, min(n * 40, 3000))
    res = ", ".join(str(r) for r in resolution)
    return f"""# ai-toolkit config for target: {ds.get('target')}
# Edit the model block and the folder path, then:
#   python run.py config/{ds.get('id')}.yaml
job: extension
config:
  name: "{ds.get('id')}"
  process:
    - type: 'sd_trainer'
      training_folder: "output"
      device: cuda:0
      trigger_word: "{trig}"
      network:
        type: "lora"
        linear: 32
        linear_alpha: 32
      save:
        dtype: float16
        save_every: 250
        max_step_saves_to_keep: 4
      datasets:
        - folder_path: "./images"
          caption_ext: "txt"
          caption_dropout_rate: 0.05
          shuffle_tokens: false
          cache_latents_to_disk: true
          resolution: [{res}]
      train:
        batch_size: 1
        steps: {steps}
        gradient_accumulation_steps: 1
        train_unet: true
        train_text_encoder: false
        gradient_checkpointing: true
        noise_scheduler: "flowmatch"
        optimizer: "adamw8bit"
        lr: 1e-4
        dtype: bf16
{_MODEL_BLOCKS.get(ds.get("target"), _MODEL_BLOCKS["other"])}
      sample:
        sampler: "flowmatch"
        sample_every: 250
        width: 1024
        height: 1024
        prompts:
          - "{trig} {ds.get('class_token', 'person')} standing in a park, full body"
          - "{trig} {ds.get('class_token', 'person')} close-up portrait, soft studio lighting"
"""


def _kohya_toml(ds: dict, resolution: List[int]) -> str:
    return f"""# kohya / ComfyUI-FluxTrainer dataset config — FLUX and SDXL only.
# Krea 2 cannot be trained by FluxTrainer (it is a separate 12.9B DiT with a
# Qwen3-VL text encoder and the Qwen-Image VAE); use dataset_aitoolkit.yaml.
[general]
shuffle_caption = false
caption_extension = '.txt'
keep_tokens = 1

[[datasets]]
resolution = {max(resolution)}
batch_size = 1
enable_bucket = true
bucket_no_upscale = false

  [[datasets.subsets]]
  image_dir = './images'
  class_tokens = '{ds.get('trigger', 'sks')} {ds.get('class_token', 'person')}'
  num_repeats = 1
"""


def _musubi_toml(ds: dict, resolution: List[int]) -> str:
    """musubi-tuner's dataset config — a DIFFERENT format from the kohya one
    (image_directory / cache_directory, and resolution lives in [general])."""
    res = max(resolution)
    return f"""# musubi-tuner dataset config for Krea 2 (kohya-ss/musubi-tuner).
# Point image_directory at the extracted images/ folder of this zip.
[general]
resolution = [{res}, {res}]
caption_extension = ".txt"
batch_size = 1
enable_bucket = true          # this set mixes aspect ratios on purpose
bucket_no_upscale = false

[[datasets]]
image_directory = "./images"
cache_directory = "./cache/{ds.get('id')}"
num_repeats = 1
"""


def _musubi_commands(ds: dict, n: int, resolution: List[int]) -> str:
    """The three commands, with the low-VRAM flags already set.

    Every number here comes from a run someone actually reported (sources in
    docs/LORA_DATASET.md) — the block-swap ladder is the one knob to move if it
    OOMs or if there is VRAM left over."""
    steps = max(1000, min(n * 40, 2500))
    return f"""# ── Krea 2 LoRA — musubi-tuner (kohya-ss), the route that fits 12–16 GB ──
#
# Verified numbers people have reported:
#   RTX 3060 12 GB : peak ~10.5 GB, 7.2–7.8 s/step @512, rank 16, swap 22
#   RTX 4070 12 GB : rank 32, swap 26, ~2 h for 2000 steps, 48 GB system RAM
#
# ⚠ SYSTEM RAM, not just VRAM: block swap parks transformer blocks in CPU RAM.
#   Budget 32–64 GB. This is the requirement people miss.
# ⚠ Train on RAW. musubi does not train Turbo — you train Raw, you generate with
#   Turbo, and the LoRA transfers.
# ⚠ K2 needs SCALED fp8: --fp8_base AND --fp8_scaled together. Plain fp8 is
#   rejected on purpose (norm casting).
#
# Models needed:
#   DiT   : krea/Krea-2-Raw            -> models/krea2/raw/raw.safetensors
#   TE    : Comfy-Org qwen3vl_4b_bf16.safetensors   (the SINGLE file, not a dir)
#   VAE   : Comfy-Org qwen_image_vae.safetensors    (you already have this one)

# 1) cache the image latents (VAE leaves the training loop)
python src/musubi_tuner/krea2_cache_latents.py \
  --dataset_config dataset_musubi.toml \
  --vae /path/to/qwen_image_vae.safetensors \
  --batch_size 1 --skip_existing

# 2) cache the text-encoder outputs — THIS is what makes it fit; the ~8 GB
#    Qwen3-VL encoder is never loaded during training
python src/musubi_tuner/krea2_cache_text_encoder_outputs.py \
  --dataset_config dataset_musubi.toml \
  --text_encoder /path/to/qwen3vl_4b_bf16.safetensors \
  --batch_size 1 --skip_existing

# 3) train
accelerate launch --num_cpu_threads_per_process 1 --mixed_precision bf16 \
  src/musubi_tuner/krea2_train_network.py \
  --dit /path/to/raw.safetensors \
  --vae /path/to/qwen_image_vae.safetensors \
  --dataset_config dataset_musubi.toml \
  --sdpa --mixed_precision bf16 \
  --timestep_sampling krea2_shift --weighting_scheme none \
  --optimizer_type adamw8bit --learning_rate 1e-4 \
  --gradient_checkpointing \
  --network_module networks.lora_krea2 --network_dim 32 --network_alpha 32 \
  --fp8_base --fp8_scaled \
  --blocks_to_swap 20 --block_swap_h2d_only --block_swap_ring_size 2 \
  --max_data_loader_n_workers 2 --persistent_data_loader_workers \
  --max_train_steps {steps} --save_every_n_steps 250 --seed 42 \
  --output_dir outputs/{ds.get('id')} --output_name {ds.get('id')}

# ── the one knob: --blocks_to_swap (max 26 of 28) ────────────────────────
#   OOM?            raise it (20 -> 22 -> 24 -> 26)
#   VRAM to spare?  lower it — every swapped block costs CPU<->GPU bandwidth,
#                   which is what makes the step time, not the GPU
#   Still tight?    add --gradient_checkpointing_cpu_offload, then --split_attn
#
#   ⚠ Do NOT reach for resolution first. Measured on Krea 2: 0.25 -> 1.05 MP
#   costs about 0.15 GB (gradient checkpointing absorbs it), while an extra
#   BATCH image costs 2.4 GB. Keep batch 1; change quantisation and swap.
#
# Timestep sampling: this set mixes aspect ratios, so krea2_shift (resolution
# aware) is correct. Training at ONE fixed size instead? use
#   --timestep_sampling shift --discrete_flow_shift 2.5
#
# Steps: {steps} for {n} images (~40/image). Likeness usually arrives between
# 500 and 1500; past ~3000 it overfits. Save every 250 and pick by eye rather
# than taking the last checkpoint.
#
# Inference: load the LoRA onto Krea 2 TURBO at strength 0.8–1.2.
"""


# Measured by Fizgig on a 5090 (Krea 2, 36 images @ 0.25 MP, batch 1) and encoded
# in their planner `src/fizgig/utils/capabilities.py`.  Peaks are TRAINING-ONLY;
# the budget they plan against is FREE VRAM, not the number on the box.
KREA2_PEAK_GB = {"nf4": 11.4, "int8": 16.2, "fp8": 18.7}
KREA2_HEADROOM_GB = 1.5
KREA2_RES_GB_PER_MP = 0.25      # 0.25 -> 1.05 MP costs ~0.15 GB; checkpointing absorbs it
KREA2_BATCH_GB = 2.4            # per EXTRA image — the largest term by far
KREA2_RANK_GB = 0.015           # per rank above 32
KREA2_SWAP_GB = 0.42            # removed per swapped block, 26 max


def _krea2_need(kind: str, mp: float = 1.05, rank: int = 16, batch: int = 1) -> float:
    return (KREA2_PEAK_GB[kind]
            + KREA2_BATCH_GB * max(0, batch - 1)
            + KREA2_RES_GB_PER_MP * max(0.0, mp - 0.25)
            + KREA2_RANK_GB * max(0, rank - 32))


def _vram_table(mp: float = 1.05, rank: int = 16) -> str:
    """What actually runs on each card, from their numbers rather than tiers."""
    import math
    rows = []
    for card, free in (("12 GB", 11.0), ("16 GB", 14.8), ("24 GB", 22.5), ("32 GB", 30.0)):
        nf4 = _krea2_need("nf4", mp, rank)
        i8 = _krea2_need("int8", mp, rank)
        fp8 = _krea2_need("fp8", mp, rank)
        if free >= i8 + KREA2_HEADROOM_GB:
            plan = "INT8 W8A8, no swap  (fastest + most accurate)"
        elif free >= nf4 + KREA2_HEADROOM_GB:
            plan = "NF4 4-bit, no swap  (--quantize_4bit)"
        elif free >= fp8 + KREA2_HEADROOM_GB:
            plan = "fp8, no swap"
        else:
            swap = min(26, math.ceil((fp8 - (free - KREA2_HEADROOM_GB)) / KREA2_SWAP_GB))
            plan = f"fp8 + {swap} blocks swapped  (NF4 cannot swap; ~4x slower)"
        rows.append(f"#   {card:<6} ~{free:>4.1f} GB free   {plan}")
    return "\n".join(rows)


def _fizgig_toml(ds: dict, resolution: List[int]) -> str:
    """Fizgig's dataset TOML (docs/CLI.md).  Same lineage as musubi's, with
    `num_repeats` and `bucket_no_upscale` in [general]."""
    res = max(resolution)
    return f"""# Fizgig dataset config (shootthesound/Fizgig, docs/CLI.md).
# Point image_directory at the extracted images/ folder; give every dataset its
# OWN cache_directory — a shared one can mix a previous dataset into the run.
[general]
resolution = [{res}, {res}]   # area target; buckets keep each image's own aspect
caption_extension = ".txt"
batch_size = 1
num_repeats = 1
enable_bucket = true
bucket_no_upscale = true

[[datasets]]
image_directory = "./images"
cache_directory = "./cache/{ds.get('id')}"
"""


def _epochs_for(n: int) -> int:
    """How long to train, from the one run that was actually measured.

    v1.259 scored every epoch's preview with ArcFace on a 40-image set:
    likeness climbs to about 0.74 by epoch 21 and the last eight epochs span
    0.028. Epoch 21 x 40 images is roughly 840 image-steps, so ~900 is the
    target and the old `n * 1.2` (which asked for 40 epochs on 40 images) was
    about three hours of GPU past the point where the number stopped moving.

    The floor stays high and the cap stays at 40 on purpose: 900 steps is
    measured on ONE set size, and undertraining is not the cheaper mistake. On a
    20-image set this still asks for 40 epochs, because no one has measured a
    20-image set. Every epoch is saved, so `scripts\\checkpoint_score.py` can end
    a run early on evidence rather than on this arithmetic."""
    return max(15, min(round(900 / max(1, n)), 40))


def _fizgig_commands(ds: dict, n: int, mp: float = 1.05, rank: int = 16) -> str:
    """The three headless commands, with the flags Fizgig's own docs validate.

    Its GUI builds exactly these and runs them as subprocesses, so nothing here
    is a second-class path."""
    trig = ds.get("trigger", "sks")
    epochs = _epochs_for(n)
    nf4 = _krea2_need("nf4", mp, rank)
    i8 = _krea2_need("int8", mp, rank)
    fp8 = _krea2_need("fp8", mp, rank)
    table = _vram_table(mp, rank)
    return f"""# ── Krea 2 LoRA — Fizgig headless (shootthesound/Fizgig) ─────────────────
#
# Fizgig's GUI just builds these commands, so the CLI is feature-complete:
# adaptive LR, the per-image loss watch, auto-recaptioning and the look-outlier
# warm-up are all available from a terminal.
#
# ⚠ YOU DO NOT NEED TO RUN THESE BY HAND.  This zip ships fizgig_run.py plus
#   train_krea2_fizgig.bat / .sh, which run exactly the commands below with the
#   model paths read out of your Fizgig folder's prefs.json:
#
#       train_krea2_fizgig.bat            (edit FIZGIG= at the top once)
#       python fizgig_run.py --fizgig /path/to/Fizgig --dry-run
#
#   The sheet below is what it runs, for when you want to change something.
#
# Models (Comfy-Org/Krea-2).  fetch_models.py FLATTENS these into
# <fizgig>/models/ and records the absolute path in prefs.json, so prefer the
# prefs values (fizgig_run.py already does) over typing a path:
#   --dit           krea2_raw_bf16.safetensors          prefs: krea2_raw_dit
#   --turbo_dit     krea2_turbo_fp8_scaled.safetensors  prefs: krea2_turbo_dit
#   --vae           qwen_image_vae.safetensors          prefs: krea2_vae
#   --text_encoder  qwen3vl_4b_*.safetensors            prefs: krea2_text_encoder
#     ^ docs/CLI.md names the bf16 file, but Fizgig's own downloader fetches
#       qwen3vl_4b_fp8_scaled — either works, which is why we read the pref
#       instead of guessing. Doubles as the auto-recaption vision model.

# 1) cache latents
python src/fizgig/scripts/krea2_cache_latents.py \
  --dataset_config dataset_fizgig.toml --vae <models/qwen_image_vae.safetensors> --skip_existing

# 2) cache text
python src/fizgig/scripts/krea2_cache_text.py \
  --dataset_config dataset_fizgig.toml \
  --text_encoder <models/qwen3vl_4b_*.safetensors> --skip_existing

# 3) train — everything on
python src/fizgig/scripts/krea2_train.py \
  --dataset_config dataset_fizgig.toml \
  --dit <models/krea2_raw_bf16.safetensors> \
  --vae <models/qwen_image_vae.safetensors> \
  --text_encoder <models/qwen3vl_4b_*.safetensors> \
  --turbo_dit <models/krea2_turbo_fp8_scaled.safetensors> \
  --output_dir ./output_loras/{ds.get('id')} --output_name {ds.get('id')} \
  --network_dim 16 --network_alpha 16 \
  --max_train_epochs {epochs} --save_every_n_epochs 1 --save_state \
  --keep_last_n_states 2 --seed 42 \
  --quantize_4bit \
  --adaptive_lr --adaptive_lr_min 5e-5 --adaptive_lr_max 4e-4 \
  --log_per_image_loss --per_image_lr --auto_recaption \
  --warmup_look_outliers --trigger_word {trig} \
  --sample_prompts sample_prompts.txt --sample_every_n_epochs 1 \
  --sample_ref_image sample_ref.png \
  --sample_width 1024 --sample_height 1024

# ── VRAM: what actually runs, from Fizgig's own measured planner ─────────
# (src/fizgig/utils/capabilities.py — peaks are TRAINING-ONLY, and the budget is
#  FREE VRAM, not the number on the box: a browser or a running ComfyUI counts.)
#
#   PEAK at this run shape:  NF4 ~{nf4:.1f} GB   INT8 ~{i8:.1f} GB   fp8 ~{fp8:.1f} GB
#   plus 1.5 GB headroom, minus 0.42 GB per swapped block (26 max)
#
{table}
#
# Measured speed (5090, Krea 2, 36 images @ 0.25 MP, batch 1):
#   fp8, no swap   0.85 s/it   20.1 GB
#   fp8, swap 20   3.09 s/it   12.3 GB    <- swapping is ~3.6x slower
#   NF4, no swap   0.70 s/it   13.8 GB
# Their rule, verbatim: "Swapping is the slow path (4.4x the time, 4x the CPU):
# quantise first, and only swap when even NF4 will not fit."
#
#   --quantize_4bit   NF4 frozen base. Fits 16 GB with no swap. CANNOT swap.
#   --quant_int8 bf16 W8A8: needs ~24 GB, but the FASTEST measured
#                     (0.637 s/it vs NF4 0.709 on a 5090) and ~7x more accurate
#                     than NF4 in forward error (8 bits beat 4)
#   (default)         dynamic fp8 + --blocks_to_swap N when it will not fit
#
# ⚠ NF4 CANNOT block-swap — the weights live in `_nf4_packed` and the trainer
#   force-zeroes blocks_to_swap under 4-bit. So NF4 either fits or it doesn't;
#   below its footprint the only combination that runs is fp8 + heavy swap.
#
# ⚠ RESOLUTION IS NOT THE LEVER. 0.25 -> 1.05 MP costs about 0.15 GB (gradient
#   checkpointing absorbs it). BATCH is +2.4 GB per extra image, and rank is
#   ~15 MB each. Keep batch 1 and change the quantisation, not the picture size.
#
#   --compile_blocks auto   ~2x faster steady-state on the INT8 path (needs
#                      triton; on Windows also the MSVC C++ Build Tools)
#
# ── the intelligence flags (Krea 2 only, and the reason to use Fizgig) ────
#   --log_per_image_loss  classifies every image each epoch (easy / suspect /
#                         stuck / exhausted / excluded) into
#                         loss_log/problem_images.json, and prints a PLATEAU
#                         BANNER with a best-checkpoint estimate — your
#                         "you're done" signal, instead of picking by eye
#   --per_image_lr        stuck images throttled x0.5 -> x0.25 -> x0.125,
#                         mined-out ones eased to x0.6, the healthy cohort x1.1
#   --auto_recaption      Qwen3-VL rewrites a stuck image's caption from what is
#                         actually visible, re-encodes the text cache, gives it a
#                         fresh start; 2 failures and it is excluded
#   --warmup_look_outliers  reads fizgig_look_scores.json (WE WRITE IT — see
#                         below) and eases unusual angles in at x0.4 -> x1.0
#
# fizgig_look_scores.json in this zip was generated from our own QC identity
# check (every image compared against the character's reference). Fizgig's docs
# say there is no headless generator for it — this is that file. Drop it in the
# images/ folder alongside the pictures.
#
# sample_prompts.txt and sample_ref.png are IN THIS ZIP.  Fizgig guards
# --sample_prompts with os.path.exists, so a missing file does not error — it
# just trains with no previews, and the previews are where the plateau banner
# and the best-checkpoint estimate come from.  Keep them next to the images.
#
# LoKr alternative: --network_type lokr --lokr_factor 8 (their validated
# default; "in our validation runs LoKR at factor 8 hit the highest likeness
# we've ever measured"). Costs ~20% step time.
#
# Pause is a file: create <output_dir>/.pause_requested and it saves state and
# exits cleanly at the next epoch boundary; --resume <state-dir> continues.
"""


def _look_scores(ds: dict, picked: List[tuple], stems: Dict[str, str]) -> dict:
    """`fizgig_look_scores.json` — schema and cutoff taken from Fizgig's source
    (`lora_trainer_gui.py` writer + `krea2/trainer.py` reader), not invented:
    keys are basenames WITHOUT extension, cutoff is the IQR fence
    `max(median - 1.5*(q3-q1), 0.25)`, and anything scoring below it gets the
    LR warm-up.  Our identity QC supplies the scores."""
    scores: Dict[str, Any] = {}
    for it, _fp in picked:
        q = it.get("qc") or {}
        s = q.get("identity_score")
        # v1.218: ONLY an ArcFace cosine may go in this file. The trainer's
        # cutoff has a 0.25 floor in ArcFace units; a vision-LLM score is on a
        # different scale entirely, and feeding it one made the fence inert.
        if q.get("identity_method") != "arcface":
            s = None
        scores[stems[it["id"]]] = None if s is None else round(float(s), 4)
    return {"baselines": (ds.get("likeness_baselines")
                          or [f"{ds.get('char_name')} references"]),
            "cutoff": _like.cutoff([v for v in scores.values()
                                    if isinstance(v, (int, float))]),
            "scores": scores}


_FIZGIG_PREFS = {"dit": "krea2_raw_dit", "vae": "krea2_vae",
                 "text_encoder": "krea2_text_encoder", "turbo_dit": "krea2_turbo_dit"}


def _sample_prompts(ds: dict) -> str:
    """Preview prompts for --sample_prompts.  Krea 2 takes PLAIN prompts only —
    geometry and seed come from --sample_width/--sample_height/--sample_seed
    (Fizgig docs/CLI.md), so no kohya-style `--w/--h/--d` overrides here.

    Chosen to be the shots a character LoRA is most likely to be WRONG on: a
    close face, a full body (where build drifts first), a 3/4 turn, a profile
    and one hard lighting change.  Same trigger + class the captions use."""
    trig = (ds.get("trigger") or "sks").strip()
    cls = (ds.get("class_token") or "person").strip()
    who = f"{trig} {cls}"
    return "\n".join([
        "# Preview prompts - rendered on the fp8 Turbo each epoch (--turbo_dit).",
        "# One prompt per line; '#' lines are comments. Krea 2 ignores --w/--h/--d here.",
        "# These are deliberately the shots identity fails on first.",
        f"{who}, close-up portrait, neutral expression, soft studio light, plain grey backdrop",
        f"{who}, full body standing, front view, neutral expression, plain studio backdrop",
        f"{who}, waist-up, three-quarter view, slight smile, window light, plain backdrop",
        f"{who}, full body standing, side profile, outdoor daylight, plain background",
        f"{who}, head and shoulders, serious expression, dramatic side light, dark backdrop",
    ]) + "\n"


def _fizgig_runner(ds: dict, n: int) -> str:
    """fizgig_run.py — the whole three-step run, headless.

    Resolves models from Fizgig's own prefs.json rather than hardcoding names:
    fetch_models.py writes ABSOLUTE paths there against the pref keys the GUI
    reads, and it flattens every weight into <fizgig>/models/, so a hardcoded
    `/models/qwen3vl_4b_bf16.safetensors` is wrong twice over (their downloader
    pulls the fp8_scaled variant, and the path is relative to the checkout)."""
    epochs = _epochs_for(n)
    return (_RUNNER_SRC
            .replace("@@PREFS@@", repr(_FIZGIG_PREFS))
            .replace("@@DS_ID@@", repr(ds.get("id")))
            .replace("@@TRIGGER@@", repr((ds.get("trigger") or "sks")))
            .replace("@@EPOCHS@@", str(epochs)))


_RUNNER_SRC = '''"""Run this dataset through Fizgig, headless.  Generated by RBMN Storyboard.

    python fizgig_run.py --fizgig C:/Fizgig
    python fizgig_run.py --fizgig ~/Fizgig --dry-run     # print, run nothing

Point --fizgig at the Fizgig checkout (the folder with lora_trainer_gui.py).
Model paths come from that folder's prefs.json, which Fizgig's own model
downloader fills in - so there is nothing to type.  Override any of them with
--dit / --vae / --text-encoder / --turbo-dit if you keep them elsewhere.

This is not a second-class path: Fizgig's docs/CLI.md says the GUI "is a
front-end that builds these exact commands and runs them as subprocesses".
"""
import argparse
import json
import os
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PREFS = @@PREFS@@
DS_ID = @@DS_ID@@
TRIGGER = @@TRIGGER@@
EPOCHS = @@EPOCHS@@


def die(msg):
    print("ERROR: " + str(msg), file=sys.stderr)
    raise SystemExit(2)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fizgig", required=True, help="Fizgig checkout (has lora_trainer_gui.py)")
    ap.add_argument("--python", default=sys.executable, help="python inside Fizgig's venv")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--skip-cache", action="store_true", help="latents/text already cached")
    ap.add_argument("--quant", choices=("nf4", "int8", "fp8"), default="nf4")
    ap.add_argument("--blocks-to-swap", type=int, default=0)
    ap.add_argument("--epochs", type=int, default=EPOCHS)
    ap.add_argument("--output-dir", default=None)
    for _k in PREFS:
        ap.add_argument("--" + _k.replace("_", "-"), default=None)
    a = ap.parse_args()

    fiz = Path(a.fizgig).expanduser().resolve()
    if not (fiz / "lora_trainer_gui.py").is_file():
        die(str(fiz) + " does not look like a Fizgig checkout (no lora_trainer_gui.py)")
    scripts = fiz / "src" / "fizgig" / "scripts"
    for s in ("krea2_cache_latents.py", "krea2_cache_text.py", "krea2_train.py"):
        if not (scripts / s).is_file():
            die("missing " + s + " - is this Fizgig up to date?")

    # --- the dataset this zip carries -------------------------------------
    imgs = sorted((HERE / "images").glob("*.png"))
    if not imgs:
        die("no images/*.png next to this script - unzip the whole export, keep the layout")
    missing = [p.name for p in imgs if not p.with_suffix(".txt").is_file()]
    if missing:
        die(str(len(missing)) + " image(s) have no caption .txt: " + str(missing[:5]))
    print("dataset: " + str(len(imgs)) + " images, all captioned")

    # --- models, from Fizgig's own prefs.json ------------------------------
    prefs = {}
    pf = fiz / "prefs.json"
    if pf.is_file():
        try:
            prefs = json.loads(pf.read_text("utf-8"))
        except Exception as e:      # a broken prefs.json is not fatal - the flags still work
            print("warning: could not read prefs.json (" + str(e) + ")")
    models = {}
    for flag, key in PREFS.items():
        val = str(getattr(a, flag) or prefs.get(key) or "").strip()
        if val and not os.path.isabs(val):
            val = str((fiz / val).resolve())
        if not val or not os.path.isfile(val):
            die("no file for --" + flag.replace("_", "-") + ".  Either run Fizgig's "
                "Preferences -> 'Download models for me' (it writes prefs.json), or pass "
                "--" + flag.replace("_", "-") + " <path>.  Got: " + (val or "<unset>"))
        models[flag] = val
        print("  " + flag.ljust(13) + " " + val)

    py = a.python

    # v1.255: the shipped toml uses relative paths, and every step runs with
    # cwd=<fizgig checkout> so the scripts can import `fizgig.krea2`. Fizgig
    # therefore resolved "./images" against the CHECKOUT and found nothing --
    # the first real run died with "No training items" after caching zero
    # images. Rewrite the paths to absolute, against wherever this zip actually
    # got unpacked, and run from that.
    src_toml = HERE / "dataset_fizgig.toml"
    if not src_toml.is_file():
        die("dataset_fizgig.toml is missing next to this script")
    lines, img_dir, cache_dir = [], str(HERE / "images"), str(HERE / "cache" / DS_ID)
    for ln in src_toml.read_text("utf-8").splitlines():
        s = ln.strip()
        if s.startswith("image_directory"):
            ln = 'image_directory = ' + json.dumps(img_dir)
        elif s.startswith("cache_directory"):
            ln = 'cache_directory = ' + json.dumps(cache_dir)
        lines.append(ln)
    resolved = HERE / "dataset_fizgig.resolved.toml"
    # Written line by line on purpose: this script is a TEMPLATE inside
    # lora.py, so any escape sequence here is processed when the backend
    # imports it rather than when the file is written. v1.255 lost a newline
    # escape exactly that way and generated an unterminated string literal.
    with resolved.open("w", encoding="utf-8") as _fh:
        for _ln in lines:
            print(_ln, file=_fh)
    cfgp = str(resolved)

    # A dry run that only echoes commands cannot catch a bad path INSIDE the
    # file those commands point at. So say what the config resolves to, and
    # count what is actually there, before anything starts.
    have = len(list((HERE / "images").glob("*.png")))
    print("")
    print("  images dir    " + img_dir + "   (" + str(have) + " png)")
    print("  cache dir     " + cache_dir)
    print("  config        " + cfgp)
    if have == 0:
        die("no PNGs in " + img_dir + " - unzip the whole export and keep the layout")
    out = a.output_dir or str(fiz / "output_loras" / DS_ID)
    quant = {"nf4": ["--quantize_4bit"], "int8": ["--quant_int8"], "fp8": []}[a.quant]
    if a.quant == "nf4" and a.blocks_to_swap:
        die("NF4 cannot block-swap - the trainer force-zeroes blocks_to_swap under 4-bit. "
            "Use --quant fp8 with --blocks-to-swap, or NF4 with none.")
    swap = ["--blocks_to_swap", str(a.blocks_to_swap)] if a.blocks_to_swap else []

    steps = []
    if not a.skip_cache:
        steps.append(("cache latents", [py, str(scripts / "krea2_cache_latents.py"),
                                        "--dataset_config", cfgp,
                                        "--vae", models["vae"], "--skip_existing"]))
        steps.append(("cache text", [py, str(scripts / "krea2_cache_text.py"),
                                     "--dataset_config", cfgp,
                                     "--text_encoder", models["text_encoder"],
                                     "--skip_existing"]))
    train = [py, str(scripts / "krea2_train.py"),
             "--dataset_config", cfgp,
             "--dit", models["dit"], "--vae", models["vae"],
             "--text_encoder", models["text_encoder"], "--turbo_dit", models["turbo_dit"],
             "--output_dir", out, "--output_name", DS_ID,
             "--network_dim", "16", "--network_alpha", "16",
             "--max_train_epochs", str(a.epochs),
             "--save_every_n_epochs", "1", "--save_state", "--keep_last_n_states", "2",
             "--seed", "42",
             "--adaptive_lr", "--adaptive_lr_min", "5e-5", "--adaptive_lr_max", "4e-4",
             "--log_per_image_loss", "--per_image_lr", "--auto_recaption",
             "--warmup_look_outliers", "--trigger_word", TRIGGER,
             "--sample_every_n_epochs", "1",
             "--sample_width", "1024", "--sample_height", "1024"] + quant + swap
    sp = HERE / "sample_prompts.txt"
    if sp.is_file():
        train += ["--sample_prompts", str(sp)]
    ref = HERE / "sample_ref.png"
    if ref.is_file():
        train += ["--sample_ref_image", str(ref)]
    steps.append(("train", train))

    for label, cmd in steps:
        print("")
        print("=== " + label + " ===")
        print(" ".join(('"' + c + '"') if " " in c else c for c in cmd))
        if a.dry_run:
            continue
        # cwd=fiz and PYTHONPATH=<fizgig>/src: the scripts do `from fizgig.krea2 import ...`,
        # which only resolves from the checkout, never from this zip's folder.
        env = dict(os.environ, PYTHONPATH=str(fiz / "src"))
        r = subprocess.run(cmd, cwd=str(fiz), env=env)
        if r.returncode != 0:
            die(label + " exited " + str(r.returncode) + " - stopping before the next step")
    if not a.dry_run:
        print("")
        print("Done.  LoRA + checkpoints in: " + out)


if __name__ == "__main__":
    main()
'''


def _fizgig_bat(ds: dict) -> str:
    """Double-clickable Windows wrapper.  One line to edit."""
    return (
        "@echo off\n"
        "REM Train " + str(ds.get("id")) + " on Fizgig.\n"
        "REM  1. set FIZGIG below to your Fizgig folder   2. double-click this file\n"
        "setlocal\n"
        'set "FIZGIG=C:\\Fizgig"\n'
        'cd /d "%~dp0"\n'
        'if not exist "%FIZGIG%\\lora_trainer_gui.py" (\n'
        "  echo Edit this file and set FIZGIG to the folder containing lora_trainer_gui.py\n"
        "  pause\n"
        "  exit /b 2\n"
        ")\n"
        "REM Fizgig's own venv python, so torch/CUDA are the ones it was installed with.\n"
        'set "PY=%FIZGIG%\\venv\\Scripts\\python.exe"\n'
        'if not exist "%PY%" set "PY=python"\n'
        '"%PY%" "%~dp0fizgig_run.py" --fizgig "%FIZGIG%" --python "%PY%" %*\n'
        "pause\n")


def _fizgig_sh(ds: dict) -> str:
    """Linux/RunPod wrapper — same runner, FIZGIG from the environment."""
    return (
        "#!/usr/bin/env bash\n"
        "# Train " + str(ds.get("id")) + " on Fizgig.\n"
        "#   FIZGIG=/workspace/Fizgig ./train_krea2_fizgig.sh\n"
        "set -euo pipefail\n"
        'cd "$(dirname "$0")"\n'
        'FIZGIG="${FIZGIG:-$HOME/Fizgig}"\n'
        '[ -f "$FIZGIG/lora_trainer_gui.py" ] || {\n'
        '  echo "Set FIZGIG to the folder containing lora_trainer_gui.py"; exit 2; }\n'
        'PY="$FIZGIG/venv/bin/python"; [ -x "$PY" ] || PY="python3"\n'
        '"$PY" ./fizgig_run.py --fizgig "$FIZGIG" --python "$PY" "$@"\n')

@router.get("/datasets/{ds_id}/outfits")
async def outfits_get(ds_id: str):
    """The wardrobe, plus how the image budget would be split across it."""
    ds = _read_ds(ds_id)
    outs = _norm_outfits(ds)
    n = len(ds.get("items") or []) or _suggested_count(len(outs))
    return {"outfits": outs,
            "named": sum(1 for o in outs if o["kind"] == "named"),
            "variety": sum(1 for o in outs if o["kind"] == "variety"),
            "named_share": NAMED_SHARE,
            "suggested_count": _suggested_count(len(outs)),
            "images_per_outfit": IMAGES_PER_OUTFIT,
            "split": dict(zip([o["id"] for o in outs], _outfit_counts(n, outs))),
            "visibility": _OUTFIT_VIS}


@router.put("/datasets/{ds_id}/outfits")
async def outfits_put(ds_id: str, body: OutfitsIn):
    """Replace the wardrobe.  Does NOT re-plan on its own — changing outfits
    after images exist would silently invalidate them, so the caller re-plans
    deliberately."""
    ds = _read_ds(ds_id)
    ds["outfits"] = _norm_outfits({"outfits": body.outfits})
    _write_ds(ds)
    rendered = sum(1 for it in ds.get("items", []) if it.get("status") == "done")
    return {"outfits": ds["outfits"],
            "suggested_count": _suggested_count(len(ds["outfits"])),
            "note": (f"{rendered} image(s) are already rendered against the old wardrobe — "
                     "re-plan and re-render to apply this") if rendered else ""}


@router.post("/characters/{slug}/wardrobe")
async def wardrobe_suggest(slug: str, body: WardrobeIn,
                           session: AsyncSession = Depends(get_session)):
    """Propose variety outfits from the character's own reference.

    Returned for REVIEW, never applied: the whole value is that he can see the
    garments named before any of them cost a render."""
    char = _load_char(slug)
    fp, _lbl = _base_for_view(slug, char, "front")
    if not fp or not fp.exists():
        raise HTTPException(409, "this character has no front base image yet — strip or tag "
                                 "one in Klein 3.0 first")
    from backend.api.vnccs_native import _ollama_cfg
    from backend.services.character_studio.vnccs_native import wizards as _wiz
    urls, _t, vision_model = await _ollama_cfg(session)
    if not urls or not vision_model:
        raise HTTPException(503, "Ollama vision model is not configured "
                                 "(Settings -> Ollama vision model).")
    import asyncio
    n = max(2, min(int(body.count or 5), 10))
    raw = await asyncio.to_thread(
        _wiz.ollama_chat_sync, urls, vision_model, _WARDROBE_SYSTEM,
        _WARDROBE_PROMPT.format(n=n), [_wiz.image_bytes_to_b64(fp.read_bytes())],
        0.4, 150.0, True)
    out = _parse_wardrobe(raw or "", n)
    if not out["outfits"]:
        raise HTTPException(422, "the vision model returned no usable wardrobe — try again, "
                                 "or add the outfits by hand")
    logger.info("lora wardrobe[%s]: %s -> %d outfits", slug,
                out["character_type"] or "?", len(out["outfits"]))
    return out


@router.post("/characters/{slug}/refs/{ref_id}/garment")
async def garment_describe(slug: str, ref_id: str,
                           session: AsyncSession = Depends(get_session)):
    """Name the garments in a tagged reference image.

    This is not a convenience.  Klein ignores category words, so "the clothing
    in image 3" produces whatever it likes — a garment reference only works when
    the prompt NAMES what is in it, and this is what produces that name."""
    fp = _cdir(slug) / "refs" / f"{ref_id}.png"
    if not fp.exists():
        raise HTTPException(404, "no such reference image")
    from backend.api.vnccs_native import _ollama_cfg
    from backend.services.character_studio.vnccs_native import wizards as _wiz
    urls, _t, vision_model = await _ollama_cfg(session)
    if not urls or not vision_model:
        raise HTTPException(503, "Ollama vision model is not configured "
                                 "(Settings -> Ollama vision model).")
    import asyncio
    raw = await asyncio.to_thread(
        _wiz.ollama_chat_sync, urls, vision_model, _GARMENT_SYSTEM, _GARMENT_PROMPT,
        [_wiz.image_bytes_to_b64(fp.read_bytes())], 0.2, 120.0, False)
    desc = _clean_garment_desc(raw or "")
    if not desc:
        raise HTTPException(422, "the vision model did not name any garments in that image "
                                 "— describe the outfit by hand instead (name each garment "
                                 "and its colour; category words alone are ignored)")
    return {"desc": desc, "ref_id": ref_id}

@router.post("/datasets/{ds_id}/resync")
async def dataset_resync(ds_id: str):
    """Rebuild `status` from what is actually on disk.

    v1.223: datasets written before the render lock have rows whose status was
    lost to the write race while the PNG survived. Nothing is rendered or
    deleted here — it only makes the bookkeeping agree with the filesystem."""
    ds = _read_ds(ds_id)
    fixed, cleared = 0, 0
    with _DS_WRITE_LOCK:
        ds = _read_ds(ds_id)
        for it in ds.get("items", []):
            on_disk = _item_path(ds_id, it["id"]).exists()
            if on_disk and it.get("status") != "done":
                it["status"] = "done"
                fixed += 1
            elif not on_disk and it.get("status") == "done":
                it["status"] = "planned"
                cleared += 1
        _write_ds(ds)
    logger.info("lora resync[%s]: %d marked rendered, %d cleared", ds_id, fixed, cleared)
    return {"marked_rendered": fixed, "cleared": cleared,
            "rendered": sum(1 for it in ds["items"] if it.get("status") == "done"),
            "total": len(ds["items"]),
            "note": ("These images were on disk but recorded as unrendered — the pre-v1.223 "
                     "write race. A re-plan would have deleted them.") if fixed else
                    "Bookkeeping already matched the filesystem."}


@router.post("/datasets/{ds_id}/plan-preview")
async def dataset_plan_preview(ds_id: str, body: PlanIn):
    """What a re-plan WOULD do.  Writes nothing, deletes nothing.

    v1.222: the only way to find out used to be to do it."""
    ds = _read_ds(ds_id)
    probe = json.loads(json.dumps(ds))
    if body.outfits is not None:
        probe["outfits"] = _norm_outfits({"outfits": body.outfits})
    if body.options is not None:
        probe["options"] = {**(probe.get("options") or {}), **(body.options or {})}
    _p = (body.options or {}).get("preset") if body.options else None
    if _p:
        probe["preset"] = _p
    count = int(body.count) if body.count is not None else (len(ds.get("items", [])) or 40)
    count = max(8, min(count, 120))
    fresh = _build_plan(count, _plan_opts(probe))
    return {"count": count, "preset": probe.get("preset"),
            "options": _plan_opts(probe),
            "impact": _plan_impact(ds, fresh),
            "warnings": _plan_warnings(count, probe.get("outfits") or [])}


@router.post("/datasets/{ds_id}/likeness")
async def dataset_likeness(ds_id: str):
    """Score every rendered image against the character\'s references, ArcFace
    only — no vision model, no worker, no GPU.

    This is the measurement that v1.213 should have made before trusting a
    threshold. Run it on a real set and read `distribution.sanity` BEFORE
    anyone tunes a number against these scores."""
    ds = _read_ds(ds_id)
    import asyncio as _aio
    # available() loads the model on first call. On the loop that is a minutes-
    # long freeze before we have even started.
    if not await _aio.to_thread(_like.available):
        raise HTTPException(503, "ArcFace scoring is unavailable — "
                                 "`pip install insightface onnxruntime` on the app host. "
                                 f"({_like.health().get('error')})")
    import asyncio

    def _work() -> Tuple[Dict[str, Any], List[str], int]:
        """Everything CPU-bound, on a worker thread.

        v1.220: this used to run inline in an `async def`, which pins the event
        loop — the whole app froze until it finished, and on first use that
        included downloading buffalo_l (~300MB)."""
        sets = _baseline_sets(ds)
        embs, labels = sets.get("front", ([], []))
        if not embs:
            raise HTTPException(409, "no usable baseline — this character needs at least one "
                                     "reference with a detectable face (a front base or a face "
                                     "tag). Back-only references cannot be scored.")
        scores: Dict[str, Any] = {}
        by_view: Dict[str, List[Any]] = {}
        changed = 0
        for it in ds.get("items", []):
            fp = _item_path(ds_id, it["id"])
            if not fp.exists():
                continue
            _bl, _bl_lbl, _bl_key = _baselines_for(sets, it.get("angle"))
            s = _like.score(fp, _bl) if _bl else None
            scores[it["id"]] = None if s is None else round(s, 4)
            by_view.setdefault(_bl_key, []).append(scores[it["id"]])
            q = it.get("qc")
            if isinstance(q, dict):
                q["identity_score"] = scores[it["id"]]
                q["identity_baseline"] = _bl_key
                q["identity_baseline_n"] = len(_bl)
                q["identity_baseline_labels"] = _bl_lbl
                q["identity_method"] = "arcface"   # it ran; s is None only if no face
                if s is not None:
                    q["identity_verdict"] = _like.verdict(s)[0]
                    _is_back = it.get("angle") == "back"
                    q["identity_scored_against_front"] = not _is_back
                    q["same_person"] = True if _is_back else s >= _like.ARC_DIFFERENT
                    # v1.246: the SAME rule as QC, including the measured
                    # framing and crop checks. When these two drifted apart a
                    # likeness re-score silently un-failed images that QC had
                    # failed for being the wrong shot.
                    q["ok"] = bool(q.get("one_person", True)
                                   and not q.get("artifacts")
                                   and q.get("outfit_ok", True)
                                   and q.get("framing_ok", True)
                                   and q.get("crop_ok", True)
                                   and q["same_person"])
                changed += 1
        # v1.247: per baseline set, so "profiles score low" stops being a thing
        # you have to notice by reading forty rows.
        view_stats: Dict[str, Any] = {}
        for k, vals in by_view.items():
            v = sorted(x for x in vals if x is not None)
            view_stats[k] = {"n": len(vals), "scored": len(v),
                             "median": round(v[len(v) // 2], 4) if v else None,
                             "min": round(v[0], 4) if v else None,
                             "max": round(v[-1], 4) if v else None,
                             "below_match": sum(1 for x in v if x < _like.ARC_MATCH),
                             "baselines": len(sets.get(k.split(" ")[0], ([], []))[0])}
        return scores, labels, changed, view_stats

    scores, labels, changed, view_stats = await asyncio.to_thread(_work)
    ds["likeness_baselines"] = labels
    _write_ds(ds)
    logger.info("lora likeness[%s]: %d scored against %d baseline(s)",
                ds_id, len(scores), len(labels))
    return {"baselines": labels, "by_baseline": view_stats,
            "baseline_note": ("v1.247: profiles are scored against the profile reference, "
                              "not against a front view. A set resting on ONE reference "
                              "has that photograph's framing bias in it — "
                              "`baselines` per view says how many."),
            "scored": len(scores), "qc_updated": changed,
            "distribution": await asyncio.to_thread(_like.distribution, scores),
            "flags": _flag_summary(ds),
            "bands": {"match": _like.ARC_MATCH, "borderline": _like.ARC_BORDERLINE,
                      "different_person_floor": _like.ARC_DIFFERENT}}


class FaceRefIn(BaseModel):
    mode: str


@router.put("/datasets/{ds_id}/face-ref")
async def dataset_face_ref(ds_id: str, body: FaceRefIn):
    """Choose which shot types get the character's face reference.  v1.249.

    Changes the option and nothing else — the next render of an affected row
    picks it up, the shot list is untouched, and no rendered image is at risk."""
    m = str(body.mode or "").lower()
    if m not in FACE_REF_MODES:
        raise HTTPException(422, f"unknown mode '{body.mode}' — one of {list(FACE_REF_MODES)}")
    with _DS_WRITE_LOCK:
        ds = _read_ds(ds_id)
        ds.setdefault("options", {})["face_ref"] = m
        _write_ds(ds)
    has_face = False
    try:
        has_face = bool(_refs_by_tag(_load_char(ds["char_slug"]), "face"))
    except Exception:  # noqa: BLE001
        pass
    return {"mode": m, "framings": list(_FACE_REF_FRAMINGS[m]),
            "character_has_face_reference": has_face,
            "affects": sum(1 for it in ds.get("items", [])
                           if _wants_face_ref(ds, it.get("framing"))),
            "note": (None if has_face else
                     "this character has NO tagged face reference, so this option "
                     "changes nothing until one is added in Klein 3.0")}


class TqWordingIn(BaseModel):
    wording: str


@router.put("/datasets/{ds_id}/tq-wording")
async def dataset_tq_wording(ds_id: str, body: TqWordingIn):
    """Choose how three-quarter rows are asked for.  v1.235.

    Changes NOTHING on disk except the option — the next render of a
    three-quarter row picks it up.  Re-planning is neither needed nor wanted:
    the shot list is identical, only the sentence differs, which is exactly
    what makes this measurable."""
    w = str(body.wording or "").lower()
    if w != "auto" and w not in TQ_WORDINGS:
        raise HTTPException(422, f"unknown wording '{body.wording}' — "
                                 f"one of {sorted(TQ_WORDINGS) + ['auto']}")
    with _DS_WRITE_LOCK:
        ds = _read_ds(ds_id)
        ds.setdefault("options", {})["tq_wording"] = w
        _write_ds(ds)
    sample = _angle_text(ds, {"angle": "three_quarter_left"},
                         _by_key(ANGLES, "three_quarter_left"), 2)
    return {"wording": w, "reads": sample, "default": TQ_DEFAULT,
            # Under "auto" the two directions read differently, and showing only
            # one of them would misreport half the dataset.
            "reads_right": _angle_text(ds, {"angle": "three_quarter_right"},
                                       _by_key(ANGLES, "three_quarter_right"), 2),
            "resolves_to": ({k: _tq_mode(ds, k) for k in _TQ_ANGLES}
                            if w == "auto" else {k: w for k in _TQ_ANGLES}),
            "target_window": list(TQ_TARGET),
            "affects": sum(1 for it in ds.get("items", [])
                           if it.get("angle") in _TQ_ANGLES),
            "available": {k: v for k, v in TQ_WORDINGS.items()}}


@router.post("/datasets/{ds_id}/angles")
async def dataset_angles(ds_id: str):
    """Re-measure every rendered image's ANGLE from head pose.

    v1.234.  No vision model, no ComfyUI, no GPU — one CPU face detection per
    image against a model that is already resident.  A 40-image dataset is
    seconds, against minutes for a QC pass through a single Ollama server, so
    the wording experiments can be scored as fast as they render.

    Writes `angle_ok` / `yaw` / `angle_method` into each `qc` block and leaves
    every other field alone.  A row that has never been QC'd gets a `qc` block
    holding only the angle facts, which is honest: that is all we know."""
    ds = _read_ds(ds_id)
    import asyncio as _aio
    if not await _aio.to_thread(_like.available):
        raise HTTPException(503, "head pose is unavailable — "
                                 "`pip install insightface onnxruntime` on the app host. "
                                 f"({_like.health().get('error')})")
    health = await _aio.to_thread(_like.angle_health)
    if not health.get("available"):
        raise HTTPException(503, health.get("error") or "landmark_3d_68 not loaded")

    def _work() -> Dict[str, Any]:
        # v1.220's lesson: every bit of this is CPU-bound and must stay off the
        # event loop or the whole app stops answering while it runs.
        #
        # v1.243: TWO passes. The first measures every face; the second judges
        # against what the first found. Framing cannot be judged one image at a
        # time without an absolute threshold, and an absolute threshold is
        # exactly the thing tuned to one character. `pose` is cached on
        # (path, mtime, size), so the second pass costs nothing.
        _have_mask = _subj.available()
        poses: Dict[str, Any] = {}
        for it in ds.get("items", []):
            fp = _item_path(ds_id, it["id"])
            if fp.exists():
                poses[it["id"]] = _like.pose(fp)
        cal = _like.framing_calibrate(
            [(it.get("framing"), (poses.get(it["id"]) or {}).get("face_h_ratio"))
             for it in ds.get("items", [])
             if poses.get(it["id"]) and (poses[it["id"]] or {}).get("face_h_ratio")])

        rows: List[Dict[str, Any]] = []
        for it in ds.get("items", []):
            if it["id"] not in poses:
                continue
            pv = poses[it["id"]]
            aok, why = _like.angle_verdict(it.get("angle"), pv)
            fok, fwhy = _like.framing_verdict(it.get("framing"), it.get("angle"), pv, cal)
            bx = _subj.box(_item_path(ds_id, it["id"])) if _have_mask else None
            cok, cwhy = _subj.crop_verdict(it.get("framing"), bx)
            q = it.get("qc")
            if not isinstance(q, dict):
                q = {}
                it["qc"] = q
            q["yaw"] = None if not pv else pv.get("yaw")
            q["yaw_detail"] = pv
            q["angle_note"] = why
            if aok is None:
                q["angle_ok"] = True
                q["angle_method"] = "unmeasured"
            else:
                q["angle_ok"] = bool(aok)
                q["angle_method"] = "head-yaw"
            q["framing_note"] = fwhy
            q["face_h_ratio"] = None if not pv else pv.get("face_h_ratio")
            if fok is None:
                q["framing_ok"] = True
                q["framing_method"] = "unmeasured"
            else:
                q["framing_ok"] = bool(fok)
                q["framing_method"] = "face-height"
            q["crop_note"] = cwhy
            q["body_h_ratio"] = None if not bx else bx.get("body_h_ratio")
            q["crop_ok"] = True if cok is None else bool(cok)
            q["crop_method"] = "unmeasured" if cok is None else "person-mask"
            rows.append({"id": it["id"], "angle": it.get("angle"),
                         "framing": it.get("framing"),
                         "yaw": q["yaw"], "ok": aok, "note": why,
                         "framing_ok": fok, "framing_note": fwhy,
                         "face_h_ratio": q["face_h_ratio"],
                         "crop_ok": cok, "crop_note": cwhy,
                         "body_h_ratio": q["body_h_ratio"],
                         "subject_box": bx,
                         "det_score": None if not pv else pv.get("det_score"),
                         "kps_yaw": None if not pv else pv.get("kps_yaw")})
        return {"rows": rows, "cal": cal}

    res = await _aio.to_thread(_work)
    with _DS_WRITE_LOCK:
        # Re-read under the lock and copy the angle fields across, so a render
        # or QC finishing mid-pass is not clobbered.  v1.223 is why.
        cur = _read_ds(ds_id)
        # Stored so the QC pass judges framing by the same numbers rather than
        # falling back to bands calibrated on a different character.
        cur["framing_cal"] = res["cal"]
        by = {r["id"]: r for r in res["rows"]}
        for it in cur.get("items", []):
            r = by.get(it["id"])
            if not r:
                continue
            src_q = next((x.get("qc") or {} for x in ds["items"] if x["id"] == it["id"]), {})
            q = it.get("qc")
            if not isinstance(q, dict):
                q = {}
                it["qc"] = q
            for k in ("yaw", "yaw_detail", "angle_note", "angle_ok", "angle_method",
                      "framing_ok", "framing_method", "framing_note", "face_h_ratio",
                      "crop_ok", "crop_method", "crop_note", "body_h_ratio"):
                if k in src_q:
                    q[k] = src_q[k]
        _write_ds(cur)
        ds_after = cur

    rows = res["rows"]
    by_angle: Dict[str, Any] = {}
    for r in rows:
        b = by_angle.setdefault(str(r["angle"]), {"n": 0, "ok": 0, "miss": 0,
                                                  "unmeasured": 0, "yaws": []})
        b["n"] += 1
        b["ok" if r["ok"] is True else ("miss" if r["ok"] is False else "unmeasured")] += 1
        if r["yaw"] is not None:
            b["yaws"].append(float(r["yaw"]))
    for b in by_angle.values():
        ys = sorted(b.pop("yaws"))
        # The median is the number that survives a bad fit or two, so it is the
        # one to compare wording variants on.
        b["yaw_median"] = None if not ys else round(ys[len(ys) // 2], 1)
        b["yaw_min"] = None if not ys else round(ys[0], 1)
        b["yaw_max"] = None if not ys else round(ys[-1], 1)
    by_framing: Dict[str, Any] = {}
    for r in rows:
        b = by_framing.setdefault(str(r["framing"]), {"n": 0, "ok": 0, "miss": 0,
                                                      "unmeasured": 0, "ratios": []})
        b["n"] += 1
        b["ok" if r["framing_ok"] is True
          else ("miss" if r["framing_ok"] is False else "unmeasured")] += 1
        if r.get("face_h_ratio") is not None:
            b["ratios"].append(float(r["face_h_ratio"]))
    for b in by_framing.values():
        rs = sorted(b.pop("ratios"))
        b["face_h_median"] = None if not rs else round(rs[len(rs) // 2], 4)
        b["face_h_min"] = None if not rs else round(rs[0], 4)
        b["face_h_max"] = None if not rs else round(rs[-1], 4)
    fhealth = _like.framing_health()
    cal = res["cal"]
    logger.info("lora angles[%s]: measured %d image(s)", ds_id, len(rows))
    return {"measured": len(rows), "by_angle": by_angle, "by_framing": by_framing,
            "rows": rows,
            "bands": health["bands"], "sign": health["sign"],
            "framing_cal": cal,
            "by_crop": _subj.summarise(rows),
            "crop": {**_subj.health(),
                     "status": "measured — 20 of 20 on real images (v1.246); an image "
                               "whose subject is cut off wrongly now fails QC"},
            "framing_method": fhealth["method"],
            "framing_fallback_bands": fhealth["fallback_bands"],
            "face_cy_max": fhealth["face_cy_max"],
            "flags": _flag_summary(ds_after)}


@router.post("/datasets/{ds_id}/export")
async def dataset_export(ds_id: str, body: ExportIn):
    """Build the training zip: images + caption txt files + both trainer configs
    + a manifest + the notes.  Flagged images stay out unless asked for."""
    ds = _read_ds(ds_id)
    resolution = [int(r) for r in (body.resolution or [512, 768, 1024])][:4] or [1024]
    literal = (body.trigger_mode or "literal") != "placeholder"
    picked = []
    # v1.248: WHY each image was left out, so the gap between "40 rendered" and
    # "36 exported" is never something you have to work out for yourself.
    excluded: List[Dict[str, Any]] = []
    floor = body.min_likeness
    for it in ds["items"]:
        fp = _item_path(ds_id, it["id"])
        if not fp.exists():
            excluded.append({"id": it["id"], "why": "never rendered"})
            continue
        if it.get("keep") is False:
            excluded.append({"id": it["id"], "why": "marked not-kept by hand"})
            continue
        q = it.get("qc") or {}
        if not body.include_flagged and q.get("ok") is False:
            excluded.append({"id": it["id"], "why": "QC flagged",
                             "issues": (q.get("issues") or [])[:2],
                             "identity_score": q.get("identity_score")})
            continue
        # v1.261: a bare row leaves the set even when include_flagged is on.
        # `include_flagged` means "ship the near misses"; it should never have
        # silently meant "ship the nudes".
        if q.get("bare") is True and not body.include_bare:
            excluded.append({"id": it["id"], "why": "subject is undressed",
                             "bare_words": q.get("bare_words") or []})
            continue
        s = q.get("identity_score")
        if floor is not None and isinstance(s, (int, float)) and s < float(floor):
            excluded.append({"id": it["id"],
                             "why": f"likeness {s:.3f} below the {float(floor):.2f} floor",
                             "identity_score": s,
                             "identity_baseline": q.get("identity_baseline")})
            continue
        picked.append((it, fp))
    if not picked:
        raise HTTPException(409, "no images to export — render some first "
                                 "(or tick include-flagged, or lower min_likeness)")
    out_dir = _ds_dir(ds_id) / "exports"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = _now().replace(":", "").replace("-", "").replace("T", "_")[:15]
    zip_name = f"{ds_id}_{len(picked)}img_{stamp}.zip"
    zp = out_dir / zip_name
    manifest = []
    stems: Dict[str, str] = {}
    with zipfile.ZipFile(zp, "w", zipfile.ZIP_DEFLATED) as z:
        for n, (it, fp) in enumerate(picked, 1):
            stem = f"{_slugify(ds.get('name', 'ds'))[:24]}_{n:04d}"
            stems[it["id"]] = stem
            cap = (it.get("caption") or "").strip() or _caption(ds, it, literal)
            if not literal:
                cap = cap.replace(f"{ds.get('trigger', '')} ", "[trigger] ", 1)
            z.write(fp, f"images/{stem}.png")
            z.writestr(f"images/{stem}.txt", cap)
            manifest.append({"file": f"{stem}.png", "caption": cap,
                             **{k: it.get(k) for k in ("id", "framing", "angle", "expression",
                                                       "pose", "lighting", "background",
                                                       "identity", "seed")},
                             "qc": it.get("qc")})
        z.writestr("manifest.json", json.dumps(
            {"dataset": {k: ds.get(k) for k in ("id", "name", "char_name", "trigger",
                                                "class_token", "target", "outfit",
                                                "created_at")},
             "images": manifest}, indent=2))
        z.writestr("dataset_aitoolkit.yaml", _aitoolkit_yaml(ds, len(picked), resolution))
        z.writestr("dataset_kohya.toml", _kohya_toml(ds, resolution))
        if ds.get("target") == "krea2":
            # Two trainers can do Krea 2 on a small card; ship both, plus the
            # look-scores file that unlocks Fizgig's outlier warm-up headless.
            z.writestr("dataset_musubi.toml", _musubi_toml(ds, resolution))
            z.writestr("train_krea2_musubi.txt",
                       _musubi_commands(ds, len(picked), resolution))
            z.writestr("dataset_fizgig.toml", _fizgig_toml(ds, resolution))
            z.writestr("train_krea2_fizgig.txt", _fizgig_commands(ds, len(picked)))
            look = _look_scores(ds, picked, stems)
            z.writestr("images/fizgig_look_scores.json", json.dumps(look, indent=2))
            # v1.214: the zip RUNS itself.  fizgig_run.py resolves the model
            # paths out of Fizgig's prefs.json and drives the same three
            # subprocesses its GUI does — nothing to retype.
            z.writestr("fizgig_run.py", _fizgig_runner(ds, len(picked)))
            z.writestr("train_krea2_fizgig.bat", _fizgig_bat(ds).replace("\n", "\r\n"))
            z.writestr("train_krea2_fizgig.sh", _fizgig_sh(ds))
            # --sample_prompts pointed at a file we never shipped.  Fizgig
            # guards with os.path.exists, so it did not fail — it silently ran
            # with no previews, and the previews ARE the plateau signal.
            z.writestr("sample_prompts.txt", _sample_prompts(ds))
            ref_png = _identity_ref_png(ds)
            if ref_png:
                z.writestr("sample_ref.png", ref_png)
        z.writestr("README.md", _training_notes(ds, len(picked)))
    # What actually went in, and what a stricter floor would have cost. Reported
    # rather than decided: a blanket floor at ARC_MATCH drops dorian's profile
    # rows at 0.4313 and 0.4410, which are fine.
    _shipped = [(it.get("qc") or {}).get("identity_score") for it, _ in picked]
    _sv = sorted(x for x in _shipped if isinstance(x, (int, float)))
    likeness = {
        "shipped": len(picked),
        "scored": len(_sv),
        "no_face": sum(1 for x in _shipped if x is None),
        "median": round(_sv[len(_sv) // 2], 4) if _sv else None,
        "min": round(_sv[0], 4) if _sv else None,
        "max": round(_sv[-1], 4) if _sv else None,
        "below_match": sum(1 for x in _sv if x < _like.ARC_MATCH),
        "floor_applied": floor,
        "would_drop": {f"{t:.2f}": sum(1 for x in _sv if x < t)
                       for t in (0.30, 0.40, 0.45, 0.50)},
        "note": ("`min_likeness` is off by default. A blanket floor at "
                 f"{_like.ARC_MATCH} drops correctly-rendered PROFILE rows, which "
                 "score lower against a single profile reference. Read "
                 "`would_drop` against `by_baseline` from /likeness before "
                 "setting one."),
    }
    logger.info("lora export[%s]: %s (%d images, %d excluded)",
                ds_id, zip_name, len(picked), len(excluded))
    return {"file": zip_name, "images": len(picked),
            "url": f"/api/lora/datasets/{ds_id}/exports/{zip_name}",
            "likeness": likeness,
            "excluded": excluded[:60],
            "excluded_total": len(excluded),
            "skipped_flagged": sum(1 for it in ds["items"]
                                   if (it.get("qc") or {}).get("ok") is False)
            if not body.include_flagged else 0}


@router.get("/datasets/{ds_id}/exports/{fname}")
async def export_download(ds_id: str, fname: str):
    if "/" in fname or "\\" in fname or ".." in fname:
        raise HTTPException(400, "bad name")
    fp = _ds_dir(ds_id) / "exports" / fname
    if not fp.exists():
        raise HTTPException(404, "export not found")
    return FileResponse(str(fp), media_type="application/zip", filename=fname)


@router.get("/recipe")
async def recipe():
    """The shot vocabulary the planner draws from — the UI renders the pickers
    from this, so the two can never drift apart."""
    return {
        "framings": [{"key": f[0], "weight": f[1], "caption": f[2], "size": list(f[4])}
                     for f in FRAMINGS],
        "presets": {k: v for k, v in FRAMING_PRESETS.items()},
        "angles": [{"key": a[0], "caption": a[1], "base_view": a[3]} for a in ANGLES],
        "expressions": [{"key": e[0], "caption": e[1]} for e in EXPRESSIONS],
        "poses": [{"key": p[0], "caption": p[1]} for p in POSES],
        "lighting": [{"key": l[0], "caption": l[1]} for l in LIGHTING],
        "backgrounds": [{"key": b[0], "caption": b[1]} for b in BACKGROUNDS],
        "views": VIEW_TAGS,
    }
