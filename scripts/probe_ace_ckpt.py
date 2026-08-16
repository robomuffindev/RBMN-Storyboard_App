"""Dump helper inventory checkpoints structure + ace sizes. HF expected 10025478736."""
import json
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from backend.api.lora_train import _helpers_list  # noqa: E402

for h in _helpers_list():
    base = f"http://{h['host']}:{int(h.get('port') or 8765)}"
    try:
        req = urllib.request.Request(f"{base}/inventory?token={h['token']}")
        d = json.load(urllib.request.urlopen(req, timeout=30))
        ck = (d.get("models") or {}).get("checkpoints")
        print(h["host"], json.dumps(ck)[:600])
    except Exception as e:
        print(h["host"], "ERR", repr(e)[:150])
