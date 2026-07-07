"""Character Studio API — stories, characters, shot generation, LoRA datasets.

Design: docs/CHARACTER_STUDIO.md.  Generation runs through the normal Job
queue inside a hidden system project (settings.studio_system=true, filtered
from the project list) with one scene per character, so all existing
dispatch/lightbox/versioning machinery applies.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlmodel import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings as cfg
from backend.database.database import get_session
from backend.database.models import (
    AppSettings, Asset, AssetType, Job, JobStatus, JobType, Project, ProjectMode,
    Scene, Story, StudioCharacter, StudioDataset,
)
from backend.services.character_studio.service import (
    build_base_prompt, build_caption_prompt, default_shot_plan,
    export_dataset, load_catalog, studio_root,
    STUDIO_STYLES, DEFAULT_STYLE, style_label, style_key_of,
)
from backend.services.character_studio import pose_renderer as _pose_renderer
from backend.services.character_studio import faces as _faces
from backend.services.character_studio import cutout as _cutout
from backend.services.character_studio.engines import (
    EngineUnavailableError, resolve_engine, pose_edit_params, costume_params,
    emotion_params, cutout_params, upscale_params,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/character-studio", tags=["character_studio"])

_STUDIO_PROJECT_NAME = "_Character Studio (system)"


# ── helpers ───────────────────────────────────────────────────────────────
async def _app_settings(session: AsyncSession) -> Optional[AppSettings]:
    return (await session.execute(select(AppSettings).limit(1))).scalars().first()


async def _ensure_studio_project(session: AsyncSession) -> Project:
    rows = (await session.execute(select(Project))).scalars().all()
    for p in rows:
        if (p.settings or {}).get("studio_system"):
            return p
    proj = Project(
        name=_STUDIO_PROJECT_NAME,
        mode=ProjectMode.MUSIC_VIDEO,
        settings={"studio_system": True, "characters": []},
    )
    session.add(proj)
    await session.commit()
    await session.refresh(proj)
    logger.info(f"Character Studio: created hidden system project {proj.id}")
    return proj


async def _ensure_scene(session: AsyncSession, proj: Project, char: StudioCharacter) -> Scene:
    if char.scene_id:
        sc = await session.get(Scene, char.scene_id)
        if sc:
            return sc
    count = len((await session.execute(
        select(Scene.id).where(Scene.project_id == proj.id))).all())
    sc = Scene(
        project_id=proj.id, order_index=count,
        name=f"[studio] {char.name}"[:80],
        start_time=float(count * 5), end_time=float(count * 5 + 5),
        prompt="", parameters={"studio_character_id": str(char.id)},
    )
    session.add(sc)
    await session.commit()
    await session.refresh(sc)
    char.scene_id = sc.id
    char.updated_at = datetime.utcnow()
    session.add(char)
    await session.commit()
    return sc


def _project_dir(proj: Project) -> Path:
    return Path(cfg.project_dir) / str(proj.id)


def _resolve_rel(proj: Project, rel: str) -> Optional[Path]:
    if not rel:
        return None
    base = Path(cfg.project_dir)
    pid = str(proj.id)
    cands = [base / rel, base / pid / rel]
    for c in cands:
        try:
            cr = c.resolve()
            if cr.exists() and str(cr).startswith(str(base.resolve())):
                return cr
        except Exception:
            continue
    return None


async def _find_asset_by_rel(session: AsyncSession, project_id: UUID, rel: str) -> Optional[Asset]:
    """Forgiving asset lookup: exact rel_path, then basename suffix match (newest)."""
    if not rel:
        return None
    rows = (await session.execute(
        select(Asset).where(Asset.project_id == project_id)
        .order_by(Asset.created_at.desc()))).scalars().all()
    for a in rows:
        if a.rel_path == rel:
            return a
    tail = Path(rel).name
    for a in rows:
        if a.rel_path and Path(a.rel_path).name == tail:
            return a
    return None


def _res_from_settings(s: Optional[AppSettings]) -> tuple[int, int]:
    w = getattr(s, "image_resolution_width", None) or 1024
    h = getattr(s, "image_resolution_height", None) or 1024
    return int(w), int(h)


# ── P2 helpers: asset registration for locally-produced images ─────────────
def _register_asset(proj: Project, src_path: Path, subdir: str,
                     asset_type: AssetType = AssetType.REFERENCE) -> Optional[Path]:
    """Copy a locally-produced image (pose render / face-masked RGBA / face
    crop / cutout) into the studio project's assets dir and return the
    project-relative path to register as an Asset row.  Mirrors the on-disk
    convention used by ``backend/api/assets.py`` uploads (project_dir/<id>/
    assets/<subdir>/...), but files here are produced in-process rather than
    uploaded, so we write directly instead of streaming an UploadFile.
    """
    if not src_path or not src_path.exists():
        return None
    dest_dir = _project_dir(proj) / "assets" / subdir
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / src_path.name
    if dest != src_path:
        import shutil as _sh
        _sh.copy2(src_path, dest)
    return dest.relative_to(Path(cfg.project_dir) / str(proj.id))


async def _create_asset_row(session: AsyncSession, proj: Project, rel_path: Path,
                             asset_type: AssetType = AssetType.REFERENCE,
                             meta: Optional[dict] = None) -> Asset:
    from backend.utils.file_utils import sha256_file
    abs_path = Path(cfg.project_dir) / str(proj.id) / rel_path
    try:
        sha = sha256_file(abs_path)
    except Exception:
        sha = ""
    asset = Asset(
        project_id=proj.id, filename=abs_path.name, rel_path=str(rel_path),
        asset_type=asset_type, sha256=sha or "",
        file_size=abs_path.stat().st_size if abs_path.exists() else 0,
        meta=meta or {},
    )
    session.add(asset)
    await session.commit()
    await session.refresh(asset)
    return asset


def _job_terminal_status(j: Job) -> Optional[str]:
    st = str(getattr(j.status, "value", j.status)).lower()
    if st in ("done", "completed"):
        return "done"
    if st in ("failed", "cancelled"):
        return "failed"
    return None


def _job_result_asset_id(j: Job) -> Optional[str]:
    res = j.result or {}
    ids = res.get("asset_ids") or res.get("created_asset_ids") or []
    return str(ids[0]) if ids else None


async def _poll_job(session_factory, job_id: UUID, timeout_s: float = 600.0,
                     interval_s: float = 5.0) -> tuple[str, Optional[str], Optional[str]]:
    """Self-contained job poller for the P2 orchestrator (generate-all /
    per-stage synchronous waits).  No shared batch-pipeline poller exists
    elsewhere in the codebase to reuse (the batch pipeline's loop is
    tightly coupled to its own scene/batch state), so this is intentionally
    small and self-contained to this module.

    Returns (status, asset_id, error) where status is "done" | "failed" | "timeout".
    """
    elapsed = 0.0
    while elapsed < timeout_s:
        async with session_factory() as session:
            j = await session.get(Job, job_id)
            if not j:
                return "failed", None, "job disappeared"
            term = _job_terminal_status(j)
            if term == "done":
                return "done", _job_result_asset_id(j), None
            if term == "failed":
                return "failed", None, str(j.error or "job failed")
        await asyncio.sleep(interval_s)
        elapsed += interval_s
    return "timeout", None, f"timed out after {timeout_s}s"


# ── schemas ───────────────────────────────────────────────────────────────
class StoryIn(BaseModel):
    name: str
    description: str = ""
    default_style: str = ""      # default art style pre-filled onto new characters


class CharacterIn(BaseModel):
    name: str
    story_id: Optional[UUID] = None
    kind: str = "character"          # character | item
    trigger_word: str = ""
    class_word: str = ""
    description: str = ""
    character_info: dict = Field(default_factory=dict)


class GenerateBaseIn(BaseModel):
    extra: str = ""                   # appended to the constructed prompt
    prompt_override: str = ""         # replaces the constructed prompt entirely
    model: str = ""                   # optional first-pass model override for this
                                      # render (z_image_turbo | krea2_turbo |
                                      # flux2_klein_dev_9b); "" = use Settings default


class GenerateShotsIn(BaseModel):
    shot_ids: Optional[list[str]] = None   # None = all enabled shots without an image
    regenerate: bool = False


class DatasetIn(BaseModel):
    name: str = ""
    target: str = "both"              # kohya | ai_toolkit | both
    trigger_word: str = ""
    class_word: str = ""
    repeats: int = 10
    quality_family: str = "illustrious"   # illustrious | noobai | pony | none
    include: Optional[list[str]] = None    # shot ids (+ "base"); None = base + all completed


class CaptionPatch(BaseModel):
    image: str
    style: str                        # natural | tags
    text: str


class PushIn(BaseModel):
    project_id: UUID
    max_extra_images: int = 3


# ── P2 schemas ────────────────────────────────────────────────────────────
class CostumeIn(BaseModel):
    name: str
    fields: dict = Field(default_factory=dict)   # {top, bottom, head, face, shoes}
    prompt: str = ""


class CostumeGenerateIn(BaseModel):
    engine: str = "auto"   # auto | qwen | klein


class PoseGenerateIn(BaseModel):
    preset_ids: list[str] = Field(default_factory=list)
    engine: str = "auto"


class EmotionGenerateIn(BaseModel):
    emotions: list[str] = Field(default_factory=list)   # safe_name keys from emotions.json
    costume_id: Optional[str] = None
    source: str = "base"   # "base" | a shot id
    engine: str = "auto"


def _worker_online(comfy_dispatcher, cap: str) -> bool:
    """Non-logging availability probe (audit H3: never 500, never spam the
    console).  Uses ``has_capability`` so optional-engine checks (vnccs /
    seedvr2 / impact) don't emit a dispatcher WARNING every poll when those
    engines are simply absent from the worker pool."""
    try:
        return comfy_dispatcher is not None and comfy_dispatcher.has_capability(cap)
    except Exception:
        return False


def _resolve_upscale_mode(comfy_dispatcher, requested: str = "auto") -> tuple[str, bool]:
    """Resolve gan-vs-seedvr2 upscale. Returns (mode, worker_online).

    auto → seedvr2 when a seedvr2-capable worker is online (premium quality,
    VNCCS-style), else gan on any upscale-capable worker.
    """
    def _online(cap: str) -> bool:
        try:
            return comfy_dispatcher is not None and (
                comfy_dispatcher.select_worker({cap}, set(), exclude_runpod=True) is not None)
        except Exception:
            return False
    req = (requested or "auto").lower()
    if req == "seedvr2":
        return "seedvr2", _online("seedvr2")
    if req == "gan":
        return "gan", _online("upscale")
    return ("seedvr2", True) if _online("seedvr2") else ("gan", _online("upscale"))


class ProcessIn(BaseModel):
    image_refs: list[str] = Field(default_factory=list)   # manifest keys: "base" | shot id | costume/pose/emotion sprite keys
    steps: dict = Field(default_factory=lambda: {"cutout": True, "upscale": False})
    engine: str = "auto"
    upscale_mode: str = "auto"   # auto | seedvr2 | gan


class GenerateAllIn(BaseModel):
    engine: str = "auto"
    include: dict = Field(default_factory=lambda: {
        "shots": True, "costume_ids": [], "emotions": [], "cutout": False, "upscale": False,
    })


class WizardCharacterIn(BaseModel):
    description: str
    style: str = ""              # art style so the tag sheet matches the target look


@router.get("/styles")
async def list_styles():
    """Canonical art-style presets for the Studio UI dropdowns. Custom
    free-text values are also accepted everywhere styles are used."""
    return {
        "default": DEFAULT_STYLE,
        "styles": [{"value": k, "label": v.get("label", k)} for k, v in STUDIO_STYLES.items()],
    }


# ── stories ───────────────────────────────────────────────────────────────
@router.get("/stories")
async def list_stories(session: AsyncSession = Depends(get_session)):
    rows = (await session.execute(select(Story).order_by(Story.created_at))).scalars().all()
    chars = (await session.execute(select(StudioCharacter))).scalars().all()
    counts: dict = {}
    for c in chars:
        if c.story_id:
            counts[str(c.story_id)] = counts.get(str(c.story_id), 0) + 1
    return [{"id": str(s.id), "name": s.name, "description": s.description,
             "default_style": getattr(s, "default_style", "") or DEFAULT_STYLE,
             "character_count": counts.get(str(s.id), 0)} for s in rows]


@router.post("/stories")
async def create_story(body: StoryIn, session: AsyncSession = Depends(get_session)):
    s = Story(name=body.name.strip(), description=body.description,
              default_style=(body.default_style or DEFAULT_STYLE).strip())
    session.add(s)
    await session.commit()
    await session.refresh(s)
    return {"id": str(s.id), "name": s.name, "description": s.description,
            "default_style": s.default_style}


@router.patch("/stories/{story_id}")
async def update_story(story_id: UUID, body: StoryIn, session: AsyncSession = Depends(get_session)):
    s = await session.get(Story, story_id)
    if not s:
        raise HTTPException(404, "Story not found")
    s.name, s.description = body.name.strip(), body.description
    if body.default_style:
        s.default_style = body.default_style.strip()
    session.add(s)
    await session.commit()
    return {"ok": True}


@router.delete("/stories/{story_id}")
async def delete_story(story_id: UUID, session: AsyncSession = Depends(get_session)):
    s = await session.get(Story, story_id)
    if not s:
        raise HTTPException(404, "Story not found")
    for c in (await session.execute(
            select(StudioCharacter).where(StudioCharacter.story_id == story_id))).scalars().all():
        c.story_id = None
        session.add(c)
    await session.delete(s)
    await session.commit()
    return {"ok": True}


# ── catalogs ──────────────────────────────────────────────────────────────
@router.get("/catalogs")
async def get_catalogs():
    _outfits = load_catalog("outfits") or []
    return {"tags": load_catalog("character_tags"),
            "emotions": load_catalog("emotions"),
            # 629 curated outfit aesthetics from the VNCCS catalog — the UI
            # offers these as suggestions in the costume builder.
            "outfits": [{"name": o.get("aesthetic", ""), "content": o.get("content", "")}
                        for o in _outfits if isinstance(o, dict)][:800]}


# ── characters ────────────────────────────────────────────────────────────
def _char_out(c: StudioCharacter, studio_project_id: Optional[str] = None) -> dict:
    return {
        "id": str(c.id), "name": c.name, "kind": c.kind,
        "story_id": str(c.story_id) if c.story_id else None,
        "trigger_word": c.trigger_word, "class_word": c.class_word,
        "description": c.description, "character_info": c.character_info or {},
        "manifest": c.manifest or {}, "scene_id": str(c.scene_id) if c.scene_id else None,
        "studio_project_id": studio_project_id,
        "created_at": c.created_at.isoformat() if c.created_at else None,
    }


@router.get("/characters")
async def list_characters(story_id: Optional[UUID] = None,
                          session: AsyncSession = Depends(get_session)):
    stmt = select(StudioCharacter).order_by(StudioCharacter.created_at.desc())
    if story_id:
        stmt = stmt.where(StudioCharacter.story_id == story_id)
    rows = (await session.execute(stmt)).scalars().all()
    proj = await _ensure_studio_project(session)
    return [_char_out(c, str(proj.id)) for c in rows]


@router.post("/characters")
async def create_character(body: CharacterIn, session: AsyncSession = Depends(get_session)):
    name = body.name.strip()
    if not name:
        raise HTTPException(400, "Character name required")
    trigger = (body.trigger_word or "").strip() or (
        "".join(ch for ch in name.lower() if ch.isalnum())[:12] or "char") 
    info = dict(body.character_info or {})
    if not info.get("style"):
        story_style = ""
        if body.story_id:
            _st = await session.get(Story, body.story_id)
            story_style = (getattr(_st, "default_style", "") or "") if _st else ""
        info["style"] = story_style or DEFAULT_STYLE
    c = StudioCharacter(
        name=name, story_id=body.story_id, kind=body.kind,
        trigger_word=trigger,
        class_word=(body.class_word or ("object" if body.kind == "item" else "person")).strip(),
        description=body.description, character_info=info,
        manifest={"shot_plan": default_shot_plan(info, body.kind), "shots": {}},
    )
    session.add(c)
    await session.commit()
    await session.refresh(c)
    proj = await _ensure_studio_project(session)
    await _ensure_scene(session, proj, c)
    return _char_out(c, str(proj.id))


@router.get("/characters/{char_id}")
async def get_character(char_id: UUID, session: AsyncSession = Depends(get_session)):
    c = await session.get(StudioCharacter, char_id)
    if not c:
        raise HTTPException(404, "Character not found")
    proj = await _ensure_studio_project(session)
    return _char_out(c, str(proj.id))


@router.patch("/characters/{char_id}")
async def update_character(char_id: UUID, body: dict,
                           session: AsyncSession = Depends(get_session)):
    c = await session.get(StudioCharacter, char_id)
    if not c:
        raise HTTPException(404, "Character not found")
    for k in ("name", "kind", "trigger_word", "class_word", "description"):
        if k in body and isinstance(body[k], str):
            setattr(c, k, body[k])
    if "story_id" in body:
        c.story_id = UUID(body["story_id"]) if body["story_id"] else None
    if isinstance(body.get("character_info"), dict):
        c.character_info = body["character_info"]
    if isinstance(body.get("manifest"), dict):
        c.manifest = body["manifest"]
    c.updated_at = datetime.utcnow()
    session.add(c)
    await session.commit()
    await session.refresh(c)
    proj = await _ensure_studio_project(session)
    # Return the full updated character (the frontend feeds this straight back
    # into its character state; returning {"ok": true} wiped every field and
    # then crashed the next save on `undefined.trim()`).
    return _char_out(c, str(proj.id))


@router.delete("/characters/{char_id}")
async def delete_character(char_id: UUID, session: AsyncSession = Depends(get_session)):
    c = await session.get(StudioCharacter, char_id)
    if not c:
        raise HTTPException(404, "Character not found")
    for d in (await session.execute(
            select(StudioDataset).where(StudioDataset.character_id == char_id))).scalars().all():
        await session.delete(d)
    await session.delete(c)
    await session.commit()
    return {"ok": True}


# ── generation ────────────────────────────────────────────────────────────
@router.post("/characters/{char_id}/generate-base")
async def generate_base(char_id: UUID, body: GenerateBaseIn, request: Request,
                        session: AsyncSession = Depends(get_session)):
    c = await session.get(StudioCharacter, char_id)
    if not c:
        raise HTTPException(404, "Character not found")
    proj = await _ensure_studio_project(session)
    scene = await _ensure_scene(session, proj, c)
    s = await _app_settings(session)
    w, h = _res_from_settings(s)
    prompt = body.prompt_override.strip() or build_base_prompt(
        c.character_info or {}, c.kind, body.extra)
    scene.prompt = prompt
    session.add(scene)
    job = Job(project_id=proj.id, scene_id=scene.id, job_type=JobType.IMAGE,
              status=JobStatus.PENDING, priority=0,
              parameters={"workflow_type": "klein_t2i", "prompt": prompt,
                          "width": w, "height": h, "reference_asset_ids": [],
                          "frame_type": "first", "auto_save_preview": True,
                          "studio_character_id": str(c.id), "studio_shot_id": "base",
                          **({"single_image_generator_override": body.model.strip()}
                             if body.model.strip() else {})})
    session.add(job)
    await session.commit()
    await session.refresh(job)
    request.app.state.job_queue.notify()
    return {"job_id": str(job.id), "prompt": prompt}


@router.post("/characters/{char_id}/reset-shot-plan")
async def reset_shot_plan(char_id: UUID, session: AsyncSession = Depends(get_session)):
    c = await session.get(StudioCharacter, char_id)
    if not c:
        raise HTTPException(404, "Character not found")
    m = dict(c.manifest or {})
    m["shot_plan"] = default_shot_plan(c.character_info or {}, c.kind)
    c.manifest = m
    session.add(c)
    await session.commit()
    return {"shot_plan": m["shot_plan"]}


class SetBaseIn(BaseModel):
    asset_id: UUID


@router.post("/characters/{char_id}/set-base")
async def set_base(char_id: UUID, body: SetBaseIn,
                   session: AsyncSession = Depends(get_session)):
    """Use an uploaded (or existing) image AS the base render directly, NVCCS
    import-mode style.  Upload the file first via
    POST /api/projects/{studio_project_id}/assets/upload, then pass its
    asset_id here; we point the studio scene's chosen_image_path at it so the
    whole downstream pipeline (shots/poses/costumes/emotions/process/dataset)
    edits from it exactly as if it had been rendered."""
    c = await session.get(StudioCharacter, char_id)
    if not c:
        raise HTTPException(404, "Character not found")
    proj = await _ensure_studio_project(session)
    scene = await _ensure_scene(session, proj, c)
    asset = await session.get(Asset, body.asset_id)
    if not asset:
        raise HTTPException(404, "Asset not found — upload the image first")
    sp = dict(scene.parameters or {})
    sp["chosen_image_path"] = asset.rel_path
    scene.parameters = sp
    session.add(scene)
    await session.commit()
    return {"ok": True, "asset_id": str(asset.id), "image_rel": asset.rel_path}


@router.post("/characters/{char_id}/generate-shots")
async def generate_shots(char_id: UUID, body: GenerateShotsIn, request: Request,
                         session: AsyncSession = Depends(get_session)):
    c = await session.get(StudioCharacter, char_id)
    if not c:
        raise HTTPException(404, "Character not found")
    proj = await _ensure_studio_project(session)
    scene = await _ensure_scene(session, proj, c)
    base_rel = (scene.parameters or {}).get("chosen_image_path")
    if not base_rel:
        raise HTTPException(400, "Generate the base render first — shots edit from it (Image 1)")
    base_asset = await _find_asset_by_rel(session, proj.id, base_rel)
    if not base_asset:
        raise HTTPException(400, "Base render asset not found — regenerate the base render")
    s = await _app_settings(session)
    w, h = _res_from_settings(s)
    m = dict(c.manifest or {})
    plan = m.get("shot_plan") or default_shot_plan(c.character_info or {}, c.kind)
    shots = dict(m.get("shots") or {})
    created = []
    for shot in plan:
        sid = shot.get("id")
        if not sid or not shot.get("enabled", True):
            continue
        if body.shot_ids is not None and sid not in body.shot_ids:
            continue
        if not body.regenerate and (shots.get(sid) or {}).get("image_rel"):
            continue
        job = Job(project_id=proj.id, scene_id=scene.id, job_type=JobType.IMAGE,
                  status=JobStatus.PENDING, priority=len(created),
                  parameters={"workflow_type": "klein_1ref",
                              "prompt": shot.get("instruction") or "",
                              "width": w, "height": h,
                              "reference_asset_ids": [str(base_asset.id)],
                              "frame_type": "first", "auto_save_preview": False,
                              "studio_character_id": str(c.id), "studio_shot_id": sid})
        session.add(job)
        await session.flush()
        shots[sid] = {"status": "pending", "job_id": str(job.id),
                      "image_rel": (shots.get(sid) or {}).get("image_rel") if not body.regenerate else None}
        created.append(sid)
    m["shots"] = shots
    c.manifest = m
    session.add(c)
    await session.commit()
    request.app.state.job_queue.notify()
    return {"created": created}


@router.get("/characters/{char_id}/status")
async def character_status(char_id: UUID, session: AsyncSession = Depends(get_session)):
    c = await session.get(StudioCharacter, char_id)
    if not c:
        raise HTTPException(404, "Character not found")
    proj = await _ensure_studio_project(session)
    scene = await session.get(Scene, c.scene_id) if c.scene_id else None
    base_rel = (scene.parameters or {}).get("chosen_image_path") if scene else None
    base_asset = await _find_asset_by_rel(session, proj.id, base_rel) if base_rel else None

    m = dict(c.manifest or {})
    shots = dict(m.get("shots") or {})
    pose_sets = dict(m.get("pose_sets") or {})
    costumes = dict(m.get("costumes") or {})
    emotions = dict(m.get("emotions") or {})
    processed = dict(m.get("processed") or {})
    jobs = (await session.execute(
        select(Job).where(Job.scene_id == c.scene_id)
        .order_by(Job.created_at.desc()).limit(500))).scalars().all() if c.scene_id else []
    changed = False

    def _new_status_for(j: Job) -> str:
        st = str(getattr(j.status, "value", j.status)).lower()
        return ("done" if st in ("done", "completed") else
                "failed" if st in ("failed", "cancelled") else
                "running" if st in ("running",) else "pending")

    def _result_asset_id(j: Job) -> Optional[str]:
        res = j.result or {}
        ids = res.get("asset_ids") or res.get("created_asset_ids") or []
        return str(ids[0]) if ids else None

    for j in jobs:
        jp = j.parameters or {}
        sid = jp.get("studio_shot_id")
        if not sid or sid == "base":
            continue
        new_status = _new_status_for(j)
        aid = _result_asset_id(j) if new_status == "done" else None

        if ":" not in sid:
            # Legacy shot-plan entry (angle/expression/framing shots)
            entry = dict(shots.get(sid) or {})
            if entry.get("job_id") and entry["job_id"] != str(j.id):
                continue
            if entry.get("status") != new_status:
                entry["status"] = new_status
                changed = True
            if new_status == "done" and not entry.get("image_rel") and aid:
                a = await session.get(Asset, UUID(aid))
                if a:
                    entry["image_rel"] = a.rel_path
                    entry["asset_id"] = str(a.id)
                    changed = True
            if new_status == "failed" and j.error and not entry.get("error"):
                entry["error"] = str(j.error)[:300]
                changed = True
            shots[sid] = entry
            continue

        kind, _, key = sid.partition(":")

        if kind == "pose":
            entry = dict(pose_sets.get(key) or {})
            if entry.get("job_id") and entry["job_id"] != str(j.id):
                continue
            if entry.get("status") != new_status:
                entry["status"] = new_status
                changed = True
            if new_status == "done" and not entry.get("image_rel") and aid:
                a = await session.get(Asset, UUID(aid))
                if a:
                    entry["image_rel"] = a.rel_path
                    entry["asset_id"] = str(a.id)
                    changed = True
            if new_status == "failed" and j.error and not entry.get("error"):
                entry["error"] = str(j.error)[:300]
                changed = True
            pose_sets[key] = entry

        elif kind == "costume":
            costume = costumes.get(key)
            if not costume:
                continue
            sprites = dict(costume.get("sprites") or {})
            entry = dict(sprites.get("base") or {})
            if entry.get("job_id") and entry["job_id"] != str(j.id):
                continue
            if entry.get("status") != new_status:
                entry["status"] = new_status
                changed = True
            if new_status == "done" and not entry.get("image_rel") and aid:
                a = await session.get(Asset, UUID(aid))
                if a:
                    entry["image_rel"] = a.rel_path
                    entry["asset_id"] = str(a.id)
                    changed = True
            if new_status == "failed" and j.error and not entry.get("error"):
                entry["error"] = str(j.error)[:300]
                changed = True
            sprites["base"] = entry
            costume["sprites"] = sprites
            costumes[key] = costume

        elif kind == "emotion":
            entry = dict(emotions.get(key) or {})
            if entry.get("job_id") and entry["job_id"] != str(j.id):
                continue
            if entry.get("status") != new_status:
                entry["status"] = new_status
                changed = True
            if new_status == "done" and not entry.get("image_rel") and aid:
                a = await session.get(Asset, UUID(aid))
                if a:
                    entry["image_rel"] = a.rel_path
                    entry["asset_id"] = str(a.id)
                    changed = True
                    # Side effect: crop the face out of the result for
                    # dataset/manifest use (mirrors the generate-all path).
                    if not entry.get("face_crop_rel"):
                        try:
                            a_abs = _resolve_rel(proj, a.rel_path)
                            if a_abs:
                                crop_dir = studio_root(Path(cfg.project_dir)) / str(c.id) / "emotion_faces"
                                crop_dir.mkdir(parents=True, exist_ok=True)
                                crop_path = crop_dir / f"{key}.png"
                                crop_bbox = _faces.crop_face(a_abs, crop_path)
                                if crop_bbox:
                                    crop_rel = _register_asset(proj, crop_path, "studio_emotion_faces")
                                    if crop_rel:
                                        entry["face_crop_rel"] = str(crop_rel)
                                        _crop_asset = await _create_asset_row(
                                            session, proj, str(crop_rel), AssetType.GENERATED_IMAGE,
                                            meta={"studio_character_id": str(c.id),
                                                  "emotion_face_crop": key})
                                        if _crop_asset:
                                            entry["face_crop_asset_id"] = str(_crop_asset.id)
                        except Exception as _crop_e:
                            logger.warning(f"Character Studio: face-crop side effect failed for {key}: {_crop_e}")
            if new_status == "failed" and j.error and not entry.get("error"):
                entry["error"] = str(j.error)[:300]
                changed = True
            emotions[key] = entry

        elif kind in ("cutout", "upscale"):
            entry = dict((processed.get(key) or {}).get(kind) or {})
            if entry.get("job_id") and entry["job_id"] != str(j.id):
                continue
            if entry.get("status") != new_status:
                entry["status"] = new_status
                changed = True
            if new_status == "done" and not entry.get("image_rel") and aid:
                a = await session.get(Asset, UUID(aid))
                if a:
                    entry["image_rel"] = a.rel_path
                    entry["asset_id"] = str(a.id)
                    changed = True
            if new_status == "failed" and j.error and not entry.get("error"):
                entry["error"] = str(j.error)[:300]
                changed = True
            proc_entry = dict(processed.get(key) or {})
            proc_entry[kind] = entry
            processed[key] = proc_entry

    if changed:
        m["shots"] = shots
        m["pose_sets"] = pose_sets
        m["costumes"] = costumes
        m["emotions"] = emotions
        m["processed"] = processed
        c.manifest = m
        session.add(c)
        await session.commit()
    # Reconcile the base render job's real status so the UI can distinguish
    # idle / running / failed (previously all three looked identical -> the
    # base preview spun "Rendering..." forever on failure).  The base job is
    # the newest job carrying studio_shot_id == "base" (jobs are ordered
    # created_at DESC, so the first match is the most recent attempt).
    base_job = next(
        (j for j in jobs if (j.parameters or {}).get("studio_shot_id") == "base"), None)
    if base_asset:
        base_status = "done"
        base_error = None
    elif base_job is not None:
        base_status = _new_status_for(base_job)
        base_error = (
            (str(base_job.error)[:300] if base_job.error else "Base render failed")
            if base_status == "failed" else None)
    else:
        base_status = None   # never started
        base_error = None
    return {"base": {"image_rel": base_rel,
                     "asset_id": str(base_asset.id) if base_asset else None,
                     "status": base_status, "error": base_error},
            "shots": shots, "shot_plan": m.get("shot_plan") or [],
            "pose_sets": pose_sets, "costumes": costumes, "emotions": emotions,
            "processed": processed, "generate_all": m.get("generate_all") or {},
            "studio_project_id": str(proj.id)}


# ── datasets ──────────────────────────────────────────────────────────────
async def _dataset_images(session: AsyncSession, proj: Project, c: StudioCharacter,
                          include: Optional[list[str]]) -> dict[str, Path]:
    """Collect candidate dataset images by manifest key.

    Key namespaces (P2 extends the Phase-1 "base" + shot-id set):
    - "base"                    — the character's base render
    - "<shot_id>"               — a Phase-1 shot-plan render
    - "costume:<costume_id>"    — a costume's base sprite
    - "emotion:<emotion_key>"   — a full emotion render
    - "emotion_face:<emotion_key>" — the cropped face-only image for that emotion
    include=None means "everything available" (base + all shots — P2 sprite
    namespaces are opt-in only, since most datasets don't want every costume/
    emotion mixed in by default).
    """
    scene = await session.get(Scene, c.scene_id) if c.scene_id else None
    out: dict[str, Path] = {}
    base_rel = (scene.parameters or {}).get("chosen_image_path") if scene else None
    m = c.manifest or {}
    shots = m.get("shots") or {}
    costumes = m.get("costumes") or {}
    emotions = m.get("emotions") or {}
    wanted = include if include is not None else (["base"] + list(shots.keys()))

    if "base" in wanted and base_rel:
        p = _resolve_rel(proj, base_rel)
        if p:
            out["base"] = p
    for sid, entry in shots.items():
        if sid in wanted and (entry or {}).get("image_rel"):
            p = _resolve_rel(proj, entry["image_rel"])
            if p:
                out[sid] = p
    for cid, costume in costumes.items():
        key = f"costume:{cid}"
        if key in wanted:
            sprite = (costume.get("sprites") or {}).get("base")
            if sprite and sprite.get("image_rel"):
                p = _resolve_rel(proj, sprite["image_rel"])
                if p:
                    out[key] = p
    for ekey, entry in emotions.items():
        face_key = f"emotion_face:{ekey}"
        full_key = f"emotion:{ekey}"
        if full_key in wanted and (entry or {}).get("image_rel"):
            p = _resolve_rel(proj, entry["image_rel"])
            if p:
                out[full_key] = p
        if face_key in wanted and (entry or {}).get("face_crop_rel"):
            p = _resolve_rel(proj, entry["face_crop_rel"])
            if p:
                out[face_key] = p
    return out


async def _run_captioning(dataset_id: UUID, session_factory) -> None:
    """Background: caption every dataset image in the required style(s)."""
    from backend.services.llm.vision import caption_image_sync
    async with session_factory() as session:
        d = await session.get(StudioDataset, dataset_id)
        if not d:
            return
        c = await session.get(StudioCharacter, d.character_id)
        proj = await _ensure_studio_project(session)
        s = await _app_settings(session)
        urls = (getattr(s, "ollama_urls", None) or
                ([s.ollama_base_url] if getattr(s, "ollama_base_url", None) else []))
        model = getattr(s, "ollama_vision_model", None)
        images = await _dataset_images(session, proj, c, (d.config or {}).get("include"))
        cfg_d = dict(d.config or {})
        cfg_d["image_names"] = sorted(images.keys())
        d.config = cfg_d
        styles = (["tags", "natural"] if d.target == "both"
                  else ["tags"] if d.target == "kohya" else ["natural"])
        if not urls or not model:
            d.status = "failed"
            d.error = "Ollama vision is not configured (Settings → Vision model) — captions need it"
            session.add(d)
            await session.commit()
            return
        caps = dict(d.captions or {})
        try:
            for name, path in images.items():
                entry = dict(caps.get(name) or {})
                entry["image_rel"] = str(path)
                for style in styles:
                    if entry.get(style):
                        continue
                    prompt = build_caption_prompt(
                        style, d.trigger_word, d.class_word, c.character_info or {},
                        c.kind, (d.config or {}).get("quality_family", "illustrious"))
                    text = await asyncio.to_thread(
                        caption_image_sync, path, urls, model, prompt, 180.0)
                    if text:
                        text = " ".join(text.split())
                        tw = d.trigger_word
                        if tw and not text.lower().startswith(tw.lower()):
                            text = f"{tw}, {text}"
                        entry[style] = text
                    else:
                        entry.setdefault("errors", []).append(f"{style}: captioner returned nothing")
                caps[name] = entry
                d.captions = dict(caps)
                session.add(d)
                await session.commit()
            missing = [n for n, e in caps.items()
                       if any(not e.get(st) for st in styles)]
            d.status = "ready" if not missing else "failed"
            d.error = "" if not missing else f"captions missing for: {', '.join(missing[:8])}"
        except Exception as e:
            logger.error(f"Character Studio captioning failed: {e}")
            d.status = "failed"
            d.error = f"{type(e).__name__}: {e}"
        session.add(d)
        await session.commit()


@router.post("/characters/{char_id}/datasets")
async def create_dataset(char_id: UUID, body: DatasetIn, request: Request,
                         session: AsyncSession = Depends(get_session)):
    c = await session.get(StudioCharacter, char_id)
    if not c:
        raise HTTPException(404, "Character not found")
    proj = await _ensure_studio_project(session)
    images = await _dataset_images(session, proj, c, body.include)
    if len(images) < 4:
        raise HTTPException(400, f"Only {len(images)} rendered image(s) available — generate the base + shots first")
    d = StudioDataset(
        character_id=c.id, name=body.name.strip() or f"{c.name} dataset",
        target=body.target,
        trigger_word=(body.trigger_word or c.trigger_word).strip(),
        class_word=(body.class_word or c.class_word).strip() or "person",
        status="captioning",
        config={"repeats": body.repeats, "quality_family": body.quality_family,
                "include": body.include},
    )
    session.add(d)
    await session.commit()
    await session.refresh(d)
    from backend.database.database import async_session
    asyncio.create_task(_run_captioning(d.id, async_session))
    return {"id": str(d.id), "status": d.status, "image_count": len(images)}


def _ds_out(d: StudioDataset) -> dict:
    return {"id": str(d.id), "character_id": str(d.character_id), "name": d.name,
            "target": d.target, "trigger_word": d.trigger_word, "class_word": d.class_word,
            "status": d.status, "error": d.error, "config": d.config or {},
            "captions": d.captions or {}, "zip_ready": bool(d.zip_path),
            "created_at": d.created_at.isoformat() if d.created_at else None}


@router.get("/characters/{char_id}/datasets")
async def list_datasets(char_id: UUID, session: AsyncSession = Depends(get_session)):
    rows = (await session.execute(
        select(StudioDataset).where(StudioDataset.character_id == char_id)
        .order_by(StudioDataset.created_at.desc()))).scalars().all()
    return [_ds_out(d) for d in rows]


@router.get("/datasets/{dataset_id}")
async def get_dataset(dataset_id: UUID, session: AsyncSession = Depends(get_session)):
    d = await session.get(StudioDataset, dataset_id)
    if not d:
        raise HTTPException(404, "Dataset not found")
    return _ds_out(d)


@router.patch("/datasets/{dataset_id}/captions")
async def patch_caption(dataset_id: UUID, body: CaptionPatch,
                        session: AsyncSession = Depends(get_session)):
    d = await session.get(StudioDataset, dataset_id)
    if not d:
        raise HTTPException(404, "Dataset not found")
    caps = dict(d.captions or {})
    entry = dict(caps.get(body.image) or {})
    entry[body.style] = body.text
    caps[body.image] = entry
    d.captions = caps
    if d.status == "failed" and all(
            (caps.get(n) or {}).get(st)
            for n in (d.config or {}).get("image_names", caps.keys())
            for st in (["tags", "natural"] if d.target == "both"
                       else ["tags"] if d.target == "kohya" else ["natural"])):
        d.status = "ready"
        d.error = ""
    session.add(d)
    await session.commit()
    return {"ok": True, "status": d.status}


@router.post("/datasets/{dataset_id}/export")
async def export_dataset_ep(dataset_id: UUID, session: AsyncSession = Depends(get_session)):
    d = await session.get(StudioDataset, dataset_id)
    if not d:
        raise HTTPException(404, "Dataset not found")
    if d.status not in ("ready", "exported"):
        raise HTTPException(400, f"Dataset not ready (status={d.status})")
    c = await session.get(StudioCharacter, d.character_id)
    proj = await _ensure_studio_project(session)
    images = await _dataset_images(session, proj, c, (d.config or {}).get("include"))
    ds_dir = studio_root(Path(cfg.project_dir)) / str(c.id) / "datasets" / str(d.id)
    zip_path = await asyncio.to_thread(
        export_dataset, ds_dir, images, d.captions or {}, d.target,
        d.trigger_word, d.class_word, int((d.config or {}).get("repeats") or 10))
    d.zip_path = str(zip_path)
    d.status = "exported"
    session.add(d)
    await session.commit()
    return {"ok": True, "zip_path": d.zip_path}


@router.get("/datasets/{dataset_id}/download")
async def download_dataset(dataset_id: UUID, session: AsyncSession = Depends(get_session)):
    d = await session.get(StudioDataset, dataset_id)
    if not d or not d.zip_path or not Path(d.zip_path).exists():
        raise HTTPException(404, "Export the dataset first")
    c = await session.get(StudioCharacter, d.character_id)
    fname = f"{(c.name if c else 'dataset').replace(' ', '_')}_lora_dataset.zip"
    return FileResponse(d.zip_path, filename=fname, media_type="application/zip")


@router.delete("/datasets/{dataset_id}")
async def delete_dataset(dataset_id: UUID, session: AsyncSession = Depends(get_session)):
    d = await session.get(StudioDataset, dataset_id)
    if not d:
        raise HTTPException(404, "Dataset not found")
    if d.zip_path:
        try:
            import shutil as _sh
            _sh.rmtree(Path(d.zip_path).parent, ignore_errors=True)
        except Exception:
            pass
    await session.delete(d)
    await session.commit()
    return {"ok": True}


# ── push to project ───────────────────────────────────────────────────────
@router.post("/characters/{char_id}/push-to-project")
async def push_to_project(char_id: UUID, body: PushIn,
                          session: AsyncSession = Depends(get_session)):
    import shutil as _sh
    c = await session.get(StudioCharacter, char_id)
    if not c:
        raise HTTPException(404, "Character not found")
    target = await session.get(Project, body.project_id)
    if not target:
        raise HTTPException(404, "Target project not found")
    proj = await _ensure_studio_project(session)
    scene = await session.get(Scene, c.scene_id) if c.scene_id else None
    base_rel = (scene.parameters or {}).get("chosen_image_path") if scene else None
    base_abs = _resolve_rel(proj, base_rel) if base_rel else None
    if not base_abs:
        raise HTTPException(400, "Character has no base render yet")

    tdir = Path(cfg.project_dir) / str(target.id) / "assets" / "studio_characters" / str(c.id)
    tdir.mkdir(parents=True, exist_ok=True)
    base_dst = tdir / f"base{base_abs.suffix.lower()}"
    _sh.copy2(base_abs, base_dst)
    base_rel_t = str(base_dst.relative_to(Path(cfg.project_dir) / str(target.id)))
    # (audit) create an Asset ROW in the target project — every generation-path
    # resolver matches characters' image_path against Asset rows; a bare file
    # copy silently produces zero attached refs (the known silent-downgrade class).
    _base_asset_row = Asset(project_id=target.id, type=AssetType.REFERENCE,
                            rel_path=base_rel_t,
                            meta={"source": "character_studio", "studio_character_id": str(c.id)})
    session.add(_base_asset_row)

    # extra angle refs: prefer distinct full-body angles, then portraits
    prefer = ["three_quarter_l", "three_quarter_r", "back", "profile_l",
              "portrait_front", "portrait_34"]
    shots = (c.manifest or {}).get("shots") or {}
    extras: list[str] = []
    for sid in prefer + [s for s in shots.keys() if s not in prefer]:
        if len(extras) >= max(0, body.max_extra_images):
            break
        rel = (shots.get(sid) or {}).get("image_rel")
        p = _resolve_rel(proj, rel) if rel else None
        if p:
            dst = tdir / f"{sid}{p.suffix.lower()}"
            _sh.copy2(p, dst)
            _rel = str(dst.relative_to(Path(cfg.project_dir) / str(target.id)))
            session.add(Asset(project_id=target.id, type=AssetType.REFERENCE, rel_path=_rel,
                              meta={"source": "character_studio", "studio_character_id": str(c.id),
                                    "angle": sid}))
            extras.append(_rel)

    info = c.character_info or {}
    desc_bits = [c.description] + [str(info[k]) for k in
                 ("race", "hair", "eyes", "face", "body", "additional_details") if info.get(k)]
    entry = {"name": c.name,
             "description": ", ".join(x for x in desc_bits if x)[:500],
             "image_path": base_rel_t, "extra_images": extras,
             "source": "character_studio", "studio_character_id": str(c.id)}
    tsettings = dict(target.settings or {})
    chars = list(tsettings.get("characters") or [])
    replaced = False
    for i, existing in enumerate(chars):
        if isinstance(existing, dict) and existing.get("studio_character_id") == str(c.id):
            chars[i] = entry
            replaced = True
            break
    if not replaced:
        chars.append(entry)
    tsettings["characters"] = chars
    target.settings = tsettings
    session.add(target)
    await session.commit()
    return {"ok": True, "replaced": replaced, "character_index": chars.index(entry),
            "extra_images": len(extras)}


# ═══════════════════════════════════════════════════════════════════════════
# P2 — Pose Studio, Costumes, Emotions, Process (cutout/upscale), Generate-All,
# Wizards.  Design: docs/CHARACTER_STUDIO.md P2 + docs/CHARACTER_STUDIO_P2_API.md.
# ═══════════════════════════════════════════════════════════════════════════

def _engine_error(e: EngineUnavailableError) -> HTTPException:
    return HTTPException(status.HTTP_409_CONFLICT, detail=str(e))


# ── pose presets ──────────────────────────────────────────────────────────
@router.get("/pose-presets")
async def list_pose_presets_ep():
    presets = list(_pose_renderer.list_pose_presets())
    for cid, entry in _load_custom_poses().items():
        presets.append({"id": cid, "name": entry.get("name", cid), "custom": True})
    return {"presets": presets}


@router.get("/pose-presets/{preset_id}/thumbnail")
async def pose_preset_thumbnail(preset_id: str):
    cache_dir = Path(cfg.project_dir) / "_character_studio_cache" / "pose_thumbs"
    if preset_id.startswith("custom_"):
        entry = _load_custom_poses().get(preset_id)
        if not entry:
            raise HTTPException(404, f"Custom pose '{preset_id}' not found")
        cache_dir.mkdir(parents=True, exist_ok=True)
        p = cache_dir / f"{preset_id}.png"
        try:
            _pose_renderer.render_pose(entry, p)
        except Exception as e:
            raise HTTPException(500, f"Pose render failed: {type(e).__name__}: {e}")
        return FileResponse(str(p), media_type="image/png")
    thumbs = _pose_renderer.render_preset_thumbnails(cache_dir)
    p = thumbs.get(preset_id)
    if not p or not Path(p).exists():
        raise HTTPException(404, f"Pose preset '{preset_id}' not found")
    return FileResponse(str(p), media_type="image/png")


# ── custom pose presets (2D pose editor) ───────────────────────────────────
_CUSTOM_POSES_PATH = Path(cfg.project_dir) / "_character_studio" / "custom_poses.json"


def _load_custom_poses() -> dict:
    try:
        return json.loads(_CUSTOM_POSES_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_custom_poses(d: dict) -> None:
    _CUSTOM_POSES_PATH.parent.mkdir(parents=True, exist_ok=True)
    _CUSTOM_POSES_PATH.write_text(json.dumps(d, indent=1), encoding="utf-8")


class CustomPoseIn(BaseModel):
    name: str
    joints: dict   # {joint_name: [x, y]} — 18-joint OpenPose-style, 512x1536 canvas


@router.get("/pose-presets/joints/{preset_id}")
async def pose_preset_joints(preset_id: str):
    """Raw joint dict for a preset — the 2D pose editor loads these as its
    starting skeleton."""
    if preset_id.startswith("custom_"):
        entry = _load_custom_poses().get(preset_id)
        if not entry:
            raise HTTPException(404, f"Custom pose '{preset_id}' not found")
        return {"id": preset_id, "name": entry.get("name", preset_id), "joints": entry.get("joints", {})}
    preset = _pose_renderer.get_pose_preset(preset_id)
    if not preset:
        raise HTTPException(404, f"Pose preset '{preset_id}' not found")
    return {"id": preset_id, "name": preset.get("name", preset_id), "joints": preset.get("joints", {})}


@router.post("/pose-presets/custom")
async def create_custom_pose(body: CustomPoseIn):
    if not body.joints or not isinstance(body.joints, dict):
        raise HTTPException(400, "joints dict is required")
    d = _load_custom_poses()
    slug = "custom_" + ("".join(ch for ch in body.name.lower() if ch.isalnum() or ch == "_")[:24] or "pose")
    base_slug, i = slug, 2
    while slug in d:
        slug = f"{base_slug}_{i}"
        i += 1
    d[slug] = {"name": body.name.strip() or slug, "joints": body.joints}
    _save_custom_poses(d)
    return {"id": slug, "name": d[slug]["name"]}


@router.delete("/pose-presets/custom/{preset_id}")
async def delete_custom_pose(preset_id: str):
    d = _load_custom_poses()
    if preset_id not in d:
        raise HTTPException(404, "Custom pose not found")
    d.pop(preset_id)
    _save_custom_poses(d)
    return {"ok": True}


@router.post("/pose-presets/preview")
async def preview_pose(body: dict):
    """Render an arbitrary joints dict → PNG (the pose editor's live preview)."""
    joints = body.get("joints")
    if not joints or not isinstance(joints, dict):
        raise HTTPException(400, "joints dict is required")
    import hashlib
    cache_dir = Path(cfg.project_dir) / "_character_studio_cache" / "pose_previews"
    cache_dir.mkdir(parents=True, exist_ok=True)
    key = hashlib.md5(json.dumps(joints, sort_keys=True).encode()).hexdigest()[:16]
    out = cache_dir / f"{key}.png"
    if not out.exists():
        try:
            _pose_renderer.render_pose(joints, out)
        except Exception as e:
            raise HTTPException(500, f"Pose render failed: {type(e).__name__}: {e}")
    return FileResponse(str(out), media_type="image/png")


# ── costumes ──────────────────────────────────────────────────────────────
@router.post("/characters/{char_id}/costumes")
async def create_costume(char_id: UUID, body: CostumeIn,
                         session: AsyncSession = Depends(get_session)):
    c = await session.get(StudioCharacter, char_id)
    if not c:
        raise HTTPException(404, "Character not found")
    m = dict(c.manifest or {})
    costumes = dict(m.get("costumes") or {})
    from uuid import uuid4 as _uuid4
    cid = str(_uuid4())
    costumes[cid] = {
        "id": cid, "name": body.name.strip() or "Costume",
        "fields": body.fields or {}, "prompt": body.prompt or "",
        "sprites": {},   # {shot_key: {status, image_rel, asset_id, job_id}}
    }
    m["costumes"] = costumes
    c.manifest = m
    session.add(c)
    await session.commit()
    return {"id": cid, "costume": costumes[cid]}


@router.patch("/characters/{char_id}/costumes/{costume_id}")
async def update_costume(char_id: UUID, costume_id: str, body: CostumeIn,
                         session: AsyncSession = Depends(get_session)):
    c = await session.get(StudioCharacter, char_id)
    if not c:
        raise HTTPException(404, "Character not found")
    m = dict(c.manifest or {})
    costumes = dict(m.get("costumes") or {})
    if costume_id not in costumes:
        raise HTTPException(404, "Costume not found")
    entry = dict(costumes[costume_id])
    entry["name"] = body.name.strip() or entry.get("name", "Costume")
    entry["fields"] = body.fields or entry.get("fields", {})
    entry["prompt"] = body.prompt if body.prompt is not None else entry.get("prompt", "")
    costumes[costume_id] = entry
    m["costumes"] = costumes
    c.manifest = m
    session.add(c)
    await session.commit()
    return {"ok": True, "costume": entry}


@router.delete("/characters/{char_id}/costumes/{costume_id}")
async def delete_costume(char_id: UUID, costume_id: str,
                         session: AsyncSession = Depends(get_session)):
    c = await session.get(StudioCharacter, char_id)
    if not c:
        raise HTTPException(404, "Character not found")
    m = dict(c.manifest or {})
    costumes = dict(m.get("costumes") or {})
    costumes.pop(costume_id, None)
    m["costumes"] = costumes
    c.manifest = m
    session.add(c)
    await session.commit()
    return {"ok": True}


async def _get_identity_asset(session: AsyncSession, proj: Project,
                              c: StudioCharacter) -> Asset:
    scene = await session.get(Scene, c.scene_id) if c.scene_id else None
    base_rel = (scene.parameters or {}).get("chosen_image_path") if scene else None
    if not base_rel:
        raise HTTPException(400, "Generate the base render first — P2 stages edit from it")
    base_asset = await _find_asset_by_rel(session, proj.id, base_rel)
    if not base_asset:
        raise HTTPException(400, "Base render asset not found — regenerate the base render")
    return base_asset


@router.post("/characters/{char_id}/costumes/{costume_id}/generate")
async def generate_costume(char_id: UUID, costume_id: str, body: CostumeGenerateIn,
                           request: Request, session: AsyncSession = Depends(get_session)):
    c = await session.get(StudioCharacter, char_id)
    if not c:
        raise HTTPException(404, "Character not found")
    m = dict(c.manifest or {})
    costumes = dict(m.get("costumes") or {})
    costume = costumes.get(costume_id)
    if not costume:
        raise HTTPException(404, "Costume not found")

    proj = await _ensure_studio_project(session)
    scene = await _ensure_scene(session, proj, c)
    identity_asset = await _get_identity_asset(session, proj, c)

    comfy_dispatcher = getattr(request.app.state, "comfy_dispatcher", None)
    try:
        engine = resolve_engine("costume", body.engine, comfy_dispatcher)
    except EngineUnavailableError as e:
        raise _engine_error(e)

    desc_bits = [costume.get("prompt", "")]
    for k in ("top", "bottom", "head", "face", "shoes"):
        v = (costume.get("fields") or {}).get(k)
        if v:
            desc_bits.append(str(v))
    description = ", ".join(x for x in desc_bits if x)

    params = costume_params(engine, identity_asset_id=str(identity_asset.id),
                            description=description)
    params.update({"frame_type": "first", "auto_save_preview": False,
                   "studio_character_id": str(c.id),
                   "studio_shot_id": f"costume:{costume_id}"})
    job = Job(project_id=proj.id, scene_id=scene.id, job_type=JobType.IMAGE,
              status=JobStatus.PENDING, parameters=params)
    session.add(job)
    await session.flush()

    sprites = dict(costume.get("sprites") or {})
    sprites["base"] = {"status": "pending", "job_id": str(job.id), "engine": engine}
    costume["sprites"] = sprites
    costumes[costume_id] = costume
    m["costumes"] = costumes
    c.manifest = m
    session.add(c)
    await session.commit()
    request.app.state.job_queue.notify()
    return {"job_id": str(job.id), "engine": engine}


# ── poses ─────────────────────────────────────────────────────────────────
@router.post("/characters/{char_id}/poses/generate")
async def generate_poses(char_id: UUID, body: PoseGenerateIn, request: Request,
                         session: AsyncSession = Depends(get_session)):
    c = await session.get(StudioCharacter, char_id)
    if not c:
        raise HTTPException(404, "Character not found")
    if not body.preset_ids:
        raise HTTPException(400, "preset_ids is required (use GET /pose-presets to list options)")

    proj = await _ensure_studio_project(session)
    scene = await _ensure_scene(session, proj, c)
    identity_asset = await _get_identity_asset(session, proj, c)

    comfy_dispatcher = getattr(request.app.state, "comfy_dispatcher", None)
    try:
        engine = resolve_engine("pose", body.engine, comfy_dispatcher)
    except EngineUnavailableError as e:
        raise _engine_error(e)

    m = dict(c.manifest or {})
    pose_sets = dict(m.get("pose_sets") or {})
    created = []
    errors = []
    for preset_id in body.preset_ids:
        preset = _pose_renderer.get_pose_preset(preset_id)
        if not preset and preset_id.startswith("custom_"):
            _custom = _load_custom_poses().get(preset_id)
            if _custom:
                preset = {"id": preset_id, "name": _custom.get("name", preset_id),
                          "joints": _custom.get("joints", {})}
        if not preset:
            errors.append(f"{preset_id}: unknown pose preset")
            continue
        try:
            render_dir = studio_root(Path(cfg.project_dir)) / str(c.id) / "poses"
            render_dir.mkdir(parents=True, exist_ok=True)
            render_path = render_dir / f"{preset_id}.png"
            _pose_renderer.render_pose(preset, render_path)
            rel = _register_asset(proj, render_path, "studio_poses")
            if not rel:
                errors.append(f"{preset_id}: failed to register rendered pose image")
                continue
            pose_asset = await _create_asset_row(
                session, proj, rel, AssetType.REFERENCE,
                meta={"studio_pose_preset_id": preset_id, "studio_character_id": str(c.id)},
            )
            params = pose_edit_params(engine, pose_asset_id=str(pose_asset.id),
                                      identity_asset_id=str(identity_asset.id))
            params.update({"frame_type": "first", "auto_save_preview": False,
                           "studio_character_id": str(c.id),
                           "studio_shot_id": f"pose:{preset_id}"})
            job = Job(project_id=proj.id, scene_id=scene.id, job_type=JobType.IMAGE,
                      status=JobStatus.PENDING, priority=len(created), parameters=params)
            session.add(job)
            await session.flush()
            pose_sets[preset_id] = {
                "status": "pending", "job_id": str(job.id), "engine": engine,
                "pose_asset_id": str(pose_asset.id),
                "name": preset["name"],
            }
            created.append(preset_id)
        except Exception as e:
            logger.error(f"Character Studio pose generate failed for {preset_id}: {e}")
            errors.append(f"{preset_id}: {type(e).__name__}: {e}")

    m["pose_sets"] = pose_sets
    c.manifest = m
    session.add(c)
    await session.commit()
    request.app.state.job_queue.notify()
    return {"created": created, "errors": errors, "engine": engine}


# ── emotions ──────────────────────────────────────────────────────────────
def _emotion_catalog_flat() -> dict[str, dict]:
    """Flatten emotions.json ({category: [entries]}) keyed by safe_name."""
    raw = load_catalog("emotions")
    out: dict[str, dict] = {}
    if isinstance(raw, dict):
        for _cat, entries in raw.items():
            for e in entries or []:
                if isinstance(e, dict) and e.get("safe_name"):
                    out[e["safe_name"]] = e
    return out


@router.post("/characters/{char_id}/emotions/generate")
async def generate_emotions(char_id: UUID, body: EmotionGenerateIn, request: Request,
                            session: AsyncSession = Depends(get_session)):
    c = await session.get(StudioCharacter, char_id)
    if not c:
        raise HTTPException(404, "Character not found")
    if not body.emotions:
        raise HTTPException(400, "emotions is required (safe_name keys from GET /catalogs)")

    proj = await _ensure_studio_project(session)
    scene = await _ensure_scene(session, proj, c)

    # Resolve the source image: base render, or a shot, or a costume sprite.
    m = dict(c.manifest or {})
    source_asset: Optional[Asset] = None
    _src_key = body.source or "base"
    if body.costume_id and _src_key in ("base", body.costume_id):
        # Costume selected → its base sprite is the source (the UI may send
        # either "base" or the costume id itself here — accept both).
        _spr = ((m.get("costumes") or {}).get(body.costume_id, {}).get("sprites", {}) or {})
        rel = (_spr.get("base") or {}).get("image_rel")
        if rel:
            source_asset = await _find_asset_by_rel(session, proj.id, rel)
        if not source_asset:
            raise HTTPException(400, f"Costume '{body.costume_id}' has no rendered base sprite yet")
    elif _src_key == "base":
        source_asset = await _get_identity_asset(session, proj, c)
    else:
        shots = m.get("shots") or {}
        shot_entry = shots.get(body.source)
        costume_sprite = None
        if body.costume_id:
            costume_sprite = ((m.get("costumes") or {}).get(body.costume_id, {})
                              .get("sprites", {}).get(body.source))
        rel = (shot_entry or {}).get("image_rel") or (costume_sprite or {}).get("image_rel")
        if rel:
            source_asset = await _find_asset_by_rel(session, proj.id, rel)
        if not source_asset:
            raise HTTPException(400, f"Source image '{body.source}' not found or not yet rendered")

    comfy_dispatcher = getattr(request.app.state, "comfy_dispatcher", None)
    try:
        engine = resolve_engine("emotion", body.engine, comfy_dispatcher)
    except EngineUnavailableError as e:
        raise _engine_error(e)

    catalog = _emotion_catalog_flat()
    emotions = dict(m.get("emotions") or {})
    created = []
    errors = []
    source_abs = _resolve_rel(proj, source_asset.rel_path)
    for key in body.emotions:
        entry_cat = catalog.get(key)
        if not entry_cat:
            errors.append(f"{key}: unknown emotion key")
            continue
        try:
            face_asset_id = None
            if engine == "klein":
                if not source_abs:
                    errors.append(f"{key}: source image file not found on disk")
                    continue
                mask_dir = studio_root(Path(cfg.project_dir)) / str(c.id) / "emotion_masks"
                mask_dir.mkdir(parents=True, exist_ok=True)
                mask_path = mask_dir / f"{key}.png"
                bbox = _faces.build_face_masked_rgba(source_abs, mask_path)
                if not bbox:
                    errors.append(
                        f"{key}: no face detected in source image — cannot build the "
                        "klein_inpaint mask (try engine='qwen' or a different source image)"
                    )
                    continue
                rel = _register_asset(proj, mask_path, "studio_emotion_masks")
                face_asset = await _create_asset_row(
                    session, proj, rel, AssetType.REFERENCE,
                    meta={"studio_emotion_mask_for": key, "studio_character_id": str(c.id)},
                )
                face_asset_id = str(face_asset.id)

            params = emotion_params(
                engine, identity_asset_id=str(source_asset.id),
                natural_prompt=entry_cat.get("natural_prompt") or entry_cat.get("description", ""),
                face_masked_asset_id=face_asset_id,
            )
            params.update({"frame_type": "first", "auto_save_preview": False,
                           "studio_character_id": str(c.id),
                           "studio_shot_id": f"emotion:{key}"})
            job = Job(project_id=proj.id, scene_id=scene.id, job_type=JobType.IMAGE,
                      status=JobStatus.PENDING, priority=len(created), parameters=params)
            session.add(job)
            await session.flush()
            emotions[key] = {
                "status": "pending", "job_id": str(job.id), "engine": engine,
                "source": body.source, "costume_id": body.costume_id,
            }
            created.append(key)
        except Exception as e:
            logger.error(f"Character Studio emotion generate failed for {key}: {e}")
            errors.append(f"{key}: {type(e).__name__}: {e}")

    m["emotions"] = emotions
    c.manifest = m
    session.add(c)
    await session.commit()
    request.app.state.job_queue.notify()
    return {"created": created, "errors": errors, "engine": engine}


# ── process (cutout / upscale) ─────────────────────────────────────────────
@router.post("/characters/{char_id}/process")
async def process_images(char_id: UUID, body: ProcessIn, request: Request,
                         session: AsyncSession = Depends(get_session)):
    """Run cutout/upscale on one or more of the character's rendered images.

    When a vnccs/upscale worker is online, dispatches jobs (async — poll via
    /status). When none is online for the requested step, runs the CPU
    fallback (cutout only — upscale has no CPU fallback) synchronously and
    returns results inline.
    """
    c = await session.get(StudioCharacter, char_id)
    if not c:
        raise HTTPException(404, "Character not found")
    if not body.image_refs:
        raise HTTPException(400, "image_refs is required")

    proj = await _ensure_studio_project(session)
    scene = await _ensure_scene(session, proj, c)
    m = dict(c.manifest or {})

    async def _resolve_ref(ref: str) -> Optional[Asset]:
        # Accept the UI's prefixed forms (audit FE-B2): "costume:<id>" → that
        # costume's base sprite; "emotion:<key>" → that emotion's sprite.
        if ref.startswith("costume:"):
            _cid = ref.split(":", 1)[1]
            _spr = (((c.manifest or {}).get("costumes") or {}).get(_cid) or {}).get("sprites") or {}
            _aid = (_spr.get("base") or {}).get("asset_id")
            return await session.get(Asset, UUID(str(_aid))) if _aid else None
        if ref.startswith("emotion:"):
            _ek = ref.split(":", 1)[1]
            _ent = ((c.manifest or {}).get("emotions") or {}).get(_ek) or {}
            _aid = _ent.get("asset_id")
            return await session.get(Asset, UUID(str(_aid))) if _aid else None
        if ref == "base":
            scene2 = await session.get(Scene, c.scene_id) if c.scene_id else None
            rel = (scene2.parameters or {}).get("chosen_image_path") if scene2 else None
            return await _find_asset_by_rel(session, proj.id, rel) if rel else None
        shots = m.get("shots") or {}
        if ref in shots and (shots[ref] or {}).get("image_rel"):
            return await _find_asset_by_rel(session, proj.id, shots[ref]["image_rel"])
        for costume in (m.get("costumes") or {}).values():
            sprite = (costume.get("sprites") or {}).get(ref)
            if sprite and sprite.get("image_rel"):
                return await _find_asset_by_rel(session, proj.id, sprite["image_rel"])
        emo = (m.get("emotions") or {}).get(ref)
        if emo and emo.get("image_rel"):
            return await _find_asset_by_rel(session, proj.id, emo["image_rel"])
        return None

    comfy_dispatcher = getattr(request.app.state, "comfy_dispatcher", None)
    want_cutout = bool(body.steps.get("cutout"))
    want_upscale = bool(body.steps.get("upscale"))
    vnccs_online = _worker_online(comfy_dispatcher, "vnccs")
    upscale_online = _worker_online(comfy_dispatcher, "upscale")

    processed = dict(m.get("processed") or {})
    created_jobs = []
    inline_results = []
    errors = []

    for ref in body.image_refs:
        asset = await _resolve_ref(ref)
        if not asset:
            errors.append(f"{ref}: image not found or not yet rendered")
            continue
        entry = dict(processed.get(ref) or {})

        if want_cutout:
            if vnccs_online:
                params = cutout_params(image_asset_id=str(asset.id))
                params.update({"frame_type": "first", "auto_save_preview": False,
                               "studio_character_id": str(c.id),
                               "studio_shot_id": f"cutout:{ref}"})
                job = Job(project_id=proj.id, scene_id=scene.id, job_type=JobType.IMAGE,
                          status=JobStatus.PENDING, parameters=params)
                session.add(job)
                await session.flush()
                entry["cutout"] = {"status": "pending", "job_id": str(job.id)}
                created_jobs.append(str(job.id))
            else:
                src_abs = _resolve_rel(proj, asset.rel_path)
                if not src_abs:
                    errors.append(f"{ref}: cutout source file missing on disk")
                else:
                    out_dir = studio_root(Path(cfg.project_dir)) / str(c.id) / "processed"
                    out_dir.mkdir(parents=True, exist_ok=True)
                    out_path = out_dir / f"{ref}_cutout.png"
                    ok, reason = _cutout.cutout_cpu(src_abs, out_path)
                    if ok:
                        rel = _register_asset(proj, out_path, "studio_processed")
                        cutout_asset = await _create_asset_row(
                            session, proj, rel, AssetType.GENERATED_IMAGE,
                            meta={"studio_processed_from": ref, "studio_character_id": str(c.id)},
                        )
                        entry["cutout"] = {"status": "done", "image_rel": str(rel),
                                           "asset_id": str(cutout_asset.id), "method": "cpu_fallback",
                                           "note": reason}
                        inline_results.append({"ref": ref, "step": "cutout", "asset_id": str(cutout_asset.id)})
                    else:
                        entry["cutout"] = {"status": "failed", "error": reason}
                        errors.append(f"{ref}: cutout failed — {reason}")

        if want_upscale:
            _up_mode, upscale_online = _resolve_upscale_mode(
                getattr(request.app.state, "comfy_dispatcher", None), body.upscale_mode)
            if upscale_online:
                params = upscale_params(image_asset_id=str(asset.id), mode=_up_mode)
                params.update({"frame_type": "first", "auto_save_preview": False,
                               "studio_character_id": str(c.id),
                               "studio_shot_id": f"upscale:{ref}"})
                job = Job(project_id=proj.id, scene_id=scene.id, job_type=JobType.IMAGE,
                          status=JobStatus.PENDING, parameters=params)
                session.add(job)
                await session.flush()
                entry["upscale"] = {"status": "pending", "job_id": str(job.id)}
                created_jobs.append(str(job.id))
            else:
                entry["upscale"] = {"status": "failed",
                                    "error": "no upscale-capable worker online — no CPU fallback exists for upscale"}
                errors.append(f"{ref}: upscale unavailable — no worker online (no CPU fallback)")

        processed[ref] = entry

    m["processed"] = processed
    c.manifest = m
    session.add(c)
    await session.commit()
    if created_jobs:
        request.app.state.job_queue.notify()
    return {"jobs": created_jobs, "inline_results": inline_results, "errors": errors}


# ── generate-all orchestrator ──────────────────────────────────────────────
async def _run_generate_all(char_id: UUID, body_dict: dict, session_factory,
                            comfy_dispatcher) -> None:
    """Background orchestrator: base → shots → costumes → emotions → process.

    Never dies on one failure — every stage/item exception is caught and
    recorded in manifest["generate_all"]["errors"]; the run continues to the
    next item/stage.  Progress is polled by the frontend via GET
    /characters/{id}/status (which reads manifest["generate_all"]).
    """
    async def _set_ga(patch: dict) -> None:
        async with session_factory() as session:
            c = await session.get(StudioCharacter, char_id)
            if not c:
                return
            m = dict(c.manifest or {})
            ga = dict(m.get("generate_all") or {})
            ga.update(patch)
            m["generate_all"] = ga
            c.manifest = m
            session.add(c)
            await session.commit()

    async def _add_error(msg: str) -> None:
        async with session_factory() as session:
            c = await session.get(StudioCharacter, char_id)
            if not c:
                return
            m = dict(c.manifest or {})
            ga = dict(m.get("generate_all") or {})
            errs = list(ga.get("errors") or [])
            errs.append(msg)
            ga["errors"] = errs
            m["generate_all"] = ga
            c.manifest = m
            session.add(c)
            await session.commit()

    engine_req = body_dict.get("engine", "auto")
    include = body_dict.get("include") or {}
    await _set_ga({"status": "running", "stage": "base", "errors": []})

    try:
        engine = resolve_engine("generate_all", engine_req, comfy_dispatcher)
    except EngineUnavailableError as e:
        await _set_ga({"status": "failed", "stage": "base"})
        await _add_error(f"base: engine resolution failed — {e}")
        return

    try:
        # ── Stage: base ──────────────────────────────────────────────
        async with session_factory() as session:
            c = await session.get(StudioCharacter, char_id)
            proj = await _ensure_studio_project(session)
            scene = await _ensure_scene(session, proj, c)
            base_rel = (scene.parameters or {}).get("chosen_image_path")
            base_job_id = None
            if not base_rel:
                s = await _app_settings(session)
                w, h = _res_from_settings(s)
                prompt = build_base_prompt(c.character_info or {}, c.kind, "")
                scene.prompt = prompt
                session.add(scene)
                job = Job(project_id=proj.id, scene_id=scene.id, job_type=JobType.IMAGE,
                          status=JobStatus.PENDING,
                          parameters={"workflow_type": "klein_t2i", "prompt": prompt,
                                      "width": w, "height": h, "reference_asset_ids": [],
                                      "frame_type": "first", "auto_save_preview": True,
                                      "studio_character_id": str(c.id), "studio_shot_id": "base"})
                session.add(job)
                await session.commit()
                await session.refresh(job)
                base_job_id = job.id
        if base_job_id:
            request_notify_ok = True
            try:
                # Best-effort notify — the dispatch loop also polls on its own timer.
                pass
            except Exception:
                request_notify_ok = False
            status_, _asset, err = await _poll_job(session_factory, base_job_id, timeout_s=600.0)
            if status_ != "done":
                await _add_error(f"base: {err or status_}")
                await _set_ga({"status": "failed", "stage": "base"})
                return

        # ── Stage: shots ─────────────────────────────────────────────
        if include.get("shots", True):
            await _set_ga({"stage": "shots"})
            async with session_factory() as session:
                c = await session.get(StudioCharacter, char_id)
                proj = await _ensure_studio_project(session)
                scene = await session.get(Scene, c.scene_id)
                base_rel = (scene.parameters or {}).get("chosen_image_path")
                base_asset = await _find_asset_by_rel(session, proj.id, base_rel) if base_rel else None
                s = await _app_settings(session)
                w, h = _res_from_settings(s)
                m = dict(c.manifest or {})
                plan = m.get("shot_plan") or default_shot_plan(c.character_info or {}, c.kind)
                shots = dict(m.get("shots") or {})
                shot_job_ids = []
                if base_asset:
                    for shot in plan:
                        sid = shot.get("id")
                        if not sid or not shot.get("enabled", True):
                            continue
                        if (shots.get(sid) or {}).get("image_rel"):
                            continue
                        job = Job(project_id=proj.id, scene_id=scene.id, job_type=JobType.IMAGE,
                                  status=JobStatus.PENDING,
                                  parameters={"workflow_type": "klein_1ref",
                                              "prompt": shot.get("instruction") or "",
                                              "width": w, "height": h,
                                              "reference_asset_ids": [str(base_asset.id)],
                                              "frame_type": "first", "auto_save_preview": False,
                                              "studio_character_id": str(c.id), "studio_shot_id": sid})
                        session.add(job)
                        await session.flush()
                        shots[sid] = {"status": "pending", "job_id": str(job.id)}
                        shot_job_ids.append((sid, job.id))
                    m["shots"] = shots
                    c.manifest = m
                    session.add(c)
                    await session.commit()
                else:
                    await _add_error("shots: no base render asset found — skipping shots stage")
            for sid, jid in shot_job_ids:
                status_, asset_id, err = await _poll_job(session_factory, jid, timeout_s=600.0)
                if status_ != "done":
                    await _add_error(f"shots/{sid}: {err or status_}")
                elif asset_id:
                    async with session_factory() as session:
                        c = await session.get(StudioCharacter, char_id)
                        m = dict(c.manifest or {})
                        shots = dict(m.get("shots") or {})
                        a = await session.get(Asset, UUID(asset_id))
                        if a:
                            shots[sid] = {"status": "done", "job_id": str(jid),
                                          "image_rel": a.rel_path, "asset_id": asset_id}
                            m["shots"] = shots
                            c.manifest = m
                            session.add(c)
                            await session.commit()

        # ── Stage: costumes ──────────────────────────────────────────
        costume_ids = include.get("costume_ids") or []
        if costume_ids:
            await _set_ga({"stage": "costumes"})
            for cid in costume_ids:
                try:
                    async with session_factory() as session:
                        c = await session.get(StudioCharacter, char_id)
                        proj = await _ensure_studio_project(session)
                        scene = await session.get(Scene, c.scene_id)
                        identity_asset = await _get_identity_asset(session, proj, c)
                        m = dict(c.manifest or {})
                        costumes = dict(m.get("costumes") or {})
                        costume = costumes.get(cid)
                        if not costume:
                            await _add_error(f"costume/{cid}: not found")
                            continue
                        desc_bits = [costume.get("prompt", "")]
                        for k in ("top", "bottom", "head", "face", "shoes"):
                            v = (costume.get("fields") or {}).get(k)
                            if v:
                                desc_bits.append(str(v))
                        description = ", ".join(x for x in desc_bits if x)
                        params = costume_params(engine, identity_asset_id=str(identity_asset.id),
                                                description=description)
                        params.update({"frame_type": "first", "auto_save_preview": False,
                                       "studio_character_id": str(c.id),
                                       "studio_shot_id": f"costume:{cid}"})
                        job = Job(project_id=proj.id, scene_id=scene.id, job_type=JobType.IMAGE,
                                  status=JobStatus.PENDING, parameters=params)
                        session.add(job)
                        await session.flush()
                        sprites = dict(costume.get("sprites") or {})
                        sprites["base"] = {"status": "pending", "job_id": str(job.id), "engine": engine}
                        costume["sprites"] = sprites
                        costumes[cid] = costume
                        m["costumes"] = costumes
                        c.manifest = m
                        session.add(c)
                        await session.commit()
                        jid = job.id
                    status_, asset_id, err = await _poll_job(session_factory, jid, timeout_s=600.0)
                    if status_ != "done":
                        await _add_error(f"costume/{cid}: {err or status_}")
                    elif asset_id:
                        async with session_factory() as session:
                            c = await session.get(StudioCharacter, char_id)
                            m = dict(c.manifest or {})
                            costumes = dict(m.get("costumes") or {})
                            a = await session.get(Asset, UUID(asset_id))
                            if a and cid in costumes:
                                sprites = dict(costumes[cid].get("sprites") or {})
                                sprites["base"] = {"status": "done", "job_id": str(jid),
                                                   "image_rel": a.rel_path, "asset_id": asset_id}
                                costumes[cid]["sprites"] = sprites
                                m["costumes"] = costumes
                                c.manifest = m
                                session.add(c)
                                await session.commit()
                except Exception as e:
                    await _add_error(f"costume/{cid}: {type(e).__name__}: {e}")

        # ── Stage: emotions ──────────────────────────────────────────
        emotion_keys = include.get("emotions") or []
        if emotion_keys:
            await _set_ga({"stage": "emotions"})
            catalog = _emotion_catalog_flat()
            for key in emotion_keys:
                try:
                    async with session_factory() as session:
                        c = await session.get(StudioCharacter, char_id)
                        proj = await _ensure_studio_project(session)
                        scene = await session.get(Scene, c.scene_id)
                        identity_asset = await _get_identity_asset(session, proj, c)
                        entry_cat = catalog.get(key)
                        if not entry_cat:
                            await _add_error(f"emotion/{key}: unknown emotion key")
                            continue
                        face_asset_id = None
                        if engine == "klein":
                            src_abs = _resolve_rel(proj, identity_asset.rel_path)
                            if not src_abs:
                                await _add_error(f"emotion/{key}: source file missing on disk")
                                continue
                            mask_dir = studio_root(Path(cfg.project_dir)) / str(c.id) / "emotion_masks"
                            mask_dir.mkdir(parents=True, exist_ok=True)
                            mask_path = mask_dir / f"{key}.png"
                            bbox = _faces.build_face_masked_rgba(src_abs, mask_path)
                            if not bbox:
                                await _add_error(f"emotion/{key}: no face detected — skipped")
                                continue
                            rel = _register_asset(proj, mask_path, "studio_emotion_masks")
                            face_asset = await _create_asset_row(
                                session, proj, rel, AssetType.REFERENCE,
                                meta={"studio_emotion_mask_for": key, "studio_character_id": str(c.id)})
                            face_asset_id = str(face_asset.id)
                        params = emotion_params(
                            engine, identity_asset_id=str(identity_asset.id),
                            natural_prompt=entry_cat.get("natural_prompt") or entry_cat.get("description", ""),
                            face_masked_asset_id=face_asset_id)
                        params.update({"frame_type": "first", "auto_save_preview": False,
                                       "studio_character_id": str(c.id),
                                       "studio_shot_id": f"emotion:{key}"})
                        job = Job(project_id=proj.id, scene_id=scene.id, job_type=JobType.IMAGE,
                                  status=JobStatus.PENDING, parameters=params)
                        session.add(job)
                        await session.flush()
                        m = dict(c.manifest or {})
                        emotions = dict(m.get("emotions") or {})
                        emotions[key] = {"status": "pending", "job_id": str(job.id), "engine": engine,
                                        "source": "base"}
                        m["emotions"] = emotions
                        c.manifest = m
                        session.add(c)
                        await session.commit()
                        jid = job.id
                    status_, asset_id, err = await _poll_job(session_factory, jid, timeout_s=600.0)
                    if status_ != "done":
                        await _add_error(f"emotion/{key}: {err or status_}")
                        continue
                    if asset_id:
                        async with session_factory() as session:
                            c = await session.get(StudioCharacter, char_id)
                            proj = await _ensure_studio_project(session)
                            m = dict(c.manifest or {})
                            emotions = dict(m.get("emotions") or {})
                            a = await session.get(Asset, UUID(asset_id))
                            if a:
                                emotions[key] = {**(emotions.get(key) or {}), "status": "done",
                                                "image_rel": a.rel_path, "asset_id": asset_id}
                                # Crop the face out of the result for dataset/manifest use.
                                a_abs = _resolve_rel(proj, a.rel_path)
                                if a_abs:
                                    crop_dir = studio_root(Path(cfg.project_dir)) / str(c.id) / "emotion_faces"
                                    crop_dir.mkdir(parents=True, exist_ok=True)
                                    crop_path = crop_dir / f"{key}.png"
                                    crop_bbox = _faces.crop_face(a_abs, crop_path)
                                    if crop_bbox:
                                        crop_rel = _register_asset(proj, crop_path, "studio_emotion_faces")
                                        if crop_rel:
                                            emotions[key]["face_crop_rel"] = str(crop_rel)
                                m["emotions"] = emotions
                                c.manifest = m
                                session.add(c)
                                await session.commit()
                except Exception as e:
                    await _add_error(f"emotion/{key}: {type(e).__name__}: {e}")

        # ── Stage: process (cutout/upscale) ──────────────────────────
        if include.get("cutout") or include.get("upscale"):
            await _set_ga({"stage": "process"})
            try:
                async with session_factory() as session:
                    c = await session.get(StudioCharacter, char_id)
                    proj = await _ensure_studio_project(session)
                    scene = await session.get(Scene, c.scene_id)
                    base_asset = await _get_identity_asset(session, proj, c)
                    vnccs_online = _worker_online(comfy_dispatcher, "vnccs")
                    if include.get("cutout"):
                        if not vnccs_online:
                            src_abs = _resolve_rel(proj, base_asset.rel_path)
                            if src_abs:
                                out_dir = studio_root(Path(cfg.project_dir)) / str(c.id) / "processed"
                                out_dir.mkdir(parents=True, exist_ok=True)
                                out_path = out_dir / "base_cutout.png"
                                ok, reason = _cutout.cutout_cpu(src_abs, out_path)
                                if not ok:
                                    await _add_error(f"process/cutout: {reason}")
                            else:
                                await _add_error("process/cutout: base image missing on disk")
                        else:
                            params = cutout_params(image_asset_id=str(base_asset.id))
                            params.update({"frame_type": "first", "auto_save_preview": False,
                                           "studio_character_id": str(c.id), "studio_shot_id": "cutout:base"})
                            job = Job(project_id=proj.id, scene_id=scene.id, job_type=JobType.IMAGE,
                                      status=JobStatus.PENDING, parameters=params)
                            session.add(job)
                            await session.commit()
                            await session.refresh(job)
                            jid = job.id
                            status_, _asset, err = await _poll_job(session_factory, jid, timeout_s=600.0)
                            if status_ != "done":
                                await _add_error(f"process/cutout: {err or status_}")
                    if include.get("upscale"):
                        _up_mode, upscale_online = _resolve_upscale_mode(
                            comfy_dispatcher, str(include.get("upscale_mode") or "auto"))
                        if not upscale_online:
                            await _add_error(f"process/upscale: no {_up_mode}-capable worker online (no CPU fallback)")
                        else:
                            params = upscale_params(image_asset_id=str(base_asset.id), mode=_up_mode)
                            params.update({"frame_type": "first", "auto_save_preview": False,
                                           "studio_character_id": str(c.id), "studio_shot_id": "upscale:base"})
                            job = Job(project_id=proj.id, scene_id=scene.id, job_type=JobType.IMAGE,
                                      status=JobStatus.PENDING, parameters=params)
                            session.add(job)
                            await session.commit()
                            await session.refresh(job)
                            jid = job.id
                            status_, _asset, err = await _poll_job(session_factory, jid, timeout_s=600.0)
                            if status_ != "done":
                                await _add_error(f"process/upscale: {err or status_}")
            except Exception as e:
                await _add_error(f"process: {type(e).__name__}: {e}")

        await _set_ga({"status": "done", "stage": "done"})
    except Exception as e:
        logger.error(f"Character Studio generate-all failed for {char_id}: {e}", exc_info=True)
        await _add_error(f"fatal: {type(e).__name__}: {e}")
        await _set_ga({"status": "failed"})


@router.post("/characters/{char_id}/generate-all")
async def generate_all(char_id: UUID, body: GenerateAllIn, request: Request,
                       session: AsyncSession = Depends(get_session)):
    c = await session.get(StudioCharacter, char_id)
    if not c:
        raise HTTPException(404, "Character not found")
    comfy_dispatcher = getattr(request.app.state, "comfy_dispatcher", None)
    try:
        resolve_engine("generate_all", body.engine, comfy_dispatcher)
    except EngineUnavailableError as e:
        raise _engine_error(e)

    m = dict(c.manifest or {})
    m["generate_all"] = {"status": "running", "stage": "queued", "errors": []}
    c.manifest = m
    session.add(c)
    await session.commit()

    from backend.database.database import async_session
    asyncio.create_task(_run_generate_all(
        char_id, {"engine": body.engine, "include": body.include}, async_session, comfy_dispatcher))
    request.app.state.job_queue.notify()
    return {"ok": True, "status": "running"}


# ── preflight ───────────────────────────────────────────────────────────────
@router.get("/characters/{char_id}/preflight")
async def preflight(char_id: UUID, engine: str = "auto", request: Request = None,
                    session: AsyncSession = Depends(get_session)):
    c = await session.get(StudioCharacter, char_id)
    if not c:
        raise HTTPException(404, "Character not found")
    warnings: list[str] = []
    ok = True

    proj = await _ensure_studio_project(session)
    scene = await session.get(Scene, c.scene_id) if c.scene_id else None
    base_rel = (scene.parameters or {}).get("chosen_image_path") if scene else None
    if not base_rel:
        warnings.append("No base render yet — pose/costume/emotion stages need it first.")

    comfy_dispatcher = getattr(request.app.state, "comfy_dispatcher", None) if request else None
    engine_resolved = None
    try:
        engine_resolved = resolve_engine("preflight", engine, comfy_dispatcher)
    except EngineUnavailableError as e:
        ok = False
        warnings.append(str(e))

    s = await _app_settings(session)
    if not (getattr(s, "vision_enabled", False) and getattr(s, "ollama_vision_model", None)):
        warnings.append(
            "Ollama vision is not fully configured (Settings → Vision) — dataset captioning "
            "and clone-from-image wizard will be degraded/unavailable."
        )
    upscale_online = _worker_online(comfy_dispatcher, "upscale")
    if not upscale_online:
        warnings.append("No upscale-capable worker online — GAN upscale will be unavailable.")
    seedvr2_online = _worker_online(comfy_dispatcher, "seedvr2")
    if not seedvr2_online:
        warnings.append("No SeedVR2-capable worker online — premium upscale falls back to GAN.")

    facedetailer_online = (_worker_online(comfy_dispatcher, "impact")
                           and _worker_online(comfy_dispatcher, "vnccs"))
    if not facedetailer_online:
        warnings.append("FaceDetailer emotion engine unavailable (needs Impact-Pack on the VNCCS worker).")

    klein_online = _worker_online(comfy_dispatcher, "klein")
    qwen_online = _worker_online(comfy_dispatcher, "vnccs")
    impact_online = _worker_online(comfy_dispatcher, "impact")
    return {"ok": ok, "engine_resolved": engine_resolved, "warnings": warnings,
            "seedvr2_online": seedvr2_online, "gan_upscale_online": upscale_online,
            "facedetailer_online": facedetailer_online,
            "klein_online": klein_online, "qwen_online": qwen_online,
            "impact_online": impact_online}


# ── wizards ─────────────────────────────────────────────────────────────────
_CHARACTER_WIZARD_SYSTEM = (
    "You are a professional anime/game character designer. Convert broad "
    "character ideas into concise structured character fields. Output valid "
    "JSON only, no markdown, no commentary."
)


def _wizard_system(style: str = "") -> str:
    """Style-aware wizard system prompt so the tag sheet matches the target
    look (anime vs photorealistic vs 3D, etc.) instead of always anime."""
    label = style_label(style_key_of(None, style))
    return (
        f"You are a professional {label} character designer. Convert broad "
        "character ideas into concise structured character fields. Output valid "
        "JSON only, no markdown, no commentary."
    )


def _character_wizard_user_prompt(description: str) -> str:
    tags = load_catalog("character_tags")
    return f"""Create a character from this abstract idea:
{description}

Prefer existing tags from this catalog when they fit (only invent something else if nothing matches):
{json.dumps(tags, ensure_ascii=False)[:4000]}

Return a raw JSON object with exactly these keys:
- sex: "male" or "female"
- age: integer from 1 to 100
- race
- skin_color
- body
- face
- hair
- eyes
- additional_details

Rules:
- Use comma-separated prompt fragments for text fields.
- The race field is for species/fantasy traits only. For normal humans set race to "human".
- Never put ethnicity, nationality, profession, role, clothing, or archetype in race.
- Put skin tone in skin_color, not race.
- For body, always provide a visible body/build descriptor.
- Do not describe clothing, background, camera, pose, quality tags, style tags, or negative prompts.
- If a field is not needed, use an empty string.
- Set sex and age explicitly based on the description; infer a reasonable adult character if unspecified.
"""


def _parse_wizard_json(content: str) -> Optional[dict]:
    content = (content or "").strip()
    try:
        return json.loads(content)
    except Exception:
        pass
    import re as _re
    if "```json" in content:
        content = content.split("```json", 1)[1].split("```", 1)[0]
    elif "```" in content:
        content = content.split("```", 1)[1].split("```", 1)[0]
    else:
        m = _re.search(r"\{.*\}", content, _re.DOTALL)
        if m:
            content = m.group(0)
    try:
        data = json.loads(content.strip())
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _ollama_chat_json(urls: list[str], model: str, system_prompt: str, user_prompt: str,
                      image_b64: Optional[str] = None, timeout: float = 120.0) -> Optional[str]:
    """Minimal direct Ollama /api/chat call for JSON-output wizards.

    Not a reuse of PromptEnhancer._enhance_ollama because that helper is
    hard-wired to the image/video prompt-enhancement system prompts and
    always runs `_clean_prompt_preserve_segments` on the output (which would
    mangle a JSON payload). Round-robins across the ollama_urls pool exactly
    like vision.caption_image_sync, and never raises.
    """
    import httpx
    message: dict = {"role": "user", "content": user_prompt}
    if image_b64:
        message["images"] = [image_b64]
    body = {
        "model": model,
        "messages": [{"role": "system", "content": system_prompt}, message],
        "stream": False,
        "options": {"temperature": 0.3},
    }
    for url in urls:
        try:
            r = httpx.post(f"{url.rstrip('/')}/api/chat", json=body, timeout=timeout)
            r.raise_for_status()
            data = r.json()
            return (data.get("message") or {}).get("content")
        except Exception as e:
            logger.warning(f"character wizard: ollama call to {url} failed: {e}")
            continue
    return None


@router.post("/wizards/character")
async def wizard_character(body: WizardCharacterIn, session: AsyncSession = Depends(get_session)):
    description = (body.description or "").strip()
    if not description:
        raise HTTPException(400, "description is required")
    s = await _app_settings(session)
    urls = (getattr(s, "ollama_urls", None) or
            ([s.ollama_base_url] if getattr(s, "ollama_base_url", None) else []))
    model = getattr(s, "ollama_model", None)
    if not urls or not model:
        raise HTTPException(
            409, "Ollama is not configured (Settings → LLM) — the character wizard needs a "
            "local Ollama text model. Configure ollama_urls + ollama_model, or fill the "
            "character sheet manually."
        )
    content = _ollama_chat_json(urls, model, _wizard_system(body.style),
                                _character_wizard_user_prompt(description))
    parsed = _parse_wizard_json(content or "")
    if parsed is None:
        raise HTTPException(502, f"Character wizard: could not parse LLM JSON output. Raw: {(content or '')[:300]}")
    for key in ("race", "skin_color", "body", "face", "hair", "eyes", "additional_details"):
        parsed.setdefault(key, "")
    parsed["sex"] = "male" if str(parsed.get("sex", "female")).lower().startswith("m") else "female"
    try:
        parsed["age"] = max(1, min(100, int(float(parsed.get("age", 18)))))
    except Exception:
        parsed["age"] = 18
    return {"character_info": parsed}


class WizardCloneIn(BaseModel):
    asset_id: UUID
    style: str = ""              # art style so the cloned tag sheet matches the look


@router.post("/wizards/clone")
async def wizard_clone(body: WizardCloneIn, session: AsyncSession = Depends(get_session)):
    """Clone-from-image: describe an uploaded/existing asset via the Ollama
    vision model, then extract a character tag sheet from that description.
    Upload the source image via the normal
    POST /api/projects/{project_id}/assets/upload endpoint first, then pass
    its asset_id here (mirrors the klein_inpaint mask-upload convention)."""
    asset = await session.get(Asset, body.asset_id)
    if not asset:
        raise HTTPException(404, "Source asset not found — upload it first via the assets endpoint")

    s = await _app_settings(session)
    urls = (getattr(s, "ollama_urls", None) or
            ([s.ollama_base_url] if getattr(s, "ollama_base_url", None) else []))
    vision_model = getattr(s, "ollama_vision_model", None)
    text_model = getattr(s, "ollama_model", None)
    if not urls or not vision_model:
        raise HTTPException(
            409, "Ollama vision is not configured (Settings → Vision model) — the clone "
            "wizard needs it to describe the source image."
        )
    if not text_model:
        raise HTTPException(409, "Ollama text model is not configured (Settings → LLM).")

    abs_path = Path(cfg.project_dir) / str(asset.project_id) / asset.rel_path
    if not abs_path.exists():
        abs_path = Path(cfg.project_dir) / asset.rel_path
    if not abs_path.exists():
        raise HTTPException(400, "Source asset file not found on disk")

    from backend.services.llm.vision import DESCRIBE_PROMPT, caption_image_sync
    description = await asyncio.to_thread(
        caption_image_sync, abs_path, urls, vision_model, DESCRIBE_PROMPT, 120.0)
    if not description:
        raise HTTPException(502, "Vision model returned no description for the source image")

    content = _ollama_chat_json(urls, text_model, _wizard_system(body.style),
                                _character_wizard_user_prompt(description))
    parsed = _parse_wizard_json(content or "")
    if parsed is None:
        raise HTTPException(502, f"Clone wizard: could not parse LLM JSON output. Raw: {(content or '')[:300]}")
    for key in ("race", "skin_color", "body", "face", "hair", "eyes", "additional_details"):
        parsed.setdefault(key, "")
    parsed["sex"] = "male" if str(parsed.get("sex", "female")).lower().startswith("m") else "female"
    try:
        parsed["age"] = max(1, min(100, int(float(parsed.get("age", 18)))))
    except Exception:
        parsed["age"] = 18
    return {"character_info": parsed, "vision_description": description}

