"""RBMN Worker Helper — the thing that owns the GPU on a worker box.

v1.215.0 (2026-08-04)

WHY THIS EXISTS
    ComfyUI and a Krea 2 LoRA trainer cannot share a 16 GB card.  ComfyUI's
    POST /free unloads models and empties the torch cache, but the CUDA CONTEXT
    cannot be released while the process lives (300-800 MB, per PyTorch's own
    issue tracker), and ComfyUI-Manager's /manager/reboot is an os.execv that
    PRESERVES the pid -- so there is no moment where the GPU is actually free.
    Something has to stop ComfyUI, prove the GPU came back, run the trainer, and
    put ComfyUI back.  That is this.

    It also removes the last hand-carried step: the app POSTs the dataset zip
    here, this runs the fizgig_run.py that shipped inside it, streams the log
    back, and hands over the trained LoRA.

DESIGN RULES
    * stdlib ONLY.  It has to install on a ComfyUI portable's python_embeded
      with no pip step.  http.server + subprocess + zipfile, nothing else.
    * It NEVER trusts ComfyUI's /system_stats for a free-VRAM decision.  That
      field is cudaMemGetInfo_free + (torch_reserved - torch_active): it adds
      back cache ComfyUI can reclaim and a SECOND PROCESS CANNOT.  Every
      capacity decision here reads NVML through nvidia-smi.
    * Exactly one holder of the GPU at a time, and the lease is written to
      disk, so a helper crash cannot leave the box in a state where both are
      believed stopped.
    * Everything that can fail says WHY in /diag, in one payload, because the
      person debugging this is reading it over a remote session.

USAGE
    python rbmn_helper.py                 # serve on 0.0.0.0:8765
    python rbmn_helper.py --port 9000
    python rbmn_helper.py --probe         # print what it found, serve nothing

The web UI is at http://<worker>:8765/ and is served from this file.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import traceback
import urllib.request
import uuid
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

VERSION = "1.221.0"
IS_WIN = os.name == "nt"
HERE = Path(__file__).resolve().parent
STATE_DIR = Path(os.environ.get("RBMN_HELPER_HOME") or (HERE / "rbmn_helper_data"))
CONFIG_PATH = STATE_DIR / "config.json"
LEASE_PATH = STATE_DIR / "lease.json"
DATASETS_DIR = STATE_DIR / "datasets"
RUNS_DIR = STATE_DIR / "runs"

# How long to wait for ComfyUI to die politely before taking the hammer out.
GRACEFUL_STOP_S = 25
# Windows WDDM can lag several seconds between process exit and the driver
# reclaiming the allocation, so exit alone is not proof.  Poll for this long.
GPU_RELEASE_TIMEOUT_S = 60
# A lease with no live run behind it is a bug or a crashed client.  Reap it.
LEASE_IDLE_REAP_S = 30 * 60

_LOCK = threading.RLock()
_RUNS: dict[str, dict] = {}


# ══════════════════════════════════════════════════════════════════════════
#  small utilities
# ══════════════════════════════════════════════════════════════════════════
def now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def sh(args, timeout=20, **kw) -> tuple[int, str, str]:
    """Run a command, never raise.  Returns (rc, stdout, stderr)."""
    try:
        p = subprocess.run([str(a) for a in args], capture_output=True, text=True,
                           timeout=timeout, **kw)
        return p.returncode, p.stdout or "", p.stderr or ""
    except FileNotFoundError:
        return 127, "", f"{args[0]}: not found"
    except subprocess.TimeoutExpired:
        return 124, "", f"{args[0]}: timed out after {timeout}s"
    except Exception as e:  # noqa: BLE001 — a helper that crashes is useless
        return 1, "", f"{args[0]}: {type(e).__name__}: {e}"


def read_json(p: Path, default):
    try:
        return json.loads(p.read_text("utf-8"))
    except Exception:  # noqa: BLE001
        return default


def write_json(p: Path, obj) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2), "utf-8")
    tmp.replace(p)     # atomic — a half-written lease file is worse than none


# ══════════════════════════════════════════════════════════════════════════
#  GPU — NVML via nvidia-smi.  NEVER ComfyUI's /system_stats.
# ══════════════════════════════════════════════════════════════════════════
def parse_gpu_query(out: str) -> list[dict]:
    """Parse `nvidia-smi --query-gpu=index,name,memory.total,memory.used,
    memory.free,driver_version --format=csv,noheader,nounits`."""
    gpus = []
    for line in out.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 6:
            continue
        try:
            gpus.append({"index": int(parts[0]), "name": parts[1],
                         "mem_total_mb": int(float(parts[2])),
                         "mem_used_mb": int(float(parts[3])),
                         "mem_free_mb": int(float(parts[4])),
                         "driver": parts[5]})
        except ValueError:
            continue
    return gpus


def parse_compute_apps(out: str) -> list[dict]:
    """Parse `nvidia-smi --query-compute-apps=pid,used_memory,process_name
    --format=csv,noheader,nounits`.  Note: on Windows WDDM this list is often
    EMPTY even with processes on the card — the driver does not report
    per-process usage in WDDM mode.  Callers must not treat empty as proof."""
    apps = []
    for line in out.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 2:
            continue
        try:
            apps.append({"pid": int(parts[0]), "used_mb": int(float(parts[1])),
                         "name": parts[2] if len(parts) > 2 else ""})
        except ValueError:
            continue
    return apps


def gpus() -> list[dict]:
    rc, out, _ = sh(["nvidia-smi", "--query-gpu=index,name,memory.total,memory.used,"
                     "memory.free,driver_version", "--format=csv,noheader,nounits"])
    return parse_gpu_query(out) if rc == 0 else []


def compute_apps() -> list[dict]:
    rc, out, _ = sh(["nvidia-smi", "--query-compute-apps=pid,used_memory,process_name",
                     "--format=csv,noheader,nounits"])
    return parse_compute_apps(out) if rc == 0 else []


def free_mb(index: int = 0) -> int:
    for g in gpus():
        if g["index"] == index:
            return g["mem_free_mb"]
    return -1


# ══════════════════════════════════════════════════════════════════════════
#  process discovery + control (Windows-first, degrades on Linux)
# ══════════════════════════════════════════════════════════════════════════
def parse_netstat_pid(out: str, port: int) -> int | None:
    """PID of whatever is LISTENING on `port`, from `netstat -ano` output.

    Matching on ':<port>' alone is wrong — it also hits an ephemeral local port
    in the FOREIGN column, and ':7860' substring-matches ':17860'.  So: require
    the LISTENING state and that the local address ENDS with exactly ':port'."""
    for line in out.splitlines():
        parts = line.split()
        if len(parts) < 4 or "LISTEN" not in line.upper():
            continue
        local = parts[1]
        if not local.endswith(":" + str(port)):
            continue
        try:
            return int(parts[-1])
        except ValueError:
            continue
    return None


def pid_on_port(port: int) -> int | None:
    if IS_WIN:
        rc, out, _ = sh(["netstat", "-ano", "-p", "TCP"])
        return parse_netstat_pid(out, port) if rc == 0 else None
    rc, out, _ = sh(["ss", "-lptn", f"sport = :{port}"])
    if rc == 0:
        m = re.search(r"pid=(\d+)", out)
        if m:
            return int(m.group(1))
    return None


def port_open(host: str, port: int, timeout=1.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout):
            return True
    except OSError:
        return False


def pid_alive(pid: int) -> bool:
    if IS_WIN:
        rc, out, _ = sh(["tasklist", "/FI", f"PID eq {pid}", "/NH"])
        return rc == 0 and str(pid) in out
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


def kill_tree(pid: int, force: bool) -> tuple[int, str]:
    """ComfyUI spawns children (pip during Manager ops, the frontend) and the
    portable launcher is itself a wrapper — killing only the parent orphans
    children that keep the GPU handle open.  /T is not optional."""
    if IS_WIN:
        args = ["taskkill", "/PID", str(pid), "/T"] + (["/F"] if force else [])
    else:
        args = ["kill", "-9" if force else "-TERM", str(pid)]
    rc, out, err = sh(args)
    return rc, (out + err).strip()


# ══════════════════════════════════════════════════════════════════════════
#  discovery — find ComfyUI and Fizgig without being told
# ══════════════════════════════════════════════════════════════════════════
def _drive_roots() -> list[Path]:
    if not IS_WIN:
        return [Path.home(), Path("/opt"), Path("/workspace")]
    roots = []
    for letter in "CDEFGH":
        p = Path(f"{letter}:\\")
        if p.exists():
            roots.append(p)
    return roots


def find_comfy() -> dict:
    """A ComfyUI portable root is the folder containing python_embeded\\ and a
    ComfyUI\\ subfolder.  Look shallowly — a full drive walk on a box with 30 TB
    of models takes minutes and this runs at startup."""
    hits = []
    for root in _drive_roots():
        for depth1 in _safe_iterdir(root):
            for cand in (depth1,) + tuple(_safe_iterdir(depth1)):
                if not cand.is_dir():
                    continue
                if (cand / "python_embeded" / "python.exe").exists() and \
                        (cand / "ComfyUI" / "main.py").exists():
                    hits.append({"root": str(cand),
                                 "python": str(cand / "python_embeded" / "python.exe"),
                                 "main": str(cand / "ComfyUI" / "main.py"),
                                 "kind": "portable"})
                elif (cand / "main.py").exists() and (cand / "custom_nodes").is_dir():
                    hits.append({"root": str(cand), "python": "",
                                 "main": str(cand / "main.py"), "kind": "source"})
    return {"candidates": hits[:8]}


def find_fizgig() -> dict:
    hits = []
    for root in _drive_roots():
        for depth1 in _safe_iterdir(root):
            for cand in (depth1,) + tuple(_safe_iterdir(depth1)):
                if cand.is_dir() and (cand / "lora_trainer_gui.py").exists() and \
                        (cand / "src" / "fizgig").is_dir():
                    py = cand / ("venv/Scripts/python.exe" if IS_WIN else "venv/bin/python")
                    hits.append({"root": str(cand),
                                 "python": str(py) if py.exists() else "",
                                 "prefs": str(cand / "prefs.json")
                                 if (cand / "prefs.json").exists() else ""})
    return {"candidates": hits[:8]}


def _safe_iterdir(p: Path):
    try:
        return [c for c in p.iterdir() if c.is_dir() and not c.name.startswith(("$", "."))]
    except (PermissionError, OSError):
        return []


# ══════════════════════════════════════════════════════════════════════════
#  config
# ══════════════════════════════════════════════════════════════════════════
DEFAULT_CONFIG = {
    "token": "",
    "gpu_index": 0,
    "comfy": {"root": "", "port": 8188, "host": "127.0.0.1", "start_cmd": "",
              "manage": True},
    "fizgig": {"root": "", "python": ""},
    # Free VRAM the trainer needs before we call the GPU "released".  13.1 GB
    # is NF4 at 1024 buckets / rank 16 (11.6 peak + 1.5 headroom) from Fizgig's
    # own measured planner.  Below this a run will page, not fail, which is
    # worse -- so we refuse to start instead.
    "min_free_mb_for_train": 13400,
}


def load_config() -> dict:
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    disk = read_json(CONFIG_PATH, {})
    for k, v in disk.items():
        if isinstance(v, dict) and isinstance(cfg.get(k), dict):
            cfg[k].update(v)
        else:
            cfg[k] = v
    if not cfg.get("token"):
        cfg["token"] = uuid.uuid4().hex
        write_json(CONFIG_PATH, cfg)
    return cfg


def save_config(patch: dict) -> dict:
    cfg = load_config()
    for k, v in patch.items():
        if isinstance(v, dict) and isinstance(cfg.get(k), dict):
            cfg[k].update(v)
        else:
            cfg[k] = v
    write_json(CONFIG_PATH, cfg)
    return cfg


def autodetect_into_config() -> dict:
    """Fill blanks only — never overwrite something a human set."""
    cfg = load_config()
    patch: dict = {}
    if not cfg["comfy"]["root"]:
        c = find_comfy()["candidates"]
        if c:
            patch.setdefault("comfy", {})["root"] = c[0]["root"]
    if not cfg["fizgig"]["root"]:
        f = find_fizgig()["candidates"]
        if f:
            patch.setdefault("fizgig", {})["root"] = f[0]["root"]
            if f[0]["python"]:
                patch["fizgig"]["python"] = f[0]["python"]
    return save_config(patch) if patch else cfg


def comfy_start_cmd(cfg: dict) -> list[str] | None:
    """Explicit start_cmd wins.  Otherwise the portable's own launcher."""
    explicit = (cfg["comfy"].get("start_cmd") or "").strip()
    if explicit:
        return ["cmd", "/c", explicit] if IS_WIN else ["bash", "-lc", explicit]
    root = cfg["comfy"].get("root") or ""
    if not root:
        return None
    r = Path(root)
    for name in ("run_nvidia_gpu.bat", "run_nvidia_gpu_fast_fp16_accumulation.bat",
                 "run_cpu.bat"):
        if (r / name).exists():
            return ["cmd", "/c", str(r / name)]
    py = r / "python_embeded" / ("python.exe" if IS_WIN else "python")
    main = r / "ComfyUI" / "main.py"
    if py.exists() and main.exists():
        return [str(py), "-s", str(main), "--windows-standalone-build"]
    return None


