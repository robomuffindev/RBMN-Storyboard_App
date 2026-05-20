"""
Video Processing and Assembly Services

Handles video normalization, effects, and final assembly.
"""

from .ffmpeg import (
    normalize_clip,
    trim_clip,
    pad_clip,
    concat_clips,
    mux_audio,
    apply_kenburns,
    crossfade,
    get_media_info,
    extract_frame,
    extract_last_frame,
    slice_audio,
)
from .assembly import assemble_music_video, assemble_narration_video

__all__ = [
    "normalize_clip",
    "trim_clip",
    "pad_clip",
    "concat_clips",
    "mux_audio",
    "apply_kenburns",
    "crossfade",
    "get_media_info",
    "extract_frame",
    "extract_last_frame",
    "slice_audio",
    "assemble_music_video",
    "assemble_narration_video",
]
