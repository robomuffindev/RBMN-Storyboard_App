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
    # v1.172 Simple pose mode: per-run klein_* settings overrides merged over the
    # saved studio settings for THIS run only (queue-safe -- serialized with the
    # job's generate_in payload). The Simple recipe rides here so switching modes
    # never clobbers the user's saved Advanced dials.
    settings_overrides: Optional[dict] = None
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
    # Per-character output canvas (Klein base + pose sprites).  When set these
    # win over the global klein_canvas_width / _height so a round/wide character
    # can use a wider frame without changing everyone's default.  Clamped
    # 512..1536 (multiple of 16) backend-side.
    canvas_w: Optional[int] = None
    canvas_h: Optional[int] = None
    # Consistent skin/lighting across a pose SET: share ONE seed for every pose
    # (kills the per-pose colour/exposure drift) + a colour-lock prompt clause.
    # None = fall back to studio setting klein_consistent_skin (default off).
    consistent_skin: Optional[bool] = None


def _resolve_consistent_skin(settings: dict, body: "GenerateIn") -> bool:
    """Consistent-skin policy: body flag wins, else studio setting
    ``klein_consistent_skin`` (default OFF).  When on, every pose in a set shares
    one seed and the prompt pins skin tone / colour grading, so the set doesn't
    drift in complexion or exposure from pose to pose."""
    b = getattr(body, "consistent_skin", None)
    if b is not None:
        return bool(b)
    return str((settings or {}).get("klein_consistent_skin") or "off").strip().lower() \
        in ("on", "true", "1", "yes", "enabled")


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
                                lock_base: bool = False,
                                costume: Optional[str] = None) -> list:
    """Identity image(s) for a Klein pose run.

    Clone runs: up to 4 uploaded references, fed DIRECTLY as Klein reference
    latents (native multi-ref — replaces the Qwen source-grid trick).
    Create runs: the ACTIVE base version, else the newest cataloged final
    sprite.  Returns a non-empty list of PNG bytes or raises 409.

    ``costume`` (clothed pose SETS): use the active version of that costume — the
    DRESSED base render — as the single lock-base reference, so every pose
    reproduces the approved outfit (paired with base_clothing='keep')."""
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
                    _gm = bv.get("gen_meta") or {}
                    logger.info("klein identity: active base version %s%s resolved as reference",
                                active, " (UPSCALED copy)" if _gm.get("upscaled") else " (original render)")
                    return data
        return None

    async def _active_costume_bytes(cname: str) -> Optional[bytes]:
        """The active version's dressed image for costume ``cname`` (clothed poses)."""
        if char is None or not cname:
            return None
        v = (char.manifest or {}).get("vnccs") or {}
        entry = (v.get("costumes") or {}).get(cname) or {}
        active = entry.get("active")
        for cv in (entry.get("versions") or []):
            if isinstance(cv, dict) and cv.get("id") == active and cv.get("asset_id"):
                data = await _asset_bytes(cv["asset_id"])
                if data:
                    return data
        # fall back to whatever version has an asset if 'active' is stale
        for cv in reversed(entry.get("versions") or []):
            if isinstance(cv, dict) and cv.get("asset_id"):
                data = await _asset_bytes(cv["asset_id"])
                if data:
                    return data
        return None

    # CLOTHED POSE SET: dress every pose from the approved costume version (the
    # dressed base) — reference it as the single lock-base image; base_clothing=
    # 'keep' then reproduces the outfit on each pose.
    if costume:
        dressed = await _active_costume_bytes(costume)
        if dressed:
            logger.info("klein identity: CLOTHED SET -> dressed costume %r version is the reference", costume)
            return [dressed]
        logger.info("klein identity: clothed set requested but no saved version for costume %r "
                    "-> falling back to base/references", costume)

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


def _klein_canvas(settings: Optional[dict],
                  override_w=None, override_h=None) -> tuple:
    """Shared base + pose output canvas (width, height) so wide characters get
    consistent room and the base image matches the pose sprites in size.  Wider
    than the old 832x1216 default.  Multiples of 16 (Flux2), clamped 512..1536.
    Global default: klein_canvas_width / klein_canvas_height.  ``override_w`` /
    ``override_h`` (per-character, carried on the generation body) win when set,
    so a round character can use a wider canvas without changing the global."""
    s = settings or {}

    def _px(override, key: str, default: int) -> int:
        raw = override if (override not in (None, "", 0)) else s.get(key)
        try:
            v = int(raw or default)
        except Exception:  # noqa: BLE001
            v = default
        return (max(512, min(1536, v)) // 16) * 16

    return (_px(override_w, "klein_canvas_width", 1024),
            _px(override_h, "klein_canvas_height", 1216))


def _qwen_headwear_room(settings, override=None) -> float:
    """v1.199.13: reserved top headroom (fraction of canvas height) for the Qwen
    pose captures, so tall hats / headdresses have room to render before the top
    edge clips them. 0.14 = prior behavior; the 'Headwear room' slider raises it
    per costume. Clamped 0.0-0.45."""
    val = override
    if val in (None, ""):
        try:
            val = (settings or {}).get("qwen_headwear_room")
        except Exception:  # noqa: BLE001
            val = None
    try:
        hr = float(val) if val not in (None, "") else 0.14
    except Exception:  # noqa: BLE001
        hr = 0.14
    return max(0.0, min(0.45, hr))

def _resolve_base_mode(body, saved: Optional[dict]) -> str:
    """Resolve the base-preview mode -> 'single' | 'set' | 'mesh'.

    Precedence: explicit body.base_mode > legacy body.base_set bool > the saved
    klein_base_set setting > 'single'.  Kept permissive so old clients (which
    only send base_set) and the new 3-way selector both land correctly."""
    m = str(getattr(body, "base_mode", None) or "").strip().lower()
    if m in ("single", "front", "off", "one", "1v"):
        return "single"
    if m in ("set", "4", "4view", "4-view", "views", "on"):
        return "set"
    if m in ("mesh", "mesh-ready", "mesh_ready", "meshready", "3d"):
        return "mesh"
    bs = getattr(body, "base_set", None)
    if bs is True:
        return "set"
    if bs is False:
        return "single"
    sv = str((saved or {}).get("klein_base_set") or "").strip().lower()
    if sv in ("mesh", "mesh-ready", "mesh_ready", "meshready", "3d"):
        return "mesh"
    if sv in ("on", "set", "true", "1", "yes"):
        return "set"
    return "single"


def _klein_gen_meta(saved: Optional[dict], *, seed=None,
                    extra: Optional[dict] = None,
                    canvas_w=None, canvas_h=None) -> dict:
    """Snapshot of the Klein tunables that produced an image, stored per base /
    costume VERSION so switching back to a previous image shows exactly what made
    it (revert to what was working).  None values are dropped for a clean display."""
    s = saved or {}
    cw, ch = _klein_canvas(s, canvas_w, canvas_h)
    meta = {
        "seed": seed,
        "canvas": f"{cw}x{ch}",
        "body_adherence": s.get("klein_body_match_strength"),
        "strip_release": s.get("klein_refbase_ref_end"),
        "cleanup": s.get("klein_cleanup"),
        "steps": s.get("klein_steps"),
        "face_refine": s.get("klein_base_face_refine"),
        "face_refine_denoise": s.get("klein_base_face_refine_denoise"),
        "face_refine_steps": s.get("klein_base_face_refine_steps"),
        "pulid": s.get("klein_pulid"),
        "sam_cleanup": s.get("klein_sam_cleanup"),
        "lock_base": s.get("klein_lock_base"),
    }
    if extra:
        meta.update(extra)
    return {k: v for k, v in meta.items() if v is not None and v != ""}


def _klein_submit(host: str, st_settings: dict, body: GenerateIn,
                  pose_subset: list, identity_bytes: list, seed: int):
    """Render pose captures app-side, upload them + the identity image to the
    worker, assemble the Klein9b pose graph and submit.  Returns
    (prompt_id, tap_map).  Sync — run in a thread."""
    from backend.services.character_studio.vnccs_native import klein_poses, pose_render
    from backend.services.character_studio.vnccs_native.workflows import creator_baseline_pose_data

    # v1.172: per-run settings overrides (Simple pose mode recipe) win over the
    # saved studio settings for this run only -- works on BOTH the direct and
    # queued paths since the body is serialized into the job payload.
    _ov = getattr(body, "settings_overrides", None) or {}
    if _ov:
        st_settings = {**(st_settings or {}),
                       **{k: v for k, v in _ov.items() if v is not None}}
        logger.info("klein pose run: %d per-run settings override(s) applied (Simple mode)", len(_ov))
    oi = _object_info(host)
    models = klein_poses.resolve_klein_models(oi, st_settings)

    pd = creator_baseline_pose_data()
    pd["poses"] = [p for p in pose_subset if isinstance(p, dict)]
    # drive the pose MANNEQUIN's build from the character so the rendered pose
    # reference (which Klein reproduces as the body) matches the intended shape.
    pd["mesh"] = {**(pd.get("mesh") or {}),
                  **klein_poses.body_mesh_params(body.character_info or {})}
    # Shared base+pose CANVAS: render the pose capture at the same (wider) frame the
    # base image uses, so wide characters get consistent room and base/poses match
    # in size.  The pose-render clamps a wide figure to fit width, so a wider frame
    # stops plump/muscular/arms-out bodies from shrinking. Tunable via
    # klein_canvas_width / klein_canvas_height.
    _cw, _ch = _klein_canvas(st_settings, getattr(body, "canvas_w", None),
                             getattr(body, "canvas_h", None))
    pd["export"] = {**(pd.get("export") or {}), "view_width": _cw, "view_height": _ch}
    # v1.175 (B3): when enabled and the character has a MIA-rigged 3D body,
    # the pose references are CLAY RENDERS of the character's real body shape
    # instead of the generic mannequin -- ends the body-shape tug-of-war.
    captures = None
    _clay_used = False
    if (st_settings or {}).get("mesh3d_pose"):
        from backend.services.character_studio.vnccs_native import pose_clay
        captures = pose_clay.render_pose_clay_captures(body.character_name, pd)
        if captures and len(captures) == len(pd["poses"]):
            _clay_used = True
            logger.info("klein pose run: using 3D-body CLAY pose references (%d)", len(captures))
        else:
            captures = None
            logger.info("klein pose run: mesh3d_pose on but clay render unavailable -- mannequin fallback")
    if not captures:
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
    # v1.152: the face crop prefers the ORIGINAL photo reference over the
    # identity image.  In lock-base mode identity_bytes is the (rendered) base,
    # whose ~200px face is a soft second-generation copy -- cropping THAT is why
    # pose faces drifted while the base preview (which crops the real photos)
    # nailed the likeness.  The request still carries the character's stored
    # photo refs in cloner_images, so crop the real face from those when
    # available: prefer a 'face'-role ref, then 'full', then the first.
    _face_src = None
    if _ci:
        _pick_img = None
        for _pref in ("face", "full"):
            _pick_img = next((img for img in _ci if isinstance(img, dict) and img.get("name")
                              and str(img.get("role") or "").strip().lower() == _pref), None)
            if _pick_img is not None:
                break
        if _pick_img is None:
            _pick_img = next((img for img in _ci if isinstance(img, dict) and img.get("name")), None)
        if _pick_img is not None:
            try:
                _face_src = client.view_image(
                    _pick_img.get("name"), _pick_img.get("subfolder", "") or "",
                    _pick_img.get("type", "input") or "input", 120)
                logger.info("klein face crop: sourced from ORIGINAL photo reference %r "
                            "(role=%s)", _pick_img.get("name"), _pick_img.get("role") or "?")
            except Exception:  # noqa: BLE001
                _face_src = None
    if _face_src is None:
        _face_src = identity_bytes[_face_pick]
        logger.info("klein face crop: no photo reference available -> cropping the identity image")
    fc = _klein_identity_crop(_face_src, expand_pct=(0.2 if not _keep else 0.6))
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
        # v1.155: pose-local PuLID override -- tweak strength or force on/off for
        # pose SETS without touching the global ⚙ PuLID setting (same pattern as
        # the pose-local face refine).  klein_pose_pulid: '' = follow global,
        # 'on'/'off' override; klein_pose_pulid_strength overrides the strength.
        _pu_eff = dict(st_settings or {})
        _ppu = str((st_settings or {}).get("klein_pose_pulid") or "").strip().lower()
        if _ppu in ("off", "false", "0", "no", "disabled", "none"):
            _pu_eff["klein_pulid"] = "off"
        elif _ppu in ("on", "true", "1", "yes"):
            _pu_eff["klein_pulid"] = "on"
        _pps = str((st_settings or {}).get("klein_pose_pulid_strength") or "").strip()
        if _pps:
            _pu_eff["klein_pulid_strength"] = _pps
        if _ppu or _pps:
            logger.info("klein pose PuLID override: mode=%s strength=%s",
                        _ppu or "(global)", _pps or "(global)")
        pulid = klein_poses.resolve_pulid(oi, _pu_eff) if face_file else None
    if pulid:
        logger.info("klein: PuLID-Flux2 active (%s, strength %.2f)",
                    pulid["file"], pulid["strength"])
    # POSE-LOCAL face-refine settings (v1.147): pose SETS tune their own
    # FaceDetailer independently of the base preview (which keeps its base-local
    # overrides).  klein_pose_face_refine gates it ('' = follow the global
    # klein_face_refine, 'on' forces auto, 'off' disables for poses only);
    # denoise/steps/guide overrides fall back to the GLOBALS -- pose runs no
    # longer inherit the BASE-local values (decoupled by request: the base
    # generates fine, poses need their own knobs).
    _pfr = str((st_settings or {}).get("klein_pose_face_refine") or "").strip().lower()
    if _pfr in ("off", "false", "0", "no", "disabled", "none"):
        face_refine = None
        logger.info("klein pose face refine: OFF (pose-local)")
    else:
        _fr_eff = dict(st_settings or {})
        if _pfr in ("on", "auto", "yes", "1", "true"):
            _fr_eff["klein_face_refine"] = "auto"
        for _src, _dst in (("klein_pose_face_refine_denoise", "klein_face_refine_denoise"),
                           ("klein_pose_face_refine_steps", "klein_face_refine_steps"),
                           ("klein_pose_face_refine_guide", "klein_face_refine_guide")):
            _v = str((st_settings or {}).get(_src) or "").strip()
            if _v:
                _fr_eff[_dst] = _v
        face_refine = klein_poses.resolve_face_refine(oi, _fr_eff)
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
    _consistent_skin = _resolve_consistent_skin(st_settings, body)
    logger.info("klein base-outfit: mode=%s keep=%s face_kind=%s real_face=%s "
                "strip_body_refs=%s pulid=%s face_ref=%s", base_clothing, _keep,
                face_kind, real_face, strip_body_refs, bool(pulid), face_as_reference)
    # SKELETON pose input (v1.150): convert mannequin captures to DWPose
    # skeletons in-graph when klein_pose_input='skeleton' (needs DWPreprocessor
    # on the worker) -- pure pose geometry, no CGI style to leak.
    dwpose = klein_poses.resolve_dwpose(oi, st_settings)
    # v1.177: 3D clay refs already ARE the character's real body shape -- running
    # DWPose over them would throw that away and hand Klein a bare stick figure,
    # defeating the point of clay. Skip the skeleton conversion whenever clay is
    # in play (the clay capture is passed straight through as pose reference 1).
    if _clay_used and dwpose:
        dwpose = None
        logger.info("klein pose input: 3D clay refs active -> DWPose skeleton conversion skipped")
    _pose_input = "skeleton" if dwpose else "mannequin"
    logger.info("klein pose input: %s (clay=%s)", _pose_input, _clay_used)
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
            body_ref_active=body_ref_active, style_custom=style_custom,
            consistent_skin=_consistent_skin, pose_input=_pose_input))

    # honor the UI's upscaler control: any non-off mode = GAN tail (SeedVR has
    # no simple graph form; the label maps to GAN here)
    upcfg = ((body.generator_overrides or {}).get("upscaler") or {})
    up_mode = str(upcfg.get("mode") or "off").lower()
    upscale_model = None
    up_mp = None
    if up_mode != "off":
        upscale_model = klein_poses.resolve_upscale_model(oi, st_settings)
        logger.info("klein pose tail: upscaler mode=%s -> GAN model %r (SeedVR2 has no "
                    "in-graph form; use the gallery Upscale-all-poses for true SeedVR2)",
                    up_mode, upscale_model)
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
    neg_text = klein_poses.with_anatomy_negative(neg_text)  # suppress extra/duplicated limbs
    ksteps = klein_poses.resolve_klein_steps(_eff)
    logger.info("klein cleanup: cfg=%.2f steps=%d neg=%s", klein_cfg, ksteps, bool(neg_text))
    rmbg = klein_poses.resolve_rmbg(oi, st_settings)
    if rmbg:
        logger.info("klein: worker-side RMBG background removal active (%s, res %d)",
                    rmbg.get("model"), rmbg.get("process_res"))
    if body_ref_active:
        logger.info("klein body-match: ReferenceLatentPlus active — %d body ref(s) "
                    "masked (garment excluded), face on crop+PuLID", len(body_files))
    # POSE-REF RELEASE (v1.148): stop referencing the CGI mannequin capture for
    # the last part of sampling so its flat plastic texture can't stamp the
    # final skin.  klein_pose_ref_end (default 0.85); >=1.0 = off (old behavior).
    try:
        _pre = float(str((st_settings or {}).get("klein_pose_ref_end") or "0.85"))
    except Exception:  # noqa: BLE001
        _pre = 0.85
    _pre = max(0.3, min(1.0, _pre))
    _pose_ref_end = None if _pre >= 0.999 else _pre
    logger.info("klein pose-ref release: %s", ("off" if _pose_ref_end is None else f"{_pose_ref_end:.2f}"))
    try:
        _pls = float(str((st_settings or {}).get("klein_pose_lora_strength") or "1.0"))
    except Exception:  # noqa: BLE001
        _pls = 1.0
    _pls = max(0.1, min(1.5, _pls))
    logger.info("klein pose LoRA: %s @ %.2f", models.get("lora") or "NONE", _pls)
    _lora_lo = str(models.get("lora") or "").lower()
    if _pose_input == "skeleton" and "refcontrol" in _lora_lo:
        # thedeoxen/refcontrol-FLUX.2-klein-9B LoRA: lead with its trained trigger
        prompts = ["apply pose from image 1 with reference from image 2. " + pr for pr in prompts]
        logger.info("klein pose input: RefControl LoRA trigger phrase prepended")
    if "maching_pose" in _lora_lo or "matchingpose" in _lora_lo:
        # nhathoangfoto/Flux.2-Klein-9B-MatchingPose: photoreal mannequin->character
        # pose transfer; its trained trigger leads the prompt (use Mannequin input)
        prompts = ["matchingpose9b, " + pr for pr in prompts]
        logger.info("klein pose input: MatchingPose trigger prepended")
    _consistency = klein_poses.resolve_consistency_lora(oi, st_settings)
    if _consistency:
        logger.info("klein: consistency LoRA stacked (%s @ %.2f)",
                    _consistency["file"], _consistency["strength"])
    api, tap_map = klein_poses.build_klein_pose_graph(
        pose_files=pose_files,
        identity_files=graph_identity,
        prompts=prompts, seed=seed, models=models, steps=ksteps,
        pose_ref_end=_pose_ref_end,
        dwpose=dwpose,
        pose_lora_strength=_pls,
        consistency_lora=_consistency,
        upscale_model=upscale_model, upscale_megapixels=up_mp,
        face_file=face_file, pulid=pulid, face_refine=face_refine,
        strip_body_refs=strip_body_refs, face_as_reference=face_as_reference,
        negative_prompt=neg_text, cfg=klein_cfg, rmbg=rmbg,
        body_files=body_files, reflatentplus=reflatentplus,
        out_width=_cw, out_height=_ch,
        consistent_seed=_consistent_skin,
        filename_prefix=f"rbmn_vnccs/{safe}/klein_sprites")
    res = client.submit_prompt(api, timeout=120)
    extras = {"face_ref": bool(face_file),
              "pulid_file": (pulid or {}).get("file"),
              "pulid_strength": (pulid or {}).get("strength"),
              "face_refine": bool(face_refine)}
    return res.get("prompt_id"), tap_map, extras


def _qwen_submit(host: str, st_settings: dict, body: "GenerateIn",
                 pose_subset: list, dressed_bytes: bytes, seed: int,
                 canvas_w=None, canvas_h=None):
    """v1.167 Qwen (VNCCS-replica) clothed pose set: render pose mannequin
    captures app-side, upload them + the DRESSED costume image (alpha filled
    #00FF00 like VNCCS's fill_alpha_with_color), assemble the Pass-B
    ClothesGenerator graph via qwen_clothes and submit.  Sync -- run in a
    thread.  Returns (prompt_id, tap_map, extras)."""
    import io as _io
    import uuid as _uuid
    from PIL import Image as _Image
    from backend.services.character_studio.vnccs_native import klein_poses, pose_render, qwen_clothes
    from backend.services.character_studio.vnccs_native.workflows import creator_baseline_pose_data

    oi = _object_info(host)
    models = qwen_clothes.resolve_qwen_models(oi, st_settings)

    pd = creator_baseline_pose_data()
    pd["poses"] = [p for p in pose_subset if isinstance(p, dict)]
    pd["mesh"] = {**(pd.get("mesh") or {}),
                  **klein_poses.body_mesh_params(body.character_info or {})}
    _cw, _ch = _klein_canvas(st_settings, canvas_w, canvas_h)
    pd["export"] = {**(pd.get("export") or {}), "view_width": _cw, "view_height": _ch,
                    "top_headroom": _qwen_headwear_room(st_settings)}
    captures = pose_render.render_pose_captures(pd, False)
    if not captures or len(captures) != len(pd["poses"]):
        raise VNCCSError("app-side pose renderer unavailable (CharacterData missing?) -- "
                         "cannot build Qwen pose references")

    def _fill_green(data: bytes) -> bytes:
        im = _Image.open(_io.BytesIO(data))
        if im.mode == "RGBA":
            bg = _Image.new("RGB", im.size, (0, 255, 0))
            bg.paste(im, mask=im.split()[3])
            im = bg
        elif im.mode != "RGB":
            im = im.convert("RGB")
        buf = _io.BytesIO()
        im.save(buf, format="PNG")
        return buf.getvalue()

    safe = "".join(ch for ch in body.character_name if ch.isalnum())[:24] or "char"
    token = _uuid.uuid4().hex[:8]
    client = _client(host, timeout=120)
    dn = f"rbmn_qwen_{safe}_{token}_dressed.png"
    client.upload_image(dn, _fill_green(dressed_bytes), "", True, 120)
    pose_files = []
    for i, cap in enumerate(captures):
        fn = f"rbmn_qwen_{safe}_{token}_pose{i}.png"
        # render_pose_captures returns data-URL strings -> DECODE to PNG bytes before
        # upload (same fix as the clone preview); uploading the raw data-URL makes the
        # worker's LoadImage fail with "cannot identify image file ... pose{i}.png".
        # v1.199.6: headroom is now BAKED INTO the capture by pose_render (no more
        # dress-time pad), so hats carried from the dressed image have room to render.
        up = client.upload_image(fn, klein_poses.decode_capture(cap), "", True, 120)
        pose_files.append(up.get("name", fn))

    st = st_settings or {}
    def _f(key, default):
        try:
            v = st.get(key)
            return float(v) if v not in (None, "") else default
        except Exception:  # noqa: BLE001
            return default
    rmbg = klein_poses.resolve_rmbg(oi, st_settings)
    api, tap_map = qwen_clothes.build_qwen_pose_set_graph(
        pose_files=pose_files, dressed_file=dn, seed=seed, models=models,
        background=str(getattr(body, "background", None) or "Green"),
        pose_lora_strength=_f("qwen_pose_lora_strength", 1.0),
        target_size=int(_f("qwen_target_size", 1024)),
        steps=int(_f("qwen_steps", 4)), cfg=_f("qwen_cfg", 1.0),
        ref_weight=_f("qwen_ref_weight", 1.0),
        rmbg=rmbg,
        filename_prefix=f"rbmn_vnccs/{safe}/qwen_sprites")
    res = client.submit_prompt(api, timeout=120)
    logger.info("qwen clothes set: %d pose(s) on %s (lora=%s, seed=%d)",
                len(pose_files), host, models["pose_lora"], seed)
    return res.get("prompt_id"), tap_map, {}


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


