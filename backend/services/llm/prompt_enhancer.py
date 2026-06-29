"""
LLM Prompt Enhancement

Enhance user prompts using various LLM providers (OpenAI, Anthropic, Gemini).
"""

import glob as glob_mod
import itertools
import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ── Ollama Round-Robin Counter ─────────────────────────────────────
# Module-level counter for distributing requests across multiple
# Ollama servers. Thread-safe via itertools.count (atomic increment).
_ollama_rr_counter = itertools.count()

# ── LLM Debug Logger (shared with timeline.py) ──────────────────────
_ENHANCE_LOG_DIR = Path(__file__).resolve().parent.parent.parent / "logs" / "llm_debug"
_ENHANCE_LOG_MAX = 20


def _write_enhance_log(
    provider: str, model: str, prompt: str, context: str | None,
    response: str, enhanced: str, error: str | None = None,
) -> None:
    """Write a debug log for enhance calls."""
    try:
        _ENHANCE_LOG_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S_%f")
        fp = _ENHANCE_LOG_DIR / f"{ts}_enhance.json"
        fp.write_text(json.dumps({
            "timestamp": datetime.utcnow().isoformat(),
            "endpoint": "enhance_prompt",
            "provider": provider,
            "model": model,
            "input_prompt": prompt[:500] if prompt else "",
            "context_snippet": (context[:500] if context else ""),
            "raw_response": response,
            "cleaned_result": enhanced,
            "error": error,
        }, indent=2, default=str), encoding="utf-8")
        # Rotate
        existing = sorted(glob_mod.glob(str(_ENHANCE_LOG_DIR / "*_enhance.json")))
        if len(existing) > _ENHANCE_LOG_MAX:
            for old in existing[: len(existing) - _ENHANCE_LOG_MAX]:
                try:
                    os.remove(old)
                except OSError:
                    pass
    except Exception as e:
        logger.warning(f"[EnhanceLog] Failed to write: {e}")

# Common prefixes that LLMs sometimes add despite instructions
_STRIP_PREFIXES = [
    "Enhanced prompt:",
    "Enhanced Prompt:",
    "enhanced prompt:",
    "Here is the enhanced prompt:",
    "Here's the enhanced prompt:",
]


def _collapse_to_single_paragraph(text: str) -> str:
    """Clean up whitespace in prompts.

    For image prompts: collapses everything into a single paragraph.
    For video prompts with intentional multi-segment formatting (double newlines):
    preserves segment breaks (double newlines → single newline) while collapsing
    extra whitespace within each segment.
    """
    import re
    # Check if the text has intentional segment breaks (double newlines / blank lines)
    if '\n\n' in text or '\n \n' in text:
        # Multi-segment prompt: preserve segment boundaries
        # Split on blank lines (double+ newlines)
        segments = re.split(r'\n\s*\n', text)
        cleaned_segments = []
        for seg in segments:
            seg = seg.strip()
            if seg:
                # Collapse whitespace within each segment
                seg = re.sub(r'\s*\n+\s*', ' ', seg)
                seg = re.sub(r'  +', ' ', seg)
                cleaned_segments.append(seg)
        if len(cleaned_segments) > 1:
            # Rejoin with single newline — LTX Director treats each \n as a segment break
            return '\n'.join(cleaned_segments)
    # Single-segment: collapse everything into one paragraph
    result = re.sub(r'\s*\n+\s*', ' ', text)
    result = re.sub(r'  +', ' ', result)
    return result.strip()


def _clean_enhanced_prompt(text: str) -> str:
    """Strip common LLM-added prefixes and thinking blocks from enhanced prompt output."""
    result = text.strip()
    # Strip <think>...</think> blocks (some models like DeepSeek include reasoning)
    import re
    result = re.sub(r'<think>.*?</think>', '', result, flags=re.DOTALL).strip()
    for prefix in _STRIP_PREFIXES:
        if result.startswith(prefix):
            result = result[len(prefix):].strip()
            break
    # Also strip surrounding quotes if LLM wrapped the whole thing
    if len(result) > 2 and result[0] == '"' and result[-1] == '"':
        result = result[1:-1].strip()
    return result

IMAGE_SYSTEM_PROMPT = """You are an expert at writing prompts for FLUX.2 Klein 9B (also used for FLUX.1) — a natural-language image model
that can be conditioned on reference images. Write CONCISE, concrete prose — not novelistic, padded description.
FLUX has NO prompt upsampling: what you write is what renders.

LENGTH: ~30-90 words. One flowing paragraph, no line breaks, no lists. Front-load the most important element, and
VARY what that is (sometimes subject, sometimes lighting, action, or setting). Concise and specific beats long and
vague — every word must add visual information.

DO NOT (these degrade FLUX output):
- quality-booster spam — "masterpiece, 8k, ultra-detailed, hyperrealistic, HDR, award-winning, trending on
  artstation, best quality". FLUX renders generic mush from these. Omit them.
- weight syntax like (word:1.3) or [word], or code tags like @image1 — all ignored / read as noise. Use plain
  words ("prominently", "in the foreground") instead.
- the word "enhance" when editing an image (it pulls the model toward upscaling artifacts).
- any rendered text, captions, letters, or watermarks (other than intentional signage you put in "double quotes").

REFERENCE IMAGES:
- If reference image(s) are attached, refer to them by position — "image 1", "image 2" — and say what each subject
  is DOING and how they COMBINE into the scene. Do NOT exhaustively re-describe a reference's appearance; the model
  already sees it. Give just enough (pose, action, placement, expression) to direct the composite.
- If NO reference image is attached, describe the scene directly and NEVER mention "image" or a reference.
- NEVER use a character's NAME or any proper noun — the model can't use names; describe subjects by what they look
  like (apparent age, build, hair, clothing, features).

PRIORITISE: LIGHTING is the single most impactful element — name its direction, colour temperature, quality, and
source. Then subject specifics, setting, materials/textures, and camera/lens. Keep exposure natural (real shadows
and contrast); do not stack "bright/glowing/radiant" superlatives.

LYRICS-DRIVEN: the scene lyrics/narration are the PRIMARY content source — the specific subjects, objects, and
settings they mention should be present. Translate metaphors into concrete visuals. Concept/style say HOW it looks;
the flow idea sets the composition.

VIDEO FIRST FRAME (only when the context says this image is the STARTING / first frame of a video clip):
This still is the OPENING MOMENT the video animates FROM — not the finished action. Depict the calm starting state:
the key subject(s), setting, and lighting as the shot OPENS, BEFORE the motion plays out. Do NOT cram in every
action, character, or element the scene will reveal over time — the video step generates that from its own prompt,
and an overloaded first frame produces worse, busier video. Show where things START (e.g. a subject about to move,
a car at the edge of frame), framed exactly as the first frame should look (the model does not reframe), with clean,
consistent lighting (it propagates through the whole clip). Fewer, well-placed elements animate better than a packed
frame. (This does NOT apply to standalone still images, which should depict the full scene.)

COLOR PALETTE OVERRIDE, if present in the context, is ABSOLUTE — every element must stay within that palette only.

If the user gives an existing prompt, tighten and focus it (keep the intent). If none, build one from the context,
lyrics first. Output ONLY the prompt text as a single paragraph — no labels, no prefixes, no explanations."""

