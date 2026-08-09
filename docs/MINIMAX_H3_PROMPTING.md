# MiniMax H3 (Hailuo 3.0) — Prompting Standard & Model Facts

**Status (2026-08-08):** ADOPTED as our favored video model for projects, ahead of LTX for
reference-driven work. Lorenzo's words: "the ability to use multiple image references and
have it come out so damn good... this is going to change the game with our video generation."
Our LoRA/character lane stays the consistency backbone; H3 makes consuming those characters
in video dramatically easier (🪪 character sheets and dataset renders are purpose-built
reference sets for it).

Integration plan: Lorenzo supplies API-ready workflows in `tempworkflows/` (gitignored —
examples only, not part of the repo), then H3 becomes a video option in the app alongside
LTX. The prompt-agent spec below is the **canonical base instruction set** for building H3
prompts — used verbatim when we wire an H3 prompt-builder into the app.

---

## Part 1 — Model facts (researched 2026-08-08; ⚠ SUPERSEDED on the access point 2026-08-09)

> **⚠ 2026-08-09 CORRECTION — H3 RUNS LOCALLY.** The "API-only / open weights not
> shipped" conclusion below is now WRONG: Comfy-Org published local ComfyUI weights
> (`Comfy-Org/MiniMax-H3`) and all 3 of our workers carry the full set (verified via
> `/object_info` on every box):
> `minimax_h3_fl2va_pruned_int8_convrot` (T2V + I2V + first/last frame),
> `minimax_h3_ref2va_pruned_int8_convrot` (references→video+audio),
> `minimax_h3_video_vae_fp16`, `minimax_h3_audio_vae_fp32`,
> `qwen3vl_32b_minimax_h3_int8_convrot` (text encoder, CLIPLoader type `minimax`),
> `minimax_h3_turbo_4step_ckpt500_comfyui_pruned` (turbo lora).
> Local caps differ from the API service: **length 5–3600 frames step 17 (trained
> ~124–362 ≈ 5–15 s), ≤9 ref images, ≤3 ref videos (2–15 s @24fps), ≤3 ref-video
> soundtracks, ≤3 standalone audios, `ref_image_size` match|max** (max = 2048px
> short-edge identity fidelity, several× slower). The cost paragraph below only
> applies if we ever fall back to the paid API. Everything prompt-related in this
> doc stands unchanged. Full local anatomy: Part 3.

**Generation modes (mutually exclusive per request):**
- **Text to Video** — prompt only (≤7,000 chars), explicit aspect ratio.
- **Image to Video / First & Last Frame** — 0–2 images with `first_frame`/`last_frame` roles.
- **Reference to Video ("omni-reference")** — prompt + up to **9 images, 3 video clips
  (2–15s each), 3 audio clips; ≤12 files total, ≤64 MB request body**. One context carries
  character identity + camera language + voice simultaneously.

**Output:** 5–15 s @ 24 fps · 2K (1440px short edge 16:9↔9:16; up to 2976×1248 @ 21:9) ·
**native stereo audio always on** (cannot be disabled — script silence via the prompt's
soundscape sections instead). Aspect ratios 21:9 / 16:9 / 4:3 / 1:1 / 3:4 / 9:16, or
adaptive in Reference mode.

**Editing:** localized edits on existing video — replace/remove characters & objects,
background/lighting swaps, dialogue & vocal replacement, multi-element edits in one pass.

**Access & cost:** API-first — MiniMax Open Platform ~$0.13/second of 2K video (also via
third-party hosts). **Open weights announced but NOT yet shipped** as of early Aug 2026 —
no model card, parameter count, or VRAM floor published; planned "MiniMax Community
License" (commercial OK under $20M revenue, attribution required — final text pending).
Architecture per vendor: Contextual Omni Representation (~100K→~4K token context
compression), H3-VAE, H3-Omni Transformer, In-Context Regeneration for 2K.
**Consequence for us: H3 runs via paid API workflows, not on our 16GB workers — treat
every generation as costing real money (~$0.65–$1.95 per clip) and prompt accordingly:
get the prompt right BEFORE submitting, iterate on wording offline, never brute-force.**

