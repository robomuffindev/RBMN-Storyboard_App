"""MiniMax Music 3 first render — self-contained go/no-go.

Checks which box has the FULL text encoder (byte-size vs HF, not filename),
submits a short track through the backend's Audio Lab, polls to completion.
Safe to re-run any time; exits early with a clear message if no box is ready.

Usage: mm3_first_render.py [seconds]  (default 15)
"""
import json
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from backend.api.lora_train import _helpers_list  # noqa: E402
from scripts.audit_model_integrity import hf_size, local_size  # noqa: E402

TE_URL = ("https://huggingface.co/Comfy-Org/MiniMax-Music-3/resolve/main/"
          "text_encoders/minimax_music3_text_encoder_pruned_int8_convrot.safetensors")
TE = "minimax_music3_text_encoder_pruned_int8_convrot.safetensors"
DIT = "minimax_music3_dit_int8_convrot.safetensors"
VAE = "minimax_music3_dav.safetensors"
API = "http://127.0.0.1:8899"

want = hf_size(TE_URL)
ready = None
for h in _helpers_list():
    port, tok = int(h.get("port") or 8765), h["token"]
    try:
        te = local_size(h["host"], port, tok, "text_encoders", TE)
        dit = local_size(h["host"], port, tok, "diffusion_models", DIT)
        vae = local_size(h["host"], port, tok, "vae", VAE)
        state = ("READY" if te == want and dit > 0 and vae > 0 else
                 f"te {max(te, 0)/1e9:.2f}/{want/1e9:.2f} GB")
        print(f"{h.get('name') or h['host']}: {state}")
        if state == "READY" and not ready:
            ready = h["host"]
    except Exception as e:  # noqa: BLE001
        print(h["host"], "ERR", repr(e)[:100])

if not ready:
    print("\nNo box has the full MM3 text encoder yet - check "
          "scripts/probe_downloads.py, then re-run this.")
    sys.exit(1)

seconds = float(sys.argv[1]) if len(sys.argv) > 1 else 15.0
body = json.dumps({
    "engine": "minimax3", "host": ready, "seconds": seconds,
    # mm3 reads `tags` as its structured CAPTION
    "tags": "A slow cinematic western instrumental: baritone guitar, sparse "
            "percussion, distant harmonica, wide desert air. Dusty americana, "
            "slow burn.",
    "lyrics": "", "label": "mm3 first render",
}).encode()
req = urllib.request.Request(f"{API}/api/audio-lab/music/generate", data=body,
                             headers={"Content-Type": "application/json"})
r = json.load(urllib.request.urlopen(req, timeout=120))
jid = r.get("id")
print("submitted", jid, "on", r.get("worker"))

t0 = time.time()
while True:
    time.sleep(15)
    jobs = json.load(urllib.request.urlopen(
        f"{API}/api/audio-lab/jobs", timeout=30)).get("jobs") or []
    j = next((x for x in jobs if x.get("id") == jid), None)
    if not j:
        continue
    if j.get("status") in ("done", "error"):
        print("STATUS:", j.get("status"), "| elapsed:", j.get("elapsed_s"),
              "s | file:", j.get("file"))
        if j.get("error"):
            print("ERR:", str(j.get("error"))[:600])
        sys.exit(0 if j.get("status") == "done" else 1)
    if time.time() - t0 > 1800:
        print("TIMEOUT waiting for the render")
        sys.exit(2)
