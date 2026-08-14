# Klein 3.0 — pure Klein reference mode

**Started v1.208.0 (2026-08-04); CURRENT THROUGH v1.276.53 — this file documents the lane as
it stands today, newest sections first.** The active character-creation lane. No 3D anywhere. The whole idea:
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

**⚠ VIEW PROMPTS MUST NAME THE GARMENTS (v1.276.14).** `_view_prompt` said "SAME outfit as
the references" — and **"outfit" is a category word, which Klein ignores** (this repo's own
rule, measured 2026-08-04). Result: front in blue jeans with a brown belt, sides in black
trousers with none. Meanwhile `additional_details` already held the full outfit and the
prompt was reading only `hair` and `body`. `_character_garments()` now pulls the named
clothing (`clothing` / `outfit` / `wardrobe` / `additional_details`) into every view job.

**⚠ THE ANCHOR MUST NOT DISPLACE THE BODY (v1.276.14).** The face anchor is a
head-and-shoulders crop carrying nothing below the collar; with the 3-ref cap it could push
the full-body front out of the list. The front reference is pinned explicitly right behind
the anchor, and the job publishes `refs_used` so what a run received is answerable.

## ⭐ The DIRECTION reference (v1.276.19) — read this before touching side views

v1.276.17 stopped the RIGHT job being handed a LEFT profile by dropping the opposite view.
That removed the sabotage and left a **vacuum**: the list became `[face crop, front upload]`,
**two frontal images, neither of which says which way to turn.** Direction then came from the
prompt alone against a model prior, and the prior won ~3 times in 4. Lorenzo's "the head was
flipped the correct way but the body wasn't" is what partial compliance with a words-only
instruction looks like. It also explains why the 🪞 mirror retry helped so little —
**mirroring a frontal image is nearly a no-op.**

**The fix: the opposite profile is MIRRORED and put back as reference 3.** A mirrored LEFT
profile *is* a RIGHT-facing body. `_view_prompt(view, fields, angle_slot)` cites it BY SLOT
NUMBER, because Klein addresses references positionally. It rides as a POSE reference only —
identity still comes from images 1 and 2, so the character's real chirality is intact. This
is the mode's founding premise turned on its own base set: *the person from image 1 in the
pose from image 2.*

    BEFORE   right view  ~1 of 4        (his count)
    AFTER    right view   5 of 5        kps +2.21 +2.47 +2.76 +2.87 +3.88, single attempt
    control  left  view   1 of 1        kps -2.96, unharmed

**✅ CONFIRMED BY LORENZO on his own characters through the UI, 2026-08-10.**

⚠ **Identity: no clear signal at this n** — new right profiles 0.269–0.381 vs left 0.327 and
the pre-change right 0.381. The spread overlaps completely. Do not quote this as evidence
either way.

**Ordering (`deferred`):** a brand-new character has neither side yet, so the RIGHT view is
rendered in a SECOND pass, after the left exists to be mirrored. Left first because it is the
direction the model reaches for unprompted. Costs no extra render — it serialises one job.

✅ **VERIFIED END TO END on a fresh character (v1.276.39).** Until then this branch had never
executed, because every character in the library already had both sides. One front photograph
in, four views out, **all correct on the first attempt**: `deferred ["right"]`, and `right`'s
reference list was `[face_crop, front_upload, dir_<the left view's own id>]` with
`angle_ref right: 3` — the direction reference is genuinely the mirrored left, cited by slot
number. Make a throwaway to re-run this with
**`python scripts\k3_new_char_from_ref.py --from <slug> --name "X"`** (uploads only, never
generated views; `--delete` to remove it). ⚠ If the LEFT view fails all its retries, the
deferred RIGHT still renders — with no direction reference, i.e. blind. It degrades rather
than failing loudly; check `angle_ref` in the job status before trusting a right view whose
left sibling is missing.

**🗂 Supersede, don't stack (v1.276.19):** an accepted view demotes older GENERATED refs of
the same tag to `other` + `superseded`, so exactly one is live. Nothing is deleted and an
UPLOAD or CROP is never touched. (Testing produced ten `right` refs on one character before
this existed.)

## 🧭 Verify & retry (v1.276.18) — the base set checks its own work

