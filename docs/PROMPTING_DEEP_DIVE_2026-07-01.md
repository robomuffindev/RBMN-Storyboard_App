# RBMN Prompting Deep Dive — Map, Research, Gap Analysis

**Date:** 2026-07-01 · **Version audited:** 1.22.1 (post fix-wave)
**Method:** full code-level map of every prompt path (file:line anchors) + web research against official sources (BFL/FLUX.2, Lightricks/LTX 2.3, Tongyi Z-Image, Krea, Ideogram schema, meta-prompting). This supersedes `PROMPT_SYSTEM_AUDIT.md` / `PROMPT_SYSTEM_OPTIMALITY_REVIEW.md` as the current reference.

**Bottom line:** the image-side auto pipeline is coherent and mostly aligned with official best practice — the doctrine (lyrics-first, never names, positional "Image N" refs, palette-as-hard-constraint, opening-moment first frames, no booster spam) is *correct* and is confirmed by every official source. The problem is that this doctrine is enforced by **five hand-copied implementations** (auto image context, auto video context, frontend manual context, batch runners, dispatch-time builders) that have drifted apart. The highest-leverage move is **consolidation**, not rewriting system prompts.

---

## Part 1 — How prompting works today (the map)

### 1.1 Who writes each generator's prompt

| Generator | Prompt origin | Anchor |
|---|---|---|
| Klein 9B edit (1–5REF) | LLM-enhanced image prompt + dispatch tails | dispatcher.py:1568-1646 |
| Klein T2I | **never runs** — always redirected to first-pass gen | dispatcher.py:1386-1404 |
| Z-Image Turbo | redirect target for ALL 0-ref jobs (incl. two-pass Pass 1) | dispatcher.py:1386-1560 |
| Krea 2 plain / + Ideogram JSON | same prompt / structured caption (stored or lazy-built) | dispatcher.py:1432-1500, 2714-2801 |
| Klein inpaint | **raw user prompt, no LLM** (still gets SFW/style/color tails) | generation.py:6598+, dispatcher.py:1649 |
| LTX i2v / fflf / v2v | LLM video prompt, optionally replaced by Video-JSON | dispatcher.py:2089-2150 |
| LTX sequencer/Director | video prompt + first-200-chars image_description; **only path with a negative-prompt input** | dispatcher.py:1900-1992 |
| LTX transition | static template + `zhuanchang` trigger, no LLM | generation.py:6330 |
| Character creator | **template string, no LLM, tag-style phrasing** | concept.py:784-791, 1476-1482 |

Eleven LLM-writer paths: manual Enhance (image/video), `/auto`, windowed batch, sequential runner, scene-intent, video-JSON (endpoint + lazy dispatch), ideogram caption (endpoint + frontend post-enhance + lazy dispatch), two-pass base (dispatch re-enhance of EVERY Pass 1), two-pass composite, flow generation, vision captions (qwen2.5-VL, cached on asset meta).

### 1.2 Context assembly (auto image FF — the "reference" implementation)

`_build_auto_enhance_context` (generation.py:2545-2869), 19 blocks joined by `" | "`, in order:
USER DIRECTION (llm_instruction) → model line → SCENE INTENT brief (if effective) → scene timing → **PRIORITY ORDER stack** (7 levels) → TARGET CANVAS → VIDEO STARTING FRAME rule (animated modes) → concept/style → NARRATION MODE → GLOBAL PROJECT CONTEXT → image direction → palette (**strict vs grade** split via `_palette_is_strict`) → cast block (cast==refs invariant, ordinal slot labels, no names) → SCENE LYRICS (primary creative source) → SCENE STORYBOARD (flow_idea) → camera → prev-scene continuity → vision descriptions per slot → ideogram ref-layout block.

LF variant swaps the cast block for FF-prompt + slot-offset + CAST-AT-LAST-FRAME + ENTERS-BY-END blocks. System prompt selection: `two_pass_phase` > `is_video` > `frame_type=="last"` > image, then per-model registry, with narration/user override taking precedence over everything (prompt_enhancer.py:882-1004).

### 1.3 The other four implementations (where drift lives)

