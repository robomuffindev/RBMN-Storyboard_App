#!/usr/bin/env python3
"""Dump what each ComfyUI worker ACTUALLY has -- the app never logs this.

The app only ever enumerates the handful of model categories it uses, so
"is the depth LoRA there? under what exact name? is there a ControlNet? a depth
preprocessor?" has never been answerable from the repo.  This asks every worker
directly and writes a compact report Claude can read through the connected
folder.

Run:  worker_probe.bat            (all workers from settings)
      worker_probe.bat http://192.168.12.224:8188/
Out:  <repo>/_diag/worker_probe/report.json  + report.txt
Then tell Claude: "probe done".
"""
from __future__ import annotations
import json, os, sqlite3, sys, urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "_diag" / "worker_probe"

# class -> the widget whose option list we want listed in full
ENUMERATE = [
    ("UNETLoader", "unet_name"),
    ("UnetLoaderGGUF", "unet_name"),
    ("LoraLoaderModelOnly", "lora_name"),
    ("VAELoader", "vae_name"),
    ("CLIPLoader", "clip_name"),
    ("ControlNetLoader", "control_net_name"),
    ("DiffControlNetLoader", "control_net_name"),
    ("UpscaleModelLoader", "model_name"),
    ("DWPreprocessor", "bbox_detector"),
    ("DWPreprocessor", "pose_estimator"),
    ("DepthAnythingV2Preprocessor", "ckpt_name"),
    ("PuLIDModelLoader", "pulid_file"),
]
# classes we only need a yes/no on
PRESENCE = [
    "ControlNetLoader", "ControlNetApplyAdvanced", "ControlNetApplySD3",
    "SetUnionControlNetType", "DiffControlNetLoader",
    "DepthAnythingV2Preprocessor", "Zoe-DepthMapPreprocessor",
    "MiDaS-DepthMapPreprocessor", "DWPreprocessor", "OpenposePreprocessor",
    "SplitSigmas", "SplitSigmasDenoise", "BasicScheduler", "Flux2Scheduler",
    "SamplerCustomAdvanced", "ImageScale", "VAEEncode", "ReferenceLatent",
    "ReferenceLatentPlus", "EmptyFlux2LatentImage", "UNETLoader",
    "TextEncodeQwenImageEdit", "TextEncodeQwenImageEditPlus",
    "SetLatentNoiseMask", "InpaintModelConditioning", "DifferentialDiffusion",
]
# substrings worth calling out wherever they appear in a model list
INTEREST = ["refcontrol", "depth", "klein", "controlnet", "matching", "posestudio",
            "consistency", "pulid", "qwen"]


def worker_urls() -> list:
    if len(sys.argv) > 1:
        return [u.strip() for u in sys.argv[1:] if u.strip()]
    # DB FIRST: the app reads its fleet from app_settings.comfyui_urls, and .env
    # goes stale (it still listed a dead .203 while the live fleet was
    # .163/.202/.224) -- probing the wrong hosts is worse than not probing.
    for db in (Path(os.path.expanduser("~/RBMN-Projects")) / "RBMN.db",):
        if not db.exists():
            continue
        try:
            con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
            row = con.execute("SELECT comfyui_urls FROM app_settings LIMIT 1").fetchone()
            con.close()
            if row and row[0]:
                raw = str(row[0]).strip()
                v = json.loads(raw) if raw.startswith("[") else [x for x in raw.split(",") if x.strip()]
                if v:
                    return [str(x).strip() for x in v]
        except Exception:  # noqa: BLE001
            pass
    envf = REPO / ".env"
    if envf.exists():
        for line in envf.read_text(encoding="utf-8", errors="ignore").splitlines():
            if line.strip().upper().startswith("COMFYUI_URLS"):
                raw = line.split("=", 1)[1].strip().strip('"').strip("'")
                try:
                    v = json.loads(raw)
                    if isinstance(v, list) and v:
                        return [str(x) for x in v]
                except Exception:  # noqa: BLE001
                    if raw:
                        return [u.strip() for u in raw.split(",") if u.strip()]
    return []


def opts(oi: dict, cls: str, name: str) -> list:
    try:
        for section in ("required", "optional"):
            spec = ((oi.get(cls) or {}).get("input") or {}).get(section, {}).get(name)
            if not spec:
                continue
            first = spec[0] if isinstance(spec, (list, tuple)) and spec else None
            if isinstance(first, list):
                return [str(o) for o in first]
    except Exception:  # noqa: BLE001
        pass
    return []


def probe(url: str) -> dict:
    base = url.rstrip("/")
    r: dict = {"url": base}
    try:
        with urllib.request.urlopen(f"{base}/object_info", timeout=30) as fh:
            oi = json.loads(fh.read().decode("utf-8"))
    except Exception as e:  # noqa: BLE001
        r["error"] = f"{type(e).__name__}: {e}"
        return r
    r["node_count"] = len(oi)
    r["present"] = {c: (c in oi) for c in PRESENCE}
    models: dict = {}
    for cls, widget in ENUMERATE:
        if cls not in oi:
            continue
        o = opts(oi, cls, widget)
        if o:
            models[f"{cls}.{widget}"] = o
    r["models"] = models
    hits: dict = {}
    for key, lst in models.items():
        for m in lst:
            low = m.lower()
            for word in INTEREST:
                if word in low:
                    hits.setdefault(word, []).append(f"{key}: {m}")
    r["interesting"] = hits
    return r


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    urls = worker_urls()
    if not urls:
        print("ERROR: no worker URLs found. Pass one: worker_probe.bat http://host:8188/")
        return 1
    out = []
    for u in urls:
        print(f"probing {u} ...")
        out.append(probe(u))
    (OUT / "report.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    lines = []
    for r in out:
        lines.append(f"===== {r['url']}")
        if r.get("error"):
            lines.append(f"  UNREACHABLE: {r['error']}")
            continue
        lines.append(f"  nodes: {r['node_count']}")
        yes = [k for k, v in r["present"].items() if v]
        no = [k for k, v in r["present"].items() if not v]
        lines.append(f"  PRESENT : {', '.join(yes)}")
        lines.append(f"  MISSING : {', '.join(no) or '(none)'}")
        for key, lst in (r.get("models") or {}).items():
            lines.append(f"  -- {key}  ({len(lst)})")
            for m in lst:
                lines.append(f"       {m}")
    txt = "\n".join(lines)
    (OUT / "report.txt").write_text(txt, encoding="utf-8")
    print("\n" + txt[:4000])
    print("\nwrote ->", OUT)
    print('Tell Claude: "probe done"')
    return 0


if __name__ == "__main__":
    sys.exit(main())
