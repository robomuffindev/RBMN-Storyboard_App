# RBMN Operations Runbook (v1.276.4, 2026-08-09)

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
1. **🧬 Text 2 Image** — character front door. Name-first (resumable), engines Klein (0-5
   refs) / **Krea 2 Turbo (renders on the training box; 🎓 LoRA picker with trigger display
   + one-click add-to-prompt; strength 1.0)**, pose scaffolds (full-body-front default),
   batch 1-8, edit-iterate loop with version chains, master gallery, 🏁 promote → front
   ref + base, 📖 Profile & Lore (Story Builder substrate, ✨ LLM fill).
2. **Create / Clothes / Emotions** — Klein 3.0 character lane (refs, base, strip, views,
   wardrobe, expressions). Views are face-anchored (v1.275.2) — but see the v1.275.8
   RETRACTION in CHANGELOG: the anchor does NOT drive view identity. **The lever is the
   REFERENCE LIST** (v1.275.9): one ref per tag, uploads first, back views last, cap 3.
   **👗 Outfit sets (v1.276.2):** `POST /api/klein3/characters/{slug}/outfits` — a named
   outfit, 13 optional garment slots (4 core + 9 detail), optional `variant` for a look
   within it, rendered across every view. Each view is its own standalone image.
2b. **🎭 `/studio`** — the Character Studio front page: EVERY character from EVERY mode
   (both stores), pipeline checklist per card, capability-aware actions.
2c. **⚙ Experimental Modes** (Settings, default OFF) — hides 🧪 Klein 1.0 and 🚀 Klein 2.0
   from the mode picker. Code untouched; kept for later game-asset export.
