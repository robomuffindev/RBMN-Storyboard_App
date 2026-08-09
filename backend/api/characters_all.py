"""Unified character list — one door onto every character, whatever made it.

v1.276.0.  This app grew its character modes one at a time and each brought its
own storage, so by now there are two disjoint universes that are joined nowhere:

  DB universe   `studio_characters` rows, UUID-keyed, images as Asset records,
                sprite shards living on a VNCCS worker.  Written by VNCCS
                Native and Klein 2.0.  Read by the /studio page, the Clothes
                tab and /api/studio/vnccs/catalog.

  DISK universe `<project_dir>/_libraries/klein3/chars/<slug>/char.json`,
                slug-keyed, files on the app machine.  Written by Klein 3.0 and
                🧬 Text 2 Image.  Read by Klein 3.0, Charsheet, LoRA and the
                🏠 Studio Hub.

Nothing in klein3.py has ever written a `StudioCharacter` row, which is why a
character made in Klein 3.0 is invisible on the /studio page and absent from
the Clothes tab's dropdown.  It was never a filter bug; the two halves of the
app genuinely did not know about each other's characters.

The fix chosen (Lorenzo, 2026-08-09) is an ADAPTER, not a sync: every character
keeps exactly one home and this module presents them together.  Copying rows
between the two stores would have been faster to write and would have created
two sources of truth that drift apart silently — the worst class of bug to
find later.

Identity is polymorphic and self-describing:

    "k3:dorian"                             a Klein 3.0 / disk character
    "db:2f5cb8a4-1e94-4bb1-be22-..."        a studio_characters row

`parse_ref()` turns either back into (source, id).  Callers that need to act on
a character resolve through here rather than assuming a UUID.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Tuple

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlmodel.ext.asyncio.session import AsyncSession

from backend.database.database import get_session

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/characters", tags=["characters"])

SRC_KLEIN3 = "k3"
SRC_DB = "db"


def make_ref(source: str, ident: str) -> str:
    return f"{source}:{ident}"


def parse_ref(ref: str) -> Tuple[str, str]:
    """('k3', 'dorian') / ('db', '<uuid>').

    A bare value with no prefix is treated as a DB UUID, because that is what
    every pre-existing caller and stored value already means.  Adding the
    prefix must not invalidate data written before it existed.
    """
    r = str(ref or "")
    if r.startswith(f"{SRC_KLEIN3}:"):
        return SRC_KLEIN3, r[len(SRC_KLEIN3) + 1:]
    if r.startswith(f"{SRC_DB}:"):
        return SRC_DB, r[len(SRC_DB) + 1:]
    return SRC_DB, r


# ── Klein 3.0 / disk side ────────────────────────────────────────────────────
def _k3_records() -> List[Dict[str, Any]]:
    """Every Klein 3.0 character, with enough pipeline state to render a card.

    Deliberately mirrors what /api/forge/studio-overview computes, because the
    /studio page is being rebuilt in the Studio Hub's image and the two must
    not disagree about the same character.

    ⚠ Roots are taken from the modules' IMPORT-TIME constants. `cfg.project_dir`
    is overridden from the DB after import, so deriving a path from cfg at
    request time silently points at the wrong folder — that exact mismatch made
    studio-overview count zero sheets in v1.272.1.
    """
    out: List[Dict[str, Any]] = []
    try:
        from backend.api.klein3 import _K3_ROOT, _load, _public_char
    except Exception as e:  # noqa: BLE001
        logger.warning("unified characters: klein3 unavailable: %s", e)
        return out

    try:
        from backend.api.lora import _DS_ROOT
    except Exception:  # noqa: BLE001
        _DS_ROOT = None  # type: ignore[assignment]
    try:
        from backend.api.charsheet import _ROOT as _SHEET_ROOT
    except Exception:  # noqa: BLE001
        _SHEET_ROOT = None  # type: ignore[assignment]

    # datasets grouped by character, so a card can show dataset/LoRA progress
    ds_by_char: Dict[str, List[dict]] = {}
    if _DS_ROOT is not None and Path(_DS_ROOT).exists():
        train_dir = Path(_DS_ROOT).parent / "_train"
        for dj in Path(_DS_ROOT).glob("*/dataset.json"):
            try:
                ds = json.loads(dj.read_text("utf-8"))
            except Exception:  # noqa: BLE001
                continue
            items = ds.get("items", []) or []
            rec = {
                "id": ds.get("id"),
                "total": len(items),
                "rendered": sum(1 for i in items if i.get("status") == "done"),
                "flagged": sum(1 for i in items
                               if (i.get("qc") or {}).get("ok") is False),
                "trigger": " ".join(x for x in (ds.get("trigger"),
                                                ds.get("class_token")) if x),
            }
            tr = train_dir / f"{ds.get('id')}.json"
            if tr.exists():
                try:
                    t = json.loads(tr.read_text("utf-8"))
                    rec["train_stage"] = t.get("stage")
                    rec["installed_lora"] = t.get("installed")
                except Exception:  # noqa: BLE001
                    pass
            ds_by_char.setdefault(str(ds.get("char_slug") or ""), []).append(rec)

    if not Path(_K3_ROOT).exists():
        return out

    for d in sorted(Path(_K3_ROOT).iterdir()):
        if not (d / "char.json").exists():
            continue
        slug = d.name
        try:
            c = _load(slug)
            pub = _public_char(slug, c, full=False)
        except Exception as e:  # noqa: BLE001
            logger.warning("unified characters: %s unreadable: %s", slug, e)
            continue

        sheets = 0
        if _SHEET_ROOT is not None:
            sd = Path(_SHEET_ROOT) / slug
            if sd.exists():
                sheets = len([p for p in sd.glob("*.png")])

        refs = c.get("refs", []) or []
        front = next((r for r in refs if r.get("tag") == "front"), None)
        thumb = (pub.get("active_base_url")
                 or (f"/api/klein3/characters/{slug}/refs/{front['id']}/image"
                     if front else None))
        dsets = ds_by_char.get(slug, [])

        out.append({
            "ref": make_ref(SRC_KLEIN3, slug),
            "source": SRC_KLEIN3,
            "source_label": "Klein 3.0",
            "id": slug,
            "slug": slug,
            "name": pub.get("name") or slug,
            "thumb": thumb,
            "updated_at": pub.get("updated_at"),
            "ref_count": pub.get("ref_count", 0),
            "has_base": bool(pub.get("has_base")),
            "has_front": bool(front),
            "missing_views": pub.get("missing_views", []),
            "fields": pub.get("fields", {}),
            "sheets": sheets,
            "lore_filled": bool(str((c.get("lore") or {}).get("text") or "").strip())
            if isinstance(c.get("lore"), dict) else bool(c.get("lore")),
            "datasets": dsets,
            "installed_loras": [d_["installed_lora"] for d_ in dsets
                                if d_.get("installed_lora")],
            # what this character can be handed to
            "capabilities": ["klein3", "views", "lora", "charsheet",
                             "forge", "clothes"],
        })
    return out


# ── DB side ──────────────────────────────────────────────────────────────────
async def _db_records(session: AsyncSession) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    try:
        from backend.database.models import StudioCharacter
    except Exception as e:  # noqa: BLE001
        logger.warning("unified characters: StudioCharacter unavailable: %s", e)
        return out
    try:
        rows = (await session.execute(
            select(StudioCharacter).order_by(StudioCharacter.created_at.desc())
        )).scalars().all()
    except Exception as e:  # noqa: BLE001
        logger.warning("unified characters: DB query failed: %s", e)
        return out

    for c in rows:
        man = c.manifest if isinstance(c.manifest, dict) else {}
        vn = man.get("vnccs") if isinstance(man.get("vnccs"), dict) else None
        variant = str((vn or {}).get("variant") or "")
        thumb = (vn or {}).get("hero_url")
        out.append({
            "ref": make_ref(SRC_DB, str(c.id)),
            "source": SRC_DB,
            "source_label": ("VNCCS Klein" if variant == "klein"
                             else "VNCCS Native" if vn else "Character Studio"),
            "id": str(c.id),
            "slug": None,
            "name": c.name,
            "thumb": thumb,
            "updated_at": (vn or {}).get("updated_at"),
            "ref_count": len((vn or {}).get("base_versions") or []),
            "has_base": bool((vn or {}).get("active_base")),
            "has_front": bool(thumb),
            "missing_views": [],
            "fields": c.character_info if isinstance(c.character_info, dict) else {},
            "sheets": 0,
            "lore_filled": bool(str(c.description or "").strip()),
            "datasets": [],
            "installed_loras": [],
            "story_id": str(c.story_id) if c.story_id else None,
            "trigger_word": c.trigger_word,
            # only a character with VNCCS sprite/base data can run the existing
            # clothes pipeline — say so rather than letting the UI find out.
            "capabilities": (["vnccs", "clothes", "poses", "emotions", "costumes"]
                             if vn else ["studio"]),
        })
    return out


@router.get("")
async def list_all(session: AsyncSession = Depends(get_session)):
    """Every character from every mode, newest-looking first.

    Sorted by `updated_at` descending with missing timestamps last, so the
    thing you just worked on is at the top regardless of which mode made it.
    """
    k3 = _k3_records()
    db = await _db_records(session)
    chars = k3 + db
    chars.sort(key=lambda c: (c.get("updated_at") or "", c.get("name") or ""),
               reverse=True)
    return {
        "characters": chars,
        "counts": {"total": len(chars), SRC_KLEIN3: len(k3), SRC_DB: len(db)},
        "sources": [
            {"key": SRC_KLEIN3, "label": "Klein 3.0 / Text 2 Image",
             "note": "disk store, slug-keyed"},
            {"key": SRC_DB, "label": "VNCCS Native / Klein 2.0",
             "note": "database store, UUID-keyed"},
        ],
    }


@router.get("/resolve/{ref}")
async def resolve(ref: str, session: AsyncSession = Depends(get_session)):
    """One character by unified ref, for callers holding only a `ref` string."""
    source, ident = parse_ref(ref)
    pool = _k3_records() if source == SRC_KLEIN3 else await _db_records(session)
    hit = next((c for c in pool if c["id"] == ident), None)
    return {"found": bool(hit), "character": hit}
