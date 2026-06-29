# Per-model prompt rules (first-pass image + edit models)

Our LLM writes the generation prompt, so its **system prompt must match the model
that will actually render**. These are the rules baked into
`backend/services/llm/prompt_enhancer.py`, grounded in each model's official
guidance + high-signal community findings. The cross-cutting rule for **all** of
them: no quality-booster spam (`masterpiece, 8k, ultra-detailed, hyperreal, HDR,
award-winning, trending on artstation`), no weight syntax `(word:1.3)`, no
negative prompts (inert at the CFG these run), and **no character names** (image
models can't use them — describe by appearance).

| Model | Prompt | Style | Length | Refs |
|---|---|---|---|---|
| **Z-Image Turbo** (Tongyi) | `Z_IMAGE_SYSTEM_PROMPT` | structured camera-direction prose, literal | ~70–160w | none (pure T2I) |
| **Krea 2 / FLUX Krea** | `KREA2_IMAGE_SYSTEM_PROMPT` | natural prose, **fewest** modifiers | ~30–110w | none |
| **FLUX.2 Klein / FLUX.1** | `IMAGE_SYSTEM_PROMPT` | concise prose, edit-aware | ~30–90w | `image 1/2`, edit-style |
| **Qwen-Image-Edit** | `QWEN_EDIT_SYSTEM_PROMPT` | imperative edit instruction | 1–3 sentences | `image 1/2/3` roles |
| **Two-pass base (Z-Image)** | `TWO_PASS_BASE_SYSTEM_PROMPT` | scene-only, no characters | ~60–140w | none |
| **Two-pass composite (Klein)** | `TWO_PASS_COMPOSITE_SYSTEM_PROMPT` | short **edit instruction** | ~20–60w | `image 1`=base, `image 2+`=chars |

### Why each is what it is
- **Z-Image Turbo** is a literal instruction-follower that **blows out highlights
  when fed booster spam + high CFG**. So: concrete art-direction, 3–5 core
  concepts, motivated single light source, bake "negatives" positively ("sharp
  focus", not "no blur"). No reference language (it's pure T2I). Sources: Tongyi HF
  card/discussions, wavespeed CFG guide, community prompting gist.
- **Klein / FLUX.2** has **no prompt upsampling** — boosters render generic mush.
  For references it wants **edit-instruction phrasing** ("the subject from image 1
  doing X", what *changes*/*combines*) not a re-description, and **`image 1/2`**
  (lowercase canonical per BFL; capitalised also parses). Avoid the word "enhance"
  in image-to-image. Lighting is the single most impactful element. Our prompt also
  handles the no-reference case gracefully. Sources: BFL FLUX.2/Klein guides, fal,
  neurocanvas.
- **Krea** was post-trained specifically to **remove the "AI look"** (waxy skin,
  blown highlights), so adding "vivid/ultra-detailed/sharp" pushes it *back* toward
  that look. Use the fewest descriptors and let its aesthetic work. Sources: Krea +
  BFL blogs.
- **Qwen-Image-Edit** is instruction-driven: imperative ("change X to Y, keep the
  rest"), `image 1/2/3` roles, quoted literal text (best-in-class text rendering).
  Sources: Qwen HF cards, ComfyUI docs.

### Routing (which prompt the LLM uses)
The **manual Enhance** button resolves the prompt model from what will render: a
**no-reference** image goes to the first-pass generator (Z-Image or Krea 2, per
the `single_image_generator` setting) → that model's prompt; **with references**,
the Klein edit model composites them → the Klein prompt
(`backend/api/generation.py`, the enhance endpoint).

> Known refinement: the **auto-gen / batch** enhance currently uses the shared
> Klein image prompt for first-pass scenes (it now degrades gracefully for the
> no-reference case — concise, no boosters, no names, "describe directly"). Wiring
> auto-gen to the dedicated Z-Image/Krea2 prompts per scene is a clean follow-up
> (the enhance call sites live across several auto-gen functions).

## First/Last frame for LTX 2.3 I2V (v1.17.1)

LTX 2.3's image-to-video guidance: the source (first frame) image defines the START; the video prompt should describe the MOTION, and the first frame should NOT be overloaded. So:

- **First frame (animated scenes only — music_video / narration_video):** depict the OPENING moment / calm starting state — key subject(s), setting, lighting as the shot opens, before the action. Don't pack in every action/element the video will reveal (the video step generates those from its own prompt). Frame as the shot opens (the model won't reframe); keep lighting clean (it propagates). This is injected as a "VIDEO STARTING FRAME" context block in `_build_auto_enhance_context` (auto) and `buildEnhanceContext` (manual), and reinforced in `IMAGE_SYSTEM_PROMPT` / `Z_IMAGE_SYSTEM_PROMPT` / `KREA2_IMAGE_SYSTEM_PROMPT`.
- **Standalone stills (narration_images):** NOT gated — the image is the final deliverable, so it still depicts the full scene.
- **Last frame:** it's the END keyframe the video interpolates to — one clean endpoint, not a packed montage; the video prompt carries the motion between the two keyframes (`LAST_FRAME_IMAGE_SYSTEM_PROMPT`).
