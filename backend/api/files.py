"""File serving endpoint for generated outputs."""
import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import FileResponse

from backend.config import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/files", tags=["files"])


# ── Raw / untrimmed endpoint — MUST be registered before the catch-all ──
@router.get("/raw/{file_path:path}", summary="Serve raw (untrimmed) version of a generated file")
async def serve_raw_file(file_path: str):
    """Serve the raw ComfyUI output before any post-processing.

    Looks for a ``_untrimmed`` backup first (created by the trim pipeline).
    Falls back to the original file if no untrimmed backup exists (meaning
    the file was never post-processed and IS the raw output).
    """
    project_dir_resolved = settings.project_dir.resolve()

    # Derive the untrimmed path: stem_untrimmed.ext
    original = (settings.project_dir / file_path).resolve()
    untrimmed = original.parent / (original.stem + "_untrimmed" + original.suffix)

    # Security check
    if not str(original).startswith(str(project_dir_resolved)):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")

    # Prefer untrimmed (raw) version; fall back to original
    serve_path = untrimmed if untrimmed.exists() and untrimmed.is_file() else original

    if not serve_path.exists() or not serve_path.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")

    import mimetypes
    content_type = mimetypes.guess_type(serve_path.name)[0] or "application/octet-stream"

    return FileResponse(
        serve_path,
        media_type=content_type,
        filename=serve_path.name,
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )


@router.get("/{file_path:path}", summary="Serve project file by relative path")
async def serve_project_file(file_path: str):
    """Serve a file from the project directory by its relative path.

    Used primarily to display generated images/videos via their output_path
    stored in GenerationHistory records.

    Args:
        file_path: Relative path within project_dir (e.g., "<project_id>/generated/image.png")
    """
    # Security: resolve and ensure it's within project_dir
    full_path = (settings.project_dir / file_path).resolve()
    project_dir_resolved = settings.project_dir.resolve()

    if not str(full_path).startswith(str(project_dir_resolved)):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied",
        )

    if not full_path.exists() or not full_path.is_file():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="File not found",
        )

    import mimetypes
    content_type = mimetypes.guess_type(full_path.name)[0] or "application/octet-stream"

    # Disable caching for generated media — files at the same path can be
    # overwritten by new generation runs, and the browser/pywebview must
    # always fetch the latest version.
    return FileResponse(
        full_path,
        media_type=content_type,
        filename=full_path.name,
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )
