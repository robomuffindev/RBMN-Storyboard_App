"""FastAPI application for Robomuffin Idea Factory."""
import asyncio
import logging
import logging.handlers
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from backend.config import settings
from backend.database import init_db, cleanup_db
from backend.api.files import router as files_router

# ---------------------------------------------------------------------------
# Logging setup — console + rotating file
# ---------------------------------------------------------------------------
_log_fmt = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
_log_level = getattr(logging, settings.log_level.upper(), logging.INFO)

# Console handler (already configured by basicConfig in run.py, but ensure it
# exists when running via `uvicorn backend.main:app` directly)
logging.basicConfig(level=_log_level, format=_log_fmt)

# File handler — writes to `logs/rbmn.log` next to the project root.
# RotatingFileHandler keeps the last 5 × 10 MB files (~50 MB max).
_log_dir = Path(__file__).resolve().parent.parent / "logs"
_log_dir.mkdir(exist_ok=True)
_file_handler = logging.handlers.RotatingFileHandler(
    _log_dir / "rbmn.log",
    maxBytes=10 * 1024 * 1024,  # 10 MB per file
    backupCount=5,
    encoding="utf-8",
)
_file_handler.setLevel(_log_level)
_file_handler.setFormatter(logging.Formatter(_log_fmt))
logging.getLogger().addHandler(_file_handler)  # attach to root logger

logger = logging.getLogger(__name__)
logger.info(f"Log file: {_log_dir / 'rbmn.log'}")

