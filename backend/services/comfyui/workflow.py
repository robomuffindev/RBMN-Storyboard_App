"""
ComfyUI Workflow Mutation Engine

Provides utilities for loading, finding nodes, and mutating workflow configurations.
"""

import json
import logging
from typing import Optional, Tuple, Any, List

logger = logging.getLogger(__name__)


def find_node_by_title(workflow: dict, title: str) -> Tuple[str, dict]:
    """
    Find a node in the workflow by its _meta.title.

    Args:
        workflow: ComfyUI workflow dict
        title: The title to search for

    Returns:
        Tuple of (node_id, node_dict)

    Raises:
        ValueError: If node not found
    """
    for node_id, node in workflow.items():
        if isinstance(node, dict) and node.get("_meta", {}).get("title") == title:
            logger.debug(f"Found node '{title}' with id {node_id}")
            return node_id, node

    raise ValueError(f"Node with title '{title}' not found in workflow")


def set_node_input(workflow: dict, title: str, input_name: str, value: Any) -> None:
    """
    Set an input value on a node found by title.

    Args:
        workflow: ComfyUI workflow dict
        title: Node title to find
        input_name: Input field name
        value: Value to set

    Raises:
        ValueError: If node not found
    """
    node_id, node = find_node_by_title(workflow, title)

    if "inputs" not in node:
        node["inputs"] = {}

    node["inputs"][input_name] = value
    logger.debug(f"Set {title}.{input_name} = {value}")


def prepare_klein_workflow(
    workflow_path: str,
    prompt: str,
    width: int,
    height: int,
    seed: int,
    ref_images: Optional[List[str]] = None,
) -> dict:
    """
    Load and prepare a Klein image generation workflow.

    Mutates these nodes:
    - "CLIP Text Encode (Positive Prompt)" → text input
    - "Load Image" → first reference image
    - "Reference 2 Image" → second reference image (if provided)
    - "Width" → width value
    - "Height" → height value
    - "RandomNoise" → noise_seed

    Args:
        workflow_path: Path to workflow JSON
        prompt: Text prompt for generation
        width: Image width
        height: Image height
        seed: Random seed
        ref_images: List of reference image paths (1-2 images)

    Returns:
        Mutated workflow dict

    Raises:
        FileNotFoundError: If workflow file not found
        ValueError: If required nodes not found
    """
    logger.info(f"Preparing Klein workflow from {workflow_path}")

    with open(workflow_path, "r", encoding="utf-8") as f:
        workflow = json.load(f)

    # Set text prompt — append anti-text suffix to prevent Klein from rendering subtitles/captions
    anti_text_suffix = ", no text, no subtitles, no captions, no words, no letters, no watermarks"
    full_prompt = prompt + anti_text_suffix if prompt else prompt
    set_node_input(workflow, "CLIP Text Encode (Positive Prompt)", "text", full_prompt)

    # Set dimensions
    set_node_input(workflow, "Width", "value", width)
    set_node_input(workflow, "Height", "value", height)

    # Set seed
    set_node_input(workflow, "RandomNoise", "noise_seed", seed)

    # Set reference images
    if ref_images:
        if len(ref_images) >= 1:
            set_node_input(workflow, "Load Image", "image", ref_images[0])

        if len(ref_images) >= 2:
            set_node_input(workflow, "Reference 2 Image", "image", ref_images[1])

        if len(ref_images) >= 3:
            set_node_input(workflow, "Reference 3", "image", ref_images[2])

        if len(ref_images) >= 4:
            set_node_input(workflow, "Reference 4", "image", ref_images[3])

    logger.info("Klein workflow prepared")
    return workflow


def _update_resize_longer_edge(workflow: dict, longer_edge: int) -> None:
    """Update all ResizeImagesByLongerEdge nodes to use the correct longer_edge.

    LTX workflows contain ResizeImagesByLongerEdge nodes with hardcoded
    longer_edge values (e.g. 1536 in I2V, 1832 in FF/LF).  If the
    project resolution differs, the downstream ImageResizeKJv2 node
    (keep_proportion=stretch) will distort the image.  This function
    finds ALL such nodes and sets their longer_edge to match the actual
    target resolution.
    """
    count = 0
    for node_id, node in workflow.items():
        if not isinstance(node, dict):
            continue
        class_type = node.get("class_type", "")
        if class_type == "ResizeImagesByLongerEdge":
            old_val = node.get("inputs", {}).get("longer_edge")
            node.setdefault("inputs", {})["longer_edge"] = longer_edge
            count += 1
            if old_val != longer_edge:
                logger.info(
                    f"Updated ResizeImagesByLongerEdge node {node_id}: "
                    f"{old_val} → {longer_edge}"
                )
    if count > 0:
        logger.info(f"Updated {count} ResizeImagesByLongerEdge node(s) to longer_edge={longer_edge}")


