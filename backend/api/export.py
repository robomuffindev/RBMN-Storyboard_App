"""Final video export endpoints for RBMN Storyboard App."""
import asyncio
import logging
import random
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
from backend.database.models import Project, Job, JobType, JobStatus, Scene, Asset, AssetType, AppSettings, Lyrics
from backend.utils.background import track as _track_task

logger = logging.getLogger(__name__)

ALL_KEN_BURNS_EFFECTS = [
    "zoom_in_center", "zoom_out_center",
    "zoom_in_top_left", "zoom_in_top_right", "zoom_in_bottom_left", "zoom_in_bottom_right",
    "pan_left", "pan_right", "pan_up", "pan_down",
    "pan_left_to_right", "pan_right_to_left",
    "zoom_in_pan_left", "zoom_in_pan_right", "zoom_out_pan_left", "zoom_out_pan_right",
]

CHUNK_SIZE = 25  # Number of scenes per assembly chunk

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
    # Subtitle burn-in options
    subtitles_enabled: bool = False
    subtitle_font: str = "Arial"
    subtitle_size: int = 24
    subtitle_color: str = "white"       # white, yellow, cyan, etc.
    subtitle_position: str = "bottom"   # bottom, top, center
    subtitle_outline: int = 2
    # Audio normalization
    normalize_audio: bool = False
    # Backing track mixer overrides (None = use project.settings defaults)
    backing_track_loop: Optional[bool] = None
    narration_volume: Optional[float] = None
    backing_volume: Optional[float] = None
    backing_main_fade_in: Optional[float] = None
    backing_main_fade_out: Optional[float] = None
    normalize_backing: Optional[bool] = None
    # Ken Burns override (None = use project.settings defaults)
    random_ken_burns: Optional[bool] = None
    ken_burns_allowed_effects: Optional[List[str]] = None
    # ── Re-export controls ──
    # audio_only_remix: skip clip rendering + chunk merge, reuse the cached
    # concatenated video from the previous successful export. Use this when
    # you only changed audio mix params (volumes, fades, normalize, etc.).
    # Requires a previous successful export with matching video params.
    audio_only_remix: bool = False
    # force_recreate: wipe the export cache before starting. Use this when
    # you want a guaranteed fresh render.
    force_recreate: bool = False
    # export_stems: ALSO produce per-channel WAVs in {output_dir}/stems/
    # so you can mix outside the app (DAW workflow).
    export_stems: bool = False
    # stems_only: SKIP all video rendering entirely and only produce the
    # audio stems. Use this when you already have the exported video and
    # just need the stems (or want to grab them later).  Outputs:
    #   stems/narration.wav, stems/backing_mix.wav, stems/backing_NN_name.wav
    stems_only: bool = False


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
    chunks: Optional[List[Dict[str, Any]]] = None  # Per-chunk status array
    total_chunks: Optional[int] = None
    stems: Optional[List[Dict[str, Any]]] = None  # Per-stem download list (stems-only / stems export)
    current_chunk: Optional[int] = None
    phase: Optional[str] = None  # "clips", "chunks", "final", "post"


class ExportFileInfo(BaseModel):
    """Info about a single export file."""
    filename: str
    size_mb: float
    created_at: str  # ISO format
    download_url: str