# Suppress noisy asyncio transport warnings (socket.send() raised exception).
# These happen when SSE clients disconnect and the ASGI transport tries to write
# to the dead socket — harmless but flood the terminal with hundreds of warnings.
logging.getLogger("asyncio").setLevel(logging.ERROR)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan context manager."""
    # Startup
    logger.info("Starting Robomuffin Idea Factory")
    await init_db()

    # Register default workflows on startup
    from backend.database import async_session
    from backend.services.comfyui.defaults import register_default_workflows

    async with async_session() as session:
        await register_default_workflows(session)

    # Initialize services
    from backend.services.comfyui.dispatcher import ComfyDispatcher
    from backend.services.jobs.dispatcher import JobDispatcher
    from backend.services.jobs.queue import JobQueue
    from backend.database.models import AppSettings
    from sqlmodel import select

    # Create job queue (DB-backed, uses async_session factory)
    app.state.job_queue = JobQueue(session_factory=async_session)

    # Create ComfyUI dispatcher
    app.state.comfy_dispatcher = ComfyDispatcher()

    # Load ComfyUI URLs from settings
    async with async_session() as session:
        settings_stmt = select(AppSettings).where(AppSettings.id == 1)
        result = await session.execute(settings_stmt)
        app_settings = result.scalars().first()

        # Apply project_dir from DB settings if set (overrides env default)
        if app_settings and app_settings.project_dir:
            from pathlib import Path as _Path
            settings.project_dir = _Path(app_settings.project_dir).expanduser()
            logger.info(f"Project directory from DB settings: {settings.project_dir}")

        if app_settings and app_settings.comfyui_urls:
            server_caps = app_settings.comfyui_server_caps or {}
            for url in app_settings.comfyui_urls:
                try:
                    worker = app.state.comfy_dispatcher.add_worker(url)
                    # Apply user-configured capabilities if set
                    caps_config = server_caps.get(url, {})
                    if caps_config:
                        user_caps = set()
                        if caps_config.get("image", True):
                            user_caps.add("klein")
                        if caps_config.get("video", True):
                            user_caps.add("ltx")
                        worker.capabilities = user_caps
                        logger.info(f"Applied user caps for {url}: {user_caps}")
                    logger.info(f"Added ComfyUI worker: {url}")
                except Exception as e:
                    logger.warning(f"Failed to add ComfyUI worker {url}: {e}")

    # Create job dispatcher (unified: reads/writes same DB as API)
    app.state.job_dispatcher = JobDispatcher(
        job_queue=app.state.job_queue,
        comfy_dispatcher=app.state.comfy_dispatcher,
        session_factory=async_session,
    )

    # Start dispatch loop
    app.state.dispatch_task = asyncio.create_task(
        app.state.job_dispatcher.dispatch_loop()
    )

    # Initialize RunPod manager if configured
    from backend.services.runpod.manager import RunPodManager
    runpod_manager = RunPodManager.get_instance()
    if app_settings and app_settings.runpod_enabled and app_settings.runpod_api_key:
        runpod_manager.configure(
            api_key=app_settings.runpod_api_key,
            pod_configs=app_settings.runpod_pods or [],
            idle_timeout_minutes=app_settings.runpod_idle_timeout or 30,
        )
        await runpod_manager.start_idle_monitor()
        logger.info("RunPod manager initialized with idle monitor")

    logger.info(f"Server configured to listen on {settings.app_host}:{settings.app_port}")

    yield

    # Shutdown
    logger.info("Shutting down Robomuffin Idea Factory")

    # Stop RunPod idle monitor
    await runpod_manager.stop_idle_monitor()

    # Stop job dispatcher
    if hasattr(app.state, "job_dispatcher"):
        app.state.job_dispatcher.stop()

    if hasattr(app.state, "dispatch_task"):
        try:
            app.state.dispatch_task.cancel()
            await app.state.dispatch_task
        except asyncio.CancelledError:
            pass

    await cleanup_db()
    logger.info("Shutdown complete")


# Create FastAPI app
app = FastAPI(
    title="Robomuffin Idea Factory",
    description="AI music video / narration video creation tool",
    version="0.1.0",
    lifespan=lifespan,
)

# Add CORS middleware (allow all origins for pywebview compatibility)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Health check endpoint
@app.get("/api/health")
async def health_check():
    """Health check endpoint."""
    return {
        "status": "ok",
        "app": "Robomuffin Idea Factory",
        "version": "0.1.0",
    }


# API Routes - include routers from backend.api
from backend.api import (
    projects_router,
    scenes_router,
    assets_router,
    generation_router,
    timeline_router,
    settings_router,
    jobs_router,
    export_router,
    workflows_router,
    concept_router,
    retrim_all_router,
)

app.include_router(projects_router)
app.include_router(scenes_router)
app.include_router(assets_router)
app.include_router(generation_router)
app.include_router(timeline_router)
app.include_router(settings_router)
app.include_router(jobs_router)
app.include_router(export_router)
app.include_router(workflows_router)
app.include_router(concept_router)
app.include_router(retrim_all_router)
app.include_router(files_router)

# Log registered routes for debugging
_gen_routes = []
for route in app.routes:
    if hasattr(route, 'methods') and hasattr(route, 'path'):
        if 'generate' in route.path:
            _gen_routes.append(f"{route.methods} {route.path}")
if _gen_routes:
    logger.info(f"Generation routes registered: {_gen_routes}")
else:
    logger.error("WARNING: No generation routes found! Check generation.py imports.")


# Static files and SPA routing
# Check if frontend build exists
frontend_dist = Path(__file__).parent.parent / "frontend" / "dist"

if frontend_dist.exists():
    # Mount static files
    app.mount("/assets", StaticFiles(directory=frontend_dist / "assets"), name="assets")

    # Catch-all for SPA routing
    @app.get("/{full_path:path}")
    async def serve_spa(full_path: str):
        """Serve SPA index.html for all non-API routes."""
        # Don't serve index.html for API routes
        if full_path.startswith("api/"):
            return {"detail": "Not Found"}

        index_path = frontend_dist / "index.html"
        if index_path.exists():
            return FileResponse(index_path)

        return {"detail": "Frontend not built"}
else:
    logger.warning(f"Frontend build not found at {frontend_dist}")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        app,
        host=settings.app_host,
        port=settings.app_port,
        log_level=settings.log_level.lower(),
        timeout_keep_alive=300,  # 5 min keep-alive for long-running requests (Demucs)
    )