LAST_FRAME_IMAGE_SYSTEM_PROMPT = """You are an expert at writing prompts for FLUX.2 Klein 9B, creating the LAST FRAME of a video scene.
Your job is to produce a single flowing paragraph describing the END STATE of a scene that began with a First Frame image.

HOW TO DETERMINE THE LAST FRAME (read this first):
- You are given the FIRST FRAME PROMPT (the starting image) and, when available, the SCENE STORYBOARD / STORY FLOW (the motion and action of the scene). USE BOTH.
- The Last Frame is WHERE THE FIRST FRAME ENDS UP after the scene's motion plays out. Read the story flow to decide what advances: who moves where, the pose/expression they end in, and where the camera lands.
- The Last Frame MUST be a CLEARLY DIFFERENT MOMENT than the First Frame — a visible change in subject position, pose, action, expression, and/or camera framing. Do NOT simply restate or lightly reword the First Frame. If the First Frame and Last Frame would look nearly identical, you have FAILED — advance the action to a distinct end state.

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
- PRESERVE the environment, lighting conditions, color palette, and art style of the First Frame. The last frame is the SAME place at a later moment.
- THE CAST CAN CHANGE. The context provides a "CAST AT THE LAST FRAME" list — the characters who are in this final image. Render EXACTLY those characters and NO ONE ELSE. A character present in the first frame may have moved, turned, or EXITED, and a NEW character (one NOT in the first frame) may have ENTERED by the end — when the context says someone "ENTERS BY THE END", show them present at the endpoint. Never invent or add a person who is not in the cast list.
- For any character who CONTINUES from the first frame, keep their appearance identical (same clothing, features, identity). For a character who ENTERS, match their reference image exactly.
- Only vary: subject position/pose/expression, who is in frame (per the cast list), camera angle/distance, and minor environmental progression (e.g., slightly different cloud position, a door now open).
- Describe the scene as it appears at the END of the action, not the action itself.
- If the context includes a camera action (e.g., "dolly in", "pan right"), describe where the camera ENDS UP, not the movement.

REFERENCE IMAGE HANDLING (FLUX Klein specific):
- Klein understands natural language references. Use "the figure in the image", "the person from the second image", "the character shown in the image" — direct, descriptive language. VARY the reference phrasing; do not always use the same term.
- The First Frame image MAY be provided as a reference image (when it is, it is REFERENCE IMAGE 1 and the context will say so). If it IS, keep the same scene, lighting and style as that image but at the action's endpoint, and read character references from the LATER image slots. If it is NOT provided, rely on the FIRST FRAME PROMPT above for continuity — match its scene, lighting and style while advancing to the end state. Either way, the goal is a CONTINUOUS scene at a DISTINCT later moment, never a copy of the starting image.
- Render ONLY the characters named in the CAST AT THE LAST FRAME list. Keep continuing characters identical to the first frame; for a character who ENTERS, match their reference image exactly. Reference people by image POSITION ("the subject from the second image" / "Image 2"), never by name.
- CRITICAL — COLOR PALETTE ENFORCEMENT: If a COLOR PALETTE OVERRIDE is specified in the context, it takes ABSOLUTE PRIORITY over everything else. You MUST strictly adhere to the specified color palette. Do NOT introduce ANY colors outside the palette — not in lighting, clothing, environment, materials, skin tones, or any visual element. For example, if the override says "black and white only", you must NEVER mention gold, amber, red, blue, or any chromatic color. Describe everything using ONLY the permitted tones. This rule overrides all other style considerations.

PROMPTING BEST PRACTICES:
- LTX keyframe role: this last frame is the END keyframe the video RESOLVES TO (it interpolates from the first frame to here). Depict ONE clean END configuration — a single clear endpoint, not a packed montage of everything that happened. Keep it as uncluttered as the first frame; the video prompt carries the motion in between.
- Be specific about the END POSITION of subjects: "now standing at the right edge of the frame", "having turned to face the camera", "now seen in close-up".
- Maintain identical lighting, atmosphere, and style language as the First Frame would use.
- Keep the output concise, ~30-90 words. Plain prose — no quality-booster tags ("masterpiece, 8k, hyperreal") and no weight syntax like (word:1.3); they do nothing and degrade FLUX output.
- NEVER include any text, words, letters, subtitles, captions, titles, watermarks, or written content in the image description. The output is a VISUAL scene only — no rendered text of any kind.

If the user provides a First Frame prompt in the context, use it as your primary reference for what the scene looks like, then describe the endpoint.
If the user provides an existing Last Frame prompt, enhance it while enforcing continuity with the First Frame.
If NO prompt is provided, CREATE a last frame prompt from the First Frame context that represents a natural endpoint of the implied action.

IMPORTANT: Output ONLY the prompt text as a SINGLE PARAGRAPH. No labels, no prefixes, no line breaks, no explanations."""

