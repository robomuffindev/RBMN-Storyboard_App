"""Klein 9B pose generation for VNCCS Klein Hybrid mode.

Faithful flattening of the vendored reference workflow
``vnccs-utils/workflows/VNCCS_Utils Pose Studio Klein9b.json`` (its two
subgraphs "Klein9b models" and "Klein9b Encoder" expanded):

    pose render   -> ImageScaleToTotalPixels(1MP, lanczos) -> VAEEncode ----+
    identity img  -> ImageScaleToTotalPixels(1MP, lanczos) -> VAEEncode --+ |
    prompt -> CLIPTextEncode -> ReferenceLatent(pose) -> ReferenceLatent(identity)
    ""     -> CLIPTextEncode -> ReferenceLatent(pose) -> ReferenceLatent(identity)   (negative)
    UNETLoader(Klein 9B) -> LoraLoaderModelOnly(VNCCS_PoseStudioKlein9b_V1, 1.0)
      -> CFGGuider(cfg=1) + RandomNoise(seed) + euler + Flux2Scheduler(4 steps,
         WxH from the scaled pose image) -> SamplerCustomAdvanced
      -> VAEDecode -> (ImageBatch chain) -> SaveImage

The latent is empty (EmptyFlux2LatentImage at the pose image's size); pose and
identity condition the run purely as Flux2 reference latents — reference order
matters: the POSE image is reference 1, the IDENTITY image is reference 2.

The pose LoRA is distributed through the VNCCS Model Manager repository
``MIUProject/VNCCS_PoseStudio_Klein`` (file
``Klein9b/VNCCS/VNCCS_PoseStudioKlein9b_V1.safetensors``).  Model names are
resolved against each worker's /object_info by basename, so subfolder and
slash-direction differences across machines don't matter.
"""
from __future__ import annotations

import base64
import logging
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# What we look for on the worker (matched by basename against /object_info).
KLEIN_POSE_LORA_BASENAME = "VNCCS_PoseStudioKlein9b_V1.safetensors"
KLEIN_POSE_REPO = "MIUProject/VNCCS_PoseStudio_Klein"
REALISM_LORA_BASENAME = "anime2real-semi.safetensors"
SAM3_LOADER_CLASS = "easy sam3ModelLoader"
SAM3_SEG_CLASS = "easy sam3ImageSegmentation"
SAM3_DEFAULT_MODEL = "sam3.pt"
SAM3_DEFAULT_ARTICLES = (
    "necklace, chain, pendant, choker, bracelet, wristband, watch, ring, earrings, "
    "shirt, t-shirt, blouse, collar, sleeve, jacket, coat")
DEFAULT_KLEIN_UNET = "flux-2-klein-9b-fp8.safetensors"
DEFAULT_KLEIN_CLIP = "qwen_3_8b_fp8mixed_abliterated.safetensors"
DEFAULT_KLEIN_VAE = "flux2-vae.safetensors"
KLEIN_POSE_STEPS = 4          # Flux2Scheduler steps in the reference workflow
KLEIN_POSE_CFG = 1.0          # CFGGuider cfg in the reference workflow

# Negative-guidance experiment for STRIP base bodies.  Klein's reference workflow
# runs cfg=1 (the negative conditioning is ignored), so shoes/jewelry the
# reference photo or the model's own prior insist on cannot be removed by
# positive text alone (v1.101.0 proved that).  Opt-in (default on for STRIP
# bases): a real negative prompt + a modest cfg so (positive - negative) pushes
# those items out.  Never applies in KEEP/clone-outfit mode.
KLEIN_STRIP_NEGATIVE = (
    # garments (the leftover-shirt / partial-clothing bits): specific outer /
    # leg garments only -- deliberately NOT "bra", "panties" or "underwear", so
    # the target white bra + panties from _base_body_state are never suppressed.
    "shirt, t-shirt, blouse, sweater, hoodie, jacket, coat, cardigan, vest, "
    "dress, gown, robe, skirt, pants, jeans, trousers, shorts, leggings, "
    "collar, lapel, sleeves, cuffs, buttons, zipper, pocket, necktie, tie, scarf, "
    "shoes, boots, sandals, high heels, loafers, footwear, socks, shoe soles, "
    "slippers, sneakers, earrings, necklace, chain, bracelet, wristband, anklet, "
    "ring, watch, jewelry, piercing, glasses, hat, headwear, "
    # foot POSTURE: removing heels must not leave the feet pointed/arched as if
    # still wearing them -- force a flat plantigrade stance.
    "tiptoe, on tiptoes, standing on toes, tip-toe, pointed toes, pointed feet, "
    "arched feet, high-arched feet, en pointe, ballet feet, ballet pointe, "
    "raised heels, heels up, heel-up stance, plantar-flexed feet, invisible heels, "
    # body IDEALIZATION: keep the reference's real weight/build; do not slim or
    # beautify a fuller figure into a generic model body.
    "slimmed down, slimmer than reference, made thinner, weight loss, "
    "idealized body, beautified body, generic fashion-model figure, supermodel body, "
    "airbrushed, over-smoothed skin, plastic skin, retouched, reduced belly, "
    "smaller waist than reference, snatched waist, flattering slimming")


def resolve_strip_negative(settings, keep_clothing):
    """(negative_text, cfg) for a STRIP-mode base run -- the "Cleanup" control.

    studio setting ``klein_cleanup``:  'off' | 'gentle' (default) | 'strong'.
      off    -> no negative, cfg 1.0 (pure reference; keeps shoes/jewelry, but
                zero cfg>1 grain -- the cleanest flat areas).
      gentle -> negative on at cfg 1.2 (removes most shoes/jewelry with much less
                flat-area interference than the old 1.5).
      strong -> negative on at cfg 1.5 (hardest suppression, a bit more grain).
    Never applies in KEEP/clone-outfit mode.  ``klein_strip_negative`` is honored
    as a legacy alias; ``klein_strip_cfg`` still overrides the cfg explicitly."""
    if keep_clothing:
        return "", KLEIN_POSE_CFG
    st = settings or {}
    mode = str(st.get("klein_cleanup") or st.get("klein_strip_negative")
               or "gentle").strip().lower()
    if mode in ("off", "false", "0", "no", "disabled", "none"):
        return "", KLEIN_POSE_CFG
    neg = str(st.get("klein_strip_negative_text") or KLEIN_STRIP_NEGATIVE).strip()
    cfg = 1.5 if mode in ("strong", "high", "hard", "2") else 1.2
    if str(st.get("klein_strip_cfg") or "").strip():
        try:
            cfg = float(st.get("klein_strip_cfg"))
        except Exception:  # noqa: BLE001
            pass
    cfg = max(1.0, min(3.0, cfg))
    return neg, cfg


# Always-on anatomy guard for POSE runs: the requested pose vs. the reference
# image's own pose can fight and spawn extra/duplicated limbs (~1 in 12). These
# terms ride in the pose negative to suppress that (only bites when cfg > 1.0,
# i.e. STRIP/cleanup mode — which is the underwear-base case where it happens).
KLEIN_ANATOMY_NEGATIVE = (
    "extra limbs, extra arms, extra legs, extra hands, extra feet, duplicated "
    "limbs, fused limbs, malformed limbs, mutated hands, extra fingers, missing "
    "fingers, deformed, disfigured, bad anatomy, "
    # dark ink-like separation lines where skin overlaps skin (fingers/hands, arm
    # against torso) at low step counts -- raising Pose steps is the main fix, this
    # nudges the model away from hard black occlusion contours
    "harsh black outlines, dark contour lines between fingers, ink outline, "
    "hard black edges where skin overlaps, lineart, cel shading")


def with_anatomy_negative(neg: str) -> str:
    """Append the anatomy guard to a pose negative (dedup-safe, handles empty)."""
    n = (neg or "").strip().rstrip(",").strip()
    if "extra limbs" in n:
        return n
    return f"{n}, {KLEIN_ANATOMY_NEGATIVE}" if n else KLEIN_ANATOMY_NEGATIVE


def resolve_klein_steps(settings) -> int:
    """Sampler steps for Klein runs.  Default 6 (the reference workflow uses 4;
    6 noticeably cleans up flat-area grain/interference at a small time cost).
    studio setting klein_steps, clamped 2-32 (was 16 -- complicated skin tones
    can show scan-line grain that only fully resolves at higher step counts,
    and poses need more headroom than the base; time scales ~linearly)."""
    try:
        n = int((settings or {}).get("klein_steps") or 6)
    except Exception:  # noqa: BLE001
        n = 6
    return max(2, min(32, n))

# PuLID-Flux2 (iFayens/ComfyUI-PuLID-Flux2) -- the only identity adapter that
# exists for FLUX.2 as of 2026-07; Klein 4B/9B are its best-supported targets.
# Node classes (verified against the pack source): PuLIDInsightFaceLoader,
# PuLIDEVACLIPLoader, PuLIDModelLoader, ApplyPuLIDFlux2 (MODEL in -> MODEL out,
# patched like a LoRA; the identity face is a plain IMAGE input).
PULID_APPLY_CLASS = "ApplyPuLIDFlux2"
PULID_DEFAULT_STRENGTH = 1.4  # the pack README's recommended value


def resolve_pulid(oi: dict, settings: Optional[dict] = None) -> Optional[Dict[str, Any]]:
    """PuLID-Flux2 config for a worker, or None when unavailable/disabled.

    studio settings keys (all optional):
      klein_pulid          'auto' (default: use whenever the node pack + weights
                           are on the worker) | 'off'
      klein_pulid_file     override the pulid weights filename
      klein_pulid_strength float, default 1.4 (pack README recommendation)
      klein_pulid_provider 'CPU' (default) | 'CUDA' | 'ROCM' for InsightFace
    """
    st = settings or {}
    # PuLID-Flux2 requires the `insightface` python package ON THE WORKER; when
    # it's missing the node ERRORS the whole job (No module named 'insightface').
    # We can't detect that from /object_info, so PuLID is strictly OPT-IN: only an
    # explicit klein_pulid='on' enables it. Install insightface on the worker,
    # then turn it On in Settings.
    mode = str(st.get("klein_pulid") or "off").strip().lower()
    if mode not in ("on", "true", "1", "yes"):
        return None
    if PULID_APPLY_CLASS not in (oi or {}):
        return None
    files = [f for f in _options(oi, "PuLIDModelLoader", "pulid_file")
             if f and f != "__create_new__"]
    if not files:
        logger.info("klein pulid: node pack present but no weights in models/pulid -- skipping")
        return None
    want = str(st.get("klein_pulid_file") or "").strip()
    file = (_resolve_name(files, want) if want else None)
    if file is None:
        # prefer a Klein-flavored weight file, then 9B-flavored, then the
        # NEWEST version suffix (v2 beats v1), then A-Z
        import re as _re

        def _ver(f: str) -> int:
            m = _re.search(r"v(\d+)", f.lower())
            return int(m.group(1)) if m else 0

        ranked = sorted(files, key=lambda f: (("klein" not in f.lower()),
                                              ("9b" not in f.lower()),
                                              -_ver(f), f.lower()))
        file = ranked[0]
    try:
        strength = float(st.get("klein_pulid_strength") or PULID_DEFAULT_STRENGTH)
    except Exception:  # noqa: BLE001
        strength = PULID_DEFAULT_STRENGTH
    # v1.171.1: ceiling raised 2.0 -> 3.0 for identity dial-in experiments
    # (>1.4 progressively stamps the embedding's texture into the skin)
    strength = max(0.0, min(3.0, strength))
    provider = str(st.get("klein_pulid_provider") or "CPU").strip().upper()
    if provider not in ("CPU", "CUDA", "ROCM"):
        provider = "CPU"
    return {"file": file, "strength": strength, "provider": provider}


def _inject_pulid(api: Dict[str, dict], model_ref: list, face_file: str,
                  pulid: Dict[str, Any]) -> list:
    """Add the PuLID-Flux2 loader/apply nodes to ``api`` (loaders are shared),
    patching the model chain -- returns the new MODEL link.  ``face_file`` is an
    uploaded image with a clear view of the identity face (crop preferred)."""
    api.setdefault("pulid_if", {"class_type": "PuLIDInsightFaceLoader",
                                "inputs": {"provider": pulid["provider"]}})
    api.setdefault("pulid_ev", {"class_type": "PuLIDEVACLIPLoader", "inputs": {}})
    api.setdefault("pulid_md", {"class_type": "PuLIDModelLoader",
                                "inputs": {"pulid_file": pulid["file"]}})
    api.setdefault("pulid_img", {"class_type": "LoadImage", "inputs": {"image": face_file}})
    api["pulid_ap"] = {"class_type": PULID_APPLY_CLASS,
                       "inputs": {"model": model_ref, "pulid_model": ["pulid_md", 0],
                                  "strength": float(pulid["strength"]),
                                  "eva_clip": ["pulid_ev", 0],
                                  "face_analysis": ["pulid_if", 0],
                                  "image": ["pulid_img", 0]}}
    return ["pulid_ap", 0]


# ReferenceLatentPlus (shootthesound/comfyui-ReferenceLatentPlus) -- a drop-in
# upgrade to the stock Flux ReferenceLatent that VAE-encodes an image into a
# reference latent BUT can MediaPipe-mask which region contributes (face / hair /
# body / clothes / background), plus per-image strength and timestep gating.  We
# use it for the BODY channel: encode a body/full reference with the GARMENT
# pixels masked out, so build/shoulders/chest/hips/stature transfer to the base
# body while the outfit cannot leak.  Node takes conditioning+vae+image(s) in and
# returns conditioning with the reference latents attached (verified INPUT_TYPES).
REFLATENTPLUS_CLASS = "ReferenceLatentPlus"


def resolve_reflatentplus(oi: dict, settings: Optional[dict] = None) -> Optional[Dict[str, Any]]:
    """Config for the ReferenceLatentPlus body channel, or None when the node
    isn't installed on the worker / is disabled.  Pure ComfyUI node (no external
    python dependency), so unlike PuLID it defaults to 'auto' = use whenever the
    node is present; runs on workers without it fall back automatically.

    studio settings keys (all optional):
      klein_body_match          'auto' (default: use when node present) | 'off'
      klein_body_match_strength float, default 1.0 (match the old full-ref latent
                                so face/body fidelity holds; node's own default 0.85)
      klein_body_match_end      float 0-1, default 1.0 (NO gating — keep the ref
                                active through the refine steps, else the FACE
                                drifts/gets reshaped in late denoise)
      klein_body_match_keep     'person' (DEFAULT: whole person = face+hair+body,
                                only CLOTHES + background masked out — carries the
                                head/face STRUCTURE + body, drops the garment)
                                | 'body' (body-skin only — NO face, legacy)
                                | 'full' (no mask, whole clothed image)
                                | 'person+clothes' (person incl. clothes, bg out)

    NOTE: masking 'body' alone excludes the face+hair regions, so the reference
    carries no head structure and PuLID/the model invent a wrong head shape and
    "swap" a face on.  Default is therefore the whole person minus clothing.
    """
    st = settings or {}
    mode = str(st.get("klein_body_match") or "auto").strip().lower()
    if mode in ("off", "0", "false", "no", "none"):
        return None
    if REFLATENTPLUS_CLASS not in (oi or {}):
        return None
    try:
        # 1.6 default (was 1.0): a STRONG body reference so the correct build
        # (base/photos) firmly out-votes the pose MANNEQUIN's generic body --
        # confirmed by testing that 1.6 holds plump/curvy bodies well where 1.0-1.25
        # drifted.  Tunable via klein_body_match_strength.
        strength = float(st.get("klein_body_match_strength") or 1.6)
    except Exception:  # noqa: BLE001
        strength = 1.6
    strength = max(-5.0, min(50.0, strength))
    try:
        end = float(st.get("klein_body_match_end") or 1.0)
    except Exception:  # noqa: BLE001
        end = 1.0
    end = max(0.05, min(1.0, end))
    keep = str(st.get("klein_body_match_keep") or "person").strip().lower()
    if keep in ("full", "all", "whole", "unmasked", "none"):
        masks = {"face": False, "hair": False, "body": False,
                 "clothes": False, "background": False}
    elif keep in ("body", "body-only", "bodyonly", "body_skin"):
        # legacy body-skin only — excludes the face/head (use only to test)
        masks = {"face": False, "hair": False, "body": True,
                 "clothes": False, "background": False}
    elif keep in ("person+clothes", "person_clothes", "body+clothes", "silhouette"):
        masks = {"face": True, "hair": True, "body": True,
                 "clothes": True, "background": False}
    else:  # 'person' (DEFAULT) — whole person, clothes + background removed
        masks = {"face": True, "hair": True, "body": True,
                 "clothes": False, "background": False}
    return {"strength": strength, "start": 0.0, "end": end, "masks": masks}


def _inject_reflatentplus(api: Dict[str, dict], cond_ref: list, body_loads: List[str],
                          cfg: Dict[str, Any], tag: str) -> list:
    """Append a ReferenceLatentPlus node onto ``cond_ref`` that adds the masked
    BODY reference latents for ``body_loads`` (already-created LoadImage node
    ids).  Returns the new conditioning ref.  ``tag`` keeps ids unique per
    pos/neg chain and per pose.  ``vae`` node ``v`` is assumed present."""
    m = cfg.get("masks") or {}
    inp: Dict[str, Any] = {
        "conditioning": [cond_ref[0], cond_ref[1]],
        "vae": ["v", 0],
        "max_megapixels": 1.0,
        "mask_fill_mode": "pixel_grey",
    }
    for i, bl in enumerate(body_loads[:4]):
        pfx = f"image{i + 1}"
        inp[f"image_{i + 1}"] = [bl, 0]
        inp[f"{pfx}_strength"] = float(cfg.get("strength", 0.85))
        inp[f"{pfx}_face"] = bool(m.get("face", False))
        inp[f"{pfx}_hair"] = bool(m.get("hair", False))
        inp[f"{pfx}_body"] = bool(m.get("body", True))
        inp[f"{pfx}_clothes"] = bool(m.get("clothes", False))
        inp[f"{pfx}_background"] = bool(m.get("background", False))
        inp[f"{pfx}_ignore_area"] = "none"
        inp[f"{pfx}_feather"] = 0
        inp[f"{pfx}_grow"] = 0
        inp[f"{pfx}_start_percent"] = float(cfg.get("start", 0.0))
        inp[f"{pfx}_end_percent"] = float(cfg.get("end", 0.7))
    nid = f"{tag}_rlp"
    api[nid] = {"class_type": REFLATENTPLUS_CLASS, "inputs": inp}
    return [nid, 0]