def _comfy_error_detail(status: dict) -> Optional[str]:
    """Pull the REAL ComfyUI execution error (failing node + exception text) out of a
    history entry's status.messages, so callers surface the actual cause instead of a
    generic 'errored on the worker'.  Returns None if nothing useful is present."""
    try:
        for item in (status.get("messages") or []):
            if not (isinstance(item, (list, tuple)) and len(item) == 2):
                continue
            ev, data = item
            if ev == "execution_error" and isinstance(data, dict):
                nt = str(data.get("node_type") or data.get("class_type") or "?")
                msg = str(data.get("exception_message") or "").strip()
                if len(msg) > 400:
                    msg = msg[:400] + "…"
                return f"worker node {nt} failed: {msg}" if msg else f"worker node {nt} failed"
    except Exception:  # noqa: BLE001
        pass
    return None


def _wait_first_image_bytes(client, prompt_id: str, timeout_s: int = 600) -> bytes:
    """Poll a worker's history for ``prompt_id`` and return the first output
    image's raw bytes.  Sync — run in a thread.  On a worker execution error, raises
    with the ACTUAL failing node + message (not a generic label)."""
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
            status = entry.get("status") or {}
            if status.get("status_str") == "error":
                detail = _comfy_error_detail(status)
                raise VNCCSError(detail or "the worker errored during render (no detail reported)")
        _t.sleep(2)
    raise VNCCSError("the worker render timed out")


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
    _versions = [b for b in (v.get("base_versions") or []) if isinstance(b, dict)]
    bv = next((b for b in _versions if b.get("id") == active), None)
    if not bv:
        raise HTTPException(status_code=409,
                            detail="No active base version to upscale — generate a base preview first.")
    # ALWAYS upscale the ORIGINAL render, never an already-upscaled version, so
    # repeated Enhance clicks don't stack upscale-on-upscale (soft, blown-up
    # results).  If the active version is itself an upscale, resolve back to its
    # source original and upscale THAT.
    _src_id = ((bv.get("gen_meta") or {}).get("upscale_source"))
    if _src_id:
        _orig = next((b for b in _versions if b.get("id") == _src_id), None)
        if _orig:
            bv = _orig
    _base_src_id = bv.get("id")

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
    # tag the new version as an upscale of the ORIGINAL so a later Enhance resolves
    # back to this same source instead of upscaling the upscale
    _up_meta = {"upscaled": True, "upscale_source": _base_src_id,
                "upscale_method": used_method, "max_side": body.max_side}
    if len(outs) > 1:
        version = await save_base_preview(
            session, character_name=name,
            views=[{"view": vl, "image_b64": b} for vl, b, _u in outs],
            variant="klein", gen_meta=_up_meta)
    else:
        version = await save_base_preview(
            session, character_name=name, image_b64=outs[0][1],
            variant="klein", gen_meta=_up_meta)
    return {"version": version, "method": used_method, "views": len(outs)}


class PoseEnhanceIn(BaseModel):
    character_name: str
    asset_ids: list                            # pose sprite asset ids to upscale (1..N = whole set)
    method: Optional[str] = "gan"              # 'gan' | 'seedvr2'
    model: Optional[str] = None                # GAN model or '' / 'auto'
    sharpen: Optional[str] = "off"
    max_side: int = 2048


@router.post("/poses/enhance")
async def poses_enhance(body: PoseEnhanceIn, request: Request,
                        session: AsyncSession = Depends(get_session)):
    """AI-upscale one or more cataloged POSE sprites (same GAN/SeedVR2 path as the
    base Enhance) and save each as a NEW upscaled asset that PRESERVES the
    original.  Pass many ``asset_ids`` to upscale a whole set at once.

    Sourcing is always the ORIGINAL: if an id points at an already-upscaled
    sprite, it resolves back to the source original before upscaling, so repeated
    runs never stack upscale-on-upscale.  Any earlier upscale of the same pose is
    replaced, so the gallery keeps one upscale per pose.  Returns
    ``{results: [{src, asset_id, url, label, method}], count, failed}``."""
    import base64 as _b64  # noqa: F401  (parity with siblings; not required here)
    from uuid import UUID as _UUID
    from pathlib import Path as _Path
    from backend.config import settings as _cfg
    from backend.database.models import Asset, StudioCharacter
    from backend.services.character_studio.vnccs_native.ingest import save_pose_upscale

    host = await _need_host(request, session)
    name = body.character_name.strip()
    char = (await session.execute(
        select(StudioCharacter).where(StudioCharacter.name == name))).scalars().first()
    if char is None:
        raise HTTPException(status_code=404, detail=f"character {name!r} not found")
    ids = [str(x).strip() for x in (body.asset_ids or []) if str(x).strip()]
    if not ids:
        raise HTTPException(status_code=400, detail="No pose asset ids to upscale.")

    async def _asset(aid):
        try:
            return await session.get(Asset, _UUID(str(aid)))
        except Exception:  # noqa: BLE001
            return None

    def _read(a) -> Optional[bytes]:
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

    # resolve every requested id to its ORIGINAL sprite + read the source bytes
    jobs = []  # (requested_id, original_id, label, src_meta_vnccs, bytes)
    for aid in ids:
        a = await _asset(aid)
        if a is None:
            continue
        mv = (a.meta or {}).get("vnccs") or {}
        if mv.get("upscaled") and mv.get("upscale_source"):
            orig = await _asset(mv.get("upscale_source"))
            if orig is not None:
                a, mv = orig, (orig.meta or {}).get("vnccs") or {}
        st, lb = mv.get("step"), mv.get("label")
        if not st or not lb:
            continue  # not a pose sprite (base/costume previews live elsewhere)
        data = _read(a)
        if not data:
            continue
        jobs.append((aid, str(a.id), f"{st}/{lb}", mv, data))
    if not jobs:
        raise HTTPException(status_code=409,
                            detail="No readable pose sprites among the selected images.")

    try:
        oi = await asyncio.to_thread(_object_info, host)
    except VNCCSError as e:
        raise HTTPException(status_code=502, detail=str(e))

    def _run(data: bytes):
        return _enhance_image_bytes(host, oi, data, method=body.method,
                                    model=body.model, sharpen=body.sharpen,
                                    max_side=body.max_side)

    results, failed = [], 0
    for req_id, orig_id, label, mv, data in jobs:
        try:
            out, used = await asyncio.to_thread(_run, data)
        except Exception as e:  # noqa: BLE001
            logger.warning("pose enhance failed for %s: %s", orig_id, e)
            failed += 1
            continue
        try:
            saved = await save_pose_upscale(
                session, character_name=name, label=label, image_bytes=out,
                src_asset_id=orig_id, src_meta=mv, upscale_method=used)
            results.append({"src": req_id, "original": orig_id, **saved, "method": used})
        except Exception as e:  # noqa: BLE001
            logger.warning("pose upscale save failed for %s: %s", orig_id, e)
            failed += 1
    if not results:
        raise HTTPException(status_code=502, detail="Pose upscale produced no images.")
    return {"results": results, "count": len(results), "failed": failed}


class BaseRestyleIn(BaseModel):
    character_name: str
    style: Optional[str] = "photorealistic"   # Output-style key or 'custom'
    style_custom: Optional[str] = None         # free text when style == 'custom'
    style_ref: Optional[dict] = None           # {name,subfolder,type} style image on the host
    strength: float = 0.7                      # 0..1 content preservation (higher = keep more)
    use_realism_lora: bool = True              # stack anime2real-semi for realistic targets


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
    # photoreal restyle uses the anime->real LoRA on the ALREADY-rendered base (NOT
    # during base generation, which stays clean). Only for realistic target styles.
    _realism = klein_poses._resolve_realism_lora(oi, {**saved, "klein_realism_lora": "on"})
    if (_realism and body.use_realism_lora
            and style in ("photorealistic", "semi-realistic", "realistic", "photoreal", "real")):
        models["realism_lora"] = _realism
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
    _gm = _klein_gen_meta(saved, seed=seed,
                          extra={"engine": "klein-restyle", "style": label,
                                 "restyle_strength": strength})
    if len(outs) > 1:
        version = await save_base_preview(
            session, character_name=name,
            views=[{"view": vl, "image_b64": b} for vl, b in outs],
            variant="klein", gen_meta=_gm)
    else:
        version = await save_base_preview(
            session, character_name=name, image_b64=outs[0][1],
            variant="klein", gen_meta=_gm)
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


async def _qwen_emotion_workitems(session, body, saved, pinned):
    """Shared gather for Qwen emotions -> (work_paths, emo_specs, empty_sets): the
    character's engine-appropriate sprites across the selected sets (Base = base
    sprites; untagged legacy passes the qwen filter), plus the emotion prompt specs.
    Used by BOTH the direct path and the queue path."""
    from pathlib import Path as _Path
    from uuid import UUID as _UUID
    from sqlmodel import select as _select
    from backend.config import settings as _cfg
    from backend.database.models import Asset, StudioCharacter
    sets = [str(c) for c in (body.costumes or []) if c] or ["Base"]
    name = body.character_name.strip()
    char = (await session.execute(
        _select(StudioCharacter).where(StudioCharacter.name == name))).scalars().first()
    if char is None:
        raise HTTPException(status_code=404, detail=f"character {name!r} not in the catalog")
    v = (char.manifest or {}).get("vnccs") or {}
    outputs = v.get("outputs") or {}
    base_labels = ("creator/sprites", "cloner/sprites", "creator/sheet", "cloner/original_sprites")

    async def _paths_for(cset):
        want_base = cset.strip().lower() in ("base", "base sprites", "")
        got = []
        for label, ids in outputs.items():
            if not (("sprites" in label) or label.endswith("sheet")):
                continue
            if want_base and label not in base_labels:
                continue
            for aid in (ids or []):
                try:
                    a = await session.get(Asset, _UUID(str(aid)))
                except Exception:  # noqa: BLE001
                    a = None
                if a is None:
                    continue
                mv = (a.meta or {}).get("vnccs") or {}
                eng = str(mv.get("engine") or "").lower()
                if eng and eng != "qwen":
                    continue
                if not want_base and str(mv.get("costume") or "") != cset:
                    continue
                rel = str(a.rel_path).replace("\\", "/")
                pid = str(a.project_id)
                p = (_Path(_cfg.project_dir) / rel if rel.startswith(pid + "/")
                     else _Path(_cfg.project_dir) / pid / rel)
                if p.exists():
                    got.append(p)
        return got[:12]

    seen = set()
    work = []
    empty = []
    for cset in sets:
        paths = await _paths_for(cset)
        if not paths:
            empty.append(cset)
            continue
        for p in paths:
            sp = str(p)
            if sp not in seen:
                seen.add(sp)
                work.append(p)
    emotions_sel = [str(e) for e in (body.emotions or []) if e]
    emo_map = {}
    try:
        data = await asyncio.to_thread(_client(pinned, timeout=30).get_json, "get_emotions")
        for lst in (data or {}).values():
            for e in (lst or []):
                if isinstance(e, dict) and e.get("safe_name"):
                    emo_map[str(e["safe_name"])] = {
                        "key": str(e.get("key") or e["safe_name"]),
                        "natural": str(e.get("natural_prompt") or "")}
    except Exception:  # noqa: BLE001
        logger.warning("qwen emotions: get_emotions unavailable -- bare names")
    emo_specs = [{"key": emo_map.get(s, {}).get("key", s),
                  "natural": emo_map.get(s, {}).get("natural", "")} for s in emotions_sel]
    return work, emo_specs, empty


def _qwen_emotion_submit_one(host, st_settings, sprite_paths, emo_specs, seed):
    """Upload a batch of sprite files to ``host`` and submit ONE Qwen emotion graph
    (sprites x emotions) -> (prompt_id, tap_map). The queue dispatcher calls this per job."""
    from pathlib import Path as _Path
    import uuid as _u
    from backend.services.character_studio.vnccs_native import qwen_clothes
    st = st_settings or {}
    client = _client(host, timeout=120)
    models = qwen_clothes.resolve_qwen_models(_object_info(host), st)
    token = _u.uuid4().hex[:8]
    files = []
    for i, sp in enumerate(sprite_paths):
        fn = f"rbmn_qemo_{token}_{i}.png"
        client.upload_image(fn, _Path(sp).read_bytes(), "", True, 120)
        files.append(fn)
    graph, tap = qwen_clothes.build_qwen_emotion_graph(
        sprite_files=files, emotions=emo_specs, seed=int(seed), models=models,
        use_emotion_lora=bool(st.get("qwen_emotion_lora_on")),
        steps=int(st.get("qwen_steps") or 4), cfg=float(st.get("qwen_cfg") or 1.0),
        target_size=int(st.get("qwen_target_size") or 1024),
        filename_prefix="rbmn_vnccs/qwen_emotions")
    return client.submit_prompt(graph, timeout=120).get("prompt_id"), tap


