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
        try:
            from sqlalchemy import text as _del_text
            _pid_hex = project_id.hex
            await session.execute(
                _del_text("UPDATE scenes SET chapter_id = NULL WHERE project_id = :pid"),
                {"pid": _pid_hex},
            )
            await session.execute(
                _del_text("UPDATE chapters SET parent_chapter_id = NULL WHERE project_id = :pid"),
                {"pid": _pid_hex},
            )
            await session.commit()
        except Exception as _fk_err:
            logger.warning(f"Project-delete FK pre-clean failed (continuing): {_fk_err}")
            try:
                await session.rollback()
            except Exception:
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
            "h3_use_audio_ref", "h3_ref_image_size", "h3_auto_sheet_refs")):
        return {"video_engine": st.get("video_engine", "ltx_2.3"),
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
    for k in ("h3_turbo", "h3_draft", "h3_use_audio_ref", "h3_auto_sheet_refs"):
        v = getattr(req, k)
        if v is not None:
            st[k] = bool(v)
    project.settings = st
    project.updated_at = datetime.utcnow()
    await session.commit()
    return {"video_engine": st.get("video_engine", "ltx_2.3"),
            **{k: st.get(k) for k in ("h3_turbo", "h3_draft", "h3_audio_mode",
                                      "h3_use_audio_ref", "h3_ref_image_size",
                                      "h3_auto_sheet_refs")}}


# ══ 🌍 story/world ↔ project link (v1.277.12) ═══════════════════════════════
class StoryLinkIn(BaseModel):
    world_id: Optional[str] = None       # '' or None with attach=False detaches
    story_id: Optional[str] = None       # optional story inside the world
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
                sw._find(w.get("stories") or [], req.story_id, "story")
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

    project.settings = st
    project.updated_at = datetime.utcnow()
    await session.commit()
    return {"world_id": st.get("world_id"), "story_id": st.get("story_id")}


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
    sid = str(st.get("story_id") or "")
    story = next((s for s in (w.get("stories") or []) if s["id"] == sid), None)
    return {"linked": True, "world_id": wid, "world_name": w.get("name"),
            "story_id": sid or None,
            "story_title": (story or {}).get("title"),
            "style_text": sw._style_text(w),
            "cast": [{"id": c["id"], "name": c["name"],
                      "char_slug": c.get("char_slug") or "",
                      "status": c.get("status")}
                     for c in (w.get("cast") or [])],
            "texts": [{"id": t["id"], "kind": t.get("kind"),
                       "title": t.get("title"), "story_id": t.get("story_id")}
                      for t in (w.get("texts") or [])]}


class PullFromStoryIn(BaseModel):
    """Which parts of the linked world/story to pull into the project."""
    concept: bool = True          # story synopsis/logline → concept_text
    style: bool = True            # world visual style → style_text
    characters: bool = False      # cast → project characters (images imported)
    lyrics_text_id: str = ""      # a world text id → Lyrics.initial_text


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
    pulled: list = []

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
        if bits:
            st["concept_text"] = "\n\n".join(bits)
            if story:
                st["song_title"] = st.get("song_title") or story.get("title")
            pulled.append("concept")

    if req.style:
        stx = sw._style_text(w)
        vs = (w.get("world") or {}).get("visual_style") or ""
        combined = ". ".join(x for x in (stx, vs) if x and x not in stx)
        if combined:
            st["style_text"] = combined
            pulled.append("style")

    if req.characters:
        chars = list(st.get("characters") or [])
        have = {str(c.get("name", "")).lower() for c in chars}
        n_added = 0
        for m in (w.get("cast") or []):
            if m["name"].lower() in have:
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
        if n_added:
            st["characters"] = chars
            pulled.append(f"characters ({n_added})")

    if req.lyrics_text_id:
        t = next((x for x in (w.get("texts") or [])
                  if x["id"] == req.lyrics_text_id), None)
        if not t:
            raise HTTPException(404, "that text does not exist in the world")
        from backend.database.models import Lyrics
        r = await session.execute(
            select(Lyrics).where(Lyrics.project_id == project_id))
        ly = r.scalars().first()
        if ly is None:
            ly = Lyrics(project_id=project_id, full_text="",
                        initial_text=t.get("body") or "")
            session.add(ly)
        else:
            ly.initial_text = t.get("body") or ""
        pulled.append(f"lyrics ({t.get('title')})")

    project.settings = st
    project.updated_at = datetime.utcnow()
    await session.commit()
    return {"pulled": pulled}


async def _import_k3_base_as_asset(project, slug: str, session) -> str:
    """Copy a klein3 character's active base PNG into the project as a
    CHARACTER asset; returns the rel_path ('' if no base exists). COPY, not a
    link — deleting the library character must not orphan the project."""
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
    rel = str(dest.relative_to(proj_dir))
    asset = Asset(project_id=project.id, filename=dest.name, rel_path=rel,
                  asset_type=AssetType.CHARACTER,
                  sha256=hashlib.sha256(data).hexdigest(),
                  file_size=len(data),
                  meta={"source": "storyworld", "slug": slug})
    session.add(asset)
    return rel