def _inject_reflatentplus_multi(api: Dict[str, dict], cond_ref: list,
                                entries: List[Dict[str, Any]], tag: str) -> list:
    """v1.164: like ``_inject_reflatentplus`` but each entry carries its OWN
    masks / strength / timestep gate, so one node can hold SPLIT-GATED
    references (research: composition forms in the noisy-early steps, identity
    and texture in the clean-late steps -- gate a body ref early and a
    face+hair ref late instead of one ref doing both jobs)."""
    inp: Dict[str, Any] = {
        "conditioning": [cond_ref[0], cond_ref[1]],
        "vae": ["v", 0],
        "max_megapixels": 1.0,
        "mask_fill_mode": "pixel_grey",
    }
    for i, e in enumerate(entries[:4]):
        m = e.get("masks") or {}
        pfx = f"image{i + 1}"
        inp[f"image_{i + 1}"] = [e["load"], 0]
        inp[f"{pfx}_strength"] = float(e.get("strength", 0.85))
        inp[f"{pfx}_face"] = bool(m.get("face", False))
        inp[f"{pfx}_hair"] = bool(m.get("hair", False))
        inp[f"{pfx}_body"] = bool(m.get("body", False))
        inp[f"{pfx}_clothes"] = bool(m.get("clothes", False))
        inp[f"{pfx}_background"] = bool(m.get("background", False))
        inp[f"{pfx}_ignore_area"] = "none"
        inp[f"{pfx}_feather"] = 0
        inp[f"{pfx}_grow"] = 0
        inp[f"{pfx}_start_percent"] = float(e.get("start", 0.0))
        inp[f"{pfx}_end_percent"] = float(e.get("end", 1.0))
    nid = f"{tag}_rlp"
    api[nid] = {"class_type": REFLATENTPLUS_CLASS, "inputs": inp}
    return [nid, 0]


def _options(oi: dict, class_type: str, input_name: str) -> List[str]:
    """The option list for a combo input from a worker's /object_info.

    Handles BOTH object_info shapes: the classic ``[[opt, ...], {config}]`` where
    the options are the first element, AND the newer COMBO form
    ``["COMBO", {"options": [opt, ...], ...}]`` where they live under the config
    dict's ``options`` key (e.g. comfyui-easy-sam3's model input)."""
    for section in ("required", "optional"):
        try:
            spec = oi[class_type]["input"][section][input_name]
        except Exception:  # noqa: BLE001
            continue
        first = spec[0] if isinstance(spec, (list, tuple)) and spec else None
        if isinstance(first, list):
            return [str(o) for o in first]
        if isinstance(spec, (list, tuple)) and len(spec) > 1 and isinstance(spec[1], dict):
            o = spec[1].get("options")
            if isinstance(o, list):
                return [str(x) for x in o]
    return []


def _resolve_name(options: List[str], want: str) -> Optional[str]:
    """Resolve ``want`` against a worker's option list: exact, then basename."""
    if want in options:
        return want
    wb = want.replace("\\", "/").split("/")[-1].lower()
    for o in options:
        if o.replace("\\", "/").split("/")[-1].lower() == wb:
            return o
    return None


def resolve_klein_models(oi: dict, settings: Optional[dict] = None,
                         require_lora: bool = True) -> Dict[str, str]:
    """Resolve the Klein unet/clip/vae + pose LoRA on a worker.

    ``settings`` (studio_vnccs_settings) may override the defaults via
    klein_unet / klein_clip / klein_vae / klein_pose_lora keys.
    Raises ValueError with an actionable message when something is missing.
    """
    st = settings or {}
    unet = _resolve_name(_options(oi, "UNETLoader", "unet_name"),
                         str(st.get("klein_unet") or DEFAULT_KLEIN_UNET))
    clip = _resolve_name(_options(oi, "CLIPLoader", "clip_name"),
                         str(st.get("klein_clip") or DEFAULT_KLEIN_CLIP))
    vae = _resolve_name(_options(oi, "VAELoader", "vae_name"),
                        str(st.get("klein_vae") or DEFAULT_KLEIN_VAE))
    _want_lora = str(st.get("klein_pose_lora") or "").strip()
    if _want_lora.lower() in ("none", "off", "disabled", "0"):
        # v1.153: run WITHOUT a pose LoRA -- native Klein multi-ref pose transfer.
        # The A/B matrix showed the VNCCS LoRA draws black contact lines at full
        # strength; 'none' lets the base model handle the pose purely from the
        # reference + prompt (background/pose adherence may loosen).
        lora = ""
        require_lora = False
        logger.info("klein models: pose LoRA DISABLED (klein_pose_lora=none)")
    else:
        lora = _resolve_name(_options(oi, "LoraLoaderModelOnly", "lora_name"),
                             _want_lora or KLEIN_POSE_LORA_BASENAME)
    missing = [label for label, v in (
        (f"unet ({st.get('klein_unet') or DEFAULT_KLEIN_UNET})", unet),
        (f"clip ({st.get('klein_clip') or DEFAULT_KLEIN_CLIP})", clip),
        (f"vae ({st.get('klein_vae') or DEFAULT_KLEIN_VAE})", vae),
    ) if not v]
    if missing:
        raise ValueError(f"Klein 9B models missing on this worker: {', '.join(missing)}")
    if not lora and require_lora:
        raise ValueError(
            f"Klein pose LoRA '{KLEIN_POSE_LORA_BASENAME}' is not on this worker. "
            f"Download the VNCCS pose pack repository '{KLEIN_POSE_REPO}' with the "
            f"VNCCS Model Manager (vnccs-utils) on that worker, then retry.")
    return {"unet": unet, "clip": clip, "vae": vae, "lora": lora or ""}


def _resolve_realism_lora(oi: dict, settings: Optional[dict]) -> Optional[Dict[str, Any]]:
    """Optional realism LoRA stacked on Klein to push outputs more photoreal
    (anime2real-semi.safetensors). OFF by default; enable via studio setting
    klein_realism_lora='on'. Strength klein_realism_lora_strength (default 1.0,
    clamped 0.0-1.5). Returns {'name','strength'} or None (off / not on worker)."""
    st = settings or {}
    mode = str(st.get("klein_realism_lora") or "off").strip().lower()
    if mode in ("off", "false", "0", "no", "disabled", "none", ""):
        return None
    name = _resolve_name(_options(oi, "LoraLoaderModelOnly", "lora_name"),
                         str(st.get("klein_realism_lora_name") or REALISM_LORA_BASENAME))
    if not name:
        return None
    try:
        strength = float(st.get("klein_realism_lora_strength") or 1.0)
    except Exception:  # noqa: BLE001
        strength = 1.0
    return {"name": name, "strength": max(0.0, min(1.5, strength))}


def _apply_realism_lora(api: Dict[str, dict], models: Dict[str, Any], model_ref: list) -> list:
    """Stack the optional realism LoRA (models['realism_lora']) onto ``model_ref``.
    No-op when absent. Shared by every Klein graph that renders the character."""
    r = (models or {}).get("realism_lora")
    if not r:
        return model_ref
    api["rlora"] = {"class_type": "LoraLoaderModelOnly",
                    "inputs": {"model": list(model_ref), "lora_name": r["name"],
                               "strength_model": float(r.get("strength", 1.0))}}
    return ["rlora", 0]

def resolve_sam3_cleanup(oi: dict, settings: Optional[dict] = None) -> Optional[Dict[str, Any]]:
    """Optional SAM3 article-cleanup config, or None.  Segments leftover
    clothing/jewelry by TEXT and inpaints just those regions to bare skin on the
    base, so Strip release can stay HIGH (max likeness) while stubborn articles are
    removed surgically.  OFF by default; enable via klein_sam_cleanup='on'.
    Needs the comfyui-easy-sam3 nodes on the worker (else None = graceful skip)."""
    st = settings or {}
    mode = str(st.get("klein_sam_cleanup") or "off").strip().lower()
    if mode in ("off", "false", "0", "no", "disabled", "none", ""):
        return None
    if SAM3_SEG_CLASS not in (oi or {}) or SAM3_LOADER_CLASS not in (oi or {}):
        return None
    opts = _options(oi, SAM3_LOADER_CLASS, "model")
    model = (_resolve_name(opts, str(st.get("klein_sam_cleanup_model") or SAM3_DEFAULT_MODEL))
             or (opts[0] if opts else SAM3_DEFAULT_MODEL))
    prompt = str(st.get("klein_sam_cleanup_prompt") or "").strip() or SAM3_DEFAULT_ARTICLES
    try:
        thr = float(st.get("klein_sam_cleanup_threshold") or 0.4)
    except Exception:  # noqa: BLE001
        thr = 0.4
    thr = max(0.05, min(0.95, thr))
    return {"model": model, "prompt": prompt, "threshold": thr,
            "grow": "GrowMaskWithBlur" in (oi or {}), "expand": 6, "blur": 4.0}


def _inject_sam3_cleanup(api: Dict[str, dict], image_node: str, model_ref: list,
                         sam: Dict[str, Any], seed: int, positive: str, negative: str,
                         steps: int, width: int, height: int) -> str:
    """Segment leftover articles on ``image_node`` by TEXT (SAM3), inpaint just
    those regions to bare skin/underwear, and composite back so ONLY the flagged
    articles change (likeness elsewhere is byte-identical).  Reuses the graph's
    Klein clip/vae ('c'/'v') + ``model_ref``.  Returns the composited image id."""
    api["sam_m"] = {"class_type": SAM3_LOADER_CLASS,
                    "inputs": {"model": sam["model"], "segmentor": "image",
                               "device": "cuda", "precision": "fp16"}}
    api["sam_seg"] = {"class_type": SAM3_SEG_CLASS,
                      "inputs": {"sam3_model": ["sam_m", 0], "images": [image_node, 0],
                                 "prompt": sam["prompt"], "threshold": float(sam["threshold"]),
                                 "keep_model_loaded": False, "add_background": "none",
                                 "detection_limit": -1}}
    mask_ref: list = ["sam_seg", 0]
    if sam.get("grow"):
        api["sam_grow"] = {"class_type": "GrowMaskWithBlur",
                           "inputs": {"mask": mask_ref, "expand": int(sam.get("expand", 6)),
                                      "incremental_expandrate": 0.0, "tapered_corners": True,
                                      "flip_input": False, "blur_radius": float(sam.get("blur", 4.0)),
                                      "lerp_alpha": 1.0, "decay_factor": 1.0, "fill_holes": True}}
        mask_ref = ["sam_grow", 0]
    api["ci_enc"] = {"class_type": "VAEEncode", "inputs": {"pixels": [image_node, 0], "vae": ["v", 0]}}
    api["ci_mask"] = {"class_type": "SetLatentNoiseMask",
                      "inputs": {"samples": ["ci_enc", 0], "mask": mask_ref}}
    api["ci_pos"] = {"class_type": "CLIPTextEncode", "inputs": {"text": positive, "clip": ["c", 0]}}
    api["ci_neg"] = {"class_type": "CLIPTextEncode", "inputs": {"text": negative, "clip": ["c", 0]}}
    api["ci_sig"] = {"class_type": "Flux2Scheduler",
                     "inputs": {"steps": int(steps), "width": int(width), "height": int(height)}}
    api["ci_gd"] = {"class_type": "CFGGuider",
                    "inputs": {"model": list(model_ref), "positive": ["ci_pos", 0],
                               "negative": ["ci_neg", 0], "cfg": KLEIN_POSE_CFG}}
    api["ci_ns"] = {"class_type": "RandomNoise", "inputs": {"noise_seed": int(seed) + 7}}
    api["ci_sm"] = {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "euler"}}
    api["ci_sc"] = {"class_type": "SamplerCustomAdvanced",
                    "inputs": {"noise": ["ci_ns", 0], "guider": ["ci_gd", 0], "sampler": ["ci_sm", 0],
                               "sigmas": ["ci_sig", 0], "latent_image": ["ci_mask", 0]}}
    api["ci_dec"] = {"class_type": "VAEDecode", "inputs": {"samples": ["ci_sc", 0], "vae": ["v", 0]}}
    api["ci_comp"] = {"class_type": "ImageCompositeMasked",
                      "inputs": {"destination": [image_node, 0], "source": ["ci_dec", 0],
                                 "mask": mask_ref, "x": 0, "y": 0, "resize_source": False}}
    return "ci_comp"


def resolve_face_refine(oi: dict, settings: Optional[dict] = None) -> Optional[Dict[str, Any]]:
    """Light face detail/corrector config for a worker, or None.

    Uses Impact-Pack's FaceDetailer as a LOW-DENOISE face refine pass on each
    generated pose sprite (VNCCS itself face-details every emotion this way).
    Denoise (default 0.55, matching VNCCS) rebuilds real face detail; eye
    geometry and small-face artifacts get corrected.

    studio settings keys (all optional):
      klein_face_refine          'auto' (default: use when FaceDetailer + a
                                 face detector model are on the worker) | 'off'
      klein_face_refine_denoise  float, default 0.55 (clamped 0.10-0.80)
      klein_face_refine_steps    int, default 6
    """
    st = settings or {}
    mode = str(st.get("klein_face_refine") or "auto").strip().lower()
    if mode in ("off", "false", "0", "disabled", "none"):
        return None
    if "FaceDetailer" not in (oi or {}) or "UltralyticsDetectorProvider" not in (oi or {}):
        return None
    dets = _options(oi, "UltralyticsDetectorProvider", "model_name")
    face_dets = [d for d in dets if "face" in d.lower()]
    if not face_dets:
        return None
    detector = next((d for d in face_dets if "yolov8m" in d.lower()), face_dets[0])
    try:
        denoise = float(st.get("klein_face_refine_denoise") or 0.55)
    except Exception:  # noqa: BLE001
        denoise = 0.55
    denoise = max(0.10, min(0.80, denoise))
    try:
        steps = int(st.get("klein_face_refine_steps") or 6)
    except Exception:  # noqa: BLE001
        steps = 6
    fd_inputs: set = set()
    for section in ("required", "optional"):
        try:
            fd_inputs |= set((oi["FaceDetailer"]["input"].get(section) or {}).keys())
        except Exception:  # noqa: BLE001
            continue
    # Refine GUIDE size: the face crop is enlarged so the detected bbox reaches
    # this many pixels, regenerated, then shrunk BACK into place.  The bigger the
    # round-trip ratio, the worse the shrink-back aliasing on dense skin texture
    # (freckles -> horizontal "VCR scan lines"; pose faces are smaller so their
    # ratio -- and the striping -- is worse).  Default dropped 1536 -> 768: about
    # half the enlargement, kills most of the moire while keeping the eye/face
    # cleanup.  Raise it only if faces look under-detailed; lower to 512 if any
    # striping survives.  Studio setting klein_face_refine_guide (384-2048).
    try:
        guide = int(st.get("klein_face_refine_guide") or 768)
    except Exception:  # noqa: BLE001
        guide = 768
    guide = max(384, min(2048, guide))
    return {"detector": detector, "denoise": denoise, "steps": max(2, min(32, steps)),
            "guide_size": guide, "max_size": guide, "fd_inputs": fd_inputs}


def _face_refine_node(api: Dict[str, dict], node_id: str, image_ref: list,
                      model_ref: list, pos_ref: list, neg_ref: list,
                      seed: int, refine: Dict[str, Any]) -> str:
    """Append a FaceDetailer refine node; returns its node id.  Inputs are
    FILTERED against the worker's actual FaceDetailer input names, so Impact
    version drift (added/removed widgets) can't fail prompt validation."""
    api.setdefault("fr_det", {"class_type": "UltralyticsDetectorProvider",
                              "inputs": {"model_name": refine["detector"]}})
    wanted: Dict[str, Any] = {
        "image": image_ref, "model": model_ref, "clip": ["c", 0], "vae": ["v", 0],
        "positive": pos_ref, "negative": neg_ref, "bbox_detector": ["fr_det", 0],
        "wildcard": "", "guide_size": int(refine.get("guide_size", 1536)),
        "guide_size_for": True, "max_size": int(refine.get("max_size", 1536)),
        "seed": int(seed), "steps": int(refine["steps"]), "cfg": KLEIN_POSE_CFG,
        "sampler_name": "euler", "scheduler": "simple",
        "denoise": float(refine["denoise"]), "feather": 5, "noise_mask": True,
        "force_inpaint": True, "bbox_threshold": 0.5, "bbox_dilation": 10,
        "bbox_crop_factor": 3.0, "sam_detection_hint": "center-1",
        "sam_dilation": 0, "sam_threshold": 0.93, "sam_bbox_expansion": 0,
        "sam_mask_hint_threshold": 0.7, "sam_mask_hint_use_negative": "False",
        "drop_size": 10, "cycle": 1, "inpaint_model": False, "noise_mask_feather": 20,
    }
    fd_inputs = refine.get("fd_inputs") or set()
    inputs = ({k: v for k, v in wanted.items() if k in fd_inputs}
              if fd_inputs else wanted)
    api[node_id] = {"class_type": "FaceDetailer", "inputs": inputs}
    return node_id


def decode_capture(b64_or_data_url: str) -> bytes:
    """PNG bytes from a pose_render capture (data URL or bare base64)."""
    return base64.b64decode(b64_or_data_url.split(",", 1)[-1])


def _keep_clothing(base_clothing: Optional[str]) -> bool:
    """True = clone the reference's outfit onto the base.  False (default) =
    STRIP clothing and render a neutral underwear/nude BASE body -- the VNCCS
    Native philosophy: bases are body-only so the Clothes/Emotions modes dress
    them freely.  Enable cloning via studio setting klein_base_clothing='keep'."""
    return str(base_clothing or "strip").strip().lower() in (
        "keep", "clone", "reference", "on", "true", "1", "yes")