class ExportJobResponse(BaseModel):
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
    override_random_kb: Optional[bool] = None,
    override_kb_allowed: Optional[List[str]] = None,
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

    # Random Ken Burns settings — request overrides take priority over project.settings
    project = await session.get(Project, project_id)
    proj_settings = project.settings or {} if project else {}
    random_kb = override_random_kb if override_random_kb is not None else proj_settings.get("random_ken_burns", False)
    kb_allowed_raw = override_kb_allowed if override_kb_allowed is not None else proj_settings.get("ken_burns_allowed_effects", [])
    kb_pool = kb_allowed_raw if kb_allowed_raw else ALL_KEN_BURNS_EFFECTS

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

        # Per-scene transition_in / transition_out from the Scene Editor.
        # These reach the assembler so they can be honored when the global
        # export-transition override is set to "none" (i.e. defer to per-scene).
        # Frontend stores these as { type: "<name>", duration: <sec> } in
        # scene.parameters; anything missing / "none" reads as no transition.
        _t_in_raw = sc_params.get("transition_in")
        _t_out_raw = sc_params.get("transition_out")
        scene_transition_in = _t_in_raw if isinstance(_t_in_raw, dict) and _t_in_raw.get("type") and _t_in_raw.get("type") != "none" else None
        scene_transition_out = _t_out_raw if isinstance(_t_out_raw, dict) and _t_out_raw.get("type") and _t_out_raw.get("type") != "none" else None

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
                "transition_in": scene_transition_in,
                "transition_out": scene_transition_out,
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
            # Random Ken Burns: pick a random effect if enabled and no manual effect set
            movement_dict = {}
            if effect and effect != "none":
                movement_dict = movement  # user's manual choice — preserve fully
            elif random_kb:
                chosen = random.choice(kb_pool)
                movement_dict = {"effect": chosen, "intensity": random.randint(30, 70), "easing": "ease_in_out"}
                effect = chosen
            scene_dicts.append({
                "image_path": resolved_image,
                "duration": sc.end_time - sc.start_time,
                "scene_source_type": "image",
                "effect": effect if effect != "none" else "zoom_in_center",
                "image_movement": movement_dict,
                "transition_clip_path": transition_clip_resolved,
                "transition_in": scene_transition_in,
                "transition_out": scene_transition_out,
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
                "transition_in": scene_transition_in,
                "transition_out": scene_transition_out,
            })
        elif resolved_image:
            # Last resort: source_type not explicitly set and only image exists
            movement = sc_params.get("image_movement") or {}
            effect = movement.get("effect", "none") if movement else "none"
            # Random Ken Burns: pick a random effect if enabled and no manual effect set
            movement_dict = {}
            if effect and effect != "none":
                movement_dict = movement  # user's manual choice — preserve fully
            elif random_kb:
                chosen = random.choice(kb_pool)
                movement_dict = {"effect": chosen, "intensity": random.randint(30, 70), "easing": "ease_in_out"}
                effect = chosen
            scene_dicts.append({
                "image_path": resolved_image,
                "duration": sc.end_time - sc.start_time,
                "scene_source_type": "image",
                "effect": effect if effect != "none" else "zoom_in_center",
                "image_movement": movement_dict,
                "transition_clip_path": transition_clip_resolved,
                "transition_in": scene_transition_in,
                "transition_out": scene_transition_out,
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
_export_tasks: dict[str, asyncio.Task] = {}  # project_id → asyncio.Task
_cancel_flag: dict[str, bool] = {}       # project_id → cancel requested
_preview_jobs: dict[str, dict] = {}      # project_id → {status, path, error}

_PROGRESS_TTL_SECONDS = 600  # evict terminal progress entries after 10 minutes


async def _schedule_progress_eviction(pid_key: str, delay: float = _PROGRESS_TTL_SECONDS):
    """Remove a terminal _export_progress entry after a delay to prevent memory leak."""
    try:
        await asyncio.sleep(delay)
        entry = _export_progress.get(pid_key)
        if entry and entry.get("status") in ("done", "failed", "cancelled"):
            _export_progress.pop(pid_key, None)
            logger.debug(f"Evicted stale export progress for {pid_key}")
    except asyncio.CancelledError:
        pass




# ── Module-level export task ──────────────────────────────────────────

async def _run_export_task(
    job_id: UUID, pid: UUID, project_mode: str, export_params: dict, pid_s: str,
):
    """Run the export assembly pipeline as a background task.

    Extracted to module level so it can be called from both export_project()
    and resume_export().
    """
    from backend.database import async_session

    # Chunk callback for per-chunk progress reporting
    def on_chunk_complete(chunk_idx: int, chunk_path: str, scene_start: int, scene_end: int):
        import os as _os
        import json as _cjson
        rel_chunk = f"{pid_s}/exports/{Path(chunk_path).name}"
        chunk_info = {
            "index": chunk_idx,
            "status": "done",
            "path": chunk_path,
            "download_url": f"/api/files/{rel_chunk}",
            "scenes": f"{scene_start + 1}-{scene_end + 1}",
            "size_mb": round(_os.path.getsize(chunk_path) / (1024 * 1024), 1) if _os.path.exists(chunk_path) else 0,
        }
        chunks = _export_progress[pid_s].get("chunks", [])
        # Update or append
        existing = next((c for c in chunks if c["index"] == chunk_idx), None)
        if existing:
            existing.update(chunk_info)
        else:
            chunks.append(chunk_info)
        _export_progress[pid_s]["chunks"] = chunks
        _export_progress[pid_s]["current_chunk"] = chunk_idx + 1

        # ── Incremental manifest save ──────────────────────────────────
        # Write manifest after EVERY chunk so crash recovery can pick up
        # where we left off, even without a clean shutdown.
        try:
            _manifest_dir = app_settings.project_dir / pid_s / "exports"
            _manifest_dir.mkdir(parents=True, exist_ok=True)
            _inc_manifest = {
                "job_id": str(job_id),
                "project_id": pid_s,
                "params": export_params,
                "output_path": None,  # not yet known
                "chunks": list(chunks),
                "total_chunks": _export_progress[pid_s].get("total_chunks", 0),
                "status": "in_progress",
                "updated_at": datetime.utcnow().isoformat(),
            }
            (_manifest_dir / "export_manifest.json").write_text(
                _cjson.dumps(_inc_manifest, indent=2, default=str)
            )
            logger.debug(f"Incremental manifest saved after chunk {chunk_idx}")
        except Exception as _me:
            logger.warning(f"Failed to write incremental manifest: {_me}")

    # Cancel check callable
    def is_cancelled():
        return _cancel_flag.get(pid_s, False)

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
            scene_dicts, master_audio = await _build_scene_dicts(
                bg_session, pid, lfff_trim_enabled=lfff_trim,
                override_random_kb=export_params.get("random_ken_burns"),
                override_kb_allowed=export_params.get("ken_burns_allowed_effects"),
            )
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
            _export_progress[pid_s]["phase"] = "clips"
            _export_progress[pid_s]["total_chunks"] = (total_scenes + CHUNK_SIZE - 1) // CHUNK_SIZE

            project_dir = app_settings.project_dir / str(pid)
            output_dir = project_dir / "exports"
            output_dir.mkdir(parents=True, exist_ok=True)

            # Clean up stale clip/chunk files from previous exports to prevent
            # the checkpoint logic from silently reusing outdated clips
            for stale in list(output_dir.glob("clip_*.mp4")) + list(output_dir.glob("chunk_*.mp4")):
                try:
                    stale.unlink()
                    logger.debug(f"Removed stale export artifact: {stale.name}")
                except OSError as e:
                    logger.warning(f"Could not remove stale file {stale.name}: {e}")

            output_ext = export_params.get("output_format", "mp4")
            output_path = str(output_dir / f"export_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.{output_ext}")
            _export_progress[pid_s]["output_path"] = output_path

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

            _export_progress[pid_s]["phase"] = "assembly"

            if project_mode in ("narration_images", "narration_video"):
                # Load backing tracks for narration projects
                from backend.database.models import BackingTrack
                bt_stmt = (
                    select(BackingTrack)
                    .where(BackingTrack.project_id == pid)
                    .order_by(BackingTrack.order_index)
                )
                bt_result = await bg_session.execute(bt_stmt)
                bt_rows = bt_result.scalars().all()
                narr_backing_tracks = None
                if bt_rows:
                    narr_backing_tracks = []
                    for bt in bt_rows:
                        bt_path = str(app_settings.project_dir / str(pid) / bt.rel_path)
                        narr_backing_tracks.append({
                            "path": bt_path,
                            "start_time": bt.start_time,
                            "end_time": bt.end_time,
                            "trim_start": bt.trim_start,
                            "trim_end": bt.trim_end,
                            "volume_db": bt.volume_db,
                            "fade_in_sec": bt.fade_in_sec,
                            "fade_out_sec": bt.fade_out_sec,
                        })

                # Build subtitle style if enabled
                narr_subtitle_words = None
                narr_subtitle_style = None
                if export_params.get("subtitles_enabled"):
                    lyrics_row_narr = (
                        await bg_session.execute(
                            select(Lyrics).where(Lyrics.project_id == pid)
                        )
                    ).scalar_one_or_none()
                    if lyrics_row_narr and lyrics_row_narr.words:
                        narr_subtitle_words = lyrics_row_narr.words
                        pos_map_narr = {"bottom": 2, "top": 8, "center": 5}
                        color_map_narr = {
                            "white": "&H00FFFFFF",
                            "yellow": "&H0000FFFF",
                            "cyan": "&H00FFFF00",
                            "green": "&H0000FF00",
                            "red": "&H000000FF",
                        }

                        def _hex_to_ass(hex_color: str) -> str:
                            """Convert CSS hex color (#RRGGBB) to ASS color (&H00BBGGRR)."""
                            h = hex_color.lstrip("#")
                            if len(h) == 6:
                                r, g, b = h[0:2], h[2:4], h[4:6]
                                return f"&H00{b.upper()}{g.upper()}{r.upper()}"
                            return "&H00FFFFFF"

                        raw_color = export_params.get("subtitle_color", "white")
                        if raw_color.startswith("#"):
                            ass_color = _hex_to_ass(raw_color)
                        else:
                            ass_color = color_map_narr.get(raw_color, "&H00FFFFFF")

                        narr_subtitle_style = {
                            "font_name": export_params.get("subtitle_font", "Arial"),
                            "font_size": export_params.get("subtitle_size", 24),
                            "primary_color": ass_color,
                            "outline_width": export_params.get("subtitle_outline", 2),
                            "alignment": pos_map_narr.get(
                                export_params.get("subtitle_position", "bottom"), 2
                            ),
                            "bold": export_params.get("subtitle_bold", False),
                        }

                # Read backing track mix settings from project.settings
                # Need to reload project for settings access
                _project_obj = await bg_session.get(Project, pid)
                _proj_settings = _project_obj.settings or {} if _project_obj else {}
                _bt_loop = bool(export_params.get("backing_track_loop", _proj_settings.get("backing_track_loop", False)))
                _narr_vol = float(export_params.get("narration_volume", _proj_settings.get("narration_volume", 1.0)))
                _back_vol = float(export_params.get("backing_volume", _proj_settings.get("backing_volume", 1.0)))
                _main_fi = float(export_params.get("backing_main_fade_in", _proj_settings.get("backing_main_fade_in", 0.0)))
                _main_fo = float(export_params.get("backing_main_fade_out", _proj_settings.get("backing_main_fade_out", 0.0)))
                _norm_bt = bool(export_params.get("normalize_backing", _proj_settings.get("normalize_backing", False)))

                # ── Stems-only short-circuit ─────────────────────────────
                # Skip the entire video pipeline; just produce the audio
                # stems from the resolved narration + backing tracks.
                if bool(export_params.get("stems_only")):
                    from backend.services.video.assembly import _export_audio_stems
                    _export_progress[pid_s]["step"] = "Exporting audio stems..."
                    _export_progress[pid_s]["percent"] = 30
                    _total_dur_for_stems = 0.0
                    try:
                        from backend.services.video.ffmpeg import get_media_info as _gmi
                        _total_dur_for_stems = _gmi(master_audio).get("duration", 0.0)
                    except Exception:
                        pass
                    await asyncio.to_thread(
                        _export_audio_stems,
                        output_path,
                        master_audio,
                        narr_backing_tracks,
                        _narr_vol,
                        _back_vol,
                        _bt_loop,
                        _main_fi,
                        _main_fo,
                        _norm_bt,
                        _total_dur_for_stems,
                        # individual_backing=False — user wants ONE backing
                        # file (the combined backing_mix), not one WAV per
                        # source track.  Looping (when enabled) is applied
                        # inside the function so backing_mix matches the
                        # narration length.
                        False,
                    )
                    # Collect the written stem files for the status response.
                    stems_dir = Path(output_path).parent / "stems"
                    stems_list: list[dict] = []
                    if stems_dir.exists():
                        for sp in sorted(stems_dir.glob("*.wav")):
                            try:
                                size_mb = round(sp.stat().st_size / (1024 * 1024), 2)
                            except Exception:
                                size_mb = 0.0
                            rel = sp.relative_to(app_settings.project_dir).as_posix()
                            stems_list.append({
                                "filename": sp.name,
                                "size_mb": size_mb,
                                "download_url": f"/api/files/{rel}",
                            })
                    # Mark the Job row as DONE so the export gallery sees the run
                    _stems_dir_rel = stems_dir.relative_to(app_settings.project_dir).as_posix() if stems_dir.exists() else None
                    export_job = await bg_session.get(Job, job_id)
                    if export_job:
                        export_job.status = JobStatus.DONE
                        export_job.result = {
                            "stems_only": True,
                            "stems_dir": str(stems_dir),
                            "stems": stems_list,
                        }
                        export_job.completed_at = datetime.utcnow()
                        await bg_session.commit()
                    # Flip _export_progress to 'done' so the frontend transitions
                    # out of the "Exporting..." view.  download_url points at
                    # the stems folder via the file API for compatibility with
                    # existing code paths; the new `stems` field is the list of
                    # individual WAV files for direct link rendering.
                    _export_progress[pid_s] = {
                        "job_id": str(job_id),
                        "status": "done",
                        "step": "Stems exported",
                        "percent": 100,
                        "path": str(stems_dir),
                        "download_url": f"/api/files/{_stems_dir_rel}/" if _stems_dir_rel else None,
                        "error": None,
                        "chunks": [],
                        "total_chunks": 0,
                        "current_chunk": 0,
                        "phase": "done",
                        "stems": stems_list,
                    }
                    logger.info(
                        f"Stems-only export complete: {len(stems_list)} files in {stems_dir}"
                    )
                    return

                await asyncio.to_thread(
                    assemble_narration_video,
                    scene_dicts, master_audio, output_path,
                    exp_w, exp_h, exp_fps,
                    default_transition=transition_type,
                    default_transition_duration=transition_dur,
                    color_match_clips=color_match,
                    progress_callback=on_progress,
                    final_crf=exp_crf,
                    backing_tracks=narr_backing_tracks,
                    subtitle_words=narr_subtitle_words,
                    subtitle_style=narr_subtitle_style,
                    normalize_audio_enabled=bool(export_params.get("normalize_audio")),
                    loop_backing=_bt_loop,
                    narration_volume=_narr_vol,
                    backing_volume=_back_vol,
                    main_fade_in=_main_fi,
                    main_fade_out=_main_fo,
                    normalize_backing=_norm_bt,
                    chunk_size=CHUNK_SIZE,
                    chunk_callback=on_chunk_complete,
                    cancel_check=is_cancelled,
                    # Re-export controls
                    audio_only_remix=bool(export_params.get("audio_only_remix")),
                    force_recreate=bool(export_params.get("force_recreate")),
                    export_stems=bool(export_params.get("export_stems")),
                )
            else:
                # ── Stems-only short-circuit for music mode ──
                # Music mode has no backing tracks; we just emit the
                # master audio as the "narration" stem.
                if bool(export_params.get("stems_only")):
                    from backend.services.video.assembly import _export_audio_stems
                    _export_progress[pid_s]["step"] = "Exporting audio stems..."
                    _export_progress[pid_s]["percent"] = 30
                    _total_dur_for_stems = 0.0
                    try:
                        from backend.services.video.ffmpeg import get_media_info as _gmi
                        _total_dur_for_stems = _gmi(master_audio).get("duration", 0.0)
                    except Exception:
                        pass
                    await asyncio.to_thread(
                        _export_audio_stems,
                        output_path,
                        master_audio,
                        None,
                        1.0,
                        1.0,
                        False,
                        0.0,
                        0.0,
                        False,
                        _total_dur_for_stems,
                        False,
                    )
                    stems_dir = Path(output_path).parent / "stems"
                    stems_list: list[dict] = []
                    if stems_dir.exists():
                        for sp in sorted(stems_dir.glob("*.wav")):
                            try:
                                size_mb = round(sp.stat().st_size / (1024 * 1024), 2)
                            except Exception:
                                size_mb = 0.0
                            rel = sp.relative_to(app_settings.project_dir).as_posix()
                            stems_list.append({
                                "filename": sp.name,
                                "size_mb": size_mb,
                                "download_url": f"/api/files/{rel}",
                            })
                    _stems_dir_rel = stems_dir.relative_to(app_settings.project_dir).as_posix() if stems_dir.exists() else None
                    export_job = await bg_session.get(Job, job_id)
                    if export_job:
                        export_job.status = JobStatus.DONE
                        export_job.result = {
                            "stems_only": True,
                            "stems_dir": str(stems_dir),
                            "stems": stems_list,
                        }
                        export_job.completed_at = datetime.utcnow()
                        await bg_session.commit()
                    _export_progress[pid_s] = {
                        "job_id": str(job_id),
                        "status": "done",
                        "step": "Stems exported",
                        "percent": 100,
                        "path": str(stems_dir),
                        "download_url": f"/api/files/{_stems_dir_rel}/" if _stems_dir_rel else None,
                        "error": None,
                        "chunks": [],
                        "total_chunks": 0,
                        "current_chunk": 0,
                        "phase": "done",
                        "stems": stems_list,
                    }
                    logger.info(
                        f"Stems-only export complete: {len(stems_list)} files in {stems_dir}"
                    )
                    return

                await asyncio.to_thread(
                    assemble_music_video,
                    scene_dicts, master_audio, output_path,
                    exp_w, exp_h, exp_fps,
                    default_transition=transition_type,
                    default_transition_duration=transition_dur,
                    color_match_clips=color_match,
                    progress_callback=on_progress,
                    final_crf=exp_crf,
                    chunk_size=CHUNK_SIZE,
                    chunk_callback=on_chunk_complete,
                    cancel_check=is_cancelled,
                )

            _export_progress[pid_s]["phase"] = "post"

            # ── Post-assembly: subtitle burn-in ──────────────
            # For narration modes, subtitles are handled inside
            # assemble_narration_video, so skip the post-assembly step.
            if (
                export_params.get("subtitles_enabled")
                and project_mode not in ("narration_images", "narration_video")
            ):
                _export_progress[pid_s]["step"] = "Burning subtitles..."
                _export_progress[pid_s]["percent"] = 96

                # Load word-level timestamps from Lyrics table
                lyrics_row = (
                    await bg_session.execute(
                        select(Lyrics).where(Lyrics.project_id == pid)
                    )
                ).scalar_one_or_none()

                if lyrics_row and lyrics_row.words:
                    from backend.services.video.ffmpeg import (
                        generate_ass_subtitles,
                        burn_subtitles,
                    )

                    # Map subtitle_position to ASS alignment
                    pos_map = {"bottom": 2, "top": 8, "center": 5}
                    alignment = pos_map.get(
                        export_params.get("subtitle_position", "bottom"), 2
                    )

                    # Map color names to ASS AABBGGRR format
                    color_map = {
                        "white": "&H00FFFFFF",
                        "yellow": "&H0000FFFF",
                        "cyan": "&H00FFFF00",
                        "green": "&H0000FF00",
                        "red": "&H000000FF",
                    }
                    sub_color = export_params.get("subtitle_color", "white")
                    if sub_color.startswith("#"):
                        # Convert CSS hex (#RRGGBB) to ASS (&H00BBGGRR)
                        h = sub_color.lstrip("#")
                        primary_color = f"&H00{h[4:6].upper()}{h[2:4].upper()}{h[0:2].upper()}" if len(h) == 6 else "&H00FFFFFF"
                    else:
                        primary_color = color_map.get(sub_color, "&H00FFFFFF")

                    style_opts = {
                        "font_name": export_params.get("subtitle_font", "Arial"),
                        "font_size": export_params.get("subtitle_size", 24),
                        "primary_color": primary_color,
                        "outline_width": export_params.get("subtitle_outline", 2),
                        "alignment": alignment,
                    }

                    tmp_ass = str(Path(output_path).parent / "subtitles.ass")
                    sub_output = str(
                        Path(output_path).parent
                        / f"sub_{Path(output_path).name}"
                    )

                    await asyncio.to_thread(
                        generate_ass_subtitles,
                        lyrics_row.words,
                        tmp_ass,
                        style_opts,
                    )
                    await asyncio.to_thread(
                        burn_subtitles, output_path, tmp_ass, sub_output
                    )

                    # Replace original with subtitled version
                    import shutil
                    shutil.move(sub_output, output_path)
                    try:
                        Path(tmp_ass).unlink(missing_ok=True)
                    except Exception:
                        pass

                    logger.info("Subtitles burned into export")
                else:
                    logger.warning(
                        "Subtitles enabled but no word timestamps found"
                    )

            # ── Post-assembly: audio normalization ────────────
            # For narration modes, normalization is handled inside
            # assemble_narration_video, so skip the post-assembly step.
            if (
                export_params.get("normalize_audio")
                and project_mode not in ("narration_images", "narration_video")
            ):
                _export_progress[pid_s]["step"] = "Normalizing audio..."
                _export_progress[pid_s]["percent"] = 98

                from backend.services.video.ffmpeg import (
                    normalize_audio as ffmpeg_normalize_audio,
                )

                norm_output = str(
                    Path(output_path).parent
                    / f"norm_{Path(output_path).name}"
                )
                await asyncio.to_thread(
                    ffmpeg_normalize_audio, output_path, norm_output
                )

                import shutil
                shutil.move(norm_output, output_path)
                logger.info("Audio normalization applied to export")

            # Compute relative path for download URL
            rel_path = f"{pid_s}/exports/{Path(output_path).name}"
            download_url = f"/api/files/{rel_path}"

            # Save export manifest for potential resume
            import json as _json
            manifest = {
                "job_id": str(job_id),
                "project_id": pid_s,
                "params": export_params,
                "output_path": output_path,
                "chunks": _export_progress[pid_s].get("chunks", []),
                "total_chunks": _export_progress[pid_s].get("total_chunks", 0),
                "status": "completed",
                "completed_at": datetime.utcnow().isoformat(),
            }
            manifest_path = output_dir / "export_manifest.json"
            manifest_path.write_text(_json.dumps(manifest, indent=2, default=str))

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
                "chunks": _export_progress.get(pid_s, {}).get("chunks", []),
                "total_chunks": _export_progress.get(pid_s, {}).get("total_chunks", 0),
                "current_chunk": _export_progress.get(pid_s, {}).get("current_chunk", 0),
                "phase": "done",
            }

            logger.info(f"Export job {job_id} completed: {output_path}")

    except asyncio.CancelledError:
        logger.info(f"Export job {job_id} was cancelled")
        _export_progress[pid_s] = {
            "job_id": str(job_id),
            "status": "cancelled",
            "step": "Export cancelled",
            "percent": _export_progress.get(pid_s, {}).get("percent", 0),
            "path": None,
            "download_url": None,
            "error": "Export was cancelled by user",
            "chunks": _export_progress.get(pid_s, {}).get("chunks", []),
            "total_chunks": _export_progress.get(pid_s, {}).get("total_chunks", 0),
            "current_chunk": _export_progress.get(pid_s, {}).get("current_chunk", 0),
            "phase": "cancelled",
        }
        # Update DB
        try:
            async with async_session() as err_session:
                export_job = await err_session.get(Job, job_id)
                if export_job:
                    export_job.status = JobStatus.FAILED
                    export_job.error = "Cancelled by user"
                    export_job.completed_at = datetime.utcnow()
                    await err_session.commit()
        except Exception as db_err:
            logger.error(f"Failed to update cancelled export job: {db_err}")

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
            "chunks": _export_progress.get(pid_s, {}).get("chunks", []),
            "total_chunks": _export_progress.get(pid_s, {}).get("total_chunks", 0),
            "current_chunk": _export_progress.get(pid_s, {}).get("current_chunk", 0),
            "phase": "failed",
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

    finally:
        _export_tasks.pop(pid_s, None)
        _cancel_flag.pop(pid_s, None)
        # Schedule eviction of terminal progress entry to prevent memory leak
        _track_task(_schedule_progress_eviction(pid_s))


# ── Endpoints ────────────────────────────────────────────────────────

@router.post(
    "",
    response_model=ExportJobResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Export project to video",
)
async def export_project(
    project_id: UUID,
    req: ExportRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> ExportJobResponse:
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
            "chunks": [],
            "total_chunks": 0,
            "current_chunk": 0,
            "phase": "preparing",
        }

        logger.info(
            f"Created export job {job.id} for project {project_id} "
            f"({req.width}x{req.height} {req.output_format})"
        )

        # Map quality setting to CRF (lower = better quality, larger file)
        # Frontend sends: "draft", "standard", "high"
        quality_to_crf = {
            "high": 16,
            "standard": 20,
            "draft": 26,
        }
        final_crf = quality_to_crf.get(req.quality, 20)  # default to "standard"
        logger.info(f"Export quality '{req.quality}' → CRF {final_crf}")

        # Clear cancel flag for fresh export
        _cancel_flag.pop(pid_str, None)

        task = asyncio.create_task(
            _run_export_task(job.id, project_id, project.mode, {
                "output_format": req.output_format,
                "width": export_w, "height": export_h,
                "fps": export_fps, "quality": req.quality,
                "final_crf": final_crf,
                "transition_type": req.transition_type,
                "transition_duration": req.transition_duration,
                "color_match_clips": req.color_match_clips,
                "subtitles_enabled": req.subtitles_enabled,
                "subtitle_font": req.subtitle_font,
                "subtitle_size": req.subtitle_size,
                "subtitle_color": req.subtitle_color,
                "subtitle_position": req.subtitle_position,
                "subtitle_outline": req.subtitle_outline,
                "normalize_audio": req.normalize_audio,
                "backing_track_loop": req.backing_track_loop,
                "audio_only_remix": req.audio_only_remix,
                "force_recreate": req.force_recreate,
                "export_stems": req.export_stems,
                "stems_only": req.stems_only,
                "narration_volume": req.narration_volume,
                "backing_volume": req.backing_volume,
                "backing_main_fade_in": req.backing_main_fade_in,
                "backing_main_fade_out": req.backing_main_fade_out,
                "normalize_backing": req.normalize_backing,
                "random_ken_burns": req.random_ken_burns,
                "ken_burns_allowed_effects": req.ken_burns_allowed_effects,
            }, pid_str)
        )
        _export_tasks[pid_str] = task

        return ExportJobResponse.model_validate(job)
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
                        default_transition=transition_type,
                        default_transition_duration=transition_dur,
                        color_match_clips=color_match,
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

        _track_task(_do_preview(project_id, project.mode))

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
            chunks=progress.get("chunks"),
            total_chunks=progress.get("total_chunks"),
            current_chunk=progress.get("current_chunk"),
            phase=progress.get("phase"),
            stems=progress.get("stems"),
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
        stems_list = None
        if export_job.result:
            if export_job.result.get("output_path"):
                output_path = export_job.result["output_path"]
            if export_job.result.get("download_url"):
                download_url = export_job.result["download_url"]
            # Stems-only / stems-export Jobs persist the per-file list in result["stems"]
            if export_job.result.get("stems"):
                stems_list = export_job.result["stems"]

        return ExportProgressResponse(
            job_id=str(export_job.id),
            status=export_job.status,
            progress_percent=progress_percent,
            current_step=current_step,
            estimated_time_remaining=None,
            output_path=output_path,
            download_url=download_url,
            error=export_job.error,
            stems=stems_list,
        )
    except Exception as e:
        logger.error(f"Error getting export status for project {project_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get export status",
        )


@router.post(
    "/cancel",
    summary="Cancel a running export",
)
async def cancel_export(
    project_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Cancel a running export job."""
    await _get_project_or_404(project_id, session)
    pid_str = str(project_id)

    # Set cancel flag (checked by assembly between chunks)
    _cancel_flag[pid_str] = True

    # Also try to cancel the asyncio task directly
    task = _export_tasks.get(pid_str)
    if task and not task.done():
        task.cancel()
        return {"status": "cancelling", "message": "Export cancellation requested"}

    progress = _export_progress.get(pid_str)
    if progress and progress.get("status") == "running":
        return {"status": "cancelling", "message": "Export cancellation requested"}

    return {"status": "not_running", "message": "No export is currently running"}


@router.post(
    "/resume",
    response_model=ExportJobResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Resume a failed export",
)
async def resume_export(
    project_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> ExportJobResponse:
    """Resume a previously failed export from where it left off.

    Uses the export manifest and existing clip/chunk files to skip
    already-completed work.
    """
    project = await _get_project_or_404(project_id, session)
    pid_str = str(project_id)

    # Check for existing manifest
    project_dir = app_settings.project_dir / pid_str
    output_dir = project_dir / "exports"
    manifest_path = output_dir / "export_manifest.json"

    if not manifest_path.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No export manifest found. Start a new export instead.",
        )

    import json as _json
    manifest = _json.loads(manifest_path.read_text())
    export_params = manifest.get("params", {})

    # Create new job for the resume
    job = Job(
        project_id=project_id,
        job_type=JobType.VIDEO,
        status=JobStatus.PENDING,
        parameters={
            "operation": "export_resume",
            "original_job_id": manifest.get("job_id"),
            **{k: v for k, v in export_params.items() if k in ("width", "height", "fps", "quality")},
        },
    )
    session.add(job)
    await session.commit()
    await session.refresh(job)

    job_id_str = str(job.id)

    # Clear cancel flag
    _cancel_flag.pop(pid_str, None)

    # Map quality to CRF if not already present
    if "final_crf" not in export_params:
        quality_to_crf = {"lossless": 0, "highest": 10, "high": 16, "medium": 20, "low": 26}
        export_params["final_crf"] = quality_to_crf.get(export_params.get("quality", "high"), 16)

    # Initialize progress with chunk data from manifest
    _export_progress[pid_str] = {
        "job_id": job_id_str,
        "status": "running",
        "step": "Resuming export...",
        "percent": 5,
        "path": None,
        "download_url": None,
        "error": None,
        "chunks": manifest.get("chunks", []),
        "total_chunks": manifest.get("total_chunks", 0),
        "current_chunk": len(manifest.get("chunks", [])),
        "phase": "resuming",
    }

    # Start a fresh export with the same params — assembly.py's clip
    # checkpointing will handle the resume by skipping existing clips
    task = asyncio.create_task(
        _run_export_task(job.id, project_id, project.mode, export_params, pid_str)
    )
    _export_tasks[pid_str] = task

    return ExportJobResponse.model_validate(job)


# ── Crash Recovery ─────────────────────────────────────────────────

class ExportScanResult(BaseModel):
    """Result of scanning the exports directory for recoverable artifacts."""
    has_manifest: bool = False
    manifest_status: Optional[str] = None  # "completed", "in_progress", None
    manifest_params: Optional[Dict[str, Any]] = None
    clip_count: int = 0
    chunk_count: int = 0
    chunks: List[Dict[str, Any]] = []
    total_clips_size_mb: float = 0.0
    total_chunks_size_mb: float = 0.0
    recoverable: bool = False
    export_running: bool = False
    last_updated: Optional[str] = None


def _scan_export_dir(project_id: UUID) -> ExportScanResult:
    """Scan the project's exports/ directory for recoverable artifacts."""
    import json as _json
    import os as _os

    pid_str = str(project_id)
    result = ExportScanResult()

    # Check if export is currently running
    if pid_str in _export_tasks and not _export_tasks[pid_str].done():
        result.export_running = True
        return result

    project_dir = app_settings.project_dir / pid_str
    output_dir = project_dir / "exports"

    if not output_dir.exists():
        return result

    # Check for manifest
    manifest_path = output_dir / "export_manifest.json"
    if manifest_path.exists():
        try:
            manifest = _json.loads(manifest_path.read_text())
            result.has_manifest = True
            result.manifest_status = manifest.get("status")
            result.manifest_params = manifest.get("params")
            result.last_updated = manifest.get("updated_at") or manifest.get("completed_at")
            # Include chunk info from manifest
            for c in manifest.get("chunks", []):
                if Path(c.get("path", "")).exists():
                    result.chunks.append(c)
        except Exception as e:
            logger.warning(f"Failed to read export manifest: {e}")

    # Scan for clip files
    clips_total_bytes = 0
    for f in sorted(output_dir.glob("clip_*.mp4")):
        if f.stat().st_size > 0:
            result.clip_count += 1
            clips_total_bytes += f.stat().st_size
    result.total_clips_size_mb = round(clips_total_bytes / (1024 * 1024), 1)

    # Scan for chunk files (on disk, not just manifest)
    chunks_dir_files = sorted(output_dir.glob("chunk_*.mp4"))
    chunks_total_bytes = 0
    for f in chunks_dir_files:
        if f.stat().st_size > 0:
            result.chunk_count += 1
            chunks_total_bytes += f.stat().st_size
            # If chunk not already in manifest data, add it from disk
            chunk_idx = int(f.stem.split("_")[1])
            if not any(c.get("index") == chunk_idx for c in result.chunks):
                rel_chunk = f"{pid_str}/exports/{f.name}"
                result.chunks.append({
                    "index": chunk_idx,
                    "status": "done",
                    "path": str(f),
                    "download_url": f"/api/files/{rel_chunk}",
                    "scenes": f"chunk {chunk_idx + 1}",
                    "size_mb": round(f.stat().st_size / (1024 * 1024), 1),
                })
    result.total_chunks_size_mb = round(chunks_total_bytes / (1024 * 1024), 1)

    # Sort chunks by index
    result.chunks.sort(key=lambda c: c.get("index", 0))

    # Determine if recoverable: must have clips or chunks AND either
    # a manifest (to know params) or be a simple restart
    result.recoverable = (
        (result.clip_count > 0 or result.chunk_count > 0)
        and result.manifest_status != "completed"
    )

    return result


@router.get(
    "/scan",
    response_model=ExportScanResult,
    summary="Scan for recoverable export artifacts",
)
async def scan_export(
    project_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> ExportScanResult:
    """Check if there are recoverable export artifacts on disk.

    This is a lightweight read-only scan that checks for:
    - Existing clip files (clip_*.mp4)
    - Existing chunk files (chunk_*.mp4)
    - Partial or complete export manifest

    Use this to decide whether to show the "Recover Last Export" button.
    """
    await _get_project_or_404(project_id, session)
    return _scan_export_dir(project_id)


@router.post(
    "/recover",
    response_model=ExportJobResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Recover and resume a crashed export",
)
async def recover_export(
    project_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> ExportJobResponse:
    """Recover from a crash by scanning the exports directory.

    Unlike /resume which requires a completed manifest, this endpoint:
    1. Scans the disk for any clip_*.mp4 and chunk_*.mp4 files
    2. Reads the partial manifest if available (written after each chunk)
    3. Rebuilds the export progress state from what's on disk
    4. Starts a new export task that leverages existing clips (checkpointing)

    Existing clips are reused automatically — the assembly pipeline checks
    for clip_*.mp4 files and skips re-rendering them.
    """
    project = await _get_project_or_404(project_id, session)
    pid_str = str(project_id)

    # Don't allow if already running
    if pid_str in _export_tasks and not _export_tasks[pid_str].done():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An export is already running for this project.",
        )

    scan = _scan_export_dir(project_id)

    if not scan.recoverable and not scan.has_manifest:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No recoverable export artifacts found. Start a new export instead.",
        )

    # Get export params from manifest or use defaults
    if scan.manifest_params:
        export_params = dict(scan.manifest_params)
    else:
        # No manifest — use project defaults. The clips on disk will still
        # be reused via checkpointing, but we need params for the new export.
        proj_settings = project.settings or {}
        export_params = {
            "output_format": "mp4",
            "width": proj_settings.get("resolution_width", 1536),
            "height": proj_settings.get("resolution_height", 864),
            "fps": proj_settings.get("video_fps", 24),
            "quality": "high",
            "final_crf": 16,
            "transition_type": "crossfade",
            "transition_duration": 0.5,
            "color_match_clips": True,
            "subtitles_enabled": False,
            "normalize_audio": False,
        }
        logger.info("No manifest found — using project defaults for recovered export")

    # Map quality to CRF if not already present
    if "final_crf" not in export_params:
        quality_to_crf = {"lossless": 0, "highest": 10, "high": 16, "medium": 20, "low": 26}
        export_params["final_crf"] = quality_to_crf.get(export_params.get("quality", "high"), 16)

    # Create new job
    job = Job(
        project_id=project_id,
        job_type=JobType.VIDEO,
        status=JobStatus.PENDING,
        parameters={
            "operation": "export_recover",
            "recovered_clips": scan.clip_count,
            "recovered_chunks": scan.chunk_count,
            "had_manifest": scan.has_manifest,
            **{k: v for k, v in export_params.items() if k in ("width", "height", "fps", "quality")},
        },
    )
    session.add(job)
    await session.commit()
    await session.refresh(job)

    job_id_str = str(job.id)

    # Clear cancel flag
    _cancel_flag.pop(pid_str, None)

    # Initialize progress with recovered chunk data
    _export_progress[pid_str] = {
        "job_id": job_id_str,
        "status": "running",
        "step": f"Recovering export ({scan.clip_count} clips, {scan.chunk_count} chunks found)...",
        "percent": 5,
        "path": None,
        "download_url": None,
        "error": None,
        "chunks": [dict(c) for c in scan.chunks],
        "total_chunks": 0,  # Will be computed once scene count is known
        "current_chunk": len(scan.chunks),
        "phase": "recovering",
    }

    logger.info(
        f"Recovering export for project {project_id}: "
        f"{scan.clip_count} clips, {scan.chunk_count} chunks, "
        f"manifest={'yes' if scan.has_manifest else 'no'}"
    )

    # Start export — clip checkpointing will skip existing clips
    task = asyncio.create_task(
        _run_export_task(job.id, project_id, project.mode, export_params, pid_str)
    )
    _export_tasks[pid_str] = task

    return ExportJobResponse.model_validate(job)


# ── Export Gallery ────────────────────────────────────────────────────

@router.get(
    "/gallery",
    response_model=List[ExportFileInfo],
    summary="List export files for this project",
)
async def list_exports(
    project_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> List[ExportFileInfo]:
    """List all completed export files in the project's exports directory.

    Excludes any file that is currently being written by an active export job
    to prevent users from playing/downloading partial files.
    """
    await _get_project_or_404(project_id, session)
    pid_str = str(project_id)
    exports_dir = app_settings.project_dir / pid_str / "exports"

    if not exports_dir.exists():
        return []

    # Determine if there's an active export writing to this directory
    active_export_path: Optional[str] = None
    progress = _export_progress.get(pid_str)
    if progress and progress.get("status") in ("running", "pending", "assembly"):
        # The output_path is stored in progress after assembly starts
        active_export_path = progress.get("output_path")

    results: List[ExportFileInfo] = []
    for f in sorted(exports_dir.glob("export_*.*"), key=lambda p: p.stat().st_mtime, reverse=True):
        if f.suffix.lower() in (".mp4", ".mkv", ".webm") and f.stat().st_size > 0:
            # Skip the file currently being written by an active export
            if active_export_path and str(f.resolve()) == str(Path(active_export_path).resolve()):
                continue
            stat = f.stat()
            results.append(ExportFileInfo(
                filename=f.name,
                size_mb=round(stat.st_size / (1024 * 1024), 1),
                created_at=datetime.utcfromtimestamp(stat.st_mtime).isoformat(),
                download_url=f"/api/files/{pid_str}/exports/{f.name}",
            ))
    return results


@router.delete(
    "/gallery/{filename}",
    summary="Delete a specific export file",
)
async def delete_export(
    project_id: UUID,
    filename: str,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Delete a specific export file from the project's exports directory."""
    await _get_project_or_404(project_id, session)
    pid_str = str(project_id)
    exports_dir = app_settings.project_dir / pid_str / "exports"
    target = exports_dir / filename

    # Security: ensure filename doesn't escape exports directory
    try:
        target.resolve().relative_to(exports_dir.resolve())
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid filename")

    if not target.exists():
        raise HTTPException(status_code=404, detail="Export file not found")

    target.unlink()
    logger.info(f"Deleted export file: {target}")
    return {"status": "deleted", "filename": filename}
