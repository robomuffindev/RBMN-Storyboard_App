"""
RunPod Pod Lifecycle Manager

Manages RunPod GPU pods for on-demand AI workloads:
- Start/stop/resume pods via the RunPod Python SDK
- Track idle time and auto-spindown after configurable timeout
- Provide pod status and health checks
- Override local ComfyUI/Whisper/LLM URLs with RunPod pod IPs when active
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
try:
    from enum import StrEnum
except ImportError:  # Python 3.10
    from enum import Enum

    class StrEnum(str, Enum):
        pass
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class ServiceType(StrEnum):
    """Service types that can be hosted on RunPod pods."""
    IMAGE = "image"
    VIDEO = "video"
    LLM = "llm"
    WHISPER = "whisper"


class PodState(StrEnum):
    """RunPod pod lifecycle states."""
    UNKNOWN = "unknown"
    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    EXITED = "exited"
    ERROR = "error"


@dataclass
class PodConfig:
    """Configuration for a single RunPod pod."""
    pod_id: str
    label: str  # User-friendly name
    service_type: str  # image, video, llm, whisper
    gpu_type_id: str = ""  # e.g. "NVIDIA GeForce RTX 4090"
    gpu_count: int = 1  # Number of GPUs to resume with (required by RunPod SDK)
    template_id: str = ""  # RunPod template ID (optional, for creating pods)
    api_port: int = 8188  # Port the service listens on (8188=ComfyUI, 7860=Gradio, etc.)
    enabled: bool = True

    @classmethod
    def from_dict(cls, d: dict) -> "PodConfig":
        return cls(
            pod_id=d.get("pod_id", ""),
            label=d.get("label", ""),
            service_type=d.get("service_type", "image"),
            gpu_type_id=d.get("gpu_type_id", ""),
            gpu_count=d.get("gpu_count", 1),
            template_id=d.get("template_id", ""),
            api_port=d.get("api_port", 8188),
            enabled=d.get("enabled", True),
        )

    def to_dict(self) -> dict:
        return {
            "pod_id": self.pod_id,
            "label": self.label,
            "service_type": self.service_type,
            "gpu_type_id": self.gpu_type_id,
            "gpu_count": self.gpu_count,
            "template_id": self.template_id,
            "api_port": self.api_port,
            "enabled": self.enabled,
        }


@dataclass
class PodStatus:
    """Runtime status for a RunPod pod."""
    pod_id: str
    state: PodState = PodState.UNKNOWN
    url: Optional[str] = None  # The reachable API URL when running
    gpu_type: str = ""
    uptime_seconds: int = 0
    cost_per_hr: float = 0.0
    last_activity: float = field(default_factory=time.time)
    error_message: str = ""


# ---------------------------------------------------------------------------
# RunPod SDK wrapper (uses `runpod` Python library under the hood)
# ---------------------------------------------------------------------------

def _ensure_runpod_sdk():
    """Import and return the runpod SDK, raising a clear error if not installed."""
    try:
        import runpod  # type: ignore
        return runpod
    except ImportError:
        raise RuntimeError(
            "The `runpod` Python package is not installed. "
            "Install it with: pip install runpod"
        )


class RunPodManager:
    """
    Singleton manager that handles RunPod pod lifecycle.

    Usage:
        manager = RunPodManager.get_instance()
        manager.configure(api_key, pods, idle_timeout)
        url = await manager.ensure_pod_running("image")
    """

    _instance: Optional["RunPodManager"] = None

    @classmethod
    def get_instance(cls) -> "RunPodManager":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self):
        self.api_key: str = ""
        self.idle_timeout_minutes: int = 30
        self.pods: Dict[str, PodConfig] = {}  # pod_id -> config
        self.pod_statuses: Dict[str, PodStatus] = {}  # pod_id -> status
        self._idle_task: Optional[asyncio.Task] = None
        self._configured: bool = False
        self._lock = asyncio.Lock()

    # ── Configuration ─────────────────────────────────────────────────

    def configure(
        self,
        api_key: str,
        pod_configs: List[dict],
        idle_timeout_minutes: int = 30,
    ) -> None:
        """Update manager configuration from settings."""
        self.api_key = api_key
        self.idle_timeout_minutes = idle_timeout_minutes

        # Parse pod configs
        self.pods = {}
        for cfg in (pod_configs or []):
            pc = PodConfig.from_dict(cfg)
            if pc.pod_id:
                self.pods[pc.pod_id] = pc

        # Initialize SDK
        if self.api_key:
            try:
                runpod = _ensure_runpod_sdk()
                runpod.api_key = self.api_key
                self._configured = True
                logger.info(f"RunPod configured with {len(self.pods)} pods, idle timeout {idle_timeout_minutes}m")
            except RuntimeError as e:
                logger.warning(f"RunPod SDK not available: {e}")
                self._configured = False
        else:
            self._configured = False

    @property
    def is_configured(self) -> bool:
        return self._configured and bool(self.api_key)

    # ── Pod Lookup by Service ────────────────────────────────────────

    def get_pods_for_service(self, service_type: str) -> List[PodConfig]:
        """Get all pods configured for a given service type."""
        return [p for p in self.pods.values() if p.service_type == service_type and p.enabled]

    # ── Pod Status ───────────────────────────────────────────────────

    async def get_pod_status(self, pod_id: str) -> PodStatus:
        """Query RunPod API for current pod status."""
        if not self.is_configured:
            return PodStatus(pod_id=pod_id, state=PodState.ERROR, error_message="RunPod not configured")

        try:
            runpod = _ensure_runpod_sdk()
            # RunPod SDK calls are synchronous — run in thread
            pod_info = await asyncio.to_thread(runpod.get_pod, pod_id)

            if not pod_info:
                return PodStatus(pod_id=pod_id, state=PodState.ERROR, error_message="Pod not found")

            # Log raw pod info for debugging
            logger.debug(f"RunPod API response for {pod_id}: desiredStatus={pod_info.get('desiredStatus')}, "
                         f"runtime={pod_info.get('runtime')}")

            # Parse the RunPod status
            desired = pod_info.get("desiredStatus", "").upper()
            runtime = pod_info.get("runtime", {}) or {}
            actual_ports = runtime.get("ports", []) or []
            gpu_type = pod_info.get("machine", {}).get("gpuDisplayName", "") if pod_info.get("machine") else ""
            uptime = runtime.get("uptimeInSeconds", 0) or 0

            # Determine state — be more generous about RUNNING detection
            # Some RunPod responses have runtime data but uptimeInSeconds=0
            # If we have runtime ports or gpuIds populated, the pod is running
            has_runtime = bool(runtime and (actual_ports or runtime.get("gpus") or uptime > 0))
            if desired == "RUNNING" and has_runtime:
                state = PodState.RUNNING
            elif desired == "RUNNING":
                state = PodState.STARTING
            elif desired in ("EXITED", "STOPPED"):
                state = PodState.STOPPED
            else:
                state = PodState.UNKNOWN

            # Build the reachable URL
            url = None
            if state == PodState.RUNNING:
                # RunPod provides a public URL per pod: https://{pod_id}-{port}.proxy.runpod.net
                pod_cfg = self.pods.get(pod_id)
                port = pod_cfg.api_port if pod_cfg else 8188
                url = f"https://{pod_id}-{port}.proxy.runpod.net"

            status = PodStatus(
                pod_id=pod_id,
                state=state,
                url=url,
                gpu_type=gpu_type,
                uptime_seconds=uptime,
                cost_per_hr=pod_info.get("costPerHr", 0) or 0,
                last_activity=self.pod_statuses.get(pod_id, PodStatus(pod_id=pod_id)).last_activity,
            )
            self.pod_statuses[pod_id] = status
            return status

        except Exception as e:
            logger.error(f"Failed to get pod status for {pod_id}: {e}")
            return PodStatus(pod_id=pod_id, state=PodState.ERROR, error_message=str(e))

    async def get_all_pod_statuses(self) -> List[dict]:
        """Get status for all configured pods."""
        results = []
        for pod_id in self.pods:
            status = await self.get_pod_status(pod_id)
            cfg = self.pods[pod_id]
            results.append({
                "pod_id": pod_id,
                "label": cfg.label,
                "service_type": cfg.service_type,
                "state": status.state.value,
                "url": status.url,
                "gpu_type": status.gpu_type,
                "uptime_seconds": status.uptime_seconds,
                "cost_per_hr": status.cost_per_hr,
                "error": status.error_message,
            })
        return results

    # ── Start / Stop ────────────────────────────────────────────────

    async def start_pod(self, pod_id: str) -> PodStatus:
        """Resume/start a stopped RunPod pod."""
        if not self.is_configured:
            return PodStatus(pod_id=pod_id, state=PodState.ERROR, error_message="RunPod not configured")

        async with self._lock:
            try:
                runpod = _ensure_runpod_sdk()
                # Get gpu_count from pod config (default 1)
                pod_cfg = self.pods.get(pod_id)
                gpu_count = pod_cfg.gpu_count if pod_cfg else 1
                logger.info(f"Starting RunPod pod {pod_id} (gpu_count={gpu_count})...")
                await asyncio.to_thread(runpod.resume_pod, pod_id, gpu_count)
                status = PodStatus(pod_id=pod_id, state=PodState.STARTING)
                status.last_activity = time.time()
                self.pod_statuses[pod_id] = status
                return status
            except Exception as e:
                logger.error(f"Failed to start pod {pod_id}: {e}")
                return PodStatus(pod_id=pod_id, state=PodState.ERROR, error_message=str(e))

    async def stop_pod(self, pod_id: str) -> PodStatus:
        """Stop a running RunPod pod (preserves data, stops billing)."""
        if not self.is_configured:
            return PodStatus(pod_id=pod_id, state=PodState.ERROR, error_message="RunPod not configured")

        async with self._lock:
            try:
                runpod = _ensure_runpod_sdk()
                logger.info(f"Stopping RunPod pod {pod_id}...")
                await asyncio.to_thread(runpod.stop_pod, pod_id)
                status = PodStatus(pod_id=pod_id, state=PodState.STOPPING)
                self.pod_statuses[pod_id] = status
                return status
            except Exception as e:
                logger.error(f"Failed to stop pod {pod_id}: {e}")
                return PodStatus(pod_id=pod_id, state=PodState.ERROR, error_message=str(e))

    # ── Ensure Running (auto-start) ─────────────────────────────────

    async def ensure_pod_running(
        self,
        service_type: str,
        timeout_seconds: int = 300,
        poll_interval: int = 5,
    ) -> Optional[str]:
        """
        Ensure at least one pod for the given service type is running.
        Returns the API URL of a running pod, or None if unavailable.

        If the pod is stopped, it will be auto-started and this call will
        block until the pod is ready or the timeout is reached.
        """
        pods = self.get_pods_for_service(service_type)
        if not pods:
            return None

        # Check if any pod is already running
        for pod_cfg in pods:
            status = await self.get_pod_status(pod_cfg.pod_id)
            if status.state == PodState.RUNNING and status.url:
                # Mark activity
                status.last_activity = time.time()
                self.pod_statuses[pod_cfg.pod_id] = status
                return status.url

        # No running pod — start the first available one
        pod_cfg = pods[0]
        status = await self.get_pod_status(pod_cfg.pod_id)

        if status.state in (PodState.STOPPED, PodState.EXITED, PodState.UNKNOWN):
            start_result = await self.start_pod(pod_cfg.pod_id)
            if start_result.state == PodState.ERROR:
                logger.error(f"Failed to start pod {pod_cfg.pod_id}: {start_result.error_message}")
                return None

        # Wait for pod to become ready
        start_time = time.time()
        while time.time() - start_time < timeout_seconds:
            status = await self.get_pod_status(pod_cfg.pod_id)
            if status.state == PodState.RUNNING and status.url:
                # Additional health check — try to reach the service
                healthy = await self._health_check(status.url, service_type)
                if healthy:
                    status.last_activity = time.time()
                    self.pod_statuses[pod_cfg.pod_id] = status
                    logger.info(f"RunPod pod {pod_cfg.pod_id} ready at {status.url}")
                    return status.url
            elif status.state == PodState.ERROR:
                logger.error(f"Pod {pod_cfg.pod_id} in error state: {status.error_message}")
                return None

            await asyncio.sleep(poll_interval)

        logger.error(f"Timed out waiting for pod {pod_cfg.pod_id} to start ({timeout_seconds}s)")
        return None

    async def _health_check(self, url: str, service_type: str) -> bool:
        """Quick health check to see if the service is responding."""
        try:
            import httpx
            check_url = url.rstrip("/")

            async with httpx.AsyncClient(timeout=10.0, verify=False) as client:
                if service_type in ("image", "video"):
                    # ComfyUI health check
                    check_endpoint = f"{check_url}/system_stats"
                    logger.debug(f"RunPod health check: GET {check_endpoint}")
                    resp = await client.get(check_endpoint)
                    if resp.status_code == 200:
                        logger.info(f"RunPod health check passed: {check_url}")
                        return True
                    else:
                        logger.warning(f"RunPod health check failed: {check_endpoint} returned {resp.status_code}")
                        return False
                elif service_type == "whisper":
                    # Gradio health check
                    resp = await client.get(f"{check_url}/info")
                    if resp.status_code != 200:
                        resp = await client.get(check_url)
                    return resp.status_code == 200
                elif service_type == "llm":
                    # Generic LLM API health check
                    resp = await client.get(f"{check_url}/health")
                    if resp.status_code != 200:
                        resp = await client.get(f"{check_url}/v1/models")
                    return resp.status_code == 200
                else:
                    return True
        except Exception as e:
            logger.warning(f"RunPod health check exception for {url}: {e}")
            return False

    # ── Activity Tracking ────────────────────────────────────────────

    def record_activity(self, service_type: str) -> None:
        """Record that a service was used (resets idle timer)."""
        for pod_cfg in self.get_pods_for_service(service_type):
            if pod_cfg.pod_id in self.pod_statuses:
                self.pod_statuses[pod_cfg.pod_id].last_activity = time.time()

    # ── Idle Spindown Background Task ────────────────────────────────

    async def start_idle_monitor(self) -> None:
        """Start the background idle monitor task."""
        if self._idle_task and not self._idle_task.done():
            return
        self._idle_task = asyncio.create_task(self._idle_monitor_loop())
        logger.info("RunPod idle monitor started")

    async def stop_idle_monitor(self) -> None:
        """Stop the background idle monitor task."""
        if self._idle_task:
            self._idle_task.cancel()
            try:
                await self._idle_task
            except asyncio.CancelledError:
                pass
            self._idle_task = None
            logger.info("RunPod idle monitor stopped")

    async def _idle_monitor_loop(self) -> None:
        """Background loop that checks for idle pods and stops them."""
        while True:
            try:
                await asyncio.sleep(60)  # Check every minute

                if not self.is_configured or self.idle_timeout_minutes <= 0:
                    continue

                timeout_secs = self.idle_timeout_minutes * 60
                now = time.time()

                for pod_id, status in list(self.pod_statuses.items()):
                    if status.state != PodState.RUNNING:
                        continue

                    idle_secs = now - status.last_activity
                    if idle_secs > timeout_secs:
                        cfg = self.pods.get(pod_id)
                        label = cfg.label if cfg else pod_id
                        logger.info(
                            f"RunPod pod '{label}' ({pod_id}) idle for "
                            f"{idle_secs / 60:.1f}m (timeout: {self.idle_timeout_minutes}m). Stopping..."
                        )
                        await self.stop_pod(pod_id)

            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"Idle monitor error: {e}")

    # ── URL Resolution (for overriding local URLs) ───────────────────

    def get_override_url(self, service_type: str) -> Optional[str]:
        """
        If RunPod is configured and a pod is running for this service,
        return its URL to override the locally-configured URL.
        Returns None if no override is available (use local config).
        """
        if not self.is_configured:
            return None

        for pod_cfg in self.get_pods_for_service(service_type):
            status = self.pod_statuses.get(pod_cfg.pod_id)
            if status and status.state == PodState.RUNNING and status.url:
                return status.url
        return None

    # ── Test Connection ──────────────────────────────────────────────

    async def test_api_key(self, api_key: str) -> dict:
        """Test a RunPod API key by listing pods."""
        try:
            runpod = _ensure_runpod_sdk()
            old_key = runpod.api_key
            runpod.api_key = api_key
            try:
                pods = await asyncio.to_thread(runpod.get_pods)
                pod_count = len(pods) if pods else 0
                pod_names = [p.get("name", p.get("id", "?")) for p in (pods or [])[:5]]
                return {
                    "success": True,
                    "message": f"Connected. Found {pod_count} pod(s).",
                    "pods": pod_names,
                }
            finally:
                runpod.api_key = old_key
        except RuntimeError as e:
            return {"success": False, "message": str(e)}
        except Exception as e:
            return {"success": False, "message": f"API error: {str(e)}"}