async def _qwen_emotions_parallel(session, body: GenerateIn, saved: dict,
                                  eligible: list, pinned: str, gen_settings: dict):
    """Qwen (VNCCS) emotions: VNCCS_QWEN_Detailer "Change emotion to X" per
    (sprite x emotion), on the character's ENGINE-APPROPRIATE sprites for each
    selected costume set (or Base). Untagged/legacy sprites pass the filter.
    Returns the same {step, chunks, ...} contract as the Klein path so the
    frontend polls + ingests identically (engine tag flows from body.engine)."""
    import random as _random
    import uuid as _uuid2
    from pathlib import Path as _Path
    from uuid import UUID as _UUID
    from sqlmodel import select as _select
    from backend.config import settings as _cfg
    from backend.database.models import Asset, StudioCharacter
    from backend.services.character_studio.vnccs_native import qwen_clothes

    emotions_sel = [str(e) for e in (body.emotions or []) if e]
    if not emotions_sel:
        raise HTTPException(status_code=400, detail="Select at least one emotion")
    sets = [str(c) for c in (body.costumes or []) if c] or ["Base"]
    name = body.character_name.strip()
    char = (await session.execute(
        _select(StudioCharacter).where(StudioCharacter.name == name))).scalars().first()
    if char is None:
        raise HTTPException(status_code=404, detail=f"character {name!r} not in the catalog")
    v = (char.manifest or {}).get("vnccs") or {}
    outputs = v.get("outputs") or {}
    _base_labels = ("creator/sprites", "cloner/sprites", "creator/sheet", "cloner/original_sprites")

    async def _paths_for(costume_set: str):
        want_base = costume_set.strip().lower() in ("base", "base sprites", "")
        got = []
        for label, ids in outputs.items():
            if not (("sprites" in label) or label.endswith("sheet")):
                continue
            if want_base and label not in _base_labels:
                continue
            for aid in (ids or []):
                try:
                    a = await session.get(Asset, _UUID(str(aid)))
                except Exception:  # noqa: BLE001
                    a = None
                if a is None:
                    continue
                mv = (a.meta or {}).get("vnccs") or {}
                eng = str(mv.get("engine") or "").lower()
                if eng and eng != "qwen":          # engine filter (untagged legacy passes)
                    continue
                if not want_base and str(mv.get("costume") or "") != costume_set:
                    continue
                rel = str(a.rel_path).replace("\\", "/")
                pid = str(a.project_id)
                p = (_Path(_cfg.project_dir) / rel if rel.startswith(pid + "/")
                     else _Path(_cfg.project_dir) / pid / rel)
                if p.exists():
                    got.append(p)
        return got[:12]

    emo_map: dict = {}
    try:
        data = await asyncio.to_thread(_client(pinned, timeout=30).get_json, "get_emotions")
        for lst in (data or {}).values():
            for e in (lst or []):
                if isinstance(e, dict) and e.get("safe_name"):
                    emo_map[str(e["safe_name"])] = {
                        "key": str(e.get("key") or e["safe_name"]),
                        "natural": str(e.get("natural_prompt") or "")}
    except Exception:  # noqa: BLE001
        logger.warning("qwen emotions: get_emotions unavailable -- using bare emotion names")
    emo_specs = [{"key": emo_map.get(s, {}).get("key", s),
                  "natural": emo_map.get(s, {}).get("natural", "")} for s in emotions_sel]

    seed = int(gen_settings.get("seed") or 0) or (
        int(body.seed) if getattr(body, "seed", None) else _random.randint(1, 2_000_000_000))
    use_emo_lora = bool((saved or {}).get("qwen_emotion_lora_on"))
    steps = int((saved or {}).get("qwen_steps") or 4)
    cfg = float((saved or {}).get("qwen_cfg") or 1.0)
    tsize = int((saved or {}).get("qwen_target_size") or 1024)
    safe = "".join(ch for ch in name if ch.isalnum())[:24] or "char"

    # gather every engine-appropriate sprite across the selected sets (dedupe by path)
    seen_paths: set = set()
    work: list = []
    empty_sets: list = []
    for cset in sets:
        paths = await _paths_for(cset)
        if not paths:
            empty_sets.append(cset)
            continue
        for p in paths:
            sp = str(p)
            if sp not in seen_paths:
                seen_paths.add(sp)
                work.append(p)
    if not work:
        raise HTTPException(status_code=502,
                            detail="No Qwen/untagged sprites for " + ", ".join(sets)
                            + " -- generate poses/costumes in Qwen mode first.")

    # batch + fan out across ALL available workers (like the Qwen pose set): sprites
    # split into per_job batches, round-robin across hosts; each batch x every emotion
    # is one job on one worker -> real worker threading, not one big push.
    try:
        per_job = int((saved or {}).get("qwen_emotions_per_job") or 1)
    except Exception:  # noqa: BLE001
        per_job = 2
    per_job = max(1, min(8, per_job))
    hosts = list(eligible) or [pinned]
    batches = [work[i:i + per_job] for i in range(0, len(work), per_job)]
    chunks = [(hosts[bi % len(hosts)], b) for bi, b in enumerate(batches)]

    def _submit(h, paths):
        client = _client(h, timeout=120)
        models = qwen_clothes.resolve_qwen_models(_object_info(h), saved)
        token = _uuid2.uuid4().hex[:8]
        files = []
        for i, p in enumerate(paths):
            fn = f"rbmn_qemo_{safe}_{token}_{i}.png"
            client.upload_image(fn, p.read_bytes(), "", True, 120)
            files.append(fn)
        graph, tap = qwen_clothes.build_qwen_emotion_graph(
            sprite_files=files, emotions=emo_specs, seed=seed, models=models,
            use_emotion_lora=use_emo_lora, steps=steps, cfg=cfg, target_size=tsize,
            filename_prefix=f"rbmn_vnccs/{safe}/qwen_emotions")
        return client.submit_prompt(graph, timeout=120).get("prompt_id"), tap

    out = []
    errors = [f"{s}: no sprites" for s in empty_sets]
    for h, paths in chunks:
        try:
            pid, tap = await asyncio.to_thread(_submit, h, paths)
            out.append({"prompt_id": pid, "host": h, "tap_map": tap,
                        "label": f"{len(paths)}x{len(emo_specs)} emo * Qwen (VNCCS)",
                        "pose_count": len(paths) * len(emo_specs)})
        except (VNCCSError, ValueError) as e:
            logger.warning("qwen emotions: chunk on %s failed: %s", h, e)
            errors.append(f"{h}: {e}")
    if not out:
        raise HTTPException(status_code=502,
                            detail="Qwen emotions produced no runs: " + "; ".join(errors))
    logger.info("qwen emotions: %d chunk(s) across %d worker(s), %d sprite(s) x %d emotion(s)",
                len(out), len({h for h, _ in chunks}), len(work), len(emo_specs))
    return {"step": "emotions", "chunks": out, "errors": errors, "seed": seed, "engine": "qwen"}


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
    _is_klein = (body.engine or "").lower() == "klein"
    _is_qwen = (body.engine or "").lower() == "qwen"
    if step in ("clothes", "emotions") and not _is_klein and not _is_qwen:
        # native clothes/emotions FaceDetail the sprites that live on each worker's
        # own disk, so they must run on the recorded shard hosts.
        recorded = await _character_hosts(session, body.character_name.strip())
        eligible = [h for h in recorded if h in all_hosts] or [pinned]
    else:
        # klein AND qwen UPLOAD their sprites/refs to whatever worker runs the chunk,
        # so they fan out across every available worker (v1.199.19: qwen emotions/clothes).
        # Klein (incl. clothed sets) uploads the identity/dressed reference to each
        # worker, so it can fan out across ALL hosts like a creator run.
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

    if (body.engine or "").lower() == "qwen" and step == "emotions":
        return await _qwen_emotions_parallel(session, body, saved, eligible, pinned, gen_settings)
    if (body.engine or "").lower() == "klein" and step == "emotions":
        return await _klein_emotions_parallel(session, body, saved, eligible, pinned, gen_settings)

    if (body.engine or "").lower() == "klein":
        if step not in ("creator", "cloner", "clothes"):
            raise HTTPException(status_code=400,
                                detail="Klein engine currently covers pose generation, clothed sets and emotions")
        kposes = [p for p in (body.pose_set or []) if isinstance(p, dict)]
        if not kposes:
            raise HTTPException(status_code=400, detail="Select at least one pose for a Klein run")
        knames = [str(x) for x in (body.pose_names or [])]
        knames_ok = len(knames) == len(kposes)
        # CLOTHED POSE SET (step 'clothes'): reference the approved DRESSED costume
        # version and reproduce its outfit on every pose (base_clothing='keep').
        _kcostume = str(getattr(body, "costume_name", None) or "").strip() if step == "clothes" else None
        if _kcostume:
            body.base_clothing = "keep"      # force keep so the outfit is reproduced
        identity = await _klein_identity_bytes(session, body, pinned,
                                               _resolve_lock_base(saved, body) or bool(_kcostume),
                                               costume=_kcostume)
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
        # consistent skin/lighting: share ONE seed across all batches so the set
        # doesn't drift in complexion/exposure (else each batch offsets its seed).
        _consistent = _resolve_consistent_skin(saved, body)
        kchunks = [(eligible[bi % len(eligible)], b,
                    (nbatches[bi] if bi < len(nbatches) else None),
                    (kseed if _consistent else kseed + bi * per_job))
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

    if (body.engine or "").lower() == "qwen":
        # v1.167/1.168: app-side VNCCS-replica Qwen pose sets -- the suite's
        # exact Pass-B process (pose mannequin as image 1 + the character image
        # as image 2 through the QIE2511 PoseStudio LoRA), assembled by us so
        # the character does NOT need to exist in the worker-side store.
        # step 'clothes': image 2 = the ACTIVE dressed costume version.
        # step 'creator'/'cloner': image 2 = the ACTIVE base version (the t2i
        # render / the clone-preview render -- VNCCS's creator flow).
        if step not in ("clothes", "creator", "cloner"):
            raise HTTPException(status_code=400,
                                detail="Qwen engine covers pose sets (creator/cloner) and clothed sets")
        qposes = [p for p in (body.pose_set or []) if isinstance(p, dict)]
        if not qposes:
            raise HTTPException(status_code=400, detail="Select at least one pose for a Qwen run")
        qnames = [str(x) for x in (body.pose_names or [])]
        qnames_ok = len(qnames) == len(qposes)
        _qcostume = (str(getattr(body, "costume_name", None) or "").strip()
                     if step == "clothes" else None)
        if step == "clothes" and not _qcostume:
            raise HTTPException(status_code=409,
                                detail="Qwen clothed set needs a costume name -- make a costume preview first.")
        identity = await _klein_identity_bytes(session, body, pinned, True, costume=_qcostume)
        try:
            per_job = int(saved.get("qwen_poses_per_job") or saved.get("klein_poses_per_job") or 2)
        except Exception:  # noqa: BLE001
            per_job = 2
        per_job = max(1, min(8, per_job))
        qseed = int(gen_settings.get("seed") or 1)
        qbatches = [qposes[i:i + per_job] for i in range(0, len(qposes), per_job)]
        nqb = ([qnames[i:i + per_job] for i in range(0, len(qnames), per_job)]
               if qnames_ok else [None] * len(qbatches))
        # VNCCS seed policy: ONE seed for every pose -- costume identity comes
        # from image 2, not the seed.
        qchunks = [(eligible[bi % len(eligible)], b, (nqb[bi] if bi < len(nqb) else None))
                   for bi, b in enumerate(qbatches)]
        out = []
        errors = []
        for h, subset, cn in qchunks:
            try:
                prompt_id, tap_map, _qx = await asyncio.to_thread(
                    _qwen_submit, h, saved, body, subset, identity[0], qseed,
                    getattr(body, "canvas_w", None), getattr(body, "canvas_h", None))
                out.append({"prompt_id": prompt_id, "host": h, "tap_map": tap_map,
                            "label": f"{len(subset)} pose(s) · Qwen (VNCCS)",
                            "pose_count": len(subset), "pose_names": cn})
            except Exception as e:  # noqa: BLE001
                logger.warning(f"vnccs qwen parallel: chunk on {h} failed: {e}")
                errors.append({"host": h, "error": str(e)})
        if not out:
            raise HTTPException(status_code=502, detail=f"All Qwen chunks failed: {errors}")
        return {"step": step, "chunks": out, "errors": errors,
                "seed": gen_settings.get("seed"), "engine": "qwen"}

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
    if step in ("clothes", "emotions") and engine != "qwen":
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

    if engine == "qwen" and step == "emotions":
        # v1.199.20: Qwen emotions via the QUEUE -> cancel + retry + worker threading.
        # One job per sprite-batch; dispatcher (studio_pose_qwen) uploads + submits.
        work, emo_specs, _empty = await _qwen_emotion_workitems(session, body, saved, pinned)
        if not work:
            raise HTTPException(status_code=400,
                                detail="No Qwen/untagged sprites for the selected sets -- "
                                       "generate poses/costumes in Qwen mode first.")
        try:
            per_job = int(saved.get("qwen_emotions_per_job") or 1)
        except Exception:  # noqa: BLE001
            per_job = 2
        per_job = max(1, min(8, per_job))
        recipe = {"postprocess": None, "engine": "qwen",
                  "emotions": body.emotions, "costumes": body.costumes}
        for bi in range(0, len(work), per_job):
            batch = work[bi:bi + per_job]
            _mk_job({"workflow_type": "studio_pose_qwen", "step": "emotions", "engine": "qwen",
                     "chunk_index": bi // per_job, "seed": base_seed, "pin_host": None,
                     "sprite_paths": [str(p) for p in batch], "emotions_spec": emo_specs,
                     "ingest_recipe": dict(recipe)})
    elif is_klein:
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
        # consistent skin/lighting: one shared seed for the whole set (no offset)
        _consistent = _resolve_consistent_skin(saved, body)
        for bi, subset in enumerate(kbatches):
            _mk_job({"workflow_type": "studio_pose", "step": step, "engine": "klein",
                     "chunk_index": bi,
                     "seed": (base_seed if _consistent else base_seed + bi * per_job),
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
    # v1.176: three-way base mode -- 'single' (front only), 'set' (4-view), or
    # 'mesh' (🧊 Mesh-ready: locked A-pose, arms clear, plain gray bg, uniform
    # framing -- the preferred input for 3D mesh generation).  Wins over
    # base_set / klein_base_set when present.
    base_mode: Optional[str] = None
    # v1.180: worker override (the base-set runner fans views across workers) and
    # a single-view override -- {label, desc, mesh_ready} makes the single-base
    # path render EXACTLY that one view. Together these let the runner render the
    # front, then derive right/left/back as reference-edits of the front.
    host: Optional[str] = None
    view_override: Optional[dict] = None
    cleanup: Optional[str] = None            # 'off' | 'gentle' | 'strong'
    klein_steps: Optional[int] = None        # sampler steps (default 6)
    # Per-character base canvas (wins over global klein_canvas_width / _height).
    canvas_w: Optional[int] = None
    # v1.171 Settings-Variation-Test hooks: merge these over the saved studio
    # settings for THIS render only, and (varitest) skip cataloging the result
    # as a base version -- the test runner files it in its own folder instead.
    settings_overrides: Optional[dict] = None
    varitest: Optional[bool] = None
    canvas_h: Optional[int] = None


@router.post("/preview")
async def generate_preview(body: PreviewIn, request: Request,
                           session: AsyncSession = Depends(get_session)):
    """Generate the character ONCE in the default pose via the host's
    ``/vnccs/preview_generate`` (the panel's "Generate Preview" button) —
    fast single image, no pose sprites, no upscale.  Uses the vendored
    graph's working gen_settings baseline + saved overrides."""
    # v1.180: worker override lets the base-set runner target a specific worker.
    _host_ovr = str(getattr(body, "host", None) or "").strip().rstrip("/")
    host = _host_ovr or await _need_host(request, session)
    st = await _settings(session)
    saved = (st.studio_vnccs_settings if st else None) or {}
    if body.settings_overrides:
        saved = {**saved, **{k: v for k, v in body.settings_overrides.items() if v is not None}}

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
    # Values the gen_meta snapshot in the version-save block below needs.  They
    # MUST live in the OUTER scope: the nested runners each compute their own local
    # copies, so without these the save block NameError'd (on seed_v / bc /
    # face_kind / style_custom) and — caught silently — never wrote the base
    # version to the DB, so the preview count never moved and new previews never
    # joined the list.  The nested runners shadow these locally; harmless.
    seed_v = int(gs.get("seed") or 0) or 1
    face_kind = str(getattr(body, "face_kind", None) or "auto").strip().lower()
    style_custom = str(getattr(body, "style_custom", None) or "").strip()
    bc = str(getattr(body, "base_clothing", None) or saved.get("klein_base_clothing") or "strip")
    # v1.176: base mode (single | set | mesh) drives both the T2I and clone
    # preview runners AND is stamped into gen_meta so the version knows how it
    # was made (mesh3d prefers a mesh-ready / view set).
    _base_mode = _resolve_base_mode(body, saved)
    if (body.engine or "").lower() == "klein":
        # Klein-mode base preview: plain Klein 9B T2I from the tag sheet — the
        # identity source for every downstream Klein pose run.
        def _run_klein_preview():
            from backend.services.character_studio.vnccs_native import klein_poses
            oi = _object_info(host)
            models = klein_poses.resolve_klein_models(oi, saved, require_lora=False)
            seed_v = int(gs.get("seed") or 0) or 1
            _sk = str(getattr(body, "face_kind", None) or "auto")
            _sc = str(getattr(body, "style_custom", None) or "").strip()
            _nsfw = bool(getattr(body, "nsfw", False))
            _steps = klein_poses.resolve_klein_steps(saved)
            _rmbg = klein_poses.resolve_rmbg(oi, saved)
            safe = "".join(ch for ch in body.character_name if ch.isalnum())[:24] or "char"
            client = _client(host, timeout=120)
            # SINGLE (front-only) -- the classic fast path.  v1.180: a
            # view_override makes this render ONE specific view (used by the
            # base-set runner to make the anchor front, or a lone view).
            if _base_mode == "single":
                _vo = getattr(body, "view_override", None) or None
                if _vo:
                    prompt = klein_poses.klein_preview_prompt(
                        body.character_info or {}, body.background, nsfw=_nsfw,
                        style_kind=_sk, style_custom=_sc,
                        view_desc=str(_vo.get("desc") or ""),
                        mesh_ready=bool(_vo.get("mesh_ready")))
                    _cw2, _ch2 = _klein_canvas(saved, body.canvas_w, body.canvas_h)
                    graph, _tap = klein_poses.build_klein_t2i_graph(
                        prompt=prompt, seed=seed_v, models=models, width=_cw2, height=_ch2,
                        steps=_steps, rmbg=_rmbg, filename_prefix=f"rbmn_vnccs/{safe}/klein_preview")
                else:
                    prompt = klein_poses.klein_preview_prompt(
                        body.character_info or {}, body.background,
                        nsfw=_nsfw, style_kind=_sk, style_custom=_sc)
                    graph, _tap = klein_poses.build_klein_t2i_graph(
                        prompt=prompt, seed=seed_v, models=models, steps=_steps,
                        rmbg=_rmbg, filename_prefix=f"rbmn_vnccs/{safe}/klein_preview")
                res = client.submit_prompt(graph, timeout=120)
                return _klein_wait_first_image(client, res.get("prompt_id"),
                                               _klein_preview_timeout(_steps, 1))
            # SET / MESH -- render the SAME character from four angles (front/
            # right/left/back).  MESH mode locks a symmetric arms-clear A-pose on
            # a plain gray backdrop so Hunyuan3D gets clean multiview input; SET
            # keeps the relaxed neutral stance.  Returns the view-list format so
            # save_base_preview stores every view (mesh3d reads bv["views"]).
            import base64 as _b64k
            mesh_ready = (_base_mode == "mesh")
            _cw, _ch = _klein_canvas(saved, body.canvas_w, body.canvas_h)
            # v1.178: shared seed across the set pins skin/lighting so the views
            # match (klein_base_consistent_seed). No refs here (pure T2I), so the
            # Consistency LoRA doesn't apply; the seed is the lever we have.
            _share_seed = str(saved.get("klein_base_consistent_seed") or "").strip().lower() \
                in ("on", "true", "1", "yes")
            logger.info("klein base preview (t2i %s): rendering %d views at %dx%d (shared_seed=%s)",
                        _base_mode, len(_BASE_VIEW_SPEC), _cw, _ch, _share_seed)
            imgs_b64: list = []
            for _i, (_vlabel, _vdesc) in enumerate(_BASE_VIEW_SPEC):
                _vprompt = klein_poses.klein_preview_prompt(
                    body.character_info or {}, body.background, nsfw=_nsfw,
                    style_kind=_sk, style_custom=_sc, view_desc=_vdesc,
                    mesh_ready=mesh_ready)
                _g, _t = klein_poses.build_klein_t2i_graph(
                    prompt=_vprompt, seed=(seed_v if _share_seed else seed_v + _i), models=models,
                    width=_cw, height=_ch, steps=_steps, rmbg=_rmbg,
                    filename_prefix=f"rbmn_vnccs/{safe}/klein_preview")
                _r = client.submit_prompt(_g, timeout=120)
                _raw = _wait_first_image_bytes(_client(host, timeout=120),
                                               _r.get("prompt_id"),
                                               _klein_preview_timeout(_steps, 1))
                imgs_b64.append(_b64k.b64encode(_raw).decode("ascii"))
            # uniform framing across the whole set (kills wasted space / scale drift)
            try:
                from backend.services.character_studio.cutout import normalize_base_set
                _raw_set = [_b64k.b64decode(x) for x in imgs_b64]
                _framed = normalize_base_set(_raw_set)
                imgs_b64 = [_b64k.b64encode(x).decode("ascii") for x in _framed]
            except Exception:  # noqa: BLE001 — framing is best-effort
                logger.exception("klein base preview (t2i set): framing/normalize failed")
            return [{"view": _BASE_VIEW_SPEC[i][0], "image_b64": b}
                    for i, b in enumerate(imgs_b64)]

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
            # v1.176: single | set | mesh.  MESH renders the same 4-view set but
            # in a locked arms-clear A-pose on a plain gray backdrop (best 3D
            # mesh input); SET keeps the relaxed neutral stance.
            _want_set = _base_mode in ("set", "mesh")
            _mesh_ready = (_base_mode == "mesh")
            _views = BASE_VIEWS if _want_set else BASE_VIEWS[:1]
            # v1.180: a view_override renders exactly ONE view (the base-set
            # runner uses this to derive right/left/back from the front).
            _vo = getattr(body, "view_override", None) or None
            # v1.182: SET-DERIVATION mode -- the reference is the APPROVED FRONT and
            # we rotate it, keeping the character (incl. clothing) EXACTLY. NOT a
            # strip: hold the reference the whole way, skip SAM/strip negatives, and
            # use the rotate prompt. The front render itself is NOT a derive (derive
            # is only set for the right/left/back views by the base-set runner).
            _derive = bool(_vo.get("derive")) if _vo else False
            _strip_hard = bool(_vo.get("strip_hard")) if _vo else False
            if _vo:
                _mesh_ready = bool(_vo.get("mesh_ready"))
                _volabel = str(_vo.get("label") or "front").lower()
                _vorot = {"front": [0, 0, 0], "right": [0, 90, 0],
                          "left": [0, -90, 0], "back": [0, 180, 0]}.get(_volabel, [0, 0, 0])
                _views = [(_volabel, _vorot, str(_vo.get("desc") or ""))]
                _want_set = False
            _pv_bg = "neutral gray" if _mesh_ready else body.background
            _view_poses = []
            for _vlabel, _rot, _vp in _views:
                _pose = dict(_base_neutral)
                _pose["modelRotation"] = _rot
                if _mesh_ready:
                    _pose["prompt"] = ("standing in a symmetric A-pose, arms lowered and held "
                                       "out about 30 degrees away from the torso so the arms and "
                                       "hands are clearly separated from the body, hands open, "
                                       "legs straight and feet shoulder-width apart, full body "
                                       "visible head to toe, " + _vp)
                else:
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
            # derive = rotate the approved front; treat as keep (no strip negatives)
            neg_text, klein_cfg = klein_poses.resolve_strip_negative(_eff, keep_c or _derive)
            neg_text = klein_poses.with_anatomy_negative(neg_text)
            if _derive:
                # kill the directional-light look that shadows the far side on rotation,
                # and the front-reference duplication that renders TWO figures.
                neg_text = ((neg_text + ", ") if neg_text else "") + (
                    "squashed, stretched, vertically compressed, distorted proportions, "
                    "wrong proportions, deformed anatomy, warped body, "
                    "two people, two characters, two figures, duplicate person, duplicated body, "
                    "cloned character, twins, mirrored copy, extra person, multiple characters, "
                    "split image, diptych, side by side, two views in one image, collage, "
                    "shadow, shadows, cast shadow, drop shadow, contact shadow, self-shadow, "
                    "directional lighting, single light source, hard light, key light, rim light, "
                    "spotlight, dramatic lighting, chiaroscuro, high contrast, dark side, "
                    "darkened skin, shaded skin, underexposed, dim lighting, uneven lighting, vignette")
                # v1.187.3: the front reference kept "winning" on the side/back views,
                # reproducing the FRONT instead of turning. Push the render off the
                # frontal pose so the requested rotation actually happens.
                if _volabel in ("right", "left", "back"):
                    neg_text += (", front view, front-facing, facing the camera, "
                                 "facing forward, frontal pose, looking at the camera, "
                                 "chest and belly toward the camera, same pose as the reference")
                # v1.187.4: the BACK view kept the FRONT torso (breasts/belly) on a
                # back-oriented body. Suppress all front anatomy so the back shows the
                # back (shoulder blades, spine, buttocks), not a see-through front.
                if _volabel == "back":
                    neg_text += (", front of the body, front torso, chest, breasts, "
                                 "cleavage, nipples, belly, navel, belly button, abs, "
                                 "face, facial features, front of the face, "
                                 "front of the thighs, transparent body, see-through torso, "
                                 "front and back at once")
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
                    # match the POSE path's default (resolve_reflatentplus uses 1.6) so
                    # an unset value gives the base + poses the SAME body strength
                    _rb_strength = float(saved.get("klein_body_match_strength") or 1.6)
                except Exception:  # noqa: BLE001
                    _rb_strength = 1.6
                if _derive:
                    # rotation: a full-strength front reference makes the model REPRODUCE
                    # the front (and add the turned copy => "two fronts"). Ease it so
                    # identity carries but the body is free to actually turn.
                    _rb_strength = min(_rb_strength, 1.1)
                    # v1.187.4: the turned views were still coming out front-facing (or
                    # front-torso-on-a-back-body), so ease the front reference harder --
                    # more for the 90-degree profiles, still substantially for the back.
                    if _volabel in ("right", "left"):
                        _rb_strength = min(_rb_strength, 0.7)
                    elif _volabel == "back":
                        _rb_strength = min(_rb_strength, 0.85)
                elif _strip_hard:
                    # v1.187.4: a side/back REFERENCE PHOTO strip (not a rotation) held
                    # its clothing (pants/shoes) too hard. Ease the hold so the tail can
                    # actually strip, while the body-shape prompt keeps proportions.
                    _rb_strength = min(_rb_strength, 1.25)
                # release the body reference over the final steps so the tail of the
                # render can wipe residual on-skin accessories (wrist/neck jewelry) the
                # prompt asks to remove -- body shape is already locked by then. 1.0 =
                # hold full (old behavior); lower strips harder. Setting klein_refbase_ref_end.
                try:
                    _rb_end = float(saved.get("klein_refbase_ref_end") or 0.85)
                except Exception:  # noqa: BLE001
                    _rb_end = 0.85
                _rb_end = max(0.5, min(1.0, _rb_end))
                if _derive:
                    # release the front reference over the last third so the model can
                    # actually TURN the body instead of duplicating the front. Identity
                    # is already locked by then (crop + PuLID keep the face).
                    _rb_end = 0.65
                    # v1.187.4: release even earlier so the front can't keep re-imposing
                    # itself (front pose on profiles, front torso on the back view).
                    if _volabel in ("right", "left"):
                        _rb_end = 0.45
                    elif _volabel == "back":
                        _rb_end = 0.5
                elif _strip_hard:
                    # side/back reference strip: release earlier so pants/shoes come off.
                    _rb_end = min(_rb_end, 0.7)
                # base-local face refine: a per-base ON/OFF toggle + optional denoise
                # override, both falling back to the global face-refine settings. Uses
                # the same FaceDetailer (ultralytics detector) the pose runs already use.
                _use_base_fr = str(saved.get("klein_base_face_refine") or "on").strip().lower() \
                    not in ("off", "false", "0", "no", "disabled", "none")
                if _use_base_fr:
                    _fr_eff = dict(saved or {})
                    _bfd = str(saved.get("klein_base_face_refine_denoise") or "").strip()
                    if _bfd:
                        _fr_eff["klein_face_refine_denoise"] = _bfd
                    _bfs = str(saved.get("klein_base_face_refine_steps") or "").strip()
                    if _bfs:
                        _fr_eff["klein_face_refine_steps"] = _bfs
                    _base_face_refine = klein_poses.resolve_face_refine(oi, _fr_eff)
                else:
                    _base_face_refine = None
                # SAM3 article cleanup: segment leftover clothing/jewelry by text and
                # inpaint it to skin, so Strip release can stay high for max likeness.
                _sam_cleanup = None if _derive else klein_poses.resolve_sam3_cleanup(oi, saved)
                _cw, _ch = _klein_canvas(saved, body.canvas_w, body.canvas_h)  # per-char / shared canvas
                logger.info("klein refbase preview: rendering base at canvas %dx%d "
                            "(klein_canvas_width=%s, body.canvas_w=%s)", _cw, _ch,
                            saved.get("klein_canvas_width"), body.canvas_w)
                # v1.178: base-set consistency. (a) The SAME dx8152 Consistency
                # LoRA the pose sets use, gated by klein_base_consistency_lora, so
                # the views hold a matching look. (b) A shared seed across the set
                # (klein_base_consistent_seed) pins skin tone + lighting so the 4
                # views don't drift pose-to-pose -- like "consistent skin" for poses.
                _cons_lora = klein_poses.resolve_consistency_lora(oi, {
                    **saved,
                    "klein_consistency_lora": saved.get("klein_base_consistency_lora"),
                    "klein_consistency_lora_strength": saved.get("klein_base_consistency_lora_strength"),
                })
                # v1.188: character TURNAROUND / multi-view LoRA -- trained to rotate the
                # subject, applied ONLY to the DERIVED views (the rotations) so the front
                # anchor stays clean. No-op until the user installs + enables the LoRA.
                _turn_lora = klein_poses.resolve_turnaround_lora(oi, saved) if _derive else None
                if _turn_lora:
                    logger.info("klein refbase: turnaround LoRA %s @ %.2f on derived view %s",
                                _turn_lora.get("file"), _turn_lora.get("strength"), _volabel)
                _share_seed = _want_set and str(saved.get("klein_base_consistent_seed") or "").strip().lower() \
                    in ("on", "true", "1", "yes")
                imgs_b64 = []
                for _i, (_vlabel, _rot, _vp) in enumerate(_views):
                    _vprompt = klein_poses.klein_refbase_prompt(
                        body.character_info or {}, _pv_bg, nsfw=_nsfw,
                        view_desc=_vp, style_kind=face_kind, style_custom=style_custom, sex=_sex,
                        mesh_ready=_mesh_ready, rotate=_derive)
                    # turnaround LoRA trigger word (if any) goes at the START of the prompt
                    _tt = (_turn_lora or {}).get("trigger") if _turn_lora else ""
                    if _tt:
                        _vprompt = f"{_tt}, {_vprompt}"
                    _g, _t = klein_poses.build_klein_refbase_graph(
                        prompt=_vprompt, seed=(seed_v if _share_seed else seed_v + _i), models=models,
                        width=_cw, height=_ch,
                        body_files=pv_body_files, reflatentplus=reflatentplus,
                        strength=_rb_strength, body_ref_end=_rb_end, face_file=face_file,
                        pulid_image=_face_name, pulid=pulid,
                        face_refine=_base_face_refine, sam_cleanup=_sam_cleanup,
                        rmbg=rmbg_cfg, cfg=klein_cfg, negative_prompt=neg_text, steps=_ksteps,
                        consistency_lora=_cons_lora, turnaround_lora=_turn_lora,
                        # HARD strip (side/back ref photo): drop the clothing mask so the
                        # reference can't reproduce pants/shoes. Rotations keep the outfit.
                        keep_clothes_mask=(not _strip_hard),
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
                        (_vp or {}).get("prompt", ""), _pv_bg, len(names),
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
        if body.varitest:
            raise StopIteration  # varitest renders are filed by the test runner, not the catalog
        from backend.services.character_studio.vnccs_native.ingest import save_base_preview
        _is_klein = (body.engine or "").lower() == "klein"
        _gm = _klein_gen_meta(
            saved, seed=seed_v, canvas_w=body.canvas_w, canvas_h=body.canvas_h,
            extra={"engine": "klein-refbase" if _is_klein else "vnccs",
                   "base_clothing": bc,
                   "base_mode": _base_mode,
                   "style": (face_kind if (face_kind and face_kind != "auto")
                             else (style_custom or "auto"))}) if _is_klein else None
        version = await save_base_preview(
            session, character_name=body.character_name.strip(),
            image_b64=payload.get("image"), views=payload.get("views"),
            variant=("klein" if _is_klein else None), gen_meta=_gm)
    except StopIteration:
        pass
    except Exception as e:  # noqa: BLE001
        logger.warning(f"vnccs preview: base-version save failed: {e}")
    return {"image": payload.get("image"), "views": payload.get("views"), "version": version}


# --------------------------------------------------------------------------- #
# Base-set runner (v1.180): front-anchored, live, parallel, cancellable.
# Renders the FRONT first, then derives right/left/back (and the mesh A-pose) as
# Klein reference-EDITS of that front -- every view is a view of ONE approved
# image instead of an independent re-roll, so the set matches. Front renders on
# the pinned worker; the derived views fan across all online workers. The UI
# polls per-view status + streams each thumbnail as it lands; Stop keeps
# whatever finished as a partial base version.
# --------------------------------------------------------------------------- #
_BASE_SET_RUNS: dict = {}      # run_id -> state dict (in-memory; UI polls it)
_BASE_SET_TASKS: dict = {}     # run_id -> asyncio.Task (keeps a handle alive)


class BaseSetStartIn(BaseModel):
    character_name: str
    character_info: dict = {}
    base_mode: Optional[str] = "set"          # 'set' | 'mesh'
    engine: Optional[str] = "klein"
    cloner_images: Optional[list] = None       # clone: reference photos (front only)
    nsfw: bool = False
    background: str = "Green"
    face_kind: Optional[str] = None
    style_custom: Optional[str] = None
    base_clothing: Optional[str] = None
    canvas_w: Optional[int] = None
    canvas_h: Optional[int] = None
    seed: Optional[int] = None
    host: Optional[str] = None
    # v1.181: anchor the set on the ALREADY-APPROVED active base image instead of
    # rendering a fresh front -- guarantees the set's starting point is the one you
    # signed off on. 4-view reuses the approved front as view 0; mesh derives an
    # A-pose gray set FROM it (the approved base is the identity reference).
    use_active_base: Optional[bool] = False
    # v1.189: how the DERIVED views (right/left/back + mesh A-pose) are produced.
    #   'reference' (default) = rotate the front as a reference-edit (no LoRA).
    #   'matchpose'          = mannequin+MatchingPose rotation via the proven pose
    #                          path, with the approved (body-correct) base as the
    #                          identity so body shape is preserved for meshing.
    derive_method: Optional[str] = None


async def _active_base_front_bytes(session, name: str) -> Optional[bytes]:
    """Bytes of the character's ACTIVE base version front image (the approved
    base), for use as the set anchor. None if there's no readable base yet."""
    from uuid import UUID as _UUID
    from pathlib import Path as _Path
    from sqlmodel import select as _select
    from backend.config import settings as _cfg
    from backend.database.models import Asset, StudioCharacter
    char = (await session.execute(
        _select(StudioCharacter).where(StudioCharacter.name == name))).scalars().first()
    if char is None:
        return None
    v = (char.manifest or {}).get("vnccs") or {}
    active = v.get("active_base")
    bv = next((b for b in (v.get("base_versions") or [])
               if isinstance(b, dict) and b.get("id") == active), None) \
        or ((v.get("base_versions") or []) or [None])[-1]
    if not bv:
        return None
    aid = bv.get("asset_id")
    for vw in (bv.get("views") or []):
        if str(vw.get("view") or "").lower() == "front" and vw.get("asset_id"):
            aid = vw.get("asset_id")
            break
    if not aid:
        return None
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


def _base_set_public(run: dict) -> dict:
    """UI-facing view of a run -- per-view state without the heavy b64 (thumbs
    are fetched from /base-set/image/{run}/{idx})."""
    return {
        "status": run.get("status"), "character": run.get("character"),
        "base_mode": run.get("base_mode"), "error": run.get("error"),
        "version": run.get("version"),
        "views": [{"view": v.get("view"), "state": v.get("state"),
                   "host": v.get("host"), "error": v.get("error"),
                   "ready": bool(v.get("b64")), "rev": int(v.get("rev") or 0)}
                  for v in run.get("views", [])],
    }


_MATCHPOSE_ROT = {"front": [0, 0, 0], "right": [0, 90, 0],
                  "left": [0, -90, 0], "back": [0, 180, 0]}


async def _matchpose_derive_view(saved: dict, body: "BaseSetStartIn", identity_bytes: bytes,
                                 label: str, mesh_ready: bool, host: str, seed: int) -> bytes:
    """v1.189: body-shape-preserving turnaround view via the PROVEN mannequin +
    MatchingPose pose path. The approved (already body-correct) base is the IDENTITY
    reference and MatchingPose rotates a neutral mannequin to the target angle -- clay
    when the character has a rigged 3D body (auto), else the generic mannequin -- so
    the output keeps the base's body shape while facing a new direction (the whole
    point of these sets: a clean multi-view body to build the 3D mesh from). Returns
    PNG bytes; raises on failure so the caller can mark the view errored."""
    import asyncio as _aio
    from backend.services.character_studio.vnccs_native import klein_poses
    from backend.services.character_studio.vnccs_native.workflows import creator_baseline_pose_data
    rot = _MATCHPOSE_ROT.get(str(label).lower(), [0, 0, 0])
    pd0 = creator_baseline_pose_data()
    _bp = pd0.get("poses") or [{}]
    # v1.189.3: keep the baseline pose's REAL bones -- the app-side mannequin renderer
    # needs joint data to pose the figure; empty bones make it produce nothing ("pose
    # renderer unavailable"). We only re-aim the camera via modelRotation for each view.
    neutral = dict(_bp[0] or {})
    neutral["modelRotation"] = rot
    neutral["prompt"] = (
        "standing upright in a symmetric A-pose, arms lowered about 30 degrees out from "
        "the body so arms and hands are clear of the torso, hands open, legs straight, "
        "feet shoulder-width apart, full body visible head to toe"
        if mesh_ready else
        "standing straight and relaxed, arms slightly away from the body, full body "
        "visible head to toe")
    oi = _object_info(host)
    # v1.189.2: base-set generation must NOT depend on any EXISTING 3D asset from the
    # mesh steps (these sets are the INPUT that builds the mesh; a stale/old rigged
    # body renders a mangled clay capture that MatchingPose then copies as a warped
    # body). So force mesh3d_pose OFF -> the GENERIC posable mannequin, built purely
    # from the character's description (body_mesh_params in _klein_submit). The
    # approved base still supplies the real body via the identity reference.
    #  - MatchingPose LoRA (its trigger is auto-prepended by _klein_submit)
    ov = {"mesh3d_pose": False}
    mp = klein_poses.resolve_matchpose_lora(oi, saved)
    if mp:
        ov["klein_pose_lora"] = mp
    else:
        logger.warning("base-set matchpose: MatchingPose LoRA not found on %s -- the "
                       "rotation falls back to the current pose LoRA (identity may drift). "
                       "Install Maching_Pose_9B_Rank256.safetensors in the worker's loras.", host)
    gen_body = GenerateIn(
        character_name=body.character_name, character_info=body.character_info or {},
        nsfw=bool(body.nsfw),
        background=("neutral gray" if mesh_ready else (body.background or "Green")),
        engine="klein", base_clothing=(body.base_clothing or "strip"),
        cloner_images=list(body.cloner_images or []),
        face_kind=body.face_kind, style_custom=body.style_custom,
        canvas_w=body.canvas_w, canvas_h=body.canvas_h,
        lock_base=True, pose_set=[neutral], settings_overrides=ov)
    prompt_id, _tap, _kx = await _aio.to_thread(
        _klein_submit, host, saved, gen_body, [neutral], [identity_bytes], int(seed))
    if not prompt_id:
        raise VNCCSError("matchpose submit returned no prompt id")
    raw = await _aio.to_thread(_wait_first_image_bytes, _client(host, timeout=120),
                               prompt_id, _klein_preview_timeout(8, 1))
    if not raw:
        raise VNCCSError("matchpose produced no image")
    return raw


async def _base_set_run(run_id: str, body: BaseSetStartIn, request: Request):
    import base64 as _b64
    import random as _random
    import uuid as _uuid
    from backend.database.database import async_session as _asession

    run = _BASE_SET_RUNS.get(run_id)
    if run is None:
        return
    mode = "mesh" if str(body.base_mode or "").lower().startswith("mesh") else "set"
    mesh_ready = (mode == "mesh")
    seed0 = int(body.seed or _random.randint(1, 2_000_000_000))
    safe = "".join(ch for ch in body.character_name if ch.isalnum())[:24] or "char"
    st = None
    # v1.187.2: the squish was an aspect MISMATCH between the reference and the
    # render frame; the v1.187.1 "match the base's tight crop" over-corrected and
    # made the frame too NARROW (cropped feet, no room for the A-pose). Correct
    # approach: render into a GENEROUS portrait (character's aspect + side margin,
    # mesh gets more for the arms) AND pad the reference to the render aspect with
    # TRANSPARENT space (never stretch/crop) so it isn't distorted.
    def _aspect_of(b) -> float:
        # the CHARACTER's bounding-box aspect (not the image's) so margin isn't
        # double-counted: use alpha if present, else colour-threshold vs a corner.
        try:
            from PIL import Image as _I
            from io import BytesIO as _B
            import numpy as _np
            im = _I.open(_B(b)).convert("RGBA")
            arr = _np.asarray(im)
            alpha = arr[:, :, 3]
            if int(alpha.min()) < 250 and int(alpha.max()) > 0:
                ys, xs = _np.where(alpha > 16)
            else:
                rgb = arr[:, :, :3].astype("float32")
                bg = rgb[0, 0]
                dist = _np.sqrt(((rgb - bg) ** 2).sum(-1))
                ys, xs = _np.where(dist > 40.0)
            if len(xs) == 0:
                w, h = im.size
                return (float(w) / float(h)) if h else 0.55
            cw = int(xs.max() - xs.min() + 1)
            chh = int(ys.max() - ys.min() + 1)
            return (cw / chh) if chh else 0.55
        except Exception:  # noqa: BLE001
            return 0.55

    def _canvas_for(char_aspect: float):
        ch = int(body.canvas_h or 1216)
        margin = 1.6 if mesh_ready else 1.35          # A-pose arms need more width
        a = max(0.5, min(0.85, float(char_aspect) * margin))
        cw = max(512, min(1280, (int(round(ch * a)) // 16) * 16))
        return cw, ch

    def _pad_to_aspect(b, tw, th):
        """Pad image bytes to the tw:th aspect with TRANSPARENT space (add blank
        margin, never stretch or crop) so it references at the right proportions."""
        try:
            from PIL import Image as _I
            from io import BytesIO as _B
            im = _I.open(_B(b)).convert("RGBA")
            w, h = im.size
            ta = float(tw) / float(th)
            ca = (w / h) if h else ta
            if abs(ca - ta) < 0.01:
                return b
            if ca < ta:                                # too narrow -> widen
                nw = int(round(h * ta))
                cv = _I.new("RGBA", (nw, h), (0, 0, 0, 0))
                cv.paste(im, ((nw - w) // 2, 0), im)
            else:                                      # too wide -> heighten
                nh = int(round(w / ta))
                cv = _I.new("RGBA", (w, nh), (0, 0, 0, 0))
                cv.paste(im, (0, (nh - h) // 2), im)
            out = _B(); cv.save(out, "PNG"); return out.getvalue()
        except Exception:  # noqa: BLE001
            return b
    _set_cw, _set_ch = _canvas_for(0.55)

    def _mark(idx, **patch):
        try:
            run["views"][idx].update(patch)
        except Exception:  # noqa: BLE001
            pass

    def _mkpv(view_label, view_desc, host_i, clone_imgs, derive=False, strip_hard=False):
        return PreviewIn(
            character_name=body.character_name,
            character_info=body.character_info or {},
            nsfw=bool(body.nsfw), background=body.background or "Green",
            engine="klein", base_mode="single",
            base_clothing=body.base_clothing,
            face_kind=body.face_kind, style_custom=body.style_custom,
            cloner_images=clone_imgs,
            canvas_w=_set_cw, canvas_h=_set_ch,
            host=host_i, varitest=True,
            gen_settings={"seed": seed0},          # shared seed => matched set
            view_override={"label": view_label, "desc": view_desc,
                           "mesh_ready": mesh_ready, "derive": derive,
                           "strip_hard": strip_hard})

    try:
        async with _asession() as s0:
            pinned = body.host or await _need_host(request, s0)
            _, st = await _resolve_host(request, s0)
        comfy = getattr(request.app.state, "comfy_dispatcher", None)
        configured = (st.studio_vnccs_host or None) if st else None
        hosts = list_vnccs_hosts(comfy, configured) or [pinned]

        spec = _BASE_VIEW_SPEC                      # [(label, desc), ...] front first
        use_base = bool(getattr(body, "use_active_base", False))
        saved = (st.studio_vnccs_settings if st else None) or {}
        _derive_method = str(getattr(body, "derive_method", None)
                             or saved.get("klein_base_derive_method") or "reference").strip().lower()
        _matchpose = _derive_method in ("matchpose", "mannequin", "match_pose", "pose")
        if _matchpose:
            logger.info("base-set %s: DERIVED views via mannequin+MatchingPose (body-locked to anchor)", run_id)
        if run.get("cancelled"):
            run["status"] = "cancelled"
            return

        # ---- 1) establish the ANCHOR (front) ----
        if use_base:
            # anchor on the ALREADY-APPROVED base -- no fresh front render
            async with _asession() as sb:
                anchor_bytes = await _active_base_front_bytes(sb, body.character_name.strip())
            if not anchor_bytes:
                raise VNCCSError("no approved base image to anchor on -- generate/approve a single base first")
            # v1.187.2: the approved base is an arbitrary external crop (often an
            # upscaled tall portrait). Size a generous frame from the CHARACTER's own
            # aspect (+margin) and PAD the base to it with TRANSPARENT space -- never
            # stretch (squish) or crop (feet) -- so every derived view references it at
            # correct proportions. This recompute is use_base ONLY: a fresh set already
            # renders its front into the default frame below, so re-deriving there would
            # desync view 0 from the derived views.
            _char_asp = _aspect_of(anchor_bytes)
            _set_cw, _set_ch = _canvas_for(_char_asp)
            anchor_bytes = _pad_to_aspect(anchor_bytes, _set_cw, _set_ch)
            logger.info("base-set %s: approved base char %.3f -> render %dx%d (padded anchor)",
                        run_id, _char_asp, _set_cw, _set_ch)
            if not mesh_ready:
                # 4-view: the PADDED approved front IS view 0 (same frame as derived views)
                _mark(0, state="done", host=pinned,
                      b64=_b64.b64encode(anchor_bytes).decode("ascii"))
                to_render = list(enumerate(spec))[1:]
            else:
                # mesh: derive ALL four views (incl an A-pose gray front) FROM the approved base
                to_render = list(enumerate(spec))
        else:
            # fresh set: render the front into the DEFAULT generous canvas (_canvas_for(0.55))
            # and keep it as-is. Every derived view renders into that same frame, so the set
            # is already aspect-consistent -- no recompute/pad needed here.
            front_label, front_desc = spec[0]
            _mark(0, state="rendering", host=pinned)
            async with _asession() as s1:
                r0 = await generate_preview(_mkpv(front_label, front_desc, pinned, body.cloner_images), request, s1)
            front_b64 = (r0 or {}).get("image")
            if not front_b64:
                raise VNCCSError("front view produced no image")
            _mark(0, state="done", b64=front_b64)
            anchor_bytes = _b64.b64decode(front_b64)
            to_render = list(enumerate(spec))[1:]

        # ---- 2) render the remaining views as reference-EDITS of the anchor, across workers ----
        front_bytes = anchor_bytes
        token = _uuid.uuid4().hex[:8]
        # keep what a single-view REGEN needs (anchor + params) on the run
        run["_ctx"] = {"anchor_b64": _b64.b64encode(anchor_bytes).decode("ascii"),
                       "hosts": hosts, "pinned": pinned, "safe": safe, "seed0": seed0,
                       "use_base": use_base, "set_cw": _set_cw, "set_ch": _set_ch,
                       "matchpose": _matchpose}
        run["_body"] = body

        def _side_ref_for(label):
            # a reference the user tagged with THIS view's angle (left/right/back)
            for ci in (body.cloner_images or []):
                if isinstance(ci, dict) and ci.get("name") \
                        and str(ci.get("angle") or "").strip().lower() == str(label).lower():
                    return ci
            return None

        async def _one(idx, label, desc, host_i):
            if run.get("cancelled"):
                _mark(idx, state="skipped")
                return
            if _matchpose:
                # body-shape-preserving turnaround takes PRIORITY when selected: the
                # approved/anchor base is the identity (its real body), MatchingPose
                # rotates a mannequin (clay if rigged) to this angle. This deliberately
                # OVERRIDES any tagged side/back photo -- the whole reason to pick this
                # mode is one consistent body across every angle for meshing, whereas a
                # per-angle photo strip re-derives (and drifts) the body.
                _mark(idx, state="rendering", host=host_i)
                try:
                    raw = await _matchpose_derive_view(
                        saved, body, anchor_bytes, label, mesh_ready, host_i, seed0)
                    _mark(idx, state="done", host=host_i,
                          b64=_b64.b64encode(raw).decode("ascii"))
                except Exception as e:  # noqa: BLE001
                    logger.warning(f"base-set {run_id}: matchpose view {label} failed: {e}")
                    _mark(idx, state="error", error=str(e)[:200])
                return
            side = _side_ref_for(label)
            if side is not None:
                # v1.185: leverage the user's REAL side/back photo — strip it from
                # that angle (on the pinned worker, where the ref already lives)
                # instead of rotating the front. Real material => accurate side.
                _mark(idx, state="rendering", host=pinned)
                try:
                    pv = _mkpv(label, desc, pinned, [{"name": side["name"], "role": "full"}],
                               derive=False, strip_hard=True)
                    async with _asession() as s2:
                        r = await generate_preview(pv, request, s2)
                    b64 = (r or {}).get("image")
                    if not b64:
                        raise VNCCSError("no image")
                    _mark(idx, state="done", host=pinned, b64=b64)
                except Exception as e:  # noqa: BLE001
                    logger.warning(f"base-set {run_id}: side-ref view {label} failed: {e}")
                    _mark(idx, state="error", error=str(e)[:200])
                return
            _mark(idx, state="rendering", host=host_i)
            try:
                up = await asyncio.to_thread(
                    _client(host_i, timeout=120).upload_image,
                    f"rbmn_baseset_{safe}_{token}_front.png", front_bytes, "", True, 120)
                fname = up.get("name", f"rbmn_baseset_{safe}_{token}_front.png")
                pv = _mkpv(label, desc, host_i, [{"name": fname, "role": "full"}], derive=True)
                async with _asession() as s2:
                    r = await generate_preview(pv, request, s2)
                b64 = (r or {}).get("image")
                if not b64:
                    raise VNCCSError("no image")
                _mark(idx, state="done", b64=b64)
            except Exception as e:  # noqa: BLE001
                logger.warning(f"base-set {run_id}: view {label} failed: {e}")
                _mark(idx, state="error", error=str(e)[:200])

        await asyncio.gather(*[
            _one(idx, lbl, dsc, hosts[k % len(hosts)])
            for k, (idx, (lbl, dsc)) in enumerate(to_render)])

        # ---- 3) save whatever finished (front + any derived) as ONE base version ----
        done = [v for v in run["views"] if v.get("state") == "done" and v.get("b64")]
        if done:
            raws = [_b64.b64decode(v["b64"]) for v in done]
            try:
                from backend.services.character_studio.cutout import normalize_base_set
                raws = normalize_base_set(raws)
            except Exception:  # noqa: BLE001
                logger.exception("base-set: framing/normalize failed")
            views_payload = [{"view": v["view"],
                              "image_b64": _b64.b64encode(raw).decode("ascii")}
                             for v, raw in zip(done, raws)]
            from backend.services.character_studio.vnccs_native.ingest import save_base_preview
            _gm = _klein_gen_meta(
                (st.studio_vnccs_settings if st else None) or {}, seed=seed0,
                canvas_w=body.canvas_w, canvas_h=body.canvas_h,
                extra={"engine": "klein-refbase", "base_mode": mode,
                       "base_clothing": str(body.base_clothing or "strip")})
            async with _asession() as s3:
                ver = await save_base_preview(
                    s3, character_name=body.character_name.strip(),
                    image_b64=None, views=views_payload, variant="klein", gen_meta=_gm, make_active=False)
            run["version"] = ver
        run["status"] = "cancelled" if run.get("cancelled") else "done"
    except Exception as e:  # noqa: BLE001
        if run.get("cancelled"):
            run["status"] = "cancelled"      # Stop interrupted a render -> not an error
        else:
            logger.exception(f"base-set {run_id} crashed")
            run["status"] = "error"
            run["error"] = str(e)[:400]
    finally:
        _BASE_SET_TASKS.pop(run_id, None)


@router.post("/base-set/start")
async def base_set_start(body: BaseSetStartIn, request: Request,
                         session: AsyncSession = Depends(get_session)):
    import uuid as _uuid
    name = body.character_name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="character name required")
    if str(body.engine or "klein").lower() != "klein":
        raise HTTPException(status_code=400, detail="the base-set runner is Klein-only")
    if body.use_active_base:
        if await _active_base_front_bytes(session, name) is None:
            raise HTTPException(status_code=409,
                                detail="No approved base image yet — generate a Single base first, then anchor the set on it.")
    mode = "mesh" if str(body.base_mode or "").lower().startswith("mesh") else "set"
    spec = _BASE_VIEW_SPEC
    run_id = "bs_" + _uuid.uuid4().hex[:10]
    _BASE_SET_RUNS[run_id] = {
        "status": "running", "character": name, "base_mode": mode,
        "error": None, "cancelled": False, "version": None,
        "views": [{"view": lbl, "state": "pending", "host": None, "b64": None, "error": None, "rev": 0}
                  for lbl, _d in spec]}
    _BASE_SET_TASKS[run_id] = asyncio.create_task(_base_set_run(run_id, body, request))
    return {"run_id": run_id, "views": [lbl for lbl, _d in spec], "mode": mode}


@router.get("/base-set/status/{run_id}")
async def base_set_status(run_id: str):
    run = _BASE_SET_RUNS.get(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="unknown base-set run")
    return {"run_id": run_id, **_base_set_public(run)}


@router.get("/base-set/image/{run_id}/{idx}")
async def base_set_image(run_id: str, idx: int):
    from fastapi.responses import Response
    run = _BASE_SET_RUNS.get(run_id)
    if not run or idx < 0 or idx >= len(run.get("views", [])):
        raise HTTPException(status_code=404, detail="no such view")
    b64 = run["views"][idx].get("b64")
    if not b64:
        raise HTTPException(status_code=404, detail="view not ready")
    import base64 as _b64
    from fastapi.responses import Response as _Resp
    return _Resp(content=_b64.b64decode(b64), media_type="image/png")


@router.post("/base-set/cancel/{run_id}")
async def base_set_cancel(run_id: str):
    run = _BASE_SET_RUNS.get(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="unknown base-set run")
    run["cancelled"] = True
    # actually stop in-flight renders: interrupt every worker this run touched
    ctx = run.get("_ctx") or {}
    hosts = list(ctx.get("hosts") or [])
    if ctx.get("pinned"):
        hosts.append(ctx["pinned"])
    seen = set()
    for h in hosts:
        if not h or h in seen:
            continue
        seen.add(h)
        try:
            await asyncio.to_thread(_client(h, timeout=30).interrupt, 30)
        except Exception:  # noqa: BLE001
            pass
    return {"ok": True, "status": run.get("status")}


async def _base_set_regen_one(run_id: str, idx: int, request: Request):
    """Re-render ONE view of an existing run (new seed) if it came out off. Uses
    the stored anchor: derived views re-rotate the front; a fresh front re-renders
    and updates the anchor for future regens."""
    import base64 as _b64
    import random as _random
    from backend.database.database import async_session as _asession
    run = _BASE_SET_RUNS.get(run_id)
    ctx = (run or {}).get("_ctx")
    body = (run or {}).get("_body")
    if not run or not ctx or not body:
        return
    spec = _BASE_VIEW_SPEC
    if idx < 0 or idx >= len(spec):
        return
    label, desc = spec[idx]
    mesh_ready = (run.get("base_mode") == "mesh")
    seed = _random.randint(1, 2_000_000_000)
    hosts = ctx.get("hosts") or [ctx.get("pinned")]
    host_i = hosts[idx % len(hosts)]
    safe = ctx.get("safe") or "char"
    use_base = bool(ctx.get("use_base"))
    # v1.187.1: reuse the run's anchor-matched aspect so a regen isn't squished
    _set_cw = int(ctx.get("set_cw") or 672)
    _set_ch = int(ctx.get("set_ch") or 1216)

    def _mkpv(clone_imgs, derive):
        return PreviewIn(
            character_name=body.character_name, character_info=body.character_info or {},
            nsfw=bool(body.nsfw), background=body.background or "Green",
            engine="klein", base_mode="single", base_clothing=body.base_clothing,
            face_kind=body.face_kind, style_custom=body.style_custom,
            cloner_images=clone_imgs, canvas_w=_set_cw, canvas_h=_set_ch,
            host=host_i, varitest=True, gen_settings={"seed": seed},
            view_override={"label": label, "desc": desc, "mesh_ready": mesh_ready, "derive": derive})

    _matchpose = bool(ctx.get("matchpose"))
    saved = None
    run["views"][idx].update(state="rendering", host=host_i, error=None)
    try:
        if idx == 0 and not use_base and not mesh_ready:
            # re-render the fresh front and refresh the anchor
            async with _asession() as s:
                r = await generate_preview(_mkpv(body.cloner_images, False), request, s)
            b64 = (r or {}).get("image")
            if b64:
                run["_ctx"]["anchor_b64"] = b64
        elif _matchpose:
            # matchpose set: re-rotate the anchor via mannequin+MatchingPose (new seed)
            async with _asession() as s:
                _st = await _settings(s)
            saved = (_st.studio_vnccs_settings if _st else None) or {}
            anchor_bytes = _b64.b64decode(ctx["anchor_b64"])
            raw = await _matchpose_derive_view(
                saved, body, anchor_bytes, label, mesh_ready, host_i, seed)
            b64 = _b64.b64encode(raw).decode("ascii")
        else:
            anchor_bytes = _b64.b64decode(ctx["anchor_b64"])
            up = await asyncio.to_thread(
                _client(host_i, timeout=120).upload_image,
                f"rbmn_baseset_{safe}_regen_front.png", anchor_bytes, "", True, 120)
            fname = up.get("name", f"rbmn_baseset_{safe}_regen_front.png")
            async with _asession() as s:
                r = await generate_preview(_mkpv([{"name": fname, "role": "full"}], True), request, s)
            b64 = (r or {}).get("image")
        if not b64:
            raise VNCCSError("no image")
        run["views"][idx].update(state="done", b64=b64,
                                 rev=int(run["views"][idx].get("rev") or 0) + 1)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"base-set {run_id}: regen view {idx} failed: {e}")
        run["views"][idx].update(state="error", error=str(e)[:200])


@router.post("/base-set/regen/{run_id}/{idx}")
async def base_set_regen(run_id: str, idx: int, request: Request):
    run = _BASE_SET_RUNS.get(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="unknown base-set run")
    if not run.get("_ctx"):
        raise HTTPException(status_code=409, detail="this run can't regen (front not ready yet)")
    _BASE_SET_TASKS[f"{run_id}:regen:{idx}"] = asyncio.create_task(
        _base_set_regen_one(run_id, idx, request))
    return {"ok": True}


@router.post("/base-set/save/{run_id}")
async def base_set_save(run_id: str, session: AsyncSession = Depends(get_session)):
    """Save the run's CURRENT finished views (after any regens) as a base version."""
    import base64 as _b64
    run = _BASE_SET_RUNS.get(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="unknown base-set run")
    done = [v for v in run.get("views", []) if v.get("state") == "done" and v.get("b64")]
    if not done:
        raise HTTPException(status_code=409, detail="no finished views to save")
    raws = [_b64.b64decode(v["b64"]) for v in done]
    try:
        from backend.services.character_studio.cutout import normalize_base_set
        raws = normalize_base_set(raws)
    except Exception:  # noqa: BLE001
        logger.exception("base-set save: framing/normalize failed")
    views_payload = [{"view": v["view"], "image_b64": _b64.b64encode(raw).decode("ascii")}
                     for v, raw in zip(done, raws)]
    from backend.services.character_studio.vnccs_native.ingest import save_base_preview
    st = await _settings(session)
    ctx = run.get("_ctx") or {}
    _gm = _klein_gen_meta((st.studio_vnccs_settings if st else None) or {},
                          seed=ctx.get("seed0"),
                          extra={"engine": "klein-refbase", "base_mode": run.get("base_mode")})
    ver = await save_base_preview(
        session, character_name=str(run.get("character") or "").strip(),
        image_b64=None, views=views_payload, variant="klein", gen_meta=_gm, make_active=False)
    run["version"] = ver
    return {"ok": True, "version": ver}


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
    canvas_w: Optional[int] = None           # per-character base+pose canvas width (Klein)


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
        # per-character canvas width (Klein) — reloaded into the Canvas control so
        # a wide character keeps its wider frame across sessions.  None = follow
        # the global default. Carry forward a previously saved value if omitted.
        "canvas_w": (int(body.canvas_w) if body.canvas_w
                     else (vnccs.get("form") or {}).get("canvas_w")),
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


class QwenCreateIn(BaseModel):
    """v1.168 app-side VNCCS-replica CharacterCreatorV2: t2i base render at
    640x1536 (Illustrious SDXL or Anima), saved as a base version."""
    character_name: str
    character_info: dict = {}
    nsfw: Optional[bool] = None
    background: Optional[str] = "Green"
    mode: Optional[str] = None                # '' auto | illustrious | anima
    steps: Optional[int] = None               # None = mode default (20 / 4-turbo / 30 / 12-turbo)
    cfg: Optional[float] = None               # None = mode default (8 / 1 / 4 / 1)
    seed: Optional[int] = None
    negative: Optional[str] = None            # override; None = VNCCS default per mode
    host: Optional[str] = None


@router.post("/create/qwen-preview")
async def create_qwen_preview(body: QwenCreateIn, request: Request,
                              session: AsyncSession = Depends(get_session)):
    """Qwen (VNCCS-replica) NEW-CHARACTER preview: the suite's exact t2i base
    render -- CharacterCreatorV2's prompt template, 640x1536 canvas, Illustrious
    (euler/normal, DMD2 turbo when installed) or Anima (er_sde/simple, turbo
    LoRA) -- saved as a BASE VERSION like the Klein preview."""
    import base64 as _b64
    import random as _random
    from backend.services.character_studio.vnccs_native import qwen_clothes
    from backend.services.character_studio.vnccs_native.ingest import save_base_preview

    host = await _need_host(request, session)
    st = await _settings(session)
    saved = (st.studio_vnccs_settings if st else None) or {}
    name = body.character_name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="character name required")
    try:
        oi = await asyncio.to_thread(_object_info, host)
        models = qwen_clothes.resolve_t2i_models(oi, saved, mode=str(body.mode or ""))
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except VNCCSError as e:
        raise HTTPException(status_code=502, detail=str(e))
    info = dict(body.character_info or {})
    if body.nsfw is not None:
        info["nsfw"] = bool(body.nsfw)
    info.setdefault("background_color", str(body.background or "Green"))
    anima = models["mode"] == "anima"
    # tag family (Illustrious / Anima) = VNCCS's exact tag template; the NL
    # family (Klein / Z-Image / Krea2 / Qwen) gets the same semantics adapted
    # to prose per docs/MODEL_PROMPTING.md (their negatives are inert at CFG 1
    # -- those graphs zero the conditioning instead).
    _nl = models["mode"] in ("klein", "zimage", "krea2", "qwen")
    prompt = (qwen_clothes.creator_prompt_natural(info, flavor=models["mode"]) if _nl
              else qwen_clothes.creator_prompt(info, anima=anima))
    negative = (str(body.negative).strip() if body.negative
                else "" if _nl
                else qwen_clothes.creator_negative(info, anima=anima))
    seed = int(body.seed) if body.seed else _random.randint(1, 2_000_000_000)
    safe = "".join(ch for ch in name if ch.isalnum())[:24] or "char"
    logger.info("qwen create preview: mode=%s seed=%d", models["mode"], seed)

    _steps = body.steps
    if _steps is None:
        try:
            _steps = int(saved.get("qwen_create_steps")) if saved.get("qwen_create_steps") else None
        except Exception:  # noqa: BLE001
            _steps = None
    _cfg = body.cfg
    if _cfg is None:
        try:
            _cfg = float(saved.get("qwen_create_cfg")) if saved.get("qwen_create_cfg") else None
        except Exception:  # noqa: BLE001
            _cfg = None
    _qloras = str(saved.get("qwen_create_quality_loras") or "") != "off"

    def _run():
        client = _client(host, timeout=120)
        graph, _tap = qwen_clothes.build_t2i_creator_graph(
            prompt=prompt, negative=negative, seed=seed, models=models,
            steps=_steps, cfg=_cfg, age=int(info.get("age") or 18),
            use_quality_loras=_qloras,
            filename_prefix=f"rbmn_vnccs/{safe}/qwen_base")
        res = client.submit_prompt(graph, timeout=120)
        raw = _wait_first_image_bytes(client, res.get("prompt_id"), 1800)
        return _b64.b64encode(raw).decode("ascii")

    try:
        img_b64 = await asyncio.to_thread(_run)
    except VNCCSError as e:
        raise HTTPException(status_code=502, detail=str(e))
    except Exception as e:  # noqa: BLE001
        logger.exception("qwen create preview failed")
        raise HTTPException(status_code=500, detail=f"qwen create preview failed: {e}")
    version = None
    try:
        _gm = _klein_gen_meta(saved, seed=seed,
                              extra={"engine": "qwen-creator", "t2i_mode": models["mode"],
                                     "prompt": prompt[:400]})
        version = await save_base_preview(session, character_name=name,
                                          image_b64=img_b64, variant="klein", gen_meta=_gm)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"qwen create preview: version save failed: {e}")
    return {"image": img_b64, "host": host, "engine": "qwen-creator",
            "version": version, "seed": seed, "t2i_mode": models["mode"]}


class QwenCloneIn(BaseModel):
    """v1.168 app-side VNCCS-replica cloner preview: reference photos ->
    collage -> optional remove-clothes edit -> ONE neutral-pose Pass-B render,
    saved as a base version."""
    character_name: str
    cloner_images: list = []                  # [{name,subfolder,type,role?}, ...]
    character_info: dict = {}
    background: Optional[str] = "Green"
    base_clothing: Optional[str] = None       # 'strip' (Naked branch, default) | 'keep' (Original)
    undress_prompt: Optional[str] = None      # def "Undress character" (workflow default)
    seed: Optional[int] = None
    target_size: Optional[int] = None
    ref_weight: Optional[float] = None        # v1.194: encoder reference strength (body adherence), 1.0 = VNCCS
    headwear_room: Optional[float] = None     # v1.199.13: reserved top headroom for tall hats (0.14 default)
    host: Optional[str] = None


@router.post("/create/qwen-clone-preview")
async def create_qwen_clone_preview(body: QwenCloneIn, request: Request,
                                    session: AsyncSession = Depends(get_session)):
    """Qwen (VNCCS-replica) CLONE preview.  VNCCS's cloner packs the reference
    photos into ONE collage grid and draws every pose from it; the Naked branch
    first runs a remove-clothes Qwen edit (ClothesCore LoRA, 'Undress
    character').  Here we run that pipeline down to a SINGLE neutral-pose
    render and file it as the base version -- pose sets then use engine=qwen."""
    import base64 as _b64
    import random as _random
    import uuid as _uuid
    from backend.services.character_studio.vnccs_native import klein_poses, pose_render, qwen_clothes
    from backend.services.character_studio.vnccs_native.workflows import creator_baseline_pose_data
    from backend.services.character_studio.vnccs_native.ingest import save_base_preview

    host = await _need_host(request, session)
    st = await _settings(session)
    saved = (st.studio_vnccs_settings if st else None) or {}
    name = body.character_name.strip()
    refs = [r for r in (body.cloner_images or []) if isinstance(r, dict) and r.get("name")]
    if not name or not refs:
        raise HTTPException(status_code=400, detail="character name and reference images required")
    try:
        oi = await asyncio.to_thread(_object_info, host)
        models = qwen_clothes.resolve_qwen_models(oi, saved)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except VNCCSError as e:
        raise HTTPException(status_code=502, detail=str(e))
    strip = str(body.base_clothing or saved.get("klein_base_clothing") or "strip").lower() != "keep"
    seed = int(body.seed) if body.seed else _random.randint(1, 2_000_000_000)
    tsize = int(body.target_size or 1024)
    background = str(body.background or "Green")
    # v1.193.1: STRIP (SFW) must leave the character in white underwear, not nude.
    # The clone always ran REMOVE_CLOTHES_PROMPT ("Undress character" -> fully nude),
    # so SFW strip came out naked. Now: explicit override wins; else NSFW -> full
    # undress (nude), SFW strip -> the "Dress character: White underwear" prompt.
    _clone_nsfw = bool((body.character_info or {}).get("nsfw"))
    if body.undress_prompt:
        undress_prompt = str(body.undress_prompt).strip()
    elif _clone_nsfw:
        undress_prompt = qwen_clothes.REMOVE_CLOTHES_PROMPT
    else:
        undress_prompt = qwen_clothes.REMOVE_CLOTHES_PROMPT_SOFT
    logger.info("qwen clone: base_clothing=%s nsfw=%s -> undress prompt %r",
                body.base_clothing, _clone_nsfw, undress_prompt)
    # v1.194: reference strength (body adherence) -- >1.0 holds the reference body
    # harder so a fuller build survives. Per-run body.ref_weight, else saved setting.
    try:
        ref_weight = float(body.ref_weight if body.ref_weight is not None
                           else saved.get("qwen_ref_weight") or 1.0)
    except Exception:  # noqa: BLE001
        ref_weight = 1.0
    logger.info("qwen clone: reference strength (body adherence) = %.2f", ref_weight)
    safe = "".join(ch for ch in name if ch.isalnum())[:24] or "char"

    # v1.195: the base PREVIEW should just STAND (like the new-character t2i render),
    # not strike a library pose. Take the default pose but ZERO every bone rotation
    # (keeps valid bone names so the renderer is happy) -> the mannequin's rest
    # near-A-pose, front-facing. The full pose SET still uses the selected poses.
    pd = creator_baseline_pose_data()
    _poses0 = pd.get("poses") or []
    _stand = dict(_poses0[0]) if _poses0 else {}
    _src_bones = _stand.get("bones") or {}
    _stand["bones"] = {k: [0, 0, 0] for k in _src_bones}
    _stand["modelRotation"] = [0, 0, 0]
    _stand["prompt"] = "standing straight and relaxed, facing forward, full body visible head to toe"
    pd["poses"] = [_stand]
    pd["mesh"] = {**(pd.get("mesh") or {}),
                  **klein_poses.body_mesh_params(body.character_info or {})}
    _cw, _ch = _klein_canvas(saved, None, None)
    pd["export"] = {**(pd.get("export") or {}), "view_width": _cw, "view_height": _ch,
                    "top_headroom": _qwen_headwear_room(saved, getattr(body, "headwear_room", None))}
    captures = pose_render.render_pose_captures(pd, False)
    if not captures:
        raise HTTPException(status_code=502,
                            detail="app-side pose renderer unavailable -- cannot build the neutral pose")
    logger.info("qwen clone preview: %d ref(s), branch=%s, seed=%d",
                len(refs), "naked" if strip else "original", seed)

    def _run():
        client = _client(host, timeout=120)
        token = _uuid.uuid4().hex[:8]
        # collage from the worker-side reference uploads (VNCCS CharacterCloner)
        ref_bytes = []
        for r in refs[:8]:
            try:
                ref_bytes.append(client.view_image(r.get("name", ""), r.get("subfolder", "") or "",
                                                   r.get("type", "input") or "input", 120))
            except VNCCSError:
                continue
        if not ref_bytes:
            raise VNCCSError("could not read any reference image from the worker")
        collage = qwen_clothes.build_reference_collage(ref_bytes)
        cn = f"rbmn_qwen_{safe}_{token}_collage.png"
        client.upload_image(cn, collage, "", True, 120)
        src_name = cn
        if strip:
            xg, _t = qwen_clothes.build_qwen_remove_clothes_graph(
                collage_file=cn, seed=seed, models=models, prompt=undress_prompt,
                target_size=tsize, ref_weight=ref_weight,
                filename_prefix=f"rbmn_vnccs/{safe}/qwen_undress")
            xr = client.submit_prompt(xg, timeout=120)
            xraw = _wait_first_image_bytes(client, xr.get("prompt_id"), 1800)
            un = f"rbmn_qwen_{safe}_{token}_undressed.png"
            client.upload_image(un, xraw, "", True, 120)
            src_name = un
            # v1.196.2: the ClothesCore LoRA leaves a last underwear layer on a NSFW
            # strip (bra straps / bottoms survive pass 1). Run a SECOND focused edit on
            # the pass-1 result to strip that residual layer -> fully nude.
            if _clone_nsfw:
                xg2, _t2 = qwen_clothes.build_qwen_remove_clothes_graph(
                    collage_file=un, seed=seed, models=models,
                    prompt=qwen_clothes.REMOVE_UNDERWEAR_PROMPT,
                    target_size=tsize, ref_weight=ref_weight,
                    filename_prefix=f"rbmn_vnccs/{safe}/qwen_nude")
                xr2 = client.submit_prompt(xg2, timeout=120)
                xraw2 = _wait_first_image_bytes(client, xr2.get("prompt_id"), 1800)
                un2 = f"rbmn_qwen_{safe}_{token}_nude.png"
                client.upload_image(un2, xraw2, "", True, 120)
                src_name = un2
                logger.info("qwen clone: NSFW two-pass strip -> second underwear-removal pass done")
        pn = f"rbmn_qwen_{safe}_{token}_pose0.png"
        # render_pose_captures returns data-URL strings; DECODE to real PNG bytes
        # before upload (every other path does this) -- uploading the raw data-URL
        # made the worker write a text file that LoadImage couldn't decode
        # ("cannot identify image file ... pose0.png").
        up = client.upload_image(pn, klein_poses.decode_capture(captures[0]), "", True, 120)
        graph, _tap = qwen_clothes.build_qwen_pose_set_graph(
            pose_files=[up.get("name", pn)], dressed_file=src_name, seed=seed,
            models=models, background=background, target_size=tsize, rmbg=None,
            ref_weight=ref_weight, filename_prefix=f"rbmn_vnccs/{safe}/qwen_base")
        res = client.submit_prompt(graph, timeout=120)
        raw = _wait_first_image_bytes(client, res.get("prompt_id"), 1800)
        return _b64.b64encode(raw).decode("ascii")

    try:
        img_b64 = await asyncio.to_thread(_run)
    except VNCCSError as e:
        raise HTTPException(status_code=502, detail=str(e))
    except Exception as e:  # noqa: BLE001
        logger.exception("qwen clone preview failed")
        raise HTTPException(status_code=500, detail=f"qwen clone preview failed: {e}")
    version = None
    try:
        _gm = _klein_gen_meta(saved, seed=seed,
                              extra={"engine": "qwen-cloner", "refs": len(refs),
                                     "branch": "naked" if strip else "original"})
        version = await save_base_preview(session, character_name=name,
                                          image_b64=img_b64, variant="klein", gen_meta=_gm)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"qwen clone preview: version save failed: {e}")
    return {"image": img_b64, "host": host, "engine": "qwen-cloner",
            "version": version, "seed": seed}


class QwenClotheIn(BaseModel):
    """v1.167 app-side VNCCS-replica Pass A (ClothesDesigner): dress the
    character once via Qwen-Image-Edit-2511 + the VNCCS ClothesCore LoRA."""
    character_name: str
    costume_name: str
    costume_info: dict = {}                   # top/bottom/head/shoes/face slots
    garment_ref: Optional[dict] = None        # clone mode: outfit photo {name,subfolder,type}
    background: Optional[str] = "Green"       # Green | Blue (VNCCS's two chroma choices)
    base_version_id: Optional[str] = None     # base version to dress (default: active)
    pose_asset_id: Optional[str] = None       # dress a cataloged POSE sprite instead
    seed: Optional[int] = None                # None = random
    steps: Optional[int] = None               # def 4 (Lightning turbo)
    cfg: Optional[float] = None               # def 1.0
    clothes_lora_strength: Optional[float] = None  # def 1.0 (VNCCS hard-codes 1)
    target_size: Optional[int] = None         # def 1024 (encoder total-pixel budget)
    headwear_room: Optional[float] = None     # v1.199.13: reserved top headroom for tall hats
    use_saved_garment: bool = False           # v1.199.5: use the costume's saved outfit ref
    host: Optional[str] = None


@router.post("/clothes/qwen-preview")
async def clothes_qwen_preview(body: QwenClotheIn, request: Request,
                               session: AsyncSession = Depends(get_session)):
    """Qwen (VNCCS-replica) clothing PREVIEW: rebuild the suite's
    ClothesDesigner graph app-side and dress the character's ACTIVE base
    render (or a chosen pose sprite / base version) in the costume slots --
    or, with ``garment_ref``, VNCCS's clone mode ("Dress character: clothes,
    footwear and accessories from Picture 2").  Saves the result as a costume
    VERSION exactly like the Klein preview does."""
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
    from backend.services.character_studio.vnccs_native import qwen_clothes

    host = await _need_host(request, session)
    st = await _settings(session)
    saved = (st.studio_vnccs_settings if st else None) or {}
    name = body.character_name.strip()
    costume = body.costume_name.strip()
    if not name or not costume:
        raise HTTPException(status_code=400, detail="character and costume name required")
    char = (await session.execute(
        _select(StudioCharacter).where(StudioCharacter.name == name))).scalars().first()
    if char is None:
        raise HTTPException(status_code=404, detail=f"character {name!r} not found")

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

    def _flat_green(data: bytes) -> bytes:
        im = _Image.open(_io.BytesIO(data))
        if im.mode == "RGBA":
            bg = _Image.new("RGB", im.size, (0, 255, 0))
            bg.paste(im, mask=im.split()[3])
            im = bg
        elif im.mode != "RGB":
            im = im.convert("RGB")
        buf = _io.BytesIO()
        im.save(buf, format="PNG")
        return buf.getvalue()

    # source image: chosen pose sprite > chosen/active base version
    src: Optional[bytes] = None
    if body.pose_asset_id:
        src = await _abytes(body.pose_asset_id)
        if not src:
            raise HTTPException(status_code=409, detail="Selected pose sprite is not readable.")
    else:
        v = (char.manifest or {}).get("vnccs") or {}
        target_id = body.base_version_id or v.get("active_base")
        bv = next((b for b in (v.get("base_versions") or [])
                   if isinstance(b, dict) and b.get("id") == target_id), None)
        if bv and bv.get("asset_id"):
            src = await _abytes(bv.get("asset_id"))
        if not src:
            raise HTTPException(status_code=409,
                                detail="No base version to dress -- generate a base preview first.")

    try:
        oi = await asyncio.to_thread(_object_info, host)
        models = qwen_clothes.resolve_qwen_models(oi, saved)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except VNCCSError as e:
        raise HTTPException(status_code=502, detail=str(e))

    _live_garment = bool(body.garment_ref and (body.garment_ref or {}).get("name"))
    _saved_garment = (_garment_bytes(str(char.id), costume)
                      if (body.use_saved_garment and not _live_garment) else None)
    has_garment = bool(_live_garment or _saved_garment)
    prompt = (qwen_clothes.QWEN_CLONE_PROMPT if has_garment
              else qwen_clothes.qwen_dress_prompt(body.costume_info or {}, body.background or "Green"))
    seed = int(body.seed) if body.seed else _random.randint(1, 2_000_000_000)
    steps = max(1, min(20, int(body.steps or 4)))
    cfg = max(1.0, min(5.0, float(body.cfg or 1.0)))
    lstr = max(0.1, min(1.5, float(body.clothes_lora_strength or 1.0)))
    tsize = int(body.target_size or 1024)
    safe = "".join(ch for ch in name if ch.isalnum())[:24] or "char"
    logger.info("qwen clothes preview: steps=%d cfg=%.2f lora=%.2f target=%d clone=%s",
                steps, cfg, lstr, tsize, has_garment)

    def _run():
        client = _client(host, timeout=120)
        token = _uuid.uuid4().hex[:8]
        bn = f"rbmn_qwen_{safe}_{token}_base.png"
        # v1.199.6: base renders now bake in headroom at generation time (framing
        # clause in the base prompts), so no dress-time pad is needed. Older bases
        # (made before v1.199.6) have no headroom -> re-render the base for hats.
        _hr = _qwen_headwear_room(saved, getattr(body, "headwear_room", None))
        client.upload_image(bn, qwen_clothes.pad_base_to_headroom(_flat_green(src), _hr), "", True, 120)
        garment_name = None
        if _live_garment:
            gr = body.garment_ref or {}
            try:
                raw = client.view_image(gr.get("name", ""), gr.get("subfolder", "") or "",
                                        gr.get("type", "input") or "input", 120)
                gn = f"rbmn_qwen_{safe}_{token}_garment.png"
                client.upload_image(gn, _flat_green(raw), "", True, 120)
                garment_name = gn
            except VNCCSError:
                garment_name = None
        elif _saved_garment:
            gn = f"rbmn_qwen_{safe}_{token}_garment.png"
            client.upload_image(gn, _flat_green(_saved_garment), "", True, 120)
            garment_name = gn
        graph, _tap = qwen_clothes.build_qwen_dress_graph(
            base_file=bn, prompt=prompt, seed=seed, models=models,
            garment_file=garment_name, clothes_lora_strength=lstr,
            target_size=tsize, steps=steps, cfg=cfg,
            filename_prefix=f"rbmn_vnccs/{safe}/qwen_clothes")
        res = client.submit_prompt(graph, timeout=120)
        raw = _wait_first_image_bytes(client, res.get("prompt_id"), 1800)
        return _b64.b64encode(raw).decode("ascii")

    try:
        img_b64 = await asyncio.to_thread(_run)
    except VNCCSError as e:
        raise HTTPException(status_code=502, detail=str(e))
    except Exception as e:  # noqa: BLE001
        logger.exception("qwen clothes preview failed")
        raise HTTPException(status_code=500, detail=f"qwen clothes preview failed: {e}")

    version = None
    try:
        from backend.services.character_studio.vnccs_native.ingest import save_costume_preview
        _gm = _klein_gen_meta(saved, seed=seed,
                              extra={"engine": "qwen-clothes", "steps": steps, "cfg": cfg,
                                     "clothes_lora_strength": lstr, "target_size": tsize,
                                     "garment_ref": has_garment})
        version = await save_costume_preview(
            session, character_name=name, costume=costume,
            image_b64=img_b64, costume_info=body.costume_info or {}, gen_meta=_gm)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"qwen clothes preview: version save failed: {e}")
    return {"image": img_b64, "host": host, "engine": "qwen-clothes",
            "version": version, "seed": seed}


class KleinClotheIn(BaseModel):
    character_name: str
    costume_name: str
    costume_info: dict = {}                   # top/bottom/head/face/shoes free text
    garment_ref: Optional[dict] = None        # {name,subfolder,type} outfit image on a host
    background: Optional[str] = "Green"
    strength: float = 1.0                     # identity/pose preservation (higher = keep more)
    base_version_id: Optional[str] = None     # base version to dress (default: active)
    view: Optional[str] = None                # single view label to dress (default: all views)
    face_refine: Optional[bool] = None        # keep the face crisp after redress (default on)
    host: Optional[str] = None
    pose_asset_id: Optional[str] = None       # dress a cataloged POSE sprite instead of the base
    steps: Optional[int] = None               # dressing steps (default: global klein steps)
    guidance: Optional[float] = None          # >1 activates the negative (def 1.0)
    ref_end: Optional[float] = None           # release the bare-skin body ref (def 0.8; 1.0 = old)
    negative: Optional[str] = None            # e.g. "sheer, see-through, skin showing through fabric"
    consistency: Optional[bool] = None        # stack the dx8152 Consistency LoRA (identity guard)
    identity_lock: Optional[bool] = None      # split-gated late face/hair identity ref (def on)
    clean_garment: Optional[bool] = None      # extract garment ref onto white bg first (def on)
    use_saved_garment: bool = False           # v1.199.5: use the costume's saved outfit ref


@router.post("/clothes/klein-preview")
async def clothes_klein_preview(body: KleinClotheIn, request: Request,
                                session: AsyncSession = Depends(get_session)):
    """Klein clothing PREVIEW: DRESS the character's ACTIVE base render in the
    costume (description slots and/or a garment reference image) via
    ``build_klein_clothes_graph``, and save the result as a costume VERSION.  The
    base rides as a person-minus-clothes reference latent, so identity + body +
    pose are preserved and only the outfit is redrawn.  Base versions untouched."""
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

    host = await _need_host(request, session)
    st = await _settings(session)
    saved = (st.studio_vnccs_settings if st else None) or {}
    name = body.character_name.strip()
    costume = body.costume_name.strip()
    if not name or not costume:
        raise HTTPException(status_code=400, detail="character and costume name required")
    char = (await session.execute(
        _select(StudioCharacter).where(StudioCharacter.name == name))).scalars().first()
    if char is None:
        raise HTTPException(status_code=404, detail=f"character {name!r} not found")
    v = (char.manifest or {}).get("vnccs") or {}
    target_id = body.base_version_id or v.get("active_base")
    bv = next((b for b in (v.get("base_versions") or [])
               if isinstance(b, dict) and b.get("id") == target_id), None)
    if not bv and not body.pose_asset_id:
        raise HTTPException(status_code=409,
                            detail="No base version to dress — generate a base preview first.")

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

    def _flatten_green(data: bytes) -> bytes:
        """Composite an RGBA sprite onto flat chroma green so the dress graph sees
        a solid background like a normal base render (v1.157: pose-sprite dressing)."""
        import io as _io2
        from PIL import Image as _Img
        im = _Img.open(_io2.BytesIO(data))
        if im.mode != "RGBA":
            return data
        bg = _Img.new("RGB", im.size, (0, 177, 64))
        bg.paste(im, mask=im.split()[3])
        buf = _io2.BytesIO()
        bg.save(buf, format="PNG")
        return buf.getvalue()

    want_view = str(body.view or "").strip().lower()
    view_items: list = []  # (view_label, bytes)
    if body.pose_asset_id:
        # v1.157: dress a specific cataloged POSE sprite (Lorenzo picks the pose;
        # upscaled copies take precedence client-side).  Single view.
        data = await _abytes(body.pose_asset_id)
        if not data:
            raise HTTPException(status_code=409, detail="Selected pose sprite is not readable.")
        view_items.append(("front", _flatten_green(data)))
        logger.info("klein clothes preview: dressing pose sprite %s", body.pose_asset_id)
    for vw in ((bv.get("views") or []) if (bv and not body.pose_asset_id) else []):
        vl = str(vw.get("view") or "front")
        if want_view and vl.lower() != want_view:
            continue
        data = await _abytes(vw.get("asset_id"))
        if data:
            view_items.append((vl, data))
    if not view_items and not want_view:
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
    if body.steps:
        steps = max(4, min(32, int(body.steps)))
    _dress_cfg = max(1.0, min(5.0, float(body.guidance or 1.0)))
    _ref_end = max(0.3, min(1.0, float(body.ref_end if body.ref_end is not None else 0.8)))
    _dress_neg = str(body.negative or "").strip()
    logger.info("klein clothes preview: steps=%d guidance=%.2f ref_end=%.2f strength=%.2f neg=%s "
                "identity_lock=%s clean_garment=%s",
                steps, _dress_cfg, _ref_end, float(body.strength or 1.0), bool(_dress_neg),
                body.identity_lock if body.identity_lock is not None else True,
                body.clean_garment if body.clean_garment is not None else True)
    _id_lock = body.identity_lock if body.identity_lock is not None else True
    _clean_g = body.clean_garment if body.clean_garment is not None else True
    _dress_cons = None
    if body.consistency:
        # v1.162: identity guard -- stack the Consistency LoRA on the dressing
        # chain (same file/strength resolution as the pose Consistency stack).
        _dress_cons = klein_poses.resolve_consistency_lora(
            oi, {**saved, "klein_consistency_lora": "on"})
        if _dress_cons:
            logger.info("klein clothes preview: consistency LoRA stacked (%s @ %.2f)",
                        _dress_cons["file"], _dress_cons["strength"])
    _use_fr = body.face_refine if body.face_refine is not None else True
    face_refine = klein_poses.resolve_face_refine(oi, saved) if _use_fr else None
    _live_garment = bool(body.garment_ref and (body.garment_ref or {}).get("name"))
    _saved_garment = (_garment_bytes(str(char.id), costume)
                      if (body.use_saved_garment and not _live_garment) else None)
    has_garment = bool(_live_garment or _saved_garment)
    prompt = klein_poses.klein_clothes_prompt(
        body.costume_info or {}, body.background or "Green",
        has_garment_ref=has_garment, style_kind="auto")
    strength = max(0.05, min(2.0, float(body.strength or 1.0)))
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
        garment_name = None
        _graw = None
        if _live_garment:
            gr = body.garment_ref or {}
            try:
                _graw = client.view_image(gr.get("name", ""), gr.get("subfolder", "") or "",
                                          gr.get("type", "input") or "input", 120)
            except VNCCSError:
                _graw = None
        elif _saved_garment:
            _graw = _saved_garment
        if _graw is not None:
            try:
                raw = _graw
                gn = f"rbmn_klein_{safe}_{token}_garment.png"
                client.upload_image(gn, _clean_png(raw), "", True, 120)
                garment_name = gn
                if _clean_g:
                    # v1.164 garment cleanup pre-pass: extract the garment onto a
                    # plain white background before dressing -- a clean reference
                    # beats telling the swap prompt to ignore the person.
                    try:
                        xg, _xt = klein_poses.build_klein_garment_extract_graph(
                            garment_file=gn, seed=seed, models=models, steps=10,
                            filename_prefix=f"rbmn_vnccs/{safe}/klein_garment")
                        xr = client.submit_prompt(xg, timeout=120)
                        xraw = _wait_first_image_bytes(client, xr.get("prompt_id"), 900)
                        gn2 = f"rbmn_klein_{safe}_{token}_garment_clean.png"
                        client.upload_image(gn2, _clean_png(xraw), "", True, 120)
                        garment_name = gn2
                        logger.info("klein clothes preview: garment cleaned onto white bg")
                    except Exception as _e:  # noqa: BLE001
                        logger.warning("garment cleanup pre-pass failed (%s) -- using raw photo", _e)
            except VNCCSError:
                garment_name = None
        outs = []  # (view, b64)
        for i, (vlabel, data) in enumerate(view_items):
            in_name = f"rbmn_klein_{safe}_{token}_clin{i}.png"
            client.upload_image(in_name, _clean_png(data), "", True, 120)
            graph, _tap = klein_poses.build_klein_clothes_graph(
                base_file=in_name, prompt=prompt, seed=seed, models=models, steps=steps,
                garment_ref_file=garment_name, strength=strength,
                reflatentplus=reflatentplus, face_refine=face_refine, rmbg=rmbg_cfg,
                cfg=(_dress_cfg if _dress_cfg > 1.0 else None),
                ref_end=_ref_end, negative_prompt=_dress_neg,
                consistency_lora=_dress_cons, identity_lock=_id_lock,
                filename_prefix=f"rbmn_vnccs/{safe}/klein_clothes")
            res = client.submit_prompt(graph, timeout=120)
            raw = _wait_first_image_bytes(client, res.get("prompt_id"), 1800)
            outs.append((vlabel, _b64.b64encode(raw).decode("ascii")))
        return outs

    try:
        outs = await asyncio.to_thread(_run)
    except VNCCSError as e:
        raise HTTPException(status_code=502, detail=str(e))
    except Exception as e:  # noqa: BLE001
        logger.exception("klein clothes preview failed")
        raise HTTPException(status_code=500, detail=f"klein clothes preview failed: {e}")
    if not outs:
        raise HTTPException(status_code=502, detail="Clothing preview produced no image.")
    version = None
    try:
        from backend.services.character_studio.vnccs_native.ingest import save_costume_preview
        _front = next((b for vl, b in outs if vl == "front"), outs[0][1])
        _gm = _klein_gen_meta(saved, seed=seed,
                              extra={"engine": "klein-clothes", "clothing_strength": strength,
                                     "garment_ref": bool(has_garment),
                                     "consistency_lora": bool(_dress_cons),
                                     "identity_lock": _id_lock, "clean_garment": _clean_g})
        version = await save_costume_preview(
            session, character_name=name, costume=costume,
            image_b64=_front, costume_info=body.costume_info or {}, gen_meta=_gm)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"klein clothes preview: version save failed: {e}")
    return {"image": outs[0][1],
            "views": [{"view": vl, "image": b} for vl, b in outs],
            "version": version, "host": host, "engine": "klein"}


class TryOnGarment(BaseModel):
    ref: dict                                  # {name, subfolder, type} uploaded garment image
    desc: Optional[str] = ""                   # short description ("red leather jacket")
    slot: Optional[str] = ""                   # top | bottom | shoes | accessory | ...


class TryOnIn(BaseModel):
    character_name: str
    costume_name: Optional[str] = None         # save the result as a version of this costume
    garments: list                             # [TryOnGarment-shaped dicts], 1..3, trained order top->bottom
    person_asset_id: Optional[str] = None      # cataloged pose sprite / image to dress
    person_ref: Optional[dict] = None          # OR a worker image {name,subfolder,type} (layering)
    person_desc: Optional[str] = ""            # short person description for the TRYON prompt
    steps: Optional[int] = 28                  # the LoRA's trained settings (NON-distilled)
    guidance: Optional[float] = 2.5
    clean_garments: Optional[bool] = None      # extract each garment onto white bg first (def on)
    host: Optional[str] = None


@router.post("/clothes/tryon")
async def clothes_tryon(body: TryOnIn, request: Request,
                        session: AsyncSession = Depends(get_session)):
    """Virtual try-on (v1.157): dress a person image (pose sprite / base / previous
    try-on result) in 1-3 GARMENT REFERENCE PHOTOS via the fal
    flux-klein-tryon LoRA (trigger "TRYON", trained image order person/top/bottom,
    steps 28 + guidance 2.5).  Layering: run once per piece, feeding the returned
    ``result_ref`` back as ``person_ref``.  When ``costume_name`` is set the result
    is also saved as a costume VERSION (same path as the classic preview)."""
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

    host = await _need_host(request, session)
    st = await _settings(session)
    saved = (st.studio_vnccs_settings if st else None) or {}
    name = body.character_name.strip()
    garments = [g for g in (body.garments or []) if isinstance(g, dict) and (g.get("ref") or {}).get("name")][:3]
    if not name or not garments:
        raise HTTPException(status_code=400, detail="character and at least one garment image required")
    char = (await session.execute(
        _select(StudioCharacter).where(StudioCharacter.name == name))).scalars().first()
    if char is None:
        raise HTTPException(status_code=404, detail=f"character {name!r} not found")

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

    def _flat(data: bytes) -> bytes:
        im = _Image.open(_io.BytesIO(data))
        if im.mode == "RGBA":
            bg = _Image.new("RGB", im.size, (0, 177, 64))
            bg.paste(im, mask=im.split()[3])
            im = bg
        elif im.mode != "RGB":
            im = im.convert("RGB")
        buf = _io.BytesIO()
        im.save(buf, format="PNG")
        return buf.getvalue()

    # person bytes: worker ref (layering) > cataloged asset > active base render
    person_bytes: Optional[bytes] = None
    if body.person_ref and (body.person_ref or {}).get("name"):
        pass  # fetched worker-side inside _run
    elif body.person_asset_id:
        person_bytes = await _abytes(body.person_asset_id)
        if not person_bytes:
            raise HTTPException(status_code=409, detail="Selected person image is not readable.")
    else:
        v = (char.manifest or {}).get("vnccs") or {}
        active = v.get("active_base")
        bv = next((b for b in (v.get("base_versions") or [])
                   if isinstance(b, dict) and b.get("id") == active), None)
        if bv and bv.get("asset_id"):
            person_bytes = await _abytes(bv.get("asset_id"))
        if not person_bytes:
            raise HTTPException(status_code=409, detail="No person image — pick a pose or generate a base first.")

    try:
        oi = await asyncio.to_thread(_object_info, host)
        models = klein_poses.resolve_klein_models(oi, saved, require_lora=False)
    except VNCCSError as e:
        raise HTTPException(status_code=502, detail=str(e))
    tryon = klein_poses.resolve_tryon_lora(oi, saved)
    if not tryon:
        raise HTTPException(status_code=409,
                            detail="Try-on LoRA (flux-klein-tryon-comfy.safetensors) not found on this worker.")
    rmbg_cfg = klein_poses.resolve_rmbg(oi, saved)

    # TRYON prompt: trigger first (trained), then person, then garment list
    pieces = []
    for g in garments:
        d = str(g.get("desc") or "").strip()
        slot = str(g.get("slot") or "").strip()
        pieces.append(d or (f"the {slot} shown in the reference image" if slot else "the garment shown in the reference image"))
    _pdesc = str(body.person_desc or "").strip() or "the person in the first image"
    prompt = ("TRYON " + _pdesc + ". Replace the outfit with " + " and ".join(pieces) +
              ". Keep the person's face, hair, identity, body proportions and pose EXACTLY the same. "
              "Only the clothing changes. Solid flat green background, evenly lit, no shadows on the background.")
    steps = max(4, min(50, int(body.steps or 28)))
    guidance = max(1.0, min(6.0, float(body.guidance or 2.5)))
    seed = _random.randint(1, 2_000_000_000)
    safe = "".join(ch for ch in name if ch.isalnum())[:24] or "char"
    _clean_g = body.clean_garments if body.clean_garments is not None else True
    logger.info("klein try-on: %d garment(s), steps=%d guidance=%.2f lora=%s @ %.2f clean_garments=%s",
                len(garments), steps, guidance, tryon["file"], tryon["strength"], _clean_g)

    def _run():
        client = _client(host, timeout=120)
        token = _uuid.uuid4().hex[:8]
        # person image onto the worker
        if body.person_ref and (body.person_ref or {}).get("name"):
            pr = body.person_ref or {}
            raw = client.view_image(pr.get("name", ""), pr.get("subfolder", "") or "",
                                    pr.get("type", "input") or "input", 120)
            person_name = f"rbmn_tryon_{safe}_{token}_person.png"
            client.upload_image(person_name, _flat(raw), "", True, 120)
        else:
            person_name = f"rbmn_tryon_{safe}_{token}_person.png"
            client.upload_image(person_name, _flat(person_bytes), "", True, 120)
        garment_names = []
        for gi, g in enumerate(garments):
            gr = g.get("ref") or {}
            raw = client.view_image(gr.get("name", ""), gr.get("subfolder", "") or "",
                                    gr.get("type", "input") or "input", 120)
            gn = f"rbmn_tryon_{safe}_{token}_g{gi}.png"
            client.upload_image(gn, _flat(raw), "", True, 120)
            if _clean_g:
                # v1.164 garment cleanup pre-pass (per garment): a clean
                # garment-on-white reference measurably improves try-on accuracy.
                try:
                    xg, _xt = klein_poses.build_klein_garment_extract_graph(
                        garment_file=gn, seed=seed + gi + 1, models=models, steps=10,
                        filename_prefix=f"rbmn_vnccs/{safe}/klein_garment")
                    xr = client.submit_prompt(xg, timeout=120)
                    xraw = _wait_first_image_bytes(client, xr.get("prompt_id"), 900)
                    gn2 = f"rbmn_tryon_{safe}_{token}_g{gi}_clean.png"
                    client.upload_image(gn2, _flat(xraw), "", True, 120)
                    gn = gn2
                    logger.info("klein try-on: garment %d cleaned onto white bg", gi + 1)
                except Exception as _e:  # noqa: BLE001
                    logger.warning("try-on garment %d cleanup failed (%s) -- using raw photo", gi + 1, _e)
            garment_names.append(gn)
        graph, _tap = klein_poses.build_klein_tryon_graph(
            person_file=person_name, garment_files=garment_names, prompt=prompt,
            seed=seed, models=models, tryon=tryon, steps=steps, guidance=guidance,
            rmbg=None,  # keep the flat background; costume save wants the full frame
            filename_prefix=f"rbmn_vnccs/{safe}/klein_tryon")
        res = client.submit_prompt(graph, timeout=120)
        raw = _wait_first_image_bytes(client, res.get("prompt_id"), 3600)
        # re-upload the RESULT as a worker input so the next layer can chain on it
        rn = f"rbmn_tryon_{safe}_{token}_result.png"
        client.upload_image(rn, raw, "", True, 120)
        return _b64.b64encode(raw).decode("ascii"), rn

    try:
        img_b64, result_name = await asyncio.to_thread(_run)
    except VNCCSError as e:
        raise HTTPException(status_code=502, detail=str(e))
    except Exception as e:  # noqa: BLE001
        logger.exception("klein try-on failed")
        raise HTTPException(status_code=500, detail=f"try-on failed: {e}")
    version = None
    if (body.costume_name or "").strip():
        try:
            from backend.services.character_studio.vnccs_native.ingest import save_costume_preview
            _gm = _klein_gen_meta(saved, seed=seed,
                                  extra={"engine": "klein-tryon", "garments": len(garments),
                                         "steps": steps, "guidance": guidance,
                                         "clean_garments": _clean_g})
            version = await save_costume_preview(
                session, character_name=name, costume=body.costume_name.strip(),
                image_b64=img_b64, costume_info={}, gen_meta=_gm)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"try-on: costume version save failed: {e}")
    return {"image": img_b64, "result_ref": {"name": result_name, "subfolder": "", "type": "input"},
            "version": version, "host": host, "engine": "klein-tryon"}


class BaseSegmentIn(BaseModel):
    character_name: str
    prompt: str                                # what to find ("earrings, necklace", "hair", ...)
    threshold: float = 0.3
    base_version_id: Optional[str] = None      # default: active base (front view)
    costume_name: Optional[str] = None         # segment a costume version instead of the base
    host: Optional[str] = None


class BaseInpaintIn(BaseModel):
    character_name: str
    mask_b64: str                              # white-on-black PNG (brush + selected segments)
    prompt: str
    negative: Optional[str] = ""
    steps: Optional[int] = 12
    guidance: Optional[float] = 1.0            # >1 activates the negative
    grow: Optional[int] = 6                    # mask expand px
    blur: Optional[float] = 4.0                # mask feather
    refs: Optional[list] = None                # up to 3 {name,subfolder,type} reference images
    base_version_id: Optional[str] = None      # default: active base (front view)
    costume_name: Optional[str] = None         # edit a costume version instead of the base
    host: Optional[str] = None


async def _edit_target_bytes(session, name: str, base_version_id: Optional[str],
                             costume_name: Optional[str]):
    """Resolve the Edit-Image target: a costume's active version when
    ``costume_name`` is set, else the requested/active BASE version's front
    view.  Returns (bytes, target_id) or raises 404/409."""
    from uuid import UUID as _UUID
    from pathlib import Path as _Path
    from sqlmodel import select as _select
    from backend.config import settings as _cfg
    from backend.database.models import Asset, StudioCharacter
    char = (await session.execute(
        _select(StudioCharacter).where(StudioCharacter.name == name))).scalars().first()
    if char is None:
        raise HTTPException(status_code=404, detail=f"character {name!r} not found")
    v = (char.manifest or {}).get("vnccs") or {}

    async def _abytes(aid):
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

    if costume_name:
        entry = (v.get("costumes") or {}).get(costume_name) or {}
        active = entry.get("active")
        cv = next((x for x in (entry.get("versions") or [])
                   if isinstance(x, dict) and x.get("id") == active), None)
        if not cv or not cv.get("asset_id"):
            raise HTTPException(status_code=409, detail=f"costume {costume_name!r} has no saved version yet")
        data = await _abytes(cv["asset_id"])
        if not data:
            raise HTTPException(status_code=409, detail="costume image is not readable")
        return data, cv.get("id")
    target_id = base_version_id or v.get("active_base")
    bv = next((b for b in (v.get("base_versions") or [])
               if isinstance(b, dict) and b.get("id") == target_id), None)
    if not bv:
        raise HTTPException(status_code=409, detail="No base version — generate a base preview first.")
    aid = next((vw.get("asset_id") for vw in (bv.get("views") or [])
                if str(vw.get("view") or "front") == "front"), None) or bv.get("asset_id")
    data = await _abytes(aid)
    if not data:
        raise HTTPException(status_code=409, detail="base image is not readable")
    return data, bv.get("id")


@router.post("/base/segment")
async def base_segment(body: BaseSegmentIn, request: Request,
                       session: AsyncSession = Depends(get_session)):
    """Edit-Image SEGMENT mode (v1.158): run SAM3 text-prompted detection on the
    base (or a costume version) and return EVERY detected mask as a white-on-
    black PNG, so the client can offer a pick-one-or-many list."""
    import base64 as _b64
    import random as _random
    import uuid as _uuid
    from backend.services.character_studio.vnccs_native import klein_poses

    host = await _need_host(request, session)
    st = await _settings(session)
    saved = (st.studio_vnccs_settings if st else None) or {}
    name = body.character_name.strip()
    if not name or not body.prompt.strip():
        raise HTTPException(status_code=400, detail="character and a detection prompt are required")
    data, target_id = await _edit_target_bytes(session, name, body.base_version_id, body.costume_name)
    try:
        oi = await asyncio.to_thread(_object_info, host)
    except VNCCSError as e:
        raise HTTPException(status_code=502, detail=str(e))
    if klein_poses.SAM3_SEG_CLASS not in (oi or {}):
        raise HTTPException(status_code=409, detail="SAM3 nodes (comfyui-easy-sam3) are not on this worker.")
    opts = klein_poses._options(oi, klein_poses.SAM3_LOADER_CLASS, "model")
    sam_model = (klein_poses._resolve_name(opts, str(saved.get("klein_sam_cleanup_model") or klein_poses.SAM3_DEFAULT_MODEL))
                 or (opts[0] if opts else klein_poses.SAM3_DEFAULT_MODEL))
    safe = "".join(ch for ch in name if ch.isalnum())[:24] or "char"

    def _run():
        client = _client(host, timeout=120)
        token = _uuid.uuid4().hex[:8]
        in_name = f"rbmn_edit_{safe}_{token}_seg.png"
        client.upload_image(in_name, data, "", True, 120)
        graph, _tap = klein_poses.build_sam3_detect_graph(
            image_file=in_name, prompt=body.prompt.strip(),
            threshold=max(0.05, min(0.95, float(body.threshold or 0.3))),
            sam_model=sam_model, filename_prefix=f"rbmn_vnccs/{safe}/sam_detect")
        res = client.submit_prompt(graph, timeout=120)
        return _klein_wait_all_images(client, res.get("prompt_id"), 600)
    try:
        masks = await asyncio.to_thread(_run)
    except VNCCSError as e:
        raise HTTPException(status_code=502, detail=str(e))
    except Exception as e:  # noqa: BLE001
        logger.exception("base segment failed")
        raise HTTPException(status_code=500, detail=f"segment failed: {e}")
    logger.info("edit-image segment: %d mask(s) for %r on %s", len(masks or []), body.prompt, target_id)
    return {"segments": masks or [], "target_id": target_id, "host": host}


@router.post("/base/inpaint")
async def base_inpaint(body: BaseInpaintIn, request: Request,
                       session: AsyncSession = Depends(get_session)):
    """Edit-Image APPLY (v1.158): masked Klein inpaint on the base (or a costume
    version) -- brush/segment mask + prompt + up to 3 reference images ("add the
    makeup from image 1", "the tattoo in image 2 on the selected arm").  Only
    the masked region changes (ImageCompositeMasked).  The result saves as a NEW
    base version (or costume version) that becomes ACTIVE -- so edits layer:
    run again and you edit the result; the version arrows are the revision
    history and Set-active is the final pick."""
    import base64 as _b64
    import random as _random
    import uuid as _uuid
    import io as _io
    from PIL import Image as _Image
    from backend.services.character_studio.vnccs_native import klein_poses

    host = await _need_host(request, session)
    st = await _settings(session)
    saved = (st.studio_vnccs_settings if st else None) or {}
    name = body.character_name.strip()
    if not name or not body.prompt.strip() or not body.mask_b64:
        raise HTTPException(status_code=400, detail="character, prompt and a mask are required")
    data, target_id = await _edit_target_bytes(session, name, body.base_version_id, body.costume_name)
    try:
        mask_raw = _b64.b64decode(body.mask_b64.split(",")[-1])
    except Exception:  # noqa: BLE001
        raise HTTPException(status_code=400, detail="mask_b64 is not valid base64 PNG data")
    im = _Image.open(_io.BytesIO(data))
    width, height = im.size
    try:
        oi = await asyncio.to_thread(_object_info, host)
        models = klein_poses.resolve_klein_models(oi, saved, require_lora=False)
    except VNCCSError as e:
        raise HTTPException(status_code=502, detail=str(e))
    refs = [r for r in (body.refs or []) if isinstance(r, dict) and r.get("name")][:3]
    steps = max(4, min(32, int(body.steps or 12)))
    guidance = max(1.0, min(5.0, float(body.guidance or 1.0)))
    seed = _random.randint(1, 2_000_000_000)
    safe = "".join(ch for ch in name if ch.isalnum())[:24] or "char"
    logger.info("edit-image inpaint: target=%s costume=%s steps=%d guidance=%.2f refs=%d",
                target_id, body.costume_name or "-", steps, guidance, len(refs))

    def _run():
        client = _client(host, timeout=120)
        token = _uuid.uuid4().hex[:8]
        in_name = f"rbmn_edit_{safe}_{token}_img.png"
        mk_name = f"rbmn_edit_{safe}_{token}_mask.png"
        client.upload_image(in_name, data, "", True, 120)
        client.upload_image(mk_name, mask_raw, "", True, 120)
        ref_names = []
        for ri, r in enumerate(refs):
            raw = client.view_image(r.get("name", ""), r.get("subfolder", "") or "",
                                    r.get("type", "input") or "input", 120)
            rn = f"rbmn_edit_{safe}_{token}_ref{ri}.png"
            client.upload_image(rn, raw, "", True, 120)
            ref_names.append(rn)
        graph, _tap = klein_poses.build_klein_inpaint_graph(
            image_file=in_name, mask_file=mk_name, prompt=body.prompt.strip(),
            seed=seed, models=models, steps=steps, guidance=guidance,
            negative_prompt=str(body.negative or ""), ref_files=ref_names,
            grow=max(0, min(64, int(body.grow if body.grow is not None else 6))),
            blur=max(0.0, min(32.0, float(body.blur if body.blur is not None else 4.0))),
            width=width, height=height,
            filename_prefix=f"rbmn_vnccs/{safe}/klein_edit")
        res = client.submit_prompt(graph, timeout=120)
        raw = _wait_first_image_bytes(client, res.get("prompt_id"), 1800)
        return _b64.b64encode(raw).decode("ascii")
    try:
        img_b64 = await asyncio.to_thread(_run)
    except VNCCSError as e:
        raise HTTPException(status_code=502, detail=str(e))
    except Exception as e:  # noqa: BLE001
        logger.exception("edit-image inpaint failed")
        raise HTTPException(status_code=500, detail=f"inpaint failed: {e}")
    version = None
    try:
        _gm = _klein_gen_meta(saved, seed=seed,
                              extra={"engine": "klein-inpaint", "parent_version": target_id,
                                     "edit_prompt": body.prompt.strip()[:300], "refs": len(refs)})
        if body.costume_name:
            from backend.services.character_studio.vnccs_native.ingest import save_costume_preview
            version = await save_costume_preview(
                session, character_name=name, costume=body.costume_name.strip(),
                image_b64=img_b64, costume_info={}, gen_meta=_gm)
        else:
            from backend.services.character_studio.vnccs_native.ingest import save_base_preview
            version = await save_base_preview(
                session, character_name=name, image_b64=img_b64, variant="klein", gen_meta=_gm)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"edit-image inpaint: version save failed: {e}")
    return {"image": img_b64, "version": version, "target_id": target_id, "host": host}


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


# ── Saved outfit reference images (v1.199.5) ────────────────────────────────
# An outfit's reference photo is persisted app-side (per character + costume) so
# it survives page reloads, worker restarts, and costume switches — you can come
# back later and re-render / tweak settings with the SAME reference.  Stored under
# <project_dir>/_studio/garments/<character_id>/<safe_costume>.png.
def _garment_store_path(character_id: str, costume: str):
    from pathlib import Path as _P
    from backend.config import settings as _c
    safe = "".join(ch for ch in str(costume) if ch.isalnum() or ch in (" ", "_", "-")).strip().replace(" ", "_") or "outfit"
    cid = "".join(ch for ch in str(character_id) if ch.isalnum() or ch == "-") or "char"
    d = _P(_c.project_dir) / "_studio" / "garments" / cid
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{safe}.png"


def _garment_bytes(character_id: str, costume: str) -> Optional[bytes]:
    try:
        p = _garment_store_path(character_id, costume)
        return p.read_bytes() if p.exists() else None
    except Exception:  # noqa: BLE001
        return None


class GarmentSaveIn(BaseModel):
    ref: dict = {}                            # {name, subfolder, type} on the worker


@router.post("/clothes/garment/{character_id}/{costume}/save")
async def save_garment_ref(character_id: str, costume: str, body: GarmentSaveIn,
                           request: Request, session: AsyncSession = Depends(get_session)):
    """Persist the outfit reference for a costume: pull the uploaded image off the
    worker and store it app-side so it can be reused/re-rendered later."""
    import io as _io3
    from PIL import Image as _Img3
    host = await _need_host(request, session)
    ref = body.ref or {}
    if not ref.get("name"):
        raise HTTPException(status_code=400, detail="no garment ref to save")

    def _fetch() -> bytes:
        client = _client(host, timeout=120)
        raw = client.view_image(ref.get("name", ""), ref.get("subfolder", "") or "",
                                ref.get("type", "input") or "input", 120)
        im = _Img3.open(_io3.BytesIO(raw))
        if im.mode not in ("RGB", "RGBA"):
            im = im.convert("RGBA")
        buf = _io3.BytesIO()
        im.save(buf, format="PNG")
        return buf.getvalue()

    try:
        data = await asyncio.to_thread(_fetch)
    except VNCCSError as e:
        raise HTTPException(status_code=502, detail=str(e))
    _garment_store_path(character_id, costume).write_bytes(data)
    # best-effort flag on the manifest so listings can show a saved-ref badge
    try:
        from uuid import UUID as _U3
        from backend.database.models import StudioCharacter
        c = await session.get(StudioCharacter, _U3(character_id))
        if c is not None:
            manifest = dict(c.manifest or {})
            v = dict(manifest.get("vnccs") or {})
            costumes = dict(v.get("costumes") or {})
            entry = dict(costumes.get(costume) or {})
            entry["garment"] = {"saved": True}
            costumes[costume] = entry
            v["costumes"] = costumes
            manifest["vnccs"] = v
            c.manifest = manifest
            await session.commit()
    except Exception:  # noqa: BLE001
        pass
    return {"ok": True, "url": f"/api/studio/vnccs/clothes/garment/{character_id}/{costume}"}


@router.get("/clothes/garment/{character_id}/{costume}/meta")
async def garment_ref_meta(character_id: str, costume: str):
    p = _garment_store_path(character_id, costume)
    exists = p.exists()
    return {"exists": exists,
            "url": (f"/api/studio/vnccs/clothes/garment/{character_id}/{costume}" if exists else None)}


@router.get("/clothes/garment/{character_id}/{costume}")
async def garment_ref_image(character_id: str, costume: str):
    from fastapi.responses import FileResponse as _FR
    p = _garment_store_path(character_id, costume)
    if not p.exists():
        raise HTTPException(status_code=404, detail="no saved outfit reference")
    return _FR(str(p), media_type="image/png")


@router.delete("/clothes/garment/{character_id}/{costume}")
async def delete_garment_ref(character_id: str, costume: str):
    p = _garment_store_path(character_id, costume)
    try:
        p.unlink(missing_ok=True)
    except Exception:  # noqa: BLE001
        pass
    return {"ok": True}


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


class GarmentAnalyzeIn(BaseModel):
    images: list                               # 1..4 uploaded garment refs {name,subfolder,type}
    host: Optional[str] = None


@router.post("/wizard/garment-analyze")
async def wizard_garment_analyze(body: GarmentAnalyzeIn, request: Request,
                                 session: AsyncSession = Depends(get_session)):
    """Vision-scan outfit reference image(s) (v1.160): STAGE 1 the vision model
    DESCRIBES each garment photo in prose; STAGE 2 the existing Clothes-Wizard
    text model synthesises the costume SLOT fields (top/bottom/head/face/shoes)
    from those descriptions -- so the slots match the reference image(s) and the
    dress prompt pulls in the same direction as the garment reference latents."""
    imgs = [i for i in (body.images or []) if isinstance(i, dict) and i.get("name")][:4]
    if not imgs:
        raise HTTPException(status_code=400, detail="No garment reference images provided")
    host, _ = await _resolve_host(request, session)
    if not host:
        raise HTTPException(status_code=503, detail="No VNCCS host available (the references live on the host).")
    urls, text_model, vision_model = await _ollama_cfg(session)
    if not urls or not vision_model or not text_model:
        raise HTTPException(status_code=503, detail=(
            "Ollama vision/text models are not configured (Settings -> Ollama URL + models)."))
    GARMENT_DESCRIBE_SYSTEM = (
        "You are a fashion cataloguer. Describe ONLY the clothing/garments/"
        "accessories visible in the image, in precise product-listing prose: for "
        "each piece give its type, colour(s), pattern, material/fabric, cut/fit, "
        "length, notable details (buttons, zips, straps, lace, prints, logos) and "
        "where on the body it is worn. Ignore the person, pose, face and "
        "background entirely. Plain prose, no lists, no JSON.")
    GARMENT_DESCRIBE = "Describe every garment and accessory in this image."
    vision: list = []
    descriptions: list = []
    for gi, gimg in enumerate(imgs):
        try:
            rb = await asyncio.to_thread(
                _client(host, timeout=60).view_image,
                gimg.get("name", ""), gimg.get("subfolder", "") or "",
                gimg.get("type", "input") or "input", 60)
        except VNCCSError as e:
            logger.warning("garment-analyze: could not fetch %s: %s", gimg.get("name"), e)
            continue
        try:
            desc = await asyncio.to_thread(
                _wiz.ollama_chat_sync, urls, vision_model,
                GARMENT_DESCRIBE_SYSTEM, GARMENT_DESCRIBE,
                [_wiz.image_bytes_to_b64(rb)], 0.2, 180.0, False)
        except Exception as e:  # noqa: BLE001
            logger.warning("garment-analyze: describe image %d failed: %s", gi, e)
            desc = None
        if desc and desc.strip():
            descriptions.append(desc.strip())
            vision.append({"name": str(gimg.get("name") or ""), "description": desc.strip()})
    if not descriptions:
        raise HTTPException(status_code=502,
                            detail="The vision model returned no usable description for any garment image.")
    combined = "\n\n".join((f"Garment reference image {i + 1}: {d}" for i, d in enumerate(descriptions)))
    sys_p, usr_p = _wiz.clothes_wizard_prompts(combined)
    content = await asyncio.to_thread(_wiz.ollama_chat_sync, urls, text_model, sys_p, usr_p, None, 0.35)
    fields = _wiz.parse_clothes_wizard_output(content or "")
    if fields is None:
        raise HTTPException(status_code=502, detail="The Clothes Wizard returned unparseable output.")
    logger.info("garment-analyze: %d image(s) described -> slots %s",
                len(descriptions), [k for k, v in (fields or {}).items() if v])
    return {"source": "ollama-vision", "fields": fields, "vision": vision}


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
                         "costume": mv.get("costume"),
                         "pose_name": mv.get("pose_name"),
                         "engine": mv.get("engine"),
                         "upscaled": bool(mv.get("upscaled")),
                         "upscale_source": mv.get("upscale_source")})
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