# ══════════════════════════════════════════════════════════════════════════
#  ComfyUI lifecycle
# ══════════════════════════════════════════════════════════════════════════
def comfy_state(cfg: dict) -> dict:
    host, port = cfg["comfy"].get("host", "127.0.0.1"), int(cfg["comfy"].get("port", 8188))
    up = port_open(host, port)
    return {"host": host, "port": port, "listening": up, "pid": pid_on_port(port) if up else None}


def comfy_stop(cfg: dict, log) -> dict:
    """Stop ComfyUI and PROVE the GPU came back.

    Two things this deliberately does not do:
      * trust process exit.  WDDM lags; we poll free VRAM.
      * trust an empty --query-compute-apps list.  In WDDM mode the driver
        often reports no per-process usage at all, so 'the list is empty'
        is not evidence of anything.  Free VRAM is the signal."""
    st = comfy_state(cfg)
    before = free_mb(cfg.get("gpu_index", 0))
    result = {"was_listening": st["listening"], "pid": st["pid"],
              "free_mb_before": before, "steps": []}
    if not st["listening"]:
        result["steps"].append("ComfyUI was not listening — nothing to stop")
        result["free_mb_after"] = before
        result["ok"] = True
        return result
    pid = st["pid"]
    if not pid:
        result["ok"] = False
        result["error"] = (f"port {st['port']} is open but no owning pid was found. "
                           "Is ComfyUI on another machine, or behind a proxy? "
                           "Set comfy.manage=false if this helper should not own it.")
        return result

    rc, msg = kill_tree(pid, force=False)
    log(f"[stop] taskkill /T pid={pid} rc={rc} {msg}")
    result["steps"].append(f"graceful kill_tree({pid}) rc={rc} {msg}")
    deadline = time.time() + GRACEFUL_STOP_S
    while time.time() < deadline and pid_alive(pid):
        time.sleep(1)
    if pid_alive(pid):
        rc, msg = kill_tree(pid, force=True)
        log(f"[stop] FORCE taskkill /T /F pid={pid} rc={rc} {msg}")
        result["steps"].append(f"forced kill_tree({pid}) rc={rc} {msg}")
        t = time.time() + 15
        while time.time() < t and pid_alive(pid):
            time.sleep(1)
    result["pid_gone"] = not pid_alive(pid)

    need = int(cfg.get("min_free_mb_for_train", 13400))
    deadline = time.time() + GPU_RELEASE_TIMEOUT_S
    cur = free_mb(cfg.get("gpu_index", 0))
    while time.time() < deadline:
        cur = free_mb(cfg.get("gpu_index", 0))
        if cur < 0 or cur >= need:
            break
        time.sleep(2)
    result["free_mb_after"] = cur
    result["min_free_mb_required"] = need
    if cur < 0:
        result["ok"] = result["pid_gone"]
        result["warning"] = ("nvidia-smi is unavailable, so the GPU release could not be "
                             "verified — going on process exit alone.")
    else:
        result["ok"] = bool(result["pid_gone"]) and cur >= need
        if not result["ok"] and cur < need:
            result["error"] = (f"ComfyUI is gone but only {cur} MB is free and the run needs "
                               f"{need} MB. Something else is holding the card — check "
                               "/diag.compute_apps.")
    log(f"[stop] free VRAM {before} -> {cur} MB (need {need}) ok={result.get('ok')}")
    return result


def comfy_start(cfg: dict, log) -> dict:
    cmd = comfy_start_cmd(cfg)
    if not cmd:
        return {"ok": False, "error": "no way to start ComfyUI — set comfy.root or "
                                      "comfy.start_cmd in the helper config"}
    st = comfy_state(cfg)
    if st["listening"]:
        return {"ok": True, "already": True, "pid": st["pid"]}
    root = cfg["comfy"].get("root") or None
    log(f"[start] {' '.join(cmd)}")
    flags = {}
    if IS_WIN:
        # Detached + its own group: ComfyUI must outlive this request, and must
        # not receive console events aimed at the helper.
        flags["creationflags"] = (getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                                  | getattr(subprocess, "DETACHED_PROCESS", 0))
    try:
        subprocess.Popen(cmd, cwd=root, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, **flags)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {e}", "cmd": cmd}
    host, port = cfg["comfy"].get("host", "127.0.0.1"), int(cfg["comfy"].get("port", 8188))
    deadline = time.time() + 180      # a cold ComfyUI with many custom nodes is slow
    while time.time() < deadline:
        if port_open(host, port):
            log("[start] ComfyUI is listening again")
            return {"ok": True, "pid": pid_on_port(port), "waited_s": None}
        time.sleep(2)
    return {"ok": False, "error": "started it, but it never began listening within 180s — "
                                  "check ComfyUI's own console"}


