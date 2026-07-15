"""VNCCS Native mode API — proxy to a VNCCS host worker's ``/vnccs/*`` routes.

This is the reuse layer for the VNCCS Native Character Studio mode.  Rather than
re-implementing VNCCS, the frontend (modelled on the VNCCS meganode/pose-studio
web UI) calls these endpoints and we relay to the pinned VNCCS host worker,
which already runs the character/costume/emotion store, the LLM wizards, the HF
pose library, previews and context lists.  Generation still goes through the
normal dispatcher (submitting the VNCCS meganode graphs); this router covers the
interactive data + catalog surface.

Endpoints:
  GET  /api/studio/vnccs/host                 -> {host, online, settings}
  PUT  /api/studio/vnccs/host                 -> set pinned host + control-center settings
  GET  /api/studio/vnccs/context-lists        -> models/loras/samplers (settings screen)
  GET  /api/studio/vnccs/characters           -> VNCCS character list on the host
  GET  /api/studio/vnccs/emotions             -> global emotion catalog
  GET  /api/studio/vnccs/pose-library         -> HF-backed pose library
  ANY  /api/studio/vnccs/r/{subpath:path}     -> generic whitelisted relay (JSON or binary)
  POST /api/studio/vnccs/wizard/character     -> LLM Character Wizard (host-first, Ollama fallback)
  POST /api/studio/vnccs/wizard/clothes       -> LLM Clothes Wizard (host-first, Ollama fallback)
  POST /api/studio/vnccs/wizard/clone-analyze -> vision analyze of an uploaded reference
"""

from __future__ import annotations

import asyncio
import logging
import random
from typing import Any, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel
from sqlmodel import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database.database import get_session
from backend.database.models import AppSettings
from backend.services.character_studio.vnccs_native import (
    VNCCSClient,
    VNCCSError,
    resolve_vnccs_host,
    vnccs_host_online,
)
from backend.services.character_studio.vnccs_native.host import list_vnccs_hosts
from backend.services.character_studio.vnccs_native.workflows import (
    assemble_step,
    creator_baseline_gen_settings,
    creator_baseline_pose_data,
    clothes_baseline_widget_data,
    clothes_baseline_control,
    default_pose_set,
    STEP_TAPS,
    STEP_FILES,
    MAX_POSE_SET,
)
from backend.services.character_studio.vnccs_native.workflows import _merge_gen_settings, map_character_info
from backend.services.character_studio.vnccs_native.ingest import ingest_result
from backend.services.character_studio.vnccs_native.catalog import list_catalog, link_to_project

logger = logging.getLogger(__name__)


def _roll_seed(saved_gs: Optional[dict], body_gs: Optional[dict]) -> dict:
    """Layer request gen_settings over saved ones and resolve the seed.

    The vendored baselines carry seed=0, which VNCCS's generate_seed() only
    randomizes AT EXECUTION TIME — but ComfyUI caches nodes by their inputs, so
    resubmitting a byte-identical graph never executes and instantly returns
    the previous images.  Rolling a concrete random seed app-side (node UI
    'randomize' parity) makes every run unique unless the user pins one."""
    gs = {**(saved_gs or {}), **(body_gs or {})}
    mode = str(gs.get("seed_mode") or "randomize").lower()
    try:
        pinned = int(gs.get("seed") or 0)
    except Exception:  # noqa: BLE001
        pinned = 0
    if mode != "fixed" or pinned == 0:
        gs["seed"] = random.getrandbits(48) or 1
    return gs

router = APIRouter(prefix="/api/studio/vnccs", tags=["vnccs_native"])


async def _settings(session: AsyncSession) -> Optional[AppSettings]:
    return (await session.execute(select(AppSettings).limit(1))).scalars().first()


async def _resolve_host(request: Request, session: AsyncSession) -> tuple[Optional[str], Optional[AppSettings]]:
    st = await _settings(session)
    configured = (st.studio_vnccs_host or None) if st else None
    comfy = getattr(request.app.state, "comfy_dispatcher", None)
    host = resolve_vnccs_host(comfy, configured)
    return host, st


def _client(host: str, timeout: int = 30) -> VNCCSClient:
    return VNCCSClient(host, timeout=timeout)


# --------------------------------------------------------------------------- #
# Host config
# --------------------------------------------------------------------------- #
class HostConfigIn(BaseModel):
    host: Optional[str] = None            # pinned URL; None/"" clears the pin
    settings: Optional[dict] = None       # Control Center settings blob


@router.get("/host")
async def get_host(request: Request, session: AsyncSession = Depends(get_session)):
    host, st = await _resolve_host(request, session)
    configured = (st.studio_vnccs_host or None) if st else None
    return {
        "host": host,
        "configured": configured,
        # a resolvable worker == available (same resolution generation uses); the
        # old check also required has_capability which could lag the pool at load
        "online": host is not None,
        "settings": (st.studio_vnccs_settings if st else None) or {},
    }


@router.put("/host")
async def set_host(body: HostConfigIn, request: Request, session: AsyncSession = Depends(get_session)):
    st = await _settings(session)
    if st is None:
        st = AppSettings()
        session.add(st)
    if body.host is not None:
        st.studio_vnccs_host = body.host.strip().rstrip("/") or None
    if body.settings is not None:
        st.studio_vnccs_settings = body.settings
    await session.commit()
    await session.refresh(st)
    host, _ = await _resolve_host(request, session)
    return {"host": host, "configured": st.studio_vnccs_host, "settings": st.studio_vnccs_settings or {}}


# --------------------------------------------------------------------------- #
# Typed convenience endpoints
# --------------------------------------------------------------------------- #
async def _need_host(request: Request, session: AsyncSession) -> str:
    host, _ = await _resolve_host(request, session)
    if not host:
        raise HTTPException(status_code=503, detail="No VNCCS host available. Pin one in Settings or add a vnccs-capable worker.")
    return host


@router.get("/context-lists")
async def context_lists(request: Request, session: AsyncSession = Depends(get_session)):
    host = await _need_host(request, session)
    try:
        return await asyncio.to_thread(_client(host).context_lists)
    except VNCCSError as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.get("/characters")
async def characters(request: Request, session: AsyncSession = Depends(get_session)):
    host = await _need_host(request, session)
    try:
        return await asyncio.to_thread(_client(host).list_characters)
    except VNCCSError as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.get("/emotions")
async def emotions(request: Request, session: AsyncSession = Depends(get_session)):
    host = await _need_host(request, session)
    try:
        return await asyncio.to_thread(_client(host).get_emotions)
    except VNCCSError as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.get("/pose-library")
async def pose_library(request: Request, full: bool = False, session: AsyncSession = Depends(get_session)):
    host = await _need_host(request, session)
    try:
        return await asyncio.to_thread(_client(host).pose_library_list, full)
    except VNCCSError as e:
        raise HTTPException(status_code=502, detail=str(e))


# --------------------------------------------------------------------------- #
# Generic whitelisted relay (JSON or binary passthrough)
# --------------------------------------------------------------------------- #
@router.api_route("/r/{subpath:path}", methods=["GET", "POST", "DELETE", "PUT"])
async def relay(subpath: str, request: Request, session: AsyncSession = Depends(get_session)):
    host = await _need_host(request, session)
    method = request.method
    params = dict(request.query_params)
    # optional per-request worker override (whitelisted against the known VNCCS
    # pool) — lets the UI browse sprite files on the SAME worker a costume
    # preview will run on, instead of always the pinned host
    override = (params.pop("_vnccs_host", "") or "").rstrip("/")
    if override and override != host:
        _, _st = await _resolve_host(request, session)
        _comfy = getattr(request.app.state, "comfy_dispatcher", None)
        _configured = (_st.studio_vnccs_host or None) if _st else None
        known = set(list_vnccs_hosts(_comfy, _configured) or []) | {host}
        if override in known:
            host = override
    json_body: Any = None
    raw: Optional[bytes] = None
    ctype_in = request.headers.get("Content-Type", "")
    if method in ("POST", "PUT"):
        body_bytes = await request.body()
        if body_bytes:
            if "application/json" in ctype_in:
                import json as _json
                try:
                    json_body = _json.loads(body_bytes)
                except Exception:
                    raw = body_bytes
            else:
                raw = body_bytes
    # generation-driving routes can be slow
    slow = any(k in subpath for k in ("preview_generate", "regenerate", "wizard",
                                      "sam3d", "download", "cloner_auto_generate",
                                      "pose_library/repositories", "clothes_preview"))
    timeout = 300 if slow else 60
    client = _client(host, timeout=timeout)
    try:
        status, resp_ctype, content = await asyncio.to_thread(
            client.relay, method, subpath,
            params=params or None,
            json_body=json_body,
            data=raw,
            content_type=(ctype_in if raw is not None else None),
            timeout=timeout,
        )
    except VNCCSError as e:
        raise HTTPException(status_code=502, detail=str(e))
    if "application/json" in resp_ctype:
        import json as _json
        try:
            return JSONResponse(status_code=status, content=_json.loads(content) if content else {})
        except Exception:
            pass
    return Response(status_code=status, content=content, media_type=resp_ctype)


# --------------------------------------------------------------------------- #
# Generation: assemble a VNCCS meganode graph, submit it, poll results
# --------------------------------------------------------------------------- #
import time as _time

_OBJECT_INFO_CACHE: dict[str, tuple[float, dict]] = {}
_OBJECT_INFO_TTL = 300.0


def _object_info(host: str) -> dict:
    now = _time.time()
    hit = _OBJECT_INFO_CACHE.get(host)
    if hit and now - hit[0] < _OBJECT_INFO_TTL:
        return hit[1]
    oi = VNCCSClient(host, timeout=60).get_object_info(timeout=60)
    _OBJECT_INFO_CACHE[host] = (now, oi)
    return oi


class GenerateIn(BaseModel):
    character_name: str
    character_info: dict = {}
    gen_settings: Optional[dict] = None
    control_center: Optional[dict] = None
    generator_overrides: Optional[dict] = None
    nsfw: bool = False
    background: str = "Green"
    # clothes (step 2)
    costume_name: Optional[str] = None
    costume_info: Optional[dict] = None
    clone_image: Optional[dict] = None
    clone_sam_prompt: Optional[str] = None
    # emotions (step 3)
    costumes: Optional[list] = None
    emotions: Optional[list] = None
    generation_model: str = "Anima"
    prompt_style: str = "Anima"
    # cloner (step 1 clone)
    cloner_images: Optional[list] = None
    # pose selection (creator / cloner / clothes)
    pose_set: Optional[list] = None
    pose_names: Optional[list] = None        # display names aligned with pose_set
    # generation engine: None/'qwen' = VNCCS meganode graph (QIE);
    # 'klein' = Klein 9B pose graph (vnccs-utils Pose Studio Klein9b parity)
    engine: Optional[str] = None
    # Klein base outfit: 'strip' (underwear/nude base) | 'keep' (clone the
    # reference's clothing).  None = fall back to studio setting klein_base_clothing.
    base_clothing: Optional[str] = None
    # Character render type: 'auto' | 'realistic' | 'anime' | '3d'. Drives PuLID:
    # 'realistic' forces it on (InsightFace finds the face even when app-side
    # detection misses); 'anime'/'3d' skip it (InsightFace can't read them).
    face_kind: Optional[str] = None
    style_custom: Optional[str] = None       # free-text when face_kind == 'custom'
    # Pose consistency: True = lock every pose to the APPROVED base render
    # (one consistent body); False = use the raw references per pose.  None =
    # fall back to studio setting klein_lock_base (default on).
    lock_base: Optional[bool] = None
    # Klein render tuning (per-run overrides of studio settings):
    cleanup: Optional[str] = None            # 'off' | 'gentle' | 'strong'
    klein_steps: Optional[int] = None        # sampler steps (default 6)


def _resolve_lock_base(settings: dict, body: "GenerateIn") -> bool:
    """Lock-base policy: body flag wins, else studio setting klein_lock_base
    (default ON).  When on, a Klein pose run uses the character's APPROVED base
    render as its single body/identity reference so every pose inherits ONE
    consistent, correctly-proportioned body (falls back to the raw references
    when no base has been generated yet)."""
    b = getattr(body, "lock_base", None)
    if b is not None:
        return bool(b)
    return str((settings or {}).get("klein_lock_base") or "on").strip().lower() \
        not in ("off", "false", "0", "no", "disabled", "none")


async def _saved_character_info(session, name: str) -> dict:
    """Load a saved character's stored character_info (build / hair / etc.) from
    the catalog.  Pose runs post an EMPTY character_info, so without this the pose
    mannequin's build and the body prompt text would be lost — the character would
    always render on the default mannequin body."""
    from sqlmodel import select as _select
    from backend.database.models import StudioCharacter
    try:
        char = (await session.execute(
            _select(StudioCharacter).where(StudioCharacter.name == (name or "").strip()))).scalars().first()
    except Exception:  # noqa: BLE001
        return {}
    if char is None:
        return {}
    v = (char.manifest or {}).get("vnccs") or {}
    for k in ("clone", "form"):
        ci = ((v.get(k) or {}).get("character_info")) or {}
        if isinstance(ci, dict) and ci:
            return dict(ci)
    return {}


async def _enrich_character_info(session, body) -> None:
    """Fill in body.character_info from the saved character when the client sent
    little/none (pose runs do) — so the build reaches the mannequin + prompt."""
    ci = dict(getattr(body, "character_info", None) or {})
    if str(ci.get("body") or "").strip() and str(ci.get("skin_color") or "").strip():
        return  # already populated by the client
    saved = await _saved_character_info(session, getattr(body, "character_name", "") or "")
    if saved:
        # client-sent values win; saved fills the gaps
        body.character_info = {**saved, **ci}


async def _klein_identity_bytes(session, body: GenerateIn, pinned: str,
                                lock_base: bool = False) -> list:
    """Identity image(s) for a Klein pose run.

    Clone runs: up to 4 uploaded references, fed DIRECTLY as Klein reference
    latents (native multi-ref — replaces the Qwen source-grid trick).
    Create runs: the ACTIVE base version, else the newest cataloged final
    sprite.  Returns a non-empty list of PNG bytes or raises 409."""
    from pathlib import Path as _Path
    from uuid import UUID as _UUID
    from sqlmodel import select as _select
    from backend.config import settings as _cfg
    from backend.database.models import Asset, StudioCharacter

    name = body.character_name.strip()
    char = (await session.execute(
        _select(StudioCharacter).where(StudioCharacter.name == name))).scalars().first()

    async def _asset_bytes(aid: str) -> Optional[bytes]:
        try:
            a = await session.get(Asset, _UUID(str(aid)))
        except Exception:  # noqa: BLE001
            return None
        if a is None:
            return None
        rel = str(a.rel_path).replace("\\", "/")
        pid = str(a.project_id)
        p = (_Path(_cfg.project_dir) / rel if rel.startswith(pid + "/")
             else _Path(_cfg.project_dir) / pid / rel)
        try:
            return p.read_bytes() if p.exists() else None
        except Exception:  # noqa: BLE001
            return None

    async def _active_base_bytes() -> Optional[bytes]:
        if char is None:
            return None
        v = (char.manifest or {}).get("vnccs") or {}
        active = v.get("active_base")
        for bv in (v.get("base_versions") or []):
            if isinstance(bv, dict) and bv.get("id") == active and bv.get("asset_id"):
                data = await _asset_bytes(bv["asset_id"])
                if data:
                    return data
        return None

    # LOCK-BASE: use the approved base render as the SINGLE body/identity
    # reference so every pose inherits one consistent, correctly-proportioned
    # body.  Bust references re-derive the body per pose (drift / oversized
    # head); the approved base does not.  Falls back to references when no base
    # has been generated yet.
    if lock_base:
        base = await _active_base_bytes()
        if base:
            logger.info("klein identity: LOCK-BASE -> approved base version is the body reference")
            return [base]
        logger.info("klein identity: lock-base on but no active base yet -> using references "
                    "(generate a base preview to lock proportions)")

    # clone references: Klein multi-ref wants the raw sources
    refs: list = []
    for img in (body.cloner_images or [])[:4]:
        nm = (img or {}).get("name") or ""
        if not nm:
            continue
        try:
            refs.append(await asyncio.to_thread(
                _client(pinned, timeout=120).view_image, nm,
                (img or {}).get("subfolder", "") or "", (img or {}).get("type", "input") or "input", 120))
        except Exception:  # noqa: BLE001
            continue
    if refs:
        return refs

    base = await _active_base_bytes()
    if base:
        return [base]
    if char is not None:
        v = (char.manifest or {}).get("vnccs") or {}
        outputs = v.get("outputs") or {}
        for label in ("cloner/original_sprites", "creator/sheet", "creator/sprites", "cloner/sprites"):
            for aid in (outputs.get(label) or []):
                data = await _asset_bytes(aid)
                if data:
                    return [data]
    raise HTTPException(status_code=409, detail=(
        "Klein pose runs need an identity image — generate a base preview "
        "(✨ Generate Character) or upload clone references first."))


def _face_crop_bytes(data: bytes, expand_pct: float = 0.6) -> Optional[bytes]:
    """Crop the largest detected face out of raw image bytes (app-side CPU
    detect: YuNet/Haar).  Returns PNG bytes or None when no face is found.
    Sync — run in a thread."""
    import tempfile
    from pathlib import Path as _Path
    from backend.services.character_studio.faces import crop_face
    try:
        with tempfile.TemporaryDirectory(prefix="rbmn_face_") as td:
            src = _Path(td) / "src.png"
            out = _Path(td) / "face.png"
            src.write_bytes(data)
            if crop_face(src, out, expand_pct=expand_pct) is None:
                return None
            return out.read_bytes()
    except Exception:  # noqa: BLE001
        logger.exception("klein: face crop failed")
        return None


def _klein_identity_crop(data: bytes, expand_pct: float = 0.6):
    """Identity face crop for Klein, with an anime fallback.  Returns
    ``(png_bytes, method)`` or ``None``.  ``method`` is 'yunet'/'haar' for a real
    photographic detection or 'heuristic' when YuNet+Haar miss (stylized/anime
    faces defeat them) and we fall back to an upper-center HEAD crop.  The
    heuristic crop still gives strip mode an identity reference that excludes the
    body/outfit — critical for VNCCS's stylized characters, where real face
    detection (and therefore PuLID) never fires."""
    import tempfile
    from pathlib import Path as _Path
    from backend.services.character_studio.faces import crop_face
    try:
        with tempfile.TemporaryDirectory(prefix="rbmn_idc_") as td:
            src = _Path(td) / "src.png"
            out = _Path(td) / "face.png"
            src.write_bytes(data)
            res = crop_face(src, out, expand_pct=expand_pct)
            if res is not None:
                return out.read_bytes(), str(res.get("method") or "detected")
            # heuristic: upper-center HEAD region (excludes shoulders/straps)
            from PIL import Image as _Img
            with _Img.open(src) as im:
                im = im.convert("RGB")
                w, h = im.size
                fw = max(1, int(w * 0.32))
                fh = max(1, int(h * 0.26))
                fx = max(0, int(w * 0.5 - fw / 2))
                fy = max(0, int(h * 0.03))
                crop = im.crop((fx, fy, fx + fw, fy + fh))
                crop.save(out, format="PNG")
            return out.read_bytes(), "heuristic"
    except Exception:  # noqa: BLE001
        logger.exception("klein: identity crop failed")
        return None


