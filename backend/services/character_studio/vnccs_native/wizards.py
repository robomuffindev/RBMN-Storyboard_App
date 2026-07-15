"""VNCCS Native LLM wizards — Character Wizard, Clothes Wizard, Cloner Analyze.

The real VNCCS nodes expose three LLM helpers as HTTP routes on the ComfyUI
host (``/vnccs/character_wizard``, ``/vnccs/clothes_wizard``,
``/vnccs/cloner_auto_generate``).  Each one spins up a Qwen2.5-VL GGUF via
llama-cpp-python *on the host* per request (auto-downloading the ~5 GB model on
first use) and returns structured JSON that fills the character / costume form.

Strategy ("thin app over VNCCS", but resilient):
  1. HOST FIRST — relay to the real VNCCS wizard route.  Identical model,
     identical prompts, identical tag catalog and post-processing: literally
     the same result as the VNCCS panel in ComfyUI.
  2. OLLAMA FALLBACK — if the host wizard fails (llama-cpp-python missing,
     GGUF download failure, timeout), rerun with the app's own Ollama using
     the VERBATIM prompts below (copied from vnccs/nodes/*.py) so the output
     shape and instructions match exactly; only the underlying LLM differs.
     The character wizard's tag catalog is fetched live from the host's
     ``/vnccs/get_tags`` (already whitelisted) so even the fallback prompt
     carries the same tag options.

Callers can force a backend with ``backend="host"`` / ``"ollama"``; the
default is ``"auto"`` (host, then Ollama).
"""

from __future__ import annotations

import base64
import json
import logging
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

# Wizards load a 7B GGUF on the host per request — generous timeout.  First-ever
# call may also download the model; VNCCS surfaces that as MODEL_DOWNLOAD status
# routes, but the wizard route itself can block on it too.
WIZARD_HOST_TIMEOUT = 600

# --- verbatim from vnccs/nodes/character_creator_v2.py -----------------------
SKIN_COLOR_OPTIONS: List[str] = [
    "light skin",
    "fair skin",
    "pale skin",
    "tan skin",
    "dark skin",
    "brown skin",
    "olive skin",
    "blue skin",
    "green skin",
    "grey skin",
]

CHARACTER_FIELD_KEYS = (
    "race", "skin_color", "hair", "eyes", "face", "body", "additional_details",
)
CLONE_EXTRA_KEYS = ("aesthetics", "height", "worn_articles")
CLOTHES_KEYS = ("top", "bottom", "shoes", "head", "face")


def extract_character_tag_options(tags_data: Any) -> Dict[str, List[str]]:
    """Verbatim port of VNCCS ``_extract_character_tag_options``."""
    tags = tags_data.get("tags", {}) if isinstance(tags_data, dict) else {}

    def collect(value: Any) -> List[str]:
        items: List[str] = []
        if isinstance(value, list):
            for item in value:
                if isinstance(item, dict) and item.get("tag"):
                    items.append(str(item["tag"]))
        elif isinstance(value, dict):
            for sub_value in value.values():
                items.extend(collect(sub_value))
        return items

    return {
        "race": collect(tags.get("races", [])),
        "hair": collect(tags.get("hair_color", [])) + collect(tags.get("hairstyles", [])),
        "eyes": collect(tags.get("eyes", {})),
        "body": collect(tags.get("breast_size", [])),
        "additional_details": collect(tags.get("details", [])),
    }


