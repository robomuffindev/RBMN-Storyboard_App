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
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Tuple

# ── installed-LoRA discovery (v1.276.42) ────────────────────────────────────
# Cached because /api/characters is polled every 20 seconds by the grid and
# this crosses the LAN to the Krea 2 box. A stale-by-two-minutes answer to
# "does this character have a LoRA" is fine; a LAN round trip on every poll is
# not. Never fatal — the box being unreachable must not empty the grid.
_LORA_CACHE: Dict[str, Any] = {"at": 0.0, "by_char": {}}
_LORA_TTL = 120.0


def _installed_loras_by_char() -> Dict[str, List[str]]:
    """{character slug: [installed lora filenames]}, from the WORKER's own list.

    Ground truth for "has this character got a LoRA" is the file sitting on the
    box, not a state file the app happens to have written. Matching is forge's:
    our trained files are named from the dataset id (`redv1-bca382-000036`) or
    a dest_name keeping the character prefix (`redv1-v2-e21`), so the dataset
    id and its slug part are both tried, longest prefix first. Files we did not
    train match nothing and are ignored — his other 37 LoRAs are not characters.

    ⚠⚠ NEVER BLOCKS THE CALLER. `_krea2_models` is a plain blocking urlopen
    with a 15s timeout, and this is reached from `async def list_all` — so
    calling it inline would pin the event loop for up to 15 seconds every time
    the cache expired and the Krea 2 box happened to be off or DHCP-moved. That
    is the v1.276.41 failure in a new place: not a deadlock, but the whole app
    unresponsive. The refresh runs on a BACKGROUND THREAD and the caller gets
    whatever is cached right now — a badge that is two minutes stale is a fair
    price for a grid that always answers.
    """
    now = time.time()
    cached: Dict[str, List[str]] = _LORA_CACHE.get("by_char") or {}
    if now - float(_LORA_CACHE.get("at") or 0) < _LORA_TTL:
        return cached
    if _LORA_CACHE.get("refreshing"):
        return cached
    _LORA_CACHE["refreshing"] = True
    threading.Thread(target=_refresh_installed_loras, daemon=True,
                     name="lora-discover").start()
    return cached


def _refresh_installed_loras() -> None:
    """The LAN half of `_installed_loras_by_char`, on its own thread."""
    by_char: Dict[str, List[str]] = {}
    try:
        # ⚠ reuse forge's own fetch rather than reconstructing the URL — the
        # model-list endpoint has already moved once, and one guessed path is
        # exactly how a "no LoRA" badge comes back.
        from backend.api.forge import _krea2_host, _krea2_models
        from backend.api.lora import _DS_ROOT

        files = [f for f in (_krea2_models(_krea2_host(), "loras") or [])
                 if str(f).lower().endswith(".safetensors")]
        # ⚠ `_krea2_models` SWALLOWS a connection failure and returns [], so an
        # empty list is ambiguous: "this box has no LoRAs" and "this box is
        # unreachable" look identical. The except-branch below could therefore
        # never fire on the normal failure path, and the badge flapped off
        # exactly as its own comment said it must not. An empty answer is
        # treated as no answer and the previous cache is kept.
        if not files:
            logger.info("unified characters: LoRA list came back empty — "
                        "keeping the previous answer rather than clearing badges")
            _LORA_CACHE["refreshing"] = False
            return
        # (prefix, slug) longest-first, exactly as forge._lora_triggers does
        cands: List[Tuple[str, str]] = []
        if _DS_ROOT.exists():
            for dj in _DS_ROOT.glob("*/dataset.json"):
                try:
                    ds = json.loads(dj.read_text("utf-8"))
                except Exception:  # noqa: BLE001
                    continue
                cs = str(ds.get("char_slug") or "")
                ds_id = str(ds.get("id") or "")
                if not cs or not ds_id:
                    continue
                cands.append((ds_id.lower(), cs))
                stem = ds_id.rsplit("-", 1)[0]
                if stem:
                    cands.append((stem.lower() + "-", cs))
        cands.sort(key=lambda c: -len(c[0]))
        for f in files:
            base = str(f).replace("\\", "/").split("/")[-1].lower()
            for prefix, cs in cands:
                if base.startswith(prefix):
                    by_char.setdefault(cs, []).append(str(f))
                    break
    except Exception as e:  # noqa: BLE001
        logger.info("unified characters: could not list installed LoRAs (%s)", e)
        # keep whatever we had rather than flapping the badge off on one
        # failed poll — a worker that moved IP should not delete a LoRA.
        _LORA_CACHE["refreshing"] = False
        return
    _LORA_CACHE.update({"at": time.time(), "by_char": by_char, "refreshing": False})

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

    # ⭐ v1.276.42 — LoRAs trained BEFORE the in-app pipeline show up too.
    # Lorenzo: "it doesnt show a lora was made for that character when the ones
    # who have them already have them like our initial lora tests."
    # The card asked ONE source — `_train/<ds_id>.json` — which only exists for
    # runs the in-app 🚀 Train button drove. His first LoRAs were trained from
    # `scripts/`, so the state file never existed and three real installed
    # LoRAs read as "no LoRA". The file on the box is the ground truth, so ask
    # the box, and match filenames back the way forge already does.
    # ⚠ Cached: /api/characters polls every 20s and this crosses the LAN.
    for slug_, files in _installed_loras_by_char().items():
        for rec in ds_by_char.get(slug_, []) or []:
            rec.setdefault("installed_lora", None)
        if files and not any((r.get("installed_lora") for r in ds_by_char.get(slug_, []))):
            # No dataset row claimed one — attach the newest to the first row,
            # or synthesise a row so a character trained entirely outside the
            # app still reads as "has a LoRA".
            rows = ds_by_char.setdefault(slug_, [])
            if rows:
                rows[0]["installed_lora"] = files[-1]
                rows[0].setdefault("train_stage", "installed (found on the worker)")
            else:
                rows.append({"id": None, "total": 0, "rendered": 0, "flagged": 0,
                             "trigger": None, "external": True,
                             "train_stage": "installed (found on the worker)",
                             "installed_lora": files[-1]})

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
            # ⚠ v1.276.42 — the UNION of what the dataset rows claim and what
            # is actually installed on the worker. Squeezing the discovered
            # files through dataset rows LOSES some: redv1 has two installed
            # LoRAs and one dataset row, so a row-only answer showed one of
            # them. The badge asks "does this character have a LoRA" and the
            # honest answer is every file that belongs to it.
            "installed_loras": sorted({
                *(d_["installed_lora"] for d_ in dsets if d_.get("installed_lora")),
                *_installed_loras_by_char().get(slug, []),
            }),
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
