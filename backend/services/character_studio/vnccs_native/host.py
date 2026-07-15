"""Resolve which ComfyUI worker hosts the VNCCS character store.

VNCCS stores characters on the local disk of whatever ComfyUI instance runs the
nodes, so all Studio-VNCCS work must pin to ONE host (otherwise characters
scatter across the pool).  Resolution order:

1. An explicit URL configured in AppSettings (``studio_vnccs_host``).
2. The first healthy, ``vnccs``-capable worker in the pool (non-RunPod).

The dispatcher (WorkerPool) lives on ``request.app.state.comfy_dispatcher`` and
exposes ``select_worker(required_caps, required_models, exclude_runpod=...)``
(returns a ``ComfyWorker`` with ``.url``) and ``has_capability(cap)``.
"""

from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)

VNCCS_CAP = "vnccs"


def resolve_vnccs_host(comfy_dispatcher, configured_url: Optional[str] = None) -> Optional[str]:
    """Return the base URL of the VNCCS host worker, or None if unavailable.

    A configured URL always wins (even if the pool can't see it — the user may
    have pinned an external box).  Otherwise pick a vnccs-capable worker.
    """
    if configured_url:
        return configured_url.rstrip("/")
    if comfy_dispatcher is None:
        return None
    try:
        worker = comfy_dispatcher.select_worker({VNCCS_CAP}, set(), exclude_runpod=True)
    except Exception as e:  # select_worker raises ValueError when none match
        logger.debug("resolve_vnccs_host: no vnccs worker (%s)", e)
        return None
    if worker is None:
        return None
    return worker.url.rstrip("/")


def vnccs_host_online(comfy_dispatcher, configured_url: Optional[str] = None) -> bool:
    """Cheap check used by the availability badge — is a VNCCS host reachable?

    When a URL is pinned we trust it (the relay will surface real errors); with
    no pin we require an actually-detected vnccs capability in the pool.
    """
    if configured_url:
        return True
    if comfy_dispatcher is None:
        return False
    try:
        return bool(comfy_dispatcher.has_capability(VNCCS_CAP))
    except Exception:
        return False
def list_vnccs_hosts(comfy_dispatcher, configured_url: Optional[str] = None) -> list:
    """All reachable VNCCS-capable worker URLs, pinned host first (deduped).

    Used by the parallel fan-out: repetitive per-pose / per-costume work can be
    chunked across every vnccs worker; the pinned host stays first so single-host
    behaviour is unchanged when the pool has one worker.
    """
    hosts: list = []
    if configured_url:
        hosts.append(configured_url.rstrip("/"))
    if comfy_dispatcher is not None:
        try:
            for w in comfy_dispatcher.workers.values():
                if not getattr(w, "healthy", False) or getattr(w, "is_runpod", False):
                    continue
                if VNCCS_CAP not in getattr(w, "capabilities", set()):
                    continue
                u = w.url.rstrip("/")
                if u not in hosts:
                    hosts.append(u)
        except Exception as e:  # noqa: BLE001
            logger.debug("list_vnccs_hosts: pool scan failed (%s)", e)
    return hosts
