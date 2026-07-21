"""VNCCS Qwen-Image-Edit-2511 clothes pipeline, replicated APP-SIDE (v1.167).

Rebuilds the graphs the VNCCS suite assembles internally for its clothing
stage, so app-catalog (Klein Hybrid) characters can be dressed through
VNCCS's EXACT process without existing in the worker-side VNCCS character
store.  Workers only need the VNCCS custom nodes + models (all shipped with a
standard VNCCS 3.x install):

* Pass A -- ``ClothesDesigner``: dress the character ONCE from the costume
  slots (or an outfit photo, "clone" mode) using Qwen-Image-Edit-2511 + the
  VNCCS ClothesCore LoRA.
* Pass B -- ``VNCCS_ClothesGenerator``: reproduce the dressed look on every
  pose using the app-side 3D-mannequin pose renders (image 1) + the dressed
  preview (image 2) with the VNCCS QIE2511 PoseStudio LoRA.

Source of truth: AHEKOT/ComfyUI_VNCCS 3.0.2 --
``nodes/clothes_designer.py`` (Pass A graph + prompt template),
``nodes/character_generator.py`` (Pass B loop + settings),
``nodes/vnccs_qwen_encoder.py`` (VNCCS_QWEN_Encoder semantics: squared
per-image weights, reference_latents_method='index_timestep_zero',
latent = image[latent_image_index]'s VAE latent, negative = zeroed positive).

Divergences (deliberate, documented in CHANGELOG 1.167.0):
* background removal uses our proven RMBG-2.0 injection (klein_poses
  ``_inject_rmbg``) instead of VNCCSChromaKey+SAM3 edge recovery -- same
  RGBA-sprite output our ingest expects; VNCCS itself ships RMBG as an option.
* no in-graph SeedVR upscale -- the gallery's on-demand SeedVR2 upscaling
  covers it without doubling VRAM in the pose graph.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from backend.services.character_studio.vnccs_native.klein_poses import (
    _inject_rmbg,
    _options,
    _resolve_name,
)

logger = logging.getLogger(__name__)

# The encoder 'instruction' (system prompt for the Qwen-VL text encoder) that
# VNCCS ClothesDesigner uses verbatim (WORKFLOW_ENCODER_DEFAULTS).
QWEN_INSTRUCTION = (
    "Describe the character and their key features (body shape, physical "
    "characteristics, clothing, items, accessories). Then explain how the "
    "user's text instruction should alter or modify the character. Generate "
    "a new image that meets the user's requirements while maintaining "
    "consistency with the original character where appropriate."
)

# Pass A clone mode -- VNCCS's exact prompt when dressing from a reference photo.
QWEN_CLONE_PROMPT = "Dress character: clothes, footwear and accessories from Picture 2"

QWEN_ENCODER_CLASS = "VNCCS_QWEN_Encoder"


def qwen_dress_prompt(costume_info: dict, background: str) -> str:
    """VNCCS ClothesDesigner ``construct_prompt`` verbatim: 'Dress the
    character:' + non-empty slots in top/bottom/head/shoes/face order + the
    solid-background directive (Green -> 00FF00, Blue -> 0000FF)."""
    ci = costume_info or {}
    lines = ["Dress the character:"]
    for key in ("top", "bottom", "head", "shoes", "face"):
        val = str(ci.get(key) or "").strip()
        if val:
            lines.append(val)
    bg = str(background or "Green").strip().lower()
    if bg.startswith("blue"):
        lines.append("solid blue (0000FF) background")
    else:
        lines.append("solid green (00FF00) background")
    return "\n".join(lines)


def qwen_pose_prompt(background: str) -> str:
    """VNCCS Pass-B per-pose prompt: PoseStudio's 'Draw character from image2'
    template + ``_prompt_with_solid_background``'s appended directive."""
    bg = str(background or "Green").strip() or "Green"
    return f"Draw character from image2, Change background to solid {bg} color"


def pad_headroom(data: bytes, top: float = 0.15, bottom: float = 0.03) -> bytes:
    """Add empty space above (and a little below) the figure before a Qwen dress /
    pose edit, so HEADWEAR (hats, hoods, animal ears, tall hair) has canvas to
    render into.  Our Klein base renders and pose-studio captures frame the head at
    the very TOP edge with ~no headroom; Qwen-Image-Edit can only paint INSIDE
    image1's canvas, so a hat has nowhere to go and gets clipped or omitted.  VNCCS
    dresses a tall 640x1536 base that already carries this headroom -- this pad gives
    our images the same room.  Pads with the image's own corner/background colour so
    it still keys out cleanly.  Best-effort: returns the original bytes on failure."""
    try:
        from io import BytesIO
        from PIL import Image
        im = Image.open(BytesIO(data)).convert("RGB")
        w, h = im.size
        pt = max(0, int(round(h * max(0.0, top))))
        pb = max(0, int(round(h * max(0.0, bottom))))
        if pt == 0 and pb == 0:
            return data
        bg = im.getpixel((1, 1)) if (w > 2 and h > 2) else (0, 255, 0)
        canvas = Image.new("RGB", (w, h + pt + pb), bg)
        canvas.paste(im, (0, pt))
        buf = BytesIO()
        canvas.save(buf, format="PNG")
        return buf.getvalue()
    except Exception:  # noqa: BLE001 -- never break a render over framing
        return data


def pad_base_to_headroom(data: bytes, target: float) -> bytes:
    """v1.199.13: pad blank space ABOVE the figure so its head sits at ``target``
    fraction from the top, giving tall headwear canvas to render into at the Qwen
    dress (Pass A) step -- where the hat is first drawn.  Measures the figure's
    current top margin (vs the corner background) and ONLY adds space when the base
    has less than target; never crops, never distorts, idempotent when the base
    already has room.  Pads with the base's own corner colour so it still keys out.
    Best-effort: returns the input bytes on any failure."""
    try:
        from io import BytesIO
        from PIL import Image
        import numpy as np
        target = max(0.0, min(0.45, float(target)))
        if target <= 0.001:
            return data
        im = Image.open(BytesIO(data)).convert("RGB")
        w, h = im.size
        if w < 2 or h < 2:
            return data
        arr = np.asarray(im).astype("float32")
        bg = arr[1, 1]
        dist = np.sqrt(((arr - bg) ** 2).sum(-1))
        rows = np.where((dist > 40.0).any(axis=1))[0]
        if len(rows) == 0:
            return data
        top_px = int(rows[0])
        if (top_px / h) >= target:
            return data
        p = int(round((target * h - top_px) / (1.0 - target)))
        if p <= 1:
            return data
        bgt = tuple(int(x) for x in im.getpixel((1, 1)))
        canvas = Image.new("RGB", (w, h + p), bgt)
        canvas.paste(im, (0, p))
        buf = BytesIO()
        canvas.save(buf, format="PNG")
        return buf.getvalue()
    except Exception:  # noqa: BLE001 -- never break a render over framing
        return data

