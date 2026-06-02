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
    BatchRun,
    BatchRunStatus as BatchRunStatusEnum,
    Lyrics,
    Project,
    ProjectMode,
    Scene,
    SongSection,
    SongSectionLabel,
    StemSelection,
)
from backend.utils.file_utils import ensure_project_dirs, sha256_file
from backend.utils.background import track as _track_task

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/batch", tags=["batch"])

# ── Module-level state ────────────────────────────────────────────────
_batch_runs: dict[str, dict] = {}

# ── Pipeline step constants (for checkpoint/resume) ──────────────────
_STEP_PROJECT_CREATED = 1
_STEP_AUDIO_COPIED = 2
_STEP_AUDIO_ANALYZED = 3
_STEP_TIMELINE_SUGGESTED = 4
_STEP_CONCEPT_GENERATED = 5
_STEP_CHARACTERS_GENERATED = 6
_STEP_FLOW_GENERATED = 7
_STEP_IMAGES_GENERATED = 8
_STEP_VIDEOS_GENERATED = 9

def _get_internal_base_url() -> str:
    """Resolve internal base URL dynamically (port may come from DB settings)."""
    return f"http://{settings.app_host}:{settings.app_port}"


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
    video_mode: str = "i2v"  # i2v | v2v | fflf
    image_mode: str = "missing"  # missing (skip filled) | all_with_refs (regenerate using prev-scene reference)
    two_pass: bool = True
    use_story_flow: bool = True
    auto_characters: bool = False
    lipsync_enabled: bool = True
    vocals_only_for_lipsync: bool = False
    override_full_set: bool = False  # for "missing" modes: regenerate everything


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


@router.post("/{batch_id}/retry")
async def retry_batch_run(batch_id: str):
    """Retry failed items in a batch run.

    Looks up the in-memory batch run, finds items with status "failed",
    resets the run to "running", and re-launches _process_single_item
    for each failed item. The existing checkpoint/resume logic inside
    _process_single_item detects the batch_run_id, reads completed_step
    from the BatchRun's run_settings, and skips already-completed steps.
    """
    run = _batch_runs.get(batch_id)
    if not run:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Batch run {batch_id} not found",
        )

    if run["status"] not in ("done", "failed"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Batch run cannot be retried (status: {run['status']})",
        )

    # Collect failed items
    failed_items = [
        item_state for item_state in run["items"]
        if item_state["status"] == "failed"
    ]
    if not failed_items:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No failed items to retry",
        )

    # Reset batch status to running
    run["status"] = "running"

    # Reset each failed item to pending and clear error
    for item_state in failed_items:
        item_state["status"] = "pending"
        item_state["error"] = None
        item_state["current_step"] = "pending retry"

    # Launch background task to process failed items
    _track_task(_retry_failed_items(batch_id, failed_items))

    logger.info(
        f"Batch run {batch_id} retry requested for {len(failed_items)} failed items"
    )

    return _build_status(batch_id)


async def _retry_failed_items(batch_id: str, failed_items: list[dict]) -> None:
    """Background task: re-process failed batch items with checkpoint resume."""
    run = _batch_runs.get(batch_id)
    if not run:
        return

    base_url = _get_internal_base_url()

    try:
        for item_state in failed_items:
            # Check for cancellation
            if run["status"] == "cancelled":
                logger.info(f"Batch {batch_id} retry cancelled")
                break

            i = item_state["index"]
            run["current_item_index"] = i
            item_state["status"] = "running"
            config = BatchItemConfig(**item_state["config"])

            # Mark the BatchRun DB record as running again
            br_id = item_state.get("batch_run_id")
            if br_id:
                _track_task(_update_batch_run_db(
                    br_id,
                    status=BatchRunStatusEnum.RUNNING,
                    current_step="retrying",
                ))

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
                    f"Batch {batch_id} retry item {i} ({item_state['project_name']}) completed"
                )

            except Exception as exc:
                logger.exception(
                    f"Batch {batch_id} retry item {i} ({item_state['project_name']}) failed: {exc}"
                )
                item_state["status"] = "failed"
                item_state["error"] = str(exc)
                item_state["current_step"] = f"failed: {exc}"

                if br_id:
                    from datetime import datetime
                    _track_task(_update_batch_run_db(
                        br_id,
                        status=BatchRunStatusEnum.FAILED,
                        current_step=f"failed: {exc}",
                        completed_at=datetime.utcnow(),
                    ))

        # Final status
        if run["status"] == "cancelled":
            pass
        elif all(s["status"] == "failed" for s in run["items"]):
            run["status"] = "failed"
        elif all(s["status"] == "done" for s in run["items"]):
            run["status"] = "done"
        else:
            run["status"] = "done"

        logger.info(f"Batch run {batch_id} retry finished with status: {run['status']}")

    except Exception as fatal:
        logger.exception(f"Batch {batch_id} retry fatal error: {fatal}")
        run["status"] = "failed"