**Why it exists:** the base set is upstream of datasets, LoRAs, sheets and every outfit, so
one wrong-facing base view is a bad ingredient in everything built afterwards. And after
v1.276.17 nothing varies between runs of the same view except the SEED — reference list and
prompt are both fixed — so a model prior toward left-facing profiles simply lands on the
requested side some fraction of the time. There is no further bug to fix; the loop is the
answer.

**The check is FREE** — `_facing_verdict()`, insightface on the CPU, no worker, no GPU. It
reads `kps_yaw` (nose offset from the eye midpoint, in half-eye-spans; NEGATIVE = nose toward
the LEFT edge), which needs no 3D model and so cannot fail the way `yaw` can:

    front   face detected, |kps| < 1.0 and |yaw| < 40
    left    |kps| >= 1.2 and kps NEGATIVE
    right   |kps| >= 1.2 and kps POSITIVE
    back    NO face detected -- that absence IS the verification

Validated 9/9 against known values before any render was spent. **An unmeasurable view is
reported OK and NOT retried** — spending a render on a maybe is worse than accepting it.

**🪞 The mirror route.** Retries alternate strategy rather than re-rolling the same one:
attempt 1 plain, 2 mirror, 3 plain, 4 mirror. The mirror route flips the REFERENCES, asks
for the OTHER side (the direction the model is good at), and flips the RESULT back — two
flips cancel, so the character keeps its real chirality (hair parting, scars, which hand
holds what). That is *not* the same as mirroring a finished left view, which swaps all of it.
Measured: plain ~1 in 4 (his count), mirror 3 of 4 (mine) — **small samples, so
`_MIRROR_FIRST` stays False** and both routes get used.

**⚠ If every attempt fails the view is left MISSING**, not filed under its tag with a
warning. Autogen can see and stop on a missing view; it cannot see a wrong one. Failures are
kept, tagged `other`, flagged `rejected`, and named with the reason.
⚠ `_refs_by_tag` filters out rejected refs — otherwise a view the verifier just caught comes
back as an identity reference through the `other` tag on the very next run.