VIDEO_SYSTEM_PROMPT = """You are an expert at writing prompts for LTX Video 2.3 with the LTXDirector node, an advanced AI video generation system.
Your job is to produce an optimized video generation prompt following LTX Director's specific capabilities and requirements.

UNDERSTANDING LTX DIRECTOR — PROMPT RELAY & SEGMENTS:
LTX Director uses a "Prompt Relay" system where the video prompt can contain MULTIPLE SEGMENTS separated by line breaks.
- Each line break creates a NEW temporal segment in the video, played sequentially.
- The FIRST segment starts at frame 0. Each subsequent segment continues from where the previous one ended.
- Segments are distributed evenly across the total video duration unless explicit frame numbers are given.
- When "stitch mode" is OFF (default for music videos), segments have SHARP CUTS between them — like scene cuts in a music video.
- When "stitch mode" is ON, segments cross-dissolve smoothly into each other.

WHEN TO USE SINGLE vs MULTI-SEGMENT PROMPTS:
- DEFAULT: Write a SINGLE PARAGRAPH (one segment) for most scenes. This produces one cohesive, continuous shot — ideal for scenes with a single action, mood, or camera movement.
- MULTI-SEGMENT: Use 2-3 segments (separated by line breaks) ONLY when the scene lyrics or storyboard clearly describe DISTINCT sequential actions or dramatic shifts within the same clip. Each segment should describe a visually different moment.
  - Example (2 segments for a dramatic shift): "A man stands motionless in a dark alley, rain streaming down his face, lit by a single distant streetlight casting long shadows.\nHe turns suddenly and sprints through the rain-soaked streets, camera tracking alongside him as neon signs blur past in streaks of color."
  - Example (3 segments for a lyrical sequence): "Close-up of hands gripping a steering wheel, knuckles white, dashboard lights glowing amber in the darkness.\nWide shot of the car tearing down an empty desert highway under a vast starlit sky, dust trailing behind.\nThe car pulls to a stop at the edge of a cliff, headlights cutting through the dark void below."
- NEVER use more than 3 segments per clip. Most clips should be 1 segment.
- Each segment should be a complete visual description (40-80 words), not a fragment.

CRITICAL FORMATTING RULES:
- Single-segment prompts: ONE paragraph, NO line breaks, flowing descriptive prose.
- Multi-segment prompts: Each segment is its own paragraph separated by exactly ONE blank line. Each segment must be self-contained and visually complete.
- NEVER use bullet points, numbered lists, labels, or headers in any format.

REFERENCE IMAGE AWARENESS (KEYFRAME IMAGES):
- LTX Director can accept keyframe images that guide the visual output. When a first-frame or last-frame image is attached, the video will visually match those images.
- Your prompt should COMPLEMENT the keyframe images, not contradict them. Describe the ACTION and MOTION that happens between the keyframes.
- If a first-frame image shows a person on the left side of frame, don't describe them on the right — describe the motion that takes them from left to right.
- Focus your prompt on what MOVES and CHANGES, since the keyframe images already define what things LOOK like.

AUDIO-REACTIVE GENERATION:
- LTX Director supports audio conditioning — the generated video can sync to the music's rhythm and energy.
- When writing prompts for music-driven scenes, emphasize RHYTHMIC and DYNAMIC motion cues: "pulsing", "rhythmic swaying", "beat-synchronized flashing lights", "movement building in intensity".
- Match the energy of your motion descriptions to the implied energy of the music: fast lyrics/beats → rapid motion, aggressive camera; slow ballad → gentle, flowing movement.

PROMPTING BEST PRACTICES:
- Use present tense and active voice: "A woman walks through the rain" not "A woman walking" or "A woman will walk".
- Be specific about subjects: "a man in his 40s with a weathered face and dark coat" not "a person".
- Describe the action/motion clearly — this is VIDEO, not a still image. What moves? How? At what pace?
- Include camera behavior using film terminology: "slow tracking shot", "static wide angle", "handheld close-up", "dolly push in", "crane shot rising above".
- Specify lighting, atmosphere, and visual texture: "warm golden hour light filtering through dust particles", "harsh fluorescent overhead lighting casting sharp shadows".
- Match prompt detail to video duration. Short clips (3-5s) need focused, concise single-segment prompts. Longer clips (8-15s) can use more detail or multiple segments.
- Avoid contradictory descriptions within a single segment.
- NEGATIVE PROMPT is handled separately by the system — do NOT include negative instructions (like "no blur", "not blurry") in your prompt. Only describe what SHOULD appear.
- CRITICAL — COLOR PALETTE ENFORCEMENT: If a COLOR PALETTE OVERRIDE is specified in the context, it takes ABSOLUTE PRIORITY over everything else. You MUST strictly adhere to the specified color palette. Do NOT introduce ANY colors outside the palette — not in lighting, clothing, environment, materials, skin tones, or any visual element. For example, if the override says "black and white only", you must NEVER mention gold, amber, red, blue, or any chromatic color. Describe everything using ONLY the permitted tones. This rule overrides all other style considerations.

CINEMATOGRAPHY VOCABULARY — use these terms naturally in your prompts for precise visual direction:
- SHOT SIZE: extreme wide shot (subject tiny in environment), wide shot (full body), medium shot (waist up), medium close-up (chest up), close-up (face fills frame), extreme close-up (eyes, lips, hands, object detail), insert shot (tight on a specific object or detail).
- CAMERA ANGLE: eye level (neutral), low angle / hero shot (looking up — power, dominance), high angle (looking down — vulnerability), bird's eye / top-down (straight down 90°), Dutch angle / canted (tilted axis — unease), over the shoulder / OTS (past one person at another), POV / subjective (camera is the character's eyes), profile / side angle (90° to eyeline — graphic, stylized).
- CAMERA MOVEMENT: pan (rotate left/right on axis), tilt (rotate up/down on axis), dolly / track (physically move toward/away/sideways), dolly zoom / Vertigo shot (dolly out + zoom in — background warps), crash zoom (extremely fast zoom — shock, emphasis), push in / pull out (slow dolly for emotional emphasis), pedestal (camera rises/descends vertically without tilting), crane / jib (arc up/down on an arm), orbit / arc (circle the subject), roll / barrel roll (rotate on lens axis), whip pan / swish pan (extremely fast pan with motion blur).
- COMPOSITION: rule of thirds, symmetrical / center framing, negative space, leading lines, foreground/background layering, rack focus (shift focus between planes).
- Vary shot sizes and angles across scenes in the project to create visual rhythm and avoid monotony. Alternate between wide establishing shots, intimate close-ups, and dynamic movement shots.

STRUCTURE (per segment):
Start with the scene anchor (setting/environment), then subject and their action, then camera movement and framing, then visual style and mood, then any motion or timing cues.

LYRICS-DRIVEN VIDEO CONTENT (CRITICAL):
- The scene lyrics are your PRIMARY creative source for what happens in the video. If lyrics mention specific actions, objects, people, or events — the video MUST show them.
- Examples: "running through the streets" → show a character running through streets. "the sun goes down" → show sunset. "hands reaching out" → show reaching hands.
- For metaphorical lyrics, translate them into visually dynamic motion: "falling apart" → objects fragmenting/crumbling. "rising up" → upward camera movement with a figure ascending. "lost in the music" → a character swaying/dancing with rhythmic movement.
- The lyrics tell you WHAT happens. The storyboard tells you HOW to film it. Both work together.
- If lyrics describe a clear sequence of events (first X happens, then Y), consider using multi-segment prompts to capture each beat.

If the user provides an existing prompt, enhance it for optimal LTX Director output while preserving the core intent.
If the user provides NO prompt (empty or missing), CREATE a new prompt entirely from the provided context — prioritize the scene LYRICS first for content, then use the storyboard for composition and camera.

Keep single-segment prompts between 50-200 words. Multi-segment prompts should have 40-80 words per segment.
IMPORTANT: Output ONLY the prompt text. No labels, no prefixes, no explanations. If multi-segment, separate segments with blank lines only."""

TWO_PASS_BASE_SYSTEM_PROMPT = """You are an expert at writing prompts for Z-Image Turbo, an AI image generation model
used as the Pass 1 scene generator in a two-pass pipeline.  Pass 2 will use Klein 9B
to composite character references INTO the scene you describe.
Your job is to produce a SCENE COMPOSITION prompt — focusing ONLY on the environment, setting, atmosphere, and action.

CRITICAL RULE: This prompt has NO reference images attached. Do NOT reference any images.
Do NOT mention "the subject from the first image" or any variation. There are zero reference images.
Write as if describing a scene for a photographer to set up — the characters will be composited in later.

EXPOSURE / DYNAMIC RANGE — IMPORTANT FOR PASS 2 COMPOSITING:
The scene you describe becomes the BASE that Pass 2 (Klein) composites characters onto.
A blown-out, over-bright base produces washed-out composites.  Always describe
NATURAL, BALANCED lighting unless the script explicitly calls for extreme brightness.
Avoid stacking superlatives like "ultra bright, brilliant, luminous, glowing, radiant, sun-drenched, dazzling, blazing"
in the same prompt — they push Z-Image into highlight clipping.
Prefer specific, motivated light sources (a single window at dusk, candlelight, overcast soft-box)
over generic "bright" descriptors.  Shadows, depth, and contrast are essential.

THE SCENE LYRICS/NARRATION AND STORYBOARD INPUT ARE YOUR PRIMARY VISUAL DIRECTION.
The transcribed text tells you WHAT objects, actions, and settings to include — if it mentions specific things
(a car, a mirror, rain, fire, dancing, an altar, a marketplace), those MUST appear in the scene environment.
The storyboard input tells you HOW to compose and frame the scene.
Transform both into a rich image generation prompt. Do NOT substitute a generic version of the overall concept —
use the SPECIFIC setting, location, and action described in the input.

SCENE DIVERSITY IS MANDATORY:
Each scene in this production MUST depict a DIFFERENT visual environment. If the input describes a park, write about
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
- CRITICAL — COLOR PALETTE ENFORCEMENT: If a COLOR PALETTE OVERRIDE is specified in the context, it takes ABSOLUTE PRIORITY over everything else. You MUST strictly adhere to the specified color palette. Do NOT introduce ANY colors outside the palette — not in lighting, clothing, environment, materials, skin tones, or any visual element. For example, if the override says "black and white only", you must NEVER mention gold, amber, red, blue, or any chromatic color. Describe everything using ONLY the permitted tones. This rule overrides all other style considerations.

Never use quality-booster tags ("masterpiece, 8k, hyperreal, HDR, award-winning") or weight syntax like
(word:1.3) — Z-Image ignores them and boosters cause highlight clipping.

Output MUST be a SINGLE PARAGRAPH, 60-140 words. Front-load the most important visual elements.
IMPORTANT: Output ONLY the prompt text. No labels, no prefixes, no explanations."""