# --------------------------------------------------------------------------- #
# v1.171 -- Settings Variation Test (Debug Options)
#
# Generates a batch of renders across setting variations into
# <project_dir>/varitests/<id>/ (images + manifest.json with every override),
# so Lorenzo can walk away, come back, thumbs-up/down the results, and get a
# settings report.  Two families:
#   * base_new / base_clone -- each variation runs the REAL /preview path
#     (default neutral base pose; varitest=True skips cataloging).
#   * pose_set -- each variation runs the REAL Klein pose pipeline
#     (_klein_submit) on the selected poses with a merged settings copy.
# One shared seed across variations by default so only the SETTINGS differ.
# --------------------------------------------------------------------------- #

_VARITESTS: dict = {}          # id -> asyncio.Task (running only)


def _vt_dir(run_id: str = ""):
    from pathlib import Path as _Path
    from backend.config import settings as _cfg
    base = _Path(_cfg.project_dir) / "varitests"
    return base / run_id if run_id else base


def _vt_manifest_path(run_id: str):
    return _vt_dir(run_id) / "manifest.json"


def _vt_load(run_id: str) -> Optional[dict]:
    import json as _json
    p = _vt_manifest_path(run_id)
    try:
        return _json.loads(p.read_text(encoding="utf-8")) if p.exists() else None
    except Exception:  # noqa: BLE001
        return None