def _base_body_state(nsfw: bool, sex: str = "") -> str:
    """State of dress for a STRIPPED base body -- mirrors VNCCS's OWN base-model
    underwear phrasing (character_creator_v2.py:1167: "wear white bra and panties"
    / "bare chest, wear white boxers").  A SPECIFIC white bra+panties anchors the
    model far better than vague "plain underwear" (Klein was drifting to topless).
    NSFW = nude."""
    male = str(sex or "").strip().lower() in ("male", "man", "boy", "m", "masculine")
    # foot posture + body-weight preservation shared by every case: after removing
    # shoes the feet must sit FLAT (heels came off -> don't keep the arched tiptoe
    # stance), and the reference's real weight/build must be kept, not slimmed.
    _flat_feet = ("The feet are flat on the ground in a natural flat-footed stance -- "
                  "soles fully flat, heels down on the floor, NOT on tiptoe, NOT on the "
                  "balls of the feet, NOT arched as if still wearing high heels. ")
    _keep_weight = ("Keep the character's EXACT body from the reference -- same overall "
                    "build, body fat and weight, belly, hips, thighs and limb thickness. "
                    "If the reference is heavy-set, plus-size or has a fuller figure, KEEP "
                    "that exact fuller body -- do NOT slim, tone, shrink, idealize or "
                    "beautify it into a thinner or generic model figure. ")
    if nsfw:
        return ("The character is fully NUDE -- naked, no clothing, underwear, "
                "footwear or accessories of any kind. " + _flat_feet + _keep_weight)
    if male:
        return ("REMOVE every garment the reference is wearing. The character is "
                "bare-chested and wears ONLY plain WHITE boxers and nothing else. The "
                "feet are COMPLETELY BARE and barefoot -- NO shoes, sandals, socks or "
                "shoe soles, nothing under the feet. " + _flat_feet + "NO jewelry of any "
                "kind -- remove every necklace, chain, bracelet, ring, watch and "
                "piercing. No other clothing, footwear or accessories. " + _keep_weight + "SFW.")
    return ("REMOVE every garment the reference is wearing. The character wears ONLY "
            "a plain WHITE STRAPLESS bra (a bandeau, NO shoulder straps) and plain "
            "WHITE panties -- simple white underwear. The strapless bra COVERS the "
            "chest and breasts (the character is NOT topless and NOT nude; only the "
            "shoulders, arms, midriff and legs are bare). The feet are COMPLETELY BARE "
            "and barefoot -- NO shoes, sandals, heels, socks, shoe soles or platforms, "
            "nothing under the feet. " + _flat_feet + "NO jewelry of any kind -- remove "
            "every earring, necklace, chain, bracelet, ring, anklet, watch and piercing. "
            "Nothing else at all besides the white bra and panties -- no other clothing, "
            "footwear or accessories. " + _keep_weight + "SFW.")


# Destination render styles that Klein/Flux.2 handles well.  Keys are the values
# the UI dropdown sends; each maps to a strong style directive so the OUTPUT looks
# the way the user wants regardless of the reference photos' own style.
_STYLIZED_STYLES = frozenset({
    "anime", "manga", "comic", "western-comic", "comic-book", "cartoon", "toon",
    "3d", "3d-render", "cgi", "pixar", "painting", "digital-painting",
    "illustration", "painterly", "concept-art",
})


def _style_is_stylized(style_kind: Optional[str]) -> bool:
    """True when the destination style is an illustrated/CGI look (so PuLID, which
    injects photoreal face geometry, should be skipped to avoid fighting it)."""
    return str(style_kind or "").strip().lower() in _STYLIZED_STYLES


def _style_directive(style_kind: Optional[str], style_custom: str = "") -> str:
    """Overall render-style directive for the OUTPUT.  Driven by the "Output style"
    dropdown so a clone renders photoreal / anime / manga / 3D / etc. as chosen,
    instead of drifting to a generic look.  'auto' (default) adds nothing and lets
    the references decide.  'custom' passes the user's own text verbatim."""
    fk = str(style_kind or "").strip().lower()
    cust = str(style_custom or "").strip()
    if fk in ("custom", "other") and cust:
        return cust if cust.endswith((".", "!", "?")) else cust + "."
    if fk in ("photorealistic", "realistic", "photoreal", "photo"):
        return ("Photorealistic, shot on a real camera: natural skin with visible "
                "pores and fine texture, realistic subsurface skin shading, true-to-"
                "life proportions and lighting. A real photograph -- NOT an "
                "illustration, painting, anime or 3D render.")
    if fk in ("semi-realistic", "semi_realistic", "semireal", "stylized-realistic"):
        return ("Semi-realistic render: mostly photoreal skin and proportions with a "
                "subtle cinematic/painterly polish; still grounded and lifelike, not "
                "full anime or cartoon.")
    if fk == "anime":
        return ("Anime style: clean cel-shaded 2D anime illustration, expressive eyes, "
                "crisp line art and flat anime shading (modern anime key-visual look).")
    if fk == "manga":
        return ("Manga style: black-and-white manga illustration, inked line art with "
                "screentone shading and hatching, high-contrast monochrome, no color.")
    if fk in ("comic", "western-comic", "comic-book"):
        return ("Western comic-book style: bold inked outlines, dynamic cel shading and "
                "halftone / ben-day dot shading, saturated comic colors.")
    if fk in ("cartoon", "toon"):
        return ("Western cartoon style: clean flat vector-like shapes, simple flat "
                "shading and bold outlines (modern animated-series look).")
    if fk in ("3d", "3d-render", "cgi", "pixar"):
        return ("Stylized 3D render: polished CGI character with soft global-"
                "illumination lighting and subtle subsurface scattering "
                "(Pixar / Blender feature-film look).")
    if fk in ("painting", "digital-painting", "illustration", "painterly", "concept-art"):
        return ("Digital painting / concept-art illustration: painterly brushwork, rich "
                "rendered lighting and soft edges (semi-realistic illustrated look).")
    return ""


def klein_pose_prompt(pose_prompt: str, background: str, n_identity: int = 1,
                      face_image_index: Optional[int] = None,
                      details: Optional[str] = None,
                      base_clothing: str = "strip", nsfw: bool = False,
                      appearance: Optional[str] = None,
                      style_kind: Optional[str] = None, sex: str = "",
                      body_ref_active: bool = False, style_custom: str = "",
                      consistent_skin: bool = False,
                      pose_input: str = "mannequin") -> str:
    """Klein-style instruction: image 1 = pose reference, images 2..N = identity,
    optionally image ``face_image_index`` = a close-up crop of the SAME face.

    ``base_clothing`` sets the OUTFIT policy for the base pose set:
      'strip' (default) -- take identity/face/body only and render a neutral
              UNDERWEAR (or nude when ``nsfw``) base body, IGNORING whatever the
              references wear.  Mirrors VNCCS Native bases: a clean body the
              Clothes/Emotions modes dress later.
      'keep'  -- clone the reference outfit exactly, filling gaps from
              ``details`` (Analyze-Reference) and never INVENTING items.

    Klein binds references better when the prompt states they are the same
    person (community "character sheet" finding) -- so when a face crop rides
    along, say so explicitly and demand an exact facial match.
    """
    who = ("the character" if n_identity <= 0
           else "the character from image 2" if n_identity == 1
           else f"the character shown in images 2-{n_identity + 1}")
    # STYLE GUARD (v1.150): Klein reference latents transfer STYLE from every
    # reference (documented Flux.2 "style blending"), so say explicitly what
    # image 1 is and that ONLY its pose may be taken -- otherwise the CGI
    # mannequin's plastic material and drawn outlines bleed into the skin.
    if str(pose_input or "").lower() == "skeleton":
        guard = ("Image 1 is a stick-figure pose skeleton diagram on a black "
                 "background: use it ONLY to read the body position and camera "
                 "framing. Nothing of image 1's appearance may show in the "
                 "output. ")
    else:
        guard = ("Image 1 is a plain 3D computer mannequin: use it ONLY to read "
                 "the body position and camera framing. Do NOT copy image 1's "
                 "rendering style, gray plastic material, CGI shading or drawn "
                 "outlines -- the output must look like a real photograph of the "
                 "character, matching the photographic style of the character "
                 "reference image(s). ")
    if _keep_clothing(base_clothing):
        parts = [guard + f"Apply the pose from image 1 to {who}, keeping the character's "
                 "identity, proportions and ENTIRE outfit exactly. Reproduce every "
                 "clothing item, footwear, stockings, straps and accessory exactly "
                 "as shown in the reference -- same garments, same colors, same "
                 "materials. Match the reference's state of dress EXACTLY: if the "
                 "character is barefoot keep the feet bare and add NO footwear or "
                 "socks; if any part of the body is bare or unclothed in the "
                 "reference keep it bare. Do NOT invent, add or remove any shoes, "
                 "boots, socks, stockings, straps, jewelry, garment or accessory "
                 "that is not clearly visible in the reference."]
    else:
        app = str(appearance or "").strip()
        parts = [guard + f"Apply the pose from image 1 to {who}. Keep the character's FACE, "
                 "hair, skin tone, eye colour and facial identity EXACTLY from the "
                 "reference images -- it is the SAME person. Change ONLY the clothing: "
                 + _base_body_state(nsfw, sex) + "."]
        if body_ref_active:
            # A masked BODY reference is riding along (ReferenceLatentPlus), so the
            # PHOTO now leads the body -- the Body-Helper text only fills gaps the
            # reference can't show (e.g. legs/height when the photo is a bust crop).
            base = ("The reference images show the character's ACTUAL body -- match "
                    "their build, shoulders, chest, waist, hips and overall body "
                    "proportions and height as closely as possible from the reference.")
            if app:
                base += (" Use these notes only to fill in what the references do not "
                         "clearly show (e.g. legs or full height): " + app + ".")
            parts.append(base)
        elif app:
            # explicit build/height from the Body Helper is AUTHORITATIVE for the
            # body -- the reference (often a bust/head crop) can't show legs or
            # height, and the old "keep exact proportions from the reference"
            # instruction was overriding these descriptors entirely.
            parts.append(
                "The character's body build, proportions and height MUST be: " + app
                + ". Render the full body head-to-toe with exactly this build and "
                "these proportions -- the reference images may show only the head or "
                "upper body, so use THESE descriptors (not a generic default body) "
                "for the torso, hips, legs and overall height.")
        else:
            parts.append("Keep the body build, figure and proportions consistent with "
                         "the reference.")
        parts.append("Use natural, anatomically-correct human proportions -- the head "
                     "must be sized PROPORTIONALLY to the body, not oversized.")
    parts.append(
        "Where the pose makes skin touch or overlap other skin -- a hand resting "
        "on the body, an arm pressed against the torso, crossed legs -- blend the "
        "contact naturally with soft shading and a subtle soft contact shadow, "
        "exactly like a photograph. Absolutely NO dark outlines, NO black contour "
        "lines and NO drawn ink-like edges between touching skin surfaces.")
    if consistent_skin:
        parts.append(
            "Keep the skin tone, complexion and overall colour grading IDENTICAL to "
            "the reference across every image in this set -- the exact same skin "
            "colour and undertone, the same even neutral lighting and white balance, "
            "with NO colour tint, warmth/coolness shift or exposure change from one "
            "pose to the next.")
    style = _style_directive(style_kind, style_custom)
    if style:
        parts.append(style)
    if face_image_index:
        parts.append(
            f"Image {face_image_index} is a close-up of the same character's face: "
            f"all images show ONE person. The generated face must match image "
            f"{face_image_index} exactly -- identical facial features, eye color, "
            "hairstyle and art style.")
    pp = str(pose_prompt or "").strip()
    if pp:
        parts.append(pp if pp.endswith(".") else pp + ".")
    # outfit detail text (Analyze-Reference) only helps the KEEP path; in STRIP
    # mode it could re-introduce clothing, so it is intentionally omitted there.
    det = str(details or "").strip()
    if det and _keep_clothing(base_clothing):
        parts.append(f"Reference outfit details to match (do not invent beyond these): {det}.")
    bg = str(background or "Green").strip() or "Green"
    # The shadow ban is scoped to the BACKGROUND only (v1.149).  The old wording
    # ("absolutely no shadows of any kind ... do not reproduce any shading")
    # forbade the soft contact shading a body needs where skin presses against
    # skin -- so the model separated touching limbs with hard BLACK LINES
    # instead.  Keying only needs the background/floor clean.
    parts.append(f"Solid flat {bg.lower()} background, evenly and uniformly lit. "
                 "The BACKGROUND must stay perfectly clean for keying: no cast "
                 "shadow, drop shadow or ground shadow falling ON THE BACKGROUND "
                 "or beneath the feet, and no ground or floor plane. The BODY "
                 "itself keeps natural soft photographic form shading and soft "
                 "contact shadows where limbs touch -- it must NOT look flat or "
                 "cel-shaded. Use even, neutral studio lighting; do not copy any "
                 "dramatic lighting from the reference images.")
    return " ".join(parts)


def _height_phrase(height) -> str:
    """Turn a height string (cm, ft'in, or inches) into a build descriptor the
    model can actually use -- diffusion can't render an exact number, but
    relative stature ("tall", "petite") shifts proportions.  A bare descriptor
    passes through unchanged; the raw number stays only in metadata."""
    import re
    h = str(height or "").strip()
    if not h:
        return ""
    cm = None
    m = re.search(r"(\d+(?:\.\d+)?)\s*cm", h.lower())
    if m:
        cm = float(m.group(1))
    else:
        fm = re.search(r"(\d+)\s*(?:'|ft|feet)\s*(\d+(?:\.\d+)?)?", h.lower())
        if fm:
            inches = int(fm.group(1)) * 12 + (float(fm.group(2)) if fm.group(2) else 0.0)
            cm = inches * 2.54
        else:
            im = re.search(r'(\d+(?:\.\d+)?)\s*(?:in\b|inch|")', h.lower())
            if im:
                cm = float(im.group(1)) * 2.54
    if cm and cm > 0:
        if cm < 150:
            return "petite, short stature, small frame"
        if cm < 163:
            return "slightly short, average-to-petite height"
        if cm < 173:
            return "average height"
        if cm < 183:
            return "tall, long-limbed"
        return "very tall, long legs and limbs"
    return h


def _height_to_cm(height) -> Optional[float]:
    """Parse a height string (cm / ft'in / inches) to centimetres, or None."""
    import re
    h = str(height or "").strip().lower()
    if not h:
        return None
    m = re.search(r"(\d+(?:\.\d+)?)\s*cm", h)
    if m:
        return float(m.group(1))
    fm = re.search(r"(\d+)\s*(?:'|ft|feet)\s*(\d+(?:\.\d+)?)?", h)
    if fm:
        inches = int(fm.group(1)) * 12 + (float(fm.group(2)) if fm.group(2) else 0.0)
        return inches * 2.54
    im = re.search(r'(\d+(?:\.\d+)?)\s*(?:in\b|inch|")', h)
    if im:
        return float(im.group(1)) * 2.54
    return None


def body_mesh_params(character_info: dict) -> Dict[str, float]:
    """Map the character's Body-Helper descriptors (sex / body tags / height /
    age) to the POSE-MANNEQUIN's MakeHuman morph sliders (all 0..1) so the pose
    reference shows the RIGHT build.  Without this the mannequin is always the
    vendored default (average) body, and Klein — which reads the mannequin figure
    as the body to render — reproduces that same body for every character.

    MakeHuman conventions: gender 0=female / 1=male; weight 0=thin / 1=heavy;
    muscle 0=soft / 1=very muscular; height 0=short / 1=tall.  Values not implied
    by the descriptors are left at the mannequin's baseline (0.5)."""
    ci = character_info or {}
    body = str(ci.get("body") or "").lower()
    extra = str(ci.get("additional_details") or "").lower()
    txt = f"{body} {extra}"
    sex = str(ci.get("sex") or "").strip().lower()
    m: Dict[str, float] = {}

    m["gender"] = 1.0 if sex.startswith("m") else 0.0
    try:
        m["age"] = float(ci.get("age") or 25)
    except Exception:  # noqa: BLE001
        m["age"] = 25.0

    def _has(*keys) -> bool:
        return any(k in txt for k in keys)

    # weight / body fat
    if _has("obese", "very overweight", "morbidly"):
        m["weight"] = 0.95
    elif _has("overweight", "chubby", "plump", "heavy", "fat", "thick", "heavyset",
              "stocky", "husky", "chunky", "pudgy", "full-figured", "plus size",
              "plus-size", "big", "large body", "wide"):
        m["weight"] = 0.82
    elif _has("curvy", "voluptuous", "chubby-cute", "soft body", "rubenesque"):
        m["weight"] = 0.68
    elif _has("skinny", "very thin", "emaciated", "bony", "waifish"):
        m["weight"] = 0.18
    elif _has("slim", "slender", "thin", "lean", "lithe", "svelte", "willowy", "slight"):
        m["weight"] = 0.3
    else:
        m["weight"] = 0.5

    # muscle tone
    if _has("bodybuilder", "ripped", "very muscular", "hulking", "brawny", "buff"):
        m["muscle"] = 0.9
    elif _has("muscular", "athletic", "toned", "fit", "muscled", "strong",
              "broad shoulders", "chiseled", "defined"):
        m["muscle"] = 0.75
    elif _has("soft", "untoned", "doughy", "flabby", "out of shape"):
        m["muscle"] = 0.3
    else:
        m["muscle"] = 0.5

    # height (prefer a real cm/ft value, else descriptors)
    cm = _height_to_cm(ci.get("height"))
    if cm is not None and cm > 0:
        if cm < 150:
            m["height"] = 0.12
        elif cm < 160:
            m["height"] = 0.3
        elif cm < 170:
            m["height"] = 0.45
        elif cm < 178:
            m["height"] = 0.6
        elif cm < 186:
            m["height"] = 0.78
        else:
            m["height"] = 0.92
    elif _has("petite", "short", "tiny", "diminutive"):
        m["height"] = 0.22
    elif _has("very tall", "towering", "statuesque"):
        m["height"] = 0.85
    elif _has("tall", "long-limbed", "long legs"):
        m["height"] = 0.75

    # breast size for female builds
    if not sex.startswith("m"):
        if _has("busty", "large breasts", "big breasts", "voluptuous", "buxom", "curvy"):
            m["breast_size"] = 0.72
        elif _has("flat", "small breasts", "flat-chested", "petite"):
            m["breast_size"] = 0.3

    # EXPLICIT slider overrides (0..100 from the UI) win over the text derivation —
    # the direct way to dial the mannequin to match a reference body.
    for src, key in (("body_weight", "weight"), ("body_muscle", "muscle"),
                     ("body_height", "height"), ("body_breast", "breast_size")):
        v = ci.get(src)
        if v is None or str(v).strip() == "":
            continue
        try:
            m[key] = max(0.0, min(1.0, float(v) / 100.0))
        except Exception:  # noqa: BLE001
            pass
    return m