def resolve_qwen_models(oi: dict, settings: Optional[dict] = None) -> Dict[str, Any]:
    """Resolve the Qwen-Image-Edit-2511 model set on a worker from its
    /object_info.  Raises ValueError with a human-readable list of anything
    missing.  Settings overrides: qwen_unet / qwen_clip / qwen_vae /
    qwen_lightning_lora / qwen_clothes_lora / qwen_pose_lora."""
    st = settings or {}
    missing: List[str] = []

    if QWEN_ENCODER_CLASS not in (oi or {}):
        missing.append("VNCCS_QWEN_Encoder node (install/update ComfyUI_VNCCS 3.x)")

    def _pick(options: List[str], needles: List[str], override: str) -> Optional[str]:
        if override:
            hit = _resolve_name(options, override)
            if hit:
                return hit
        for o in options:
            lo = o.lower().replace("\\", "/")
            if any(n in lo for n in needles):
                return o
        return None

    # UNet: GGUF quant first (VNCCS default), plain UNETLoader as fallback.
    unet = None
    unet_loader = "gguf"
    gguf_opts = _options(oi, "UnetLoaderGGUF", "unet_name")
    unet = _pick(gguf_opts, ["qwen-image-edit-2511", "qwen_image_edit_2511"],
                 str(st.get("qwen_unet") or "").strip())
    if not unet:
        unet_loader = "unet"
        unet = _pick(_options(oi, "UNETLoader", "unet_name"),
                     ["qwen-image-edit-2511", "qwen_image_edit_2511"],
                     str(st.get("qwen_unet") or "").strip())
    if not unet:
        missing.append("Qwen-Image-Edit-2511 unet (e.g. qwen-image-edit-2511-Q5_0.gguf)")

    clip = _pick(_options(oi, "CLIPLoader", "clip_name"),
                 ["qwen_2.5_vl_7b", "qwen2.5_vl", "qwen_2.5_vl"],
                 str(st.get("qwen_clip") or "").strip())
    if not clip:
        missing.append("Qwen2.5-VL text encoder (qwen_2.5_vl_7b_fp8_scaled.safetensors)")

    vae = _pick(_options(oi, "VAELoader", "vae_name"), ["qwen_image_vae"],
                str(st.get("qwen_vae") or "").strip())
    if not vae:
        missing.append("Qwen image VAE (qwen_image_vae.safetensors)")

    lora_opts = _options(oi, "LoraLoaderModelOnly", "lora_name")
    lightning = _pick(lora_opts, ["2511-lightning", "lightning-4steps"],
                      str(st.get("qwen_lightning_lora") or "").strip())
    clothes_lora = _pick(lora_opts, ["clothescore"],
                         str(st.get("qwen_clothes_lora") or "").strip())
    pose_lora = _pick(lora_opts, ["qie2511_posestudio", "qie2511-posestudio"],
                      str(st.get("qwen_pose_lora") or "").strip())
    if not lightning:
        # not fatal: without it 4-step/CFG-1 quality collapses, so warn loudly
        logger.warning("qwen clothes: Lightning 4-step LoRA not found on this "
                       "worker -- results at steps=4/cfg=1 will be poor")
    if not clothes_lora:
        missing.append("VNCCS ClothesCore LoRA (VNCCS_QIE2511_ClothesCore-*.safetensors)")
    if not pose_lora:
        missing.append("VNCCS QIE2511 PoseStudio LoRA (VNCCS_QIE2511_PoseStudio_ART_*.safetensors)")

    if missing:
        raise ValueError("Qwen (VNCCS) clothes pipeline -- missing on this worker: "
                         + "; ".join(missing))
    has_tiled = "VAEDecodeTiled" in (oi or {})
    # v1.199.15: OPTIONAL emotion pieces (never fatal -- only the Qwen Emotions engine
    # uses them). EmotionCore LoRA is off by default (VNCCS's ChangeEmotion example runs
    # it disabled and drives the change via the prompt); the face detector is for
    # VNCCS_QWEN_Detailer's bbox.
    emotion_lora = _pick(lora_opts, ["emotioncore", "qie2511_emotion"],
                         str(st.get("qwen_emotion_lora") or "").strip())
    _det_opts = _options(oi, "UltralyticsDetectorProvider", "model_name")
    face_detector = (str(st.get("qwen_face_detector") or "").strip() or
                     next((o for o in _det_opts if "face_yolov8" in o.lower()), None) or
                     next((o for o in _det_opts if o.lower().startswith("bbox/")), None) or
                     (_det_opts[0] if _det_opts else "bbox/face_yolov8m.pt"))
    return {"unet": unet, "unet_loader": unet_loader, "clip": clip, "vae": vae,
            "lightning": lightning, "clothes_lora": clothes_lora,
            "pose_lora": pose_lora, "tiled_decode": has_tiled,
            "emotion_lora": emotion_lora, "face_detector": face_detector}


def _qwen_loaders(api: Dict[str, dict], models: Dict[str, Any]) -> Tuple[list, list, list]:
    """Loader nodes + the always-on Lightning turbo LoRA (model+clip, strength
    1.0 -- VNCCS auto-forces it on for 4-step/CFG-1 Qwen).  Returns
    (model_ref, clip_ref, vae_ref)."""
    if models.get("unet_loader") == "gguf":
        api["u"] = {"class_type": "UnetLoaderGGUF",
                    "inputs": {"unet_name": models["unet"]}}
    else:
        api["u"] = {"class_type": "UNETLoader",
                    "inputs": {"unet_name": models["unet"], "weight_dtype": "default"}}
    api["c"] = {"class_type": "CLIPLoader",
                "inputs": {"clip_name": models["clip"], "type": "qwen_image",
                           "device": "default"}}
    api["v"] = {"class_type": "VAELoader", "inputs": {"vae_name": models["vae"]}}
    model_ref: list = ["u", 0]
    clip_ref: list = ["c", 0]
    if models.get("lightning"):
        api["turbo"] = {"class_type": "LoraLoader",
                        "inputs": {"model": model_ref, "clip": clip_ref,
                                   "lora_name": models["lightning"],
                                   "strength_model": 1.0, "strength_clip": 1.0}}
        model_ref = ["turbo", 0]
        clip_ref = ["turbo", 1]
    return model_ref, clip_ref, ["v", 0]


def _clamp_ref_weight(w) -> float:
    """Reference strength clamp (node allows 0.0-2.0; keep a sane 0.5-1.8 band)."""
    try:
        return max(0.5, min(1.8, float(w)))
    except Exception:  # noqa: BLE001
        return 1.0


def _encoder_inputs(clip_ref: list, vae_ref: list, prompt: str, *,
                    image1: list, image2: Optional[list] = None,
                    names: Tuple[str, str, str] = ("Picture 1", "Picture 2", "Picture 3"),
                    target_size: int = 1024, background_color: str = "White",
                    weight1: float = 1.0, weight2: float = 1.0, weight3: float = 1.0,
                    instruction: str = QWEN_INSTRUCTION) -> Dict[str, Any]:
    """VNCCS_QWEN_Encoder inputs exactly as ClothesDesigner/ClothesGenerator
    wire them (default weights 1/1/1, latent_image_index 1, vl_size 384, lanczos,
    crop disabled, qwen_2511 True). Per-image weights are the encoder's reference
    strength (quadratically mapped node-side: influence = weight**2), so >1.0 makes
    the reference (body/identity) hold harder."""
    inp: Dict[str, Any] = {
        "clip": clip_ref, "vae": vae_ref, "prompt": prompt,
        "image1": image1,
        "image1_name": names[0], "image2_name": names[1], "image3_name": names[2],
        "target_size": int(target_size), "upscale_method": "lanczos",
        "crop_method": "disabled", "latent_image_index": 1,
        "weight1": float(weight1), "weight2": float(weight2), "weight3": float(weight3),
        "vl_size": 384, "instruction": instruction,
        "qwen_2511": True, "background_color": background_color,
    }
    if image2 is not None:
        inp["image2"] = image2
    return inp


