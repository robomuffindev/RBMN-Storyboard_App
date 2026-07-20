"""3D-body clay pose captures (v1.175) -- app-side wrapper for clay_driver.py.

Given a character with a MIA-rigged mesh (manifest.vnccs.mesh3d, rigged.fbx on
disk), renders the VNCCS pose set as CLAY captures of the character's REAL
body shape, in the same base64-data-URI list format as
pose_render.render_pose_captures -- a drop-in replacement at the Klein
pose-reference injection point.  Returns None on ANY failure so callers fall
back to the mannequin renderer.  Sync; run in a thread.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import logging
import subprocess
import tempfile
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from backend.services.character_studio.vnccs_native import mia_rig

logger = logging.getLogger(__name__)

_LOCK = threading.Lock()
_CACHE: Dict[str, List[str]] = {}
_CACHE_MAX = 6


def _find_rigged_fbx(character_name: str) -> Optional[Path]:
    """Locate <project>/mesh3d/<char_id>/rigged.fbx for a character NAME.
    meta.json carries character_name from v1.175 on; older rigs (no name)
    match only when they are unambiguous (exactly one unnamed rigged dir)."""
    try:
        from backend.config import settings as _cfg
        root = Path(_cfg.project_dir) / "mesh3d"
        if not root.is_dir():
            return None
        named = []
        unnamed = []
        for d in root.iterdir():
            fbx = d / "rigged.fbx"
            if not fbx.exists():
                continue
            try:
                meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                meta = {}
            nm = str(meta.get("character_name") or "").strip()
            if nm == character_name:
                named.append(fbx)
            elif not nm:
                unnamed.append(fbx)
        if named:
            return named[0]
        if len(unnamed) == 1:
            logger.info("pose_clay[%s]: using the only unnamed rigged mesh %s "
                        "(pre-1.175 rig without character_name)", character_name, unnamed[0])
            return unnamed[0]
        return None
    except Exception:  # noqa: BLE001
        return None


def has_rigged_mesh(character_name: str) -> bool:
    return _find_rigged_fbx(character_name) is not None


def render_pose_clay_captures(character_name: str,
                              pose_data: Dict[str, Any]) -> Optional[List[str]]:
    """Clay captures for every pose in ``pose_data`` (same shape as
    render_pose_captures output: 'data:image/png;base64,...'). Never raises."""
    try:
        fbx = _find_rigged_fbx(character_name)
        if fbx is None:
            logger.info("pose_clay[%s]: no rigged 3D body -- mannequin fallback", character_name)
            return None
        if not mia_rig.env_ready():
            logger.info("pose_clay[%s]: MIA env not set up -- mannequin fallback", character_name)
            return None
        poses = pose_data.get("poses") or []
        if not poses or len(poses) > 16:
            return None
        export = pose_data.get("export", {}) or {}
        width = int(export.get("view_width", export.get("view_size", 832)))
        height = int(export.get("view_height", export.get("view_size", 1216)))
        bg = export.get("bg_color", [40, 40, 40])
        bg = [int(c) for c in bg[:3]] if isinstance(bg, (list, tuple)) else [40, 40, 40]

        key = hashlib.sha256(json.dumps(
            {"f": str(fbx), "m": fbx.stat().st_mtime, "p": poses,
             "w": width, "h": height, "bg": bg}, sort_keys=True, default=str,
        ).encode()).hexdigest()
        hit = _CACHE.get(key)
        if hit:
            return list(hit)

        job = {"fbx": str(fbx),
               "poses": [{"bones": (p or {}).get("bones") or {},
                          "modelRotation": (p or {}).get("modelRotation") or [0, 0, 0]}
                         for p in poses],
               "width": width, "height": height}
        with _LOCK, tempfile.TemporaryDirectory(prefix="rbmn_clay_") as td:
            out_dir = Path(td) / "out"
            job["out_dir"] = str(out_dir)
            job_path = Path(td) / "job.json"
            job_path.write_text(json.dumps(job), encoding="utf-8")
            cmd = [str(mia_rig._venv_python()),
                   str(mia_rig.MIA_LOCAL_DIR / "clay_driver.py"), "--job", str(job_path)]
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=600,
                               cwd=str(mia_rig.MIA_LOCAL_DIR))
            done = "CLAY_DONE" in (r.stdout or "")
            pngs = sorted(out_dir.glob("pose_*.png")) if out_dir.is_dir() else []
            if (r.returncode != 0 and not done) or len(pngs) != len(poses):
                tail = ((r.stderr or "") + "\n" + (r.stdout or "")).strip().splitlines()[-8:]
                logger.warning("pose_clay[%s]: clay render failed (rc=%s, %d/%d pngs): %s",
                               character_name, r.returncode, len(pngs), len(poses),
                               " | ".join(tail))
                return None
            # composite transparent renders onto the capture background color
            from PIL import Image
            captures: List[str] = []
            dbg_dir = fbx.parent / "clay_last"      # v1.175.1: inspectable copies
            try:
                dbg_dir.mkdir(parents=True, exist_ok=True)
                for old in dbg_dir.glob("pose_*.png"):
                    old.unlink()
            except Exception:  # noqa: BLE001
                dbg_dir = None
            for i, p in enumerate(pngs):
                img = Image.open(p).convert("RGBA")
                base = Image.new("RGBA", img.size, tuple(bg) + (255,))
                base.alpha_composite(img)
                buf = io.BytesIO()
                base.convert("RGB").save(buf, format="PNG")
                captures.append("data:image/png;base64,"
                                + base64.b64encode(buf.getvalue()).decode("ascii"))
                if dbg_dir is not None:
                    try:
                        (dbg_dir / f"pose_{i:03d}.png").write_bytes(buf.getvalue())
                    except Exception:  # noqa: BLE001
                        pass
        if len(_CACHE) >= _CACHE_MAX:
            _CACHE.pop(next(iter(_CACHE)))
        _CACHE[key] = list(captures)
        logger.info("pose_clay[%s]: rendered %d clay capture(s) at %dx%d",
                    character_name, len(captures), width, height)
        return captures
    except Exception as e:  # noqa: BLE001 -- best-effort by design
        logger.warning("pose_clay[%s]: failed (%s) -- mannequin fallback", character_name, e)
        return None
