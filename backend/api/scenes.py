"""Scene management endpoints for RBMN Storyboard App."""
import asyncio
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File, Form
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified
from sqlalchemy.orm import selectinload
from sqlmodel import select

from backend.database import get_session
from backend.database.models import Scene, Project, GenerationHistory, TimelinePosition, StemSelection, Asset, AssetType, JobType
from backend.config import settings

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/projects/{project_id}/scenes", tags=["scenes"])


def _snap_to_frame(t: float, fps: int = 24) -> float:
    """Round a time value to the nearest frame boundary."""
    if t <= 0:
        return 0.0
    return round(t * fps) / fps


# Pydantic models for request/response
class SceneCreate(BaseModel):
    """Request model for creating a scene."""

    name: str
    start_time: float
    end_time: float
    prompt: str
    negative_prompt: str = ""


class SceneUpdate(BaseModel):
    """Request model for updating a scene."""

    name: Optional[str] = None
    start_time: Optional[float] = None
    end_time: Optional[float] = None
    prompt: Optional[str] = None
    negative_prompt: Optional[str] = None
    parameters: Optional[dict] = None


class SceneReorderItem(BaseModel):
    """Item for reordering scenes."""

    scene_id: UUID
    order_index: int


class GenerationHistoryResponse(BaseModel):
    """Response model for generation history entry."""

    id: UUID
    job_type: str
    prompt_id: str
    status: str
    parameters: dict
    output_path: Optional[str]
    error_message: Optional[str]
    created_at: datetime
    completed_at: Optional[datetime]

    class Config:
        from_attributes = True


class TimelinePositionResponse(BaseModel):
    """Response model for timeline position."""

    id: UUID
    asset_id: UUID
    track: int
    start_sec: float
    end_sec: float
    gain_db: float
    effects: dict

    class Config:
        from_attributes = True


class StemSelectionResponse(BaseModel):
    """Response model for stem selection."""

    vocals: bool
    drums: bool
    bass: bool
    other: bool

    class Config:
        from_attributes = True


class PrevLastFrameResponse(BaseModel):
    """Response model for previous scene's last frame."""

    image_path: Optional[str] = None

    class Config:
        from_attributes = True


class SceneResponse(BaseModel):
    """Response model for a scene."""

    id: UUID
    project_id: UUID
    order_index: int
    name: str
    start_time: float
    end_time: float
    prompt: str
    negative_prompt: str
    parameters: dict
    workflow_snapshot: Optional[dict] = None  # gotcha #54 — frontend reads this
    generation_history: list[GenerationHistoryResponse] = []
    stem_selection: Optional[StemSelectionResponse] = None
    timeline_positions: list[TimelinePositionResponse] = []

    class Config:
        from_attributes = True


# Helper: load a single scene with all relationships eagerly loaded
async def _load_scene_with_relations(session: AsyncSession, scene_id: UUID) -> Scene | None:
    """Load a scene with generation_history, stem_selection, timeline_positions."""
    stmt = (
        select(Scene)
        .where(Scene.id == scene_id)
        .options(
            selectinload(Scene.generation_history),
            selectinload(Scene.stem_selection),
            selectinload(Scene.timeline_positions),
        )
    )
    result = await session.execute(stmt)
    return result.scalars().first()


# Helper function to validate project exists
async def _get_project_or_404(project_id: UUID, session: AsyncSession) -> Project:
    """Get project or raise 404."""
    project = await session.get(Project, project_id)
    if not project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Project {project_id} not found",
        )
    return project