def _decode(api: Dict[str, dict], nid: str, samples: list, vae_ref: list,
            tiled: bool) -> str:
    if tiled:
        api[nid] = {"class_type": "VAEDecodeTiled",
                    "inputs": {"vae": vae_ref, "samples": samples,
                               "tile_size": 512, "overlap": 64,
                               "temporal_size": 64, "temporal_overlap": 8}}
    else:
        api[nid] = {"class_type": "VAEDecode",
                    "inputs": {"vae": vae_ref, "samples": samples}}
    return nid


def build_qwen_dress_graph(
    *,
    base_file: str,
    prompt: str,
    seed: int,
    models: Dict[str, Any],
    garment_file: Optional[str] = None,
    clothes_lora_strength: float = 1.0,
    target_size: int = 1024,
    steps: int = 4,
    cfg: float = 1.0,
    filename_prefix: str = "rbmn_vnccs/qwen_clothes",
) -> Tuple[Dict[str, dict], Dict[str, str]]:
    """Pass A -- VNCCS ClothesDesigner graph: dress ``base_file`` once.
    ``garment_file`` (clone mode) rides as Picture 2.  Sampler = the suite's
    exact settings: euler/simple, denoise 1.0, latent = the base image's own
    VAE latent (latent_image_index 1)."""
    if not base_file:
        raise ValueError("qwen dress graph needs a base image")
    api: Dict[str, dict] = {}
    model_ref, clip_ref, vae_ref = _qwen_loaders(api, models)
    api["cl"] = {"class_type": "LoraLoaderModelOnly",
                 "inputs": {"model": model_ref,
                            "lora_name": models["clothes_lora"],
                            "strength_model": float(max(0.1, min(1.5, clothes_lora_strength)))}}
    model_ref = ["cl", 0]
    api["base_load"] = {"class_type": "LoadImage", "inputs": {"image": base_file}}
    image2 = None
    if garment_file:
        api["garment_load"] = {"class_type": "LoadImage", "inputs": {"image": garment_file}}
        image2 = ["garment_load", 0]
    api["enc"] = {"class_type": QWEN_ENCODER_CLASS,
                  "inputs": _encoder_inputs(clip_ref, vae_ref, prompt,
                                            image1=["base_load", 0], image2=image2,
                                            target_size=target_size,
                                            background_color="White")}
    api["ks"] = {"class_type": "KSampler",
                 "inputs": {"model": model_ref, "seed": int(seed),
                            "steps": int(steps), "cfg": float(cfg),
                            "sampler_name": "euler", "scheduler": "simple",
                            "positive": ["enc", 0], "negative": ["enc", 1],
                            "latent_image": ["enc", 2], "denoise": 1.0}}
    dec = _decode(api, "dec", ["ks", 0], vae_ref, bool(models.get("tiled_decode")))
    api["save"] = {"class_type": "SaveImage",
                   "inputs": {"images": [dec, 0], "filename_prefix": filename_prefix}}
    return api, {"clothes": "save"}


def build_qwen_pose_set_graph(
    *,
    pose_files: List[str],
    dressed_file: str,
    seed: int,
    models: Dict[str, Any],
    background: str = "Green",
    pose_lora_strength: float = 1.0,
    target_size: int = 1024,
    steps: int = 4,
    cfg: float = 1.0,
    ref_weight: float = 1.0,
    rmbg: Optional[Dict[str, Any]] = None,
    filename_prefix: str = "rbmn_vnccs/qwen_sprites",
) -> Tuple[Dict[str, dict], Dict[str, str]]:
    """Pass B -- VNCCS ClothesGenerator loop as ONE graph: for every pose
    mannequin render (image 1) reproduce the DRESSED character (image 2,
    alpha-filled green app-side) via the QIE2511 PoseStudio LoRA.  Same seed
    for every pose (the suite's policy -- costume identity comes from image 2,
    not the seed).  Decoded poses are batched into one SaveImage so the
    standard ingest maps them by pose order."""
    if not pose_files:
        raise ValueError("qwen pose set graph needs pose renders")
    if not dressed_file:
        raise ValueError("qwen pose set graph needs the dressed costume image")
    api: Dict[str, dict] = {}
    model_ref, clip_ref, vae_ref = _qwen_loaders(api, models)
    api["pl"] = {"class_type": "LoraLoaderModelOnly",
                 "inputs": {"model": model_ref,
                            "lora_name": models["pose_lora"],
                            "strength_model": float(max(0.1, min(1.5, pose_lora_strength)))}}
    model_ref = ["pl", 0]
    api["dressed_load"] = {"class_type": "LoadImage", "inputs": {"image": dressed_file}}
    prompt = qwen_pose_prompt(background)
    tiled = bool(models.get("tiled_decode"))
    decoded: List[str] = []
    for i, pf in enumerate(pose_files):
        t = f"p{i}"
        api[f"{t}_load"] = {"class_type": "LoadImage", "inputs": {"image": pf}}
        api[f"{t}_enc"] = {"class_type": QWEN_ENCODER_CLASS,
                           "inputs": _encoder_inputs(
                               clip_ref, vae_ref, prompt,
                               image1=[f"{t}_load", 0], image2=["dressed_load", 0],
                               names=("image 1", "image 2", "image 3"),
                               target_size=target_size,
                               # image2 is the DRESSED character (holds the body) --
                               # boost its weight so the pose keeps the real build.
                               weight2=_clamp_ref_weight(ref_weight),
                               background_color=str(background or "Green"))}
        api[f"{t}_ks"] = {"class_type": "KSampler",
                          "inputs": {"model": model_ref, "seed": int(seed),
                                     "steps": int(steps), "cfg": float(cfg),
                                     "sampler_name": "euler", "scheduler": "simple",
                                     "positive": [f"{t}_enc", 0],
                                     "negative": [f"{t}_enc", 1],
                                     "latent_image": [f"{t}_enc", 2], "denoise": 1.0}}
        decoded.append(_decode(api, f"{t}_dec", [f"{t}_ks", 0], vae_ref, tiled))
    # batch the decoded poses into one stream (pose order preserved)
    cur = decoded[0]
    for i, d in enumerate(decoded[1:]):
        bid = f"batch{i}"
        api[bid] = {"class_type": "ImageBatch",
                    "inputs": {"image1": [cur, 0], "image2": [d, 0]}}
        cur = bid
    cur = _inject_rmbg(api, cur, rmbg)
    api["save"] = {"class_type": "SaveImage",
                   "inputs": {"images": [cur, 0], "filename_prefix": filename_prefix}}
    return api, {"sprites": "save"}


# --------------------------------------------------------------------------- #
# v1.199.15 -- Qwen EMOTIONS engine (VNCCS Step 3 replica).                    #
# Mirrors vnccs-utils "QwenDetailer_ChangeEmotion" workflow: VNCCS_QWEN_Detailer
# (Ultralytics face bbox -> QIE face edit -> stitch) with a "Change emotion to X"
# prompt, on our standard Qwen loaders. EmotionCore LoRA optional (off by default).
QWEN_EMOTION_INSTRUCTION = (
    "Describe the key features of the input image (color, shape, size, texture, "
    "objects, background), then explain how the user's text instruction should alter "
    "or modify the image. Generate a new image that meets the user's requirements "
    "while maintaining consistency with the original input where appropriate.")


def qwen_emotion_prompt(natural: str = "", key: str = "") -> str:
    """VNCCS's ChangeEmotion prompt form ("Change emotion to <x>")."""
    e = (str(natural or "").strip() or str(key or "").strip() or "neutral")
    return f"Change emotion to {e}"


