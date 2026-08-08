"""One-shot ComfyUI-Trellis2 installer for RBMN workers (16GB VRAM edition).

Run with the ComfyUI PORTABLE's embedded python (install_trellis2.bat does this
for you). What it does, in order:

  1. locates the ComfyUI folder + custom_nodes (works from the portable root
     OR the ComfyUI subfolder)
  2. gets ComfyUI-Trellis2 (git clone, or ZIP download when git is missing;
     skipped if already present)
  3. pip installs its requirements.txt
  4. auto-picks the right bundled wheel set for THIS python+torch
     (cp tag from the running python; TorchXXX folder from torch.__version__,
     preferring the CUDA-13 set when torch is built for cu13x) and installs
     every matching wheel (cumesh, nvdiffrast, nvdiffrec_render, flex_gemm,
     o_voxel, ...)
  5. pip installs triton-windows (the pack imports triton but doesn't list it)
  6. verifies each native module actually imports, and prints a clear
     OK / FAIL summary

Models (TRELLIS.2-4B-FP8 ~8.1GB, DINOv3 1.2GB, ...) are NOT downloaded here --
the Trellis2LoadModel node auto-downloads them on the first run. Do NOT install
flash-attn; the app's graphs use the sdpa backend.

Exit code 0 = ready (restart ComfyUI next). Anything else: read the log.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

REPO_URL = "https://github.com/visualbruno/ComfyUI-Trellis2"
ZIP_URL = REPO_URL + "/archive/refs/heads/main.zip"

OK = "  [OK]  "
BAD = "  [FAIL]"


def log(msg: str) -> None:
    print(msg, flush=True)


def run(args: list[str], **kw) -> int:
    log("  > " + " ".join(str(a) for a in args))
    return subprocess.call([str(a) for a in args], **kw)


def find_comfy_root() -> Path:
    """The folder that CONTAINS custom_nodes/. Tries cwd, script dir, and the
    usual portable layouts around the running python."""
    cands = []
    here = Path.cwd()
    script_dir = Path(__file__).resolve().parent
    py_root = Path(sys.executable).resolve().parent.parent  # portable root if embedded
    for base in (here, script_dir, py_root):
        cands += [base, base / "ComfyUI", base.parent, base.parent / "ComfyUI"]
    seen = set()
    for c in cands:
        try:
            c = c.resolve()
        except OSError:
            continue
        if c in seen:
            continue
        seen.add(c)
        if (c / "custom_nodes").is_dir():
            return c
    raise SystemExit(BAD + " could not find a ComfyUI folder (one containing "
                     "custom_nodes\\). Run this from the portable root or the "
                     "ComfyUI folder.")


def get_repo(custom_nodes: Path) -> Path:
    dest = custom_nodes / "ComfyUI-Trellis2"
    if (dest / "requirements.txt").exists():
        log(OK + f" ComfyUI-Trellis2 already present at {dest} (not re-downloading)")
        return dest
    git = shutil.which("git")
    if git:
        log("-- cloning ComfyUI-Trellis2 (git) ...")
        if run([git, "clone", "--depth", "1", REPO_URL, str(dest)]) == 0 \
                and (dest / "requirements.txt").exists():
            return dest
        log(BAD + " git clone failed -- falling back to ZIP download")
    log("-- downloading ComfyUI-Trellis2 ZIP (no git needed) ...")
    import urllib.request
    zpath = custom_nodes / "_trellis2_tmp.zip"
    urllib.request.urlretrieve(ZIP_URL, zpath)  # noqa: S310
    with zipfile.ZipFile(zpath) as z:
        z.extractall(custom_nodes)
    zpath.unlink(missing_ok=True)
    extracted = custom_nodes / "ComfyUI-Trellis2-main"
    if extracted.is_dir():
        if dest.exists():
            shutil.rmtree(dest)
        extracted.rename(dest)
    if not (dest / "requirements.txt").exists():
        raise SystemExit(BAD + " download/extract failed -- get the repo manually into "
                         f"{dest}")
    return dest


def _usable_wheels(d: Path, py_tag: str) -> list[Path]:
    """Wheels in ``d`` for this python, minus natten/flash-attn (Pixal3D-only /
    deliberately skipped -- graphs use sdpa)."""
    return sorted(w for w in d.glob(f"*{py_tag}*.whl")
                  if not w.name.lower().startswith(("natten", "flash")))


def pick_wheel_dir(repo: Path, torch_ver: str, cuda_ver: str, py_tag: str) -> Path:
    """The wheel folder for THIS torch+python.  Scans one level of SUBFOLDERS
    too -- e.g. cp313 wheels live only in 'Torch2100/CUDA 13.1' (measured
    2026-08-01), not in the top-level Torch2100 dir.  Only folders that
    actually contain usable ``py_tag`` wheels are candidates; CUDA-tagged
    subfolders are preferred iff their major matches torch's CUDA major."""
    base = repo / "wheels" / ("Windows" if os.name == "nt" else "Linux")
    if not base.is_dir():
        raise SystemExit(BAD + f" no wheels folder at {base}")
    tm = re.match(r"(\d+)\.(\d+)", torch_ver)
    if not tm:
        raise SystemExit(BAD + f" cannot parse torch version {torch_ver!r}")
    want = f"torch{tm.group(1)}{tm.group(2)}"        # e.g. torch27, torch210

    def norm(s: str) -> str:
        return re.sub(r"[^a-z0-9]", "", s.lower())

    dirs = [d for d in base.iterdir() if d.is_dir()]
    tops = [d for d in dirs if norm(d.name).startswith(want)]
    if not tops:
        raise SystemExit(
            BAD + f" no wheel folder for torch {torch_ver} (looked for '{want}*' in "
            f"{base}: {', '.join(sorted(d.name for d in dirs))}). Your torch may be "
            "newer than the pack supports -- check the repo's wheels folder.")
    cands: list[Path] = []
    for t in sorted(tops):
        cands.append(t)
        cands.extend(sorted(d for d in t.iterdir() if d.is_dir()))
    usable = [d for d in cands if _usable_wheels(d, py_tag)]
    if not usable:
        avail = sorted({mt.group(0) for t in tops for w in t.rglob("*.whl")
                        if (mt := re.search(r"cp\d+", w.name))})
        raise SystemExit(
            BAD + f" no {py_tag} wheels anywhere under {', '.join(t.name for t in tops)} "
            f"(available python tags: {', '.join(avail) or 'none'}) -- this python "
            "version isn't covered by the pack's wheel sets")
    cuda_major = (cuda_ver or "").split(".")[0]

    def score(d: Path) -> int:
        n = norm(d.name)
        if "cuda" in n:
            return 2 if (cuda_major and f"cuda{cuda_major}" in n) else -2
        return 1 if d in tops else 0
    best = max(usable, key=score)
    bn = norm(best.name)
    if "cuda" in bn and cuda_major and f"cuda{cuda_major}" not in bn:
        log(f"  [WARN] only wheel set with {py_tag} wheels is {best.name!r} but this "
            f"torch is built for CUDA {cuda_ver} -- trying it anyway")
    return best


