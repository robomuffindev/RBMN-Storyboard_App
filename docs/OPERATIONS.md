# RBMN Operations Runbook (v1.277.51, 2026-08-19)

Everything that exists, where it runs, and how to drive it. This is the "which tool, which
box, which command" page — the narrative is in `HANDOVER_PROMPT.md`, the decisions in
`CHANGELOG.md`, the LoRA method in `docs/LORA_DATASET.md`, the 🌍 world/story/chapter/codex
lane in `docs/STORYWORLD.md`.

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

## 5a. 🆕 NEW WORKER BOX — the checklist (his standing ask, 2026-08-18)

Everything here is scripted and driven from the app host; none of it needs a keyboard on
the box beyond installing the helper itself.

1. **Helper** — copy `scripts/worker/rbmn_helper.py` to the box, run its bat, take the
   `TOKEN …` from the console into Settings → 🔧 Worker Helpers. `/health` proves the box
   is alive and NOTHING about your token.
2. **SageAttention** — matched wheel + triton + **CPython headers into python_embeded** +
   `--use-sage-attention` in the launch bat. Verify with `POST /verify/sageattention` (a
   REAL kernel call); measured 37% faster. See §5.
3. **Models** — `python scripts/install_audio.py` (ACE-Step + the F5-TTS node; add
   `--minimax3` for MM3) and the H3/LTX sets. ⭐ Peer-copy over the LAN beats re-downloading;
   `scripts/audit_model_integrity.py` catches truncated files (**a filename proves nothing**).