# ══════════════════════════════════════════════════════════════════════════
#  GPU lease
# ══════════════════════════════════════════════════════════════════════════
def get_lease() -> dict | None:
    lease = read_json(LEASE_PATH, None)
    if not lease:
        return None
    run = _RUNS.get(lease.get("run_id") or "")
    if run and run.get("status") == "running":
        return lease
    age = time.time() - float(lease.get("at_epoch") or 0)
    if age > LEASE_IDLE_REAP_S:
        return None
    return lease


def take_lease(holder: str, run_id: str | None) -> dict:
    lease = {"holder": holder, "run_id": run_id, "at": now(), "at_epoch": time.time(),
             "id": uuid.uuid4().hex[:12]}
    write_json(LEASE_PATH, lease)
    return lease


def drop_lease() -> None:
    try:
        LEASE_PATH.unlink()
    except OSError:
        pass


# ══════════════════════════════════════════════════════════════════════════
#  runs
# ══════════════════════════════════════════════════════════════════════════
def run_dir(rid: str) -> Path:
    return RUNS_DIR / rid


def load_runs() -> None:
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    for d in RUNS_DIR.iterdir():
        if not d.is_dir():
            continue
        st = read_json(d / "state.json", None)
        if st:
            # A run marked 'running' in a file cannot be running — this process
            # just started, so nothing it spawned survived.  Mark it honestly.
            if st.get("status") == "running":
                st["status"] = "interrupted"
                st["error"] = "the helper restarted while this run was going"
                write_json(d / "state.json", st)
            _RUNS[st["id"]] = st


def save_run(st: dict) -> None:
    write_json(run_dir(st["id"]) / "state.json", st)


def start_run(cfg: dict, dataset: str, opts: dict) -> dict:
    ds_dir = DATASETS_DIR / dataset
    runner = ds_dir / "fizgig_run.py"
    if not runner.exists():
        raise ValueError(f"{dataset} has no fizgig_run.py — upload an export from the app "
                         "(v1.214 or newer builds one into every krea2 zip)")
    fiz = cfg["fizgig"].get("root") or ""
    if not fiz or not (Path(fiz) / "lora_trainer_gui.py").exists():
        raise ValueError("fizgig.root is not set (or is not a Fizgig checkout) — set it in "
                         "the helper UI")
    lease = get_lease()
    if lease:
        raise ValueError(f"the GPU is already leased to {lease['holder']} "
                         f"(run {lease.get('run_id')}) — cancel it first")

    rid = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:4]
    d = run_dir(rid)
    d.mkdir(parents=True, exist_ok=True)
    log_path = d / "log.txt"
    st = {"id": rid, "dataset": dataset, "status": "starting", "started": now(),
          "finished": None, "rc": None, "error": None, "pid": None,
          "stopped_comfy": False, "opts": opts, "log": str(log_path)}
    _RUNS[rid] = st
    save_run(st)
    take_lease("fizgig", rid)
    threading.Thread(target=_run_thread, args=(cfg, st, ds_dir, runner, opts),
                     daemon=True).start()
    return st


def _run_thread(cfg: dict, st: dict, ds_dir: Path, runner: Path, opts: dict) -> None:
    log_path = Path(st["log"])
    fh = log_path.open("a", encoding="utf-8", errors="replace")

    def log(msg: str) -> None:
        fh.write(f"{now()}  {msg}\n")
        fh.flush()

    try:
        log(f"=== RBMN helper {VERSION} · run {st['id']} · dataset {st['dataset']} ===")
        for g in gpus():
            log(f"[gpu] {g['index']} {g['name']} {g['mem_free_mb']}/{g['mem_total_mb']} MB free "
                f"(driver {g['driver']})")

        # ── 1. take the GPU ────────────────────────────────────────────────
        if cfg["comfy"].get("manage", True):
            log("[stop] stopping ComfyUI — the CUDA context cannot be released any other way")
            res = comfy_stop(cfg, log)
            st["stopped_comfy"] = bool(res.get("was_listening"))
            st["stop_result"] = res
            save_run(st)
            if not res.get("ok"):
                raise RuntimeError(res.get("error") or "could not free the GPU")
        else:
            log("[stop] comfy.manage is false — not touching ComfyUI")
            free = free_mb(cfg.get("gpu_index", 0))
            need = int(cfg.get("min_free_mb_for_train", 13400))
            if 0 <= free < need:
                raise RuntimeError(f"only {free} MB free, the run needs {need} MB, and this "
                                   "helper is configured not to manage ComfyUI")

        # ── 2. train ───────────────────────────────────────────────────────
        py = (cfg["fizgig"].get("python") or "").strip() or sys.executable
        cmd = [py, str(runner), "--fizgig", cfg["fizgig"]["root"], "--python", py]
        for flag in ("quant", "epochs", "output-dir"):
            v = opts.get(flag.replace("-", "_"))
            if v not in (None, ""):
                cmd += ["--" + flag, str(v)]
        if opts.get("blocks_to_swap"):
            cmd += ["--blocks-to-swap", str(opts["blocks_to_swap"])]
        if opts.get("skip_cache"):
            cmd.append("--skip-cache")
        if opts.get("dry_run"):
            cmd.append("--dry-run")
        log("[run] " + " ".join(f'"{c}"' if " " in c else c for c in cmd))
        st["status"] = "running"
        st["cmd"] = cmd
        save_run(st)

        flags = {}
        if IS_WIN:
            flags["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        proc = subprocess.Popen(cmd, cwd=str(ds_dir), stdout=fh, stderr=subprocess.STDOUT,
                                **flags)
        st["pid"] = proc.pid
        save_run(st)
        rc = proc.wait()
        st["rc"] = rc
        st["status"] = "done" if rc == 0 else "failed"
        if rc != 0:
            st["error"] = f"fizgig_run.py exited {rc} — see the log"
        log(f"[run] exited {rc}")
    except Exception as e:  # noqa: BLE001
        st["status"] = "failed"
        st["error"] = f"{type(e).__name__}: {e}"
        log("[error] " + st["error"])
        log(traceback.format_exc())
    finally:
        # ── 3. give the GPU back, ALWAYS ───────────────────────────────────
        try:
            if st.get("stopped_comfy") and cfg["comfy"].get("manage", True):
                log("[start] restarting ComfyUI")
                st["start_result"] = comfy_start(cfg, log)
        except Exception as e:  # noqa: BLE001
            log(f"[error] could not restart ComfyUI: {e}")
        drop_lease()
        st["finished"] = now()
        save_run(st)
        log("=== end ===")
        fh.close()


def cancel_run(rid: str) -> dict:
    st = _RUNS.get(rid)
    if not st:
        raise ValueError("no such run")
    if st.get("status") != "running":
        return {"ok": True, "status": st.get("status"), "note": "not running"}
    pid = st.get("pid")
    if pid:
        kill_tree(int(pid), force=True)
    st["status"] = "cancelled"
    save_run(st)
    return {"ok": True, "status": "cancelled"}


def run_artifacts(cfg: dict, st: dict) -> list[dict]:
    out = (st.get("opts") or {}).get("output_dir") or ""
    if not out:
        fiz = cfg["fizgig"].get("root") or ""
        ds = st.get("dataset") or ""
        # fizgig_run.py's default is <fizgig>/output_loras/<DS_ID>, and DS_ID is
        # the dataset id baked into the zip — which is the folder name we
        # unpacked to, so this resolves without asking the runner.
        out = str(Path(fiz) / "output_loras" / ds) if fiz else ""
    p = Path(out)
    if not out or not p.is_dir():
        return []
    # v1.216: RECURSIVE, and not just weights. The previews under sample/ and the
    # per-image loss log are the only things that say whether a checkpoint is any
    # good, and neither was reachable.
    kinds = {".safetensors": "weights", ".png": "image", ".jpg": "image",
             ".jpeg": "image", ".webp": "image", ".jsonl": "log", ".json": "log",
             ".txt": "log", ".log": "log", ".toml": "config", ".yaml": "config",
             ".csv": "log"}
    files = []
    for f in sorted(p.rglob("*")):
        if not f.is_file():
            continue
        kind = kinds.get(f.suffix.lower())
        if not kind:
            continue
        try:
            files.append({
                # Relative POSIX path, so `sample/epoch_000027_00.png` is a name
                # the download route can take verbatim.
                "name": f.relative_to(p).as_posix(),
                "kind": kind,
                "bytes": f.stat().st_size,
                "modified": time.strftime("%Y-%m-%dT%H:%M:%S",
                                          time.localtime(f.stat().st_mtime))})
        except OSError:
            continue
        if len(files) >= 500:
            break
    return files


# Served so a preview opens in a browser rather than downloading as a blob.
ARTIFACT_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                  ".webp": "image/webp", ".json": "application/json",
                  ".jsonl": "application/x-ndjson", ".txt": "text/plain; charset=utf-8",
                  ".log": "text/plain; charset=utf-8",
                  ".toml": "text/plain; charset=utf-8",
                  ".yaml": "text/plain; charset=utf-8",
                  ".csv": "text/csv; charset=utf-8"}


