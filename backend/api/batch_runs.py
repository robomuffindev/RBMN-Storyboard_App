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


class BatchRunDetail(BatchRunSummary):
    """Full batch run detail with per-scene results and error log."""
    scene_results: dict = {}
    error_log: list = []
    run_settings: dict = {}


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
    """Get full detail of a batch run including per-scene results."""
    stmt = select(BatchRun).where(BatchRun.id == batch_run_id)
    result = await session.execute(stmt)
    r = result.scalars().first()
    if not r:
        raise HTTPException(status_code=404, detail="Batch run not found")

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

    asyncio.create_task(
        _resume_sequential_auto_gen(
            batch_run_id=str(run.id),
            project_id=str(run.project_id),
            mode=run.mode,
            run_settings=run.run_settings or {},
            scene_results=run.scene_results or {},
        )
    )

    return {"status": "resuming", "batch_run_id": str(run.id)}


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