def _fix_image_resize_stretch(workflow: dict) -> None:
    """Change ImageResizeKJv2 nodes from 'stretch' to 'resize' mode.

    The LTX workflows use ImageResizeKJv2 with keep_proportion='stretch'
    which ACTIVELY DISTORTS the image to fit the target dimensions.
    When intermediate processing steps produce slightly different aspect
    ratios (due to latent space rounding, VAE encode/decode, etc.), the
    stretch mode squishes/elongates the image to force-fit.

    Changing to 'resize' (maintains aspect ratio) prevents the distortion.
    The upstream ResizeImagesByLongerEdge node already ensures the image
    is approximately the correct size, so this second resize just needs
    to handle minor dimension mismatches without distortion.

    NOTE: 'disabled' is NOT a valid value for keep_proportion — it causes
    a ComfyUI validation error that silently excludes VHS_VideoCombine
    (node 1087) from execution, producing 0 output files.
    Valid values: stretch, resize, pad, pad_edge, pad_edge_pixel, crop,
    pillarbox_blur, total_pixels.
    """
    count = 0
    for node_id, node in workflow.items():
        if not isinstance(node, dict):
            continue
        if node.get("class_type") == "ImageResizeKJv2":
            inputs = node.get("inputs", {})
            old_val = inputs.get("keep_proportion")
            if old_val == "stretch":
                inputs["keep_proportion"] = "resize"
                count += 1
                logger.info(
                    f"Fixed ImageResizeKJv2 node {node_id}: "
                    f"keep_proportion 'stretch' → 'resize'"
                )
    if count > 0:
        logger.info(f"Fixed {count} ImageResizeKJv2 node(s) to prevent stretching")


def _update_ltxv_preprocess_compression(workflow: dict, img_compression: int) -> None:
    """Update all LTXVPreprocess nodes to use the specified img_compression.

    The LTXVPreprocess node applies JPEG-quality compression to the input
    image BEFORE it enters the VAE encoder.  Lower values = more compression
    = more visual degradation.  The default I2V workflow ships with
    img_compression=18 (very aggressive) which causes visible content shifts
    when chaining scenes with last-frame-as-first-frame.

    Recommended values:
      18 = original I2V default (severe, visible shifts at transitions)
      35 = balanced (good fidelity, works well for chaining)
      38 = FF/LF workflow default
      50 = high fidelity (less creative freedom for the model)
    """
    count = 0
    for node_id, node in workflow.items():
        if not isinstance(node, dict):
            continue
        if node.get("class_type") == "LTXVPreprocess":
            old_val = node.get("inputs", {}).get("img_compression")
            node.setdefault("inputs", {})["img_compression"] = img_compression
            count += 1
            if old_val != img_compression:
                logger.info(
                    f"Updated LTXVPreprocess node {node_id}: "
                    f"img_compression {old_val} → {img_compression}"
                )
    if count > 0:
        logger.info(f"Updated {count} LTXVPreprocess node(s) to img_compression={img_compression}")


def prepare_ltx_workflow(
    workflow_path: str,
    prompt: str,
    width: int,
    height: int,
    duration: float,
    framerate: int,
    seed: int,
    audio_path: str,
    first_frame: Optional[str] = None,
    last_frame: Optional[str] = None,
    ltx_model_gguf: Optional[str] = None,
) -> dict:
    """
    Load and prepare an LTX video generation workflow.

    Supports two variants:
    1. Frame-to-Frame (with first_frame and last_frame)
    2. Image-to-Video (with just audio)

    Mutates:
    - "CLIP Text Encode (Prompt)" → text input
    - "LOAD FIRST IMAGE FRAME" → first frame (FF/LF variant)
    - "LOAD LAST IMAGE FRAME" → last frame (FF/LF variant)
    - "LOAD IMAGE" → image (I2V variant)
    - "Load Audio" → audio path
    - "WIDTH" → width value
    - "HEIGHT" → height value
    - "Audio - Video Duration" → duration value
    - "Framerate" → framerate value
    - "RandomNoise" → noise_seed

    Args:
        workflow_path: Path to workflow JSON
        prompt: Text prompt for generation
        width: Video width
        height: Video height
        duration: Video duration in seconds
        framerate: Framerate (typically 24, 30)
        seed: Random seed
        audio_path: Path to audio file
        first_frame: Path to first frame (FF/LF variant)
        last_frame: Path to last frame (FF/LF variant)

    Returns:
        Mutated workflow dict

    Raises:
        FileNotFoundError: If workflow file not found
        ValueError: If required nodes not found
    """
    logger.info(f"Preparing LTX workflow from {workflow_path}")

    with open(workflow_path, "r", encoding="utf-8") as f:
        workflow = json.load(f)

    # Override LTX GGUF model if specified
    if ltx_model_gguf:
        try:
            set_node_input(workflow, "Unet Loader (GGUF)", "unet_name", ltx_model_gguf)
            logger.info(f"LTX model GGUF set to: {ltx_model_gguf}")
        except ValueError:
            logger.warning("No 'Unet Loader (GGUF)' node found — skipping model override")

    # Set text prompt
    set_node_input(workflow, "CLIP Text Encode (Prompt)", "text", prompt)

    # Set dimensions
    set_node_input(workflow, "WIDTH", "value", width)
    set_node_input(workflow, "HEIGHT", "value", height)

    # Fix ResizeImagesByLongerEdge nodes — these have hardcoded longer_edge
    # values in the workflow JSON that must match the actual target resolution.
    # If they don't match, the downstream ImageResizeKJv2 (keep_proportion=stretch)
    # will distort the image to fit, causing skewed/elongated output.
    longer_edge = max(width, height)
    _update_resize_longer_edge(workflow, longer_edge)

    # Fix ImageResizeKJv2 stretch mode — this is the direct cause of the
    # height distortion at scene transitions.  The stretch mode force-fits
    # the image to target dimensions even when aspect ratios differ slightly
    # due to intermediate latent processing.  Disabling it lets the image
    # pass through at its natural dimensions.
    _fix_image_resize_stretch(workflow)

    # Normalize LTXVPreprocess img_compression across all nodes.
    # The I2V workflow ships with img_compression=18 which is very aggressive
    # and causes visible content shifts at scene boundaries when chaining
    # last-frame-as-first-frame.  A value of 35-40 preserves much more
    # visual fidelity with minimal impact on generation quality.
    _update_ltxv_preprocess_compression(workflow, img_compression=35)

    # Set audio and timing
    if audio_path:
        set_node_input(workflow, "Load Audio", "audio", audio_path)
    set_node_input(workflow, "Audio - Video Duration", "value", duration)
    set_node_input(workflow, "Framerate", "value", framerate)

    # Set seed
    set_node_input(workflow, "RandomNoise", "noise_seed", seed)

    # Frame-to-Frame variant
    if first_frame and last_frame:
        logger.info("Using FF/LF variant")
        set_node_input(workflow, "LOAD FIRST IMAGE FRAME", "image", first_frame)
        set_node_input(workflow, "LOAD LAST IMAGE FRAME", "image", last_frame)
    # Image-to-Video variant
    elif first_frame:
        logger.info("Using I2V variant")
        set_node_input(workflow, "LOAD IMAGE", "image", first_frame)

    logger.info("LTX workflow prepared")
    return workflow


