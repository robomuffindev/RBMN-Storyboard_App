"""Final video export endpoints for RBMN Storyboard App."""
import asyncio
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any, Callable
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from backend.config import settings as app_settings
from backend.database import get_session
from backend.database.models import Project, Job, JobType, JobStatus, Scene, Asset, AssetType, AppSettings

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/projects/{project_id}/export", tags=["export"])


# ── Pydantic models ──────────────────────────────────────────────────

class ExportRequest(BaseModel):
    """Request model for exporting final video.

    Width, height, and FPS default to 0 which means "use project resolution".
    The export endpoint resolves these from project.settings before assembly.
    """
    output_format: str = "mp4"
    width: int = 0   # 0 = use project resolution
    height: int = 0  # 0 = use project resolution
    fps: int = 0     # 0 = use default (24)
    quality: str = "high"
    transition_type: Optional[str] = None  # none, crossfade, dissolve — None = use settings default
    transition_duration: Optional[float] = None  # seconds — None = use settings default
    color_match_clips: Optional[bool] = None  # None = use settings default


class ExportProgressResponse(BaseModel):
    """Response model for export progress."""
    job_id: str
    status: str
    progress_percent: int
    current_step: str
    estimated_time_remaining: Optional[float] = None
    output_path: Optional[str] = None
    download_url: Optional[str] = None
    error: Optional[str] = None


class JobResponse(BaseModel):
    """Response model for a job."""
    id: UUID
    project_id: UUID
    job_type: str
    status: str
    created_at: datetime

    class Config:
        from_attributes = True


class PreviewRenderResponse(BaseModel):
    """Response model for preview render."""
    job_id: str
    status: str
    preview_path: Optional[str] = None
    preview_url: Optional[str] = None
    error: Optional[str] = None


# ── Shared helpers ───────────────────────────────────────────────────

async def _get_project_or_404(project_id: UUID, session: AsyncSession) -> Project:
    """Get project or raise 404."""
    project = await session.get(Project, project_id)
    if not project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Project {project_id} not found",
        )
    return project


