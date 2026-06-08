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

        # Delete from database (cascade deletes scenes, assets, jobs, etc.)
        await session.delete(project)
        await session.commit()

        # Delete project directory
        project_path = settings.project_dir / str(project_id)
        if project_path.exists():
            shutil.rmtree(project_path)
            logger.info(f"Deleted project directory: {project_path}")

        logger.info(f"Deleted project {project_id}")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting project {project_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to delete project",
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
