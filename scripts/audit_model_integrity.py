"""Byte-size integrity audit for recently staged models, fleet-wide.

Filename presence lies: an interrupted helper download can promote a TRUNCATED
file and report "done" (the pre-v1.221 helper never compared bytes to
Content-Length). This audits ACTUAL local size (helper /serve/model's
Content-Length header, i.e. a stat) against the HF original's size.

Usage:
  audit_model_integrity.py            audit only
  audit_model_integrity.py --fix      audit + start repairs (peer copy when a
                                      good copy exists on any box, else HF
                                      download on the FIRST box only)
"""
from __future__ import annotations

import http.client
import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from scripts.install_ltx25 import FILES as LTX25_FILES  # noqa: E402

#: (hf_url, models subfolder, filename)
TARGETS = [
    ("https://huggingface.co/Comfy-Org/ace_step_1.5_ComfyUI_files/resolve/main/"
     "checkpoints/ace_step_1.5_turbo_aio.safetensors",
     "checkpoints", "ace_step_1.5_turbo_aio.safetensors"),
    ("https://huggingface.co/Comfy-Org/MiniMax-Music-3/resolve/main/"
     "diffusion_models/minimax_music3_dit_int8_convrot.safetensors",
     "diffusion_models", "minimax_music3_dit_int8_convrot.safetensors"),
    ("https://huggingface.co/Comfy-Org/MiniMax-Music-3/resolve/main/"
     "text_encoders/minimax_music3_text_encoder_pruned_int8_convrot.safetensors",
     "text_encoders", "minimax_music3_text_encoder_pruned_int8_convrot.safetensors"),
    ("https://huggingface.co/Comfy-Org/MiniMax-Music-3/resolve/main/"
     "vae/minimax_music3_dav.safetensors", "vae", "minimax_music3_dav.safetensors"),
] + [
    # 🎚 v1.277.17 — the ACE-Step 1.5 QUALITY lane (split files; the AIO
    # checkpoint above is the TURBO/speed model). Staged by
    # scripts/install_ace_quality.py.
    ("https://huggingface.co/Comfy-Org/ace_step_1.5_ComfyUI_files/resolve/main/"
     "split_files/diffusion_models/acestep_v1.5_xl_sft_bf16.safetensors",
     "diffusion_models", "acestep_v1.5_xl_sft_bf16.safetensors"),
    ("https://huggingface.co/Comfy-Org/ace_step_1.5_ComfyUI_files/resolve/main/"
     "split_files/diffusion_models/acestep_v1.5_xl_base_bf16.safetensors",
     "diffusion_models", "acestep_v1.5_xl_base_bf16.safetensors"),
    ("https://huggingface.co/Comfy-Org/ace_step_1.5_ComfyUI_files/resolve/main/"
     "split_files/text_encoders/qwen_0.6b_ace15.safetensors",
     "text_encoders", "qwen_0.6b_ace15.safetensors"),
    ("https://huggingface.co/Comfy-Org/ace_step_1.5_ComfyUI_files/resolve/main/"
     "split_files/text_encoders/qwen_4b_ace15.safetensors",
     "text_encoders", "qwen_4b_ace15.safetensors"),
    ("https://huggingface.co/Comfy-Org/ace_step_1.5_ComfyUI_files/resolve/main/"
     "split_files/vae/ace_1.5_vae.safetensors",
     "vae", "ace_1.5_vae.safetensors"),
] + [(u, folder, fname) for (u, folder, fname, _route) in LTX25_FILES]


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


def hf_size(url: str) -> int:
    req = urllib.request.Request(url, method="HEAD")
    req.add_header("User-Agent", "rbmn-audit")
    try:
        r = urllib.request.urlopen(req, timeout=45)
        return int(r.headers.get("x-linked-size")
                   or r.headers.get("Content-Length") or 0)
    except urllib.error.HTTPError as e:
        # 302 handled by urllib; 401/403 on HEAD of LFS -> try GET range 0-0
        if e.code in (401, 403, 405):
            req2 = urllib.request.Request(url, headers={
                "Range": "bytes=0-0", "User-Agent": "rbmn-audit"})
            r2 = urllib.request.urlopen(req2, timeout=45)
            cr = r2.headers.get("Content-Range") or ""
            if "/" in cr:
                return int(cr.split("/")[-1])
        raise


