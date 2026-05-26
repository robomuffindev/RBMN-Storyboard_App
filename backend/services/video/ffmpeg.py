"""
FFmpeg Video Operations

Provides video processing primitives: normalization, trimming, concatenation,
audio mixing, and effects.  Automatically detects GPU hardware acceleration
(NVIDIA NVENC / AMD AMF) and uses it for encoding when available.
"""

import json
import logging
import re
import subprocess
from pathlib import Path
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)


# ── GPU Hardware Acceleration Detection ─────────────────────────────────
# Detected once at import time and cached for the process lifetime.

class _GPUAccel:
    """Cached GPU acceleration capabilities for FFmpeg.

    Supports NVIDIA (NVENC), AMD (AMF / VAAPI), Intel (QSV), and CPU fallback.
    Detection is two-phase: first checks `ffmpeg -encoders` for candidates,
    then test-encodes a single frame to verify the hardware + drivers are
    actually present.  This prevents failures like 'Cannot load nvcuda.dll'
    on machines where FFmpeg was compiled with GPU support but the GPU isn't
    installed.

    Provides both hardware-accelerated encoding (get_encode_flags) and
    decoding (get_decode_flags).  Decode flags go BEFORE ``-i`` in the
    FFmpeg command line; encode flags go after the output options.
    """

    # Encoder candidates in priority order.
    # Each entry: (encoder_name, gpu_type_label, decode_hwaccel)
    #   decode_hwaccel: the -hwaccel value for hardware-accelerated decoding.
    #   For AMD on Windows, d3d11va provides GPU-accelerated decoding alongside AMF encoding.
    _CANDIDATES = [
        ("h264_nvenc", "nvidia", "cuda"),
        ("h264_amf",   "amd",    "d3d11va"),   # AMD AMF (Windows) — d3d11va for decode
        ("h264_vaapi", "amd",    "vaapi"),      # AMD VAAPI (Linux)
        ("h264_qsv",   "intel",  "qsv"),        # Intel QuickSync
    ]

    def __init__(self):
        self._detected = False
        self.encoder: str = "libx264"  # CPU fallback
        self.decode_hwaccel: str = ""  # e.g. "cuda", "d3d11va", "vaapi", "qsv"
        self.gpu_type: str = "cpu"
        self.disabled: bool = False  # Set True to force CPU even when GPU detected

    def _test_encoder(self, encoder: str) -> bool:
        """Test if an encoder actually works by encoding a tiny synthetic clip.

        FFmpeg may list encoders that were compiled in but can't run without
        the right hardware/drivers (e.g. h264_nvenc without nvcuda.dll).
        """
        try:
            result = subprocess.run(
                [
                    "ffmpeg", "-y",
                    "-f", "lavfi", "-i", "color=black:s=64x64:d=0.1:r=1",
                    "-frames:v", "1",
                    "-c:v", encoder,
                    "-f", "null", "-",
                ],
                capture_output=True, text=True, timeout=15,
            )
            return result.returncode == 0
        except Exception:
            return False

    def _test_hwaccel_decode(self, hwaccel: str) -> bool:
        """Test if hardware-accelerated decoding actually works.

        Some FFmpeg builds have the hwaccel compiled in but the GPU/drivers
        don't support it (e.g. d3d11va on a machine without a GPU).
        """
        try:
            result = subprocess.run(
                [
                    "ffmpeg", "-y",
                    "-hwaccel", hwaccel,
                    "-f", "lavfi", "-i", "color=black:s=64x64:d=0.1:r=1",
                    "-frames:v", "1",
                    "-f", "null", "-",
                ],
                capture_output=True, text=True, timeout=15,
            )
            return result.returncode == 0
        except Exception:
            return False

    def detect(self):
        """Detect the best available GPU encoder and decoder.

        Iterates through candidates (NVIDIA → AMD AMF → AMD VAAPI → Intel QSV),
        checks if each is listed in `ffmpeg -encoders`, then test-encodes a
        single frame.  First one that passes wins.  Falls back to CPU libx264.
        Also tests hardware-accelerated decoding for the matched GPU.
        """
        if self._detected:
            return
        self._detected = True

        try:
            result = subprocess.run(
                ["ffmpeg", "-encoders"],
                capture_output=True, text=True, timeout=10,
            )
            encoders = result.stdout

            for encoder_name, gpu_label, hwaccel_decode in self._CANDIDATES:
                if encoder_name not in encoders:
                    continue
                logger.info(f"GPU detection: {encoder_name} found in FFmpeg, testing...")
                if self._test_encoder(encoder_name):
                    self.encoder = encoder_name
                    self.gpu_type = gpu_label

                    # Test hardware-accelerated decoding
                    if self._test_hwaccel_decode(hwaccel_decode):
                        self.decode_hwaccel = hwaccel_decode
                        logger.info(
                            f"GPU acceleration: {encoder_name} encoder + "
                            f"{hwaccel_decode} decoder verified — "
                            f"using {gpu_label.upper()} hardware encode+decode"
                        )
                    else:
                        logger.info(
                            f"GPU acceleration: {encoder_name} encoder verified, "
                            f"but {hwaccel_decode} decode not available — "
                            f"using {gpu_label.upper()} encode + CPU decode"
                        )
                    return
                else:
                    logger.info(
                        f"GPU detection: {encoder_name} listed but not functional "
                        f"(missing GPU/drivers) — skipping"
                    )

            logger.info("GPU acceleration: No working GPU encoder found — using CPU (libx264)")
        except Exception as e:
            logger.warning(f"GPU detection failed: {e} — using CPU encoding")

    def get_decode_flags(self) -> list[str]:
        """Get hardware-accelerated decoding flags.

        These flags go BEFORE ``-i`` in the FFmpeg command line.
        Returns empty list for CPU fallback (no hardware decode).

        Hardware decode offloads video decoding from CPU to GPU, which is
        critical for export performance — without it, FFmpeg maxes out CPU
        cores decoding input clips even when encoding uses GPU.
        """
        self.detect()
        if self.disabled:
            return []
        if self.decode_hwaccel:
            return ["-hwaccel", self.decode_hwaccel]
        return []

    def get_encode_flags(self, crf: int = 18) -> list[str]:
        """Get encoder flags for the detected GPU (or CPU fallback).

        Quality parameter mapping:
          NVIDIA NVENC:  -cq (constant quality, 0-51, lower=better)
          AMD AMF:       -qp_i/-qp_p (quantization, CQP mode)
          AMD VAAPI:     -qp (global quantization parameter)
          Intel QSV:     -global_quality (ICQ mode, 1-51)
          CPU libx264:   -crf (constant rate factor, 0-51)
        """
        self.detect()
        if self.disabled:
            return ["-c:v", "libx264", "-crf", str(crf), "-preset", "fast"]
        if self.encoder == "h264_nvenc":
            return ["-c:v", "h264_nvenc", "-preset", "p4", "-cq", str(crf)]
        elif self.encoder == "h264_amf":
            return [
                "-c:v", "h264_amf", "-quality", "balanced",
                "-rc", "cqp", "-qp_i", str(crf), "-qp_p", str(crf),
            ]
        elif self.encoder == "h264_vaapi":
            return ["-c:v", "h264_vaapi", "-qp", str(crf)]
        elif self.encoder == "h264_qsv":
            return [
                "-c:v", "h264_qsv", "-preset", "fast",
                "-global_quality", str(crf),
            ]
        else:
            return ["-c:v", "libx264", "-crf", str(crf), "-preset", "fast"]


# Singleton — detect once, reuse everywhere
_gpu = _GPUAccel()



