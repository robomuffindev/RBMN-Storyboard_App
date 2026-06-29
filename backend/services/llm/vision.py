"""Ollama vision-model image description.

Describes a reference image so the prompt-enhancer LLM understands what the image
actually contains (subjects, appearance, setting, composition, colors, lighting)
— more reliable than the source prompt alone, and the only signal for images
imported from outside the app.

Kept deliberately small + fast: one Ollama /api/chat call per image, low
temperature, a tight factual caption prompt, and the result is cached on the
asset so it's generated at most once per image.
"""
from __future__ import annotations

import base64
import logging
from pathlib import Path
from typing import Optional, Sequence

logger = logging.getLogger(__name__)

# Tight, factual caption for image-generation reference use. No mood/story
# interpretation — just what's visibly there, so the enhancer can describe the
# referenced subject accurately.
DESCRIBE_PROMPT = (
    "Describe this image factually and concisely so it can be used as a REFERENCE "
    "for image generation. Cover, in order: the main subject(s) and their visible "
    "appearance (apparent age, build, hair, skin tone, clothing, distinctive "
    "features), the setting/background, the composition and framing (shot type, "
    "angle), the dominant colors, and the lighting. Be specific and literal — only "
    "describe what is visibly present; do not invent details or interpret mood. "
    "Respond with 2-4 plain sentences, no preamble, no markdown."
)


def _normalize_urls(urls) -> list[str]:
    if not urls:
        return []
    if isinstance(urls, str):
        urls = [urls]
    return [u.rstrip("/") for u in urls if u and str(u).strip()]


def describe_image_sync(
    image_path: str | Path,
    ollama_urls: Sequence[str] | str,
    model: str,
    timeout: float = 90.0,
) -> Optional[str]:
    """Describe an image via an Ollama vision model. Returns the caption or None.

    Tries each URL in the pool until one succeeds. Never raises — returns None on
    any failure so callers degrade gracefully (no vision description).
    """
    urls = _normalize_urls(ollama_urls)
    if not urls or not model:
        return None
    p = Path(image_path)
    if not p.exists():
        logger.warning(f"vision: image not found: {p}")
        return None
    try:
        b64 = base64.b64encode(p.read_bytes()).decode("ascii")
    except Exception as e:
        logger.warning(f"vision: could not read image {p}: {e}")
        return None

    body = {
        "model": model,
        "messages": [{"role": "user", "content": DESCRIBE_PROMPT, "images": [b64]}],
        "stream": False,
        "options": {"temperature": 0.2},
    }

    import httpx

    last_err = None
    for url in urls:
        try:
            r = httpx.post(f"{url}/api/chat", json=body, timeout=timeout)
            r.raise_for_status()
            data = r.json()
            content = ((data.get("message") or {}).get("content") or "").strip()
            if content:
                return content
            last_err = "empty response"
        except Exception as e:
            last_err = e
            continue
    logger.warning(f"vision: describe failed on all {len(urls)} server(s): {last_err}")
    return None
