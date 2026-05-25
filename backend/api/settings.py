"""Application settings endpoints for RBMN Storyboard App."""
import asyncio
import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, File, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from backend.config import settings as env_settings
from backend.database import get_session
from backend.database.models import AppSettings

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/settings", tags=["settings"])


# Pydantic models for request/response
class SystemPromptOverrideEntry(BaseModel):
    """A single model's system prompt override."""
    text: str = ""
    enabled: bool = False


class RunPodPodEntry(BaseModel):
    """A single RunPod pod configuration."""
    pod_id: str = ""
    label: str = ""
    service_type: str = "image"  # image, video, llm, whisper
    gpu_type_id: str = ""
    template_id: str = ""
    api_port: int = 8188
    enabled: bool = True


class SettingsResponse(BaseModel):
    """Response model for application settings."""

    comfyui_urls: list[str]
    comfyui_server_caps: Optional[dict] = None  # {url: {image: bool, video: bool}}
    whisper_mode: str
    whisper_remote_url: Optional[str]
    whisper_comfyui_url: Optional[str] = None
    whisper_model: str
    whisper_language: str = "English"
    openai_api_key: Optional[str]  # Masked to last 4 chars
    openai_model: Optional[str]
    anthropic_api_key: Optional[str]  # Masked to last 4 chars
    anthropic_model: Optional[str]
    gemini_api_key: Optional[str]  # Masked to last 4 chars
    gemini_model: Optional[str]
    image_model_type: str = "flux2_klein_dev_9b"
    video_model_type: str = "ltx_2.3"
    ltx_model_gguf: str = "ltx-2.3-22b-dev-Q8_0.gguf"
    single_image_generator: str = "z_image_turbo"
    use_distilled_lora: bool = True
    distilled_lora_name: str = "ltx-2.3-22b-distilled-lora-384-1.1.safetensors"
    default_llm_provider: Optional[str] = None
    video_max_duration: int = 15
    video_min_duration: int = 5
    video_tail: int = 0
    color_correction_enabled: bool = True
    restrict_explicit_content: bool = False
    # Export transition settings
    export_transition_type: str = "crossfade"
    export_transition_duration: float = 0.5
    export_color_match_clips: bool = True
    # System prompt overrides keyed by model name
    image_system_prompt_overrides: Optional[dict[str, SystemPromptOverrideEntry]] = None
    video_system_prompt_overrides: Optional[dict[str, SystemPromptOverrideEntry]] = None
    # RunPod integration
    runpod_enabled: bool = False
    runpod_api_key: Optional[str] = None  # Masked
    runpod_idle_timeout: int = 30
    runpod_pods: Optional[list[RunPodPodEntry]] = None
    # Network access — when True, server binds to 0.0.0.0 (LAN/WAN)
    network_access: bool = False
    # App port
    app_port: int = 8899
    # Project directory
    project_dir: Optional[str] = None
    # LTXDirector video generation settings
    director_guide_strength: float = 0.5
    director_audio_guidance: float = 0.001
    director_stitch: bool = False
    director_auto_image_desc: bool = True
    global_video_negative_prompt: Optional[str] = None

    class Config:
        from_attributes = True


class SettingsUpdate(BaseModel):
    """Request model for updating settings."""

    comfyui_urls: Optional[list[str]] = None
    comfyui_server_caps: Optional[dict] = None
    whisper_mode: Optional[str] = None
    whisper_remote_url: Optional[str] = None
    whisper_comfyui_url: Optional[str] = None
    whisper_model: Optional[str] = None
    whisper_language: Optional[str] = None
    openai_api_key: Optional[str] = None
    openai_model: Optional[str] = None
    anthropic_api_key: Optional[str] = None
    anthropic_model: Optional[str] = None
    gemini_api_key: Optional[str] = None
    gemini_model: Optional[str] = None
    image_model_type: Optional[str] = None
    video_model_type: Optional[str] = None
    ltx_model_gguf: Optional[str] = None
    single_image_generator: Optional[str] = None
    use_distilled_lora: Optional[bool] = None
    distilled_lora_name: Optional[str] = None
    default_llm_provider: Optional[str] = None
    video_max_duration: Optional[int] = None
    video_min_duration: Optional[int] = None
    video_tail: Optional[int] = None
    color_correction_enabled: Optional[bool] = None
    restrict_explicit_content: Optional[bool] = None
    export_transition_type: Optional[str] = None
    export_transition_duration: Optional[float] = None
    export_color_match_clips: Optional[bool] = None
    image_system_prompt_overrides: Optional[dict[str, SystemPromptOverrideEntry]] = None
    video_system_prompt_overrides: Optional[dict[str, SystemPromptOverrideEntry]] = None
    # RunPod
    runpod_enabled: Optional[bool] = None
    runpod_api_key: Optional[str] = None
    runpod_idle_timeout: Optional[int] = None
    runpod_pods: Optional[list[RunPodPodEntry]] = None
    # Network access
    network_access: Optional[bool] = None
    # App port
    app_port: Optional[int] = None
    # LTXDirector settings
    director_guide_strength: Optional[float] = None
    director_audio_guidance: Optional[float] = None
    director_stitch: Optional[bool] = None
    director_auto_image_desc: Optional[bool] = None
    global_video_negative_prompt: Optional[str] = None


class ChangeProjectDirRequest(BaseModel):
    """Request model for changing the project directory."""
    new_path: str
    move_data: bool = False


class ChangeProjectDirResponse(BaseModel):
    """Response model for project directory change."""
    success: bool
    message: str
    old_path: str
    new_path: str


class TestComfyUIRequest(BaseModel):
    """Request model for testing ComfyUI connection."""
    url: str


class TestLLMRequest(BaseModel):
    """Request model for testing LLM API."""
    provider: str
    api_key: Optional[str] = None
    model: Optional[str] = None


class TestConnectionResponse(BaseModel):
    """Response model for connection tests."""

    success: bool
    message: str
    details: Optional[dict] = None