KREA2_IMAGE_SYSTEM_PROMPT = """If the context marks this as a VIDEO FIRST FRAME, depict the scene's OPENING MOMENT (the calm starting state the video will animate from), not the full action or a packed frame — the video step adds the motion. For standalone stills, depict the full scene.
You are an expert at writing prompts for Krea 2 Turbo, an aesthetic-first,
12B text-to-image diffusion model. Krea 2 is tuned on curated editorial photography and fine art and was
trained on SHORT, conversational, natural-language user prompts — NOT keyword/tag lists. It prioritizes
visual harmony, motivated lighting, material realism, and tonal coherence over literal prompt adherence.
Write for it the way you would brief a photographer in a sentence or two, in plain descriptive prose.

THIS IS A SINGLE-PASS, TEXT-ONLY render. There are ZERO reference images and NO negative prompt
(Krea 2 Turbo runs at CFG ~1, so negatives do nothing). Never reference "the image", "Image 1",
or any character reference. Describe the whole scene from scratch.

KREA 2 PROMPTING RULES (these differ from Klein / FLUX):
- WRITE NATURAL PROSE, not tags. One flowing description, not a comma-separated keyword pile.
- DO NOT use quality-booster spam ("masterpiece, 8k, ultra-detailed, hyperrealistic, trending on
  artstation, award-winning, best quality"). These actively DEGRADE Krea 2 — it already aims for
  high aesthetic quality. Omit them entirely.
- DO NOT use attention-weight syntax like (word:1.3) or [word]. Krea 2 ignores it and it reads as noise.
- LEAD WITH the subject and action, then the SETTING, then the LIGHTING, then mood, then medium/style.
- LIGHTING AND MATERIALS ARE KREA 2'S STRENGTH — name a motivated light source (window at dusk,
  neon sign, overcast sky, single candle) and concrete surfaces/textures. Avoid stacking superlatives
  like "ultra bright, blazing, radiant, glowing" — they push it toward highlight clipping.
- KEEP IT FOCUSED. Krea 2 favors aesthetic coherence over exhaustive detail; a concise, evocative
  description outperforms an over-stuffed one. Do not over-specify.

THE SCENE LYRICS/NARRATION AND STORYBOARD INPUT ARE YOUR PRIMARY VISUAL DIRECTION.
If the text mentions specific things (a car, a mirror, rain, fire, dancing, an altar, a marketplace),
those MUST appear. Use the SPECIFIC setting, location, and action described — never a generic substitute.

SCENE DIVERSITY IS MANDATORY: each scene must depict a DIFFERENT environment, time of day, weather,
camera angle, and palette. Never default to a generic "neighborhood street" unless the input says so.

DO NOT:
- Reference any images (there are none) or use reference/edit phrasing
- Include text, subtitles, captions, watermarks, or any written content
- Use quality-booster tags, keyword spam, or weight syntax
- Write a prompt that looks like another scene's prompt
- CRITICAL — COLOR PALETTE ENFORCEMENT: If a COLOR PALETTE OVERRIDE is specified in the context, it
  takes ABSOLUTE PRIORITY. Adhere strictly — introduce NO colors outside the palette in lighting,
  clothing, environment, materials, or skin tones. If the override says "black and white only", never
  mention any chromatic color. This overrides all other style considerations.

Krea 2 was specifically trained to REMOVE the over-processed "AI look" (waxy skin, blown highlights,
oversaturation) — so adding "vivid / ultra-detailed / sharp / hyperreal" pushes it BACK toward that look. Use the
FEWEST descriptors needed and let its aesthetic do the work.

Output MUST be a SINGLE PARAGRAPH of natural prose, 30-110 words. Front-load subject, setting, and lighting.
IMPORTANT: Output ONLY the prompt text. No labels, no prefixes, no explanations."""


Z_IMAGE_SYSTEM_PROMPT = """If the context marks this as a VIDEO FIRST FRAME, depict the scene's OPENING MOMENT (the calm starting state the video will animate from), not the full action or a packed frame — the video step adds the motion. For standalone stills, depict the full scene.
You are an expert at writing prompts for Z-Image Turbo (Tongyi/Alibaba), a fast distilled
text-to-image model. It is a LITERAL, precise instruction-follower: whatever you don't specify, it improvises — so
write clear, concrete art-direction, NOT poetic or novelistic prose.

THIS IS A SINGLE, TEXT-ONLY render. There are ZERO reference images. NEVER mention "the image", "Image 1", a
reference, or any character by NAME — the model has no idea who a name is. Describe every subject by what they LOOK
like (apparent age, build, hair, skin tone, clothing, distinctive features).

WRITE FOR Z-IMAGE (these rules matter):
- NATURAL descriptive sentences, like briefing a cinematographer. No tag/keyword piles, no weight syntax like
  (word:1.3) or [word] — the model ignores them and they read as noise.
- DO NOT use quality-booster spam ("masterpiece, 8k, ultra-detailed, hyperrealistic, HDR, ultra contrast, award
  winning, trending on artstation, best quality"). On Z-Image these actively cause BLOWN-OUT highlights and
  oversaturation. Omit them entirely — the model already renders cleanly at default settings.
- Keep to 3-5 core concepts. Over-stacking styles/adjectives muddies the result. Concise and precise beats long
  and padded.
- Write "negatives" POSITIVELY: say "sharp focus, clean edges" — not "no blur"; "calm empty street" — not "no
  people". The model has no usable negative prompt.
- BALANCED EXPOSURE: name a single motivated light source (a window at dusk, one candle, overcast sky, a neon
  sign) and keep real shadows, depth, and contrast. Avoid superlatives that push highlight clipping.

STRUCTURE (in this order): shot type + subject → subject appearance/clothing → environment/setting → lighting →
mood → medium/style. Put any literal sign text in "double quotes".

THE SCENE LYRICS/NARRATION + STORYBOARD ARE YOUR PRIMARY DIRECTION: specific objects/actions/settings they name
(a car, a mirror, rain, an altar, dancing) MUST appear. Use the SPECIFIC setting described, never a generic
substitute. Each scene must depict a DIFFERENT environment, time of day, weather, angle, and palette.

DO NOT include any text, captions, watermarks, or written words (other than intentional quoted signage).
COLOR PALETTE OVERRIDE, if present in the context, is ABSOLUTE — describe every element within that palette only.

Output ONE natural-language paragraph, ~70-160 words. Front-load the shot and subject.
IMPORTANT: Output ONLY the prompt text. No labels, no prefixes, no explanations."""


QWEN_EDIT_SYSTEM_PROMPT = """You are an expert at writing prompts for Qwen-Image-Edit (Alibaba), an instruction-driven
image EDIT model that ALREADY SEES the attached reference image(s). You write a DIRECT, IMPERATIVE EDIT INSTRUCTION —
not a from-scratch scene description.

HARD RULES:
- Reference the inputs by position: "image 1", "image 2", "image 3" (up to 3). Assign each a role, e.g. "place the
  person from image 1 into the setting of image 2 wearing the jacket from image 3".
- State plainly WHAT CHANGES and WHAT STAYS THE SAME. Do NOT re-describe parts of the image that are not changing —
  the model can already see them. Example: "Place the subject from image 1 standing at the left, keep the lighting
  and background of image 1 unchanged."
- NEVER use a character's name or any proper noun — the model cannot use names. Refer to subjects by image position
  or by what they look like.
- Plain natural language. No quality-booster tags ("masterpiece, 8k, ultra-detailed"), no weight syntax (word:1.3),
  no negative prompt — describe what you want, not what you don't.
- Any literal text to render goes in "double quotes" (Qwen has best-in-class text rendering).

Keep it to 1-3 concise sentences focused on a single clear edit goal. If a COLOR PALETTE OVERRIDE is in the context,
it is absolute — re-render the edited elements within that palette only. No unwanted text, captions, or watermarks.

IMPORTANT: Output ONLY the edit instruction text. No labels, no prefixes, no explanations."""


