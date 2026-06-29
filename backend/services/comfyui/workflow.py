"""
ComfyUI Workflow Mutation Engine

Provides utilities for loading, finding nodes, and mutating workflow configurations.
"""

import json
import logging
import math
import os
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


def _update_power_lora_distilled(workflow: dict, distilled_lora_name: str) -> None:
    """
    Update the distilled LoRA filename in PowerLoraLoader nodes.

    Non-Sequencer LTX workflows use PowerLoraLoader with lora_1, lora_2, etc.
    sub-dicts. The distilled LoRA is always in lora_1. This function finds any
    PowerLoraLoader node and updates lora_1.lora to the user's selection.
    """
    for node_id, node in workflow.items():
        if not isinstance(node, dict):
            continue
        class_type = node.get("class_type", "")
        if "Power Lora Loader" in class_type or "PowerLoraLoader" in class_type:
            inputs = node.get("inputs", {})
            lora_1 = inputs.get("lora_1")
            if isinstance(lora_1, dict) and "lora" in lora_1:
                old_name = lora_1["lora"]
                lora_1["lora"] = distilled_lora_name
                logger.info(
                    f"PowerLoraLoader distilled LoRA updated: "
                    f"{old_name} → {distilled_lora_name}"
                )
                return
    logger.debug("No PowerLoraLoader node found — distilled LoRA not updated")


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

    NOTE: Klein workflows have NO negative prompt node. The CFGGuider's negative
    input is wired to empty conditioning. Do NOT append negative prompt text to
    the positive prompt — it pollutes the generation.

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
        ref_images: List of reference image paths (1-5 images)

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
    # NOTE: No negative prompt appended here — Klein has no negative prompt node
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

        if len(ref_images) >= 5:
            set_node_input(workflow, "Reference 5", "image", ref_images[4])

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
    distilled_lora_name: Optional[str] = None,
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

    # Override distilled LoRA if specified
    if distilled_lora_name:
        _update_power_lora_distilled(workflow, distilled_lora_name)

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
    # Audio - Video Duration is an INTConstant node — ComfyUI silently
    # truncates float values.  Use math.ceil so the generated video is
    # always at least as long as the scene; normalize_clip's max_duration
    # trims the excess back to the exact frame boundary.
    set_node_input(workflow, "Audio - Video Duration", "value", math.ceil(duration))
    set_node_input(workflow, "Framerate", "value", framerate)

    # Set seed
    set_node_input(workflow, "RandomNoise", "noise_seed", seed)

    # Guard: first_frame is required — without it ComfyUI uses its baked-in
    # default image and produces garbage output with no error.
    if not first_frame:
        raise ValueError(
            "LTX video workflow requires a first frame image but none was provided. "
            "Ensure the scene has a generated first frame image before running video generation."
        )

    # Frame-to-Frame variant
    if first_frame and last_frame:
        logger.info("Using FF/LF variant")
        set_node_input(workflow, "LOAD FIRST IMAGE FRAME", "image", first_frame)
        set_node_input(workflow, "LOAD LAST IMAGE FRAME", "image", last_frame)
    # Image-to-Video variant
    else:
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

    # Set timing — ceil to avoid INT truncation (see prepare_ltx_workflow comment)
    set_node_input(workflow, "Audio - Video Duration", "value", math.ceil(duration))
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

    # Set timing — ceil to avoid INT truncation (see prepare_ltx_workflow comment)
    set_node_input(workflow, "Audio - Video Duration", "value", math.ceil(duration))
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

    # Set timing — ceil to avoid INT truncation (see prepare_ltx_workflow comment)
    set_node_input(workflow, "Audio - Video Duration", "value", math.ceil(duration))
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
    distilled_lora_name: Optional[str] = None,
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

    # Override distilled LoRA if specified
    if distilled_lora_name:
        _update_power_lora_distilled(workflow, distilled_lora_name)

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

    # Set timing — ceil to avoid INT truncation (see prepare_ltx_workflow comment)
    set_node_input(workflow, "Audio - Video Duration", "value", math.ceil(duration))
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

        # INTConstant truncation guard: the LTX "Audio - Video Duration"
        # node is an INTConstant; ComfyUI silently floors any float passed
        # to it. The 6 hardcoded prepare_*_workflow() functions apply
        # math.ceil() before calling set_node_input - apply the same fix
        # here so user-uploaded WorkflowConfig templates don't re-trigger
        # the bug. See feedback_v2v_join_split_fix memory note.
        if field_type == "duration" and isinstance(value, float):
            value = math.ceil(value)

        try:
            set_node_input(workflow, node_title, input_name, value)
            logger.debug(f"Applied: {field_type} -> {node_title}.{input_name}")
        except ValueError as e:
            logger.warning(f"Failed to apply mapping {field_type}: {e}")
            raise

    logger.info("Workflow prepared from config")
    return workflow


