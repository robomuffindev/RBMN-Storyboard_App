"""FastAPI API routers for RBMN Storyboard App."""
from backend.api.projects import router as projects_router
from backend.api.scenes import router as scenes_router
from backend.api.scenes import _retrim_all_router as retrim_all_router
from backend.api.assets import router as assets_router
from backend.api.generation import router as generation_router
from backend.api.timeline import router as timeline_router
from backend.api.settings import router as settings_router
from backend.api.jobs import router as jobs_router
from backend.api.export import router as export_router
from backend.api.workflows import router as workflows_router
from backend.api.concept import router as concept_router
from backend.api.batch import router as batch_router
from backend.api.batch_runs import router as batch_runs_router
from backend.api.backing_tracks import router as backing_tracks_router

__all__ = [
    "projects_router",
    "scenes_router",
    "assets_router",
    "generation_router",
    "timeline_router",
    "settings_router",
    "jobs_router",
    "export_router",
    "workflows_router",
    "concept_router",
    "retrim_all_router",
    "batch_router",
    "batch_runs_router",
    "backing_tracks_router",
]