@router.get(
    "",
    response_model=list[SceneResponse],
    summary="List scenes",
)
async def list_scenes(
    project_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> list[SceneResponse]:
    """Get all scenes for a project ordered by order_index.

    Args:
        project_id: UUID of the project.
        session: Database session.

    Returns:
        List of scenes ordered by order_index.

    Raises:
        HTTPException: If project not found.
    """
    try:
        await _get_project_or_404(project_id, session)

        stmt = (
            select(Scene)
            .where(Scene.project_id == project_id)
            .order_by(Scene.order_index)
            .options(
                selectinload(Scene.generation_history),
                selectinload(Scene.stem_selection),
                selectinload(Scene.timeline_positions),
            )
        )
        result = await session.execute(stmt)
        scenes = result.scalars().all()

        return [
            SceneResponse.model_validate(scene)
            for scene in scenes
        ]
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error listing scenes for project {project_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to list scenes",
        )


@router.post(
    "",
    response_model=SceneResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create scene",
)
async def create_scene(
    project_id: UUID,
    req: SceneCreate,
    session: AsyncSession = Depends(get_session),
) -> SceneResponse:
    """Create a new scene for a project.

    Args:
        project_id: UUID of the project.
        req: Scene creation request.
        session: Database session.

    Returns:
        Created scene.

    Raises:
        HTTPException: If project not found.
    """
    try:
        await _get_project_or_404(project_id, session)

        # Get next order_index
        stmt = select(Scene).where(Scene.project_id == project_id)
        result = await session.execute(stmt)
        scenes = result.scalars().all()
        next_order_index = len(scenes)

        scene = Scene(
            project_id=project_id,
            order_index=next_order_index,
            name=req.name,
            start_time=_snap_to_frame(req.start_time),
            end_time=_snap_to_frame(req.end_time),
            prompt=req.prompt,
            negative_prompt=req.negative_prompt,
        )
        session.add(scene)
        await session.flush()

        # Create default stem selection
        stem_selection = StemSelection(
            scene_id=scene.id,
            vocals=True,
            drums=True,
            bass=True,
            other=True,
        )
        session.add(stem_selection)

        await session.commit()

        logger.info(f"Created scene {scene.id} in project {project_id}")

        # Re-load with relationships eagerly loaded for response
        loaded = await _load_scene_with_relations(session, scene.id)
        return SceneResponse.model_validate(loaded)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating scene in project {project_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create scene",
        )


# ── Scene Cleanup (must be before /{scene_id} routes) ───────────────


class SceneCleanupResponse(BaseModel):
    """Response for scene cleanup operation."""
    total_before: int
    total_after: int
    removed: int
    message: str


@router.post(
    "/cleanup",
    response_model=SceneCleanupResponse,
    summary="Clean up orphaned/duplicate scenes",
)
async def cleanup_scenes(
    project_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> SceneCleanupResponse:
    """Detect and remove orphaned or duplicate scenes.

    Keeps scenes with the lowest order_index values (contiguous from 0)
    and removes any scenes beyond the expected count. Also re-indexes
    remaining scenes to ensure contiguous order_index values (0, 1, 2, ...).

    This is useful when previous operations (e.g. scenes-from-sections)
    left orphaned scenes in the database due to incomplete cleanup.

    Args:
        project_id: UUID of the project.
        session: Database session.

    Returns:
        Cleanup summary with before/after counts.
    """
    try:
        await _get_project_or_404(project_id, session)

        # Get all scenes ordered by order_index
        stmt = (
            select(Scene)
            .where(Scene.project_id == project_id)
            .order_by(Scene.order_index)
        )
        result = await session.execute(stmt)
        all_scenes = list(result.scalars().all())
        total_before = len(all_scenes)

        if total_before == 0:
            return SceneCleanupResponse(
                total_before=0,
                total_after=0,
                removed=0,
                message="No scenes to clean up",
            )

        # Find the highest contiguous order_index starting from 0
        # Scenes with order_index 0, 1, 2, ... N are kept; duplicates/gaps are flagged
        seen_indices: dict[int, Scene] = {}
        duplicates: list[Scene] = []

        for scene in all_scenes:
            idx = scene.order_index
            if idx in seen_indices:
                # Duplicate order_index — keep the one with more data (has prompt or parameters)
                existing = seen_indices[idx]
                existing_has_data = bool(existing.prompt) or bool(existing.parameters)
                new_has_data = bool(scene.prompt) or bool(scene.parameters)

                if new_has_data and not existing_has_data:
                    # New one has more data, replace
                    duplicates.append(existing)
                    seen_indices[idx] = scene
                else:
                    duplicates.append(scene)
            else:
                seen_indices[idx] = scene

        # Delete duplicates
        for dup in duplicates:
            logger.info(f"Removing duplicate scene {dup.id} (order_index={dup.order_index})")
            await session.delete(dup)

        # Re-index remaining scenes to be contiguous (0, 1, 2, ...)
        remaining = sorted(seen_indices.values(), key=lambda s: s.order_index)
        for new_idx, scene in enumerate(remaining):
            if scene.order_index != new_idx:
                scene.order_index = new_idx

        await session.commit()

        total_after = len(remaining)
        removed = total_before - total_after

        logger.info(
            f"Scene cleanup for project {project_id}: "
            f"{total_before} → {total_after} scenes ({removed} removed)"
        )

        return SceneCleanupResponse(
            total_before=total_before,
            total_after=total_after,
            removed=removed,
            message=f"Cleaned up {removed} orphaned scenes. {total_after} scenes remaining.",
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error cleaning up scenes for project {project_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Scene cleanup failed: {str(e)}",
        )


@router.put(
    "/reorder",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Reorder scenes",
)
async def reorder_scenes(
    project_id: UUID,
    items: list[SceneReorderItem],
    session: AsyncSession = Depends(get_session),
) -> None:
    """Reorder scenes by setting new order_index values.

    NOTE: Named route — MUST be registered BEFORE `/{scene_id}` routes,
    otherwise FastAPI parses the literal "reorder" as a UUID and 422s.
    See gotcha #18.

    Args:
        project_id: UUID of the project.
        items: List of scene_id + order_index pairs.
        session: Database session.

    Raises:
        HTTPException: If project not found or scene not in project.
    """
    try:
        await _get_project_or_404(project_id, session)

        for item in items:
            scene = await session.get(Scene, item.scene_id)
            if not scene or scene.project_id != project_id:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Scene {item.scene_id} not found in project",
                )
            scene.order_index = item.order_index

        await session.commit()
        logger.info(f"Reordered {len(items)} scenes in project {project_id}")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error reordering scenes in project {project_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to reorder scenes",
        )


@router.get(
    "/{scene_id}",
    response_model=SceneResponse,
    summary="Get scene details",
)
async def get_scene(
    project_id: UUID,
    scene_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> SceneResponse:
    """Get detailed scene information including generation history and stem selections.

    Args:
        project_id: UUID of the project.
        scene_id: UUID of the scene.
        session: Database session.

    Returns:
        Scene details with generation history and timeline positions.

    Raises:
        HTTPException: If project or scene not found.
    """
    try:
        await _get_project_or_404(project_id, session)

        scene = await _load_scene_with_relations(session, scene_id)
        if not scene or scene.project_id != project_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Scene {scene_id} not found",
            )

        return SceneResponse.model_validate(scene)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting scene {scene_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get scene",
        )


@router.put(
    "/{scene_id}",
    response_model=SceneResponse,
    summary="Update scene",
)
async def update_scene(
    project_id: UUID,
    scene_id: UUID,
    req: SceneUpdate,
    session: AsyncSession = Depends(get_session),
) -> SceneResponse:
    """Update scene details (prompt, timing, parameters, etc.).

    Args:
        project_id: UUID of the project.
        scene_id: UUID of the scene.
        req: Update request.
        session: Database session.

    Returns:
        Updated scene.

    Raises:
        HTTPException: If project or scene not found.
    """
    try:
        await _get_project_or_404(project_id, session)

        scene = await session.get(Scene, scene_id)
        if not scene or scene.project_id != project_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Scene {scene_id} not found",
            )

        if req.name is not None:
            scene.name = req.name
        if req.start_time is not None:
            scene.start_time = _snap_to_frame(req.start_time)
        if req.end_time is not None:
            scene.end_time = _snap_to_frame(req.end_time)
        if req.prompt is not None:
            scene.prompt = req.prompt
        if req.negative_prompt is not None:
            scene.negative_prompt = req.negative_prompt
        if req.parameters is not None:
            scene.parameters = req.parameters
            # Defensive: MutableDict.as_mutable should detect this, but
            # gotcha #53 has shown SQLAlchemy can silently skip the commit
            # when the new dict happens to compare equal to the old one
            # under JSON serialization. Explicit flag_modified guarantees
            # the column persists.
            flag_modified(scene, "parameters")

        await session.commit()

        # Re-slice audio if timing changed
        timing_changed = req.start_time is not None or req.end_time is not None
        if timing_changed:
            try:
                from backend.services.video.ffmpeg import slice_audio as _slice_audio

                # Find project's music asset
                music_stmt = (
                    select(Asset)
                    .where(Asset.project_id == project_id)
                    .where(Asset.asset_type == AssetType.MUSIC)
                    .where(~Asset.rel_path.contains("stems/"))
                )
                music_result = await session.execute(music_stmt)
                music_asset = music_result.scalars().first()

                if music_asset:
                    audio_path = settings.project_dir / str(project_id) / music_asset.rel_path
                    if audio_path.exists():
                        clips_dir = settings.project_dir / str(project_id) / "audio_clips"
                        clips_dir.mkdir(parents=True, exist_ok=True)

                        clip_filename = f"scene_{scene.order_index:03d}_{scene.start_time:.2f}_{scene.end_time:.2f}.wav"
                        clip_path = clips_dir / clip_filename
                        rel_clip_path = str(clip_path.relative_to(settings.project_dir))

                        await asyncio.to_thread(
                            _slice_audio, str(audio_path), str(clip_path),
                            scene.start_time, scene.end_time,
                        )

                        scene_params = dict(scene.parameters or {})
                        scene_params["audio_clip_path"] = rel_clip_path
                        scene.parameters = scene_params
                        await session.commit()
                        logger.info(f"Re-sliced audio for scene {scene_id}")
            except Exception as e:
                logger.warning(f"Re-slice audio for scene {scene_id} failed: {e}")

        logger.info(f"Updated scene {scene_id}")

        # Re-load with relationships eagerly loaded for response
        loaded = await _load_scene_with_relations(session, scene_id)
        return SceneResponse.model_validate(loaded)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating scene {scene_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update scene",
        )


class SceneDeleteRequest(BaseModel):
    """Body for DELETE /scenes/{id} — controls how the deleted time slot is
    redistributed across neighboring scenes.

    The default ("previous") matches the legacy AppLayout client-side
    behavior so callers that don't send a body get the same result they
    used to.  New callers can pass "next" to extend the following scene
    backward, or "gap" to leave a hole in the timeline (export will
    render a silent stretch on the previous scene's last frame).
    """
    merge_target: str = Field(default="previous")  # "previous" | "next" | "gap"


async def _reslice_audio_for_scene(scene: Scene, project_id: UUID, session: AsyncSession) -> None:
    """Best-effort: re-slice the master audio for a scene whose start/end
    just changed (because the deleted scene's time slot was absorbed).
    Failure is non-fatal — the scene still works, just without a
    pre-sliced clip until the user re-runs audio analysis."""
    try:
        from sqlmodel import select as _audio_select
        from backend.database.models import Asset, AssetType
        from pathlib import Path
        from backend.config import settings as app_settings
        # Find the master audio asset
        _stmt = (
            _audio_select(Asset)
            .where(Asset.project_id == project_id)
            .where(Asset.asset_type.in_([AssetType.MUSIC, AssetType.NARRATION]))
            .order_by(Asset.created_at.desc())  # type: ignore
        )
        master = (await session.execute(_stmt)).scalars().first()
        if not master:
            return
        # Resolve master audio absolute path.  Asset.rel_path is usually
        # already project-id-prefixed ("<pid>/uploads/x.wav") but legacy
        # rows sometimes store it as just a filename.  Try both shapes
        # so the function works regardless of how the row was created.
        master_abs = app_settings.project_dir / master.rel_path
        if not master_abs.exists():
            master_abs = app_settings.project_dir / str(project_id) / master.rel_path
        if not master_abs.exists():
            return
        # Output: audio_clips/scene_NNN_START_END.wav in the project dir
        clips_dir = app_settings.project_dir / str(project_id) / "audio_clips"
        clips_dir.mkdir(parents=True, exist_ok=True)
        out_name = f"scene_{scene.order_index + 1:03d}_{scene.start_time:.2f}_{scene.end_time:.2f}.wav"
        out_path = clips_dir / out_name
        # Use ffmpeg to slice
        import subprocess
        dur = max(0.0, scene.end_time - scene.start_time)
        if dur <= 0:
            return
        result = subprocess.run(
            ["ffmpeg", "-y", "-ss", f"{scene.start_time:.3f}", "-t", f"{dur:.3f}",
             "-i", str(master_abs), "-acodec", "pcm_s16le", "-ar", "48000", "-ac", "2",
             str(out_path)],
            capture_output=True, text=True, timeout=60,
        )
        if result.returncode == 0 and out_path.exists():
            sc_params = dict(scene.parameters or {})
            sc_params["audio_clip_path"] = str(out_path.relative_to(app_settings.project_dir))
            scene.parameters = sc_params
    except Exception as _audio_err:
        logger.warning(f"Audio re-slice for scene {scene.id} failed (non-fatal): {_audio_err}")


@router.delete(
    "/{scene_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete scene with merge-target control",
)
async def delete_scene(
    project_id: UUID,
    scene_id: UUID,
    request: Optional[SceneDeleteRequest] = None,
    session: AsyncSession = Depends(get_session),
) -> None:
    """Delete a scene and redistribute its time slot.

    Body (optional, JSON):
        {"merge_target": "previous" | "next" | "gap"}

    Behavior:
        - "previous" (DEFAULT): previous scene's end_time is extended to
          cover the deleted scene's time range.  Lyrics in that range
          are absorbed automatically (Whisper words are stored separately
          and queried by time range at export time).  Audio clip for the
          previous scene is re-sliced from master audio.
        - "next": next scene's start_time is moved backward to cover the
          deleted scene's range.  Same audio re-slice + lyric absorption.
        - "gap": just delete.  Other scenes are unchanged; a hole remains
          in the timeline that the export pipeline renders as a silent
          freeze-frame on the previous scene's last frame.

    Edge cases:
        - Deleting the FIRST scene with merge_target="previous" → auto
          falls back to "next".
        - Deleting the LAST scene with merge_target="next" → auto falls
          back to "previous".
        - Deleting the only scene → merge_target ignored (no neighbors).

    After delete:
        - order_index on remaining scenes is re-numbered contiguously
          (0, 1, 2, ...) so the Scenes panel + Timeline render in stable
          order without gaps in the index sequence.
        - The deleted scene's generated assets (images, videos) are left
          in the project's asset library — the user can re-use them.
        - The export cache is invalidated by the cache-key dependency
          on scene durations (no explicit wipe needed).
    """
    try:
        await _get_project_or_404(project_id, session)

        scene = await session.get(Scene, scene_id)
        if not scene or scene.project_id != project_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Scene {scene_id} not found",
            )

        merge_target = (request.merge_target if request else "previous").lower()
        if merge_target not in ("previous", "next", "gap"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid merge_target {merge_target!r}. Use 'previous', 'next', or 'gap'.",
            )

        # Load all scenes for this project ordered by order_index so we
        # can find the previous + next neighbors deterministically.
        from sqlmodel import select as _del_select
        _all_stmt = (
            _del_select(Scene)
            .where(Scene.project_id == project_id)
            .order_by(Scene.order_index.asc())  # type: ignore
        )
        all_scenes = list((await session.execute(_all_stmt)).scalars().all())
        idx = next((i for i, s in enumerate(all_scenes) if s.id == scene.id), -1)
        prev_scene = all_scenes[idx - 1] if idx > 0 else None
        next_scene = all_scenes[idx + 1] if 0 <= idx < len(all_scenes) - 1 else None

        # Resolve merge target with auto-fallback at edges
        effective_target = merge_target
        if merge_target == "previous" and prev_scene is None:
            effective_target = "next" if next_scene is not None else "gap"
            logger.info(
                f"Scene delete: merge_target=previous on first scene → falling back to {effective_target!r}"
            )
        elif merge_target == "next" and next_scene is None:
            effective_target = "previous" if prev_scene is not None else "gap"
            logger.info(
                f"Scene delete: merge_target=next on last scene → falling back to {effective_target!r}"
            )

        # Apply the merge to the absorbing scene BEFORE deleting (so the
        # foreign-key cascade can't interfere).
        absorbing_scene: Optional[Scene] = None
        if effective_target == "previous" and prev_scene is not None:
            prev_scene.end_time = scene.end_time
            sc_params = dict(prev_scene.parameters or {})
            sc_params["extended_via_delete"] = True
            sc_params["extended_at"] = (sc_params.get("extended_at", []) + [{
                "from_scene_id": str(scene.id),
                "from_scene_name": scene.name,
                "absorbed_seconds": round(scene.end_time - scene.start_time, 3),
            }])[-10:]  # keep last 10
            prev_scene.parameters = sc_params
            absorbing_scene = prev_scene
            logger.info(
                f"Scene delete: merged into previous scene {prev_scene.id} "
                f"({prev_scene.name}) — new end_time={prev_scene.end_time:.2f}s"
            )
        elif effective_target == "next" and next_scene is not None:
            next_scene.start_time = scene.start_time
            sc_params = dict(next_scene.parameters or {})
            sc_params["extended_via_delete"] = True
            sc_params["extended_at"] = (sc_params.get("extended_at", []) + [{
                "from_scene_id": str(scene.id),
                "from_scene_name": scene.name,
                "absorbed_seconds": round(scene.end_time - scene.start_time, 3),
            }])[-10:]
            next_scene.parameters = sc_params
            absorbing_scene = next_scene
            logger.info(
                f"Scene delete: merged into next scene {next_scene.id} "
                f"({next_scene.name}) — new start_time={next_scene.start_time:.2f}s"
            )
        else:
            logger.info(f"Scene delete: leaving gap ({scene.end_time - scene.start_time:.2f}s)")

        # Best-effort: re-slice audio for the absorbing scene so the
        # pre-sliced clip on disk matches the new time range.
        if absorbing_scene is not None:
            await _reslice_audio_for_scene(absorbing_scene, project_id, session)

        # Delete the scene (cascades to TimelinePosition, StemSelection,
        # GenerationHistory, Job rows via the cascade_delete=True on the
        # Scene model)
        await session.delete(scene)

        # Re-number order_index on remaining scenes so the sequence is
        # contiguous (0, 1, 2, ...).  Skip the just-deleted scene.
        remaining = [s for s in all_scenes if s.id != scene_id]
        for new_idx, s in enumerate(remaining):
            if s.order_index != new_idx:
                s.order_index = new_idx

        await session.commit()

        logger.info(
            f"Deleted scene {scene_id} (merge_target={merge_target}, "
            f"effective={effective_target}, remaining={len(remaining)})"
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting scene {scene_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to delete scene: {e}",
        )




@router.post(
    "/{scene_id}/stems",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Set stem selection",
)
async def set_stem_selection(
    project_id: UUID,
    scene_id: UUID,
    stems: StemSelectionResponse,
    session: AsyncSession = Depends(get_session),
) -> None:
    """Set stem selection (vocals, drums, bass, other) for a scene.

    Args:
        project_id: UUID of the project.
        scene_id: UUID of the scene.
        stems: Stem selection flags.
        session: Database session.

    Raises:
        HTTPException: If project or scene not found.
    """
    try:
        await _get_project_or_404(project_id, session)

        scene = await session.get(Scene, scene_id)
        if not scene or scene.project_id != project_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Scene {scene_id} not found",
            )

        # Query for existing stem selection
        stmt = select(StemSelection).where(StemSelection.scene_id == scene_id)
        result = await session.execute(stmt)
        stem_selection = result.scalars().first()

        if not stem_selection:
            stem_selection = StemSelection(scene_id=scene_id)
            session.add(stem_selection)

        stem_selection.vocals = stems.vocals
        stem_selection.drums = stems.drums
        stem_selection.bass = stems.bass
        stem_selection.other = stems.other

        await session.commit()
        logger.info(f"Updated stem selection for scene {scene_id}")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error setting stem selection for scene {scene_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to set stem selection",
        )