- **Auto video** (`_build_video_enhance_context`, generation.py:2872-3057): missing the priority stack, strict-vs-grade palette (still old "HIGHEST PRIORITY" wording), scene intent, global project context — and lists **all characters by name, uncapped** (2952-2958), contradicting the never-names doctrine. Has video-only blocks the manual path lacks (SHOT EXTENSION, CAMERA CONTINUITY, LIPSYNC).
- **Manual (frontend TS)** (SceneEditor.tsx:2026-2101 + 2168-2311): re-implements the context in TypeScript. Ref descriptions **include character names** (ReferenceSelector.tsx:392-393); priority stack still says "2-character limit" (2029) vs backend 3; manual video is wrapped in the *image* context builder (image canvas, no video blocks); `prompt_guidance` is never passed (generation.py:1083-1094); multi-angle `extra_images` not collected (ReferenceSelector.tsx:362-369).
- **Batch runners** (generation.py:4166-6251): re-implement ref resolution — chars balanced to the full 5 budget THEN extras appended → can exceed 5 refs (4333-4337, 5477-5481; `/auto` does it correctly at 2113-2114). Two-pass base gets **double-enhanced** (batch pre-enhance with phase="base" at 4419/4642/5520, then dispatch re-enhances every Pass 1 at dispatcher.py:600-640). In narration mode the narration override **clobbers TWO_PASS_BASE** (override wins over phase, prompt_enhancer.py:999-1004).
- **Dispatch-time builders**: video-JSON lazy build context = `"Scene duration: Xs."` only (dispatcher.py:2678) vs the endpoint's full context; ideogram lazy caption is built from the **suffix-polluted** prompt (SFW/style/color tails ride into the caption source, dispatcher.py:1405-1437).

### 1.4 Dispatch mutations (post-LLM, applied in order)

SFW suffix → image-direction style tag → color-override suffix (+pass2-strong) → pass-2 preservation anchor → two-pass base re-enhance → ideogram caption swap → video-JSON swap (gated to i2v/fflf/v2v, constraints re-injected as `visual_style_mood.dispatch_constraints`) → transition trigger. All propagate through the v1.22.1 `_record()` channel; `submitted_*` now records exactly what was sent.

**Negative-prompt reality:** Klein/Z-Image/Krea2 have no negative node. Standard LTX i2v/fflf/v2v: negatives exist only as baked workflow-JSON defaults — `prepare_ltx_workflow` takes no negative param. **Only `ltx_seq_*` (Director) receives `global_video_negative_prompt`.** Yet VIDEO_SYSTEM_PROMPT tells the LLM "NEGATIVE PROMPT is handled separately by the system" (prompt_enhancer.py:258) — untrue for the most common path.

---

## Part 2 — Research digest (what the model makers actually say)

### FLUX.2 Klein (our edit/composite model)
- Structure: Subject → Action → Style → Context; **word order matters, front-load**. 30–80 words ideal; >100 words degrades edits. Edit prompts are **instructions, not scene re-descriptions** — exactly our 1.12.4 short-edit rework.
- Official prompts always pair the change with **explicit keep-clauses**: "Keep the pose, lighting, and overall composition of Image 1 unchanged", "keep image 1 colors". This is the sanctioned mitigation for multi-ref color/style bleed (it is NOT a workflow bug — matches our Pass-2 findings).
- Positional refs official ("the woman in image 2"); proper names never appear in official docs — our no-names rule is aligned.
- No negative phrasing in the positive prompt; no booster tokens anywhere in the official corpus; photorealism via camera/film-stock specifics.
- ⚠️ **Official Klein reference limit is 4 images** (BFL multi-ref guide; [pro] scales higher). Our budget is 5 (`MAX_TOTAL_REF_IMAGES=5`, klein_5ref workflow). Community: ref *compatibility* and *order* matter more than count; first ref carries most weight.
- Sources: docs.bfl.ml prompting_guide_flux2 + prompting_editing_multi_reference + character_consistency; fal.ai Klein guide; myaiforce multi-ref study.

### Z-Image Turbo (first-pass T2I)
- Official (Tongyi): "works best with **long and detailed** prompts" — but every token must be concrete visual detail; 512-token encoder cap, silent truncation. Their official enhancer (pe.py) is the only first-party meta-prompt in this model set: lock immutable core elements first; objective/concrete only; **no metaphor, no emotional rhetoric, no meta-tags (8K/masterpiece), no drawing instructions**; to-be-rendered text in **double quotes verbatim**; "output only the final prompt".
- Negative prompts are **ignored entirely** (no CFG). Booster stacking is the documented cause of blown-out exposure — confirms our 1.12.5 booster kill.
- Turbo distillation collapses seed diversity — vary the *prompt* for variation.

### Krea 2 / FLUX Krea (alternate first-pass)
- "Opinionated aesthetics": describe only what matters, let the model decide the rest; texture/material/film vocabulary is the main lever; named-style refs work; no tag spam. 30–120 words typical.
- Ideogram structured caption: exact schema is `high_level_description` / `style_description{aesthetics, lighting, photo|art_style, medium, color_palette[≤16 hex]}` / `compositional_deconstruction{background, elements[{type, bbox[ymin,xmin,ymax,xmax] on 0–1000, desc, color_palette[≤5]}]}`. Hard cap **2048 tokens**; **omit empty keys** rather than emitting them. Structured beats prose for multi-element layout, verbatim text, per-object hex; prose wins for single-subject mood shots.