def _vt_save(man: dict) -> None:
    import json as _json
    d = _vt_dir(man["id"])
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / "manifest.json.tmp"
    tmp.write_text(_json.dumps(man, indent=1), encoding="utf-8")
    tmp.replace(d / "manifest.json")


def _vt_variations(axes: dict, max_runs: int) -> list:
    """Cartesian product of the chosen axes, evenly sampled down to max_runs.
    Item 0 is always the BASELINE (no overrides -- the current settings)."""
    import itertools
    keys = [k for k, vs in (axes or {}).items() if isinstance(vs, list) and vs]
    combos = [dict(zip(keys, vals))
              for vals in itertools.product(*[axes[k] for k in keys])] if keys else []
    max_runs = max(2, min(48, int(max_runs or 12)))
    budget = max_runs - 1                      # slot 0 = baseline
    if len(combos) > budget and budget > 0:
        stride = len(combos) / budget
        combos = [combos[int(i * stride)] for i in range(budget)]
    return [{}] + combos


class VaritestStartIn(BaseModel):
    character_name: str
    test_type: str                             # 'base_new' | 'base_clone' | 'pose_set'
    axes: dict = {}                            # {settings_key: [values]}
    max_runs: Optional[int] = 12
    same_seed: Optional[bool] = True
    seed: Optional[int] = None
    # pose_set only: the poses to render per variation (base tests use the
    # preview path's own default neutral pose -- no pose selection there)
    poses: Optional[list] = None               # [{pose dict}]
    pose_names: Optional[list] = None
    # context mirrored from the studio form
    character_info: dict = {}
    cloner_images: Optional[list] = None
    nsfw: Optional[bool] = None
    background: Optional[str] = "Green"
    face_kind: Optional[str] = None
    style_custom: Optional[str] = None
    base_clothing: Optional[str] = None
    canvas_w: Optional[int] = None
    host: Optional[str] = None


