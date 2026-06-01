"""
Chapter REST endpoints.

Mounted at ``/api/projects/{project_id}/chapters``.

Endpoints
---------
GET    /                  → tree of chapters with scene IDs per node
POST   /reparse           → re-derive chapters from script + scenes
PATCH  /{chapter_id}      → rename, recolor, retag, mark manual
POST   /{chapter_id}/split → split a chapter at a specific scene
POST   /{chapter_id}/merge_with_next → merge with the next sibling
POST   /{chapter_id}/preview-llm-batches → dry-run LLM batch planning
GET    /shortcode/{code}  → resolve any shortcode (asset / scene / chapter)
"""

from __future__ import annotations

import logging
from dataclasses import asdict
from typing import Any, Dict, List, Optional
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from backend.database.database import get_session
from backend.database.models import AppSettings, Chapter, Project, Scene
from backend.services.chapters import (
    build_chapter_tree_response,
    rebuild_chapters,
    resolve_llm_batches,
    scenes_in_chapter_tree,
)
from backend.services.shortcode import allocate_shortcode

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/projects/{project_id}/chapters", tags=["chapters"])


# ── Models ────────────────────────────────────────────────────────────


class ChapterUpdateRequest(BaseModel):
    name: Optional[str] = None
    color: Optional[str] = None
    tags: Optional[List[str]] = None
    metadata_patch: Optional[Dict[str, Any]] = None


class ReparseRequest(BaseModel):
    force_auto: bool = False


class SplitRequest(BaseModel):
    at_scene_id: UUID
    new_name: str = "New Chapter"


class PreviewBatchRequest(BaseModel):
    llm_provider: Optional[str] = None  # cloud | ollama | None → use default


# ── Helpers ───────────────────────────────────────────────────────────


async def _ensure_project(session: AsyncSession, project_id: UUID) -> Project:
    result = await session.execute(select(Project).where(Project.id == project_id))
    proj = result.scalars().first()
    if not proj:
        raise HTTPException(status_code=404, detail="Project not found")
    return proj


async def _get_chapter(session: AsyncSession, project_id: UUID, chapter_id: UUID) -> Chapter:
    result = await session.execute(
        select(Chapter).where(Chapter.id == chapter_id, Chapter.project_id == project_id)
    )
    ch = result.scalars().first()
    if not ch:
        raise HTTPException(status_code=404, detail="Chapter not found")
    return ch


async def _build_scene_id_map(
    session: AsyncSession, project_id: UUID
) -> Dict[UUID, List[UUID]]:
    """Group scene IDs by leaf chapter for the tree response."""
    result = await session.execute(
        select(Scene.id, Scene.chapter_id).where(
            Scene.project_id == project_id,
            Scene.chapter_id.is_not(None),
        ).order_by(Scene.order_index)
    )
    out: Dict[UUID, List[UUID]] = {}
    for sid, cid in result.all():
        out.setdefault(cid, []).append(sid)
    return out


# ── Endpoints ─────────────────────────────────────────────────────────


