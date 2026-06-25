"""Database engine setup and session management."""
import asyncio
import logging
from typing import AsyncGenerator

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from backend.config import settings
from backend.database.models import SQLModel

logger = logging.getLogger(__name__)


def get_database_url() -> str:
    """Get database URL with aiosqlite driver."""
    db_path = settings.db_path
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return f"sqlite+aiosqlite:///{db_path}"


# Create async engine — pool_size raised to handle concurrent asset thumbnail loads
engine = create_async_engine(
    get_database_url(),
    echo=False,
    future=True,
    pool_pre_ping=True,
    pool_size=5,
    max_overflow=10,
    pool_timeout=60,
    connect_args={
        "timeout": 30,
        "check_same_thread": False,
    },
)


# Configure SQLite pragmas on connection
@event.listens_for(engine.sync_engine, "connect")
def set_sqlite_pragmas(dbapi_conn, connection_record):
    """Set SQLite pragmas for performance and safety."""
    cursor = dbapi_conn.cursor()

    # WAL mode for better concurrency
    cursor.execute("PRAGMA journal_mode=WAL")

    # Auto-checkpoint threshold (frames).  SQLite's automatic checkpoint runs
    # in PASSIVE mode when the -wal reaches this many pages.  Default is 1000
    # pages which at the 4 KB page size is ~4 MB — the ceiling the -wal parks
    # at.  PASSIVE checkpoints fold committed frames back into the main .db but
    # never TRUNCATE the -wal file, so it sits at ~4 MB with "nothing to
    # commit".  We keep the default threshold and reclaim the space with an
    # explicit TRUNCATE checkpoint (periodic + on shutdown — see checkpoint_wal).
    cursor.execute("PRAGMA wal_autocheckpoint=1000")

    # Write-ahead log synchronous mode (NORMAL = balance between safety and speed)
    cursor.execute("PRAGMA synchronous=NORMAL")

    # Enable foreign key constraints
    cursor.execute("PRAGMA foreign_keys=ON")

    # Busy timeout — 30 s to handle concurrent auto-gen writes
    cursor.execute("PRAGMA busy_timeout=30000")

    # Cache size (64MB)
    cursor.execute("PRAGMA cache_size=-65536")

    # Use memory for temp storage
    cursor.execute("PRAGMA temp_store=MEMORY")

    # Memory-mapped I/O (256MB)
    cursor.execute("PRAGMA mmap_size=268435456")

    cursor.close()


# Session factory
async_session = sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    future=True,
)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """Get async database session as FastAPI dependency."""
    async with async_session() as session:
        try:
            yield session
        finally:
            await session.close()


