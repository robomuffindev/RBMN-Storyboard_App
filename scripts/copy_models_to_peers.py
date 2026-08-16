"""📦 Copy a model from ONE worker to the rest — LAN speed, not WAN thirds.

His observation (2026-08-15): three boxes each downloading a 21 GB file split
the internet connection three ways and take 3× as long. The right shape is
DOWNLOAD ONCE on one box, then peers pull it over the local network at wire
speed. Helper v1.220 adds `GET /serve/model/{folder}/{filename}` for exactly
this; each peer's own `/download/model` (background, resumable via /downloads)
does the pulling.

    # copy a file that exists on the trainer to every other box:
    python scripts/copy_models_to_peers.py --folder diffusion_models \\
        --filename ltx-2.5-22b-distilled-transformer-comfy-int8-convrot.safetensors

    # pick a different source box / copy several files:
    python scripts/copy_models_to_peers.py --source 192.168.12.163 \\
        --folder vae --filename a.safetensors --filename b.safetensors

⚠ Requires helper v1.220+ ON THE SOURCE box (peers only need /download/model,
which every helper has). Update helpers by copying scripts/worker/rbmn_helper.py
over D:\\RBMNHelper\\rbmn_helper.py on each box and restarting its bat.
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


def _req(url: str, body=None, timeout=60):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(url, data=data,
                               method="POST" if data else "GET")
    if data:
        r.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(r, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default="", help="source box host (default: the trainer)")
    ap.add_argument("--folder", required=True)
    ap.add_argument("--filename", action="append", required=True)
    a = ap.parse_args()

    from backend.api.lora_train import _helpers_list
    helpers = _helpers_list()
    src = next((h for h in helpers
                if h["host"] == a.source or (not a.source and h.get("is_trainer"))),
               None)
    if not src:
        print(f"❌ source box {a.source or '(trainer)'} not in the helper registry")
        return 1
    peers = [h for h in helpers if h["host"] != src["host"]]
    print(f"📦 source: {src.get('name')} ({src['host']}) → {len(peers)} peer(s)")

    # source helper must have /serve/model (v1.220+)
    try:
        h = _req(f"http://{src['host']}:{src.get('port', 8765)}/health", timeout=15)
        if str(h.get("helper", "0")) < "1.220":
            print(f"❌ source helper is v{h.get('helper')} — needs v1.220+ "
                  f"(copy scripts/worker/rbmn_helper.py over its install and restart)")
            return 1
    except Exception as e:  # noqa: BLE001
        print(f"❌ source helper unreachable: {e}")
        return 1

    ok = True
    for fname in a.filename:
        serve = (f"http://{src['host']}:{src.get('port', 8765)}/serve/model/"
                 f"{urllib.parse.quote(a.folder)}/{urllib.parse.quote(fname)}"
                 f"?token={urllib.parse.quote(src['token'])}")
        for peer in peers:
            base = f"http://{peer['host']}:{peer.get('port', 8765)}"
            try:
                r = _req(f"{base}/download/model?token={peer['token']}",
                         {"url": serve, "folder": a.folder, "filename": fname},
                         timeout=60)
                print(f"  ⬇ {peer.get('name')} ← {fname}: started "
                      f"({r.get('id')})")
            except Exception as e:  # noqa: BLE001
                print(f"  ❌ {peer.get('name')} ← {fname}: {e}")
                ok = False
    print("\nPeers pull in the background — watch each box's /downloads, or "
          "re-run the relevant install script with --check.")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