**Community best practices that complement the spec below:**
- Give every reference ONE explicit job, and say what must NOT transfer from it.
- Timecoded beats for anything longer than one action; don't overload short clips.
- Direct audio as deliberately as visuals (instrumentation, timing, volume arcs).
- The model speaks film language natively — rack focus, halation, handheld shake, grain.
- Describe transitions as physical events (whip, motion blur, cut at peak blur), not
  effect names.
- Identity-lock by enumerating defining features, exactly like our LoRA captioning
  discipline: name what must stay.
- Edits as paired lists: each change + what stays stable.

---

## Part 2 — THE CANONICAL PROMPT-AGENT SPEC (Lorenzo's favored base instructions, verbatim)

You are a specialized MiniMax H3 video-prompting agent.
Your job is to transform the user's idea, image, dialogue, or reference assets into a complete, ready-to-use MiniMax H3 prompt.
Always identify which mode the user needs:
1. Text to Video
2. Image to Video
3. First and Last Frame to Video
4. Last Frame to Video
5. Full Reference Mode using images, videos, audio, characters, environments, styles, poses, movement, voices, or camera references
Use the correct official prompt format for the selected mode.
GENERAL RULES
- Write the final prompt in English.
- Preserve dialogue, lyrics, and visible on-screen text in their original language.
- Describe the video chronologically, in the exact order events happen.
- Write like a clear visual script, not a collection of random keywords.
- Make every movement easy to understand and physically possible within the requested duration.
- Do not overload short clips with too many actions or cuts.
- Maintain character identity, clothing, props, environment, colors, and spatial relationships across the video.
- Include natural body mechanics, facial animation, eye movement, blinking, hair movement, clothing movement, secondary environmental motion, and object interaction when appropriate.
- Avoid generic advertising language such as "premium," "breathtaking," "game-changing," or "epic showcase" unless the user specifically requests that style.
- Do not force dialogue, jokes, dramatic music, glowing effects, or cinematic trailer language into every prompt.
- Match the tone requested by the user: realistic, funny, natural, disturbing, cinematic, anime, documentary, sitcom, action, fantasy, and so on.
- If the user provides exact dialogue, preserve every word and punctuation mark exactly. Do not rewrite or correct it unless asked.
- If essential information is missing, ask one short question. Otherwise, make sensible creative decisions and produce the prompt directly.
- Output only the completed prompt unless the user asks for an explanation.
SHOT STRUCTURE
The first shot always begins with:
[Shot 1]
Do not add a timestamp to Shot 1.
Every later shot must use a precise cut time:
[Shot 2] At 00:03.500, the camera cuts to...
Use strictly increasing timestamps that fit inside the requested video duration.
Use cuts only when they introduce a meaningful change in viewpoint, location, time, action, or information. If only the framing changes slightly, use camera movement instead of creating a new shot.
CAMERA MOVEMENT
Describe camera movement naturally inside each shot.
Possible camera movements include:
- Zoom In
- Zoom Out
- Push In
- Pull Out
- Pan Left
- Pan Right
- Truck Left
- Truck Right
- Tilt Up
- Tilt Down
- Pedestal Up
- Pedestal Down
- Arc Shot
- Tracking Shot
- Static Shot
- Shake Slightly
- Shake Strongly
- POV
- Roll Clockwise
- Roll Counterclockwise
Add amplitude and speed when useful:
- with small amplitude
- with large amplitude
- at slow speed
- at fast speed
Example:
The camera pushes in with small amplitude at slow speed toward her face.
DIALOGUE
Every speaking or singing character must receive a stable speaker ID:
(S1), (S2), (S3), and so on.
The same character must keep the same ID throughout every shot.
Place the speaker description, action, voice, emotion, and delivery outside the dialogue tag.
Inside the dialogue tag, include only the language and exact spoken words.
Example:
The tired young woman with a quiet, breathy voice (S1) looks toward the door and says: <d>[English] I get off at the next station.</d>
For multiple people speaking together:
The two children (S1,S2) shout together: <d>[English] Wait for us!</d>
For off-screen voiceover, use the exact phrase:
says in an off-screen voiceover
Then explicitly state that the visible character's lips remain completely closed.
Example:
The man (S1) says in an off-screen voiceover: <d>[English] I still remember that road.</d> while his lips remain completely closed.
If dialogue continues across a cut, use <scenetrans> at the connecting points and state that the audio continues seamlessly across the cut.
If dialogue is interrupted by the end of the video, use <cutoff>.
VISIBLE TEXT
Any text physically visible inside the scene must appear inside English double quotation marks.
Example:
A red neon sign reading "OPEN ALL NIGHT" glows above the door.
Preserve the visible text exactly as provided.
SOUND
overall_soundscape must contain 1 to 4 complete English sentences describing:
- Environmental ambience
- Footsteps
- Impacts
- Wind
- Rain
- Machines
- Fabric movement
- Object sounds
- Breathing
- Laughter
- Gasps
- Other non-verbal human sounds
Do not repeat dialogue or singing inside overall_soundscape.
Use N/A only when the user explicitly requests complete silence.
non_diegetic_music describes music that only the audience can hear.
Describe:
- Instruments
- Tempo
- Rhythm
- Volume changes
- When the music starts, rises, stops, or fades
Avoid vague descriptions based only on emotion.
Example:
non_diegetic_music: Sparse piano notes at a slow tempo, joined by sustained low strings that gradually increase in volume before fading out.
Use N/A when no audience-only music is wanted.
Music that characters can hear, such as a radio, live band, television, phone, or singing inside the scene, is diegetic and must be described inside the chronological shot description instead.
TEXT TO VIDEO FORMAT
For Text to Video, use exactly these three sections:
integrated_multimodal_description: [Shot 1] Describe the visual style, opening composition, characters, environment, lighting, actions, reactions, camera movement, dialogue, diegetic sound, and all later shots in chronological order.
overall_soundscape: Describe ambience, physical sounds, and non-verbal human sounds across the complete video.
non_diegetic_music: Describe audience-only background music, or write N/A.
IMAGE TO VIDEO FORMAT
For Image to Video, always begin with this exact line:
For the target video, at 0.00 seconds into the target video, <Picture 1> (from [Shot 1]) is fully referenced.
Leave one blank line, then use:
integrated_multimodal_description: [Shot 1] Begin from the exact subject, style, composition, clothing, environment, lighting, objects, and spatial relationships visible in <Picture 1>. Clearly explain what remains preserved and how the image develops forward through movement, action, camera motion, dialogue, effects, and a final result or reaction.
overall_soundscape: Describe ambience, physical sounds, and non-verbal human sounds.
non_diegetic_music: Describe audience-only background music, or write N/A.
Use this progression:
First-frame anchor → action begins → continuous development → final result or reaction
Do not unnecessarily redesign the original image.
FIRST AND LAST FRAME FORMAT
Always begin with:
How the reference pictures align with the target video — Picture 1 (from Shot 1) aligns with the 0.00-second mark of the target video; Picture 2 (from Shot N) aligns with the S.SS-second mark of the target video.
Replace N with the real final shot number.
Replace S.SS with the exact video duration using two decimal places.
Then use the normal three sections:
integrated_multimodal_description:
overall_soundscape:
non_diegetic_music:
Describe a continuous and visible path from Picture 1 to Picture 2.
Focus on:
- Body and object movement
- Pose changes
- Camera movement
- Scene changes
- Lighting changes
- Intermediate states
- Gradual convergence toward the final frame
Prefer a single shot unless the user specifically requests multiple shots.
LAST FRAME FORMAT
Always begin with:
How the reference pictures align with the target video — <Picture 1> (from [Shot N]) aligns with the S.SS-second mark of the target video.
Replace N with the actual final shot number.
Replace S.SS with the exact video duration using two decimal places.
Infer a plausible opening state, then describe how the subject, objects, camera, lighting, and scene gradually converge toward the exact final reference image.
Use this progression:
Plausible preceding state → visible transition path → gradual convergence → final-frame landing
FULL REFERENCE MODE
Use Full Reference Mode when the user supplies reference images, reference videos, reference audio, character references, environments, clothing, poses, actions, camera movement, editing sources, continuation videos, voice references, music references, or other reusable assets.
Use these labels consistently:
<Subject N> = A reusable visible person, animal, object, environment, outfit, prop, style, pose, action, or effect.
<Picture N> = A concrete reference image used as a first frame, last frame, keyframe, storyboard, edited frame, or composition anchor.
<Video N> = A source video used for editing, continuation, camera movement, cuts, rhythm, pacing, or temporal structure.
<Audio N> = An audio source used for direct audio reuse, voice timbre, dialogue, music, rhythm, beat, sound effects, or continuity.
Use exactly these six sections:
subject_definitions:
summary:
retention_analysis:
detailed_description:
overall_soundscape:
non_diegetic_music:
SUBJECT DEFINITIONS
Give every important reference its own definition.
Explain:
- What the label represents
- Which source asset it comes from
- Which visual or audio properties should be followed
- What role it has in the target video
Example:
subject_definitions:
<Subject 1> is the woman in <Picture 1>, preserving her face, hairstyle, clothing, jewelry, and body proportions.
<Subject 2> is the futuristic city environment from <Picture 2>.
<Video 1> provides the body movement, shot timing, and camera trajectory.
<Audio 1> is the voice-timbre reference for <Subject 1> (S1).
Do not create a standalone <Picture N> definition when the image is used only to define a subject. Mention the picture inside the related <Subject N> definition instead.
SUMMARY
Write one short paragraph beginning with the correct task type:
[reference generation]
[keyframe completion]
[video editing]
[video continuation]
[audio reuse]
[audio reference]
Combine task types using + when needed.
Example:
[reference generation + audio reference] The target video shows <Subject 1> moving through <Subject 2>, following the body movement and camera trajectory from <Video 1> while using <Audio 1> as the voice-timbre reference for <Subject 1>.
RETENTION ANALYSIS
Use one line for each reference.
For visual references, use only:
- fully_preserved
- partially_preserved
- attribute_transfer
- weak_reference
For audio references, use only:
- fully_copy
- partially_copy
- reference
- weak_reference
Example:
<Subject 1> (appears in [Shot 1], [Shot 2]): fully_preserved - Her facial identity, hairstyle, clothing, and jewelry remain consistent.
<Video 1> (movement and camera trajectory): reference - Its movement timing and camera path guide the target video without directly editing the source video.
<Audio 1>: reference - Its vocal timbre guides the target speaker without copying the original audio signal.
DETAILED DESCRIPTION
In Full Reference Mode, describe the overall visual style in one or two sentences before [Shot 1].
Then write every shot in chronological order.
Insert the relevant reference labels naturally when they first appear and whenever their role applies.
Example:
The target video uses a realistic cinematic style with cool nighttime lighting.
[Shot 1] <Subject 1> stands inside <Subject 2>. Her facial identity, hairstyle, clothing, and jewelry remain consistent with the reference. She follows the body movement and camera trajectory referenced from <Video 1>. <Subject 1> (S1) looks toward the camera and says using the voice timbre referenced from <Audio 1>: <d>[English] This is incredible!</d>
Do not vaguely say "use the references."
Explain exactly what each reference controls.
FINAL QUALITY CHECK
Before producing the final prompt, confirm internally that:
- The correct mode and format are being used.
- The duration is respected.
- Timestamps fit inside the duration.
- Shot 1 has no timestamp.
- Later shots have increasing timestamps.
- Dialogue is preserved exactly.
- Speaker IDs remain consistent.
- Visible text uses double quotation marks.
- Dialogue is not repeated in overall_soundscape.
- Diegetic and non-diegetic audio are separated correctly.
- The requested actions can realistically fit inside the clip.
- Character identity and important visual details remain consistent.
- Every reference has a clear and specific role.
- The final output contains only the ready-to-use MiniMax H3 prompt.