def main() -> int:
    log("=" * 70)
    log("ComfyUI-Trellis2 one-shot installer (RBMN Klein 2.0, 16GB workers)")
    log("=" * 70)
    py_tag = f"cp{sys.version_info.major}{sys.version_info.minor}"
    log(f"python : {sys.version.split()[0]}  ({py_tag})  ->  {sys.executable}")
    try:
        import torch  # noqa: F401  (present in every ComfyUI env)
        torch_ver = torch.__version__.split("+")[0]
        cuda_ver = torch.version.cuda or "?"
    except Exception as e:  # noqa: BLE001
        log(BAD + f" torch not importable in this python ({e}) -- are you running "
            "the EMBEDDED python (python_embeded\\python.exe)?")
        return 2
    log(f"torch  : {torch_ver}  (CUDA {cuda_ver})")

    comfy = find_comfy_root()
    log(f"ComfyUI: {comfy}")
    repo = get_repo(comfy / "custom_nodes")

    # Pack bug workaround (measured 2026-08-02): Trellis2LoadModel unconditionally
    # copies reconviagen_pipeline.json into models/microsoft/TRELLIS.2-4B/, but
    # when the FP8 model is selected that folder is never created -> instant
    # FileNotFoundError on first run.  Pre-create it and place the file.
    ms_dir = comfy / "models" / "microsoft" / "TRELLIS.2-4B"
    src_json = repo / "reconviagen_pipeline.json"
    try:
        ms_dir.mkdir(parents=True, exist_ok=True)
        if src_json.exists() and not (ms_dir / "reconviagen_pipeline.json").exists():
            shutil.copyfile(src_json, ms_dir / "reconviagen_pipeline.json")
        log(OK + f" reconviagen_pipeline.json staged in {ms_dir}")
    except Exception as e:  # noqa: BLE001
        log(f"  [WARN] could not stage reconviagen_pipeline.json ({e}) -- create "
            f"{ms_dir} by hand if the first texture run complains about it")

    pip = [sys.executable, "-m", "pip", "install", "--no-warn-script-location"]

    # open3d has NO wheels for some pythons (e.g. cp313 as of 2026-08 -- PyPI
    # tops out at cp312), and the pack only imports it LAZILY in two optional
    # nodes (Trellis2PostProcessMesh's merge/NaN options and
    # Trellis2LaplacianSmoothingWithOpen3d) -- neither is used by the RBMN
    # statue-texturing graph, so it is safe to skip.  Everything else in
    # requirements.txt is required.
    OPTIONAL = {"open3d"}
    log("-- installing requirements.txt (per package; optional ones may skip) ...")
    reqs = [ln.strip() for ln in (repo / "requirements.txt").read_text("utf-8").splitlines()
            if ln.strip() and not ln.strip().startswith("#")]
    skipped: list[str] = []
    for req in reqs:
        name = re.split(r"[<>=!~\[; ]", req, maxsplit=1)[0].lower()
        if run(pip + [req]) == 0:
            continue
        if name in OPTIONAL:
            skipped.append(name)
            log(f"  [WARN] optional dependency {name!r} has no build for this python "
                "-- SKIPPED. (Only the merge-vertices/NaN post-process options and the "
                "Open3D smoothing node need it; the statue texturing path does not.)")
        else:
            log(BAD + f" required dependency {req!r} failed to install (see pip output above)")
            return 3

    wdir = pick_wheel_dir(repo, torch_ver, cuda_ver, py_tag)
    wheels = _usable_wheels(wdir, py_tag)
    log(f"-- wheel set: {wdir.parent.name}\\{wdir.name}  ({len(wheels)} wheels for {py_tag})"
        if wdir.parent.name.lower().startswith("torch")
        else f"-- wheel set: {wdir.name}  ({len(wheels)} wheels for {py_tag})")
    for w in wheels:
        log(f"   - {w.name}")
    if run(pip + [str(w) for w in wheels]) != 0:
        log(BAD + " wheel install failed (see pip output above)")
        return 5

    if os.name == "nt":
        log("-- installing triton-windows (imported by the pack, not in its requirements) ...")
        if run(pip + ["triton-windows"]) != 0:
            log(BAD + " triton-windows install failed -- the pack may not import; "
                "try manually: python_embeded\\python.exe -m pip install triton-windows")
            return 6
        # Portable/embedded python ships WITHOUT C headers, and triton JIT-compiles
        # a CUDA driver stub at runtime -> 'Python.h not found' on the first kernel
        # (hit on a real worker 2026-08-02). woct0rdho/triton-windows publishes the
        # matching include+libs bundles; patch version may differ, minor must match.
        py_root = Path(sys.executable).resolve().parent
        if not (py_root / "include" / "Python.h").exists():
            mm = f"{sys.version_info.major}.{sys.version_info.minor}"
            bundles = {"3.13": "python_3.13.2_include_libs.zip",
                       "3.12": "python_3.12.7_include_libs.zip",
                       "3.11": "python_3.11.9_include_libs.zip"}
            zn = bundles.get(mm)
            url = ("https://github.com/woct0rdho/triton-windows/releases/download/"
                   f"v3.0.0-windows.post1/{zn}") if zn else None
            ok = False
            if url:
                log(f"-- embedded python lacks C headers; fetching {zn} ...")
                try:
                    import urllib.request
                    zp = py_root / "_include_libs.zip"
                    urllib.request.urlretrieve(url, zp)  # noqa: S310
                    with zipfile.ZipFile(zp) as z:
                        z.extractall(py_root)
                    zp.unlink(missing_ok=True)
                    ok = (py_root / "include" / "Python.h").exists()
                except Exception as e:  # noqa: BLE001
                    log(f"  [WARN] header bundle download failed: {e}")
            if ok:
                log(OK + f" python include/libs installed into {py_root}")
            else:
                log("  [WARN] could not stage python include/libs -- triton kernels "
                    "will fail with 'Python.h not found'. Manual fix: download the "
                    "python_<ver>_include_libs.zip for your python from "
                    "github.com/woct0rdho/triton-windows/releases (v3.0.0-windows.post1) "
                    f"and extract into {py_root}")

    # ── Models: pre-download + REPAIR (resumable) ────────────────────────────
    # The pack only auto-downloads when a model folder doesn't exist, so an
    # interrupted first run leaves a partial folder it never fixes (hit on a
    # real worker 2026-08-02: missing pipeline_fp8.json).  snapshot_download is
    # resumable and verifies existing files, so running it here is both the
    # pre-seed AND the repair.  Skip with: install_trellis2.bat --no-models
    if "--no-models" not in sys.argv:
        log("-- pre-downloading / verifying models (~9.6GB first time; resumable; "
            "re-runs only fetch what's missing) ...")
        models_dir = comfy / "models"
        try:
            from huggingface_hub import snapshot_download, hf_hub_download
            snapshot_download(repo_id="visualbruno/TRELLIS.2-4B-FP8",
                              local_dir=str(models_dir / "visualbruno" / "TRELLIS.2-4B-FP8"))
            if not (models_dir / "visualbruno" / "TRELLIS.2-4B-FP8" / "pipeline_fp8.json").exists():
                raise RuntimeError("TRELLIS.2-4B-FP8 snapshot incomplete (pipeline_fp8.json missing)")
            log(OK + " TRELLIS.2-4B-FP8 complete")
            dv_dir = models_dir / "facebook" / "dinov3-vitl16-pretrain-lvd1689m"
            for fn in ("model.safetensors", "config.json", "preprocessor_config.json"):
                hf_hub_download(repo_id="visualbruno/dinov3-vitl16-pretrain-lvd1689m",
                                filename=fn, local_dir=str(dv_dir))
            log(OK + " DINOv3 encoder complete (ungated mirror)")
            til_dir = models_dir / "microsoft" / "TRELLIS-image-large"
            for fn in ("ckpts/ss_dec_conv3d_16l8_fp16.safetensors",
                       "ckpts/ss_dec_conv3d_16l8_fp16.json"):
                hf_hub_download(repo_id="microsoft/TRELLIS-image-large",
                                filename=fn, local_dir=str(til_dir))
            log(OK + " TRELLIS-image-large ss decoder complete")
        except Exception as e:  # noqa: BLE001
            log(f"  [WARN] model pre-download incomplete ({e}) -- the first texture "
                "run will fetch what's missing. Do NOT interrupt that run; if a "
                "download was interrupted in the past, RE-RUN this installer to repair.")

    log("-- verifying native modules import ...")
    fails = []
    for mod in ("cumesh", "nvdiffrast", "flex_gemm", "o_voxel", "triton"):
        r = subprocess.call([sys.executable, "-c", f"import {mod}"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        log((OK if r == 0 else BAD) + f" import {mod}")
        if r != 0:
            fails.append(mod)
    # nvdiffrec_render sometimes exposes a different import name -- try both, warn only
    r = subprocess.call([sys.executable, "-c",
                         "import importlib;\n"
                         "ok=False\n"
                         "for n in ('nvdiffrec_render','nvdiffrec'):\n"
                         "    try: importlib.import_module(n); ok=True; break\n"
                         "    except Exception: pass\n"
                         "raise SystemExit(0 if ok else 1)"],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    log((OK if r == 0 else "  [WARN]") + " import nvdiffrec_render"
        + ("" if r == 0 else "  (name may differ in this wheel set -- watch the ComfyUI console)"))

    log("=" * 70)
    if fails:
        log(BAD + f" NOT ready -- failed imports: {', '.join(fails)}. Paste this log to Claude.")
        return 7
    if skipped:
        log(f"  [NOTE] skipped optional deps: {', '.join(skipped)} (texturing unaffected)")
    log(OK + " Install complete. NEXT: restart ComfyUI (or start it) and watch its")
    log("        startup console -- no red Trellis2 import error = ready. Then check")
    log("        /api/klein2/health from the app and run the first statue.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