async def _vt_run(run_id: str, body: VaritestStartIn, request: Request):
    """Background runner.  Own DB sessions; updates the manifest after every
    item so a browser refresh (or app restart mid-run) loses nothing."""
    import base64 as _b64
    import random as _random
    import time as _time
    from backend.database.database import async_session as _asession

    man = _vt_load(run_id)
    if man is None:
        return
    variations = [it["overrides"] for it in man["items_plan"]]
    seed0 = int(body.seed or _random.randint(1, 2_000_000_000))
    try:
        async with _asession() as session:
            pinned = await _need_host(request, session)
            _, st = await _resolve_host(request, session)
            comfy = getattr(request.app.state, "comfy_dispatcher", None)
            configured = (st.studio_vnccs_host or None) if st else None
            hosts = list_vnccs_hosts(comfy, configured) or [pinned]
            saved = (st.studio_vnccs_settings if st else None) or {}

            identity = None
            poses = [p for p in (body.poses or []) if isinstance(p, dict)]
            if body.test_type == "pose_set":
                gen_body = GenerateIn(character_name=body.character_name,
                                      character_info=body.character_info or {},
                                      cloner_images=body.cloner_images,
                                      background=body.background or "Green",
                                      engine="klein",
                                      face_kind=body.face_kind,
                                      style_custom=body.style_custom,
                                      canvas_w=body.canvas_w)
                identity = await _klein_identity_bytes(session, gen_body, pinned, True)

        item_idx = 0
        for vi, overrides in enumerate(variations):
            if _vt_load(run_id).get("status") == "cancelled":
                return
            merged = {**saved, **{k: v for k, v in overrides.items() if v is not None}}
            seed = seed0 if (body.same_seed is not False) else seed0 + vi * 101
            host_i = hosts[vi % len(hosts)]
            t0 = _time.time()
            try:
                if body.test_type == "pose_set":
                    gen_body = GenerateIn(character_name=body.character_name,
                                          character_info=body.character_info or {},
                                          cloner_images=body.cloner_images,
                                          background=body.background or "Green",
                                          engine="klein",
                                          face_kind=body.face_kind,
                                          style_custom=body.style_custom,
                                          lock_base=True,
                                          canvas_w=body.canvas_w)
                    prompt_id, _tap, _x = await asyncio.to_thread(
                        _klein_submit, host_i, merged, gen_body, poses, identity, seed)
                    client = _client(host_i, timeout=120)
                    imgs = await asyncio.to_thread(
                        _wait_all_image_bytes, client, prompt_id, 3600)
                    for pi, raw in enumerate(imgs):
                        pn = (body.pose_names[pi] if body.pose_names
                              and pi < len(body.pose_names) else f"pose {pi + 1}")
                        _vt_record(run_id, item_idx, overrides, seed, host_i,
                                   _time.time() - t0, raw, pose_name=pn, baseline=(vi == 0))
                        item_idx += 1
                else:
                    pv = PreviewIn(character_name=body.character_name,
                                   character_info=body.character_info or {},
                                   nsfw=bool(body.nsfw), background=body.background or "Green",
                                   engine="klein",
                                   base_clothing=body.base_clothing,
                                   face_kind=body.face_kind, style_custom=body.style_custom,
                                   cloner_images=(body.cloner_images
                                                  if body.test_type == "base_clone" else None),
                                   base_set=False, canvas_w=body.canvas_w,
                                   gen_settings={"seed_mode": "fixed", "seed": seed},
                                   settings_overrides=overrides, varitest=True)
                    async with _asession() as s2:
                        r = await generate_preview(pv, request, s2)
                    raw = _b64.b64decode(r.get("image") or "")
                    if not raw:
                        raise VNCCSError("preview returned no image")
                    _vt_record(run_id, item_idx, overrides, seed, pinned,
                               _time.time() - t0, raw, baseline=(vi == 0))
                    item_idx += 1
            except Exception as e:  # noqa: BLE001 -- record the failure, keep sweeping
                logger.warning(f"varitest {run_id}: variation {vi} failed: {e}")
                _vt_record(run_id, item_idx, overrides, seed, host_i,
                           _time.time() - t0, None, error=str(e)[:300], baseline=(vi == 0))
                item_idx += 1
        man = _vt_load(run_id)
        if man and man.get("status") == "running":
            man["status"] = "done"
            _vt_save(man)
    except Exception as e:  # noqa: BLE001
        logger.exception(f"varitest {run_id} crashed")
        man = _vt_load(run_id) or man
        man["status"] = "error"
        man["error"] = str(e)[:400]
        _vt_save(man)
    finally:
        _VARITESTS.pop(run_id, None)