# Helper function to get or create singleton settings
async def _get_or_create_settings(session: AsyncSession) -> AppSettings:
    """Get or create the singleton AppSettings record.

    On first run (no DB row yet), seeds from .env values so the user's
    environment configuration becomes the initial database defaults.
    The Settings screen is the source of truth after that.
    """
    stmt = select(AppSettings).where(AppSettings.id == 1)
    result = await session.execute(stmt)
    settings = result.scalars().first()

    if not settings:
        # Seed from .env on first creation
        settings = AppSettings(
            id=1,
            comfyui_urls=env_settings.comfyui_urls or [],
            whisper_mode=env_settings.whisper_mode or "local",
            whisper_remote_url=env_settings.whisper_remote_url,
            whisper_model=env_settings.whisper_model or "large-v2",
            openai_api_key=env_settings.openai_api_key,
            openai_model=env_settings.openai_model,
            anthropic_api_key=env_settings.anthropic_api_key,
            anthropic_model=env_settings.anthropic_model,
            gemini_api_key=env_settings.gemini_api_key,
            gemini_model=env_settings.gemini_model,
        )
        session.add(settings)
        await session.flush()
        logger.info("Created initial settings from .env values")
    else:
        # Backfill empty DB fields from .env (one-time migration for
        # databases created before the seeding logic was added)
        changed = False
        if not settings.comfyui_urls and env_settings.comfyui_urls:
            settings.comfyui_urls = env_settings.comfyui_urls
            changed = True
        if not settings.openai_api_key and env_settings.openai_api_key:
            settings.openai_api_key = env_settings.openai_api_key
            changed = True
        if not settings.openai_model and env_settings.openai_model:
            settings.openai_model = env_settings.openai_model
            changed = True
        if not settings.anthropic_api_key and env_settings.anthropic_api_key:
            settings.anthropic_api_key = env_settings.anthropic_api_key
            changed = True
        if not settings.anthropic_model and env_settings.anthropic_model:
            settings.anthropic_model = env_settings.anthropic_model
            changed = True
        if not settings.gemini_api_key and env_settings.gemini_api_key:
            settings.gemini_api_key = env_settings.gemini_api_key
            changed = True
        if not settings.gemini_model and env_settings.gemini_model:
            settings.gemini_model = env_settings.gemini_model
            changed = True
        if not settings.whisper_remote_url and env_settings.whisper_remote_url:
            settings.whisper_remote_url = env_settings.whisper_remote_url
            changed = True
        if changed:
            await session.flush()
            logger.info("Backfilled empty settings from .env values")

    return settings


def _mask_api_key(api_key: Optional[str]) -> Optional[str]:
    """Mask API key to last 4 characters for security."""
    if not api_key:
        return None
    if len(api_key) <= 4:
        return "****"
    return f"***{api_key[-4:]}"


def _parse_prompt_overrides(raw: dict | None) -> dict[str, SystemPromptOverrideEntry] | None:
    """Convert raw JSON dict from DB into typed override entries."""
    if not raw:
        return None
    result = {}
    for model_key, entry in raw.items():
        if isinstance(entry, dict):
            result[model_key] = SystemPromptOverrideEntry(
                text=entry.get("text", ""),
                enabled=entry.get("enabled", False),
            )
    return result if result else None


def _build_response(settings: AppSettings) -> SettingsResponse:
    """Build a SettingsResponse with null-safe field access."""
    return SettingsResponse(
        comfyui_urls=settings.comfyui_urls or [],
        comfyui_server_caps=settings.comfyui_server_caps,
        whisper_mode=settings.whisper_mode or "local",
        whisper_remote_url=settings.whisper_remote_url,
        whisper_comfyui_url=settings.whisper_comfyui_url,
        whisper_model=settings.whisper_model or "large-v2",
        whisper_language=settings.whisper_language or "English",
        openai_api_key=_mask_api_key(settings.openai_api_key),
        openai_model=settings.openai_model,
        anthropic_api_key=_mask_api_key(settings.anthropic_api_key),
        anthropic_model=settings.anthropic_model,
        gemini_api_key=_mask_api_key(settings.gemini_api_key),
        gemini_model=settings.gemini_model,
        image_model_type=settings.image_model_type or "flux2_klein_dev_9b",
        video_model_type=settings.video_model_type or "ltx_2.3",
        ltx_model_gguf=settings.ltx_model_gguf or "ltx-2.3-22b-dev-Q8_0.gguf",
        single_image_generator=settings.single_image_generator or "z_image_turbo",
        use_distilled_lora=settings.use_distilled_lora if settings.use_distilled_lora is not None else True,
        distilled_lora_name=settings.distilled_lora_name or "ltx-2.3-22b-distilled-lora-384-1.1.safetensors",
        default_llm_provider=settings.default_llm_provider,
        video_max_duration=settings.video_max_duration or 15,
        video_min_duration=settings.video_min_duration if settings.video_min_duration is not None else 5,
        video_tail=settings.video_tail or 0,
        color_correction_enabled=settings.color_correction_enabled if settings.color_correction_enabled is not None else True,
        restrict_explicit_content=settings.restrict_explicit_content or False,
        export_transition_type=settings.export_transition_type or "crossfade",
        export_transition_duration=settings.export_transition_duration if settings.export_transition_duration is not None else 0.5,
        export_color_match_clips=settings.export_color_match_clips if settings.export_color_match_clips is not None else True,
        image_system_prompt_overrides=_parse_prompt_overrides(settings.image_system_prompt_overrides),
        video_system_prompt_overrides=_parse_prompt_overrides(settings.video_system_prompt_overrides),
        runpod_enabled=settings.runpod_enabled or False,
        runpod_api_key=_mask_api_key(settings.runpod_api_key),
        runpod_idle_timeout=settings.runpod_idle_timeout or 30,
        runpod_pods=[RunPodPodEntry(**p) for p in (settings.runpod_pods or [])],
        network_access=settings.network_access or False,
        app_port=settings.app_port or 8899,
        project_dir=settings.project_dir or str(env_settings.project_dir),
        director_guide_strength=settings.director_guide_strength if settings.director_guide_strength is not None else 0.5,
        director_audio_guidance=settings.director_audio_guidance if settings.director_audio_guidance is not None else 0.001,
        director_stitch=settings.director_stitch if settings.director_stitch is not None else False,
        director_auto_image_desc=settings.director_auto_image_desc if settings.director_auto_image_desc is not None else True,
        global_video_negative_prompt=settings.global_video_negative_prompt,
    )


