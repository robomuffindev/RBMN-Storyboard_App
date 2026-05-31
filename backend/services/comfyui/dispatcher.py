"""
ComfyUI Multi-Instance Load Balancer

Manages multiple ComfyUI workers and intelligently distributes workflow jobs
based on capability requirements and current load.
"""

import logging
import time
from dataclasses import dataclass, field
from typing import Optional, Dict, List, Set, Any, Generator
from datetime import datetime

from .client import ComfyUIClient, ComfyUIConnectionError, ComfyUIVRAMError, ComfyUIWorkflowError

logger = logging.getLogger(__name__)


@dataclass
class ComfyWorker:
    """Represents a ComfyUI worker instance."""

    url: str
    healthy: bool = True
    in_flight: int = 0
    capabilities: Set[str] = field(default_factory=set)  # e.g., {"klein", "ltx", "upscale"}
    models: Set[str] = field(default_factory=set)  # e.g., {"SD15", "SD3"}
    last_check: datetime = field(default_factory=datetime.now)
    is_runpod: bool = False  # True if this worker was added via RunPod integration

    def __hash__(self):
        return hash(self.url)

    def __eq__(self, other):
        if isinstance(other, ComfyWorker):
            return self.url == other.url
        return self.url == other


class ComfyDispatcher:
    """
    Load balancer for multiple ComfyUI instances.

    Selects workers based on capability requirements and current load.
    """

    def __init__(self):
        """Initialize dispatcher."""
        self.workers: Dict[str, ComfyWorker] = {}
        self.clients: Dict[str, ComfyUIClient] = {}
        logger.info("ComfyDispatcher initialized")

    def add_worker(self, url: str, skip_health_check: bool = False, is_runpod: bool = False) -> ComfyWorker:
        """
        Add a ComfyUI worker to the dispatcher.

        Args:
            url: ComfyUI server URL
            skip_health_check: If True, skip initial connectivity test (RunPod workers
                              are pre-validated via RunPodManager health check)
            is_runpod: If True, mark this worker as a RunPod-managed worker so it
                      won't be selected for job types that don't have a RunPod pod configured.

        Returns:
            ComfyWorker instance

        Raises:
            ComfyUIConnectionError: If unable to connect (unless skip_health_check)
        """
        if url in self.workers:
            logger.info(f"Worker {url} already registered")
            return self.workers[url]

        logger.info(f"Adding worker: {url} (skip_health_check={skip_health_check}, is_runpod={is_runpod})")

        # Test connectivity
        try:
            client = ComfyUIClient(url, skip_health_check=skip_health_check)
            self.clients[url] = client
        except ComfyUIConnectionError as e:
            logger.error(f"Failed to connect to {url}: {e}")
            raise

        # Discover capabilities
        worker = ComfyWorker(url=url, healthy=True, is_runpod=is_runpod)
        self.workers[url] = worker

        if not skip_health_check:
            try:
                self.discover_capabilities(worker)
            except Exception as e:
                logger.warning(f"Failed to discover capabilities for {url}: {e}")

        logger.info(f"Worker {url} added: {worker.capabilities}")
        return worker

    def remove_worker(self, url: str) -> None:
        """
        Remove a worker from the dispatcher.

        Args:
            url: Worker URL
        """
        if url in self.workers:
            del self.workers[url]
        if url in self.clients:
            del self.clients[url]
        logger.info(f"Worker removed: {url}")

    def health_check_all(self) -> Dict[str, bool]:
        """
        Check health of all workers.

        Returns:
            Dict mapping worker URLs to health status
        """
        results = {}
        now = datetime.now()

        for url, worker in self.workers.items():
            try:
                client = self.clients.get(url)
                if not client:
                    client = ComfyUIClient(url)
                    self.clients[url] = client

                stats = client.get_system_stats()
                worker.healthy = True
                worker.last_check = now

                # Check VRAM threshold (warn if >90%)
                if "ram" in stats:
                    used = stats["ram"].get("used", 0)
                    total = stats["ram"].get("total", 1)
                    usage_pct = (used / total) * 100 if total > 0 else 0
                    if usage_pct > 90:
                        logger.warning(f"{url} VRAM usage: {usage_pct:.1f}%")

                results[url] = True
                logger.debug(f"Health check passed: {url}")

            except Exception as e:
                worker.healthy = False
                worker.last_check = now
                results[url] = False
                logger.warning(f"Health check failed for {url}: {e}")

        return results

    def discover_capabilities(self, worker: ComfyWorker) -> None:
        """
        Discover worker capabilities by querying object_info.

        Updates worker.capabilities and worker.models based on available nodes.

        Args:
            worker: ComfyWorker instance

        Raises:
            ComfyUIConnectionError: If unable to query
        """
        client = self.clients.get(worker.url)
        if not client:
            raise ComfyUIConnectionError(f"No client for {worker.url}")

        logger.debug(f"Discovering capabilities for {worker.url}")

        try:
            obj_info = client.get_object_info()

            # Detect models based on node availability
            capabilities = set()
            models = set()

            node_types = set(obj_info.keys())

            # Check for Klein nodes
            if any("Klein" in n or "klein" in n.lower() for n in node_types):
                capabilities.add("klein")

            # Check for LTX nodes
            if any("LTX" in n or "ltx" in n.lower() for n in node_types):
                capabilities.add("ltx")

            # Check for upscale nodes
            if any("Upscale" in n or "upscale" in n.lower() for n in node_types):
                capabilities.add("upscale")

            # Check for inpainting nodes
            if any("Inpaint" in n or "inpaint" in n.lower() for n in node_types):
                capabilities.add("inpaint")

            # Detect models from checkpoint loaders
            # object_info format: {"CheckpointLoaderSimple": {"input": {"required": {"ckpt_name": [["model1.safetensors", ...]]}}}}
            if "CheckpointLoaderSimple" in obj_info:
                loader_info = obj_info["CheckpointLoaderSimple"]
                try:
                    ckpt_field = loader_info.get("input", {}).get("required", {}).get("ckpt_name", [])
                    # ckpt_field is typically [["list", "of", "checkpoint", "names"]]
                    checkpoint_list = ckpt_field[0] if ckpt_field and isinstance(ckpt_field[0], list) else []
                    for ckpt in checkpoint_list:
                        ckpt_lower = str(ckpt).lower()
                        if "sd_1" in ckpt_lower or "sd15" in ckpt_lower or "v1-5" in ckpt_lower:
                            models.add("SD15")
                        if "sd3" in ckpt_lower or "sd_3" in ckpt_lower:
                            models.add("SD3")
                        if "flux" in ckpt_lower:
                            models.add("FLUX")
                        if "klein" in ckpt_lower:
                            models.add("KLEIN")
                        if "ltx" in ckpt_lower:
                            models.add("LTX")
                except (IndexError, TypeError, AttributeError) as e:
                    logger.warning(f"Failed to parse checkpoint list: {e}")

            # Also scan unet / diffusion-model loaders for GGUF and
            # safetensors files. Klein 9B and LTX 2.3 are loaded via
            # UnetLoaderGGUF or UNETLoader, NOT via CheckpointLoaderSimple,
            # so without this scan auto-discovery never finds them and
            # Klein jobs fail capability/model checks at dispatch time.
            # The /object_info field shape is identical to ckpt_name:
            # the available file list is the first element of the tuple.
            for unet_loader in ("UnetLoaderGGUF", "UNETLoader"):
                if unet_loader not in obj_info:
                    continue
                try:
                    unet_field = (
                        obj_info[unet_loader]
                        .get("input", {})
                        .get("required", {})
                        .get("unet_name", [])
                    )
                    unet_list = (
                        unet_field[0]
                        if unet_field and isinstance(unet_field[0], list)
                        else []
                    )
                    for unet in unet_list:
                        unet_lower = str(unet).lower()
                        if "klein" in unet_lower or "flux" in unet_lower:
                            models.add("FLUX")
                            capabilities.add("klein")
                        if "ltx" in unet_lower:
                            models.add("LTX")
                            capabilities.add("ltx")
                except (IndexError, TypeError, AttributeError) as e:
                    logger.warning(
                        f"Failed to parse {unet_loader} unet list for "
                        f"{worker.url}: {e}"
                    )

            worker.capabilities = capabilities
            worker.models = models
            logger.info(f"Worker {worker.url}: capabilities={capabilities}, models={models}")

        except Exception as e:
            logger.error(f"Failed to discover capabilities: {e}")
            raise

    def select_worker(
        self,
        required_caps: Optional[Set[str]] = None,
        required_models: Optional[Set[str]] = None,
        exclude_runpod: bool = False,
        reserve: bool = False,
    ) -> Optional[ComfyWorker]:
        """
        Select best worker based on requirements and load.

        Selection strategy:
        1. Filter by required capabilities and models
        2. Among healthy workers, select least-loaded
        3. Tiebreak by most recent health check

        Args:
            required_caps: Required capabilities (e.g., {"klein", "upscale"})
            required_models: Required models (e.g., {"SD15"})
            exclude_runpod: If True, exclude RunPod-managed workers from selection.
                           Used when falling back to local workers for job types
                           that don't have a RunPod pod configured.
            reserve: If True, immediately increment ``in_flight`` on the
                     selected worker.  This prevents a concurrent dispatch
                     task from picking the same worker before the job is
                     actually submitted (there are ``await`` points between
                     selection and submission).  The caller must call
                     ``release_worker()`` if the job fails before
                     ``submit_job()`` is reached.

        Returns:
            Selected ComfyWorker, or None if no match

        Raises:
            ValueError: If no healthy workers available
        """
        required_caps = required_caps or set()
        required_models = required_models or set()

        logger.debug(
            f"Selecting worker: caps={required_caps}, models={required_models}, "
            f"exclude_runpod={exclude_runpod}, total_workers={len(self.workers)}"
        )

        # Filter by health (and optionally exclude RunPod workers)
        healthy = [w for w in self.workers.values() if w.healthy]
        if exclude_runpod:
            healthy = [w for w in healthy if not w.is_runpod]
        if not healthy:
            raise ValueError("No healthy workers available")

        # Filter by capabilities
        capable = healthy
        if required_caps:
            capable = [w for w in healthy if required_caps.issubset(w.capabilities)]

        if not capable:
            logger.warning(
                f"No workers available with required capabilities {required_caps}. "
                f"Healthy workers and their caps: "
                f"{{{', '.join(f'{w.url}: {w.capabilities}' for w in healthy)}}}"
            )
            return None

        # Filter by models (only if workers actually have models configured)
        if required_models:
            # Only apply model filter to workers that have models declared.
            # Workers with empty model sets are assumed to support whatever
            # models their capability implies (e.g., ltx capability = LTX model).
            workers_with_models = [w for w in capable if w.models]
            if workers_with_models:
                # Some workers declare models — filter those
                model_capable = [w for w in capable if required_models.issubset(w.models) or not w.models]
                if not model_capable:
                    logger.warning(
                        f"No workers available with required models {required_models}. "
                        f"Capable workers and their models: "
                        f"{{{', '.join(f'{w.url}: {w.models}' for w in capable)}}}"
                    )
                    return None
                capable = model_capable
            # else: no workers have models configured — skip model filter entirely

        # Select least-loaded
        selected = min(capable, key=lambda w: (w.in_flight, -w.last_check.timestamp()))

        if reserve:
            selected.in_flight += 1
            logger.info(
                f"Selected & reserved worker {selected.url} "
                f"(in_flight now {selected.in_flight})"
            )
        else:
            logger.info(f"Selected worker {selected.url} (load={selected.in_flight})")

        return selected

    def release_worker(self, worker_url: str) -> None:
        """Release a reservation made by ``select_worker(reserve=True)``.

        Call this when a job fails before reaching ``submit_job()`` so the
        ``in_flight`` counter stays accurate.
        """
        worker = self.workers.get(worker_url)
        if worker:
            worker.in_flight = max(0, worker.in_flight - 1)
            logger.debug(
                f"Released worker {worker_url} (in_flight now {worker.in_flight})"
            )

    def submit_job(
        self,
        workflow: dict,
        worker_url: Optional[str] = None,
        already_reserved: bool = False,
    ) -> str:
        """
        Submit a workflow to a worker.

        Args:
            workflow: ComfyUI workflow dict
            worker_url: Specific worker URL, or None to auto-select
            already_reserved: If True, ``in_flight`` was already incremented
                              by ``select_worker(reserve=True)`` — skip the
                              increment here to avoid double-counting.

        Returns:
            Prompt ID

        Raises:
            ValueError: If worker not found
            ComfyUIConnectionError: If submission fails
        """
        if worker_url:
            if worker_url not in self.workers:
                raise ValueError(f"Worker not found: {worker_url}")
            worker = self.workers[worker_url]
        else:
            worker = self.select_worker()
            if worker is None:
                raise ValueError("No capable workers available for this job")

        client = self.clients.get(worker.url)
        if not client:
            raise ComfyUIConnectionError(f"No client for {worker.url}")

        try:
            result = client.queue_prompt(workflow)
            prompt_id = result["prompt_id"]

            # Check for node validation errors in the prompt response
            node_errors = result.get("node_errors")
            if node_errors:
                import json as _json_ne
                logger.error(
                    f"Job {prompt_id}: ComfyUI NODE VALIDATION ERRORS: "
                    f"{_json_ne.dumps(node_errors, default=str)[:2000]}"
                )

            if not already_reserved:
                worker.in_flight += 1
            logger.info(f"Job {prompt_id} submitted to {worker.url} (in_flight={worker.in_flight})")
            return prompt_id
        except Exception as e:
            logger.error(f"Failed to submit job: {e}")
            raise

    def stream_and_wait(
        self,
        worker_url: str,
        prompt_id: str,
        on_progress: Optional[Any] = None,
        client_id: Optional[str] = None,
    ) -> dict:
        """
        Wait for a job to complete via WebSocket and return execution history.

        Optionally calls on_progress(msg) for each WebSocket message.

        Args:
            worker_url: Worker URL
            prompt_id: Prompt ID
            on_progress: Optional callback for progress messages
            client_id: Optional WebSocket client ID

        Returns:
            Execution history dict from /history/{prompt_id}

        Raises:
            ValueError: If worker not found
            ComfyUIConnectionError: If stream fails
        """
        if worker_url not in self.workers:
            raise ValueError(f"Worker not found: {worker_url}")

        client = self.clients.get(worker_url)
        if not client:
            raise ComfyUIConnectionError(f"No client for {worker_url}")

        worker = self.workers[worker_url]

        try:
            # Capture per-node output data from WebSocket `executed` messages.
            # Some ComfyUI servers never include VHS_VideoCombine output in the
            # /history API, but they DO send it via WebSocket during execution.
            # Format: {"type": "executed", "data": {"node": "1087",
            #   "output": {"gifs": [{"filename": "...", "subfolder": "", "type": "output"}]}}}
            ws_node_outputs: Dict[str, dict] = {}

            # Stream messages until completion
            for msg in client.stream_prompt(prompt_id, client_id):
                # Capture executed node outputs
                msg_type = msg.get("type", "")
                msg_data = msg.get("data", {})
                if msg_type == "executed" and "output" in msg_data:
                    node_id = msg_data.get("node", "unknown")
                    ws_node_outputs[node_id] = msg_data["output"]
                    logger.info(
                        f"WS captured output for node {node_id}: "
                        f"keys={list(msg_data['output'].keys())}"
                    )

                if on_progress:
                    try:
                        on_progress(msg)
                    except Exception as cb_err:
                        logger.warning(f"Progress callback error: {cb_err}")

            if ws_node_outputs:
                logger.info(
                    f"Job {prompt_id}: captured WS outputs for {len(ws_node_outputs)} node(s): "
                    f"{list(ws_node_outputs.keys())}"
                )

            # Get authoritative history after completion.
            # Retry a few times if outputs don't contain file-like entries.
            # VHS_VideoCombine writes the video file to disk and then
            # updates the history — there can be a race where the history
            # is populated with non-file output nodes (e.g. MathExpression)
            # but the VHS node hasn't finished yet.
            import time as _time_hist

            # File output keys used by ComfyUI nodes.
            # VHS_VideoCombine → "gifs", SaveImage → "images", etc.
            _FILE_OUTPUT_KEYS = {"images", "gifs", "videos"}

            def _has_file_outputs(hist: dict) -> bool:
                """Check if history contains actual file outputs (not just values)."""
                outputs = hist.get("outputs", {})
                if not outputs:
                    return False
                for node_id, node_out in outputs.items():
                    if not isinstance(node_out, dict):
                        continue
                    # Check known file keys
                    for key in _FILE_OUTPUT_KEYS:
                        items = node_out.get(key, [])
                        if isinstance(items, list) and any(
                            isinstance(item, dict) and item.get("filename")
                            for item in items
                        ):
                            return True
                    # Fallback: any list-of-dicts with "filename" key
                    for key, val in node_out.items():
                        if key in _FILE_OUTPUT_KEYS:
                            continue
                        if isinstance(val, list) and any(
                            isinstance(item, dict) and item.get("filename")
                            for item in val
                        ):
                            return True
                return False

            history = client.get_history(prompt_id)

            # ── Log full execution status for diagnostics ──
            import json as _json_status
            status_dict = history.get("status", {})
            status_str = status_dict.get("status_str", "unknown")
            completed = status_dict.get("completed", "unknown")
            messages = status_dict.get("messages", [])
            logger.info(
                f"Job {prompt_id}: execution status={status_str}, "
                f"completed={completed}, messages={len(messages)}"
            )
            if messages:
                for msg_entry in messages[:20]:
                    if isinstance(msg_entry, (list, tuple)) and len(msg_entry) >= 2:
                        msg_type, msg_body = msg_entry[0], msg_entry[1]
                        logger.warning(
                            f"Job {prompt_id}: STATUS MESSAGE [{msg_type}]: "
                            f"{_json_status.dumps(msg_body, default=str)[:500]}"
                        )
                    else:
                        logger.warning(
                            f"Job {prompt_id}: STATUS MESSAGE: "
                            f"{_json_status.dumps(msg_entry, default=str)[:500]}"
                        )
            if not completed or status_str not in ("success", "unknown"):
                logger.error(
                    f"Job {prompt_id}: EXECUTION DID NOT COMPLETE SUCCESSFULLY. "
                    f"Full status: {_json_status.dumps(status_dict, default=str)[:2000]}"
                )
                # Check for OOM errors in status messages — ComfyUI catches
                # OOM internally, marks prompt as "executed" but with error
                # status, and does NOT always send an `execution_error` WS msg.
                status_text = _json_status.dumps(status_dict, default=str).lower()
                if "outofmemory" in status_text or "out of memory" in status_text or "cuda" in status_text:
                    raise ComfyUIVRAMError(
                        f"ComfyUI execution OOM: status={status_str}, "
                        f"check server console for details"
                    )
                # For non-OOM errors, raise a generic error so the job fails.
                # This covers status_str == "error", "interrupted", or any
                # other non-success value.  Previously only "error" was caught,
                # allowing interrupted/partial executions to slip through as
                # "completed".
                # Extract error details from messages if available
                err_detail = ""
                for msg_entry in messages:
                    if isinstance(msg_entry, (list, tuple)) and len(msg_entry) >= 2:
                        if msg_entry[0] in ("execution_error", "execution_interrupted"):
                            err_detail = str(msg_entry[1])[:500]
                            break
                raise ComfyUIWorkflowError(
                    f"ComfyUI execution failed: status={status_str}. {err_detail}"
                )

            if not _has_file_outputs(history):
                for _retry in range(1, 11):
                    logger.warning(
                        f"Job {prompt_id}: no file outputs in history on attempt "
                        f"{_retry}, retrying in 3s..."
                    )
                    _time_hist.sleep(3)
                    history = client.get_history(prompt_id)
                    if _has_file_outputs(history):
                        logger.info(
                            f"Job {prompt_id}: file outputs found on retry {_retry}"
                        )
                        break
                else:
                    import json as _json_retry
                    raw_out = history.get("outputs", {})
                    logger.error(
                        f"Job {prompt_id}: no file outputs after 10 retries (30s). "
                        f"Raw history keys: {list(history.keys())}, "
                        f"outputs: {_json_retry.dumps(raw_out, default=str)[:1000]}"
                    )

                    # ── Merge WS-captured outputs into history ──
                    # If the /history API never included VHS output but we
                    # captured it from WebSocket `executed` messages, inject
                    # those outputs so the downstream handler can extract files.
                    if ws_node_outputs:
                        merged_count = 0
                        if "outputs" not in history:
                            history["outputs"] = {}
                        for ws_node_id, ws_out in ws_node_outputs.items():
                            if ws_node_id not in history["outputs"]:
                                history["outputs"][ws_node_id] = ws_out
                                merged_count += 1
                                logger.info(
                                    f"Job {prompt_id}: merged WS output for "
                                    f"node {ws_node_id} into history"
                                )
                        if merged_count > 0:
                            logger.info(
                                f"Job {prompt_id}: merged {merged_count} WS "
                                f"node output(s) — re-checking for file outputs"
                            )
                            if _has_file_outputs(history):
                                logger.info(
                                    f"Job {prompt_id}: file outputs found "
                                    f"via WS merge!"
                                )

            logger.info(f"Job {prompt_id} completed on {worker_url}")
            return history

        except Exception as e:
            logger.error(f"Error waiting for {prompt_id}: {e}")
            raise
        finally:
            worker.in_flight = max(0, worker.in_flight - 1)
            worker.last_check = datetime.now()  # Update for round-robin tiebreak

    def get_status(self) -> Dict[str, Any]:
        """
        Get overall dispatcher status.

        Returns:
            Dict with worker statuses and queue info
        """
        return {
            "workers": {
                url: {
                    "healthy": w.healthy,
                    "in_flight": w.in_flight,
                    "capabilities": list(w.capabilities),
                    "models": list(w.models),
                    "last_check": w.last_check.isoformat(),
                }
                for url, w in self.workers.items()
            },
            "total_workers": len(self.workers),
            "healthy_workers": sum(1 for w in self.workers.values() if w.healthy),
        }
