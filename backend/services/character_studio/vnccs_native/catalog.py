"""Catalog + project-linking for VNCCS Native characters.

Ingested VNCCS characters live as StudioCharacter rows (VNCCS link under
manifest["vnccs"]) with their sprites/faces/sheets in the hidden Character-Studio
project's asset store.  This module lists that catalog and copies a character's
images into a target project so they can be used as scene references — the
"project-linking" step that makes cataloged characters usable in real projects.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import UUID

from sqlmodel import select

from backend.config import settings as cfg
from backend.database.models import Asset, AssetType, Project, StudioCharacter

logger = logging.getLogger(__name__)


def _vnccs_manifest(c: StudioCharacter) -> Optional[dict]:
    m = c.manifest or {}
    v = m.get("vnccs")
    return v if isinstance(v, dict) else None


async def list_catalog(session) -> List[Dict[str, Any]]:
    """All ingested VNCCS Native characters with a summary of their outputs."""
    rows = (await session.execute(select(StudioCharacter))).scalars().all()
    out: List[Dict[str, Any]] = []
    for c in rows:
        v = _vnccs_manifest(c)
        if not v:
            continue
        outputs = v.get("outputs") or {}
        # thumbnail: user-chosen hero, else the ACTIVE base version, else the
        # newest base version (all already carry served /api/files/ urls)
        hero_url = v.get("hero_url")
        if not hero_url:
            versions = [bv for bv in (v.get("base_versions") or []) if isinstance(bv, dict)]
            active = v.get("active_base")
            hero_url = next((bv.get("url") for bv in versions if bv.get("id") == active), None)
            if not hero_url and versions:
                hero_url = versions[-1].get("url")
        out.append({
            "character_id": str(c.id),
            "name": c.name,
            "story_id": str(c.story_id) if c.story_id else None,
            "ref": v.get("ref"),
            "host": v.get("host"),
            "step": v.get("step"),
            "variant": v.get("variant") or "native",
            "hero_url": hero_url,
            "hero_asset_id": v.get("hero_asset_id"),
            "outputs": {label: len(ids) for label, ids in outputs.items()},
            "updated_at": v.get("updated_at"),
            "form": v.get("form"),   # saved Create-tab form (character_info + gen_settings)
            "hosts": v.get("hosts") or [],   # workers holding this character's sprites
        })
    out.sort(key=lambda r: r.get("updated_at") or "", reverse=True)
    return out


async def _studio_project(session) -> Optional[Project]:
    rows = (await session.execute(select(Project))).scalars().all()
    for p in rows:
        if (p.settings or {}).get("studio_system"):
            return p
    return None


async def link_to_project(
    session,
    *,
    character_id: UUID,
    project_id: UUID,
    labels: Optional[List[str]] = None,
    max_per_label: int = 0,
) -> Dict[str, Any]:
    """Copy a cataloged VNCCS character's images into ``project_id`` as CHARACTER
    reference assets.  ``labels`` selects which output groups (default: the hero
    group — sheet/upscaled/sprites); ``max_per_label`` caps images per group
    (0 = all).  Returns {created_asset_ids, character, project_id}.
    """
    char = await session.get(StudioCharacter, character_id)
    if not char:
        raise ValueError("character not found")
    v = _vnccs_manifest(char)
    if not v:
        raise ValueError("character is not a VNCCS Native character")
    target = await session.get(Project, project_id)
    if not target:
        raise ValueError("target project not found")
    studio = await _studio_project(session)
    if not studio:
        raise ValueError("studio system project missing")

    outputs: Dict[str, List[str]] = v.get("outputs") or {}
    # output keys are namespaced "step/label" (e.g. "creator/upscaled"); match on
    # the label suffix so callers can pass plain labels.
    def _suffix(k: str) -> str:
        return k.split("/")[-1]
    if labels:
        wanted = {k: ids for k, ids in outputs.items() if k in labels or _suffix(k) in labels}
    else:
        # hero set: prefer a representative still + the sprite sheet
        pref = ["upscaled", "sheet", "original_upscaled", "sprites", "original_sprites", "faces"]
        wanted = {}
        for p in pref:
            match = {k: ids for k, ids in outputs.items() if _suffix(k) == p and ids}
            if match:
                wanted = match
                break
        if not wanted:  # fall back to everything
            wanted = outputs

    from backend.utils.file_utils import sha256_file
    created: List[Asset] = []
    dest_root = Path(cfg.project_dir) / str(target.id) / "assets" / "vnccs" / _safe(char.name)
    for label, ids in wanted.items():
        picked = ids if not max_per_label else ids[:max_per_label]
        for aid in picked:
            src_asset = await session.get(Asset, UUID(aid)) if _is_uuid(aid) else None
            if not src_asset or not src_asset.rel_path:
                continue
            src_abs = Path(cfg.project_dir) / str(studio.id) / src_asset.rel_path
            if not src_abs.exists():
                continue
            dest_dir = dest_root / label
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest_abs = dest_dir / src_abs.name
            if dest_abs != src_abs:
                shutil.copy2(src_abs, dest_abs)
            rel = dest_abs.relative_to(Path(cfg.project_dir) / str(target.id))
            try:
                sha = sha256_file(dest_abs)
            except Exception:
                sha = ""
            asset = Asset(
                project_id=target.id, filename=dest_abs.name, rel_path=str(rel),
                asset_type=AssetType.CHARACTER, sha256=sha or "",
                file_size=dest_abs.stat().st_size if dest_abs.exists() else 0,
                meta={"vnccs": {"character": char.name, "label": label,
                                "source_character_id": str(char.id)}},
            )
            session.add(asset)
            created.append(asset)
    await session.commit()
    ids_out = []
    for a in created:
        await session.refresh(a)
        ids_out.append(str(a.id))
    return {"created_asset_ids": ids_out, "character": char.name, "project_id": str(target.id)}


def _safe(name: str) -> str:
    return "".join(ch if (ch.isalnum() or ch in " _-") else "_" for ch in (name or "char")).strip() or "char"


def _is_uuid(s: str) -> bool:
    try:
        UUID(str(s))
        return True
    except Exception:
        return False