@router.get("/", summary="Chapter tree")
async def get_chapters(
    project_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> Dict[str, Any]:
    """Return the project's chapter tree.

    Each node carries its scene IDs (direct children only — descendants
    show up in the ``children`` subtree).
    """
    await _ensure_project(session, project_id)
    result = await session.execute(
        select(Chapter).where(Chapter.project_id == project_id).order_by(
            Chapter.depth, Chapter.order_index
        )
    )
    chapters = list(result.scalars().all())
    scene_map = await _build_scene_id_map(session, project_id)
    tree = build_chapter_tree_response(chapters, scene_map)

    def _serialize(node) -> Dict[str, Any]:
        d = asdict(node)
        d["children"] = [_serialize(c) for c in node.children]
        return d

    return {
        "project_id": str(project_id),
        "chapter_count": len(chapters),
        "chapters": [_serialize(r) for r in tree],
    }


@router.post("/reparse", summary="Re-derive chapters from current script + scenes")
async def reparse_chapters(
    project_id: UUID,
    req: ReparseRequest,
    session: AsyncSession = Depends(get_session),
) -> Dict[str, Any]:
    """Run the chapter resolution pipeline.

    Called when the script changes, when scenes are reordered, or when
    the user explicitly clicks "Re-parse chapters" in the UI.
    """
    await _ensure_project(session, project_id)
    chapters = await rebuild_chapters(session, project_id, force_auto=req.force_auto)
    scene_map = await _build_scene_id_map(session, project_id)
    tree = build_chapter_tree_response(chapters, scene_map)

    def _serialize(node) -> Dict[str, Any]:
        d = asdict(node)
        d["children"] = [_serialize(c) for c in node.children]
        return d

    return {
        "project_id": str(project_id),
        "chapter_count": len(chapters),
        "chapters": [_serialize(r) for r in tree],
        "rebuild_method": "auto" if req.force_auto else "auto-from-script",
    }


@router.patch("/{chapter_id}", summary="Update a chapter")
async def update_chapter(
    project_id: UUID,
    chapter_id: UUID,
    req: ChapterUpdateRequest,
    session: AsyncSession = Depends(get_session),
) -> Dict[str, Any]:
    """Rename, recolor, retag a chapter.

    Any field provided overrides; omitted fields are left alone.
    Setting any field flips ``source`` to ``"manual"`` so the next
    re-parse won't wipe the customization.
    """
    ch = await _get_chapter(session, project_id, chapter_id)
    changed = False
    if req.name is not None and req.name.strip() != ch.name:
        ch.name = req.name.strip()
        changed = True
    if req.color is not None and req.color != ch.color:
        ch.color = req.color
        changed = True
    if req.tags is not None:
        ch.tags = list(req.tags)
        changed = True
    if req.metadata_patch is not None:
        ch.chapter_metadata = {**(ch.chapter_metadata or {}), **req.metadata_patch}
        changed = True
    if changed:
        ch.source = "manual"
        ch.updated_at = datetime.utcnow()
        session.add(ch)
        await session.commit()
        await session.refresh(ch)
        logger.info(f"Chapter {ch.short_code} updated (source → manual)")
    return _chapter_to_dict(ch)


@router.post("/{chapter_id}/split", summary="Split a chapter at a scene boundary")
async def split_chapter(
    project_id: UUID,
    chapter_id: UUID,
    req: SplitRequest,
    session: AsyncSession = Depends(get_session),
) -> Dict[str, Any]:
    """Split chapter into two siblings at the given scene.

    The original chapter keeps scenes BEFORE ``at_scene_id``; a new
    chapter (named ``new_name``) holds scenes from ``at_scene_id``
    onward.  Both keep their parent and depth.
    """
    ch = await _get_chapter(session, project_id, chapter_id)

    # Validate the split point belongs to this chapter
    target_result = await session.execute(
        select(Scene).where(Scene.id == req.at_scene_id, Scene.chapter_id == chapter_id)
    )
    target_scene = target_result.scalars().first()
    if not target_scene:
        raise HTTPException(
            status_code=400,
            detail="at_scene_id does not belong to this chapter",
        )

    # Find all subsequent scenes
    subsequent_result = await session.execute(
        select(Scene)
        .where(
            Scene.chapter_id == chapter_id,
            Scene.start_time >= target_scene.start_time,
        )
        .order_by(Scene.order_index)
    )
    subsequent = list(subsequent_result.scalars().all())
    if not subsequent:
        raise HTTPException(status_code=400, detail="Nothing to split")

    # Allocate a new chapter at same depth, sibling order = ch.order_index + 1
    sc = await allocate_shortcode(session, project_id, "ch")
    new_ch = Chapter(
        id=uuid4(),
        project_id=project_id,
        parent_chapter_id=ch.parent_chapter_id,
        order_index=ch.order_index + 1,
        depth=ch.depth,
        name=req.new_name,
        short_code=sc,
        color=ch.color,
        auto_generated=False,
        source="manual",
        start_time=float(subsequent[0].start_time),
        end_time=ch.end_time,
        tags=[],
        chapter_metadata={},
    )
    session.add(new_ch)
    await session.flush()

    # Bump order_index on existing siblings after the split point
    sibling_result = await session.execute(
        select(Chapter).where(
            Chapter.project_id == project_id,
            Chapter.parent_chapter_id == ch.parent_chapter_id,
            Chapter.depth == ch.depth,
            Chapter.order_index > ch.order_index,
            Chapter.id != new_ch.id,
        )
    )
    for sib in sibling_result.scalars().all():
        sib.order_index += 1
        session.add(sib)

    # Re-bind subsequent scenes to the new chapter
    for s in subsequent:
        s.chapter_id = new_ch.id
        session.add(s)

    # Update original chapter's end_time
    ch.end_time = float(target_scene.start_time)
    ch.updated_at = datetime.utcnow()
    session.add(ch)

    await session.commit()
    logger.info(f"Split chapter {ch.short_code} → new sibling {new_ch.short_code}")
    return {"original": _chapter_to_dict(ch), "new": _chapter_to_dict(new_ch)}


@router.post("/{chapter_id}/merge_with_next", summary="Merge with next sibling")
async def merge_chapter_with_next(
    project_id: UUID,
    chapter_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> Dict[str, Any]:
    """Merge this chapter with the next sibling at the same depth.

    All of the next sibling's scenes move into this chapter; the next
    sibling is deleted.  The merged chapter inherits the larger time range.
    """
    ch = await _get_chapter(session, project_id, chapter_id)
    next_result = await session.execute(
        select(Chapter).where(
            Chapter.project_id == project_id,
            Chapter.parent_chapter_id == ch.parent_chapter_id,
            Chapter.depth == ch.depth,
            Chapter.order_index == ch.order_index + 1,
        )
    )
    next_ch = next_result.scalars().first()
    if not next_ch:
        raise HTTPException(status_code=400, detail="No next sibling to merge with")

    # Move scenes
    await session.execute(
        select(Scene).where(Scene.chapter_id == next_ch.id)
    )
    move_result = await session.execute(
        select(Scene).where(Scene.chapter_id == next_ch.id)
    )
    for sc in move_result.scalars().all():
        sc.chapter_id = ch.id
        session.add(sc)

    # Inherit time range
    ch.end_time = max(ch.end_time, next_ch.end_time)
    ch.updated_at = datetime.utcnow()
    ch.source = "manual"
    session.add(ch)
    await session.delete(next_ch)

    # Shift order_index for later siblings
    later_result = await session.execute(
        select(Chapter).where(
            Chapter.project_id == project_id,
            Chapter.parent_chapter_id == ch.parent_chapter_id,
            Chapter.depth == ch.depth,
            Chapter.order_index > next_ch.order_index,
        )
    )
    for sib in later_result.scalars().all():
        sib.order_index -= 1
        session.add(sib)

    await session.commit()
    logger.info(f"Merged chapter {ch.short_code} ← {next_ch.short_code}")
    return _chapter_to_dict(ch)


@router.post("/{chapter_id}/preview-llm-batches", summary="Dry-run LLM batching")
async def preview_llm_batches(
    project_id: UUID,
    chapter_id: UUID,
    req: PreviewBatchRequest,
    session: AsyncSession = Depends(get_session),
) -> Dict[str, Any]:
    """Show how the chapter would be batched for LLM generation.

    Useful for the "Generate prompts for this chapter" preview.  No work
    is actually started — this just reports the plan.
    """
    ch = await _get_chapter(session, project_id, chapter_id)
    scenes = await scenes_in_chapter_tree(session, chapter_id)

    settings_result = await session.execute(select(AppSettings).where(AppSettings.id == 1))
    s = settings_result.scalars().first()
    if (req.llm_provider or "cloud").lower() == "ollama":
        limit = int(getattr(s, "llm_chapter_scene_limit_ollama", 12) or 12)
        provider = "ollama"
    else:
        limit = int(getattr(s, "llm_chapter_scene_limit_cloud", 25) or 25)
        provider = "cloud"

    batches = resolve_llm_batches(scenes, limit)
    return {
        "chapter": ch.short_code,
        "provider": provider,
        "limit": limit,
        "total_scenes": len(scenes),
        "batch_count": len(batches),
        "batches": [
            {
                "index": i,
                "scene_count": len(b),
                "scene_ids": [str(sc.id) for sc in b],
                "start_time": float(b[0].start_time) if b else 0.0,
                "end_time": float(b[-1].end_time) if b else 0.0,
            }
            for i, b in enumerate(batches)
        ],
    }


def _chapter_to_dict(ch: Chapter) -> Dict[str, Any]:
    return {
        "id": str(ch.id),
        "project_id": str(ch.project_id),
        "parent_chapter_id": str(ch.parent_chapter_id) if ch.parent_chapter_id else None,
        "order_index": ch.order_index,
        "depth": ch.depth,
        "name": ch.name,
        "short_code": ch.short_code,
        "color": ch.color,
        "auto_generated": ch.auto_generated,
        "source": ch.source,
        "start_time": ch.start_time,
        "end_time": ch.end_time,
        "tags": list(ch.tags or []),
    }


# ── Shortcode resolver ───────────────────────────────────────────────
# Mounted as a top-level router (no project_id prefix) below — but we
# define a separate router instance in shortcodes.py to keep this file
# focused on chapter CRUD.


# Late import to avoid loading datetime everywhere at module top
from datetime import datetime  # noqa: E402
