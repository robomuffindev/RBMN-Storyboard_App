"""
Job Dispatcher

Main orchestrator for processing jobs: dequeuing from DB, worker selection,
workflow preparation, submission, monitoring, and result handling.

Uses the unified DB-backed JobQueue — no separate SQLite or dataclass.
"""

import asyncio
import copy
import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Optional, Callable, Any, Dict
from uuid import UUID

from backend.database.models import Job, JobStatus, JobType, Asset, AssetType, Scene, Project, WorkflowConfig
from ..comfyui.client import ComfyUIVRAMError, ComfyUIConnectionError
from ..comfyui.workflow import (
    prepare_klein_workflow,
    prepare_ltx_workflow,
    prepare_v2v_extend_workflow,
    prepare_v2v_pass1_workflow,
    prepare_v2v_pass2_workflow,
    prepare_transition_workflow,
    prepare_zimage_workflow,
    prepare_sequencer_workflow,
    prepare_workflow_from_config,
    stamp_vhs_unique_prefix,
    flatten_group_nodes,
    strip_non_essential_nodes,
    validate_node_types,
    remove_missing_nodes,
)
from ..video.ffmpeg import extract_last_frame
from .queue import JobQueue

logger = logging.getLogger(__name__)

# Pub/sub broadcaster for SSE streaming to multiple clients.
# Each SSE connection subscribes its own queue; events are broadcast to all.
class JobEventBroadcaster:
    """Broadcast job events to all connected SSE clients."""

    def __init__(self) -> None:
        self._subscribers: list[asyncio.Queue] = []
        self._lock: asyncio.Lock | None = None  # created lazily in async context

    def subscribe(self) -> asyncio.Queue:
        """Create a new subscriber queue for an SSE connection."""
        q: asyncio.Queue = asyncio.Queue(maxsize=200)
        self._subscribers.append(q)
        logger.debug(f"SSE subscriber added (total: {len(self._subscribers)})")
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        """Remove a subscriber queue when SSE connection closes."""
        try:
            self._subscribers.remove(q)
            logger.debug(f"SSE subscriber removed (total: {len(self._subscribers)})")
        except ValueError:
            pass

    def put_nowait(self, event: dict) -> None:
        """Broadcast an event to ALL subscribers."""
        dead: list[asyncio.Queue] = []
        for q in self._subscribers:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                dead.append(q)
        # Remove subscribers whose queues are full (stale connections)
        for q in dead:
            try:
                self._subscribers.remove(q)
                logger.warning("Removed stale SSE subscriber (queue full)")
            except ValueError:
                pass


job_event_broadcaster = JobEventBroadcaster()

# Keep backward-compatible name for imports in jobs.py
job_event_queue = job_event_broadcaster