def prepare_v2v_extend_workflow(
    workflow_path: str,
    prompt: str,
    width: int,
    height: int,
    duration: float,
    framerate: int,
    seed: int,
    audio_path: str,
    previous_video: str,
    ref_seconds: int = 3,
    ltx_model_gguf: Optional[str] = None,
    frame_overlap: int = 16,
    prev_video_duration: Optional[float] = None,
) -> dict:
    """
    Load and prepare a V2V (Video-to-Video) extending workflow.

    V2 uses LTXVExtendSampler with multi-frame overlap and
    LinearOverlapLatentTransition for smooth scene transitions.
    The previous video's frames are VAE-encoded and fed directly
    as input latents — the ExtendSampler generates new frames while
    alpha-blending the overlap region for seamless continuity.

    Mutates:
    - "CLIP Text Encode (Prompt)" → text input
    - "LOAD PREVIOUS VIDEO" → previous video path
    - "Load Audio" → audio path
    - "WIDTH" → width value
    - "HEIGHT" → height value
    - "Audio - Video Duration" → duration value
    - "Framerate" → framerate value
    - "RandomNoise" → noise_seed (both pass 1 and pass 2)
    - "Resize Video Frames" → longer_edge from resolution
    - "LTXV Extend Sampler" → frame_overlap (v2 workflow)

    Args:
        workflow_path: Path to V2V workflow JSON
        prompt: Text prompt for generation
        width: Video width
        height: Video height
        duration: Video duration in seconds
        framerate: Framerate (typically 24)
        seed: Random seed
        audio_path: Path to audio file
        previous_video: Path to previous scene's video file
        ref_seconds: Seconds of reference from end of previous video (default 3)
        ltx_model_gguf: Optional GGUF model variant name
        frame_overlap: Number of overlap frames for LTXVExtendSampler (default 8)

    Returns:
        Mutated workflow dict

    Raises:
        FileNotFoundError: If workflow file not found
        ValueError: If required nodes not found
    """
    logger.info(f"Preparing V2V extend workflow from {workflow_path}")

    with open(workflow_path, "r", encoding="utf-8") as f:
        workflow = json.load(f)

    # Override LTX GGUF model if specified
    if ltx_model_gguf:
        try:
            set_node_input(workflow, "Unet Loader (GGUF)", "unet_name", ltx_model_gguf)
            logger.info(f"V2V LTX model GGUF set to: {ltx_model_gguf}")
        except ValueError:
            logger.warning("No 'Unet Loader (GGUF)' node found — skipping model override")

    # Set text prompt
    set_node_input(workflow, "CLIP Text Encode (Prompt)", "text", prompt)

    # Set dimensions
    set_node_input(workflow, "WIDTH", "value", width)
    set_node_input(workflow, "HEIGHT", "value", height)

    # Set timing
    set_node_input(workflow, "Audio - Video Duration", "value", duration)
    set_node_input(workflow, "Framerate", "value", framerate)

    # Set seeds (both passes)
    set_node_input(workflow, "RandomNoise", "noise_seed", seed)
    try:
        set_node_input(workflow, "RandomNoise Pass 2", "noise_seed", seed + 1)
    except ValueError:
        pass  # Pass 2 seed node might not exist in all variants

    # Set previous video — only load the tail frames needed for overlap.
    # LTXVExtendSampler only uses the last `frame_overlap` frames for blending;
    # loading the entire video wastes VRAM encoding frames that are never used.
    #
    # IMPORTANT: The video file may be the UNTRIMMED version (raw model output
    # with video_tail overshoot).  prev_video_duration is the SCENE duration
    # (not file duration), so we use it to calculate which frames are within
    # the scene boundary.  frame_load_cap ensures we DON'T load tail frames
    # beyond the scene end, even if the file has more frames available.
    set_node_input(workflow, "LOAD PREVIOUS VIDEO", "video", previous_video)
    set_node_input(workflow, "LOAD PREVIOUS VIDEO", "force_rate", framerate)

    if prev_video_duration and prev_video_duration > 0:
        # Calculate total frames WITHIN SCENE BOUNDARY and skip to the tail region.
        # We keep frame_overlap + 8 buffer frames from the end of the scene content.
        total_frames = round(prev_video_duration * framerate)
        keep_frames = frame_overlap + 8
        skip = max(0, total_frames - keep_frames)
        if skip > 0:
            set_node_input(workflow, "LOAD PREVIOUS VIDEO", "skip_first_frames", skip)
            # Also cap the number of frames loaded to prevent loading tail
            # overshoot frames beyond the scene boundary (the file may be
            # longer than prev_video_duration if it's the untrimmed version).
            set_node_input(workflow, "LOAD PREVIOUS VIDEO", "frame_load_cap", keep_frames)
            logger.info(
                f"V2V previous video: skipping first {skip} of {total_frames} scene frames, "
                f"loading exactly {keep_frames} frames (overlap={frame_overlap}, "
                f"frame_load_cap={keep_frames})"
            )
    else:
        logger.warning("V2V prev_video_duration not provided — loading all frames (may use excess VRAM)")

    # Set audio
    if audio_path:
        set_node_input(workflow, "Load Audio", "audio", audio_path)

    # Set resize longer edge
    longer_edge = max(width, height)
    try:
        set_node_input(workflow, "Resize Video Frames", "longer_edge", longer_edge)
    except ValueError:
        pass

    # Set frame_overlap on LTXVExtendSampler (v2 workflow)
    try:
        set_node_input(workflow, "LTXV Extend Sampler", "frame_overlap", frame_overlap)
        logger.info(f"V2V frame_overlap set to: {frame_overlap}")
    except ValueError:
        # Legacy v1 workflow without ExtendSampler — skip
        logger.debug("No 'LTXV Extend Sampler' node — using legacy workflow")

    # Fix ResizeImagesByLongerEdge nodes (same as I2V)
    _update_resize_longer_edge(workflow, longer_edge)

    # Normalize LTXVPreprocess compression
    _update_ltxv_preprocess_compression(workflow, img_compression=35)

    # Fix ImageResizeKJv2 stretch mode
    _fix_image_resize_stretch(workflow)

    logger.info("V2V extend workflow prepared")
    return workflow