### LTX-2.3 (video)
- Single flowing paragraph, present tense, chronological, "like a cinematographer, not a poet". 4–8 sentences, **<200 words**, and — the big one — **prompt length must scale with clip duration: one main action per 2–3 seconds**. LTX 2.3 rewards more detail than LTX-2 did.
- **Unspecified camera = random drift.** Every prompt should carry an explicit camera clause, even "static frame". Camera vocab: follows/tracks/pans/circles/tilts/pushes in/pulls back/handheld/OTS/static.
- Emotion via **physical cues, never labels**. Sequential phasing markers for 8–10s clips ("Initially… After a moment… As the zoom continues…"). Dialogue: quoted lines broken into short phrases with acting directions between beats. Audio described last (AV-native: ambient bed, voice character, volume, room tone).
- I2V official: "describe the transition from stillness to motion; avoid describing static elements already visible" — direct confirmation of our 1.17.1 opening-moment fix.
- Negative prompt: supported in ComfyUI (CFG); short targeted lists beat exhaustive ones ("morphing, distortion, warping, flicker, jitter, blur, artifacts, watermark, text, subtitles").
- **JSON prompts are NOT officially endorsed** — Gemma-3 connector parses them semantically so they "work", but official temporal tools are chronological prose phasing and **Prompt Relay** (global prompt + `|`-separated per-beat locals, 3–6 beats — exactly what LTXDirector targets). Recommendation below.
- Params: cfg 2–5 video (3 typical), stg 0.5–1.5 (raise for character drift), rescale ~0.7.

### Meta-prompting (the LLM that writes prompts)
- Persona → Task → Constraints → Format; craft-specific persona ("cinematographer writing a shot description").
- **Numeric constraints, not adjectives** ("30–80 words", "one action per 2–3s") — we do this.
- Explicit ban lists (boosters, negative phrasing, emotional labels, metaphor, instruction echo) — we do most of this.
- **Deterministic priority ordering with a resolution rule** ("drop the lowest-priority item; never merge contradictions") — we have the stack, we lack the "drop, don't merge" instruction.
- **Few-shot examples measurably beat descriptions** (practitioner consensus): 2–3 in/out pairs per system prompt, including one no-ref and one 2-ref example so graceful degradation is *demonstrated*.
- VLM ref descriptions: prompt-driven captioning keyed by slot number, treated as ground truth of slot contents — we already do this shape (vision.py + slot-keyed blocks). Extraction should be use-case-specific (identity refs: face structure/hair/skin/marks/clothing/palette).

---

## Part 3 — Gap analysis & recommendations

### P0 — correctness / cheap, do first

| # | Gap | Fix |
|---|---|---|
| P0-1 | **Klein 5-ref budget exceeds the documented 4-ref limit.** Slot 5 may condition weakly or not at all. | **DECIDED (v1.23.0): follow BFL — `MAX_TOTAL_REF_IMAGES=4`, dispatch clamps the LF FF-prepend overflow, klein_5ref kept for legacy jobs only.** |
| P0-2 | Manual ref descriptions **include character names** (ReferenceSelector.tsx:392) and `buildEnhanceContext` lists named characters (SceneEditor.tsx:2064) while every system prompt forbids names. | Strip names: "Image N shows: {description}" — mirror the auto path (generation.py:2711-2728). |
| P0-3 | Frontend still says "2-character reference limit" / "only the FIRST 2 sent" (SceneEditor.tsx:2029, 2069) vs backend 3. | Update strings to 3 + picker-based wording. |
| P0-4 | **Autogen 0-ref scenes get the Klein system prompt** but render on Z-Image/Krea2 (routing exists only in `/enhance-prompt`, generation.py:1012-1019). | In each autogen enhance call: if resolved ref count == 0 and not two-pass, pass `gen_model_name = single_image_generator` ("z_image"/"krea2") — same rule as manual. |
| P0-5 | **Two-pass base double-enhance**: batch pre-enhances phase="base", dispatch re-enhances every Pass 1 (dispatcher.py:600-640). Two LLM calls, compounding drift. | Skip the dispatch re-enhance when `two_pass_scene_prompt` already exists on params (batch produced it), or drop the batch pre-enhance and let dispatch own it. |
| P0-6 | **Narration override clobbers TWO_PASS_BASE** in batch (override wins over phase, prompt_enhancer.py:999-1004) — Pass 1 loses its no-refs/scene-only rules. | In `enhance()`: when `two_pass_phase` is set, ignore the override (or fold narration guidance in as prompt_guidance). |
| P0-7 | Ideogram lazy caption built from the **suffix-polluted** dispatch prompt (dispatcher.py:1405-1437) — "SFW, fully clothed…" leaks into the structured caption source. | Pass the pre-tail prompt (capture before tails are appended) to `_build_or_get_ideogram_caption`. |
| P0-8 | Video-JSON lazy build context = `"Scene duration: Xs."` (dispatcher.py:2678) vs endpoint's full video context — autogen JSON prompts are far weaker. | Call `_build_video_enhance_context` from the lazy build (import from generation or extract to a shared service module). |
| P0-9 | Scene-intent brief injected role-blind: manual **video** enhance gets a first-frame-role intent declared AUTHORITATIVE (generation.py:1073-1081); auto video gets none. | Gate injection on role match (or inject with a role caveat); add intent (role=video) to the auto video builder. |
| P0-10 | VIDEO_SYSTEM_PROMPT claims negatives are "handled separately" — false for i2v/fflf/v2v (no negative injection; workflow.py:263). | Either wire `global_video_negative_prompt` into `prepare_ltx_workflow` (the LTX graphs have CFG negative inputs) or correct the system-prompt sentence. Wiring it is the better fix — LTX officially supports targeted negatives. |

