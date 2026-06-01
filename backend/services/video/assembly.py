"""
Video Assembly Pipeline

Final video assembly: clip normalization, concatenation, and audio muxing.

Performance optimizations (v1.6.0):
- Single-pass FFmpeg filter graphs: normalize+pad+fade+color in ONE call per clip
  (was 3-5 separate decode→encode cycles per scene)
- Parallel clip processing: ThreadPoolExecutor for independent clip rendering
- Stream-copy concat: -f concat -c copy when no transitions (no re-encode)
- Threading flags: -threads 0 -filter_threads 4 for all FFmpeg calls
- Pre-computed transition compensation: padding folded into single-pass
- Tmpfs intermediate files: /dev/shm on Linux for RAM-backed I/O
"""

import logging
import os
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import List, Dict, Any, Optional, Callable

from .ffmpeg import (
    normalize_clip,
    concat_clips,
    concat_clips_copy,
    mux_audio,
    apply_transition,
    get_media_info,
    pad_video_end,
    process_clip_single_pass,
    process_image_single_pass,
    mix_audio_tracks,
    normalize_audio,
    generate_ass_subtitles,
    burn_subtitles,
)
from .color_correction import match_adjacent_clips

logger = logging.getLogger(__name__)

# Maximum parallel FFmpeg workers.  Each FFmpeg process can use multiple
# CPU cores (via -threads 0), so too many workers starve each other.
# 4 is a safe default that keeps GPU encoders fed without thrashing.
_MAX_PARALLEL_CLIPS = int(os.environ.get("RBMN_PARALLEL_CLIPS", "4"))

# Minimum free space (bytes) required on tmpfs before we'll use it.
# 512 MB — a 20-scene export with CRF 14 intermediates can easily use 200+ MB.
_TMPFS_MIN_FREE = int(os.environ.get("RBMN_TMPFS_MIN_FREE", str(512 * 1024 * 1024)))


# ─── Persistent export cache (audio-only remix support) ──────────────────
# The expensive part of an export is the per-clip render + chunk merge,
# producing a single silent concatenated video (concat.mp4 / chunkmerge).
# When the user re-exports the same project with only audio tweaks
# (narration volume, backing track levels, etc.), we want to skip
# clip rendering entirely and jump straight to the audio mux step.
#
# Strategy: after every successful concat we save the merged silent video
# AND a manifest of the video-affecting params to a persistent cache dir
# next to the output. On the next export we compute the same hash and, if
# it matches, skip the entire render pipeline and reuse the cached concat.
#
# The "audio-only" flag forces use of the cache (errors if missing).
# The "force_recreate" flag wipes the cache before starting.

import hashlib as _cache_hashlib


def _video_cache_key(
    scenes: List[Dict[str, Any]],
    width: int,
    height: int,
    fps: int,
    intermediate_crf: int,
    final_crf: int,
    default_transition: Optional[str],
    default_transition_duration: float,
    color_match_clips: bool,
    ai_transition_clips: Optional[Dict[int, str]] = None,
) -> str:
    """Hash all params that affect the silent concatenated video.

    Audio-only params (narration_volume, backing_volume, fades,
    normalize_audio, subtitles, etc.) are deliberately excluded — they're
    applied AFTER the cached concat is reused.
    """
    payload = {
        "scenes": [
            {
                "src": s.get("scene_source_type"),
                "img": s.get("image_path"),
                "vid": s.get("video_path"),
                "dur": s.get("duration"),
                "fx": s.get("image_movement") or s.get("effect"),
                "tin": s.get("transition_in"),
                "tout": s.get("transition_out"),
                "tclip": s.get("transition_clip_path"),
            }
            for s in scenes
        ],
        "geom": [width, height, fps],
        "crf": [intermediate_crf, final_crf],
        "trans": [default_transition, default_transition_duration],
        "cm": color_match_clips,
        "ait": (ai_transition_clips or {}),
    }
    blob = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    return _cache_hashlib.sha256(blob).hexdigest()[:16]


def _export_cache_dir(output_path: str) -> Path:
    """Cache lives alongside the output, hidden from gallery listing."""
    return Path(output_path).parent / ".export_cache"


def _load_cached_concat(
    output_path: str, expected_key: str
) -> Optional[Path]:
    """Return path to cached concat.mp4 if and only if the manifest matches."""
    cdir = _export_cache_dir(output_path)
    concat = cdir / "concat.mp4"
    manifest = cdir / "manifest.json"
    if not concat.exists() or not manifest.exists():
        return None
    try:
        m = json.loads(manifest.read_text())
        if m.get("video_cache_key") != expected_key:
            return None
        if not concat.stat().st_size > 0:
            return None
    except Exception:
        return None
    return concat


def _save_concat_to_cache(
    output_path: str, concat_path: Path, key: str, scene_count: int
) -> None:
    """Copy the merged silent video into the cache + write manifest.

    Best-effort: failures are logged but don't break the export.
    """
    try:
        cdir = _export_cache_dir(output_path)
        cdir.mkdir(parents=True, exist_ok=True)
        target = cdir / "concat.mp4"
        shutil.copy2(str(concat_path), str(target))
        (cdir / "manifest.json").write_text(json.dumps({
            "video_cache_key": key,
            "scene_count": scene_count,
            "saved_at": datetime.utcnow().isoformat() + "Z",
        }, indent=2))
        logger.info(f"Export cache saved: {target} (key={key})")
    except Exception as e:
        logger.warning(f"Failed to write export cache: {e}")


def _clear_export_cache(output_path: str) -> None:
    """Wipe the cache directory entirely (used by force_recreate)."""
    cdir = _export_cache_dir(output_path)
    if cdir.exists():
        try:
            shutil.rmtree(str(cdir), ignore_errors=True)
            logger.info(f"Export cache cleared: {cdir}")
        except Exception as e:
            logger.warning(f"Failed to clear export cache: {e}")


def _safe_stem_name(name: str) -> str:
    """Strip path/extension noise and keep the bit safe for a filename."""
    base = Path(name).stem if name else ""
    # Replace anything that isn't alnum / underscore / dash with underscore.
    out = "".join(c if (c.isalnum() or c in "-_") else "_" for c in base)
    return (out or "track").strip("_") or "track"