def klein_identity_text(character_info: dict) -> str:
    """Physical identity descriptor (NO clothing) for STRIP-mode pose prompts.
    When the clothed full-body reference is withheld (so its outfit can't leak
    onto the base body), hair/skin/eyes/face/build ride in the prompt text
    instead — face itself is still carried by the face crop + PuLID."""
    ci = character_info or {}
    bits = []
    for key in ("skin_color", "hair", "eyes", "face", "body"):
        v = str(ci.get(key) or "").strip()
        if v:
            bits.append(v)
    hp = _height_phrase(ci.get("height"))
    if hp:
        bits.append(hp)
    return ", ".join(bits)


def klein_body_text(character_info: dict) -> str:
    """Body-ONLY descriptor (build + height) for the STRIP base prompt, kept
    separate from face/hair identity (which comes from the reference) so the
    Body Helper's build/height actually drives the rendered body proportions."""
    ci = character_info or {}
    bits = []
    v = str(ci.get("body") or "").strip()
    if v:
        bits.append(v)
    hp = _height_phrase(ci.get("height"))
    if hp:
        bits.append(hp)
    return ", ".join(bits)


def klein_detail_text(character_info: dict) -> str:
    """Extra appearance/clothing anchor for pose prompts: the character's
    additional-details tags (clothing, accessories, distinguishing marks).
    Wizard-made base characters carry no dedicated clothing field, but cloned
    characters store 'wearing ...' style tags here -- a weak textual backstop
    for the reference image when small attire items (footwear, straps) drift."""
    ci = character_info or {}
    return str(ci.get("additional_details") or "").strip()


def build_klein_pose_graph(
    *,
    pose_files: List[str],
    identity_files: List[str],
    prompts: List[str],
    seed: int,
    models: Dict[str, str],
    steps: int = KLEIN_POSE_STEPS,
    filename_prefix: str = "rbmn_vnccs/klein",
    upscale_model: Optional[str] = None,
    upscale_megapixels: Optional[float] = None,
    face_file: Optional[str] = None,
    pulid: Optional[Dict[str, Any]] = None,
    face_refine: Optional[Dict[str, Any]] = None,
    strip_body_refs: bool = False,
    face_as_reference: bool = True,
    negative_prompt: str = "",
    cfg: Optional[float] = None,
    rmbg: Optional[Dict[str, Any]] = None,
    face_refine_first_only: bool = False,
    body_files: Optional[List[str]] = None,
    reflatentplus: Optional[Dict[str, Any]] = None,
    out_width: Optional[int] = None,
    out_height: Optional[int] = None,
    consistent_seed: bool = False,
    pose_ref_end: Optional[float] = None,
    dwpose: Optional[Dict[str, Any]] = None,
    pose_lora_strength: float = 1.0,
    consistency_lora: Optional[Dict[str, Any]] = None,
) -> Tuple[Dict[str, dict], Dict[str, str]]:
    """API-format graph: one Klein9b-Encoder chain per pose, batched into a
    single SaveImage tap.  Returns (api_graph, tap_map={'sprites': save_id}).

    ``identity_files`` may hold 1-4 images (clone mode feeds the raw references
    directly — Klein's native multi-ref replaces the Qwen source-grid trick).
    ``face_file`` (optional) is an uploaded CLOSE-UP CROP of the identity face,
    appended as the LAST reference — the full-body identity lands at ~1MP where
    the face is a few dozen pixels, so a dedicated face reference is what
    actually carries facial identity (v1.77.0 face-consistency wave).
    ``pulid`` (from resolve_pulid) additionally patches the model with
    PuLID-Flux2 identity guidance using ``face_file`` (or the first identity).
    ``upscale_model`` bolts an ImageUpscaleWithModel (+ downscale to
    ``upscale_megapixels``) onto each pose's tail before saving.

    ``consistent_seed`` (the Consistent-skin toggle): every pose in the graph
    samples from the SAME noise seed instead of seed+i, so the set can't drift
    in complexion/exposure pose-to-pose.  (v1.136 only synced seeds BETWEEN
    parallel batches; within a graph each pose still rolled seed+i -- this is
    the missing half.)

    ``pose_ref_end`` (0-1, None/1.0 = off): RELEASE the pose-mannequin reference
    at this fraction of sampling.  The mannequin is a flat-shaded CGI render;
    held as a reference for the WHOLE run its plastic, contour-lined texture
    bleeds into the final skin (etched/waxy look on realistic characters -- the
    base preview never shows it because its references are real photos).  The
    pose/composition locks in the EARLY steps, so releasing at ~0.85 keeps the
    pose while the late texture-forming steps see only the identity refs.
    Implemented with ConditioningSetTimestepRange + ConditioningCombine (core
    nodes): with-pose conditioning covers 0..end, without-pose covers end..1."""
    if not pose_files:
        raise ValueError("Klein pose graph needs at least one pose image")
    if len(prompts) != len(pose_files):
        raise ValueError("prompts and pose_files must align")
    _rlp_active = bool(reflatentplus and body_files)
    if not identity_files and not _rlp_active:
        raise ValueError("Klein pose graph needs at least one identity or body image")
    identity_files = (identity_files or [])[:4]
    body_files = (body_files or [])[:4]
    _cfg = float(cfg) if cfg else KLEIN_POSE_CFG

    api: Dict[str, dict] = {
        "u": {"class_type": "UNETLoader",
              "inputs": {"unet_name": models["unet"], "weight_dtype": "default"}},
        "c": {"class_type": "CLIPLoader",
              "inputs": {"clip_name": models["clip"], "type": "flux2", "device": "default"}},
        "v": {"class_type": "VAELoader", "inputs": {"vae_name": models["vae"]}},
    }
    if models.get("lora"):
        api["lora"] = {"class_type": "LoraLoaderModelOnly",
                       "inputs": {"model": ["u", 0], "lora_name": models["lora"],
                                  # v1.153: tunable -- at 1.0 the VNCCS pose LoRA
                                  # imposes its trained style hard enough to draw
                                  # black contact lines on realistic skin; 0.6-0.8
                                  # keeps the pose while weakening the style stamp.
                                  "strength_model": float(pose_lora_strength or 1.0)}}
    id_encs: List[str] = []
    # STRIP base mode withholds the full-body (clothed) reference latents so the
    # reference's outfit (e.g. a strappy dress) can't leak onto the underwear/
    # nude base — identity then rides on the face crop + PuLID + appearance text.
    if not strip_body_refs:
        for k, idf in enumerate(identity_files):
            api[f"id{k}_load"] = {"class_type": "LoadImage", "inputs": {"image": idf}}
            api[f"id{k}_scale"] = {"class_type": "ImageScaleToTotalPixels",
                                   "inputs": {"image": [f"id{k}_load", 0],
                                              "upscale_method": "lanczos", "megapixels": 1.0,
                                              "resolution_steps": 1}}
            api[f"id{k}_enc"] = {"class_type": "VAEEncode",
                                 "inputs": {"pixels": [f"id{k}_scale", 0], "vae": ["v", 0]}}
            id_encs.append(f"id{k}_enc")
    if face_file and face_as_reference:
        # face crop rides as the final reference; scaled like the others (it is
        # already a crop, so at 1MP the face detail vastly exceeds the full-body ref).
        # STRIP mode with PuLID drops even this (face_as_reference=False) so NO
        # clothed pixels are referenced at all -- PuLID carries the face instead.
        api["face_load"] = {"class_type": "LoadImage", "inputs": {"image": face_file}}
        api["face_scale"] = {"class_type": "ImageScaleToTotalPixels",
                             "inputs": {"image": ["face_load", 0],
                                        "upscale_method": "lanczos", "megapixels": 1.0,
                                        "resolution_steps": 1}}
        api["face_enc"] = {"class_type": "VAEEncode",
                           "inputs": {"pixels": ["face_scale", 0], "vae": ["v", 0]}}
        id_encs.append("face_enc")

    # BODY channel: shared LoadImage nodes for the body/full references that ride
    # through ReferenceLatentPlus (garment masked out).  Created once, reused by
    # every pose's pos+neg chain.  ReferenceLatentPlus VAE-encodes internally, so
    # these feed IMAGE (not a pre-encoded latent) unlike the identity refs above.
    body_load_ids: List[str] = []
    if _rlp_active:
        for k, bf in enumerate(body_files):
            bid = f"body{k}_load"
            api[bid] = {"class_type": "LoadImage", "inputs": {"image": bf}}
            body_load_ids.append(bid)

    model_ref = ["lora", 0] if models.get("lora") else ["u", 0]
    if consistency_lora:
        # dx8152 Consistency LoRA stacked AFTER the pose LoRA (triggerless).
        api["clora"] = {"class_type": "LoraLoaderModelOnly",
                        "inputs": {"model": list(model_ref),
                                   "lora_name": consistency_lora["file"],
                                   "strength_model": float(consistency_lora["strength"])}}
        model_ref = ["clora", 0]
    if pulid:
        # PuLID's InsightFace does its OWN face detection+alignment, so feed it the
        # FULL identity image (a real photo where it can find the face), NOT our
        # app-side crop -- a heuristic crop can miss the face entirely (face=0).
        # When the body channel carries the refs (identity_files empty) fall back to
        # a body/full photo, then the face crop, so PuLID always has a source.
        pulid_src = (identity_files[0] if identity_files
                     else (body_files[0] if body_files else face_file))
        if pulid_src:
            model_ref = _inject_pulid(api, model_ref, pulid_src, pulid)

    decoded: List[str] = []
    for i, pf in enumerate(pose_files):
        p = f"p{i}"
        api[f"{p}_load"] = {"class_type": "LoadImage", "inputs": {"image": pf}}
        _pose_src = f"{p}_load"
        if dwpose:
            # SKELETON pose input: convert the mannequin capture to a DWPose
            # skeleton on black IN-GRAPH -- pure pose geometry, zero CGI
            # material/shading for the reference latents to leak into the skin.
            api[f"{p}_dw"] = {"class_type": DWPOSE_CLASS,
                              "inputs": {"image": [f"{p}_load", 0],
                                         "detect_hand": "enable",
                                         "detect_body": "enable",
                                         "detect_face": "disable",
                                         "resolution": 1024,
                                         "bbox_detector": dwpose["bbox_detector"],
                                         "pose_estimator": dwpose["pose_estimator"]}}
            _pose_src = f"{p}_dw"
        api[f"{p}_scale"] = {"class_type": "ImageScaleToTotalPixels",
                             "inputs": {"image": [_pose_src, 0],
                                        "upscale_method": "lanczos", "megapixels": 1.0,
                                          "resolution_steps": 1}}
        api[f"{p}_size"] = {"class_type": "GetImageSize", "inputs": {"image": [f"{p}_scale", 0]}}
        # Explicit output dims (out_width/out_height) make every pose render at a
        # FIXED canvas that matches the base image, so wide characters get the same
        # room everywhere.  Falls back to the pose-capture size when unset.
        if out_width and out_height:
            _ow, _oh = int(out_width), int(out_height)
            api[f"{p}_lat"] = {"class_type": "EmptyFlux2LatentImage",
                               "inputs": {"width": _ow, "height": _oh, "batch_size": 1}}
            api[f"{p}_sig"] = {"class_type": "Flux2Scheduler",
                               "inputs": {"steps": int(steps), "width": _ow, "height": _oh}}
        else:
            api[f"{p}_lat"] = {"class_type": "EmptyFlux2LatentImage",
                               "inputs": {"width": [f"{p}_size", 0], "height": [f"{p}_size", 1],
                                          "batch_size": 1}}
            api[f"{p}_sig"] = {"class_type": "Flux2Scheduler",
                               "inputs": {"steps": int(steps), "width": [f"{p}_size", 0],
                                          "height": [f"{p}_size", 1]}}
        api[f"{p}_enc"] = {"class_type": "VAEEncode",
                           "inputs": {"pixels": [f"{p}_scale", 0], "vae": ["v", 0]}}
        api[f"{p}_pos"] = {"class_type": "CLIPTextEncode",
                           "inputs": {"text": prompts[i], "clip": ["c", 0]}}
        api[f"{p}_neg"] = {"class_type": "CLIPTextEncode",
                           "inputs": {"text": negative_prompt, "clip": ["c", 0]}}
        # reference order (node parity): pose first, identities after — both chains
        def _cond_chain(tag: str, include_pose: bool):
            """One pos/neg conditioning chain from the text encodes:
            [pose ref?] -> body channel -> identity refs.  ``tag`` keeps node
            ids unique per branch."""
            pc, nc = f"{p}_pos", f"{p}_neg"
            if include_pose:
                api[f"{p}_pr0{tag}"] = {"class_type": "ReferenceLatent",
                                        "inputs": {"conditioning": [pc, 0], "latent": [f"{p}_enc", 0]}}
                api[f"{p}_nr0{tag}"] = {"class_type": "ReferenceLatent",
                                        "inputs": {"conditioning": [nc, 0], "latent": [f"{p}_enc", 0]}}
                pc, nc = f"{p}_pr0{tag}", f"{p}_nr0{tag}"
            # BODY channel: masked body reference latents (garment excluded) ride
            # right after the pose ref, before the identity/face refs.
            if _rlp_active and body_load_ids:
                _pr = _inject_reflatentplus(api, [pc, 0], body_load_ids, reflatentplus, f"{p}_pos{tag}")
                _nr = _inject_reflatentplus(api, [nc, 0], body_load_ids, reflatentplus, f"{p}_neg{tag}")
                pc, nc = _pr[0], _nr[0]
            for k, ide in enumerate(id_encs):
                api[f"{p}_pr{k + 1}{tag}"] = {"class_type": "ReferenceLatent",
                                              "inputs": {"conditioning": [pc, 0], "latent": [ide, 0]}}
                api[f"{p}_nr{k + 1}{tag}"] = {"class_type": "ReferenceLatent",
                                              "inputs": {"conditioning": [nc, 0], "latent": [ide, 0]}}
                pc, nc = f"{p}_pr{k + 1}{tag}", f"{p}_nr{k + 1}{tag}"
            return pc, nc

        _rel = float(pose_ref_end) if pose_ref_end else 1.0
        if 0.0 < _rel < 0.999:
            # POSE-REF RELEASE: with-pose conditioning drives 0..rel (locks the
            # pose), pose-free conditioning drives rel..1 (texture forms from
            # the identity refs only -- the CGI mannequin can't stamp the skin).
            _pa, _na = _cond_chain("", True)
            _pb, _nb = _cond_chain("b", False)
            api[f"{p}_tsA"] = {"class_type": "ConditioningSetTimestepRange",
                               "inputs": {"conditioning": [_pa, 0], "start": 0.0, "end": _rel}}
            api[f"{p}_tsB"] = {"class_type": "ConditioningSetTimestepRange",
                               "inputs": {"conditioning": [_pb, 0], "start": _rel, "end": 1.0}}
            api[f"{p}_cmb"] = {"class_type": "ConditioningCombine",
                               "inputs": {"conditioning_1": [f"{p}_tsA", 0],
                                          "conditioning_2": [f"{p}_tsB", 0]}}
            api[f"{p}_tsAn"] = {"class_type": "ConditioningSetTimestepRange",
                                "inputs": {"conditioning": [_na, 0], "start": 0.0, "end": _rel}}
            api[f"{p}_tsBn"] = {"class_type": "ConditioningSetTimestepRange",
                                "inputs": {"conditioning": [_nb, 0], "start": _rel, "end": 1.0}}
            api[f"{p}_cmbn"] = {"class_type": "ConditioningCombine",
                                "inputs": {"conditioning_1": [f"{p}_tsAn", 0],
                                           "conditioning_2": [f"{p}_tsBn", 0]}}
            pos_cur, neg_cur = f"{p}_cmb", f"{p}_cmbn"
        else:
            pos_cur, neg_cur = _cond_chain("", True)
        api[f"{p}_gd"] = {"class_type": "CFGGuider",
                          "inputs": {"model": list(model_ref), "positive": [pos_cur, 0],
                                     "negative": [neg_cur, 0], "cfg": _cfg}}
        _pseed = int(seed) if consistent_seed else int(seed) + i
        api[f"{p}_ns"] = {"class_type": "RandomNoise", "inputs": {"noise_seed": _pseed}}
        api[f"{p}_sm"] = {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "euler"}}
        api[f"{p}_sc"] = {"class_type": "SamplerCustomAdvanced",
                          "inputs": {"noise": [f"{p}_ns", 0], "guider": [f"{p}_gd", 0],
                                     "sampler": [f"{p}_sm", 0], "sigmas": [f"{p}_sig", 0],
                                     "latent_image": [f"{p}_lat", 0]}}
        api[f"{p}_dec"] = {"class_type": "VAEDecode",
                           "inputs": {"samples": [f"{p}_sc", 0], "vae": ["v", 0]}}
        tail = f"{p}_dec"
        # GAN upscale FIRST (when active), THEN face refine on the upscaled
        # image, THEN the megapixels downscale.  On the raw ~1MP render a pose
        # face is ~100-150px, so FaceDetailer's guide round-trip is a 5-7x
        # blow-up-and-shrink that aliases textured skin into horizontal "scan
        # lines" (v1.145 root cause) -- and the GAN then AMPLIFIES the striping.
        # On the 2-4x upscaled image the face is big enough that the round-trip
        # is ~1.5-2x (the same regime where the base preview is clean), and the
        # final whole-image lanczos downscale is antialiased.
        if upscale_model:
            api.setdefault("up_model", {"class_type": "UpscaleModelLoader",
                                        "inputs": {"model_name": upscale_model}})
            api[f"{p}_up"] = {"class_type": "ImageUpscaleWithModel",
                              "inputs": {"upscale_model": ["up_model", 0], "image": [tail, 0]}}
            tail = f"{p}_up"
        if face_refine and (not face_refine_first_only or i == 0):
            # low-denoise FaceDetailer pass: sharpens the face/eyes while the
            # low denoise preserves the likeness.  For the base SET we only
            # refine the front view (the identity anchor) -- a FaceDetailer pass
            # on all four views is 4x the cost and blew the preview past its
            # wait timeout.
            tail = _face_refine_node(api, f"{p}_fd", [tail, 0], list(model_ref),
                                     [pos_cur, 0], [neg_cur, 0],
                                     _pseed, face_refine)
        if upscale_model and upscale_megapixels and upscale_megapixels > 0:
            api[f"{p}_upsc"] = {"class_type": "ImageScaleToTotalPixels",
                                "inputs": {"image": [tail, 0], "upscale_method": "lanczos",
                                           "megapixels": float(upscale_megapixels),
                                           "resolution_steps": 1}}
            tail = f"{p}_upsc"
        decoded.append(tail)

    cur = decoded[0]
    for j, nxt in enumerate(decoded[1:]):
        bid = f"batch{j}"
        api[bid] = {"class_type": "ImageBatch",
                    "inputs": {"image1": [cur, 0], "image2": [nxt, 0]}}
        cur = bid
    if rmbg:
        # worker-side background removal (VNCCS RMBG2 / RMBG-2.0) -- matches how
        # VNCCS itself strips backgrounds; runs on the worker GPU so the sprites
        # come back already cut out (RGBA) and the app-side chroma key is skipped.
        api["rmbg"] = {"class_type": RMBG_NODE_CLASS,
                       "inputs": {"image": [cur, 0],
                                  "model": rmbg.get("model", "RMBG-2.0"),
                                  "background": rmbg.get("background", "Alpha"),
                                  "sensitivity": float(rmbg.get("sensitivity", 1.0)),
                                  "process_res": int(rmbg.get("process_res", 1024)),
                                  "refine_foreground": bool(rmbg.get("refine_foreground", True)),
                                  "mask_blur": int(rmbg.get("mask_blur", 0)),
                                  "mask_offset": int(rmbg.get("mask_offset", 0)),
                                  "invert_output": bool(rmbg.get("invert_output", False))}}
        cur = "rmbg"
    api["save"] = {"class_type": "SaveImage",
                   "inputs": {"images": [cur, 0], "filename_prefix": filename_prefix}}
    return api, {"sprites": "save"}