def _run_ffmpeg(cmd: list, description: str = "") -> str:
    """
    Run FFmpeg command with error handling.

    Args:
        cmd: FFmpeg command list
        description: Description for logging

    Returns:
        stdout output

    Raises:
        RuntimeError: If command fails
    """
    logger.debug(f"Running: {' '.join(cmd)}")

    try:
        result = subprocess.run(
            cmd,
            check=True,
            capture_output=True,
            text=True,
            timeout=3600,  # 1 hour timeout
        )
        logger.debug(f"FFmpeg {description}: {result.stdout[:100]}")
        return result.stdout
    except subprocess.CalledProcessError as e:
        error_msg = e.stderr or str(e)
        raise RuntimeError(f"FFmpeg {description} failed: {error_msg}")
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"FFmpeg {description} timeout (>1 hour)")
    except FileNotFoundError:
        raise RuntimeError("FFmpeg not found. Install with: apt install ffmpeg")


def normalize_clip(
    input_path: str,
    output_path: str,
    width: int,
    height: int,
    fps: int = 24,
    skip_first_frame: bool = False,
    skip_head_frames: int = 0,
    max_duration: Optional[float] = None,
    crf: int = 23,
) -> None:
    """
    Normalize clip: scale+pad, set fps, convert to yuv420p, set SAR.

    Args:
        input_path: Input video path
        output_path: Output video path
        width: Target width
        height: Target height
        fps: Target framerate (default 24)
        skip_first_frame: If True, trim the first frame to avoid duplicate
            frames at LF-as-FF scene transitions. Ignored if skip_head_frames > 0.
        skip_head_frames: Number of frames to skip from the start. Used for
            V2V overlap removal where frame matching determines the exact
            number of overlap frames to drop. Takes precedence over
            skip_first_frame.
        max_duration: If set, trim the output to at most this many seconds.
            Essential for V2V videos whose raw duration (with video tail)
            exceeds the scene duration.

    Raises:
        RuntimeError: If processing fails
    """
    # skip_head_frames takes precedence over skip_first_frame
    effective_skip = skip_head_frames if skip_head_frames > 0 else (1 if skip_first_frame else 0)
    skip_label = f" (skipping {effective_skip} head frames)" if effective_skip > 0 else ""
    dur_label = f", max_dur={max_duration}s" if max_duration else ""
    logger.info(f"Normalizing: {input_path} → {output_path} ({width}x{height}@{fps}fps){skip_label}{dur_label}")

    # Log source video properties for diagnostics
    try:
        src_info = get_media_info(input_path)
        logger.info(
            f"Source video props: {src_info.get('width')}x{src_info.get('height')} "
            f"@ {src_info.get('fps')}fps, duration={src_info.get('duration')}s, "
            f"codec={src_info.get('codec')}"
        )
    except Exception as e:
        logger.warning(f"Could not probe source video: {e}")

    # Build video filter chain.
    # IMPORTANT: Do NOT use the `fps` filter here — it pads the tail with a
    # duplicate of the last frame when input duration doesn't divide evenly
    # into the target fps.  Even with -frames:v trimming, the padding causes
    # timing jitter that propagates through re-encode passes and creates a
    # visible stutter at concat splice points.  Use `-r {fps}` as an OUTPUT
    # option instead — it sets the container framerate without duplicating.
    filters = []
    if effective_skip > 0:
        # Trim from frame N onward (skip first N frames) and reset timestamps.
        # skip_first_frame=True → effective_skip=1 (LF-as-FF duplicate frame).
        # skip_head_frames=N → effective_skip=N (V2V overlap removal).
        filters.append(f"trim=start_frame={effective_skip}")
        filters.append("setpts=PTS-STARTPTS")
    filters.append(f"scale={width}:{height}:force_original_aspect_ratio=decrease")
    filters.append(f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2")
    filters.append("setsar=1")

    vf = ",".join(filters)

    # Limit output to exact frame count instead of -t (time-based).
    # -t {duration} is ambiguous at frame boundaries: at 24fps a 7.0s clip
    # could include the frame at PTS=7.000 (frame 168), which is essentially
    # a duplicate of frame 167 (only 42ms later).  This extra near-duplicate
    # at the tail of every clip creates a visible stutter at each splice.
    # -frames:v N gives exact control: floor(duration * fps) = the precise
    # number of frames that fit within the scene duration.
    frame_limit_flags: list[str] = []
    if max_duration:
        # round() instead of int() — int() truncates, so floating-point
        # imprecision like 168.9999 → 168 loses a frame.  round() gives
        # the correct 169.
        requested_frames = round(max_duration * fps)

        # Clamp to actual source frame count to avoid last-frame
        # duplication.  If the source has fewer frames than requested
        # (e.g. trim_video produced 167 instead of 168), the encoder
        # duplicates the last frame to fill the gap — visible as a
        # "same frame twice" motion jerk at each splice point.
        #
        # When skip_first_frame is True, the trim=start_frame=1 filter
        # removes frame 0 BEFORE encoding, so only (src_count - 1)
        # frames are actually available to the encoder.  We must
        # account for this lost frame in the clamp calculation.
        src_frame_count = count_video_frames(input_path)
        if effective_skip > 0 and src_frame_count > 0:
            src_frame_count -= effective_skip  # N frames removed by trim filter
            logger.info(f"skip_head_frames={effective_skip}: effective source frames = {src_frame_count}")
        if src_frame_count > 0 and src_frame_count < requested_frames:
            logger.info(
                f"Source has {src_frame_count} frames, requested {requested_frames} "
                f"— clamping to source count to avoid last-frame duplication"
            )
            exact_frames = src_frame_count
        else:
            exact_frames = requested_frames

        frame_limit_flags = ["-frames:v", str(exact_frames)]
        logger.info(f"Frame-exact trim: {exact_frames} frames for {max_duration:.3f}s @ {fps}fps")

    cmd = [
        "ffmpeg",
        *_gpu.get_decode_flags(),
        "-i", input_path,
        "-vf", vf,
        # -r sets the output container framerate.  Source frame count is
        # clamped above to prevent the encoder from ever duplicating the
        # last frame to fill a gap.
        "-r", str(fps),
        *_gpu.get_encode_flags(crf=crf),
        "-pix_fmt", "yuv420p",
        # Force keyframe at start, regular keyframe interval every 1 second
        # (limits P-frame reference chain length — long chains accumulate
        # prediction artifacts that create a visible "pop" at splice points),
        # disable B-frames (prevents encoder flush dropping last 1-2 frames),
        # and consistent timescale for clean concat.
        "-force_key_frames", "expr:eq(n,0)",
        "-g", str(fps),
        "-bf", "0",
        "-video_track_timescale", str(fps * 1000),
        *frame_limit_flags,
        # Strip audio — export assembly muxes the master audio track at the
        # very end, so per-clip audio is irrelevant.
        "-an",
        "-y",
        output_path,
    ]

    _run_ffmpeg(cmd, "normalize")

    # Verify output duration matches expectations
    try:
        out_info = get_media_info(output_path)
        out_dur = out_info.get("duration", 0)
        if max_duration:
            expected_dur = round(max_duration * fps) / fps
            logger.info(
                f"Normalized: {output_path} (duration={out_dur:.3f}s, "
                f"target={max_duration:.3f}s, frames={round(max_duration * fps)})"
            )
            if abs(out_dur - expected_dur) > 0.5:
                logger.warning(
                    f"Duration mismatch after normalize! "
                    f"Got {out_dur:.3f}s, expected ~{expected_dur:.3f}s"
                )
        else:
            logger.info(f"Normalized: {output_path} (duration={out_dur:.3f}s)")
    except Exception:
        logger.info(f"Normalized: {output_path}")


def pad_video_end(input_path: str, output_path: str, pad_seconds: float, crf: int = 18) -> None:
    """
    Extend a video by holding its last frame for `pad_seconds`.

    Uses FFmpeg's tpad filter to freeze the final frame, keeping
    resolution and codec identical.

    Args:
        input_path: Input video path
        output_path: Output video path
        pad_seconds: Seconds of freeze-frame padding to add

    Raises:
        RuntimeError: If processing fails
    """
    if pad_seconds <= 0:
        # Nothing to pad — just copy
        import shutil
        shutil.copy2(input_path, output_path)
        return

    logger.info(f"Padding video end by {pad_seconds:.2f}s: {input_path}")

    # Probe input fps for consistent timing flags
    try:
        info = get_media_info(input_path)
        fps = int(round(info.get("fps", 24)))
    except Exception:
        fps = 24

    # tpad stop_mode=clone holds the last frame; stop_duration sets how long
    cmd = [
        "ffmpeg",
        *_gpu.get_decode_flags(),
        "-i", input_path,
        "-vf", f"tpad=stop_mode=clone:stop_duration={pad_seconds}",

        "-r", str(fps),
        *_gpu.get_encode_flags(crf=crf),
        "-pix_fmt", "yuv420p",
        "-force_key_frames", "expr:eq(n,0)",
        "-g", str(fps),
        "-bf", "0",
        "-video_track_timescale", str(fps * 1000),
        "-an",
        "-y",
        output_path,
    ]

    _run_ffmpeg(cmd, "pad_end")
    logger.info(f"Padded: {output_path}")


def trim_clip(input_path: str, output_path: str, start_sec: float, end_sec: float) -> None:
    """
    Trim video to specified range (frame-accurate).

    Args:
        input_path: Input video path
        output_path: Output video path
        start_sec: Start time in seconds
        end_sec: End time in seconds

    Raises:
        RuntimeError: If processing fails
    """
    duration = end_sec - start_sec
    logger.info(f"Trimming: {start_sec}s → {end_sec}s (duration {duration}s)")

    cmd = [
        "ffmpeg",
        *_gpu.get_decode_flags(),
        "-ss", str(start_sec),
        "-i", input_path,
        "-t", str(duration),
        "-c:v", "copy",
        "-c:a", "copy",
        "-y",
        output_path,
    ]

    _run_ffmpeg(cmd, "trim")
    logger.info(f"Trimmed: {output_path}")


def pad_clip(input_path: str, output_path: str, target_duration: float) -> None:
    """
    Pad clip with last frame clone to reach target duration.

    Args:
        input_path: Input video path
        output_path: Output video path
        target_duration: Target duration in seconds

    Raises:
        RuntimeError: If processing fails
    """
    logger.info(f"Padding: {input_path} to {target_duration}s")

    # Get input duration
    info = get_media_info(input_path)
    input_duration = info["duration"]

    if input_duration >= target_duration:
        logger.info(f"Clip already {input_duration}s, no padding needed")
        subprocess.run(["cp", input_path, output_path], check=True)
        return

    pad_duration = target_duration - input_duration

    cmd = [
        "ffmpeg",
        *_gpu.get_decode_flags(),
        "-i", input_path,
        "-vf", f"tpad=stop_mode=clone:stop_duration={pad_duration}",
        *_gpu.get_encode_flags(),
        "-c:a", "aac",
        "-y",
        output_path,
    ]

    _run_ffmpeg(cmd, "pad")
    logger.info(f"Padded: {output_path}")


def concat_clips(clip_paths: list, output_path: str, fps: int = 24, crf: int = 18) -> None:
    """
    Concatenate video clips using the FFmpeg concat VIDEO FILTER.

    Uses the concat filter (re-encodes) instead of the concat demuxer
    (-c copy) because clips may have been through different processing
    stages (normalize → color correction) that produce different internal
    timescales, GOP structures, and metadata.  The concat demuxer with
    -c copy is extremely fragile about these mismatches and causes speed
    glitches / frozen frames.  The concat filter decodes all inputs and
    re-encodes through a single unified pipeline, eliminating ALL timing
    inconsistencies.

    Output option ``-r {fps}`` sets the container framerate without
    resampling pixel data.  Do NOT use ``-vsync cfr`` — when all clips
    are already at the target fps, it over-processes and can create
    ghosted/blended frames at clip boundaries.

    Args:
        clip_paths: List of input clip paths
        output_path: Output video path
        fps: Target framerate for the output (default 24)

    Raises:
        RuntimeError: If processing fails
    """
    if not clip_paths:
        raise ValueError("No clips to concatenate")

    n = len(clip_paths)
    logger.info(f"Concatenating {n} clips (concat filter, re-encode, {fps}fps)")

    # Log each clip's duration for diagnostics
    total_dur = 0.0
    for i, cp in enumerate(clip_paths):
        try:
            info = get_media_info(cp)
            dur = info.get("duration", 0)
            total_dur += dur
            logger.info(f"  Clip {i}: {dur:.2f}s  {cp}")
        except Exception:
            logger.warning(f"  Clip {i}: could not probe  {cp}")

    logger.info(f"  Expected total duration: {total_dur:.2f}s")

    # Build inputs
    inputs: list[str] = []
    for cp in clip_paths:
        inputs.extend(["-i", cp])

    # Build concat filter with setpts reset after joining.
    # setpts=PTS-STARTPTS normalises the output timeline, eliminating any
    # accumulated micro-timestamp jitter from the multiple re-encode passes
    # (normalize → color correction → concat) that causes a 1-frame
    # "step back" artefact at splice points.
    #
    # IMPORTANT: Do NOT use the fps= filter here.  It resamples frame
    # timing at clip boundaries and creates blended/ghosted frames at
    # splice points.  Instead, -r as an OUTPUT option sets the container
    # framerate without resampling pixel data.  Do NOT use -vsync cfr
    # either — when clips are already at the target fps (e.g. LTX 24fps
    # → export 24fps), it over-processes and causes the same ghosting.
    filter_inputs = "".join(f"[{i}:v]" for i in range(n))
    filter_complex = (
        f"{filter_inputs}concat=n={n}:v=1:a=0,"
        f"setpts=PTS-STARTPTS[v]"
    )

    cmd = [
        "ffmpeg",
        *_gpu.get_decode_flags(),
        *inputs,
        "-filter_complex", filter_complex,
        "-map", "[v]",
        "-r", str(fps),
        *_gpu.get_encode_flags(crf=crf),
        "-pix_fmt", "yuv420p",
        "-force_key_frames", "expr:eq(n,0)",
        "-g", str(fps),
        "-bf", "0",
        "-video_track_timescale", str(fps * 1000),
        "-movflags", "+faststart",
        "-y",
        output_path,
    ]

    _run_ffmpeg(cmd, "concat")

    # Verify output duration
    try:
        out_info = get_media_info(output_path)
        out_dur = out_info.get("duration", 0)
        logger.info(f"Concatenated: {output_path} ({out_dur:.2f}s, expected ~{total_dur:.2f}s)")
        if total_dur > 0 and abs(out_dur - total_dur) > 1.0:
            logger.warning(
                f"Duration mismatch! Output {out_dur:.2f}s vs expected {total_dur:.2f}s "
                f"(diff={out_dur - total_dur:.2f}s)"
            )
    except Exception:
        logger.info(f"Concatenated: {output_path}")


def mux_audio(video_path: str, audio_path: str, output_path: str) -> None:
    """
    Mux audio into video (use -shortest to match shorter stream).

    Args:
        video_path: Input video path
        audio_path: Input audio path
        output_path: Output video path

    Raises:
        RuntimeError: If processing fails
    """
    logger.info(f"Muxing audio: {video_path} + {audio_path}")

    cmd = [
        "ffmpeg",
        *_gpu.get_decode_flags(),
        "-i", video_path,
        "-i", audio_path,
        "-map", "0:v:0",   # take video from input 0
        "-map", "1:a:0",   # take audio from input 1 (NOT input 0's existing audio)
        "-c:v", "copy",
        "-c:a", "aac",
        "-b:a", "128k",
        "-shortest",
        "-y",
        output_path,
    ]

    _run_ffmpeg(cmd, "mux")
    logger.info(f"Muxed: {output_path}")


def apply_kenburns(
    image_path: str,
    output_path: str,
    duration: float,
    width: int,
    height: int,
    effect: str = "zoom_in_center",
    intensity: int = 50,
    easing: str = "ease_in_out",
    fps: int = 24,
    crf: int = 18,
) -> None:
    """
    Apply Ken Burns / pan / zoom effect to image with 8x upscaling trick.

    Effects:
    - zoom_in_center, zoom_out_center: Zoom in/out from center
    - zoom_in_top_left/top_right/bottom_left/bottom_right: Zoom toward corners
    - pan_left, pan_right, pan_up, pan_down: Linear pans
    - pan_left_to_right, pan_right_to_left: Full-width pans
    - zoom_in_pan_left/right, zoom_out_pan_left/right: Combo moves

    Args:
        image_path: Input image path
        output_path: Output video path
        duration: Video duration in seconds
        width: Output width
        height: Output height
        effect: Effect preset name
        intensity: 0-100 controlling how extreme the effect is
        easing: Easing curve — linear, ease_in, ease_out, ease_in_out
        fps: Output framerate

    Raises:
        RuntimeError: If processing fails
    """
    logger.info(f"Applying Ken Burns ({effect}, intensity={intensity}, easing={easing}): {duration}s @ {width}x{height}")

    # Scale intensity factor: 0-100 → 0.0-1.0
    t = max(0, min(100, intensity)) / 100.0

    # Zoom range based on intensity: subtle (1.1) to dramatic (1.5)
    max_zoom = 1.0 + (0.5 * t)
    # Zoom increment per frame
    total_frames = round(duration * fps)
    zoom_inc = (max_zoom - 1.0) / max(total_frames, 1)
    zoom_dec = zoom_inc  # same magnitude for zoom out

    # Pan zoom level (static zoom for pan effects)
    pan_zoom = 1.0 + (0.3 * t)

    # Build zoompan expressions for each effect
    # 'progress' = on/n where on = frame number, n = total frames
    # We define it inline since zoompan doesn't have a built-in progress var
    progress = f"(on/{total_frames})"

    # Easing functions applied to progress (0→1)
    if easing == "ease_in":
        p = f"({progress}*{progress})"
    elif easing == "ease_out":
        p = f"(1-(1-{progress})*(1-{progress}))"
    elif easing == "ease_in_out":
        # smoothstep: 3p² - 2p³
        p = f"(3*{progress}*{progress}-2*{progress}*{progress}*{progress})"
    else:
        p = progress  # linear

    effects_map = {
        # Zoom effects
        "zoom_in_center": f"z='1+{max_zoom - 1}*{p}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'",
        "zoom_out_center": f"z='{max_zoom}-{max_zoom - 1}*{p}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'",
        "zoom_in_top_left": f"z='1+{max_zoom - 1}*{p}':x='0':y='0'",
        "zoom_in_top_right": f"z='1+{max_zoom - 1}*{p}':x='iw-iw/zoom':y='0'",
        "zoom_in_bottom_left": f"z='1+{max_zoom - 1}*{p}':x='0':y='ih-ih/zoom'",
        "zoom_in_bottom_right": f"z='1+{max_zoom - 1}*{p}':x='iw-iw/zoom':y='ih-ih/zoom'",

        # Pan effects (constant zoom, moving x or y)
        "pan_left": f"z='{pan_zoom}':x='(iw-iw/zoom)*{p}':y='ih/2-ih/zoom/2'",
        "pan_right": f"z='{pan_zoom}':x='(iw-iw/zoom)*(1-{p})':y='ih/2-ih/zoom/2'",
        "pan_up": f"z='{pan_zoom}':x='iw/2-iw/zoom/2':y='(ih-ih/zoom)*{p}'",
        "pan_down": f"z='{pan_zoom}':x='iw/2-iw/zoom/2':y='(ih-ih/zoom)*(1-{p})'",
        "pan_left_to_right": f"z='{pan_zoom}':x='(iw-iw/zoom)*(1-{p})':y='ih/2-ih/zoom/2'",
        "pan_right_to_left": f"z='{pan_zoom}':x='(iw-iw/zoom)*{p}':y='ih/2-ih/zoom/2'",

        # Combo effects (zoom + pan simultaneously)
        "zoom_in_pan_left": f"z='1+{max_zoom - 1}*{p}':x='(iw-iw/zoom)*{p}':y='ih/2-ih/zoom/2'",
        "zoom_in_pan_right": f"z='1+{max_zoom - 1}*{p}':x='(iw-iw/zoom)*(1-{p})':y='ih/2-ih/zoom/2'",
        "zoom_out_pan_left": f"z='{max_zoom}-{max_zoom - 1}*{p}':x='(iw-iw/zoom)*{p}':y='ih/2-ih/zoom/2'",
        "zoom_out_pan_right": f"z='{max_zoom}-{max_zoom - 1}*{p}':x='(iw-iw/zoom)*(1-{p})':y='ih/2-ih/zoom/2'",
    }

    zoompan = effects_map.get(effect, effects_map["zoom_in_center"])

    # Use 8x upscale trick for better quality
    scale_factor = 8
    upscaled_width = width * scale_factor
    upscaled_height = height * scale_factor

    cmd = [
        "ffmpeg",
        *_gpu.get_decode_flags(),
        "-loop", "1",
        "-i", image_path,
        "-vf", f"scale={upscaled_width}:{upscaled_height},zoompan={zoompan}:d={total_frames}:s={upscaled_width}x{upscaled_height}:fps={fps},scale={width}:{height}",
        "-r", str(fps),
        *_gpu.get_encode_flags(crf=crf),
        "-pix_fmt", "yuv420p",
        "-force_key_frames", "expr:eq(n,0)",
        "-g", str(fps),
        "-bf", "0",
        "-video_track_timescale", str(fps * 1000),
        "-t", str(duration),
        "-an",
        "-y",
        output_path,
    ]

    _run_ffmpeg(cmd, "ken_burns")
    logger.info(f"Applied Ken Burns: {output_path}")


def crossfade(clip1_path: str, clip2_path: str, output_path: str, fade_duration: float = 1.0) -> None:
    """
    Crossfade between two clips (legacy wrapper for apply_transition).

    Args:
        clip1_path: First clip path
        clip2_path: Second clip path
        output_path: Output video path
        fade_duration: Fade duration in seconds
    """
    apply_transition(clip1_path, clip2_path, output_path, "crossfade", fade_duration)


def apply_transition(
    clip1_path: str,
    clip2_path: str,
    output_path: str,
    transition_type: str = "crossfade",
    fade_duration: float = 1.0,
    crf: int = 18,
) -> None:
    """
    Apply a transition between two clips using FFmpeg xfade filter.

    Supported transitions:
    - crossfade / dissolve: Standard fade between clips
    - fade_to_black / fade_from_black: Fade via black
    - fade_to_white / fade_from_white: Fade via white
    - wipe_left / wipe_right / wipe_up / wipe_down: Directional wipes
    - slide_left / slide_right: Slide transitions

    Args:
        clip1_path: First clip path (outgoing)
        clip2_path: Second clip path (incoming)
        output_path: Output video path
        transition_type: Transition preset name
        fade_duration: Transition duration in seconds

    Raises:
        RuntimeError: If processing fails
    """
    logger.info(f"Applying transition ({transition_type}, {fade_duration}s): {clip1_path} → {clip2_path}")

    # Map our transition names to FFmpeg xfade transition names
    xfade_map = {
        "crossfade": "fade",
        "dissolve": "dissolve",
        "fade_to_black": "fadeblack",
        "fade_from_black": "fadeblack",
        "fade_to_white": "fadewhite",
        "fade_from_white": "fadewhite",
        "wipe_left": "wipeleft",
        "wipe_right": "wiperight",
        "wipe_up": "wipeup",
        "wipe_down": "wipedown",
        "slide_left": "slideleft",
        "slide_right": "slideright",
    }

    xfade_name = xfade_map.get(transition_type, "fade")
    clip1_info = get_media_info(clip1_path)
    clip1_duration = clip1_info["duration"]
    fps = int(round(clip1_info.get("fps", 24)))
    offset = max(0, clip1_duration - fade_duration)

    cmd = [
        "ffmpeg",
        *_gpu.get_decode_flags(),
        "-i", clip1_path,
        "-i", clip2_path,
        "-filter_complex",
        f"[0][1]xfade=transition={xfade_name}:duration={fade_duration}:offset={offset}[v]",
        "-map", "[v]",

        "-r", str(fps),
        *_gpu.get_encode_flags(crf=crf),
        "-pix_fmt", "yuv420p",
        "-force_key_frames", "expr:eq(n,0)",
        "-g", str(fps),
        "-bf", "0",
        "-video_track_timescale", str(fps * 1000),
        "-y",
        output_path,
    ]

    _run_ffmpeg(cmd, f"transition_{transition_type}")
    logger.info(f"Applied transition: {output_path}")


def apply_fade_in(
    clip_path: str,
    output_path: str,
    fade_duration: float = 0.5,
    color: str = "black",
    crf: int = 18,
) -> None:
    """Apply a fade-in from black or white to the start of a clip.

    Args:
        clip_path: Input clip path
        output_path: Output clip path
        fade_duration: Duration of the fade in seconds
        color: 'black' or 'white'
        crf: Encoding quality (default 18)
    """
    logger.info(f"Applying fade-in ({color}, {fade_duration}s): {clip_path}")
    vf = f"fade=t=in:st=0:d={fade_duration}:color={color}"
    cmd = [
        "ffmpeg", *_gpu.get_decode_flags(), "-i", clip_path,
        "-vf", vf,

        *_gpu.get_encode_flags(crf=crf), "-pix_fmt", "yuv420p",
        "-c:a", "copy", "-y", output_path,
    ]
    _run_ffmpeg(cmd, "fade_in")


def apply_fade_out(
    clip_path: str,
    output_path: str,
    fade_duration: float = 0.5,
    color: str = "black",
    crf: int = 18,
) -> None:
    """Apply a fade-out to black or white at the end of a clip.

    Args:
        clip_path: Input clip path
        output_path: Output clip path
        fade_duration: Duration of the fade in seconds
        color: 'black' or 'white'
        crf: Encoding quality (default 18)
    """
    logger.info(f"Applying fade-out ({color}, {fade_duration}s): {clip_path}")
    clip_duration = get_media_info(clip_path)["duration"]
    fade_start = max(0, clip_duration - fade_duration)
    vf = f"fade=t=out:st={fade_start}:d={fade_duration}:color={color}"
    cmd = [
        "ffmpeg", *_gpu.get_decode_flags(), "-i", clip_path,
        "-vf", vf,

        *_gpu.get_encode_flags(crf=crf), "-pix_fmt", "yuv420p",
        "-c:a", "copy", "-y", output_path,
    ]
    _run_ffmpeg(cmd, "fade_out")


def count_video_frames(path: str) -> int:
    """Count the exact number of video frames using ffprobe.

    Uses ``nb_read_packets`` (requires decoding) for accuracy — container
    metadata (``nb_frames``) is often wrong or missing.

    Args:
        path: Video file path.

    Returns:
        Exact frame count, or 0 on failure.
    """
    try:
        cmd = [
            "ffprobe",
            "-v", "error",
            "-select_streams", "v:0",
            "-count_packets",
            "-show_entries", "stream=nb_read_packets",
            "-of", "csv=p=0",
            path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if result.returncode == 0 and result.stdout.strip():
            return int(result.stdout.strip())
    except Exception as e:
        logger.warning(f"count_video_frames failed for {path}: {e}")
    return 0


def get_media_info(path: str) -> Dict[str, Any]:
    """
    Get media file information using ffprobe.

    Args:
        path: Media file path

    Returns:
        Dict with: duration, width, height, fps, codec

    Raises:
        RuntimeError: If ffprobe fails
    """
    try:
        cmd = [
            "ffprobe",
            "-v", "error",
            "-show_entries", "format=duration:stream=width,height,r_frame_rate,codec_type",
            "-of", "json",
            path,
        ]

        result = subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=30)
        data = json.loads(result.stdout)

        # Extract values
        duration = float(data["format"].get("duration", 0))

        video_stream = next(
            (s for s in data.get("streams", []) if s.get("codec_type") == "video"),
            {}
        )

        width = video_stream.get("width", 1280)
        height = video_stream.get("height", 720)

        # Parse fps
        fps_str = video_stream.get("r_frame_rate", "30/1")
        if "/" in fps_str:
            num, denom = map(int, fps_str.split("/"))
            fps = num / denom if denom > 0 else 30
        else:
            fps = float(fps_str) if fps_str else 30

        return {
            "duration": duration,
            "width": width,
            "height": height,
            "fps": fps,
            "codec": video_stream.get("codec_name", "unknown"),
        }

    except Exception as e:
        raise RuntimeError(f"ffprobe failed for {path}: {e}")


def get_video_stream_duration(path: str) -> float:
    """Get the VIDEO STREAM duration (not container/format duration).

    The container ``format.duration`` equals the maximum of all stream
    durations. When a video has an audio track that is longer than the
    video track (common with LTX audio-conditioned outputs), seeking to
    ``format.duration - 1/fps`` overshoots the video content and
    ``extract_frame`` produces no output.

    This function queries the video stream's duration directly. Falls
    back to ``format.duration`` if the stream duration is unavailable.
    """
    try:
        cmd = [
            "ffprobe",
            "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=duration",
            "-show_entries", "format=duration",
            "-of", "json",
            path,
        ]
        result = subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=30)
        data = json.loads(result.stdout)

        # Try video stream duration first
        for stream in data.get("streams", []):
            dur = stream.get("duration")
            if dur is not None:
                return float(dur)

        # Fall back to format (container) duration
        fmt_dur = data.get("format", {}).get("duration")
        if fmt_dur is not None:
            return float(fmt_dur)

        return 0.0
    except Exception as e:
        logger.warning(f"get_video_stream_duration failed for {path}: {e}")
        return 0.0


