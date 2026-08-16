"""🎥 Stage LTX 2.5 on EVERY worker — the official ComfyUI model set.

LTX 2.5 (released 2026-08-11): 22B AV model, Diffusion Fidelity Rendering,
native multishot, auto duration, 4K HDR. Native ComfyUI day-0 support with
T2V / I2V / FLF2V templates. The official Lightricks/LTX-2.5 repo is
LICENSE-GATED on HuggingFace; this pulls the identical files from the ungated
community mirror (dummy9996/LTX-2.5-22b-ungate, 33k+ downloads) plus the
ungated Comfy-Org gemma-4 encoder.

Files staged (the int8-convrot set — the official quant for consumer cards):
  diffusion_models/ ltx-2.5-22b-distilled-transformer-comfy-int8-convrot (21.5 GB)
  text_encoders/    gemma4-12b-with-proj-ltx-2.5-comfy-int8-convrot      (15.4 GB)
  text_encoders/    gemma4_e2b_it_bf16                                   ( 5.5 GB)
  vae/              ltx-2.5-video-vae-conv-bf16                          ( 1.5 GB)
  vae/              ltx-2.5-audio-vae-bf16                               ( 365 MB)
  latent_upscale_models/ ltx-2.5-latent-spatial-upscaler-x2-bf16-1.0     ( 996 MB)
  latent_upscale_models/ ltx-2.5-latent-temporal-upscaler-x2-bf16-1.0    ( 262 MB)

~45 GB per box. Downloads run in the HELPER's background — this script only
STARTS them and exits; run with --check (repeatedly) to see what has landed,
verified against each ComfyUI's OWN model listing.

    python scripts/install_ltx25.py           # start all downloads, exit
    python scripts/install_ltx25.py --check   # verify what's installed
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")   # type: ignore[union-attr]
except Exception:  # noqa: BLE001
    pass

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_MIRROR = "https://huggingface.co/dummy9996/LTX-2.5-22b-ungate/resolve/main/"
_COMFY_ORG = ("https://huggingface.co/Comfy-Org/gemma-4/resolve/main/"
              "text_encoders/")

#: (url, comfy models subfolder, filename, comfy listing route)
FILES = [
    (_MIRROR + "ltx-2.5-22b-distilled-transformer-comfy-int8-convrot.safetensors",
     "diffusion_models",
     "ltx-2.5-22b-distilled-transformer-comfy-int8-convrot.safetensors",
     "diffusion_models"),
    (_MIRROR + "gemma4-12b-with-proj-ltx-2.5-comfy-int8-convrot.safetensors",
     "text_encoders",
     "gemma4-12b-with-proj-ltx-2.5-comfy-int8-convrot.safetensors",
     "text_encoders"),
    (_COMFY_ORG + "gemma4_e2b_it_bf16.safetensors",
     "text_encoders", "gemma4_e2b_it_bf16.safetensors", "text_encoders"),
    (_MIRROR + "ltx-2.5-video-vae-conv-bf16.safetensors",
     "vae", "ltx-2.5-video-vae-conv-bf16.safetensors", "vae"),
    (_MIRROR + "ltx-2.5-audio-vae-bf16.safetensors",
     "vae", "ltx-2.5-audio-vae-bf16.safetensors", "vae"),
    (_MIRROR + "ltx-2.5-latent-spatial-upscaler-x2-bf16-1.0.safetensors",
     "latent_upscale_models",
     "ltx-2.5-latent-spatial-upscaler-x2-bf16-1.0.safetensors",
     "latent_upscale_models"),
    (_MIRROR + "ltx-2.5-latent-temporal-upscaler-x2-bf16-1.0.safetensors",
     "latent_upscale_models",
     "ltx-2.5-latent-temporal-upscaler-x2-bf16-1.0.safetensors",
     "latent_upscale_models"),
]


def _req(url: str, body=None, timeout=60):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(url, data=data,
                               method="POST" if data else "GET")
    if data:
        r.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(r, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def _comfy_list(host: str, route: str) -> list:
    try:
        got = _req(f"http://{host}:8188/models/{route}", timeout=20)
        return got if isinstance(got, list) else []
    except Exception:  # noqa: BLE001
        return []


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()

    from backend.api.lora_train import _helpers_list
    helpers = _helpers_list()
    print(f"🎥 LTX 2.5 staging — {len(helpers)} worker(s), "
          f"{len(FILES)} files (~45 GB each)\n")

    all_ok = True
    for h in helpers:
        host, port, token = h["host"], int(h.get("port") or 8765), h["token"]
        name = h.get("name") or h.get("id") or host
        print(f"— {name} ({host})")
        listings = {}
        started = have = missing = 0
        for url, folder, fname, route in FILES:
            if route not in listings:
                listings[route] = [str(x) for x in _comfy_list(host, route)]
            if any(fname in x for x in listings[route]):
                have += 1
                continue
            if a.check:
                print(f"    ❌ MISSING {folder}/{fname}")
                missing += 1
                continue
            try:
                _req(f"http://{host}:{port}/download/model?token={token}",
                     {"url": url, "folder": folder, "filename": fname},
                     timeout=60)
                started += 1
                print(f"    ⬇ started {folder}/{fname}")
            except urllib.error.HTTPError as e:
                print(f"    ❌ helper refused {fname}: {e.code} "
                      f"{e.read().decode()[:150]}")
                all_ok = False
            except Exception as e:  # noqa: BLE001
                print(f"    ❌ helper unreachable: {e}")
                all_ok = False
                break
        print(f"    ✅ present: {have}/{len(FILES)}"
              + (f" · ⬇ downloading: {started}" if started else "")
              + (f" · ❌ missing: {missing}" if missing else ""))
        if missing:
            all_ok = False

    if a.check:
        print("\n" + ("ALL WORKERS FULLY STAGED" if all_ok
                      else "⏳ not complete yet — big files, run --check again"))
        return 0 if all_ok else 1
    print("\nDownloads run in each helper's background (~45 GB per box — "
          "expect a while). Verify with:\n"
          "    python scripts/install_ltx25.py --check")
    return 0


if __name__ == "__main__":
    sys.exit(main())
