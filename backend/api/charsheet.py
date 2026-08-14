"""🪪 Character Sheet — one downloadable reference image per character.

Composites a model-ready character sheet (turnaround + face row) from what the
character ALREADY has: the identity-scored LoRA dataset renders first (best
measured likeness per cell), falling back to tagged Klein 3.0 refs and the
active base. Pure PIL on the backend — no GPU, no worker, no LoRA required.

Cells prefer, in order:
  1. a rendered dataset item matching the cell's framing+angle, QC ok, not
     bare, highest ArcFace identity score
  2. the character's tagged reference for that view (front/left/right/back/face)
  3. the active base (front cells only)
A cell with no source is left empty and reported in `missing`.

Labels default OFF: text on a reference sheet can leak into generations on
some models. Turn them on for human-facing sheets.

Storage: <project_dir>/_libraries/charsheet/<slug>/sheet_<ts>.png (+ .json meta)
"""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from backend.config import settings as cfg

router = APIRouter(prefix="/api/charsheet", tags=["charsheet"])

_ROOT = Path(cfg.project_dir) / "_libraries" / "charsheet"

# ── cell geometry ────────────────────────────────────────────────────────────
CELL_W, CELL_H = 768, 1152          # portrait cell; everything letterboxes in
MARGIN, GUTTER = 28, 18
HEADER_H = 96                       # name strip (only when labels on)
LABEL_H = 40                        # per-cell caption strip (only when labels on)

# Each cell: (key, label, framings, angles). Angles listed in preference order;
# "expression" is a special key handled in _pick.
PRESETS: dict[str, List[tuple]] = {
    "standard": [
        ("full_front", "front", ("full",), ("front",)),
        ("full_tq", "three-quarter", ("full",), ("three_quarter_right", "three_quarter_left")),
        ("full_profile", "profile", ("full",), ("profile_left", "profile_right")),
        ("full_back", "back", ("full",), ("back",)),
        ("face_front", "face", ("face", "headshot"), ("front",)),
        ("face_tq", "face ¾", ("face", "headshot"), ("three_quarter_left", "three_quarter_right")),
        ("upper_front", "upper body", ("upper",), ("front",)),
        ("expression", "expression", ("face", "headshot"), ("front", "three_quarter_left",
                                                            "three_quarter_right")),
    ],
    "turnaround": [
        ("full_front", "front", ("full",), ("front",)),
        ("full_tq", "three-quarter", ("full",), ("three_quarter_right", "three_quarter_left")),
        ("full_profile", "profile", ("full",), ("profile_left", "profile_right")),
        ("full_back", "back", ("full",), ("back",)),
    ],
    # 🧥 v1.277.2 — one sheet PER OUTFIT: composed from that outfit's own five
    # rendered views (never the dataset), so the character can be referenced
    # in a specific attire. His ask: "if we want to use the character in a
    # certain style or attire we can."
    "outfit": [
        ("full_front", "front", ("full",), ("front",)),
        ("full_left", "left", ("full",), ("profile_left",)),
        ("full_right", "right", ("full",), ("profile_right",)),
        ("full_back", "back", ("full",), ("back",)),
        ("face_front", "face", ("face", "headshot"), ("front",)),
    ],
}
_COLS = {"standard": 4, "turnaround": 4, "outfit": 5}

#: outfit views are front/back/left/right/face — map a cell's angle wish onto
#: the outfit view that shows it
_OUTFIT_VIEW_FOR_ANGLE = {
    "front": "front", "back": "back",
    "profile_left": "left", "three_quarter_left": "left",
    "profile_right": "right", "three_quarter_right": "right",
}

_REF_TAG_FOR_ANGLE = {
    "front": "front", "back": "back",
    "profile_left": "left", "profile_right": "right",
    "three_quarter_left": "left", "three_quarter_right": "right",
}


def _sheets_dir(slug: str) -> Path:
    if "/" in slug or "\\" in slug or ".." in slug:
        raise HTTPException(400, "bad slug")
    return _ROOT / slug