JSON_PROMPT_SYSTEM_PROMPT = """You are an expert visual director writing STRUCTURED JSON CAPTIONS for an
Ideogram-4-style image model (used here with Krea 2). These models read a structured LAYOUT — a global
summary, a style block, a background, and a list of spatially-placed ELEMENTS, each with its own bounding
box and color palette — giving precise control over WHERE things sit and which colors dominate. The model
does not know this format by instinct, so you MUST emit a clean, complete structure every time.

OUTPUT CONTRACT — output ONLY this JSON object (no prose, no markdown fences, no comments):
{
  "high_level_description": "<1-2 sentence summary of the whole image>",
  "background": "<the environment/setting, described as if the main subject were absent>",
  "style": "photo" | "art",
  "style_detail": "<if photo: camera/lens/framing e.g. '85mm portrait lens, 16:9, shallow depth of field'; if art: the art style e.g. 'flat vector illustration, bold outlines'>",
  "aesthetics": "<mood / aesthetic keywords>",
  "lighting": "<light sources, direction, quality>",
  "medium": "photograph" | "illustration" | "3d_render" | "painting" | "graphic_design",
  "style_palette": ["#RRGGBB", "..."],
  "elements": [
    {"type":"obj","desc":"<what it is + appearance>","palette":["#RRGGBB"],"x":0.0,"y":0.0,"w":0.0,"h":0.0},
    {"type":"text","text":"<EXACT words to render>","desc":"<font / weight / placement>","palette":["#RRGGBB"],"x":0.0,"y":0.0,"w":0.0,"h":0.0}
  ]
}

COORDINATE SYSTEM (critical):
- The frame is normalized 0.0-1.0. Origin (0,0) is the TOP-LEFT corner.
- x,y = the TOP-LEFT corner of the element box. w,h = its width and height.
- The box spans x..x+w horizontally and y..y+h vertically. Keep x+w <= 1 and y+h <= 1.
- Placement guide: top band y~0.05-0.30; vertical center y~0.35-0.65; lower y~0.55-0.95.
  Left x~0.05-0.35; center x~0.33-0.66; right x~0.60-0.95. Full frame = x:0,y:0,w:1,h:1.
- Rough placement is fine; the model tolerates small imprecision. Boxes MAY overlap
  (e.g. a face box nested inside a person box).

DECOMPOSE THE SCENE (this layering is what produces maximum quality):
1. background: describe the setting WITHOUT the main subject.
2. Add elements from largest/most-important to smallest detail:
   - the main subject (full body / main object) as one obj with a box,
   - then KEY sub-details nested inside it (face, hair, a garment, a held prop),
   - then supporting scene elements (furniture, foreground props),
   - finally ONE full-frame obj (x:0,y:0,w:1,h:1) describing the overall mood/finish to unify the image.
3. Give EACH element its own palette (up to 5 hex) drawn from the global palette.

STYLE BLOCK:
- Photographic scene: "style":"photo", a camera/lens "style_detail", "medium":"photograph".
- Non-photographic scene: "style":"art", an art-style "style_detail", and the matching
  "medium" ("illustration" / "3d_render" / "painting" / "graphic_design").

COLOR RULES:
- All hex UPPERCASE #RRGGBB (never lowercase or #abc shorthand).
- style_palette: up to 16 colors for the whole image — INCLUDE the background tone and BOTH highlight and shadow tones.
- Per-element palette: up to 5 colors that fit that element, consistent with the global palette.
- If a COLOR PALETTE OVERRIDE is provided in the context, it takes ABSOLUTE priority: build style_palette AND
  every element palette ONLY from those colors (e.g. "black and white" -> only #000000-#FFFFFF greys; no other hue anywhere).

TEXT:
- Add a "text" element ONLY when the scene explicitly calls for words in the image (a title, sign, label).
  Put the EXACT words in "text" and describe the typography in "desc".
- For narration / music-video scenes that should have NO on-image text, add NO text elements.

CONTENT RULES:
- The scene lyrics/narration and storyboard input are your PRIMARY direction: include the SPECIFIC objects,
  actions and setting they describe; never substitute a generic scene.
- Two-pass character scenes: describe the scene and leave appropriate space/boxes for characters; their identity
  is composited later, so describe placement/pose generically here unless a specific name is given.
- Each scene must be visually DISTINCT from the others in the production.
- Output ONLY the JSON object."""


TWO_PASS_COMPOSITE_SYSTEM_PROMPT = """You write a SHORT, literal EDIT INSTRUCTION for FLUX.2 Klein 9B — an image EDIT model that ALREADY SEES the
reference images attached to this job. You are NOT describing a scene from scratch; you are telling the model
what to ADD to images it can already see. Treat this like Photoshop directions, not a T2I prompt.

WHAT THE IMAGES ARE (always in this order):
- Image 1 = the finished BASE SCENE (environment, composition, lighting, palette). KEEP IT. Do NOT re-describe
  its contents — the model can see them. You only NAME its lighting/palette briefly to lock them.
- Image 2 (and Image 3, 4 … if present) = the CHARACTER(S) to insert. Each is used ONLY for face/identity,
  body shape, and clothing silhouette.

HARD RULES:
- Refer to every subject ONLY as "Image 1", "Image 2", "the character in Image 2", etc. NEVER use a character
  name or any proper noun — the model has no idea who a name refers to, so a name is wasted and misleading.
- Do NOT re-describe the scene, the background, props, or each character's full wardrobe/colours. The images
  already carry all of that. State ONLY the edit: which character (by Image number) goes where in Image 1,
  what they are doing/their pose and expression, and that they are re-lit to match Image 1.
- PRESERVE IMAGE 1's LOOK: keep its exact brightness, exposure, colour grade, and palette. Klein tends to
  darken / dim / re-grade on edits — explicitly tell it to keep Image 1's lighting and to NOT darken, dim, or
  restyle. Re-light the inserted character(s) to match Image 1's light direction and palette.
- Output ONE concise instruction, ~20–60 words, a single paragraph. No lists, no labels, no names, no text or
  watermarks, no preamble.

SHAPE TO AIM FOR (an instruction, not a description):
"Place the character from Image 2 standing at the left of the scene in Image 1, turned toward the gate with a
braced, weary stance, re-lit to match Image 1's dawn light and exact exposure; keep Image 1's palette and
brightness unchanged — do not darken, dim, or restyle it."

If a COLOR PALETTE OVERRIDE is given in the context, it is absolute: re-render the inserted character(s)
entirely within that palette regardless of their reference-photo colours.

IMPORTANT: Output ONLY the instruction text."""

NARRATION_IMAGE_SYSTEM_PROMPT = """You are an expert at writing prompts for FLUX.2 Klein 9B, a reference-image-conditioned AI image generation model.
Your job is to produce a single concise paragraph that the model can render into a clear, high-quality image to illustrate a narration script. FLUX has no prompt upsampling — what you write is what renders, so be concrete, not padded.

CRITICAL FORMATTING RULES:
- Output MUST be a SINGLE PARAGRAPH with NO line breaks, NO bullet points, NO numbered lists.
- Write as one continuous block of concrete descriptive prose. NEVER use quality-booster tags ("masterpiece, 8k, hyperreal, award-winning") or weight syntax like (word:1.3) — FLUX ignores them and boosters degrade output. Refer to any reference images as "image 1"/"image 2" and to subjects by appearance, never by name.
- Front-load the most important visual element — but VARY what that element is. Sometimes lead with the environment, sometimes with action, sometimes with lighting or mood. Do NOT always start the same way.

REFERENCE IMAGE HANDLING (FLUX Klein specific):
- Klein understands natural language references to input images. Use direct, descriptive references.
- With 1 reference image: refer to its content directly — "the person shown in the image", "the figure from the image", "the character in the image" — Klein knows what's in the loaded reference.
- With 2+ reference images: use ordinal language — "the figure from the first image", "the person in the second image", "the environment from the third image".
- CRITICAL: VARY YOUR OPENING every time. Never start multiple prompts with the same phrase. Alternate between leading with setting, action, lighting, mood, or composition.
- Describe what each referenced subject is DOING — their pose, action, expression, and interaction with the scene.
- Include enough visual detail about each referenced subject (clothing, features, body language) to reinforce the reference match.
- NEVER use code-style tags like "@image1" or "img_ref_1" — always use natural descriptive language.
- CRITICAL — COLOR PALETTE ENFORCEMENT: If a COLOR PALETTE OVERRIDE is specified in the context, it takes ABSOLUTE PRIORITY over everything else. You MUST strictly adhere to the specified color palette. Do NOT introduce ANY colors outside the palette — not in lighting, clothing, environment, materials, skin tones, or any visual element. For example, if the override says "black and white only", you must NEVER mention gold, amber, red, blue, or any chromatic color. Describe everything using ONLY the permitted tones. This rule overrides all other style considerations.

PROMPTING BEST PRACTICES:
- LIGHTING is the single most impactful element — always describe it in detail: direction, color temperature, quality (soft/hard), source.
- Be specific about subjects: "a weathered man in his 50s with deep-set eyes and a salt-and-pepper beard" not "a person".
- Use cinematic and photographic language: describe lens choice, depth of field, camera angle.
- Describe textures and materials: "rough linen", "polished obsidian", "rain-slicked asphalt reflecting neon".
- Keep the output concise, ~30-90 words. Specific and precise beats long and padded.
- Avoid contradictory descriptions or keyword stuffing.
- NEVER include any text, words, letters, subtitles, captions, titles, watermarks, or written content in the image description. The output is a VISUAL scene only — no rendered text of any kind.

SCRIPT-DRIVEN IMAGERY (CRITICAL):
- The narration script text is your PRIMARY creative source. If the script mentions specific objects, people, actions, settings, or concepts — those elements MUST appear visually in the scene description.
- Examples: "the ancient temple" → describe an ancient temple. "a child running through fields" → show a child running through fields. "the microscope revealed hidden structures" → show a microscope with visible cellular structures.
- For abstract or conceptual narration, translate into striking visuals: "the weight of responsibility" → a figure carrying a heavy load on their shoulders against a vast landscape. "knowledge spread across continents" → an aerial view of illuminated cities connected by glowing pathways.
- The script tells you WHAT to show. The concept/style tell you HOW it looks. The flow idea tells you the scene composition. All three work together, but the script comes first for content.
- The viewer should be able to understand the narration's topic from the visuals alone.

If the user provides an existing prompt, tighten and focus it while preserving the core intent.
If the user provides NO prompt (empty or missing), CREATE a new prompt entirely from the provided context — prioritize the narration SCRIPT first, then concept, visual style, characters, and scene flow.

IMPORTANT: Output ONLY the prompt text as a SINGLE PARAGRAPH. No labels, no prefixes, no line breaks, no explanations."""

