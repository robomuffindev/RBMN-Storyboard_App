# Klein 3.0 — pure Klein reference mode

**v1.208.0 (2026-08-04). The active character-creation lane.** No 3D anywhere. The whole idea:
Klein 9B is excellent at "the person from image 1 in the pose from image 2" — so the mode is
nothing but well-managed reference images.

    image 1 = the character's base view MATCHING THE POSE'S DOMINANT ANGLE
              (front/back/left/right — falls back to the active base)
    image 2 = a POSE image (mannequin render, photo, openpose skeleton, depth map)
            + the pose IN WORDS (from its prompt, or described by the vision LLM)
    output  = the character in that pose

UI: Create area → **🎯 Klein 3.0** engine sub-tab (Klein page, after 🧪 / 🟣 / 🚀).
Backend: `backend/api/klein3.py` (`/api/klein3`); pose library lives in `backend/api/klein2.py`
(`/api/klein2/poses*` — shared store). Preflight: `GET /api/klein3/health` (worker table).

## Character workflow (left → middle columns)

**🙂 Face anchor (v1.275.2):** "Generate missing views" runs two-phase — a zoomed face
close-up (832×1024, head+shoulders, sharp on the eyes) renders FIRST from the identity
refs, saved as a ref tagged `face`, then every view render gets it as reference image 1.
An existing face ref is reused (no wasted render); the "🙂 Regenerate views
(face-anchored)" button (shown when the set is complete) forces a fresh anchor + full
re-run — the fix for a set whose faces drifted. Opt-outs: `face_first:false`,
`regen_face:true` on POST /views/generate.

## 👗 Outfit sets (v1.276.2/.3)

`GET|POST /api/klein3/characters/{slug}/outfits`

An outfit is a Klein edit of a VIEW reference — that view's own image as reference 1 with
the identity refs behind it — fanned across the workers and saved back as an `outfit`-tagged
ref. A SET is that same edit applied to every view, so a costume is consistent front, back
and both sides. **Each view is a standalone 832×1216 PNG**; the "outfit" is metadata
grouping them, never a merged file, so any single view can be used as a reference elsewhere.

**13 slots, all optional.** Core: `outerwear · top · bottom · shoes`. Detail: `headwear ·
eyewear · underlayer · belt · legwear · gloves · jewellery · accessories · carried`.

Two rules baked into `_outfit_prompt`, both consequences of Klein having no negative node at
cfg=1:

- **Empty slots are skipped entirely.** Emitting "no hat" would put a hat on the character.
- **Declaration order IS prompt order** — head to toe, then held items — because the slots
  are comma-joined into one sentence and it should read like a description. `carried` gets
  its own "and carrying …" clause, since a satchel is held rather than worn.

**Variants** (`variant: "jacket off"`) are a look WITHIN an outfit: outfit → variants →
per-view images. A scene where she takes the jacket off should not need a second wardrobe
entry. An empty variant is the base look and sorts first.

`?download=1` on the ref image route returns a meaningfully-named attachment —
`clonejoan_red-leather_jacket-off_front.png`, not `9c848c8e8c41.png`.

**As a dataset base (v1.276.3):** `GET|PUT /api/lora/datasets/{id}/base-outfit`. Opt-in and
unset by default — an existing dataset must not change what it renders because a new option
appeared. A chosen outfit outranks every other base tier (it is the only one named directly
by the user), and **views it has no image for fall through to the normal base chain**, so a
partial outfit degrades instead of failing rows. `/identity-preview` reports which image
each view will actually start from, before a render is spent.

### 📐 The anchor is gated, and here is what measuring it found (v1.275.4–.6)

Run **`python scripts\k3_face_audit.py --char <slug>`** before spending renders on identity
work. CPU-only, no GPU, no worker, free. It ArcFace-scores every ref of a character against
the uploaded front reference AND against the face anchor, with head yaw, keypoint yaw and
detector score alongside — so "no face found" (a back view) reads as itself rather than as
bad likeness. Bands: different <0.25, borderline <0.30, match ≥0.45.