def _wait_all_image_bytes(client, prompt_id: str, timeout: int) -> list:
    """Like _wait_first_image_bytes but returns EVERY output image of the
    prompt (a pose-set graph batches all poses into one SaveImage)."""
    import time as _time
    deadline = _time.time() + timeout
    while _time.time() < deadline:
        try:
            hist = client.get_history(prompt_id, timeout=60)
        except Exception:  # noqa: BLE001
            hist = None
        rec = (hist or {}).get(prompt_id) if isinstance(hist, dict) else None
        if rec and isinstance(rec.get("outputs"), dict):
            outs = []
            for node in rec["outputs"].values():
                for im in (node or {}).get("images", []) or []:
                    try:
                        outs.append(client.view_image(im.get("filename", ""),
                                                      im.get("subfolder", "") or "",
                                                      im.get("type", "output") or "output", 120))
                    except Exception:  # noqa: BLE001
                        continue
            if outs:
                return outs
            st = (rec.get("status") or {})
            if st.get("completed") or st.get("status_str") in ("success", "error"):
                return []
        _time.sleep(3)
    raise VNCCSError("varitest render timed out")


def _vt_record(run_id: str, idx: int, overrides: dict, seed: int, host: str,
               elapsed: float, raw: Optional[bytes], pose_name: Optional[str] = None,
               error: Optional[str] = None, baseline: bool = False) -> None:
    man = _vt_load(run_id)
    if man is None:
        return
    fn = f"{idx:03d}.png"
    if raw:
        (_vt_dir(run_id) / fn).write_bytes(raw)
    man.setdefault("items", []).append({
        "index": idx, "file": fn if raw else None, "overrides": overrides,
        "seed": seed, "host": host, "elapsed": round(elapsed, 1),
        "pose_name": pose_name, "rating": 0, "error": error,
        "baseline": baseline,
    })
    man["progress"] = {"done": len(man["items"]), "total": man["progress"]["total"]}
    _vt_save(man)


