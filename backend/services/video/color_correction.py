"""
Color Correction Service (with GPU acceleration support)

Computes per-channel color gains between a reference image and a video's
first frame, then applies correction to the entire video via FFmpeg
colorchannelmixer filter.  This fixes the brightness / color-balance drift
that is common when AI video models (LTX, etc.) deviate from their input
reference frames.

Algorithm:
1. Load the reference image (the first-frame input we gave to the model).
2. Extract the video's actual first frame.
3. Compute per-channel (R, G, B) mean pixel values for both images.
4. Derive gain factors:  gain_c = ref_mean_c / video_mean_c
5. Clamp gains to a safe range (0.5 – 2.0) to avoid extreme shifts.
6. Apply via FFmpeg  -vf colorchannelmixer=rr=Gr:gg=Gg:bb=Gb  in one pass.
"""

import logging
import subprocess
import tempfile
from pathlib import Path
from typing import Optional, Tuple

from PIL import Image
import numpy as np

from .ffmpeg import extract_frame, extract_last_frame, get_media_info, count_video_frames, _run_ffmpeg, _gpu as _ffmpeg_gpu


def _get_gpu_encode_flags(crf: int = 18) -> list[str]:
    """Get GPU-accelerated encoding flags from the shared FFmpeg GPU detector."""
    return _ffmpeg_gpu.get_encode_flags(crf)


def _get_gpu_decode_flags() -> list[str]:
    """Get GPU-accelerated decoding flags from the shared FFmpeg GPU detector."""
    return _ffmpeg_gpu.get_decode_flags()


def _probe_fps(video_path: str) -> int:
    """Probe a video file for its framerate, defaulting to 24."""
    try:
        info = get_media_info(video_path)
        fps = info.get("fps", 24)
        return int(round(fps))
    except Exception:
        return 24

logger = logging.getLogger(__name__)

# Gain clamp range — prevents runaway correction on very dark/bright frames
GAIN_MIN = 0.5
GAIN_MAX = 2.0

# Minimum difference threshold — skip correction if channels are already close.
# 5% of 255 (~12.75 pixel values) is the minimum perceptible difference.
# Previously 2% — but V2V-conditioned videos already have good color
# continuity.  Re-encoding clips with tiny corrections (2-5% range)
# introduced more visual artifacts (1-2 frame flicker at concat splice
# points) than the color mismatch it was trying to fix.
DIFF_THRESHOLD = 0.05  # 5 % of 255

# Per-channel gain proximity range.  Even when the aggregate difference
# exceeds DIFF_THRESHOLD, skip correction if every individual channel
# gain is close to 1.0.  The re-encode pass itself introduces a subtle
# quality/timing shift at clip boundaries that is more visible than
# the color correction it applies.
GAIN_SKIP_MIN = 0.96
GAIN_SKIP_MAX = 1.04


def compute_channel_means(image_path: str) -> Tuple[float, float, float]:
    """Compute mean R, G, B values for an image (0-255 scale).

    Args:
        image_path: Path to the image file.

    Returns:
        Tuple of (mean_r, mean_g, mean_b).

    Raises:
        RuntimeError: If image cannot be loaded.
    """
    try:
        img = Image.open(image_path).convert("RGB")
        arr = np.array(img, dtype=np.float64)
        mean_r = float(arr[:, :, 0].mean())
        mean_g = float(arr[:, :, 1].mean())
        mean_b = float(arr[:, :, 2].mean())
        return mean_r, mean_g, mean_b
    except Exception as e:
        raise RuntimeError(f"Failed to compute channel means for {image_path}: {e}")