def resolve_llm_config(app_settings: AppSettings) -> tuple[str, str, str]:
    """Resolve the LLM provider, API key, and model to use.

    Uses the default_llm_provider if set and has a valid API key;
    otherwise falls back to the first available provider.

    Returns:
        Tuple of (provider, api_key, model).

    Raises:
        HTTPException: If no LLM API key is configured.
    """
    # Map of provider -> (key, model, default_model)
    providers = {
        "openai": (app_settings.openai_api_key, app_settings.openai_model, "gpt-4o"),
        "anthropic": (app_settings.anthropic_api_key, app_settings.anthropic_model, "claude-sonnet-4-20250514"),
        "gemini": (app_settings.gemini_api_key, app_settings.gemini_model, "gemini-2.0-flash"),
    }

    # If a default is set and has a valid key, use it
    default = app_settings.default_llm_provider
    if default and default in providers:
        key, model, fallback_model = providers[default]
        if key:
            return default, key, model or fallback_model

    # Fallback: first available provider
    for provider, (key, model, fallback_model) in providers.items():
        if key:
            return provider, key, model or fallback_model

    raise HTTPException(status_code=400, detail="No LLM API key configured in settings")


@router.get(
    "",
    response_model=SettingsResponse,
    summary="Get settings",
)
async def get_settings(
    session: AsyncSession = Depends(get_session),
) -> SettingsResponse:
    """Get current application settings with API keys masked.

    Returns:
        Current settings with API keys masked to last 4 characters.
    """
    try:
        settings = await _get_or_create_settings(session)

        return _build_response(settings)
    except Exception as e:
        logger.error(f"Error getting settings: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get settings",
        )