@router.get("/active")
async def get_active_batches():
    """Return any in-memory batch runs that are still tracked.

    Also returns batch_run_ids (persistent DB IDs) for each item so the
    frontend can reconnect after navigating away and find them in /batches.
    """
    active = []
    for bid, run in _batch_runs.items():
        if run["status"] in ("running", "done", "failed", "cancelled"):
            items_status = []
            for item_state in run["items"]:
                items_status.append({
                    "index": item_state["index"],
                    "project_name": item_state["project_name"],
                    "project_id": item_state.get("project_id"),
                    "status": item_state["status"],
                    "current_step": item_state.get("current_step", ""),
                    "error": item_state.get("error"),
                    "batch_run_id": item_state.get("batch_run_id"),
                })
            active.append({
                "batch_id": bid,
                "status": run["status"],
                "total_items": len(run["items"]),
                "completed_items": sum(1 for s in run["items"] if s["status"] in ("done", "failed")),
                "current_item_index": run.get("current_item_index", -1),
                "items": items_status,
            })
    return active


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

    base_url = _get_internal_base_url()

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

                # Mark the BatchRun as failed
                br_id = item_state.get("batch_run_id")
                if br_id:
                    from datetime import datetime
                    _track_task(_update_batch_run_db(
                        br_id,
                        status=BatchRunStatusEnum.FAILED,
                        current_step=f"failed: {exc}",
                        completed_at=datetime.utcnow(),
                    ))

                # B-4: best-effort cleanup of the orphan project IF the
                # failure happened before any image/video was generated.
                # Once generation has produced work we want to preserve,
                # leave the project intact so the user can salvage it.
                _cleanup_proj_id = item_state.get("project_id")
                if _cleanup_proj_id:
                    try:
                        async with async_session() as cleanup_sess:
                            from uuid import UUID as _UUID
                            pid_obj = _UUID(_cleanup_proj_id) if isinstance(_cleanup_proj_id, str) else _cleanup_proj_id
                            # Check if any generation work happened
                            gen_count_stmt = select(Asset).where(
                                Asset.project_id == pid_obj,
                                Asset.asset_type.in_([
                                    AssetType.GENERATED_IMAGE,
                                    AssetType.GENERATED_VIDEO,
                                ]),
                            )
                            gen_count_result = await cleanup_sess.execute(gen_count_stmt)
                            has_gen = gen_count_result.scalars().first() is not None
                            if not has_gen:
                                proj_stmt = select(Project).where(Project.id == pid_obj)
                                proj_result = await cleanup_sess.execute(proj_stmt)
                                proj = proj_result.scalars().first()
                                if proj:
                                    await cleanup_sess.delete(proj)
                                    await cleanup_sess.commit()
                                    logger.info(
                                        f"Batch item {i}: cleaned up orphan project {_cleanup_proj_id} "
                                        f"(no generation work to preserve)"
                                    )
                                # Best-effort: also remove the project directory
                                try:
                                    _orphan_dir = settings.project_dir / _cleanup_proj_id
                                    if _orphan_dir.exists():
                                        shutil.rmtree(_orphan_dir, ignore_errors=True)
                                except Exception:
                                    pass
                            else:
                                logger.info(
                                    f"Batch item {i}: keeping project {_cleanup_proj_id} "
                                    f"(has generation work)"
                                )
                    except Exception as cleanup_err:
                        logger.warning(
                            f"Batch item {i}: orphan cleanup failed: {cleanup_err}"
                        )

            # B-12: only delete the staging file on SUCCESS so the retry
            # path can still find the audio if the item failed early.
            if item_state["status"] == "done":
                try:
                    staging_path = Path(config.audio_upload_path)
                    if staging_path.exists():
                        staging_path.unlink()
                    parent = staging_path.parent
                    if parent.exists() and not any(parent.iterdir()):
                        parent.rmdir()
                except Exception as cleanup_err:
                    logger.warning(f"Staging cleanup error for item {i}: {cleanup_err}")

        # ── Final status ──────────────────────────────────────────
        if run["status"] == "cancelled":
            pass  # already set
        elif all(s["status"] == "failed" for s in run["items"]):
            # Every single item failed — mark the whole batch as failed
            run["status"] = "failed"
        elif all(s["status"] == "done" for s in run["items"]):
            run["status"] = "done"
        elif any(s["status"] == "failed" for s in run["items"]):
            # Some succeeded and some failed — partial success is still "done"
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


