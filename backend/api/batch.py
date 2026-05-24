"""Batch mode endpoints for RBMN Storyboard App.

Orchestrates creating multiple projects from audio files and running
the full auto-generation pipeline on each one sequentially.
"""
import asyncio
import hashlib
import json
import logging
import os
import shutil
from pathlib import Path
from typing import Optional
from uuid import UUID, uuid4

import httpx
from fastapi import APIRouter, File, HTTPException, UploadFile, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from backend.config import settings
from backend.database import async_session
from backend.database.models import (
    AppSettings,
    Asset,
    AssetType,
    Lyrics,
    Project,
    ProjectMode,
    Scene,
    SongSection,
    SongSectionLabel,
    StemSelection,
)
from backend.utils.file_utils import ensure_project_dirs, sha256_file

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/batch", tags=["batch"])

# ── Module-level state ────────────────────────────────────────────────
_batch_runs: dict[str, dict] = {}

# Base URL for internal API calls
_INTERNAL_BASE_URL = f"http://{settings.app_host}:{settings.app_port}"


# ── Pydantic models ──────────────────────────────────────────────────

class BatchItemConfig(BaseModel):
    """Configuration for a single batch item."""
    audio_filename: str  # filename of uploaded audio
    audio_upload_path: str  # temp path from upload endpoint
    lyrics_text: str = ""
    project_name: str = ""  # auto-derived from filename if empty
    concept_direction: str = ""
    style_text: str = ""
    render_type: str = "music_video"  # music_video | narration_video
    video_mode: str = "i2v"  # i2v | v2v
    two_pass: bool = True
    use_story_flow: bool = True


class BatchRunRequest(BaseModel):
    """Request to start a batch run."""
    items: list[BatchItemConfig]


class BatchItemStatus(BaseModel):
    """Status of a single batch item."""
    index: int
    project_name: str
    project_id: str | None = None
    status: str  # pending | running | done | failed
    current_step: str = ""
    error: str | None = None


class BatchRunStatus(BaseModel):
    """Status of the entire batch run."""
    batch_id: str
    status: str  # idle | running | done | failed | cancelled
    total_items: int
    completed_items: int
    current_item_index: int = -1
    items: list[BatchItemStatus]


# ── Helper: build BatchRunStatus from internal state ─────────────────

def _build_status(batch_id: str) -> BatchRunStatus:
    """Build a BatchRunStatus from the module-level state dict."""
    run = _batch_runs.get(batch_id)
    if not run:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Batch run {batch_id} not found",
        )

    items_status = []
    completed = 0
    for item_state in run["items"]:
        items_status.append(BatchItemStatus(
            index=item_state["index"],
            project_name=item_state["project_name"],
            project_id=item_state.get("project_id"),
            status=item_state["status"],
            current_step=item_state.get("current_step", ""),
            error=item_state.get("error"),
        ))
        if item_state["status"] in ("done", "failed"):
            completed += 1

    return BatchRunStatus(
        batch_id=batch_id,
        status=run["status"],
        total_items=len(run["items"]),
        completed_items=completed,
        current_item_index=run.get("current_item_index", -1),
        items=items_status,
    )


# ── Endpoints ─────────────────────────────────────────────────────────

@router.post("/upload-audio")
async def upload_batch_audio(file: UploadFile = File(...)):
    """Upload an audio file to the batch staging directory.

    Returns the temporary path and original filename so the frontend
    can reference it when submitting the batch run.
    """
    if not file.filename:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No filename provided",
        )

    staging_dir = settings.project_dir / "_batch_staging" / str(uuid4())
    staging_dir.mkdir(parents=True, exist_ok=True)

    dest_path = staging_dir / file.filename
    content = await file.read()
    with open(dest_path, "wb") as f:
        f.write(content)

    logger.info(f"Batch audio staged: {dest_path} ({len(content)} bytes)")

    return {
        "upload_path": str(dest_path),
        "filename": file.filename,
    }