def extract_frame(video_path: str, output_path: str, time_sec: float) -> None:
    """
    Extract single frame from video at specified time.

    Uses input seeking (-ss before -i) for speed. If that produces no output
    file (can happen near end of video), falls back to output seeking
    (-ss after -i) which is slower but more reliable.

    Args:
        video_path: Input video path
        output_path: Output image path
        time_sec: Time in seconds

    Raises:
        RuntimeError: If extraction fails
    """
    logger.info(f"Extracting frame at {time_sec}s: {output_path}")

    # Attempt 1: input seeking (fast)
    cmd = [
        "ffmpeg",
        *_gpu.get_decode_flags(),
        "-ss", str(time_sec),
        "-i", video_path,
        "-vframes", "1",
        "-q:v", "2",
        "-y",
        output_path,
    ]

    _run_ffmpeg(cmd, "extract_frame")

    # Verify output was actually produced — input seeking can exit 0
    # without writing a file when seeking near end of video
    if not Path(output_path).exists():
        logger.warning(
            f"Input seeking produced no output at {time_sec}s, "
            f"retrying with output seeking"
        )
        # Attempt 2: output seeking (-ss after -i, slower but reliable)
        cmd_fallback = [
            "ffmpeg",
            *_gpu.get_decode_flags(),
            "-i", video_path,
            "-ss", str(time_sec),
            "-vframes", "1",
            "-q:v", "2",
            "-y",
            output_path,
        ]
        _run_ffmpeg(cmd_fallback, "extract_frame (output seeking)")

    if not Path(output_path).exists():
        raise RuntimeError(
            f"Frame extraction failed: no output at {output_path} "
            f"(video={video_path}, time={time_sec}s)"
        )

    logger.info(f"Frame extracted: {output_path}")