@router.put(
    "",
    response_model=SettingsResponse,
    summary="Update settings",
)
async def update_settings(
    req: SettingsUpdate,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> SettingsResponse:
    """Update application settings.

    Args:
        req: Settings update request.
        request: FastAPI request (for app.state access).
        session: Database session.

    Returns:
        Updated settings with API keys masked.
    """
    try:
        settings = await _get_or_create_settings(session)

        if req.comfyui_urls is not None:
            settings.comfyui_urls = req.comfyui_urls
        if req.comfyui_server_caps is not None:
            settings.comfyui_server_caps = req.comfyui_server_caps
        if req.whisper_mode is not None:
            settings.whisper_mode = req.whisper_mode
        if req.whisper_remote_url is not None:
            settings.whisper_remote_url = req.whisper_remote_url
        if req.whisper_comfyui_url is not None:
            settings.whisper_comfyui_url = req.whisper_comfyui_url
        if req.whisper_model is not None:
            settings.whisper_model = req.whisper_model
        if req.whisper_language is not None:
            settings.whisper_language = req.whisper_language
        # Only update API keys if they're not masked values
        if req.openai_api_key is not None and not req.openai_api_key.startswith("***"):
            settings.openai_api_key = req.openai_api_key
        if req.openai_model is not None:
            settings.openai_model = req.openai_model
        if req.anthropic_api_key is not None and not req.anthropic_api_key.startswith("***"):
            settings.anthropic_api_key = req.anthropic_api_key
        if req.anthropic_model is not None:
            settings.anthropic_model = req.anthropic_model
        if req.gemini_api_key is not None and not req.gemini_api_key.startswith("***"):
            settings.gemini_api_key = req.gemini_api_key
        if req.gemini_model is not None:
            settings.gemini_model = req.gemini_model
        if req.image_model_type is not None:
            settings.image_model_type = req.image_model_type
        if req.video_model_type is not None:
            settings.video_model_type = req.video_model_type
        if req.ltx_model_gguf is not None:
            settings.ltx_model_gguf = req.ltx_model_gguf
        if req.single_image_generator is not None:
            settings.single_image_generator = req.single_image_generator
        if req.use_distilled_lora is not None:
            settings.use_distilled_lora = req.use_distilled_lora
        if req.distilled_lora_name is not None:
            settings.distilled_lora_name = req.distilled_lora_name
        if req.default_llm_provider is not None:
            # Allow empty string to clear the default (fall back to auto-pick)
            settings.default_llm_provider = req.default_llm_provider or None
        if req.video_max_duration is not None:
            settings.video_max_duration = req.video_max_duration
        if req.video_min_duration is not None:
            settings.video_min_duration = req.video_min_duration
        if req.video_tail is not None:
            settings.video_tail = req.video_tail
        if req.color_correction_enabled is not None:
            settings.color_correction_enabled = req.color_correction_enabled
        if req.restrict_explicit_content is not None:
            settings.restrict_explicit_content = req.restrict_explicit_content
        if req.export_transition_type is not None:
            settings.export_transition_type = req.export_transition_type
        if req.export_transition_duration is not None:
            settings.export_transition_duration = req.export_transition_duration
        if req.export_color_match_clips is not None:
            settings.export_color_match_clips = req.export_color_match_clips
        if req.image_system_prompt_overrides is not None:
            settings.image_system_prompt_overrides = {
                k: v.model_dump() for k, v in req.image_system_prompt_overrides.items()
            }
        if req.video_system_prompt_overrides is not None:
            settings.video_system_prompt_overrides = {
                k: v.model_dump() for k, v in req.video_system_prompt_overrides.items()
            }
        # RunPod settings
        if req.runpod_enabled is not None:
            settings.runpod_enabled = req.runpod_enabled
        if req.runpod_api_key is not None and not req.runpod_api_key.startswith("***"):
            settings.runpod_api_key = req.runpod_api_key
        if req.runpod_idle_timeout is not None:
            settings.runpod_idle_timeout = max(1, req.runpod_idle_timeout)
        if req.runpod_pods is not None:
            settings.runpod_pods = [p.model_dump() for p in req.runpod_pods]
        if req.network_access is not None:
            settings.network_access = req.network_access
        if req.app_port is not None:
            settings.app_port = max(1024, min(65535, req.app_port))
        # LTXDirector settings
        if req.director_guide_strength is not None:
            settings.director_guide_strength = max(0.0, min(1.0, req.director_guide_strength))
        if req.director_audio_guidance is not None:
            settings.director_audio_guidance = max(0.001, min(1.0, req.director_audio_guidance))
        if req.director_stitch is not None:
            settings.director_stitch = req.director_stitch
        if req.director_auto_image_desc is not None:
            settings.director_auto_image_desc = req.director_auto_image_desc
        if req.global_video_negative_prompt is not None:
            settings.global_video_negative_prompt = req.global_video_negative_prompt or None

        await session.commit()
        await session.refresh(settings)

        # Reconfigure RunPod manager if RunPod settings changed
        if any([
            req.runpod_enabled is not None,
            req.runpod_api_key is not None,
            req.runpod_idle_timeout is not None,
            req.runpod_pods is not None,
        ]):
            try:
                from backend.services.runpod.manager import RunPodManager
                rp_manager = RunPodManager.get_instance()
                if settings.runpod_enabled and settings.runpod_api_key:
                    rp_manager.configure(
                        api_key=settings.runpod_api_key,
                        pod_configs=settings.runpod_pods or [],
                        idle_timeout_minutes=settings.runpod_idle_timeout or 30,
                    )
                    # Ensure idle monitor is running
                    import asyncio
                    await rp_manager.start_idle_monitor()
                    logger.info("RunPod manager reconfigured after settings update")
                else:
                    # Disabled — stop monitor if running
                    await rp_manager.stop_idle_monitor()
                    rp_manager._configured = False
                    logger.info("RunPod manager disabled after settings update")
            except Exception as rp_err:
                logger.warning(f"Failed to reconfigure RunPod manager: {rp_err}")

        # Sync ComfyUI dispatcher workers when URLs or server caps change
        if req.comfyui_urls is not None or req.comfyui_server_caps is not None:
            try:
                comfy_dispatcher = request.app.state.comfy_dispatcher
                new_urls = set(settings.comfyui_urls or [])
                current_urls = set(comfy_dispatcher.workers.keys())
                server_caps = settings.comfyui_server_caps or {}

                # Remove workers no longer in settings
                for url in current_urls - new_urls:
                    # Don't remove RunPod workers — they're managed separately
                    worker = comfy_dispatcher.workers.get(url)
                    if worker and not worker.is_runpod:
                        comfy_dispatcher.remove_worker(url)
                        logger.info(f"Removed ComfyUI worker: {url}")

                # Add new workers
                for url in new_urls - current_urls:
                    try:
                        worker = comfy_dispatcher.add_worker(url)
                        logger.info(f"Added ComfyUI worker: {url}")
                    except Exception as add_err:
                        logger.warning(f"Failed to add ComfyUI worker {url}: {add_err}")

                # Update capabilities on all current workers
                for url in new_urls:
                    worker = comfy_dispatcher.workers.get(url)
                    if worker:
                        caps_config = server_caps.get(url, {})
                        if caps_config:
                            user_caps = set()
                            if caps_config.get("image", True):
                                user_caps.add("klein")
                            if caps_config.get("video", True):
                                user_caps.add("ltx")
                            worker.capabilities = user_caps
                            logger.info(f"Updated caps for {url}: {user_caps}")

                logger.info(
                    f"ComfyUI dispatcher synced: {len(comfy_dispatcher.workers)} workers"
                )
            except Exception as sync_err:
                logger.warning(f"Failed to sync ComfyUI dispatcher: {sync_err}")

        logger.info("Updated application settings")

        return _build_response(settings)
    except Exception as e:
        logger.error(f"Error updating settings: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update settings",
        )


@router.post(
    "/test-comfyui",
    response_model=TestConnectionResponse,
    summary="Test ComfyUI connection",
)
async def test_comfyui(
    req: TestComfyUIRequest,
    session: AsyncSession = Depends(get_session),
) -> TestConnectionResponse:
    """Test connection to a ComfyUI URL.

    Args:
        req: Request containing URL to test.
        session: Database session.

    Returns:
        Connection test result with system stats and available models.
    """
    url = req.url
    try:
        import httpx

        # Simple HTTP check — hit the /system_stats endpoint
        async with httpx.AsyncClient(timeout=10.0) as client:
            # Normalize URL
            base = url.rstrip("/")
            resp = await client.get(f"{base}/system_stats")
            resp.raise_for_status()
            stats = resp.json()

        return TestConnectionResponse(
            success=True,
            message="Connected to ComfyUI",
            details={
                "system_stats": stats,
            },
        )
    except Exception as e:
        logger.error(f"Error testing ComfyUI connection to {url}: {e}")
        return TestConnectionResponse(
            success=False,
            message=f"Failed to connect: {str(e)}",
        )


@router.post(
    "/test-whisper",
    response_model=TestConnectionResponse,
    summary="Test Whisper connection",
)
async def test_whisper(
    session: AsyncSession = Depends(get_session),
) -> TestConnectionResponse:
    """Test Whisper connection (local or remote).

    Args:
        session: Database session.

    Returns:
        Connection test result.
    """
    try:
        settings = await _get_or_create_settings(session)

        if settings.whisper_mode == "local":
            # Check if local Whisper is available
            try:
                import whisperx
                return TestConnectionResponse(
                    success=True,
                    message=f"Connected to Whisper ({settings.whisper_mode} mode)",
                    details={
                        "mode": settings.whisper_mode,
                        "model": settings.whisper_model,
                    },
                )
            except ImportError:
                return TestConnectionResponse(
                    success=False,
                    message="WhisperX not installed locally",
                )
        elif settings.whisper_mode == "comfyui":
            # Test connection to ComfyUI Whisper server
            if not settings.whisper_comfyui_url:
                return TestConnectionResponse(
                    success=False,
                    message="No ComfyUI Whisper URL configured",
                )

            import requests

            comfy_url = settings.whisper_comfyui_url.rstrip("/")
            try:
                # Check ComfyUI system stats endpoint
                resp = requests.get(f"{comfy_url}/system_stats", timeout=10)
                if resp.status_code == 200:
                    stats = resp.json()
                    # Also check if "Apply Whisper" node is available
                    has_whisper = False
                    try:
                        obj_resp = requests.get(f"{comfy_url}/object_info/Apply%20Whisper", timeout=10)
                        if obj_resp.status_code == 200:
                            has_whisper = True
                    except Exception:
                        pass

                    if has_whisper:
                        return TestConnectionResponse(
                            success=True,
                            message=f"Connected to ComfyUI Whisper server",
                            details={
                                "mode": "comfyui",
                                "url": comfy_url,
                                "model": settings.whisper_model,
                                "server_type": "comfyui",
                                "whisper_node_available": True,
                            },
                        )
                    else:
                        return TestConnectionResponse(
                            success=False,
                            message=f"ComfyUI server reachable but 'Apply Whisper' node not found. "
                                    f"Install the ComfyUI-Whisper extension (yuvraj108c/ComfyUI-Whisper).",
                            details={
                                "mode": "comfyui",
                                "url": comfy_url,
                                "whisper_node_available": False,
                            },
                        )
                else:
                    return TestConnectionResponse(
                        success=False,
                        message=f"ComfyUI server at {comfy_url} returned status {resp.status_code}",
                    )
            except requests.exceptions.ConnectionError:
                return TestConnectionResponse(
                    success=False,
                    message=f"Cannot connect to ComfyUI server at {comfy_url}",
                )
            except Exception as e:
                return TestConnectionResponse(
                    success=False,
                    message=f"Error connecting to ComfyUI Whisper: {str(e)}",
                )

        else:
            # Test connection to remote Whisper URL
            if not settings.whisper_remote_url:
                return TestConnectionResponse(
                    success=False,
                    message="No remote Whisper URL configured",
                )

            import requests

            base_url = settings.whisper_remote_url.rstrip("/")
            server_type = None

            # Try Gradio endpoints first (Whisper-WebUI is a Gradio app)
            try:
                resp = requests.get(f"{base_url}/info", timeout=5)
                if resp.status_code == 200:
                    server_type = "gradio"
                    info = resp.json()
            except Exception:
                pass

            # Try /config (another Gradio endpoint)
            if not server_type:
                try:
                    resp = requests.get(f"{base_url}/config", timeout=5)
                    if resp.status_code == 200:
                        server_type = "gradio"
                except Exception:
                    pass

            # Try /health (OpenAI-compatible / generic servers)
            if not server_type:
                try:
                    resp = requests.get(f"{base_url}/health", timeout=5)
                    if resp.status_code == 200:
                        server_type = "rest"
                except Exception:
                    pass

            # Try root URL as last resort (Gradio apps return HTML)
            if not server_type:
                try:
                    resp = requests.get(base_url, timeout=5)
                    if resp.status_code == 200:
                        if "gradio" in resp.text.lower():
                            server_type = "gradio"
                        else:
                            server_type = "unknown"
                except Exception as e:
                    return TestConnectionResponse(
                        success=False,
                        message=f"Failed to connect to remote Whisper at {base_url}: {str(e)}",
                    )

            if server_type:
                details = {
                    "mode": settings.whisper_mode,
                    "url": settings.whisper_remote_url,
                    "model": settings.whisper_model,
                    "server_type": server_type,
                }
                label = "Gradio/Whisper-WebUI" if server_type == "gradio" else "Whisper API"
                return TestConnectionResponse(
                    success=True,
                    message=f"Connected to {label} ({settings.whisper_mode} mode)",
                    details=details,
                )
            else:
                return TestConnectionResponse(
                    success=False,
                    message=f"No Whisper server found at {base_url}",
                )
    except Exception as e:
        logger.error(f"Error testing Whisper connection: {e}")
        return TestConnectionResponse(
            success=False,
            message=f"Failed to connect: {str(e)}",
        )


@router.post(
    "/test-llm",
    response_model=TestConnectionResponse,
    summary="Test LLM API",
)
async def test_llm(
    req: TestLLMRequest,
    session: AsyncSession = Depends(get_session),
) -> TestConnectionResponse:
    """Test an LLM API key (OpenAI, Anthropic, or Gemini).

    Args:
        req: Request containing provider, optional api_key and model.
        session: Database session.

    Returns:
        Connection test result.
    """
    try:
        settings = await _get_or_create_settings(session)
        provider = req.provider.lower()

        # Use DB-stored key (frontend sends masked keys, so ignore those)
        if provider == "openai":
            api_key = settings.openai_api_key
            model = settings.openai_model or "gpt-4o"
        elif provider == "anthropic":
            api_key = settings.anthropic_api_key
            model = settings.anthropic_model or "claude-sonnet-4-20250514"
        elif provider == "gemini":
            api_key = settings.gemini_api_key
            model = settings.gemini_model or "gemini-2.0-flash"
        else:
            return TestConnectionResponse(
                success=False,
                message=f"Unknown provider: {provider}",
            )

        if not api_key:
            return TestConnectionResponse(
                success=False,
                message=f"No API key configured for {provider}",
            )

        # Lightweight API validation — just list models or send a tiny request
        import httpx

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                if provider == "openai":
                    resp = await client.get(
                        "https://api.openai.com/v1/models",
                        headers={"Authorization": f"Bearer {api_key}"},
                    )
                    resp.raise_for_status()
                elif provider == "anthropic":
                    resp = await client.post(
                        "https://api.anthropic.com/v1/messages",
                        headers={
                            "x-api-key": api_key,
                            "anthropic-version": "2023-06-01",
                            "content-type": "application/json",
                        },
                        json={
                            "model": model,
                            "max_tokens": 1,
                            "messages": [{"role": "user", "content": "hi"}],
                        },
                    )
                    resp.raise_for_status()
                elif provider == "gemini":
                    resp = await client.get(
                        f"https://generativelanguage.googleapis.com/v1beta/models?key={api_key}",
                    )
                    resp.raise_for_status()

            return TestConnectionResponse(
                success=True,
                message=f"Connected to {req.provider}",
                details={"provider": req.provider, "model": model},
            )
        except httpx.HTTPStatusError as e:
            return TestConnectionResponse(
                success=False,
                message=f"API error ({e.response.status_code}): {e.response.text[:200]}",
            )
        except Exception as e:
            return TestConnectionResponse(
                success=False,
                message=f"Connection failed: {str(e)}",
            )
    except Exception as e:
        logger.error(f"Error testing LLM API: {e}")
        return TestConnectionResponse(
            success=False,
            message=f"Failed to connect: {str(e)}",
        )


# ── Settings Export / Import ────────────────────────────────────────


class SettingsExportData(BaseModel):
    """Schema for the exported settings file."""

    export_version: int = 1
    exported_at: str
    comfyui_urls: list[str]
    comfyui_server_caps: Optional[dict] = None  # {url: {image: bool, video: bool}}
    whisper_mode: str
    whisper_remote_url: Optional[str]
    whisper_comfyui_url: Optional[str] = None
    whisper_model: str
    whisper_language: str = "English"
    openai_api_key: Optional[str]
    openai_model: Optional[str]
    anthropic_api_key: Optional[str]
    anthropic_model: Optional[str]
    gemini_api_key: Optional[str]
    gemini_model: Optional[str]
    image_model_type: str
    video_model_type: str
    ltx_model_gguf: str = "ltx-2.3-22b-dev-Q8_0.gguf"
    single_image_generator: str = "z_image_turbo"
    use_distilled_lora: bool = True
    distilled_lora_name: str = "ltx-2.3-22b-distilled-lora-384-1.1.safetensors"
    default_llm_provider: Optional[str] = None
    video_max_duration: int = 15
    video_min_duration: int = 5
    video_tail: int = 0
    color_correction_enabled: bool = True
    restrict_explicit_content: bool = False
    export_transition_type: str = "crossfade"
    export_transition_duration: float = 0.5
    export_color_match_clips: bool = True
    image_system_prompt_overrides: Optional[dict] = None
    video_system_prompt_overrides: Optional[dict] = None
    # RunPod
    runpod_enabled: bool = False
    runpod_api_key: Optional[str] = None
    runpod_idle_timeout: int = 30
    runpod_pods: Optional[list[dict]] = None
    # Network access
    network_access: bool = False


@router.get(
    "/export",
    summary="Export settings as JSON backup",
)
async def export_settings(
    session: AsyncSession = Depends(get_session),
):
    """Export all application settings as a downloadable JSON file.

    Returns a JSON file with all settings (API keys included in full)
    stamped with the export date/time.
    """
    try:
        settings = await _get_or_create_settings(session)

        payload = SettingsExportData(
            exported_at=datetime.now(timezone.utc).isoformat(),
            comfyui_urls=settings.comfyui_urls or [],
        comfyui_server_caps=settings.comfyui_server_caps,
            whisper_mode=settings.whisper_mode or "local",
            whisper_remote_url=settings.whisper_remote_url,
            whisper_comfyui_url=settings.whisper_comfyui_url,
            whisper_model=settings.whisper_model or "large-v2",
        whisper_language=settings.whisper_language or "English",
            openai_api_key=settings.openai_api_key,
            openai_model=settings.openai_model,
            anthropic_api_key=settings.anthropic_api_key,
            anthropic_model=settings.anthropic_model,
            gemini_api_key=settings.gemini_api_key,
            gemini_model=settings.gemini_model,
            image_model_type=settings.image_model_type or "flux2_klein_dev_9b",
            video_model_type=settings.video_model_type or "ltx_2.3",
            ltx_model_gguf=settings.ltx_model_gguf or "ltx-2.3-22b-dev-Q8_0.gguf",
            single_image_generator=settings.single_image_generator or "z_image_turbo",
            use_distilled_lora=settings.use_distilled_lora if settings.use_distilled_lora is not None else True,
            distilled_lora_name=settings.distilled_lora_name or "ltx-2.3-22b-distilled-lora-384-1.1.safetensors",
            default_llm_provider=settings.default_llm_provider,
            video_max_duration=settings.video_max_duration or 15,
            video_tail=settings.video_tail or 0,
            color_correction_enabled=settings.color_correction_enabled if settings.color_correction_enabled is not None else True,
            restrict_explicit_content=settings.restrict_explicit_content or False,
            export_transition_type=settings.export_transition_type or "crossfade",
            export_transition_duration=settings.export_transition_duration if settings.export_transition_duration is not None else 0.5,
            export_color_match_clips=settings.export_color_match_clips if settings.export_color_match_clips is not None else True,
            image_system_prompt_overrides=settings.image_system_prompt_overrides,
            video_system_prompt_overrides=settings.video_system_prompt_overrides,
            runpod_enabled=settings.runpod_enabled or False,
            runpod_api_key=settings.runpod_api_key,
            runpod_idle_timeout=settings.runpod_idle_timeout or 30,
            runpod_pods=settings.runpod_pods,
            network_access=settings.network_access or False,
            app_port=settings.app_port or 8899,
        )

        # Build filename with date stamp
        date_stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M%S")
        filename = f"rbmn_settings_{date_stamp}.rbmn-settings.json"

        return JSONResponse(
            content=payload.model_dump(),
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
            },
        )
    except Exception as e:
        logger.error(f"Error exporting settings: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to export settings",
        )