def prepare_zimage_workflow(
    workflow_path: str,
    prompt: str,
    width: int,
    height: int,
    seed: int,
) -> dict:
    """
    Load and prepare a Z-Image Turbo text-to-image workflow.

    Z-Image is text-only — no reference images.
    Uses KSampler with 8 steps, res_multistep sampler.

    NOTE: Z-Image workflows have NO negative prompt node. Do NOT append
    negative prompt text to the positive prompt.

    Mutates:
    - "CLIP Text Encode (Positive Prompt)" → text input
    - "Empty Latent Image" → width, height
    - "KSampler" → seed
    """
    logger.info(f"Preparing Z-Image workflow from {workflow_path}")

    with open(workflow_path, "r", encoding="utf-8") as f:
        workflow = json.load(f)

    # Set text prompt with anti-text suffix
    # NOTE: No negative prompt appended here — Z-Image has no negative prompt node
    anti_text_suffix = ", no text, no subtitles, no captions, no words, no letters, no watermarks"
    full_prompt = prompt + anti_text_suffix if prompt else prompt
    set_node_input(workflow, "CLIP Text Encode (Positive Prompt)", "text", full_prompt)

    # Set dimensions
    set_node_input(workflow, "Empty Latent Image", "width", width)
    set_node_input(workflow, "Empty Latent Image", "height", height)

    # Set seed
    set_node_input(workflow, "KSampler", "seed", seed)

    logger.info("Z-Image workflow prepared")
    return workflow


def _krea2_set_by_title_or_class(
    workflow: dict, titles, class_types, input_name: str, value, label: str
) -> bool:
    """Set ``input_name``=value on the first node matching any of ``titles``
    (by _meta.title) or, failing that, any of ``class_types`` (by class_type).

    Tolerant helper for the Krea 2 workflow whose exact node titles are not
    known until the user supplies their tested JSON.  Returns True if a node
    was updated.  Never raises — logs a warning if nothing matched.
    """
    # 1) exact title match
    for t in titles:
        for node in workflow.values():
            if isinstance(node, dict) and node.get("_meta", {}).get("title") == t:
                node.setdefault("inputs", {})[input_name] = value
                logger.debug(f"Krea2: set {t}.{input_name} = {value}")
                return True
    # 2) class_type match (first node that has the input slot)
    for node in workflow.values():
        if not isinstance(node, dict):
            continue
        if node.get("class_type") in class_types:
            inp = node.setdefault("inputs", {})
            if input_name in inp or not inp:
                inp[input_name] = value
                logger.debug(f"Krea2: set {node.get('class_type')}.{input_name} = {value}")
                return True
    logger.warning(f"Krea2: could not find a node to set {label} ({input_name})")
    return False


