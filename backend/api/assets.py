"""Asset management endpoints for RBMN Storyboard App."""
import logging
from datetime import datetime
from typing import Optional
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from backend.config import settings
from backend.database import get_session
from backend.database.models import Asset, AssetType, Project
from backend.utils.file_utils import sha256_file, content_addressed_path

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/projects/{project_id}/assets", tags=["assets"])


# Pydantic models for request/response
class AssetMetadata(BaseModel):
    """Request model for asset metadata update."""

    tags: Optional[list[str]] = None
    name: Optional[str] = None


class AssetResponse(BaseModel):
    """Response model for an asset."""

    id: UUID
    project_id: UUID
    filename: str
    rel_path: str
    asset_type: AssetType
    sha256: str
    duration_sec: Optional[float] = None
    width: Optional[int] = None
    height: Optional[int] = None
    file_size: int
    meta: dict = {}
    created_at: datetime

    class Config:
        from_attributes = True


# Helper function to validate project exists
async def _get_project_or_404(project_id: UUID, session: AsyncSession) -> Project:
    """Get project or raise 404."""
    project = await session.get(Project, project_id)
    if not project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Project {project_id} not found",
        )
    return project


@router.post(
    "/upload",
    response_model=AssetResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Upload asset",
)
async def upload_assets(
    project_id: UUID,
    asset_type: AssetType = Form(...),
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_session),
) -> AssetResponse:
    """Upload one or more asset files with asset_type tag.

    Files are stored in a content-addressed path structure based on SHA256 hash.
    Multiple files can be uploaded at once.

    Args:
        project_id: UUID of the project.
        asset_type: Type of asset (character, clothing, item, place, music, narration, etc.).
        files: List of files to upload.
        session: Database session.

    Returns:
        List of created asset records.

    Raises:
        HTTPException: If project not found or upload fails.
    """
    try:
        await _get_project_or_404(project_id, session)

        project_path = settings.project_dir / str(project_id)
        assets_base = project_path / "assets"

        # Read file content
        content = await file.read()

        # Compute SHA256
        import hashlib
        file_sha256 = hashlib.sha256(content).hexdigest() if content else None

        # Determine subdirectory based on asset type
        if asset_type in (AssetType.MUSIC, AssetType.NARRATION):
            type_dir = "audio"
        elif asset_type == AssetType.GENERATED_VIDEO:
            type_dir = "video"
        else:
            type_dir = "images"

        type_path = assets_base / type_dir

        # Use content-addressed path if we have SHA256
        if file_sha256:
            target_path = content_addressed_path(type_path, file_sha256, file.filename)
        else:
            target_path = type_path / file.filename

        # Write file
        target_path.parent.mkdir(parents=True, exist_ok=True)
        with open(target_path, "wb") as f:
            f.write(content)

        # Compute relative path from project directory
        rel_path = target_path.relative_to(project_path)

        # Create asset record
        asset = Asset(
            project_id=project_id,
            filename=file.filename,
            rel_path=str(rel_path),
            asset_type=asset_type,
            sha256=file_sha256 or "",
            file_size=len(content),
            meta={
                "content_type": file.content_type or "application/octet-stream"
            },
        )
        session.add(asset)
        await session.commit()
        await session.refresh(asset)

        logger.info(f"Uploaded asset {file.filename} to project {project_id}")

        return AssetResponse.model_validate(asset)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error uploading asset to project {project_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to upload asset",
        )


@router.get(
    "",
    response_model=list[AssetResponse],
    summary="List assets",
)
async def list_assets(
    project_id: UUID,
    asset_type: Optional[AssetType] = None,
    session: AsyncSession = Depends(get_session),
) -> list[AssetResponse]:
    """List all assets in a project, optionally filtered by asset_type.

    Args:
        project_id: UUID of the project.
        asset_type: Optional asset type filter.
        session: Database session.

    Returns:
        List of assets.

    Raises:
        HTTPException: If project not found.
    """
    try:
        await _get_project_or_404(project_id, session)

        if asset_type:
            stmt = (
                select(Asset)
                .where(
                    (Asset.project_id == project_id)
                    & (Asset.asset_type == asset_type)
                )
                .order_by(Asset.created_at.desc())
            )
        else:
            stmt = (
                select(Asset)
                .where(Asset.project_id == project_id)
                .order_by(Asset.created_at.desc())
            )

        result = await session.execute(stmt)
        assets = result.scalars().all()

        return [
            AssetResponse.model_validate(asset)
            for asset in assets
        ]
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error listing assets for project {project_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to list assets",
        )