def _ensure_frame_dimensions(
    frame_path: str,
    target_width: int,
    target_height: int,
) -> None:
    """Correct a frame image to match the target dimensions exactly.

    LTX video models may output at slightly different dimensions than
    requested (due to internal latent-space alignment — often dimensions
    must be divisible by 32 or 64).  For example, requesting 1536×864 may
    produce 1536×896 video (896 = 64×14, but 864 is not divisible by 64).

    When the extracted frame is used as the next scene's first frame input,
    any dimension mismatch causes visible jumps at scene boundaries.

    Strategy:
      1. If aspect ratios are close (same width, height off by < 10%):
         CENTER-CROP to the target height.  This preserves content
         proportions — no stretching or squishing.
      2. If significantly different: scale-to-fit then center-crop.
    """
    try:
        from PIL import Image as PILImage
        with PILImage.open(frame_path) as img:
            fw, fh = img.size
            if fw == target_width and fh == target_height:
                logger.info(f"Frame dimensions {fw}x{fh} match target — no correction needed")
                return

            logger.warning(
                f"Frame dimensions {fw}x{fh} differ from target "
                f"{target_width}x{target_height} — correcting"
            )

            # Determine correction strategy
            width_ratio = fw / target_width
            height_ratio = fh / target_height

            if abs(width_ratio - 1.0) < 0.05 and abs(height_ratio - 1.0) < 0.10:
                # Dimensions are close — use center-crop (preserves proportions)
                # Scale to ensure we cover the target dimensions, then crop
                scale = max(target_width / fw, target_height / fh)
                if abs(scale - 1.0) > 0.001:
                    scaled_w = round(fw * scale)
                    scaled_h = round(fh * scale)
                    img = img.resize((scaled_w, scaled_h), PILImage.Resampling.LANCZOS)
                    fw, fh = scaled_w, scaled_h

                # Center-crop to exact target
                left = (fw - target_width) // 2
                top = (fh - target_height) // 2
                cropped = img.crop((left, top, left + target_width, top + target_height))
                cropped.save(frame_path)
                logger.info(
                    f"Frame center-cropped to {target_width}x{target_height} "
                    f"(removed {fh - target_height}px height, {fw - target_width}px width)"
                )
            else:
                # Significantly different — scale to fit then crop
                scale = max(target_width / fw, target_height / fh)
                scaled_w = round(fw * scale)
                scaled_h = round(fh * scale)
                img = img.resize((scaled_w, scaled_h), PILImage.Resampling.LANCZOS)

                left = (scaled_w - target_width) // 2
                top = (scaled_h - target_height) // 2
                cropped = img.crop((left, top, left + target_width, top + target_height))
                cropped.save(frame_path)
                logger.info(
                    f"Frame scaled ({fw}x{fh} → {scaled_w}x{scaled_h}) and "
                    f"center-cropped to {target_width}x{target_height}"
                )
    except Exception as e:
        logger.warning(f"Failed to verify/correct frame dimensions: {e}")


