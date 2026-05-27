"""
ComfyUI HTTP and WebSocket Client

Provides low-level communication with ComfyUI API for workflow submission,
monitoring, and resource management.
"""

import json
import logging
import uuid
from typing import Optional, Generator, Any, Dict
from urllib.parse import urljoin

import requests
import websocket

logger = logging.getLogger(__name__)


class ComfyUIConnectionError(Exception):
    """Raised when unable to connect to ComfyUI server."""
    pass


class ComfyUIWorkflowError(Exception):
    """Raised when workflow execution fails."""
    pass


class ComfyUIVRAMError(Exception):
    """Raised when VRAM is insufficient."""
    pass


class ComfyUIClient:
    """
    HTTP and WebSocket client for ComfyUI.

    Handles workflow queuing, monitoring, image upload, and system management.
    """

    def __init__(self, base_url: str, timeout: int = 30, skip_health_check: bool = False):
        """
        Initialize ComfyUI client.

        Args:
            base_url: ComfyUI server URL (e.g., "http://localhost:8188")
            timeout: Request timeout in seconds
            skip_health_check: If True, skip initial connectivity test (for RunPod workers added dynamically)
        """
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()

        # Disable SSL verification for RunPod proxy URLs (they use their own certs)
        if "runpod.net" in self.base_url or self.base_url.startswith("https://"):
            self.session.verify = False
            # Suppress InsecureRequestWarning for RunPod
            import urllib3
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

        if skip_health_check:
            logger.info(f"ComfyUI client created for {self.base_url} (health check skipped)")
            return

        # Test connectivity
        try:
            resp = self.session.get(
                urljoin(self.base_url, "/system_stats"),
                timeout=self.timeout
            )
            resp.raise_for_status()
            logger.info(f"ComfyUI client connected to {self.base_url}")
        except requests.RequestException as e:
            raise ComfyUIConnectionError(f"Failed to connect to {self.base_url}: {e}")

    def _make_request(self, method: str, endpoint: str, **kwargs) -> Dict[str, Any]:
        """Make HTTP request to ComfyUI API."""
        url = urljoin(self.base_url, endpoint)
        try:
            resp = self.session.request(method, url, timeout=self.timeout, **kwargs)
            if resp.status_code >= 400:
                # Log the full response body before raise_for_status destroys it
                try:
                    error_body = resp.json()
                    logger.error(
                        f"ComfyUI {resp.status_code} response body for {method} {endpoint}: "
                        f"{json.dumps(error_body, indent=2)[:5000]}"
                    )
                except Exception:
                    logger.error(
                        f"ComfyUI {resp.status_code} response text for {method} {endpoint}: "
                        f"{resp.text[:3000]}"
                    )
            resp.raise_for_status()
            return resp.json() if resp.text else {}
        except requests.RequestException as e:
            logger.error(f"ComfyUI request failed: {method} {endpoint}: {e}")
            raise ComfyUIConnectionError(f"Request failed: {e}")

    def queue_prompt(self, workflow: dict) -> dict:
        """
        Queue a workflow for execution.

        Args:
            workflow: ComfyUI workflow dict

        Returns:
            Dict with 'prompt_id' and 'number' keys

        Raises:
            ComfyUIWorkflowError: If workflow is invalid
            ComfyUIConnectionError: If request fails
        """
        try:
            response = self._make_request(
                "POST",
                "/prompt",
                json={"prompt": workflow}
            )

            if "prompt_id" not in response:
                raise ComfyUIWorkflowError(f"Invalid response: {response}")

            # Log validation warnings/errors from ComfyUI
            node_errors = response.get("node_errors", {})
            if node_errors:
                logger.error(
                    f"ComfyUI VALIDATION ERRORS for prompt {response['prompt_id']}: "
                    f"{json.dumps(node_errors, indent=2)}"
                )
            error = response.get("error")
            if error:
                logger.error(
                    f"ComfyUI prompt error for {response['prompt_id']}: "
                    f"{json.dumps(error) if isinstance(error, dict) else error}"
                )

            logger.info(f"Queued prompt {response['prompt_id']}")
            return response
        except ComfyUIConnectionError:
            raise
        except Exception as e:
            raise ComfyUIWorkflowError(f"Failed to queue workflow: {e}")

    def get_history(self, prompt_id: str) -> dict:
        """
        Get execution history for a prompt.

        Args:
            prompt_id: Prompt ID

        Returns:
            Dict with execution history
        """
        response = self._make_request("GET", f"/history/{prompt_id}")
        return response.get(prompt_id, {})

    def get_full_history(self, prompt_id: str) -> dict:
        """
        Get the RAW full history response for a prompt (no extraction).

        Useful for diagnostics when the extracted history is missing expected outputs.

        Returns:
            The full response from /history/{prompt_id} as-is
        """
        return self._make_request("GET", f"/history/{prompt_id}")

    def get_recent_history(self, max_items: int = 3) -> dict:
        """
        Get recent execution history (all prompts, limited).

        Useful for diagnosing what outputs ComfyUI actually produces.

        Returns:
            Dict of prompt_id → history entries
        """
        try:
            return self._make_request("GET", f"/history?max_items={max_items}")
        except Exception as e:
            logger.warning(f"Failed to get recent history: {e}")
            return {}

    def upload_image(self, filepath: str, filename: str, overwrite: bool = True) -> dict:
        """
        Upload an image to ComfyUI.

        Args:
            filepath: Local file path
            filename: Name for the file on ComfyUI
            overwrite: If True, overwrite existing file with same name (default True)

        Returns:
            Dict with upload result including 'name' (actual stored filename)
        """
        try:
            with open(filepath, "rb") as f:
                files = {"image": (filename, f)}
                data = {}
                if overwrite:
                    data["overwrite"] = "true"
                response = self.session.post(
                    urljoin(self.base_url, "/upload/image"),
                    files=files,
                    data=data,
                    timeout=self.timeout
                )
                response.raise_for_status()
                result = response.json()
                actual_name = result.get("name", filename)
                logger.info(f"Uploaded image: {filename} -> stored as: {actual_name}")
                return result
        except Exception as e:
            logger.error(f"Image upload failed: {e}")
            raise ComfyUIConnectionError(f"Image upload failed: {e}")

    def get_queue(self) -> dict:
        """
        Get current queue status.

        Returns:
            Dict with 'queue_pending' and 'queue_running' lists
        """
        return self._make_request("GET", "/queue")

    def interrupt(self) -> None:
        """Interrupt current execution."""
        try:
            self._make_request("POST", "/interrupt")
            logger.info("Interrupted ComfyUI execution")
        except ComfyUIConnectionError:
            logger.warning("Failed to send interrupt command")

    def free_memory(self) -> None:
        """Free GPU memory and unload models."""
        try:
            self._make_request(
                "POST", "/free",
                json={"unload_models": True, "free_memory": True}
            )
            logger.info("Freed memory on ComfyUI")
        except ComfyUIConnectionError:
            logger.warning("Failed to free memory")

    def get_system_stats(self) -> dict:
        """
        Get system statistics.

        Returns:
            Dict with system info including VRAM usage
        """
        return self._make_request("GET", "/system_stats")

    def get_object_info(self) -> dict:
        """
        Get available node types and their info.

        Returns:
            Dict of node type definitions
        """
        return self._make_request("GET", "/object_info")

    def download_output(self, filename: str, subfolder: str = "", filetype: str = "output") -> bytes:
        """
        Download output file from ComfyUI.

        Args:
            filename: Output filename
            subfolder: Subfolder (e.g., "videos", "images")
            filetype: File type directory (default "output")

        Returns:
            File bytes
        """
        url = urljoin(self.base_url, f"/view?filename={filename}&subfolder={subfolder}&type={filetype}")
        try:
            resp = self.session.get(url, timeout=self.timeout)
            resp.raise_for_status()
            logger.info(f"Downloaded: {filename}")
            return resp.content
        except Exception as e:
            logger.error(f"Download failed: {e}")
            raise ComfyUIConnectionError(f"Download failed: {e}")

    def try_download_output(
        self, filename: str, subfolder: str = "", file_type: str = "output"
    ) -> Optional[bytes]:
        """
        Try to download an output file from ComfyUI. Returns bytes if found, None if not.

        This is a non-throwing fallback for when /history doesn't include VHS output.

        Args:
            filename: Output filename to try
            subfolder: Subfolder within the output directory
            file_type: ComfyUI file type — "output" or "temp"

        Returns:
            File bytes if found, None if not found or error
        """
        url = urljoin(
            self.base_url,
            f"/view?filename={filename}&subfolder={subfolder}&type={file_type}"
        )
        try:
            resp = self.session.get(url, timeout=self.timeout)
            if resp.status_code == 200 and len(resp.content) > 1000:
                logger.info(f"Fallback download succeeded: {filename} ({len(resp.content)} bytes)")
                return resp.content
            else:
                logger.info(
                    f"Fallback download miss: {filename} "
                    f"(status={resp.status_code}, size={len(resp.content)}, "
                    f"type={file_type}, subfolder='{subfolder}')"
                )
                return None
        except Exception as e:
            logger.info(f"Fallback download failed for {filename}: {e}")
            return None

    def stream_prompt(
        self, prompt_id: str, client_id: Optional[str] = None
    ) -> Generator[dict, None, None]:
        """
        Connect to WebSocket and stream execution messages for a specific prompt.

        Yields messages until the prompt completes (executing node = None)
        or an execution_error arrives for this prompt.

        Args:
            prompt_id: The prompt ID to monitor
            client_id: Client ID for the WebSocket (auto-generated if not provided)

        Yields:
            Dict messages from ComfyUI filtered to this prompt_id

        Raises:
            ComfyUIWorkflowError: If execution produces an error
            ComfyUIVRAMError: If a VRAM / OOM error occurs
            ComfyUIConnectionError: If WebSocket connection fails
        """
        client_id = client_id or str(uuid.uuid4())
        ws_url = self.base_url.replace("http", "ws", 1) + f"/ws?clientId={client_id}"

        ws = None
        try:
            # For WSS (RunPod proxy), skip SSL verification
            ws_kwargs = {"timeout": self.timeout}
            if ws_url.startswith("wss://"):
                import ssl
                ws_kwargs["sslopt"] = {"cert_reqs": ssl.CERT_NONE}
            ws = websocket.create_connection(ws_url, **ws_kwargs)
            # Set recv timeout so we can periodically poll history as fallback
            ws.settimeout(10)
            logger.info(f"Connected to WebSocket for prompt {prompt_id}")

            saw_progress_complete = False
            idle_cycles = 0  # Count of recv timeouts with no relevant messages
            # Track when progress last hit 100% — used to delay the
            # queue_remaining=0 shortcut so non-sampling nodes (VAE decode,
            # VHS_VideoCombine) have time to finish.
            import time as _ws_time
            _progress_complete_at: float = 0.0

            while True:
                try:
                    raw = ws.recv()

                    if not raw:
                        continue

                    # WebSocket may send binary preview frames — skip them
                    if isinstance(raw, bytes):
                        continue

                    msg = json.loads(raw)
                    msg_type = msg.get("type", "")
                    data = msg.get("data", {})
                    msg_prompt = data.get("prompt_id")

                    # Skip noisy monitor messages — don't reset idle counter
                    if msg_type in ("crystools.monitor",):
                        continue

                    # progress_state messages are very noisy (dozens per second).
                    # Don't log them, don't reset idle counter, but still yield
                    # them so the progress callback can use them.
                    if msg_type == "progress_state":
                        if msg_prompt and msg_prompt != prompt_id:
                            continue
                        yield msg
                        continue

                    # Log non-noisy messages for debugging
                    logger.info(
                        f"WS [{prompt_id[:8]}] type={msg_type} "
                        f"prompt={msg_prompt} node={data.get('node')} "
                        f"keys={list(data.keys())}"
                    )

                    # Only process messages for our prompt
                    if msg_prompt and msg_prompt != prompt_id:
                        continue

                    # Reset idle counter only for messages relevant to our prompt
                    idle_cycles = 0

                    yield msg

                    # Track when progress hits max
                    if msg_type == "progress":
                        value = data.get("value", 0)
                        max_val = data.get("max", 1)
                        if value >= max_val:
                            saw_progress_complete = True
                            _progress_complete_at = _ws_time.monotonic()

                    # Completion: executing with node=None means the whole graph finished
                    # This is the AUTHORITATIVE completion signal from ComfyUI.
                    if (
                        msg_type == "executing"
                        and data.get("node") is None
                    ):
                        logger.info(f"Prompt {prompt_id} completed (executing node=None)")
                        break

                    # Some ComfyUI versions send execution_complete or executed
                    if msg_type in ("execution_complete", "executed", "complete",
                                    "execution_success"):
                        if msg_prompt == prompt_id or msg_prompt is None:
                            logger.info(f"Prompt {prompt_id} completed ({msg_type})")
                            break

                    # Status message with queue_remaining=0 after progress = done.
                    # Some ComfyUI versions don't send `executing node=None` at
                    # all — they only send this status message.  Accept it as a
                    # valid completion signal.  The file-output retry in
                    # stream_and_wait() handles the case where VHS_VideoCombine
                    # hasn't finished writing to the history yet.
                    if msg_type == "status" and saw_progress_complete:
                        queue_remaining = (
                            data.get("status", {})
                            .get("exec_info", {})
                            .get("queue_remaining", -1)
                        )
                        if queue_remaining == 0:
                            elapsed_since_progress = (
                                _ws_time.monotonic() - _progress_complete_at
                            )
                            logger.info(
                                f"Prompt {prompt_id} completed "
                                f"(status queue_remaining=0, {elapsed_since_progress:.0f}s "
                                f"after last progress complete)"
                            )
                            break

                    # execution_cached means some/all nodes were cached — not an error
                    # but if all nodes are cached, executing node=None follows immediately
                    if msg_type == "execution_cached":
                        cached_nodes = data.get("nodes", [])
                        if cached_nodes:
                            logger.info(
                                f"WS [{prompt_id[:8]}] {len(cached_nodes)} nodes "
                                f"cached (execution skipped for these)"
                            )

                    # Execution interrupted (cancelled, timed out, etc.)
                    if msg_type == "execution_interrupted":
                        if msg_prompt == prompt_id or msg_prompt is None:
                            logger.error(
                                f"WS [{prompt_id[:8]}] EXECUTION INTERRUPTED: "
                                f"{json.dumps(data, default=str)[:500]}"
                            )
                            raise ComfyUIWorkflowError(
                                f"Execution interrupted: {json.dumps(data, default=str)[:300]}"
                            )

                    # Execution error
                    if msg_type == "execution_error":
                        if msg_prompt == prompt_id or msg_prompt is None:
                            error_msg = data.get("exception_message", "Unknown error")
                            exception_type = data.get("exception_type", "")

                            # Detect VRAM errors
                            if "OutOfMemory" in exception_type or "CUDA" in error_msg:
                                raise ComfyUIVRAMError(f"VRAM error: {error_msg}")

                            raise ComfyUIWorkflowError(f"Execution error: {error_msg}")

                except websocket.WebSocketTimeoutException:
                    # recv() timed out — no message in 10 seconds
                    idle_cycles += 1
                    idle_seconds = idle_cycles * 10

                    # If we saw 100% progress, poll the history API as fallback
                    if saw_progress_complete:
                        logger.info(
                            f"WS idle after 100% progress for prompt {prompt_id}, "
                            f"polling history (cycle {idle_cycles})"
                        )
                        try:
                            history = self._make_request("GET", f"/history/{prompt_id}")
                            prompt_history = history.get(prompt_id, {})
                            if prompt_history and prompt_history.get("outputs"):
                                logger.info(
                                    f"Prompt {prompt_id} completed (history poll fallback)"
                                )
                                break
                        except Exception as poll_err:
                            logger.warning(f"History poll failed: {poll_err}")

                    # Periodically check if the prompt is still running on ComfyUI
                    # (every 30s of idle time). Video generation can take 5-10+ minutes
                    # during model loading and frame processing with no WS messages.
                    if idle_cycles % 3 == 0:
                        try:
                            # First check history — maybe it completed
                            history = self._make_request("GET", f"/history/{prompt_id}")
                            prompt_history = history.get(prompt_id, {})
                            if prompt_history and prompt_history.get("outputs"):
                                logger.info(
                                    f"Prompt {prompt_id} completed (idle history check at {idle_seconds}s)"
                                )
                                break

                            # Check queue — is our prompt still pending/running?
                            queue = self.get_queue()
                            running = queue.get("queue_running", [])
                            pending = queue.get("queue_pending", [])
                            # ComfyUI queue format: each entry is [number, prompt_id, ...]
                            all_queued_ids = set()
                            for entry in running:
                                if len(entry) >= 2:
                                    all_queued_ids.add(str(entry[1]))
                            for entry in pending:
                                if len(entry) >= 2:
                                    all_queued_ids.add(str(entry[1]))

                            if prompt_id in all_queued_ids:
                                logger.info(
                                    f"Prompt {prompt_id} still in queue after {idle_seconds}s idle — continuing to wait"
                                )
                            else:
                                # Not in queue and no history — might have failed silently
                                logger.warning(
                                    f"Prompt {prompt_id} not in queue or history after {idle_seconds}s idle"
                                )
                                # Give it one more cycle in case history is still being written
                                if idle_cycles >= 6:
                                    raise ComfyUIConnectionError(
                                        f"Prompt {prompt_id} not in queue or history after {idle_seconds}s"
                                    )
                        except ComfyUIConnectionError:
                            raise
                        except Exception as poll_err:
                            logger.warning(f"Queue/history poll failed: {poll_err}")

                    # Ultimate safety: 15 minutes (90 cycles × 10s) absolute max
                    if idle_cycles >= 90:
                        logger.error(
                            f"Prompt {prompt_id}: absolute timeout after {idle_seconds}s idle"
                        )
                        # One final history check
                        try:
                            history = self._make_request("GET", f"/history/{prompt_id}")
                            prompt_history = history.get(prompt_id, {})
                            if prompt_history and prompt_history.get("outputs"):
                                logger.info(
                                    f"Prompt {prompt_id} completed (absolute timeout history check)"
                                )
                                break
                        except Exception:
                            pass
                        raise ComfyUIConnectionError(
                            f"Absolute timeout after {idle_seconds}s, prompt {prompt_id} may be stuck"
                        )

                    continue

                except json.JSONDecodeError:
                    logger.warning("Failed to decode WebSocket message")
                    continue
                except (ComfyUIWorkflowError, ComfyUIVRAMError):
                    raise

        except (ComfyUIWorkflowError, ComfyUIVRAMError):
            raise
        except Exception as e:
            logger.error(f"WebSocket error: {e}")
            raise ComfyUIConnectionError(f"WebSocket failed: {e}")
        finally:
            if ws:
                try:
                    ws.close()
                except Exception:
                    pass