# ── sources ──────────────────────────────────────────────────────────────────
def _char_datasets(slug: str) -> List[dict]:
    """Every LoRA dataset for this character, parsed."""
    from backend.api.lora import _DS_ROOT
    out = []
    if _DS_ROOT.exists():
        for fp in sorted(_DS_ROOT.glob("*/dataset.json")):
            try:
                ds = json.loads(fp.read_text("utf-8"))
            except Exception:  # noqa: BLE001
                continue
            if ds.get("char_slug") == slug:
                out.append(ds)
    return out


def _dataset_candidates(slug: str) -> List[dict]:
    """Rendered, non-flagged, dressed dataset items with their image paths."""
    from backend.api.lora import _item_path
    cands = []
    for ds in _char_datasets(slug):
        for it in ds.get("items", []):
            q = it.get("qc") or {}
            if q.get("ok") is False or q.get("bare") is True:
                continue
            fp = _item_path(ds["id"], it["id"])
            if not fp.exists():
                continue
            score = q.get("identity_score")
            cands.append({"path": fp, "framing": it.get("framing"),
                          "angle": it.get("angle"),
                          "expression": (it.get("expression") or "").lower(),
                          "score": score if isinstance(score, (int, float)) else None,
                          "source": f"dataset {ds['id']} · {it['id']}"})
    return cands


def _pick(cands: List[dict], key: str, framings: tuple, angles: tuple,
          taken: Optional[set] = None) -> Optional[dict]:
    pool = [c for c in cands if c["framing"] in framings and c["angle"] in angles
            and (taken is None or str(c["path"]) not in taken)]
    if key == "expression":
        pool = [c for c in pool if c["expression"] not in ("", "neutral")]
    # Identity is meaningless on back rows (frontal baselines) — measured
    # median 0.125 there; treat all back candidates as equal.
    def sort_key(c):
        s = c["score"] if (c["score"] is not None and c["angle"] != "back") else -1.0
        return -s
    pool.sort(key=sort_key)
    return pool[0] if pool else None


def _ref_fallback(slug: str, char: dict, key: str, angles: tuple) -> Optional[dict]:
    from backend.api.klein3 import _cdir, _refs_by_tag, _active_base_path
    if key.startswith("face") or key == "expression":
        tags = ["face"]
    else:
        tags = [_REF_TAG_FOR_ANGLE[a] for a in angles if a in _REF_TAG_FOR_ANGLE]
    for tag in tags:
        for r in _refs_by_tag(char, tag):
            fp = _cdir(slug) / "refs" / f"{r['id']}.png"
            if fp.exists():
                return {"path": fp, "score": None, "source": f"ref {tag} · {r['id']}"}
    if "front" in angles:
        ab = _active_base_path(slug, char)
        if ab and ab.exists():
            return {"path": ab, "score": None, "source": "active base"}
    return None


def _outfit_pick(slug: str, char: dict, key: str, angles: tuple,
                 outfit: dict) -> Optional[dict]:
    """A cell source drawn ONLY from the named outfit's rendered views.

    ⚠ Outfit renders are tagged `outfit` with the view inside r["outfit"], so
    the generic `_ref_fallback` (which reads front/back/left/right/face tags)
    cannot see them — this is the lookup that can. Uses klein3's own
    `_outfit_ref_for_view`, the same helper the LoRA dataset uses to train on
    a specific outfit.
    """
    from backend.api.klein3 import _outfit_ref_for_view
    if key.startswith("face") or key == "expression":
        views = ["face"]
    else:
        views = [v for v in
                 (_OUTFIT_VIEW_FOR_ANGLE.get(a) for a in angles) if v]
    for view in views:
        got = _outfit_ref_for_view(slug, char, view, outfit)
        if got:
            fp, label = got
            if fp.exists():
                return {"path": fp, "score": None,
                        "source": f"outfit {label} · {view}"}
    return None