def extract_last_frame(
    video_path: str,
    output_path: str,
    target_width: int = 0,
    target_height: int = 0,
) -> None:
    """
    Extract the last frame from a video.

    Calls get_media_info() to get video duration and FPS, then extracts the
    frame at (duration - 1/fps) seconds for accurate last-frame capture.
    Falls back to seeking to the end if duration is 0 or unknown.

    If target_width and target_height are provided, the extracted frame is
    resized to exactly match those dimensions.  This is important for
    scene-to-scene continuity: LTX may output at slightly different
    dimensions than requested (8-pixel alignment), and without correction
    the distortion compounds across scenes.

    Args:
        video_path: Input video path
        output_path: Output image path
        target_width: Expected width (0 = don't resize)
        target_height: Expected height (0 = don't resize)

    Raises:
        RuntimeError: If extraction fails
    """
    logger.info(f"Extracting last frame: {output_path}")

    try:
        info = get_media_info(video_path)
        duration = info.get("duration", 0)
        fps = info.get("fps", 24)

        if duration > 0:
            # Use FPS-aware offset: seek to one frame before the end
            frame_duration = 1.0 / max(fps, 1)
            time_sec = max(0, duration - frame_duration)
            logger.info(f"Video duration: {duration}s, fps: {fps}, extracting at {time_sec:.4f}s")
        else:
            # Fall back to seeking to the very end
            logger.warning(f"Unknown duration for {video_path}, seeking to end")
            time_sec = -1  # Special handling: use -1 to trigger end-seek

        if time_sec == -1:
            # Seek to end by using a high timestamp
            extract_frame(video_path, output_path, 999999.0)
        else:
            extract_frame(video_path, output_path, time_sec)

        # Ensure extracted frame matches target dimensions exactly
        if target_width > 0 and target_height > 0:
            _ensure_frame_dimensions(output_path, target_width, target_height)
        else:
            # Just log the dimensions for debugging
            try:
                from PIL import Image as PILImage
                with PILImage.open(output_path) as img:
                    fw, fh = img.size
                    logger.info(f"Last frame dimensions: {fw}x{fh}")
            except Exception as e:
                logger.warning(f"Could not read last frame dimensions: {e}")

        logger.info(f"Last frame extracted: {output_path}")
    except Exception as e:
        logger.error(f"Failed to extract last frame: {e}")
        raise