def local_size(host: str, port: int, token: str, folder: str, fname: str) -> int:
    """HEAD-style probe of /serve/model: read headers, close before the body."""
    c = http.client.HTTPConnection(host, port, timeout=30)
    try:
        path = (f"/serve/model/{urllib.parse.quote(folder)}/"
                f"{urllib.parse.quote(fname)}?token={urllib.parse.quote(token)}")
        c.request("GET", path)
        r = c.getresponse()
        if r.status == 404:
            return -1
        if r.status != 200:
            raise RuntimeError(f"HTTP {r.status}")
        return int(r.headers.get("Content-Length") or 0)
    finally:
        c.close()


def start_hf_download(base: str, token: str, url: str, folder: str, fname: str):
    body = json.dumps({"url": url, "folder": folder, "filename": fname}).encode()
    req = urllib.request.Request(
        f"{base}/download/model?token={urllib.parse.quote(token)}",
        data=body, headers={"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req, timeout=60))


def main():
    fix = "--fix" in sys.argv
    helpers = _helpers_stdlib()
    bad = ok_all = 0
    repairs = []
    for url, folder, fname in TARGETS:
        try:
            want = hf_size(url)
        except Exception as e:  # noqa: BLE001
            print(f"?? {folder}/{fname}: HF size unknown ({repr(e)[:80]})")
            continue
        states = []
        for h in helpers:
            port, tok = int(h.get("port") or 8765), h["token"]
            try:
                got = local_size(h["host"], port, tok, folder, fname)
            except Exception as e:  # noqa: BLE001
                states.append((h, None, f"ERR {repr(e)[:60]}"))
                continue
            if got == -1:
                states.append((h, -1, "MISSING"))
            elif got == want:
                states.append((h, got, "OK"))
            else:
                states.append((h, got, f"TRUNCATED {got/1e9:.2f}/{want/1e9:.2f} GB"))
        line = ", ".join(f"{(h.get('name') or h['host'])}:{s[-1] if isinstance(s, tuple) else s}"
                         for (h, _g, s) in states)
        good = [h for (h, g, s) in states if s == "OK"]
        broken = [(h, s) for (h, g, s) in states if s != "OK" and not s.startswith("ERR")]
        flag = "✅" if not broken else "❌"
        print(f"{flag} {folder}/{fname} ({want/1e9:.2f} GB) — {line}")
        if broken:
            bad += len(broken)
            if fix:
                for h, _s in broken:
                    base = f"http://{h['host']}:{int(h.get('port') or 8765)}"
                    if good:
                        src = good[0]
                        peer = (f"http://{src['host']}:{int(src.get('port') or 8765)}"
                                f"/serve/model/{urllib.parse.quote(folder)}/"
                                f"{urllib.parse.quote(fname)}"
                                f"?token={urllib.parse.quote(src['token'])}")
                        r = start_hf_download(base, h["token"], peer, folder, fname)
                        repairs.append((h["host"], fname,
                                        f"peer←{src['host']} id={r.get('id')}"))
                    elif h is broken[0][0]:
                        r = start_hf_download(base, h["token"], url, folder, fname)
                        repairs.append((h["host"], fname, f"HF id={r.get('id')}"))
                    else:
                        repairs.append((h["host"], fname,
                                        "WAIT (no good copy; HF started on one box"
                                        " — re-run --fix after it lands)"))
        else:
            ok_all += 1
    print(f"\n{ok_all}/{len(TARGETS)} files clean everywhere; "
          f"{bad} bad copies" + (" — repairs:" if repairs else ""))
    for host, fname, how in repairs:
        print(f"  🔧 {host} {fname}: {how}")


if __name__ == "__main__":
    main()