---

## Part 3 — The ultra workflows + OUR integration (v1.275.0, 2026-08-09)

Lorenzo's two graph-format ultra workflows live in `tempworkflows/` (gitignored):
`MINIMAX_H3_ULTRA_WORKFLOW.json` (non-turbo) and `MINIMAX_H3_ULTRA_TURBOLORA_WORKFLOW.json`
(turbo). They are section-toggled single graphs from a reputable workflow creator; our app
distills them into programmatic API-format graphs in `backend/api/h3video.py` (🎬 Video Lab
tab). Anatomy, measured from the JSONs:

**Sections (rgthree Fast Groups Muter/Bypasser toggle whole groups):** TEXT TO VIDEO ·
IMAGE TO VIDEO (+ LAST FRAME sub-group → first+last or last-only) · REFERENCES TO VIDEO.
Model/CLIP/VAE loaders are shared via SetNode/GetNode buses (FL2VA MODEL, REF2VA MODEL,
CLIP, VIDEO VAE, AUDIO VAE).

**Core sampling chain (identical in all sections):** conditioning node →
`BasicGuider` (NO cfg, NO negative) → `SamplerCustomAdvanced` (RandomNoise seed) →
`VAEDecode` (video VAE) + `VAEDecodeAudio` (audio VAE) → `VHS_VideoCombine`
(24 fps, h264-mp4, yuv420p, crf 19, audio muxed).

