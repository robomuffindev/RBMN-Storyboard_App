import json
import sys
import urllib.parse
import urllib.request

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

H = "192.168.12.201"
for n in ("MiniMaxMusic3TextEncode", "EmptyMiniMaxMusic3LatentAudio",
          "VAEDecodeAudioTiled", "CLIPLoader", "UNETLoader"):
    try:
        d = json.load(urllib.request.urlopen(
            f"http://{H}:8188/object_info/{urllib.parse.quote(n)}", timeout=20))
        spec = d[n]["input"]
        parts = []
        for sec in ("required", "optional"):
            for k, v in (spec.get(sec) or {}).items():
                t = v[0] if isinstance(v, (list, tuple)) and v else "?"
                parts.append(f"{k}:{t if isinstance(t, str) else 'CHOICE'}"
                             + ("(opt)" if sec == "optional" else ""))
        outs = d[n].get("output") or []
        print(n, "| in:", ", ".join(parts), "| out:", outs)
    except Exception as e:
        print(n, "ERR", repr(e)[:100])