def artifact_path(cfg: dict, st: dict, want: str) -> Path:
    """Resolve an artifact name inside the run's output folder, or raise.

    The name comes off the URL, so it is resolved and checked to be UNDER the
    output folder — a download route must not become a way to read
    ..\\..\\Windows\\System32."""
    out = (st.get("opts") or {}).get("output_dir") or ""
    if not out:
        fiz = cfg["fizgig"].get("root") or ""
        out = str(Path(fiz) / "output_loras" / (st.get("dataset") or "")) if fiz else ""
    base = Path(out).resolve()
    fp = (base / want).resolve()
    if base != fp and base not in fp.parents:
        raise ValueError("that name resolves outside the run's output folder")
    if not fp.is_file():
        raise FileNotFoundError(want)
    return fp


def loras_dir(cfg: dict) -> Path:
    """Where ComfyUI reads LoRAs on THIS box.

    Order: explicit comfy.loras_dir -> derived from comfy.root (the portable
    root or the inner ComfyUI folder) -> the known install on the 16GB
    training box (last resort so a blank config still works there)."""
    explicit = (cfg["comfy"].get("loras_dir") or "").strip()
    if explicit:
        p = Path(explicit)
        if p.is_dir():
            return p
        raise ValueError(f"comfy.loras_dir is set but is not a folder: {explicit}")
    cands = []
    root = (cfg["comfy"].get("root") or "").strip()
    if root:
        cands += [Path(root) / "ComfyUI" / "models" / "loras",
                  Path(root) / "models" / "loras"]
    cands.append(Path(r"E:\ComfyMaster\V1\ComfyUI_windows_portable\ComfyUI\models\loras"))
    for c in cands:
        if c.is_dir():
            return c
    raise ValueError("no loras folder found - set comfy.loras_dir in the helper config")


def install_lora(cfg: dict, st: dict, body: dict) -> dict:
    """v1.217: copy a weights artifact from a run's output into ComfyUI's loras.

    The output folder is shared by every run of the same dataset, so a
    checkpoint whose mtime falls outside THIS run's start/finish window is the
    OTHER run's file and is refused unless force:true says otherwise."""
    name = (body.get("name") or "").strip()
    if not name:
        raise ValueError("name is required")
    src = artifact_path(cfg, st, name)
    if src.suffix.lower() != ".safetensors":
        raise ValueError("only .safetensors files install as LoRAs")
    mod = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(src.stat().st_mtime))
    t0, t1 = str(st.get("started") or ""), str(st.get("finished") or "")
    if t0 and (mod < t0 or (t1 and mod > t1)) and not body.get("force"):
        raise ValueError(f"{name} was written {mod}, outside this run "
                         f"({t0} -> {t1}) - it is another run's checkpoint. "
                         "Pass force:true only if you know why.")
    dest_dir = loras_dir(cfg)
    dest = dest_dir / Path(str(body.get("dest_name") or src.name)).name
    tmp = dest.with_suffix(dest.suffix + ".part")
    shutil.copy2(src, tmp)
    if tmp.stat().st_size != src.stat().st_size:
        tmp.unlink(missing_ok=True)
        raise ValueError("size mismatch after copy")
    os.replace(tmp, dest)
    return {"ok": True, "installed": str(dest), "bytes": dest.stat().st_size,
            "source": str(src), "source_modified": mod,
            "window": [t0, t1]}


# ══════════════════════════════════════════════════════════════════════════
#  v1.218 — inventory + installers (the "what's on this worker" layer)
# ══════════════════════════════════════════════════════════════════════════
_DOWNLOADS: dict = {}


def _comfy_dir(cfg: dict) -> Path:
    root = Path(cfg["comfy"].get("root") or "")
    return root / "ComfyUI" if (root / "ComfyUI").is_dir() else root


def _embedded_python(cfg: dict) -> str:
    explicit = (cfg["comfy"].get("python") or "").strip()
    if explicit:
        return explicit
    root = Path(cfg["comfy"].get("root") or "")
    for cand in (root / "python_embeded" / "python.exe",
                 root / "python_embedded" / "python.exe",
                 _comfy_dir(cfg) / "venv" / "Scripts" / "python.exe"):
        if cand.exists():
            return str(cand)
    return sys.executable


_PROBE_SRC = (
    "import json,sys\n"
    "out={'python':sys.version.split()[0]}\n"
    "def probe(mod):\n"
    "    try:\n"
    "        m=__import__(mod); return getattr(m,'__version__','?')\n"
    "    except Exception as e:\n"
    "        return 'MISSING: '+type(e).__name__\n"
    "try:\n"
    "    import torch\n"
    "    out['torch']=torch.__version__; out['cuda']=torch.version.cuda\n"
    "    out['cuda_available']=torch.cuda.is_available()\n"
    "    if torch.cuda.is_available():\n"
    "        out['gpu']=torch.cuda.get_device_name(0)\n"
    "        cc=torch.cuda.get_device_capability(0); out['sm']=cc[0]*10+cc[1]\n"
    "except Exception as e:\n"
    "    out['torch']='MISSING: '+type(e).__name__\n"
    "for m in ('triton','sageattention','xformers','flash_attn'):\n"
    "    out[m]=probe(m)\n"
    "print(json.dumps(out))\n")


def _run_embedded(cfg: dict, code: str, timeout: float = 120.0) -> dict:
    py = _embedded_python(cfg)
    try:
        r = subprocess.run([py, "-c", code], capture_output=True, text=True,
                           timeout=timeout)
        return {"rc": r.returncode, "stdout": r.stdout.strip()[-8000:],
                "stderr": r.stderr.strip()[-8000:], "python_exe": py}
    except Exception as e:  # noqa: BLE001
        return {"rc": -1, "stdout": "", "stderr": f"{type(e).__name__}: {e}",
                "python_exe": py}


def env_probe(cfg: dict) -> dict:
    r = _run_embedded(cfg, _PROBE_SRC.replace("\\n", "\n"))
    try:
        env = json.loads(r["stdout"].splitlines()[-1]) if r["stdout"] else {}
    except Exception:  # noqa: BLE001
        env = {}
    env["python_exe"] = r.get("python_exe")
    if r["rc"] != 0:
        env["probe_error"] = r["stderr"][:400]
    return env


def inventory(cfg: dict) -> dict:
    cdir = _comfy_dir(cfg)
    nodes = []
    nd = cdir / "custom_nodes"
    if nd.is_dir():
        for d in sorted(nd.iterdir()):
            if d.is_file() and d.suffix == ".py":
                nodes.append({"name": d.name, "kind": "file"})
            elif d.is_dir() and d.name != "__pycache__":
                nodes.append({"name": d.name, "kind": "git" if (d / ".git").exists()
                              else "dir",
                              "disabled": d.name.endswith(".disabled"),
                              "has_requirements": (d / "requirements.txt").exists()})
    models = {}
    md = cdir / "models"
    if md.is_dir():
        for d in sorted(md.iterdir()):
            if not d.is_dir():
                continue
            files = [f for f in d.rglob("*") if f.is_file()
                     and f.suffix.lower() in (".safetensors", ".ckpt", ".pt", ".pth",
                                              ".onnx", ".gguf", ".bin", ".sft")]
            if files:
                models[d.name] = {"count": len(files),
                                  "gb": round(sum(f.stat().st_size for f in files)
                                              / 1e9, 2)}
    return {"comfy_root": str(cfg["comfy"].get("root") or ""),
            "comfy_dir": str(cdir), "custom_nodes": nodes, "models": models,
            "env": env_probe(cfg)}


def install_node(cfg: dict, body: dict) -> dict:
    url = (body.get("git_url") or "").strip()
    if not url.startswith(("https://", "http://")):
        raise ValueError("git_url must be an http(s) git URL")
    name = (body.get("name") or url.rstrip("/").split("/")[-1]
            .removesuffix(".git")).strip()
    if "/" in name or "\\" in name or ".." in name:
        raise ValueError("bad name")
    dest = _comfy_dir(cfg) / "custom_nodes" / name
    steps = []
    if dest.exists():
        r = subprocess.run(["git", "-C", str(dest), "pull", "--ff-only"],
                           capture_output=True, text=True, timeout=600)
        steps.append(f"git pull rc={r.returncode}: {(r.stdout + r.stderr)[-300:]}")
    else:
        r = subprocess.run(["git", "clone", "--depth", "1", url, str(dest)],
                           capture_output=True, text=True, timeout=1800)
        steps.append(f"git clone rc={r.returncode}: {(r.stdout + r.stderr)[-300:]}")
        if r.returncode != 0:
            return {"ok": False, "steps": steps}
    req = dest / "requirements.txt"
    if req.exists():
        rr = _run_embedded(cfg, "", 0.1)  # just to resolve python path
        py = rr["python_exe"]
        r = subprocess.run([py, "-m", "pip", "install", "-r", str(req)],
                           capture_output=True, text=True, timeout=1800)
        steps.append(f"pip -r requirements rc={r.returncode}: "
                     f"{(r.stdout + r.stderr)[-400:]}")
    return {"ok": True, "installed": str(dest), "steps": steps,
            "note": "restart ComfyUI to load the node"}


