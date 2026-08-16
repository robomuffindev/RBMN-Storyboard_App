"""Disk + .part-orphan report for every worker, via helper /diag + /inventory."""
import json
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from backend.api.lora_train import _helpers_list  # noqa: E402

for h in _helpers_list():
    base = f"http://{h['host']}:{int(h.get('port') or 8765)}"
    tok = h["token"]
    print(f"- {h.get('name') or h['host']} ({h['host']})")
    for ep in ("/diag", "/inventory", "/config"):
        try:
            d = json.load(urllib.request.urlopen(
                f"{base}{ep}?token={tok}", timeout=30))
            s = json.dumps(d)
            # surface anything that smells like disk info
            for key in ("disk", "free", "drive", "space", "comfy_root", "comfy_dir"):
                def find(o, path=""):
                    if isinstance(o, dict):
                        for k, v in o.items():
                            if key in str(k).lower() and not isinstance(v, (dict, list)):
                                print(f"    {ep}{path}/{k} = {str(v)[:100]}")
                            find(v, f"{path}/{k}")
                find(d)
            break_after = ep == "/diag"
        except Exception as e:
            print(f"    {ep} ERR {repr(e)[:100]}")