def prepare_v2v_pass1_workflow(
    workflow_path: str,
    prompt: str,
    width: int,
    height: int,
    duration: float,
    framerate: int,
    seed: int,
    previous_video: str,
    audio_path: str = "",
    ltx_model_gguf: Optional[str] = None,
    frame_overlap: int = 16,
    prev_video_duration: Optional[float] = None,
) -> dict:
    """
    Load and prepare V2V Pass 1 workflow (ExtendSampler → VAE Decode → save).

    Single-pass V2V pipeline with audio conditioning. The workflow includes
    a full audio chain (LoadAudio → TrimAudioDuration → LTXVAudioVAEEncode →
    SetLatentNoiseMask → LTXVConcatAVLatent) that feeds audio-visual latent
    to the LTXVExtendSampler, giving the model audio context during sampling.
    Audio is decoded via LTXVAudioVAEDecode and muxed into the output video.

    Mutates:
    - "CLIP Text Encode (Prompt)" → text input
    - "LOAD PREVIOUS VIDEO" → previous video path
    - "Load Audio" → audio file path
    - "Audio - Video Duration" → duration value
    - "Framerate" → framerate value
    - "RandomNoise" → noise_seed
    - "Resize Video Frames" → longer_edge from resolution
    - "LTXV Extend Sampler" → frame_overlap
    - "Unet Loader (GGUF)" → model variant

    Args:
        workflow_path: Path to V2V pass1 workflow JSON
        prompt: Text prompt for generation
        width: Video width
        height: Video height
        duration: Video duration in seconds
        framerate: Framerate (typically 24)
        seed: Random seed
        previous_video: Path to previous scene's video file
        audio_path: Path to scene audio file (uploaded to ComfyUI)
        ltx_model_gguf: Optional GGUF model variant name
        frame_overlap: Number of overlap frames for LTXVExtendSampler (default 16)
        prev_video_duration: Duration of the previous video for frame-skip optimization

    Returns:
        Mutated workflow dict
    """
    logger.info(f"Preparing V2V Pass 1 workflow from {workflow_path}")

    with open(workflow_path, "r", encoding="utf-8") as f:
        workflow = json.load(f)

    # Override LTX GGUF model if specified
    if ltx_model_gguf:
        try:
            set_node_input(workflow, "Unet Loader (GGUF)", "unet_name", ltx_model_gguf)
            logger.info(f"V2V Pass 1 LTX model GGUF set to: {ltx_model_gguf}")
        except ValueError:
            logger.warning("No 'Unet Loader (GGUF)' node found — skipping model override")

    # Set text prompt
    set_node_input(workflow, "CLIP Text Encode (Prompt)", "text", prompt)

    # Set timing
    set_node_input(workflow, "Audio - Video Duration", "value", duration)
    set_node_input(workflow, "Framerate", "value", framerate)

    # Set seed
    set_node_input(workflow, "RandomNoise", "noise_seed", seed)

    # Set previous video — only load the tail frames needed for overlap
    # (same logic as main V2V prep — see comments there for full explanation)
    set_node_input(workflow, "LOAD PREVIOUS VIDEO", "video", previous_video)
    set_node_input(workflow, "LOAD PREVIOUS VIDEO", "force_rate", framerate)

    if prev_video_duration and prev_video_duration > 0:
        total_frames = round(prev_video_duration * framerate)
        keep_frames = frame_overlap + 8
        skip = max(0, total_frames - keep_frames)
        if skip > 0:
            set_node_input(workflow, "LOAD PREVIOUS VIDEO", "skip_first_frames", skip)
            set_node_input(workflow, "LOAD PREVIOUS VIDEO", "frame_load_cap", keep_frames)
            logger.info(
                f"V2V Pass 1 previous video: skipping first {skip} of {total_frames} scene frames, "
                f"loading exactly {keep_frames} frames (overlap={frame_overlap}, "
                f"frame_load_cap={keep_frames})"
            )
    else:
        logger.warning("V2V Pass 1 prev_video_duration not provided — loading all frames (may use excess VRAM)")

    # Set resize longer edge
    longer_edge = max(width, height)
    try:
        set_node_input(workflow, "Resize Video Frames", "longer_edge", longer_edge)
    except ValueError:
        pass

    # Set frame_overlap on LTXVExtendSampler
    try:
        set_node_input(workflow, "LTXV Extend Sampler", "frame_overlap", frame_overlap)
        logger.info(f"V2V Pass 1 frame_overlap set to: {frame_overlap}")
    except ValueError:
        logger.warning("No 'LTXV Extend Sampler' node in Pass 1 workflow")

    # Set audio path (for audio conditioning during V2V generation)
    if audio_path:
        try:
            set_node_input(workflow, "Load Audio", "audio", audio_path)
            logger.info(f"V2V Pass 1 audio set to: {audio_path}")
        except ValueError:
            logger.warning("No 'Load Audio' node found in V2V workflow — skipping audio")

    # Fix ResizeImagesByLongerEdge nodes
    _update_resize_longer_edge(workflow, longer_edge)

    # Fix ImageResizeKJv2 stretch mode
    _fix_image_resize_stretch(workflow)

    logger.info("V2V Pass 1 workflow prepared")
    return workflow


