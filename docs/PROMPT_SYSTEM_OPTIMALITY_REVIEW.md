# Prompt System — Optimality Review (research-grounded)

_Is the prompt system truly set up to do the app's job? Evaluated against current (2026) model best practices — v1.19.0._

**Bottom line:** the system is **architecturally sound and well-aligned** with how these models actually want to be prompted in 2026. The pipeline matches the current best-practice pattern for AI music/narration video (separate identity from motion, keyframe-first, lyrics-driven storyboard). The remaining items are **refinements, not structural flaws** — the biggest real lever is using more reference images per scene than we currently do.

---

## 1. The app's objective and how the pipeline serves it

**Objective:** turn an audio track + concept into a coherent, scene-by-scene music or narration video, locally, via ComfyUI.

**Pipeline:** `audio → concept → per-scene storyboard (flow) → first-frame image → (last-frame image) → image-to-video → export`.

Independent 2026 guidance for consistent AI music video converges on exactly this shape: *"separate identity creation from motion creation"* and a *keyframe → image-to-video* discipline (Neolemon/PixVerse/AI Magicx). Our FF/LF keyframes anchor the *look*; LTX animates the *motion*. **This is the right decomposition** — we are not fighting the tools.

---

## 2. Per-model optimality scorecard

Each model's system prompt vs what current guides recommend:

| Model | What 2026 guidance says | What we do | Verdict |
|---|---|---|---|
| **FLUX.2 Klein** (first frame + 2-pass edit) | Natural language; **most important element first**; **no negatives → positive phrasing**; multi-image: name each image's role ("subject from image 1"); 4–6 refs help consistency | Prose, front-load, "Image N" refs, edit-instruction Pass 2, face-preservation clause | ✅ Strong. ⚠️ We cap refs low (see §5); anti-text *negatives* still appended at dispatch |
| **Krea 2 Turbo** | Conversational prose; lighting + materials; **no tag piles / booster spam**; aesthetic coherence | Exactly this (dedicated Krea2 prompt + guide) | ✅ Aligned |
| **Z-Image Turbo** | **Short, precise** film-director cues; **3–5 core concepts**; no negatives (CFG~1); exposure control; ~512 tok | "30–110 words, front-load, 3–5 core concepts, no tag piles, exposure-aware" | ✅ Aligned (matches the guide almost line-for-line) |
| **Qwen-Image-Edit** | Natural language; **say what changes / what stays / boundaries**; label refs "Image 1/2" | Edit-mode prompt, refs by position, change-vs-keep | ✅ Aligned |
| **LTX 2.3** (video) | Describe **motion not static**; present tense; camera language; **keyframe-aware**; multi-segment Prompt Relay; long prompt for long clip | Motion-focused, keyframe-aware, segment-preserving cleaner, Prompt Relay | ✅ Aligned (hardened this session; segment preservation verified) |
| **Ideogram structured** (Krea2 JSON) | Global summary + style + spatial elements w/ boxes + palettes | Full structured caption builder + editor + ref-layout feedback | ✅ Aligned |

---

## 3. Context hierarchy (post-1.19.0) — is the LLM given the right inputs?

After this week's hardening the per-scene context now carries, in priority order: user direction → **explicit priority stack** → **target canvas/aspect** → role rule (first/last/still) → concept/style → **narration directive** (narration projects) → global context → image direction → **palette (strict vs grade)** → cast (in/out/enters) → lyrics (PRIMARY) → storyboard/flow → camera → prev-scene continuity → **vision ref descriptions** → **Ideogram ref layouts**.

That is a comprehensive, well-ordered brief. Against the research it covers every lever the models actually respond to (subject, lighting, composition, camera, palette, references, aspect). **Verdict: the context is now close to complete.**

---

## 4. Pipeline coherence — the storyboard is the linchpin

The single biggest quality driver is the **per-scene storyboard (`flow_idea`)** generated up front (`_generate_flow_inner`). Its system prompt already enforces the right things: *scenes must be visually **DISTINCT** (not the same place at different angles), the lyrics/narration are the **#1 source**, and each idea must specify location + camera + action + mood + composition.* This is exactly what the downstream image/video prompts need. **Verdict: strong.** If outputs feel generic, the flow step is where to look first.

---

## 5. The real gaps (prioritized) — where we are NOT yet optimal

### 5.1 We under-use reference images (highest-impact)
FLUX.2's effective range for character fidelity is **4–6 reference images**, and we ship `KLEIN_EDIT_ULTRA_WORKFLOW_1REF … 5REF` — so the **model and our workflows support up to 5 refs**. But the enhance **context describes only the first 2 characters** (`characters[:2]`), while some auto-gen paths actually attach **3** (`max_chars=3`). Two problems:
- **Inconsistency:** context can describe 2 while 3 refs are attached → the LLM under-describes a character that IS in the frame.
- **Under-utilization:** scenes with 3–4 key characters, or a single character that would benefit from multiple reference angles, are capped below what the model handles well.

