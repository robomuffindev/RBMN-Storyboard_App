"""Project CRUD endpoints for RBMN Storyboard App."""
import logging
import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from backend.config import settings
from backend.database import get_session
from backend.database.models import Project, ProjectMode, Scene, Asset, Job
from backend.services import scene_ref_mode
from backend.utils.file_utils import ensure_project_dirs

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/projects", tags=["projects"])


# Pydantic models for request/response
class ProjectCreate(BaseModel):
    """Request model for creating a project."""

    name: str
    mode: ProjectMode = ProjectMode.MUSIC_VIDEO
    settings: Optional[dict] = None  # Optional initial settings (e.g. lipsync_default)


class ProjectUpdate(BaseModel):
    """Request model for updating a project."""

    name: Optional[str] = None
    settings: Optional[dict] = None


class ProjectResponse(BaseModel):
    """Response model for a project."""

    id: UUID
    name: str
    mode: ProjectMode
    created_at: datetime
    updated_at: datetime
    settings: Optional[dict] = None
    scenes_count: int = 0
    assets_count: int = 0

    class Config:
        from_attributes = True


@router.post(
    "",
    response_model=ProjectResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new project",
)
async def create_project(
    req: ProjectCreate,
    session: AsyncSession = Depends(get_session),
) -> ProjectResponse:
    """Create a new project with the specified name and mode.

    Creates the standard project directory structure (assets/audio, assets/video,
    assets/images, assets/thumbs, cache, backups).

    Args:
        req: Project creation request (name, mode).
        session: Database session.

    Returns:
        Created project with id and metadata.

    Raises:
        HTTPException: If project creation fails.
    """
    try:
        # Create project in database
        project = Project(name=req.name, mode=req.mode)
        if req.settings:
            project.settings = req.settings
        session.add(project)
        await session.flush()

        # Create project directories
        project_path = settings.project_dir / str(project.id)
        project_path.mkdir(parents=True, exist_ok=True)
        ensure_project_dirs(project_path)

        await session.commit()
        await session.refresh(project)

        logger.info(f"Created project {project.id}: {project.name} ({project.mode})")

        return ProjectResponse(
            id=project.id,
            name=project.name,
            mode=project.mode,
            created_at=project.created_at,
            updated_at=project.updated_at,
            settings=project.settings,
            scenes_count=0,
            assets_count=0,
        )
    except Exception as e:
        logger.error(f"Error creating project: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create project",
        )


@router.get(
    "",
    response_model=list[ProjectResponse],
    summary="List all projects",
)
async def list_projects(
    session: AsyncSession = Depends(get_session),
) -> list[ProjectResponse]:
    """Get all projects with scene and asset counts.

    Returns:
        List of all projects with metadata.
    """
    try:
        stmt = select(Project).order_by(Project.updated_at.desc())
        result = await session.execute(stmt)
        projects = result.scalars().all()

        response = []
        for project in projects:
            # Character Studio's hidden system project never shows in the UI
            if (project.settings or {}).get("studio_system"):
                continue
            # Count scenes and assets
            scenes_stmt = select(Scene).where(Scene.project_id == project.id)
            scenes_result = await session.execute(scenes_stmt)
            scenes_count = len(scenes_result.scalars().all())

            assets_stmt = select(Asset).where(Asset.project_id == project.id)
            assets_result = await session.execute(assets_stmt)
            assets_count = len(assets_result.scalars().all())

            response.append(
                ProjectResponse(
                    id=project.id,
                    name=project.name,
                    mode=project.mode,
                    created_at=project.created_at,
                    updated_at=project.updated_at,
                    settings=project.settings,
                    scenes_count=scenes_count,
                    assets_count=assets_count,
                )
            )

        return response
    except Exception as e:
        logger.error(f"Error listing projects: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to list projects",
        )


