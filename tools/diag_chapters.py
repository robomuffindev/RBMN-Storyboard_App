"""
Chapter doubling diagnostic.

Usage:
    python tools/diag_chapters.py [project_id]

If no project_id is passed, the most recently updated project is used.

Output:
    - Project info, mode, lyrics state
    - FULL chapter table dump (every row, even orphaned)
    - Per-chapter scene count
    - Duplicate (name, depth, parent) tuples (the user's "doubling")
    - Scenes-not-in-any-chapter list
    - Recent rebuild_chapters log lines (last 100)
"""

import asyncio
import json
import sys
from pathlib import Path
from collections import defaultdict


async def main(project_id_arg: str | None = None) -> None:
    # Make backend importable
    repo_root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(repo_root))
    from backend.config import settings as cfg
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession

    db_path = cfg.project_dir / "RBMN.db"
    if not db_path.exists():
        print(f"ERROR: DB not found at {db_path}")
        return

    eng = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    async with AsyncSession(eng) as session:
        # Pick project
        if project_id_arg:
            target_pid = project_id_arg.replace("-", "").lower()
        else:
            r = await session.execute(
                text("SELECT id, name FROM projects ORDER BY updated_at DESC LIMIT 1")
            )
            row = r.first()
            if not row:
                print("No projects in DB")
                return
            target_pid = row[0]
            print(f"Using most recent project: {row[1]} ({target_pid})")

        # Project info
        proj = await session.execute(
            text(
                "SELECT id, name, mode, updated_at FROM projects WHERE id = :pid"
            ),
            {"pid": target_pid},
        )
        prow = proj.first()
        if not prow:
            print(f"Project {target_pid} not found")
            return
        print(f"\n=== PROJECT ===")
        print(f"  id   : {prow[0]}")
        print(f"  name : {prow[1]}")
        print(f"  mode : {prow[2]}")
        print(f"  upd  : {prow[3]}")

        # Lyrics
        lyrics = await session.execute(
            text(
                "SELECT initial_text, full_text, words "
                "FROM lyrics WHERE project_id = :pid"
            ),
            {"pid": target_pid},
        )
        lrow = lyrics.first()
        if lrow:
            init_text = (lrow[0] or "")
            full_text = (lrow[1] or "")
            words_json = lrow[2] or "[]"
            try:
                words = json.loads(words_json) if isinstance(words_json, str) else (words_json or [])
            except Exception:
                words = []
            block_words = sum(1 for w in words if isinstance(w, dict) and "block" in w)
            uniq_blocks = len({w.get("block") for w in words if isinstance(w, dict) and "block" in w})
            header_lines = [l for l in init_text.splitlines() if l.strip().startswith("#")]
            print(f"\n=== LYRICS ===")
            print(f"  initial_text  : {len(init_text)} chars, "
                  f"{len(init_text.splitlines())} lines, "
                  f"{len(header_lines)} # header lines")
            if header_lines:
                for hl in header_lines[:10]:
                    print(f"    > {hl[:80]}")
            print(f"  full_text     : {len(full_text)} chars")
            print(f"  words         : {len(words)} entries")
            print(f"  block words   : {block_words} (SRT loaded: {block_words > 0})")
            print(f"  unique blocks : {uniq_blocks}")
        else:
            print("\n=== LYRICS === (no lyrics row)")

        # Chapters — FULL dump
        ch = await session.execute(
            text(
                "SELECT id, name, depth, parent_chapter_id, source, "
                "       start_time, end_time, auto_generated, created_at "
                "FROM chapters WHERE project_id = :pid "
                "ORDER BY depth, order_index, created_at"
            ),
            {"pid": target_pid},
        )
        rows = ch.all()
        print(f"\n=== CHAPTERS ({len(rows)} total) ===")
        if not rows:
            print("  (no chapters)")
        else:
            # Get per-chapter scene counts
            sc = await session.execute(
                text(
                    "SELECT chapter_id, COUNT(*) FROM scenes "
                    "WHERE project_id = :pid AND chapter_id IS NOT NULL "
                    "GROUP BY chapter_id"
                ),
                {"pid": target_pid},
            )
            scene_counts = {r[0]: r[1] for r in sc.all()}
            for r in rows:
                cid, name, depth, parent, source, start, end, auto, created = r
                indent = "  " * (int(depth) + 1)
                n_scenes = scene_counts.get(cid, 0)
                print(
                    f"{indent}{name[:30]:30}  "
                    f"d={depth} src={source or 'NULL':12} "
                    f"t={float(start or 0):.1f}-{float(end or 0):.1f}  "
                    f"scenes={n_scenes:3}  "
                    f"id={str(cid)[:8]}"
                )

        # Duplicate detection
        by_key: dict[tuple, list] = defaultdict(list)
        for r in rows:
            cid, name, depth, parent, source, start, end, auto, created = r
            t_bucket = round(float(start or 0), 1)
            key = (name or "", int(depth or 0), parent or "", t_bucket)
            by_key[key].append((cid, source, created))
        dups = [(k, v) for k, v in by_key.items() if len(v) > 1]
        print(f"\n=== DUPLICATE GROUPS (key=name,depth,parent,start_time) ===")
        if not dups:
            print("  (no duplicates)")
        else:
            for key, members in dups:
                name, depth, parent, t_bucket = key
                print(f"  '{name}' d={depth} parent={parent[:8] if parent else 'None'} t={t_bucket}")
                for cid, source, created in members:
                    print(f"      id={str(cid)[:12]} source={source or 'NULL':12} created={created}")

        # Orphan scenes
        orph = await session.execute(
            text(
                "SELECT id, name, start_time, end_time, order_index "
                "FROM scenes WHERE project_id = :pid AND chapter_id IS NULL "
                "ORDER BY order_index"
            ),
            {"pid": target_pid},
        )
        orph_rows = orph.all()
        print(f"\n=== SCENES NOT IN ANY CHAPTER ({len(orph_rows)} orphans) ===")
        for r in orph_rows[:20]:
            print(f"  #{r[4]:3} '{r[1][:40]:40}' t={float(r[2] or 0):.1f}-{float(r[3] or 0):.1f}")
        if len(orph_rows) > 20:
            print(f"  ... and {len(orph_rows)-20} more")

        # Total scenes
        sc2 = await session.execute(
            text("SELECT COUNT(*) FROM scenes WHERE project_id = :pid"),
            {"pid": target_pid},
        )
        n_total = sc2.scalar() or 0
        print(f"\n=== SCENE TOTAL: {n_total} ===")

    await eng.dispose()


if __name__ == "__main__":
    arg = sys.argv[1] if len(sys.argv) > 1 else None
    asyncio.run(main(arg))