@router.get(
    "/{scene_id}/versions",
    response_model=list[GenerationHistoryResponse],
    summary="Get generation history",
)
async def get_generation_history(
    project_id: UUID,
    scene_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> list[GenerationHistoryResponse]:
    """Get all generation history versions for a scene.

    Args:
        project_id: UUID of the project.
        scene_id: UUID of the scene.
        session: Database session.

    Returns:
        List of generation history entries ordered by creation date.

    Raises:
        HTTPException: If project or scene not found.
    """
    try:
        await _get_project_or_404(project_id, session)

        scene = await session.get(Scene, scene_id)
        if not scene or scene.project_id != project_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Scene {scene_id} not found",
            )

        stmt = (
            select(GenerationHistory)
            .where(GenerationHistory.scene_id == scene_id)
            .order_by(GenerationHistory.created_at.desc())
        )
        result = await session.execute(stmt)
        history = result.scalars().all()

        return [
            GenerationHistoryResponse.model_validate(h)
            for h in history
        ]
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting generation history for scene {scene_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get generation history",
        )


@router.delete(
    "/{scene_id}/versions/{version_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a generation history version",
)
async def delete_generation_version(
    project_id: UUID,
    scene_id: UUID,
    version_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> None:
    """Delete a generation history entry and its associated file on disk.

    If the deleted version was the chosen preview image, clears chosen_image_path.

    Args:
        project_id: UUID of the project.
        scene_id: UUID of the scene.
        version_id: UUID of the generation history entry.
        session: Database session.

    Raises:
        HTTPException: If project, scene, or version not found.
    """
    try:
        await _get_project_or_404(project_id, session)

        scene = await session.get(Scene, scene_id)
        if not scene or scene.project_id != project_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Scene {scene_id} not found",
            )

        version = await session.get(GenerationHistory, version_id)
        if not version or version.scene_id != scene_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Version {version_id} not found",
            )

        # Delete the file on disk if it exists
        if version.output_path:
            file_path = (settings.project_dir / version.output_path).resolve()
            project_dir_resolved = settings.project_dir.resolve()
            if str(file_path).startswith(str(project_dir_resolved)) and file_path.exists():
                try:
                    file_path.unlink()
                    logger.info(f"Deleted file: {file_path}")
                except OSError as e:
                    logger.warning(f"Could not delete file {file_path}: {e}")

            # If this was the chosen preview image, clear it
            scene_params = dict(scene.parameters or {})
            if scene_params.get("chosen_image_path") == version.output_path:
                scene_params.pop("chosen_image_path", None)
                scene.parameters = scene_params
                logger.info(f"Cleared chosen_image_path for scene {scene_id}")

        await session.delete(version)
        await session.commit()

        logger.info(f"Deleted generation version {version_id} from scene {scene_id}")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting version {version_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to delete version",
        )