def pip_install(cfg: dict, body: dict) -> dict:
    args = body.get("args") or []
    if not isinstance(args, list) or not args:
        raise ValueError("args list required, e.g. ['install','triton-windows']")
    bad = [a for a in args if not isinstance(a, str)]
    if bad:
        raise ValueError("args must be strings")
    py = _embedded_python(cfg)
    r = subprocess.run([py, "-m", "pip"] + args, capture_output=True, text=True,
                       timeout=3600)
    return {"ok": r.returncode == 0, "rc": r.returncode,
            "python_exe": py,
            "tail": (r.stdout + "\n" + r.stderr)[-3000:]}


_SAGE_TEST = (
    "import torch\n"
    "from sageattention import sageattn\n"
    "q=torch.randn(1,8,128,64,dtype=torch.float16,device='cuda')\n"
    "k=torch.randn(1,8,128,64,dtype=torch.float16,device='cuda')\n"
    "v=torch.randn(1,8,128,64,dtype=torch.float16,device='cuda')\n"
    "o=sageattn(q,k,v,tensor_layout='HND')\n"
    "print('SAGE_OK', tuple(o.shape), o.dtype)\n")


def sage_verify(cfg: dict) -> dict:
    """The step pip cannot fake: run a REAL sage kernel on the GPU."""
    r = _run_embedded(cfg, _SAGE_TEST.replace("\\n", "\n"), 180.0)
    ok = r["rc"] == 0 and "SAGE_OK" in r["stdout"]
    return {"ok": ok, "stdout": r["stdout"][-800:], "stderr": r["stderr"][-1500:]}


def sage_install(cfg: dict, body: dict) -> dict:
    """Probe the EMBEDDED python, pick the matching prebuilt wheel, install,
    then verify with a real kernel call. Never reports success unverified."""
    steps = []
    env = env_probe(cfg)
    steps.append(f"env: python {env.get('python')} torch {env.get('torch')} "
                 f"cuda {env.get('cuda')} sm {env.get('sm')} "
                 f"triton {env.get('triton')}")
    torch_v = str(env.get("torch") or "")
    if torch_v.startswith("MISSING") or not torch_v:
        return {"ok": False, "steps": steps,
                "error": "torch missing in the embedded python — wrong python probed? "
                         "set comfy.python in the helper config"}
    if not env.get("cuda_available"):
        return {"ok": False, "steps": steps, "error": "CUDA not available"}
    # triton first (windows build)
    if str(env.get("triton", "")).startswith("MISSING"):
        r = pip_install(cfg, {"args": ["install", "-U", "triton-windows"]})
        steps.append(f"triton-windows install ok={r['ok']}: {r['tail'][-200:]}")
        if not r["ok"]:
            return {"ok": False, "steps": steps, "error": "triton-windows failed"}
    wheel = (body.get("wheel_url") or "").strip()
    if not wheel:
        # pick from woct0rdho prebuilt releases by torch major.minor + cu version
        tmm = ".".join(torch_v.split("+")[0].split(".")[:2])
        cu = str(env.get("cuda") or "").replace(".", "")
        try:
            with urllib.request.urlopen(
                    "https://api.github.com/repos/woct0rdho/SageAttention/releases",
                    timeout=60) as rr:
                rels = json.loads(rr.read().decode("utf-8"))
            assets = [a["browser_download_url"] for rel in rels
                      for a in rel.get("assets", [])
                      if a["name"].endswith(".whl") and "windows" in a["name"].lower()]
            cands = [u for u in assets
                     if f"torch{tmm}" in u.replace("+", "") or tmm in u]
            cands = [u for u in cands if not cu or f"cu{cu}" in u] or cands
            if not cands:
                return {"ok": False, "steps": steps,
                        "error": f"no prebuilt wheel found for torch {tmm} cu{cu} — "
                                 f"pass wheel_url explicitly. {len(assets)} assets seen.",
                        "assets_sample": assets[:10]}
            wheel = cands[0]
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "steps": steps,
                    "error": f"could not query wheel releases: {e} — pass wheel_url"}
    steps.append(f"wheel: {wheel}")
    r = pip_install(cfg, {"args": ["install", "-U", wheel]})
    steps.append(f"wheel install ok={r['ok']}: {r['tail'][-300:]}")
    if not r["ok"]:
        return {"ok": False, "steps": steps, "error": "wheel install failed"}
    ver = sage_verify(cfg)
    steps.append(f"VERIFY (real kernel on GPU): ok={ver['ok']} {ver['stdout'][-120:]}")
    out = {"ok": ver["ok"], "steps": steps, "verify": ver}
    if ver["ok"]:
        out["note"] = ("verified with a real kernel call. Launch ComfyUI with "
                       "--use-sage-attention (or a node pack that enables it) to use it.")
    else:
        out["error"] = ("installed but the kernel FAILED — this is the "
                        "'says installed, still errors' case: wheel does not match "
                        "this torch/CUDA/GPU. Try another wheel_url.")
    return out


_HEADER_ZIPS = {
    # triton needs CPython include/ + libs/ inside the embedded python;
    # zips published by the triton-windows author, minor version must match.
    "3.13": "https://github.com/woct0rdho/triton-windows/releases/download/"
            "v3.0.0-windows.post1/python_3.13.2_include_libs.zip",
    "3.12": "https://github.com/woct0rdho/triton-windows/releases/download/"
            "v3.0.0-windows.post1/python_3.12.8_include_libs.zip",
    "3.11": "https://github.com/woct0rdho/triton-windows/releases/download/"
            "v3.0.0-windows.post1/python_3.11.9_include_libs.zip",
}


def install_python_headers(cfg: dict, body: dict) -> dict:
    """v1.219: give the EMBEDDED python its missing include/ + libs/ so
    triton's runtime compiler can link (-lpythonXYZ). The classic reason
    SageAttention 'installs but errors' on ComfyUI portable."""
    py = Path(_embedded_python(cfg))
    pydir = py.parent
    if pydir.name.lower() in ("scripts",):          # venv layout: python lives deeper
        pydir = pydir.parent
    env = env_probe(cfg)
    minor = ".".join(str(env.get("python") or "").split(".")[:2])
    url = (body.get("url") or "").strip() or _HEADER_ZIPS.get(minor)
    if not url:
        raise ValueError(f"no known include/libs zip for python {minor} — pass url")
    steps = [f"python {env.get('python')} at {pydir}", f"zip: {url}"]
    import io
    with urllib.request.urlopen(url, timeout=300) as r:
        blob = r.read()
    steps.append(f"downloaded {len(blob)} bytes")
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        names = z.namelist()
        wanted = [n for n in names
                  if n.replace("\\", "/").lstrip("/").lower()
                  .startswith(("include/", "libs/"))]
        if not wanted:
            raise ValueError(f"zip has no include/ or libs/ at its root: {names[:5]}")
        z.extractall(str(pydir), members=wanted)
    steps.append(f"extracted {len(wanted)} files into {pydir}")
    ok = (pydir / "include" / "Python.h").exists() or \
         (pydir / "Include" / "Python.h").exists()
    libs = list((pydir / "libs").glob("python*.lib")) if (pydir / "libs").exists() else []
    steps.append(f"Python.h present: {ok} · libs: {[p.name for p in libs][:3]}")
    return {"ok": bool(ok and libs), "steps": steps}


def model_download(cfg: dict, body: dict) -> dict:
    url = (body.get("url") or "").strip()
    folder = (body.get("folder") or "").strip()
    if not url.startswith(("https://", "http://")):
        raise ValueError("url required")
    if not folder or "/" in folder or "\\" in folder or ".." in folder:
        raise ValueError("folder must be a plain models subfolder name")
    fname = (body.get("filename") or url.split("?")[0].rstrip("/").split("/")[-1]).strip()
    if "/" in fname or "\\" in fname or ".." in fname:
        raise ValueError("bad filename")
    dest_dir = _comfy_dir(cfg) / "models" / folder
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / fname
    did = uuid.uuid4().hex[:8]
    st = {"id": did, "url": url, "dest": str(dest), "status": "running",
          "bytes": 0, "total": None, "error": None}
    _DOWNLOADS[did] = st

    def _dl():
        tmp = dest.with_suffix(dest.suffix + ".part")
        try:
            req = urllib.request.Request(url)
            tok = (body.get("token") or "").strip()
            if tok:
                req.add_header("Authorization", f"Bearer {tok}")
            with urllib.request.urlopen(req, timeout=120) as r, tmp.open("wb") as fh:
                st["total"] = int(r.headers.get("Content-Length") or 0) or None
                while True:
                    chunk = r.read(1 << 22)
                    if not chunk:
                        break
                    fh.write(chunk)
                    st["bytes"] += len(chunk)
            # v1.221: a dropped connection reads as a clean EOF (b"") — without
            # this check a PARTIAL file gets promoted and reports "done".
            # (Bit us fleet-wide on 2026-08-16: LTX 2.5 + MM3 staging files all
            # truncated at identical byte counts by a mid-download restart.)
            if st["total"] and st["bytes"] != st["total"]:
                raise IOError(f"truncated: got {st['bytes']} of "
                              f"{st['total']} bytes")
            tmp.replace(dest)
            st["status"] = "done"
        except Exception as e:  # noqa: BLE001
            st.update(status="error", error=f"{type(e).__name__}: {e}")
            try:
                tmp.unlink()
            except OSError:
                pass

    threading.Thread(target=_dl, daemon=True).start()
    return {"started": True, "id": did, "dest": str(dest)}


