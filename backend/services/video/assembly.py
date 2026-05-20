"""
Video Assembly Pipeline

Final video assembly: clip normalization, concatenation, and audio muxing.
"""

import logging
from pathlib import Path
from typing import List, Dict, Any, Optional, Callable

from .ffmpeg import (
    normalize_clip,
    concat_clips,
    mux_audio,
    apply_kenburns,
    crossfade,
    apply_transition,
    apply_fade_in,
    apply_fade_out,
    get_media_info,
    pad_video_end,
)
from .color_correction import match_adjacent_clips

logger = logging.getLogger(__name__)


def assemble_music_video(
    scenes: List[Dict[str, Any]],
    master_audio_path: str,
    output_path: str,
    width: int = 1280,
    height: int = 720,
    fps: int = 24,
    default_transition: Optional[str] = "none",
    default_transition_duration: float = 0.5,
    color_match_clips: bool = True,
    progress_callback: Optional[Callable[[str, int], None]] = None,
    final_crf: int = 18,
) -> None:
    """
    Assemble music video from scenes.

    Each scene specifies its source type ('image' or 'video') and optional
    image_movement effects and transitions.

    Workflow:
    1. For each scene, create a clip:
       - scene_source_type='video' → normalize the video clip
       - scene_source_type='image' → apply Ken Burns / movement to still image
    2. Apply scene-level fade in/out transitions
    3. Concatenate all processed clips (with xfade transitions where specified)
    4. Mux with master audio

    Scene format:
    [
        {
            "video_path": "/path/to/video.mp4",      # for video source scenes
            "image_path": "/path/to/image.png",       # for image source scenes
            "duration": 5.0,                          # scene duration in seconds
            "scene_source_type": "image" | "video",   # which source to use
            "image_movement": {                       # optional movement for image scenes
                "effect": "zoom_in_center",
                "intensity": 50,
                "easing": "ease_in_out",
            },
            "transition_in": {"type": "crossfade", "duration": 0.5},   # optional
            "transition_out": {"type": "fade_to_black", "duration": 0.5},  # optional
        },
        ...
    ]

    Args:
        scenes: List of scene dicts
        master_audio_path: Path to master audio track
        output_path: Final output video path
        width: Target width (default 1280)
        height: Target height (default 720)
        fps: Target framerate (default 30)
        default_transition: Default transition between clips when scenes don't
            specify their own.  'crossfade', 'dissolve', or 'none'.
        default_transition_duration: Duration of the default transition in seconds.
        color_match_clips: If True, apply per-channel colour matching between
            adjacent clips to reduce AI color drift jarring.
        progress_callback: Optional callback(step_description, percent_0_100)
            for reporting progress to the caller.
        final_crf: CRF quality for the final output encode (default 18).
            All intermediate re-encode stages use CRF 14 (near-lossless) to
            prevent cumulative quality degradation across multiple passes.

    Raises:
        ValueError: If scenes list empty
        RuntimeError: If processing fails
    """
    if not scenes:
        raise ValueError("No scenes provided")

    def report(step: str, percent: int) -> None:
        if progress_callback:
            try:
                progress_callback(step, percent)
            except Exception:
                pass  # Don't let callback errors break assembly

    # Use near-lossless CRF for ALL intermediate re-encode stages.
    # Only the final concat/output uses final_crf (the user's quality choice).
    # This prevents cumulative quality degradation across the pipeline:
    #   normalize (CRF 14) → color match (CRF 14) → transition (CRF 14) → final concat (final_crf)
    intermediate_crf = 14

    logger.info(
        f"Assembling music video: {len(scenes)} scenes → {output_path} "
        f"(intermediate CRF={intermediate_crf}, final CRF={final_crf})"
    )
    report(f"Assembling {len(scenes)} scenes...", 0)

    output_dir = Path(output_path).parent
    output_dir.mkdir(parents=True, exist_ok=True)
    temp_files: list[str] = []

    total_scenes = len(scenes)

    # ── Xfade type set (used for transition detection and compensation) ──
    xfade_types_set = {"crossfade", "dissolve", "wipe_left", "wipe_right",
                       "wipe_up", "wipe_down", "slide_left", "slide_right"}

    # ── V2V overlap handling ────────────────────────────────────────
    # V2V overlap trimming is handled by the dispatcher at generation
    # time: after scene B's video is generated, the dispatcher compares
    # B's first frame against A's tail to find the overlap point, then
    # re-trims A's chosen_video_path so it ends right before the overlap.
    # By the time we reach assembly, A is already correctly trimmed and
    # B is used as-is — no pre-step needed here.

    # Step 1: Create a clip for each scene based on its source type
    # We track which original scene indices produce clips so that
    # transition compensation and xfade application use the correct
    # scene data even when some scenes are skipped (no content).
    # Clip creation takes ~60% of total time
    scene_clips: list[str] = []
    clip_scene_indices: list[int] = []  # original scene index per clip
    for i, scene in enumerate(scenes):
        source_type = scene.get("scene_source_type", "image")
        duration = scene.get("duration", 5.0)
        clip_percent = int((i / total_scenes) * 60)
        report(f"Rendering clip {i + 1}/{total_scenes}...", clip_percent)

        if source_type == "video":
            # Use the generated/uploaded video
            video_path = scene.get("video_path")
            if not video_path:
                logger.warning(f"Scene {i} is video source but missing video_path, skipping")
                continue
            clip_path = output_dir / f"clip_{i:03d}.mp4"
            # If this scene re-uses the previous scene's last frame as its
            # first frame, skip the duplicate opening frame during normalize
            # to eliminate the stutter at the transition.
            skip_ff = bool(scene.get("trim_first_frame", False))
            # V2V overlap is handled by trim-A in the dispatcher: scene A
            # is trimmed at the MSE match point and scene boundaries are
            # adjusted, so no skip_head_frames needed here.
            normalize_clip(video_path, str(clip_path), width, height, fps,
                           skip_first_frame=skip_ff,
                           max_duration=duration, crf=intermediate_crf)

        else:
            # Use the still image with optional movement effect
            image_path = scene.get("image_path")
            if not image_path:
                # Fall back to video_path if image not set
                video_path = scene.get("video_path")
                if video_path:
                    clip_path = output_dir / f"clip_{i:03d}.mp4"
                    normalize_clip(video_path, str(clip_path), width, height, fps,
                                   max_duration=duration, crf=intermediate_crf)
                else:
                    logger.warning(f"Scene {i} has no image_path or video_path, skipping")
                    continue
            else:
                clip_path = output_dir / f"clip_{i:03d}.mp4"
                movement = scene.get("image_movement", {})
                effect = movement.get("effect", "none") if movement else "none"

                if effect and effect != "none":
                    apply_kenburns(
                        image_path,
                        str(clip_path),
                        duration,
                        width,
                        height,
                        effect=effect,
                        intensity=movement.get("intensity", 50),
                        easing=movement.get("easing", "ease_in_out"),
                        fps=fps,
                        crf=intermediate_crf,
                    )
                else:
                    # Static image — just create a still video
                    apply_kenburns(
                        image_path,
                        str(clip_path),
                        duration,
                        width,
                        height,
                        effect="zoom_in_center",
                        intensity=0,  # no movement
                        fps=fps,
                        crf=intermediate_crf,
                    )

        temp_files.append(str(clip_path))

        # Apply fade-in/fade-out for self-contained transitions
        transition_in = scene.get("transition_in")
        transition_out = scene.get("transition_out")

        if transition_in and transition_in.get("type") in ("fade_from_black", "fade_from_white"):
            faded_path = output_dir / f"clip_{i:03d}_fi.mp4"
            color = "white" if transition_in["type"] == "fade_from_white" else "black"
            apply_fade_in(str(clip_path), str(faded_path), transition_in.get("duration", 0.5), color)
            temp_files.append(str(faded_path))
            clip_path = faded_path

        if transition_out and transition_out.get("type") in ("fade_to_black", "fade_to_white"):
            faded_path = output_dir / f"clip_{i:03d}_fo.mp4"
            color = "white" if transition_out["type"] == "fade_to_white" else "black"
            apply_fade_out(str(clip_path), str(faded_path), transition_out.get("duration", 0.5), color)
            temp_files.append(str(faded_path))
            clip_path = faded_path

        scene_clips.append(str(clip_path))
        clip_scene_indices.append(i)

    if not scene_clips:
        raise RuntimeError("No clips to concatenate")

    # ── Post-clip transition compensation ────────────────────────────
    # Now that we know which scenes produced clips, compute transition
    # durations between *actually adjacent* clips and extend each clip
    # to compensate for xfade overlap.  Each transition removes exactly
    # `transition_duration` from the combined length; we split the
    # overlap: half added to the outgoing clip, half to the incoming.

    num_clips = len(scene_clips)

    # Determine if we'll use a default xfade
    _has_explicit = False
    for ci in range(num_clips - 1):
        si_out = clip_scene_indices[ci]
        si_in = clip_scene_indices[ci + 1]
        if (scenes[si_out].get("transition_out", {}) or {}).get("type", "none") in xfade_types_set:
            _has_explicit = True
            break
        if (scenes[si_in].get("transition_in", {}) or {}).get("type", "none") in xfade_types_set:
            _has_explicit = True
            break

    _will_use_default = (
        not _has_explicit
        and default_transition
        and default_transition != "none"
        and default_transition in xfade_types_set
        and default_transition_duration > 0
        and num_clips > 1
    )

    # Build per-clip-boundary transition durations
    clip_boundary_durations: list[float] = []  # length = num_clips - 1
    for ci in range(num_clips - 1):
        si_out = clip_scene_indices[ci]
        si_in = clip_scene_indices[ci + 1]
        t_in = scenes[si_in].get("transition_in", {}) or {}
        t_out = scenes[si_out].get("transition_out", {}) or {}

        t_dur = 0.0
        if t_in.get("type") in xfade_types_set:
            t_dur = t_in.get("duration", 0.5)
        elif t_out.get("type") in xfade_types_set:
            t_dur = t_out.get("duration", 0.5)
        elif _will_use_default:
            t_dur = default_transition_duration
        clip_boundary_durations.append(t_dur)

    # Compute per-clip padding: each clip absorbs half the overlap from
    # its left boundary and half from its right boundary.
    clip_padding: list[float] = [0.0] * num_clips
    for ci, bd in enumerate(clip_boundary_durations):
        if bd > 0:
            clip_padding[ci] += bd / 2.0        # outgoing clip gets half
            clip_padding[ci + 1] += bd / 2.0     # incoming clip gets half

    total_padding = sum(clip_boundary_durations)
    if total_padding > 0:
        logger.info(
            f"Transition compensation: {len(clip_boundary_durations)} boundaries, "
            f"{total_padding:.1f}s total overlap to compensate"
        )

    # Apply padding by re-rendering/extending clips that need it
    for ci in range(num_clips):
        pad = clip_padding[ci]
        if pad <= 0:
            continue

        original_clip = scene_clips[ci]
        si = clip_scene_indices[ci]
        scene = scenes[si]
        source_type = scene.get("scene_source_type", "image")
        image_path = scene.get("image_path")

        # For image-source scenes with a still image, re-render Ken Burns
        # with extended duration.  For video-source scenes (or image
        # fallback-to-video), freeze-frame-pad the end.
        if source_type == "image" and image_path:
            # Re-render Ken Burns with extended duration
            duration = scene.get("duration", 5.0)
            kb_duration = duration + pad
            movement = scene.get("image_movement", {})
            effect = movement.get("effect", "none") if movement else "none"
            padded_path = output_dir / f"clip_{si:03d}_padded.mp4"

            if effect and effect != "none":
                apply_kenburns(
                    image_path,
                    str(padded_path),
                    kb_duration,
                    width,
                    height,
                    effect=effect,
                    intensity=movement.get("intensity", 50),
                    easing=movement.get("easing", "ease_in_out"),
                    fps=fps,
                    crf=intermediate_crf,
                )
            else:
                apply_kenburns(
                    image_path,
                    str(padded_path),
                    kb_duration,
                    width,
                    height,
                    effect="zoom_in_center",
                    intensity=0,
                    fps=fps,
                    crf=intermediate_crf,
                )
            temp_files.append(str(padded_path))
            scene_clips[ci] = str(padded_path)
        else:
            # Video source — freeze-frame pad the end
            padded_path = output_dir / f"clip_{si:03d}_padded.mp4"
            pad_video_end(original_clip, str(padded_path), pad, crf=intermediate_crf)
            temp_files.append(str(padded_path))
            scene_clips[ci] = str(padded_path)

    # Step 1a: V2V overlap is handled by trim-A in the dispatcher.
    # Scene A is trimmed at the MSE match point, and scene boundaries
    # are adjusted so A/B join seamlessly.  No skip_head_frames or
    # post-normalize frame matching needed here.

    # Step 1b: Adjacent-clip colour matching (before transitions)
    if color_match_clips and len(scene_clips) > 1:
        report("Matching colors between adjacent clips...", 62)
        logger.info("Applying adjacent-clip colour matching across %d clips", len(scene_clips))
        cm_dir = str(output_dir / "_colormatch")
        matched_clips = match_adjacent_clips(scene_clips, cm_dir, crf=intermediate_crf)
        # Track any new files for cleanup
        for mc in matched_clips:
            if mc not in scene_clips and mc not in temp_files:
                temp_files.append(mc)
        scene_clips = matched_clips

    # ── Step 1c: Check for AI transition clips ──
    # If any scene has a transition_clip_path, we'll interleave those clips
    # between scene clips instead of using xfade for those boundaries.
    # First, normalize any AI transition clips found.
    ai_transition_clips: dict[int, str] = {}  # clip_index → normalized transition clip path
    for ci in range(num_clips - 1):
        si = clip_scene_indices[ci]
        t_clip = scenes[si].get("transition_clip_path")
        if t_clip and Path(t_clip).exists():
            t_norm_path = output_dir / f"transition_{ci:03d}.mp4"
            normalize_clip(t_clip, str(t_norm_path), width, height, fps,
                           crf=intermediate_crf)
            temp_files.append(str(t_norm_path))
            ai_transition_clips[ci] = str(t_norm_path)
            logger.info(f"Normalized AI transition clip for boundary {ci}→{ci+1}")

    if ai_transition_clips:
        logger.info(f"Found {len(ai_transition_clips)} AI transition clips to interleave")

    # Step 2: Apply inter-scene xfade transitions where specified, then concatenate
    report("Applying transitions...", 70)
    # Use clip_scene_indices to look up the correct scene data for each clip
    has_xfade = _has_explicit or _will_use_default
    use_default_xfade = _will_use_default

    if use_default_xfade:
        logger.info(
            "Applying default %s transition (%.1fs) between %d clips",
            default_transition, default_transition_duration, len(scene_clips),
        )

    if (has_xfade or ai_transition_clips) and len(scene_clips) > 1:
        # Sequential merge: pair-by-pair with xfade or AI transition insert
        total_transitions = len(scene_clips) - 1
        merged = scene_clips[0]
        for ci in range(1, len(scene_clips)):
            t_percent = 70 + int((ci / total_transitions) * 18)
            report(f"Applying transition {ci}/{total_transitions}...", t_percent)

            # Check if there's an AI transition clip for this boundary
            ai_clip = ai_transition_clips.get(ci - 1)
            if ai_clip:
                # Insert AI transition clip between scene clips (no xfade needed)
                cat_path = output_dir / f"cat_ai_{ci:03d}.mp4"
                concat_clips([merged, ai_clip, scene_clips[ci]], str(cat_path), fps=fps,
                             crf=intermediate_crf)
                temp_files.append(str(cat_path))
                merged = str(cat_path)
                logger.info(f"Inserted AI transition clip at boundary {ci-1}→{ci}")
                continue

            si_in = clip_scene_indices[ci]
            si_prev = clip_scene_indices[ci - 1]
            scene_in = scenes[si_in]
            scene_prev = scenes[si_prev]

            t_in = scene_in.get("transition_in", {})
            t_out_prev = scene_prev.get("transition_out", {})

            t_type = None
            t_dur = 0.5
            # Prefer incoming scene's lead-in, fall back to outgoing scene's lead-out
            if t_in and t_in.get("type") in xfade_types_set:
                t_type = t_in["type"]
                t_dur = t_in.get("duration", 0.5)
            elif t_out_prev and t_out_prev.get("type") in xfade_types_set:
                t_type = t_out_prev["type"]
                t_dur = t_out_prev.get("duration", 0.5)

            # Fall back to default transition if no explicit one
            if not t_type and use_default_xfade:
                t_type = default_transition
                t_dur = default_transition_duration

            if t_type:
                xfade_path = output_dir / f"xfade_{ci:03d}.mp4"
                apply_transition(merged, scene_clips[ci], str(xfade_path), t_type, t_dur,
                                 crf=intermediate_crf)
                temp_files.append(str(xfade_path))
                merged = str(xfade_path)
            else:
                # No transition — concat directly
                cat_path = output_dir / f"cat_{ci:03d}.mp4"
                concat_clips([merged, scene_clips[ci]], str(cat_path), fps=fps,
                             crf=intermediate_crf)
                temp_files.append(str(cat_path))
                merged = str(cat_path)

        concat_path = Path(merged)
    else:
        # No xfade transitions — simple concatenation
        report("Concatenating clips...", 75)
        concat_path = output_dir / "concatenated.mp4"
        concat_clips(scene_clips, str(concat_path), fps=fps, crf=final_crf)
        if str(concat_path) not in temp_files:
            temp_files.append(str(concat_path))

    # Step 3: Mux audio
    report("Muxing audio track...", 90)
    logger.info(f"Muxing with master audio: {master_audio_path}")
    mux_audio(str(concat_path), master_audio_path, output_path)

    # Cleanup intermediate files
    report("Cleaning up temporary files...", 97)
    for f in temp_files:
        Path(f).unlink(missing_ok=True)
    # concat_list.txt is no longer created (concat filter used instead of
    # concat demuxer), but clean up in case an older code path left one.
    (output_dir / "concat_list.txt").unlink(missing_ok=True)
    # Clean up colormatch temp directory
    cm_dir_path = output_dir / "_colormatch"
    if cm_dir_path.exists():
        import shutil
        shutil.rmtree(cm_dir_path, ignore_errors=True)

    report("Assembly complete!", 100)
    logger.info(f"Music video assembled: {output_path}")