# ── Scene Media Upload ───────────────────────────────────────────────


@router.post(
    "/{scene_id}/upload",
    response_model=GenerationHistoryResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Upload an image or video to a scene gallery",
)
async def upload_scene_media(
    project_id: UUID,
    scene_id: UUID,
    file: UploadFile = File(...),
    media_type: str = Form("image"),
    frame_type: str = Form("first"),
    session: AsyncSession = Depends(get_session),
) -> GenerationHistoryResponse:
    """Upload a user-provided image or video into a scene's gallery.

    Creates an Asset record and a GenerationHistory entry so the upload
    appears alongside AI-generated versions. Automatically sets the
    uploaded file as the scene's chosen preview.

    Args:
        project_id: UUID of the project.
        scene_id: UUID of the scene.
        file: The image or video file to upload.
        media_type: "image" or "video".
        frame_type: "first" or "last" (only used for images).
        session: Database session.

    Returns:
        The GenerationHistory entry for the uploaded file.
    """
    import hashlib
    from uuid import uuid4

    try:
        await _get_project_or_404(project_id, session)

        scene = await session.get(Scene, scene_id)
        if not scene or scene.project_id != project_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Scene {scene_id} not found",
            )

        # Read file content
        content = await file.read()
        if not content:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Uploaded file is empty",
            )

        file_sha256 = hashlib.sha256(content).hexdigest()
        ext = Path(file.filename or "upload").suffix or (".png" if media_type == "image" else ".mp4")
        safe_name = f"{file_sha256[:12]}_{Path(file.filename or 'upload').stem}{ext}"

        # Determine destination path
        if media_type == "video":
            type_dir = "video"
            asset_type = AssetType.GENERATED_VIDEO
            job_type = JobType.VIDEO
        else:
            type_dir = "images"
            asset_type = AssetType.GENERATED_IMAGE
            job_type = JobType.IMAGE

        rel_dir = Path(str(project_id)) / type_dir
        rel_path = str(rel_dir / safe_name)

        # Write file to disk
        abs_dir = settings.project_dir / rel_dir
        abs_dir.mkdir(parents=True, exist_ok=True)
        abs_path = settings.project_dir / rel_path
        abs_path.write_bytes(content)
        logger.info(f"Uploaded scene media: {rel_path} ({len(content)} bytes)")

        # Create Asset record
        asset = Asset(
            id=uuid4(),
            project_id=project_id,
            filename=file.filename or safe_name,
            rel_path=rel_path,
            asset_type=asset_type,
            sha256=file_sha256,
            file_size=len(content),
            meta={"content_type": file.content_type or "application/octet-stream", "source": "user_upload"},
        )
        session.add(asset)

        # Create GenerationHistory entry so it shows in the gallery
        gen_history = GenerationHistory(
            id=uuid4(),
            project_id=project_id,
            scene_id=scene_id,
            job_type=job_type,
            prompt_id="user_upload",
            status="completed",
            parameters={
                "frame_type": frame_type if media_type == "image" else None,
                "source": "user_upload",
                "original_filename": file.filename,
            },
            output_path=rel_path,
            completed_at=datetime.utcnow(),
        )
        session.add(gen_history)

        # Auto-set as the chosen preview for the scene
        scene_params = dict(scene.parameters or {})
        if media_type == "video":
            scene_params["chosen_video_path"] = rel_path
        elif frame_type == "last":
            scene_params["chosen_last_frame_path"] = rel_path
        else:
            scene_params["chosen_image_path"] = rel_path
        scene.parameters = scene_params

        await session.commit()
        await session.refresh(gen_history)

        logger.info(
            f"Created gallery entry for uploaded {media_type} "
            f"(scene={scene_id}, frame_type={frame_type})"
        )

        return GenerationHistoryResponse.model_validate(gen_history)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error uploading scene media: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to upload scene media: {str(e)}",
        )