async def _update_batch_run_db(
    batch_run_id: str,
    completed_step: int | None = None,
    checkpoint_project_id: str | None = None,
    **kwargs,
) -> None:
    """Update a BatchRun record in the database.

    Supports a special `step_entry` kwarg that appends to the step_log list
    instead of replacing it, matching the behavior of generation.py's
    _update_batch_run helper.

    The `completed_step` and `checkpoint_project_id` params are stored inside
    run_settings metadata (not as top-level columns) to avoid DB migrations.
    """
    step_entry = kwargs.pop("step_entry", None)
    try:
        async with async_session() as session:
            from sqlmodel import select as sel
            stmt = sel(BatchRun).where(BatchRun.id == batch_run_id)
            result = await session.execute(stmt)
            run = result.scalars().first()
            if not run:
                return
            for k, v in kwargs.items():
                if hasattr(run, k):
                    setattr(run, k, v)
            # Persist checkpoint metadata inside run_settings
            if completed_step is not None or checkpoint_project_id is not None:
                updated_settings = dict(run.run_settings or {})
                if completed_step is not None:
                    updated_settings["completed_step"] = completed_step
                if checkpoint_project_id is not None:
                    updated_settings["project_id"] = checkpoint_project_id
                run.run_settings = updated_settings
            # Append step_entry to the step_log list
            if step_entry:
                current_log = list(run.step_log or [])
                current_log.append(step_entry)
                run.step_log = current_log
            session.add(run)
            await session.commit()
    except Exception as e:
        logger.warning(f"Failed to update BatchRun {batch_run_id}: {e}")


