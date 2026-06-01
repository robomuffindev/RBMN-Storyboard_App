"""
Resolve chapter membership for scenes and produce LLM batch lists.

This module has the *runtime* operations that other services call:

- ``scenes_in_chapter_tree(session, chapter_id)`` — collect all scenes
  belonging to a chapter or any of its descendants, in playback order.
- ``resolve_llm_batches(scenes, limit)`` — chunk a scene list into
  batches sized to the configured LLM limit; respects chapter boundaries
  when called with multiple chapters.
- ``bind_scenes_to_chapters_by_time(session, project_id)`` — assigns
  ``Scene.chapter_id`` based on each scene's start_time falling inside
  a chapter's time range.

These are the "small math" pieces.  The big orchestration is in
``builder.rebuild_chapters``.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from backend.database.models import Chapter, Scene

logger = logging.getLogger(__name__)


async def scenes_in_chapter_tree(
    session: AsyncSession,
    chapter_id: UUID,
) -> List[Scene]:
    """Return all Scenes that belong to ``chapter_id`` or any descendant.

    Walk the chapter tree breadth-first then collect scenes whose
    ``chapter_id`` matches any visited chapter.  Scenes are returned in
    ``order_index`` (project-wide playback order).
    """
    # Collect all descendant chapter IDs
    visited: set = {chapter_id}
    queue: list = [chapter_id]
    while queue:
        parent = queue.pop(0)
        result = await session.execute(
            select(Chapter.id).where(Chapter.parent_chapter_id == parent)
        )
        for row in result.scalars().all():
            if row not in visited:
                visited.add(row)
                queue.append(row)

    if not visited:
        return []

    # Fetch scenes in any of these chapters, ordered by order_index
    result = await session.execute(
        select(Scene)
        .where(Scene.chapter_id.in_(list(visited)))
        .order_by(Scene.order_index)
    )
    return list(result.scalars().all())


def resolve_llm_batches(
    scenes: List[Any],
    limit: int,
    *,
    chapter_id_attr: str = "chapter_id",
) -> List[List[Any]]:
    """Group ``scenes`` into batches of at most ``limit`` per LLM call.

    Respects chapter boundaries — a batch never spans two chapters.  This
    matters when the caller passes a mixed-chapter scene list (e.g. an
    "all scenes" Auto-Gen run).

    If a single chapter has more scenes than ``limit``, that chapter
    gets multiple batches.  The chapter builder normally prevents this
    by auto-splitting first, but the resolver handles it defensively.
    """
    if not scenes:
        return []
    if limit <= 0:
        logger.warning(f"Invalid LLM batch limit {limit} — falling back to 25")
        limit = 25

    batches: List[List[Any]] = []
    current: List[Any] = []
    current_chapter = None
    for sc in scenes:
        sc_ch = getattr(sc, chapter_id_attr, None)
        if current and (sc_ch != current_chapter or len(current) >= limit):
            batches.append(current)
            current = []
        if not current:
            current_chapter = sc_ch
        current.append(sc)
    if current:
        batches.append(current)

    logger.info(
        f"LLM batches: {len(scenes)} scenes → {len(batches)} batches (limit={limit})"
    )
    return batches


async def bind_scenes_to_chapters_by_time(
    session: AsyncSession,
    project_id: UUID,
) -> int:
    """Re-bind each scene's ``chapter_id`` based on time-range overlap.

    For each scene S, find the LEAF chapter whose time range contains
    S.start_time (or use the closest chapter if none strictly contains).

    Returns the number of scenes whose chapter_id changed.
    """
    # Fetch all chapters + scenes for the project
    ch_result = await session.execute(
        select(Chapter).where(Chapter.project_id == project_id).order_by(Chapter.depth.desc(), Chapter.order_index)
    )
    chapters = list(ch_result.scalars().all())
    if not chapters:
        return 0

    sc_result = await session.execute(
        select(Scene).where(Scene.project_id == project_id).order_by(Scene.order_index)
    )
    scenes = list(sc_result.scalars().all())

    # Build leaf-first list so deepest chapter wins when ranges overlap
    leaves_first = sorted(chapters, key=lambda c: (-c.depth, c.order_index))

    n_changed = 0
    for sc in scenes:
        # Pick first leaf whose time range contains the scene start
        new_chapter_id = None
        for ch in leaves_first:
            if ch.start_time <= sc.start_time < (ch.end_time or float("inf")):
                # Confirm this is a leaf — has no children
                has_children = any(c.parent_chapter_id == ch.id for c in chapters)
                if not has_children:
                    new_chapter_id = ch.id
                    break

        # Fallback — closest chapter by start_time
        if new_chapter_id is None and leaves_first:
            closest = min(
                (c for c in leaves_first if not any(x.parent_chapter_id == c.id for x in chapters)),
                key=lambda c: abs(c.start_time - sc.start_time),
                default=None,
            )
            if closest:
                new_chapter_id = closest.id

        if new_chapter_id != sc.chapter_id:
            sc.chapter_id = new_chapter_id
            n_changed += 1

    if n_changed:
        await session.commit()
    logger.info(
        f"Bind scenes→chapters: project={project_id} chapters={len(chapters)} "
        f"scenes={len(scenes)} changed={n_changed}"
    )
    return n_changed