def build_qwen_emotion_graph(
    *,
    sprite_files: List[str],
    emotions: List[Dict[str, str]],
    seed: int,
    models: Dict[str, Any],
    use_emotion_lora: bool = False,
    emotion_lora_strength: float = 1.0,
    steps: int = 4,
    cfg: float = 1.0,
    denoise: float = 1.0,
    target_size: int = 1024,
    face_threshold: float = 0.5,
    filename_prefix: str = "rbmn_vnccs/qwen_emotions",
) -> Tuple[Dict[str, dict], Dict[str, str]]:
    """For every (sprite x emotion) run VNCCS_QWEN_Detailer with a
    "Change emotion to X" prompt -- VNCCS's own Qwen emotion method (face detect
    -> QIE edit -> stitch), leaving body/clothes/background untouched.
    ``emotions`` = [{"key","natural"}...]. Decoded results batch into one SaveImage
    in (sprite, emotion) order so the standard ingest maps them back."""
    if not sprite_files:
        raise ValueError("qwen emotion graph needs sprite(s)")
    if not emotions:
        raise ValueError("qwen emotion graph needs emotion(s)")
    api: Dict[str, dict] = {}
    model_ref, clip_ref, vae_ref = _qwen_loaders(api, models)
    if use_emotion_lora and models.get("emotion_lora"):
        api["el"] = {"class_type": "LoraLoaderModelOnly",
                     "inputs": {"model": model_ref, "lora_name": models["emotion_lora"],
                                "strength_model": float(max(0.1, min(1.5, emotion_lora_strength)))}}
        model_ref = ["el", 0]
    det = models.get("face_detector") or "bbox/face_yolov8m.pt"
    api["det"] = {"class_type": "UltralyticsDetectorProvider",
                  "inputs": {"model_name": det}}
    decoded: List[str] = []
    for si, sp in enumerate(sprite_files):
        api[f"s{si}_load"] = {"class_type": "LoadImage", "inputs": {"image": sp}}
        for ei, emo in enumerate(emotions):
            t = f"s{si}e{ei}"
            api[f"{t}_det"] = {"class_type": "VNCCS_QWEN_Detailer",
                               "inputs": {
                                   "image": [f"s{si}_load", 0], "bbox_detector": ["det", 0],
                                   "model": model_ref, "clip": clip_ref, "vae": vae_ref,
                                   "prompt": qwen_emotion_prompt(emo.get("natural"), emo.get("key")),
                                   "threshold": float(face_threshold), "dilation": 0,
                                   "drop_size": 10, "feather": 0, "steps": int(steps),
                                   "cfg": float(cfg), "seed": int(seed), "sampler_name": "euler",
                                   "scheduler": "simple", "denoise": float(denoise),
                                   "tiled_vae_decode": False, "tile_size": 512,
                                   "sam_detection_hint": "center-1", "sam_dilation": 0,
                                   "sam_threshold": 0.93, "sam_bbox_expansion": 0,
                                   "sam_mask_hint_threshold": 0.7,
                                   "sam_mask_hint_use_negative": "False",
                                   "target_size": int(target_size), "upscale_method": "nearest-exact",
                                   "crop_method": "disabled", "instruction": QWEN_EMOTION_INSTRUCTION,
                                   "inpaint_mode": False,
                                   "inpaint_prompt": "[!!!IMPORTANT!!!] Inpaint mode: draw only inside black box.",
                                   "color_match_method": "kornia_reinhard", "seam_fix": True,
                                   "qwen_2511": True, "distortion_fix": True}}
            decoded.append(f"{t}_det")
    cur = decoded[0]
    for i, d in enumerate(decoded[1:]):
        bid = f"emb{i}"
        api[bid] = {"class_type": "ImageBatch",
                    "inputs": {"image1": [cur, 0], "image2": [d, 0]}}
        cur = bid
    api["save"] = {"class_type": "SaveImage",
                   "inputs": {"images": [cur, 0], "filename_prefix": filename_prefix}}
    return api, {"sprites": "save"}


# =========================================================================== #
# v1.168 -- CHARACTER CREATION stage (VNCCS Step 1), replicated app-side.
# Source: character_creator_v2.py, character_cloner.py, character_generator.py
# (AHEKOT/ComfyUI_VNCCS 3.0.2).  New characters: a t2i base render (Illustrious
# SDXL or Anima) at 640x1536 -- there is NO multi-view sheet in 3.0.x; pose
# variety comes from the Qwen Pass-B pose pass.  Clones: reference photos are
# packed into ONE collage grid which rides as image 2 of every Pass-B render
# ("Original" look); the "Naked" base runs a remove-clothes Qwen edit on the
# collage first (ClothesCore LoRA, "Undress character").
# =========================================================================== #

# piecewise-linear age-LoRA strength curve (utils.py AGE_CONTROL_POINTS)
_AGE_POINTS = [(0, -5.0), (3, -4.0), (5, -3.0), (7, -2.0), (9, -1.0), (11, 0.0),
               (14, 1.0), (16, 1.5), (18, 2.0), (30, 2.5), (40, 3.0), (50, 3.5),
               (60, 3.5), (70, 4.0), (80, 5.0)]

CREATOR_WIDTH = 640
CREATOR_HEIGHT = 1536


def age_lora_strength(age: int) -> float:
    a = max(0, min(80, int(age or 18)))
    for (x0, y0), (x1, y1) in zip(_AGE_POINTS, _AGE_POINTS[1:]):
        if a <= x1:
            if x1 == x0:
                return y1
            t = (a - x0) / (x1 - x0)
            return y0 + t * (y1 - y0)
    return _AGE_POINTS[-1][1]


def _age_body_descriptor(age: int, male: bool) -> str:
    """VNCCS age bucket tags (utils.py age_body_descriptor)."""
    a = int(age or 18)
    kid = "boy" if male else "girl"
    if a <= 3:
        return f"(toddler {kid}:1.0)"
    if a <= 11:
        return "(shota:1.0)" if male else "(loli:1.0)"
    if (male and a <= 16) or (not male and a <= 18):
        return f"(teenager {kid}:1.0)"
    if a <= 24:
        return "(young adult man:1.5)" if male else "(young adult woman:1.0)"
    if a <= 50:
        return "(adult man:1.0)" if male else "(adult woman:1.0)"
    return "(old man:1.0)" if male else "(old woman:1.0)"


def creator_prompt(info: dict, *, anima: bool = False) -> str:
    """VNCCS CharacterCreatorV2.construct_prompt, field for field."""
    ci = info or {}
    male = str(ci.get("sex") or "female").strip().lower().startswith("m")
    nsfw = bool(ci.get("nsfw"))
    aesthetics = str(ci.get("aesthetics") or "").strip() or (
        "masterpiece, best quality, score_7, anime" if anima else "masterpiece")
    parts = [aesthetics, "simple background", "expressionless", "solo", "full_body"]
    parts += ["(1boy)", "(male_focus)"] if male else ["(1girl)"]
    if nsfw:
        parts.append("(naked, nude, penis)" if male else "(naked, nude, vagina, nipples)")
    else:
        parts.append("(bare chest, wear white boxers)" if male else "(wear white bra and panties)")
    age = int(ci.get("age") or 18)
    parts.append(f"{age}yo")
    parts.append(_age_body_descriptor(age, male))
    bg = str(ci.get("background_color") or "Green").strip() or "Green"
    parts.append(f"{bg} background")
    hair = str(ci.get("hair") or "").strip()
    if hair and "hair" not in hair.lower():
        hair = f"{hair} hair"
    for val in (str(ci.get("race") or "").strip(), hair,
                str(ci.get("eyes") or "").strip(), str(ci.get("face") or "").strip(),
                str(ci.get("body") or "").strip(), str(ci.get("skin_color") or "").strip(),
                str(ci.get("additional_details") or "").strip()):
        if val:
            parts.append(f"({val}:1.0)")
    lp = str(ci.get("lora_prompt") or "").strip()
    if lp:
        parts.append(lp)
    return ", ".join(parts)