async def _build_scene_dicts(
    session: AsyncSession,
    project_id: UUID,
    lfff_trim_enabled: bool = True,
) -> tuple[List[Dict[str, Any]], str]:
    """Build scene dicts and find master audio for assembly.

    Args:
        lfff_trim_enabled: If True, scenes using prev scene's last frame
            will have trim_first_frame=True (removes duplicate frame 0).
            If False, no frame trimming occurs.

    Returns:
        (scene_dicts, master_audio_path) — scene_dicts ready for assembly,
        master_audio_path empty string if no audio asset found.
    """
    # Get all scenes
    stmt = select(Scene).where(Scene.project_id == project_id).order_by(Scene.order_index)
    result = await session.execute(stmt)
    scenes = result.scalars().all()

    if not scenes:
        raise ValueError("No scenes found in project")

    # Find the master audio asset
    audio_stmt = (
        select(Asset)
        .where(Asset.project_id == project_id)
        .where(Asset.asset_type == AssetType.MUSIC)
        .order_by(Asset.created_at.desc())
        .limit(1)
    )
    audio_result = await session.execute(audio_stmt)
    audio_asset = audio_result.scalars().first()

    project_dir = app_settings.project_dir / str(project_id)
    # chosen_*_path values already include the project ID prefix
    # (e.g., "bb4075ed-.../generated/file.mp4"), so resolve from base project_dir
    base_dir = app_settings.project_dir

    # Build scene list for assembly — only include scenes that have content
    scene_dicts: List[Dict[str, Any]] = []
    for sc in scenes:
        sc_params = sc.parameters or {}
        source_type = sc_params.get("scene_source_type", "image")
        video_path = sc_params.get("chosen_video_path")
        image_path = sc_params.get("chosen_image_path")
        logger.info(
            f"Scene {sc.order_index}: source_type={source_type}, "
            f"video_path={'set' if video_path else 'NONE'}, "
            f"image_path={'set' if image_path else 'NONE'}, "
            f"video_mode={sc_params.get('video_mode', 'N/A')}"
        )
        # V2V image-based conditioning: scene B's first frame IS scene A's
        # last frame (conditioned on that exact image via I2V workflow).
        # Same duplicate-frame situation as FF/LF mode with use_prev_lf_as_ff.
        #
        # If the dispatcher already skipped frame 0 during its trim_video
        # step (dispatcher_skipped_first_frame=True), export does NOT need
        # to skip again — the chosen_video_path is already clean.
        # Only apply export-side skip for legacy videos generated before
        # the dispatcher-side fix (no flag set).
        is_v2v = sc_params.get("video_mode") == "v2v_extend"
        dispatcher_already_skipped = bool(sc_params.get("dispatcher_skipped_first_frame"))
        trim_first_frame = (
            lfff_trim_enabled
            and (bool(sc_params.get("use_prev_lf_as_ff")) or is_v2v)
            and not dispatcher_already_skipped
        )

        # Resolve full path — chosen paths include project ID prefix already
        def _resolve(rel: str) -> str:
            if rel.startswith("/") or rel.startswith("\\"):
                return rel
            return str(base_dir / rel)

        # Source selection strategy:
        # Use chosen_video_path as the authoritative source.  Only fall back
        # to video_untrimmed_path when it belongs to the SAME generation as
        # chosen_video_path (the untrimmed filename stem should be a prefix
        # of the chosen filename, or the chosen path should be derivable
        # from the untrimmed path by replacing _untrimmed).
        #
        # Previously, export always preferred video_untrimmed_path, but this
        # path is never cleared when the user switches active videos via
        # "Save as Preview" — it could point to a completely different
        # generation, causing wrong content in the export.
        video_source = video_path  # default to chosen (authoritative)
        v2v_overlap_removed = sc_params.get("v2v_overlap_removed", False)
        v2v_trim_a_applied = sc_params.get("v2v_trim_a_applied", False)
        untrimmed_path = sc_params.get("video_untrimmed_path")

        if v2v_overlap_removed or v2v_trim_a_applied:
            # Legacy V2V scenes with old overlap processing — use chosen
            video_source = video_path
            logger.info(
                f"Scene {sc.order_index}: legacy V2V processed scene — using "
                f"chosen_video_path"
            )
        elif untrimmed_path and video_path:
            # Only use untrimmed if it belongs to the same generation as chosen.
            # The untrimmed path has "_untrimmed" inserted before the extension.
            # E.g. chosen = "abc_j1234_5678.mp4", untrimmed = "abc_j1234_5678_untrimmed.mp4"
            # Check: removing "_untrimmed" from untrimmed stem should match chosen stem.
            from pathlib import PurePosixPath
            chosen_stem = PurePosixPath(video_path).stem
            untrimmed_stem = PurePosixPath(untrimmed_path).stem
            stems_match = untrimmed_stem.replace("_untrimmed", "") == chosen_stem

            untrimmed_full = _resolve(untrimmed_path)
            if stems_match and Path(untrimmed_full).exists():
                video_source = untrimmed_path
                logger.info(
                    f"Scene {sc.order_index}: using untrimmed source for "
                    f"clean boundary frames (normalize_clip handles trim)"
                )
            else:
                if not stems_match:
                    logger.info(
                        f"Scene {sc.order_index}: untrimmed path belongs to a "
                        f"different generation — using chosen_video_path"
                    )
                else:
                    logger.info(
                        f"Scene {sc.order_index}: untrimmed file not found, "
                        f"using chosen_video_path"
                    )

        # Resolve AI transition clip path (if any) for this scene → next scene
        transition_clip = sc_params.get("transition_clip_path")
        transition_clip_resolved = _resolve(transition_clip) if transition_clip else None
        if transition_clip_resolved and not Path(transition_clip_resolved).exists():
            logger.warning(f"Scene {sc.order_index}: transition clip not found: {transition_clip_resolved}")
            transition_clip_resolved = None

        # Validate resolved paths exist before adding to scene_dicts
        resolved_video = _resolve(video_source) if video_source else None
        resolved_image = _resolve(image_path) if image_path else None
        if resolved_video and not Path(resolved_video).exists():
            logger.warning(
                f"Scene {sc.order_index}: video file not found: {resolved_video} — "
                f"falling back to image if available"
            )
            resolved_video = None
        if resolved_image and not Path(resolved_image).exists():
            logger.warning(
                f"Scene {sc.order_index}: image file not found: {resolved_image} — skipping"
            )
            resolved_image = None

        # Respect scene_source_type: if set to 'video', prefer video; otherwise prefer image.
        # IMPORTANT: When source_type is 'video' but the video file is missing,
        # do NOT silently fall back to a still image — that creates jarring
        # single-frame flashes in the export.  Skip the scene instead.
        if source_type == "video" and resolved_video:
            scene_dicts.append({
                "video_path": resolved_video,
                "duration": sc.end_time - sc.start_time,
                "scene_source_type": "video",
                "trim_first_frame": trim_first_frame,
                "is_v2v": is_v2v,
                "transition_clip_path": transition_clip_resolved,
            })
        elif source_type == "video" and not resolved_video:
            # Video source type but video file is missing — skip entirely.
            # Do NOT fall back to image, as that creates a jarring still-frame
            # flash in an otherwise all-video export.
            logger.warning(
                f"Scene {sc.order_index}: source_type is 'video' but no video "
                f"file found — skipping scene (not falling back to image)"
            )
        elif source_type == "image" and resolved_image:
            # Determine movement effect from image_movement parameters
            movement = sc_params.get("image_movement") or {}
            effect = movement.get("effect", "none") if movement else "none"
            scene_dicts.append({
                "image_path": resolved_image,
                "duration": sc.end_time - sc.start_time,
                "scene_source_type": "image",
                "effect": effect if effect != "none" else "zoom_in_center",
                "transition_clip_path": transition_clip_resolved,
            })
        elif resolved_video:
            # Fallback: source_type is image but only video exists
            scene_dicts.append({
                "video_path": resolved_video,
                "duration": sc.end_time - sc.start_time,
                "scene_source_type": "video",
                "trim_first_frame": trim_first_frame,
                "is_v2v": is_v2v,
                "transition_clip_path": transition_clip_resolved,
            })
        elif resolved_image:
            # Last resort: source_type not explicitly set and only image exists
            movement = sc_params.get("image_movement") or {}
            effect = movement.get("effect", "none") if movement else "none"
            scene_dicts.append({
                "image_path": resolved_image,
                "duration": sc.end_time - sc.start_time,
                "scene_source_type": "image",
                "effect": effect if effect != "none" else "zoom_in_center",
                "transition_clip_path": transition_clip_resolved,
            })
        # else: scene has no content yet — skip it

    if not scene_dicts:
        raise ValueError("No generated content found for any scene. Generate images/videos first.")

    master_audio = ""
    if audio_asset:
        master_audio = str(project_dir / audio_asset.rel_path)

    return scene_dicts, master_audio


