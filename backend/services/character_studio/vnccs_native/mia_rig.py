"""Local Make-It-Animatable auto-rigging (v1.174) -- app-side manager.

Rigs character meshes into Mixamo-skeleton FBX **on the app machine** -- no
ComfyUI custom nodes, no worker install.  MIA (CVPR 2025, MIT) runs fine on
CPU (~1-3 min per character, once) and uses CUDA automatically when present.

Three responsibilities, all lazy so users install nothing up-front:
* ensure_venv()    -- create runtime/mia/venv with pinned CPU (or CUDA) wheels
                      (torch 2.1.2, torch-cluster, einops, timm, bpy 4.3 ...).
                      All prebuilt wheels; no compilers, no spconv/flash-attn.
* ensure_weights() -- download the 5 MIA checkpoints (~2.2GB total, once) from
                      HuggingFace + the Mixamo template FBX.
* run_rig()        -- run mia_local/driver.py in that venv as a subprocess,
                      stream progress, return the rigged FBX bytes.

This module itself imports NO heavy deps -- safe to import in the app process.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import threading
import urllib.request
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger(__name__)

_HERE = Path(__file__).parent.absolute()
MIA_LOCAL_DIR = _HERE / "mia_local"
REPO_ROOT = _HERE.parents[3]
RUNTIME_DIR = Path(os.environ.get("RBMN_MIA_RUNTIME") or (REPO_ROOT / "runtime" / "mia"))
VENV_DIR = RUNTIME_DIR / "venv"
MODELS_DIR = RUNTIME_DIR / "models"
TEMPLATE_PATH = MIA_LOCAL_DIR / "assets" / "animation_characters" / "mixamo.fbx"

# bump when the dependency set changes -- triggers a venv rebuild
_ENV_TAG = "mia-env-v3|torch2.1.2+tv0.16.2|bpy4.3.0|shapely"

MODEL_FILES = ("bw.pth", "bw_normal.pth", "joints.pth", "joints_coarse.pth", "pose.pth")
_HF_BASE = "https://huggingface.co/jasongzy/Make-It-Animatable/resolve/main/output/best/new/"
_TEMPLATE_URL = ("https://raw.githubusercontent.com/PozzettiAndrea/ComfyUI-UniRig/"
                 "main/assets/animation_characters/mixamo.fbx")

_LOCK = threading.Lock()
ProgressCB = Optional[Callable[[str], None]]


def _say(cb: ProgressCB, msg: str) -> None:
    logger.info("mia_rig: %s", msg)
    if cb:
        try:
            cb(msg)
        except Exception:  # noqa: BLE001
            pass


def _venv_python() -> Path:
    return VENV_DIR / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def has_cuda() -> bool:
    return shutil.which("nvidia-smi") is not None


def env_ready() -> bool:
    marker = VENV_DIR / ".rbmn_env_tag"
    try:
        return _venv_python().exists() and marker.read_text(encoding="utf-8").strip().startswith(_ENV_TAG)
    except Exception:  # noqa: BLE001
        return False


def weights_ready() -> bool:
    return all((MODELS_DIR / f).exists() for f in MODEL_FILES) and TEMPLATE_PATH.exists()


def is_ready() -> bool:
    return env_ready() and weights_ready()


def _pip(args: list, cb: ProgressCB, what: str, timeout: int = 2400) -> None:
    _say(cb, f"installing {what}...")
    cmd = [str(_venv_python()), "-m", "pip", "install", "--no-input", *args]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        tail = "\n".join((r.stderr or r.stdout or "").splitlines()[-12:])
        raise RuntimeError(f"MIA env: pip install {what} failed:\n{tail}")


def _find_py311() -> str:
    """Python 3.11 interpreter for the MIA venv (bpy 4.3 wheels are 3.11-only).
    Prefers the interpreter the app itself runs on; falls back to the Windows
    'py -3.11' launcher or a python3.11 on PATH."""
    if sys.version_info[:2] == (3, 11):
        return sys.executable
    candidates = []
    py = shutil.which("py")
    if py:
        candidates.append([py, "-3.11"])
    for exe in ("python3.11", "python311"):
        p = shutil.which(exe)
        if p:
            candidates.append([p])
    for cand in candidates:
        try:
            r = subprocess.run([*cand, "-c", "import sys;print(sys.version_info[:2])"],
                               capture_output=True, text=True, timeout=30)
            if r.returncode == 0 and "(3, 11)" in (r.stdout or ""):
                return " ".join(cand) if len(cand) == 1 else cand  # type: ignore[return-value]
        except Exception:  # noqa: BLE001
            continue
    raise RuntimeError(
        f"MIA rigging needs Python 3.11 (bpy wheels); the app runs "
        f"{sys.version.split()[0]} and no 3.11 was found. Install Python 3.11 "
        "from python.org (the app itself can stay on its current version).")


def ensure_venv(cb: ProgressCB = None) -> None:
    """Create/refresh the dedicated MIA venv (idempotent, ~5-10 min first time)."""
    if env_ready():
        return
    base_py = _find_py311()
    base_cmd = base_py if isinstance(base_py, list) else [base_py]
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    gi = RUNTIME_DIR.parent / ".gitignore"
    if not gi.exists():
        try:
            gi.write_text("*\n", encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass
    if VENV_DIR.exists():
        _say(cb, "rebuilding MIA environment (dependency set changed)")
        shutil.rmtree(VENV_DIR, ignore_errors=True)
    _say(cb, "creating MIA python environment...")
    r = subprocess.run([*base_cmd, "-m", "venv", str(VENV_DIR)],
                       capture_output=True, text=True, timeout=600)
    if r.returncode != 0:
        raise RuntimeError(f"MIA env: venv creation failed: {(r.stderr or '')[-400:]}")

    cuda = has_cuda()
    flavor = "cu121" if cuda else "cpu"
    _say(cb, f"MIA torch flavor: {flavor} ({'NVIDIA GPU found' if cuda else 'no NVIDIA GPU -- CPU mode'})")
    _pip(["--upgrade", "pip", "wheel"], cb, "pip", timeout=600)
    # torchvision is pinned WITH torch: timm depends on torchvision, and
    # letting pip resolve it later would upgrade torch to the latest PyPI
    # build, silently breaking the pinned torch-cluster wheel (v1.174.2).
    _pip(["torch==2.1.2", "torchvision==0.16.2",
          "--index-url", f"https://download.pytorch.org/whl/{flavor}"],
         cb, f"torch 2.1.2 ({flavor}) [~200MB-2GB]")
    _pip(["torch-cluster", "-f", f"https://data.pyg.org/whl/torch-2.1.2+{flavor}.html"],
         cb, "torch-cluster")
    _pip(["einops", "timm", "numpy>=1.26,<2", "trimesh", "plyfile", "safetensors",
          "shapely", "scipy", "networkx",  # trimesh geometry ops (slice_plane etc.)
          "bpy==4.3.0"], cb, "einops/timm/trimesh/shapely/bpy")
    # belt+suspenders: verify the pins survived dependency resolution
    r = subprocess.run([str(_venv_python()), "-c",
                        "import torch,torchvision;print(torch.__version__,torchvision.__version__)"],
                       capture_output=True, text=True, timeout=300)
    if r.returncode != 0 or "2.1.2" not in (r.stdout or ""):
        raise RuntimeError("MIA env: torch pin verification failed: "
                           + ((r.stdout or "") + (r.stderr or ""))[-300:])
    (VENV_DIR / ".rbmn_env_tag").write_text(f"{_ENV_TAG}|{flavor}", encoding="utf-8")
    _say(cb, "MIA environment ready")


def _download(url: str, dest: Path, cb: ProgressCB, label: str) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    req = urllib.request.Request(url, headers={"User-Agent": "rbmn-storyboard/1.0"})
    with urllib.request.urlopen(req, timeout=120) as resp, open(tmp, "wb") as f:
        total = int(resp.headers.get("Content-Length") or 0)
        got = 0
        last_pct = -10
        while True:
            chunk = resp.read(1024 * 512)
            if not chunk:
                break
            f.write(chunk)
            got += len(chunk)
            if total:
                pct = int(got * 100 / total)
                if pct >= last_pct + 10:
                    last_pct = pct
                    _say(cb, f"downloading {label}: {pct}% of {total // (1024*1024)}MB")
    if dest.exists():
        dest.unlink()
    tmp.rename(dest)


def ensure_weights(cb: ProgressCB = None) -> None:
    """Download MIA checkpoints (~2.2GB, once) + the Mixamo template FBX."""
    for i, f in enumerate(MODEL_FILES, 1):
        p = MODELS_DIR / f
        if p.exists():
            continue
        _say(cb, f"MIA weights {i}/{len(MODEL_FILES)}: {f}")
        _download(_HF_BASE + f + "?download=true", p, cb, f)
    if not TEMPLATE_PATH.exists():
        _say(cb, "downloading Mixamo skeleton template")
        _download(_TEMPLATE_URL, TEMPLATE_PATH, cb, "mixamo.fbx")


def run_rig(
    mesh_glb: bytes,
    *,
    out_dir: Path,
    name: str = "rigged",
    device: str = "auto",
    use_normal: bool = False,
    fingers: bool = False,
    cb: ProgressCB = None,
    timeout: int = 3600,
) -> bytes:
    """Rig ``mesh_glb`` locally; returns the rigged FBX bytes.
    Bootstraps the venv + weights on first use (serialized by a lock)."""
    with _LOCK:
        ensure_venv(cb)
        ensure_weights(cb)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    mesh_path = out_dir / f"_{name}_in.glb"
    fbx_path = out_dir / f"_{name}_out.fbx"
    mesh_path.write_bytes(mesh_glb)
    if fbx_path.exists():
        fbx_path.unlink()

    cmd = [str(_venv_python()), str(MIA_LOCAL_DIR / "driver.py"),
           "--mesh", str(mesh_path), "--out", str(fbx_path),
           "--models-dir", str(MODELS_DIR), "--device", device]
    if use_normal:
        cmd.append("--use-normal")
    if fingers:
        cmd.append("--fingers")
    env = dict(os.environ)
    env["MIA_WORK_DIR"] = str(RUNTIME_DIR / "work")
    env["PYTHONUNBUFFERED"] = "1"
    _say(cb, "starting local MIA rig (first run loads ~2GB of models"
             + (", CPU mode may take a few minutes)" if not has_cuda() else ")"))
    # v1.174.2: stderr MERGED into stdout -- with a separate unread stderr
    # pipe, a warning-happy child fills the 64KB buffer and deadlocks.
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, encoding="utf-8", errors="replace",
                            env=env, cwd=str(MIA_LOCAL_DIR))
    tail: list = []
    done = False
    try:
        import time as _t
        deadline = _t.time() + timeout
        for line in proc.stdout:  # type: ignore[union-attr]
            line = line.rstrip()
            if not line:
                continue
            tail.append(line)
            del tail[:-40]
            if line.startswith("MIA_PHASE "):
                _say(cb, line[10:])
            elif line.startswith("MIA_STEP "):
                _say(cb, f"rigging step {line[9:]}")
            elif line.startswith("MIA_LOG "):
                _say(cb, line[8:168])
            elif line.startswith("MIA_DONE "):
                done = True
            if _t.time() > deadline:
                proc.kill()
                raise RuntimeError("MIA rig timed out")
        proc.wait(timeout=60)
    finally:
        if proc.poll() is None:
            proc.kill()
    # v1.174.5: MIA_DONE + the FBX on disk IS success -- bpy-as-module can
    # crash in exit handlers AFTER finishing, poisoning the return code.
    if done and fbx_path.exists():
        if proc.returncode != 0:
            logger.warning("mia_rig: driver exited %s after MIA_DONE (bpy "
                           "teardown crash) -- result is valid", proc.returncode)
    elif proc.returncode != 0:
        raise RuntimeError("MIA rig failed:\n" + "\n".join(tail[-14:]))
    if not fbx_path.exists():
        raise RuntimeError("MIA rig reported success but produced no FBX")
    data = fbx_path.read_bytes()
    for p in (mesh_path, fbx_path):
        try:
            p.unlink()
        except OSError:
            pass
    return data