@router.get(
    "/{scene_id}/prev-last-frame",
    response_model=PrevLastFrameResponse,
    summary="Get previous scene's last frame",
)
async def get_prev_last_frame(
    project_id: UUID,
    scene_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> PrevLastFrameResponse:
    """Get the last frame image path from the previous scene.

    Resolves the last frame based on the previous scene's source type:
    - If previous scene source is "video": use video_last_frame_path
    - If previous scene source is "image": use chosen_last_frame_path, fallback to chosen_image_path

    Args:
        project_id: UUID of the project.
        scene_id: UUID of the current scene.
        session: Database session.

    Returns:
        Object with image_path (or None if no previous scene or no frame available).

    Raises:
        HTTPException: 404 if current scene is first scene, 500 on other errors.
    """
    try:
        await _get_project_or_404(project_id, session)

        scene = await session.get(Scene, scene_id)
        if not scene or scene.project_id != project_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Scene {scene_id} not found",
            )

        # Find the previous scene by order_index - 1
        prev_order_index = scene.order_index - 1
        if prev_order_index < 0:
            # This is the first scene
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="No previous scene (this is the first scene)",
            )

        stmt = (
            select(Scene)
            .where(Scene.project_id == project_id)
            .where(Scene.order_index == prev_order_index)
        )
        result = await session.execute(stmt)
        prev_scene = result.scalars().first()

        if not prev_scene:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"No scene with order_index {prev_order_index}",
            )

        # Resolve the image path based on scene source type
        image_path = None
        scene_params = prev_scene.parameters or {}
        scene_source_type = scene_params.get("scene_source_type", "image")

        if scene_source_type == "video":
            # Try video_last_frame_path
            image_path = scene_params.get("video_last_frame_path")
        else:
            # For image source type, try chosen_last_frame_path first, then chosen_image_path
            image_path = scene_params.get("chosen_last_frame_path")
            if not image_path:
                image_path = scene_params.get("chosen_image_path")

        logger.info(
            f"Resolved previous scene's last frame for scene {scene_id}: {image_path}"
        )

        return PrevLastFrameResponse(image_path=image_path)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting previous scene's last frame: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get previous scene's last frame",
        )