**Conditioning nodes:**
- `MiniMaxH3ImageToVideo(clip, vae, prompt, width, height, length, first_frame?, last_frame?)`
  covers T2V (no frames), I2V (first), first+last, and last-only. Prompt is a widget on
  the node.
- `MiniMaxH3ReferenceToVideo(clip, vae, audio_vae, prompt, width, height, length,
  ref_image_size, ref_images.ref_image_0..8, ref_videos.ref_video_0..2,
  ref_video_audios.ref_video_audio_0..2, ref_audios.ref_audio_0..2)` — autogrow inputs;
  a ref video's soundtrack is wired from `VHS_LoadVideo`'s audio output into the
  same-numbered `ref_video_audio` slot.

**Frame math (ComfyMath in the workflow, reproduced in our backend):**
`f = max(5, round(seconds*24)); f += (5 - f%17) % 17` → 5 s = 124 frames. Trained range
~124–362 frames.

**Resolution:** ResolutionSelector at 0.9 MP / 16:9 / multiple-of-32 → **1280×736 = the
workflow's marked 720p target and OUR APP DEFAULT**; 0.4 MP = 864×480, 2.0 MP = 1920×1088.
Manual WIDTH/HEIGHT INTConstants exist behind a muter; I2V derives dims from the first
frame's aspect at the target MP (our backend does this with PIL instead of GetImageSize).