> **Recommendation:** align the first-frame cast block to the *resolved* references (like the last-frame block already does), raise the cap toward the 5-ref workflow ceiling (configurable), and consider **multiple reference angles per character** for stronger identity lock (the #1 consistency technique in every 2026 guide).

### 5.2 Anti-text / SFW negatives appended at dispatch
FLUX.2 and Krea 2 explicitly prefer **positive phrasing over negatives**; the system prompts already say "no rendered text," so the comma-negative `, no text, no subtitles…` suffix appended at dispatch is **redundant and against-grain**. We made it transparent (the `dispatch_mutations` export) but did not remove it. *Recommendation:* drop the negative suffix for FLUX/Krea2 (the system prompt covers it) and keep only safety-critical tails, woven positively.

### 5.3 First-frame character block uses project order, not scene selection
The last-frame block now respects per-scene cast (who's in/out/enters); the **first-frame** block still describes `project.settings.characters[:2]` rather than the scene's selected/flow cast. *Recommendation:* unify on the same `_frame_present_char_indices` logic for both frames.

### 5.4 Manual vs auto parity (improved, not unified)
We mirrored the new directives into the frontend builder, but the two context builders are still separate code. Low risk now, but full backend centralization remains the durable fix.

### 5.5 Minor
- ✅ *Fixed in this pass:* the "FLUX has NO prompt upsampling" claim (FLUX.2 **does** have it; corrected to "this local workflow doesn't run it").
- No automated pre-dispatch validators yet (reference-without-image, palette contradiction) — would catch failures before GPU spend.

---

## 6. Verdict by front

| Front | Verdict |
|---|---|
| Fit to the app's objective | ✅ Strong — correct keyframe-first architecture |
| Per-model prompt quality | ✅ Strong — aligned with current guides across all 6 models |
| Context completeness | ✅ Strong (post-1.19.0) |
| Storyboard / scene diversity | ✅ Strong |
| Cross-scene consistency | 🟡 Good, but reference usage is conservative (§5.1) |
| Dispatch hygiene | 🟡 Negatives-at-tail against best practice (§5.2) |
| Manual/auto parity | 🟡 Mirrored, not unified (§5.4) |
| Safety net / validators | 🟠 Not yet present |

**Overall: the prompting system is genuinely good and fit for purpose.** It reflects how these specific models want to be driven and matches the production pattern proven for AI music video in 2026. The highest-value next step is **using more reference images per scene** (and aligning the first-frame cast to what's actually attached) — that targets the one thing audiences notice most: character consistency.

---

## Sources

- FLUX.2 prompting + multi-image (Black Forest Labs / fal / Apatero) — <https://docs.bfl.ml/guides/prompting_guide_flux2>, <https://fal.ai/learn/devs/flux-2-klein-prompt-guide>
- Z-Image Turbo prompting guides — <https://huggingface.co/Tongyi-MAI/Z-Image-Turbo/discussions/8>, <https://fliki.ai/blog/z-image-turbo-prompting-guide>
- Qwen-Image-Edit prompt guide — <https://huggingface.co/Qwen/Qwen-Image-Edit-2511/discussions/7>
- LTX-2.3 prompt guide + I2V — <https://ltx.io/model/model-blog/ltx-2-3-prompt-guide>, <https://docs.ltx.video/open-source-model/usage-guides/image-to-video>
- AI music-video character consistency / keyframe workflow — <https://www.neolemon.com/blog/how-to-create-consistent-characters-in-ai-videos-complete-guide/>, <https://pixverse.ai/en/blog/ai-video-generator-with-character-consistency>


---

## 7. Gap closure (v1.20.0)

The §5 gaps were resolved:

| Gap | Resolution |
|---|---|
| §5.1 Under-used references + context/refs mismatch | `MAX_SCENE_CHARACTER_REFS=3`; first-frame cast + refs now scene-aware (symmetric with last frame); cast described == refs attached; raised 2→3 |
| §5.2 Negatives at dispatch | Phantom anti-text suffix removed (was recorded, never sent); SFW rewritten to positive phrasing |
| §5.3 First-frame cast ignored scene selection | First-frame block now uses `_frame_present_char_indices` like the last-frame block |
| §5.4 Manual/auto drift | Palette + camera added to the frontend builder; global context injected server-side in the enhance endpoint |
| §5.5 No validators | Read-only pre-flight validators in the prompt export + a Prompt-tab warnings panel |
| Minor: "no prompt upsampling" wording | Corrected |

**Both former 'future work' items are now SHIPPED (v1.21.0):** multi-angle references per character (auto-balanced) and an opt-in structured Scene-Intent mode.
