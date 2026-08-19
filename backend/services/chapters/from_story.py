"""🎬 Chapters from a linked story's ARCS (v1.277.24).

The third chapter producer, beside `# script headers` and the scene-count
auto-split. Its input is not the audio and not the script — it is the STORY the
project is linked to, whose arcs are the authored spine.

**How arcs get their time ranges — his call (2026-08-16): MATCH THE DETECTED
SECTIONS.** The audio analysis already found where the recording changes; arcs
are laid onto those boundaries in order. That beats inventing durations from a
weight the LLM guessed, because a chapter boundary that does not coincide with
a real pause cuts a sentence in half, and a narration take is exactly where the
real pauses are.

    5 arcs, 5 sections  →  one arc per section, boundaries kept
    3 arcs, 7 sections  →  sections are DISTRIBUTED (2,2,3), arcs keep order
    7 arcs, 3 sections  →  the extra arcs split the last sections evenly, on
                           scene boundaries so nothing cuts mid-scene
    no sections at all  →  fall back to an even split across the audio, snapped
                           to scene starts

⚠ These rows carry `source="story"`, which `_rebuild_chapters_locked` PRESERVES
alongside `manual` — otherwise the next `suggest-timeline` (which fires on
every audio re-analysis) would delete the story's structure without a word.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from backend.database.models import Chapter, Scene, SongSection
from backend.services.shortcode import allocate_shortcode

logger = logging.getLogger(__name__)

_COLORS = ["#3b82f6", "#8b5cf6", "#ec4899", "#f59e0b", "#10b981", "#06b6d4",
           "#ef4444", "#84cc16"]


def _spread(n_arcs: int, bounds: List[float]) -> List[tuple]:
    """Distribute section boundaries over arcs, in order.

    `bounds` is [start0, start1, …, end] — len(bounds) - 1 sections."""
    n_sec = max(1, len(bounds) - 1)
    out: List[tuple] = []
    if n_arcs <= n_sec:
        # group sections per arc, as evenly as possible, order preserved
        per = n_sec // n_arcs
        extra = n_sec % n_arcs
        i = 0
        for a in range(n_arcs):
            take = per + (1 if a < extra else 0)
            out.append((bounds[i], bounds[min(i + take, n_sec)]))
            i += take
        return out
    # more arcs than sections: subdivide each section evenly among the arcs
    # that fall into it — the arcs keep their order, no boundary moves
    per = n_arcs // n_sec
    extra = n_arcs % n_sec
    for sidx in range(n_sec):
        take = per + (1 if sidx < extra else 0)
        s0, s1 = bounds[sidx], bounds[sidx + 1]
        step = (s1 - s0) / max(1, take)
        for k in range(take):
            out.append((s0 + k * step, s0 + (k + 1) * step))
    return out


def _snap(t: float, starts: List[float]) -> float:
    """Snap a boundary to the nearest SCENE start — a chapter that begins
    mid-scene binds that scene to the wrong side of the boundary."""
    if not starts:
        return t
    return min(starts, key=lambda s: abs(s - t))


async def create_chapters_from_arcs(
    session: AsyncSession,
    project_id: UUID,
    arcs: List[Dict[str, Any]],
    audio_duration: float = 0.0,
) -> List[Chapter]:
    """Build one chapter per arc, timed against the detected sections.

    Returns the created rows (already added to the session, not committed —
    the caller owns the transaction, as every other producer here does)."""
    if not arcs:
        return []

    scenes = (await session.execute(
        select(Scene).where(Scene.project_id == project_id)
        .order_by(Scene.start_time))).scalars().all()
    sections = (await session.execute(
        select(SongSection).where(SongSection.project_id == project_id)
        .order_by(SongSection.start_time))).scalars().all()

    scene_starts = [float(s.start_time or 0.0) for s in scenes]
    total = float(audio_duration or 0.0)
    if not total:
        total = max([float(s.end_time or 0.0) for s in scenes] or [0.0]) \
            or max([float(x.end_time or 0.0) for x in sections] or [0.0])

    if sections:
        bounds = [float(sections[0].start_time or 0.0)]
        for x in sections:
            bounds.append(float(x.end_time or 0.0))
        source_note = f"{len(sections)} detected section(s)"
    elif total:
        # no sections: an even split is the honest fallback, snapped to scenes
        n = len(arcs)
        bounds = [total * i / n for i in range(n + 1)]
        source_note = "even split (no sections detected)"
    else:
        bounds = []
        source_note = "no audio yet — chapters carry no times"

    spans = _spread(len(arcs), bounds) if bounds else [(0.0, 0.0)] * len(arcs)

    made: List[Chapter] = []
    for i, arc in enumerate(arcs):
        s0, s1 = spans[i]
        if bounds and scene_starts:
            s0 = _snap(s0, scene_starts) if i else min(scene_starts + [s0])
            if i + 1 < len(spans):
                s1 = _snap(s1, scene_starts)
        code = await allocate_shortcode(session, project_id, "ch")
        ch = Chapter(
            project_id=project_id,
            parent_chapter_id=None,
            order_index=i,
            depth=0,
            name=arc.get("title") or f"Arc {i + 1}",
            short_code=code,
            color=_COLORS[i % len(_COLORS)],
            auto_generated=True,
            # ⚠ 'story' is a PRESERVED source in _rebuild_chapters_locked
            source="story",
            start_time=float(s0) if bounds else None,
            end_time=float(s1) if bounds else None,
            # the arc IS the creative direction — this is what
            # /chapters/{id}/generate-description would have had to invent
            description=arc.get("summary") or "",
            character_focus=[c for c in (arc.get("characters") or []) if c],
            style_notes=arc.get("mood") or "",
            # ⚠⚠ `from_story` is the PROVENANCE flag, and it is what a re-pull
            # must delete on. `source` is MUTABLE — `backend/api/chapters.py`
            # flips a chapter to "manual" the moment you rename, split, merge
            # or re-describe it (lines 214/306/353/597) — so a re-pull that
            # deleted on `source='story'` skipped every chapter he had touched
            # and then built the full set again beside them. That is the 1.8.15
            # doubled-chapter signature, arrived at from a new direction.
            chapter_metadata={"arc_id": arc.get("id"), "arc_index": i,
                              "from_story": True,
                              "locations": arc.get("locations") or [],
                              "timed_from": source_note},
        )
        session.add(ch)
        made.append(ch)
    logger.info("chapters-from-arcs: %d chapter(s) for project %s (%s)",
                len(made), project_id, source_note)
    return made


def is_from_story(ch: Chapter) -> bool:
    """⭐⭐ THE PROVENANCE PREDICATE — "did the story/chapter pull build this?"

    ⚠⚠ **`source` IS MUTABLE. PROVENANCE IS NOT.** `backend/api/chapters.py`
    sets `source = "manual"` the moment a chapter is renamed, split, merged or
    re-described (lines 214 / 306 / 353 / 597). So *"is this a story chapter?"*
    cannot be asked as `source == "story"` — the answer flips the first time he
    edits one, and then:

      · the pull's DELETE skips it and builds a duplicate beside it
        (the 1.8.15 doubled-chapter signature, found in the v1.277.46 review)
      · `_rebuild_chapters_locked`'s short-circuit misses it, so the header/auto
        producers run anyway and grow a SECOND competing set
      · this function never re-times it, so his edited chapter keeps the times
        it was born with while its neighbours move

    `chapter_metadata["from_story"]` is written once by
    `create_chapters_from_arcs` and never changed. Ask THAT.

    ⚠ `source == "story"` is still accepted so chapters built before v1.277.46
    keep working — an untouched one carries it and nothing else."""
    if (ch.source or "") == "story":
        return True
    return bool((ch.chapter_metadata or {}).get("from_story"))


async def retime_story_chapters(session: AsyncSession, project_id: UUID) -> int:
    """Re-time EXISTING story chapters against the sections detected NOW.

    ⭐ Why this exists: a story is usually linked BEFORE the narration is
    recorded, so the chapters get built with no times at all. When the audio
    finally arrives, the arcs must find their place in it — without deleting
    and rebuilding, which would throw away any editing done since.

    Returns the number of chapters re-timed."""
    # ⚠ filtered in PYTHON on `is_from_story`, not in SQL on `source` — a
    # renamed chapter is still a story chapter (see the predicate's docstring).
    chapters = [c for c in (await session.execute(
        select(Chapter).where(Chapter.project_id == project_id)
        .order_by(Chapter.order_index))).scalars().all()
        if is_from_story(c)]
    if not chapters:
        return 0
    sections = (await session.execute(
        select(SongSection).where(SongSection.project_id == project_id)
        .order_by(SongSection.start_time))).scalars().all()
    scenes = (await session.execute(
        select(Scene).where(Scene.project_id == project_id)
        .order_by(Scene.start_time))).scalars().all()
    if not sections and not scenes:
        return 0
    scene_starts = [float(x.start_time or 0.0) for x in scenes]
    if sections:
        bounds = [float(sections[0].start_time or 0.0)] + \
                 [float(x.end_time or 0.0) for x in sections]
        note = f"{len(sections)} detected section(s)"
    else:
        total = max([float(x.end_time or 0.0) for x in scenes] or [0.0])
        n = len(chapters)
        bounds = [total * i / n for i in range(n + 1)]
        note = "even split (no sections detected)"
    spans = _spread(len(chapters), bounds)
    for i, ch in enumerate(chapters):
        s0, s1 = spans[i]
        if scene_starts:
            s0 = _snap(s0, scene_starts) if i else min(scene_starts + [s0])
            if i + 1 < len(spans):
                s1 = _snap(s1, scene_starts)
        ch.start_time = float(s0)
        ch.end_time = float(s1)
        meta = dict(ch.chapter_metadata or {})
        meta["timed_from"] = note
        ch.chapter_metadata = meta
        session.add(ch)
    logger.info("retimed %d story chapter(s) for %s (%s)",
                len(chapters), project_id, note)
    return len(chapters)