def character_wizard_prompts(user_description: str,
                             tag_options: Optional[Dict[str, List[str]]] = None,
                             ) -> Tuple[str, str]:
    """(system, user) — verbatim VNCCS Character Wizard prompt."""
    system_prompt = (
        "You are a professional anime/game character designer. "
        "Convert broad character ideas into concise structured character fields. "
        "Output valid JSON only."
    )
    user_prompt = f"""
Create a character from this abstract idea:
{user_description}

Prefer these exact existing tags when they fit. Only invent a different tag or phrase if no listed tag matches the character:
{json.dumps(tag_options or {}, ensure_ascii=False)}

Use one of these skin_color values only when the user's idea explicitly mentions skin tone or complexion:
{json.dumps(SKIN_COLOR_OPTIONS, ensure_ascii=False)}

Return a raw JSON object with exactly these keys:
- sex: "male" or "female"
- age: integer from 1 to 100
- race
- skin_color
- body
- face
- hair
- eyes
- additional_details

Rules:
- Use comma-separated prompt fragments for text fields.
- The race field is for species/fantasy traits only. For normal humans set race to "human".
- Never put ethnicity, nationality, profession, role, clothing, or archetype in race. Examples of invalid race values: "afro_student", "black student", "asian girl", "teacher".
- Put skin tone in skin_color, not race. "afro", "African", "African-American", "black", or similar means skin_color should be "dark skin" unless another skin tone is explicit.
- For body, always provide a visible body/build descriptor. Use listed breast/chest tags when relevant, and add concise build phrases like "slim build", "average build", "athletic build" when useful.
- For race, hair, eyes, body and additional_details, prefer exact tags from the provided tag list when they fit.
- For skin_color, do not guess a default. Use an empty string unless the user's idea explicitly mentions skin tone, complexion, or non-human skin color.
- Do not use "pale skin" as a fallback.
- Do not describe clothing or outfit items.
- Do not add background, camera, pose, quality tags, style tags, nsfw, nudity, sex acts, or negative prompts.
- Keep fields practical for the existing character form.
- If a field is not needed, use an empty string.
- Set sex and age explicitly based on the user's description. If unspecified, infer a reasonable adult character.

Example:
{{
  "sex": "female",
  "age": 24,
  "race": "demon_girl, demon_horns",
  "skin_color": "",
  "body": "medium_breasts, slim waist",
  "face": "mole_under_eye, sharp features",
  "hair": "white_hair, long_hair, blunt_bangs",
  "eyes": "red_eyes, glowing",
  "additional_details": "tattoo, black_nails"
}}
"""
    return system_prompt, user_prompt


# --- verbatim from vnccs/nodes/clothes_designer.py ---------------------------
def clothes_wizard_prompts(user_description: str) -> Tuple[str, str]:
    """(system, user) — verbatim VNCCS Clothes Wizard prompt."""
    system_prompt = (
        "You are a professional anime/game character costume designer. "
        "Convert broad outfit ideas into concrete, visual clothing prompts. "
        "Output valid JSON only."
    )
    user_prompt = f"""
Expand this abstract clothing idea into detailed outfit parts:
{user_description}

Return a raw JSON object with exactly these string keys:
- top
- bottom
- shoes
- head
- face (ONLY wearable face items/accessories)

Rules:
- Each value must be a detailed visual description suitable for image generation.
- Do not repeat the same abstract phrase from the user.
- Describe materials, colors, shape, trims, accessories, fit, and distinctive details.
- If a category is not needed, use an empty string.
- Keep descriptions clothing-focused.
- The "face" field is NOT for facial expression, makeup, blush, eyeshadow, lipstick, skin, cheeks, or facial features.
- Use "face" only for wearable/accessory items placed on the face, such as glasses, sunglasses, goggles, mask, veil, eyepatch, respirator, scarf over mouth, piercings, stickers, or temporary tattoos.
- If there is no wearable face item, set "face" to an empty string.
- Do not describe the body, pose, background, camera, quality tags, nudity, sex acts, facial expression, makeup, blush, eyeshadow, lipstick, skin, or cheeks.

Example for "Santa Claus costume":
{{
  "top": "red velvet Santa coat with thick white fur trim on cuffs, hem and front opening, black leather belt with square gold buckle, long sleeves, festive winter fabric texture",
  "bottom": "matching red velvet trousers with white fur cuffs, fitted but comfortable costume pants",
  "shoes": "black polished leather boots with rounded toes and folded cuffs",
  "head": "red Santa hat with white fur brim and white pom-pom, slightly tilted",
  "face": ""
}}
"""
    return system_prompt, user_prompt


# --- verbatim from vnccs/nodes/character_cloner.py (vision analyze) ----------
CLONE_ANALYZE_SYSTEM = (
    "You are a character description specialist. Analyze the image and output valid JSON only."
)
CLONE_ANALYZE_INSTRUCTION = """Analyze the image and strictly output valid JSON. 
Use Danbooru-style tags for descriptions.

Keys:
- sex (string: 'male' or 'female')
- age (int: estimated number)
- race (string: e.g. 'human', 'elf', 'cyborg')
- skin_color (string: choose only one clearly visible value from: light skin, fair skin, pale skin, tan skin, dark skin, brown skin, olive skin, blue skin, green skin, grey skin)
- hair (string: comma-separated tags for color and style, e.g. 'blue hair, long hair, ponytail')
- eyes (string: comma-separated tags for color and shape, e.g. 'green eyes, tsurime')
- face (string: tags for features, e.g. 'blush', 'scars', 'makeup')
- body (string: build & proportions tags, e.g. 'slim', 'athletic build', 'broad shoulders', 'narrow waist', 'long legs', 'petite', 'curvy')
- additional_details (string: tags for clothing, accessories, pose, e.g. 'wearing suit, sitting, holding sword')
- aesthetics (string: high quality tags e.g. 'masterpiece, best quality, anime style')
- height (string: apparent stature descriptor, e.g. 'tall', 'petite', 'average height'; add an estimated range like "about 5'6-5'8" only if confidently judgeable from the framing, otherwise just the descriptor or "")
- nsfw (boolean)

Rules:
- Determine skin_color from visible skin only.
- Use "pale skin" only for unusually pale/very light skin, never as a generic default.
- If skin is hidden, heavily stylized by lighting, or uncertain, set skin_color to "".

Structure the response as a raw JSON object. Do not output the word 'tag' as a value. DESCRIBE the character."""