def creator_prompt_natural(info: dict, flavor: str = "klein") -> str:
    """v1.169/1.170: the SAME creator semantics as VNCCS's tag template,
    adapted PER MODEL per docs/MODEL_PROMPTING.md.  Field mapping is 1:1 with
    construct_prompt -- solo, expressionless, full-body head-to-toe framing
    with headroom above the head and below the feet, white
    underwear (SFW) / nude (NSFW), solid keyable background.  Flavors:
    * klein  -- concise prose (~30-90w), lighting emphasized, no boosters.
    * qwen   -- descriptive natural sentences (Qwen-Image reads prose well).
    * zimage -- STRUCTURED camera-direction blocks (Subject/Framing/Lighting/
                Background) -- Z-Image is a literal instruction-follower and
                blows out with booster spam; negatives baked positively.
    * krea2  -- FEWEST modifiers (~30-110w): Krea was post-trained to remove
                the "AI look"; extra adjectives push it back toward it."""
    ci = info or {}
    male = str(ci.get("sex") or "female").strip().lower().startswith("m")
    nsfw = bool(ci.get("nsfw"))
    age = int(ci.get("age") or 18)
    race = str(ci.get("race") or "").strip() or "human"
    noun = "man" if male else "woman"
    if age < 20:
        noun = "young " + noun
    wear = ("completely nude" if nsfw else
            ("wearing only plain white boxer shorts, bare chest" if male
             else "wearing only a plain white bra and panties"))
    bits = []
    hair = str(ci.get("hair") or "").strip()
    if hair:
        bits.append(hair if "hair" in hair.lower() else f"{hair} hair")
    for key, suffix in (("eyes", ""), ("face", ""), ("body", ""),
                        ("skin_color", " skin"), ("additional_details", "")):
        v = str(ci.get(key) or "").strip()
        if v:
            bits.append(v + (suffix if suffix and suffix.strip() not in v.lower() else ""))
    feats = ", ".join(bits)
    bg = str(ci.get("background_color") or "Green").strip() or "Green"
    pron = "He" if male else "She"
    has = "He has" if male else "She has"

    if flavor == "zimage":
        # structured, literal art-direction blocks
        lines = [
            f"Full-body studio reference photograph of one {age}-year-old {race} {noun}.",
            f"Subject: standing upright in a neutral relaxed pose, arms at the sides, "
            f"facing the camera directly, calm expressionless face, {wear}.",
        ]
        if feats:
            lines.append(f"Appearance: {feats}.")
        lines.append("Framing: full-length shot showing the whole figure head to "
                     "toe, a small margin of empty space above the head and below the "
                     "feet, subject centered, camera at waist height, straight-on angle.")
        lines.append(f"Lighting: soft even studio light from the front, uniform "
                     f"exposure, sharp focus on the whole figure.")
        lines.append(f"Background: solid flat {bg.lower()}, completely uniform, "
                     f"no shadows, no texture, no objects.")
        return " ".join(lines)
    if flavor == "krea2":
        # minimal modifiers -- let Krea's aesthetic do the work
        parts = [f"Studio photo of a {age}-year-old {race} {noun} standing in a "
                 f"neutral pose facing the camera, expressionless, {wear}, the whole "
                 f"figure visible head to toe with a small margin of empty space above "
                 f"the head and below the feet."]
        if feats:
            parts.append(f"{has} {feats}.")
        parts.append(f"Plain {bg.lower()} backdrop, soft even light.")
        return " ".join(parts)
    # klein / qwen -- concise descriptive prose
    parts = [
        f"A full-body studio reference photo of a single {age}-year-old {race} {noun}, "
        f"standing upright in a neutral relaxed pose facing the camera, the whole "
        f"figure visible head to toe, with a small margin of empty space above the "
        f"head and below the feet (the figure centered, not touching the top or "
        f"bottom edges), with a calm, expressionless face.",
        f"{pron} is {wear}.",
    ]
    if feats:
        parts.append(f"{has} {feats}.")
    parts.append(f"Solid flat {bg.lower()} background, even ambient studio lighting, "
                 "no shadows on the background, sharp focus.")
    return " ".join(parts)


def creator_negative(info: dict, *, anima: bool = False) -> str:
    ci = info or {}
    male = str(ci.get("sex") or "female").strip().lower().startswith("m")
    neg = str(ci.get("negative_prompt") or "").strip() or (
        "bad quality, worst quality, low quality, score_1, score_2, score_3, "
        "blurry, jpeg artifacts, sepia" if anima else
        "bad quality,worst quality,worst detail,sketch,censor, missing arm, "
        "missing leg, distorted body")
    gender = (", ((((1girl, girl, woman, femine, breasts, vagina))))" if male
              else ", 1boy, man, penis, dick")
    return neg + gender