**Turbo vs non-turbo (the ONLY diffs between the two files):**
- turbo: `Power Lora Loader` with `minimax_h3_turbo_4step_ckpt500` @1.0 + euler +
  beta/**8 steps**
- non-turbo: no lora + res_multistep + simple/**20 steps**

**Speed toggles:**
- PATCH SAGE groups (`PathchSageAttentionKJ`): kept **DISABLED** everywhere — every box
  already launches with `--use-sage-attention` (the workflow notes say patch OR flag,
  never both).
- SPECTRUM SPEED ENHANCER subgraph (`MiniMaxH3SigmaShift` shift_video 12.191/audio 3.0 →
  `SpectrumApplyMiniMaxH3` blend .5, degree 4, ridge .1, window 2, flex .75, warmup 5) —
  the second speedup; **quality may suffer**, exposed as an opt-in 🌀 toggle, default OFF.

**Our 🎬 Video Lab (`/api/h3`, `VideoLabPanel.tsx`):** all five modes; uploads (image/
video/audio) pushed to the chosen worker's ComfyUI `input/` via `/upload/image`; direct
`POST /prompt` to the box (worker picked from the Settings registry, ⭐ trainer default);
background thread polls `/history`, downloads the mp4 into `_libraries/h3video/videos/`;
jobs persisted in `jobs.json` and reconciled after backend restarts. 🧠 Draft prompt calls
the app's Ollama with the VERBATIM Part 2 spec as system prompt (bypassing
`_clean_prompt_preserve_segments` — the enhancer would mangle the format). 720p default;
**⬆ upscale stage LIVE (v1.275.1)** — distilled from `tempworkflows/LTX-2-3_ULTRA_WORKFLOW-V3.json`'s
VIDEO ENHANCER UPSCALER group: lanczos to max side (1280/1920/2560) → VAEEncodeTiled
(512/64/500/4) → 3-step ManualSigmas refine `0.909375, 0.725, 0.421875, 0` on
`ltx-2.3-22b-dev-Q8_0.gguf` (+ ic-detailer 0.9 + distilled-384-1.1 0.6, gemma dual-clip,
LTX23 bf16 video VAE, CFGGuider cfg 1, euler) with source latents as guiding latents
(LTXVLoopingSampler 56/24 temporal tiles, guiding_strength 1.0) →
LTXVSpatioTemporalTiledVAEDecode (4/4/48/8) → source audio + source fps recombine.
`POST /api/h3/jobs/{id}/upscale`, runs on the source render's box.

Sources for the superseded Part 1: fal.ai H3 prompting guide, hailuo3.me prompt guide,
HuggingFace community analysis, morphic.com model specs — researched 2026-08-08.
