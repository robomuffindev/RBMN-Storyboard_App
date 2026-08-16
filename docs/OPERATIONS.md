# RBMN Operations Runbook (v1.277.14, 2026-08-15)

Everything that exists, where it runs, and how to drive it. This is the "which tool, which
box, which command" page — the narrative is in `HANDOVER_PROMPT.md`, the decisions in
`CHANGELOG.md`, the LoRA method in `docs/LORA_DATASET.md`.

## 1. The machines

| box | role | address | notes |
|---|---|---|---|
| app machine | backend `127.0.0.1:8899`, frontend, agent | localhost | `run.bat` starts both; rebuilds frontend SILENTLY (a failed vite build serves the OLD UI with no error — check `frontend/dist/assets/*.js` for a new string when in doubt) |
| Klein workers | rendering (Klein, upscale, LTX, **H3**…) | `.163` (ZOAI3), `.224` (ZOAI1) :8188 + helpers :8765 | 16GB each, identical stacks (py 3.13.11 / torch 2.10.0+cu130 / sm89); **SageAttention live via --use-sage-attention (37% faster, ArcFace-verified)**; each runs `rbmn_helper.py` v1.219 with its OWN auto-generated token |
| **ZOMAIN01** (training box) | Fizgig training + Krea 2 inference + installed character LoRAs | **DHCP — `.201` as of 2026-08-08** (was `.202`) | RTX 4060 Ti 16GB → **fp8** Krea2 file; ComfyUI `E:\ComfyMaster\V1\ComfyUI_windows_portable` (start via `run_nvidia_gpu-LTX2-16GB.bat` — the network-bound bat; the default bat binds localhost-only); Fizgig `D:\Fitzgig\Fizgig`; helper data `D:\RBMNHelper\rbmn_helper_data` |
| Ollama vision/LLM | QC, captions, wardrobe, lore | `.176:11434` | `qwen2.5vl:7b`, strictly sequential |

**ALL boxes are DHCP (no static IPs on this network).** When one moves: Settings → Worker
Helpers registry → edit the row's host → Save (⭐ trainer row also feeds the legacy
krea2_host). To find a moved box: `scripts\find_helper.py` via the agent scans the subnet
for ports 8765/8188. Every worker's ComfyUI + Fizgig paths are visible/editable per row
(🔍 Detect suggests them); tokens are per-box — the `TOKEN …` line in each helper console.

## 2. The app modes (VNCCS Native page, workflow order)

0. **🏠 Studio Hub** (default landing) — every character's pipeline at a glance (front ref
   → views → dataset → LoRA → sheet → lore, live train/autogen stages) with one-click
   jumps into any tab, character preselected.
