"""Start the three big HF downloads on ZOAI1 (the box with disk room) —
the trainer's models drive is FULL, so it gets these via LAN peer-copy after
Lorenzo frees space. Usage: stage_on_zoai1.py"""
import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from backend.api.lora_train import _helpers_list  # noqa: E402

WANT = [
    ("https://huggingface.co/dummy9996/LTX-2.5-22b-ungate/resolve/main/"
     "ltx-2.5-22b-distilled-transformer-comfy-int8-convrot.safetensors",
     "diffusion_models", "ltx-2.5-22b-distilled-transformer-comfy-int8-convrot.safetensors"),
    ("https://huggingface.co/dummy9996/LTX-2.5-22b-ungate/resolve/main/"
     "gemma4-12b-with-proj-ltx-2.5-comfy-int8-convrot.safetensors",
     "text_encoders", "gemma4-12b-with-proj-ltx-2.5-comfy-int8-convrot.safetensors"),
    ("https://huggingface.co/Comfy-Org/MiniMax-Music-3/resolve/main/"
     "text_encoders/minimax_music3_text_encoder_pruned_int8_convrot.safetensors",
     "text_encoders", "minimax_music3_text_encoder_pruned_int8_convrot.safetensors"),
]

h = next(x for x in _helpers_list() if x["host"] == "192.168.12.224")
base = f"http://{h['host']}:{int(h.get('port') or 8765)}"
for url, folder, fname in WANT:
    body = json.dumps({"url": url, "folder": folder, "filename": fname}).encode()
    req = urllib.request.Request(
        f"{base}/download/model?token={urllib.parse.quote(h['token'])}",
        data=body, headers={"Content-Type": "application/json"})
    r = json.load(urllib.request.urlopen(req, timeout=60))
    print(fname, "->", r.get("id"))
