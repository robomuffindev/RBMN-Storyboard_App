"""Character Studio core service: shot plans, LoRA caption templates, exports.

Design: docs/CHARACTER_STUDIO.md.  Conventions come from the 2026 LoRA
dataset research (see the doc): kohya/SDXL = booru tags + `N_trigger class`
folders + TOML; ai-toolkit/FLUX-family = flat folder + natural-language
captions + trigger_word config.  Captioning runs on the existing Ollama
vision pool with prune-the-constant-traits rules baked into the prompts.
"""
from __future__ import annotations

import json
import logging
import shutil
import zipfile
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

_DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "character_studio"


def load_catalog(name: str) -> Any:
    """Load a bundled catalog JSON (character_tags / outfits / emotions / pose_presets)."""
    try:
        return json.loads((_DATA_DIR / f"{name}.json").read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning(f"character_studio: catalog {name} failed to load: {e}")
        return {}


def studio_root(project_dir: Path) -> Path:
    root = Path(project_dir) / "_character_studio"
    root.mkdir(parents=True, exist_ok=True)
    return root


# ── Art-style registry ────────────────────────────────────────────────────
# Canonical style presets. ``booru`` selects danbooru-tag subjects
# (1girl/1boy) for anime-family styles vs natural-language subjects for the
# rest. ``descriptor`` is appended to the base render prompt. UNKNOWN style
# values (custom free-text from the UI) are used verbatim as the descriptor,
# so the dropdown is never a hard limit. Frontend mirrors these keys in
# characterStudioStyles.ts — keep the values in sync.
DEFAULT_STYLE = "anime"

STUDIO_STYLES: dict[str, dict] = {
    "anime":          {"label": "Anime / Visual Novel",  "booru": True,  "descriptor": "anime style, clean cel shading, crisp lineart, vibrant colors"},
    "semi_realistic": {"label": "Semi-realistic",        "booru": False, "descriptor": "semi-realistic digital painting, painterly rendering, detailed shading"},
    "photorealistic": {"label": "Photorealistic",        "booru": False, "descriptor": "photorealistic, photograph, natural skin texture, realistic lighting, sharp focus"},
    "3d_render":      {"label": "3D render",             "booru": False, "descriptor": "stylized 3D character render, physically based rendering, soft global illumination"},
    "comic":          {"label": "Western comic",         "booru": True,  "descriptor": "western comic book art, bold inking, halftone shading"},
    "storybook":      {"label": "Storybook illustration","booru": False, "descriptor": "children's storybook illustration, soft watercolor, gentle outlines"},
}


def style_key_of(info: Optional[dict], style: str = "") -> str:
    """Resolve the effective style key: explicit arg > info['style'] > default."""
    return (style or (info or {}).get("style") or DEFAULT_STYLE).strip() or DEFAULT_STYLE


def style_is_booru(style_key: str) -> bool:
    return STUDIO_STYLES.get(style_key, {}).get("booru", False)


def style_descriptor(style_key: str) -> str:
    meta = STUDIO_STYLES.get(style_key)
    if meta:
        return meta.get("descriptor", "")
    # Custom free-text style — use the raw value as the descriptor.
    return style_key if style_key and style_key != DEFAULT_STYLE else ""


def style_label(style_key: str) -> str:
    return STUDIO_STYLES.get(style_key, {}).get("label") or style_key


def _subject_tokens(style_key: str, sex: str) -> str:
    sx = (sex or "").lower()
    if style_is_booru(style_key):
        return {"male": "1boy, solo", "female": "1girl, solo"}.get(sx, "solo character")
    return {"male": "a man, solo", "female": "a woman, solo"}.get(sx, "a person, solo")


# ── Base render prompt ────────────────────────────────────────────────────
def clothing_phrase(sex: str, nsfw: bool) -> str:
    """VNCCS's base-render clothing default: SFW = underwear, NSFW = nude.
    Both produce a clothing-READY base to layer costumes over (matches
    vnccs/nodes/character_creator.py). Kept verbatim for parity."""
    male = (sex or "").lower().startswith("m")
    if nsfw:
        return "(naked, nude, penis)" if male else "(naked, nude, vagina, nipples)"
    return "(bare chest, wear white boxers)" if male else "(wear white bra and panties)"


def build_base_prompt(info: dict, kind: str = "character", extra: str = "", style: str = "",
                      nsfw=None) -> str:
    """Compose the base render prompt from the VNCCS-style tag sheet.

    Written for our first-pass generators (Z-Image / Krea2): concrete prose-ish
    tag flow, full body, neutral pose, plain background — the canonical LoRA
    anchor image and the Klein edit-ref source.
    """
    style_key = style_key_of(info, style)
    if kind == "item":
        parts = [
            info.get("additional_details") or info.get("body") or "an object",
            "centered, full object visible",
            "plain neutral background, soft even studio lighting, high detail",
        ]
        _sd = style_descriptor(style_key)
        if _sd:
            parts.append(_sd)
        if extra:
            parts.append(extra)
        return ", ".join(x for x in parts if x)

    sex = (info.get("sex") or "").strip()
    subject = _subject_tokens(style_key, sex)
    parts = [subject]
    if info.get("age"):
        parts.append(f"{info['age']} years old")
    for key, suffix in (("race", ""), ("skin_color", " skin"), ("hair", " hair"), ("eyes", " eyes"),
                        ("face", ""), ("body", "")):
        v = (info.get(key) or "").strip()
        if v:
            parts.append(f"{v}{suffix}")
    if info.get("additional_details"):
        parts.append(str(info["additional_details"]))
    _nsfw = bool(info.get("nsfw")) if nsfw is None else bool(nsfw)
    outfit = (info.get("outfit") or "").strip()
    parts.append(outfit if outfit else clothing_phrase(sex, _nsfw))
    parts.extend([
        "standing straight, arms relaxed at sides, neutral expression",
        "front view, full body from head to feet",
        f"plain {info.get('background_color') or 'light gray'} background",
        "even studio lighting, high detail",
    ])
    _sd = style_descriptor(style_key)
    if _sd:
        parts.append(_sd)
    if extra:
        parts.append(extra)
    return ", ".join(x for x in parts if x)


# ── Shot plan ─────────────────────────────────────────────────────────────
def default_shot_plan(info: dict, kind: str = "character") -> list[dict]:
    """Editable default shot list for a LoRA-ready dataset.

    Research consensus: 15-40 images; angles incl. >=30% non-front, profile and
    back present; portrait-heavy with a full-body minority; expressions vary;
    some background/lighting variation.  Every shot is a Klein edit instruction
    against Image 1 (the base render).  Klein refs use positional "Image 1"
    language — never character names.
    """
    kf = "Using the character in Image 1, render the exact same character with the same face, hair, outfit and art style, both eyes the SAME color, identical facial features"
    if kind == "item":
        ki = "Using the object in Image 1, render the exact same object with identical shape, materials and details"
        return [
            {"id": "front", "label": "Front", "instruction": f"{ki}, viewed straight from the front, centered, plain background.", "enabled": True},
            {"id": "back", "label": "Back", "instruction": f"{ki}, viewed from behind, plain background.", "enabled": True},
            {"id": "three_quarter_l", "label": "3/4 left", "instruction": f"{ki}, viewed from a three-quarter left angle, plain background.", "enabled": True},
            {"id": "three_quarter_r", "label": "3/4 right", "instruction": f"{ki}, viewed from a three-quarter right angle, plain background.", "enabled": True},
            {"id": "top", "label": "Top-down", "instruction": f"{ki}, viewed from above at a high angle, plain background.", "enabled": True},
            {"id": "closeup_detail", "label": "Detail close-up", "instruction": f"{ki}, extreme close-up on its most distinctive detail, showing texture and material.", "enabled": True},
            {"id": "closeup_detail2", "label": "Detail close-up 2", "instruction": f"{ki}, close-up on a different part of the object, showing texture and material.", "enabled": True},
            {"id": "in_context", "label": "In use / in scene", "instruction": f"{ki}, shown in a realistic scene being used in its natural context.", "enabled": True},
            {"id": "in_context2", "label": "Second context", "instruction": f"{ki}, shown in a different realistic environment.", "enabled": True},
            {"id": "lighting_warm", "label": "Warm lighting", "instruction": f"{ki}, plain background, warm golden-hour lighting from the side.", "enabled": True},
            {"id": "lighting_cool", "label": "Cool lighting", "instruction": f"{ki}, plain background, cool blue evening lighting.", "enabled": True},
        ]

    return [
        # angles — full body (the base render is the front full-body anchor)
        {"id": "three_quarter_l", "label": "3/4 left, full body", "instruction": f"{kf}, turned to a three-quarter left view, full body from head to feet, neutral expression, plain background.", "enabled": True},
        {"id": "three_quarter_r", "label": "3/4 right, full body", "instruction": f"{kf}, turned to a three-quarter right view, full body from head to feet, neutral expression, plain background.", "enabled": True},
        {"id": "profile_l", "label": "Left profile, full body", "instruction": f"{kf}, in exact left side profile, full body from head to feet, neutral expression, plain background.", "enabled": True},
        {"id": "back", "label": "Back view, full body", "instruction": f"{kf}, seen fully from behind showing the back of the head and outfit, full body, plain background.", "enabled": True},
        # portraits / framings
        {"id": "portrait_front", "label": "Portrait, front", "instruction": f"{kf}, close-up head-and-shoulders portrait facing the camera, neutral expression, plain background.", "enabled": True},
        {"id": "portrait_34", "label": "Portrait, 3/4", "instruction": f"{kf}, close-up head-and-shoulders portrait at a three-quarter angle, plain background.", "enabled": True},
        {"id": "portrait_profile", "label": "Portrait, profile", "instruction": f"{kf}, close-up head-and-shoulders portrait in exact side profile, plain background.", "enabled": True},
        {"id": "upper_front", "label": "Upper body, front", "instruction": f"{kf}, waist-up shot facing the camera, arms visible, neutral expression, plain background.", "enabled": True},
        {"id": "upper_34", "label": "Upper body, 3/4", "instruction": f"{kf}, waist-up shot at a three-quarter angle, plain background.", "enabled": True},
        # expressions
        {"id": "expr_smile", "label": "Smiling portrait", "instruction": f"{kf}, close-up portrait, smiling warmly with a happy expression, plain background.", "enabled": True},
        {"id": "expr_serious", "label": "Serious portrait", "instruction": f"{kf}, close-up portrait with an intense serious expression, brows slightly furrowed, plain background.", "enabled": True},
        {"id": "expr_surprised", "label": "Surprised portrait", "instruction": f"{kf}, close-up portrait with a surprised expression, eyes wide and mouth slightly open, plain background.", "enabled": True},
        # pose + background/lighting variation
        {"id": "pose_action", "label": "Action pose", "instruction": f"{kf}, in a dynamic walking pose mid-stride, full body, plain background.", "enabled": True},
        {"id": "bg_outdoor", "label": "Outdoor scene", "instruction": f"{kf}, upper body, standing outdoors on a city street with natural daylight.", "enabled": True},
        {"id": "bg_indoor", "label": "Indoor scene", "instruction": f"{kf}, upper body, in a cozy indoor room with warm evening lighting.", "enabled": True},
    ]


# ── Caption templates ─────────────────────────────────────────────────────
_QUALITY_PREFIX = {
    "illustrious": "masterpiece, best quality",
    "noobai": "masterpiece, best quality, newest, absurdres",
    "pony": "score_9, score_8_up, score_7_up",
    "none": "",
}


def constant_traits(info: dict) -> list[str]:
    """Traits constant across every dataset image — pruned from captions so
    they fuse into the trigger word (the prune-what-you-want-learned rule)."""
    out = []
    for key, suffix in (("race", ""), ("skin_color", " skin"), ("hair", " hair"),
                        ("eyes", " eyes"), ("face", ""), ("body", "")):
        v = (info.get(key) or "").strip()
        if v:
            out.append(f"{v}{suffix}")
    return out


def build_caption_prompt(style: str, trigger: str, class_word: str, info: dict,
                         kind: str = "character", quality_family: str = "illustrious") -> str:
    """Vision-model prompt that produces ONE ready-to-save caption line."""
    const = "; ".join(constant_traits(info)) or "none listed"
    if style == "tags":
        prefix = _QUALITY_PREFIX.get(quality_family, "")
        rules = (
            f"Write ONE line of comma-separated booru-style tags for this training image. "
            f"Start EXACTLY with: {trigger}, {(('1girl' if (info.get('sex') or '').lower()=='female' else '1boy' if (info.get('sex') or '').lower()=='male' else class_word) if style_is_booru(style_key_of(info)) else class_word)}"
            + (f", {prefix}" if prefix else "") + ". "
            f"Then tag ONLY what VARIES in this image: framing (portrait / upper body / full body), "
            f"viewing angle (front / three-quarter / profile / from behind), pose, expression, "
            f"background, lighting, and any clothing or props visible. "
            f"Use spaces inside tags (long hair, not long_hair). "
            f"DO NOT tag these constant traits (they are learned by the trigger word): {const}. "
            f"No sentences, no quotes, no explanations — output the single tag line only."
        )
    else:
        rules = (
            f"Write ONE natural-language caption (2-4 sentences) for this LoRA training image. "
            f"The very first word must be the trigger word {trigger}, used as the subject's name "
            f"(e.g. \"{trigger}, a {class_word}, is ...\"). "
            f"Describe ONLY what varies in this image: camera framing and angle, pose, facial "
            f"expression, visible clothing, background/setting, and lighting. "
            f"DO NOT describe these constant traits — they belong to the trigger word: {const}. "
            f"Plain prose, no lists, no quotes, no preamble — output the caption only."
        )
    if kind == "item":
        rules = rules.replace("the subject's name", "the object's name").replace(
            "facial expression", "state and context of use")
    return rules


# ── Export builders ───────────────────────────────────────────────────────
def _write_kohya(ds_dir: Path, images: dict[str, Path], captions: dict, trigger: str,
                 class_word: str, repeats: int) -> Path:
    root = ds_dir / "kohya"
    img_dir = root / f"{max(1, repeats)}_{trigger} {class_word}".strip()
    img_dir.mkdir(parents=True, exist_ok=True)
    for name, src in images.items():
        dst = img_dir / f"{name}{src.suffix.lower()}"
        shutil.copy2(src, dst)
        cap = ((captions.get(name) or {}).get("tags") or "").strip()
        dst.with_suffix(".txt").write_text(cap, encoding="utf-8")
    (root / "dataset_config.toml").write_text(
        "# kohya_ss / sd-scripts dataset config (SDXL-family)\n"
        "[general]\nenable_bucket = true\nbucket_reso_steps = 64\n"
        "min_bucket_reso = 512\nmax_bucket_reso = 2048\n\n"
        "[[datasets]]\nresolution = 1024\n\n"
        "  [[datasets.subsets]]\n"
        f"  image_dir = \"./{img_dir.name}\"\n"
        f"  class_tokens = \"{trigger} {class_word}\"\n"
        f"  num_repeats = {max(1, repeats)}\n",
        encoding="utf-8")
    (root / "README.txt").write_text(
        "SDXL-family (kohya_ss / sd-scripts) LoRA dataset.\n"
        f"- Folder '{img_dir.name}' follows the legacy '<repeats>_<trigger> <class>' GUI convention.\n"
        "- dataset_config.toml is the modern equivalent for CLI use (point --dataset_config at it).\n"
        f"- Trigger word: {trigger} (always the first tag of every caption).\n"
        "- Constant identity traits are intentionally NOT tagged — they fuse into the trigger.\n",
        encoding="utf-8")
    return root


def _write_ai_toolkit(ds_dir: Path, images: dict[str, Path], captions: dict, trigger: str,
                      class_word: str) -> Path:
    root = ds_dir / "ai-toolkit"
    img_dir = root / "images"
    img_dir.mkdir(parents=True, exist_ok=True)
    for name, src in images.items():
        dst = img_dir / f"{name}{src.suffix.lower()}"
        shutil.copy2(src, dst)
        cap = ((captions.get(name) or {}).get("natural") or "").strip()
        dst.with_suffix(".txt").write_text(cap, encoding="utf-8")
    (root / "config.yaml").write_text(
        "# ai-toolkit (ostris) job config skeleton — FLUX/Qwen-family LoRA\n"
        "# Fill model.name_or_path for your target (FLUX.2 Klein / Qwen-Image / etc.)\n"
        "job: extension\nconfig:\n"
        f"  name: {trigger}_lora\n"
        "  process:\n"
        "    - type: sd_trainer\n"
        f"      trigger_word: \"{trigger}\"\n"
        "      network:\n        type: lora\n        linear: 32\n        linear_alpha: 32\n"
        "      save:\n        dtype: float16\n        save_every: 250\n"
        "      datasets:\n"
        "        - folder_path: ./images\n"
        "          caption_ext: txt\n"
        "          caption_dropout_rate: 0.05\n"
        "          resolution: [512, 768, 1024]\n"
        "      train:\n        batch_size: 1\n        steps: 1500\n        lr: 1e-4\n"
        "        optimizer: adamw8bit\n        noise_scheduler: flowmatch\n",
        encoding="utf-8")
    (root / "README.txt").write_text(
        "FLUX/Qwen-family (ai-toolkit) LoRA dataset.\n"
        "- Flat images/ folder with natural-language .txt captions (same basename).\n"
        f"- Trigger word: {trigger} — first word of every caption AND set as trigger_word in config.yaml.\n"
        "- config.yaml is a starting skeleton: set the base model path, then run ai-toolkit on it.\n",
        encoding="utf-8")
    return root


def export_dataset(ds_dir: Path, images: dict[str, Path], captions: dict, target: str,
                   trigger: str, class_word: str, repeats: int = 10) -> Path:
    """Build the requested layout(s) under ds_dir and zip the result. Returns zip path."""
    ds_dir.mkdir(parents=True, exist_ok=True)
    if target in ("kohya", "both"):
        _write_kohya(ds_dir, images, captions, trigger, class_word, repeats)
    if target in ("ai_toolkit", "both"):
        _write_ai_toolkit(ds_dir, images, captions, trigger, class_word)
    zip_path = ds_dir / "dataset.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for sub in ("kohya", "ai-toolkit"):
            d = ds_dir / sub
            if d.exists():
                for f in sorted(d.rglob("*")):
                    if f.is_file():
                        zf.write(f, f.relative_to(ds_dir))
    return zip_path