def _export_audio_stems(
    output_path: str,
    narration_audio_path: str,
    backing_tracks: Optional[List[Dict[str, Any]]],
    narration_volume: float,
    backing_volume: float,
    loop_backing: bool,
    main_fade_in: float,
    main_fade_out: float,
    normalize_backing: bool,
    total_duration: float,
    individual_backing: bool = False,
) -> None:
    """Write per-channel WAVs for DAW remixing.

    Produces (under ``{output_dir}/stems/``):
        {basename}.narration.wav          — narration with master volume
        {basename}.backing_mix.wav        — all backing tracks combined
        {basename}.backing_NN_name.wav    — each backing track separately
                                            (only when individual_backing=True)

    Best-effort: failures are logged but don't break the export.
    """
    try:
        out_p = Path(output_path)
        stems_dir = out_p.parent / "stems"
        stems_dir.mkdir(parents=True, exist_ok=True)
        base = out_p.stem

        # ── narration stem: narration audio with volume applied ──
        narr_out = stems_dir / f"{base}.narration.wav"
        narr_filter = f"volume={narration_volume:.4f}"
        subprocess.run([
            "ffmpeg", "-y",
            "-i", narration_audio_path,
            "-af", narr_filter,
            "-c:a", "pcm_s16le",
            "-ar", "48000",
            str(narr_out),
        ], check=True, capture_output=True)
        logger.info(f"Stem written: {narr_out}")

        if not backing_tracks:
            return

        # ── individual backing track stems (one WAV per track) ──
        if individual_backing:
            for idx, bt in enumerate(backing_tracks, start=1):
                try:
                    src = bt.get("path")
                    if not src or not Path(src).exists():
                        logger.warning(
                            f"Individual backing stem #{idx}: source missing — {src}"
                        )
                        continue
                    raw_name = bt.get("name") or bt.get("filename") or Path(src).name
                    pretty = _safe_stem_name(str(raw_name))
                    bt_out = stems_dir / f"{base}.backing_{idx:02d}_{pretty}.wav"
                    vol = float(bt.get("volume_db", 0))
                    vol_linear = (10 ** (vol / 20.0)) * float(backing_volume)
                    start_ms = int(float(bt.get("start_time", 0)) * 1000)
                    filt_parts = []
                    if start_ms > 0:
                        filt_parts.append(f"adelay={start_ms}|{start_ms}")
                    filt_parts.append(f"volume={vol_linear:.4f}")
                    filt = ",".join(filt_parts) or "anull"
                    subprocess.run([
                        "ffmpeg", "-y",
                        "-i", src,
                        "-af", filt,
                        "-c:a", "pcm_s16le",
                        "-ar", "48000",
                        str(bt_out),
                    ], check=True, capture_output=True)
                    logger.info(f"Individual backing stem written: {bt_out}")
                except Exception as _ind_err:
                    logger.warning(
                        f"Individual backing stem #{idx} failed (non-fatal): {_ind_err}"
                    )

        # ── backing_mix: all backing tracks summed into one stem ──
        back_out = stems_dir / f"{base}.backing_mix.wav"
        cmd = ["ffmpeg", "-y"]
        for bt in backing_tracks:
            cmd += ["-i", bt["path"]]
        filter_parts = []
        for i, bt in enumerate(backing_tracks):
            vol = float(bt.get("volume_db", 0))
            vol_linear = (10 ** (vol / 20.0)) * float(backing_volume)
            start_ms = int(float(bt.get("start_time", 0)) * 1000)
            delay_filter = f"adelay={start_ms}|{start_ms}" if start_ms > 0 else "anull"
            filter_parts.append(
                f"[{i}:a]{delay_filter},volume={vol_linear:.4f}[a{i}]"
            )
        mix_inputs = "".join(f"[a{i}]" for i in range(len(backing_tracks)))
        filter_parts.append(
            f"{mix_inputs}amix=inputs={len(backing_tracks)}:duration=longest:dropout_transition=0[mix]"
        )
        cmd += [
            "-filter_complex", ";".join(filter_parts),
            "-map", "[mix]",
            "-c:a", "pcm_s16le",
            "-ar", "48000",
            str(back_out),
        ]
        subprocess.run(cmd, check=True, capture_output=True)
        logger.info(f"Stem written: {back_out}")
    except Exception as e:
        logger.warning(f"Stems export failed (non-fatal): {e}")


def _get_tmpfs_dir(fallback: Path, label: str = "rbmn_export") -> Path:
    """Pick the best directory for intermediate files.

    On Linux, /dev/shm is a tmpfs mount backed by RAM — reads and writes
    are dramatically faster than spinning disk or even SSD.  We use it
    when available and when there's enough free space; otherwise we fall
    back to the output directory (``fallback``).

    The env var ``RBMN_TMPFS_DIR`` overrides auto-detection (useful for
    custom ramdisks on Windows/macOS).

    Returns a per-export subdirectory (created, caller must clean up).
    """
    override = os.environ.get("RBMN_TMPFS_DIR", "")
    candidates: list[str] = []
    if override:
        candidates.append(override)
    else:
        # /dev/shm is the standard Linux tmpfs
        candidates.append("/dev/shm")

    for candidate in candidates:
        cpath = Path(candidate)
        if not cpath.is_dir():
            continue
        try:
            stat = os.statvfs(str(cpath))
            free = stat.f_bavail * stat.f_frsize
            if free < _TMPFS_MIN_FREE:
                logger.info(
                    f"tmpfs {candidate}: only {free / (1024*1024):.0f} MB free "
                    f"(need {_TMPFS_MIN_FREE / (1024*1024):.0f} MB), skipping"
                )
                continue
        except (OSError, AttributeError):
            # os.statvfs not available on Windows; skip candidate
            continue

        tmpdir = cpath / f"{label}_{os.getpid()}"
        try:
            tmpdir.mkdir(parents=True, exist_ok=True)
            # Quick write test
            test_file = tmpdir / ".write_test"
            test_file.write_text("ok")
            test_file.unlink()
            logger.info(
                f"Using tmpfs for intermediates: {tmpdir} "
                f"({free / (1024*1024):.0f} MB free)"
            )
            return tmpdir
        except OSError as e:
            logger.warning(f"tmpfs {candidate} write test failed: {e}")
            continue

    # Fallback: use a subdirectory of the output folder
    fallback_dir = fallback / "_tmp"
    fallback_dir.mkdir(parents=True, exist_ok=True)
    return fallback_dir


def _cleanup_tmpfs_dir(tmpdir: Path, output_dir: Path) -> None:
    """Remove the tmpfs working directory if it's outside the output tree.

    If tmpdir is a subdirectory of output_dir (the fallback case), we
    still remove it since it's our own _tmp folder.
    """
    try:
        if tmpdir.exists():
            shutil.rmtree(str(tmpdir), ignore_errors=True)
    except Exception as e:
        logger.warning(f"tmpfs cleanup failed for {tmpdir}: {e}")


def _build_clip_task(
    scene: dict,
    scene_idx: int,
    work_dir: Path,
    width: int,
    height: int,
    fps: int,
    crf: int,
    pad_seconds: float,
) -> Optional[dict]:
    """Build a clip task descriptor for a single scene.

    Args:
        work_dir: Directory for intermediate clip files.  May be a tmpfs
                  mount (RAM-backed) for faster I/O.

    Returns a dict with all info needed to render the clip, or None if
    the scene has no content (should be skipped).
    """
    source_type = scene.get("scene_source_type", "image")
    duration = scene.get("duration", 5.0)
    clip_path = str(work_dir / f"clip_{scene_idx:03d}.mp4")

    # Extract fade parameters
    transition_in = scene.get("transition_in")
    transition_out = scene.get("transition_out")
    fade_in_type = None
    fade_in_dur = 0.5
    fade_out_type = None
    fade_out_dur = 0.5
    if transition_in and transition_in.get("type") in ("fade_from_black", "fade_from_white"):
        fade_in_type = transition_in["type"]
        fade_in_dur = transition_in.get("duration", 0.5)
    if transition_out and transition_out.get("type") in ("fade_to_black", "fade_to_white"):
        fade_out_type = transition_out["type"]
        fade_out_dur = transition_out.get("duration", 0.5)

    common = {
        "scene_idx": scene_idx,
        "clip_path": clip_path,
        "width": width,
        "height": height,
        "fps": fps,
        "crf": crf,
        "duration": duration,
        "pad_seconds": pad_seconds,
        "fade_in_type": fade_in_type,
        "fade_in_dur": fade_in_dur,
        "fade_out_type": fade_out_type,
        "fade_out_dur": fade_out_dur,
    }

    if source_type == "video":
        video_path = scene.get("video_path")
        if not video_path:
            return None
        common["type"] = "video"
        common["input_path"] = video_path
        common["skip_first_frame"] = bool(scene.get("trim_first_frame", False))
        return common
    else:
        image_path = scene.get("image_path")
        if image_path:
            # Image source with Ken Burns
            movement = scene.get("image_movement", {})
            if movement and movement.get("effect"):
                effect = movement["effect"]
                intensity = movement.get("intensity", 50)
                easing = movement.get("easing", "ease_in_out")
            else:
                effect = scene.get("effect", "zoom_in_center")
                intensity = 50
                easing = "ease_in_out"
            if not effect or effect == "none":
                effect = "zoom_in_center"
                intensity = 0

            common["type"] = "image"
            common["input_path"] = image_path
            common["effect"] = effect
            common["intensity"] = intensity
            common["easing"] = easing
            return common
        else:
            # Fallback to video_path
            video_path = scene.get("video_path")
            if not video_path:
                return None
            common["type"] = "video"
            common["input_path"] = video_path
            common["skip_first_frame"] = False
            return common


