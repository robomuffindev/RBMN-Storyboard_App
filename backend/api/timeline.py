"""Timeline and audio analysis endpoints for RBMN Storyboard App."""
import asyncio
import glob as glob_mod
import hashlib
import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from backend.config import settings
from backend.database import get_session
from backend.database.models import (
    Project,
    Scene,
    SongSection,
    SongSectionLabel,
    Asset,
    AssetType,
    StemSelection,
    AppSettings,
    Lyrics,
)

logger = logging.getLogger(__name__)

# ── LLM Debug Logger ──────────────────────────────────────────────────
# Writes full LLM request/response to logs/llm_debug/ with auto-rotation.
# Max 20 log files, oldest deleted when threshold exceeded.
LLM_LOG_DIR = Path(__file__).resolve().parent.parent.parent / "logs" / "llm_debug"
LLM_LOG_MAX_FILES = 20


def _write_llm_log(
    endpoint: str,
    provider: str,
    model: str,
    system_prompt: str,
    user_prompt: str,
    raw_response: str,
    parsed_result: Any = None,
    error: str | None = None,
    extra: dict | None = None,
) -> None:
    """Write a timestamped LLM debug log file with full request/response."""
    try:
        LLM_LOG_DIR.mkdir(parents=True, exist_ok=True)

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{ts}_{endpoint}.json"
        filepath = LLM_LOG_DIR / filename

        log_entry = {
            "timestamp": datetime.now().isoformat(),
            "endpoint": endpoint,
            "provider": provider,
            "model": model,
            "system_prompt": system_prompt,
            "user_prompt": user_prompt,
            "raw_response": raw_response,
            "parsed_result": parsed_result,
            "error": error,
        }
        if extra:
            log_entry["extra"] = extra

        filepath.write_text(json.dumps(log_entry, indent=2, default=str), encoding="utf-8")
        logger.info(f"[LLM-Log] Wrote debug log: {filepath}")

        # Rotate: delete oldest files if over threshold
        existing = sorted(glob_mod.glob(str(LLM_LOG_DIR / "*.json")))
        if len(existing) > LLM_LOG_MAX_FILES:
            for old_file in existing[: len(existing) - LLM_LOG_MAX_FILES]:
                try:
                    os.remove(old_file)
                    logger.info(f"[LLM-Log] Rotated old log: {old_file}")
                except OSError:
                    pass
    except Exception as e:
        logger.warning(f"[LLM-Log] Failed to write debug log: {e}")
router = APIRouter(prefix="/api/projects/{project_id}/timeline", tags=["timeline"])


def _snap_to_frame(t: float, fps: int) -> float:
    """Round a time value to the nearest frame boundary.

    At 24fps, frame boundaries are at 0, 1/24, 2/24, ...
    This ensures (end - start) * fps is always an exact integer,
    eliminating rounding drift in the video pipeline.
    """
    if t <= 0:
        return 0.0
    return round(t * fps) / fps


# Pydantic models for request/response
class SectionResponse(BaseModel):
    """Response model for song section."""

    id: UUID
    label: SongSectionLabel
    start_time: float
    end_time: float
    color: str

    class Config:
        from_attributes = True


class SectionUpdate(BaseModel):
    """Request model for updating section boundaries."""

    start_time: Optional[float] = None
    end_time: Optional[float] = None
    label: Optional[SongSectionLabel] = None
    color: Optional[str] = None


class WordTimestamp(BaseModel):
    """Word with timestamp in lyrics."""

    word: str
    start_time: float
    end_time: float


class LyricsResponse(BaseModel):
    """Response model for transcribed lyrics."""

    text: str
    words: list[WordTimestamp]
    initial_text: str = ""  # User-provided lyrics/script input


class WaveformPeaksResponse(BaseModel):
    """Response model for waveform peaks."""

    peaks: list[float]
    duration: float
    channels: int


class AnalyzeAudioResponse(BaseModel):
    """Response model for audio analysis."""

    sections: list[SectionResponse]
    lyrics: LyricsResponse
    bpm: float
    stems_created: list[str]


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


async def _slice_audio_for_scenes(
    project_id: UUID,
    session: AsyncSession,
) -> int:
    """Slice the project's master audio into per-scene audio clips.

    Finds the project's MUSIC asset, then for each scene in the project,
    slices the audio from scene.start_time to scene.end_time and saves it
    as a WAV file. Stores the relative path in scene.parameters.audio_clip_path.

    Returns the number of scenes processed.
    """
    from backend.services.video.ffmpeg import slice_audio

    # Find the project's music asset
    stmt = (
        select(Asset)
        .where(Asset.project_id == project_id)
        .where(Asset.asset_type == AssetType.MUSIC)
        .where(~Asset.rel_path.contains("stems/"))  # Exclude stem files
    )
    result = await session.execute(stmt)
    music_asset = result.scalars().first()

    if not music_asset:
        logger.warning(f"No music asset found for project {project_id}, skipping audio slicing")
        return 0

    # rel_path is project-relative (e.g. "assets/audio/song.mp3"), need project_id prefix
    audio_path = settings.project_dir / str(project_id) / music_asset.rel_path
    if not audio_path.exists():
        logger.warning(f"Music file not found at {audio_path}")
        return 0

    # Get all scenes
    scenes_stmt = (
        select(Scene)
        .where(Scene.project_id == project_id)
        .order_by(Scene.order_index)
    )
    scenes_result = await session.execute(scenes_stmt)
    scenes = list(scenes_result.scalars().all())

    if not scenes:
        return 0

    # Create output directory for scene audio clips
    clips_dir = settings.project_dir / str(project_id) / "audio_clips"
    clips_dir.mkdir(parents=True, exist_ok=True)

    count = 0
    for scene in scenes:
        try:
            clip_filename = f"scene_{scene.order_index:03d}_{scene.start_time:.2f}_{scene.end_time:.2f}.wav"
            clip_path = clips_dir / clip_filename
            rel_clip_path = str(clip_path.relative_to(settings.project_dir))

            await asyncio.to_thread(
                slice_audio,
                str(audio_path),
                str(clip_path),
                scene.start_time,
                scene.end_time,
            )

            # Store in scene parameters
            scene_params = dict(scene.parameters or {})
            scene_params["audio_clip_path"] = rel_clip_path
            scene.parameters = scene_params
            count += 1
        except Exception as e:
            logger.warning(f"Failed to slice audio for scene {scene.order_index}: {e}")

    return count