@router.get(
    "/{asset_id}",
    response_model=AssetResponse,
    summary="Get asset details",
)
async def get_asset(
    project_id: UUID,
    asset_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> AssetResponse:
    """Get detailed asset information.

    Args:
        project_id: UUID of the project.
        asset_id: UUID of the asset.
        session: Database session.

    Returns:
        Asset details.

    Raises:
        HTTPException: If project or asset not found.
    """
    try:
        await _get_project_or_404(project_id, session)

        asset = await session.get(Asset, asset_id)
        if not asset or asset.project_id != project_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Asset {asset_id} not found",
            )

        return AssetResponse.model_validate(asset)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting asset {asset_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get asset",
        )


@router.delete(
    "/{asset_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete asset",
)
async def delete_asset(
    project_id: UUID,
    asset_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> None:
    """Delete an asset file and its database record.

    Args:
        project_id: UUID of the project.
        asset_id: UUID of the asset.
        session: Database session.

    Raises:
        HTTPException: If project or asset not found.
    """
    try:
        await _get_project_or_404(project_id, session)

        asset = await session.get(Asset, asset_id)
        if not asset or asset.project_id != project_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Asset {asset_id} not found",
            )

        # Delete file — handle both rel_path formats (with/without project_id prefix)
        pid_str = str(project_id)
        if asset.rel_path.startswith(pid_str + "/") or asset.rel_path.startswith(pid_str + "\\"):
            asset_path = settings.project_dir / asset.rel_path
        else:
            asset_path = settings.project_dir / pid_str / asset.rel_path
        if asset_path.exists():
            asset_path.unlink()
            logger.info(f"Deleted asset file: {asset_path}")

        # Delete record
        await session.delete(asset)
        await session.commit()

        logger.info(f"Deleted asset {asset_id}")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting asset {asset_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to delete asset",
        )


class BulkDeleteRequest(BaseModel):
    """Request model for bulk asset deletion."""
    asset_ids: list[UUID]


