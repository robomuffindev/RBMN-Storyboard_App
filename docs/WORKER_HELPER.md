# RBMN Worker Helper

A small service that **owns the GPU** on a worker box. Nothing else starts or
stops ComfyUI or Fizgig; exactly one of them holds the card at a time.

- `scripts/worker/rbmn_helper.py` — the whole thing, stdlib only
- `scripts/worker/rbmn_helper.bat` — launcher (no install step)

---

## Why it exists

ComfyUI and a Krea 2 trainer cannot share a 16 GB card, and **you cannot make
ComfyUI let go without killing it**:

| | releases | notes |
|---|---|---|
| `POST /free` | model weights, torch cache, DynamicVRAM VBAR pages | fire-and-forget: the 200 comes back before anything is freed |
| — | **NOT the CUDA context** | 300–800 MB, per-process, impossible while the process lives ([pytorch#20532](https://github.com/pytorch/pytorch/issues/20532)) |
| Manager `POST /manager/reboot` | nothing | it is `os.execv` — the **pid is preserved**, so there is no moment where the GPU is free |
| process exit | everything | the only complete answer |

At 1024 buckets / rank 16, NF4 needs **~13.1 GB free** (11.6 peak + 1.5 headroom,
from Fizgig's measured planner). Idle-ComfyUI residual plus Windows WDDM
overhead eats the margin, and WDDM **pages rather than OOMs** — so the failure
mode is a training run that silently takes 4× as long, not one that errors.
Hence: stop it, prove the VRAM came back, train, put it back.

> **There is an untested alternative.** ComfyUI has an undocumented
> `--vram-headroom GB` flag whose own help text says it keeps VRAM free *"even
> counting VRAM from other apps"*, with NVML pressure on by default. It is
> absent from the official docs and nobody has published whether it survives a
> 13 GB neighbour. If you want to try coexistence later, that is the flag —
> set `comfy.manage = false` in the helper config and measure.

### The `/system_stats` trap

`system_stats.devices[].vram_free` is **not** free VRAM. Per
`model_management.get_free_memory()` it is
`cudaMemGetInfo_free + (torch_reserved - torch_active)` — it adds back cache
**ComfyUI** can reclaim and a **second process cannot**. Sizing a training run
from it will overcommit. The helper never reads it for a capacity decision;
every such decision goes through NVML via `nvidia-smi`.

---

## Setup

Copy both files anywhere on the worker (they do not need to live inside
ComfyUI). Then:

```
rbmn_helper.bat --probe
```

That prints the full diagnostic and exits without serving. Read it before
anything else — it tells you what was auto-detected and what is blocking a run.
Then:

```
rbmn_helper.bat
```

It prints a URL with the token baked in. Open it from your desktop browser:

```
open  http://192.168.1.50:8765/?token=<generated>
```

Windows will prompt for firewall access the first time — tick **Private
networks**. If you dismissed it:

```
netsh advfirewall firewall add rule name="RBMN Helper" dir=in action=allow protocol=TCP localport=8765
```

Auto-detection looks two levels deep on each drive root for a ComfyUI portable
(`python_embeded\` + `ComfyUI\main.py`) and a Fizgig checkout
(`lora_trainer_gui.py` + `src\fizgig\`). It **only fills blanks** — anything you
set by hand in the UI is never overwritten. Config lives in
`rbmn_helper_data\config.json` next to the script (override with
`RBMN_HELPER_HOME`).

---

## API

`/health` is deliberately **unauthenticated** — it is how the app discovers a
helper exists, and it leaks nothing but the hostname, GPU model and lease
state. Everything else needs `X-RBMN-Token` (header or `?token=`).

| method | route | what |
|---|---|---|
| GET | `/health` | version, GPU, ComfyUI state, lease — no token needed |
| GET | `/diag` | **everything**, one payload, token redacted, with a `blockers` list |
| GET/POST | `/config` | partial patch merges; the token survives |
| GET | `/detect` · POST `/detect/apply` | re-scan for ComfyUI/Fizgig |
| GET | `/gpu` | live NVML numbers + compute apps + lease |
| POST | `/comfy/stop` · `/comfy/start` | manual arbiter control |
| POST | `/gpu/release` | force-drop a stranded lease |
| GET | `/datasets` · POST `/datasets/{name}` | list; upload an export zip as the **raw body** |
| POST | `/runs` | `{"dataset": "...", "opts": {...}}` |
| GET | `/runs` · `/runs/{id}` | state + artifacts |
| GET | `/runs/{id}/log?offset=N` | byte-offset tail; returns `{offset, size, eof, status, text}` |
| POST | `/runs/{id}/cancel` | kill the tree |
| GET | `/runs/{id}/artifacts/{file}` | download the trained LoRA |

`opts` passes straight through to the `fizgig_run.py` inside the zip:
`quant` (`nf4`/`int8`/`fp8`), `epochs`, `blocks_to_swap`, `skip_cache`,
`dry_run`, `output_dir`.

---

## What it guarantees

- **The lease is on disk.** A helper crash cannot leave the box believing both
  ComfyUI and Fizgig are stopped. A run marked `running` in a state file is
  re-read as `interrupted` on startup, because nothing it spawned survived.
- **A failed run still releases the GPU.** The restart is in a `finally`.
- **Stopping is verified, not assumed.** `taskkill /T` (the `/T` matters —
  ComfyUI spawns children that keep the GPU handle), escalate to `/F` after
  25 s, then poll free VRAM until it clears the threshold or 60 s elapse.
  Process exit alone is not accepted as proof; WDDM lags.
- **An empty `--query-compute-apps` list proves nothing.** Under WDDM the
  driver frequently reports no per-process usage at all. Free VRAM is the
  signal, and `/diag` says so in the payload.
- **Uploads cannot escape.** `zipfile.extractall` does not protect you; every
  member is checked for absolute paths, `..`, and drive letters before
  extraction.
- **`netstat` matching is exact.** `:8188` must be the LISTENING *local*
  address — not a foreign address, and not a substring of `:18188`.

---

## Testing

`scripts/patches/test_helper_v1215.py` starts the real server on a random port
and drives it over HTTP: parser fixtures from captured `nvidia-smi` /
`netstat` output, a fake Fizgig checkout, a real export zip, a run that
succeeds, a run that exits 3, a traversing zip, a stranded-lease restart, and
the artifact download. 60 checks, no GPU required.

```
python scripts/patches/test_helper_v1215.py scripts/worker/rbmn_helper.py
```

---

## Not in v1 (deliberate)

Inventory/preflight (custom-node packs via `/object_info`'s `python_module`,
model files via `/experiment/models/{folder}`), node install via
ComfyUI-Manager's queue, and HuggingFace model fetch. The Manager notes worth
keeping for when we do:

- its queue is **two-phase** — `POST /manager/queue/install` only enqueues;
  nothing runs until `POST /manager/queue/start`, then poll
  `/manager/queue/status` until `is_processing` is false, then `/manager/reboot`
- at the default `security_level = normal`, `/customnode/install/git_url` and
  `/customnode/install/pip` **403**; they need `weak`
- snapshots cover custom nodes + pip, **not models** — model parity needs its
  own path


---

# v1.217 additions (2026-08-07) — install-lora + the network bat

## POST /runs/{id}/install-lora

Copies a weights artifact from the run's output folder into ComfyUI's loras folder **on the
same box**, so a picked checkpoint never travels through a person.

    POST /runs/<run-id>/install-lora?token=...
    {"name": "dorian-v1-b1966f-000016.safetensors",
     "dest_name": "optional-rename.safetensors",
     "force": false}

- Only `.safetensors`. Copy is `.part` → size check → atomic `os.replace`.
- `dest_name` is flattened to its basename — the route cannot write outside the loras folder.
- **Run-window guard:** Fizgig's output folder is shared by every run of the same dataset, so
  an artifact whose mtime falls outside THIS run's started→finished window is the OTHER run's
  checkpoint and is refused unless `force: true`. (Numbered above this run's epoch count =
  definitely the other run's.)
- Destination resolution order: `comfy.loras_dir` config → `comfy.root`/ComfyUI/models/loras
  → `comfy.root`/models/loras → the training box's known install path as a last resort.
- Returns `{installed, bytes, source, source_modified, window}`.

## comfy.start_cmd must be the NETWORK bat

The portable's own `run_nvidia_gpu.bat` (the root-derived fallback) binds ComfyUI to
127.0.0.1 — invisible to every other machine, which cost a debugging round on 2026-08-07.
Set the explicit command instead (persisted via `POST /config`):

    {"comfy": {"start_cmd": "E:\\ComfyMaster\\V1\\ComfyUI_windows_portable\\run_nvidia_gpu-LTX2-16GB.bat"}}

`save_config` merges one level deep, so posting just `{"comfy": {"start_cmd": ...}}` keeps
root/port/manage intact.

## Restart behavior

`_RUNS` reloads from each run's `state.json` at startup (`load_runs`), so restarting the
helper (e.g. to deploy new helper code) keeps run history and artifact serving for old runs.
The helper cannot self-update: copy the new `rbmn_helper.py` over the box's copy and restart
its bat.