async def _read_lfff_trim_setting(session: AsyncSession) -> bool:
    """Read the LFFF scene trim enabled setting from DB."""
    stmt = select(AppSettings).where(AppSettings.id == 1)
    result = await session.execute(stmt)
    settings = result.scalars().first()
    if settings and hasattr(settings, 'export_lfff_trim_enabled'):
        return settings.export_lfff_trim_enabled
    return True  # default enabled


async def _read_transition_settings(session: AsyncSession) -> tuple[str, float, bool]:
    """Read transition settings from AppSettings.

    Returns:
        (transition_type, transition_duration, color_match_clips)
    """
    settings_obj = await session.get(AppSettings, 1)
    if settings_obj:
        return (
            getattr(settings_obj, "export_transition_type", "none") or "none",
            getattr(settings_obj, "export_transition_duration", 0.5) or 0.5,
            getattr(settings_obj, "export_color_match_clips", True),
        )
    return ("none", 0.5, True)


# ── In-memory progress tracking ────────────────────────────────────
# Lightweight tracking for export + preview progress.

_export_progress: dict[str, dict] = {}   # project_id → {status, step, percent, path, error}
_preview_jobs: dict[str, dict] = {}      # project_id → {status, path, error}


# ── Endpoints ────────────────────────────────────────────────────────