def _execute_clip_task(task: dict) -> dict:
    """Execute a single clip rendering task. Thread-safe (FFmpeg subprocess).

    Returns the task dict with 'result_path' set to the final clip path.
    """
    clip_path = task["clip_path"]

    # Skip if clip already exists AND is valid (resume support)
    if Path(clip_path).exists() and Path(clip_path).stat().st_size > 0:
        try:
            info = get_media_info(clip_path)
            if info.get("duration", 0) > 0:
                logger.info(f"Reusing existing clip: {clip_path}")
                task["result_path"] = clip_path
                task["extra_temp_files"] = []
                return task
            else:
                logger.warning(f"Existing clip has zero duration, re-rendering: {clip_path}")
                Path(clip_path).unlink()
        except Exception:
            logger.warning(f"Existing clip is corrupt (no moov atom?), re-rendering: {clip_path}")
            Path(clip_path).unlink(missing_ok=True)

    extra_temps: list[str] = []

    if task["type"] == "video":
        process_clip_single_pass(
            task["input_path"], clip_path,
            task["width"], task["height"],
            fps=task["fps"],
            skip_first_frame=task.get("skip_first_frame", False),
            max_duration=task["duration"],
            crf=task["crf"],
            pad_seconds=task["pad_seconds"],
            fade_in_type=task["fade_in_type"],
            fade_in_duration=task["fade_in_dur"],
            fade_out_type=task["fade_out_type"],
            fade_out_duration=task["fade_out_dur"],
        )
    else:
        # image type
        process_image_single_pass(
            task["input_path"], clip_path,
            task["duration"],
            task["width"], task["height"],
            effect=task["effect"],
            intensity=task["intensity"],
            easing=task["easing"],
            fps=task["fps"],
            crf=task["crf"],
            pad_seconds=task["pad_seconds"],
            fade_in_type=task["fade_in_type"],
            fade_in_duration=task["fade_in_dur"],
            fade_out_type=task["fade_out_type"],
            fade_out_duration=task["fade_out_dur"],
        )

    # Safety-net duration pad
    final_path = clip_path
    try:
        _clip_info = get_media_info(clip_path)
        _clip_dur = _clip_info.get("duration", 0)
        _target = task["duration"] + task["pad_seconds"]
        _shortfall = _target - _clip_dur
        if _shortfall > (1.0 / task["fps"]):
            logger.warning(
                f"Scene {task['scene_idx']} clip is {_shortfall:.3f}s shorter "
                f"than target — re-padding"
            )
            dur_pad_path = str(Path(clip_path).parent / f"clip_{task['scene_idx']:03d}_durpad.mp4")
            pad_video_end(clip_path, dur_pad_path, _shortfall, crf=task["crf"])
            extra_temps.append(clip_path)
            final_path = dur_pad_path
    except Exception as e:
        logger.warning(f"Scene {task['scene_idx']} duration check failed: {e}")

    task["result_path"] = final_path
    task["extra_temp_files"] = extra_temps
    return task


def _process_clips_parallel(
    tasks: list[dict],
    report: Optional[Callable] = None,
    cancel_check: Optional[Callable[[], bool]] = None,
    total_scenes: int = 0,
    base_percent: int = 0,
    percent_range: int = 60,
) -> tuple[list[str], list[int], list[str]]:
    """Process clip tasks in parallel using ThreadPoolExecutor.

    FFmpeg subprocesses release the GIL, so ThreadPoolExecutor gives true
    parallelism without the overhead of ProcessPoolExecutor serialization.

    Args:
        tasks: List of clip task dicts from _build_clip_task
        report: Progress callback
        cancel_check: Cancellation check
        total_scenes: Total scene count for progress calculation
        base_percent: Starting progress percentage
        percent_range: Progress range allocated to clip processing

    Returns:
        (scene_clips, clip_scene_indices, temp_files) — ordered by scene index
    """
    if not tasks:
        return [], [], []

    scene_clips: list[str] = []
    clip_scene_indices: list[int] = []
    temp_files: list[str] = []

    # For small numbers of clips, sequential is fine (less overhead)
    max_workers = min(_MAX_PARALLEL_CLIPS, len(tasks))
    if max_workers <= 1:
        max_workers = 1

    logger.info(f"Processing {len(tasks)} clips with {max_workers} parallel workers")

    completed = 0
    total = len(tasks)

    if max_workers == 1:
        # Sequential path — simpler, preserves order naturally
        for task in tasks:
            if cancel_check and cancel_check():
                raise RuntimeError("Export cancelled by user")

            pct = base_percent + int((completed / max(total_scenes, total)) * percent_range)
            if report:
                report(f"Rendering clip {completed + 1}/{total}...", pct)

            result = _execute_clip_task(task)
            scene_clips.append(result["result_path"])
            clip_scene_indices.append(result["scene_idx"])
            temp_files.append(result["result_path"])
            temp_files.extend(result.get("extra_temp_files", []))
            completed += 1
    else:
        # Parallel path — submit all tasks, collect in order
        # We use a dict to maintain original order since futures complete
        # in arbitrary order
        future_to_task: dict = {}
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            for task in tasks:
                if cancel_check and cancel_check():
                    raise RuntimeError("Export cancelled by user")
                future = executor.submit(_execute_clip_task, task)
                future_to_task[future] = task

            # Collect results as they complete (for progress reporting)
            results_by_idx: dict[int, dict] = {}
            for future in as_completed(future_to_task):
                if cancel_check and cancel_check():
                    executor.shutdown(wait=False, cancel_futures=True)
                    raise RuntimeError("Export cancelled by user")

                result = future.result()  # raises if task failed
                results_by_idx[result["scene_idx"]] = result
                completed += 1

                pct = base_percent + int((completed / max(total_scenes, total)) * percent_range)
                if report:
                    report(f"Rendered {completed}/{total} clips...", pct)

        # Reconstruct in original scene order
        for task in tasks:
            idx = task["scene_idx"]
            result = results_by_idx[idx]
            scene_clips.append(result["result_path"])
            clip_scene_indices.append(idx)
            temp_files.append(result["result_path"])
            temp_files.extend(result.get("extra_temp_files", []))

    return scene_clips, clip_scene_indices, temp_files


