"""Concept and Video Flow endpoints for RBMN Storyboard App."""
import asyncio
import logging
import time
from typing import Any, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from backend.database import get_session
from backend.database.models import Project, Scene, AppSettings

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/projects/{project_id}/concept", tags=["concept"])

# ── In-memory flow generation progress tracking ────────────────────────
# Key: str(project_id) → progress dict
_flow_progress: dict[str, dict] = {}


# ── Request / response models ────────────────────────────────────────

class CharacterModel(BaseModel):
    """A character in the concept."""
    name: str = ""
    description: str = ""
    image_path: Optional[str] = None  # relative path to character reference image


class ConceptData(BaseModel):
    """Full concept payload (read + write)."""
    song_title: str = ""
    concept_text: str = ""
    style_text: str = ""
    image_direction: str = ""  # preset style: photorealistic, cinematic, cartoon, anime, sketch, etc.
    custom_image_direction: str = ""  # free-text when image_direction == 'custom'
    characters: list[CharacterModel] = []
    resolution_width: int = 1536
    resolution_height: int = 864
    project_fps: int = 24
    global_seed_enabled: bool = False
    global_seed: int = 0
    use_transition_lora: bool = False
    transition_lora_strength: float = 1.0
    random_ken_burns: bool = False
    ken_burns_allowed_effects: list[str] = []  # empty = all effects allowed


class SceneFlowIdea(BaseModel):
    """A single scene's flow idea."""
    scene_id: str
    flow_idea: str


class VideoFlowResponse(BaseModel):
    """Response after generating or fetching video flow."""
    ideas: list[SceneFlowIdea]
    scene_count: int = 0  # actual DB scene count for frontend display


class FlowProgressResponse(BaseModel):
    """Response for flow generation progress polling."""
    status: str = "idle"  # 'idle', 'running', 'done', 'failed'
    total_scenes: int = 0
    total_batches: int = 0
    completed_batches: int = 0
    current_message: Optional[str] = None
    error: Optional[str] = None
    started_at: Optional[float] = None  # epoch seconds


class GenerateCharacterRequest(BaseModel):
    """Request to generate a character image."""
    character_index: int
    prompt_override: Optional[str] = None  # optional custom prompt
    width: int = 1024
    height: int = 1024
    workflow_type: Optional[str] = None  # auto-selected if not provided
    reference_asset_ids: list[str] = []
    seed: Optional[int] = None


# ── Helpers ───────────────────────────────────────────────────────────

async def _get_project(project_id: UUID, session: AsyncSession) -> Project:
    project = await session.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail=f"Project {project_id} not found")
    return project


# ── Concept CRUD ──────────────────────────────────────────────────────