# Two-stage clone analyze: the VISION model DESCRIBES each image in prose (which
# local vision models do reliably), then a TEXT model SYNTHESISES the structured
# fields from those descriptions (which text models do reliably) — far more robust
# than asking a local vision model to emit strict JSON.
CLONE_VISION_DESCRIBE_SYSTEM = (
    "You are a visual character analyst. Describe ONLY what is visibly present in "
    "the image, concisely and accurately. Never invent details you cannot see."
)
CLONE_VISION_DESCRIBE = """Describe the person/character in this image for a character reference sheet.
Cover ONLY what is clearly visible: apparent sex; approximate age; race or species; skin tone;
hair (colour, length, style); eyes (colour, shape); face (distinguishing features, makeup, marks);
body build and proportions (overall build, shoulders, chest, waist, hips, apparent height/stature);
and clothing / accessories. If a feature is not visible or you are unsure, say "not visible".
Write 3-6 plain sentences. Do not output JSON, lists, or tags — just the description."""

CLONE_SYNTHESIZE_SYSTEM = (
    "You compile ONE character sheet from several written descriptions of the SAME "
    "character. Output valid JSON only."
)


def build_clone_synthesis_prompt(described: "List[tuple]") -> str:
    """Build the text-LLM prompt that turns per-image descriptions into the
    structured character fields.  ``described`` is a list of (role, text) where
    role is 'face' | 'body' | 'full'."""
    lines = []
    for i, (role, text) in enumerate(described, 1):
        hint = ({"face": "close-up of the face",
                 "body": "body / full-body shot",
                 "full": "full view (face + body)"}.get(str(role).lower(), "reference image"))
        lines.append(f"Image {i} ({hint}):\n{str(text).strip()}")
    joined = "\n\n".join(lines)
    return (
        "Below are descriptions of the SAME character from different reference images. "
        "Combine them into ONE character sheet. Prefer FACE/hair/eyes details from the "
        "close-up (face) descriptions, and body build / proportions / height from the "
        "body or full-body descriptions. If descriptions conflict, pick the most "
        "confident and detailed. Ignore anything marked \"not visible\".\n\n"
        f"{joined}\n\n"
        "Output strict JSON with EXACTLY these keys (use Danbooru-style comma tags "
        "for the descriptive fields; empty string \"\" when unknown):\n"
        "- sex (\"male\" or \"female\")\n"
        "- age (integer)\n"
        "- race (e.g. \"human\", \"elf\")\n"
        "- skin_color (one of: light skin, fair skin, pale skin, tan skin, dark skin, "
        "brown skin, olive skin, blue skin, green skin, grey skin; \"\" if unclear)\n"
        "- hair (colour + style tags)\n"
        "- eyes (colour + shape tags)\n"
        "- face (feature tags: marks, makeup, etc.)\n"
        "- body (build & proportion tags: build, shoulders, chest, waist, hips, legs)\n"
        "- additional_details (clothing / accessory tags)\n"
        "- aesthetics (quality/style tags, e.g. \"masterpiece, best quality\")\n"
        "- height (stature descriptor, e.g. \"tall\", \"average height\", or \"\")\n"
        "- worn_articles (comma list of the specific JEWELRY and CLOTHING items the "
        "character is WEARING that a strip / cleanup pass should REMOVE -- e.g. "
        "\"necklace, bracelet, earrings, watch, shirt, jacket, jeans, shoes\"; list only "
        "items actually visible; \"\" if none or nude)\n"
        "- nsfw (boolean)\n"
        "Output the raw JSON object only.")