def slice_audio(audio_path: str, output_path: str, start_sec: float, end_sec: float) -> None:
    """
    Extract audio segment.

    Args:
        audio_path: Input audio path
        output_path: Output audio path
        start_sec: Start time in seconds
        end_sec: End time in seconds

    Raises:
        RuntimeError: If slicing fails
    """
    duration = end_sec - start_sec
    logger.info(f"Slicing audio: {start_sec}s → {end_sec}s")

    cmd = [
        "ffmpeg",
        *_gpu.get_decode_flags(),
        "-ss", str(start_sec),
        "-i", audio_path,
        "-t", str(duration),
        "-c:a", "copy",
        "-y",
        output_path,
    ]

    _run_ffmpeg(cmd, "slice_audio")
    logger.info(f"Audio sliced: {output_path}")


def trim_video(
    input_path: str,
    output_path: str,
    duration: float,
    skip_first_frame: bool = False,
) -> str:
    """Trim a video to exactly ``duration`` seconds via frame-exact re-encode.

    Uses ``-frames:v N`` with a full re-encode instead of stream copy
    (``-c:v copy``).  Stream copy operates on packet/GOP boundaries, not
    frame boundaries — if the source video contains B-frames (common in
    LTX / ComfyUI outputs), the trim can leave corrupted trailing frames
    whose P/B references point beyond the cut.  Those corrupt boundary
    frames then propagate through normalize → concat in the export
    pipeline, creating a visible "flash" or "jerk" at scene splice
    points.  They also corrupt V2V conditioning for the next scene.

    Re-encoding is slightly slower but guarantees every frame is cleanly
    decodable, with no B-frames (``-bf 0``) and a keyframe at the start.

    Args:
        input_path: Path to the source video.
        output_path: Path for the trimmed output.
        duration: Target duration in seconds.
        skip_first_frame: If True, remove frame 0 before trimming.
            Used for V2V and use_prev_lf_as_ff scenes where frame 0
            is a duplicate of the previous scene's last frame.  The
            output still has exactly ``round(duration * fps)`` frames
            — frame 0 is discarded and the next N frames are kept.

    Returns:
        The output_path.
    """
    # Probe source fps for frame-exact calculation
    try:
        info = get_media_info(input_path)
        fps = int(round(info.get("fps", 24)))
    except Exception:
        fps = 24

    exact_frames = round(duration * fps)

    skip_label = " (skipping duplicate frame 0)" if skip_first_frame else ""
    logger.info(
        f"Trimming video to {duration}s ({exact_frames} frames @ {fps}fps){skip_label}: "
        f"{input_path} → {output_path}"
    )

    # Build video filter chain
    vf_filters = []
    if skip_first_frame:
        # Remove frame 0 (duplicate of previous scene's last frame for
        # V2V / use_prev_lf_as_ff scenes) and reset timestamps.
        # The -frames:v limit below still requests exact_frames frames,
        # which now come from frames 1..N of the source instead of 0..N-1.
        vf_filters.append("trim=start_frame=1")
        vf_filters.append("setpts=PTS-STARTPTS")

    vf_flags = ["-vf", ",".join(vf_filters)] if vf_filters else []

    cmd = [
        "ffmpeg",
        *_gpu.get_decode_flags(),
        "-i", input_path,
        *vf_flags,
        "-frames:v", str(exact_frames),
        "-r", str(fps),
        *_gpu.get_encode_flags(crf=18),
        "-pix_fmt", "yuv420p",
        "-force_key_frames", "expr:eq(n,0)",
        "-g", str(fps),
        "-bf", "0",
        "-video_track_timescale", str(fps * 1000),
        "-c:a", "copy",
        "-y",
        output_path,
    ]

    _run_ffmpeg(cmd, "trim_video")
    logger.info(f"Video trimmed: {output_path}")
    return output_path


