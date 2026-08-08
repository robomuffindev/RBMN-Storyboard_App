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


def _renorm_depth(img):
    """Re-normalise a 16-bit Blender Z render over its VISIBLE pixels.

    clay_driver maps the Z pass across the pose's full bbox depth, which is safe
    (nothing can clip) but conservative: you only ever SEE the front slice of a
    body, so the figure lands in the top fraction of the range and the reference
    reads as near-uniform white -- exactly what the first production depth refs
    looked like. Re-stretching over the 1st/99th percentile of the non-background
    pixels gives the same encoding pose_render's mannequin depth already uses, so
    both renderers hand the LoRA an identically-scaled signal. Falls back to a
    plain 8-bit convert if anything is unexpected.
    """
    from PIL import Image
    try:
        import numpy as np
        # Pillow's I;16 handling is version-dependent; normalise to a mode numpy
        # reads reliably before touching it.
        if str(img.mode).startswith("I;16"):
            img = img.convert("I")
        a = np.asarray(img)
        if a.ndim == 3:
            a = a[..., 0]
        a = a.astype("float64")
        mask = a > 0
        if mask.sum() < 64:
            return img.convert("RGB")
        # v1.199.93: measure the range on the INTERIOR only. Every silhouette
        # pixel is antialiased against the black background, so the boundary is a
        # ring of spurious "very far" samples -- with a complex silhouette that
        # ring alone pinned the 1st percentile at ~1 and the real body still ended
        # up squeezed into the top quarter of the range (measured: 65% of body
        # pixels above 200, median 212, on a map whose nominal spread was 254).
        # Eroding 2px before taking percentiles measures actual geometry.
        inner = mask.copy()
        for _ in range(2):
            e = inner.copy()
            for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1),
                           (1, 1), (1, -1), (-1, 1), (-1, -1)):
                e &= np.roll(inner, (dy, dx), axis=(0, 1))
            inner = e
        body = a[inner] if inner.sum() >= 64 else a[mask]
        lo = float(np.percentile(body, 1.0))
        hi = float(np.percentile(body, 99.0))
        out = np.zeros(a.shape, dtype="uint8")
        if hi - lo < 1e-9:
            out[mask] = 255
        else:
            rel = np.clip((a - lo) / (hi - lo), 0.0, 1.0)
            out = np.where(mask, np.clip(1.0 + rel * 254.0, 1, 255), 0).astype("uint8")
        return Image.fromarray(np.repeat(out[:, :, None], 3, axis=2), "RGB")
    except Exception as e:  # noqa: BLE001
        logger.info("pose_clay: depth re-normalise skipped (%s)", e)
        return img.convert("RGB")