def prepare_krea2_workflow(
    workflow_path: str,
    prompt: str,
    width: int,
    height: int,
    seed: int,
    model_name: Optional[str] = None,
) -> dict:
    """Load and prepare a Krea 2 Turbo text-to-image workflow.

    Krea 2 Turbo is a single-pass, text-only model (no reference images, no
    negative prompt — it runs at CFG ~1).  This mirrors prepare_zimage_workflow
    but is resilient to node-title variation since the user supplies their own
    tested workflow JSON.

    Sets (by title, falling back to class_type):
    - positive CLIP Text Encode  -> text (+ anti-text suffix)
    - empty latent image          -> width, height
    - KSampler / sampler          -> seed (seed or noise_seed)
    - diffusion model loader       -> unet_name (only if model_name provided)
    """
    logger.info(f"Preparing Krea2 workflow from {workflow_path}")
    with open(workflow_path, "r", encoding="utf-8") as f:
        workflow = json.load(f)

    # Defensive numeric coercion — NEVER raise on a null/odd width/height/seed.
    # A raise here would be caught by the dispatcher's redirect and silently fall
    # back to Z-Image, which looks like "Krea 2 isn't working".
    def _as_int(v, default):
        try:
            return int(v)
        except (TypeError, ValueError):
            return int(default)
    width = _as_int(width, 1024)
    height = _as_int(height, 1024)
    seed = _as_int(seed, 0)

    anti_text_suffix = ", no text, no subtitles, no captions, no words, no letters, no watermarks"
    full_prompt = (prompt + anti_text_suffix) if prompt else prompt

    # ── Prompt ──
    # The Krea 2 graph feeds its prompt from a PrimitiveStringMultiline
    # ("MANUAL PROMPT") through an Any Switch into the CLIP Text Encode.  Write
    # into that primitive's .value — NEVER into CLIPTextEncode.text when it is
    # wired to an upstream node (a list), which would sever the graph.
    _prompt_set = False
    for node in workflow.values():
        if not isinstance(node, dict):
            continue
        ct = node.get("class_type", "")
        title = (node.get("_meta", {}) or {}).get("title", "") or ""
        inp = node.get("inputs", {})
        if (ct in ("PrimitiveStringMultiline", "PrimitiveString")
                or "PROMPT" in title.upper()) and "value" in inp \
                and not isinstance(inp.get("value"), list):
            inp["value"] = full_prompt
            _prompt_set = True
            logger.debug(f"Krea2: set prompt on '{title or ct}'.value")
            break
    if not _prompt_set:
        # Fallback: a CLIP Text Encode whose text is a literal string (not wired)
        for node in workflow.values():
            if isinstance(node, dict) and node.get("class_type") == "CLIPTextEncode":
                inp = node.setdefault("inputs", {})
                if not isinstance(inp.get("text"), list):
                    inp["text"] = full_prompt
                    _prompt_set = True
                    logger.debug("Krea2: set prompt on CLIPTextEncode.text (literal)")
                    break
    if not _prompt_set:
        logger.warning("Krea2: no prompt node found to set")

    # ── Dimensions ── (Empty[SD3]LatentImage) — dynamic scene resolution
    _krea2_set_by_title_or_class(
        workflow,
        ["Empty Latent Image", "EmptySD3LatentImage", "Empty SD3 Latent Image"],
        {"EmptySD3LatentImage", "EmptyLatentImage"},
        "width", width, "latent width",
    )
    _krea2_set_by_title_or_class(
        workflow,
        ["Empty Latent Image", "EmptySD3LatentImage", "Empty SD3 Latent Image"],
        {"EmptySD3LatentImage", "EmptyLatentImage"},
        "height", height, "latent height",
    )

    # ── Seed ── set EVERY seed slot in the sampling chain so the app's per-scene
    # seed fully controls reproducibility AND per-scene variance.  We touch ONLY
    # the seed fields — never steps/cfg/sampler/scheduler/denoise or any of the
    # Smart-Seed-Variance / Krea2-Rebalance correction settings.
    _seed_targets = 0
    for node in workflow.values():
        if not isinstance(node, dict):
            continue
        ct = node.get("class_type", "")
        inp = node.get("inputs", {})
        if ct == "KSampler" and not isinstance(inp.get("seed"), list) and "seed" in inp:
            inp["seed"] = seed; _seed_targets += 1
        elif ct == "RBG_Smart_Seed_Variance" and not isinstance(inp.get("seed"), list) and "seed" in inp:
            inp["seed"] = seed; _seed_targets += 1
        elif ct in ("RandomNoise", "KSamplerSelect") and not isinstance(inp.get("noise_seed"), list) and "noise_seed" in inp:
            inp["noise_seed"] = seed; _seed_targets += 1
    if not _seed_targets:
        logger.warning("Krea2: no seed node found to set")
    else:
        logger.debug(f"Krea2: set seed={seed} on {_seed_targets} node(s)")

    # ── Diffusion model file ── (fp8 vs mxfp8 per server GPU) — only if provided
    if model_name:
        _krea2_set_by_title_or_class(
            workflow,
            ["Load Diffusion Model", "UNETLoader", "Unet Loader"],
            {"UNETLoader"},
            "unet_name", model_name, "diffusion model name",
        )

    logger.info("Krea2 workflow prepared")
    return workflow


