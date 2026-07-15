# Changelog

## [1.120.0] - 2026-07-15
### Added -- SAM3 article cleanup: remove leftover clothing/jewelry without losing likeness
- Strip release trades likeness for a cleaner strip (lower = the reference lets go
  sooner, so less of the person carries through). This breaks that tradeoff: keep
  Strip release HIGH for max likeness and let SAM3 remove the stubborn scraps.
- New pass on the base (after FaceDetailer, before background removal): SAM3
  (comfyui-easy-sam3, `easy sam3ImageSegmentation`) segments leftover articles by
  TEXT -> GrowMaskWithBlur -> Flux2 inpaint (SetLatentNoiseMask + SamplerCustomAdvanced,
  reusing the Klein model/clip/vae, positive = the underwear base prompt, negative =
  the strip negative) -> ImageCompositeMasked back onto the base, so ONLY the flagged
  regions change and likeness elsewhere is untouched. Gracefully skipped when the
  worker lacks the SAM3 nodes.
- Settings + base-preview controls: Article cleanup (SAM3) On/Off (klein_sam_cleanup),
  editable 'Articles to remove' text (klein_sam_cleanup_prompt; default jewelry +
  shirt/collar/sleeve/jacket, deliberately NOT bra/panties), Detection threshold
  Global/0.25-0.60 (klein_sam_cleanup_threshold). OFF by default.

## [1.119.0] - 2026-07-15
### Added -- Realism LoRA toggle for Klein generation (anime2real-semi)
- Optional realism LoRA stacked onto the Klein model chain to push outputs more
  photoreal, since Klein alone can only go so far. Resolved in resolve_klein_models
  (rides in the models dict) and stacked via _apply_realism_lora in BOTH the base
  (refbase) and pose graphs -- applies across Klein generation. OFF by default.
- Settings: klein_realism_lora ('on'/'off'), klein_realism_lora_strength (default
  1.0, clamped 0.0-1.5), klein_realism_lora_name (default anime2real-semi.safetensors).
  New base-preview controls: Realism LoRA On/Off + Realism strength (1.00 default,
  down to 0.40). Gracefully no-ops if the LoRA isn't on the worker.

## [1.118.0] - 2026-07-15
### Added -- FaceDetailer refine pass on the base + base-local face controls
- The refbase base preview now runs the SAME low-denoise FaceDetailer refine the
  pose runs use (ultralytics YOLO face detector -- NOT PuLID/InsightFace, which
  can't detect these reference faces: worker logs `AUCUN VISAGE`). Runs on the
  decoded image BEFORE background removal. Gracefully skipped when the worker lacks
  FaceDetailer / a face detector model.
- New base-local controls next to the base-preview settings (globals in the gear
  panel still apply as fallback): Face refine (base) On/Off (klein_base_face_refine),
  Refine denoise (base) with a Global option + 0.35-0.65 increments
  (klein_base_face_refine_denoise), Refine steps (base) Global/4/6/8/10
  (klein_base_face_refine_steps). Auto-save; apply on the next base preview.
- Note: PuLID's source was already switched to the face crop in 1.116, but it needs
  the APP backend restarted (not just ComfyUI) to take effect; even then it no-ops
  on undetectable-face refs -- FaceDetailer is the reliable base-face path.

## [1.117.0] - 2026-07-15
### Added -- "Strip release" control for the refbase base (tune leftover clothing/jewelry live)
- Exposed klein_refbase_ref_end (added in 1.116) as a segmented control in the Klein
  settings, next to Cleanup: Hold / 0.90 / 0.85 (default) / 0.80 / 0.75 / 0.70 / 0.65.
  Lower = the body reference lets go earlier so the final render steps strip leftover
  clothing/jewelry harder; Hold (1.0) keeps the reference the whole way (old behavior).
  Persists to studio settings (debounced auto-save) and takes effect on the next base
  preview -- no backend restart needed to retune, just re-run.

## [1.116.0] - 2026-07-15
### Changed -- Refbase base: PuLID now engages + late reference release for residual jewelry
- PuLID was fed the full-body reference, where the face is too small for InsightFace
  to detect (worker logged `face=0` = no-op, so PuLID contributed nothing). It now
  uses the dedicated face crop as its source, so it actually engages and tightens
  likeness. If faces stiffen, lower klein_pulid_strength (1.4 -> 1.0-1.2).
- Leftover on-skin accessories (wrist/neck jewelry) survived because the clothed
  reference is held for the full render. The body reference now RELEASES over the
  final steps (default end 0.85) so the tail of the render can wipe residual
  jewelry the strip prompt asks to remove -- body shape is already locked by then,
  so the body match is preserved. Tunable via klein_refbase_ref_end (0.5-1.0; 1.0 =
  old hold-full behavior, lower = strips harder). Applies to the refbase base preview.

## [1.115.0] - 2026-07-15
### Fixed -- Leftover clothing & jewelry on the reference-driven base
- The base build now matches the reference body well, but partial shirt pieces and
  jewelry survived the strip. Root cause: the strip "Cleanup" negative listed shoes
  and every jewelry type but NO garment words, so the negative had nothing to push
  against for the shirt/top, and the whole-person reference (clothes mask ON, which
  we keep because it locks the torso/chest/hip shape) kept dragging the garment back.
- Expanded KLEIN_STRIP_NEGATIVE with specific outer/leg garments and trim
  (shirt, t-shirt, blouse, sweater, jacket, dress, skirt, pants, shorts, collar,
  sleeves, buttons, zipper, necktie, scarf, ...), deliberately EXCLUDING bra/panties/
  underwear so the target white underwear from _base_body_state is never suppressed.
  Applies to the refbase base preview and every strip run (honored via the existing
  klein_strip_negative_text override).
- No graph change: for stubborn leftovers set the Cleanup control to Strong (cfg 1.5)
  -- the negative now bites on garments, not just jewelry/footwear.

## [1.114.0] - 2026-07-15
### Added -- Base body now comes from the reference photos (no mannequin)
- The mannequin used for base generation imposed its own generic body, so the
  character's build/chest/shoulders/height never matched the references. New
  "base from references, then pose it" path: when body/full-body references are
  present and the base isn't locked, the clone base preview generates the character
  directly from the photos with a prompt-described neutral pose and NO mannequin --
  body reproduced via ReferenceLatentPlus (full-person mask, strength ~1.15) + face
  crop + PuLID, empty init latent, RMBG background removal. That image becomes the
  identity/pose reference. Falls back to the mannequin path when there's no body
  reference or the base is locked.

## [1.113.0] - 2026-07-13

### Fixed — OOM on base pose runs: 1 pose per job, round-robin across workers
- Klein pose runs sent 4 poses per graph; stacked with the 1536 FaceDetailer +
  PuLID + RMBG2 + Klein-9B + 8B CLIP all resident, that OOMs some GPUs. Poses are
  now grouped into small per-job batches (default 1) and round-robin across every
  eligible worker, so each job stays light and models can free between jobs while
  the workers still run in parallel. Tunable via studio setting
  klein_poses_per_job (1-8); each pose also gets a unique seed now.

### Fixed — Body Helper height/build had no visible effect
- The strip base prompt hard-instructed "keep EXACT body/build/proportions/height
  from the reference," which overrode the Body Helper descriptors (and the bust
  references don't even show legs/height). Now: face, hair, skin and identity come
  from the reference as before, but when you provide build/height they are
  AUTHORITATIVE for the body/legs/overall height. A new klein_body_text feeds the
  body descriptors separately from face identity.

## [1.112.0] - 2026-07-13

### Changed — sharper faces: FaceDetailer now matches VNCCS's settings
- We already ran the same Impact-Pack FaceDetailer as VNCCS, but with weaker
  settings, which is why faces looked soft when zoomed. Now matched to VNCCS:
  face crop guide_size/max_size 768/1024 -> 1536/1536 (the face is regenerated
  at ~4x the pixels -> real detail without needing a huge source image), and
  denoise 0.40 -> 0.55 (rebuilds detail instead of only lightly touching up).
- Both stay tunable: klein_face_refine_guide (384-2048, default 1536),
  klein_face_refine_denoise (0.10-0.80, default 0.55). Dial denoise down toward
  0.4 if you ever see likeness drift; lower the guide size if it's too slow.
- Cost: the 1536 face pass is heavier, so pose runs take a bit longer per face
  (the base preview only refines the front view, so it's barely affected).

## [1.111.0] - 2026-07-13

### Added — Cleanup + Steps controls to fight flat-area interference
- New "Cleanup" control (Off / Gentle / Strong) next to the Klein controls sets
  the shoe/jewelry strip strength. Off = pure cfg 1.0 reference (cleanest flat
  areas, keeps shoes/jewelry); Gentle (new DEFAULT) = negative at cfg 1.2 (was
  1.5) -- removes most shoes/jewelry with much less cfg-induced grain; Strong =
  cfg 1.5. Persisted as klein_cleanup, sent per-run.
- New "Steps" control (4 / 6 / 8 / 10), DEFAULT 6 (up from the reference 4).
  More steps noticeably cleans up flat-area grain/interference at a small time
  cost. Persisted as klein_steps, sent per-run, clamped 2-16.
- Applies to the base preview and all Klein pose runs (creator + clone).
  klein_strip_negative is still honored as a legacy alias for klein_cleanup.

## [1.110.0] - 2026-07-13

### Changed — base preview is FRONT-only by default; 4-view set is now a toggle
- The clone base preview renders just the front view again by default (fast),
  which is the recommended way to dial in the base. The full 4-view set
  (front/right/left/back) is now opt-in via a "Base preview" toggle next to the
  Klein controls (Front only / 4-view set), persisted as klein_base_set and sent
  per-run. Versioning, the gallery, framing and lock-base all work the same for
  1 or 4 views (the front view is always the primary). Preview wait scales with
  the view count (600s single, 1200s set).

## [1.109.0] - 2026-07-13

### Fixed — 4-view base preview timing out (600s)
- The base preview became a 4-view set (v1.107.0) but each view ran its own
  PuLID + FaceDetailer pass, so the render was ~4x the old single-view cost and
  overran the 600s wait ("Klein job timed out after 600s").
- The base set now runs the FaceDetailer face-refine pass on the FRONT view only
  (the identity anchor used by lock-base) instead of all four -- most of the
  extra cost removed while the important face stays sharp
  (build_klein_pose_graph face_refine_first_only).
- The preview wait was also raised to 1200s to accommodate the heavier 4-view
  set. (Full pose runs are unaffected -- they still refine every pose.)

## [1.108.0] - 2026-07-13

### Changed — background removal now runs on the worker (VNCCS RMBG2), matching VNCCS
- Klein pose sprites are now cut out ON THE WORKER GPU using VNCCS's own RMBG2
  node (RMBG-2.0 / BiRefNet, Alpha output, refine_foreground on) -- the exact
  model VNCCS itself uses. This replaces the fragile app-side colour chroma key
  (which left frame-filling figures dark/semi-transparent) with a real ML matte,
  at full GPU speed and with zero dependency or hardware requirement on the app
  host. Enabled by default whenever the worker exposes the node.
- Ingest auto-detects an already-removed (transparent) sprite and SKIPS the
  app-side cutout entirely -- no flags to thread. The app-side chroma key / rembg
  path remains only as a fallback for a pool with no VNCCS-capable worker.
- Opt out or tune via studio settings: klein_rmbg ('off' to force the app-side
  path), klein_rmbg_model, klein_rmbg_res (256-2048, default 1024).

## [1.107.0] - 2026-07-13

### Added — the base is now a 4-view SET (front / left / right / back)
- Generating a clone base preview now renders FOUR views in the same neutral
  stance -- front, right-side, left-side and back -- in one Klein batch. They
  become the character's canonical reference set. Regenerating recreates the
  whole set; versioning and active-base now operate on the SET (the front view
  is the primary, so lock-base and older single-image consumers keep working).
- Auto-framing: every view is cropped to the figure with uniform padding and
  normalized onto one shared green canvas, killing the wasted empty space at the
  top and giving the set consistent framing (cutout.normalize_base_set).
- UI: the Base image area shows the selected view large with a labeled
  Front/Left/Right/Back thumbnail gallery underneath to toggle between them;
  version prev/next and Set-active work across sets.
- Backend: _klein_wait_all_images collects every batched image; save_base_preview
  stores a view set per version ([{view, asset_id, url}]); the /preview response
  carries the full set.

## [1.106.0] - 2026-07-13

### Added — Body Helper: feet/inches height + body-type & chest quick-picks
- Height can now be set in feet + inches (or cm) - all three stay in sync, with
  a ft/in (cm) readout. Backend already translates it to a stature descriptor.
- Two quick-pick dropdowns append Danbooru-style tags into the "body" field the
  models read well: Body type (petite/slim/athletic/curvy/chubby/muscular/...)
  and Chest/bust (flat/small/medium/large/huge + perky/natural/sagging). Useful
  when references don't show the full body.

### Changed — pose-sprite background removal prefers rembg (robust)
- Full-body pose sprites now try rembg (subject segmentation) FIRST, then the
  colour chroma key, then the crude fallback. A frame-filling figure contaminates
  the chroma key's border-ring background sample, which could leave the character
  semi-transparent/dark; rembg is background-independent and avoids that.
  Install on the app host to enable: pip install rembg --break-system-packages
  (falls back to the chroma key when rembg is absent).

## [1.105.0] - 2026-07-13

### Added — Lock-base-then-pose: consistent proportions across a whole pose set
- Klein clone pose runs can now use the APPROVED base render as the single
  body/identity reference for every pose, instead of re-deriving the body from
  the raw (head-heavy) bust references each time. This is the fix for
  proportions/likeness drifting between poses -- especially the oversized head
  -- and it gives the cross-pose consistency that LoRA datasets need.
- New "Pose consistency" toggle next to the Klein controls: "Lock to approved
  base" (default) or "Use references". Persisted as studio setting
  klein_lock_base; also sent per-run so the choice takes effect immediately.
- Behaviour: when lock-base is on and the character has an active base version
  (created by Generate Preview), every pose references that one base. If no base
  exists yet it transparently falls back to the references and logs a hint to
  generate a preview first. Reuses the existing active_base infrastructure
  (_klein_identity_bytes) that create-mode runs already used.

## [1.104.0] - 2026-07-13

### Added — Body Helper: height + build fields that feed the Klein base body
- The clone editor now has a Body Helper block: height in cm and inches (synced,
  with a ft/in readout) plus the existing body/build field. Height is stored on
  the character (useful as LoRA caption metadata) and, because a diffusion model
  can't render an exact number from a lone figure on green, it's translated into
  a stature descriptor the model CAN use -- petite / average / tall / very tall
  -- and injected into the STRIP base prompt alongside the build tags.
- Analyze Reference now reads ALL uploaded reference images together (not just
  the first) via the vision model and returns a combined body build + an
  estimated height descriptor, so "Analyze all references" fills both fields.
- Backend: `clone-analyze` accepts an `images[]` list (Ollama vision path);
  `klein_identity_text` appends the height descriptor; analyze schema gained a
  `height` field and richer build/proportions guidance.

## [1.103.0] - 2026-07-13

### Added — negative-prompt / cfg toggle to strip leaked shoes & jewelry (STRIP bases)
- Klein's reference workflow runs cfg=1, so the negative conditioning is ignored
  and shoes/earrings/bracelets that the reference photo or the model's own prior
  insist on cannot be removed by positive text (v1.101.0's emphatic "bare feet,
  no jewelry" prompt proved this -- they persisted anyway). STRIP base runs now
  set a real negative prompt (shoes/footwear/socks/jewelry/earrings/bracelet/...)
  and a modest cfg (default 1.5) so (positive - negative) actively pushes those
  items out. Applies to both the clone preview and full pose runs; never in
  KEEP/clone-outfit mode (where those items are wanted).
- Default ON for STRIP bases. Tunables via studio settings:
  `klein_strip_negative` ('off' to restore pure cfg=1 reference behaviour),
  `klein_strip_negative_text` (override the list), `klein_strip_cfg` (1.0-3.0).
- Logs `klein strip-negative ON: cfg=... neg='...'` so the run confirms it engaged.

### Note — PuLID insightface error on a worker
- The `[PuLID] No module named 'insightface'` crash is the portable-ComfyUI python
  split: `pip install insightface` in a normal shell targets system python, but
  `ComfyUI_windows_portable` uses `python_embeded`. PuLID already defaults OFF on
  disk (strictly opt-in), so restarting the backend stops the crash regardless.
  To actually use PuLID, install into the embedded env:
  `...\ComfyUI_windows_portable\python_embeded\python.exe -m pip install insightface onnxruntime`

## [1.102.0] - 2026-07-13

### Fixed — corrupt/dark pose sprites and un-keyed backgrounds
- Pose-sprite background removal now runs the real `chroma_key_cutout` (median
  border-ring sample + distance ramp + despill) instead of silently falling back
  to the crude corner-sampling cutout. The crude method mis-sampled the
  background whenever the figure reached the frame edges (0% keyed -> full
  background left, or holes punched in dark hair/shadow), which produced the
  "background left + graphical issues" sprites.
- Downloaded sprite PNGs are now verified to decode fully before use, with up to
  3 retries. A truncated `/view` download used to be written straight to disk as
  a corrupt, half-black image -- the source of the "incredibly dark, ribbed/torn"
  pose sprites. Truncated frames are now retried and, if still bad, skipped and
  logged instead of saved.
- `chroma_key_cutout` now returns a diagnostic note (sampled bg colour, % keyed
  transparent, subject luma) so `rbmn.py logs N cutout` proves the keyer ran.

## [1.101.0] - 2026-07-13

### Changed — force BARE FEET and NO JEWELRY on the base body
- Preview still leaked jewelry and shoe soles under the feet. The base-body prompt
  now emphatically removes all jewelry (earrings, necklaces, chains, bracelets,
  rings, anklets, watches, piercings) and demands completely BARE, barefoot feet
  with no shoes/sandals/socks/soles/platforms under them.
## [1.100.0] - 2026-07-13

### Changed — clone preview uses a dedicated NEUTRAL default pose (VNCCS-style)
- The clone "Generate Preview" no longer uses the first pose-library pose; it uses
  a dedicated neutral rest stance (empty bones = the mannequin's natural pose,
  front-facing, arms slightly out, full body). Like VNCCS's neutral base, this
  suits any reference rather than forcing a library pose that may not fit.
  (If it renders a T-pose, explicit A-pose arm rotations will replace the empties.)
## [1.99.0] - 2026-07-13

### Changed — strapless base bra, sharper face, proportion emphasis
- Base underwear bra is now a plain white STRAPLESS bandeau (no shoulder straps).
- Redress prompt now demands natural anatomically-correct proportions — the head
  sized proportionally to the body (not oversized), matching the reference build.
- Klein face-refine (FaceDetailer) renders the face at a larger guide size
  (512 → 768) for a clearer, less blurry face that better matches the body.
## [1.98.0] - 2026-07-13

### Fixed — strip mode invented a generic/anime body (withheld the reference); now keeps it (VNCCS method)
- Root cause of "a skin on a wrong-shaped head / doesn't use the body": strip mode
  WITHHELD the full-body reference, so Klein had no body to work from and invented a
  generic (often anime) one, then painted the face on. This mirrors nothing VNCCS does.
- VNCCS's remove_clothes KEEPS the full character image and runs an edit that changes
  ONLY the clothing (image1 = full character, latent_image_index=1, "Dress character:
  White underwear", "maintain consistency with the original"). Adopted that model:
  strip mode now KEEPS the full-body reference (body, proportions, face, identity all
  preserved) and the prompt REDRESSES — "keep the exact body/figure/identity, change
  ONLY the clothing to a plain white bra and panties." Face crop + PuLID ride along.
- With `real_face=False` on his references (InsightFace can't detect the face → PuLID
  face=0), identity now comes from Klein's own reference push (the kept full-body ref),
  which is what actually carries a realistic body + face shape.
## [1.97.0] - 2026-07-13

### Changed — base underwear now mirrors VNCCS's own proven phrasing (specific WHITE, sex-aware)
- Checked how VNCCS prompts its base models (character_creator_v2.py:1167):
  "(wear white bra and panties)" for female, "(bare chest, wear white boxers)"
  for male. The key is a SPECIFIC color (white) and concise phrasing — vague
  "plain underwear" let Klein drift to topless. `_base_body_state` now renders a
  plain WHITE bra + WHITE panties (female) / bare chest + WHITE boxers (male),
  driven by character_info.sex, still explicit that the chest is covered / SFW.
  NSFW = fully nude.
## [1.96.0] - 2026-07-13

### Fixed — lost all identity when PuLID returned face=0; and topless-when-SFW
- When PuLID is on but its InsightFace finds no face (face=0 — common when the
  reference face isn't cleanly photographic/detectable), we had DROPPED the
  face-crop reference, leaving no identity source at all → generic/anime output.
  The tight face crop is now ALWAYS kept as an identity reference latent (Klein
  copies its pixels even when InsightFace can't detect a face there); PuLID is
  purely additive on top when it does find a face.
- Strip mode was coming back topless in SFW: the base-body prompt now forcefully
  states the chest/breasts ARE covered by a plain bra (not topless, not nude —
  only shoulders/arms/midriff bare, SFW underwear).

### Note
- If the worker still logs `face=0`, PuLID genuinely can't read that reference's
  face (too small/stylized/angled) — identity then rides on the crop reference.
## [1.95.0] - 2026-07-13

### Fixed — PuLID got face=0 (no identity applied); now fed the FULL reference image
- Worker logs showed PuLID loading fine but `face=0` — its InsightFace found no
  face, so it applied no identity and the output came back generic/anime. Cause:
  we handed PuLID our app-side crop, which for a realistic character was the crude
  heuristic head crop that doesn't frame a detectable face. PuLID runs its OWN
  detection+alignment, so it now receives the FULL identity image (a real photo it
  can find the face in) instead of our crop. The app-side crop is still used for
  the reference latent when PuLID is off.

### Added — render-style hint from Character type
- Realistic characters now get a "photorealistic, real photograph, not an
  illustration" directive in the prompt (anime → anime style, 3D → 3D render),
  so a realistic clone doesn't come back as generic anime. Applied to Klein pose
  runs, the base "Generate Character" T2I, and the clone preview.
## [1.94.0] - 2026-07-13

### Changed — NSFW moved into the toggle group (SFW/NSFW button) by Character type / Base outfit
- The per-character NSFW control (info.nsfw / cloneInfo.nsfw — the same flag the
  wizard/analyze fills and that drives nude-vs-underwear base) is now an SFW/NSFW
  button row grouped with Character type and Base outfit on the Create/Clone tabs,
  instead of a lone checkbox. Shows in both Native and Klein modes; Character type
  and Base outfit remain Klein-only. NSFW still persists with the character (💾),
  and Character type / Base outfit auto-save to host settings as before.
## [1.93.0] - 2026-07-12

### Fixed — Klein editor showed "no host" even when a worker was available
- The top-of-page availability badge computed `online` from a has_capability check
  that could lag the worker pool at page load, so it read "no host" while
  generation resolved a worker fine. It now reflects whether a worker actually
  resolves (the same resolution generation uses) and refreshes every 15s so it
  can't go stale after workers connect. Text is now "worker online: <url>" /
  "no worker detected".
## [1.92.0] - 2026-07-12

### Changed — removed the "VNCCS host URL" field from the Klein editor settings
- The VNCCS host/worker is managed in the main app settings, not here. The
  duplicate host field in the ⚙ Settings panel caused issues when saved blank
  (especially now that settings auto-save), so it's removed. These settings saves
  now always pass host=null, which the backend treats as "leave the configured
  host unchanged" — so saving/auto-saving Klein settings never touches the worker
  pin. "Save host" button renamed "Save settings"; the panel header is now
  "Settings — models & generation".
## [1.91.0] - 2026-07-12

### Added — Character Studio (Klein) settings auto-save + Reset to defaults
- The ⚙ Settings in the VNCCS Native / Klein editor now PERSIST automatically: any
  change (host/edit model, generation params, PuLID, face refine, base outfit,
  character type) is saved to studio_vnccs_settings a moment after you change it
  (debounced) and restored on refresh — no need to remember "Save host", and the
  picks carry into the next generation set. "Save host" still works for an
  immediate save + reconnect.
- New "↺ Reset to defaults" button beside Save host restores all these settings to
  their base defaults.
- Removed the redundant per-toggle save added in 1.90.0 (the general auto-save
  covers Character type + Base outfit now).
## [1.90.0] - 2026-07-12

### Fixed — PuLID crashed the job ("No module named 'insightface'"); now strictly opt-in
- The worker's ComfyUI-PuLID-Flux2 node requires the `insightface` python package,
  which isn't installed — so forcing PuLID on (Realistic) errored the whole job.
  We can't detect that from /object_info, so PuLID is now OPT-IN: it engages ONLY
  when klein_pulid='on' is set in Settings (default Off). The Realistic type no
  longer forces it; stylized types still never use it. To enable PuLID: install
  insightface in the worker's ComfyUI python, then set PuLID = On.
- With PuLID off, realistic identity rides on the face-crop reference (real
  detection) — so a good face crop matters; see the face-detection follow-up.

### Added — toggle selections persist across visits
- Character type and Base outfit picks are now saved into host settings the moment
  you change them (klein_face_kind, klein_run_base_clothing) and restored when you
  reopen the page, so you can see what you had selected.
## [1.89.0] - 2026-07-12

### Added — Character type control (Auto / Realistic / Anime / 3D) + clearer option buttons
- The log showed the real problem for realistic characters: our photographic
  face detector STILL missed the face, so PuLID stayed off and identity fell to a
  crude heuristic head crop (face/hair way off). Fix: a "Character type" control
  by the Klein Create/Clone generate area. 'Realistic' forces PuLID on — its
  InsightFace finds the face on the worker even when our app-side detector misses,
  and carries the identity properly; 'Anime'/'3D' skip PuLID (InsightFace can't
  read stylized faces and would error); 'Auto' keeps the previous behavior.
  Sent as GenerateIn.face_kind (creator, cloner, previews).
- The base-outfit and character-type controls are now clear button rows
  (segmented toggles), not a dropdown — hard to miss.
- Face detection made a touch more sensitive (YuNet score 0.7→0.6; Haar
  minNeighbors 5→4, minSize 48→36) so 'Auto' catches more real faces.
- Log line now includes face_kind + real_face for diagnosis.
## [1.88.0] - 2026-07-12

### Fixed — THE strip root cause: anime faces defeat detection, so strip silently fell back
- Logs revealed strip mode was engaging but `strip_body_refs=False pulid=False`
  every time — because no face was detected. YuNet + Haar (and PuLID's
  InsightFace) are PHOTOGRAPHIC detectors and miss VNCCS's stylized/anime faces,
  so `crop_face` returned None → no face crop → PuLID off → strip fell back to
  feeding the clothed full-body reference (the dress leaking).
- New `_klein_identity_crop`: tries real detection, and when it misses falls back
  to a heuristic upper-center HEAD crop (excludes shoulders/straps). Strip mode
  now always gets an identity reference that carries the face/hair but NOT the
  outfit, so the clothed body reference is withheld and the dress can't leak.
  PuLID is gated to REAL detections only (InsightFace errors on anime crops).
  Applies to Klein pose runs and the clone preview.
## [1.87.1] - 2026-07-12

### Added — base-outfit log on the clone "Generate Preview" path too
- The v1.87.0 policy log only fired on pose runs; the clone Generate Preview had
  no log line, so `rbmn.py logs base-outfit` came back empty after a preview.
  Preview now logs `klein base-outfit (clone preview): mode=… strip_body_refs=…
  pulid=… face_ref=…` as well.
## [1.87.0] - 2026-07-12

### Fixed — strip mode still copied the top from the FACE CROP; now references nothing clothed
- With the full-body reference already withheld (1.86.0), the remaining leak was
  the face crop: it was expanded 60% around the face, so a strappy dress's straps
  and shoulders rode along and Klein copied them. Now, in strip mode:
  (a) the face crop is TIGHT (20% expand — face + hair, no shoulders); and
  (b) when PuLID is active it carries the face as an EMBEDDING (no pixel copy), so
  the face-crop reference latent is dropped entirely — the ONLY thing referenced
  is the pose skeleton, plus PuLID + the hair/skin/build text. Zero clothed pixels
  are referenced, so no garment can leak. Without PuLID it falls back to the tight
  face crop as the sole identity reference.
- Added a log line each run: `klein base-outfit: mode=… strip_body_refs=… pulid=…
  face_ref=…` so the active policy is visible in rbmn logs.
## [1.86.0] - 2026-07-12

### Fixed — strip mode STILL kept the reference's top (dress/straps) — now structural
- Prompt wording alone couldn't beat it: Klein anchors hard to the clothed
  full-body reference latent and copies its top. In strip mode the pose graph now
  WITHHOLDS the full-body (clothed) reference entirely — identity rides on the
  face crop + PuLID + a text descriptor of the character's hair/skin/build
  (klein_identity_text) instead. With no clothed image in the reference chain,
  there's nothing for Klein to copy the dress from, so the underwear/nude base
  actually renders. Applies to Klein pose runs and the clone preview; only when a
  face crop was detected (else it falls back to the old body-ref behavior). Keep
  mode is unchanged (still uses the full outfit references).
## [1.85.0] - 2026-07-12

### Fixed — strip-mode base kept the reference's TOP (only bottoms became underwear)
- In "strip to base body" mode Klein swapped the bottoms for briefs but clung to
  the reference's shirt/top. The instruction was too passive. It's now an ACTIVE
  undress command that names the upper body explicitly: remove the top / shirt /
  jacket / dress and replace it with a plain bra (bare shoulders + midriff),
  remove any bottoms for plain briefs; NSFW = fully nude (top and bottom removed).
  The lead-in also states the references are clothed but for identity only and
  must be undressed. Prompt-only; strongest for the Clone flow where the identity
  reference is a clothed full-body image.
## [1.84.1] - 2026-07-12

### Fixed — per-run "Base outfit" control now also on the Clone sub-tab
- v1.84.0 added the per-run base-outfit dropdown only to Create > New. The
  Create > Clone sub-tab (the "✨ Generate Preview" flow) had no control, so it
  was invisible when cloning from references. Added the same Klein-only dropdown
  by the Clone sub-tab's Background/Generate Preview area.
## [1.84.0] - 2026-07-12

### Added — per-run "Base outfit" control on the Klein Create/Clone tabs
- The base-clothing choice (strip to underwear/nude base vs keep the reference's
  clothing) is now a visible dropdown right by the Generate buttons in the Klein
  editor, not only in ⚙ Settings. Options: "Use Settings default", "Strip to a
  clean base body", "Keep / clone the reference's clothing". It rides on the pose
  run as GenerateIn.base_clothing and overrides the studio setting for that run;
  blank = use the ⚙ Settings default. Wired through creator and cloner Klein runs
  and the clone preview. Klein-mode only.
## [1.83.0] - 2026-07-12

### Added — on-screen "Klein base outfit" toggle (Settings panel)
- Surfaces the v1.82.0 base-clothing behavior in the UI. The VNCCS
  Native / Klein Hybrid Settings box gains a "Klein base outfit" control:
  "Strip to a clean base body — underwear, or nude when NSFW (recommended)" vs
  "Keep / clone the reference's clothing". Loads from and saves to
  studio_vnccs_settings.klein_base_clothing via "Save host". Frontend verified
  with the cloud tsc recipe (npm ci + tsc --noEmit, 0 errors).
## [1.82.0] - 2026-07-12

### Changed — Klein base poses are now a body-only BASE (underwear/nude), not a clothing clone
- Corrects the intent of the 1.80.0/1.81.0 clothing work. Like VNCCS Native
  bases, Klein base poses should capture the character's identity, face and body
  but DROP the reference's clothing, leaving a clean body the Clothes/Emotions
  modes can dress later. Klein pose runs and the "Generate Character" preview now
  render the character in plain neutral UNDERWEAR by default (fully nude when the
  NSFW flag is on), taking identity/face/hair/skin/body/marks from the references
  while explicitly IGNORING any clothing, footwear or accessories they show.

### Added — optional "keep the reference's clothing" mode (studio setting)
- Set studio_vnccs_settings `klein_base_clothing` to `keep` (default `strip`) to
  instead clone the outfit from the references — for when you have a full-body
  reference whose costume you want as the base. In keep mode the outfit is
  reproduced exactly and gaps are filled from the character's Analyze-Reference
  `additional_details` text, with a hard rule against INVENTING items (no shoes
  if there were no shoes). A visible on-screen toggle will follow; this ships the
  behavior and the setting key now. NSFW nudity applies only to the strip base.
## [1.81.0] - 2026-07-12

### Fixed — Klein sprite background removal (edge halos + shadow remnants)
- Cut-out Klein sprites kept a colored rim around the silhouette and left
  shadow-tinted background, despite rendering on a flat solid field. Cause: the
  ingest cutout led with rembg (a general subject-matting net that leaves a
  green/blue edge halo and can keep shadowed background). For a KNOWN
  solid-color render a real chroma key is both simpler and cleaner. New
  `chroma_key_cutout` (numpy): samples the background color as the median of the
  image border ring, makes pixels within an inner RGB distance fully
  transparent and ramps alpha to opaque by an outer distance (a soft band that
  yields a clean anti-aliased edge AND removes shadow-darkened background), then
  despills the dominant background channel on the keyed edge so no fringe
  survives. Klein ingest now uses it first, falling back to rembg/crude cutout
  only when numpy/PIL are unavailable.

### Changed — Klein pose prompt: don't ADD clothing; keep bare parts bare
- Costume still drifted because the prompt told Klein to reproduce the outfit
  but never forbade INVENTING items. The pose instruction now demands matching
  the reference's state of dress exactly: barefoot stays barefoot (add no
  footwear/socks), bare/unclothed parts stay bare, and Klein must not invent,
  add or remove any shoes, boots, socks, stockings, straps, jewelry, garment or
  accessory not clearly visible in the reference.

## [1.80.0] - 2026-07-12

### Fixed — parallel pose runs saved 3 rows of the SAME poses (ingest overwrite)
- A 12-pose run fanned out across 3 workers came back as three identical rows
  of 4 sprites. Root cause was on the INGEST side (distinct from the v1.78.0
  upload-name fix): every chunk's Klein graph saved with the same
  `filename_prefix`, and ComfyUI's SaveImage counter is per-worker-local, so
  three fresh workers each produced `klein_sprites_00001_.png … _00004_.png`.
  Ingest wrote every chunk's images to `assets/vnccs/{char}/{label}/{filename}`,
  so later chunks OVERWROTE earlier chunks' files on our disk — 12 catalog
  rows, but only the last chunk's 4 images. Ingest now namespaces every stored
  file by its `prompt_id` (unique per chunk/job), so cross-worker filename
  collisions can never overwrite. Covers Klein, native, clothes and emotions.

### Changed — Klein background prompt: flat, evenly lit, no shadows
- The cut-out sprites kept a rim of background where the character's cast
  shadow darkened the green (the chroma/rembg cutout can't match shadowed
  green to the sampled corner color). Klein pose and preview prompts now demand
  a "solid flat background, evenly and uniformly lit, no shadows, no cast
  shadow, no ground or floor plane", so the field stays uniform and the cutout
  comes back clean.

### Changed — stronger attire preservation in Klein pose prompts
- Footwear, leg straps and stockings drifted pose to pose because the only
  clothing signal was the full-body identity reference at ~1MP (tiny details)
  and CFG=1 makes the empty negative inert. The pose instruction now explicitly
  demands reproducing the ENTIRE outfit — every clothing item, footwear,
  stockings, straps and accessory — exactly, adding/removing nothing. When the
  character carries `additional_details` text (cloned characters do; wizard
  characters intentionally don't), it rides along as an explicit "preserve
  these character details exactly" anchor. Best-effort: if drift persists on
  wizard-made characters, per-reference strength (ReferenceLatentPlus) remains
  the heavier follow-up.

## [1.79.0] - 2026-07-12

### Added — Klein face-consistency settings in the ⚙ Settings panel
- The VNCCS Native / Klein Hybrid Settings box gains a "Klein face
  consistency" section: PuLID Auto/Off + strength (default 1.4), Face refine
  Auto/Off + denoise (default 0.40). Persisted into studio_vnccs_settings via
  "Save host" — the same keys the backend already honored.

### Fixed — Clone "✨ Generate Preview" now previews the CLONE, not Anima
- In Native mode the clone preview called the host's /vnccs/preview_generate,
  which renders the checkpoint (Anima) from the tag sheet — not your
  references. With references present it now runs the REAL CharacterCloner
  meganode limited to ONE pose (upscaler off) and returns the posed clone
  (preferring the sprite tap over face-crop/sheet taps). Klein mode already
  rendered through the multi-ref identity chain.

### Added — character mode identity: Native vs Klein everywhere
- Characters now carry manifest.vnccs.variant ('native'|'klein'), stamped at
  save (Create/Clone 💾), base-preview save, and ingest ('klein' engine wins
  once set — a Klein character never silently flips back).
- Character Studio main screen: the card badge reads "✨ VNCCS Native" or
  "🧪 VNCCS Klein", and clicking a character opens the editor OF ITS MODE
  (/studio/vnccs vs /studio/vnccs-klein). Deep-links and the in-page Library
  "Load into Create" redirect to the right variant too.

### Added — character thumbnails + choose-your-own
- Main-screen character cards now show a real thumbnail: the chosen hero
  image, else the ACTIVE base version, else the newest base version.
- Every image tile in the character Library grid gains a ★ button — "use as
  thumbnail" — POST /api/studio/vnccs/catalog/{id}/hero. A manually chosen
  thumbnail is locked (ingest won't auto-replace it). Catalog list responses
  include variant + hero_url; the VNCCS Library tab shows a mini-thumb and a
  Native/Klein chip per character.

## [1.78.0] - 2026-07-12

### Fixed — Klein parallel pose runs rendered the SAME poses on every worker
- Upload filenames were fixed per character (rbmn_klein_<name>_pose0.png …),
  so when multiple workers share one ComfyUI input folder (multi-GPU boxes,
  or one host reachable under several URLs) each chunk's pose captures
  OVERWROTE the previous chunk's before the queued jobs executed — every
  chunk then rendered the LAST chunk's poses ("3 rows of the same 4 poses").
  All Klein uploads (pose captures, identity, face crop, emotion masks) now
  carry a unique per-chunk token in the filename.

### Added — Klein face refine: light FaceDetailer pass on every pose sprite
- Faces were soft/blurry with off eyes when zoomed: the face occupies a tiny
  fraction of a ~1MP sprite. When Impact-Pack's FaceDetailer + a face yolo
  model are on the worker (auto-detected, VNCCS installs have them), every
  Klein pose sprite now gets a LOW-DENOISE face refine at up to 1024px guide
  size — denoise 0.40 keeps the likeness (the same technique VNCCS applies to
  its own emotions); only sharpness, eyes and small-face artifacts change.
  Runs before the optional GAN upscale. Inputs are FILTERED against the
  worker's actual FaceDetailer schema, so Impact version drift can't fail
  validation. Settings: klein_face_refine ('auto'|'off'),
  klein_face_refine_denoise (0.40, clamp 0.10-0.80), klein_face_refine_steps
  (6). Reported in /klein-status and as "+detail" in run labels.

### Added — Clone tab: ✨ Generate Preview
- The Clone subtab now has the same review-before-committing preview as
  Create/New: renders the character ONCE (first default pose) from the
  uploaded references — in Klein mode through the FULL identity chain
  (native multi-ref + face crop + PuLID + face refine) — and files it as a
  base VERSION (which then anchors identity for the real pose runs). The
  preview/base-version browser now shows on the Clone subtab as well.
  POST /preview accepts cloner_images for this.

### Verified — Klein multi-reference method (no change needed)
- Audited our multi-ref conditioning against the official ComfyUI Klein
  templates, BFL's FLUX.2 guidance and the vendored VNCCS Klein9b workflow:
  chained ReferenceLatent nodes (daisy-chain) IS the native/official method,
  and prompts already use positional "image N" indexing. The grid/stitch
  approach is a community alternative that loses per-image indexing — not
  adopted. Studio Klein graphs are built programmatically in
  vnccs_native/klein_poses.py; the main app's workflow templates
  (workflows/*.json — KLEIN, KLEIN_INPAINT, studio graphs) are NOT touched
  by any of the 1.77.x-1.78.0 changes.

## [1.77.2] - 2026-07-12

### Added — visible confirmation that the face-consistency machinery engaged
- **GET /api/studio/vnccs/klein-status** — browser-friendly readiness report:
  app version, per-worker Klein models + pose LoRA, whether PuLID-Flux2 will
  engage (weight file, strength, provider) or the exact reason it won't
  (disabled / pack missing / no weights), app-side face-detector state, and
  the effective klein_pulid* settings. NOTE: these are backend settings keys
  in studio_vnccs_settings — they have no Character-Studio UI fields; 'auto'
  defaults mean zero configuration is needed.
- **Run labels now carry the indicator**: Klein pose chunks show
  "N pose(s) · Klein · face-ref+PuLID" (or "· face-ref" without PuLID) and
  Klein emotion chunks show "N face(s) · Klein emotions · face-ref+PuLID"
  (or "· face-anchor") in the existing progress UI — if the suffix is absent,
  no face was detected and the run fell back to pre-1.77 behavior.
- /generate/{step} Klein responses include a face_consistency object
  (face_ref / pulid_file / pulid_strength).

## [1.77.1] - 2026-07-12

### Fixed — PuLID weight auto-pick prefers the newest version
- With multiple PuLID weight files installed (e.g. pulid_flux2_klein_v1 AND
  _v2), the auto-picker fell back to alphabetical order and chose v1. It now
  ranks by version suffix (v2 beats v1) after the klein/9b name preference.
  `klein_pulid_file` in studio settings still overrides.

## [1.77.0] - 2026-07-12

### Changed — Klein Hybrid: face-consistency wave (poses + emotions)
- **Why faces drifted:** Klein only ever saw the identity as a full-body image
  squeezed to 1MP (the face = a few dozen pixels of reference), and the
  emotion recipe regenerated the WHOLE sprite from an empty latent anchored to
  the (already-drifted) sprite itself, then pasted the face rectangle back.
- **Pose runs now carry a face-crop reference:** a close-up crop of the
  identity face (app-side YuNet/Haar detect) rides as the LAST reference
  latent, and the prompt binds it explicitly ("Image N is a close-up of the
  same character's face … must match exactly"). No face detected = previous
  behavior, logged.
- **Emotion runs are now CROP-AND-STITCH and anchored to the base version:**
  the worker crops an expanded face-context box (app-side computed, min 256px,
  ×8-aligned), samples ONLY that region at ~1MP (empty Flux2 latent at the
  scaled crop size + SetLatentNoiseMask), conditions on BOTH the masked crop
  context and a canonical identity face crop taken from the ACTIVE base
  version (fallback: first sprite with a detectable face, then the raw base
  image), and composites the region back into the sprite in-graph
  (GrowMaskWithBlur seam → ImageScale back → ImageCompositeMasked at the crop
  origin). Faces render at ~6-10x the old effective resolution and every
  expression is anchored to the same canonical face.
- Emotion prompts use the same-person binding language ("Image 2 is a close-up
  of this same character's face … change only the expression").

### Added — PuLID-Flux2 identity adapter support (auto-detected)
- When a worker has the ComfyUI-PuLID-Flux2 node pack (iFayens) + weights in
  models/pulid, Klein pose AND emotion graphs are automatically patched with
  Apply PuLID ✦ Flux.2 fed by the identity face crop — the only true identity
  adapter that exists for FLUX.2 (Klein 4B/9B are its best-supported models).
  InsightFace AntelopeV2 + EVA-CLIP load on the worker; weight file is picked
  by preferring "klein"/"9b"-named files.
- Studio settings keys (all optional, no UI change needed):
  `klein_pulid` ('auto' default | 'off'), `klein_pulid_file`,
  `klein_pulid_strength` (default 1.4, the pack's recommended value, clamped
  0-2), `klein_pulid_provider` ('CPU' default | 'CUDA' | 'ROCM').
- Workers without the pack are untouched — runs behave exactly as before.

## [1.76.0] - 2026-07-11

### Changed — VNCCS Native/Klein: regenerating poses REPLACES the old images
- Every generated image is now tagged with ITS pose name (chunks carry their
  exact pose-name subset from the backend's own split, so the mapping is
  reliable across parallel fan-out, both engines, and all taps —
  finals AND intermediates).
- At ingest, older images of the SAME pose — matched on label + pose name +
  costume + base version — are deleted and swapped for the new ones. Rerun a
  single pose, or the whole set with upscaling turned on, and the library
  keeps ONE image per pose per context instead of accumulating
  near-duplicates across base/clothing runs. Klein emotion runs replace per
  (emotion × sprite) the same way.
- Version-safe: poses linked to a DIFFERENT base version are never touched
  (the per-version pose history stays intact), and images from before this
  release carry no pose tag so they're left alone (prune those with ✕ once).
- Qwen emotion runs still accumulate (the meganode's output order isn't
  pose-mappable); use ✕ or rerun-and-prune there for now.

## [1.75.1] - 2026-07-11

### Fixed — Klein Hybrid: ImageScaleToTotalPixels validation failure
- Newer ComfyUI requires a `resolution_steps` input on ImageScaleToTotalPixels;
  the Klein graphs omitted it and every chunk failed prompt validation
  ("required_input_missing: resolution_steps"). All scale nodes now send
  resolution_steps=1 — the exact widget value in the vendored Klein9b
  reference workflow.

## [1.75.0] - 2026-07-11

### Added — Klein Hybrid plan items 2-5 (pre-test wave)
- **(2) Upscale + BG-removal for Klein sprites:** the pose graph honors the
  upscaler control — any non-Off mode adds a GAN tail (ImageUpscaleWithModel,
  model resolved per worker, settings key klein_upscale_model; SeedVR maps to
  GAN here) scaled to the chosen resolution. Backgrounds are removed APP-SIDE
  at ingest (rembg, chroma-distance fallback) via the new
  IngestIn.postprocess='chroma' flag — Klein sprites land BG-removed like Qwen
  finals.
- **(3) Klein base preview:** in Klein mode, ✨ Generate Character runs a plain
  Klein 9B T2I built from the tag sheet (832×1216, 4-step Flux2, cfg 1,
  ConditioningZeroOut negative) instead of the host's checkpoint preview —
  files as a base VERSION exactly like before. No pose LoRA needed for this.
- **(4) Clone identity via native multi-ref:** Klein clone runs feed up to 4
  raw reference images directly as reference latents (pose = image 1,
  identity = images 2-5) instead of Qwen's source-grid collage.
- **(5) Klein face-inpaint emotions:** in Klein mode, Generate Emotions runs
  the KLEIN_INPAINT recipe per (cataloged sprite × emotion): app-side face
  detection (YuNet/Haar + anime fallback) builds a feathered mask-in-alpha
  RGBA, the worker inpaints only the face (GrowMaskWithBlur → SetLatentNoise-
  Mask → dual ReferenceLatent → ImageCompositeMasked), pairs fan out across
  workers, emotion prompts pull the host catalog's natural prompts. Clothing
  sets don't apply in Klein mode (UI adjusts); sprites with no detectable face
  are skipped and reported.

## [1.74.0] - 2026-07-11

### Added — Klein Hybrid: pose generation runs on Klein 9B (official VNCCS support)
- In Klein mode, "Generate Poses" (Create AND Clone) now submits our
  flattening of the official vnccs-utils reference workflow
  ("VNCCS_Utils Pose Studio Klein9b.json"): Klein 9B fp8 + flux2 CLIP/VAE,
  the **VNCCS_PoseStudioKlein9b_V1 LoRA** (VNCCS's own Klein pose LoRA, repo
  MIUProject/VNCCS_PoseStudio_Klein), dual reference latents (pose capture =
  ref 1, identity = ref 2, on positive AND negative), Flux2Scheduler 4 steps,
  euler, cfg 1. New backend module
  backend/services/character_studio/vnccs_native/klein_poses.py; template at
  workflows/vnccs/KLEIN_POSES_TEMPLATE.json.
- Pose captures render app-side (the same three.js-parity renderer) and upload
  to each worker with the identity image; identity = ACTIVE base version →
  newest cataloged sprite → first clone reference. Model/LoRA names resolve
  per worker by basename (overridable via studio settings klein_unet/
  klein_clip/klein_vae/klein_pose_lora); a missing LoRA errors with the exact
  Model Manager repo to download.
- Multi-worker fan-out splits poses across workers like Qwen runs; results
  ingest as "Base poses (Klein)" with full run-recipe/seed tracking. Klein
  runs are NOT recorded as VNCCS sprite-shard hosts (they don't populate the
  worker-side character store — Qwen clothes/emotions can't chain off them
  yet, noted in the UI).
- GenerateIn.engine ('klein') on /generate and /generate-parallel; Native mode
  is untouched (engine defaults to the Qwen meganode path).

### Added — docs/KLEIN_MODE_PLAN.md
- Full Qwen→Klein swap map: where VNCCS uses Qwen, which stages depend on
  Qwen-only LoRAs (ClothesCore, EmotionCore — no Klein twins), where the swap
  is easy (base preview, clone multi-ref, upscale/BG helpers), and a suggested
  order of attack.

## [1.73.0] - 2026-07-11

### Changed — VNCCS Native: Pose Studio tab replaced by a Pose Library tab
- The 3D Pose Studio tab (non-functional) is removed from the character
  creation screens; pose creation will integrate into the Pose Library
  instead. (The module stays in the repo, just unrouted.)
- New **Pose Library** top-level tab — the same VNCCS host library the ➕
  modal uses, promoted to a full page and auto-loaded on open: Hugging Face
  pose-pack repositories (enable/disable, ⬇ download, add your own repo id),
  pose browser with preview thumbnails, and "Add to pose set" straight into
  the generation selection. The ➕ Pose Library modal inside the pose section
  renders the identical panel, so both stay in sync.
- Applies to Native AND Klein Hybrid (shared component).

## [1.72.1] - 2026-07-10

### Fixed — VNCCS Native: clone results no longer show every pose twice
- A cloner run taps TWO final sprite sets — `original_sprites` and
  `naked_sprites` (the undressed base the clothes step dresses) — and both
  passed the "finals" filter, so a 16-pose clone displayed 32 images in the
  live results and in the reopened character's pose grid. The default view now
  shows the Original set only; the undressed set (and other intermediates)
  lives behind the "show all pipeline outputs / intermediates" toggles.

### Changed — Character Studio: header "✨ VNCCS Native" button removed
- The New Character mode dialog covers mode entry now; existing characters
  still open their own editor from their cards.

### Note — all creation modes share these tweaks
- VNCCS Klein Hybrid renders the same component as Native (variant prop), so
  every fix and feature in this line applies to both automatically until the
  Klein-specific steps start diverging.

## [1.72.0] - 2026-07-10

### Added — VNCCS Native: pose runs linked to their recipe (like emotion runs)
- Every pose run (creator / cloner / clothes) now records its full recipe on
  the character — pose names, the complete pose set data, costume, and seed
  (parallel chunks carry the full run's recipe; deduped to one record per run,
  last 12 kept).
- **✓ done badges:** pose tiles (defaults AND library extras) show a green ✓
  when that pose has already been generated in the current context — base
  poses on Create, per-costume on Clothes.
- **Previous pose runs** list in the pose section with **↻ Load**: restores
  that run's exact pose selection (defaults re-selected by name; library poses
  reattached from the stored pose data), the fixed seed, and — on the Clothes
  tab — the costume. Hit Generate to redo the run.

## [1.71.0] - 2026-07-10

### Fixed — VNCCS Native: cloned characters show their generated pose set
- The "Base poses" library grid was gated to the New sub-tab, so reopening a
  CLONED character (which lands on the Clone sub-tab) hid its generated poses.
  The grid now shows on both sub-modes, same controls (lightbox, ✕ delete,
  intermediates toggle).

### Added — VNCCS Native: Emotions tab remembers what you've made
- Selecting a character on the Emotions tab loads its generated emotion sets:
  a "Generated emotions" grid in the results panel (grouped per clothing set,
  lightbox + ✕ delete), refreshed on tab open and after runs.
- **Runs are linked to their recipe:** every emotions ingest records
  emotions × clothing sets × seed (+ host/prompt_id/time) on the character and
  stamps each image's asset meta. A "Previous emotion runs" list shows the
  history with a **↻ Load** button that restores that run's emotion selection,
  clothing sets AND fixed seed — hit Generate to redo it exactly.
- **Done-tracking in the picker:** emotions that were already generated get a
  ✓ badge on their tile and come PRE-SELECTED when the character loads.
- All ingests (every step) now stamp the run seed into asset meta too.

## [1.70.3] - 2026-07-10

### Fixed — VNCCS Native: fresh clothes show up on the Emotions tab without a refresh
- The Emotions tab's clothing-set list was fetched once per character from the
  PINNED host only — after generating clothes (which run on the recorded shard
  workers) the list was stale AND could miss costumes the pinned host has never
  heard of, forcing a browser refresh. The list is now re-queried every time
  the Emotions tab opens and unioned across ALL workers holding the character.
- The Clothes tab's outfit gallery host-side names get the same multi-worker
  union.

## [1.70.2] - 2026-07-10

### Changed — VNCCS Native: safer upscaler defaults
- Upscaler now defaults to **Off** (was SeedVR) and the upscale resolution
  defaults to **1024** (was 2048) — accidental runs no longer kick off heavy
  SeedVR jobs that most machines choke on. The dropdown lists Off first;
  SeedVR is labeled "best, heavy".

## [1.70.1] - 2026-07-10

### Added — delete characters everywhere (stories already had it)
- **Main character grid:** every character card gets a 🗑 button (hover) —
  engine characters delete via the existing route (datasets removed too);
  VNCCS characters delete via the new catalog route below.
- **VNCCS Native Library tab:** 🗑 Delete character per card.
- **New `DELETE /api/studio/vnccs/catalog/{character_id}`** — removes the
  catalog entry plus EVERY app-side asset it owns (pose runs, base/costume
  preview versions, files on disk). Optional `?from_hosts=true` also deletes
  the character's folder on the recorded VNCCS workers via the node's own
  POST /vnccs/delete (the node UI's DEL) — the UI asks with a second confirm.
- Story delete was already present (trash icon in the stories sidebar).

## [1.70.0] - 2026-07-10

### Added — Character Studio: New Character mode dialog + VNCCS Klein Hybrid
- Clicking "New Character" on the main character screen now opens a mode
  picker: **VNCCS Native** (the existing mode) or **VNCCS Klein Hybrid** — a
  clone of the Native interface at /studio/vnccs-klein where Klein-powered
  steps will be grafted in (currently identical to Native, labeled
  experimental). The legacy engine-based form remains reachable via a small
  link in the dialog (frozen, not fixed).

### Added — VNCCS Native: create mode (New/Clone) persists per character
- Characters remember HOW they're made: `manifest.vnccs.create_mode` with
  **clone precedence** (once cloned, tweaks from the New form can't flip it
  back). A clone run stamps it automatically; creator runs only set 'new' if
  never cloned.
- The Clone screen saves in full — analyzed fields AND the uploaded reference
  image list — via 💾 Save and automatically with every clone run. Reopening a
  cloned character from the character list lands directly on the Clone sub-tab
  with references and fields restored for review/tweaking.

### Added — docs/ENGINE_MODE_FEATURES.md
- Full inventory of the legacy engine-based method: everything it was planned
  to do, what it shipped, and the 14 EXTRAS it has over VNCCS Native (LoRA
  dataset pipeline, multi-model base, per-stage engines, pose import breadth,
  SeedVR2 post-process, Generate-All, push-to-project, …) — the checklist for
  building new Native-derived modes.

## [1.69.1] - 2026-07-10

### Fixed — VNCCS Native: parallel clone runs failed on workers without the reference files
- "Сначала загрузите изображение персонажа в Character Cloner" ("upload a
  character image first") on fan-out chunks: the Cloner node reads its source
  images from the worker's LOCAL ComfyUI input folder, but references were
  uploaded to the pinned host only — every other worker loaded zero images and
  errored. The parallel route now replicates the reference files to each chunk
  worker before submitting (download from pinned /view → upload to the worker's
  input folder); if replication to a worker fails, its chunk is rerouted to
  the pinned host instead of failing.

## [1.69.0] - 2026-07-10

### Fixed — VNCCS Native: Cloner matches the node UI (no phantom preview button)
- Verified against the node source: the cloner panel has NO "generate preview"
  button — its own code says "(Preview removed: Using Native Preview Window
  below)" and the big preview shows the SELECTED SOURCE IMAGE. The 1.68.0
  "Generate Character preview" button is removed; instead the reference images
  now work like the node: a large preview of the selected source, clickable
  thumbnails (selected = highlighted, ✕ removes one, clear all), and clicking
  the preview opens the lightbox with arrows across all references.
- 💾 Save (analyzed fields → library) stays.

### Changed — VNCCS Native: Create tab gets New / Clone sub-tabs
- Cloner is no longer a separate top-level tab: Create now has "New" (the
  existing form) and "Clone" (the reference-clone flow) sub-tabs, so you pick
  how the character comes to life in one place.
- Clothes and Emotions follow the active sub-mode's character: switching to
  those tabs preselects the character you're working on in New or Clone
  (applied once per character, so a manual dropdown pick isn't stomped).

## [1.68.0] - 2026-07-10

### Added — VNCCS Native: Cloner tab gets the Create-tab QoL
- **✨ Generate Character preview** — renders ONE default-pose image from the
  analyzed clone fields (fast) so you can audition the description before the
  full reference-conditioned clone run. Each preview files as a base VERSION
  with the same n/N ‹ › ● Active browser (and lightbox) as the Create tab,
  which now also shows on the Cloner tab.
- **💾 Save** — saves the clone's name + analyzed fields to your library
  without generating.
- Version browsing is per-character now: previewing a different character
  starts a fresh version list instead of mixing with the previous one.
- (Already present via the shared pose section: pose selection + library,
  upscaler, ⚡ parallel split, and the 🎲 seed control.)

## [1.67.0] - 2026-07-10

### Changed — VNCCS Native: Emotions tab catches up (node-UI parity)
- **Emotion picker with face images:** the multi-select list is replaced by an
  image-tile grid using the node's own emotion face pictures
  (`get_emotion_image` per emotion, with a "no image" placeholder exactly like
  the node UI), plus All/None buttons. Click tiles to toggle.
- **Character preview shows ALL poses:** the Emotions tab's preview strip now
  merges every recorded worker's sprite list (same multi-worker fix the
  Clothes mannequin got in 1.66.0) instead of one worker's shard.

### Added — VNCCS Native: pose strips open in the lightbox
- Clicking the preview image in the Clothes mannequin picker or the Emotions
  character preview opens it full-size in the lightbox with ‹ › arrows /
  arrow keys across the whole pose set. On the Clothes tab, arrowing in the
  lightbox also UPDATES the mannequin selection — browse big, land on the pose
  you want, close, and it's picked.

## [1.66.0] - 2026-07-10

### Changed — VNCCS Native: mannequin picker shows ALL generated base poses
- The Clothes tab's mannequin strip previously listed only ONE worker's sprite
  files — with multi-worker fan-out each machine stores just its shard, so only
  a few poses were offered. The picker now merges every recorded worker's
  sprite list, so you cycle through the character's FULL generated pose set
  (including any poses you add later), with the source worker shown when more
  than one is involved.
- Each pose remembers which worker it lives on, and the costume preview runs
  on that exact machine — so whichever pose you pick is the one that gets
  dressed.

## [1.65.0] - 2026-07-10

### Added — VNCCS Native: browse a pose set inside the lightbox
- Opening any image from a pose grid (base poses, costume poses, live run
  results) now opens it as a GALLERY: ‹ › arrows on the lightbox edges (and
  ←/→ arrow keys) step to the previous/next image in that set without closing,
  with an "n / N" position badge up top. Zoom/pan reset per image; Esc closes.
- Base/costume preview lightboxes: ←/→ arrow keys now cycle versions too.

## [1.64.2] - 2026-07-10

### Fixed — VNCCS Native: pose libraries live on the right tabs
- The Create tab's library grid now shows ONLY the character's base poses
  (creator/cloner finals) — costume and emotion poses no longer leak into it.
  The "show pipeline intermediates" toggle still reveals everything.
- The Clothes tab now shows the generated poses for the SELECTED costume
  (creator-tagged clothes/emotions finals for that outfit), with the same
  lightbox + ✕ delete controls, refreshed automatically after runs and when
  switching characters/outfits.

## [1.64.1] - 2026-07-10

### Changed — VNCCS Native: base/costume preview image fills its panel
- The Base image and Costume image previews were capped at 420px tall, which
  left portrait sprites small and hard to read. They now render centered at the
  full width of the results panel (click still opens the zoom/pan lightbox).

## [1.64.0] - 2026-07-10

### Fixed — VNCCS Native: costume preview finally dresses the pose you picked
- Root cause was the multi-worker fan-out: each worker holds only ITS shard of
  a character's pose sprites, and the mannequin strip browsed the PINNED host's
  files while the preview ran on a recorded shard worker — "pose 3" was a
  different file on each machine. The strip now browses the exact worker the
  preview will run on (relay accepts a whitelisted `_vnccs_host` override; the
  strip hint shows which worker), and the preview request pins that same
  worker.

### Fixed — VNCCS Native: re-running a generation actually regenerates
- We submitted graphs with seed=0 and relied on VNCCS randomizing at execution
  time — but ComfyUI caches nodes by their inputs, so a byte-identical
  resubmission never executes and instantly returns the previous images (the
  "instantly completes" effect). A concrete random seed is now rolled app-side
  per run (node UI "randomize" parity), shared across all parallel chunks of
  that run, and reported back (shown in the completion message).
- New seed control next to the generate buttons: "🎲 New random seed each run"
  (default, matches the node UI dice) or untick for a FIXED seed — type one or
  leave blank to roll once and keep it (the field auto-fills with the seed
  used, and fixed reruns then benefit from ComfyUI's cache, completing
  instantly by design).
- Request gen_settings now LAYER over saved gen_settings instead of replacing
  them, so the seed control can't clobber saved model settings.

## [1.63.0] - 2026-07-10

### Fixed — VNCCS Native: costume preview honors the background color and mannequin pose
- **Background:** the ClothesDesigner builds its prompt from
  `gen_settings.background_color` inside the designer state — we never passed
  the UI's selection, so previews always used the vendored baseline. The
  selected background is now sent (the node supports Green/Blue; White/Alpha
  fall back to Green — noted in the UI).
- **Mannequin pose:** `selected_preview_sprite` was sent with costume
  `'Original'`, but the pose strip you cycle enumerates sprites with NO
  costume. When a character has both Original and Naked sprite folders the two
  lists resolve different folders, so the node dressed the wrong sprite. Both
  sides now enumerate identically (costume = null), so the preview dresses
  exactly the pose shown in the strip.

### Added — VNCCS Native: outfit gallery, prompt saving, import from another character
- The Clothes tab now opens with an **Outfit gallery** for the selected
  character: every costume as a card (active-version thumbnail, name, version
  count; host-side costumes appear too). Click a card to load its name +
  prompt set; **➕ New outfit** clears the form.
- **⬇ Import from character…** — pick any cataloged character, see their
  outfits with thumbnails, click one to copy its full prompt set (top / bottom /
  head / face / shoes) into the slots to replicate on the current character.
- **💾 Save outfit prompts** button under the slots (new
  `POST /api/studio/vnccs/costume-info`) — saves prompt tweaks without
  generating; prompts also auto-save with every costume preview and every full
  costume generation run. Saved prompts live on the character manifest
  (`costumes[name].costume_info`) and are what the gallery/import restore.
- Costume version actions (Set active) now bind to the character selected on
  the Clothes tab rather than the one loaded in Create.

## [1.62.1] - 2026-07-10

### Fixed — VNCCS Native: lightbox scroll-zoom no longer scrolls the page behind it
- The results lightbox now locks page scrolling while open and registers a
  non-passive wheel listener on the overlay (browsers force React's synthetic
  wheel handler to be passive, so its `preventDefault()` was ignored) — the
  mouse wheel zooms the image only, with `overscroll-behavior: contain` as a
  further guard against scroll chaining.

## [1.62.0] - 2026-07-10

### Fixed — VNCCS Native: pose batches no longer overwrite each other in the library
- **Root cause of "I don't see my poses, just 4 images":** every ingest *replaced*
  the character's cataloged output list for that step, so each new pose batch —
  and even each worker chunk of a parallel run — wiped the record of earlier
  poses. Ingest now **merges** (appends, deduped) and the manifest update is
  serialized so concurrent chunks can't drop each other's images.
- **Self-heal on reload:** opening a character re-scans its asset folder and
  re-links any previously orphaned images back into the catalog, so poses
  "lost" by older versions reappear automatically.

### Changed — VNCCS Native: character library view shows poses, not pipeline taps
- Reloading a character now shows **final BG-removed sprites** under friendly
  headings ("Base poses", "Costume poses — <outfit>", "Emotions") instead of raw
  tap groups (`creator/sheet`, `creator/pose_generation`, …). A "show pipeline
  intermediates" toggle reveals the faces / pre-BG passes when needed.
- Costume/emotion finals are grouped per outfit; base-version filtering
  ("poses linked to base vN") works as before.

### Added — VNCCS Native: prune the library as you build
- Every library image has a ✕ button (DELETE
  `/api/studio/vnccs/catalog/{character_id}/images/{asset_id}`) — removes the
  manifest entry, the Asset row and the file on disk; the hero thumbnail is
  reassigned if you delete it.
- After a generation run finishes, the library grid refreshes automatically so
  new poses appear alongside the old ones — generate a few, prune, add more.

## [1.61.0] - 2026-07-10

### Added — VNCCS Native: Clothes tab gets the full staged flow (audition → version → poses)

- **Results are now per-tab** — switching tabs clears the previous run's grid (the Clothes tab
  no longer shows the Create tab's images).
- **Mannequin pose picker**: the Clothes tab shows the character's existing pose sprites with
  ‹ › cycling; the selected pose is the sprite the costume preview dresses
  (`selected_preview_sprite`) — audition the outfit on the pose that matters.
- **✨ Generate costume preview** (like the node UI): dresses the chosen mannequin sprite via
  the host's `/vnccs/control_center/clothes_preview` (new `POST /api/studio/vnccs/`
  `costume-preview`, runs on a worker that holds the character; auto-creates the costume).
- **Costume versioning with prompt snapshots**: every preview is persisted as a version per
  (character, costume) — n/N browser with ‹ ›, ● Active / Set active (also inside the
  lightbox), newest = active default, and **cycling versions restores that image's outfit
  prompts** so you can tweak an old look without rebuilding it. Stored in
  `manifest["vnccs"]["costumes"]`; `POST /character/{id}/costume-active` switches.
- **Pose runs link to the active costume version** (ingest tags `costume` +
  `costume_version`) — same tweak-and-rerun loop as base versions.
- **Character pickers fixed**: freshly generated characters now appear — the host list is
  re-fetched when opening Clothes/Emotions and merged with our catalog names (characters can
  live on shard workers the pinned host doesn't know). Costume name input gets a datalist of
  the character's existing costumes.

## [1.60.0] - 2026-07-10

### Added — VNCCS Native: base-image versioning linked to pose runs

- **Every "Generate Character" preview is now persisted as a base-image VERSION**
  (`save_base_preview` → asset under assets/vnccs/<char>/base/ + entry in
  `manifest["vnccs"]["base_versions"]`); the newest version automatically becomes the ACTIVE
  default. `POST /character/{id}/base-active` switches the active version.
- **Pose runs link to the base version that was active when they were ingested**
  (asset meta `vnccs.base_version`) — tweak the base, set it active, rerun Generate Poses,
  and every iteration stays grouped instead of starting over.
- **Version browser UI**: the Base image panel shows `n / N` with ‹ › toggles, a green
  "● Active" indicator and a "Set active" button; the border highlights the active version.
  The lightbox gets the same controls (page versions, see active state, set active) while
  zoom/pan still work.
- **Library images filter by version**: by default the character's images show only those
  linked to the base version being viewed (legacy unversioned images always shown); an
  "all versions" checkbox reveals everything. `GET /catalog/{id}/images` now returns
  `base_versions`, `active_base` and each image's `base_version`.

## [1.59.0] - 2026-07-10

### Fixed / Added — VNCCS Native: correct final sprites, character editor binding, Pose Studio loading

- **Results now show the BG-REMOVED sprites** — verified in the generator source: the creator's
  post-BG finals come out of the `sheet` output (`faces` duplicates it) and `upscaled` is the
  PRE-BG upscale pass; clothes/cloner/emotions finals are the `sprites` outputs. The results
  filter now prefers sprites/sheet (v1.58.1 wrongly preferred `upscaled`, showing green-BG
  images). "Show all pipeline outputs" still reveals everything.
- **VNCCS Native is now the editor for its characters**: clicking a VNCCS-created character on
  the Character Studio screen (marked with a ✨ VNCCS badge) opens `/studio/vnccs?char=Name`
  instead of the engine-mode editor. The page deep-links: prefills the Create form from the
  saved form, sets the Clothes/Emotions character pickers, and shows ALL previously ingested
  images grouped by output (`GET /api/studio/vnccs/catalog/{id}/images`, served via
  /api/files) in the Results panel with lightbox — so characters can be re-edited and tweaked
  over time. Library "Load into Create" uses the same loader.
- **Pose Studio "Failed to fetch dynamically imported module"**: the backend only mounted
  `/assets` and the SPA catch-all returned index.html for everything else — including the
  vendored `/vnccs-pose/*.js` ES modules. `serve_spa` now serves real files from the build
  (traversal-guarded, `.js` pinned to text/javascript for Windows mime-registry quirks) before
  falling back to index.html.

## [1.58.1] - 2026-07-10

### Fixed — VNCCS Native: rear-facing sprites, tiny figures, duplicate outputs in Results

- **Rear-facing generations**: the pose captures had long dark slivers scribbled across the
  face — MakeHuman's cleaned weight map leaves ~350 face vertices (eyelid/eye area) with ZERO
  bone weight, which collapsed to the origin during skinning and dragged sliver polygons over
  the head; with no readable face, the QIE pose transfer rendered the character facing away.
  Skinning now normalizes partial weight sums and binds orphan vertices to their nearest bone;
  interior helper faces (eyes/teeth) are excluded and backfaces culled — mannequin faces are
  now clean and front poses generate front-facing characters.
- **Tiny figures**: capture framing fit the SMALLER canvas dimension against the LARGEST body
  extent (standing figure ≈ 1/3 of the 640×1536 frame). Now fits the limiting axis to ~92% of
  the canvas (matching the viewer's captures), with the per-axis no-clip clamp kept.
- **Results show final sprites only** (like the node UI): the grid now displays the
  post-BG-removal `upscaled` taps (or `sprites` for emotions) and hides sheet/faces/pre-BG
  intermediates behind a "Show all pipeline outputs" toggle. Everything is still ingested into
  the library regardless.

## [1.58.0] - 2026-07-10

### Added — VNCCS Native: multi-worker fan-out + real progress UI

- **Parallel generation across the VNCCS fleet**: `POST /generate-parallel/{step}` splits work
  across every reachable vnccs-capable worker (`GET /hosts`, `list_vnccs_hosts`). Sharding
  respects VNCCS's local sprite storage: creator/cloner split the pose set round-robin and
  record participating workers on the character (`manifest["vnccs"]["hosts"]`, shown in the
  catalog); clothes chunks go only to recorded hosts (base sprite is local) with poses split
  across them; emotions submit the same request to every recorded host — each worker processes
  only its local sprites. A worker that rejects a chunk is skipped; the run continues.
- **Progress UI**: overall progress bar (chunks completed / total) + a status row per chunk
  (worker · chunk size · running/filing/done/error · live image count), with per-chunk ingest
  as each finishes and a completion summary (total images, workers used, elapsed minutes).
  ⚡ parallel toggle appears when >1 worker is online (default on; also on Emotions).
- `/result/{prompt_id}` and `/view` accept a `host` override so chunk results poll/proxy the
  worker they ran on; results grid + lightbox are host-aware.

## [1.57.1] - 2026-07-10

### Fixed — VNCCS Native: poses were rendering as identical A-poses (thumbnails AND generation)

Root cause (found by comparing against the vendored three.js viewer source): the VNCCS node's
Python fallback pose math — which our pre-renderer originally ported faithfully — is doubly
wrong upstream. It composes Euler rotations Rz·Ry·Rx while the three.js panel uses XYZ order
(Rx·Ry·Rz), AND it applies them in MakeHuman's bone-aligned rest frames while the viewer builds
its bones with translation-only, world-axis-aligned frames. Result: every pose collapsed to a
near-A-pose — so the pose thumbnails were all identical AND the captures fed to generation
produced standing characters regardless of the selected pose.

- **`_apply_pose` rewritten to replicate the three.js viewer FK exactly**
  (`local = T(head_rel)·EulerXYZ(rot)`, `world = parent·local`, `skin = world·T(-head_abs)`,
  model rotation in XYZ order). All 12 default silhouettes now match the node UI's Pose Manager
  (leg raised, hand on hip, arms up, wide stances, side profile…), and generated sprites follow
  the selected poses.
- Capture framing gets a per-axis clamp so cam_zoom can never push limbs off-canvas.
- Poses added from the Pose Library now show their preview thumbnails as cards (with ✕ remove),
  matching the default-pose grid, instead of text chips.

Note: generating poses without first rendering a character preview is fine — the Step-1 run
saves the character config itself; and 1 selected pose returning 4 images is expected (the four
pipeline outputs: sheet / faces / pose_generation / upscaled).

## [1.57.0] - 2026-07-10

### Added — VNCCS Native: node-parity pose manager + results lightbox

- **Default poses ARE the node's out-of-the-box 12** (verified byte-identical from the vendored
  STEP1 graph: same bones/rotations, mesh 0.66/0.5/0.85, 640×1536 camera). Thumbnails are now
  rendered as black-on-white SILHOUETTES to match the node UI's Pose Manager exactly.
- **Modifiable pose list like the node**: every default pose card gets a ✕ remove button
  (plus the existing select checkbox-style toggle), "↺ Restore defaults", and "💾 Save pose
  set" — the customized list (removed defaults + added library poses) persists in
  `studio_vnccs_settings.pose_set` and preloads on the next visit.
- **Add your own pose packs**: the Pose Library modal gets an "add Hugging Face repo id" input
  (host route `pose_library/repositories/add`). Any HF repo with a `pose_library.json` manifest
  works; the built-in pack is `MIUProject/VNCCS_PoseLibrary_Main`. Poses saved from our 3D
  Pose Studio tab land in the host's local library too.
- **Results lightbox**: click any result image (or the default-pose preview) to open a
  full-screen viewer — scroll to zoom (cursor-anchored, up to 12×), drag to pan, double-click
  to reset, Esc/✕ to close.

## [1.56.1] - 2026-07-10

### Fixed — VNCCS Native: empty Pose Library + stale-backend hint

- **Pose packs downloadable in-app**: the host pose library is fed by Hugging Face pose
  repositories that must be downloaded once (`/vnccs/pose_library/repositories` +
  `/refresh`) — a fresh worker has an EMPTY library, which is what Lorenzo saw. The
  "➕ Pose Library" modal now lists the host's repositories (the node UI's built-ins) with
  enable toggles and Download buttons (per-repo + all-enabled), then reloads the pose list.
- Library pose cards now show their preview images (`pose_library/preview/{name}`), and
  entries whose full data wasn't returned by `list` are fetched on demand via
  `pose_library/get/{name}` when added to the pose set.
- "Preview failed: Method Not Allowed" is the SPA catch-all answering for a route the RUNNING
  backend doesn't have (405 on POST /preview = the backend process predates v1.56.0). The
  error now says so explicitly and tells you to restart the backend.

## [1.56.0] - 2026-07-10

### Changed — VNCCS Native: staged creation flow (matches the panel's intent; fixes the
"Timed out waiting for VNCCS generation" experience — the old Create button ran the FULL
12-pose + SeedVR pipeline in one shot)

- **Create tab reworked**: "✨ Generate Character" now renders ONE default-pose preview via the
  host's `/vnccs/preview_generate` (fast; new `POST /api/studio/vnccs/preview` merges the
  vendored gen_settings baseline + saved overrides). "💾 Save" persists the form to the Studio
  catalog (`POST /character/save`, stored in `manifest["vnccs"]["form"]`); Library cards get
  "Load into Create". Then pick poses and hit "Generate Poses" for the full pipeline.
- **Pose selection**: default 12 VNCCS poses shown with app-rendered thumbnails
  (`GET /pose-defaults`, best-effort via pose_render) + "➕ Pose Library" modal to add host
  library poses (full data). New `pose_set` on `POST /generate/{step}` replaces the Pose Studio
  pose list (capped 16 = node CSR limit); the app-side capture pre-render follows the subset.
  Shared across Create / Cloner / Clothes.
- **Upscaler controls** on Create / Cloner / Clothes: SeedVR/GAN/Off + upscale resolution +
  pose target size → `generator_overrides` merged into the generator widget_data
  (`upscaler.mode/resolution`, `pose_generation.target_size`) — rest of the vendored
  upscaler/BG-remove config preserved.
- **Clothes/Emotions character preview**: pose-sprite strip with prev/next switching (host
  `get_character_pose_preview` + `_meta`), Emotions adds a costume switcher for the preview.
- **Emotions costumes**: host costume list (`get_character_costumes`) rendered as checkboxes —
  generate all selected emotions × all selected clothing sets (incl. the base set); free-text
  fallback when the list is empty.
- Poll timeout 20 → 60 min with elapsed-minutes status; timeout message now explains the job
  may still be running on the host and suggests fewer poses / Upscaler=Off.

## [1.55.1] - 2026-07-10

### Fixed — VNCCS Native: Pose Studio crash on headless generation (2nd live-test error)

`VNCCS_PoseStudio` died with `could not broadcast input array from shape (19158,3) into shape
(19158,)`. Reproduced locally against the vendored MakeHuman data: the node's headless Python
fallback renderer has an UPSTREAM bug — poses with non-zero `modelRotation` (all rear/side
views; 8 of the 12 default poses) hit `np.dot(posed, rot.T)` where `rot` is `np.matrix`, the
vertex array silently adopts matrix semantics, and the screen projection breaks. The ComfyUI
panel never hits this because the browser pre-renders the poses (CSR `captured_images` path).

- **New `vnccs_native/pose_render.py`**: faithful port of the node's fallback renderer
  (MakeHuman solve → FK skinning → flat-shaded PIL render) with `np.asarray` guards on every
  matrix product, running app-side against the vendored `vnccs-utils/CharacterData`.
- **`_inject_pose_captures`** in `workflows.py`: every assembled step graph containing a
  Pose Studio node (creator/cloner/clothes) gets its 12 poses pre-rendered and injected as
  `pose_data["captured_images"]` (+ lighting_prompts), so the node takes its well-tested CSR
  path — the same one the panel uses. First generate pays ~17 s (data load + renders), then
  cached. Honors view size, bg color, lights and cam_zoom from the pose data.
- Best-effort: if `vnccs-utils/CharacterData` is missing or anything fails, the graph is
  submitted unchanged (previous behaviour).

## [1.55.0] - 2026-07-09

### Fixed — VNCCS Native: "No Checkpoint selected in Character Creator V2" (first live test)

Root cause: the GUI meganodes (CharacterCreatorV2/CharacterCloner/ClothesDesigner) declare
`widget_data` as a HIDDEN input, so the UI→API converter dropped it and the assembler rebuilt
it from `{}` — the creator then ran with empty gen_settings → illustrious mode → empty
ckpt_name → ValueError on the worker.

- **`_seed_hidden_widget_data`** (`vnccs_native/workflows.py`): each meganode's original
  widget_data JSON is now carried over from the vendored graph before patching, so steps run
  with the graph's WORKING baseline (Creator: anima + anima-base-v1.0 + Qwen CLIP/VAE + turbo
  LoRA + mode_settings profiles; the generator's SeedVR-upscaler/BG-remove config was already
  carried as a declared widget). Verified against the node source + Lorenzo's node-UI
  screenshots (samples/vnccs/, untracked).
- **Mode-aware gen_settings merge** (`_merge_gen_settings`): VNCCS `normalize_gen_settings`
  applies `mode_settings[mode]` LAST, so overrides are now written both top-level AND into the
  active mode profile (previously e.g. steps overrides would be silently shadowed by the
  baseline profile). Baseline's fixed template seed is reset to 0 (=random per run,
  `generate_seed(0)`) unless a seed is pinned.
- **Emotions honors saved generation settings too**: merged into the EmotionGeneratorV2
  `generation_settings` JSON widget (+ generation_model synced to the mode).

### Added

- **Settings → Character generation** section on the VNCCS Native page: mode
  (anima/illustrious), base model picked from the host's diffusion_models/checkpoints lists,
  steps/cfg/sampler/scheduler (host lists), seed (blank = random). Blank everything = the
  vendored graph's working defaults. Saved in `studio_vnccs_settings.gen_settings`.

## [1.54.0] - 2026-07-09

### Added — VNCCS Native LLM Wizards (Character · Clothes · Cloner-Analyze)

The wizard buttons from the VNCCS ComfyUI panel now exist in VNCCS Native:

- **✨ Character Wizard** (Create tab): plain-language idea → fills sex/age/race/skin/hair/
  eyes/face/body/details.
- **✨ Clothes Wizard** (Clothes tab): outfit idea → fills the five costume slots.
- **🔎 Analyze reference** (Cloner tab): vision-describes the first uploaded reference into an
  editable character-info panel (incl. aesthetics + NSFW) that is now actually SENT with the
  clone job (previously the cloner submitted an empty `character_info`).

Backend: `POST /api/studio/vnccs/wizard/{character|clothes|clone-analyze}` — **host-first,
Ollama fallback**. Host path relays to the real VNCCS wizard routes (identical Qwen2.5-VL GGUF,
prompts, tag catalog and post-processing = same result as the VNCCS panel; note the host loads
the LLM per request and auto-downloads ~5 GB on first ever use). On host failure the app reruns
the VERBATIM VNCCS prompts (new `vnccs_native/wizards.py`) on its own Ollama (`ollama_model` /
`ollama_vision_model`), pulling the same tag catalog from the host `/vnccs/get_tags`; the UI
shows which backend produced the fields. `backend: "host"|"ollama"` forces a path.

## [1.53.2] - 2026-07-09

### Fixed — full past-asks audit fix-wave (6 parallel audits vs every prior request; report: AUDIT_2026-07-09)

- **Studio "FLUX.2 Klein T2I" base-model option was inert** (`services/jobs/dispatcher.py`):
  no-ref `klein_t2i` jobs are always routed through the first-pass redirect, and
  `flux2_klein_dev_9b` matched no branch — picking it silently rendered Z-Image Turbo.
  An explicit per-job Klein override now skips the redirect and runs the real Klein T2I
  workflow (`KLEIN_EDIT_ULTRA_WORKFLOW_Text2Image.json`); asset meta keeps the Klein label
  instead of being resolved to the first-pass generator.
- **Pose Organizer scans could die silently** (`api/tools.py`): the scan background task was
  created without retaining a reference, so Python could garbage-collect it mid-run on large
  folders. Now retained in `_BG_TASKS` with a done-callback, mirroring the sample-gen path.
- **Talkie video prompts survive text export/import** (`services/project_text_io.py`):
  `_project_renders_video` excluded talkie, so `video_*` fields were dropped on round-trip
  even though the lip-sync engines consume the scene video prompt.
- **Batch mode can create Talkie projects** (`BatchItemAddModal.tsx`): added the missing
  "Talkie (Lip-Sync)" render type (backend already supported it) with a portrait-setup hint;
  talkie uses the narration-style SRT/Whisper options.
- **Mobile projects list labels talkie** (`MobileProjects.tsx`): was showing the raw mode string.

### Docs

- README: removed the stale "ultra pipeline not included yet" note (it ships) and added the
  missing `anima-turbo-lora-v0.1.safetensors` row to the Anima model table.
- `docs/MODEL_PROMPTING.md`: added the missing Anima section (quality-tag formula, dispatch-side
  `ANIMA_DEFAULT_NEGATIVE`, narration-mode precedence note).
- `vnccs_native/graph.py`: documented the hidden-`widget_data` coupling — new VNCCS step nodes
  MUST get a matching `_apply_*` patch in workflows.py.

### Known items deliberately left open (from the audit, for later decision)

- Anima inpaint ignores the object `reference_asset_id` (prompt-guided only, unlike Klein).
- Narration/talkie modes override the Anima prompt formula with the narration system prompt.
- CustomBaseModal (Advanced) uses the saved — not live — NSFW toggle for `krea2_sfw_override`.

## [1.53.1] - 2026-07-09

### Fixed — VNCCS Native audit fix-wave (2 parallel read-only audits: backend + frontend)

- **`submit_prompt` NameError on the error path** (`vnccs_native/client.py`): `json` was only imported
  locally, so a host `node_errors` response raised `NameError` and masked the real error as a 500.
  Added a module-level `import json`.
- **Relay whitelist `..` traversal bypass** (`vnccs_native/client.py`): `_path_allowed("models/../../prompt")`
  returned True and `urljoin` collapsed it to a non-`/vnccs/` core route (`/prompt`, `/view`). Now any
  subpath containing a `..` segment is rejected.
- **Ingest clobbered prior-step outputs** (`vnccs_native/ingest.py`): outputs were keyed by label, and
  labels overlap across steps (`faces`/`pose_generation`/`upscaled` in both creator and clothes), so
  ingesting Clothes overwrote the Creator's catalog entries. Outputs are now namespaced `"{step}/{label}"`;
  `catalog.link_to_project` matches on the label suffix.
- **`/result` reported errored jobs as `completed`** (`api/vnccs_native.py`): a host `error` status with no
  outputs now returns `status: "error"` so the UI surfaces the failure instead of trying to ingest nothing.
- **Pose Studio morph-worker listener leak** (`PoseStudio3D.tsx`): the `error` branch of a solve now removes
  its `message` listener (previously only the success branch did).
- **`editModel` dropped other saved Control-Center keys** (`VNCCSNativePage.tsx`): picking an edit-model
  override now merges with the saved `control_center` blob instead of replacing it.


## [1.53.0] - 2026-07-09

### Added — VNCCS Native mode: Cloner + project-linking (Phase 6)

Completes the VNCCS Native build: clone characters from reference photos, and make any cataloged
VNCCS character usable inside real projects.

- **Cloner (Step 1 clone).** Backend: `VNCCSClient.upload_image` (→ host `/upload/image`), a
  `POST /api/studio/vnccs/upload` endpoint, and `_apply_cloner_images` which injects the uploaded
  refs into `CharacterCloner.source_images`. `assemble_step`/`build_cloner_graph` take `cloner_images`
  (unit-tested). Frontend: a **Cloner** tab — upload reference images (multi), name, generate → the
  same poll/ingest pipeline. The clone emits both clothed and nude sprite paths.
- **Catalog + project-linking.** `catalog.py`: `list_catalog` (all ingested VNCCS characters via
  `manifest["vnccs"]`) and `link_to_project` (copies a character's images into a target project as
  `CHARACTER` reference assets so they can be used in that project's scenes). Endpoints
  `GET /catalog` + `POST /link`. Frontend: a **Library** tab listing cataloged characters with a
  project picker and one-click "Link to project".

This closes the phased VNCCS Native replica: Create · Cloner · Clothes · Emotions · 3D Pose Studio ·
Library, all driving the real VNCCS nodes on a pinned host and cataloged in our system.


## [1.52.0] - 2026-07-09

### Added — VNCCS Native mode: 3D Pose Studio (Phase 5, v1)

The interactive 3D pose editor, built by **reusing the portable, ComfyUI-independent
`PoseViewerCore`** rather than porting it — the vendored engine does the Three.js camera/FK/IK/hand
gizmos; our host only wires controls + the pose-library round-trip.

- **Vendored** the portable JS to `frontend/public/vnccs-pose/` (served static, self-resolving via
  `import.meta.url`): `vnccs_pose_studio_core.js`, `three.module.js`, `OrbitControls.js`,
  `TransformControls.js`, `vnccs_hand_presets.js`, `vnccs_openpose_import.js`, `vnccs_mixamo_import.js`,
  `vnccs_camera_control.js`, `vnccs_pose_morph_worker.js`. The morph worker's `MORPH_URL` is patched to
  our proxy (`/api/studio/vnccs/r/character_studio/morph_data.bin`) and given an `init` message that
  returns the bone/joint topology for the first `viewer.loadData()`.
- **`PoseStudio3D.tsx`** (React host, lazy-loaded): mounts `PoseViewerCore` on a canvas, runs `init()`,
  drives the morph worker (topology → morphed vertices → `loadData`/`updateBodyVertices`), and exposes
  camera orbit + IK/FK toggle + body-shape sliders + capture. Author a pose and **Save to Pose Library**
  (`getPose()` + a captured preview → `/vnccs/pose_library/save`) — the same library the generation
  pipeline consumes. A "Pose Studio" tab was added to the VNCCS Native page (three.js only loads on open).

*Note:* this phase needs a live VNCCS host to fully exercise (it fetches `morph_data.bin` through the
proxy) and fails gracefully otherwise; Phases 1-4 are fully offline-verifiable, this one is not.


## [1.51.0] - 2026-07-09

### Added — VNCCS Native mode: Clothes (Step 2) + Emotions (Step 3)

Extends the VNCCS Native Character Studio mode (v1.50.0) with the next two pipeline steps,
both end-to-end (backend assembly + frontend + ingest into our catalog).

- **Backend** (`vnccs_native/workflows.py`): step-specific injectors added to `assemble_step` —
  `_apply_clothes_form` patches `ClothesDesigner.widget_data` (the 5 costume slots
  top/bottom/head/face/shoes + clone-image + ClothesCore LoRA), and `_apply_emotions_form`
  patches `EmotionGeneratorV2` (`character` + `costumes_data`/`emotions_data` as JSON strings +
  generation_model/prompt_style). New `build_clothes_graph` / `build_emotions_graph`. The
  `/generate/{step}` endpoint now accepts the clothes/emotions fields. Both assemblies unit-tested
  against the real vendored graphs.
- **Frontend** (`VNCCSNativePage.tsx`): a Create / Clothes / Emotions tab bar sharing one
  generate→poll→ingest engine. Clothes = character picker + costume name + 5 slot fields; Emotions =
  character picker + costumes + a multi-select emotion catalog loaded from the host
  (`/api/studio/vnccs/emotions`). Host character list drives the pickers.


## [1.50.0] - 2026-07-09

### Added — VNCCS Native mode (Phases 1-2: reuse layer + Creator end-to-end)

A NEW Character Studio mode, separate from the engine mode, that drives the *real* VNCCS
meganode pipeline on a pinned host and catalogs the outputs in our system. Built as a **thin
app over VNCCS** (the workers already run it), not a reimplementation.

- **Reuse layer (Phase 1).** `backend/services/character_studio/vnccs_native/`:
  - `client.py` — `VNCCSClient`: a security-whitelisted generic *relay* to the workers'
    `~80 /vnccs/*` routes (character/costume/emotion store, LLM wizards, HF pose library,
    previews, context lists) + typed helpers + core ComfyUI routes (`/object_info`, `/prompt`,
    `/history`, `/view`). `_path_allowed` blocks non-vnccs paths + traversal (unit-tested).
  - `host.py` — pins Studio-VNCCS work to one host (configured URL, else first vnccs-capable worker).
  - `api/vnccs_native.py` — `/api/studio/vnccs/*`: host config, typed context-lists/characters/
    emotions/pose-library, and a generic JSON/binary relay (`/r/{subpath}`).
  - `AppSettings.studio_vnccs_host` + `studio_vnccs_settings` (+ additive migration).
- **Generation engine (Phase 2).**
  - `graph.py` — faithful ComfyUI UI→API `/prompt` converter using the worker's `object_info`
    (no guessing at widget names); resolves Reroutes, taps generator outputs with SaveImage,
    patches JSON widgets. Unit-tested against the real Creator graph.
  - `workflows.py` — `assemble_step` / `build_creator_graph` / `build_cloner_graph`: inject our
    character form + gen-settings + Control-Center config into the vendored VNCCS Step graphs
    (`workflows/vnccs/STEP1_CREATOR|STEP1_CLONER|STEP2_CLOTHES|STEP3_EMOTIONS.json`).
  - `ingest.py` — downloads tapped outputs into our asset store and catalogs them on a
    `StudioCharacter` (VNCCS link stored in `manifest["vnccs"]` — no schema change).
  - Endpoints: `POST /generate/{step}`, `GET /result/{prompt_id}`, `GET /view`, `POST /ingest`.
  - **Frontend:** `components/VNCCSNative/` (`vnccsNativeApi.ts` + `VNCCSNativePage.tsx`) — settings
    (host pin + optional model), the VNCCS character form, and generate→poll→ingest with a results
    gallery. Route `/studio/vnccs` + a "✨ VNCCS Native" button on the Character Studio header.

Remaining (later phases): costumes, emotions, cloner UI, the 3D Pose Studio (reusing the portable
`vnccs_pose_studio_core.js`), and project/scene linking.


## [1.49.0] - 2026-07-08

### Fixed — deep VNCCS + Anima parity (phased audit vs vnccs/ + anima/ source)

- **Qwen pose was fed a backwards/over-specified prompt.** VNCCS drives pose/emotion/clothes via the
  task LoRA + a GENERIC system instruction + minimal prompt + latent_image_index. Our qwen prompts
  described the images backwards (claimed image1=character when image1 is the skeleton) and overrode
  the generic instruction — fighting the LoRA. Now: pose uses a minimal prompt + VNCCS_QIE_INSTRUCTION
  (image1=skeleton, image2=identity, latent_image_index=1, PoseStudio LoRA), matching VNCCS exactly.
- **Costume qwen** now uses VNCCS's `Dress the character:\n{outfit}\nsolid <bg> background` prompt +
  the generic instruction, and `latent_image_index` 2→1 (VNCCS never uses 2 for clothes).
- **Emotion**: qwen path uses the generic instruction; `auto` now prefers **FaceDetailer** (VNCCS's real
  emotion mechanism) when the worker has Impact-Pack. FaceDetailer params corrected to VNCCS values —
  bbox_threshold 0.5→0.1, bbox_crop_factor 3.0→4.5, sam_dilation 0→25, tiled encode/decode on, and the
  emotion text is now set into the FaceDetailer wildcard too.
- **Anima ULTRA dimension bug**: prepare_anima_workflow now sets the WIDTH/HEIGHT INTConstant nodes, so
  the SEGS body-detailer mask + i2i resize honor the project resolution (was hardcoded to 1920×1080 /
  produced mismatched SEGS at other sizes). ANIMA_INPAINT.json fallback set to turbo-only LoRA (matches
  the reference inpaint export).
- Confirmed our bundled pose presets + ClothesCore RC3.7 + base SFW/NSFW clothing tokens are
  byte/string-exact matches to the VNCCS source.

## [1.48.0] - 2026-07-08

### Fixed/Added — past-asks audit fix-wave (vs VNCCS source)

- **Pose render now matches VNCCS exactly** (LoRA fidelity): OpenPose line thickness 4→3, the one
  divergent bone color `neck->l_shoulder`→(255,85,0) (VNCCS FALLBACK_PALETTE), and DEFAULT_SKELETON
  restored to VNCCS values (reverted an incorrect flip). Confirmed our bundled pose_presets.json is
  byte-identical to VNCCS's vnccs_poseset.json.
- **Per-character NSFW now truly overrides the global SFW toggle** — the global SFW suffix is suppressed
  when a character is set NSFW (was still appended, fighting the nude clothing phrase).
- **Character clone extracts `nsfw` + `aesthetics`** (VNCCS parity) with a skin-color enumeration +
  anti-pale guard; the NSFW toggle now auto-populates from a reference image (applyTagSheet preserves
  booleans instead of stringifying them).
- **Anima img2img + inpaint are now triggerable** (were wired in dispatch but dead): Custom Base
  Advanced gets an img2img source (→ anima_i2i); the Inpaint modal gets a Klein/Anima engine selector
  (→ anima_inpaint). Added a default Anima negative prompt (user's token list) to all Anima renders.
- **Pose Organizer 'vision scan' implemented** — was a dead no-op param; now optionally describes each
  pose with the Ollama vision model and adds semantic tags (UI toggle in the organizer).
- **Emotion prompt folds the booru description tags** with natural_prompt (VNCCS convention), not
  natural_prompt alone.

## [1.47.0] - 2026-07-08

### Added — Pose/Expression Library pickers in Studio + skeleton fix

- **Pose Library picker** in the Character Studio pose tab: browse/search/select from the Tools Pose
  Library and add the chosen poses to the character's pose set (via /pose-library/to-presets).
- **Expression Library picker** in the emotion tab: pick expressions from the Tools Expression Library
  and generate them directly. `emotions/generate` now accepts `custom_expressions` [{name, natural_prompt}].
- Shared `StudioLibraryPicker` modal (pose thumbnails / expression list, category + search, multi-select).

### Fixed

- `DEFAULT_SKELETON` fallback flipped to standard front-facing OpenPose convention (subject-right on the
  viewer's left), matching the bundled pose presets. (Confirmed the actual bundled presets were already
  correct — the earlier 'wrong direction' was the centering bug fixed in 1.45.4; no global flip applied,
  which would have broken the correct presets + imported OpenPose.)

## [1.46.0] - 2026-07-08

### Added — direct PNG OpenPose control images

- **Import PNG poses** (Character Studio pose tab): bulk-import PNG/JPG OpenPose skeleton images (a
  single image or a .zip of thousands) as pose presets. `POST /pose-presets/import-images`.
- These presets use the image **directly as the pose control** (no keypoint conversion, no re-render),
  so your existing OpenPose skeleton collections work as-is and bypass the renderer entirely. They
  appear in the pose grid with their own thumbnail and generate on either engine.

### Fixed

- Pose skeleton is now scaled + centered into the target canvas (was drawn at raw 512x1536 coords →
  off-center). Corrected the stale 'Klein has no pose-control' warning (Klein uses the RefControl Pose
  LoRA). Worker-aware LoRA-name resolution + ClothesCore filename (from 1.45.3).

### Known / next

- Built-in joint poses appear mirrored vs standard OpenPose (subject-right on viewer-right) — pending
  confirmation before flipping; PNG-direct poses are unaffected. Pose/Expression Library pickers inside
  the Studio tabs still to come.

## [1.45.4] - 2026-07-08

### Fixed — pose skeleton centering + stale Klein message

- **Off-center / mis-scaled poses (both engines):** pose joints are authored in a native 512x1536
  space but the OpenPose control skeleton was drawn at raw coordinates on the target canvas, so the
  figure landed off to one side. render_pose now scales + centers the skeleton into the target
  dimensions (aspect-preserved) — fixes the character being pushed to the left / wrong size.
- **Stale Klein pose warning corrected:** the PoseStudio tab said Klein has 'no pose-control'. Klein
  DOES do pose transfer via the RefControl Pose LoRA (set cs_klein_pose_lora + install
  refcontrol_v2_poses.safetensors); message updated. Qwen (VNCCS) still recommended for strongest control.

## [1.45.3] - 2026-07-08

### Fixed — LoRA path resolution (worker-aware) + pose sizing

- **Definitive LoRA-not-found fix.** Before submitting any workflow, the dispatcher now resolves every
  LoRA reference (LoraLoader `lora_name`, pysssss LoraLoader, and rgthree Power-Lora `lora_N` slots) to
  the worker's EXACT listed string, matched by filename against `/object_info`. This handles subfolders
  and Windows backslashes (e.g. `qwen\\VNCCS\\VNCCS_QIE2511_PoseStudio_ART_V5.9.5.safetensors`) with no
  config — replaces the fragile v1.45.2 slash-prefix approach (reverted). Covers both the Qwen pose
  engine and the Klein RefControl pose LoRA.
- Fixed the ClothesCore task-LoRA filename (`RC3.7`, was `RC3.x`).
- **Pose sizing:** the OpenPose control skeleton is now rendered at the TARGET character dimensions
  instead of a fixed 512x1536 canvas, so pose proportions match the output (QIE follows the skeleton's
  size via `latent_image_index=1`; Klein resizes the ref). Fixes distorted/mis-scaled poses.

## [1.45.2] - 2026-07-08

### Fixed — Character Studio pose/costume/emotion LoRA path

- VNCCS/Qwen task LoRAs (PoseStudio/ClothesCore/EmotionCore) live in a `models/loras` **subfolder** on
  the worker, so ComfyUI lists them WITH the prefix — a bare filename failed with
  `value_not_in_list` (400 from /prompt), breaking pose (and costume/emotion) generation on the Qwen
  engine. The dispatcher now prepends a configurable subfolder (`AppSettings.cs_qie_lora_subdir`,
  default `qwen/VNCCS/`) to the bare task-LoRA name at dispatch (skipped if the name already has a
  path). Migration adds the column; STUDIO_QIE_EDIT.json fallback default updated too. Override the
  column if your worker lists the LoRAs with a different prefix / slash direction.

## [1.45.1] - 2026-07-08

### Fixed — Talkie audit fix-wave

- **Talkie had no working video entry point** (BLOCKING): the frontend + backend start-image guards
  rejected Talkie scenes (which have no per-scene image). Both now bypass the guard for Talkie when a
  portrait is set (the dispatcher injects it as the source).
- **Autogen** no longer generates a throwaway per-scene image for Talkie scenes (would hang on
  lip-sync-only workers and override the portrait); FF is treated as satisfied. The dispatcher now
  ALWAYS forces the portrait as the source for Talkie video jobs (overrides any per-scene image).
- `prepare_lipsync_workflow` warns loudly when the portrait/audio can't be wired (empty or unmatched
  node title) instead of silently rendering an empty clip.
- Simplified the Sonic capability matcher; SceneEditor hides the Stems tab for Talkie; AudioSetup uses
  narration labels (Script) for Talkie.
- Talkie video-gen guard now shows a Talkie-specific message ('upload a portrait in Talkie Setup')
  when no portrait is set. Corrected LatentSync VRAM guidance in docs (~8-12 GB with optimizations;
  MuseTalk ~4 GB is the low-VRAM pick). LTX Talkie path audited clean end-to-end.

## [1.45.0] - 2026-07-08

### Added — Talkie mode (talking-head lip-sync)

- New project mode **Talkie**: upload one portrait + a narration, and each scene renders that
  stationary portrait lip-syncing the scene's narration slice into a talking-head clip. Reuses the
  narration pipeline (Whisper/AAF segmentation, per-scene audio slicing, subtitles,
  `assemble_narration_video`) — it's narration-like but video-producing.
- Four capability-routed lip-sync engines (auto-detected per worker):
  - **lipsync_ltx** — reuses your existing LTX-2.3 image+audio path (natural head motion). Works out
    of the box, no install. This is the default.
  - **lipsync_latentsync / lipsync_musetalk / lipsync_sonic** — dedicated stationary/expressive
    engines. Wired end-to-end (routing, capability detection, defensive title-based
    `prepare_lipsync_workflow`); drop your tested ComfyUI export at `workflows/LIPSYNC_LATENTSYNC.json`
    / `LIPSYNC_MUSETALK.json` / `LIPSYNC_SONIC.json` to activate (see docs/TALKIE_MODE.md).
- Dispatcher **Talkie routing**: any video job in a Talkie project is redirected to the chosen engine
  and gets the project's portrait injected as the source image (works for manual + autogen + batch).
- `PUT /api/projects/{id}/talkie-config` sets `portrait_asset_id` + `talkie_engine` (project.settings).
  Frontend: a **Talkie Setup** toolbar button (portrait upload + engine picker), mode option in
  project creation, and narration-like gating throughout. No DB migration.

## [1.44.0] - 2026-07-08

### Added — Generate Sample (poses & expressions from our own models)

- Tools → Pose Library, Pose Organizer, and Expression Library each get a **Generate Sample** button
  opening a modal where you pick a **prompt + model** (Z-Image / Krea2 / Anima / Klein) + **count**
  (1–8) + size, with an **Isolate subject** toggle that auto-appends the right framing directives
  (full-body, plain background for poses; head-and-shoulders for expressions) plus sensible negatives.
- Results stream into a **grid gallery** (click any tile to view large in a lightbox); multi-select the
  ones you want, add a **category + name + tags**, and commit to the library — no need to source a
  reference elsewhere.
  - **Poses** run the chosen image(s) through **DWPose** on a worker to extract real editable keypoints
    (auto-tagged + deduped), stored canonically like the rest of the Pose Library.
  - **Expressions** store the chosen crop as the entry's reference image + a natural-language prompt
    (defaults to your gen prompt) the emotion engines consume.
- Backend (`backend/api/tools.py`): `POST /api/tools/sample/generate` (background task, per-model t2i
  via the existing prepare_* workflows), `GET /sample/{id}` status polling, `GET /sample/{id}/image/{name}`,
  `POST /sample/{id}/commit` (pose→keypoints, expression→reference), and `GET /expression-library/{id}/thumbnail`.
- Expression Library rows now show a thumbnail when an entry has a reference image.

## [1.43.0] - 2026-07-08

### Added — Mobile Mode (touch-first)

- New **Mobile Mode** card on the home page opens a dedicated touch-first app at `/mobile`, built for
  phone/tablet use over the LAN (relative `/api` base + existing viewport meta — works as-is).
- **Projects** (`/mobile`): tappable project list. **Project shell** with a bottom tab bar
  (Overview · Scenes · Cast · Queue) and large tap targets throughout.
- **Overview** (`/mobile/p/:id`): scene/asset counts, quick-nav tiles (incl. Storyboard), and an
  **Auto-Generate** bottom sheet (all 7 pipeline modes) driving `startSequentialAutoGen` with a live
  progress bar, current scene/step, Stop, and a link into batch details.
- **Scenes** (`/mobile/p/:id/scenes`): per-scene cards with First/Last frame thumbnails (+version
  count), lyric/narration text, and audio playback. Tapping a frame opens the shared regen modal
  (version strip + active selector + prompt/refs/model/seed/two-pass generate) reused from Storyboard.
- **Cast** (`/mobile/p/:id/characters`): create/edit characters (name+description via the concept),
  generate their base image (`generateCharacterImage`, polled), version strip with set-active + delete,
  and character delete.
- **Queue** (`/mobile/p/:id/queue`): live generation jobs (from the global SSE store, seeded via
  `getJobs`) with progress + cancel/retry/delete, inline auto-gen status, and batch-run cards.
- **Batch detail** (`/mobile/batch/:id`): live progress, per-worker `active_jobs` render %, latest
  asset preview, activity feed, error log, and resume/cancel.
- Shared pieces: `MobileShell` (header + bottom tabs), `MobileSheet` (bottom sheet), `useProjectData`
  (loads + hydrates the store for standalone routes, mirroring StoryboardPage).

## [1.42.0] - 2026-07-08

### Added — Storyboard Mode

- New **Storyboard Mode** button in the per-project toolbar opens a full-window, ComfyUI-style
  zoomable/pannable canvas (`/project/:id/storyboard`) showing every scene left-to-right.
- Each scene card shows the **First Frame** and **Last Frame** active images (with a version-count
  badge), the scene name, its lyric/narration text (`scene.parameters.lyrics`, with a word-timing
  fallback), and a **play/pause** control for the scene's sliced audio (`audio_clip_path`).
- Clicking a frame opens a regen modal: large preview with version cycling, a **version strip with an
  active-state selector** (Set Active writes `chosen_image_path`/`chosen_last_frame_path`) and
  per-version delete, a First/Last frame toggle, and a generate form (editable prompt + Enhance,
  ReferenceSelector, workflow/custom-model dropdown, seed, two-pass) that dispatches via the existing
  `generate/image` endpoint. Everything is a live reflection of the timeline data — no new backend.
- Canvas: wheel-to-zoom (cursor-anchored), drag-to-pan, zoom in/out/reset controls; live per-scene
  “Rendering” badges and auto-refresh driven by the job SSE stream.

## [1.41.1] - 2026-07-08

### Fixed — audit fix-wave (full front/back review of 1.30.1→1.41.0)

- **Base render never showed progress for Restyle / Advanced / any out-of-band base job.** The
  status endpoint forced `base.status="done"` whenever an old base asset resolved, so a freshly
  queued base job (restyle, advanced, controlnet) was invisible and nothing polled for it. Now
  reports `running` when the newest base job is pending/running regardless of an existing asset,
  so both the parent poll and the sheet self-poll pick up completion and the new version appears.
- **NSFW toggle only applied after a Save.** Quick "Generate Base Render" read the persisted
  `character_info.nsfw`, ignoring an unsaved toggle. `GenerateBaseIn` now accepts an optional
  `nsfw` override and the sheet passes the live toggle value.
- **Anima base jobs weren't model-routed on multi-worker fleets.** `_get_required_models` omitted
  `anima_t2i/i2i/inpaint/controlnet`, so they fell through to no constraint and could dispatch to a
  worker lacking the Anima model. Now constrained to the single-image-generator model slot.
- **Pose Organizer only loaded/committed the first 240 candidates.** Status poll now requests the
  full candidate set (backend-capped) so select-all / commit cover every scanned pose.
- **Pose Organizer poll hammered the endpoint after a hard error.** The poll now clears its interval
  in the catch path (e.g. scan row gone) instead of retrying every 1.5s forever.
- **Scan summary `sample_only` counter always read 0** (candidates were tagged `"sample"`); the
  source-type tag now matches the counter key.

## [1.41.0] - 2026-07-08

### Added — Anima Ultra pipeline (FaceDetailer + Ultimate SD Upscale)

- Derived `ANIMA_T2I_ULTRA.json` / `ANIMA_I2I_ULTRA.json` / `ANIMA_INPAINT_CN.json` from the source
  ultra workflows: FaceDetailer/EditDetailer face-hand-eye refinement + Ultimate SD upscale. Each is
  reduced to a single final output (the fully detailed+upscaled image) with dynamic inputs cleared
  and parameterized (prompt/negative/seed/dims/image via `prepare_anima_workflow`).
- New `app_settings.anima_ultra` (default on) + a Settings toggle (shown when Anima is the
  generator). When on, the Anima t2i redirect and the i2i/inpaint dispatch prefer the ULTRA/CN
  workflows; off falls back to the clean cores.
- Requires Impact-Pack + UltimateSDUpscale nodes and the detector/SAM/upscale models on the worker
  (see README). User confirmed all Anima nodes/models are installed.

## [1.40.0] - 2026-07-08

### Added — Anima LLLite ControlNet

- `ANIMA_CONTROLNET.json` + `anima_controlnet` workflow type: control-guided Anima generation via
  the `AnimaLLLiteApply` node (wraps the model with an LLLite controlnet + a control-hint image).
  `prepare_anima_workflow` now parameterizes `lllite_name`/`strength` and the control/seed nodes.
- Usable from **Create Custom Base (Advanced)**: upload a control image (pose skeleton, depth, …),
  pick the LLLite model (Pose / Inpainting), and generate — lands as a base version. Pairs
  naturally with a pose skeleton exported from the Pose Library.
- Requires the `AnimaLLLiteApply` node + LLLite models (`anima-lllite-pose-1.safetensors`,
  `anima-lllite-inpainting-v1.safetensors`) on the worker. The reference "ultra" FaceDetailer /
  UltimateSDUpscale pipeline remains a documented, opt-in follow-up (many extra detector/upscale
  models).

## [1.39.0] - 2026-07-08

### Added — Anima anime base model (t2i first-pass + img2img + inpaint)

- **Anima** is now selectable as the Single Image Generator (Settings + Character Studio base model
  dropdowns). Qwen-VAE + Qwen-0.6B-CLIP anime base, turbo sampling (er_sde/simple, 12 steps, cfg 1).
- Bundled clean workflows `ANIMA_T2I.json` / `ANIMA_I2I.json` / `ANIMA_INPAINT.json`;
  `prepare_anima_workflow` parameterizes prompt/negative/dims/seed/image/denoise.
- Dispatch: `klein_t2i` redirects to Anima when the generator is `anima`; new `anima_i2i` and
  `anima_inpaint` workflow types (source image via reference/masked asset).
- **Anima prompt enhancer**: dedicated system prompt (quality tags → subject tags → optional
  @artist → anime tags → natural-language scene) so LLM enhancement outputs Anima-format prompts;
  a strong default anime negative is baked into the workflows.
- Requires the Anima models on the worker (see README) — core ComfyUI nodes + rgthree Power Lora
  Loader only; a recent ComfyUI with the `er_sde` sampler. The reference "ultra" pipeline
  (FaceDetailer/UltimateSDUpscale/AnimaLLLite controlnet) is documented but not yet built in.

## [1.38.0] - 2026-07-07

### Added — per-character SFW/NSFW base + clothing-from-reference costumes

- **Per-character SFW/NSFW toggle** (Sheet tab, default SFW; stored in `character_info.nsfw`).
  Mirrors VNCCS: the base render's clothing default becomes underwear (SFW:
  `bare chest, wear white boxers` / `wear white bra and panties`) or nude (NSFW:
  `naked, nude, ...`) so costumes layer over a clothing-ready body. Overrides the global
  `krea2_sfw_mode` per character — base jobs pass `krea2_sfw_override`, the dispatcher honors it
  over the global (NSFW → NSFW Krea2 workflow). Applies to generate-base, generate-all, and the
  advanced generator.
- **Clothing reference image on costumes**: a costume can carry a garment reference image
  (`reference_asset_id`). When set, generation dresses the character (image 1) in the exact
  clothing from the reference (image 2) via the edit models — `klein_2ref` (Klein) or
  `studio_qie_edit` w/ ClothesCore (Qwen) — so one outfit can be applied across characters, or a
  garment generated elsewhere swapped in. Upload it in the costume form; overrides the text fields.

## [1.37.0] - 2026-07-07

### Added — Create Custom Base Image (Advanced)

- New **Create Custom Base (Advanced)** button on the Sheet tab opens a modal to build a base
  render freehand: write a prompt, **LLM-enhance** it (uses the character sheet as context via the
  app's PromptEnhancer + configured LLM), add up to 5 **reference images** (→ Klein `klein_Nref`
  edit; none → text-to-image), and pick the first-pass **model**.
- Endpoints: `POST /characters/{id}/enhance-base-prompt` (returns the optimized prompt for review)
  and `POST /characters/{id}/generate-base-advanced` (dispatches the render).
- Results land as a new **base version** and auto-activate, so you can freehand-experiment or
  define exactly what you want and let the LLM write the optimal prompt.

## [1.36.0] - 2026-07-07

### Added — Base image editor: versions + restyle (Character Studio Sheet tab)

- The base render now keeps **versions**. Every generate + restyle is recorded, uploads add
  themselves, and `/status` returns `base.versions` + `base.active_asset_id`.
- New **Edit / Versions** button on the base render opens a modal: view any version in a lightbox,
  and click a version to make it the **active base** (`POST /characters/{id}/base-versions/set-active`
  updates the scene's chosen_image_path, so the whole pipeline edits from it).
- **Restyle** (`POST /characters/{id}/restyle-base`) redraws the current base with the Klein edit
  model, keeping the character/pose/composition and only changing the art style. Style source:
  the character's style, a custom style, a **reference image** (klein_2ref art-style match), or a
  **video project's** `style_text`. The restyle lands as a new version and auto-activates. Ideal
  for an uploaded character photo that needs a specific look.

## [1.35.1] - 2026-07-07

### Fixed — DWPose extraction node contract (verified against comfyui_controlnet_aux source)

- Nodes are **`DWPreprocessor`** ("DWPose Estimator") → **`SavePoseKpsAsJsonFile`** ("Save Pose
  Keypoints", forces execution) + core `LoadImage`. Keypoints are read from `DWPreprocessor`'s
  `ui.openpose_json` in `/history`.
- Bug fix: `openpose_json` is `[json.dumps([frame_dict, ...])]` (a JSON **array of frames**); the
  parser now flattens that array instead of handing a list to the single-pose converter.
- Workflow now uses the node's documented defaults (`bbox_detector=yolox_l.onnx`,
  `pose_estimator=dw-ll_ucoco_384_bs5.torchscript.pt`, `scale_stick_for_xinsr_cn=disable`).

## [1.35.0] - 2026-07-07

### Added — Tools Phase 2: DWPose image→pose extraction + HD mannequin thumbnails

- **Extract poses from images (DWPose)**: the Pose Organizer can now turn arbitrary images —
  photos, character art, downloaded skeleton/mannequin renders — into real editable keypoints via
  a GPU worker. Runs DWPose (`comfyui_controlnet_aux` `DWPreprocessor`), reads the OpenPose
  keypoints back from the worker's `/history`, converts to the 18-joint schema, auto-tags, and adds
  to the library. New `dwpose` worker capability (auto-detected). Requires `comfyui_controlnet_aux`
  on a GPU ComfyUI worker.
- **HD mannequin thumbnails**: render a clean grey artist-mannequin thumbnail per library pose via
  the Klein RefControl Pose LoRA (image 1 = OpenPose skeleton, image 2 = 2D schematic mannequin) —
  select poses → HD thumbnails. Reuses the existing Klein worker + `refcontrol_v2_poses.safetensors`;
  no new nodes.
- `GET /api/tools/capabilities` (dwpose/klein); the UI gates both actions on worker availability.

  Note: both flows call the worker directly (upload → queue → poll `/history` → download) and need
  first-run validation on your GPU worker — in particular the `DWPreprocessor` widget names and the
  direct Klein render. The instant 2D mannequin thumbnail remains the default; HD is opt-in.

## [1.34.0] - 2026-07-07

### Added — Tools section: Pose Library + Pose Organizer + Expression Library

New **Tools** main section (Home → Tools, `/tools`) — a reusable asset-library system.

**Pose Organizer**
- Scan a **server folder path** or an uploaded **`.zip`** of pose files (background scan, poll for
  progress). Each file is classified (heuristics: keypoints / openpose-image / depth / natural),
  OpenPose keypoints converted to the VNCCS 18-joint schema, **auto-tagged from geometry**
  (front/back/profile, standing/sitting/crouching/lying, arms up/down), and **deduped** by pose
  shape (cross-scan + against the library). Paired sample images are used as free thumbnails;
  otherwise a 2D mannequin thumbnail is rendered.
- Review grid with duplicate/img-only badges, lightbox, bulk select, category + extra tags, and
  commit-selected-to-library.

**Pose Library**
- Browse committed poses by category/tag/search with thumbnails + lightbox. Poses stored
  **canonically as keypoints** (re-renderable to any control format via `/pose-library/{id}/control`).
- **Send to Pose picker** (bridges selected poses into the Character Studio custom-pose store so
  they're pickable on a character's Poses tab), retag/recategorize, delete, and **export/import
  portable pose packs** (`.zip` of keypoints + tags + thumbnails + manifest).

**Expression Library**
- Reusable facial expressions as name + natural-language prompt (+ category/tags). **Import the
  bundled 157-emotion catalog** in one click, add your own, edit prompts inline, filter/search,
  delete.

**Also**
- Character Studio Poses tab: explicit **view** button on each card opens the pose in a lightbox
  without toggling selection.
- New tables `pose_library`, `expression_library`, `library_scans` (created on startup).
- Planned Phase 2 (scaffolding present): DWPose extraction from arbitrary reference images, and
  optional HD mannequin thumbnail renders via the pose LoRA.

## [1.33.0] - 2026-07-07

### Added — OpenPose import + working Klein pose transfer (RefControl LoRA)

**OpenPose → pose library converter**
- New `POST /pose-presets/import-openpose` (multipart) ingests OpenPose keypoint files as
  categorized pose presets. Accepts a single `.json` (one OpenPose object or an array) or a
  `.zip` of thousands. Auto-detects **BODY_25** vs **COCO-18**, remaps to the VNCCS 18-joint
  schema, and scales each pose into the 512x1536 canvas (aspect preserved, centered).
- Poses tab gains an **Import OpenPose** button (categorizes by filename).

**Klein RefControl Pose LoRA — real pose transfer without the VNCCS worker**
- Pose control images now render as the standard **colored OpenPose skeleton on black** (what
  pose LoRAs actually consume) instead of the mannequin schematic; the mannequin stays as the
  browsable library thumbnail.
- The Klein pose path now uses the RefControl Pose LoRA when configured: image 1 = pose
  skeleton, image 2 = identity, trigger `apply pose from image 1 with reference from image 2`,
  LoRA enabled in the existing rgthree Power Lora Loader node. New `app_settings.cs_klein_pose_lora`
  column (default `refcontrol_v2_poses.safetensors`; empty disables → weak 2-ref fallback).
- Requires `refcontrol_v2_poses.safetensors` present on each Klein worker (FLUX.2 Klein Base 9B).

### Docs

- Updated README (pose LoRA in the Klein LoRA table + a Character Studio requirements block:
  Qwen/VNCCS, FaceDetailer, SeedVR2, RMBG2, Ollama models), `docs/CHARACTER_STUDIO.md`
  (pose-control render, RefControl path, requirements checklist), `docs/CHARACTER_STUDIO_P2_API.md`
  (1.30.1→1.33.0 endpoint addendum), and `HANDOVER_PROMPT.md` (→ v1.33.0 current state).

## [1.32.2] - 2026-07-07

### Added — click-to-lightbox on all Character Studio thumbnails

- Emotions tab (full result + face crop), Poses tab (rendered pose result), Process panel
  (cutout/upscale results), and Dataset caption thumbnails now open a full-size lightbox on
  click, matching the base render / Renders / Costumes tabs. Pose preset thumbnails keep their
  click-to-select behavior (only the rendered result opens the lightbox).

## [1.32.1] - 2026-07-07

### Fixed — emotions-from-costume: "has no rendered base sprite yet" on a rendered costume

- The emotion endpoint resolved the costume source only via the sprite's `image_rel` (rel-path
  lookup). It now prefers the sprite's `asset_id` — which is always set once the costume renders
  (it's what draws the thumbnail) — and falls back to `image_rel`, so a successfully rendered
  costume is reliably found. Clearer messages distinguish "costume not found" from "not rendered
  yet."

## [1.32.0] - 2026-07-07

### Added / Fixed — Character Studio pose & consistency follow-ups

- **Renders tab lightbox**: clicking a shot thumbnail now opens the full-size image in a lightbox
  (was opening a new browser tab).
- **Klein emotions no longer hard-fail on anime**: when YuNet/Haar detect no face, the emotion
  mask now falls back to a heuristic upper-center face region (method="heuristic") so
  `klein_inpaint` can run instead of erroring. Qwen is still recommended (no detection needed).
- **Engine guidance**: Poses tab warns when the engine isn't Qwen (Klein has no pose-control and
  reproduces the base pose); Emotions tab warns when using Klein (drift + face-detection reliance).
- **Identity-lock prompts**: pose/costume/emotion/shot edit instructions now append an identity
  clause ("BOTH eyes the SAME color, same features/hair/skin — no drift") to reduce Klein's
  color/identity blending (e.g. mismatched eye colors).
- **Pose library import + categories**: new `POST /pose-presets/import` ingests a VNCCS poseset
  JSON (or a flat pose list) as categorized custom presets; the Poses tab gains an Import button
  and a category filter. `/pose-presets` now returns a `category` per preset.

## [1.31.3] - 2026-07-07

### Fixed — pose refs looked like wireframes; costume images now open a lightbox

- Pose control/thumbnail images composited the light peach body ovals (`#FFE5D9`) onto a **white**
  background, so the mannequin body vanished and only the dark skeleton lines + red joint dots
  showed — reading as a wireframe. The renderer now uses a neutral gray backdrop and turns the
  skeleton overlay OFF by default, so poses render as a solid mannequin figure (VNCCS look).
  Thumbnail cache now also invalidates when the renderer changes.
- Costume sprite images are clickable to open a full-size lightbox (new shared `ImageLightbox`
  in p2Shared, reusable across studio tabs).

## [1.31.2] - 2026-07-07

### Fixed — pose / costume generation failed with "invalid dimensions width=0, height=0"

- The Klein-engine pose (`klein_2ref`) and costume (`klein_1ref`) param builders never set
  `width`/`height`, so the dispatcher's dimension guard refused every job. They now take
  `width`/`height` (from the project's image resolution, falling back to `target_size`), passed
  from the pose/costume endpoints and the Generate-All flow.
- The input-derived studio workflows (`studio_qie_edit`, `studio_rmbg2`, `studio_upscale`,
  `studio_seedvr2`, `studio_facedetailer`) are now exempt from the width/height guard, like
  `klein_inpaint` — they compute output dimensions from the input image / target_size, not from
  params. This unblocks the Qwen engine and the Process (cutout/upscale) stages too.

## [1.31.1] - 2026-07-07

### Fixed — saving the Identity section wiped all fields, then crashed

- `PATCH /characters/{id}` returned `{"ok": true}` instead of the updated character. The Sheet
  tab fed that straight into its character state, so `character.name` became undefined (blanking
  every field), and the next Save called `undefined.trim()` → "Cannot read properties of undefined
  (reading 'trim')". The endpoint now returns the full character via `_char_out` (matching
  create/get). Added a frontend guard so a malformed response can never clobber the form.
  Note: the DB rows were never actually cleared — the second save threw before calling the API —
  so reloading a character shows its data intact.

## [1.31.0] - 2026-07-07

### Added — selectable character art style (not just anime)

- New **art-style registry** (`STUDIO_STYLES` in service.py): Anime/Visual Novel (default),
  Semi-realistic, Photorealistic, 3D render, Western comic, Storybook illustration — plus custom
  free-text (used verbatim as the style descriptor, so it's never a hard limit). Mirrored on the
  frontend in `characterStudioStyles.tsx` with a reusable `StyleSelect` dropdown.
- `build_base_prompt` is now style-aware: subject tokens switch between danbooru tags
  (`1girl`/`1boy`, anime-family) and natural language (`a woman`/`a man`, realistic), and the
  style descriptor is appended. `build_caption_prompt` tag-mode subject follows the same rule.
  Everything downstream (shots/poses/costumes/emotions) edits from the base image, so it inherits
  the style automatically.
- Style is stored per-character in `character_info.style` (no migration) and selectable in the
  **Sheet tab** and **New Character** form. New characters pre-fill from their Story's default.
- **Story-level default**: new `studio_stories.default_style` column (idempotent migration) with a
  Default-style picker on the New Story form — set a whole project's look once.
- The **Wizard** and **Clone-from-image** tag-sheet generators are style-aware: their Ollama
  system prompt says "professional {style} character designer" so the extracted sheet matches the
  target look. `GET /character-studio/styles` exposes the canonical presets.

## [1.30.4] - 2026-07-07

### Fixed — base render feedback + reliable preview refresh (Sheet tab)

- Clicking **Generate Base Render** now shows a prominent status banner ("Rendering base image
  on a worker… (Ns)") with a live elapsed-seconds counter, plus the button switches to
  "Rendering…". Previously the only cue was a tiny spinner in the preview box, so it was hard to
  tell anything was happening.
- The preview now reliably updates when the render finishes. The Sheet tab drives its own
  completion poll (`onStatusRefresh` every 2.5s) the moment a render is submitted, independent of
  the parent status-poll chain, and clears itself as soon as the image lands or the render fails
  (soft-capped at 10 min). Fixes the case where a finished base render didn't appear until a
  manual page refresh.

## [1.30.3] - 2026-07-07

### Added — Clone character from reference image (Sheet tab)

- New **Clone from image** button beside the Wizard in the Character Info header. Upload a
  reference character image; the Ollama vision model describes it and the character wizard
  extracts a full tag sheet (sex/age/race/skin/body/face/hair/eyes/details) that auto-fills the
  editable fields — NVCCS clone-character style. Surfaces the existing `POST /wizards/clone`
  endpoint (previously backend-only). Shows an elapsed timer + staged status while the vision
  model runs. Requires Ollama Vision (Settings → Vision) + text model (Settings → LLM).
- Wizard and Clone now share one `applyTagSheet` merge helper; Clone also fills the Description
  field from the vision description when empty.

## [1.30.2] - 2026-07-07

### Added — Character Studio base-render controls (Sheet tab)

- **Click-to-lightbox**: click the base render to view it full-size in an overlay.
- **Per-render model dropdown**: pick the first-pass model for a single base render
  (Z-Image Turbo / Krea 2 Turbo / FLUX.2 Klein T2I), defaulting to the configured First
  Frame model (Settings → `single_image_generator`). Wired through `generate-base`
  (`model` field) → job param `single_image_generator_override` → dispatcher
  `_try_zimage_redirect` honors it per-job without changing the global default.
- **Upload-image-as-base** (NVCCS import style): upload any image; it's stored as a
  studio-project asset and the new `POST /characters/{id}/set-base` points the scene's
  `chosen_image_path` at it, so the whole downstream pipeline (shots/poses/costumes/
  emotions/process/dataset) edits from it exactly as a rendered base would.

## [1.30.1] - 2026-07-07

### Fixed — Character Studio base render never surfaced failure / idle state

- `/characters/{id}/status` now reconciles the newest `studio_shot_id=="base"` job and returns
  `base.status` (idle/pending/running/failed) + `base.error`. Previously base state was inferred
  solely from `chosen_image_path`, so a failed or never-started render was indistinguishable from a
  running one — the preview spun "Rendering…" forever. The Sheet tab now shows a Render-failed
  state with the error and a "Retry Base Render" button, and polling keys off the real status.

### Changed — quieter optional-engine probes

- New `ComfyDispatcher.has_capability(cap)` non-logging probe. Character Studio's `_worker_online`
  availability checks (vnccs / seedvr2 / impact) route through it instead of `select_worker`, so the
  dispatcher no longer logs a `No workers available with required capabilities` WARNING every poll
  when those optional engines are simply absent from the pool. Real dispatch still warns.

### Added — engine-availability chips + Tag Sheet live status

- Preflight now reports `klein_online` / `qwen_online` / `impact_online`; the detail-header
  PreflightBadges render explicit Klein / Qwen (VNCCS) / FaceDetailer chips so it's obvious which
  engines the current workers support.
- Character Wizard "Generate Tag Sheet" button shows an elapsed-seconds timer + staged text
  ("Contacting Ollama…" → "Model is generating your tag sheet…") so the Ollama call never looks
  frozen. (Reminder: the wizard talks directly to the Ollama HTTP API in Settings → LLM, not
  ComfyUI — it needs `ollama_urls` + `ollama_model` reachable and the model pulled.)
- Describe-a-character auto-fill is now on the single-character **edit page** (Sheet tab → Character
  Info), not just the New Character modal. The same wizard merges the LLM tag sheet into the
  editable age/race/body/face/hair/eyes/etc. fields so you can regenerate attributes from a
  description at any time, then tweak and Save.

## [1.30.0] - 2026-07-06

### Added — third emotion engine: FaceDetailer (VNCCS's exact mechanism)

- `STUDIO_FACEDETAILER.json`: YOLOv8 face bbox + SAM mask → face-crop re-render at guide/max 1536
  with the EmotionCore LoRA on QIE-2511 Lightning (denoise 0.55, feather 5, force-inpaint,
  noise-mask-feather 20 — VNCCS's proven recipe), feather-pasted back. Best for small faces in
  full-body sprites (the crop-upscale advantage the whole-image edit lacks).
- Requires Impact-Pack + Impact-Subpack on the VNCCS worker (new auto-detected `impact`
  capability) plus `bbox/face_yolov8m.pt` and `sam_vit_b_01ec64.pth`. Engine resolution rejects it
  with an actionable 409 when unavailable; preflight reports `facedetailer_online`.
- Emotions tab gains a per-tab engine override select (follow page / Qwen whole-image edit /
  Klein face-mask inpaint / FaceDetailer face-crop re-render) so all three are A/B-testable.

## [1.29.1] - 2026-07-06

### Fixed — Character Studio deep audit (3 adversarial reviews before first testing)

- **BLOCKING — pushed characters had zero working refs**: `push_to_project` (and the Global
  Library's `import_to_project` — same latent bug) copied image files but never created Asset
  rows; every generation-path resolver matches characters against Asset rows, so pushed/imported
  characters looked fine in the UI but silently attached no reference images. Both paths now
  register Asset rows.
- **BLOCKING — `auto_save_preview` was a phantom on completion**: stage renders (poses, costumes,
  emotions, processing) would each overwrite the studio scene's chosen image — corrupting the
  identity source mid-pipeline. The completion handler now honors the flag.
- **BLOCKING — emotion/process refs mismatch**: costume-source emotions 400'd (UI sent the costume
  id as `source`), and Process never resolved `costume:`/`emotion:` refs. Backend now accepts both
  forms; UI sends canonical values.
- HIGH: missing `AssetType` import (NameError on pose/emotion/cutout registration); unguarded
  `select_worker` calls 500'd when zero workers were online (now degrade gracefully everywhere);
  emotion face crops get Asset rows + `face_crop_asset_id` (UI thumbnails now render); status
  polling now tracks cutout/upscale jobs; Concept-panel saves no longer strip Studio provenance
  from characters (re-push updates in place instead of duplicating).
- Full audit reports in `diagnostics/audit_studio_{backend,frontend,integration}.md` (gitignored);
  open MEDIUM/LOW items listed there.

## [1.29.0] - 2026-07-06

### Added — Character Studio: final gap closure (everything test-ready)

- **SeedVR2 premium upscale**: `STUDIO_SEEDVR2.json` (VNCCS-proven defaults: 2048/3840, lab color
  correction, tiled 1024/128) on workers with the SeedVR2 node pack (new auto-detected `seedvr2`
  capability). Upscale mode selector everywhere upscale runs (Process panel + Generate All):
  Auto (SeedVR2 when available, else GAN) / SeedVR2 / GAN; preflight reports both availabilities.
- **2D Pose Editor**: drag-the-joints skeleton editor (18-joint OpenPose layout on the 512×1536
  canvas, bone-aware, live server-rendered preview) — load any bundled preset as a starting point,
  save as reusable custom presets (with delete), and generate pose sets from customs exactly like
  built-ins. New endpoints: pose joints fetch, live preview render, custom preset CRUD.
- **Outfit catalog suggestions**: the 629-aesthetic VNCCS outfit catalog is now served by /catalogs
  and surfaced as a searchable picker in the costume builder (fills the prompt with the aesthetic's
  tag set, still fully editable).
- **YuNet auto-download**: the face-detection model fetches itself from the official opencv_zoo on
  first use (Klein-engine emotions get quality face masks out of the box; Haar remains the fallback
  if the download fails).

## [1.28.0] - 2026-07-06

### Added — Character Studio Phase 2: full VNCCS-parity pipeline with a dual engine (Qwen + Klein)

- **Dual-engine stages**: every edit stage (poses, costumes, emotions) runs on either engine —
  **qwen** (our own thin API graphs from VNCCS atomic nodes: Qwen-Image-Edit-2511 GGUF + Lightning +
  the VNCCS pose/clothes/emotion core LoRAs via `VNCCS_QWEN_Encoder`; requires a worker with VNCCS
  installed, auto-detected as a new `vnccs` capability) or **klein** (existing `klein_2ref`/
  `klein_1ref`/`klein_inpaint` workflows — zero new worker deps). Engine selector with `auto`
  fallback + per-character preflight validation (worker caps, base render, vision config) before
  anything dispatches.
- **Pose sets**: bundled VNCCS pose presets rendered server-side as 18-joint skeletons (ported 2D
  renderer; opencv with PIL fallback) → pose-conditioned sprites (qwen: pose LoRA + pose-as-canvas
  recipe; klein: skeleton-as-reference instruction).
- **Costumes**: per-character costume library (structured top/bottom/head/face/shoes + prompt),
  generated against the base identity (ClothesCore LoRA on qwen).
- **Emotions**: full 157-emotion catalog picker; qwen path edits via EmotionCore; klein path builds a
  CPU face-detected (YuNet→Haar fallback), feather-masked RGBA and runs our Klein inpaint —
  face crops saved separately for portrait dataset material.
- **Cutout & upscale**: transparent sprites via `STUDIO_RMBG2` on a VNCCS worker or CPU fallback
  (optional `rembg`, chroma-distance last resort — surfaced in preflight, never crashes); GAN
  upscale via standard nodes on any `upscale`-capable worker.
- **Generate All**: one-click orchestrator (base → shots → costumes → emotions → processing) with
  per-stage checkpoints, skip-and-record error handling, and live progress in the UI.
- **Wizards**: describe-in-prose → structured tag sheet (LLM), clone-from-image → tag sheet (vision).
- Datasets can now include costume/emotion/pose sprites and face crops. New workflows
  `STUDIO_QIE_EDIT/RMBG2/UPSCALE.json`; API contract in `docs/CHARACTER_STUDIO_P2_API.md`;
  35 new endpoints; frontend Poses/Costumes/Emotions tabs + engine selector + Generate All modal.
- New dependency: `opencv-python-headless` (CPU face detect + skeleton rendering).

## [1.27.0] - 2026-07-05

### Added — Character Studio (Phase 1)

- New app section (Home → 🎭 Character Studio, route `/studio`): create reusable characters (or
  items), organize them into **Stories** for series with recurring casts, and use them across
  projects. Design + phasing: `docs/CHARACTER_STUDIO.md` (built after a full analysis of the VNCCS
  3.0 ComfyUI suite + LoRA-dataset research; VNCCS catalogs — tags/outfits/157 emotions/pose
  presets — are bundled under `backend/data/character_studio/`; the `vnccs/` reference folder is
  gitignored).
- **Character sheet → base render → shot plan**: VNCCS-style tag sheet builds the base prompt
  (first-pass generator); an editable research-backed shot plan (angles incl. profile+back,
  portrait/upper/full framings, expressions, background/lighting variation — item mode swaps in
  context/detail shots) renders each shot as a Klein "Image 1" edit of the base render. All
  generation runs through the normal job queue inside a hidden system project (one scene per
  character), so the existing dispatcher/lightbox/versions machinery applies. The hidden project
  is filtered from the project list.
- **LoRA dataset builder ("idiot-proof")**: pick renders → auto-caption on the existing Ollama
  vision pool with per-style templates (trigger-word-first, prune-constant-traits rule, SDXL
  quality prefix branched by Illustrious/NoobAI/Pony) → review/edit every caption in the UI →
  export **kohya** (`N_trigger class` folder + captions + dataset_config.toml) and/or
  **ai-toolkit** (flat images + natural-language captions + trigger_word config.yaml skeleton),
  zipped with per-format READMEs.
- **Push to Project**: copies the base render + best angle shots into a target project and
  adds/updates the character in `settings.characters` with `extra_images` angles (re-push updates
  in place via the studio id).
- New tables `studio_stories` / `studio_characters` / `studio_datasets` (auto-created);
  `caption_image_sync` (custom-prompt vision call) in the vision service.
- Phase 2 (planned, per docs): VNCCS-quality upgrades as our own thin graphs on the VNCCS-equipped
  worker — pose-conditioned sprite sets, costumes, emotions (FaceDetailer), transparent sprites
  (RMBG2/ChromaKey), SeedVR2 upscale toggle. Phase 3: clone-from-image, training handoff.

## [1.26.0] - 2026-07-05

### Changed — FF/LF Keyframes mode now runs as three parallel phases

- The Keyframes mode was shipped on the sequential runner (FF → LF → video, one scene at a time),
  which left N−1 workers idle — but unlike Chaining, this mode has ZERO cross-scene dependency.
  Reworked as a phased orchestrator on the windowed-batch machinery: **Phase 1 renders every scene's
  first frame across all workers, Phase 2 every last frame, Phase 3 every FF→LF video.** In-scene
  ordering is preserved by the phase boundaries; a drain barrier between Phases 1→2 waits out
  two-pass composite follow-on jobs and applies the base-image fallback where a composite failed.
- New internal windowed modes `kf_last_frames` (LF images w/ FF continuity ref + slot reservation)
  and `kf_videos_fflf` (ltx_fflf videos; scenes missing either keyframe are skipped with a BatchRun
  error entry instead of failing the run). `_run_windowed_batch` gained `finalize=False` so phases
  don't prematurely mark the run done. Per-scene failures skip-and-continue like other windowed modes.
- The serial per-scene branch is removed; the 1.25.1 two-pass FF guards remain on the chaining/V2V
  sequential paths. UI description updated to describe the phased behavior.

## [1.25.1] - 2026-07-05

### Fixed — FF/LF Keyframes run died on the first two-pass scene

- Root cause (spotted via `rbmn jobs`: two Klein jobs created 8s apart on the failing scene): two-pass
  FF generation completes in TWO jobs — the awaited Pass-1 (base) job spawns a Pass-2 (composite) job
  at completion, and only Pass 2 sets `chosen_image_path`. All sequential auto-gen FF steps waited on
  the Pass-1 row only, then raced ahead → `ltx_fflf` dispatched with no first frame ("LTX video
  workflow requires a first frame image"). Char-less scenes downgrade to single-pass, which is why the
  first few scenes worked.
- New `_await_scene_first_frame`: after every sequential FF wait (keyframes mode + chaining scene-1 +
  chaining fallback + v2v FF + defensive single branch), poll until the scene REALLY has a first frame.
  If the composite FAILED, falls back to the Pass-1 base image (loud warning, `two_pass_composite_failed`
  kept for the UI) so the run continues instead of dying. Also fixes the LF job silently rendering
  without its FF continuity ref in the same race window.
- Keyframes step 3: 30s grace poll on `chosen_last_frame_path` instead of an instant hard fail.
- `rbmn jobs` now shows scene number/name and frame-type/two-pass phase per job.

## [1.25.0] - 2026-07-04

### Added — Auto-Gen mode: Full Pipeline — FF/LF Keyframes (Independent)

- New sequential auto-gen mode `all_video_fflf_keyframes`: for each scene independently, generate a
  First-Frame image AND a Last-Frame image (two keyframes of one continuous shot), then render the
  video with the true FF→LF interpolation workflow (`ltx_fflf`). Unlike "FF/LF Chaining" (which
  chains the previous VIDEO's last frame into the next scene and renders plain I2V), no cross-scene
  coupling — each scene stands on its own keyframes. Two-pass, character refs, FF-as-LF-continuity-ref
  (slot 1, opt-out respected), override/skip-existing, lipsync and chapter scoping all behave like the
  sibling modes.
- **Keyframe-aware prompting** (applies to ALL FF/LF scenes, auto + manual, prose + Video-JSON):
  - Video enhance context: new KEYFRAME INTERPOLATION block — clip starts/ends EXACTLY on the two
    keyframes; write ONE continuous shot bridging them (subject motion + camera move), no content
    absent from both frames, no cuts/location changes; includes both keyframe prompts as anchors.
    Replaces the previous one-line mention. Flows into Video-JSON lazy builds automatically.
  - FF image context: KEYFRAME PAIR block — compose keyframe A so the end state is reachable; keep
    setting/lighting/wardrobe stable, put the energy in pose/action; cites the LF prompt when present.
  - LF image context: reachability rules — exact final frame via continuous motion over the scene's
    real duration, no teleports, but advance the action DECISIVELY (near-identical endpoints = static clip).
  - VIDEO + NARRATION_VIDEO system prompts: FFLF section (single segment always — multi-segment fights
    fixed endpoints; prompt supplies the JOURNEY between decided compositions; duration-scaled pacing).
  - VIDEO_JSON system prompt: FFLF rule (subject.action = journey between keyframes, camera bridges the
    two compositions, preserve_from_input_image = constants across the whole clip).
- Note: LTX Director-node path deliberately NOT used here (its workflow is still an unvalidated
  draft); Video-JSON mode keeps working via the project toggle since `ltx_fflf` is in its gate.

## [1.24.1] - 2026-07-04

### Added — unified CLI troubleshooting suite (`tools/rbmn.py`)

- One entry point for every debugging task: `projects` / `project` / `prompts` / `jobs` / `db`
  (read-only DB inspection), `audio` / `timeline` / `chapters` / `general` / `aaf` (wrap the existing
  diag scripts), `media` (ffprobe + content fingerprint), `logs` (tail `logs/rbmn.log`), and live
  backend commands `health` / `api` (raw escape hatch to any endpoint) / `slice` / `detach-aaf`
  (port auto-read from `app_settings.app_port`).
- **Every command mirrors its output to `diagnostics/latest_<cmd>.txt`** (+ timestamped copy) inside
  the repo, so debugging no longer requires copy/pasting terminal output — the assistant reads the
  reports (and the backend log) directly from the repo folder. `diagnostics/` is gitignored.
- Full documentation with symptom→command recipes: `docs/CLI_TOOLS.md`.

### Fixed

- Auto-Gen panel showed literal `\u2014` in "Override — Regenerate Full Set" / "Use Existing
  Prompts — Just Render" labels: `\uXXXX` escapes are only interpreted inside JS string literals,
  not in JSX text content. Replaced with real em-dash characters (3 sites in AppLayout.tsx).

## [1.24.0] - 2026-07-01

### Fixed — stale-master accumulation, unordered master lookups, project delete

- Lorenzo's `diag_audio` run exposed the compounding data state: repeated AAF imports **accumulated
  master asset rows** (cleanup only deleted one), and several master lookups used **unordered
  `.first()`** — the slicer could pick a stale master from an earlier run (clip filenames carried a
  version stamp 7h older than the newest master). Now: re-import purges **every** prior AAF-extracted
  master (rows + files), and all master queries (bulk/single/dispatch auto-slice + job-media fallback)
  order newest-first; the job-media fallback also excludes stems (desc order would have preferred a
  newer stem row).
- **Project deletion could fail with IntegrityError** on deep chapter trees (AAF imports auto-split
  into parent+sub chapters; ORM cascade order vs the chapters self-FK + scenes→chapters FK is not
  guaranteed with `PRAGMA foreign_keys=ON`). Delete now NULLs both FK chains first; directory removal
  is non-fatal (a locked file no longer resurrects a half-deleted project); the 500 detail includes
  the real exception instead of a blind "Failed to delete project".
- `tools/diag_audio.py` now honors the **`app_settings.project_dir` override** (DB and project files
  can live in different roots) and prints the project folder + `audio_clips/` contents, so
  "MISSING-FILE" verdicts distinguish wrong-root from genuinely-absent.

### Fixed — THE AAF audio root cause: essence resolver returned clip 1 for every scene

- **ElevenLabs AAFs use ONE MasterMob with N slots** (diag: `MasterMob: 1, SourceMob: 56`) — every
  timeline clip references the same MasterMob, distinguished only by `slot_id`. The extraction's
  essence resolver scanned the MasterMob for "the first SourceMob with essence" and therefore
  returned **clip 1's audio for all 56 timeline clips** — the reconstructed master was scene 1's
  audio stamped at every clip position, which then sliced into 56 identical-content clips. Exactly
  the observed "same waveform/audio on every scene".
- Fixed: essence now resolves **per clip through its own source-reference chain** (mob_id + slot_id
  at every hop via pyaaf2's `SourceClip.walk()`, source offsets accumulated); a clip whose chain
  fails is left silent with a loud log instead of borrowing another clip's audio.
- **Verified against a synthetic AAF with the exact ElevenLabs topology** (1 MasterMob / N slots /
  N SourceMobs): three distinct tones land in their own regions. The earlier test used one
  MasterMob per clip — the wrong topology — which is why it passed while real files failed.
- **New `tools/diag_audio.py`** — one command audits the whole chain from the DB out: master asset
  (codec/duration), every scene's clip (exists/codec/duration) plus a decoded-PCM content
  fingerprint that makes "all clips identical" undeniable, and a plain verdict.

### Fixed — AAF re-import now actually repairs a broken project (three compounding gaps)

- **Re-import replaces a previously AAF-extracted master** instead of silently reusing it — projects
  whose master was a stale artifact of older extraction code (e.g. the giant PCM WAV) kept the broken
  master forever, so re-importing changed nothing. User-uploaded masters are still reused untouched.
  Extracted masters also get a timestamped filename (new URL, no browser cache).
- **Per-scene clip filenames now embed the master's mtime** (all three slicers: bulk, single-scene,
  dispatch auto-slice). Names used to be identical across re-slices, so browsers kept serving the OLD
  cached clip files even after a correct re-slice — "like nothing ever happened."
- **"Re-slice audio" button added to the AAF panel** — the old re-slice control only rendered in the
  post-Whisper section, which AAF projects (correctly) never show, making the documented repair
  path unreachable. Clarified: AAF import/re-import slices automatically; Process Audio is never
  required for AAF projects.

### Fixed — per-scene audio clips were MP3-in-a-.wav-container (the "scene 1 over and over" bug)

- `slice_audio` used `-c:a copy`: with the (new) MP3 master it wrote **MP3 packets into a `.wav`
  container**. ffmpeg reads such files, but browsers and ComfyUI's LoadAudio mis-decode them — the
  timeline showed/played the same waveform for every scene and playback behaved as if only scene 1's
  clip existed. Verified by repro: `codec_name=mp3` inside `.wav` slices. Slices (and the backing-mix
  narration copy) now re-encode to REAL PCM whenever the output is `.wav`; non-wav outputs keep stream
  copy. Slice content itself was always positionally correct.
- **Repair for affected projects:** Re-slice Audio on the Audio tab (regenerates all per-scene clips
  as real PCM). Old WAV/MP3-upload projects were never affected (PCM master → copy produced real PCM).

### Fixed — AAF extracted audio now lands as MP3 + merge-cuts control for choppy AAF timelines

- **Extracted AAF audio is written as 192k MP3** instead of a reconstructed PCM WAV. A 7-minute
  reconstruction was 80-190 MB of WAV — the frontend's WebAudio decode choked on it (no waveform,
  broken timeline playback), while a ~10 MB MP3 behaves exactly like a normal upload. Extraction
  verified end-to-end again on the MP3 path (clips at exact positions, silent gaps).
- **"Merge cuts < N s" control in the AAF panel**: ElevenLabs often renders one clip per SENTENCE,
  which produces choppy scenes that split paragraphs at every sentence boundary. The new field feeds
  the importer's existing `min_scene_seconds` merge so close cut points collapse into one scene
  (0 = exact AAF cuts).
- `tools/diag_aaf.py` now prints a **per-track breakdown** (clips/fillers per Sound track) — a second
  audio track in the AAF cuts scenes at ITS clip starts too, which is the other cause of odd splits.

### Fixed — scene videos baked with the beginning of the FULL audio track

- The completion-time audio mux had a last-resort fallback that muxed the **entire master audio,
  unseeked**, onto a scene's video whenever the per-scene clip was missing and auto-slice failed —
  so every affected scene played the beginning of the whole track (observed on AAF-imported
  projects whose scenes predated the audio). The fallback now **slices the master to the scene's
  own time range inline**; a true full-master mux is only allowed for a scene starting at 0
  (positionally correct), and mid-timeline scenes keep model audio with a loud log telling you to
  re-slice + regenerate instead of shipping wrong audio. Master lookup also excludes Demucs stems.
- Already-rendered clips carry the wrong audio in their files: click **Re-slice Audio** (Audio tab)
  to (re)create per-scene clips, then regenerate the affected videos. Exports were never affected —
  the export assembler lays the master track over the timeline positionally.

### Changed — flow LLM casts characters by fit, not list order

- The story-flow prompt now directs the LLM like a **casting director**: for each scene, pick WHO
  belongs from the scene's lyrics/script content and each character's role/description — never default
  to the first characters in the list or reuse the same pair out of habit; character-free scenes are
  explicitly fine; spread the cast across the video where the story supports it. The character list is
  framed as a **CAST SHEET** at both prompt sites, and the LLM is told to name the scene's PRIMARY
  subject FIRST — mention order feeds reference-image priority (Klein weighs earlier slots more).
  The per-scene retry prompt carries the same casting rules.

### Fixed — auto-gen character references permanently locked off (empty-seed poisoning)

- Auto-gen seeds each scene's character pick into `image_refs_first` so the Image tab shows what was
  used. When the auto-pick found NOBODY (typical for a first run on fresh AAF-imported scenes, before
  flow/SRT text existed), it persisted an **empty** selection — which every later run then respected as
  the user's explicit "no characters" choice. Result: no scene ever attached character references again.
- Fix, unified across `/auto`, windowed and sequential runners: an empty stored selection only counts as
  explicit when the reference picker's **manual lock** (`image_refs_first_manual`, set on real user edits)
  is present; unlocked empties fall through and re-pick from today's flow/prompt/narration text. Auto-gen
  now only seeds NON-empty picks. Existing poisoned projects self-heal on their next auto-gen run
  (bonus: a user-locked "no characters" choice is now respected by `/auto` too, which previously ignored it).

### Added — AAF-first audio setup for narration projects (Audio tab)

The AAF exported by ElevenLabs (or any AAF-capable editor) becomes a first-class — and recommended —
audio setup path, with clear override semantics while it's active.

- **AAF Timeline panel on the Audio tab** (narration modes, shown first, marked RECOMMENDED): pick the
  AAF (+ the matching audio file, optional if audio already uploaded, + the SRT export if you want
  narration text + subtitles), one Import button, **upload progress bar** (AAFs are big) → "Parsing AAF
  timeline & slicing audio…" → optional SRT step. Uses the proven 1.14.0 import pipeline (replace scenes
  from sample-accurate clip boundaries, slice audio, rebuild chapters).
- **AAF is authoritative while attached** (`project.settings.audio_source = "aaf"`, set by the import
  endpoint — the project-menu Import AAF modal gets this for free):
  - Whisper analyze / SRT upload **never resync scene boundaries** (gated inside
    `_maybe_resync_scene_boundaries` with an explicit `aaf_authoritative` reason).
  - **Suggest Timeline and create-scenes-from-sections return 409** with a clear "detach the AAF first"
    message; the Audio tab's post-analyze auto scene-create/suggest chain is skipped client-side too.
  - The rest of the Audio tab renders dimmed under a banner explaining exactly what is superseded
    (boundaries) and what SRT/Whisper are still for (narration text for prompts, word timing for
    subtitles — **research note: ElevenLabs AAFs carry timing only; the text ships in their SRT/CSV**,
    which is why the panel takes an optional SRT alongside).
- **Active-state panel** shows the imported filename, date, clip/scene counts, with **Re-import AAF**
  (replaces scenes, with warning) and **Detach AAF timeline** (new `POST /timeline/detach-aaf` —
  clears the authority flag, keeps scenes/audio/chapters untouched, re-enables the normal flow).
- **Embedded audio extraction** (`extract_aaf_embedded_audio`): ElevenLabs AAFs embed every clip's
  rendered audio essence (a ~200 MB AAF vs an 8 MB MP3 — it's mostly audio). The importer now pulls each
  SourceClip's essence (RIFF/AIFC/raw-PCM aware, chunked reads) and reconstructs the full-length track
  with ffmpeg at exact timeline positions (silence base + adelay + amix, no normalization) → saved as the
  project's narration audio and sliced per scene. Priority: uploaded audio > existing project audio >
  embedded essence > **fail fast with a clear 400** (previously an AAF-only import silently produced a
  dead timeline: scenes but no waveform/playback).
- Importer improvement: clip text is now also read from AAF mob **user comments** (best-effort) in case
  a producer embeds dialogue there — ElevenLabs puts the text in its SRT/CSV exports, so scene names
  still fall back to "Scene N" until an SRT provides text.
- **`tools/diag_aaf.py`** — inspect any AAF: clip count/names (text present?), mob user comments,
  embedded essence streams with sizes + format signatures (RIFF/AIFC/raw-PCM + sample rate/channels/bits),
  and a plain verdict on what the file provides. Loads the parser directly by path (no heavy deps).
- **Extraction verified end-to-end** against a pyaaf2-authored embedded-essence AAF (raw-PCM, the exact
  format real ElevenLabs exports use per diag): clips land at exact timeline positions, gaps and tail
  are digital silence. Also fixed a pyaaf2 quirk the first diag run exposed: essence streams return
  **bytearray** (unhashable), which silently zeroed the size counts.

## [1.23.0] - 2026-07-01

### Changed — Prompt-system overhaul (deep-dive P0/P1/P2 + official LTX JSON schema)

Implements `docs/PROMPTING_DEEP_DIVE_2026-07-01.md` end-to-end, informed by LTX's official
JSON-prompting article supplied by the user.

**Video JSON mode → official LTX schema**
- The structured video prompt now follows LTX's OFFICIAL format: `scene` / `subject` / `camera` / `duration` (was a community five-section shape). Camera is three mandatory discrete fields (shot_type / angle / movement — "static" stated explicitly); `preserve_from_input_image` lives under `scene`; duration paces the motion. **Legacy v1.22.0 objects auto-convert on load** (`normalize_video_json`) so existing scenes keep working. Dispatch SFW/style/colour constraints inject as `scene.style_constraints`; duration backfills from the scene when the model leaves it 0.
- **The lazy dispatch build now uses the FULL video enhance context** (same builder as the /video-json endpoint) — autogen-built JSON prompts had only a duration line as context.

**Routing & overrides (correctness)**
- **Autogen 0-ref renders route to the first-pass model's prompt rules** (Z-Image / Krea 2) in `/auto`, windowed batch and all five sequential FF sites — they never render on Klein, but were always prompted as Klein. Narration override skipped for first-pass models (context still carries NARRATION MODE), matching manual enhance.
- **Two-pass phase prompts now beat narration/user overrides** — a narration-mode batch Pass 1 was silently enhanced under the narration prompt, losing its scene-only/no-refs rules.
- **Two-pass base double-enhance removed** — the seven batch `("base",)` pre-enhance sites are gone; dispatch's `_build_two_pass_base_prompt` is the single authoritative base enhance (one LLM call instead of two, no compounding drift).
- **Ideogram lazy captions build from the CLEAN stored prompt** — SFW/style/colour dispatch tails were leaking into the structured caption's source prose.
- **Scene Intent is role-gated** — an intent authored for the first frame is no longer declared AUTHORITATIVE for last-frame/video prompts (cross-role → continuity-reference phrasing); manual video enhance passes the video role; auto video now receives the intent block at all.
- **Placeholder guard** — "Scene 12" / "Cinematic scene 3" placeholders are treated as empty by the enhancer instead of being "tightened while keeping the intent".

**Video context parity (the image builder's generation of upgrades, ported)**
- Auto video context gains: explicit priority stack (with the new "drop, don't merge" resolution rule — also added to image + manual stacks), strict-vs-grade palette split, global project context, Scene Intent (video role), clip pacing ("one main action per 2-3s", beat count computed from duration), CAMERA IS MANDATORY clause, and an AV-native audio-description block when model audio is on.
- The named-uncapped character list is replaced by an appearance-only cast capped at 3 (video prompts were being fed names the model can't use).
- Manual video enhance: video-resolution canvas (was image), camera-mandatory clause.

**Reference handling**
- Batch/sequential runners use the same budget order as `/auto`: extras count against the 5-slot budget first, characters fill the remainder (they could previously exceed the Klein slot budget).
- Manual generation now attaches multi-angle `extra_images` identity refs (appended after extras so Image-N numbering stays aligned; capped at 5).
- Manual ref descriptions are appearance-only ("Image N shows: …") — character names are no longer fed to an LLM that is simultaneously forbidden from using them; stale "2-character limit" copy → 3.

**System prompts (research-backed)**
- Klein composite: mandatory explicit KEEP-CLAUSE ("keep the pose, lighting, composition and colors of Image 1 unchanged") — BFL's documented anti-bleed technique.
- Z-Image: adopted Tongyi's official pe.py rules — lock-the-core-first, detail-not-padding (five enrichment axes), no metaphor/emotional rhetoric, band widened to ~70-200 words (512-token encoder note), plus a target-shape example; persona now precedes the first-frame conditional (also Krea 2).
- LTX video prompts: honest negative-prompt statement (standard path has NO negative channel — write positively; only Director has one), length↔duration coupling with beat math, camera-clause-always, emotion-as-physical-cues, and a target-shape example (both music and narration variants).
- IMAGE prompt: two few-shot examples (one 2-ref, one no-ref) demonstrating graceful degradation.
- Flow generation: restored the per-scene cast rule (1-2 most important, 3 max — was "up to 5, name freely"); the per-scene retry prompt regains the location-diversity + cast rules.
- Character-creator portraits: prose phrasing (appearance-led, neutral studio, balanced exposure) instead of tag piles — they render on Z-Image/Krea 2 whose rules forbid tag spam.
- Vision ref captions: face shape/structure + clothing colours called out (identity-critical detail for the prompt writer); Ideogram captions hard-capped under the 2048-token limit by trimming tail elements.
- Manual /enhance-prompt now passes per-model prompt guidance (Settings) — autogen always did; the button silently dropped it.

### Fixed — post-release verification pass (5 adversarial audit agents re-checked every claim)
All v1.22.1 + v1.23.0 claims verified present and correct; the pass surfaced and fixed:
- **`_save_fallback_output` was EOF-truncated (pre-existing, at HEAD)** — the VHS direct-download fallback saved assets then hit `TypeError` in its caller because the final `return created_asset_ids` had been lost (the recurring Edit-tool truncation class; AST-clean so compilers can't catch it). Restored.
- **Last 5-ref leak paths closed**: `_resolve_frame_ref_asset_ids` now hard-clamps its return to the 4-slot budget (≥5 manual extras used to leak through); LF queueing reserves slot 1 for the FF continuity ref inside the 4 limit; frontend ref caps 5/4 → 4/3 and the LF workflow label can no longer say `klein_5ref`.
- **Worker-caps residual gap**: `z_image_turbo`/`krea2_turbo` jobs carry empty caps and were still hitting the legacy merged-set check — a video-restricted worker wrongly rejected first-pass image jobs. Empty-caps jobs now use the image category.
- **Stale "2 character references" Prompt-tab warning** contradicted the 3-ref limit and false-warned 3-character projects → corrected to 3.
- Sequential 0-ref scenes: the context's model line now matches the routed system prompt (was still labeled Klein).
- Minor: export normalize temp WAVs cleaned on failure too; dead `if False` expression removed from `build_scene_intent`; `CharacterInfo.extra_images` typed (killed an `as any`); ReferenceSelector header comment corrected.

### Notes
- **Klein reference budget locked to 4** (`MAX_TOTAL_REF_IMAGES = 4`) per BFL's official multi-reference guide — dispatch also clamps the LF first-frame-prepend case to 4 total; `klein_5ref` stays mapped for legacy queued jobs only, nothing new produces 5 refs.
- Full server-side context consolidation (killing the frontend TS context fork entirely) remains the recommended next structural step (P1-2 in the deep-dive doc).

## [1.22.1] - 2026-07-01

### Fixed — full-audit fix wave (complete findings list in AUDIT_2026-07-01.md)

**Blocking**
- **`/auto` enhanced modes 500'd on every run** — undefined `session_factory` in the video branch; plus three swallowed `skip_existing_prompts` NameErrors that meant `/auto` LLM enhancement NEVER actually ran; plus a chapter-scoped `/auto` + LLM crash (and scope leak) in the post-flow scene re-read.
- **Chapter-scoped export rendered the full project** — `chapter_selection` was never forwarded into the export task params; all scope machinery (scene filter, audio slice, subtitle shift, filename label) was unreachable. Also forwarded the previously-dead `subtitle_bold`.
- **SceneEditor Rules-of-Hooks crash** — the Prompt-tab audit `useEffect` sat below the `!activeScene` early return; moved above it.

**Dispatch correctness (the params-rebind class)**
- `_build_builtin_workflow` now builds on ONE local copy of the params and propagates routing/record keys (`submitted_*`, `effective_negative_prompt`, `ideogram_caption`, `skip_audio_mux`, `video_tail`, seed) to `job.parameters` via a `_record()` channel. Previously, five mid-function `dict(params)` rebinds silently dropped everything written after them whenever SFW / image-direction / colour-override was active — losing submitted-prompt records, mis-routing redirected jobs, and muxing project audio over AV-native model audio. Redirects signal worker routing via `_effective_workflow_type` without poisoning retries; the actual seed is recorded (asset meta no longer says `seed: null`, retries reuse it).
- **Anti-text suffix truly removed** — 1.20.0 removed it from the *record* while `prepare_klein/zimage/krea2_workflow` still appended it to what was SENT. Send sites now match the record (and FLUX/Krea "no negatives in the positive prompt" practice).
- **Video JSON mode gated to i2v / fflf / v2v-extend** — it was hijacking transition clips (zhuanchang LoRA trigger lost) and V2V pass-2 refinement prompts; it also bypassed the SFW/style/colour dispatch constraints (now re-injected into `visual_style_mood`) and its build failures were logged at debug (now warning). `video_tail` no longer snapshots the whole params dict (which lost the submitted video prompt).
- Klein inpaint mask controls (`expand`/`blur_radius`) never applied — node title is "Grow Mask With Blur" (with spaces); both spellings now tried.
- **Per-worker model caps are per-category** — restricting a worker's image models no longer excludes it from ALL video jobs (the old merged set could never contain the video model).

**Prompt/reference integrity**
- FF cast context no longer claims "reference photos attached" when the resolver attaches nothing (cast == refs symmetry); stale "2-character limit" copy → 3; `rerun_pass2` honours an explicit empty character selection and uses the image-resolution chain; three image-job sites (missing-images mode + two sequential scene-1 FFs) rendered at VIDEO resolution → now image resolution; `enhanced_count` no longer over-reports.
- Ollama failover actually fires (OpenAI SDK raises `APIConnectionError`, not builtin `ConnectionError`); Gemini calls get a 600 s timeout; OpenAI token-param mismatch retries once with the alternate parameter.

**Timeline / chapters / audio**
- Re-uploading an SRT now triggers the scene-boundary auto-resync (the missing half of the 1.8.20 drift fix); batch SRT items get the same re-anchor before substitution.
- Chapter split marks the original chapter manual (rebuild no longer silently undoes the split); rebuild pre-clean re-parents manual children of deleted auto parents (FK crash); header-chapter reparse respects surviving manual chapters (no renamed-chapter duplicates); `_apply_auto_split` raw SQL binds hex GUIDs (was a silent 0-row UPDATE).
- Delete-merge can no longer slice scene audio from a Demucs stem (stems exclusion + stem-row upsert on analyze); suggest-timeline commits the new timeline BEFORE the chapter rebuild (a rebuild failure used to roll back every scene while reporting success); `/retrim` colour correction was dead (aliased-import NameError); dead duration fallback referenced a nonexistent enum + attribute.
- Demucs timeout is now enforceable (threaded stderr drain, DEVNULL stdout, honest message); remote Whisper detection no longer misroutes Gradio (404 ≠ OpenAI-compatible) and remote POST timeouts scale with audio duration.

**Export / batch / IO**
- Failed exports no longer poison the next run — work_dir carries a params hash and stale leftovers are wiped on mismatch (keyed resume still works).
- Music-mode "Normalize audio" no longer fails the export at 98 % (PCM-in-MP4): audio is extracted, normalised as WAV, re-muxed with the video stream copied.
- Batch image filter wrote a key nobody read (`image_filter` → `global_image_color_filter`); resume/recover CRF maps aligned with the main map; upload filenames sanitised (batch/assets/timeline).
- Project text export/import round-trips the structured-prompt state (per-scene + project `scene_intent_mode`/`scene_intent`/`video_json_mode`/`video_json_prompt`, project `json_prompt_mode`).

**Frontend**
- ConceptPanel "Create" character button no longer wipes the song title; `handleCreatorSave` dep array carries all 13 missing payload fields (stale-closure saves reverted resolutions/global-context/model-audio); duplicate character-library modal removed.
- The RQ→Zustand mirror re-points `activeScene` at its refetched row — one checkbox click after an auto-gen run can no longer clobber freshly-persisted refs/prompts/chosen image via a stale whole-parameters PUT. The per-scene GGUF select goes through `updateSceneAndSync` (was the last raw-write holdout).
- LLM results (enhance / scene intent / video JSON) no-op when the scene changed mid-request instead of landing on the wrong scene; CharacterCreatorModal preserves fields it doesn't edit (`library_origin_id`) and its version-poll no longer leaks intervals.
- Backend FastAPI/health version strings read from `VERSION` (were 1.11.0 / 0.1.0); startup orphan-sweep message reports the job's original status.

### Deferred (documented in AUDIT_2026-07-01.md)
`/rerun-whisper` SRT-preservation guards, server-side scene-parameters merge, music-mode remix/stems support, per-scene Scene-Intent/Video-JSON override UI, library `extra_images` round-trip, autogen per-model prompt routing, video-context prompt upgrades (the last two are picked up by the prompt-system deep-dive).


## [1.22.0] - 2026-06-29

### Added — Video JSON Prompt mode (opt-in, structured LTX prompting)

- **Send the video prompt to LTX as structured JSON instead of prose.** A new per-project/scene toggle (Concept tab → "Video JSON Prompt Mode", LTX only). When on, the video prompt for a scene is a structured JSON object — `setting_environment` (location, lighting, `preserve_from_input_image`, environment motion, color palette), `subject_action` (subject + timed `action_sequence` + motion characteristics), `camera_movement` (style, movement, framing, `forbidden_camera_behavior`), `visual_style_mood`, and `motion_timing_cues` (duration, intensity, animation behavior, `negative_cues`). LTX 2.3 parses these fields with higher fidelity than prose, giving much tighter control over camera behavior, action timing, and motion. Schema mirrors the community example that "worked great."
- **Generated + fully editable on the Prompt tab** ("✨ Generate with AI" builds it from the scene's video context; every field is editable JSON; Save persists it). The stored object is sent verbatim at dispatch in place of the prose prompt.
- **`preserve_from_input_image` auto-fills** from the scene's first frame when left empty, so image-to-video keeps the established composition, subject placement, lighting, palette, and background geometry.
- **Auto-gen and per-scene video both follow the setting** (the structured object is built lazily at dispatch when none is stored, so no auto-gen changes were needed). Off by default — the existing prose flow is untouched when off. LTX Director mode is unaffected (it has its own dispatch path). New endpoint: `POST /generate/video-json`; prompt export now includes `video_json_mode` + `video_json`.


## [1.21.0] - 2026-06-28

### Added — the two deferred prompt-system features

- **Multiple reference angles per character (auto-balanced).** Characters can now hold extra reference-angle images (`extra_images`) in the character editor ("Extra reference angles (identity lock)"). When a scene references characters, the Klein 5-slot budget is spent intelligently: one main character fills its slots with angles for strong identity lock; several characters get one each before any extra angles. Fully backward compatible — a single-image character behaves exactly as before.
- **Scene Intent mode (opt-in, structured).** A per-project/scene toggle (Concept tab → "Scene Intent Mode"). When on, each scene builds a structured intent — anchor, cast, environment, lighting, camera, palette, must-include/avoid, continuity — that the image/video prompt is compiled to realize exactly. Generated + editable on the Prompt tab ("✨ Generate with AI" + JSON editor + Save), injected as an authoritative brief into both auto-gen and manual Enhance, and included in the prompt export. Off by default; the current prose flow is untouched when off. Mirrors the proven Ideogram-JSON-mode pattern. New endpoint: `POST /generate/scene-intent`.


## [1.20.0] - 2026-06-28

### Changed — Prompt-system gap closure (from the optimality review)

Closed the gaps from `PROMPT_SYSTEM_OPTIMALITY_REVIEW.md`, in the safest way for the app:

- **Reference handling aligned + raised.** New `MAX_SCENE_CHARACTER_REFS = 3` applied consistently. The **first-frame** cast block + auto-gen first-frame references now use the scene's selected (else story-flow) characters — symmetric with the last-frame path — so the **described cast always matches the attached references** (no more "context says 2, 3 attached"). Raised from 2 → 3 (the Klein workflows support up to 5).
- **Dispatch suffix hygiene.** Removed the **phantom anti-text suffix** that was written to the *record* but never actually sent (the models forbid text via their system prompt anyway) — so the Prompt tab now shows exactly what ComfyUI received. The **SFW** suffix that *is* sent was rewritten to **positive phrasing** ("fully clothed, modest, tasteful, family-friendly") per FLUX/Krea best practice instead of negative tags.
- **Manual ⇄ auto parity.** The manual Enhance context now carries the same colour-palette (strict vs grade) and camera-action directives, and the enhance endpoint injects the global project context server-side — eliminating the remaining drift between the Enhance button and Auto-Gen.
- **Pre-flight validators.** `Download Prompts JSON` and a new **Prompt-tab panel** now show read-only warnings before you spend GPU time: "Image N referenced but only M attached", palette contradictions, accidental text/signage, and character-count over the limit. Computed on demand — zero generation-path risk.
- Corrected the inaccurate "FLUX has NO prompt upsampling" claim (FLUX.2 has it; our local workflow doesn't run it).

### Deferred (documented, not gaps)
Multiple reference *angles per character* (needs a per-character multi-image library) and the full Scene-Intent-Object compiler remain future enhancements, not bugs.


## [1.19.0] - 2026-06-28

### Changed — Prompt-system hardening (external-review fixes)

Acted on a prompt-system review (Gemini + ChatGPT against `PROMPT_SYSTEM_AUDIT.md`). Implemented the valid fixes; corrected one false positive.

- **Target canvas / aspect ratio** now injected into the enhance context (auto image + video, and manual) so the LLM composes for the real frame shape instead of guessing.
- **First-frame ↔ heavy-action resolution**: the video-starting-frame rule now tells the LLM how to resolve "calm opening" vs "lyrics describe running/explosion" — depict the charged moment *just before* the action, one coherent frame.
- **Narration routing fixed**: a model-agnostic NARRATION MODE directive is injected whenever the project is narration, so Krea 2 / Z-Image first-pass scenes get the illustrate-the-script bias (previously only Klein did).
- **Explicit priority stack** added to the context, replacing the competing HIGHEST/MANDATORY/ABSOLUTE/PRIMARY labels with one deterministic order.
- **Palette: strict vs grade** — monochrome/B&W/duotone palettes stay strict (all elements incl. skin), but a *named colour grade* now governs mood/lighting/wardrobe while keeping skin and material tones believable (fixes over-strict photoreal skin).
- **Two-pass face preservation** — the composite (Pass 2) prompt now tells Klein to re-light only to match direction/warmth and NOT blow out, harden, or re-shape the inserted face (preserves identity/softness).
- **Character limit surfaced** — when a project has >2 characters, the context notes the 2-reference limit, it's logged, and the Prompt tab shows a warning (no more silent drop).
- **Dispatch transparency** — `Download Prompts JSON` now includes `dispatch_mutations` per frame (which suffixes dispatch appended: SFW / anti-text / style-colour tail), so stored-vs-submitted differences are visible.
- Renamed the misleading `_collapse_to_single_paragraph` → `_clean_prompt_preserve_segments` (alias kept). **Verified it already preserves LTX multi-segment video prompts** — the top concern from both reviewers was a false positive (they read the old name, not the body).


## [1.18.0] - 2026-06-28

### Added — Editable Prompt tab (full manual control)

The scene Prompt tab is now an editor, not just a read-only view — for scenes that are hard to represent visually you can hand-write every prompt.

- **Editable First Frame / Last Frame / Video prompts** (plus the two-pass Pass 1 / Pass 2 prompts when present), each with its own **Save** button. Edited fields highlight amber and show "unsaved" until saved; Save persists via the coherent scene-update path (backend + React Query cache + store).
- **Import** button: load a JSON made outside the app to populate the fields. Accepts a flat shape (`{first_frame_prompt, last_frame_prompt, video_prompt, two_pass_scene_prompt, two_pass_composite_prompt}`) or the **Download Prompts JSON** export shape (`{first_frame:{prompt}, …}`). Imported values fill the fields for review, then you Save each.
- The exact strings sent to ComfyUI (Final Submitted Image/Last-Frame/Video, Two-Pass Original) remain below as **read-only** diagnostics under a "What was actually sent to ComfyUI" divider.


## [1.17.1] - 2026-06-28

### Changed — First/last-frame prompts tuned for LTX 2.3 image-to-video

Per LTX 2.3's own image-to-video guidance, the source (first frame) image should show the scene's STARTING moment — the video step animates the motion from it — and an overloaded first frame produces busier, worse video. Our first-frame prompts were cramming the full scene/action into the still.

- **First frame = opening moment.** For animated scenes (music_video / narration_video), the enhance context now instructs the model to depict the calm starting state — the key subject(s), setting, and lighting as the shot opens, BEFORE the action — and NOT to pack in every action/character/element the video will reveal (the video prompt handles those). Applies to both auto-gen and manual Enhance, across all first-pass generators (Z-Image / Krea 2 / Klein). Standalone stills (narration_images) keep depicting the full scene.
- Softened the shared image prompt's "all actions MUST appear" rule and added the video-first-frame role to `IMAGE_SYSTEM_PROMPT`, `Z_IMAGE_SYSTEM_PROMPT`, and `KREA2_IMAGE_SYSTEM_PROMPT`.
- **Last frame = clean end keyframe.** `LAST_FRAME_IMAGE_SYSTEM_PROMPT` now notes the last frame is the keyframe the video resolves to — depict one clean endpoint, not a packed montage; keep it as uncluttered as the first frame, with the video prompt carrying the motion in between.


## [1.17.0] - 2026-06-28

### Added — Ideogram structured-prompt improvements + prompt-tab tooling

- **Enhance builds the structured caption.** When Ideogram (structured-JSON) mode is on for a Krea 2 scene, the main image **Enhance** now also builds/refreshes the structured caption from the freshly enhanced prose — one click gives you the curated, positioned prompt, ready to hand-edit in the JSON Prompt editor. (Previously Enhance only produced prose; the caption was built separately or at render.)
- **References carry their Ideogram layout.** A generated image's structured caption is now stored on the asset (`meta.ideogram_caption`). When that image is later used as a reference, its authored element **layout/positioning** is fed into the prompt context — combined with the vision-model description — so the model respects the fuller composition, not just a generic description.
- **Reference vision-scan audit panel** on the Image tab: each reference shows a clickable thumbnail of the exact image scanned next to the vision model's description (and an "ideogram" badge when the image was composed with structured prompting), so you can audit what the vision model sees.
- **"Download Prompts JSON"** button on the Image tab: exports a troubleshooting JSON of the scene's first-frame / last-frame / video prompts, the exact strings submitted to ComfyUI, models, resolution, seed, and resolved references. Ideogram-mode frames include the **full structured caption** (the actual positioned layout sent to ComfyUI), clearly marked — not just prose. New endpoint: `GET /generate/prompts-export`.

### Fixed

- Restored a pre-existing truncation in `pyproject.toml` (the `[tool.setuptools.packages.find]` section and ruff `select` line had been cut off by an earlier edit).


## [1.16.0] - 2026-06-28

### Changed — Last Frame generation is now cast-aware (introduce characters at the end)

Reworked how the Last Frame (FF/LF mode) image is generated so it respects who is actually in the scene and can introduce a character who was not in the first frame (e.g. a second character enters by the end), giving the video model a real reference instead of an invented look.

- **Scene-aware LF references.** Auto-gen now attaches the Last-Frame tab's selected character reference images (or, when nothing is picked, the characters the story flow names) — previously it attached only "extras", so the video model hallucinated any character that entered at the end.
- **Explicit cast in the prompt context.** The LF enhancer is now told exactly which characters are present at the end, that no one else is in frame, and which character ENTERS who was not in the first frame — and it only claims reference images are attached when they actually are. The First Frame prompt is passed in explicitly for continuity.
- **System prompt allows cast changes.** `LAST_FRAME_IMAGE_SYSTEM_PROMPT` no longer forces "keep all characters identical to the first frame"; a character may exit or a referenced character may enter by the end, but the model must never invent anyone not in the cast list.
- **First Frame attached by default.** The chosen first-frame image is now used as a Klein reference (slot 1) for the last frame by default for tight continuity (dispatch-time, resolved when the FF is ready). The per-scene "Don't reference first frame image" toggle now defaults OFF (attach); flip it on for a freer end-point.

### Added — Vision-model activity indicator

The reference-image vision model (Ollama) was already wired into every enhance path but had no visible signal. Now there is one:

- A live **vision activity tracker** per project (count of reference images described + cache hits, the model, and a last-activity message), exposed at `GET /generate/vision-activity` and merged into the sequential auto-gen status.
- INFO logging whenever the vision model describes a reference image.
- A small **eye badge** on the running auto-gen button showing how many reference images the vision model has described this run (hover for the model + last message).


## [1.15.0] - 2026-06-28

### Added — Krea 2 "Ultra" V2 workflows + SFW/NSFW mode

Replaced the Krea 2 Turbo workflows with the tuned V2 ("Ultra") graphs and added an SFW/NSFW switch.

- **Four workflow files** now ship in `workflows/`: `KREA2_TURBO_T2I.json` (SFW) / `KREA2_TURBO_T2I_NSFW.json` and `KREA2_IDEOGRAM_T2I.json` (SFW) / `KREA2_IDEOGRAM_T2I_NSFW.json`. The V2 graphs drop the separate `ConditioningKrea2Rebalance` node, route the prompt through `RBG_Smart_Seed_Variance` directly, and sharpen at 0.75.
- **NSFW variants** insert the `ComfyUI-Krea2T-Enhancer` node (`capitan01R`) on the model path (`enabled: true`, `strength: 1.0`), which patches the Krea2 text-fusion path and bypasses the model's built-in safety checker. SFW variants omit the node entirely (safety checker active).
- **Settings → Single Image Generator → Krea 2**: new **"SFW mode (model safety checker on)"** toggle (default ON). When OFF, the dispatcher loads the NSFW workflow for both plain and Ideogram modes, falling back to the SFW file if the NSFW one is missing.
- New `krea2_sfw_mode` app setting (DB column + migration + API schemas).

### Fixed

- `prepare_krea2_ideogram_workflow` now sets the `EmptyLatentImage` width/height (the actual render resolution) so Ideogram-mode renders follow the scene resolution instead of being pinned at the workflow's baked 1920×1080.

### Notes

- The V2 install `.bat` also offers a `krea2_turbo_lora_rank_64_bf16` LoRA; per the user it is intentionally **not** used here (the Power Lora Loader stays empty).
- The `KREA2V2/` source-export folder is gitignored.


## [1.14.2] - 2026-06-28

### Fixed — AAF parser validated against a real ElevenLabs export

Tested the importer against a real ~238MB ElevenLabs Dubbing Studio AAF and fixed two issues it surfaced (the parser now produces 377 clean, contiguous scenes from that file):

- **Composition discovery** — pyaaf2's `content.toplevel()` returns *nothing* for ElevenLabs AAFs, so the parser now falls back to scanning all mobs for the `CompositionMob` (then any mob with a Sound sequence). Without this, real imports failed with "no top-level composition".
- **Scene names** — ElevenLabs AAF clips are named generically ("Render") and the track name is uniform, so scenes now fall back to clean "Scene 1…N" names instead of repeating a meaningless label. (The dialogue text lives in ElevenLabs' separate CSV export, not the AAF.)
- Added `sampleaaf/` and `*.aaf` to `.gitignore` so large/personal AAF files are never committed.


## [1.14.1] - 2026-06-28

### Fixed — AAF import + manual scene editing (post-audit hardening)

An independent audit of 1.14.0 found no blockers; these robustness fixes were applied:

- **Manually-added / split scenes are now bound to a chapter.** `create_scene` assigns the new scene to the chapter whose time range contains it (deepest match, else the last chapter) and extends that chapter — so manual add and split no longer leave a scene with `chapter_id=NULL` that chapter-scoped Auto-Gen / Export / Story Flow would skip. Add/Split also refresh the Chapters view.
- **New scenes are clamped to the audio length.** `create_scene` now clamps a scene's end to the master audio's duration (in addition to the min/max bounds), preventing a manually-added scene from extending past the audio and slicing a silent tail.
- **AAF import no longer leaks the old audio file.** When you upload replacement audio during import, the previous music file is removed from disk (not just its DB row).
- **AAF import validates the AAF first** (before any scene/audio change) and **surfaces a clear warning** if chapters couldn't be rebuilt (so you can run "Re-derive Chapters").


## [1.14.0] - 2026-06-27

### Added — Import ElevenLabs AAF timeline + manual timeline editing

**AAF import.** The project 3-dots menu has a new **Import AAF (ElevenLabs)** option. It parses an ElevenLabs Dubbing Studio AAF (binary, via `pyaaf2`) into scene boundaries and **replaces** the project's scenes with that timeline, then slices audio per scene and rebuilds chapters — mirroring Suggest Timeline. The dialog lets you **use the project's existing audio or upload a new file** (sliced to the new boundaries). Clip cut points become scene cuts; clip names become scene names. (Requires `pyaaf2` in the backend env; a clear message tells you to `pip install pyaaf2` if it's missing.)

**Manual timeline editing (power users).** You can now build/adjust timelines by hand without the auto flows:

- **Add Scene** button in the timeline toolbar — appends a new blank scene.
- **Numeric Start/End entry** per scene (Scene → Tools tab) — type exact times; the scene's audio re-slices to match.
- Plus the existing **Split at playhead**, **Delete** (with merge), and **drag scene boundaries** — all confirmed working and unchanged.

None of this affects the existing auto pipeline (audio analysis, SRT/lyrics, Suggest Timeline) — they remain the default. See `docs/TIMELINE_EDITING.md`.


## [1.13.0] - 2026-06-27

### Added — Klein inpaint (mask-paint editing of rendered images)

Review a generated image and fix/replace part of it by painting a mask — like ComfyUI's mask editor, right in the app.

- **Inpaint button** in the image lightbox (where you review a generated image full-size). Opens a full **InpaintModal**.
- **Mask painting** over the displayed image: brush (size slider), eraser, clear. The mask is baked into the source's alpha channel (ComfyUI clipspace convention) at full resolution.
- **Prompt** for what should appear in the masked area.
- **Reference (optional)** to place a specific object/character into the masked area: **upload** an image, pick from **project assets**, or pick from your **characters list** — and optionally **crop a region** of the reference to use just a part of it. With no reference, it inpaints from the source image + prompt alone.
- **Result** comes back as a new image **version** on the scene; review it and **Save as scene preview**, or inpaint again.
- **Backend**: `workflows/KLEIN_INPAINT.json` (FLUX.2 Klein), `prepare_klein_inpaint_workflow`, a `klein_inpaint` dispatch route (source + reference uploaded as LoadImage files; result composited back over only the masked region), and a `POST /generate/inpaint` endpoint.


## [1.12.5] - 2026-06-27

### Fixed — First-pass image prompts optimized per model (no more blown-out fluff)

Researched each model's official + community prompting guidance and reworked our LLM system prompts so each one is written for the model that actually renders — concise, and free of the quality-booster spam that was causing the blown-out look.

- **New `Z_IMAGE_SYSTEM_PROMPT`** (Tongyi Z-Image Turbo): structured camera-direction prose, no reference language, "negatives" written positively, motivated lighting — and an explicit ban on booster terms (`masterpiece/8k/HDR/ultra-contrast`), which on Z-Image directly cause highlight clipping/oversaturation. **Fixes a real bug**: no-reference Z-Image renders were being enhanced with the *Klein reference* prompt.
- **New `QWEN_EDIT_SYSTEM_PROMPT`** (Qwen-Image-Edit): imperative edit instructions, `image 1/2/3` roles, quoted literal text.
- **`IMAGE_SYSTEM_PROMPT` (Klein / FLUX.1) de-fluffed**: ~30–90 words, edit-instruction phrasing with `image 1/2` references (say what *changes/combines*, don't re-describe), graceful no-reference handling, no boosters/weight-syntax, no character names, lighting-first.
- **Krea 2 / two-pass-base / last-frame / narration** prompts tightened (word counts trimmed, booster spam banned). Krea 2 in particular was trained to *remove* the AI look, so booster words are counter-productive.
- **Routing fix**: the manual Enhance now picks the prompt by what will render — no references → first-pass generator (Z-Image/Krea 2); with references → Klein. (Auto-gen uses the shared, now-graceful Klein image prompt; per-model auto-gen routing is a documented follow-up.)

See `docs/MODEL_PROMPTING.md` for the per-model rules + sources.


## [1.12.4] - 2026-06-27

### Fixed — Klein two-pass (Pass 2) prompts: edit instructions, not T2I descriptions

The Pass-2 (Klein composite) LLM prompt was being written like a from-scratch T2I description — long, blown-out paragraphs that re-described everything Klein already sees in the reference images, and that echoed character **names** the edit model can't possibly use. Reworked to treat Klein as the **edit model it is**:

- The Pass-2 system prompt now asks for a **short edit instruction** (~20–60 words): Image 1 = the finished base scene (keep its exact lighting/exposure/palette, don't darken or restyle), Image 2+ = the character(s) to insert. It explicitly forbids re-describing what the images already show.
- **No more character names.** The dispatcher no longer feeds character *names* into the composite context — characters are referenced only as "Image 2", "Image 3", … by appearance. A hard "never use a name / proper noun" rule is in the system prompt too (names are wasted, misleading tokens for an image model).
- The composite is now **seeded with a concise edit instruction** instead of the entire base-scene prose, so the LLM stops re-describing the environment.
- Single-pass Klein image prompts also stop emitting character names (reference subjects by image position instead).


## [1.12.3] - 2026-06-27

### Added — Player: follow-playback scene selection + prev/next scene

- The main stage now **selects the scene under the playhead while playing**, so if you spot a problem you can just pause and that scene is already open in the editor to fix — no clicking around the timeline.
- Added **Previous scene** / **Next scene** buttons to the main player controls (alongside play/seek/fullscreen). Previous jumps to the current scene's start when you're more than ~0.5s in, otherwise to the previous scene; Next jumps to the next scene's start. Both also select the target scene. (The Timeline toolbar already had equivalent prev/next-section skip buttons; this brings them to the player bar.)


## [1.12.2] - 2026-06-27

### Added — Main stage: full-screen toggle + player controls

The main preview stage (the canvas at the top-centre that plays the timeline) now has its own controls overlay: a **play/pause** button, a **seek** scrubber, current/total **time**, and a **full-screen toggle**. Controls fade in on hover (and stay visible while paused or in full-screen). Full-screen uses the browser Fullscreen API on the stage; everything stays wired to the same timeline playback state, so it's in sync with the timeline transport.


## [1.12.1] - 2026-06-27

### Added — LTX Director: Retake/editing + High-Quality two-stage

- **Retake / edit an existing clip** — in the Director editor, enable Retake to re-generate a chosen span (start + length) of an existing video with a new prompt and strength, keeping the rest. Source video = this scene's current video, or pick/upload one. Wires the node's `retakeMode`/`retakeVideo`/`retakeStart`/`retakeLength`/`retakePrompt`/`retakeStrength` and flips `LTXDirectorGuide.retake_mode`; the source video is uploaded with the timeline files.
- **Quality toggle** — Standard (single-stage, fast) vs **High (2× upscale)**, a two-stage workflow (`LTX_DIRECTOR_HQ.json`) that adds an `LTXVLatentUpsampler` 2× spatial upscale + refine pass and tiled VAE decode (sharper, low-VRAM friendly). Selected per scene; the dispatcher routes to the HQ workflow when set (falls back to single-stage if the file is absent).

### Fixed (LTX Director audit)

- Motion-track guides now resolve their `asset_id` to a file (`videoFile`/`imageFile`) so they're uploaded and actually reach the node.
- Fixed an autosave feedback loop in the Director editor (debounced save no longer re-fires on parent re-render).
- "Auto-size from keyframes" is now honored — the editor defaults to pinned project dims, and an explicit auto (0) is passed through instead of always falling back to project dims.
- Project **text export/import now preserves advanced per-scene config** — Director Mode timelines, LLM instructions, and vision / JSON-prompt toggles survive a round-trip (carried under a per-scene `advanced_params` block). Previously these were silently dropped.

### Notes

- Auto-gen / batch video generation intentionally ignores Director Mode and produces a standard LTX video; regenerating a Director-enabled scene via auto-gen overwrites its result (the saved Director config is preserved, just not used by auto-gen).


## [1.12.0] - 2026-06-27

### Added — LTX Director Mode (per-scene timeline editor)

A full-screen timeline editor on the Video tab that drives the v2.0.0 LTXDirector ComfyUI node, grafted onto our existing LTX stack (GGUF unet + distilled LoRA + gemma DualCLIP + KJ VAEs + VHS output). Enable it per scene and it replaces the normal video options with direct timeline control.

- **Video tab toggle** "Enable LTX Director Mode" — greys out the normal video controls and reveals an "Open Director Timeline" button + an inline Generate.
- **Full-screen editor** (`LTXDirectorModal`): zoomable timeline with frames/seconds display and three lanes —
  - **Prompt Relay** — time-segmented prompts (draggable/resizable blocks); each conditions its own span of the clip while a **global prompt** anchors what's constant. Per-segment epsilon transition control.
  - **Keyframes** — image guides from project assets, uploads, or the **previous scene's last frame** (one-click "Continue from previous scene"); each pinned at a frame with a strength slider; drag to reposition.
  - **Audio** — defaults to the scene's audio (conditioning / lip-sync), overridable by picking or uploading an audio asset; or let LTX generate its own.
  - **Motion track** (advanced) + output controls (pin size / resize method / keyframe CRF).
- **Saves on every edit** to `scene.parameters.ltx_director` (reopen any time); **Generate** enqueues an `ltx_director` video job to the batch like normal.
- **Backend:** new `ltx_director` workflow_type → `workflows/LTX_DIRECTOR.json` (validated API export on our stack), `prepare_ltx_director_workflow`, dispatch route that builds `timeline_data` + Prompt-Relay strings from the scene config and resolves keyframe/audio assets, plus a timeline-file uploader. Gated on the workflow file existing.


## [1.11.0] - 2026-06-21

### Added — Vision model (Ollama) to describe reference images for the prompt LLM

Reference images now get described by a local vision model and the description is fed to the prompt-enhancer LLM, so it understands what a reference image actually shows — more reliable than the source prompt alone, and the only signal for images imported from outside the app.

- **Settings → LLM:** a new Vision section under the existing Ollama config (reuses the same Ollama server pool). Global **"Enable Vision Descriptions for Reference Images"** toggle + a **Vision Model** selector with a Refresh button (lists models from the Ollama pool via `/settings/ollama/vision/models`). Recommended **qwen2.5vl:7b** (best caption accuracy; faster: qwen2.5vl:3b / moondream; higher quality: llama3.2-vision:11b).
- **Per-image override** on each Image tab (Project default / On / Off), shown when a vision model is configured. Saved on the scene and overrides the global setting.
- **Auto-gen** honors it: the image and video auto-gen enhance contexts describe the scene's selected reference images. A toggle is also surfaced in the Auto-Gen panel's advanced options.
- The description is **cached on the asset** (`asset.meta.vision_description`) so each image is described at most once; the call is a single low-temperature Ollama `/api/chat` request with a tight factual-caption prompt. Everything degrades gracefully (no model / unreachable → plain enhance).
- Schema: `ollama_vision_model`, `ollama_vision_available_models`, `vision_enabled` on app_settings (+ migration); new `backend/services/llm/vision.py`; manual + auto-gen enhance injection.

## [1.10.2] - 2026-06-21

### Added — "Include LLM Instruction" for prompt enhancement

A per-scene custom instruction you can hand the LLM to keep it on track when Enhance drifts from what you want.

- A compact button (pencil icon) sits next to the **Enhance** button on both the **image** and **video** tabs. Click it to open a small lightbox showing the current prompt plus a box for your direction (e.g. "keep her seated", "wide shot only", "no text"). It's saved on the scene and reused every Enhance until cleared.
- The button **lights up amber with a dot when an instruction is set**, so you can see at a glance that one is active.
- The instruction is injected as the **highest-priority** line of the enhance context (it overrides other guidance on conflict). Stored separately for image (`llm_instruction_image`) and video (`llm_instruction_video`).
- **Auto-gen honors it too:** the same per-scene instruction is prepended to the auto-generation enhance context for both image and video, so the LLM stays on track during batch runs — not just manual Enhance.

## [1.10.1] - 2026-06-21

### Improved — Last Frame image generation (distinct end-point + first-frame reference control)

Last Frame renders were coming out too similar to the First Frame. Two changes:

- **Stronger Last Frame prompting:** `LAST_FRAME_IMAGE_SYSTEM_PROMPT` now leads with how to *derive* the last frame — read the First Frame prompt **and** the scene's story flow, then advance the action to a CLEARLY DIFFERENT moment (subject position/pose/action/expression and/or camera framing) rather than restating the first frame. The First-Frame-image reference is now treated as optional (rely on the First Frame prompt for continuity when it isn't attached). The Enhance call also injects the scene's story flow + an explicit "distinct end-state" directive into the Last Frame context.
- **New per-scene toggle "Don't reference first frame image"** (under "Reference: First frame set" on the Last Frame tab), **ON by default**. On = the last frame is generated freely from the prompt + character refs (no pixel over-anchoring to the first frame — the prior behavior). Off = the chosen first-frame image is prepended as Klein reference slot 1 for tight visual continuity (the workflow auto-bumps a ref tier). Character reference selections continue to apply to the last frame as before.

## [1.10.0] - 2026-06-21

### Added — Ideogram Prompting Mode (Krea 2 structured-JSON captions)

Opt-in mode that prompts Krea 2 with the Ideogram-4 structured caption format — positional bounding boxes + per-element color palettes — for precise composition control, instead of plain natural language. OFF by default; only engages when the first-pass model is Krea 2.

- **Concept tab:** global "Ideogram Prompting Mode" toggle (stored in project.settings.json_prompt_mode).
- **Image tab (Krea 2 only):** per-scene override (Project default / On / Off) plus a **JSON Prompt** button opening a simple editor — view/edit the caption, **✨ Generate with AI** (drafts it from the scene prompt), and an **Instructions** panel.
- **Auto-gen honors the setting:** the dispatcher checks the effective mode (scene override ▸ project default) and, when on, builds/loads a structured caption and routes to a Krea 2 workflow with the Ideogram Prompt Builder node — so auto-gen needs no special handling.
- **LLM prompting:** new `JSON_PROMPT_SYSTEM_PROMPT` teaches any LLM the format (coordinate system, layered decomposition, color rules, palette-override priority) + `normalize_ideogram_caption` validator (uppercase hex, clamped 0-1 coords, palette caps). Captions are cached on the scene; manual edits are respected. Graceful fallback to plain Krea 2 if the caption can't be built.
- `prepare_krea2_ideogram_workflow` populates the Ideogram4PromptBuilderKJ node (x/y/w/h fractions → the node converts to Ideogram bbox), leaving all tuned sampler/variance/model settings untouched. New `POST /generate/json-prompt` endpoint. Workflow `KREA2_IDEOGRAM_T2I.json` registered when present. Full design in `docs/IDEOGRAM_JSON_PROMPT_MODE.md`.

### Fixed — Pass-2 rerun crashed with "Unknown workflow type: klein_6ref"

The "Re-run Pass 2" path gathered ALL project characters with no cap (1 base + 5 chars = klein_6ref, which doesn't exist — Klein ships up to 5REF). Now it uses the scene's selected characters (image_refs_first.characterIndices, the single source of truth), caps at 3 (matching the auto-chain), and hard-clamps the workflow to klein_5ref.

## [1.9.3] - 2026-06-21

### Fixed — two-pass Pass-1 mislabeled "Z-Image Turbo" when actually rendered by Krea 2

The image was rendering correctly on Krea 2 (confirmed in logs: `Redirecting to Krea 2 Turbo (two-pass Pass 1...)` → `Krea2_*.png`), but the UI labeled the Pass-1 image "Z-Image Turbo". Root cause: the two-pass base re-enhances its prompt and rebuilds the workflow; the first-pass redirect's in-memory `workflow_type="krea2_turbo"` mutation didn't survive the rebuild + session refresh, so the asset recorded `klein_t2i`, which the frontend maps to "Z-Image Turbo".

- **Backend** (`dispatcher.py`): when an asset's resolved `workflow_type` is `klein_t2i`, record the configured first-pass generator (`krea2_turbo`/`z_image_turbo`) instead — since `klein_t2i` is always redirected to it. Fixes the stored model on all new renders.
- **Frontend** (`SceneEditor.tsx`): the Pass-1 base label treats `klein_t2i`/missing as the configured first-pass generator, so already-generated images also label correctly.
- **Hardening** (`workflow.py`): `prepare_krea2_workflow` now coerces width/height/seed defensively so a null value can never raise and cause a silent Z-Image fallback.

> Existing two-pass images keep their stored value but now label correctly via the frontend fix; regenerate to also correct the stored metadata.

## [1.9.2] - 2026-06-21

### Fixed — two-pass base re-enhanced prompt silently dropped; Krea 2 first-pass diagnostic

- **Two-pass base double-build bug** (`dispatcher.py`): for two-pass scenes, Pass 1 re-enhances the prompt to scene-only, then rebuilt the workflow. But the first build had already redirected `workflow_type` away from `klein_t2i` (to `z_image_turbo`/`krea2_turbo`), so the rebuild raised "Unknown workflow type" — caught and ignored — which silently discarded the re-enhanced scene-only prompt (Pass 1 ran with the original, character-laden prompt). Fixed by resetting `workflow_type` to `klein_t2i` before the rebuild so the first-pass redirect re-runs (same model choice) with the re-enhanced prompt. Affected both Z-Image and Krea 2 first passes.
- **New `tools/diag_krea2.py`**: pinpoints why a first pass still renders as Z-Image when Krea 2 is selected — checks the saved `single_image_generator`, the presence of `KREA2_TURBO_T2I.json`, and the on-disk VERSION (to confirm the backend was restarted on the new code).

> Note: the "first pass shows Z-Image" label is driven by the dispatcher's first-pass redirect, which chooses Krea 2 only when `single_image_generator == 'krea2_turbo'` AND `KREA2_TURBO_T2I.json` exists. If either is false at runtime — or the backend is still running pre-1.9.0 code — it falls back to Z-Image. Run `tools/diag_krea2.py` to see which.

## [1.9.1] - 2026-06-21

### Added — Chapter scope picker for Auto-Gen (All / Single / Multiple)

The Auto-Generate panel now has the same chapter scope selector as the Export screen, so you can run auto-gen on the whole project, one chapter, or several specific chapters — instead of only "the chapter you're currently viewing" vs "everything."

- Reuses the export `ChapterPicker` (All / Single / Multiple). Defaults to the chapter you're currently viewing (Single) if any, else the whole project (All) — so existing behavior is preserved.
- **Multiple** runs the selected chapters **sequentially**, one scoped pass per chapter in timeline order: each chapter gets its own scoped story-flow pre-step + auto-gen run, and the next only starts after the current one finishes (polls the auto-gen status endpoint for completion). No changes to the batch pipeline internals.
- Cancel stops the whole queue between chapters. Per-chapter failures are collected and reported without aborting the rest of the queue.
- Picker only appears for projects that have chapters; single/all paths are unchanged.

## [1.9.0] - 2026-06-21

### Added — Krea 2 Turbo as an optional first-pass image generator

Full integrated, **gated** support for Krea 2 Turbo as an alternative to Z-Image for no-reference (first-pass) text-to-image. Krea 2 is first-pass only (not an edit model) — character compositing (Pass 2) still always uses FLUX.2 Klein. **Nothing changes for existing users:** the default remains Z-Image, and Krea 2 only activates once a tested `KREA2_TURBO_T2I.json` is present (otherwise it logs a notice and falls back to Z-Image).

- **Settings:** new "Krea 2 Turbo" option under Single Image Generator, plus a "Krea 2 Model File" picker (`krea2_turbo_fp8.safetensors` for RTX 40xx/older, `krea2_turbo_mxfp8.safetensors` for RTX 50xx Blackwell). New `krea2_model_name` setting (schema + migration + API serialization).
- **Dispatcher:** the `klein_t2i` first-pass redirect now resolves the selected generator — Z-Image (default) or Krea 2 — and is gated on the workflow file existing. Sets the real `workflow_type` (`krea2_turbo`/`z_image_turbo`) for correct worker capability matching and UI labels.
- **Workflow prep:** new `prepare_krea2_workflow()` — tolerant node resolution (title with class-type fallbacks) so it works with whatever tested JSON is supplied; overrides the diffusion model to the chosen fp8/mxfp8 file.
- **Prompting:** dedicated `KREA2_IMAGE_SYSTEM_PROMPT` with Krea 2-specific rules (natural-language prose, no quality-booster/tag spam, no weight syntax, lighting/material-led, concise). The enhancer auto-uses it when Krea 2 is selected. Other models' prompts are untouched.
- **UI labels:** scene model badges and the generation queue badge recognize Krea 2 Turbo; the predicted 0-ref label follows the setting.
- **Docs:** new `docs/KREA2_GUIDE.md` — model variants + fp8/mxfp8 (50xx vs older), download locations, ComfyUI settings (8 steps, CFG 0–1, er_sde, simple), prompting best practices, and an activation checklist.

## [1.8.31] - 2026-06-21

### Fixed — SQLite WAL never shrank (parked at ~4 MB with "nothing to commit")

Power-user report: the `-wal` file grows to ~4 MB and stays there even when there's nothing left to commit. Diagnosed as expected-but-untidy SQLite behavior, not corruption:

- WAL mode was enabled but no explicit checkpoint was ever forced. SQLite's automatic checkpoint runs in **PASSIVE** mode at the `wal_autocheckpoint` threshold (default 1000 pages × 4 KB page size = **~4 MB**, exactly the size observed). PASSIVE folds committed frames back into the main `.db` but **never truncates** the `-wal` file, so it parks at ~4 MB. The data is already committed ("nothing transferable") — the file just isn't reclaimed.
- The WAL capping at 4 MB (rather than growing without bound) confirms checkpoints were succeeding, so there was **no leaked/long-lived reader** blocking checkpointing.

Fixes (`backend/database/database.py`, `backend/main.py`):
- New `checkpoint_wal(mode="TRUNCATE")` helper runs `PRAGMA wal_checkpoint(TRUNCATE)` to fold frames in **and** shrink the `-wal` to 0 bytes.
- `cleanup_db()` now TRUNCATE-checkpoints before `engine.dispose()`, so a clean shutdown leaves a 0-byte `-wal`.
- New `periodic_wal_checkpoint()` background loop (every 5 min) TRUNCATE-checkpoints during long sessions, reclaiming disk after big write bursts (auto-gen / batch). Cancelled on shutdown.
- `wal_autocheckpoint=1000` is now set explicitly with a comment documenting the 4 MB relationship.

## [1.8.30] - 2026-06-18

### Improved — Pass-2 Klein compositing preserves the base scene (anti-darkening)

Two-pass character compositing was re-grading and darkening scenes instead of just inserting characters. Root cause: the Klein "Edit Ultra" workflow generates from an EMPTY latent conditioned on reference latents, so it regenerates the whole frame (using the base scene only as a reference) and drifts in exposure/palette — Klein also blends lighting from the character reference photos.

Prompt-level hardening (the structural fix is a workflow change — see note):
- **Always-on base-preservation anchor at dispatch** (`dispatcher.py`): every Pass-2 composite prompt now gets a strong instruction appended at the very end (where Klein weighs tokens most) to keep the first reference image's exact lighting, exposure, brightness, contrast, color grade, palette and composition, and to insert ONLY the characters — "do not darken, dim, desaturate, re-grade or restyle." Previously this only happened when a color override was active.
- **Stronger anti-darkening in the Pass-2 system prompt** (`prompt_enhancer.py`): the top rule now explicitly calls out Klein's tendency to darken/re-grade and requires the prompt to lock the base image's exact brightness and exposure.

> **Note (workflow-level fix):** the most complete fix is to run Pass 2 as img2img — feed the base scene as the *init latent* with denoise ~0.5–0.7 instead of `EmptyFlux2LatentImage` — so Klein preserves the base pixels and only paints the characters in. That requires editing `KLEIN_EDIT_ULTRA_WORKFLOW_*REF.json` and testing in ComfyUI; can be added as a tunable per Lorenzo's preference.

## [1.8.29] - 2026-06-18

### Fixed — Workflow label now shows Z-Image for no-reference renders

The Image tab displayed "FLUX Klein – Text to Image" for a 0-reference scene, even though the backend always redirects `klein_t2i` to Z-Image Turbo at dispatch (Klein is only ever used to composite character refs in Pass 2). The label was misleading — the actual render was already Z-Image. Fixed three frontend label spots to show **Z-Image Turbo** for `klein_t2i`: `labelWorkflow()`, the per-scene model label (no longer gated on the `single_image_generator` setting), and the computed-workflow display. Post-render history already showed the correct model.

## [1.8.28] - 2026-06-18

### Added — Auto-pick character references on Enhance + robust auto-gen selection

Completed the character-selection model so the right references are chosen automatically without ever overriding a deliberate choice. The rule everywhere: **a scene's `image_refs_first.characterIndices` is authoritative; auto-pick only fills it in when it's absent (never set).**

- **Enhance/Generate now auto-pick when a scene has no explicit selection.** The frontend re-enabled `autoSelectCharactersForScene`, but gated on "no explicit selection yet": it matches the scene's flow/prompt/narration text against the character roster (full name or first-name, word-boundary aware), selects up to 3, and persists them — so enhancing a fresh scene picks the correct refs. An explicit selection (manual picks, a deliberate empty `[]`, or what auto-gen saved) is respected verbatim and never overridden.
- **Auto-gen auto-selects in the image phase too, not just at flow time.** Both the windowed and sequential auto-gen paths now run the same server-side character pick (`_select_scene_characters_from_flow` over flow + prompt + narration, cap 3) for any scene without an explicit selection, and persist the result to `image_refs_first` so the Image tab shows exactly what was used. This makes auto-gen's selection reliable even when story flow already existed (previously the pick only ran inside `_ensure_video_flow` during fresh flow generation).

Net: run Auto Gen → each scene gets its most-relevant characters chosen, persisted, and visible. Manually enhance a brand-new scene → it picks the right characters for you. Deselect characters on a scene → that choice sticks and nothing re-adds them.

## [1.8.27] - 2026-06-18

### Fixed — Deep audit of image-gen: no-ref renders use Z-Image, characters only when selected

Two linked regressions: a scene with no characters selected still rendered with characters, and no-reference renders ran on Klein instead of Z-Image. Root cause was a chain of "default to the first N project characters" fallbacks (one of them added in 1.8.26) that injected characters a scene never asked for — which made the workflow `klein_Nref` (Klein-with-refs) instead of `klein_t2i`, so it never redirected to Z-Image.

Audited the whole path and made the scene's `image_refs_first.characterIndices` the strict single source of truth:

- **Removed every "default first-N characters" fallback** — in the `/generate-image` two-pass resolver, and both the windowed and sequential auto-gen paths. No selection (empty OR absent) now means **no characters**. Auto-gen still auto-selects via the server-side LLM character pick (which persists the choice and shows it on the Image tab); scenes the LLM names no character for stay character-free.
- **Auto-gen seeds an explicit empty selection** (`characterIndices: []`) when a scene has none, so the Image tab reflects "no characters" instead of silently pulling project characters in.
- **Frontend `autoSelectCharactersForScene` disabled** — it used to re-add characters mentioned in the flow/prompt text on every Enhance/Generate, overriding the visible selection. The saved selection is now authoritative everywhere.
- **`klein_t2i` always redirects to Z-Image Turbo** in the dispatcher (regardless of the `single_image_generator` preference). Klein is only ever used to composite character references (Pass 2); a zero-reference text-to-image render must always be Z-Image. Two-pass Pass 1 was already forced to Z-Image; this extends it to all no-reference renders.

Net: remove all characters from a scene → it renders single-pass on Z-Image with no characters; select 1–3 → Klein Pass 2 composites exactly those. The Image-tab selection is precisely what gets used and is saved for re-render/troubleshooting.

## [1.8.26] - 2026-06-18

### Fixed — Scene character selection is now the single source of truth (auto-gen used hidden refs)

A scene with no characters selected on the Image tab could still render with characters (a "4-reference" composite), and the picker didn't reflect what auto-gen actually used.

- **`/generate-image` two-pass fallback no longer resolves ALL project characters.** When a generation request carries no explicit refs, it now resolves characters from the scene's `image_refs_first.characterIndices` (an explicit empty list = "no characters here"), and when the field is absent it defaults to the first 3 AND persists that — so the Image tab always reflects exactly what Pass 2 composited.
- **Character caps raised 2 → 3** across the auto-gen paths (`_resolve_character_asset_ids(..., max_chars=3)`, the first-N defaults, and the seeded `characterIndices`) so a scene's saved 3-character pick is actually honored end-to-end (slot 1 = base image, up to 3 character refs).
- **Frontend refreshes scenes when auto-gen finishes** (`invalidateQueries(['scenes', id])`), so the Image-tab selections update to match what auto-gen persisted instead of showing stale empty state.

Net: the characters shown selected on each frame's Image tab are exactly the references used in the second pass, and that selection is saved for re-render / troubleshooting.

## [1.8.25] - 2026-06-18

### Fixed — Manual scene character selections now stick (were overridden by auto-select)

On the Image tab, manually selecting/deselecting characters didn't survive clicking **Enhance** or **Generate**. Cause: `autoSelectCharactersForScene()` ran on every Enhance/Generate, re-added any character whose name appeared in the flow/prompt/lyrics text, and saved that over the user's picks — so deselecting a character that's mentioned in the text silently reverted.

Fix (`frontend/src/components/SceneEditor/`):
- The reference picker's `onChange` now marks the frame as **manually edited** (`image_refs_first_manual` / `image_refs_last_manual` in scene params, persisted via the cache-coherent `updateSceneAndSync`).
- `autoSelectCharactersForScene()` short-circuits and returns the current selection unchanged once that manual flag is set — so auto-select can seed an initial suggestion, but never overrides a user's explicit choice afterward.
- Raised the per-frame character cap from 2 to **3** in both the `ReferenceSelector` UI and the auto-select logic, matching 1.8.24 (slot 1 stays the base scene image; up to 3 character refs).

Now: pick/deselect characters on a scene, hit Enhance or Generate, and your selection is exactly what's used and saved.

## [1.8.24] - 2026-06-18

### Changed — Per-scene character cap is 3 (not 2)

Per Lorenzo: the Klein composite daisy-chains references (slot 1 = the base scene image, then character refs), and up to **3 characters** gives great results while more starts "off-roading". Raised the per-scene character selection cap from 2 to 3 (`_select_scene_characters_from_flow(..., cap=3)`), updated the story-flow prompt to state the 3-character limit, and lowered the dispatcher's hard ceiling `MAX_CHARS_IN_COMPOSITE` from 4 to 3 so no composite ever exceeds 3 character refs. "Fewer is better when only one or two truly matter" is kept in the prompt so scenes aren't crowded.

## [1.8.23] - 2026-06-18

### Added — LLM picks the 2 most important characters per scene (was: first 2)

Scene images previously defaulted each scene's character refs to the **first 2 project characters** positionally (`characters[:2]`), regardless of who the scene is actually about. The story-flow LLM is now told the **3-character limit** (slot 1 is reserved for the base scene image; up to 3 character refs) and instructed to reference only the **1–3 most important characters by name** for each scene; `_ensure_video_flow` then derives `image_refs_first.characterIndices` from the characters the flow actually named (matched by name / first token, in order of appearance, capped at 3). Falls back to the existing default only when the flow names no character, and never overrides an explicit manual selection. New helper `_select_scene_characters_from_flow` in `backend/api/generation.py`.

Result: a scene about "the Rabbit and the Fox" gets the Rabbit and Fox refs — not whoever happens to be characters #1 and #2. Re-run Auto Gen / regenerate Story Flow to apply to existing scenes.

## [1.8.22] - 2026-06-18

### Fixed — SRT upload no longer blocks on Whisper; re-anchor moved into Process Audio

The 1.8.20/1.8.21 SRT re-anchor ran Whisper **synchronously inside the SRT upload**. With a ComfyUI Whisper backend (multi-minute, ~52-min budget) the upload HTTP request blocked and the frontend errored — and it could spawn a second Whisper run alongside an in-flight Process Audio pass.

Reworked so the timing re-anchor happens where Whisper already runs — **Process Audio** — and SRT upload stays instant:

- **`upload_srt` is now fast and never runs Whisper.** If the project already has Whisper word timing (from a prior Process Audio run) it maps the SRT spelling + cue grouping onto it instantly; otherwise it just stores the SRT and logs that Process Audio will re-anchor it.
- **`analyze_audio` (Process Audio) now re-anchors instead of discarding Whisper timing.** Previously, re-analyzing a project that had an SRT *kept the SRT's drifting timestamps* and threw away the fresh Whisper timing. It now combines them via `retime_srt_words_to_audio`: SRT words + cue blocks (correct spelling) with Whisper's audio-accurate timing.

Workflow (matches the intent): **Upload SRT (instant) → Process Audio (Whisper runs once, re-anchors) → Suggest Timeline.** Result: SRT spelling + cue grouping with audio-accurate cut points, and no upload timeouts.

## [1.8.21] - 2026-06-18

### Changed — SRT re-anchor now reuses the existing Whisper pass (robust)

Refined the 1.8.20 SRT-timing fix after confirming the real use case: the SRT is needed for correct ElevenLabs **spelling** (Whisper garbles words), while Whisper provides accurate **timing** from the audio. The combination — SRT words + Whisper timing — is exactly what `retime_srt_words_to_audio` produces.

`upload_srt` now sources the timing in priority order: (1) **reuse Whisper words already stored on the project** from a prior Process Audio run — no re-transcription, and no dependency on locating the audio file at upload time; (2) fall back to a fresh Whisper pass on the audio (with hardened path resolution that tries both `project_dir/<id>/rel` and `project_dir/rel`). This removes the audio-path fragility that caused the re-anchor to silently no-op on projects whose media lives on a different drive than the database.

Deterministic workflow: **Process Audio (Whisper) → Upload SRT → Suggest Timeline.** Result: clean SRT spelling + cue grouping, with audio-accurate cut points. Verified by simulation (Whisper "tortis" → SRT "tortoise" spelling kept, blocks kept, timing error 0.000s vs audio).

> Requires the backend to be restarted on this version. Note: this surfaced a separate environment issue — a project whose **database is on C: but media on D:** (leftover from the old broken `change_project_dir`); consolidating those onto one `project_dir` is recommended.

## [1.8.20] - 2026-06-18

### Fixed — SRT timings re-anchored to the actual audio (root cause of narration drift)

Diagnosed the long-standing "scenes cut earlier and earlier, dialogue still playing after the cut" problem to its true root cause — and it was NOT the segmenter. Using a Whisper pass over the real audio of a 13-minute narration (`1-The_Song_Beneath_the_Stump_V6`), the SRT-derived scene boundaries were shown to be a perfect match to the SRT (0 bleed) but drift progressively against the actual audio: **39 of 48 scenes ended mid-word** when measured against real speech, with the offset growing to ~10s by the end. The cause is that ElevenLabs (and similar) **SRT word timestamps drift from the rendered audio** on long files — the text and cue grouping are correct, but the times are not. Because the segmenter faithfully placed cuts on the SRT times, every prior segmentation fix reproduced the bad input timing.

Fix: when an SRT is uploaded, the app now **re-anchors the SRT's timing to the audio**. New `retime_srt_words_to_audio()` in `backend/services/audio/text_align.py` runs a difflib sequence alignment between the SRT word stream and a Whisper pass over the real audio, then keeps the SRT's word strings AND cue (`block`) grouping while transferring Whisper's audio-accurate `start`/`end` onto each word (interpolating words Whisper missed, distributing time across mismatched runs, enforcing monotonic order). `upload_srt` runs this automatically (Whisper on the actual audio, `skip_demucs=True`), and it is fully best-effort: any failure, low SRT/Whisper similarity (<30%), or a project with `disable_whisper` set falls back to the SRT's own timings so upload never breaks.

Result: you keep clean ElevenLabs SRT wording AND cue grouping, but scene boundaries now sit on audio-accurate times. Verified by simulation (SRT drift 0.36s mean → 0.000s after re-anchor; text + blocks preserved; monotonic; graceful bail on mismatched audio and on Whisper-missing words).

> **To fix an existing project:** re-upload its SRT (this triggers the audio re-anchor), then re-run **Suggest Timeline**. Note the SRT upload now runs Whisper, so it takes ~30–90s instead of being instant.

### Added — `tools/diag_timeline.py` audio reality check

The timeline diagnostic now auto-targets the most recently edited project and adds an "audio reality check": it locates the project's audio file, ffprobes its true duration, and (for SRT projects) compares cue times to ffmpeg-detected speech onsets — the tooling that surfaced the SRT-vs-audio drift. Also reports per-scene bleed and drift-growth.

## [1.8.19] - 2026-06-18

### Added — Structure-first narration segmentation (two-phase)

Reworked `_dp_segment_narration` so scene cuts follow the narration's real structure instead of only chasing an even length. Per Lorenzo's two-phase model:

- **Phase 1 — adaptive major-silence anchors.** The segmenter measures this narration's own inter-phrase pauses and flags the ones that are clearly larger than typical (≥ 3× the median pause, floor 1.5s — adaptive so it works for tight SRT cues and loose Whisper timing alike). These structural pauses become near-inviolable scene boundaries via a heavy DP span penalty (a scene that would swallow a major silence is charged 50 cost units, dwarfing every normal term). The penalty is finite, so honouring an anchor can never push the DP into the fixed-slice fallback.
- **Phase 2 — even fill.** The existing duration-balancing DP fills each chunk between anchors, keeping scenes as consistent as possible within the project's min/max (e.g. 8–20s).
- **Standalone silence scenes.** A pause at least as long as the scene minimum (e.g. an instrumental break or deliberate beat) is carved into its OWN scene rather than split 50/50 between neighbours. Guarded so a long pause can never shave an adjacent scene below 60% of the minimum.

This is additive: projects with no major silences (e.g. uniform-cue SRT like the verified V6) segment exactly as before — even-filled, midpoint boundaries, no spurious silence scenes. The win shows up on long narrations and anywhere a scene previously spanned a clear topic-change pause.

Verified by simulation: uniform SRT → unchanged even fill (no silence scenes); SRT with a 12s instrumental gap → exactly one carved ~12s silence scene; Whisper with structural pauses → cuts anchored on the pauses, all scenes within 8–20s, no uniform-10s collapse.

> **To apply:** re-run **Suggest Timeline** on narration projects (segmentation only changes on regeneration; Process Audio just re-snaps existing scenes).

### Note — uniform-10s projects are stale, not a live bug

Confirmed the fixed-slice fallback is unreachable in current code: running the real segmenter on Judges-scale Whisper data (1,200 words) yields 60+ distinct scene lengths, never uniform 10s. Projects still showing a uniform 10s grid (e.g. *The Book of Judges*, *Bacon Is For Liars V2*) were segmented by an older build and never re-segmented — Process Audio only re-snaps, it doesn't re-run segmentation. Re-running Suggest Timeline regenerates them correctly. Added `tools/diag_timeline.py` to inspect any project's SRT-cue map, timing source, and per-scene boundary alignment.

## [1.8.18] - 2026-06-18

### Fixed — SRT cue times are now the authoritative scene-segmentation map (no more cumulative drift)

Lorenzo re-ran *Process Audio* with an SRT loaded and saw narration scenes start aligned but progressively cut earlier and earlier before each pause toward the end of the timeline — even though the SRT carries exact per-cue start/end times, so segmentation should be a pure lookup.

Root cause in `backend/api/timeline.py::_group_words_into_sentences` (the phrase grouper that feeds `_dp_segment_narration` / Suggest Timeline): when a pasted script (`initial_text`) was present, it always ran the fuzzy `_match_words_to_lyrics_lines` "monotonic multi-word anchor" matcher to map script lines onto word timestamps — **even when the words came from an SRT.** That matcher interpolates missed anchors by time and tolerates matches up to 20s away, so its small per-line errors accumulate down a long script. The previous 1.8.17 fix corrected boundary *placement* between phrase groups, but the phrase *groups* themselves were still built by the drifting matcher, so the SRT's exact map was never actually used.

Fix: `_group_words_into_sentences` now checks for SRT `block` indices first. When present, it groups one phrase per cue directly from the block map (each cue's words already carry exact SRT start/end) and returns immediately — the fuzzy matcher is bypassed entirely. Scene boundaries become an exact lookup against the cue times, identical for every project regardless of length. Whisper-only projects (no `block`) fall through to the unchanged lyrics/punctuation grouping.

Verified by simulation: 40 SRT cues with varied inter-cue pauses produce boundaries that land at the exact midpoint of every cue gap with **zero cumulative error** (error does not grow with cue index) and zero dialogue bleed.

> **To apply:** re-run **Suggest Timeline** on the project. *Process Audio* only re-snaps existing scene boundaries (it preserves the scene count and your manual edits); regenerating the segmentation from the corrected cue map requires a Suggest Timeline pass.

## [1.8.17] - 2026-06-18

A maintenance + narration-precision release: a critical truncation repair in the settings API, three code-hygiene cleanups, and a precise rework of how narration scene boundaries and subtitle cues are timed so video stays locked to the audio through to the end.

### Fixed — Truncated `change_project_dir` endpoint (data-loss risk)

`backend/api/settings.py` had shipped truncated: the `change_project_dir` endpoint ended mid-statement at `settings.project_dir = str` — no closing call, no `commit`, no `return`. It parsed clean only because `str` is a valid builtin reference, so neither `ast.parse` nor `tsc` ever flagged it (the Edit-tool Windows-mount truncation pattern from the handover notes). With *move data* enabled the endpoint physically `shutil.move`d all project data to the new folder, then failed to persist the new path — leaving the DB pointing at the now-empty old directory, and returning no body despite declaring `response_model=ChangeProjectDirResponse`. Completed the statement to persist (`str(new_path)`), `session.add` / `commit` / `refresh`, log, and return the declared model — matching the commit pattern already used elsewhere in the file. Restart-to-apply behavior is by design and already surfaced in the UI.

### Fixed — Narration scene cuts now split pauses evenly and never clip dialogue

Lorenzo reported that the ends of some scene dialogues were still being heard after the visual had already cut to the next scene, even though that dialogue belonged to the scene's lyrics — and that the mismatch compounded toward the end of long narrations. The live preview is audio-driven (it shows whichever scene owns the current master-audio position), so the bleed meant a scene's `end_time` was genuinely landing before its dialogue finished.

Reworked the boundary builder in `backend/api/timeline.py::_dp_segment_narration`:

- **Inter-phrase pauses are now split evenly between the two adjacent scenes.** The boundary is placed at the midpoint of the gap (`(prev_end + start) / 2`), so a 1.0s pause leaves 0.5s tailing the current scene and 0.5s leading into the next (previously a >0.6s gap was biased to `next_word_start − 0.3`). Per Lorenzo's spec.
- **Anti-bleed guarantee.** The phrase end used for the cut is now the MAX word-end across the whole phrase rather than the last word's recorded `.end`, so the boundary always sits after every spoken word even when Whisper under-shoots a word's end. The midpoint placement then guarantees `boundary > prev_end`, so dialogue can never overflow the cut.
- **Intro/outro pauses are never split.** Scene 1 now always starts at `0.0` (was `first_word − 0.3`, which could shift the entire timeline out of sync by the intro length), owning the intro silence; the final boundary stays at `total_duration` so the outro stays whole with the last scene. Long deliberate lead-in/lead-out pauses are left intact because they belong to a single scene only.

Verified by standalone simulation: a 1.0s pause splits to the exact midpoint, a 0.4s pause splits to its midpoint, intro/outro are preserved, and every interior boundary lands at or after its phrase's max word-end (zero bleed).

### Fixed — Burned-in subtitle cues now honor the SRT and break at pauses

`backend/services/video/ffmpeg.py::generate_ass_subtitles` previously re-grouped words by a fixed gap+word-count heuristic, ignoring the SRT `block` index — so exported captions could diverge from the uploaded SRT (and from the live preview, which groups strictly by block). When words carry SRT block indices that cue grouping is now AUTHORITATIVE: exactly one subtitle line per block, matching the SRT and the preview. Whisper-sourced words (no block) keep pause-based breaking (>0.3s gap, or 8 words) so captions track the spoken rhythm and clear during silences.

> **Applying the narration fixes:** existing projects must re-run **Suggest Timeline** to regenerate boundaries — the change is in boundary *generation*, not a migration of existing scenes.

### Changed — Code hygiene cleanups (no behavior change)

- **Removed deprecated `asyncio.get_event_loop()`** at both call sites in `settings.py` (folder picker + change-dir), swapped to `await asyncio.to_thread(fn)`, matching the pattern already used elsewhere in the file. Avoids the Python 3.12+ `DeprecationWarning`.
- **Production console stripping.** `frontend/vite.config.ts` now uses the `defineConfig(({ mode }) => ...)` form with `esbuild: { drop: mode === 'production' ? ['console', 'debugger'] : [] }`. Because the app always runs `vite build` and serves `dist/`, all `console.*` statements are stripped from the shipped bundle while `vite` dev keeps them for diagnostics.
- **Removed stale artifacts** — `frontend/dist_new/`, an empty top-level `New folder/`, and ~19 leftover `frontend/vite.config.ts.timestamp-*.mjs` Vite temp files.

### Verification

Backend `ast.parse` clean across all 59 files; `py_compile` clean on every touched/related file; truncation marker + abrupt-EOF + bare-builtin-assignment sweeps all clean; frontend `tsc --noEmit` clean; boundary and subtitle-grouping logic confirmed via standalone simulations.

## [1.8.16] - 2026-06-17

This release covers a large surface — batch-mode parity with all features added in 1.8.x, server priority routing, global project context for LLM prompts, a long-running narration-timing drift fix, several UI state-refresh fixes, and the Generation Queue scene-name persistence.

### Fixed — Narration scenes cut before previous rhyme finishes speaking

Lorenzo reported that final-exported narration videos cut to the next scene while the previous scene's words were still being heard in the audio. The drift was visible enough to make the rhyming structure feel "off."

Root cause traced to two places in `backend/api/timeline.py::_dp_segment_narration`:

- **DP main path silent rejection** — the inner loop's `if dur > max_dur: break` rejected any single phrase group whose span exceeded `video_max_duration`. When that happened to ANY phrase, the entire DP gave up and fell through to the natural-break fallback (which has its own drift problem).
- **Natural-break fallback's flat clamp** — `elif snapped - pos > max_dur: snapped = pos + max_dur` capped a scene's `end_time` at a fixed offset regardless of where the phrase's words actually ended. The next scene's `start_time = pos = snapped` inherited the clamped value. The master narration audio plays continuously across the assembly, so any words past the clamped boundary overflow into the next scene's visual window.

Two-part fix:

- **Pre-split overlong phrase groups before the DP runs.** New block at the top of `_dp_segment_narration` walks every phrase group; if `last_word.end - first_word.start > max_dur`, recursively splits at the largest internal inter-word gap (the speaker's natural breath/pause). Each sub-phrase fits and the DP main path always finds a solution. Logs a `WARNING` with the original vs. final group count whenever splits happen.
- **Phrase-respecting clamp in the fallback.** Replaced the flat `snapped = pos + max_dur` with a search for the LAST natural break in `[pos+min_dur, pos+max_dur]` — SRT cue end or Whisper word-gap candidate. When no candidate exists (genuinely impossible-to-satisfy constraint — e.g. a single 15s word), a loud `WARNING` logs the situation and accepts the flat clamp as a last resort, telling the user how to fix it (raise `video_max_duration` in Settings or split the phrase).

Runtime smoke test with a deliberately overlong 25s phrase containing a 15s unsplitable word produced four scenes, none exceeding `max_dur`, the unsplitable region called out with a `WARNING` rather than silent drift.

### Fixed — Auto-Gen modal stuck on completed state until page refresh

Closing the Auto-Gen modal after a completed run kept the local `autoGenStatus = 'done'` plus `sessionHasStarted = true` latch from 1.8.4. The next click on Auto Gen re-opened the modal directly on the completion screen — no path back to the setup form without a full page refresh.

Fix in `AppLayout.tsx::AutoGenerateModal.onClose`: when status is terminal (`done`/`completed`/`failed`/`cancelled`) at close time, reset the entire chunk of auto-gen state (`autoGenStatus → 'idle'`, mode, completed/total counters, step text, scene name, batch run ID, minimized + dismissed flags) and clear the polling interval. Next open shows the setup form fresh. The active-run branch (running/pending) still goes to minimized state as before.

### Fixed — SRT upload didn't refresh the SRT-loaded indicator or Disable Whisper toggle

Both the "SRT loaded" chip on the Audio tab and the new "Disable Whisper Detection" toggle (1.8.15) gate off `lyricsData.source === 'srt'`. After SRT upload, the handler did `setQueryData(['lyrics', projectId], srtData)` — but if the backend's upload-SRT response omitted the `source` field, the cache was written with that field undefined and the UI stayed in "no SRT" state until a full page refresh.

Fix in `AudioSetup.tsx::onChange` (SRT input):

- **Force `srtData.source = 'srt'`** before writing to the cache so the indicator flips green and the toggle un-greys immediately.
- **Backfill `cue_count`** from the words' `block` set or `srt_blocks.length` when missing, so the chip's "N cues · M words" text is accurate.
- **Also `invalidateQueries(['lyrics', projectId])`** so subsequent consumers (Concept tab, scene boundary audit) refetch authoritative state from `GET /lyrics`.

### Added — Per-server priority for ComfyUI worker selection

Mixed-speed render farms now route jobs in priority order. Lower priority number = picked first when idle; among workers with equal `in_flight` load, the lower-priority-number worker wins. Once a high-priority server saturates, idle lower-priority workers pick up the overflow automatically — the "use fast server first, fall back to slow servers when fast is busy" behavior users wanted.

- `backend/services/comfyui/dispatcher.py::ComfyWorker.priority: int = 100` — new field, range 0-1000.
- `apply_user_caps` reads `caps_config["priority"]` and clamps; ignores non-numeric input rather than resetting.
- `select_worker` sort key changed from `(in_flight, -last_check)` to `(in_flight, priority, -last_check)`. `in_flight` stays primary so a busy high-priority worker yields to an idle low-priority one — the fallback path.
- Settings UI: small `PRIO` number input on every server row, default 100, step 10, clamped client-side.
- Persistence: lives in `AppSettings.comfyui_server_caps[url]["priority"]` — same JSON shape as the image/video toggles, so the existing settings PUT handler persists it without backend changes.

Runtime-tested with three workers (fast prio=10, mid=50, slow=100): Job 1 → fast, Job 2 (fast busy) → mid, Job 3 (fast+mid busy) → slow. Exactly the expected fallback ladder.

### Added — Global Project Context (Concept tab)

A new "Enable Global Project Context" section on the Concept tab lets the user specify environmental context (time of day, season, weather, custom free-text) that's injected into every LLM enhance call as a MANDATORY context. **OFF by default** — users must explicitly toggle it on, matching the user's spec.

- `ConceptData` gained 5 fields: `global_context_enabled`, `global_context_time_of_day`, `global_context_season`, `global_context_weather`, `global_context_custom`. All persist independently on `Project.settings` so the user can pre-fill the dropdowns and only flip the toggle when ready.
- 11 time-of-day presets (dawn → midnight, including golden hour, twilight), 6 seasons (spring/summer/fall/winter/monsoon/dry_season), 14 weather conditions (sunny → dust storm, including fog, mist, thunderstorm).
- New `resolve_global_context(settings)` helper in `backend/api/concept.py` translates enum keys into rich LLM-facing phrasing (e.g. `morning` → `"morning (clear bright daylight, fresh feel, soft shadows)"`) and returns `""` when disabled or empty.
- LLM injection in `backend/api/generation.py::_build_auto_enhance_context` — when enabled, the resolved string gets injected as `⚠️ MANDATORY GLOBAL PROJECT CONTEXT (applies to EVERY scene unless explicitly overridden by per-scene direction)`. Same enforcement level as the per-project color override.

### Added — Batch mode now exposes everything added since it was last revisited

Audit caught 6 batch-mode gaps. All closed:

- **SRT upload per item.** New `/api/batch/upload-srt` endpoint mirrors `upload-audio`. `BatchItemConfig.srt_upload_path` carries it through; at audio-analyze time the pipeline parses the SRT (`AudioAnalyzer._parse_srt_to_words`) and substitutes its words for Whisper output as the authoritative timing source.
- **Disable Whisper toggle.** `BatchItemConfig.disable_whisper`. When true AND an SRT is attached, the pipeline passes `whisper_mode="skip"` to `analyze_full`. When the toggle is on without an SRT, a `WARNING` logs and Whisper runs anyway.
- **`narration_images` render type.** Previously only `narration_video` was selectable. Three-radio option now.
- **Model-generated audio (LTX 2.3 AV-native).** `BatchItemConfig.enable_model_audio` — writes `Concept.enable_model_audio = True` after concept generation so every I2V uses the AV-native workflow.
- **Color scheme override.** `BatchItemConfig.color_scheme` (free-text) — written to `Project.settings.color_scheme` at project create AND re-applied after `base-on-lyrics` so the LLM doesn't wipe it.
- **Image post-process filter.** `BatchItemConfig.image_filter` (`none` / `grayscale` / `bw` / `sepia`) — written to `Project.settings.image_filter` for the export FFmpeg post-process.

Frontend: `BatchItemAddModal` got an SRT file picker (narration-mode-only, with X-to-clear), Disable Whisper checkbox (greyed until SRT loaded), narration_images radio, Color Scheme input, Image Filter dropdown, and Model-Generated Audio checkbox.

### Added — Generation Queue scene names persist + are clickable

Two complementary improvements:

- **Clickable scene chips.** Clicking "Scene 79" in any job card on the Generation Queue selects that scene in the timeline, seeks the playhead, and switches the SceneEditor tabbed panel — same pattern Story Flow scene titles use (`AppLayout.tsx::goToSceneInTimeline`). Hover state, keyboard focus ring, `e.stopPropagation()` so the parent card's cancel/retry/delete still work.
- **Scene name persistence on done/deleted-scene jobs.** `JobResponse` now carries an optional `scene_name` field, bulk-resolved server-side via a single `SELECT id, name FROM scenes WHERE id IN (...)` query at every list / get / retry endpoint. If the scene has since been deleted, the response falls back to `job.parameters["scene_name"]`. Frontend `JobCard` prefers `job.scene_name` over the local `scenes` array lookup, so the chip remains visible (in non-clickable gray) even after scene deletion or project switching. Zero schema migration — `Scene` row stays the source of truth, with a `parameters` fallback path for future snapshot writes.

### Files (1.8.16)

- `backend/api/timeline.py` — DP pre-split overlong phrase groups; phrase-respecting clamp in fallback.
- `backend/api/jobs.py` — `JobResponse.scene_name` field + bulk resolver in list_jobs / get_job / retry_job.
- `backend/services/comfyui/dispatcher.py` — `ComfyWorker.priority`; `apply_user_caps` reads it; `select_worker` sort key updated.
- `backend/api/concept.py` — 5 `global_context_*` fields; `resolve_global_context` helper; `GLOBAL_CONTEXT_*` preset dicts.
- `backend/api/generation.py` — global context injection in `_build_auto_enhance_context`.
- `backend/api/batch.py` — `BatchItemConfig` 6 new fields; `/upload-srt` endpoint; SRT word substitution + `whisper_mode="skip"` plumbing; project settings seeding at create + re-apply after `base-on-lyrics`; `Concept.enable_model_audio` set post-concept.
- `frontend/src/components/Layout/AppLayout.tsx` — Auto-Gen modal close-time reset for terminal states.
- `frontend/src/components/AudioSetup/AudioSetup.tsx` — SRT upload cache write injects `source='srt'` + invalidates lyrics query.
- `frontend/src/components/Settings/SettingsPage.tsx` — PRIO number input per ComfyUI server row.
- `frontend/src/components/ConceptPanel/ConceptPanel.tsx` — Global Project Context UI block (enable checkbox + 3 dropdowns + custom textarea).
- `frontend/src/components/BatchMode/BatchItemAddModal.tsx` — SRT picker, Disable Whisper, narration_images radio, Color Scheme, Image Filter, Model Audio checkbox.
- `frontend/src/components/GenerationPanel/GenerationPanel.tsx` — clickable scene chip + `job.scene_name` preference.
- `frontend/src/api/client.ts` — `uploadBatchSrt` helper; ConceptData GET/PUT types extended with `global_context_*`.
- `frontend/src/types/index.ts` — `BatchItemConfig` types extended; `Job.scene_name` added.
- `VERSION`, `pyproject.toml`, `backend/main.py` — bumped to 1.8.16.

## [1.8.15] - 2026-06-15

### Fixed — Doubled chapters after Reprocess Audio (long-running root-cause hunt)

Lorenzo reported that re-uploading audio + SRT + clicking "Reprocess Audio" produced doubled chapters. Three earlier rounds of attempted fixes (NULL/empty source backfill, widened SQL filter, nuclear pre-clean wiping every non-manual row, verify+raw-DELETE escape hatch) cleaned the rebuild path itself but the doubling kept showing up in the UI.

The real bug was uncovered with a new chapter diagnostic (`tools/diag_chapters.py`) that dumped the raw chapters table. The state was:

| Name | Source | Time |
|------|--------|------|
| Chapter 1 | manual | 0–190 |
| Chapter 2 | manual | 190–380 |
| Chapter 2 | **auto** | 194.4–407.3 |
| Chapter 3 | manual | 380–580 |
| Chapter 3 | **auto** | 407.3–608.8 |
| Chapter 4 | manual | 580–763.6 |
| Chapter 4 | **auto** | 608.8–809.8 |

Root cause: `_create_auto_chapters` blindly numbered new chapters starting from 1 regardless of any pre-existing manual rows. When a project had manual chapters that covered most of the timeline but didn't extend to the audio end, the auto-creator filled the tail with rows named "Chapter 2", "Chapter 3", "Chapter 4" at slightly different time ranges. The dedup (which now keys on `(name, depth, parent, start_time_rounded_to_0.1s)`) correctly did NOT collapse them — they're structurally distinct rows referring to distinct timeline positions — but to the user they looked like duplicates because the names matched.

Fix in two places:

- `backend/services/chapters/builder.py::_create_auto_chapters` — at the top, after auto-row cleanup, check for existing `source='manual'` rows. If present: extend the last manual chapter's `end_time` to the project's audio end so tail scenes bind to it, then return without creating any auto rows. Respects the user's manual structure as the source of truth.
- `backend/main.py` lifespan — new auto-vs-manual collision sweep on every boot. Finds `(name, depth, parent)` collisions where one row has `source='auto'` and another has `source='manual'` in the same project. Unbinds scenes from the auto row, re-parents any sub-chapters, raw-DELETEs the auto row via `exec_driver_sql`, then calls `bind_scenes_to_chapters_by_time` to rebind the orphan scenes to the surviving manual sibling. Self-heals existing DBs on first restart.

### Added — Defense-in-depth chapter integrity

Several safeguards built during the 1.8.14 → 1.8.15 hunt remain as belt-and-suspenders:

- **Per-project `asyncio.Lock` around `rebuild_chapters`**. Prevents concurrent rebuilds (e.g. analyze_audio's onSuccess firing suggestTimeline twice in quick succession) from both passing the nuclear pre-clean and inserting N rows each. Second caller waits for the first to commit; logs a `WARNING` so the concurrency is visible.
- **In-line auto-dedup at end of `rebuild_chapters`**. After the build + bind + auto-split, walks every chapter row and groups by `(name, depth, parent_chapter_id, start_time_bucket=0.1s)`. Any duplicate cluster collapses to its oldest row (by `created_at`) via raw `exec_driver_sql`. Critically, after the DELETE, re-runs `bind_scenes_to_chapters_by_time` so scenes that lost their chapter_id get rebound to the survivor — without this rebind a dedup left orphan scenes (the regression Lorenzo saw with "4 scenes not in chapter 4").
- **Standalone `deduplicate_project_chapters(session, project_id)` helper** in `backend.services.chapters`. Same dedup + rebind logic, callable from anywhere. Used by the startup sweep and available for manual recovery.
- **Verify-and-raw-DELETE escape hatch** after the nuclear pre-clean. If a session-level `DELETE FROM chapters` reports `rowcount=N` but the next SELECT in the same session still sees rows (SQLite-async transaction-visibility edge case), the function drops down to the raw connection and forces the DELETE through.

### Added — "Disable Whisper Detection (SRT Required)" toggle

For projects with an authoritative SRT (ElevenLabs, Aivo, etc.), Whisper transcription is now optional. Toggle lives on the Audio tab under the Upload SRT button. When enabled AND an SRT is loaded, the next "Reprocess Audio" skips Whisper entirely and uses the SRT cues directly as the narration timing source.

- Backend `analyze_audio` reads `Project.settings.disable_whisper`, verifies an SRT is loaded (presence of `block` keys in `Lyrics.words`), and passes `whisper_mode="skip"` to `analyze_full`. Falls back to running Whisper if the toggle is on but no SRT is loaded.
- `analyze_full` honors `whisper_mode == "skip"` by returning an empty transcription list, skipping the meaningful-words check, and letting the SRT path upstream substitute its words into the analysis result.
- Frontend `AudioSetup` adds a labelled checkbox below the Upload SRT button. Disabled (greyed out) until an SRT is loaded — surfaces what the toggle requires before the user wastes a click on it. Persists to `Project.settings.disable_whisper` via `updateProject` and syncs the local Zustand store immediately so the checkbox state survives a re-render.

Saves the 30–90s Whisper pass on each Reprocess and avoids Whisper-vs-SRT timing conflicts entirely when the user has the authoritative timing source.

### Added — Clickable scene names in the Generation Queue

Mirroring the Story Flow scene-title navigation pattern (`AppLayout.tsx::goToSceneInTimeline`): clicking "Scene 79" in the Generation Queue now selects that scene in the timeline, seeks the playhead to its start, and the SceneEditor's tabbed info panel switches to show that scene's data.

- `frontend/src/components/GenerationPanel/GenerationPanel.tsx` — `getSceneName` replaced with `getSceneForJob` which returns the full Scene object. The chip is now a `<button>` with `setActiveScene + setPlaybackPosition + setIsPlaying(false)`, hover state (purple underline), keyboard focus ring, and tooltip. `e.stopPropagation()` keeps the parent card's cancel/retry/delete buttons working. Falls back to non-clickable gray text if the scene was deleted but the job still references it.

### Added — `tools/diag_chapters.py` debug script

Standalone diagnostic that dumps the chapters table for the most recently-updated project (or any project ID passed as an argument). Prints:

- Project mode + lyrics state (`initial_text` length, `# header` line count, words count, SRT block count, unique blocks).
- Full chapter table with name, depth, parent, source, time range, scene count, ID prefix.
- Duplicate groups under the dedup key `(name, depth, parent, start_time_bucket)`.
- Scenes not bound to any chapter (orphans), with their times and order_index.
- Total scene count.

Usage: `python tools/diag_chapters.py > chapters.md`. Paste the output anywhere a chapter symptom needs to be diagnosed — the snapshot replaces several rounds of "what's actually in the DB?" guesswork.

### Files

- `backend/services/chapters/builder.py` — per-project `asyncio.Lock` + `_get_rebuild_lock`; `rebuild_chapters` delegates to `_rebuild_chapters_locked` under the lock; manual-respect short-circuit at top of `_create_auto_chapters`; in-line auto-dedup with time-bucket key + post-dedup rebind; standalone `deduplicate_project_chapters` helper.
- `backend/services/chapters/__init__.py` — exports `deduplicate_project_chapters`.
- `backend/main.py` — chapter dedup sweep + auto-vs-manual collision sweep in lifespan.
- `backend/api/timeline.py` — `disable_whisper` gate in `analyze_audio`; substitutes SRT words when Whisper is skipped.
- `backend/services/audio/analysis.py` — `analyze_full` honors `whisper_mode="skip"`.
- `frontend/src/components/AudioSetup/AudioSetup.tsx` — Disable Whisper toggle UI; persists via `updateProject`.
- `frontend/src/components/GenerationPanel/GenerationPanel.tsx` — clickable scene-name chip.
- `tools/diag_chapters.py` (new) — chapter integrity diagnostic.
- `VERSION`, `pyproject.toml`, `backend/main.py` — bumped to 1.8.15.

## [1.8.14] - 2026-06-14

### Fixed — Narration / video duration drift ("everything defaults to 10 seconds")

Lorenzo reported that in narration modes scenes "default to 10 seconds which doesn't work with the narration. It also slowly makes it so the narration and concepts in the generated videos and images no longer make sense with each other."

Root cause was three silent 10-second fallbacks compounding with stale scene boundaries after Whisper / SRT re-analysis:

- **`GenerateVideoRequest.duration` and `AutoGenerateRequest.duration`** defaulted to `10.0` when the frontend omitted the field — whatever the scene's actual `end_time - start_time` was got silently overridden. Both fields are now `Optional[float] = None`; absent values resolve server-side via the new `_resolve_video_duration(session, scene, requested)` helper that returns the scene's actual range, then clamps to `AppSettings.video_min_duration` / `video_max_duration`.
- **Dispatcher silent fallback** at `dispatcher.py` `params.get("duration", 10.0)` and the LTX-sequencer `params.get("duration", 5.0)` now log a `WARNING` when the field is missing or non-positive, and fall back to the project's `video_min_duration` (not the hardcoded 10/5). Surfaces upstream bugs instead of papering over them.
- **No min/max clamp on manual scene create/edit.** `POST /scenes` and `PATCH /scenes` now run every start/end pair through `_clamp_scene_duration(...)`, which reads `AppSettings.video_min_duration` / `video_max_duration` and trims/pads `end_time` to fit. Start time is never moved (would change which Whisper words land in the scene — too destructive). Each clamp logs a warning so the user can see what happened in `diag.md`.

### Added — Stale-boundary detector + auto-resync on narration source change

The "narration and concepts drift apart" symptom was caused by Whisper re-transcribing the narration audio (or a fresh SRT upload arriving) without ever re-running Suggest Timeline. The DB kept the original scene boundaries from the first pass; the new Whisper / SRT word timing was different; `_get_scene_lyrics` then sliced the wrong words into each scene; the LLM prompt enhancer received the wrong narration text; the generated images / videos drifted away from what's actually being said.

New module `backend/services/scene_boundaries.py`:

- `source_label(words)` — detects whether `Lyrics.words` came from Whisper or SRT (SRT-parsed words carry a `block` integer).
- `cue_ranges(words)` — for SRT sources, groups words by `block` so the resync can snap to actual cue boundaries.
- `natural_break_points(words)` — SRT → every cue start/end; Whisper → every >300ms inter-word gap.
- `closest_break(boundary, breaks)` — returns nearest natural break point + distance.
- `audit_scene_boundaries(scenes, words, min, max)` — full per-scene report: out-of-bounds duration, distance from nearest natural break, snap suggestion when SRT is present.
- `needs_auto_resync(audit)` — heuristic: SRT mismatch ALWAYS triggers (cues are authoritative); Whisper triggers when stale fraction ≥30%.

Auto-resync fires from two places in `backend/api/timeline.py`:

- After `analyze_audio` (`POST /api/projects/{id}/analyze`) commits the new Lyrics row — for narration projects, audits boundaries against the new Whisper words and snaps each scene's start/end to the closest phrase boundary when stale.
- After `upload_srt` (`POST /api/projects/{id}/upload_srt`) commits the parsed cues — always snaps narration-mode scenes to cue boundaries (SRT > Whisper precision).

The resync is gated to `narration_video` / `narration_images` projects — music video mode uses LLM-picked cuts and is left alone. Failure is non-fatal: a snap error never breaks the analyze / upload endpoint.

### Added — `/scenes/audit-boundaries` debug endpoint

`GET /api/projects/{id}/scenes/audit-boundaries` returns the same audit dict the auto-resync uses. Per-scene `duration_status` (`ok` / `below_min` / `above_max`), `start_drift_s` / `end_drift_s` against the nearest natural break, and `snap_suggestion` when SRT is present. Used by `diag.py` and any future "fix boundaries" UI; safe to hit from the Settings page to see how many scenes have drifted.

### Why SRT is preferred over Whisper

SRT files come from authoritative sources (ElevenLabs, Aivo, etc.) — the cue boundaries are the *intended* phrasing of the narration, not a probabilistic Whisper guess. When `Lyrics.words` carry the `block` key, every downstream consumer (audit, resync, scene slicing) treats those as ground truth and aligns boundaries to cue starts/ends. Whisper-sourced words still get audited, but with a higher stale-fraction threshold (30%) since Whisper phrase boundaries are noisier and a few manually-tuned scenes shouldn't trigger a wholesale resync.

### Files

- `backend/api/generation.py` — `GenerateVideoRequest.duration` / `AutoGenerateRequest.duration` → `Optional[float] = None`; `_resolve_video_duration` helper at top of module; consumers in `generate_video` (`/generate-video`) and `generate_asset` (`/asset`) updated.
- `backend/services/jobs/dispatcher.py` — sequencer path + i2v path both log + clamp when `duration` is missing/non-positive; falls back to `AppSettings.video_min_duration`.
- `backend/api/scenes.py` — new `_clamp_scene_duration` helper; `POST /scenes` and `PATCH /scenes` both call it; new `GET /api/projects/{id}/scenes/audit-boundaries` endpoint.
- `backend/services/scene_boundaries.py` (new) — SRT-vs-Whisper detection + audit + resync heuristic primitives.
- `backend/api/timeline.py` — `_maybe_resync_scene_boundaries` helper; auto-resync hooks in `analyze_audio` + `upload_srt`.

### Notes

- Old projects with already-drifted scenes need one re-upload of audio (or SRT) to trigger the resync. After that, drift can't accumulate again.
- The dispatcher `WARNING` log line is the canary for any remaining silent-fallback bug. If you see `[<job_id>] Video job has non-positive / missing duration` after this release, that's a caller that needs fixing — please report.

## [1.8.13] - 2026-06-14

### Fixed — Auto-gen stuck at N/M with "status flipped to None"

User reported Auto-Gen sitting at "13 / 77 scenes" forever even though ComfyUI workers were still completing jobs. Backend log showed the windowed-batch poll loop logging `"Windowed batch: exiting poll loop — status flipped to None"` mid-run.

Root cause: a previous Auto-Gen run's 30-minute `_evict_seq_auto_job(pid)` eviction task fired during the current run and popped the live tracking dict entry. The poll loop's status check then returned `None` (not `"running"`) and exited; the queue/dispatcher kept processing in-flight jobs (giving the illusion of partial progress) but no new Pass 1 jobs got submitted.

Fix in `backend/api/generation.py`:

- Stamped a per-run `_evict_id` UUID on `_seq_auto_jobs[pid]` at run start (both `start_sequential_auto_gen` and `_resume_sequential_auto_gen`).
- `_schedule_eviction(pid, evict_id)` passes the token into the eviction coroutine.
- `_evict_seq_auto_job(pid, evict_id)` only pops when (a) the entry still exists, (b) `entry["_evict_id"] == evict_id` (same run we scheduled cleanup for), AND (c) the entry is in a terminal state.
- Stale evictions log `"Eviction for {pid} skipped — entry replaced by newer run"` instead of silently killing the live run.

## [1.8.12] - 2026-06-08

### Added — Scene delete dialog with merge-target selection

User feedback: *"I think we def may want to think through what happens when you scene delete and ask the user if they want the time and lyric to move to the previous or next scene."*

Before: clicking Delete on a scene fired a browser `window.confirm` and the AppLayout client did a quick "expand neighbor + delete" sequence in two API calls. No preview of what was about to be deleted, no choice of where the time slot should go, no re-numbering of `order_index`, no re-slicing of the absorbing scene's audio, no audit log on the absorbing scene.

Now: a proper `SceneDeleteModal` opens with three radio choices.

| Choice | Behavior |
|---|---|
| **Add to previous scene** (default) | `prev.end_time = deleted.end_time` — the previous scene absorbs the deleted time range. Lyrics that fell in the range are picked up automatically (Whisper words are time-anchored, not scene-anchored). |
| **Add to next scene** | `next.start_time = deleted.start_time` — the next scene extends backward to cover the range. |
| **Just delete (leave a gap)** | No neighbor changes. Export pipeline renders the gap as a silent freeze-frame on the previous scene's last frame. |

First/last scene edge cases auto-disable the invalid option. Solo-scene case offers only "leave a gap" with a warning that the project will be scene-less.

### Backend — atomic delete + merge in a single endpoint

`backend/api/scenes.py` `DELETE /api/projects/{pid}/scenes/{sid}`:

- New optional JSON body: `{"merge_target": "previous" | "next" | "gap"}` — defaults to `"previous"` so callers that don't send a body get the same merge semantics as before.
- Loads the project's scenes ordered by `order_index`, finds the deleted scene's neighbors.
- Edge auto-fallback: `previous` on the first scene falls through to `next`; `next` on the last scene falls through to `previous`; solo scene drops to `gap`.
- When merging, the absorbing scene's `start_time`/`end_time` updates and gets two scene-parameter flags so the UI can show "this scene was extended":
  - `extended_via_delete = true`
  - `extended_at = [{from_scene_id, from_scene_name, absorbed_seconds}, ...]` (rolling last 10 entries)
- Best-effort audio re-slice: ffmpeg subprocess cuts a fresh per-scene WAV at the new time range and updates `parameters.audio_clip_path`. Failure is non-fatal — logs a warning and the delete still succeeds; the user can re-run audio analysis to regenerate the clip.
- Cascade delete via the Scene model relationships handles TimelinePosition, StemSelection, GenerationHistory, Job rows automatically.
- After delete, `order_index` is re-numbered on the remaining scenes so the sequence is contiguous (0, 1, 2, …) — no gaps in the index even after multiple deletes.
- All in one DB transaction — the merge, re-slice, delete, and re-number commit together so a failure rolls back atomically.

### Frontend

- **`frontend/src/api/client.ts`** — `deleteScene(projectId, sceneId, opts?: { merge_target })` with new `SceneMergeTarget` type. Defaults to `"previous"` so existing call sites without the new arg keep working.
- **`frontend/src/components/SceneEditor/SceneDeleteModal.tsx`** — new component. Shows the scene's time range + duration, a lyric/narration preview for the deleted span (so the user can see what's about to be absorbed), three radio options with live previews of the resulting neighbor durations, and a note about the asset library + video-mismatch caveat.
- **`frontend/src/components/Layout/AppLayout.tsx`** — `handleDeleteScene` now opens the modal instead of calling `window.confirm`. New `handleDeleteSceneConfirm` callback fires the single backend call with the chosen `merge_target`, then invalidates `['scenes', id]`, `['lyrics', id]`, and `['chapters', id]` so React Query refetches everything that could have been affected by the merge.

### Mode-agnostic

Works the same in `music_video`, `narration_video`, and `narration_images` — the merge logic only touches `start_time`/`end_time` and the per-scene audio clip. No mode-specific branches.

### Verified

- `backend/api/scenes.py` parses OK; one `delete_scene` async def, one `_reslice_audio_for_scene` helper (no duplicates).
- Frontend TypeScript compiles clean.
- The 3 cascade relationships on Scene (TimelinePosition, StemSelection, GenerationHistory, Job) handle child-row cleanup automatically — no manual DELETE statements needed.
- Backward-compat: callers that don't send a body (and there's exactly one — `AppLayout.tsx`, which now always sends the body via the new modal) default to `merge_target="previous"` which matches the prior behavior.
- Solo-scene delete handled (modal shows "leave a gap" with a warning; backend's `effective_target` falls through to `gap` regardless of input).
- First-scene + last-scene edge cases handled both in modal (radio disable) and backend (auto-fallback).

### Changed

- VERSION → 1.8.12. `pyproject.toml`, `backend/main.py` FastAPI version updated.

---

# Changelog

## [1.8.11] - 2026-06-08

### Fixed — Chapter backfill failed for 4 projects at every startup

User reported log spam at backend boot:
```
WARNING: Backfill default chapter for project ... skipped:
(sqlite3.IntegrityError) NOT NULL constraint failed: chapters.description
```
…repeated for 4 different projects, every single startup. The user's affected projects had **no default chapter row at all** — broken chapter UI, no chapter scope filtering, no chapter-scoped Auto-Gen / Export for those projects.

**Root cause** — the startup migration at `backend/services/shortcode.py:311-329` builds the default "Chapter 1" row with an INSERT that omits `description`, `character_focus`, and `style_notes`. These columns were added in 1.8.0 as part of the Chapter Direction Panel and the migration was `ALTER TABLE chapters ADD COLUMN description TEXT DEFAULT ''`. On some users' DBs the column ended up `NOT NULL` without an effective runtime default — likely because the column was first created by a `SQLModel.metadata.create_all` against a fresh DB on a version that already had the field in the model, where SQLModel translated `Field(default="")` to `NOT NULL` but the SQLite engine didn't always honor the Python-side default for inserts that omit the column. Result: `IntegrityError` every time the backfill ran, every startup forever.

**Fix** — `backend/services/shortcode.py` — INSERT now includes the three fields explicitly:
```sql
INSERT INTO chapters (... description, character_focus, style_notes, ...)
VALUES (... '', '[]', '', ...)
```
Works regardless of which schema variant the user's DB has. The 4 projects that have been failing for some time will now get their default chapter created on next backend start, unblocking their chapter UI.

### Diagnostic — what the user's log was telling us

For each affected project, the user was seeing:
```
WARNING: Backfill default chapter for project <pid> skipped:
(sqlite3.IntegrityError) NOT NULL constraint failed: chapters.description
```
Translation: "Backend started. Tried to create a default Chapter 1 for project X. SQLite refused because the description column requires a value and we didn't provide one. Skipping — leaving project X without a chapter." Repeated every boot because the migration tries again every time. The fix makes the INSERT explicit so it succeeds.

The other warnings/errors in the user's log were **expected and harmless**:
- `Worker http://127.0.0.1:8188: Failed to connect ...` — local ComfyUI not running, fine if the user has remote workers (they do: `192.168.68.117:8188` + `192.168.68.106:8188` both connected successfully).
- `Orphan sweep: no stale jobs (>1h old) found` — orphan sweep ran clean, nothing to do.
- `Demucs GPU: CUDA available (NVIDIA GeForce RTX 5070)` — GPU detected, good.

### Verified

- `backend/services/shortcode.py` parses OK.
- INSERT now contains `description, character_focus, style_notes` columns + empty defaults.
- Existing INSERTs that already work (fresh DBs with the column defaults applied) continue to work — adding explicit values is always safe.
- VERSION → 1.8.11. `pyproject.toml`, `backend/main.py` FastAPI version updated.

---

# Changelog

## [1.8.10] - 2026-06-07

### Fixed — Two-pass silently downgraded to single-pass with no characters

User reported: "toggled two-pass off, then back on, now scenes generate with nobody in them." Root cause traced through the pipeline:

1. **SceneEditor** sends generate request with `two_pass: true` and `reference_asset_ids: []` (no per-scene character selection).
2. **`backend/api/generation.py` `/generate-image`** falls back to concept characters: iterates `project.settings["characters"]`, looks up each character's `image_path` against `Asset.rel_path` with **strict equality**. Any subtle mismatch (leading slash, project_id prefix variant, whitespace) → lookup fails → `char_ref_ids` stays empty → `_two_pass_effective = False` → downgrades to single-pass.
3. **Pass 1 job** is created as single-pass `klein_t2i` with no refs. The scene image generates without characters.
4. **No log** told the user WHY two-pass downgraded — looked like the toggle just stopped working.

This is a long-standing latent bug that the user's toggle-off-then-on sequence happened to expose. Same lookup pattern existed in `_resolve_character_asset_ids` used by auto-gen, so auto-gen could hit it too.

### Fix — Forgiving 3-tier character asset lookup

Both lookup sites (`/generate-image` concept fallback + `_resolve_character_asset_ids` for auto-gen) now try in order:

1. **Exact `rel_path == image_path`** (fast path, matches frontend's primary lookup)
2. **Suffix match `rel_path LIKE '%image_path'`** (forgives leading slashes, project_id prefix variants, path encoding differences — matches frontend's `endsWith()` fallback)
3. **Basename-only match `rel_path LIKE '%filename'`** (last resort — if the path structure changed entirely but the filename is intact)

If ALL characters fail to resolve, a **clear warning is logged** so the user can see why two-pass downgraded:

```
Two-pass requested but ALL N character image_path lookups failed.
Paths tried: [...]. Either the characters have no image_path, the
Assets were deleted, or the rel_path doesn't match. Two-pass will
downgrade to single-pass and the image will have no characters.
```

### Verified

- `backend/api/generation.py` parses OK.
- Both code paths affected: `/generate-image` per-scene endpoint AND `_resolve_character_asset_ids` (auto-gen).
- Frontend lookup semantics now match backend so refs survive the request boundary.
- VERSION → 1.8.10. `pyproject.toml`, `backend/main.py` FastAPI version updated.

### Notes

- Existing scenes where the asset resolves successfully via exact match are completely unaffected (fast path unchanged).
- If a user's character's image_path is genuinely missing from Assets (e.g., the asset was deleted), the warning surfaces that fact instead of silently producing a no-character image.
- Combined with the 1.8.9 "respect per-scene characterIndices" change, the system now correctly handles all four combinations: scene-explicit refs, project-default refs, no refs at all, AND the edge case where concept characters have slightly mismatched image_paths.

---

## [1.8.9] - 2026-06-07

### Fixed — Auto-gen no longer force-overrides per-scene character selection

User reported that two-pass was firing on scenes that shouldn't need it. Root cause: auto-gen Phase 1 was unconditionally writing `image_refs_first.characterIndices = [0, 1]` (first 2 project chars) onto every scene before computing refs. This overwrote any per-scene character selection the user had made (including the legitimate "no characters on this scene" case = empty list), so every scene ended up with refs → every scene got two-pass → 2 image renders per scene whether the user wanted that or not.

**`backend/api/generation.py`** in TWO auto-gen paths (`_run_windowed_batch` Phase 1 + the sequential auto-gen loop):

- **Reads existing `image_refs_first.characterIndices` first.** If the user has an explicit per-scene selection — including an empty list meaning "no characters on this scene" — use ONLY those characters.
- **Falls back to "first 2 project chars" only when the field is absent** (truly new scene with no prior selection). Brand-new scenes still get a sensible default.
- **Stops overwriting `image_refs_first` on scenes that already have one.** The auto-gen no longer touches the field unless it's missing.

End-to-end effect:

| Scene state | Old behavior | New behavior |
|---|---|---|
| User selected 1 character on scene | overwritten to use first 2 → two-pass with 2 chars | uses 1 char → two-pass with 1 char ✓ |
| User selected NO characters (empty list) | overwritten to use first 2 → two-pass | NO chars → no refs → **single-pass** ✓ |
| Brand-new scene, no selection | uses first 2 → two-pass | uses first 2 → two-pass (unchanged) |
| No project characters configured | empty refs → single-pass (unchanged) | empty refs → single-pass (unchanged) |

This is the rule the user asked for: **"two-pass runs if the scene has references; if the scene has no references, no two-pass — regardless of the modal checkbox."** The two-pass checkbox is now an UPPER bound (turn it OFF to disable two-pass entirely), not an override that forces refs to appear.

The downstream short-circuits in `_apply_two_pass_to_job_params` (`if not two_pass or not ref_ids: return params`) were already correct — the bug was that ref_ids was always non-empty due to the unconditional overwrite. Both layers now agree.

### Changed

- VERSION → 1.8.9. `pyproject.toml`, `backend/main.py` FastAPI version updated.

### Verified

- Backend Python parses OK; frontend TypeScript compiles clean.
- Three patches applied: windowed-batch resolution, sequential-path resolution, both characterIndices override sites guarded with `"image_refs_first" not in scene_params`.

---

## [1.8.8] - 2026-06-07

### Fixed — User-reported regressions

**Project deletion failed with `sqlalche.me/e/20/gkpj` (IntegrityError)** — the GlobalCharacter table I added in 1.8.6 declares `source_project_id` with `ondelete="SET NULL"`, but `SQLModel.metadata.create_all` only creates MISSING tables, never alters existing ones. Users whose DB was created before that fix had the old constraint shape and FK enforcement blocked the project cascade delete.

- **`backend/api/projects.py` `delete_project`** now pre-nulls `source_project_id` on every GlobalCharacter row referencing the project, BEFORE running the cascade delete. The cached `source_project_name` on each library entry preserves attribution after the project is gone (matches the "copy semantics — library entry outlives source project" design from 1.8.6). Works regardless of which DB schema variant the user is on.

**Two "Enable Model-Generated Audio" checkboxes on the Concept tab** — leftover from the AV-native checkbox patch being re-applied during an Edit-tool truncation repair earlier in this session. ConceptPanel had two complete blocks; one had stale wording ("scenes whose Video tab opt in..." — outdated since the master toggle is now the source of truth).

- **`frontend/src/components/ConceptPanel/ConceptPanel.tsx`** — removed the older duplicate block (2,329 chars). Single canonical Enable Model-Generated Audio toggle remains.

**Full Pipeline Single Image autogen silently generated 2 images per scene** — the AutoGenerate modal's `twoPass` checkbox defaulted to ON, so every scene with character refs got Pass 1 (base) + Pass 2 (composite). User got 2× the rendering work without asking for it. Same default in BatchItemAddModal.

- **`frontend/src/components/Layout/AppLayout.tsx:3125`** — `useState(true)` → `useState(false)`.
- **`frontend/src/components/BatchMode/BatchItemAddModal.tsx:20`** — same change. Two-pass is now strictly opt-in.

**"Drew an image without making a prompt for it first"** — auto-gen Phase 1's empty-prompt fallback chain was `scene.prompt or f"Scene {scene.order_index + 1}"`. When LLM enhance failed (timeout, misconfig, etc.) AND the scene had no prompt, the literal string `"Scene 7"` was sent to Klein and produced garbage. The `flow_idea` field generated earlier by `_ensure_video_flow` was being IGNORED in this fallback path.

- **`backend/api/generation.py` `_run_windowed_batch` Phase 1** — new fallback chain:
  1. `scene.prompt` (user-edited or successfully enhanced)
  2. `scene.parameters.flow_idea` (from story flow generation)
  3. SKIP the scene with a clear warning + status update if only the literal `"Scene N"` placeholder remains.

  When skipped, the status text shows `"skipped {scene_name} (no prompt / flow idea, LLM enhance failed)"` so the user knows exactly why. Re-running after fixing LLM config or writing a manual prompt picks the scene up cleanly. Saves the user from a wasted render that would have produced an image of "Scene 7" rendered literally.

### Changed

- VERSION → 1.8.8. `pyproject.toml`, `backend/main.py` FastAPI version updated.

### Verified

- All backend Python files parse OK.
- Frontend TypeScript compiles with zero new errors.
- Concept tab now has exactly 1 `Enable Model-Generated Audio` occurrence (was 2).
- Both `twoPass` defaults flipped to false; comment in source explains the opt-in rationale.
- Empty-prompt skip path in generation.py has `_prompt_is_placeholder` guard + `flow_idea` fallback (4 mentions in code).

---

## [1.8.7] - 2026-06-07

### Fixed — Auto-gen drain loop waited 30 min for ghost jobs

User reported "auto-gen ran one video successfully then stopped, status panel kept polling." Root cause traced to the post-Phase-2 drain loop: it polled the DB for any `PENDING`/`RUNNING` jobs on the in-batch scene IDs and waited them out — but had no time filter, so it would happily wait on orphaned jobs from PREVIOUS sessions whose ComfyUI workers were long gone. The drain would only give up after the 30-min `batch_timeout` fired.

- **`backend/api/generation.py` `_run_windowed_batch` drain query** now filters by `Job.created_at >= run_started_at`, so the drain only waits on jobs THIS run created. Two-pass composites and transition clips spawned during the main loop pass the filter; pre-existing orphans don't.
- **`backend/main.py` lifespan startup** now sweeps PENDING/RUNNING jobs older than 1 hour and marks them FAILED. `recover_running_jobs()` still handles fresh-restart reconnect (keeps RUNNING-with-prompt_id alive); the new sweep only cleans up stale orphans that recover left behind.

### Fixed — Image movement override discarded user's "static" choice

User changed scenes from `zoom_in_center` to "static" in the UI; the change persisted to the DB; export still rendered Ken Burns. This wasn't a cache bug — even a force-recreate produced movement.

- **`backend/api/export.py`** lines 607 + 641: removed the `"effect": effect if effect != "none" else "zoom_in_center"` override that was silently replacing the user's "none" choice with a default.
- **`backend/services/video/assembly.py`** `to_common()`: when effect is "none" / empty, sets `effect = "static"` (new value) instead of coercing to `zoom_in_center` with `intensity=0` (which still ran zoompan and could produce subtle motion).
- **`backend/services/video/ffmpeg.py`** `apply_kenburns()`: new static early-return path emits a clean image-to-video clip with NO zoompan filter — just `scale + pad + setsar + format` held for `duration` seconds, with explicit `-frames:v` for frame-exact splice timing.

### Fixed — Project-wide "Enable Model-Generated Audio" toggle now actually project-wide

User flipped the master AV-native toggle on the Concept tab, expecting every video in the project to use it. The dispatcher was requiring BOTH the project gate AND the per-scene checkbox.

- **`backend/services/jobs/dispatcher.py`** `_build_workflow` AV-native routing: changed `if _scene_av and _proj_av` → `if _proj_av or _scene_av`. Master toggle is now the single source of truth; per-scene checkbox is a secondary opt-in when the master is off.
- **`frontend/src/components/ConceptPanel/ConceptPanel.tsx`**: updated copy ("every I2V video render in this project will use AV-native") and added explanatory hint about per-scene fallback.
- **`frontend/src/components/SceneEditor/SceneEditor.tsx`** Video tab: per-scene checkbox now renders in a purple highlighted box with a `🔒 forced ON by project setting` badge when the master is on. Tooltip explains the gate hierarchy.

### Fixed — Auxiliary saveConcept calls were dropping 5 critical settings

User reported settings "reverting" intermittently. Root cause: ConceptPanel had FIVE saveConcept call sites but only TWO included the full payload. The other three (auto-save after adding character, auto-save after editing character, auto-save after generating character image) omitted `global_color_override`, `custom_color_palette`, `global_image_color_filter`, `enable_model_audio`, `model_audio_volume`. Every character-related auto-save silently flipped the master AV-native toggle back to OFF and wiped color palette.

- **`frontend/src/components/ConceptPanel/ConceptPanel.tsx`**: all 3 incomplete saveConcept call sites now include the 5 missing fields.
- **`frontend/src/api/client.ts`** `saveConcept` type extended to include the new fields.

### Fixed — Export cache key was missing color filter + per-scene dims

User changed a per-scene color filter; export reused the stale concat.mp4 because the cache key didn't hash `color_filter`. Same bug pattern as the "static" override.

- **`backend/services/video/assembly.py`** `_video_cache_key()`: scene payload now includes `cf` (color filter) + `iw`/`ih` (per-scene image dimensions). Changing any of these now correctly invalidates the cache and forces a fresh render.

### Fixed — Multiple silent-failure paths from the deep audit

**BLOCKING**

- **Pass 2 commit rollback** (`dispatcher.py` `_download_and_save_outputs`): when `_create_two_pass_composite_job` raises after Pass 1 is already saved, scene now gets `two_pass_composite_failed=true` flag + truncated error so the UI can surface a "Pass 2 failed — retry?" affordance. Previously Pass 1 would show as completed with no indication that Pass 2 never ran.
- **Whisper empty transcription raises instead of swallowing** (`backend/services/audio/analysis.py`): when both the full audio AND the vocal-stem fallback produce no meaningful words, the code now raises `RuntimeError` with actionable text. Previously it silently set `transcription = []` and the export would produce zero subtitles with no error.

**HIGH**

- **LLM calls wrapped in `asyncio.wait_for(timeout=180)`** in `backend/api/concept.py` (5 sites) + `backend/api/timeline.py` (1 site). Stalled LLM HTTP requests can no longer hang the request task forever.
- **Demucs timeout scales with audio duration** (`analysis.py:365`): `_demucs_timeout = max(1800, int(audio_dur * 2))`. A 2-hour narration on CPU now gets 4 hours of timeout instead of failing at 30 minutes.
- **Dispatch-time parameter validation** (`dispatcher.py` `_build_builtin_workflow`): raises `ValueError("Dispatch refused: ...")` BEFORE sending to ComfyUI when `width/height/duration <= 0`. Stops the silent 0-frame video / corrupt image output. Excludes `ltx_transition` (auto-derives dims).
- **Audio-only remix duration check** (`assembly.py` `_load_cached_concat` site): when `audio_only_remix=True`, the cached concat's actual duration is probed via ffprobe and the cache is dropped if `abs(actual - expected) > 0.5s`. Protects against interrupted-write manifest mismatches.
- **ConceptPanel unsaved-edits guard** (`ConceptPanel.tsx:213`): `useEffect([conceptData, dirty])` now only re-hydrates local state when `!dirty`. Background refetches (library import, etc.) no longer silently wipe in-progress user edits.

### Notes

- VERSION → 1.8.7. `pyproject.toml`, `backend/main.py` FastAPI version updated.
- Backend Python files all parse OK; frontend TypeScript compiles with zero new errors.
- All fixes are net-positive correctness with no behavior change for the happy path — they only kick in on the edge cases that previously failed silently.

---

## [1.8.6] - 2026-06-06

### Added — Global Character Library (reusable across projects)

For users building a series of related content (multiple music videos with the same protagonist, episodic narrations with recurring characters, etc.), characters can now be saved to a project-independent library and re-imported into any other project.

- **"💾 Save As Asset" button** on the Character Edit modal (footer). Click → opens a small dialog where you optionally add comma-separated tags ("protagonist", "noir", "fantasy"), then "Save to Library". The character's main image, description, prompt, all reference images, and the source project's name are copied into the global library folder so the entry is fully portable. Disabled when the character has no main image yet.
- **"🎭 Library" button** next to the Concept tab's "Add" character button. Opens a browse modal showing every saved character as a thumbnail grid with name, tags, and source-project attribution. Filter bar: name/description search + clickable tag chips ("All" + every distinct tag). When multiple source projects are represented, a left sidebar groups counts by project.
- **"+ Add to project"** on each library card copies the character into the current project's `settings.characters` list. **Copy semantics** — once imported, the project copy is fully independent: editing it does NOT touch the library entry, and updating the library entry does NOT push changes into projects that already imported it. Matches how stock-photo / clipart libraries work; least surprising for users.
- **Storage layout** — `{project_dir}/_global_characters/{id}/` holds the main image, plus `refs/` subfolder for reference images. The leading underscore prevents collision with user-named projects. Moving your `project_dir` brings the library along automatically.
- **Source project attribution** — `source_project_id` (FK, nullable) + `source_project_name` (cached at save time). If the source project is deleted, the library entry keeps the cached name so attribution survives.

### Added — backend API surface

`/api/global-characters`:
- `POST` — create from a payload (name, description, image_path, prompt, refs, tags, source_project_id). Copies files into the library folder.
- `GET` — list with `?search=` / `?tag=` / `?source_project_id=` filters.
- `GET /tags` — distinct tag list, sorted (for tag chip picker).
- `GET /{id}` — detail.
- `PUT /{id}` — update name / description / tags only.
- `DELETE /{id}` — removes DB row, folder, and version history. Does NOT affect projects that already imported the character.
- `GET /{id}/versions` — list version snapshots (frontend version-history UI is a follow-up).
- `POST /{id}/import` — copy into a target project. Returns the new `character_index` so the UI can scroll to the imported entry.

### Added — DB tables (auto-created on next backend start)

- `global_characters` — id (UUID PK), name (indexed), description, image_path, last_prompt, reference_images (JSON list), tags (JSON list), source_project_id (FK → projects.id, nullable, indexed), source_project_name, created_at, updated_at.
- `global_character_versions` — id (UUID PK), global_character_id (FK indexed), image_path, prompt, reference_images (JSON list), note, created_at. Populated when a library entry is regenerated (future "regenerate from library" flow).

No migration needed — `SQLModel.metadata.create_all` adds the new tables idempotently on first startup after upgrade. Existing data is untouched.

### Frontend

- `frontend/src/api/client.ts` — new `GlobalCharacter` + `GlobalCharacterCreate` types; client methods for list/create/delete/import + tag list.
- `frontend/src/components/ConceptPanel/CharacterCreatorModal.tsx` — Save As Asset button + tag-input sub-dialog.
- `frontend/src/components/ConceptPanel/GlobalCharacterLibraryModal.tsx` — new browse + import modal (search, tag filter, project group sidebar, grid with thumbnail/name/tags/source, + Add to project / 🗑 delete buttons).
- `frontend/src/components/ConceptPanel/ConceptPanel.tsx` — wires the 🎭 Library button + renders the modal.

### Notes — what's NOT in this cut

These were deliberately deferred to keep the v1 contained:
- **Version-history UI** in the browse modal (backend stores versions; the modal doesn't expose them yet).
- **"Regenerate from library"** — re-create variations using the saved prompt + refs.
- **Re-sync** — push an updated library entry into a project that previously imported it.
- **Folders** on top of tags — current organization is tag-based + auto-recorded source project.

The DB schema already supports versioning + attribution, so adding the UI later is purely frontend work.

### Changed

- VERSION → 1.8.6, `pyproject.toml`, `backend/main.py` FastAPI version updated.

---

## [1.8.5] - 2026-06-06

### Added — Model-Generated Audio (LTX 2.3 AV-native)

LTX 2.3 has a native AV-latent pipeline that produces audio (speech / SFX / ambient) in the same forward pass as the video — but only when the audio input is left unconditioned. Until now we always conditioned with the project's narration / backing audio, which trains the model toward lipsync but throws away the generative audio path entirely. New feature lets scenes opt into the unconditioned path so the model fills in its own sound.

- **New ComfyUI workflow** `workflows/LTX-2-3_AV_NATIVE.json` — derived from the I2V workflow with the audio-input chain surgically removed (`LoadAudio` / `LTXVAudioVAEEncode` / `SetLatentNoiseMask` / `TrimAudioDuration` / its int-to-float helper all dropped, 53 nodes total). The audio_latent switch now hardwires the empty-latent path so the sampler denoises audio from pure noise; the output audio switch hardwires the model-decoded path so the VHS_VideoCombine mux uses what the model produced. The "Audio - Video Duration" int constant is repurposed as the user-controllable "Video Duration (seconds)" since there's no input audio to derive it from.
- **Registration** in `backend/services/comfyui/defaults.py` as workflow_type `ltx_av_native` (name "LTX 2.3 - AV Native (model generates audio)"). Routed through the existing capabilities map (`{"ltx"}`) and model-requirements map (resolved to `video_model_type`, same as every other LTX flavor).
- **Dispatcher routing** in `_build_workflow.` When the project has `enable_model_audio` AND the scene's parameters say `use_model_audio`, any `ltx_i2v` job is auto-swapped to `ltx_av_native` and `skip_audio_mux=True` is forced. The swap happens at dispatch time rather than at submission time, so every code path that creates an `ltx_i2v` job (interactive Video tab, Auto-Gen, Batch Mode) gets AV-native routing for free without touching the submission sites.
- **Post-download audio extraction** in `_download_and_save_outputs`. When the completed video came from `ltx_av_native`, we ffprobe for an audio stream and (if present) ffmpeg-extract it to a sidecar WAV (48 kHz / 16-bit PCM / stereo) at `<video>.model_audio.wav`. The relative path is stored on `scene.parameters.chosen_model_audio_path` so the mixer can later route the channel independently of the muxed MP4. New helper `extract_audio_track()` in `backend/services/video/ffmpeg.py` does the probe + extraction with conservative fallbacks (empty / tiny WAVs return False so the assembler knows the scene has nothing to layer in).
- **Concept tab UI** — new "Enable Model-Generated Audio (LTX 2.3 AV-native)" checkbox + "Model Audio Mixer Volume" range slider (0–2×, 0.05 step) in `ConceptPanel.tsx`. Hidden when the global toggle is off so users don't get confused about why the per-scene checkbox is doing nothing. Saves through `concept.py` `ConceptData` fields `enable_model_audio: bool` and `model_audio_volume: float` (clamped to 0..2 server-side).
- **Per-scene Video tab UI** — new "Let model generate its own audio" checkbox in `SceneEditor.tsx` Video tab. Disabled (greyed + tooltip "Enable Model-Generated Audio on the Concept tab first") when the project gate is off, so the dependency is discoverable from the scene editor without having to navigate back.
- **Scene playback** of any AV-native scene immediately reflects the model audio in the per-scene preview because it's baked into the MP4 (no mixer plumbing required for single-scene preview). The full-export mixer integration that respects `model_audio_volume` is staged in but not wired into the assembly pipeline yet — follow-up to use `chosen_model_audio_path` as a 4th channel layered on top of the narration + backing mix.

### Changed

- README + VERSION bumped to 1.8.5. `pyproject.toml`, `backend/main.py` FastAPI version updated.

### Notes

- The AV-native model needs LTX 2.3's `LTX23_audio_vae_bf16.safetensors` VAE installed on your ComfyUI server (same file the existing I2V workflow uses for audio decoding — already required by your current setup).
- Workflow does NOT apply lipsync — there's no input audio to sync to. The "Lipsync" toggle on Video tab is independent and only affects non-AV-native jobs.
- Narration-images mode hides the per-scene checkbox (video gen is disabled in that mode entirely).

---

## [1.8.4] - 2026-06-06

### Fixed — auto-gen reliability + observability (the big one)

Most reported "auto-gen stuck doing nothing" reports tracked to **three independent silent-failure paths**, all now caught:

- **Phase 1 FF image failure used to kill the entire run.** A single first-frame image timing out or failing in Phase 1 set `_seq_auto_jobs[pid].status = "failed"` and `return`ed, killing a 23-scene run because of one bad scene. Now logs `SKIPPING this scene and continuing with the rest of the batch`, records the failure in BatchRun's error log, and `continue`s to the next scene so the other 22 still process. (`backend/api/generation.py` `_run_windowed_batch` Phase 1 FF wait path)
- **`_ensure_video_flow` LLM calls had no timeout.** Run pre-step that generates story-flow ideas could hang indefinitely if the LLM provider stalled, leaving the modal frozen at `current_step = "starting"` and `0/N` with no log activity. Each `_call_llm` invocation (single-shot + each concurrent batch) now wrapped in `asyncio.wait_for(..., timeout=180.0)`; the outer call gets a 10-minute backstop. Status text now updates to `"checking story flow ideas..."` then `"generating story flow ideas for N scenes (LLM)..."` before the LLM work so the user can see the step is active. Timeout falls through to raw prompts so Phase 1 always reaches scene gen.
- **Phase 2 main loop exited if `active_jobs` briefly empties.** Loop condition was `while active_jobs and elapsed < timeout`. Between a completed job and the refill attempt, `active_jobs` could go to 0 momentarily; if anything (transient DB lock) caused the refill to fail, the loop terminated. Loop now: `while (active_jobs or next_to_submit < total_eligible) and elapsed < batch_timeout` — exits ONLY when all eligible submitted AND nothing in flight.

### Added — diagnostic logging for every wait path

Silence in the log used to be indistinguishable from "running fine but slow." Now every wait point has a heartbeat:

- **Phase 1 per-scene log line** `Phase 1 [N/M]: 'scene_name' (elapsed=Xs total)` on every iteration entry
- **`_wait_for_job` heartbeat** every 30s: `_wait_for_job heartbeat: job=<uuid> status=PENDING|RUNNING elapsed=Xs/Ys`
- **Phase 2 main-loop heartbeat** every 20s: `Windowed batch heartbeat: tick=N, active=X, submitted=Y/Z, done=W, elapsed=Ts`
- **Phase 2 START log** at handoff: `Windowed batch Phase 2 START: mode=X, eligible=N`
- **Status text updated at every transition** so the modal shows what we're waiting on, e.g. `"waiting for FF image of Scene 4 (scene 4/23)"`, `"dispatching (N scenes ready, submitting first batch...)"`, `"generating (X active, Y/Z complete)"`

### Fixed — multi-fault tolerance throughout the dispatch pipeline

`_submit_next` increments `next_to_submit` BEFORE the DB write. Failures used to leak this counter — the failed eligible entry was permanently SKIPPED. All four sites now roll back on exception:

- **Initial fill** (`for _ in range(window_size)`) — on `_submit_next` exception, decrement `next_to_submit` and continue trying the next slot
- **Main-loop top-up** — tracks `_topup_failures_this_tick`, tolerates up to 3 failures per tick with 0.5s backoff, rolls back `next_to_submit` on each failure
- **Rescue pass** (runs when main loop exits with un-submitted entries) — tolerates up to 5 cumulative failures with 1s backoff, rolls back on each
- **Self-healing top-up** runs UNCONDITIONALLY every 2-second tick (no status-running gate) — `len(active_jobs) < window_size` is enough to trigger another refill attempt

### Changed — audio normalization target -16 → -14 LUFS

`backend/services/video/ffmpeg.py` `normalize_audio()` default target. Old -16 LUFS = broadcast/film standard; sounded "super quiet" vs every streaming platform (Spotify, YouTube, Apple Music, TikTok all use -14). Voice-heavy programs suffered extra because integrated loudness drops further with pause gaps. Both code paths (post-assembly for music_video, in-assembly for narration_video) now hit -14 LUFS when "Normalize audio" is enabled. True-peak ceiling of -1.5 dBTP unchanged.

### Added — FFmpeg image color filter (B&W / Grayscale / Sepia)

Independent of the LLM Color Override (which steers the prompt). This filter runs FFmpeg over the generated image AFTER the model produces it for a deterministic pixel transform.

- **Concept tab** — new "Force Color Filter on Generated Images (FFmpeg)" dropdown: `Off / Black & White (high contrast) / Grayscale (desaturated) / Sepia Tone`. Off by default.
- **Per-scene Image tab** — same dropdown with `Inherit from project (Off/B&W/etc.)` as default; explicit `Off` overrides project default for one scene.
- **Backend** — `apply_image_color_filter(input, output, mode)` in `backend/services/video/ffmpeg.py` (B&W = `hue=s=0,eq=contrast=1.25`, Grayscale = `hue=s=0`, Sepia = standard ImageMagick matrix). Tempfile + atomic move so in-place is safe. Called from `backend/services/jobs/dispatcher.py` after every image download.

### Fixed — character edit persistence + asset picker

- **Choose from asset library OR upload** added to the character image source (was: only Klein generation). Single "🖼️ Choose Asset / Upload" button right under Generate opens the asset picker with both tabs. Picked asset goes through the same `setActiveMutation` as "Set as Active" on a generated version.
- **Description + prompt + reference images persist** across save/close. `CharacterModel` Pydantic in `backend/api/concept.py` had only `name/description/image_path` — Pydantic silently stripped `last_prompt` and `reference_images` on every save. Added both as optional fields; modal hydrates them on mount; `handleSaveAndClose` passes them back through `onSave`. Reopen a character and the prompt + reference list are exactly as you left them.

### Fixed — color override + scene navigation

- **Scene Editor "Default Color Palette" inheritance label** showed "(no project default set)" even after saving on Concept tab. Cause: ConceptPanel only invalidated `['concept', projectId]` query but Scene Editor reads from `['project', projectId]` (`currentProject.settings`). All six save-related invalidations now invalidate both queries — Scene Editor's inheritance label updates immediately after any concept save.
- **Scenes panel** — clicking a scene title now navigates the Timeline to that scene's start position + sets it active + pauses playback. Title is its own button with hover state and tooltip `"Go to {scene name} in the timeline"`. Whole row still works for users who don't notice.

### Added — generation queue model + phase chips

Each in-flight job item in the Generation Queue panel now shows up to three header chips:
- **Pass 1/2 badge** (blue) — when `two_pass_phase` is set, with tooltip explaining each phase
- **Model badge** (color-coded) — `Z-Image Turbo`, `Klein 9B · 3REF`, `LTX 2.3 · I2V`, etc., derived from `job.parameters.workflow_type` (ground truth after Pass-1 Z-Image redirects). Raw workflow_type in tooltip.
- **Existing worker badge** + scene name unchanged

### Added — batch screen live active-jobs panel

`backend/api/batch_runs.py` `BatchRunDetail` response now includes `active_jobs[]` — live snapshot of every RUNNING job in the project with per-job progress %, current ComfyUI node, worker URL, scene name, two-pass phase, and workflow_type. Dispatcher writes into in-memory `_live_job_progress` dict on every WebSocket progress event; cleared on `mark_done`/`mark_failed`. Batch detail screen renders an "Active workers (N)" panel under current_step with progress bars updating live. 5-minute LTX renders no longer look "stuck" — you see the percentage climb.

### Added — persistent auto-gen status across browser refresh

`/auto-sequential/status` now falls back to the most recent `BatchRun` row for the project when the in-memory `_seq_auto_jobs` dict is empty (eviction, backend restart, etc.). Reload the project page mid-run and the status pill + modal both repopulate. The DB read only fires when in-memory has nothing — the polling hot path during active runs stays DB-free.

### Fixed — SQLite "database is locked" contention during auto-gen

`/auto-sequential/status` endpoint now reads from in-memory dict only (no DB read on the polling hot path). Was opening a session + doing a SELECT on projects every poll; under 3-second polling × heavy auto-gen writes the polling SELECTs starved the dispatcher writes for up to 60 seconds. Frontend polling also bumped 3 s → 5 s.

### Added — Klein workflow reverted to Turbo/distilled params (style preservation)

User-supplied known-good 4REF workflow surfaced five drifted values in all five `KLEIN_EDIT_ULTRA_WORKFLOW_{1..5}REF.json` files. Reverted:
- `Flux2Scheduler.steps` 20 → **4**
- `CFGGuider.cfg` 5 → **1**
- `ImageScaleToTotalPixels.upscale_method` `lanczos` → **`nearest-exact`**

At CFG=5 + 20 steps Klein follows the text prompt aggressively and drifts from references — exactly the "Pass 2 overtakes the style" symptom users reported. Turbo config (4 steps, CFG=1) is what Pass 2 character compositing needs.

### Added — "Use Existing Prompts — Just Render" auto-gen toggle

Advanced option in the Auto-Gen modal. When ON, scenes with a non-placeholder prompt are NOT re-enhanced — auto-gen renders them with the existing text. Blank scenes still get a fresh enhancement. Useful for re-runs after you've curated prompts manually — saves LLM tokens and preserves your edits. Backend threads `skip_existing_prompts` through 14 enhance call sites with a shared `_should_enhance(skip_existing, current)` helper.

### Fixed — klein_6ref crash on Pass 2

Klein ships 1REF through 5REF workflows only. Scene image always claims slot 1, so character refs are now clamped at 4 (klein_5ref max). Extras dropped with a warning showing which IDs got cut. Also fixed `_apply_two_pass_to_job_params` to only stash **character** refs (not scene "extras" like location/prop refs) into `two_pass_character_ref_ids` — extras were getting mis-classified as characters and counted toward the ref limit.

### Changed — story flow generation batching threshold 20 → 10

`backend/api/concept.py` flow-gen now batches anything over 10 scenes concurrently instead of doing one big synchronous LLM call. A single 20-scene OpenAI call routinely takes 60-90 seconds and exceeded the frontend's 60s axios timeout. Three concurrent batches of 10 finish in ~25-35s. Frontend `generateVideoFlow` also got a `timeout: 300000` (5 min) safety cap.

### Added — per-character last_prompt + reference_images persistence

Already covered in character edit section but worth restating: characters now save their generation context across sessions, so editing-and-regenerating doesn't require re-typing.

---

## [1.8.3] - 2026-06-04

### Added

#### Per-worker model assignment (multiselect under Image / Video checkboxes)
- **Settings → ComfyUI Servers** now lets each worker be restricted to a specific subset of models — useful when one machine runs Klein but another runs LTX, or you keep a "fast" T2I box separate from a "slow but accurate" 2-pass composite box. Below each Images/Video checkbox is a chip multiselect with an **ALL** option (default) plus every preset from the Generation Models section (`flux2_klein_dev_9b`, `flux1_dev`, `z_image`, `qwen_edit`, `z_image_turbo` on the image side; `ltx_2.3`, `wan_2.2` on the video side, plus any custom model names you've set). When ALL is active the worker accepts every model in its enabled category; selecting one or more chips constrains routing to only those models. Toggling the category checkbox OFF hides its multiselect entirely
- **Backend wiring** — `comfyui_server_caps` JSON now stores `{url: {image, video, image_models[], video_models[]}}`. Shared helper `apply_user_caps(worker, caps_config)` lives in `backend/services/comfyui/dispatcher.py` and is used both at startup (`main.py` lifespan) and on Settings save (`api/settings.py` resync), so the on-disk JSON, the dispatcher's `ComfyWorker.capabilities`, and `ComfyWorker.models` always agree. An empty `image_models` / `video_models` list = ALL (worker.models stays empty so `select_worker` treats it as unconstrained — existing semantics preserved)
- **Dispatch-time routing** — `JobDispatcher._get_required_models(workflow_type, app_settings)` now resolves the workflow_type family to the user-facing model the user has selected on the Settings screen: Klein workflows → `AppSettings.image_model_type`, Z-Image redirects → `AppSettings.single_image_generator`, LTX workflows → `AppSettings.video_model_type`. AppSettings is read once per dispatch from the same async session; on the rare DB-unavailable path the dispatcher falls back to the historical FLUX/LTX markers so no job is ever blocked. Custom model strings the user types into the Generation Models section are honored end-to-end

#### Per-job-type resolution split (image vs video)
- **Concept tab — new "Image Generation Size" and "Video Generation Size" controls** under the existing Desired Resolution picker. Image jobs (Klein / Z-Image) and video jobs (LTX 2.3) can now render at different resolutions. The unified Desired Resolution remains the master default; both per-type fields are 0 / blank by default, falling through to it. Rationale: Klein composites need larger images for cleaner Pass 2 character compositing, while LTX video benefits from smaller per-frame sizes and is usually upscaled after generation
- **Backend wiring** — `backend/api/concept.py` `ConceptData` model + GET/PUT extended with `image_resolution_width/height` and `video_resolution_width/height`. `backend/api/generation.py` `_run_sequential_auto_gen` resolves `img_w/img_h/vid_w/vid_h` at the top, passes them through `_run_windowed_batch`, and every per-scene IMAGE job uses `img_w/img_h` while every per-scene VIDEO job uses `vid_w/vid_h`. Character autogen in `concept.py` also picks up the image-resolution split
- **Frontend wiring** — `frontend/src/components/ConceptPanel/ConceptPanel.tsx` exposes both fields with placeholder hints showing the current unified value. `frontend/src/api/client.ts` types both `getConcept` return and `saveConcept` arg extended

#### Project Text Data Import / Export
- **New 3-dot menu item "📤 Import / Export Project Text Details"** available on all project modes. Opens a two-tab modal:
  - **Export tab** — pretty-printed JSON of every editable text field in the project: concept (title, concept, style, image direction, color palette), characters (names + descriptions), chapters (descriptions, character focus, style notes, nesting), scenes (timing, transcribed text, image prompt, video prompt, story flow idea, character references by name, transitions, image movement, per-scene resolution override), resolution settings, source script / lyrics initial text. Buttons: **Copy to Clipboard**, **Download .json**
  - **Import tab** — paste / upload JSON. Radio toggle for **Override all matching fields** vs **Fill only missing fields**. Optional **Accept project-mode mismatch** checkbox. Per-stat result panel after apply (chapters touched / scenes touched / characters added / characters updated / video fields dropped / scenes skipped out of range)
- **Footer links on the modal** — **📄 Download example JSON for this mode** + **📖 View LLM instructions for this mode**. Both auto-target the current project's mode so users get the right file with one click
- **Backend service** `backend/services/project_text_io.py` — pure logic: `build_export(project, session)` and `apply_import(project, session, payload, mode, accept_mode_mismatch)`. Mode-aware (drops video-only fields for narration_images), character lookup by name (case-insensitive), chapter lookup by integer `order`, scene lookup by `order_index`. Validates schema version. Round-trip safe: `override_resolution`/`width`/`height` per-scene resolution overrides persist through export → edit → import
- **Backend endpoints** `backend/api/projects.py` — `GET /api/projects/{id}/text-export`, `POST /api/projects/{id}/text-import`
- **Static assets** bundled in `frontend/public/`:
  - `examples/narration_video.json`, `examples/narration_images.json`, `examples/music_video.json` — fully-filled 1–2 chapter, 2 scene example projects per mode (so an agent has a real template to pattern-match)
  - `docs/narration_video_llm_instructions.md`, `docs/narration_images_llm_instructions.md`, `docs/music_video_llm_instructions.md` — per-mode agent contracts: complete schema table, output rules, common patterns, do's-and-don'ts, mode-specific guidance (period accuracy for narration, lyric-literal visualization for music_video, etc.). Drag the right file into an LLM and it knows what to do
- **Per-scene `narration_text` / `lyrics_text` populated from Whisper words** — the export now extracts the transcribed words that overlap each scene's time range so the LLM agent sees the ground-truth spoken content per scene, not just the full script

### Changed

#### Image generation quality
- **Pass 2 composite context now anchors to style settings** (`backend/services/jobs/dispatcher.py` `_build_two_pass_composite_prompt`). The Klein composite prompt builder now folds `project.settings.image_direction` (or `custom_image_direction`) and per-scene `color_override` (with global fallback) into the LLM context. Previously these style anchors were missing, so the LLM drifted to generic "cinematic, vivid" descriptors that Klein rendered as overexposed composites — visibly washed out / "super bright" in user reports. The same `MANDATORY COLOR PALETTE OVERRIDE` directive used by single-pass image enhance now fires for Pass 2 too
- **`TWO_PASS_BASE_SYSTEM_PROMPT` updated for Z-Image Turbo** (`backend/services/llm/prompt_enhancer.py`). The prompt opened with "You are an expert at writing prompts for FLUX.2 Klein 9B" — but with the always-Z-Image-for-Pass-1 rule from 1.8.1, Pass 1 actually runs Z-Image. Updated:
  - Opening identifies Z-Image Turbo as the Pass 1 model and explains Pass 2 will composite characters via Klein
  - New `EXPOSURE / DYNAMIC RANGE` section explicitly forbids stacking "ultra-bright, brilliant, luminous, glowing, radiant, sun-drenched, dazzling, blazing" superlatives that push Z-Image into highlight clipping
  - Requires natural / balanced lighting unless the script explicitly calls for extreme brightness; "Shadows, depth, and contrast are essential"
  - Prefers specific motivated light sources ("a single window at dusk", "candlelight", "overcast soft-box") over generic "bright" descriptors
  - Music-video-only wording removed so the same prompt works correctly for narration_video and narration_images Pass 1 without losing music_video behavior

### Fixed
- **Pass 2 character composites no longer "overtake" the base scene style — KLEIN REF workflows reverted to Turbo/distilled config** (`workflows/KLEIN_EDIT_ULTRA_WORKFLOW_{1..5}REF.json`). Comparing the shipped JSONs against a user-supplied known-good 4REF workflow surfaced FIVE drifted values, all in the same direction: the workflows were running the standard Klein config (`steps=20, cfg=5, upscale_method=lanczos`) when they should be running the distilled Klein config (`steps=4, cfg=1, upscale_method=nearest-exact`). At CFG=5 the LLM-enhanced text prompt has heavy classifier-free guidance pull that OVERRULES the reference image colors and composition; at CFG=1 the model leans on the references for color/lighting/style. Combined with 5× more sampler iterations (drift) and lanczos blurring ref colors during the latent prep, the output composite was a fresh rendering of the prompt rather than a character insert into the base scene. All five REF workflows now match Turbo config end-to-end. Klein Text2Image was already on the distilled path (steps=4, cfg=1, lenovo LoRA on) — left untouched
- **Pass 2 character ref list could exceed Klein's 5REF ceiling** (`backend/services/jobs/dispatcher.py` `_create_two_pass_composite_job`). When auto-gen scenes carried >4 character references in `two_pass_character_ref_ids` (e.g. project had many characters auto-resolved from concept data), the dispatcher built `workflow_type = f"klein_{count}ref"` and the build failed with `Unknown workflow type: klein_6ref`. Now clamped at `MAX_CHARS_IN_COMPOSITE = 4` (scene image always claims slot 1 → klein_5ref is the ceiling). Extras dropped with a warning so the dropped IDs show up in the log
- **Auto-gen was carrying scene "extras" into Pass 2 as if they were characters** (`backend/api/generation.py` `_apply_two_pass_to_job_params`). The FF picker allows up to 3 extra reference images (locations, props, style refs) in addition to up to 2 character refs. Every auto-gen callsite was doing `ref_ids = char_asset_ids + extra_ref_ids` then stashing the WHOLE list as `two_pass_character_ref_ids`. Result: 2 chars + 3 extras = 5 refs → Pass 2 = 1 scene + 5 = klein_6ref crash, AND non-character image colors blending into the composite. Helper now accepts a `character_only_ids` kwarg; all 7 auto-gen callsites pass `char_asset_ids` / `seq_char_aids` (the character-only list already computed one line earlier). Extras are intentionally dropped in two-pass mode — they had no correct slot anyway since Pass 1 runs Z-Image (no refs) and Pass 2 is for character compositing only
- **Pass 2 brightness / "washed out" regression on narration_video** — root caused to missing style anchors in the composite context (Issue #1 above) and Z-Image's response to Klein-style verbose prompts (Issue #2). Both addressed. Music_video Pass 2 also benefits since the same fixes apply
- **Pass 2 Klein composite overtook the base scene's color/style (B&W noir → color leak)** — three-layer fix because Klein at CFG=5 blends color signals from BOTH the scene ref and the (usually full-color) character refs. Workflow params are NOT the cause (1REF/2REF/3REF Klein workflows all use identical steps=20/CFG=5/euler with the LoRA OFF — verified) — the bug lives in the prompt-side instructions:
  - **`TWO_PASS_COMPOSITE_SYSTEM_PROMPT` rewritten** (`backend/services/llm/prompt_enhancer.py`): leads with an explicit "ABSOLUTE TOP RULE — PRESERVE THE BASE SCENE STYLE" block stating the first reference is the AUTHORITATIVE VISUAL BASELINE. Character references are now described as IDENTITY and POSE only — their colors, skin tones, and lighting must be ignored and re-rendered to match the first image. Added a "CHARACTER DESCRIPTION COLOR FILTER" section that tells the LLM to translate character color cues ("brown leather jacket", "blue eyes") through any active palette override. Length cap raised 150→180 to make room for the explicit style-lock language Klein needs
  - **Pass 2 LLM context restructured** (`backend/services/jobs/dispatcher.py` `_build_two_pass_composite_prompt`): the style-preservation contract now leads the context list (before the base prompt, before character descriptions) so the LLM treats the first ref's palette as ground truth before it even sees the scene details. Each character ref description now explicitly says "IGNORE the lighting, color cast, skin tone, and clothing colors of the reference photo — re-render the character under the FIRST reference image's lighting and palette." When a color override is active, the directive gets an extra Pass-2-specific tail: "the character reference photos may be in full color, but the final composite MUST use ONLY the palette above — re-render the characters' skin, clothing, hair, and eyes in this palette only"
  - **Dispatch-time color suffix strengthened for Pass 2 only** (`backend/services/jobs/dispatcher.py` color injection block): when `two_pass_phase == "composite"` and a color override is set, the appended suffix gets an additional trailing clause restating that the entire composite must match the first reference image palette and that the model must ignore colors from the character reference photos. Lands near the end of the prompt where Klein weighs the latest tokens most heavily. Single-pass and Pass 1 paths unchanged
- **Default Color Palette on Concept tab did not save** — `ConceptData` Pydantic model in `backend/api/concept.py` was missing `global_color_override` and `custom_color_palette` fields, so the frontend's value was silently dropped on PUT and never returned on GET. Added both fields to the model, the GET response, and the PUT settings write (with empty-string-clears-key semantics so an unset palette falls through to no override)
- **Per-scene Color Override now defaults to "None — use project Default Color Palette"** in `frontend/src/components/SceneEditor/SceneEditor.tsx`. Previously the picker showed a real palette as the "default" even when no scene override was set, creating the illusion that the project-level palette was being ignored. Selecting "None" deletes the per-scene `color_override` + `custom_color_palette` keys so generation falls through cleanly to the project's `global_color_override`. The active project default is shown inline in the dropdown label
- **Auto-gen "complete" status no longer flips while Pass 2 composites are still rendering** (`backend/api/generation.py` `_run_windowed_batch`). Two-pass jobs spawn a NEW Pass 2 Job row from `dispatcher._create_two_pass_composite_job` AFTER Pass 1 finishes; that new row was never tracked in the windowed batch's `active_jobs` dict, so when the last Pass 1 left the dict the batch declared "complete" while the final composites were still running on workers. The batch now drains: after the main loop ends, it polls the DB for any PENDING/RUNNING jobs scoped to this batch's scene IDs and waits them out, surfacing `current_step = "finishing follow-on jobs (N remaining: X × composite, Y × image)"` so the UI shows what's happening. Per-job cap on the drain matches the main `batch_timeout` so a wedged worker can't pin the status forever

---

## [1.8.2] - 2026-06-03

### Added

#### Narration Images mode lock (six layers of defense)
The Narration Images project mode (Ken Burns slideshow output) now strictly enforces image-only behavior across the entire pipeline, fixing the case where a project could accumulate video artifacts and then show them in preview / export despite the mode setting:

- **Export assembly** (`backend/api/export.py` `_build_scene_dicts`): when `project.mode == "narration_images"`, every scene's `scene_source_type` is forced to `"image"` and `chosen_video_path` is nulled before assembly. Any leftover videos on scenes are ignored. Log line: `Project is in narration_images mode — forcing scene_source_type='image' on every scene for this export`
- **Auto-Gen sequential** (`POST /auto-sequential`): rejects video-producing modes (`all_video_*`, `missing_videos_*`) with a 400 when project is narration_images. Allowed modes: `all_images`, `missing_images_independent`
- **Auto-Gen legacy** (`POST /auto`): rejects video-touching enhanced modes (`enhanced_all`, `enhanced_missing`) with a 400. Allowed modes: `all_images`, `empty_only`
- **Story Flow auto-gen** (`_ensure_video_flow`): skips entirely for narration_images projects — the system prompt is video direction ("camera movement, action, mood, composition") which would only waste tokens for a Ken Burns slideshow. Image enhancement falls back to the scene's narration text + concept block, so the skip degrades nothing
- **Single-scene enhance** (`POST /enhance-prompt`): rejects `is_video=True` for narration_images projects with a clear error message
- **Live preview** (`frontend/src/components/VideoPreview/VideoPreview.tsx`): forces `sourceType = 'image'` and nulls the video URL when project is narration_images. Hitting Play on the timeline now correctly shows the still image with Ken Burns / movement, even if `chosen_video_path` is still stored on the scene from before the lock. Also skips the next-video preload
- **Auto-Gen modal UI** (`AppLayout.tsx`): filters `AUTO_GEN_OPTIONS` to image-only modes and defaults to `missing_images_independent`. Video modes don't even appear in the picker

**Narration Video mode is untouched.** Every guard branches on `project.mode == "narration_images"` specifically; `narration_video` continues to use the full video pipeline including LTX Director and video-prompt enhancement.

#### Pre-flight guards for video generation
- **No-start-image guard** (`POST /generate/video`): if the requested workflow type needs a first frame (everything except `ltx_v2v_extend` / `ltx_seq_v2v`) and the request has no `first_frame_asset_id` AND the scene has no `chosen_image_path` AND no `use_prev_lf_as_ff` flag, returns a 400 with a clear message. Previously the job got created, ComfyUI received a workflow pointing at a missing image (logged as 404 by the worker), then reported "completed" with nothing rendered. Now the job is never created; the dispatcher never wastes a worker slot
- **Frontend pre-flight** in `SceneEditor.tsx` `generateVideoMutation`: checks the same conditions client-side and pops an `alert()` before any network call. Throws to short-circuit the mutation cleanly
- **Mutation `onError` surfacing**: video-gen mutation now reads `err.response.data.detail` and pops it in an alert. Whether the rejection came from the client guard or the server guard (or any other failure), the user sees the actual error message instead of dying silently

### Fixed
- **VideoPreview no longer plays leftover videos in narration_images projects** — see live preview bullet above
- **Auto-Gen modal no longer offers video modes when project is narration_images** — the picker is filtered client-side so the user can't even pick a video mode

---

## [1.8.1] - 2026-06-02

### Added

#### Image generation transparency
- **Model indicator badge on the Image tab** — under the two-pass toggle, a `Will render with:` row predicts which model the backend will actually use given the current ref count + two-pass toggle + global `single_image_generator` setting. Single-pass shows one chip (Klein N-ref / Klein T2I / Z-Image Turbo); two-pass shows `Pass 1: Z-Image Turbo → Pass 2: Klein N-ref`. When two-pass is on but zero refs are selected, an amber `⚠ no refs — single-pass` chip appears so you know exactly why the backend will downgrade
- **Per-pass model label on lightboxes** — the main image lightbox reads `version.parameters.workflow_type` (ground truth from `GenerationHistory.parameters`) and pins a blue chip top-left showing the actual model that produced the preview, with `· Pass 2` appended when applicable. The "View Original" Pass 1 lightbox resolves the base asset's `meta.workflow_type` and labels accordingly so any model deviation is immediately visible

### Changed

#### Image generation guards (logic correctness)
- **Pass 1 is now ALWAYS Z-Image Turbo** — the dispatcher's `_try_zimage_redirect` short-circuits the `AppSettings.single_image_generator` check when `two_pass_phase == "base"`. Rationale: Pass 1 paints the scene with no refs; Klein would benefit from refs it never receives. Log line says `Redirecting to Z-Image Turbo (two-pass Pass 1 (forced — characters added in Pass 2)...)`. Independent of the user's global setting
- **Two-pass downgrades to single-pass when no refs are resolvable** — `POST /generate/image` now checks request `reference_asset_ids` + concept-character fallback. If both are empty, downgrades to single-pass at the API layer with `Downgrading to single-pass.` log. Mirrors the existing `_apply_two_pass_to_job_params` guard so the manual Generate button matches auto-gen behavior. Stops wasted Pass 1 runs followed by silently-skipped Pass 2
- **Transition picker label** — "None (Hard Cut)" → **"None (Use Per Scene Preference)"** in both the Export modal (`AppLayout.tsx`) and Settings (`SettingsPage.tsx`). When global is `none`, assembly correctly falls through to each scene's `transition_in`/`transition_out` — the new label tells the truth

#### Persistence semantics
- **Reference picker auto-saves on every change** — `ReferenceSelector` `onChange` now goes through the cache-coherent `updateSceneAndSync` helper (backend + React Query cache + Zustand in one shot). Previously, picker state only persisted on Generate click; if the user removed a character ref and navigated away, the next two-pass run could still use Klein with the stale ref ID
- **Transition selectors auto-save on every change** — both transition `<select>` handlers now use `updateSceneAndSync` instead of the raw `updateScene` + `updateSceneInStore` pair. The raw path skipped React Query, letting the AppLayout cache-mirror revert the change on next refetch
- **Export cache nested by scope** — `.export_cache/<cache_key>/concat.mp4` (was `.export_cache/concat.mp4`). Before: exporting chapter A, then chapter B, then chapter A again forced a full re-render of A because B's save overwrote A's slot. Now each scope (full project, each chapter, each chapter subset) keeps its own durable cache. `force_recreate` still wipes the entire root

### Fixed

#### Chapter scope leaks
- **Background auto-gen task re-fetched all project scenes** — `_run_sequential_auto_gen` accepted `chapter_id=None` and unconditionally re-built the scene list from `select(Scene).where(project_id)`, so even though the request handler scoped to 23 chapter scenes the bg task processed all 328. Fix: handler now passes `chapter_id=req.chapter_id` into the task; task branches on `chapter_id is not None` in all three scene queries (initial load, flow-gen scope, post-flow re-read) using `scenes_in_chapter_tree`
- **Story Flow pre-step ignored chapter scope** — `AppLayout.handleQueue` called `generateVideoFlow(projectId)` without `chapter_id` when `useStoryFlow` was on, regenerating ideas for all 328 scenes before scoping the actual gen to 23. Fix: forwards `chapterScope.chapterId` AND inspects in-scope scenes — if every one already has `flow_idea`, skips the pre-step entirely (no LLM cost, no overwriting user edits). Console logs `[AutoGen] Skipping flow gen — all 23 chapter scenes already have flow_idea`
- **`POST /auto` was not chapter-aware** — older auto-generate endpoint (separate from `/auto-sequential`) did `select(Scene).where(project_id)` with no chapter scope support. Added `chapter_id: Optional[UUID]` to `AutoGenerateRequest` + the same `scenes_in_chapter_tree` branch the sequential path uses

#### Backend silent breakage
- **`name 'json' is not defined` on chapter-scoped exports** — `backend/services/video/assembly.py` called `json.dumps`/`json.loads` (cache key + manifest read/write) without importing `json`. The cache path was never exercised in single-project mode; chapter exports hit it and the export job crashed with NameError. Added `import json`
- **Audio-only-remix cache silently disabled** — `_save_concat_to_cache` called `datetime.utcnow().isoformat()` for the manifest's `saved_at` field but `datetime` was never imported. The call was wrapped in try/except so exports didn't crash, but the manifest was never written, which means `_load_cached_concat` always returned None and the audio-only-remix feature was effectively non-functional. Added `from datetime import datetime`

### Backend audit summary
Surrounding-areas audit found no other backend file using a stdlib module without importing it. The export pipeline's chapter_selection flow (frontend → `ExportRequest.chapter_selection` → `_resolve_chapter_scope` → `_build_scene_dicts(chapter_ids=...)`) is end-to-end correct. Per-scene transition override IS honored when global is `none` (`assembly.py:1131-1138`) so the new "Use Per Scene Preference" label is truthful.

---

## [1.8.0] - 2026-06-02

### Added

#### Narration Chapters — long-form workflow
- **Chapter model** — new `chapter` table with `parent_chapter_id` self-reference for sub-chapters (up to 3 depth levels). Every chapter carries `name`, `short_code` (e.g. `a3f9-ch-01`), `color`, `tags`, `description`, `character_focus` list, and `style_notes`. Scenes get a `chapter_id` FK; assets and scenes get human-readable `short_code` columns
- **Auto-chapter pipeline** — Markdown `# Heading` / `## Heading` markers in the narration script become chapters automatically. Without headers, the project is auto-split by scene count (`chapter_auto_split_threshold` in Settings, default 25). Oversized chapters auto-split into sub-chapters at natural pause boundaries
- **Suggest Timeline auto-builds chapters** — every successful Suggest Timeline run also runs the chapter resolver, so chapters appear immediately on the timeline overlay and in the Chapters tab. Backend logs `[SuggestTimeline] Auto-built N chapter(s) from M scenes`
- **Chapter REST API** — `GET /api/projects/{pid}/chapters/` (tree), `POST /reparse`, `PATCH /{cid}` (rename/recolor/retag/description/character_focus/style_notes), `POST /{cid}/split`, `POST /{cid}/merge_with_next`, `POST /{cid}/generate-description` (LLM), `POST /{cid}/preview-llm-batches`, plus universal `GET /api/shortcode/{code}` resolver
- **Chapter scope banner** — when the URL is `/project/:id/c/:short_code`, a banner appears at the top of the editor with chapter name + color + scene count + time range + prev/next chapter buttons + back-to-project link. Description, character chips, and style notes are editable inline with Save / ✨ Generate buttons
- **Chapter Direction panel** (Chapters tab) — every chapter renders as a card with inline description textarea, character chips, style notes, and per-card ✨ Generate description + 🎬 Generate Story Flow buttons. Top toolbar has a **✨ Generate ALL** batch button (sequential with progress bar) plus the existing Re-parse
- **Chapter-scoped Timeline** — drilling into a chapter narrows the Timeline scene list to that chapter's subtree. Zustand `chapterScope` slice (sceneIds Set + start/end time) is the single source of truth
- **Chapter-scoped Export** — Export modal opened from a chapter view defaults to `mode: 'single'` with the active chapter pre-selected. Backend `ExportRequest.chapter_selection` filters scenes, slices `master_audio` with FFmpeg, shifts backing tracks + subtitle word timestamps so the output is a self-contained MP4 starting at 0:00. Output filename includes the chapter shortcode
- **Chapter-scoped Story Flow** — `POST /concept/flow/generate?chapter_id={cid}` scopes per-scene flow generation to one chapter and folds the chapter's description, character_focus, and style_notes into the LLM concept block
- **Chapter overlay on timeline** — colored bars row above the waveform, one per chapter (with sub-chapter row when nested). Click to drill in
- **LLM batching limits** — Settings → Chapter Batching exposes `llm_chapter_scene_limit_cloud` (default 25) and `llm_chapter_scene_limit_ollama` (default 12). The resolver respects chapter boundaries when batching
- **Shortcode system** — every asset, scene, and chapter gets a stable `{project_prefix}-{type}-{seq}` identifier (e.g. `a3f9-img-0047`, `a3f9-sce-005`, `a3f9-ch-01`). Universal `/s/{code}` URL redirects to the right entity. Backfill migration assigns codes to existing rows
- **Auto-chapter on initial backfill** — projects without any chapters get one default "Chapter 1" umbrella created at startup so the rest of the chapter pipeline always has something to bind to

#### Subtitle reconciliation
- **Whisper-to-canonical alignment** — `backend/services/audio/text_align.py` reconciles Whisper word strings against the user's pasted ElevenLabs script using `difflib.SequenceMatcher` opcodes. Whisper timestamps (audio-accurate) are preserved; word strings get replaced with canonical tokens, hallucinations dropped, missed words interpolated. Bails out cleanly if similarity < 30%. Applied at export time so existing projects benefit without re-transcribing

#### Whisper / Demucs optimizations
- **`skip_demucs=True` in narration modes** — analyze-audio + batch pipelines now skip Demucs entirely when project mode is `narration_*`. Stems dict points at the original audio for downstream consumers. Saves ~30 min/item and avoids phase artifacts on pure-speech audio
- **Audio-duration-scaled Whisper timeouts** — ComfyUI Whisper poll budget now scales `max(20 min, 4× audio length, 30 min floor)` capped at 6 h. Batch wait_for scales similarly capped at 8 h. Up-front log shows the chosen budget. Queue position from `/queue` is surfaced every 30 s during the poll so wedged-vs-running is distinguishable
- **Whisper heartbeat** — local transcribe path logs an estimated runtime up-front (e.g. "audio=3600s; estimated ~7200s on cpu") then emits a heartbeat every 60 s while `model.transcribe()` blocks. No more silent multi-hour waits

#### Transition handling
- **Global override semantics** — Export "Transition" picker now means: `none` → defer to per-scene `transition_in`/`transition_out`; anything else → override all boundaries uniformly. Per-scene transitions are now actually forwarded from `Scene.parameters` to the assembler (previously stripped silently)

#### Debug
- **`/api/debug/chapters/{project_id}`** — compact JSON snapshot of chapter state for a project: parsed headers, clean-text word count, scene-to-chapter binding stats, unbound scenes, and current settings
- **`tools/diag.py --chapters PROJECT_ID`** — CLI wrapper that prints the snapshot as markdown

### Changed
- **`Suggest Timeline` response** — DP-narration scene-creation block fixed (`Scene.order_index` instead of non-existent `scene_index`; required `prompt` field filled). Response shape unified between LLM and DP paths
- **OpenAI param fallback** — chapter description endpoint detects newer model families (gpt-4.1+, gpt-5, o-series, chatgpt-*) and uses `max_completion_tokens` automatically; on `BadRequestError` it retries once with the other token-param style so model aliases the heuristic misses still succeed

### Fixed
- **Chapter URL singular/plural mismatch** — chapter components were navigating to `/projects/:pid/c/:shortcode` (plural) while the route is `/project/:pid/c/:shortcode` (singular). Every chapter click was hitting the catch-all `*` route and redirecting to `/`. Fixed in `ChapterOverlay`, `ChapterTree`, `ChapterBreadcrumb`, `ChapterScopeBanner`, and backend `shortcode.py` redirect URLs
- **Chapter description fields not in GET response** — `ChapterTreeNode` dataclass was defined before the description fields were added, so the list endpoint dropped them silently. Now part of the dataclass + populated by `build_chapter_tree_response`
- **FK violation on chapter re-build** — `_create_auto_chapters` was deleting parent chapters before their sub-chapters and scenes-pointing-at-them, causing SQLite FK rejection mid-transaction. Rewrote to unbind scenes first, then DELETE chapters depth-DESC via raw SQL with project_id.hex (SQLite stores UUIDs without dashes). Same fix in the headers path
- **PendingRollbackError on Suggest Timeline after chapter failure** — pre-capture `scene_ids` before chapter rebuild so a chapter-build error can't poison the session's lazy-load of `sc.id` in the response
- **`_auto_slice_scene_audio` NameError** — Suggest Timeline now calls the actual helper `_slice_audio_for_scenes`, wrapped in try/except so a slice failure doesn't lose the scenes
- **Chapter tab blank on re-run** — frontend wasn't refetching the chapter tree after Suggest Timeline. Window event `rbmn:chapters:invalidate` dispatched from Timeline / AudioSetup → AppLayout listener refetches. Also reloads on Chapters-tab click
- **Stems-only export status** — backend now marks the Job DONE + populates `_export_progress` with `status="done"` + the stems list before returning, so the frontend transitions out of "Exporting…"
- **Single Download button hidden on stems-only success** — per-stem download cards are the right action
- **`ProjectMode` NameError** in `timeline.py` analyze-audio endpoint — added to the top-level import block

### Documentation
- `BLUEPRINT_CHAPTERS_v1.md` — design doc for the chapter system (kept in repo as historical record of the design decisions and Phase 1.5/2/3 punch list)
- This CHANGELOG section
- README narration-mode section (next)

---

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.7.0] - 2026-05-31

### Added

#### Export — re-export controls and stems
- **Audio-only re-mix** — After every successful export, the silent concatenated video is saved to `{output_dir}/.export_cache/concat.mp4` along with a manifest hashing the video-affecting params (scenes, dimensions, transitions, color match, CRF). On the next export, if the hash matches, the entire clip-rendering and chunk-merge phases are skipped — only the audio mix, mux, optional stems, and optional subtitles run. Use case: change narration volume / backing track levels / fades / normalization without re-rendering hours of video. Export modal has a "Audio-only re-mix" checkbox that requires the cache to exist (errors loudly if not), and auto-disables when "Force full recreate" is on
- **Export audio stems** — New checkbox in the Export modal that ALSO writes per-channel WAVs to `{output_dir}/stems/`: `narration.wav` (narration with master volume), `backing_mix.wav` (all backing tracks mixed), and `backing_NN_name.wav` for each individual backing track separately. 48 kHz 16-bit PCM — drop straight into a DAW for outside-the-app remixing
- **Stems-only export** — Skip ALL video rendering entirely and just produce the audio stem WAVs. Use case: you already have the exported video and want to grab stems later for separate mixing. Output appears in `{output_dir}/stems/` with `narration.wav` + `backing_mix.wav` + one `backing_NN_name.wav` per backing track. Runs in seconds since no clip rendering or muxing is involved
- **Force full recreate** — New checkbox that wipes the export cache before starting, guaranteeing a fresh render. Available in both narration and music modes. Mutually exclusive with audio-only re-mix
- **Cache invalidation** — The cache key covers everything that affects the silent video (scene paths/durations/transitions, dimensions, FPS, CRF, color match), so changing any of those triggers a fresh render. Audio params (volumes, fades, normalize, subtitles) are deliberately excluded — they're applied after the cached concat is reused

#### Batch processing reliability (B-1 through B-14)
- **B-1: Idle-race guard** — Auto-gen kickoff POST is now required to succeed (raises on non-200). The poll loop tracks `saw_running` and only treats `status="idle"` as terminal after first confirming the run actually started. Previously the batch could falsely "complete" with zero work done if the kickoff failed
- **B-2: 2-hour poll deadlines** — Both image and video step poll loops now have hard deadlines. A wedged auto-gen can no longer hang a batch item indefinitely
- **B-3: Exhaustive video mode map** — `video_mode_map` now includes `fflf` (FF/LF chaining). Unknown values raise instead of silently demoting to single-image mode. Same treatment for image mode (`missing` / `all_with_refs`)
- **B-4: Orphan project cleanup** — Failed items that haven't generated anything yet are now best-effort cleaned up (project row + directory removed) instead of being left as junk in the project list
- **B-6: Skip base-on-lyrics when user supplied direction + style** — Saves an LLM call when both fields are already filled in
- **B-7: Whisper 1-hour timeout** — Audio analysis wrapped in `asyncio.wait_for`. A wedged Whisper can no longer hang the item indefinitely (Demucs already had a 30-min subprocess timeout)
- **B-9: Lyrics retry uses fresh session** — Avoids gotcha #9's corrupted-session pattern and preserves `initial_text` (previously dropped on retry)
- **B-10: BatchItemAddModal expanded** — UI now exposes Image Mode (Missing only / All with prev-scene refs), FF/LF video chaining, Lipsync-aware prompts, Vocals-only audio, and Override-regenerate-full-set
- **B-12: Staging cleanup on success only** — Retries can find the staged audio file again
- **B-14: Surfaced auto-character failures** — Now appear as warning entries in the BatchRun activity feed

#### Debugging tools
- **`GET /api/debug/snapshot`** — Returns JSON of in-memory batch runs, in-memory auto-gen runs, ComfyUI worker stats, job queue depth + running + failed jobs, and recent WARNING/ERROR log entries. Query params: `?log_lines=N&log_grep=substring`
- **`GET /api/debug/log/tail?lines=N&level=ERROR&grep=substring`** — Filtered tail of `rbmn.log` returning structured entries (each message capped at 500 chars)
- **`tools/diag.py`** — CLI helper that hits the snapshot endpoint and prints a compact markdown summary. Use `python tools/diag.py > diag.md` to capture the current backend state instead of pasting raw log files. Supports `--logs N`, `--grep TERM`, `--json`, `--tail`, `--host` overrides

### Fixed

- **Active image/video set delay** — Setting a chosen image/video as active on a scene didn't stick — leave and come back and it would still show as inactive until a later DB refresh caught up. Root cause: `updateScene` PUT + `updateSceneInStore` Zustand update without updating the React Query cache that AppLayout mirrors back into Zustand on every change. The stale cache eventually overwrote the fresh Zustand state. Fix: added `updateSceneAndSync` helper in `SceneEditor.tsx` that updates backend + React Query cache + Zustand atomically, applied to all 24 scene-update call sites. Defensive `flag_modified(scene, "parameters")` added to backend `update_scene` to also guarantee persistence even if SQLAlchemy's MutableDict detection has edge cases. Same fix applied to `useJobEvents.ts` SSE reconnect path
- **Auto Gen modal "Full Pipeline" did nothing** — The modal exposed `enhanced_all` / `enhanced_missing` / `empty_only` modes but the backend `_run_sequential_auto_gen` only handled the 6 modes `all_images` / `missing_images_independent` / `all_video_*` / `missing_videos_single`. Picking "Full Pipeline (All)" hit no branch and the function fell off the end marking complete with zero work. Fix: replaced modal options with the 6 actually-supported modes (`all_video_fflf` is the new default), added `Override — regenerate full set` toggle, added backend `_VALID_MODES` guard that fails loudly on unknown modes
- **Auto Gen "status window disappears then selection comes back"** — Timeline toolbar's Auto Gen button opened a duplicate legacy modal that wasn't wired to the bottom-of-screen `AutoGenStatusBar`. Local state was lost on every unmount, so the user would see the selection screen instead of progress. Fix: lifted `autoGenOpen` into the Zustand store so the Timeline button opens the same modal as the header button, removed the legacy modal entirely
- **WebSocket completion detection too slow (450s+ delay)** — `crystools.monitor` (every 1-2s) and `progress_state` messages kept `ws.recv()` from ever timing out, blocking the history-poll fallback that lives inside the WebSocketTimeoutException handler. Added a wall-clock history poll inside the recv-success branch that runs every 10s once progress hits 100%, regardless of message flow
- **PUT `/scenes/reorder` and GET `/assets/generated` shadowed** — Named routes were registered AFTER `/{scene_id}` and `/{asset_id}` so FastAPI parsed the literal strings as UUIDs and returned 422. Reordered the route declarations
- **`JobResponse` class name collision** — Same class name in `api/jobs.py` and `api/export.py` overwrote OpenAPI schema. Renamed `export.py`'s class to `ExportJobResponse`
- **`Scene.workflow_snapshot` and `Job.prompt_id` silently dropped** — Pydantic response models didn't include fields the DB model had. Fields added to `SceneResponse` and `JobResponse`
- **Demucs could hang forever** — `Popen` + `process.wait()` had no timeout. Wrapped with `wait(timeout=1800)` + `kill()` on `TimeoutExpired`
- **`/api/files/*` path-traversal** — `startswith` lacked a separator boundary guard. Replaced with `relative_to()`
- **Asset upload read whole file into memory** — Replaced with streaming 1 MB chunks + incremental SHA256 + hard 2 GB cap (returns 413 over limit)
- **`asyncio.create_task` fire-and-forget GC risk** — The auto-gen pipeline and ~15 batch-run DB-update tasks were vulnerable to event-loop weak-ref GC. Added `backend/utils/background.py` with a `track()` helper that holds strong references and logs exceptions; replaced all fire-and-forget calls in `api/generation.py`, `api/batch.py`, `api/batch_runs.py`, `api/export.py`
- **Restart cancelled in-flight ComfyUI prompts** — `recover_running_jobs` cancelled ALL RUNNING jobs unconditionally. Now jobs with a live `prompt_id`+`worker_url` are left in RUNNING; the dispatcher's startup reconnects via the existing retry fast-path so expensive LTX renders survive backend restarts
- **Worker `in_flight` counter drift on retry** — The retry fast-path skipped `select_worker(reserve=True)` but `stream_and_wait`'s `finally` always decremented `in_flight`. Counter drifted toward zero, leading to over-scheduling busy workers. Now the retry path explicitly increments `in_flight` to match the decrement
- **`cancel_job` allow-list included DONE/FAILED** — A stale cancel could flip a DONE job to CANCELLED. Restricted to PENDING/RUNNING with 409 otherwise
- **`mux_audio` in narration export bypassed `_run_ffmpeg`** — Raw `subprocess.run(..., timeout=120)` left truncated muxed files on timeout. Now raises on `TimeoutExpired` and cleans up partial output on non-zero return code
- **`datetime.now()` (local TZ) in `api/timeline.py` and `services/llm/prompt_enhancer.py`** — Replaced with `datetime.utcnow()` for consistency with frontend's Z-normalization
- **`BackingTrack` missing cascade delete** — Deleting a project with backing tracks raised a FK violation. Added `cascade_delete=True` relationship and `ondelete="CASCADE"` on the column
- **`update_project` didn't bump `updated_at`** — Project list sorted by `updated_at DESC` didn't reflect edits. Now bumps the timestamp on commit
- **`color_correction_enabled` reset on every startup** — Migration ran an unguarded UPDATE that overwrote the user's choice every boot. Guarded with a sentinel column `_mig_color_default_off` so it only runs once. Same fix for `_mig_transition_none`
- **`requests.get(...)` in test-whisper endpoints blocked the event loop** — Six sites wrapped in `asyncio.to_thread`
- **INTConstant duration truncation in custom workflows** — `prepare_workflow_from_config` (the path for user-uploaded WorkflowConfig templates) didn't apply `math.ceil(duration)` like the 6 hardcoded workflow builders do. Added an INTConstant truncation guard so custom-workflow uploads that map `duration` to an INTConstant node don't re-trigger the floor-on-write bug
- **Klein image generation rejected by every worker** — Two compounding bugs: (1) `discover_capabilities` only scanned `CheckpointLoaderSimple`, missing GGUF-quantized Klein models loaded via `UnetLoaderGGUF`/`UNETLoader`; (2) `worker.capabilities = user_caps` in `main.py` and `api/settings.py` REPLACED auto-discovered caps (including `inpaint` and `upscale`). Fixed: added GGUF unet loader scan in `discover_capabilities`, and changed user caps to MERGE (preserve auto-discovered) with explicit add/discard for klein/ltx based on the user's image/video checkboxes
- **15+ frontend components subscribed to the whole Zustand store** — Every SSE `job_progress` event re-rendered huge subtrees. Converted to per-field selectors across `AppLayout.tsx`, `Timeline.tsx`, `SceneEditor.tsx`, `WaveformDisplay.tsx`, `useBackingTrackPlayer.ts`, `VideoFlowPanel.tsx`, `AssetManager.tsx`, `AssetManageModal.tsx`, `AudioSetup.tsx`, `GenerationPanel.tsx`, `SectionMarkers.tsx`, `VideoPreview.tsx`, `ReferenceSelector.tsx`, `CharacterCreatorModal.tsx`, `BatchPreviewPIP.tsx`
- **9 frontend timestamp sites missing Z-normalization** — Backend sends `datetime.utcnow().isoformat()` without a Z suffix; JavaScript was interpreting these as local time. New `frontend/src/utils/time.ts` helper (`parseBackendDate`, `parseBackendMs`) wired into GenerationPanel, AssetManageModal, AssetGeneratorModal, SettingsPage, BatchesDashboard, AppLayout (formatDate), ProjectList, SceneEditor (5 sites), useJobEvents

### Documentation
- README "Required Custom Nodes" expanded with 7 packs the shipped workflows actually need: ComfyUI-Detail-Daemon, ComfyUI_essentials, ComfyUI-TTPlanet, ResizeImagesByLongerEdge, TrimAudioDuration, ComfySwitchNode
- README "Environment Variables" expanded with Ollama (`OLLAMA_BASE_URL`, `OLLAMA_URLS`, `OLLAMA_MODEL`), bind controls (`APP_HOST`, `APP_PORT`), and performance vars (`RBMN_PARALLEL_CLIPS`, `RBMN_TMPFS_DIR`, `RBMN_TMPFS_MIN_FREE`)
- README version line removed (points at `VERSION` + CHANGELOG instead of hard-coding 1.4.0)
- `pyproject.toml` and `backend/main.py` versions synced to track `VERSION`

## [1.6.3] - 2026-05-31

### Added
- **Batch pipeline per-step checkpointing** — Every stage of the batch render pipeline (project creation, audio analysis, timeline suggestion, concept generation, character generation, video flow, image gen, video gen) now saves a checkpoint to the database after completing. On resume, the pipeline reads the last completed step and skips directly to where it left off. Previously, any failure (LLM timeout, worker unavailable, etc.) required restarting the entire pipeline from scratch including expensive audio analysis and Whisper transcription
- **Batch retry endpoint** — `POST /api/batch/{batch_id}/retry` re-launches failed batch items using the checkpoint/resume system. Resets failed items to pending, sets batch status back to running, and re-calls `_process_single_item` with the existing `batch_run_id` so completed steps are skipped automatically
- **Proper batch failure status** — Batch runs now report `"failed"` status when ALL items fail, instead of always saying `"done"`. Partial success (some items done, some failed) still shows `"done"` with per-item error details

### Fixed
- **Z-Image Turbo crash on workers without Klein** — When `single_image_generator` is set to Z-Image Turbo, the dispatcher correctly redirects `klein_t2i` jobs to Z-Image, but the worker capability check happened BEFORE the redirect, demanding `klein` capability that LTX-only workers don't have. Fixed by updating `params["workflow_type"]` inside the redirect itself so worker selection picks up the correct (empty) capability set
- **Workers with empty model sets rejected for LTX jobs** — `select_worker` required `{"LTX"}` model tag but workers had empty model sets. Fixed: workers with no models declared now pass the model filter when they match on capability
- **Missing sequencer workflow types in capability/model maps** — `ltx_seq_i2v`, `ltx_seq_fflf`, `ltx_seq_v2v` and `klein_5ref` were missing from the dispatcher's capability and model maps, causing wrong worker selection
- **Image worker count hardcoded to Klein** — `_count_capable_workers` required `{"klein"}` for image jobs, but Z-Image Turbo needs no special capability. Changed to `set()` so all healthy workers are counted for the parallel dispatch window
- **BatchRun marked COMPLETED when all jobs failed** — `_run_windowed_batch` now sets `BatchRunStatus.FAILED` when `total_succeeded == 0` and `total_failed > 0`
- **Progress count dropped at end of windowed batch** — `completed_scenes` was set to `total_succeeded` only, ignoring failures. Changed to `total_succeeded + total_failed` so the progress reflects all processed scenes
- **Batch pipeline LLM timeouts** — Increased timeouts for all LLM-dependent batch steps (suggest-timeline, base-on-lyrics, character autogenerate, video flow generate) from 120-300s to 600s (10 minutes) to handle slower LLM providers and longer songs

## [1.6.2] - 2026-05-30

### Fixed
- **Export crash destroys hours of rendered clips** — When export failed at a post-rendering step (subtitle burn-in, audio mux, normalization), the error handler cleaned up the entire working directory including all rendered clips and the merged video. This made the existing resume detection useless — it could detect previous work but there was never any to find. Now the working directory is preserved on failure so the next export attempt can reuse already-rendered clips (per-clip duration validation) and skip directly past chunk merge (merged-video detection). Turns a multi-hour re-render into a ~30 second retry. Fixed in both music and narration assembly pipelines

## [1.6.1] - 2026-05-30

### Fixed
- **Narration export audio volume drop** — FFmpeg's `amix` filter divides each input's volume by N (number of inputs) by default, causing massive volume loss when mixing narration + backing tracks. Added `normalize=0` to preserve the original gain of each track. Previously, a 3-track mix would reduce each track to ~33% of its configured volume
- **Volume boost not applied during export** — Narration and backing track volume filters only applied when volume was `< 1.0`, silently ignoring boost values `> 1.0`. Fixed condition to use `abs(volume - 1.0) > 1e-6` so both attenuation and amplification are applied correctly
- **Subtitle burn-in crash on Windows paths** — FFmpeg `ass` filter parsed the colon in Windows drive letters (e.g., `D:`) as a filter option separator, causing `Unable to parse option value` errors. Both backslash-escaping (`\:`) and single-quote wrapping failed on Windows FFmpeg builds. Fixed by setting FFmpeg's working directory (`cwd`) to the ASS file's parent folder and referencing only the basename in the filter — the filter sees `ass=subtitles.ass` with no path, no drive letter, no colon to escape
- **Video prompt enhancer ignores scene sequence during auto-gen** — In all auto-gen modes (single, FF/LF, missing videos), the LLM prompt enhancer had no knowledge that consecutive scenes were related, causing wild visual shifts between scenes. Root cause: `use_prev_scene_last_frame` was always set to `False` during auto-gen, which gated out the entire continuity context block. Three fixes: (1) Removed the `use_prev_lf` gate — continuity context now fires whenever `prev_scene` is provided; (2) Added two tiers of continuity language: "SHOT EXTENSION (CRITICAL)" for FF/LF mode (same shot, camera still rolling) vs "NARRATIVE CONTINUITY" for sequential mode (different frame, maintain visual coherence); (3) `missing_videos_single` mode now passes the previous scene instead of `None`

## [1.6.0] - 2026-05-29

### Added
- **Single-pass FFmpeg filter graphs** — Clip normalization (scale+pad+setsar), duration padding (tpad), fade in/out, and color correction are now chained into ONE FFmpeg call per clip. Previously required 3-5 separate decode→encode cycles per scene, each with full quality degradation. New `process_clip_single_pass()` and `process_image_single_pass()` functions in ffmpeg.py
- **Parallel clip processing** — Independent clips are now rendered in parallel using `ThreadPoolExecutor` (FFmpeg subprocesses release the GIL, giving true parallelism without ProcessPoolExecutor serialization overhead). Default 4 workers, configurable via `RBMN_PARALLEL_CLIPS` env var. Both music and narration assembly pipelines use the parallel path
- **Stream-copy concat** — When no transitions are needed, clips are concatenated with `-f concat -c copy` (zero re-encode). Automatic fallback to filter concat if format mismatch is detected. New `concat_clips_copy()` function
- **FFmpeg threading flags** — All FFmpeg invocations now auto-inject `-threads 0 -filter_threads 4 -filter_complex_threads 4` for better CPU utilization across decode, filter, and encode stages
- **Pre-computed transition compensation** — Transition overlap padding is now calculated BEFORE clip creation and folded into the single-pass FFmpeg call, eliminating a separate re-render loop that previously added an extra decode→encode cycle per clip
- **Tmpfs intermediate files** — Export pipeline automatically uses `/dev/shm` (Linux tmpfs) for intermediate clip files when available and has sufficient free space (512 MB minimum). Eliminates disk I/O bottleneck for temp files. Configurable via `RBMN_TMPFS_DIR` and `RBMN_TMPFS_MIN_FREE` env vars. Falls back to output directory subdirectory on Windows/macOS or when tmpfs is unavailable
- **Ken Burns 8x upscale** — Image scenes now use zoompan at 8x resolution then downscale for higher quality Ken Burns effects, integrated into the single-pass pipeline

### Fixed
- **Frame-exact duration limiting** — `process_image_single_pass` now uses `-frames:v` (frame count) instead of `-t` (time-based) for precise output duration, eliminating off-by-one frame issues at non-integer framerates (e.g. 29.97fps)
- **Frame-exact fade timing** — All fade in/out effects in single-pass functions now use `start_frame`/`nb_frames` instead of `st`/`d` (time-based), ensuring fades align exactly to frame boundaries regardless of framerate
- **Dead cleanup code removed** — Removed stale cleanup paths targeting `output_dir` for `_colormatch/` and `concat_list.txt` that were unreachable after tmpfs migration (`_cleanup_tmpfs_dir` already handles full cleanup)
- **Dead imports removed** — Removed unused `apply_kenburns`, `apply_fade_in`, `apply_fade_out` imports from assembly.py (logic folded into single-pass functions)
- **Concat fallback parameters** — `concat_clips_copy` now forwards `fps` and `crf` to the filter-based concat fallback instead of using FFmpeg defaults
- **Chunk files written to tmpfs** — `_chunked_transition_merge` wrote chunk files to `output_dir` which receives `work_dir` (tmpfs) from callers. After `_cleanup_tmpfs_dir` runs, chunk download URLs became 404s. Added separate `chunk_output_dir` parameter so chunks are written to the durable project exports directory
- **Dead variable and redundant import** — Removed unused `all_indices` variable and redundant inline `import shutil as _shutil` (module-level `shutil` already imported)
- **V2V join-and-split crash** — `b_stem` was only assigned inside `if not output_b_path:` but used unconditionally on the next line to build `joined_path`. When dispatcher passes an explicit `output_b_path` (which it always does), `b_stem` was undefined → `NameError` crash. Moved assignment before the conditional
- **ffprobe double `-show_entries` flag** — `get_video_stream_duration` passed two separate `-show_entries` flags; ffprobe only honors the last one, so stream duration was silently ignored and the function always returned container/format duration. Merged into single `-show_entries stream=duration:format=duration`
- **Dead `zoom_inc`/`zoom_dec` variables** — Removed unused computed variables from `apply_kenburns` (leftover from pre-easing implementation)
- **Narration inline imports consolidated** — Moved `mix_audio_tracks`, `normalize_audio`, `generate_ass_subtitles`, `burn_subtitles` from inline imports inside try blocks to top-level imports in assembly.py. Removed redundant `get_media_info` re-import
- **Export crash from corrupt LTX audio** — LTX-generated clips contain garbage AAC streams (51 channels, invalid band types) that crash FFmpeg during decode with "Error reinitializing filters!" Added `-an` to all video-processing functions that don't need audio: `concat_clips`, `apply_transition`, `extract_frame` (both seek paths), `trim_video`, and `concat_clips_copy`. These intermediate operations never need audio — the master audio track is muxed at the very end by `mux_audio()`
- **Export crash from truncated clip reuse (moov atom not found)** — When FFmpeg crashes mid-encode, the moov atom (MP4 index) is never written, leaving a truncated file on disk. The resume logic in `_execute_clip_task` checked only `size > 0`, so truncated files passed and were reused in subsequent exports, causing `moov atom not found` errors. Two fixes: (1) Resume check now validates clips with `get_media_info()` — if duration is 0 or the file can't be probed, it's deleted and re-rendered. (2) `_run_ffmpeg` now deletes partial output files on both crash and timeout, preventing corrupt files from accumulating on disk

### Performance
- Export speed improvement: ~3-5x faster for typical 20-scene projects due to elimination of redundant decode→encode cycles and parallel processing
- Disk I/O reduction: tmpfs support eliminates SSD/HDD writes for intermediate files on Linux systems

## [1.5.3] - 2026-05-29

### Fixed
- **Ollama multi-server failover** — Fixed broken round-robin failover where the try/except block was outside the for loop, preventing retry on connection errors. Now correctly tries each server in rotation and continues on `ConnectionError`/`OSError`/`TimeoutError`
- **Auto-gen memory leak** — `_seq_auto_jobs` tracking dict entries are now evicted after 5 minutes via background asyncio task, preventing unbounded growth across multiple auto-gen runs
- **Assembly temp file cleanup** — Both `assemble_music_video` and `assemble_narration_video` now properly clean up intermediate files on all exit paths (success, error, cancellation) using try/except/else pattern
- **Zustand jobs array unbounded growth** — `addJob()` and `updateJob()` now call `pruneJobs()` (which was defined but never wired in). Caps at 200 entries, evicting oldest terminal jobs first
- **Auto-gen elapsed timer never resets** — Timer now clears `startTime` when the backend reaches a terminal state (done/failed/cancelled), ensuring the next run starts fresh instead of counting from the previous run's start time
- **Dispatcher null worker crash** — `submit_job()` auto-select path now raises a clear `ValueError` instead of crashing on `None.url` when no capable workers are available
- **Deduplicated `_group_words_into_sentences`** — Removed ~200-line divergent copy in `generation.py`; now imports the canonical version from `timeline.py` which includes section header and parenthetical line filtering

### Improved
- **Chunk gallery UX** — All four chunk gallery states (during export, done, failed, cancelled) now show rich 2-column cards with video thumbnail preview, play overlay on hover, file size display, and per-chunk download button. Lightbox overlay includes a header bar with download button

## [1.5.2] - 2026-05-28

### Fixed
- **Export quality mismatch** — Frontend sends "draft"/"standard"/"high" but backend CRF map expected "lossless"/"highest"/"high"/"medium"/"low". Only "high" mapped correctly; "draft" and "standard" silently fell back to CRF 16 (high quality). Now correctly maps: draft → CRF 26, standard → CRF 20, high → CRF 16
- **Stale clips on re-export** — Fresh exports no longer silently reuse leftover `clip_*.mp4` and `chunk_*.mp4` files from previous runs. Old artifacts are cleaned up before rendering begins
- **Export progress memory leak** — `_export_progress` dict entries for completed/failed/cancelled exports are now evicted after 10 minutes via a background asyncio task

## [1.5.1] - 2026-05-28

### Added
- **Export Crash Recovery** — New "Recover & Resume Export" button in the export modal. On modal open, scans the project's exports/ directory for leftover clip and chunk files from a crashed export (e.g., power loss, app crash). Shows a recovery banner with the count and size of recoverable artifacts. Clicking it starts a new export that skips already-rendered clips (idempotent checkpoint). Works even without a manifest — falls back to project defaults for export parameters
- **Incremental Manifest Saves** — Export manifest JSON (`export_manifest.json`) is now written after every chunk completes, not just at the end. This means crash recovery has chunk-level state on disk even if the app was killed mid-export
- **Export Scan Endpoint** — `GET /export/scan` returns a lightweight summary of what's on disk (clip count, chunk count, sizes, manifest status) without triggering a recovery
- **Export Recover Endpoint** — `POST /export/recover` scans the disk, rebuilds progress state from files + partial manifest, and starts a new export that leverages existing clips

### Fixed
- **Chunk download URL path** — Fixed chunk gallery download URLs using a non-existent `exports/chunks/` subdirectory instead of the correct `exports/` directory. Affected both the `on_chunk_complete` callback (live gallery during export) and the `_scan_export_dir` recovery scanner. Chunk lightbox previews now load correctly

## [1.5.0] - 2026-05-28

### Added
- **Chunked Export Assembly** — Exports now render in 