**`POST /characters/{slug}/views/verify` `{demote}`** checks the views you ALREADY have, free.
`demote:true` sets the failures aside so the view reads as missing again. UI: 🧭 Verify
current views (free) · ⤵ set aside the bad ones. First live run on clonejoan: 8 checked,
2 failed (one of them from a run of Lorenzo's) → demoted → 6 checked, 0 failed.

Request knobs on `views/generate`: `verify` (default true), `max_tries` (default 3),
`mirror_retry`, `mirror_first`.

**⚠ REFERENCE LISTS ARE PER VIEW (v1.276.17).** Every view job used to get the SAME list. On
a character that already had a left view that list was `[face, front, LEFT]` — so the RIGHT
job was shown a left profile and rendered a left-facing pose.

> ⚠ **SUPERSEDED BY v1.276.19 (see the section above).** Dropping the opposite profile was
> only half the answer and it created a worse problem — a side job left holding two frontal
> references. The opposite profile is now **mirrored and put back** as the direction
> reference. The paragraph below is kept because the reasoning about a side reference
> helping a FRONT render is still true and still constrains the code.

`_view_ref_paths()` dropped the **opposite profile** and nothing else: a side reference genuinely helps a FRONT render
(measured v1.275.9, 0.3637 → 0.4498), so this must not become "strip all side refs". A
shorter list is fine — two refs beat three containing a contradiction. `refs_used` is
per-view; `refs_pool` shows the unfiltered pool.

**MEASURED** (`scripts/k3_face_audit.py`, yaw: negative = nose toward the LEFT edge):
left ref −73.4 · right BEFORE −72.6 (wrong) · right AFTER **+82.0** (correct).

⚠ **Read the yaw, do not eyeball a thumbnail.** On this exact fix I looked at a 340px
full-body side-by-side, concluded it had failed, and spent another render before running the
free audit that proved it had worked. Profile direction is not reliably readable at that
size.

**🙂 Face anchor (v1.275.2, and ⚠ CROP-FIRST since v1.275.7):** "Generate missing views" runs
two-phase — a face close-up (832×1024, head+shoulders) is obtained FIRST and then every view
render gets it as reference image 1. ⚠ **By default it is CROPPED out of the uploaded front
reference (`face_from_crop: true`), not generated** — a crop cannot drift, because it IS the
upload. `_face_crop_ref()` runs first and a Klein render only happens if the crop scores under
`_ANCHOR_MIN`. Saved as a ref tagged `face`.
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

**UI (v1.276.7):** the 👗 Outfits section runs full width under the three columns — name +
variant, the 4 core slots with the 9 detail slots behind `＋ more detail`, view chips, and
the wardrobe listed as outfit → variants → per-view thumbnails, each opening the zoom/pan
lightbox with its own ⬇ download. **✎ edit / new variant** loads a saved variant back into
the form so a new look does not mean retyping thirteen fields.

**🖼 AN OUTFIT CAN COME FROM A PHOTOGRAPH (v1.276.17).**
`POST /characters/{slug}/outfits/scan` — multipart `file`, optional `keep` ("just the hat").
The vision model names what it sees into the 13 slots as EDITABLE text, and the photo is
saved as a ref tagged **`garment`** (never an identity reference, never part of the core
set). Passing `garment_ref` to `POST /outfits` puts that photo in as **image 2** and
`_outfit_prompt` points at it BY SLOT NUMBER — Klein addresses references positionally, so
"the garment photo" would mean nothing where "image 2" does. Negative answers ("none",
"no hat") are stripped from the scan: at cfg=1 writing "no hat" puts a hat on. A scan MERGES
into the form rather than clobbering it, and `garment_ref` is stored on the outfit so ↻
regenerate reproduces the same jacket later.

**✎ THE PANEL IS AN EDITOR (v1.276.17).** `＋ New outfit` clears the form; clicking any
outfit row loads it (with a bar saying what is loaded and the row highlighted);
**💾 Save changes** → `POST /outfits/update` writes **metadata only — no render** (a typo fix
must not cost four renders), a rename MOVES the existing images onto the new name, and
renaming onto an existing (name, variant) **409s** instead of merging two wardrobes;
`＋ new variation` copies the base look's slots in and clears the variant name.

## ⭐⭐ DESCRIBE THE GARMENT, DO NOT NAME THE FRANCHISE (measured 2026-08-10)

**A character or franchise name inside a garment slot drags that character's whole costume in
with it, accessories included.** Lorenzo reported glasses appearing on a Supergirl costume
that were never in the source. They were not a bug in the reference list or the prompt
builder — they were the *word*:

    slots said "a blue SUPERGIRL leotard …"                   -> glasses, 5 of 5 renders
    same costume, SAME SEED (90210), described literally:
    "a blue long-sleeved leotard with a red and yellow
     diamond shield emblem"                                   -> NO glasses, clean, 1st try

⚠ **Correction does not fix it.** An affirmative counter-clause appended to the prompt: 3/3
glasses. The same clause moved to the leading position (the only emphasis lever at cfg=1):
3/3 again. The name is the cause; a correction is a symptom patch. Same family as the
category-word rule — Klein answers what a phrase EVOKES, not only what it lists.

The UI warns live under the slot fields when a slot contains a character name, and the
warning is appended to any flagged result that has EXTRA items alongside one.

## 👗 The Costume Library (v1.276.27) — `backend/api/costumes.py`, `/api/costumes`

**✍ `POST /draft`** — a sentence → the 13 named slots, using the **TEXT** model (no image
exists yet). Merges into the form. Its prompt forbids character/franchise names and says why,
carrying the v1.276.22 finding into the LLM itself.

⚠ **NO STAND — the mannequin is FULL-BODY (v1.276.28).** The first version used a dress form
"on a slim metal stand", and that stand is IN the image, so a character rendered from the
costume wore the pole. Telling the outfit prompt to omit it would be a negation (cfg=1 ⇒ it
paints one), so the mannequin stopped being the kind of object that HAS a stand: a full-body
figure with legs and feet standing on the floor has nowhere to put one — and gives the
footwear real feet to sit on. Measured at a fixed seed: pole visible → none, 2/2, and the
character render came back clean. ⚠ **Costumes designed before .28 still contain their pole —
regenerate them** (the seed and slots are stored on every library entry).

⚠ **v1.276.32 — NO UNFILTERED COSTUME FETCHES IN THE PANEL.** The design-job poller fetched
`GET /api/costumes` with no `stage` and assigned it to the LIBRARY list, so the library grid
was redrawn with every candidate every three seconds. The API was right the whole time
(`{candidates: 17, library: 1}`); the poll undid the split on a timer. If candidates ever
reappear in the library, grep the panel for a costume fetch without `stage=`.
**A correct API does not mean a correct screen** — I verified the backend, found it fine, and
closed the report a version too early.

**🔎 BROWSABLE (v1.276.35).** `GET /api/costumes` takes `wearer` (woman|man|unisex) and `q`;
the search matches name + prompt + every garment slot + model + wearer, and `by_wearer` counts
ride along for the filter chips. `POST /{cid}/rename` also accepts `wearer`, so a mis-set cut
is fixable without re-rendering. Each library card has an **ℹ info** panel: model, seed,
wearer, garment slots, the reference images it was built from, and the full prompt with a copy
button — the design's whole provenance, which is what makes it reusable.

**🧪 EVERYTHING LANDS AS A CANDIDATE (v1.276.30).** `GET ?stage=candidates|library|all`,
`POST /{cid}/approve`, `POST /candidates/clear`. **`adopt` 409s on an unapproved candidate** —
a character can only be dressed from the library. Four renders a click fills a library in an
hour, which is why the gate exists.

**📷 WITH A REFERENCE, THE GARMENT TEXT IS READ OFF THE IMAGE (v1.276.34).** If references are
attached and no custom prompt is typed, reference 1 is **vision-scanned** and the garment text
comes from what it actually contains — a typed description and a photograph can disagree, and
at cfg=1 the words win. **The counterpoint is narrow and worth keeping:** the prompt cannot go
away entirely because the reference cannot say "on a plain grey mannequin, cut for a woman" —
the photo supplies the GARMENT, the prompt supplies the STAGING. `scan_refs:false` opts out; a
typed prompt still wins. The scan is published as `scanned` and stored on the record's slots,
so a photo-built costume arrives in the library already described.
Verified with no prompt at all: a green bikini reference →
`{top: "a green halterneck bikini top", bottom: "a green high-waisted bikini bottom"}` → two
faithful Klein designs.

**🖼 REFERENCE IMAGES, FOR EDIT MODELS ONLY (v1.276.33).** `GET /models` returns each model's
`refs` capacity (klein 5 · qie 2 · everything else 0) and the UI shows the uploader only when
it is above zero — an upload box on a pure text-to-image model would be a lie.
`POST /refs` (multipart) → `{id, url}`; pass `refs: [id, …]` to `/design`. Capped to the
model's capacity, stored on the costume record, uploaded PER-WORKER (uploads are per-box), and
**cited by slot number in the prompt** because Klein/QIE address references positionally.
Verified: a character photo → 2 Klein designs on `.163`/`.224`, coat + straps + buckles
transferred onto a bare mannequin.

**👤 `wearer`: woman | man | unisex** — only the mannequin's PROPORTIONS change; it stays
blank-headed and matte grey so nothing identifying enters the reference.

**🧵 Fanned across workers with per-image status** — the job publishes `items`
(queued/running/done/error + which worker) and `workers`. **Krea 2 fans out too (v1.276.31)**:
`forge.py` USED TO render it on one host and this inherited that (⚡ forge fans out too since
v1.276.45 — the pin was habit, not hardware), but `/models/diffusion_models` on
ALL THREE workers lists `krea2_turbo_fp8.safetensors` — the single-box rule was a habit, not a
hardware limit. ⚠ Workers are assigned **round-robin UP FRONT**: asking the dispatcher inside
each thread meant every thread asked before any load registered and they all got the same box.
Measured: 3 images → .163 / .224 / .201 in parallel.

⚠ **The custom prompt describes the GARMENTS; the mannequin framing is still added.** It used
to be a FULL override, so typing in it silently discarded the mannequin — Lorenzo's bathing
suits came back as flat product shots because the stored prompt was literally
`"a 2 piece high waist string bikini set with no footwear"` and nothing else. `raw_prompt:true`
is the real escape hatch.

⚠⚠ **FAILED EXPERIMENT, DO NOT REPEAT: garments-first ordering.** A bikini rendered as a
floor-length dress, so I tried naming the garments before the staging. Result: **bare
mannequins wearing nothing**, twice, same seed — strictly worse. Mannequin-first restored.
⚠ **AND THE FOLLOW-ON CLAIM WAS RETRACTED IN v1.276.34.** I wrote here that "minimal swimwear
does not render on this mannequin with Krea 2 under either wording". **Too strong** — Lorenzo's
own run produced a clean green bikini on a mannequin with Krea 2 at the same settings.
Swimwear is LESS RELIABLE than a coat, not impossible; attach a reference image (which is then
vision-scanned) when it matters. Only the garments-first ordering result stands.

**🎨 `POST /design`** — renders candidate costume images. **Krea 2 default** (his call);
z_image / anima / klein selectable; custom prompt overrides the slot-built one.
⚠ **Krea 2 does NOT use the generic t2i path.** It has its own lane in `forge.py` because the
Krea 2 box has no decorator custom nodes and the unet name baked into `KREA2_TURBO_T2I.json`
is not what is installed — `_krea2_unet()` DISCOVERS it on the host. Sending the raw workflow
returns a flat `400` from `/prompt`, which is exactly what the first two renders here did.

**On a NEUTRAL MANNEQUIN.** A matte-grey dress form carries the garment and nothing else,
where a person carries a face, a body and a facing into whatever it later references. The
description is affirmative throughout (cfg=1: "no face" paints a face).

**📚 Shared library** at `<project>/_libraries/costumes/` — one design dresses a cast.
`POST /{cid}/adopt {slug}` **COPIES** it into the character as a `garment` ref (copied, not
linked, so deleting a design cannot orphan a rendered outfit) and rescans it into the slots.

⚠ **The mannequin gets scanned as clothing.** The first adopt returned four phantom garments
("gray long-sleeved shirt", "gray tights", "gray leggings", "gray pants") — all of them the
dress form. Telling the vision model it is a mannequin cut four → two; the rest is filtered in
code (`_looks_like_the_mannequin`), and ONLY for slots the costume never asked for, so a
genuinely grey garment survives. Nine-case check in the CHANGELOG.

## 🔗 Side-to-side garment continuity (v1.276.26)

A side view used to have **no garment evidence** — it invented the trims from words,
independently of the other side, which is why details drifted between left and right. It now
receives **the opposite side's finished render, MIRRORED**, cited by slot number.

Mirrored, because this lane has learned twice that a frontal render (v1.276.16) or a raw
opposite profile (v1.276.17) behind a side view drags the facing — while a mirrored opposite
profile faces the SAME way as the target (v1.276.19). RIGHT renders after LEFT so a sibling
exists to mirror.

**Measured, same outfit, same seed, A/B on `sibling_ref`:**

    independent sides           MISMATCH 5   (incl. brown shoes left vs red boots right)
    sibling garment reference   MISMATCH 1   (one spurious slot)
    facing                      preserved (left faces left, right faces right)
    identity                    0.2970 -> 0.3156 mean, n=2/arm — inside noise

⚠ **n=1 outfit pair.** `sibling_ref` is exposed on `POST /outfits` so this stays falsifiable.
⚠ A side view with a sibling carries **4 references**; v1.275.10 measured 4 as worse for BASE
views, so identity was checked here rather than assumed. It did not drop.

**Instrument: `scripts/k3_side_compare.py --char <slug> --outfit "<name>"`** — free, CPU, no
GPU. ⚠ Its FIRST design failed: asking the model to "list the differences" between two images
returned an empty list even where a shoe colour plainly differed. It now scans each side with
the proven single-image slot prompt and diffs the dicts in code. Still noisy — some of the
score is description verbosity, not garment difference.

## 🔄 BACK views: strip the front detailing (v1.276.24)

The back view kept rendering the costume **backwards** — a chest emblem printed across her
back, twice, on two runs. A clause saying the emblem "belongs on the front, turned away from
the camera" **did not work**. The cause was one layer down: the garment list still said
*"a blue leotard with a red and yellow shield emblem ON THE CHEST"*, and Klein renders what is
named, placing it where it can be seen.

`_back_garments()` removes front-only detailing for back views — clauses introduced by
"with / featuring / bearing / emblazoned", or a trailing comma-clause, mentioning an emblem,
logo, print, shield, crest, badge, zip, button, buckle, pocket, collar or neckline.

    "a blue long-sleeved leotard with a … shield emblem on the chest"
        -> "a blue long-sleeved leotard"                     (verified: back now clean)

**Third occurrence of the same rule in this lane: describe what is in view, do not name what
is not.** (The others: category words, and the franchise name.)

⚠ The verifier is **view-aware** for the same reason — `_outfit_expected(…, view)` judges a
back view against the stripped list and a close-up against `_FACE_VISIBLE_SLOTS` only.
Otherwise it reports the chest emblem "missing" from a photo of someone's back and spends a
retry proving it.

## 🙂 A close-up copies its reference's FRAMING (v1.276.24 — ⚠ NOW THE FALLBACK PATH)

> ⚠ **Since v1.276.29 the outfit close-up is CROPPED from the front render, not rendered** (see
> the section above). Everything below applies only when that crop is unavailable — no front
> render yet, or no face found in it — and `_headshot_of()` is still what keeps a full-body
> reference from turning a close-up into a bust shot.


The outfit close-up is handed the outfit's own FRONT render so it carries that costume's
collar and jewellery — but that render is a full body, and **Klein reproduces a reference's
framing as readily as its content**, so the close-up came out as a bust shot. `_headshot_of()`
crops it to head-and-shoulders first, reusing `_face_crop_box` so every close-up in the lane
frames the head identically.

## 🔍 Showing the sources (v1.276.24)

Each outfit view records `built_from` — the reference images that job actually received — and
the wardrobe renders them as a thumbnail strip under the result. Derived inputs (mirrored
direction refs, generated head crops) are excluded; they are not references anyone chose.

## 👁 Outfit verification (v1.276.22)

`POST /outfits` vision-checks each finished view against the garment list (`_outfit_verdict`,
same Ollama vision model as the scan) → `{missing, extra, wrong_colour}`, re-rendering on
failure (`max_tries`, default 2). **EXTRA is the one that matters**: an item nobody asked for
appears nowhere in the request, so no prompt review can find it — it has to be seen.
An unusable reply counts as OK (a render on a maybe is worse than keeping the image). Unlike a
base view, a failing outfit view is **kept and flagged**, not left missing — a wardrobe entry
with a wrong-coloured cape beats a hole and is not upstream of a LoRA.

⚠ **Every correction phrase is AFFIRMATIVE** (`_EXTRA_FIXES`, 24 of them, checked
programmatically for negation tokens): glasses → *"her bare eyes and eyebrows fully visible
and unobstructed"*. One of my own drafts failed that check — bags read *"nothing hanging from
her shoulder"*, which names the object it is trying to displace.

⚠ **REFERENCE URLS ARE VERSIONED (v1.276.25).** An upscale replaces the file IN PLACE under
the same id — right, because every job reading that slot should get the better image — but the
URL used not to change, so browsers served the stale copy and it looked like the upscale had
not happened. `_ref_url()` appends the file mtime as `?v=`; use it for every reference URL
(cards, outfit views, `built_from`, garment photo, downloads). The panel also reloads when a
`refup` job finishes.

📏 **Refs carry their real pixel size** (`size`, `orig_size`, `upscaled_engine`, `small` on
`_public_char`) and the card shows it, amber under `_REF_MIN_SIDE`.

**🔍 Small references get upscaled (v1.276.22, extended v1.276.25 to the ORDINARY upload
path — `POST /characters/{slug}/refs`, not just `outfits/scan`).** Every reference is scaled to ~1MP before it
reaches Klein, so a 400px web grab is scaled UP out of detail that was never in the file.
`outfits/scan` measures the upload and, when the short side is under `_REF_MIN_SIDE` (768 —
fires on web thumbnails, never on this app's own 832×1216), starts a background upscale with
`engine="auto"` (prefers SeedVR2: it restores rather than sharpens). Reported as `size` /
`upscaling`. Shared implementation: `_start_ref_upscale()`.

**⭐ v1.276.37 — the FRONT is UPSCALED BEFORE the crop is taken.** A head-and-shoulders box is
~15% of an 832×1216 frame, so cropping first hands the upscaler a ~180×220 source and asks it
to invent 16× the pixels; upscaling the front to 2048 first makes that box ~440×540 of REAL
detail. Measured at a fixed seed: **712×876 → 1192×1464**, with visibly separated hair strands
and clean strap edges. `_upscale_file()` is NON-DESTRUCTIVE (temp path, never over the outfit's
own front render) and falls back to the original on failure. Knob: `upscale_front_first`.

**⭐ THE FACE VIEW IS A CROP OF THE FRONT RENDER (v1.276.29)** — Lorenzo's idea, and it is
better than generating it. The close-up used to be its own Klein render asked to match the
front; that is a request, not a guarantee, and it lost (he got a face view whose torso
clothing differed from the front). Now: crop the outfit's own `front` render with
`_face_crop_box`, upscale it, save it as the `face` view. **It cannot disagree with the
costume because it IS the costume**, and it costs zero extra Klein renders. Falls back to the
render path when no front exists; `face_from_front: false` restores the old behaviour.
Verified 832×1216 → crop 178×219 → **712×876**.

⚠ **`_start_ref_upscale(..., blocking=True)` when calling from a BACKGROUND THREAD.** `_spawn`
uses `asyncio.create_task`, which needs a running event loop in the CURRENT thread; from a
worker thread it raises, the caller swallows it, and the status — already set to "running" —
hangs there while the workers sit idle. **A status set before the work is scheduled can lie.**
⚠ The upscale is part of the step, not fire-and-forget: the first version reported the outfit
done while the face reference was still a raw 182px crop.

**🙂 AN OUTFIT HAS FIVE VIEWS, AND THEY RENDER IN A SPECIFIC ORDER (v1.276.21).**
`_OUTFIT_VIEWS = front · back · left · right · face`. The close-up exists because earrings, a
necklace, glasses and a collar are decided at head height and are a handful of pixels in an
832×1216 full body. `_FACE_VISIBLE_SLOTS` limits its prompt to what a head-and-shoulders crop
can contain — naming boots in a portrait invites them into frame at cfg=1.

**The order is Lorenzo's and it matters:** ① FRONT → ② 🙂 FACE close-up taken FROM that front
(⚠ **CROPPED since v1.276.29**, not rendered — see the section above; it carries this outfit's
own collar/earrings/necklace either way) → ③ back/left/right, each
given THAT close-up as its face reference instead of the plain character crop. His reasoning:
the plain crop knows the face but nothing about this outfit; the outfit's close-up knows both.
`styled_face` switches the prompt to *"take the face AND the jewellery from image N"* — a
styled reference treated as identity-only gets its earrings re-invented at 40 pixels. Back
views get no face reference. **No extra render**: pass 3 still fans across every worker.

⚠ **"Regenerate all" means the whole SET, not the views that exist.** It used to send
`Object.keys(v.views)`, so a 3-of-4 outfit could never recover the 4th. And a view with no
base image to dress used to be dropped from `sources` SILENTLY — it is now reported in
`skipped` and the job ends `done_with_errors`.

**🙂 THE FACE IS PINNED AND CITED (v1.276.20).** An outfit render is an EDIT of a view, so
reference 1 is a GENERATED image already sitting ~0.33–0.41 against the upload — take the
face from it and you are copying a copy. The face crop WAS in the list, but only by tag order
and **never named in the prompt**, and Klein addresses references positionally. It is now
pinned to slot 2 for every view that has a face, the prompt says *"take the face from image
2, not from image 1"*, and a garment photo goes in BEHIND it (clothes travel in words far
better than a face does). `_outfit_ref_paths` returns `(paths, face_slot)`; the job publishes
`face_ref`. Measured: front 0.5766/0.5193 → **0.6574**, left 0.3845/0.3378 → **0.3984**
(⚠ n=1 per cell after the change — directionally right, not a settled number). Back views
get no face reference: there is no face in them.

**⚠ EVERY OUTFIT JOB IS PROMPTED AND REFERENCED PER VIEW (v1.276.16).** It used to build ONE
prompt for the whole set, saying "identical standing pose, **camera angle** and framing" — a
category word, so Klein ignored it, and **all four views came back frontal** (the "back view"
was a picture of her face). Two fixes, both measured on clonejoan:

1. `_outfit_prompt(..., view=…)` names the facing with the same `_VIEW_PROMPTS` vocabulary
   the base view set uses. Back became a real back view immediately.
2. `_outfit_ref_paths()` chooses the identity refs RELATIVE TO THE TARGET VIEW. Reference 1
   is always that view's own image; **`outfit`-tagged refs are never identity references**
   (they are this app's own earlier renders of the image being replaced — all frontal — i.e.
   the v1.275.9 drift loop again); the opposite profile is dropped; the front full-body is a
   fallback, not a preference; and a **back** view drops the face crop too, because no face
   is visible from behind so a face close-up is an instruction to turn around.

`refs_used` and `prompts` are published on the job, per view.

⚠ **A faithful outfit view exposes a bad BASE view.** After the fix, clonejoan's left base
view (a three-quarter turn) produced a three-quarter outfit view while her right base view
(a full profile) produced a full profile. That asymmetry is upstream in the ⭐ core set — the
old code hid it by making everything frontal.

**Managing them (v1.276.16):** `only_missing: true` on `POST /characters/{slug}/outfits`
renders ONLY the views that (name, variant) has no image for — a view counts as present only
if its FILE exists, so deleting a bad view is what makes it eligible, and the route **409s
rather than spending renders** when nothing is missing. In the UI every view of the set gets
a tile whether it exists or not: a missing one is a dashed **＋ render** placeholder, an
existing one carries **↻** (re-render just this view) and **🗑** (delete just this view), and
the variant row offers **＋ missing (n)** beside **↻ regenerate all**. One bad render is
therefore 🗑 → ＋ missing, with the good views untouched.

**Managing them (v1.276.9):** outfits use SLOT semantics — one image per
(name, variant, view) — so **re-running an outfit REPLACES those views in place**. That
makes ↻ regenerate the right button after changing base images, and stops a wardrobe
accumulating copies. `POST /outfits/delete` {name, variant?, view?} removes a whole outfit,
one variant, or one view, files included.

**⬆ Upscaling references (v1.276.9→.13):** `POST /characters/{slug}/refs/{rid}/upscale`
{engine, max_side, model_name?, seed?} — in place, on ONE reference.

- **⚠ THE UPSCALE MODEL MUST BE PHOTOREAL.** `STUDIO_UPSCALE.json` ships with
  `4x_APISR_GRL_GAN_generator.pth` baked in — **APISR is an ANIME model**. On a photoreal
  face it posterises skin and draws black line-art through hair. Default is now
  `4x-ClearRealityV1.pth` (`_GAN_MODEL_DEFAULT`). **Measured: the face-crop anchor scored
  0.8440 with the anime model and 0.9840 with the photoreal one** — the wrong upscaler cost
  0.14 of identity on the image everything downstream is seeded from.
- **engine:** `auto | seedvr2 | gan`. `auto` prefers SeedVR2 when a seedvr2-capable worker
  is online; an EXPLICIT seedvr2 request **fails loudly** if none is, rather than silently
  giving GAN output. SeedVR2 selects on the `seedvr2` capability (node class is
  `SeedVR2VideoUpscaler`, not "SeedVR2") and gets a 600s timeout vs the GAN's 300s.
- **max_side** (default 2048) is the amount: the GAN model is a fixed 4x with no scale
  input, so this is the long side the result is fitted back to. Unbounded meant 832×1216 →
  3328×4864 / 5.33 MB, and a reference is uploaded to a worker on EVERY render that reads it.
- **The original is preserved** as `<id>.orig.png` on first upscale, and every later upscale
  re-runs FROM IT — otherwise upscaling is not idempotent and engines cannot be compared.
  `POST /refs/{rid}/revert-upscale` restores it. Sidecars are never listed as refs and both
  delete paths remove them with the ref.

⚠ `_public_char` WHITELISTS ref fields; a new field on a ref record stays invisible to the
UI until it is named there too.

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