@router.post("/varitest/start")
async def varitest_start(body: VaritestStartIn, request: Request,
                         session: AsyncSession = Depends(get_session)):
    import random as _random
    import uuid as _uuid
    from datetime import datetime as _dt
    if body.test_type not in ("base_new", "base_clone", "pose_set"):
        raise HTTPException(status_code=400, detail="unknown test_type")
    if body.test_type == "pose_set" and not (body.poses or []):
        raise HTTPException(status_code=400, detail="pose_set test needs at least one pose")
    if body.test_type == "base_clone" and not (body.cloner_images or []):
        raise HTTPException(status_code=400, detail="base_clone test needs reference images")
    variations = _vt_variations(body.axes or {}, body.max_runs or 12)
    per_var = len(body.poses or []) if body.test_type == "pose_set" else 1
    total = len(variations) * max(1, per_var)
    run_id = "vt_" + _uuid.uuid4().hex[:10]
    st = await _settings(session)
    saved = (st.studio_vnccs_settings if st else None) or {}
    man = {"id": run_id, "created": _dt.utcnow().isoformat() + "Z",
           "test_type": body.test_type, "character": body.character_name,
           "status": "running", "error": None,
           "axes": body.axes or {}, "max_runs": body.max_runs,
           "same_seed": body.same_seed is not False,
           "seed": int(body.seed or 0) or None,
           "pose_names": body.pose_names,
           "base_settings": {k: v for k, v in saved.items()
                             if isinstance(k, str) and (k.startswith("klein_") or k.startswith("qwen_"))},
           "progress": {"done": 0, "total": total},
           "items_plan": [{"variation": i, "overrides": ov} for i, ov in enumerate(variations)],
           "items": []}
    if body.seed is None:
        body.seed = _random.randint(1, 2_000_000_000)
    man["seed"] = body.seed
    _vt_save(man)
    task = asyncio.create_task(_vt_run(run_id, body, request))
    _VARITESTS[run_id] = task
    return {"id": run_id, "total": total, "variations": len(variations)}


@router.get("/varitest/list")
async def varitest_list():
    out = []
    base = _vt_dir()
    if base.exists():
        for d in sorted(base.iterdir(), reverse=True):
            man = _vt_load(d.name)
            if not man:
                continue
            rated = sum(1 for it in man.get("items", []) if it.get("rating"))
            out.append({"id": man["id"], "created": man.get("created"),
                        "test_type": man.get("test_type"), "character": man.get("character"),
                        "status": man.get("status"), "progress": man.get("progress"),
                        "rated": rated})
    return {"runs": out[:50]}


@router.get("/varitest/{run_id}")
async def varitest_get(run_id: str):
    man = _vt_load(run_id)
    if not man:
        raise HTTPException(status_code=404, detail="unknown varitest run")
    return man


@router.get("/varitest/{run_id}/image/{idx}")
async def varitest_image(run_id: str, idx: int):
    from fastapi.responses import FileResponse
    p = _vt_dir(run_id) / f"{int(idx):03d}.png"
    if not p.exists():
        raise HTTPException(status_code=404, detail="no image for this item")
    return FileResponse(str(p), media_type="image/png")


class VaritestRateIn(BaseModel):
    index: int
    rating: int                                # 1 (up) | -1 (down) | 0 (clear)


@router.post("/varitest/{run_id}/rate")
async def varitest_rate(run_id: str, body: VaritestRateIn):
    man = _vt_load(run_id)
    if not man:
        raise HTTPException(status_code=404, detail="unknown varitest run")
    for it in man.get("items", []):
        if it.get("index") == body.index:
            it["rating"] = max(-1, min(1, int(body.rating)))
            _vt_save(man)
            return {"ok": True, "rating": it["rating"]}
    raise HTTPException(status_code=404, detail="unknown item index")


@router.post("/varitest/{run_id}/cancel")
async def varitest_cancel(run_id: str):
    man = _vt_load(run_id)
    if not man:
        raise HTTPException(status_code=404, detail="unknown varitest run")
    if man.get("status") == "running":
        man["status"] = "cancelled"
        _vt_save(man)
    return {"ok": True, "status": man["status"]}


def _vt_analyze(man: dict) -> dict:
    """Per-axis score table + best/worst combos + concrete suggestions from
    the thumbs data.  score = (ups - downs) / rated, only over rated items."""
    items = [it for it in man.get("items", []) if not it.get("error")]
    rated = [it for it in items if it.get("rating")]
    axes = man.get("axes") or {}
    axis_tables = {}
    suggestions = []
    for key, values in axes.items():
        rows = []
        for v in values + ["(baseline)"]:
            if v == "(baseline)":
                grp = [it for it in rated if it.get("baseline")]
            else:
                grp = [it for it in rated
                       if not it.get("baseline") and str(it["overrides"].get(key)) == str(v)]
            ups = sum(1 for it in grp if it["rating"] > 0)
            downs = sum(1 for it in grp if it["rating"] < 0)
            n = len(grp)
            rows.append({"value": v, "rated": n, "ups": ups, "downs": downs,
                         "score": round((ups - downs) / n, 2) if n else None})
        axis_tables[key] = rows
        scored = [r for r in rows if r["score"] is not None and r["value"] != "(baseline)"
                  and r["rated"] >= 2]
        if len(scored) >= 2:
            best = max(scored, key=lambda r: r["score"])
            worst = min(scored, key=lambda r: r["score"])
            if best["score"] - worst["score"] >= 0.5:
                suggestions.append({"setting": key, "use": best["value"],
                                    "avoid": worst["value"],
                                    "confidence": f"{best['score']:+.2f} vs {worst['score']:+.2f} over {best['rated']}+{worst['rated']} rated"})
    ups_items = sorted([it for it in rated if it["rating"] > 0],
                       key=lambda it: -it["rating"])
    downs_items = [it for it in rated if it["rating"] < 0]
    return {"rated": len(rated), "total": len(items),
            "axis_tables": axis_tables, "suggestions": suggestions,
            "liked": [{"index": it["index"], "pose": it.get("pose_name"),
                       "overrides": it["overrides"], "baseline": it.get("baseline", False)}
                      for it in ups_items],
            "disliked": [{"index": it["index"], "pose": it.get("pose_name"),
                          "overrides": it["overrides"], "baseline": it.get("baseline", False)}
                         for it in downs_items]}


@router.get("/varitest/{run_id}/report")
async def varitest_report(run_id: str, fmt: str = "json"):
    import json as _json
    man = _vt_load(run_id)
    if not man:
        raise HTTPException(status_code=404, detail="unknown varitest run")
    an = _vt_analyze(man)
    if fmt != "md":
        return {"analysis": an, "manifest": {k: man[k] for k in
                ("id", "created", "test_type", "character", "status", "axes",
                 "same_seed", "seed", "base_settings", "progress") if k in man}}
    L = [f"# Settings Variation Test report — {man['id']}", "",
         f"- character: **{man.get('character')}**",
         f"- test type: **{man.get('test_type')}**",
         f"- created: {man.get('created')}  ·  status: {man.get('status')}",
         f"- shared seed: {man.get('seed') if man.get('same_seed') else 'varied per item'}",
         f"- rated: {an['rated']}/{an['total']} renders", "",
         "## Suggestions", ""]
    if an["suggestions"]:
        for sg in an["suggestions"]:
            L.append(f"- **{sg['setting']}** → use `{sg['use']}`, avoid `{sg['avoid']}` ({sg['confidence']})")
    else:
        L.append("_Not enough rated data yet — rate at least 2 images per value on the axes you care about._")
    L += ["", "## Per-axis results", ""]
    for key, rows in an["axis_tables"].items():
        L += [f"### {key}", "", "| value | rated | 👍 | 👎 | score |", "|---|---|---|---|---|"]
        for r in rows:
            L.append(f"| {r['value']} | {r['rated']} | {r['ups']} | {r['downs']} | "
                     f"{r['score'] if r['score'] is not None else '—'} |")
        L.append("")
    L += ["## 👍 Liked renders", ""]
    for it in an["liked"]:
        L.append(f"- #{it['index']}{' · ' + it['pose'] if it.get('pose') else ''}: "
                 f"`{_json.dumps(it['overrides']) if it['overrides'] else 'BASELINE (current settings)'}`")
    L += ["", "## 👎 Disliked renders", ""]
    for it in an["disliked"]:
        L.append(f"- #{it['index']}{' · ' + it['pose'] if it.get('pose') else ''}: "
                 f"`{_json.dumps(it['overrides']) if it['overrides'] else 'BASELINE (current settings)'}`")
    L += ["", "## Base settings snapshot (what every variation started from)", "",
          "```json", _json.dumps(man.get("base_settings") or {}, indent=1), "```", ""]
    from fastapi.responses import PlainTextResponse
    return PlainTextResponse("\n".join(L), media_type="text/markdown")


# --------------------------------------------------------------------------- #
# v1.173 -- Tier-1 3D character body: Hunyuan3D mesh + UniRig auto-rig
# (once per character; results filed under <project_dir>/mesh3d/<char_id>/ and
# recorded in manifest.vnccs.mesh3d).  See docs/CHARACTER_3D_PLAN.md.
# --------------------------------------------------------------------------- #

_MESH3D_RUNS: dict = {}     # character_name -> {"status","phase","error","started","host"}


def _mesh3d_dir(char_id: str):
    from pathlib import Path as _Path
    from backend.config import settings as _cfg
    return _Path(_cfg.project_dir) / "mesh3d" / str(char_id)


class Mesh3dGenerateIn(BaseModel):
    character_name: str
    template: Optional[str] = "mixamo"        # 'mixamo' (humanoid) | 'articulationxl' (creature)
    use_views: Optional[bool] = True          # feed left/back/right views when the base set has them
    seed: Optional[int] = None
    host: Optional[str] = None
    reuse_mesh: Optional[bool] = False        # v1.173.1: re-rig the stored GLB (skip mesh gen)


@router.post("/mesh3d/generate")
async def mesh3d_generate(body: Mesh3dGenerateIn, request: Request,
                          session: AsyncSession = Depends(get_session)):
    """Generate the character-shaped 3D mannequin: active base render (+ extra
    views) -> Hunyuan3D shape-only GLB -> UniRig auto-rig -> rigged FBX.
    Runs in the background; poll GET /mesh3d/status/{character}."""
    import uuid as _uuid
    from uuid import UUID as _UUID
    from pathlib import Path as _Path
    from sqlmodel import select as _select
    from backend.config import settings as _cfg
    from backend.database.models import Asset, StudioCharacter
    from backend.services.character_studio.vnccs_native import char_mesh

    host = await _need_host(request, session)
    st = await _settings(session)
    saved = (st.studio_vnccs_settings if st else None) or {}
    name = body.character_name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="character name required")
    if (_MESH3D_RUNS.get(name) or {}).get("status") == "running":
        raise HTTPException(status_code=409, detail="A 3D-body generation is already running for this character.")
    char = (await session.execute(
        _select(StudioCharacter).where(StudioCharacter.name == name))).scalars().first()
    if char is None:
        raise HTTPException(status_code=404, detail=f"character {name!r} not found")
    v = (char.manifest or {}).get("vnccs") or {}
    active = v.get("active_base")
    _versions = [b for b in (v.get("base_versions") or []) if isinstance(b, dict)]
    # v1.186: the ACTIVE base is now the single main reference (sets no longer
    # hijack it), so for 3D input prefer the best MULTI-VIEW set: honor the active
    # if it's already a set, else a 🧊 Mesh-ready set, else any 4-view set, else
    # the active/latest. So generating a mesh set no longer needs to be "active"
    # to feed the 3D body -- it's picked up automatically.
    _act = next((b for b in _versions if b.get("id") == active), None)

    def _base_mode_of(b):
        return str((b.get("gen_meta") or {}).get("base_mode") or "").lower()

    def _is_set(b):
        return len(b.get("views") or []) > 1 or _base_mode_of(b) in ("mesh", "set")
    if _act is not None and _is_set(_act):
        bv = _act
    else:
        _mesh = [b for b in _versions if _base_mode_of(b) == "mesh"]
        _sets = [b for b in _versions if len(b.get("views") or []) > 1]
        bv = (_mesh[-1] if _mesh else (_sets[-1] if _sets else
              (_act or (_versions[-1] if _versions else None))))
    if not bv or not bv.get("asset_id"):
        raise HTTPException(status_code=409, detail="No base version -- generate a base preview first.")

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

    # collect views: primary = front; extra views when the base was a 4-view set
    views: dict = {}
    front = await _abytes(bv.get("asset_id"))
    if not front:
        raise HTTPException(status_code=409, detail="Base image is not readable.")
    views["front"] = front
    if body.use_views is not False:
        for vw in (bv.get("views") or []):
            vl = str(vw.get("view") or "").lower()
            if vl in ("left", "back", "right") and vw.get("asset_id"):
                data = await _abytes(vw.get("asset_id"))
                if data:
                    views[vl] = data

    try:
        oi = await asyncio.to_thread(_object_info, host)
        models = char_mesh.resolve_mesh3d_models(oi, saved)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except VNCCSError as e:
        raise HTTPException(status_code=502, detail=str(e))
    template = ("articulationxl" if str(body.template or "").lower().startswith("art")
                else "mixamo")
    seed = int(body.seed) if body.seed else 1
    char_id = str(char.id)
    safe = "".join(ch for ch in name if ch.isalnum())[:24] or "char"

    # v1.173.1: know up-front whether this worker can rig; v1.174: when it
    # can't, rigging runs LOCALLY via Make-It-Animatable (Mixamo-native FBX,
    # CPU-capable) -- no worker custom nodes required.
    from backend.services.character_studio.vnccs_native import mia_rig
    rig_classes = char_mesh.find_unirig_classes(oi)
    rig_hint = None
    if rig_classes is None:
        rig_hint = ("worker has no UniRig nodes -- rigging locally via "
                    "Make-It-Animatable" +
                    ("" if mia_rig.is_ready() else
                     " (first run: one-time env setup + ~2.2GB model download)"))
        logger.info("mesh3d[%s]: %s", name, rig_hint)

    # v1.173.1: re-rig a stored mesh without regenerating it
    reuse_glb: Optional[bytes] = None
    if body.reuse_mesh:
        mp = _mesh3d_dir(char_id) / "character.glb"
        if not mp.exists():
            raise HTTPException(status_code=409,
                                detail="No stored 3D mesh to re-rig -- generate the 3D body first.")
        reuse_glb = mp.read_bytes()

    _MESH3D_RUNS[name] = {"status": "running",
                          "phase": "rig" if reuse_glb is not None else "mesh",
                          "error": None, "started": True, "host": host,
                          "template": template}
    logger.info("mesh3d[%s]: starting (ckpt=%s, views=%s, template=%s, reuse=%s, rig=%s)",
                name, models["checkpoint"], sorted(views), template,
                bool(reuse_glb), "yes" if rig_classes else "NO")

    def _wait_history(client, pid: str, timeout: int) -> dict:
        import time as _t
        deadline = _t.time() + timeout
        while _t.time() < deadline:
            try:
                hist = client.get_history(pid, timeout=60)
            except Exception:  # noqa: BLE001
                hist = None
            rec = (hist or {}).get(pid) if isinstance(hist, dict) else None
            if rec and (rec.get("outputs") or (rec.get("status") or {}).get("completed")):
                return rec
            _t.sleep(3)
        raise VNCCSError("mesh3d render timed out")

    def _run_sync():
        client = _client(host, timeout=300)
        token = _uuid.uuid4().hex[:8]
        if reuse_glb is not None:
            # v1.173.1: skip mesh gen -- rig the stored GLB
            glb_bytes = reuse_glb
        else:
            # 1) upload views + mesh graph
            vf = {}
            for vl, data in views.items():
                fn = f"rbmn_mesh3d_{safe}_{token}_{vl}.png"
                up = client.upload_image(fn, data, "", True, 120)
                vf[vl] = up.get("name", fn)
            graph, _t1 = char_mesh.build_hunyuan3d_graph(
                view_files=vf, models=models, seed=seed,
                filename_prefix=f"rbmn_mesh3d/{safe}_{token}")
            res = client.submit_prompt(graph, timeout=120)
            rec = _wait_history(client, res.get("prompt_id"), 1800)
            glbs = char_mesh.harvest_output_files(rec, (".glb",))
            if not glbs:
                raise VNCCSError("Hunyuan3D produced no GLB (check the worker console)")
            g = glbs[0]
            glb_bytes = client.view_image(g.get("filename", ""), g.get("subfolder", "") or "",
                                          g.get("type", "output") or "output", 300)
        _MESH3D_RUNS[name]["phase"] = "rig"
        # 2) rig -- v1.173.1: rigging failures must NOT lose the mesh we just
        #    made; catch everything and report it alongside the saved GLB.
        #    v1.174: worker UniRig when its nodes exist, otherwise (or on
        #    worker-rig failure) LOCAL Make-It-Animatable -- Mixamo-native
        #    FBX rigged on this machine, CPU-capable, zero worker installs.
        fbx_bytes = None
        fbx_name = None
        rig_engine = None
        rig_err = None

        def _detail(msg):
            run = _MESH3D_RUNS.get(name)
            if isinstance(run, dict):
                run["detail"] = str(msg)[:160]

        if rig_classes is not None:
            try:
                _detail("rigging on worker (UniRig)")
                mesh_name = f"rbmn_mesh3d_{safe}_{token}.glb"
                up = client.upload_image(mesh_name, glb_bytes, "", True, 120)
                mesh_filename = up.get("name", mesh_name)
                fbx_base = f"rbmn_rig_{safe}_{token}"
                rig_graph, _t2 = char_mesh.build_unirig_graph(
                    oi=oi, mesh_filename=mesh_filename, template=template,
                    fbx_name=fbx_base)
                res2 = client.submit_prompt(rig_graph, timeout=120)
                rec2 = _wait_history(client, res2.get("prompt_id"), 2400)
                fbxs = char_mesh.harvest_output_files(rec2, (".fbx",))
                if fbxs:
                    f = fbxs[0]
                    fbx_bytes = client.view_image(f.get("filename", ""), f.get("subfolder", "") or "",
                                                  f.get("type", "output") or "output", 300)
                    fbx_name = f.get("filename")
                else:
                    # the FBX lands in the output ROOT as <fbx_name>_<template>.fbx
                    # -- fetch by the known name when history parsing yields nothing
                    for cand in (f"{fbx_base}_{template}.fbx", f"{fbx_base}_unknown.fbx"):
                        try:
                            fbx_bytes = client.view_image(cand, "", "output", 300)
                            fbx_name = cand
                            break
                        except Exception:  # noqa: BLE001
                            continue
                if fbx_bytes:
                    rig_engine = "unirig"
                else:
                    rig_err = "worker UniRig returned no FBX"
            except Exception as e:  # noqa: BLE001
                logger.exception(f"mesh3d[{name}] worker rig failed (falling back to local MIA)")
                rig_err = f"worker UniRig failed: {str(e)[:200]}"

        if fbx_bytes is None:
            try:
                _detail("rigging locally (Make-It-Animatable)")
                fbx_bytes = mia_rig.run_rig(
                    glb_bytes, out_dir=_mesh3d_dir(char_id), name=safe,
                    cb=_detail)
                fbx_name = f"{safe}_mia.fbx"
                rig_engine = "mia"
                rig_err = None
            except Exception as e:  # noqa: BLE001
                logger.exception(f"mesh3d[{name}] local MIA rig failed (mesh kept)")
                mia_err = str(e)[:300]
                rig_err = (rig_err + " | " + mia_err) if rig_err else mia_err
        return glb_bytes, fbx_bytes, fbx_name, rig_engine, rig_err

    async def _task():
        from datetime import datetime as _dt
        from backend.database.database import async_session as _asession
        try:
            glb_bytes, fbx_bytes, fbx_name, rig_engine, rig_err = await asyncio.to_thread(_run_sync)
            d = _mesh3d_dir(char_id)
            d.mkdir(parents=True, exist_ok=True)
            (d / "character.glb").write_bytes(glb_bytes)
            if fbx_bytes:
                (d / "rigged.fbx").write_bytes(fbx_bytes)
            meta = {"template": template, "checkpoint": models["checkpoint"],
                    "views": sorted(views), "seed": seed,
                    "created": _dt.utcnow().isoformat() + "Z",
                    "character_name": name,      # v1.175: pose_clay looks rigs up by name
                    "rigged": bool(fbx_bytes), "fbx_source": fbx_name,
                    "rig_engine": rig_engine, "rig_error": rig_err}
            import json as _json
            (d / "meta.json").write_text(_json.dumps(meta, indent=1), encoding="utf-8")
            async with _asession() as s2:
                c2 = (await s2.execute(
                    _select(StudioCharacter).where(StudioCharacter.name == name))).scalars().first()
                if c2 is not None:
                    man = dict(c2.manifest or {})
                    vn = dict(man.get("vnccs") or {})
                    vn["mesh3d"] = {**meta, "dir": f"mesh3d/{char_id}"}
                    man["vnccs"] = vn
                    c2.manifest = man
                    await s2.commit()
            _MESH3D_RUNS[name] = {"status": "done" if fbx_bytes else "mesh_only",
                                  "phase": "done", "error": None if fbx_bytes else
                                  ("3D mesh saved. Rigging failed: " + rig_err if rig_err
                                   else "mesh saved, but UniRig returned no FBX -- check the worker console"),
                                  "host": host, "template": template}
            logger.info("mesh3d[%s]: %s (glb %d bytes, fbx %s)",
                        name, "done" if fbx_bytes else "mesh_only",
                        len(glb_bytes), len(fbx_bytes) if fbx_bytes else "none")
        except Exception as e:  # noqa: BLE001
            logger.exception(f"mesh3d[{name}] failed")
            _MESH3D_RUNS[name] = {"status": "error", "phase": "error",
                                  "error": str(e)[:400], "host": host, "template": template}

    asyncio.create_task(_task())
    return {"ok": True, "character": name, "template": template,
            "views": sorted(views), "checkpoint": models["checkpoint"],
            "reuse_mesh": bool(reuse_glb is not None),
            "rig_available": bool(rig_classes), "rig_hint": rig_hint}


@router.get("/mesh3d/status/{character_name}")
async def mesh3d_status(character_name: str,
                        session: AsyncSession = Depends(get_session)):
    from sqlmodel import select as _select
    from backend.database.models import StudioCharacter
    name = character_name.strip()
    live = _MESH3D_RUNS.get(name)
    char = (await session.execute(
        _select(StudioCharacter).where(StudioCharacter.name == name))).scalars().first()
    mesh3d = ((char.manifest or {}).get("vnccs") or {}).get("mesh3d") if char else None
    return {"character": name, "run": live, "mesh3d": mesh3d}


@router.get("/mesh3d/file/{character_name}/{kind}")
async def mesh3d_file(character_name: str, kind: str,
                      session: AsyncSession = Depends(get_session)):
    from fastapi.responses import FileResponse
    from sqlmodel import select as _select
    from backend.database.models import StudioCharacter
    name = character_name.strip()
    char = (await session.execute(
        _select(StudioCharacter).where(StudioCharacter.name == name))).scalars().first()
    if char is None:
        raise HTTPException(status_code=404, detail="unknown character")
    fn = {"glb": "character.glb", "fbx": "rigged.fbx"}.get(kind)
    if not fn:
        raise HTTPException(status_code=400, detail="kind must be glb|fbx")
    p = _mesh3d_dir(str(char.id)) / fn
    if not p.exists():
        raise HTTPException(status_code=404, detail=f"no {kind} for this character yet")
    return FileResponse(str(p), media_type="application/octet-stream", filename=fn)

