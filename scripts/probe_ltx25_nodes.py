"""LTX-related node classes + staged 2.5 models on the trainer."""
import json
import urllib.request

H = "192.168.12.201"
d = json.load(urllib.request.urlopen(f"http://{H}:8188/object_info", timeout=60))
names = sorted(n for n in d if "ltx" in n.lower() or "LTX" in n)
print("LTX NODES:", len(names))
for n in names:
    print(" ", n)

for route in ("diffusion_models", "text_encoders", "vae", "checkpoints",
              "latent_upscale_models", "upscale_models"):
    try:
        got = json.load(urllib.request.urlopen(
            f"http://{H}:8188/models/{route}", timeout=15))
        hits = [g for g in got if "ltx" in str(g).lower() or "gemma" in str(g).lower()]
        if hits:
            print(route, "→", hits)
    except Exception as e:
        print(route, "ERR", repr(e)[:80])