def prepare_krea2_ideogram_workflow(
    workflow_path: str,
    caption: dict,
    width: int,
    height: int,
    seed: int,
    model_name: Optional[str] = None,
) -> dict:
    """Prepare a Krea 2 workflow that uses the Ideogram 4 Prompt Builder node.

    ``caption`` is the normalized Ideogram caption (see
    prompt_enhancer.normalize_ideogram_caption): high_level_description,
    background, style ("photo"/"art"), style_detail, aesthetics, lighting,
    medium, style_palette (list of hex), elements (list of
    {type,text,desc,palette,x,y,w,h} with x/y = top-left, w/h = size, all 0-1).

    Populates the Ideogram4PromptBuilderKJ node; the node assembles + converts to
    the final structured caption and the Any Switch feeds it to the CLIP encoder.
    Leaves all tuned sampler / variance / model settings untouched (only fills the
    prompt-builder fields, dims, seed, and the diffusion model file).
    """
    import json as _json

    def _as_int(v, default):
        try:
            return int(v)
        except (TypeError, ValueError):
            return int(default)
    width = _as_int(width, 1024)
    height = _as_int(height, 1024)
    seed = _as_int(seed, 0)

    logger.info(f"Preparing Krea2+Ideogram workflow from {workflow_path}")
    with open(workflow_path, "r", encoding="utf-8") as f:
        workflow = json.load(f)

    caption = caption or {}
    style = "photo" if str(caption.get("style", "photo")).lower().startswith("photo") else "art"
    # Element list in the node's expected shape (obj gets text:"").
    elements = []
    for e in (caption.get("elements") or []):
        if not isinstance(e, dict):
            continue
        elements.append({
            "type": "text" if e.get("type") == "text" else "obj",
            "text": str(e.get("text", "") or ""),
            "desc": str(e.get("desc", "") or ""),
            "palette": list(e.get("palette") or []),
            "x": float(e.get("x", 0) or 0),
            "y": float(e.get("y", 0) or 0),
            "w": float(e.get("w", 0) or 0),
            "h": float(e.get("h", 0) or 0),
        })

    # Locate the Ideogram4PromptBuilderKJ node and fill its widgets.
    _found = False
    for node in workflow.values():
        if not isinstance(node, dict) or node.get("class_type") != "Ideogram4PromptBuilderKJ":
            continue
        inp = node.setdefault("inputs", {})
        inp["width"] = width
        inp["height"] = height
        inp["high_level_description"] = str(caption.get("high_level_description", "") or "")
        inp["background"] = str(caption.get("background", "") or "")
        inp["style"] = "photo" if style == "photo" else "art_style"
        # Set the matching detail field; unknown keys are ignored by ComfyUI.
        if style == "photo":
            inp["style.photo"] = str(caption.get("style_detail", "") or "")
        else:
            inp["style.art_style"] = str(caption.get("style_detail", "") or "")
        inp["aesthetics"] = str(caption.get("aesthetics", "") or "")
        inp["lighting"] = str(caption.get("lighting", "") or "")
        inp["medium"] = str(caption.get("medium", "photograph") or "photograph")
        inp["style_palette_data"] = _json.dumps(list(caption.get("style_palette") or []))
        inp["elements_data"] = _json.dumps(elements)
        # Keep the node's conversion widgets as authored, but enforce the
        # contract our coords assume (normalized x/y/w/h, yx output order).
        inp["coord_mode"] = "normalized"
        inp["bbox_order"] = "yx"
        inp.setdefault("output_format", "compact")
        inp.setdefault("import_mode", "when empty")
        _found = True
        break
    if not _found:
        logger.warning("Krea2+Ideogram: Ideogram4PromptBuilderKJ node not found in workflow")

    # Backstop: also write the high-level description into the MANUAL PROMPT
    # primitive (the Any Switch falls back to it if the builder yields nothing).
    hld = str(caption.get("high_level_description", "") or "")
    if hld:
        for node in workflow.values():
            if isinstance(node, dict) and node.get("class_type") in ("PrimitiveStringMultiline", "PrimitiveString"):
                ip = node.setdefault("inputs", {})
                if not isinstance(ip.get("value"), list):
                    ip["value"] = hld
                    break

    # Seeds (KSampler + RBG variance + RandomNoise) — seed fields only.
    for node in workflow.values():
        if not isinstance(node, dict):
            continue
        ct = node.get("class_type", "")
        ip = node.get("inputs", {})
        if ct == "KSampler" and "seed" in ip and not isinstance(ip.get("seed"), list):
            ip["seed"] = seed
        elif ct == "RBG_Smart_Seed_Variance" and "seed" in ip and not isinstance(ip.get("seed"), list):
            ip["seed"] = seed
        elif ct in ("RandomNoise", "KSamplerSelect") and "noise_seed" in ip and not isinstance(ip.get("noise_seed"), list):
            ip["noise_seed"] = seed

    # Latent dims — the EmptyLatentImage drives the actual render resolution, so it
    # must follow the scene resolution (the Ideogram node's width/height only feed
    # the caption builder's coordinate context). Mirrors prepare_krea2_workflow.
    for node in workflow.values():
        if not isinstance(node, dict):
            continue
        if node.get("class_type") in ("EmptyLatentImage", "EmptySD3LatentImage"):
            ip = node.setdefault("inputs", {})
            if not isinstance(ip.get("width"), list):
                ip["width"] = width
            if not isinstance(ip.get("height"), list):
                ip["height"] = height

    # Diffusion model file (fp8 / mxfp8) override.
    if model_name:
        for node in workflow.values():
            if isinstance(node, dict) and node.get("class_type") == "UNETLoader":
                node.setdefault("inputs", {})["unet_name"] = model_name
                break

    logger.info(f"Krea2+Ideogram workflow prepared ({len(elements)} element(s))")
    return workflow


