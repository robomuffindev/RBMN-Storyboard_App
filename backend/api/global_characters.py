"""Global Character Library — reusable characters across projects.

The library lives at `settings.project_dir / "_global_characters/{id}/"`
holding the character's main image plus copies of the reference images
used in its last generation.  Each row in `global_characters` points to
files inside that folder so the entry is portable: the only thing tying
it to the original project is the `source_project_id` attribution field,
which goes NULL if the source project is deleted (no FK cascade — see
the model definition).

Copy semantics: pressing "Add to project" creates an INDEPENDENT copy
inside the target project.  Editing the project copy does NOT mutate the
library entry, and editing the library entry does NOT push changes into
projects already using it.  Matches how stock-photo / clipart libraries
work — least surprising for users.
"""
from __future__ import annotations

import logging
import shutil
import uuid as _uuid
from pathlib import Path
from typing import Any, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlmodel import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings as app_settings
from backend.database.database import get_session
from backend.database.models import (
    GlobalCharacter,
    GlobalCharacterVersion,
    Project,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/global-characters", tags=["global_characters"])


# ─── Storage helpers ─────────────────────────────────────────────────────


def _global_root() -> Path:
    """Root folder for all global characters.

    Lives alongside projects (under `project_dir`) so a user who relocates
    their project directory takes the library with them automatically.
    The leading underscore avoids collision with a user-named project.
    """
    root = app_settings.project_dir / "_global_characters"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _char_dir(char_id: UUID) -> Path:
    """Per-character folder."""
    d = _global_root() / str(char_id)
    d.mkdir(parents=True, exist_ok=True)
    return d


def _versions_dir(char_id: UUID) -> Path:
    d = _char_dir(char_id) / "versions"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _copy_into_global(
    source_abs: Path, char_id: UUID, *, subdir: str = ""
) -> Optional[str]:
    """Copy a file into the global character folder, return path REL to project_dir.

    Returns None if the source doesn't exist.  Filename is preserved when
    possible; collisions get a UUID suffix so we never overwrite an
    existing image (no silent data loss).
    """
    if not source_abs.exists():
        logger.warning(f"_copy_into_global: source not found {source_abs}")
        return None
    dest_dir = _char_dir(char_id) / subdir if subdir else _char_dir(char_id)
    dest_dir.mkdir(parents=True, exist_ok=True)
    name = source_abs.name
    dest = dest_dir / name
    if dest.exists():
        # Avoid clobber — append a short random suffix
        suffix = source_abs.suffix
        stem = source_abs.stem
        dest = dest_dir / f"{stem}_{_uuid.uuid4().hex[:6]}{suffix}"
    shutil.copy2(source_abs, dest)
    return str(dest.relative_to(app_settings.project_dir))


def _resolve_rel(rel_path: str) -> Optional[Path]:
    """Resolve a rel path (under project_dir) to an absolute path if it exists."""
    if not rel_path:
        return None
    abs_p = app_settings.project_dir / rel_path
    return abs_p if abs_p.exists() else None


# ─── Pydantic schemas ────────────────────────────────────────────────────


class GlobalCharacterCreate(BaseModel):
    """Payload when saving a project character into the global library."""

    name: str
    description: str = ""
    image_path: str = ""  # rel to project_dir — already in the source project
    last_prompt: str = ""
    reference_images: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    source_project_id: Optional[UUID] = None  # caller fills with current project


class GlobalCharacterUpdate(BaseModel):
    """Edit name/desc/tags on an existing library entry."""

    name: Optional[str] = None
    description: Optional[str] = None
    tags: Optional[list[str]] = None


class GlobalCharacterRead(BaseModel):
    id: UUID
    name: str
    description: str
    image_path: str
    last_prompt: str
    reference_images: list[str]
    tags: list[str]
    source_project_id: Optional[UUID]
    source_project_name: str
    version_count: int = 0
    created_at: Any  # datetime, serialized by FastAPI
    updated_at: Any


class GlobalCharacterVersionRead(BaseModel):
    id: UUID
    image_path: str
    prompt: str
    reference_images: list[str]
    note: str
    created_at: Any


class ImportRequest(BaseModel):
    """Import a library entry into a target project."""

    project_id: UUID


class ImportResponse(BaseModel):
    character_index: int  # index in project.settings["characters"]
    project_image_path: str  # rel path inside the project where the image landed


# ─── Endpoints ───────────────────────────────────────────────────────────


@router.post("", response_model=GlobalCharacterRead, summary="Save a character to the global library")
async def create_global_character(
    payload: GlobalCharacterCreate,
    session: AsyncSession = Depends(get_session),
) -> GlobalCharacterRead:
    """Save a project character to the global library.

    Copies the image + every reference image into the library folder so
    the entry is portable.  Records the source project's name at save
    time so attribution survives the project's deletion.
    """
    # Look up source project name (best-effort — payload may have null source)
    src_proj_name = ""
    if payload.source_project_id:
        proj = await session.get(Project, payload.source_project_id)
        if proj:
            src_proj_name = proj.name

    new_id = _uuid.uuid4()

    # Copy main image into the global folder
    new_image_rel = ""
    if payload.image_path:
        src_abs = _resolve_rel(payload.image_path)
        if src_abs:
            copied = _copy_into_global(src_abs, new_id)
            new_image_rel = copied or ""

    # Copy reference images (preserve order, drop any that are missing)
    new_refs: list[str] = []
    for ref in payload.reference_images:
        src_abs = _resolve_rel(ref)
        if src_abs:
            copied = _copy_into_global(src_abs, new_id, subdir="refs")
            if copied:
                new_refs.append(copied)

    char = GlobalCharacter(
        id=new_id,
        name=payload.name.strip() or "Untitled",
        description=payload.description.strip(),
        image_path=new_image_rel,
        last_prompt=payload.last_prompt,
        reference_images=new_refs,
        tags=[t.strip() for t in payload.tags if t.strip()],
        source_project_id=payload.source_project_id,
        source_project_name=src_proj_name,
    )
    session.add(char)
    await session.commit()
    await session.refresh(char)

    logger.info(
        f"Saved global character {char.id} '{char.name}' "
        f"(image={'yes' if new_image_rel else 'no'}, refs={len(new_refs)}, tags={char.tags})"
    )

    return GlobalCharacterRead(
        id=char.id,
        name=char.name,
        description=char.description,
        image_path=char.image_path,
        last_prompt=char.last_prompt,
        reference_images=char.reference_images,
        tags=char.tags,
        source_project_id=char.source_project_id,
        source_project_name=char.source_project_name,
        version_count=0,
        created_at=char.created_at,
        updated_at=char.updated_at,
    )


@router.get("", response_model=list[GlobalCharacterRead], summary="List global characters")
async def list_global_characters(
    search: Optional[str] = None,
    tag: Optional[str] = None,
    source_project_id: Optional[UUID] = None,
    session: AsyncSession = Depends(get_session),
) -> list[GlobalCharacterRead]:
    """Filterable list.

    `search` matches name OR description (case-insensitive substring).
    `tag` matches any character that has the tag in its `tags` list.
    `source_project_id` filters by attribution.
    """
    stmt = select(GlobalCharacter).order_by(GlobalCharacter.created_at.desc())
    if source_project_id:
        stmt = stmt.where(GlobalCharacter.source_project_id == source_project_id)
    result = await session.execute(stmt)
    rows = result.scalars().all()

    out: list[GlobalCharacterRead] = []
    needle = (search or "").strip().lower()
    for c in rows:
        if needle and needle not in c.name.lower() and needle not in c.description.lower():
            continue
        if tag and tag not in (c.tags or []):
            continue
        # Cheap count of versions
        vstmt = select(GlobalCharacterVersion).where(GlobalCharacterVersion.global_character_id == c.id)
        vcount = len((await session.execute(vstmt)).scalars().all())
        out.append(
            GlobalCharacterRead(
                id=c.id,
                name=c.name,
                description=c.description,
                image_path=c.image_path,
                last_prompt=c.last_prompt,
                reference_images=c.reference_images,
                tags=c.tags,
                source_project_id=c.source_project_id,
                source_project_name=c.source_project_name,
                version_count=vcount,
                created_at=c.created_at,
                updated_at=c.updated_at,
            )
        )
    return out


@router.get("/tags", response_model=list[str], summary="Distinct tag list")
async def list_tags(
    session: AsyncSession = Depends(get_session),
) -> list[str]:
    """Distinct tag set across all library characters, sorted."""
    result = await session.execute(select(GlobalCharacter))
    tags: set[str] = set()
    for c in result.scalars().all():
        for t in c.tags or []:
            if t and isinstance(t, str):
                tags.add(t)
    return sorted(tags)


@router.get("/{char_id}", response_model=GlobalCharacterRead, summary="Get one")
async def get_global_character(
    char_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> GlobalCharacterRead:
    c = await session.get(GlobalCharacter, char_id)
    if not c:
        raise HTTPException(404, f"Global character {char_id} not found")
    vstmt = select(GlobalCharacterVersion).where(GlobalCharacterVersion.global_character_id == c.id)
    vcount = len((await session.execute(vstmt)).scalars().all())
    return GlobalCharacterRead(
        id=c.id,
        name=c.name,
        description=c.description,
        image_path=c.image_path,
        last_prompt=c.last_prompt,
        reference_images=c.reference_images,
        tags=c.tags,
        source_project_id=c.source_project_id,
        source_project_name=c.source_project_name,
        version_count=vcount,
        created_at=c.created_at,
        updated_at=c.updated_at,
    )


@router.put("/{char_id}", response_model=GlobalCharacterRead, summary="Update name / description / tags")
async def update_global_character(
    char_id: UUID,
    patch: GlobalCharacterUpdate,
    session: AsyncSession = Depends(get_session),
) -> GlobalCharacterRead:
    c = await session.get(GlobalCharacter, char_id)
    if not c:
        raise HTTPException(404, f"Global character {char_id} not found")
    from datetime import datetime as _dt

    if patch.name is not None:
        c.name = patch.name.strip() or c.name
    if patch.description is not None:
        c.description = patch.description.strip()
    if patch.tags is not None:
        c.tags = [t.strip() for t in patch.tags if t.strip()]
    c.updated_at = _dt.utcnow()
    await session.commit()
    await session.refresh(c)
    return await get_global_character(char_id, session)


@router.delete("/{char_id}", summary="Delete a global character + its folder")
async def delete_global_character(
    char_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> dict[str, bool]:
    c = await session.get(GlobalCharacter, char_id)
    if not c:
        raise HTTPException(404, f"Global character {char_id} not found")
    # Remove the on-disk folder (best-effort; DB row removal is the contract)
    try:
        folder = _char_dir(char_id)
        if folder.exists():
            shutil.rmtree(folder, ignore_errors=True)
    except Exception as e:
        logger.warning(f"delete_global_character: folder cleanup failed for {char_id}: {e}")
    # Also drop versions
    vstmt = select(GlobalCharacterVersion).where(GlobalCharacterVersion.global_character_id == char_id)
    for v in (await session.execute(vstmt)).scalars().all():
        await session.delete(v)
    await session.delete(c)
    await session.commit()
    return {"ok": True}


@router.get(
    "/{char_id}/versions",
    response_model=list[GlobalCharacterVersionRead],
    summary="Version history for a library character",
)
async def list_versions(
    char_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> list[GlobalCharacterVersionRead]:
    c = await session.get(GlobalCharacter, char_id)
    if not c:
        raise HTTPException(404, f"Global character {char_id} not found")
    vstmt = (
        select(GlobalCharacterVersion)
        .where(GlobalCharacterVersion.global_character_id == char_id)
        .order_by(GlobalCharacterVersion.created_at.desc())
    )
    return [
        GlobalCharacterVersionRead(
            id=v.id,
            image_path=v.image_path,
            prompt=v.prompt,
            reference_images=v.reference_images,
            note=v.note,
            created_at=v.created_at,
        )
        for v in (await session.execute(vstmt)).scalars().all()
    ]


@router.post(
    "/{char_id}/import",
    response_model=ImportResponse,
    summary="Copy a library character into a target project",
)
async def import_to_project(
    char_id: UUID,
    req: ImportRequest,
    session: AsyncSession = Depends(get_session),
) -> ImportResponse:
    """Copy a library character INTO a target project.

    Copy semantics — the library entry is unchanged after import; the new
    project character is independent.  Files are duplicated into the
    project's own folder so deleting either side doesn't break the other.
    """
    c = await session.get(GlobalCharacter, char_id)
    if not c:
        raise HTTPException(404, f"Global character {char_id} not found")
    proj = await session.get(Project, req.project_id)
    if not proj:
        raise HTTPException(404, f"Project {req.project_id} not found")

    # Target subfolder inside the project — sits next to user-uploaded refs
    proj_chars_dir = app_settings.project_dir / str(proj.id) / "characters"
    proj_chars_dir.mkdir(parents=True, exist_ok=True)

    # Copy main image
    new_image_rel = ""
    if c.image_path:
        src_abs = _resolve_rel(c.image_path)
        if src_abs and src_abs.exists():
            dest = proj_chars_dir / src_abs.name
            if dest.exists():
                dest = proj_chars_dir / f"{src_abs.stem}_{_uuid.uuid4().hex[:6]}{src_abs.suffix}"
            shutil.copy2(src_abs, dest)
            new_image_rel = str(dest.relative_to(app_settings.project_dir))

    # Copy ref images
    new_refs: list[str] = []
    for ref in c.reference_images or []:
        src_abs = _resolve_rel(ref)
        if src_abs and src_abs.exists():
            dest = proj_chars_dir / src_abs.name
            if dest.exists():
                dest = proj_chars_dir / f"{src_abs.stem}_{_uuid.uuid4().hex[:6]}{src_abs.suffix}"
            shutil.copy2(src_abs, dest)
            new_refs.append(str(dest.relative_to(app_settings.project_dir)))

    # Append to project.settings["characters"]
    proj_settings = dict(proj.settings or {})
    chars = list(proj_settings.get("characters", []))
    new_char: dict[str, Any] = {
        "name": c.name,
        "description": c.description,
        "image_path": new_image_rel,
        "last_prompt": c.last_prompt,
        "reference_images": new_refs,
        # Stash the library origin id so the UI can show "imported from library"
        # and offer a 'Re-import' / 'Update from library' affordance later.
        "library_origin_id": str(c.id),
    }
    chars.append(new_char)
    proj_settings["characters"] = chars
    proj.settings = proj_settings
    await session.commit()

    logger.info(
        f"Imported global character {c.id} '{c.name}' into project {proj.id} "
        f"as index {len(chars) - 1}"
    )
    return ImportResponse(
        character_index=len(chars) - 1,
        project_image_path=new_image_rel,
    )