# ── Frame-Matching Overlap Detection ─────────────────────────────────────
# Brute-force best-frame-match between the tail of clip A and the head of
# clip B.  Replaces the old sequence-matching approach with a more robust
# single-best-pair comparison across a wide search window.


def _read_frames_cv2(
    video_path: str,
    start_frame: int,
    count: int,
) -> list[tuple[int, "np.ndarray"]]:
    """Read a range of frames from a video via OpenCV.

    Returns list of (absolute_frame_index, BGR ndarray) tuples.
    Uses seek + sequential read, with a full-sequential fallback for
    codecs that lie about seek positions.
    """
    import cv2
    import numpy as np

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        return []

    out: list[tuple[int, np.ndarray]] = []
    try:
        cap.set(cv2.CAP_PROP_POS_FRAMES, start_frame)
        idx = start_frame
        while len(out) < count:
            ok, frame = cap.read()
            if not ok or frame is None:
                break
            out.append((idx, frame))
            idx += 1
    except Exception:
        out = []
    cap.release()

    # Sequential fallback if seek produced too few frames
    if len(out) < count:
        cap = cv2.VideoCapture(video_path)
        buf: list[tuple[int, np.ndarray]] = []
        i = 0
        while True:
            ok, frame = cap.read()
            if not ok or frame is None:
                break
            if i >= start_frame:
                buf.append((i, frame))
                if len(buf) >= count:
                    break
            i += 1
        cap.release()
        if len(buf) > len(out):
            out = buf

    return out


def find_best_frame_match(
    clip_a_path: str,
    clip_b_path: str,
    a_tail_frames: int = 240,
    b_head_frames: int = 1,
    max_mse: float = 2500.0,
    a_max_frame: int = 0,
) -> dict | None:
    """Find where Video A should be trimmed so Video B continues seamlessly.

    Compares B's first frame (frame 0) against A's tail frames to find
    the exact point where A should end.  B is NEVER cut — we only trim A.

    This replicates the Robomuffin Scene Frame Tools Video Joiner algorithm:
    find the overlap point, trim A to end just before it, B stays intact.

    Example: A = ABCDEFGH, B = FGHIJKL
      → B[0]='F' matches A[5]='F', MSE ≈ 0
      → Trim A to frames 0..4 (= ABCDE, 5 frames), B stays as FGHIJKL
      → Concat: ABCDE + FGHIJKL = ABCDEFGHIJKL (F appears once, from B)

    Args:
        clip_a_path: Path to the outgoing (first) video clip.
        clip_b_path: Path to the incoming (second) video clip.
        a_tail_frames: How many frames from A's tail to compare
            (default 240 — ~10 seconds at 24fps).
        b_head_frames: How many frames from B's head to compare
            (default 1 — only B's first frame).
        max_mse: Maximum MSE to accept as a valid match (default 2500).
            Pairs above this are considered non-overlapping.
        a_max_frame: If > 0, treat A as if it only has this many frames.
            The tail search window is computed relative to this limit
            instead of A's actual frame count.  Used when A's raw video
            is longer than its scene duration (video tail overshoot) and
            we only want to search within the scene-duration portion.

    Returns:
        Dict with keys: a_frame (index in A where B[0] matches),
        b_frame (always 0), mse, a_trim_frames (number of frames to
        keep from A = a_frame, so A ends just BEFORE the match),
        a_total, b_total.  Or None if no acceptable match is found.
    """
    import numpy as np

    try:
        count_a = count_video_frames(clip_a_path)
        count_b = count_video_frames(clip_b_path)

        if count_a == 0 or count_b == 0:
            logger.warning("Cannot count frames for overlap detection, skipping")
            return None

        # If a_max_frame is set, pretend A only has that many frames.
        # This constrains the search to within A's scene duration when
        # the raw video has overshoot frames beyond the scene boundary.
        effective_a = min(a_max_frame, count_a) if a_max_frame > 0 else count_a

        tail_count = min(a_tail_frames, effective_a)
        head_count = min(b_head_frames, count_b)
        tail_start = effective_a - tail_count

        logger.info(
            f"Frame match: reading last {tail_count} frames of A "
            f"(effective {effective_a} of {count_a} total) and first "
            f"{head_count} frame(s) of B ({count_b} total)"
        )

        frames_a = _read_frames_cv2(clip_a_path, tail_start, tail_count)
        frames_b = _read_frames_cv2(clip_b_path, 0, head_count)

        if not frames_a or not frames_b:
            logger.warning("No frames extracted for overlap detection")
            return None

        logger.info(
            f"Frame match: comparing {len(frames_a)} A-tail frames "
            f"against {len(frames_b)} B-head frame(s)"
        )

        # Compare B's first frame(s) against A's tail frames
        best: dict | None = None
        for a_idx, a_frame in frames_a:
            for b_idx, b_frame in frames_b:
                if a_frame.shape != b_frame.shape:
                    continue
                diff = a_frame.astype(np.int32) - b_frame.astype(np.int32)
                mse = float(np.mean(diff * diff))
                if best is None or mse < best["mse"]:
                    best = {"a_frame": a_idx, "b_frame": b_idx, "mse": mse}

        if best is None:
            logger.info("No comparable frame pairs found")
            return None

        if best["mse"] > max_mse:
            logger.info(
                f"Best match MSE {best['mse']:.2f} exceeds threshold {max_mse}, "
                f"no overlap detected"
            )
            return None

        # A should be trimmed to end just BEFORE the match frame.
        # A[a_frame] matches B[0], so A keeps frames 0..a_frame-1.
        # B stays fully intact starting from frame 0.
        # Example: A=ABCDEFGH, match at A[5]='F'
        #   → keep A[0..4] = ABCDE (a_trim_frames = 5)
        #   → B = FGHIJKL (untouched)
        #   → concat = ABCDEFGHIJKL
        best["a_trim_frames"] = best["a_frame"]  # number of frames to KEEP from A
        best["a_total"] = count_a
        best["b_total"] = count_b

        avg_diff = float(np.sqrt(best["mse"]))
        logger.info(
            f"Best frame match: B[{best['b_frame']}] matches A[{best['a_frame']}] "
            f"MSE={best['mse']:.2f} (avg diff {avg_diff:.1f}/255). "
            f"Trim A to {best['a_trim_frames']} frames (was {count_a}). "
            f"B stays intact ({count_b} frames)."
        )

        return best

    except ImportError as e:
        logger.warning(f"Missing dependency for frame matching: {e}")
        return None
    except Exception as e:
        logger.warning(f"Frame match detection failed: {e}")
        return None