# ══════════════════════════════════════════════════════════════════════════
#  diagnostics — one payload, everything, for pasting into a chat
# ══════════════════════════════════════════════════════════════════════════
def diag(cfg: dict) -> dict:
    d: dict = {"helper": {"version": VERSION, "python": sys.version.split()[0],
                          "platform": sys.platform, "state_dir": str(STATE_DIR),
                          "time": now()},
               "config": json.loads(json.dumps(cfg))}
    d["config"]["token"] = "***"        # a diag gets pasted into chats
    d["gpus"] = gpus()
    d["compute_apps"] = compute_apps()
    d["compute_apps_note"] = ("Windows WDDM often reports NO per-process usage here even when "
                              "processes are on the card. An empty list proves nothing; "
                              "gpus[].mem_free_mb is the number that matters.")
    d["nvidia_smi"] = "ok" if d["gpus"] else "MISSING or failed — no VRAM arbitration possible"

    # ComfyUI
    c = comfy_state(cfg)
    c["start_cmd"] = comfy_start_cmd(cfg)
    c["root_exists"] = bool(cfg["comfy"].get("root")) and Path(cfg["comfy"]["root"]).is_dir()
    if c["listening"]:
        c["system_stats"] = _http_json(f"http://{c['host']}:{c['port']}/system_stats")
        c["system_stats_warning"] = (
            "vram_free here is cudaMemGetInfo_free + (torch_reserved - torch_active): it adds "
            "back cache ComfyUI can reclaim and another process CANNOT. Never size a training "
            "run from it.")
    d["comfyui"] = c

    # Fizgig
    f = {"root": cfg["fizgig"].get("root"), "python": cfg["fizgig"].get("python")}
    root = Path(f["root"]) if f["root"] else None
    f["is_checkout"] = bool(root and (root / "lora_trainer_gui.py").exists())
    if root:
        scripts = root / "src" / "fizgig" / "scripts"
        f["scripts"] = {s: (scripts / s).exists() for s in
                        ("krea2_cache_latents.py", "krea2_cache_text.py", "krea2_train.py")}
        prefs = read_json(root / "prefs.json", {})
        f["prefs_found"] = bool(prefs)
        f["models"] = {}
        for key in ("krea2_raw_dit", "krea2_vae", "krea2_text_encoder", "krea2_turbo_dit"):
            v = prefs.get(key) or ""
            f["models"][key] = {"path": v, "exists": bool(v) and Path(v).is_file(),
                                "bytes": (Path(v).stat().st_size
                                          if v and Path(v).is_file() else 0)}
        f["ready_to_train"] = f["is_checkout"] and all(
            m["exists"] for m in f["models"].values())
    d["fizgig"] = f

    # disk
    d["disk"] = {}
    for label, p in (("state", STATE_DIR), ("fizgig", root)):
        if not p:
            continue
        try:
            u = shutil.disk_usage(str(p if Path(p).exists() else Path(p).anchor))
            d["disk"][label] = {"path": str(p), "free_gb": round(u.free / 2**30, 1),
                                "total_gb": round(u.total / 2**30, 1)}
        except OSError as e:
            d["disk"][label] = {"path": str(p), "error": str(e)}

    d["lease"] = get_lease()
    d["datasets"] = list_datasets()
    d["runs"] = [{k: v for k, v in r.items() if k != "opts"}
                 for r in sorted(_RUNS.values(), key=lambda r: r["id"], reverse=True)[:10]]

    # what would stop a run right now
    blockers = []
    if not d["gpus"]:
        blockers.append("nvidia-smi is not available — the helper cannot verify VRAM")
    if not f.get("is_checkout"):
        blockers.append("fizgig.root is not set or is not a Fizgig checkout")
    elif not f.get("ready_to_train"):
        missing = [k for k, m in f.get("models", {}).items() if not m["exists"]]
        blockers.append("Fizgig models missing from prefs.json: " + ", ".join(missing) +
                        " — run Fizgig's Preferences -> 'Download models for me'")
    if cfg["comfy"].get("manage", True) and c["listening"] and not c["pid"]:
        blockers.append(f"ComfyUI is listening on {c['port']} but no owning pid was found — "
                        "the helper cannot stop it (is it on another machine?)")
    if d["gpus"]:
        g = next((x for x in d["gpus"] if x["index"] == cfg.get("gpu_index", 0)), d["gpus"][0])
        need = int(cfg.get("min_free_mb_for_train", 13400))
        if g["mem_total_mb"] < need:
            blockers.append(f"this card has {g['mem_total_mb']} MB total and the configured "
                            f"run needs {need} MB free — it cannot fit at any setting")
    d["blockers"] = blockers
    d["ready"] = not blockers
    return d


def _http_json(url: str, timeout=5):
    import urllib.request
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except Exception as e:  # noqa: BLE001
        return {"error": f"{type(e).__name__}: {e}"}


# ══════════════════════════════════════════════════════════════════════════
#  datasets
# ══════════════════════════════════════════════════════════════════════════
def list_datasets() -> list[dict]:
    DATASETS_DIR.mkdir(parents=True, exist_ok=True)
    out = []
    for d in sorted(DATASETS_DIR.iterdir()):
        if not d.is_dir():
            continue
        imgs = list((d / "images").glob("*.png")) if (d / "images").is_dir() else []
        out.append({"name": d.name, "images": len(imgs),
                    "captions": len(list((d / "images").glob("*.txt"))) if imgs else 0,
                    "runnable": (d / "fizgig_run.py").exists(),
                    "has_sample_prompts": (d / "sample_prompts.txt").exists(),
                    "has_look_scores": (d / "images" / "fizgig_look_scores.json").exists()})
    return out


def accept_dataset(name: str, blob: bytes) -> dict:
    """Unzip an export.  Rejects absolute and traversing member paths — this is
    an endpoint on a LAN, and zipfile.extractall does not protect you."""
    safe = re.sub(r"[^A-Za-z0-9._-]", "-", name)[:80] or "dataset"
    dest = DATASETS_DIR / safe
    if dest.exists():
        shutil.rmtree(dest, ignore_errors=True)
    dest.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".zip")
    tmp.write_bytes(blob)
    bad = []
    try:
        with zipfile.ZipFile(tmp) as z:
            for m in z.namelist():
                p = Path(m)
                if p.is_absolute() or ".." in p.parts or (IS_WIN and ":" in m):
                    bad.append(m)
            if bad:
                raise ValueError(f"refusing unsafe zip member(s): {bad[:5]}")
            z.extractall(dest)
    finally:
        tmp.unlink(missing_ok=True)
    info = next((d for d in list_datasets() if d["name"] == safe), {"name": safe})
    if not info.get("runnable"):
        info["warning"] = ("no fizgig_run.py in this zip — export again from the app "
                           "(v1.214+ builds one into every krea2 export)")
    return info