0b. **＋ New Character** (the actual front door since v1.276.40, on `/studio`) — 🎯 **Klein 3.0**
   first and marked `main mode` (name-first: it creates the character and opens the panel with
   it selected), ⚡ **Autogen** (photos or a description → the chain, with toggles, a cost
   preview and a batch queue — §10), then **VNCCS Native** as its own independent flow
   (the hybrid lane was retired in v1.277.13 — Klein Mode's create IS Klein 3.0).
1. **🧬 Text 2 Image** — the other character entry point. Name-first (resumable), engines Klein (0-5
   refs) / **Krea 2 Turbo (⚡ since v1.276.45 it renders ROUND-ROBIN across every box that has the
   model — it was pinned to one out of habit; a job naming a LoRA still pins to the boxes
   that have that FILE, and says so in `krea2_note`; 🎓 LoRA picker with trigger display
   + one-click add-to-prompt; strength 1.0)**, pose scaffolds (full-body-front default),
   batch 1-8, edit-iterate loop with version chains, master gallery, 🏁 promote → front
   ref + base, 📖 Profile & Lore (Story Builder substrate, ✨ LLM fill).
2. **Create / Clothes / Pose Library — 🎯 KLEIN MODE (v1.277.13)**: Create IS Klein 3.0
   (VNCCS-Klein/Qwen create lanes and the Emotions tab left this mode — all retained,
   untouched, in **VNCCS Native** at `/studio/vnccs`); **👗 outfits render on the CLOTHES
   tab; 🕺 poses + generate on the POSE LIBRARY tab** (`Klein3Panel only: main|outfits|
   poses` — one component, JSX-gated, current-character shared across tabs). Views are
   face-anchored (v1.275.2) — but see the v1.275.8 RETRACTION in CHANGELOG: the anchor
   does NOT drive view identity. **The lever is the REFERENCE LIST** (v1.275.9): one ref
   per tag, uploads first, back views last, cap 3.
   **👗 Outfit sets (v1.276.2):** `POST /api/klein3/characters/{slug}/outfits` — a named
   outfit, 13 optional garment slots (4 core + 9 detail), optional `variant` for a look
   within it, rendered across every view. Each view is its own standalone image.
   **Per view (v1.276.16):** its OWN prompt (naming the facing) and its OWN reference list;
   ↻ re-render one · 🗑 delete one · ＋ missing (`only_missing:true`) fills only the gaps.
   **🖼 From a photo (v1.276.17):** `POST /outfits/scan` (multipart, optional `keep`="just
   the hat") vision-names the garments into the 13 slots AND saves the photo as a `garment`
   ref; `garment_ref` then passes it to the render as image 2, cited BY SLOT NUMBER.
   **✎ Editor (v1.276.17):** ＋ New outfit · click a row to load it · 💾 Save
   (`POST /outfits/update`, metadata only — NO render; rename moves the images, 409 on
   collision) · ＋ new variation.
   **🙂 Five views + ORDER (v1.276.21):** an outfit produces front · back · left · right ·
   **face close-up** — ⚠ the close-up is **CROPPED from the front render, not rendered**
   (v1.276.29), and the front is upscaled first (v1.276.37). Order is ① front → ② close-up FROM that front → ③ the rest,
   each given the close-up as its face reference (`styled_face`), so jewellery and collar
   propagate instead of being re-invented at 40px. "Regenerate all" = the whole SET; a view
   with no base image to dress is reported in `skipped`, never silently dropped.
   **⭐ Base-set views (v1.276.18/.19):** each view job is measured after it renders
   (`_facing_verdict`, free CPU) and re-rendered if wrong; a side view gets the opposite
   profile MIRRORED as its direction reference (right view ~1/4 → 5/5); RIGHT is deferred to
   a second pass on a fresh character so LEFT exists to mirror; accepted views SUPERSEDE
   older generated ones. `POST /views/verify {demote}` audits an existing set for free.
2a. **👗 Costume Library** (v1.276.27→.35: 🧪 candidates → ✅ approve → library; 🔎 filter by cut + free-text search + ✏️ rename + ℹ info (prompt/slots/refs/seed); `adopt` refuses an
   unapproved candidate · 👤 `wearer` woman/man/unisex · fanned across workers with per-image
   status — **Krea 2 fans out too since .31** (all 3 boxes have krea2_turbo_fp8; the
   one-box rule was inherited habit) and workers are assigned ROUND-ROBIN up front,
   because asking the dispatcher per-thread handed every thread the same box · the custom prompt describes GARMENTS and keeps
   the mannequin framing — `raw_prompt` opts out · 🖼 REFERENCE IMAGES for edit models only (`POST /refs`, `refs:[id]`; klein 5 / qie 2, uploader hidden for text-only models) · 📷 with a reference and no typed prompt the garment text is VISION-SCANNED off the
   image · ⚠ garments-first ordering made things WORSE, not better) (`/api/costumes`, `backend/api/costumes.py`) — ✍ describe an outfit
   and the TEXT model fills the 13 slots · 🎨 design it as an image on a **neutral mannequin**
   (**Krea 2 default**, z_image/anima/klein selectable, custom prompt override) · a SHARED
   library so one costume dresses a cast · adopt COPIES it into a character as a `garment` ref
   and rescans it into the slots. ⚠ Krea 2 uses forge's own graph, not the generic t2i path.
2b. **🎭 `/studio`** — the Character Studio front page: EVERY character from EVERY mode
   (both stores), pipeline checklist per card, capability-aware actions, the ＋ New Character
   picker (0b above) and the **⚡ Autogen board** (queue / running / stop / retry), which
   renders nothing when nothing is queued. **🔍 verbose** (persisted) or ▸ per job expands to
   the stage chain WITH per-stage durations, the base-gate verdict, candidate scores, outfits,
   QC counts, estimate-vs-reality and the **full timestamped log**; ⏱ elapsed ticks while
   running and freezes when done (v1.276.46). The collapsed row shows **◎ likeness** (green at
   or above the 0.45 match band, amber below) and a **⚠ epoch** chip when the installed epoch is
   not the best-scoring one — "did the 7 hours produce a good LoRA" should not need a click
   (v1.276.52). ⚠ Timing comes from the SERVER (`t0`,
   `stage_times`, `elapsed_s`) — a browser stopwatch would be wrong after any reload or restart,
   which is exactly when an hours-long run needs it. ⚠ The 🎓 LoRA chip reads the WORKER's installed
   files as well as the app's own train state — LoRAs trained from `scripts/` before the
   in-app pipeline existed used to read as "no LoRA" (v1.276.42).
2c. **⚙ Experimental Modes** (Settings, default OFF) — hides 🧪 Klein 1.0 and 🚀 Klein 2.0
   from the mode picker. Code untouched; kept for later game-asset export.
3. **Pose Library** — SETS/TAGS, imports, generate-missing.
4. **🎓 LoRA Dataset Gen** — the dataset+training lane:
   - **⚡ Autogen** (top, the LORA-PANEL one — `POST /api/lora/autogen`): a character that
     already HAS a base → whole recipe → installed LoRA. Options: 👕 signature outfit /
     👗 wardrobe variations (vision-proposed outfits mixed in so the base outfit isn't baked),
     "dataset only". ⚠ **This button had never once worked before v1.276.41/.42** — it
     deadlocked the event loop by calling the app's own API from an `async def` route, and its
     step 1 posted `{}` to `views/generate`, which 400s. Both fixed.
     ⚠ **Not to be confused with ⚡ Autogen v2** (`/api/autogen/*`, §10) which starts from
     nothing and batches; that one is reached from ＋ New Character, not from here.
   - Per dataset: plan/render/caption/QC/repair/export, and **🚀 Train** (export → upload →
     Fizgig → ArcFace pick → install; background, stages persisted, survives restarts and
     box dropouts; `{run_id}` attaches to an existing run).
5. **🪪 Character Sheet** — downloadable reference sheets: turnaround+face from
   identity-scored dataset images, and **per-OUTFIT sheets** (v1.277.2 — five cells from
   that outfit's OWN views only; every generated outfit auto-builds its 2048px sheet since
   v1.277.12: the MiniMax identity anchor). Labelled sheet library in the panel. No GPU.
6. **🎬 Video Lab** — a HOME-SCREEN destination since v1.277.7 (`/video-lab`; the studio
   strip button is gone, `?tab=video` deep links still resolve). MiniMax H3, LOCAL. Five
   modes: 📝 t2v · 🖼 i2v · 🎞 first+last · 🎯 last-frame · 🧩 references→video (≤9 images,
   ≤3 videos each with a 🔊 use-its-soundtrack toggle, ≤3 audios, match/max ref fidelity).
   720p default (1280×736), ⚡ turbo-lora path default (8-step) vs 20-step quality, 🌀
   SPECTRUM opt-in (quality may suffer), 🧠 Draft prompt (Ollama + the verbatim H3 spec —
   NEVER the prompt enhancer), and **⬆ Upscale (LTX 2.3 enhancer)** on every finished
   render (720p/1080p/1440p). Jobs persist + survive backend restarts. Method + anatomy:
   `docs/MINIMAX_H3_PROMPTING.md` Part 3; source graphs in `tempworkflows/` (gitignored).

## 3. The measured method (do not re-litigate)

- **Dataset recipe:** face_heavy 40 · universal face ref · dressed base · ONE targeted
  re-render round on below-match rows (<0.45, non-back) · `min_likeness 0.25` at export.
- **Checkpoint pick:** ArcFace on run-window-filtered previews vs the character's own
  refs. NEVER loss. TURBO exam scores above RAW previews (3/3 runs).
- **Inference rules:** strength 1.0 · trigger + class in prompt (shown in the 🧬 picker) ·
  ALWAYS name the outfit (captioned clothing = promptable clothing; bare prompt = skin) ·
  unload the LoRA for shots the character isn't in.
- **Proof:** dorian 0.8118 (ds median 0.69) / redv1-v1 0.5677 (0.534) / redv1-v2 0.6089
  (0.5684) vs no-LoRA controls 0.12/0.05 — likeness tracks dataset quality.
- Klein prompts are AFFIRMATIVE only (no negative node, cfg=1: "do NOT X" injects X).
- **🙂 The outfit FACE view is a CROP of that outfit's front render** (v1.276.29), not a second
  render — it cannot disagree with the costume, and costs no extra Klein render.
- ⚠ **`asyncio.create_task` from a background thread raises** (no event loop in that thread).
  `_start_ref_upscale(..., blocking=True)` when off the event loop. **A status set before the
  work is scheduled is a status that can lie** — it hung at "running" with idle workers.
- ⚠ **A costume reference hands over EVERYTHING in it, including the mannequin's stand.**
  v1.276.28: the dress form's pole was rendering onto characters. Fixed by changing what the
  mannequin IS (full-body, standing on its own feet) rather than asking for the pole to be
  omitted — "no stand" paints a stand at cfg=1. ⚠ costumes designed before .28 still have it.
- ⚠ **Krea 2 renders through `forge.py`, never the generic t2i builder** — the box lacks the
  decorator nodes and the unet name in the workflow is not what is installed
  (`_krea2_unet()` discovers it). Raw workflow ⇒ flat 400 from `/prompt`.
- **⭐ DESCRIBE WHAT IS IN VIEW; DO NOT NAME WHAT IS NOT.** Three occurrences now: category
  words, the franchise name, and (v1.276.24) a chest emblem named in a BACK view — Klein
  renders what is named and puts it where it can be SEEN, so the back came out with the
  emblem across it. Contradicting it afterwards does not work; `_back_garments()` removes the
  front-only detailing instead. ⚠ the outfit verifier is view-aware for the same reason.
- **🔗 Side views get the OTHER side's render, MIRRORED, as garment evidence** (v1.276.26) —
  measured 5 → 1 mismatch at a fixed seed, facing preserved, identity unchanged. Mirrored
  because a raw frontal or opposite profile drags the facing. Instrument:
  `scripts/k3_side_compare.py` (free). ⚠ n=1 pair; `sibling_ref` toggles it.
- **A close-up copies its reference's FRAMING**, so a full-body reference makes a bust shot —
  crop the reference first (`_headshot_of`).
- **⭐ DESCRIBE THE GARMENT, DO NOT NAME THE FRANCHISE.** A character name in a garment slot
  brings that character's accessories with it. MEASURED 2026-08-10: "a blue supergirl
  leotard" produced Clark-Kent glasses on **5 of 5** renders, and corrections did NOT remove
  them (0/3 appended, 0/3 in leading position); the same costume described literally, at the
  SAME seed, produced none on the first try. Klein answers what a phrase EVOKES, not only
  what it lists.
- **👁 Outfit renders are vision-checked** against the garment list (missing / wrong colour /
  **EXTRA**) and re-rendered; a failing outfit view is KEPT and flagged, unlike a base view
  which is left missing. Correction phrases are affirmative — "no glasses" adds glasses.
- ⚠ **An in-place upscale needs a VERSIONED URL** or the browser serves the stale copy and it
  looks like nothing happened (v1.276.25 — `_ref_url()` appends the file mtime as `?v=`).
- **🔍 A reference under 768px short side gets auto-upscaled** (SeedVR2 preferred) on BOTH the garment scan and the ordinary ⬆ Upload reference path: every
  reference is scaled to ~1MP anyway, so a small file is being scaled UP out of detail that
  was never there.
- **NAME THE FACING in any job that has one.** "camera angle" is a category word too:
  outfit sets built one prompt for all four views and every view came back frontal
  (v1.276.16). Per-view prompt, per-view reference list.
- **Never feed a lane its own previous output as an identity reference.** Twice now:
  view generation (v1.275.9) and outfit sets (v1.276.16). Check `refs_used` on the job.
- **Never feed a view job the OPPOSITE profile AS-IS — mirror it.** One shared reference list
  meant the RIGHT view was shown the LEFT profile and rendered a left-facing pose
  (v1.276.17). Dropping it was the first fix and it was not enough (see the next bullet):
  a mirrored opposite profile is the DIRECTION reference that side views need.
- **⭐ A side view needs a picture of its DIRECTION (v1.276.19).** Dropping the opposite
  profile left a side job with two FRONTAL refs and nothing saying which way to turn — the
  model's prior then decided, ~3 times in 4 wrongly. The opposite profile is now MIRRORED
  and passed as reference 3, cited BY SLOT NUMBER in the prompt. **Right view: ~1 of 4 →
  5 of 5, single attempt.** (Also why the 🪞 mirror retry underperformed: mirroring a frontal
  image is nearly a no-op.)
- **The base set verifies itself (v1.276.18).** `views/generate` measures each finished view
  (free, CPU insightface) and re-renders it if it shows the wrong thing; retries alternate
  plain / 🪞 mirror. **If every attempt fails the view is left MISSING** — autogen can stop on
  a missing view, it cannot see a wrong one. `POST /views/verify {demote}` checks an existing
  set for nothing.
- **A correct API does not mean a correct screen.** v1.276.32: the costume candidates/library
  split was right on the server and a polling loop overwrote it in the UI every 3 seconds. A
  user reporting what they SEE needs a check that reaches the screen, not the endpoint.
- **Facing is a MEASUREMENT, not a glance.** `scripts/k3_face_audit.py` prints head yaw —
  negative = nose toward the LEFT edge. A 340px full-body thumbnail is not readable for
  profile direction; on v1.276.17 I called a working fix broken from one and spent a render
  on it.
- **Klein ignores CATEGORY words — NAME every garment, every time.** Broken again on
  2026-08-09: a view prompt said "SAME outfit as the references" and the sides came back in
  different trousers and boots. If a prompt says "outfit", "clothing" or "attire" instead of
  naming the items, it is wrong.
- **🙂 An outfit render must be told WHERE the face is.** It edits a generated view, so
  reference 1 is already a copy; the face crop is pinned to slot 2 and cited BY NUMBER
  ("take the face from image 2, not from image 1"). v1.276.20, front 0.52–0.58 → 0.66 (n=1).
- **Upscale models: use a PHOTOREAL one.** ⚠ v1.276.20: `base/upscale` had MISSED this fix
  for six versions and was still running the anime model. If a route takes `model_name`,
  check it defaults to `_GAN_MODEL_DEFAULT` and not "whatever the workflow has baked in". `STUDIO_UPSCALE.json` defaults to
  `4x_APISR_GRL_GAN_generator.pth`, which is an ANIME model — it posterises faces and draws
  line-art hair. Default is now `4x-ClearRealityV1.pth`; measured worth 0.8440 → 0.9840 of
  face-crop anchor identity.

## 4. The agent (how Claude drives everything)

`scripts\agent.bat` on the app machine — **it dies with its console window; relaunch it
after any dead session** (queued jobs then run). Claude writes JSON into
`scripts/_agent/inbox/<id>.json`, reads `scripts/_agent/outbox/<id>.json`. Heartbeat:
`scripts/_agent/status.json`.

| kind | does | example |
|---|---|---|
| `http` | any LAN request | `{"kind":"http","method":"POST","path":"/api/lora/datasets/X/qc","body":{"overwrite":true}}` — add `"host":"http://192.168.12.201:8765"` + `?token=…` in path for the helper |
| `script` | anything under scripts\ | `{"kind":"script","file":"checkpoint_score.py","args":["--run","ID","--char","redv1","--helper","http://IP:8765"]}` |
| `upload` | file bytes → helper | `{"kind":"upload","file":"C:\\…\\export.zip","host":"http://IP:8765","path":"/datasets/ID?token=…"}` |
| `download` | URL → repo (confined) | `{"kind":"download","path":"/api/…/image","to":"scripts/_diag/x.png"}` |
| `restart` | run.bat cycle + health wait | use after EVERY backend patch |

⚠ **Agent-driven publishes MUST pass `-Yes`**: `{"kind":"script","file":"publish_clean.ps1",
"args":["-Yes","-Message","release vX.Y.Z - ..."]}`. Without it the script's
`Read-Host "Proceed? (y/N)"` blocks forever headless — a publish hung a full hour on it
(v1.277.3) until the agent's 3600s script timeout killed it.

## 5. The worker helpers (v1.219, on ALL boxes)

`rbmn_helper.py` :8765 on every worker — dataset/training lifecycle on the trainer, plus
fleet plumbing (inventory, installs, LoRA sync) everywhere. Cannot self-update: copy the
repo file over `D:\RBMNHelper\rbmn_helper.py` on each box, restart its bat (run history
survives — `load_runs` reads state.json). **Tokens are per-box auto-generated** — the
`TOKEN …` banner in each helper console; paste into the Settings registry row.
`/health` is UNAUTHENTICATED (fine for liveness, proves nothing about your token).

Key routes (all others `?token=…`):
- **Config/paths:** `/config` GET/POST (comfy.root/start_cmd, fizgig.root/python — the
  source of truth for which install runs; surfaced + switchable in Settings via 🔍 Detect)
  · `/detect` · `/comfy/stop|start`.
- **Training (trainer box):** `/datasets/{id}` POST zip · `/runs` POST {dataset}
  (hard-stops ComfyUI first) · `/runs/{id}` (+`?kind=weights|image`) · `/runs/{id}/log` ·
  `/runs/{id}/artifacts/{name}` · `/runs/{id}/install-lora` {name, dest_name?, force?} —
  window-guarded (out-of-window file = the OTHER run's checkpoint) · `/runs/{id}/cancel`.
- **Fleet (v1.218/219, any box):** `/inventory` (custom_nodes + model folder counts/GB +
  env: python/torch/cuda/gpu/sm/triton/sage/xformers) · `/install/node` {git_url} ·
  `/install/pip` {packages} · `/download/model` {url, folder, filename} (background,
  `/downloads` to watch) · `/install/python-headers` (include/+libs into python_embeded —
  the triton-link fix) · `/install/sageattention` {wheel_url?} · `/verify/sageattention`
  (REAL kernel call — the only proof; "installed" means nothing).
- **Full reference:** `docs/WORKER_HELPER.md`.

## 6. The loop scripts (scripts\, all take --host/--helper; run via agent)

| script | does |
|---|---|
| `find_helper.py` | subnet scan for the moved training box |
| `train_report.py --run ID` | loss curve + adaptive-LR + recaption log |
| `checkpoint_score.py --run ID --char SLUG` | ArcFace per-epoch preview scores, window-filtered |
| `fetch_pick.py --run ID --epochs 14,16` | pull epoch previews for eyeballing |
| `fetch_ckpt.py --name FILE` | download one checkpoint, window-verified |
| `lora_test.py --host IP --lora FILE --char SLUG --trigger T --cls woman` | the 6-render TURBO exam (core-node graph) |
| `lora_score.py --char SLUG` | ArcFace table over an existing exam grid |
| `preflight_autogen.py [--name "X"]` | **(v1.276.50, FREE — seconds, no renders)** every dependency a long ⚡ Autogen run needs, checked BEFORE you walk away. ⏱ **a full run is ~7h but ~32 MIN with 🚀 LoRA off — training is 92% of it** (v1.276.51): all 3 boxes + their helpers · the fleet is actually 3 (⚠ a box down at startup is not registered — but since v1.276.48 the health loop re-attaches it on the next 45s sweep, so WAIT and re-run rather than restarting the backend) · every box has the `klein` cap · Ollama vision · **ArcFace** (the free base gate AND the epoch scoring that picks the LoRA) · trainer online + token + Fizgig paths · trigger/slug collisions. ⚠ It canNOT check free disk on the trainer, or whether the boxes STAY up. |
| `test_weights_pick.py` | **(v1.276.49, FREE — no worker, no GPU, no network)** unit-tests `_weights_for_epoch`, the checkpoint picker whose absence cost a 7-hour run: exact match · nearest-lower fallback · `install_note` · 4- vs 6-digit names · `-state` files ignored · the raise-that-names-the-files case. **Run after touching the train pipeline.** |
| `autogen_smoke.py` | **(v1.276.42, FREE — zero renders)** drives ⚡ Autogen end to end: queue, serial drainer, state files, cancel, retry, batch, cleanup — using a spec whose only stage is `base` FROM A PHOTOGRAPH. **30 checks** (one SKIPs when no view job is in memory to read). Creates throwaway characters and deletes them (`--keep` to inspect). Run this after ANY change to the autogen lane. |
| `build_frontend.bat` | **(v1.276.40)** the same `npx vite build` run.bat ships, but with its output VISIBLE and its exit code meaningful — run.bat builds with `>nul 2>&1` and serves the OLD `dist/` behind a one-line warning when it fails. It then runs `tsc --noEmit` as INFORMATION, not as a gate. ⚠ **v1.276.41 CORRECTION: `npm run build` (= `tsc && vite build`) had never once succeeded because tsc reported 16 pre-existing errors — those are now CLEARED and tsc is clean, so it would work.** Prefer this script anyway: it runs exactly what `run.bat` ships, so a build that passes here is the build the app serves. Always follow with a grep of `dist\assets` for a string only your change contains. |
| `k3_new_char_from_ref.py --from SLUG --name "X"` | **(v1.276.39)** seeds a THROWAWAY Klein 3.0 character from an existing one's **uploaded** refs, over the backend's own API. The only way to exercise a fresh character's path (the `deferred` right-waits-for-left pass) without clicking through the UI. ⚠ uploads only — never generated views. `--delete` removes it. |
| `k3_side_compare.py --char SLUG --outfit "NAME"` | **(v1.276.26, FREE — CPU + one Ollama call)** scans each SIDE view of an outfit with the proven single-image slot prompt and diffs the dicts IN CODE. ⚠ Its first design asked the vision model to "list the differences" and got an EMPTY list for both baselines, including one where the shoe colour plainly differed — a 7B model comparing two images holistically says "same costume" and stops. Still noisy: some of the score is one side being described more thoroughly. |
| `k3_face_audit.py --char SLUG` | **(v1.275.4, FREE — CPU, no GPU, no worker)** ArcFace-scores EVERY ref of a Klein 3.0 character against the uploaded front ref AND against the face anchor, plus head yaw / keypoint yaw / detector score. Run it before spending renders on identity work. `--json` for the machine-readable form. |

The in-app 🚀/⚡ pipelines are these scripts' logic as code (`backend/api/lora_train.py`);
the scripts remain the manual/recovery path.

## 6a. 🩺 When a box drops out (v1.276.48)

**A worker's `healthy` flag is now actually re-checked** — every 45s, in a thread. Before .48
`health_check_all()` existed and **nothing called it**, so `healthy` was an optimistic value set
at registration and never revisited: a rebooted box read as healthy indefinitely, and since
v1.276.45 fans work round-robin across every healthy worker, that meant **every Nth image of a
batch failing** rather than one idle box.
- Transitions are logged loudly: `Worker WENT DOWN` / `Worker RECOVERED` / `Worker REJOINED the
  fleet`. Steady state is quiet — warning every 45s about a box that is simply off buries the
  moment it went down.
- **A worker that is unreachable AT STARTUP is retried each sweep.** `add_worker` raises on an
  unreachable box so it never enters the registry, and the health loop only iterates the
  registry — so before .48 a box asleep at boot was invisible until the next restart.
- `GET /api/debug/snapshot` publishes **`last_check`** per worker. *"Healthy" without "as of
  when" is the claim that hid all of this.*

**⚠ THE TWO PORTS ARE INDEPENDENT — check both.** `:8188` is ComfyUI (rendering) and `:8765` is
the helper (datasets, training, installs). A box can have one up and the other down, and they
fail completely differently: no ComfyUI = the box silently leaves the render fleet; no helper =
training dies with a connection timeout. Restarting "the worker" often means only one of them.

**Recovery, from this session's actual failures:**
- *training finished but the INSTALL failed* → **do NOT retrain.**
  `POST /api/lora/datasets/{id}/train {"run_id": "<the finished run>"}` re-attaches and redoes
  score + install only.
- *a box vanished from the fleet, or was asleep when the backend booted* → it **rejoins by
  itself within 45s**; watch for `Worker REJOINED the fleet`. No restart needed.

    scripts\find_helper.py          scans the subnet for BOTH ports
    GET  http://<ip>:8188/system_stats     is ComfyUI up
    GET  http://<ip>:8765/health           is the helper up (unauthenticated)
    POST http://<ip>:8765/comfy/start      the helper can START ComfyUI for you

## 6b. ⚡ FAN-OUT — which lanes use the whole fleet (v1.276.45)

⚠ Since v1.276.48 `last_check` IS refreshed every 45s — but `health_check_all` stamps every
worker with one shared `now`, so the `-last_check` tiebreak still collapses and the fact below
is unchanged. Do not "fix" the sort expecting balance.

**⚠⚠ THE ONE FACT BEHIND EVERY PIN IN THIS APP.** These image lanes submit straight to the
ComfyUI client and never go through `dispatcher.submit_job`, which is the only thing that
increments `in_flight`. So **`in_flight` is permanently 0 on every box**, and
`select_worker`'s sort key `(in_flight, priority, -last_check)` collapses to a constant — it
returns the SAME box every time. **Calling `select_worker` once per image in a loop does not
load-balance; it pins.** The only thing that actually spreads work here is a worker LIST with
**round-robin assigned UP FRONT**, or `_parallel_klein_edits`' thread-per-worker + shared queue.

| lane | fans? | why |
|---|---|---|
| `_parallel_klein_edits` (klein3) — views, outfits, strip, posefit, poses, LoRA dataset render | ✅ | one pinned thread per worker pulling a shared queue; work-stealing, so an uneven mix self-balances |
| base views, first pass | ✅ | front/back/left are independent |
| base views, `right` | ⏸ **by dependency** | needs LEFT finished and mirrored as its direction reference. Only `right` waits |
| outfit set | ✅ / ⏸ | front → face-crop → the rest; `right` is a separate phase **only when `sibling_ref` is on** (v1.276.45 — it used to split unconditionally, making a 5-view set 4 phases deep with width 2 on a 3-box fleet) |
| forge Klein (`_fan_out_klein`) | ✅ | same pattern |
| **forge Krea 2** | ✅ **since v1.276.45** | was SERIAL ON ONE BOX out of habit — all three have `krea2_turbo_fp8`. ⚠ a job naming a **LoRA** still pins to boxes that have that file, checked per box |
| costumes `/design` | ✅ | round-robin up front (v1.276.31), Krea 2 included |
| **Image Workshop** | ✅ **since v1.276.45** | was a serial `for` loop calling `select_worker` per image — i.e. N images, one box, one at a time |
| LoRA `/qc` | ✅ *different pool* | one thread per **Ollama** server, not GPU. ⚠ publishes `tasks[].server`, not `worker` |
| GAN / SeedVR2 upscales | ➖ one box | genuinely ONE image. `seedvr2` is a real capability constraint. v1.276.45 made them REPORT their worker (`aux_renders`) — they used to be invisible and therefore un-cancellable |
| ⚡ Autogen | ➖ orchestration only | it renders nothing itself; it calls the lanes above, so it inherits their fan-out. **Batch across characters is serial ON PURPOSE** |

**Proved, not asserted** (v1.276.45, one throwaway character, 4 renders):

    front -> .163   back -> .224   left -> .201     (three boxes, one pass)
    right -> .163   deferred, because it waits for left

⚠ **`_klein_workers_all` silently degrades to a ONE-worker pool** if no box reports the `klein`
capability, and nothing in the job status says the pool shrank. `scripts\autogen_smoke.py` §6b
now checks the capability count for exactly this reason — free, no renders.

## 7. Key API surfaces (beyond the UI)

- `/api/lora/*` — datasets, plan(-preview) (re-plan DESTROYS moved slots; force required),
  generate, caption, qc, repair, likeness (per-angle baselines), wardrobe-check, export
  (min_likeness floor), **train, train/status, autogen, autogen/{slug}/status,
  trainer-settings, trainer-paths, trainer-detect**.
  ⚠ **THERE ARE TWO AUTOGEN LANES AND THEY ARE NOT THE SAME THING.** `/api/lora/autogen`
  (here, `lora_train.py`) starts from a character that ALREADY has a base and ends at a LoRA —
  it is the dataset+train recipe. `/api/autogen/*` (below, `autogen.py`, v1.276.42) starts from
  NOTHING (photos or a description), stops wherever you toggled, and runs a serial queue across
  characters — and it CALLS these routes rather than duplicating them.
- **`/api/autogen/*`** — ⚡ Autogen v2: `run` · `batch` · `estimate` · `refs` ·
  `refs/{rid}/image` · `jobs` · **`jobs/{id}?log=N`** (−1 all, 0 none) ·
  `jobs/{id}/cancel|retry|delete` · `queue/clear` · **`queue/pause` {paused}** (v1.277.2 —
  ON-DISK flag; the running job finishes, nothing new starts; survives a reboot, so pause →
  restart → resume keeps a large batch intact) · `health`. `workers_used` persists per job
  (v1.277.1). Timing + log fields in §10.
- **`/api/storyworld/*`** (v1.277.0) — 🌍 Story / World Builder, home page → `/worlds`:
  `meta` (server-driven field vocab) · `llms` (the per-task brain picker) · `worlds` CRUD +
  `rename|world|llm` · `stories`/`texts`/`cast` CRUD (all MERGE, never replace) ·
  `enhance/world|story/{sid}|cast/{cid}|field` (fill|overwrite, optional `{provider,model}`
  override) · `cast/generate` (LLM proposes ≤cap, lands as PAPER) · `bigbang` (idea → world +
  stories + cast) · **`cast/submit`** (level `details`…`lora`, `estimate_only`, builds
  AutogenSpecs and calls autogen `_enqueue` IN-PROCESS — the bulk-submission producer) ·
  `cast/status` (joins the board to the queue, write-backs terminal results). Worlds live in
  `_libraries/storyworld/worlds/`. Free suite: `scripts/storyworld_smoke.py` (44 checks).
  ⚠ `POST /cast/{cid}` is declared LAST in the module — route order is load-bearing
  (literal `/cast/submit`·`/cast/generate` must match first).
  🎨 **Visual style** (v1.277.2): `style` {preset, custom_text} · `style/ref` (multipart —
  the VISION model describes the artistic STYLE, never the content) · `style/samples`
  {count, model, direction} (klein+ref when a style ref exists, else t2i; fanned; live
  status at `style/job`) · `style/samples` GET/list · `style/samples/{sid}/image|delete`.
  The style text is injected into every LLM context for that world.
- **`/api/charsheet/*`** additions (v1.277.2): `generate` takes `outfit_name`/
  `outfit_variant` — the `outfit` preset (5 cells) composes ONLY from that outfit's
  rendered views; the sheets list carries `outfit` metadata. The Video Lab's 📚 buttons
  open `CharacterImagePicker` (sheets/views/dataset, preview-first) and re-register the
  picked image as an H3 upload.
- `/api/costumes/*` — 👗 the shared costume library: `draft` · `design` · `job` · `models` ·
  `refs` · list/filter · `{cid}/approve|adopt|rename|delete` · `candidates/clear`. See §2a.
- `/api/forge/*` — 🧬: characters (name-first), generate, edit, gallery, promote, lore(+
  generate), engines, **loras (with trigger map), krea2-host**.
- `/api/charsheet/*` — characters, generate, sheets (+`?download=1`).
- `/api/h3/*` — 🎬: overview (workers/caps/defaults), upload (image|video|audio),
  generate (all five modes, `draft:true` = the v1.0 8-step turbo lora at 4 STEPS —
  v1.277.9's testing mode; **`plan_upscale` default TRUE** — renders one H3 step longer so
  the LTX upscaler's 8k+1 flooring eats slack, not the tail; the upscale is trimmed back to
  `target_frames`), jobs (+{id}, DELETE), media/{id} (+`?download=1`),
  jobs/{id}/upscale (LTX 2.3, largest_size 1280/1920/2560), draft-prompt (Ollama+spec),
  **llm-prompt** (v1.277.11 — the verbatim prompting-agent text from
  docs/MINIMAX_H3_LLM_PROMPT.md, for external LLM use; the 🤖 button in the Video Lab).
- **`/api/projects/{pid}` engine + story routes (v1.277.12)** — `PUT video-config` (merge:
  `video_engine` ltx_2.3|ltx_2.5|minimax_h3 + the H3 knobs `h3_turbo/h3_draft/
  h3_audio_mode(project|model)/h3_use_audio_ref/h3_ref_image_size/h3_auto_sheet_refs`;
  an empty body READS without writing) · `PUT/GET story-link` (two-way world/story link) ·
  `POST pull-from-story` (concept/style/cast-with-images/texts→lyrics) ·
  `POST concept/characters/autogenerate` now submits ⚡ AutogenSpecs (real klein3
  characters) with `POST concept/characters/{i}/adopt-k3` as the write-back.
- **`/api/audio-lab/*` (v1.277.14)** — 🎧: `overview` (per-worker engine detection from
  object_info + model listings) · `music/generate` (ace15; exact seconds/bpm/key;
  minimax3 auto-detected, graph pending) · `tts/voices` CRUD (clone = 5-15s clean
  sample + exact transcript) · `tts/generate` (blank-line chunking, pause_ms between
  paragraphs) · `jobs`(+{id}) · `media/{id}` · `jobs/{id}/send-to-project` (MUSIC
  asset). Staging: `scripts/install_audio.py` (+`--minimax3`); F5 node needs a ComfyUI
  restart after install. Helper v1.220: `/serve/model/{folder}/{file}` (peer copy — 
  `scripts/copy_models_to_peers.py`) + `/ollama/status` (LLM-only boxes).
- **`/api/storyworld` locations (v1.277.14)** — `locations` CRUD + `/generate` (scout)
  + `/{lid}/enhance`; sheets feed world/story/cast LLM context; ⚠ literal `/generate`
  declared before `/{lid}`.
- **Dispatcher video workflow types (v1.277.12)**: the `ltx_*` family, plus **`h3_i2v` /
  `h3_first_last` / `h3_ref2v` / `h3_t2v`** — a `minimax_h3` project's LTX-typed jobs are
  REWRITTEN to these in `_build_workflow`; scene `video_refs.urls` + auto outfit sheets
  feed `h3_ref2v` (H3's identity mode). Transitions stay on LTX. A missing MiniMaxH3 node
  FAILS the job loudly; h3 jobs skip RunPod.
- `/api/klein3/*` — refs, base, strip, views/generate, generate(-set), posefit.
  Loop script: `k3_side_compare.py --char <slug> --outfit "<name>"` (free, CPU) scores how
  far an outfit's LEFT and RIGHT views disagree about the garments.
  **views/generate** {views, verify=true, max_tries=3, mirror_retry, mirror_first,
  ref_count=3, face_first, regen_face} — per-view prompts AND per-view reference lists, a
  MIRRORED opposite profile as the direction reference on side views, post-render facing
  verification with retry, and RIGHT deferred to a 2nd pass on a fresh character. Status
  publishes `refs_used`, `refs_pool`, `angle_ref`, `attempts`, `failed`, **`deferred`**
  (which view waited and why) and, for outfits, **`phases`** = which views ran in parallel,
  which were deferred, and WHY.
  **`POST /characters/{slug}/views/verify` {demote}** — audit an EXISTING set, free (CPU
  insightface, no worker); `demote` files failures under `other`+`rejected` so the view reads
  as missing again.
  **Outfits:** `GET|POST /characters/{slug}/outfits` (13 named slots, variants, rendered per
  view; **SLOT SEMANTICS — re-running REPLACES that (name, variant, view) in place**, which
  is what makes ↻ regenerate safe after changing base images; `only_missing:true` renders
  ONLY the gaps and 409s when there are none; `garment_ref` passes a scanned photo as
  image 2) · `POST /characters/{slug}/outfits/delete` {name, variant?, view?} (files removed
  too) · `POST /characters/{slug}/outfits/scan` (multipart `file` + `keep`) → 13 slots +
  a `garment`-tagged ref · `POST /characters/{slug}/outfits/update` {new_name?, new_variant?,
  slots?, extra?} — **metadata only, renders nothing**; a rename MOVES the images, and a
  collision 409s rather than merging two wardrobes.
  **Reference upscale:** `POST /characters/{slug}/refs/{rid}/upscale` {model_name?,
  max_side=2048} — the STUDIO_UPSCALE GAN on ONE reference, in place. ⚠ the GAN returns 4x
  (832×1216 → 3328×4864, 5.33 MB measured); the cap exists because a reference is uploaded
  to a worker on EVERY render that reads it. Job key `refup`.
  `refs/{id}/image?download=1` gives a meaningfully-named single file.
  ⚠ **`_public_char` WHITELISTS ref fields** — a new field on a ref record is invisible to
  the UI until it is named there (`upscaled` shipped broken exactly this way).
- `/api/characters` — **the unified character list (v1.276.0)**: every character from every
  mode, from BOTH stores, with polymorphic ids `k3:<slug>` / `db:<uuid>`. Use this rather
  than assuming a character is a `studio_characters` row — Klein 3.0 characters never are.
- `/api/lora/datasets/{id}/base-outfit` — GET options / PUT the outfit a dataset renders
  from. Opt-in; unset keeps the existing base behaviour exactly.
- Zero-cost preflights: `/api/health`, `/api/lora/health`, `/api/lora/likeness-health`,
  `/api/klein3/health`, helper `/health`.

## 7c. Secrets, publishing and repo hygiene (v1.276.23)

**The helper token is NOT in the source any more.** It used to be a hard-coded default in
seven tracked files. `scripts/helper_token.py` resolves it, first hit wins:

1. `RBMN_HELPER_TOKEN` in the environment
2. `scripts/helper_token.txt` — **gitignored**, this is where it lives on this machine
3. `token` / `helper_token` / `trainer_token` in `_libraries/forge/settings.json`
4. empty — which surfaces as a helper 401, a far better failure than a token from git

Rotating the token means changing it on the boxes and updating (2) or (1). Nothing else.

**⚠ THE WORKING REPO STILL TRACKS 953 FILES THAT .gitignore EXCLUDES** — `_diag/` (683),
`VNCCS302/` (266), the restore zip, `HANDOVER_PROMPT.md`. That is the documented trap:
.gitignore does not untrack anything already committed. The publish step is what keeps them
out, and since **v1.276.23 it derives that automatically** from
`git ls-files --cached --ignored --exclude-standard` rather than from a hand-written list —
so **adding a rule to .gitignore is now genuinely sufficient**. The old `$DROP_DIRS` /
`$DROP_FILES` remain as a second explicit check.
⚠ Why it changed: `backend/data/character_studio/face_detection_yunet_2023mar.onnx` was
tracked, matched `*.onnx`, and was on NEITHER drop list — it would have shipped unreviewed.
It is also a genuine runtime dependency (checked at startup), so .gitignore now keeps it
deliberately with `!backend/data/**/*.onnx`. A `git rm --cached` sweep would otherwise have
silently removed face detection from fresh clones.
Verified 2026-08-10: 1502 tracked → **550 published**, HANDOVER/token/publish scripts all
excluded, the yunet model kept.

### Publishing — DONE 2026-08-09, and this is the workflow now

**⚠ THE GITHUB REPO IS PUBLIC.** Develop in this working folder; publish a curated copy.

    PUBLISH_TO_GITHUB.bat            (repo root, gitignored, double-clickable)
      -> scripts/publish_clean.ps1   syncs the public file set into
      -> ..\RBMN-Storyboard_App-clean   then commits and pushes

**`.gitignore` is the single source of truth for what is public.** The file set is
`git ls-files --cached --others --exclude-standard` — everything .gitignore permits,
tracked or not — so a brand-new file publishes without needing `git add` first. The only
extra list is the handful of paths still TRACKED in the working repo that must never ship
(`_diag`, `VNCCS302`, the restore zip, the one-shot patch script, `HANDOVER_PROMPT.md`),
because **.gitignore cannot exclude a file that is already tracked.**

The script reads this repo (never a git mutation in it), shows the plan including deletions
and asks y/N, then **re-verifies the clean folder for any dropped path or
`scripts/helper_token.txt` and ABORTS before committing** if anything leaked.

**History was rewritten once, before the first push** (`scripts/git_history_clean.ps1`,
clone → `git filter-repo --invert-paths` → verify → stop). Measured: **1496 files /
372.52 MB → 550 / ~13 MB**, `.git` **880 MB → 5 MB**, all 137+ commits preserved. Why it
mattered: `_diag/last_pose_run/*/identity_*.png` are the literal reference images each run
used, and many runs used STRIPPED bases — underwear character renders, headed for a public
repo. It was never a decision that they were tracked: `.gitignore` had `scripts/_diag/` and
never the ROOT `_diag/`.

⚠ **A .gitignore rule does not untrack an already-committed file** — verified both ways.
⚠ `--path` removes PATHS, not text: the old helper token still sits in past commits inside
files that were KEPT. Moot, because it is rotated.

## 8. Recovery playbook

| symptom | fix |
|---|---|
| Claude session died, "working on it forever" | new session → check `scripts/_agent/status.json` heartbeat → relaunch `agent.bat` if stale → queued jobs resume |
| training box unreachable | box asleep or IP moved → wake it, then `find_helper.py`; update IP in Settings; helper restart keeps run history; a finished run loses NOTHING |
| backend patched but behaving old | agent `restart` job; hard-refresh; if UI looks old, the silent vite fallback — check dist for a new string |
| training died mid-run | `/runs` shows status/rc; Fizgig `--resume <state-dir>` from the run's `-state` folders; or re-run (window filter handles the shared folder) |
| wrong/stale checkpoint suspicion | mtime inside run window = this run; numbered above epoch count = the OTHER run |
| QC flags look wrong | trust the instruments in `likeness.py`/`wardrobe.py`; vision-LLM answers only for one-person/artifacts/clothing description |

## 9. Where things live on disk

- Characters: `<project>/_libraries/klein3/chars/<slug>/` (char.json + refs/ + base/ +
  forge/ gallery + lore in char.json)
- Datasets: `<project>/_libraries/lora/datasets/<id>/` (dataset.json, images/, exports/)
- Pipeline state: `_libraries/lora/_train/<ds>.json`, `_libraries/lora/_autogen/<slug>.json`
- Character sheets: `_libraries/charsheet/<slug>/`
- Forge/trainer settings: `_libraries/forge/settings.json`
- Trained LoRAs: training box `…\ComfyUI\models\loras\` (dorian-v1-…-000016, redv1-v2-e21 ←
  use, redv1-…-000036 old)
- Diagnostics Claude can read: `scripts/_diag/` (gitignored)

## 10. Current state & roadmap (2026-08-12, v1.276.54)

**Done and measured this session:**
- **✅✅ A FULL ⚡ AUTOGEN RAN CLEAN, END TO END, UNATTENDED** (v1.276.51, `walterv1`): 8/8 stages,
  7.12h, description → base → views → clothing → sheet → 40-image dataset → 24 scored epochs →
  installed LoRA `rbmnwalterv1` at **0.6285**. Base gate 4/4, charsheet with zero empty cells,
  3 of 40 flagged, 0 artifacts.
  **⏱ WHERE THE TIME GOES:** `lora 6.58h (92.4%) · clothing 15.6min · dataset 14.7min ·
  views 1.5min · gate 20s · charsheet 1s`. ⭐ **A full character WITHOUT the LoRA is ~32
  MINUTES** — leave 🚀 LoRA off while iterating on a look, tick it when the character is settled.
  ⚠ The top five epochs spanned 0.6264–0.6285: **the curve plateaus and epoch choice is worth
  0.002.** The dataset is the lever, not epoch selection.
- **🩺 WORKER HEALTH IS ACTUALLY CHECKED NOW** (v1.276.48) — `health_check_all()` had **no
  callers**, so a rebooted box read healthy forever; much worse since the .45 round-robin, where
  a dead box fails every Nth image instead of none. 45s loop, the check in a thread, transitions
  logged, and **a box unreachable at STARTUP is retried each sweep** instead of being invisible
  until the next restart. `last_check` published. See §6a.
- **🎓 THE CHECKPOINT IS LOOKED UP, NOT CONSTRUCTED** (v1.276.49) — scoring counts epochs from
  PREVIEWS, installing needs WEIGHTS, and on a real run they differed (39 vs 38): a 7-hour run
  died posting a filename that did not exist. `_weights_for_epoch()` + `install_note`, and
  `_hj` now reads the HTTPError BODY (`str(HTTPError)` carries none). Recovery without
  retraining: `POST /datasets/{id}/train {"run_id": …}`.
- **🚦 `scripts/preflight_autogen.py`** (v1.276.50) — every dependency of a long run, free, in
  seconds, GO/NO-GO. **◎ The board shows the epoch story** (v1.276.52): likeness on the collapsed
  row, and a ⚠ chip when the installed epoch is not the best-scoring one.
- **🎬 Video Lab VALIDATED ON GPU** — first live H3 render PASSED (t2v 5.17s/124f/720p/turbo,
  **377s** incl. model load, real AAC audio) and **⬆ LTX 2.3 upscale PASSED** (494s →
  1920×1088, genuine re-detailing). ⚠ H3 (f%17==5) and LTX (f=8k+1) disagree about legal
  frame counts — proven 3/3 (73→73, 124→121, 175→169) — so **`plan_upscale` defaults ON**:
  render one step longer, trim the upscale back. Verified 141→137→124 @ 5.167s with audio.
- **⭐ THE REFERENCE LIST WAS THE LEVER** (v1.275.9). `_identity_ref_paths` took every
  front-tagged ref first and view generation APPENDED one every run (v1.276.19: views SUPERSEDE) → Klein was handed
  `[upload, generated front, generated front]`. A drift loop. Fixed to ONE REF PER TAG:
  front views **0.3637 → 0.4498 mean, no overlap**. ⚠ 4 refs measured WORSE (0.3797).
  **The face anchor does NOT drive view identity — see the v1.275.8 retraction.**
- **🎭 Character Studio unified** — `/api/characters` merges the two disjoint stores;
  `/studio` rebuilt with per-character pipeline cards, filters, delete.
- **👗 Klein 3.0 outfits** — 13 slots, variants, slot semantics, delete/regenerate, and an
  outfit can be a dataset's base. **⭐ Core set separated** from other refs, **⬆ per-reference
  GAN upscale** (capped 2048 — the GAN returns 4x / 5.33 MB).
- **👗 THE COSTUME LIBRARY** (v1.276.27→.35, `backend/api/costumes.py`) — ✍ describe an outfit
  and the TEXT model fills the 13 slots · 🎨 design it as an image on a neutral FULL-BODY
  mannequin (**Krea 2 default**, fanned round-robin across all three boxes since .31) ·
  👤 cut for woman/man/unisex · 🖼 reference images for EDIT models only, with the garment text
  📷 VISION-SCANNED off the reference when no prompt is typed · 🧪 candidates → ✅ approve →
  library (adopt 409s on a candidate) · 🔎 filter by cut + free-text search + ✏️ rename + ℹ info
  (model/seed/slots/refs/prompt). ⚠ Krea 2 uses forge's own graph, never the generic t2i path.
- **👗 OUTFIT SETS ARE 5 VIEWS AND CHECK THEMSELVES** (v1.276.16→.29) — per-view prompts AND
  per-view reference lists; the 🙂 face view is a CROP of that outfit's own front render (not a
  second render, so it cannot disagree) — and since .37 the FRONT is upscaled BEFORE the crop
  (712×876 → 1192×1464 of real detail); back views strip front-only detailing; side views get
  the opposite side MIRRORED as garment evidence (mismatch 5 → 1 at a fixed seed); every view
  is vision-checked against the garment list (missing / wrong colour / **EXTRA**); ↻ / 🗑 per
  view and ＋ missing fills only the gaps.
- **🧭 THE BASE SET IS SELF-CHECKING AND FACES THE RIGHT WAY** (v1.276.17→.19) — the right
  view came out left-facing ~3 times in 4. Cause: after v1.276.17 dropped the *wrong* side
  reference, a side job held **two FRONTAL refs** and nothing said which way to turn, so
  words fought a model prior and lost. Fix: the opposite profile is **MIRRORED back in as a
  direction reference**, cited by slot number. **~1 of 4 → 5 of 5 first-attempt; confirmed by
  Lorenzo on his own characters.** Plus free per-view verification with retry (back = no face
  detected), views left MISSING rather than mis-tagged when every attempt fails, and
  supersede-don't-stack.
- **🖼 THE BASE SET FIXED** (v1.276.14/.15) — three defects in one upscaled image:
  `STUDIO_UPSCALE.json` had an **ANIME** model baked in (APISR → black line-art hair,
  posterised skin; photoreal default took the face anchor **0.8440 → 0.9840**);
  `_view_prompt` said "SAME **outfit**" — a category word Klein ignores — so the sides wore
  different clothes (garments are NAMED now); and the face crop could displace the full-body
  front ref (front PINNED, `refs_used` published). Plus `?tab=` finally being read, so grid
  jumps land on the right tab.
- **🔍 One lightbox** with zoom+pan everywhere; **⚙ Experimental Modes** hides Klein 1.0/2.0.
- **🔐 Repo publishes clean** — token out of source, history purged; the gate lifted at
  v1.277.3 and publishing is NORMAL CADENCE (564 public files). ⚠ Agent publishes MUST pass
  `-Yes` (§4).

**⚠ Standing rules this session added (all measured, all in docs/KLEIN3.md):**
NAME only what is IN VIEW (category words, franchise names, a chest emblem on a back view) ·
never feed a lane its own prior output or a contradicting view as a reference — check
`refs_used` · a close-up copies its reference's FRAMING · an in-place upscale needs a VERSIONED
url · `asyncio.create_task` raises off the event loop, and a status set before the work is
scheduled can LIE · a correct API does not mean a correct screen.

**Open, in order** (same list as README and `HANDOVER_PROMPT.md`'s START HERE — identical):
(1) **LTX 2.5 graphs** — models staging fleet-wide (`scripts/install_ltx25.py --check`);
engine slot/prompts/settings live, but ltx_2.5 projects render on the 2.3 pipeline until
API-format 2.5 graphs are exported from a worker and wired ·
(2) **MiniMax H3 project lane — first LIVE end-to-end scene render pending** (smoke +
adversarial review verified); also tools.py's sample generator still carries the broken
raw-workflow krea2 path (use z_image/anima/klein there) ·
(3) **the adopt-k3 cast watcher does not survive a restart** — re-adopt by hand via
`POST …/concept/characters/{i}/adopt-k3` after a reboot mid-cast ·
(4) LoRA panel **base-outfit picker** (route built + tested, no UI) ·
(5) **H3's Video-Lab modes beyond t2v/i2v** (first_last / last_frame / ref2v untested there) ·
(6) whether 🙂 `face_first` earns its keep at all · (7) `LoraPanel`'s `nOutfit` has
no setter wired, so a new dataset's "outfit" field always submits `''`.
✅ Recently closed: Story/World follow-through (pull-from-story BUILT v1.277.12; LLM lanes +
a real 7-character batch ran live) · publish (normal cadence since v1.277.3).

### ⚡ Autogen v2 (v1.276.42) — `backend/api/autogen.py`, prefix `/api/autogen`

`POST /run` (one) · `POST /batch` (many, **strictly serial**) · `POST /estimate` (cost before
you commit) · `POST /refs` (multipart, for a character that does not exist yet) ·
`GET /health` · `GET /jobs` · `GET /jobs/{id}` · `POST /jobs/{id}/cancel|retry|delete` ·
`GET /refs/{rid}/image` · `POST /queue/clear`.
Stages, in dependency order: `character · base · views · gate · clothing · dataset · charsheet
· lora`. **⏱ Measured on a real 8/8 run (v1.276.51):** `lora 6.58h (92.4%) · clothing 15.6min ·
dataset 14.7min · views 1.5min · gate 20s · charsheet 1s` — so **the same chain WITHOUT 🚀 LoRA
is ~32 MINUTES.** That is the toggle to use while iterating on a look. ⚠ **`gate` sits AFTER `views`** — it ran before them in the first draft and therefore
gated nothing (on a fresh character only `front` exists, so the check passed trivially and the
three views it guards were rendered afterwards).
State: `<libraries>/autogen/jobs/<id>.json` + `queue.json`; `completed[]` is per stage, which
is what makes resume resume rather than restart.

**Status fields (v1.276.46).** `GET /jobs` rows carry `stage · detail · completed[] ·
stage_times{} · stage_started_at · elapsed_s · queued · active · log_lines · estimate`.
`GET /jobs/{id}` adds the whole record plus `elapsed_human · stage_elapsed_s · log[] ·
log_total`, and takes **`?log=N`** (default 200, `-1` = all, `0` = none).
**The epoch story (v1.276.52):** `/jobs` rows carry `best_score · epochs_scored ·
installed_epoch · best_epoch · install_note`; **`run_id` reaches `/jobs/{id}` only** (it returns
the whole record). ⭐ **`install_note` is present ONLY when
the best-scoring epoch had no checkpoint and a nearer one was substituted — its absence is the
good news.** ⚠ These are written to the TRAIN state; `_merge_train_facts()` fills them in at
READ time, so runs that finished before .52 display correctly and there is no migration.
⚠ **Elapsed is computed against the WALL CLOCK for a running job**, not read from the file —
the file is only as fresh as the last write, and a stage can sit quiet for minutes. A frozen
timer on a live run is the exact "is this stuck?" question the timer exists to answer.
⚠ `log[]` is capped at 400 and written by **`_tick()` ONLY when the detail text changes** —
logging every poll would be spam over a four-hour render, and a change is an event.
`_stage_t0` is internal bookkeeping and is stripped from the response.
⚠ **"Atomic" needs a caveat on Windows (v1.276.43):** `os.replace` fails with `WinError 5` if
ANY process has the target open — including a status poller reading it — so
`lora_train._state_save` uses a per-pid+thread temp name and retries ~0.6s, and `_run_one`
guarantees a terminal stage even if the file cannot be written. Before that, a failed write
escaped and left a FINISHED job stranded at a non-terminal stage, looking hung forever.
⚠ **`_app()` appears only inside the pipeline THREAD** — never in a route (v1.276.41).
⚠ Clothing auto-approval is **by id**, only what the run made. ⚠ Candidate picking scores
**usability, not likeness**.
**📌 BATCH MODE IS DELIBERATELY THIN AND STAYS THAT WAY** (Lorenzo, 2026-08-12): it is the
single-character form repeated onto a serial queue — *"basically what we do with One mode
anyway"* — and that is enough, because a **bulk-submission mode** is coming that will own the
scaling story. **Do not grow batch mode; the queue underneath it is the primitive that mode will
build on.** ⚠ **The `lora` stage is the ONE place ⏹ stop cannot reach** —
`_train_pipeline` waits in a bare `while True: sleep(60)` with no cancel hook, so a cancel is
seen only when training ends. The check happens before training starts, which is the last
honest moment. Free smoke test: `python scripts\autogen_smoke.py` (30 checks, zero renders,
cleans up after itself) — **run it 4× after touching this lane; the worst bug it ever caught
was a race that passed on the first run.**
✅ **CLOSED v1.276.39: a FRESH character generating all four views in ONE go.** The `deferred`
second pass ran, `right` got the mirrored `left` at slot 3, and all four views passed the
verifier on attempt 1. See the CHANGELOG.


**📌 NEXT BIG MODE (Lorenzo, 2026-08-12):** a **bulk-submission mode** — "what does character
generations in larger submissions of items to render at a time". ⚡ Autogen's serial batch queue
is the primitive underneath it, which is exactly why batch mode is being left as-is rather than
grown: the scaling story belongs in the new mode, not bolted onto this one.

**Roadmap (Lorenzo's words):** verify the new stuff tracks → finish the character studio
area → tie characters into projects/other site sections (H3 video into project scenes) →
Story Builder mode reading the 📖 lore store. Parked lanes: Klein 1.0 pose control
(feet/hands/pelvis), Klein 2.0 3D statue mode (16GB likeness ceiling). Shelf: small-set
epoch measurement, bilingual captions, masked training, per-image LR, Fizgig Repair
Studio / LoRA Royale, LTX ultra extender/slicer/audio-replacer sections (unmined).