@router.get("", response_model=ConceptData, summary="Get project concept")
async def get_concept(
    project_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> ConceptData:
    """Return the concept data stored in project.settings."""
    project = await _get_project(project_id, session)
    s = project.settings or {}
    return ConceptData(
        song_title=s.get("song_title", ""),
        concept_text=s.get("concept_text", ""),
        style_text=s.get("style_text", ""),
        image_direction=s.get("image_direction", ""),
        custom_image_direction=s.get("custom_image_direction", ""),
        characters=[CharacterModel(**c) for c in s.get("characters", [])],
        resolution_width=s.get("resolution_width", 1536),
        resolution_height=s.get("resolution_height", 864),
        project_fps=s.get("project_fps", 24),
        global_seed_enabled=s.get("global_seed_enabled", False),
        global_seed=s.get("global_seed", 0),
        use_transition_lora=s.get("use_transition_lora", False),
        transition_lora_strength=s.get("transition_lora_strength", 1.0),
        random_ken_burns=s.get("random_ken_burns", False),
        ken_burns_allowed_effects=s.get("ken_burns_allowed_effects", []),
    )


@router.put("", response_model=ConceptData, summary="Save project concept")
async def save_concept(
    project_id: UUID,
    req: ConceptData,
    session: AsyncSession = Depends(get_session),
) -> ConceptData:
    """Persist concept data into project.settings."""
    project = await _get_project(project_id, session)
    settings = dict(project.settings or {})
    settings["song_title"] = req.song_title
    settings["concept_text"] = req.concept_text
    settings["style_text"] = req.style_text
    settings["image_direction"] = req.image_direction
    settings["custom_image_direction"] = req.custom_image_direction
    settings["characters"] = [c.model_dump() for c in req.characters]
    settings["resolution_width"] = req.resolution_width
    settings["resolution_height"] = req.resolution_height
    settings["project_fps"] = req.project_fps
    settings["global_seed_enabled"] = req.global_seed_enabled
    settings["global_seed"] = req.global_seed
    settings["use_transition_lora"] = req.use_transition_lora
    settings["transition_lora_strength"] = req.transition_lora_strength
    settings["random_ken_burns"] = req.random_ken_burns
    settings["ken_burns_allowed_effects"] = req.ken_burns_allowed_effects
    project.settings = settings
    await session.commit()
    await session.refresh(project)
    logger.info(f"Saved concept for project {project_id}")
    return req


# ── Base on Lyrics ───────────────────────────────────────────────────

class BaseOnLyricsRequest(BaseModel):
    """Current state of concept fields so LLM knows what to generate."""
    song_title: str = ""
    concept_text: str = ""
    style_text: str = ""


class BaseOnLyricsResponse(BaseModel):
    """Generated concept fields from lyrics analysis."""
    song_title: str = ""
    concept_text: str = ""
    style_text: str = ""


@router.post("/base-on-lyrics", response_model=BaseOnLyricsResponse, summary="Generate concept from lyrics via LLM")
async def base_on_lyrics(
    project_id: UUID,
    req: BaseOnLyricsRequest,
    session: AsyncSession = Depends(get_session),
) -> BaseOnLyricsResponse:
    """Use an LLM to generate concept/style/title from the project's lyrics.

    Priority for lyrics source:
    1. User-provided initial_text (from Audio tab import)
    2. Whisper-detected full_text

    If concept_text is already filled, only generate style_text (and vice versa).
    If song_title is empty, generate one too.
    """
    from backend.database.models import Lyrics as LyricsModel

    project = await _get_project(project_id, session)

    # Fetch lyrics
    lyrics_stmt = select(LyricsModel).where(LyricsModel.project_id == project_id)
    lyrics_result = await session.execute(lyrics_stmt)
    lyrics_record = lyrics_result.scalars().first()

    # Determine best lyrics source
    lyrics_text = ""
    lyrics_source = ""
    if lyrics_record:
        initial = getattr(lyrics_record, "initial_text", "") or ""
        whisper = lyrics_record.full_text or ""
        if initial.strip():
            lyrics_text = initial.strip()
            lyrics_source = "user-provided lyrics"
        elif whisper.strip():
            lyrics_text = whisper.strip()
            lyrics_source = "Whisper-detected lyrics"

    if not lyrics_text:
        raise HTTPException(
            status_code=400,
            detail="No lyrics available. Please add lyrics in the Audio tab or process audio with Whisper first."
        )

    # Determine what needs generating
    has_title = bool(req.song_title.strip())
    has_concept = bool(req.concept_text.strip())
    has_style = bool(req.style_text.strip())

    if has_concept and has_style and has_title:
        # Everything is filled — nothing to generate
        return BaseOnLyricsResponse(
            song_title=req.song_title,
            concept_text=req.concept_text,
            style_text=req.style_text,
        )

    # Build generation instructions
    generate_parts = []
    if not has_title:
        generate_parts.append('"song_title": a creative, fitting title for this song/video')
    if not has_concept:
        generate_parts.append('"concept_text": an overall video concept (themes, narrative arc, mood, story) in 2-4 sentences')
    if not has_style:
        generate_parts.append('"style_text": a visual style description (color palette, aesthetic, cinematography, mood, art direction) in 1-3 sentences')

    # Include existing fields as context
    context_parts = []
    if has_title:
        context_parts.append(f"Song Title: {req.song_title}")
    if has_concept:
        context_parts.append(f"Existing Concept: {req.concept_text}")
    if has_style:
        context_parts.append(f"Existing Visual Style: {req.style_text}")

    # Include existing characters as context
    existing_characters = (project.settings or {}).get("characters", [])
    if existing_characters:
        char_lines = []
        for i, c in enumerate(existing_characters, 1):
            name = c.get("name", "Unnamed")
            desc = c.get("description", "No description")
            char_lines.append(f"  Character {i}: {name} — {desc}")
        context_parts.append("Existing Characters:\n" + "\n".join(char_lines))

    context_block = "\n".join(context_parts) if context_parts else "(No existing concept data)"

    system_prompt = (
        "You are a creative director for AI-generated music videos and narration videos. "
        "Given song lyrics, generate the requested fields for a video production concept. "
        "Your output should be evocative, specific, and practical — aimed at guiding AI image/video generation.\n\n"
        "LYRICS-DRIVEN CONCEPT GENERATION:\n"
        "- The concept should reflect the NARRATIVE ARC of the lyrics in chronological order. "
        "Identify key visual elements (objects, characters, settings, actions) as they appear in the lyrics "
        "and incorporate them into the concept so the video tells the same story the lyrics tell.\n"
        "- Call out specific concrete imagery from the lyrics (e.g., 'a red car', 'the ocean at night', "
        "'dancing in a crowded room') — these will be used as visual anchors across scenes.\n"
        "- The style should support the lyrical content — match the visual aesthetic to the emotional "
        "journey of the song.\n\n"
        "Note: This app supports character reference images (up to 5 characters) that can be used across scenes "
        "to maintain visual consistency. When writing the concept, feel free to reference characters and their roles "
        "in the narrative — the user can define and generate character images separately.\n\n"
        "IMPORTANT: Return ONLY a JSON object with the requested keys. "
        "No markdown, no code fences, no explanation — just the raw JSON object."
    )

    user_prompt = (
        f"Lyrics ({lyrics_source}):\n{lyrics_text}\n\n"
        f"Existing project data:\n{context_block}\n\n"
        f"Generate the following fields as a JSON object:\n"
        + "\n".join(f"  - {p}" for p in generate_parts)
        + "\n\nReturn ONLY the JSON object with the requested keys."
    )

    # Get LLM settings
    settings_stmt = select(AppSettings).where(AppSettings.id == 1)
    settings_result = await session.execute(settings_stmt)
    app_settings = settings_result.scalars().first()
    if not app_settings:
        raise HTTPException(status_code=400, detail="App settings not configured")

    from backend.api.settings import resolve_llm_config
    provider, api_key, model = resolve_llm_config(app_settings)

    try:
        raw_text = await asyncio.to_thread(
            _call_llm, provider, api_key, model, system_prompt, user_prompt
        )
    except Exception as e:
        logger.error(f"LLM base-on-lyrics failed: {e}")
        raise HTTPException(status_code=500, detail=f"LLM call failed: {e}")

    # Parse JSON response
    import json
    try:
        cleaned = raw_text.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else cleaned[3:]
        if cleaned.endswith("```"):
            cleaned = cleaned.rsplit("```", 1)[0]
        cleaned = cleaned.strip()
        result = json.loads(cleaned)
        if not isinstance(result, dict):
            raise ValueError("Expected a JSON object")
    except (json.JSONDecodeError, ValueError) as e:
        logger.error(f"Failed to parse LLM response as JSON: {e}\nRaw: {raw_text[:500]}")
        raise HTTPException(status_code=500, detail="Failed to parse LLM response")

    # Merge: keep existing values, fill in generated ones
    return BaseOnLyricsResponse(
        song_title=req.song_title if has_title else result.get("song_title", ""),
        concept_text=req.concept_text if has_concept else result.get("concept_text", ""),
        style_text=req.style_text if has_style else result.get("style_text", ""),
    )


# ── Autogenerate Characters ─────────────────────────────────────────

class AutogenCharactersResponse(BaseModel):
    """Response from autogenerate characters."""
    characters: list[CharacterModel] = []
    job_ids: list[str] = []
    message: str = ""


@router.post(
    "/characters/autogenerate",
    response_model=AutogenCharactersResponse,
    summary="Auto-generate characters from concept/lyrics via LLM",
)
async def autogenerate_characters(
    project_id: UUID,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> AutogenCharactersResponse:
    """Use an LLM to analyze lyrics, concept, and style to generate a cast of characters,
    then queue image generation jobs for each one.

    The LLM determines appropriate characters based on the song's narrative,
    generating names and detailed visual descriptions for each.
    Supports up to 5 characters to keep the cast focused and manageable.
    """
    from backend.database.models import Job, JobType, JobStatus, Lyrics as LyricsModel
    from backend.services.jobs.queue import JobQueue

    project = await _get_project(project_id, session)
    s = project.settings or {}
    concept_text = s.get("concept_text", "")
    style_text = s.get("style_text", "")
    song_title = s.get("song_title", "")
    resolution_w = s.get("resolution_width", 1024)
    resolution_h = s.get("resolution_height", 1024)

    # Fetch lyrics
    lyrics_stmt = select(LyricsModel).where(LyricsModel.project_id == project_id)
    lyrics_result = await session.execute(lyrics_stmt)
    lyrics_record = lyrics_result.scalars().first()

    lyrics_text = ""
    if lyrics_record:
        initial = getattr(lyrics_record, "initial_text", "") or ""
        whisper = lyrics_record.full_text or ""
        lyrics_text = initial.strip() or whisper.strip()

    # Resolve image direction
    image_direction = s.get("image_direction", "")
    direction_text = ""
    if image_direction and image_direction != "none" and image_direction != "":
        if image_direction == "custom":
            custom_dir = s.get("custom_image_direction", "")
            if custom_dir:
                direction_text = custom_dir
        else:
            direction_text = image_direction.replace("_", " ").title()

    # Build context for the LLM
    context_parts = []
    if song_title:
        context_parts.append(f"Song Title: {song_title}")
    if concept_text:
        context_parts.append(f"Video Concept: {concept_text}")
    if style_text:
        context_parts.append(f"Visual Style: {style_text}")
    if direction_text:
        context_parts.append(f"Image Direction / Art Style: {direction_text}")
    if lyrics_text:
        context_parts.append(f"Lyrics:\n{lyrics_text}")

    if not context_parts:
        raise HTTPException(
            status_code=400,
            detail="Not enough information to generate characters. Add a concept, lyrics, or song title first."
        )

    context_block = "\n\n".join(context_parts)

    system_prompt = (
        "You are a creative director for AI-generated music videos and narration videos. "
        "Analyze the provided song/video information and create a cast of characters that would appear in this video.\n\n"
        "Guidelines:\n"
        "- Generate between 1 and 5 characters based on what the song/concept calls for\n"
        "- Each character needs a short name and a DETAILED visual description\n"
        "- Descriptions should be specific enough for an AI image generator: include ethnicity/skin tone, "
        "hair color/style, eye color, age range, build, clothing, accessories, and distinguishing features\n"
        "- Match the visual style and mood of the project\n"
        "- Characters should make sense for the narrative in the lyrics/concept\n"
        "- If the song is abstract or doesn't clearly reference people, create symbolic/artistic characters "
        "that embody the themes\n\n"
        "IMPORTANT: Return ONLY a JSON array of objects, each with \"name\" and \"description\" keys. "
        "No markdown, no code fences, no explanation — just the raw JSON array."
    )

    user_prompt = (
        f"{context_block}\n\n"
        "Based on the above, generate a cast of characters for this video. "
        "Return a JSON array of objects with \"name\" and \"description\" keys."
    )

    # Get LLM settings
    settings_stmt = select(AppSettings).where(AppSettings.id == 1)
    settings_result = await session.execute(settings_stmt)
    app_settings = settings_result.scalars().first()
    if not app_settings:
        raise HTTPException(status_code=400, detail="App settings not configured")

    from backend.api.settings import resolve_llm_config
    provider, api_key, model = resolve_llm_config(app_settings)

    try:
        raw_text = await asyncio.to_thread(
            _call_llm, provider, api_key, model, system_prompt, user_prompt
        )
    except Exception as e:
        logger.error(f"LLM autogenerate characters failed: {e}")
        raise HTTPException(status_code=500, detail=f"LLM call failed: {e}")

    # Parse JSON response
    import json
    try:
        cleaned = raw_text.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else cleaned[3:]
        if cleaned.endswith("```"):
            cleaned = cleaned.rsplit("```", 1)[0]
        cleaned = cleaned.strip()
        chars_list = json.loads(cleaned)
        if not isinstance(chars_list, list):
            raise ValueError("Expected a JSON array")
    except (json.JSONDecodeError, ValueError) as e:
        logger.error(f"Failed to parse LLM response: {e}\nRaw: {raw_text[:500]}")
        raise HTTPException(status_code=500, detail="Failed to parse LLM character response")

    # Limit to 5 characters
    chars_list = chars_list[:5]

    # Build character models
    new_characters = [
        CharacterModel(
            name=c.get("name", f"Character {i+1}"),
            description=c.get("description", ""),
            image_path=None,
        )
        for i, c in enumerate(chars_list)
    ]

    # Save characters to project.settings
    settings = dict(project.settings or {})
    settings["characters"] = [c.model_dump() for c in new_characters]
    project.settings = settings
    await session.commit()
    await session.refresh(project)

    # Queue image generation jobs for each character
    job_queue: JobQueue = request.app.state.job_queue
    job_ids = []

    # Build combined style for character image prompts
    combined_char_style = direction_text or style_text or "cinematic, detailed"
    if direction_text and style_text:
        combined_char_style = f"{direction_text}, {style_text}"

    for idx, char in enumerate(new_characters):
        char_prompt = (
            f"Character portrait: {char.name}. "
            f"{char.description}. "
            f"Style: {combined_char_style}. "
            "Full body or upper body shot, clear features, studio lighting, "
            "character reference sheet style."
        )

        job = Job(
            project_id=project_id,
            scene_id=None,
            job_type=JobType.IMAGE,
            status=JobStatus.PENDING,
            priority=idx,  # ascending priority so they process in order
            parameters={
                "workflow_type": "klein_t2i",
                "prompt": char_prompt,
                "width": min(resolution_w, 1024),
                "height": min(resolution_h, 1024),
                "reference_asset_ids": [],
                "character_gen": True,
                "character_index": idx,
            },
        )
        session.add(job)
        await session.flush()
        job_ids.append(str(job.id))

    await session.commit()
    job_queue.notify()

    logger.info(
        f"Autogenerated {len(new_characters)} characters for project {project_id}, "
        f"queued {len(job_ids)} image jobs"
    )

    return AutogenCharactersResponse(
        characters=new_characters,
        job_ids=job_ids,
        message=f"Generated {len(new_characters)} characters and queued image generation for each.",
    )


# ── Video Flow ────────────────────────────────────────────────────────

@router.get("/flow", response_model=VideoFlowResponse, summary="Get video flow")
async def get_video_flow(
    project_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> VideoFlowResponse:
    """Return the per-scene flow ideas from scene.parameters."""
    await _get_project(project_id, session)
    stmt = select(Scene).where(Scene.project_id == project_id).order_by(Scene.order_index)
    result = await session.execute(stmt)
    scenes = result.scalars().all()
    ideas = [
        SceneFlowIdea(scene_id=str(s.id), flow_idea=s.parameters.get("flow_idea", ""))
        for s in scenes
    ]
    return VideoFlowResponse(ideas=ideas, scene_count=len(scenes))


@router.get("/flow/progress", response_model=FlowProgressResponse, summary="Poll flow generation progress")
async def get_flow_progress(project_id: UUID) -> FlowProgressResponse:
    """Return the current progress of flow generation for this project."""
    key = str(project_id)
    prog = _flow_progress.get(key)
    if not prog:
        return FlowProgressResponse(status="idle")
    return FlowProgressResponse(**prog)


@router.post("/flow/generate", response_model=VideoFlowResponse, summary="Generate video flow via LLM")
async def generate_video_flow(
    project_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> VideoFlowResponse:
    """Use an LLM to generate a cohesive scene-by-scene storyboard flow
    based on the project concept, style, characters, lyrics, and existing scenes."""
    project = await _get_project(project_id, session)

    # Gather scenes
    stmt = select(Scene).where(Scene.project_id == project_id).order_by(Scene.order_index)
    result = await session.execute(stmt)
    scenes = result.scalars().all()

    if not scenes:
        raise HTTPException(status_code=400, detail="No scenes found. Create scenes first.")

    # Initialize progress tracking
    pid = str(project_id)
    _flow_progress[pid] = {
        "status": "running",
        "total_scenes": len(scenes),
        "total_batches": 1,
        "completed_batches": 0,
        "current_message": f"Preparing flow generation for {len(scenes)} scenes...",
        "error": None,
        "started_at": time.time(),
    }

    try:
        result = await _generate_flow_inner(
            project_id, project, scenes, session, pid
        )
        _flow_progress[pid]["status"] = "done"
        _flow_progress[pid]["completed_batches"] = _flow_progress[pid]["total_batches"]
        # Count how many scenes actually got filled
        filled = sum(1 for idea in result.ideas if idea.flow_idea and idea.flow_idea.strip())
        if filled == len(scenes):
            _flow_progress[pid]["current_message"] = f"Completed — all {len(scenes)} scenes filled"
        else:
            _flow_progress[pid]["current_message"] = (
                f"Completed — {filled}/{len(scenes)} scenes filled"
            )
        return result
    except Exception as e:
        _flow_progress[pid]["status"] = "failed"
        _flow_progress[pid]["error"] = str(e)
        _flow_progress[pid]["current_message"] = f"Failed: {e}"
        raise


async def _generate_flow_inner(
    project_id: UUID,
    project: Any,
    scenes: list,
    session: AsyncSession,
    pid: str,
) -> "VideoFlowResponse":
    """Inner flow generation logic with progress updates."""
    from backend.database.models import Lyrics as LyricsModel
    from backend.api.generation import _get_scene_lyrics

    # Re-gather concept info (already loaded in caller but keep encapsulated)
    s = project.settings or {}
    concept_text = s.get("concept_text", "")
    style_text = s.get("style_text", "")
    characters = s.get("characters", [])

    # Gather lyrics
    lyrics_stmt = select(LyricsModel).where(LyricsModel.project_id == project_id)
    lyrics_result = await session.execute(lyrics_stmt)
    lyrics_record = lyrics_result.scalars().first()
    lyrics_words = lyrics_record.words if lyrics_record else []
    full_lyrics = ""
    if lyrics_record:
        full_lyrics = (getattr(lyrics_record, "initial_text", "") or "").strip()
        if not full_lyrics:
            full_lyrics = (lyrics_record.full_text or "").strip()

    # Build LLM prompt
    char_block = ""
    for i, c in enumerate(characters, 1):
        char_block += f"\n  Character {i}: {c.get('name', 'Unnamed')} — {c.get('description', 'No description')}"
    if not char_block:
        char_block = "\n  (No characters defined)"

    # Build scene list with per-scene lyrics
    scene_lines = []
    for i, sc in enumerate(scenes):
        scene_lyrics = _get_scene_lyrics(sc, lyrics_words) if lyrics_words else ""
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
        "visually in that scene — the SPECIFIC LOCATION, camera movement, action, mood, and composition — "
        "so that an AI image/video generator can produce compelling, visually DISTINCT frames. "
        "Keep each idea under 100 words.\n\n"
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
        "CRITICAL — VISUAL DIVERSITY ACROSS SCENES:\n"
        "Each scene MUST take place in a DIFFERENT physical location or setting. Do NOT set every scene "
        "in the same place with different camera angles — that produces identical-looking images. Instead:\n"
        "- Vary the LOCATION: street → park → rooftop → interior → bridge → market → alley → waterfront\n"
        "- Vary the TIME OF DAY: dawn → morning → midday → golden hour → dusk → night\n"
        "- Vary the ATMOSPHERE: sunny → overcast → neon-lit → foggy → rainy → warm indoor glow\n"
        "- Vary the CAMERA SHOT SIZE: extreme wide shot (EWS — subject tiny in environment) → wide shot (full body) → "
        "medium shot (waist up) → medium close-up (chest up) → close-up (face) → extreme close-up (detail/eyes/hands) → "
        "insert shot (tight on a key object)\n"
        "- Vary the CAMERA ANGLE: eye level (neutral) → low angle / hero shot (looking up — power) → "
        "high angle (looking down — vulnerability) → bird's eye / top-down (straight down) → "
        "Dutch angle (tilted — unease) → over the shoulder (dialogue/relationship) → POV (first-person) → "
        "profile / side angle (cinematic, graphic)\n"
        "- Vary the CAMERA MOVEMENT: static locked-off → slow dolly push in → tracking shot → crane rising → "
        "orbit / arc around subject → handheld (energy, urgency) → pan across → tilt up/down reveal → "
        "pedestal up/down → whip pan (scene transition energy)\n"
        "- Avoid using the same shot size or angle for consecutive scenes. Create visual rhythm: "
        "wide → close → medium → extreme close → wide. Alternate between movement and static shots.\n"
        "CAMERA DIRECTION CONTINUITY: When adjacent scenes share a visual transition (e.g., scene B "
        "starts from scene A's final frame), avoid abrupt direction reversals. If scene A ends with a "
        "leftward pan, scene B should NOT immediately pan right — instead, continue the motion, settle "
        "to a static shot, or smoothly transition to a new direction. Direction changes between scenes "
        "should feel intentional, not jarring.\n"
        "Even if the song concept is about one journey through one area, find DIFFERENT specific spots "
        "within that area. A 'neighborhood walk' should visit: the park entrance, a shop interior, "
        "a rooftop view, a crosswalk, a cafe patio — NOT the same street 10 times.\n\n"
        "Note: This app supports character reference images (up to 5 characters) that maintain visual "
        "consistency across scenes. When characters are defined, feel free to reference them by name "
        "in your scene descriptions — the system can use their reference images to keep their appearance "
        "consistent throughout the video.\n\n"
        "IMPORTANT: Return ONLY a JSON array of strings, one per scene, in order. "
        "No markdown, no labels, no explanation — just the JSON array."
    )

    # Include full lyrics for overall narrative arc context
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

    # Get LLM settings
    settings_stmt = select(AppSettings).where(AppSettings.id == 1)
    settings_result = await session.execute(settings_stmt)
    app_settings = settings_result.scalars().first()
    if not app_settings:
        raise HTTPException(status_code=400, detail="App settings not configured")

    from backend.api.settings import resolve_llm_config
    provider, api_key, model = resolve_llm_config(app_settings)

    # Determine batch size based on provider and scene count.
    # Ollama local models have smaller context windows (4K-8K), so we process
    # scenes in small batches to avoid context overflow. Cloud providers can
    # handle more scenes at once but still benefit from batching on large projects.
    if provider == "ollama":
        batch_size = 5  # ~1500 tokens per batch (system + 5 scenes + output)
    elif len(scenes) > 20:
        batch_size = 10  # Even cloud models can struggle with 40+ scenes
    else:
        batch_size = len(scenes)  # Small projects: send all at once

    ideas_list: list[str] = []

    if batch_size >= len(scenes):
        # Single-shot: send all scenes in one call
        _flow_progress[pid].update({
            "total_batches": 1,
            "completed_batches": 0,
            "current_message": f"Sending {len(scenes)} scenes to {provider} ({model})...",
        })
        flow_max_tokens = max(2000, len(scenes) * 150 + 500)
        logger.info(f"Generating video flow for {len(scenes)} scenes in single call (max_tokens={flow_max_tokens})")
        try:
            raw_text = await asyncio.to_thread(
                _call_llm, provider, api_key, model, system_prompt, user_prompt,
                max_tokens=flow_max_tokens,
            )
        except Exception as e:
            logger.error(f"LLM flow generation failed: {e}")
            raise HTTPException(status_code=500, detail=f"LLM call failed: {e}")
        ideas_list = _parse_flow_json_array(raw_text, len(scenes))
        _flow_progress[pid]["completed_batches"] = 1
    else:
        # Batched + concurrent: process scenes in groups, running multiple
        # batches in parallel across all available Ollama servers (or cloud).
        # Each batch gets concept/style/characters + truncated lyrics summary +
        # its own per-scene lyrics. No sequential dependency between batches —
        # per-scene lyrics provide the primary creative direction.
        total_batches = (len(scenes) + batch_size - 1) // batch_size

        _flow_progress[pid].update({
            "total_batches": total_batches,
            "completed_batches": 0,
            "current_message": f"Processing {total_batches} batches of {batch_size} scenes ({provider})...",
        })

        # Build truncated lyrics summary once (shared across all batches)
        lyrics_summary = ""
        if full_lyrics:
            words = full_lyrics.split()
            if len(words) > 200:
                lyrics_summary = " ".join(words[:200]) + "..."
            else:
                lyrics_summary = full_lyrics

        # Build all batch prompts upfront
        batch_prompts: list[tuple[int, int, str, int]] = []  # (batch_idx, count, prompt, max_tokens)
        for batch_idx in range(0, len(scenes), batch_size):
            batch_scenes = scenes[batch_idx:batch_idx + batch_size]
            batch_scene_lines = scene_lines[batch_idx:batch_idx + batch_size]
            batch_scene_list = "\n".join(batch_scene_lines)

            batch_user_prompt = (
                f"Video Concept: {concept_text or '(not set)'}\n"
                f"Visual Style: {style_text or '(not set)'}\n"
                f"Characters: {char_block}\n"
            )
            if lyrics_summary:
                batch_user_prompt += f"\nNarrative/Lyrics Summary (for overall arc):\n{lyrics_summary}\n"

            batch_user_prompt += (
                f"\nScenes {batch_idx + 1}–{batch_idx + len(batch_scenes)} "
                f"(of {len(scenes)} total, with per-scene lyrics):\n{batch_scene_list}\n\n"
                f"Generate a storyboard idea for each of these {len(batch_scenes)} scenes. "
                f"The lyrics for each scene are your PRIMARY source of visual direction. "
                f"Return a JSON array of {len(batch_scenes)} strings."
            )
            batch_max_tokens = max(1200, len(batch_scenes) * 150 + 300)
            batch_prompts.append((batch_idx, len(batch_scenes), batch_user_prompt, batch_max_tokens))

        # Determine concurrency: number of Ollama servers, or sensible default
        if provider == "ollama" and api_key:
            import json as _jc
            try:
                if api_key.startswith("["):
                    num_servers = len(_jc.loads(api_key))
                else:
                    num_servers = 1
            except Exception:
                num_servers = 1
            concurrency = max(num_servers, 1)
        else:
            concurrency = 3  # Cloud providers can handle parallel calls

        logger.info(
            f"Generating video flow: {total_batches} batches of {batch_size}, "
            f"{concurrency} concurrent (provider={provider}, {len(scenes)} scenes)"
        )

        # Run batches concurrently with a semaphore to limit parallelism
        sem = asyncio.Semaphore(concurrency)
        batch_results: dict[int, list[str]] = {}  # batch_idx -> ideas

        async def _run_batch(b_idx: int, b_count: int, b_prompt: str, b_max_tokens: int) -> None:
            b_num = b_idx // batch_size + 1
            async with sem:
                logger.info(f"  Batch {b_num}/{total_batches}: scenes {b_idx + 1}–{b_idx + b_count}")
                try:
                    raw_text = await asyncio.to_thread(
                        _call_llm, provider, api_key, model, system_prompt, b_prompt,
                        max_tokens=b_max_tokens,
                    )
                    ideas = _parse_flow_json_array(raw_text, b_count)
                except Exception as e:
                    logger.warning(f"  Batch {b_num} failed: {e} — filling with empty ideas")
                    ideas = [""] * b_count

                # Pad/trim to exact count
                while len(ideas) < b_count:
                    ideas.append("")
                ideas = ideas[:b_count]
                batch_results[b_idx] = ideas
                # Update progress
                _flow_progress[pid]["completed_batches"] = len(batch_results)
                _flow_progress[pid]["current_message"] = (
                    f"Batch {len(batch_results)}/{total_batches} complete "
                    f"(scenes {b_idx + 1}–{b_idx + b_count})"
                )
                logger.info(f"  Batch {b_num}: got {len(ideas)} ideas")

        # Launch all batches concurrently (semaphore limits actual parallelism)
        await asyncio.gather(*[
            _run_batch(b_idx, b_count, b_prompt, b_max_tokens)
            for b_idx, b_count, b_prompt, b_max_tokens in batch_prompts
        ])

        # Reassemble results in scene order
        for b_idx, b_count, _, _ in batch_prompts:
            ideas_list.extend(batch_results.get(b_idx, [""] * b_count))

    # Pad to scene count if needed
    while len(ideas_list) < len(scenes):
        ideas_list.append("")

    # ── Retry pass: re-generate any empty ideas individually ──────────
    empty_indices = [i for i, idea in enumerate(ideas_list) if not idea or not idea.strip()]
    if empty_indices:
        logger.warning(
            f"Flow generation: {len(empty_indices)} empty ideas detected — "
            f"retrying individually (scenes: {[i+1 for i in empty_indices]})"
        )
        _flow_progress[pid]["current_message"] = (
            f"Retrying {len(empty_indices)} empty scenes individually..."
        )

        retry_system = (
            "You are a creative director for AI-generated music videos and narration videos. "
            "Given scene details, generate ONE visual storyboard idea under 100 words describing "
            "the specific location, camera movement, action, mood, and composition. "
            "Return ONLY the idea text — no JSON, no labels, no explanation."
        )

        async def _retry_single(idx: int) -> None:
            sc = scenes[idx]
            sc_lyrics = ""
            if lyrics_words:
                sc_lyrics = _get_scene_lyrics(sc, lyrics_words)
            retry_prompt = (
                f"Concept: {concept_text or '(not set)'}\n"
                f"Style: {style_text or '(not set)'}\n"
                f"Scene {idx + 1} \"{sc.name}\" ({sc.start_time:.1f}s – {sc.end_time:.1f}s)\n"
            )
            if sc_lyrics:
                retry_prompt += f"Lyrics: \"{sc_lyrics}\"\n"
            retry_prompt += "\nGenerate one vivid storyboard idea for this scene."

            try:
                raw = await asyncio.to_thread(
                    _call_llm, provider, api_key, model, retry_system, retry_prompt,
                    max_tokens=300,
                )
                text = raw.strip().strip('"').strip("'")
                if text:
                    ideas_list[idx] = text
                    logger.info(f"  Retry scene {idx + 1}: OK ({len(text)} chars)")
                else:
                    logger.warning(f"  Retry scene {idx + 1}: still empty")
            except Exception as e:
                logger.warning(f"  Retry scene {idx + 1} failed: {e}")

        # Determine retry concurrency from provider (use all Ollama servers)
        _retry_conc = 2
        if provider == "ollama" and api_key:
            import json as _rjc
            try:
                if api_key.startswith("["):
                    _retry_conc = max(len(_rjc.loads(api_key)), 1)
            except Exception:
                pass
        retry_sem = asyncio.Semaphore(_retry_conc)

        async def _retry_with_sem(idx: int) -> None:
            async with retry_sem:
                await _retry_single(idx)

        await asyncio.gather(*[_retry_with_sem(i) for i in empty_indices])

        still_empty = sum(1 for i in empty_indices if not ideas_list[i] or not ideas_list[i].strip())
        if still_empty:
            logger.warning(f"Flow generation: {still_empty} scenes still empty after retry")
            _flow_progress[pid]["current_message"] = (
                f"Completed with {still_empty} empty scene(s) — edit manually"
            )
        else:
            logger.info(f"Flow generation: all {len(empty_indices)} retries succeeded")

    # Save to scene parameters and build response
    filled_count = sum(1 for idea in ideas_list if idea and idea.strip())
    _flow_progress[pid]["current_message"] = (
        f"Saving {filled_count}/{len(scenes)} flow ideas to scenes..."
    )
    ideas: list[SceneFlowIdea] = []
    for i, scene in enumerate(scenes):
        idea_text = ideas_list[i] if i < len(ideas_list) else ""
        params = dict(scene.parameters or {})
        params["flow_idea"] = idea_text
        scene.parameters = params
        ideas.append(SceneFlowIdea(scene_id=str(scene.id), flow_idea=idea_text))

    await session.commit()
    logger.info(f"Generated video flow for project {project_id} ({len(scenes)} scenes, {len(ideas_list)} ideas received)")
    return VideoFlowResponse(ideas=ideas, scene_count=len(scenes))


@router.put("/flow/{scene_id}", summary="Update a single scene's flow idea")
async def update_scene_flow(
    project_id: UUID,
    scene_id: UUID,
    req: SceneFlowIdea,
    session: AsyncSession = Depends(get_session),
) -> SceneFlowIdea:
    """Update the flow_idea for a single scene."""
    await _get_project(project_id, session)
    scene = await session.get(Scene, scene_id)
    if not scene or scene.project_id != project_id:
        raise HTTPException(status_code=404, detail="Scene not found")
    params = dict(scene.parameters or {})
    params["flow_idea"] = req.flow_idea
    scene.parameters = params
    await session.commit()
    return SceneFlowIdea(scene_id=str(scene.id), flow_idea=req.flow_idea)


# ── Character Image Generation ────────────────────────────────────────

@router.post("/characters/generate", summary="Generate a character reference image")
async def generate_character_image(
    project_id: UUID,
    req: GenerateCharacterRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Generate a character reference image using the image generation pipeline.
    Creates a job just like normal image generation but uses character description as prompt."""
    from backend.database.models import Job, JobType, JobStatus
    from backend.services.jobs.queue import JobQueue

    project = await _get_project(project_id, session)
    s = project.settings or {}
    characters = s.get("characters", [])

    if req.character_index < 0 or req.character_index >= len(characters):
        raise HTTPException(status_code=400, detail="Invalid character index")

    char = characters[req.character_index]
    style_text = s.get("style_text", "")

    # Resolve image direction for character generation
    image_direction = s.get("image_direction", "")
    direction_text = ""
    if image_direction and image_direction != "none" and image_direction != "":
        if image_direction == "custom":
            custom_dir = s.get("custom_image_direction", "")
            if custom_dir:
                direction_text = custom_dir
        else:
            direction_text = image_direction.replace("_", " ").title()

    # Combine style sources — image direction takes priority as the primary art style
    combined_style = direction_text or style_text or "cinematic, detailed"
    if direction_text and style_text:
        combined_style = f"{direction_text}, {style_text}"

    # Build prompt from character description + style
    char_prompt = req.prompt_override or (
        f"Character portrait: {char.get('name', 'Character')}. "
        f"{char.get('description', '')}. "
        f"Style: {combined_style}. "
        "Full body or upper body shot, clear features, studio lighting, "
        "character reference sheet style."
    )

    # Auto-select workflow based on reference count
    ref_count = len(req.reference_asset_ids)
    wf_mapping = {0: "klein_t2i", 1: "klein_1ref", 2: "klein_2ref", 3: "klein_3ref", 4: "klein_4ref"}
    workflow_type = req.workflow_type or wf_mapping.get(min(ref_count, 4), "klein_t2i")

    # Create a job for image generation (no scene_id — it's a character gen)
    job = Job(
        project_id=project_id,
        scene_id=None,
        job_type=JobType.IMAGE,
        status=JobStatus.PENDING,
        parameters={
            "workflow_type": workflow_type,
            "prompt": char_prompt,
            "width": req.width,
            "height": req.height,
            "seed": req.seed,
            "reference_asset_ids": req.reference_asset_ids,
            "character_gen": True,
            "character_index": req.character_index,
        },
    )
    session.add(job)
    await session.commit()
    await session.refresh(job)

    # Notify the dispatcher
    job_queue: JobQueue = request.app.state.job_queue
    job_queue.notify()

    logger.info(f"Created character generation job {job.id} for character {req.character_index}")
    return {"job_id": str(job.id), "message": "Character image generation started"}


# ── Character Version History ─────────────────────────────────────────

class CharacterVersionResponse(BaseModel):
    """A single character generation version."""
    id: str
    output_path: Optional[str] = None
    prompt: str = ""
    parameters: dict = {}
    status: str = ""
    created_at: Optional[str] = None


@router.get(
    "/characters/{character_index}/versions",
    response_model=list[CharacterVersionResponse],
    summary="List character generation versions",
)
async def get_character_versions(
    project_id: UUID,
    character_index: int,
    session: AsyncSession = Depends(get_session),
) -> list[CharacterVersionResponse]:
    """Get all generation history entries for a specific character index."""
    from backend.database.models import GenerationHistory

    await _get_project(project_id, session)

    stmt = (
        select(GenerationHistory)
        .where(
            GenerationHistory.project_id == project_id,
            GenerationHistory.scene_id.is_(None),
        )
        .order_by(GenerationHistory.created_at.desc())
    )
    result = await session.execute(stmt)
    all_history = result.scalars().all()

    # Filter by character_index in parameters
    versions = []
    for h in all_history:
        params = h.parameters or {}
        if params.get("character_gen") and params.get("character_index") == character_index:
            versions.append(
                CharacterVersionResponse(
                    id=str(h.id),
                    output_path=h.output_path,
                    prompt=params.get("prompt", ""),
                    parameters=params,
                    status=h.status,
                    created_at=h.created_at.isoformat() if h.created_at else None,
                )
            )

    return versions


@router.delete(
    "/characters/{character_index}/versions/{version_id}",
    summary="Delete a character version",
)
async def delete_character_version(
    project_id: UUID,
    character_index: int,
    version_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Delete a character generation history entry and its associated asset."""
    from backend.database.models import GenerationHistory, Asset
    import os

    await _get_project(project_id, session)

    gen = await session.get(GenerationHistory, version_id)
    if not gen or gen.project_id != project_id:
        raise HTTPException(status_code=404, detail="Version not found")

    # Delete associated asset file if exists
    if gen.output_path:
        from backend.config import settings as app_config
        full_path = os.path.join(app_config.project_dir, gen.output_path)
        if os.path.exists(full_path):
            os.remove(full_path)

        # Delete asset record
        asset_stmt = select(Asset).where(Asset.rel_path == gen.output_path, Asset.project_id == project_id)
        asset_result = await session.execute(asset_stmt)
        asset = asset_result.scalars().first()
        if asset:
            await session.delete(asset)

    await session.delete(gen)
    await session.commit()

    return {"message": "Version deleted"}


class SetCharacterImageRequest(BaseModel):
    """Request to set a character's active image."""
    output_path: str


@router.put(
    "/characters/{character_index}/active-image",
    summary="Set a character's active image from versions",
)
async def set_character_active_image(
    project_id: UUID,
    character_index: int,
    req: SetCharacterImageRequest,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Set which generated image is the active one for a character."""
    project = await _get_project(project_id, session)

    s = dict(project.settings or {})
    characters = list(s.get("characters", []))

    if character_index < 0 or character_index >= len(characters):
        raise HTTPException(status_code=400, detail="Invalid character index")

    characters[character_index] = dict(characters[character_index])
    characters[character_index]["image_path"] = req.output_path
    s["characters"] = characters
    project.settings = s
    await session.commit()

    return {"message": "Active image updated", "image_path": req.output_path}


# ── LLM helpers ───────────────────────────────────────────────────────


def _parse_flow_json_array(raw_text: str, expected_count: int) -> list[str]:
    """Parse an LLM response as a JSON array of strings, with fallback repair."""
    import json

    cleaned = raw_text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else cleaned[3:]
    if cleaned.endswith("```"):
        cleaned = cleaned.rsplit("```", 1)[0]
    cleaned = cleaned.strip()

    try:
        ideas_list = json.loads(cleaned)
        if not isinstance(ideas_list, list):
            raise ValueError("Expected a JSON array")
        return [str(item) for item in ideas_list]
    except (json.JSONDecodeError, ValueError) as e:
        logger.error(f"Failed to parse flow JSON: {e}\nRaw: {raw_text[:500]}")
        repaired = _try_repair_truncated_json_array(cleaned)
        if repaired is not None:
            logger.info(f"Repaired truncated JSON: got {len(repaired)} of {expected_count} ideas")
            return repaired
        # Final fallback: split by double newline
        return [p.strip() for p in raw_text.split("\n\n") if p.strip()]


def _try_repair_truncated_json_array(text: str) -> list[str] | None:
    """Attempt to recover a JSON array of strings that was truncated mid-stream.

    When the LLM hits its token limit, the JSON often ends like:
        ["idea 1", "idea 2", "idea 3 which was cut off mid-sen
    This function finds the last complete string element and parses up to it.
    """
    import json as _json

    text = text.strip()
    if not text.startswith("["):
        return None

    # Try progressively trimming from the end to find valid JSON
    # Strategy: find the last complete '", ' or '"\n' boundary
    last_good = -1
    for i in range(len(text) - 1, 0, -1):
        if text[i] == '"':
            # Try parsing up to this quote + closing bracket
            candidate = text[:i + 1].rstrip().rstrip(",").rstrip() + "]"
            try:
                result = _json.loads(candidate)
                if isinstance(result, list) and len(result) > 0:
                    return [str(item) for item in result]
            except _json.JSONDecodeError:
                continue

    return None


def _call_llm(
    provider: str,
    api_key: str,
    model: str,
    system_prompt: str,
    user_prompt: str,
    max_tokens: int = 2000,
) -> str:
    """Blocking LLM call — intended to be called via asyncio.to_thread.

    Args:
        max_tokens: Maximum output tokens. Default 2000 is fine for single-scene
                    operations; callers generating content for many scenes (video flow,
                    suggest-timeline) should pass a higher value.
    """
    if provider == "openai":
        from openai import OpenAI
        client = OpenAI(api_key=api_key)
        # Newer OpenAI models (GPT-4.1+, GPT-5.x, o-series, chatgpt-* series)
        # require max_completion_tokens instead of the legacy max_tokens param
        _new_style = any(
            model.startswith(p)
            for p in ("gpt-4.1", "gpt-5", "chatgpt", "o1", "o3", "o4")
        )
        # Reasoning models (GPT-5.x, o-series) spend output tokens on
        # internal thinking BEFORE producing visible content.  Boost the
        # budget so the caller's intended output size still fits after
        # the model's chain-of-thought overhead.
        _is_reasoning = any(model.startswith(p) for p in ("gpt-5", "o1", "o3", "o4"))
        effective_max = max(max_tokens * 3, 8192) if _is_reasoning else max_tokens

        extra_params: dict = {}
        if _new_style:
            extra_params["max_completion_tokens"] = effective_max
            # These models only accept temperature=1 (the default)
        else:
            extra_params["max_tokens"] = max_tokens
            extra_params["temperature"] = 0.8
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            **extra_params,
        )
        choice = response.choices[0]
        content = choice.message.content
        # Guard against None/empty content — can happen with reasoning models,
        # content filters, or when finish_reason is not 'stop'
        if not content or not content.strip():
            reason = getattr(choice, "finish_reason", "unknown")
            refusal = getattr(choice.message, "refusal", None)
            detail = f"Empty response from {model} (finish_reason={reason})"
            if refusal:
                detail += f", refusal={refusal}"
            raise ValueError(detail)
        return content

    elif provider == "anthropic":
        from anthropic import Anthropic
        client = Anthropic(api_key=api_key)
        response = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )
        content = response.content[0].text if response.content else ""
        if not content or not content.strip():
            raise ValueError(f"Empty response from {model} (stop_reason={response.stop_reason})")
        return content

    elif provider == "gemini":
        import google.generativeai as genai
        genai.configure(api_key=api_key)
        model_obj = genai.GenerativeModel(
            model_name=model,
            system_instruction=system_prompt,
        )
        response = model_obj.generate_content(
            user_prompt,
            generation_config=genai.types.GenerationConfig(
                max_output_tokens=max_tokens,
                temperature=0.8,
            ),
        )
        content = response.text
        if not content or not content.strip():
            raise ValueError(f"Empty response from {model}")
        return content

    elif provider == "ollama":
        import json as _json
        import threading
        from openai import OpenAI

        # Parse URL(s) from the api_key slot (resolve_llm_config packs them here)
        raw = api_key or "http://localhost:11434"
        if raw.startswith("["):
            try:
                urls = _json.loads(raw)
            except (_json.JSONDecodeError, TypeError):
                urls = [raw]
        else:
            urls = [raw]

        # Per-server semaphores: dispatch to whichever server is free first.
        # This replaces the old blind round-robin which could queue work behind
        # a slow server while a fast server sits idle.
        if not hasattr(_call_llm, "_ollama_sems"):
            _call_llm._ollama_sems = {}  # type: ignore[attr-defined]
        sems: dict[str, threading.Semaphore] = _call_llm._ollama_sems  # type: ignore[attr-defined]
        for u in urls:
            if u not in sems:
                sems[u] = threading.Semaphore(1)

        # Try non-blocking acquire on each server (round-robin start to be fair)
        if not hasattr(_call_llm, "_ollama_start"):
            _call_llm._ollama_start = 0  # type: ignore[attr-defined]
        start: int = _call_llm._ollama_start % len(urls)  # type: ignore[attr-defined]
        _call_llm._ollama_start = start + 1  # type: ignore[attr-defined]

        chosen_url = None
        for i in range(len(urls)):
            candidate = urls[(start + i) % len(urls)]
            if sems[candidate].acquire(blocking=False):
                chosen_url = candidate
                break

        # If no server was free, block on the next in rotation
        if chosen_url is None:
            chosen_url = urls[start % len(urls)]
            sems[chosen_url].acquire()

        base_url = chosen_url.rstrip("/")
        idx = urls.index(chosen_url)
        logger.info(f"_call_llm Ollama → server {idx + 1}/{len(urls)}: {base_url}")

        try:
            client = OpenAI(
                api_key="ollama",
                base_url=f"{base_url}/v1",
                timeout=600.0,
            )

            effective_model = model or "qwen3:14b"
            # Ollama local models need shorter, more structured instructions
            ollama_system = (
                f"{system_prompt}\n\n"
                "CRITICAL RULES:\n"
                "1. Output ONLY the requested content — no prefixes, explanations, or commentary.\n"
                "2. If asked for JSON, return ONLY valid JSON — no markdown fences.\n"
                "3. If thinking step by step, do your reasoning silently — output ONLY the final result."
            )

            response = client.chat.completions.create(
                model=effective_model,
                messages=[
                    {"role": "system", "content": ollama_system},
                    {"role": "user", "content": user_prompt},
                ],
                max_tokens=max_tokens,
                temperature=0.6,
            )
            content = response.choices[0].message.content
            if not content or not content.strip():
                raise ValueError(f"Empty response from Ollama model {effective_model}")
            return content
        finally:
            # Always release the server semaphore so the next caller can use it
            sems[chosen_url].release()

    raise ValueError(f"Unknown provider: {provider}")