3. **Pose Library** — SETS/TAGS, imports, generate-missing.
4. **🎓 LoRA Dataset Gen** — the dataset+training lane:
   - **⚡ Autogen** (top): character → whole recipe → installed LoRA. Options: 👕 signature
     outfit / 👗 wardrobe variations (vision-proposed outfits mixed in so the base outfit
     isn't baked), "dataset only".
   - Per dataset: plan/render/caption/QC/repair/export, and **🚀 Train** (export → upload →
     Fizgig → ArcFace pick → install; background, stages persisted, survives restarts and
     box dropouts; `{run_id}` attaches to an existing run).
5. **🪪 Character Sheet** — one downloadable reference image (turnaround + face row) per
   character, composited from identity-scored dataset images. No GPU, no LoRA needed.
6. **🎬 Video Lab** — MiniMax H3 video generation, LOCAL on any registry worker. Five
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
| `k3_face_audit.py --char SLUG` | **(v1.275.4, FREE — CPU, no GPU, no worker)** ArcFace-scores EVERY ref of a Klein 3.0 character against the uploaded front ref AND against the face anchor, plus head yaw / keypoint yaw / detector score. Run it before spending renders on identity work. `--json` for the machine-readable form. |

The in-app 🚀/⚡ pipelines are these scripts' logic as code (`backend/api/lora_train.py`);
the scripts remain the manual/recovery path.

## 7. Key API surfaces (beyond the UI)

- `/api/lora/*` — datasets, plan(-preview) (re-plan DESTROYS moved slots; force required),
  generate, caption, qc, repair, likeness (per-angle baselines), wardrobe-check, export
  (min_likeness floor), **train, train/status, autogen, autogen/{slug}/status,
  trainer-settings, trainer-paths, trainer-detect**.
- `/api/forge/*` — 🧬: characters (name-first), generate, edit, gallery, promote, lore(+
  generate), engines, **loras (with trigger map), krea2-host**.
- `/api/charsheet/*` — characters, generate, sheets (+`?download=1`).
- `/api/h3/*` — 🎬: overview (workers/caps/defaults), upload (image|video|audio),
  generate (all five modes), jobs (+{id}, DELETE), media/{id} (+`?download=1`),
  jobs/{id}/upscale (LTX 2.3, largest_size 1280/1920/2560), draft-prompt (Ollama+spec).
- `/api/klein3/*` — refs, base, strip, views/generate, generate(-set), posefit,
  **outfits (GET+POST: 13 named slots, variants, rendered per view)**, and
  `refs/{id}/image?download=1` for a meaningfully-named single file.
- `/api/characters` — **the unified character list (v1.276.0)**: every character from every
  mode, from BOTH stores, with polymorphic ids `k3:<slug>` / `db:<uuid>`. Use this rather
  than assuming a character is a `studio_characters` row — Klein 3.0 characters never are.
- `/api/lora/datasets/{id}/base-outfit` — GET options / PUT the outfit a dataset renders
  from. Opt-in; unset keeps the existing base behaviour exactly.
- Zero-cost preflights: `/api/health`, `/api/lora/health`, `/api/lora/likeness-health`,
  `/api/klein3/health`, helper `/health`.

## 7c. Secrets and repo hygiene (v1.276.4 — read before any push)

**The helper token is NOT in the source any more.** It used to be a hard-coded default in
seven tracked files. `scripts/helper_token.py` resolves it, first hit wins:

1. `RBMN_HELPER_TOKEN` in the environment
2. `scripts/helper_token.txt` — **gitignored**, this is where it lives on this machine
3. `token` / `helper_token` / `trainer_token` in `_libraries/forge/settings.json`
4. empty — which surfaces as a helper 401, a far better failure than a token from git

Rotating the token means changing it on the boxes and updating (2) or (1). Nothing else.

**Before pushing:** `.gitignore` does NOT untrack anything already committed. Measured
2026-08-09: `_diag/` held 683 tracked files / 327 MB and `VNCCS302/` 266 files / 31 MB,
against roughly 11 MB of actual application code. Run
`powershell -ExecutionPolicy Bypass -File scripts\git_cleanup.ps1` to see the report and
`-Apply` to untrack them (files stay on disk; only the index changes).

⚠ That shrinks FUTURE commits only. `.git` is ~880 MB because those binaries are in past
commits; shrinking the remote needs a deliberate `git filter-repo` on a fresh clone.

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

## 10. Current state & roadmap (2026-08-09)

**Done and measured:** dataset lane + wardrobe QC · training loop (4× by hand, then
automated) · controlled experiment (dataset quality = the ceiling) · 🏠/🧬/🪪/🎓 modes ·
in-app 🚀 Train + ⚡ Autogen + multi-worker registry · SageAttention fleet-wide (37%
faster, ArcFace unchanged) · **🎬 Video Lab: MiniMax H3 local — ✅ FIRST LIVE RENDER PASSED
2026-08-09** (t2v, 5.17s/124f/1280×736/turbo on ZOAI3, **377s** incl. first model load,
h264 + AAC stereo, identity coherent across the clip) **and ✅ ⬆ LTX 2.3 upscale PASSED**
(494s → 1920×1088 + source audio, real re-detailing, artifacts in `scripts/_diag/`).
⚠ Two known issues from that exam: `/object_info` "optional" args can still be positional
in the node's Python (cost one failed run — v1.275.3), and **the upscale returns 121 frames
from 124** because LTX needs f=8k+1 while H3 wants f%17==5; they agree only at
f ∈ {73, 209, 345, …}. · 🙂 face-anchored view generation **MEASURED** (v1.275.4–.6):
propagation is excellent (views 0.76–0.82 vs the anchor) but the anchor itself is only
0.35–0.47 vs Lorenzo's upload — the set converges on the WRONG face, so `_face_prompt` is
now the open item; anchor gated at 0.45, BEST-not-newest, back views no longer usable as
identity refs. Still pending: first real ⚡ Autogen run, and H3's other four modes
(i2v / first_last / last_frame / ref2v have never touched the GPU).

**Roadmap (Lorenzo's words):** verify the new stuff tracks → finish the character studio
area → tie characters into projects/other site sections (H3 video into project scenes) →
Story Builder mode reading the 📖 lore store. Parked lanes: Klein 1.0 pose control
(feet/hands/pelvis), Klein 2.0 3D statue mode (16GB likeness ceiling). Shelf: small-set
epoch measurement, bilingual captions, masked training, per-image LR, Fizgig Repair
Studio / LoRA Royale, LTX ultra extender/slicer/audio-replacer sections (unmined).
