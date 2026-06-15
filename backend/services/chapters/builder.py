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
    # Chapter-level creative direction (Phase 2)
    description: str
    character_focus: List[str]
    style_notes: str
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
    # after header chapters are placed).  See _create_auto_chapters for
    # the same FK-safe ordering: scenes first, then chapters depth-DESC.
    _pid_hex = project_id.hex
    for c in list(existing_chapters):
        # Treat NULL/'' source the same as 'auto' — matches the widened
        # SQL DELETE below so legacy rows expunge in lock-step with the
        # delete and can't be re-flushed from the ORM cache.
        if c.source in ("auto", "", None):
            try:
                session.expunge(c)
            except Exception:
                pass
    await session.execute(
        text("UPDATE scenes SET chapter_id = NULL WHERE project_id = :pid"),
        {"pid": _pid_hex},
    )
    for d in range(settings_.max_depth, -1, -1):
        await session.execute(
            text(
                # Catch legacy NULL/'' source rows too — they predate the
                # 1.8.0 ``source`` column default and would otherwise
                # survive cleanup and pile up under newly-built chapters.
                "DELETE FROM chapters "
                "WHERE project_id = :pid "
                "AND (source = 'auto' OR source IS NULL OR source = '') "
                "AND depth = :d"
            ),
            {"pid": _pid_hex, "d": d},
        )
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

    # ── Wipe auto-generated chapters ─────────────────────────────────
    # Order is critical for SQLite FK enforcement:
    #   1. NULL out scenes.chapter_id (scenes reference chapters)
    #   2. DELETE depth-DESC (sub-chapters reference parents)
    # Otherwise either step fails: a chapter can't be deleted while
    # scenes point at it, and parents can't be deleted while subs do.
    project_id = scenes[0].project_id
    _pid_hex = project_id.hex
    # Expunge cached ORM objects so we don't re-flush stale copies.
    for c in list(existing_chapters):
        # Treat NULL/'' source the same as 'auto' — matches the widened
        # SQL DELETE below so legacy rows expunge in lock-step with the
        # delete and can't be re-flushed from the ORM cache.
        if c.source in ("auto", "", None):
            try:
                session.expunge(c)
            except Exception:
                pass
    # Step 1: unbind scenes from any chapter (we'll re-bind after the new
    # chapter tree is built).
    await session.execute(
        text("UPDATE scenes SET chapter_id = NULL WHERE project_id = :pid"),
        {"pid": _pid_hex},
    )
    # Step 2: delete auto chapters deepest-first so sub→parent FK holds.
    for d in range(settings_.max_depth, -1, -1):
        await session.execute(
            text(
                # Catch legacy NULL/'' source rows too — they predate the
                # 1.8.0 ``source`` column default and would otherwise
                # survive cleanup and pile up under newly-built chapters.
                "DELETE FROM chapters "
                "WHERE project_id = :pid "
                "AND (source = 'auto' OR source IS NULL OR source = '') "
                "AND depth = :d"
            ),
            {"pid": _pid_hex, "d": d},
        )
    await session.flush()

    # ── Respect existing MANUAL chapters ─────────────────────────────
    # If the user has manual chapters (source='manual'), do NOT create
    # auto chapters on top — that produces colliding "Chapter N" names
    # at different time ranges and the user sees "doubled" chapters in
    # the UI.  Diagnosed in 1.8.15: a project had 4 manual rows covering
    # 0..763.6s + 3 auto rows covering 194..809.8s with the same names.
    #
    # Instead: extend the last manual chapter's end_time to the project
    # audio end so tail scenes (past the manual coverage) still bind to
    # a real chapter.  Then return early — the rebuild orchestrator's
    # bind_scenes_to_chapters_by_time call will assign every scene to
    # one of the manual chapters.
    manual_chapters = [c for c in existing_chapters if c.source == "manual"]
    if manual_chapters:
        manual_chapters.sort(key=lambda c: (c.depth, c.order_index, float(c.start_time or 0.0)))
        last = max(manual_chapters, key=lambda c: float(c.end_time or 0.0))
        audio_end = _project_audio_end_time(scenes)
        if audio_end > float(last.end_time or 0.0):
            old_end = float(last.end_time or 0.0)
            last.end_time = audio_end
            session.add(last)
            await session.flush()
            logger.info(
                f"_create_auto_chapters: extended last manual chapter "
                f"'{last.name}' end_time from {old_end:.1f}s to {audio_end:.1f}s "
                f"so tail scenes bind to it instead of triggering "
                f"auto-chapter creation."
            )
        logger.info(
            f"_create_auto_chapters: respecting {len(manual_chapters)} "
            f"existing manual chapter(s); skipping auto-chapter creation."
        )
        return list(manual_chapters)

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


