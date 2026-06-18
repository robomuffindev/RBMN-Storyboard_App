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
from backend.services.audio.analysis import AudioAnalyzer
from backend.database.models import (
    Project,
    ProjectMode,
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

        ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        filename = f"{ts}_{endpoint}.json"
        filepath = LLM_LOG_DIR / filename

        log_entry = {
            "timestamp": datetime.utcnow().isoformat(),
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
    block: Optional[int] = None  # SRT block index for preserving original subtitle grouping


class SrtBlock(BaseModel):
    """A single SRT subtitle line with its time range."""

    text: str
    start: float
    end: float


class LyricsResponse(BaseModel):
    """Response model for transcribed lyrics."""

    text: str
    words: list[WordTimestamp]
    srt_blocks: list[SrtBlock] = []  # Pre-built subtitle lines from SRT
    initial_text: str = ""  # User-provided lyrics/script input
    # Word-timing source — surfaces in the Audio-tab indicator chip so
    # the user can see at a glance whether the project's narration
    # timing is from an authoritative SRT (e.g. ElevenLabs export) or
    # from a probabilistic Whisper pass.  Empty string means no lyrics
    # row exists yet.  ``cue_count`` is the distinct SRT cue index
    # count when source == "srt" (0 otherwise), useful for the chip
    # tooltip ("12 cues from the SRT") and for the audit endpoint.
    source: str = ""  # "srt" | "whisper" | ""
    cue_count: int = 0


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


async def _maybe_resync_scene_boundaries(
    project_id: UUID,
    session: AsyncSession,
    trigger: str,
) -> dict:
    """Run the boundary audit and, if the source has drifted past the
    threshold, re-snap each existing scene's start/end to the natural
    break point of the new narration source.

    Called immediately after the narration source updates — Whisper
    re-analyze (``trigger="whisper_analyze"``) or SRT upload
    (``trigger="srt_upload"``).  For narration projects (`narration_video`
    / `narration_images`) only; music_video uses LLM-picked cuts and is
    left alone.

    Returns the resulting audit dict (with ``resynced`` boolean + count
    keys appended) so the caller can log + the calling endpoint can
    surface it to the frontend later.  Failure is non-fatal — the user
    can always run Suggest Timeline manually.
    """
    from backend.services.scene_boundaries import (
        audit_scene_boundaries,
        needs_auto_resync,
        closest_break,
        natural_break_points,
    )
    from backend.database.models import AppSettings as _AS

    # Mode gate — we only auto-resync narration projects.  Music video
    # uses LLM-picked cuts; the user expects creative control there.
    proj = await session.get(Project, project_id)
    if not proj:
        return {"resynced": False, "reason": "project_not_found"}
    if getattr(proj, "mode", None) not in ("narration_video", "narration_images"):
        return {"resynced": False, "reason": "mode_not_narration"}

    # ── Active auto-gen guard ─────────────────────────────────────────
    # If a sequential auto-gen run is in flight, do NOT mutate scene
    # boundaries — the run reads scene.end_time - scene.start_time fresh
    # on each iteration, so changing those values mid-run would leave
    # in-flight ComfyUI jobs carrying old durations and subsequent
    # submissions carrying new ones.  The user's project would end up
    # half-and-half (1.8.14 audit gap "I").  Resync gets deferred — the
    # user can re-upload the SRT or click *Suggest Timeline* manually
    # once the run finishes.
    try:
        from backend.api.generation import _seq_auto_jobs as _seq_jobs
        _pid_s = str(project_id)
        _state = _seq_jobs.get(_pid_s, {})
        _state_status = _state.get("status") if _state else None
        if _state_status == "running":
            logger.warning(
                f"_maybe_resync_scene_boundaries({trigger}, project={project_id}): "
                f"auto-gen is currently running — DEFERRING resync to avoid "
                f"mid-run timing inconsistency.  User can run Suggest Timeline "
                f"after the auto-gen run completes."
            )
            return {"resynced": False, "reason": "auto_gen_running"}
    except Exception as _gate_err:
        # The guard is best-effort.  If the import or state lookup
        # fails, we'd rather over-resync than silently skip.
        logger.debug(
            f"_maybe_resync_scene_boundaries gate-check failed: {_gate_err}"
        )

    # Pull bounds + scenes + words.
    _row = (await session.execute(select(_AS).where(_AS.id == 1))).scalars().first()
    _min = float(_row.video_min_duration if _row else 5.0) or 5.0
    _max = float(_row.video_max_duration if _row else 15.0) or 15.0

    scenes_stmt = (
        select(Scene).where(Scene.project_id == project_id).order_by(Scene.order_index)
    )
    scenes = list((await session.execute(scenes_stmt)).scalars().all())
    if not scenes:
        return {"resynced": False, "reason": "no_scenes"}

    lyrics_row = (
        await session.execute(select(Lyrics).where(Lyrics.project_id == project_id))
    ).scalars().first()
    words = list(lyrics_row.words or []) if lyrics_row else []
    if not words:
        return {"resynced": False, "reason": "no_words"}

    audit = audit_scene_boundaries(
        scenes=scenes, words=words, min_duration=_min, max_duration=_max,
    )
    if not needs_auto_resync(audit):
        logger.info(
            f"_maybe_resync_scene_boundaries({trigger}, project={project_id}): "
            f"audit OK — source={audit['source']!r}, stale_fraction="
            f"{audit['stale_fraction']:.2f} (threshold not exceeded, leaving "
            f"scenes alone)"
        )
        audit["resynced"] = False
        audit["reason"] = "audit_passes"
        return audit

    # Snap each scene's start/end to the closest natural break point.
    # Pad / trim afterwards through _clamp_scene_duration-equivalent
    # logic so the resync doesn't itself violate min/max.  Snap each
    # scene FIRST into a candidate (start, end) buffer, then sweep the
    # whole list to enforce monotonic neighbor ordering (scene[N].end ≤
    # scene[N+1].start).  Without the second pass, independent snapping
    # can produce overlapping ranges (scene N pads to start+15 while
    # scene N+1's start originally lived inside that pad) or accidental
    # gaps (scene N's end snaps earlier than scene N+1's start) — both
    # break downstream lyric slicing.  See 1.8.14 audit gap "D".
    breaks = natural_break_points(words)

    snapped: list[tuple[float, float, float, float]] = []  # (new_start, new_end, sdist, edist)
    for sc in scenes:
        new_start, sdist = closest_break(float(sc.start_time), breaks)
        new_end, edist = closest_break(float(sc.end_time), breaks)
        if new_end <= new_start:
            new_end = new_start + _min
        # Clamp span to min/max post-snap.
        span = new_end - new_start
        if span < _min:
            new_end = new_start + _min
        elif span > _max:
            new_end = new_start + _max
        snapped.append((new_start, new_end, sdist, edist))

    # ── Monotonic neighbor pass ──────────────────────────────────────
    # Walk left-to-right; if a scene's start dips below the previous
    # scene's end, push it up to that end.  If that pushes the span
    # below min_duration, extend the end correspondingly (but cap at
    # max_duration so we never re-introduce the gap-vs-overlap problem).
    for i in range(1, len(snapped)):
        prev_end = snapped[i - 1][1]
        new_start, new_end, sdist, edist = snapped[i]
        if new_start < prev_end:
            new_start = prev_end
            if new_end <= new_start:
                new_end = new_start + _min
            elif new_end - new_start < _min:
                new_end = new_start + _min
            elif new_end - new_start > _max:
                new_end = new_start + _max
            snapped[i] = (new_start, new_end, sdist, edist)
        # Also detect a gap larger than the snap tolerance: if the prior
        # scene ended noticeably earlier than this scene starts, pull
        # the prior scene's end forward so the timeline is contiguous
        # (matches how the assembler renders narration videos — silence
        # in the gap would be jarring).  Only fix gaps > 100ms.
        if new_start - prev_end > 0.1:
            p_start, _, p_sdist, p_edist = snapped[i - 1]
            new_prev_end = new_start
            # Re-clamp the previous scene's span too.
            if new_prev_end - p_start > _max:
                # Can't extend that far — leave the gap, will be a
                # silent freeze-frame in the export.
                pass
            elif new_prev_end - p_start < _min:
                # Already below min after the gap-fix; leave alone.
                pass
            else:
                snapped[i - 1] = (p_start, new_prev_end, p_sdist, p_edist)

    # ── Final-scene clamp to audio length ────────────────────────────
    # The monotonic-neighbor pass above can chain-shift scenes forward
    # when adjacent scenes snap to the same SRT cue.  Without a top
    # clamp the very last scene's end_time can drift past the actual
    # audio length, producing a clip whose slice would silently
    # truncate at EOF (and whose video would render as silence over
    # the final phantom seconds).  Compute the audio length once from
    # the words' last word.end (cheapest source — we already have the
    # words in scope), and clip the final snapped end to that value.
    if snapped:
        _last_word_end = 0.0
        try:
            _last_word_end = float(words[-1].get("end", 0.0) or 0.0)
        except Exception:
            _last_word_end = 0.0
        if _last_word_end > 0:
            _last_idx = len(snapped) - 1
            _ls, _le, _lsd, _led = snapped[_last_idx]
            if _le > _last_word_end + 0.05:
                _new_le = max(_ls + _min, _last_word_end)
                logger.info(
                    f"_resync: clamping final scene end_time "
                    f"{_le:.2f}->{_new_le:.2f} (audio ends at {_last_word_end:.2f}s)"
                )
                snapped[_last_idx] = (_ls, _new_le, _lsd, _led)

    # ── Tiny-tail absorption in resync output ──────────────────────
    # If the last scene's snapped span is below ``video_min_duration``
    # AND merging into the previous scene stays within
    # ``video_max_duration``, absorb it.  Same producer-agnostic
    # behavior the DP main / fallback paths now use.  This catches the
    # 1.78s tail user observed in 1.8.14 after re-running narration
    # analyze: the resync snapped scene N-1 forward to a cue boundary
    # and left scene N with only the trailing fragment of the audio.
    if len(snapped) >= 2:
        _last_idx = len(snapped) - 1
        _ls, _le, _lsd, _led = snapped[_last_idx]
        _last_span = _le - _ls
        if _last_span < _min - 1e-6:
            _ps, _pe, _psd, _ped = snapped[_last_idx - 1]
            _merged_span = _le - _ps
            if _merged_span <= _max + 1e-6:
                snapped[_last_idx - 1] = (_ps, _le, _psd, _ped)
                snapped.pop()
                logger.info(
                    f"_resync: absorbed {_last_span:.2f}s tail into previous "
                    f"scene → merged span {_merged_span:.2f}s"
                )
            else:
                logger.warning(
                    f"_resync: final scene span {_last_span:.2f}s is below "
                    f"video_min_duration={_min:.2f}s but cannot merge into "
                    f"previous ({_merged_span:.2f}s > video_max_duration="
                    f"{_max:.2f}s).  Leaving tiny tail in place — adjust "
                    f"manually if needed."
                )

    snapped_count = 0
    # ``scenes`` may have one more element than ``snapped`` after the
    # tail absorption above — pair only the survivors, then delete the
    # leftover scene row from the DB so the timeline shows the merged
    # span, not the old (start_old, end_old) ghost.
    _orphan_scenes: list = []
    if len(scenes) > len(snapped):
        _orphan_scenes = list(scenes[len(snapped):])
    for sc, (new_start, new_end, sdist, edist) in zip(scenes, snapped):
        if abs(new_start - float(sc.start_time)) > 1e-3 or abs(new_end - float(sc.end_time)) > 1e-3:
            logger.info(
                f"_resync scene {sc.order_index}: "
                f"{sc.start_time:.2f}->{new_start:.2f} | "
                f"{sc.end_time:.2f}->{new_end:.2f} "
                f"(start_drift={sdist:.2f}s, end_drift={edist:.2f}s)"
            )
            sc.start_time = new_start
            sc.end_time = new_end
            snapped_count += 1
    # Remove orphan scenes that the tail-absorption pruned out so the
    # final timeline matches the merged span, not the old N-scene shape.
    # The lingering scene's chapter_id is preserved on its absorbed
    # neighbor via the merge (we already extended that scene's
    # ``end_time``), so deleting the orphan is safe — no audio span is
    # lost.  CASCADE deletes its child rows (jobs / timeline_positions
    # / etc.) just like the manual scene-delete path.
    for _orphan in _orphan_scenes:
        logger.info(
            f"_resync: deleting orphan scene {_orphan.id} (order_index "
            f"{_orphan.order_index}) merged into previous"
        )
        await session.delete(_orphan)
    if _orphan_scenes:
        # Re-number ``order_index`` on the surviving scenes so the
        # sequence is contiguous (no gap left by the deleted orphan).
        for _new_idx, sc in enumerate(scenes[:len(snapped)]):
            if sc.order_index != _new_idx:
                sc.order_index = _new_idx
    await session.commit()
    logger.info(
        f"_maybe_resync_scene_boundaries({trigger}, project={project_id}): "
        f"snapped {snapped_count}/{len(scenes)} scenes to source={audit['source']!r} "
        f"break points"
    )
    audit["resynced"] = True
    audit["scenes_snapped"] = snapped_count
    audit["trigger"] = trigger
    return audit


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

    # Probe the actual audio duration up front so we can clamp scene
    # end_times that overshoot it.  This guards against the SRT-
    # preservation case: when a user re-records narration with shorter
    # audio but keeps the old SRT cues, scene.end_time can sit past
    # the new audio's EOF.  ffmpeg's `-ss S -t D` silently produces an
    # empty / truncated WAV in that case, which the LTX dispatcher and
    # the export assembler both happily ship — manifesting as a silent
    # tail in the final video.  Bounds-check + warn so the user sees
    # the mismatch in diag.md and the clip falls back to the audible
    # portion of the source.
    _audio_total_duration: Optional[float] = None
    try:
        from backend.services.video.ffmpeg import get_media_info
        _audio_total_duration = float(
            (get_media_info(str(audio_path)) or {}).get("duration") or 0.0
        )
        if _audio_total_duration <= 0:
            _audio_total_duration = None
    except Exception as _probe_err:
        logger.debug(
            f"Audio duration probe failed (non-fatal): {_probe_err!r}"
        )

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
    _truncated_scene_count = 0
    for scene in scenes:
        try:
            # Bounds check: clamp scene end_time to the audio's actual
            # length so ffmpeg can't be asked to slice past EOF.  Don't
            # mutate scene.end_time on the DB row — only the slice
            # parameters — so the timeline UI still shows what the user
            # intended (the SRT cue) and we don't silently rewrite their
            # timeline.  If the scene start ALSO falls past EOF, skip.
            _eff_end = float(scene.end_time)
            _eff_start = float(scene.start_time)
            if _audio_total_duration is not None:
                if _eff_start >= _audio_total_duration:
                    logger.warning(
                        f"Scene {scene.order_index}: start_time "
                        f"{_eff_start:.2f}s ≥ audio length "
                        f"{_audio_total_duration:.2f}s — skipping slice "
                        f"(scene timing exceeds new audio file)"
                    )
                    _truncated_scene_count += 1
                    continue
                if _eff_end > _audio_total_duration:
                    logger.warning(
                        f"Scene {scene.order_index}: end_time "
                        f"{_eff_end:.2f}s clamped to audio length "
                        f"{_audio_total_duration:.2f}s for slicing"
                    )
                    _eff_end = _audio_total_duration
                    _truncated_scene_count += 1

            clip_filename = f"scene_{scene.order_index:03d}_{_eff_start:.2f}_{_eff_end:.2f}.wav"
            clip_path = clips_dir / clip_filename
            rel_clip_path = str(clip_path.relative_to(settings.project_dir))

            await asyncio.to_thread(
                slice_audio,
                str(audio_path),
                str(clip_path),
                _eff_start,
                _eff_end,
            )

            # Store in scene parameters
            scene_params = dict(scene.parameters or {})
            scene_params["audio_clip_path"] = rel_clip_path
            scene.parameters = scene_params
            count += 1
        except Exception as e:
            logger.warning(f"Failed to slice audio for scene {scene.order_index}: {e}")

    if _truncated_scene_count > 0 and _audio_total_duration is not None:
        logger.warning(
            f"Audio slicing for project {project_id}: {_truncated_scene_count} "
            f"scene(s) had timing past audio EOF ({_audio_total_duration:.2f}s).  "
            f"This usually means the SRT cues + the current audio file disagree. "
            f"Re-upload the SRT or re-record the audio to match."
        )

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

        # Determine whether Demucs stem separation is worth running.
        # Narration modes upload pure-speech audio (e.g. ElevenLabs TTS)
        # with no music to separate — Demucs is wasted compute there and
        # can introduce phase artifacts in the "vocal" stem.
        _proj_for_mode = await session.get(Project, project_id)
        _skip_demucs = (
            _proj_for_mode is not None
            and _proj_for_mode.mode in (ProjectMode.NARRATION_IMAGES, ProjectMode.NARRATION_VIDEO)
        )

        # ── SRT-only narration mode ────────────────────────────────────
        # When the user has uploaded an SRT and set the project's
        # ``disable_whisper`` flag, skip Whisper entirely and use the
        # existing SRT cues as the sole narration timing.  Whisper's
        # output would otherwise overwrite the precise ElevenLabs cue
        # boundaries with probabilistic guesses that re-introduce drift.
        # The flag lives on ``Project.settings.disable_whisper`` so it's
        # per-project (different projects can be in different modes).
        # The Audio-tab UI gates this toggle on "SRT loaded" so the user
        # can't accidentally enable it for a Whisper-only project.
        _proj_settings_pre = (_proj_for_mode.settings if _proj_for_mode else {}) or {}
        _disable_whisper = bool(_proj_settings_pre.get("disable_whisper", False))
        _has_srt_already = False
        if _disable_whisper:
            # Verify SRT is actually loaded — refuse to skip Whisper if
            # there's no fallback source of word timing.
            _lyrics_pre_stmt = select(Lyrics).where(Lyrics.project_id == project_id)
            _lyrics_pre = (await session.execute(_lyrics_pre_stmt)).scalars().first()
            if _lyrics_pre:
                _pre_words = list(_lyrics_pre.words or [])
                _has_srt_already = any(
                    isinstance(_w, dict) and _w.get("block") is not None
                    for _w in _pre_words
                )
            if not _has_srt_already:
                logger.warning(
                    f"analyze_audio: project {project_id} has disable_whisper=True "
                    f"but no SRT-derived words on Lyrics row.  Refusing to skip "
                    f"Whisper — falling back to normal transcription."
                )
                _disable_whisper = False

        logger.info(
            f"Analyzing audio file {audio_asset.filename} for project "
            f"{project_id} (asset {audio_asset.id}, skip_demucs={_skip_demucs}, "
            f"disable_whisper={_disable_whisper})"
        )

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
        if _disable_whisper:
            # SRT-only mode: skip Whisper, run only sections + (skip_demucs)
            # stem prep so the rest of the pipeline gets the section data.
            # Reuse the existing SRT-derived words as the transcription
            # output — the SRT preservation block below recognizes them.
            logger.info(
                f"analyze_audio: project {project_id} skipping Whisper "
                f"(disable_whisper=True, SRT loaded with "
                f"{len(_pre_words)} words)"
            )
            analysis_result = await asyncio.to_thread(
                analyzer.analyze_full,
                str(audio_path),
                "skip",  # whisper_mode='skip' tells analyze_full not to transcribe
                whisper_remote_url,
                whisper_comfyui_url=whisper_comfyui_url,
                initial_text=initial_text,
                whisper_model=whisper_model,
                whisper_language=whisper_language,
                skip_demucs=_skip_demucs,
            )
            # Override the transcription field with SRT words so downstream
            # code finds them on Lyrics.words.  The analyze_full with
            # whisper_mode='skip' returns empty words; we substitute SRT.
            analysis_result["transcription"] = list(_pre_words)
            analysis_result["lyrics"] = {
                "text": " ".join(
                    str(_w.get("word", "")).strip()
                    for _w in _pre_words
                    if isinstance(_w, dict) and _w.get("word")
                ).strip(),
                "words": list(_pre_words),
            }
        else:
            analysis_result = await asyncio.to_thread(
                analyzer.analyze_full,
                str(audio_path),
                whisper_mode,
                whisper_remote_url,
                whisper_comfyui_url=whisper_comfyui_url,
                initial_text=initial_text,
                whisper_model=whisper_model,
                whisper_language=whisper_language,
                skip_demucs=_skip_demucs,
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
            # Upsert lyrics — delete existing first.
            #
            # SRT-preservation guard: if the previously-saved Lyrics row
            # was derived from an SRT upload (any word carries a ``block``
            # index), the cue boundaries it encodes are authoritative —
            # ElevenLabs / the narrator's own production pipeline wrote
            # them.  Whisper's re-transcription is statistically softer
            # and would overwrite that precision.  In that case we keep
            # the SRT words but refresh ``full_text`` / ``initial_text``
            # so the lyrics panel still picks up any user-side edits.
            existing_lyrics_stmt = select(Lyrics).where(Lyrics.project_id == project_id)
            existing_lyrics_result = await session.execute(existing_lyrics_stmt)
            existing_lyrics = existing_lyrics_result.scalars().first()

            _srt_words_existing: list[dict] = []
            if existing_lyrics:
                try:
                    _existing_words = list(existing_lyrics.words or [])
                    if any(
                        isinstance(_w, dict) and _w.get("block") is not None
                        for _w in _existing_words
                    ):
                        _srt_words_existing = _existing_words
                except Exception:
                    _srt_words_existing = []

            if existing_lyrics:
                await session.delete(existing_lyrics)
                await session.flush()

            if _srt_words_existing:
                # RE-ANCHOR: combine the SRT's TEXT + cue (block) grouping with
                # Whisper's audio-accurate TIMING.  ElevenLabs/SRT timestamps
                # drift from the rendered audio over long files, but the SRT's
                # wording is correct (Whisper garbles spellings).  So we keep
                # the SRT words + blocks and transfer the fresh Whisper timing
                # onto them.  (When Whisper was skipped — disable_whisper —
                # ``transcription_words`` equals the SRT words, so re-timing is
                # a harmless no-op.)  This is the place the re-anchor belongs:
                # Whisper has just run here, so SRT upload itself stays instant.
                _words_to_save = _srt_words_existing
                try:
                    if (
                        transcription_words
                        and transcription_words is not _srt_words_existing
                    ):
                        from backend.services.audio.text_align import (
                            retime_srt_words_to_audio as _retime,
                        )
                        _retimed = _retime(_srt_words_existing, transcription_words)
                        if _retimed and len(_retimed) == len(_srt_words_existing):
                            _words_to_save = _retimed
                            logger.info(
                                f"Process Audio: re-anchored {len(_srt_words_existing)} "
                                f"SRT words to Whisper audio timing (SRT spelling + "
                                f"cue blocks preserved)."
                            )
                        else:
                            logger.warning(
                                "Process Audio: SRT re-anchor length mismatch — "
                                "keeping SRT timings."
                            )
                    else:
                        logger.info(
                            f"Re-analyzing project {project_id}: no fresh Whisper "
                            f"timing to re-anchor against — keeping "
                            f"{len(_srt_words_existing)} SRT-cue words as-is."
                        )
                except Exception as _re_err:
                    logger.warning(
                        f"Process Audio: SRT re-anchor failed "
                        f"({type(_re_err).__name__}: {_re_err}) — keeping SRT timings."
                    )
                # Keep full_text in lock-step with the words we're saving.
                _srt_full_text = " ".join(
                    str(_w.get("word", "")).strip()
                    for _w in _words_to_save
                    if isinstance(_w, dict) and _w.get("word")
                ).strip()
                _full_text_to_save = _srt_full_text or lyrics_text
            else:
                _words_to_save = transcription_words
                _full_text_to_save = lyrics_text

            lyrics_record = Lyrics(
                project_id=project_id,
                full_text=_full_text_to_save,
                initial_text=initial_text or "",
                words=_words_to_save,
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

        # Auto-resync existing scene boundaries to the freshly-analyzed
        # narration timing when staleness exceeds threshold (narration
        # projects only — music_video uses LLM-picked cuts so we leave
        # those alone unless the user explicitly re-runs Suggest Timeline).
        try:
            await _maybe_resync_scene_boundaries(
                project_id, session, trigger="whisper_analyze",
            )
        except Exception as _resync_err:
            logger.warning(
                f"Post-Whisper auto-resync failed (non-fatal) for "
                f"project {project_id}: {_resync_err}"
            )

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

        def _build_srt_blocks(raw_words: list[dict]) -> list[SrtBlock]:
            """Build pre-grouped subtitle lines from words that have block indices."""
            blocks_map: dict[int, list[dict]] = {}
            for w in raw_words:
                b = w.get("block")
                if b is None:
                    continue
                blocks_map.setdefault(b, []).append(w)
            if not blocks_map:
                return []
            result = []
            for block_idx in sorted(blocks_map.keys()):
                bw = blocks_map[block_idx]
                text = " ".join(w.get("word", "") for w in bw)
                start = min(w.get("start", 0.0) for w in bw)
                end = max(w.get("end", 0.0) for w in bw)
                if text.strip():
                    result.append(SrtBlock(text=text.strip(), start=start, end=end))
            return result

        if lyrics_record:
            raw_words = lyrics_record.words or []
            blocks_count = sum(1 for w in raw_words if w.get("block") is not None)
            logger.debug(
                f"get_lyrics({project_id}): {len(raw_words)} words from DB, "
                f"{blocks_count} with block field"
            )
            if raw_words:
                logger.debug(f"  First word keys: {list(raw_words[0].keys())}")
            words = [
                WordTimestamp(
                    word=w.get("word", ""),
                    start_time=w.get("start", 0.0),
                    end_time=w.get("end", 0.0),
                    block=w.get("block"),
                )
                for w in raw_words
            ]
            srt_blocks = _build_srt_blocks(raw_words)
            logger.debug(
                f"get_lyrics({project_id}): built {len(srt_blocks)} srt_blocks"
            )
            if srt_blocks:
                logger.debug(
                    f"  First block: \"{srt_blocks[0].text}\" "
                    f"[{srt_blocks[0].start:.3f}-{srt_blocks[0].end:.3f}]"
                )
            initial = getattr(lyrics_record, "initial_text", "") or ""
            # Source-label the response so the frontend's Audio-tab chip
            # can show "SRT loaded — N cues" vs "Whisper transcription".
            try:
                from backend.services.scene_boundaries import (
                    source_label as _src_label,
                    cue_ranges as _cue_ranges,
                )
                _src = _src_label(raw_words)
                _cues = len(_cue_ranges(raw_words)) if _src == "srt" else 0
            except Exception:
                _src, _cues = "", 0
            return LyricsResponse(
                text=lyrics_record.full_text, words=words,
                srt_blocks=srt_blocks, initial_text=initial,
                source=_src, cue_count=_cues,
            )

        # Fall back to cached lyrics JSON file
        project_path = settings.project_dir / str(project_id)
        lyrics_cache_path = project_path / "cache" / "lyrics.json"

        if lyrics_cache_path.exists():
            with open(lyrics_cache_path, "r") as f:
                lyrics_data = json.load(f)
                raw_words = lyrics_data.get("words", [])
                words = [
                    WordTimestamp(
                        word=w.get("word", ""),
                        start_time=w.get("start", 0.0),
                        end_time=w.get("end", 0.0),
                        block=w.get("block"),
                    )
                    for w in raw_words
                ]
                srt_blocks = _build_srt_blocks(raw_words)
                try:
                    from backend.services.scene_boundaries import (
                        source_label as _src_label,
                        cue_ranges as _cue_ranges,
                    )
                    _src = _src_label(raw_words)
                    _cues = len(_cue_ranges(raw_words)) if _src == "srt" else 0
                except Exception:
                    _src, _cues = "", 0
                return LyricsResponse(
                    text=lyrics_data.get("text", ""), words=words,
                    srt_blocks=srt_blocks, source=_src, cue_count=_cues,
                )

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


class SaveInitialTextRequest(BaseModel):
    """Request body for saving user-provided lyrics/script text."""
    initial_text: str = ""


@router.put(
    "/lyrics/text",
    response_model=LyricsResponse,
    summary="Save user-provided lyrics/script text",
)
async def save_lyrics_text(
    project_id: UUID,
    body: SaveInitialTextRequest,
    session: AsyncSession = Depends(get_session),
) -> LyricsResponse:
    """Save (or create) the user-provided lyrics/script text.

    This endpoint is called by the frontend auto-save mechanism when the user
    types or pastes into the script/lyrics textarea. It preserves newlines and
    paragraph structure exactly as provided.
    """
    try:
        await _get_project_or_404(project_id, session)

        lyrics_stmt = select(Lyrics).where(Lyrics.project_id == project_id)
        lyrics_result = await session.execute(lyrics_stmt)
        lyrics_record = lyrics_result.scalars().first()

        if lyrics_record:
            lyrics_record.initial_text = body.initial_text
        else:
            # Create a new lyrics record with just the initial_text
            lyrics_record = Lyrics(
                project_id=project_id,
                full_text="",
                initial_text=body.initial_text,
                words=[],
                language="en",
            )
            session.add(lyrics_record)

        await session.commit()
        await session.refresh(lyrics_record)

        words = [
            WordTimestamp(
                word=w.get("word", ""),
                start_time=w.get("start", 0.0),
                end_time=w.get("end", 0.0),
            )
            for w in (lyrics_record.words or [])
        ]
        return LyricsResponse(
            text=lyrics_record.full_text,
            words=words,
            initial_text=lyrics_record.initial_text or "",
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error saving lyrics text for project {project_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to save lyrics text",
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

    # ── SRT CUES ARE AUTHORITATIVE (highest priority) ───────────────────
    # When the words carry SRT ``block`` indices (uploaded SRT / Eleven-
    # Labs export), the cue grouping IS the ground-truth phrase map and
    # every word already carries an EXACT start/end from the SRT.  Group
    # one phrase per cue and return immediately — we must NOT fall through
    # to the fuzzy ``_match_words_to_lyrics_lines`` anchor matcher, whose
    # monotonic interpolation drifts cumulatively down a long script and
    # produces the "scenes start well then cut earlier and earlier" bug.
    # With the SRT present, scene boundaries become an exact lookup rather
    # than an estimate.  (Whisper-only projects have no ``block`` and fall
    # through to the lyrics/punctuation logic below, unchanged.)
    if any(isinstance(w, dict) and w.get("block") is not None for w in words):
        by_block: dict[int, list[dict]] = {}
        order: list[int] = []
        for w in words:
            b = w.get("block")
            if b is None:
                # A stray word with no block (rare mid-SRT gap) attaches to
                # the most recently seen cue so no audio is dropped.
                if order:
                    by_block[order[-1]].append(w)
                continue
            b = int(b)
            if b not in by_block:
                by_block[b] = []
                order.append(b)
            by_block[b].append(w)
        groups = [by_block[b] for b in sorted(by_block.keys()) if by_block[b]]
        logger.info(
            f"[PhraseGroups] SRT source detected — grouped {len(words)} words "
            f"into {len(groups)} cue-phrases (block-authoritative; fuzzy lyrics "
            f"matcher bypassed so cue times map 1:1 to scene boundaries)"
        )
        if groups:
            return groups

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

        # Mix stems by summing (np.mean halves volume with 2 stems)
        # then peak-normalize to prevent clipping
        mixed = np.sum(stems_to_mix, axis=0)
        peak = np.max(np.abs(mixed))
        if peak > 0:
            mixed = mixed / peak * 0.95  # Leave 5% headroom

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


# ═══════════════════════════════════════════════════════════════════════
# NARRATION DP SEGMENTATION
# ═══════════════════════════════════════════════════════════════════════
# For narration projects, scene boundaries can be computed mathematically
# using dynamic programming — no LLM needed.  This is essentially the
# Knuth-Plass line-breaking algorithm applied to time segmentation.
#
# Inputs:  phrase boundaries with timestamps, min/max scene duration
# Output:  optimal partition into scenes that respects constraints and
#          prefers natural break points (long pauses, paragraph gaps)
# ═══════════════════════════════════════════════════════════════════════

def _absorb_tiny_tail(
    scenes: list[dict],
    min_dur: float,
    max_dur: float,
    *,
    where: str = "",
) -> list[dict]:
    """Merge a final scene whose span is below ``min_dur`` into the
    previous scene when that merge stays within ``max_dur``.

    Why it exists: multiple scene-producing paths (DP main backtracking,
    DP fallback, LLM ``_split_oversized``, post-Whisper / post-SRT
    resync) can each land the timeline with a 1–3 s tail that violates
    the project's ``video_min_duration`` setting.  LTX 2.3 can't
    meaningfully render a 1.78 s scene, so we'd rather extend the
    previous scene to cover that audio than ship a sub-minimum scene
    and rely on downstream clamps to bail us out.

    Returns the same list (mutated when an absorb happens) for chaining.
    Logs every absorb so the operator can see why the timeline grew a
    little at the tail.  Inverse case ("can't merge, span would exceed
    ``max_dur``") is left alone — that's a real edge case the user
    should see in the audit so they can adjust manually.
    """
    if len(scenes) < 2:
        return scenes
    last = scenes[-1]
    last_span = float(last.get("end_time", 0.0)) - float(last.get("start_time", 0.0))
    if last_span >= min_dur - 1e-6:
        return scenes
    prev = scenes[-2]
    new_prev_span = float(last.get("end_time", 0.0)) - float(prev.get("start_time", 0.0))
    if new_prev_span > max_dur + 1e-6:
        logger.warning(
            f"_absorb_tiny_tail({where}): final scene {last_span:.2f}s is below "
            f"video_min_duration={min_dur:.2f}s but cannot be merged into the "
            f"previous scene ({new_prev_span:.2f}s > video_max_duration={max_dur:.2f}s).  "
            f"Leaving tiny tail in place — user should tweak boundaries manually."
        )
        return scenes
    prev["end_time"] = last["end_time"]
    scenes.pop()
    logger.info(
        f"_absorb_tiny_tail({where}): absorbed {last_span:.2f}s tail into "
        f"previous scene → new span {new_prev_span:.2f}s"
    )
    return scenes


def _dp_segment_narration(
    phrase_groups: list[list[dict]],
    total_duration: float,
    min_dur: float,
    max_dur: float,
    user_lyrics: str = "",
) -> list[dict]:
    """Compute optimal scene boundaries for narration using dynamic programming.

    Each phrase_group is an indivisible unit (one sentence/line from the script).
    The DP finds the partition into scenes that minimizes a cost function
    balancing: duration targets, pause-awareness, and evenness.

    Returns list of dicts: [{name, start_time, end_time, lyrics}, ...]
    """
    if not phrase_groups:
        return [{"name": "Scene 1", "start_time": 0.0, "end_time": total_duration}]

    # ── Pre-split overlong phrase groups ────────────────────────────────
    # A single phrase whose span (first word start → last word end) exceeds
    # ``max_dur`` is unsplitable by the DP (the inner loop's
    # ``if dur > max_dur: break`` rejects it and the whole DP falls back
    # to a lossy natural-break path).  More importantly, even when the
    # fallback runs, clamping to pos+max_dur strands the phrase's tail
    # words inside the NEXT scene's visual window — that's the "scenes
    # cut early while the previous rhyme is still being spoken" symptom.
    #
    # The fix is to split any overlong phrase here, BEFORE the DP, at
    # the largest internal inter-word gap.  Each resulting sub-phrase is
    # then short enough to participate in the DP normally and every word
    # stays inside a scene boundary.  Repeat until every group fits.
    _safe_groups: list[list[dict]] = []
    _split_count = 0
    for _pg in phrase_groups:
        if not _pg:
            _safe_groups.append(_pg)
            continue
        _pending = [_pg]
        while _pending:
            _cur = _pending.pop(0)
            if not _cur or len(_cur) < 2:
                _safe_groups.append(_cur)
                continue
            _span = float(_cur[-1].get("end", 0.0)) - float(_cur[0].get("start", 0.0))
            if _span <= max_dur or len(_cur) < 2:
                _safe_groups.append(_cur)
                continue
            # Find the largest inter-word gap and split there.  This
            # preserves natural phrasing — the gap is where the speaker
            # took a breath or pause, which is the best place to cut.
            _best_gap = -1.0
            _best_idx = -1
            for _wi in range(1, len(_cur)):
                _gap = float(_cur[_wi].get("start", 0.0)) - float(_cur[_wi - 1].get("end", 0.0))
                if _gap > _best_gap:
                    _best_gap = _gap
                    _best_idx = _wi
            # Defensive fallback: if no gaps exist (degenerate), split at midpoint
            if _best_idx <= 0:
                _best_idx = max(1, len(_cur) // 2)
            _left = _cur[:_best_idx]
            _right = _cur[_best_idx:]
            _pending.insert(0, _right)
            _pending.insert(0, _left)
            _split_count += 1
    if _split_count > 0:
        logger.warning(
            f"[NarrationDP] Pre-split {_split_count} overlong phrase group(s) "
            f"(span > max_dur={max_dur:.1f}s) to keep words aligned with scene "
            f"boundaries.  Original {len(phrase_groups)} groups → {len(_safe_groups)} "
            f"after splitting.  Each split picks the largest internal inter-word "
            f"gap so the cut lands at a natural breath/pause."
        )
        phrase_groups = _safe_groups

    n = len(phrase_groups)

    # ── Build boundary times and gap sizes ──────────────────────────────
    # boundary[i] = the time BEFORE phrase_group[i] starts
    # boundary[n] = total_duration (end of last phrase or total audio)
    boundaries: list[float] = []
    gaps: list[float] = []  # gap[i] = silence gap before phrase i

    for i, pg in enumerate(phrase_groups):
        if not pg:
            boundaries.append(boundaries[-1] if boundaries else 0.0)
            gaps.append(0.0)
            continue
        start = float(pg[0].get("start", 0.0) or 0.0)
        if i == 0:
            # Scene 1 OWNS the intro silence.  A long lead-in pause is not
            # attached to two scenes, so it is never split — the first
            # scene always starts at 0.0 and covers everything up to the
            # first cut (per Lorenzo's spec: intro/outro pauses may be long
            # on purpose and belong to a single scene).
            boundaries.append(0.0)
            gaps.append(start)  # gap from 0 to first word (intro length)
        else:
            # Robust phrase end: the LAST word's recorded ``end`` can under-
            # shoot the true spoken end (Whisper) or be slightly out of
            # order, so take the MAX end across the whole phrase.  This
            # guarantees the boundary sits AFTER every spoken word in the
            # phrase, so the scene's audio slot fully contains its dialogue
            # and nothing bleeds past the cut.
            prev_pg = phrase_groups[i - 1]
            prev_end = (
                max((float(w.get("end", 0.0) or 0.0) for w in prev_pg), default=0.0)
                if prev_pg else 0.0
            )
            gap = max(0.0, start - prev_end)
            # SPLIT THE INTER-PHRASE PAUSE EVENLY between the two scenes:
            # half the silence tails the current scene, half leads into the
            # next (a 1.0s pause → 0.5s each side).  The midpoint also
            # guarantees boundary > prev_end, which is what eliminates the
            # "previous scene's dialogue is still playing after the cut"
            # symptom.
            boundaries.append(round((prev_end + start) / 2.0, 3))
            gaps.append(gap)

    # Final boundary = end of audio.  The trailing/outro silence belongs to
    # the LAST scene only and is never split — mirrors the intro rule above.
    boundaries.append(round(total_duration, 3))

    # ── Detect paragraph breaks from user lyrics ────────────────────────
    # A blank line in the script = paragraph break → strong preference for cut
    paragraph_breaks: set[int] = set()
    if user_lyrics:
        lyrics_lines = user_lyrics.splitlines()
        phrase_idx = 0
        for li, line in enumerate(lyrics_lines):
            if not line.strip():
                # Blank line → the next non-blank line starts a new paragraph
                paragraph_breaks.add(phrase_idx)
            elif phrase_idx < n:
                phrase_idx += 1

    # ── Phase 1: detect MAJOR silences (adaptive structural anchors) ─────
    # Lorenzo's two-phase model: the long pauses are where a narrator
    # finishes a thought, so they should be near-inviolable scene cuts;
    # the even-sizing DP then fills the chunks between them.  "Major" is
    # measured RELATIVE to this narration's own phrasing (adaptive) so it
    # works for tight SRT cues (~1s gaps) and loose Whisper timing alike:
    # a gap is major when it's well above the typical inter-phrase gap.
    # ``major_set`` holds phrase indices i whose PRECEDING gap (gaps[i]) is
    # a structural break — the DP is penalised for letting a scene span one.
    major_set: set[int] = set()
    _interior_gaps = [gaps[k] for k in range(1, n)]
    if _interior_gaps:
        _sorted_g = sorted(_interior_gaps)
        _median_g = _sorted_g[len(_sorted_g) // 2]
        # Threshold: 3x the median pause, never below a 1.5s floor.  Any gap
        # at/above this is a structural break.
        _major_thresh = max(1.5, _median_g * 3.0)
        for k in range(1, n):
            if gaps[k] >= _major_thresh:
                major_set.add(k)
        if major_set:
            logger.info(
                f"[NarrationDP] Phase 1: {len(major_set)} major silence(s) "
                f"(gap >= {_major_thresh:.2f}s; median pause {_median_g:.2f}s) "
                f"will anchor scene cuts."
            )

    # ── Ideal duration for even scene sizing ────────────────────────────
    ideal_dur = (min_dur + max_dur) / 2.0

    # ── DP: dp[i] = min cost to segment phrases [0..i) ─────────────────
    # We try every valid split: scene from phrase j to phrase i
    # where the duration boundaries[i] - boundaries[j] is in [min_dur, max_dur]
    INF = float("inf")
    dp = [INF] * (n + 1)
    dp[0] = 0.0
    parent = [-1] * (n + 1)  # for backtracking

    for i in range(1, n + 1):
        for j in range(i - 1, -1, -1):
            dur = boundaries[i] - boundaries[j]
            if dur < min_dur and i < n:
                # Too short — keep extending (unless it's the last possible scene)
                continue
            if dur > max_dur:
                # Too long — no point going further back
                break

            # Cost function:
            # 1) Duration deviation from ideal (quadratic for stronger penalty)
            cost = ((dur - ideal_dur) / ideal_dur) ** 2

            # 2) Pause bonus: prefer cuts at long pauses (natural break points)
            #    Gap at boundary j = gap before phrase j
            if j > 0 and j < n:
                gap_at_cut = gaps[j]
                # Longer pauses → lower cost (bonus up to -0.5 for 2s+ gaps)
                pause_bonus = min(gap_at_cut / 4.0, 0.5)
                cost -= pause_bonus

            # 3) Paragraph break bonus: strongly prefer cutting at paragraph starts
            if j in paragraph_breaks:
                cost -= 1.0  # strong bonus

            # 4) MAJOR-SILENCE anchor: heavily penalise a scene that would
            #    swallow a structural pause (one lying strictly inside the
            #    span j<k<i).  The penalty (50/silence) dwarfs every normal
            #    cost term so cuts land ON the major silences — but it stays
            #    finite, so the DP never fails when honouring an anchor would
            #    violate min/max (it then accepts the least-bad span rather
            #    than collapsing to the fixed-slice fallback).
            if major_set:
                _spanned = sum(1 for _k in major_set if j < _k < i)
                if _spanned:
                    cost += 50.0 * _spanned

            total = dp[j] + cost
            if total < dp[i]:
                dp[i] = total
                parent[i] = j

    # ── Backtrack to find optimal boundaries ────────────────────────────
    if dp[n] == INF:
        # Fallback: no valid partition found (constraints too tight).
        # Previous behavior was to chop the audio into fixed-length
        # ``ideal_dur`` slices (5+15)/2 = 10.0s — exactly the "everything
        # is 10 seconds" symptom users hit when Whisper merged a long
        # narration into a single phrase or one phrase exceeded
        # ``max_dur`` alone.  See 1.8.14 audit: that produced
        # ``scene_000_0.00_10.00`` / ``scene_001_10.00_20.00`` /... slices
        # with zero alignment to the actual narration.
        #
        # The smarter fallback below builds the same evenly-spaced target
        # positions but SNAPS each one to the closest natural break
        # (SRT cue boundary when the source is SRT, or a >300 ms inter-
        # word gap when the source is Whisper) using the same helper the
        # boundary audit uses.  Scenes still respect ``min_dur``/``max_dur``
        # in expectation, but cut points actually align with the audio
        # — which is what the user expected from "re-split scenes."
        logger.warning(
            f"[NarrationDP] No valid DP solution found with min={min_dur}, "
            f"max={max_dur} ({n} phrases) — falling back to natural-break "
            f"snapping (was previously: fixed {ideal_dur:.1f}s slices)."
        )

        # Flatten phrase_groups back into a plain word list so the
        # boundary helper can work — it expects the same shape as
        # ``Lyrics.words`` (list of dicts with ``start``/``end``/``word``
        # and optional ``block``).
        _flat_words: list[dict] = []
        for pg in phrase_groups:
            for _w in pg:
                _flat_words.append(_w)

        try:
            from backend.services.scene_boundaries import (
                natural_break_points,
                closest_break,
                source_label,
            )
            _breaks = natural_break_points(_flat_words)
            _source = source_label(_flat_words)
        except Exception as _import_err:
            logger.warning(
                f"[NarrationDP] Could not import boundary helper "
                f"({_import_err!r}) — using raw fixed-slice fallback"
            )
            _breaks = []
            _source = ""

        scenes: list[dict] = []
        if _breaks and len(_breaks) >= 2:
            # Build candidate ideal positions, then snap each to the
            # nearest break.  We accept the snap unless it would create
            # a zero-length or below-min span, in which case we step to
            # the next break > current scene start + min_dur.
            pos = 0.0
            scene_num = 1
            _exhausted_break_count = 0
            while pos < total_duration - 0.5:
                target = min(pos + ideal_dur, total_duration)
                # Snap target to nearest break.
                snapped, _dist = closest_break(target, _breaks)
                # Reject snaps that produce too-short spans — step
                # forward to the smallest break strictly greater than
                # pos + min_dur.
                if snapped <= pos + min_dur * 0.5:
                    larger = [b for b in _breaks if b >= pos + min_dur]
                    if larger:
                        snapped = larger[0]
                    else:
                        # Natural breaks exhausted for the remainder of
                        # the audio — the user has no more cue/phrase
                        # boundaries to snap to.  Surface this as a
                        # WARNING (not the silent INFO the original code
                        # would have produced) so the tail's uniform
                        # slicing is visible in diag.md.  This usually
                        # means the SRT had cues only for the front of
                        # the audio, or Whisper's phrase grouping
                        # bunched everything into the start.
                        _exhausted_break_count += 1
                        snapped = min(pos + ideal_dur, total_duration)
                # Clamp the resulting span to min/max.
                if snapped - pos < min_dur:
                    snapped = min(pos + min_dur, total_duration)
                elif snapped - pos > max_dur:
                    # PHRASE-RESPECTING CLAMP — previously this branch
                    # silently set ``snapped = pos + max_dur`` regardless
                    # of where the narration's words actually ended.  That
                    # produced the "scenes cut early while the previous
                    # rhyme is still being spoken" bug: the master audio
                    # plays continuously across the assembled scenes, so
                    # if a scene's visual ends at pos+max_dur but the
                    # phrase's last word lives at pos+(max_dur+2), those
                    # 2s of speech overflow into the NEXT scene's visual
                    # window.  The fix is to snap the clamp DOWN to the
                    # last natural break (SRT cue end or Whisper word gap)
                    # that fits in [pos+min_dur, pos+max_dur].  When no
                    # break fits — meaning the user has a single phrase
                    # that's genuinely longer than max_dur — log a loud
                    # WARNING and accept the flat clamp as a last resort.
                    _flat_clamp = pos + max_dur
                    _eligible = [
                        b for b in _breaks
                        if (pos + min_dur) <= b <= _flat_clamp
                    ]
                    if _eligible:
                        snapped = _eligible[-1]
                    else:
                        logger.warning(
                            f"[NarrationDP] No natural break in [{pos + min_dur:.1f}s, "
                            f"{_flat_clamp:.1f}s] at pos={pos:.1f}s — falling back to "
                            f"flat clamp at {_flat_clamp:.1f}s.  This means the next "
                            f"scene's visuals will start while the previous scene's "
                            f"words may still be playing.  Raise video_max_duration in "
                            f"Settings or split the phrase to fix."
                        )
                        snapped = _flat_clamp
                scenes.append({
                    "name": f"Scene {scene_num}",
                    "start_time": round(pos, 2),
                    "end_time": round(snapped, 2),
                })
                pos = snapped
                scene_num += 1
                # Safety: if we somehow didn't advance, bail to prevent
                # an infinite loop (shouldn't happen given the min_dur
                # clamp, but defense in depth).
                if scenes[-1]["end_time"] <= scenes[-1]["start_time"] + 1e-3:
                    break

            # Tiny tail absorption — same rationale as the DP main
            # path.  Routed through the shared helper so logging and
            # behavior stay in lock-step across producers.
            scenes = _absorb_tiny_tail(
                scenes, min_dur=min_dur, max_dur=max_dur,
                where="DP fallback",
            )

            if _exhausted_break_count > 0:
                logger.warning(
                    f"[NarrationDP] Natural-break fallback exhausted candidate "
                    f"breaks {_exhausted_break_count} time(s) — the tail of "
                    f"the audio fell back to uniform {ideal_dur:.1f}s slices "
                    f"(source={_source!r}).  Upload an SRT covering the full "
                    f"narration to get phrase-aligned scenes throughout."
                )
            logger.info(
                f"[NarrationDP] Natural-break fallback produced {len(scenes)} "
                f"scenes (source={_source!r}, {len(_breaks)} candidate breaks, "
                f"{_exhausted_break_count} uniform-tail steps)"
            )
            return scenes

        # Worst-case path (no usable breaks at all): keep the original
        # fixed-slice behavior so we don't regress to "zero scenes."
        # Logged as ERROR so it's visible in the user's diag.md when it
        # actually happens — it means the audio has no detectable speech
        # boundaries at all and the user needs to upload an SRT.
        logger.error(
            f"[NarrationDP] No natural break points available — falling back to "
            f"raw {ideal_dur:.1f}s slices.  User should upload an SRT to get "
            f"phrase-aligned scenes."
        )
        scenes = []
        pos = 0.0
        scene_num = 1
        while pos < total_duration - 0.5:
            end = min(pos + ideal_dur, total_duration)
            scenes.append({
                "name": f"Scene {scene_num}",
                "start_time": round(pos, 2),
                "end_time": round(end, 2),
            })
            pos = end
            scene_num += 1
        return scenes

    # Walk parent pointers back from n to 0
    cuts = []
    idx = n
    while idx > 0:
        cuts.append(idx)
        idx = parent[idx]
    cuts.reverse()

    # ── Build scene list ────────────────────────────────────────────────
    def _get_phrase_text(pg: list[dict]) -> str:
        return " ".join(
            (w.get("word", "") or w.get("value", "") or w.get("text", "")).strip()
            for w in pg
        ).strip()

    scenes = []
    prev_cut = 0
    for si, cut_idx in enumerate(cuts):
        start_t = boundaries[prev_cut]
        end_t = boundaries[cut_idx]
        # Collect lyrics text for this scene
        scene_phrases = phrase_groups[prev_cut:cut_idx]
        lyrics_text = " ".join(_get_phrase_text(pg) for pg in scene_phrases if pg).strip()
        # Speech span (earliest word start / latest word end) for this scene —
        # used by the standalone-silence carve below.
        _sp_start = min((float(w.get("start", 0.0) or 0.0) for pg in scene_phrases for w in pg), default=start_t)
        _sp_end = max((float(w.get("end", 0.0) or 0.0) for pg in scene_phrases for w in pg), default=end_t)
        # Auto-name from first few words
        first_words = lyrics_text[:50].strip()
        if len(lyrics_text) > 50:
            first_words = first_words.rsplit(" ", 1)[0] + "..."
        name = f"Scene {si + 1}"
        if first_words:
            name = f"Scene {si + 1} - {first_words}"

        scenes.append({
            "name": name,
            "start_time": round(start_t, 2),
            "end_time": round(end_t, 2),
            "lyrics": lyrics_text,
            "_sp_start": _sp_start,
            "_sp_end": _sp_end,
        })
        prev_cut = cut_idx

    # ── Standalone "silence" scenes for long pauses (>= min_dur) ─────────
    # Per Lorenzo's spec: a pause longer than the scene minimum (e.g. an
    # instrumental break or a deliberate beat of silence) becomes its OWN
    # scene rather than being split 50/50 between its neighbours.  Shorter
    # pauses keep the even midpoint split.  Guard: only carve when both
    # neighbours stay comfortably above 60% of min_dur, so a long pause can
    # never shave an adjacent scene below the minimum.
    if len(scenes) >= 2:
        _carved: list[dict] = []
        for _i, _sc in enumerate(scenes):
            _carved.append(_sc)
            if _i + 1 < len(scenes):
                _nxt = scenes[_i + 1]
                _gap = float(_nxt.get("_sp_start", _nxt["start_time"])) - float(_sc.get("_sp_end", _sc["end_time"]))
                if _gap >= min_dur:
                    _sil_s = round(float(_sc["_sp_end"]), 2)
                    _sil_e = round(float(_nxt["_sp_start"]), 2)
                    _left_ok = (_sil_s - float(_sc["start_time"])) >= min_dur * 0.6
                    _right_ok = (float(_nxt["end_time"]) - _sil_e) >= min_dur * 0.6
                    if (_sil_e - _sil_s) >= min_dur and _left_ok and _right_ok:
                        _sc["end_time"] = _sil_s
                        _nxt["start_time"] = _sil_e
                        _carved.append({
                            "name": f"Silence / instrumental ({_sil_e - _sil_s:.0f}s)",
                            "start_time": _sil_s,
                            "end_time": _sil_e,
                            "lyrics": "",
                            "_sp_start": _sil_s,
                            "_sp_end": _sil_e,
                            "_silence": True,
                        })
                        logger.info(
                            f"[NarrationDP] Carved standalone silence scene "
                            f"[{_sil_s:.2f}-{_sil_e:.2f}s] ({_sil_e - _sil_s:.1f}s pause)"
                        )
        scenes = _carved

    # DP cost function intentionally exempts the LAST scene from the
    # ``dur >= min_dur`` rule (see the `and i < n` at line ~2706 of
    # the DP loop) so it can always produce SOMETHING that covers the
    # tail, but that leaks 1–3s scenes into the user's timeline (e.g.
    # the 1.78s tail user hit in 1.8.14).  Absorb the tail here so the
    # DP-main path matches the fallback path's behavior.
    scenes = _absorb_tiny_tail(
        scenes, min_dur=min_dur, max_dur=max_dur, where="DP main",
    )

    logger.info(
        f"[NarrationDP] Segmented {n} phrases into {len(scenes)} scenes "
        f"(min={min_dur}s, max={max_dur}s, ideal={ideal_dur:.1f}s)"
    )
    for i, s in enumerate(scenes):
        logger.info(
            f"[NarrationDP]   Scene {i + 1}: [{s['start_time']:.2f}–{s['end_time']:.2f}s] "
            f"dur={s['end_time'] - s['start_time']:.1f}s "
            f"'{s['name'][:60]}'"
        )

    for _s in scenes:
        _s.pop("_sp_start", None)
        _s.pop("_sp_end", None)
        _s.pop("_silence", None)

    return scenes


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
                "gap": gap,
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

    # ── NARRATION MODE: use DP segmentation instead of LLM ────────────
    # For narration projects, we have continuous speech with known word
    # timestamps.  Scene boundaries can be computed mathematically using
    # dynamic programming (Knuth-Plass style optimal segmentation).
    # This is instant, deterministic, and handles 600+ phrases easily.
    # Long pauses between phrases are used as natural break points.

    from backend.database.models import ProjectMode
    is_narration = project.mode in (ProjectMode.NARRATION_IMAGES, ProjectMode.NARRATION_VIDEO)

    if is_narration and word_timestamps and phrase_groups:
        logger.info(
            f"[SuggestTimeline] Narration mode detected — using DP segmentation "
            f"({len(phrase_groups)} phrases, min={min_dur}s, max={max_dur}s)"
        )
        dp_scenes = _dp_segment_narration(
            phrase_groups=phrase_groups,
            total_duration=total_duration,
            min_dur=min_dur,
            max_dur=max_dur,
            user_lyrics=user_lyrics,
        )

        # ── Apply the DP scenes (same post-processing as LLM path) ──────
        # Delete existing scenes
        existing_scenes_stmt = (
            select(Scene)
            .where(Scene.project_id == project_id)
            .order_by(Scene.order_index)
        )
        existing_scenes = (await session.execute(existing_scenes_stmt)).scalars().all()
        for sc in existing_scenes:
            await session.delete(sc)
        await session.flush()

        # Create new scenes from DP output
        # NOTE: Scene model uses `order_index` (not scene_index) — see
        # backend.database.models.Scene.  Also fill the required `prompt`
        # field so the NOT NULL constraint doesn't trip on the flush.
        created_scenes = []
        for i, sd in enumerate(dp_scenes):
            new_scene = Scene(
                project_id=project_id,
                name=sd["name"],
                start_time=sd["start_time"],
                end_time=sd["end_time"],
                order_index=i,
                prompt="",
                parameters={"lyrics": sd.get("lyrics", "")},
            )
            session.add(new_scene)
            created_scenes.append(new_scene)
        await session.flush()

        # Slice audio segments — the helper is _slice_audio_for_scenes
        # (returns the per-scene slice count; we don't need it here).
        try:
            await _slice_audio_for_scenes(project_id, session)
        except Exception as _slice_err:
            # Slicing is convenience — log and continue so the user still
            # gets the scenes even if their audio asset isn't slice-able.
            logger.warning(
                f"[SuggestTimeline] Auto-slice failed (non-fatal): {_slice_err}"
            )

        # Capture the scene IDs NOW, before chapter auto-build runs,
        # so a chapter-build failure that poisons the session can't
        # break our response.  ORM-loaded UUIDs are already populated
        # at this point because we session.flushed earlier.
        _created_scene_ids = [str(sc.id) for sc in created_scenes]

        # ── Auto-create chapters from the new scenes ─────────────────
        # Whether the user added `# headers` to the script or not, the
        # chapter builder gives us either:
        #   - headers present → one chapter per `# heading` block
        #   - no headers      → auto-split by scene count (Settings >
        #                       Chapter Batching > "Chapter auto-split
        #                       threshold" controls the size)
        # This makes a 1-hour narration immediately workable in chunks
        # without the user having to do anything manual.
        chapter_count = 0
        try:
            from backend.services.chapters import rebuild_chapters
            chapters = await rebuild_chapters(session, project_id)
            chapter_count = len(chapters)
            logger.info(
                f"[SuggestTimeline] Auto-built {chapter_count} chapter(s) "
                f"from {len(created_scenes)} scenes"
            )
        except Exception as _ch_err:
            # Chapter builder failed; rollback its session changes so
            # the OUTER request handler can still close cleanly.  The
            # scenes themselves were committed by the audio-slicer
            # earlier so they're safe.
            logger.warning(
                f"[SuggestTimeline] Chapter auto-build failed (non-fatal): {_ch_err}"
            )
            try:
                await session.rollback()
            except Exception:
                pass

        return SuggestTimelineResponse(
            created_count=len(created_scenes),
            scene_ids=_created_scene_ids,
            message=(
                f"Created {len(created_scenes)} scenes using optimal "
                f"segmentation (DP algorithm, {len(phrase_groups)} phrases). "
                f"Auto-built {chapter_count} chapter(s) for chunked editing."
            ),
        )

    # ── Build lyrics block for LLM (music mode) ───────────────────────

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

    # Show the valid cut points — these are the ONLY allowed boundary times.
    # Use compact single-line format to keep prompt size manageable for
    # reasoning models that consume output tokens on internal thinking.
    #
    # IMPORTANT: The LLM only needs to pick ~15-30 scene boundaries, so
    # showing 600 cut points is overwhelming and wastes tokens.  We thin
    # to a target of ~80-100 "best" cut points using quality scoring:
    #   - Section / instrumental boundaries always kept (structural)
    #   - Remaining cuts scored by gap size (bigger gap = better cut)
    #   - Top N kept to hit target, ensuring even time distribution
    TARGET_DISPLAY_CUTS = 100  # sweet spot: 3-4x typical scene count

    if valid_cuts:
        display_cuts = valid_cuts
        if len(valid_cuts) > TARGET_DISPLAY_CUTS:
            # Separate structural cuts (always kept) from phrase cuts (scored)
            structural: list[dict] = []
            phrase_cuts: list[dict] = []
            for vc in valid_cuts:
                if "CUT_SEC" in vc["label"] or "CUT_INST" in vc["label"]:
                    structural.append(vc)
                else:
                    phrase_cuts.append(vc)

            # How many phrase cuts we can keep after reserving structural slots
            budget = max(TARGET_DISPLAY_CUTS - len(structural), 20)

            if len(phrase_cuts) > budget:
                # Score each phrase cut by gap size (bigger gap = cleaner break)
                # Also boost cuts near section boundaries (within 3s)
                section_times = set()
                if sections:
                    for s in sections:
                        section_times.add(round(s.start_time, 1))
                        section_times.add(round(s.end_time, 1))

                def _score(vc: dict) -> float:
                    gap = vc.get("gap", 0.3)
                    score = gap  # base score = gap size in seconds
                    # Bonus for cuts near section boundaries
                    t = vc["time"]
                    if any(abs(t - st) < 3.0 for st in section_times):
                        score += 2.0
                    return score

                # Sort by score descending, take top `budget`
                scored = sorted(phrase_cuts, key=_score, reverse=True)
                kept = scored[:budget]
                # Re-sort by time for display
                kept.sort(key=lambda vc: vc["time"])
                phrase_cuts = kept

            display_cuts = sorted(
                structural + phrase_cuts,
                key=lambda vc: vc["time"],
            )
            logger.info(
                f"[SuggestTimeline] Thinned cut points from {len(valid_cuts)} "
                f"to {len(display_cuts)} (structural={len(structural)}, "
                f"phrase={len(phrase_cuts)}, target={TARGET_DISPLAY_CUTS})"
            )

        lyrics_block += "═══ VALID CUT POINTS ═══\n"
        lyrics_block += "You MUST use ONLY these exact times for scene boundaries (start_time / end_time).\n"
        lyrics_block += "Each cut sits between two complete phrases. Using any other time WILL split a phrase.\n\n"
        for vc in display_cuts:
            # Compact single-line: time | after_phrase → before_phrase
            lyrics_block += f"  {vc['time']:.2f}s | \"{vc['after_phrase']}\" → \"{vc['before_phrase']}\"\n"
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

    # Reasoning models (GPT-5.x, o-series) use max_completion_tokens for BOTH
    # internal thinking AND visible output.  With hundreds of cut points the
    # model can exhaust a modest budget on reasoning alone and return nothing.
    # Give a generous budget so the thinking + JSON output both fit.
    _is_reasoning_model = any(
        model.startswith(p)
        for p in ("gpt-5", "o1", "o3", "o4")
    )
    llm_max_tokens = 16384 if _is_reasoning_model else 4000

    # Retry once on transient empty responses
    raw_text = ""
    last_error: Exception | None = None
    for attempt in range(2):
        try:
            raw_text = await asyncio.wait_for(
                asyncio.to_thread(
                    _call_llm, provider, api_key, model, system_prompt, user_prompt,
                max_tokens=llm_max_tokens,
            ),
                timeout=180.0,
            )
            break  # success
        except Exception as e:
            last_error = e
            if attempt == 0:
                logger.warning(f"LLM call attempt 1 failed for suggest-timeline: {e} — retrying")
            else:
                logger.error(f"LLM call failed for suggest-timeline after 2 attempts: {e}")
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

    # LLM-produced timelines can finish with a tail below
    # ``min_dur`` — the LLM sometimes uses every remaining cut point to
    # finish "filling" the audio.  Same producer-agnostic absorption
    # the DP paths run.
    validated_scenes = _absorb_tiny_tail(
        validated_scenes, min_dur=min_dur, max_dur=max_dur,
        where="LLM post-split",
    )

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

    # Auto-build chapters from the new scenes — same as the DP-narration
    # path above.  This wires "Suggest Timeline" → chunked editing for
    # free regardless of project mode.  Picks `# script_headers` if
    # present, otherwise auto-splits by scene count from Settings.
    _chapter_count = 0
    try:
        from backend.services.chapters import rebuild_chapters as _rebuild_chapters
        _chs = await _rebuild_chapters(session, project_id)
        _chapter_count = len(_chs)
        logger.info(
            f"Suggest Fresh Timeline: auto-built {_chapter_count} chapter(s) "
            f"for project {project_id}"
        )
    except Exception as _ch_err:
        logger.warning(f"Chapter auto-build failed (non-fatal): {_ch_err}")

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


@router.post(
    "/upload-srt",
    response_model=LyricsResponse,
    summary="Upload SRT file to set narration word timestamps",
)
async def upload_srt(
    project_id: UUID,
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_session),
) -> LyricsResponse:
    """Upload an SRT subtitle file and parse it into word-level timestamps.

    The SRT is parsed using AudioAnalyzer._parse_srt_to_words() and the
    resulting words are saved as the project's Lyrics record (upsert).

    Returns a full LyricsResponse including pre-built srt_blocks so the
    frontend can immediately use the data without a separate refetch.

    Args:
        project_id: UUID of the project.
        file: The .srt file to upload.
        session: Database session.

    Returns:
        LyricsResponse with words and srt_blocks.

    Raises:
        HTTPException: If project not found, file is not .srt, or parsing fails.
    """
    from sqlalchemy.orm.attributes import flag_modified

    try:
        await _get_project_or_404(project_id, session)

        # Validate file extension
        if not file.filename or not file.filename.lower().endswith(".srt"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="File must be an .srt subtitle file",
            )

        # Read file content — try UTF-8 BOM first, then plain UTF-8
        content = await file.read()
        srt_text = ""
        for enc in ("utf-8-sig", "utf-8", "latin-1"):
            try:
                srt_text = content.decode(enc)
                logger.info(f"SRT file decoded as {enc} ({len(srt_text)} chars)")
                break
            except UnicodeDecodeError:
                continue
        if not srt_text:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Could not decode SRT file — unsupported encoding",
            )

        # Parse SRT into word-level timestamps
        words = AudioAnalyzer._parse_srt_to_words(srt_text)
        if not words:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No words could be parsed from the SRT file",
            )

        # Verify block fields are present
        blocks_with_field = sum(1 for w in words if "block" in w)
        unique_blocks = len(set(w.get("block") for w in words if "block" in w))
        logger.info(
            f"SRT parsed: {len(words)} words, {blocks_with_field} with block field, "
            f"{unique_blocks} unique blocks"
        )

        # Reconstruct full text from parsed words
        full_text = " ".join(w["word"] for w in words)

        # ── Instant SRT/Whisper timing align (NO synchronous Whisper) ────
        # SRT upload must stay FAST.  It must NOT run Whisper here — ComfyUI
        # Whisper can take many minutes and would time out the upload (and
        # double up with a Process Audio run).  So:
        #   * If the project ALREADY has Whisper word timing (from a prior
        #     Process Audio run), map the SRT spelling + cue grouping onto it
        #     instantly (no transcription).
        #   * Otherwise just store the SRT.  The audio re-anchor then happens
        #     when the user runs **Process Audio** (Whisper), which re-anchors
        #     the SRT to the audio timing as part of that pass.
        # Skipped entirely when ``disable_whisper`` is set.
        try:
            from backend.services.audio.text_align import (
                retime_srt_words_to_audio as _retime,
            )
            _proj = await session.get(Project, project_id)
            _psettings = (_proj.settings if _proj else {}) or {}
            _ex_stmt = select(Lyrics).where(Lyrics.project_id == project_id)
            _ex = (await session.execute(_ex_stmt)).scalars().first()
            _prev_words = list(_ex.words or []) if _ex else []
            _prev_is_whisper = bool(_prev_words) and not all(
                isinstance(w, dict) and w.get("block") is not None for w in _prev_words
            )
            if _psettings.get("disable_whisper", False):
                logger.info("SRT upload: disable_whisper set — keeping SRT timings.")
            elif _prev_is_whisper:
                _retimed = _retime(words, _prev_words)
                if _retimed and len(_retimed) == len(words):
                    words = _retimed
                    logger.info(
                        f"SRT upload: mapped SRT spelling onto existing Whisper "
                        f"timing ({len(_prev_words)} words) — instant, no transcription."
                    )
            else:
                logger.info(
                    "SRT upload: stored with the SRT's own timings.  Run "
                    "Process Audio (Whisper) to re-anchor the timing to the "
                    "audio — your SRT spelling + cue grouping are preserved."
                )
        except Exception as _e:
            logger.warning(
                f"SRT upload: timing-align step failed ({type(_e).__name__}: {_e}) "
                f"— keeping SRT timings."
            )

        # Upsert lyrics record
        existing_stmt = select(Lyrics).where(Lyrics.project_id == project_id)
        existing_result = await session.execute(existing_stmt)
        existing_lyrics = existing_result.scalars().first()

        if existing_lyrics:
            existing_lyrics.full_text = full_text
            existing_lyrics.words = words
            # Force SQLAlchemy to detect JSON column change
            flag_modified(existing_lyrics, "words")
            flag_modified(existing_lyrics, "full_text")
        else:
            lyrics_record = Lyrics(
                project_id=project_id,
                full_text=full_text,
                words=words,
            )
            session.add(lyrics_record)

        await session.commit()
        logger.info(
            f"SRT upload for project {project_id}: {len(words)} words, "
            f"{len(full_text)} chars, {unique_blocks} SRT blocks committed to DB"
        )

        # Verify the data was persisted by re-reading from DB
        verify_stmt = select(Lyrics).where(Lyrics.project_id == project_id)
        verify_result = await session.execute(verify_stmt)
        verify_record = verify_result.scalars().first()
        if verify_record:
            stored_words = verify_record.words or []
            stored_blocks = sum(1 for w in stored_words if w.get("block") is not None)
            logger.info(
                f"SRT upload verification: {len(stored_words)} words in DB, "
                f"{stored_blocks} with block field"
            )
            if stored_blocks == 0 and unique_blocks > 0:
                logger.error(
                    "CRITICAL: block fields were lost during DB save! "
                    "SQLAlchemy may have failed to persist the JSON change."
                )

        # Build the full response with srt_blocks
        def _build_srt_blocks_local(raw_words: list[dict]) -> list[SrtBlock]:
            blocks_map: dict[int, list[dict]] = {}
            for w in raw_words:
                b = w.get("block")
                if b is None:
                    continue
                blocks_map.setdefault(b, []).append(w)
            if not blocks_map:
                return []
            result = []
            for block_idx in sorted(blocks_map.keys()):
                bw = blocks_map[block_idx]
                text = " ".join(w.get("word", "") for w in bw)
                start = min(w.get("start", 0.0) for w in bw)
                end = max(w.get("end", 0.0) for w in bw)
                if text.strip():
                    result.append(SrtBlock(text=text.strip(), start=start, end=end))
            return result

        srt_blocks = _build_srt_blocks_local(words)
        word_models = [
            WordTimestamp(
                word=w.get("word", ""),
                start_time=w.get("start", 0.0),
                end_time=w.get("end", 0.0),
                block=w.get("block"),
            )
            for w in words
        ]

        logger.info(
            f"SRT upload response: {len(word_models)} words, {len(srt_blocks)} srt_blocks"
        )

        return LyricsResponse(
            text=full_text,
            words=word_models,
            srt_blocks=srt_blocks,
            initial_text=getattr(existing_lyrics, "initial_text", "") if existing_lyrics else "",
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error uploading SRT for project {project_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to process SRT file: {str(e)}",
        )