Measured on clonejoan, four independent runs. Generated **front** views — a frontal image
against a frontal baseline, so no profile excuse (yaw ≈ +1°):

    vs the UPLOAD   0.3312   0.3592   0.3727   0.3900
    vs the ANCHOR   0.7587   0.7644   0.7773   0.8226

**Reference propagation is excellent and reproducible. Fidelity to the uploaded person is
not. The set converges — on the wrong face.** An unmeasured anchor is a drift amplifier:
it is copied into every downstream job, so its error becomes the floor for every view,
strip, pose and dataset row that character will ever produce. Three anchors scored 0.4660 /
0.3926 / 0.3499 against the upload.

> **⚠⚠ RETRACTED THE SAME DAY (v1.275.8/.9). Read this before acting on the paragraph
> above.** The conclusion drawn here — "fix the close-up and the views follow" — was WRONG.
> The anchor was then fixed completely by cropping it out of the upload
> (**0.4660 → 0.8440**, +81%) and **the views did not move**: mean 0.3633 → 0.3641 across
> four runs each. Meanwhile "vs anchor" COLLAPSED from 0.76–0.82 to 0.33–0.37, which
> reveals what the original number really was: **sibling similarity**, not propagation.
> Anchor and views were both Klein renditions of the same references, so of course they
> resembled each other; put a real photograph in slot 1 and the resemblance vanishes,
> because the views were never taking their face from slot 1.
>
> **The actual lever was the REFERENCE LIST** (v1.275.9). `_identity_ref_paths` took every
> `front`-tagged ref before any other tag, and view generation APPENDS a front ref every
> run — so Klein was being handed `[upload, generated front, generated front]`: zero angle
> information, and the app feeding its own output back in as identity evidence. A drift
> loop. Fixed to ONE REF PER TAG, uploads first, back last:
>
>     3 refs, slot 3 = duplicate front   n=8   mean 0.3637   (0.3312–0.3900)
>     3 refs, slot 3 = LEFT view         n=3   mean 0.4498   (0.4191–0.4749)
>
> No overlap between the groups. ⚠ `ref_count` is exposed (1–5) but **4 measured WORSE**
> (0.4498 → 0.3797, n=2): a profile reference drags a frontal render toward profile
> features. Default stays 3.
>
> Klein 3.0 identity now sits ~0.45 on a good frontal. It is still NOT a likeness
> guarantee — that is the LoRA lane (dorian 0.8118 vs a 0.1211 no-LoRA control). Treat
> Klein 3.0 output as staging and dataset material.

The fixes listed below are all still correct and still live; only the causal story was
wrong, and it is kept rather than deleted because a recorded wrong turn is worth more than
a deleted one.

Consequences now in the code:

- **Back views are no longer identity references.** `_identity_ref_paths` ranked by tag and
  capped at 3; a fresh character has exactly two face-bearing refs, so slot 3 went to
  `back` — the back of a head, with no face in it at all. Back rows sort LAST now. A 2-ref
  list beats a 3-ref list padded with a faceless one.
- **`_ANCHOR_MIN = 0.45`** (= `likeness.ARC_MATCH`, chosen over the borderline band on
  purpose). A fresh anchor below the bar buys ONE more render on a different seed and the
  better of the two wins; the loser is retagged `other`, never deleted, and only if this
  run produced it.
- **Selection is BEST-by-score, not NEWEST**, for both reuse and forced regen. Newest is
  not a quality signal — `existing[-1]` would have reused a 0.3926 anchor while a 0.4660
  one sat in the same char.json. `anchor_score` and `anchor_source` publish on the job
  status (`"reused (best of N)"` / `"generated (N render(s))"` /
  `"kept incumbent (beat N fresh render(s))"`).

1. **Create a character**, upload reference images, tag them (front / back / left / right /
   face / outfit / other). The **front** ref is the default base.
2. **🪄 Analyze references (LLM)** — sends up to 4 refs (front+face first) through the
   existing VNCCS vision wizard and fills the 11 description fields (editable, 💾 Save).