def prepare_v2v_pass2_workflow(
    workflow_path: str,
    prompt: str,
    width: int,
    height: int,
    duration: float,
    framerate: int,
    seed: int,
    audio_path: str,
    intermediate_video: str,
    ltx_model_gguf: Optional[str] = None,
) -> dict:
    """
    Load and prepare V2V Pass 2 workflow (load intermediate → upscale → refine → save).

    This is the second half of the split-pass V2V pipeline. It loads the
    intermediate video from Pass 1, spatially upscales it, runs a 3-step
    euler refinement pass with audio, and saves the final video.

    Memory footprint matches I2V (which works on 16GB GPUs) because there
    are no cached previous-video latents — only the intermediate video is
    loaded fresh.

    Mutates:
    - "CLIP Text Encode (Prompt)" → text input
    - "LOAD INTERMEDIATE VIDEO" → intermediate video path
    - "Load Audio" → audio path
    - "Audio - Video Duration" → duration value
    - "Framerate" → framerate value
    - "RandomNoise Pass 2" → noise_seed
    - "Resize Intermediate Half" → longer_edge // 2 (VAE encode path, spatial upscaler 2x → target)
    - "Resize Intermediate Full" → longer_edge (last frame extraction for LTXVImgToVideoInplace)

    Args:
        workflow_path: Path to V2V pass2 workflow JSON
        prompt: Text prompt for generation
        width: Video width
        height: Video height
        duration: Video duration in seconds
        framerate: Framerate (typically 24)
        seed: Random seed
        audio_path: Path to audio file
        intermediate_video: Path to the intermediate video from Pass 1
        ltx_model_gguf: Optional GGUF model variant name

    Returns:
        Mutated workflow dict
    """
    logger.info(f"Preparing V2V Pass 2 workflow from {workflow_path}")

    with open(workflow_path, "r", encoding="utf-8") as f:
        workflow = json.load(f)

    # Override LTX GGUF model if specified
    if ltx_model_gguf:
        try:
            set_node_input(workflow, "Unet Loader (GGUF)", "unet_name", ltx_model_gguf)
            logger.info(f"V2V Pass 2 LTX model GGUF set to: {ltx_model_gguf}")
        except ValueError:
            logger.warning("No 'Unet Loader (GGUF)' node found — skipping model override")

    # Set text prompt
    set_node_input(workflow, "CLIP Text Encode (Prompt)", "text", prompt)

    # Set timing
    set_node_input(workflow, "Audio - Video Duration", "value", duration)
    set_node_input(workflow, "Framerate", "value", framerate)

    # Set seed for Pass 2 sampler
    try:
        set_node_input(workflow, "RandomNoise Pass 2", "noise_seed", seed)
    except ValueError:
        # Fall back to generic RandomNoise if Pass 2 node not found
        set_node_input(workflow, "RandomNoise", "noise_seed", seed)

    # Set intermediate video path
    set_node_input(workflow, "LOAD INTERMEDIATE VIDEO", "video", intermediate_video)
    set_node_input(workflow, "LOAD INTERMEDIATE VIDEO", "force_rate", framerate)

    # Set audio
    if audio_path:
        set_node_input(workflow, "Load Audio", "audio", audio_path)

    # Set dual resize paths for V2V Pass 2:
    # - "Resize Intermediate Half" at half resolution → VAE Encode → spatial upscale 2x → target
    # - "Resize Intermediate Full" at full resolution → last frame extraction for LTXVImgToVideoInplace
    # This mirrors the I2V workflow pattern: start at half res so the spatial upscaler lands at target,
    # NOT at 2x target (which caused OOM on 16GB GPUs).
    longer_edge = max(width, height)
    half_edge = longer_edge // 2

    try:
        set_node_input(workflow, "Resize Intermediate Half", "longer_edge", half_edge)
        logger.info(f"V2V Pass 2: Resize Intermediate Half set to {half_edge} (VAE encode path)")
    except ValueError:
        # Fallback: try old node name or generic update
        logger.warning("No 'Resize Intermediate Half' node — falling back to generic resize")
        _update_resize_longer_edge(workflow, half_edge)

    try:
        set_node_input(workflow, "Resize Intermediate Full", "longer_edge", longer_edge)
        logger.info(f"V2V Pass 2: Resize Intermediate Full set to {longer_edge} (last frame path)")
    except ValueError:
        logger.warning("No 'Resize Intermediate Full' node — skipping full-res resize")

    # Bypass LTXVImgToVideoInplace for V2V — this node conditions the first frame
    # with a reference image, which is correct for I2V but harmful for V2V where
    # the overlap transition from ExtendSampler should be preserved as-is.
    # Without bypass, the last frame of the intermediate fades into the start of
    # the video, overwriting the scene 1→2 transition region.
    try:
        set_node_input(workflow, "LTXVImgToVideoInplace Pass 2 (BYPASSED for V2V)", "bypass", True)
        logger.info("V2V Pass 2: LTXVImgToVideoInplace bypassed (preserving overlap transition)")
    except ValueError:
        # Try original title as fallback
        try:
            set_node_input(workflow, "LTXVImgToVideoInplace Pass 2", "bypass", True)
            logger.info("V2V Pass 2: LTXVImgToVideoInplace bypassed (fallback title)")
        except ValueError:
            logger.warning("No LTXVImgToVideoInplace node found — bypass not applied")

    # Normalize LTXVPreprocess compression
    _update_ltxv_preprocess_compression(workflow, img_compression=35)

    # Fix ImageResizeKJv2 stretch mode
    _fix_image_resize_stretch(workflow)

    logger.info("V2V Pass 2 workflow prepared")
    return workflow