def compute_correction_gains(
    ref_means: Tuple[float, float, float],
    video_means: Tuple[float, float, float],
) -> Optional[Tuple[float, float, float]]:
    """Compute per-channel gain factors to match video colours to reference.

    Returns None if the difference is within the threshold (no correction
    needed).

    Args:
        ref_means: (R, G, B) means of the reference image.
        video_means: (R, G, B) means of the video's first frame.

    Returns:
        Tuple of (gain_r, gain_g, gain_b) clamped to safe range, or None
        if correction is unnecessary.
    """
    gains = []
    needs_correction = False

    for ref_val, vid_val in zip(ref_means, video_means):
        if vid_val < 1.0:
            # Near-black channel — clamp to 1.0 (no change) to avoid div-by-zero
            gains.append(1.0)
            continue

        gain = ref_val / vid_val
        gain = max(GAIN_MIN, min(GAIN_MAX, gain))
        gains.append(gain)

        # Check if this channel deviates enough to warrant correction
        if abs(ref_val - vid_val) / 255.0 > DIFF_THRESHOLD:
            needs_correction = True

    if not needs_correction:
        logger.info(
            "Color correction skipped — channels within %.1f%% threshold "
            "(ref=%s, vid=%s)",
            DIFF_THRESHOLD * 100,
            [f"{v:.1f}" for v in ref_means],
            [f"{v:.1f}" for v in video_means],
        )
        return None

    # Even if the aggregate threshold was exceeded, skip when all per-channel
    # gains are close to 1.0.  The re-encode itself introduces a subtle
    # quality shift at clip boundaries that can be more visible than the
    # correction (manifests as 1-2 frame color/motion flicker at concat
    # splice points).
    all_gains_near_unity = all(
        GAIN_SKIP_MIN <= g <= GAIN_SKIP_MAX for g in gains
    )
    if all_gains_near_unity:
        logger.info(
            "Color correction skipped — gains near unity "
            "(R=%.4f G=%.4f B=%.4f, range [%.2f-%.2f])",
            gains[0], gains[1], gains[2], GAIN_SKIP_MIN, GAIN_SKIP_MAX,
        )
        return None

    return (gains[0], gains[1], gains[2])


def apply_color_correction(
    video_path: str,
    output_path: str,
    gains: Tuple[float, float, float],
    crf: int = 18,
    keep_audio: bool = False,
) -> str:
    """Apply per-channel colour gains to a video via FFmpeg colorchannelmixer.

    This re-encodes the video with libx264 since the filter modifies pixel
    values.

    Args:
        video_path: Input video path.
        output_path: Output (corrected) video path.
        gains: (gain_r, gain_g, gain_b) multipliers.

    Returns:
        The output_path.

    Raises:
        RuntimeError: If FFmpeg fails.
    """
    gr, gg, gb = gains
    fps = _probe_fps(video_path)

    # Probe exact frame count BEFORE re-encoding so we can constrain
    # the output to the same count.  Without this, the re-encode can
    # produce ±1 frame due to PTS rounding at clip boundaries, which
    # creates a 1-frame skip at concat splice points.
    src_frames = count_video_frames(video_path)

    logger.info(
        "Applying color correction: R=%.3f G=%.3f B=%.3f  %s → %s (fps=%d, frames=%d)",
        gr, gg, gb, video_path, output_path, fps, src_frames,
    )

    frame_limit_flags: list[str] = []
    if src_frames > 0:
        frame_limit_flags = ["-frames:v", str(src_frames)]

    cmd = [
        "ffmpeg",
        *_get_gpu_decode_flags(),
        "-i", video_path,
        "-vf", f"colorchannelmixer=rr={gr:.4f}:gg={gg:.4f}:bb={gb:.4f}",
        # -r sets the output container framerate, matching normalize_clip.
        "-r", str(fps),
        *_get_gpu_encode_flags(crf=crf),
        "-pix_fmt", "yuv420p",
        # Match normalize_clip timing flags exactly — mismatched timescale
        # or keyframe placement between normalize and color correction
        # causes a 1-frame "step back" at concat splice points.
        "-force_key_frames", "expr:eq(n,0)",
        "-g", str(fps),
        "-bf", "0",
        "-video_track_timescale", str(fps * 1000),
        *frame_limit_flags,
        # AV-native: preserve the model-generated audio that lives in the
        # source MP4.  Re-encode to AAC (we're already re-encoding video,
        # so an extra audio re-encode is cheap) instead of -an strip.
        *(["-c:a", "aac", "-b:a", "192k"] if keep_audio else ["-an"]),
        "-y",
        output_path,
    ]

    _run_ffmpeg(cmd, "color_correction")
    logger.info("Color correction applied: %s", output_path)
    return output_path