def resolve_t2i_models(oi: dict, settings: Optional[dict] = None,
                       mode: str = "") -> Dict[str, Any]:
    """Resolve the creator's t2i stack on a worker.  mode '' = auto
    (Illustrious if a checkpoint is found, else Anima).  Overrides:
    qwen_create_ckpt / qwen_create_dmd_lora / qwen_create_age_lora /
    qwen_create_unet / qwen_create_clip."""
    st = settings or {}

    def _pick(options: List[str], needles: List[str], override: str) -> Optional[str]:
        if override:
            hit = _resolve_name(options, override)
            if hit:
                return hit
        for o in options:
            lo = o.lower().replace("\\", "/")
            if any(n in lo for n in needles):
                return o
        return None

    want = (mode or str(st.get("qwen_create_mode") or "")).strip().lower()
    ckpts = _options(oi, "CheckpointLoaderSimple", "ckpt_name")
    unets = _options(oi, "UNETLoader", "unet_name")
    ggufs = _options(oi, "UnetLoaderGGUF", "unet_name")
    clips = _options(oi, "CLIPLoader", "clip_name")
    vaes = _options(oi, "VAELoader", "vae_name")
    loras = _options(oi, "LoraLoader", "lora_name") or _options(oi, "LoraLoaderModelOnly", "lora_name")

    # ---- v1.169: the app's own t2i model family (graphs mirrored from the
    # project-side workflows: ZIMAGE_TURBO_T2I / KREA2_TURBO_T2I /
    # KLEIN_EDIT_ULTRA Text2Image / Qwen-Image-Edit-2511 as t2i) --------------
    if want == "zimage":
        z_unet = _pick(unets, ["z_image", "z-image"], str(st.get("qwen_create_unet") or "").strip())
        z_clip = _pick(clips, ["qwen_3_4b"], str(st.get("qwen_create_clip") or "").strip())
        z_vae = _pick(vaes, ["ae.safetensors"], "")
        if not (z_unet and z_clip and z_vae):
            raise ValueError("Z-Image Turbo creator -- missing on this worker: "
                             + "; ".join(n for n, ok in (
                                 ("z_image_turbo unet", z_unet), ("qwen_3_4b CLIP", z_clip),
                                 ("ae.safetensors VAE", z_vae)) if not ok))
        return {"mode": "zimage", "unet": z_unet, "clip": z_clip, "vae": z_vae}
    if want == "krea2":
        k_unet = _pick(unets, ["krea2"], str(st.get("qwen_create_unet") or "").strip())
        k_clip = _pick(clips, ["qwen3vl", "qwen3_vl"], str(st.get("qwen_create_clip") or "").strip())
        k_vae = _pick(vaes, ["qwen_image_vae"], "")
        if not (k_unet and k_clip and k_vae):
            raise ValueError("Krea2 creator -- missing on this worker: "
                             + "; ".join(n for n, ok in (
                                 ("krea2_turbo unet", k_unet), ("qwen3vl CLIP", k_clip),
                                 ("qwen_image_vae VAE", k_vae)) if not ok))
        return {"mode": "krea2", "unet": k_unet, "clip": k_clip, "vae": k_vae}
    if want == "klein":
        from backend.services.character_studio.vnccs_native import klein_poses as _kp
        km = _kp.resolve_klein_models(oi, st, require_lora=False)
        # the app's proven Klein-t2i realism LoRA (KLEIN_EDIT_ULTRA workflow)
        realism = _pick(loras, ["lenovo_flux_klein9b", "lenovo_flux_klein"], "")
        return {"mode": "klein", "unet": km["unet"], "clip": km["clip"], "vae": km["vae"],
                "realism_lora": realism}
    if want == "qwen":
        q_unet = _pick(ggufs, ["qwen-image-edit-2511", "qwen_image_edit_2511"], "")
        q_loader = "gguf"
        if not q_unet:
            q_unet = _pick(unets, ["qwen-image-edit-2511", "qwen_image_edit_2511", "qwen_image"], "")
            q_loader = "unet"
        q_clip = _pick(clips, ["qwen_2.5_vl_7b", "qwen2.5_vl"], "")
        q_vae = _pick(vaes, ["qwen_image_vae"], "")
        q_light = _pick(loras, ["2511-lightning", "lightning-4steps"], "")
        if not (q_unet and q_clip and q_vae):
            raise ValueError("Qwen-Image creator -- missing on this worker: "
                             + "; ".join(n for n, ok in (
                                 ("qwen-image-edit-2511 unet", q_unet),
                                 ("qwen_2.5_vl_7b CLIP", q_clip),
                                 ("qwen_image_vae VAE", q_vae)) if not ok))
        return {"mode": "qwen", "unet": q_unet, "unet_loader": q_loader,
                "clip": q_clip, "vae": q_vae, "lightning": q_light}

    # ---- VNCCS's own two creator stacks -------------------------------------
    il_ckpt = _pick(ckpts, ["ilflatmix", "illustrious", "newgroundsmix", "waiillustrious"],
                    str(st.get("qwen_create_ckpt") or "").strip())
    anima_unet = _pick(unets, ["anima-base"], str(st.get("qwen_create_unet") or "").strip())
    if want == "illustrious" and not il_ckpt:
        raise ValueError("Illustrious creator -- no Illustrious checkpoint "
                         "(e.g. ILFlatMix.safetensors) on this worker")
    if want == "anima" and not anima_unet:
        raise ValueError("Anima creator -- anima-base-v1.0.safetensors not on this worker")
    if want != "anima" and il_ckpt:
        dmd = _pick(loras, ["dmd2_sdxl_4step"], str(st.get("qwen_create_dmd_lora") or "").strip())
        age = _pick(loras, ["mimimeter"], str(st.get("qwen_create_age_lora") or "").strip())
        return {"mode": "illustrious", "ckpt": il_ckpt, "dmd_lora": dmd, "age_lora": age}
    if anima_unet:
        clip = _pick(clips, ["qwen_3_06b"], str(st.get("qwen_create_clip") or "").strip())
        vae = _pick(vaes, ["qwen_image_vae"], "")
        turbo = _pick(loras, ["anima-turbo"], "")
        if not clip or not vae:
            raise ValueError("Anima creator needs qwen_3_06b_base.safetensors (CLIP) "
                             "and qwen_image_vae.safetensors on the worker")
        # the app's tuned Anima aesthetic stack (ANIMA_T2I workflow): optional,
        # applied only when the UI's "app LoRA stack" toggle is on -- VNCCS-exact
        # stays the default.
        stack = [h for h in (
            _pick(loras, ["anima-highres-aesthetic-boost"], ""),
            _pick(loras, ["masterpieces-v5", "anima-preview-3-masterpieces"], ""),
            _pick(loras, ["anima_p3_rdbt"], ""),
        ) if h]
        return {"mode": "anima", "unet": anima_unet, "clip": clip, "vae": vae,
                "turbo_lora": turbo, "stack_loras": stack}
    raise ValueError("Qwen (VNCCS) creator -- no Illustrious checkpoint "
                     "(e.g. ILFlatMix.safetensors) or Anima model "
                     "(anima-base-v1.0.safetensors) found on this worker -- "
                     "or pick Klein / Z-Image / Krea2 / Qwen in Qwen create settings")