def prepare_transition_workflow(
    workflow_path: str,
    prompt: str,
    width: int,
    height: int,
    duration: float,
    framerate: int,
    seed: int,
    audio_path: str,
    first_frame: str,
    last_frame: str,
    ltx_model_gguf: Optional[str] = None,
    transition_strength: float = 1.0,
) -> dict:
    """
    Load and prepare a Transition LoRA workflow.

    Generates a short AI transition clip between two scenes using the
    LTX Transition LoRA (valiantcat/LTX-2.3-Transition-LORA).
    Conditions on first frame (end of scene A) and last frame (start
    of scene B) with the "zhuanchang" trigger word.

    Mutates:
    - "CLIP Text Encode (Prompt)" → text with trigger word
    - "LOAD FIRST FRAME" → first frame image path
    - "LOAD LAST FRAME" → last frame image path
    - "Load Audio" → audio path
    - "WIDTH/HEIGHT" → dimensions
    - "Audio - Video Duration" → transition duration
    - "Framerate" → framerate
    - "RandomNoise" → seeds

    Args:
        workflow_path: Path to transition workflow JSON
        prompt: Text prompt describing the transition
        width: Video width
        height: Video height
        duration: Transition clip duration in seconds (typically 2-4)
        framerate: Framerate (typically 24)
        seed: Random seed
        audio_path: Path to audio file for the transition segment
        first_frame: Path to the last frame of scene A
        last_frame: Path to the first frame of scene B
        ltx_model_gguf: Optional GGUF model variant name
        transition_strength: Transition LoRA strength (default 1.0)

    Returns:
        Mutated workflow dict
    """
    logger.info(f"Preparing transition workflow from {workflow_path}")

    with open(workflow_path, "r", encoding="utf-8") as f:
        workflow = json.load(f)

    # Override LTX GGUF model if specified
    if ltx_model_gguf:
        try:
            set_node_input(workflow, "Unet Loader (GGUF)", "unet_name", ltx_model_gguf)
            logger.info(f"Transition LTX model GGUF set to: {ltx_model_gguf}")
        except ValueError:
            logger.warning("No 'Unet Loader (GGUF)' node found — skipping model override")

    # Set text prompt — ensure trigger word is present.
    # Model card recommends placing trigger word at the END of the prompt
    # so the base prompt content drives the generation, with the LoRA
    # activated by the trailing trigger word.
    if "zhuanchang" not in prompt.lower():
        prompt = f"{prompt}, zhuanchang"
    set_node_input(workflow, "CLIP Text Encode (Prompt)", "text", prompt)

    # Set dimensions
    set_node_input(workflow, "WIDTH", "value", width)
    set_node_input(workflow, "HEIGHT", "value", height)

    # Set timing
    set_node_input(workflow, "Audio - Video Duration", "value", duration)
    set_node_input(workflow, "Framerate", "value", framerate)

    # Set seeds
    set_node_input(workflow, "RandomNoise", "noise_seed", seed)
    try:
        set_node_input(workflow, "RandomNoise Pass 2", "noise_seed", seed + 1)
    except ValueError:
        pass

    # Set frame images
    set_node_input(workflow, "LOAD FIRST FRAME", "image", first_frame)
    set_node_input(workflow, "LOAD LAST FRAME", "image", last_frame)

    # Set audio
    if audio_path:
        set_node_input(workflow, "Load Audio", "audio", audio_path)

    # Set resize longer edge
    longer_edge = max(width, height)
    _update_resize_longer_edge(workflow, longer_edge)

    # Normalize LTXVPreprocess compression
    _update_ltxv_preprocess_compression(workflow, img_compression=35)

    # Fix ImageResizeKJv2 stretch mode
    _fix_image_resize_stretch(workflow)

    # Update transition LoRA strength if not default
    if transition_strength != 1.0:
        for node_id, node in workflow.items():
            if not isinstance(node, dict):
                continue
            if node.get("class_type") == "Power Lora Loader (rgthree)":
                inputs = node.get("inputs", {})
                # Find the transition LoRA slot
                for key, val in inputs.items():
                    if isinstance(val, dict) and val.get("lora") == "ltx2.3-transition.safetensors":
                        val["strength"] = transition_strength
                        logger.info(f"Transition LoRA strength set to: {transition_strength}")
                        break

    logger.info("Transition workflow prepared")
    return workflow


