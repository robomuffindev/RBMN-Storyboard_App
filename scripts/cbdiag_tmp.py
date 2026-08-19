import sys, json, time, urllib.request
try: sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception: pass
from pathlib import Path
HOST="192.168.12.163"
st=json.loads((Path.home()/"RBMN-Projects"/"_libraries"/"forge"/"settings.json").read_text("utf-8"))
tok=next(h["token"] for h in st["helpers"] if h["host"]==HOST)
def post(p,b=None,t=300):
    d=json.dumps(b or {}).encode()
    r=urllib.request.Request(f"http://{HOST}:8765{p}?token={tok}",data=d,method="POST")
    r.add_header("Content-Type","application/json")
    with urllib.request.urlopen(r,timeout=t) as x: return json.loads(x.read().decode())
post("/comfy/stop"); time.sleep(6); post("/comfy/start")
for i in range(60):
    time.sleep(10)
    try:
        with urllib.request.urlopen(f"http://{HOST}:8188/object_info",timeout=30) as x:
            oi=json.loads(x.read().decode())
        print(f"ComfyUI back: {len(oi)} nodes"); break
    except Exception: print(f"  waiting {i*10+10}s")
else:
    print("DID NOT COME BACK"); sys.exit(1)
