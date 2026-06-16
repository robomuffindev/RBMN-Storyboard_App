"""Job queue management endpoints for RBMN Storyboard App."""
import asyncio
import json
import logging
from datetime import datetime
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from backend.database import get_session
from backend.database.models import Job, Project, JobStatus
from backend.services.jobs.queue import JobQueue

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/jobs", tags=["jobs"])


# Pydantic models for request/response
class JobResponse(BaseModel):
    """Response model for a job."""

    id: UUID
    project_id: UUID
    scene_id: Optional[UUID]
    # Snapshot of the scene's name at the time the response is built.
    # This is included so the Generation Queue UI can still display
    # "Scene 79" on a completed job after the underlying Scene row has
    # been deleted or after the user switches to a different project.
    # Populated by the list endpoint via a single bulk lookup.  When
    # the scene_id is null or no longer exists, this field is null.
    scene_name: Optional[str] = None
    job_type: str
    status: str
    priority: int
    worker_url: Optional[str]
    prompt_id: Optional[str] = None  # ComfyUI prompt id — useful for debugging stuck jobs
    parameters: dict
    result: Optional[dict] = None
    error: Optional[str] = None
    created_at: datetime
    started_at: Optional[datetime]
    completed_at: Optional[datetime]
    retry_count: int

    class Config:
        from_attributes = True


class JobProgressEvent(BaseModel):
    """Server-sent event for job progress."""

    event: str  # job_started, job_progress, job_completed, job_failed
    job_id: str
    data: dict


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
    response_model=list[JobResponse],
    summary="List jobs",
)
async def list_jobs(
    project_id: Optional[UUID] = None,
    status_filter: Optional[str] = None,
    session: AsyncSession = Depends(get_session),
) -> list[JobResponse]:
    """List all jobs, optionally filtered by project_id and/or status."""
    try:
        query = select(Job)

        if project_id:
            await _get_project_or_404(project_id, session)
            query = query.where(Job.project_id == project_id)

        if status_filter:
            query = query.where(Job.status == status_filter)

        query = query.order_by(Job.created_at.desc())

        result = await session.execute(query)
        jobs = list(result.scalars().all())

        # Bulk-resolve scene names so the Generation Queue UI can still
        # show "Scene 79" on completed jobs even after the underlying
        # Scene row has been deleted or after the user switched projects
        # (in which case the frontend's local `scenes` array no longer
        # contains the relevant rows).  Single SELECT for every distinct
        # scene_id referenced by the returned jobs.
        scene_name_by_id: dict[UUID, str] = {}
        _scene_ids = list({j.scene_id for j in jobs if j.scene_id is not None})
        if _scene_ids:
            from backend.database.models import Scene
            _sn_q = await session.execute(
                select(Scene.id, Scene.name).where(Scene.id.in_(_scene_ids))  # type: ignore[arg-type]
            )
            for _sid, _sname in _sn_q.all():
                scene_name_by_id[_sid] = _sname

        out: list[JobResponse] = []
        for j in jobs:
            r = JobResponse.model_validate(j)
            if j.scene_id and j.scene_id in scene_name_by_id:
                r.scene_name = scene_name_by_id[j.scene_id]
            elif j.scene_id and isinstance(j.parameters, dict):
                # Fallback: a job-create site may have snapshot the name
                # into parameters before the Scene row was deleted.
                _pname = j.parameters.get("scene_name")
                if isinstance(_pname, str) and _pname:
                    r.scene_name = _pname
            out.append(r)
        return out
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error listing jobs: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to list jobs",
        )


# ---- SSE stream MUST be registered BEFORE /{job_id} ----
# Otherwise FastAPI tries to parse "stream" as a UUID and returns 422.