def prepare_sequencer_workflow(
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
    distilled_lora_name: Optional[str] = None,
    negative_prompt: str = "",
    audio_guidance: float = 0.001,
    guide_strength: float = 0.5,
    stitch: bool = False,
    image_description: str = "",
) -> dict:
    """
    Load and prepare an LTX Sequencer-based video generation workflow.

    Uses LTXDirector + LTXDirectorGuide nodes for frame conditioning.
    Supports I2V (single first frame), FF/LF (first + last frame), and V2V extend.
    Includes distilled LoRA for fast 8-step generation.

    Mutates:
    - "LTXDirector" → text prompt, negative_prompt, audio_guidance, stitch,
      image_description, duration, width, height, framerate, segments
    - "LTXDirectorGuide" → strength
    - "LOAD IMAGE" or "LOAD FIRST IMAGE FRAME" → first frame image
    - "LOAD LAST IMAGE FRAME" → last frame image (FF/LF only)
    - "Load Audio" → audio path
    - "WIDTH", "HEIGHT" → dimension constants
    - "Audio - Video Duration" → duration
    - "Framerate" → framerate
    - "RandomNoise" → noise_seed
    - "Unet Loader (GGUF)" → model GGUF override
    - "Distilled LoRA" → LoRA filename
    """
    logger.info(f"Preparing Sequencer workflow from {workflow_path}")

    with open(workflow_path, "r", encoding="utf-8") as f:
        workflow = json.load(f)

    # Override LTX GGUF model if specified
    if ltx_model_gguf:
        try:
            set_node_input(workflow, "Unet Loader (GGUF)", "unet_name", ltx_model_gguf)
            logger.info(f"LTX model GGUF set to: {ltx_model_gguf}")
        except ValueError:
            logger.warning("No 'Unet Loader (GGUF)' node found — skipping model override")

    # Override distilled LoRA if specified
    if distilled_lora_name:
        try:
            set_node_input(workflow, "Distilled LoRA", "lora_name", distilled_lora_name)
            logger.info(f"Distilled LoRA set to: {distilled_lora_name}")
        except ValueError:
            logger.warning("No 'Distilled LoRA' node found — skipping LoRA override")

    # Set prompt on LTXDirector node
    try:
        set_node_input(workflow, "LTXDirector", "text", prompt)
    except ValueError:
        logger.warning("No LTXDirector node found — falling back to CLIP Text Encode")
        set_node_input(workflow, "CLIP Text Encode (Prompt)", "text", prompt)

    # Set LTXDirector advanced parameters
    try:
        set_node_input(workflow, "LTXDirector", "negative_prompt", negative_prompt)
        set_node_input(workflow, "LTXDirector", "audio_guidance", audio_guidance)
        set_node_input(workflow, "LTXDirector", "stitch", stitch)
        if image_description:
            set_node_input(workflow, "LTXDirector", "image_description", image_description)
        logger.info(
            f"LTXDirector: neg_prompt={'yes' if negative_prompt else 'no'}, "
            f"audio_guidance={audio_guidance}, stitch={stitch}, "
            f"image_desc={'yes' if image_description else 'no'}"
        )
    except ValueError:
        logger.warning("LTXDirector node not found — skipping advanced params")

    # Set LTXDirectorGuide strength
    try:
        set_node_input(workflow, "LTXDirectorGuide", "strength", guide_strength)
        logger.info(f"LTXDirectorGuide strength set to: {guide_strength}")
    except ValueError:
        logger.warning("No LTXDirectorGuide node found — skipping strength override")

    # Set dimensions
    set_node_input(workflow, "WIDTH", "value", width)
    set_node_input(workflow, "HEIGHT", "value", height)

    # Set duration and framerate — ceil to avoid INT truncation
    # (see prepare_ltx_workflow comment for full explanation)
    set_node_input(workflow, "Audio - Video Duration", "value", math.ceil(duration))
    set_node_input(workflow, "Framerate", "value", framerate)

    # Set seed
    set_node_input(workflow, "RandomNoise", "noise_seed", seed)

    # Set audio
    if audio_path:
        set_node_input(workflow, "Load Audio", "audio", audio_path)

    # Embed frame images into LTXDirector segments JSON
    # LTXDirector reads images from the segments field (imageFile key),
    # NOT from connected LoadImage nodes. Image files must be pre-uploaded
    # to ComfyUI's input folder.
    segments_data: dict = {"segments": [], "audioSegments": []}
    if first_frame:
        # Extract just the filename if it's a full path
        ff_name = os.path.basename(first_frame) if "/" in first_frame or "\\" in first_frame else first_frame
        segments_data["segments"].append({
            "frame_index": 0,
            "strength": 1.0,
            "imageFile": ff_name,
        })
    if last_frame:
        lf_name = os.path.basename(last_frame) if "/" in last_frame or "\\" in last_frame else last_frame
        segments_data["segments"].append({
            "frame_index": -1,
            "strength": 1.0,
            "imageFile": lf_name,
        })
    # If no images at all, still set empty segments
    try:
        set_node_input(workflow, "LTXDirector", "segments", json.dumps(segments_data))
        logger.info(f"Set LTXDirector segments with {len(segments_data['segments'])} image(s)")
    except ValueError:
        logger.warning("No LTXDirector node found — segments not set")

    # Also set LoadImage nodes for compatibility (some workflow variants may use them)
    if first_frame:
        try:
            set_node_input(workflow, "LOAD IMAGE", "image", first_frame)
        except ValueError:
            try:
                set_node_input(workflow, "LOAD FIRST IMAGE FRAME", "image", first_frame)
            except ValueError:
                pass  # Images handled via segments JSON above
    if last_frame:
        try:
            set_node_input(workflow, "LOAD LAST IMAGE FRAME", "image", last_frame)
        except ValueError:
            pass  # Images handled via segments JSON above

    # Apply image processing fixes (same as existing LTX workflows)
    longer_edge = max(width, height)
    _update_resize_longer_edge(workflow, longer_edge)
    _fix_image_resize_stretch(workflow)

    logger.info("Sequencer workflow prepared")
    return workflow