# ── Retrim (Re-run Post-Processing) ─────────────────────────────────


class RetrimResponse(BaseModel):
    """Response for retrim operation."""
    success: bool
    message: str
    video_path: Optional[str] = None


class RetrimAllResponse(BaseModel):
    """Response for retrim-all operation."""
    status: str
    total_scenes: int
    processed: int
    skipped: int
    errors: int
    details: list[str]


async def _retrim_scene(
    scene: "Scene",
    project_id: UUID,
    session: AsyncSession,
) -> tuple[bool, str]:
    """Re-run post-processing pipeline on a single scene's existing video.

    Pipeline: auto-trim (video_tail) → color correction → audio mux → last-frame extraction.

    Returns (success, message).
    """
    sc_params = scene.parameters or {}
    # Find source video — prefer untrimmed, fall back to chosen
    untrimmed_rel = sc_params.get("video_untrimmed_path")
    chosen_rel = sc_params.get("chosen_video_path")

    if not untrimmed_rel and not chosen_rel:
        return False, f"Scene {scene.order_index}: no video to retrim"

    base_dir = settings.project_dir

    def _resolve(rel: str) -> str:
        if rel.startswith("/") or rel.startswith("\\"):
            return rel
        return str(base_dir / rel)

    # Determine source video path — prefer untrimmed BUT only if it belongs
    # to the SAME generation as chosen_video_path. video_untrimmed_path is
    # never cleared when the user switches active videos or regenerates via
    # a different method, so it can point to a completely different (stale)
    # generation. Verify by checking that the untrimmed filename stem minus
    # "_untrimmed" matches the chosen filename stem.
    source_rel = None
    if untrimmed_rel and chosen_rel:
        from pathlib import PurePosixPath
        untrimmed_stem = PurePosixPath(untrimmed_rel).stem.replace("_untrimmed", "")
        chosen_stem = PurePosixPath(chosen_rel).stem
        if untrimmed_stem == chosen_stem:
            untrimmed_abs = _resolve(untrimmed_rel)
            if Path(untrimmed_abs).exists():
                source_rel = untrimmed_rel
                logger.info(f"Retrim scene {scene.order_index}: using untrimmed source (matches chosen)")
        else:
            logger.warning(
                f"Retrim scene {scene.order_index}: untrimmed '{untrimmed_stem}' does NOT match "
                f"chosen '{chosen_stem}' — untrimmed is from a different generation, skipping it"
            )
    elif untrimmed_rel and not chosen_rel:
        untrimmed_abs = _resolve(untrimmed_rel)
        if Path(untrimmed_abs).exists():
            source_rel = untrimmed_rel

    if not source_rel and chosen_rel:
        chosen_abs = _resolve(chosen_rel)
        if Path(chosen_abs).exists():
            source_rel = chosen_rel
            logger.info(f"Retrim scene {scene.order_index}: using chosen_video_path as source")

    if not source_rel:
        return False, f"Scene {scene.order_index}: video file(s) not found on disk"

    source_abs = _resolve(source_rel)
    scene_duration = scene.end_time - scene.start_time
    if scene_duration <= 0:
        return False, f"Scene {scene.order_index}: invalid duration ({scene_duration})"

    project_dir = settings.project_dir / str(project_id)

    # Read project FPS
    project = await session.get(Project, project_id)
    project_fps = 24
    if project and project.settings:
        project_fps = project.settings.get("project_fps", 24) or 24

    import shutil
    from backend.services.video.ffmpeg import trim_video, extract_frame, get_media_info, _ensure_frame_dimensions

    # Determine output path — write to same location as chosen_video_path
    if chosen_rel:
        output_abs = _resolve(chosen_rel)
    else:
        # No chosen path — derive from untrimmed
        from pathlib import PurePosixPath
        stem = PurePosixPath(untrimmed_rel).stem.replace("_untrimmed", "")
        ext = PurePosixPath(untrimmed_rel).suffix
        output_dir = Path(source_abs).parent
        output_abs = str(output_dir / f"{stem}{ext}")

    steps_done = []

    # Detect V2V / use_prev_lf_as_ff for first-frame skip during trim
    is_v2v_retrim = sc_params.get("video_mode") == "v2v_extend"
    uses_prev_lf_retrim = bool(sc_params.get("use_prev_lf_as_ff"))
    should_skip_ff_retrim = is_v2v_retrim or uses_prev_lf_retrim

    # Step 1: Auto-trim to scene duration
    if untrimmed_rel and source_rel == untrimmed_rel:
        try:
            trimmed_tmp = Path(output_abs).parent / (Path(output_abs).stem + "_retrim_tmp" + Path(output_abs).suffix)
            await asyncio.to_thread(
                trim_video,
                source_abs,
                str(trimmed_tmp),
                scene_duration,
                skip_first_frame=should_skip_ff_retrim,
            )
            shutil.move(str(trimmed_tmp), output_abs)
            steps_done.append("trimmed")
            # Update the dispatcher_skipped_first_frame flag
            if should_skip_ff_retrim:
                sc_params_update = dict(scene.parameters or {})
                sc_params_update["dispatcher_skipped_first_frame"] = True
                scene.parameters = sc_params_update
            skip_msg = " + skipped frame 0" if should_skip_ff_retrim else ""
            logger.info(f"Retrim scene {scene.order_index}: trimmed to {scene_duration:.2f}s{skip_msg}")
        except Exception as e:
            logger.warning(f"Retrim scene {scene.order_index}: trim failed: {e}")
            # If trim fails but source != output, copy source to output
            if source_abs != output_abs:
                shutil.copy2(source_abs, output_abs)
    elif source_abs != output_abs:
        # Source is chosen (already trimmed) — copy to work on
        shutil.copy2(source_abs, output_abs)

    # Step 2: Color correction
    try:
        from backend.database.models import AppSettings as DBAppSettings
        cc_stmt = select(AppSettings).where(AppSettings.id == 1)
        cc_result = await session.execute(cc_stmt)
        cc_settings_obj = cc_result.scalars().first()
        cc_enabled = cc_settings_obj.color_correction_enabled if cc_settings_obj and hasattr(cc_settings_obj, 'color_correction_enabled') and cc_settings_obj.color_correction_enabled is not None else True

        if cc_enabled:
            ref_image_rel = sc_params.get("chosen_image_path")
            if ref_image_rel:
                ref_image_abs = _resolve(ref_image_rel)
                if Path(ref_image_abs).exists():
                    from backend.services.video.color_correction import color_correct_video
                    corrected = await asyncio.to_thread(color_correct_video, output_abs, ref_image_abs)
                    if corrected:
                        steps_done.append("color-corrected")
                        logger.info(f"Retrim scene {scene.order_index}: color correction applied")
    except Exception as e:
        logger.warning(f"Retrim scene {scene.order_index}: color correction failed: {e}")

    # Step 3: Audio mux
    try:
        audio_abs = None
        scene_audio = sc_params.get("audio_clip_path")
        if scene_audio:
            candidate = settings.project_dir / scene_audio
            if candidate.exists():
                audio_abs = candidate

        # Auto-slice from master audio if clip doesn't exist
        if not audio_abs and scene.start_time is not None and scene.end_time is not None:
            from backend.services.video.ffmpeg import slice_audio
            music_stmt = (
                select(Asset)
                .where(Asset.project_id == project_id)
                .where(Asset.asset_type == AssetType.MUSIC)
                .where(~Asset.rel_path.contains("stems/"))
            )
            music_result = await session.execute(music_stmt)
            music_asset = music_result.scalars().first()
            if music_asset:
                audio_path = settings.project_dir / str(project_id) / music_asset.rel_path
                if audio_path.exists():
                    clips_dir = settings.project_dir / str(project_id) / "audio_clips"
                    clips_dir.mkdir(parents=True, exist_ok=True)
                    clip_filename = f"scene_{scene.order_index:03d}_{scene.start_time:.2f}_{scene.end_time:.2f}.wav"
                    clip_path = clips_dir / clip_filename
                    await asyncio.to_thread(slice_audio, str(audio_path), str(clip_path), scene.start_time, scene.end_time)
                    audio_abs = clip_path

        # Fall back to full master audio
        if not audio_abs:
            music_stmt2 = (
                select(Asset)
                .where(Asset.project_id == project_id)
                .where(Asset.asset_type == AssetType.MUSIC)
                .order_by(Asset.created_at.desc())
            )
            result2 = await session.execute(music_stmt2)
            music_asset2 = result2.scalars().first()
            if music_asset2:
                candidate2 = settings.project_dir / str(project_id) / music_asset2.rel_path
                if candidate2.exists():
                    audio_abs = candidate2

        if audio_abs:
            from backend.services.video.ffmpeg import mux_audio
            mux_tmp = Path(output_abs).parent / (Path(output_abs).stem + "_muxed" + Path(output_abs).suffix)
            await asyncio.to_thread(mux_audio, output_abs, str(audio_abs), str(mux_tmp))
            shutil.move(str(mux_tmp), output_abs)
            steps_done.append("audio-muxed")
            logger.info(f"Retrim scene {scene.order_index}: audio muxed")
    except Exception as e:
        logger.warning(f"Retrim scene {scene.order_index}: audio mux failed: {e}")

    # Step 4: Extract last frame
    try:
        lf_filename = Path(output_abs).stem + "_lastframe.png"
        lf_path = Path(output_abs).parent / lf_filename

        # Determine extraction time
        if untrimmed_rel:
            untrimmed_abs_path = _resolve(untrimmed_rel)
            if Path(untrimmed_abs_path).exists():
                extract_time = max(0, scene_duration - (1.0 / project_fps))
                source_for_lf = untrimmed_abs_path
            else:
                info = await asyncio.to_thread(get_media_info, output_abs)
                vid_duration = info.get("duration", 0)
                vid_fps = info.get("fps", project_fps)
                extract_time = max(0, vid_duration - (1.0 / vid_fps))
                source_for_lf = output_abs
        else:
            info = await asyncio.to_thread(get_media_info, output_abs)
            vid_duration = info.get("duration", 0)
            vid_fps = info.get("fps", project_fps)
            extract_time = max(0, vid_duration - (1.0 / vid_fps))
            source_for_lf = output_abs

        await asyncio.to_thread(extract_frame, source_for_lf, str(lf_path), extract_time)

        # Ensure dimensions match (get target from scene params)
        target_w = sc_params.get("width", 0)
        target_h = sc_params.get("height", 0)
        if target_w > 0 and target_h > 0:
            await asyncio.to_thread(_ensure_frame_dimensions, str(lf_path), target_w, target_h)

        lf_rel_path = str(lf_path.relative_to(settings.project_dir))
        new_params = dict(scene.parameters or {})
        new_params["video_last_frame_path"] = lf_rel_path
        scene.parameters = new_params
        steps_done.append("last-frame-extracted")
        logger.info(f"Retrim scene {scene.order_index}: last frame extracted at {extract_time:.4f}s")
    except Exception as e:
        logger.warning(f"Retrim scene {scene.order_index}: last frame extraction failed: {e}")

    # Update chosen_video_path if it changed
    if chosen_rel:
        # chosen_video_path is already correct
        pass
    else:
        new_params = dict(scene.parameters or {})
        new_rel = str(Path(output_abs).relative_to(settings.project_dir))
        new_params["chosen_video_path"] = new_rel
        scene.parameters = new_params

    await session.commit()

    if steps_done:
        return True, f"Scene {scene.order_index}: {', '.join(steps_done)}"
    else:
        return True, f"Scene {scene.order_index}: no post-processing needed"