async def _process_single_item(
    batch_id: str,
    item_index: int,
    item_state: dict,
    config: BatchItemConfig,
    base_url: str,
) -> None:
    """Process a single batch item through all pipeline steps."""

    run = _batch_runs[batch_id]

    # ── Resume detection ──────────────────────────────────────────────
    from datetime import datetime
    completed_step = 0  # 0 = fresh run, >0 = resume from this step
    batch_run_id: str | None = item_state.get("batch_run_id")
    project_id = None
    project_path = None
    audio_dest = None

    if batch_run_id:
        # Check if this is a resume by looking up existing BatchRun metadata
        try:
            async with async_session() as session:
                from sqlmodel import select as sel
                stmt = sel(BatchRun).where(BatchRun.id == batch_run_id)
                result = await session.execute(stmt)
                existing_run = result.scalars().first()
                if existing_run and existing_run.run_settings:
                    completed_step = existing_run.run_settings.get("completed_step", 0) or 0
                    stored_project_id = existing_run.run_settings.get("project_id") or existing_run.project_id
                    if completed_step > 0 and stored_project_id:
                        project_id = UUID(stored_project_id) if isinstance(stored_project_id, str) else stored_project_id
                        project_path = settings.project_dir / str(project_id)
                        audio_dest = project_path / "assets" / "audio" / config.audio_filename
                        item_state["project_id"] = str(project_id)
                        logger.info(
                            f"Batch item {item_index}: RESUMING from step {completed_step}, "
                            f"project {project_id}"
                        )
        except Exception as e:
            logger.warning(f"Resume detection failed for batch item {item_index}: {e}")
            completed_step = 0

    # ── Create persistent BatchRun for this item (fresh run only) ───
    if not batch_run_id:
        batch_run_id = str(uuid4())
        run_settings = {
            "audio_filename": config.audio_filename,
            "audio_upload_path": config.audio_upload_path,
            "lyrics_text": config.lyrics_text,
            "project_name": config.project_name,
            "concept_direction": config.concept_direction,
            "style_text": config.style_text,
            "render_type": config.render_type,
            "video_mode": config.video_mode,
            "two_pass": config.two_pass,
            "use_story_flow": config.use_story_flow,
        }
        try:
            async with async_session() as session:
                _now = datetime.utcnow()
                batch_run = BatchRun(
                    id=batch_run_id,
                    project_id="",  # will be set once project is created
                    project_name=item_state["project_name"],
                    mode="full_pipeline",
                    status=BatchRunStatusEnum.RUNNING,
                    total_scenes=0,  # will be updated after timeline
                    started_at=_now,
                    run_settings=run_settings,
                    current_step="initializing",
                    step_log=[{
                        "step": "initializing batch pipeline",
                        "timestamp": _now.isoformat(),
                        "type": "info",
                    }],
                )
                session.add(batch_run)
                await session.commit()
            item_state["batch_run_id"] = batch_run_id
        except Exception as e:
            logger.warning(f"Failed to create BatchRun for batch item {item_index}: {e}")
            batch_run_id = None
    else:
        # Resuming — mark status as running again
        if batch_run_id:
            _track_task(_update_batch_run_db(
                batch_run_id,
                status=BatchRunStatusEnum.RUNNING,
                current_step="resuming",
            ))

    # ── Checkpoint helper ──────────────────────────────────────────
    async def _save_checkpoint(step_number: int) -> None:
        """Persist completed step and project_id to BatchRun metadata for resume."""
        nonlocal completed_step
        completed_step = step_number
        if batch_run_id:
            await _update_batch_run_db(
                batch_run_id,
                completed_step=step_number,
                checkpoint_project_id=str(project_id) if project_id else None,
            )

    def _update_step(step: str, entry_type: str = "info", scene_name: str | None = None) -> None:
        item_state["current_step"] = step
        logger.info(f"Batch {batch_id} item {item_index}: {step}")
        if batch_run_id:
            _step_entry: dict = {
                "step": step,
                "timestamp": datetime.utcnow().isoformat(),
                "type": entry_type,
            }
            if scene_name:
                _step_entry["scene_name"] = scene_name
            _track_task(_update_batch_run_db(
                batch_run_id, current_step=step,
                step_entry=_step_entry,
            ))

    def _check_cancelled() -> None:
        if run["status"] == "cancelled":
            raise RuntimeError("Batch cancelled")

    # ── Step 1: Create project ────────────────────────────────────
    if completed_step >= _STEP_PROJECT_CREATED:
        _update_step("skipping project creation (already done)")
        logger.info(f"Batch item {item_index}: skipping step 1 (project already created: {project_id})")
    else:
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

        # Update BatchRun with project_id now that it's known
        if batch_run_id:
            _track_task(_update_batch_run_db(
                batch_run_id, project_id=str(project_id),
            ))

        await _save_checkpoint(_STEP_PROJECT_CREATED)

    # ── Step 2: Copy audio to project ─────────────────────────────
    if completed_step >= _STEP_AUDIO_COPIED:
        _update_step("skipping audio copy (already done)")
        # Recover audio_dest path for later steps
        audio_dest = project_path / "assets" / "audio" / config.audio_filename
        logger.info(f"Batch item {item_index}: skipping step 2 (audio already copied)")
    else:
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

        await _save_checkpoint(_STEP_AUDIO_COPIED)

    # ── Step 3: Audio analysis ────────────────────────────────────
    if completed_step >= _STEP_AUDIO_ANALYZED:
        _update_step("skipping audio analysis (already done)")
        logger.info(f"Batch item {item_index}: skipping step 3 (audio already analyzed)")
    else:
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
        # Narration batches feed pure-speech audio — skip Demucs to save
        # ~30 min / item and avoid phase artifacts from stem separation.
        _skip_demucs = config.render_type in ("narration_video", "narration_images")
        if _skip_demucs:
            logger.info(
                f"Batch item: skipping Demucs for narration render type "
                f"({config.render_type})"
            )
        # B-7 (rev): per-item budget now SCALES with audio length so
        # hour-long narrations don't get killed prematurely.  Floor at
        # 1h (matches the old hard cap), then add 4× audio runtime for
        # Whisper + 2× for Demucs on slow machines.  Cap at 8h for safety.
        from backend.services.audio.analysis import _get_audio_duration_seconds
        _audio_dur = _get_audio_duration_seconds(str(audio_dest))
        _whisper_budget = int(_audio_dur * 4)            # 4× real time
        _demucs_budget = 0 if _skip_demucs else int(_audio_dur * 2)
        _budget = max(3600, _whisper_budget + _demucs_budget, 1800)
        _budget = min(_budget, 8 * 3600)
        logger.info(
            f"Batch item analysis budget: {_budget // 60} min "
            f"(audio ~{_audio_dur:.0f}s, skip_demucs={_skip_demucs})"
        )
        try:
            analysis_result = await asyncio.wait_for(
                asyncio.to_thread(
                    analyzer.analyze_full,
                    str(audio_dest),
                    whisper_mode,
                    whisper_remote_url,
                    whisper_comfyui_url=whisper_comfyui_url,
                    initial_text=initial_text,
                    whisper_model=whisper_model,
                    whisper_language=whisper_language,
                    skip_demucs=_skip_demucs,
                ),
                timeout=_budget,
            )
        except asyncio.TimeoutError:
            raise RuntimeError(
                "Audio analysis timed out after 1 hour "
                "(check Whisper backend / GPU)"
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

        # B-9: use a fresh session for the retry to escape transaction
        # corruption (gotcha #9: a failed commit + rollback can corrupt the
        # session and break subsequent commits in the same session).  Also
        # don't silently strip initial_text on retry — preserve user lyrics.
        _lyrics_saved = False
        try:
            async with async_session() as session:
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
                _lyrics_saved = True
        except Exception as lyrics_err:
            logger.warning(f"Lyrics save failed: {lyrics_err}; retrying with fresh session")

        if not _lyrics_saved:
            try:
                async with async_session() as session2:
                    existing_lyrics_stmt = select(Lyrics).where(
                        Lyrics.project_id == project_id
                    )
                    existing_lyrics_result = await session2.execute(existing_lyrics_stmt)
                    existing_lyrics = existing_lyrics_result.scalars().first()
                    if existing_lyrics:
                        await session2.delete(existing_lyrics)
                        await session2.flush()
                    lyrics_record = Lyrics(
                        project_id=project_id,
                        full_text=lyrics_text,
                        initial_text=initial_text,
                        words=transcription_words,
                    )
                    session2.add(lyrics_record)
                    await session2.commit()
            except Exception as e2:
                logger.error(
                    f"Lyrics save retry also failed: {e2} — continuing without lyrics record"
                )

        # Cache lyrics to file
        lyrics_data = {"text": lyrics_text, "words": transcription_words}
        lyrics_cache_path = cache_dir / "lyrics.json"
        with open(lyrics_cache_path, "w") as f:
            json.dump(lyrics_data, f)

        logger.info(f"Batch item {item_index}: audio analysis complete")

        await _save_checkpoint(_STEP_AUDIO_ANALYZED)

    # ── Step 4: Suggest timeline ──────────────────────────────────
    if completed_step >= _STEP_TIMELINE_SUGGESTED:
        _update_step("skipping timeline suggestion (already done)")
        logger.info(f"Batch item {item_index}: skipping step 4 (timeline already suggested)")
    else:
        _update_step("suggesting timeline")
        _check_cancelled()

        async with httpx.AsyncClient(base_url=base_url, timeout=600) as client:
            resp = await client.post(
                f"/api/projects/{project_id}/timeline/suggest-timeline",
            )
            if resp.status_code != 200:
                logger.warning(
                    f"Suggest timeline returned {resp.status_code}: {resp.text}"
                )
                # Non-fatal — continue even if suggestion fails

        logger.info(f"Batch item {item_index}: timeline suggested")

        # Update BatchRun with scene count after timeline is suggested
        if batch_run_id:
            try:
                async with async_session() as session:
                    scene_count_stmt = select(Scene).where(Scene.project_id == project_id)
                    scene_count_result = await session.execute(scene_count_stmt)
                    scenes_list = scene_count_result.scalars().all()
                    _track_task(_update_batch_run_db(
                        batch_run_id, total_scenes=len(scenes_list),
                    ))
            except Exception:
                pass

        await _save_checkpoint(_STEP_TIMELINE_SUGGESTED)

    # ── Step 5: Generate concept from lyrics ──────────────────────
    if completed_step >= _STEP_CONCEPT_GENERATED:
        _update_step("skipping concept generation (already done)")
        logger.info(f"Batch item {item_index}: skipping step 5 (concept already generated)")
    else:
        _check_cancelled()
        # B-6: skip the LLM call entirely when the user already supplied
        # both concept_direction and style_text — they'd be overwritten
        # anyway, so the call is pure waste.
        concept_data: dict = {}
        if config.concept_direction and config.style_text:
            _update_step("skipping concept LLM (user supplied direction + style)")
            logger.info(
                f"Batch item {item_index}: skipping base-on-lyrics — user supplied both fields"
            )
        else:
            _update_step("generating concept from lyrics")
            try:
                async with httpx.AsyncClient(base_url=base_url, timeout=600) as client:
                    resp = await client.post(
                        f"/api/projects/{project_id}/concept/base-on-lyrics",
                        json={},
                    )
                if resp.status_code != 200:
                    logger.warning(
                        f"Base-on-lyrics returned {resp.status_code}: {resp.text[:300]}"
                    )
                else:
                    try:
                        concept_data = resp.json()
                    except Exception:
                        pass
            except Exception as concept_err:
                logger.warning(f"Base-on-lyrics call raised: {concept_err}")

        async with async_session() as session:
            stmt = select(Project).where(Project.id == project_id)
            result = await session.execute(stmt)
            project = result.scalars().first()
            if project:
                proj_settings = dict(project.settings) if project.settings else {}
                # Save LLM-generated concept fields
                if concept_data.get("song_title"):
                    proj_settings["song_title"] = concept_data["song_title"]
                if concept_data.get("concept_text"):
                    proj_settings["concept_text"] = concept_data["concept_text"]
                if concept_data.get("style_text"):
                    proj_settings["style_text"] = concept_data["style_text"]
                # Override with user-provided values from batch config
                if config.concept_direction:
                    proj_settings["concept_direction"] = config.concept_direction
                if config.style_text:
                    proj_settings["style_text"] = config.style_text
                project.settings = proj_settings
                await session.commit()

        logger.info(f"Batch item {item_index}: concept generated")

        await _save_checkpoint(_STEP_CONCEPT_GENERATED)

    # ── Step 5b: Auto-generate characters (optional) ─────────────
    if completed_step >= _STEP_CHARACTERS_GENERATED:
        if config.auto_characters:
            _update_step("skipping character generation (already done)")
            logger.info(f"Batch item {item_index}: skipping step 5b (characters already generated)")
    else:
        if config.auto_characters:
            _update_step("auto-generating characters")
            _check_cancelled()

            try:
                async with httpx.AsyncClient(base_url=base_url, timeout=600) as client:
                    resp = await client.post(
                        f"/api/projects/{project_id}/concept/characters/autogenerate",
                    )
                if resp.status_code != 200:
                    msg = (
                        f"Character autogenerate returned {resp.status_code}: "
                        f"{resp.text[:300]}"
                    )
                    logger.warning(msg)
                    _update_step(f"warning: characters failed ({resp.status_code})",
                                 entry_type="warning")
                else:
                    logger.info(f"Batch item {item_index}: characters auto-generated")
            except Exception as char_err:
                logger.warning(f"Character autogenerate raised: {char_err}")
                _update_step(f"warning: characters error: {char_err}",
                             entry_type="warning")

        await _save_checkpoint(_STEP_CHARACTERS_GENERATED)

    # ── Step 6: Generate video flow ───────────────────────────────
    if completed_step >= _STEP_FLOW_GENERATED:
        _update_step("skipping video flow generation (already done)")
        logger.info(f"Batch item {item_index}: skipping step 6 (video flow already generated)")
    else:
        _update_step("generating video flow")
        _check_cancelled()

        async with httpx.AsyncClient(base_url=base_url, timeout=600) as client:
            resp = await client.post(
                f"/api/projects/{project_id}/concept/flow/generate",
            )
            if resp.status_code != 200:
                logger.warning(
                    f"Video flow returned {resp.status_code}: {resp.text}"
                )

        logger.info(f"Batch item {item_index}: video flow generated")

        await _save_checkpoint(_STEP_FLOW_GENERATED)

    # ── Step 7: Auto-gen images ───────────────────────────────────
    if completed_step >= _STEP_IMAGES_GENERATED:
        _update_step("skipping image generation (already done)")
        logger.info(f"Batch item {item_index}: skipping step 7 (images already generated)")
    else:
        _update_step("generating images")
        _check_cancelled()

        # B-10: image_mode selects between missing-only and full set with refs
        image_mode_map = {
            "missing": "missing_images_independent",
            "all_with_refs": "all_images",
        }
        if config.image_mode not in image_mode_map:
            raise RuntimeError(
                f"Unknown image_mode {config.image_mode!r}. "
                f"Valid: {sorted(image_mode_map)}"
            )
        auto_gen_mode = image_mode_map[config.image_mode]

        # B-1: kickoff must succeed; raise on failure so the item is marked
        # failed instead of "completing" with zero work done.
        async with httpx.AsyncClient(base_url=base_url, timeout=30) as client:
            try:
                resp = await client.post(
                    f"/api/projects/{project_id}/generate/auto-sequential",
                    json={
                        "mode": auto_gen_mode,
                        "two_pass": config.two_pass,
                        "use_story_flow": config.use_story_flow,
                        "override_full_set": config.override_full_set,
                        "lipsync_enabled": config.lipsync_enabled,
                        "vocals_only_for_lipsync": config.vocals_only_for_lipsync,
                    },
                )
            except Exception as start_err:
                raise RuntimeError(f"Image auto-gen kickoff failed: {start_err}")
            if resp.status_code != 200:
                raise RuntimeError(
                    f"Image auto-gen kickoff returned {resp.status_code}: "
                    f"{resp.text[:300]}"
                )

        # B-2: bounded poll with 2-hour deadline. B-1: saw_running guards
        # against the idle race (idle = no entry, which happens before the
        # run starts OR after 5-min eviction post-completion).
        import time as _t
        deadline = _t.monotonic() + 2 * 60 * 60  # 2h cap
        saw_running = False
        async with httpx.AsyncClient(base_url=base_url, timeout=15) as client:
            while True:
                if _t.monotonic() > deadline:
                    raise RuntimeError(
                        "Image auto-gen poll timed out after 2h "
                        "(check ComfyUI workers / queue)"
                    )
                _check_cancelled()
                try:
                    status_resp = await client.get(
                        f"/api/projects/{project_id}/generate/auto-sequential/status",
                    )
                    if status_resp.status_code != 200:
                        logger.warning(
                            f"Image gen status check returned {status_resp.status_code}"
                        )
                        await asyncio.sleep(3)
                        continue
                    data = status_resp.json()
                    gen_status = data.get("status", "unknown")
                    if gen_status == "running":
                        saw_running = True
                        step_detail = data.get("current_step", "")
                        _update_step(f"generating images: {step_detail}")
                    elif gen_status in ("done", "failed", "cancelled"):
                        if gen_status == "failed":
                            err = data.get("error") or "auto-gen reported failure"
                            raise RuntimeError(f"Image auto-gen failed: {err}")
                        if gen_status == "cancelled":
                            raise RuntimeError("Image auto-gen was cancelled")
                        break
                    elif gen_status == "idle":
                        # B-1 idle race: only treat idle as terminal AFTER we
                        # confirmed the run started.  If we see idle before
                        # ever seeing running, the run never kicked off.
                        if saw_running:
                            break
                        # Give the kickoff a generous head start before we
                        # call it a no-show; backend sets the entry inside
                        # the POST handler so this should be near-immediate.
                        await asyncio.sleep(2)
                        continue
                except RuntimeError:
                    raise
                except Exception as poll_err:
                    logger.warning(f"Image gen poll error: {poll_err}")

                await asyncio.sleep(3)

        logger.info(f"Batch item {item_index}: images generated")

        await _save_checkpoint(_STEP_IMAGES_GENERATED)

    # ── Step 8: Auto-gen videos ───────────────────────────────────
    if completed_step >= _STEP_VIDEOS_GENERATED:
        _update_step("skipping video generation (already done)")
        logger.info(f"Batch item {item_index}: skipping step 8 (videos already generated)")
    else:
        _update_step("generating videos")
        _check_cancelled()

        # B-3: exhaustive video mode map. Unknown values now raise instead
        # of silently demoting to single-image mode.
        video_mode_map = {
            "i2v": "all_video_single",
            "v2v": "all_video_v2v",
            "fflf": "all_video_fflf",
        }
        if config.video_mode not in video_mode_map:
            raise RuntimeError(
                f"Unknown video_mode {config.video_mode!r}. "
                f"Valid: {sorted(video_mode_map)}"
            )
        video_gen_mode = video_mode_map[config.video_mode]

        async with httpx.AsyncClient(base_url=base_url, timeout=30) as client:
            try:
                resp = await client.post(
                    f"/api/projects/{project_id}/generate/auto-sequential",
                    json={
                        "mode": video_gen_mode,
                        "use_story_flow": config.use_story_flow,
                        "override_full_set": config.override_full_set,
                        "lipsync_enabled": config.lipsync_enabled,
                        "vocals_only_for_lipsync": config.vocals_only_for_lipsync,
                    },
                )
            except Exception as start_err:
                raise RuntimeError(f"Video auto-gen kickoff failed: {start_err}")
            if resp.status_code != 200:
                raise RuntimeError(
                    f"Video auto-gen kickoff returned {resp.status_code}: "
                    f"{resp.text[:300]}"
                )

        # B-2: 2-hour deadline. B-1: saw_running guard for idle race.
        import time as _t
        deadline = _t.monotonic() + 2 * 60 * 60
        saw_running = False
        async with httpx.AsyncClient(base_url=base_url, timeout=15) as client:
            while True:
                if _t.monotonic() > deadline:
                    raise RuntimeError(
                        "Video auto-gen poll timed out after 2h "
                        "(check ComfyUI workers / queue)"
                    )
                _check_cancelled()
                try:
                    status_resp = await client.get(
                        f"/api/projects/{project_id}/generate/auto-sequential/status",
                    )
                    if status_resp.status_code != 200:
                        logger.warning(
                            f"Video gen status check returned {status_resp.status_code}"
                        )
                        await asyncio.sleep(5)
                        continue
                    data = status_resp.json()
                    gen_status = data.get("status", "unknown")
                    if gen_status == "running":
                        saw_running = True
                        step_detail = data.get("current_step", "")
                        _update_step(f"generating videos: {step_detail}")
                    elif gen_status in ("done", "failed", "cancelled"):
                        if gen_status == "failed":
                            err = data.get("error") or "auto-gen reported failure"
                            raise RuntimeError(f"Video auto-gen failed: {err}")
                        if gen_status == "cancelled":
                            raise RuntimeError("Video auto-gen was cancelled")
                        break
                    elif gen_status == "idle":
                        if saw_running:
                            break
                        await asyncio.sleep(2)
                        continue
                except RuntimeError:
                    raise
                except Exception as poll_err:
                    logger.warning(f"Video gen poll error: {poll_err}")

                await asyncio.sleep(5)

        logger.info(f"Batch item {item_index}: videos generated")

        await _save_checkpoint(_STEP_VIDEOS_GENERATED)

    # ── Step 9: Done ──────────────────────────────────────────────
    _update_step("completed")

    # Mark BatchRun as completed
    if batch_run_id:
        _track_task(_update_batch_run_db(
            batch_run_id,
            status=BatchRunStatusEnum.COMPLETED,
            completed_at=datetime.utcnow(),
            current_step="completed",
        ))