### P1 — parity consolidation (the structural win)

- **P1-1 Bring the video context builder to image parity** (generation.py:2872-3057): add the priority stack, strict-vs-grade palette (reuse `_palette_is_strict`), global project context, scene-intent (role "video"), and replace the named-uncapped character list with the position-based capped cast block. Add the research rules while there: explicit camera clause ALWAYS (default "static frame, locked-off shot" when user picked none), length↔duration coupling ("this clip is Xs — describe roughly one main action per 2–3 seconds, N actions total"), emotion-as-physical-cues.
- **P1-2 Kill the TypeScript context fork.** Manual Enhance should send only user *choices* (llm_instruction, camera, palette pick, ref selection, frame type) and let the backend build the context with the same builders autogen uses. This one change permanently ends the 2-vs-3 / names / image-canvas-for-video drift class. Interim cheap fix if deferred: manual video base should stop being wrapped in the image `buildEnhanceContext` and get video canvas + SHOT EXTENSION/CAMERA CONTINUITY/LIPSYNC parity.
- **P1-3 Batch ref budget order**: batch runners append extras after filling 5 char slots (can exceed 5, generation.py:4333/5477) — use `/auto`'s `_resolve_frame_ref_asset_ids` ordering (extras first, chars fill remainder) everywhere.
- **P1-4 Flow prompt regressions**: restore the per-scene character-count instruction ("pick the 1–2 (max 3) most important characters per scene by name") lost in the rewrite (concept.py:1109 says "up to 5… reference them by name" while `_select_scene_characters_from_flow` assumes 1-2); give the per-scene retry system prompt the diversity/continuity rules (concept.py:1334-1339).
- **P1-5 Manual path completeness**: pass `prompt_guidance` from `/enhance-prompt` (generation.py:1083-1094); collect `extra_images` multi-angle refs in `collectRefAssetIds`.
- **P1-6 Placeholder prompts**: don't send "Scene 12" as "Original prompt" to the LLM (generation.py:3324/3436/4405) — send empty ("generate from context") when `_should_enhance` classified it as blank.
- **P1-7 One palette module**: strict-vs-grade logic + wording exists in 5 places with drifting keyword lists (generation.py:2657, SceneEditor.tsx:2082, generation.py:2936, dispatcher.py:2574, COLOR_SUFFIXES). Extract one backend helper returning the block text; frontend stops computing it after P1-2.

### P2 — model-tuning from research (quality upside)

