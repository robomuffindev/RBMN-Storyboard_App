"""VNCCS Native mode — a thin app over the VNCCS ComfyUI nodes.

Rather than re-implementing the VNCCS character pipeline, this package talks
directly to the ``/vnccs/*`` HTTP routes that the VNCCS + vnccs-utils custom
nodes register on a ComfyUI worker (character/costume/emotion CRUD, the pose
library, the LLM wizards, previews and context lists), and submits the VNCCS
meganode graphs for the actual generation.  Our app is the system of record:
we index + project-link everything in the Studio DB and pull generated sprites
into our asset store.

See ``client.py`` for the relay/client and ``host.py`` for host resolution.
"""

from .client import VNCCSClient, VNCCSError, VNCCS_ROUTE_WHITELIST
from .host import resolve_vnccs_host, vnccs_host_online

__all__ = [
    "VNCCSClient",
    "VNCCSError",
    "VNCCS_ROUTE_WHITELIST",
    "resolve_vnccs_host",
    "vnccs_host_online",
]
