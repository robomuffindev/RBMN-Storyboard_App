"""API endpoints for persistent batch run tracking.

BatchRun records are created when Auto Gen starts and updated as each scene
completes.  They survive app restarts and can be resumed from the last
completed scene.
"""
import logging
from datetime import datetime
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from backend.database import get_session
from backend.database.models import BatchRun, BatchRunStatus, Project
from backend.utils.background import track as _track_task

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/batch-runs", tags=["batch-runs"])


# ── Response models ─────────────────────────────────────────────────────────

class BatchRunSummary(BaseModel):
    """Lightweight batch run for list views."""
    id: str
    project_id: str
    project_name: str
    mode: str
    status: str
    total_scenes: int
    completed_scenes: int
    current_scene_name: Optional[str] = None
    current_step: Optional[str] = None
    error_count: int = 0
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    elapsed_ms: int = 0
    last_asset_url: Optional[str] = None
    last_asset_scene_name: Optional[str] = None
    created_at: Optional[str] = None


class ActiveJobInfo(BaseModel):
    """Live snapshot of a currently-running job for the batch detail screen.

    Lets the batch UI show "Worker X is rendering scene Y at 84%" instead
    of a static current_step that hasn't changed in 5 minutes during a
    long LTX video render — which made the run look frozen even when
    everything was healthy.
    """
    job_id: str
    job_type: str  # "image" / "video"
    scene_id: Optional[str] = None
    scene_name: Optional[str] = None
    worker_url: Optional[str] = None
    workflow_type: Optional[str] = None
    progress_percent: float = 0.0
    current_node: Optional[str] = None
    started_at: Optional[str] = None
    two_pass_phase: Optional[str] = None  # "base" | "composite" | None


class BatchRunDetail(BatchRunSummary):
    """Full batch run detail with per-scene results and error log."""
    scene_results: dict = {}
    error_log: list = []
    run_settings: dict = {}
    step_log: list = []
    # Live snapshot of in-flight jobs in this batch's project.  Empty
    # when nothing is running.  Populated each detail-fetch so the
    # frontend's regular poll picks up sub-job progress without needing
    # a separate channel.
    active_jobs: list[ActiveJobInfo] = []


# ── Endpoints ───────────────────────────────────────────────────────────────

@router.get("")
async def list_batch_runs(
    project_id: Optional[str] = None,
    limit: int = 50,
    session: AsyncSession = Depends(get_session),
):
    """List batch runs, most recent first. Optionally filter by project."""
    stmt = select(BatchRun).order_by(BatchRun.created_at.desc()).limit(limit)
    if project_id:
        stmt = stmt.where(BatchRun.project_id == project_id)
    result = await session.execute(stmt)
    runs = result.scalars().all()

    return [
        BatchRunSummary(
            id=str(r.id),
            project_id=str(r.project_id),
            project_name=r.project_name,
            mode=r.mode,
            status=r.status,
            total_scenes=r.total_scenes,
            completed_scenes=r.completed_scenes,
            current_scene_name=r.current_scene_name,
            current_step=r.current_step,
            error_count=len(r.error_log) if r.error_log else 0,
            started_at=r.started_at.isoformat() if r.started_at else None,
            completed_at=r.completed_at.isoformat() if r.completed_at else None,
            elapsed_ms=r.elapsed_ms,
            last_asset_url=r.last_asset_url,
            last_asset_scene_name=r.last_asset_scene_name,
            created_at=r.created_at.isoformat() if r.created_at else None,
        )
        for r in runs
    ]


