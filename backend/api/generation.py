"""Image and video generation endpoints for RBMN Storyboard App."""
import asyncio
import json
import logging
from datetime import datetime
from typing import Optional
from uuid import UUID

from sqlalchemy.exc import OperationalError as SAOperationalError

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from backend.config import settings
from backend.database import get_session
from backend.database.models import (
    Project,
    Scene,
    Asset,
    Job,
    JobType,
    JobStatus,
    AppSettings,
    GenerationHistory,
    Lyrics,
)
from backend.services.jobs.queue import JobQueue

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/projects/{project_id}/generate", tags=["generation"])


# Pydantic models for request/response
class GenerateImageRequest(BaseModel):
    """Request model for image generation."""

    scene_id: UUID
    workflow_type: Optional[str] = None  # klein_1ref, klein_2ref, klein_3ref, klein_4ref, klein_t2i
    workflow_config_id: Optional[UUID] = None  # for custom workflows
    prompt: str
    width: int = 1024
    height: int = 576
    seed: Optional[int] = None
    reference_asset_ids: list[UUID] = []
    frame_type: Optional[str] = None  # 'first' or 'last' — tags the generation for FF/LF filtering
    two_pass: bool = False  # If True, generate scene image first (no refs), then composite with character refs


class GenerateVideoRequest(BaseModel):
    """Request model for video generation."""

    scene_id: UUID
    workflow_type: Optional[str] = None  # ltx_fflf, ltx_i2v
    workflow_config_id: Optional[UUID] = None  # for custom workflows
    prompt: str
    width: int = 1024
    height: int = 576
    duration: float = 10.0
    framerate: int = 24
    seed: Optional[int] = None
    first_frame_asset_id: Optional[UUID] = None
    last_frame_asset_id: Optional[UUID] = None
    audio_asset_id: Optional[UUID] = None
    skip_audio_mux: bool = False  # If True, keeps LTX model-generated audio (better for lip-sync testing)


class EnhancePromptRequest(BaseModel):
    """Request model for prompt enhancement."""

    prompt: str
    context: Optional[str] = None
    provider: Optional[str] = None  # openai, anthropic, gemini — None = use default
    is_video: bool = False  # Use video-specific system prompt (LTX optimized)
    frame_type: Optional[str] = None  # 'first' or 'last' — selects FF vs LF system prompt


class BatchGenerationItem(BaseModel):
    """Item in batch generation request."""

    type: str  # image or video
    scene_id: UUID
    workflow_type: str
    prompt: str
    width: Optional[int] = None
    height: Optional[int] = None
    duration: Optional[float] = None
    framerate: Optional[int] = None
    seed: Optional[int] = None
    reference_asset_ids: Optional[list[UUID]] = None


class BatchGenerationRequest(BaseModel):
    """Request model for batch generation."""

    items: list[BatchGenerationItem]


class GenerationJobResponse(BaseModel):
    """Response model for a generation job."""

    id: UUID
    project_id: UUID
    scene_id: Optional[UUID]
    job_type: str
    status: str
    created_at: datetime

    class Config:
        from_attributes = True


# Helper function to resolve seed with fallback chain
async def _resolve_seed(
    project_id: UUID,
    scene_id: UUID,
    explicit_seed: Optional[int],
    session: AsyncSession,
    job_type: str = "image",
    frame_type: Optional[str] = None,
) -> Optional[int]:
    """Resolve seed with priority: explicit > scene override > global > None.

    Args:
        project_id: Project UUID
        scene_id: Scene UUID
        explicit_seed: Explicitly provided seed in request (highest priority)
        session: Database session
        job_type: "image" or "video" — determines which override to check
        frame_type: "first" or "last" for images — determines which per-frame seed

    Returns:
        Resolved seed value or None (will be randomized by dispatcher)
    """
    # Highest priority: explicit seed in request
    if explicit_seed is not None:
        return explicit_seed

    # Check scene override
    scene = await session.get(Scene, scene_id)
    if scene and scene.parameters:
        if job_type == "video":
            if scene.parameters.get("video_seed_override"):
                scene_seed = scene.parameters.get("video_seed")
                if scene_seed is not None:
                    return scene_seed
        else:
            # Image: check per-frame seed overrides
            if frame_type == "last":
                if scene.parameters.get("image_seed_override_last"):
                    scene_seed = scene.parameters.get("image_seed_last")
                    if scene_seed is not None:
                        return scene_seed
            else:
                # Default to first frame
                if scene.parameters.get("image_seed_override_first"):
                    scene_seed = scene.parameters.get("image_seed_first")
                    if scene_seed is not None:
                        return scene_seed

    # Check global seed
    project = await session.get(Project, project_id)
    if project and project.settings:
        if project.settings.get("global_seed_enabled"):
            global_seed = project.settings.get("global_seed", 0)
            if global_seed:  # Only use if non-zero
                return global_seed

    # No seed set — dispatcher will randomize
    return None


# Helper function to validate project and scene exist
async def _get_project_or_404(project_id: UUID, session: AsyncSession) -> Project:
    """Get project or raise 404."""
    project = await session.get(Project, project_id)
    if not project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Project {project_id} not found",
        )
    return project


async def _get_scene_or_404(
    scene_id: UUID, project_id: UUID, session: AsyncSession
) -> Scene:
    """Get scene in project or raise 404."""
    scene = await session.get(Scene, scene_id)
    if not scene or scene.project_id != project_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Scene {scene_id} not found",
        )
    return scene