def flatten_group_nodes(workflow: dict) -> dict:
    """
    Flatten ComfyUI group node IDs so all nodes become top-level.

    ComfyUI group nodes use composite IDs like "1217:1089" (group_id:inner_node_id).
    Some ComfyUI execution engines skip top-level nodes that depend on outputs from
    inside a group node (cross-boundary dependencies). This causes nodes like
    VHS_VideoCombine to never execute because their inputs reference group-internal
    nodes like "1217:1156".

    This function:
    1. Renames all "X:Y" node IDs to "X_Y" (making them top-level)
    2. Updates ALL input connection references throughout the workflow

    Must be called BEFORE stamp_vhs_unique_prefix() and workflow submission.

    Args:
        workflow: ComfyUI workflow dict (API format)

    Returns:
        The same workflow dict with flattened IDs (mutated in-place)
    """
    # Build mapping of old_id -> new_id for all group-internal nodes
    id_map = {}
    for node_id in list(workflow.keys()):
        if ":" in str(node_id):
            new_id = str(node_id).replace(":", "_")
            id_map[node_id] = new_id

    if not id_map:
        logger.debug("No group nodes found — skipping flatten")
        return workflow

    logger.info(
        f"Flattening {len(id_map)} group node IDs "
        f"(e.g. {list(id_map.items())[0][0]} → {list(id_map.items())[0][1]})"
    )

    # Step 1: Rename node keys in the workflow dict
    for old_id, new_id in id_map.items():
        workflow[new_id] = workflow.pop(old_id)

    # Step 2: Update all input connection references
    # Connection references are lists like ["1217:1089", 0] where index 0 is the
    # source node ID. Walk all node inputs and update any matching references.
    for node_id, node in workflow.items():
        if not isinstance(node, dict):
            continue
        inputs = node.get("inputs")
        if not inputs or not isinstance(inputs, dict):
            continue
        for input_name, input_val in inputs.items():
            _update_refs_recursive(input_val, id_map, inputs, input_name)

    logger.info(f"Flattened {len(id_map)} group node IDs to top-level")
    return workflow


def _update_refs_recursive(
    value: Any, id_map: dict, parent: Any, key: Any
) -> None:
    """Recursively walk a value and update connection references.

    ComfyUI connection refs are [node_id_str, output_index_int].
    """
    if isinstance(value, list):
        # Check if this is a connection reference: [str, int]
        if (
            len(value) == 2
            and isinstance(value[0], str)
            and isinstance(value[1], int)
            and value[0] in id_map
        ):
            value[0] = id_map[value[0]]
        else:
            # Recurse into list items
            for i, item in enumerate(value):
                _update_refs_recursive(item, id_map, value, i)
    elif isinstance(value, dict):
        for k, v in value.items():
            _update_refs_recursive(v, id_map, value, k)


def strip_non_essential_nodes(workflow: dict) -> List[str]:
    """
    Remove non-essential utility/debug nodes that don't contribute to generation output.

    These nodes are workflow-author conveniences (image comparison displays)
    that can cause validation errors when submitted to servers with different
    node versions or when group node flattening changes the graph structure.

    Removes:
    - Image Comparer (rgthree) — debug comparison display, not needed for generation

    NOTE: easy cleanGpuUsed and easy clearCacheAll are intentionally KEPT.
    These nodes actively free VRAM between sampling passes and before VAE
    decode, which is critical for preventing OOM on 16GB GPUs. Stripping
    them was causing V2V and other multi-pass workflows to OOM during Pass 2.
    If the server doesn't have these nodes installed, the missing-node
    auto-removal system (validate_node_types + remove_missing_nodes) will
    handle them gracefully.

    Also rewires connections: if node B depends on a removed node A, and A has a single
    input connection, B is rewired to use A's source directly.

    Args:
        workflow: ComfyUI workflow dict (mutated in-place)

    Returns:
        List of removed node descriptions
    """
    STRIP_CLASS_TYPES = {
        "Image Comparer (rgthree)",
    }

    removed = []
    nodes_to_remove = []

    for node_id, node in workflow.items():
        if not isinstance(node, dict):
            continue
        class_type = node.get("class_type", "")
        if class_type in STRIP_CLASS_TYPES:
            nodes_to_remove.append(node_id)

    if not nodes_to_remove:
        return removed

    for node_id in nodes_to_remove:
        node = workflow[node_id]
        class_type = node.get("class_type", "")
        title = node.get("_meta", {}).get("title", "")

        # Find this node's first input connection (its source)
        source_ref = None
        inputs = node.get("inputs", {})
        for inp_name, inp_val in inputs.items():
            if isinstance(inp_val, list) and len(inp_val) == 2 and isinstance(inp_val[0], str):
                source_ref = inp_val
                break

        # Rewire any nodes that reference this node to use its source instead
        for other_id, other_node in workflow.items():
            if not isinstance(other_node, dict) or other_id == node_id:
                continue
            other_inputs = other_node.get("inputs", {})
            for inp_name, inp_val in list(other_inputs.items()):
                if (
                    isinstance(inp_val, list)
                    and len(inp_val) == 2
                    and isinstance(inp_val[0], str)
                    and inp_val[0] == node_id
                ):
                    if source_ref:
                        other_inputs[inp_name] = list(source_ref)
                    else:
                        # No source to rewire to — remove the connection
                        del other_inputs[inp_name]

        del workflow[node_id]
        removed.append(f"{class_type} (node {node_id}, title='{title}')")

    if removed:
        logger.info(f"Stripped {len(removed)} non-essential nodes: {removed}")

    return removed


def validate_node_types(workflow: dict, available_types: set) -> List[str]:
    """
    Check all workflow node class_types exist in the server's available types.

    Args:
        workflow: ComfyUI workflow dict
        available_types: Set of class_type strings from /object_info

    Returns:
        List of missing class_type strings (empty if all valid)
    """
    missing = []
    for node_id, node in workflow.items():
        if not isinstance(node, dict):
            continue
        class_type = node.get("class_type", "")
        if class_type and class_type not in available_types:
            title = node.get("_meta", {}).get("title", "")
            missing.append(f"{class_type} (node {node_id}, title='{title}')")
    return missing


