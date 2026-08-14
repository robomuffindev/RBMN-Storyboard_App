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
                _orig_status = _oj.status
                _oj.status = JobStatus.FAILED
                _oj.error = (
                    "Orphaned at startup — previous backend session left this "
                    f"job in {_orig_status.value if hasattr(_orig_status, 'value') else _orig_status} "
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

    # ── Chapter dedup sweep ──────────────────────────────────────────────
    # The chapter-doubling bug (1.8.0–1.8.14) left some user databases with
    # 2x rows per (name, depth, parent) tuple.  Even after the in-line
    # auto-dedup at the end of rebuild_chapters, projects that haven't been
    # re-rebuilt since the bug ran still carry the doubles.  Run a
    # one-shot sweep on every boot — cheap (one query per project, only
    # touches projects that actually have duplicates) and self-healing.
    try:
        from backend.services.chapters import deduplicate_project_chapters
        from backend.database.models import Project as _Proj
        from sqlmodel import select as _sel
        async with async_session() as _dd_session:
            _proj_rows = await _dd_session.execute(_sel(_Proj.id))
            _proj_ids = [r[0] for r in _proj_rows.all()]
            _total_dropped = 0
            for _pid in _proj_ids:
                try:
                    _dropped = await deduplicate_project_chapters(_dd_session, _pid)
                    if _dropped:
                        _total_dropped += _dropped
                        logger.warning(
                            f"Chapter dedup sweep: project {_pid} had "
                            f"{_dropped} duplicate chapter row(s) — removed."
                        )
                except Exception as _proj_err:
                    logger.error(
                        f"Chapter dedup sweep failed for project {_pid}: "
                        f"{_proj_err}",
                        exc_info=True,
                    )
            if _total_dropped > 0:
                logger.warning(
                    f"Chapter dedup sweep: removed {_total_dropped} duplicate "
                    f"chapter row(s) across {len(_proj_ids)} project(s)."
                )
            else:
                logger.info(
                    f"Chapter dedup sweep: no duplicates across "
                    f"{len(_proj_ids)} project(s)."
                )
    except Exception as _dd_err:
        logger.error(f"Chapter dedup sweep failed (non-fatal): {_dd_err}", exc_info=True)

    # ── Auto-vs-manual name-collision sweep ──────────────────────────────
    # Real-world DBs (diagnosed 2026-06-15) have rows like:
    #   Chapter 2  source=manual  t=190-380   <- user-edited round bounds
    #   Chapter 2  source=auto    t=194-407   <- auto-creator's pass
    # Same name, different time, dedup-key (name,depth,parent,start_time)
    # correctly does NOT collapse them.  But the user sees a "doubled"
    # chapter in the UI.  Manual rows are the user's source of truth.
    # Drop the auto-side collisions on startup so existing DBs heal
    # without manual cleanup.
    try:
        from sqlalchemy import text as _txt
        async with async_session() as _coll_session:
            _coll_rows = await _coll_session.execute(
                _txt(
                    "SELECT a.id, a.project_id, a.name "
                    "FROM chapters a "
                    "JOIN chapters b "
                    "  ON a.project_id = b.project_id "
                    " AND a.name = b.name "
                    " AND a.depth = b.depth "
                    " AND COALESCE(a.parent_chapter_id, '') = COALESCE(b.parent_chapter_id, '') "
                    "WHERE a.source = 'auto' AND b.source = 'manual'"
                )
            )
            _coll_ids = [(r[0], r[1], r[2]) for r in _coll_rows.all()]
            if _coll_ids:
                _ids = [r[0] for r in _coll_ids]
                _ph = ",".join("?" * len(_ids))
                _raw = await _coll_session.connection()
                # Unbind scenes pointing at the auto rows so they cascade
                # to the manual sibling (rebound by the next suggest
                # timeline / rebuild call).
                await _raw.exec_driver_sql(
                    f"UPDATE scenes SET chapter_id = NULL WHERE chapter_id IN ({_ph})",
                    tuple(_ids),
                )
                # Re-parent any sub-chapters of the doomed rows so the
                # parent FK doesn't kill them.
                await _raw.exec_driver_sql(
                    f"UPDATE chapters SET parent_chapter_id = NULL WHERE parent_chapter_id IN ({_ph})",
                    tuple(_ids),
                )
                await _raw.exec_driver_sql(
                    f"DELETE FROM chapters WHERE id IN ({_ph})",
                    tuple(_ids),
                )
                await _coll_session.commit()
                # Rebind orphan scenes to surviving manual chapters by time.
                from backend.services.chapters.resolver import bind_scenes_to_chapters_by_time
                _by_proj: dict = {}
                for _id, _pid, _name in _coll_ids:
                    _by_proj.setdefault(_pid, []).append(_name)
                for _pid, _names in _by_proj.items():
                    try:
                        await bind_scenes_to_chapters_by_time(_coll_session, _pid)
                    except Exception as _rb_err:
                        logger.error(
                            f"Auto-manual collision sweep: rebind failed "
                            f"for project {_pid}: {_rb_err}"
                        )
                await _coll_session.commit()
                logger.warning(
                    f"Auto-manual chapter collision sweep: dropped "
                    f"{len(_coll_ids)} auto row(s) colliding with manual "
                    f"chapter names: {[r[2] for r in _coll_ids[:5]]}"
                )
            else:
                logger.info("Auto-manual chapter collision sweep: none found")
    except Exception as _coll_err:
        logger.error(
            f"Auto-manual chapter collision sweep failed (non-fatal): "
            f"{_coll_err}",
            exc_info=True,
        )

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

    # Periodic WAL checkpoint (TRUNCATE) — keeps the SQLite -wal file from
    # parking at the ~4 MB autocheckpoint ceiling and reclaims disk after
    # write bursts (auto-gen / batch).  See database.checkpoint_wal.
    from backend.database import periodic_wal_checkpoint
    app.state.wal_checkpoint_task = asyncio.create_task(
        periodic_wal_checkpoint(interval_seconds=300)
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

    # ⚠⚠ v1.276.48 — WORKER HEALTH WAS NEVER RE-CHECKED. `add_worker` sets
    # `healthy=True` optimistically at registration and `health_check_all()`
    # existed but **nothing ever called it** — so a box that rebooted, slept or
    # was switched off stayed "healthy" forever. Lorenzo's trainer rebooted
    # mid-session and `/api/debug/snapshot` still reported it healthy while
    # BOTH its ports were timing out.
    # That matters much more since v1.276.45: the fan-out assigns work
    # ROUND-ROBIN across every "healthy" worker, so one dead box quietly fails
    # every Nth image of a batch instead of being skipped.
    # ⚠ Runs in a THREAD — `health_check_all` is synchronous and talks to three
    # boxes over the LAN; on the event loop it would stall the whole app for as
    # long as a dead box takes to time out, which is the v1.276.41 mistake.
    # ⚠⚠ AND THE SECOND HALF, which is the one that actually bit him:
    # `add_worker` RAISES when a box is unreachable, so a worker that is down
    # AT STARTUP is never registered — and the health loop cannot rescue it,
    # because that loop only iterates workers already in the registry. Net
    # effect: **a box that is asleep when the backend starts stays invisible
    # until the next restart.** His trainer rebooted, the backend restarted
    # while it was still coming up, and .201 simply vanished from the fleet
    # even after it was back. The loop re-attempts the missing ones.
    async def _reattach_missing_workers() -> None:
        from sqlalchemy import select as _select

        from backend.database.models import AppSettings as _AS
        from backend.services.comfyui.dispatcher import apply_user_caps as _caps
        d = getattr(app.state, "comfy_dispatcher", None)
        if not d:
            return
        async with async_session() as s:
            row = (await s.execute(_select(_AS).where(_AS.id == 1))).scalars().first()
        urls = list((row.comfyui_urls if row else None) or [])
        caps = (row.comfyui_server_caps if row else None) or {}
        for url in urls:
            if url in d.workers:
                continue
            try:
                w = await asyncio.to_thread(d.add_worker, url)
                _caps(w, caps.get(url, {}))
                logger.info(f"Worker REJOINED the fleet: {url}")
            except Exception:  # noqa: BLE001 — still down; try again next sweep
                logger.debug(f"Worker still unreachable, not registered: {url}")

    async def _worker_health_loop() -> None:
        while True:
            await asyncio.sleep(45)
            try:
                d = getattr(app.state, "comfy_dispatcher", None)
                if d:
                    await asyncio.to_thread(d.health_check_all)
                await _reattach_missing_workers()
            except Exception:  # noqa: BLE001 — never let this kill the loop
                logger.exception("worker health check failed (continuing)")

    app.state.worker_health_task = asyncio.create_task(_worker_health_loop())

    # ⚡ Autogen: re-attach to any run a restart interrupted (v1.276.42).
    # ⚠ AFTER everything else and just before the yield, because a resumed job
    # immediately calls this app's own HTTP API — doing it at import time (where
    # it started life) fires those requests before uvicorn binds the port, so
    # every resumed job would fail with "connection refused" the instant it
    # started. A tiny delay is used for the same reason: `lifespan` runs before
    # the socket accepts, so the drainer waits a moment for the door to open.
    try:
        import threading as _thr

        from backend.api.autogen import resume_on_startup as _autogen_resume

        def _late_resume() -> None:
            import time as _t
            _t.sleep(5)
            try:
                _autogen_resume()
            except Exception:  # noqa: BLE001
                logger.exception("autogen: resume failed (continuing)")

        _thr.Thread(target=_late_resume, daemon=True, name="autogen-resume").start()
    except Exception:  # noqa: BLE001
        logger.exception("autogen: could not schedule resume (continuing)")

    yield

    # Shutdown
    logger.info("Shutting down Robomuffin Idea Factory")

    if hasattr(app.state, "worker_health_task"):
        app.state.worker_health_task.cancel()
        try:
            await app.state.worker_health_task
        except asyncio.CancelledError:
            pass

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

    # Stop the periodic WAL checkpoint loop before final checkpoint+dispose
    if hasattr(app.state, "wal_checkpoint_task"):
        app.state.wal_checkpoint_task.cancel()
        try:
            await app.state.wal_checkpoint_task
        except asyncio.CancelledError:
            pass

    await cleanup_db()
    logger.info("Shutdown complete")


# Resolve the app version from the repo VERSION file (single source of
# truth — pyproject/CHANGELOG track it) with a safe fallback.
def _read_app_version() -> str:
    try:
        from pathlib import Path as _P
        _vf = _P(__file__).resolve().parent.parent / "VERSION"
        _v = _vf.read_text(encoding="utf-8").strip()
        return _v or "0.0.0"
    except Exception:
        return "0.0.0"


APP_VERSION = _read_app_version()

# Create FastAPI app
app = FastAPI(
    title="Robomuffin Idea Factory",
    description="AI music video / narration video creation tool",
    version=APP_VERSION,
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
        "version": APP_VERSION,
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
from backend.api.character_studio import router as character_studio_router
from backend.api.tools import router as tools_router
from backend.api.vnccs_native import router as vnccs_native_router
from backend.api.image_workshop import router as image_workshop_router
app.include_router(global_characters_router)
app.include_router(character_studio_router)
app.include_router(vnccs_native_router)
app.include_router(tools_router)
app.include_router(image_workshop_router)
from backend.api.klein2 import router as klein2_router  # noqa: E402
app.include_router(klein2_router)
from backend.api.klein3 import router as klein3_router  # noqa: E402
app.include_router(klein3_router)

# 👗 Costume Library (v1.276.27) — design a costume as an image on a neutral
# mannequin, reuse it on any character. Registered after klein3 because it
# imports klein3 helpers at call time.
from backend.api.costumes import router as costumes_router  # noqa: E402
app.include_router(costumes_router)

from backend.api.lora import router as lora_router  # noqa: E402
app.include_router(lora_router)

from backend.api.charsheet import router as charsheet_router  # noqa: E402
app.include_router(charsheet_router)

from backend.api.forge import router as forge_router  # noqa: E402
app.include_router(forge_router)

from backend.api.lora_train import router as lora_train_router  # noqa: E402
app.include_router(lora_train_router)

from backend.api.h3video import router as h3video_router  # noqa: E402
app.include_router(h3video_router)

# ⚡⚡ Autogen v2 (v1.276.42) — a character from a description or photos, to
# whatever point you toggled: base, views, clothing, sheet, dataset, LoRA.
# Plus a serial batch queue across characters. Registered AFTER lora_train
# because it reuses that module's state helpers and _train_pipeline.
from backend.api.autogen import router as autogen_router  # noqa: E402
app.include_router(autogen_router)
# ⚠⚠ Resume is deliberately NOT called here. This block is top-level module
# code that runs at IMPORT time — before uvicorn has bound the port — and the
# autogen pipeline drives everything through this app's own HTTP API. A resumed
# job would fire its first request at a socket nobody is listening on, get
# "connection refused", and be marked `error` instantly. Every resumed job would
# fail, by construction. It is called from `lifespan` instead, once the server
# is actually up. See v1.276.42.

# v1.276.0 — the unified character list. Characters live in two disjoint stores
# (studio_characters rows vs the klein3 disk store) and nothing joined them, so
# a Klein 3.0 character was invisible on /studio and in the Clothes picker.
from backend.api.characters_all import router as characters_all_router  # noqa: E402
app.include_router(characters_all_router)

# 🌍 Story / World Builder (v1.277.0) — worlds contain stories, a shared cast
# and texts (lyrics/narrations). LLM-enhance everything; the cast board submits
# PAPER characters to the autogen serial queue in bulk via a direct _enqueue
# call (same-process — never an HTTP self-call from a route; see v1.276.41).
# Registered after autogen because it bridges into that module's queue.
from backend.api.storyworld import router as storyworld_router  # noqa: E402
app.include_router(storyworld_router)

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
        """Serve real build files, else SPA index.html (non-API routes only)."""
        # Don't serve index.html for API routes
        if full_path.startswith("api/"):
            return {"detail": "Not Found"}

        # Serve actual files from the build first — e.g. /vnccs-pose/*.js ES
        # modules + workers, favicons. Previously everything outside /assets got
        # index.html, so the Pose Studio's dynamic import received HTML and died
        # with "Failed to fetch dynamically imported module".
        if full_path:
            try:
                candidate = (frontend_dist / full_path).resolve()
                candidate.relative_to(frontend_dist.resolve())  # traversal guard
                if candidate.is_file():
                    media_type = None
                    if candidate.suffix in (".js", ".mjs"):
                        # Windows registries sometimes map .js to text/plain,
                        # which browsers reject for ES modules — pin it.
                        media_type = "text/javascript"
                    return FileResponse(candidate, media_type=media_type)
            except (ValueError, OSError):
                pass

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