# Per-project rebuild lock.  Without this, two concurrent rebuild_chapters
# calls (e.g. SuggestTimeline firing twice in quick succession from the
# frontend, or rebuild_chapters being called from an SRT-upload side-effect
# at the same time as from analyze_audio onSuccess) can both pass the
# nuclear pre-clean — each sees an empty chapters table after the OTHER's
# DELETE commits — and then each insert N rows, producing 2N total.
# The asyncio.Lock serializes rebuilds per-project so the second caller
# waits for the first to fully commit before starting its own pre-clean.
import asyncio as _asyncio
_REBUILD_LOCKS: dict[str, _asyncio.Lock] = {}


def _get_rebuild_lock(project_id: UUID) -> _asyncio.Lock:
    """Return (creating on demand) the per-project rebuild lock."""
    key = project_id.hex if hasattr(project_id, "hex") else str(project_id)
    lk = _REBUILD_LOCKS.get(key)
    if lk is None:
        lk = _asyncio.Lock()
        _REBUILD_LOCKS[key] = lk
    return lk


async def rebuild_chapters(
    session: AsyncSession,
    project_id: UUID,
    *,
    force_auto: bool = False,
) -> List[Chapter]:
    """Re-derive the project's chapter tree from current state.

    Idempotent — calling repeatedly produces the same result given the
    same inputs.  Per-project serialised via ``_get_rebuild_lock`` so
    concurrent callers can't both pass the pre-clean and double-insert.

    Args:
        session: Async DB session.
        project_id: Project UUID.
        force_auto: Ignore script headers and force auto-chaptering by
            scene count.  Useful for the "Reset chapters" action.

    Returns:
        The fresh list of Chapter rows for this project.
    """
    _lock = _get_rebuild_lock(project_id)
    if _lock.locked():
        logger.warning(
            f"rebuild_chapters({project_id}): another rebuild is in "
            f"flight — WAITING for it to finish before starting.  "
            f"Concurrent rebuilds are the 1.8.x chapter-doubling root cause."
        )
    async with _lock:
        return await _rebuild_chapters_locked(
            session, project_id, force_auto=force_auto
        )