def render_pose_clay_captures(character_name: str,
                              pose_data: Dict[str, Any],
                              keep_smear: bool = False) -> Optional[List[str]]:
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
        # v1.199.83: "depth" renders a true DEPTH MAP of the rigged mesh instead
        # of the clay shading -- pose + volume + height in the one channel the
        # RefControl depth LoRA obeys spatially.  Single source of truth is
        # export["render_mode"], same key pose_render reads.
        rmode = str(export.get("render_mode") or "shaded").strip().lower()

        key = hashlib.sha256(json.dumps(
            {"f": str(fbx), "m": fbx.stat().st_mtime, "p": poses,
             "w": width, "h": height, "bg": bg, "ks": keep_smear, "rm": rmode,
             "pv": bool(export.get("clay_preserve_volume")),
             "ss": export.get("clay_smear_stretch"),
             "fo": export.get("clay_fix_orphans", True) is not False,
             "cs": export.get("clay_corrective_smooth", 1.0),
             "ci": export.get("clay_corrective_iters", 20),
             "wr": export.get("clay_weld_rel", 0.0005),
             "rs": export.get("clay_reskin", "blender"),
             "ab": export.get("clay_arm_abduct", 0.0),
             "aa": export.get("clay_auto_abduct", True) is not False},
            sort_keys=True, default=str,
        ).encode()).hexdigest()
        hit = _CACHE.get(key)
        if hit:
            return list(hit)

        job = {"fbx": str(fbx),
               "poses": [{"bones": (p or {}).get("bones") or {},
                          "modelRotation": (p or {}).get("modelRotation") or [0, 0, 0]}
                         for p in poses],
               "width": width, "height": height,
               "render_mode": rmode,
               # v1.199.86: DQ skinning explodes unweighted verts -- off unless asked
               "preserve_volume": bool(export.get("clay_preserve_volume")),
               # ...and the unweighted verts themselves get repaired (see clay_driver)
               "fix_orphan_weights": export.get("clay_fix_orphans", True) is not False,
               # v1.199.95: relaxes armpit shredding from noisy auto-rig weights
               "corrective_smooth": float(export.get("clay_corrective_smooth", 1.0) or 0.0),
               "corrective_smooth_iters": int(export.get("clay_corrective_iters", 20) or 0),
               # v1.199.97: weld the scan's duplicate verts -- measured cause of the tearing
               # v1.199.99: scale-relative weld + Blender heat weights by default
               # (measured 17x lower peak stretch than MIA's weights).
               "weld_rel": float(export.get("clay_weld_rel", 0.0005) or 0.0),
               # v1.199.113: DEFAULT ON. With the parent-transform bug fixed
               # (v112) the re-skin measures better than MIA's weights on BOTH
               # axes across all 12 library poses -- penetration lower on 9/12,
               # peak stretch lower on 12/12 -- and the contact sheet shows arms
               # visible where they were previously buried. clay_reskin="off"
               # reverts to MIA's binding.
               "reskin": str(export.get("clay_reskin", "blender") or ""),
               # v1.199.100: extra shoulder abduction so library poses authored for
               # an average build do not bury the arms inside a wide torso
               "arm_abduct_deg": float(export.get("clay_arm_abduct", 0.0) or 0.0),
               # v1.199.101: auto-derived per character from its own torso width
               "auto_abduct": export.get("clay_auto_abduct", True) is not False}
        if rmode in ("depth", "normal"):
            # v1.199.93: REVERTED to keep-all. The v1.199.85 "middle ground" of 4.0
            # was reasoning, not measurement -- and it was wrong. Cutting faces
            # punches HOLES, and a hole in a depth map is a void showing the far
            # interior wall of the mesh: Klein reads that as "nothing is here" and
            # contorts the arm around it. holes_fill does not reliably close the
            # large, ragged boundaries the armpit cut leaves. Measured: every clean
            # depth render in depth_test used 1e6 (keep all); every production ref
            # with a black chest void used 4.0. Ragged fringe is cosmetic; a void
            # is structural. Overridable via export["clay_smear_stretch"].
            job["smear_stretch"] = float(export.get("clay_smear_stretch") or 1e6)
        elif keep_smear:
            # v1.199.82: this capture feeds DWPose (skeleton input) -- keypoint
            # detection needs a COMPLETE body far more than a clean one. The
            # smear-face removal + keep-largest-island cleanup can AMPUTATE the
            # head/arms at hard poses (observed: headless armless torso blob ->
            # garbage skeleton), so disable it by raising the stretch threshold.
            job["smear_stretch"] = 1e6
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
                img = Image.open(p)
                if rmode == "depth":
                    # Depth is rendered opaque with a BLACK (= infinitely far)
                    # background; compositing it onto the capture bg colour would
                    # destroy exactly that meaning.
                    base = _renorm_depth(img)
                elif rmode == "normal":
                    # v1.199.115: composite onto the flat facing-camera normal
                    # colour (128,128,255) -- what a DSINE map shows for a flat
                    # backdrop wall -- so the background reads as "wall behind
                    # the figure", not a void. No re-normalisation: the matcap
                    # encoding is already the final signal.
                    img = img.convert("RGBA")
                    base = Image.new("RGBA", img.size, (128, 128, 255, 255))
                    base.alpha_composite(img)
                else:
                    img = img.convert("RGBA")
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
        logger.info("pose_clay[%s]: rendered %d %s capture(s) at %dx%d",
                    character_name, len(captures), rmode, width, height)
        return captures
    except Exception as e:  # noqa: BLE001 -- best-effort by design
        logger.warning("pose_clay[%s]: failed (%s) -- mannequin fallback", character_name, e)
        return None