NARRATION_VIDEO_SYSTEM_PROMPT = """You are an expert at writing prompts for LTX Video 2.3 with the LTXDirector node, an advanced AI video generation system.
Your job is to produce an optimized video generation prompt for a narration video following LTX Director's specific capabilities and requirements.

UNDERSTANDING LTX DIRECTOR — PROMPT RELAY & SEGMENTS:
LTX Director uses a "Prompt Relay" system where the video prompt can contain MULTIPLE SEGMENTS separated by line breaks.
- Each line break creates a NEW temporal segment in the video, played sequentially.
- The FIRST segment starts at frame 0. Each subsequent segment continues from where the previous one ended.
- Segments are distributed evenly across the total video duration unless explicit frame numbers are given.
- When "stitch mode" is OFF, segments have SHARP CUTS between them.
- When "stitch mode" is ON, segments cross-dissolve smoothly into each other — preferred for narration.

WHEN TO USE SINGLE vs MULTI-SEGMENT PROMPTS:
- DEFAULT: Write a SINGLE PARAGRAPH (one segment) for most scenes. This produces one cohesive, continuous shot — ideal for narration scenes with a single visual subject.
- MULTI-SEGMENT: Use 2-3 segments (separated by line breaks) ONLY when the narration script clearly describes DISTINCT sequential actions or transitions within the same clip.
  - Example (2 segments for a narrative shift): "Wide establishing shot of a quiet library interior, golden afternoon light streaming through tall windows, dust particles floating in the warm beams.\nSlow dolly in toward a single open book on a wooden desk, pages illuminated by a desk lamp, shadows deepening in the background."
  - Example (3 segments for a documentary sequence): "Close-up of a scientist's hands adjusting a microscope dial, soft lab lighting casting a cool blue glow.\nMedium shot revealing a row of microscopes in a research lab, researchers working intently at their stations.\nWide shot pulling back through a window to show the full research facility against a twilight sky."
- NEVER use more than 3 segments per clip. Most clips should be 1 segment.
- Each segment should be a complete visual description (40-80 words), not a fragment.

CRITICAL FORMATTING RULES:
- Single-segment prompts: ONE paragraph, NO line breaks, flowing descriptive prose.
- Multi-segment prompts: Each segment is its own paragraph separated by exactly ONE blank line. Each segment must be self-contained and visually complete.
- NEVER use bullet points, numbered lists, labels, or headers in any format.

REFERENCE IMAGE AWARENESS (KEYFRAME IMAGES):
- LTX Director can accept keyframe images that guide the visual output. When a first-frame or last-frame image is attached, the video will visually match those images.
- Your prompt should COMPLEMENT the keyframe images, not contradict them. Describe the ACTION and MOTION that happens between the keyframes.
- Focus your prompt on what MOVES and CHANGES, since the keyframe images already define what things LOOK like.

PROMPTING BEST PRACTICES:
- Use present tense and active voice: "A woman walks through the rain" not "A woman walking" or "A woman will walk".
- Be specific about subjects: "a man in his 40s with a weathered face and dark coat" not "a person".
- Describe the action/motion clearly — this is VIDEO, not a still image. What moves? How? At what pace?
- Emphasize SMOOTH, DELIBERATE camera work suitable for narration: "slow tracking shot", "gentle dolly push", "static wide angle", "steady crane rising", "smooth pan across".
- Avoid rapid or aggressive camera movements unless the narration calls for urgency.
- Specify lighting, atmosphere, and visual texture: "warm golden hour light filtering through dust particles", "harsh fluorescent overhead lighting casting sharp shadows".
- Match prompt detail to video duration. Short clips (3-5s) need focused, concise single-segment prompts. Longer clips (8-15s) can use more detail or multiple segments.
- Avoid contradictory descriptions within a single segment.
- NEGATIVE PROMPT is handled separately by the system — do NOT include negative instructions in your prompt.
- CRITICAL — COLOR PALETTE ENFORCEMENT: If a COLOR PALETTE OVERRIDE is specified in the context, it takes ABSOLUTE PRIORITY over everything else. You MUST strictly adhere to the specified color palette. Do NOT introduce ANY colors outside the palette — not in lighting, clothing, environment, materials, skin tones, or any visual element. For example, if the override says "black and white only", you must NEVER mention gold, amber, red, blue, or any chromatic color. Describe everything using ONLY the permitted tones. This rule overrides all other style considerations.

CINEMATOGRAPHY VOCABULARY — use these terms naturally in your prompts for precise visual direction:
- SHOT SIZE: extreme wide shot (subject tiny in environment), wide shot (full body), medium shot (waist up), medium close-up (chest up), close-up (face fills frame), extreme close-up (eyes, lips, hands, object detail), insert shot (tight on a specific object or detail).
- CAMERA ANGLE: eye level (neutral), low angle (looking up — authority), high angle (looking down — vulnerability, overview), bird's eye / top-down (straight down 90° — patterns, scope), Dutch angle (tilted axis — unease), over the shoulder / OTS (past one person at another), POV / subjective (camera is the character's eyes), profile / side angle (90° to eyeline — cinematic, graphic).
- CAMERA MOVEMENT: pan (rotate left/right), tilt (rotate up/down), dolly / track (physically move toward/away/sideways), push in / pull out (slow dolly for emotional emphasis), pedestal (camera rises/descends vertically), crane / jib (arc up/down), orbit / arc (circle the subject), rack focus (shift focus between planes).
- COMPOSITION: rule of thirds, symmetrical / center framing, negative space, leading lines, foreground/background layering.
- For narration, favor shot sizes and angles that reinforce the narrative: wide shots for establishing context, medium shots for subjects, close-ups for emotional beats, insert shots for key details the narrator mentions.

STRUCTURE (per segment):
Start with the scene anchor (setting/environment), then subject and their action, then camera movement and framing, then visual style and mood.

SCRIPT-DRIVEN VIDEO CONTENT (CRITICAL):
- The narration script text is your PRIMARY creative source for what happens in the video. If the script mentions specific actions, objects, people, or events — the video MUST show them.
- Examples: "the cells divide rapidly" → show cell division under a microscope. "she walked along the shore" → show a figure walking along a shoreline. "the city transformed over decades" → show a timelapse-style urban transformation.
- For abstract narration, translate into visually dynamic motion: "understanding grew" → a slow reveal of an illuminated landscape. "the discovery changed everything" → a dramatic dolly push toward a glowing object.
- The script tells you WHAT happens. The storyboard tells you HOW to film it. Both work together.
- Visual storytelling should enhance and illustrate the spoken narration, not distract from it.

If the user provides an existing prompt, enhance it for optimal LTX Director output while preserving the core intent.
If the user provides NO prompt (empty or missing), CREATE a new prompt entirely from the provided context — prioritize the narration SCRIPT first for content, then use the storyboard for composition and camera.

Keep single-segment prompts between 50-200 words. Multi-segment prompts should have 40-80 words per segment.
IMPORTANT: Output ONLY the prompt text. No labels, no prefixes, no explanations. If multi-segment, separate segments with blank lines only."""

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
        "image": Z_IMAGE_SYSTEM_PROMPT,
        "image_last_frame": LAST_FRAME_IMAGE_SYSTEM_PROMPT,
        "two_pass_base": TWO_PASS_BASE_SYSTEM_PROMPT,
    },
    "krea2": {
        "image": KREA2_IMAGE_SYSTEM_PROMPT,
        "image_last_frame": LAST_FRAME_IMAGE_SYSTEM_PROMPT,
        # Krea 2 used as the Pass-1 base of a two-pass run: reuse the
        # natural-language base-scene prompt (no refs, balanced exposure).
        "two_pass_base": TWO_PASS_BASE_SYSTEM_PROMPT,
    },
    "qwen_edit": {
        "image": QWEN_EDIT_SYSTEM_PROMPT,
        "image_last_frame": LAST_FRAME_IMAGE_SYSTEM_PROMPT,
    },
    # Video models
    "ltx_2.3": {
        "video": VIDEO_SYSTEM_PROMPT,
    },
    "wan_2.2": {
        "video": VIDEO_SYSTEM_PROMPT,
    },
    # Narration-specific prompts (used when project mode is narration)
    "narration_image": {
        "image": NARRATION_IMAGE_SYSTEM_PROMPT,
    },
    "narration_video": {
        "video": NARRATION_VIDEO_SYSTEM_PROMPT,
    },
}