@router.post("/run", response_model=BatchRunStatus)
async def start_batch_run(req: BatchRunRequest):
    """Start a batch run that processes each item through the full pipeline.

    Creates a background task and returns immediately with the batch ID
    so the frontend can poll for progress.
    """
    if not req.items:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No items provided",
        )

    # Validate all upload paths exist
    for i, item in enumerate(req.items):
        upload_path = Path(item.audio_upload_path)
        if not upload_path.exists():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Item {i}: upload path does not exist: {item.audio_upload_path}",
            )

    batch_id = uuid4().hex

    # Derive project names from filenames where not provided
    items_state = []
    for i, item in enumerate(req.items):
        name = item.project_name.strip()
        if not name:
            # Strip extension to derive project name
            name = Path(item.audio_filename).stem
            # Clean up common suffixes
            for suffix in ("_master", "_final", "_mixed"):
                if name.lower().endswith(suffix):
                    name = name[: -len(suffix)]
            name = name.replace("_", " ").replace("-", " ").strip().title()
            if not name:
                name = f"Batch Project {i + 1}"

        items_state.append({
            "index": i,
            "project_name": name,
            "project_id": None,
            "status": "pending",
            "current_step": "",
            "error": None,
            "config": item.model_dump(),
        })

    _batch_runs[batch_id] = {
        "status": "running",
        "current_item_index": -1,
        "items": items_state,
        "task": None,
    }

    # Launch the pipeline as a background task
    task = asyncio.create_task(_run_batch_pipeline(batch_id))
    _batch_runs[batch_id]["task"] = task

    logger.info(f"Batch run {batch_id} started with {len(req.items)} items")
    return _build_status(batch_id)


@router.get("/{batch_id}/status", response_model=BatchRunStatus)
async def get_batch_status(batch_id: str):
    """Get the current status of a batch run."""
    return _build_status(batch_id)


@router.post("/{batch_id}/cancel")
async def cancel_batch_run(batch_id: str):
    """Cancel a running batch run.

    Sets the status to 'cancelled' so the pipeline loop stops
    after the current item finishes.
    """
    run = _batch_runs.get(batch_id)
    if not run:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Batch run {batch_id} not found",
        )

    if run["status"] != "running":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Batch run is not running (status: {run['status']})",
        )

    run["status"] = "cancelled"
    logger.info(f"Batch run {batch_id} cancellation requested")

    return {"batch_id": batch_id, "status": "cancelled"}


@router.delete("/staging")
async def cleanup_staging():
    """Clean up any leftover batch staging files."""
    staging_base = settings.project_dir / "_batch_staging"
    if staging_base.exists():
        shutil.rmtree(staging_base, ignore_errors=True)
        logger.info("Batch staging directory cleaned up")
        return {"cleaned": True}
    return {"cleaned": False, "message": "No staging directory found"}


# ── Pipeline ──────────────────────────────────────────────────────────

async def _run_batch_pipeline(batch_id: str) -> None:
    """Process each batch item sequentially through the full pipeline.

    Steps per item:
        1. Create project
        2. Copy audio to project
        3. Audio analysis (Whisper + stems + sections)
        4. Suggest timeline
        5. Generate concept from lyrics
        6. Generate video flow
        7. Auto-gen images
        8. Auto-gen videos
    """
    run = _batch_runs.get(batch_id)
    if not run:
        logger.error(f"Batch run {batch_id} not found in state")
        return

    base_url = _INTERNAL_BASE_URL

    try:
        for i, item_state in enumerate(run["items"]):
            # ── Cancellation check ────────────────────────────────
            if run["status"] == "cancelled":
                logger.info(f"Batch {batch_id} cancelled before item {i}")
                for remaining in run["items"][i:]:
                    if remaining["status"] == "pending":
                        remaining["status"] = "failed"
                        remaining["error"] = "Batch cancelled"
                break

            run["current_item_index"] = i
            item_state["status"] = "running"
            config = BatchItemConfig(**item_state["config"])

            try:
                await _process_single_item(
                    batch_id=batch_id,
                    item_index=i,
                    item_state=item_state,
                    config=config,
                    base_url=base_url,
                )
                item_state["status"] = "done"
                item_state["current_step"] = "completed"
                logger.info(
                    f"Batch {batch_id} item {i} ({item_state['project_name']}) completed"
                )

            except Exception as exc:
                logger.exception(
                    f"Batch {batch_id} item {i} ({item_state['project_name']}) failed: {exc}"
                )
                item_state["status"] = "failed"
                item_state["error"] = str(exc)
                item_state["current_step"] = f"failed: {exc}"

            # Clean up staging file for this item
            try:
                staging_path = Path(config.audio_upload_path)
                if staging_path.exists():
                    staging_path.unlink()
                # Remove parent dir if empty
                parent = staging_path.parent
                if parent.exists() and not any(parent.iterdir()):
                    parent.rmdir()
            except Exception as cleanup_err:
                logger.warning(f"Staging cleanup error for item {i}: {cleanup_err}")

        # ── Final status ──────────────────────────────────────────
        if run["status"] == "cancelled":
            pass  # already set
        elif all(s["status"] == "done" for s in run["items"]):
            run["status"] = "done"
        elif any(s["status"] == "failed" for s in run["items"]):
            # If some succeeded and some failed, mark the batch as done
            # (individual items have their own status)
            run["status"] = "done"
        else:
            run["status"] = "done"

        logger.info(f"Batch run {batch_id} finished with status: {run['status']}")

    except Exception as fatal:
        logger.exception(f"Batch {batch_id} fatal error: {fatal}")
        run["status"] = "failed"

    # Final staging cleanup
    try:
        staging_base = settings.project_dir / "_batch_staging"
        if staging_base.exists():
            # Only remove if empty
            remaining = list(staging_base.iterdir())
            if not remaining:
                staging_base.rmdir()
    except Exception:
        pass