def resolve_upscale_model(oi: dict, settings: Optional[dict] = None) -> Optional[str]:
    """A GAN upscale model on this worker: settings override, else the best AUTO
    pick, else None.  Auto prefers a 4x, photo-realistic model and AVOIDS the
    heavy edge-sharpening packs (UltraSharp / anime / *sharp*) that draw hard
    ink-like lines on realistic renders (the "looks drawn" artifact).  An explicit
    klein_upscale_model override always wins."""
    opts = _options(oi, "UpscaleModelLoader", "model_name")
    if not opts:
        return None
    want = str((settings or {}).get("klein_upscale_model") or "").strip()
    if want:
        hit = _resolve_name(opts, want)
        if hit:
            return hit

    def _rank(o: str):
        lo = o.lower()
        is4x = ("4x" in lo or "x4" in lo)
        heavy = any(k in lo for k in ("ultrasharp", "ultra_sharp", "anime", "sharp"))
        realistic = any(k in lo for k in
                        ("realesrgan", "realesr", "realistic", "remacri", "foolhardy", "nmkd"))
        # sort key ascending: 4x first, non-heavy next, realistic next, then A-Z
        return (0 if is4x else 1, 1 if heavy else 0, 0 if realistic else 1, lo)

    return sorted(opts, key=_rank)[0]


DWPOSE_CLASS = "DWPreprocessor"


def resolve_dwpose(oi: dict, settings: Optional[dict] = None) -> Optional[Dict[str, Any]]:
    """Skeleton pose-input config, or None for the classic mannequin reference.

    Community finding (RefControl Klein-9B pose LoRA + the Flux.2 style-blending
    guidance): a DWPose SKELETON on black carries pure pose geometry with zero
    material/shading to leak into the output, unlike a shaded CGI mannequin --
    Klein reference latents transfer STYLE from every reference.  When studio
    setting ``klein_pose_input`` = 'skeleton' and comfyui_controlnet_aux's
    DWPreprocessor is on the worker, the mannequin capture is converted to a
    skeleton in-graph and THAT becomes reference image 1."""
    st = settings or {}
    mode = str(st.get("klein_pose_input") or "mannequin").strip().lower()
    if mode not in ("skeleton", "dwpose", "bones"):
        return None
    if DWPOSE_CLASS not in (oi or {}):
        logger.info("klein pose input: skeleton requested but DWPreprocessor is not "
                    "on this worker (comfyui_controlnet_aux) -> using mannequin")
        return None
    bbox = _options(oi, DWPOSE_CLASS, "bbox_detector")
    est = _options(oi, DWPOSE_CLASS, "pose_estimator")
    return {"bbox_detector": next((o for o in bbox if "yolox_l" in o.lower()), bbox[0]) if bbox else "yolox_l.onnx",
            "pose_estimator": next((o for o in est if "dw-ll" in o.lower()), est[0]) if est else "dw-ll_ucoco_384_bs5.torchscript.pt"}


def resolve_consistency_lora(oi: dict, settings: Optional[dict] = None) -> Optional[Dict[str, Any]]:
    """Optional dx8152 Flux2-Klein-9B-Consistency LoRA, STACKED on the pose
    chain (no trigger words; improves i2i identity/colour coherence -- 38k+
    downloads/mo).  Gated by klein_consistency_lora='on'; resolves
    klein_consistency_lora_name, else the first worker LoRA containing
    'consistency'.  Strength klein_consistency_lora_strength (default 0.7)."""
    st = settings or {}
    mode = str(st.get("klein_consistency_lora") or "off").strip().lower()
    if mode not in ("on", "true", "1", "yes"):
        return None
    opts = _options(oi, "LoraLoaderModelOnly", "lora_name")
    want = str(st.get("klein_consistency_lora_name") or "").strip()
    hit = _resolve_name(opts, want) if want else next(
        (o for o in opts if "consistency" in o.lower()), None)
    if not hit:
        logger.info("klein: consistency LoRA enabled but no matching file on this worker")
        return None
    try:
        strength = float(st.get("klein_consistency_lora_strength") or 0.7)
    except Exception:  # noqa: BLE001
        strength = 0.7
    return {"file": hit, "strength": max(0.1, min(1.2, strength))}


# Common filename tokens for a Klein/Flux.2 character TURNAROUND / multi-view LoRA
# (e.g. Zovetry's "Flux2 Klein Multi-view Character Generation", 4-view sprite-sheet
# LoRAs, dx8152 angle LoRAs). Ordered most- to least-specific so an auto-match
# prefers an obvious turnaround file over a generic "...view..." name.
_TURNAROUND_LORA_TOKENS = (
    "turnaround", "multi-view", "multiview", "multi_view", "multiangle",
    "multi-angle", "multi_angle", "sprite", "4view", "4-view", "fourview",
    "rotation", "rotate", "character-sheet", "character_sheet", "charsheet",
)


def resolve_turnaround_lora(oi: dict, settings: Optional[dict] = None) -> Optional[Dict[str, Any]]:
    """Optional Klein/Flux.2 character TURNAROUND (multi-view) LoRA, STACKED on the
    base-set DERIVED views (right/left/back + the mesh A-pose) so the model actually
    rotates the character to a new angle instead of reproducing the front. This is
    the trained alternative to hand-easing reference weights.

    Gated by ``klein_base_turnaround_lora='on'``. Resolves the exact file from
    ``klein_base_turnaround_lora_name`` when set, else auto-matches a worker LoRA
    whose name contains a turnaround token. Strength ``klein_base_turnaround_lora_strength``
    (default 1.0). Returns None (a silent no-op) until the file is present + enabled,
    so nothing changes until the user drops the LoRA on the worker and turns it on."""
    st = settings or {}
    mode = str(st.get("klein_base_turnaround_lora") or "off").strip().lower()
    if mode not in ("on", "true", "1", "yes"):
        return None
    opts = _options(oi, "LoraLoaderModelOnly", "lora_name")
    want = str(st.get("klein_base_turnaround_lora_name") or "").strip()
    if want:
        hit = _resolve_name(opts, want)
    else:
        hit = next((o for o in opts
                    if any(tok in o.lower() for tok in _TURNAROUND_LORA_TOKENS)), None)
    if not hit:
        logger.info("klein: turnaround LoRA enabled but no matching file on this worker "
                    "(set klein_base_turnaround_lora_name to the exact filename)")
        return None
    try:
        strength = float(st.get("klein_base_turnaround_lora_strength") or 1.0)
    except Exception:  # noqa: BLE001
        strength = 1.0
    # optional activation text: many of these LoRAs need a trigger word/phrase at the
    # START of the positive prompt (e.g. 'matchingpose9b'). Blank = triggerless LoRA.
    trigger = str(st.get("klein_base_turnaround_lora_trigger") or "").strip()
    return {"file": hit, "strength": max(0.1, min(1.5, strength)), "trigger": trigger}


def resolve_matchpose_lora(oi: dict, settings: Optional[dict] = None) -> Optional[str]:
    """Filename of the nhathoangfoto MatchingPose LoRA on this worker, for the
    body-shape-preserving base-set turnaround (mannequin pose -> character). Prefers
    the user's klein_pose_lora when it already points at MatchingPose, else
    auto-matches a file named matchingpose / maching_pose. Returns None if absent."""
    st = settings or {}
    opts = _options(oi, "LoraLoaderModelOnly", "lora_name")
    cur = str(st.get("klein_pose_lora") or "").strip()
    if cur and ("matchingpose" in cur.lower() or "maching_pose" in cur.lower()):
        hit = _resolve_name(opts, cur)
        if hit:
            return hit
    return next((o for o in opts
                 if "matchingpose" in o.lower() or "maching_pose" in o.lower()), None)


def build_klein_inpaint_graph(
    *,
    image_file: str,
    mask_file: str,
    prompt: str,
    seed: int,
    models: Dict[str, str],
    steps: int = 12,
    guidance: float = 1.0,
    negative_prompt: str = "",
    ref_files: Optional[List[str]] = None,
    grow: int = 6,
    blur: float = 4.0,
    width: int = 1024,
    height: int = 1216,
    filename_prefix: str = "rbmn_vnccs/klein_inpaint",
) -> Tuple[Dict[str, dict], Dict[str, str]]:
    """Masked INPAINT editor graph (v1.158, the Edit-Image feature): repaint ONLY
    the masked region of ``image_file`` guided by the prompt and up to 3 optional
    reference images (Klein reference latents -- "add the makeup from image 1",
    "the tattoo design in image 2 on the selected arm", ...).  Same
    SetLatentNoiseMask + ImageCompositeMasked pattern as the SAM3 article
    cleanup, so everything OUTSIDE the mask stays byte-identical.  ``mask_file``
    is a white-on-black PNG painted client-side (brush and/or selected SAM3
    segments); GrowMaskWithBlur feathers the seam."""
    ref_files = [r for r in (ref_files or []) if r][:3]
    api: Dict[str, dict] = {
        "u": {"class_type": "UNETLoader",
              "inputs": {"unet_name": models["unet"], "weight_dtype": "default"}},
        "c": {"class_type": "CLIPLoader",
              "inputs": {"clip_name": models["clip"], "type": "flux2", "device": "default"}},
        "v": {"class_type": "VAELoader", "inputs": {"vae_name": models["vae"]}},
        "img": {"class_type": "LoadImage", "inputs": {"image": image_file}},
        "mimg": {"class_type": "LoadImage", "inputs": {"image": mask_file}},
        "mask": {"class_type": "ImageToMask", "inputs": {"image": ["mimg", 0], "channel": "red"}},
    }
    model_ref: list = ["u", 0]
    mask_ref: list = ["mask", 0]
    if grow or blur:
        api["mgrow"] = {"class_type": "GrowMaskWithBlur",
                        "inputs": {"mask": mask_ref, "expand": int(grow),
                                   "incremental_expandrate": 0.0, "tapered_corners": True,
                                   "flip_input": False, "blur_radius": float(blur),
                                   "lerp_alpha": 1.0, "decay_factor": 1.0, "fill_holes": True}}
        mask_ref = ["mgrow", 0]
    api["enc"] = {"class_type": "VAEEncode", "inputs": {"pixels": ["img", 0], "vae": ["v", 0]}}
    api["nm"] = {"class_type": "SetLatentNoiseMask", "inputs": {"samples": ["enc", 0], "mask": mask_ref}}
    api["pos"] = {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["c", 0]}}
    api["neg"] = {"class_type": "CLIPTextEncode", "inputs": {"text": negative_prompt or "", "clip": ["c", 0]}}
    pos_cur, neg_cur = "pos", "neg"
    for k, rf in enumerate(ref_files):
        api[f"r{k}_load"] = {"class_type": "LoadImage", "inputs": {"image": rf}}
        api[f"r{k}_scale"] = {"class_type": "ImageScaleToTotalPixels",
                              "inputs": {"image": [f"r{k}_load", 0], "upscale_method": "lanczos",
                                         "megapixels": 1.0, "resolution_steps": 1}}
        api[f"r{k}_enc"] = {"class_type": "VAEEncode",
                            "inputs": {"pixels": [f"r{k}_scale", 0], "vae": ["v", 0]}}
        api[f"r{k}_pr"] = {"class_type": "ReferenceLatent",
                           "inputs": {"conditioning": [pos_cur, 0], "latent": [f"r{k}_enc", 0]}}
        api[f"r{k}_nr"] = {"class_type": "ReferenceLatent",
                           "inputs": {"conditioning": [neg_cur, 0], "latent": [f"r{k}_enc", 0]}}
        pos_cur, neg_cur = f"r{k}_pr", f"r{k}_nr"
    api["sig"] = {"class_type": "Flux2Scheduler",
                  "inputs": {"steps": int(steps), "width": int(width), "height": int(height)}}
    api["gd"] = {"class_type": "CFGGuider",
                 "inputs": {"model": list(model_ref), "positive": [pos_cur, 0],
                            "negative": [neg_cur, 0], "cfg": float(guidance)}}
    api["ns"] = {"class_type": "RandomNoise", "inputs": {"noise_seed": int(seed)}}
    api["sm"] = {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "euler"}}
    api["sc"] = {"class_type": "SamplerCustomAdvanced",
                 "inputs": {"noise": ["ns", 0], "guider": ["gd", 0], "sampler": ["sm", 0],
                            "sigmas": ["sig", 0], "latent_image": ["nm", 0]}}
    api["dec"] = {"class_type": "VAEDecode", "inputs": {"samples": ["sc", 0], "vae": ["v", 0]}}
    api["comp"] = {"class_type": "ImageCompositeMasked",
                   "inputs": {"destination": ["img", 0], "source": ["dec", 0],
                              "mask": mask_ref, "x": 0, "y": 0, "resize_source": False}}
    api["save"] = {"class_type": "SaveImage",
                   "inputs": {"images": ["comp", 0], "filename_prefix": filename_prefix}}
    return api, {"inpaint": "save"}


def build_sam3_detect_graph(
    *,
    image_file: str,
    prompt: str,
    threshold: float,
    sam_model: str,
    filename_prefix: str = "rbmn_vnccs/sam_detect",
) -> Tuple[Dict[str, dict], Dict[str, str]]:
    """SAM3 detection graph for the Edit-Image SEGMENT mode: segment
    ``image_file`` by TEXT and save EVERY detected mask as its own image (the
    batch fans out to one file per mask) so the client can show a pick-list."""
    api: Dict[str, dict] = {
        "img": {"class_type": "LoadImage", "inputs": {"image": image_file}},
        "sam_m": {"class_type": SAM3_LOADER_CLASS,
                  "inputs": {"model": sam_model, "segmentor": "image",
                             "device": "cuda", "precision": "fp16"}},
        "sam_seg": {"class_type": SAM3_SEG_CLASS,
                    "inputs": {"sam3_model": ["sam_m", 0], "images": ["img", 0],
                               "prompt": prompt, "threshold": float(threshold),
                               "keep_model_loaded": False, "add_background": "none",
                               "detection_limit": -1}},
        "m2i": {"class_type": "MaskToImage", "inputs": {"mask": ["sam_seg", 0]}},
        "save": {"class_type": "SaveImage",
                 "inputs": {"images": ["m2i", 0], "filename_prefix": filename_prefix}},
    }
    return api, {"masks": "save"}


def resolve_tryon_lora(oi: dict, settings: Optional[dict] = None) -> Optional[Dict[str, Any]]:
    """The fal virtual try-on LoRA (flux-klein-tryon-comfy.safetensors) if present
    on this worker.  Trained on Klein 9B Edit with trigger "TRYON ..." and image
    order person / top / bottom; its card recommends steps 28 + guidance 2.5
    (NON-distilled settings).  klein_tryon_lora_name overrides the filename,
    klein_tryon_lora_strength the strength (default 1.0)."""
    st = settings or {}
    opts = _options(oi, "LoraLoaderModelOnly", "lora_name")
    want = str(st.get("klein_tryon_lora_name") or "").strip()
    hit = _resolve_name(opts, want) if want else next(
        (o for o in opts if "tryon" in o.lower().replace("-", "").replace("_", "")), None)
    if not hit:
        return None
    try:
        strength = float(st.get("klein_tryon_lora_strength") or 1.0)
    except Exception:  # noqa: BLE001
        strength = 1.0
    return {"file": hit, "strength": max(0.1, min(1.5, strength))}


