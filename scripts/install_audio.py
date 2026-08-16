"""🎧 Stage the Audio Lab on the fleet — ACE-Step 1.5 model + F5-TTS node.

    python scripts/install_audio.py            # install everything missing
    python scripts/install_audio.py --check    # report only

What it does, per worker:
  * ACE-Step 1.5 XL turbo AIO checkpoint (~7 GB) → models/checkpoints/
    (downloads via the helper; if another box already HAS it, pulls from that
    box over the LAN via helper v1.220's /serve/model — download once, copy
    across, his bandwidth rule.)
  * ComfyUI-F5-TTS custom node (github: niknah/ComfyUI-F5-TTS) via the
    helper's /install/node + its pip requirements. F5-TTS model weights
    auto-download on the node's first use. ⚠ ComfyUI needs a RESTART after a
    node install: POST helper /comfy/stop then /comfy/start (this script asks).
  * MiniMax Music 3 files are NOT staged by default (int8 DiT ~? GB) — pass
    --minimax3 to stage them too; the engine stays auto-detected either way.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")   # type: ignore[union-attr]
except Exception:  # noqa: BLE001
    pass

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

ACE_URL = ("https://huggingface.co/Comfy-Org/ace_step_1.5_ComfyUI_files/"
           "resolve/main/checkpoints/ace_step_1.5_turbo_aio.safetensors")
ACE_FILE = "ace_step_1.5_turbo_aio.safetensors"
F5_GIT = "https://github.com/niknah/ComfyUI-F5-TTS"
MM3 = [
    ("https://huggingface.co/Comfy-Org/MiniMax-Music-3/resolve/main/"
     "diffusion_models/minimax_music3_dit_int8_convrot.safetensors",
     "diffusion_models", "minimax_music3_dit_int8_convrot.safetensors"),
    ("https://huggingface.co/Comfy-Org/MiniMax-Music-3/resolve/main/"
     "text_encoders/minimax_music3_text_encoder_pruned_int8_convrot.safetensors",
     "text_encoders", "minimax_music3_text_encoder_pruned_int8_convrot.safetensors"),
    ("https://huggingface.co/Comfy-Org/MiniMax-Music-3/resolve/main/"
     "vae/minimax_music3_dav.safetensors", "vae", "minimax_music3_dav.safetensors"),
]


def _req(url, body=None, timeout=60):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(url, data=data, method="POST" if data else "GET")
    if data:
        r.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(r, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def _comfy_has(host, route, fname):
    try:
        return any(fname in str(x) for x in
                   _req(f"http://{host}:8188/models/{route}", timeout=15))
    except Exception:  # noqa: BLE001
        return False


def _node_present(host, node):
    try:
        d = _req(f"http://{host}:8188/object_info/{urllib.parse.quote(node)}",
                 timeout=15)
        return bool(d.get(node))
    except Exception:  # noqa: BLE001
        return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--minimax3", action="store_true")
    a = ap.parse_args()

    from backend.api.lora_train import _helpers_list
    helpers = _helpers_list()
    have_ace = [h for h in helpers if _comfy_has(h["host"], "checkpoints", ACE_FILE)]
    ok = True

    for h in helpers:
        host, port, token = h["host"], int(h.get("port") or 8765), h["token"]
        name = h.get("name") or host
        print(f"— {name} ({host})")
        base = f"http://{host}:{port}"

        # ACE checkpoint: LAN peer-copy when possible
        if _comfy_has(host, "checkpoints", ACE_FILE):
            print("    ✅ ACE-Step 1.5 AIO present")
        elif a.check:
            print("    ❌ ACE-Step 1.5 AIO missing")
            ok = False
        else:
            src = next((s for s in have_ace if s["host"] != host), None)
            if src:
                url = (f"http://{src['host']}:{src.get('port', 8765)}"
                       f"/serve/model/checkpoints/{ACE_FILE}"
                       f"?token={urllib.parse.quote(src['token'])}")
                how = f"LAN peer-copy from {src['host']}"
            else:
                url, how = ACE_URL, "internet"
            try:
                _req(f"{base}/download/model?token={token}",
                     {"url": url, "folder": "checkpoints",
                      "filename": ACE_FILE}, timeout=60)
                print(f"    ⬇ ACE-Step download started ({how})")
            except Exception as e:  # noqa: BLE001
                print(f"    ❌ ACE download failed: {e}")
                ok = False

        # F5-TTS node
        if _node_present(host, "F5TTSAudio") or _node_present(host, "F5TTSAudioInputs"):
            print("    ✅ F5-TTS node present")
        elif a.check:
            print("    ❌ F5-TTS node missing")
            ok = False
        else:
            try:
                r = _req(f"{base}/install/node?token={token}",
                         {"git_url": F5_GIT}, timeout=600)
                print(f"    📦 F5-TTS node installed: {json.dumps(r)[:120]}")
                print("    ↻ restart ComfyUI on this box to load it "
                      "(helper /comfy/stop + /comfy/start, or the app's "
                      "Settings → worker row)")
            except Exception as e:  # noqa: BLE001
                print(f"    ❌ node install failed: {e}")
                ok = False

        if a.minimax3:
            for url, folder, fname in MM3:
                if _comfy_has(host, folder, fname):
                    print(f"    ✅ {fname} present")
                elif not a.check:
                    try:
                        _req(f"{base}/download/model?token={token}",
                             {"url": url, "folder": folder, "filename": fname},
                             timeout=60)
                        print(f"    ⬇ {fname} started")
                    except Exception as e:  # noqa: BLE001
                        print(f"    ❌ {fname}: {e}")
                        ok = False

    print("\n" + ("READY (or downloads running — re-run --check)"
                  if ok else "❌ some steps failed"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
