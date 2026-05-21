"""Timeline and audio analysis endpoints for RBMN Storyboard App."""
import asyncio
import hashlib
import json
import logging
import os
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

    Each lyrics line defines an indivisible phrase. We walk through the
    Whisper words and assign them to lines by matching word counts per line.

    This handles common mismatches:
    - Whisper may contract words ("there's" vs "there" + "'s")
    - Whisper may split/join words slightly differently
    - Extra or missing words get absorbed into the nearest group

    The algorithm uses fuzzy sequential matching: for each lyrics line,
    find the best word-count match by comparing cleaned word text.
    """
    import re

    def clean_word(w: str) -> str:
        """Normalize a word for comparison."""
        return re.sub(r'[^a-zA-Z0-9]', '', w).lower()

    # Count words per lyrics line
    line_word_counts = []
    for line in lines:
        line_words = line.split()
        line_word_counts.append(len(line_words))

    # Build a flat list of cleaned Whisper words for matching
    whisper_cleaned = []
    for w in words:
        raw = (w.get("word", "") or w.get("value", "") or w.get("text", "")).strip()
        whisper_cleaned.append(clean_word(raw))

    # Sequential assignment: walk through words, assigning chunks to lines
    groups: list[list[dict]] = []
    word_idx = 0

    for line_idx, line in enumerate(lines):
        line_words_clean = [clean_word(w) for w in line.split()]
        expected_count = len(line_words_clean)

        if word_idx >= len(words):
            break

        # Try to find the start of this line in the Whisper words
        # Look for the first word of the line near our current position
        first_word = line_words_clean[0] if line_words_clean else ""

        # Check if we need to skip or if current position matches
        best_start = word_idx
        if first_word:
            # Look within a small window for the first word match
            search_end = min(word_idx + 5, len(words))
            for s in range(word_idx, search_end):
                if whisper_cleaned[s] == first_word:
                    best_start = s
                    break

        # Assign `expected_count` words from best_start
        # But cap at the start of the NEXT line's first word to avoid stealing
        group_end = best_start + expected_count

        # If this is not the last line, try to find where the next line starts
        # and cap our group there
        if line_idx + 1 < len(lines):
            next_line_words = [clean_word(w) for w in lines[line_idx + 1].split()]
            next_first = next_line_words[0] if next_line_words else ""
            if next_first:
                # Look for next line's first word starting from our expected end
                for s in range(max(best_start + 1, group_end - 2), min(group_end + 5, len(words))):
                    if whisper_cleaned[s] == next_first:
                        group_end = s  # Cap here
                        break

        group_end = min(group_end, len(words))

        if best_start < group_end:
            groups.append(words[best_start:group_end])
            word_idx = group_end
        else:
            # Edge case: no words match this line, skip it
            word_idx = best_start

    # Any remaining words go into the last group
    if word_idx < len(words):
        if groups:
            groups[-1].extend(words[word_idx:])
        else:
            groups.append(words[word_idx:])

    # Filter out empty groups
    groups = [g for g in groups if g]

    logger.info(
        f"Matched {len(words)} Whisper words to {len(lines)} lyrics lines → "
        f"{len(groups)} phrase groups"
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

    # ── Build lyrics + word timing text ──────────────────────────────────

    lyrics_block = ""
    if user_lyrics:
        lyrics_block += f"USER-PROVIDED LYRICS (use these for structural tags like [Verse 1], [Chorus], etc.):\n{user_lyrics}\n\n"

    if whisper_text and whisper_text != user_lyrics:
        lyrics_block += f"WHISPER-TRANSCRIBED LYRICS:\n{whisper_text}\n\n"

    # Word timestamps — critical for alignment
    if word_timestamps:
        # Group words into phrases using the same lyrics-line-aware function
        # that scene boundary snapping uses — ensures consistency
        phrases = _group_words_into_sentences(word_timestamps, lyrics_text=user_lyrics)

        # Build phrase-grouped timing display
        lyrics_block += "LYRICS GROUPED BY PHRASE (each line = one complete phrase that must NOT be split):\n"
        for phrase in phrases:
            first_start = phrase[0].get("start", 0)
            last_end = phrase[-1].get("end", 0)
            phrase_text = " ".join(
                (w.get("word", "") or w.get("value", "") or w.get("text", "")).strip()
                for w in phrase
            )
            lyrics_block += f"  [{first_start:.2f}s–{last_end:.2f}s] {phrase_text}\n"

        # Build explicit phrase break points — safe places to put scene boundaries
        lyrics_block += "\nPHRASE BREAK POINTS (safe times to place scene boundaries — ranked by quality):\n"
        scored_gaps = _build_phrase_gaps(word_timestamps, lyrics_text=user_lyrics)
        # Show top break points (up to 40, sorted by time for readability)
        top_gaps = sorted(scored_gaps[:40], key=lambda g: g["time"])
        for gap in top_gaps:
            quality = "SENTENCE END" if gap["score"] >= 10 else "VERSE BREAK" if gap["score"] >= 8 else "PHRASE BREAK" if gap["score"] >= 4 else "pause"
            lyrics_block += (
                f"  {gap['time']:.2f}s (gap={gap['gap_size']:.1f}s, {quality}, "
                f"after: \"{gap['word_before']}\")\n"
            )

        lyrics_block += "\n"

    if not lyrics_block:
        lyrics_block = "No lyrics available.\n"

    # ── Read video_max_duration from AppSettings ────────────────────────

    settings_stmt = select(AppSettings).where(AppSettings.id == 1)
    settings_result = await session.execute(settings_stmt)
    app_settings = settings_result.scalars().first()
    if not app_settings:
        raise HTTPException(status_code=400, detail="App settings not configured")
    max_dur = app_settings.video_max_duration or 15

    # ── LLM system prompt ────────────────────────────────────────────────

    system_prompt = (
        "You are a professional music video editor and timeline architect. "
        "Your job is to create an optimal scene breakdown for an AI-generated music video.\n\n"

        "CRITICAL CONSTRAINTS:\n"
        f"- Each scene will be rendered by an AI video model that has a MAXIMUM of {max_dur} seconds per generation.\n"
        f"- Ideal scene duration: {max(3, max_dur // 2)}-{max(5, max_dur - 3)} seconds. Minimum: 3 seconds. Absolute maximum: {max_dur} seconds.\n"
        "- Scenes must tile the entire duration with no gaps — every second must belong to a scene.\n"
        "- The first scene starts at 0.0 and the last scene ends at the total duration.\n\n"

        "PHRASE/SENTENCE INTEGRITY — THIS IS THE #1 PRIORITY:\n"
        "- NEVER split a phrase, sentence, or verse across two scenes. This is the single most important rule.\n"
        "- A scene MUST contain COMPLETE lyrical phrases. If a sentence starts in a scene, it must END in that scene.\n"
        "- Place scene boundaries ONLY at natural language breaks: end of a sentence (period, question mark, exclamation), "
        "end of a verse/chorus section, or a long pause (> 1 second) between phrases.\n"
        "- WRONG: Scene 1 ends with 'walking down the' → Scene 2 starts with 'street at night' (sentence split!)\n"
        "- RIGHT: Scene 1 ends with 'walking down the street at night.' → Scene 2 starts with 'The moon is shining...'\n"
        "- When the user provides PHRASE BREAK POINTS below, you MUST place scene boundaries at those points. "
        "These are pre-analyzed natural language boundaries where it's safe to cut.\n"
        "- If a phrase break point doesn't align perfectly with your ideal scene duration, "
        "it is ALWAYS better to have a slightly shorter or longer scene than to cut mid-sentence.\n\n"

        "SCENE BOUNDARY TIMING:\n"
        "- Scenes (except the very first) should START approximately 0.3 seconds BEFORE "
        "the first word of the new phrase begins. This gives the viewer a brief moment to register "
        "the new visual before the vocal comes in — a standard music video editing convention.\n"
        "- The first scene with vocals should start its boundary BEFORE the first sung word, not after it. "
        "For example, if the first word starts at 13.5s, the scene should start at ~13.0s.\n\n"

        "SECTION HANDLING:\n"
        "- Focus on MAIN structural sections: [Verse], [Pre-Chorus], [Chorus], [Bridge], [Outro], [Intro]. "
        "Ignore adlibs, background vocals, tags like [ad-lib], [Background], etc.\n"
        "- Long sections (e.g. a 30-second verse) MUST be split into 2-3 sub-scenes, but ONLY at "
        "phrase/sentence boundaries — never mid-sentence.\n"
        f"- Instrumental breaks, intros, and outros can be single scenes if they're under {max_dur} seconds, "
        "otherwise split them.\n\n"

        "WHEN NO LYRICS ARE AVAILABLE:\n"
        "- Use detected sections to determine scene boundaries.\n"
        f"- If sections are also missing, create evenly-spaced scenes of ~{min(10, max_dur - 2)}-{min(12, max_dur)} seconds each.\n"
        f"- Still respect the {max_dur}-second maximum.\n\n"

        "NAMING CONVENTION:\n"
        "- Name each scene descriptively based on the lyrical content or section: "
        "e.g. 'Verse 1 - Opening Lines', 'Chorus - Hook', 'Bridge - Breakdown', 'Outro - Fade'.\n"
        "- For sub-scenes within a section, append a part number: 'Verse 1 (Part 1)', 'Verse 1 (Part 2)'.\n\n"

        "RETURN FORMAT:\n"
        "Return ONLY a JSON array of objects, each with:\n"
        "  { \"name\": \"Scene Name\", \"start_time\": 0.0, \"end_time\": 10.5 }\n"
        "Ensure start_time of scene N+1 equals end_time of scene N (no gaps).\n"
        "Round times to 2 decimal places.\n"
        "No markdown, no explanation — just the JSON array."
    )

    # ── LLM user prompt ──────────────────────────────────────────────────

    user_prompt = (
        f"Total audio duration: {total_duration:.2f} seconds\n"
    )
    if song_title:
        user_prompt += f"Song Title: {song_title}\n"
    if concept_text:
        user_prompt += f"Video Concept: {concept_text}\n"
    if style_text:
        user_prompt += f"Visual Style: {style_text}\n"
    user_prompt += f"\n{sections_text}\n{lyrics_block}\n"
    user_prompt += "Generate an optimal scene timeline. Return ONLY the JSON array."

    # ── Call LLM ─────────────────────────────────────────────────────────

    provider, api_key, model = _get_llm_config(app_settings)

    try:
        raw_text = await asyncio.to_thread(
            _call_llm, provider, api_key, model, system_prompt, user_prompt,
            max_tokens=4000,  # Timeline generates scene defs — needs room for 30+ scenes
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
        logger.error(f"Failed to parse LLM response: {e}\nRaw: {raw_text[:500]}")
        raise HTTPException(status_code=500, detail="Failed to parse LLM scene response")

    # ── Validate and fix scene data ──────────────────────────────────────

    validated_scenes: list[dict[str, Any]] = []
    for i, sd in enumerate(scenes_data):
        name = sd.get("name", f"Scene {i + 1}")
        start = float(sd.get("start_time", 0))
        end = float(sd.get("end_time", start + 10))

        # Clamp to total duration
        start = max(0, min(start, total_duration))
        end = max(start + 1, min(end, total_duration))

        # Enforce max duration from settings
        if end - start > max_dur:
            end = start + max_dur

        validated_scenes.append({
            "name": name,
            "start_time": _snap_to_frame(start, project_fps),
            "end_time": _snap_to_frame(end, project_fps),
        })

    # Ensure contiguous coverage: fix any gaps between scenes
    for i in range(1, len(validated_scenes)):
        if validated_scenes[i]["start_time"] != validated_scenes[i - 1]["end_time"]:
            validated_scenes[i]["start_time"] = validated_scenes[i - 1]["end_time"]

    # Ensure first starts at 0 and last ends at total_duration
    if validated_scenes:
        validated_scenes[0]["start_time"] = 0.0
        validated_scenes[-1]["end_time"] = _snap_to_frame(total_duration, project_fps)

    # Re-enforce max duration after contiguity fixes.
    # The gap-closing and duration-clamping above can create scenes that
    # exceed max_dur (e.g. the last scene gets stretched to total_duration).
    # Split any oversized scenes at sentence boundaries within the scene.
    final_scenes: list[dict[str, Any]] = []
    for sd in validated_scenes:
        dur = sd["end_time"] - sd["start_time"]
        if dur > max_dur:
            import math
            n_splits = math.ceil(dur / max_dur)

            # Find sentence gaps within this scene's time range
            scene_gaps = []
            if word_timestamps:
                all_gaps = _build_phrase_gaps(word_timestamps, lyrics_text=user_lyrics)
                for gap in all_gaps:
                    if sd["start_time"] < gap["time"] < sd["end_time"]:
                        scene_gaps.append(gap)
                # Sort by time for ordered splitting
                scene_gaps.sort(key=lambda g: g["time"])

            if scene_gaps and len(scene_gaps) >= n_splits - 1:
                # Pick the best N-1 sentence gaps as split points
                # Sort by score, take top N-1, then re-sort by time
                best_gaps = sorted(scene_gaps, key=lambda g: g["score"], reverse=True)[:n_splits - 1]
                split_times = sorted([g["time"] for g in best_gaps])

                boundaries_list = [sd["start_time"]] + split_times + [sd["end_time"]]
                for j in range(len(boundaries_list) - 1):
                    sub_name = sd["name"] if n_splits == 1 else f"{sd['name']} (Part {j + 1})"
                    final_scenes.append({
                        "name": sub_name,
                        "start_time": _snap_to_frame(boundaries_list[j], project_fps),
                        "end_time": _snap_to_frame(boundaries_list[j + 1], project_fps),
                    })
            else:
                # Not enough sentence gaps — fall back to even splits
                sub_dur = dur / n_splits
                for j in range(n_splits):
                    sub_start = _snap_to_frame(sd["start_time"] + j * sub_dur, project_fps)
                    sub_end = _snap_to_frame(sd["start_time"] + (j + 1) * sub_dur, project_fps)
                    if j == n_splits - 1:
                        sub_end = sd["end_time"]
                    sub_name = sd["name"] if n_splits == 1 else f"{sd['name']} (Part {j + 1})"
                    final_scenes.append({
                        "name": sub_name,
                        "start_time": sub_start,
                        "end_time": sub_end,
                    })
            logger.info(
                f"Split oversized scene '{sd['name']}' ({dur:.1f}s) into {n_splits} sub-scenes"
            )
        else:
            final_scenes.append(sd)
    validated_scenes = final_scenes

    # ── Snap boundaries to word gaps ──────────────────────────────────
    # Even though the LLM was instructed to respect phrase boundaries,
    # it doesn't always get it right. Apply the same boundary-snapping
    # that create_scenes_from_sections uses.
    if word_timestamps and len(validated_scenes) > 1:
        boundaries = [validated_scenes[0]["start_time"]]
        for sd in validated_scenes:
            boundaries.append(sd["end_time"])
        boundaries = _snap_boundaries_to_word_gaps(
            boundaries, word_timestamps, fps=project_fps, lyrics_text=user_lyrics
        )
        for i, sd in enumerate(validated_scenes):
            sd["start_time"] = _snap_to_frame(boundaries[i], project_fps)
            sd["end_time"] = _snap_to_frame(boundaries[i + 1], project_fps)

    # ── Delete existing scenes and create new ones ───────────────────────

    existing_stmt = select(Scene).where(Scene.project_id == project_id)
    existing_result = await session.execute(existing_stmt)
    for old_scene in existing_result.scalars().all():
        await session.delete(old_scene)
    await session.flush()

    created_ids: list[str] = []
    for i, sd in enumerate(validated_scenes):
        scene = Scene(
            id=uuid4(),
            project_id=project_id,
            order_index=i,
            name=sd["name"],
            start_time=sd["start_time"],
            end_time=sd["end_time"],
            prompt="",
            negative_prompt="",
            parameters={},
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