async def _rebuild_chapters_locked(
    session: AsyncSession,
    project_id: UUID,
    *,
    force_auto: bool = False,
) -> List[Chapter]:
    """The actual rebuild body, run under the per-project lock."""
    logger.info(f"Rebuilding chapters for project {project_id} (force_auto={force_auto})")

    settings_ = await _load_settings(session)

    # ── Nuclear pre-clean ───────────────────────────────────────────────
    # Earlier rebuild paths tried to be clever — `_create_auto_chapters`
    # filtered DELETE by ``source = 'auto'`` and
    # `_create_chapters_from_headers` ran a header diff to "carry forward"
    # matched chapters.  Both produced doubled chapters across multiple
    # 1.8.x sessions when ANY of these conditions held:
    #
    #   * Legacy rows with NULL / empty source (pre-1.8.0 chapter table)
    #   * SQLAlchemy ORM cache re-flushing rows the SQL DELETE just
    #     wiped (expunge timing)
    #   * Header diff matching against stale script_header chapters
    #     whose names had subtly drifted (case, whitespace, encoding)
    #
    # The robust fix is to STOP being clever: at the top of every
    # rebuild, unconditionally delete every non-manual chapter row for
    # the project via raw SQL deepest-first, expire the ORM cache, and
    # only THEN run the build.  Manual chapters (``source = 'manual'``)
    # are preserved because those represent explicit user intent
    # (created via POST /chapters/:id, not auto-built).  Everything
    # else — auto, script_header, NULL, empty — gets wiped.
    #
    # We sacrifice the "preserve color customization across rebuilds"
    # micro-feature for the much bigger "rebuilds are actually
    # idempotent" guarantee.  Color customization can come back later
    # via a "remember color by name" pass if it ever matters.
    _pid_hex_pre = project_id.hex

    # Step 1: unbind scenes from any chapter so the chapter DELETE can't
    # FK-fail on referenced rows.
    await session.execute(
        text("UPDATE scenes SET chapter_id = NULL WHERE project_id = :pid"),
        {"pid": _pid_hex_pre},
    )

    # Step 2: delete every non-manual chapter, deepest-first, so
    # sub-chapters go before parents and the parent_chapter_id FK never
    # references a dead row.  Use a high explicit depth ceiling rather
    # than the settings_.max_depth (which can be reduced between
    # sessions and leave deeper rows behind).
    _deleted_total = 0
    for d in range(10, -1, -1):
        del_res = await session.execute(
            text(
                "DELETE FROM chapters "
                "WHERE project_id = :pid "
                "AND (source IS NULL OR source != 'manual') "
                "AND depth = :d"
            ),
            {"pid": _pid_hex_pre, "d": d},
        )
        _deleted_total += del_res.rowcount or 0

    # Step 3: commit + expire all ORM cache so any cached chapter rows
    # from THIS session can't be re-flushed on the next session.add().
    # session.expire_all is essential — without it, SQLAlchemy can
    # silently re-INSERT rows whose primary key still lives in its
    # identity map even though the underlying SQL DELETE removed them.
    await session.commit()
    session.expire_all()

    if _deleted_total > 0:
        logger.info(
            f"rebuild_chapters({project_id}): pre-cleaned {_deleted_total} "
            f"existing non-manual chapter row(s).  Build will create a "
            f"fresh tree from current scenes/script."
        )

    # Step 4: VERIFY the pre-clean actually landed in the DB.  On
    # SQLite-async with ``expire_on_commit=False`` + a busy connection,
    # the DELETE can report ``rowcount=N`` but the next SELECT in the
    # same session still sees the rows because the autobegin transaction
    # wraps both statements.  If anything survived, drop down to the raw
    # connection (bypasses the ORM session snapshot entirely) and force
    # the DELETE through.  Logs LOUD so this self-diagnoses on the next
    # diag.md if it ever happens.  This is the safety net the prior
    # match-and-update + nuclear-pre-clean attempts both lacked.
    _check = await session.execute(
        text(
            "SELECT COUNT(*) FROM chapters "
            "WHERE project_id = :pid "
            "AND (source IS NULL OR source != 'manual')"
        ),
        {"pid": _pid_hex_pre},
    )
    _surv = int(_check.scalar() or 0)
    if _surv > 0:
        logger.error(
            f"rebuild_chapters({project_id}): pre-clean DID NOT PERSIST — "
            f"{_surv} non-manual chapter row(s) survived the commit.  This "
            f"is the doubled-chapter bug.  Forcing a raw-connection DELETE "
            f"to bypass any session-level snapshot."
        )
        # Fall through to raw connection, bypassing the ORM session.
        try:
            _raw_conn = await session.connection()
            await _raw_conn.exec_driver_sql(
                "DELETE FROM chapters WHERE project_id = ? "
                "AND (source IS NULL OR source != 'manual')",
                (_pid_hex_pre,),
            )
            await session.commit()
            session.expire_all()
            # Re-check.  If STILL non-zero, something fundamental is
            # broken with the project's FK constraints — surface that
            # rather than silently shipping doubled chapters.
            _check2 = await session.execute(
                text(
                    "SELECT COUNT(*) FROM chapters "
                    "WHERE project_id = :pid "
                    "AND (source IS NULL OR source != 'manual')"
                ),
                {"pid": _pid_hex_pre},
            )
            _surv2 = int(_check2.scalar() or 0)
            if _surv2 > 0:
                logger.critical(
                    f"rebuild_chapters({project_id}): even the raw-connection "
                    f"DELETE failed to remove {_surv2} chapter(s).  Aborting "
                    f"rebuild — the build phase would produce doubled rows.  "
                    f"Check FK constraints on the chapters table and report."
                )
                raise RuntimeError(
                    f"Cannot pre-clean chapters for project {project_id}: "
                    f"{_surv2} row(s) refuse to delete.  See ERROR log."
                )
            logger.warning(
                f"rebuild_chapters({project_id}): raw-connection DELETE "
                f"succeeded — pre-clean is now correct.  Investigate why "
                f"the ORM-level DELETE failed to persist (likely an outer "
                f"transaction snapshot on this request session)."
            )
        except RuntimeError:
            raise
        except Exception as _raw_err:
            logger.critical(
                f"rebuild_chapters({project_id}): raw-connection DELETE "
                f"itself raised {_raw_err!r}.  Aborting rebuild to avoid "
                f"doubled chapters."
            )
            raise RuntimeError(
                f"Pre-clean fallback failed: {_raw_err}"
            ) from _raw_err

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

    # ── Post-build sanity check: duplicate detection ───────────────────
    # The "doubled chapters" bug was hard to catch because the rebuild
    # path technically ran to completion — it just left twice as many
    # rows in the DB.  This block surfaces it: if any (name, depth,
    # parent_chapter_id) triple appears more than once, that's a
    # duplicate.  We log it loud (ERROR) so the next "diag.md" snapshot
    # makes the regression obvious; we don't auto-remediate here because
    # silently deleting rows the user might have customized is worse
    # than the duplicates themselves.
    # ── True-duplicate detection ────────────────────────────────────────
    # A "true" duplicate is a chapter that shares not just name + depth +
    # parent with another row, but ALSO occupies the same time range.
    # Two legitimately distinct chapters with the same name (e.g. two
    # scenes both called "Verse" at different parts of a song) MUST NOT
    # be collapsed — they're real, distinct story beats.  The dedup key
    # therefore includes a coarse start-time bucket (rounded to 0.1s) so
    # only rows that genuinely refer to the same beat get collapsed.
    _dup_keys: dict[tuple, list] = {}
    for c in final:
        _t_bucket = round(float(c.start_time or 0.0), 1)
        k = (
            c.name or "",
            c.depth,
            str(c.parent_chapter_id) if c.parent_chapter_id else None,
            _t_bucket,
        )
        _dup_keys.setdefault(k, []).append(c)
    _dups = [(k, rows) for k, rows in _dup_keys.items() if len(rows) > 1]
    if _dups:
        logger.error(
            f"rebuild_chapters({project_id}): DUPLICATE CHAPTERS DETECTED — "
            f"{len(_dups)} (name, depth, parent, start_time) tuple(s) appear "
            f"more than once.  Total chapters: {len(final)}.  Auto-dedup now."
        )
        # ── Auto-dedup: keep the OLDEST row per dedup key and raw-DELETE
        # the rest.  Use raw connection so the ORM session snapshot can't
        # resurrect the deleted rows on next commit.  Critically: after
        # the DELETE we MUST re-run bind_scenes_to_chapters_by_time so
        # the scenes that were pointing at the now-dead rows (we null'd
        # their chapter_id below to avoid an FK violation) get rebound
        # to the survivor row.  Without that rebind, every dedup leaves
        # orphan scenes — which is exactly the "4 scenes not in
        # chapter 4" symptom the user just reported.
        _ids_to_drop: list[str] = []
        _surviving_ids: list = []
        for _key, _rows in _dups:
            _rows.sort(key=lambda r: (r.created_at or datetime.utcnow(), str(r.id)))
            _surviving_ids.append(_rows[0].id)
            for _extra in _rows[1:]:
                _ids_to_drop.append(_extra.id.hex if hasattr(_extra.id, "hex") else str(_extra.id).replace("-", ""))
        if _ids_to_drop:
            try:
                # Unbind any scenes still pointing at the doomed rows
                _placeholders = ",".join("?" * len(_ids_to_drop))
                _raw = await session.connection()
                await _raw.exec_driver_sql(
                    f"UPDATE scenes SET chapter_id = NULL WHERE chapter_id IN ({_placeholders})",
                    tuple(_ids_to_drop),
                )
                # Re-parent any chapters whose parent is about to die — point
                # them at NULL so the cascade doesn't take them with it.
                await _raw.exec_driver_sql(
                    f"UPDATE chapters SET parent_chapter_id = NULL WHERE parent_chapter_id IN ({_placeholders})",
                    tuple(_ids_to_drop),
                )
                await _raw.exec_driver_sql(
                    f"DELETE FROM chapters WHERE id IN ({_placeholders})",
                    tuple(_ids_to_drop),
                )
                await session.commit()
                session.expire_all()
                logger.info(
                    f"rebuild_chapters({project_id}): auto-dedup dropped "
                    f"{len(_ids_to_drop)} duplicate chapter row(s).  "
                    f"Re-binding orphan scenes to survivor rows..."
                )
                # CRITICAL: rebind any scenes that just had their chapter_id
                # nulled.  Without this the dedup leaves orphan scenes
                # which the user sees as "N scenes not in chapter X".
                await bind_scenes_to_chapters_by_time(session, project_id)
                await session.commit()
                # Refresh final list
                fresh2 = await session.execute(
                    select(Chapter).where(Chapter.project_id == project_id).order_by(Chapter.depth, Chapter.order_index)
                )
                final = list(fresh2.scalars().all())
                # Sanity-check the rebind actually rebound everything
                _orphan_count_q = await session.execute(
                    text(
                        "SELECT COUNT(*) FROM scenes "
                        "WHERE project_id = :pid AND chapter_id IS NULL"
                    ),
                    {"pid": _pid_hex_pre},
                )
                _orphan_count = int(_orphan_count_q.scalar() or 0)
                if _orphan_count > 0:
                    logger.warning(
                        f"rebuild_chapters({project_id}): {_orphan_count} "
                        f"scene(s) still unbound after dedup rebind.  These "
                        f"likely fall outside any chapter's time range — "
                        f"check chapter start/end_time vs scene boundaries."
                    )
            except Exception as _dedup_err:
                logger.critical(
                    f"rebuild_chapters({project_id}): auto-dedup FAILED — "
                    f"{_dedup_err!r}.  Returning doubled chapter list."
                )

    logger.info(
        f"Rebuild complete: project={project_id} {len(final)} chapters "
        f"(used_headers={use_headers}, duplicates_removed={len(_dups)})"
    )
    return final