def remove_missing_nodes(workflow: dict, available_types: set) -> List[str]:
    """
    Remove nodes with class_types not available on the target server.

    For utility nodes (GPU cleanup, cache clear, etc.) that sit between
    processing nodes, this rewires connections to bypass them. For essential
    nodes that can't be bypassed, logs a warning.

    This prevents ComfyUI from excluding entire dependency chains (including
    VHS_VideoCombine) when a non-essential custom node is missing.

    Args:
        workflow: ComfyUI workflow dict (mutated in-place)
        available_types: Set of class_type strings from /object_info

    Returns:
        List of removed node descriptions
    """
    removed = []
    nodes_to_remove = []

    for node_id, node in workflow.items():
        if not isinstance(node, dict):
            continue
        class_type = node.get("class_type", "")
        if class_type and class_type not in available_types:
            nodes_to_remove.append(node_id)

    if not nodes_to_remove:
        return removed

    for node_id in nodes_to_remove:
        node = workflow[node_id]
        class_type = node.get("class_type", "")
        title = node.get("_meta", {}).get("title", "")

        # Find what this node's input connections are (its sources)
        # If this node has a single primary input connection, we can
        # rewire its consumers to point to that source instead
        input_connections = {}
        for inp_name, inp_val in node.get("inputs", {}).items():
            if isinstance(inp_val, list) and len(inp_val) == 2 and isinstance(inp_val[0], str):
                input_connections[inp_name] = inp_val

        # Find all downstream nodes that reference this node
        consumers = []
        for other_id, other_node in workflow.items():
            if not isinstance(other_node, dict) or other_id == node_id:
                continue
            for inp_name, inp_val in other_node.get("inputs", {}).items():
                if (isinstance(inp_val, list) and len(inp_val) == 2
                        and isinstance(inp_val[0], str) and inp_val[0] == node_id):
                    consumers.append((other_id, inp_name, inp_val[1]))

        # For pass-through nodes (single input of matching type → single output),
        # rewire consumers to skip this node
        if input_connections and consumers:
            # Use the first input connection as the bypass source
            first_inp = list(input_connections.values())[0]
            for consumer_id, consumer_inp_name, output_slot in consumers:
                workflow[consumer_id]["inputs"][consumer_inp_name] = first_inp
                logger.info(
                    f"Rewired {consumer_id}.{consumer_inp_name}: "
                    f"was [{node_id}, {output_slot}] → now {first_inp}"
                )

        del workflow[node_id]
        desc = f"{class_type} (node {node_id}, title='{title}')"
        removed.append(desc)
        logger.warning(f"Removed missing node: {desc}")

    if removed:
        logger.warning(
            f"Removed {len(removed)} missing node type(s) from workflow: "
            f"{', '.join(removed)}"
        )
    return removed


def stamp_vhs_unique_prefix(workflow: dict, unique_tag: str) -> Optional[str]:
    """
    Find VHS_VideoCombine node and set a unique filename_prefix.

    This enables fallback file download when ComfyUI's /history API
    doesn't include VHS output (observed on some server configurations).
    With a unique prefix, we know exactly what filename to look for.

    VHS names output files as: {filename_prefix}_{counter:05d}_.mp4
    Since the prefix is unique per job, counter will always be 00001.

    Args:
        workflow: ComfyUI workflow dict
        unique_tag: Short unique identifier (e.g., job ID prefix)

    Returns:
        The unique prefix set, or None if no VHS node found
    """
    for node_id, node in workflow.items():
        if not isinstance(node, dict):
            continue
        if node.get("class_type") == "VHS_VideoCombine":
            original = node.get("inputs", {}).get("filename_prefix", "VHS")
            unique_prefix = f"{original}_j{unique_tag}"
            node.setdefault("inputs", {})["filename_prefix"] = unique_prefix
            logger.info(
                f"VHS node {node_id}: filename_prefix '{original}' → '{unique_prefix}'"
            )
            return unique_prefix
    return None


def prepare_workflow_from_config(
    workflow_json: dict, field_mappings: List[dict], values: dict
) -> dict:
    """
    Prepare a workflow for submission using WorkflowConfig's field_mappings.

    This is the universal workflow preparation method that works with any workflow,
    not just the hardcoded Klein/LTX ones.

    Args:
        workflow_json: The workflow JSON dict
        field_mappings: List of field mapping dicts from WorkflowConfig.field_mappings
        values: Dict mapping field_type to value, e.g.:
            {"prompt": "...", "width": 1024, "height": 768, "seed": 123, "image": "path.png"}

    Returns:
        Mutated workflow copy ready for submission

    Raises:
        ValueError: If a required field cannot be mapped
    """
    import copy

    # Deep copy to avoid mutating original
    workflow = copy.deepcopy(workflow_json)

    logger.info(f"Preparing workflow with {len(field_mappings)} field mappings")

    for mapping in field_mappings:
        node_title = mapping.get("node_title")
        input_name = mapping.get("input_name")
        field_type = mapping.get("field_type")

        # Skip if no value provided for this field type
        if field_type not in values or values[field_type] is None:
            logger.debug(f"Skipping field {field_type} (no value provided)")
            continue

        value = values[field_type]

        try:
            set_node_input(workflow, node_title, input_name, value)
            logger.debug(f"Applied: {field_type} -> {node_title}.{input_name}")
        except ValueError as e:
            logger.warning(f"Failed to apply mapping {field_type}: {e}")
            raise

    logger.info("Workflow prepared from config")
    return workflow