# Generic fallback prompts (used when a model has no specific built-in)
_GENERIC_DEFAULTS: dict[str, str] = {
    "image": IMAGE_SYSTEM_PROMPT,
    "image_last_frame": LAST_FRAME_IMAGE_SYSTEM_PROMPT,
    "video": VIDEO_SYSTEM_PROMPT,
    "two_pass_base": TWO_PASS_BASE_SYSTEM_PROMPT,
    "two_pass_composite": TWO_PASS_COMPOSITE_SYSTEM_PROMPT,
    "narration_image": NARRATION_IMAGE_SYSTEM_PROMPT,
    "narration_video": NARRATION_VIDEO_SYSTEM_PROMPT,
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

        if provider == "ollama":
            result = PromptEnhancer._enhance_ollama(prompt, context, api_key, model, system_prompt)
        elif provider == "openai":
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
            client = OpenAI(api_key=api_key, timeout=600.0)

            if prompt and prompt.strip():
                user_message = f"Original prompt: {prompt}"
            else:
                user_message = "No prompt provided. Generate a new prompt entirely from the context below."
            if context:
                user_message += f"\n\nContext: {context}"

            effective_model = model or "gpt-4-turbo"
            # Newer OpenAI models (GPT-4.1+, GPT-5.x, o-series, chatgpt-* series)
            # require max_completion_tokens instead of the legacy max_tokens parameter
            _new_style = any(
                effective_model.startswith(p)
                for p in ("gpt-4.1", "gpt-5", "chatgpt", "o1", "o3", "o4")
            )
            enhance_tokens = 800
            extra_params: dict = {}
            if _new_style:
                extra_params["max_completion_tokens"] = enhance_tokens
                # These models only accept temperature=1 (the default)
            else:
                extra_params["max_tokens"] = enhance_tokens
                extra_params["temperature"] = 0.7

            response = client.chat.completions.create(
                model=effective_model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message},
                ],
                **extra_params,
            )

            raw_content = response.choices[0].message.content or ""
            enhanced = _clean_enhanced_prompt(raw_content)
            logger.info(f"Enhanced prompt ({len(enhanced)} chars)")
            _write_enhance_log("openai", effective_model, prompt, context, raw_content, enhanced)
            return enhanced

        except Exception as e:
            logger.error(f"OpenAI enhancement failed: {e}")
            _write_enhance_log("openai", model or "?", prompt or "", context, "", "", str(e))
            raise RuntimeError(f"OpenAI API error: {e}")

    @staticmethod
    def _enhance_ollama(
        prompt: str,
        context: Optional[str],
        api_key: str,  # Contains Ollama URL(s) (passed via resolve_llm_config)
        model: str,
        system_prompt: str = IMAGE_SYSTEM_PROMPT,
    ) -> str:
        """Enhance using a local Ollama server via OpenAI-compatible API.

        Ollama models (especially smaller ones like qwen3:14b) need more
        explicit, structured instructions than cloud models. We:
        1. Use the OpenAI SDK pointed at Ollama's /v1/ endpoint
        2. Wrap the system prompt with extra formatting guardrails
        3. Provide a very explicit user message with step-by-step structure
        4. Use lower temperature for more reliable output

        The ``api_key`` parameter carries one of:
        - A single URL string (e.g. ``"http://localhost:11434"``)
        - A JSON-encoded list of URLs for multi-server round-robin

        resolve_llm_config() provides this value.
        """
        try:
            from openai import OpenAI
        except ImportError:
            raise RuntimeError("OpenAI SDK not installed. Install with: pip install openai")

        # Parse URL(s) from the api_key slot
        raw = api_key or "http://localhost:11434"
        if raw.startswith("["):
            # JSON-encoded list of URLs
            try:
                urls = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                urls = [raw]
        else:
            urls = [raw]

        # Round-robin with failover: try each server starting from the next in rotation
        start_idx = next(_ollama_rr_counter) % len(urls)
        last_error = None
        for attempt in range(len(urls)):
            idx = (start_idx + attempt) % len(urls)
            base_url = urls[idx].rstrip("/")
            ollama_api_url = f"{base_url}/v1"
            if attempt > 0:
                logger.warning(f"Ollama failover → trying server {idx + 1}/{len(urls)}: {base_url}")
            else:
                logger.info(f"Ollama round-robin → server {idx + 1}/{len(urls)}: {base_url}")

            try:
                # Ollama's OpenAI-compatible endpoint needs a dummy API key
                client = OpenAI(
                    api_key="ollama",
                    base_url=ollama_api_url,
                    timeout=600.0,
                )

                # Build a more explicit, structured system prompt for local models.
                # Smaller models need very clear instructions about what NOT to do.
                ollama_system_prompt = f"""{system_prompt}

CRITICAL RULES FOR YOUR RESPONSE (FOLLOW EXACTLY):
1. Output ONLY the enhanced prompt text — nothing else.
2. Do NOT start with "Enhanced prompt:", "Here is", "Sure!", "Okay", or any prefix.
3. Do NOT add explanations, notes, or commentary before or after the prompt.
4. Do NOT use bullet points, numbered lists, or markdown formatting.
5. Do NOT wrap your output in quotes.
6. Write as ONE continuous paragraph of flowing descriptive prose.
7. If thinking step by step, do your reasoning silently — output ONLY the final prompt.
8. Your entire response should be usable as-is for image/video generation."""

                # Build a very structured user message for local models
                if prompt and prompt.strip():
                    user_parts = [
                        "TASK: Enhance the following prompt for AI image/video generation.",
                        f"ORIGINAL PROMPT: {prompt}",
                    ]
                else:
                    user_parts = [
                        "TASK: Create a new AI image/video generation prompt from the context below.",
                        "There is no original prompt — generate one entirely from the context.",
                    ]

                if context:
                    user_parts.append(f"CONTEXT (use this for creative direction):\n{context}")

                user_parts.append(
                    "REMEMBER: Output ONLY the prompt text as a single paragraph. "
                    "No prefixes, no explanations, no markdown, no quotes. Just the prompt."
                )

                user_message = "\n\n".join(user_parts)

                effective_model = model or "qwen3:14b"
                response = client.chat.completions.create(
                    model=effective_model,
                    messages=[
                        {"role": "system", "content": ollama_system_prompt},
                        {"role": "user", "content": user_message},
                    ],
                    max_tokens=800,
                    temperature=0.6,  # Lower than cloud — local models drift more at higher temps
                )

                raw_content = response.choices[0].message.content or ""

                # Extra cleanup for local models that tend to add thinking blocks
                # or chain-of-thought before the actual output
                cleaned = raw_content.strip()

                # Strip <think>...</think> blocks that qwen3 models produce
                import re
                cleaned = re.sub(r'<think>.*?</think>', '', cleaned, flags=re.DOTALL).strip()

                # Strip common local-model prefixes
                for prefix in [
                    "Here is the enhanced prompt:",
                    "Here's the enhanced prompt:",
                    "Enhanced prompt:",
                    "Sure! Here",
                    "Sure,",
                    "Okay,",
                    "Here you go:",
                ]:
                    if cleaned.lower().startswith(prefix.lower()):
                        cleaned = cleaned[len(prefix):].strip()
                        break

                enhanced = _clean_enhanced_prompt(cleaned)
                logger.info(f"Enhanced prompt via Ollama ({effective_model}, {len(enhanced)} chars)")
                _write_enhance_log("ollama", effective_model, prompt, context, raw_content, enhanced)
                return enhanced

            except (ConnectionError, OSError, TimeoutError) as e:
                # Server unreachable — try next one
                last_error = e
                logger.warning(f"Ollama server {idx + 1}/{len(urls)} unreachable: {e}")
                continue
            except Exception as e:
                # Non-connection error (model error, bad response, etc.) — don't failover
                logger.error(f"Ollama enhancement failed: {e}")
                _write_enhance_log("ollama", model or "?", prompt or "", context, "", "", str(e))
                raise RuntimeError(f"Ollama API error: {e}")

        # All servers exhausted
        logger.error(f"All {len(urls)} Ollama servers unreachable")
        _write_enhance_log("ollama", model or "?", prompt or "", context, "", "", str(last_error))
        raise RuntimeError(f"All Ollama servers unreachable. Last error: {last_error}")

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
            import httpx as _httpx
            client = Anthropic(
                api_key=api_key,
                timeout=_httpx.Timeout(timeout=600.0, connect=10.0),
            )

            if prompt and prompt.strip():
                user_message = f"Original prompt: {prompt}"
            else:
                user_message = "No prompt provided. Generate a new prompt entirely from the context below."
            if context:
                user_message += f"\n\nContext: {context}"

            effective_model = model or "claude-sonnet-4-20250514"
            response = client.messages.create(
                model=effective_model,
                max_tokens=800,
                system=system_prompt,
                messages=[
                    {"role": "user", "content": user_message},
                ],
            )

            raw_content = response.content[0].text or ""
            enhanced = _clean_enhanced_prompt(raw_content)
            logger.info(f"Enhanced prompt ({len(enhanced)} chars)")
            _write_enhance_log("anthropic", effective_model, prompt, context, raw_content, enhanced)
            return enhanced

        except Exception as e:
            logger.error(f"Anthropic enhancement failed: {e}")
            _write_enhance_log("anthropic", model or "?", prompt or "", context, "", "", str(e))
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
                    max_output_tokens=800,
                    temperature=0.7,
                ),
            )

            raw_content = response.text or ""
            enhanced = _clean_enhanced_prompt(raw_content)
            logger.info(f"Enhanced prompt ({len(enhanced)} chars)")
            _write_enhance_log("gemini", model or "gemini-pro", prompt, context, raw_content, enhanced)
            return enhanced

        except Exception as e:
            logger.error(f"Gemini enhancement failed: {e}")
            _write_enhance_log("gemini", model or "?", prompt or "", context, "", "", str(e))
            raise RuntimeError(f"Gemini API error: {e}")


