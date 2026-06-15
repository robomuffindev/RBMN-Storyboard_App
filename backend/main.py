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

    # ── Stale orphan job sweep ───────────────────────────────────────────
    # `JobQueue.recover_running_jobs()` runs from the dispatch_loop on
    # startup and handles the FRESH-restart case nicely: cancels PENDING
    # jobs and RUNNING-without-prompt_id, but KEEPS RUNNING-with-prompt_id
    # alive so the retry fast-path can reconnect to expensive in-flight
    # ComfyUI renders that survived a graceful restart.
    #
    # That logic has one blind spot: a RUNNING-with-prompt_id job whose
    # worker is gone (host shut down, ComfyUI history cleared, etc.)
    # stays in RUNNING forever — recover keeps it alive expecting a
    # reconnect that never happens.  Those rows then wedge:
    #   • The auto-gen drain loop, which polls for PENDING/RUNNING jobs
    #     on in-batch scene IDs and waits up to the 30-minute timeout
    #     (the bug the user just hit — see the drain fix in generation.py
    #     `_run_windowed_batch` for the per-run filter).
    #   • The "active workers" panel + queue badges which keep showing
    #     a job that died days ago.
    #
    # Cutoff: 1 hour.  Any PENDING/RUNNING job older than that is
    # definitely orphaned — no real render takes that long without
    # heartbeat, and a recently-started job from a graceful restart
    # less than an hour ago is preserved for reconnect.
    try:
        from backend.database.models import Job, JobStatus
        from sqlmodel import select as _orph_select
        from datetime import datetime as _orph_dt, timedelta as _orph_td
        _orph_cutoff = _orph_dt.utcnow() - _orph_td(hours=1)
        async with async_session() as _orph_session:
            _orph_stmt = _orph_select(Job).where(
                Job.status.in_([JobStatus.PENDING, JobStatus.RUNNING]),  # type: ignore
                Job.created_at < _orph_cutoff,  # type: ignore
            )
            _orph_jobs = list((await _orph_session.execute(_orph_stmt)).scalars().all())
            for _oj in _orph_jobs:
                _oj.status = JobStatus.FAILED
                _oj.error = (
                    "Orphaned at startup — previous backend session left this "
                    f"job in {_oj.status.value if hasattr(_oj.status, 'value') else _oj.status} "
                    "state for >1h without progress."
                )
                _oj.completed_at = _orph_dt.utcnow()
            if _orph_jobs:
                await _orph_session.commit()
                logger.warning(
                    f"Orphan sweep: marked {len(_orph_jobs)} stale job(s) FAILED "
                    f"(>1h old, left PENDING/RUNNING by a prior session). "
                    f"recover_running_jobs() will handle any fresh in-flight rows."
                )
            else:
                logger.info("Orphan sweep: no stale jobs (>1h old) found")
    except Exception as _orph_err:
        logger.error(f"Orphan sweep failed (non-fatal): {_orph_err}", exc_info=True)

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
            from backend.services.comfyui.dispatcher import apply_user_caps
            server_caps = app_settings.comfyui_server_caps or {}
            for url in app_settings.comfyui_urls:
                try:
                    worker = app.state.comfy_dispatcher.add_worker(url)
                    apply_user_caps(worker, server_caps.get(url, {}))
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

    # Eagerly detect GPU capabilities at startup so it's visible in logs
    from backend.services.video.ffmpeg import _gpu as _ffmpeg_gpu
    from backend.services.audio.analysis import _demucs_device
    _ffmpeg_gpu.detect()
    _demucs_device.detect()
    logger.info(
        f"GPU status — FFmpeg: {_ffmpeg_gpu.encoder} ({_ffmpeg_gpu.gpu_type}), "
        f"Demucs: {_demucs_device.device}"
        f"{(' (' + _demucs_device.gpu_name + ')') if _demucs_device.gpu_name else ''}"
    )

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
    version="1.8.14",
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
    """Health check endpoint with GPU status."""
    from backend.services.video.ffmpeg import _gpu as _ffmpeg_gpu
    from backend.services.audio.analysis import _demucs_device
    return {
        "status": "ok",
        "app": "Robomuffin Idea Factory",
        "version": "0.1.0",
        "gpu": {
            "ffmpeg_encoder": _ffmpeg_gpu.encoder,
            "ffmpeg_gpu_type": _ffmpeg_gpu.gpu_type,
            "ffmpeg_decode_hwaccel": _ffmpeg_gpu.decode_hwaccel or "cpu",
            "demucs_device": _demucs_device.device,
            "demucs_gpu_name": _demucs_device.gpu_name or None,
        },
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
    batch_router,
    batch_runs_router,
    backing_tracks_router,
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
app.include_router(batch_router)
app.include_router(batch_runs_router)
app.include_router(backing_tracks_router)
app.include_router(files_router)

# Chapters + shortcodes (Phase 1 — chapter umbrellas)
from backend.api.chapters import router as chapters_router
from backend.api.shortcodes import router as shortcodes_router
app.include_router(chapters_router)
app.include_router(shortcodes_router)

# Debug / diagnostics endpoints (snapshot + log tail)
from backend.api.debug import router as debug_router
app.include_router(debug_router)

# Global character library — reusable characters across projects
from backend.api.global_characters import router as global_characters_router
app.include_router(global_characters_router)

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