3. **🧭 Generate missing views** — Klein N-ref edits synthesize absent back/left/right views
   from your identity refs, in parallel across workers, auto-tagged into the set.
4. **👙 Strip → base set** — strips the newest ref of EACH tagged view (underwear or nude,
   sex-aware garments, explicitly barefoot) in parallel. Each view owns ONE slot: regenerating
   REPLACES it (🔁 per version), 🗑 deletes, click activates. Front auto-activates.
5. **⬆ Upscale** the active base (STUDIO_UPSCALE GAN graph) — result becomes active.

Storage: `<project_dir>/_libraries/klein3/chars/<slug>/` (char.json + refs/ + base/).
Generations: `_libraries/klein3/_gen/_gen_<gid>/` — every batch keeps the EXACT refs it used.

## Pose Library (🕺 button → modal)

**Sets are user-named containers** (own registry, exist empty, shared across all characters).
**Tags are pose metadata** — import files' `category`/`tags` columns become tags, never set
names. Imports always land in the set you have OPEN.

- 📥 **Import poses (.json/.csv)** — `[{name, prompt, category?, tags?, raw?}]` or CSV with
  those headers. Prompts get the gray-mannequin style wrapper unless `raw`. Dupe names
  skipped per set.
- 📦 **Import pack (.zip / openpose .json)** — zips of control images (openpose skeletons,
  depth maps, DWpose renders) and/or OpenPose keypoint JSONs (rendered to skeleton images
  server-side; COCO-18 + BODY_25, canvas/normalized coords). Exotic binary formats are NOT
  parsed.
- 📄 **LLM guide** — downloads `pose_set_llm_instructions.md` (mirrored at
  `docs/POSE_SET_LLM_GUIDE.md`): hand it to any LLM with "I want a set of X poses" and get a
  valid import file back.
- 🎨 **Generate missing** — renders every image-less prompt pose in the open set, fanned
  across ALL klein workers with live per-pose worker/status.
- Per pose: ✏️ view/edit the stored prompt, 🔁 save+regenerate, 🗑 delete. Tag chips filter;
  on 🌐 All, a tag selection can be USED directly ("▶ Use N tagged poses").
- ✏️ editor also owns the pose's **📦 set** (dropdown = move it) and its **🏷 tags** (chips
  with ✕, + field autocompleting from every known tag, max 8). "💾 Save set + tags" leaves the
  prompt and image alone, so uploaded/promptless poses can be organised too.
