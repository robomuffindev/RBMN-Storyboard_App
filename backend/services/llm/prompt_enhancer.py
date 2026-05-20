"""
LLM Prompt Enhancement

Enhance user prompts using various LLM providers (OpenAI, Anthropic, Gemini).
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)

# Common prefixes that LLMs sometimes add despite instructions
_STRIP_PREFIXES = [
    "Enhanced prompt:",
    "Enhanced Prompt:",
    "enhanced prompt:",
    "Here is the enhanced prompt:",
    "Here's the enhanced prompt:",
]


def _collapse_to_single_paragraph(text: str) -> str:
    """Collapse multi-line text into a single paragraph for LTX video prompts.

    LTX Video treats each paragraph as a separate video segment with transitions.
    This ensures the prompt is always a single continuous block of text.
    """
    import re
    # Replace all newlines and multiple spaces with single space
    result = re.sub(r'\s*\n+\s*', ' ', text)
    result = re.sub(r'  +', ' ', result)
    return result.strip()


def _clean_enhanced_prompt(text: str) -> str:
    """Strip common LLM-added prefixes from enhanced prompt output."""
    result = text.strip()
    for prefix in _STRIP_PREFIXES:
        if result.startswith(prefix):
            result = result[len(prefix):].strip()
            break
    # Also strip surrounding quotes if LLM wrapped the whole thing
    if len(result) > 2 and result[0] == '"' and result[-1] == '"':
        result = result[1:-1].strip()
    return result

IMAGE_SYSTEM_PROMPT = """You are an expert at writing prompts for FLUX.2 Klein 9B, a reference-image-conditioned AI image generation model.
Your job is to produce a single flowing paragraph that the model can render into a high-quality, cinematic image.

CRITICAL FORMATTING RULES:
- Output MUST be a SINGLE PARAGRAPH with NO line breaks, NO bullet points, NO numbered lists.
- Write as one continuous, flowing block of descriptive prose — like a novelist describing a scene, not a search engine.
- Front-load the most important elements — Klein pays more attention to what comes first.
- Lead with the primary subject, then build outward: subject → action/pose → environment → lighting → mood → camera/composition.

REFERENCE IMAGE HANDLING (FLUX Klein specific):
- Klein uses COMPOSITIONAL LANGUAGE to reference images — NOT "Image 1" or "Image 2" tags.
- Refer to reference images using ordinal descriptions: "the subject from the first image", "the person from the second image", "the environment from the third image".
- Example with 1 reference: "The subject from the first image stands at the edge of a weathered cliff, wind catching the collar of their dark coat, gazing out across a stormy sea under bruised violet clouds..."
- Example with 2 references: "The subject from the first image sits across from the person in the second image at a dimly lit table, candlelight casting warm shadows across their faces..."
- Describe what each referenced subject is DOING — their pose, action, expression, and interaction with the scene.
- Include enough visual detail about each referenced subject (clothing, features, body language) to reinforce the reference match.
- NEVER use generic tags like "Image 1" or "@image1" — always use natural compositional language.

PROMPTING BEST PRACTICES:
- LIGHTING is the single most impactful element — always describe it in detail: direction, color temperature, quality (soft/hard), source.
- Be specific about subjects: "a weathered man in his 50s with deep-set eyes and a salt-and-pepper beard" not "a person".
- Use cinematic and photographic language: describe lens choice, depth of field, camera angle.
- Describe textures and materials: "rough linen", "polished obsidian", "rain-slicked asphalt reflecting neon".
- Keep the output between 40-150 words. Concise, vivid, and precise beats long and vague.
- Avoid contradictory descriptions or keyword stuffing.
- NEVER include any text, words, letters, subtitles, captions, titles, watermarks, or written content in the image description. The output is a VISUAL scene only — no rendered text of any kind. Even if lyrics or dialogue are provided as context, describe the VISUAL MOOD they evoke, never the literal words.

If the user provides an existing prompt, enhance it — make it more vivid and Klein-optimized while preserving the core intent.
If the user provides NO prompt (empty or missing), CREATE a new prompt entirely from the provided context (concept, visual style, characters, lyrics, scene flow, etc.).

