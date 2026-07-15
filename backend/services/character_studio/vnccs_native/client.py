"""HTTP client / relay for the VNCCS ``/vnccs/*`` routes on a ComfyUI worker.

The VNCCS + vnccs-utils custom nodes register ~80 aiohttp routes on the ComfyUI
server.  This client is deliberately thin: a *generic relay* (``relay``) that
forwards a request to any whitelisted ``/vnccs/*`` path and returns the raw
status / content-type / body — so our frontend (modelled on the VNCCS web UI)
can drive the real endpoints without us re-declaring every parameter — plus a
handful of *typed helpers* for the calls our backend makes itself during
ingest, cataloging, and the settings screen.

Sync (``requests``); call from FastAPI via ``await asyncio.to_thread(...)``.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urljoin

import requests

logger = logging.getLogger(__name__)


class VNCCSError(Exception):
    """Raised when a VNCCS route call fails."""


# Every ``/vnccs/*`` subpath (after the ``/vnccs/`` prefix) we permit the relay
# to forward.  Trailing ``/`` entries are treated as prefixes (e.g. dynamic
# ``{filename}`` / ``{id}`` segments).  Derived from the routes the nodes
# register (grep of vnccs/ + vnccs-utils/).
VNCCS_ROUTE_WHITELIST: Tuple[str, ...] = (
    # --- context / config / control center (settings screen) ---
    "context_lists", "config", "module_status",
    "control_center/check", "control_center/lora_files", "control_center/nunchaku_fix_status",
    "control_center/clothes_preview", "control_center/custom_lora", "control_center/custom_lora/delete",
    "control_center/dependencies", "control_center/download", "control_center/nunchaku_apply_fix",
    "character_generator/gan_upscale_models", "character_generator/seedvr_attention",
    "character_generator/regenerate",
    # --- character store / CRUD ---
    "create", "delete", "list_characters", "character_info", "get_tags",
    "get_cached_preview", "preview_generate",
    "get_character_pose_preview", "get_character_pose_preview_meta",
    "get_character_sheet_preview", "character_studio/update_preview",
    "character_studio/morph_data.bin",
    # --- costumes ---
    "create_costume", "save_costume", "get_costume", "list_costumes",
    "get_character_costumes", "get_costumes_by_emotion",
    # --- emotions ---
    "get_emotions", "add_custom_emotion", "get_character_emotions",
    "get_emotion_image", "get_sheet_preview", "get_preview",
    # --- wizards (LLM already on the box) ---
    "character_wizard", "cloner_auto_generate", "clothes_wizard",
    "cloner_download_model", "cloner_download_status",
    "qwen_vl_download_model", "qwen_vl_download_status",
    # --- pose library / poses / captures (vnccs-utils) ---
    "pose_presets", "pose_preset/", "pose_captures/", "pose_captures_upload",
    "pose_library/",  # covers list/get/save/delete/preview/repositories/*
    "pose_sync/upload_capture",
    # --- sam3d pose import / mesh overlay ---
    "sam3d/process_image_to_pose_json", "sam3d/render_mesh_overlay", "sam3d/import_status/",
    # --- unicanvas (draw/segment tool) ---
    "unicanvas/assets", "unicanvas/checkpoints", "unicanvas/presets",
    "unicanvas/presets/status", "unicanvas/presets/download", "unicanvas/progress/",
    "unicanvas/draw", "unicanvas/segment", "unicanvas_state/", "unicanvas_state_upload",
    # --- model / repo manager ---
    "manager/check", "manager/status", "manager/download", "manager/save_token",
    "manager/set_active", "utils/manager/status", "models/",
    # --- migration assistant ---
    "migration/characters", "migration/status/", "migration/start",
    "migration/repair-sprites", "migrate",
)


def _path_allowed(subpath: str) -> bool:
    """True if ``subpath`` (no leading slash, no query) is whitelisted."""
    sp = subpath.lstrip("/").split("?", 1)[0]
    # reject path traversal — otherwise "models/../../prompt" escapes /vnccs/
    if ".." in sp.split("/"):
        return False
    for entry in VNCCS_ROUTE_WHITELIST:
        if entry.endswith("/"):
            if sp == entry.rstrip("/") or sp.startswith(entry):
                return True
        elif sp == entry:
            return True
    return False


class VNCCSClient:
    """Thin client for one VNCCS host worker.

    Args:
        base_url: e.g. ``http://localhost:8188`` (the ComfyUI host running VNCCS).
        timeout: per-request timeout (seconds).  Generation-driving POSTs
            (``preview_generate``, ``character_generator/regenerate``) can be slow;
            callers may pass a larger timeout per call.
    """

    def __init__(self, base_url: str, timeout: int = 30):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()
        if "runpod.net" in self.base_url or self.base_url.startswith("https://"):
            self.session.verify = False
            try:
                import urllib3
                urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
            except Exception:
                pass

    # -- generic relay ----------------------------------------------------
    def relay(
        self,
        method: str,
        subpath: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        json_body: Optional[Any] = None,
        data: Optional[bytes] = None,
        content_type: Optional[str] = None,
        timeout: Optional[int] = None,
    ) -> Tuple[int, str, bytes]:
        """Forward a request to ``/vnccs/<subpath>`` and return (status, ctype, body).

        Raises ``VNCCSError`` if the subpath isn't whitelisted or the host is
        unreachable.  HTTP error *statuses* are returned as-is (the proxy relays
        them to the caller) rather than raised.
        """
        sub = subpath.lstrip("/")
        if not _path_allowed(sub):
            raise VNCCSError(f"VNCCS route not allowed: {sub}")
        url = urljoin(self.base_url + "/", "vnccs/" + sub)
        headers = {}
        if content_type:
            headers["Content-Type"] = content_type
        try:
            resp = self.session.request(
                method.upper(), url,
                params=params, json=json_body, data=data,
                headers=headers or None,
                timeout=timeout or self.timeout,
            )
        except requests.RequestException as e:
            raise VNCCSError(f"VNCCS host unreachable ({self.base_url}): {e}")
        ctype = resp.headers.get("Content-Type", "application/octet-stream")
        return resp.status_code, ctype, resp.content

    # -- JSON helpers -----------------------------------------------------
    def get_json(self, subpath: str, params: Optional[Dict[str, Any]] = None,
                 timeout: Optional[int] = None) -> Any:
        status, ctype, body = self.relay("GET", subpath, params=params, timeout=timeout)
        if status >= 400:
            raise VNCCSError(f"GET /vnccs/{subpath} -> {status}: {body[:500]!r}")
        return _decode_json(body)

    def post_json(self, subpath: str, payload: Optional[Any] = None,
                  timeout: Optional[int] = None) -> Any:
        status, ctype, body = self.relay("POST", subpath, json_body=payload or {}, timeout=timeout)
        if status >= 400:
            raise VNCCSError(f"POST /vnccs/{subpath} -> {status}: {body[:500]!r}")
        return _decode_json(body)

    def get_bytes(self, subpath: str, params: Optional[Dict[str, Any]] = None,
                  timeout: Optional[int] = None) -> Tuple[str, bytes]:
        """Return (content_type, bytes) for a binary route (previews, thumbnails)."""
        status, ctype, body = self.relay("GET", subpath, params=params, timeout=timeout)
        if status >= 400:
            raise VNCCSError(f"GET /vnccs/{subpath} -> {status}")
        return ctype, body

    # -- typed helpers (calls our backend makes itself) -------------------
    def context_lists(self) -> Dict[str, Any]:
        """Checkpoints/diffusion_models/text_encoders/vae/samplers/schedulers/
        characters/loras — powers the settings screen."""
        return self.get_json("context_lists")

    def control_center_lora_files(self) -> Any:
        return self.get_json("control_center/lora_files")

    def list_characters(self) -> Any:
        return self.get_json("list_characters")

    def character_info(self, name: str) -> Any:
        return self.get_json("character_info", params={"name": name})

    def get_tags(self) -> Any:
        return self.get_json("get_tags")

    def get_emotions(self) -> Any:
        return self.get_json("get_emotions")

    def get_character_costumes(self, name: str) -> Any:
        return self.get_json("get_character_costumes", params={"character": name})

    def get_character_emotions(self, name: str) -> Any:
        return self.get_json("get_character_emotions", params={"character": name})

    def pose_library_list(self, full: bool = False) -> Any:
        return self.get_json("pose_library/list", params={"full": "true"} if full else None)

    # -- core ComfyUI routes (NOT /vnccs/*, so they bypass the relay whitelist) --
    def get_object_info(self, timeout: Optional[int] = None) -> Dict[str, Any]:
        """The worker's node definitions — needed to convert VNCCS UI graphs to API."""
        url = urljoin(self.base_url + "/", "object_info")
        try:
            resp = self.session.get(url, timeout=timeout or self.timeout)
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as e:
            raise VNCCSError(f"/object_info failed ({self.base_url}): {e}")

    def submit_prompt(self, graph: Dict[str, Any], client_id: Optional[str] = None,
                      timeout: Optional[int] = None) -> Dict[str, Any]:
        """Queue an API-format prompt on the VNCCS host. Returns {prompt_id, number, ...}."""
        url = urljoin(self.base_url + "/", "prompt")
        payload: Dict[str, Any] = {"prompt": graph}
        if client_id:
            payload["client_id"] = client_id
        try:
            resp = self.session.post(url, json=payload, timeout=timeout or self.timeout)
        except requests.RequestException as e:
            raise VNCCSError(f"/prompt submit failed ({self.base_url}): {e}")
        if resp.status_code >= 400:
            raise VNCCSError(f"/prompt -> {resp.status_code}: {resp.text[:1500]}")
        data = resp.json()
        if "prompt_id" not in data:
            raise VNCCSError(f"/prompt bad response: {data}")
        if data.get("node_errors"):
            raise VNCCSError(f"/prompt node_errors: {json.dumps(data['node_errors'])[:1500]}")
        return data

    def get_history(self, prompt_id: str, timeout: Optional[int] = None) -> Dict[str, Any]:
        """History entry for a prompt (contains per-node 'outputs' -> images once done)."""
        url = urljoin(self.base_url + "/", f"history/{prompt_id}")
        try:
            resp = self.session.get(url, timeout=timeout or self.timeout)
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as e:
            raise VNCCSError(f"/history/{prompt_id} failed: {e}")

    def upload_image(self, filename: str, data: bytes, subfolder: str = "",
                     overwrite: bool = True, timeout: Optional[int] = None) -> Dict[str, Any]:
        """Upload an image to the host's ComfyUI input folder (/upload/image).

        Returns ComfyUI's {name, subfolder, type} — the ref to put in a node's
        image widget (e.g. CharacterCloner.source_images)."""
        url = urljoin(self.base_url + "/", "upload/image")
        files = {"image": (filename, data, "application/octet-stream")}
        form = {"type": "input", "overwrite": "true" if overwrite else "false"}
        if subfolder:
            form["subfolder"] = subfolder
        try:
            resp = self.session.post(url, files=files, data=form, timeout=timeout or self.timeout)
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as e:
            raise VNCCSError(f"/upload/image {filename} failed ({self.base_url}): {e}")

    def view_image(self, filename: str, subfolder: str = "", ftype: str = "output",
                   timeout: Optional[int] = None) -> bytes:
        """Download a generated image via /view."""
        url = urljoin(self.base_url + "/", "view")
        try:
            resp = self.session.get(url, params={"filename": filename, "subfolder": subfolder,
                                                 "type": ftype}, timeout=timeout or self.timeout)
            resp.raise_for_status()
            return resp.content
        except requests.RequestException as e:
            raise VNCCSError(f"/view {filename} failed: {e}")


def _decode_json(body: bytes) -> Any:
    import json
    if not body:
        return {}
    try:
        return json.loads(body.decode("utf-8"))
    except Exception as e:
        raise VNCCSError(f"non-JSON VNCCS response: {e}: {body[:300]!r}")