- 🗒 **Pose description** (v1.206) — the pose also travels as TEXT. A prompt-made pose
  already has one (the mannequin style wrapper is stripped back off); an image-only pose
  (pack / upload / openpose) gets one from the vision LLM via 🔍 **Describe missing (N)** —
  background pass, one thread per configured Ollama server, live `name @ server ⏳`, and it
  fills an empty dominant angle from the same look (`POST /api/klein2/poses/describe`
  {ids|category, overwrite, set_view}). Editable per pose in the ✏️ editor; "auto-describe
  imports" runs it right after a pack/upload import.
  **Why it matters:** the mannequin's build is not the character's, so copying image 2
  literally puts limbs in the wrong place — a heavy character's "hands on hips" came back
  with hands on the belly. The render prompt now states the pose in words and instructs
  Klein to land it on THIS body ("hands on the hips means his own hip bones at the sides of
  the waist, not his stomach"), keeping limb angles and facing from image 2. Toggle:
  "🗒 Send the pose description with the image" (ON); the generate box shows the exact text,
  or the count of poses that have one for a set run.
- 🧭 **Dominant angle** (v1.205) — every pose can record which side of the body the camera
  sees (front/back/left/right). At generation the identity image becomes the character's
  matching base view instead of whatever is active: **upscaled base of that view → any base
  version of that view → a ref tagged with that view → the active base**, and the run reports
  which one it used (job line, gallery, `klein3 generate … identity=…` in the log). This is
  the same mechanism that measured 0.744 → 0.901 on a −124° pose in the clay lane — a side
  pose driven from a front base is what costs likeness. Toggle: "🧭 Match identity to the
  pose's dominant angle" in the generate box (ON by default); the box predicts the exact
  identity source per pose BEFORE the run.
  Angles arrive from: the `view` column on import (synonyms + degrees accepted, front 0 /
  right +90 / left −90 / back 180, ties → front), filenames in a pack (`pose_back_03`), the
  ✏️ editor dropdown, or ☑ Select → 🧭 Set angle in bulk. Angle filter chips sit above the
  grid. Backend: `POST /api/klein2/poses/bulk-view`.
- ☑ **Select** (header) turns the grid into multi-select — image-less poses included — with a
  bulk bar: ➡ Move / ⧉ Copy into any set (copy duplicates record + image; name clashes get
  " (2)", nothing is silently dropped), ＋/− tag across the selection, 🗑 delete.
  Backend: `POST /api/klein2/poses/bulk-move | bulk-tags | bulk-delete`.

## Body drift controls (v1.207 / v1.208)

The Klein edit graph builds from an EMPTY latent — there is no denoise to hold structure, so
the only levers are the PROMPT and the REFERENCES.

**⚠ Two measured facts that shape every prompt in this mode.** The graph has NO negative-prompt
node (CFGGuider's negative is empty conditioning) and runs at **cfg = 1** — so a "do NOT make
him thinner" guard has nothing behind it and simply feeds *thinner* to the text encoder. Every
clause here is therefore AFFIRMATIVE. And "appearance / style" are category words: the
exclusion must NAME image 2's build, weight, height and limb thickness, exactly like garment
edits must name the garment.

- **🗒 Pose text is scrubbed** (v1.208): build words in the description ("a slim mannequin
  standing…") pulled the render toward that build. `_clean_pose_desc()` strips physique words
  (slim/athletic/muscular/thin-before-a-noun…) and swaps mannequin/dummy → person before the
  text is used; the stored description is untouched, and 🔎 Preview shows what is actually sent.
- **🧍 Body-matched pose mannequins** (v1.208, Lorenzo's idea — the structural fix): a Klein
  2-ref pre-pass (image 1 = mannequin, image 2 = his base) redraws the mannequin with HIS
  proportions while holding the pose, cached per character+pose under
  `chars/<slug>/posefit/<pose_id>.png` and reused by every later run. Then image 2 no longer
  carries a competing body at all. `POST /characters/{slug}/posefit` {pose_ids|category|tags,
  overwrite, match_angle} — fanned across all klein workers, live per-pose status, 40/run,
  already-fitted poses skipped. The panel shows library vs fitted side by side with 🔁 re-fit
  and 🗑. Generation: **🧍 Pose image = from library | body-matched**; a pose with no fitted
  mannequin silently falls back to the library image and the run records which was used. All four live in the generate box and all
are visible before a run via **🔎 Preview prompt** (`POST /api/klein3/preview-prompt`, zero
cost, same `_compose_prompt()` the generators call — the preview IS what runs):

- **🗒 Pose text: off | brief | full** — brief (default) states the pose in one line; full adds
  the limb-placement reconciliation paragraph. The long v1.206 paragraph pushed the identity
  clauses far from the end of the prompt and the body drifted, hence brief.
- **🧍 Lock body** (default ON) — TERMINAL clause, always last so it is the freshest: same
  weight, width at shoulders/chest/belly/waist/hips, limb thickness, height, head-to-body
  proportion; do NOT slim, heighten, thin, youthen, athleticize or idealize; only arms, legs,
  torso angle and head direction change. The user's extra prompt is placed BEFORE it.
- **📋 His build words** (default ON) — inserts his own `body`/`height` description fields
  ("Remember his physique: his build is …"). Named attributes hold better than "same as
  image 1".
- **👥 Identity boost** (default OFF) — adds a SECOND image of him (front base → face ref →
  front ref, never the one already used) as image 3; the graph auto-selects the 3REF workflow
  and the prompt says images 1 and 3 are the same person.

## Generation

Three selection modes (main screen card shows which): a **single pose**, a whole **📦 SET**
(1 image per rendered pose), or a **🏷 TAG selection** across all sets (ANY-match). Set/tag
runs create one gallery entry per pose, fan across every klein-capable worker (pinned-thread
queue), and stream per-pose `name @ worker ⏳` status. The baked prompt assigns roles
(identity from 1, pose ONLY from 2, photoreal) and auto-appends a pose-diagram note when
image 2 is an uploaded/promptless pose (skeletons, depth maps). Results land in the
**📚 Saved results** gallery — persistent, pose-linked, filterable, deletable.

## Status visibility (standing rule)

Every job (views/strip/upscale/set-run/batch-render) records and displays its worker; batches
thread across all workers; the workers bar (top of generate column) shows the fleet with
capabilities/health/load. If something crashes, it shows ✗ with the error, attributed.

## Open items / audit flags

SeedVR2 upscale lane (GAN only today) ·
generation size fixed 832×1216 in the UI · nude strip ungated · prompt-driven view synthesis
unproven vs the SAM3D turnaround recipe (port it if back views drift) · skeleton-pose
adherence vs mannequin poses unmeasured (designed fallback: SDXL-CN skeleton→mannequin).

## History

Klein 3.0 supersedes the pinned 🚀 Klein 2.0 3D-statue lane (`docs/KLEIN2_3D_POSTMORTEM.md`)
after the 16GB statue likeness ceiling; the classic 🧪 Klein 1.0 clay/depth lane is parked and
untouched. Full decision log: CHANGELOG v1.200.0 → v1.208.0.

## Base mode — dressed vs stripped (v1.217)

Stripping is a **choice**, not a stage. It costs an extra Klein edit per view and
introduces its own drift, so when a shot never needed the clothing replaced, the
uploaded reference is the better identity image.

| mode | what it picks |
|---|---|
| `auto` | pre-v1.217 behaviour — newest base version of that view wins |
| `dressed` | clothed sources only: ref copies, upscales of them, then tagged references |
| `stripped` | stripped versions and upscales of them, falling back to a reference |

**A character with no dressed base still works in `dressed` mode.** The
tagged-reference tier is inherently clothed, and generated missing views land
there too (`views_generate` writes them as refs with `source: "generated"`), so
uploads + generated angles are enough on their own — no strip run at all.

```
PUT /characters/{slug}/base-mode   {"mode": "dressed"}
  -> {"mode": "dressed",
      "resolves_to": {"front": {"found": true, "source": "front base (ref_copy)"},
                      "back":  {"found": true, "source": "back reference (generated)"}, ...}}
```

`resolves_to` is the point of the toggle: see the consequence for every view
**before** spending a render. `/generate`, `/generate-set` and each LoRA dataset
take a `base_mode` that overrides the character default for that run.

### Provenance

Every base version now records where it came from:

- `ref_copy` → `{kind, source_ref, view}` — **the `view` is new in v1.217.** It was
  never recorded, and `_base_for_view` filters on it, so a ref copy could never be
  matched to an angle. It was reachable only as the active base.
- `upscaled` → `{kind, view, from_kind, from_id}` — **`from_kind`/`from_id` are new.**
  Without them an upscale could not be told apart from an upscale of a strip, and
  the picker prefers upscaled first.

A version with genuinely unknown provenance (an upscale written before v1.217) is
**used, not dropped**, and labelled `provenance unknown`. Dropping it would repeat
the v1.205 bug where an empty preferred tier skipped everything behind it.

### Labels

Every pick reports its source, and the label is the record of what ran — never
infer it from the code path:

```
front base (ref_copy)                back reference (generated)
front base (stripped_underwear)      left reference · dressed fallback
front base (upscaled · provenance unknown)
```

`dressed fallback` means a `stripped` run found no stripped version for that view
and used the clothed reference — it says so rather than implying a strip happened.