@router.post(
    "/import",
    response_model=SettingsResponse,
    summary="Import settings from JSON backup",
)
async def import_settings(
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_session),
) -> SettingsResponse:
    """Import application settings from a previously exported JSON file.

    Overwrites all current settings with the values from the backup.
    API keys in the backup are restored in full.

    Args:
        file: The .rbmn-settings.json backup file.
        session: Database session.

    Returns:
        Updated settings with API keys masked.
    """
    import json as _json

    try:
        raw = await file.read()
        try:
            data = _json.loads(raw)
        except _json.JSONDecodeError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid JSON file",
            )

        # Validate required structure
        if "export_version" not in data:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Not a valid RBMN settings backup file (missing export_version)",
            )

        settings = await _get_or_create_settings(session)

        # Apply all fields from the backup
        if "comfyui_urls" in data:
            settings.comfyui_urls = data["comfyui_urls"]
        if "whisper_mode" in data:
            settings.whisper_mode = data["whisper_mode"]
        if "whisper_remote_url" in data:
            settings.whisper_remote_url = data["whisper_remote_url"]
        if "whisper_comfyui_url" in data:
            settings.whisper_comfyui_url = data["whisper_comfyui_url"]
        if "whisper_model" in data:
            settings.whisper_model = data["whisper_model"]
        if "whisper_language" in data:
            settings.whisper_language = data["whisper_language"]
        if "openai_api_key" in data and data["openai_api_key"]:
            settings.openai_api_key = data["openai_api_key"]
        if "openai_model" in data:
            settings.openai_model = data["openai_model"]
        if "anthropic_api_key" in data and data["anthropic_api_key"]:
            settings.anthropic_api_key = data["anthropic_api_key"]
        if "anthropic_model" in data:
            settings.anthropic_model = data["anthropic_model"]
        if "gemini_api_key" in data and data["gemini_api_key"]:
            settings.gemini_api_key = data["gemini_api_key"]
        if "gemini_model" in data:
            settings.gemini_model = data["gemini_model"]
        if "image_model_type" in data:
            settings.image_model_type = data["image_model_type"]
        if "video_model_type" in data:
            settings.video_model_type = data["video_model_type"]
        if "ltx_model_gguf" in data:
            settings.ltx_model_gguf = data["ltx_model_gguf"]
        if "single_image_generator" in data:
            settings.single_image_generator = data["single_image_generator"]
        if "use_distilled_lora" in data:
            settings.use_distilled_lora = data["use_distilled_lora"]
        if "distilled_lora_name" in data:
            settings.distilled_lora_name = data["distilled_lora_name"]
        if "default_llm_provider" in data:
            settings.default_llm_provider = data["default_llm_provider"]
        if "video_max_duration" in data:
            settings.video_max_duration = data["video_max_duration"]
        if "video_min_duration" in data:
            settings.video_min_duration = data["video_min_duration"]
        if "video_tail" in data:
            settings.video_tail = data["video_tail"]
        if "color_correction_enabled" in data:
            settings.color_correction_enabled = data["color_correction_enabled"]
        if "restrict_explicit_content" in data:
            settings.restrict_explicit_content = data["restrict_explicit_content"]
        if "export_transition_type" in data:
            settings.export_transition_type = data["export_transition_type"]
        if "export_transition_duration" in data:
            settings.export_transition_duration = data["export_transition_duration"]
        if "export_color_match_clips" in data:
            settings.export_color_match_clips = data["export_color_match_clips"]
        if "image_system_prompt_overrides" in data:
            settings.image_system_prompt_overrides = data["image_system_prompt_overrides"]
        if "video_system_prompt_overrides" in data:
            settings.video_system_prompt_overrides = data["video_system_prompt_overrides"]
        # RunPod
        if "runpod_enabled" in data:
            settings.runpod_enabled = data["runpod_enabled"]
        if "runpod_api_key" in data and data["runpod_api_key"]:
            settings.runpod_api_key = data["runpod_api_key"]
        if "runpod_idle_timeout" in data:
            settings.runpod_idle_timeout = data["runpod_idle_timeout"]
        if "runpod_pods" in data:
            settings.runpod_pods = data["runpod_pods"]
        if "network_access" in data:
            settings.network_access = data["network_access"]
        if "app_port" in data:
            settings.app_port = max(1024, min(65535, int(data["app_port"])))

        await session.commit()
        await session.refresh(settings)

        logger.info(
            "Imported settings from backup (exported_at=%s)",
            data.get("exported_at", "unknown"),
        )

        return _build_response(settings)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error importing settings: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to import settings: {str(e)}",
        )


