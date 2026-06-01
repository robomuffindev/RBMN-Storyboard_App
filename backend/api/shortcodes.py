"""Shortcode lookup API — universal entity finder.

``GET /api/shortcode/{code}`` resolves any shortcode to its entity
(asset / scene / chapter) and returns a frontend redirect URL.

Used by:
- Frontend command bar (Ctrl-K) to jump to any entity by shortcode
- External tools / scripts that have a shortcode and need the UUID
- Diagnostic logs — operators can copy a shortcode from a log line
  and look it up immediately
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database.database import get_session
from backend.services.shortcode import resolve_shortcode

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/shortcode", tags=["shortcodes"])


@router.get("/{code}", summary="Resolve a shortcode")
async def resolve(
    code: str,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Look up the entity for ``code`` (e.g. ``a3f9-img-0047``).

    Returns ``{kind, id, project_id, shortcode, frontend_route, ...}``
    or 404 if the code can't be parsed or no entity matches.
    """
    result = await resolve_shortcode(session, code)
    if result is None:
        raise HTTPException(status_code=404, detail=f"Shortcode {code!r} not found")
    return result
