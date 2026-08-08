"""3D-scan -> pose-mannequin body fit (v1.199.74).

The character's OWN Hunyuan3D scan (``<project>/mesh3d/<id>/character.glb``)
is the ground truth for body shape -- it was reconstructed from the approved
mesh-ready turnaround, so it already encodes weight, gut and stockiness with
no text guessing.  This module measures the scan's TORSO DEPTH PROFILE
(front-to-back thickness at hip / navel / chest bands, normalized by standing
height) and solves the parametric mannequin's ``weight`` + ``belly`` so the
mannequin's profile matches.  Depth-only metrics are used because arms hanging
at the sides pollute WIDTH measurements but barely affect DEPTH.

Result: the pose mannequin gets the character's real body automatically --
no description keywords, no sliders, no settings.  Explicit UI sliders
(body_weight / body_belly / ...) still override; text derivation remains the
fallback when no 3D scan exists.

Never raises: every entry point returns {} / None on failure so callers keep
whatever mesh they already had.  Cached per (character, glb mtime) under
``runtime/mesh_fit/``.
"""
from __future__ import annotations

import json
import logging
import os
import struct
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# depth-measurement bands as fractions of standing height (feet=0.0, crown=1.0)
_BANDS = {
    "hip":   (0.46, 0.53),
    "navel": (0.53, 0.62),
    "chest": (0.67, 0.75),
}
# weight/belly search grids (coarse pass, then a local refine)
_W_GRID = (0.30, 0.50, 0.70, 0.85, 1.00)
_B_GRID = (0.0, 0.35, 0.70, 1.05, 1.40)
_REFINE_STEPS = (0.08, 0.18)          # (weight step, belly step) for the refine pass


# --------------------------------------------------------------------------
# Minimal GLB reader (positions only; tolerant; numpy required)
# --------------------------------------------------------------------------
_CT_FMT = {5120: "b", 5121: "B", 5122: "h", 5123: "H", 5125: "I", 5126: "f"}
_TYPE_N = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT4": 16}


def load_glb_positions(path) -> Optional["Any"]:
    """All POSITION vertices from a .glb, node transforms applied. (N,3) f64."""
    try:
        import numpy as np
        raw = Path(path).read_bytes()
        if raw[:4] != b"glTF":
            return None
        n = len(raw)
        off = 12
        gltf = None
        bin_chunk = b""
        while off + 8 <= n:
            clen, ctype = struct.unpack_from("<II", raw, off)
            off += 8
            data = raw[off:off + clen]
            off += clen  # spec: chunkData is already padded to 4-byte alignment
            if ctype == 0x4E4F534A:      # 'JSON'
                gltf = json.loads(data.decode("utf-8"))
            elif ctype == 0x004E4942:    # 'BIN'
                bin_chunk = data
        if not gltf:
            return None
        views = gltf.get("bufferViews", [])
        accs = gltf.get("accessors", [])

        def read_accessor(ai):
            a = accs[ai]
            ncomp = _TYPE_N.get(a.get("type"), 3)
            fmt = _CT_FMT.get(a.get("componentType"))
            if fmt is None:
                return None
            count = int(a.get("count", 0))
            bv = views[a["bufferView"]] if a.get("bufferView") is not None else None
            if bv is None or count <= 0:
                return None
            base = int(bv.get("byteOffset", 0)) + int(a.get("byteOffset", 0))
            stride = int(bv.get("byteStride", 0)) or struct.calcsize("<" + fmt * ncomp)
            itemsize = struct.calcsize("<" + fmt)
            out = np.empty((count, ncomp), dtype=np.float64)
            if stride == itemsize * ncomp:
                arr = np.frombuffer(bin_chunk, dtype="<" + fmt,
                                    count=count * ncomp, offset=base)
                out[:] = arr.reshape(count, ncomp)
            else:
                for i in range(count):
                    o = base + i * stride
                    out[i] = struct.unpack_from("<" + fmt * ncomp, bin_chunk, o)
            return out

        # node world transforms (matrix or TRS), defaulting to identity
        def node_mat(nd):
            m = np.identity(4)
            if "matrix" in nd:
                m = np.asarray(nd["matrix"], dtype=np.float64).reshape(4, 4).T
                return m
            t = nd.get("translation", [0, 0, 0])
            r = nd.get("rotation", [0, 0, 0, 1])   # xyzw quaternion
            s = nd.get("scale", [1, 1, 1])
            x, y, z, w = [float(v) for v in r]
            R = np.array([
                [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])
            M = np.identity(4)
            M[:3, :3] = R * np.asarray(s, dtype=np.float64)[None, :]
            M[:3, 3] = t
            return M

        nodes = gltf.get("nodes", [])
        meshes = gltf.get("meshes", [])
        scenes = gltf.get("scenes", [{}])
        roots = scenes[gltf.get("scene", 0) if gltf.get("scene", 0) < len(scenes) else 0].get(
            "nodes", list(range(len(nodes))))
        chunks: List[Any] = []

        def walk(ni, parent):
            nd = nodes[ni]
            world = parent @ node_mat(nd)
            mi = nd.get("mesh")
            if mi is not None and mi < len(meshes):
                for prim in meshes[mi].get("primitives", []):
                    ai = (prim.get("attributes") or {}).get("POSITION")
                    if ai is None:
                        continue
                    pos = read_accessor(ai)
                    if pos is None or pos.shape[1] < 3:
                        continue
                    p4 = np.hstack([pos[:, :3], np.ones((len(pos), 1))])
                    chunks.append((p4 @ world.T)[:, :3])
            for ch in nd.get("children", []):
                walk(ch, world)

        for r0 in roots:
            if r0 < len(nodes):
                walk(r0, np.identity(4))
        if not chunks:
            # no scene graph? read every POSITION accessor raw
            for mesh in meshes:
                for prim in mesh.get("primitives", []):
                    ai = (prim.get("attributes") or {}).get("POSITION")
                    if ai is not None:
                        pos = read_accessor(ai)
                        if pos is not None:
                            chunks.append(pos[:, :3])
        if not chunks:
            return None
        return np.vstack(chunks)
    except Exception as exc:  # noqa: BLE001
        logger.info("mesh_fit: glb read failed (%s)", exc)
        return None


# --------------------------------------------------------------------------
# Orientation + depth profile
# --------------------------------------------------------------------------
def _orient(verts):
    """Return (up_idx, lat_idx, dep_idx, up_sign) for a standing figure."""
    import numpy as np
    ext = verts.max(0) - verts.min(0)
    up = int(np.argmax(ext))
    others = [i for i in range(3) if i != up]
    # widest cross-band position tells head-vs-feet: shoulders/arms sit above mid
    u = verts[:, up]
    lo, hi = float(u.min()), float(u.max())
    H = max(hi - lo, 1e-6)
    nbins = 24
    widths = []
    for i in range(nbins):
        a = lo + H * i / nbins
        b = lo + H * (i + 1) / nbins
        m = (u >= a) & (u < b)
        if m.sum() < 8:
            widths.append(0.0)
            continue
        w1 = verts[m, others[0]].max() - verts[m, others[0]].min()
        w2 = verts[m, others[1]].max() - verts[m, others[1]].min()
        widths.append(max(float(w1), float(w2)))
    peak_frac = (int(np.argmax(widths)) + 0.5) / nbins
    up_sign = 1.0 if peak_frac >= 0.5 else -1.0
    # lateral axis = the wider of the two at the widest band (shoulder region)
    i = int(np.argmax(widths))
    a = lo + H * i / nbins
    b = lo + H * (i + 1) / nbins
    m = (u >= a) & (u < b)
    w1 = verts[m, others[0]].max() - verts[m, others[0]].min()
    w2 = verts[m, others[1]].max() - verts[m, others[1]].min()
    lat, dep = (others[0], others[1]) if w1 >= w2 else (others[1], others[0])
    return up, lat, dep, up_sign


def depth_profile(verts, robust: float = 0.995) -> Optional[Dict[str, float]]:
    """{band: front-to-back depth / standing height} for a standing mesh.
    ``robust``: extents use symmetric quantiles to shrug off stray verts."""
    try:
        import numpy as np
        if verts is None or len(verts) < 100:
            return None
        up, lat, dep, sgn = _orient(verts)
        u = verts[:, up] * sgn
        lo, hi = float(np.quantile(u, 1 - robust)), float(np.quantile(u, robust))
        H = max(hi - lo, 1e-6)
        out: Dict[str, float] = {}
        for name, (fa, fb) in _BANDS.items():
            m = (u >= lo + fa * H) & (u <= lo + fb * H)
            if m.sum() < 12:
                return None
            d = verts[m, dep]
            depth = float(np.quantile(d, robust) - np.quantile(d, 1 - robust))
            out[name] = depth / H
        return out
    except Exception as exc:  # noqa: BLE001
        logger.info("mesh_fit: depth profile failed (%s)", exc)
        return None


# v1.199.132: THE MISSING HALF OF THE FIT.  `depth_profile` measures front-to-back
# depth only (`d = verts[m, dep]`), so every fit this module has ever made optimised
# the ONE dimension the control image cannot show -- Klein sees a FRONT view, i.e. a
# WIDTH silhouette -- and left width completely unconstrained.  That is why the
# rendered body came out 63-72% of the reference's width while weight and belly sat
# pinned at their ceilings: the solver was busy matching depth.
# Band choice: 0.24-0.36 of standing height is thighs/upper legs -- arm-free in a
# T-pose AND in an arms-down rest pose, so the scan and the mannequin are comparable
# without having to pose the mannequin first.  Same band body_match calls "clean".
_WIDTH_BAND = (0.24, 0.36)


def width_profile(verts, robust: float = 0.99) -> Optional[Dict[str, float]]:
    """{"thigh": lateral width / standing height} for a standing mesh."""
    try:
        import numpy as np
        if verts is None or len(verts) < 100:
            return None
        up, lat, dep, sgn = _orient(verts)
        u = verts[:, up] * sgn
        lo, hi = float(np.quantile(u, 1 - robust)), float(np.quantile(u, robust))
        H = max(hi - lo, 1e-6)
        fa, fb = _WIDTH_BAND
        m = (u >= lo + fa * H) & (u <= lo + fb * H)
        if m.sum() < 12:
            return None
        x = verts[m, lat]
        w = float(np.quantile(x, robust) - np.quantile(x, 1 - robust))
        return {"thigh": w / H}
    except Exception as exc:  # noqa: BLE001
        logger.info("mesh_fit: width profile failed (%s)", exc)
        return None


# --------------------------------------------------------------------------
# Mannequin profile + fit
# --------------------------------------------------------------------------
def _mannequin_profile(mesh: Dict[str, Any],
                       want_width: bool = False) -> Optional[Dict[str, float]]:
    """Depth profile of OUR parametric mannequin for a given mesh dict
    (weight/belly/gender/... -- same solver + belly displacement as the
    renderer, body verts only so helper geometry doesn't skew extents)."""
    try:
        from . import pose_render as pr
        if not pr._ensure_loaded():
            return None
        bv = pr._solve_base_verts(mesh)
        try:
            b = float(mesh.get("belly", 0) or 0)
        except Exception:  # noqa: BLE001
            b = 0.0
        if b > 0:
            bv = pr._apply_belly(bv, b)
        # v1.199.132: the fit must see the SAME vert pipeline the renderer runs, or
        # it would score every body_width candidate identically and always return 1.0.
        try:
            _bw = float(mesh.get("body_width", 1.0) or 1.0)
        except Exception:  # noqa: BLE001
            _bw = 1.0
        if abs(_bw - 1.0) > 1e-6:
            bv = pr._apply_width(bv, _bw)
        body = bv[pr._body_vert_indices()]
        if want_width:
            return width_profile(body)
        return depth_profile(body)
    except Exception as exc:  # noqa: BLE001
        logger.info("mesh_fit: mannequin profile failed (%s)", exc)
        return None


def _err(a: Dict[str, float], b: Dict[str, float]) -> float:
    # navel weighted highest: the gut is what we are actually fitting
    wts = {"hip": 1.0, "navel": 1.6, "chest": 1.0}
    return sum(wts[k] * (a[k] - b[k]) ** 2 for k in _BANDS if k in a and k in b)


def fit_weight_belly(scan_profile: Dict[str, float],
                     base_mesh: Dict[str, Any],
                     scan_width: Optional[Dict[str, float]] = None) -> Optional[Dict[str, float]]:
    """Solve mannequin weight+belly so its depth profile matches the scan's.
    Two-stage grid search (coarse 5x5, then a 3x3 local refine)."""
    if not scan_profile:
        return None
    base = {k: v for k, v in (base_mesh or {}).items() if k not in ("weight", "belly")}
    best = None
    for w in _W_GRID:
        for b in _B_GRID:
            prof = _mannequin_profile({**base, "weight": w, "belly": b})
            if not prof:
                return None
            e = _err(scan_profile, prof)
            if best is None or e < best[0]:
                best = (e, w, b)
    if best is None:
        return None
    _, w0, b0 = best
    dw, db = _REFINE_STEPS
    for w in (w0 - dw, w0, w0 + dw):
        for b in (b0 - db, b0, b0 + db):
            wc = max(0.0, min(1.0, w))
            bc = max(0.0, min(1.5, b))
            prof = _mannequin_profile({**base, "weight": wc, "belly": bc})
            if not prof:
                continue
            e = _err(scan_profile, prof)
            if e < best[0]:
                best = (e, wc, bc)
    err, w, b = best
    out = {"weight": round(w, 3), "belly": round(b, 3)}
    # v1.199.132: SOLVE THE WIDTH AXIS TOO.  weight and belly were both pinning at
    # their ceilings (1.00 / 1.50) and STILL under-shooting, because neither is a
    # width lever: measured on the device, driving weight 0.5 -> 1.0 moves the front
    # silhouette only 0.149 -> 0.161 (chest) and 0.172 -> 0.180 (hips), while belly
    # displaces along the ANTERIOR axis and shows up frontally as one bulge band.
    # Against Duke's scan (0.256 / 0.290 / 0.268 at y = 0.34 / 0.50 / 0.66) the
    # maxed mannequin sat at 0.161 / 0.198 / 0.180 -- 63-68%.  `body_width` (see
    # pose_render._apply_width) is the missing axis; solve it here so nothing is
    # per-character dialled.  1-D search AFTER weight/belly, coarse then refine.
    if scan_width and scan_width.get("thigh"):
        tgt = float(scan_width["thigh"])
        cand = None
        for bw in (1.0, 1.15, 1.30, 1.45, 1.60, 1.75, 1.90, 2.05):
            wp = _mannequin_profile({**base, "weight": w, "belly": b, "body_width": bw},
                                    want_width=True)
            if not wp:
                continue
            e = abs(wp["thigh"] - tgt)
            if cand is None or e < cand[0]:
                cand = (e, bw, wp["thigh"])
        if cand is not None:
            for bw in (cand[1] - 0.07, cand[1] + 0.07):
                bwc = max(1.0, min(2.2, bw))
                wp = _mannequin_profile({**base, "weight": w, "belly": b,
                                         "body_width": bwc}, want_width=True)
                if wp and abs(wp["thigh"] - tgt) < cand[0]:
                    cand = (abs(wp["thigh"] - tgt), bwc, wp["thigh"])
            if abs(cand[1] - 1.0) > 1e-6:
                out["body_width"] = round(cand[1], 3)
            base_w = _mannequin_profile({**base, "weight": w, "belly": b},
                                        want_width=True)
            logger.info("mesh_fit: WIDTH axis -- scan thigh %.3f, mannequin %.3f -> "
                        "%.3f at body_width=%.2f", tgt,
                        (base_w or {}).get("thigh", float("nan")), cand[2], cand[1])
    # a genuinely fat fit implies soft tissue -- cap text-derived "athletic"
    # muscle so 'broad shoulders' on a heavy character can't rebuild him lean
    if w >= 0.75:
        try:
            mu = float(base_mesh.get("muscle", 0.5))
        except Exception:  # noqa: BLE001
            mu = 0.5
        out["muscle"] = round(min(mu, 0.6), 3)
    logger.info("mesh_fit: fitted weight=%.2f belly=%.2f (err=%.5f)", w, b, err)
    return out


# --------------------------------------------------------------------------
# App-side entry: character name -> fitted params (cached)
# --------------------------------------------------------------------------
def _safe(name: str) -> str:
    return "".join(c for c in str(name or "") if c.isalnum())[:32] or "char"


def _cache_dir() -> Path:
    env = os.environ.get("RBMN_MESHFIT_CACHE")
    if env:
        return Path(env)
    repo = Path(__file__).resolve().parents[4]
    return repo / "runtime" / "mesh_fit"


def find_character_glb(character_name: str) -> Optional[Path]:
    """<project>/mesh3d/<id>/character.glb for a character, same resolution
    rules as pose_clay._find_rigged_fbx (named match, else single unnamed)."""
    try:
        from backend.config import settings as _cfg
        root = Path(_cfg.project_dir) / "mesh3d"
        if not root.is_dir():
            return None
        named, unnamed = [], []
        for d in root.iterdir():
            glb = d / "character.glb"
            if not glb.exists():
                continue
            try:
                meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                meta = {}
            nm = str(meta.get("character_name") or "").strip()
            if nm == character_name:
                named.append(glb)
            elif not nm:
                unnamed.append(glb)
        if named:
            return named[0]
        if len(unnamed) == 1:
            return unnamed[0]
        return None
    except Exception:  # noqa: BLE001
        return None


def get_meshfit_params(character_name: str, mesh: Dict[str, Any]) -> Dict[str, float]:
    """MAIN ENTRY: fitted {weight, belly[, muscle]} from the character's own 3D
    scan, or {} when no scan / any failure. Cached per (character, glb mtime)."""
    glb = find_character_glb(character_name)
    if glb is None:
        return {}
    try:
        mt = int(glb.stat().st_mtime)
    except Exception:  # noqa: BLE001
        return {}
    # _v2: refit required -- v1.199.74 fits ran against the tall-leaked
    # (height 0.85) mannequin profile and overfit the belly.
    # _v3: refit required -- v1.199.132 added the body_width axis, so every _v2
    # cache holds a fit made with weight/belly pinned at their ceilings.
    cpath = _cache_dir() / ("%s_%d_v3.json" % (_safe(character_name), mt))
    if cpath.exists():
        try:
            cached = json.loads(cpath.read_text(encoding="utf-8"))
            if isinstance(cached, dict):
                return cached
        except Exception:  # noqa: BLE001
            pass
    verts = load_glb_positions(glb)
    prof = depth_profile(verts) if verts is not None else None
    if not prof:
        logger.info("mesh_fit[%s]: no usable depth profile from %s", character_name, glb)
        return {}
    fitted = fit_weight_belly(prof, mesh or {}, width_profile(verts))
    if not fitted:
        return {}
    logger.info("mesh_fit[%s]: scan profile %s -> %s", character_name,
                {k: round(v, 3) for k, v in prof.items()}, fitted)
    try:
        cpath.parent.mkdir(parents=True, exist_ok=True)
        cpath.write_text(json.dumps(fitted), encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    return fitted
