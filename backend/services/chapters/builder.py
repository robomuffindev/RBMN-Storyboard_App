"""
Chapter builder — the main orchestration.

``rebuild_chapters(session, project_id)`` is the single entry point
that re-derives the chapter tree for a project.  It handles:

1. Reading the user's script (Lyrics.initial_text)
2. Parsing headers via ``parser.parse_script_headers``
3. Mapping each header to a start time using the reconciled word list
   (Lyrics.words)
4. Diffing against existing Chapter rows so user-renamed chapters
   survive a re-parse
5. Creating / updating / deleting Chapter rows
6. Allocating shortcodes for new chapters
7. Auto-splitting oversized chapters into sub-chapters
8. Binding each scene's ``chapter_id``

The function is idempotent — calling it twice in a row produces the
same DB state.  Logs every decision so the debug snapshot can show
exactly what happened.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from backend.database.models import AppSettings, Chapter, Lyrics, Project, Scene
from backend.services.shortcode import allocate_shortcode

from .auto_split import SubChapterPlan, auto_split_oversized_chapters
from .parser import ParsedScript, ScriptHeader, diff_headers, parse_script_headers
from .resolver import bind_scenes_to_chapters_by_time

logger = logging.getLogger(__name__)


# ── Rebuild settings ──────────────────────────────────────────────────


@dataclass
class RebuildSettings:
    """Cached AppSettings values relevant to chapter rebuild."""

    auto_split_threshold: int = 25
    max_depth: int = 2
    cloud_limit: int = 25
    ollama_limit: int = 12


async def _load_settings(session: AsyncSession) -> RebuildSettings:
    result = await session.execute(select(AppSettings).where(AppSettings.id == 1))
    s = result.scalars().first()
    if not s:
        return RebuildSettings()
    return RebuildSettings(
        auto_split_threshold=int(getattr(s, "chapter_auto_split_threshold", 25) or 25),
        max_depth=int(getattr(s, "chapter_max_depth", 2) or 2),
        cloud_limit=int(getattr(s, "llm_chapter_scene_limit_cloud", 25) or 25),
        ollama_limit=int(getattr(s, "llm_chapter_scene_limit_ollama", 12) or 12),
    )


# ── Public types ──────────────────────────────────────────────────────


@dataclass
class ChapterTreeNode:
    """Serializable chapter tree returned by ``build_chapter_tree_response``."""

    id: str
    project_id: str
    parent_chapter_id: Optional[str]
    order_index: int
    depth: int
    name: str
    short_code: str
    color: str
    auto_generated: bool
    source: str
    start_time: float
    end_time: float
    tags: List[str]
    scene_count: int
    scene_ids: List[str]
    children: List["ChapterTreeNode"] = field(default_factory=list)


# ── Header → time lookup ──────────────────────────────────────────────


def _header_start_time(header: ScriptHeader, words: List[Dict[str, Any]]) -> Optional[float]:
    """Look up the audio timestamp for the word the header points at.

    Returns None if the word index is out of range (header at end of script
    with no following words).  Caller decides what to do (typically place
    the chapter at end of audio).
    """
    if not words:
        return None
    idx = header.word_index_in_clean
    if idx < 0:
        return 0.0
    if idx >= len(words):
        return None
    w = words[idx]
    return float(w.get("start", 0.0))


def _project_audio_end_time(scenes: List[Scene]) -> float:
    """Best-effort: latest scene end_time as the project's overall end."""
    if not scenes:
        return 0.0
    return max(float(sc.end_time or 0.0) for sc in scenes)


# ── Header → Chapter rows ─────────────────────────────────────────────