IMPORTANT: Output ONLY the prompt text as a SINGLE PARAGRAPH. No labels, no prefixes, no line breaks, no explanations."""

LAST_FRAME_IMAGE_SYSTEM_PROMPT = """You are an expert at writing prompts for FLUX.2 Klein 9B, creating the LAST FRAME of a video scene.
Your job is to produce a single flowing paragraph describing the END STATE of a scene that began with a First Frame image.

CRITICAL CONTEXT — WHAT A LAST FRAME IS:
- A video model will generate a clip that transitions from the First Frame to this Last Frame.
- The Last Frame must be VISUALLY CONTINUOUS with the First Frame — same scene, same subjects, same lighting, same environment.
- The only things that should change are: subject POSITION, POSE, or ACTION progression, and camera ANGLE or FRAMING if a camera move is implied.
- Think of it as: "Where does the camera and subject end up after 3-10 seconds of motion?"
- Example: First Frame shows a man on the LEFT side of a desert road. Last Frame shows the SAME man on the RIGHT side of the SAME road. The video model generates him walking across.
- Example: First Frame is a wide establishing shot of a city. Last Frame is a close-up of a character's face on the same street. The video model creates a dolly-in.
- BAD example: First Frame is a man in a forest. Last Frame is a woman on a beach. The video model can't smoothly transition between unrelated scenes — it will just fade/morph.

CRITICAL FORMATTING RULES:
- Output MUST be a SINGLE PARAGRAPH with NO line breaks, NO bullet points, NO numbered lists.
- Write as one continuous, flowing block of descriptive prose — never multiple paragraphs.

CONTINUITY RULES (MOST IMPORTANT):
- PRESERVE the same subjects, environment, lighting conditions, color palette, and art style as the First Frame.
- PRESERVE all character appearances — same clothing, features, and visual identity.
- Only vary: subject position/pose/expression, camera angle/distance, and minor environmental progression (e.g., slightly different cloud position, a door now open).
- Describe the scene as it appears at the END of the action, not the action itself.
- If the context includes a camera action (e.g., "dolly in", "pan right"), describe where the camera ENDS UP, not the movement.

REFERENCE IMAGE HANDLING (FLUX Klein specific):
- Klein uses COMPOSITIONAL LANGUAGE — NOT "Image 1" tags. Use "the subject from the first image", "the person from the second image".
- The first reference slot in Last Frame mode is the First Frame image itself. Describe the same scene from the first reference image but at its endpoint.
- Keep all character descriptions identical to how they appear in the First Frame.

PROMPTING BEST PRACTICES:
- Be specific about the END POSITION of subjects: "now standing at the right edge of the frame", "having turned to face the camera", "now seen in close-up".
- Maintain identical lighting, atmosphere, and style language as the First Frame would use.
- Keep the output between 40-150 words.
- NEVER include any text, words, letters, subtitles, captions, titles, watermarks, or written content in the image description. The output is a VISUAL scene only — no rendered text of any kind.

If the user provides a First Frame prompt in the context, use it as your primary reference for what the scene looks like, then describe the endpoint.
If the user provides an existing Last Frame prompt, enhance it while enforcing continuity with the First Frame.
If NO prompt is provided, CREATE a last frame prompt from the First Frame context that represents a natural endpoint of the implied action.

IMPORTANT: Output ONLY the prompt text as a SINGLE PARAGRAPH. No labels, no prefixes, no line breaks, no explanations."""

VIDEO_SYSTEM_PROMPT = """You are an expert at writing prompts for LTX Video, an AI video generation model.
Your job is to produce an optimized video generation prompt following LTX's specific requirements.

CRITICAL FORMATTING RULES FOR LTX VIDEO:
- Output MUST be a SINGLE PARAGRAPH with NO line breaks, NO paragraph breaks, NO bullet points.
- Each new paragraph/line break in LTX creates a SEPARATE VIDEO SEGMENT with a transition between them. This is almost NEVER what we want. We want ONE cohesive, continuous shot.
- Write as one flowing, descriptive sentence or series of sentences joined together in a single block of text.