4. **🔊 The AUDIO DECODE CHAIN — new boxes do NOT have it, and F5-TTS is dead without it.**
   A fresh ComfyUI portable raises `Could not load libtorchcodec`. Two pieces; the first
   alone is not enough:
   - **`python scripts/install_ffmpeg_shared.py --apply`** — FFmpeg **7** SHARED DLLs
     (`avcodec/avformat/avutil/avfilter/swresample/swscale`) placed beside `python.exe`.
     `ffmpeg.exe` on PATH does NOT count: only a `full_build-shared` archive ships DLLs.
     The script downloads once here, wraps the DLLs in a wheel, ships it through the
     helper's `POST /datasets/<name>` (raw ZIP) and lets the box's own pip unpack it with
     `--target …\python_embeded`. ⚠ Do not hardcode a BtbN asset filename — the rolling
     `latest` tag rotates them off (404) — and GitHub 404s a bare `Python-urllib` UA.
   - **`python scripts/tts_doctor.py --fix`** — pins **torchcodec to the box's torch**
     (`--no-deps`, so pip can never drag the fleet's torch with it). Published pairs:
     0.11↔2.11 · **0.10↔2.10 (ours)** · 0.9↔2.9 · 0.7↔2.8 · 0.5↔2.7.
     ⚠⚠ A mismatch does not announce itself as a version problem. It is a Windows
     **message box** — *"procedure entry point `torch_dtype_float4_e2m1fn_x2` could not be
     located in … `libtorchcodec_core7.dll`"* — and that modal **BLOCKS ComfyUI's startup
     until somebody clicks OK on the machine**. A box that "won't come back after a
     restart" may simply be waiting behind it.
5. ⚠ **Kokoro is NOT a worker install** — it runs in the APP venv (python 3.11); the workers'
   3.13 has no `misaki`/`numpy` wheels. `venv\Scripts\python -m pip install
   --only-binary=:all: kokoro misaki[en]` on the app host, once.
6. **Restart ComfyUI, then PROVE it** — `python scripts/tts_doctor.py --probe` renders
   three words on the box and prints what the node actually said. An install is not a
   decode, and a version table is context, never proof.

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

### The FREE suites — run these before spending a render

*(§6a and §6b below are the box-dropout and fan-out sections — this table has no number
on purpose, so it cannot collide with them.)*

No GPU, no worker, no LLM, no money. Every one creates throwaway records and deletes them.

| suite | checks | covers |
|---|---|---|
| `scripts/storyworld_smoke.py` | 44 | 🌍 worlds, stories, texts, cast CRUD + merge semantics, the estimate, a real `details`-level submission through the queue, project attach |
| **`scripts/story_chapters_smoke.py`** | **89** free<br>**+7** `--live` | 📖 chapters (route order, many-per-arc, reorder-never-deletes, beats are arc-shaped) · ✍ **paragraphs** (`_prose` vs the `_flat` comma-weld, `_paragraphize`'s three repairs) and the **beat call plan is a PARTITION** · 📚 codex (canon hashes, the ✍/📌 preservation invariants, the cap, the "Unknown" guard) · the chapter-scoped project link and `?brief=1` picker shape.<br>⭐ **`--live` writes ONE real narration and MEASURES what reached disk** — a green unit test on the helpers is not evidence about the artifact. v1.277.47: 717 words vs a 600 target, 13 paragraphs, 27 s. |
| `scripts/story_audio_smoke.py` | 9 | 🎙 the story's three narration file slots |
| **`scripts/chatterbox_probe.py`** | **9** live | 🗣 **CHATTERBOX** — a real render plus the `crash_protection_template` question MEASURED (renders the same two words under each template and compares durations). ⚠ First run per box is ~12 min: it downloads the model. |
| **`scripts/cue_precision_verify.py`** | **20** live | ⭐⭐ **THE BOUNDARY GUARANTEE, MEASURED FROM THE AUDIO** — downloads what the app serves, decodes it, and checks every cue gap for speech-level energy (a mid-word cut), whether the error GROWS down the file (the v1.8.20 signature), gap lengths, SRT agreement and the AAF round-trip. Never compares a cue to another number we computed. v1.277.49, 6 min / 70 cues: **0.00 ms** end drift, **max gap energy 0.000**, no growth. |
| **`scripts/chapter_voice_probe.py`** | **34** live | ⭐ **THE WHOLE VOICE CHAIN, MEASURED**: chapter → 🎙 spoken take → cues → 📝 SRT → 🎬 project with scenes. Runs on Kokoro (app host, no GPU, no worker, free) and deletes everything it makes — including the project. v1.277.48: 6 cues, **0.01 s drift** against a 26.48 s file, 4 scenes covering it. |
| `scripts/audio_score_smoke.py` | 20 | 🎼 score-a-story cue planning (`--render` adds a live fan-out) |
| `scripts/prompt_shape_smoke.py` | 21 | 🎛 per-engine caption shaping (loads the module BY PATH — runs outside the venv) |
| `scripts/autogen_smoke.py` | 30 | ⚡ the autogen queue end to end (one SKIPs with no view job in memory) |
| `scripts/pause_tag_smoke.py` | 38 | 🫁 narration pause tagging |
| `scripts/voice_library_smoke.py` | 24 | 🎤 the voice library |
| `scripts/scene_ref_mode_smoke.py` | 14 (+7 live) | 🎛 scene reference mode |
| `scripts/test_weights_pick.py` | — | the LoRA checkpoint picker (see §6) |

⭐ **Count the assertions in the SOURCE, not the PASS lines in the output.** A grep for `PASS`
matches the summary's own `ALL PASS` footer and inflates by exactly one, silently, every time
(v1.276.47 — reported wrong twice before anyone checked).
⚠ Operator scripts are **STDLIB ONLY**: importing `backend.api.*` for one helper pulls in
FastAPI, so `python scripts\…` dies outside the venv — which is exactly when you need it.

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
  📖 **STORY CHAPTERS** (v1.277.46, `backend/api/storychapters.py` — same prefix, own module,
  registered right after storyworld): `GET stories/{sid}/chapters` (**`?brief=1`** = the
  shared PICKER shape, titles + counts, never the prose — the full list carries every
  chapter's narration) · `POST stories/{sid}/chapters` (create) · **`chapters/generate`**
  (✨ Outline from the arcs — titles + summaries ONLY; 409 on a story with no arcs) ·
  `chapters/reorder` (a forgotten id keeps its place; a reorder never DELETES) ·
  **`chapters/{cid}/narration`** (✍ STARTS A JOB — v1.277.47) + `…/narration/job` (live) +
  `…/narration/cancel`. ⭐ **One model call PER BEAT**, each with its share of the word budget
  and the previous beat's TAIL for continuity: one call asked for 1500 words returns 400, six
  asked for 250 return 1500. Default **10 min ≈ 1500 words**. ⚠⚠ `_beat_groups` is a
  PARTITION — the first version zeroed the tail beats and a 24-beat chapter silently never
  told beats 13-24. ⚠⚠ **NEVER `sw._flat()` ON PROSE** — it joins a list with `", "`, which
  comma-welded a model's paragraph array into one block; use `_prose()` + `_paragraphize()`
  (paragraphs are where the pause-tagger puts `[pause]`). ·
  `chapters/{cid}/beats` (split an existing narration) · `chapters/{cid}/delete` ·
  🎙 **`chapters/{cid}/tts/options|tts|tts/keep`** (v1.277.48 — voices/engines/readiness ·
  render a take · keep it). All of `audio_lab`'s machinery, called IN-PROCESS; the chapter
  supplies only which text and where the take lands. ⭐ **Audition first, keep second** —
  nothing touches the chapter until Keep. ⭐⭐ **Keep writes the audio AND the srt** from the
  render's own cues, and stores `cues` on the chapter. ⚠ >1.0 pace = SLOWER on both engines.
  🎬 **`chapters/{cid}/project-readiness|create-project`** (v1.277.48) — readiness is a
  separate GET so the button can be disabled WITH A REASON; the POST re-checks anyway.
  ⭐⭐ **The gate is narration text + audio + SRT, ALL THREE** (his call); missing beats/cast
  are warnings. Creates → links to the chapter → pulls everything → **builds scenes from the
  cues** → re-times the chapters onto them. Sets `settings["scene_source"]="chapter_cues"`
  (⚠ NOT `audio_source="aaf"` — no AAF exists and that would arm the resync gates). ·
  `chapters/{cid}/file/{slot}` POST/GET/`/delete` (audio·aaf·srt, in
  `_libraries/storyworld/chapter_audio/{wid}/`) · `POST chapters/{cid}` (update — **declared
  LAST**, same shape as `/generate`). Free: `scripts/story_chapters_smoke.py` (**96 checks**, `--live` writes one real narration).
  📚 **THE CODEX** (v1.277.46, `backend/api/storycodex.py`): `GET /codex/meta` (entry kinds,
  server-driven) · `GET worlds/{wid}/codex` (+`?story_id=` — also returns **`stale`**, which
  is answered from the canon HASHES with **no LLM call**, so the 🔴 badge is free) ·
  **`worlds/{wid}/codex/recalc`** {force, story_id, do_world, do_characters, llm} → starts a
  THREAD · `codex/job` (live status: stage · WHERE · elapsed · stage_times · log) ·
  `codex/cancel` · `codex/entry` (✍ upsert by hand = `manual`) · `codex/entry/{eid}/pin` ·
  `codex/entry/{eid}/delete` · `codex/character/{cid}` · `codex/character/{cid}/event`
  (+`/{eid}/delete`) · `codex/character/{cid}/pin-state`.
  ⭐ **Ollama is the DEFAULT brain here** (his call — the most token-hungry lane in the app).
  ⚠⚠ **SCOPE MUST BE STATED, NEVER INFERRED FROM AN EMPTY LIST** — reading "no stories in
  scope" as "everything is in scope" deleted whole generated codexes silently while the ✅
  badge said "up to date". ⚠ ✍ `manual` and 📌 `pinned` survive every recalc through the ONE
  `_keep()` predicate, and the 400-entry cap truncates the GENERATED slice only.
  **Full reference for both: `docs/STORYWORLD.md`.**
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
  an empty body READS without writing) · **`PUT/GET story-link`** (two-way world/story link;
  since v1.277.46 it takes a THIRD level, **`chapter_id`** — `settings["chapter_id"]`, which
  requires `story_id` and is CLEARED when the story changes. `GET` returns `chapter_id` /
  `chapter_title` / **`chapter_missing`** / `chapters[]` in the shared picker shape
  (`storychapters.chapter_row`, titles and counts only — never the prose)) ·
  `POST pull-from-story` (concept/style/cast-with-images/texts→lyrics — CHAPTER-SCOPED when
  the link names one; there is deliberately no per-part override) ·
  `POST concept/characters/autogenerate` now submits ⚡ AutogenSpecs (real klein3
  characters) with `POST concept/characters/{i}/adopt-k3` as the write-back.
- **🎬 the STORY SPINE (v1.277.24-.46)** — the routes a linked project runs on.
  ⚠ These are the **STORY** rung. The **CHAPTER** rung (one arc told at length = one video
  project, with its own narration, its own recording and its own beats) is in the
  `/api/storyworld/*` block above and in full in **`docs/STORYWORLD.md`**:
  · `POST /api/storyworld/worlds/{wid}/stories/{sid}/structure` — prose → **arcs**
    (title·summary·mood·characters·locations), max 24; returns **200 + a note** (not 409) if
    the story is already structured and `overwrite` is off. ⚠ Big Bang calls it BEFORE the
    cast exists, so a Big-Bang story's arcs have EMPTY `characters` — re-Structure (or run
    `POST …/cast/map-stories`, which needs ≥2 stories) once the cast is there.
  · `POST|GET|DELETE /api/storyworld/worlds/{wid}/stories/{sid}/narration/audio` — 🎙 the
    RECORDING, one per story (a second upload replaces it and deletes the old file), stored
    in `_libraries/storyworld/narration_audio/{wid}/`. Duration is MEASURED with ffprobe.
    ⚠ `.aaf` is accepted but is a TIMELINE: no preview, and in a project it takes **Import
    AAF**, not Analyze. `pull-from-story {narration_audio:true}` (default ON) copies it in as
    a MUSIC asset and does NOT auto-analyze. Free suite: `scripts/story_audio_smoke.py`.
  · `GET|POST /api/storyworld/worlds/{wid}/stories/{sid}/narration` — ✍ the words a TTS
    reads, stored as a world TEXT of kind `narration` linked to the story. Written **arc by
    arc with `## Arc` headers** so narration, chapters and backing beds share boundaries.
    ⭐ length is a WORD BUDGET (`minutes × 150`), not a duration request.
  · ⚠ Only FIVE call sites read through `effective()` (both flow builders, the image and
    video enhance contexts, and the pull). The dispatcher's two-pass prompt builders,
    `timeline.suggest_timeline` and `concept.autogenerate_characters` still read the raw
    project keys — story-linked projects do NOT yet steer those.
  · `GET /api/projects/{id}/story-context` — what the project's direction RESOLVES to
    (`linked`, `derived`, `effective`, `own`, `overrides`, `arcs`, story-scoped `cast`,
    and since v1.277.46 **`chapter`**, **`arcs_are_beats`**, **`chapter_missing`**).
    ⚠⚠ **`arcs` HOLDS THE CHAPTER'S BEATS when a chapter is linked** — everything downstream
    reads `arcs` and needed no change, which is exactly why `arcs_are_beats` has to travel
    beside it: a reader that does not know which rung it is holding will label the wrong one.
    `effective()` also prefers the CHAPTER title for `song_title` (the story title would put
    one name on every video in a series). `concept_text` now carries the 📚 codex brief +
    the character histories.
    The Concept tab reads this. `PUT …/story-override` pins ONE field
    (`settings["story_overrides"]` — never written into the concept keys, or nobody could
    tell what unlinking restores).
  · `POST /api/projects/{id}/pull-from-story` — now defaults `characters` + `chapters` ON,
    `concept`/`style` OFF (they are DERIVED, not copied). Chapters come from
    `services/chapters/from_story.py`, timed against the **detected sections**.
    ⚠⚠ **WHAT it builds them FROM depends on the link (v1.277.46):** with
    `settings["chapter_id"]` set they come from **that chapter's BEATS**; only a story-wide
    project uses the story's ARCS. Timing twelve story arcs against one chapter's four
    minutes of audio gives twelve chapters that all start at zero — which is why the pull
    REFUSES a beat-less chapter instead of widening. The script, the audio/aaf/srt and the
    cast pull are chapter-scoped the same way. Full table: `docs/STORYWORLD.md`.
  · ⚠⚠ **`source` IS MUTABLE — PROVENANCE IS NOT (v1.277.46).** `backend/api/chapters.py`
    sets `source = "manual"` on rename (:214), split (:306), merge (:353) and
    generate-description (:597). So *"is this a story chapter?"* must NOT be asked as
    `source == "story"`: **`from_story.is_from_story(ch)`** reads
    `chapter_metadata["from_story"]`, written once by `create_chapters_from_arcs` and never
    changed. Three places ask it, and each was a real bug before it existed — the pull's
    DELETE (skipping edited chapters → **doubled chapters**, the 1.8.15 signature from a new
    direction), `_rebuild_chapters_locked`'s producer short-circuit (renaming one chapter
    re-enabled the auto producer), and `retime_story_chapters` (an edited chapter kept the
    times it was born with). ⚠ PRESERVATION is a different question from PROVENANCE — see
    the next bullet.
  · ⚠⚠ `source="story"` is PRESERVED by `_rebuild_chapters_locked` — **FIVE** predicates:
    delete · **FK pre-null** (missed until v1.277.29) · survivor check · raw-connection
    delete · its re-check. ⚠ **EXCEPT on `force_auto`**, which deletes every chapter
    regardless (that is what "Reset chapters" means); API-only, the UI never sends it.
    ⚠ The producers are skipped when the project **HAS STORY CHAPTERS**, not merely when it
    is linked — link without pulling and the auto producer still runs. Existing arcs are
    re-timed instead (`retime_story_chapters`). Rebuild fires from `suggest-timeline` and
    `import-aaf` (NOT slice-audio) and from `/chapters/reparse`.
  · `POST /api/audio-lab/score/project` — one INSTRUMENTAL backing bed per arc on
    **`ace15` (turbo) by default**; the length is the CHAPTER's real duration (clamped at
    300 s **and flagged**; 60 s + a note when the chapter has no time yet). ⚠ the 5 s FLOOR
    is applied silently by `_clean_cue`. Cue lengths deliberately NOT normalised to a total.
  · `POST /api/storyworld/worlds/{wid}/locations/{lid}/reference` — 📷 upload YOUR photo:
    vision documents the PLACE (never the photograph, never the people in it), FILL-only
    into the six fields. ⚠ **The upload renders nothing** — press 🖼 afterwards; from then on
    the plates cite that image with `ref_mode="subject"` (*"Image 1 is the PLACE itself"*),
    which is a different prompt from the world STYLE ref (*"in the artistic style of image
    1"*) — feeding one into the other's branch was the v1.277.29 bug. Plates default to **4**;
    the bulk lane is `POST /locations/shots/all` with live status on
    **`GET /locations/shots/job`**.
- **🎛 prompt shaping (v1.277.19)** — `backend/api/prompt_shape.py`, a PURE module called
  by `enqueue_music`. ⚠⚠ **ACE and MM3 want OPPOSITE prompt shapes.** ACE: metadata OUT of
  the caption (its tokenizer injects a `# Metas` block), Title-Case lyric tags with ≤1
  modifier, `[Instrumental]` never an empty box, and a line budget `bars = s×bpm/240`.
  MM3: metadata IN the caption in the `Global Metadata`/`Vocal Details`/`Arrangement`
  layout, the vocal ALWAYS named (or it drifts instrumental), tags lowercased and alone on
  their line, stage directions in (parentheses), no four-space runs, and a **5000-token
  hard error** (it raises, it does not truncate). Every rewrite is published on the job as
  `prompt_notes`. Free suite: `scripts/prompt_shape_smoke.py` (21 checks, no GPU/LLM/app).
- **`/api/audio-lab/score/*` (v1.277.16)** — 🎼 the ARC PAIRING lane:
  `GET sources` (worlds+stories+texts+projects+saved scores in ONE call) ·
  `POST plan` (LLM → cues on PAPER, renders nothing) · `POST manual` (same object, no
  LLM — what the free smoke drives) · `GET list` · `POST {sid}/cues` (edit; POSITIONAL
  write-back keeps in-flight job ids) · `POST {sid}/render` (round-robin across ready
  boxes, claim-under-lock before enqueue, revert on failure) · `POST {sid}/cancel` ·
  `POST {sid}/import` (every finished cue → MUSIC assets, in cue order) ·
  `POST {sid}/delete` · `GET {sid}` **declared LAST** (it would swallow `sources`/`list`).
  Cue lengths are clamped 5-300s and normalised to SUM to the target (`adjusted_by` on
  the cue that moved). Renders go through `audio_lab.enqueue_music` **in-process**.
  Free suite: `scripts/audio_score_smoke.py` (`--render` adds two 5s cues).
- **`/api/audio-lab/*` (v1.277.19)** — 🎧: `GET overview` (per-worker engine detection from
  object_info + model listings; names the MISSING FILE, not just "not ready") ·
  **`GET staging`** (live model-download progress per box — an engine chip only flips when a
  file has FULLY landed; needs the box token, which is why `/health` cannot answer it) ·
  `POST music/generate` (**four engines**: `ace15` | `ace15_sft` | `ace15_base` |
  `minimax3`; exact `seconds`; `steps`/`cfg` overrides, 0 = the engine's default;
  `normalize` default ON) · **`POST music/compare`** (one prompt → every picked engine,
  round-robin across boxes — with more engines than boxes two share one — same seed, all
  loudness-matched) · `GET tts/voices` / `POST tts/voices` / `POST tts/voices/{vid}/delete`
  (clone = ONE clean reference clip of **≤12 s** + its EXACT transcript) · `POST
  tts/generate` (blank-line chunking, pause_ms between paragraphs; ⚠ NOT loudness-normalised
  — that applies to music only) · `GET jobs`(+`/{id}`) · **`DELETE jobs/{id}`** (also
  deletes the audio file from disk) · `GET media/{id}` · `POST jobs/{id}/send-to-project`
  (MUSIC asset). ⚠ Normalisation needs **ffmpeg on the app host**; without it the job's
  `loudness` field says so and the render is left as-is. Staging:
  `scripts/install_audio.py` (+`--minimax3`), `scripts/install_ace_quality.py` (the XL
  models); F5 node needs a ComfyUI restart after install. Helper **v1.221** deployed 3/3:
  `/serve/model/{folder}/{file}` (peer copy — `scripts/copy_models_to_peers.py`),
  `/downloads` (token!), `/diag` (disk), `/cleanup/parts`, `/ollama/status`.
- **`/api/storyworld` locations (v1.277.14, 🖼 plates .21)** — `locations` CRUD +
  `/generate` (scout) + `/{lid}/enhance` + **`/{lid}/shots`** (render N plates; `GET` for
  plates+live job, `/{sid}/active` ⭐, `/{sid}/delete`, `/locations/shots/{sid}/image`).
  Plate prompts come from the SHEET (no LLM call) and are **AFFIRMATIVE about emptiness**
  ("a deserted, unoccupied place… a clean environment plate") — ⚠ the first version said
  "no people" and every plate came back full of them, because these lanes run at cfg 1 with
  no negative. Also: **`/locations/shots/all`** (bulk, `only_missing`), **`/{lid}/sheet`**
  (recompose the 🪪 composite — PIL, free), **`GET /api/storyworld/location-images`** (feeds
  the reference pickers, sheet first);
  rendering goes through the shared `_render_prompt_set()` (same three paths as 🎨 style
  samples). ⚠ the `/shots` routes are declared BEFORE the bare `POST /locations/{lid}`; sheets feed world/story/cast LLM context; ⚠ literal `/generate`
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
- 🌍 **Worlds**: `_libraries/storyworld/worlds/<wid>.json` — ONE file per world, holding the
  sheet, stories (with their **arcs**, **chapters** and each chapter's narration), cast,
  locations, texts, 🎨 style and the 📚 **codex** (entries · character pages · canon hashes ·
  the run history). Read-modify-write under a module lock; `_save` writes a unique temp then
  `os.replace` with a retry, because a Windows READER holding the target makes replace fail.
- 🎙 **Story recordings**: `_libraries/storyworld/narration_audio/{wid}/<fileid><ext>`
  📖 **Chapter recordings**: `_libraries/storyworld/chapter_audio/{wid}/<fileid><ext>`
  ⚠ Two different directories for the same three slots — pick the path resolver alongside the
  file list, not after it. `delete_story` and `delete_chapter` clean BOTH (metadata collected
  inside the lock, unlinked after the write lands).
- 🎨 Style refs / samples: `_libraries/storyworld/style_refs/`, `…/samples/`
  📍 Location plates: `_libraries/storyworld/location_shots/`
- Forge/trainer settings: `_libraries/forge/settings.json`
- Trained LoRAs: training box `…\ComfyUI\models\loras\` (dorian-v1-…-000016, redv1-v2-e21 ←
  use, redv1-…-000036 old)
- Diagnostics Claude can read: `scripts/_diag/` (gitignored)

## 10. Current state & roadmap (2026-08-19, v1.277.51)

**Done and measured this session:**
- **🗣 CHATTERBOX IS THE DEFAULT CLONE ENGINE** (v1.277.51) — his call, and the LICENCE is the
  reason: **F5-TTS is CC-BY-NC 4.0** and he intends to give the app to the public, so shipping
  F5 as the default would restrict everyone who uses it. **Chatterbox is MIT.** ⚠ F5 is NOT
  removed — it is selectable and labelled `⚠ non-commercial` IN THE PICKER. Zero-shot: no
  reference transcript, no 12 s cap, and an `exaggeration` dial for character.
  📦 `scripts/install_chatterbox.py` — **one box first**, `--check` never writes; installs
  `diodiogod/TTS-Audio-Suite` (also brings IndexTTS-2, Higgs 2/3, **VibeVoice**, RVC).
  ⚠⚠⚠ **The first render was a GREEN JOB AND ONE SECOND OF SILENCE** — `execution_success` in
  191 ms while the node had raised `ChatterboxTTS not available`. The pack keeps its hard deps
  OUT of `requirements.txt` (its own `install.py` installs them; our helper never runs it):
  `s3tokenizer`, `resemble-perth`, `descript-audio-codec`, now installed `--no-deps`.
  ⚠⚠ **A running ComfyUI LOCKS its DLLs on Windows** — the trainer's install died on
  `numpy.libs\msvcp140-*.dll`; the installer now stops → installs → starts → **verifies**.
  ⭐⭐ Measured and it corrected me: `"{seg}"` 1.28 s · node default 1.72 s · **my comma guess
  2.28 s** for identical words. 🔊 Narration now normalised to **-16 LUFS**, and a
  transcript-vs-clip plausibility check warns about the mismatch that makes F5 mumble.
  ✅ 3/3 boxes; 4.08 s render, cues exact, SRT writes. ⏱ ~12 min model download per box.
- **🔒 THE BOUNDARIES ARE LOCKED, AND NOW PROVEN** (v1.277.49) — ⭐⭐ **the AAF's advantage was
  never mostly the arithmetic; it was THREE GATES.** v1.277.48 reproduced the maths and none of
  the authority, so pressing Analyze on a cue-built project would have re-snapped every scene to
  Whisper timing — straight back into the bug he escaped.
  **`timeline.authoritative_timeline()` is the ONE predicate** and all three gates call it;
  it covers `audio_source=="aaf"` AND `scene_source=="chapter_cues"`. `/detach-aaf` releases
  either; the Audio tab shows a 🔒 banner so a refusal reads as the guarantee working.
  ⚠⚠⚠ I had also **re-introduced the v1.8.20 random walk** — `_concat_with_pauses` accumulated
  `round(seconds, 2)`. Now integer sample counts (`_pcm_frames`), divided once, like
  `import_aaf`'s edit units. 🎬 And the AAF WRITER he asked for (`services/export_aaf.py`,
  edit_rate = sample rate, verified by reading it back through our own importer; `pyaaf2` was
  missing from `requirements.txt` entirely).
  ✅ Measured on 6 min / 70 cues: **0.00 ms** end drift · every start a whole sample ·
  **max gap energy 0.000** (0 of 69 gaps contain speech) · **no drift growth** · SRT 0.00 ms ·
  AAF round-trip 0.0 s, 70/70 names.
- **🎙📝🎬 SPEAK A CHAPTER · SRT FOR FREE · CHAPTER → PROJECT** (v1.277.48) —
  ⭐⭐ **the SRT was already paid for**: the TTS renders sentence by sentence and the join
  already probed every part's duration; accumulating instead of summing gives exact `cues`
  (start·end·spoken text) and `GET /api/audio-lab/jobs/{jid}/srt`. ⚠ the single-chunk fast
  path bypasses the loop, and `_stretch` runs AFTER the concat so cues are scaled at capture.
  🚫 **No AAF writer, deliberately** — an AAF carries per-sentence cut points and NOT the
  words; our cues are exact and named, so `scenes_from_cues` replaces it (reusing
  `clips_to_scenes`, the AAF path's own math). 🎙 chapter TTS with voice/engine/pace/pauses,
  audition-then-keep. 🎬 chapter → project, gated on text+audio+SRT.
  ⚠⚠⚠ Three pre-existing bugs the live probe surfaced: an emoji in `Content-Disposition`
  (a header is **latin-1**) 500'd the SRT route · **`shortcode_counters` has a project FK and
  no ORM relationship**, so any project that ever had chapters **could not be deleted at all**
  (same for `timeline_positions`/`stem_selections` with sliced audio) · and my first fix put
  the whole pre-clean in ONE try, so one bad column hid every later step —
  ⭐ **a best-effort cleanup must be best-effort PER STEP.**
  ✅ `scripts/chapter_voice_probe.py`: **34 checks ALL PASS** — 6 cues, **0.01 s drift** on a
  26.48 s file, gaps exactly as asked, 4 scenes covering it, named with the spoken words.
- **✍ THE CHAPTER NARRATION IS A FULL TELLING, IN PARAGRAPHS** (v1.277.47) — his report, two
  causes. ⚠⚠ The "single block" was **`sw._flat()` joining a list with `", "`** — right for
  fields, destructive on prose; `_prose()` + `_paragraphize()` replace it and ENFORCE
  paragraphs (they are where the pause-tagger puts `[pause]`). ⭐⭐ The length needed a
  different SHAPE, not a bigger number: **one call per beat**, each with its share of the
  budget and the previous beat's tail, run as a JOB with live status. Default **10 min ≈ 1500
  words**. ⚠⚠ My own first budget split **zeroed the tail beats** — a 24-beat chapter narrated
  1-12 and never told 13-24, green job, plausible word count; `_beat_groups` is a partition now.
  ✅ **717 words vs a 600 target, 13 paragraphs, 27 s** — measured on the artifact via
  `story_chapters_smoke.py --live` (**96 checks** total).
- **📖📚 STORY CHAPTERS + THE CODEX** (v1.277.46) — the ladder is three deep:
  STORY(prose+arcs) → **CHAPTER**(one arc told at length, its own full narration + its own
  audio/aaf/srt — ⭐ **one chapter IS one video project**) → **BEAT**(→ the project's timeline
  chapters). `settings["chapter_id"]` narrows the script, the audio, the timeline, the cast
  and the project name. 📚 The codex is the canon-only world sheet + a page per character
  (with a **state line** — where they stand now, what a sequel starts from), hash-incremental,
  Ollama by default, ✍/📌 entries kept through every recalc. Full reference:
  **`docs/STORYWORLD.md`**. Free: `scripts/story_chapters_smoke.py` (**96 checks**, ALL PASS
  live on v1.277.47).
  ⚠⚠ The review caught three that would have cost real work: `_merge_entries` inferring scope
  from an EMPTY LIST (a character-only recalc deleted whole codexes, silently, showing ✅ "up
  to date") · an entry named **"Unknown"** appending `None` and 500-ing every later read
  (`_flat` blanks that word) · the pull deleting on the MUTABLE `source` instead of
  provenance, which would have **doubled his chapters**.
- **🎙 THE NARRATION LANE, END TO END** (v1.277.38-.45) — record a voice (source kept + ≤12 s
  clip, every re-trim from the SOURCE, 0.05 s nudges) or **🎨 invent one** (Kokoro: 28 presets,
  ▶ auditionable + blendable, transcript exact by construction). Two engines: `f5tts` (clones,
  on a worker) and `kokoro` (instant, app host, factory voices only — it REFUSES a recorded
  voice with a reason). 🫁 `[pause]` tags **written and re-valued for you** (`[pause!]` pins
  one); 🐢 pace via **rubberband stretch** instead of asking the model to slow down.
  ⚠⚠ Kokoro is an APP-HOST install (py3.11); the workers' py3.13 has no `misaki`/`numpy`
  wheels. ⚠⚠ F5's `speed` is INVERTED (>1 = slower, measured); Kokoro's is not.
  Free: `pause_tag_smoke` 38 · `voice_library_smoke` 24 · **`pause_render_verify.py`** (the one
  that measures the ARTIFACT, not the plan).
- **⚠⚠⚠ THE PAUSE BUG WORTH REMEMBERING** (fixed .42, found by his ear against 28 green tests):
  downloads were named `<jid><ext>` — the JOB's id — so **every chunk overwrote the last** and
  a multi-part narration was the final sentence repeated; and ComfyUI SaveAudio returns FLAC,
  so the concat **demuxer silently dropped our PCM silence while exiting 0**. Everything is
  normalised before joining and the join verifies its own arithmetic.
  ⭐⭐ **A green unit test on the planner is not evidence about the artifact.**
- **🔧 THE `klein_t2i` 400 WAS NOT KLEIN** (v1.277.37) — node 63 `RBG_Smart_Seed_Variance`
  inside the four `workflows/KREA2_*_T2I.json` graphs gained two REQUIRED inputs on the
  workers (`protect_mode`, `protect_regions`), so every stored graph using it failed
  validation before a step ran. Patched with the node's own defaults; the same job then
  finished on `.163`. ⚠⚠ **A custom node gaining a required input silently breaks every
  stored graph that uses it.** The 400 BODY was already on disk — `scripts/log_grep.py`
  (⚠ the log is `<repo>/logs/rbmn.log`, NOT `backend/logs`) and `scripts/probe_klein_t2i.py`.
- **🎛 SCENE REF MODE** (v1.277.37) — `backend/services/scene_ref_mode.py`: `t2i_swap` (Pass 1
  empty stage → Pass 2 composite → i2v) vs `full_reference` (sheets AS references, one pass;
  H3 → `h3_ref2v`) vs per-scene `inherit`. Project default on the Concept tab + Engine modal,
  override in the Scene editor. Read by the image lane (7 auto-gen call sites) and the H3
  branch. ⚠ `two_pass_enabled` is route 1's legacy spelling and is still honoured; both
  writers keep the pair in sync. Free: `scripts/scene_ref_mode_smoke.py` — **21/21 live**.
- **🎤 THE VOICE LIBRARY** (v1.277.37) — source kept whole, a ≤12 s CLIP cut from it
  (auto-start at the first non-silent moment), **every re-trim from the SOURCE** (proven:
  3 s → back to 10 s), dropdown + 🪪 details (made-on · source · every render · the projects
  and STORIES each landed in) + `send-to-story`. `tts/generate` refuses an over-cap voice.
  Free: `scripts/voice_library_smoke.py` — **20/20 live**, builds its own 40 s sample.
- **✅✅ F5-TTS SPEAKS — 3/3 boxes** (measured 2026-08-18: `--probe` → SPOKE on .201/.163/.224,
  torch 2.10.0+cu130 · torchcodec 0.10.0). It needed BOTH halves of the decode chain, and
  fixing the first only exposed the second: FFmpeg 7 SHARED DLLs beside `python.exe`
  (`scripts/install_ffmpeg_shared.py --apply`, ~50 s for the fleet, nobody touched a box)
  **and** torchcodec pinned to the box's torch (`tts_doctor.py --fix`, `--no-deps`).
  ⚠⚠ The mismatch presents as a Windows **message box** that BLOCKS ComfyUI startup until
  someone clicks OK. ⚠ Fleet tools are stdlib-only now (`scripts/_fleet.py`) — importing the
  app made them venv-only, which is exactly wrong for a repair tool. **New boxes need this
  too: §5a.**
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
(1) **⚡ THE AUTO-GEN 'DO EVERYTHING FROM THE STORY' CHAIN — the one thing still owed** — A sequencer over the lanes that now exist: pull → cast → chapters → per-chapter narration → beats → flow → backing beds → images → videos, with per-stage toggles like ⚡ Autogen v2. ⭐ Its unit is a CHAPTER now, not a whole story (v1.277.46): one chapter is one video, so the chain runs per chapter and each result stays small enough to re-roll. Deliberately last: sequencing lanes while they are still moving is how you ship a button that lies about what it did. · (2) **Model integrity after ANY staging** — ⭐ FILENAME PRESENCE LIES — run `scripts/audit_model_integrity.py` (byte-size vs HF; `--fix` repairs) after any staging; `shape … invalid for input of size …` at a Loader node = TRUNCATION, not wrong-loader. ✅ 2026-08-16: 13/16 clean everywhere, every AUDIO and ACE-quality model byte-correct 3/3, helper v1.221 deployed 3/3. Live progress: `scripts/dl_progress.py` or the Music tab's ⬇ strip. · (3) **LTX 2.5 = IN PLACE, NOT A FOCUS (his call, 2026-08-16 — twice)** — Graphs wired (`ltx25_graphs.py` + dispatcher `ltx25_i2v`/`ltx25_t2v`), validated on a worker, HIDDEN from the frontend pickers (grep `ltx_2.5 hidden` to restore). Its transformer + gemma4 are the only truncated copies the audit still flags — leave them. · (4) **📖📚 THE STORY SPINE + CHAPTERS + THE CODEX ARE SHIPPED — what is open is HIS FIRST REAL RUN THROUGH THEM** — Stories carry **arcs** (the spine) and now **CHAPTERS**: a chapter tells ONE arc at length, owns its own full narration and recording, and **IS one video project** (`settings["chapter_id"]`, picked in 🎬 Engine & Story); its **beats** become that project's timeline chapters. A linked project still DERIVES concept/style live (`services/story_context.py`) with per-field pins, and a chapter narrows all of it. 📚 The **codex** (`backend/api/storycodex.py`) is the canon-only world cheat sheet + per-character history, hash-incremental, Ollama by default, and ✍ hand-written / 📌 pinned entries survive every recalc. ✍ narration is written BEAT BY BEAT (one call per beat, 10 min default, a job with live status) · 🎙 a chapter can be SPOKEN with any voice, auditioned, and the take kept — which writes its **audio AND its SRT** from the render's own measured cues · 🎬 **a chapter becomes a project** in one press, gated on text+audio+SRT, with the scenes built from those cues — and 🔒 **that timeline is AUTHORITATIVE** (`timeline.authoritative_timeline`), so Whisper resync / Suggest Timeline / scenes-from-sections all refuse, exactly as they do for an AAF. That rule, not the arithmetic, is what ended the drift (v1.277.49). ⚠ NOT yet seen live: ✨ Outline against a real model and one ♻ codex recalc end to end. Free: `scripts/story_chapters_smoke.py` (**129 checks**) + `scripts/chapter_voice_probe.py` (**34**) + `scripts/cue_precision_verify.py` (**20**, decoded from the audio: 0.00 ms end drift, max gap energy 0.000, no drift growth over 70 cues). · (5) **🎵 THE MUSIC LANE IS SHIPPED AND SETTLED — real-use testing is what is left** — Four engines pickable + 🆚 Compare (same prompt, same seed, round-robin across boxes, all loudness-matched) · 🎼 Score a story AND **`POST /score/project`** (one INSTRUMENTAL backing bed per arc, length = the chapter's real duration) · every MUSIC track normalised to -14 LUFS / -1 dBTP **when ffmpeg is on the app host** (narration is not normalised) · per-engine prompt shaping. Recipe: turbo is the DEFAULT sketch engine, `ace15_sft` 50 steps @ **cfg 3** is the keeper (seed-confirmed 4/4). ⚠ Do NOT restore ComfyUI's cfg 7/6 and do NOT promote sft to default. ⚠ `music_bench.py` renders worker-direct and BYPASSES both shaping and normalisation. · (6) **MiniMax H3 project lane — first LIVE end-to-end render pending** — Smoke + adversarial review verified; a real scene through h3_i2v/h3_ref2v with sheets/audio has not run yet. Also `tools.py`'s sample generator still carries the raw-workflow krea2 path that 400s (use z_image/anima/klein there). · (7) **F5-TTS first voice clone** — Machinery ready 3/3; needs Lorenzo's clean sample + its EXACT transcript — and the ✍ story narration lane now produces the words to read. ⚠ the real reference cap is **12 s** (hard cut mid-word), and the narration chunk size derives from the transcript's byte length. · (8) **The adopt-k3 cast watcher does not survive a restart** — A reboot mid-cast leaves project characters image-less; `POST …/concept/characters/{i}/adopt-k3 {slug}` re-adopts by hand. · (9) **LoRA panel base-outfit picker** — Route built + tested, no UI. · (10) **H3's Video-Lab modes beyond t2v/i2v** — first_last / last_frame / ref2v untested there. · (11) **Does 🙂 `face_first` earn its keep at all** — It does NOT move identity. · (12) **`LoraPanel`'s `nOutfit` has no setter wired to any control** — So a new dataset's outfit field always submits an empty string (found v1.276.41, deliberately not fixed).
📌 **v1.277.15-.28 are UNPUBLISHED at his request** (2026-08-16) — publishing is otherwise normal cadence; agent publishes MUST pass `-Yes`.

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
