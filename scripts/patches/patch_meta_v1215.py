"""v1.215 — VERSION, pyproject, CHANGELOG for the Worker Helper."""
import sys
from pathlib import Path

ROOT = Path(sys.argv[1] if len(sys.argv) > 1 else ".")

v = ROOT / "VERSION"
assert v.read_text("utf-8").strip() == "1.214.0", v.read_text("utf-8")
v.write_text("1.215.0\n", "utf-8")

pp = ROOT / "pyproject.toml"
s = pp.read_text("utf-8")
assert s.count('version = "1.214.0"') == 1
pp.write_text(s.replace('version = "1.214.0"', 'version = "1.215.0"', 1), "utf-8")

ENTRY = '''## v1.215.0 -- the Worker Helper: something that OWNS the GPU on a worker (2026-08-04)

His idea, and the right one: a small app on the worker that detects ComfyUI and Fizgig, can
invoke either, gives real debugging, moves data both ways -- "as i dont believe both can be
running at the same time as they would capture the gpu."

**He is right, and the research says it is worse than that.** Verified against ComfyUI 0.30.0
and ComfyUI-Manager V3.41 source, not prose:
- `POST /free` (which our client already calls) sets two queue flags and returns 200 BEFORE
  anything is freed. It does release model weights, the torch cache and DynamicVRAM's VBAR
  pages -- but the **CUDA context cannot be released while the process lives** (300-800 MB,
  per PyTorch's own issue tracker; contexts are per-process and unshareable).
- Manager's `POST /manager/reboot` is an `os.execv` -- the **pid is preserved**. There is no
  instant at which the GPU is free. It is not a release path at all.
- So a 13.1 GB NF4 run on a 16 GB card, against an idle-but-running ComfyUI plus Windows WDDM
  overhead, has under 1-2 GB of margin -- and **WDDM pages rather than OOMs**, so the failure
  mode is a run that silently takes 4x as long, not one that errors.
  -> Decision (his): always hard-stop ComfyUI. Something has to own that. This is it.

**A trap we were one step away from walking into.** `system_stats.devices[].vram_free` is NOT
free VRAM: `get_free_memory()` returns `cudaMemGetInfo_free + (torch_reserved - torch_active)`,
i.e. it adds back cache ComfyUI can reclaim and **a second process cannot**. Our client reads
`/system_stats` today. The helper never uses it for a capacity decision -- every one goes
through NVML via `nvidia-smi`.

`scripts/worker/rbmn_helper.py` + `rbmn_helper.bat` -- **stdlib only, no install step**, so it
runs on any python 3.9+ including ComfyUI's `python_embeded`. It serves its own single-file web
UI on its port (not tkinter: works headless, reachable from his desktop browser).
- **GPU lease, on disk.** One holder at a time. A helper crash cannot leave the box believing
  both are stopped, and a run recorded `running` in a state file is re-read as `interrupted` on
  startup because nothing it spawned survived.
- **Stopping is proven, not assumed.** `taskkill /T` (the `/T` matters -- ComfyUI spawns
  children that keep the GPU handle open), escalate to `/F` after 25 s, then poll free VRAM
  until it clears 13400 MB or 60 s elapse. Process exit is not accepted as proof; WDDM lags.
  An empty `--query-compute-apps` list proves nothing either -- under WDDM the driver often
  reports no per-process usage at all, and `/diag` says so in the payload.
- **The transfer problem is gone.** The app POSTs the v1.214 export zip to `/datasets/{name}`,
  the helper runs the `fizgig_run.py` that shipped inside it, streams the log back by byte
  offset, and serves the trained `.safetensors` from `/runs/{id}/artifacts/{file}`.
- **A failed run still gives the GPU back** -- the ComfyUI restart is in a `finally`.
- **`/diag`** is one payload with a `blockers` list: missing nvidia-smi, a Fizgig prefs.json
  whose models are absent (named by key), a ComfyUI listening on a port with no owning pid.
  Token redacted, because a diag gets pasted into a chat.
- `/health` is deliberately unauthenticated -- it is how the app discovers a helper exists.

Two security details that are not theatre: `zipfile.extractall` does not protect you, so every
member is checked for absolute paths, `..` and drive letters before extraction; and the netstat
PID match requires the LISTENING **local** address to end in exactly `:port`, so a foreign
address does not win and `:8188` does not match `:18188`.

Verified: `test_helper_v1215.py` starts the REAL server on a random port and drives it over
HTTP -- parser fixtures from captured nvidia-smi/netstat output, a fake Fizgig checkout, a real
export zip, a run that succeeds, a run that exits 3, a traversing zip, a stranded-lease
restart, artifact download. 60 checks, no GPU needed, all pass on the live file
(md5 6276997d52ae43674f742aab29c71d72).

**Left on the table deliberately, with the notes to do it later:** ComfyUI-Manager's queue is
two-phase (`/manager/queue/install` only enqueues; nothing runs until `/manager/queue/start`,
then poll `/manager/queue/status`, then `/manager/reboot`); at the default
`security_level = normal` a git-URL or pip node install **403s** and needs `weak`; and Manager
snapshots cover custom nodes + pip but **not models**, so model parity needs its own path.
Also noted: ComfyUI has an **undocumented `--vram-headroom GB`** flag whose help text claims it
holds VRAM free "even counting VRAM from other apps" -- absent from the official docs, unproven
at 13 GB. If coexistence is ever worth testing, that is the flag and `comfy.manage=false` is
the switch.

Not yet wired: the app-side button. That comes next, once his `--probe` output says what his
box actually reports.

'''
cl = ROOT / "CHANGELOG.md"
s = cl.read_text("utf-8")
assert s.startswith("## v1.214.0"), s[:40]
cl.write_text(ENTRY + s, "utf-8")
print("VERSION 1.215.0 · pyproject · CHANGELOG")