def _classify_ref_role(data: bytes) -> str:
    """Auto-suggest a reference ROLE for the role-tagging UI: 'face' (a close-up
    headshot that should drive the face crop + PuLID), 'body' (no usable face —
    a body shot, drives the masked body channel), or 'full' (face + body, drives
    both).  Cheap heuristic from the detected face-crop size vs the whole image;
    the UI defaults to this but lets the user override.  Never raises."""
    try:
        import io as _io
        from PIL import Image as _Image
        with _Image.open(_io.BytesIO(data)) as im:
            w, h = im.size
        w = max(1, int(w))
        h = max(1, int(h))
        aspect = h / float(w)
        fc = _klein_identity_crop(data, expand_pct=0.35)
        if not fc:
            return "full"
        cbytes, method = fc
        with _Image.open(_io.BytesIO(cbytes)) as cim:
            cw, ch = cim.size
        area = (int(cw) * int(ch)) / float(w * h)
        if method not in ("yunet", "haar"):
            # no real face detected -> treat as a body/scene shot
            return "body" if aspect >= 1.15 else "full"
        # real face: a 0.35-expanded crop that still dominates the frame = close-up
        return "face" if area >= 0.30 else "full"
    except Exception:  # noqa: BLE001
        return "full"


def _context_crop_box(bbox: dict, img_w: int, img_h: int,
                      pad_factor: float = 0.75, min_side: int = 256) -> dict:
    """Expand a face bbox into the CONTEXT box Klein repaints (crop-and-stitch):
    generous padding so the model sees hair/shoulders around the face, clamped
    to the image, sides rounded to multiples of 8."""
    x, y, w, h = int(bbox["x"]), int(bbox["y"]), int(bbox["w"]), int(bbox["h"])
    pad = int(max(w, h) * pad_factor)
    x0 = max(0, x - pad)
    y0 = max(0, y - pad)
    x1 = min(img_w, x + w + pad)
    y1 = min(img_h, y + h + pad)
    # honor a minimum context size when the face is tiny
    if x1 - x0 < min_side:
        grow = (min_side - (x1 - x0)) // 2 + 1
        x0, x1 = max(0, x0 - grow), min(img_w, x1 + grow)
    if y1 - y0 < min_side:
        grow = (min_side - (y1 - y0)) // 2 + 1
        y0, y1 = max(0, y0 - grow), min(img_h, y1 + grow)
    cw = max(8, ((x1 - x0) // 8) * 8)
    ch = max(8, ((y1 - y0) // 8) * 8)
    cw = min(cw, img_w - x0)
    ch = min(ch, img_h - y0)
    return {"x": x0, "y": y0, "w": cw, "h": ch}


def _klein_submit(host: str, st_settings: dict, body: GenerateIn,
                  pose_subset: list, identity_bytes: list, seed: int):
    """Render pose captures app-side, upload them + the identity image to the
    worker, assemble the Klein9b pose graph and submit.  Returns
    (prompt_id, tap_map).  Sync — run in a thread."""
    from backend.services.character_studio.vnccs_native import klein_poses, pose_render
    from backend.services.character_studio.vnccs_native.workflows import creator_baseline_pose_data

    oi = _object_info(host)
    models = klein_poses.resolve_klein_models(oi, st_settings)

    pd = creator_baseline_pose_data()
    pd["poses"] = [p for p in pose_subset if isinstance(p, dict)]
    # drive the pose MANNEQUIN's build from the character so the rendered pose
    # reference (which Klein reproduces as the body) matches the intended shape.
    pd["mesh"] = {**(pd.get("mesh") or {}),
                  **klein_poses.body_mesh_params(body.character_info or {})}
    captures = pose_render.render_pose_captures(pd, False)
    if not captures or len(captures) != len(pd["poses"]):
        raise VNCCSError("app-side pose renderer unavailable (CharacterData missing?) — "
                         "cannot build Klein pose references")

    safe = "".join(ch for ch in body.character_name if ch.isalnum())[:24] or "char"
    # UNIQUE-per-chunk upload names.  Multiple workers often share ONE ComfyUI
    # input folder (multi-GPU boxes / one host under several URLs); with fixed
    # names every chunk's pose captures overwrote the previous chunk's BEFORE
    # the queued jobs executed, so all chunks rendered the LAST chunk's poses
    # (the "same N poses repeated per worker" bug).
    import uuid as _uuid
    token = _uuid.uuid4().hex[:8]
    client = _client(host, timeout=120)
    identity_files = []
    for k, ib in enumerate(identity_bytes[:4]):
        fn = f"rbmn_klein_{safe}_{token}_identity{k}.png"
        up = client.upload_image(fn, ib, "", True, 120)
        identity_files.append(up.get("name", fn))
    # resolve the base-outfit policy EARLY: it decides the face-crop tightness,
    # whether any (clothing-carrying) image is referenced at all, and the prompt
    base_clothing = str(getattr(body, "base_clothing", None)
                        or (st_settings or {}).get("klein_base_clothing") or "strip")
    _keep = klein_poses._keep_clothing(base_clothing)
    # Per-image roles from the UI (aligned to identity order), computed EARLY so the
    # face crop is taken from a dedicated 'face' image when the user tagged one.
    _ci = list(getattr(body, "cloner_images", None) or [])
    _roles: list = []
    for _k in range(len(identity_files)):
        _r = ""
        if _k < len(_ci) and isinstance(_ci[_k], dict):
            _r = str(_ci[_k].get("role") or "").strip().lower()
        _roles.append(_r if _r in ("face", "body", "full") else "full")
    # face-crop source: a dedicated 'face' ref first, then a 'full', else the first.
    _face_pick = 0
    for _pref in ("face", "full"):
        _hit = next((k for k in range(len(identity_bytes))
                     if k < len(_roles) and _roles[k] == _pref), None)
        if _hit is not None:
            _face_pick = _hit
            break
    # v1.77.0: a close-up crop of the identity face. STRIP crops TIGHT (face+hair,
    # no shoulders) so a strappy top can't ride along; KEEP keeps the wide crop.
    face_file = None
    face_index = None
    face_kind = str(getattr(body, "face_kind", None) or "auto").strip().lower()
    style_custom = str(getattr(body, "style_custom", None) or "").strip()
    fc = _klein_identity_crop(identity_bytes[_face_pick], expand_pct=(0.2 if not _keep else 0.6))
    real_face = False
    if fc:
        face_bytes, face_method = fc
        real_face = face_method in ("yunet", "haar")
        up = client.upload_image(f"rbmn_klein_{safe}_{token}_face.png", face_bytes, "", True, 120)
        face_file = up.get("name", f"rbmn_klein_{safe}_{token}_face.png")
    else:
        logger.info("klein: identity crop unavailable — pose run proceeds without a "
                    "face-crop reference")
    # PuLID decision by character type: 'realistic' forces it on (its InsightFace
    # finds the face on the worker even when OUR app-side detector missed);
    # 'anime'/'3d' skip it (InsightFace can't read stylized faces and would error);
    # 'auto' uses it only when app-side detection actually found a photo face.
    # PuLID is opt-in (needs insightface on the worker). Never for stylized faces
    # (InsightFace can't read them); otherwise let resolve_pulid decide (it returns
    # None unless klein_pulid='on'). No more forcing it — that just errored the job.
    if klein_poses._style_is_stylized(face_kind):
        pulid = None
    else:
        pulid = klein_poses.resolve_pulid(oi, st_settings) if face_file else None
    if pulid:
        logger.info("klein: PuLID-Flux2 active (%s, strength %.2f)",
                    pulid["file"], pulid["strength"])
    face_refine = klein_poses.resolve_face_refine(oi, st_settings)
    if face_refine:
        logger.info("klein: face refine active (FaceDetailer %s, denoise %.2f)",
                    face_refine["detector"], face_refine["denoise"])
    kposes_nsfw = bool(getattr(body, "nsfw", False))
    details_txt = klein_poses.klein_detail_text(body.character_info or {})
    # STRIP: withhold the clothed full-body reference. When PuLID is active it
    # carries the face as an EMBEDDING (no pixel copy), so we ALSO drop the
    # face-crop reference latent -> ZERO clothed pixels referenced, so the outfit
    # simply cannot leak. Without PuLID we keep the (tight) face crop for identity.
    # VNCCS-style: KEEP the full-body reference in strip mode too (withholding it
    # made Klein invent a generic/anime body -- "skin on a wrong-shaped head").
    # Klein builds the character FROM the references (body+face+proportions); the
    # prompt redresses to white underwear (mirrors VNCCS remove_clothes: keep the
    # full image, change only the clothing). Face crop rides along; PuLID additive.
    strip_body_refs = False
    face_as_reference = bool(face_file)
    # BODY-MATCH channel (ReferenceLatentPlus): when the node is on the worker we
    # route the BODY/FULL references through it with the garment masked out, so the
    # base body matches the photo's build/shoulders/chest/hips WITHOUT the outfit
    # leaking.  Face still rides on the crop + PuLID.  Auto-detected + opt-out; on
    # workers without the node this stays None and behaviour is unchanged.
    reflatentplus = klein_poses.resolve_reflatentplus(oi, st_settings) if not _keep else None
    # (roles computed earlier for the face-crop pick; reused here for the body split)
    body_files: list = []
    graph_identity = list(identity_files)
    body_ref_active = False
    if reflatentplus:
        body_files = [identity_files[_k] for _k, _r in enumerate(_roles)
                      if _r in ("body", "full")]
        # body carried (masked) via the Plus channel; face rides on the crop+PuLID,
        # so NO image is sent as a stock full-image reference latent (that is what
        # leaked the outfit).  Fall back to stock refs only if role-split left the
        # body channel empty (e.g. every ref tagged face-only).
        if body_files:
            graph_identity = []
            body_ref_active = True
        else:
            reflatentplus = None
    if body_ref_active:
        n_ident = 0
        face_index = None
    else:
        n_ident = 0 if strip_body_refs else len(graph_identity)
        if face_file and face_as_reference:
            face_index = 2 if strip_body_refs else (1 + len(graph_identity) + 1)
        else:
            face_index = None
    appearance_txt = (klein_poses.klein_body_text(body.character_info or {})
                      if not _keep else None)
    logger.info("klein base-outfit: mode=%s keep=%s face_kind=%s real_face=%s "
                "strip_body_refs=%s pulid=%s face_ref=%s", base_clothing, _keep,
                face_kind, real_face, strip_body_refs, bool(pulid), face_as_reference)
    pose_files = []
    prompts = []
    for i, cap in enumerate(captures):
        fn = f"rbmn_klein_{safe}_{token}_pose{i}.png"
        up = client.upload_image(fn, klein_poses.decode_capture(cap), "", True, 120)
        pose_files.append(up.get("name", fn))
        prompts.append(klein_poses.klein_pose_prompt(
            (pd["poses"][i] or {}).get("prompt", ""), body.background, n_ident,
            face_image_index=face_index, details=details_txt,
            base_clothing=base_clothing, nsfw=kposes_nsfw, appearance=appearance_txt,
            style_kind=face_kind, sex=str((body.character_info or {}).get("sex") or ""),
            body_ref_active=body_ref_active, style_custom=style_custom))

    # honor the UI's upscaler control: any non-off mode = GAN tail (SeedVR has
    # no simple graph form; the label maps to GAN here)
    upcfg = ((body.generator_overrides or {}).get("upscaler") or {})
    up_mode = str(upcfg.get("mode") or "off").lower()
    upscale_model = None
    up_mp = None
    if up_mode != "off":
        upscale_model = klein_poses.resolve_upscale_model(oi, st_settings)
        try:
            res_px = int(upcfg.get("resolution") or 0)
        except Exception:  # noqa: BLE001
            res_px = 0
        up_mp = (res_px * res_px) / 1_000_000.0 if res_px > 0 else None
        if not upscale_model:
            logger.warning("klein: no UpscaleModelLoader models on %s — skipping upscale tail", host)

    _eff = dict(st_settings or {})
    if getattr(body, "cleanup", None):
        _eff["klein_cleanup"] = body.cleanup
    if getattr(body, "klein_steps", None):
        _eff["klein_steps"] = body.klein_steps
    neg_text, klein_cfg = klein_poses.resolve_strip_negative(_eff, _keep)
    ksteps = klein_poses.resolve_klein_steps(_eff)
    logger.info("klein cleanup: cfg=%.2f steps=%d neg=%s", klein_cfg, ksteps, bool(neg_text))
    rmbg = klein_poses.resolve_rmbg(oi, st_settings)
    if rmbg:
        logger.info("klein: worker-side RMBG background removal active (%s, res %d)",
                    rmbg.get("model"), rmbg.get("process_res"))
    if body_ref_active:
        logger.info("klein body-match: ReferenceLatentPlus active — %d body ref(s) "
                    "masked (garment excluded), face on crop+PuLID", len(body_files))
    api, tap_map = klein_poses.build_klein_pose_graph(
        pose_files=pose_files,
        identity_files=graph_identity,
        prompts=prompts, seed=seed, models=models, steps=ksteps,
        upscale_model=upscale_model, upscale_megapixels=up_mp,
        face_file=face_file, pulid=pulid, face_refine=face_refine,
        strip_body_refs=strip_body_refs, face_as_reference=face_as_reference,
        negative_prompt=neg_text, cfg=klein_cfg, rmbg=rmbg,
        body_files=body_files, reflatentplus=reflatentplus,
        filename_prefix=f"rbmn_vnccs/{safe}/klein_sprites")
    res = client.submit_prompt(api, timeout=120)
    extras = {"face_ref": bool(face_file),
              "pulid_file": (pulid or {}).get("file"),
              "pulid_strength": (pulid or {}).get("strength"),
              "face_refine": bool(face_refine)}
    return res.get("prompt_id"), tap_map, extras


def _native_submit(step: str, host: str, ref_host: str, body: "GenerateIn",
                   pose_subset, gen_settings: dict, control_center):
    """Assemble a native VNCCS meganode graph for ``step`` and submit it to
    ``host``.  Cloner reads its sources from the worker's LOCAL input folder, so
    references (uploaded to ``ref_host``) are replicated to ``host`` first.
    Sync — run in a thread.  Returns ``(prompt_id, tap_map)``.  Mirrors the
    ``_submit``/``_replicate_refs`` closures in generate_parallel."""
    from backend.services.character_studio.vnccs_native.workflows import assemble_step
    cloner_images = body.cloner_images
    if step == "cloner" and body.cloner_images and host != ref_host and ref_host:
        src = _client(ref_host, timeout=120)
        dst = _client(host, timeout=120)
        reps = []
        for img in (body.cloner_images or []):
            nm = (img or {}).get("name") or ""
            if not nm:
                continue
            sub = (img or {}).get("subfolder", "") or ""
            typ = (img or {}).get("type", "input") or "input"
            data = src.view_image(nm, sub, typ, 120)
            up = dst.upload_image(nm, data, sub, True, 120)
            reps.append({"name": up.get("name", nm),
                         "subfolder": up.get("subfolder", sub),
                         "type": up.get("type", "input")})
        cloner_images = reps
    oi = _object_info(host)
    api, tap_map = assemble_step(
        step, oi,
        character_name=body.character_name,
        character_info=body.character_info,
        gen_settings=gen_settings,
        control_center=control_center,
        generator_overrides=body.generator_overrides,
        nsfw=body.nsfw,
        background=body.background,
        costume_name=body.costume_name,
        costume_info=body.costume_info,
        clone_image=body.clone_image,
        clone_sam_prompt=body.clone_sam_prompt,
        costumes=body.costumes,
        emotions=body.emotions,
        generation_model=body.generation_model,
        prompt_style=body.prompt_style,
        cloner_images=cloner_images,
        pose_set=pose_subset,
    )
    res = VNCCSClient(host, timeout=120).submit_prompt(api, timeout=120)
    return res.get("prompt_id"), tap_map


def _klein_run_suffix(extras: Optional[dict]) -> str:
    """Human-readable face-consistency tag appended to run/chunk labels —
    the in-UI indicator that the identity machinery actually engaged."""
    e = extras or {}
    parts = []
    if e.get("face_ref"):
        parts.append("face-ref")
    elif e.get("face_anchor"):
        parts.append("face-anchor")
    if e.get("pulid_file"):
        parts.append("PuLID")
    if e.get("face_refine"):
        parts.append("detail")
    return f" · {'+'.join(parts)}" if parts else ""


def _klein_wait_first_image(client, prompt_id: str, timeout_s: int = 600) -> str:
    """Poll a submitted job's history until it yields an image; return b64."""
    import base64 as _b64
    import time as _time
    deadline = _time.time() + timeout_s
    while _time.time() < deadline:
        try:
            hist = client.get_history(prompt_id, timeout=30)
        except VNCCSError:
            hist = None
        entry = hist.get(prompt_id) if isinstance(hist, dict) else None
        outs = (entry or {}).get("outputs") or {}
        imgs = []
        for out in outs.values():
            imgs.extend(out.get("images") or [])
        if imgs:
            data = client.view_image(imgs[0]["filename"], imgs[0].get("subfolder", ""),
                                     imgs[0].get("type", "output"), 120)
            return _b64.b64encode(data).decode("ascii")
        status = ((entry or {}).get("status") or {})
        if status.get("status_str") == "error":
            raise VNCCSError("Klein job errored on the worker — check its console")
        _time.sleep(2)
    raise VNCCSError(f"Klein job timed out after {timeout_s}s")


def _klein_wait_all_images(client, prompt_id: str, timeout_s: int = 600) -> list:
    """Like _klein_wait_first_image but returns ALL images the job produced
    (base64), in batch order — used by the 4-view base set."""
    import base64 as _b64
    import time as _time
    deadline = _time.time() + timeout_s
    while _time.time() < deadline:
        try:
            hist = client.get_history(prompt_id, timeout=30)
        except VNCCSError:
            hist = None
        entry = hist.get(prompt_id) if isinstance(hist, dict) else None
        outs = (entry or {}).get("outputs") or {}
        imgs = []
        for out in outs.values():
            imgs.extend(out.get("images") or [])
        if imgs:
            result = []
            for im in imgs:
                data = client.view_image(im["filename"], im.get("subfolder", ""),
                                         im.get("type", "output"), 120)
                result.append(_b64.b64encode(data).decode("ascii"))
            return result
        status = ((entry or {}).get("status") or {})
        if status.get("status_str") == "error":
            raise VNCCSError("Klein job errored on the worker — check its console")
        _time.sleep(2)
    raise VNCCSError(f"Klein job timed out after {timeout_s}s")


def _klein_preview_timeout(steps, views: int = 1) -> int:
    """Wait budget for a Klein preview, scaled by step count (baseline 6) and
    view count.  Sized so the heaviest supported config (14 steps, 4-view set)
    finishes well inside it while the host is still working; everything lighter
    fits in less.  600s single / 1200s set at 6 steps, scaled up with steps."""
    try:
        st = max(2, int(steps or 6))
    except Exception:  # noqa: BLE001
        st = 6
    vw = max(1, int(views or 1))
    base = 1200 if vw > 1 else 600
    scale = max(1.0, st / 6.0)
    return int(base * scale) + 300


def _vnccs_wait_tap_image(client, prompt_id: str, prefer_nodes: list,
                          timeout_s: int = 900) -> str:
    """Poll a job until it finishes, then return (b64) the first image from the
    first PREFERRED tap node that produced one — falling back to any image.
    Unlike _klein_wait_first_image this waits for COMPLETION, so multi-tap
    meganode runs (Cloner) return the posed sprite, not whichever helper tap
    (face crop / sheet) happened to save first."""
    import base64 as _b64
    import time as _time
    deadline = _time.time() + timeout_s
    while _time.time() < deadline:
        try:
            hist = client.get_history(prompt_id, timeout=30)
        except VNCCSError:
            hist = None
        entry = hist.get(prompt_id) if isinstance(hist, dict) else None
        status = ((entry or {}).get("status") or {})
        if status.get("status_str") == "error":
            raise VNCCSError("VNCCS job errored on the worker — check its console")
        if entry is not None and (status.get("completed") or status.get("status_str") == "success"):
            outs = entry.get("outputs") or {}
            ordered = [n for n in prefer_nodes if n in outs] + \
                      [n for n in outs if n not in prefer_nodes]
            for nid in ordered:
                imgs = (outs.get(nid) or {}).get("images") or []
                if imgs:
                    data = client.view_image(imgs[0]["filename"], imgs[0].get("subfolder", ""),
                                             imgs[0].get("type", "output"), 120)
                    return _b64.b64encode(data).decode("ascii")
            raise VNCCSError("VNCCS job finished but produced no images")
        _time.sleep(2)
    raise VNCCSError(f"VNCCS job timed out after {timeout_s}s")


@router.post("/generate/{step}")
async def generate_step(step: str, body: GenerateIn, request: Request,
                        session: AsyncSession = Depends(get_session)):
    """Assemble the VNCCS Step graph from the form, submit it to the host, return
    the prompt_id + tap map.  Poll ``/result/{prompt_id}`` for the output images."""
    if step not in STEP_FILES:
        raise HTTPException(status_code=404, detail=f"unknown step {step!r}")
    host = await _need_host(request, session)
    # Merge saved Control-Center settings unless the request overrides them.
    st = await _settings(session)
    saved = (st.studio_vnccs_settings if st else None) or {}
    control_center = body.control_center or saved.get("control_center")
    gen_settings = _roll_seed(saved.get("gen_settings"), body.gen_settings)

    if (body.engine or "").lower() == "klein":
        if step not in ("creator", "cloner"):
            raise HTTPException(status_code=400,
                                detail="Klein engine currently covers pose generation (Create/Clone) only")
        poses = [p for p in (body.pose_set or []) if isinstance(p, dict)]
        if not poses:
            raise HTTPException(status_code=400, detail="Select at least one pose for a Klein run")
        identity = await _klein_identity_bytes(session, body, host,
                                               _resolve_lock_base(saved, body))
        try:
            prompt_id, tap_map, kextras = await asyncio.to_thread(
                _klein_submit, host, saved, body, poses, identity, int(gen_settings.get("seed") or 1))
        except (VNCCSError, ValueError) as e:
            raise HTTPException(status_code=502, detail=str(e))
        return {"prompt_id": prompt_id, "host": host, "step": step, "tap_map": tap_map,
                "seed": gen_settings.get("seed"), "engine": "klein",
                "face_consistency": kextras}

    def _assemble_and_submit():
        oi = _object_info(host)
        api, tap_map = assemble_step(
            step, oi,
            character_name=body.character_name,
            character_info=body.character_info,
            gen_settings=gen_settings,
            control_center=control_center,
            generator_overrides=body.generator_overrides,
            nsfw=body.nsfw,
            background=body.background,
            costume_name=body.costume_name,
            costume_info=body.costume_info,
            clone_image=body.clone_image,
            clone_sam_prompt=body.clone_sam_prompt,
            costumes=body.costumes,
            emotions=body.emotions,
            generation_model=body.generation_model,
            prompt_style=body.prompt_style,
            cloner_images=body.cloner_images,
            pose_set=body.pose_set,
        )
        client = VNCCSClient(host, timeout=120)
        res = client.submit_prompt(api, timeout=120)
        return res.get("prompt_id"), tap_map

    try:
        prompt_id, tap_map = await asyncio.to_thread(_assemble_and_submit)
    except VNCCSError as e:
        raise HTTPException(status_code=502, detail=str(e))
    except Exception as e:
        logger.exception("VNCCS assemble/submit failed")
        raise HTTPException(status_code=500, detail=str(e))
    return {"prompt_id": prompt_id, "host": host, "step": step, "tap_map": tap_map,
            "seed": gen_settings.get("seed")}


@router.get("/result/{prompt_id}")
async def generate_result(prompt_id: str, request: Request,
                          host: Optional[str] = None,
                          session: AsyncSession = Depends(get_session)):
    """Poll the host history for a submitted VNCCS prompt.  Returns per-node output
    images (filename/subfolder/type) once the job completes.  ``host`` overrides
    the pinned worker (parallel fan-out chunks live on different workers)."""
    host = (host or "").rstrip("/") or await _need_host(request, session)

    def _poll():
        hist = VNCCSClient(host, timeout=30).get_history(prompt_id, timeout=30)
        entry = hist.get(prompt_id) if isinstance(hist, dict) else None
        if not entry:
            return {"status": "pending", "images": []}
        status = (entry.get("status") or {}).get("status_str") or "running"
        images = []
        for node_id, out in (entry.get("outputs") or {}).items():
            for img in out.get("images", []) or []:
                images.append({"node_id": node_id, "filename": img.get("filename"),
                               "subfolder": img.get("subfolder", ""), "type": img.get("type", "output")})
        if status == "error":
            return {"status": "error", "images": images}
        done = bool(entry.get("outputs")) or status == "success"
        return {"status": "completed" if done else status, "images": images}

    try:
        return await asyncio.to_thread(_poll)
    except VNCCSError as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.get("/view")
async def view_image(filename: str, request: Request, subfolder: str = "",
                     type: str = "output", host: Optional[str] = None,
                     session: AsyncSession = Depends(get_session)):
    """Proxy a single generated image from the host's /view (so the browser can
    render VNCCS outputs without hitting the worker directly)."""
    host = (host or "").rstrip("/") or await _need_host(request, session)
    try:
        data = await asyncio.to_thread(
            VNCCSClient(host, timeout=60).view_image, filename, subfolder, type, 60)
    except VNCCSError as e:
        raise HTTPException(status_code=502, detail=str(e))
    return Response(content=data, media_type="image/png")


class IngestIn(BaseModel):
    costume: Optional[str] = None
    prompt_id: str
    host: Optional[str] = None
    character_name: str
    step: str
    tap_map: dict
    story_id: Optional[str] = None
    emotions: Optional[list] = None          # emotions step: which emotions ran
    costumes: Optional[list] = None          # emotions step: which sets ran
    seed: Optional[int] = None               # the rolled seed (regeneration)
    pose_names: Optional[list] = None        # pose steps: display names of the FULL run's poses
    pose_set: Optional[list] = None          # pose steps: the FULL run's pose dicts (recipe)
    postprocess: Optional[str] = None        # 'chroma' = app-side BG cutout (Klein runs)
    chunk_pose_names: Optional[list] = None  # THIS chunk's pose names (image order)
    engine: Optional[str] = None             # 'klein' stamps the character's variant


@router.post("/ingest")
async def ingest(body: IngestIn, request: Request, session: AsyncSession = Depends(get_session)):
    """Download a finished VNCCS job's tapped outputs and catalog them on a
    StudioCharacter (assets in our store, link in manifest['vnccs'])."""
    host = body.host or await _need_host(request, session)
    story_uuid = None
    if body.story_id:
        try:
            from uuid import UUID as _UUID
            story_uuid = _UUID(body.story_id)
        except Exception:
            story_uuid = None
    try:
        return await ingest_result(
            session, host=host, prompt_id=body.prompt_id,
            character_name=body.character_name, step=body.step,
            tap_map=body.tap_map, story_id=story_uuid,
            costume=body.costume,
            emotions=body.emotions, costumes=body.costumes, seed=body.seed,
            pose_names=body.pose_names, pose_set_full=body.pose_set,
            postprocess=body.postprocess,
            chunk_pose_names=body.chunk_pose_names,
            engine=body.engine,
        )
    except VNCCSError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except Exception as e:
        logger.exception("VNCCS ingest failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/upload")
async def upload_reference(request: Request, file: UploadFile = File(...),
                          session: AsyncSession = Depends(get_session)):
    """Upload a reference image to the VNCCS host's input folder (for the Cloner /
    costume clone-from-reference). Returns ComfyUI's {name, subfolder, type}."""
    host = await _need_host(request, session)
    data = await file.read()
    fname = file.filename or "reference.png"
    try:
        res = await asyncio.to_thread(
            VNCCSClient(host, timeout=120).upload_image, fname, data, "", True, 120)
    except VNCCSError as e:
        raise HTTPException(status_code=502, detail=str(e))
    # auto-suggest a role for the role-tagging UI (face / body / full); best-effort
    try:
        role = await asyncio.to_thread(_classify_ref_role, data)
    except Exception:  # noqa: BLE001
        role = "full"
    if isinstance(res, dict):
        return {**res, "suggested_role": role}
    return res


def _wait_first_image_bytes(client, prompt_id: str, timeout_s: int = 600) -> bytes:
    """Poll a worker's history for ``prompt_id`` and return the first output
    image's raw bytes.  Sync — run in a thread."""
    import time as _t
    deadline = _t.time() + max(30, int(timeout_s))
    while _t.time() < deadline:
        hist = client.get_history(prompt_id, timeout=30)
        entry = hist.get(prompt_id) if isinstance(hist, dict) else None
        if entry:
            for _nid, out in (entry.get("outputs") or {}).items():
                for img in (out.get("images") or []):
                    fn = img.get("filename")
                    if fn:
                        return client.view_image(fn, img.get("subfolder", ""),
                                                 img.get("type", "output"), 120)
            if ((entry.get("status") or {}).get("status_str")) == "error":
                raise VNCCSError("reference upscale errored on the worker")
        _t.sleep(2)
    raise VNCCSError("reference upscale timed out on the worker")


def _pil_enhance_bytes(data: bytes, sharpen: str = "off", max_side: int = 2048,
                       lanczos_factor: float = 1.0) -> bytes:
    """App-side post-process: optional Lanczos pre-scale (fallback when no GAN
    model), cap the longest side to ``max_side``, and apply an UnsharpMask by
    level.  Returns PNG bytes.  Sync — run in a thread."""
    import io as _io
    from PIL import Image as _Image, ImageFilter as _ImageFilter
    im = _Image.open(_io.BytesIO(data))
    if im.mode not in ("RGB", "RGBA"):
        im = im.convert("RGB")
    if lanczos_factor and lanczos_factor != 1.0:
        im = im.resize((max(1, int(im.width * lanczos_factor)),
                        max(1, int(im.height * lanczos_factor))), _Image.LANCZOS)
    long_side = max(im.width, im.height)
    cap = max(256, min(4096, int(max_side or 2048)))
    if long_side > cap:
        s = cap / float(long_side)
        im = im.resize((max(1, int(im.width * s)), max(1, int(im.height * s))), _Image.LANCZOS)
    lvl = str(sharpen or "off").strip().lower()
    # Halo-safe UnsharpMask: small radius (thin halos), modest percent (less edge
    # amplification) and a HIGHER threshold so flat skin isn't touched — the old
    # aggressive presets (percent 80-160, threshold 2-3) drew dark ink-like lines
    # under the chin / armpits / clothing seams and made realistic renders look drawn.
    _params = {"light": (0.7, 35, 4), "medium": (1.0, 55, 5), "strong": (1.3, 85, 6)}
    if lvl in _params:
        r, pct, thr = _params[lvl]
        im = im.filter(_ImageFilter.UnsharpMask(radius=r, percent=pct, threshold=thr))
    buf = _io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


@router.get("/upscale-models")
async def upscale_models(request: Request, session: AsyncSession = Depends(get_session)):
    """The pinned host's GAN upscale models (UpscaleModelLoader) — powers the
    reference-enhance model picker."""
    host = await _need_host(request, session)
    from backend.services.character_studio.vnccs_native.klein_poses import _options as _ks_options
    try:
        oi = await asyncio.to_thread(_object_info, host)
    except VNCCSError as e:
        raise HTTPException(status_code=502, detail=str(e))
    return {"models": list(_ks_options(oi, "UpscaleModelLoader", "model_name") or [])}


class RefEnhanceIn(BaseModel):
    ref: dict                              # {name, subfolder, type} of an uploaded reference
    host: Optional[str] = None             # worker URL to run on (else the pinned host)
    method: Optional[str] = "gan"          # 'gan' (UpscaleModelLoader) | 'seedvr2'
    model: Optional[str] = None            # GAN model name, or '' / 'auto' for best 4x
    sharpen: Optional[str] = "off"         # off | light | medium | strong
    max_side: int = 2048                   # target longest side / SeedVR2 resolution (px)


@router.post("/reference/enhance")
async def reference_enhance(body: RefEnhanceIn, request: Request,
                            session: AsyncSession = Depends(get_session)):
    """AI-enhance ONE reference image on a ComfyUI worker, then re-upload the
    result to that worker's input folder.  ALL heavy work runs on the worker GPU
    (GAN UpscaleModelLoader or the SeedVR2 node pack); the app host only does a
    trivial single-image sharpen/size-cap in Pillow.

    The reference was uploaded to the PINNED host; when a fan-out targets a
    DIFFERENT worker, the file isn't in that worker's input folder (LoadImage ->
    "Invalid image file"), so we always copy the reference onto the target
    worker first, re-encoded to a clean PNG.  Returns
    ``{name, subfolder, type, method, width, height, host}``."""
    from pathlib import Path as _Path
    pinned = await _need_host(request, session)          # where the ref was uploaded
    target = (str(body.host or "").rstrip("/")) or pinned  # worker to run on
    ref = body.ref or {}
    name = str(ref.get("name") or "").strip()
    sub = str(ref.get("subfolder") or "")
    typ = str(ref.get("type") or "input") or "input"
    if not name:
        raise HTTPException(status_code=400, detail="no reference image name")
    method = str(body.method or "gan").strip().lower()
    from backend.services.character_studio.vnccs_native.klein_poses import resolve_upscale_model

    try:
        oi = await asyncio.to_thread(_object_info, target)
    except VNCCSError as e:
        raise HTTPException(status_code=502, detail=str(e))

    max_side = max(256, min(4096, int(body.max_side or 2048)))

    def _dims(data: bytes):
        try:
            import io as _io
            from PIL import Image as _Image
            with _Image.open(_io.BytesIO(data)) as im:
                return int(im.width), int(im.height)
        except Exception:  # noqa: BLE001
            return 0, 0

    def _reencode_png(data: bytes) -> bytes:
        import io as _io
        from PIL import Image as _Image
        im = _Image.open(_io.BytesIO(data))
        if im.mode not in ("RGB", "RGBA"):
            im = im.convert("RGB")
        buf = _io.BytesIO()
        im.save(buf, format="PNG")
        return buf.getvalue()

    def _run_seedvr2(run_host: str, image_name: str):
        import json as _json
        wf_path = _Path(__file__).resolve().parents[2] / "workflows" / "STUDIO_SEEDVR2.json"
        if not wf_path.exists():
            raise VNCCSError("STUDIO_SEEDVR2.json not found in workflows/")
        graph = _json.loads(wf_path.read_text(encoding="utf-8"))
        for _nid, node in graph.items():
            t = (node.get("_meta") or {}).get("title")
            if t == "STUDIO SEEDVR INPUT":
                node.setdefault("inputs", {})["image"] = image_name
            elif t == "STUDIO SEEDVR UPSCALER":
                node.setdefault("inputs", {})["resolution"] = max_side
        client = _client(run_host, timeout=300)
        res = client.submit_prompt(graph, timeout=300)
        raw = _wait_first_image_bytes(client, res.get("prompt_id"), 1800)
        return _pil_enhance_bytes(raw, sharpen=body.sharpen, max_side=max_side, lanczos_factor=1.0), "seedvr2"

    def _run_gan(run_host: str, image_name: str):
        settings = ({"klein_upscale_model": str(body.model).strip()}
                    if (body.model and str(body.model).strip().lower() != "auto") else {})
        model = resolve_upscale_model(oi, settings)
        client = _client(run_host, timeout=180)
        if model:
            graph = {
                "load": {"class_type": "LoadImage", "inputs": {"image": image_name}},
                "loader": {"class_type": "UpscaleModelLoader", "inputs": {"model_name": model}},
                "up": {"class_type": "ImageUpscaleWithModel",
                       "inputs": {"upscale_model": ["loader", 0], "image": ["load", 0]}},
                "save": {"class_type": "SaveImage",
                         "inputs": {"images": ["up", 0], "filename_prefix": "rbmn_refup"}},
            }
            res = client.submit_prompt(graph, timeout=180)
            raw = _wait_first_image_bytes(client, res.get("prompt_id"), 600)
            return _pil_enhance_bytes(raw, sharpen=body.sharpen, max_side=max_side, lanczos_factor=1.0), str(model)
        # no GAN model on the worker — honest app-side Lanczos 2x fallback
        raw = client.view_image(image_name, "", "input", 120)
        return (_pil_enhance_bytes(raw, sharpen=body.sharpen, max_side=max_side, lanczos_factor=2.0),
                "lanczos-2x (no GAN model on host)")

    def _run():
        import uuid as _uuid
        # 1) copy the reference onto the TARGET worker as a clean PNG so LoadImage
        #    can always find + decode it (fixes fan-out "Invalid image file")
        raw_in = _client(pinned, timeout=120).view_image(name, sub, typ, 120)
        png_in = _reencode_png(raw_in)
        in_name = f"rbmn_refin_{_uuid.uuid4().hex[:8]}.png"
        _client(target, timeout=180).upload_image(in_name, png_in, "", True, 120)
        # 2) enhance on the target worker
        out, used = (_run_seedvr2(target, in_name) if method == "seedvr2"
                     else _run_gan(target, in_name))
        w, h = _dims(out)
        safe = "".join(ch for ch in name if ch.isalnum() or ch in "._-")[-40:] or "ref.png"
        newname = f"rbmn_enh_{_uuid.uuid4().hex[:8]}_{safe}"
        if not newname.lower().endswith(".png"):
            newname += ".png"
        # Consolidate the RESULT onto the pinned host so display (/view
        # defaults to pinned) and the clone run's ref replication both find
        # it, no matter which worker did the upscale.
        upd = _client(pinned, timeout=120).upload_image(newname, out, "", True, 120)
        return {"name": upd.get("name", newname),
                "subfolder": upd.get("subfolder", ""),
                "type": upd.get("type", "input"),
                "method": used, "width": w, "height": h, "host": pinned, "worker": target}

    try:
        return await asyncio.to_thread(_run)
    except VNCCSError as e:
        raise HTTPException(status_code=502, detail=str(e))
    except Exception as e:  # noqa: BLE001
        logger.exception("reference enhance failed")
        raise HTTPException(status_code=500, detail=f"enhance failed: {e}")



def _enhance_image_bytes(target_host: str, oi: dict, image_bytes: bytes,
                         method: str = "gan", model: Optional[str] = None,
                         sharpen: str = "off", max_side: int = 2048):
    """Upscale ONE image (raw bytes) on a worker (GAN or SeedVR2) + app-side
    sharpen/size-cap.  Uploads the image to ``target_host`` as a clean PNG first
    so LoadImage always finds a valid file.  Returns ``(png_bytes, method_used)``.
    Sync — run in a thread.  Shared by the base-image enhancer."""
    import io as _io
    import json as _json
    import uuid as _uuid
    from pathlib import Path as _Path
    from PIL import Image as _Image
    from backend.services.character_studio.vnccs_native.klein_poses import resolve_upscale_model
    ms = max(256, min(4096, int(max_side or 2048)))
    im = _Image.open(_io.BytesIO(image_bytes))
    if im.mode not in ("RGB", "RGBA"):
        im = im.convert("RGB")
    _b = _io.BytesIO()
    im.save(_b, format="PNG")
    in_name = f"rbmn_baseenh_{_uuid.uuid4().hex[:8]}.png"
    _client(target_host, timeout=180).upload_image(in_name, _b.getvalue(), "", True, 120)
    m = str(method or "gan").strip().lower()
    if m == "seedvr2":
        wf_path = _Path(__file__).resolve().parents[2] / "workflows" / "STUDIO_SEEDVR2.json"
        if not wf_path.exists():
            raise VNCCSError("STUDIO_SEEDVR2.json not found in workflows/")
        graph = _json.loads(wf_path.read_text(encoding="utf-8"))
        for _nid, node in graph.items():
            t = (node.get("_meta") or {}).get("title")
            if t == "STUDIO SEEDVR INPUT":
                node.setdefault("inputs", {})["image"] = in_name
            elif t == "STUDIO SEEDVR UPSCALER":
                node.setdefault("inputs", {})["resolution"] = ms
        client = _client(target_host, timeout=300)
        res = client.submit_prompt(graph, timeout=300)
        raw = _wait_first_image_bytes(client, res.get("prompt_id"), 1800)
        return _pil_enhance_bytes(raw, sharpen=sharpen, max_side=ms, lanczos_factor=1.0), "seedvr2"
    settings = ({"klein_upscale_model": str(model).strip()}
                if (model and str(model).strip().lower() != "auto") else {})
    gm = resolve_upscale_model(oi, settings)
    client = _client(target_host, timeout=180)
    if gm:
        graph = {
            "load": {"class_type": "LoadImage", "inputs": {"image": in_name}},
            "loader": {"class_type": "UpscaleModelLoader", "inputs": {"model_name": gm}},
            "up": {"class_type": "ImageUpscaleWithModel",
                   "inputs": {"upscale_model": ["loader", 0], "image": ["load", 0]}},
            "save": {"class_type": "SaveImage",
                     "inputs": {"images": ["up", 0], "filename_prefix": "rbmn_baseup"}},
        }
        res = client.submit_prompt(graph, timeout=180)
        raw = _wait_first_image_bytes(client, res.get("prompt_id"), 600)
        return _pil_enhance_bytes(raw, sharpen=sharpen, max_side=ms, lanczos_factor=1.0), str(gm)
    raw = client.view_image(in_name, "", "input", 120)
    return (_pil_enhance_bytes(raw, sharpen=sharpen, max_side=ms, lanczos_factor=2.0),
            "lanczos-2x (no GAN model on host)")


class BaseEnhanceIn(BaseModel):
    character_name: str
    method: Optional[str] = "gan"          # 'gan' | 'seedvr2'
    model: Optional[str] = None            # GAN model or '' / 'auto'
    sharpen: Optional[str] = "off"
    max_side: int = 2048


@router.post("/base/enhance")
async def base_enhance(body: BaseEnhanceIn, request: Request,
                       session: AsyncSession = Depends(get_session)):
    """AI-upscale the character's ACTIVE base render (all 4 views if it's a
    set) on a worker, then save the result as a NEW base version — which becomes
    the ACTIVE base, so every lock-base pose run automatically uses the sharper
    version.  Returns the new version.  Original stays as a prior version to
    compare/revert via the version arrows."""
    import base64 as _b64
    from uuid import UUID as _UUID
    from pathlib import Path as _Path
    from sqlmodel import select as _select
    from backend.config import settings as _cfg
    from backend.database.models import Asset, StudioCharacter
    from backend.services.character_studio.vnccs_native.ingest import save_base_preview

    host = await _need_host(request, session)
    name = body.character_name.strip()
    char = (await session.execute(
        _select(StudioCharacter).where(StudioCharacter.name == name))).scalars().first()
    if char is None:
        raise HTTPException(status_code=404, detail=f"character {name!r} not found")
    v = (char.manifest or {}).get("vnccs") or {}
    active = v.get("active_base")
    bv = next((b for b in (v.get("base_versions") or [])
               if isinstance(b, dict) and b.get("id") == active), None)
    if not bv:
        raise HTTPException(status_code=409,
                            detail="No active base version to upscale — generate a base preview first.")

    async def _abytes(aid) -> Optional[bytes]:
        try:
            a = await session.get(Asset, _UUID(str(aid)))
        except Exception:  # noqa: BLE001
            return None
        if a is None:
            return None
        rel = str(a.rel_path).replace("\\", "/")
        pid = str(a.project_id)
        p = (_Path(_cfg.project_dir) / rel if rel.startswith(pid + "/")
             else _Path(_cfg.project_dir) / pid / rel)
        try:
            return p.read_bytes() if p.exists() else None
        except Exception:  # noqa: BLE001
            return None

    view_items: list = []  # (view_label, bytes)
    for vw in (bv.get("views") or []):
        data = await _abytes(vw.get("asset_id"))
        if data:
            view_items.append((str(vw.get("view") or "front"), data))
    if not view_items:
        data = await _abytes(bv.get("asset_id"))
        if data:
            view_items.append(("front", data))
    if not view_items:
        raise HTTPException(status_code=409, detail="Base version has no readable image asset.")

    try:
        oi = await asyncio.to_thread(_object_info, host)
    except VNCCSError as e:
        raise HTTPException(status_code=502, detail=str(e))

    def _run():
        outs = []  # (view, b64, method)
        for vlabel, data in view_items:
            ob, used = _enhance_image_bytes(host, oi, data, method=body.method,
                                            model=body.model, sharpen=body.sharpen,
                                            max_side=body.max_side)
            outs.append((vlabel, _b64.b64encode(ob).decode("ascii"), used))
        return outs

    try:
        outs = await asyncio.to_thread(_run)
    except VNCCSError as e:
        raise HTTPException(status_code=502, detail=str(e))
    except Exception as e:  # noqa: BLE001
        logger.exception("base enhance failed")
        raise HTTPException(status_code=500, detail=f"base enhance failed: {e}")

    used_method = outs[0][2] if outs else "?"
    if len(outs) > 1:
        version = await save_base_preview(
            session, character_name=name,
            views=[{"view": vl, "image_b64": b} for vl, b, _u in outs], variant="klein")
    else:
        version = await save_base_preview(
            session, character_name=name, image_b64=outs[0][1], variant="klein")
    return {"version": version, "method": used_method, "views": len(outs)}


class BaseRestyleIn(BaseModel):
    character_name: str
    style: Optional[str] = "photorealistic"   # Output-style key or 'custom'
    style_custom: Optional[str] = None         # free text when style == 'custom'
    style_ref: Optional[dict] = None           # {name,subfolder,type} style image on the host
    strength: float = 0.7                      # 0..1 content preservation (higher = keep more)


@router.post("/base/restyle")
async def base_restyle(body: BaseRestyleIn, request: Request,
                       session: AsyncSession = Depends(get_session)):
    """Switch Style: restyle the character's ACTIVE base render into a new art
    style via a Klein reference-EDIT (the base rides as a reference, a full
    generation follows the restyle prompt), then save the result as a NEW base
    version that becomes ACTIVE — so lock-base pose runs inherit the new style.
    The original stays a prior version to compare/revert via the version arrows."""
    import base64 as _b64
    import io as _io
    import random as _random
    import uuid as _uuid
    from uuid import UUID as _UUID
    from pathlib import Path as _Path
    from PIL import Image as _Image
    from sqlmodel import select as _select
    from backend.config import settings as _cfg
    from backend.database.models import Asset, StudioCharacter
    from backend.services.character_studio.vnccs_native import klein_poses
    from backend.services.character_studio.vnccs_native.ingest import save_base_preview

    host = await _need_host(request, session)
    st = await _settings(session)
    saved = (st.studio_vnccs_settings if st else None) or {}
    name = body.character_name.strip()
    char = (await session.execute(
        _select(StudioCharacter).where(StudioCharacter.name == name))).scalars().first()
    if char is None:
        raise HTTPException(status_code=404, detail=f"character {name!r} not found")
    v = (char.manifest or {}).get("vnccs") or {}
    active = v.get("active_base")
    bv = next((b for b in (v.get("base_versions") or [])
               if isinstance(b, dict) and b.get("id") == active), None)
    if not bv:
        raise HTTPException(status_code=409,
                            detail="No active base version to restyle — generate a base preview first.")

    async def _abytes(aid) -> Optional[bytes]:
        try:
            a = await session.get(Asset, _UUID(str(aid)))
        except Exception:  # noqa: BLE001
            return None
        if a is None:
            return None
        rel = str(a.rel_path).replace("\\", "/")
        pid = str(a.project_id)
        p = (_Path(_cfg.project_dir) / rel if rel.startswith(pid + "/")
             else _Path(_cfg.project_dir) / pid / rel)
        try:
            return p.read_bytes() if p.exists() else None
        except Exception:  # noqa: BLE001
            return None

    view_items: list = []  # (view_label, bytes)
    for vw in (bv.get("views") or []):
        data = await _abytes(vw.get("asset_id"))
        if data:
            view_items.append((str(vw.get("view") or "front"), data))
    if not view_items:
        data = await _abytes(bv.get("asset_id"))
        if data:
            view_items.append(("front", data))
    if not view_items:
        raise HTTPException(status_code=409, detail="Base version has no readable image asset.")

    try:
        oi = await asyncio.to_thread(_object_info, host)
        models = klein_poses.resolve_klein_models(oi, saved, require_lora=False)
    except VNCCSError as e:
        raise HTTPException(status_code=502, detail=str(e))
    reflatentplus = klein_poses.resolve_reflatentplus(oi, saved)
    rmbg_cfg = klein_poses.resolve_rmbg(oi, saved)
    steps = klein_poses.resolve_klein_steps(saved)
    style = str(body.style or "photorealistic").strip().lower()
    style_custom = str(body.style_custom or "").strip()
    has_ref = bool(body.style_ref and (body.style_ref or {}).get("name"))
    prompt = klein_poses.klein_restyle_prompt(style, style_custom, has_style_ref=has_ref)
    strength = max(0.05, min(2.0, float(body.strength or 0.7)))
    seed = _random.randint(1, 2_000_000_000)
    safe = "".join(ch for ch in name if ch.isalnum())[:24] or "char"

    def _clean_png(data: bytes) -> bytes:
        im = _Image.open(_io.BytesIO(data))
        if im.mode not in ("RGB", "RGBA"):
            im = im.convert("RGB")
        buf = _io.BytesIO()
        im.save(buf, format="PNG")
        return buf.getvalue()

    def _run():
        client = _client(host, timeout=120)
        token = _uuid.uuid4().hex[:8]
        # style reference (optional): re-upload a clean PNG copy onto this host
        style_name = None
        if has_ref:
            sr = body.style_ref or {}
            try:
                raw = _client(host, timeout=120).view_image(
                    sr.get("name", ""), sr.get("subfolder", "") or "",
                    sr.get("type", "input") or "input", 120)
                sn = f"rbmn_klein_{safe}_{token}_styleref.png"
                _client(host, timeout=120).upload_image(sn, _clean_png(raw), "", True, 120)
                style_name = sn
            except VNCCSError:
                style_name = None
        outs = []  # (view, b64)
        for i, (vlabel, data) in enumerate(view_items):
            in_name = f"rbmn_klein_{safe}_{token}_rsin{i}.png"
            client.upload_image(in_name, _clean_png(data), "", True, 120)
            graph, _tap = klein_poses.build_klein_restyle_graph(
                base_file=in_name, prompt=prompt, seed=seed, models=models, steps=steps,
                style_ref_file=style_name, strength=strength, reflatentplus=reflatentplus,
                rmbg=rmbg_cfg,  # strip the background as the last step
                filename_prefix=f"rbmn_vnccs/{safe}/klein_restyle")
            res = client.submit_prompt(graph, timeout=120)
            raw = _wait_first_image_bytes(client, res.get("prompt_id"), 1800)
            outs.append((vlabel, _b64.b64encode(raw).decode("ascii")))
        return outs

    try:
        outs = await asyncio.to_thread(_run)
    except VNCCSError as e:
        raise HTTPException(status_code=502, detail=str(e))
    except Exception as e:  # noqa: BLE001
        logger.exception("base restyle failed")
        raise HTTPException(status_code=500, detail=f"base restyle failed: {e}")

    if not outs:
        raise HTTPException(status_code=502, detail="Restyle produced no image.")
    label = style_custom if (style == "custom" and style_custom) else style
    if len(outs) > 1:
        version = await save_base_preview(
            session, character_name=name,
            views=[{"view": vl, "image_b64": b} for vl, b in outs], variant="klein")
    else:
        version = await save_base_preview(
            session, character_name=name, image_b64=outs[0][1], variant="klein")
    return {"version": version, "style": label, "views": len(outs), "host": host}


@router.get("/hosts")
async def vnccs_hosts(request: Request, session: AsyncSession = Depends(get_session)):
    """All reachable VNCCS-capable workers (pinned first) — powers parallel fan-out."""
    _, st = await _resolve_host(request, session)
    comfy = getattr(request.app.state, "comfy_dispatcher", None)
    configured = (st.studio_vnccs_host or None) if st else None
    return {"hosts": list_vnccs_hosts(comfy, configured)}


@router.get("/klein-status")
async def klein_status(request: Request, session: AsyncSession = Depends(get_session)):
    """Klein Hybrid face-consistency readiness — the v1.77.x verification
    endpoint.  Open it in a browser (GET /api/studio/vnccs/klein-status) to see,
    per worker: Klein models, the pose LoRA, and whether PuLID-Flux2 will
    engage (which weight file, strength, provider) or why not.  Also reports
    the app-side face detector and the effective klein_pulid* settings."""
    from backend.services.character_studio.vnccs_native import klein_poses
    _, st = await _resolve_host(request, session)
    saved = (st.studio_vnccs_settings if st else None) or {}
    comfy = getattr(request.app.state, "comfy_dispatcher", None)
    configured = (st.studio_vnccs_host or None) if st else None
    hosts = list_vnccs_hosts(comfy, configured)

    def _probe(h: str) -> dict:
        try:
            oi = _object_info(h)
        except Exception as e:  # noqa: BLE001
            return {"host": h, "online": False, "error": str(e)}
        entry: dict = {"host": h, "online": True}
        try:
            models = klein_poses.resolve_klein_models(oi, saved, require_lora=False)
            entry["klein_models"] = models
            entry["pose_lora"] = bool(models.get("lora"))
        except ValueError as e:
            entry["klein_models_error"] = str(e)
        refine = klein_poses.resolve_face_refine(oi, saved)
        if refine:
            entry["face_refine"] = {"active": True, "detector": refine["detector"],
                                    "denoise": refine["denoise"], "steps": refine["steps"]}
        else:
            fr_mode = str(saved.get("klein_face_refine") or "auto").strip().lower()
            entry["face_refine"] = {"active": False, "reason": (
                "disabled via the klein_face_refine setting"
                if fr_mode in ("off", "false", "0", "disabled", "none")
                else "FaceDetailer (Impact-Pack) or a face yolo detector model "
                     "is missing on this worker")}
        pulid = klein_poses.resolve_pulid(oi, saved)
        if pulid:
            entry["pulid"] = {"active": True, **pulid}
        else:
            mode = str(saved.get("klein_pulid") or "auto").strip().lower()
            if mode in ("off", "false", "0", "disabled", "none"):
                reason = "disabled via the klein_pulid setting"
            elif klein_poses.PULID_APPLY_CLASS not in (oi or {}):
                reason = "PuLID-Flux2 node pack not installed on this worker"
            else:
                reason = "no weight files found in the worker's models/pulid folder"
            entry["pulid"] = {"active": False, "reason": reason}
        return entry

    results = await asyncio.gather(*[asyncio.to_thread(_probe, h) for h in hosts])

    # app-side face detection (drives the face-crop reference + PuLID gating)
    face_detect: dict = {}
    try:
        from backend.services.character_studio import faces as _faces
        face_detect = {
            "cv2": bool(getattr(_faces, "_HAVE_CV2", False)),
            "yunet_model": bool(_faces._YUNET_MODEL_PATH.exists()),
            "note": ("YuNet ready" if _faces._YUNET_MODEL_PATH.exists()
                     else "YuNet auto-downloads on first use; Haar fallback until then"),
        }
    except Exception as e:  # noqa: BLE001
        face_detect = {"error": str(e)}

    app_version = None
    try:
        from pathlib import Path as _Path
        app_version = (_Path(__file__).resolve().parents[2] / "VERSION").read_text(
            encoding="utf-8").strip()
    except Exception:  # noqa: BLE001
        pass

    return {
        "app_version": app_version,
        "face_consistency_since": "1.77.0",
        "workers": list(results),
        "face_detect": face_detect,
        "effective_settings": {
            "klein_pulid": saved.get("klein_pulid", "auto"),
            "klein_pulid_file": saved.get("klein_pulid_file") or "(auto: klein > 9b > newest version)",
            "klein_pulid_strength": saved.get("klein_pulid_strength", klein_poses.PULID_DEFAULT_STRENGTH),
            "klein_pulid_provider": saved.get("klein_pulid_provider", "CPU"),
        },
    }


async def _character_hosts(session, name: str) -> list:
    """Hosts recorded as holding this character's sprites (shard registry)."""
    from backend.database.models import StudioCharacter
    rows = (await session.execute(select(StudioCharacter).where(StudioCharacter.name == name))).scalars().all()
    for c in rows:
        v = (c.manifest or {}).get("vnccs") or {}
        hosts = v.get("hosts")
        if isinstance(hosts, list) and hosts:
            return [str(h).rstrip("/") for h in hosts]
    return []


async def _record_character_hosts(session, name: str, hosts: list) -> None:
    from backend.services.character_studio.vnccs_native.ingest import _find_or_create_character
    char = await _find_or_create_character(session, name, None)
    manifest = dict(char.manifest or {})
    vnccs = dict(manifest.get("vnccs") or {})
    existing = [str(h).rstrip("/") for h in (vnccs.get("hosts") or [])]
    for h in hosts:
        h = str(h).rstrip("/")
        if h and h not in existing:
            existing.append(h)
    vnccs["hosts"] = existing
    vnccs.setdefault("ref", name)
    manifest["vnccs"] = vnccs
    char.manifest = manifest
    await session.commit()


class ParallelGenerateIn(GenerateIn):
    max_hosts: int = 0          # 0 = use every eligible worker; 1 = pinned only


async def _klein_emotions_parallel(session, body: GenerateIn, saved: dict,
                                   eligible: list, pinned: str, gen_settings: dict):
    """Klein face-inpaint emotions: for each (cataloged sprite x emotion),
    build a masked-face RGBA app-side (YuNet/Haar + anime fallback) and run the
    KLEIN_INPAINT recipe on a worker.  Pairs fan out across workers."""
    import tempfile
    from pathlib import Path as _Path
    from uuid import UUID as _UUID
    from sqlmodel import select as _select
    from backend.config import settings as _cfg
    from backend.database.models import Asset, StudioCharacter
    from backend.services.character_studio.faces import build_face_masked_rgba
    from backend.services.character_studio.vnccs_native import klein_poses

    emotions = [str(e) for e in (body.emotions or []) if e]
    if not emotions:
        raise HTTPException(status_code=400, detail="Select at least one emotion")
    name = body.character_name.strip()
    char = (await session.execute(
        _select(StudioCharacter).where(StudioCharacter.name == name))).scalars().first()
    if char is None:
        raise HTTPException(status_code=404, detail=f"character {name!r} not in the catalog")
    v = (char.manifest or {}).get("vnccs") or {}
    outputs = v.get("outputs") or {}
    sprite_paths: list = []
    for label in ("creator/sprites", "cloner/sprites", "creator/sheet", "cloner/original_sprites"):
        for aid in (outputs.get(label) or []):
            try:
                a = await session.get(Asset, _UUID(str(aid)))
            except Exception:  # noqa: BLE001
                a = None
            if a is None:
                continue
            rel = str(a.rel_path).replace("\\", "/")
            pid = str(a.project_id)
            p = (_Path(_cfg.project_dir) / rel if rel.startswith(pid + "/")
                 else _Path(_cfg.project_dir) / pid / rel)
            if p.exists():
                sprite_paths.append(p)
        if sprite_paths:
            break
    if not sprite_paths:
        raise HTTPException(status_code=409,
                            detail="No cataloged sprites for this character — generate poses first")
    sprite_paths = sprite_paths[:12]

    # emotion prompt catalog from the pinned host (safe_name -> natural prompt)
    emo_prompts: dict = {}
    try:
        data = await asyncio.to_thread(_client(pinned, timeout=30).get_json, "get_emotions")
        for lst in (data or {}).values():
            for e in (lst or []):
                if isinstance(e, dict) and e.get("safe_name"):
                    emo_prompts[str(e["safe_name"])] = {
                        "key": str(e.get("key") or e["safe_name"]),
                        "natural": str(e.get("natural_prompt") or ""),
                    }
    except Exception:  # noqa: BLE001
        logger.warning("klein emotions: get_emotions unavailable — using bare emotion names")

    # masked RGBA per sprite (app-side face detect; skip faces we can't find),
    # plus the context box the worker crops/repaints (v1.77.0 crop-and-stitch —
    # the face is sampled at ~1MP instead of at tiny sprite scale)
    tmpdir = _Path(tempfile.mkdtemp(prefix="rbmn_klein_emo_"))
    masked: dict = {}
    crops: dict = {}
    skipped = 0
    for i, sp in enumerate(sprite_paths):
        outp = tmpdir / f"masked_{i}.png"
        bbox = await asyncio.to_thread(build_face_masked_rgba, sp, outp)
        if bbox and outp.exists():
            try:
                from PIL import Image as _PILImage
                with _PILImage.open(sp) as _im:
                    _w, _h = _im.size
                crops[i] = _context_crop_box(bbox, _w, _h)
                masked[i] = outp
            except Exception:  # noqa: BLE001
                logger.warning("klein emotions: could not size %s — skipping", sp)
                skipped += 1
        else:
            skipped += 1
    if not masked:
        raise HTTPException(status_code=409,
                            detail="No faces detected in any sprite — cannot run Klein emotion inpaint")

    # canonical identity anchor: face crop of the ACTIVE base version — the old
    # recipe anchored each edit to the (already-drifted) sprite itself, so
    # expression runs compounded pose drift.  Falls back to the first sprite
    # with a detectable face, then to the raw base image.
    id_face_bytes = None
    ident_full = None
    try:
        ident = await _klein_identity_bytes(session, body, pinned)
        if ident:
            ident_full = ident[0]
            id_face_bytes = await asyncio.to_thread(_face_crop_bytes, ident_full)
    except HTTPException:
        pass
    if id_face_bytes is None:
        for i in sorted(masked.keys()):
            id_face_bytes = await asyncio.to_thread(
                _face_crop_bytes, sprite_paths[i].read_bytes())
            if id_face_bytes:
                break
    id_face_is_crop = id_face_bytes is not None
    if id_face_bytes is None and ident_full:
        id_face_bytes = ident_full   # last resort: full base image as the anchor

    pairs = [(i, emo) for i in sorted(masked.keys()) for emo in emotions]
    n = min(len(eligible), len(pairs)) or 1
    buckets: list = [[] for _ in range(n)]
    for k, pr in enumerate(pairs):
        buckets[k % n].append(pr)
    kseed = int(gen_settings.get("seed") or 1)
    safe = "".join(ch for ch in name if ch.isalnum())[:24] or "char"

    def _submit_host(host: str, subset: list):
        import uuid as _uuid
        token = _uuid.uuid4().hex[:8]      # unique names — shared input folders
        oi = _object_info(host)
        models = klein_poses.resolve_klein_models(oi, saved, require_lora=False)
        # gate PuLID on a REAL detected face crop (see the pose-path note)
        pulid = klein_poses.resolve_pulid(oi, saved) if id_face_is_crop else None
        client = _client(host, timeout=120)
        id_face_name = None
        if id_face_bytes:
            up = client.upload_image(f"rbmn_kem_{safe}_{token}_idface.png",
                                     id_face_bytes, "", True, 120)
            id_face_name = up.get("name", f"rbmn_kem_{safe}_{token}_idface.png")
        if pulid and id_face_name:
            logger.info("klein emotions: PuLID-Flux2 active (%s, strength %.2f)",
                        pulid["file"], pulid["strength"])
        uploaded: dict = {}
        gpairs = []
        gprompts = []
        for j, (si, emo) in enumerate(subset):
            if si not in uploaded:
                msk = client.upload_image(f"rbmn_kem_{safe}_{token}_m{si}.png",
                                          masked[si].read_bytes(), "", True, 120)
                uploaded[si] = msk.get("name")
            mnm = uploaded[si]
            info = emo_prompts.get(emo) or {"key": emo, "natural": ""}
            gpairs.append({"masked": mnm, "crop": crops[si]})
            prompt = (f"Change the character's facial expression to {info['key']}. "
                      f"{info['natural']}").strip() + " "
            if id_face_name:
                prompt += ("Image 2 is a close-up of this same character's face: the "
                           "result must stay the exact same person as image 2 — "
                           "identical facial features, eye color, hairstyle and art "
                           "style. Change only the expression.")
            else:
                prompt += ("Modify only the face; keep the identity, hairstyle, "
                           "colors and art style exactly the same.")
            gprompts.append(prompt)
        graph, tap_map = klein_poses.build_klein_emotion_graph(
            pairs=gpairs, prompts=gprompts, seed=kseed, models=models,
            id_face=id_face_name, pulid=pulid,
            filename_prefix=f"rbmn_vnccs/{safe}/klein_emotions")
        res = client.submit_prompt(graph, timeout=120)
        extras = {"face_anchor": bool(id_face_name),
                  "pulid_file": (pulid or {}).get("file"),
                  "pulid_strength": (pulid or {}).get("strength")}
        return res.get("prompt_id"), tap_map, extras

    out = []
    errors = []
    for host, subset in zip(eligible[:n], buckets):
        if not subset:
            continue
        try:
            prompt_id, tap_map, kextras = await asyncio.to_thread(_submit_host, host, subset)
            out.append({"prompt_id": prompt_id, "host": host, "tap_map": tap_map,
                        "label": f"{len(subset)} face(s) · Klein emotions" + _klein_run_suffix(kextras),
                        "pose_count": len(subset),
                        "pose_names": [f"{emo}@s{si}" for si, emo in subset]})
        except Exception as e:  # noqa: BLE001
            logger.warning(f"klein emotions: chunk on {host} failed: {e}")
            errors.append({"host": host, "error": str(e)})
    if not out:
        raise HTTPException(status_code=502, detail=f"All Klein emotion chunks failed: {errors}")
    if skipped:
        errors.append({"host": "(app)", "error": f"{skipped} sprite(s) skipped — no face detected"})
    return {"step": "emotions", "chunks": out, "errors": errors,
            "seed": gen_settings.get("seed"), "engine": "klein"}


@router.post("/generate-parallel/{step}")
async def generate_parallel(step: str, body: ParallelGenerateIn, request: Request,
                            session: AsyncSession = Depends(get_session)):
    """Fan a VNCCS step out across multiple vnccs-capable workers.

    Sharding rules (VNCCS stores sprites on the LOCAL disk of whichever worker
    runs the graph, so placement matters):
      - creator/cloner: split the pose_set round-robin across ALL eligible
        workers; every worker that runs a chunk is recorded on the character
        (manifest["vnccs"]["hosts"]) as holding part of its sprites.
      - clothes: chunks may only go to recorded hosts (each has the character's
        base sprite locally); the selected poses are split across them.
      - emotions: the SAME request is submitted to every recorded host — each
        worker FaceDetails only the costume sprites on its own disk, so the
        work splits naturally without pose bookkeeping.
    Returns {chunks: [{prompt_id, host, tap_map, label, pose_count}]} — poll
    each via GET /result/{prompt_id}?host=… and ingest each with its host.
    """
    if step not in STEP_FILES:
        raise HTTPException(status_code=404, detail=f"unknown step {step!r}")
    pinned = await _need_host(request, session)
    _, st = await _resolve_host(request, session)
    comfy = getattr(request.app.state, "comfy_dispatcher", None)
    configured = (st.studio_vnccs_host or None) if st else None
    all_hosts = list_vnccs_hosts(comfy, configured) or [pinned]
    if pinned not in all_hosts:
        all_hosts.insert(0, pinned)

    saved = (st.studio_vnccs_settings if st else None) or {}
    # pose runs post an empty character_info — refill from the saved character so
    # the mannequin build + body prompt text apply (else the default body renders).
    await _enrich_character_info(session, body)
    control_center = body.control_center or saved.get("control_center")
    # rolled ONCE per request — every chunk of this run shares the same seed,
    # like a single-worker run in the node UI
    gen_settings = _roll_seed(saved.get("gen_settings"), body.gen_settings)

    # eligible hosts per sharding rules
    if step in ("clothes", "emotions"):
        recorded = await _character_hosts(session, body.character_name.strip())
        eligible = [h for h in recorded if h in all_hosts] or [pinned]
    else:
        eligible = list(all_hosts)
    if body.max_hosts and body.max_hosts > 0:
        eligible = eligible[:body.max_hosts]

    # chunk assignment — pose display names travel with each chunk so ingest
    # can tag every image with ITS pose (regeneration replaces by pose name)
    all_names = [str(x) for x in (body.pose_names or [])]
    chunks: list = []          # (host, pose_subset_or_None, label, names_or_None)
    if step == "emotions":
        for h in eligible:
            chunks.append((h, None, f"{len(body.costumes or ['Original'])} set(s) × {len(body.emotions or [])} emotions", None))
    else:
        poses = body.pose_set or []
        names_ok = len(all_names) == len(poses)
        if len(eligible) <= 1 or len(poses) <= 1:
            chunks.append((eligible[0], poses or None, f"{len(poses) or 'default'} poses",
                           all_names if (poses and names_ok) else None))
        else:
            n = min(len(eligible), len(poses))
            buckets: list = [[] for _ in range(n)]
            nbuckets: list = [[] for _ in range(n)]
            for i, p in enumerate(poses):
                buckets[i % n].append(p)
                if names_ok:
                    nbuckets[i % n].append(all_names[i])
            for h, bucket, nb in zip(eligible[:n], buckets, nbuckets):
                chunks.append((h, bucket, f"{len(bucket)} pose(s)", nb if names_ok else None))

    if (body.engine or "").lower() == "klein" and step == "emotions":
        return await _klein_emotions_parallel(session, body, saved, eligible, pinned, gen_settings)

    if (body.engine or "").lower() == "klein":
        if step not in ("creator", "cloner"):
            raise HTTPException(status_code=400,
                                detail="Klein engine currently covers pose generation and emotions")
        kposes = [p for p in (body.pose_set or []) if isinstance(p, dict)]
        if not kposes:
            raise HTTPException(status_code=400, detail="Select at least one pose for a Klein run")
        knames = [str(x) for x in (body.pose_names or [])]
        knames_ok = len(knames) == len(kposes)
        identity = await _klein_identity_bytes(session, body, pinned,
                                               _resolve_lock_base(saved, body))
        try:
            per_job = int(saved.get("klein_poses_per_job") or 1)
        except Exception:  # noqa: BLE001
            per_job = 1
        per_job = max(1, min(8, per_job))
        kseed = int(gen_settings.get("seed") or 1)
        # Group poses into small per-job batches (default 1). The 1536
        # FaceDetailer + PuLID + RMBG2 + Klein-9B + 8B CLIP all stacked in ONE
        # graph OOM some GPUs at 4 poses/graph, so we keep each job light and
        # round-robin the batches across every eligible worker for parallelism.
        kbatches = [kposes[i:i + per_job] for i in range(0, len(kposes), per_job)]
        nbatches = ([knames[i:i + per_job] for i in range(0, len(knames), per_job)]
                    if knames_ok else [None] * len(kbatches))
        kchunks = [(eligible[bi % len(eligible)], b,
                    (nbatches[bi] if bi < len(nbatches) else None),
                    kseed + bi * per_job)
                   for bi, b in enumerate(kbatches)]
        out = []
        errors = []
        for h, subset, cn, csd in kchunks:
            try:
                prompt_id, tap_map, kextras = await asyncio.to_thread(
                    _klein_submit, h, saved, body, subset, identity, csd)
                out.append({"prompt_id": prompt_id, "host": h, "tap_map": tap_map,
                            "label": f"{len(subset)} pose(s) · Klein" + _klein_run_suffix(kextras),
                            "pose_count": len(subset),
                            "pose_names": cn})
            except Exception as e:  # noqa: BLE001 — a dead worker shouldn't sink the run
                logger.warning(f"vnccs klein parallel: chunk on {h} failed: {e}")
                errors.append({"host": h, "error": str(e)})
        if not out:
            raise HTTPException(status_code=502, detail=f"All Klein chunks failed: {errors}")
        # NOTE: klein runs do NOT populate the VNCCS worker-side character store,
        # so they are NOT recorded as sprite shard hosts (qwen clothes/emotions
        # steps can't use them as sources).
        return {"step": step, "chunks": out, "errors": errors,
                "seed": gen_settings.get("seed"), "engine": "klein"}

    # The Cloner node reads its source images from the worker's LOCAL ComfyUI
    # input folder — but references are uploaded to the PINNED host only.  A
    # fan-out chunk on any other worker finds no files, loads zero images and
    # dies with "Сначала загрузите изображение персонажа в Character Cloner".
    # Replicate the reference files to every chunk host before submitting; if
    # replication to a host fails, reroute that chunk to the pinned host.
    per_host_refs: dict = {}

    def _replicate_refs(dst_host: str):
        src = _client(pinned, timeout=120)
        dst = _client(dst_host, timeout=120)
        out_refs = []
        for img in (body.cloner_images or []):
            name = (img or {}).get("name") or ""
            if not name:
                continue
            sub = (img or {}).get("subfolder", "") or ""
            typ = (img or {}).get("type", "input") or "input"
            data = src.view_image(name, sub, typ, 120)
            up = dst.upload_image(name, data, sub, True, 120)
            out_refs.append({"name": up.get("name", name),
                             "subfolder": up.get("subfolder", sub),
                             "type": up.get("type", "input")})
        return out_refs

    if step == "cloner" and body.cloner_images:
        fixed_chunks = []
        for h, ps, lb, nb in chunks:
            if h == pinned:
                fixed_chunks.append((h, ps, lb, nb))
                continue
            try:
                per_host_refs[h] = await asyncio.to_thread(_replicate_refs, h)
                fixed_chunks.append((h, ps, lb, nb))
            except Exception as e:  # noqa: BLE001 — run the chunk where the refs live
                logger.warning(f"vnccs parallel: ref replication to {h} failed ({e}); "
                               f"rerouting chunk to pinned host")
                fixed_chunks.append((pinned, ps, f"{lb} (rerouted)", nb))
        chunks = fixed_chunks

    def _submit(host: str, pose_subset):
        oi = _object_info(host)
        api, tap_map = assemble_step(
            step, oi,
            character_name=body.character_name,
            character_info=body.character_info,
            gen_settings=gen_settings,
            control_center=control_center,
            generator_overrides=body.generator_overrides,
            nsfw=body.nsfw,
            background=body.background,
            costume_name=body.costume_name,
            costume_info=body.costume_info,
            clone_image=body.clone_image,
            clone_sam_prompt=body.clone_sam_prompt,
            costumes=body.costumes,
            emotions=body.emotions,
            generation_model=body.generation_model,
            prompt_style=body.prompt_style,
            cloner_images=per_host_refs.get(host, body.cloner_images),
            pose_set=pose_subset,
        )
        res = VNCCSClient(host, timeout=120).submit_prompt(api, timeout=120)
        return res.get("prompt_id"), tap_map

    out = []
    errors = []
    for host, pose_subset, label, chunk_names in chunks:
        try:
            prompt_id, tap_map = await asyncio.to_thread(_submit, host, pose_subset)
            out.append({"prompt_id": prompt_id, "host": host, "tap_map": tap_map,
                        "label": label, "pose_count": len(pose_subset) if pose_subset else None,
                        "pose_names": chunk_names})
        except Exception as e:  # noqa: BLE001 — a dead worker shouldn't sink the run
            logger.warning(f"vnccs parallel: chunk on {host} failed to submit: {e}")
            errors.append({"host": host, "error": str(e)})
    if not out:
        raise HTTPException(status_code=502, detail=f"All chunks failed to submit: {errors}")

    # creator/cloner runs establish which workers hold this character's sprites
    if step in ("creator", "cloner"):
        try:
            await _record_character_hosts(session, body.character_name.strip(),
                                          [c["host"] for c in out])
        except Exception as e:  # noqa: BLE001
            logger.warning(f"vnccs parallel: host recording failed: {e}")

    return {"step": step, "chunks": out, "errors": errors,
            "seed": gen_settings.get("seed")}


@router.post("/generate-queue/{step}")
async def generate_queue(step: str, body: ParallelGenerateIn, request: Request,
                         session: AsyncSession = Depends(get_session)):
    """Enqueue a Character-Studio run as central Job-queue rows (one Job/chunk).

    The JobDispatcher then selects/pins a worker, submits, monitors and INGESTS
    each chunk — so the run appears in the Generation Queue and inherits
    cancel / purge / retry / SSE.  Returns ``{run_id, job_ids}``.

    Covered: Klein pose runs (engine='klein', creator/cloner -> studio_pose) and
    native VNCCS runs (creator/cloner/clothes/emotions -> studio_pose_native).
    Klein emotions still use /generate-parallel (shared crop-and-stitch prep).
    """
    from uuid import uuid4 as _uuid4
    from backend.database.models import Job, JobType, JobStatus
    from backend.services.character_studio.vnccs_native.ingest import _studio_project

    if step not in STEP_FILES:
        raise HTTPException(status_code=404, detail=f"unknown step {step!r}")
    engine = (body.engine or "").lower()
    is_klein = engine == "klein"
    if is_klein and step not in ("creator", "cloner"):
        raise HTTPException(
            status_code=400,
            detail=("Queue mode supports Klein pose runs (creator/cloner) and native runs; "
                    "Klein emotions use /generate-parallel."))

    pinned = await _need_host(request, session)
    _, st = await _resolve_host(request, session)
    comfy = getattr(request.app.state, "comfy_dispatcher", None)
    configured = (st.studio_vnccs_host or None) if st else None
    all_hosts = list_vnccs_hosts(comfy, configured) or [pinned]
    if pinned not in all_hosts:
        all_hosts.insert(0, pinned)
    saved = (st.studio_vnccs_settings if st else None) or {}
    control_center = body.control_center or saved.get("control_center")
    gen_settings = _roll_seed(saved.get("gen_settings"), body.gen_settings)

    # eligibility per sharding rules (clothes/emotions must run on the hosts
    # that already hold this character's sprites)
    if step in ("clothes", "emotions"):
        recorded = await _character_hosts(session, body.character_name.strip())
        eligible = [h for h in recorded if h in all_hosts] or [pinned]
    else:
        eligible = list(all_hosts)
    if body.max_hosts and body.max_hosts > 0:
        eligible = eligible[:body.max_hosts]
    if not eligible:
        raise HTTPException(status_code=502, detail="No VNCCS-capable worker is online.")

    proj = await _studio_project(session)
    run_id = _uuid4().hex
    # pose runs post an empty character_info — refill it from the saved character
    # so the mannequin build + body prompt text carry into every pose job.
    await _enrich_character_info(session, body)
    gi = {k: getattr(body, k) for k in GenerateIn.model_fields}
    kposes = [p for p in (body.pose_set or []) if isinstance(p, dict)]
    knames = [str(x) for x in (body.pose_names or [])]
    knames_ok = len(knames) == len(kposes)
    base_seed = int(gen_settings.get("seed") or 1)

    jobs: list = []

    def _mk_job(extra: dict):
        params = {
            "run_id": run_id,
            "character_name": body.character_name.strip(),
            "generate_in": gi,
            "studio_settings": saved,
            "gen_settings": gen_settings,
            "control_center": control_center,
            "ref_host": pinned,
            "project_id": str(proj.id),
            "auto_save_preview": False,
        }
        params.update(extra)
        jobs.append(Job(project_id=proj.id, scene_id=None, job_type=JobType.IMAGE,
                        status=JobStatus.PENDING, parameters=params, priority=0))

    if is_klein:
        if not kposes:
            raise HTTPException(status_code=400, detail="Select at least one pose for a Klein run")
        # fast-fail if no identity image is available
        _ = await _klein_identity_bytes(session, body, pinned, _resolve_lock_base(saved, body))
        try:
            per_job = int(saved.get("klein_poses_per_job") or 1)
        except Exception:  # noqa: BLE001
            per_job = 1
        per_job = max(1, min(8, per_job))
        kbatches = [kposes[i:i + per_job] for i in range(0, len(kposes), per_job)]
        nbatches = ([knames[i:i + per_job] for i in range(0, len(knames), per_job)]
                    if knames_ok else [None] * len(kbatches))
        recipe = {"postprocess": "chroma", "engine": "klein",
                  "pose_set": body.pose_set, "pose_names": knames if knames_ok else None}
        for bi, subset in enumerate(kbatches):
            _mk_job({"workflow_type": "studio_pose", "step": step, "engine": "klein",
                     "chunk_index": bi, "seed": base_seed + bi * per_job,
                     "pose_subset": subset,
                     "pose_names": nbatches[bi] if bi < len(nbatches) else None,
                     "ingest_recipe": recipe})
    elif step in ("creator", "cloner"):
        # native poses: split across N chunks; dispatcher assigns the workers
        poses = body.pose_set or []
        if len(eligible) <= 1 or len(poses) <= 1:
            buckets = [poses or None]
            nbuckets = [knames if (poses and knames_ok) else None]
        else:
            n = min(len(eligible), len(poses))
            buckets = [[] for _ in range(n)]
            nbuckets_l = [[] for _ in range(n)]
            for i, p in enumerate(poses):
                buckets[i % n].append(p)
                if knames_ok:
                    nbuckets_l[i % n].append(knames[i])
            nbuckets = [nb if knames_ok else None for nb in nbuckets_l]
        recipe = {"postprocess": None, "engine": None,
                  "pose_set": body.pose_set, "pose_names": knames if knames_ok else None}
        for bi, subset in enumerate(buckets):
            _mk_job({"workflow_type": "studio_pose_native", "step": step, "engine": None,
                     "chunk_index": bi, "seed": base_seed,
                     "pose_subset": subset,
                     "pose_names": nbuckets[bi] if bi < len(nbuckets) else None,
                     "ingest_recipe": dict(recipe)})
    elif step == "clothes":
        # native clothes: pin each chunk to a recorded host; split poses across them
        poses = body.pose_set or []
        n = max(1, len(eligible))
        buckets = [[] for _ in range(n)]
        nbuckets_l = [[] for _ in range(n)]
        for i, p in enumerate(poses):
            buckets[i % n].append(p)
            if knames_ok:
                nbuckets_l[i % n].append(knames[i])
        recipe = {"postprocess": None, "engine": None, "costume": body.costume_name,
                  "pose_set": body.pose_set, "pose_names": knames if knames_ok else None}
        for bi, h in enumerate(eligible):
            subset = buckets[bi] if bi < len(buckets) else []
            _mk_job({"workflow_type": "studio_pose_native", "step": step, "engine": None,
                     "chunk_index": bi, "seed": base_seed, "pin_host": h,
                     "pose_subset": subset or None,
                     "pose_names": (nbuckets_l[bi] if (bi < len(nbuckets_l) and knames_ok) else None),
                     "ingest_recipe": dict(recipe)})
    else:  # native emotions: SAME request to every recorded host (each FaceDetails its own sprites)
        recipe = {"postprocess": None, "engine": None,
                  "emotions": body.emotions, "costumes": body.costumes}
        for bi, h in enumerate(eligible):
            _mk_job({"workflow_type": "studio_pose_native", "step": step, "engine": None,
                     "chunk_index": bi, "seed": base_seed, "pin_host": h,
                     "pose_subset": None, "pose_names": None,
                     "ingest_recipe": dict(recipe)})

    if not jobs:
        raise HTTPException(status_code=400, detail="Nothing to enqueue for this run.")
    for job in jobs:
        session.add(job)
    await session.commit()
    job_ids = [str(j.id) for j in jobs]

    job_queue = getattr(request.app.state, "job_queue", None)
    if job_queue is not None:
        try:
            job_queue.notify()
        except Exception as e:  # noqa: BLE001
            logger.warning(f"vnccs queue: job_queue.notify failed: {e}")

    logger.info("vnccs queue: enqueued run %s — %d chunk job(s) for %s/%s (engine=%s)",
                run_id, len(job_ids), body.character_name.strip(), step, engine or "native")
    return {"run_id": run_id, "job_ids": job_ids, "step": step,
            "engine": engine or "native", "chunk_count": len(job_ids),
            "seed": gen_settings.get("seed")}


class PreviewIn(BaseModel):
    character_name: str
    character_info: dict = {}
    gen_settings: Optional[dict] = None
    nsfw: bool = False
    background: str = "Green"
    engine: Optional[str] = None             # 'klein' = Klein 9B preview
    base_clothing: Optional[str] = None      # 'strip'|'keep' base outfit (per-run override)
    face_kind: Optional[str] = None          # output style key (also PuLID gate)
    style_custom: Optional[str] = None       # free-text when face_kind == 'custom'
    # Clone-tab preview: uploaded reference images (host input files).  With
    # engine='klein' the preview renders the default pose from these refs via
    # the full identity chain (multi-ref + face crop + PuLID) instead of T2I.
    cloner_images: Optional[list] = None
    # Base preview: False/None = FRONT view only (default); True = full 4-view
    # set (front/right/left/back).  None falls back to setting klein_base_set.
    base_set: Optional[bool] = None
    cleanup: Optional[str] = None            # 'off' | 'gentle' | 'strong'
    klein_steps: Optional[int] = None        # sampler steps (default 6)


@router.post("/preview")
async def generate_preview(body: PreviewIn, request: Request,
                           session: AsyncSession = Depends(get_session)):
    """Generate the character ONCE in the default pose via the host's
    ``/vnccs/preview_generate`` (the panel's "Generate Preview" button) —
    fast single image, no pose sprites, no upscale.  Uses the vendored
    graph's working gen_settings baseline + saved overrides."""
    host = await _need_host(request, session)
    st = await _settings(session)
    saved = (st.studio_vnccs_settings if st else None) or {}

    gs = creator_baseline_gen_settings()
    if saved.get("gen_settings"):
        _merge_gen_settings(gs, saved.get("gen_settings"))
    if body.gen_settings:
        _merge_gen_settings(gs, body.gen_settings)
    if not (saved.get("gen_settings") or body.gen_settings):
        # still randomize the template's fixed seed
        _merge_gen_settings(gs, {})
    ci = map_character_info(body.character_info or {}, name=body.character_name.strip(),
                            nsfw=body.nsfw, background=body.background)
    if (body.engine or "").lower() == "klein":
        # Klein-mode base preview: plain Klein 9B T2I from the tag sheet — the
        # identity source for every downstream Klein pose run.
        def _run_klein_preview():
            from backend.services.character_studio.vnccs_native import klein_poses
            oi = _object_info(host)
            models = klein_poses.resolve_klein_models(oi, saved, require_lora=False)
            seed_v = int(gs.get("seed") or 0) or 1
            prompt = klein_poses.klein_preview_prompt(
                body.character_info or {}, body.background,
                nsfw=bool(getattr(body, "nsfw", False)),
                style_kind=str(getattr(body, "face_kind", None) or "auto"),
                style_custom=str(getattr(body, "style_custom", None) or "").strip())
            safe = "".join(ch for ch in body.character_name if ch.isalnum())[:24] or "char"
            graph, _tap = klein_poses.build_klein_t2i_graph(
                prompt=prompt, seed=seed_v, models=models,
                steps=klein_poses.resolve_klein_steps(saved),
                rmbg=klein_poses.resolve_rmbg(oi, saved),
                filename_prefix=f"rbmn_vnccs/{safe}/klein_preview")
            client = _client(host, timeout=120)
            res = client.submit_prompt(graph, timeout=120)
            return _klein_wait_first_image(client, res.get("prompt_id"),
                                            _klein_preview_timeout(klein_poses.resolve_klein_steps(saved), 1))

        def _run_klein_clone_preview():
            # Clone-tab preview: ONE default-pose render from the uploaded
            # reference images, through the same identity chain as real pose
            # runs (multi-ref + face crop + PuLID + face refine) — review the
            # likeness BEFORE committing to a full pose set.
            import uuid as _uuid
            from backend.services.character_studio.vnccs_native import klein_poses, pose_render
            from backend.services.character_studio.vnccs_native.workflows import creator_baseline_pose_data
            oi = _object_info(host)
            models = klein_poses.resolve_klein_models(oi, saved)
            seed_v = int(gs.get("seed") or 0) or 1
            client = _client(host, timeout=120)
            token = _uuid.uuid4().hex[:8]
            safe = "".join(ch for ch in body.character_name if ch.isalnum())[:24] or "char"
            names = [str((img or {}).get("name") or "")
                     for img in (body.cloner_images or []) if (img or {}).get("name")][:4]
            if not names:
                raise VNCCSError("Clone preview needs uploaded reference images")
            pd = creator_baseline_pose_data()
            # DEDICATED NEUTRAL default pose (like VNCCS's neutral base) -- NOT one of
            # the pose-library poses: empty bones = the mannequin's natural rest stance,
            # front-facing, so the base preview suits any reference. (If this renders a
            # T-pose we set explicit arm-down A-pose rotations instead.)
            # Base preview: FRONT view by default; the full 4-view set
            # (front/right/left/back, same neutral stance, mannequin rotated) is
            # opt-in via the base_set flag / klein_base_set setting.
            _bp = pd.get("poses") or [{}]
            _base_neutral = dict(_bp[0]) if _bp else {}
            _base_neutral["bones"] = {}
            BASE_VIEWS = [
                ("front", [0, 0, 0], "FRONT view facing forward"),
                ("right", [0, 90, 0], "RIGHT-SIDE profile view, body turned 90 degrees to the side"),
                ("left", [0, -90, 0], "LEFT-SIDE profile view, body turned 90 degrees to the other side"),
                ("back", [0, 180, 0], "BACK view seen from directly behind, facing away from the camera"),
            ]
            _want_set = bool(getattr(body, "base_set", None)) or (
                str(saved.get("klein_base_set") or "").strip().lower()
                in ("on", "true", "1", "yes", "set"))
            _views = BASE_VIEWS if _want_set else BASE_VIEWS[:1]
            _view_poses = []
            for _vlabel, _rot, _vp in _views:
                _pose = dict(_base_neutral)
                _pose["modelRotation"] = _rot
                _pose["prompt"] = ("standing straight and relaxed, arms resting slightly "
                                   "away from the body, full body visible head to toe, " + _vp)
                _view_poses.append(_pose)
            pd["poses"] = _view_poses
            face_kind = str(getattr(body, "face_kind", None) or "auto").strip().lower()
            style_custom = str(getattr(body, "style_custom", None) or "").strip()
            _sex = str((body.character_info or {}).get("sex") or "")
            _nsfw = bool(getattr(body, "nsfw", False))
            # face-crop source: a dedicated 'face' ref first, then 'full', else names[0]
            face_file = None
            real_face = False
            _pv_ci = list(getattr(body, "cloner_images", None) or [])
            _face_name = names[0]
            for _pref in ("face", "full"):
                _hit = next((names[_k] for _k in range(len(names))
                             if _k < len(_pv_ci) and isinstance(_pv_ci[_k], dict)
                             and str(_pv_ci[_k].get("role") or "").strip().lower() == _pref), None)
                if _hit:
                    _face_name = _hit
                    break
            try:
                ref_bytes = client.view_image(_face_name, "", "input", 120)
                fc = _klein_identity_crop(ref_bytes)
                if fc:
                    face_bytes, face_method = fc
                    real_face = face_method in ("yunet", "haar")
                    upf = client.upload_image(f"rbmn_klein_{safe}_{token}_pvface.png",
                                              face_bytes, "", True, 120)
                    face_file = upf.get("name", f"rbmn_klein_{safe}_{token}_pvface.png")
            except VNCCSError:
                pass
            if klein_poses._style_is_stylized(face_kind):
                pulid = None
            else:
                pulid = klein_poses.resolve_pulid(oi, saved) if face_file else None
            face_refine = klein_poses.resolve_face_refine(oi, saved)
            bc = str(getattr(body, "base_clothing", None)
                     or saved.get("klein_base_clothing") or "strip")
            keep_c = klein_poses._keep_clothing(bc)
            reflatentplus = klein_poses.resolve_reflatentplus(oi, saved) if not keep_c else None
            # roles -> the BODY reference images (body/full)
            _pv_roles = []
            for _k in range(len(names)):
                _r = ""
                if _k < len(_pv_ci) and isinstance(_pv_ci[_k], dict):
                    _r = str(_pv_ci[_k].get("role") or "").strip().lower()
                _pv_roles.append(_r if _r in ("face", "body", "full") else "full")
            pv_body_files = [names[_k] for _k, _r in enumerate(_pv_roles) if _r in ("body", "full")]
            _eff = dict(saved or {})
            if getattr(body, "cleanup", None):
                _eff["klein_cleanup"] = body.cleanup
            if getattr(body, "klein_steps", None):
                _eff["klein_steps"] = body.klein_steps
            neg_text, klein_cfg = klein_poses.resolve_strip_negative(_eff, keep_c)
            _ksteps = klein_poses.resolve_klein_steps(_eff)
            rmbg_cfg = klein_poses.resolve_rmbg(oi, saved)

            # REFERENCE-DRIVEN base: when body references exist and we're stripping to
            # a neutral base, build the body FROM the photos (no mannequin, so the
            # mannequin's build can't override the references).  The neutral pose comes
            # from the prompt; every pose later LOCKS to this base.  One render per view.
            use_refbase = bool(pv_body_files) and not keep_c
            if use_refbase:
                logger.info("klein refbase (clone preview): reference-driven base from "
                            "%d body ref(s) — no mannequin", len(pv_body_files))
                import base64 as _b64r
                try:
                    _rb_strength = float(saved.get("klein_body_match_strength") or 1.15)
                except Exception:  # noqa: BLE001
                    _rb_strength = 1.15
                # release the body reference over the final steps so the tail of the
                # render can wipe residual on-skin accessories (wrist/neck jewelry) the
                # prompt asks to remove -- body shape is already locked by then. 1.0 =
                # hold full (old behavior); lower strips harder. Setting klein_refbase_ref_end.
                try:
                    _rb_end = float(saved.get("klein_refbase_ref_end") or 0.85)
                except Exception:  # noqa: BLE001
                    _rb_end = 0.85
                _rb_end = max(0.5, min(1.0, _rb_end))
                imgs_b64 = []
                for _i, (_vlabel, _rot, _vp) in enumerate(_views):
                    _vprompt = klein_poses.klein_refbase_prompt(
                        body.character_info or {}, body.background, nsfw=_nsfw,
                        view_desc=_vp, style_kind=face_kind, style_custom=style_custom, sex=_sex)
                    _g, _t = klein_poses.build_klein_refbase_graph(
                        prompt=_vprompt, seed=seed_v + _i, models=models,
                        body_files=pv_body_files, reflatentplus=reflatentplus,
                        strength=_rb_strength, body_ref_end=_rb_end, face_file=face_file, pulid=pulid,
                        rmbg=rmbg_cfg, cfg=klein_cfg, negative_prompt=neg_text, steps=_ksteps,
                        filename_prefix=f"rbmn_vnccs/{safe}/klein_refbase")
                    _r = client.submit_prompt(_g, timeout=120)
                    _raw = _wait_first_image_bytes(_client(host, timeout=120), _r.get("prompt_id"),
                                                   _klein_preview_timeout(_ksteps, 1))
                    imgs_b64.append(_b64r.b64encode(_raw).decode("ascii"))
            else:
                # FALLBACK (face-only clone or keep-clothing): the mannequin path, with
                # its build matched to the character.
                pd["mesh"] = {**(pd.get("mesh") or {}),
                              **klein_poses.body_mesh_params(body.character_info or {})}
                caps = pose_render.render_pose_captures(pd, False)
                if not caps or len(caps) != len(_views):
                    raise VNCCSError("app-side pose renderer unavailable (CharacterData missing?)")
                pose_files = []
                for _i, _cap in enumerate(caps):
                    _upv = client.upload_image(f"rbmn_klein_{safe}_{token}_pv{_i}.png",
                                               klein_poses.decode_capture(_cap), "", True, 120)
                    pose_files.append(_upv.get("name", f"rbmn_klein_{safe}_{token}_pv{_i}.png"))
                face_ref = bool(face_file)
                fidx = (1 + len(names) + 1) if (face_file and face_ref) else None
                appear = (klein_poses.klein_body_text(body.character_info or {})
                          if not keep_c else None)
                prompts = [
                    klein_poses.klein_pose_prompt(
                        (_vp or {}).get("prompt", ""), body.background, len(names),
                        face_image_index=fidx, base_clothing=bc, nsfw=_nsfw, appearance=appear,
                        style_kind=face_kind, sex=_sex, style_custom=style_custom)
                    for _vp in pd["poses"]]
                graph, _tap = klein_poses.build_klein_pose_graph(
                    pose_files=pose_files, identity_files=list(names), prompts=prompts,
                    seed=seed_v, models=models, steps=_ksteps, face_file=face_file, pulid=pulid,
                    face_refine=face_refine, strip_body_refs=False, face_as_reference=face_ref,
                    negative_prompt=neg_text, cfg=klein_cfg, rmbg=rmbg_cfg,
                    face_refine_first_only=True,
                    filename_prefix=f"rbmn_vnccs/{safe}/klein_preview")
                res = client.submit_prompt(graph, timeout=120)
                imgs_b64 = _klein_wait_all_images(client, res.get("prompt_id"),
                                                  _klein_preview_timeout(_ksteps, len(_views)))
            if not imgs_b64:
                raise VNCCSError("Klein base preview produced no images")
            # tighten + uniformly frame the whole set (kills wasted space)
            try:
                import base64 as _b64c
                from backend.services.character_studio.cutout import normalize_base_set
                _raw = [_b64c.b64decode(x) for x in imgs_b64]
                _framed = normalize_base_set(_raw)
                imgs_b64 = [_b64c.b64encode(x).decode("ascii") for x in _framed]
            except Exception:  # noqa: BLE001 — framing is best-effort
                logger.exception("klein base preview: framing/normalize failed")
            _labels = [v[0] for v in _views]
            return [{"view": (_labels[i] if i < len(_labels) else f"view{i}"),
                     "image_b64": b} for i, b in enumerate(imgs_b64)]

        runner = _run_klein_clone_preview if body.cloner_images else _run_klein_preview
        try:
            _res = await asyncio.to_thread(runner)
        except (VNCCSError, ValueError) as e:
            raise HTTPException(status_code=502, detail=str(e))
        if isinstance(_res, list):        # clone = 4-view base set
            payload = {"views": _res,
                       "image": (_res[0].get("image_b64") if _res else None)}
        else:
            payload = {"image": _res}
    elif body.cloner_images:
        # NATIVE clone preview: the host's /vnccs/preview_generate renders the
        # checkpoint (Anima) from the tag sheet — NOT the cloned person.  Run
        # the REAL CharacterCloner meganode limited to ONE pose instead, so
        # the preview shows the actual clone before a full pose run.
        def _run_native_clone_preview():
            oi = _object_info(host)
            poses = default_pose_set()
            api_graph, tap_map = assemble_step(
                "cloner", oi,
                character_name=body.character_name,
                character_info=ci,
                gen_settings=gs,
                control_center=saved.get("control_center"),
                generator_overrides={"upscaler": {"mode": "off"}},
                nsfw=body.nsfw,
                background=body.background,
                cloner_images=body.cloner_images,
                pose_set=poses[:1] if poses else None,
            )
            client = _client(host, timeout=120)
            res = client.submit_prompt(api_graph, timeout=120)
            prefer = [str(tap_map.get(k)) for k in
                      ("original_sprites", "sprites", "original_upscaled", "sheet")
                      if tap_map.get(k)]
            return _vnccs_wait_tap_image(client, res.get("prompt_id"), prefer, 900)
        try:
            payload = {"image": await asyncio.to_thread(_run_native_clone_preview)}
        except (VNCCSError, ValueError) as e:
            raise HTTPException(status_code=502, detail=str(e))
    else:
        try:
            payload = await asyncio.to_thread(
                _client(host, timeout=600).post_json, "preview_generate",
                {"gen_settings": gs, "character_info": ci, "character": body.character_name.strip()},
                600)
        except VNCCSError as e:
            raise HTTPException(status_code=502, detail=str(e))
    if not isinstance(payload, dict) or not payload.get("image"):
        raise HTTPException(status_code=502, detail=f"preview_generate returned no image: {payload}")
    # persist as a base-image VERSION (latest becomes the active default);
    # best-effort — the preview is still returned if cataloging fails
    version = None
    try:
        from backend.services.character_studio.vnccs_native.ingest import save_base_preview
        version = await save_base_preview(
            session, character_name=body.character_name.strip(),
            image_b64=payload.get("image"), views=payload.get("views"),
            variant=("klein" if (body.engine or "").lower() == "klein" else None))
    except Exception as e:  # noqa: BLE001
        logger.warning(f"vnccs preview: base-version save failed: {e}")
    return {"image": payload.get("image"), "views": payload.get("views"), "version": version}


@router.get("/pose-defaults")
async def pose_defaults(thumbs: bool = True):
    """The 12 default VNCCS poses (full data for pose_set) + optional app-side
    rendered thumbnails (best-effort — null when CharacterData is unavailable)."""
    poses = default_pose_set()
    out = [{"index": i,
            "name": f"Pose {i + 1}",
            "prompt": (p.get("prompt") or "") if isinstance(p, dict) else "",
            "pose": p, "thumb": None} for i, p in enumerate(poses)]
    if thumbs and poses:
        try:
            from backend.services.character_studio.vnccs_native import pose_render
            pd = creator_baseline_pose_data()
            pd["export"] = {**(pd.get("export") or {}),
                            "view_width": 144, "view_height": 328,
                            "bg_color": [255, 255, 255]}
            # silhouette=True matches the node UI's Pose Manager thumbnails
            caps = await asyncio.to_thread(pose_render.render_pose_captures, pd, True)
            if caps and len(caps) == len(out):
                for i, c in enumerate(caps):
                    out[i]["thumb"] = c
        except Exception as e:  # noqa: BLE001 — thumbs are cosmetic
            logger.debug(f"pose-defaults thumbs unavailable: {e}")
    return {"poses": out, "max_pose_set": MAX_POSE_SET}


class CharacterSaveIn(BaseModel):
    name: str
    character_info: dict = {}
    gen_settings: Optional[dict] = None
    story_id: Optional[str] = None
    create_mode: Optional[str] = None        # 'new' | 'clone' — clone takes precedence
    clone_refs: Optional[list] = None        # uploaded reference images ({name,subfolder,type})
    variant: Optional[str] = None            # 'native' | 'klein' — which studio mode


@router.post("/character/save")
async def character_save(body: CharacterSaveIn, session: AsyncSession = Depends(get_session)):
    """Save the Create-tab form onto a StudioCharacter (our catalog = system of
    record) so the character can be revisited, tweaked and regenerated later."""
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Character name required")
    from uuid import UUID as _UUID
    from backend.services.character_studio.vnccs_native.ingest import _find_or_create_character
    sid = None
    if body.story_id:
        try:
            sid = _UUID(body.story_id)
        except Exception:
            raise HTTPException(status_code=400, detail="invalid story_id")
    char = await _find_or_create_character(session, name, sid)
    from datetime import datetime
    manifest = dict(char.manifest or {})
    vnccs = dict(manifest.get("vnccs") or {})
    vnccs["form"] = {
        "character_info": body.character_info or {},
        "gen_settings": body.gen_settings,
        "saved_at": datetime.utcnow().isoformat(),
    }
    # remember HOW this character is made. Clone wins: once a character has
    # been cloned, later tweaks from the New form must not flip it back.
    if body.create_mode in ("new", "clone"):
        vnccs["create_mode"] = "clone" if (body.create_mode == "clone"
                                           or vnccs.get("create_mode") == "clone") else "new"
    if body.create_mode == "clone":
        # distinguish "not sent" (None -> keep existing) from "sent, possibly
        # edited/emptied" (list -> REPLACE) so add/remove of references persists.
        _refs = (body.clone_refs if body.clone_refs is not None
                 else (vnccs.get("clone") or {}).get("refs") or [])
        vnccs["clone"] = {
            "character_info": body.character_info or {},
            "refs": _refs,
            "saved_at": datetime.utcnow().isoformat(),
        }
    if body.variant in ("native", "klein"):
        # Klein wins once set — a Klein-made character keeps its Klein editor
        vnccs["variant"] = ("klein" if (body.variant == "klein"
                                        or vnccs.get("variant") == "klein") else "native")
    vnccs.setdefault("ref", name)
    manifest["vnccs"] = vnccs
    char.manifest = manifest  # reassign whole dict so SQLAlchemy tracks it
    await session.commit()
    return {"character_id": str(char.id), "name": char.name}


class CostumePreviewIn(BaseModel):
    character_name: str
    costume_name: str
    costume_info: dict = {}
    background: str = "Green"                # designer supports Green/Blue
    sprite_index: Optional[int] = None       # which base pose sprite to dress
    host: Optional[str] = None


@router.post("/costume-preview")
async def costume_preview(body: CostumePreviewIn, request: Request,
                          session: AsyncSession = Depends(get_session)):
    """Dress ONE pose sprite in the costume via the host's
    ``/vnccs/control_center/clothes_preview`` (the panel's costume preview) —
    fast audition before generating all poses.  Persisted as a costume VERSION
    with its prompt snapshot; newest becomes active."""
    name = body.character_name.strip()
    costume = body.costume_name.strip()
    if not name or not costume:
        raise HTTPException(status_code=400, detail="character and costume name required")
    pinned = await _need_host(request, session)
    _, st = await _resolve_host(request, session)
    comfy = getattr(request.app.state, "comfy_dispatcher", None)
    configured = (st.studio_vnccs_host or None) if st else None
    all_hosts = list_vnccs_hosts(comfy, configured) or [pinned]
    recorded = await _character_hosts(session, name)
    eligible = [h for h in recorded if h in all_hosts] or [pinned]
    host = (body.host or "").rstrip("/") or eligible[0]
    client = _client(host, timeout=600)

    def _run():
        # ensure the costume exists on the host (idempotent)
        try:
            client.post_json("create_costume", {"character": name, "costume": costume}, 60)
        except Exception:  # noqa: BLE001 — may already exist
            pass
        wd = clothes_baseline_widget_data()
        wd["character"] = name
        wd["costume"] = costume
        wd["activeTab"] = "generate"
        wd["character_ready"] = True
        wd.pop("clone_image", None)
        ci = dict(wd.get("costume_info") or {})
        for slot in ("top", "bottom", "head", "face", "shoes"):
            if body.costume_info.get(slot) is not None:
                ci[slot] = body.costume_info[slot]
        wd["costume_info"] = ci
        # the designer builds its prompt from gen_settings.background_color —
        # without this the preview ignored the UI's background selection
        # (node supports Green/Blue; anything else falls back to Green)
        gs = dict(wd.get("gen_settings") or {})
        gs["background_color"] = body.background or gs.get("background_color", "Green")
        wd["gen_settings"] = gs
        if body.sprite_index is not None:
            count = 0
            try:
                meta = client.get_json("get_character_pose_preview_meta", params={"character": name})
                count = int(meta.get("count") or 0)
            except Exception:  # noqa: BLE001
                pass
            # costume=None makes the designer enumerate sprites EXACTLY like the
            # get_character_pose_preview strip the user cycles in our UI (both
            # walk Naked/Neutral -> Original/Neutral -> ...).  Passing 'Original'
            # made the node resolve a DIFFERENT folder first when the character
            # has both Original and Naked sprites, so it dressed the wrong pose.
            wd["selected_preview_sprite"] = {"character": name, "costume": None,
                                             "index": int(body.sprite_index), "count": count}
        repo_id, node_state = clothes_baseline_control()
        saved_cc = ((st.studio_vnccs_settings if st else None) or {}).get("control_center") or {}
        if saved_cc.get("selected_model"):
            try:
                import json as _json
                ns = _json.loads(node_state)
                ns["selected_model"] = saved_cc["selected_model"]
                stype = saved_cc.get("selected_type") or ns.get("selected_type")
                if stype:
                    ns["selected_type"] = stype
                    ns.setdefault("selected_models", {})[stype] = saved_cc["selected_model"]
                node_state = _json.dumps(ns)
            except Exception:  # noqa: BLE001
                pass
        return client.post_json("control_center/clothes_preview",
                                {"repo_id": repo_id, "node_state": node_state,
                                 "clothes_state": wd}, 600)

    try:
        payload = await asyncio.to_thread(_run)
    except VNCCSError as e:
        raise HTTPException(status_code=502, detail=str(e))
    if not isinstance(payload, dict) or not payload.get("image"):
        raise HTTPException(status_code=502, detail=f"clothes_preview returned no image: {payload}")
    version = None
    try:
        from backend.services.character_studio.vnccs_native.ingest import save_costume_preview
        version = await save_costume_preview(session, character_name=name, costume=costume,
                                             image_b64=payload["image"], costume_info=body.costume_info)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"vnccs costume-preview: version save failed: {e}")
    return {"image": payload["image"], "version": version, "host": host}


class CostumeInfoIn(BaseModel):
    character_name: str
    costume: str
    costume_info: dict = {}


@router.post("/costume-info")
async def save_costume_info_route(body: CostumeInfoIn,
                                  session: AsyncSession = Depends(get_session)):
    """Save an outfit's WORKING prompt set without generating anything — the
    Clothes tab's 💾 button and the auto-save after generation runs."""
    name = body.character_name.strip()
    costume = body.costume.strip()
    if not name or not costume:
        raise HTTPException(status_code=400, detail="character and costume required")
    from backend.services.character_studio.vnccs_native.ingest import save_costume_info
    return await save_costume_info(session, character_name=name, costume=costume,
                                   costume_info=body.costume_info)


class CostumeActiveIn(BaseModel):
    costume: str
    version_id: str


@router.post("/character/{character_id}/costume-active")
async def set_active_costume(character_id: str, body: CostumeActiveIn,
                             session: AsyncSession = Depends(get_session)):
    """Mark a costume-preview version as ACTIVE for that costume."""
    from uuid import UUID as _UUID
    from backend.database.models import StudioCharacter
    try:
        cid = _UUID(character_id)
    except Exception:
        raise HTTPException(status_code=400, detail="invalid character_id")
    c = await session.get(StudioCharacter, cid)
    if c is None:
        raise HTTPException(status_code=404, detail="character not found")
    manifest = dict(c.manifest or {})
    v = dict(manifest.get("vnccs") or {})
    costumes = dict(v.get("costumes") or {})
    entry = dict(costumes.get(body.costume) or {})
    if not any(ver.get("id") == body.version_id for ver in (entry.get("versions") or [])):
        raise HTTPException(status_code=404, detail="unknown costume version")
    entry["active"] = body.version_id
    costumes[body.costume] = entry
    v["costumes"] = costumes
    manifest["vnccs"] = v
    c.manifest = manifest
    await session.commit()
    return {"active": body.version_id, "costume": body.costume}


class BaseActiveIn(BaseModel):
    version_id: str


@router.post("/character/{character_id}/base-active")
async def set_active_base(character_id: str, body: BaseActiveIn,
                          session: AsyncSession = Depends(get_session)):
    """Mark a base-image version as ACTIVE — subsequent pose runs link to it."""
    from uuid import UUID as _UUID
    from backend.database.models import StudioCharacter
    try:
        cid = _UUID(character_id)
    except Exception:
        raise HTTPException(status_code=400, detail="invalid character_id")
    c = await session.get(StudioCharacter, cid)
    if c is None:
        raise HTTPException(status_code=404, detail="character not found")
    manifest = dict(c.manifest or {})
    v = dict(manifest.get("vnccs") or {})
    versions = v.get("base_versions") or []
    if not any(ver.get("id") == body.version_id for ver in versions):
        raise HTTPException(status_code=404, detail="unknown base version")
    v["active_base"] = body.version_id
    manifest["vnccs"] = v
    c.manifest = manifest
    await session.commit()
    return {"active": body.version_id}


# --------------------------------------------------------------------------- #
# LLM Wizards — Character / Clothes / Cloner-Analyze
#
# Host-first (the real VNCCS wizard routes: identical model + prompts + tag
# catalog = literally the same result as the VNCCS panel), with an automatic
# Ollama fallback that reuses the VERBATIM VNCCS prompts (only the LLM
# differs) so the buttons keep working when the host lacks llama-cpp-python
# or the Qwen GGUF download fails.  ``backend`` forces "host" or "ollama".
# --------------------------------------------------------------------------- #
from backend.services.character_studio.vnccs_native import wizards as _wiz


class HeroIn(BaseModel):
    asset_id: str


@router.post("/catalog/{character_id}/hero")
async def set_hero_image(character_id: str, body: HeroIn,
                         session: AsyncSession = Depends(get_session)):
    """Pick ANY cataloged image (pose/costume/emotion/base) as the character's
    thumbnail — shown on the Character Studio main screen and the library."""
    from uuid import UUID as _UUID
    from backend.database.models import Asset, StudioCharacter
    try:
        char = await session.get(StudioCharacter, _UUID(character_id))
    except Exception:
        char = None
    if char is None:
        raise HTTPException(status_code=404, detail="character not found")
    try:
        a = await session.get(Asset, _UUID(body.asset_id))
    except Exception:
        a = None
    if a is None:
        raise HTTPException(status_code=404, detail="asset not found")
    rel = str(a.rel_path).replace("\\", "/")
    pid = str(a.project_id)
    url = f"/api/files/{pid}/" + (rel[len(pid) + 1:] if rel.startswith(pid + "/") else rel)
    manifest = dict(char.manifest or {})
    vnccs = dict(manifest.get("vnccs") or {})
    vnccs["hero_asset_id"] = str(a.id)
    vnccs["hero_url"] = url
    vnccs["hero_locked"] = True   # ingest won't auto-replace a user-chosen hero
    manifest["vnccs"] = vnccs
    char.manifest = manifest
    session.add(char)
    await session.commit()
    return {"ok": True, "hero_asset_id": str(a.id), "hero_url": url}


class WizardIn(BaseModel):
    description: str
    backend: str = "auto"          # auto | host | ollama


class CloneAnalyzeIn(BaseModel):
    image: dict = {}                # {name, subfolder, type} from /upload
    images: Optional[list] = None   # multiple refs -> analyze the SET together
    backend: str = "auto"


def _wizard_host_error(payload: Any) -> Optional[str]:
    """VNCCS wizard routes return 200/500 JSON with an ``error`` key on failure."""
    if isinstance(payload, dict) and payload.get("error"):
        return f"{payload.get('error')}: {payload.get('message', '')}"
    return None


async def _ollama_cfg(session: AsyncSession) -> tuple[list, Optional[str], Optional[str]]:
    st = await _settings(session)
    urls = (st.ollama_urls if st else None) or ([st.ollama_base_url] if st and st.ollama_base_url else [])
    return urls or [], (st.ollama_model if st else None), (st.ollama_vision_model if st else None)


@router.post("/wizard/character")
async def wizard_character(body: WizardIn, request: Request,
                           session: AsyncSession = Depends(get_session)):
    desc = body.description.strip()
    if not desc:
        raise HTTPException(status_code=400, detail="No character description provided")
    host, _ = await _resolve_host(request, session)
    host_err: Optional[str] = None

    if body.backend in ("auto", "host") and host:
        try:
            payload = await asyncio.to_thread(
                _client(host, timeout=_wiz.WIZARD_HOST_TIMEOUT).post_json,
                "character_wizard", {"description": desc}, _wiz.WIZARD_HOST_TIMEOUT)
            host_err = _wizard_host_error(payload)
            if not host_err:
                return {"source": "host", "fields": _wiz.normalize_character_fields(payload)}
        except Exception as e:  # noqa: BLE001 — fall back below
            host_err = str(e)
        logger.warning(f"vnccs character_wizard host path failed: {host_err}")
    if body.backend == "host":
        raise HTTPException(status_code=502, detail=f"Host Character Wizard failed: {host_err or 'no host'}")

    urls, text_model, _vis = await _ollama_cfg(session)
    if not urls or not text_model:
        raise HTTPException(status_code=503, detail=(
            f"Host wizard unavailable ({host_err or 'no host'}) and Ollama is not configured "
            "(Settings -> Ollama URL + model)."))
    tag_options: dict = {}
    if host:
        try:
            tags_data = await asyncio.to_thread(_client(host, timeout=30).get_tags)
            tag_options = _wiz.extract_character_tag_options(tags_data)
        except Exception:  # noqa: BLE001 — tags are an enrichment, not required
            tag_options = {}
    sys_p, usr_p = _wiz.character_wizard_prompts(desc, tag_options)
    content = await asyncio.to_thread(_wiz.ollama_chat_sync, urls, text_model, sys_p, usr_p, None, 0.3)
    fields = _wiz.parse_character_wizard_output(content or "")
    if fields is None:
        raise HTTPException(status_code=502, detail="Ollama Character Wizard returned unparseable output.")
    return {"source": "ollama", "fields": fields}


@router.post("/wizard/clothes")
async def wizard_clothes(body: WizardIn, request: Request,
                         session: AsyncSession = Depends(get_session)):
    desc = body.description.strip()
    if not desc:
        raise HTTPException(status_code=400, detail="No clothes description provided")
    host, _ = await _resolve_host(request, session)
    host_err: Optional[str] = None

    if body.backend in ("auto", "host") and host:
        try:
            payload = await asyncio.to_thread(
                _client(host, timeout=_wiz.WIZARD_HOST_TIMEOUT).post_json,
                "clothes_wizard", {"description": desc}, _wiz.WIZARD_HOST_TIMEOUT)
            host_err = _wizard_host_error(payload)
            if not host_err:
                return {"source": "host", "fields": _wiz.normalize_clothes_fields(payload)}
        except Exception as e:  # noqa: BLE001 — fall back below
            host_err = str(e)
        logger.warning(f"vnccs clothes_wizard host path failed: {host_err}")
    if body.backend == "host":
        raise HTTPException(status_code=502, detail=f"Host Clothes Wizard failed: {host_err or 'no host'}")

    urls, text_model, _vis = await _ollama_cfg(session)
    if not urls or not text_model:
        raise HTTPException(status_code=503, detail=(
            f"Host wizard unavailable ({host_err or 'no host'}) and Ollama is not configured "
            "(Settings -> Ollama URL + model)."))
    sys_p, usr_p = _wiz.clothes_wizard_prompts(desc)
    content = await asyncio.to_thread(_wiz.ollama_chat_sync, urls, text_model, sys_p, usr_p, None, 0.35)
    fields = _wiz.parse_clothes_wizard_output(content or "")
    if fields is None:
        raise HTTPException(status_code=502, detail="Ollama Clothes Wizard returned unparseable output.")
    return {"source": "ollama", "fields": fields}


@router.post("/wizard/clone-analyze")
async def wizard_clone_analyze(body: CloneAnalyzeIn, request: Request,
                               session: AsyncSession = Depends(get_session)):
    img = body.image or {}
    ref_list = [i for i in (body.images or []) if i and i.get("name")]
    if not img.get("name") and ref_list:
        img = ref_list[0]
    if not img.get("name"):
        raise HTTPException(status_code=400, detail="No uploaded image reference provided")
    multi = len(ref_list) > 1
    host, _ = await _resolve_host(request, session)
    if not host:
        raise HTTPException(status_code=503, detail="No VNCCS host available (the reference lives on the host).")
    host_err: Optional[str] = None

    if body.backend in ("auto", "host") and not multi:
        try:
            payload = await asyncio.to_thread(
                _client(host, timeout=_wiz.WIZARD_HOST_TIMEOUT).post_json,
                "cloner_auto_generate", {"image_name": img}, _wiz.WIZARD_HOST_TIMEOUT)
            host_err = _wizard_host_error(payload)
            if not host_err:
                return {"source": "host",
                        "fields": _wiz.normalize_character_fields(payload, include_clone_extras=True)}
        except Exception as e:  # noqa: BLE001 — fall back below
            host_err = str(e)
        logger.warning(f"vnccs cloner_auto_generate host path failed: {host_err}")
    if body.backend == "host":
        raise HTTPException(status_code=502, detail=f"Host Cloner analyze failed: {host_err}")

    urls, _text, vision_model = await _ollama_cfg(session)
    if not urls or not vision_model:
        raise HTTPException(status_code=503, detail=(
            f"Host analyze unavailable ({host_err or 'skipped'}) and Ollama vision is not configured "
            "(Settings -> Ollama vision model)."))
    ref_imgs = (ref_list or [img])[:4]
    # Analyze each reference ONE AT A TIME then resolve the fields across them.
    # Sending several images in a single vision call makes local Ollama models
    # emit rambling / truncated non-JSON (the "unparseable output" failure); one
    # image per call is reliable.  body/height favour the body/full-tagged shots
    # (a face close-up can't reveal them); a single bad image no longer sinks the
    # whole analyze — we only fail if EVERY image is unusable.
    from collections import Counter as _Counter
    items: list = []  # (b64, role)
    for rimg in ref_imgs:
        try:
            rb = await asyncio.to_thread(
                _client(host, timeout=60).view_image,
                rimg.get("name", ""), rimg.get("subfolder", "") or "",
                rimg.get("type", "input") or "input", 60)
            _role = str((rimg or {}).get("role") or "full").strip().lower()
            items.append((_wiz.image_bytes_to_b64(rb),
                          _role if _role in ("face", "body", "full") else "full",
                          str((rimg or {}).get("name") or "")))
        except VNCCSError as e:
            logger.warning("clone-analyze: could not fetch %s: %s", rimg.get("name"), e)
    if not items:
        raise HTTPException(status_code=502, detail="Could not fetch any reference image from the host.")

    # STAGE 1 — the VISION model DESCRIBES each image in prose (reliable), stored
    # per image as its "Vision Scan Data".  STAGE 2 — a TEXT model SYNTHESISES the
    # structured fields from those descriptions (text models handle JSON far better
    # than local vision models, which was the "unparseable output" failure).
    vision: list = []      # [{name, role, description}] for the UI
    described: list = []   # [(role, description)] for synthesis
    for _idx, (_b64, _role, _name) in enumerate(items):
        try:
            desc = await asyncio.to_thread(
                _wiz.ollama_chat_sync, urls, vision_model,
                _wiz.CLONE_VISION_DESCRIBE_SYSTEM, _wiz.CLONE_VISION_DESCRIBE,
                [_b64], 0.2, 180.0, False)  # json_format=False -> free-form prose
        except Exception as e:  # noqa: BLE001 — tolerate a single bad image
            logger.warning("clone-analyze: describe image %d failed: %s", _idx, e)
            desc = None
        if desc and desc.strip():
            described.append((_role, desc.strip()))
            vision.append({"name": _name, "role": _role, "description": desc.strip()})
    if not described:
        raise HTTPException(status_code=502,
                            detail="The vision model returned no usable description for any reference.")

    # STAGE 2: synthesise the fields from the descriptions (prefer the text model,
    # fall back to the vision model for the text-only synthesis if none is set).
    synth_model = _text or vision_model
    fields = None
    try:
        synth_prompt = _wiz.build_clone_synthesis_prompt(described)
        sc = await asyncio.to_thread(
            _wiz.ollama_chat_sync, urls, synth_model,
            _wiz.CLONE_SYNTHESIZE_SYSTEM, synth_prompt, None, 0.2, 180.0, True)
        fields = _wiz.parse_clone_analyze_output(sc or "")
    except Exception as e:  # noqa: BLE001
        logger.warning("clone-analyze: synthesis failed: %s", e)
        fields = None
    if fields is not None:
        return {"source": "ollama", "fields": fields,
                "analyzed": len(described), "vision": vision}

    # FALLBACK: synthesis didn't yield JSON — ask the vision model for per-image
    # JSON directly and merge (the previous behaviour), still returning the
    # descriptions we already have so the Vision Scan Data is available.
    logger.warning("clone-analyze: synthesis unparseable — falling back to per-image JSON")
    per_image: list = []   # parsed field dicts, aligned with ``roles``
    roles: list = []
    for _idx, (_b64, _role, _name) in enumerate(items):
        try:
            content = await asyncio.to_thread(
                _wiz.ollama_chat_sync, urls, vision_model,
                _wiz.CLONE_ANALYZE_SYSTEM, _wiz.CLONE_ANALYZE_INSTRUCTION, [_b64], 0.2)
            parsed = _wiz.parse_clone_analyze_output(content or "")
        except Exception as e:  # noqa: BLE001 — tolerate a single bad image
            logger.warning("clone-analyze: image %d failed: %s", _idx, e)
            parsed = None
        if parsed:
            per_image.append(parsed)
            roles.append(_role)
    if not per_image:
        raise HTTPException(status_code=502, detail="Ollama clone analyze returned unparseable output.")
    if len(per_image) == 1:
        return {"source": "ollama", "fields": per_image[0], "analyzed": 1, "vision": vision}

    def _pick(vals: list) -> str:
        nz = [str(v).strip() for v in vals if v and str(v).strip()]
        if not nz:
            return ""
        c = _Counter(nz)
        return max(nz, key=lambda v: (c[v], len(v)))  # most agreed-on, then most detailed

    def _pick_roled(key: str, prefer: tuple) -> str:
        pri = [per_image[i].get(key, "") for i in range(len(per_image)) if roles[i] in prefer]
        return _pick(pri) or _pick([r.get(key, "") for r in per_image])

    merged: dict = {}
    for k in ("race", "skin_color", "hair", "eyes", "face", "aesthetics"):
        merged[k] = _pick([r.get(k, "") for r in per_image])
    merged["body"] = _pick_roled("body", ("body", "full"))
    merged["height"] = _pick_roled("height", ("body", "full"))
    # additional_details: union the distinct tags seen across every image
    _tags: list = []
    _seen: set = set()
    for r in per_image:
        for t in str(r.get("additional_details", "")).split(","):
            t = t.strip()
            if t and t.lower() not in _seen:
                _seen.add(t.lower())
                _tags.append(t)
    merged["additional_details"] = ", ".join(_tags)
    _sexes = [r.get("sex", "") for r in per_image if r.get("sex")]
    merged["sex"] = _Counter(_sexes).most_common(1)[0][0] if _sexes else "female"
    _ages = [r.get("age") for r in per_image if isinstance(r.get("age"), int)]
    merged["age"] = _Counter(_ages).most_common(1)[0][0] if _ages else 18
    _nsfws = [bool(r.get("nsfw")) for r in per_image]
    merged["nsfw"] = sum(_nsfws) > len(_nsfws) / 2.0
    merged = _wiz.normalize_character_fields(merged, include_clone_extras=True)
    return {"source": "ollama", "fields": merged, "analyzed": len(per_image), "vision": vision}


@router.get("/catalog")
async def catalog(session: AsyncSession = Depends(get_session)):
    """List ingested VNCCS Native characters (name, outputs, hero) for reuse."""
    return await list_catalog(session)


@router.get("/catalog/{character_id}/images")
async def catalog_images(character_id: str, session: AsyncSession = Depends(get_session)):
    """All ingested images for a cataloged character, grouped by output label,
    with app-served URLs — powers the VNCCS Native editor view (reopen a
    character and see everything it has generated so far)."""
    from uuid import UUID as _UUID
    from backend.database.models import StudioCharacter, Asset
    try:
        cid = _UUID(character_id)
    except Exception:
        raise HTTPException(status_code=400, detail="invalid character_id")
    c = await session.get(StudioCharacter, cid)
    if c is None:
        raise HTTPException(status_code=404, detail="character not found")
    v = (c.manifest or {}).get("vnccs") or {}
    outputs = {k: [str(x) for x in (ids or [])] for k, ids in (v.get("outputs") or {}).items()}

    # Self-heal: releases before 1.62.0 REPLACED each step's output list on every
    # ingest (and parallel chunks raced), orphaning earlier batches' asset ids.
    # The files + Asset rows still exist — rediscover everything under this
    # character's asset folder and merge it back into the manifest.
    healed = False
    try:
        from sqlalchemy import or_ as _or
        from backend.services.character_studio.vnccs_native.ingest import (
            _safe as _safe_name, _studio_project)
        proj = await _studio_project(session)
        pref = f"assets/vnccs/{_safe_name(c.name)}/"
        rows = (await session.execute(
            select(Asset).where(Asset.project_id == proj.id).where(
                _or(Asset.rel_path.like(pref + "%"),
                    Asset.rel_path.like(pref.replace("/", "\\") + "%"))
            ))).scalars().all()
        rows.sort(key=lambda x: str(getattr(x, "created_at", "") or ""))
        for a in rows:
            mv = (a.meta or {}).get("vnccs") or {}
            st, lb = mv.get("step"), mv.get("label")
            if not st or not lb:
                continue  # base/costume previews live in base_versions/costumes
            key = f"{st}/{lb}"
            lst = outputs.setdefault(key, [])
            if str(a.id) not in lst:
                lst.append(str(a.id))
                healed = True
    except Exception:  # healing is best-effort; never break the editor view
        logger.exception("catalog_images: self-heal scan failed")
    if healed:
        _manifest = dict(c.manifest or {})
        _vn = dict(_manifest.get("vnccs") or {})
        _vn["outputs"] = outputs
        _manifest["vnccs"] = _vn
        c.manifest = _manifest
        session.add(c)
        await session.commit()

    def _rank(label: str) -> int:
        # finals first (BG-removed sprites/sheet), intermediates last
        if "sprites" in label or label.endswith("sheet"):
            return 0
        if "faces" in label:
            return 2
        return 1

    out = []
    for label in sorted(outputs.keys(), key=_rank):
        imgs = []
        for aid in (outputs.get(label) or [])[:80]:
            try:
                a = await session.get(Asset, _UUID(str(aid)))
            except Exception:
                a = None
            if a is None:
                continue
            rel = str(a.rel_path).replace("\\", "/")
            mv = (a.meta or {}).get("vnccs") or {}
            imgs.append({"asset_id": str(a.id), "url": f"/api/files/{a.project_id}/{rel}",
                         "base_version": mv.get("base_version"),
                         "costume": mv.get("costume")})
        if imgs:
            out.append({"label": label, "images": imgs})
    return {"character_id": str(c.id), "name": c.name, "form": v.get("form"),
            "hosts": v.get("hosts") or [], "outputs": out,
            "base_versions": v.get("base_versions") or [],
            "active_base": v.get("active_base"),
            "costumes": v.get("costumes") or {},
            "create_mode": v.get("create_mode"),
            "clone": v.get("clone"),
            "emotion_runs": v.get("emotion_runs") or [],
            "pose_runs": v.get("pose_runs") or []}


@router.delete("/catalog/{character_id}")
async def delete_catalog_character(character_id: str, request: Request,
                                   from_hosts: bool = False,
                                   session: AsyncSession = Depends(get_session)):
    """Delete a cataloged VNCCS character: the StudioCharacter row plus every
    app-side asset it owns (generated poses, base/costume previews).  With
    ``?from_hosts=true`` also deletes the character's folder on the recorded
    VNCCS workers (the node UI's DEL button, POST /vnccs/delete)."""
    from pathlib import Path as _Path
    from uuid import UUID as _UUID
    from backend.config import settings as _cfg
    from backend.database.models import Asset, StudioCharacter
    try:
        cid = _UUID(character_id)
    except Exception:
        raise HTTPException(status_code=400, detail="invalid character_id")
    c = await session.get(StudioCharacter, cid)
    if c is None:
        raise HTTPException(status_code=404, detail="character not found")
    v = (c.manifest or {}).get("vnccs") or {}
    name = str(v.get("ref") or c.name or "").strip()

    # every app asset this character owns (pose runs + base/costume previews)
    ids: set = set()
    for lst in (v.get("outputs") or {}).values():
        ids.update(str(x) for x in (lst or []))
    for bv in (v.get("base_versions") or []):
        if isinstance(bv, dict) and bv.get("asset_id"):
            ids.add(str(bv["asset_id"]))
    for entry in (v.get("costumes") or {}).values():
        for ver in (entry or {}).get("versions") or []:
            if isinstance(ver, dict) and ver.get("asset_id"):
                ids.add(str(ver["asset_id"]))
    removed = 0
    for aid in ids:
        try:
            a = await session.get(Asset, _UUID(aid))
        except Exception:
            a = None
        if a is None:
            continue
        rel = str(a.rel_path).replace("\\", "/")
        pid = str(a.project_id)
        p = (_Path(_cfg.project_dir) / rel if rel.startswith(pid + "/")
             else _Path(_cfg.project_dir) / pid / rel)
        try:
            if p.exists():
                p.unlink()
        except Exception:  # noqa: BLE001
            logger.warning("delete character: could not remove file %s", p)
        await session.delete(a)
        removed += 1

    hosts_result: list = []
    if from_hosts and name:
        _, st = await _resolve_host(request, session)
        comfy = getattr(request.app.state, "comfy_dispatcher", None)
        configured = ((st.studio_vnccs_host or "") if st else "").rstrip("/")
        pool = list_vnccs_hosts(comfy, configured or None) or []
        targets = [h for h in (v.get("hosts") or []) if h] or pool
        if configured:
            targets = targets + [configured]

        def _host_delete(hh: str):
            cl = _client(hh, timeout=60)
            r = cl.session.post(hh.rstrip("/") + "/vnccs/delete", json={"name": name},
                                headers={"X-VNCCS-CSRF": "1"}, timeout=60)
            return r.status_code

        for h in dict.fromkeys(x.rstrip("/") for x in targets):
            try:
                code = await asyncio.to_thread(_host_delete, h)
                hosts_result.append({"host": h, "status": code})
            except Exception as e:  # noqa: BLE001
                hosts_result.append({"host": h, "status": "error", "error": str(e)})

    await session.delete(c)
    await session.commit()
    return {"ok": True, "assets_removed": removed, "hosts": hosts_result}


@router.delete("/catalog/{character_id}/images/{asset_id}")
async def delete_catalog_image(character_id: str, asset_id: str,
                               session: AsyncSession = Depends(get_session)):
    """Remove one generated image from a character's library — drops the
    manifest entry, the Asset row and the file on disk, so the user can prune
    bad poses while building out a character."""
    from pathlib import Path as _Path
    from uuid import UUID as _UUID
    from backend.config import settings as _cfg
    from backend.database.models import Asset, StudioCharacter
    try:
        cid = _UUID(character_id)
        aid = _UUID(asset_id)
    except Exception:
        raise HTTPException(status_code=400, detail="invalid id")
    c = await session.get(StudioCharacter, cid)
    if c is None:
        raise HTTPException(status_code=404, detail="character not found")
    manifest = dict(c.manifest or {})
    vn = dict(manifest.get("vnccs") or {})
    outputs = {k: [str(x) for x in (ids or [])] for k, ids in (vn.get("outputs") or {}).items()}
    changed = False
    for k in list(outputs.keys()):
        if asset_id in outputs[k]:
            outputs[k] = [x for x in outputs[k] if x != asset_id]
            changed = True
        if not outputs[k]:
            outputs.pop(k)
    vn["outputs"] = outputs
    if vn.get("hero_asset_id") == asset_id:
        vn["hero_asset_id"] = next(
            (ids[0] for lb, ids in outputs.items()
             if ids and ("sprites" in lb or lb.endswith("sheet"))),
            next((ids[0] for ids in outputs.values() if ids), None))
        changed = True
    a = await session.get(Asset, aid)
    if a is not None:
        rel = str(a.rel_path).replace("\\", "/")
        pid = str(a.project_id)
        p = (_Path(_cfg.project_dir) / rel if rel.startswith(pid + "/")
             else _Path(_cfg.project_dir) / pid / rel)
        try:
            if p.exists():
                p.unlink()
        except Exception:
            logger.warning("delete_catalog_image: could not remove file %s", p)
        await session.delete(a)
        changed = True
    if not changed:
        raise HTTPException(status_code=404, detail="image not found on this character")
    manifest["vnccs"] = vn
    c.manifest = manifest
    session.add(c)
    await session.commit()
    return {"ok": True, "asset_id": asset_id}


class LinkIn(BaseModel):
    character_id: str
    project_id: str
    labels: Optional[list] = None
    max_per_label: int = 0


@router.post("/link")
async def link(body: LinkIn, session: AsyncSession = Depends(get_session)):
    """Copy a cataloged VNCCS character's images into a project as CHARACTER
    reference assets (so it can be used in that project's scenes)."""
    from uuid import UUID as _UUID
    try:
        cid = _UUID(body.character_id); pid = _UUID(body.project_id)
    except Exception:
        raise HTTPException(status_code=400, detail="invalid id")
    try:
        return await link_to_project(session, character_id=cid, project_id=pid,
                                     labels=body.labels, max_per_label=body.max_per_label)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.exception("VNCCS link failed")
        raise HTTPException(status_code=500, detail=str(e))
