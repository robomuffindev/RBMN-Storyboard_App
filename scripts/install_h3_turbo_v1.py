"""🎬 Install the MiniMax H3 Turbo LoRA v1.0 (8-step) on EVERY worker.

Why: the app shipped with `minimax_h3_turbo_4step_ckpt500_comfyui_pruned` — an
early v0.1-era 4-step preview checkpoint — while the turbo path samples at 8
steps. On 2026-08-11 Lightx2v/ModelTC released v1.0 with a checkpoint
DISTILLED AT 8 NFE, made for exactly our step count:

    minimax_h3_fl2v_turbo_8step_v1.0_comfyui_bf16.safetensors
    https://huggingface.co/lightx2v/Minimax-h3-Turbo

This drives each helper's /download/model (background) and watches /downloads
until every box has the file, then verifies it in ComfyUI's own /models/loras
listing — "downloaded" is the helper's claim, the worker's listing is the
proof. Idempotent: boxes that already have the file are skipped.

    python scripts/install_h3_turbo_v1.py           # install + verify
    python scripts/install_h3_turbo_v1.py --check   # verify only, no download
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")   # type: ignore[union-attr]
except Exception:  # noqa: BLE001
    pass

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

LORA_FILE = "minimax_h3_fl2v_turbo_8step_v1.0_comfyui_bf16.safetensors"
LORA_URL = ("https://huggingface.co/lightx2v/Minimax-h3-Turbo/resolve/main/"
            + LORA_FILE)


def _req(url: str, body=None, timeout=60):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(url, data=data,
                               method="POST" if data else "GET")
    if data:
        r.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(r, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def _comfy_has_lora(host: str) -> bool:
    try:
        loras = _req(f"http://{host}:8188/models/loras", timeout=20)
        return any(LORA_FILE in str(x) for x in loras)
    except Exception as e:  # noqa: BLE001
        print(f"    ⚠ could not list loras on {host}:8188: {e}")
        return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()

    from backend.api.lora_train import _helpers_list
    helpers = _helpers_list()
    print(f"🎬 H3 Turbo LoRA v1.0 (8-step) — {len(helpers)} worker(s)\n"
          f"   file: {LORA_FILE}\n")

    ok = True
    for h in helpers:
        host, port, token = h["host"], int(h.get("port") or 8765), h["token"]
        name = h.get("name") or h.get("id") or host
        print(f"— {name} ({host})")
        if _comfy_has_lora(host):
            print("    ✅ already present (ComfyUI lists it)")
            continue
        if a.check:
            print("    ❌ MISSING")
            ok = False
            continue
        base = f"http://{host}:{port}"
        try:
            r = _req(f"{base}/download/model?token={token}",
                     {"url": LORA_URL, "folder": "loras",
                      "filename": LORA_FILE}, timeout=60)
            print(f"    ⬇ download started: {json.dumps(r)[:120]}")
        except urllib.error.HTTPError as e:
            print(f"    ❌ helper refused: {e.code} {e.read().decode()[:200]}")
            ok = False
            continue
        except Exception as e:  # noqa: BLE001
            print(f"    ❌ helper unreachable: {e}")
            ok = False
            continue
        # watch /downloads until it lands (bf16 lora ≈ a few hundred MB)
        done = False
        for _ in range(240):                      # up to 20 min
            time.sleep(5)
            try:
                ds = _req(f"{base}/downloads?token={token}", timeout=30)
            except Exception:  # noqa: BLE001
                continue
            rows = ds if isinstance(ds, list) else \
                (ds.get("downloads") or ds.get("jobs") or [])
            mine = [d for d in rows if LORA_FILE in json.dumps(d)]
            if mine:
                st = mine[-1]
                s = str(st.get("status") or st.get("state") or "?")
                if s in ("done", "complete", "completed", "finished"):
                    done = True
                    break
                if s in ("error", "failed"):
                    print(f"    ❌ download failed: "
                          f"{str(st.get('error'))[:200]}")
                    break
                pct = st.get("pct") or st.get("progress") or ""
                print(f"    … {s} {pct}")
        # the PROOF is the worker's own listing, not the helper's claim
        if done or _comfy_has_lora(host):
            if _comfy_has_lora(host):
                print("    ✅ installed — ComfyUI lists it")
            else:
                print("    ⚠ helper says done but ComfyUI does not list it "
                      "yet (may need a ComfyUI restart to rescan)")
        else:
            print("    ❌ not installed")
            ok = False

    print("\n" + ("ALL WORKERS READY" if ok else "❌ SOME WORKERS MISSING IT"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