@router.get("/{batch_run_id}")
async def get_batch_run(
    batch_run_id: str,
    session: AsyncSession = Depends(get_session),
):
    """Get full detail of a batch run including per-scene results.

    Also embeds a live snapshot of any RUNNING jobs in the same
    project (with per-job progress %, current_node, worker, two-pass
    phase), so the batch detail screen can show that a 5-minute LTX
    render is actually progressing instead of looking frozen.
    """
    stmt = select(BatchRun).where(BatchRun.id == batch_run_id)
    result = await session.execute(stmt)
    r = result.scalars().first()
    if not r:
        raise HTTPException(status_code=404, detail="Batch run not found")

    # ── Live in-flight job snapshot ──────────────────────────────
    # Query Jobs with status=RUNNING for this batch's project, then
    # overlay the dispatcher's in-memory progress dict to surface live
    # render % + current ComfyUI node.  Both reads are cheap (indexed
    # status filter + dict lookup).
    active_jobs_out: list[ActiveJobInfo] = []
    try:
        from backend.database.models import Job as _Job, JobStatus as _JS, Scene as _Scene
        from backend.services.jobs.dispatcher import get_live_job_progress
        from sqlmodel import select as _sm_select
        # Project_id on BatchRun is a string; Jobs uses UUID
        try:
            from uuid import UUID as _UUID
            _pid_uuid = _UUID(str(r.project_id))
        except Exception:
            _pid_uuid = None
        if _pid_uuid is not None:
            jobs_stmt = (
                _sm_select(_Job)
                .where(_Job.project_id == _pid_uuid)
                .where(_Job.status == _JS.RUNNING)
                .order_by(_Job.started_at.desc())  # type: ignore
            )
            jobs_result = await session.execute(jobs_stmt)
            running_jobs = list(jobs_result.scalars().all())
            # Resolve scene names in one go to avoid N+1
            scene_ids = [j.scene_id for j in running_jobs if j.scene_id]
            scene_map: dict = {}
            if scene_ids:
                sc_stmt = _sm_select(_Scene).where(_Scene.id.in_(scene_ids))  # type: ignore
                sc_result = await session.execute(sc_stmt)
                for sc in sc_result.scalars().all():
                    scene_map[sc.id] = sc.name or f"Scene {sc.order_index + 1}"
            for j in running_jobs:
                live = get_live_job_progress(str(j.id)) or {}
                params = j.parameters or {}
                active_jobs_out.append(ActiveJobInfo(
                    job_id=str(j.id),
                    job_type=str(j.job_type or ""),
                    scene_id=str(j.scene_id) if j.scene_id else None,
                    scene_name=scene_map.get(j.scene_id) if j.scene_id else None,
                    worker_url=j.worker_url,
                    workflow_type=params.get("workflow_type"),
                    progress_percent=float(live.get("percent") or 0.0),
                    current_node=live.get("current_node"),
                    started_at=j.started_at.isoformat() if j.started_at else None,
                    two_pass_phase=params.get("two_pass_phase"),
                ))
    except Exception as live_err:
        # Live overlay is best-effort — never break the detail endpoint
        # on snapshot errors.
        import logging as _logging
        _logging.getLogger(__name__).debug(
            f"batch-run detail: active_jobs snapshot skipped: {live_err}"
        )

    return BatchRunDetail(
        id=str(r.id),
        project_id=str(r.project_id),
        project_name=r.project_name,
        mode=r.mode,
        status=r.status,
        total_scenes=r.total_scenes,
        completed_scenes=r.completed_scenes,
        current_scene_name=r.current_scene_name,
        current_step=r.current_step,
        error_count=len(r.error_log) if r.error_log else 0,
        started_at=r.started_at.isoformat() if r.started_at else None,
        completed_at=r.completed_at.isoformat() if r.completed_at else None,
        elapsed_ms=r.elapsed_ms,
        last_asset_url=r.last_asset_url,
        last_asset_scene_name=r.last_asset_scene_name,
        created_at=r.created_at.isoformat() if r.created_at else None,
        scene_results=r.scene_results or {},
        error_log=r.error_log or [],
        run_settings=r.run_settings or {},
        step_log=r.step_log or [],
        active_jobs=active_jobs_out,
    )


@router.post("/{batch_run_id}/resume")
async def resume_batch_run(
    batch_run_id: str,
    session: AsyncSession = Depends(get_session),
):
    """Resume a batch run from where it left off.

    Checks scene_results to find the first incomplete scene and restarts
    the auto-gen from that point.  Only works for runs with status
    'failed', 'cancelled', or 'paused'.
    """
    stmt = select(BatchRun).where(BatchRun.id == batch_run_id)
    result = await session.execute(stmt)
    run = result.scalars().first()
    if not run:
        raise HTTPException(status_code=404, detail="Batch run not found")

    if run.status not in (BatchRunStatus.FAILED, BatchRunStatus.CANCELLED, BatchRunStatus.PAUSED):
        raise HTTPException(
            status_code=400,
            detail=f"Cannot resume a batch run with status '{run.status}'. "
                   f"Only failed, cancelled, or paused runs can be resumed.",
        )

    # Mark as running again
    run.status = BatchRunStatus.RUNNING
    run.current_step = "resuming..."
    session.add(run)
    await session.commit()

    # Trigger the auto-gen for this project, starting from the resume point
    # The generation.py code will check scene_results to skip already-done scenes
    from backend.api.generation import _resume_sequential_auto_gen
    import asyncio

    _track_task(
        _resume_sequential_auto_gen(
            batch_run_id=str(run.id),
            project_id=str(run.project_id),
            mode=run.mode,
            run_settings=run.run_settings or {},
            scene_results=run.scene_results or {},
        )
    )

    return {"status": "resuming", "batch_run_id": str(run.id)}


@router.delete("")
async def delete_batch_runs_bulk(
    status: Optional[str] = None,
    session: AsyncSession = Depends(get_session),
):
    """Delete batch run records in bulk.

    - No status filter: deletes ALL non-running batch runs.
    - status=completed: deletes only completed runs.
    - status=failed: deletes failed and cancelled runs.
    """
    stmt = select(BatchRun).where(BatchRun.status != BatchRunStatus.RUNNING)

    if status == "completed":
        stmt = stmt.where(BatchRun.status == BatchRunStatus.COMPLETED)
    elif status == "failed":
        stmt = stmt.where(
            BatchRun.status.in_([BatchRunStatus.FAILED, BatchRunStatus.CANCELLED])
        )
    # else: delete all non-running

    result = await session.execute(stmt)
    runs = result.scalars().all()
    count = len(runs)

    for run in runs:
        await session.delete(run)

    await session.commit()
    logger.info("Bulk deleted %d batch runs (filter=%s)", count, status or "all")
    return {"status": "deleted", "deleted_count": count}


@router.delete("/{batch_run_id}")
async def delete_batch_run(
    batch_run_id: str,
    session: AsyncSession = Depends(get_session),
):
    """Delete a batch run record."""
    stmt = select(BatchRun).where(BatchRun.id == batch_run_id)
    result = await session.execute(stmt)
    run = result.scalars().first()
    if not run:
        raise HTTPException(status_code=404, detail="Batch run not found")

    if run.status == BatchRunStatus.RUNNING:
        raise HTTPException(
            status_code=400,
            detail="Cannot delete a running batch run. Cancel it first.",
        )

    await session.delete(run)
    await session.commit()
    return {"status": "deleted", "batch_run_id": batch_run_id}