# ══════════════════════════════════════════════════════════════════════════
#  HTTP
# ══════════════════════════════════════════════════════════════════════════
class Handler(BaseHTTPRequestHandler):
    server_version = f"RBMNHelper/{VERSION}"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        sys.stderr.write(f"{now()}  {self.address_string()}  {fmt % args}\n")

    # ── plumbing ─────────────────────────────────────────────────────────
    def _send(self, code: int, body: bytes, ctype="application/json"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "X-RBMN-Token, Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def json(self, obj, code=200):
        self._send(code, json.dumps(obj, indent=2, default=str).encode("utf-8"))

    def fail(self, code: int, msg: str):
        self.json({"ok": False, "error": msg}, code)

    def authed(self) -> bool:
        cfg = load_config()
        want = cfg.get("token") or ""
        if not want:
            return True
        got = self.headers.get("X-RBMN-Token") or \
            parse_qs(urlparse(self.path).query).get("token", [""])[0]
        return got == want

    def do_OPTIONS(self):      # noqa: N802
        self._send(204, b"")

    # ── routes ───────────────────────────────────────────────────────────
    def do_GET(self):          # noqa: N802
        u = urlparse(self.path)
        p, q = u.path.rstrip("/") or "/", parse_qs(u.query)
        try:
            if p == "/":
                return self._send(200, UI_HTML.encode("utf-8"), "text/html; charset=utf-8")
            # /health is deliberately unauthenticated: it is how the app finds out
            # a helper exists at all, and it leaks nothing.
            if p == "/health":
                cfg = load_config()
                g = gpus()
                return self.json({"ok": True, "helper": VERSION, "host": socket.gethostname(),
                                  "gpu": g[0] if g else None,
                                  "comfy": comfy_state(cfg),
                                  "lease": get_lease(),
                                  "needs_token": bool(cfg.get("token"))})
            if not self.authed():
                return self.fail(401, "bad or missing X-RBMN-Token")
            cfg = load_config()
            if p == "/diag":
                return self.json(diag(cfg))
            if p == "/config":
                c = json.loads(json.dumps(cfg))
                return self.json(c)
            if p == "/detect":
                return self.json({"comfy": find_comfy(), "fizgig": find_fizgig()})
            if p == "/gpu":
                return self.json({"gpus": gpus(), "compute_apps": compute_apps(),
                                  "lease": get_lease()})
            if p == "/datasets":
                return self.json({"datasets": list_datasets()})
            if p == "/inventory":
                return self.json(inventory(cfg))
            if p == "/downloads":
                return self.json({"downloads": sorted(_DOWNLOADS.values(),
                                                      key=lambda d: d["id"])})
            if p == "/runs":
                return self.json({"runs": sorted(_RUNS.values(), key=lambda r: r["id"],
                                                 reverse=True)})
            m = re.match(r"^/runs/([^/]+)$", p)
            if m:
                st = _RUNS.get(m.group(1))
                if not st:
                    return self.fail(404, "no such run")
                arts = run_artifacts(cfg, st)
                # v1.216: ?kind=image asks for the previews without wading
                # through forty 117MB checkpoints.
                kind = (q.get("kind") or [""])[0]
                if kind:
                    arts = [a for a in arts if a.get("kind") == kind]
                return self.json({**st, "artifacts": arts,
                                  "artifact_kinds": sorted({a["kind"] for a
                                                            in run_artifacts(cfg, st)})})
            m = re.match(r"^/runs/([^/]+)/log$", p)
            if m:
                st = _RUNS.get(m.group(1))
                if not st:
                    return self.fail(404, "no such run")
                off = int((q.get("offset") or ["0"])[0])
                lp = Path(st["log"])
                if not lp.exists():
                    return self.json({"offset": 0, "eof": True, "text": ""})
                size = lp.stat().st_size
                off = max(0, min(off, size))
                with lp.open("rb") as fh:
                    fh.seek(off)
                    chunk = fh.read(256 * 1024)
                return self.json({"offset": off + len(chunk), "size": size,
                                  "eof": off + len(chunk) >= size,
                                  "status": st.get("status"),
                                  "text": chunk.decode("utf-8", "replace")})
            # ── v1.220: peer model serving — download ONCE on one box, then
            # every other box pulls over the LAN at wire speed instead of
            # three boxes splitting the WAN pipe. A peer points its own
            # /download/model at http://this:8765/serve/model/{folder}/{file}
            # ?token=THIS box's token. Streams in 4MB chunks (files are tens
            # of GB — read_bytes() would eat the RAM).
            m = re.match(r"^/serve/model/([^/]+)/(.+)$", p)
            if m:
                folder, fname = m.group(1), unquote(m.group(2))
                if any(x in folder for x in ("/", "\\", "..")) or \
                        any(x in fname for x in ("/", "\\", "..")):
                    return self.fail(400, "bad path")
                fp = _comfy_dir(cfg) / "models" / folder / fname
                if not fp.exists():
                    return self.fail(404, f"no {folder}/{fname} here")
                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Length", str(fp.stat().st_size))
                self.end_headers()
                with fp.open("rb") as fh:
                    while True:
                        chunk = fh.read(1 << 22)
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                return
            # ── v1.220: LLM-worker support — this helper can run on an
            # Ollama-only box (no ComfyUI config needed); the app reads model
            # list + liveness through it, same as it debugs render boxes.
            if p == "/ollama/status":
                try:
                    port = (q.get("port") or ["11434"])[0]
                    with urllib.request.urlopen(
                            f"http://127.0.0.1:{port}/api/tags", timeout=10) as r:
                        tags = json.loads(r.read().decode())
                    return self.json({"ok": True,
                                      "models": [m0.get("name") for m0 in
                                                 (tags.get("models") or [])]})
                except Exception as e:  # noqa: BLE001
                    return self.json({"ok": False,
                                      "error": f"{type(e).__name__}: {e}"})
            # v1.221: report .part orphans (interrupted downloads) under
            # models/ — the 2026-08-16 disk-full forensics tool. ?delete=1
            # removes them (only .part files, nothing else).
            if p == "/cleanup/parts":
                found = []
                mroot = _comfy_dir(cfg) / "models"
                if mroot.is_dir():
                    for fp in mroot.rglob("*.part"):
                        e = {"file": str(fp.relative_to(mroot)),
                             "bytes": fp.stat().st_size}
                        if (q.get("delete") or [""])[0] in ("1", "true"):
                            try:
                                fp.unlink()
                                e["deleted"] = True
                            except OSError as ex:
                                e["deleted"] = False
                                e["error"] = str(ex)
                        found.append(e)
                return self.json({"parts": found,
                                  "total_gb": round(sum(x["bytes"] for x in
                                                        found) / 1e9, 2)})
            m = re.match(r"^/runs/([^/]+)/artifacts/(.+)$", p)
            if m:
                st = _RUNS.get(m.group(1))
                if not st:
                    return self.fail(404, "no such run")
                want = unquote(m.group(2))
                try:
                    fp = artifact_path(cfg, st, want)
                except ValueError as e:
                    return self.fail(400, str(e))
                except (FileNotFoundError, OSError):
                    return self.fail(404, f"no artifact {want}")
                ctype = ARTIFACT_TYPES.get(fp.suffix.lower(), "application/octet-stream")
                return self._send(200, fp.read_bytes(), ctype)
            return self.fail(404, f"no route {p}")
        except Exception as e:  # noqa: BLE001
            self.fail(500, f"{type(e).__name__}: {e}\n{traceback.format_exc()}")

    def do_POST(self):         # noqa: N802
        u = urlparse(self.path)
        p = u.path.rstrip("/") or "/"
        try:
            if not self.authed():
                return self.fail(401, "bad or missing X-RBMN-Token")
            n = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(n) if n else b""
            cfg = load_config()

            m = re.match(r"^/datasets/([^/]+)$", p)
            if m:
                if not raw:
                    return self.fail(400, "POST the zip as the raw request body")
                return self.json(accept_dataset(m.group(1), raw))

            body = json.loads(raw.decode("utf-8")) if raw else {}
            if p == "/config":
                return self.json(save_config(body))
            if p == "/detect/apply":
                return self.json(autodetect_into_config())
            if p == "/comfy/stop":
                return self.json(comfy_stop(cfg, lambda s: sys.stderr.write(s + "\n")))
            if p == "/comfy/start":
                return self.json(comfy_start(cfg, lambda s: sys.stderr.write(s + "\n")))
            if p == "/gpu/release":
                drop_lease()
                return self.json({"ok": True, "lease": None})
            if p == "/runs":
                ds = (body.get("dataset") or "").strip()
                if not ds:
                    return self.fail(400, "dataset is required")
                return self.json(start_run(cfg, ds, body.get("opts") or {}))
            m = re.match(r"^/runs/([^/]+)/cancel$", p)
            if m:
                return self.json(cancel_run(m.group(1)))
            if p == "/install/node":
                return self.json(install_node(cfg, body))
            if p == "/install/pip":
                return self.json(pip_install(cfg, body))
            if p == "/install/python-headers":
                return self.json(install_python_headers(cfg, body))
            if p == "/install/sageattention":
                return self.json(sage_install(cfg, body))
            if p == "/verify/sageattention":
                return self.json(sage_verify(cfg))
            if p == "/download/model":
                return self.json(model_download(cfg, body))
            m = re.match(r"^/runs/([^/]+)/install-lora$", p)
            if m:
                st = _RUNS.get(m.group(1))
                if not st:
                    return self.fail(404, "no such run")
                return self.json(install_lora(cfg, st, body))
            return self.fail(404, f"no route {p}")
        except ValueError as e:
            self.fail(409, str(e))
        except Exception as e:  # noqa: BLE001
            self.fail(500, f"{type(e).__name__}: {e}\n{traceback.format_exc()}")


UI_HTML = """<!doctype html><html><head><meta charset="utf-8">
<title>RBMN Worker Helper</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
:root{color-scheme:dark}
body{background:#111418;color:#dfe4ea;font:14px/1.5 system-ui,Segoe UI,sans-serif;margin:0;padding:20px}
h1{font-size:18px;margin:0 0 4px}h2{font-size:14px;margin:22px 0 8px;color:#8fa6bd;
text-transform:uppercase;letter-spacing:.08em}
.card{background:#191d23;border:1px solid #262c34;border-radius:8px;padding:14px;margin:10px 0}
button{background:#2b6cb0;color:#fff;border:0;border-radius:6px;padding:7px 13px;cursor:pointer;
font-size:13px;margin:2px 4px 2px 0}button:hover{background:#3182ce}
button.warn{background:#9b2c2c}button.warn:hover{background:#c53030}
button.ghost{background:#2d3542}button.ghost:hover{background:#3a4453}
input{background:#0e1116;border:1px solid #2d3542;color:#dfe4ea;border-radius:5px;
padding:6px 8px;width:100%;box-sizing:border-box;font:13px monospace}
label{display:block;font-size:12px;color:#8fa6bd;margin:8px 0 3px}
pre{background:#0b0e12;border:1px solid #222831;border-radius:6px;padding:10px;overflow:auto;
max-height:420px;font:12px/1.45 Consolas,monospace;white-space:pre-wrap}
.row{display:flex;gap:14px;flex-wrap:wrap}.row>*{flex:1;min-width:260px}
.ok{color:#68d391}.bad{color:#fc8181}.warnc{color:#f6ad55}
table{width:100%;border-collapse:collapse;font-size:13px}td,th{text-align:left;padding:4px 6px;
border-bottom:1px solid #222831}th{color:#8fa6bd;font-weight:500}
.pill{display:inline-block;padding:1px 8px;border-radius:99px;font-size:11px;background:#2d3542}
</style></head><body>
<h1>RBMN Worker Helper <span class="pill" id="ver"></span></h1>
<div id="sub" style="color:#8fa6bd;font-size:12px"></div>

<div class="card"><h2 style="margin-top:0">Status</h2><div id="status">loading…</div></div>

<div class="card"><h2 style="margin-top:0">Paths</h2>
<div class="row">
  <div><label>ComfyUI root (the folder with python_embeded\\)</label>
       <input id="comfyRoot"><label>ComfyUI port</label><input id="comfyPort"></div>
  <div><label>Fizgig root (the folder with lora_trainer_gui.py)</label>
       <input id="fizRoot"><label>Fizgig python (blank = auto)</label><input id="fizPy"></div>
</div>
<div style="margin-top:10px">
<button onclick="save()">Save</button>
<button class="ghost" onclick="detect()">Auto-detect</button>
<button class="ghost" onclick="post('/comfy/start')">Start ComfyUI</button>
<button class="warn" onclick="post('/comfy/stop')">Stop ComfyUI</button>
</div></div>

<div class="card"><h2 style="margin-top:0">Datasets</h2><div id="ds">…</div></div>
<div class="card"><h2 style="margin-top:0">Runs</h2><div id="runs">…</div>
<div id="logbox" style="display:none"><h2>Log</h2><pre id="log"></pre></div></div>

<div class="card"><h2 style="margin-top:0">Diagnostics</h2>
<button onclick="loadDiag()">Refresh</button>
<button class="ghost" onclick="copyDiag()">Copy all</button>
<span id="copied" style="color:#68d391;font-size:12px"></span>
<pre id="diag">press Refresh</pre></div>

<script>
const T = new URLSearchParams(location.search).get('token') || localStorage.getItem('rbmn') || '';
if (T) localStorage.setItem('rbmn', T);
const H = {'X-RBMN-Token': T, 'Content-Type': 'application/json'};
const j = (u,o) => fetch(u,{headers:H,...(o||{})}).then(r=>r.json());
const post = (u,b) => j(u,{method:'POST',body:JSON.stringify(b||{})}).then(r=>{refresh();return r});
let DIAG = null, tailing = null, offset = 0;

function esc(s){return String(s).replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]))}

async function refresh(){
  const h = await j('/health');
  document.getElementById('ver').textContent = h.helper || '?';
  document.getElementById('sub').textContent = (h.host||'') + (T ? '' : ' — no token set');
  const g = h.gpu, c = h.comfy, l = h.lease;
  document.getElementById('status').innerHTML =
    (g ? `<div><b>${esc(g.name)}</b> — <span class="${g.mem_free_mb>13400?'ok':'warnc'}">
      ${g.mem_free_mb} MB free</span> of ${g.mem_total_mb} MB · driver ${esc(g.driver)}</div>`
       : `<div class="bad">nvidia-smi unavailable — no VRAM arbitration possible</div>`)
    + `<div>ComfyUI on :${c.port} — ` + (c.listening
        ? `<span class="ok">up</span>${c.pid?` (pid ${c.pid})`:' <span class="bad">(no owning pid — cannot stop it)</span>'}`
        : `<span class="warnc">stopped</span>`) + `</div>`
    + `<div>GPU lease — ` + (l ? `<span class="warnc">${esc(l.holder)}</span> (run ${esc(l.run_id||'-')})`
        : `<span class="ok">free</span>`) + `</div>`;
  const cfg = await j('/config');
  if (document.activeElement.tagName !== 'INPUT') {
    comfyRoot.value = cfg.comfy.root||''; comfyPort.value = cfg.comfy.port||8188;
    fizRoot.value = cfg.fizgig.root||''; fizPy.value = cfg.fizgig.python||'';
  }
  const d = await j('/datasets');
  document.getElementById('ds').innerHTML = d.datasets.length ? '<table><tr><th>name</th>'
    + '<th>images</th><th>captions</th><th></th></tr>' + d.datasets.map(x=>`<tr>
      <td>${esc(x.name)}</td><td>${x.images}</td><td>${x.captions}</td>
      <td>${x.runnable?`<button onclick="startRun('${esc(x.name)}')">Train</button>
      <button class="ghost" onclick="startRun('${esc(x.name)}',1)">Dry run</button>`
      :'<span class="bad">no fizgig_run.py</span>'}</td></tr>`).join('') + '</table>'
    : '<i style="color:#8fa6bd">none yet — the app posts them here</i>';
  const r = await j('/runs');
  document.getElementById('runs').innerHTML = r.runs.length ? '<table><tr><th>id</th>'
    + '<th>dataset</th><th>status</th><th></th></tr>' + r.runs.map(x=>`<tr>
      <td>${esc(x.id)}</td><td>${esc(x.dataset)}</td>
      <td class="${x.status==='done'?'ok':x.status==='running'?'warnc':x.status==='failed'?'bad':''}">
      ${esc(x.status)}${x.error?' — '+esc(x.error):''}</td>
      <td><button class="ghost" onclick="tail('${esc(x.id)}')">Log</button>
      ${x.status==='running'?`<button class="warn" onclick="post('/runs/${esc(x.id)}/cancel')">Cancel</button>`:''}
      </td></tr>`).join('') + '</table>' : '<i style="color:#8fa6bd">none yet</i>';
}
function save(){ post('/config',{comfy:{root:comfyRoot.value,port:+comfyPort.value},
  fizgig:{root:fizRoot.value,python:fizPy.value}}) }
function detect(){ post('/detect/apply') }
function startRun(ds,dry){ post('/runs',{dataset:ds,opts:dry?{dry_run:true}:{}})
  .then(r=>{ if(r.error) alert(r.error); else tail(r.id) }) }
function tail(id){
  offset = 0; document.getElementById('log').textContent = '';
  document.getElementById('logbox').style.display = 'block';
  if (tailing) clearInterval(tailing);
  const pull = () => j(`/runs/${id}/log?offset=${offset}`).then(r=>{
    if (r.text) { const el = document.getElementById('log');
      el.textContent += r.text; el.scrollTop = el.scrollHeight; offset = r.offset; }
    if (r.status && r.status !== 'running' && r.eof) { clearInterval(tailing); tailing=null; refresh(); }
  });
  pull(); tailing = setInterval(pull, 1500);
}
function loadDiag(){ j('/diag').then(d=>{ DIAG=d;
  document.getElementById('diag').textContent = JSON.stringify(d,null,2) }) }
function copyDiag(){ navigator.clipboard.writeText(JSON.stringify(DIAG||{},null,2))
  .then(()=>{ const c=document.getElementById('copied'); c.textContent='copied';
  setTimeout(()=>c.textContent='',1500) }) }
refresh(); setInterval(refresh, 5000);
</script></body></html>"""


# ══════════════════════════════════════════════════════════════════════════
def main() -> int:
    ap = argparse.ArgumentParser(description="RBMN Worker Helper")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--probe", action="store_true", help="print what it found, serve nothing")
    a = ap.parse_args()

    STATE_DIR.mkdir(parents=True, exist_ok=True)
    DATASETS_DIR.mkdir(parents=True, exist_ok=True)
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    cfg = autodetect_into_config()
    load_runs()

    if a.probe:
        print(json.dumps(diag(cfg), indent=2, default=str))
        return 0

    srv = ThreadingHTTPServer((a.host, a.port), Handler)
    srv.daemon_threads = True
    ips = []
    try:
        ips.append(socket.gethostbyname(socket.gethostname()))
    except OSError:
        pass
    print(f"RBMN Worker Helper {VERSION}")
    print(f"  state    {STATE_DIR}")
    print(f"  ComfyUI  {cfg['comfy']['root'] or '(not found — set it in the UI)'}")
    print(f"  Fizgig   {cfg['fizgig']['root'] or '(not found — set it in the UI)'}")
    print(f"  TOKEN    {cfg['token']}")
    print()
    for ip in ips or ["<this-box>"]:
        print(f"  open  http://{ip}:{a.port}/?token={cfg['token']}")
    print("\nCtrl-C to stop.")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