def prepare_ltx_director_workflow(
    workflow_path: str,
    *,
    timeline_data: dict,
    global_prompt: str = "",
    local_prompts: str = "",
    segment_lengths: str = "",
    guide_strength: str = "",
    duration_frames: int = 120,
    duration_seconds: float = 5.0,
    frame_rate: int = 24,
    width: int = 0,
    height: int = 0,
    use_custom_audio: bool = True,
    use_custom_motion: bool = False,
    epsilon: float = 0.001,
    img_compression: int = 18,
    resize_method: str = "maintain aspect ratio",
    retake_mode: bool = False,
    seed: int = 0,
    audio_path: str = "",
    ltx_model_gguf: Optional[str] = None,
    distilled_lora_name: Optional[str] = None,
) -> dict:
    """Prepare the LTX **Director Mode** (v2.0.0 LTXDirector node) workflow.

    Unlike ``prepare_sequencer_workflow`` (which drives the *v1* LTXDirector via
    ``text``/``segments``), this targets the **v2.0.0** node whose timeline is
    driven by a single ``timeline_data`` JSON plus Prompt-Relay widgets
    (``global_prompt`` / ``local_prompts`` / ``segment_lengths``) and per-keyframe
    ``guide_strength``.

    ``timeline_data`` is the editor state already resolved for ComfyUI: image
    keyframes reference ``imageFile`` basenames and audio references ``audioFile``
    basenames (the dispatcher uploads those files to ComfyUI's input dir first).

    The workflow is grafted onto **our** existing LTX stack (GGUF unet + distilled
    LoRA + gemma DualCLIP + KJ VAE loaders + ``VHS_VideoCombine`` output), so it
    runs with the same models as the rest of the app's LTX pipeline. Every mutation
    is best-effort: a missing node/field warns and continues so a slightly
    different (re-exported) workflow still works.
    """
    logger.info(f"Preparing LTX Director workflow from {workflow_path}")

    with open(workflow_path, "r", encoding="utf-8") as f:
        workflow = json.load(f)

    def _try(title: str, field: str, value: Any) -> None:
        try:
            set_node_input(workflow, title, field, value)
        except ValueError:
            logger.warning(f"LTX Director: node '{title}' not found — skipping {field}")

    # --- Model stack overrides (same knobs as the rest of our LTX pipeline) ---
    if ltx_model_gguf:
        _try("Unet Loader (GGUF)", "unet_name", ltx_model_gguf)
    if distilled_lora_name:
        _try("Distilled LoRA", "lora_name", distilled_lora_name)
        _update_power_lora_distilled(workflow, distilled_lora_name)

    # --- The v2.0.0 LTXDirector node (timeline + Prompt Relay) ---
    # global_prompt is NOT a node widget — it lives inside timeline_data (confirmed
    # against a real API export). Fold the param in so either call style works.
    if global_prompt:
        timeline_data = {**timeline_data, "global_prompt": global_prompt}
    elif "global_prompt" not in timeline_data:
        timeline_data["global_prompt"] = ""
    _try("LTXDirector", "timeline_data", json.dumps(timeline_data))
    _try("LTXDirector", "local_prompts", local_prompts or "")
    _try("LTXDirector", "segment_lengths", segment_lengths or "")
    _try("LTXDirector", "guide_strength", guide_strength or "")
    _try("LTXDirector", "use_custom_audio", bool(use_custom_audio))
    _try("LTXDirector", "use_custom_motion", bool(use_custom_motion))
    _try("LTXDirector", "epsilon", float(epsilon))
    _try("LTXDirector", "img_compression", int(img_compression))
    _try("LTXDirector", "resize_method", resize_method)

    # Duration / framerate — ceil seconds→frames consistency (see prepare_ltx_workflow).
    _dur_frames = int(duration_frames) if duration_frames else int(math.ceil(duration_seconds * frame_rate))
    _dur_seconds = float(duration_seconds) if duration_seconds else (_dur_frames / float(frame_rate or 24))
    _try("LTXDirector", "frame_rate", int(frame_rate))
    _try("LTXDirector", "display_mode", "seconds")
    _try("LTXDirector", "duration_frames", _dur_frames)
    _try("LTXDirector", "duration_seconds", _dur_seconds)
    _try("LTXDirector", "end_frame", _dur_frames)
    _try("LTXDirector", "end_second", _dur_seconds)
    _try("LTXDirector", "start_frame", 0)
    _try("LTXDirector", "start_second", 0)

    # Dimensions: the v2 node derives size from keyframes when custom_* == 0.
    # Only pin them when the caller passes an explicit scene resolution.
    if width and height:
        _try("LTXDirector", "custom_width", int(width))
        _try("LTXDirector", "custom_height", int(height))

    # --- Retake / in-place editing: flip the (stage-1) guide node into retake mode ---
    if retake_mode:
        _try("LTXDirectorGuide", "retake_mode", True)

    # --- Our output framerate constant (feeds VHS_VideoCombine via IntToFloat) ---
    _try("Framerate", "value", int(frame_rate))

    # --- Seed + audio ---
    _try("RandomNoise", "noise_seed", int(seed))
    if audio_path:
        _try("Load Audio", "audio", audio_path)

    logger.info(
        f"LTX Director workflow prepared: "
        f"{len(timeline_data.get('segments', []))} segment(s), "
        f"{len(timeline_data.get('audioSegments', []))} audio clip(s), "
        f"{len(timeline_data.get('motionSegments', []))} motion clip(s), "
        f"custom_audio={use_custom_audio}, frames={_dur_frames}@{frame_rate}fps"
    )
    return workflow


