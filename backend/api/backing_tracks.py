"""Backing track CRUD endpoints for narration projects."""
import logging
import os
import subprocess
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from backend.config import settings
from backend.database import get_session
from backend.database.models import BackingTrack, Project

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/projects/{project_id}/backing-tracks",
    tags=["backing-tracks"],
)


# ── Pydantic models ──────────────────────────────────────────────────────

class BackingTrackResponse(BaseModel):
    """Response model for a backing track."""

    id: UUID
    project_id: UUID
    filename: str
    rel_path: str
    order_index: int
    start_time: float
    end_time: float
    trim_start: float
    trim_end: float
    volume_db: float
    fade_in_sec: float
    fade_out_sec: float

    class Config:
        from_attributes = True


class BackingTrackUpdate(BaseModel):
    """Request model for updating a backing track."""

    order_index: Optional[int] = None
    start_time: Optional[float] = None
    end_time: Optional[float] = None
    trim_start: Optional[float] = None
    trim_end: Optional[float] = None
    volume_db: Optional[float] = None
    fade_in_sec: Optional[float] = None
    fade_out_sec: Optional[float] = None


# ── Helpers ───────────────────────────────────────────────────────────────

async def _get_project_or_404(project_id: UUID, session: AsyncSession) -> Project:
    """Get project or raise 404."""
    project = await session.get(Project, project_id)
    if not project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Project {project_id} not found",
        )
    return project


# ── Endpoints ─────────────────────────────────────────────────────────────

@router.get(
    "",
    response_model=list[BackingTrackResponse],
    summary="List backing tracks",
)
async def list_backing_tracks(
    project_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> list[BackingTrackResponse]:
    """List all backing tracks for a project, ordered by order_index.

    Args:
        project_id: UUID of the project.
        session: Database session.

    Returns:
        List of backing tracks.

    Raises:
        HTTPException: If project not found.
    """
    try:
        await _get_project_or_404(project_id, session)

        stmt = (
            select(BackingTrack)
            .where(BackingTrack.project_id == project_id)
            .order_by(BackingTrack.order_index)
        )
        result = await session.execute(stmt)
        tracks = result.scalars().all()

        return [BackingTrackResponse.model_validate(t) for t in tracks]
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error listing backing tracks for project {project_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to list backing tracks",
        )


@router.post(
    "",
    response_model=BackingTrackResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Upload backing track",
)
async def create_backing_track(
    project_id: UUID,
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_session),
) -> BackingTrackResponse:
    """Upload an audio file and create a BackingTrack record.

    The file is saved to the project's assets/backing_tracks/ directory.

    Args:
        project_id: UUID of the project.
        file: Audio file to upload.
        session: Database session.

    Returns:
        Created backing track record.

    Raises:
        HTTPException: If project not found or upload fails.
    """
    try:
        await _get_project_or_404(project_id, session)

        if not file.filename:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="File must have a filename",
            )

        # Save file to project directory
        project_path = settings.project_dir / str(project_id)
        tracks_dir = project_path / "assets" / "backing_tracks"
        tracks_dir.mkdir(parents=True, exist_ok=True)

        file_path = tracks_dir / file.filename
        content = await file.read()
        with open(file_path, "wb") as f:
            f.write(content)

        rel_path = f"assets/backing_tracks/{file.filename}"

        # Determine next order_index
        count_stmt = (
            select(BackingTrack)
            .where(BackingTrack.project_id == project_id)
        )
        count_result = await session.execute(count_stmt)
        existing_count = len(count_result.scalars().all())

        # Detect audio duration for sensible default end_time
        audio_duration = 0.0
        try:
            probe_result = subprocess.run(
                [
                    "ffprobe", "-v", "quiet", "-show_entries",
                    "format=duration", "-of", "csv=p=0",
                    str(file_path),
                ],
                capture_output=True, text=True, timeout=10,
            )
            if probe_result.returncode == 0 and probe_result.stdout.strip():
                audio_duration = float(probe_result.stdout.strip())
        except Exception as e:
            logger.warning(f"Could not detect duration for backing track '{file.filename}': {e}")

        track = BackingTrack(
            project_id=project_id,
            filename=file.filename,
            rel_path=rel_path,
            order_index=existing_count,
            end_time=audio_duration,
        )
        session.add(track)
        await session.commit()
        await session.refresh(track)

        logger.info(
            f"Created backing track '{file.filename}' for project {project_id} "
            f"(order_index={existing_count})"
        )

        return BackingTrackResponse.model_validate(track)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating backing track for project {project_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create backing track",
        )


@router.patch(
    "/{track_id}",
    response_model=BackingTrackResponse,
    summary="Update backing track",
)
async def update_backing_track(
    project_id: UUID,
    track_id: UUID,
    req: BackingTrackUpdate,
    session: AsyncSession = Depends(get_session),
) -> BackingTrackResponse:
    """Update backing track properties (volume, timing, fade, order).

    Args:
        project_id: UUID of the project.
        track_id: UUID of the backing track.
        req: Update fields.
        session: Database session.

    Returns:
        Updated backing track.

    Raises:
        HTTPException: If project or track not found.
    """
    try:
        await _get_project_or_404(project_id, session)

        track = await session.get(BackingTrack, track_id)
        if not track or track.project_id != project_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Backing track {track_id} not found",
            )

        if req.order_index is not None:
            track.order_index = req.order_index
        if req.start_time is not None:
            track.start_time = req.start_time
        if req.end_time is not None:
            track.end_time = req.end_time
        if req.trim_start is not None:
            track.trim_start = req.trim_start
        if req.trim_end is not None:
            track.trim_end = req.trim_end
        if req.volume_db is not None:
            track.volume_db = req.volume_db
        if req.fade_in_sec is not None:
            track.fade_in_sec = req.fade_in_sec
        if req.fade_out_sec is not None:
            track.fade_out_sec = req.fade_out_sec

        await session.commit()
        await session.refresh(track)

        logger.info(f"Updated backing track {track_id}")

        return BackingTrackResponse.model_validate(track)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating backing track {track_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update backing track",
        )


@router.delete(
    "/{track_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete backing track",
)
async def delete_backing_track(
    project_id: UUID,
    track_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> None:
    """Delete a backing track record and its file from disk.

    Args:
        project_id: UUID of the project.
        track_id: UUID of the backing track.
        session: Database session.

    Raises:
        HTTPException: If project or track not found.
    """
    try:
        await _get_project_or_404(project_id, session)

        track = await session.get(BackingTrack, track_id)
        if not track or track.project_id != project_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Backing track {track_id} not found",
            )

        # Delete the file from disk
        if track.rel_path:
            file_path = settings.project_dir / str(project_id) / track.rel_path
            if file_path.exists():
                try:
                    os.remove(file_path)
                    logger.info(f"Deleted backing track file: {file_path}")
                except OSError as e:
                    logger.warning(f"Failed to delete backing track file {file_path}: {e}")

        await session.delete(track)
        await session.commit()

        logger.info(f"Deleted backing track {track_id} from project {project_id}")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting backing track {track_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to delete backing track",
        )