def build_klein_tryon_graph(
    *,
    person_file: str,
    garment_files: List[str],
    prompt: str,
    seed: int,
    models: Dict[str, str],
    tryon: Dict[str, Any],
    steps: int = 28,
    guidance: float = 2.5,
    rmbg: Optional[Dict[str, Any]] = None,
    filename_prefix: str = "rbmn_vnccs/klein_tryon",
) -> Tuple[Dict[str, dict], Dict[str, str]]:
    """Virtual try-on graph (fal flux-klein-tryon LoRA): image 1 = the PERSON
    (a pose sprite or base render), images 2..N = garment photos (top, bottom,
    ...).  The prompt must lead with the trained trigger "TRYON".  Unlike the
    distilled pose graphs this runs at the LoRA's trained settings (default 28
    steps / guidance 2.5).  Returns (api_graph, tap_map={'tryon': save_id})."""
    if not person_file:
        raise ValueError("try-on needs a person image")
    garment_files = [g for g in (garment_files or []) if g][:3]
    if not garment_files:
        raise ValueError("try-on needs at least one garment image")
    api: Dict[str, dict] = {
        "u": {"class_type": "UNETLoader",
              "inputs": {"unet_name": models["unet"], "weight_dtype": "default"}},
        "c": {"class_type": "CLIPLoader",
              "inputs": {"clip_name": models["clip"], "type": "flux2", "device": "default"}},
        "v": {"class_type": "VAELoader", "inputs": {"vae_name": models["vae"]}},
        "tlora": {"class_type": "LoraLoaderModelOnly",
                  "inputs": {"model": ["u", 0], "lora_name": tryon["file"],
                             "strength_model": float(tryon.get("strength", 1.0))}},
    }
    model_ref = ["tlora", 0]
    # person + garments as reference latents (person FIRST -- trained order)
    encs: List[str] = []
    for k, fn in enumerate([person_file] + garment_files):
        api[f"t{k}_load"] = {"class_type": "LoadImage", "inputs": {"image": fn}}
        api[f"t{k}_scale"] = {"class_type": "ImageScaleToTotalPixels",
                              "inputs": {"image": [f"t{k}_load", 0],
                                         "upscale_method": "lanczos", "megapixels": 1.0,
                                         "resolution_steps": 1}}
        api[f"t{k}_enc"] = {"class_type": "VAEEncode",
                            "inputs": {"pixels": [f"t{k}_scale", 0], "vae": ["v", 0]}}
        encs.append(f"t{k}_enc")
    api["pos"] = {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["c", 0]}}
    api["neg"] = {"class_type": "CLIPTextEncode", "inputs": {"text": "", "clip": ["c", 0]}}
    pos_cur, neg_cur = "pos", "neg"
    for k, e in enumerate(encs):
        api[f"pr{k}"] = {"class_type": "ReferenceLatent",
                         "inputs": {"conditioning": [pos_cur, 0], "latent": [e, 0]}}
        api[f"nr{k}"] = {"class_type": "ReferenceLatent",
                         "inputs": {"conditioning": [neg_cur, 0], "latent": [e, 0]}}
        pos_cur, neg_cur = f"pr{k}", f"nr{k}"
    api["size"] = {"class_type": "GetImageSize", "inputs": {"image": ["t0_scale", 0]}}
    api["lat"] = {"class_type": "EmptyFlux2LatentImage",
                  "inputs": {"width": ["size", 0], "height": ["size", 1], "batch_size": 1}}
    api["sig"] = {"class_type": "Flux2Scheduler",
                  "inputs": {"steps": int(steps), "width": ["size", 0], "height": ["size", 1]}}
    api["gd"] = {"class_type": "CFGGuider",
                 "inputs": {"model": list(model_ref), "positive": [pos_cur, 0],
                            "negative": [neg_cur, 0], "cfg": float(guidance)}}
    api["ns"] = {"class_type": "RandomNoise", "inputs": {"noise_seed": int(seed)}}
    api["sm"] = {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "euler"}}
    api["sc"] = {"class_type": "SamplerCustomAdvanced",
                 "inputs": {"noise": ["ns", 0], "guider": ["gd", 0], "sampler": ["sm", 0],
                            "sigmas": ["sig", 0], "latent_image": ["lat", 0]}}
    api["dec"] = {"class_type": "VAEDecode", "inputs": {"samples": ["sc", 0], "vae": ["v", 0]}}
    cur = "dec"
    cur = _inject_rmbg(api, cur, rmbg) if rmbg else cur
    api["save"] = {"class_type": "SaveImage",
                   "inputs": {"images": [cur, 0], "filename_prefix": filename_prefix}}
    return api, {"tryon": "save"}


RMBG_NODE_CLASS = "VNCCS_RMBG2"


def _inject_rmbg(api: Dict[str, dict], cur: str, rmbg: Optional[Dict[str, Any]]) -> str:
    """Append the VNCCS RMBG2 background-removal node onto image node ``cur`` and
    return the new node id (RGBA cutout), or ``cur`` unchanged when ``rmbg`` is
    None.  The FINAL step before SaveImage, so the render comes back with the
    solid background already stripped — same node/behaviour the pose sprites use."""
    if not rmbg:
        return cur
    api["rmbg"] = {"class_type": RMBG_NODE_CLASS,
                   "inputs": {"image": [cur, 0],
                              "model": rmbg.get("model", "RMBG-2.0"),
                              "background": rmbg.get("background", "Alpha"),
                              "sensitivity": float(rmbg.get("sensitivity", 1.0)),
                              "process_res": int(rmbg.get("process_res", 1024)),
                              "refine_foreground": bool(rmbg.get("refine_foreground", True)),
                              "mask_blur": int(rmbg.get("mask_blur", 0)),
                              "mask_offset": int(rmbg.get("mask_offset", 0)),
                              "invert_output": bool(rmbg.get("invert_output", False))}}
    return "rmbg"


def resolve_rmbg(oi: dict, settings: Optional[dict] = None) -> Optional[Dict[str, Any]]:
    """Worker-side background removal via VNCCS's own RMBG2 node (RMBG-2.0 /
    BiRefNet), or None when unavailable/disabled.

    This is exactly how VNCCS strips backgrounds -- a real ML matte on the worker
    GPU -- so sprites come back already cut out (RGBA), far cleaner than the
    app-side chroma key and with no CPU cost on the app host.  Enabled by default
    whenever the worker exposes the node; opt out via studio setting
    klein_rmbg='off'.  Tunables: klein_rmbg_model, klein_rmbg_res (256-2048).
    """
    st = settings or {}
    mode = str(st.get("klein_rmbg") or "auto").strip().lower()
    if mode in ("off", "false", "0", "no", "disabled", "none"):
        return None
    if RMBG_NODE_CLASS not in (oi or {}):
        return None
    opts = _options(oi, RMBG_NODE_CLASS, "model")
    want = str(st.get("klein_rmbg_model") or "RMBG-2.0").strip()
    model = _resolve_name(opts, want) or (opts[0] if opts else want)
    try:
        proc = int(st.get("klein_rmbg_res") or 1024)
    except Exception:  # noqa: BLE001
        proc = 1024
    proc = max(256, min(2048, proc))
    return {"model": model, "background": "Alpha", "sensitivity": 1.0,
            "process_res": proc, "refine_foreground": True}


def build_klein_t2i_graph(
    *,
    prompt: str,
    seed: int,
    models: Dict[str, str],
    width: int = 832,
    height: int = 1216,
    steps: int = KLEIN_POSE_STEPS,
    rmbg: Optional[Dict[str, Any]] = None,
    filename_prefix: str = "rbmn_vnccs/klein_preview",
) -> Tuple[Dict[str, dict], Dict[str, str]]:
    """Plain Klein 9B text-to-image (no LoRA, no references) — the Klein-mode
    "Generate Character" base preview.  ``rmbg`` (from resolve_rmbg) strips the
    background as the final step so the base comes back cut out (RGBA)."""
    api: Dict[str, dict] = {
        "u": {"class_type": "UNETLoader",
              "inputs": {"unet_name": models["unet"], "weight_dtype": "default"}},
        "c": {"class_type": "CLIPLoader",
              "inputs": {"clip_name": models["clip"], "type": "flux2", "device": "default"}},
        "v": {"class_type": "VAELoader", "inputs": {"vae_name": models["vae"]}},
        "pos": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["c", 0]}},
        "neg": {"class_type": "ConditioningZeroOut", "inputs": {"conditioning": ["pos", 0]}},
        "lat": {"class_type": "EmptyFlux2LatentImage",
                "inputs": {"width": int(width), "height": int(height), "batch_size": 1}},
        "sig": {"class_type": "Flux2Scheduler",
                "inputs": {"steps": int(steps), "width": int(width), "height": int(height)}},
        "gd": {"class_type": "CFGGuider",
               "inputs": {"model": ["u", 0], "positive": ["pos", 0],
                          "negative": ["neg", 0], "cfg": KLEIN_POSE_CFG}},
        "ns": {"class_type": "RandomNoise", "inputs": {"noise_seed": int(seed)}},
        "sm": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "euler"}},
        "sc": {"class_type": "SamplerCustomAdvanced",
               "inputs": {"noise": ["ns", 0], "guider": ["gd", 0], "sampler": ["sm", 0],
                          "sigmas": ["sig", 0], "latent_image": ["lat", 0]}},
        "dec": {"class_type": "VAEDecode", "inputs": {"samples": ["sc", 0], "vae": ["v", 0]}},
    }
    _cur = _inject_rmbg(api, "dec", rmbg)
    api["save"] = {"class_type": "SaveImage",
                   "inputs": {"images": [_cur, 0], "filename_prefix": filename_prefix}}
    return api, {"preview": "save"}


def klein_restyle_prompt(style_kind: Optional[str], style_custom: str = "",
                         has_style_ref: bool = False) -> str:
    """Edit instruction for a Switch-Style restyle: redraw image 1 in a new art
    style while keeping the SAME character, pose, framing and dress.  Klein/Flux.2
    is a reference-edit model, so a style change is a canonical edit."""
    directive = _style_directive(style_kind, style_custom)
    if has_style_ref:
        style_txt = ("Render the character in the EXACT art style of the second image "
                     "(the style reference): match its medium, linework, shading, colour "
                     "treatment and overall aesthetic.")
        if directive:
            style_txt += " " + directive
    else:
        style_txt = directive
    parts = [
        "Redraw the character shown in image 1 in a NEW ART STYLE. Keep the SAME "
        "character exactly: identical face and facial identity, hair, body build, "
        "proportions and skin tone, the SAME pose, framing, camera angle and "
        "composition, and the SAME state of dress. Change ONLY the rendering / art "
        "style, nothing else."]
    if style_txt:
        parts.append(style_txt)
    parts.append("Solid flat background, evenly lit with flat ambient light, no shadows, "
                 "no cast shadow, no floor or ground plane.")
    return " ".join(parts)


def build_klein_restyle_graph(
    *,
    base_file: str,
    prompt: str,
    seed: int,
    models: Dict[str, str],
    steps: int = KLEIN_POSE_STEPS,
    style_ref_file: Optional[str] = None,
    strength: float = 0.7,
    reflatentplus: Optional[Dict[str, Any]] = None,
    cfg: Optional[float] = None,
    rmbg: Optional[Dict[str, Any]] = None,
    filename_prefix: str = "rbmn_vnccs/klein_restyle",
) -> Tuple[Dict[str, dict], Dict[str, str]]:
    """Klein reference-EDIT graph that restyles ``base_file`` (image 1) into a new
    art style.  The base rides as a reference latent (holding character / pose /
    composition) and a full Flux.2 generation then follows ``prompt`` to change
    only the art style.  ``style_ref_file`` optionally adds the target-style image
    as a second reference.  ``strength`` (0..1, via ReferenceLatentPlus when
    available) trades content preservation vs restyle freedom — higher preserves
    more of the original."""
    if not base_file:
        raise ValueError("restyle graph needs a base image")
    _cfg = float(cfg) if cfg else KLEIN_POSE_CFG
    api: Dict[str, dict] = {
        "u": {"class_type": "UNETLoader",
              "inputs": {"unet_name": models["unet"], "weight_dtype": "default"}},
        "c": {"class_type": "CLIPLoader",
              "inputs": {"clip_name": models["clip"], "type": "flux2", "device": "default"}},
        "v": {"class_type": "VAELoader", "inputs": {"vae_name": models["vae"]}},
    }
    if models.get("lora"):
        api["lora"] = {"class_type": "LoraLoaderModelOnly",
                       "inputs": {"model": ["u", 0], "lora_name": models["lora"],
                                  "strength_model": 1.0}}
        model_ref: list = ["lora", 0]
    else:
        model_ref = ["u", 0]
    model_ref = _apply_realism_lora(api, models, model_ref)
    # base image -> 1MP -> size + encode
    api["base_load"] = {"class_type": "LoadImage", "inputs": {"image": base_file}}
    api["base_scale"] = {"class_type": "ImageScaleToTotalPixels",
                         "inputs": {"image": ["base_load", 0], "upscale_method": "lanczos",
                                    "megapixels": 1.0, "resolution_steps": 1}}
    api["base_size"] = {"class_type": "GetImageSize", "inputs": {"image": ["base_scale", 0]}}
    api["base_enc"] = {"class_type": "VAEEncode",
                       "inputs": {"pixels": ["base_scale", 0], "vae": ["v", 0]}}
    api["lat"] = {"class_type": "EmptyFlux2LatentImage",
                  "inputs": {"width": ["base_size", 0], "height": ["base_size", 1],
                             "batch_size": 1}}
    api["sig"] = {"class_type": "Flux2Scheduler",
                  "inputs": {"steps": int(steps), "width": ["base_size", 0],
                             "height": ["base_size", 1]}}
    api["pos"] = {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["c", 0]}}
    api["neg"] = {"class_type": "CLIPTextEncode", "inputs": {"text": "", "clip": ["c", 0]}}
    pos_cur, neg_cur = "pos", "neg"
    # base as content reference — strength-controlled via ReferenceLatentPlus (no
    # mask: preserve the whole composition), else stock ReferenceLatent.
    if reflatentplus:
        _rcfg = {"strength": float(max(0.05, min(2.0, strength))), "start": 0.0, "end": 1.0,
                 "masks": {"face": False, "hair": False, "body": False,
                           "clothes": False, "background": False}}
        pos_cur = _inject_reflatentplus(api, [pos_cur, 0], ["base_load"], _rcfg, "rs_pos")[0]
        neg_cur = _inject_reflatentplus(api, [neg_cur, 0], ["base_load"], _rcfg, "rs_neg")[0]
    else:
        api["base_pr"] = {"class_type": "ReferenceLatent",
                          "inputs": {"conditioning": [pos_cur, 0], "latent": ["base_enc", 0]}}
        api["base_nr"] = {"class_type": "ReferenceLatent",
                          "inputs": {"conditioning": [neg_cur, 0], "latent": ["base_enc", 0]}}
        pos_cur, neg_cur = "base_pr", "base_nr"
    # optional style reference image (image 2) as an extra reference latent
    if style_ref_file:
        api["style_load"] = {"class_type": "LoadImage", "inputs": {"image": style_ref_file}}
        api["style_scale"] = {"class_type": "ImageScaleToTotalPixels",
                              "inputs": {"image": ["style_load", 0], "upscale_method": "lanczos",
                                         "megapixels": 1.0, "resolution_steps": 1}}
        api["style_enc"] = {"class_type": "VAEEncode",
                            "inputs": {"pixels": ["style_scale", 0], "vae": ["v", 0]}}
        api["style_pr"] = {"class_type": "ReferenceLatent",
                           "inputs": {"conditioning": [pos_cur, 0], "latent": ["style_enc", 0]}}
        api["style_nr"] = {"class_type": "ReferenceLatent",
                           "inputs": {"conditioning": [neg_cur, 0], "latent": ["style_enc", 0]}}
        pos_cur, neg_cur = "style_pr", "style_nr"
    api["gd"] = {"class_type": "CFGGuider",
                 "inputs": {"model": list(model_ref), "positive": [pos_cur, 0],
                            "negative": [neg_cur, 0], "cfg": _cfg}}
    api["ns"] = {"class_type": "RandomNoise", "inputs": {"noise_seed": int(seed)}}
    api["sm"] = {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "euler"}}
    api["sc"] = {"class_type": "SamplerCustomAdvanced",
                 "inputs": {"noise": ["ns", 0], "guider": ["gd", 0], "sampler": ["sm", 0],
                            "sigmas": ["sig", 0], "latent_image": ["lat", 0]}}
    api["dec"] = {"class_type": "VAEDecode", "inputs": {"samples": ["sc", 0], "vae": ["v", 0]}}
    _cur = _inject_rmbg(api, "dec", rmbg)
    api["save"] = {"class_type": "SaveImage",
                   "inputs": {"images": [_cur, 0], "filename_prefix": filename_prefix}}
    return api, {"restyle": "save"}