def color_correct_video(
    video_path: str,
    reference_image_path: str,
    output_path: Optional[str] = None,
    keep_audio: bool = False,
) -> Optional[str]:
    """Full pipeline: analyse reference vs video first frame, apply correction.

    If the colour difference is below the threshold the video is left
    untouched and None is returned.

    Args:
        video_path: Path to the video to correct.
        reference_image_path: Path to the reference image (e.g. the chosen
            first frame image that was fed to the model).
        output_path: Where to write the corrected video.  If None, overwrites
            the original (backing up to *_precorrection* first).

    Returns:
        Path to the corrected video, or None if no correction was needed.
    """
    logger.info(
        "Color correction: video=%s  ref=%s",
        video_path, reference_image_path,
    )

    # 1. Extract the video's actual first frame to a temp file
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        first_frame_path = tmp.name

    try:
        extract_frame(video_path, first_frame_path, 0.0)

        # 2. Compute channel means
        ref_means = compute_channel_means(reference_image_path)
        vid_means = compute_channel_means(first_frame_path)

        logger.info(
            "Channel means — ref: R=%.1f G=%.1f B=%.1f  vid: R=%.1f G=%.1f B=%.1f",
            *ref_means, *vid_means,
        )

        # 3. Compute gains
        gains = compute_correction_gains(ref_means, vid_means)
        if gains is None:
            return None

        # 4. Apply correction
        if output_path is None:
            # In-place: back up original, correct into original path
            import shutil
            backup_path = str(Path(video_path).with_suffix("")) + "_precorrection" + Path(video_path).suffix
            shutil.copy2(video_path, backup_path)
            logger.info("Backed up original to %s", backup_path)

            corrected_tmp = str(Path(video_path).with_suffix("")) + "_cctmp" + Path(video_path).suffix
            apply_color_correction(video_path, corrected_tmp, gains, keep_audio=keep_audio)
            shutil.move(corrected_tmp, video_path)
            return video_path
        else:
            apply_color_correction(video_path, output_path, gains, keep_audio=keep_audio)
            return output_path

    finally:
        Path(first_frame_path).unlink(missing_ok=True)


def match_adjacent_clips(
    clip_paths: list[str],
    output_dir: str,
    crf: int = 18,
) -> list[str]:
    """Apply color matching between adjacent clips to reduce visual jarring.

    For each consecutive pair of clips (A, B), extracts the last frame of A
    and first frame of B, computes a colour correction for B so its opening
    tones match A's closing tones, and writes the corrected B to output_dir.

    The first clip is always returned unmodified (it is the reference anchor).

    Args:
        clip_paths: Ordered list of clip paths.
        output_dir: Directory for corrected clips.

    Returns:
        New list of clip paths (same length as input) — some may point to
        corrected copies in *output_dir*, others to the original.
    """
    if len(clip_paths) < 2:
        return list(clip_paths)

    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    result_paths = [clip_paths[0]]  # first clip is anchor

    for i in range(1, len(clip_paths)):
        prev_clip = result_paths[i - 1]  # use the (possibly corrected) previous
        curr_clip = clip_paths[i]

        prev_last = str(out_dir / f"_adj_prev_last_{i}.png")
        curr_first = str(out_dir / f"_adj_curr_first_{i}.png")

        try:
            extract_last_frame(prev_clip, prev_last)
            extract_frame(curr_clip, curr_first, 0.0)

            ref_means = compute_channel_means(prev_last)
            vid_means = compute_channel_means(curr_first)

            gains = compute_correction_gains(ref_means, vid_means)
            if gains is None:
                # Close enough — keep original
                result_paths.append(curr_clip)
            else:
                corrected = str(out_dir / f"colormatch_{i:03d}.mp4")
                apply_color_correction(curr_clip, corrected, gains, crf=crf)
                result_paths.append(corrected)
                logger.info(
                    "Adjacent-clip color match %d→%d: R=%.3f G=%.3f B=%.3f",
                    i - 1, i, *gains,
                )

        except Exception as e:
            logger.warning("Adjacent-clip color match %d→%d failed: %s", i - 1, i, e)
            result_paths.append(curr_clip)

        finally:
            Path(prev_last).unlink(missing_ok=True)
            Path(curr_first).unlink(missing_ok=True)

    return result_paths