@router.post(
    "/analyze",
    response_model=AnalyzeAudioResponse,
    summary="Analyze audio",
)
async def analyze_audio(
    project_id: UUID,
    file: Optional[UploadFile] = File(None),
    asset_id: Optional[str] = Form(None),
    initial_text: Optional[str] = Form(None),
    session: AsyncSession = Depends(get_session),
) -> AnalyzeAudioResponse:
    """Analyze audio file for stems, transcription, and sections.

    Accepts either a new file upload OR an existing asset_id to analyze
    an already-uploaded audio file.

    Runs:
    1. Demucs for audio stem separation (vocals, drums, bass, other)
    2. WhisperX for transcription with word-level timestamps
    3. allin1 for music section detection (verse, chorus, bridge, etc.)

    Saves stems as project assets and updates database with sections and lyrics.

    Args:
        project_id: UUID of the project.
        file: Audio file to analyze (optional if asset_id provided).
        asset_id: UUID of an existing audio asset to analyze (optional if file provided).
        initial_text: Optional lyrics/script text to improve Whisper accuracy.
        session: Database session.

    Returns:
        Analysis results with sections, lyrics, BPM, and created stems.

    Raises:
        HTTPException: If project not found or analysis fails.
    """
    try:
        await _get_project_or_404(project_id, session)

        project_path = settings.project_dir / str(project_id)
        audio_dir = project_path / "assets" / "audio"
        audio_dir.mkdir(parents=True, exist_ok=True)

        if asset_id:
            # Analyze an already-uploaded asset
            from uuid import UUID as _UUID
            existing_asset = await session.get(Asset, _UUID(asset_id))
            if not existing_asset or existing_asset.project_id != project_id:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Asset {asset_id} not found in project",
                )
            audio_path = project_path / existing_asset.rel_path
            if not audio_path.exists():
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Audio file not found on disk",
                )
            audio_asset = existing_asset
        elif file and file.filename:
            # Upload new file and create asset
            content = await file.read()
            sha256 = hashlib.sha256(content).hexdigest()

            audio_path = audio_dir / file.filename
            with open(audio_path, "wb") as f:
                f.write(content)

            rel_path = f"assets/audio/{file.filename}"
            audio_asset = Asset(
                project_id=project_id,
                filename=file.filename,
                rel_path=rel_path,
                asset_type=AssetType.MUSIC,
                sha256=sha256,
                file_size=len(content),
            )
            session.add(audio_asset)
            await session.flush()
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Either file or asset_id must be provided",
            )

        logger.info(f"Analyzing audio file {audio_asset.filename} for project {project_id} (asset {audio_asset.id})")

        # Get settings for Whisper mode
        settings_stmt = select(AppSettings).where(AppSettings.id == 1)
        settings_result = await session.execute(settings_stmt)
        app_settings = settings_result.scalars().first()

        whisper_mode = app_settings.whisper_mode if app_settings else "local"
        whisper_remote_url = app_settings.whisper_remote_url if app_settings else None
        whisper_comfyui_url = app_settings.whisper_comfyui_url if app_settings else None
        whisper_model = app_settings.whisper_model if app_settings else "large-v2"
        whisper_language = app_settings.whisper_language if app_settings else "English"

        # Run audio analysis pipeline
        from backend.services.audio.analysis import AudioAnalyzer

        analyzer = AudioAnalyzer(cache_dir=str(project_path / "cache" / "audio_analysis"))
        analysis_result = await asyncio.to_thread(
            analyzer.analyze_full,
            str(audio_path),
            whisper_mode,
            whisper_remote_url,
            whisper_comfyui_url=whisper_comfyui_url,
            initial_text=initial_text,
            whisper_model=whisper_model,
            whisper_language=whisper_language,
        )

        # Save analysis results to cache
        cache_dir = project_path / "cache"
        cache_dir.mkdir(parents=True, exist_ok=True)

        # Create Asset records for generated stems
        stems_dir = project_path / "assets" / "stems"
        stem_names = ["vocals", "drums", "bass", "other"]
        for stem_name in stem_names:
            stem_path = stems_dir / f"{stem_name}.wav"
            if stem_path.exists():
                stem_size = os.path.getsize(stem_path)
                with open(stem_path, "rb") as sf:
                    stem_hash = hashlib.sha256(sf.read()).hexdigest()
                stem_asset = Asset(
                    project_id=project_id,
                    filename=f"{stem_name}.wav",
                    rel_path=f"assets/stems/{stem_name}.wav",
                    asset_type=AssetType.MUSIC,
                    sha256=stem_hash,
                    file_size=stem_size,
                    meta={"stem": stem_name, "source_audio": audio_asset.filename},
                )
                session.add(stem_asset)

        # Delete any existing sections for this project before saving new ones
        existing_sections_stmt = select(SongSection).where(SongSection.project_id == project_id)
        existing_sections_result = await session.execute(existing_sections_stmt)
        for old_section in existing_sections_result.scalars().all():
            await session.delete(old_section)

        # Color palette for sections
        section_colors = {
            "intro": "#6366f1",    # indigo
            "verse": "#3b82f6",    # blue
            "chorus": "#f59e0b",   # amber
            "bridge": "#8b5cf6",   # violet
            "outro": "#64748b",    # slate
            "pre-chorus": "#06b6d4",  # cyan
            "post-chorus": "#f97316",  # orange
            "hook": "#ef4444",     # red
            "interlude": "#14b8a6",  # teal
            "break": "#a3a3a3",    # neutral
        }

        # Save sections to database
        # detect_sections returns "start"/"end", map to start_time/end_time
        saved_sections = []
        for section_data in analysis_result.get("sections", []):
            label_str = section_data.get("label", "other")
            # Map label string to enum — use OTHER as fallback
            try:
                label_enum = SongSectionLabel(label_str.lower())
            except ValueError:
                label_enum = SongSectionLabel.OTHER

            start = section_data.get("start", section_data.get("start_time", 0.0))
            end = section_data.get("end", section_data.get("end_time", 0.0))
            color = section_data.get("color", section_colors.get(label_str.lower(), "#FFFFFF"))

            song_section = SongSection(
                project_id=project_id,
                label=label_enum,
                start_time=start,
                end_time=end,
                color=color,
            )
            session.add(song_section)
            await session.flush()  # Get the ID
            saved_sections.append(song_section)
            logger.info(f"Saved section: {label_enum.value} {start:.1f}s-{end:.1f}s")

        # Commit sections first so they're saved even if lyrics fail
        await session.commit()
        logger.info(f"Committed {len(saved_sections)} sections to database")

        # Save lyrics to database (separate transaction so section data is safe)
        transcription_words = analysis_result.get("transcription", [])
        lyrics_text = ""
        if transcription_words:
            lyrics_text = " ".join(w.get("word", "") for w in transcription_words)

        # Fallback: if Whisper produced no transcription but the user provided
        # lyrics in the input box, use those as the detected lyrics text.
        # This ensures the Concept panel shows lyrics and "Base on Lyrics" works
        # even when Whisper fails (e.g. byte-token hallucination on certain audio).
        if not lyrics_text and initial_text:
            lyrics_text = initial_text.strip()
            logger.info(
                f"Whisper produced no transcription — using user-provided lyrics "
                f"as detected text ({len(lyrics_text)} chars)"
            )

        try:
            # Upsert lyrics — delete existing first
            existing_lyrics_stmt = select(Lyrics).where(Lyrics.project_id == project_id)
            existing_lyrics_result = await session.execute(existing_lyrics_stmt)
            existing_lyrics = existing_lyrics_result.scalars().first()
            if existing_lyrics:
                await session.delete(existing_lyrics)
                await session.flush()

            lyrics_record = Lyrics(
                project_id=project_id,
                full_text=lyrics_text,
                initial_text=initial_text or "",
                words=transcription_words,
            )
            session.add(lyrics_record)
            await session.commit()
            logger.info("Lyrics saved to database")
        except Exception as lyrics_err:
            logger.warning(f"Failed to save lyrics to database: {lyrics_err}")
            await session.rollback()
            # Try without initial_text in case the column doesn't exist yet
            try:
                existing_lyrics_stmt = select(Lyrics).where(Lyrics.project_id == project_id)
                existing_lyrics_result = await session.execute(existing_lyrics_stmt)
                existing_lyrics = existing_lyrics_result.scalars().first()
                if existing_lyrics:
                    await session.delete(existing_lyrics)
                    await session.flush()
                lyrics_record = Lyrics(
                    project_id=project_id,
                    full_text=lyrics_text,
                    words=transcription_words,
                )
                session.add(lyrics_record)
                await session.commit()
                logger.info("Lyrics saved (without initial_text)")
            except Exception as e2:
                logger.warning(f"Lyrics save retry also failed: {e2}")
                await session.rollback()

        # Also cache to file for backward compatibility
        lyrics_data = {"text": lyrics_text, "words": transcription_words}
        lyrics_cache_path = cache_dir / "lyrics.json"
        with open(lyrics_cache_path, "w") as f:
            json.dump(lyrics_data, f)

        # Create StemSelection for first scene if it doesn't already exist
        try:
            stmt = select(Scene).where(Scene.project_id == project_id).order_by(Scene.order_index)
            result = await session.execute(stmt)
            first_scene = result.scalars().first()

            if first_scene:
                # Check if a StemSelection already exists for this scene
                existing_stmt = select(StemSelection).where(StemSelection.scene_id == first_scene.id)
                existing_result = await session.execute(existing_stmt)
                existing_sel = existing_result.scalars().first()

                if not existing_sel:
                    stem_selection = StemSelection(
                        scene_id=first_scene.id,
                        vocals=True,
                        drums=True,
                        bass=True,
                        other=True,
                    )
                    session.add(stem_selection)
                    await session.commit()
                    logger.info(f"Created stem selection for scene {first_scene.id}")
                else:
                    logger.info(f"Stem selection already exists for scene {first_scene.id}, skipping")
        except Exception as stem_err:
            logger.warning(f"Failed to create stem selection: {stem_err}")
            try:
                await session.rollback()
            except Exception:
                logger.warning("Session rollback also failed — session may be in broken state")

        # Build response with actual saved sections
        section_responses = [
            SectionResponse(
                id=s.id,
                label=s.label,
                start_time=s.start_time,
                end_time=s.end_time,
                color=s.color,
            )
            for s in saved_sections
        ]

        # Build words for response
        word_responses = [
            WordTimestamp(
                word=w.get("word", ""),
                start_time=w.get("start", 0.0),
                end_time=w.get("end", 0.0),
            )
            for w in transcription_words
        ]

        return AnalyzeAudioResponse(
            sections=section_responses,
            lyrics=LyricsResponse(text=lyrics_data["text"], words=word_responses),
            bpm=analysis_result.get("bpm", 120.0),
            stems_created=["vocals", "drums", "bass", "other"],
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error analyzing audio for project {project_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to analyze audio",
        )


@router.get(
    "/sections",
    response_model=list[SectionResponse],
    summary="Get song sections",
)
async def get_sections(
    project_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> list[SectionResponse]:
    """Get song sections detected for the project.

    Returns:
        List of song sections ordered by start_time.

    Raises:
        HTTPException: If project not found.
    """
    try:
        await _get_project_or_404(project_id, session)

        stmt = (
            select(SongSection)
            .where(SongSection.project_id == project_id)
            .order_by(SongSection.start_time)
        )
        result = await session.execute(stmt)
        sections = result.scalars().all()

        return [SectionResponse.model_validate(s) for s in sections]
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting sections for project {project_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get sections",
        )


@router.put(
    "/sections/{section_id}",
    response_model=SectionResponse,
    summary="Update section",
)
async def update_section(
    project_id: UUID,
    section_id: UUID,
    req: SectionUpdate,
    session: AsyncSession = Depends(get_session),
) -> SectionResponse:
    """Update song section boundaries and metadata.

    Args:
        project_id: UUID of the project.
        section_id: UUID of the section.
        req: Update request.
        session: Database session.

    Returns:
        Updated section.

    Raises:
        HTTPException: If project or section not found.
    """
    try:
        await _get_project_or_404(project_id, session)

        section = await session.get(SongSection, section_id)
        if not section or section.project_id != project_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Section {section_id} not found",
            )

        if req.start_time is not None:
            section.start_time = req.start_time
        if req.end_time is not None:
            section.end_time = req.end_time
        if req.label is not None:
            section.label = req.label
        if req.color is not None:
            section.color = req.color

        await session.commit()
        await session.refresh(section)

        logger.info(f"Updated section {section_id}")

        return SectionResponse.model_validate(section)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating section {section_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update section",
        )


@router.post(
    "/rerun-whisper",
    response_model=LyricsResponse,
    summary="Re-run Whisper transcription only",
)
async def rerun_whisper(
    project_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> LyricsResponse:
    """Re-run only the Whisper transcription step without full audio reprocessing.

    Uses the existing vocal stem (if available) or the original audio file.
    Skips stem separation and section detection — only transcribes and
    saves the new word-level timestamps to the Lyrics table.
    """
    try:
        project = await _get_project_or_404(project_id, session)
        project_path = settings.project_dir / str(project_id)

        # Find the audio asset
        audio_stmt = (
            select(Asset)
            .where(Asset.project_id == project_id, Asset.asset_type == AssetType.MUSIC)
            .where(~Asset.rel_path.contains("stems/"))
            .order_by(Asset.created_at.desc())
        )
        audio_result = await session.execute(audio_stmt)
        audio_asset = audio_result.scalars().first()
        if not audio_asset:
            raise HTTPException(status_code=400, detail="No audio file found in project")

        audio_path = project_path / audio_asset.rel_path

        # Use the FULL audio file for re-run Whisper (not the vocal stem).
        # Demucs vocal separation can introduce artifacts that degrade Whisper
        # accuracy. The full mix often produces better transcription because
        # Whisper was trained on full audio, not isolated vocals.
        cache_dir = project_path / "cache" / "audio_analysis"
        whisper_input = str(audio_path)

        # Clear any cached transcription so we always re-run fresh
        import glob
        for cached in glob.glob(str(cache_dir / "*_transcription.json")):
            Path(cached).unlink(missing_ok=True)
            logger.info(f"Cleared cached transcription: {cached}")

        logger.info(f"Re-running Whisper on full audio: {whisper_input}")

        # Get Whisper settings
        settings_stmt = select(AppSettings).where(AppSettings.id == 1)
        settings_result = await session.execute(settings_stmt)
        app_settings = settings_result.scalars().first()

        whisper_mode = app_settings.whisper_mode if app_settings else "local"
        whisper_remote_url = app_settings.whisper_remote_url if app_settings else None
        whisper_comfyui_url = app_settings.whisper_comfyui_url if app_settings else None
        whisper_model = app_settings.whisper_model if app_settings else "large-v2"
        whisper_language = app_settings.whisper_language if app_settings else "English"

        # Get user-provided lyrics for initial prompt
        lyrics_stmt = select(Lyrics).where(Lyrics.project_id == project_id)
        lyrics_result_db = await session.execute(lyrics_stmt)
        existing_lyrics = lyrics_result_db.scalars().first()
        initial_text = existing_lyrics.initial_text if existing_lyrics else None

        # Run transcription only
        from backend.services.audio.analysis import AudioAnalyzer
        analyzer = AudioAnalyzer(cache_dir=str(cache_dir))

        transcription_words = []
        if whisper_mode == "comfyui" and whisper_comfyui_url:
            transcription_words = await asyncio.to_thread(
                analyzer.transcribe_comfyui,
                whisper_input, whisper_comfyui_url,
                initial_text=initial_text, whisper_model=whisper_model,
                whisper_language=whisper_language,
            )
        elif whisper_mode == "remote" and whisper_remote_url:
            transcription_words = await asyncio.to_thread(
                analyzer.transcribe_remote,
                whisper_input, whisper_remote_url,
                initial_text=initial_text, whisper_model=whisper_model,
            )
        else:
            transcription_words = await asyncio.to_thread(
                analyzer.transcribe_local,
                whisper_input, initial_text=initial_text,
                whisper_model=whisper_model,
            )

        lyrics_text = " ".join(w.get("word", "") for w in transcription_words) if transcription_words else ""
        if not lyrics_text and initial_text:
            lyrics_text = initial_text.strip()
            logger.info("Whisper produced no transcription — keeping existing lyrics text")

        # Save to DB (upsert)
        if existing_lyrics:
            await session.delete(existing_lyrics)
            await session.flush()

        new_lyrics = Lyrics(
            project_id=project_id,
            full_text=lyrics_text,
            initial_text=initial_text or "",
            words=transcription_words,
        )
        session.add(new_lyrics)
        await session.commit()

        logger.info(f"Whisper re-run complete: {len(transcription_words)} words with timestamps")

        words_out = [
            WordTimestamp(word=w.get("word", ""), start_time=w.get("start", 0.0), end_time=w.get("end", 0.0))
            for w in transcription_words
        ]
        return LyricsResponse(text=lyrics_text, words=words_out, initial_text=initial_text or "")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error re-running Whisper: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to re-run Whisper: {str(e)}")


@router.get(
    "/lyrics",
    response_model=LyricsResponse,
    summary="Get lyrics",
)
async def get_lyrics(
    project_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> LyricsResponse:
    """Get transcribed lyrics with word-level timestamps.

    Returns:
        Lyrics object with full text and word-by-word breakdown with timings.

    Raises:
        HTTPException: If project not found.
    """
    try:
        await _get_project_or_404(project_id, session)

        # Try database first
        lyrics_stmt = select(Lyrics).where(Lyrics.project_id == project_id)
        lyrics_result = await session.execute(lyrics_stmt)
        lyrics_record = lyrics_result.scalars().first()

        if lyrics_record:
            words = [
                WordTimestamp(
                    word=w.get("word", ""),
                    start_time=w.get("start", 0.0),
                    end_time=w.get("end", 0.0),
                )
                for w in (lyrics_record.words or [])
            ]
            initial = getattr(lyrics_record, "initial_text", "") or ""
            return LyricsResponse(text=lyrics_record.full_text, words=words, initial_text=initial)

        # Fall back to cached lyrics JSON file
        project_path = settings.project_dir / str(project_id)
        lyrics_cache_path = project_path / "cache" / "lyrics.json"

        if lyrics_cache_path.exists():
            with open(lyrics_cache_path, "r") as f:
                lyrics_data = json.load(f)
                words = [
                    WordTimestamp(
                        word=w.get("word", ""),
                        start_time=w.get("start", 0.0),
                        end_time=w.get("end", 0.0),
                    )
                    for w in lyrics_data.get("words", [])
                ]
                return LyricsResponse(text=lyrics_data.get("text", ""), words=words)

        # Return empty response if no lyrics found
        return LyricsResponse(text="", words=[])
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting lyrics for project {project_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get lyrics",
        )


def _reconstruct_phrases_from_whisper(words: list[dict]) -> list[str]:
    """Reconstruct phrase lines from Whisper word timestamps.

    When the user hasn't pasted lyrics (initial_text is empty), we can
    still detect phrase boundaries using signals in the Whisper output:
    1. Capital letters: Whisper often capitalizes the first word of a new phrase
    2. Pauses: gaps > 0.4s between words suggest phrase boundaries
    3. Punctuation: sentence-ending punctuation (. ! ?) marks phrase ends

    Returns a list of phrase strings suitable for use as lyrics_text lines.
    """
    if not words:
        return []

    def get_raw(w: dict) -> str:
        return (w.get("word", "") or w.get("value", "") or w.get("text", "")).strip()

    phrases: list[str] = []
    current_words: list[str] = []

    for i, w in enumerate(words):
        raw = get_raw(w)
        if not raw:
            continue

        # Check if this word starts a new phrase
        is_new_phrase = False

        if i > 0 and current_words:
            prev_raw = get_raw(words[i - 1])
            prev_end = words[i - 1].get("end", 0)
            this_start = w.get("start", 0)
            gap = this_start - prev_end

            # Capital letter after a pause = strong phrase boundary
            if raw[0].isupper() and gap > 0.2:
                is_new_phrase = True

            # Long pause alone = phrase boundary
            elif gap > 0.8:
                is_new_phrase = True

            # Sentence-ending punctuation on previous word
            import re
            if prev_raw and re.search(r'[.!?…]$', prev_raw):
                is_new_phrase = True

        if is_new_phrase and current_words:
            phrases.append(" ".join(current_words))
            current_words = []

        current_words.append(raw)

    if current_words:
        phrases.append(" ".join(current_words))

    logger.info(
        f"[ReconstructPhrases] Built {len(phrases)} phrases from "
        f"{len(words)} Whisper words using capital letters + pauses"
    )
    for i, p in enumerate(phrases[:5]):
        logger.info(f"[ReconstructPhrases]   Phrase {i}: '{p[:60]}'")

    return phrases


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
    and pauses.

    Returns a list of word-groups, each group being a phrase that must be
    kept together — scene boundaries may ONLY be placed between these
    groups, never inside them.
    """
    import re
    if not words:
        return []

    # ── PRIMARY: Use lyrics lines if available ──────────────────────────
    if lyrics_text and lyrics_text.strip():
        raw_lines = [l.strip() for l in lyrics_text.strip().splitlines() if l.strip()]
        # Filter out section headers like [Intro], [Verse 1], [Chorus], etc.
        # and purely parenthetical lines like (Mm-mm-mm...), (That's right...)
        # These are structural annotations, not sung lyrics — Whisper won't
        # detect them, so trying to match them consumes words incorrectly
        # and desynchronizes all subsequent phrase groups.
        lines = []
        for line in raw_lines:
            # Skip section headers: [Intro], [Verse 1], [Pre-Chorus], etc.
            if re.match(r'^\[.*\]$', line):
                logger.info(f"[LyricsFilter] Skipping section header: '{line}'")
                continue
            # Skip purely parenthetical lines: (Mm-mm-mm...), (That's right...)
            if re.match(r'^\(.*\)$', line):
                logger.info(f"[LyricsFilter] Skipping parenthetical line: '{line}'")
                continue
            lines.append(line)
        logger.info(
            f"[LyricsFilter] {len(raw_lines)} raw lines → {len(lines)} after filtering "
            f"({len(raw_lines) - len(lines)} removed)"
        )
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

        # Check for clause-ending punctuation (comma, semicolon, colon, dash)
        has_clause_end = bool(re.search(r'[,;:\-–—]$', word_text))

        # Check for pause after this word
        next_start = words[i + 1].get("start", 0)
        this_end = w.get("end", 0)
        gap = next_start - this_end

        # Break on sentence-ending punctuation, long pauses, or clause + medium pause
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

    TIME-AWARE MULTI-WORD MONOTONIC ANCHOR STRATEGY:
    - Uses Whisper word TIMESTAMPS (not word-count ratios) to estimate
      where each lyrics line should appear in the audio stream
    - Anchors use first 2-3 words of each lyrics line for matching
    - Search is STRICTLY MONOTONIC: each anchor must be after the previous
    - Time-based reasonableness check rejects matches >20s from expected
    - Missed anchors interpolated using TIME positions (not word counts)
    """
    import re

    def clean_word(w: str) -> str:
        return re.sub(r'[^a-zA-Z0-9]', '', w).lower()

    def words_similar(a: str, b: str) -> bool:
        if a == b:
            return True
        if len(a) >= 3 and len(b) >= 3:
            if a.startswith(b) or b.startswith(a):
                return True
        if len(a) >= 4 and len(b) >= 4 and abs(len(a) - len(b)) <= 1:
            diffs = sum(1 for ca, cb in zip(a, b) if ca != cb)
            diffs += abs(len(a) - len(b))
            if diffs <= 1:
                return True
        return False

    def get_raw_word(w: dict) -> str:
        return (w.get("word", "") or w.get("value", "") or w.get("text", "")).strip()

    # Build cleaned Whisper word arrays and time index
    whisper_cleaned: list[str] = []
    whisper_raw: list[str] = []
    whisper_times: list[float] = []  # start time of each Whisper word
    for w in words:
        raw = get_raw_word(w)
        whisper_raw.append(raw)
        whisper_cleaned.append(clean_word(raw))
        # Handle both field name conventions: start/end and start_time/end_time
        whisper_times.append(w.get("start", 0) or w.get("start_time", 0))

    # Total audio span covered by Whisper words
    audio_start = whisper_times[0] if whisper_times else 0
    last_w = words[-1] if words else {}
    audio_end = (last_w.get("end", 0) or last_w.get("end_time", 0)) if words else 0

    logger.info(
        f"[WordMatch] Matching {len(words)} Whisper words to {len(lines)} lyrics lines "
        f"(audio {audio_start:.1f}s–{audio_end:.1f}s)"
    )

    # Pre-process lyrics lines
    line_infos: list[dict] = []
    for line in lines:
        stripped = re.sub(r'\s*\([^)]*\)\s*', ' ', line).strip()
        stripped = stripped.replace('…', '').strip()
        raw_tokens = stripped.split() if stripped else line.split()
        clean_tokens = [clean_word(w) for w in raw_tokens]
        clean_tokens = [w for w in clean_tokens if w]
        line_infos.append({
            "original": line,
            "clean_tokens": clean_tokens,
            "first_word": clean_tokens[0] if clean_tokens else "",
            "word_count": len(clean_tokens),
        })

    total_lyrics_words = sum(info["word_count"] for info in line_infos)
    logger.info(
        f"[WordMatch] Total lyrics words: {total_lyrics_words}, Whisper words: {len(words)}"
    )

    # ── Compute expected WORD INDEX for each lyrics line ─────────────
    # Use word-count ratio through the Whisper stream (NOT time ratio).
    # This naturally handles instrumental breaks because Whisper has no
    # words during them — the ratio skips over gaps automatically.
    cum_words = [0]
    for info in line_infos:
        cum_words.append(cum_words[-1] + info["word_count"])

    n_whisper = len(words)
    expected_indices: list[int] = []
    for li in range(len(line_infos)):
        frac = cum_words[li] / max(total_lyrics_words, 1)
        expected_indices.append(int(frac * n_whisper))

    logger.info(
        f"[WordMatch] Expected indices (first 10): "
        f"{expected_indices[:10]} ... (last: {expected_indices[-1] if expected_indices else 'N/A'})"
    )

    # ══════════════════════════════════════════════════════════════════
    # BOUNDED WINDOW MONOTONIC ANCHORING
    #
    # Key design:
    # - ALL match types search a BOUNDED window around expected position
    #   (NOT the entire forward stream — that causes chorus-skipping)
    # - Within the window, collect ALL candidates and pick CLOSEST to
    #   expected position (avoids wrong-occurrence in repeated sections)
    # - After each confirmed anchor, compute drift and adjust remaining
    #   expected positions to stay accurate
    # - Prefer stronger match types: exact3 > exact2 > fuzzy2 > exact1 > fuzzy1
    # ══════════════════════════════════════════════════════════════════

    drift = 0  # cumulative offset: positive = lyrics are later than expected

    # Window size: search ±30% of total words, minimum 60
    # This is generous enough for tempo variations but prevents
    # jumping from verse 1 to the final chorus
    _SEARCH_WINDOW = max(60, n_whisper * 3 // 10)

    def _find_anchor_bounded(
        tokens: list[str], search_start: int, expected_idx: int,
    ) -> tuple[int, str]:
        """Find anchor in a bounded window around expected_idx.
        Collects ALL candidates per match type, picks closest to expected."""
        if not tokens:
            return -1, "none"

        # Bounded search range: [search_start, expected + window]
        # We always start from search_start (monotonic) but cap the upper end
        sw_end = min(len(whisper_cleaned), expected_idx + _SEARCH_WINDOW)
        # Ensure at least some range past search_start
        sw_end = max(sw_end, min(len(whisper_cleaned), search_start + _SEARCH_WINDOW))

        # --- Try each match type in priority order ---
        # For each type, collect ALL matches in window, pick closest to expected

        # 3-word exact sequence
        if len(tokens) >= 3:
            candidates = []
            for s in range(search_start, sw_end):
                if s + 2 < len(whisper_cleaned):
                    if (whisper_cleaned[s] == tokens[0]
                            and words_similar(whisper_cleaned[s + 1], tokens[1])
                            and words_similar(whisper_cleaned[s + 2], tokens[2])):
                        candidates.append(s)
            if candidates:
                return min(candidates, key=lambda p: abs(p - expected_idx)), "exact3"

        # 2-word exact sequence (first word exact, second similar)
        if len(tokens) >= 2:
            candidates = []
            for s in range(search_start, sw_end):
                if s + 1 < len(whisper_cleaned):
                    if (whisper_cleaned[s] == tokens[0]
                            and words_similar(whisper_cleaned[s + 1], tokens[1])):
                        candidates.append(s)
            if candidates:
                return min(candidates, key=lambda p: abs(p - expected_idx)), "exact2"

        # 2-word fuzzy sequence (both words fuzzy)
        if len(tokens) >= 2:
            candidates = []
            for s in range(search_start, sw_end):
                if s + 1 < len(whisper_cleaned):
                    if (words_similar(whisper_cleaned[s], tokens[0])
                            and words_similar(whisper_cleaned[s + 1], tokens[1])):
                        candidates.append(s)
            if candidates:
                return min(candidates, key=lambda p: abs(p - expected_idx)), "fuzzy2"

        # 1-word exact
        candidates = [s for s in range(search_start, sw_end)
                      if whisper_cleaned[s] == tokens[0]]
        if candidates:
            return min(candidates, key=lambda p: abs(p - expected_idx)), "exact1"

        # 1-word fuzzy
        candidates = [s for s in range(search_start, sw_end)
                      if words_similar(whisper_cleaned[s], tokens[0])]
        if candidates:
            return min(candidates, key=lambda p: abs(p - expected_idx)), "fuzzy1"

        return -1, "none"

    anchors: list[int] = []  # anchors[i] = Whisper word index, or -1 if missed
    min_next_pos = 0  # Strict monotonic
    consecutive_misses = 0

    # Time validation thresholds: reject matches where the audio time
    # jumps too far from expected. This prevents single common words
    # like "What" from matching a later chorus instead of the current verse.
    _TIME_THRESH_STRONG = 40.0  # seconds — for exact3/exact2 (strong signal)
    _TIME_THRESH_WEAK = 20.0    # seconds — for fuzzy2/exact1/fuzzy1 (weak signal)

    for li, info in enumerate(line_infos):
        tokens = info["clean_tokens"]
        if not tokens:
            anchors.append(-1)
            continue

        # Apply drift correction to expected index
        adj_expected = max(min_next_pos, expected_indices[li] + drift)
        adj_expected = min(adj_expected, n_whisper - 1)

        pos, match_type = _find_anchor_bounded(tokens, min_next_pos, adj_expected)

        if pos >= 0:
            # TIME VALIDATION: check if the match is at a reasonable audio time
            match_time = whisper_times[pos] if pos < len(whisper_times) else audio_end
            # Expected time = Whisper timestamp at the expected word index
            exp_idx_clamped = max(0, min(adj_expected, n_whisper - 1))
            expected_time = whisper_times[exp_idx_clamped] if exp_idx_clamped < len(whisper_times) else audio_end
            time_diff = abs(match_time - expected_time)

            thresh = _TIME_THRESH_STRONG if match_type in ("exact3", "exact2") else _TIME_THRESH_WEAK
            if time_diff > thresh:
                # Match is too far from expected time — likely wrong section
                logger.info(
                    f"[WordMatch] Line {li}: REJECTED {match_type} '{whisper_raw[pos]}' "
                    f"at {match_time:.1f}s (expected ~{expected_time:.1f}s, diff={time_diff:.1f}s > {thresh:.0f}s) "
                    f"| '{info['original'][:40]}'"
                )
                anchors.append(-1)
                consecutive_misses += 1
            else:
                anchors.append(pos)
                min_next_pos = pos + 1
                consecutive_misses = 0

                # Update drift: how far off was our prediction?
                raw_expected = expected_indices[li]
                new_drift = pos - raw_expected
                # Smooth drift: blend with existing (avoid wild swings)
                drift = int(0.6 * new_drift + 0.4 * drift)

                if match_type not in ("exact3", "exact2"):
                    logger.info(
                        f"[WordMatch] Line {li}: {match_type} anchor "
                        f"'{whisper_raw[pos]}' ≈ '{info['original'][:40]}' "
                        f"at pos {pos} ({match_time:.1f}s, expected ~{expected_time:.1f}s, drift={drift})"
                    )
        else:
            anchors.append(-1)
            consecutive_misses += 1
            logger.info(
                f"[WordMatch] Line {li}: MISS for '{info['original'][:40]}' "
                f"(adj_expected={adj_expected}, searched from {min_next_pos}, drift={drift})"
            )

    matched_count = sum(1 for a in anchors if a >= 0)
    logger.info(
        f"[WordMatch] Anchoring complete: {matched_count}/{len(anchors)} matched, "
        f"final drift={drift}"
    )

    # ── Interpolate missed anchors ────────────────────────────────────
    confirmed = [(i, anchors[i]) for i in range(len(anchors)) if anchors[i] >= 0]

    if not confirmed:
        # Nothing matched — distribute evenly by word count ratio
        logger.warning("[WordMatch] No anchors matched! Distributing evenly")
        for li in range(len(anchors)):
            anchors[li] = min(expected_indices[li], n_whisper - 1)
    else:
        # Add virtual anchors at start and end for edge interpolation
        if confirmed[0][0] > 0:
            confirmed.insert(0, (0, 0))
            anchors[0] = 0
        if confirmed[-1][0] < len(anchors) - 1:
            last_li = len(anchors) - 1
            last_confirmed = confirmed[-1]
            # Place last virtual anchor near end of Whisper stream
            last_pos = min(n_whisper - 1, last_confirmed[1] + line_infos[last_confirmed[0]]["word_count"])
            # But also ensure it reaches the actual end for the last lines
            last_pos = max(last_pos, n_whisper - 1)
            confirmed.append((last_li, last_pos))
            anchors[last_li] = last_pos

        # Interpolate between consecutive confirmed anchors using word-count ratio
        for ci in range(len(confirmed) - 1):
            left_li, left_pos = confirmed[ci]
            right_li, right_pos = confirmed[ci + 1]
            if right_li - left_li <= 1:
                continue

            pos_span = max(right_pos - left_pos, 1)
            # Count words in the gap lines
            gap_words_total = sum(line_infos[li]["word_count"] for li in range(left_li, right_li + 1))
            gap_words_total = max(gap_words_total, 1)

            gap_cum = 0
            for li in range(left_li + 1, right_li):
                if anchors[li] >= 0:
                    gap_cum += line_infos[li]["word_count"]
                    continue
                gap_cum += line_infos[li]["word_count"]
                frac = gap_cum / gap_words_total
                interp_pos = int(left_pos + frac * pos_span)
                anchors[li] = max(left_pos + 1, min(interp_pos, right_pos - 1))
                if anchors[li] < len(whisper_times):
                    interp_time = whisper_times[anchors[li]]
                    logger.info(
                        f"[WordMatch] Line {li}: interpolated to pos {anchors[li]} "
                        f"({interp_time:.1f}s)"
                    )

    # Ensure strict monotonic after interpolation
    for i in range(1, len(anchors)):
        if anchors[i] <= anchors[i - 1]:
            anchors[i] = anchors[i - 1] + 1
    # Clamp to valid range
    for i in range(len(anchors)):
        anchors[i] = max(0, min(anchors[i], len(words)))

    # ══════════════════════════════════════════════════════════════════
    # BUILD PHRASE GROUPS from anchor positions
    # ══════════════════════════════════════════════════════════════════
    groups: list[list[dict]] = []
    group_ranges: list[tuple[int, int]] = []
    word_to_group: dict[int, int] = {}

    for li in range(len(anchors)):
        g_start = anchors[li]
        g_end = anchors[li + 1] if li + 1 < len(anchors) else len(words)
        g_end = min(g_end, len(words))

        if g_start >= len(words) or g_start >= g_end:
            continue

        g_idx = len(groups)
        group_words = list(words[g_start:g_end])
        if group_words:
            group_words[0] = dict(group_words[0])
            group_words[0]["__source_lyrics_line__"] = line_infos[li]["original"]
        groups.append(group_words)
        group_ranges.append((g_start, g_end))
        for wi in range(g_start, g_end):
            word_to_group[wi] = g_idx

    # Handle leading words before first anchor
    if anchors and anchors[0] > 0 and groups:
        leading = list(words[0:anchors[0]])
        if leading:
            groups[0] = leading + groups[0]
            old_start, old_end = group_ranges[0]
            group_ranges[0] = (0, old_end)
            for wi in range(0, anchors[0]):
                word_to_group[wi] = 0

    # Merge orphaned words into nearest group
    orphaned = [i for i in range(len(words)) if i not in word_to_group]
    if orphaned and groups:
        for oi in orphaned:
            best_group = 0
            best_dist = float('inf')
            for gi, (gs, ge) in enumerate(group_ranges):
                dist = min(abs(oi - gs), abs(oi - ge))
                if dist < best_dist:
                    best_dist = dist
                    best_group = gi
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

    # Merge single-word groups into neighbors
    if len(groups) > 1:
        merged_groups: list[list[dict]] = []
        for gi, group in enumerate(groups):
            if len(group) == 1 and len(groups) > 1:
                if merged_groups:
                    merged_groups[-1].extend(group)
                elif gi + 1 < len(groups):
                    groups[gi + 1] = group + groups[gi + 1]
                else:
                    merged_groups.append(group)
            else:
                merged_groups.append(group)
        groups = merged_groups

    groups = [g for g in groups if g]

    logger.info(
        f"[WordMatch] RESULT: {len(words)} Whisper words → {len(lines)} lyrics lines → "
        f"{len(groups)} phrase groups"
    )
    for gi, group in enumerate(groups):
        g_text = " ".join(get_raw_word(w) for w in group)
        g_start = group[0].get("start", 0)
        g_end = group[-1].get("end", 0)
        logger.info(
            f"[WordMatch]   Group {gi}: [{g_start:.2f}s–{g_end:.2f}s] "
            f"({len(group)} words) '{g_text[:60]}'"
        )

    return groups


def _build_phrase_gaps(
    words: list[dict],
    lyrics_text: str = "",
) -> list[dict]:
    """Build a scored list of gaps ONLY at phrase/line boundaries.

    CRITICAL: This function only returns gaps that fall BETWEEN lyrics
    lines (phrases), never inside them. This guarantees that scene
    boundaries cannot split a lyrical phrase like "White sheet folded
    neat and small" across two scenes.

    When lyrics_text is provided (user's pasted lyrics with line breaks),
    each line defines a phrase. Without it, falls back to punctuation/pause
    detection.

    Each gap gets a score based on:
    - Gap duration (longer pause = better boundary point)
    - Sentence-ending punctuation before the gap (period, !, ?, …)
    - Clause-ending punctuation (comma, semicolon, colon, dash)
    - Whether the gap looks like a verse/section break (very long pause > 3s)

    Returns a list of dicts sorted by score (best first):
      { "time": float (midpoint), "after_word_idx": int, "gap_size": float, "score": float }
    """
    if len(words) < 2:
        return []

    import re
    import math

    # Group words into phrases — gaps are ONLY allowed between phrases
    sentences = _group_words_into_sentences(words, lyrics_text=lyrics_text)
    if len(sentences) < 2:
        return []

    gaps: list[dict] = []

    # ── Gap BEFORE the first phrase (intro → first lyrics) ────────────
    # This is critical: without it, a boundary between an instrumental
    # intro and the first vocal phrase has no phrase-gap candidate to
    # snap to, causing words like "Black" to leak into the intro scene.
    if sentences and sentences[0]:
        first_word = sentences[0][0]
        first_start = first_word.get("start", 0)
        if first_start > 0.1:  # There's silence before lyrics start
            # Find the index of the first word in the original list
            first_word_idx_in_words = None
            for i, w in enumerate(words):
                if w is first_word:
                    first_word_idx_in_words = i
                    break

            if first_word_idx_in_words is not None:
                # Gap from start of audio (or previous word) to first lyrics word
                gap_start = 0.0
                if first_word_idx_in_words > 0:
                    gap_start = words[first_word_idx_in_words - 1].get("end", 0)
                gap_size = max(first_start - gap_start, 0.01)

                score = max(0, math.log(max(gap_size, 0.01) / 0.1) * 2.0)
                # Bonus: this is a section transition (instrumental → vocal)
                if gap_size > 3.0:
                    score += 8.0
                elif gap_size > 1.5:
                    score += 4.0
                elif gap_size > 0.5:
                    score += 1.5
                score += 6.0  # Extra bonus for being a lyrics-start boundary

                gap_center = (gap_start + first_start) / 2.0
                # Use a synthetic after_word_idx: the word BEFORE the first lyrics word
                # (or -1 if first lyrics word is also the first Whisper word)
                after_idx = first_word_idx_in_words - 1 if first_word_idx_in_words > 0 else -1

                gaps.append({
                    "time": gap_center,
                    "after_word_idx": after_idx,
                    "gap_size": gap_size,
                    "score": score,
                    "word_before": "(intro)",
                })

    for s_idx in range(len(sentences) - 1):
        last_word = sentences[s_idx][-1]
        first_word_next = sentences[s_idx + 1][0]

        # Find the index of last_word in the original words list
        last_word_idx = None
        for i, w in enumerate(words):
            if w is last_word:
                last_word_idx = i
                break
        if last_word_idx is None:
            continue

        end_i = last_word.get("end", 0)
        start_next = first_word_next.get("start", 0)
        gap_size = max(start_next - end_i, 0.01)

        word_text = (last_word.get("word", "") or last_word.get("value", "") or last_word.get("text", "")).strip()

        # Base score from gap duration (logarithmic — 0.3s=1, 1s=3, 2s=5, 5s=8)
        score = max(0, math.log(max(gap_size, 0.01) / 0.1) * 2.0)

        # Sentence-ending punctuation bonus (period, !, ?, ellipsis)
        if re.search(r'[.!?…]$', word_text):
            score += 10.0  # Strong signal: sentence complete

        # Clause-ending punctuation bonus (comma, semicolon, colon, dash)
        elif re.search(r'[,;:\-–—]$', word_text):
            score += 4.0

        # Verse/section break bonus (very long pause > 3s)
        if gap_size > 3.0:
            score += 8.0  # Likely a section break
        elif gap_size > 1.5:
            score += 4.0  # Phrase break
        elif gap_size > 0.5:
            score += 1.5  # Noticeable pause

        gap_center = (end_i + start_next) / 2.0

        gaps.append({
            "time": gap_center,
            "after_word_idx": last_word_idx,
            "gap_size": gap_size,
            "score": score,
            "word_before": word_text,
        })

    # Sort by score descending (best boundary points first)
    gaps.sort(key=lambda g: g["score"], reverse=True)
    return gaps


def _snap_boundaries_to_word_gaps(
    boundaries: list[float],
    words: list[dict],
    search_window: float = 3.0,
    fps: int = 24,
    lyrics_text: str = "",
    max_scene_duration: float = 0,
) -> list[float]:
    """Adjust scene boundaries so they fall ONLY at sentence/phrase breaks.

    HARD CONSTRAINT: Boundaries may ONLY be placed between complete
    sentences/phrases — never mid-sentence. A scene always starts and
    ends with a complete lyrical phrase. The algorithm:

    1. Group all words into sentences/phrases (by punctuation and pauses).
    2. Build a scored list of gaps ONLY at sentence boundaries — gaps
       inside a sentence are never candidates.
    3. For each interior boundary, find the BEST-SCORED sentence gap
       within a search window. If none found, expand the window until
       one is found (we never fall back to mid-sentence placement).
    4. Apply 0.3s lead time: boundaries are placed 0.3s before the next
       word starts, giving the viewer a moment to register the new visual
       before vocals resume (standard music video editing convention).

    This ensures scenes contain complete phrases — "Black hat on a
    wooden chair" will NEVER be split across two scenes.
    """
    if not words or len(boundaries) < 3:
        return boundaries

    adjusted = list(boundaries)

    # Build scored gaps — ranked by phrase-boundary quality
    scored_gaps = _build_phrase_gaps(words, lyrics_text=lyrics_text)

    # Also build a simple list for fast lookup of ALL gaps (even tiny ones)
    all_word_gaps: list[tuple[int, float, float]] = []  # (word_idx, gap_start, gap_end)
    for i in range(len(words) - 1):
        end_i = words[i].get("end", 0)
        start_next = words[i + 1].get("start", 0)
        all_word_gaps.append((i, end_i, start_next))

    # Track which gap positions have been used (avoid two boundaries in same gap)
    used_gap_indices: set[int] = set()

    # Adjust each interior boundary (skip first and last)
    for b_idx in range(1, len(adjusted) - 1):
        boundary = adjusted[b_idx]
        prev_boundary = adjusted[b_idx - 1]
        next_boundary = adjusted[b_idx + 1] if b_idx + 1 < len(adjusted) else boundary + 30

        # Find the best-scored gap within the search window
        best_gap = None
        best_score = -999.0

        for gap in scored_gaps:
            gap_time = gap["time"]
            gap_idx = gap["after_word_idx"]

            # Must be within search window of the original boundary
            if abs(gap_time - boundary) > search_window:
                continue

            # Must not overlap with adjacent boundaries (leave at least 2s margin)
            if gap_time <= prev_boundary + 2.0 or gap_time >= next_boundary - 2.0:
                continue

            # Prefer unused gaps
            if gap_idx in used_gap_indices:
                continue

            # Max scene duration constraint: moving boundary here must not
            # make either adjacent scene exceed max_scene_duration
            if max_scene_duration > 0:
                left_dur = gap_time - prev_boundary
                right_dur = next_boundary - gap_time
                if left_dur > max_scene_duration or right_dur > max_scene_duration:
                    continue

            if gap["score"] > best_score:
                best_score = gap["score"]
                best_gap = gap

        if best_gap is not None:
            used_gap_indices.add(best_gap["after_word_idx"])

            # Place boundary with 0.3s lead time before next word starts
            next_word_start = words[best_gap["after_word_idx"] + 1].get("start", 0) if best_gap["after_word_idx"] + 1 < len(words) else best_gap["time"]
            # Use the gap midpoint, but shift toward the end of the gap
            # (0.3s before the next word, or gap midpoint if gap is small)
            if best_gap["gap_size"] > 0.6:
                new_boundary = next_word_start - 0.3
            else:
                new_boundary = best_gap["time"]

            new_boundary = max(prev_boundary + 1.0, new_boundary)
            adjusted[b_idx] = round(new_boundary, 2)
            logger.info(
                f"Boundary {b_idx}: {boundary:.2f}s → {adjusted[b_idx]:.2f}s "
                f"(score={best_score:.1f}, gap={best_gap['gap_size']:.2f}s "
                f"after '{best_gap['word_before']}')"
            )
            continue

        # Fallback: no sentence gap found in initial window.
        # Expand window progressively until we find a sentence boundary.
        # NEVER fall back to mid-sentence placement.
        expanded_gap = None
        for expand in [6.0, 10.0, 20.0, 999.0]:
            for gap in scored_gaps:
                gap_time = gap["time"]
                gap_idx = gap["after_word_idx"]
                if abs(gap_time - boundary) > expand:
                    continue
                if gap_time <= prev_boundary + 2.0 or gap_time >= next_boundary - 2.0:
                    continue
                if gap_idx in used_gap_indices:
                    continue
                # Max scene duration constraint
                if max_scene_duration > 0:
                    left_dur = gap_time - prev_boundary
                    right_dur = next_boundary - gap_time
                    if left_dur > max_scene_duration or right_dur > max_scene_duration:
                        continue
                expanded_gap = gap
                break
            if expanded_gap:
                break

        if expanded_gap is not None:
            used_gap_indices.add(expanded_gap["after_word_idx"])
            next_word_start = words[expanded_gap["after_word_idx"] + 1].get("start", 0) if expanded_gap["after_word_idx"] + 1 < len(words) else expanded_gap["time"]
            if expanded_gap["gap_size"] > 0.6:
                new_boundary = next_word_start - 0.3
            else:
                new_boundary = expanded_gap["time"]
            new_boundary = max(prev_boundary + 1.0, new_boundary)
            adjusted[b_idx] = round(new_boundary, 2)
            logger.info(
                f"Boundary {b_idx}: {boundary:.2f}s → {adjusted[b_idx]:.2f}s "
                f"(expanded search, score={expanded_gap['score']:.1f}, "
                f"after '{expanded_gap['word_before']}')"
            )
        else:
            # Absolute last resort: no sentence gaps exist at all (instrumental section?)
            # Keep original boundary but ensure it doesn't straddle a word
            for w in words:
                w_start = w.get("start", 0)
                w_end = w.get("end", 0)
                if w_start < boundary < w_end:
                    new_boundary = max(prev_boundary + 0.5, w_start - 0.3)
                    adjusted[b_idx] = round(new_boundary, 2)
                    word_text = (w.get("word", "") or w.get("value", "") or w.get("text", "")).strip()
                    logger.info(
                        f"Boundary {b_idx}: {boundary:.2f}s → {adjusted[b_idx]:.2f}s "
                        f"(no sentence gaps — moved before straddled word '{word_text}')"
                    )
                    break

    # Snap all boundaries to frame boundaries for exact integer frame counts
    adjusted = [_snap_to_frame(t, fps) for t in adjusted]

    # Ensure boundaries are still monotonically increasing
    for i in range(1, len(adjusted)):
        if adjusted[i] <= adjusted[i - 1]:
            adjusted[i] = _snap_to_frame(adjusted[i - 1] + 1.0 / fps, fps)

    return adjusted


@router.post(
    "/scenes-from-sections",
    status_code=status.HTTP_201_CREATED,
    summary="Create scenes from sections",
)
async def create_scenes_from_sections(
    project_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Auto-create scenes from detected song sections.

    Creates one scene per detected section with automatic naming and timing.

    Args:
        project_id: UUID of the project.
        session: Database session.

    Returns:
        Dictionary with created_count and scene_ids.

    Raises:
        HTTPException: If project not found.
    """
    try:
        await _get_project_or_404(project_id, session)

        # Delete ALL existing scenes — the user explicitly chose to recreate
        # scenes from sections, so old scenes (including user-edited ones) must go.
        # Previously this only deleted scenes with empty prompts, which left
        # orphaned scenes in the DB causing ghost scene counts.
        existing_scenes_stmt = select(Scene).where(Scene.project_id == project_id)
        existing_scenes_result = await session.execute(existing_scenes_stmt)
        old_scenes = existing_scenes_result.scalars().all()
        if old_scenes:
            logger.info(f"Deleting {len(old_scenes)} existing scenes before creating new ones from sections")
            for old_scene in old_scenes:
                await session.delete(old_scene)
        await session.flush()

        # Get all sections for project
        stmt = (
            select(SongSection)
            .where(SongSection.project_id == project_id)
            .order_by(SongSection.start_time)
        )
        result = await session.execute(stmt)
        sections = result.scalars().all()

        # Get project FPS for frame alignment
        proj_stmt = select(Project).where(Project.id == project_id)
        proj_result = await session.execute(proj_stmt)
        project = proj_result.scalars().first()
        project_fps = 24
        if project and project.settings:
            project_fps = project.settings.get("project_fps", 24) or 24

        # Load word timestamps and lyrics text to snap boundaries to phrase gaps
        lyrics_stmt = select(Lyrics).where(Lyrics.project_id == project_id)
        lyrics_result = await session.execute(lyrics_stmt)
        lyrics_record = lyrics_result.scalars().first()
        word_timestamps = (lyrics_record.words or []) if lyrics_record else []
        # Use user's pasted lyrics (with line breaks) as phrase boundary source
        user_lyrics_text = (lyrics_record.initial_text or lyrics_record.full_text or "") if lyrics_record else ""

        # Build adjusted boundaries that don't cut through phrases
        boundaries = [s.start_time for s in sections]
        if sections:
            boundaries.append(sections[-1].end_time)
        boundaries = _snap_boundaries_to_word_gaps(
            boundaries, word_timestamps, fps=project_fps, lyrics_text=user_lyrics_text
        )

        created_scenes = []

        for idx, section in enumerate(sections):
            scene = Scene(
                project_id=project_id,
                order_index=idx,
                name=f"{section.label.value.capitalize()} {idx + 1}",
                start_time=_snap_to_frame(boundaries[idx], project_fps),
                end_time=_snap_to_frame(boundaries[idx + 1], project_fps),
                prompt="",
                negative_prompt="",
            )
            session.add(scene)
            await session.flush()
            created_scenes.append(scene.id)

        await session.commit()
        logger.info(f"Created {len(created_scenes)} scenes from sections in project {project_id}")

        # Slice master audio into per-scene clips
        try:
            sliced = await _slice_audio_for_scenes(project_id, session)
            await session.commit()
            logger.info(f"Sliced audio for {sliced} scenes")
        except Exception as e:
            logger.warning(f"Audio slicing failed: {e}")

        return {
            "created_count": len(created_scenes),
            "scene_ids": [str(sid) for sid in created_scenes],
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating scenes from sections: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create scenes from sections",
        )


@router.get(
    "/stems/{scene_id}",
    summary="Get scene stems",
)
async def get_scene_stems(
    project_id: UUID,
    scene_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Get selected stems for a scene.

    Args:
        project_id: UUID of the project.
        scene_id: UUID of the scene.
        session: Database session.

    Returns:
        Dictionary with stem selections (vocals, drums, bass, other booleans).

    Raises:
        HTTPException: If project or scene not found.
    """
    try:
        await _get_project_or_404(project_id, session)

        scene = await session.get(Scene, scene_id)
        if not scene or scene.project_id != project_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Scene {scene_id} not found",
            )

        # Query the StemSelection record from the database
        stmt = select(StemSelection).where(StemSelection.scene_id == scene_id)
        result = await session.execute(stmt)
        stem_selection = result.scalars().first()

        if stem_selection:
            return {
                "vocals": stem_selection.vocals,
                "drums": stem_selection.drums,
                "bass": stem_selection.bass,
                "other": stem_selection.other,
            }

        # Return default if no selection exists
        return {
            "vocals": True,
            "drums": True,
            "bass": True,
            "other": True,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting stems for scene {scene_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get stems",
        )


@router.post(
    "/stems/{scene_id}/mix",
    summary="Create mixed audio",
)
async def create_stem_mix(
    project_id: UUID,
    scene_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Create a mixed audio file from selected stems for a scene's time range.

    Mixes the selected stems (vocals, drums, bass, other) based on the scene's
    stem_selection settings and time boundaries.

    Args:
        project_id: UUID of the project.
        scene_id: UUID of the scene.
        session: Database session.

    Returns:
        Dictionary with path to mixed audio file.

    Raises:
        HTTPException: If project or scene not found.
    """
    try:
        await _get_project_or_404(project_id, session)

        scene = await session.get(Scene, scene_id)
        if not scene or scene.project_id != project_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Scene {scene_id} not found",
            )

        # Get stem selection for scene
        stmt = select(StemSelection).where(StemSelection.scene_id == scene_id)
        result = await session.execute(stmt)
        stem_selection = result.scalars().first()

        if not stem_selection:
            stem_selection = StemSelection(
                scene_id=scene_id,
                vocals=True,
                drums=True,
                bass=True,
                other=True,
            )

        # Get stem file paths from project assets
        project_path = settings.project_dir / str(project_id)
        stems_dir = project_path / "assets" / "stems"

        if not stems_dir.exists():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No stems available for this project",
            )

        # Mix selected stems using soundfile/numpy
        try:
            import soundfile as sf
            import numpy as np
        except ImportError:
            logger.error("soundfile or numpy not installed")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Audio mixing dependencies not available",
            )

        # Load and mix stems
        stems_to_mix = []
        stem_names = ["vocals", "drums", "bass", "other"]
        stem_flags = [
            stem_selection.vocals,
            stem_selection.drums,
            stem_selection.bass,
            stem_selection.other,
        ]

        for stem_name, include in zip(stem_names, stem_flags):
            if include:
                stem_path = stems_dir / f"{stem_name}.wav"
                if stem_path.exists():
                    data, sr = sf.read(str(stem_path))
                    stems_to_mix.append(data)

        if not stems_to_mix:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No selected stems found",
            )

        # Mix stems by averaging
        mixed = np.mean(stems_to_mix, axis=0)

        # Save mixed audio to project cache
        cache_audio_dir = project_path / "cache" / "audio"
        cache_audio_dir.mkdir(parents=True, exist_ok=True)
        mixed_path = cache_audio_dir / f"mixed-{scene_id}.wav"

        sf.write(str(mixed_path), mixed, sr)

        return {"path": f"cache/audio/mixed-{scene_id}.wav"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating stem mix for scene {scene_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create stem mix",
        )


@router.get(
    "/waveform-peaks",
    response_model=WaveformPeaksResponse,
    summary="Get waveform peaks",
)
async def get_waveform_peaks(
    project_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> WaveformPeaksResponse:
    """Get pre-computed waveform peaks for wavesurfer.js visualization.

    Computes and caches peaks on demand if not already cached.

    Args:
        project_id: UUID of the project.
        session: Database session.

    Returns:
        Waveform peaks data with duration and channel count.

    Raises:
        HTTPException: If project not found.
    """
    try:
        await _get_project_or_404(project_id, session)

        project_path = settings.project_dir / str(project_id)
        cache_dir = project_path / "cache"
        peaks_cache_path = cache_dir / "waveform_peaks.json"

        # Check if peaks are cached
        if peaks_cache_path.exists():
            with open(peaks_cache_path, "r") as f:
                cached_data = json.load(f)
                return WaveformPeaksResponse(
                    peaks=cached_data.get("peaks", []),
                    duration=cached_data.get("duration", 0.0),
                    channels=cached_data.get("channels", 2),
                )

        # Compute peaks from audio file if not cached
        audio_dir = project_path / "assets" / "audio"
        audio_files = list(audio_dir.glob("*.wav")) if audio_dir.exists() else []

        if not audio_files:
            return WaveformPeaksResponse(peaks=[], duration=0.0, channels=2)

        # Use first audio file
        audio_path = audio_files[0]

        try:
            import soundfile as sf
        except ImportError:
            logger.error("soundfile not installed")
            return WaveformPeaksResponse(peaks=[], duration=0.0, channels=2)

        # Read audio and compute peaks
        data, sr = sf.read(str(audio_path))

        # Handle mono/stereo
        if len(data.shape) == 1:
            channels = 1
        else:
            channels = data.shape[1]

        # Downsample to compute peaks (1000 samples per second)
        target_samples = int((len(data) / sr) * 1000)
        if target_samples > 0:
            import numpy as np

            peaks = np.abs(data.reshape(-1) if channels == 1 else data.mean(axis=1))
            peaks = np.interp(
                np.linspace(0, len(peaks) - 1, target_samples),
                np.arange(len(peaks)),
                peaks,
            )
            peaks = peaks.tolist()
        else:
            peaks = []

        duration = len(data) / sr

        # Cache the peaks
        cache_dir.mkdir(parents=True, exist_ok=True)
        peaks_data = {
            "peaks": peaks,
            "duration": duration,
            "channels": channels,
        }
        with open(peaks_cache_path, "w") as f:
            json.dump(peaks_data, f)

        return WaveformPeaksResponse(
            peaks=peaks,
            duration=duration,
            channels=channels,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting waveform peaks for project {project_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get waveform peaks",
        )


# ===========================================================================
# Suggest Fresh Timeline — LLM-powered scene generation
# ===========================================================================

class SuggestTimelineResponse(BaseModel):
    """Response from suggest-timeline endpoint."""
    created_count: int
    scene_ids: list[str]
    message: str


def _get_llm_config(app_settings: AppSettings) -> tuple[str, str, str]:
    """Pick the default (or first available) LLM provider, return (provider, api_key, model)."""
    from backend.api.settings import resolve_llm_config
    return resolve_llm_config(app_settings)


@router.post(
    "/suggest-timeline",
    response_model=SuggestTimelineResponse,
    summary="Use LLM to suggest an optimal scene timeline",
)
async def suggest_timeline(
    project_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> SuggestTimelineResponse:
    """Analyze all available data (lyrics, word timings, sections, concept)
    and use an LLM to generate an optimal set of scenes.

    Key constraints:
    - Each scene must be 3–15 seconds (AI video model limit)
    - Scenes (except first/last) should start ~0.3–0.5s before a lyric/vocal onset
    - Focus on main structural tags ([Verse], [Chorus], etc.), skip adlibs/tags
    - If no lyrics, fall back to section-based or evenly-spaced scenes
    """
    from backend.api.concept import _call_llm

    project = await _get_project_or_404(project_id, session)

    # ── Gather all available data ────────────────────────────────────────

    # 1) Sections
    sections_stmt = (
        select(SongSection)
        .where(SongSection.project_id == project_id)
        .order_by(SongSection.start_time)
    )
    sections_result = await session.execute(sections_stmt)
    sections = sections_result.scalars().all()

    # 2) Lyrics
    lyrics_stmt = select(Lyrics).where(Lyrics.project_id == project_id)
    lyrics_result = await session.execute(lyrics_stmt)
    lyrics_record = lyrics_result.scalars().first()

    user_lyrics = ""
    whisper_text = ""
    word_timestamps: list[dict[str, Any]] = []
    if lyrics_record:
        user_lyrics = (getattr(lyrics_record, "initial_text", "") or "").strip()
        whisper_text = (lyrics_record.full_text or "").strip()
        word_timestamps = lyrics_record.words or []

    # ── DIAGNOSTIC: Log lyrics state ────────────────────────────────
    if user_lyrics:
        lyrics_lines = [l for l in user_lyrics.splitlines() if l.strip()]
        logger.info(
            f"[SuggestTimeline] initial_text has {len(user_lyrics)} chars, "
            f"{len(lyrics_lines)} lines (phrase-aware mode ACTIVE)"
        )
        for i, line in enumerate(lyrics_lines[:5]):
            logger.info(f"[SuggestTimeline]   Line {i}: '{line[:60]}'")
        if len(lyrics_lines) > 5:
            logger.info(f"[SuggestTimeline]   ... and {len(lyrics_lines) - 5} more lines")
    else:
        logger.warning(
            f"[SuggestTimeline] initial_text is EMPTY — phrase-aware mode DISABLED. "
            f"Whisper text: {len(whisper_text)} chars, "
            f"Word timestamps: {len(word_timestamps)}"
        )
        # If we have Whisper text but no user lyrics, try to reconstruct
        # phrase boundaries from capital letters and pauses in Whisper output
        if word_timestamps and not user_lyrics:
            reconstructed_lines = _reconstruct_phrases_from_whisper(word_timestamps)
            if reconstructed_lines:
                user_lyrics = "\n".join(reconstructed_lines)
                logger.info(
                    f"[SuggestTimeline] Reconstructed {len(reconstructed_lines)} phrase lines "
                    f"from Whisper word timestamps (capital letters + pauses)"
                )

    logger.info(
        f"[SuggestTimeline] Word timestamps: {len(word_timestamps)}, "
        f"user_lyrics: {len(user_lyrics)} chars"
    )

    # 3) Concept & style
    proj_settings = project.settings or {}
    concept_text = proj_settings.get("concept_text", "")
    style_text = proj_settings.get("style_text", "")
    song_title = proj_settings.get("song_title", "")
    project_fps = proj_settings.get("project_fps", 24) or 24

    # 4) Total duration from sections or audio asset
    total_duration = 0.0
    if sections:
        total_duration = max(s.end_time for s in sections)
    elif word_timestamps:
        total_duration = max(w.get("end", 0) for w in word_timestamps)

    if total_duration <= 0:
        # Try to find an audio asset to get duration
        audio_stmt = select(Asset).where(
            Asset.project_id == project_id,
            Asset.asset_type == AssetType.AUDIO,
        )
        audio_result = await session.execute(audio_stmt)
        audio_asset = audio_result.scalars().first()
        if audio_asset and audio_asset.metadata_:
            total_duration = audio_asset.metadata_.get("duration", 0)

    if total_duration <= 0:
        raise HTTPException(
            status_code=400,
            detail="Cannot determine audio duration. Please process audio first."
        )

    # ── Build sections text ──────────────────────────────────────────────

    sections_text = ""
    if sections:
        sections_text = "DETECTED SONG SECTIONS (from audio analysis):\n"
        for s in sections:
            sections_text += f"  [{s.label.value}] {s.start_time:.1f}s – {s.end_time:.1f}s\n"
    else:
        sections_text = "No song sections detected.\n"

    # ── Read video_max_duration from AppSettings (needed for cut points) ─

    settings_stmt = select(AppSettings).where(AppSettings.id == 1)
    settings_result = await session.execute(settings_stmt)
    app_settings = settings_result.scalars().first()
    if not app_settings:
        raise HTTPException(status_code=400, detail="App settings not configured")
    max_dur = app_settings.video_max_duration or 15
    min_dur = getattr(app_settings, 'video_min_duration', 5) or 5

    # ── Build the VALID CUT POINTS list ────────────────────────────────
    # These are the ONLY times where scene boundaries may be placed.
    # Each cut point sits between two complete phrases — never mid-phrase.

    valid_cuts: list[dict] = []  # [{time, label, after_phrase, before_phrase}, ...]

    if word_timestamps:
        # Group words into phrases
        phrase_groups = _group_words_into_sentences(word_timestamps, lyrics_text=user_lyrics)

        def _get_phrase_text(pg: list[dict]) -> str:
            return " ".join(
                (w.get("word", "") or w.get("value", "") or w.get("text", "")).strip()
                for w in pg
            ).strip()

        def _get_phrase_lyrics_line(pg: list[dict]) -> str:
            """Get the original user-typed lyrics line for a phrase group.
            Falls back to Whisper text if no source annotation exists."""
            if pg and pg[0].get("__source_lyrics_line__"):
                return pg[0]["__source_lyrics_line__"]
            return _get_phrase_text(pg)

        # Build cut point before the first phrase (intro → lyrics)
        if phrase_groups and phrase_groups[0]:
            first_start = phrase_groups[0][0].get("start", 0)
            if first_start > 0.3:
                cut_time = round(first_start - 0.3, 2)
                valid_cuts.append({
                    "time": cut_time,
                    "label": f"CUT_A: {cut_time:.2f}s — before first phrase starts",
                    "after_phrase": "(instrumental intro)",
                    "before_phrase": _get_phrase_text(phrase_groups[0])[:60],
                })

        # Build cut points between each pair of consecutive phrases
        for i in range(len(phrase_groups) - 1):
            pg_cur = phrase_groups[i]
            pg_next = phrase_groups[i + 1]
            if not pg_cur or not pg_next:
                continue

            phrase_end = pg_cur[-1].get("end", 0)
            next_start = pg_next[0].get("start", 0)
            gap = next_start - phrase_end

            # Place cut 0.3s before next phrase, or at gap midpoint if tight
            if gap > 0.6:
                cut_time = round(next_start - 0.3, 2)
            else:
                cut_time = round((phrase_end + next_start) / 2.0, 2)

            letter = chr(ord('A') + (len(valid_cuts) % 26))
            valid_cuts.append({
                "time": cut_time,
                "label": f"CUT_{letter}: {cut_time:.2f}s — between phrases (gap={gap:.1f}s)",
                "after_phrase": _get_phrase_text(pg_cur)[:60],
                "before_phrase": _get_phrase_text(pg_next)[:60],
            })

        # Build cut point after the last phrase (lyrics → instrumental outro)
        if phrase_groups and phrase_groups[-1]:
            last_end = phrase_groups[-1][-1].get("end", 0)
            if last_end < total_duration - 1.0:
                cut_time = round(last_end + 0.3, 2)
                letter = chr(ord('A') + (len(valid_cuts) % 26))
                valid_cuts.append({
                    "time": cut_time,
                    "label": f"CUT_{letter}: {cut_time:.2f}s — after last phrase ends",
                    "after_phrase": _get_phrase_text(phrase_groups[-1])[:60],
                    "before_phrase": "(instrumental outro)",
                })

        # For long instrumental regions (intro, outro, AND interior breaks),
        # add evenly-spaced cuts so scenes can be split to respect max_dur.
        # These are "safe" because no lyrics exist in those regions.

        # Build a list of "edge" times: 0.0, each cut point, total_duration
        # Then scan consecutive pairs for gaps > max_dur and fill them.
        edge_times = sorted(
            [0.0, total_duration] + [vc["time"] for vc in valid_cuts]
        )

        instrumental_cuts: list[dict] = []
        for ei in range(len(edge_times) - 1):
            gap_start = edge_times[ei]
            gap_end = edge_times[ei + 1]
            gap_len = gap_end - gap_start
            if gap_len > max_dur:
                # Fill this gap with evenly-spaced cuts
                n_splits = int(gap_len / (max_dur - 1.0))
                step = gap_len / (n_splits + 1)
                for si in range(1, n_splits + 1):
                    t = round(gap_start + step * si, 2)
                    # Don't place within 2s of existing edges
                    if t > gap_start + 2.0 and t < gap_end - 2.0:
                        region = "intro" if gap_start == 0.0 else (
                            "outro" if gap_end == total_duration else "instrumental break"
                        )
                        instrumental_cuts.append({
                            "time": t,
                            "label": f"CUT_INST: {t:.2f}s — {region} split",
                            "after_phrase": "(instrumental)",
                            "before_phrase": "(instrumental)",
                        })

        if instrumental_cuts:
            valid_cuts.extend(instrumental_cuts)
            logger.info(
                f"[SuggestTimeline] Added {len(instrumental_cuts)} instrumental "
                f"split points for gaps > {max_dur}s"
            )

        # Re-sort valid_cuts by time after adding instrumental cuts
        valid_cuts.sort(key=lambda vc: vc["time"])

    else:
        # No word timestamps — instrumental track.
        # Seed edge_times from section boundaries so the gap-filler can
        # place evenly-spaced cut points across long sections.
        section_times: list[float] = []
        if sections:
            for s in sections:
                if s.start_time > 0.0:
                    section_times.append(s.start_time)
                if s.end_time < total_duration:
                    section_times.append(s.end_time)

        edge_times = sorted(set([0.0, total_duration] + section_times))

        instrumental_cuts: list[dict] = []
        for ei in range(len(edge_times) - 1):
            gap_start = edge_times[ei]
            gap_end = edge_times[ei + 1]
            gap_len = gap_end - gap_start
            if gap_len > max_dur:
                n_splits = int(gap_len / (max_dur - 1.0))
                step = gap_len / (n_splits + 1)
                for si in range(1, n_splits + 1):
                    t = round(gap_start + step * si, 2)
                    if t > gap_start + 2.0 and t < gap_end - 2.0:
                        region = "intro" if gap_start == 0.0 else (
                            "outro" if gap_end == total_duration else "instrumental break"
                        )
                        instrumental_cuts.append({
                            "time": t,
                            "label": f"CUT_INST: {t:.2f}s — {region} split",
                            "after_phrase": "(instrumental)",
                            "before_phrase": "(instrumental)",
                        })

        if instrumental_cuts:
            valid_cuts.extend(instrumental_cuts)
            valid_cuts.sort(key=lambda vc: vc["time"])
            logger.info(
                f"[SuggestTimeline] Added {len(instrumental_cuts)} instrumental "
                f"cut points for instrumental track (no word timestamps)"
            )

        # Also add section boundaries as cut points directly
        for st in section_times:
            if st > 2.0 and st < total_duration - 2.0:
                # Don't duplicate if already near an existing cut
                if not any(abs(vc["time"] - st) < 1.0 for vc in valid_cuts):
                    valid_cuts.append({
                        "time": round(st, 2),
                        "label": f"CUT_SEC: {st:.2f}s — section boundary",
                        "after_phrase": "(instrumental)",
                        "before_phrase": "(instrumental)",
                    })

        valid_cuts.sort(key=lambda vc: vc["time"])
        logger.info(
            f"[SuggestTimeline] Instrumental track: {len(valid_cuts)} total cut points "
            f"from {len(section_times)} section boundaries"
        )

    # ── Build lyrics block for LLM ──────────────────────────────────────

    lyrics_block = ""
    if user_lyrics:
        lyrics_block += f"LYRICS (each line = one complete phrase):\n{user_lyrics}\n\n"

    # Show timed phrases so LLM knows when each phrase occurs
    if word_timestamps and phrase_groups:
        lyrics_block += "TIMED PHRASES (each line = one phrase with its time range):\n"
        for pg in phrase_groups:
            if not pg:
                continue
            p_start = pg[0].get("start", 0)
            p_end = pg[-1].get("end", 0)
            p_text = _get_phrase_text(pg)
            lyrics_block += f"  [{p_start:.2f}s – {p_end:.2f}s] {p_text}\n"
        lyrics_block += "\n"

    # Show the valid cut points — these are the ONLY allowed boundary times
    if valid_cuts:
        lyrics_block += "═══ VALID CUT POINTS ═══\n"
        lyrics_block += "You MUST use ONLY these exact times for scene boundaries (start_time / end_time).\n"
        lyrics_block += "Each cut point sits between two complete phrases. Using any other time WILL split a phrase.\n\n"
        for vc in valid_cuts:
            lyrics_block += f"  {vc['label']}\n"
            lyrics_block += f"    ends: \"{vc['after_phrase']}\"\n"
            lyrics_block += f"    next: \"{vc['before_phrase']}\"\n"
        lyrics_block += "\n"
    elif not word_timestamps:
        lyrics_block += "No word timestamps available.\n"

    if not lyrics_block:
        lyrics_block = "No lyrics available.\n"

    # ── LLM system prompt ────────────────────────────────────────────────

    system_prompt = (
        "You are a professional music video editor. Create a scene timeline for an AI-generated music video.\n\n"

        "═══ RULE #1: PHRASE INTEGRITY (HIGHEST PRIORITY) ═══\n"
        "NEVER split a lyrical phrase across two scenes. This is non-negotiable.\n"
        "- Each line of lyrics is ONE phrase. A phrase must be fully contained within a single scene.\n"
        "- You are given a list of VALID CUT POINTS below. These are the ONLY times you may use "
        "for start_time and end_time values (plus 0.0 for the first scene and the total duration for the last).\n"
        "- Using any time that is NOT a valid cut point WILL split a phrase. Do NOT invent your own times.\n"
        "- Phrases usually start with a capital letter. If you're unsure where a phrase begins, look for capitals.\n"
        "- It is ALWAYS better to have a slightly longer or shorter scene than to split a phrase.\n\n"

        "═══ RULE #2: DURATION CONSTRAINTS ═══\n"
        f"- Maximum scene duration: {max_dur} seconds. This is an ABSOLUTE hard limit.\n"
        f"- Ideal scene duration: {max(min_dur, max_dur // 2)}–{max(min_dur + 2, max_dur - 2)} seconds.\n"
        f"- Minimum scene duration: {min_dur} seconds. Do NOT create scenes shorter than this.\n"
        "- You MUST keep every scene at or under the maximum. Pick cut points that achieve this.\n"
        "- There are always enough cut points between phrases to keep scenes under the max.\n"
        "- If a section has many phrases close together, use more scenes (shorter durations).\n"
        "- If a section has few phrases spread far apart, each scene can be longer (up to max).\n\n"

        "═══ RULE #3: COVERAGE ═══\n"
        "- First scene starts at 0.0, last scene ends at total duration.\n"
        "- Scenes must tile with no gaps: scene N end_time = scene N+1 start_time.\n"
        "- Every second of audio must belong to exactly one scene.\n\n"

        "═══ SECTION HANDLING ═══\n"
        "- Long sections (> max duration) must be split at valid cut points.\n"
        f"- Instrumental intros/outros: single scene if under {max_dur}s, otherwise split evenly.\n"
        "- Ignore [ad-lib], [Background] tags.\n\n"

        "═══ NAMING ═══\n"
        "- Name scenes based on lyrical content or section: 'Verse 1 - Opening', 'Chorus - Hook', etc.\n"
        "- Sub-scenes: 'Verse 1 (Part 1)', 'Verse 1 (Part 2)'.\n\n"

        "═══ RETURN FORMAT ═══\n"
        "Return ONLY a JSON array:\n"
        "  [{ \"name\": \"Scene Name\", \"start_time\": 0.0, \"end_time\": 10.5 }, ...]\n"
        "Use times from the VALID CUT POINTS list. No markdown, no explanation."
    )

    # ── LLM user prompt ──────────────────────────────────────────────────

    user_prompt = (
        f"Total audio duration: {total_duration:.2f} seconds\n"
        f"Max scene duration: {max_dur} seconds\n"
        f"Min scene duration: {min_dur} seconds\n"
    )
    if song_title:
        user_prompt += f"Song Title: {song_title}\n"
    if concept_text:
        user_prompt += f"Video Concept: {concept_text}\n"
    if style_text:
        user_prompt += f"Visual Style: {style_text}\n"
    user_prompt += f"\n{sections_text}\n{lyrics_block}\n"
    user_prompt += (
        "Generate an optimal scene timeline using ONLY the valid cut points for boundaries. "
        "Return ONLY the JSON array."
    )

    # ── Call LLM ─────────────────────────────────────────────────────────

    provider, api_key, model = _get_llm_config(app_settings)

    logger.info(f"[SuggestTimeline] Calling LLM ({provider}/{model}) with {len(valid_cuts)} valid cut points")

    try:
        raw_text = await asyncio.to_thread(
            _call_llm, provider, api_key, model, system_prompt, user_prompt,
            max_tokens=4000,
        )
    except Exception as e:
        logger.error(f"LLM call failed for suggest-timeline: {e}")
        raise HTTPException(status_code=500, detail=f"LLM call failed: {e}")

    # ── Parse LLM response ───────────────────────────────────────────────

    try:
        cleaned = raw_text.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else cleaned[3:]
        if cleaned.endswith("```"):
            cleaned = cleaned.rsplit("```", 1)[0]
        cleaned = cleaned.strip()
        scenes_data = json.loads(cleaned)
        if not isinstance(scenes_data, list) or len(scenes_data) == 0:
            raise ValueError("Expected a non-empty JSON array")
    except (json.JSONDecodeError, ValueError) as e:
        # Fallback: try to extract a JSON array from within a prose response.
        # LLMs sometimes wrap valid JSON in explanation text, especially when
        # given 0 cut points (instrumental tracks).
        extracted = None
        try:
            start_idx = raw_text.find("[{")
            end_idx = raw_text.rfind("]")
            if start_idx != -1 and end_idx > start_idx:
                candidate = raw_text[start_idx:end_idx + 1]
                extracted = json.loads(candidate)
                if not isinstance(extracted, list) or len(extracted) == 0:
                    extracted = None
        except (json.JSONDecodeError, Exception):
            extracted = None

        if extracted:
            logger.info(
                f"[SuggestTimeline] Extracted JSON array from prose response "
                f"({len(extracted)} scenes)"
            )
            scenes_data = extracted
        else:
            logger.error(f"Failed to parse LLM response: {e}\nRaw: {raw_text[:500]}")
            _write_llm_log(
                endpoint="suggest_timeline",
                provider=provider, model=model,
                system_prompt=system_prompt, user_prompt=user_prompt,
                raw_response=raw_text, error=str(e),
                extra={"valid_cuts_count": len(valid_cuts), "max_dur": max_dur, "min_dur": min_dur},
            )
            raise HTTPException(status_code=500, detail="Failed to parse LLM scene response")

    # Write success log with full request/response
    _write_llm_log(
        endpoint="suggest_timeline",
        provider=provider, model=model,
        system_prompt=system_prompt, user_prompt=user_prompt,
        raw_response=raw_text, parsed_result=scenes_data,
        extra={
            "valid_cuts_count": len(valid_cuts),
            "valid_cut_times": [vc["time"] for vc in valid_cuts],
            "max_dur": max_dur,
            "min_dur": min_dur,
            "total_duration": total_duration,
        },
    )

    # ── Validate and fix LLM output ─────────────────────────────────────
    # Strategy: build a clean boundary list from LLM output, snap each
    # interior boundary to the nearest valid cut point, then split any
    # oversized scenes.  Boundaries are the ONLY thing that matters —
    # scene names are cosmetic.

    # Build the set of valid cut times for fast lookup.
    # Include 0.0 and total_duration as always-valid endpoints.
    valid_cut_times = sorted(set(
        [0.0, round(total_duration, 3)]
        + [vc["time"] for vc in valid_cuts]
    ))

    def _nearest_valid_cut(t: float, max_dist: float = 999.0) -> Optional[float]:
        """Find the nearest valid cut point to time t within max_dist.
        Returns None if no cut point is within max_dist."""
        if not valid_cut_times:
            return None
        best = min(valid_cut_times, key=lambda vt: abs(vt - t))
        if abs(best - t) > max_dist:
            return None
        return best

    # Parse LLM scenes — extract unique interior boundaries
    llm_scenes: list[dict[str, Any]] = []
    for i, sd in enumerate(scenes_data):
        name = sd.get("name", f"Scene {i + 1}")
        start = float(sd.get("start_time", 0))
        end = float(sd.get("end_time", start + 10))
        start = max(0, min(start, total_duration))
        end = max(start + 0.5, min(end, total_duration))
        llm_scenes.append({"name": name, "start_time": start, "end_time": end})

    logger.info(f"[SuggestTimeline] LLM returned {len(llm_scenes)} scenes")
    for i, s in enumerate(llm_scenes):
        logger.info(
            f"[SuggestTimeline]   LLM Scene {i}: '{s['name']}' "
            f"[{s['start_time']:.2f}–{s['end_time']:.2f}s] "
            f"dur={s['end_time'] - s['start_time']:.1f}s"
        )

    # Extract interior boundaries (between scenes) from LLM output.
    # These are the end_time of each scene except the last.
    llm_boundaries: list[float] = []
    for i in range(len(llm_scenes) - 1):
        llm_boundaries.append(llm_scenes[i]["end_time"])

    # Snap each interior boundary to the nearest valid cut point.
    # Use a generous max distance (8s) — if the LLM is further off than
    # that, it's probably in a region with no phrases and any time is OK.
    MAX_SNAP = 8.0
    snapped_boundaries: list[float] = []
    for bi, b in enumerate(llm_boundaries):
        nearest = _nearest_valid_cut(b, max_dist=MAX_SNAP)
        if nearest is not None and abs(nearest - b) > 0.01:
            logger.info(
                f"[SuggestTimeline] Boundary {bi}: {b:.2f}s → snapped to "
                f"valid cut {nearest:.2f}s (dist={abs(nearest - b):.1f}s)"
            )
            snapped_boundaries.append(nearest)
        else:
            snapped_boundaries.append(b)

    # Remove duplicate boundaries (two LLM scenes snapping to same cut)
    # while preserving order
    seen_boundaries: set[float] = set()
    unique_boundaries: list[float] = []
    for b in snapped_boundaries:
        b_rounded = round(b, 3)
        if b_rounded not in seen_boundaries:
            seen_boundaries.add(b_rounded)
            unique_boundaries.append(b)

    # Rebuild scenes from boundaries: [0.0, b1, b2, ..., total_duration]
    all_boundaries = [0.0] + unique_boundaries + [total_duration]

    # Map back scene names from LLM output
    rebuilt_scenes: list[dict[str, Any]] = []
    for i in range(len(all_boundaries) - 1):
        s_start = all_boundaries[i]
        s_end = all_boundaries[i + 1]
        # Find matching LLM scene name
        if i < len(llm_scenes):
            name = llm_scenes[i]["name"]
        else:
            name = f"Scene {i + 1}"
        rebuilt_scenes.append({
            "name": name,
            "start_time": s_start,
            "end_time": s_end,
        })

    logger.info(
        f"[SuggestTimeline] After boundary validation: "
        f"{len(rebuilt_scenes)} scenes from {len(all_boundaries)} boundaries"
    )

    # ── Split oversized scenes at valid cut points ──────────────────────
    # Use ALL valid_cut_times (which includes intro/outro instrumental cuts)
    # IMPORTANT: respect min_dur when splitting — never create sub-scenes
    # shorter than the user's minimum duration setting.

    def _split_oversized(
        scenes_in: list[dict[str, Any]],
        cut_times: list[float],
        max_d: float,
        min_d: float,
        pass_label: str = "",
    ) -> list[dict[str, Any]]:
        """Split any scene exceeding max_d at the nearest valid cut points.

        This is factored out so it can be called TWICE:
        1) After initial LLM boundary validation
        2) After phrase integrity checks and min-dur merges (which can re-create oversized scenes)
        """
        result: list[dict[str, Any]] = []
        for sd in scenes_in:
            dur = sd["end_time"] - sd["start_time"]
            if dur <= max_d:
                result.append(sd)
                continue

            # Find valid cut points strictly inside this scene
            # Use a relaxed min_d for the inner search: allow 2s minimum
            # sub-scene to avoid rejecting all cuts when min_d is large
            effective_min = min(min_d, dur / 3.0, 3.0)
            interior_cuts = sorted([
                vt for vt in cut_times
                if sd["start_time"] + effective_min < vt < sd["end_time"] - effective_min
            ])

            logger.info(
                f"[SplitOversized{pass_label}] Scene '{sd['name']}' "
                f"[{sd['start_time']:.2f}–{sd['end_time']:.2f}s] dur={dur:.1f}s > {max_d}s, "
                f"found {len(interior_cuts)} interior cuts (effective_min={effective_min:.1f}s)"
            )
            if interior_cuts:
                logger.info(
                    f"[SplitOversized{pass_label}]   Interior cut times: "
                    f"{[round(c, 2) for c in interior_cuts[:20]]}"
                )

            if not interior_cuts:
                # Last resort: create evenly-spaced cuts ignoring phrase boundaries
                n_parts = max(2, int(dur / max_d) + 1)
                step = dur / n_parts
                logger.warning(
                    f"[SplitOversized{pass_label}] No interior cuts for "
                    f"'{sd['name']}' ({dur:.1f}s) — force-splitting into {n_parts} "
                    f"equal parts of ~{step:.1f}s"
                )
                sub_scenes: list[dict[str, Any]] = []
                for p in range(n_parts):
                    s_start = sd["start_time"] + step * p
                    s_end = sd["start_time"] + step * (p + 1) if p < n_parts - 1 else sd["end_time"]
                    sub_scenes.append({
                        "name": f"{sd['name']} (Part {p + 1})" if n_parts > 1 else sd["name"],
                        "start_time": round(s_start, 3),
                        "end_time": round(s_end, 3),
                    })
                result.extend(sub_scenes)
                continue

            # Use interior cuts to split
            sub_scenes = []
            cursor = sd["start_time"]
            part = 1
            while cursor < sd["end_time"] - 0.5:
                remaining = sd["end_time"] - cursor
                if remaining <= max_d:
                    sub_scenes.append({
                        "name": f"{sd['name']} (Part {part})" if part > 1 else sd["name"],
                        "start_time": cursor,
                        "end_time": sd["end_time"],
                    })
                    break
                # Find the latest cut that keeps this segment ≤ max_d
                candidates = [
                    c for c in interior_cuts
                    if c >= cursor + effective_min
                    and c <= cursor + max_d
                ]
                if candidates:
                    pick = candidates[-1]
                else:
                    # Take the first cut after cursor, even if it exceeds max_d
                    after_cursor = [c for c in interior_cuts if c > cursor + 1.0]
                    if after_cursor:
                        pick = after_cursor[0]
                    else:
                        sub_scenes.append({
                            "name": f"{sd['name']} (Part {part})" if part > 1 else sd["name"],
                            "start_time": cursor,
                            "end_time": sd["end_time"],
                        })
                        break
                sub_scenes.append({
                    "name": f"{sd['name']} (Part {part})",
                    "start_time": cursor,
                    "end_time": pick,
                })
                cursor = pick
                part += 1
            result.extend(sub_scenes)
            logger.info(
                f"[SplitOversized{pass_label}] Split '{sd['name']}' into "
                f"{len(sub_scenes)} sub-scenes: "
                f"{[(round(s['start_time'],1), round(s['end_time'],1)) for s in sub_scenes]}"
            )
        return result

    validated_scenes = _split_oversized(rebuilt_scenes, valid_cut_times, max_dur, min_dur, " Pass1")

    # ── Frame-snap boundaries ──────────────────────────────────────────
    # Snap each boundary to the nearest frame, then propagate so scenes
    # remain perfectly contiguous.  We snap boundaries (not start/end
    # independently) to avoid creating gaps or overlaps.
    boundary_times = [0.0]
    for vs in validated_scenes:
        boundary_times.append(vs["end_time"])

    # Frame-snap interior boundaries only (keep 0.0 and total_duration exact)
    for i in range(1, len(boundary_times) - 1):
        boundary_times[i] = _snap_to_frame(boundary_times[i], project_fps)

    # Ensure last boundary is exactly total_duration
    boundary_times[-1] = total_duration

    # Rebuild scene times from snapped boundaries
    for i, vs in enumerate(validated_scenes):
        vs["start_time"] = boundary_times[i]
        vs["end_time"] = boundary_times[i + 1]

    # ── PHRASE INTEGRITY CHECK ────────────────────────────────────────
    # Safety net: after frame snapping, verify no interior boundary falls
    # inside any phrase group's time range.  If it does, move the boundary
    # to the nearest gap between phrases (with a small margin).
    if word_timestamps and phrase_groups:
        phrase_ranges = []
        for pg in phrase_groups:
            if not pg:
                continue
            p_start = pg[0].get("start", 0)
            p_end = pg[-1].get("end", 0)
            phrase_ranges.append((p_start, p_end))

        integrity_fixes = 0
        for bi in range(1, len(boundary_times) - 1):  # skip 0.0 and total_duration
            bt = boundary_times[bi]
            for p_start, p_end in phrase_ranges:
                # Is this boundary inside a phrase? (with small margin for frame jitter)
                if p_start + 0.05 < bt < p_end - 0.05:
                    # Boundary is inside a phrase — move it out.
                    # Option 1: move to just before phrase starts (0.15s before)
                    before_phrase = _snap_to_frame(max(0.0, p_start - 0.15), project_fps)
                    # Option 2: move to just after phrase ends (0.15s after)
                    after_phrase = _snap_to_frame(min(total_duration, p_end + 0.15), project_fps)
                    # Pick whichever is closer to original boundary
                    dist_before = abs(bt - before_phrase)
                    dist_after = abs(bt - after_phrase)
                    new_bt = before_phrase if dist_before <= dist_after else after_phrase
                    logger.warning(
                        f"[PhraseIntegrity] Boundary {bi} at {bt:.3f}s falls inside "
                        f"phrase [{p_start:.2f}–{p_end:.2f}s], moving to {new_bt:.3f}s"
                    )
                    boundary_times[bi] = new_bt
                    integrity_fixes += 1
                    break  # Fixed this boundary, move to next

        if integrity_fixes > 0:
            # Re-sort boundaries in case moves caused reordering
            boundary_times = sorted(set(round(b, 6) for b in boundary_times))
            # Ensure endpoints are present
            if boundary_times[0] != 0.0:
                boundary_times.insert(0, 0.0)
            if boundary_times[-1] != total_duration:
                boundary_times.append(total_duration)
            # Rebuild validated_scenes from fixed boundaries
            new_validated: list[dict[str, Any]] = []
            for i in range(len(boundary_times) - 1):
                s_start = boundary_times[i]
                s_end = boundary_times[i + 1]
                if s_end - s_start < 0.1:
                    continue
                if i < len(validated_scenes):
                    name = validated_scenes[i]["name"]
                else:
                    name = f"Scene {i + 1}"
                new_validated.append({
                    "name": name,
                    "start_time": s_start,
                    "end_time": s_end,
                })
            validated_scenes = new_validated
            logger.info(
                f"[PhraseIntegrity] Fixed {integrity_fixes} boundaries that split phrases. "
                f"Now {len(validated_scenes)} scenes."
            )

    # ── MINIMUM DURATION ENFORCEMENT ────────────────────────────────────
    # Merge scenes that are shorter than min_dur into their neighbors.
    # Prefer merging into the shorter neighbor to balance scene lengths.
    # Loop until no more tiny scenes can be merged (skip unmergeable ones).
    if min_dur > 0 and len(validated_scenes) > 1:
        merge_count = 0
        max_passes = len(validated_scenes)  # Can't merge more than we have
        for _pass in range(max_passes):
            merged_any = False
            for i in range(len(validated_scenes)):
                dur = validated_scenes[i]["end_time"] - validated_scenes[i]["start_time"]
                if dur >= min_dur - 0.1:
                    continue  # This scene is fine
                tiny = validated_scenes[i]
                tiny_dur = dur
                # Choose merge direction: prefer shorter neighbor, avoid exceeding max_dur
                prev_dur = (validated_scenes[i - 1]["end_time"] - validated_scenes[i - 1]["start_time"]) if i > 0 else float('inf')
                next_dur = (validated_scenes[i + 1]["end_time"] - validated_scenes[i + 1]["start_time"]) if i + 1 < len(validated_scenes) else float('inf')
                merge_into_prev = i > 0 and (prev_dur + tiny_dur <= max_dur + 0.5 or next_dur == float('inf'))
                merge_into_next = i + 1 < len(validated_scenes) and (next_dur + tiny_dur <= max_dur + 0.5 or prev_dur == float('inf'))
                if merge_into_prev and merge_into_next:
                    # Both fit — pick the shorter neighbor
                    if prev_dur <= next_dur:
                        merge_into_next = False
                    else:
                        merge_into_prev = False
                if merge_into_prev:
                    validated_scenes[i - 1]["end_time"] = tiny["end_time"]
                    logger.info(
                        f"[MinDuration] Merged tiny scene '{tiny['name']}' ({tiny_dur:.1f}s) "
                        f"into previous scene '{validated_scenes[i - 1]['name']}'"
                    )
                    validated_scenes.pop(i)
                    merge_count += 1
                    merged_any = True
                    break  # Restart scan — indices shifted
                elif merge_into_next:
                    validated_scenes[i + 1]["start_time"] = tiny["start_time"]
                    # Keep the tiny scene's name if it has lyrics content
                    logger.info(
                        f"[MinDuration] Merged tiny scene '{tiny['name']}' ({tiny_dur:.1f}s) "
                        f"into next scene '{validated_scenes[i + 1]['name']}'"
                    )
                    validated_scenes.pop(i)
                    merge_count += 1
                    merged_any = True
                    break  # Restart scan — indices shifted
                else:
                    # Can't merge without exceeding max_dur — skip this one, try others
                    logger.warning(
                        f"[MinDuration] Scene '{tiny['name']}' ({tiny_dur:.1f}s) is under "
                        f"min_dur={min_dur}s but merging would exceed max_dur={max_dur}s — skipping"
                    )
                    continue
            if not merged_any:
                break  # No more mergeable tiny scenes
        if merge_count > 0:
            logger.info(f"[MinDuration] Merged {merge_count} tiny scene(s), now {len(validated_scenes)} scenes")

    # Final sanity: remove any scenes that ended up with zero or negative duration
    validated_scenes = [
        vs for vs in validated_scenes
        if vs["end_time"] - vs["start_time"] > 0.1
    ]

    # ── SECOND SPLIT PASS ─────────────────────────────────────────────
    # Phrase integrity fixes and min-dur merges can re-create oversized
    # scenes.  Run the splitter again to catch any that slipped through.
    oversized_count = sum(
        1 for vs in validated_scenes
        if vs["end_time"] - vs["start_time"] > max_dur + 0.5
    )
    if oversized_count > 0:
        logger.warning(
            f"[SuggestTimeline] {oversized_count} scene(s) still exceed "
            f"max_dur={max_dur}s after phrase/min-dur passes — running split Pass 2"
        )
        validated_scenes = _split_oversized(
            validated_scenes, valid_cut_times, max_dur, min_dur, " Pass2"
        )
        # Clean up again
        validated_scenes = [
            vs for vs in validated_scenes
            if vs["end_time"] - vs["start_time"] > 0.1
        ]

    # De-duplicate names
    name_counts: dict[str, int] = {}
    for vs in validated_scenes:
        name_counts[vs["name"]] = name_counts.get(vs["name"], 0) + 1
    name_indices: dict[str, int] = {}
    for vs in validated_scenes:
        if name_counts[vs["name"]] > 1:
            name_indices[vs["name"]] = name_indices.get(vs["name"], 0) + 1
            vs["name"] = f"{vs['name']} (Part {name_indices[vs['name']]})"

    # Log final scenes with their phrases for debugging
    if word_timestamps:
        phrase_list = []
        for pg in phrase_groups:
            if not pg:
                continue
            phrase_list.append({
                "start": pg[0].get("start", 0),
                "end": pg[-1].get("end", 0),
                "text": _get_phrase_text(pg),
            })
        for vs in validated_scenes:
            scene_phrases = [
                p["text"][:50] for p in phrase_list
                if p["start"] >= vs["start_time"] - 0.5 and p["end"] <= vs["end_time"] + 0.5
            ]
            logger.info(
                f"[SuggestTimeline] Scene '{vs['name']}' "
                f"[{vs['start_time']:.2f}–{vs['end_time']:.2f}s]: "
                f"{len(scene_phrases)} phrases"
            )
            for pt in scene_phrases:
                logger.info(f"[SuggestTimeline]   → {pt}")

    logger.info(
        f"[SuggestTimeline] Final: {len(validated_scenes)} scenes "
        f"(max_dur={max_dur}s, total={total_duration:.1f}s)"
    )

    # ── Map real lyrics lines to each scene ─────────────────────────────
    # For each validated scene, find which phrase groups overlap with it
    # and collect their original lyrics line text (not Whisper transcription).
    # This ensures prompt generation uses the user's actual lyrics.
    #
    # Assignment strategy: START-TIME with BOUNDARY NUDGE
    # - A phrase belongs to the scene where it STARTS (not overlap %)
    # - Exception: if a phrase starts in the last ~1s of a scene but most
    #   of it plays in the NEXT scene, assign to the next scene instead
    #   (the listener perceives it as part of the new scene)
    _BOUNDARY_NUDGE = 1.0  # seconds — if phrase starts within this of scene end,
                            # check if it fits better in the next scene
    scene_lyrics_map: dict[int, str] = {}  # scene_index → lyrics text
    if word_timestamps and phrase_groups:
        # First, assign each phrase to a scene
        phrase_assignments: dict[int, int] = {}  # phrase_group_index → scene_index
        for pi, pg in enumerate(phrase_groups):
            if not pg:
                continue
            pg_start = pg[0].get("start", 0) or pg[0].get("start_time", 0)
            pg_end = pg[-1].get("end", 0) or pg[-1].get("end_time", 0)
            phrase_dur = max(0.01, pg_end - pg_start)

            # Find scene that contains the phrase's start time
            assigned = -1
            for si, vs in enumerate(validated_scenes):
                if vs["start_time"] <= pg_start < vs["end_time"]:
                    assigned = si
                    break

            # If not found (phrase starts after last scene), assign to last scene
            if assigned < 0:
                assigned = len(validated_scenes) - 1

            # Boundary nudge: if phrase starts near end of assigned scene,
            # check if most of it actually plays in the next scene
            vs = validated_scenes[assigned]
            time_to_scene_end = vs["end_time"] - pg_start
            if (time_to_scene_end <= _BOUNDARY_NUDGE
                    and assigned + 1 < len(validated_scenes)):
                # How much of phrase is in current scene vs next scene?
                overlap_current = max(0.0, min(pg_end, vs["end_time"]) - pg_start)
                next_vs = validated_scenes[assigned + 1]
                overlap_next = max(0.0, min(pg_end, next_vs["end_time"]) - next_vs["start_time"])
                if overlap_next > overlap_current:
                    logger.info(
                        f"[LyricsPerScene] Nudging phrase {pi} from scene {assigned} → "
                        f"{assigned + 1} (starts {time_to_scene_end:.1f}s before boundary, "
                        f"{overlap_next:.1f}s in next vs {overlap_current:.1f}s in current)"
                    )
                    assigned = assigned + 1

            phrase_assignments[pi] = assigned

        # Build scene lyrics from assignments
        for si, vs in enumerate(validated_scenes):
            scene_lines: list[str] = []
            for pi, pg in enumerate(phrase_groups):
                if phrase_assignments.get(pi) != si:
                    continue
                line_text = _get_phrase_lyrics_line(pg)
                if line_text and line_text not in scene_lines:
                    scene_lines.append(line_text)
            if scene_lines:
                scene_lyrics_map[si] = "\n".join(scene_lines)
                logger.info(
                    f"[LyricsPerScene] Scene {si} '{vs['name']}': "
                    f"{len(scene_lines)} lyrics line(s)"
                )
            else:
                scene_lyrics_map[si] = "(instrumental)"
                logger.info(
                    f"[LyricsPerScene] Scene {si} '{vs['name']}': (instrumental)"
                )

    # ── KEYFRAME WORD VERIFICATION PASS ──────────────────────────────────
    # After mapping lyrics to scenes, verify alignment by checking if
    # distinctive "keyframe" words from each lyrics line actually appear
    # in the Whisper data at the scene's time range.
    #
    # Strategy:
    # 1. Build a word→time index from Whisper data (word → list of timestamps)
    # 2. For each scene's assigned lyrics, extract distinctive words
    #    (words that appear ≤2 times in the full lyrics = more reliable anchors)
    # 3. Check if any keyframe word from that lyrics line has a Whisper
    #    timestamp inside the scene's time window
    # 4. If NONE of a line's keyframe words appear in the scene's time range,
    #    search for where they DO appear and reassign if a better scene exists
    if word_timestamps and phrase_groups and scene_lyrics_map:
        import re as _re

        def _clean_kw(w: str) -> str:
            return _re.sub(r'[^a-zA-Z0-9]', '', w).lower()

        # Build Whisper word → timestamps index
        whisper_word_times: dict[str, list[tuple[float, float]]] = {}
        for w in word_timestamps:
            raw = (w.get("word", "") or w.get("value", "") or w.get("text", "")).strip()
            cleaned = _clean_kw(raw)
            if cleaned and len(cleaned) >= 3:  # Skip tiny words (a, I, no, etc.)
                t_start = w.get("start", 0) or w.get("start_time", 0)
                t_end = w.get("end", 0) or w.get("end_time", 0)
                whisper_word_times.setdefault(cleaned, []).append((t_start, t_end))

        # Count word frequency across ALL lyrics to find distinctive words
        all_lyrics_text = "\n".join(scene_lyrics_map.values())
        all_lyrics_words = [_clean_kw(w) for w in all_lyrics_text.split() if _clean_kw(w)]
        word_freq: dict[str, int] = {}
        for w in all_lyrics_words:
            word_freq[w] = word_freq.get(w, 0) + 1

        # For each scene, verify its lyrics keyframe words
        reassignment_count = 0
        for si, vs in enumerate(validated_scenes):
            lyrics_text = scene_lyrics_map.get(si, "")
            if not lyrics_text or lyrics_text == "(instrumental)":
                continue

            s_start = vs["start_time"]
            s_end = vs["end_time"]
            margin = 1.5  # Allow 1.5s margin for timing jitter

            # Extract keyframe words: distinctive (freq ≤ 2), ≥ 4 chars
            scene_words = [_clean_kw(w) for w in lyrics_text.split() if _clean_kw(w)]
            keyframe_words = [
                w for w in scene_words
                if len(w) >= 4 and word_freq.get(w, 0) <= 2
                and w in whisper_word_times
            ]

            if not keyframe_words:
                continue  # No distinctive words to verify — skip

            # Check if ANY keyframe word has a Whisper timestamp in this scene
            found_in_scene = False
            for kw in keyframe_words:
                for (t_start, t_end) in whisper_word_times[kw]:
                    if s_start - margin <= t_start <= s_end + margin:
                        found_in_scene = True
                        break
                if found_in_scene:
                    break

            if found_in_scene:
                continue  # Lyrics verified — keyframe word matches scene audio

            # MISMATCH: lyrics don't match this scene's audio.
            # Find which scene these lyrics actually belong to based on
            # where the keyframe words ARE spoken.
            best_scene = -1
            best_count = 0
            for kw in keyframe_words:
                for (t_start, t_end) in whisper_word_times[kw]:
                    for sj, vs2 in enumerate(validated_scenes):
                        if sj == si:
                            continue
                        if vs2["start_time"] - margin <= t_start <= vs2["end_time"] + margin:
                            # This keyframe word is spoken during scene sj
                            if best_scene != sj:
                                # Count how many keyframe words match scene sj
                                count = sum(
                                    1 for kw2 in keyframe_words
                                    for (t2s, t2e) in whisper_word_times.get(kw2, [])
                                    if vs2["start_time"] - margin <= t2s <= vs2["end_time"] + margin
                                )
                                if count > best_count:
                                    best_count = count
                                    best_scene = sj

            if best_scene >= 0 and best_count >= 2:
                # Swap: move these lyrics to the correct scene
                old_lyrics = scene_lyrics_map.get(best_scene, "")
                correct_lyrics = scene_lyrics_map[si]

                # Only reassign if the target scene doesn't already have
                # this exact text and the lyrics actually belong there
                if correct_lyrics not in (old_lyrics or ""):
                    # Append to target scene (don't overwrite existing lyrics)
                    if old_lyrics and old_lyrics != "(instrumental)":
                        scene_lyrics_map[best_scene] = old_lyrics + "\n" + correct_lyrics
                    else:
                        scene_lyrics_map[best_scene] = correct_lyrics

                    # Remove from source scene — check if other lyrics should stay
                    scene_lyrics_map[si] = "(instrumental)"

                    reassignment_count += 1
                    logger.info(
                        f"[KeyframeVerify] Reassigned lyrics from scene {si} → {best_scene}: "
                        f"'{correct_lyrics[:60]}' "
                        f"({best_count} keyframe words confirmed in scene {best_scene}'s time range)"
                    )

        if reassignment_count > 0:
            logger.info(
                f"[KeyframeVerify] Verification complete: {reassignment_count} lyrics reassignment(s)"
            )
        else:
            logger.info("[KeyframeVerify] Verification complete: all lyrics confirmed in correct scenes")

    # ── Delete existing scenes and create new ones ───────────────────────

    existing_stmt = select(Scene).where(Scene.project_id == project_id)
    existing_result = await session.execute(existing_stmt)
    for old_scene in existing_result.scalars().all():
        await session.delete(old_scene)
    await session.flush()

    created_ids: list[str] = []
    for i, sd in enumerate(validated_scenes):
        scene_params: dict[str, Any] = {}
        # Store real lyrics per scene for prompt generation
        if i in scene_lyrics_map:
            scene_params["lyrics"] = scene_lyrics_map[i]
        scene = Scene(
            id=uuid4(),
            project_id=project_id,
            order_index=i,
            name=sd["name"],
            start_time=sd["start_time"],
            end_time=sd["end_time"],
            prompt="",
            negative_prompt="",
            parameters=scene_params,
        )
        session.add(scene)
        await session.flush()
        created_ids.append(str(scene.id))

    await session.commit()

    # Slice master audio into per-scene clips
    try:
        sliced = await _slice_audio_for_scenes(project_id, session)
        await session.commit()
        logger.info(f"Sliced audio for {sliced} scenes")
    except Exception as e:
        logger.warning(f"Audio slicing failed: {e}")

    logger.info(
        f"Suggest Fresh Timeline: created {len(created_ids)} scenes for project {project_id}"
    )

    return SuggestTimelineResponse(
        created_count=len(created_ids),
        scene_ids=created_ids,
        message=f"Created {len(created_ids)} scenes based on lyrics, sections, and timing analysis.",
    )


@router.post(
    "/slice-audio",
    summary="Slice master audio into per-scene clips",
)
async def slice_audio_for_scenes(
    project_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Re-slice the project's master audio into per-scene audio clips.

    Useful after manually adjusting scene boundaries or when setting up a new project.
    Creates individual WAV files for each scene based on start_time and end_time,
    storing the relative path in scene.parameters.audio_clip_path.

    Args:
        project_id: UUID of the project.
        session: Database session.

    Returns:
        Dictionary with sliced_count and message.

    Raises:
        HTTPException: If project not found or slicing fails.
    """
    await _get_project_or_404(project_id, session)

    try:
        count = await _slice_audio_for_scenes(project_id, session)
        await session.commit()
        return {"sliced_count": count, "message": f"Sliced audio for {count} scenes"}
    except Exception as e:
        logger.error(f"Audio slicing failed: {e}")
        raise HTTPException(status_code=500, detail=f"Audio slicing failed: {e}")


@router.post(
    "/slice-audio/{scene_id}",
    summary="Slice master audio for a single scene",
)
async def slice_audio_for_single_scene(
    project_id: UUID,
    scene_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Re-slice the project's master audio for one specific scene.

    Useful when a scene's boundaries have been manually adjusted, or when the
    scene's audio clip is missing and needs to be regenerated.
    """
    from backend.services.video.ffmpeg import slice_audio

    await _get_project_or_404(project_id, session)

    scene = await session.get(Scene, scene_id)
    if not scene or scene.project_id != project_id:
        raise HTTPException(status_code=404, detail="Scene not found")

    if scene.start_time is None or scene.end_time is None:
        raise HTTPException(status_code=400, detail="Scene has no timing data")

    # Find master audio
    stmt = (
        select(Asset)
        .where(Asset.project_id == project_id)
        .where(Asset.asset_type == AssetType.MUSIC)
        .where(~Asset.rel_path.contains("stems/"))
    )
    result = await session.execute(stmt)
    music_asset = result.scalars().first()
    if not music_asset:
        raise HTTPException(status_code=404, detail="No music asset found for project")

    audio_path = settings.project_dir / str(project_id) / music_asset.rel_path
    if not audio_path.exists():
        raise HTTPException(status_code=404, detail="Music file not found on disk")

    try:
        clips_dir = settings.project_dir / str(project_id) / "audio_clips"
        clips_dir.mkdir(parents=True, exist_ok=True)

        clip_filename = f"scene_{scene.order_index:03d}_{scene.start_time:.2f}_{scene.end_time:.2f}.wav"
        clip_path = clips_dir / clip_filename
        rel_clip_path = str(clip_path.relative_to(settings.project_dir))

        await asyncio.to_thread(
            slice_audio,
            str(audio_path),
            str(clip_path),
            scene.start_time,
            scene.end_time,
        )

        scene_params = dict(scene.parameters or {})
        scene_params["audio_clip_path"] = rel_clip_path
        scene.parameters = scene_params
        await session.commit()

        return {
            "audio_clip_path": rel_clip_path,
            "message": f"Sliced audio for scene {scene.order_index}: {scene.start_time:.2f}s–{scene.end_time:.2f}s",
        }
    except Exception as e:
        logger.error(f"Single scene audio slicing failed: {e}")
        raise HTTPException(status_code=500, detail=f"Audio slicing failed: {e}")