@router.get(
    "/stream",
    summary="Stream job progress",
)
async def stream_job_progress(
    request: Request,
    project_id: Optional[UUID] = None,
):
    """Server-Sent Events (SSE) endpoint for real-time job progress streaming."""

    async def event_generator():
        """Generate SSE events for job progress.

        Each SSE connection gets its own subscriber queue from the
        broadcaster, ensuring every connection receives every event
        (previously a single asyncio.Queue meant only one connection
        got each event, causing the others to miss completions).
        """
        subscriber = None
        try:
            try:
                from backend.services.jobs.dispatcher import job_event_broadcaster
            except (ImportError, AttributeError):
                logger.warning("Job event broadcaster not available, using heartbeat only")
                yield "data: {\"event\": \"stream_ready\"}\n\n"
                while True:
                    if await request.is_disconnected():
                        break
                    await asyncio.sleep(30)
                    yield ": heartbeat\n\n"
                return

            # Subscribe this connection
            subscriber = job_event_broadcaster.subscribe()

            # Send initial ready event
            yield "data: {\"event\": \"stream_ready\"}\n\n"

            while True:
                # Check disconnect before waiting for events
                try:
                    if await request.is_disconnected():
                        break
                except Exception:
                    break

                try:
                    event = await asyncio.wait_for(subscriber.get(), timeout=15.0)

                    if project_id and event.get("project_id") != str(project_id):
                        continue

                    yield f"data: {json.dumps(event)}\n\n"

                except asyncio.TimeoutError:
                    # Send heartbeat — if client disconnected, the yield will
                    # raise GeneratorExit or ConnectionError
                    yield ": heartbeat\n\n"

        except (asyncio.CancelledError, GeneratorExit):
            # Normal disconnect
            pass
        except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError, OSError):
            # Client disconnected — suppress these completely
            pass
        except Exception as e:
            logger.error(f"Error in job progress stream: {e}")
        finally:
            # Always clean up the subscriber
            if subscriber is not None:
                try:
                    from backend.services.jobs.dispatcher import job_event_broadcaster
                    job_event_broadcaster.unsubscribe(subscriber)
                except Exception:
                    pass

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# ---- Routes with {job_id} path param below ----

