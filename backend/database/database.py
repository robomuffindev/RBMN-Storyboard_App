"""Database engine setup and session management."""
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


# Create async engine
engine = create_async_engine(
    get_database_url(),
    echo=False,
    future=True,
    pool_pre_ping=True,
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

    # Write-ahead log synchronous mode (NORMAL = balance between safety and speed)
    cursor.execute("PRAGMA synchronous=NORMAL")

    # Enable foreign key constraints
    cursor.execute("PRAGMA foreign_keys=ON")

    # Busy timeout (5 seconds)
    cursor.execute("PRAGMA busy_timeout=5000")

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
            # Migration: change default transition from crossfade to none
            "UPDATE app_settings SET export_transition_type = 'none' WHERE export_transition_type = 'crossfade'",
            # Migration: disable color correction and color match by default (too harsh)
            "UPDATE app_settings SET color_correction_enabled = 0 WHERE color_correction_enabled = 1",
            "UPDATE app_settings SET export_color_match_clips = 0 WHERE export_color_match_clips = 1",
            "ALTER TABLE app_settings ADD COLUMN ltx_model_gguf VARCHAR DEFAULT 'ltx-2.3-22b-dev-Q8_0.gguf'",
            "ALTER TABLE app_settings ADD COLUMN restrict_explicit_content BOOLEAN DEFAULT 0",
            "ALTER TABLE app_settings ADD COLUMN project_dir VARCHAR DEFAULT NULL",
        ]:
            try:
                await conn.execute(text(col_sql))
                logger.info(f"Migration: {col_sql.split('ADD COLUMN ')[1].split(' ')[0]} added to app_settings")
            except Exception:
                pass  # Column already exists

    logger.info("Database initialized successfully")


async def cleanup_db() -> None:
    """Close database connections."""
    await engine.dispose()
    logger.info("Database connections closed")