async def deduplicate_project_chapters(
    session: AsyncSession, project_id: UUID
) -> int:
    """Standalone dedup pass for a single project's chapters table.

    Used by:
      * The startup migration (sweeps every project once per app boot).
      * The `/chapters/reparse` endpoint as a last-resort manual recovery
        when the user reports doubled chapters and wants them cleaned
        without rerunning the full rebuild.

    Returns the number of rows deleted.  Preserves the oldest row in each
    (name, depth, parent_chapter_id) cluster — the row most likely to
    have the user's accumulated edits.
    """
    _pid_hex = project_id.hex
    # Find duplicate groups.  GROUP BY can be tricky with NULL parent_id
    # on SQLite so we fetch and group in Python.  Include start_time in
    # the dedup key (rounded to 0.1s) so two distinct chapters that happen
    # to share a name but live at different points in the timeline do NOT
    # get collapsed into one — they're real, separate story beats.
    rows = await session.execute(
        text(
            "SELECT id, name, depth, parent_chapter_id, created_at, start_time "
            "FROM chapters WHERE project_id = :pid"
        ),
        {"pid": _pid_hex},
    )
    by_key: dict[tuple, list] = {}
    for r in rows.all():
        _t_bucket = round(float(r[5] or 0.0), 1)
        key = (r[1] or "", int(r[2] or 0), r[3], _t_bucket)
        by_key.setdefault(key, []).append((r[0], r[4]))
    drops: list[str] = []
    for _key, _rows in by_key.items():
        if len(_rows) <= 1:
            continue
        _rows.sort(key=lambda t: (t[1] or "", t[0]))
        for _extra_id, _ in _rows[1:]:
            drops.append(_extra_id)
    if not drops:
        return 0
    placeholders = ",".join("?" * len(drops))
    raw = await session.connection()
    await raw.exec_driver_sql(
        f"UPDATE scenes SET chapter_id = NULL WHERE chapter_id IN ({placeholders})",
        tuple(drops),
    )
    await raw.exec_driver_sql(
        f"UPDATE chapters SET parent_chapter_id = NULL WHERE parent_chapter_id IN ({placeholders})",
        tuple(drops),
    )
    await raw.exec_driver_sql(
        f"DELETE FROM chapters WHERE id IN ({placeholders})",
        tuple(drops),
    )
    await session.commit()
    # Rebind any orphan scenes to the survivor rows.  Without this the
    # standalone dedup (used by the startup migration) would leave the
    # caller's scenes unbound just because they happened to be pointing
    # at the now-dead duplicate row.  bind_scenes_to_chapters_by_time
    # walks every leaf chapter and assigns scenes by time overlap, so
    # the surviving row inherits the orphans automatically.
    try:
        await bind_scenes_to_chapters_by_time(session, project_id)
        await session.commit()
    except Exception as _rebind_err:
        logger.error(
            f"deduplicate_project_chapters({project_id}): rebind after "
            f"dedup FAILED — {_rebind_err!r}.  Scenes left orphaned; "
            f"user can re-run Suggest Timeline to recover."
        )
    return len(drops)


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
            description=getattr(c, "description", "") or "",
            character_focus=list(getattr(c, "character_focus", []) or []),
            style_notes=getattr(c, "style_notes", "") or "",
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