def prepare_klein_inpaint_workflow(
    workflow_path: str,
    *,
    source_masked_path: str,
    reference_path: str,
    prompt: str,
    seed: int = 0,
    klein_model_gguf: Optional[str] = None,
    mask_expand: Optional[int] = None,
    mask_blur: Optional[int] = None,
) -> dict:
    """Prepare the Klein **inpaint** workflow (KLEIN_INPAINT.json).

    The source image carries the paint mask in its ALPHA channel (ComfyUI
    clipspace convention: painted/inpaint region = transparent → LoadImage MASK
    = 1 there).  An optional reference image (node 'INPAINT REFERENCE') is
    VAE-encoded and chained via ReferenceLatent so Klein can pull a specific
    object / character into the masked area.  When the caller has no reference,
    pass the original (un-masked) source as ``reference_path`` so the reference
    latent simply reinforces the existing image.

    Mutates by node title:
    - "INPAINT SOURCE"  -> image (RGBA source + mask-in-alpha)
    - "INPAINT REFERENCE" -> image (reference, or the source when none)
    - "CLIP Text Encode (Positive Prompt)" -> text
    - "RandomNoise" -> noise_seed
    - "GrowMaskWithBlur" -> expand / blur_radius (optional)
    - "Unet Loader (GGUF)" -> unet_name (optional Klein model override)

    Image files are uploaded to ComfyUI by the dispatcher's file-upload pass
    (both nodes are LoadImage), so we set local paths/basenames here.
    """
    logger.info(f"Preparing Klein inpaint workflow from {workflow_path}")

    with open(workflow_path, "r", encoding="utf-8") as f:
        workflow = json.load(f)

    def _try(title: str, field: str, value: Any) -> None:
        try:
            set_node_input(workflow, title, field, value)
        except ValueError:
            logger.warning(f"Klein inpaint: node '{title}' not found — skipping {field}")

    _try("INPAINT SOURCE", "image", source_masked_path)
    _try("INPAINT REFERENCE", "image", reference_path or source_masked_path)
    _try("CLIP Text Encode (Positive Prompt)", "text", prompt or "")
    _try("RandomNoise", "noise_seed", int(seed))

    if klein_model_gguf:
        _try("Unet Loader (GGUF)", "unet_name", klein_model_gguf)

    if mask_expand is not None:
        _try("GrowMaskWithBlur", "expand", int(mask_expand))
    if mask_blur is not None:
        _try("GrowMaskWithBlur", "blur_radius", int(mask_blur))

    logger.info(
        f"Klein inpaint prepared: source={os.path.basename(source_masked_path)}, "
        f"reference={os.path.basename(reference_path or source_masked_path)}, "
        f"prompt={'yes' if prompt else 'no'}, seed={seed}"
    )
    return workflow