async def _process_single_item(
    batch_id: str,
    item_index: int,
    item_state: dict,
    config: BatchItemConfig,
    base_url: str,
) -> None:
    """Process a single batch item through all pipeline steps."""

    run = _batch_runs[batch_id]

    def _update_step(step: str) -> None:
        item_state["current_step"] = step
        logger.info(f"Batch {batch_id} item {item_index}: {step}")

    def _check_cancelled() -> None:
        if run["status"] == "cancelled":
            raise RuntimeError("Batch cancelled")

    # ── Step 1: Create project ────────────────────────────────────
    _update_step("creating project")
    _check_cancelled()

    async with async_session() as session:
        project = Project(
            name=item_state["project_name"],
            mode=ProjectMode(config.render_type),
        )
        session.add(project)
        await session.flush()

        project_id = project.id
        project_path = settings.project_dir / str(project_id)
        project_path.mkdir(parents=True, exist_ok=True)
        ensure_project_dirs(project_path)

        await session.commit()

    item_state["project_id"] = str(project_id)
    logger.info(f"Batch item {item_index}: created project {project_id}")

    # ── Step 2: Copy audio to project ─────────────────────────────
    _update_step("copying audio")
    _check_cancelled()

    src_audio = Path(config.audio_upload_path)
    if not src_audio.exists():
        raise FileNotFoundError(f"Audio file not found: {src_audio}")

    audio_dest_dir = project_path / "assets" / "audio"
    audio_dest_dir.mkdir(parents=True, exist_ok=True)
    audio_dest = audio_dest_dir / config.audio_filename
    shutil.copy2(str(src_audio), str(audio_dest))

    # Compute hash and create Asset record
    file_hash = sha256_file(audio_dest)
    file_size = os.path.getsize(audio_dest)

    async with async_session() as session:
        audio_asset = Asset(
            project_id=project_id,
            filename=config.audio_filename,
            rel_path=f"assets/audio/{config.audio_filename}",
            asset_type=AssetType.MUSIC,
            sha256=file_hash,
            file_size=file_size,
        )
        session.add(audio_asset)
        await session.flush()
        audio_asset_id = audio_asset.id
        await session.commit()

    logger.info(f"Batch item {item_index}: audio copied as asset {audio_asset_id}")

    # ── Step 3: Audio analysis ────────────────────────────────────
    _update_step("analyzing audio (Whisper + stems + sections)")
    _check_cancelled()

    async with async_session() as session:
        # Get Whisper settings from AppSettings
        settings_stmt = select(AppSettings).where(AppSettings.id == 1)
        settings_result = await session.execute(settings_stmt)
        app_settings = settings_result.scalars().first()

        whisper_mode = app_settings.whisper_mode if app_settings else "local"
        whisper_remote_url = app_settings.whisper_remote_url if app_settings else None
        whisper_comfyui_url = app_settings.whisper_comfyui_url if app_settings else None
        whisper_model = app_settings.whisper_model if app_settings else "large-v2"
        whisper_language = app_settings.whisper_language if app_settings else "English"

    # Run audio analysis pipeline
    from backend.services.audio.analysis import AudioAnalyzer

    analyzer = AudioAnalyzer(
        cache_dir=str(project_path / "cache" / "audio_analysis")
    )
    initial_text = config.lyrics_text.strip() or ""
    analysis_result = await asyncio.to_thread(
        analyzer.analyze_full,
        str(audio_dest),
        whisper_mode,
        whisper_remote_url,
        whisper_comfyui_url=whisper_comfyui_url,
        initial_text=initial_text,
        whisper_model=whisper_model,
        whisper_language=whisper_language,
    )

    # Save analysis results to cache
    cache_dir = project_path / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)

    # Create Asset records for generated stems
    async with async_session() as session:
        stems_dir = project_path / "assets" / "stems"
        stem_names = ["vocals", "drums", "bass", "other"]
        for stem_name in stem_names:
            stem_path = stems_dir / f"{stem_name}.wav"
            if stem_path.exists():
                stem_size = os.path.getsize(stem_path)
                stem_hash = sha256_file(stem_path)
                stem_asset = Asset(
                    project_id=project_id,
                    filename=f"{stem_name}.wav",
                    rel_path=f"assets/stems/{stem_name}.wav",
                    asset_type=AssetType.MUSIC,
                    sha256=stem_hash,
                    file_size=stem_size,
                    meta={"stem": stem_name, "source_audio": config.audio_filename},
                )
                session.add(stem_asset)

        # Delete existing sections
        existing_sections_stmt = select(SongSection).where(
            SongSection.project_id == project_id
        )
        existing_sections_result = await session.execute(existing_sections_stmt)
        for old_section in existing_sections_result.scalars().all():
            await session.delete(old_section)

        # Color palette for sections
        section_colors = {
            "intro": "#6366f1",
            "verse": "#3b82f6",
            "chorus": "#f59e0b",
            "bridge": "#8b5cf6",
            "outro": "#64748b",
            "pre-chorus": "#06b6d4",
            "post-chorus": "#f97316",
            "hook": "#ef4444",
            "interlude": "#14b8a6",
            "break": "#a3a3a3",
        }

        # Save sections to database
        for section_data in analysis_result.get("sections", []):
            label_str = section_data.get("label", "other")
            try:
                label_enum = SongSectionLabel(label_str.lower())
            except ValueError:
                label_enum = SongSectionLabel.OTHER

            start = section_data.get("start", section_data.get("start_time", 0.0))
            end = section_data.get("end", section_data.get("end_time", 0.0))
            color = section_data.get(
                "color", section_colors.get(label_str.lower(), "#FFFFFF")
            )

            song_section = SongSection(
                project_id=project_id,
                label=label_enum,
                start_time=start,
                end_time=end,
                color=color,
            )
            session.add(song_section)

        await session.commit()

    # Save lyrics to database
    transcription_words = analysis_result.get("transcription", [])
    lyrics_text = ""
    if transcription_words:
        lyrics_text = " ".join(w.get("word", "") for w in transcription_words)

    if not lyrics_text and initial_text:
        lyrics_text = initial_text

    async with async_session() as session:
        try:
            # Upsert lyrics
            existing_lyrics_stmt = select(Lyrics).where(
                Lyrics.project_id == project_id
            )
            existing_lyrics_result = await session.execute(existing_lyrics_stmt)
            existing_lyrics = existing_lyrics_result.scalars().first()
            if existing_lyrics:
                await session.delete(existing_lyrics)
                await session.flush()

            lyrics_record = Lyrics(
                project_id=project_id,
                full_text=lyrics_text,
                initial_text=initial_text,
                words=transcription_words,
            )
            session.add(lyrics_record)
            await session.commit()
        except Exception as lyrics_err:
            logger.warning(f"Lyrics save failed: {lyrics_err}")
            await session.rollback()
            try:
                existing_lyrics_stmt = select(Lyrics).where(
                    Lyrics.project_id == project_id
                )
                existing_lyrics_result = await session.execute(existing_lyrics_stmt)
                existing_lyrics = existing_lyrics_result.scalars().first()
                if existing_lyrics:
                    await session.delete(existing_lyrics)
                    await session.flush()
                lyrics_record = Lyrics(
                    project_id=project_id,
                    full_text=lyrics_text,
                    words=transcription_words,
                )
                session.add(lyrics_record)
                await session.commit()
            except Exception as e2:
                logger.warning(f"Lyrics save retry also failed: {e2}")
                await session.rollback()

    # Cache lyrics to file
    lyrics_data = {"text": lyrics_text, "words": transcription_words}
    lyrics_cache_path = cache_dir / "lyrics.json"
    with open(lyrics_cache_path, "w") as f:
        json.dump(lyrics_data, f)

    logger.info(f"Batch item {item_index}: audio analysis complete")

    # ── Step 4: Suggest timeline ──────────────────────────────────
    _update_step("suggesting timeline")
    _check_cancelled()

    async with httpx.AsyncClient(base_url=base_url, timeout=300) as client:
        resp = await client.post(
            f"/api/projects/{project_id}/timeline/suggest-timeline",
        )
        if resp.status_code != 200:
            logger.warning(
                f"Suggest timeline returned {resp.status_code}: {resp.text}"
            )
            # Non-fatal — continue even if suggestion fails

    logger.info(f"Batch item {item_index}: timeline suggested")

    # ── Step 5: Generate concept from lyrics ──────────────────────
    _update_step("generating concept from lyrics")
    _check_cancelled()

    async with httpx.AsyncClient(base_url=base_url, timeout=120) as client:
        resp = await client.post(
            f"/api/projects/{project_id}/concept/base-on-lyrics",
            json={},
        )
        if resp.status_code != 200:
            logger.warning(
                f"Base-on-lyrics returned {resp.status_code}: {resp.text}"
            )

    # If concept_direction or style_text provided, update project settings
    if config.concept_direction or config.style_text:
        async with async_session() as session:
            stmt = select(Project).where(Project.id == project_id)
            result = await session.execute(stmt)
            project = result.scalars().first()
            if project:
                proj_settings = dict(project.settings) if project.settings else {}
                if config.concept_direction:
                    proj_settings["concept_direction"] = config.concept_direction
                if config.style_text:
                    proj_settings["style_text"] = config.style_text
                project.settings = proj_settings
                await session.commit()

    logger.info(f"Batch item {item_index}: concept generated")

    # ── Step 6: Generate video flow ───────────────────────────────
    _update_step("generating video flow")
    _check_cancelled()

    async with httpx.AsyncClient(base_url=base_url, timeout=120) as client:
        resp = await client.post(
            f"/api/projects/{project_id}/concept/video-flow",
        )
        if resp.status_code != 200:
            logger.warning(
                f"Video flow returned {resp.status_code}: {resp.text}"
            )

    logger.info(f"Batch item {item_index}: video flow generated")

    # ── Step 7: Auto-gen images ───────────────────────────────────
    _update_step("generating images")
    _check_cancelled()

    auto_gen_mode = "missing_images_independent"  # parallel image gen
    async with httpx.AsyncClient(base_url=base_url, timeout=30) as client:
        resp = await client.post(
            f"/api/projects/{project_id}/generate/auto-sequential",
            json={
                "mode": auto_gen_mode,
                "two_pass": config.two_pass,
                "use_story_flow": config.use_story_flow,
            },
        )
        if resp.status_code != 200:
            logger.warning(
                f"Auto-gen images start returned {resp.status_code}: {resp.text}"
            )
            # Try to continue — might already be running

    # Poll until done
    async with httpx.AsyncClient(base_url=base_url, timeout=30) as client:
        while True:
            _check_cancelled()
            try:
                status_resp = await client.get(
                    f"/api/projects/{project_id}/generate/auto-sequential/status",
                )
                if status_resp.status_code == 200:
                    data = status_resp.json()
                    gen_status = data.get("status", "unknown")
                    if gen_status in ("done", "failed", "cancelled", "idle"):
                        break
                    step_detail = data.get("current_step", "")
                    _update_step(f"generating images: {step_detail}")
                else:
                    logger.warning(
                        f"Image gen status check returned {status_resp.status_code}"
                    )
            except Exception as poll_err:
                logger.warning(f"Image gen poll error: {poll_err}")

            await asyncio.sleep(3)

    logger.info(f"Batch item {item_index}: images generated")

    # ── Step 8: Auto-gen videos ───────────────────────────────────
    _update_step("generating videos")
    _check_cancelled()

    video_mode_map = {
        "i2v": "all_video_single",
        "v2v": "all_video_v2v",
    }
    video_gen_mode = video_mode_map.get(config.video_mode, "all_video_single")

    async with httpx.AsyncClient(base_url=base_url, timeout=30) as client:
        resp = await client.post(
            f"/api/projects/{project_id}/generate/auto-sequential",
            json={
                "mode": video_gen_mode,
                "use_story_flow": config.use_story_flow,
            },
        )
        if resp.status_code != 200:
            logger.warning(
                f"Auto-gen videos start returned {resp.status_code}: {resp.text}"
            )

    # Poll until done
    async with httpx.AsyncClient(base_url=base_url, timeout=30) as client:
        while True:
            _check_cancelled()
            try:
                status_resp = await client.get(
                    f"/api/projects/{project_id}/generate/auto-sequential/status",
                )
                if status_resp.status_code == 200:
                    data = status_resp.json()
                    gen_status = data.get("status", "unknown")
                    if gen_status in ("done", "failed", "cancelled", "idle"):
                        break
                    step_detail = data.get("current_step", "")
                    _update_step(f"generating videos: {step_detail}")
                else:
                    logger.warning(
                        f"Video gen status check returned {status_resp.status_code}"
                    )
            except Exception as poll_err:
                logger.warning(f"Video gen poll error: {poll_err}")

            await asyncio.sleep(5)

    logger.info(f"Batch item {item_index}: videos generated")

    # ── Step 9: Done ──────────────────────────────────────────────
    _update_step("completed")
