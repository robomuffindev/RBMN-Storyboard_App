"""
Unified Job Queue — DB-Backed with Async Notification

Single source of truth is the Job table in the main SQLite database.
The in-memory asyncio.Event is used purely to wake the dispatcher
when a new job is enqueued, avoiding polling delays.
"""

import asyncio
import logging
from typing import Optional, List
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from backend.database.models import Job, JobStatus

logger = logging.getLogger(__name__)


class JobQueue:
    """
    DB-backed job queue with async notification.

    The database Job table is the single source of truth.
    An asyncio.Event wakes the dispatch loop instantly when
    a new job is created, while the loop also polls periodically
    as a fallback (crash recovery, retries, etc.).
    """

    def __init__(self, session_factory):
        """
        Initialize the job queue.

        Args:
            session_factory: An async session factory (e.g. async_session from database module).
        """
        self._session_factory = session_factory
        self._notify = asyncio.Event()
        logger.info("JobQueue initialized (DB-backed)")

    def notify(self) -> None:
        """Signal that a new job is available. Non-blocking."""
        self._notify.set()

    async def wait_for_jobs(self, timeout: float = 5.0) -> None:
        """
        Wait for a notification or timeout.

        Args:
            timeout: Max seconds to wait before returning anyway.
        """
        try:
            await asyncio.wait_for(self._notify.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            pass
        finally:
            self._notify.clear()

    async def dequeue(self) -> Optional[Job]:
        """
        Fetch the highest-priority PENDING job from the database,
        atomically set it to RUNNING, and return it.

        Uses optimistic locking (WHERE status = 'pending' in the UPDATE)
        to prevent the same job from being dispatched to two workers
        in concurrent dequeue calls.

        Priority: higher number = more urgent, then oldest created_at wins ties.

        Returns:
            A Job instance in RUNNING state, or None if nothing is pending.
        """
        from sqlalchemy import update
        from datetime import datetime

        async with self._session_factory() as session:
            # Step 1: Find the best candidate
            stmt = (
                select(Job)
                .where(Job.status == JobStatus.PENDING)
                .order_by(Job.priority.desc(), Job.created_at.asc())
                .limit(1)
            )
            result = await session.execute(stmt)
            job = result.scalars().first()

            if not job:
                return None

            # Step 2: Atomically UPDATE only if still PENDING (optimistic lock)
            upd = (
                update(Job)
                .where(Job.id == job.id, Job.status == JobStatus.PENDING)
                .values(status=JobStatus.RUNNING, started_at=datetime.utcnow())
            )
            upd_result = await session.execute(upd)
            await session.commit()

            # If rowcount == 0, another worker already claimed this job
            if upd_result.rowcount == 0:
                logger.info(f"Job {job.id} already claimed by another dequeue — skipping")
                return None

            await session.refresh(job)
            logger.info(f"Dequeued job {job.id} (type={job.job_type}, priority={job.priority})")
            return job

    async def mark_running(self, job_id: UUID) -> Optional[Job]:
        """
        Mark a job as RUNNING.

        Args:
            job_id: Job UUID.

        Returns:
            Updated Job, or None if not found.
        """
        from datetime import datetime

        async with self._session_factory() as session:
            job = await session.get(Job, job_id)
            if not job:
                return None

            job.status = JobStatus.RUNNING
            job.started_at = datetime.utcnow()
            session.add(job)
            await session.commit()
            await session.refresh(job)
            return job

    async def mark_done(self, job_id: UUID, result: Optional[dict] = None) -> Optional[Job]:
        """
        Mark a job as DONE.

        Args:
            job_id: Job UUID.
            result: Optional result dict (output paths, prompt_id, etc.).

        Returns:
            Updated Job, or None if not found.
        """
        from datetime import datetime

        async with self._session_factory() as session:
            job = await session.get(Job, job_id)
            if not job:
                return None

            job.status = JobStatus.DONE
            job.completed_at = datetime.utcnow()
            if result:
                job.result = result
            session.add(job)
            await session.commit()
            await session.refresh(job)

            # Drop any live-progress entry — job is no longer in flight
            try:
                from backend.services.jobs.dispatcher import clear_live_job_progress
                clear_live_job_progress(str(job_id))
            except Exception:
                pass

            logger.info(f"Job {job_id} marked DONE")
            return job

    async def mark_failed(self, job_id: UUID, error: str) -> Optional[Job]:
        """
        Mark a job as FAILED.

        Args:
            job_id: Job UUID.
            error: Error message.

        Returns:
            Updated Job, or None if not found.
        """
        from datetime import datetime

        async with self._session_factory() as session:
            job = await session.get(Job, job_id)
            if not job:
                return None

            job.status = JobStatus.FAILED
            job.completed_at = datetime.utcnow()
            job.error = error
            session.add(job)
            await session.commit()
            await session.refresh(job)

            try:
                from backend.services.jobs.dispatcher import clear_live_job_progress
                clear_live_job_progress(str(job_id))
            except Exception:
                pass

            logger.info(f"Job {job_id} marked FAILED: {error}")
            return job

    async def mark_retrying(self, job_id: UUID) -> Optional[Job]:
        """
        Increment retry_count on a job but keep it in RUNNING status.

        The job stays RUNNING so the dispatch loop's ``dequeue()`` won't
        pick it up — the retry code in ``_process_job_with_retry`` handles
        re-processing directly without going through the queue.

        Previous approach set the job to PENDING, which caused a race:
        the dispatch loop would grab it during its 5-second poll cycle,
        creating a second concurrent task for the same prompt.

        Args:
            job_id: Job UUID.

        Returns:
            Updated Job, or None if not found.
        """
        async with self._session_factory() as session:
            job = await session.get(Job, job_id)
            if not job:
                return None

            # Keep RUNNING — don't reset to PENDING
            job.retry_count += 1
            job.error = None
            session.add(job)
            await session.commit()
            await session.refresh(job)

            logger.info(f"Job {job_id} retrying (attempt {job.retry_count})")
            return job

    async def cancel(self, job_id: UUID) -> bool:
        """
        Cancel a PENDING or RUNNING job.

        Args:
            job_id: Job UUID.

        Returns:
            True if cancelled, False if not found or already terminal.
        """
        async with self._session_factory() as session:
            job = await session.get(Job, job_id)
            if not job:
                return False

            if job.status not in (JobStatus.PENDING, JobStatus.RUNNING):
                logger.warning(f"Cannot cancel job {job_id} with status {job.status}")
                return False

            job.status = JobStatus.CANCELLED
            session.add(job)
            await session.commit()

            logger.info(f"Cancelled job {job_id}")
            return True

    async def get_pending_count(self) -> int:
        """Get number of pending jobs."""
        async with self._session_factory() as session:
            stmt = select(Job).where(Job.status == JobStatus.PENDING)
            result = await session.execute(stmt)
            return len(result.scalars().all())

    async def recover_running_jobs(self) -> int:
        """
        On startup, handle incomplete jobs from a previous session.

        Behaviour:
          - PENDING jobs are cancelled.
          - RUNNING jobs WITH `prompt_id` + `worker_url` are LEFT in
            RUNNING state so the dispatcher's retry fast-path can
            reconnect via WebSocket and pick up the result, preserving
            expensive in-flight ComfyUI renders across a restart.
          - RUNNING jobs without `prompt_id` are cancelled.

        Returns:
            Number of jobs marked CANCELLED.
        """
        async with self._session_factory() as session:
            stmt = select(Job).where(
                Job.status.in_([JobStatus.PENDING, JobStatus.RUNNING])
            )
            result = await session.execute(stmt)
            stale_jobs = result.scalars().all()

            cancelled = 0
            reconnect = 0
            for job in stale_jobs:
                if (
                    job.status == JobStatus.RUNNING
                    and job.prompt_id
                    and job.worker_url
                ):
                    reconnect += 1
                    continue
                job.status = JobStatus.CANCELLED
                job.error = "Cancelled: application restarted"
                session.add(job)
                cancelled += 1

            if cancelled > 0 or reconnect > 0:
                await session.commit()
                logger.info(
                    f"Startup cleanup: cancelled {cancelled} stale jobs, "
                    f"kept {reconnect} RUNNING jobs for reconnect"
                )

            return cancelled

    async def get_running_jobs_for_reconnect(self) -> list[Job]:
        """Return RUNNING jobs with an in-flight ComfyUI prompt.

        Called by the dispatcher on startup so we can reconnect via the
        retry fast-path instead of cancelling expensive renders.
        """
        async with self._session_factory() as session:
            stmt = (
                select(Job)
                .where(
                    Job.status == JobStatus.RUNNING,
                    Job.prompt_id.is_not(None),
                    Job.worker_url.is_not(None),
                )
                .order_by(Job.priority.desc(), Job.created_at.asc())
            )
            result = await session.execute(stmt)
            return list(result.scalars().all())