PROMPTING BEST PRACTICES:
- Use present tense and active voice: "A woman walks through the rain" not "A woman walking" or "A woman will walk".
- Be specific about subjects: "a man in his 40s with a weathered face and dark coat" not "a person".
- Describe the action/motion clearly — this is VIDEO, not a still image. What moves? How? At what pace?
- Include camera behavior using film terminology: "slow tracking shot", "static wide angle", "handheld close-up", "dolly push in", "crane shot rising above".
- Specify lighting, atmosphere, and visual texture: "warm golden hour light filtering through dust particles", "harsh fluorescent overhead lighting casting sharp shadows".
- Match prompt detail to video duration. Short clips (3-5s) need focused, concise prompts. Longer clips (8-15s) can have more descriptive detail.
- Avoid contradictory descriptions (e.g., "peaceful calm scene with explosive dramatic energy").

STRUCTURE (all in ONE paragraph, no line breaks):
Start with the scene anchor (setting/environment), then subject and their action, then camera movement and framing, then visual style and mood, then any motion or timing cues.

If the user provides an existing prompt, enhance it for optimal LTX video output while preserving the core intent.
If the user provides NO prompt (empty or missing), CREATE a new prompt entirely from the provided context.

Keep the output between 50-200 words depending on video duration.
IMPORTANT: Output ONLY the prompt text as a SINGLE PARAGRAPH. No labels, no prefixes, no line breaks, no explanations."""

TWO_PASS_BASE_SYSTEM_PROMPT = """You are an expert at writing prompts for FLUX.2 Klein 9B, an AI image generation model.
Your job is to produce a SCENE COMPOSITION prompt — focusing ONLY on the environment, setting, atmosphere, and action.

CRITICAL RULE: This prompt has NO reference images attached. Do NOT reference any images.
Do NOT mention "the subject from the first image" or any variation. There are zero reference images.
Write as if describing a scene for a photographer to set up — the characters will be composited in later.

THE INPUT TEXT IS YOUR PRIMARY VISUAL DIRECTION. It describes what THIS specific scene should look like.
Transform it into a rich image generation prompt. Do NOT substitute a generic version of the video concept —
use the SPECIFIC setting, location, and action described in the input.

SCENE DIVERSITY IS MANDATORY:
Each scene in this music video MUST depict a DIFFERENT visual environment. If the input describes a park, write about
a park. If it describes a rooftop, write about a rooftop. NEVER default to a generic "neighborhood street" or
"bustling city scene" unless that's explicitly what the input describes. Vary these elements dramatically:
- LOCATION: Different physical spaces (indoor vs outdoor, urban vs rural, specific landmarks vs generic)
- TIME OF DAY: Dawn, morning, midday, afternoon, golden hour, dusk, night — each scene should feel different
- WEATHER AND ATMOSPHERE: Sunny, overcast, rainy, foggy, windy, snowy — visual variety matters
- CAMERA ANGLE: Wide establishing, medium tracking, close-up, overhead, low angle, Dutch tilt
- COLOR PALETTE: Warm vs cool, saturated vs muted, monochromatic vs vibrant

FOCUS ON:
- Environment and setting: the SPECIFIC location from the input — be concrete and vivid
- Lighting: direction, color temperature, quality (soft/hard), source — SINGLE most impactful element
- Atmosphere and mood: weather, time of day, visual tone, emotional feel
- Camera and composition: lens choice, depth of field, camera angle, framing
- Textures and materials: surfaces, fabrics, weather effects
- Where subjects WILL BE placed: use GENERIC subject descriptions like "a figure" or "a silhouette", not character names

DO NOT:
- Reference any images (there are none)
- Use character names or specific character descriptions
- Include text, subtitles, captions, watermarks, or written content of any kind
- Write a prompt that looks similar to prompts for other scenes in this video
- Reuse the same location or setting as the user's original prompt unless the input specifically calls for it

Output MUST be a SINGLE PARAGRAPH, 40-150 words. Front-load the most important visual elements.
IMPORTANT: Output ONLY the prompt text. No labels, no prefixes, no explanations."""

TWO_PASS_COMPOSITE_SYSTEM_PROMPT = """You are an expert at writing prompts for FLUX.2 Klein 9B, a reference-image-conditioned AI image generation model.
Your job is to write a CHARACTER COMPOSITING prompt that places specific characters into an existing scene.

