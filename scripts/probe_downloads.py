"""List every helper's /downloads — dest, status, bytes/total."""
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
    try:
        req = urllib.request.Request(f"{base}/downloads?token={h['token']}")
        d = json.load(urllib.request.urlopen(req, timeout=30))
        print(f"- {h.get('name') or h['host']} ({h['host']})")
        for dl in d.get("downloads", []):
            dest = str(dl.get("dest") or "").replace("\\", "/").rsplit("/", 1)[-1]
            b, t = dl.get("bytes") or 0, dl.get("total")
            pct = f" {100*b/t:.0f}%" if t else ""
            err = f" ERR={dl.get('error')}" if dl.get("error") else ""
            print(f"    {dl.get('id')} {dl.get('status'):>7}{pct} "
                  f"{b/1e9:.2f}/{(t or 0)/1e9:.2f}GB {dest}{err}")
    except Exception as e:
        print(h["host"], "ERR", repr(e)[:150])