@router.post(
    "/purge",
    summary="Purge all queued and running jobs",
)
async def purge_jobs(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Cancel all PENDING and RUNNING jobs.

    This is the manual equivalent of the startup cleanup — wipes the
    queue clean so nothing is processing or waiting to process.
    """
    try:
        stmt = select(Job).where(
            Job.status.in_([JobStatus.PENDING, JobStatus.RUNNING])
        )
        result = await session.execute(stmt)
        stale_jobs = list(result.scalars().all())

        count = 0
        for job in stale_jobs:
            job.status = JobStatus.CANCELLED
            job.error = "Cancelled: queue purged by user"
            session.add(job)
            count += 1

        if count > 0:
            await session.commit()

        # Also cancel any in-progress sequential auto-gen tasks
        from backend.api.generation import _seq_auto_jobs
        for pid, info in _seq_auto_jobs.items():
            if info.get("status") == "running":
                info["status"] = "cancelled"
                info["current_step"] = "cancelled — queue purged"

        logger.info(f"Purged {count} jobs (PENDING/RUNNING → CANCELLED)")
        return {"purged": count, "message": f"Cancelled {count} queued/running jobs"}

    except Exception as e:
        logger.error(f"Error purging jobs: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to purge jobs",
        )


@router.get(
    "/{job_id}",
    response_model=JobResponse,
    summary="Get job details",
)
async def get_job(
    job_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> JobResponse:
    """Get detailed job information including status and progress."""
    try:
        job = await session.get(Job, job_id)
        if not job:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Job {job_id} not found",
            )

        r = JobResponse.model_validate(job)
        # Resolve scene_name so the Generation Queue chip stays visible
        # on this job even after the Scene row is deleted.  Same logic
        # as list_jobs but for a single record.
        if job.scene_id is not None:
            from backend.database.models import Scene
            _s = await session.get(Scene, job.scene_id)
            if _s is not None:
                r.scene_name = _s.name
            elif isinstance(job.parameters, dict):
                _pname = job.parameters.get("scene_name")
                if isinstance(_pname, str) and _pname:
                    r.scene_name = _pname
        return r
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting job {job_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get job",
        )


@router.post(
    "/{job_id}/cancel",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Cancel job",
)
async def cancel_job(
    job_id: UUID,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> None:
    """Cancel a pending or running job.

    For running jobs, also sends an interrupt to the ComfyUI worker
    to stop the in-progress generation.
    """
    try:
        job = await session.get(Job, job_id)
        if not job:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Job {job_id} not found",
            )

        # Only PENDING and RUNNING jobs can be cancelled.  Allowing DONE/FAILED
        # here let stale cancels flip completed jobs to CANCELLED and emit a
        # misleading job_failed SSE event — confusing the retry UX.
        if job.status not in [JobStatus.PENDING, JobStatus.RUNNING]:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Cannot cancel job in status {job.status}",
            )

        # If running on a ComfyUI worker, try to interrupt it
        if job.status == JobStatus.RUNNING and job.worker_url:
            try:
                from backend.services.comfyui.dispatcher import ComfyDispatcher
                comfy_dispatcher = getattr(request.app.state, "comfy_dispatcher", None)
                if comfy_dispatcher and job.worker_url in comfy_dispatcher.clients:
                    client = comfy_dispatcher.clients[job.worker_url]
                    client.interrupt()
                    logger.info(f"Sent interrupt to {job.worker_url} for job {job_id}")
            except Exception as interrupt_err:
                logger.warning(f"Failed to interrupt ComfyUI worker for job {job_id}: {interrupt_err}")

        job.status = JobStatus.CANCELLED
        job.error = "Cancelled by user"

        await session.commit()
        logger.info(f"Cancelled job {job_id}")

        # Emit SSE event so frontend updates
        try:
            from backend.services.jobs.dispatcher import job_event_broadcaster
            job_event_broadcaster.put_nowait({
                "event": "job_failed",
                "job_id": str(job_id),
                "job": {"id": str(job_id)},
                "error": "Cancelled by user",
            })
        except Exception:
            pass
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error cancelling job {job_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to cancel job",
        )


@router.post(
    "/{job_id}/retry",
    response_model=JobResponse,
    summary="Retry failed job",
)
async def retry_job(
    job_id: UUID,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> JobResponse:
    """Retry a failed or cancelled job by resetting it to PENDING status."""
    try:
        job = await session.get(Job, job_id)
        if not job:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Job {job_id} not found",
            )

        retryable_statuses = [JobStatus.FAILED, JobStatus.CANCELLED]
        if job.status not in retryable_statuses:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Cannot retry job with status {job.status}. Must be failed or cancelled.",
            )

        job.status = JobStatus.PENDING
        job.retry_count += 1
        job.error = None

        await session.commit()
        await session.refresh(job)

        logger.info(f"Retried job {job_id} (retry count: {job.retry_count})")

        # Notify the dispatcher that a job is available
        job_queue: JobQueue = request.app.state.job_queue
        job_queue.notify()

        r = JobResponse.model_validate(job)
        # Resolve scene_name so the Generation Queue chip stays visible
        # on the retried job — same fallback chain as list_jobs / get_job.
        if job.scene_id is not None:
            from backend.database.models import Scene
            _s = await session.get(Scene, job.scene_id)
            if _s is not None:
                r.scene_name = _s.name
            elif isinstance(job.parameters, dict):
                _pname = job.parameters.get("scene_name")
                if isinstance(_pname, str) and _pname:
                    r.scene_name = _pname
        return r
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error retrying job {job_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retry job",
        )


@router.delete(
    "/{job_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete job",
)
async def delete_job(
    job_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> None:
    """Delete a job record from the database.

    Only completed, failed, or cancelled jobs can be deleted.
    Running/pending jobs must be cancelled first.
    """
    try:
        job = await session.get(Job, job_id)
        if not job:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Job {job_id} not found",
            )

        deletable_statuses = [JobStatus.DONE, JobStatus.FAILED, JobStatus.CANCELLED]
        if job.status not in deletable_statuses:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Cannot delete job with status {job.status}. Cancel it first.",
            )

        await session.delete(job)
        await session.commit()
        logger.info(f"Deleted job {job_id}")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting job {job_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to delete job",
        )