# ── Ideogram / structured-JSON caption helpers ───────────────────────────────
import json as _json
import re as _re


def extract_json_object(text):
    """Best-effort extract a JSON object from an LLM response (strips ``` fences,
    grabs the outermost {...}). Returns a dict or raises ValueError."""
    if isinstance(text, dict):
        return text
    if not isinstance(text, str):
        raise ValueError("LLM caption response is not text")
    t = text.strip()
    # strip markdown code fences
    if t.startswith("```"):
        t = _re.sub(r"^```[a-zA-Z0-9]*\s*", "", t)
        t = _re.sub(r"\s*```$", "", t).strip()
    # outermost object
    start = t.find("{")
    end = t.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("no JSON object found in caption response")
    return _json.loads(t[start:end + 1])


def _norm_hex(c):
    c = str(c).strip().upper()
    if not c.startswith("#"):
        c = "#" + c
    if len(c) == 4:  # #RGB -> #RRGGBB
        c = "#" + "".join(ch * 2 for ch in c[1:])
    return c[:7]


def _clamp01(v, default=0.0):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return default
    return max(0.0, min(1.0, v))


def normalize_ideogram_caption(obj):
    """Validate + clamp an LLM (or hand-edited) Ideogram caption into the canonical
    contract dict: uppercase hex, clamped 0-1 coords (x+w<=1, y+h<=1), palette caps
    (16 global / 5 per element), obj/text element types. Raises ValueError if the
    object is unusable (no description and no elements)."""
    if isinstance(obj, str):
        obj = extract_json_object(obj)
    if not isinstance(obj, dict):
        raise ValueError("caption is not a JSON object")

    style = str(obj.get("style", "photo")).strip().lower()
    style = "photo" if style.startswith("photo") else "art"
    medium = str(obj.get("medium", "")).strip().lower() or ("photograph" if style == "photo" else "illustration")

    out = {
        "high_level_description": str(obj.get("high_level_description", "")).strip(),
        "background": str(obj.get("background", "")).strip(),
        "style": style,
        "style_detail": str(obj.get("style_detail", "")).strip(),
        "aesthetics": str(obj.get("aesthetics", "")).strip(),
        "lighting": str(obj.get("lighting", "")).strip(),
        "medium": medium,
        "style_palette": [_norm_hex(c) for c in (obj.get("style_palette") or []) if c][:16],
        "elements": [],
    }
    for e in (obj.get("elements") or []):
        if not isinstance(e, dict):
            continue
        t = "text" if str(e.get("type", "obj")).strip().lower() == "text" else "obj"
        x, y = _clamp01(e.get("x", 0)), _clamp01(e.get("y", 0))
        w, h = _clamp01(e.get("w", 0)), _clamp01(e.get("h", 0))
     
        if x + w > 1:
            w = max(0.0, 1.0 - x)
        if y + h > 1:
            h = max(0.0, 1.0 - y)
        ne = {
            "type": t,
            "text": str(e.get("text", "")).strip() if t == "text" else "",
            "desc": str(e.get("desc", "")).strip(),
            "palette": [_norm_hex(c) for c in (e.get("palette") or []) if c][:5],
            "x": round(x, 4), "y": round(y, 4), "w": round(w, 4), "h": round(h, 4),
        }
        out["elements"].append(ne)

    if not out["high_level_description"] and not out["elements"] and not out["background"]:
        raise ValueError("caption has no usable content")
    return out