def build_klein_clothes_graph(
    *,
    base_file: str,
    prompt: str,
    seed: int,
    models: Dict[str, str],
    steps: int = KLEIN_POSE_STEPS,
    garment_ref_file: Optional[str] = None,
    strength: float = 1.0,
    reflatentplus: Optional[Dict[str, Any]] = None,
    face_refine: Optional[Dict[str, Any]] = None,
    cfg: Optional[float] = None,
    rmbg: Optional[Dict[str, Any]] = None,
    ref_end: float = 1.0,
    negative_prompt: str = "",
    consistency_lora: Optional[Dict[str, Any]] = None,
    identity_lock: bool = True,
    filename_prefix: str = "rbmn_vnccs/klein_clothes",
) -> Tuple[Dict[str, dict], Dict[str, str]]:
    """Klein reference-EDIT that DRESSES ``base_file`` in a new outfit.  The base
    rides as a MASKED reference latent that keeps FACE + HAIR + BODY + POSE but
    DROPS the current garment (the 'person minus clothes' mask), so a full Flux.2
    generation following ``prompt`` redraws ONLY the clothing.  ``garment_ref_file``
    optionally adds the target-outfit image as a second reference latent so the
    model reproduces that specific garment.  ``strength`` trades identity/pose
    preservation vs redraw freedom.  Optional FaceDetailer keeps the face crisp;
    RMBG strips the background."""
    if not base_file:
        raise ValueError("clothes graph needs a base image")
    _cfg = float(cfg) if cfg else KLEIN_POSE_CFG
    api: Dict[str, dict] = {
        "u": {"class_type": "UNETLoader",
              "inputs": {"unet_name": models["unet"], "weight_dtype": "default"}},
        "c": {"class_type": "CLIPLoader",
              "inputs": {"clip_name": models["clip"], "type": "flux2", "device": "default"}},
        "v": {"class_type": "VAELoader", "inputs": {"vae_name": models["vae"]}},
    }
    if models.get("lora"):
        api["lora"] = {"class_type": "LoraLoaderModelOnly",
                       "inputs": {"model": ["u", 0], "lora_name": models["lora"],
                                  "strength_model": 1.0}}
        model_ref: list = ["lora", 0]
    else:
        model_ref = ["u", 0]
    if consistency_lora:
        # v1.162: dx8152 Consistency LoRA stacked on the DRESSING chain -- an
        # identity guard that helps hold the character's face/body/skin colour
        # while the outfit is redrawn (same LoRA + strength as the pose stack).
        api["clora"] = {"class_type": "LoraLoaderModelOnly",
                        "inputs": {"model": model_ref, "lora_name": consistency_lora["file"],
                                   "strength_model": float(consistency_lora["strength"])}}
        model_ref = ["clora", 0]
    # base image -> 1MP -> size (+ encode for the stock-reference fallback)
    api["base_load"] = {"class_type": "LoadImage", "inputs": {"image": base_file}}
    api["base_scale"] = {"class_type": "ImageScaleToTotalPixels",
                         "inputs": {"image": ["base_load", 0], "upscale_method": "lanczos",
                                    "megapixels": 1.0, "resolution_steps": 1}}
    api["base_size"] = {"class_type": "GetImageSize", "inputs": {"image": ["base_scale", 0]}}
    api["lat"] = {"class_type": "EmptyFlux2LatentImage",
                  "inputs": {"width": ["base_size", 0], "height": ["base_size", 1],
                             "batch_size": 1}}
    api["sig"] = {"class_type": "Flux2Scheduler",
                  "inputs": {"steps": int(steps), "width": ["base_size", 0],
                             "height": ["base_size", 1]}}
    api["pos"] = {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["c", 0]}}
    api["neg"] = {"class_type": "CLIPTextEncode", "inputs": {"text": negative_prompt or "", "clip": ["c", 0]}}
    pos_cur, neg_cur = "pos", "neg"
    # base as content reference — KEEP face+hair+body+pose, DROP the garment so the
    # prompt redraws the outfit.  ReferenceLatentPlus 'person minus clothes' mask
    # when the node is present, else stock ReferenceLatent (whole base) as fallback.
    if reflatentplus:
        # ref_end (v1.159): RELEASE the bare-skin body reference before the late
        # texture-forming steps.  Held to 1.0 the model keeps seeing bare skin
        # exactly where the garment must go, and the skin BLEEDS THROUGH the
        # fabric (translucent / skin-toned clothing).  ~0.8 keeps identity/body/
        # pose locked while the garments finish opaque.
        _end = float(max(0.3, min(1.0, ref_end)))
        _str = float(max(0.05, min(2.0, strength)))
        entries: List[Dict[str, Any]] = [
            # composition ref -- face+hair+body, released at ref_end so bare skin
            # stops showing where the garment must finish opaque (v1.159)
            {"load": "base_load", "strength": _str, "start": 0.0, "end": _end,
             "masks": {"face": True, "hair": True, "body": True,
                       "clothes": False, "background": False}},
        ]
        if identity_lock:
            # v1.164 split-gated identity ref -- face+hair ONLY, re-asserted in
            # the clean LATE steps where identity/texture actually forms.  The
            # mask never covers the clothing area, so the garment stays opaque
            # while the face and hair stop drifting after the body ref releases.
            entries.append({"load": "base_load", "strength": 0.9,
                            "start": max(0.4, _end - 0.3), "end": 1.0,
                            "masks": {"face": True, "hair": True, "body": False,
                                      "clothes": False, "background": False}})
        pos_cur = _inject_reflatentplus_multi(api, [pos_cur, 0], entries, "cl_pos")[0]
        neg_cur = _inject_reflatentplus_multi(api, [neg_cur, 0], entries, "cl_neg")[0]
    else:
        api["base_enc"] = {"class_type": "VAEEncode",
                           "inputs": {"pixels": ["base_scale", 0], "vae": ["v", 0]}}
        api["base_pr"] = {"class_type": "ReferenceLatent",
                          "inputs": {"conditioning": [pos_cur, 0], "latent": ["base_enc", 0]}}
        api["base_nr"] = {"class_type": "ReferenceLatent",
                          "inputs": {"conditioning": [neg_cur, 0], "latent": ["base_enc", 0]}}
        pos_cur, neg_cur = "base_pr", "base_nr"
    # optional garment reference image (image 2) as an extra reference latent
    if garment_ref_file:
        api["garment_load"] = {"class_type": "LoadImage", "inputs": {"image": garment_ref_file}}
        api["garment_scale"] = {"class_type": "ImageScaleToTotalPixels",
                                "inputs": {"image": ["garment_load", 0], "upscale_method": "lanczos",
                                           "megapixels": 1.0, "resolution_steps": 1}}
        api["garment_enc"] = {"class_type": "VAEEncode",
                              "inputs": {"pixels": ["garment_scale", 0], "vae": ["v", 0]}}
        api["garment_pr"] = {"class_type": "ReferenceLatent",
                             "inputs": {"conditioning": [pos_cur, 0], "latent": ["garment_enc", 0]}}
        api["garment_nr"] = {"class_type": "ReferenceLatent",
                             "inputs": {"conditioning": [neg_cur, 0], "latent": ["garment_enc", 0]}}
        pos_cur, neg_cur = "garment_pr", "garment_nr"
    api["gd"] = {"class_type": "CFGGuider",
                 "inputs": {"model": list(model_ref), "positive": [pos_cur, 0],
                            "negative": [neg_cur, 0], "cfg": _cfg}}
    api["ns"] = {"class_type": "RandomNoise", "inputs": {"noise_seed": int(seed)}}
    api["sm"] = {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "euler"}}
    api["sc"] = {"class_type": "SamplerCustomAdvanced",
                 "inputs": {"noise": ["ns", 0], "guider": ["gd", 0], "sampler": ["sm", 0],
                            "sigmas": ["sig", 0], "latent_image": ["lat", 0]}}
    api["dec"] = {"class_type": "VAEDecode", "inputs": {"samples": ["sc", 0], "vae": ["v", 0]}}
    _cur = "dec"
    if face_refine:
        _cur = _face_refine_node(api, "cl_fd", [_cur, 0], list(model_ref),
                                 [pos_cur, 0], [neg_cur, 0], int(seed), face_refine)
    _cur = _inject_rmbg(api, _cur, rmbg)
    api["save"] = {"class_type": "SaveImage",
                   "inputs": {"images": [_cur, 0], "filename_prefix": filename_prefix}}
    return api, {"clothes": "save"}


GARMENT_EXTRACT_PROMPT = (
    "Extract only the clothing from image 1 -- the COMPLETE outfit with EVERY "
    "garment piece and accessory it includes (top, bottom, dress, shoes, belt, "
    "gloves, headwear, jewellery) -- and present it laid flat, front view, "
    "arranged together on a plain pure white background, product-catalogue "
    "style. Completely remove the person, mannequin, hanger, hands and the "
    "original background. Do not drop, merge or simplify any piece; keep every "
    "garment's exact colors, pattern, print, fabric texture, cut, seams and "
    "details unchanged. The clothing fills most of the frame."
)


def build_klein_garment_extract_graph(
    *,
    garment_file: str,
    seed: int,
    models: Dict[str, str],
    steps: int = 10,
    filename_prefix: str = "rbmn_vnccs/klein_garment",
) -> Tuple[Dict[str, dict], Dict[str, str]]:
    """v1.164 garment CLEANUP pre-pass: a quick Klein reference edit that
    extracts the garment from a photo (model wearing it, busy background, ...)
    onto a plain white background before the actual dressing/try-on pass.
    Research finding: feeding a clean 'garment on white' reference measurably
    improves garment accuracy vs telling the swap prompt to ignore the person
    -- give Klein a simpler task per pass."""
    if not garment_file:
        raise ValueError("garment extract graph needs a garment image")
    api: Dict[str, dict] = {
        "u": {"class_type": "UNETLoader",
              "inputs": {"unet_name": models["unet"], "weight_dtype": "default"}},
        "c": {"class_type": "CLIPLoader",
              "inputs": {"clip_name": models["clip"], "type": "flux2", "device": "default"}},
        "v": {"class_type": "VAELoader", "inputs": {"vae_name": models["vae"]}},
        "g_load": {"class_type": "LoadImage", "inputs": {"image": garment_file}},
        "g_scale": {"class_type": "ImageScaleToTotalPixels",
                    "inputs": {"image": ["g_load", 0], "upscale_method": "lanczos",
                               "megapixels": 1.0, "resolution_steps": 1}},
        "g_size": {"class_type": "GetImageSize", "inputs": {"image": ["g_scale", 0]}},
        "lat": {"class_type": "EmptyFlux2LatentImage",
                "inputs": {"width": ["g_size", 0], "height": ["g_size", 1], "batch_size": 1}},
        "sig": {"class_type": "Flux2Scheduler",
                "inputs": {"steps": int(steps), "width": ["g_size", 0], "height": ["g_size", 1]}},
        "pos": {"class_type": "CLIPTextEncode",
                "inputs": {"text": GARMENT_EXTRACT_PROMPT, "clip": ["c", 0]}},
        "neg": {"class_type": "CLIPTextEncode", "inputs": {"text": "", "clip": ["c", 0]}},
        "g_enc": {"class_type": "VAEEncode", "inputs": {"pixels": ["g_scale", 0], "vae": ["v", 0]}},
        "pos_r": {"class_type": "ReferenceLatent",
                  "inputs": {"conditioning": ["pos", 0], "latent": ["g_enc", 0]}},
        "neg_r": {"class_type": "ReferenceLatent",
                  "inputs": {"conditioning": ["neg", 0], "latent": ["g_enc", 0]}},
        "gd": {"class_type": "CFGGuider",
               "inputs": {"model": ["u", 0], "positive": ["pos_r", 0],
                          "negative": ["neg_r", 0], "cfg": KLEIN_POSE_CFG}},
        "ns": {"class_type": "RandomNoise", "inputs": {"noise_seed": int(seed)}},
        "sm": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "euler"}},
        "sc": {"class_type": "SamplerCustomAdvanced",
               "inputs": {"noise": ["ns", 0], "guider": ["gd", 0], "sampler": ["sm", 0],
                          "sigmas": ["sig", 0], "latent_image": ["lat", 0]}},
        "dec": {"class_type": "VAEDecode", "inputs": {"samples": ["sc", 0], "vae": ["v", 0]}},
        "save": {"class_type": "SaveImage",
                 "inputs": {"images": ["dec", 0], "filename_prefix": filename_prefix}},
    }
    return api, {"garment": "save"}


def klein_clothes_prompt(costume_info: dict, background: str,
                         has_garment_ref: bool = False,
                         style_kind: Optional[str] = None, style_custom: str = "") -> str:
    """Prompt that DRESSES the referenced base body in a specific outfit while
    keeping the SAME person, body, face, hair and POSE -- change ONLY the
    clothing.  Reads the costume slots (top/bottom/head/face/shoes) as free text
    and/or points at the garment reference image."""
    ci = costume_info or {}
    slot_labels = (("head", "on the head"), ("face", "on the face / eyewear"),
                   ("top", "upper body"), ("bottom", "lower body"),
                   ("shoes", "footwear"))
    slots = []
    for key, label in slot_labels:
        val = str(ci.get(key) or "").strip()
        if val:
            slots.append(f"{label}: {val}")
    parts = ["The SAME person in the SAME pose, body and framing as the reference, "
             "with the same face, hair, skin tone and proportions -- change ONLY the "
             "clothing they are wearing."]
    if has_garment_ref:
        # v1.164: community-converged Klein swap template -- numbered-image
        # instruction + explicit negative-scope clause (suppresses hallucinated
        # accessories) + the accepted identity-anchor phrase.
        parts.append("Change only the clothing of the person to match the garments "
                     "shown in image 2: faithfully reproduce their colors, patterns, "
                     "materials, cut and details, fitted naturally to this body and "
                     "pose. Do not add any accessories, jewellery, bags, hats or "
                     "shoes that are not clearly visible in image 2. Keep the face, "
                     "hair, pose and background unchanged.")
    if slots:
        parts.append("The character is now fully dressed -- " + "; ".join(slots) + ".")
    elif not has_garment_ref:
        parts.append("The character is now fully dressed in a complete, well-fitted "
                     "everyday outfit.")
    parts.append("The clothing fits and drapes naturally on the body in this exact "
                 "pose, full body visible head to toe, no floating or detached garments.")
    # v1.159: the bare-skin base reference tends to bleed through garments --
    # state OPACITY explicitly (positive wording works at guidance 1.0).
    parts.append("Every garment is OPAQUE fabric with its OWN colour and texture: "
                 "the clothing fully covers and hides the skin beneath it, with a "
                 "crisp, clearly visible edge where fabric meets skin. NO skin tone "
                 "showing through any garment -- not sheer, not translucent, not "
                 "body paint.")
    directive = _style_directive(style_kind, style_custom)
    if directive:
        parts.append(directive)
    bg = str(background or "Green").strip() or "Green"
    parts.append(f"Solid flat {bg.lower()} background, evenly lit with flat ambient "
                 "light, no shadows, no cast shadow, no floor or ground plane, so the "
                 "background can be keyed out cleanly.")
    return " ".join(parts)


def klein_refbase_prompt(character_info: dict, background: str, nsfw: bool = False,
                         view_desc: str = "", style_kind: Optional[str] = None,
                         style_custom: str = "", sex: str = "",
                         mesh_ready: bool = False, rotate: bool = False) -> str:
    """Prompt for a REFERENCE-DRIVEN neutral base: the character's body comes from
    the reference photos (fed as content), posed neutrally by this text — NO
    mannequin, so nothing overrides the reference build.

    ``mesh_ready`` locks the character into a symmetric arms-clear A-pose on a
    plain neutral-gray backdrop — the preferred input for 3D mesh generation.

    ``rotate`` = SET-DERIVATION mode: the reference IS the approved front base, and
    we want to KEEP it EXACTLY (face, hair, body, skin AND clothing/state, colours)
    and only change the camera angle. NOT a strip — a novel-view rotation, so every
    view of the set is the same character seen from a different side."""
    if rotate:
        vd = str(view_desc or "").strip() or "the same view"
        bg = "neutral gray" if mesh_ready else (str(background or "Green").strip() or "Green")
        stance = ("the SAME symmetric A-pose, arms held out about 30 degrees from the body"
                  if mesh_ready else "the SAME relaxed standing pose")
        return (
            "Novel-view render of the EXACT SAME character shown in the reference image. "
            "Keep EVERYTHING identical — the same face and facial identity, hair, body build "
            "and proportions, skin tone, and the SAME clothing / state of dress and the SAME "
            "colours and materials. Do NOT add, remove or change any clothing, accessories or "
            "colours. Change ONLY the camera viewpoint: show this identical character from " + vd +
            ", in " + stance + ", the whole figure visible head to toe, uniformly framed. "
            f"Solid flat {bg.lower()} background, evenly lit with flat ambient light, no shadows, "
            "no cast shadow, no floor or ground plane, so the background can be keyed out cleanly.")
    parts = [
        "Full-body character reference of the SAME person shown in the reference "
        "images. Reproduce their EXACT body: overall build, body fat / weight, "
        "proportions, shoulders, chest, waist, hips and height, and their face, "
        "hair, skin tone and facial identity — match the references closely."]

    vd = str(view_desc or "").strip() or "front view, facing the camera"
    if mesh_ready:
        parts.append("Standing in a symmetric A-pose: upright and centered, both arms "
                     "lowered and held out to the sides about 30 degrees away from the "
                     "torso so the arms and hands are clearly separated from the body "
                     "with a visible gap on each side, hands open with fingers apart, "
                     "legs straight and feet about shoulder-width apart, the whole figure "
                     "visible head to toe and uniformly framed, " + vd + ".")
    else:
        parts.append("Standing straight and relaxed, arms resting slightly away from the "
                     "body, the whole figure visible head to toe, with a small margin of empty space above the head and below the feet (the figure centered, not touching the top or bottom edges), " + vd + ".")
    parts.append("Change ONLY the clothing: " + _base_body_state(nsfw, sex) + ".")
    directive = _style_directive(style_kind, style_custom)
    if directive:
        parts.append(directive)
    bg = "neutral gray" if mesh_ready else (str(background or "Green").strip() or "Green")
    parts.append(f"Solid flat {bg.lower()} background, evenly lit with flat ambient "
                 "light, no shadows, no cast shadow, no floor or ground plane, so the "
                 "background can be keyed out cleanly.")
    return " ".join(parts)


