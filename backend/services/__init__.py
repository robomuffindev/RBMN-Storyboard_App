"""
RBMN Storyboard App - Backend Services

Core services for AI music/narration video generation:
- ComfyUI client and workflow management
- Audio analysis and processing
- Video assembly and effects
- LLM prompt enhancement
- Job queue and dispatching
"""

__version__ = "0.1.0"

from .comfyui.client import ComfyUIClient, ComfyUIConnectionError, ComfyUIWorkflowError, ComfyUIVRAMError
from .comfyui.dispatcher import ComfyDispatcher, ComfyWorker
from .audio.analysis import AudioAnalyzer
from .video.ffmpeg import (
    normalize_clip,
    trim_clip,
    pad_clip,
    concat_clips,
    mux_audio,
    apply_kenburns,
    crossfade,
    get_media_info,
    extract_frame,
    slice_audio,
)
from .video.assembly import assemble_music_video, assemble_narration_video
from .llm.prompt_enhancer import PromptEnhancer
from .jobs.queue import JobQueue
from .jobs.dispatcher import JobDispatcher

__all__ = [
    "ComfyUIClient",
    "ComfyUIConnectionError",
    "ComfyUIWorkflowError",
    "ComfyUIVRAMError",
    "ComfyDispatcher",
    "ComfyWorker",
    "AudioAnalyzer",
    "normalize_clip",
    "trim_clip",
    "pad_clip",
    "concat_clips",
    "mux_audio",
    "apply_kenburns",
    "crossfade",
    "get_media_info",
    "extract_frame",
    "slice_audio",
    "assemble_music_video",
    "assemble_narration_video",
    "PromptEnhancer",
    "JobQueue",
    "JobDispatcher",
]