@router.post(
    "/{scene_id}/retrim",
    response_model=RetrimResponse,
    summary="Re-run post-processing on a scene's video",
)
async def retrim_scene(
    project_id: UUID,
    scene_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> RetrimResponse:
    """Re-run the post-processing pipeline on a single scene's existing video.

    Pipeline: auto-trim → color correction → audio mux → last-frame extraction.
    Does NOT regenerate the video — only re-processes the existing file.

    Args:
        project_id: UUID of the project.
        scene_id: UUID of the scene.
        session: Database session.

    Returns:
        Whether retrim was applied and what steps completed.
    """
    try:
        await _get_project_or_404(project_id, session)

        scene = await session.get(Scene, scene_id)
        if not scene or scene.project_id != project_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Scene {scene_id} not found in project {project_id}",
            )

        success, message = await _retrim_scene(scene, project_id, session)
        video_path = (scene.parameters or {}).get("chosen_video_path")

        return RetrimResponse(
            success=success,
            message=message,
            video_path=video_path,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error retrimming scene {scene_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Retrim failed: {str(e)}",
        )


# ── Retrim-all is on the project-level prefix ──────────────────────────

_retrim_all_router = APIRouter(
    prefix="/api/projects/{project_id}",
    tags=["scenes"],
)


@_retrim_all_router.post(
    "/retrim-all",
    response_model=RetrimAllResponse,
    summary="Re-run post-processing on all scenes",
)
async def retrim_all_scenes(
    project_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> RetrimAllResponse:
    """Re-run post-processing pipeline on all scenes sequentially.

    Processes scenes in order_index order. Skips scenes without videos.

    Args:
        project_id: UUID of the project.
        session: Database session.

    Returns:
        Summary of what was processed.
    """
    try:
        await _get_project_or_404(project_id, session)

        stmt = select(Scene).where(Scene.project_id == project_id).order_by(Scene.order_index)
        result = await session.execute(stmt)
        scenes = result.scalars().all()

        processed = 0
        skipped = 0
        errors = 0
        details: list[str] = []

        for sc in scenes:
            sc_params = sc.parameters or {}
            has_video = sc_params.get("chosen_video_path") or sc_params.get("video_untrimmed_path")
            if not has_video:
                skipped += 1
                details.append(f"Scene {sc.order_index}: skipped (no video)")
                continue

            try:
                success, message = await _retrim_scene(sc, project_id, session)
                if success:
                    processed += 1
                else:
                    skipped += 1
                details.append(message)
            except Exception as e:
                errors += 1
                details.append(f"Scene {sc.order_index}: error — {str(e)}")
                logger.error(f"Retrim-all scene {sc.order_index} failed: {e}")

        return RetrimAllResponse(
            status="done",
            total_scenes=len(scenes),
            processed=processed,
            skipped=skipped,
            errors=errors,
            details=details,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error retrimming all scenes for project {project_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Retrim-all failed: {str(e)}",
        )


# ── Manual Color Correction ──────────────────────────────────────────


class ColorCorrectionResponse(BaseModel):
    """Response for color correction operation."""
    corrected: bool
    message: str
    video_path: Optional[str] = None


@router.post(
    "/{scene_id}/color-correct",
    response_model=ColorCorrectionResponse,
    summary="Color-correct a scene's active video",
)
async def color_correct_scene_video(
    project_id: UUID,
    scene_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> ColorCorrectionResponse:
    """Apply color correction to a scene's active video.

    Uses the scene's chosen first-frame image as the colour reference.
    If the scene is not the first and has no chosen_image_path but has
    use_prev_lf_as_ff, falls back to the previous scene's last frame.

    Args:
        project_id: UUID of the project.
        scene_id: UUID of the scene.
        session: Database session.

    Returns:
        Whether correction was applied and the resulting video path.
    """
    try:
        scene = await session.get(Scene, scene_id)
        if not scene or scene.project_id != project_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Scene {scene_id} not found in project {project_id}",
            )

        sc_params = scene.parameters or {}
        video_rel = sc_params.get("chosen_video_path") or sc_params.get("generated_video_path")
        if not video_rel:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Scene has no active video to color-correct",
            )

        project_dir = settings.project_dir / str(project_id)
        video_abs = str(project_dir / video_rel) if not video_rel.startswith("/") else video_rel

        if not Path(video_abs).exists():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Video file not found on disk",
            )

        # Resolve reference image: chosen first frame, or previous scene's last frame
        ref_image_rel = sc_params.get("chosen_image_path")
        if not ref_image_rel and sc_params.get("use_prev_lf_as_ff"):
            # Try previous scene's last frame
            stmt = (
                select(Scene)
                .where(Scene.project_id == project_id)
                .where(Scene.order_index < scene.order_index)
                .order_by(Scene.order_index.desc())
                .limit(1)
            )
            result = await session.execute(stmt)
            prev_scene = result.scalars().first()
            if prev_scene:
                prev_params = prev_scene.parameters or {}
                ref_image_rel = prev_params.get("video_last_frame_path")

        if not ref_image_rel:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No reference image available for color correction (no chosen first frame or previous scene last frame)",
            )

        ref_image_abs = str(project_dir / ref_image_rel) if not ref_image_rel.startswith("/") else ref_image_rel
        if not Path(ref_image_abs).exists():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Reference image file not found on disk",
            )

        # Run color correction in a thread (FFmpeg + PIL work)
        from backend.services.video.color_correction import color_correct_video

        corrected = await asyncio.to_thread(
            color_correct_video,
            video_abs,
            ref_image_abs,
        )

        if corrected:
            logger.info(f"Manual color correction applied to scene {scene_id}")
            return ColorCorrectionResponse(
                corrected=True,
                message="Color correction applied successfully",
                video_path=video_rel,
            )
        else:
            return ColorCorrectionResponse(
                corrected=False,
                message="No correction needed — colors are already within acceptable range",
                video_path=video_rel,
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error color-correcting scene {scene_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Color correction failed: {str(e)}",
        )