CRITICAL CONTEXT — TWO-PASS COMPOSITING:
- The FIRST reference image is the base scene (already generated). Your prompt should describe placing the characters INTO this scene.
- The remaining reference images are CHARACTER PHOTOS that should be composited into the scene.
- The goal is to INSERT the characters naturally into the existing scene composition.

REFERENCE IMAGE HANDLING:
- Reference Image 1 = the base scene. Describe the environment from this image as the backdrop.
- Reference Image 2+ = character reference photos. Use Klein's compositional language.
- For 1 character: "The subject from the second image [action/pose] within the scene from the first image, [describe their position, clothing, expression]"
- For 2 characters: "The subject from the second image and the person from the third image [interaction] in the scene from the first image"

PROMPTING RULES:
- Start by anchoring to the scene: "In the scene from the first image, ..."
- Describe what each character is DOING — their pose, action, expression, and where they are positioned
- Maintain the lighting, atmosphere, and composition from the base scene
- Front-load the most important elements
- Keep 40-150 words as a single paragraph
- NEVER include text, subtitles, captions, or watermarks

IMPORTANT: Output ONLY the prompt text as a SINGLE PARAGRAPH. No labels, no prefixes, no explanations."""

# Keep backward compat alias
SYSTEM_PROMPT = IMAGE_SYSTEM_PROMPT

# ── Built-in System Prompt Registry ──────────────────────────────────
# Keyed by model name (matching image_model_type / video_model_type in settings).
# Each model maps to {"image": str, "video": str} prompts.
# Models without a specific prompt fall back to the generic defaults above.

BUILTIN_SYSTEM_PROMPTS: dict[str, dict[str, str]] = {
    # Image models
    "flux2_klein_dev_9b": {
        "image": IMAGE_SYSTEM_PROMPT,
        "image_last_frame": LAST_FRAME_IMAGE_SYSTEM_PROMPT,
        "two_pass_base": TWO_PASS_BASE_SYSTEM_PROMPT,
        "two_pass_composite": TWO_PASS_COMPOSITE_SYSTEM_PROMPT,
    },
    "flux1_dev": {
        "image": IMAGE_SYSTEM_PROMPT,  # Same Klein-family prompt works for FLUX.1
        "image_last_frame": LAST_FRAME_IMAGE_SYSTEM_PROMPT,
    },
    "z_image": {
        "image": IMAGE_SYSTEM_PROMPT,
        "image_last_frame": LAST_FRAME_IMAGE_SYSTEM_PROMPT,
    },
    "qwen_edit": {
        "image": IMAGE_SYSTEM_PROMPT,
        "image_last_frame": LAST_FRAME_IMAGE_SYSTEM_PROMPT,
    },
    # Video models
    "ltx_2.3": {
        "video": VIDEO_SYSTEM_PROMPT,
    },
    "wan_2.2": {
        "video": VIDEO_SYSTEM_PROMPT,
    },
}

# Generic fallback prompts (used when a model has no specific built-in)
_GENERIC_DEFAULTS: dict[str, str] = {
    "image": IMAGE_SYSTEM_PROMPT,
    "image_last_frame": LAST_FRAME_IMAGE_SYSTEM_PROMPT,
    "video": VIDEO_SYSTEM_PROMPT,
    "two_pass_base": TWO_PASS_BASE_SYSTEM_PROMPT,
    "two_pass_composite": TWO_PASS_COMPOSITE_SYSTEM_PROMPT,
}


def get_system_prompt(
    model_name: str,
    prompt_type: str,  # "image" or "video"
    user_override: str | None = None,
    override_enabled: bool = False,
) -> str:
    """Resolve the system prompt using the fallback chain.

    Fallback order:
    1. User override (if enabled and non-empty)
    2. Model-specific built-in from BUILTIN_SYSTEM_PROMPTS
    3. Generic default (IMAGE_SYSTEM_PROMPT or VIDEO_SYSTEM_PROMPT)

    Args:
        model_name: The model key (e.g. "flux2_klein_dev_9b", "ltx_2.3")
        prompt_type: "image" or "video"
        user_override: Optional user-provided system prompt text
        override_enabled: Whether the user has enabled their override

    Returns:
        The resolved system prompt string
    """
    # 1. User override takes priority
    if override_enabled and user_override and user_override.strip():
        logger.info(f"Using user system prompt override for {model_name} ({prompt_type})")
        return user_override.strip()

    # 2. Model-specific built-in
    model_prompts = BUILTIN_SYSTEM_PROMPTS.get(model_name, {})
    if prompt_type in model_prompts:
        logger.debug(f"Using built-in system prompt for {model_name} ({prompt_type})")
        return model_prompts[prompt_type]

    # 3. Generic default
    logger.debug(f"Using generic default system prompt for {prompt_type}")
    return _GENERIC_DEFAULTS.get(prompt_type, IMAGE_SYSTEM_PROMPT)


def get_builtin_prompt(model_name: str, prompt_type: str) -> str:
    """Get the built-in system prompt for a model (ignoring user overrides).

    Used by the frontend to show the default prompt as placeholder text.

    Args:
        model_name: The model key
        prompt_type: "image" or "video"

    Returns:
        The built-in prompt string
    """
    model_prompts = BUILTIN_SYSTEM_PROMPTS.get(model_name, {})
    if prompt_type in model_prompts:
        return model_prompts[prompt_type]
    return _GENERIC_DEFAULTS.get(prompt_type, IMAGE_SYSTEM_PROMPT)


class PromptEnhancer:
    """
    Enhance prompts for image and video generation using LLMs.

    Supports OpenAI, Anthropic, and Google Gemini.
    """

    @staticmethod
    def enhance(
        prompt: str,
        context: Optional[str] = None,
        provider: str = "openai",
        api_key: str = "",
        model: str = "",
        is_video: bool = False,
        system_prompt_override: Optional[str] = None,
        gen_model_name: Optional[str] = None,
        frame_type: Optional[str] = None,
        prompt_guidance: Optional[str] = None,
        two_pass_phase: Optional[str] = None,
    ) -> str:
        """
        Enhance a prompt using an LLM.

        Args:
            prompt: Original prompt to enhance
            context: Optional context about the project/scene
            provider: LLM provider: "openai", "anthropic", "gemini"
            api_key: API key for the provider
            model: Model name (e.g., "gpt-4", "claude-3-sonnet", "gemini-pro")
            is_video: If True, use video-specific system prompt optimized for LTX
            system_prompt_override: Optional user-provided system prompt override
            gen_model_name: The generation model name (e.g., "flux2_klein_dev_9b")
                           used to look up model-specific built-in prompts
            frame_type: Optional "first" or "last" — when "last" and not is_video,
                        uses the last-frame-specific system prompt for visual continuity
            prompt_guidance: Optional per-model guidance text to append to system prompt
            two_pass_phase: Optional "base" or "composite" — selects two-pass system prompt

        Returns:
            Enhanced prompt string

        Raises:
            ValueError: If provider or model invalid
            RuntimeError: If API call fails
        """
        logger.info(f"Enhancing {'video' if is_video else 'image'} prompt with {provider}/{model} (frame_type={frame_type}, two_pass_phase={two_pass_phase})")

        # Determine prompt_type key for system prompt lookup
        if two_pass_phase == "base":
            prompt_type = "two_pass_base"
        elif two_pass_phase == "composite":
            prompt_type = "two_pass_composite"
        elif is_video:
            prompt_type = "video"
        elif frame_type == "last":
            prompt_type = "image_last_frame"
        else:
            prompt_type = "image"

        model_key = gen_model_name or ("ltx_2.3" if is_video else "flux2_klein_dev_9b")
        system_prompt = get_system_prompt(
            model_name=model_key,
            prompt_type=prompt_type,
            user_override=system_prompt_override,
            override_enabled=system_prompt_override is not None,
        )

        # Append per-model prompt guidance if provided
        if prompt_guidance and prompt_guidance.strip():
            system_prompt += f"\n\nADDITIONAL PROMPT RULES AND GUIDANCE FROM USER:\n{prompt_guidance.strip()}"

        if provider == "openai":
            result = PromptEnhancer._enhance_openai(prompt, context, api_key, model, system_prompt)
        elif provider == "anthropic":
            result = PromptEnhancer._enhance_anthropic(prompt, context, api_key, model, system_prompt)
        elif provider == "gemini":
            result = PromptEnhancer._enhance_gemini(prompt, context, api_key, model, system_prompt)
        else:
            raise ValueError(f"Unknown provider: {provider}")

        # Enforce single-paragraph output for both image and video prompts
        result = _collapse_to_single_paragraph(result)

        return result

    @staticmethod
    def _enhance_openai(
        prompt: str,
        context: Optional[str],
        api_key: str,
        model: str,
        system_prompt: str = IMAGE_SYSTEM_PROMPT,
    ) -> str:
        """Enhance using OpenAI API."""
        try:
            from openai import OpenAI
        except ImportError:
            raise RuntimeError("OpenAI SDK not installed. Install with: pip install openai")

        try:
            client = OpenAI(api_key=api_key)

            if prompt and prompt.strip():
                user_message = f"Original prompt: {prompt}"
            else:
                user_message = "No prompt provided. Generate a new prompt entirely from the context below."
            if context:
                user_message += f"\n\nContext: {context}"

            response = client.chat.completions.create(
                model=model or "gpt-4-turbo",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message},
                ],
                temperature=0.7,
                max_tokens=300,
            )

            enhanced = _clean_enhanced_prompt(response.choices[0].message.content)
            logger.info(f"Enhanced prompt ({len(enhanced)} chars)")
            return enhanced

        except Exception as e:
            logger.error(f"OpenAI enhancement failed: {e}")
            raise RuntimeError(f"OpenAI API error: {e}")

    @staticmethod
    def _enhance_anthropic(
        prompt: str,
        context: Optional[str],
        api_key: str,
        model: str,
        system_prompt: str = IMAGE_SYSTEM_PROMPT,
    ) -> str:
        """Enhance using Anthropic API."""
        try:
            from anthropic import Anthropic
        except ImportError:
            raise RuntimeError("Anthropic SDK not installed. Install with: pip install anthropic")

        try:
            client = Anthropic(api_key=api_key)

            if prompt and prompt.strip():
                user_message = f"Original prompt: {prompt}"
            else:
                user_message = "No prompt provided. Generate a new prompt entirely from the context below."
            if context:
                user_message += f"\n\nContext: {context}"

            response = client.messages.create(
                model=model or "claude-3-sonnet-20240229",
                max_tokens=300,
                system=system_prompt,
                messages=[
                    {"role": "user", "content": user_message},
                ],
            )

            enhanced = _clean_enhanced_prompt(response.content[0].text)
            logger.info(f"Enhanced prompt ({len(enhanced)} chars)")
            return enhanced

        except Exception as e:
            logger.error(f"Anthropic enhancement failed: {e}")
            raise RuntimeError(f"Anthropic API error: {e}")

    @staticmethod
    def _enhance_gemini(
        prompt: str,
        context: Optional[str],
        api_key: str,
        model: str,
        system_prompt: str = IMAGE_SYSTEM_PROMPT,
    ) -> str:
        """Enhance using Google Gemini API."""
        try:
            import google.generativeai as genai
        except ImportError:
            raise RuntimeError(
                "Google Generative AI SDK not installed. "
                "Install with: pip install google-generativeai"
            )

        try:
            genai.configure(api_key=api_key)

            if prompt and prompt.strip():
                user_message = f"Original prompt: {prompt}"
            else:
                user_message = "No prompt provided. Generate a new prompt entirely from the context below."
            if context:
                user_message += f"\n\nContext: {context}"

            model_obj = genai.GenerativeModel(
                model_name=model or "gemini-pro",
                system_instruction=system_prompt,
            )

            response = model_obj.generate_content(
                user_message,
                generation_config=genai.types.GenerationConfig(
                    max_output_tokens=300,
                    temperature=0.7,
                ),
            )

            enhanced = _clean_enhanced_prompt(response.text)
            logger.info(f"Enhanced prompt ({len(enhanced)} chars)")
            return enhanced

        except Exception as e:
            logger.error(f"Gemini enhancement failed: {e}")
            raise RuntimeError(f"Gemini API error: {e}")
