"""First live LTX 2.5 render — small t2v straight to a worker, no backend.

Usage: ltx25_live_test.py [host] [seconds] [width] [height]
"""
import json
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from backend.services.jobs.ltx25_graphs import build_ltx25_graph  # noqa: E402

HOST = sys.argv[1] if len(sys.argv) > 1 else "192.168.12.201"
SECONDS = float(sys.argv[2]) if len(sys.argv) > 2 else 2.0
W = int(sys.argv[3]) if len(sys.argv) > 3 else 768
H = int(sys.argv[4]) if len(sys.argv) > 4 else 448

PROMPT = ("A weathered cowboy on horseback rides slowly down the dusty main "
          "street of a frontier town at golden hour, wooden storefronts on "
          "both sides, tumbleweed drifting past. The camera tracks alongside "
          "him at a steady pace. Warm cinematic light, gentle wind, creaking "
          "leather and distant saloon piano.")

g = build_ltx25_graph("t2v", PROMPT, width=W, height=H, seconds=SECONDS,
                      fps=24, seed=123456789,
                      filename_prefix="video/LTX25_LIVETEST")
body = json.dumps({"prompt": g}).encode()
req = urllib.request.Request(f"http://{HOST}:8188/prompt", data=body,
                             headers={"Content-Type": "application/json"})
try:
    r = json.load(urllib.request.urlopen(req, timeout=60))
except urllib.error.HTTPError as e:
    print("SUBMIT FAILED:", e.code)
    print(e.read().decode("utf-8", "replace")[:2000])
    sys.exit(1)
pid = r.get("prompt_id")
print("submitted", pid, "to", HOST, f"({W}x{H}, {SECONDS}s)")

t0 = time.time()
while True:
    time.sleep(10)
    try:
        h = json.load(urllib.request.urlopen(
            f"http://{HOST}:8188/history/{pid}", timeout=30))
    except Exception as e:
        print("poll err", repr(e)[:100])
        continue
    if pid not in h:
        el = time.time() - t0
        print(f"  … running {el:.0f}s")
        if el > 3000:
            print("TIMEOUT")
            sys.exit(2)
        continue
    st = h[pid].get("status") or {}
    outs = h[pid].get("outputs") or {}
    el = time.time() - t0
    if st.get("status_str") == "error":
        print(f"ERROR after {el:.1f}s")
        for m in st.get("messages") or []:
            if m and m[0] == "execution_error":
                d = m[1]
                print("node", d.get("node_id"), d.get("node_type"), "→",
                      str(d.get("exception_message"))[:500])
        sys.exit(1)
    files = []
    for o in outs.values():
        for k in ("images", "video", "gifs"):
            for f in o.get(k) or []:
                files.append(f"{f.get('subfolder','')}/{f.get('filename')}")
    print(f"DONE in {el:.1f}s → {files}")
    break