# ── composition (blocking; runs in a thread) ─────────────────────────────────
def _compose(slug: str, name: str, preset: str, labels: bool,
             out_width: Optional[int], outfit: Optional[dict] = None) -> dict:
    from PIL import Image, ImageDraw, ImageFont
    from backend.api.klein3 import _load

    char = _load(slug)
    cands = [] if outfit else _dataset_candidates(slug)
    cells = PRESETS[preset]
    cols = _COLS[preset]
    rows = (len(cells) + cols - 1) // cols

    chosen: List[tuple] = []          # (cellspec, pick|None)
    taken: set = set()                # no image appears in two cells
    for spec in cells:
        key, _lbl, framings, angles = spec
        if outfit:
            # OUTFIT MODE: the outfit's own views are the ONLY source — mixing
            # in dataset or base-look images would put the wrong clothes on
            # the sheet, which defeats its purpose.
            pick = _outfit_pick(slug, char, key, angles, outfit)
        else:
            pick = (_pick(cands, key, framings, angles, taken)
                    or _pick(cands, key, framings, angles)  # reuse beats an empty cell
                    or _ref_fallback(slug, char, key, angles))
        if pick is not None:
            taken.add(str(pick["path"]))
        chosen.append((spec, pick))

    label_h = LABEL_H if labels else 0
    header_h = HEADER_H if labels else 0
    W = MARGIN * 2 + cols * CELL_W + (cols - 1) * GUTTER
    H = MARGIN * 2 + header_h + rows * (CELL_H + label_h) + (rows - 1) * GUTTER
    canvas = Image.new("RGB", (W, H), "#ffffff")
    draw = ImageDraw.Draw(canvas)
    try:
        font_big = ImageFont.truetype("arial.ttf", 52)
        font = ImageFont.truetype("arial.ttf", 24)
    except Exception:  # noqa: BLE001
        font_big = ImageFont.load_default()
        font = ImageFont.load_default()

    if labels:
        draw.text((MARGIN, MARGIN + 14), name, fill="#111111", font=font_big)

    used, missing = [], []
    for i, (spec, pick) in enumerate(chosen):
        key, lbl, _f, _a = spec
        r, c = divmod(i, cols)
        x = MARGIN + c * (CELL_W + GUTTER)
        y = MARGIN + header_h + r * (CELL_H + label_h + (GUTTER if rows > 1 else 0))
        draw.rectangle([x, y, x + CELL_W, y + CELL_H], outline="#d8d8d8", width=2)
        if pick is None:
            missing.append(key)
            draw.text((x + 20, y + 20), "(missing)", fill="#999999", font=font)
        else:
            try:
                im = Image.open(pick["path"]).convert("RGB")
                s = min(CELL_W / im.width, CELL_H / im.height)
                im = im.resize((max(1, int(im.width * s)), max(1, int(im.height * s))),
                               Image.LANCZOS)
                canvas.paste(im, (x + (CELL_W - im.width) // 2,
                                  y + (CELL_H - im.height) // 2))
                used.append({"cell": key, "source": pick["source"],
                             "identity_score": pick["score"]})
            except Exception as e:  # noqa: BLE001
                missing.append(key)
                draw.text((x + 20, y + 20), f"(unreadable: {e})"[:60],
                          fill="#bb4444", font=font)
        if labels:
            draw.text((x + 4, y + CELL_H + 8), lbl, fill="#333333", font=font)

    if out_width and out_width < W:
        canvas = canvas.resize((out_width, int(H * out_width / W)), Image.LANCZOS)

    d = _sheets_dir(slug)
    d.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S")
    otok = ""
    if outfit:
        import re as _re
        otok = "_" + (_re.sub(r"[^a-z0-9]+", "-",
                              (outfit.get("name") or "outfit").lower())
                      .strip("-")[:32] or "outfit")
    fname = f"sheet_{preset}{otok}_{ts}.png"
    canvas.save(d / fname, "PNG")
    meta = {"file": fname, "preset": preset, "labels": labels,
            "size": list(canvas.size), "cells": used, "missing": missing,
            "outfit": (outfit or None),
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    (d / f"{fname}.json").write_text(json.dumps(meta, indent=2), "utf-8")
    return meta


# ── routes ───────────────────────────────────────────────────────────────────
@router.get("/characters")
async def characters():
    """Klein 3.0 characters + how much sheet-usable material each has."""
    from backend.api.klein3 import _K3_ROOT, _load, _public_char
    out = []
    if _K3_ROOT.exists():
        for d in sorted(_K3_ROOT.iterdir()):
            if not (d / "char.json").exists():
                continue
            slug = d.name
            try:
                c = _load(slug)
            except Exception:  # noqa: BLE001
                continue
            pub = _public_char(slug, c)
            n_items = sum(1 for x in _dataset_candidates(slug))
            out.append({"slug": slug, "name": pub["name"], "ref_count": pub["ref_count"],
                        "has_base": pub["has_base"], "dataset_images": n_items})
    return {"characters": out}


class GenReq(BaseModel):
    slug: str
    preset: str = "standard"
    labels: bool = False
    width: Optional[int] = None       # e.g. 2048 to downscale; None = full size
    # 🧥 build the sheet from ONE OUTFIT's rendered views instead of the
    # dataset/base material. variant "" = the outfit's base look.
    outfit_name: str = ""
    outfit_variant: str = ""


@router.post("/generate")
async def generate(req: GenReq):
    outfit = ({"name": req.outfit_name.strip(),
               "variant": req.outfit_variant.strip()}
              if req.outfit_name.strip() else None)
    preset = req.preset
    if outfit and preset not in ("outfit",):
        preset = "outfit"             # outfit sheets have their own 5-cell layout
    if preset not in PRESETS:
        raise HTTPException(400, f"preset must be one of {sorted(PRESETS)}")
    if preset == "outfit" and not outfit:
        raise HTTPException(400, "the 'outfit' preset needs an outfit_name")
    from backend.api.klein3 import _load, _K3_ROOT
    if not (_K3_ROOT / req.slug / "char.json").exists():
        raise HTTPException(404, f"no character '{req.slug}'")
    c = _load(req.slug)
    name = c.get("name") or req.slug
    if outfit:
        from backend.api.klein3 import _outfit_ref_for_view
        if not any(_outfit_ref_for_view(req.slug, c, v, outfit)
                   for v in ("front", "back", "left", "right", "face")):
            raise HTTPException(400, f"outfit {req.outfit_name!r} has no "
                                     f"rendered views to build a sheet from")
        name = f"{name} — {req.outfit_name.strip()}" \
               + (f" ({req.outfit_variant.strip()})" if req.outfit_variant.strip() else "")
    meta = await asyncio.to_thread(_compose, req.slug, name, preset,
                                   req.labels, req.width, outfit)
    meta["url"] = f"/api/charsheet/characters/{req.slug}/sheets/{meta['file']}"
    return meta


@router.get("/characters/{slug}/sheets")
async def sheets(slug: str):
    d = _sheets_dir(slug)
    out = []
    if d.exists():
        for fp in sorted(d.glob("sheet_*.png"), reverse=True):
            meta = {}
            mp = d / f"{fp.name}.json"
            if mp.exists():
                try:
                    meta = json.loads(mp.read_text("utf-8"))
                except Exception:  # noqa: BLE001
                    meta = {}
            out.append({"file": fp.name, "bytes": fp.stat().st_size,
                        "url": f"/api/charsheet/characters/{slug}/sheets/{fp.name}",
                        **{k: meta.get(k) for k in ("preset", "labels", "size",
                                                    "cells", "missing", "outfit",
                                                    "created_at")}})
    return {"sheets": out}


@router.get("/characters/{slug}/sheets/{fname}")
async def sheet_image(slug: str, fname: str, download: bool = False):
    if "/" in fname or "\\" in fname or ".." in fname:
        raise HTTPException(400, "bad name")
    fp = _sheets_dir(slug) / fname
    if not fp.exists():
        raise HTTPException(404, "no such sheet")
    if download:
        return FileResponse(str(fp), media_type="image/png", filename=f"{slug}_{fname}")
    return FileResponse(str(fp), media_type="image/png")


@router.post("/characters/{slug}/sheets/{fname}/delete")
async def sheet_delete(slug: str, fname: str):
    if "/" in fname or "\\" in fname or ".." in fname:
        raise HTTPException(400, "bad name")
    fp = _sheets_dir(slug) / fname
    if fp.exists():
        fp.unlink()
    mp = _sheets_dir(slug) / f"{fname}.json"
    if mp.exists():
        mp.unlink()
    return {"ok": True}


@router.get("/health")
async def health():
    try:
        from PIL import Image  # noqa: F401
        pil = True
    except Exception:  # noqa: BLE001
        pil = False
    return {"ok": pil, "pil": pil, "root": str(_ROOT),
            "presets": {k: len(v) for k, v in PRESETS.items()}}