@router.post(
    "/image",
    response_model=GenerationJobResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Generate image",
)
async def generate_image(
    project_id: UUID,
    req: GenerateImageRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> GenerationJobResponse:
    """Generate an image for a scene using ComfyUI workflows.

    Loads workflow JSON based on workflow_type, mutates it with scene parameters,
    submits to ComfyUI via dispatcher, and returns a job_id for tracking.

    Args:
        project_id: UUID of the project.
        req: Image generation request.
        session: Database session.

    Returns:
        Job record with job_id for tracking.

    Raises:
        HTTPException: If project or scene not found.
    """
    try:
        await _get_project_or_404(project_id, session)
        scene = await _get_scene_or_404(req.scene_id, project_id, session)

        # Read scene-level negative prompt so dispatcher can apply it
        scene_negative_prompt = scene.negative_prompt or ""

        # Resolve seed with fallback chain
        resolved_seed = await _resolve_seed(
            project_id, req.scene_id, req.seed, session,
            job_type="image", frame_type=req.frame_type,
        )

        # Two-pass mode: Pass 1 generates scene without character refs,
        # Pass 2 (auto-chained by dispatcher) composites characters into the scene.
        # Character refs may come from the request OR be resolved from project concept data.
        if req.two_pass:
            # Collect character ref IDs: from request first, then fall back to concept data
            char_ref_ids = [str(aid) for aid in req.reference_asset_ids] if req.reference_asset_ids else []
            if not char_ref_ids:
                # Resolve from project concept data — find characters with image_path
                project = await session.get(Project, project_id)
                if project and project.settings:
                    characters = project.settings.get("characters", [])
                    for char in characters:
                        img_path = char.get("image_path", "")
                        if img_path:
                            # Find the Asset by rel_path to get its ID
                            from sqlmodel import select
                            stmt = select(Asset).where(
                                Asset.project_id == project_id,
                                Asset.rel_path == img_path,
                            )
                            result = await session.execute(stmt)
                            asset = result.scalars().first()
                            if asset:
                                char_ref_ids.append(str(asset.id))
                    if char_ref_ids:
                        logger.info(f"Two-pass: resolved {len(char_ref_ids)} character ref IDs from concept data")

            job = Job(
                project_id=project_id,
                scene_id=req.scene_id,
                job_type=JobType.IMAGE,
                status=JobStatus.PENDING,
                parameters={
                    "workflow_type": "klein_t2i",  # Pass 1: no refs
                    "workflow_config_id": None,
                    "prompt": req.prompt,
                    "width": req.width,
                    "height": req.height,
                    "seed": resolved_seed,
                    "reference_asset_ids": [],  # No refs for Pass 1
                    **({"frame_type": req.frame_type} if req.frame_type else {}),
                    "negative_prompt": scene_negative_prompt,
                    "two_pass": True,
                    "two_pass_phase": "base",
                    # Store character refs for Pass 2 (from request or concept data)
                    "two_pass_character_ref_ids": char_ref_ids,
                    "two_pass_original_workflow": req.workflow_type,
                },
            )
        else:
            # Standard single-pass generation
            job = Job(
                project_id=project_id,
                scene_id=req.scene_id,
                job_type=JobType.IMAGE,
                status=JobStatus.PENDING,
                parameters={
                    "workflow_type": req.workflow_type,
                    "workflow_config_id": str(req.workflow_config_id) if req.workflow_config_id else None,
                    "prompt": req.prompt,
                    "width": req.width,
                    "height": req.height,
                    "seed": resolved_seed,
                    "reference_asset_ids": [str(aid) for aid in req.reference_asset_ids],
                    **({"frame_type": req.frame_type} if req.frame_type else {}),
                    "negative_prompt": scene_negative_prompt,
                },
            )
        session.add(job)
        await session.commit()
        await session.refresh(job)

        logger.info(
            f"Created image generation job {job.id} for scene {req.scene_id} "
            f"with seed={resolved_seed} two_pass={req.two_pass}"
        )

        # Notify the dispatcher that a new job is available
        job_queue: JobQueue = request.app.state.job_queue
        job_queue.notify()

        return GenerationJobResponse.model_validate(job)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error generating image for scene {req.scene_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to generate image",
        )


@router.post(
    "/video",
    response_model=GenerationJobResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Generate video",
)
async def generate_video(
    project_id: UUID,
    req: GenerateVideoRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> GenerationJobResponse:
    """Generate a video for a scene using ComfyUI workflows.

    Supports frame-to-frame (ltx_fflf) and image-to-video (ltx_i2v) workflows.
    Returns a job_id for tracking.

    Args:
        project_id: UUID of the project.
        req: Video generation request.
        session: Database session.

    Returns:
        Job record with job_id for tracking.

    Raises:
        HTTPException: If project or scene not found.
    """
    try:
        await _get_project_or_404(project_id, session)
        await _get_scene_or_404(req.scene_id, project_id, session)

        # Resolve seed with fallback chain
        resolved_seed = await _resolve_seed(
            project_id, req.scene_id, req.seed, session,
            job_type="video",
        )

        # Create job record
        job = Job(
            project_id=project_id,
            scene_id=req.scene_id,
            job_type=JobType.VIDEO,
            status=JobStatus.PENDING,
            parameters={
                "workflow_type": req.workflow_type,
                "workflow_config_id": str(req.workflow_config_id) if req.workflow_config_id else None,
                "prompt": req.prompt,
                "width": req.width,
                "height": req.height,
                "duration": req.duration,
                "framerate": req.framerate,
                "seed": resolved_seed,
                "first_frame_asset_id": str(req.first_frame_asset_id) if req.first_frame_asset_id else None,
                "last_frame_asset_id": str(req.last_frame_asset_id) if req.last_frame_asset_id else None,
                "audio_asset_id": str(req.audio_asset_id) if req.audio_asset_id else None,
                "skip_audio_mux": req.skip_audio_mux,
            },
        )
        session.add(job)
        await session.commit()
        await session.refresh(job)

        logger.info(
            f"Created video generation job {job.id} for scene {req.scene_id} with seed={resolved_seed} | "
            f"workflow={req.workflow_type} ff_asset={req.first_frame_asset_id} "
            f"lf_asset={req.last_frame_asset_id} audio_asset={req.audio_asset_id} "
            f"w={req.width} h={req.height}"
        )

        # Notify the dispatcher that a new job is available
        job_queue: JobQueue = request.app.state.job_queue
        job_queue.notify()

        return GenerationJobResponse.model_validate(job)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error generating video for scene {req.scene_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to generate video",
        )


@router.post(
    "/enhance-prompt",
    summary="Enhance prompt using LLM",
)
async def enhance_prompt(
    project_id: UUID,
    req: EnhancePromptRequest,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Enhance a prompt using an LLM (OpenAI, Anthropic, or Gemini).

    Takes a base prompt and optional context (lyrics, scene description),
    enhances it with LLM, and returns the enhanced prompt.

    Args:
        project_id: UUID of the project.
        req: Prompt enhancement request.
        session: Database session.

    Returns:
        Dictionary with enhanced_prompt and tokens_used.

    Raises:
        HTTPException: If project not found or LLM provider unavailable.
    """
    try:
        await _get_project_or_404(project_id, session)

        # Get settings for the specified provider
        settings_stmt = select(AppSettings).where(AppSettings.id == 1)
        settings_result = await session.execute(settings_stmt)
        app_settings = settings_result.scalars().first()

        if not app_settings:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Application settings not configured",
            )

        # Resolve LLM provider — use request override or default
        from backend.api.settings import resolve_llm_config

        if req.provider:
            # Explicit provider requested
            provider_lower = req.provider.lower()
            providers_map = {
                "openai": (app_settings.openai_api_key, app_settings.openai_model, "gpt-4o"),
                "anthropic": (app_settings.anthropic_api_key, app_settings.anthropic_model, "claude-sonnet-4-20250514"),
                "gemini": (app_settings.gemini_api_key, app_settings.gemini_model, "gemini-2.0-flash"),
            }
            if provider_lower not in providers_map:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Unknown provider: {req.provider}",
                )
            api_key, model_name, default_model = providers_map[provider_lower]
            if not api_key:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"No API key configured for {req.provider}",
                )
            model = model_name or default_model
            provider_lower = provider_lower  # already set
        else:
            # Use the default LLM provider
            provider_lower, api_key, model = resolve_llm_config(app_settings)

        # Resolve system prompt override from settings
        system_prompt_override = None
        gen_model_name = None
        if req.is_video:
            gen_model_name = app_settings.video_model_type or "ltx_2.3"
            overrides = app_settings.video_system_prompt_overrides or {}
        else:
            gen_model_name = app_settings.image_model_type or "flux2_klein_dev_9b"
            overrides = app_settings.image_system_prompt_overrides or {}

        model_override = overrides.get(gen_model_name, {})
        if isinstance(model_override, dict) and model_override.get("enabled") and model_override.get("text", "").strip():
            system_prompt_override = model_override["text"]

        # Call LLM service to enhance prompt
        from backend.services.llm.prompt_enhancer import PromptEnhancer

        enhancer = PromptEnhancer()
        enhanced_prompt = await asyncio.to_thread(
            enhancer.enhance,
            req.prompt,
            req.context,
            provider_lower,
            api_key,
            model,
            req.is_video,
            system_prompt_override,
            gen_model_name,
            req.frame_type,
        )

        return {
            "enhanced_prompt": enhanced_prompt,
            "tokens_used": 0,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error enhancing prompt: {e}")
        # Surface the actual error message so users can see what went wrong
        # (e.g. model not found, invalid API key, unsupported parameter)
        detail = f"LLM call failed: {e}" if str(e) else "Failed to enhance prompt"
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=detail,
        )


@router.post(
    "/batch",
    response_model=list[GenerationJobResponse],
    status_code=status.HTTP_201_CREATED,
    summary="Submit batch generation",
)
async def batch_generate(
    project_id: UUID,
    req: BatchGenerationRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> list[GenerationJobResponse]:
    """Submit multiple generation jobs at once (batch processing).

    Args:
        project_id: UUID of the project.
        req: Batch generation request with list of job specs.
        session: Database session.

    Returns:
        List of created job records.

    Raises:
        HTTPException: If project not found or validation fails.
    """
    try:
        await _get_project_or_404(project_id, session)

        created_jobs = []

        for item in req.items:
            # Validate scene exists
            await _get_scene_or_404(item.scene_id, project_id, session)

            # Determine job type
            job_type = JobType.IMAGE if item.type == "image" else JobType.VIDEO

            # Create job record
            job = Job(
                project_id=project_id,
                scene_id=item.scene_id,
                job_type=job_type,
                status=JobStatus.PENDING,
                parameters={
                    "workflow_type": item.workflow_type,
                    "prompt": item.prompt,
                    **({"width": item.width} if item.width else {}),
                    **({"height": item.height} if item.height else {}),
                    **({"duration": item.duration} if item.duration else {}),
                    **({"framerate": item.framerate} if item.framerate else {}),
                    **({"seed": item.seed} if item.seed else {}),
                    **(
                        {"reference_asset_ids": [str(aid) for aid in item.reference_asset_ids]}
                        if item.reference_asset_ids
                        else {}
                    ),
                },
            )
            session.add(job)
            await session.flush()
            created_jobs.append(job)

        await session.commit()
        logger.info(f"Created {len(created_jobs)} batch generation jobs for project {project_id}")

        # Notify the dispatcher
        job_queue: JobQueue = request.app.state.job_queue
        job_queue.notify()

        return [GenerationJobResponse.model_validate(job) for job in created_jobs]
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating batch generation jobs: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create batch generation jobs",
        )


# ── Auto Generate ────────────────────────────────────────────────────


class AutoGenerateRequest(BaseModel):
    """Request model for auto generation."""

    mode: str  # all_images, empty_only, enhanced_all, enhanced_missing


class AutoGenerateResponse(BaseModel):
    """Response model for auto generation."""

    jobs_created: int
    job_ids: list[str]
    enhanced_count: int  # how many prompts were LLM-enhanced
    skipped_count: int  # scenes that were skipped (already complete)


def _scene_has_first_frame(scene: Scene) -> bool:
    """Check if scene has a chosen first frame image."""
    return bool(scene.parameters.get("chosen_image_path"))


def _scene_has_last_frame(scene: Scene) -> bool:
    """Check if scene has a chosen last frame image."""
    return bool(scene.parameters.get("chosen_last_frame_path"))


def _scene_uses_ff_lf(scene: Scene) -> bool:
    """Check if scene uses first/last frame video mode."""
    return scene.parameters.get("video_mode") == "ff_lf"


def _auto_workflow_type(ref_count: int) -> str:
    """Map reference image count to workflow type."""
    mapping = {0: "klein_t2i", 1: "klein_1ref", 2: "klein_2ref", 3: "klein_3ref", 4: "klein_4ref"}
    return mapping.get(min(ref_count, 4), "klein_t2i")


def _apply_two_pass_to_job_params(
    params: dict,
    two_pass: bool,
    ref_ids: list[str],
) -> dict:
    """Transform image job parameters for two-pass mode.

    When two_pass is enabled and there are character reference IDs:
    - Changes workflow to klein_t2i (no refs)
    - Stores original char refs in two_pass_character_ref_ids
    - Sets two_pass=True, two_pass_phase="base"
    - Clears reference_asset_ids (Pass 1 has no refs)

    The dispatcher auto-chains Pass 2 (composite) on Pass 1 completion.

    Args:
        params: Original job parameters dict
        two_pass: Whether two-pass mode is enabled
        ref_ids: The original reference asset IDs (character refs)

    Returns:
        Modified params dict (or original if two-pass not applicable)
    """
    if not two_pass or not ref_ids:
        return params

    # Transform for Pass 1: no refs, scene composition only
    params = dict(params)  # Don't mutate the original
    params["two_pass"] = True
    params["two_pass_phase"] = "base"
    params["two_pass_character_ref_ids"] = list(ref_ids)
    params["two_pass_original_workflow"] = params.get("workflow_type", "klein_t2i")
    params["workflow_type"] = "klein_t2i"
    params["reference_asset_ids"] = []
    return params


async def _persist_two_pass_on_scene(scene: Scene, session, two_pass: bool, ref_ids: list[str]):
    """Persist two_pass_enabled flag on scene parameters when auto-gen uses two-pass.

    This ensures the UI checkbox reflects what auto-gen actually did.
    Only updates if two_pass is True and there are refs (i.e., two-pass will actually run).
    """
    if not two_pass or not ref_ids:
        return
    scene_params = dict(scene.parameters or {})
    if not scene_params.get("two_pass_enabled"):
        scene_params["two_pass_enabled"] = True
        scene.parameters = scene_params
        await session.commit()


def _collect_ref_asset_ids(scene: Scene, frame: str) -> list[str]:
    """Collect reference asset IDs for a frame type from scene parameters.

    Only collects extra reference images from scene parameters.
    Character images are resolved separately by _resolve_character_asset_ids()
    which needs async DB access.
    """
    key = f"image_refs_{frame}"
    refs_data = scene.parameters.get(key, {})
    ids: list[str] = []
    extras = refs_data.get("extras", [])
    for extra in extras:
        if extra.get("asset_id"):
            ids.append(extra["asset_id"])
    return ids


async def _resolve_character_asset_ids(
    characters: list[dict],
    project_id,
    session,
    max_chars: int = 2,
) -> list[str]:
    """Resolve character image paths to Asset IDs for use as references.

    Characters with image_path set have their images stored as Assets.
    This looks up the Asset by rel_path to get the UUID needed for
    the generation job's reference_asset_ids.

    Returns asset IDs in order (Image 1, Image 2, etc.).
    """
    if not characters:
        return []

    asset_ids: list[str] = []
    for char in characters[:max_chars]:
        image_path = char.get("image_path", "")
        if not image_path:
            continue

        # Look up the asset by rel_path
        stmt = select(Asset).where(
            Asset.project_id == project_id,
            Asset.rel_path == image_path,
        )
        result = await session.execute(stmt)
        asset = result.scalars().first()
        if asset:
            asset_ids.append(str(asset.id))
        else:
            logger.warning(f"Character image asset not found for path: {image_path}")

    return asset_ids


async def _ensure_video_flow(
    project: Project,
    scenes: list[Scene],
    session: AsyncSession,
    llm_provider: str | None,
    llm_api_key: str | None,
    llm_model: str | None,
) -> bool:
    """Auto-generate video flow if no scenes have flow ideas.

    Only generates if:
    - LLM is configured
    - No scenes already have a flow_idea set

    Returns True if flow was generated, False if skipped.
    """
    if not llm_provider or not llm_api_key:
        logger.info("Auto-flow: No LLM configured, skipping video flow generation")
        return False

    # Check if any scene already has a flow idea
    has_flow = any(
        (scene.parameters or {}).get("flow_idea", "").strip()
        for scene in scenes
    )
    if has_flow:
        logger.info("Auto-flow: Scenes already have flow ideas, skipping generation")
        return False

    # Generate video flow via LLM (same logic as concept.py generate_video_flow)
    logger.info(f"Auto-flow: No flow ideas found — auto-generating video flow for {len(scenes)} scenes")

    s = project.settings or {}
    concept_text = s.get("concept_text", "")
    style_text = s.get("style_text", "")
    characters = s.get("characters", [])

    # Fetch lyrics for per-scene context
    from backend.database.models import Lyrics as LyricsModel
    lyrics_stmt = select(LyricsModel).where(LyricsModel.project_id == project.id)
    lyrics_result = await session.execute(lyrics_stmt)
    lyrics_record = lyrics_result.scalars().first()
    lyrics_words = lyrics_record.words if lyrics_record else []
    full_lyrics = ""
    if lyrics_record:
        full_lyrics = (getattr(lyrics_record, "initial_text", "") or "").strip()
        if not full_lyrics:
            full_lyrics = (lyrics_record.full_text or "").strip()

    char_block = ""
    for i, c in enumerate(characters, 1):
        char_block += f"\n  Character {i}: {c.get('name', 'Unnamed')} — {c.get('description', 'No description')}"
    if not char_block:
        char_block = "\n  (No characters defined)"

    # Build scene list with per-scene lyrics
    scene_lines = []
    for i, sc in enumerate(scenes):
        scene_lyrics = _get_scene_lyrics(sc, lyrics_words, lyrics_text=full_lyrics) if lyrics_words else ""
        line = f"  Scene {i+1} \"{sc.name}\" ({sc.start_time:.1f}s – {sc.end_time:.1f}s)"
        if scene_lyrics:
            line += f"\n    LYRICS: \"{scene_lyrics}\""
        else:
            line += "\n    LYRICS: (instrumental / no vocals)"
        scene_lines.append(line)
    scene_list = "\n".join(scene_lines)

    system_prompt = (
        "You are a creative director for AI-generated music videos and narration videos. "
        "Given a video concept, visual style, characters, LYRICS for each scene, and a list of scenes with timings, "
        "generate a cohesive storyboard idea for each scene. Each idea should describe what happens "
        "visually in that scene — camera movement, action, mood, composition — so that an AI image/video "
        "generator can produce compelling frames that tell a SEQUENTIAL STORY.\n\n"
        "CRITICAL — LYRICS ARE YOUR PRIMARY CREATIVE DRIVER:\n"
        "The lyrics for each scene are the #1 source of creative direction. Your storyboard ideas MUST:\n"
        "1. VISUALLY DEPICT specific objects, people, actions, and settings mentioned in the lyrics. "
        "If the lyrics say 'red car', 'broken mirror', 'dancing in the rain', 'walking through fire' — "
        "those elements MUST appear in your scene description. Do NOT abstract them into vague mood.\n"
        "2. FOLLOW THE NARRATIVE ORDER of the lyrics. Events described first in the song happen first "
        "in the video. The visual story should track the lyrical story beat by beat.\n"
        "3. For instrumental/no-vocal scenes: use the overall concept and surrounding lyrical context "
        "to create transitional or atmospheric visuals that bridge the narrative.\n"
        "4. Translate metaphors into compelling visuals — 'heart on fire' could be a character with "
        "glowing embers around their chest, 'drowning in sorrow' could be a character submerged in "
        "dark water. Make abstract lyrics VISUALLY CONCRETE.\n\n"
        "CRITICAL: Each scene MUST be visually DISTINCT from the others. Vary the composition, "
        "camera angle, subject position, action, and environment across scenes.\n\n"
        "Note: This app supports character reference images (up to 5 characters) that maintain visual "
        "consistency across scenes. When characters are defined, reference them by name "
        "in your scene descriptions — their appearance stays consistent automatically.\n\n"
        "Keep each idea under 100 words.\n\n"
        "IMPORTANT: Return ONLY a JSON array of strings, one per scene, in order. "
        "No markdown, no labels, no explanation — just the JSON array."
    )

    lyrics_block = ""
    if full_lyrics:
        lyrics_block = f"\nFull Song Lyrics (for overall narrative arc):\n{full_lyrics}\n"

    user_prompt = (
        f"Video Concept: {concept_text or '(not set)'}\n"
        f"Visual Style: {style_text or '(not set)'}\n"
        f"Characters: {char_block}\n"
        f"{lyrics_block}\n"
        f"Scenes (with per-scene lyrics):\n{scene_list}\n\n"
        "Generate a storyboard idea for each scene. The lyrics for each scene are your PRIMARY source "
        "of visual direction — depict what they describe. Return a JSON array of strings."
    )

    from backend.api.concept import _call_llm, _try_repair_truncated_json_array

    flow_max_tokens = max(2000, len(scenes) * 150 + 500)

    try:
        raw_text = await asyncio.to_thread(
            _call_llm, llm_provider, llm_api_key, llm_model, system_prompt, user_prompt,
            max_tokens=flow_max_tokens,
        )
    except Exception as e:
        logger.warning(f"Auto-flow: LLM call failed: {e}")
        return False

    # Parse JSON array
    import json as json_mod
    try:
        cleaned = raw_text.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else cleaned[3:]
        if cleaned.endswith("```"):
            cleaned = cleaned.rsplit("```", 1)[0]
        cleaned = cleaned.strip()

        ideas_list = json_mod.loads(cleaned)
        if not isinstance(ideas_list, list):
            raise ValueError("Expected a JSON array")
    except (json_mod.JSONDecodeError, ValueError):
        repaired = _try_repair_truncated_json_array(cleaned)
        if repaired is not None:
            ideas_list = repaired
        else:
            ideas_list = [p.strip() for p in raw_text.split("\n\n") if p.strip()]

    # Save flow ideas to scenes
    for i, scene in enumerate(scenes):
        idea_text = ideas_list[i] if i < len(ideas_list) else ""
        params = dict(scene.parameters or {})
        params["flow_idea"] = idea_text
        scene.parameters = params

    await session.commit()
    logger.info(f"Auto-flow: Generated flow ideas for {min(len(ideas_list), len(scenes))} of {len(scenes)} scenes")
    return True


async def _build_auto_enhance_context(
    project: Project,
    scene: Scene,
    lyrics_text: str,
    frame_type: str,
    model_type: str,
    prev_scene: Scene | None = None,
) -> str:
    """Build LLM enhance context for auto-generation."""
    parts: list[str] = []

    # Model info
    parts.append(f"Image generation model: {model_type}. Optimize the prompt for this specific model's strengths, requirements, and quirks.")

    # Scene timing
    parts.append(f"Scene timing: {scene.start_time}s to {scene.end_time}s. Frame: {frame_type}.")

    # Concept & style
    concept_text = project.settings.get("concept_text", "")
    style_text = project.settings.get("style_text", "")
    if concept_text:
        parts.append(f"Video concept: {concept_text}")
    if style_text:
        parts.append(f"Visual style: {style_text}")

    # Image direction
    image_direction = project.settings.get("image_direction", "")
    if image_direction and image_direction != "none":
        if image_direction == "custom":
            custom_dir = project.settings.get("custom_image_direction", "")
            if custom_dir:
                parts.append(f"Image direction / art style: {custom_dir}")
        else:
            dir_label = image_direction.replace("_", " ").title()
            parts.append(f"Image direction / art style: {dir_label}")

    # Characters — Klein uses compositional language for reference images
    characters = project.settings.get("characters", [])
    if characters:
        chars_with_images = [c for c in characters[:2] if c.get("image_path")]
        chars_without_images = [c for c in characters[:2] if not c.get("image_path")]

        if chars_with_images:
            ref_labels = ["first", "second", "third", "fourth"]
            parts.append(
                "REFERENCE IMAGES: Character reference photos are being sent with this prompt. "
                "Use COMPOSITIONAL language to reference them — describe what the subject from the "
                "reference image should be doing in the scene. For example: 'the subject from the "
                "first image stands in the doorway' or 'the person from the second image sits at "
                "the table'. Do NOT just say 'Image 1' — describe them compositionally. "
                "Front-load the most important visual elements. Prioritize lighting description."
            )
            for i, c in enumerate(chars_with_images):
                name = c.get("name", "Unnamed")
                desc = c.get("description", "no description")
                label = ref_labels[i] if i < len(ref_labels) else f"reference {i + 1}"
                parts.append(
                    f'The {label} reference image is character "{name}" ({desc}). '
                    f'When this character appears, write "the subject from the {label} image" '
                    f'followed by what they are doing in this scene.'
                )

        for c in chars_without_images:
            parts.append(f'Character "{c.get("name", "Unnamed")}": {c.get("description", "no description")}')

    # Scene lyrics — PRIMARY creative driver
    if lyrics_text:
        parts.append(
            f'SCENE LYRICS (PRIMARY CREATIVE SOURCE — specific objects, people, actions, and settings '
            f'mentioned here MUST appear visually in the image. The lyrics tell you WHAT to show): '
            f'"{lyrics_text}"'
        )

    # Story flow — provides scene composition, camera, and visual diversity
    # Only include if the per-scene use_story_flow flag is set (defaults to True)
    scene_use_flow = scene.parameters.get("use_story_flow", True)
    flow_idea = scene.parameters.get("flow_idea", "")
    if flow_idea and scene_use_flow:
        parts.append(
            f"SCENE STORYBOARD (describes HOW to compose and frame the scene — camera angle, "
            f"composition, location, and mood. Use this alongside the lyrics above to create a "
            f"visually unique scene that depicts the lyrical content): {flow_idea}"
        )

    # Camera action (if set)
    camera_action = scene.parameters.get("camera_action", "")
    if camera_action and camera_action != "none":
        if camera_action == "custom":
            custom_cam = scene.parameters.get("custom_camera_action", "")
            if custom_cam:
                parts.append(f"Requested camera movement: {custom_cam}")
        else:
            parts.append(f"Requested camera movement: {camera_action.replace('_', ' ').title()}")

    # Visual continuity from previous scene (when "Use Last Frame of Previous Scene" is enabled)
    # Skip if "Ignore Previous Scene Image as Reference" is checked
    use_prev_lf = scene.parameters.get("use_prev_scene_last_frame", False)
    ignore_prev_ref = scene.parameters.get("ignore_prev_scene_ref", False)
    if use_prev_lf and prev_scene and frame_type == "first" and not ignore_prev_ref:
        prev_source = prev_scene.parameters.get("scene_source_type", "image")
        prev_prompt = (
            prev_scene.parameters.get("video_prompt")
            or prev_scene.parameters.get("last_frame_prompt")
            or prev_scene.prompt
            or ""
        ) if prev_source == "video" else (
            prev_scene.parameters.get("last_frame_prompt")
            or prev_scene.prompt
            or ""
        )
        parts.append(
            "STYLE CONTINUITY FROM PREVIOUS SCENE: The previous scene's image is provided as a "
            "style reference. Match the overall art style, color palette, lighting mood, and visual "
            "tone of the previous scene — but the CONTENT of this new scene should be DIFFERENT and "
            "UNIQUE. Do NOT recreate or closely copy the previous image's composition or subject "
            "placement. Instead, use the previous scene only as a guide for consistent aesthetic "
            "style across the video. Focus the prompt on what THIS scene depicts according to its "
            "own story flow, lyrics, and concept."
        )
        if prev_prompt:
            parts.append(f'PREVIOUS SCENE (style reference only): "{prev_prompt}"')
        prev_flow = prev_scene.parameters.get("flow_idea", "")
        if prev_flow:
            parts.append(f'THIS SCENE STORY FLOW: "{prev_flow}"')
        parts.append("Generate a prompt for a NEW image that shares the visual style of the previous scene but depicts entirely new content for this scene.")
    elif use_prev_lf and prev_scene and frame_type == "last" and not ignore_prev_ref:
        parts.append(
            "NOTE: The first frame for this scene was carried over from the previous scene's "
            "last frame. This last frame should transition from that visual context into the "
            "current scene's unique content."
        )

    return " | ".join(parts)


async def _build_video_enhance_context(
    project: Project,
    scene: Scene,
    lyrics_text: str,
    video_model_type: str,
    prev_scene: Scene | None = None,
) -> str:
    """Build LLM enhance context for video auto-generation."""
    parts: list[str] = []

    video_mode = scene.parameters.get("video_mode", "single")
    mode_context = (
        "This video uses First Frame / Last Frame mode. The video transitions between two keyframe images."
        if video_mode == "ff_lf"
        else "This video uses a single reference image as input."
    )

    parts.append(
        f"Video generation model: {video_model_type}. Optimize the prompt for this specific model's strengths, requirements, and quirks. "
        f"{mode_context} Scene timing: {scene.start_time}s to {scene.end_time}s. "
        f"Enhance for smooth cinematic video motion and transitions."
    )

    concept_text = project.settings.get("concept_text", "")
    style_text = project.settings.get("style_text", "")
    if concept_text:
        parts.append(f"Video concept: {concept_text}")
    if style_text:
        parts.append(f"Visual style: {style_text}")

    # Image direction
    image_direction = project.settings.get("image_direction", "")
    if image_direction and image_direction != "none":
        if image_direction == "custom":
            custom_dir = project.settings.get("custom_image_direction", "")
            if custom_dir:
                parts.append(f"Image direction / art style: {custom_dir}")
        else:
            dir_label = image_direction.replace("_", " ").title()
            parts.append(f"Image direction / art style: {dir_label}")

    characters = project.settings.get("characters", [])
    if characters:
        char_block = ". ".join(
            f'Character {i + 1}: "{c.get("name", "Unnamed")}" — {c.get("description", "no description")}'
            for i, c in enumerate(characters)
        )
        parts.append(f"Characters: {char_block}")

    # Scene lyrics — PRIMARY creative driver for video content
    if lyrics_text:
        parts.append(
            f'SCENE LYRICS (PRIMARY CREATIVE SOURCE — specific actions, movements, and events '
            f'mentioned here should drive the video motion and content. The lyrics tell you WHAT '
            f'happens in this scene): "{lyrics_text}"'
        )

    # Story flow — provides scene composition and camera direction
    # Only include if the per-scene use_story_flow flag is set (defaults to True)
    scene_use_flow = scene.parameters.get("use_story_flow", True)
    flow_idea = scene.parameters.get("flow_idea", "")
    if flow_idea and scene_use_flow:
        parts.append(
            f"SCENE STORYBOARD (describes HOW to compose and film the scene — camera movement, "
            f"framing, location, and mood. Use this alongside the lyrics above to create "
            f"compelling video motion that depicts the lyrical content): {flow_idea}"
        )

    # Visual continuity from previous scene (skip if ignore_prev_scene_ref is set)
    use_prev_lf = scene.parameters.get("use_prev_scene_last_frame", False)
    ignore_prev_ref = scene.parameters.get("ignore_prev_scene_ref", False)
    if use_prev_lf and prev_scene and not ignore_prev_ref:
        prev_prompt = (
            prev_scene.parameters.get("video_prompt")
            or prev_scene.parameters.get("last_frame_prompt")
            or prev_scene.prompt
            or ""
        )
        if prev_prompt:
            parts.append(
                f"CONTINUITY: The starting frame of this video is the ending frame of the previous scene. "
                f'Previous scene described: "{prev_prompt}". The video should visually continue from that context.'
            )

    return " | ".join(parts)


def _group_words_into_sentences(
    words: list[dict],
    lyrics_text: str = "",
) -> list[list[dict]]:
    """Group words into sentences/phrases — one per lyrics line.

    STRATEGY: If user-provided lyrics text with line breaks is available,
    use those lines as the authoritative phrase boundaries. Each line in
    the lyrics = one indivisible phrase. Whisper word timestamps are matched
    to lyrics lines by counting words per line.

    FALLBACK: If no lyrics text is available, detect phrases via punctuation
    and pauses (for instrumental sections or missing lyrics).

    Returns a list of word-groups, each group being a phrase that must be
    kept together and assigned to one scene.
    """
    import re
    if not words:
        return []

    # ── PRIMARY: Use lyrics lines if available ──────────────────────────
    if lyrics_text and lyrics_text.strip():
        lines = [l.strip() for l in lyrics_text.strip().splitlines() if l.strip()]
        if lines:
            return _match_words_to_lyrics_lines(words, lines)

    # ── FALLBACK: Punctuation and pause detection ───────────────────────
    sentences: list[list[dict]] = []
    current: list[dict] = []

    for i, w in enumerate(words):
        current.append(w)
        word_text = (w.get("word", "") or w.get("value", "") or w.get("text", "")).strip()

        is_last = (i == len(words) - 1)
        if is_last:
            sentences.append(current)
            break

        # Check for sentence-ending punctuation
        has_sentence_end = bool(re.search(r'[.!?…]$', word_text))

        # Check for clause-ending punctuation
        has_clause_end = bool(re.search(r'[,;:\-–—]$', word_text))

        # Check for significant pause after this word
        next_start = words[i + 1].get("start", 0)
        this_end = w.get("end", 0)
        gap = next_start - this_end

        if has_sentence_end or gap > 1.5 or (has_clause_end and gap > 0.4):
            sentences.append(current)
            current = []

    if current:
        sentences.append(current)

    return sentences


def _match_words_to_lyrics_lines(
    words: list[dict],
    lines: list[str],
) -> list[list[dict]]:
    """Match Whisper word timestamps to user-provided lyrics lines.

    Each lyrics line defines an indivisible phrase. We walk through the
    Whisper words and assign them to lines by matching word counts per line.

    WORD ANCHORING: After initial matching, any words that were skipped
    (between groups) are merged into the nearest adjacent group. Single-word
    groups are also merged into their neighbor. This ensures a word like
    "Black" from "Black hat on a wooden chair" is NEVER orphaned.
    """
    import re

    def clean_word(w: str) -> str:
        return re.sub(r'[^a-zA-Z0-9]', '', w).lower()

    whisper_cleaned = []
    for w in words:
        raw = (w.get("word", "") or w.get("value", "") or w.get("text", "")).strip()
        whisper_cleaned.append(clean_word(raw))

    # Track which word indices are assigned to which group
    # This lets us detect orphaned words after matching
    word_to_group: dict[int, int] = {}  # word_index → group_index
    groups: list[list[dict]] = []
    group_ranges: list[tuple[int, int]] = []  # (start_idx, end_idx) for each group
    word_idx = 0

    for line_idx, line in enumerate(lines):
        line_words_clean = [clean_word(w) for w in line.split()]
        expected_count = len(line_words_clean)

        if word_idx >= len(words):
            break

        first_word = line_words_clean[0] if line_words_clean else ""
        best_start = word_idx
        if first_word:
            search_end = min(word_idx + 5, len(words))
            for s in range(word_idx, search_end):
                if whisper_cleaned[s] == first_word:
                    best_start = s
                    break

        group_end = best_start + expected_count

        if line_idx + 1 < len(lines):
            next_line_words = [clean_word(w) for w in lines[line_idx + 1].split()]
            next_first = next_line_words[0] if next_line_words else ""
            if next_first:
                for s in range(max(best_start + 1, group_end - 2), min(group_end + 5, len(words))):
                    if whisper_cleaned[s] == next_first:
                        group_end = s
                        break

        group_end = min(group_end, len(words))

        if best_start < group_end:
            g_idx = len(groups)
            groups.append(words[best_start:group_end])
            group_ranges.append((best_start, group_end))
            for wi in range(best_start, group_end):
                word_to_group[wi] = g_idx
            word_idx = group_end
        else:
            word_idx = best_start

    # Trailing words → last group
    if word_idx < len(words):
        if groups:
            g_idx = len(groups) - 1
            groups[g_idx].extend(words[word_idx:])
            old_start, _ = group_ranges[g_idx]
            group_ranges[g_idx] = (old_start, len(words))
            for wi in range(word_idx, len(words)):
                word_to_group[wi] = g_idx
        else:
            groups.append(words[word_idx:])
            group_ranges.append((word_idx, len(words)))
            for wi in range(word_idx, len(words)):
                word_to_group[wi] = 0

    # ── WORD ANCHORING: merge orphaned words into nearest group ───────
    # Find any word indices NOT assigned to any group
    orphaned: list[int] = [i for i in range(len(words)) if i not in word_to_group]
    if orphaned and groups:
        logger.info(f"[WordAnchor] Found {len(orphaned)} orphaned word(s): "
                     f"{[whisper_cleaned[i] for i in orphaned]}")
        for oi in orphaned:
            # Find the nearest group by proximity to group boundaries
            best_group = 0
            best_dist = float('inf')
            for gi, (gs, ge) in enumerate(group_ranges):
                dist = min(abs(oi - gs), abs(oi - ge))
                if dist < best_dist:
                    best_dist = dist
                    best_group = gi

            # Insert the orphaned word into the group at the right position
            word_obj = words[oi]
            word_time = word_obj.get("start", 0)
            inserted = False
            for pos, gw in enumerate(groups[best_group]):
                if gw.get("start", 0) > word_time:
                    groups[best_group].insert(pos, word_obj)
                    inserted = True
                    break
            if not inserted:
                groups[best_group].append(word_obj)

            word_to_group[oi] = best_group
            logger.info(f"[WordAnchor] Anchored '{whisper_cleaned[oi]}' → group {best_group}")

    # ── Merge single-word groups into neighbors ──────────────────────
    # A single-word group means the matching failed for that word's line.
    # Merge it into the closest adjacent group to prevent orphaned display.
    if len(groups) > 1:
        merged_groups: list[list[dict]] = []
        for gi, group in enumerate(groups):
            if len(group) == 1 and len(groups) > 1:
                # Single-word group — merge with neighbor
                word_text = (group[0].get("word", "") or "").strip()
                if merged_groups:
                    # Merge into previous group
                    merged_groups[-1].extend(group)
                    logger.info(f"[WordAnchor] Merged single-word '{word_text}' into previous group")
                elif gi + 1 < len(groups):
                    # No previous group yet — will prepend to next group
                    groups[gi + 1] = group + groups[gi + 1]
                    logger.info(f"[WordAnchor] Merged single-word '{word_text}' into next group")
                else:
                    merged_groups.append(group)
            else:
                merged_groups.append(group)
        groups = merged_groups

    groups = [g for g in groups if g]
    return groups


def _get_scene_lyrics(scene: Scene, words: list[dict], lyrics_text: str = "") -> str:
    """Extract lyrics text for a scene's time range from word timestamps.

    Uses sentence-aware assignment with WORD ANCHORING:

    1. Groups all words into sentences/phrases (by lyrics lines or punctuation)
    2. Word anchoring merges any orphaned single words into their nearest phrase
    3. For each phrase, >50% duration overlap determines scene assignment
    4. Entire phrases are assigned atomically — never split

    This ensures that if "Black hat on a wooden chair" is a sentence where
    "Black" barely starts before the scene boundary, the ENTIRE sentence
    stays in the scene where most of its words are spoken.
    """
    if not words:
        return ""

    sentences = _group_words_into_sentences(words, lyrics_text=lyrics_text)
    if not sentences:
        return ""

    scene_words: list[dict] = []
    scene_name = getattr(scene, 'name', f'Scene@{scene.start_time:.1f}')

    for s_idx, sentence in enumerate(sentences):
        if not sentence:
            continue

        # Calculate the sentence's time span
        sent_start = sentence[0].get("start", 0)
        sent_end = sentence[-1].get("end", 0)

        # Calculate how much of the sentence overlaps with this scene
        overlap_start = max(sent_start, scene.start_time)
        overlap_end = min(sent_end, scene.end_time)
        overlap = max(0, overlap_end - overlap_start)

        sent_duration = max(sent_end - sent_start, 0.01)
        overlap_pct = overlap / sent_duration if sent_duration > 0 else 0

        # Log for first few scenes to help debug orphaned words
        if scene.order_index is not None and scene.order_index < 3:
            sent_text = " ".join(
                (w.get("word", "") or "").strip() for w in sentence
            )
            logger.debug(
                f"[SceneLyrics] {scene_name} (idx={scene.order_index}, "
                f"{scene.start_time:.2f}-{scene.end_time:.2f}s) | "
                f"phrase[{s_idx}] ({len(sentence)} words): \"{sent_text}\" "
                f"({sent_start:.2f}-{sent_end:.2f}s) | "
                f"overlap={overlap:.2f}s/{sent_duration:.2f}s = {overlap_pct:.1%} "
                f"{'→ INCLUDED' if overlap_pct >= 0.5 else '→ excluded'}"
            )

        # The sentence belongs to this scene if MORE THAN HALF of its
        # duration falls within the scene boundaries
        if overlap >= sent_duration * 0.5:
            scene_words.extend(sentence)

    if not scene_words:
        return ""

    return " ".join(w["word"] for w in scene_words).strip()


@router.post(
    "/auto",
    response_model=AutoGenerateResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Auto-generate scene content",
)
async def auto_generate(
    project_id: UUID,
    req: AutoGenerateRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> AutoGenerateResponse:
    """Auto-generate images (and optionally videos) for scenes.

    Modes:
    - all_images: Queue first-frame image generation for every scene.
    - empty_only: Queue first-frame image generation only for scenes missing a chosen image.
    - enhanced_all: Generate video flow, LLM-enhance prompts, then queue FF images,
      LF images (if FF/LF mode), and videos for every scene.
    - enhanced_missing: Like enhanced_all but only for scenes/frames that are missing.

    Jobs are queued with ascending priority so they execute in scene order.
    Enhanced modes call the LLM to create/improve prompts before queuing.

    Args:
        project_id: UUID of the project.
        req: Auto generation request with mode.
        session: Database session.

    Returns:
        Summary of jobs created.
    """
    try:
        project = await _get_project_or_404(project_id, session)

        # Load all scenes sorted by order
        scenes_stmt = (
            select(Scene)
            .where(Scene.project_id == project_id)
            .order_by(Scene.order_index)
        )
        scenes_result = await session.execute(scenes_stmt)
        scenes = list(scenes_result.scalars().all())

        if not scenes:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No scenes found in project",
            )

        # Load settings for LLM access and model types
        settings_stmt = select(AppSettings).where(AppSettings.id == 1)
        settings_result = await session.execute(settings_stmt)
        app_settings = settings_result.scalars().first()

        image_model = (app_settings.image_model_type if app_settings else None) or "flux2_klein_dev_9b"
        video_model = (app_settings.video_model_type if app_settings else None) or "ltx_2.3"

        # Get project resolution
        res_w = project.settings.get("resolution_width", 1536)
        res_h = project.settings.get("resolution_height", 864)

        # Load lyrics for enhance context
        lyrics_stmt = select(Lyrics).where(Lyrics.project_id == project_id)
        lyrics_result = await session.execute(lyrics_stmt)
        lyrics_record = lyrics_result.scalars().first()
        lyrics_words: list[dict] = lyrics_record.words if lyrics_record else []
        user_lyrics_text = ""
        if lyrics_record:
            user_lyrics_text = (getattr(lyrics_record, "initial_text", "") or "").strip()
            if not user_lyrics_text:
                user_lyrics_text = (lyrics_record.full_text or "").strip()

        is_enhanced = req.mode in ("enhanced_all", "enhanced_missing")
        only_missing = req.mode in ("empty_only", "enhanced_missing")

        # For enhanced modes, we need LLM access
        enhancer = None
        llm_provider = None
        llm_api_key = None
        llm_model = None
        image_sys_override = None
        video_sys_override = None
        if is_enhanced and app_settings:
            from backend.services.llm.prompt_enhancer import PromptEnhancer
            from backend.api.settings import resolve_llm_config
            enhancer = PromptEnhancer()
            try:
                llm_provider, llm_api_key, llm_model = resolve_llm_config(app_settings)
            except Exception:
                pass  # No LLM configured — enhanced mode will skip enhancement

            # Resolve system prompt overrides for image and video
            img_overrides = app_settings.image_system_prompt_overrides or {}
            img_override_entry = img_overrides.get(image_model, {})
            if isinstance(img_override_entry, dict) and img_override_entry.get("enabled") and img_override_entry.get("text", "").strip():
                image_sys_override = img_override_entry["text"]

            vid_overrides = app_settings.video_system_prompt_overrides or {}
            vid_override_entry = vid_overrides.get(video_model, {})
            if isinstance(vid_override_entry, dict) and vid_override_entry.get("enabled") and vid_override_entry.get("text", "").strip():
                video_sys_override = vid_override_entry["text"]

        # Auto-generate video flow if no scenes have flow ideas
        # This ensures each scene gets a unique storyboard idea for differentiation
        if llm_api_key and llm_provider:
            await _ensure_video_flow(
                project, scenes, session, llm_provider, llm_api_key, llm_model
            )
            # Re-read scenes to get updated flow ideas
            scenes_result = await session.execute(scenes_stmt)
            scenes = list(scenes_result.scalars().all())

        created_jobs: list[Job] = []
        enhanced_count = 0
        skipped_count = 0
        priority = 0  # ascending priority = scene order

        prev_scene: Scene | None = None
        for scene in scenes:
            scene_lyrics = _get_scene_lyrics(scene, lyrics_words, lyrics_text=user_lyrics_text)
            uses_ff_lf = _scene_uses_ff_lf(scene)
            has_ff = _scene_has_first_frame(scene)
            has_lf = _scene_has_last_frame(scene)

            # ── First Frame Image ──
            needs_ff = not has_ff if only_missing else True
            if needs_ff:
                prompt = scene.prompt or f"Scene {scene.order_index + 1}"
                # Include character image refs + scene extras
                characters_list = project.settings.get("characters", [])
                char_aids = []
                if characters_list:
                    for c in characters_list[:2]:
                        cp = c.get("image_path", "")
                        if cp:
                            aid_stmt = select(Asset).where(Asset.project_id == project_id, Asset.rel_path == cp)
                            aid_r = await session.execute(aid_stmt)
                            aid_a = aid_r.scalars().first()
                            if aid_a:
                                char_aids.append(str(aid_a.id))
                extra_ids = _collect_ref_asset_ids(scene, "first")
                ref_ids = char_aids + extra_ids
                wf_type = _auto_workflow_type(len(ref_ids))

                if is_enhanced and enhancer and llm_api_key:
                    try:
                        context = await _build_auto_enhance_context(
                            project, scene, scene_lyrics, "first", image_model, prev_scene
                        )
                        prompt = await asyncio.to_thread(
                            enhancer.enhance, prompt, context, llm_provider, llm_api_key, llm_model,
                            False, image_sys_override, image_model, "first",
                        )
                        enhanced_count += 1
                    except Exception as e:
                        logger.warning(f"Auto-enhance failed for scene {scene.order_index}: {e}")

                # Save prompt to scene so it appears in the editor UI
                scene.prompt = prompt
                scene_params = dict(scene.parameters or {})
                scene.parameters = scene_params

                job = Job(
                    project_id=project_id,
                    scene_id=scene.id,
                    job_type=JobType.IMAGE,
                    status=JobStatus.PENDING,
                    priority=priority,
                    parameters={
                        "workflow_type": wf_type,
                        "prompt": prompt,
                        "width": res_w,
                        "height": res_h,
                        "reference_asset_ids": ref_ids,
                        "frame_type": "first",
                        "auto_save_preview": True,  # Signal dispatcher to auto-set as chosen
                    },
                )
                session.add(job)
                created_jobs.append(job)
                priority += 1
            else:
                skipped_count += 1

            # ── Last Frame Image (only if FF/LF mode) ──
            if uses_ff_lf or (is_enhanced and not only_missing):
                needs_lf = not has_lf if only_missing else True
                if needs_lf and (uses_ff_lf or not only_missing):
                    lf_prompt = scene.parameters.get("last_frame_prompt", "") or scene.prompt or f"Scene {scene.order_index + 1} - last frame"
                    ref_ids_lf = _collect_ref_asset_ids(scene, "last")
                    wf_type_lf = _auto_workflow_type(len(ref_ids_lf))

                    if is_enhanced and enhancer and llm_api_key:
                        try:
                            context = await _build_auto_enhance_context(
                                project, scene, scene_lyrics, "last", image_model, prev_scene
                            )
                            lf_prompt = await asyncio.to_thread(
                                enhancer.enhance, lf_prompt, context, llm_provider, llm_api_key, llm_model,
                                False, image_sys_override, image_model, "last",
                            )
                            enhanced_count += 1
                        except Exception as e:
                            logger.warning(f"Auto-enhance LF failed for scene {scene.order_index}: {e}")

                    # Save last frame prompt to scene parameters
                    scene_params = dict(scene.parameters or {})
                    scene_params["last_frame_prompt"] = lf_prompt
                    scene.parameters = scene_params

                    job = Job(
                        project_id=project_id,
                        scene_id=scene.id,
                        job_type=JobType.IMAGE,
                        status=JobStatus.PENDING,
                        priority=priority,
                        parameters={
                            "workflow_type": wf_type_lf,
                            "prompt": lf_prompt,
                            "width": res_w,
                            "height": res_h,
                            "reference_asset_ids": ref_ids_lf,
                            "frame_type": "last",
                            "auto_save_preview": True,
                        },
                    )
                    session.add(job)
                    created_jobs.append(job)
                    priority += 1
                else:
                    skipped_count += 1

            # ── Video (only for enhanced modes) ──
            if is_enhanced:
                video_prompt = scene.parameters.get("video_prompt", "") or scene.prompt or f"Cinematic scene {scene.order_index + 1}"

                if is_enhanced and enhancer and llm_api_key:
                    try:
                        vid_context = await _build_video_enhance_context(
                            project, scene, scene_lyrics, video_model, prev_scene
                        )
                        video_prompt = await asyncio.to_thread(
                            enhancer.enhance, video_prompt, vid_context, llm_provider, llm_api_key, llm_model,
                            True, video_sys_override, video_model,
                        )
                        enhanced_count += 1
                    except Exception as e:
                        logger.warning(f"Auto-enhance video failed for scene {scene.order_index}: {e}")

                # Save video prompt to scene parameters
                scene_params = dict(scene.parameters or {})
                scene_params["video_prompt"] = video_prompt
                scene.parameters = scene_params

                duration = scene.end_time - scene.start_time
                vid_wf = "ltx_fflf" if uses_ff_lf else "ltx_i2v"

                job = Job(
                    project_id=project_id,
                    scene_id=scene.id,
                    job_type=JobType.VIDEO,
                    status=JobStatus.PENDING,
                    priority=priority,
                    parameters={
                        "workflow_type": vid_wf,
                        "prompt": video_prompt,
                        "width": res_w,
                        "height": res_h,
                        "duration": duration,
                        "framerate": 24,
                        "needs_scene_images": True,  # Signal: wait for this scene's images first
                    },
                )
                session.add(job)
                created_jobs.append(job)
                priority += 1

            # Track previous scene for continuity context in next iteration
            prev_scene = scene

        await session.commit()
        logger.info(
            f"Auto-generate ({req.mode}): Created {len(created_jobs)} jobs, "
            f"enhanced {enhanced_count} prompts, skipped {skipped_count}"
        )

        # Notify the dispatcher
        job_queue: JobQueue = request.app.state.job_queue
        job_queue.notify()

        return AutoGenerateResponse(
            jobs_created=len(created_jobs),
            job_ids=[str(j.id) for j in created_jobs],
            enhanced_count=enhanced_count,
            skipped_count=skipped_count,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in auto-generate: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to auto-generate: {str(e)}",
        )


# ── Sequential Auto-Generation ──────────────────────────────────────────
# Processes scenes one at a time, waiting for each job to complete before
# moving to the next.  Needed for modes that depend on the previous scene's
# generated output (e.g. use previous video's last frame as first frame).

class SeqAutoGenRequest(BaseModel):
    """Request model for sequential auto-generation."""
    mode: str  # 'all_video_fflf', 'all_video_v2v', 'all_video_single', 'all_images', 'missing_videos_single', 'missing_images_independent'
    override_full_set: bool = False  # If True, regenerate ALL scenes (ignore existing images/videos)
    vocals_only_audio: bool = False  # If True, send only vocal stems to video generator (better lip-sync)
    skip_audio_mux: bool = False  # If True, keeps LTX model-generated audio (better for lip-sync testing)
    two_pass: bool = False  # If True, use two-pass image generation (scene composition → character composite)
    use_story_flow: bool = True  # If True, include video flow ideas in LLM enhance context


class SeqAutoGenStatusResponse(BaseModel):
    """Response model for sequential auto-gen status."""
    status: str  # 'idle', 'running', 'done', 'failed'
    mode: Optional[str] = None
    total_scenes: int = 0
    completed_scenes: int = 0
    current_scene_name: Optional[str] = None
    current_step: Optional[str] = None  # 'enhancing', 'generating_image', 'generating_video'
    error: Optional[str] = None


# In-memory tracking per project
_seq_auto_jobs: dict[str, dict] = {}


@router.post(
    "/auto-sequential",
    response_model=SeqAutoGenStatusResponse,
    summary="Start sequential auto-generation",
)
async def start_sequential_auto_gen(
    project_id: UUID,
    req: SeqAutoGenRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> SeqAutoGenStatusResponse:
    """Start sequential auto-generation of missing scenes.

    Unlike /auto, this processes scenes one at a time and waits for each
    job to complete before starting the next.  This is required for modes
    where the next scene depends on the previous scene's output.

    Modes:
    - all_video_fflf: Generate videos using previous video's last frame as
      first frame.  Generates first frame image for scene 1 if missing.
    - all_video_single: Generate videos from single first-frame image (no LF).
      Generates first frame image for each scene if missing.
    - all_images: Generate first-frame images only (still-image video).
    - missing_videos_single: Generate missing videos using existing first-frame
      images. Only generates images for scenes that have no first frame at all.
    - missing_images_independent: Generate first-frame images for scenes
      missing them, ignoring previous scene image as reference (each scene
      generated independently — only character refs used).
    """
    project = await _get_project_or_404(project_id, session)
    pid = str(project_id)

    # Don't start if already running
    existing = _seq_auto_jobs.get(pid)
    if existing and existing.get("status") == "running":
        return SeqAutoGenStatusResponse(**existing)

    # Count eligible scenes
    scenes_stmt = select(Scene).where(Scene.project_id == project_id).order_by(Scene.order_index)
    scenes_result = await session.execute(scenes_stmt)
    scenes = list(scenes_result.scalars().all())

    if not scenes:
        raise HTTPException(status_code=400, detail="No scenes found in project")

    # Cancel any stale PENDING jobs from previous auto-gen runs for this project.
    # The dispatch loop processes ALL pending jobs regardless of origin, so leftover
    # image jobs from a cancelled/failed previous run would be picked up and confuse
    # the current run (e.g., user expects only video jobs but sees old image jobs).
    stale_stmt = (
        select(Job)
        .where(
            Job.project_id == project_id,
            Job.status == JobStatus.PENDING,
        )
    )
    stale_result = await session.execute(stale_stmt)
    stale_jobs = list(stale_result.scalars().all())
    if stale_jobs:
        for sj in stale_jobs:
            sj.status = JobStatus.CANCELLED
            sj.error = "Cancelled: new sequential auto-gen started"
        await session.commit()
        logger.info(
            f"Cancelled {len(stale_jobs)} stale PENDING jobs for project {project_id} "
            f"before starting sequential auto-gen (mode={req.mode})"
        )

    _seq_auto_jobs[pid] = {
        "status": "running",
        "mode": req.mode,
        "total_scenes": len(scenes),
        "completed_scenes": 0,
        "current_scene_name": None,
        "current_step": "starting",
        "error": None,
    }

    from backend.database import async_session as bg_session_factory

    asyncio.create_task(
        _run_sequential_auto_gen(
            pid, project_id, req.mode,
            request.app.state.job_queue,
            bg_session_factory,
            comfy_dispatcher=request.app.state.comfy_dispatcher,
            override_full_set=req.override_full_set,
            vocals_only_audio=req.vocals_only_audio,
            skip_audio_mux=req.skip_audio_mux,
            two_pass=req.two_pass,
            use_story_flow=req.use_story_flow,
        )
    )

    return SeqAutoGenStatusResponse(**_seq_auto_jobs[pid])


@router.get(
    "/auto-sequential/status",
    response_model=SeqAutoGenStatusResponse,
    summary="Get sequential auto-gen status",
)
async def get_sequential_auto_gen_status(
    project_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> SeqAutoGenStatusResponse:
    """Poll the status of a sequential auto-generation run."""
    await _get_project_or_404(project_id, session)
    pid = str(project_id)
    info = _seq_auto_jobs.get(pid)
    if not info:
        return SeqAutoGenStatusResponse(status="idle")
    return SeqAutoGenStatusResponse(**info)


@router.post(
    "/auto-sequential/cancel",
    response_model=SeqAutoGenStatusResponse,
    summary="Cancel sequential auto-gen",
)
async def cancel_sequential_auto_gen(
    project_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> SeqAutoGenStatusResponse:
    """Cancel a running sequential auto-generation."""
    await _get_project_or_404(project_id, session)
    pid = str(project_id)
    info = _seq_auto_jobs.get(pid)
    if info and info.get("status") == "running":
        info["status"] = "cancelled"
        info["current_step"] = "cancelled by user"
    return SeqAutoGenStatusResponse(**(info or {"status": "idle"}))


async def _wait_for_job(
    job_id: UUID,
    session_factory,
    timeout: float = 600,
) -> bool:
    """Wait for a job to reach DONE or FAILED status, polling every 2s.

    Returns True if DONE, False if FAILED or timeout.

    Default timeout is 600s (10 min) for image jobs.  Callers should pass
    a longer timeout for video jobs (1800s recommended) to account for
    RunPod cold starts, LTX generation time, and post-processing (trim,
    color correction, last-frame extraction).
    """
    elapsed = 0.0
    while elapsed < timeout:
        await asyncio.sleep(2)
        elapsed += 2
        async with session_factory() as session:
            job = await session.get(Job, job_id)
            if not job:
                return False
            if job.status in (JobStatus.DONE, "done"):
                return True
            if job.status in (JobStatus.FAILED, "failed"):
                logger.warning(f"Sequential auto-gen: Job {job_id} failed: {job.error}")
                return False
            if job.status in (JobStatus.CANCELLED, "cancelled"):
                logger.warning(f"Sequential auto-gen: Job {job_id} was cancelled")
                return False
    logger.warning(f"Sequential auto-gen: Job {job_id} timed out after {timeout}s")
    return False


# Timeout constants for auto-gen job waits
_IMAGE_JOB_TIMEOUT = 600    # 10 minutes — enough for image generation
_VIDEO_JOB_TIMEOUT = 1800   # 30 minutes — accounts for RunPod cold start + LTX + post-processing


async def _wait_for_jobs_batch(
    job_ids: list[UUID],
    session_factory,
    pid: str,
    timeout: float = 600,
    completed_offset: int = 0,
) -> tuple[int, int]:
    """Wait for a batch of jobs to all reach terminal status.

    Polls every 2s and updates the in-memory progress tracker.

    Args:
        completed_offset: Number of scenes already completed from previous
                         windows.  Added to the current batch count so the
                         progress bar shows cumulative progress (e.g., 6+1=7
                         instead of resetting to 1).

    Returns (succeeded_count, failed_count).
    """
    remaining = set(job_ids)
    succeeded = 0
    failed = 0
    elapsed = 0.0

    while remaining and elapsed < timeout:
        await asyncio.sleep(2)
        elapsed += 2

        # Check cancellation
        if _seq_auto_jobs.get(pid, {}).get("status") != "running":
            return succeeded, failed

        done_this_round: set[UUID] = set()
        async with session_factory() as session:
            for jid in remaining:
                job = await session.get(Job, jid)
                if not job:
                    done_this_round.add(jid)
                    failed += 1
                elif job.status in (JobStatus.DONE, "done"):
                    done_this_round.add(jid)
                    succeeded += 1
                elif job.status in (JobStatus.FAILED, "failed"):
                    done_this_round.add(jid)
                    failed += 1
                    logger.warning(f"Batch auto-gen: Job {jid} failed: {job.error}")

        remaining -= done_this_round

        # Update progress tracker with cumulative offset
        if pid in _seq_auto_jobs:
            _seq_auto_jobs[pid]["completed_scenes"] = completed_offset + succeeded + failed

    if remaining:
        logger.warning(f"Batch auto-gen: {len(remaining)} jobs timed out after {timeout}s")
        failed += len(remaining)

    return succeeded, failed


def _count_capable_workers(comfy_dispatcher, job_type: str) -> int:
    """Count healthy ComfyUI workers capable of the given job type.

    Returns at least 1 so the window always makes progress.
    """
    if not comfy_dispatcher:
        return 1

    required_caps = {"ltx"} if job_type == "video" else {"klein"}
    healthy = [w for w in comfy_dispatcher.workers.values() if w.healthy]
    capable = [w for w in healthy if required_caps.issubset(w.capabilities)]
    # Fall back to all healthy workers if none have explicit caps
    if not capable:
        capable = healthy
    return max(1, len(capable))


async def _run_windowed_batch(
    pid: str,
    project_id: UUID,
    mode: str,
    scenes: list,
    job_queue: "JobQueue",
    session_factory,
    comfy_dispatcher=None,
    override_full_set: bool = False,
    project=None,
    lyrics_words: list = None,
    enhancer=None,
    llm_provider=None,
    llm_api_key=None,
    llm_model=None,
    image_model: str = "flux2_klein_dev_9b",
    video_model: str = "ltx_2.3",
    image_sys_override=None,
    video_sys_override=None,
    res_w: int = 1536,
    res_h: int = 864,
    vocals_only_audio: bool = False,
    skip_audio_mux: bool = False,
    two_pass: bool = False,
    user_lyrics_text: str = "",
    use_story_flow: bool = True,
):
    """Process scenes via continuous dispatch (pool of N = worker count).

    Phase 1: Prepares all eligible scenes (resolve images, enhance prompts).
    Phase 2: Fills N worker slots initially, then as each job completes,
    immediately submits the next pending one. No idle workers between jobs.

    Handles both missing_videos_single and missing_images_independent modes.
    """
    lyrics_words = lyrics_words or []

    # Determine window size from available workers
    job_type = "video" if mode == "missing_videos_single" else "image"
    window_size = _count_capable_workers(comfy_dispatcher, job_type)
    logger.info(
        f"Windowed batch: mode={mode}, window_size={window_size}, "
        f"total_scenes={len(scenes)}"
    )

    # Phase 1: Collect eligible scenes and prepare them
    # (resolve images, enhance prompts — all prep work before submission)
    eligible: list[dict] = []  # Each entry has scene data + prepared job params

    for i, scene in enumerate(scenes):
        if _seq_auto_jobs.get(pid, {}).get("status") != "running":
            return

        scene_name = scene.name or f"Scene {scene.order_index + 1}"
        _seq_auto_jobs[pid]["current_scene_name"] = scene_name
        _seq_auto_jobs[pid]["current_step"] = f"preparing scene {i + 1}/{len(scenes)}"

        async with session_factory() as session:
            scene = await session.get(Scene, scene.id)
            project_fresh = await session.get(Project, project_id)
            if not scene:
                continue

            scene_lyrics = _get_scene_lyrics(scene, lyrics_words, lyrics_text=user_lyrics_text)
            has_ff = _scene_has_first_frame(scene)
            has_video = bool(scene.parameters.get("chosen_video_path"))

            # Collect reference IDs: character images + scene extras
            characters = (project_fresh.settings or {}).get("characters", []) if project_fresh else []
            char_asset_ids = await _resolve_character_asset_ids(
                characters, project_id, session, max_chars=2
            )
            extra_ref_ids = _collect_ref_asset_ids(scene, "first")
            ref_ids = char_asset_ids + extra_ref_ids  # Characters first = Image 1, Image 2
            wf_type = _auto_workflow_type(len(ref_ids))

            # Reset flags — auto-gen starts fresh, user can set these manually later
            scene_params = dict(scene.parameters or {})
            if mode != "missing_images_independent":
                scene_params["ignore_prev_scene_ref"] = False
            scene_params["use_prev_scene_last_frame"] = False
            # Persist use_story_flow so per-scene checkbox reflects auto-gen setting
            scene_params["use_story_flow"] = use_story_flow
            # Set character indices so UI shows them as selected
            if characters:
                scene_params["image_refs_first"] = {
                    "characterIndices": list(range(min(2, len(characters)))),
                    "extras": scene_params.get("image_refs_first", {}).get("extras", []),
                }
            scene.parameters = scene_params
            await session.commit()

            if mode == "missing_videos_single":
                # Skip scenes that already have video
                if has_video and not override_full_set:
                    logger.info(f"Windowed batch: Scene {i} already has video, skipping")
                    continue

                # Resolve first frame image if missing
                if not has_ff:
                    # Check GenerationHistory for existing FF images
                    hist_stmt = (
                        select(GenerationHistory)
                        .where(
                            GenerationHistory.scene_id == scene.id,
                            GenerationHistory.job_type == JobType.IMAGE,
                            GenerationHistory.status == "completed",
                            GenerationHistory.output_path.isnot(None),
                        )
                        .order_by(GenerationHistory.completed_at.desc())
                    )
                    hist_result = await session.execute(hist_stmt)
                    existing_images = list(hist_result.scalars().all())
                    ff_images = [
                        h for h in existing_images
                        if h.parameters.get("frame_type", "first") == "first"
                    ]
                    if ff_images:
                        adopted_path = ff_images[0].output_path
                        scene_params = dict(scene.parameters or {})
                        scene_params["chosen_image_path"] = adopted_path
                        scene.parameters = scene_params
                        await session.commit()
                        has_ff = True
                        logger.info(f"Windowed batch: Scene {i} auto-adopted FF image: {adopted_path}")

                # If still no FF, generate one (sequential — must wait)
                if not has_ff:
                    _seq_auto_jobs[pid]["current_step"] = f"generating first frame for scene {i + 1}"
                    prompt = scene.prompt or f"Scene {scene.order_index + 1}"
                    if enhancer and llm_api_key:
                        try:
                            context = await _build_auto_enhance_context(
                                project_fresh, scene, scene_lyrics, "first", image_model, None
                            )
                            enhance_args = (enhancer.enhance, prompt, context, llm_provider, llm_api_key, llm_model,
                                False, image_sys_override, image_model, "first")
                            if two_pass and ref_ids:
                                enhance_args += (None, "base")
                            prompt = await asyncio.to_thread(*enhance_args)
                        except Exception as e:
                            logger.warning(f"Windowed batch: enhance FF failed scene {i}: {e}")

                    scene.prompt = prompt
                    await session.commit()

                    img_params = {
                        "workflow_type": wf_type,
                        "prompt": prompt,
                        "width": res_w, "height": res_h,
                        "reference_asset_ids": ref_ids,
                        "frame_type": "first",
                        "auto_save_preview": True,
                    }
                    img_params = _apply_two_pass_to_job_params(img_params, two_pass, ref_ids)
                    await _persist_two_pass_on_scene(scene, session, two_pass, ref_ids)
                    img_job = Job(
                        project_id=project_id,
                        scene_id=scene.id,
                        job_type=JobType.IMAGE,
                        status=JobStatus.PENDING,
                        priority=0,
                        parameters=img_params,
                    )
                    session.add(img_job)
                    await session.commit()
                    await session.refresh(img_job)
                    job_queue.notify()

                    ok = await _wait_for_job(img_job.id, session_factory)
                    if not ok:
                        _seq_auto_jobs[pid]["status"] = "failed"
                        _seq_auto_jobs[pid]["error"] = f"FF image failed for {scene_name}"
                        return

                # Prepare video job params — expire + re-read to pick up
                # chosen_image_path set by dispatcher in a different session
                scene_id = scene.id
                session.expire(scene)
                scene = await session.get(Scene, scene_id)
                scene_params = dict(scene.parameters or {})
                scene_params["video_mode"] = "single"
                scene_params["use_prev_lf_as_ff"] = False
                scene_params["scene_source_type"] = "video"
                scene.parameters = scene_params
                await session.commit()

                _seq_auto_jobs[pid]["current_step"] = f"enhancing video prompt {i + 1}/{len(scenes)}"
                # Always re-derive video prompt from the current image prompt
                # (not from a stale video_prompt saved by a previous auto-gen run)
                video_prompt = scene.prompt or f"Cinematic scene {scene.order_index + 1}"
                if enhancer and llm_api_key:
                    try:
                        vid_ctx = await _build_video_enhance_context(
                            project_fresh, scene, scene_lyrics, video_model, None
                        )
                        video_prompt = await asyncio.to_thread(
                            enhancer.enhance, video_prompt, vid_ctx, llm_provider, llm_api_key, llm_model,
                            True, video_sys_override, video_model,
                        )
                    except Exception as e:
                        logger.warning(f"Windowed batch: enhance video failed scene {i}: {e}")

                scene_params = dict(scene.parameters or {})
                scene_params["video_prompt"] = video_prompt
                scene.parameters = scene_params
                await session.commit()

                duration = scene.end_time - scene.start_time
                eligible.append({
                    "scene_id": scene.id,
                    "scene_name": scene_name,
                    "scene_index": i,
                    "job_type": JobType.VIDEO,
                    "parameters": {
                        "workflow_type": "ltx_i2v",
                        "prompt": video_prompt,
                        "width": res_w, "height": res_h,
                        "duration": duration,
                        "framerate": 24,
                        "vocals_only_audio": vocals_only_audio,
                        "use_stem_selections": not vocals_only_audio,
                        "skip_audio_mux": skip_audio_mux,
                    },
                })

            elif mode == "missing_images_independent":
                if has_ff and not override_full_set:
                    logger.info(f"Windowed batch: Scene {i} already has first frame, skipping")
                    continue

                # Set ignore flag
                scene_params = dict(scene.parameters or {})
                scene_params["ignore_prev_scene_ref"] = True
                scene.parameters = scene_params
                await session.commit()

                _seq_auto_jobs[pid]["current_step"] = f"enhancing image prompt {i + 1}/{len(scenes)}"
                prompt = scene.prompt or f"Scene {scene.order_index + 1}"
                if enhancer and llm_api_key:
                    try:
                        context = await _build_auto_enhance_context(
                            project_fresh, scene, scene_lyrics, "first", image_model, None
                        )
                        enhance_args = (enhancer.enhance, prompt, context, llm_provider, llm_api_key, llm_model,
                            False, image_sys_override, image_model, "first")
                        if two_pass and ref_ids:
                            enhance_args += (None, "base")
                        prompt = await asyncio.to_thread(*enhance_args)
                    except Exception as e:
                        logger.warning(f"Windowed batch: enhance failed scene {i}: {e}")

                scene.prompt = prompt
                scene_params = dict(scene.parameters or {})
                scene_params["scene_source_type"] = "image"
                scene_params["ignore_prev_scene_ref"] = True
                if two_pass and ref_ids:
                    scene_params["two_pass_enabled"] = True
                scene.parameters = scene_params
                await session.commit()

                img_params = {
                    "workflow_type": wf_type,
                    "prompt": prompt,
                    "width": res_w, "height": res_h,
                    "reference_asset_ids": ref_ids,
                    "frame_type": "first",
                    "auto_save_preview": True,
                }
                img_params = _apply_two_pass_to_job_params(img_params, two_pass, ref_ids)
                eligible.append({
                    "scene_id": scene.id,
                    "scene_name": scene_name,
                    "scene_index": i,
                    "job_type": JobType.IMAGE,
                    "parameters": img_params,
                })

    if not eligible:
        _seq_auto_jobs[pid]["status"] = "done"
        _seq_auto_jobs[pid]["completed_scenes"] = len(scenes)
        _seq_auto_jobs[pid]["current_step"] = "complete — nothing to generate"
        _seq_auto_jobs[pid]["current_scene_name"] = None
        return

    # Phase 2: Continuous dispatch — fill all worker slots, and as each job
    # completes immediately submit the next pending one. No idle workers.
    total_eligible = len(eligible)
    total_succeeded = 0
    total_failed = 0
    next_to_submit = 0  # index into eligible[] for next job to submit

    _seq_auto_jobs[pid]["total_scenes"] = total_eligible

    # Active jobs: map job_id -> scene_name (for progress display)
    active_jobs: dict[UUID, str] = {}
    batch_timeout = _VIDEO_JOB_TIMEOUT if job_type == "video" else _IMAGE_JOB_TIMEOUT
    elapsed = 0.0

    async def _submit_next() -> bool:
        """Submit the next eligible job. Returns True if a job was submitted."""
        nonlocal next_to_submit
        if next_to_submit >= total_eligible:
            return False

        entry = eligible[next_to_submit]
        next_to_submit += 1

        # Retry loop for SQLite "database is locked" under concurrency
        for _attempt in range(4):
            try:
                async with session_factory() as session:
                    job = Job(
                        project_id=project_id,
                        scene_id=entry["scene_id"],
                        job_type=entry["job_type"],
                        status=JobStatus.PENDING,
                        priority=0,
                        parameters=entry["parameters"],
                    )
                    session.add(job)
                    await session.flush()
                    job_id = job.id
                    await session.commit()
                break
            except SAOperationalError as exc:
                if "database is locked" in str(exc) and _attempt < 3:
                    logger.warning(
                        f"DB locked on job insert (attempt {_attempt + 1}/4), retrying..."
                    )
                    await asyncio.sleep(0.5 * (_attempt + 1))
                else:
                    raise

        active_jobs[job_id] = entry["scene_name"]
        job_queue.notify()
        logger.info(
            f"Continuous dispatch: submitted job {job_id} for "
            f"{entry['scene_name']} ({next_to_submit}/{total_eligible})"
        )
        return True

    # Fill initial worker slots
    for _ in range(window_size):
        if next_to_submit >= total_eligible:
            break
        if _seq_auto_jobs.get(pid, {}).get("status") != "running":
            return
        await _submit_next()

    _seq_auto_jobs[pid]["current_step"] = (
        f"generating ({len(active_jobs)} active, "
        f"0/{total_eligible} complete)"
    )
    _seq_auto_jobs[pid]["current_scene_name"] = ", ".join(active_jobs.values())

    logger.info(
        f"Continuous dispatch: initial fill {len(active_jobs)} jobs, "
        f"{total_eligible} total (mode={mode})"
    )

    # Poll loop: check for completions and immediately refill slots
    while active_jobs and elapsed < batch_timeout:
        await asyncio.sleep(2)
        elapsed += 2

        # Check cancellation
        if _seq_auto_jobs.get(pid, {}).get("status") != "running":
            return

        # Check which active jobs have completed
        completed_this_round: list[UUID] = []
        async with session_factory() as session:
            for jid in list(active_jobs.keys()):
                job = await session.get(Job, jid)
                if not job:
                    completed_this_round.append(jid)
                    total_failed += 1
                elif job.status in (JobStatus.DONE, "done"):
                    completed_this_round.append(jid)
                    total_succeeded += 1
                elif job.status in (JobStatus.FAILED, "failed"):
                    completed_this_round.append(jid)
                    total_failed += 1
                    logger.warning(
                        f"Continuous dispatch: Job {jid} failed: {job.error}"
                    )

        # Remove completed jobs and immediately submit replacements
        for jid in completed_this_round:
            del active_jobs[jid]
            # Reset elapsed timeout since we're making progress
            elapsed = 0.0
            # Immediately fill the freed slot
            if _seq_auto_jobs.get(pid, {}).get("status") == "running":
                await _submit_next()

        # Update progress tracker
        total_done = total_succeeded + total_failed
        if pid in _seq_auto_jobs:
            _seq_auto_jobs[pid]["completed_scenes"] = total_done
            _seq_auto_jobs[pid]["current_step"] = (
                f"generating ({len(active_jobs)} active, "
                f"{total_done}/{total_eligible} complete)"
            )
            active_names = list(active_jobs.values())
            _seq_auto_jobs[pid]["current_scene_name"] = (
                ", ".join(active_names) if active_names else None
            )

    # Handle timeout
    if active_jobs:
        logger.warning(
            f"Continuous dispatch: {len(active_jobs)} jobs timed out "
            f"after {batch_timeout}s"
        )
        total_failed += len(active_jobs)

    # Done
    if total_failed > 0:
        _seq_auto_jobs[pid]["status"] = "done"
        _seq_auto_jobs[pid]["current_step"] = (
            f"complete — {total_succeeded} succeeded, {total_failed} failed"
        )
    else:
        _seq_auto_jobs[pid]["status"] = "done"
        _seq_auto_jobs[pid]["current_step"] = "complete"
    _seq_auto_jobs[pid]["completed_scenes"] = total_succeeded
    _seq_auto_jobs[pid]["current_scene_name"] = None
    logger.info(
        f"Continuous dispatch complete: {total_succeeded} succeeded, "
        f"{total_failed} failed (mode={mode})"
    )


async def _run_sequential_auto_gen(
    pid: str,
    project_id: UUID,
    mode: str,
    job_queue: JobQueue,
    session_factory,
    comfy_dispatcher=None,
    override_full_set: bool = False,
    vocals_only_audio: bool = False,
    skip_audio_mux: bool = False,
    two_pass: bool = False,
    use_story_flow: bool = True,
):
    """Background task: process scenes sequentially or via continuous dispatch.

    Sequential modes (all_images, all_video_single, all_video_fflf):
      Process one scene at a time, waiting for completion before the next.

    Continuous dispatch modes (missing_videos_single, missing_images_independent):
      Count available workers N, fill all N slots, then as each job completes
      immediately submit the next pending one. No idle workers between jobs.
    """
    try:
        async with session_factory() as session:
            # Load project, settings, scenes, lyrics
            project = await session.get(Project, project_id)
            if not project:
                _seq_auto_jobs[pid]["status"] = "failed"
                _seq_auto_jobs[pid]["error"] = "Project not found"
                return

            settings_stmt = select(AppSettings).where(AppSettings.id == 1)
            settings_result = await session.execute(settings_stmt)
            app_settings = settings_result.scalars().first()

            image_model = (app_settings.image_model_type if app_settings else None) or "flux2_klein_dev_9b"
            video_model = (app_settings.video_model_type if app_settings else None) or "ltx_2.3"

            res_w = project.settings.get("resolution_width", 1536)
            res_h = project.settings.get("resolution_height", 864)

            lyrics_stmt = select(Lyrics).where(Lyrics.project_id == project_id)
            lyrics_result = await session.execute(lyrics_stmt)
            lyrics_record = lyrics_result.scalars().first()
            lyrics_words: list[dict] = lyrics_record.words if lyrics_record else []
            user_lyrics_text: str = (getattr(lyrics_record, "initial_text", "") or "").strip() if lyrics_record else ""

            scenes_stmt = select(Scene).where(Scene.project_id == project_id).order_by(Scene.order_index)
            scenes_result = await session.execute(scenes_stmt)
            scenes = list(scenes_result.scalars().all())

            # Set up LLM enhancer
            enhancer = None
            llm_provider = llm_api_key = llm_model = None
            image_sys_override = video_sys_override = None
            if app_settings:
                from backend.services.llm.prompt_enhancer import PromptEnhancer
                from backend.api.settings import resolve_llm_config
                enhancer = PromptEnhancer()
                try:
                    llm_provider, llm_api_key, llm_model = resolve_llm_config(app_settings)
                except Exception:
                    pass

                img_overrides = app_settings.image_system_prompt_overrides or {}
                img_entry = img_overrides.get(image_model, {})
                if isinstance(img_entry, dict) and img_entry.get("enabled") and img_entry.get("text", "").strip():
                    image_sys_override = img_entry["text"]

                vid_overrides = app_settings.video_system_prompt_overrides or {}
                vid_entry = vid_overrides.get(video_model, {})
                if isinstance(vid_entry, dict) and vid_entry.get("enabled") and vid_entry.get("text", "").strip():
                    video_sys_override = vid_entry["text"]

        # ── Auto-generate video flow if missing ─────────────────────────
        # Ensures each scene has a unique storyboard idea before generation
        if llm_api_key and llm_provider:
            async with session_factory() as flow_session:
                # Re-load project and scenes for flow generation
                flow_project = await flow_session.get(Project, project_id)
                flow_scenes_stmt = select(Scene).where(Scene.project_id == project_id).order_by(Scene.order_index)
                flow_scenes_result = await flow_session.execute(flow_scenes_stmt)
                flow_scenes = list(flow_scenes_result.scalars().all())
                generated = await _ensure_video_flow(
                    flow_project, flow_scenes, flow_session, llm_provider, llm_api_key, llm_model
                )
                if generated:
                    _seq_auto_jobs[pid]["current_step"] = "generated video flow"
                    # Re-read scenes with updated flow ideas
                    scenes_stmt2 = select(Scene).where(Scene.project_id == project_id).order_by(Scene.order_index)
                    scenes_result2 = await flow_session.execute(scenes_stmt2)
                    scenes = list(scenes_result2.scalars().all())

        # ── Windowed batch modes ──────────────────────────────────────
        # These modes process scenes in windows of N (= number of available
        # workers) rather than one-at-a-time or all-at-once.  This keeps
        # the generation queue visible and manageable.
        if mode in ("missing_videos_single", "missing_images_independent"):
            await _run_windowed_batch(
                pid, project_id, mode, scenes, job_queue, session_factory,
                comfy_dispatcher=comfy_dispatcher,
                override_full_set=override_full_set,
                project=project, lyrics_words=lyrics_words,
                enhancer=enhancer, llm_provider=llm_provider,
                llm_api_key=llm_api_key, llm_model=llm_model,
                image_model=image_model, video_model=video_model,
                image_sys_override=image_sys_override,
                video_sys_override=video_sys_override,
                res_w=res_w, res_h=res_h,
                vocals_only_audio=vocals_only_audio,
                skip_audio_mux=skip_audio_mux,
                two_pass=two_pass,
                user_lyrics_text=user_lyrics_text,
                use_story_flow=use_story_flow,
            )
            return

        # Process scenes one by one (sequential modes)
        prev_scene = None
        for i, scene in enumerate(scenes):
            # Check for cancellation
            if _seq_auto_jobs.get(pid, {}).get("status") != "running":
                logger.info(f"Sequential auto-gen cancelled at scene {i}")
                return

            scene_name = scene.name or f"Scene {scene.order_index + 1}"
            _seq_auto_jobs[pid]["current_scene_name"] = scene_name
            _seq_auto_jobs[pid]["completed_scenes"] = i

            # Re-read scene to get latest parameters (may have been updated by previous job)
            async with session_factory() as session:
                scene = await session.get(Scene, scene.id)
                project = await session.get(Project, project_id)
                if not scene:
                    continue

                scene_lyrics = _get_scene_lyrics(scene, lyrics_words, lyrics_text=user_lyrics_text)
                has_ff = _scene_has_first_frame(scene)
                has_video = bool(scene.parameters.get("chosen_video_path"))

                # Include character image refs + scene extras
                proj_chars = (project.settings or {}).get("characters", []) if project else []
                seq_char_aids = await _resolve_character_asset_ids(
                    proj_chars, project_id, session, max_chars=2
                )
                extra_ids = _collect_ref_asset_ids(scene, "first")
                ref_ids = seq_char_aids + extra_ids
                wf_type = _auto_workflow_type(len(ref_ids))

                # Reset flags for auto-gen — start fresh
                scene_params = dict(scene.parameters or {})
                scene_params["ignore_prev_scene_ref"] = False
                scene_params["use_prev_scene_last_frame"] = False
                # Persist use_story_flow so per-scene checkbox reflects auto-gen setting
                scene_params["use_story_flow"] = use_story_flow
                # Set character indices so UI shows them as selected
                if proj_chars:
                    scene_params["image_refs_first"] = {
                        "characterIndices": list(range(min(2, len(proj_chars)))),
                        "extras": scene_params.get("image_refs_first", {}).get("extras", []),
                    }
                scene.parameters = scene_params
                await session.commit()

                # ── MODE: all_images ──
                if mode == "all_images":
                    if has_ff and not override_full_set:
                        # Already has image — skip (unless override)
                        logger.info(f"Sequential auto-gen: Scene {i} already has first frame, skipping")
                        prev_scene = scene
                        continue

                    _seq_auto_jobs[pid]["current_step"] = "enhancing image prompt"
                    prompt = scene.prompt or f"Scene {scene.order_index + 1}"

                    if enhancer and llm_api_key:
                        try:
                            context = await _build_auto_enhance_context(
                                project, scene, scene_lyrics, "first", image_model, prev_scene
                            )
                            enhance_args = (enhancer.enhance, prompt, context, llm_provider, llm_api_key, llm_model,
                                False, image_sys_override, image_model, "first")
                            if two_pass and ref_ids:
                                enhance_args += (None, "base")
                            prompt = await asyncio.to_thread(*enhance_args)
                        except Exception as e:
                            logger.warning(f"Sequential auto-gen: enhance failed scene {i}: {e}")

                    _seq_auto_jobs[pid]["current_step"] = "generating first frame image"

                    # Update scene: save prompt + set image mode
                    scene.prompt = prompt
                    scene_params = dict(scene.parameters or {})
                    scene_params["scene_source_type"] = "image"
                    scene.parameters = scene_params
                    await session.commit()

                    img_params = {
                        "workflow_type": wf_type,
                        "prompt": prompt,
                        "width": res_w, "height": res_h,
                        "reference_asset_ids": ref_ids,
                        "frame_type": "first",
                        "auto_save_preview": True,
                    }
                    img_params = _apply_two_pass_to_job_params(img_params, two_pass, ref_ids)
                    await _persist_two_pass_on_scene(scene, session, two_pass, ref_ids)
                    job = Job(
                        project_id=project_id,
                        scene_id=scene.id,
                        job_type=JobType.IMAGE,
                        status=JobStatus.PENDING,
                        priority=0,
                        parameters=img_params,
                    )
                    session.add(job)
                    await session.commit()
                    await session.refresh(job)
                    job_queue.notify()

                    ok = await _wait_for_job(job.id, session_factory)
                    if not ok:
                        _seq_auto_jobs[pid]["status"] = "failed"
                        _seq_auto_jobs[pid]["error"] = f"Image generation failed for {scene_name}"
                        return

                # NOTE: missing_images_independent is handled by _run_windowed_batch() above

                # ── MODE: all_video_single ──
                elif mode == "all_video_single":
                    if has_video and not override_full_set:
                        logger.info(f"Sequential auto-gen: Scene {i} already has video, skipping")
                        prev_scene = scene
                        continue

                    # Step 1: Generate first frame image ONLY if missing.
                    # override_full_set controls video skip (line 1930), NOT image regen.
                    # When the user runs "Generate All Images" then "Generate All Videos",
                    # we must USE the existing images, not overwrite them.
                    if not has_ff:
                        _seq_auto_jobs[pid]["current_step"] = "generating first frame image"
                        prompt = scene.prompt or f"Scene {scene.order_index + 1}"
                        if enhancer and llm_api_key:
                            try:
                                context = await _build_auto_enhance_context(
                                    project, scene, scene_lyrics, "first", image_model, prev_scene
                                )
                                enhance_args = (enhancer.enhance, prompt, context, llm_provider, llm_api_key, llm_model,
                                    False, image_sys_override, image_model, "first")
                                if two_pass and ref_ids:
                                    enhance_args += (None, "base")
                                prompt = await asyncio.to_thread(*enhance_args)
                            except Exception as e:
                                logger.warning(f"Sequential auto-gen: enhance FF failed scene {i}: {e}")

                        # Save prompt to scene
                        scene.prompt = prompt
                        await session.commit()

                        img_params = {
                            "workflow_type": wf_type,
                            "prompt": prompt,
                            "width": res_w, "height": res_h,
                            "reference_asset_ids": ref_ids,
                            "frame_type": "first",
                            "auto_save_preview": True,
                        }
                        img_params = _apply_two_pass_to_job_params(img_params, two_pass, ref_ids)
                        await _persist_two_pass_on_scene(scene, session, two_pass, ref_ids)
                        job = Job(
                            project_id=project_id,
                            scene_id=scene.id,
                            job_type=JobType.IMAGE,
                            status=JobStatus.PENDING,
                            priority=0,
                            parameters=img_params,
                        )
                        session.add(job)
                        await session.commit()
                        await session.refresh(job)
                        job_queue.notify()

                        ok = await _wait_for_job(job.id, session_factory)
                        if not ok:
                            _seq_auto_jobs[pid]["status"] = "failed"
                            _seq_auto_jobs[pid]["error"] = f"First frame generation failed for {scene_name}"
                            return

                    # Step 2: Set scene to single-image video mode, then generate video
                    _seq_auto_jobs[pid]["current_step"] = "generating video"

                    # Re-read scene to get the now-generated first frame
                    # (dispatcher updated chosen_image_path in a different session)
                    scene_id = scene.id  # capture before expire to avoid greenlet error
                    session.expire(scene)
                    scene = await session.get(Scene, scene_id)
                    scene_params = dict(scene.parameters or {})
                    scene_params["video_mode"] = "single"
                    scene_params["use_prev_lf_as_ff"] = False
                    scene_params["scene_source_type"] = "video"
                    scene.parameters = scene_params
                    await session.commit()

                    # Always re-derive from current image prompt — not stale video_prompt
                    video_prompt = scene.prompt or f"Cinematic scene {scene.order_index + 1}"
                    if enhancer and llm_api_key:
                        try:
                            vid_ctx = await _build_video_enhance_context(
                                project, scene, scene_lyrics, video_model, prev_scene
                            )
                            video_prompt = await asyncio.to_thread(
                                enhancer.enhance, video_prompt, vid_ctx, llm_provider, llm_api_key, llm_model,
                                True, video_sys_override, video_model,
                            )
                        except Exception as e:
                            logger.warning(f"Sequential auto-gen: enhance video failed scene {i}: {e}")

                    # Save video prompt to scene
                    scene_params = dict(scene.parameters or {})
                    scene_params["video_prompt"] = video_prompt
                    scene.parameters = scene_params
                    await session.commit()

                    duration = scene.end_time - scene.start_time
                    job = Job(
                        project_id=project_id,
                        scene_id=scene.id,
                        job_type=JobType.VIDEO,
                        status=JobStatus.PENDING,
                        priority=0,
                        parameters={
                            "workflow_type": "ltx_i2v",
                            "prompt": video_prompt,
                            "width": res_w, "height": res_h,
                            "duration": duration,
                            "framerate": 24,
                            "skip_audio_mux": skip_audio_mux,
                        },
                    )
                    session.add(job)
                    await session.commit()
                    await session.refresh(job)
                    job_queue.notify()

                    ok = await _wait_for_job(job.id, session_factory, timeout=_VIDEO_JOB_TIMEOUT)
                    if not ok:
                        _seq_auto_jobs[pid]["status"] = "failed"
                        _seq_auto_jobs[pid]["error"] = f"Video generation failed for {scene_name}"
                        return

                # NOTE: missing_videos_single is handled by _run_windowed_batch() above

                # ── MODE: all_video_fflf ──
                elif mode == "all_video_fflf":
                    if has_video and not override_full_set:
                        logger.info(f"Sequential auto-gen: Scene {i} already has video, skipping")
                        prev_scene = scene
                        continue

                    is_first_scene = (i == 0)

                    # Step 1: For scene 1, generate first frame if missing.
                    # For later scenes, the first frame comes from prev video's last frame.
                    if is_first_scene and not has_ff:
                        _seq_auto_jobs[pid]["current_step"] = "generating first frame image (scene 1)"
                        prompt = scene.prompt or f"Scene {scene.order_index + 1}"
                        if enhancer and llm_api_key:
                            try:
                                context = await _build_auto_enhance_context(
                                    project, scene, scene_lyrics, "first", image_model, prev_scene
                                )
                                enhance_args = (enhancer.enhance, prompt, context, llm_provider, llm_api_key, llm_model,
                                    False, image_sys_override, image_model, "first")
                                if two_pass and ref_ids:
                                    enhance_args += (None, "base")
                                prompt = await asyncio.to_thread(*enhance_args)
                            except Exception as e:
                                logger.warning(f"Sequential auto-gen: enhance FF scene 1 failed: {e}")

                        # Save prompt to scene
                        scene.prompt = prompt
                        await session.commit()

                        img_params = {
                            "workflow_type": wf_type,
                            "prompt": prompt,
                            "width": res_w, "height": res_h,
                            "reference_asset_ids": ref_ids,
                            "frame_type": "first",
                            "auto_save_preview": True,
                        }
                        img_params = _apply_two_pass_to_job_params(img_params, two_pass, ref_ids)
                        await _persist_two_pass_on_scene(scene, session, two_pass, ref_ids)
                        job = Job(
                            project_id=project_id,
                            scene_id=scene.id,
                            job_type=JobType.IMAGE,
                            status=JobStatus.PENDING,
                            priority=0,
                            parameters=img_params,
                        )
                        session.add(job)
                        await session.commit()
                        await session.refresh(job)
                        job_queue.notify()

                        ok = await _wait_for_job(job.id, session_factory)
                        if not ok:
                            _seq_auto_jobs[pid]["status"] = "failed"
                            _seq_auto_jobs[pid]["error"] = f"First frame generation failed for {scene_name}"
                            return

                    # For non-first scenes: set use_prev_lf_as_ff and copy previous
                    # video's last frame as this scene's chosen_image_path
                    if not is_first_scene and prev_scene:
                        scene_id = scene.id
                        prev_scene_id = prev_scene.id
                        session.expire(scene)
                        scene = await session.get(Scene, scene_id)
                        scene_params = dict(scene.parameters or {})

                        # Get previous scene's video last frame
                        # (prev_scene may be from a different session, so just fetch fresh by ID)
                        prev_scene_fresh = await session.get(Scene, prev_scene_id)
                        prev_params = prev_scene_fresh.parameters or {} if prev_scene_fresh else {}
                        prev_lf = prev_params.get("video_last_frame_path")

                        if prev_lf:
                            scene_params["chosen_image_path"] = prev_lf
                            scene_params["use_prev_lf_as_ff"] = True
                            logger.info(
                                f"Sequential auto-gen: Set scene {i} FF from prev video LF: {prev_lf}"
                            )
                        else:
                            # No previous video last frame available — generate FF image instead
                            if not has_ff:
                                _seq_auto_jobs[pid]["current_step"] = "generating first frame image (no prev LF)"
                                prompt = scene.prompt or f"Scene {scene.order_index + 1}"
                                if enhancer and llm_api_key:
                                    try:
                                        context = await _build_auto_enhance_context(
                                            project, scene, scene_lyrics, "first", image_model, prev_scene
                                        )
                                        enhance_args = (enhancer.enhance, prompt, context, llm_provider, llm_api_key, llm_model,
                                            False, image_sys_override, image_model, "first")
                                        if two_pass and ref_ids:
                                            enhance_args += (None, "base")
                                        prompt = await asyncio.to_thread(*enhance_args)
                                    except Exception as e:
                                        logger.warning(f"Sequential auto-gen: enhance FF fallback failed: {e}")

                                # Save prompt to scene
                                scene.prompt = prompt
                                await session.commit()

                                img_params = {
                                    "workflow_type": wf_type,
                                    "prompt": prompt,
                                    "width": res_w, "height": res_h,
                                    "reference_asset_ids": ref_ids,
                                    "frame_type": "first",
                                    "auto_save_preview": True,
                                }
                                img_params = _apply_two_pass_to_job_params(img_params, two_pass, ref_ids)
                                await _persist_two_pass_on_scene(scene, session, two_pass, ref_ids)
                                job = Job(
                                    project_id=project_id,
                                    scene_id=scene.id,
                                    job_type=JobType.IMAGE,
                                    status=JobStatus.PENDING,
                                    priority=0,
                                    parameters=img_params,
                                )
                                session.add(job)
                                await session.commit()
                                await session.refresh(job)
                                job_queue.notify()

                                ok = await _wait_for_job(job.id, session_factory)
                                if not ok:
                                    _seq_auto_jobs[pid]["status"] = "failed"
                                    _seq_auto_jobs[pid]["error"] = f"FF image fallback failed for {scene_name}"
                                    return
                                # Re-read scene (expire cache to get dispatcher's updates)
                                scene_id = scene.id
                                session.expire(scene)
                                scene = await session.get(Scene, scene_id)
                                scene_params = dict(scene.parameters or {})

                        scene_params["video_mode"] = "single"
                        scene_params["scene_source_type"] = "video"
                        scene.parameters = scene_params
                        await session.commit()

                    # Step 2: Generate video (i2v — uses chosen_image_path as first frame)
                    _seq_auto_jobs[pid]["current_step"] = "generating video"

                    # Expire cache to get latest scene data from DB
                    scene_id = scene.id
                    session.expire(scene)
                    scene = await session.get(Scene, scene_id)
                    scene_params = dict(scene.parameters or {})
                    scene_params["scene_source_type"] = "video"
                    scene.parameters = scene_params
                    await session.commit()

                    # Always re-derive from current image prompt — not stale video_prompt
                    video_prompt = scene.prompt or f"Cinematic scene {scene.order_index + 1}"
                    if enhancer and llm_api_key:
                        try:
                            vid_ctx = await _build_video_enhance_context(
                                project, scene, scene_lyrics, video_model, prev_scene
                            )
                            video_prompt = await asyncio.to_thread(
                                enhancer.enhance, video_prompt, vid_ctx, llm_provider, llm_api_key, llm_model,
                                True, video_sys_override, video_model,
                            )
                        except Exception as e:
                            logger.warning(f"Sequential auto-gen: enhance video failed scene {i}: {e}")

                    # Save video prompt to scene
                    scene_params = dict(scene.parameters or {})
                    scene_params["video_prompt"] = video_prompt
                    scene.parameters = scene_params
                    await session.commit()

                    duration = scene.end_time - scene.start_time
                    job = Job(
                        project_id=project_id,
                        scene_id=scene.id,
                        job_type=JobType.VIDEO,
                        status=JobStatus.PENDING,
                        priority=0,
                        parameters={
                            "workflow_type": "ltx_i2v",
                            "prompt": video_prompt,
                            "width": res_w, "height": res_h,
                            "duration": duration,
                            "framerate": 24,
                            "skip_audio_mux": skip_audio_mux,
                        },
                    )
                    session.add(job)
                    await session.commit()
                    await session.refresh(job)
                    job_queue.notify()

                    ok = await _wait_for_job(job.id, session_factory, timeout=_VIDEO_JOB_TIMEOUT)
                    if not ok:
                        _seq_auto_jobs[pid]["status"] = "failed"
                        _seq_auto_jobs[pid]["error"] = f"Video generation failed for {scene_name}"
                        return

                # ── MODE: all_video_v2v ──
                elif mode == "all_video_v2v":
                    if has_video and not override_full_set:
                        logger.info(f"Sequential auto-gen: Scene {i} already has video, skipping")
                        prev_scene = scene
                        continue

                    is_first_scene = (i == 0)

                    # Step 1: Scene 1 needs a first-frame image (for I2V).
                    # Later scenes use V2V extend — no image needed.
                    if is_first_scene and not has_ff:
                        _seq_auto_jobs[pid]["current_step"] = "generating first frame image (scene 1)"
                        prompt = scene.prompt or f"Scene {scene.order_index + 1}"
                        if enhancer and llm_api_key:
                            try:
                                context = await _build_auto_enhance_context(
                                    project, scene, scene_lyrics, "first", image_model, prev_scene
                                )
                                enhance_args = (enhancer.enhance, prompt, context, llm_provider, llm_api_key, llm_model,
                                    False, image_sys_override, image_model, "first")
                                if two_pass and ref_ids:
                                    enhance_args += (None, "base")
                                prompt = await asyncio.to_thread(*enhance_args)
                            except Exception as e:
                                logger.warning(f"Sequential auto-gen V2V: enhance FF scene 1 failed: {e}")

                        scene.prompt = prompt
                        await session.commit()

                        img_params = {
                            "workflow_type": wf_type,
                            "prompt": prompt,
                            "width": res_w, "height": res_h,
                            "reference_asset_ids": ref_ids,
                            "frame_type": "first",
                            "auto_save_preview": True,
                        }
                        img_params = _apply_two_pass_to_job_params(img_params, two_pass, ref_ids)
                        await _persist_two_pass_on_scene(scene, session, two_pass, ref_ids)
                        job = Job(
                            project_id=project_id,
                            scene_id=scene.id,
                            job_type=JobType.IMAGE,
                            status=JobStatus.PENDING,
                            priority=0,
                            parameters=img_params,
                        )
                        session.add(job)
                        await session.commit()
                        await session.refresh(job)
                        job_queue.notify()

                        ok = await _wait_for_job(job.id, session_factory)
                        if not ok:
                            _seq_auto_jobs[pid]["status"] = "failed"
                            _seq_auto_jobs[pid]["error"] = f"First frame generation failed for {scene_name}"
                            return

                    # Step 2: Generate video
                    _seq_auto_jobs[pid]["current_step"] = "generating video"

                    # Expire cache to get latest scene data
                    scene_id = scene.id
                    session.expire(scene)
                    scene = await session.get(Scene, scene_id)
                    scene_params = dict(scene.parameters or {})
                    scene_params["scene_source_type"] = "video"
                    # Tag the video mode so the scene remembers
                    scene_params["video_mode"] = "v2v_extend" if not is_first_scene else "single"
                    scene.parameters = scene_params
                    await session.commit()

                    # Always re-derive from current image prompt — not stale video_prompt
                    video_prompt = scene.prompt or f"Cinematic scene {scene.order_index + 1}"
                    if enhancer and llm_api_key:
                        try:
                            vid_ctx = await _build_video_enhance_context(
                                project, scene, scene_lyrics, video_model, prev_scene
                            )
                            video_prompt = await asyncio.to_thread(
                                enhancer.enhance, video_prompt, vid_ctx, llm_provider, llm_api_key, llm_model,
                                True, video_sys_override, video_model,
                            )
                        except Exception as e:
                            logger.warning(f"Sequential auto-gen V2V: enhance video failed scene {i}: {e}")

                    # Save video prompt
                    scene_params = dict(scene.parameters or {})
                    scene_params["video_prompt"] = video_prompt
                    scene.parameters = scene_params
                    await session.commit()

                    duration = scene.end_time - scene.start_time
                    # Scene 1 = I2V, scenes 2+ = V2V extend
                    vid_wf = "ltx_v2v_extend" if not is_first_scene else "ltx_i2v"
                    job = Job(
                        project_id=project_id,
                        scene_id=scene.id,
                        job_type=JobType.VIDEO,
                        status=JobStatus.PENDING,
                        priority=0,
                        parameters={
                            "workflow_type": vid_wf,
                            "prompt": video_prompt,
                            "width": res_w, "height": res_h,
                            "duration": duration,
                            "framerate": 24,
                            "skip_audio_mux": skip_audio_mux,
                        },
                    )
                    session.add(job)
                    await session.commit()
                    await session.refresh(job)
                    job_queue.notify()

                    ok = await _wait_for_job(job.id, session_factory, timeout=_VIDEO_JOB_TIMEOUT)
                    if not ok:
                        _seq_auto_jobs[pid]["status"] = "failed"
                        _seq_auto_jobs[pid]["error"] = f"V2V video generation failed for {scene_name}"
                        return

            prev_scene = scene

        # ── Transition LoRA pass (V2V modes only) ──
        # After all scene videos are generated, generate AI transition clips
        # between consecutive scene pairs if use_transition_lora is enabled.
        if mode == "all_video_v2v":
            proj_settings = project.settings or {}
            use_transition = proj_settings.get("use_transition_lora", False)
            transition_strength = proj_settings.get("transition_lora_strength", 1.0)
            project_fps = proj_settings.get("project_fps", 24)

            if use_transition and len(scenes) > 1:
                _seq_auto_jobs[pid]["current_step"] = "generating AI transition clips"
                logger.info(f"Sequential auto-gen: starting transition LoRA pass for {len(scenes)-1} pairs")

                for ti in range(len(scenes) - 1):
                    if _seq_auto_jobs.get(pid, {}).get("cancel"):
                        _seq_auto_jobs[pid]["status"] = "cancelled"
                        return

                    scene_a = scenes[ti]
                    scene_b = scenes[ti + 1]
                    scene_a_params = scene_a.parameters or {}
                    scene_b_params = scene_b.parameters or {}

                    # Need IMAGES (not videos) for transition LoRA's LoadImage nodes.
                    # Scene A: last frame extracted from its video, or chosen image as fallback
                    # Scene B: first frame image, or chosen image as fallback
                    first_frame_src = (
                        scene_a_params.get("video_last_frame_path")
                        or scene_a_params.get("chosen_image_path")
                    )
                    last_frame_src = (
                        scene_b_params.get("chosen_image_path")
                        or scene_b_params.get("video_last_frame_path")
                    )

                    if not first_frame_src or not last_frame_src:
                        logger.warning(f"Transition {ti}: missing content for scene pair {ti}→{ti+1}, skipping")
                        continue

                    _seq_auto_jobs[pid]["current_scene_name"] = f"Transition {scene_a.order_index+1}→{scene_b.order_index+1}"

                    # Create transition job — short clip (2-4s)
                    transition_duration = min(3.0, (scene_a.end_time - scene_a.start_time) * 0.3)
                    transition_duration = max(2.0, transition_duration)

                    transition_job = Job(
                        project_id=project_id,
                        scene_id=scene_a.id,  # Store on scene A
                        job_type=JobType.VIDEO,
                        status=JobStatus.PENDING,
                        priority=0,
                        parameters={
                            "workflow_type": "ltx_transition",
                            "prompt": "smooth cinematic transition between scenes, zhuanchang",
                            "width": res_w,
                            "height": res_h,
                            "duration": transition_duration,
                            "framerate": project_fps,
                            "transition_first_frame": first_frame_src,
                            "transition_last_frame": last_frame_src,
                            "transition_lora_strength": transition_strength,
                            "is_transition_clip": True,
                            "skip_audio_mux": True,
                            "scene_a_id": str(scene_a.id),
                            "scene_b_id": str(scene_b.id),
                        },
                    )
                    session.add(transition_job)
                    await session.commit()
                    await session.refresh(transition_job)
                    job_queue.notify()

                    ok = await _wait_for_job(transition_job.id, session_factory, timeout=_VIDEO_JOB_TIMEOUT)
                    if not ok:
                        logger.warning(f"Transition clip {ti}→{ti+1} failed, continuing with remaining transitions")
                        # Don't fail the whole run — transitions are optional
                        continue

                    # Save transition clip path on scene A's parameters
                    session.expire(scene_a)
                    scene_a = await session.get(Scene, scene_a.id)
                    # The dispatcher should have saved the output — check job for output path
                    transition_job_refreshed = await session.get(Job, transition_job.id)
                    if transition_job_refreshed and transition_job_refreshed.status == JobStatus.COMPLETED:
                        tj_params = transition_job_refreshed.parameters or {}
                        output_path = tj_params.get("output_path")
                        if output_path:
                            sa_params = dict(scene_a.parameters or {})
                            sa_params["transition_clip_path"] = output_path
                            scene_a.parameters = sa_params
                            await session.commit()
                            logger.info(f"Saved transition clip for scene {scene_a.order_index}: {output_path}")

                logger.info(f"Transition LoRA pass complete")

        # Sequential modes completed — mark done
        _seq_auto_jobs[pid]["status"] = "done"
        _seq_auto_jobs[pid]["completed_scenes"] = len(scenes)
        _seq_auto_jobs[pid]["current_step"] = "complete"

        _seq_auto_jobs[pid]["current_scene_name"] = None
        logger.info(f"Sequential auto-gen completed for project {project_id} (mode={mode})")

    except Exception as e:
        logger.error(f"Sequential auto-gen failed: {e}", exc_info=True)
        _seq_auto_jobs[pid] = {
            **_seq_auto_jobs.get(pid, {}),
            "status": "failed",
            "error": str(e),
        }


# ===== Transition Clip Generation =====


class GenerateTransitionRequest(BaseModel):
    """Request model for AI transition clip generation between scenes."""
    scene_a_id: UUID  # Source scene (extract last frame from its video)
    scene_b_id: UUID  # Target scene (extract first frame from its video/image)
    prompt: str = "zhuanchang smooth transition between scenes"
    width: int = 1024
    height: int = 576
    duration: float = 3.0
    framerate: int = 24
    seed: Optional[int] = None
    transition_lora_strength: float = 1.0


@router.post("/transition", response_model=GenerationJobResponse, summary="Generate AI transition clip between two scenes")
async def generate_transition(
    project_id: UUID,
    req: GenerateTransitionRequest,
    session: AsyncSession = Depends(get_session),
) -> GenerationJobResponse:
    """
    Generate an AI transition clip between scene A and scene B using the Transition LoRA.
    Extracts last frame from scene A's video and first frame from scene B's video/image,
    then submits a transition generation job.
    """
    # Verify project
    project = await session.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    # Verify scenes
    scene_a = await session.get(Scene, req.scene_a_id)
    scene_b = await session.get(Scene, req.scene_b_id)
    if not scene_a or not scene_b:
        raise HTTPException(status_code=404, detail="Scene not found")

    # Resolve frames for transition
    scene_a_params = scene_a.parameters or {}
    scene_b_params = scene_b.parameters or {}

    # Scene A: last frame IMAGE extracted from its video, or chosen image as fallback.
    # Must be an image file — LoadImage node can't load .mp4 files.
    transition_first = (
        scene_a_params.get("video_last_frame_path")
        or scene_a_params.get("chosen_image_path")
    )
    if not transition_first:
        raise HTTPException(
            status_code=400,
            detail="Scene A has no extracted last frame or chosen image. Generate a video first so the last frame is extracted."
        )

    # Scene B: first frame image (chosen_image_path is always an image file)
    transition_last = (
        scene_b_params.get("chosen_image_path")
        or scene_b_params.get("video_last_frame_path")
    )
    if not transition_last:
        raise HTTPException(
            status_code=400,
            detail="Scene B has no chosen image or extracted frame for transition."
        )

    # Create job
    job = Job(
        project_id=project_id,
        scene_id=req.scene_a_id,  # Store on scene A (transition FROM this scene)
        job_type=JobType.VIDEO,
        status=JobStatus.PENDING,
        priority=0,
        parameters={
            "workflow_type": "ltx_transition",
            "prompt": req.prompt,
            "width": req.width,
            "height": req.height,
            "duration": req.duration,
            "framerate": req.framerate,
            "seed": req.seed,
            "transition_first_frame": transition_first,
            "transition_last_frame": transition_last,
            "transition_lora_strength": req.transition_lora_strength,
            "is_transition_clip": True,
            "skip_audio_mux": True,
            "scene_a_id": str(req.scene_a_id),
            "scene_b_id": str(req.scene_b_id),
        },
    )
    session.add(job)
    await session.commit()
    await session.refresh(job)

    # Notify the job queue
    job_queue = JobQueue.get_instance()
    job_queue.notify()

    return GenerationJobResponse(
        id=job.id,
        project_id=project_id,
        scene_id=req.scene_a_id,
        job_type=job.job_type.value,
        status=job.status.value,
        created_at=job.created_at,
    )


# ===== Rerun Two-Pass (Pass 2 only) =====


class RerunPass2Request(BaseModel):
    """Request model for rerunning only Pass 2 of two-pass generation."""

    scene_id: UUID
    seed: Optional[int] = None  # Optional new seed for variety


@router.post(
    "/rerun-pass2",
    response_model=GenerationJobResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Rerun two-pass Pass 2 (character compositing)",
)
async def rerun_pass2(
    project_id: UUID,
    req: RerunPass2Request,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> GenerationJobResponse:
    """Rerun only Pass 2 (character compositing) using the existing Pass 1 base image.

    Uses the stored two_pass_base_asset_id from scene.parameters to create a new
    composite job without needing to regenerate the scene image (Pass 1).

    Args:
        project_id: UUID of the project.
        req: Rerun Pass 2 request.
        session: Database session.

    Returns:
        Job record with job_id for tracking.

    Raises:
        HTTPException: If project/scene not found or no base image exists.
    """
    try:
        await _get_project_or_404(project_id, session)
        scene = await _get_scene_or_404(req.scene_id, project_id, session)

        # Verify the scene has a stored two-pass base image
        scene_params = scene.parameters or {}
        base_asset_id = scene_params.get("two_pass_base_asset_id")
        if not base_asset_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No two-pass base image found for this scene. Generate with two-pass mode first.",
            )

        # Verify the base asset exists (convert string → UUID for session.get)
        if isinstance(base_asset_id, str):
            base_asset_id = UUID(base_asset_id)
        base_asset = await session.get(Asset, base_asset_id)
        if not base_asset:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Two-pass base image asset no longer exists.",
            )

        # Resolve character ref IDs from project concept data
        project = await session.get(Project, project_id)
        char_ref_ids: list[str] = []
        if project and project.settings:
            characters = project.settings.get("characters", [])
            for char in characters:
                img_path = char.get("image_path", "")
                if img_path:
                    stmt = select(Asset).where(
                        Asset.project_id == project_id,
                        Asset.rel_path == img_path,
                    )
                    result = await session.execute(stmt)
                    asset = result.scalars().first()
                    if asset:
                        char_ref_ids.append(str(asset.id))

        if not char_ref_ids:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No character images found in project concept. Add characters with images first.",
            )

        # Build ref list: base scene image (slot 1) + character refs (slots 2+)
        all_ref_ids = [str(base_asset_id)] + char_ref_ids
        ref_count = len(all_ref_ids)
        workflow_type = f"klein_{ref_count}ref"

        # Resolve seed
        resolved_seed = await _resolve_seed(
            project_id, req.scene_id, req.seed, session,
            job_type="image", frame_type="first",
        )

        # Get dimensions from scene params or defaults
        width = scene_params.get("width", 1024)
        height = scene_params.get("height", 576)

        # Get the original prompt used for this scene
        original_prompt = scene_params.get("two_pass_original_prompt", scene.prompt or "")

        # Create Pass 2 job — the dispatcher will build the composite prompt via LLM
        pass2_params = {
            "workflow_type": workflow_type,
            "workflow_config_id": None,
            "prompt": original_prompt,
            "width": width,
            "height": height,
            "seed": resolved_seed,
            "reference_asset_ids": all_ref_ids,
            "frame_type": "first",
            "two_pass": True,
            "two_pass_phase": "composite",
            "auto_save_preview": True,
            "two_pass_character_ref_ids": char_ref_ids,
            "two_pass_original_prompt": original_prompt,
            "two_pass_scene_prompt": scene_params.get("two_pass_scene_prompt", ""),
            "rerun_pass2": True,  # Flag so dispatcher knows this is a rerun
        }

        job = Job(
            project_id=project_id,
            scene_id=req.scene_id,
            job_type=JobType.IMAGE,
            status=JobStatus.PENDING,
            parameters=pass2_params,
        )
        session.add(job)
        await session.commit()
        await session.refresh(job)

        logger.info(
            f"Created rerun-pass2 job {job.id} for scene {req.scene_id} "
            f"using base asset {base_asset_id} with {len(char_ref_ids)} character refs"
        )

        # Notify the dispatcher
        job_queue: JobQueue = request.app.state.job_queue
        job_queue.notify()

        return GenerationJobResponse.model_validate(job)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating rerun-pass2 job for scene {req.scene_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create rerun pass 2 job",
        )