- **P2-1 Klein keep-clauses**: add explicit preserve-statements to the Pass-2 composite prompt ("Keep the pose, lighting, and overall composition of Image 1 unchanged; keep Image 1 colors") — official phrasing, stronger than our current anchor alone — and instruct the LF prompt to state what carries over from FF.
- **P2-2 Video-JSON mode → compile to prose or Prompt Relay.** JSON is not officially endorsed for LTX; official temporal control = chronological prose + Prompt Relay beats. Recommendation: keep the structured JSON as the *editing/authoring* format (it's a great UI), but compile it at dispatch into (a) a chronological prose paragraph with phase markers, or (b) Prompt Relay `global | beat | beat` when segments ≥ 2 — mapping `motion_timing_cues` onto beats. A/B test raw-JSON vs compiled-prose on 3 scenes before committing.
- **P2-3 Z-Image prompt upgrades** (adopt pe.py rules): verbatim double-quoted text handling; "lock immutable core elements first"; explicitly allow longer output (raise the 70-160w band toward ~200w of concrete detail; the encoder truncates at 512 tokens); keep the anti-metaphor/anti-booster bans.
- **P2-4 LTX audio prompting** (AV-native): add an audio-description block to the video context when `use_model_audio` (ambient bed, voice character, volume, room tone; dialogue in quotes with beat-split acting directions) — currently the prompt says nothing about audio and the model invents it.
- **P2-5 Few-shot examples**: add 2–3 input/output pairs to IMAGE, TWO_PASS_COMPOSITE, VIDEO, and Z_IMAGE system prompts (one no-ref + one 2-ref example for Klein). Measure on a fixed regression set (below).
- **P2-6 Priority stack**: append the resolution rule — "when two constraints cannot both be satisfied, DROP the lower-priority one entirely; never merge contradictions into one sentence."
- **P2-7 Character creator**: route the portrait prompt through the enhancer with the first-pass model's system prompt (it's tag-style text going to models whose prompts forbid tag piles).
- **P2-8 Ideogram normalizer**: enforce ≤2048-token cap and omit-empty-keys (verify `normalize_ideogram_caption` emits no empty `style_description`/`color_palette` keys); bbox grid note: community schema uses 0–1000 ints — ours clamps 0–1 floats; verify against what Ideogram4PromptBuilderKJ node expects (kijai's node may rescale).
- **P2-9 Vision captioning**: make `DESCRIBE_PROMPT` use-case-aware — identity refs get "face structure, hair, skin tone, distinguishing marks, clothing, palette"; style refs get "medium, lighting, grain, palette". Two prompt variants selected by asset role.
- **P2-10 Sequencer `image_description`**: truncate at a sentence boundary, not 200 raw chars (dispatcher.py:1944-1950).
- **P2-11 KREA2/Z_IMAGE system prompts open with the VIDEO-FIRST-FRAME sentence before the persona line** (prompt_enhancer.py:341, 390) — reorder (persona first; front-loading matters to the *writer* LLM too).

### Verification protocol (before/after any prompt change)
Keep a fixed regression set: ~6 scene briefs covering (1) 0-ref T2I, (2) 2-ref Klein scene, (3) two-pass composite w/ B&W palette, (4) narration still, (5) 6s i2v with camera move, (6) 10s FFLF with character entrance. After each system-prompt or context change, re-run enhance on all six and diff outputs (`Download Prompts JSON` already captures stored-vs-submitted). Judge against: word-count band, front-loading, no names, positional refs match attachments, camera clause present (video), no boosters/negatives-in-positive.

---

## Appendix — System prompt census (quick reference)

| Prompt | Target | Words | Core contract |
|---|---|---|---|
| IMAGE_SYSTEM_PROMPT | Klein/FLUX | ~450 | 30-90w, positional refs, never names, lighting first, lyrics primary |
| LAST_FRAME_IMAGE | all models' LF | ~700 | distinct later moment, cast-at-LF blocks, FF may be ref 1 |
| VIDEO_SYSTEM_PROMPT | LTX 2.3 | ~900 | segments (max 3, 40-80w each), present tense, camera vocab |
| TWO_PASS_BASE | Z-Image P1 | ~450 | scene only, no refs/names, anti-blowout, 60-140w |
| TWO_PASS_COMPOSITE | Klein P2 | ~350 | 20-60w edit instruction, Image 2+ identity-only, anti-darkening |
| KREA2_IMAGE | Krea 2 | ~400 | conversational prose, boosters degrade, 30-110w |
| Z_IMAGE | Z-Image | ~350 | literal follower, positive-only, 70-160w, 3-5 concepts |
| QWEN_EDIT | Qwen edit | ~200 | 1-3 imperative sentences, image roles |
| JSON_PROMPT | Ideogram/Krea2 | ~550 | full caption schema, bbox decomposition |
| SCENE_INTENT | planning | ~220 | structured brief JSON, role-aware |
| VIDEO_JSON | LTX | ~380 | 5-section JSON, timed beats, camera vocab |
| NARRATION_IMAGE / _VIDEO | Klein / LTX | ~550/~850 | script-driven, no on-screen text |

Selection: `two_pass_phase` > `is_video` > `frame_type=="last"` > image → per-model registry → narration/user override beats all (the P0-6 bug). `prompt_guidance` appended as user rules (autogen only — P1-5).