class JobDispatcher:
    """
    Main job dispatcher and orchestrator.

    Workflow:
    1. Wait for notification or poll interval
    2. Dequeue highest-priority PENDING job from DB
    3. Build the ComfyUI workflow from job.parameters + WorkflowConfig
    4. Select ComfyUI worker via ComfyDispatcher
    5. Submit workflow, monitor via WebSocket
    6. Handle completion/failure with retry logic
    7. Emit SSE progress events
    """

    MAX_RETRIES = 3
    RETRY_BASE_WAIT = 2  # seconds, exponential backoff base

    def __init__(
        self,
        job_queue: JobQueue,
        comfy_dispatcher,
        session_factory,
        on_progress: Optional[Callable[[str, Dict[str, Any]], None]] = None,
    ):
        """
        Initialize job dispatcher.

        Args:
            job_queue: Unified DB-backed JobQueue.
            comfy_dispatcher: ComfyDispatcher instance.
            session_factory: Async session factory for DB access.
            on_progress: Optional callback for progress updates.
        """
        self.job_queue = job_queue
        self.comfy_dispatcher = comfy_dispatcher
        self._session_factory = session_factory
        self.on_progress = on_progress or self._default_progress
        self.running = False
        # Track in-flight dispatch tasks for concurrent processing
        self._active_tasks: set[asyncio.Task] = set()
        # Track VHS unique prefixes for fallback download when history is empty
        self._vhs_prefixes: Dict[str, str] = {}

        logger.info("JobDispatcher initialized")

    # Map internal event names to frontend-expected SSE event names
    _SSE_EVENT_MAP = {
        "processing_started": "job_started",
        "worker_assigned": "job_worker_assigned",
        "progress": "job_progress",
        "executing": "job_progress",
        "completed": "job_completed",
        "failed_final": "job_failed",
        "retrying": "job_retrying",
    }

    def _default_progress(self, job_id: str, event: Dict[str, Any]) -> None:
        """Default progress callback — logs and pushes to SSE queue."""
        logger.info(f"[{job_id}] {json.dumps(event)}")

        event_name = event.get("event", "unknown")
        sse_event: Dict[str, Any] = {
            "event": self._SSE_EVENT_MAP.get(event_name, event_name),
            "job_id": job_id,
            "job": {"id": job_id},
        }

        # Always include job_type if present in the event
        if "job_type" in event:
            sse_event["job_type"] = event["job_type"]

        # Attach progress-specific fields the frontend expects
        if event_name == "progress":
            sse_event["progress"] = event.get("percent", 0)
        elif event_name == "executing":
            sse_event["node"] = event.get("node")
        elif event_name == "worker_assigned":
            sse_event["worker_url"] = event.get("worker_url")
            sse_event["scene_id"] = event.get("scene_id")
        elif event_name == "completed":
            sse_event["project_id"] = event.get("project_id")
            sse_event["scene_id"] = event.get("scene_id")
            if event.get("character_gen"):
                sse_event["character_gen"] = True
            if event.get("v2v_scene_a_id"):
                sse_event["v2v_scene_a_id"] = event["v2v_scene_a_id"]
        elif event_name == "failed_final":
            sse_event["error"] = event.get("error", "Job failed")

        try:
            job_event_queue.put_nowait(sse_event)
        except asyncio.QueueFull:
            logger.warning("Job event queue full, dropping event")

    def stop(self) -> None:
        """Stop the dispatch loop gracefully."""
        self.running = False
        logger.info("JobDispatcher stopped")

    def _count_available_slots(self) -> int:
        """Return number of available worker slots for concurrent dispatch.

        Each healthy ComfyUI worker can handle at least one job.  We allow
        up to ``max(1, healthy_workers)`` concurrent dispatch tasks so that
        every worker can be utilised simultaneously.
        """
        healthy = [
            w for w in self.comfy_dispatcher.workers.values() if w.healthy
        ]
        # At least 1 slot so the loop always makes progress
        return max(1, len(healthy))

    async def dispatch_loop(self) -> None:
        """
        Main dispatch loop — concurrent, multi-worker.

        Runs continuously:
        1. Recover any interrupted jobs on first iteration.
        2. Wait for notification or timeout.
        3. Dequeue as many jobs as there are free worker slots and process
           them concurrently.  Each job runs in its own asyncio Task so
           multiple ComfyUI servers are utilised in parallel.
        """
        self.running = True

        # Crash recovery on startup
        recovered = await self.job_queue.recover_running_jobs()
        if recovered:
            logger.info(f"Recovered {recovered} jobs from previous run")

        while self.running:
            try:
                # Wait for signal or timeout
                await self.job_queue.wait_for_jobs(timeout=5.0)

                if not self.running:
                    break

                # Clean up finished tasks
                done_tasks = {t for t in self._active_tasks if t.done()}
                for t in done_tasks:
                    # Surface exceptions so they don't get silently swallowed
                    if t.exception():
                        logger.exception(
                            "Dispatch task raised an exception",
                            exc_info=t.exception(),
                        )
                self._active_tasks -= done_tasks

                # Determine how many more jobs we can start
                max_slots = self._count_available_slots()
                free_slots = max_slots - len(self._active_tasks)

                # Dequeue and dispatch as many jobs as we have free slots
                while free_slots > 0:
                    job = await self.job_queue.dequeue()
                    if not job:
                        break  # no more pending jobs

                    task = asyncio.create_task(
                        self._dispatch_single_job(job),
                        name=f"dispatch-{job.id}",
                    )
                    self._active_tasks.add(task)
                    free_slots -= 1

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.exception(f"Unexpected error in dispatch loop: {e}")
                await asyncio.sleep(5)

        # Graceful shutdown: wait for in-flight tasks
        if self._active_tasks:
            logger.info(
                "Dispatch loop stopping — waiting for %d in-flight jobs",
                len(self._active_tasks),
            )
            await asyncio.gather(*self._active_tasks, return_exceptions=True)

    async def _free_worker_memory(self, worker_url: str) -> None:
        """Call ComfyUI /free endpoint to release GPU/CPU memory on a worker.

        This prevents OOM errors during long auto-gen runs where accumulated
        memory from previous generations isn't freed between jobs.
        """
        client = self.comfy_dispatcher.clients.get(worker_url)
        if not client:
            return
        try:
            await asyncio.to_thread(client.free_memory)
            logger.info(f"Freed memory on worker {worker_url}")
        except Exception as e:
            logger.warning(f"Failed to free memory on {worker_url}: {e}")

    async def _dispatch_single_job(self, job: Job) -> None:
        """Process a single job end-to-end (runs as a concurrent task)."""
        job_id_str = str(job.id)
        try:
            logger.info(f"Processing job {job_id_str} (type={job.job_type})")
            self.on_progress(job_id_str, {
                "event": "processing_started",
                "job_type": job.job_type,
                "scene_id": str(job.scene_id) if job.scene_id else None,
                "project_id": str(job.project_id),
            })

            success = await self._process_job_with_retry(job)

            if success:
                logger.info(f"Job {job_id_str} completed successfully")

                # Free GPU/CPU memory after video jobs to prevent OOM on
                # consecutive generations.  Video jobs (LTX) accumulate
                # significant memory in the VAE decoder and sampler that
                # ComfyUI doesn't always release between executions.
                if job.job_type in ("video", "ltx", "ltx_v2v_extend", "ltx_transition"):
                    worker_url = None
                    async with self._session_factory() as session:
                        db_job = await session.get(Job, job.id)
                        if db_job:
                            worker_url = db_job.worker_url
                    if worker_url:
                        await self._free_worker_memory(worker_url)

                completed_event: Dict[str, Any] = {
                    "event": "completed",
                    "project_id": str(job.project_id),
                    "scene_id": str(job.scene_id) if job.scene_id else None,
                }
                if job.parameters and job.parameters.get("character_gen"):
                    completed_event["character_gen"] = True
                # Pass V2V scene A ID so frontend can refresh scene A too.
                # _handle_completed_job stores this in job params after trim-A.
                async with self._session_factory() as session:
                    refreshed_job = await session.get(Job, job.id)
                    if refreshed_job and refreshed_job.parameters:
                        v2v_a_id = refreshed_job.parameters.get("v2v_scene_a_id")
                        if v2v_a_id:
                            completed_event["v2v_scene_a_id"] = v2v_a_id
                self.on_progress(job_id_str, completed_event)
            else:
                logger.error(f"Job {job_id_str} failed after retries")
                self.on_progress(job_id_str, {"event": "failed_final"})
        except Exception as e:
            logger.exception(f"Dispatch task error for job {job_id_str}: {e}")
            self.on_progress(job_id_str, {
                "event": "failed_final",
                "error": str(e),
            })

    async def _process_job_with_retry(self, job: Job) -> bool:
        """
        Process a job with retry logic.

        Retries on VRAM and connection errors only.  The job stays in
        RUNNING status during retries (never PENDING) so the dispatch
        loop won't pick it up and create a competing task.

        Args:
            job: DB Job model instance.

        Returns:
            True if successful, False if failed.
        """
        max_retries = self.MAX_RETRIES
        attempt = 0
        job_id_str = str(job.id)

        while attempt < max_retries:
            try:
                await self._process_job(job)
                return True

            except ComfyUIVRAMError as e:
                attempt += 1
                logger.warning(f"VRAM error on {job_id_str} (attempt {attempt}/{max_retries}): {e}")

                # Force ComfyUI to release GPU/CPU memory before retry
                worker_url = None
                async with self._session_factory() as session:
                    db_job = await session.get(Job, job.id)
                    if db_job:
                        worker_url = db_job.worker_url
                if worker_url:
                    await self._free_worker_memory(worker_url)

                if attempt < max_retries:
                    wait_time = self.RETRY_BASE_WAIT ** attempt + (attempt * 10)
                    self.on_progress(job_id_str, {
                        "event": "retrying",
                        "attempt": attempt,
                        "max_retries": max_retries,
                        "reason": "VRAM error",
                        "wait_seconds": wait_time,
                    })
                    await self.job_queue.mark_retrying(job.id)
                    await asyncio.sleep(wait_time)
                    # Re-read the job from DB (stays RUNNING, not re-queued)
                    async with self._session_factory() as session:
                        job = await session.get(Job, job.id)
                        if not job:
                            return False
                else:
                    await self.job_queue.mark_failed(job.id, f"VRAM error after {max_retries} retries: {e}")
                    return False

            except ComfyUIConnectionError as e:
                attempt += 1
                logger.warning(f"Connection error on {job_id_str} (attempt {attempt}/{max_retries}): {e}")
                if attempt < max_retries:
                    wait_time = self.RETRY_BASE_WAIT ** attempt + (attempt * 10)
                    self.on_progress(job_id_str, {
                        "event": "retrying",
                        "attempt": attempt,
                        "max_retries": max_retries,
                        "reason": "Connection error",
                        "wait_seconds": wait_time,
                    })
                    await self.job_queue.mark_retrying(job.id)
                    await asyncio.sleep(wait_time)
                    # Re-read the job from DB (stays RUNNING, not re-queued)
                    async with self._session_factory() as session:
                        job = await session.get(Job, job.id)
                        if not job:
                            return False
                else:
                    await self.job_queue.mark_failed(job.id, f"Connection error after {max_retries} retries: {e}")
                    return False

            except Exception as e:
                # Non-retryable
                logger.error(f"Non-retryable error on {job_id_str}: {e}")
                await self.job_queue.mark_failed(job.id, str(e))
                return False

        return False

    async def _process_job(self, job: Job) -> None:
        """
        Process a single job end-to-end:
        1. Build workflow from parameters + WorkflowConfig
        2. Select worker
        3. Submit to ComfyUI
        4. Monitor via WebSocket
        5. Save result

        On retry: if the job already has a prompt_id (meaning it was already
        submitted to ComfyUI), skip directly to monitoring instead of
        re-submitting.  This prevents duplicate workflows piling up in
        ComfyUI's queue when the WebSocket connection drops.

        Args:
            job: DB Job model instance (already in RUNNING state).

        Raises:
            ComfyUIVRAMError, ComfyUIConnectionError: Retryable.
            Exception: Non-retryable.
        """
        job_id_str = str(job.id)
        params = job.parameters or {}

        # Debug: log two-pass state at the start of processing
        logger.info(
            f"[{job_id_str}] _process_job START: two_pass={params.get('two_pass')}, "
            f"two_pass_phase={params.get('two_pass_phase')}, "
            f"scene_id={job.scene_id}, "
            f"two_pass_character_ref_ids={params.get('two_pass_character_ref_ids')}"
        )

        # ── Fast-path for retries: if already submitted, skip to monitoring ──
        existing_prompt_id = job.prompt_id
        existing_worker_url = job.worker_url
        if existing_prompt_id and existing_worker_url:
            logger.info(
                f"[{job_id_str}] Retry detected — already submitted as prompt "
                f"{existing_prompt_id} on {existing_worker_url}. "
                f"Skipping re-build/re-submit, going straight to monitoring."
            )
            # Make sure the worker/client is still available
            worker = self.comfy_dispatcher.workers.get(existing_worker_url)
            client = self.comfy_dispatcher.clients.get(existing_worker_url)
            if worker and client:
                # First check if the prompt already completed while we were retrying
                try:
                    # get_history() already unwraps the prompt_id key,
                    # so `history` is the inner dict: {outputs: {...}, ...}
                    history = client.get_history(existing_prompt_id)
                    if history and history.get("outputs"):
                        logger.info(
                            f"[{job_id_str}] Prompt {existing_prompt_id} already completed "
                            f"(found in history on retry). Processing outputs."
                        )
                        # Jump straight to output handling
                        await self._handle_completed_job(
                            job, history, worker, client
                        )
                        return
                except Exception as hist_err:
                    logger.warning(
                        f"[{job_id_str}] History check on retry failed: {hist_err}. "
                        f"Will try to reconnect WS."
                    )

                # Prompt still running — reconnect WebSocket and monitor
                self.on_progress(job_id_str, {
                    "event": "worker_assigned",
                    "worker_url": existing_worker_url,
                    "scene_id": str(job.scene_id) if job.scene_id else None,
                    "job_type": job.job_type,
                })
                await self._monitor_and_complete(
                    job, existing_prompt_id, worker, client
                )
                return
            else:
                logger.warning(
                    f"[{job_id_str}] Worker {existing_worker_url} no longer available. "
                    f"Cannot resume — will re-submit."
                )
                # Clear prompt_id so we don't try to resume a stale prompt
                async with self._session_factory() as session:
                    db_job = await session.get(Job, job.id)
                    if db_job:
                        db_job.prompt_id = None
                        db_job.worker_url = None
                        session.add(db_job)
                        await session.commit()

        # Step 1: Build the workflow
        workflow = await self._build_workflow(job)

        # Step 1a: Flatten group node IDs so all nodes are top-level.
        # ComfyUI group nodes use "X:Y" composite IDs. Some execution engines
        # skip top-level nodes (like VHS_VideoCombine) that depend on outputs
        # from inside a group node. Flattening converts "X:Y" → "X_Y" so the
        # execution engine sees all nodes as top-level and runs them all.
        workflow = flatten_group_nodes(workflow)

        # Step 1a.5: Strip non-essential utility/debug nodes (GPU cleanup, image comparers)
        # that can cause validation errors after flattening or on different server configs.
        strip_non_essential_nodes(workflow)

        # Step 1a.9: For two-pass base (Pass 1), re-enhance the prompt for scene-only generation.
        # The user may have manually enhanced with CHARACTER language before selecting two-pass mode.
        # Pass 1 should focus on environment/lighting/composition ONLY, so re-enhance with base system prompt.
        is_two_pass_base = params.get("two_pass") and params.get("two_pass_phase") == "base"
        if is_two_pass_base and job.scene_id:
            try:
                base_prompt = params.get("prompt", "")
                async with self._session_factory() as session:
                    enhanced_base_prompt = await self._build_two_pass_base_prompt(
                        session=session,
                        project_id=job.project_id,
                        scene_id=job.scene_id,
                        user_prompt=base_prompt,
                        job_id_str=job_id_str,
                    )
                    # Update the prompt in params
                    params["prompt"] = enhanced_base_prompt
                    # Also store the original user prompt
                    params["two_pass_original_prompt"] = base_prompt
                    params["two_pass_scene_prompt"] = enhanced_base_prompt
                    # Rebuild workflow with the new prompt
                    workflow = await self._build_workflow(job)
                    # Re-apply post-build transformations
                    workflow = flatten_group_nodes(workflow)
                    strip_non_essential_nodes(workflow)
                    vhs_prefix = stamp_vhs_unique_prefix(workflow, job_id_str[:8])
                    if vhs_prefix:
                        self._vhs_prefixes[job_id_str] = vhs_prefix
                logger.info(f"[{job_id_str}] Two-pass base prompt re-enhanced. Original: {base_prompt[:60]}... → Enhanced: {enhanced_base_prompt[:60]}...")
            except Exception as e:
                logger.warning(f"[{job_id_str}] Failed to re-enhance base prompt: {e} — using original prompt")

        # Step 1b: Stamp a unique filename prefix on VHS_VideoCombine nodes.
        # Some ComfyUI servers don't include VHS output in /history — this
        # unique prefix enables fallback direct download by known filename.
        vhs_prefix = stamp_vhs_unique_prefix(workflow, job_id_str[:8])
        if vhs_prefix:
            self._vhs_prefixes[job_id_str] = vhs_prefix
            logger.info(f"[{job_id_str}] VHS prefix stamped: {vhs_prefix}")

        # Step 1c: RunPod auto-start — ensure pod is running if enabled
        # If RunPod is configured and has a pod for this service type, prefer it over local workers
        preferred_runpod_url = None
        runpod_was_attempted = False
        try:
            from backend.services.runpod.manager import RunPodManager
            rp_manager = RunPodManager.get_instance()
            if rp_manager.is_configured:
                service_type = "video" if job.job_type == "video" else "image"
                pods = rp_manager.get_pods_for_service(service_type)
                logger.info(f"[{job_id_str}] RunPod enabled, {len(pods)} pod(s) configured for '{service_type}'")
                if pods:
                    runpod_was_attempted = True
                    rp_url = await rp_manager.ensure_pod_running(service_type)
                    if rp_url:
                        # Dynamically add RunPod worker if not already registered
                        # Skip health check — RunPodManager already validated via _health_check()
                        if rp_url not in self.comfy_dispatcher.workers:
                            self.comfy_dispatcher.add_worker(rp_url, skip_health_check=True, is_runpod=True)
                            logger.info(f"[{job_id_str}] Added RunPod worker: {rp_url}")
                        else:
                            # Ensure the is_runpod flag is set even if the URL was
                            # previously registered as a "local" worker (e.g., if the
                            # user also added the RunPod URL to comfyui_urls in settings).
                            # Without this, exclude_runpod=True won't filter it out for
                            # job types that don't have a RunPod pod configured.
                            existing = self.comfy_dispatcher.workers[rp_url]
                            if not existing.is_runpod:
                                existing.is_runpod = True
                                logger.info(f"[{job_id_str}] Marked existing worker as RunPod: {rp_url}")
                            else:
                                logger.info(f"[{job_id_str}] RunPod worker already registered: {rp_url}")
                        preferred_runpod_url = rp_url
                        rp_manager.record_activity(service_type)
                        logger.info(f"[{job_id_str}] Will PREFER RunPod worker: {rp_url}")
                    else:
                        logger.warning(f"[{job_id_str}] RunPod ensure_pod_running returned None — pod may not be healthy, falling back to local workers")
                else:
                    logger.info(f"[{job_id_str}] No RunPod pods configured for service type '{service_type}'")
            else:
                logger.debug(f"[{job_id_str}] RunPod not configured, skipping")
        except Exception as rp_err:
            logger.warning(f"[{job_id_str}] RunPod auto-start failed, falling back to local: {rp_err}", exc_info=True)

        # Step 2: Select worker — prefer RunPod if available.
        # Use reserve=True so in_flight is incremented immediately, preventing
        # a concurrent dispatch task from picking the same worker before we
        # reach submit_job().
        worker_reserved = False
        try:
            if preferred_runpod_url and preferred_runpod_url in self.comfy_dispatcher.workers:
                # Use RunPod worker directly — it's been health-checked by RunPodManager
                worker = self.comfy_dispatcher.workers[preferred_runpod_url]
                worker.in_flight += 1
                worker_reserved = True
                logger.info(f"[{job_id_str}] Using preferred RunPod worker: {worker.url} (in_flight={worker.in_flight})")
            else:
                # Fall back to standard worker selection (local workers only).
                # Exclude RunPod workers so a video-only RunPod pod doesn't get
                # picked for image jobs (or vice versa).
                required_caps = self._get_required_caps(params.get("workflow_type", ""))
                required_models = self._get_required_models(params.get("workflow_type", ""))
                worker = self.comfy_dispatcher.select_worker(
                    required_caps, required_models, exclude_runpod=True, reserve=True,
                )
                worker_reserved = True
                logger.info(f"[{job_id_str}] Using local worker (no RunPod available): {worker.url}")
        except ValueError as e:
            if runpod_was_attempted:
                raise Exception(
                    f"No suitable worker available. RunPod pod failed to start and no local workers "
                    f"have the required capabilities. Check RunPod settings and local ComfyUI servers. ({e})"
                )
            raise Exception(f"No suitable worker: {e}")

        logger.info(f"[{job_id_str}] Selected worker {worker.url}")

        # Update job with worker info
        async with self._session_factory() as session:
            db_job = await session.get(Job, job.id)
            if db_job:
                db_job.worker_url = worker.url
                session.add(db_job)
                await session.commit()

        # Emit worker assignment so frontend can show it immediately
        self.on_progress(job_id_str, {
            "event": "worker_assigned",
            "worker_url": worker.url,
            "scene_id": str(job.scene_id) if job.scene_id else None,
            "job_type": job.job_type,
        })

        # Step 2b: Health-check RunPod worker before uploading files.
        # RunPod pods are added with skip_health_check=True since the
        # RunPodManager reports them as RUNNING — but "RUNNING" only means
        # the container started, not that ComfyUI is ready to serve.  We
        # poll /system_stats until it responds (up to 5 minutes).
        client = self.comfy_dispatcher.clients.get(worker.url)
        if client and getattr(worker, 'is_runpod', False):
            max_health_wait = 300  # 5 minutes
            poll_interval = 10
            waited = 0
            logger.info(f"[{job_id_str}] Waiting for RunPod worker to become healthy: {worker.url}")
            while waited < max_health_wait:
                try:
                    info = await asyncio.to_thread(client.get_system_stats)
                    if info:
                        logger.info(f"[{job_id_str}] RunPod worker healthy after {waited}s: {worker.url}")
                        break
                except Exception:
                    pass
                await asyncio.sleep(poll_interval)
                waited += poll_interval
                if waited % 30 == 0:
                    logger.info(f"[{job_id_str}] Still waiting for RunPod health ({waited}s/{max_health_wait}s)...")
            else:
                if worker_reserved:
                    self.comfy_dispatcher.release_worker(worker.url)
                raise ComfyUIConnectionError(
                    f"RunPod worker {worker.url} did not become healthy within {max_health_wait}s"
                )

        # Step 2c: Validate workflow node types against worker's available nodes.
        # If any class_types in the workflow don't exist on the server, ComfyUI
        # silently excludes the entire dependency chain from execution — which is
        # why VHS_VideoCombine never runs on I2V/FF-LF workflows when utility
        # nodes like "easy cleanGpuUsed" are missing.
        if client:
            try:
                object_info = await asyncio.to_thread(client.get_object_info)
                available_types = set(object_info.keys())
                logger.info(f"[{job_id_str}] Worker {worker.url} has {len(available_types)} node types available")

                # Check which workflow nodes are missing
                missing = validate_node_types(workflow, available_types)
                if missing:
                    logger.warning(
                        f"[{job_id_str}] MISSING NODE TYPES on {worker.url}: "
                        f"{'; '.join(missing)}"
                    )
                    # Auto-remove missing nodes and rewire dependencies
                    removed = remove_missing_nodes(workflow, available_types)
                    if removed:
                        logger.warning(
                            f"[{job_id_str}] Auto-removed {len(removed)} missing node(s) "
                            f"from workflow — execution should now include VHS_VideoCombine"
                        )
                    # Re-validate after removal to check if any critical nodes are still missing
                    still_missing = validate_node_types(workflow, available_types)
                    if still_missing:
                        logger.error(
                            f"[{job_id_str}] CRITICAL: Still missing node types after removal "
                            f"(these may be essential): {'; '.join(still_missing)}"
                        )
                else:
                    logger.info(f"[{job_id_str}] All workflow node types validated OK")
            except Exception as val_err:
                # Don't block submission on validation failure — log and continue
                logger.warning(
                    f"[{job_id_str}] Node type validation failed (continuing anyway): {val_err}"
                )

        # Upload referenced files to remote ComfyUI worker
        if client:
            try:
                await self._upload_workflow_files(client, workflow, job)
            except Exception as e:
                # Release reservation on upload failure
                if worker_reserved:
                    self.comfy_dispatcher.release_worker(worker.url)
                raise

        # Step 2d: Free GPU/CPU memory on the worker before submitting video jobs.
        # V2V workflows need maximum VRAM — residual memory from prior executions
        # can cause OOM even on a single job (16 GB GPU: Pass 1 succeeds but Pass 2
        # OOMs if prior allocations aren't released).
        wf_type = params.get("workflow_type", "")
        if wf_type in ("ltx", "ltx_v2v_extend", "ltx_v2v_pass1", "ltx_v2v_pass2", "ltx_transition") or job.job_type in ("video", "ltx", "ltx_v2v_extend", "ltx_transition"):
            await self._free_worker_memory(worker.url)

        # Step 3: Submit (already_reserved=True since we incremented in_flight at selection)
        # Log key workflow inputs for debugging auto-gen video issues
        try:
            _diag_inputs = {}
            for _nid, _node in workflow.items():
                if not isinstance(_node, dict):
                    continue
                _title = _node.get("_meta", {}).get("title", "")
                _ct = _node.get("class_type", "")
                _inputs = _node.get("inputs", {})
                if _title in ("LOAD IMAGE", "LOAD FIRST IMAGE FRAME", "LOAD LAST IMAGE FRAME", "Load Audio", "LOAD PREVIOUS VIDEO"):
                    _diag_inputs[_title] = {k: v for k, v in _inputs.items() if not isinstance(v, list)}
                elif _title == "CLIP Text Encode (Prompt)":
                    _diag_inputs["prompt"] = str(_inputs.get("text", ""))[:120]
                elif _title == "RandomNoise":
                    _diag_inputs["seed"] = _inputs.get("noise_seed")
            logger.info(f"[{job_id_str}] WORKFLOW SUBMISSION DIAG: {_diag_inputs}")
        except Exception:
            pass  # Diagnostic only, don't break submission

        try:
            prompt_id = self.comfy_dispatcher.submit_job(workflow, worker.url, already_reserved=True)
        except Exception as e:
            # Release reservation on submit failure
            if worker_reserved:
                self.comfy_dispatcher.release_worker(worker.url)
            raise Exception(f"Failed to submit job: {e}")

        # Save prompt_id
        async with self._session_factory() as session:
            db_job = await session.get(Job, job.id)
            if db_job:
                db_job.prompt_id = prompt_id
                session.add(db_job)
                await session.commit()

        logger.info(f"[{job_id_str}] Submitted as prompt {prompt_id}")

        # Step 4+5: Monitor via WebSocket and handle completion
        client = self.comfy_dispatcher.clients.get(worker.url)
        await self._monitor_and_complete(job, prompt_id, worker, client)

    async def _monitor_and_complete(
        self, job: Job, prompt_id: str, worker, client
    ) -> None:
        """Monitor a submitted prompt via WebSocket, then handle outputs."""
        job_id_str = str(job.id)

        client_id = f"job-{job_id_str}"

        def _on_ws_progress(msg: dict) -> None:
            """Callback for WebSocket progress messages."""
            msg_type = msg.get("type")

            if msg_type == "executing":
                node = msg.get("data", {}).get("node")
                self.on_progress(job_id_str, {
                    "event": "executing",
                    "node": node,
                    "timestamp": datetime.utcnow().isoformat(),
                })

            elif msg_type == "progress":
                value = msg.get("data", {}).get("value", 0)
                max_val = msg.get("data", {}).get("max", 1)
                self.on_progress(job_id_str, {
                    "event": "progress",
                    "value": value,
                    "max": max_val,
                    "percent": (value / max_val * 100) if max_val > 0 else 0,
                })

        try:
            # stream_and_wait is synchronous (blocking WS), run in thread
            execution_history = await asyncio.to_thread(
                self.comfy_dispatcher.stream_and_wait,
                worker.url,
                prompt_id,
                on_progress=_on_ws_progress,
                client_id=client_id,
            )
        except Exception as e:
            logger.error(f"[{job_id_str}] Execution failed: {e}")
            raise

        await self._handle_completed_job(job, execution_history, worker, client)

    async def _handle_completed_job(
        self, job: Job, execution_history: dict, worker, client
    ) -> None:
        """Extract outputs from completed ComfyUI execution and save assets."""
        job_id_str = str(job.id)
        prompt_id = job.prompt_id or "unknown"

        # Step 5: Extract outputs, download files, create Asset records
        logger.info(f"[{job_id_str}] stream_and_wait completed. History keys: {list(execution_history.keys()) if execution_history else 'empty'}")

        # Log raw outputs structure for debugging
        raw_outputs = execution_history.get("outputs", {})
        if raw_outputs:
            for nid, nout in raw_outputs.items():
                if isinstance(nout, dict):
                    logger.info(
                        f"[{job_id_str}] Output node {nid}: "
                        f"keys={list(nout.keys())}, "
                        f"counts={{k: len(v) if isinstance(v, list) else type(v).__name__ for k, v in nout.items()}}"
                    )
                else:
                    logger.info(
                        f"[{job_id_str}] Output node {nid}: "
                        f"type={type(nout).__name__}, value={str(nout)[:300]}"
                    )
        else:
            logger.warning(f"[{job_id_str}] History 'outputs' is empty or missing. Full history keys: {list(execution_history.keys())}")

        output_files = self._extract_output_files(execution_history)
        logger.info(f"[{job_id_str}] Found {len(output_files)} output files: {[f.get('filename') for f in output_files]}")
        saved_assets = []

        if output_files:
            if client:
                saved_assets = await self._download_and_save_outputs(
                    client, job, output_files
                )
                logger.info(f"[{job_id_str}] Downloaded and saved {len(saved_assets)} assets")
            else:
                logger.error(f"[{job_id_str}] No client found for {worker.url} to download outputs")
        else:
            logger.warning(f"[{job_id_str}] No output files found in execution history")
            # Dump full outputs for post-mortem debugging
            import json as _json_dbg
            try:
                logger.warning(
                    f"[{job_id_str}] RAW OUTPUTS DUMP: "
                    f"{_json_dbg.dumps(raw_outputs, default=str)[:2000]}"
                )
            except Exception:
                logger.warning(f"[{job_id_str}] RAW OUTPUTS (repr): {repr(raw_outputs)[:2000]}")

            # ── VHS fallback: try direct download using stamped prefix ──
            # Some ComfyUI servers never include VHS_VideoCombine output
            # in the /history API. Since we stamped a unique filename_prefix,
            # we know exactly what file to look for.
            vhs_prefix = self._vhs_prefixes.pop(job_id_str, None)
            if vhs_prefix and client:
                logger.info(
                    f"[{job_id_str}] Attempting VHS fallback download with prefix: {vhs_prefix}"
                )
                file_bytes = await self._vhs_fallback_scan(
                    client, job_id_str, vhs_prefix
                )
                if file_bytes:
                    candidate_name = f"{vhs_prefix}_00001_.mp4"
                    output_files = [{
                        "filename": candidate_name,
                        "subfolder": "",
                        "type": "output",
                        "media_type": "video",
                    }]
                    saved_assets = await self._save_fallback_output(
                        job, candidate_name, file_bytes
                    )
                    logger.info(
                        f"[{job_id_str}] VHS fallback saved {len(saved_assets)} assets"
                    )
                else:
                    # ── Deep diagnostic: dump execution status + recent history ──
                    await self._vhs_diagnostic_dump(
                        client, job_id_str, prompt_id
                    )
            elif not vhs_prefix:
                logger.warning(f"[{job_id_str}] No VHS prefix stored — fallback not possible")

        # Clean up VHS prefix if still present (success path)
        self._vhs_prefixes.pop(job_id_str, None)

        # If no outputs were found AND no assets were saved, the execution
        # failed (e.g., ComfyUI OOMed mid-execution — it still reports
        # "executed" but produces 0 files).  Mark as failed, not completed.
        if not saved_assets and not output_files:
            error_msg = (
                "ComfyUI execution produced no output files. "
                "This usually means the workflow hit an OOM error during execution. "
                "Check the ComfyUI server console for details."
            )
            logger.error(f"[{job_id_str}] {error_msg}")
            await self.job_queue.mark_failed(job.id, error_msg)
            return

        result = {
            "prompt_id": prompt_id,
            "worker": worker.url,
            "asset_ids": [str(a) for a in saved_assets],
            "output_count": len(output_files),
        }

        await self.job_queue.mark_done(job.id, result)

    async def _build_workflow(self, job: Job) -> dict:
        """
        Build the ComfyUI workflow dict from job parameters.

        Supports two paths:
        1. WorkflowConfig-based (if workflow_config_id is in parameters)
        2. Built-in workflow type mapping (klein_*, ltx_*)

        Args:
            job: DB Job model.

        Returns:
            Mutated workflow dict ready for submission.
        """
        params = job.parameters or {}
        workflow_config_id = params.get("workflow_config_id")

        if workflow_config_id:
            return await self._build_from_config(workflow_config_id, params)

        # Fall back to built-in workflow type
        workflow_type = params.get("workflow_type", "")
        return await self._build_builtin_workflow(workflow_type, params, job)

    async def _build_from_config(self, config_id: str, params: dict) -> dict:
        """Build workflow using a WorkflowConfig's JSON and field mappings."""
        from sqlmodel import select

        async with self._session_factory() as session:
            config = await session.get(WorkflowConfig, config_id)
            if not config:
                raise Exception(f"WorkflowConfig {config_id} not found")

            # Map app values to field types
            values = {
                "prompt": params.get("prompt"),
                "negative_prompt": params.get("negative_prompt"),
                "width": params.get("width"),
                "height": params.get("height"),
                "seed": params.get("seed"),
                "duration": params.get("duration"),
                "framerate": params.get("framerate"),
                "audio": params.get("audio_path"),
                "image": params.get("image_path"),
                "first_frame": params.get("first_frame_path"),
                "last_frame": params.get("last_frame_path"),
            }

            return prepare_workflow_from_config(
                config.workflow_json,
                config.field_mappings,
                values,
            )

    async def _build_builtin_workflow(self, workflow_type: str, params: dict, job: Optional[Job] = None) -> dict:
        """Build workflow from built-in workflow types."""
        import random
        from pathlib import Path

        workflows_dir = Path(__file__).parent.parent.parent.parent / "workflows"
        seed = params.get("seed") or random.randint(0, 2**32 - 1)

        # Check if SFW content restriction is enabled — append suffix to prompt
        # Also read global negative prompt for image generation
        sfw_suffix = ""
        global_negative_prompt = ""
        try:
            from sqlmodel import select as sfw_select
            from backend.database.models import AppSettings as SFWAppSettings
            async with self._session_factory() as sfw_session:
                sfw_stmt = sfw_select(SFWAppSettings).where(SFWAppSettings.id == 1)
                sfw_result = await sfw_session.execute(sfw_stmt)
                sfw_settings = sfw_result.scalars().first()
                if sfw_settings:
                    if sfw_settings.restrict_explicit_content:
                        sfw_suffix = ", SFW, safe for work, fully clothed, no nudity, no explicit content, no NSFW"
                    if sfw_settings.global_negative_prompt:
                        global_negative_prompt = sfw_settings.global_negative_prompt.strip()
        except Exception:
            pass  # If settings can't be read, skip SFW suffix

        # Apply SFW suffix to prompt if enabled
        if sfw_suffix:
            prompt_val = params.get("prompt", "")
            if prompt_val:
                params = dict(params)
                params["prompt"] = prompt_val + sfw_suffix

        # Inject image direction / art style into prompt at dispatch time
        # This ensures the style tag always reaches ComfyUI regardless of whether
        # the prompt was enhanced via LLM or written manually by the user
        try:
            project_id = params.get("_project_id") or job.project_id
            async with self._session_factory() as style_session:
                from backend.database.models import Project as StyleProject
                style_project = await style_session.get(StyleProject, project_id)
                if style_project and style_project.settings:
                    img_dir = style_project.settings.get("image_direction", "")
                    if img_dir and img_dir != "none" and img_dir != "":
                        if img_dir == "custom":
                            custom_dir = style_project.settings.get("custom_image_direction", "")
                            if custom_dir:
                                style_tag = f", {custom_dir} style"
                            else:
                                style_tag = ""
                        else:
                            style_tag = f", {img_dir.replace('_', ' ')} style"
                        if style_tag:
                            prompt_val = params.get("prompt", "")
                            if prompt_val:
                                params = dict(params)
                                params["prompt"] = prompt_val + style_tag
                                logger.info(f"[{job.id}] Injected image direction style tag: {style_tag.strip(', ')}")
        except Exception as e:
            logger.debug(f"Could not inject image direction: {e}")

        # Z-Image redirect: when single_image_generator is z_image_turbo and workflow is klein_t2i,
        # use Z-Image Turbo instead of Klein for text-to-image (no reference images)
        if workflow_type == "klein_t2i":
            try:
                async with self._session_factory() as zig_session:
                    from backend.database.models import AppSettings as ZigAppSettings
                    zig_stmt = sfw_select(ZigAppSettings).where(ZigAppSettings.id == 1)
                    zig_result = await zig_session.execute(zig_stmt)
                    zig_settings = zig_result.scalars().first()
                    if zig_settings and zig_settings.single_image_generator == "z_image_turbo":
                        workflow_path = str(workflows_dir / "ZIMAGE_TURBO_T2I.json")
                        prompt_text = params.get("prompt", "")
                        scene_neg = params.get("negative_prompt", "").strip() if params.get("negative_prompt") else ""
                        effective_neg = scene_neg if scene_neg else global_negative_prompt
                        params["effective_negative_prompt"] = effective_neg
                        params["submitted_image_prompt"] = prompt_text
                        logger.info(f"[{job.id if job else 'N/A'}] Redirecting klein_t2i to Z-Image Turbo")
                        return prepare_zimage_workflow(
                            workflow_path=workflow_path,
                            prompt=prompt_text,
                            width=params.get("width", 1024),
                            height=params.get("height", 1024),
                            seed=seed,
                            negative_prompt=effective_neg,
                        )
            except Exception as e:
                logger.debug(f"Could not check single_image_generator: {e} — using Klein")

        # Klein image workflows
        klein_map = {
            "klein_1ref": "KLEIN_EDIT_ULTRA_WORKFLOW_1REF.json",
            "klein_2ref": "KLEIN_EDIT_ULTRA_WORKFLOW_2REF.json",
            "klein_3ref": "KLEIN_EDIT_ULTRA_WORKFLOW_3REF.json",
            "klein_4ref": "KLEIN_EDIT_ULTRA_WORKFLOW_4REF.json",
            "klein_t2i": "KLEIN_EDIT_ULTRA_WORKFLOW_Text2Image.json",
        }

        if workflow_type in klein_map:
            # Resolve reference image paths from asset IDs
            ref_images = await self._resolve_asset_paths(
                params.get("reference_asset_ids", [])
            )
            # Fall back to correct workflow if resolved ref count doesn't match requested
            # (e.g., frontend sent klein_1ref but the character image asset couldn't be found)
            actual_ref_count = len(ref_images)
            ref_count_map = {0: "klein_t2i", 1: "klein_1ref", 2: "klein_2ref", 3: "klein_3ref", 4: "klein_4ref"}
            effective_workflow = ref_count_map.get(actual_ref_count, "klein_4ref")
            if effective_workflow != workflow_type:
                logger.warning(
                    f"Workflow mismatch: requested {workflow_type} but only resolved "
                    f"{actual_ref_count} ref images — falling back to {effective_workflow}"
                )
            workflow_path = str(workflows_dir / klein_map[effective_workflow])
            prompt_text = params.get("prompt", "")
            # Build effective negative prompt: scene-level overrides global, otherwise use global
            scene_neg = params.get("negative_prompt", "").strip() if params.get("negative_prompt") else ""
            effective_neg = scene_neg if scene_neg else global_negative_prompt
            # Store what was actually used so frontend can display it
            params["effective_negative_prompt"] = effective_neg
            # Capture the final submitted prompt (includes SFW + image direction + anti-text suffix + neg prompt)
            anti_text_suffix = ", no text, no subtitles, no captions, no words, no letters, no watermarks"
            if effective_neg:
                anti_text_suffix += ", " + effective_neg
            params["submitted_image_prompt"] = (prompt_text + anti_text_suffix) if prompt_text else prompt_text
            return prepare_klein_workflow(
                workflow_path=workflow_path,
                prompt=prompt_text,
                width=params.get("width", 1024),
                height=params.get("height", 576),
                seed=seed,
                ref_images=ref_images,
                negative_prompt=effective_neg,
            )

        # LTX video workflows
        ltx_map = {
            "ltx_fflf": "LTX-2-3_ULTRA_WORKFLOW_FF_LF.json",
            "ltx_i2v": "LTX-2-3_ULTRA_WORKFLOW_Image2Video.json",
            "ltx_v2v_extend": "LTX-2-3_V2V_EXTEND_v2.json",
            "ltx_v2v_pass1": "LTX-2-3_V2V_EXTEND_v2_pass1.json",
            "ltx_v2v_pass2": "LTX-2-3_V2V_EXTEND_v2_pass2.json",
            "ltx_transition": "LTX-2-3_TRANSITION_LORA.json",
            "ltx_seq_i2v": "LTX_SEQ_I2V.json",
            "ltx_seq_fflf": "LTX_SEQ_FFLF.json",
            "ltx_seq_v2v": "LTX_SEQ_V2V.json",
        }

        if workflow_type in ltx_map:
            workflow_path = str(workflows_dir / ltx_map[workflow_type])

            # Resolve frame images — try asset IDs first, fall back to scene parameters
            first_frame = await self._resolve_single_asset_path(
                params.get("first_frame_asset_id")
            )
            last_frame = await self._resolve_single_asset_path(
                params.get("last_frame_asset_id")
            )
            audio_path = await self._resolve_single_asset_path(
                params.get("audio_asset_id")
            )

            # Auto-resolve from scene parameters if asset IDs weren't provided
            if not first_frame or not last_frame or not audio_path:
                resolved = await self._auto_resolve_video_assets(job, params)
                if not first_frame and resolved.get("first_frame"):
                    first_frame = resolved["first_frame"]
                    logger.info(f"Auto-resolved first_frame from scene: {first_frame}")
                if not last_frame and resolved.get("last_frame"):
                    last_frame = resolved["last_frame"]
                    logger.info(f"Auto-resolved last_frame from scene: {last_frame}")
                if not audio_path and resolved.get("audio"):
                    audio_path = resolved["audio"]
                    logger.info(f"Auto-resolved audio from project: {audio_path}")

            # For I2V workflow, don't pass last_frame — it only has "LOAD IMAGE" node
            # For FF/LF workflow, pass both frames
            effective_last_frame = last_frame if workflow_type == "ltx_fflf" else None

            # Sequencer-based workflows use prepare_sequencer_workflow
            if workflow_type.startswith("ltx_seq_"):
                # Read distilled LoRA and model settings
                distilled_lora_name = None
                ltx_model_gguf_override = None
                try:
                    async with self._session_factory() as seq_session:
                        from backend.database.models import AppSettings as SeqAppSettings
                        seq_stmt = sfw_select(SeqAppSettings).where(SeqAppSettings.id == 1)
                        seq_result = await seq_session.execute(seq_stmt)
                        seq_settings = seq_result.scalars().first()
                        if seq_settings:
                            if seq_settings.use_distilled_lora:
                                distilled_lora_name = seq_settings.distilled_lora_name or "ltx-2.3-22b-distilled-lora-384.safetensors"
                            ltx_model_gguf_override = seq_settings.ltx_model_gguf if hasattr(seq_settings, 'ltx_model_gguf') else None
                except Exception as e:
                    logger.debug(f"Could not read sequencer settings: {e}")
                    ltx_model_gguf_override = None

                effective_last_frame_seq = last_frame if workflow_type == "ltx_seq_fflf" else None

                return prepare_sequencer_workflow(
                    workflow_path=workflow_path,
                    prompt=params.get("prompt", ""),
                    width=params.get("width", 768),
                    height=params.get("height", 512),
                    duration=params.get("duration", 5.0),
                    framerate=params.get("framerate", 24),
                    seed=seed,
                    audio_path=audio_path or "",
                    first_frame=first_frame,
                    last_frame=effective_last_frame_seq,
                    ltx_model_gguf=ltx_model_gguf_override,
                    distilled_lora_name=distilled_lora_name,
                )

            # Read video_tail and ltx_model_gguf from AppSettings
            from sqlmodel import select
            video_tail = 0
            ltx_model_gguf = None
            async with self._session_factory() as session:
                try:
                    from backend.database.models import AppSettings as DBAppSettings
                    stmt = select(DBAppSettings).where(DBAppSettings.id == 1)
                    result = await session.execute(stmt)
                    db_settings = result.scalars().first()
                    if db_settings:
                        video_tail = db_settings.video_tail or 0
                        ltx_model_gguf = db_settings.ltx_model_gguf or None
                except Exception as e:
                    logger.warning(f"Failed to read video settings: {e}")

            base_duration = params.get("duration", 10.0)
            effective_duration = base_duration + video_tail

            # V2V overlap compensation removed — V2V now uses I2V workflow
            # with the previous scene's last frame as image input, so no
            # multi-frame overlap zone exists.
            v2v_overlap_compensation = 0.0

            logger.info(
                f"LTX workflow build: workflow_type={workflow_type}, "
                f"workflow_file={ltx_map[workflow_type]}, "
                f"first_frame={first_frame}, "
                f"last_frame={effective_last_frame}, audio={audio_path}, "
                f"duration={base_duration}s + tail={video_tail}s + v2v_overlap={v2v_overlap_compensation:.2f}s = {effective_duration:.2f}s"
            )

            # Store video_tail in job params so completion handler knows to trim
            if video_tail > 0:
                params_copy = dict(params)
                params_copy["video_tail"] = video_tail
                params_copy["original_duration"] = base_duration
                # Update the job's parameters
                job.parameters = params_copy

                # Re-slice audio clip to include tail duration so the video model
                # has music for the full generation length (base + tail).
                # Without this, the last `video_tail` seconds of generated video
                # would have silence, producing an audible cutoff.
                if audio_path and job.scene_id:
                    extended_audio = await self._reslice_audio_with_tail(
                        job, audio_path, video_tail
                    )
                    if extended_audio:
                        audio_path = extended_audio

            # Capture the final submitted video prompt (includes SFW + image direction suffixes)
            params["submitted_video_prompt"] = params.get("prompt", "")

            # V2V extend: uses I2V workflow with previous scene's last frame
            # as image conditioning.  This gives us precise control over which
            # frame conditions the generation — we extract it ourselves from
            # scene A's trimmed video at exactly the scene boundary, then send
            # the IMAGE (not the video) to the I2V workflow.
            # No VHS_LoadVideo, no frame-skip, no overlap zone, no trim-A needed.
            if workflow_type == "ltx_v2v_extend":
                # Resolve previous scene's last frame image
                v2v_first_frame = await self._resolve_v2v_last_frame_image(job, params)
                if not v2v_first_frame:
                    raise Exception(
                        "V2V extend requires previous scene's last frame image but none found. "
                        "Ensure scene A has a generated video with an extracted last frame."
                    )
                logger.info(f"V2V extend: using previous scene last frame image: {v2v_first_frame}")

                job.parameters = params

                # Route through I2V workflow with prev scene's last frame as input
                i2v_path = str(workflows_dir / ltx_map["ltx_i2v"])
                return prepare_ltx_workflow(
                    workflow_path=i2v_path,
                    prompt=params.get("prompt", ""),
                    width=params.get("width", 1024),
                    height=params.get("height", 576),
                    duration=effective_duration,
                    framerate=params.get("framerate", 24),
                    seed=seed,
                    audio_path=audio_path or "",
                    first_frame=v2v_first_frame,
                    ltx_model_gguf=ltx_model_gguf,
                )

            # V2V Pass 2: load intermediate video → upscale → refine
            if workflow_type == "ltx_v2v_pass2":
                intermediate_video = params.get("intermediate_video_path")
                if not intermediate_video:
                    raise Exception(
                        "V2V Pass 2 requires intermediate video from Pass 1"
                    )
                logger.info(f"V2V Pass 2: using intermediate video {intermediate_video}")

                return prepare_v2v_pass2_workflow(
                    workflow_path=workflow_path,
                    prompt=params.get("prompt", ""),
                    width=params.get("width", 1024),
                    height=params.get("height", 576),
                    duration=effective_duration,
                    framerate=params.get("framerate", 24),
                    seed=seed + 1,  # Different seed for Pass 2
                    audio_path=audio_path or "",
                    intermediate_video=intermediate_video,
                    ltx_model_gguf=ltx_model_gguf,
                )

            # Transition LoRA workflow — generates AI transition clips
            if workflow_type == "ltx_transition":
                transition_first = params.get("transition_first_frame")
                transition_last = params.get("transition_last_frame")
                if not transition_first or not transition_last:
                    raise Exception(
                        "Transition workflow requires both first_frame (end of scene A) "
                        "and last_frame (start of scene B)"
                    )
                logger.info(
                    f"Transition LoRA: FF={transition_first}, LF={transition_last}"
                )

                return prepare_transition_workflow(
                    workflow_path=workflow_path,
                    prompt=params.get("prompt", "smooth cinematic transition between scenes, zhuanchang"),
                    width=params.get("width", 1024),
                    height=params.get("height", 576),
                    duration=params.get("transition_duration", 3),
                    framerate=params.get("framerate", 24),
                    seed=seed,
                    audio_path=audio_path or "",
                    first_frame=transition_first,
                    last_frame=transition_last,
                    ltx_model_gguf=ltx_model_gguf,
                    transition_strength=params.get("transition_lora_strength", 1.0),
                )

            return prepare_ltx_workflow(
                workflow_path=workflow_path,
                prompt=params.get("prompt", ""),
                width=params.get("width", 1024),
                height=params.get("height", 576),
                duration=effective_duration,
                framerate=params.get("framerate", 24),
                seed=seed,
                audio_path=audio_path or "",
                first_frame=first_frame,
                last_frame=effective_last_frame,
                ltx_model_gguf=ltx_model_gguf,
            )

        raise Exception(f"Unknown workflow type: {workflow_type}")

    async def _auto_resolve_video_assets(
        self, job: Job, params: dict
    ) -> Dict[str, Optional[str]]:
        """
        Auto-resolve first_frame, last_frame, and audio paths from scene/project data.

        Falls back to scene.parameters.chosen_image_path / chosen_last_frame_path
        and the project's music asset when explicit asset IDs aren't provided.

        If the scene has a stored audio_clip_path but the file doesn't exist on disk,
        auto-slices it from the master audio using FFmpeg.

        Returns:
            Dict with keys 'first_frame', 'last_frame', 'audio' — each a rel_path or None.
        """
        from sqlmodel import select
        from backend.config import settings as app_settings

        result: Dict[str, Optional[str]] = {
            "first_frame": None,
            "last_frame": None,
            "audio": None,
        }

        async with self._session_factory() as session:
            # Get scene for frame paths
            if job.scene_id:
                scene = await session.get(Scene, job.scene_id)
                if scene:
                    scene_params = scene.parameters or {}

                    # First frame from chosen_image_path
                    ff_path = scene_params.get("chosen_image_path")
                    if ff_path:
                        result["first_frame"] = ff_path
                        logger.info(
                            f"[{str(job.id)[:8]}] Scene {scene.order_index}: "
                            f"chosen_image_path = {ff_path}"
                        )
                    else:
                        logger.warning(
                            f"[{str(job.id)[:8]}] Scene {scene.order_index}: "
                            f"NO chosen_image_path in scene parameters"
                        )

                    # Last frame from chosen_last_frame_path
                    lf_path = scene_params.get("chosen_last_frame_path")
                    if lf_path:
                        result["last_frame"] = lf_path

                    # ── Stem-based audio for video generation ──
                    # Priority: vocals_only flag > per-scene StemSelection > regular audio clip
                    vocals_only = params.get("vocals_only_audio", False)
                    use_stem_mix = params.get("use_stem_selections", True)

                    if vocals_only:
                        # Auto-gen "vocals only" mode — override everything
                        stem_audio = await self._create_stem_audio_for_scene(
                            scene, job.project_id, session, vocals_only=True,
                        )
                        if stem_audio:
                            result["audio"] = stem_audio
                            logger.info(f"Using vocals-only stem audio for scene {scene.order_index}")
                    elif use_stem_mix:
                        # Check per-scene StemSelection — if any stems are deselected, create mix
                        stem_audio = await self._create_stem_audio_for_scene(
                            scene, job.project_id, session, vocals_only=False,
                        )
                        if stem_audio:
                            result["audio"] = stem_audio
                            logger.info(f"Using custom stem mix for scene {scene.order_index}")

                    # Fall back to scene-specific audio clip (full mix)
                    if not result.get("audio"):
                        audio_clip = scene_params.get("audio_clip_path")
                        clip_exists = False
                        if audio_clip:
                            clip_full_path = app_settings.project_dir / audio_clip
                            clip_exists = clip_full_path.exists()

                        if audio_clip and clip_exists:
                            result["audio"] = audio_clip
                        elif scene.start_time is not None and scene.end_time is not None:
                            # Auto-slice: clip missing or never created — generate it now
                            sliced = await self._auto_slice_scene_audio(
                                scene, job.project_id, session
                            )
                            if sliced:
                                result["audio"] = sliced

            # Fall back to full music asset if no scene-specific clip
            if not result["audio"]:
                stmt = (
                    select(Asset)
                    .where(Asset.project_id == job.project_id)
                    .where(Asset.asset_type == AssetType.MUSIC)
                    .order_by(Asset.created_at.desc())  # type: ignore
                )
                audio_result = await session.execute(stmt)
                audio_asset = audio_result.scalars().first()
                if audio_asset:
                    result["audio"] = audio_asset.rel_path

        return result

    async def _create_two_pass_composite_job(
        self,
        session,
        project_id,
        scene_id,
        base_asset: Asset,
        base_params: dict,
        job_id_str: str,
    ) -> None:
        """Create Pass 2 (composite) job after Pass 1 (base) completes.

        Takes the Pass 1 scene image and the original character ref IDs,
        builds a composite prompt using LLM, and creates a new image job
        that places characters into the scene.

        Args:
            session: DB session
            project_id: Project UUID
            scene_id: Scene UUID
            base_asset: The Asset created from Pass 1 output
            base_params: The Pass 1 job parameters
            job_id_str: Job ID string for logging
        """
        from backend.config import settings as app_settings

        # Retrieve character ref asset IDs from Pass 1 params
        char_ref_ids_str = base_params.get("two_pass_character_ref_ids", [])
        if not char_ref_ids_str:
            logger.warning(f"[{job_id_str}] Two-pass Pass 1 complete but no character ref IDs stored — skipping Pass 2")
            return

        # Build ref list: scene image (slot 1) + character refs (slots 2+)
        all_ref_ids = [str(base_asset.id)] + list(char_ref_ids_str)

        # Determine workflow type based on ref count
        ref_count = len(all_ref_ids)
        workflow_type = f"klein_{ref_count}ref"  # e.g. klein_2ref, klein_3ref

        # Build a composite prompt using LLM
        composite_prompt = await self._build_two_pass_composite_prompt(
            session=session,
            project_id=project_id,
            scene_id=scene_id,
            base_prompt=base_params.get("prompt", ""),
            char_ref_ids=char_ref_ids_str,
            job_id_str=job_id_str,
        )

        # Create Pass 2 job
        # Store both the original user prompt and the two-pass prompts for reference
        pass2_params = {
            "workflow_type": workflow_type,
            "workflow_config_id": None,
            "prompt": composite_prompt,
            "width": base_params.get("width", 1024),
            "height": base_params.get("height", 576),
            "seed": base_params.get("seed"),
            "reference_asset_ids": all_ref_ids,
            **({"frame_type": base_params.get("frame_type")} if base_params.get("frame_type") else {}),
            "two_pass": True,
            "two_pass_phase": "composite",
            "auto_save_preview": base_params.get("auto_save_preview", True),
            # Store the prompts for reference
            "two_pass_original_prompt": base_params.get("two_pass_original_prompt", base_params.get("prompt", "")),
            "two_pass_scene_prompt": base_params.get("two_pass_scene_prompt", ""),
            "two_pass_composite_prompt": composite_prompt,
        }
        pass2_job = Job(
            project_id=project_id,
            scene_id=scene_id,
            job_type=JobType.IMAGE,
            status=JobStatus.PENDING,
            parameters=pass2_params,
        )
        session.add(pass2_job)
        await session.commit()
        await session.refresh(pass2_job)

        logger.info(
            f"[{job_id_str}] Two-pass: Created Pass 2 (composite) job {pass2_job.id} "
            f"with {ref_count} refs ({workflow_type}), prompt: {composite_prompt[:80]}..."
        )

        # Notify dispatcher of new job
        if self.job_queue:
            self.job_queue.notify()

    async def _build_two_pass_composite_prompt(
        self,
        session,
        project_id,
        scene_id,
        base_prompt: str,
        char_ref_ids: list[str],
        job_id_str: str,
    ) -> str:
        """Build the LLM-enhanced composite prompt for Pass 2.

        Uses TWO_PASS_COMPOSITE_SYSTEM_PROMPT to generate a prompt that
        places characters into the base scene image.
        """
        from backend.database.models import AppSettings as DBAppSettings
        from sqlmodel import select as llm_select

        try:
            # Get app settings for LLM config
            stmt = llm_select(DBAppSettings).where(DBAppSettings.id == 1)
            result = await session.execute(stmt)
            app_settings_db = result.scalars().first()
            if not app_settings_db:
                logger.warning(f"[{job_id_str}] No app settings for LLM — using base prompt for Pass 2")
                return base_prompt

            # Resolve LLM config
            from backend.api.settings import resolve_llm_config
            provider, api_key, model = resolve_llm_config(app_settings_db)
            if not api_key:
                logger.warning(f"[{job_id_str}] No LLM API key — using base prompt for Pass 2")
                return base_prompt

            # Get project for character info
            project = await session.get(Project, project_id)
            characters = project.settings.get("characters", []) if project and project.settings else []

            # Build character descriptions for context
            # Resolve each char_ref_id (Asset UUID) to its rel_path, then match against character image_paths
            char_descriptions = []
            ref_labels = ["second", "third", "fourth"]
            for i, char_ref_id in enumerate(char_ref_ids):
                label = ref_labels[i] if i < len(ref_labels) else f"reference {i + 2}"
                char_desc = f"Character reference {i + 1}"

                # Look up the Asset to get its rel_path
                try:
                    char_asset = await session.get(Asset, char_ref_id)
                    if char_asset:
                        asset_rel_path = char_asset.rel_path
                        # Match against character image_paths
                        for c in characters:
                            if c.get("image_path", "") == asset_rel_path:
                                char_desc = f'{c.get("name", "Character")} — {c.get("description", "no description")}'
                                break
                except Exception:
                    pass  # Fall back to generic description

                char_descriptions.append(
                    f"The {label} reference image is {char_desc}. "
                    f"Place this character naturally into the scene."
                )

            # Build context
            context_parts = [
                f"BASE SCENE PROMPT (the first reference image was generated from this): {base_prompt}",
                "The first reference image is the base scene composition. All characters should be placed INTO this scene.",
            ]
            context_parts.extend(char_descriptions)

            # Add concept/style info
            if project and project.settings:
                concept = project.settings.get("concept_text", "")
                style = project.settings.get("style_text", "")
                if concept:
                    context_parts.append(f"Video concept: {concept}")
                if style:
                    context_parts.append(f"Visual style: {style}")

            # Get scene for flow idea
            scene = await session.get(Scene, scene_id)
            if scene and scene.parameters:
                flow_idea = scene.parameters.get("flow_idea", "")
                if flow_idea:
                    context_parts.append(f"Scene storyboard: {flow_idea}")

            context = "\n".join(context_parts)

            # Call LLM with composite system prompt
            from backend.services.llm.prompt_enhancer import PromptEnhancer

            enhanced = await asyncio.to_thread(
                PromptEnhancer.enhance,
                base_prompt,  # Use base prompt as starting point
                context,
                provider,
                api_key,
                model,
                False,  # is_video
                None,  # system_prompt_override — use built-in two_pass_composite
                "flux2_klein_dev_9b",  # gen_model_name
                None,  # frame_type — not applicable for composite
                None,  # prompt_guidance
                "composite",  # two_pass_phase — selects TWO_PASS_COMPOSITE_SYSTEM_PROMPT
            )

            logger.info(f"[{job_id_str}] Two-pass composite prompt enhanced: {enhanced[:80]}...")
            return enhanced

        except Exception as e:
            logger.warning(f"[{job_id_str}] LLM enhance for Pass 2 failed ({e}) — using base prompt with character insertion prefix")
            # Fallback: prefix base prompt with character insertion language
            return f"In the scene from the first image, the subject from the second image is present in the composition. {base_prompt}"

    async def _build_two_pass_base_prompt(
        self,
        session,
        project_id,
        scene_id,
        user_prompt: str,
        job_id_str: str,
    ) -> str:
        """Build the LLM-enhanced base prompt for Pass 1 (scene-only generation).

        Uses TWO_PASS_BASE_SYSTEM_PROMPT to generate a prompt that focuses on
        the scene environment, lighting, atmosphere, and composition — stripping
        character language and references since Pass 1 has no character refs.
        """
        from backend.database.models import AppSettings as DBAppSettings
        from sqlmodel import select as llm_select

        try:
            # Get app settings for LLM config
            stmt = llm_select(DBAppSettings).where(DBAppSettings.id == 1)
            result = await session.execute(stmt)
            app_settings_db = result.scalars().first()
            if not app_settings_db:
                logger.warning(f"[{job_id_str}] No app settings for LLM — using user prompt for Pass 1")
                return user_prompt

            # Resolve LLM config
            from backend.api.settings import resolve_llm_config
            provider, api_key, model = resolve_llm_config(app_settings_db)
            if not api_key:
                logger.warning(f"[{job_id_str}] No LLM API key — using user prompt for Pass 1")
                return user_prompt

            # Get project for concept/style info
            project = await session.get(Project, project_id)

            # Get scene for flow idea
            scene = await session.get(Scene, scene_id)

            # Build context
            context_parts = [
                "This is Pass 1 of two-pass generation: generate the SCENE COMPOSITION ONLY.",
                "Environment, lighting, atmosphere, composition, camera framing.",
                "NO character descriptions — characters will be composited in Pass 2.",
                "CRITICAL: Each scene in this video must depict a DIFFERENT environment, setting, "
                "and visual composition. Do NOT repeat similar scenes — the story must progress visually.",
            ]

            # Add scene timing and position
            if scene:
                context_parts.append(
                    f"Scene timing: {scene.start_time}s to {scene.end_time}s "
                    f"(Scene {scene.order_index + 1})."
                )

            # Add concept/style info
            if project and project.settings:
                concept = project.settings.get("concept_text", "")
                style = project.settings.get("style_text", "")
                if concept:
                    context_parts.append(f"Video concept: {concept}")
                if style:
                    context_parts.append(f"Visual style: {style}")

                # Image direction
                image_direction = project.settings.get("image_direction", "")
                if image_direction and image_direction != "none":
                    if image_direction == "custom":
                        custom_dir = project.settings.get("custom_image_direction", "")
                        if custom_dir:
                            context_parts.append(f"Image direction / art style: {custom_dir}")
                    else:
                        dir_label = image_direction.replace("_", " ").title()
                        context_parts.append(f"Image direction / art style: {dir_label}")

            # Add scene lyrics for context
            if scene:
                try:
                    from backend.database.models import Lyrics
                    from sqlmodel import select as lyrics_select
                    lyrics_stmt = lyrics_select(Lyrics).where(Lyrics.project_id == project_id)
                    lyrics_result = await session.execute(lyrics_stmt)
                    lyrics_record = lyrics_result.scalars().first()
                    if lyrics_record and lyrics_record.words:
                        from backend.api.generation import _get_scene_lyrics
                        scene_lyrics = _get_scene_lyrics(scene, lyrics_record.words)
                        if scene_lyrics:
                            context_parts.append(
                                f'Lyrics during this scene: "{scene_lyrics}" — '
                                f"Use these lyrics to inform the ENVIRONMENT and MOOD of the scene "
                                f"(not literal text in the image)."
                            )
                except Exception as e:
                    logger.debug(f"[{job_id_str}] Could not load scene lyrics: {e}")

            # Determine the primary prompt for Pass 1.
            # When a flow idea exists, use IT as the prompt input (not the user's
            # full enhanced prompt, which anchors every scene to the same setting).
            # The user's original prompt is demoted to context as a style reference.
            flow_idea = ""
            if scene and scene.parameters:
                flow_idea = scene.parameters.get("flow_idea", "")

            if flow_idea:
                # Flow idea drives the scene — use it as the primary prompt
                primary_prompt = flow_idea
                context_parts.append(
                    f"The user's original image prompt (use for STYLE and MOOD reference only, "
                    f"NOT for setting/location — the storyboard above determines the location): "
                    f"{user_prompt}"
                )
                logger.info(f"[{job_id_str}] Two-pass Pass 1: using flow idea as primary prompt (not user prompt)")
            else:
                # No flow idea — fall back to user prompt as primary input
                primary_prompt = user_prompt
                logger.info(f"[{job_id_str}] Two-pass Pass 1: no flow idea, using user prompt as primary")

            context = "\n".join(context_parts)

            # Call LLM with base system prompt
            from backend.services.llm.prompt_enhancer import PromptEnhancer

            enhanced = await asyncio.to_thread(
                PromptEnhancer.enhance,
                primary_prompt,
                context,
                provider,
                api_key,
                model,
                False,  # is_video
                None,  # system_prompt_override — use built-in two_pass_base
                "flux2_klein_dev_9b",  # gen_model_name
                None,  # frame_type — not applicable for base
                None,  # prompt_guidance
                "base",  # two_pass_phase — selects TWO_PASS_BASE_SYSTEM_PROMPT
            )

            logger.info(f"[{job_id_str}] Two-pass base prompt enhanced: {enhanced[:80]}...")
            return enhanced

        except Exception as e:
            logger.warning(f"[{job_id_str}] LLM enhance for Pass 1 failed ({e}) — using user prompt as-is")
            return user_prompt

    async def _v2v_trim_a_at_overlap(
        self,
        session,
        scene_b: "Scene",
        scene_b_video: str,
        project_id,
        job_id_str: str,
    ) -> Optional[dict]:
        """V2V overlap resolution via join-and-split (Robomuffin Video Joiner algorithm).

        After V2V generation, scene B's raw output starts with ~28 overlap frames
        that visually duplicate A's tail.  This method:

        1. Runs MSE frame comparison to find the exact overlap match point
        2. Joins A[0..match] + B[match+1..end] into a seamless video
        3. Splits the joined video at scene A's frame count
        4. Sets Part A as scene A's ``chosen_video_path``
        5. Sets Part B as scene B's ``chosen_video_path`` (overlap REMOVED)
        6. Adjusts scene boundaries so A/B join seamlessly

        Returns a dict with ``b_video`` (path to B's precut video) and
        ``scene_a_id`` (UUID of scene A that was also modified), or None
        if the process was skipped.
        """
        from backend.services.video.ffmpeg import (
            find_best_frame_match, trim_video, v2v_join_and_split,
            get_media_info, extract_frame, _ensure_frame_dimensions,
            count_video_frames,
        )
        from backend.config import settings as app_settings
        import shutil

        if scene_b is None:
            logger.warning(f"[{job_id_str}] V2V join-split: scene B not found, skipping")
            return None

        # Find scene A (previous scene by order_index)
        from sqlmodel import select as sel_stmt
        stmt = (
            sel_stmt(Scene)
            .where(Scene.project_id == project_id)
            .where(Scene.order_index == (scene_b.order_index or 0) - 1)
        )
        result = await session.execute(stmt)
        scene_a = result.scalars().first()
        if scene_a is None:
            logger.info(f"[{job_id_str}] V2V join-split: no previous scene (scene B is first), skipping")
            return None

        # Get scene A's video — ALWAYS use the untrimmed (raw model output)
        # so that repeated V2V re-runs don't compound-trim A shorter each time.
        a_params = dict(scene_a.parameters or {})
        a_untrimmed_rel = a_params.get("video_untrimmed_path")
        a_chosen_rel = a_params.get("chosen_video_path")

        # Prefer untrimmed (idempotent source); fall back to chosen if untrimmed missing
        a_source_rel = None
        if a_untrimmed_rel:
            _untrimmed_abs = str(app_settings.project_dir / a_untrimmed_rel)
            if Path(_untrimmed_abs).exists():
                a_source_rel = a_untrimmed_rel
                logger.info(f"[{job_id_str}] V2V join-split: using untrimmed source for A (idempotent)")
        if not a_source_rel:
            a_source_rel = a_chosen_rel
            if a_source_rel:
                logger.info(f"[{job_id_str}] V2V join-split: untrimmed not available, falling back to chosen_video_path for A")

        if not a_source_rel:
            logger.warning(f"[{job_id_str}] V2V join-split: scene A has no video, skipping")
            return None

        a_source_abs = str(app_settings.project_dir / a_source_rel)
        if not Path(a_source_abs).exists():
            logger.warning(f"[{job_id_str}] V2V join-split: scene A source video not found: {a_source_abs}")
            return None

        # Get project FPS
        _proj = await session.get(Project, project_id)
        _fps = 24
        if _proj and _proj.settings:
            _fps = _proj.settings.get("project_fps", 24) or 24

        # --- Idempotent boundary restoration ---
        original_a_end = a_params.get("v2v_trim_a_original_a_end")
        if original_a_end is not None:
            scene_a.end_time = original_a_end
            scene_b.start_time = original_a_end
            logger.info(
                f"[{job_id_str}] V2V join-split: restored original boundary "
                f"A.end=B.start={original_a_end:.4f}s for idempotent re-run"
            )
        else:
            original_a_end = scene_a.end_time
            a_params["v2v_trim_a_original_a_end"] = original_a_end

        # Constrain search to scene A's ORIGINAL duration (exclude video_tail overshoot)
        a_scene_dur = (original_a_end or 0) - (scene_a.start_time or 0)
        a_max_frame = round(a_scene_dur * _fps) if a_scene_dur > 0 else 0

        logger.info(
            f"[{job_id_str}] V2V join-split: comparing A={a_source_abs} "
            f"(scene dur={a_scene_dur:.2f}s, max_frame={a_max_frame}) vs B={scene_b_video}"
        )

        # --- Step 1: MSE frame comparison ---
        match = await asyncio.to_thread(
            find_best_frame_match,
            a_source_abs,
            scene_b_video,
            a_tail_frames=240,
            b_head_frames=1,
            max_mse=2500.0,
            a_max_frame=a_max_frame,
        )

        if match is None:
            logger.info(f"[{job_id_str}] V2V join-split: no acceptable overlap match found, skipping")
            return None

        a_match_frame = match["a_frame"]    # index in A where B[0] matches
        b_match_frame = match["b_frame"]    # always 0
        mse = match["mse"]
        a_total = match["a_total"]
        b_total = match["b_total"]

        # Frames from user's Video Joiner algorithm:
        # A keeps 0..a_match (inclusive) = a_match+1 frames
        # B skips 0..b_match (inclusive), keeps b_match+1..end
        a_frames_to_use = a_match_frame + 1
        b_frames_to_skip = b_match_frame + 1
        b_frames_to_use = b_total - b_frames_to_skip

        if a_frames_to_use <= 0:
            logger.warning(f"[{job_id_str}] V2V join-split: match at frame 0 — nothing to keep from A")
            return None

        logger.info(
            f"[{job_id_str}] V2V join-split: B[{b_match_frame}] ↔ A[{a_match_frame}] "
            f"MSE={mse:.2f}. A keeps {a_frames_to_use} frames (INCLUDES match), "
            f"B skips {b_frames_to_skip} frames (EXCLUDES match), "
            f"B keeps {b_frames_to_use} frames. "
            f"Joined total = {a_frames_to_use + b_frames_to_use} frames"
        )

        # --- Step 2: Join A + B at match point, split into two files ---
        # The split point is at a_frames_to_use (= where scene A ends in the joined video)
        # v2v_join_and_split creates the joined video then extracts Part B.
        # We also need Part A (trimmed scene A).

        # Determine output paths
        b_source_path = Path(scene_b_video)
        b_precut_path = str(b_source_path.parent / (b_source_path.stem + "_v2v_precut" + b_source_path.suffix))

        a_source_path = Path(a_source_abs)
        a_trimmed_path = str(a_source_path.parent / (a_source_path.stem + "_v2v_trimmed" + a_source_path.suffix))

        # Step 2a: Trim A to a_frames_to_use frames (INCLUDE match frame from A)
        a_trim_duration = a_frames_to_use / _fps
        await asyncio.to_thread(trim_video, a_source_abs, a_trimmed_path, a_trim_duration)

        # Step 2b: Create Part B — B with overlap head removed
        # Use v2v_join_and_split which does: join A[0..match]+B[match+1..end], split at a_frames_to_use
        await asyncio.to_thread(
            v2v_join_and_split,
            a_source_abs,
            scene_b_video,
            a_match_frame,
            b_match_frame,
            a_frames_to_use,   # split point = where A ends in joined video
            _fps,
            b_precut_path,
        )

        # Verify Part B was created and has frames
        if not Path(b_precut_path).exists():
            logger.warning(f"[{job_id_str}] V2V join-split: Part B file not created, skipping")
            return None

        b_precut_frames = await asyncio.to_thread(count_video_frames, b_precut_path)
        logger.info(
            f"[{job_id_str}] V2V join-split: Part B created with {b_precut_frames} frames "
            f"(expected ~{b_frames_to_use})"
        )

        # --- Step 3: Set chosen_video_path for BOTH scenes ---

        # Scene A: set trimmed video as chosen
        if a_chosen_rel:
            a_chosen_abs = str(app_settings.project_dir / a_chosen_rel)
        else:
            a_chosen_abs = str(a_source_path.parent / (a_source_path.stem.replace("_untrimmed", "") + a_source_path.suffix))
            a_chosen_rel = str(Path(a_chosen_abs).relative_to(app_settings.project_dir))

        shutil.move(a_trimmed_path, a_chosen_abs)
        a_params["chosen_video_path"] = a_chosen_rel

        logger.info(f"[{job_id_str}] V2V join-split: A trimmed to {a_trim_duration:.3f}s → {a_chosen_rel}")

        # Scene B: set precut video as chosen (overlap frames REMOVED)
        # Replace the raw V2V output with the precut version
        shutil.move(b_precut_path, scene_b_video)
        logger.info(
            f"[{job_id_str}] V2V join-split: B precut (overlap removed) → {scene_b_video}"
        )

        # Also store a flag so downstream code knows B has been precut
        b_params = dict(scene_b.parameters or {})
        b_params["v2v_overlap_removed"] = True
        b_params["v2v_b_frames_skipped"] = b_frames_to_skip
        scene_b.parameters = b_params

        # --- Step 4: Adjust scene boundaries ---
        new_a_end = scene_a.start_time + a_trim_duration
        new_a_end = round(new_a_end * _fps) / _fps  # snap to frame

        logger.info(
            f"[{job_id_str}] V2V join-split: boundary — "
            f"A.end: {original_a_end:.4f}s → {new_a_end:.4f}s, "
            f"B.start: {original_a_end:.4f}s → {new_a_end:.4f}s "
            f"(shift: {original_a_end - new_a_end:.4f}s earlier)"
        )

        scene_a.end_time = new_a_end
        scene_b.start_time = new_a_end

        # Store trim info in scene A's parameters
        a_params["v2v_trim_a_frames_kept"] = a_frames_to_use
        a_params["v2v_trim_a_match_mse"] = round(mse, 2)
        a_params["v2v_trim_a_dropped_frames"] = a_total - a_frames_to_use
        a_params["v2v_trim_a_applied"] = True  # tells export to use chosen_video_path, not untrimmed
        scene_a.parameters = a_params

        # --- Step 5: Re-extract last frame from trimmed A ---
        try:
            a_chosen_path_obj = Path(a_chosen_abs)
            lf_filename = a_chosen_path_obj.stem + "_lastframe.png"
            lf_path = a_chosen_path_obj.parent / lf_filename

            info = await asyncio.to_thread(get_media_info, a_chosen_abs)
            vid_dur = info.get("duration", a_trim_duration)
            vid_fps = info.get("fps", _fps)
            extract_time = max(0, vid_dur - (1.0 / vid_fps))

            await asyncio.to_thread(extract_frame, a_chosen_abs, str(lf_path), extract_time)

            # Resize if needed
            target_w = b_params.get("width", 0)
            target_h = b_params.get("height", 0)
            if target_w > 0 and target_h > 0:
                await asyncio.to_thread(_ensure_frame_dimensions, str(lf_path), target_w, target_h)

            lf_rel = str(lf_path.relative_to(app_settings.project_dir))
            a_params["video_last_frame_path"] = lf_rel
            scene_a.parameters = a_params
            logger.info(f"[{job_id_str}] V2V join-split: re-extracted last frame from trimmed A")
        except Exception as e:
            logger.warning(f"[{job_id_str}] V2V join-split: failed to re-extract last frame: {e}")

        # --- Step 6: Re-slice audio for both scenes ---
        try:
            await self._auto_slice_scene_audio(scene_a, project_id, session)
            logger.info(f"[{job_id_str}] V2V join-split: re-sliced audio for scene A")
        except Exception as e:
            logger.warning(f"[{job_id_str}] V2V join-split: failed to re-slice audio for A: {e}")

        try:
            await self._auto_slice_scene_audio(scene_b, project_id, session)
            logger.info(f"[{job_id_str}] V2V join-split: re-sliced audio for scene B")
        except Exception as e:
            logger.warning(f"[{job_id_str}] V2V join-split: failed to re-slice audio for B: {e}")

        await session.commit()
        logger.info(
            f"[{job_id_str}] V2V join-split complete: A ends at {new_a_end:.4f}s "
            f"(was {original_a_end:.4f}s), B starts at {new_a_end:.4f}s. "
            f"A keeps {a_frames_to_use} frames (INCLUDES match), "
            f"B skipped {b_frames_to_skip} overlap frames."
        )

        return {
            "b_video": scene_b_video,
            "scene_a_id": str(scene_a.id),
        }

    async def _auto_slice_scene_audio(
        self, scene: Scene, project_id, session
    ) -> Optional[str]:
        """Auto-slice a single scene's audio clip from the master audio.

        Returns the relative path to the new clip, or None if slicing fails.
        """
        from sqlmodel import select
        from backend.config import settings as app_settings
        from backend.services.video.ffmpeg import slice_audio

        try:
            # Find the project's music asset
            stmt = (
                select(Asset)
                .where(Asset.project_id == project_id)
                .where(Asset.asset_type == AssetType.MUSIC)
                .where(~Asset.rel_path.contains("stems/"))
            )
            res = await session.execute(stmt)
            music_asset = res.scalars().first()
            if not music_asset:
                logger.warning(f"No music asset for project {project_id}, cannot auto-slice")
                return None

            audio_path = app_settings.project_dir / str(project_id) / music_asset.rel_path
            if not audio_path.exists():
                return None

            clips_dir = app_settings.project_dir / str(project_id) / "audio_clips"
            clips_dir.mkdir(parents=True, exist_ok=True)

            clip_filename = f"scene_{scene.order_index:03d}_{scene.start_time:.2f}_{scene.end_time:.2f}.wav"
            clip_path = clips_dir / clip_filename
            rel_clip_path = str(clip_path.relative_to(app_settings.project_dir))

            await asyncio.to_thread(
                slice_audio,
                str(audio_path),
                str(clip_path),
                scene.start_time,
                scene.end_time,
            )

            # Update scene parameters
            new_params = dict(scene.parameters or {})
            new_params["audio_clip_path"] = rel_clip_path
            scene.parameters = new_params
            await session.commit()

            logger.info(
                f"Auto-sliced audio for scene {scene.order_index}: "
                f"{scene.start_time:.2f}–{scene.end_time:.2f} → {rel_clip_path}"
            )
            return rel_clip_path

        except Exception as e:
            logger.warning(f"Auto-slice failed for scene {scene.order_index}: {e}")
            return None

    async def _create_stem_audio_for_scene(
        self,
        scene: Scene,
        project_id,
        session,
        vocals_only: bool = False,
    ) -> Optional[str]:
        """Create a stem-based audio clip for video generation.

        If vocals_only is True, uses only the vocal stem.
        Otherwise, reads the scene's StemSelection from the DB and mixes
        only the selected stems.

        The mixed audio is sliced to the scene's time boundaries and saved
        as a WAV file.  Returns the relative path or None on failure.
        """
        from sqlmodel import select
        from backend.config import settings as app_settings
        from backend.database.models import StemSelection
        from backend.services.video.ffmpeg import slice_audio

        try:
            project_path = app_settings.project_dir / str(project_id)
            stems_dir = project_path / "assets" / "stems"

            if not stems_dir.exists():
                logger.warning(f"No stems directory for project {project_id}")
                return None

            # Determine which stems to include
            if vocals_only:
                include = {"vocals": True, "drums": False, "bass": False, "other": False}
                mix_label = "vocals_only"
            else:
                # Read scene's StemSelection
                stmt = select(StemSelection).where(StemSelection.scene_id == scene.id)
                result = await session.execute(stmt)
                sel = result.scalars().first()
                if not sel or (sel.vocals and sel.drums and sel.bass and sel.other):
                    # All stems selected (default) — no custom mix needed
                    return None
                include = {
                    "vocals": sel.vocals,
                    "drums": sel.drums,
                    "bass": sel.bass,
                    "other": sel.other,
                }
                parts = [k for k, v in include.items() if v]
                mix_label = "_".join(parts) if parts else "silent"

            # Mix selected stems
            import soundfile as sf
            import numpy as np

            stems_data = []
            sample_rate = None
            for stem_name in ["vocals", "drums", "bass", "other"]:
                if not include.get(stem_name):
                    continue
                stem_path = stems_dir / f"{stem_name}.wav"
                if stem_path.exists():
                    data, sr = sf.read(str(stem_path))
                    stems_data.append(data)
                    sample_rate = sr

            if not stems_data or sample_rate is None:
                logger.warning(f"No stem files found for mixing (scene {scene.order_index})")
                return None

            mixed = np.mean(stems_data, axis=0)

            # Save full mixed audio
            cache_dir = project_path / "cache" / "stem_mixes"
            cache_dir.mkdir(parents=True, exist_ok=True)
            full_mix_path = cache_dir / f"mix_{mix_label}_{scene.id}.wav"
            sf.write(str(full_mix_path), mixed, sample_rate)

            # Slice to scene boundaries
            if scene.start_time is not None and scene.end_time is not None:
                clips_dir = project_path / "audio_clips"
                clips_dir.mkdir(parents=True, exist_ok=True)
                clip_filename = (
                    f"scene_{scene.order_index:03d}_"
                    f"{scene.start_time:.2f}_{scene.end_time:.2f}_{mix_label}.wav"
                )
                clip_path = clips_dir / clip_filename
                await asyncio.to_thread(
                    slice_audio,
                    str(full_mix_path),
                    str(clip_path),
                    scene.start_time,
                    scene.end_time,
                )
                rel_path = str(clip_path.relative_to(app_settings.project_dir))
            else:
                rel_path = str(full_mix_path.relative_to(app_settings.project_dir))

            logger.info(
                f"Created stem mix for scene {scene.order_index} "
                f"({mix_label}): {rel_path}"
            )
            return rel_path

        except ImportError:
            logger.warning("soundfile/numpy not available for stem mixing")
            return None
        except Exception as e:
            logger.warning(f"Stem mix failed for scene {scene.order_index}: {e}")
            return None

    async def _reslice_audio_with_tail(
        self, job: Job, current_audio_path: str, video_tail: float
    ) -> Optional[str]:
        """Re-slice the scene's audio clip to include video_tail extra seconds.

        The video model generates `duration + tail` seconds of video but the
        scene audio clip only covers `duration` seconds.  We extend the audio
        clip so the model has music for the full generation length.  The final
        exported video will still be trimmed back to the real scene duration.

        Returns the relative path to the extended clip, or None on failure.
        """
        from backend.config import settings as app_settings
        from backend.services.video.ffmpeg import slice_audio

        try:
            async with self._session_factory() as session:
                scene = await session.get(Scene, job.scene_id)
                if not scene or scene.start_time is None or scene.end_time is None:
                    return None

                # Calculate extended end time (clamped to audio length later by FFmpeg)
                extended_end = scene.end_time + video_tail

                # Find the master audio asset
                from sqlmodel import select
                stmt = (
                    select(Asset)
                    .where(Asset.project_id == job.project_id)
                    .where(Asset.asset_type == AssetType.MUSIC)
                    .where(~Asset.rel_path.contains("stems/"))
                )
                res = await session.execute(stmt)
                music_asset = res.scalars().first()
                if not music_asset:
                    return None

                audio_path = app_settings.project_dir / str(job.project_id) / music_asset.rel_path
                if not audio_path.exists():
                    return None

                # Create extended clip with "_tail" suffix
                clips_dir = app_settings.project_dir / str(job.project_id) / "audio_clips"
                clips_dir.mkdir(parents=True, exist_ok=True)

                clip_filename = (
                    f"scene_{scene.order_index:03d}_{scene.start_time:.2f}"
                    f"_{extended_end:.2f}_tail.wav"
                )
                clip_path = clips_dir / clip_filename
                rel_clip_path = str(clip_path.relative_to(app_settings.project_dir))

                await asyncio.to_thread(
                    slice_audio,
                    str(audio_path),
                    str(clip_path),
                    scene.start_time,
                    extended_end,
                )

                logger.info(
                    f"Re-sliced audio with tail for scene {scene.order_index}: "
                    f"{scene.start_time:.2f}–{extended_end:.2f} "
                    f"(+{video_tail}s tail) → {rel_clip_path}"
                )
                return rel_clip_path

        except Exception as e:
            logger.warning(f"Audio tail re-slice failed: {e}")
            return None

    async def _resolve_asset_paths(self, asset_ids: list) -> list:
        """Resolve a list of asset ID strings to file paths."""
        if not asset_ids:
            logger.debug("_resolve_asset_paths: no asset IDs provided")
            return []

        logger.info(f"_resolve_asset_paths: resolving {len(asset_ids)} asset IDs: {asset_ids}")
        paths = []
        async with self._session_factory() as session:
            for aid_str in asset_ids:
                if not aid_str:
                    continue
                try:
                    aid = UUID(aid_str) if isinstance(aid_str, str) else aid_str
                    asset = await session.get(Asset, aid)
                    if asset:
                        paths.append(asset.rel_path)
                except Exception as e:
                    logger.warning(f"Could not resolve asset {aid_str}: {e}")
        return paths

    async def _resolve_single_asset_path(self, asset_id) -> Optional[str]:
        """Resolve a single asset ID to a file path."""
        if not asset_id:
            return None

        async with self._session_factory() as session:
            try:
                aid = UUID(asset_id) if isinstance(asset_id, str) else asset_id
                asset = await session.get(Asset, aid)
                return asset.rel_path if asset else None
            except Exception as e:
                logger.warning(f"Could not resolve asset {asset_id}: {e}")
                return None

    async def _resolve_previous_video(self, job: Job) -> tuple[Optional[str], Optional[float]]:
        """
        Resolve the previous scene's video path and duration for V2V extending.

        Looks at the scene before this one (by order_index) and returns
        a tuple of (video_path, scene_duration_seconds).

        CRITICAL: Must use the video_untrimmed_path (raw model output trimmed
        only to scene duration by auto-trim) and the ORIGINAL scene duration
        (before V2V trim-A shifted it).  If we use chosen_video_path after
        trim-A has run, it points to a SHORTER video (cut at the MSE overlap
        match point).  Frame-skip optimization then calculates wrong skip
        values, causing V2V to condition on frames from the middle of the
        scene instead of the tail — producing visible skips at transitions.
        """
        if not job.scene_id or not job.project_id:
            return None, None

        from sqlmodel import select

        async with self._session_factory() as session:
            # Get current scene to find order_index
            current_scene = await session.get(Scene, job.scene_id)
            if not current_scene:
                return None

            # Find previous scene by order_index
            stmt = (
                select(Scene)
                .where(
                    Scene.project_id == job.project_id,
                    Scene.order_index < current_scene.order_index,
                )
                .order_by(Scene.order_index.desc())
                .limit(1)
            )
            result = await session.execute(stmt)
            prev_scene = result.scalars().first()

            if not prev_scene:
                logger.warning("V2V extend: no previous scene found")
                return None, None

            prev_params = prev_scene.parameters or {}

            # Calculate the ORIGINAL scene duration for frame-skip optimization.
            # If trim-A was previously applied, end_time was shifted earlier —
            # we MUST use the original end time to get the correct duration.
            # The frame-skip calculation needs the duration of the video FILE,
            # not the post-trim-A scene boundary.
            original_a_end = prev_params.get("v2v_trim_a_original_a_end")
            if original_a_end is not None and prev_scene.start_time is not None:
                # Trim-A was previously applied — use original duration
                prev_duration = original_a_end - prev_scene.start_time
                logger.info(
                    f"V2V extend: using ORIGINAL scene duration {prev_duration:.2f}s "
                    f"(current end_time shifted by previous trim-A to "
                    f"{prev_scene.end_time:.2f}s)"
                )
            elif prev_scene.end_time is not None and prev_scene.start_time is not None:
                prev_duration = prev_scene.end_time - prev_scene.start_time
            else:
                prev_duration = None

            # Use video_untrimmed_path (raw model output auto-trimmed to SCENE
            # DURATION only).  This file's duration matches prev_duration above.
            # chosen_video_path may have been shortened by a previous trim-A,
            # making it much shorter than the scene duration — wrong for V2V.
            #
            # Fallback to chosen_video_path only if untrimmed is missing
            # (first-run scene without video_tail, or legacy data).
            from backend.config import settings as app_settings

            video_path = None
            untrimmed_rel = prev_params.get("video_untrimmed_path")
            chosen_rel = prev_params.get("chosen_video_path")

            if untrimmed_rel:
                full_path = Path(app_settings.project_dir) / untrimmed_rel
                if full_path.exists():
                    video_path = untrimmed_rel
                    logger.info(
                        f"V2V extend: using video_untrimmed_path for conditioning "
                        f"(idempotent, not affected by trim-A)"
                    )

            if not video_path and chosen_rel:
                full_path = Path(app_settings.project_dir) / chosen_rel
                if full_path.exists():
                    video_path = chosen_rel
                    # Warn if trim-A was applied — chosen_video_path is shorter
                    if prev_params.get("v2v_trim_a_applied"):
                        logger.warning(
                            f"V2V extend: falling back to chosen_video_path but "
                            f"trim-A was applied — video may be shorter than expected!"
                        )
                    else:
                        logger.info(
                            f"V2V extend: using chosen_video_path (no untrimmed available)"
                        )
                else:
                    logger.warning(f"V2V extend: chosen_video_path not found: {full_path}")

            if video_path:
                full_path = Path(app_settings.project_dir) / video_path
                dur_str = f" (duration={prev_duration:.2f}s)" if prev_duration else ""
                logger.info(
                    f"V2V extend: resolved previous video from scene "
                    f"{prev_scene.order_index}: {video_path}{dur_str}"
                )
                return str(full_path), prev_duration

            logger.warning(
                f"V2V extend: previous scene {prev_scene.order_index} has no video"
            )
            return None, None

    async def _resolve_v2v_last_frame_image(
        self, job: Job, params: dict
    ) -> Optional[str]:
        """Resolve the previous scene's last frame IMAGE for V2V conditioning.

        V2V now uses the I2V workflow with image-based conditioning instead of
        loading the previous video.  This gives us precise control over which
        frame is used — we extract it ourselves at exactly the scene boundary.

        Resolution order:
        1. If previous scene already has video_last_frame_path → extract fresh
           from the trimmed video to ensure it's current
        2. Fall back to existing video_last_frame_path if extraction fails
        3. Fall back to chosen_image_path (first frame) as last resort

        Returns absolute path to the last frame image, or None.
        """
        from backend.services.video.ffmpeg import extract_frame, get_media_info
        from backend.config import settings as app_settings
        from sqlmodel import select

        if not job.scene_id or not job.project_id:
            return None

        try:
            async with self._session_factory() as session:
                # Find previous scene
                current_scene = await session.get(Scene, job.scene_id)
                if not current_scene:
                    logger.warning("V2V resolve last frame: current scene not found")
                    return None

                stmt = (
                    select(Scene)
                    .where(
                        Scene.project_id == job.project_id,
                        Scene.order_index == (current_scene.order_index or 0) - 1,
                    )
                )
                result = await session.execute(stmt)
                prev_scene = result.scalars().first()
                if not prev_scene:
                    logger.warning("V2V resolve last frame: no previous scene found")
                    return None

                prev_params = dict(prev_scene.parameters or {})
                project_dir = app_settings.project_dir
                fps = params.get("framerate", 24) or 24

                # Try to extract fresh from the trimmed video (chosen_video_path)
                # This is the most accurate — it's the actual video that plays in
                # the timeline, trimmed to exact scene duration
                chosen_rel = prev_params.get("chosen_video_path")
                if chosen_rel:
                    chosen_abs = str(Path(project_dir) / chosen_rel)
                    if Path(chosen_abs).exists():
                        try:
                            # Use VIDEO STREAM duration (not container duration).
                            # LTX audio-conditioned outputs can have audio tracks
                            # longer than video, making format.duration overshoot.
                            from backend.services.video.ffmpeg import get_video_stream_duration
                            vid_dur = await asyncio.to_thread(
                                get_video_stream_duration, chosen_abs
                            )
                            if vid_dur <= 0:
                                # Fallback to format duration
                                info = await asyncio.to_thread(get_media_info, chosen_abs)
                                vid_dur = info.get("duration", 0)

                            if vid_dur > 0:
                                # Save alongside the video
                                video_path = Path(chosen_abs)
                                lf_filename = video_path.stem + "_lastframe.png"
                                lf_path = video_path.parent / lf_filename

                                # Try extracting at progressively earlier times
                                # in case the reported duration exceeds actual
                                # decodable video content
                                extracted = False
                                offsets = [1.0 / fps, 3.0 / fps, 0.5, 1.0]
                                for offset in offsets:
                                    extract_time = max(0, vid_dur - offset)
                                    try:
                                        await asyncio.to_thread(
                                            extract_frame, chosen_abs,
                                            str(lf_path), extract_time,
                                        )
                                        if lf_path.exists():
                                            extracted = True
                                            break
                                    except Exception:
                                        logger.warning(
                                            f"V2V: extract_frame failed at "
                                            f"{extract_time:.3f}s, trying earlier"
                                        )
                                        continue

                                if extracted and lf_path.exists():
                                    # Store in scene A parameters
                                    lf_rel = str(lf_path.relative_to(project_dir))
                                    prev_params["video_last_frame_path"] = lf_rel
                                    prev_scene.parameters = prev_params
                                    await session.commit()

                                    logger.info(
                                        f"V2V: extracted last frame from scene A "
                                        f"chosen_video at {extract_time:.3f}s → {lf_rel}"
                                    )
                                    return str(lf_path)
                                else:
                                    logger.warning(
                                        f"V2V: all extraction attempts failed for "
                                        f"{chosen_abs} (vid_dur={vid_dur:.2f}s)"
                                    )
                        except Exception as e:
                            logger.warning(
                                f"V2V: failed to extract last frame from "
                                f"chosen_video_path: {e}"
                            )

                # Fallback: use existing video_last_frame_path if available
                lf_rel = prev_params.get("video_last_frame_path")
                if lf_rel:
                    lf_abs = str(Path(project_dir) / lf_rel)
                    if Path(lf_abs).exists():
                        logger.info(
                            f"V2V: using existing video_last_frame_path: {lf_rel}"
                        )
                        return lf_abs

                # Last resort: use chosen_image_path (first frame image)
                img_rel = prev_params.get("chosen_image_path")
                if img_rel:
                    img_abs = str(Path(project_dir) / img_rel)
                    if Path(img_abs).exists():
                        logger.warning(
                            f"V2V: no last frame available, falling back to "
                            f"chosen_image_path: {img_rel}"
                        )
                        return img_abs

                logger.warning(
                    f"V2V: previous scene {prev_scene.order_index} has no "
                    f"video_last_frame_path or chosen_image_path"
                )
                return None

        except Exception as e:
            logger.error(f"V2V resolve last frame image failed: {e}")
            return None

    async def _extract_prev_scene_last_frame_for_v2v(
        self,
        job: Job,
        previous_video: str,
        prev_video_dur: Optional[float],
        framerate: int,
    ) -> None:
        """Extract and store scene A's last frame from the video used for V2V.

        Ensures video_last_frame_path is always fresh and matches what
        ExtendSampler actually conditions on (the tail of this video at
        the scene boundary).  This runs BEFORE V2V submission so the
        stored last frame is guaranteed to be in sync.
        """
        from backend.services.video.ffmpeg import extract_frame, get_media_info, _ensure_frame_dimensions
        from backend.config import settings as app_settings
        from sqlmodel import select

        if not job.scene_id or not job.project_id:
            return

        try:
            async with self._session_factory() as session:
                # Find previous scene
                current_scene = await session.get(Scene, job.scene_id)
                if not current_scene:
                    return

                stmt = (
                    select(Scene)
                    .where(
                        Scene.project_id == job.project_id,
                        Scene.order_index == (current_scene.order_index or 0) - 1,
                    )
                )
                result = await session.execute(stmt)
                prev_scene = result.scalars().first()
                if not prev_scene:
                    return

                # Determine extract time: last frame within scene boundary
                fps = framerate or 24
                if prev_video_dur and prev_video_dur > 0:
                    extract_time = max(0, prev_video_dur - (1.0 / fps))
                else:
                    # Fall back to probing the file
                    info = await asyncio.to_thread(get_media_info, previous_video)
                    vid_dur = info.get("duration", 0)
                    extract_time = max(0, vid_dur - (1.0 / fps))

                # Extract to a path alongside the video
                video_path = Path(previous_video)
                lf_filename = video_path.stem + "_lastframe.png"
                lf_path = video_path.parent / lf_filename

                await asyncio.to_thread(extract_frame, previous_video, str(lf_path), extract_time)

                if not lf_path.exists():
                    logger.warning("V2V pre-extract: last frame file not created")
                    return

                # Store in scene A's parameters
                lf_rel = str(lf_path.relative_to(app_settings.project_dir))
                prev_params = dict(prev_scene.parameters or {})
                prev_params["video_last_frame_path"] = lf_rel
                prev_scene.parameters = prev_params
                await session.commit()

                logger.info(
                    f"V2V pre-extract: saved scene A last frame at "
                    f"{extract_time:.3f}s → {lf_rel}"
                )
        except Exception as e:
            logger.warning(f"V2V pre-extract last frame failed (non-fatal): {e}")

    async def _upload_workflow_files(self, client, workflow: dict, job: Job) -> None:
        """
        Scan a workflow for image/audio file references and upload them to the
        remote ComfyUI server before submission.

        Looks for nodes with class_type that load files (LoadImage, LoadAudio, etc.)
        and uploads the referenced file from the local project directory.

        After upload, the workflow node is updated to use just the filename
        (as ComfyUI stores uploads in its own input/ directory).
        """
        from backend.config import settings

        job_id_str = str(job.id)

        # Node class types that reference local files and their input field names
        IMAGE_LOAD_CLASSES = {
            "LoadImage": "image",
            "Load Image": "image",        # Some custom nodes
            "VHS_LoadAudio": "audio_file",
            "LoadAudio": "audio",
            "VHS_LoadVideo": "video",     # V2V extend: upload previous video
        }

        # Also check by _meta.title for known node titles
        IMAGE_TITLE_FIELDS = {
            "Load Image": "image",
            "Reference 2 Image": "image",
            "Reference 3": "image",
            "Reference 4": "image",
            "LOAD IMAGE": "image",
            "LOAD FIRST IMAGE FRAME": "image",
            "LOAD LAST IMAGE FRAME": "image",
            "LOAD FIRST FRAME": "image",      # Transition LoRA: end of scene A
            "LOAD LAST FRAME": "image",        # Transition LoRA: start of scene B
            "Load Audio": "audio",
            "LOAD PREVIOUS VIDEO": "video",  # V2V extend: upload previous video
            "LOAD INTERMEDIATE VIDEO": "video",  # V2V split-pass: upload Pass 1 intermediate
        }

        uploaded = set()  # Track already-uploaded filenames to avoid duplicates

        for node_id, node in workflow.items():
            if not isinstance(node, dict):
                continue

            inputs = node.get("inputs", {})
            title = node.get("_meta", {}).get("title", "")
            class_type = node.get("class_type", "")

            # Determine which input field holds a file path
            field_name = None
            if title in IMAGE_TITLE_FIELDS:
                field_name = IMAGE_TITLE_FIELDS[title]
            elif class_type in IMAGE_LOAD_CLASSES:
                field_name = IMAGE_LOAD_CLASSES[class_type]

            if not field_name or field_name not in inputs:
                continue

            file_value = inputs[field_name]

            # Skip if value is empty, a list (connection reference), or already uploaded
            if not file_value or isinstance(file_value, list):
                continue

            file_str = str(file_value)

            # Resolve the local file path — try multiple strategies
            # Some rel_paths include project_id prefix, some don't
            project_id_str = str(job.project_id)
            candidates = [
                settings.project_dir / file_str,                          # Direct: project_dir/rel_path
                settings.project_dir / project_id_str / file_str,         # With project ID: project_dir/pid/rel_path
            ]

            local_path = None
            for candidate in candidates:
                if candidate.exists():
                    local_path = candidate
                    break

            # If still not found, try searching recursively for bare filenames
            if local_path is None:
                if os.sep not in file_str and "/" not in file_str:
                    # Bare filename — search project dir recursively
                    found = list(settings.project_dir.rglob(file_str))
                    if found:
                        local_path = found[0]
                        logger.info(f"[{job_id_str}] Found bare filename '{file_str}' at {local_path}")
                    else:
                        # Bare filename not found locally — skip (might be on ComfyUI already)
                        logger.debug(f"[{job_id_str}] Bare filename '{file_str}' not found locally, skipping")
                        continue
                else:
                    logger.warning(
                        f"[{job_id_str}] File not found for upload: tried {[str(c) for c in candidates]} "
                        f"(node '{title}', field '{field_name}')"
                    )
                    continue

            # Log file details for debugging — hash lets us verify the correct file content is sent
            try:
                import hashlib as _hashlib
                _file_hash = _hashlib.sha256(local_path.read_bytes()).hexdigest()[:16]
                _file_size = local_path.stat().st_size
                logger.info(
                    f"[{job_id_str}] Upload prep: node='{title}' field='{field_name}' "
                    f"local={local_path} size={_file_size} sha256={_file_hash}"
                )
            except Exception:
                pass

            # Use a unique filename for ComfyUI to bust execution cache.
            # ComfyUI hashes file names (not content) into its cache key,
            # so re-uploading a different file with the same name can return
            # cached results from a previous run.
            import time as _time
            stem = local_path.stem
            suffix = local_path.suffix
            cache_bust = int(_time.time())
            upload_filename = f"{stem}_{cache_bust}{suffix}"

            if upload_filename in uploaded:
                # Already uploaded this file, just update the node to use the filename
                inputs[field_name] = upload_filename
                continue

            try:
                logger.info(
                    f"[{job_id_str}] Uploading {upload_filename} to ComfyUI "
                    f"({local_path} -> node '{title}'.{field_name})"
                )
                upload_result = await asyncio.to_thread(
                    client.upload_image, str(local_path), upload_filename
                )
                # Use the actual filename ComfyUI stored (in case of rename)
                actual_name = upload_result.get("name", upload_filename) if isinstance(upload_result, dict) else upload_filename
                uploaded.add(actual_name)
                # Update the workflow node to reference the actual stored filename
                inputs[field_name] = actual_name
                logger.info(f"[{job_id_str}] Upload successful: {upload_filename} -> stored as {actual_name}")
            except Exception as e:
                logger.error(
                    f"[{job_id_str}] Failed to upload {upload_filename}: {e}"
                )
                raise

    @staticmethod
    def _get_required_caps(workflow_type: str) -> set:
        """Get required capabilities for a workflow type."""
        caps_map = {
            "klein_1ref": {"klein"},
            "klein_2ref": {"klein"},
            "klein_3ref": {"klein"},
            "klein_4ref": {"klein"},
            "klein_t2i": {"klein"},
            "ltx_fflf": {"ltx"},
            "ltx_i2v": {"ltx"},
            "ltx_v2v_extend": {"ltx"},
            "ltx_v2v_pass1": {"ltx"},
            "ltx_v2v_pass2": {"ltx"},
            "ltx_transition": {"ltx"},
        }
        return caps_map.get(workflow_type, set())

    @staticmethod
    def _get_required_models(workflow_type: str) -> set:
        """Get required models for a workflow type."""
        models_map = {
            "klein_1ref": {"FLUX"},
            "klein_2ref": {"FLUX"},
            "klein_3ref": {"FLUX"},
            "klein_4ref": {"FLUX"},
            "klein_t2i": {"FLUX"},
            "ltx_fflf": {"LTX"},
            "ltx_i2v": {"LTX"},
            "ltx_v2v_extend": {"LTX"},
            "ltx_v2v_pass1": {"LTX"},
            "ltx_v2v_pass2": {"LTX"},
            "ltx_transition": {"LTX"},
        }
        return models_map.get(workflow_type, set())

    @staticmethod
    def _extract_output_files(history: dict) -> list[dict]:
        """
        Extract output file info from ComfyUI execution history.

        ComfyUI history format:
        {
            "outputs": {
                "<node_id>": {
                    "images": [{"filename": "...", "subfolder": "...", "type": "output"}],
                    "gifs": [...]
                }
            }
        }

        VHS_VideoCombine (Video Helper Suite) may output under "gifs" or
        other keys.  Some ComfyUI versions nest outputs differently.

        Returns:
            List of dicts with filename, subfolder, type keys.
        """
        files = []
        outputs = history.get("outputs", {})

        # Diagnostic: log the raw output structure so we can debug
        # when 0 files are found despite a successful execution.
        if outputs:
            for node_id, node_outputs in outputs.items():
                if isinstance(node_outputs, dict):
                    keys_summary = {
                        k: (len(v) if isinstance(v, list) else type(v).__name__)
                        for k, v in node_outputs.items()
                    }
                    logger.debug(
                        f"Output node {node_id}: keys={keys_summary}"
                    )
                else:
                    logger.debug(
                        f"Output node {node_id}: type={type(node_outputs).__name__}, "
                        f"value={str(node_outputs)[:200]}"
                    )
        else:
            logger.warning("History 'outputs' is empty or missing")

        # Known output keys to check and their media types.
        # "images" → image, "gifs"/"videos" → video.
        # VHS_VideoCombine uses "gifs" even for MP4 output.
        _IMAGE_KEYS = ["images"]
        _VIDEO_KEYS = ["gifs", "videos"]

        for node_id, node_outputs in outputs.items():
            if not isinstance(node_outputs, dict):
                logger.warning(
                    f"Output node {node_id} is not a dict: "
                    f"{type(node_outputs).__name__}"
                )
                continue

            # Check for images
            for key in _IMAGE_KEYS:
                for item in node_outputs.get(key, []):
                    if isinstance(item, dict) and item.get("filename"):
                        files.append({
                            "filename": item["filename"],
                            "subfolder": item.get("subfolder", ""),
                            "type": item.get("type", "output"),
                            "media_type": "image",
                        })

            # Check for videos (gifs, videos)
            for key in _VIDEO_KEYS:
                for item in node_outputs.get(key, []):
                    if isinstance(item, dict) and item.get("filename"):
                        files.append({
                            "filename": item["filename"],
                            "subfolder": item.get("subfolder", ""),
                            "type": item.get("type", "output"),
                            "media_type": "video",
                        })

            # Fallback: check ANY list-of-dicts with a "filename" key
            # that we haven't already processed. This catches unknown
            # output formats from custom nodes.
            _known_keys = set(_IMAGE_KEYS + _VIDEO_KEYS)
            for key, value in node_outputs.items():
                if key in _known_keys:
                    continue
                if not isinstance(value, list):
                    continue
                for item in value:
                    if not isinstance(item, dict) or not item.get("filename"):
                        continue
                    # Determine media type from extension
                    fname = item["filename"]
                    ext = Path(fname).suffix.lower()
                    if ext in (".mp4", ".webm", ".mov", ".avi", ".mkv", ".gif"):
                        media = "video"
                    else:
                        media = "image"
                    files.append({
                        "filename": fname,
                        "subfolder": item.get("subfolder", ""),
                        "type": item.get("type", "output"),
                        "media_type": media,
                    })
                    logger.info(
                        f"Found output file under unexpected key '{key}' "
                        f"in node {node_id}: {fname} (media_type={media})"
                    )

        return files

    async def _download_and_save_outputs(
        self,
        client,
        job: Job,
        output_files: list[dict],
    ) -> list[UUID]:
        """
        Download output files from ComfyUI, save locally, create Asset records.

        Args:
            client: ComfyUIClient connected to the worker.
            job: The Job model instance.
            output_files: List of file info dicts from _extract_output_files.

        Returns:
            List of created Asset UUIDs.
        """
        import hashlib
        from pathlib import Path
        from backend.config import settings
        from backend.database.models import (
            Asset,
            AssetType,
            GenerationHistory,
            JobType,
        )

        job_id_str = str(job.id)
        project_id = job.project_id
        scene_id = job.scene_id
        params = job.parameters or {}

        # Output directory: <project_dir>/<project_id>/generated/
        output_dir = settings.project_dir / str(project_id) / "generated"
        output_dir.mkdir(parents=True, exist_ok=True)

        created_asset_ids = []

        for file_info in output_files:
            filename = file_info["filename"]
            subfolder = file_info.get("subfolder", "")
            filetype = file_info.get("type", "output")
            media_type = file_info.get("media_type", "image")

            try:
                # Download from ComfyUI
                file_bytes = await asyncio.to_thread(
                    client.download_output,
                    filename,
                    subfolder,
                    filetype,
                )

                if not file_bytes:
                    logger.warning(f"[{job_id_str}] Empty download for {filename}")
                    continue

                # Save to disk with a UNIQUE filename to prevent overwriting
                # previous generations.  ComfyUI output filenames (e.g.
                # LTX_00001_.mp4) reset when the server restarts, so different
                # runs can produce the same filename.  Saving with the raw
                # filename overwrites the previous file, making all
                # GenerationHistory entries point to the same content and
                # causing the browser to serve a cached version (same URL).
                import time as _ts_save
                _stem = Path(filename).stem
                _ext = Path(filename).suffix
                _job_short = job_id_str[:8]
                unique_filename = f"{_stem}_{_job_short}_{int(_ts_save.time())}{_ext}"
                local_path = output_dir / unique_filename
                local_path.write_bytes(file_bytes)
                # Use the unique filename from here on so rel_path, asset
                # records, and chosen_*_path all reference the distinct file.
                filename = unique_filename

                # Log actual dimensions of downloaded media for debugging
                if media_type == "video":
                    try:
                        from backend.services.video.ffmpeg import get_media_info
                        dl_info = await asyncio.to_thread(get_media_info, str(local_path))
                        dl_w = dl_info.get("width", "?")
                        dl_h = dl_info.get("height", "?")
                        expected_w = params.get("width", "?")
                        expected_h = params.get("height", "?")
                        if dl_w != expected_w or dl_h != expected_h:
                            logger.warning(
                                f"[{job_id_str}] DIMENSION MISMATCH: LTX output {dl_w}x{dl_h} "
                                f"but job requested {expected_w}x{expected_h}"
                            )
                        else:
                            logger.info(
                                f"[{job_id_str}] Video dimensions OK: {dl_w}x{dl_h} "
                                f"(matches requested {expected_w}x{expected_h})"
                            )
                    except Exception:
                        pass
                elif media_type == "image":
                    try:
                        from PIL import Image as _PILImg
                        with _PILImg.open(str(local_path)) as _img:
                            _iw, _ih = _img.size
                            expected_w = params.get("width", "?")
                            expected_h = params.get("height", "?")
                            if _iw != expected_w or _ih != expected_h:
                                logger.warning(
                                    f"[{job_id_str}] IMAGE DIMENSION MISMATCH: output {_iw}x{_ih} "
                                    f"but job requested {expected_w}x{expected_h}"
                                )
                            else:
                                logger.info(f"[{job_id_str}] Image dimensions OK: {_iw}x{_ih}")
                    except Exception:
                        pass

                # Compute hash
                sha256 = hashlib.sha256(file_bytes).hexdigest()

                # Determine asset type
                is_character_gen = params.get("character_gen", False)
                if is_character_gen and media_type == "image":
                    asset_type = AssetType.CHARACTER
                else:
                    asset_type = (
                        AssetType.GENERATED_IMAGE
                        if media_type == "image"
                        else AssetType.GENERATED_VIDEO
                    )

                # Relative path from project_dir
                rel_path = str(local_path.relative_to(settings.project_dir))

                # Create Asset record
                async with self._session_factory() as session:
                    asset = Asset(
                        project_id=project_id,
                        filename=filename,
                        rel_path=rel_path,
                        asset_type=asset_type,
                        sha256=sha256,
                        file_size=len(file_bytes),
                        width=params.get("width"),
                        height=params.get("height"),
                        meta={
                            "job_id": job_id_str,
                            "scene_id": str(scene_id) if scene_id else None,
                            "prompt": params.get("prompt", ""),
                            "seed": params.get("seed"),
                            "workflow_type": params.get("workflow_type", ""),
                        },
                    )
                    session.add(asset)

                    # Also create a GenerationHistory entry
                    gen_history = GenerationHistory(
                        project_id=project_id,
                        scene_id=scene_id,
                        job_type=(
                            JobType.IMAGE if media_type == "image" else JobType.VIDEO
                        ),
                        prompt_id=str(job.prompt_id or ""),
                        status="completed",
                        parameters=params,
                        output_path=rel_path,
                        completed_at=datetime.utcnow(),
                    )
                    session.add(gen_history)

                    # Auto-set as chosen preview image for the scene
                    # Skip for two-pass Pass 1 — only Pass 2 (composite) should set the preview
                    is_two_pass_base = params.get("two_pass") and params.get("two_pass_phase") == "base"
                    is_two_pass_composite = params.get("two_pass") and params.get("two_pass_phase") == "composite"
                    logger.info(
                        f"[{job_id_str}] COMPLETION two-pass check: two_pass={params.get('two_pass')}, "
                        f"phase={params.get('two_pass_phase')}, is_base={is_two_pass_base}, "
                        f"is_composite={is_two_pass_composite}, media={media_type}, scene={scene_id}"
                    )
                    if media_type == "image" and scene_id and not is_two_pass_base:
                        scene = await session.get(Scene, scene_id)
                        if scene:
                            scene_params = dict(scene.parameters or {})
                            frame_type = params.get("frame_type", "first")
                            if frame_type == "last":
                                scene_params["chosen_last_frame_path"] = rel_path
                                logger.info(
                                    f"[{job_id_str}] Auto-set chosen_last_frame_path for scene {scene_id}"
                                )
                            else:
                                scene_params["chosen_image_path"] = rel_path
                                logger.info(
                                    f"[{job_id_str}] Auto-set chosen_image_path for scene {scene_id}"
                                )

                            # For two-pass composite (Pass 2), also store the prompts used
                            if is_two_pass_composite:
                                scene_params["two_pass_original_prompt"] = params.get("two_pass_original_prompt", "")
                                scene_params["two_pass_scene_prompt"] = params.get("two_pass_scene_prompt", "")
                                scene_params["two_pass_composite_prompt"] = params.get("two_pass_composite_prompt", "")
                                logger.info(
                                    f"[{job_id_str}] Stored two-pass prompts for scene {scene_id}"
                                )

                            # Store the final submitted prompt (with all dispatch-time suffixes)
                            submitted = params.get("submitted_image_prompt")
                            if submitted:
                                if frame_type == "last":
                                    scene_params["submitted_last_frame_prompt"] = submitted
                                else:
                                    scene_params["submitted_image_prompt"] = submitted
                                logger.info(f"[{job_id_str}] Stored submitted_image_prompt for scene {scene_id}")

                            scene.parameters = scene_params

                    # Two-pass: store base image path in scene parameters for "View Original" UI
                    if media_type == "image" and is_two_pass_base and scene_id:
                        scene = await session.get(Scene, scene_id)
                        if scene:
                            scene_params = dict(scene.parameters or {})
                            scene_params["two_pass_base_image_path"] = rel_path
                            scene_params["two_pass_base_asset_id"] = str(asset.id)
                            scene.parameters = scene_params

                    # Two-pass auto-chain: when Pass 1 (base) completes, create Pass 2 (composite) job
                    if media_type == "image" and is_two_pass_base and scene_id:
                        try:
                            await self._create_two_pass_composite_job(
                                session=session,
                                project_id=project_id,
                                scene_id=scene_id,
                                base_asset=asset,
                                base_params=params,
                                job_id_str=job_id_str,
                            )
                        except Exception as e:
                            logger.error(f"[{job_id_str}] Failed to create two-pass Pass 2 job: {e}")

                    # Auto-set character image_path in project.settings
                    if is_character_gen and media_type == "image":
                        char_idx = params.get("character_index")
                        if char_idx is not None:
                            project = await session.get(Project, project_id)
                            if project:
                                proj_settings = dict(project.settings or {})
                                chars = list(proj_settings.get("characters", []))
                                if 0 <= char_idx < len(chars):
                                    chars[char_idx] = dict(chars[char_idx])
                                    chars[char_idx]["image_path"] = rel_path
                                    proj_settings["characters"] = chars
                                    project.settings = proj_settings
                                    logger.info(
                                        f"[{job_id_str}] Auto-set image_path for character {char_idx}"
                                    )

                    # Auto-trim video if video_tail was applied
                    # Skip post-processing for transition clips
                    #
                    # V2V now uses I2V workflow (image-based conditioning) so
                    # there is no overlap zone — no trim-A needed.  All video
                    # types (I2V, V2V, FF/LF) follow the same post-processing:
                    # auto-trim → color correction → audio mux → last-frame.
                    _is_transition = params.get("is_transition_clip", False)
                    if media_type == "video" and scene_id and not _is_transition:
                        video_tail_applied = params.get("video_tail", 0)
                        scene = await session.get(Scene, scene_id)
                        if scene and video_tail_applied > 0:
                            try:
                                scene_duration = scene.end_time - scene.start_time
                                if scene_duration > 0:
                                    from backend.services.video.ffmpeg import trim_video
                                    import shutil

                                    # Keep original as untrimmed backup
                                    untrimmed_path = output_dir / (Path(filename).stem + "_untrimmed" + Path(filename).suffix)
                                    shutil.copy2(str(local_path), str(untrimmed_path))

                                    # Trim to scene duration (uses CURRENT boundary
                                    # which may have been adjusted by trim-A for V2V)
                                    trim_target = scene_duration

                                    # Detect V2V / use_prev_lf_as_ff scenes where
                                    # frame 0 is a duplicate of the previous scene's
                                    # last frame.  Skip it during trim so that
                                    # chosen_video_path is already clean — export
                                    # won't need to skip again (which would lose
                                    # 1 frame per scene, compounding across 30+
                                    # scenes into ~1.5s of total drift).
                                    sc_params = dict(scene.parameters or {})
                                    wf_type = params.get("workflow_type", "")
                                    is_v2v = wf_type == "ltx_v2v_extend"
                                    uses_prev_lf = bool(sc_params.get("use_prev_lf_as_ff"))
                                    should_skip_ff = is_v2v or uses_prev_lf

                                    # Trim to target duration
                                    trimmed_tmp = output_dir / (Path(filename).stem + "_trimmed" + Path(filename).suffix)
                                    await asyncio.to_thread(
                                        trim_video,
                                        str(local_path),
                                        str(trimmed_tmp),
                                        trim_target,
                                        skip_first_frame=should_skip_ff,
                                    )

                                    # Replace original with trimmed version
                                    shutil.move(str(trimmed_tmp), str(local_path))

                                    # Store untrimmed path and skip flag in scene params
                                    scene_params = dict(scene.parameters or {})
                                    untrimmed_rel = str(untrimmed_path.relative_to(settings.project_dir))
                                    scene_params["video_untrimmed_path"] = untrimmed_rel
                                    if should_skip_ff:
                                        scene_params["dispatcher_skipped_first_frame"] = True
                                    scene.parameters = scene_params
                                    skip_msg = " + skipped duplicate frame 0" if should_skip_ff else ""
                                    logger.info(f"[{job_id_str}] Trimmed video to {trim_target:.2f}s (scene={scene_duration:.2f}s){skip_msg}, untrimmed saved to {untrimmed_rel}")
                            except Exception as e:
                                logger.warning(f"[{job_id_str}] Failed to trim video: {e}")

                    # Auto color-correct video if enabled and reference frame exists
                    # (skip for transition clips and V2V Pass 1 intermediates)
                    if media_type == "video" and scene_id and not _is_transition:
                        try:
                            # Read color correction setting from DB
                            from backend.database.models import AppSettings as DBAppSettingsCC
                            from sqlmodel import select as cc_select
                            cc_stmt = cc_select(DBAppSettingsCC).where(DBAppSettingsCC.id == 1)
                            cc_result = await session.execute(cc_stmt)
                            cc_settings = cc_result.scalars().first()
                            cc_enabled = cc_settings.color_correction_enabled if cc_settings and cc_settings.color_correction_enabled is not None else True

                            if cc_enabled:
                                scene = await session.get(Scene, scene_id)
                                if scene:
                                    sc_params = scene.parameters or {}
                                    # Use the chosen first frame image as the colour reference
                                    ref_image_rel = sc_params.get("chosen_image_path")
                                    if ref_image_rel:
                                        ref_image_abs = str(settings.project_dir / ref_image_rel)
                                        if Path(ref_image_abs).exists():
                                            from backend.services.video.color_correction import color_correct_video
                                            corrected = await asyncio.to_thread(
                                                color_correct_video,
                                                str(local_path),
                                                ref_image_abs,
                                            )
                                            if corrected:
                                                logger.info(f"[{job_id_str}] Color correction applied to video")
                                            else:
                                                logger.info(f"[{job_id_str}] Color correction skipped (within threshold)")
                                        else:
                                            logger.debug(f"[{job_id_str}] Color correction skipped — ref image not found: {ref_image_abs}")
                        except Exception as e:
                            logger.warning(f"[{job_id_str}] Color correction failed (non-fatal): {e}")

                    # Replace model-generated audio with the scene's actual audio clip.
                    # LTX 2.3's audio pipeline encodes audio into latent space and
                    # decodes it back, producing a reconstruction that sounds different
                    # from the original.  For music videos, we want the original music
                    # in each scene's video so individual playback sounds correct.
                    # Unless skip_audio_mux is True, in which case we keep the model's audio.
                    skip_mux = params.get("skip_audio_mux", False)
                    if skip_mux:
                        logger.info(f"[{job_id_str}] Skipping audio mux (skip_audio_mux=True) — keeping model-generated audio")
                    elif media_type == "video" and scene_id:
                        try:
                            scene = await session.get(Scene, scene_id)
                            if scene:
                                sc_params = scene.parameters or {}
                                audio_abs = None

                                # 1. Try scene's audio_clip_path
                                scene_audio = sc_params.get("audio_clip_path")
                                if scene_audio:
                                    candidate = settings.project_dir / scene_audio
                                    if candidate.exists():
                                        audio_abs = candidate
                                        logger.info(f"[{job_id_str}] Found scene audio clip: {scene_audio}")

                                # 2. Auto-slice from master audio if clip doesn't exist
                                if not audio_abs and scene.start_time is not None and scene.end_time is not None:
                                    sliced = await self._auto_slice_scene_audio(
                                        scene, project_id, session
                                    )
                                    if sliced:
                                        candidate = settings.project_dir / sliced
                                        if candidate.exists():
                                            audio_abs = candidate
                                            logger.info(f"[{job_id_str}] Auto-sliced scene audio: {sliced}")

                                # 3. Fall back to full master audio asset
                                if not audio_abs:
                                    from sqlmodel import select as mux_select
                                    stmt = (
                                        mux_select(Asset)
                                        .where(Asset.project_id == project_id)
                                        .where(Asset.asset_type == AssetType.MUSIC)
                                        .order_by(Asset.created_at.desc())
                                    )
                                    result = await session.execute(stmt)
                                    music_asset = result.scalars().first()
                                    if music_asset:
                                        # rel_path is relative to project subfolder (project_dir/project_id/)
                                        candidate = settings.project_dir / str(project_id) / music_asset.rel_path
                                        if candidate.exists():
                                            audio_abs = candidate
                                            logger.info(f"[{job_id_str}] Using master audio: {music_asset.rel_path}")

                                if audio_abs:
                                    from backend.services.video.ffmpeg import mux_audio
                                    import shutil

                                    mux_tmp = output_dir / (Path(filename).stem + "_muxed" + Path(filename).suffix)
                                    await asyncio.to_thread(
                                        mux_audio,
                                        str(local_path),
                                        str(audio_abs),
                                        str(mux_tmp),
                                    )
                                    shutil.move(str(mux_tmp), str(local_path))
                                    logger.info(
                                        f"[{job_id_str}] Replaced model audio with scene audio: {audio_abs.name}"
                                    )
                                else:
                                    logger.warning(f"[{job_id_str}] No audio source found for mux — video keeps model audio")
                        except Exception as e:
                            logger.warning(f"[{job_id_str}] Audio replacement failed (non-fatal): {e}")

                    # Auto-extract last frame from generated videos.
                    #
                    # IMPORTANT: We extract at a precise time position from the
                    # UNTRIMMED video rather than using extract_last_frame() on the
                    # trimmed version.  This is because:
                    #
                    # 1. trim_video() uses stream copy (-c:v copy) which can only cut
                    #    at keyframe boundaries, so the trimmed video's actual duration
                    #    may differ from the requested scene_duration by several frames.
                    #
                    # 2. If we extract the "last frame" from the trimmed video, we might
                    #    get a frame from the video_tail overshoot, or miss frames due
                    #    to keyframe misalignment.
                    #
                    # Instead, we extract at exactly (scene_duration - 1/fps) from the
                    # untrimmed video.  This guarantees we get the actual last frame of
                    # the scene content, regardless of trim keyframe alignment.
                    if media_type == "video" and scene_id and not _is_transition:
                        scene = await session.get(Scene, scene_id)
                        if scene:
                            try:
                                from backend.services.video.ffmpeg import extract_frame, get_media_info, get_video_stream_duration
                                from backend.services.video.ffmpeg import _ensure_frame_dimensions

                                lf_filename = Path(filename).stem + "_lastframe.png"
                                lf_path = output_dir / lf_filename
                                target_w = params.get("width", 0)
                                target_h = params.get("height", 0)

                                # Determine the precise extraction time
                                scene_duration = (scene.end_time or 0) - (scene.start_time or 0)
                                video_tail_applied = params.get("video_tail", 0)
                                _lf_fps = 24
                                try:
                                    _lf_proj = await session.get(Project, project_id)
                                    if _lf_proj and _lf_proj.settings:
                                        _lf_fps = _lf_proj.settings.get("project_fps", 24) or 24
                                except Exception:
                                    pass

                                # V2V now uses I2V workflow (no overlap frames), so all
                                # video types follow the same last-frame extraction path.
                                if True:
                                    # Check if untrimmed version exists (it will if video_tail was applied)
                                    untrimmed_path = output_dir / (Path(filename).stem + "_untrimmed" + Path(filename).suffix)
                                    if video_tail_applied > 0 and untrimmed_path.exists():
                                        # Extract from untrimmed at exact scene_duration - 1/fps
                                        extract_time = max(0, scene_duration - (1.0 / _lf_fps))
                                        source_video = str(untrimmed_path)
                                        logger.info(
                                            f"[{job_id_str}] Extracting last frame from untrimmed video "
                                            f"at {extract_time:.4f}s (scene_duration={scene_duration:.2f}s, fps={_lf_fps})"
                                        )
                                    else:
                                        # No tail — extract from actual video's last frame
                                        # Use VIDEO STREAM duration, not container duration.
                                        # LTX audio-conditioned outputs can have audio
                                        # tracks longer than video, causing overshoot.
                                        vid_duration = await asyncio.to_thread(
                                            get_video_stream_duration, str(local_path)
                                        )
                                        if vid_duration <= 0:
                                            info = await asyncio.to_thread(get_media_info, str(local_path))
                                            vid_duration = info.get("duration", 0)
                                        extract_time = max(0, vid_duration - (1.0 / _lf_fps))
                                        source_video = str(local_path)
                                        logger.info(
                                            f"[{job_id_str}] Extracting last frame from video "
                                            f"at {extract_time:.4f}s (vid_stream_dur={vid_duration:.2f}s, fps={_lf_fps})"
                                        )

                                # Try extraction with progressively earlier offsets
                                _lf_extracted = False
                                _lf_offsets = [extract_time]
                                # Add fallback times if primary fails
                                if extract_time > 0:
                                    _lf_offsets.extend([
                                        max(0, extract_time - (2.0 / _lf_fps)),
                                        max(0, extract_time - 0.5),
                                    ])
                                for _lf_t in _lf_offsets:
                                    try:
                                        await asyncio.to_thread(
                                            extract_frame, source_video, str(lf_path), _lf_t
                                        )
                                        if lf_path.exists():
                                            _lf_extracted = True
                                            if _lf_t != extract_time:
                                                logger.info(
                                                    f"[{job_id_str}] Last frame extracted at "
                                                    f"fallback time {_lf_t:.4f}s (original {extract_time:.4f}s failed)"
                                                )
                                            break
                                    except Exception:
                                        logger.warning(
                                            f"[{job_id_str}] extract_frame failed at {_lf_t:.4f}s, trying earlier"
                                        )
                                        continue

                                if not _lf_extracted:
                                    logger.warning(f"[{job_id_str}] All last-frame extraction attempts failed")

                                # Ensure dimensions match project resolution
                                if target_w > 0 and target_h > 0:
                                    await asyncio.to_thread(
                                        _ensure_frame_dimensions, str(lf_path), target_w, target_h
                                    )

                                lf_rel_path = str(lf_path.relative_to(settings.project_dir))
                                scene_params = dict(scene.parameters or {})
                                scene_params["video_last_frame_path"] = lf_rel_path
                                scene.parameters = scene_params
                                logger.info(f"[{job_id_str}] Extracted last frame: {lf_rel_path}")
                            except Exception as e:
                                logger.warning(f"[{job_id_str}] Failed to extract last frame: {e}")

                    # Auto-set as chosen video for the scene (always — latest video wins)
                    # Exception: transition clips store to transition_clip_path instead
                    # Exception: V2V Pass 1 intermediate — Pass 2 will set chosen_video_path
                    if media_type == "video" and scene_id and not _is_transition:
                        scene = await session.get(Scene, scene_id)
                        if scene:
                            scene_params = dict(scene.parameters or {})
                            if params.get("is_transition_clip"):
                                # Transition LoRA clip — store as transition, not main video
                                scene_params["transition_clip_path"] = rel_path
                                scene.parameters = scene_params
                                logger.info(
                                    f"[{job_id_str}] Saved transition_clip_path for scene {scene_id}"
                                )
                            else:
                                scene_params["chosen_video_path"] = rel_path
                                scene_params["scene_source_type"] = "video"
                                # Store the final submitted video prompt (with all dispatch-time suffixes)
                                submitted_video = params.get("submitted_video_prompt")
                                if submitted_video:
                                    scene_params["submitted_video_prompt"] = submitted_video
                                    logger.info(f"[{job_id_str}] Stored submitted_video_prompt for scene {scene_id}")
                                scene.parameters = scene_params
                                logger.info(
                                    f"[{job_id_str}] Auto-set chosen_video_path for scene {scene_id}"
                                )

                    await session.commit()
                    created_asset_ids.append(asset.id)

                logger.info(
                    f"[{job_id_str}] Saved {media_type} asset {asset.id}: {filename}"
                )

            except Exception as e:
                logger.error(f"[{job_id_str}] Failed to download/save {filename}: {e}")

        return created_asset_ids

    async def _vhs_diagnostic_dump(
        self,
        client,
        job_id_str: str,
        prompt_id: str,
    ) -> None:
        """
        Dump diagnostic info when VHS output cannot be found anywhere.

        Queries:
        1. Full history for this prompt (including status/messages)
        2. Recent history entries to see what filenames other prompts produced
        """
        import json as _diag_json

        tag = f"[{job_id_str}] VHS-DIAG"

        # 1. Full raw history for this prompt — check status/messages for errors
        try:
            full_raw = await asyncio.to_thread(
                client.get_full_history, prompt_id
            )
            prompt_entry = full_raw.get(prompt_id, {})
            status = prompt_entry.get("status", {})
            status_str = status.get("status_str", "unknown")
            completed = status.get("completed", False)
            messages = status.get("messages", [])

            logger.error(
                f"{tag} Execution status: status_str={status_str}, "
                f"completed={completed}, messages_count={len(messages)}"
            )
            # Log each status message — look for node errors
            for msg in messages[:20]:
                if isinstance(msg, (list, tuple)) and len(msg) >= 2:
                    msg_type = msg[0]
                    msg_data = msg[1] if isinstance(msg[1], dict) else str(msg[1])[:200]
                    logger.error(f"{tag}   status_msg: {msg_type} → {_diag_json.dumps(msg_data, default=str)[:500]}")

            # Log ALL output node IDs and their keys
            outputs = prompt_entry.get("outputs", {})
            logger.error(
                f"{tag} Outputs: {len(outputs)} nodes: "
                f"{list(outputs.keys())}"
            )
            for nid, nout in outputs.items():
                if isinstance(nout, dict):
                    logger.error(
                        f"{tag}   output[{nid}]: keys={list(nout.keys())} "
                        f"data={_diag_json.dumps(nout, default=str)[:300]}"
                    )
        except Exception as e:
            logger.error(f"{tag} Failed to get full history: {e}")

        # 2. Recent history — check if ANY recent prompt has VHS file output
        try:
            recent = await asyncio.to_thread(client.get_recent_history, 5)
            logger.error(
                f"{tag} Recent history: {len(recent)} prompts"
            )
            for pid, entry in list(recent.items())[:5]:
                is_ours = " (THIS JOB)" if pid == prompt_id else ""
                outputs = entry.get("outputs", {})
                file_nodes = []
                for nid, nout in outputs.items():
                    if isinstance(nout, dict):
                        for k in ("images", "gifs", "videos"):
                            items = nout.get(k, [])
                            if isinstance(items, list):
                                for item in items:
                                    if isinstance(item, dict) and item.get("filename"):
                                        file_nodes.append(
                                            f"{nid}.{k}={item['filename']}"
                                        )
                logger.error(
                    f"{tag}   prompt {pid[:12]}...{is_ours}: "
                    f"output_nodes={list(outputs.keys())}, "
                    f"file_outputs={file_nodes or 'NONE'}"
                )
        except Exception as e:
            logger.error(f"{tag} Failed to get recent history: {e}")

    async def _vhs_fallback_scan(
        self,
        client,
        job_id_str: str,
        vhs_prefix: str,
    ) -> Optional[bytes]:
        """
        Scan ComfyUI server for VHS output files using multiple naming patterns.

        Tries several strategies in order:
        1. Stamped unique prefix with counter 00001 (expected for unique prefix)
        2. Stamped prefix with counters 00002-00005
        3. Original prefix (e.g. "LTX2") with counter scan (recent 50 values)
        4. All of the above with type=temp instead of type=output
        5. Variations without trailing underscore

        Args:
            client: ComfyUIClient instance
            job_id_str: Job ID string for logging
            vhs_prefix: The stamped VHS prefix (e.g. "LTX2_j5fca22c3")

        Returns:
            File bytes if found, None if not
        """
        # Extract original prefix from stamped prefix (e.g. "LTX2_j5fca22c3" → "LTX2")
        original_prefix = vhs_prefix.rsplit("_j", 1)[0] if "_j" in vhs_prefix else vhs_prefix

        # Build candidate list: (filename, file_type) pairs
        candidates: list[tuple[str, str]] = []

        # Strategy 1: Stamped prefix with low counters (output dir)
        for counter in range(1, 6):
            candidates.append((f"{vhs_prefix}_{counter:05d}_.mp4", "output"))

        # Strategy 2: Stamped prefix WITHOUT trailing underscore
        for counter in range(1, 3):
            candidates.append((f"{vhs_prefix}_{counter:05d}.mp4", "output"))

        # Strategy 3: Original prefix with broad counter scan (output dir)
        # VHS scans existing files and increments counter.
        # Server may have accumulated many files, so counter could be high.
        # Try recent range: 1-20 and 95-110 to cover fresh servers and busy ones.
        for counter in list(range(1, 21)) + list(range(95, 111)):
            candidates.append((f"{original_prefix}_{counter:05d}_.mp4", "output"))

        # Strategy 4: All stamped patterns with type=temp
        for counter in range(1, 4):
            candidates.append((f"{vhs_prefix}_{counter:05d}_.mp4", "temp"))
            candidates.append((f"{vhs_prefix}_{counter:05d}.mp4", "temp"))

        # Strategy 5: Original prefix with type=temp
        for counter in range(1, 11):
            candidates.append((f"{original_prefix}_{counter:05d}_.mp4", "temp"))

        # Deduplicate while preserving order
        seen = set()
        unique_candidates = []
        for c in candidates:
            if c not in seen:
                seen.add(c)
                unique_candidates.append(c)

        logger.info(
            f"[{job_id_str}] VHS fallback scanning {len(unique_candidates)} "
            f"candidates (prefix={vhs_prefix}, original={original_prefix})"
        )

        for filename, file_type in unique_candidates:
            try:
                file_bytes = await asyncio.to_thread(
                    client.try_download_output, filename, "", file_type
                )
            except Exception as dl_err:
                logger.warning(f"[{job_id_str}] VHS fallback error for {filename}: {dl_err}")
                file_bytes = None

            if file_bytes:
                logger.info(
                    f"[{job_id_str}] VHS fallback SUCCESS: {filename} "
                    f"(type={file_type}, {len(file_bytes)} bytes)"
                )
                return file_bytes

        logger.error(
            f"[{job_id_str}] VHS fallback FAILED: exhausted all {len(unique_candidates)} "
            f"candidates (prefix={vhs_prefix}, original={original_prefix})"
        )
        return None

    async def _save_fallback_output(
        self,
        job: Job,
        filename: str,
        file_bytes: bytes,
    ) -> list[UUID]:
        """
        Save a fallback-downloaded file (VHS direct download) as an asset.

        This mirrors the save logic in _download_and_save_outputs but takes
        already-downloaded bytes instead of fetching from ComfyUI.

        Args:
            job: The Job model instance.
            filename: Original filename from ComfyUI.
            file_bytes: Already-downloaded file content.

        Returns:
            List of created Asset UUIDs.
        """
        import hashlib
        import time as _ts_save

        job_id_str = str(job.id)
        project_id = job.project_id
        scene_id = job.scene_id
        params = job.parameters or {}

        from backend.config import settings as app_cfg
        output_dir = app_cfg.project_dir / str(project_id) / "generated"
        output_dir.mkdir(parents=True, exist_ok=True)

        # Save with unique filename
        _stem = Path(filename).stem
        _ext = Path(filename).suffix
        _job_short = job_id_str[:8]
        unique_filename = f"{_stem}_{_job_short}_{int(_ts_save.time())}{_ext}"
        local_path = output_dir / unique_filename
        local_path.write_bytes(file_bytes)
        filename = unique_filename

        logger.info(f"[{job_id_str}] VHS fallback saved to: {local_path}")

        # Determine media type from extension
        ext_lower = Path(filename).suffix.lower()
        if ext_lower in (".mp4", ".webm", ".mov", ".avi", ".mkv", ".gif"):
            media_type = "video"
        else:
            media_type = "image"

        sha256 = hashlib.sha256(file_bytes).hexdigest()
        asset_type = (
            AssetType.GENERATED_IMAGE if media_type == "image"
            else AssetType.GENERATED_VIDEO
        )
        rel_path = str(local_path.relative_to(app_cfg.project_dir))

        created_asset_ids = []

        from backend.database.models import GenerationHistory, JobType

        async with self._session_factory() as session:
            asset = Asset(
                project_id=project_id,
                filename=filename,
                rel_path=rel_path,
                asset_type=asset_type,
                sha256=sha256,
                file_size=len(file_bytes),
                width=params.get("width"),
                height=params.get("height"),
                meta={
                    "job_id": job_id_str,
                    "scene_id": str(scene_id) if scene_id else None,
                    "prompt": params.get("prompt", ""),
                    "seed": params.get("seed"),
                    "workflow_type": params.get("workflow_type", ""),
                    "vhs_fallback": True,
                },
            )
            session.add(asset)

            gen_history = GenerationHistory(
                project_id=project_id,
                scene_id=scene_id,
                job_type=(
                    JobType.IMAGE if media_type == "image" else JobType.VIDEO
                ),
                prompt_id=str(job.prompt_id or ""),
                status="completed",
                parameters=params,
                output_path=rel_path,
                completed_at=datetime.utcnow(),
            )
            session.add(gen_history)

            # Auto-trim if video_tail was applied
            if media_type == "video" and scene_id:
                video_tail_applied = params.get("video_tail", 0)
                scene = await session.get(Scene, scene_id)
                if scene and video_tail_applied > 0:
                    try:
                        scene_duration = scene.end_time - scene.start_time
                        if scene_duration > 0:
                            from backend.services.video.ffmpeg import trim_video
                            import shutil

                            untrimmed_path = output_dir / (Path(filename).stem + "_untrimmed" + Path(filename).suffix)
                            shutil.copy2(str(local_path), str(untrimmed_path))

                            # Detect V2V / use_prev_lf_as_ff for first-frame skip
                            sc_params_fb = dict(scene.parameters or {})
                            wf_type_fb = params.get("workflow_type", "")
                            is_v2v_fb = wf_type_fb == "ltx_v2v_extend"
                            uses_prev_lf_fb = bool(sc_params_fb.get("use_prev_lf_as_ff"))
                            should_skip_ff_fb = is_v2v_fb or uses_prev_lf_fb

                            trimmed_tmp = output_dir / (Path(filename).stem + "_trimmed" + Path(filename).suffix)
                            await asyncio.to_thread(
                                trim_video,
                                str(local_path),
                                str(trimmed_tmp),
                                scene_duration,
                                skip_first_frame=should_skip_ff_fb,
                            )
                            shutil.move(str(trimmed_tmp), str(local_path))

                            scene_params = dict(scene.parameters or {})
                            untrimmed_rel = str(untrimmed_path.relative_to(app_cfg.project_dir))
                            scene_params["video_untrimmed_path"] = untrimmed_rel
                            if should_skip_ff_fb:
                                scene_params["dispatcher_skipped_first_frame"] = True
                            scene.parameters = scene_params
                            skip_msg_fb = " + skipped duplicate frame 0" if should_skip_ff_fb else ""
                            logger.info(f"[{job_id_str}] VHS fallback: trimmed to {scene_duration:.2f}s{skip_msg_fb}")
                    except Exception as e:
                        logger.warning(f"[{job_id_str}] VHS fallback trim failed: {e}")

            # Auto-set chosen path
            if media_type == "video" and scene_id:
                scene = await session.get(Scene, scene_id)
                if scene:
                    scene_params = dict(scene.parameters or {})
                    scene_params["chosen_video_path"] = rel_path
                    scene_params["scene_source_type"] = "video"
                    # Store the final submitted video prompt (with all dispatch-time suffixes)
                    submitted_video = params.get("submitted_video_prompt")
                    if submitted_video:
                        scene_params["submitted_video_prompt"] = submitted_video
                    scene.parameters = scene_params
                    logger.info(f"[{job_id_str}] VHS fallback: set chosen_video_path")

                # Extract last frame
                try:
                    from backend.services.video.ffmpeg import extract_frame, get_media_info, _ensure_frame_dimensions

                    lf_filename = Path(filename).stem + "_lastframe.png"
                    lf_path = output_dir / lf_filename
                    target_w = params.get("width", 0)
                    target_h = params.get("height", 0)

                    info = await asyncio.to_thread(get_media_info, str(local_path))
                    vid_duration = info.get("duration", 0)
                    vid_fps = info.get("fps", 24)
                    extract_time = max(0, vid_duration - (1.0 / vid_fps))

                    await asyncio.to_thread(extract_frame, str(local_path), str(lf_path), extract_time)

                    if target_w > 0 and target_h > 0:
                        await asyncio.to_thread(_ensure_frame_dimensions, str(lf_path), target_w, target_h)

                    lf_rel_path = str(lf_path.relative_to(app_cfg.project_dir))
                    scene = await session.get(Scene, scene_id)
                    if scene:
                        scene_params = dict(scene.parameters or {})
                        scene_params["video_last_frame_path"] = lf_rel_path
                        scene.parameters = scene_params
                        logger.info(f"[{job_id_str}] VHS fallback: extracted last frame")
                except Exception as e:
                    logger.warning(f"[{job_id_str}] VHS fallback: last frame extraction failed: {e}")

            elif media_type == "image" and scene_id:
                scene = await session.get(Scene, scene_id)
                if scene:
                    scene_params = dict(scene.parameters or {})
                    frame_type = params.get("frame_type", "first")
                    if frame_type == "last":
                        scene_params["chosen_last_frame_path"] = rel_path
                    else:
                        scene_params["chosen_image_path"] = rel_path
                    scene.parameters = scene_params

            await session.commit()
            created_asset_ids.append(asset.id)

        return created_asset_ids