def v2v_join_and_split(
    clip_a_path: str,
    clip_b_path: str,
    a_match_frame: int,
    b_match_frame: int,
    split_frame: int,
    fps: int = 24,
    output_b_path: str = "",
    crf: int = 14,
) -> str:
    """Join two V2V clips at match point, split at scene boundary, return B's pre-cut source.

    Replicates the Robomuffin Scene Frame Tools Video Joiner algorithm:
    1. Join: A[0..a_match] + B[b_match+1..end] into a seamless video
    2. Split the joined video at split_frame (scene A's frame count)
    3. Return the second part — scene B's source with overlap head removed

    The joined video is created via FFmpeg concat filter in a single pass.
    The split produces Part B which starts exactly where scene A ends in
    the joined timeline, guaranteeing zero overlap and zero gap.

    Args:
        clip_a_path: Path to scene A's raw (untrimmed) video.
        clip_b_path: Path to scene B's raw (untrimmed) video.
        a_match_frame: Frame index in A where the best match was found.
        b_match_frame: Frame index in B where the best match was found.
        split_frame: Frame index at which to split (= round(scene_A_duration * fps)).
        fps: Target framerate.
        output_b_path: Where to write Part B. If empty, auto-generates next to clip_b.
        crf: Encoding quality (default 14 for near-lossless intermediate).

    Returns:
        Path to Part B (scene B's pre-cut video source).
    """
    if not output_b_path:
        b_stem = Path(clip_b_path).stem
        output_b_path = str(Path(clip_b_path).parent / f"{b_stem}_v2v_precut.mp4")

    # Frames to keep from A: 0..a_match (inclusive) = a_match+1 frames
    a_keep = a_match_frame + 1
    # Frames to skip from B head: 0..b_match (inclusive)
    b_skip = b_match_frame + 1

    logger.info(
        f"V2V join-and-split: A[0..{a_match_frame}] ({a_keep} frames) + "
        f"B[{b_skip}..end] → joined → split at frame {split_frame} → Part B"
    )

    # Step 1: Create the joined video via concat filter.
    # A contributes frames 0..a_match, B contributes frames b_match+1..end.
    # The concat filter decodes both, joins, and re-encodes — handling all
    # timing/format differences internally.
    joined_path = str(Path(clip_b_path).parent / f"{b_stem}_v2v_joined.mp4")

    join_cmd = [
        "ffmpeg",
        *_gpu.get_decode_flags(),
        "-i", clip_a_path,
        "-i", clip_b_path,
        "-filter_complex",
        (
            f"[0:v]trim=end_frame={a_keep},setpts=PTS-STARTPTS[va];"
            f"[1:v]trim=start_frame={b_skip},setpts=PTS-STARTPTS[vb];"
            f"[va][vb]concat=n=2:v=1:a=0[v]"
        ),
        "-map", "[v]",
        "-r", str(fps),
        *_gpu.get_encode_flags(crf=crf),
        "-pix_fmt", "yuv420p",
        "-force_key_frames", "expr:eq(n,0)",
        "-g", str(fps),
        "-bf", "0",
        "-video_track_timescale", str(fps * 1000),
        "-an",
        "-y",
        joined_path,
    ]
    _run_ffmpeg(join_cmd, "v2v_join")

    joined_frames = count_video_frames(joined_path)
    logger.info(f"V2V joined video: {joined_frames} frames at {joined_path}")

    if split_frame >= joined_frames:
        logger.warning(
            f"Split frame {split_frame} >= joined frame count {joined_frames}, "
            f"cannot split — returning joined as-is for Part B"
        )
        import shutil
        shutil.copy2(joined_path, output_b_path)
        return output_b_path

    # Step 2: Extract Part B — everything from split_frame onwards.
    # This is scene B's pre-cut source with the overlap head removed.
    part_b_frames = joined_frames - split_frame

    split_cmd = [
        "ffmpeg",
        *_gpu.get_decode_flags(),
        "-i", joined_path,
        "-vf", f"trim=start_frame={split_frame},setpts=PTS-STARTPTS",
        "-frames:v", str(part_b_frames),
        "-r", str(fps),
        *_gpu.get_encode_flags(crf=crf),
        "-pix_fmt", "yuv420p",
        "-force_key_frames", "expr:eq(n,0)",
        "-g", str(fps),
        "-bf", "0",
        "-video_track_timescale", str(fps * 1000),
        "-an",
        "-y",
        output_b_path,
    ]
    _run_ffmpeg(split_cmd, "v2v_split_b")

    out_frames = count_video_frames(output_b_path)
    logger.info(
        f"V2V Part B: {out_frames} frames at {output_b_path} "
        f"(split at frame {split_frame} of {joined_frames})"
    )

    # Clean up joined intermediate
    try:
        Path(joined_path).unlink(missing_ok=True)
    except Exception:
        pass

    return output_b_path


def trim_head_frames(
    input_path: str,
    output_path: str,
    skip_frames: int,
    fps: int = 24,
    crf: int = 14,
) -> str:
    """Trim N frames from the start of a video clip.

    Used after overlap detection to remove the overlapping head frames
    from clip B before concatenating with clip A.

    Args:
        input_path: Input video path.
        output_path: Output video path.
        skip_frames: Number of frames to skip from the start.
        fps: Target framerate.
        crf: Encoding quality (default 14 for intermediate).

    Returns:
        Output path.
    """
    src_frame_count = count_video_frames(input_path)
    remaining_frames = src_frame_count - skip_frames

    if remaining_frames <= 0:
        logger.warning(
            f"Cannot trim {skip_frames} frames from {src_frame_count}-frame clip, "
            f"skipping trim"
        )
        import shutil
        shutil.copy2(input_path, output_path)
        return output_path

    logger.info(
        f"Trimming {skip_frames} head frames: {src_frame_count} → {remaining_frames} "
        f"frames: {input_path} → {output_path}"
    )

    vf = f"trim=start_frame={skip_frames},setpts=PTS-STARTPTS"

    cmd = [
        "ffmpeg",
        *_gpu.get_decode_flags(),
        "-i", input_path,
        "-vf", vf,
        "-frames:v", str(remaining_frames),
        "-r", str(fps),
        *_gpu.get_encode_flags(crf=crf),
        "-pix_fmt", "yuv420p",
        "-force_key_frames", "expr:eq(n,0)",
        "-g", str(fps),
        "-bf", "0",
        "-video_track_timescale", str(fps * 1000),
        "-an",
        "-y",
        output_path,
    ]

    _run_ffmpeg(cmd, "trim_head_frames")
    logger.info(f"Head frames trimmed: {output_path} ({remaining_frames} frames)")
    return output_path


def trim_tail_frames(
    input_path: str,
    output_path: str,
    keep_frames: int,
    fps: int = 24,
    crf: int = 14,
) -> str:
    """Keep only the first N frames of a video clip (trim the tail).

    Used after overlap detection to remove frames after the match point
    from clip A.

    Args:
        input_path: Input video path.
        output_path: Output video path.
        keep_frames: Number of frames to keep from the start.
        fps: Target framerate.
        crf: Encoding quality (default 14 for intermediate).

    Returns:
        Output path.
    """
    src_frame_count = count_video_frames(input_path)

    if keep_frames >= src_frame_count:
        logger.info(
            f"No tail trim needed: keeping {keep_frames} of {src_frame_count} frames"
        )
        import shutil
        shutil.copy2(input_path, output_path)
        return output_path

    dropped = src_frame_count - keep_frames
    logger.info(
        f"Trimming {dropped} tail frames: {src_frame_count} → {keep_frames} "
        f"frames: {input_path} → {output_path}"
    )

    cmd = [
        "ffmpeg",
        *_gpu.get_decode_flags(),
        "-i", input_path,
        "-frames:v", str(keep_frames),
        "-r", str(fps),
        *_gpu.get_encode_flags(crf=crf),
        "-pix_fmt", "yuv420p",
        "-force_key_frames", "expr:eq(n,0)",
        "-g", str(fps),
        "-bf", "0",
        "-video_track_timescale", str(fps * 1000),
        "-an",
        "-y",
        output_path,
    ]

    _run_ffmpeg(cmd, "trim_tail_frames")
    logger.info(f"Tail frames trimmed: {output_path} ({keep_frames} frames)")
    return output_path