def _chunked_transition_merge(
    scene_clips: list[str],
    clip_scene_indices: list[int],
    scenes: list[dict],
    xfade_types_set: set,
    default_transition: str,
    default_transition_duration: float,
    use_default_xfade: bool,
    has_xfade: bool,
    ai_transition_clips: dict[int, str],
    output_dir: Path,
    fps: int,
    intermediate_crf: int,
    final_crf: int,
    chunk_size: int = 0,
    chunk_callback: Optional[Callable] = None,
    cancel_check: Optional[Callable[[], bool]] = None,
    report: Optional[Callable] = None,
    report_base_percent: int = 70,
    report_range: int = 18,
    chunk_output_dir: Optional[Path] = None,
) -> tuple[Path, list[str], list[str]]:
    """Merge clips with transitions, optionally in chunks.

    Args:
        output_dir: Working directory for merge intermediates (may be tmpfs).
        chunk_output_dir: Durable directory for chunk files that survive cleanup.
            If None, falls back to output_dir.

    Returns (final_merged_path, chunk_file_paths, temp_files_created).
    """
    # Chunk files must survive tmpfs cleanup for export recovery/gallery
    _chunk_dir = chunk_output_dir or output_dir
    temp_files: list[str] = []
    chunk_file_paths: list[str] = []

    def _do_report(step: str, percent: int) -> None:
        if report:
            report(step, percent)

    def _resolve_transition(ci_out: int, ci_in: int) -> tuple[Optional[str], float]:
        """Resolve transition type and duration for boundary between clip ci_out and ci_in."""
        si_in = clip_scene_indices[ci_in]
        si_prev = clip_scene_indices[ci_out]
        scene_in = scenes[si_in]
        scene_prev = scenes[si_prev]

        t_in = scene_in.get("transition_in", {}) or {}
        t_out_prev = scene_prev.get("transition_out", {}) or {}

        t_type = None
        t_dur = 0.5
        if t_in.get("type") in xfade_types_set:
            t_type = t_in["type"]
            t_dur = t_in.get("duration", 0.5)
        elif t_out_prev.get("type") in xfade_types_set:
            t_type = t_out_prev["type"]
            t_dur = t_out_prev.get("duration", 0.5)

        if not t_type and use_default_xfade:
            t_type = default_transition
            t_dur = default_transition_duration

        return t_type, t_dur

    def _sequential_merge(clips: list[str], clip_indices: list[int],
                          ai_clips: dict[int, str], label: str = "",
                          progress_offset: int = 0, progress_range: int = 18) -> str:
        """Merge a list of clips sequentially with transitions. Returns path to merged result."""
        if len(clips) == 1:
            return clips[0]

        total_transitions = len(clips) - 1
        merged = clips[0]
        for idx in range(1, len(clips)):
            ci_global = clip_indices[idx]
            ci_prev_global = clip_indices[idx - 1]

            t_percent = progress_offset + int((idx / total_transitions) * progress_range)
            _do_report(f"Applying transition {label}{idx}/{total_transitions}...", t_percent)

            # Check for AI transition clip at this boundary
            ai_clip = ai_clips.get(ci_prev_global)
            if ai_clip:
                cat_path = output_dir / f"cat_ai_{ci_global:03d}.mp4"
                concat_clips([merged, ai_clip, clips[idx]], str(cat_path), fps=fps,
                             crf=intermediate_crf)
                temp_files.append(str(cat_path))
                merged = str(cat_path)
                logger.info(f"Inserted AI transition clip at boundary {ci_prev_global}→{ci_global}")
                continue

            t_type, t_dur = _resolve_transition(ci_prev_global, ci_global)

            if t_type:
                xfade_path = output_dir / f"xfade_{ci_global:03d}.mp4"
                apply_transition(merged, clips[idx], str(xfade_path), t_type, t_dur,
                                 crf=intermediate_crf)
                temp_files.append(str(xfade_path))
                merged = str(xfade_path)
            else:
                cat_path = output_dir / f"cat_{ci_global:03d}.mp4"
                concat_clips([merged, clips[idx]], str(cat_path), fps=fps,
                             crf=intermediate_crf)
                temp_files.append(str(cat_path))
                merged = str(cat_path)

        return merged

    # Decide whether to use chunked or sequential merge
    use_chunking = chunk_size > 0 and len(scene_clips) > chunk_size

    if not use_chunking:
        # Legacy path: sequential merge of all clips (or simple concat)
        if (has_xfade or ai_transition_clips) and len(scene_clips) > 1:
            _do_report("Applying transitions...", report_base_percent)
            if use_default_xfade:
                logger.info(
                    "Applying default %s transition (%.1fs) between %d clips",
                    default_transition, default_transition_duration, len(scene_clips),
                )
            # Map local indices to global clip indices for AI clip lookup
            merged_path = _sequential_merge(
                scene_clips, list(range(len(scene_clips))),
                ai_transition_clips, progress_offset=report_base_percent,
                progress_range=report_range,
            )
            return Path(merged_path), chunk_file_paths, temp_files
        else:
            _do_report("Concatenating clips (stream copy)...", report_base_percent + 5)
            concat_path = output_dir / "concatenated.mp4"
            # Use stream-copy concat (no re-encode) when clips are uniform.
            # Falls back to filter concat automatically if format mismatch.
            concat_clips_copy(scene_clips, str(concat_path), fps=fps, crf=intermediate_crf)
            temp_files.append(str(concat_path))
            return concat_path, chunk_file_paths, temp_files

    # ── Chunked merge path ──
    logger.info(
        f"Chunked transition merge: {len(scene_clips)} clips in chunks of {chunk_size}"
    )

    # Split clips into chunk groups
    chunks: list[list[int]] = []  # each entry is a list of local clip indices
    for start in range(0, len(scene_clips), chunk_size):
        end = min(start + chunk_size, len(scene_clips))
        chunks.append(list(range(start, end)))

    total_chunks = len(chunks)
    logger.info(f"Split into {total_chunks} chunks")

    # Phase 1: Merge within each chunk
    chunk_results: list[str] = []
    phase1_range = int(report_range * 0.7)  # 70% of progress for phase 1
    phase2_range = report_range - phase1_range  # 30% for phase 2

    for chunk_idx, chunk_clip_indices in enumerate(chunks):
        if cancel_check and cancel_check():
            raise RuntimeError("Export cancelled by user")

        chunk_clips = [scene_clips[ci] for ci in chunk_clip_indices]

        # Build AI transition clips dict for this chunk (keyed by global clip index)
        chunk_ai_clips: dict[int, str] = {}
        for ci in chunk_clip_indices[:-1]:  # boundaries within this chunk
            if ci in ai_transition_clips:
                chunk_ai_clips[ci] = ai_transition_clips[ci]

        per_chunk_range = max(1, phase1_range // total_chunks)
        chunk_progress_offset = report_base_percent + (chunk_idx * per_chunk_range)

        if len(chunk_clips) == 1:
            chunk_result = chunk_clips[0]
        else:
            chunk_result = _sequential_merge(
                chunk_clips, chunk_clip_indices, chunk_ai_clips,
                label=f"chunk {chunk_idx + 1}/{total_chunks} ",
                progress_offset=chunk_progress_offset,
                progress_range=per_chunk_range,
            )

        # Save chunk to durable dir (survives tmpfs cleanup for recovery/gallery)
        chunk_out_path = _chunk_dir / f"chunk_{chunk_idx:03d}.mp4"
        if chunk_result != str(chunk_out_path):
            shutil.copy2(chunk_result, str(chunk_out_path))
        chunk_results.append(str(chunk_out_path))
        chunk_file_paths.append(str(chunk_out_path))

        first_scene = clip_scene_indices[chunk_clip_indices[0]]
        last_scene = clip_scene_indices[chunk_clip_indices[-1]]
        logger.info(
            f"Chunk {chunk_idx} complete: scenes {first_scene}-{last_scene} → {chunk_out_path}"
        )

        if chunk_callback:
            try:
                chunk_callback(chunk_idx, str(chunk_out_path), first_scene, last_scene)
            except Exception as _e:
                logger.warning(f"chunk_callback error: {_e}")

    # Phase 2: Merge chunks together with boundary transitions
    if cancel_check and cancel_check():
        raise RuntimeError("Export cancelled by user")

    if len(chunk_results) == 1:
        return Path(chunk_results[0]), chunk_file_paths, temp_files

    logger.info(f"Merging {len(chunk_results)} chunks with boundary transitions")
    _do_report("Merging chunks...", report_base_percent + phase1_range)

    merged = chunk_results[0]
    for ci in range(1, len(chunk_results)):
        t_percent = report_base_percent + phase1_range + int((ci / (len(chunk_results) - 1)) * phase2_range)
        _do_report(f"Merging chunk {ci + 1}/{len(chunk_results)}...", t_percent)

        # Boundary: last clip of previous chunk → first clip of current chunk
        prev_chunk_last_clip_idx = chunks[ci - 1][-1]
        curr_chunk_first_clip_idx = chunks[ci][0]

        # Check for AI transition clip at this boundary
        ai_clip = ai_transition_clips.get(prev_chunk_last_clip_idx)
        if ai_clip:
            cat_path = output_dir / f"chunkmerge_ai_{ci:03d}.mp4"
            concat_clips([merged, ai_clip, chunk_results[ci]], str(cat_path), fps=fps,
                         crf=intermediate_crf)
            temp_files.append(str(cat_path))
            merged = str(cat_path)
            logger.info(f"Inserted AI transition at chunk boundary {ci - 1}→{ci}")
            continue

        t_type, t_dur = _resolve_transition(prev_chunk_last_clip_idx, curr_chunk_first_clip_idx)

        if t_type:
            xfade_path = output_dir / f"chunkmerge_xfade_{ci:03d}.mp4"
            apply_transition(merged, chunk_results[ci], str(xfade_path), t_type, t_dur,
                             crf=intermediate_crf)
            temp_files.append(str(xfade_path))
            merged = str(xfade_path)
        else:
            cat_path = output_dir / f"chunkmerge_cat_{ci:03d}.mp4"
            concat_clips([merged, chunk_results[ci]], str(cat_path), fps=fps,
                         crf=intermediate_crf)
            temp_files.append(str(cat_path))
            merged = str(cat_path)

    return Path(merged), chunk_file_paths, temp_files


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
    chunk_size: int = 0,
    chunk_callback: Optional[Callable[[int, str, int, int], None]] = None,
    cancel_check: Optional[Callable[[], bool]] = None,
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

    # Acquire a tmpfs-backed working directory for intermediate files.
    # On Linux with /dev/shm this eliminates disk I/O for temp clips.
    work_dir = _get_tmpfs_dir(output_dir, label="rbmn_music_export")
    temp_files: list[str] = []

    def _cleanup_temp_files() -> None:
        """Remove all tracked temp files and colormatch dir, safe to call on any path."""
        for _f in temp_files:
            try:
                Path(_f).unlink(missing_ok=True)
            except Exception:
                pass
        # Clean up tmpfs working directory (removes all intermediates)
        _cleanup_tmpfs_dir(work_dir, output_dir)

    try:
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

        # ── Pre-compute transition compensation padding ─────────────────
        # Moved BEFORE clip creation so we can include padding in the single-pass
        # FFmpeg call instead of re-rendering clips after the fact.
        # First pass: identify which scenes will produce clips (have content)
        valid_scene_indices: list[int] = []
        for i, scene in enumerate(scenes):
            source_type = scene.get("scene_source_type", "image")
            if source_type == "video":
                if scene.get("video_path"):
                    valid_scene_indices.append(i)
            else:
                if scene.get("image_path") or scene.get("video_path"):
                    valid_scene_indices.append(i)

        num_expected = len(valid_scene_indices)

        # Determine if we'll use a default xfade
        _has_explicit = False
        for ci in range(num_expected - 1):
            si_out = valid_scene_indices[ci]
            si_in = valid_scene_indices[ci + 1]
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
            and num_expected > 1
        )

        # Build per-clip-boundary transition durations
        clip_boundary_durations: list[float] = []
        for ci in range(num_expected - 1):
            si_out = valid_scene_indices[ci]
            si_in = valid_scene_indices[ci + 1]
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

        # Per-clip padding: each clip absorbs half the overlap from
        # its left boundary and half from its right boundary.
        clip_padding: list[float] = [0.0] * num_expected
        for ci, bd in enumerate(clip_boundary_durations):
            if bd > 0:
                clip_padding[ci] += bd / 2.0
                clip_padding[ci + 1] += bd / 2.0

        total_padding = sum(clip_boundary_durations)
        if total_padding > 0:
            logger.info(
                f"Transition compensation (pre-computed): {len(clip_boundary_durations)} boundaries, "
                f"{total_padding:.1f}s total overlap to compensate"
            )

        # Map scene index → padding amount for single-pass processing
        scene_padding_map: dict[int, float] = {}
        for ci, si in enumerate(valid_scene_indices):
            if clip_padding[ci] > 0:
                scene_padding_map[si] = clip_padding[ci]

        # ── Step 1: Create clips — SINGLE-PASS + PARALLEL pipeline ──────
        # Each clip is created with ONE FFmpeg call that chains:
        #   normalize (scale+pad+setsar) + duration pad + fade in + fade out
        # Clips are independent and processed in parallel (ThreadPoolExecutor).
        clip_tasks: list[dict] = []
        for i, scene in enumerate(scenes):
            task = _build_clip_task(
                scene, i, work_dir, width, height, fps,
                intermediate_crf, scene_padding_map.get(i, 0.0),
            )
            if task is None:
                logger.warning(f"Scene {i} has no content, skipping")
                continue
            clip_tasks.append(task)

        scene_clips, clip_scene_indices, clip_temp_files = _process_clips_parallel(
            clip_tasks,
            report=report,
            cancel_check=cancel_check,
            total_scenes=total_scenes,
            base_percent=0,
            percent_range=60,
        )
        temp_files.extend(clip_temp_files)

        if not scene_clips:
            raise RuntimeError("No clips to concatenate")

        # Cancel check: between clip creation and color matching
        if cancel_check and cancel_check():
            raise RuntimeError("Export cancelled by user")

        num_clips = len(scene_clips)

        # Step 1b: Adjacent-clip colour matching (before transitions)
        if color_match_clips and len(scene_clips) > 1:
            report("Matching colors between adjacent clips...", 62)
            logger.info("Applying adjacent-clip colour matching across %d clips", len(scene_clips))
            cm_dir = str(work_dir / "_colormatch")
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
                t_norm_path = work_dir / f"transition_{ci:03d}.mp4"
                normalize_clip(t_clip, str(t_norm_path), width, height, fps,
                               crf=intermediate_crf)
                temp_files.append(str(t_norm_path))
                ai_transition_clips[ci] = str(t_norm_path)
                logger.info(f"Normalized AI transition clip for boundary {ci}→{ci+1}")

        if ai_transition_clips:
            logger.info(f"Found {len(ai_transition_clips)} AI transition clips to interleave")

        # Cancel check: between color matching and transition merge
        if cancel_check and cancel_check():
            raise RuntimeError("Export cancelled by user")

        # Step 2: Apply inter-scene xfade transitions where specified, then concatenate
        has_xfade = _has_explicit or _will_use_default
        use_default_xfade = _will_use_default

        # ── Resume detection: check if a previous merged video exists in work_dir ──
        _existing_merge = sorted(work_dir.glob("chunkmerge_xfade_*.mp4"))
        if not _existing_merge:
            _existing_merge = sorted(work_dir.glob("concatenated.mp4"))
        if _existing_merge:
            _merge_candidate = _existing_merge[-1]
            _merge_info = get_media_info(str(_merge_candidate))
            _merge_dur = _merge_info.get("duration", 0)
            if _merge_dur > 10:
                logger.info(
                    f"RESUME: Found existing merged video {_merge_candidate.name} "
                    f"({_merge_dur:.1f}s), skipping clip processing and chunk merge"
                )
                report("Resuming from merged video...", 88)
                concat_path = _merge_candidate
                chunk_file_paths: list[str] = []
                merge_temp_files: list[str] = []
            else:
                _existing_merge = []

        if not _existing_merge:
            concat_path, chunk_file_paths, merge_temp_files = _chunked_transition_merge(
                scene_clips=scene_clips,
                clip_scene_indices=clip_scene_indices,
                scenes=scenes,
                xfade_types_set=xfade_types_set,
                default_transition=default_transition,
                default_transition_duration=default_transition_duration,
                use_default_xfade=use_default_xfade,
                has_xfade=has_xfade,
                ai_transition_clips=ai_transition_clips,
                output_dir=work_dir,
                fps=fps,
                intermediate_crf=intermediate_crf,
                final_crf=final_crf,
                chunk_size=chunk_size,
                chunk_callback=chunk_callback,
                cancel_check=cancel_check,
                report=report,
                report_base_percent=70,
                chunk_output_dir=output_dir,
                report_range=18,
            )
        # Add merge temp files to our cleanup list (but NOT chunk_file_paths)
        temp_files.extend(merge_temp_files)

        # Step 3: Mux audio
        report("Muxing audio track...", 90)
        logger.info(f"Muxing with master audio: {master_audio_path}")
        mux_audio(str(concat_path), master_audio_path, output_path)

        report("Assembly complete!", 100)
        logger.info(f"Music video assembled: {output_path}")
    except BaseException as exc:
        # ── PRESERVE working directory on failure for resume ──────────
        # The clip rendering step (Step 1) is the most expensive — hours
        # of CPU-bound FFmpeg work.  If a later step fails (mux, subtitles,
        # normalization), we keep the work_dir intact so the next export
        # attempt can reuse already-rendered clips and the merged video.
        # Individual clips are validated before reuse (duration > 0 check),
        # and the merged-video resume logic (above) skips straight past
        # clip rendering + chunk merge when a valid merge file exists.
        # ── PRESERVE everything on failure for resume ─────────────────
        # DO NOT delete temp_files — clips and merge outputs ARE temp_files.
        logger.error(
            f"Music video assembly failed — preserving work_dir {work_dir} "
            f"for resume on next export attempt: {exc}"
        )
        raise
    else:
        # Success path cleanup — remove everything
        _cleanup_temp_files()


