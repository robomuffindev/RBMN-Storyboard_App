"""
Shortcode allocation and lookup.

Shortcodes are short, stable, human-readable identifiers for every asset
in the system.  Format:

    {project_prefix}-{type_code}-{seq}

    project_prefix : first 4 hex chars of project UUID
    type_code      : 2-3 letter code (img, vid, aud, ref, chr, sce, ch, ...)
    seq            : zero-padded sequence number, per (project, type)

Examples
--------
    a3f9-img-0047   — 47th image generated for project a3f9...
    a3f9-vid-0012   — 12th video
    a3f9-ch-01      — 1st chapter (chapters use 2-digit padding)
    a3f9-sce-005    — 5th scene (3-digit padding)

Properties
----------
- 4-char project prefix lets you grep across the whole disk
- Type code is human-readable
- Sequence is stable forever once assigned (never reused / never renumbered)
- Lexicographically sortable within type
- Fits filename limits; copy-pasteable; screenshot-friendly

Allocation
----------
The ``ShortcodeCounter`` table holds one row per (project_id, type_code)
recording the *next* sequence number.  Allocation reads + updates that row
inside a single transaction so concurrent writers can't collide.

Backfill
--------
``backfill_missing_shortcodes`` runs at app startup and assigns a code to
any row that doesn't have one yet.  Existing rows keep their UUIDs;
shortcodes are additive, not a replacement.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional, Tuple
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

logger = logging.getLogger(__name__)


# ── Type code registry ────────────────────────────────────────────────
# Lowercase, 2-3 chars.  When adding a type, also list it here so the
# shortcode endpoint can route ``/api/shortcode/{code}`` to the right
# entity.

TYPE_CODES: Dict[str, str] = {
    # Assets — AssetType enum value → shortcode
    "generated_image": "img",
    "generated_video": "vid",
    "narration":       "aud",
    "music":           "mus",
    "reference":       "ref",
    "character":       "chr",
    "clothing":        "clo",
    "item":            "itm",
    "place":           "plc",
    # Other entities
    "scene":           "sce",
    "chapter":         "ch",
    "backing_track":   "bt",
}

# Per-type sequence padding — chapters/backing tracks are rarer than
# images, so they get fewer digits.
_PAD: Dict[str, int] = {
    "ch":  2,
    "bt":  2,
    "sce": 3,
    "default": 4,
}


def _project_prefix(project_id: UUID) -> str:
    """Return the first 4 hex chars of a project UUID, lowercase."""
    return str(project_id).replace("-", "")[:4].lower()


def _pad_for(type_code: str) -> int:
    return _PAD.get(type_code, _PAD["default"])


def asset_type_to_code(asset_type: str) -> str:
    """Map an AssetType enum value (string) to its 3-letter code."""
    return TYPE_CODES.get(asset_type, "ast")


def build_shortcode(project_id: UUID, type_code: str, seq: int) -> str:
    """Format a shortcode from its components.  Caller already allocated seq."""
    return f"{_project_prefix(project_id)}-{type_code}-{seq:0{_pad_for(type_code)}d}"


# ── Allocation ────────────────────────────────────────────────────────


async def allocate_next_seq(
    session_or_conn: Any,
    project_id: UUID,
    type_code: str,
) -> int:
    """Atomically allocate the next sequence number for (project, type).

    Works with either an ``AsyncSession`` or an ``AsyncConnection``.  The
    counter row is created on first use.

    Returns the allocated sequence integer (>= 1).
    """
    # SQLModel stores UUID columns as hex without dashes on SQLite,
    # so we must pass the no-dash form to raw SQL or the WHERE clause
    # never matches.
    pid_str = project_id.hex

    # Try UPDATE first (counter exists)
    upd = text(
        "UPDATE shortcode_counters "
        "SET next_seq = next_seq + 1, "
        "    updated_at = CURRENT_TIMESTAMP "
        "WHERE project_id = :pid AND type_code = :tc "
        "RETURNING next_seq"
    )
    try:
        result = await session_or_conn.execute(upd, {"pid": pid_str, "tc": type_code})
        row = result.fetchone()
        if row is not None:
            # next_seq AFTER increment; the seq we hand out is one less
            return int(row[0]) - 1
    except Exception:
        # Some drivers don't support RETURNING; fall through to lookup
        pass

    # No counter row yet — insert with next_seq=2 (we hand out 1)
    from uuid import uuid4
    ins = text(
        "INSERT INTO shortcode_counters (id, project_id, type_code, next_seq, updated_at) "
        "VALUES (:id, :pid, :tc, 2, CURRENT_TIMESTAMP) "
        "ON CONFLICT (project_id, type_code) DO UPDATE "
        "SET next_seq = shortcode_counters.next_seq + 1, "
        "    updated_at = CURRENT_TIMESTAMP"
    )
    try:
        await session_or_conn.execute(
            ins, {"id": str(uuid4()), "pid": pid_str, "tc": type_code}
        )
    except Exception:
        # If ON CONFLICT isn't recognised (older sqlite), fall back to
        # explicit lookup + retry.
        sel = text(
            "SELECT next_seq FROM shortcode_counters "
            "WHERE project_id = :pid AND type_code = :tc"
        )
        result = await session_or_conn.execute(sel, {"pid": pid_str, "tc": type_code})
        row = result.fetchone()
        if row is None:
            await session_or_conn.execute(
                text(
                    "INSERT INTO shortcode_counters "
                    "(id, project_id, type_code, next_seq, updated_at) "
                    "VALUES (:id, :pid, :tc, 2, CURRENT_TIMESTAMP)"
                ),
                {"id": str(uuid4()), "pid": pid_str, "tc": type_code},
            )
            return 1
        # Already exists — increment manually
        await session_or_conn.execute(
            text(
                "UPDATE shortcode_counters SET next_seq = next_seq + 1 "
                "WHERE project_id = :pid AND type_code = :tc"
            ),
            {"pid": pid_str, "tc": type_code},
        )
        return int(row[0])

    # After insert: we handed out 1
    sel = text(
        "SELECT next_seq FROM shortcode_counters "
        "WHERE project_id = :pid AND type_code = :tc"
    )
    result = await session_or_conn.execute(sel, {"pid": pid_str, "tc": type_code})
    row = result.fetchone()
    return int(row[0]) - 1 if row else 1


async def allocate_shortcode(
    session_or_conn: Any,
    project_id: UUID,
    type_code: str,
) -> str:
    """Allocate AND format a complete shortcode.  One call, atomic."""
    seq = await allocate_next_seq(session_or_conn, project_id, type_code)
    return build_shortcode(project_id, type_code, seq)


# ── Lookup ────────────────────────────────────────────────────────────


def parse_shortcode(code: str) -> Optional[Tuple[str, str, int]]:
    """Parse a shortcode string into (project_prefix, type_code, seq).

    Returns None if the code doesn't match the expected format.
    """
    if not code or not isinstance(code, str):
        return None
    parts = code.strip().lower().split("-")
    if len(parts) != 3:
        return None
    prefix, type_code, seq_str = parts
    if len(prefix) != 4 or not all(c in "0123456789abcdef" for c in prefix):
        return None
    if not type_code or not seq_str.isdigit():
        return None
    return prefix, type_code, int(seq_str)


# ── Backfill ──────────────────────────────────────────────────────────


async def backfill_missing_shortcodes(
    conn: AsyncConnection,
) -> Tuple[int, int, int]:
    """Allocate shortcodes for any row that doesn't have one.

    Run at startup.  Returns ``(n_assets, n_scenes, n_chapters)`` counts.
    The allocation order is deterministic — ``created_at ASC`` then ``id``
    so the same project will produce the same shortcodes on a fresh DB.

    Also creates one default Chapter per project if it has scenes but
    zero chapters — so every existing project gets at least an "Auto
    Chapter 1" umbrella covering all its scenes.
    """
    n_assets = 0
    n_scenes = 0
    n_chapters = 0

    # 1. Assets — backfill in creation order, per-project
    asset_rows = await conn.execute(
        text(
            "SELECT id, project_id, asset_type FROM assets "
            "WHERE short_code IS NULL OR short_code = '' "
            "ORDER BY project_id, created_at, id"
        )
    )
    for row in asset_rows.fetchall():
        a_id, pid, a_type = row[0], row[1], row[2]
        try:
            tc = asset_type_to_code(str(a_type))
            sc = await allocate_shortcode(conn, UUID(str(pid)), tc)
            await conn.execute(
                text("UPDATE assets SET short_code = :sc WHERE id = :id"),
                {"sc": sc, "id": str(a_id)},
            )
            n_assets += 1
        except Exception as e:
            logger.warning(f"Backfill asset {a_id} skipped: {e}")

    # 2. Scenes — backfill in (project, order_index) order
    scene_rows = await conn.execute(
        text(
            "SELECT id, project_id FROM scenes "
            "WHERE short_code IS NULL OR short_code = '' "
            "ORDER BY project_id, order_index, id"
        )
    )
    for row in scene_rows.fetchall():
        s_id, pid = row[0], row[1]
        try:
            sc = await allocate_shortcode(conn, UUID(str(pid)), "sce")
            await conn.execute(
                text("UPDATE scenes SET short_code = :sc WHERE id = :id"),
                {"sc": sc, "id": str(s_id)},
            )
            n_scenes += 1
        except Exception as e:
            logger.warning(f"Backfill scene {s_id} skipped: {e}")

    # 3. Default chapter for projects that have scenes but no chapters
    project_rows = await conn.execute(
        text(
            "SELECT p.id FROM projects p "
            "WHERE EXISTS (SELECT 1 FROM scenes s WHERE s.project_id = p.id) "
            "  AND NOT EXISTS (SELECT 1 FROM chapters c WHERE c.project_id = p.id)"
        )
    )
    from uuid import uuid4
    for row in project_rows.fetchall():
        pid = row[0]
        try:
            sc = await allocate_shortcode(conn, UUID(str(pid)), "ch")
            ch_id = str(uuid4())
            # Get scene time range to anchor the chapter
            time_rows = await conn.execute(
                text(
                    "SELECT MIN(start_time), MAX(end_time) FROM scenes "
                    "WHERE project_id = :pid"
                ),
                {"pid": str(pid)},
            )
            tr = time_rows.fetchone()
            start_t = float(tr[0]) if tr and tr[0] is not None else 0.0
            end_t = float(tr[1]) if tr and tr[1] is not None else 0.0
            await conn.execute(
                text(
                    "INSERT INTO chapters "
                    "(id, project_id, parent_chapter_id, order_index, depth, "
                    " name, short_code, color, auto_generated, source, "
                    " start_time, end_time, tags, chapter_metadata, "
                    " created_at, updated_at) "
                    "VALUES (:id, :pid, NULL, 0, 0, :name, :sc, '#7c3aed', 1, 'auto', "
                    " :st, :et, '[]', '{}', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                ),
                {
                    "id": ch_id,
                    "pid": str(pid),
                    "name": "Chapter 1",
                    "sc": sc,
                    "st": start_t,
                    "et": end_t,
                },
            )
            # Bind all of the project's scenes to this chapter
            await conn.execute(
                text(
                    "UPDATE scenes SET chapter_id = :cid WHERE project_id = :pid"
                ),
                {"cid": ch_id, "pid": str(pid)},
            )
            n_chapters += 1
        except Exception as e:
            logger.warning(f"Backfill default chapter for project {pid} skipped: {e}")

    return n_assets, n_scenes, n_chapters


# ── Lookup helpers (used by /api/shortcode/{code}) ────────────────────


async def resolve_shortcode(session: AsyncSession, code: str) -> Optional[Dict[str, Any]]:
    """Find the entity matching a shortcode.  Returns a dict with kind,
    id, project_id, and a frontend route hint, or None if not found.
    """
    parsed = parse_shortcode(code)
    if parsed is None:
        return None
    prefix, type_code, seq = parsed
    normalized = build_shortcode_from_prefix(prefix, type_code, seq)

    # Asset
    if type_code in ("img", "vid", "aud", "mus", "ref", "chr", "clo", "itm", "plc"):
        result = await session.execute(
            text("SELECT id, project_id FROM assets WHERE short_code = :sc"),
            {"sc": normalized},
        )
        row = result.fetchone()
        if row:
            return {
                "kind": "asset",
                "id": str(row[0]),
                "project_id": str(row[1]),
                "shortcode": normalized,
                "frontend_route": f"/projects/{row[1]}/assets#{row[0]}",
            }
        return None

    # Scene
    if type_code == "sce":
        result = await session.execute(
            text("SELECT id, project_id, chapter_id FROM scenes WHERE short_code = :sc"),
            {"sc": normalized},
        )
        row = result.fetchone()
        if row:
            return {
                "kind": "scene",
                "id": str(row[0]),
                "project_id": str(row[1]),
                "chapter_id": str(row[2]) if row[2] else None,
                "shortcode": normalized,
                "frontend_route": (
                    f"/projects/{row[1]}/c/{await _chapter_shortcode(session, row[2])}"
                    if row[2] else f"/projects/{row[1]}"
                ),
            }
        return None

    # Chapter
    if type_code == "ch":
        result = await session.execute(
            text("SELECT id, project_id FROM chapters WHERE short_code = :sc"),
            {"sc": normalized},
        )
        row = result.fetchone()
        if row:
            return {
                "kind": "chapter",
                "id": str(row[0]),
                "project_id": str(row[1]),
                "shortcode": normalized,
                "frontend_route": f"/projects/{row[1]}/c/{normalized}",
            }
        return None

    return None


def build_shortcode_from_prefix(prefix: str, type_code: str, seq: int) -> str:
    """Same as ``build_shortcode`` but takes a raw 4-char prefix string."""
    return f"{prefix}-{type_code}-{seq:0{_pad_for(type_code)}d}"


async def _chapter_shortcode(session: AsyncSession, chapter_id: Any) -> Optional[str]:
    """Lookup a chapter's shortcode by its UUID."""
    if not chapter_id:
        return None
    result = await session.execute(
        text("SELECT short_code FROM chapters WHERE id = :id"),
        {"id": str(chapter_id)},
    )
    row = result.fetchone()
    return str(row[0]) if row else None