def build_t2i_creator_graph(
    *,
    prompt: str,
    negative: str,
    seed: int,
    models: Dict[str, Any],
    steps: Optional[int] = None,
    cfg: Optional[float] = None,
    width: int = CREATOR_WIDTH,
    height: int = CREATOR_HEIGHT,
    age: Optional[int] = None,
    use_quality_loras: bool = True,
    filename_prefix: str = "rbmn_vnccs/qwen_creator",
) -> Tuple[Dict[str, dict], Dict[str, str]]:
    """VNCCS CharacterCreatorV2 base render: plain t2i at 640x1536.
    Illustrious: euler/normal 20 steps CFG 8 (DMD2 turbo -> 4/1); Anima:
    er_sde/simple 30 steps CFG 4 (turbo LoRA -> 12/1, strength_clip 0)."""
    api: Dict[str, dict] = {}
    mode = models["mode"]
    # ---- v1.169: the app's t2i family -- graphs mirrored from the project-side
    # workflows; all run CFG 1 with a ZEROED negative (text negatives are inert
    # there), so ``negative`` is ignored for these modes.
    if mode == "zimage":
        api["u"] = {"class_type": "UNETLoader",
                    "inputs": {"unet_name": models["unet"], "weight_dtype": "default"}}
        api["c"] = {"class_type": "CLIPLoader",
                    "inputs": {"clip_name": models["clip"], "type": "lumina2", "device": "default"}}
        api["v"] = {"class_type": "VAELoader", "inputs": {"vae_name": models["vae"]}}
        api["ms"] = {"class_type": "ModelSamplingAuraFlow",
                     "inputs": {"model": ["u", 0], "shift": 3.0}}
        api["pos"] = {"class_type": "CLIPTextEncode", "inputs": {"clip": ["c", 0], "text": prompt}}
        api["neg"] = {"class_type": "ConditioningZeroOut", "inputs": {"conditioning": ["pos", 0]}}
        api["lat"] = {"class_type": "EmptySD3LatentImage",
                      "inputs": {"width": int(width), "height": int(height), "batch_size": 1}}
        api["ks"] = {"class_type": "KSampler",
                     "inputs": {"model": ["ms", 0], "seed": int(seed),
                                "steps": int(steps or 8), "cfg": float(cfg or 1.0),
                                "sampler_name": "res_multistep", "scheduler": "simple",
                                "positive": ["pos", 0], "negative": ["neg", 0],
                                "latent_image": ["lat", 0], "denoise": 1.0}}
        api["dec"] = {"class_type": "VAEDecode", "inputs": {"vae": ["v", 0], "samples": ["ks", 0]}}
        api["save"] = {"class_type": "SaveImage",
                       "inputs": {"images": ["dec", 0], "filename_prefix": filename_prefix}}
        return api, {"base": "save"}
    if mode == "krea2":
        api["u"] = {"class_type": "UNETLoader",
                    "inputs": {"unet_name": models["unet"], "weight_dtype": "default"}}
        api["c"] = {"class_type": "CLIPLoader",
                    "inputs": {"clip_name": models["clip"], "type": "krea2", "device": "default"}}
        api["v"] = {"class_type": "VAELoader", "inputs": {"vae_name": models["vae"]}}
        api["pos"] = {"class_type": "CLIPTextEncode", "inputs": {"clip": ["c", 0], "text": prompt}}
        api["neg"] = {"class_type": "ConditioningZeroOut", "inputs": {"conditioning": ["pos", 0]}}
        api["lat"] = {"class_type": "EmptyLatentImage",
                      "inputs": {"width": int(width), "height": int(height), "batch_size": 1}}
        api["ks"] = {"class_type": "KSampler",
                     "inputs": {"model": ["u", 0], "seed": int(seed),
                                "steps": int(steps or 8), "cfg": float(cfg or 1.0),
                                "sampler_name": "er_sde", "scheduler": "simple",
                                "positive": ["pos", 0], "negative": ["neg", 0],
                                "latent_image": ["lat", 0], "denoise": 1.0}}
        api["dec"] = {"class_type": "VAEDecode", "inputs": {"vae": ["v", 0], "samples": ["ks", 0]}}
        api["save"] = {"class_type": "SaveImage",
                       "inputs": {"images": ["dec", 0], "filename_prefix": filename_prefix}}
        return api, {"base": "save"}
    if mode == "klein":
        api["u"] = {"class_type": "UNETLoader",
                    "inputs": {"unet_name": models["unet"], "weight_dtype": "default"}}
        api["c"] = {"class_type": "CLIPLoader",
                    "inputs": {"clip_name": models["clip"], "type": "flux2", "device": "default"}}
        api["v"] = {"class_type": "VAELoader", "inputs": {"vae_name": models["vae"]}}
        _mref, _cref = ["u", 0], ["c", 0]
        if use_quality_loras and models.get("realism_lora"):
            # lenovo realism LoRA -- the app's proven Klein-t2i look
            api["rl"] = {"class_type": "LoraLoader",
                         "inputs": {"model": _mref, "clip": _cref,
                                    "lora_name": models["realism_lora"],
                                    "strength_model": 1.0, "strength_clip": 1.0}}
            _mref, _cref = ["rl", 0], ["rl", 1]
        api["pos"] = {"class_type": "CLIPTextEncode", "inputs": {"clip": _cref, "text": prompt}}
        api["neg"] = {"class_type": "ConditioningZeroOut", "inputs": {"conditioning": ["pos", 0]}}
        api["lat"] = {"class_type": "EmptyFlux2LatentImage",
                      "inputs": {"width": int(width), "height": int(height), "batch_size": 1}}
        api["sig"] = {"class_type": "Flux2Scheduler",
                      "inputs": {"steps": int(steps or 8), "width": int(width), "height": int(height)}}
        api["gd"] = {"class_type": "CFGGuider",
                     "inputs": {"model": _mref, "positive": ["pos", 0],
                                "negative": ["neg", 0], "cfg": float(cfg or 1.0)}}
        api["ns"] = {"class_type": "RandomNoise", "inputs": {"noise_seed": int(seed)}}
        api["sm"] = {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "euler"}}
        api["sc"] = {"class_type": "SamplerCustomAdvanced",
                     "inputs": {"noise": ["ns", 0], "guider": ["gd", 0], "sampler": ["sm", 0],
                                "sigmas": ["sig", 0], "latent_image": ["lat", 0]}}
        api["dec"] = {"class_type": "VAEDecode", "inputs": {"vae": ["v", 0], "samples": ["sc", 0]}}
        api["save"] = {"class_type": "SaveImage",
                       "inputs": {"images": ["dec", 0], "filename_prefix": filename_prefix}}
        return api, {"base": "save"}
    if mode == "qwen":
        if models.get("unet_loader") == "gguf":
            api["u"] = {"class_type": "UnetLoaderGGUF", "inputs": {"unet_name": models["unet"]}}
        else:
            api["u"] = {"class_type": "UNETLoader",
                        "inputs": {"unet_name": models["unet"], "weight_dtype": "default"}}
        api["c"] = {"class_type": "CLIPLoader",
                    "inputs": {"clip_name": models["clip"], "type": "qwen_image", "device": "default"}}
        api["v"] = {"class_type": "VAELoader", "inputs": {"vae_name": models["vae"]}}
        model_ref, clip_ref = ["u", 0], ["c", 0]
        if models.get("lightning"):
            api["turbo"] = {"class_type": "LoraLoader",
                            "inputs": {"model": model_ref, "clip": clip_ref,
                                       "lora_name": models["lightning"],
                                       "strength_model": 1.0, "strength_clip": 1.0}}
            model_ref, clip_ref = ["turbo", 0], ["turbo", 1]
        api["pos"] = {"class_type": "CLIPTextEncode", "inputs": {"clip": clip_ref, "text": prompt}}
        api["neg"] = {"class_type": "ConditioningZeroOut", "inputs": {"conditioning": ["pos", 0]}}
        api["lat"] = {"class_type": "EmptySD3LatentImage",
                      "inputs": {"width": int(width), "height": int(height), "batch_size": 1}}
        api["ks"] = {"class_type": "KSampler",
                     "inputs": {"model": model_ref, "seed": int(seed),
                                "steps": int(steps or (4 if models.get("lightning") else 20)),
                                "cfg": float(cfg or (1.0 if models.get("lightning") else 2.5)),
                                "sampler_name": "euler", "scheduler": "simple",
                                "positive": ["pos", 0], "negative": ["neg", 0],
                                "latent_image": ["lat", 0], "denoise": 1.0}}
        api["dec"] = {"class_type": "VAEDecode", "inputs": {"vae": ["v", 0], "samples": ["ks", 0]}}
        api["save"] = {"class_type": "SaveImage",
                       "inputs": {"images": ["dec", 0], "filename_prefix": filename_prefix}}
        return api, {"base": "save"}
    if models["mode"] == "illustrious":
        api["ckpt"] = {"class_type": "CheckpointLoaderSimple",
                       "inputs": {"ckpt_name": models["ckpt"]}}
        model_ref, clip_ref, vae_ref = ["ckpt", 0], ["ckpt", 1], ["ckpt", 2]
        if models.get("dmd_lora"):
            api["dmd"] = {"class_type": "LoraLoader",
                          "inputs": {"model": model_ref, "clip": clip_ref,
                                     "lora_name": models["dmd_lora"],
                                     "strength_model": 1.0, "strength_clip": 1.0}}
            model_ref, clip_ref = ["dmd", 0], ["dmd", 1]
        if models.get("age_lora") and age is not None:
            a_str = age_lora_strength(age)
            if abs(a_str) > 0.01:
                api["age"] = {"class_type": "LoraLoader",
                              "inputs": {"model": model_ref, "clip": clip_ref,
                                         "lora_name": models["age_lora"],
                                         "strength_model": a_str, "strength_clip": a_str}}
                model_ref, clip_ref = ["age", 0], ["age", 1]
        _steps = int(steps or (4 if models.get("dmd_lora") else 20))
        _cfg = float(cfg or (1.0 if models.get("dmd_lora") else 8.0))
        sampler, scheduler = "euler", "normal"
    else:  # anima
        api["u"] = {"class_type": "UNETLoader",
                    "inputs": {"unet_name": models["unet"], "weight_dtype": "default"}}
        api["c"] = {"class_type": "CLIPLoader",
                    "inputs": {"clip_name": models["clip"], "type": "stable_diffusion",
                               "device": "default"}}
        api["v"] = {"class_type": "VAELoader", "inputs": {"vae_name": models["vae"]}}
        model_ref, clip_ref, vae_ref = ["u", 0], ["c", 0], ["v", 0]
        if models.get("turbo_lora"):
            api["turbo"] = {"class_type": "LoraLoader",
                            "inputs": {"model": model_ref, "clip": clip_ref,
                                       "lora_name": models["turbo_lora"],
                                       "strength_model": 1.0, "strength_clip": 0.0}}
            model_ref, clip_ref = ["turbo", 0], ["turbo", 1]
        if use_quality_loras:
            # the app's ANIMA_T2I aesthetic stack (highres boost + masterpieces
            # + rdbt), all @1.0 -- optional deviation from VNCCS-exact
            for si, ln in enumerate((models.get("stack_loras") or [])[:4]):
                nid = f"stk{si}"
                api[nid] = {"class_type": "LoraLoader",
                            "inputs": {"model": model_ref, "clip": clip_ref,
                                       "lora_name": ln,
                                       "strength_model": 1.0, "strength_clip": 1.0}}
                model_ref, clip_ref = [nid, 0], [nid, 1]
        _steps = int(steps or (12 if models.get("turbo_lora") else 30))
        _cfg = float(cfg or (1.0 if models.get("turbo_lora") else 4.0))
        sampler, scheduler = "er_sde", "simple"
    api["pos"] = {"class_type": "CLIPTextEncode", "inputs": {"clip": clip_ref, "text": prompt}}
    api["neg"] = {"class_type": "CLIPTextEncode", "inputs": {"clip": clip_ref, "text": negative}}
    api["lat"] = {"class_type": "EmptyLatentImage",
                  "inputs": {"width": int(width), "height": int(height), "batch_size": 1}}
    api["ks"] = {"class_type": "KSampler",
                 "inputs": {"model": model_ref, "seed": int(seed), "steps": _steps,
                            "cfg": _cfg, "sampler_name": sampler, "scheduler": scheduler,
                            "positive": ["pos", 0], "negative": ["neg", 0],
                            "latent_image": ["lat", 0], "denoise": 1.0}}
    api["dec"] = {"class_type": "VAEDecode", "inputs": {"vae": vae_ref, "samples": ["ks", 0]}}
    api["save"] = {"class_type": "SaveImage",
                   "inputs": {"images": ["dec", 0], "filename_prefix": filename_prefix}}
    return api, {"base": "save"}