def assemble_narration_video(
    scenes: List[Dict[str, Any]],
    narration_audio_path: str,
    output_path: str,
    width: int = 1280,
    height: int = 720,
    fps: int = 24,
    default_transition: Optional[str] = "crossfade",
    default_transition_duration: float = 0.5,
    color_match_clips: bool = False,
    progress_callback: Optional[Callable[[str, int], None]] = None,
    final_crf: int = 18,
    backing_tracks: Optional[List[Dict[str, Any]]] = None,
    subtitle_words: Optional[List[Dict[str, Any]]] = None,
    subtitle_style: Optional[Dict[str, Any]] = None,
    normalize_audio_enabled: bool = False,
    loop_backing: bool = False,
    narration_volume: float = 1.0,
    backing_volume: float = 1.0,
    main_fade_in: float = 0.0,
    main_fade_out: float = 0.0,
    normalize_backing: bool = False,
    chunk_size: int = 0,
    chunk_callback: Optional[Callable[[int, str, int, int], None]] = None,
    cancel_check: Optional[Callable[[], bool]] = None,
    # ── Re-export cache controls ──
    audio_only_remix: bool = False,
    force_recreate: bool = False,
    export_stems: bool = False,
) -> None:
    """
    Assemble narration video from scenes with full feature parity to music video.

    Supports both image and video source types, transitions, color matching,
    AI transition clips, backing track mixing, subtitle burn-in, and audio
    normalization.

    Workflow:
    1. For each scene, create a clip based on source type (image→Ken Burns, video→normalize)
    2. Apply scene-level fade in/out transitions
    3. Optionally apply adjacent-clip color matching
    4. Concatenate clips with xfade transitions (crossfade, dissolve, etc.)
    5. Mix narration with optional backing tracks
    6. Mux audio with video
    7. Optionally burn in subtitles
    8. Optionally normalize audio

    Scene format:
    [
        {
            "image_path": "/path/to/image.jpg",       # for image source scenes
            "video_path": "/path/to/video.mp4",        # for video source scenes
            "duration": 5.0,
            "scene_source_type": "image" | "video",
            "effect": "zoom_in_center",                # legacy key
            "image_movement": {                        # new movement dict
                "effect": "zoom_in_center",
                "intensity": 50,
                "easing": "ease_in_out",
            },
            "transition_in": {"type": "crossfade", "duration": 0.5},
            "transition_out": {"type": "fade_to_black", "duration": 0.5},
            "transition_clip_path": "/path/to/ai_transition.mp4",  # optional
        },
        ...
    ]

    Args:
        scenes: List of scene dicts with image/video paths and duration
        narration_audio_path: Path to narration audio
        output_path: Final output video path
        width: Target width (default 1280)
        height: Target height (default 720)
        fps: Target framerate (default 24)
        default_transition: Default transition between clips ('crossfade',
            'dissolve', 'none').  Default 'crossfade'.
        default_transition_duration: Duration of default transition in seconds.
        color_match_clips: If True, apply adjacent-clip colour matching.
        progress_callback: Optional callback(step_description, percent_0_100).
        final_crf: CRF quality for the final output encode (default 18).
        backing_tracks: Optional list of backing track dicts for audio mixing.
        subtitle_words: Optional list of word dicts for subtitle burn-in.
        subtitle_style: Optional dict with ASS subtitle style options.
        normalize_audio_enabled: If True, normalize audio after muxing.

    Raises:
        ValueError: If scenes list empty
        RuntimeError: If processing fails
    """
    if not scenes:
        raise ValueError("No scenes provided")

    # ── Mutual-exclusion guard ─────────────────────────────────────────
    # audio_only_remix relies on the cache; force_recreate WIPES the cache.
    # The frontend already enforces this, but guard at the backend too.
    if audio_only_remix and force_recreate:
        raise RuntimeError(
            "audio_only_remix and force_recreate are mutually exclusive: "
            "force_recreate clears the cache that audio_only_remix needs."
        )

    # ── Re-export cache: compute key & decide strategy ──────────────────
    # Hash the video-affecting params; if cache hits we skip the entire
    # render pipeline and reuse the cached silent concat for audio mux.
    intermediate_crf_for_cache = 14  # matches the value used below
    _cache_key = _video_cache_key(
        scenes,
        width=width,
        height=height,
        fps=fps,
        intermediate_crf=intermediate_crf_for_cache,
        final_crf=final_crf,
        default_transition=default_transition,
        default_transition_duration=default_transition_duration,
        color_match_clips=color_match_clips,
    )
    if force_recreate:
        logger.info("force_recreate=True — clearing export cache before render")
        _clear_export_cache(output_path)
    _cached_concat_path: Optional[Path] = _load_cached_concat(output_path, _cache_key)
    if audio_only_remix and _cached_concat_path is None:
        raise RuntimeError(
            "audio_only_remix requested but no matching cached video found. "
            "Run a full export first, or disable audio_only_remix to render "
            "from scratch."
        )
    if _cached_concat_path is not None:
        logger.info(
            f"Reusing cached concat from previous export: {_cached_concat_path} "
            f"(key={_cache_key}). Audio mix will run fresh."
        )

    def report(step: str, percent: int) -> None:
        if progress_callback:
            try:
                progress_callback(step, percent)
            except Exception:
                pass

    # Use near-lossless CRF for intermediate stages (same as music video).
    intermediate_crf = 14

    logger.info(
        f"Assembling narration video: {len(scenes)} scenes → {output_path} "
        f"(intermediate CRF={intermediate_crf}, final CRF={final_crf})"
    )
    report(f"Assembling {len(scenes)} scenes...", 0)

    output_dir = Path(output_path).parent
    output_dir.mkdir(parents=True, exist_ok=True)

    # Acquire a tmpfs-backed working directory for intermediate files.
    work_dir = _get_tmpfs_dir(output_dir, label="rbmn_narr_export")
    temp_files: list[str] = []

    def _cleanup_narration_temp_files() -> None:
        """Remove all tracked temp files and colormatch dir. Safe on any exit path."""
        for _f in temp_files:
            try:
                Path(_f).unlink(missing_ok=True)
            except Exception:
                pass
        # Clean up tmpfs working directory (removes all intermediates)
        _cleanup_tmpfs_dir(work_dir, output_dir)

    try:
        total_scenes = len(scenes)

        # ── Xfade type set (used for transition detection and compensation) ──
        xfade_types_set = {"crossfade", "dissolve", "wipe_left", "wipe_right",
                           "wipe_up", "wipe_down", "slide_left", "slide_right"}

        # ── Pre-compute transition compensation padding ─────────────────
        # Moved BEFORE clip creation so padding is included in single-pass FFmpeg call.
        valid_scene_indices: list[int] = []
        for i, scene in enumerate(scenes):
            source_type = scene.get("scene_source_type", "image")
            if source_type == "video":
                if scene.get("video_path"):
                    valid_scene_indices.append(i)
            else:
                if scene.get("image_path") or scene.get("video_path"):
                    valid_scene_indices.append(i)

        num_expected = len(valid_scene_indices)

        _has_explicit = False
        for ci in range(num_expected - 1):
            si_out = valid_scene_indices[ci]
            si_in = valid_scene_indices[ci + 1]
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
            and num_expected > 1
        )

        clip_boundary_durations: list[float] = []
        for ci in range(num_expected - 1):
            si_out = valid_scene_indices[ci]
            si_in = valid_scene_indices[ci + 1]
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

        clip_padding: list[float] = [0.0] * num_expected
        for ci, bd in enumerate(clip_boundary_durations):
            if bd > 0:
                clip_padding[ci] += bd / 2.0
                clip_padding[ci + 1] += bd / 2.0

        total_padding = sum(clip_boundary_durations)
        if total_padding > 0:
            logger.info(
                f"Transition compensation (pre-computed): {len(clip_boundary_durations)} boundaries, "
                f"{total_padding:.1f}s total overlap to compensate"
            )

        scene_padding_map: dict[int, float] = {}
        for ci, si in enumerate(valid_scene_indices):
            if clip_padding[ci] > 0:
                scene_padding_map[si] = clip_padding[ci]

        # ── Skip Step 1/1b/1c/2-setup entirely when cache hits ──
        if _cached_concat_path is None:
            # ── Step 1: Create clips — SINGLE-PASS + PARALLEL pipeline ──────
            # Each clip is created with ONE FFmpeg call that chains:
            #   normalize (scale+pad+setsar) + duration pad + fade in + fade out
            # Clips are independent and processed in parallel (ThreadPoolExecutor).
            clip_tasks: list[dict] = []
            for i, scene in enumerate(scenes):
                task = _build_clip_task(
                    scene, i, work_dir, width, height, fps,
                    intermediate_crf, scene_padding_map.get(i, 0.0),
                )
                if task is None:
                    logger.warning(f"Scene {i} has no content, skipping")
                    continue
                clip_tasks.append(task)

            scene_clips, clip_scene_indices, clip_temp_files = _process_clips_parallel(
                clip_tasks,
                report=report,
                cancel_check=cancel_check,
                total_scenes=total_scenes,
                base_percent=0,
                percent_range=50,
            )
            temp_files.extend(clip_temp_files)

            if not scene_clips:
                raise RuntimeError("No clips to concatenate")

            if cancel_check and cancel_check():
                raise RuntimeError("Export cancelled by user")

            num_clips = len(scene_clips)

            # ── Step 1b: Adjacent-clip colour matching (before transitions) ──
            if color_match_clips and len(scene_clips) > 1:
                report("Matching colors between adjacent clips...", 55)
                logger.info("Applying adjacent-clip colour matching across %d clips", len(scene_clips))
                cm_dir = str(work_dir / "_colormatch")
                matched_clips = match_adjacent_clips(scene_clips, cm_dir, crf=intermediate_crf)
                for mc in matched_clips:
                    if mc not in scene_clips and mc not in temp_files:
                        temp_files.append(mc)
                scene_clips = matched_clips

            # ── Step 1c: Check for AI transition clips ──
            ai_transition_clips: dict[int, str] = {}
            for ci in range(num_clips - 1):
                si = clip_scene_indices[ci]
                t_clip = scenes[si].get("transition_clip_path")
                if t_clip and Path(t_clip).exists():
                    t_norm_path = work_dir / f"transition_{ci:03d}.mp4"
                    normalize_clip(t_clip, str(t_norm_path), width, height, fps,
                                   crf=intermediate_crf)
                    temp_files.append(str(t_norm_path))
                    ai_transition_clips[ci] = str(t_norm_path)
                    logger.info(f"Normalized AI transition clip for boundary {ci}→{ci+1}")

            if ai_transition_clips:
                logger.info(f"Found {len(ai_transition_clips)} AI transition clips to interleave")

            # Cancel check: between color matching and transition merge
            if cancel_check and cancel_check():
                raise RuntimeError("Export cancelled by user")

            # ── Step 2: Apply inter-scene xfade transitions, then concatenate ──
            has_xfade = _has_explicit or _will_use_default
            use_default_xfade = _will_use_default

            # ── Wrap chunk_callback for narration: mux audio into each chunk ──
            # This lets users preview chunks with audio during long renders.
            _original_chunk_callback = chunk_callback
            if chunk_callback and narration_audio_path and Path(narration_audio_path).exists():
                # Pre-compute cumulative scene start times for chunk audio slicing
                _cumulative_times: list[float] = []
                _t = 0.0
                for _sc in scenes:
                    _cumulative_times.append(_t)
                    _t += _sc.get("duration", 0.0)
                _total_audio_dur = _t

                def _narration_chunk_callback(chunk_idx: int, chunk_path: str, scene_start: int, scene_end: int):
                    """Mux the corresponding audio segment into the chunk before reporting."""
                    try:
                        # Find time range for this chunk's scenes
                        audio_start = _cumulative_times[scene_start] if scene_start < len(_cumulative_times) else 0.0
                        if scene_end + 1 < len(_cumulative_times):
                            audio_end = _cumulative_times[scene_end + 1]
                        else:
                            audio_end = _total_audio_dur
                        audio_duration = audio_end - audio_start
                        if audio_duration <= 0:
                            logger.warning(f"Chunk {chunk_idx}: audio duration <= 0 ({audio_start:.1f}s-{audio_end:.1f}s), skipping mux")
                        else:
                            # Mux audio segment into chunk
                            chunk_with_audio = chunk_path.replace(".mp4", "_muxed.mp4")
                            mux_cmd = [
                                "ffmpeg", "-y",
                                "-i", chunk_path,
                                "-ss", str(audio_start),
                                "-t", str(audio_duration),
                                "-i", narration_audio_path,
                                "-c:v", "copy",
                                "-c:a", "aac", "-b:a", "192k",
                                "-map", "0:v:0", "-map", "1:a:0",
                                "-shortest",
                                chunk_with_audio,
                            ]
                            import subprocess
                            try:
                                result = subprocess.run(
                                    mux_cmd, capture_output=True, text=True, timeout=180
                                )
                            except subprocess.TimeoutExpired:
                                Path(chunk_with_audio).unlink(missing_ok=True)
                                raise RuntimeError(
                                    f"FFmpeg mux timed out after 180s for chunk {chunk_idx}"
                                )
                            if result.returncode != 0:
                                Path(chunk_with_audio).unlink(missing_ok=True)
                                logger.warning(
                                    f"FFmpeg mux failed for chunk {chunk_idx}: "
                                    f"{(result.stderr or '')[:500]}"
                                )
                            elif Path(chunk_with_audio).exists() and Path(chunk_with_audio).stat().st_size > 0:
                                shutil.move(chunk_with_audio, chunk_path)
                                logger.info(
                                    f"Muxed narration audio ({audio_start:.1f}s-{audio_end:.1f}s) "
                                    f"into chunk {chunk_idx}"
                                )
                            else:
                                logger.warning(
                                    f"Audio mux produced empty file for chunk {chunk_idx}, "
                                    f"keeping video-only"
                                )
                                Path(chunk_with_audio).unlink(missing_ok=True)
                    except Exception as _mux_err:
                        logger.warning(f"Failed to mux audio into chunk {chunk_idx}: {_mux_err}")
                        # Clean up partial file
                        Path(chunk_path.replace(".mp4", "_muxed.mp4")).unlink(missing_ok=True)

                    # Always call original callback
                    if _original_chunk_callback:
                        _original_chunk_callback(chunk_idx, chunk_path, scene_start, scene_end)

                chunk_callback = _narration_chunk_callback

        # ── PERSISTENT CACHE HIT: use cached concat from previous export ──
        # This skips ALL clip rendering for audio-only remixes.
        if _cached_concat_path is not None:
            logger.info(
                f"PERSISTENT CACHE: Using cached concat {_cached_concat_path}, "
                f"skipping clip processing and chunk merge"
            )
            report("Using cached video, mixing audio...", 80)
            concat_path = _cached_concat_path
            chunk_file_paths: list[str] = []
            merge_temp_files: list[str] = []
            scene_clips: list = []
            clip_scene_indices: list = []
            xfade_types_set = set()  # not needed
            use_default_xfade = False
            has_xfade = False
            ai_transition_clips = {}
            _existing_merge = [concat_path]
            # Skip directly past clip rendering by ensuring downstream code
            # sees the cached merge.
        # ── Resume detection: check if a previous merged video exists in work_dir ──
        # If the export failed at the audio mux/subtitle step after the expensive
        # chunked merge was already complete, we can skip straight to audio prep.
        if _cached_concat_path is None:
            _existing_merge = sorted(work_dir.glob("chunkmerge_xfade_*.mp4"))
            if not _existing_merge:
                _existing_merge = sorted(work_dir.glob("concatenated.mp4"))
        if _existing_merge and _cached_concat_path is None:
            _merge_candidate = _existing_merge[-1]  # largest index = final merge
            _merge_info = get_media_info(str(_merge_candidate))
            _merge_dur = _merge_info.get("duration", 0)
            if _merge_dur > 10:  # sanity: must be > 10 seconds
                logger.info(
                    f"RESUME: Found existing merged video {_merge_candidate.name} "
                    f"({_merge_dur:.1f}s), skipping clip processing and chunk merge"
                )
                report("Resuming from merged video...", 80)
                concat_path = _merge_candidate
                chunk_file_paths: list[str] = []
                merge_temp_files: list[str] = []
            else:
                _existing_merge = []  # too short, redo merge

        if not _existing_merge:
            concat_path, chunk_file_paths, merge_temp_files = _chunked_transition_merge(
                scene_clips=scene_clips,
                clip_scene_indices=clip_scene_indices,
                scenes=scenes,
                xfade_types_set=xfade_types_set,
                default_transition=default_transition,
                default_transition_duration=default_transition_duration,
                use_default_xfade=use_default_xfade,
                has_xfade=has_xfade,
                ai_transition_clips=ai_transition_clips,
                output_dir=work_dir,
                fps=fps,
                intermediate_crf=intermediate_crf,
                final_crf=final_crf,
                chunk_size=chunk_size,
                chunk_callback=chunk_callback,
                cancel_check=cancel_check,
                report=report,
                report_base_percent=60,
                report_range=18,
                chunk_output_dir=output_dir,
            )
            # Save the concat to the persistent cache for future audio-only
            # remixes. Best-effort — failure here doesn't break this export.
            _save_concat_to_cache(
                output_path, Path(concat_path), _cache_key, len(scenes)
            )
        # Add merge temp files to our cleanup list (but NOT chunk_file_paths)
        temp_files.extend(merge_temp_files)

        # ── Step 3: Audio preparation ──
        audio_path = narration_audio_path

        # 3a: Mix narration with backing tracks if provided
        if backing_tracks:
            report("Mixing backing tracks...", 82)
            logger.info(f"Mixing {len(backing_tracks)} backing track(s) with narration")
            # Compute total timeline duration for loop feature
            _total_dur = 0.0
            try:
                _total_dur = get_media_info(narration_audio_path).get("duration", 0.0)
            except Exception:
                pass
            mixed_audio_path = str(work_dir / "mixed_audio.wav")
            mix_audio_tracks(
                narration_audio_path, backing_tracks, mixed_audio_path,
                loop_backing=loop_backing,
                total_duration=_total_dur,
                narration_volume=narration_volume,
                backing_volume=backing_volume,
                main_fade_in=main_fade_in,
                main_fade_out=main_fade_out,
                normalize_backing=normalize_backing,
            )
            temp_files.append(mixed_audio_path)
            audio_path = mixed_audio_path

        # 3b: Normalize audio if enabled
        if normalize_audio_enabled and audio_path:
            report("Normalizing audio...", 85)
            logger.info("Applying audio normalization")
            norm_audio_path = str(work_dir / "normalized_audio.wav")
            normalize_audio(audio_path, norm_audio_path)
            temp_files.append(norm_audio_path)
            audio_path = norm_audio_path

        # ── Step 4: Mux audio ──
        report("Muxing audio track...", 88)
        logger.info(f"Muxing with audio: {audio_path}")
        mux_audio(str(concat_path), audio_path, output_path)

        # ── Step 4b: Optional stems export for DAW remixing ──
        # Writes per-channel WAVs alongside the main MP4 so the user can
        # remix outside the app without re-rendering.
        if export_stems:
            report("Exporting audio stems...", 91)
            logger.info("Exporting audio stems for DAW remixing")
            _total_dur_for_stems = 0.0
            try:
                _total_dur_for_stems = get_media_info(
                    narration_audio_path
                ).get("duration", 0.0)
            except Exception:
                pass
            try:
                _export_audio_stems(
                    output_path=output_path,
                    narration_audio_path=narration_audio_path,
                    backing_tracks=backing_tracks,
                    narration_volume=narration_volume,
                    backing_volume=backing_volume,
                    loop_backing=loop_backing,
                    main_fade_in=main_fade_in,
                    main_fade_out=main_fade_out,
                    normalize_backing=normalize_backing,
                    total_duration=_total_dur_for_stems,
                    individual_backing=True,
                )
            except Exception as _stems_err:
                logger.warning(f"Stems export failed (non-fatal): {_stems_err}")

        # ── Step 5: Subtitle burn-in ──
        if subtitle_words and subtitle_style:
            report("Burning subtitles...", 93)
            logger.info("Burning subtitles into narration video")
            tmp_ass = str(work_dir / "subtitles.ass")
            sub_output = str(work_dir / f"sub_{Path(output_path).name}")
            generate_ass_subtitles(subtitle_words, tmp_ass, subtitle_style)
            burn_subtitles(output_path, tmp_ass, sub_output)

            # Replace original with subtitled version
            shutil.move(sub_output, output_path)
            Path(tmp_ass).unlink(missing_ok=True)
            logger.info("Subtitles burned into narration video")


        report("Assembly complete!", 100)
        logger.info(f"Narration video assembled: {output_path}")
    except BaseException as exc:
        # ── PRESERVE everything on failure for resume ─────────────────
        # Clip rendering (Step 1) is the most expensive phase — hours of
        # CPU-bound FFmpeg work with 4 parallel threads.  If a later step
        # fails (audio mix, subtitle burn-in, normalization), we keep ALL
        # files in work_dir intact so the next export attempt can:
        #   1. Reuse already-rendered clips (per-clip duration check)
        #   2. Skip directly to audio prep if merged video exists
        # This turns a multi-hour redo into a ~30 second retry.
        # DO NOT delete temp_files — clips and merge outputs ARE temp_files.
        logger.error(
            f"Narration video assembly failed — preserving work_dir {work_dir} "
            f"for resume on next export attempt: {exc}"
        )
        raise
    else:
        # Success path cleanup — remove everything
        _cleanup_narration_temp_files()