@router.get(
    "/{project_id}",
    response_model=ProjectResponse,
    summary="Get project details",
)
async def get_project(
    project_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> ProjectResponse:
    """Get detailed project information.

    Args:
        project_id: UUID of the project.
        session: Database session.

    Returns:
        Project details with scene and asset counts.

    Raises:
        HTTPException: If project not found.
    """
    try:
        project = await session.get(Project, project_id)
        if not project:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Project {project_id} not found",
            )

        # Count scenes and assets
        scenes_stmt = select(Scene).where(Scene.project_id == project_id)
        scenes_result = await session.execute(scenes_stmt)
        scenes_count = len(scenes_result.scalars().all())

        assets_stmt = select(Asset).where(Asset.project_id == project_id)
        assets_result = await session.execute(assets_stmt)
        assets_count = len(assets_result.scalars().all())

        return ProjectResponse(
            id=project.id,
            name=project.name,
            mode=project.mode,
            created_at=project.created_at,
            updated_at=project.updated_at,
            settings=project.settings,
            scenes_count=scenes_count,
            assets_count=assets_count,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting project {project_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get project",
        )


@router.put(
    "/{project_id}",
    response_model=ProjectResponse,
    summary="Update project",
)
async def update_project(
    project_id: UUID,
    req: ProjectUpdate,
    session: AsyncSession = Depends(get_session),
) -> ProjectResponse:
    """Update project name and/or settings.

    Args:
        project_id: UUID of the project.
        req: Update request with optional name and settings.
        session: Database session.

    Returns:
        Updated project.

    Raises:
        HTTPException: If project not found.
    """
    try:
        project = await session.get(Project, project_id)
        if not project:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Project {project_id} not found",
            )

        if req.name:
            project.name = req.name
        if req.settings is not None:
            project.settings = req.settings

        # Bump updated_at so list_projects (which sorts by updated_at DESC)
        # reflects the edit.
        from datetime import datetime as _dt
        project.updated_at = _dt.utcnow()

        await session.commit()
        await session.refresh(project)

        logger.info(f"Updated project {project_id}")

        # Recount scenes and assets
        scenes_stmt = select(Scene).where(Scene.project_id == project_id)
        scenes_result = await session.execute(scenes_stmt)
        scenes_count = len(scenes_result.scalars().all())

        assets_stmt = select(Asset).where(Asset.project_id == project_id)
        assets_result = await session.execute(assets_stmt)
        assets_count = len(assets_result.scalars().all())

        return ProjectResponse(
            id=project.id,
            name=project.name,
            mode=project.mode,
            created_at=project.created_at,
            updated_at=project.updated_at,
            settings=project.settings,
            scenes_count=scenes_count,
            assets_count=assets_count,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating project {project_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update project",
        )


@router.delete(
    "/{project_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete project",
)
async def delete_project(
    project_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> None:
    """Delete a project and all associated files.

    Removes the project from the database and deletes its directory tree.

    Args:
        project_id: UUID of the project.
        session: Database session.

    Raises:
        HTTPException: If project not found.
    """
    try:
        project = await session.get(Project, project_id)
        if not project:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Project {project_id} not found",
            )

        # Pre-null Global Character Library FK references to this project.
        # The model declares ondelete="SET NULL" but `metadata.create_all`
        # doesn't ALTER existing tables — DBs created before that fix have
        # the constraint without SET NULL behavior and would raise an
        # IntegrityError (https://sqlalche.me/e/20/gkpj) on cascade.  We
        # do this manually so deletion works on every schema variant.
        # The cached `source_project_name` on each library row preserves
        # attribution after the project is gone (matches the "Copy
        # semantics — library entry outlives source project" design).
        try:
            from backend.database.models import GlobalCharacter as _GCDel
            from sqlmodel import select as _gc_select
            _gc_stmt = _gc_select(_GCDel).where(_GCDel.source_project_id == project_id)
            _gc_result = await session.execute(_gc_stmt)
            _gc_rows = list(_gc_result.scalars().all())
            for _gc in _gc_rows:
                _gc.source_project_id = None
            if _gc_rows:
                await session.commit()
                logger.info(
                    f"Nulled source_project_id on {len(_gc_rows)} GlobalCharacter "
                    f"row(s) referencing project {project_id} before cascade delete"
                )
        except Exception as _gc_err:
            # Non-fatal: log and proceed.  If the FK was already SET NULL
            # by schema, the cascade handles it.  If it wasn't and the
            # main delete still fails, the outer except will catch it.
            logger.warning(f"GlobalCharacter pre-null on project delete failed: {_gc_err}")

        # FK pre-clean: with PRAGMA foreign_keys=ON, ORM cascade order is not
        # guaranteed for the chapters SELF-FK (parent_chapter_id) and the
        # scenes->chapters FK.  Deep chapter trees (AAF imports auto-split
        # into parent+sub chapters) made project deletion raise
        # IntegrityError mid-cascade.  NULL both reference chains first.
        # ⚠⚠⚠ EACH STATEMENT IS GUARDED SEPARATELY, and that is the whole
        # lesson here. The first version ran them in ONE try/except, so a
        # single `no such column` aborted the block and every LATER statement
        # was silently skipped — the log said "pre-clean failed (continuing)",
        # the delete then failed for a completely different reason, and the
        # message pointed at the wrong table. **A best-effort cleanup loop must
        # be best-effort PER STEP, or the first failure hides the rest.**
        from sqlalchemy import text as _del_text
        _pid_hex = project_id.hex
        _steps = [
            # (1) the two self/cross FKs the ORM cascade cannot order safely
            ("UPDATE scenes SET chapter_id = NULL WHERE project_id = :pid", {}),
            ("UPDATE chapters SET parent_chapter_id = NULL WHERE project_id = :pid", {}),
            # (2) two tables point at SCENES and broke deletion for any project
            # with SLICED per-scene audio — every AAF import, every
            # chapter-built project. `timeline_positions` references BOTH
            # scenes and assets, so whichever the ORM cascades first, the other
            # FK fails mid-transaction. Per-scene rows, project going away →
            # delete outright, nothing survives to orphan.
            ("DELETE FROM timeline_positions WHERE scene_id IN "
             "(SELECT id FROM scenes WHERE project_id = :pid)", {}),
            ("DELETE FROM stem_selections WHERE scene_id IN "
             "(SELECT id FROM scenes WHERE project_id = :pid)", {}),
            # (3) ⚠⚠ `shortcode_counters` has a `projects.id` FK and **no
            # relationship on `Project`**, so the ORM never cascades it and
            # `DELETE FROM projects` ITSELF raises. `allocate_shortcode` writes
            # a row the first time any chapter or asset gets a code — so a
            # project that has ever had chapters could not be deleted AT ALL.
            # Not a new bug; merely unreachable until this lane made deleting
            # a freshly-built project routine.
            ("DELETE FROM shortcode_counters WHERE project_id = :pid", {}),
            # (4) ⭐ `global_characters` is a LIBRARY that deliberately outlives
            # the project it was saved from (copy semantics — see the model's
            # docstring). Its column is `source_project_id`, and the right move
            # is to NULL the provenance, never to delete his saved characters.
            ("UPDATE global_characters SET source_project_id = NULL "
             "WHERE source_project_id = :pid", {}),
        ]
        for _sql, _extra in _steps:
            try:
                await session.execute(_del_text(_sql), {"pid": _pid_hex, **_extra})
            except Exception as _step_err:                       # noqa: BLE001
                logger.warning("Project-delete pre-clean step skipped (%s): %s",
                               _sql.split(" WHERE")[0][:60], _step_err)
                try:
                    await session.rollback()
                except Exception:                                # noqa: BLE001
                    pass
        try:
            await session.commit()
        except Exception as _fk_err:                             # noqa: BLE001
            logger.warning(f"Project-delete FK pre-clean commit failed "
                           f"(continuing): {_fk_err}")
            try:
                await session.rollback()
            except Exception:                                    # noqa: BLE001
                pass

        # Delete from database (cascade deletes scenes, assets, jobs, etc.)
        await session.delete(project)
        await session.commit()

        # Delete project directory — NON-FATAL: a locked file (Windows +
        # an ffmpeg/preview handle) must not resurrect a half-deleted
        # project in the UI.  Leftover folders can be removed manually.
        project_path = settings.project_dir / str(project_id)
        if project_path.exists():
            try:
                shutil.rmtree(project_path)
                logger.info(f"Deleted project directory: {project_path}")
            except Exception as _rm_err:
                logger.warning(
                    f"Project DB row deleted but directory removal failed "
                    f"(locked file?): {project_path} — {_rm_err}. "
                    f"Delete the folder manually."
                )

        logger.info(f"Deleted project {project_id}")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting project {project_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to delete project: {type(e).__name__}: {e}",
        )


@router.post(
    "/{project_id}/duplicate",
    response_model=ProjectResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Duplicate a project",
)
async def duplicate_project(
    project_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> ProjectResponse:
    """Duplicate an existing project with all its scenes and assets.

    Creates a new project with the same settings, scenes, and copies all assets.

    Args:
        project_id: UUID of the project to duplicate.
        session: Database session.

    Returns:
        Newly created duplicate project.

    Raises:
        HTTPException: If source project not found.
    """
    try:
        # Get source project
        source_project = await session.get(Project, project_id)
        if not source_project:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Project {project_id} not found",
            )

        # Create new project
        new_project = Project(
            name=f"{source_project.name} (Copy)",
            mode=source_project.mode,
            settings=source_project.settings.copy() if source_project.settings else {},
        )
        session.add(new_project)
        await session.flush()

        # Create project directories
        new_project_path = settings.project_dir / str(new_project.id)
        new_project_path.mkdir(parents=True, exist_ok=True)
        ensure_project_dirs(new_project_path)

        # Copy source project directory structure and assets
        source_project_path = settings.project_dir / str(source_project.id)
        if source_project_path.exists():
            for item in source_project_path.iterdir():
                if item.name not in ["cache", "backups"]:  # Skip cache and backups
                    if item.is_dir():
                        shutil.copytree(item, new_project_path / item.name, dirs_exist_ok=True)
                    else:
                        shutil.copy2(item, new_project_path / item.name)

        await session.commit()
        await session.refresh(new_project)

        logger.info(f"Duplicated project {project_id} -> {new_project.id}")

        return ProjectResponse(
            id=new_project.id,
            name=new_project.name,
            mode=new_project.mode,
            created_at=new_project.created_at,
            updated_at=new_project.updated_at,
            settings=new_project.settings,
            scenes_count=0,
            assets_count=0,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error duplicating project {project_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to duplicate project",
        )


# ── Convert Narration Images → Narration Video ────────────────────────
#
# The legacy /duplicate endpoint above is a shell: it copies the on-disk
# directory and the Project row but leaves every other table empty, so the
# resulting project appears blank in the UI.  Convert-to-narration-video
# needs the FULL picture (scenes, chapters, lyrics, assets, etc.) so the
# user can pick up where they left off and start generating videos.

@router.post(
    "/{project_id}/convert-to-narration-video",
    response_model=ProjectResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Duplicate a narration_images project as a new narration_video project",
)
async def convert_to_narration_video(
    project_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> ProjectResponse:
    """Convert a narration_images project to a new narration_video project."""
    from backend.database.models import (
        Chapter, Lyrics, SongSection, StemSelection,
        TimelinePosition, BackingTrack,
    )
    from backend.services.shortcode import allocate_shortcode

    try:
        source_project = await session.get(Project, project_id)
        if not source_project:
            raise HTTPException(status_code=404, detail=f"Project {project_id} not found")
        if source_project.mode != ProjectMode.NARRATION_IMAGES:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Convert-to-narration-video only works on Narration "
                    f"Images projects (this project is "
                    f"{source_project.mode.value})."
                ),
            )

        new_project = Project(
            name=f"{source_project.name} (Video)",
            mode=ProjectMode.NARRATION_VIDEO,
            settings=(source_project.settings or {}).copy(),
        )
        session.add(new_project)
        await session.flush()

        new_project_path = settings.project_dir / str(new_project.id)
        new_project_path.mkdir(parents=True, exist_ok=True)
        ensure_project_dirs(new_project_path)

        source_project_path = settings.project_dir / str(source_project.id)
        if source_project_path.exists():
            for item in source_project_path.iterdir():
                if item.name in ("cache", "backups", ".export_cache"):
                    continue
                if item.is_dir():
                    shutil.copytree(item, new_project_path / item.name, dirs_exist_ok=True)
                else:
                    shutil.copy2(item, new_project_path / item.name)

        lyr_stmt = select(Lyrics).where(Lyrics.project_id == project_id)
        lyr = (await session.execute(lyr_stmt)).scalars().first()
        if lyr:
            session.add(Lyrics(
                project_id=new_project.id,
                full_text=lyr.full_text,
                initial_text=getattr(lyr, "initial_text", "") or "",
                words=list(lyr.words or []),
                language=getattr(lyr, "language", None),
            ))
            await session.flush()

        asset_map: dict = {}
        a_stmt = select(Asset).where(Asset.project_id == project_id)
        for a in (await session.execute(a_stmt)).scalars().all():
            new_short = await allocate_shortcode(session, new_project.id, _asset_type_code(a.asset_type))
            new_a = Asset(
                project_id=new_project.id,
                asset_type=a.asset_type,
                rel_path=a.rel_path,
                sha256=a.sha256,
                short_code=new_short,
                tags=list(a.tags or []),
                meta=dict(a.meta or {}),
            )
            session.add(new_a)
            await session.flush()
            asset_map[a.id] = new_a.id

        chapter_map: dict = {}
        ch_stmt = select(Chapter).where(Chapter.project_id == project_id).order_by(Chapter.depth)
        for ch in (await session.execute(ch_stmt)).scalars().all():
            new_parent = chapter_map.get(ch.parent_chapter_id) if ch.parent_chapter_id else None
            new_short = await allocate_shortcode(session, new_project.id, "ch")
            new_ch = Chapter(
                project_id=new_project.id,
                parent_chapter_id=new_parent,
                name=ch.name,
                short_code=new_short,
                color=ch.color,
                tags=list(ch.tags or []),
                description=getattr(ch, "description", "") or "",
                character_focus=list(getattr(ch, "character_focus", []) or []),
                style_notes=getattr(ch, "style_notes", "") or "",
                source=getattr(ch, "source", "auto"),
                depth=ch.depth,
                start_time=ch.start_time,
                end_time=ch.end_time,
            )
            session.add(new_ch)
            await session.flush()
            chapter_map[ch.id] = new_ch.id

        scene_map: dict = {}
        s_stmt = select(Scene).where(Scene.project_id == project_id).order_by(Scene.order_index)
        for sc in (await session.execute(s_stmt)).scalars().all():
            new_params = _remap_asset_ids(dict(sc.parameters or {}), asset_map)
            new_short = await allocate_shortcode(session, new_project.id, "sce")
            new_sc = Scene(
                project_id=new_project.id,
                chapter_id=chapter_map.get(sc.chapter_id) if sc.chapter_id else None,
                name=sc.name,
                short_code=new_short,
                order_index=sc.order_index,
                start_time=sc.start_time,
                end_time=sc.end_time,
                prompt=sc.prompt or "",
                negative_prompt=sc.negative_prompt or "",
                parameters=new_params,
                workflow_snapshot=dict(sc.workflow_snapshot or {}),
            )
            session.add(new_sc)
            await session.flush()
            scene_map[sc.id] = new_sc.id

        # SongSection (project-scoped)
        ss_stmt = select(SongSection).where(SongSection.project_id == project_id)
        for row in (await session.execute(ss_stmt)).scalars().all():
            kwargs = {c.name: getattr(row, c.name) for c in row.__table__.columns if c.name != "id"}
            kwargs["project_id"] = new_project.id
            kwargs.pop("created_at", None)
            kwargs.pop("updated_at", None)
            session.add(SongSection(**kwargs))

        # BackingTrack (project-scoped)
        bt_stmt = select(BackingTrack).where(BackingTrack.project_id == project_id)
        for row in (await session.execute(bt_stmt)).scalars().all():
            kwargs = {c.name: getattr(row, c.name) for c in row.__table__.columns if c.name != "id"}
            kwargs["project_id"] = new_project.id
            kwargs.pop("created_at", None)
            kwargs.pop("updated_at", None)
            session.add(BackingTrack(**kwargs))

        # StemSelection (scene-scoped)
        if scene_map:
            stems_stmt = select(StemSelection).where(
                StemSelection.scene_id.in_(list(scene_map.keys()))
            )
            for row in (await session.execute(stems_stmt)).scalars().all():
                kwargs = {c.name: getattr(row, c.name) for c in row.__table__.columns if c.name != "id"}
                new_scene = scene_map.get(kwargs.get("scene_id"))
                if not new_scene:
                    continue
                kwargs["scene_id"] = new_scene
                kwargs.pop("created_at", None)
                kwargs.pop("updated_at", None)
                session.add(StemSelection(**kwargs))

        # TimelinePosition (scene+asset scoped)
        if scene_map:
            tp_stmt = select(TimelinePosition).where(
                TimelinePosition.scene_id.in_(list(scene_map.keys()))
            )
            for tp in (await session.execute(tp_stmt)).scalars().all():
                new_scene = scene_map.get(tp.scene_id)
                if not new_scene:
                    continue
                new_asset = asset_map.get(tp.asset_id) if tp.asset_id else None
                session.add(TimelinePosition(
                    scene_id=new_scene,
                    asset_id=new_asset,
                    start_time=tp.start_time,
                    end_time=tp.end_time,
                ))

        await session.commit()
        await session.refresh(new_project)

        logger.info(
            f"Converted narration_images project {project_id} -> narration_video "
            f"{new_project.id} (chapters={len(chapter_map)}, scenes={len(scene_map)}, "
            f"assets={len(asset_map)})"
        )

        return ProjectResponse(
            id=new_project.id,
            name=new_project.name,
            mode=new_project.mode,
            created_at=new_project.created_at,
            updated_at=new_project.updated_at,
            settings=new_project.settings,
            scenes_count=len(scene_map),
            assets_count=len(asset_map),
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Convert-to-narration-video failed for {project_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Convert failed: {e}")


def _asset_type_code(asset_type) -> str:
    s = str(asset_type.value if hasattr(asset_type, "value") else asset_type).lower()
    if "video" in s:
        return "vid"
    if "audio" in s or "music" in s:
        return "aud"
    return "img"


def _remap_asset_ids(params: dict, asset_map: dict) -> dict:
    """Deep-walk a scene.parameters dict and remap any UUID strings that
    appear in the asset old->new map."""
    if not asset_map:
        return params
    str_map = {str(k): str(v) for k, v in asset_map.items()}
    def walk(obj):
        if isinstance(obj, dict):
            return {k: walk(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [walk(x) for x in obj]
        if isinstance(obj, str) and obj in str_map:
            return str_map[obj]
        return obj
    return walk(params)



# ── Project Text Data Export / Import ─────────────────────────────────
#
# Two endpoints that wrap backend/services/project_text_io.py.  They
# expose all editable text data (concept, characters, chapters, scenes,
# prompts, story flow, transitions) as a single JSON payload suitable
# for handing to an AI agent.  See public/docs/*.md for the LLM-facing
# instructions.

from pydantic import BaseModel as _PD_BaseModel
from typing import Literal as _PD_Literal


class TextImportRequest(_PD_BaseModel):
    """Payload for POST /projects/{id}/text-import."""
    json_payload: dict
    import_mode: _PD_Literal["override", "fill_missing"] = "fill_missing"
    accept_mode_mismatch: bool = False


@router.get(
    "/{project_id}/text-export",
    summary="Export project text data as JSON",
)
async def text_export(
    project_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Build the canonical text-data export for a project."""
    from backend.services.project_text_io import build_export
    project = await session.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail=f"Project {project_id} not found")
    return await build_export(project, session)


@router.post(
    "/{project_id}/text-import",
    summary="Apply a text-data JSON payload to a project",
)
async def text_import(
    project_id: UUID,
    req: TextImportRequest,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Apply an import payload.  See project_text_io.apply_import."""
    from backend.services.project_text_io import apply_import, ImportError as _ImpErr
    project = await session.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail=f"Project {project_id} not found")
    try:
        stats = await apply_import(
            project, session,
            req.json_payload,
            mode=req.import_mode,
            accept_mode_mismatch=req.accept_mode_mismatch,
        )
        return {"ok": True, "stats": stats}
    except _ImpErr as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Text import failed for {project_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Import failed: {e}")


class TalkieConfigIn(BaseModel):
    """Set the Talkie project's single source portrait + lip-sync engine."""
    portrait_asset_id: Optional[str] = None
    talkie_engine: Optional[str] = None


_TALKIE_ENGINES = ("lipsync_ltx", "lipsync_latentsync", "lipsync_musetalk", "lipsync_sonic")


@router.put("/{project_id}/talkie-config")
async def set_talkie_config(
    project_id: UUID,
    req: TalkieConfigIn,
    session: AsyncSession = Depends(get_session),
):
    """Merge the Talkie portrait_asset_id / talkie_engine into project.settings."""
    project = await session.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    st = dict(project.settings or {})
    if req.portrait_asset_id is not None:
        st["portrait_asset_id"] = req.portrait_asset_id or None
    if req.talkie_engine is not None:
        if req.talkie_engine not in _TALKIE_ENGINES:
            raise HTTPException(status_code=400, detail=f"engine must be one of {_TALKIE_ENGINES}")
        st["talkie_engine"] = req.talkie_engine

    project.settings = st
    project.updated_at = datetime.utcnow()
    await session.commit()
    return {
        "portrait_asset_id": st.get("portrait_asset_id"),
        "talkie_engine": st.get("talkie_engine", "lipsync_ltx"),
    }


# ══ 🎬 per-project VIDEO ENGINE (v1.277.12) ═════════════════════════════════
# LTX 2.3 is how everything worked before and stays the default. minimax_h3
# routes the project's video jobs through the H3 lane in the dispatcher.
# ltx_2.5 is STAGED (models on the workers, graphs pending its first live
# export) — selectable so projects can opt in the moment it goes live.
_VIDEO_ENGINES = ("ltx_2.3", "ltx_2.5", "minimax_h3")
_H3_AUDIO_MODES = ("project", "model")   # project = mux our narration/music
                                         # model   = keep H3's generated audio


class VideoConfigIn(BaseModel):
    """Merge per-project video-engine settings (the talkie-config pattern)."""
    video_engine: Optional[str] = None
    h3_turbo: Optional[bool] = None          # 8-step turbo lora (default on)
    h3_draft: Optional[bool] = None          # 4-step testing mode
    h3_audio_mode: Optional[str] = None      # project | model
    h3_use_audio_ref: Optional[bool] = None  # feed the scene's audio slice as
                                             # an H3 audio REFERENCE (ref2v)
    h3_ref_image_size: Optional[str] = None  # match | max
    h3_auto_sheet_refs: Optional[bool] = None  # auto-attach outfit sheets of
                                               # present characters as refs
    # 🎛 the project-wide default for how a scene carries identity — a scene
    # may override it. See backend/services/scene_ref_mode.py; it lives here
    # rather than in its own route because the Concept tab already reads this
    # one config object, and a second endpoint would be a second round trip
    # for a field that is edited beside the engine picker.
    scene_ref_mode: Optional[str] = None      # t2i_swap | full_reference


@router.put("/{project_id}/video-config")
async def set_video_config(
    project_id: UUID,
    req: VideoConfigIn,
    session: AsyncSession = Depends(get_session),
):
    project = await session.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    st = dict(project.settings or {})
    # a pure READ ({}) must not rewrite the row / bump updated_at
    if all(getattr(req, k) is None for k in
           ("video_engine", "h3_turbo", "h3_draft", "h3_audio_mode",
            "h3_use_audio_ref", "h3_ref_image_size", "h3_auto_sheet_refs",
            "scene_ref_mode")):
        return {"video_engine": st.get("video_engine", "ltx_2.3"),
                "scene_ref_mode": scene_ref_mode.project_mode(st),
                **{k: st.get(k) for k in ("h3_turbo", "h3_draft",
                                          "h3_audio_mode", "h3_use_audio_ref",
                                          "h3_ref_image_size",
                                          "h3_auto_sheet_refs")}}
    if req.video_engine is not None:
        if req.video_engine not in _VIDEO_ENGINES:
            raise HTTPException(400, f"video_engine must be one of {_VIDEO_ENGINES}")
        st["video_engine"] = req.video_engine
    if req.h3_audio_mode is not None:
        if req.h3_audio_mode not in _H3_AUDIO_MODES:
            raise HTTPException(400, f"h3_audio_mode must be one of {_H3_AUDIO_MODES}")
        st["h3_audio_mode"] = req.h3_audio_mode
    if req.h3_ref_image_size is not None:
        if req.h3_ref_image_size not in ("match", "max"):
            raise HTTPException(400, "h3_ref_image_size must be match or max")
        st["h3_ref_image_size"] = req.h3_ref_image_size
    if req.scene_ref_mode is not None:
        # ⚠ the project default may NOT be 'inherit' — there is nothing above
        # it to inherit from, and storing it would make every scene fall back
        # to the module default while the UI showed the user's choice.
        _m = scene_ref_mode.normalise(req.scene_ref_mode)
        if not _m:
            raise HTTPException(
                400, f"scene_ref_mode must be one of {scene_ref_mode.MODES}")
        st["scene_ref_mode"] = _m
    for k in ("h3_turbo", "h3_draft", "h3_use_audio_ref", "h3_auto_sheet_refs"):
        v = getattr(req, k)
        if v is not None:
            st[k] = bool(v)

    project.settings = st
    project.updated_at = datetime.utcnow()
    await session.commit()
    return {"video_engine": st.get("video_engine", "ltx_2.3"),
            "scene_ref_mode": scene_ref_mode.project_mode(st),
            **{k: st.get(k) for k in ("h3_turbo", "h3_draft", "h3_audio_mode",
                                      "h3_use_audio_ref", "h3_ref_image_size",
                                      "h3_auto_sheet_refs")}}


# ══ 🌍 story/world ↔ project link (v1.277.12) ═══════════════════════════════
class StoryLinkIn(BaseModel):
    world_id: Optional[str] = None       # '' or None with attach=False detaches
    story_id: Optional[str] = None       # optional story inside the world
    # 📖 v1.277.46 — and optionally ONE CHAPTER of that story. His call:
    # "a chapter will essentially be a single video project." Selecting one
    # narrows the pull (that chapter's narration, recording and beats) and
    # narrows the derived concept text to that chapter. Absent = story-wide,
    # exactly as before.
    chapter_id: Optional[str] = None
    attach: bool = True


@router.put("/{project_id}/story-link")
async def set_story_link(
    project_id: UUID,
    req: StoryLinkIn,
    session: AsyncSession = Depends(get_session),
):
    """Link a project to a Story/World Builder world (+ optional story).
    TWO-WAY: writes project.settings AND the world's project_ids, so both
    screens can show the link and jump across."""
    from backend.api import storyworld as sw
    project = await session.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    st = dict(project.settings or {})
    old_wid = str(st.get("world_id") or "")
    pid_s = str(project_id)

    if req.attach:
        wid = (req.world_id or "").strip()
        if not wid:
            raise HTTPException(400, "world_id is required to attach")
        with sw._LOCK:
            w = sw._load(wid)               # 404s if missing
            if req.story_id:
                story = sw._find(w.get("stories") or [], req.story_id, "story")
                if req.chapter_id:
                    sw._find(story.get("chapters") or [], req.chapter_id,
                             "chapter")
            elif req.chapter_id:
                raise HTTPException(400, "a chapter needs its story — send "
                                         "story_id with chapter_id")
            ids = [str(x) for x in (w.get("project_ids") or [])]
            if pid_s not in ids:
                ids.append(pid_s)
            w["project_ids"] = ids
            sw._save(w)
        # detach from a previously-linked different world
        if old_wid and old_wid != wid:
            try:
                with sw._LOCK:
                    ow = sw._load(old_wid)
                    ow["project_ids"] = [x for x in (ow.get("project_ids") or [])
                                         if str(x) != pid_s]
                    sw._save(ow)
            except HTTPException:
                pass
        st["world_id"] = wid
        st["story_id"] = (req.story_id or "").strip()
        # ⚠ Changing the STORY must clear a chapter that belonged to the old
        # one, or the project keeps a chapter_id that resolves to nothing and
        # story_context silently falls back to story-wide with no sign why.
        st["chapter_id"] = ((req.chapter_id or "").strip()
                            if st["story_id"] else "")
        if not st["chapter_id"]:
            st.pop("chapter_id", None)
    else:
        if old_wid:
            try:
                with sw._LOCK:
                    ow = sw._load(old_wid)
                    ow["project_ids"] = [x for x in (ow.get("project_ids") or [])
                                         if str(x) != pid_s]
                    sw._save(ow)
            except HTTPException:
                pass
        st.pop("world_id", None)
        st.pop("story_id", None)
        st.pop("chapter_id", None)


    project.settings = st
    project.updated_at = datetime.utcnow()
    await session.commit()
    return {"world_id": st.get("world_id"), "story_id": st.get("story_id"),
            "chapter_id": st.get("chapter_id") or ""}


@router.get("/{project_id}/story-link")
async def get_story_link(project_id: UUID,
                         session: AsyncSession = Depends(get_session)):
    """The linked world/story, resolved to names for the project header."""
    from backend.api import storyworld as sw
    project = await session.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    st = project.settings or {}
    wid = str(st.get("world_id") or "")
    if not wid:
        return {"linked": False}
    try:
        w = sw._load(wid)
    except HTTPException:
        return {"linked": False, "stale_world_id": wid}
    from backend.api import storychapters as _sch
    sid = str(st.get("story_id") or "")
    cid = str(st.get("chapter_id") or "")
    story = next((s for s in (w.get("stories") or []) if s["id"] == sid), None)
    chapters = (story or {}).get("chapters") or []
    chapter = next((c for c in chapters if c.get("id") == cid), None)
    return {"linked": True, "world_id": wid, "world_name": w.get("name"),
            "story_id": sid or None,
            "story_title": (story or {}).get("title"),
            "chapter_id": cid or None,
            "chapter_title": (chapter or {}).get("title"),
            # ⚠ A chapter_id that no longer resolves is REPORTED, not swallowed:
            # the pull would quietly fall back to the whole story otherwise.
            "chapter_missing": bool(cid and not chapter),
            # ⭐ the SHARED picker shape — same function the `?brief=1` chapter
            # list uses, so the dropdown can never get two different objects
            # depending on which screen filled it.
            "chapters": [_sch.chapter_row(c) for c in chapters],
            "style_text": sw._style_text(w),
            "cast": [{"id": c["id"], "name": c["name"],
                      "char_slug": c.get("char_slug") or "",
                      "status": c.get("status")}
                     for c in (w.get("cast") or [])],
            "texts": [{"id": t["id"], "kind": t.get("kind"),
                       "title": t.get("title"), "story_id": t.get("story_id")}
                      for t in (w.get("texts") or [])]}


class PullFromStoryIn(BaseModel):
    """Which parts of the linked world/story to pull into the project.

    ⚠ `concept`/`style` are COPY semantics and are now OPTIONAL: a linked
    project DERIVES those two live (see `services/story_context.py`), so the
    copy is only for people who want to detach and edit.

    📖 v1.277.46 — the SCOPE comes from the link, not from here. If the link
    names a chapter (`settings["chapter_id"]`) every switch below reads that
    chapter's material: its narration becomes the script, its recording becomes
    the audio, its BEATS become the timeline chapters, and its named cast
    narrows the character pull. There is no `chapter` flag on purpose — a
    project that is a chapter's video is that all the way down, and a per-part
    override would let it be half one thing and half another."""
    concept: bool = False         # copy story text → concept_text (legacy)
    style: bool = False           # copy world style → style_text (legacy)
    characters: bool = True       # story-scoped cast → project characters
    lyrics_text_id: str = ""      # a world text id → Lyrics.initial_text
    chapters: bool = True         # story ARCS → chapters (timed to sections)
    narration_audio: bool = True  # the story's audio/aaf/srt → project assets
    narration_text: bool = True   # the story's narration → the script (spoken only)


class StoryOverrideIn(BaseModel):
    """Pin ONE field against the linked story (or release it with "")."""
    field: str
    value: str = ""


@router.get("/{project_id}/story-context")
async def get_story_context(project_id: UUID,
                            session: AsyncSession = Depends(get_session)):
    """🌍 What this project's creative direction RESOLVES to right now.

    The Concept tab reads this: when `linked` is true it shows the story's
    values (greyed, with a 'from <story>' badge) instead of the project's own,
    and `overrides` says which fields the user has pinned. Nothing is copied —
    edit the world and the project follows."""
    from backend.services import story_context as sc
    project = await session.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    st = dict(project.settings or {})
    ctx = sc.resolve(st)
    eff = sc.effective(st, ctx)
    # what the project itself holds, so the UI can show what unlinking restores
    own = {k: st.get(k) or "" for k in sc.OVERRIDABLE}
    return {"linked": ctx["linked"], "world": ctx["world"],
            "story": ctx["story"], "arcs": ctx["arcs"],
            # 📖 v1.277.46 — the chapter scope, forwarded rather than left in
            # the resolver. ⚠ `arcs` above is the CHAPTER'S BEATS when one is
            # selected; a reader that does not know which it is holding will
            # label the wrong rung, so `arcs_are_beats` travels with it.
            # `chapter_missing` says a linked chapter has been deleted — the
            # resolver widens to the whole story and this is the only signal.
            "chapter": ctx.get("chapter"),
            "arcs_are_beats": bool(ctx.get("arcs_are_beats")),
            "chapter_missing": bool(ctx.get("chapter_missing")),
            "cast": [{"id": m.get("id"), "name": m.get("name"),
                      "char_slug": m.get("char_slug") or "",
                      "role": m.get("role") or "",
                      "status": m.get("status") or "paper"}
                     for m in (ctx["characters"] or [])],
            "derived": {k: ctx.get(k, "") for k in ("concept_text", "style_text")},
            "effective": eff, "own": own, "overrides": ctx["overrides"]}


@router.put("/{project_id}/story-override")
async def set_story_override(project_id: UUID, req: StoryOverrideIn,
                             session: AsyncSession = Depends(get_session)):
    """✏ Pin one field so the story stops driving it — or release it.

    ⚠ Stored in its OWN key (`story_overrides`), never by writing into
    `concept_text`: a value written into the concept keys is indistinguishable
    from a value the story derived, and then nobody can tell what unlinking
    would restore."""
    from backend.services import story_context as sc
    if req.field not in sc.OVERRIDABLE:
        raise HTTPException(400, f"field must be one of {list(sc.OVERRIDABLE)}")
    project = await session.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    st = dict(project.settings or {})
    ov = dict(st.get("story_overrides") or {})
    if (req.value or "").strip():
        ov[req.field] = req.value
    else:
        ov.pop(req.field, None)
    st["story_overrides"] = ov
    project.settings = st
    project.updated_at = datetime.utcnow()
    await session.commit()
    return {"overrides": ov}


@router.post("/{project_id}/pull-from-story")
async def pull_from_story(
    project_id: UUID,
    req: PullFromStoryIn,
    session: AsyncSession = Depends(get_session),
):
    """Copy story/world material into the project — his flow: 'build out most
    of the information in story mode before making our videos'. COPY semantics
    (the global-character-library rule): the project's copy is independent."""
    from backend.api import storyworld as sw
    project = await session.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    st = dict(project.settings or {})
    wid = str(st.get("world_id") or "")
    if not wid:
        raise HTTPException(400, "link a world first (PUT story-link)")
    w = sw._load(wid)
    sid = str(st.get("story_id") or "")
    story = next((s for s in (w.get("stories") or []) if s["id"] == sid), None)
    # 📖 v1.277.46 — the link may name ONE CHAPTER, and a chapter is one video.
    # Every branch below prefers the chapter's material and falls back to the
    # story's, so an unscoped project behaves exactly as it did before.
    # ⚠ A chapter_id that no longer resolves is reported LOUDLY rather than
    # silently widening to the whole story — that is a 40-minute video where a
    # 4-minute one was asked for.
    cid = str(st.get("chapter_id") or "")
    chapter = next((c for c in ((story or {}).get("chapters") or [])
                    if c.get("id") == cid), None)
    pulled: list = []
    if cid and not chapter:
        pulled.append(f"⚠ the linked chapter {cid} no longer exists on this "
                      f"story — pulling the WHOLE story instead")
    if chapter:
        pulled.append(f"scoped to chapter {int(chapter.get('i') or 0) + 1}: "
                      f"{chapter.get('title')}")

    if req.concept:
        bits = []
        ws = w.get("world") or {}
        if ws.get("logline"):
            bits.append(f"World: {ws['logline']}")
        if story:
            sf = story.get("fields") or {}
            for k in ("logline", "synopsis", "beats"):
                if sf.get(k):
                    bits.append(f"{k.capitalize()}: {sf[k]}")
        if chapter:
            bits.append(f"This video is chapter "
                        f"{int(chapter.get('i') or 0) + 1}: {chapter.get('title')}")
            if chapter.get("summary"):
                bits.append(f"Chapter: {chapter['summary']}")
            if chapter.get("mood"):
                bits.append(f"Mood: {chapter['mood']}")
        if bits:
            st["concept_text"] = "\n\n".join(bits)
            # ⭐ the CHAPTER names the project — the story title would put the
            # same name on every video in the series.
            st["song_title"] = (st.get("song_title")
                                or (chapter or {}).get("title")
                                or (story or {}).get("title"))
            pulled.append("concept")

    if req.style:
        stx = sw._style_text(w)
        vs = (w.get("world") or {}).get("visual_style") or ""
        combined = ". ".join(x for x in (stx, vs) if x and x not in stx)
        if combined:
            st["style_text"] = combined
            pulled.append("style")

    if req.characters:
        # ⚠⚠ COPY THE DICTS, not just the list. `st = dict(project.settings)`
        # is shallow, so mutating a character dict mutates the object SQLAlchemy
        # loaded — and then the new value compares EQUAL to the old one, no
        # UPDATE is emitted, and the change vanishes on commit. Eight
        # "repaired image" lines were reported and none of them persisted
        # (2026-08-18). Caught by re-READING the project, not by the response.
        chars = [dict(c) for c in (st.get("characters") or [])]
        have = {str(c.get("name", "")).lower() for c in chars}
        n_added = 0
        # ⚠ repairs must be SAVED too. The first version only wrote the list
        # back `if n_added:` — so eight "repaired image" lines were reported
        # and not one of them persisted (2026-08-18, caught by re-reading the
        # project after the pull rather than trusting the response).
        n_repaired = 0
        # ⚠ v1.277.24 — the cast of THIS STORY, not of the whole world. A
        # world with three stories used to dump every character into every
        # project, which is exactly the "we don't necessarily need them all
        # available" complaint.
        from backend.services import story_context as _sc
        pid_prefix = f"{project_id}/"
        # 📖 a chapter that names its cast narrows it one rung further; a
        # chapter that names nobody inherits the story's cast rather than
        # pulling an empty list (the story_cast fallback, one level down).
        _cast = _sc.story_cast(w, sid)
        if chapter and (chapter.get("characters") or []):
            _want = {str(n).strip().lower() for n in chapter["characters"]}
            _cast = [m for m in _cast
                     if str(m.get("name") or "").strip().lower() in _want] or _cast
        for m in _cast:
            if m["name"].lower() in have:
                # ⚠ ALREADY HERE — but it may carry the pre-.31 broken
                # image_path (project-relative, so /api/files 404s and the card
                # shows a name with no face). Repair it in place rather than
                # making him delete and re-pull.
                cur = next((c for c in chars
                            if str(c.get("name", "")).lower() == m["name"].lower()), None)
                slug0 = (cur or {}).get("char_slug") or m.get("char_slug") or ""
                if cur is not None and slug0:
                    ip = str(cur.get("image_path") or "").replace("\\", "/")
                    if not ip or not ip.startswith(pid_prefix):
                        try:
                            rel = await _import_k3_base_as_asset(project, slug0,
                                                                 session)
                            if rel:
                                cur["image_path"] = rel
                                n_repaired += 1
                                pulled.append(f"repaired image: {m['name']}")
                        except Exception as e:                   # noqa: BLE001
                            logger.warning("pull: repair failed for %s: %s",
                                           slug0, e)
                    if not cur.get("description"):
                        cur["description"] = _sc_member_desc(m)
                        n_repaired += 1
                continue
            desc_bits = [m.get("role") or ""]
            f = m.get("fields") or {}
            desc_bits += [f"{k}: {v}" for k, v in f.items() if v]
            sr = (m.get("lore") or {}).get("story_role")
            if sr:
                desc_bits.append(sr)
            entry = {"name": m["name"],
                     "description": ". ".join(x for x in desc_bits if x)[:1500],
                     "image_path": "", "extra_images": [],
                     "source": "storyworld", "world_cast_id": m["id"],
                     "char_slug": m.get("char_slug") or ""}
            # import the generated character's active base as a project asset
            slug = m.get("char_slug") or ""
            if slug:
                try:
                    rel = await _import_k3_base_as_asset(project, slug, session)
                    if rel:
                        entry["image_path"] = rel
                except Exception as e:                       # noqa: BLE001
                    logger.warning("pull-from-story: base import failed for "
                                   "%s: %s", slug, e)
            chars.append(entry)
            have.add(m["name"].lower())
            n_added += 1
        if n_added or n_repaired:
            st["characters"] = chars
            if n_added:
                pulled.append(f"characters ({n_added})")

    if req.lyrics_text_id or (getattr(req, "narration_text", False) and story):
        src_label, raw_body = "", None
        if req.lyrics_text_id:
            t = next((x for x in (w.get("texts") or [])
                      if x["id"] == req.lyrics_text_id), None)
            if not t:
                raise HTTPException(404, "that text does not exist in the world")
            src_label, raw_body = t.get("title") or "text", t.get("body") or ""
        elif chapter and (chapter.get("narration") or "").strip():
            # ⭐ THE CHAPTER'S OWN NARRATION IS THE SCRIPT. This is the whole
            # point of the chapter lane: the project renders one chapter's
            # worth of words, not the whole book's.
            src_label = f"chapter — {chapter.get('title')}"
            raw_body = chapter["narration"]
        else:
            # no chapter (or an unwritten one): the STORY's narration, as before
            t = sw._story_narration(w, sid)
            if not t:
                raise HTTPException(
                    404, "this chapter has no narration yet — write it on the "
                         "Story tab (and the story has none either)"
                    if chapter else "this story has no narration text yet")
            src_label, raw_body = t.get("title") or "narration", t.get("body") or ""
        from backend.database.models import Lyrics
        r = await session.execute(
            select(Lyrics).where(Lyrics.project_id == project_id))
        ly = r.scalars().first()
        # ⚠ SPOKEN TEXT ONLY. The story's narration carries `## Arc` headers so
        # narration/chapters/beds share boundaries — but a script field feeds a
        # reader and Whisper alignment, and neither should ever see a heading.
        body_txt = sw.spoken_only(raw_body or "")
        if ly is None:
            ly = Lyrics(project_id=project_id, full_text="",
                        initial_text=body_txt)
            session.add(ly)
        else:
            ly.initial_text = body_txt
        pulled.append(f"script ({src_label} — spoken text only)")

    if getattr(req, "narration_audio", False) and story:
        # 🎙 v1.277.31 — the story's THREE narration files, each to the place
        # the project actually consumes it:
        #   audio → a MUSIC asset + `settings["story_audio_asset_id"]` so the
        #           Audio tab can select it without hunting
        #   srt   → an asset the SRT upload can read
        #   aaf   → an asset AND `settings["story_aaf_asset_id"]`, which the
        #           Audio tab's Import-AAF picks up
        # ⚠ Nothing is auto-ANALYZED and no scenes are replaced: analysis runs
        # Whisper and an AAF import REPLACES the scene list. Both are his
        # decision, not a side effect of pressing Pull.
        import hashlib
        import shutil as _sh
        from backend.database.models import Asset, AssetType
        # 📖 a chapter's own recording wins when it has one — a chapter is one
        # video, so its take is the take. ⚠ The two lanes store files in
        # DIFFERENT directories (`chapter_audio` vs `narration_audio`), so the
        # path resolver has to be picked alongside the file list, not after.
        from backend.api import storychapters as _sch
        if chapter and (chapter.get("narration_files") or {}):
            files = dict(chapter["narration_files"])
            _fp_of = _sch._ch_slot_fp
            _src_label = "chapter"
        else:
            files = sw._narr_files(story)
            _fp_of = sw._slot_fp
            _src_label = "story"
            if chapter:
                pulled.append("this chapter has no recording — falling back to "
                              "the story's")
        if not files:
            pulled.append("no narration files on the story yet")
        got = []
        for slot in ("audio", "aaf", "srt"):
            meta = files.get(slot)
            if not meta:
                continue
            src = _fp_of(wid, meta)
            if not src.exists():
                pulled.append(f"⚠ the {_src_label}'s {slot} file is missing "
                              f"on disk")
                continue
            proj_dir = Path(settings.project_dir) / str(project_id)
            sub = "audio" if slot in ("audio", "aaf") else "subtitles"
            dest_dir = proj_dir / "assets" / sub
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest = dest_dir / f"story_{meta['id']}{meta.get('ext') or ''}"
            _sh.copy2(src, dest)
            data = dest.read_bytes()
            asset = Asset(project_id=project_id, filename=dest.name,
                          rel_path=str(dest.relative_to(proj_dir)),
                          asset_type=(AssetType.MUSIC if slot == "audio"
                                      else AssetType.REFERENCE),
                          sha256=hashlib.sha256(data).hexdigest(),
                          file_size=len(data),
                          meta={"source": "storyworld", "story_id": sid,
                                "chapter_id": (chapter or {}).get("id") or "",
                                "slot": slot, "from": _src_label,
                                "original_name": meta.get("filename")})
            session.add(asset)
            await session.flush()          # need the id to point settings at it
            st[f"story_{slot}_asset_id"] = str(asset.id)
            st[f"story_{slot}_name"] = meta.get("filename") or dest.name
            got.append(slot)
        if got:
            pulled.append("narration files: " + ", ".join(got)
                          + (" — the Audio tab can select them now"
                             if "aaf" not in got else
                             " — press Import AAF on the Audio tab"))

    if getattr(req, "chapters", False):
        # 🎬 ARCS → project chapters, timed against the DETECTED SECTIONS (his
        # call). Story chapters are a preserved source, so a later audio
        # re-analysis will not delete them.
        #
        # 📖 v1.277.46 — when the project is scoped to a STORY CHAPTER, its
        # BEATS are what become the timeline, not the story's arcs. The arcs
        # describe the whole book; this project is one chapter of it, and
        # timing twelve arcs against four minutes of one chapter's audio
        # produces twelve chapters that all start at zero.
        # ⭐ A beat is arc-shaped (`_clean_arcs` normalises both), so nothing
        # downstream — the builder, the re-timer, `arc_context`, the backing-bed
        # lane — needed a line changed for this.
        if chapter:
            arcs = chapter.get("beats") or []
            _from = f"beats of “{chapter.get('title')}”"
            _hint = ("this chapter has no beats yet — ✍ write its narration "
                     "(beats come back with it) or press Beats on the Story tab")
        else:
            arcs = (story or {}).get("arcs") or []
            _from = "arcs"
            _hint = ("the story has no arcs yet (✨ Structure it on /worlds)")
        if not arcs:
            pulled.append(f"chapters skipped — {_hint}")
        else:
            from sqlalchemy import text as _text
            from backend.services.chapters.from_story import (
                create_chapters_from_arcs)
            # Clear the previously-pulled chapters (an idempotent re-pull),
            # unbinding their scenes first.
            #
            # ⚠⚠ MATCH ON PROVENANCE, NOT ON `source`. `source` is mutable:
            # `backend/api/chapters.py` sets it to "manual" on rename (:214),
            # split (:306), merge (:353) and generate-description (:597). A
            # DELETE on `source='story'` therefore skipped every chapter he had
            # edited, and `create_chapters_from_arcs` then built the whole set
            # again beside the survivors — two chapters over the same seconds,
            # which is the doubled-chapter bug the builder has a safety net for.
            #
            # ⭐ It asks `is_from_story()` — the SAME function the builder's
            # producer short-circuit and `retime_story_chapters` ask. A SQL
            # re-implementation of the test was the first version, and it had
            # already drifted: `chapter_metadata LIKE '%"from_story"%'` matches
            # the KEY'S PRESENCE while the predicate matches the VALUE'S TRUTH,
            # so `{"from_story": false}` would be deleted here and kept there.
            # Three call sites, one definition — a provenance rule with two
            # implementations is a provenance rule with two answers.
            from sqlalchemy import bindparam
            from backend.database.models import Chapter
            from backend.services.chapters.from_story import is_from_story
            _prev = (await session.execute(
                select(Chapter).where(Chapter.project_id == project_id)
            )).scalars().all()
            _kill = [c.id for c in _prev if is_from_story(c)]
            if _kill:
                await session.execute(
                    _text("UPDATE scenes SET chapter_id = NULL "
                          "WHERE project_id = :pid AND chapter_id IN :ids")
                    .bindparams(bindparam("ids", expanding=True)),
                    {"pid": project_id.hex, "ids": [c.hex for c in _kill]})
                await session.execute(
                    _text("DELETE FROM chapters WHERE id IN :ids")
                    .bindparams(bindparam("ids", expanding=True)),
                    {"ids": [c.hex for c in _kill]})
            await session.flush()
            made = await create_chapters_from_arcs(session, project_id, arcs)
            await session.flush()
            from backend.services.chapters.resolver import (
                bind_scenes_to_chapters_by_time)
            try:
                await bind_scenes_to_chapters_by_time(session, project_id)
            except Exception as e:                           # noqa: BLE001
                logger.warning("pull-from-story: scene binding failed: %s", e)
            pulled.append(f"chapters ({len(made)} from {_from})")

    project.settings = st
    project.updated_at = datetime.utcnow()
    await session.commit()
    return {"pulled": pulled}


def _sc_member_desc(m: dict) -> str:
    """The cast member's sheet as one description string — so a project
    character carries WHO they are, not just a name."""
    bits = [m.get("role") or ""]
    for k, v in (m.get("fields") or {}).items():
        if v:
            bits.append(f"{k}: {v}")
    lore = m.get("lore") or {}
    for k in ("story_role", "personality", "motivations", "voice"):
        if lore.get(k):
            bits.append(f"{k.replace('_', ' ')}: {lore[k]}")
    return ". ".join(x for x in bits if x)[:1500]


async def _import_k3_base_as_asset(project, slug: str, session) -> str:
    """Copy a klein3 base PNG in as a CHARACTER asset. COPY, not a link.

    ⚠⚠ RETURNS THE PATH THE **UI** NEEDS, WHICH IS NOT THE ASSET'S rel_path.
    An `Asset.rel_path` is relative to the PROJECT folder
    (assets/characters/x.png), but a character's `image_path` is rendered as
    `/api/files/{image_path}`, and that route resolves against `project_dir`
    ROOT — so it needs the project id in front
    (`<project_id>/assets/characters/x.png`).

    Returning the asset's rel_path here is why every storyworld character
    arrived with a name and a broken image (2026-08-18). The same helper backs
    adopt-k3, so the autogenerate-characters watcher had the same hole.
    Forward slashes on purpose: it becomes a URL."""
    import hashlib
    from backend.api.klein3 import _active_base_path, _load as _k3_load
    from backend.database.models import AssetType
    c = _k3_load(slug)
    fp = _active_base_path(slug, c)
    if not fp or not fp.exists():
        return ""
    proj_dir = Path(settings.project_dir) / str(project.id)
    dest_dir = proj_dir / "assets" / "characters"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{slug}_base.png"
    shutil.copy2(fp, dest)
    data = dest.read_bytes()
    rel = str(dest.relative_to(proj_dir))               # the ASSET's path
    asset = Asset(project_id=project.id, filename=dest.name, rel_path=rel,
                  asset_type=AssetType.CHARACTER,
                  sha256=hashlib.sha256(data).hexdigest(),
                  file_size=len(data),
                  meta={"source": "storyworld", "slug": slug})
    session.add(asset)
    # the UI path: project-id-prefixed, forward slashes, /api/files-resolvable
    return str(dest.relative_to(Path(settings.project_dir))).replace("\\", "/")