async def _create_chapters_from_headers(
    session: AsyncSession,
    project_id: UUID,
    parsed: ParsedScript,
    words: List[Dict[str, Any]],
    scenes: List[Scene],
    existing_chapters: List[Chapter],
    settings_: RebuildSettings,
) -> List[Chapter]:
    """Build (and persist) Chapter rows from parsed script headers.

    Strategy:
    1. Diff old chapters against new headers using normalized keys
    2. Carry forward matched chapter rows (preserve id, color, tags,
       manual rename state)
    3. Delete chapters that disappeared AND have no manual customization
    4. Create new chapters for added headers
    """
    if not parsed.headers:
        return []

    # Reconstruct "old headers" view of existing chapters (for diff)
    # We only consider chapters that came from script_header source —
    # auto-generated ones get blown away on re-parse.
    old_script_chapters = [c for c in existing_chapters if c.source == "script_header"]
    pseudo_old: List[ScriptHeader] = []
    for c in old_script_chapters:
        pseudo_old.append(ScriptHeader(
            depth=c.depth,
            raw_depth=c.depth + 1,
            name=c.name,
            char_offset=c.script_offset_start or 0,
            word_index_in_clean=0,  # unused for matching
        ))

    diff = diff_headers(pseudo_old, parsed.headers)
    logger.info(
        f"Chapter diff: matched={len(diff['matched'])} "
        f"added={len(diff['added'])} removed={len(diff['removed'])}"
    )

    # Pre-compute header times — clamped to audio_end so a header that
    # falls past the last scene gets pinned to the end of the timeline.
    audio_end = _project_audio_end_time(scenes)
    times: List[float] = []
    for h in parsed.headers:
        t = _header_start_time(h, words)
        if t is None:
            t = audio_end
        times.append(min(max(t, 0.0), audio_end))

    # Build the parent stack for depth tracking — index per depth
    # parent_stack[d] = chapter row currently open at depth d
    parent_stack: List[Optional[Chapter]] = [None] * (settings_.max_depth + 1)
    order_counter: List[int] = [0] * (settings_.max_depth + 1)

    # Map matched old chapter index → existing Chapter row (so we can update in place)
    matched_old_by_new: Dict[int, Chapter] = {}
    for old_idx, new_idx in diff["matched"]:
        matched_old_by_new[new_idx] = old_script_chapters[old_idx]

    # Delete removed chapters (only those without manual customization)
    for old_idx in diff["removed"]:
        c = old_script_chapters[old_idx]
        if c.source == "manual":
            logger.info(
                f"Chapter '{c.name}' removed from script but kept "
                f"(manual customization)"
            )
            continue
        logger.info(f"Deleting orphaned chapter '{c.name}' ({c.short_code})")
        await session.delete(c)

    # Delete all auto-generated chapters (they'll be recreated by auto-split
    # after header chapters are placed)
    for c in existing_chapters:
        if c.source == "auto":
            await session.delete(c)
    await session.flush()

    # Create / update chapter rows from headers
    result_chapters: List[Chapter] = []
    for i, h in enumerate(parsed.headers):
        start_t = times[i]
        # End time = start of NEXT header at SAME OR HIGHER depth, else audio end
        end_t = audio_end
        for j in range(i + 1, len(parsed.headers)):
            if parsed.headers[j].depth <= h.depth:
                end_t = times[j]
                break
        # Clamp end so it never precedes start (header past audio fix).
        end_t = max(end_t, start_t)
        end_t = min(end_t, audio_end if audio_end > 0 else end_t)

        # Find parent — first non-None entry in parent_stack at a depth shallower than h.depth
        parent: Optional[Chapter] = None
        for d in range(h.depth - 1, -1, -1):
            if parent_stack[d] is not None:
                parent = parent_stack[d]
                break

        if i in matched_old_by_new:
            # Update existing
            ch = matched_old_by_new[i]
            ch.depth = h.depth
            ch.start_time = start_t
            ch.end_time = end_t
            ch.script_offset_start = h.char_offset
            ch.parent_chapter_id = parent.id if parent else None
            ch.order_index = order_counter[h.depth]
            ch.updated_at = datetime.utcnow()
            session.add(ch)
        else:
            # Create new
            sc = await allocate_shortcode(session, project_id, "ch")
            ch = Chapter(
                id=uuid4(),
                project_id=project_id,
                parent_chapter_id=parent.id if parent else None,
                order_index=order_counter[h.depth],
                depth=h.depth,
                name=h.name,
                short_code=sc,
                color=_pick_color(order_counter[h.depth] + sum(order_counter[:h.depth])),
                auto_generated=False,
                source="script_header",
                script_offset_start=h.char_offset,
                script_offset_end=h.char_offset + len(h.name),
                start_time=start_t,
                end_time=end_t,
                tags=[],
                chapter_metadata={},
            )
            session.add(ch)

        order_counter[h.depth] += 1
        parent_stack[h.depth] = ch
        # Reset deeper depths since this opens a new parent at h.depth
        for d in range(h.depth + 1, settings_.max_depth + 1):
            parent_stack[d] = None
            order_counter[d] = 0
        result_chapters.append(ch)

    await session.flush()
    return result_chapters