def assemble_narration_video(
    scenes: List[Dict[str, Any]],
    narration_audio_path: str,
    output_path: str,
    width: int = 1280,
    height: int = 720,
    fps: int = 24,
    progress_callback: Optional[Callable[[str, int], None]] = None,
) -> None:
    """
    Assemble narration video from image scenes with Ken Burns effects.

    Workflow:
    1. Apply Ken Burns to each image, matching narration segment duration
    2. Crossfade between clips
    3. Mux with narration audio

    Scene format:
    [
        {
            "image_path": "/path/to/image.jpg",
            "duration": 5.0,
            "effect": "zoom_in_center",  # optional, default zoom_in_center
        },
        ...
    ]

    Args:
        scenes: List of scene dicts with image_path and duration
        narration_audio_path: Path to narration audio
        output_path: Final output video path
        width: Target width (default 1280)
        height: Target height (default 720)
        fps: Target framerate (default 30)
        progress_callback: Optional callback(step_description, percent_0_100)
            for reporting progress to the caller.

    Raises:
        ValueError: If scenes list empty
        RuntimeError: If processing fails
    """
    if not scenes:
        raise ValueError("No scenes provided")

    def report(step: str, percent: int) -> None:
        if progress_callback:
            try:
                progress_callback(step, percent)
            except Exception:
                pass

    logger.info(f"Assembling narration video: {len(scenes)} scenes → {output_path}")
    report(f"Assembling {len(scenes)} scenes...", 0)

    output_dir = Path(output_path).parent
    output_dir.mkdir(parents=True, exist_ok=True)

    total_scenes = len(scenes)

    # Step 1: Apply Ken Burns to each image
    kb_clips = []
    for i, scene in enumerate(scenes):
        image_path = scene.get("image_path")
        duration = scene.get("duration", 5.0)

        clip_percent = int((i / total_scenes) * 70)
        report(f"Rendering clip {i + 1}/{total_scenes}...", clip_percent)

        # Support both old 'effect' key and new 'image_movement' dict
        movement = scene.get("image_movement", {})
        if movement and movement.get("effect"):
            effect = movement["effect"]
            intensity = movement.get("intensity", 50)
            easing = movement.get("easing", "ease_in_out")
        else:
            effect = scene.get("effect", "zoom_in_center")
            intensity = 50
            easing = "ease_in_out"

        if not image_path:
            logger.warning(f"Scene {i} missing image_path, skipping")
            continue

        kb_path = output_dir / f"kenburns_{i:03d}.mp4"

        logger.info(f"Applying Ken Burns to scene {i}: {effect} ({duration}s)")
        apply_kenburns(
            image_path,
            str(kb_path),
            duration,
            width,
            height,
            effect=effect,
            intensity=intensity,
            easing=easing,
            fps=fps,
        )

        kb_clips.append(str(kb_path))

    if not kb_clips:
        raise RuntimeError("No clips to concatenate")

    # Step 2: Crossfade between clips
    report("Concatenating clips...", 75)
    # For simplicity, concatenate directly; crossfade can be added if desired
    concat_path = output_dir / "concatenated.mp4"
    logger.info(f"Concatenating {len(kb_clips)} Ken Burns clips")
    concat_clips(kb_clips, str(concat_path), fps=fps)

    # Step 3: Mux narration audio
    report("Muxing audio track...", 90)
    logger.info(f"Muxing with narration: {narration_audio_path}")
    mux_audio(str(concat_path), narration_audio_path, output_path)

    # Cleanup intermediate files
    report("Cleaning up temporary files...", 97)
    for clip_path in kb_clips:
        Path(clip_path).unlink(missing_ok=True)
    concat_path.unlink(missing_ok=True)

    report("Assembly complete!", 100)
    logger.info(f"Narration video assembled: {output_path}")
