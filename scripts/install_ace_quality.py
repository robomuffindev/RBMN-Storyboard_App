"""🎚 Stage the ACE-Step 1.5 QUALITY models (v1.277.17).

The Audio Lab shipped with `ace_step_1.5_turbo_aio.safetensors` — which is the
**speed** checkpoint (ComfyUI's own turbo template: 8 steps, cfg 1) wrapped
around the small 4.8 GB DiT. Lorenzo heard the difference. The quality models
are separate downloads and a different recipe:

    acestep_v1.5_xl_base_bf16.safetensors   9.97 GB   50 steps  (the general model)
    acestep_v1.5_xl_sft_bf16.safetensors    9.97 GB   50 steps  (the song finetune)
    ⚠ we run BOTH at cfg 3, not the templates' 6/7 — see ACE_XL in audio_lab.py
    qwen_0.6b_ace15.safetensors             1.19 GB   DualCLIPLoader slot 1
    qwen_4b_ace15.safetensors               8.38 GB   DualCLIPLoader slot 2
    ace_1.5_vae.safetensors                 0.34 GB

~27.8 GiB per box for BOTH quality models (18.5 GiB for sft alone; the two DiTs
plus the VAE are ~18.9 GB of that when the qwen encoders are already staged,
which they were here). His
bandwidth rule applies: **download ONCE on the box with disk room, then LAN
peer-copy** via helper v1.220's /serve/model.

    python scripts/install_ace_quality.py --check
    python scripts/install_ace_quality.py --first 192.168.12.224     # start
    python scripts/install_ace_quality.py --fanout                   # peers
    python scripts/install_ace_quality.py --sft-only                 # skip base

⚠ Downloads run IN THE HELPER, in the background — this script returns as soon
as they are queued. Re-run with --check (or `scripts/audit_model_integrity.py`,
which compares BYTES, because a filename appearing proves nothing).
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HF = "https://huggingface.co/Comfy-Org/ace_step_1.5_ComfyUI_files/resolve/main/split_files"

#: (folder, filename, gigabytes, needed_for)
FILES = [
    ("diffusion_models", "acestep_v1.5_xl_sft_bf16.safetensors", 9.29, "sft"),
    ("diffusion_models", "acestep_v1.5_xl_base_bf16.safetensors", 9.29, "base"),
    ("text_encoders", "qwen_0.6b_ace15.safetensors", 1.11, "both"),
    ("text_encoders", "qwen_4b_ace15.safetensors", 7.80, "both"),
    ("vae", "ace_1.5_vae.safetensors", 0.31, "both"),
]


def _helpers_stdlib() -> list:
    """The worker registry WITHOUT importing the app.

    ⭐ `from backend.api.lora_train import _helpers_list` runs
    `backend/api/__init__.py`, which imports FastAPI — so a plain
    `python scripts\\…` outside the venv dies on ModuleNotFoundError. It did,
    for him, on dl_progress.py. Operator-facing scripts read the registry off
    disk instead, and only fall back to the app import if that fails."""
    import os
    import re as _re
    v = os.environ.get("PROJECT_DIR") or ""
    if not v:
        try:
            for line in (Path(__file__).resolve().parent.parent
                         / ".env").read_text("utf-8").splitlines():
                m = _re.match(r"\s*PROJECT_DIR\s*=\s*(.+?)\s*$", line, _re.I)
                if m:
                    v = m.group(1).strip().strip('"').strip("'")
                    break
        except Exception:                                      # noqa: BLE001
            pass
    fp = (Path(v or "~/RBMN-Projects").expanduser() / "_libraries" / "forge"
          / "settings.json")
    try:
        hs = json.loads(fp.read_text("utf-8")).get("helpers") or []
        if hs:
            return hs
    except Exception:                                          # noqa: BLE001
        pass
    from backend.api.lora_train import _helpers_list           # last resort
    return _helpers_list()


def _req(url, body=None, timeout=60):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(url, data=data, method="POST" if data else "GET")
    if data:
        r.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(r, timeout=timeout) as resp:
        return json.loads(resp.read().decode() or "{}")


def _has(host: str, folder: str, fname: str) -> bool:
    try:
        return any(fname in str(x) for x in
                   _req(f"http://{host}:8188/models/{folder}", timeout=20))
    except Exception:                                          # noqa: BLE001
        return False


def _free_gb(h: dict) -> float:
    """Free space where the models live — the trainer's E: filled up mid-stage
    on 2026-08-16 and every download died on 'No space left on device'."""
    try:
        # ⚠ /health is the SHALLOW probe — disk lives on /diag (and /diag wants
        # the token). Asking the wrong endpoint returns 200 and no answer.
        d = _req(f"http://{h['host']}:{h.get('port', 8765)}/diag"
                 f"?token={urllib.parse.quote(h['token'])}", timeout=30)
        disks = (d.get("disk") or {})
        vals = [v.get("free_gb") for v in disks.values()
                if isinstance(v, dict) and v.get("free_gb") is not None]
        return min(vals) if vals else -1.0
    except Exception:                                          # noqa: BLE001
        return -1.0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="report only")
    ap.add_argument("--first", default="", help="the box that downloads from HF")
    ap.add_argument("--fanout", action="store_true",
                    help="LAN peer-copy whatever a box already has to the rest")
    ap.add_argument("--sft-only", action="store_true",
                    help="skip the base model (18.5 GiB instead of 27.8)")
    a = ap.parse_args()

    helpers = _helpers_stdlib()
    if not helpers:
        print("no worker helpers configured")
        return 1
    files = [f for f in FILES if not (a.sft_only and f[3] == "base")]
    want_gb = sum(f[2] for f in files)

    print(f"ACE quality staging — {len(files)} files, ~{want_gb:.1f} GB per box\n")
    state = {}
    for h in helpers:
        host = h["host"]
        have = [f for f in files if _has(host, f[0], f[1])]
        state[host] = {f[1] for f in have}
        missing = [f for f in files if f[1] not in state[host]]
        free = _free_gb(h)
        need = sum(f[2] for f in missing)
        # ⚠ /diag reports the STATE and FIZGIG paths — NOT necessarily the
        # drive ComfyUI's models live on (the trainer's E: is separate, and E:
        # is the one that filled on 2026-08-16). Treat this as a signal.
        flag = "" if free < 0 else (
            f"  free {free:.0f} GB (state drive)"
            + ("  ⚠ NOT ENOUGH" if free < need + 5 else ""))
        print(f"— {h.get('name') or host} ({host}): "
              f"{len(have)}/{len(files)} present, {need:.1f} GB to fetch{flag}")
        for f in missing:
            print(f"    ❌ {f[0]}/{f[1]}  ({f[2]:.2f} GB)")
    if a.check:
        print("\n(check only — nothing started; byte-verify with "
              "scripts/audit_model_integrity.py)")
        return 0

    started = 0
    for h in helpers:
        host, token = h["host"], h["token"]
        port = int(h.get("port") or 8765)
        base = f"http://{host}:{port}"
        for folder, fname, gb, _k in files:
            if fname in state[host]:
                continue
            src = next((s for s in helpers
                        if s["host"] != host and fname in state[s["host"]]), None)
            if src:
                url = (f"http://{src['host']}:{src.get('port', 8765)}"
                       f"/serve/model/{folder}/{fname}"
                       f"?token={urllib.parse.quote(src['token'])}")
                how = f"LAN from {src['host']}"
            else:
                # ⭐ ONE box pulls from the internet — that is his bandwidth rule
                if a.fanout:
                    continue                     # fan-out pass: peers only
                if a.first and host != a.first:
                    continue
                url, how = f"{HF}/{folder}/{fname}", "internet"
            try:
                _req(f"{base}/download/model?token={token}",
                     {"url": url, "folder": folder, "filename": fname},
                     timeout=60)
                print(f"  ⬇ {host}: {fname} ({how})")
                started += 1
            except Exception as e:                             # noqa: BLE001
                print(f"  ❌ {host}: {fname} — {e}")

    print(f"\n{started} download(s) queued in the helpers (background).")
    print("Re-run with --check to watch, then --fanout once the first box is "
          "complete, and finish with scripts/audit_model_integrity.py "
          "(bytes, not filenames).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