def build_klein_refbase_graph(
    *,
    prompt: str,
    seed: int,
    models: Dict[str, str],
    body_files: List[str],
    width: int = 832,
    height: int = 1216,
    steps: int = KLEIN_POSE_STEPS,
    reflatentplus: Optional[Dict[str, Any]] = None,
    strength: float = 1.15,
    body_ref_end: float = 1.0,
    face_file: Optional[str] = None,
    pulid_image: Optional[str] = None,
    pulid: Optional[Dict[str, Any]] = None,
    face_refine: Optional[Dict[str, Any]] = None,
    sam_cleanup: Optional[Dict[str, Any]] = None,
    rmbg: Optional[Dict[str, Any]] = None,
    cfg: Optional[float] = None,
    negative_prompt: str = "",
    consistency_lora: Optional[Dict[str, Any]] = None,
    turnaround_lora: Optional[Dict[str, Any]] = None,
    keep_clothes_mask: bool = True,
    filename_prefix: str = "rbmn_vnccs/klein_refbase",
) -> Tuple[Dict[str, dict], Dict[str, str]]:
    """Reference-DRIVEN base: the body comes from the reference PHOTOS (ridden as
    strong whole-person reference latents), posed neutrally by ``prompt`` from an
    empty latent — NO pose mannequin, so the mannequin's build can't override the
    references.  The face crop drives the face-detail reference latent; PuLID is fed
    the FULL face image (``pulid_image``) so InsightFace can detect+align it; RMBG
    strips the background."""
    if not body_files:
        raise ValueError("refbase graph needs at least one body reference")
    body_files = body_files[:4]
    _cfg = float(cfg) if cfg else KLEIN_POSE_CFG
    api: Dict[str, dict] = {
        "u": {"class_type": "UNETLoader",
              "inputs": {"unet_name": models["unet"], "weight_dtype": "default"}},
        "c": {"class_type": "CLIPLoader",
              "inputs": {"clip_name": models["clip"], "type": "flux2", "device": "default"}},
        "v": {"class_type": "VAELoader", "inputs": {"vae_name": models["vae"]}},
    }
    if models.get("lora"):
        api["lora"] = {"class_type": "LoraLoaderModelOnly",
                       "inputs": {"model": ["u", 0], "lora_name": models["lora"],
                                  "strength_model": 1.0}}
        model_ref: list = ["lora", 0]
    else:
        model_ref = ["u", 0]
    if consistency_lora:
        # dx8152 Consistency LoRA stacked AFTER the main LoRA (triggerless) --
        # the SAME cross-image consistency the pose sets use, now on base sets so
        # the 4 views hold a matching look. Same node the pose graph uses.
        api["clora"] = {"class_type": "LoraLoaderModelOnly",
                        "inputs": {"model": list(model_ref),
                                   "lora_name": consistency_lora["file"],
                                   "strength_model": float(consistency_lora["strength"])}}
        model_ref = ["clora", 0]
    if turnaround_lora:
        # character TURNAROUND / multi-view LoRA stacked on top -- trained to rotate
        # the subject to a new camera angle, so the DERIVED views (right/left/back +
        # mesh A-pose) actually turn instead of reproducing the front reference.
        api["tlora"] = {"class_type": "LoraLoaderModelOnly",
                        "inputs": {"model": list(model_ref),
                                   "lora_name": turnaround_lora["file"],
                                   "strength_model": float(turnaround_lora["strength"])}}
        model_ref = ["tlora", 0]

    body_load_ids: List[str] = []
    for k, bf in enumerate(body_files):
        bid = f"rb{k}_load"
        api[bid] = {"class_type": "LoadImage", "inputs": {"image": bf}}
        body_load_ids.append(bid)

    # face crop -> reference latent (dominant facial detail)
    id_encs: List[str] = []
    if face_file:
        api["face_load"] = {"class_type": "LoadImage", "inputs": {"image": face_file}}
        api["face_scale"] = {"class_type": "ImageScaleToTotalPixels",
                             "inputs": {"image": ["face_load", 0], "upscale_method": "lanczos",
                                        "megapixels": 1.0, "resolution_steps": 1}}
        api["face_enc"] = {"class_type": "VAEEncode",
                           "inputs": {"pixels": ["face_scale", 0], "vae": ["v", 0]}}
        id_encs.append("face_enc")

    if pulid:
        # PuLID's InsightFace runs its OWN face detection + alignment, so feed it the
        # FULL face-role reference image (``pulid_image``) -- NOT our app-side crop.
        # A heuristic crop (when YuNet/Haar miss) can chop the face or rescale a
        # fragment, and InsightFace then finds nothing ("face=0"/AUCUN VISAGE) -> the
        # adapter no-ops.  This matches build_klein_pose_graph, which feeds the full
        # identity image for the same reason.  Fall back to the crop, then a body ref,
        # only if no full face image was supplied.
        pulid_src = pulid_image or face_file or (body_files[0] if body_files else None)
        if pulid_src:
            model_ref = _inject_pulid(api, model_ref, pulid_src, pulid)

    api["lat"] = {"class_type": "EmptyFlux2LatentImage",
                  "inputs": {"width": int(width), "height": int(height), "batch_size": 1}}
    api["sig"] = {"class_type": "Flux2Scheduler",
                  "inputs": {"steps": int(steps), "width": int(width), "height": int(height)}}
    api["pos"] = {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["c", 0]}}
    api["neg"] = {"class_type": "CLIPTextEncode", "inputs": {"text": negative_prompt, "clip": ["c", 0]}}
    pos_cur, neg_cur = "pos", "neg"

    # BODY content from the photos — whole person (bg removed), strong, so the
    # reference build drives the base.  ReferenceLatentPlus when present (mask +
    # strength), else stock ReferenceLatent on each body ref.
    if reflatentplus:
        # keep_clothes_mask=False (a HARD strip): don't reference the clothing region,
        # so the reference can't reproduce pants/shoes/footwear -- the prompt fills
        # those areas with the underwear/nude base instead. body mask still holds shape.
        _rcfg = {"strength": float(max(0.05, min(3.0, strength))), "start": 0.0,
                 "end": float(max(0.5, min(1.0, body_ref_end))),
                 "masks": {"face": True, "hair": True, "body": True,
                           "clothes": bool(keep_clothes_mask), "background": False}}
        pos_cur = _inject_reflatentplus(api, [pos_cur, 0], body_load_ids, _rcfg, "rbp")[0]
        neg_cur = _inject_reflatentplus(api, [neg_cur, 0], body_load_ids, _rcfg, "rbn")[0]
    else:
        for k, bl in enumerate(body_load_ids):
            api[f"{bl}_scale"] = {"class_type": "ImageScaleToTotalPixels",
                                  "inputs": {"image": [bl, 0], "upscale_method": "lanczos",
                                             "megapixels": 1.0, "resolution_steps": 1}}
            api[f"{bl}_enc"] = {"class_type": "VAEEncode",
                                "inputs": {"pixels": [f"{bl}_scale", 0], "vae": ["v", 0]}}
            api[f"rbp{k}"] = {"class_type": "ReferenceLatent",
                              "inputs": {"conditioning": [pos_cur, 0], "latent": [f"{bl}_enc", 0]}}
            api[f"rbn{k}"] = {"class_type": "ReferenceLatent",
                              "inputs": {"conditioning": [neg_cur, 0], "latent": [f"{bl}_enc", 0]}}
            pos_cur, neg_cur = f"rbp{k}", f"rbn{k}"

    for k, ide in enumerate(id_encs):
        api[f"fp{k}"] = {"class_type": "ReferenceLatent",
                         "inputs": {"conditioning": [pos_cur, 0], "latent": [ide, 0]}}
        api[f"fn{k}"] = {"class_type": "ReferenceLatent",
                         "inputs": {"conditioning": [neg_cur, 0], "latent": [ide, 0]}}
        pos_cur, neg_cur = f"fp{k}", f"fn{k}"

    api["gd"] = {"class_type": "CFGGuider",
                 "inputs": {"model": list(model_ref), "positive": [pos_cur, 0],
                            "negative": [neg_cur, 0], "cfg": _cfg}}
    api["ns"] = {"class_type": "RandomNoise", "inputs": {"noise_seed": int(seed)}}
    api["sm"] = {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "euler"}}
    api["sc"] = {"class_type": "SamplerCustomAdvanced",
                 "inputs": {"noise": ["ns", 0], "guider": ["gd", 0], "sampler": ["sm", 0],
                            "sigmas": ["sig", 0], "latent_image": ["lat", 0]}}
    api["dec"] = {"class_type": "VAEDecode", "inputs": {"samples": ["sc", 0], "vae": ["v", 0]}}
    # optional low-denoise FaceDetailer refine on the base face (ultralytics detector,
    # not PuLID/InsightFace) -- runs on the decoded RGB BEFORE background removal.
    _fr_src = "dec"
    if face_refine:
        _fr_src = _face_refine_node(api, "fd", ["dec", 0], list(model_ref),
                                    [pos_cur, 0], [neg_cur, 0], int(seed), face_refine)
    if sam_cleanup:
        _fr_src = _inject_sam3_cleanup(api, _fr_src, list(model_ref), sam_cleanup,
                                       int(seed), prompt, negative_prompt,
                                       int(steps), int(width), int(height))
    _cur = _inject_rmbg(api, _fr_src, rmbg)
    api["save"] = {"class_type": "SaveImage",
                   "inputs": {"images": [_cur, 0], "filename_prefix": filename_prefix}}
    return api, {"base": "save"}


def klein_preview_prompt(character_info: dict, background: str,
                         nsfw: bool = False, style_kind: Optional[str] = None,
                         style_custom: str = "", view_desc: str = "",
                         mesh_ready: bool = False) -> str:
    """T2I prompt from the tag-sheet fields (Klein prose style).  The base
    preview is a body-only BASE -- the character in neutral underwear (or nude
    when ``nsfw``) so the Clothes/Emotions modes dress it later, matching VNCCS
    Native base characters.

    ``view_desc`` overrides the default "front view" clause so the SAME
    character can be rendered from several angles for a 4-view base set.
    ``mesh_ready`` swaps the relaxed stance for a locked, symmetric A-pose with
    arms held clear of the body and forces a plain neutral-gray backdrop --
    the ideal input for 3D mesh generation (Hunyuan3D)."""
    ci = character_info or {}
    bits: List[str] = []
    sex = str(ci.get("sex") or "").strip()
    age = ci.get("age")
    race = str(ci.get("race") or "").strip()
    lead = " ".join(x for x in (race, sex) if x)
    if lead:
        bits.append(lead + (f", age {age}" if age else ""))
    for key in ("skin_color", "hair", "eyes", "face", "body", "additional_details", "aesthetics"):
        v = str(ci.get(key) or "").strip()
        if v:
            bits.append(v)
    # mesh-ready always keys out on a plain uniform gray (best for shape gen)
    bg = "neutral gray" if mesh_ready else (str(background or "Green").strip() or "Green")
    desc = ". ".join(bits)
    style = _style_directive(style_kind, style_custom)
    style = (style + " ") if style else ""
    vd = str(view_desc or "").strip() or "front view"
    if mesh_ready:
        stance = ("Standing in a symmetric A-pose: upright and centered, both arms "
                  "lowered and held out to the sides about 30 degrees away from the torso "
                  "so the arms and hands are clearly separated from the body with a visible "
                  "gap on each side, hands open with fingers apart, legs straight and feet "
                  "about shoulder-width apart, " + vd + ", the whole figure visible head to "
                  "toe and uniformly framed with even margins")
    else:
        stance = "Standing relaxed, " + vd + ", whole figure visible head to toe, with a small margin of empty space above the head and below the feet (the figure centered, not touching the top or bottom edges)"
    return (f"{style}Full body character reference of {desc}. {stance}. "
            f"{_base_body_state(nsfw, str((character_info or {}).get('sex') or ''))}. "
            f"Solid flat {bg.lower()} background, evenly lit with flat ambient light. "
            f"Absolutely NO shadows of any kind: no cast shadow, no drop shadow, no "
            f"contact or ground shadow beneath the feet, and no shadow on the "
            f"background. No floor or ground plane, so the background can be keyed out "
            f"cleanly. High quality, detailed.")


def build_klein_emotion_graph(
    *,
    pairs: List[Dict[str, Any]],
    prompts: List[str],
    seed: int,
    models: Dict[str, str],
    id_face: Optional[str] = None,
    pulid: Optional[Dict[str, Any]] = None,
    steps: int = KLEIN_POSE_STEPS,
    filename_prefix: str = "rbmn_vnccs/klein_emotions",
) -> Tuple[Dict[str, dict], Dict[str, str]]:
    """Klein face expression edit per (sprite x emotion) — CROP-AND-STITCH
    (v1.77.0 face-consistency rework of the full-frame KLEIN_INPAINT mirror).

    The old recipe regenerated the ENTIRE sprite from an empty latent and
    composited the face rectangle back: the face was repainted at sprite scale
    (tiny) with only the sprite itself as identity reference — the main source
    of expression-drift.  Now the worker samples ONLY an expanded face-context
    crop at ~1MP, anchored to a canonical identity face crop, and composites
    the region back into the sprite in-graph:

    masked-RGBA sprite (face transparent -> LoadImage MASK=face)
      -> ImageCrop / CropMask (app-side context box around the face)
      -> GrowMaskWithBlur(3, blur 2)      (soft composite seam)
      -> crop ImageScaleToTotalPixels(1MP) -> VAEEncode -> SetLatentNoiseMask
         (mask ResizeMask'd to the scaled crop's exact WxH)
    positive = CLIPTextEncode(emotion prompt)
      -> ReferenceLatent(masked crop context)
      -> ReferenceLatent(identity face crop)          [when ``id_face`` given]
    negative = ConditioningZeroOut, same reference chain
    CFGGuider(cfg 1, optionally PuLID-patched model) + euler +
    Flux2Scheduler(steps, scaled-crop WxH), latent = EMPTY at scaled-crop size
    -> VAEDecode -> ImageScale back to the crop's true size
    -> ImageCompositeMasked into the ORIGINAL sprite at the crop origin.

    ``pairs``: [{"masked": <uploaded masked rgba>,
                 "crop": {"x", "y", "w", "h"}}]   aligned with ``prompts``.
    ``id_face``: uploaded close-up crop of the character's canonical face
    (ACTIVE base version) — the identity anchor shared by every pair.
    ``pulid``: from resolve_pulid; patches the model with PuLID-Flux2 using
    ``id_face``.  All composites batch into one SaveImage tap.
    """
    if not pairs or len(pairs) != len(prompts):
        raise ValueError("pairs and prompts must align (and be non-empty)")
    api: Dict[str, dict] = {
        "u": {"class_type": "UNETLoader",
              "inputs": {"unet_name": models["unet"], "weight_dtype": "default"}},
        "c": {"class_type": "CLIPLoader",
              "inputs": {"clip_name": models["clip"], "type": "flux2", "device": "default"}},
        "v": {"class_type": "VAELoader", "inputs": {"vae_name": models["vae"]}},
    }
    if id_face:
        api["idf_load"] = {"class_type": "LoadImage", "inputs": {"image": id_face}}
        api["idf_scale"] = {"class_type": "ImageScaleToTotalPixels",
                            "inputs": {"image": ["idf_load", 0],
                                       "upscale_method": "lanczos", "megapixels": 1.0,
                                       "resolution_steps": 1}}
        api["idf_enc"] = {"class_type": "VAEEncode",
                          "inputs": {"pixels": ["idf_scale", 0], "vae": ["v", 0]}}
    model_ref = ["u", 0]
    if pulid and id_face:
        model_ref = _inject_pulid(api, model_ref, id_face, pulid)

    outs: List[str] = []
    for i, pair in enumerate(pairs):
        e = f"e{i}"
        crop = pair.get("crop") or {}
        cx, cy = int(crop.get("x", 0)), int(crop.get("y", 0))
        cw, ch = int(crop.get("w", 0)), int(crop.get("h", 0))
        if cw <= 0 or ch <= 0:
            raise ValueError(f"pair {i} has no crop box")
        api[f"{e}_src"] = {"class_type": "LoadImage", "inputs": {"image": pair["masked"]}}
        api[f"{e}_crop"] = {"class_type": "ImageCrop",
                            "inputs": {"image": [f"{e}_src", 0], "width": cw, "height": ch,
                                       "x": cx, "y": cy}}
        api[f"{e}_cmask"] = {"class_type": "CropMask",
                             "inputs": {"mask": [f"{e}_src", 1], "x": cx, "y": cy,
                                        "width": cw, "height": ch}}
        api[f"{e}_gmask"] = {"class_type": "GrowMaskWithBlur",
                             "inputs": {"mask": [f"{e}_cmask", 0], "expand": 3,
                                        "incremental_expandrate": 0, "tapered_corners": False,
                                        "flip_input": False, "blur_radius": 2, "lerp_alpha": 1,
                                        "decay_factor": 1, "fill_holes": False}}
        api[f"{e}_cscale"] = {"class_type": "ImageScaleToTotalPixels",
                              "inputs": {"image": [f"{e}_crop", 0],
                                         "upscale_method": "lanczos", "megapixels": 1.0,
                                         "resolution_steps": 1}}
        api[f"{e}_size"] = {"class_type": "GetImageSize", "inputs": {"image": [f"{e}_cscale", 0]}}
        api[f"{e}_rmask"] = {"class_type": "ResizeMask",
                             "inputs": {"mask": [f"{e}_gmask", 0],
                                        "width": [f"{e}_size", 0], "height": [f"{e}_size", 1],
                                        "keep_proportions": False, "upscale_method": "area",
                                        "crop": "disabled"}}
        api[f"{e}_srcenc"] = {"class_type": "VAEEncode",
                              "inputs": {"pixels": [f"{e}_cscale", 0], "vae": ["v", 0]}}
        api[f"{e}_nmask"] = {"class_type": "SetLatentNoiseMask",
                             "inputs": {"samples": [f"{e}_srcenc", 0], "mask": [f"{e}_rmask", 0]}}
        api[f"{e}_pos"] = {"class_type": "CLIPTextEncode",
                           "inputs": {"text": prompts[i], "clip": ["c", 0]}}
        api[f"{e}_neg"] = {"class_type": "ConditioningZeroOut",
                           "inputs": {"conditioning": [f"{e}_pos", 0]}}
        pos_cur, neg_cur = f"{e}_pos", f"{e}_neg"
        api[f"{e}_pr1"] = {"class_type": "ReferenceLatent",
                           "inputs": {"conditioning": [pos_cur, 0], "latent": [f"{e}_nmask", 0]}}
        api[f"{e}_nr1"] = {"class_type": "ReferenceLatent",
                           "inputs": {"conditioning": [neg_cur, 0], "latent": [f"{e}_nmask", 0]}}
        pos_cur, neg_cur = f"{e}_pr1", f"{e}_nr1"
        if id_face:
            api[f"{e}_pr2"] = {"class_type": "ReferenceLatent",
                               "inputs": {"conditioning": [pos_cur, 0], "latent": ["idf_enc", 0]}}
            api[f"{e}_nr2"] = {"class_type": "ReferenceLatent",
                               "inputs": {"conditioning": [neg_cur, 0], "latent": ["idf_enc", 0]}}
            pos_cur, neg_cur = f"{e}_pr2", f"{e}_nr2"
        api[f"{e}_sig"] = {"class_type": "Flux2Scheduler",
                           "inputs": {"steps": int(steps), "width": [f"{e}_size", 0],
                                      "height": [f"{e}_size", 1]}}
        api[f"{e}_lat"] = {"class_type": "EmptyFlux2LatentImage",
                           "inputs": {"width": [f"{e}_size", 0], "height": [f"{e}_size", 1],
                                      "batch_size": 1}}
        api[f"{e}_gd"] = {"class_type": "CFGGuider",
                          "inputs": {"model": list(model_ref), "positive": [pos_cur, 0],
                                     "negative": [neg_cur, 0], "cfg": KLEIN_POSE_CFG}}
        api[f"{e}_ns"] = {"class_type": "RandomNoise", "inputs": {"noise_seed": int(seed) + i}}
        api[f"{e}_sm"] = {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "euler"}}
        api[f"{e}_sc"] = {"class_type": "SamplerCustomAdvanced",
                          "inputs": {"noise": [f"{e}_ns", 0], "guider": [f"{e}_gd", 0],
                                     "sampler": [f"{e}_sm", 0], "sigmas": [f"{e}_sig", 0],
                                     "latent_image": [f"{e}_lat", 0]}}
        api[f"{e}_dec"] = {"class_type": "VAEDecode",
                           "inputs": {"samples": [f"{e}_sc", 0], "vae": ["v", 0]}}
        api[f"{e}_back"] = {"class_type": "ImageScale",
                            "inputs": {"image": [f"{e}_dec", 0], "upscale_method": "lanczos",
                                       "width": cw, "height": ch, "crop": "disabled"}}
        api[f"{e}_comp"] = {"class_type": "ImageCompositeMasked",
                            "inputs": {"destination": [f"{e}_src", 0], "source": [f"{e}_back", 0],
                                       "mask": [f"{e}_gmask", 0], "x": cx, "y": cy,
                                       "resize_source": False}}
        outs.append(f"{e}_comp")
    cur = outs[0]
    for j, nxt in enumerate(outs[1:]):
        bid = f"ebatch{j}"
        api[bid] = {"class_type": "ImageBatch",
                    "inputs": {"image1": [cur, 0], "image2": [nxt, 0]}}
        cur = bid
    api["save"] = {"class_type": "SaveImage",
                   "inputs": {"images": [cur, 0], "filename_prefix": filename_prefix}}
    return api, {"sprites": "save"}