# --------------------------------------------------------------------------- #
# JSON parsing / normalization (mirrors VNCCS ``_parse_character_wizard_json``)
# --------------------------------------------------------------------------- #
def _extract_json_obj(content: str) -> Optional[dict]:
    data: Any = None
    json_str = (content or "").strip()
    try:
        if "```json" in json_str:
            json_str = json_str.split("```json", 1)[1].split("```", 1)[0]
        elif "```" in json_str:
            json_str = json_str.split("```", 1)[1].split("```", 1)[0]
        else:
            match = re.search(r"\{.*\}", json_str, re.DOTALL)
            if match:
                json_str = match.group(0)
        data = json.loads(json_str.strip())
    except Exception:
        # last resort: first {...} block even inside fences
        try:
            match = re.search(r"\{.*\}", content or "", re.DOTALL)
            data = json.loads(match.group(0)) if match else None
        except Exception:
            data = None
    if isinstance(data, list) and data and isinstance(data[0], dict):
        data = data[0]
    return data if isinstance(data, dict) else None


def _coerce_str(value: Any) -> str:
    if isinstance(value, list):
        return ", ".join(str(item).strip() for item in value if str(item).strip())
    if isinstance(value, str):
        return value.strip()
    return str(value).strip() if value is not None else ""


def normalize_character_fields(data: dict, include_clone_extras: bool = False) -> dict:
    """Same normalization VNCCS applies: comma-joined strings, sex/age clamps."""
    result: Dict[str, Any] = {}
    keys = list(CHARACTER_FIELD_KEYS) + (list(CLONE_EXTRA_KEYS) if include_clone_extras else [])
    for key in keys:
        result[key] = _coerce_str(data.get(key, ""))
    sex = str(data.get("sex", "female")).strip().lower()
    result["sex"] = "male" if sex.startswith("m") else "female"
    try:
        age = int(float(data.get("age", 18)))
    except Exception:
        age = 18
    result["age"] = max(1, min(100, age))
    if include_clone_extras:
        nsfw = data.get("nsfw", False)
        if isinstance(nsfw, str):
            nsfw = nsfw.strip().lower() in ("true", "yes", "1")
        result["nsfw"] = bool(nsfw)
    return result


def normalize_clothes_fields(data: dict) -> dict:
    return {key: _coerce_str(data.get(key, "")) for key in CLOTHES_KEYS}


def parse_character_wizard_output(content: str) -> Optional[dict]:
    data = _extract_json_obj(content)
    return normalize_character_fields(data) if data else None


def parse_clone_analyze_output(content: str) -> Optional[dict]:
    data = _extract_json_obj(content)
    return normalize_character_fields(data, include_clone_extras=True) if data else None


def parse_clothes_wizard_output(content: str) -> Optional[dict]:
    data = _extract_json_obj(content)
    return normalize_clothes_fields(data) if data else None


# --------------------------------------------------------------------------- #
# Ollama backends (fallback / forced) — same API shape as services/llm/vision
# --------------------------------------------------------------------------- #
def _normalize_urls(urls: Any) -> List[str]:
    if isinstance(urls, str):
        urls = [urls]
    out: List[str] = []
    for u in urls or []:
        u = str(u or "").strip().rstrip("/")
        if u:
            out.append(u)
    return out


def ollama_chat_sync(ollama_urls: Sequence[str] | str, model: str,
                     system_prompt: str, user_prompt: str,
                     images_b64: Optional[List[str]] = None,
                     temperature: float = 0.3,
                     timeout: float = 300.0,
                     json_format: bool = True) -> Optional[str]:
    """Plain Ollama /api/chat call (optionally multimodal). Never raises.
    ``json_format`` toggles Ollama's structured-JSON mode — set False for a
    free-form prose reply (e.g. a vision DESCRIBE pass), True to force JSON."""
    urls = _normalize_urls(ollama_urls)
    if not urls or not model:
        return None
    user_msg: Dict[str, Any] = {"role": "user", "content": user_prompt}
    if images_b64:
        user_msg["images"] = images_b64
    body = {
        "model": model,
        "messages": [{"role": "system", "content": system_prompt}, user_msg],
        "stream": False,
        "options": {"temperature": temperature},
    }
    if json_format:
        body["format"] = "json"  # Ollama structured mode — matches "Output valid JSON only"
    import httpx
    last_err: Any = None
    for url in urls:
        try:
            r = httpx.post(f"{url}/api/chat", json=body, timeout=timeout)
            r.raise_for_status()
            data = r.json()
            content = ((data.get("message") or {}).get("content") or "").strip()
            if content:
                return content
            last_err = "empty response"
        except Exception as e:  # noqa: BLE001 — pool fallthrough
            last_err = e
            continue
    logger.warning(f"vnccs wizard: ollama chat failed on all {len(urls)} server(s): {last_err}")
    return None


def image_bytes_to_b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")