async def init_db() -> None:
    """Initialize database tables."""
    logger.info(f"Initializing database at {settings.db_path}")

    # Create all tables
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)

    # Run safe migrations for new columns on existing tables
    # SQLModel.metadata.create_all won't ALTER existing tables
    async with engine.begin() as conn:
        # Add initial_text column to lyrics table if missing
        try:
            await conn.execute(
                text("ALTER TABLE lyrics ADD COLUMN initial_text TEXT DEFAULT ''")
            )
            logger.info("Migration: added initial_text column to lyrics table")
        except Exception:
            pass  # Column already exists

        # Add image_model_type column to app_settings if missing
        try:
            await conn.execute(
                text("ALTER TABLE app_settings ADD COLUMN image_model_type VARCHAR DEFAULT 'flux2_klein_dev_9b'")
            )
            logger.info("Migration: added image_model_type column to app_settings table")
        except Exception:
            pass  # Column already exists

        # Add video_model_type column to app_settings if missing
        try:
            await conn.execute(
                text("ALTER TABLE app_settings ADD COLUMN video_model_type VARCHAR DEFAULT 'ltx_2.3'")
            )
            logger.info("Migration: added video_model_type column to app_settings table")
        except Exception:
            pass  # Column already exists

        # Add system prompt override columns to app_settings if missing
        try:
            await conn.execute(
                text("ALTER TABLE app_settings ADD COLUMN image_system_prompt_overrides JSON DEFAULT NULL")
            )
            logger.info("Migration: added image_system_prompt_overrides column to app_settings table")
        except Exception:
            pass  # Column already exists

        try:
            await conn.execute(
                text("ALTER TABLE app_settings ADD COLUMN video_system_prompt_overrides JSON DEFAULT NULL")
            )
            logger.info("Migration: added video_system_prompt_overrides column to app_settings table")
        except Exception:
            pass  # Column already exists

        # Add prompt guidance columns to app_settings if missing
        try:
            await conn.execute(
                text("ALTER TABLE app_settings ADD COLUMN image_prompt_guidance JSON DEFAULT NULL")
            )
            logger.info("Migration: added image_prompt_guidance column to app_settings table")
        except Exception:
            pass  # Column already exists

        try:
            await conn.execute(
                text("ALTER TABLE app_settings ADD COLUMN video_prompt_guidance JSON DEFAULT NULL")
            )
            logger.info("Migration: added video_prompt_guidance column to app_settings table")
        except Exception:
            pass  # Column already exists

        # Add global video_fps column to app_settings if missing
        try:
            await conn.execute(
                text("ALTER TABLE app_settings ADD COLUMN video_fps INTEGER DEFAULT 24")
            )
            logger.info("Migration: added video_fps column to app_settings table")
        except Exception:
            pass  # Column already exists

        # Add default_llm_provider column to app_settings if missing
        try:
            await conn.execute(
                text("ALTER TABLE app_settings ADD COLUMN default_llm_provider VARCHAR DEFAULT NULL")
            )
            logger.info("Migration: added default_llm_provider column to app_settings table")
        except Exception:
            pass  # Column already exists

        # Add video_max_duration column to app_settings if missing
        try:
            await conn.execute(
                text("ALTER TABLE app_settings ADD COLUMN video_max_duration INTEGER DEFAULT 15")
            )
            logger.info("Migration: added video_max_duration column to app_settings table")
        except Exception:
            pass  # Column already exists

        # Add video_min_duration column to app_settings if missing
        try:
            await conn.execute(
                text("ALTER TABLE app_settings ADD COLUMN video_min_duration INTEGER DEFAULT 5")
            )
            logger.info("Migration: added video_min_duration column to app_settings table")
        except Exception:
            pass  # Column already exists

        # Add video_tail column to app_settings if missing
        try:
            await conn.execute(
                text("ALTER TABLE app_settings ADD COLUMN video_tail INTEGER DEFAULT 0")
            )
            logger.info("Migration: added video_tail column to app_settings table")
        except Exception:
            pass  # Column already exists

        # Add color_correction_enabled column to app_settings if missing
        try:
            await conn.execute(
                text("ALTER TABLE app_settings ADD COLUMN color_correction_enabled BOOLEAN DEFAULT 1")
            )
            logger.info("Migration: added color_correction_enabled column to app_settings table")
        except Exception:
            pass  # Column already exists

        # Add RunPod columns to app_settings if missing
        for col_sql in [
            "ALTER TABLE app_settings ADD COLUMN runpod_enabled BOOLEAN DEFAULT 0",
            "ALTER TABLE app_settings ADD COLUMN runpod_api_key VARCHAR DEFAULT NULL",
            "ALTER TABLE app_settings ADD COLUMN runpod_idle_timeout INTEGER DEFAULT 30",
            "ALTER TABLE app_settings ADD COLUMN runpod_pods JSON DEFAULT NULL",
        ]:
            try:
                await conn.execute(text(col_sql))
                logger.info(f"Migration: {col_sql.split('ADD COLUMN ')[1].split(' ')[0]} added to app_settings")
            except Exception:
                pass  # Column already exists

        # Add export transition columns to app_settings if missing
        for col_sql in [
            "ALTER TABLE app_settings ADD COLUMN export_transition_type VARCHAR DEFAULT 'none'",
            "ALTER TABLE app_settings ADD COLUMN export_transition_duration REAL DEFAULT 0.5",
            "ALTER TABLE app_settings ADD COLUMN export_color_match_clips BOOLEAN DEFAULT 1",
            "ALTER TABLE app_settings ADD COLUMN whisper_comfyui_url VARCHAR DEFAULT NULL",
            "ALTER TABLE app_settings ADD COLUMN whisper_language VARCHAR DEFAULT 'English'",
            "ALTER TABLE app_settings ADD COLUMN comfyui_server_caps JSON DEFAULT NULL",
            "ALTER TABLE app_settings ADD COLUMN export_lfff_trim_enabled BOOLEAN DEFAULT 1",
            "UPDATE app_settings SET export_lfff_trim_enabled = 1 WHERE export_lfff_trim_enabled IS NULL",
            # Migration: change default transition from crossfade to none (one-shot)
            "ALTER TABLE app_settings ADD COLUMN _mig_transition_none BOOLEAN DEFAULT 0",
            "UPDATE app_settings SET export_transition_type = 'none', _mig_transition_none = 1 WHERE _mig_transition_none = 0 AND export_transition_type = 'crossfade'",
            "UPDATE app_settings SET _mig_transition_none = 1 WHERE _mig_transition_none IS NULL",
            # Migration: disable color correction and color match by default once
            # (the previous unguarded UPDATE reset the user's choice on every restart).
            "ALTER TABLE app_settings ADD COLUMN _mig_color_default_off BOOLEAN DEFAULT 0",
            "UPDATE app_settings SET color_correction_enabled = 0, export_color_match_clips = 0, _mig_color_default_off = 1 WHERE _mig_color_default_off = 0 OR _mig_color_default_off IS NULL",
            "ALTER TABLE app_settings ADD COLUMN ltx_model_gguf VARCHAR DEFAULT 'ltx-2.3-22b-dev-Q8_0.gguf'",
            "ALTER TABLE app_settings ADD COLUMN single_image_generator VARCHAR DEFAULT 'z_image_turbo'",
            "ALTER TABLE app_settings ADD COLUMN use_distilled_lora BOOLEAN DEFAULT 1",
            "ALTER TABLE app_settings ADD COLUMN distilled_lora_name VARCHAR DEFAULT 'ltx-2.3-22b-distilled-lora-384.safetensors'",
            "ALTER TABLE app_settings ADD COLUMN restrict_explicit_content BOOLEAN DEFAULT 0",
            "ALTER TABLE app_settings ADD COLUMN global_negative_prompt TEXT DEFAULT NULL",
            "ALTER TABLE app_settings ADD COLUMN project_dir VARCHAR DEFAULT NULL",
            "ALTER TABLE app_settings ADD COLUMN network_access BOOLEAN DEFAULT 0",
        ]:
            try:
                await conn.execute(text(col_sql))
                logger.info(f"Migration: {col_sql.split('ADD COLUMN ')[1].split(' ')[0]} added to app_settings")
            except Exception:
                pass  # Column already exists

        # Add LTXDirector settings columns to app_settings if missing
        for col_sql in [
            "ALTER TABLE app_settings ADD COLUMN director_guide_strength REAL DEFAULT 0.5",
            "ALTER TABLE app_settings ADD COLUMN director_audio_guidance REAL DEFAULT 0.001",
            "ALTER TABLE app_settings ADD COLUMN director_stitch BOOLEAN DEFAULT 0",
            "ALTER TABLE app_settings ADD COLUMN director_auto_image_desc BOOLEAN DEFAULT 1",
            "ALTER TABLE app_settings ADD COLUMN global_video_negative_prompt TEXT DEFAULT NULL",
        ]:
            try:
                await conn.execute(text(col_sql))
                logger.info(f"Migration: {col_sql.split('ADD COLUMN ')[1].split(' ')[0]} added to app_settings")
            except Exception:
                pass  # Column already exists

        # Add app_port column to app_settings if missing
        try:
            await conn.execute(text(
                "ALTER TABLE app_settings ADD COLUMN app_port INTEGER DEFAULT 8899"
            ))
            logger.info("Migration: app_port added to app_settings")
        except Exception:
            pass  # Column already exists

        # Add gpu_acceleration_enabled column to app_settings if missing
        try:
            await conn.execute(text(
                "ALTER TABLE app_settings ADD COLUMN gpu_acceleration_enabled BOOLEAN DEFAULT 1"
            ))
            logger.info("Migration: gpu_acceleration_enabled added to app_settings")
        except Exception:
            pass  # Column already exists

        # Add FFmpeg threading columns to app_settings if missing
        for col_sql in [
            "ALTER TABLE app_settings ADD COLUMN ffmpeg_threads INTEGER DEFAULT 0",
            "ALTER TABLE app_settings ADD COLUMN ffmpeg_filter_threads INTEGER DEFAULT 4",
        ]:
            try:
                await conn.execute(text(col_sql))
                logger.info(f"Migration: {col_sql.split('ADD COLUMN ')[1].split(' ')[0]} added to app_settings")
            except Exception:
                pass  # Column already exists

        # Add Ollama (local LLM) columns to app_settings if missing
        for col_sql in [
            "ALTER TABLE app_settings ADD COLUMN ollama_base_url VARCHAR DEFAULT NULL",
            "ALTER TABLE app_settings ADD COLUMN ollama_urls JSON DEFAULT NULL",
            "ALTER TABLE app_settings ADD COLUMN ollama_model VARCHAR DEFAULT NULL",
            "ALTER TABLE app_settings ADD COLUMN ollama_available_models JSON DEFAULT NULL",
        ]:
            try:
                await conn.execute(text(col_sql))
                logger.info(f"Migration: {col_sql.split('ADD COLUMN ')[1].split(' ')[0]} added to app_settings")
            except Exception:
                pass  # Column already exists

        # Create batch_runs table if it doesn't exist
        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS batch_runs (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                project_name TEXT DEFAULT '',
                mode TEXT DEFAULT '',
                status TEXT DEFAULT 'pending',
                total_scenes INTEGER DEFAULT 0,
                completed_scenes INTEGER DEFAULT 0,
                current_scene_name TEXT,
                current_step TEXT,
                scene_results TEXT DEFAULT '{}',
                error_log TEXT DEFAULT '[]',
                started_at TEXT,
                completed_at TEXT,
                elapsed_ms INTEGER DEFAULT 0,
                run_settings TEXT DEFAULT '{}',
                last_asset_url TEXT,
                last_asset_scene_name TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """))
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_batch_runs_project_id ON batch_runs (project_id)"
        ))
        logger.info("Migration: batch_runs table ensured")

        # Add step_log column to batch_runs if missing
        try:
            await conn.execute(text(
                "ALTER TABLE batch_runs ADD COLUMN step_log TEXT DEFAULT '[]'"
            ))
            logger.info("Migration: step_log added to batch_runs")
        except Exception:
            pass  # Column already exists

        # ── Stale batch run cleanup ──
        # If the app was killed while a batch was running, those runs are stuck
        # in "running" / "pending" forever.  Mark them as failed on startup.
        stale = await conn.execute(text(
            "UPDATE batch_runs SET status = 'failed', "
            "current_step = 'Interrupted — app was restarted' "
            "WHERE status IN ('running', 'pending')"
        ))
        if stale.rowcount:
            logger.info(f"Migration: marked {stale.rowcount} stale batch run(s) as failed")


        # ── Chapters / Shortcodes migration ────────────────────────────
        # See BLUEPRINT_CHAPTERS_v1.md for the design.
        # All operations idempotent — safe to re-run on startup.

        # 1. Add chapter_id + short_code to scenes
        for col_sql in (
            "ALTER TABLE scenes ADD COLUMN chapter_id VARCHAR",
            "ALTER TABLE scenes ADD COLUMN short_code VARCHAR",
        ):
            try:
                await conn.execute(text(col_sql))
                logger.info(f"Migration (chapters): {col_sql}")
            except Exception:
                pass

        # 2. Add short_code + tags to assets
        for col_sql in (
            "ALTER TABLE assets ADD COLUMN short_code VARCHAR",
            "ALTER TABLE assets ADD COLUMN tags JSON DEFAULT '[]'",
        ):
            try:
                await conn.execute(text(col_sql))
                logger.info(f"Migration (chapters): {col_sql}")
            except Exception:
                pass

        # 3. Add 4 LLM-batching columns to app_settings
        for col_sql, default in (
            ("ALTER TABLE app_settings ADD COLUMN llm_chapter_scene_limit_cloud INTEGER DEFAULT 25", 25),
            ("ALTER TABLE app_settings ADD COLUMN llm_chapter_scene_limit_ollama INTEGER DEFAULT 12", 12),
            ("ALTER TABLE app_settings ADD COLUMN chapter_auto_split_threshold INTEGER DEFAULT 25", 25),
            ("ALTER TABLE app_settings ADD COLUMN chapter_max_depth INTEGER DEFAULT 2", 2),
        ):
            try:
                await conn.execute(text(col_sql))
                logger.info(f"Migration (chapters): {col_sql}")
            except Exception:
                pass

        # 4. Indexes for new shortcode columns (unique partial index — only enforce on non-null)
        for idx_sql in (
            "CREATE UNIQUE INDEX IF NOT EXISTS ix_scenes_short_code ON scenes (short_code) WHERE short_code IS NOT NULL",
            "CREATE UNIQUE INDEX IF NOT EXISTS ix_assets_short_code ON assets (short_code) WHERE short_code IS NOT NULL",
            "CREATE INDEX IF NOT EXISTS ix_scenes_chapter_id ON scenes (chapter_id)",
        ):
            try:
                await conn.execute(text(idx_sql))
            except Exception as e:
                logger.debug(f"Migration (chapters) idx skipped: {e}")

        # 5. Indexes for chapters table (created by create_all but explicit for clarity)
        for idx_sql in (
            "CREATE INDEX IF NOT EXISTS ix_chapters_project_id ON chapters (project_id)",
            "CREATE INDEX IF NOT EXISTS ix_chapters_parent ON chapters (parent_chapter_id)",
            "CREATE UNIQUE INDEX IF NOT EXISTS ix_chapters_short_code ON chapters (short_code)",
            "CREATE INDEX IF NOT EXISTS ix_shortcode_counters_project_type ON shortcode_counters (project_id, type_code)",
        ):
            try:
                await conn.execute(text(idx_sql))
            except Exception as e:
                logger.debug(f"Migration (chapters) idx skipped: {e}")

        # New: chapter creative-direction fields
        for col_sql in (
            "ALTER TABLE chapters ADD COLUMN description TEXT DEFAULT ''",
            "ALTER TABLE chapters ADD COLUMN character_focus JSON DEFAULT '[]'",
            "ALTER TABLE chapters ADD COLUMN style_notes TEXT DEFAULT ''",
        ):
            try:
                await conn.execute(text(col_sql))
                logger.info(f"Migration (chapters): {col_sql}")
            except Exception:
                pass

        logger.info("Migration (chapters): schema patches complete")

        # 6. Backfill — runs only for projects/assets that don't yet have shortcodes.
        #    The actual allocation logic lives in backend.services.shortcode so the
        #    same algorithm is used at runtime.  Import lazily to avoid circular
        #    imports during startup.
        try:
            from backend.services.shortcode import backfill_missing_shortcodes
            n_assets, n_scenes, n_chapters = await backfill_missing_shortcodes(conn)
            if n_assets or n_scenes or n_chapters:
                logger.info(
                    f"Migration (chapters): backfilled shortcodes — "
                    f"{n_assets} assets, {n_scenes} scenes, {n_chapters} chapters"
                )
        except Exception as e:
            # Backfill failure is non-fatal — runtime allocation still works
            logger.warning(f"Migration (chapters): backfill failed (non-fatal): {e}")

    logger.info("Database initialized successfully")


async def checkpoint_wal(mode: str = "TRUNCATE"):
    """Force a WAL checkpoint; for TRUNCATE this shrinks the -wal to 0 bytes.

    Automatic checkpoints run in PASSIVE mode at the wal_autocheckpoint
    threshold (~4 MB).  PASSIVE folds committed frames back into the main .db
    but never truncates the -wal, so it stays at ~4 MB even with nothing left
    to commit.  An explicit wal_checkpoint(TRUNCATE) reclaims that space.

    Returns (busy, log_frames, checkpointed_frames) or None on failure.
    busy=1 means a reader/writer held the lock so the file was not truncated
    this pass — it succeeds on a later idle pass and is NOT an error.
    """
    mode = (mode or "TRUNCATE").upper()
    if mode not in ("PASSIVE", "FULL", "RESTART", "TRUNCATE"):
        mode = "TRUNCATE"
    try:
        async with engine.connect() as conn:
            result = await conn.exec_driver_sql(f"PRAGMA wal_checkpoint({mode})")
            row = result.fetchone()
        if row is not None:
            busy, log_frames, ckpt = int(row[0]), int(row[1]), int(row[2])
            if busy:
                logger.debug(
                    f"WAL checkpoint({mode}) busy=1 — lock held, -wal not "
                    f"truncated this pass (log={log_frames} frames)"
                )
            else:
                logger.debug(
                    f"WAL checkpoint({mode}) ok — log={log_frames}, "
                    f"checkpointed={ckpt}"
                )
            return busy, log_frames, ckpt
        return None
    except Exception as e:
        logger.warning(f"WAL checkpoint({mode}) failed: {e}")
        return None


async def periodic_wal_checkpoint(interval_seconds: int = 300) -> None:
    """Background loop: periodically TRUNCATE-checkpoint the WAL.

    Keeps the -wal file from parking at the ~4 MB ceiling during long sessions
    and reclaims disk after large write bursts (auto-gen / batch).  Cancelled
    on shutdown.
    """
    while True:
        try:
            await asyncio.sleep(interval_seconds)
            await checkpoint_wal("TRUNCATE")
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.debug(f"periodic_wal_checkpoint iteration failed: {e}")


async def cleanup_db() -> None:
    """Checkpoint the WAL, then close database connections."""
    # Flush + shrink the -wal so a clean shutdown leaves a 0-byte WAL instead
    # of the ~4 MB PASSIVE-checkpoint residue.
    await checkpoint_wal("TRUNCATE")
    await engine.dispose()
    logger.info("Database connections closed")