@router.post(
    "/bulk-delete",
    status_code=status.HTTP_200_OK,
    summary="Bulk delete assets",
)
async def bulk_delete_assets(
    project_id: UUID,
    req: BulkDeleteRequest,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Delete multiple assets and their files in one request.

    Args:
        project_id: UUID of the project.
        req: List of asset IDs to delete.
        session: Database session.

    Returns:
        Dict with count of deleted assets.
    """
    try:
        await _get_project_or_404(project_id, session)

        deleted = 0
        errors = []
        pid_str = str(project_id)

        for asset_id in req.asset_ids:
            asset = await session.get(Asset, asset_id)
            if not asset or asset.project_id != project_id:
                errors.append(f"Asset {asset_id} not found")
                continue

            # Delete file
            if asset.rel_path.startswith(pid_str + "/") or asset.rel_path.startswith(pid_str + "\\"):
                asset_path = settings.project_dir / asset.rel_path
            else:
                asset_path = settings.project_dir / pid_str / asset.rel_path
            if asset_path.exists():
                asset_path.unlink()
                logger.info(f"Deleted asset file: {asset_path}")

            await session.delete(asset)
            deleted += 1

        await session.commit()
        logger.info(f"Bulk deleted {deleted} assets from project {project_id}")

        return {"deleted": deleted, "errors": errors}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error bulk deleting assets: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to bulk delete assets",
        )


@router.get(
    "/{asset_id}/file",
    summary="Serve asset file",
)
async def get_asset_file(
    project_id: UUID,
    asset_id: UUID,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Serve the actual asset file with HTTP Range support for audio/video streaming.

    HTML5 <audio> and <video> elements require Range headers for seeking/scrubbing.
    This endpoint returns 206 Partial Content when a Range header is present,
    or 200 with the full file otherwise.

    IMPORTANT: We extract all DB data upfront and close the session before
    returning the file response. This prevents connection pool exhaustion
    when many thumbnails are loaded concurrently in the Asset Manager.
    """
    import mimetypes
    from pathlib import Path

    # --- Phase 1: DB lookup (session is open) ---
    try:
        await _get_project_or_404(project_id, session)

        asset = await session.get(Asset, asset_id)
        if not asset or asset.project_id != project_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Asset {asset_id} not found",
            )

        # Extract everything we need from the ORM object before closing session
        asset_rel_path = asset.rel_path
        asset_filename = asset.filename
        asset_meta = dict(asset.meta) if asset.meta else {}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error looking up asset {asset_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to look up asset",
        )
    finally:
        # Explicitly close session so the connection returns to the pool
        # BEFORE we start streaming file bytes
        await session.close()

    # --- Phase 2: File serving (session is closed, no DB connection held) ---
    try:
        pid_str = str(project_id)
        if asset_rel_path.startswith(pid_str + "/") or asset_rel_path.startswith(pid_str + "\\"):
            asset_path = settings.project_dir / asset_rel_path
        else:
            asset_path = settings.project_dir / pid_str / asset_rel_path

        if not asset_path.exists():
            alt_path = (
                settings.project_dir / asset_rel_path
                if not asset_rel_path.startswith(pid_str)
                else settings.project_dir / pid_str / asset_rel_path
            )
            if alt_path.exists():
                asset_path = alt_path
            else:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Asset file not found on disk",
                )

        content_type = asset_meta.get("content_type")
        if not content_type or content_type == "application/octet-stream":
            content_type = mimetypes.guess_type(asset_filename)[0] or "application/octet-stream"

        # Sanitize filename for HTTP headers — latin-1 codec can't handle
        # Unicode chars like ’ (right single quote) in Content-Disposition.
        # Use RFC 5987 filename* for UTF-8 and an ASCII fallback for filename.
        safe_filename = asset_filename.encode("ascii", errors="replace").decode("ascii")
        # Build RFC 5987 Content-Disposition with both ASCII and UTF-8 variants
        cd_header = (
            f'inline; filename="{safe_filename}"; '
            f"filename*=UTF-8''{quote(asset_filename)}"
        )

        file_size = asset_path.stat().st_size
        range_header = request.headers.get("range")

        if range_header:
            range_spec = range_header.strip().replace("bytes=", "")
            parts = range_spec.split("-")
            start = int(parts[0]) if parts[0] else 0
            end = int(parts[1]) if parts[1] else file_size - 1
            end = min(end, file_size - 1)
            content_length = end - start + 1

            async def ranged_file():
                with open(asset_path, "rb") as f:
                    f.seek(start)
                    remaining = content_length
                    while remaining > 0:
                        chunk_size = min(65536, remaining)
                        data = f.read(chunk_size)
                        if not data:
                            break
                        remaining -= len(data)
                        yield data

            from starlette.responses import StreamingResponse as StarletteStreamingResponse
            return StarletteStreamingResponse(
                ranged_file(),
                status_code=206,
                media_type=content_type,
                headers={
                    "Content-Range": f"bytes {start}-{end}/{file_size}",
                    "Accept-Ranges": "bytes",
                    "Content-Length": str(content_length),
                    "Content-Disposition": cd_header,
                },
            )
        else:
            from fastapi.responses import FileResponse
            return FileResponse(
                asset_path,
                media_type=content_type,
                filename=safe_filename,
                headers={
                    "Accept-Ranges": "bytes",
                    "Content-Disposition": cd_header,
                },
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error serving asset {asset_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to serve asset",
        )


@router.put(
    "/{asset_id}",
    response_model=AssetResponse,
    summary="Update asset metadata",
)
async def update_asset(
    project_id: UUID,
    asset_id: UUID,
    req: AssetMetadata,
    session: AsyncSession = Depends(get_session),
) -> AssetResponse:
    """Update asset metadata (tags, name).

    Args:
        project_id: UUID of the project.
        asset_id: UUID of the asset.
        req: Update request with tags and/or name.
        session: Database session.

    Returns:
        Updated asset.

    Raises:
        HTTPException: If project or asset not found.
    """
    try:
        await _get_project_or_404(project_id, session)

        asset = await session.get(Asset, asset_id)
        if not asset or asset.project_id != project_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Asset {asset_id} not found",
            )

        if req.tags is not None:
            asset.meta["tags"] = req.tags
        if req.name is not None:
            asset.filename = req.name

        await session.commit()
        await session.refresh(asset)

        logger.info(f"Updated asset {asset_id}")

        return AssetResponse.model_validate(asset)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating asset {asset_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update asset",
        )