# ── Auto-chapter when no headers ──────────────────────────────────────


async def _create_auto_chapters(
    session: AsyncSession,
    project_id: UUID,
    scenes: List[Scene],
    existing_chapters: List[Chapter],
    settings_: RebuildSettings,
) -> List[Chapter]:
    """Build chapters by splitting scenes into batches of N.

    Used when the script has no ``# headers``.  Replaces all
    auto-generated chapters in the project; preserves manual chapters
    untouched if any exist (rare case — user manually created a chapter
    then deleted all script headers).
    """
    if not scenes:
        return []

    # Wipe auto-generated chapters
    for c in existing_chapters:
        if c.source == "auto":
            await session.delete(c)
    await session.flush()

    # Group scenes into batches
    limit = settings_.auto_split_threshold
    n = len(scenes)
    num_chapters = max(1, (n + limit - 1) // limit)
    target_size = n / num_chapters
    chapters: List[Chapter] = []
    start_idx = 0
    for i in range(num_chapters):
        end_idx = round((i + 1) * target_size) if i < num_chapters - 1 else n
        if end_idx <= start_idx:
            end_idx = start_idx + 1
        chunk = scenes[start_idx:end_idx]
        if not chunk:
            continue
        sc = await allocate_shortcode(session, project_id, "ch")
        ch = Chapter(
            id=uuid4(),
            project_id=project_id,
            parent_chapter_id=None,
            order_index=i,
            depth=0,
            name=f"Chapter {i + 1}",
            short_code=sc,
            color=_pick_color(i),
            auto_generated=True,
            source="auto",
            script_offset_start=None,
            script_offset_end=None,
            start_time=float(chunk[0].start_time),
            end_time=float(chunk[-1].end_time),
            tags=[],
            chapter_metadata={},
        )
        session.add(ch)
        chapters.append(ch)
        start_idx = end_idx
    await session.flush()
    logger.info(f"Auto-chaptered project {project_id}: {n} scenes → {len(chapters)} chapters")
    return chapters


# ── Top-level orchestration ───────────────────────────────────────────


async def rebuild_chapters(
    session: AsyncSession,
    project_id: UUID,
    *,
    force_auto: bool = False,
) -> List[Chapter]:
    """Re-derive the project's chapter tree from current state.

    Idempotent — calling repeatedly produces the same result given the
    same inputs.

    Args:
        session: Async DB session.
        project_id: Project UUID.
        force_auto: Ignore script headers and force auto-chaptering by
            scene count.  Useful for the "Reset chapters" action.

    Returns:
        The fresh list of Chapter rows for this project.
    """
    logger.info(f"Rebuilding chapters for project {project_id} (force_auto={force_auto})")

    settings_ = await _load_settings(session)

    # Load project, lyrics, scenes, existing chapters
    result = await session.execute(select(Project).where(Project.id == project_id))
    project = result.scalars().first()
    if not project:
        raise ValueError(f"Project {project_id} not found")

    lyrics_result = await session.execute(
        select(Lyrics).where(Lyrics.project_id == project_id)
    )
    lyrics = lyrics_result.scalars().first()

    scene_result = await session.execute(
        select(Scene).where(Scene.project_id == project_id).order_by(Scene.order_index)
    )
    scenes = list(scene_result.scalars().all())

    chapter_result = await session.execute(
        select(Chapter).where(Chapter.project_id == project_id)
    )
    existing_chapters = list(chapter_result.scalars().all())

    # Decide path: headers or auto
    parsed: Optional[ParsedScript] = None
    use_headers = False
    if not force_auto and lyrics and lyrics.initial_text:
        parsed = parse_script_headers(
            lyrics.initial_text, max_depth=settings_.max_depth
        )
        use_headers = bool(parsed.headers)

    if use_headers and parsed is not None:
        chapters = await _create_chapters_from_headers(
            session, project_id, parsed,
            (lyrics.words if lyrics else []) or [],
            scenes, existing_chapters, settings_,
        )
    else:
        # When force_auto is set we wipe ALL prior chapters (including
        # ones originally derived from script headers) so the auto
        # builder gets a clean slate.  Raw SQL bypasses any ORM cascade
        # quirks that would re-hydrate deleted rows.
        if force_auto:
            # Hard reset: unbind every scene from its chapter, drop the
            # chapter rows via raw SQL, expire the session cache, then
            # re-fetch scenes so subsequent ORM writes see the clean state.
            # session.delete() + session.commit() proved insufficient on
            # SQLite (the cascade_delete relationship on Project.chapters
            # re-attached cached rows on the next flush).
            for c in list(existing_chapters):
                try:
                    session.expunge(c)
                except Exception:
                    pass
            # SQLite (via SQLModel) stores UUIDs without dashes — pass
            # the hex form so the WHERE clause actually matches.
            _pid_hex = project_id.hex
            await session.execute(
                text("UPDATE scenes SET chapter_id = NULL WHERE project_id = :pid"),
                {"pid": _pid_hex},
            )
            del_result = await session.execute(
                text("DELETE FROM chapters WHERE project_id = :pid"),
                {"pid": _pid_hex},
            )
            logger.info(
                f"force_auto reset: deleted {del_result.rowcount} chapter "
                f"row(s) for project {project_id}"
            )
            await session.commit()
            session.expire_all()
            existing_chapters = []
            # Re-fetch scenes since expire_all wiped our local cache
            sc_re = await session.execute(
                select(Scene).where(Scene.project_id == project_id).order_by(Scene.order_index)
            )
            scenes = list(sc_re.scalars().all())
        chapters = await _create_auto_chapters(
            session, project_id, scenes, existing_chapters, settings_,
        )

    await session.flush()

    # Bind each scene to its leaf chapter by time
    await bind_scenes_to_chapters_by_time(session, project_id)

    # Now auto-split any oversized leaves
    await _apply_auto_split(session, project_id, settings_)

    # Re-bind after splits
    await bind_scenes_to_chapters_by_time(session, project_id)

    # Refresh and return
    fresh = await session.execute(
        select(Chapter).where(Chapter.project_id == project_id).order_by(Chapter.depth, Chapter.order_index)
    )
    final = list(fresh.scalars().all())
    logger.info(
        f"Rebuild complete: project={project_id} {len(final)} chapters "
        f"(used_headers={use_headers})"
    )
    return final


async def _apply_auto_split(
    session: AsyncSession,
    project_id: UUID,
    settings_: RebuildSettings,
) -> None:
    """Walk leaf chapters; split any whose scene count exceeds the threshold."""
    chapter_result = await session.execute(
        select(Chapter).where(Chapter.project_id == project_id).order_by(Chapter.depth, Chapter.order_index)
    )
    all_chapters = list(chapter_result.scalars().all())
    # Leaf = no other chapter references it as parent
    parent_ids = {c.parent_chapter_id for c in all_chapters if c.parent_chapter_id is not None}
    leaves = [c for c in all_chapters if c.id not in parent_ids]

    for leaf in leaves:
        scenes_result = await session.execute(
            select(Scene).where(Scene.chapter_id == leaf.id).order_by(Scene.order_index)
        )
        scene_rows = list(scenes_result.scalars().all())
        if len(scene_rows) <= settings_.auto_split_threshold:
            continue
        if leaf.depth >= settings_.max_depth:
            continue  # already at max depth

        scene_dicts = [
            {"id": str(s.id), "start_time": s.start_time, "end_time": s.end_time}
            for s in scene_rows
        ]
        plans = auto_split_oversized_chapters(
            chapter_name=leaf.name,
            scene_list=scene_dicts,
            limit=settings_.auto_split_threshold,
            max_depth=settings_.max_depth,
            current_depth=leaf.depth,
        )
        for idx, plan in enumerate(plans):
            sc = await allocate_shortcode(session, project_id, "ch")
            sub = Chapter(
                id=uuid4(),
                project_id=project_id,
                parent_chapter_id=leaf.id,
                order_index=idx,
                depth=leaf.depth + 1,
                name=plan.name,
                short_code=sc,
                color=leaf.color,
                auto_generated=True,
                source="auto",
                start_time=plan.start_time,
                end_time=plan.end_time,
                tags=[],
                chapter_metadata={},
            )
            session.add(sub)
            await session.flush()
            # Re-assign the scenes in this part to the sub-chapter
            await session.execute(
                text("UPDATE scenes SET chapter_id = :cid WHERE id IN ({})".format(
                    ", ".join(f":id{i}" for i in range(len(plan.scene_ids)))
                )),
                {"cid": str(sub.id), **{f"id{i}": sid for i, sid in enumerate(plan.scene_ids)}},
            )
        logger.info(
            f"Auto-split leaf '{leaf.name}' into {len(plans)} parts"
        )
    await session.commit()


# ── Color palette ─────────────────────────────────────────────────────


_COLOR_PALETTE = [
    "#7c3aed",  # purple
    "#2563eb",  # blue
    "#10b981",  # green
    "#f59e0b",  # amber
    "#ec4899",  # pink
    "#06b6d4",  # cyan
    "#ef4444",  # red
    "#8b5cf6",  # violet
]


def _pick_color(index: int) -> str:
    return _COLOR_PALETTE[index % len(_COLOR_PALETTE)]


# ── Serialize tree for API ────────────────────────────────────────────


def build_chapter_tree_response(
    chapters: List[Chapter],
    scene_id_map: Dict[UUID, List[UUID]],
) -> List[ChapterTreeNode]:
    """Convert a flat chapter list + scene map into a serializable tree."""
    by_id: Dict[UUID, ChapterTreeNode] = {}
    for c in chapters:
        sids = scene_id_map.get(c.id, [])
        node = ChapterTreeNode(
            id=str(c.id),
            project_id=str(c.project_id),
            parent_chapter_id=str(c.parent_chapter_id) if c.parent_chapter_id else None,
            order_index=c.order_index,
            depth=c.depth,
            name=c.name,
            short_code=c.short_code,
            color=c.color,
            auto_generated=c.auto_generated,
            source=c.source,
            start_time=c.start_time,
            end_time=c.end_time,
            tags=list(c.tags or []),
            scene_count=len(sids),
            scene_ids=[str(s) for s in sids],
        )
        by_id[c.id] = node

    roots: List[ChapterTreeNode] = []
    for c in chapters:
        node = by_id[c.id]
        if c.parent_chapter_id and c.parent_chapter_id in by_id:
            by_id[c.parent_chapter_id].children.append(node)
        else:
            roots.append(node)

    def _sort_node(n: ChapterTreeNode) -> None:
        n.children.sort(key=lambda x: (x.depth, x.order_index))
        for child in n.children:
            _sort_node(child)

    roots.sort(key=lambda x: x.order_index)
    for r in roots:
        _sort_node(r)
    return roots