# ── Built-in System Prompt Lookup ────────────────────────────────────


@router.get(
    "/builtin-prompt",
    summary="Get built-in system prompt for a model",
)
async def get_builtin_prompt_endpoint(
    model_name: str,
    prompt_type: str = "image",
) -> dict:
    """Get the built-in system prompt for a given model name.

    Used by the frontend to display the default prompt as placeholder text.

    Args:
        model_name: The generation model key (e.g. "flux2_klein_dev_9b", "ltx_2.3")
        prompt_type: "image" or "video"

    Returns:
        Dictionary with the built-in prompt text.
    """
    from backend.services.llm.prompt_enhancer import get_builtin_prompt

    prompt = get_builtin_prompt(model_name, prompt_type)
    return {"model_name": model_name, "prompt_type": prompt_type, "prompt": prompt}


# ── RunPod Endpoints ──────────────────────────────────────────────────


class RunPodTestRequest(BaseModel):
    """Request to test a RunPod API key."""
    api_key: str


class RunPodPodActionRequest(BaseModel):
    """Request to start or stop a specific pod."""
    pod_id: str


@router.post(
    "/runpod/test",
    summary="Test RunPod API key",
)
async def test_runpod(
    req: RunPodTestRequest,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Test a RunPod API key by listing pods."""
    from backend.services.runpod.manager import RunPodManager

    manager = RunPodManager.get_instance()
    result = await manager.test_api_key(req.api_key)
    return result


@router.get(
    "/runpod/status",
    summary="Get RunPod pod statuses",
)
async def get_runpod_status(
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Get current status of all configured RunPod pods."""
    from backend.services.runpod.manager import RunPodManager

    settings = await _get_or_create_settings(session)
    if not settings.runpod_enabled or not settings.runpod_api_key:
        return {"enabled": False, "pods": []}

    manager = RunPodManager.get_instance()
    if not manager.is_configured:
        manager.configure(
            api_key=settings.runpod_api_key,
            pod_configs=settings.runpod_pods or [],
            idle_timeout_minutes=settings.runpod_idle_timeout or 30,
        )

    statuses = await manager.get_all_pod_statuses()
    return {"enabled": True, "pods": statuses}


@router.post(
    "/runpod/start",
    summary="Start a RunPod pod",
)
async def start_runpod_pod(
    req: RunPodPodActionRequest,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Start/resume a specific RunPod pod."""
    from backend.services.runpod.manager import RunPodManager

    settings = await _get_or_create_settings(session)
    if not settings.runpod_enabled or not settings.runpod_api_key:
        raise HTTPException(status_code=400, detail="RunPod not enabled or API key not set")

    manager = RunPodManager.get_instance()
    if not manager.is_configured:
        manager.configure(
            api_key=settings.runpod_api_key,
            pod_configs=settings.runpod_pods or [],
            idle_timeout_minutes=settings.runpod_idle_timeout or 30,
        )

    status = await manager.start_pod(req.pod_id)
    return {
        "pod_id": status.pod_id,
        "state": status.state.value,
        "error": status.error_message,
    }


@router.post(
    "/runpod/stop",
    summary="Stop a RunPod pod",
)
async def stop_runpod_pod(
    req: RunPodPodActionRequest,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Stop a specific RunPod pod."""
    from backend.services.runpod.manager import RunPodManager

    settings = await _get_or_create_settings(session)
    if not settings.runpod_enabled or not settings.runpod_api_key:
        raise HTTPException(status_code=400, detail="RunPod not enabled or API key not set")

    manager = RunPodManager.get_instance()
    if not manager.is_configured:
        manager.configure(
            api_key=settings.runpod_api_key,
            pod_configs=settings.runpod_pods or [],
            idle_timeout_minutes=settings.runpod_idle_timeout or 30,
        )

    status = await manager.stop_pod(req.pod_id)
    return {
        "pod_id": status.pod_id,
        "state": status.state.value,
        "error": status.error_message,
    }


# ── Project Directory ───────────────────────────────────────────────

@router.post(
    "/browse-directory",
    summary="Open native folder picker dialog",
)
async def browse_directory():
    """Open a native folder picker dialog and return the selected path.

    Uses tkinter for cross-platform native file dialog support.
    Falls back gracefully if tkinter is not available (headless server).
    """
    import asyncio

    def _pick_folder() -> str | None:
        try:
            import tkinter as tk
            from tkinter import filedialog

            root = tk.Tk()
            root.withdraw()
            root.attributes("-topmost", True)
            folder = filedialog.askdirectory(
                title="Select Project Directory",
                mustexist=False,
            )
            root.destroy()
            return folder if folder else None
        except Exception as e:
            logger.warning(f"Folder picker not available: {e}")
            return None

    loop = asyncio.get_event_loop()
    selected = await loop.run_in_executor(None, _pick_folder)

    if selected is None:
        return {"success": False, "path": None, "message": "No folder selected or dialog unavailable"}

    return {"success": True, "path": selected, "message": "Folder selected"}


@router.post(
    "/change-project-dir",
    response_model=ChangeProjectDirResponse,
    summary="Change the project data directory",
)
async def change_project_dir(
    req: ChangeProjectDirRequest,
    session: AsyncSession = Depends(get_session),
):
    """Change the project data directory with optional data migration.

    Args:
        req: Contains new_path and move_data flag.
        session: Database session.

    Returns:
        Result of the directory change operation.
    """
    import asyncio

    settings = await _get_or_create_settings(session)
    old_path_str = settings.project_dir or str(env_settings.project_dir)
    old_path = Path(old_path_str).expanduser().resolve()
    new_path = Path(req.new_path).expanduser().resolve()

    # Validate the new path
    if not req.new_path.strip():
        raise HTTPException(status_code=400, detail="Path cannot be empty")

    if old_path == new_path:
        raise HTTPException(status_code=400, detail="New path is the same as the current path")

    # Check if new path is inside old path (would cause recursive issues)
    try:
        new_path.relative_to(old_path)
        raise HTTPException(
            status_code=400,
            detail="New path cannot be inside the current project directory"
        )
    except ValueError:
        pass  # Not relative — this is fine

    def _do_directory_change() -> str:
        """Perform directory operations in a thread (blocking I/O)."""
        # Create the new directory if it doesn't exist
        new_path.mkdir(parents=True, exist_ok=True)

        if req.move_data and old_path.exists():
            # Move all contents from old directory to new directory
            moved_count = 0
            errors = []
            for item in old_path.iterdir():
                dest = new_path / item.name
                try:
                    if dest.exists():
                        # Skip if destination already exists
                        logger.warning(f"Skipping {item.name} — already exists in destination")
                        continue
                    shutil.move(str(item), str(dest))
                    moved_count += 1
                except Exception as e:
                    errors.append(f"{item.name}: {e}")
                    logger.error(f"Failed to move {item}: {e}")

            if errors:
                return f"Moved {moved_count} items with {len(errors)} errors: {'; '.join(errors[:3])}"
            return f"Moved {moved_count} items to new directory"
        else:
            return "Directory set (no data moved)"

    try:
        loop = asyncio.get_event_loop()
        result_msg = await loop.run_in_executor(None, _do_directory_change)
    except Exception as e:
        logger.error(f"Failed to change project directory: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to change directory: {e}")

    # Save the new path to database settings
    settings.project_dir = str(new_path)
    session.add(settings)
    await session.commit()

    # Update the runtime config so it takes effect immediately
    env_settings.project_dir = new_path

    logger.info(f"Project directory changed: {old_path} → {new_path} ({result_msg})")

    return ChangeProjectDirResponse(
        success=True,
        message=result_msg,
        old_path=str(old_path),
        new_path=str(new_path),
    )