@router.post(
    "",
    response_model=JobResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Export project to video",
)
async def export_project(
    project_id: UUID,
    req: ExportRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> JobResponse:
    """Assemble and export final video from all scenes and assets."""
    try:
        project = await _get_project_or_404(project_id, session)

        # Resolve dimensions from project settings if not specified
        proj_settings = project.settings or {}
        export_w = req.width if req.width > 0 else proj_settings.get("resolution_width", 1536)
        export_h = req.height if req.height > 0 else proj_settings.get("resolution_height", 864)
        export_fps = req.fps if req.fps > 0 else proj_settings.get("project_fps", 24)

        logger.info(
            f"Export resolution: {export_w}x{export_h}@{export_fps}fps "
            f"(from {'request' if req.width > 0 else 'project settings'})"
        )

        job = Job(
            project_id=project_id,
            job_type=JobType.VIDEO,
            status=JobStatus.PENDING,
            parameters={
                "operation": "export",
                "output_format": req.output_format,
                "width": export_w,
                "height": export_h,
                "fps": export_fps,
                "quality": req.quality,
                "transition_type": req.transition_type,
                "transition_duration": req.transition_duration,
            },
        )
        session.add(job)
        await session.commit()
        await session.refresh(job)

        pid_str = str(project_id)
        job_id_str = str(job.id)

        # Initialize in-memory progress
        _export_progress[pid_str] = {
            "job_id": job_id_str,
            "status": "running",
            "step": "Preparing scenes...",
            "percent": 0,
            "path": None,
            "download_url": None,
            "error": None,
        }

        logger.info(
            f"Created export job {job.id} for project {project_id} "
            f"({req.width}x{req.height} {req.output_format})"
        )

        # Map quality setting to CRF (lower = better quality, larger file)
        quality_to_crf = {
            "lossless": 0,
            "highest": 10,
            "high": 16,
            "medium": 20,
            "low": 26,
        }
        final_crf = quality_to_crf.get(req.quality, 16)  # default to "high"
        logger.info(f"Export quality '{req.quality}' → CRF {final_crf}")

        from backend.database import async_session

        async def run_export(
            job_id: UUID, pid: UUID, project_mode: str, export_params: dict,
        ):
            pid_s = str(pid)
            try:
                async with async_session() as bg_session:
                    export_job = await bg_session.get(Job, job_id)
                    if export_job:
                        export_job.status = JobStatus.RUNNING
                        export_job.started_at = datetime.utcnow()
                        await bg_session.commit()

                    _export_progress[pid_s]["step"] = "Loading scene data..."
                    _export_progress[pid_s]["percent"] = 5

                    lfff_trim = await _read_lfff_trim_setting(bg_session)
                    scene_dicts, master_audio = await _build_scene_dicts(bg_session, pid, lfff_trim_enabled=lfff_trim)
                    transition_type, transition_dur, color_match = await _read_transition_settings(bg_session)

                    # Apply per-export overrides if provided in request
                    if export_params.get("transition_type") is not None:
                        transition_type = export_params["transition_type"]
                    if export_params.get("transition_duration") is not None:
                        transition_dur = export_params["transition_duration"]
                    if export_params.get("color_match_clips") is not None:
                        color_match = export_params["color_match_clips"]

                    total_scenes = len(scene_dicts)
                    _export_progress[pid_s]["step"] = f"Preparing {total_scenes} scenes..."
                    _export_progress[pid_s]["percent"] = 10

                    project_dir = app_settings.project_dir / str(pid)
                    output_dir = project_dir / "exports"
                    output_dir.mkdir(parents=True, exist_ok=True)
                    output_ext = export_params.get("output_format", "mp4")
                    output_path = str(output_dir / f"export_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.{output_ext}")

                    from backend.services.video.assembly import assemble_music_video, assemble_narration_video

                    # Progress callback for the assembly pipeline
                    def on_progress(step: str, percent: int) -> None:
                        # Map assembly percent (0-100) to our 10-95 range
                        mapped = 10 + int(percent * 0.85)
                        _export_progress[pid_s]["step"] = step
                        _export_progress[pid_s]["percent"] = min(mapped, 95)

                    exp_w = export_params.get("width", 1536)
                    exp_h = export_params.get("height", 864)
                    exp_fps = export_params.get("fps", 24)

                    logger.info(f"Assembly params: {exp_w}x{exp_h}@{exp_fps}fps")

                    exp_crf = export_params.get("final_crf", 16)

                    if project_mode in ("narration_images", "narration_video"):
                        await asyncio.to_thread(
                            assemble_narration_video,
                            scene_dicts, master_audio, output_path,
                            exp_w, exp_h, exp_fps,
                            progress_callback=on_progress,
                        )
                    else:
                        await asyncio.to_thread(
                            assemble_music_video,
                            scene_dicts, master_audio, output_path,
                            exp_w, exp_h, exp_fps,
                            default_transition=transition_type,
                            default_transition_duration=transition_dur,
                            color_match_clips=color_match,
                            progress_callback=on_progress,
                            final_crf=exp_crf,
                        )

                    # Compute relative path for download URL
                    rel_path = f"{pid}/exports/{Path(output_path).name}"
                    download_url = f"/api/files/{rel_path}"

                    export_job = await bg_session.get(Job, job_id)
                    if export_job:
                        export_job.status = JobStatus.DONE
                        export_job.result = {"output_path": output_path, "download_url": download_url}
                        export_job.completed_at = datetime.utcnow()
                        await bg_session.commit()

                    _export_progress[pid_s] = {
                        "job_id": str(job_id),
                        "status": "done",
                        "step": "Export complete!",
                        "percent": 100,
                        "path": output_path,
                        "download_url": download_url,
                        "error": None,
                    }

                    logger.info(f"Export job {job_id} completed: {output_path}")

            except Exception as e:
                logger.error(f"Export job {job_id} failed: {e}")
                _export_progress[pid_s] = {
                    "job_id": str(job_id),
                    "status": "failed",
                    "step": "Export failed",
                    "percent": 0,
                    "path": None,
                    "download_url": None,
                    "error": str(e),
                }
                try:
                    async with async_session() as err_session:
                        export_job = await err_session.get(Job, job_id)
                        if export_job:
                            export_job.status = JobStatus.FAILED
                            export_job.error = str(e)
                            export_job.completed_at = datetime.utcnow()
                            await err_session.commit()
                except Exception as db_err:
                    logger.error(f"Failed to update export job status: {db_err}")

        asyncio.create_task(
            run_export(job.id, project_id, project.mode, {
                "output_format": req.output_format,
                "width": export_w, "height": export_h,
                "fps": export_fps, "quality": req.quality,
                "final_crf": final_crf,
                "transition_type": req.transition_type,
                "transition_duration": req.transition_duration,
            })
        )

        return JobResponse.model_validate(job)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating export job for project {project_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create export job",
        )


@router.post(
    "/preview",
    response_model=PreviewRenderResponse,
    summary="Render a preview of the assembled video",
)
async def render_preview(
    project_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> PreviewRenderResponse:
    """Render a quick preview of the assembled video with transitions and colour
    matching applied.  Uses 720p and fast encoding settings.

    The render runs in a background task.  Poll GET /export/preview to check
    status and retrieve the path once complete.
    """
    try:
        project = await _get_project_or_404(project_id, session)
        pid_str = str(project_id)

        # If already rendering, return current status
        existing = _preview_jobs.get(pid_str)
        if existing and existing.get("status") == "rendering":
            return PreviewRenderResponse(job_id=pid_str, status="rendering")

        # Mark as rendering
        _preview_jobs[pid_str] = {"status": "rendering", "path": None, "error": None}

        from backend.database import async_session

        async def _do_preview(pid: UUID, project_mode: str):
            pid_s = str(pid)
            try:
                async with async_session() as bg_session:
                    lfff_trim = await _read_lfff_trim_setting(bg_session)
                    scene_dicts, master_audio = await _build_scene_dicts(bg_session, pid, lfff_trim_enabled=lfff_trim)
                    transition_type, transition_dur, color_match = await _read_transition_settings(bg_session)
                    # Read project FPS from project settings
                    preview_project = await bg_session.get(Project, pid)
                    preview_fps = (preview_project.settings or {}).get("project_fps", 24) if preview_project else 24

                project_dir = app_settings.project_dir / str(pid)
                preview_dir = project_dir / "cache" / "previews"
                preview_dir.mkdir(parents=True, exist_ok=True)
                # Overwrite previous preview to save disk
                preview_path = str(preview_dir / "preview.mp4")

                from backend.services.video.assembly import assemble_music_video, assemble_narration_video

                if project_mode in ("narration_images", "narration_video"):
                    await asyncio.to_thread(
                        assemble_narration_video,
                        scene_dicts, master_audio, preview_path,
                        720,          # preview width
                        480,          # preview height
                        preview_fps,  # from project settings
                    )
                else:
                    await asyncio.to_thread(
                        assemble_music_video,
                        scene_dicts, master_audio, preview_path,
                        720,          # preview width
                        480,          # preview height
                        preview_fps,  # from project settings
                        default_transition=transition_type,
                        default_transition_duration=transition_dur,
                        color_match_clips=color_match,
                    )

                # Compute relative path for /api/files/ serving (needs project ID prefix)
                # Use forward slashes for URL compatibility on Windows
                rel_path = f"{pid}/cache/previews/preview.mp4"
                _preview_jobs[pid_s] = {
                    "status": "done",
                    "path": rel_path,
                    "error": None,
                }
                logger.info(f"Preview render complete for project {pid}: {preview_path}")

            except Exception as e:
                logger.error(f"Preview render failed for project {pid}: {e}")
                _preview_jobs[pid_s] = {
                    "status": "failed",
                    "path": None,
                    "error": str(e),
                }

        asyncio.create_task(_do_preview(project_id, project.mode))

        return PreviewRenderResponse(job_id=pid_str, status="rendering")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error starting preview render for project {project_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to start preview render",
        )


@router.get(
    "/preview",
    response_model=PreviewRenderResponse,
    summary="Get preview render status",
)
async def get_preview_status(
    project_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> PreviewRenderResponse:
    """Check the status of a preview render.

    Returns the preview video URL when complete.
    """
    await _get_project_or_404(project_id, session)
    pid_str = str(project_id)

    job_info = _preview_jobs.get(pid_str)
    if not job_info:
        return PreviewRenderResponse(job_id=pid_str, status="none")

    preview_url = None
    if job_info["status"] == "done" and job_info.get("path"):
        preview_url = f"/api/files/{job_info['path']}"

    return PreviewRenderResponse(
        job_id=pid_str,
        status=job_info["status"],
        preview_path=job_info.get("path"),
        preview_url=preview_url,
        error=job_info.get("error"),
    )


@router.get(
    "/status",
    response_model=ExportProgressResponse,
    summary="Get export progress",
)
async def get_export_status(
    project_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> ExportProgressResponse:
    """Get export progress for the project's current export job.

    Uses in-memory progress tracking for granular updates during assembly.
    """
    await _get_project_or_404(project_id, session)
    pid_str = str(project_id)

    # Check in-memory progress first (has granular steps)
    progress = _export_progress.get(pid_str)
    if progress:
        return ExportProgressResponse(
            job_id=progress.get("job_id", pid_str),
            status=progress["status"],
            progress_percent=progress["percent"],
            current_step=progress["step"],
            estimated_time_remaining=None,
            output_path=progress.get("path"),
            download_url=progress.get("download_url"),
            error=progress.get("error"),
        )

    # Fallback to DB if no in-memory record
    try:
        stmt = (
            select(Job)
            .where(Job.project_id == project_id)
            .where(Job.job_type == JobType.VIDEO)
            .order_by(Job.created_at.desc())
        )
        result = await session.execute(stmt)
        export_job = result.scalars().first()

        if not export_job:
            return ExportProgressResponse(
                job_id=pid_str,
                status="none",
                progress_percent=0,
                current_step="No export started",
            )

        progress_percent = 0
        current_step = "Pending"
        if export_job.status == JobStatus.RUNNING:
            progress_percent = 50
            current_step = "Rendering"
        elif export_job.status == JobStatus.DONE:
            progress_percent = 100
            current_step = "Complete"
        elif export_job.status == JobStatus.FAILED:
            progress_percent = 0
            current_step = "Failed"

        output_path = None
        download_url = None
        if export_job.result and export_job.result.get("output_path"):
            output_path = export_job.result["output_path"]
        if export_job.result and export_job.result.get("download_url"):
            download_url = export_job.result["download_url"]

        return ExportProgressResponse(
            job_id=str(export_job.id),
            status=export_job.status,
            progress_percent=progress_percent,
            current_step=current_step,
            estimated_time_remaining=None,
            output_path=output_path,
            download_url=download_url,
            error=export_job.error,
        )
    except Exception as e:
        logger.error(f"Error getting export status for project {project_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get export status",
        )