def build_reference_collage(images: List[bytes], background=(255, 255, 255)) -> bytes:
    """VNCCS CharacterCloner.process(): pack the reference photos into ONE grid
    -- cell = max WxH across images, column count minimizing
    symmetric_aspect + 0.01*|cols-rows|, black gaps, images centered."""
    import io as _io
    import math as _math
    from PIL import Image, ImageOps
    ims = []
    for b in images:
        im = Image.open(_io.BytesIO(b))
        im = ImageOps.exif_transpose(im)
        if im.mode == "RGBA":
            bg = Image.new("RGB", im.size, tuple(background))
            bg.paste(im, mask=im.split()[3])
            im = bg
        elif im.mode != "RGB":
            im = im.convert("RGB")
        ims.append(im)
    if not ims:
        raise ValueError("collage needs at least one reference image")
    if len(ims) == 1:
        buf = _io.BytesIO()
        ims[0].save(buf, format="PNG")
        return buf.getvalue()
    cw = max(im.width for im in ims)
    ch = max(im.height for im in ims)
    n = len(ims)
    best, best_score = 1, None
    for cols in range(1, n + 1):
        rows = _math.ceil(n / cols)
        w, h = cols * cw, rows * ch
        aspect = max(w / h, h / w)
        score = aspect + 0.01 * abs(cols - rows)
        if best_score is None or score < best_score:
            best, best_score = cols, score
    cols = best
    rows = _math.ceil(n / cols)
    canvas = Image.new("RGB", (cols * cw, rows * ch), (0, 0, 0))
    for i, im in enumerate(ims):
        cx = (i % cols) * cw + (cw - im.width) // 2
        cy = (i // cols) * ch + (ch - im.height) // 2
        canvas.paste(im, (cx, cy))
    buf = _io.BytesIO()
    canvas.save(buf, format="PNG")
    return buf.getvalue()


REMOVE_CLOTHES_PROMPT = "Undress character"          # shipped cloner workflow
# SFW strip base (v1.197.1): the bra must fully cover the chest — spelling it out
# stops the LoRA leaving a bare chest + straps.
REMOVE_CLOTHES_PROMPT_SOFT = ("Dress character: plain white bra and plain white panties. "
                              "The white bra fully covers both breasts and the whole chest "
                              "(not topless, no bare chest, no exposed nipples). Barefoot "
                              "with bare feet, remove all shoes, socks and footwear")
# NSFW second pass (v1.196.3): the ClothesCore LoRA is TRAINED on "Undress character",
# so pass 2 reuses that trained trigger to clear the last underwear layer.
REMOVE_UNDERWEAR_PROMPT = "Undress character"


def build_qwen_remove_clothes_graph(
    *,
    collage_file: str,
    seed: int,
    models: Dict[str, Any],
    prompt: str = REMOVE_CLOTHES_PROMPT,
    target_size: int = 1024,
    steps: int = 4,
    cfg: float = 1.0,
    ref_weight: float = 1.0,
    filename_prefix: str = "rbmn_vnccs/qwen_undress",
) -> Tuple[Dict[str, dict], Dict[str, str]]:
    """VNCCS cloner ``remove_clothes`` stage: ONE Qwen edit of the reference
    collage through the ClothesCore LoRA (there is no separate undress LoRA).
    ``ref_weight`` boosts the collage (body/identity) reference so a fuller build
    survives the strip."""
    api: Dict[str, dict] = {}
    model_ref, clip_ref, vae_ref = _qwen_loaders(api, models)
    api["cl"] = {"class_type": "LoraLoaderModelOnly",
                 "inputs": {"model": model_ref, "lora_name": models["clothes_lora"],
                            "strength_model": 1.0}}
    model_ref = ["cl", 0]
    api["col_load"] = {"class_type": "LoadImage", "inputs": {"image": collage_file}}
    api["enc"] = {"class_type": QWEN_ENCODER_CLASS,
                  "inputs": _encoder_inputs(clip_ref, vae_ref, prompt,
                                            image1=["col_load", 0],
                                            names=("image 1", "image 2", "image 3"),
                                            target_size=target_size,
                                            weight1=_clamp_ref_weight(ref_weight),
                                            background_color="White")}
    api["ks"] = {"class_type": "KSampler",
                 "inputs": {"model": model_ref, "seed": int(seed), "steps": int(steps),
                            "cfg": float(cfg), "sampler_name": "euler",
                            "scheduler": "simple", "positive": ["enc", 0],
                            "negative": ["enc", 1], "latent_image": ["enc", 2],
                            "denoise": 1.0}}
    dec = _decode(api, "dec", ["ks", 0], vae_ref, bool(models.get("tiled_decode")))
    api["save"] = {"class_type": "SaveImage",
                   "inputs": {"images": [dec, 0], "filename_prefix": filename_prefix}}
    return api, {"undressed": "save"}
