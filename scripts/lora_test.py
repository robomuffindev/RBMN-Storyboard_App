"""Krea 2 TURBO test grid for a trained character LoRA. ArcFace-scored.

The retrained dorian LoRA has only ever been judged on Fizgig's RAW-model
previews. This runs the question that matters on the box it will be used on:
TURBO inference, the exported checkpoint, six renders that isolate one variable
each, scored with the same ArcFace path the dataset QC uses.

    key               prompt            LoRA      what it measures
    trig_default_10   trigger, plain    1.0       likeness + default wardrobe
    trig_default_08   trigger, plain    0.8       likeness cost of backing off
    trig_suit_10      trigger, suit     1.0       is wardrobe controllable?
    trig_suit_08      trigger, suit     0.8       ditto, lighter grip
    notrig_10         NO trigger        1.0       does identity leak w/o trigger?
    control_nolora    trigger, plain    none      base model = the floor

Same seed everywhere, so variants differ only by their variable.
All HTTP from this machine: helper starts ComfyUI, ComfyUI renders, app API
serves the character references. Images land in scripts/_diag/lora_test/.

RUN (via agent):  {"kind":"script","file":"lora_test.py"}
"""
from __future__ import annotations

import copy
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "_diag" / "lora_test"

APP = "http://127.0.0.1:8899"
HELPER = "http://192.168.12.202:8765"
from helper_token import helper_token as _helper_token  # v1.276.4: token out of source
TOKEN = _helper_token()
COMFY = "http://192.168.12.202:8188"
LORA = "dorian-v1-b1966f-000016.safetensors"
CHAR = "dorian"
SEED = 42
WF = ROOT.parent / "workflows" / "KREA2_TURBO_T2I.json"

P_TRIG = ("rbmndorianv man stands facing the camera, upper body from the waist "
          "up, in a plain gray studio, soft even daylight.")
P_SUIT = ("rbmndorianv man wearing a navy blue suit jacket over a white dress "
          "shirt, standing facing the camera, upper body from the waist up, in "
          "a plain gray studio, soft even daylight.")
P_NOTRIG = ("a man stands facing the camera, upper body from the waist up, in "
            "a plain gray studio, soft even daylight.")

GRID = [
    ("trig_default_10", P_TRIG, 1.0),
    ("trig_default_08", P_TRIG, 0.8),
    ("trig_suit_10", P_SUIT, 1.0),
    ("trig_suit_08", P_SUIT, 0.8),
    ("notrig_10", P_NOTRIG, 1.0),
    ("control_nolora", P_TRIG, None),
]


def jget(url, timeout=60.0):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def jpost(url, body, timeout=120.0):
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"),
                                 method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def bget(url, timeout=300.0):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return r.read()


def comfy_up(timeout=5.0) -> bool:
    try:
        jget(f"{COMFY}/system_stats", timeout=timeout)
        return True
    except Exception:  # noqa: BLE001
        return False


def model_list(folder: str) -> list[str]:
    try:
        out = jget(f"{COMFY}/models/{folder}", timeout=15)
        return out if isinstance(out, list) else []
    except Exception:  # noqa: BLE001
        return []


def main() -> int:
    global HELPER, COMFY, LORA, CHAR, GRID, OUT
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="192.168.12.202", help="training box IP (DHCP moves it)")
    ap.add_argument("--lora", default=LORA)
    ap.add_argument("--char", default=CHAR)
    ap.add_argument("--trigger", default="rbmndorianv")
    ap.add_argument("--cls", default="man")
    a = ap.parse_args()
    HELPER = f"http://{a.host}:8765"
    COMFY = f"http://{a.host}:8188"
    LORA, CHAR = a.lora, a.char
    OUT = ROOT / "_diag" / f"lora_test_{CHAR}"
    p_trig = (f"{a.trigger} {a.cls} stands facing the camera, upper body from the "
              f"waist up, in a plain gray studio, soft even daylight.")
    p_suit = (f"{a.trigger} {a.cls} wearing a navy blue business suit over a white "
              f"shirt, standing facing the camera, upper body from the waist up, in "
              f"a plain gray studio, soft even daylight.")
    p_notrig = (f"a {a.cls} stands facing the camera, upper body from the waist up, "
                f"in a plain gray studio, soft even daylight.")
    GRID = [("trig_default_10", p_trig, 1.0), ("trig_default_08", p_trig, 0.8),
            ("trig_suit_10", p_suit, 1.0), ("trig_suit_08", p_suit, 0.8),
            ("notrig_10", p_notrig, 1.0), ("control_nolora", p_trig, None)]

    # ── 0. ComfyUI up? ───────────────────────────────────────────────────
    if not comfy_up():
        print("ComfyUI not listening; asking the helper to start it...")
        try:
            res = jpost(f"{HELPER}/comfy/start?token={TOKEN}", {})
            print(f"  helper: {json.dumps(res)[:300]}")
        except Exception as e:  # noqa: BLE001
            print(f"  helper /comfy/start failed: {e}")
        t0 = time.time()
        while time.time() - t0 < 600:
            if comfy_up():
                break
            time.sleep(5)
    if not comfy_up():
        print("FAIL: ComfyUI on :8188 is not reachable FROM THIS MACHINE. "
              "If it is running on the box, it is bound to localhost - start it "
              "with run_nvidia_gpu-LTX2-16GB.bat (the network one) and re-run.")
        return 1
    print("ComfyUI is up.")

    # ── 1. preflight: are the Krea2 pieces actually installed there? ────
    wf = json.loads(WF.read_text(encoding="utf-8"))
    missing = []
    loras = model_list("loras")
    if loras and LORA not in loras:
        missing.append(f"loras/{LORA}")
    unets = model_list("diffusion_models") or model_list("unet")
    want_unet = wf["54"]["inputs"]["unet_name"]
    if unets:
        turbo = [u for u in unets if "krea2" in u.lower() and "turbo" in u.lower()]
        # This box is an RTX 4060 Ti: mxfp8 is the Blackwell (50xx) format,
        # fp8 is the one that runs here. Prefer it whenever it exists.
        fp8 = [u for u in turbo if "fp8" in u.lower() and "mxfp8" not in u.lower()]
        if fp8:
            if want_unet not in fp8:
                print(f"  40xx box: using {fp8[0]} instead of {want_unet}")
            wf["54"]["inputs"]["unet_name"] = fp8[0]
        elif want_unet in unets:
            pass
        elif turbo:
            print(f"  unet {want_unet} absent; using {turbo[0]}")
            wf["54"]["inputs"]["unet_name"] = turbo[0]
        else:
            missing.append(f"diffusion_models/{want_unet} (no krea2_turbo* at all)")
    clips = model_list("text_encoders") or model_list("clip")
    want_clip = wf["53"]["inputs"]["clip_name"]
    if clips and want_clip not in clips:
        alt = [c for c in clips if "qwen3vl" in c.lower()]
        if alt:
            print(f"  clip {want_clip} absent; using {alt[0]}")
            wf["53"]["inputs"]["clip_name"] = alt[0]
        else:
            missing.append(f"text_encoders/{want_clip} (no qwen3vl* at all)")
    vaes = model_list("vae")
    want_vae = wf["40"]["inputs"]["vae_name"]
    if vaes and want_vae not in vaes:
        missing.append(f"vae/{want_vae}")
    if missing:
        print("FAIL: this ComfyUI is missing, by its own model list:")
        for m in missing:
            print(f"  - {m}")
        print("Copy the missing files into E:\\ComfyMaster\\V1\\"
              "ComfyUI_windows_portable\\ComfyUI\\models\\<folder> and re-run.")
        return 1
    print(f"preflight OK  (lora listed: {LORA in loras if loras else 'unknown'})")

    # ── 2. build + submit the grid ───────────────────────────────────────
    jobs = []
    for key, prompt, strength in GRID:
        # v1.266.1: this box's ComfyUI has none of the decorator custom
        # nodes (RBG seed variance, rgthree, KJ sharpen) -- their absence is
        # what the first submit measured. Strip to CORE nodes: same models,
        # same sampler settings, and the strip is identical across variants,
        # so within-grid comparisons are untouched.
        g = copy.deepcopy(wf)
        g["78:15"]["inputs"]["text"] = prompt          # direct, no Any Switch
        for dead in ("143", "78:72", "63", "82", "141"):
            g.pop(dead, None)
        g["78:75"]["inputs"]["positive"] = ["78:15", 0]  # was RBG variance
        g["12"]["inputs"]["images"] = ["78:74", 0]       # was ImageSharpenKJ
        g["78:76"]["inputs"].update(width=1024, height=1536, batch_size=1)
        g["78:75"]["inputs"]["seed"] = SEED
        g["12"]["inputs"]["filename_prefix"] = f"LORATEST_{key}"
        if strength is not None:
            g["200"] = {"inputs": {"lora_name": LORA,
                                   "strength_model": strength,
                                   "model": ["54", 0]},
                        "class_type": "LoraLoaderModelOnly",
                        "_meta": {"title": "LoRA under test"}}
            g["78:75"]["inputs"]["model"] = ["200", 0]
        else:
            g["78:75"]["inputs"]["model"] = ["54", 0]
        try:
            r = jpost(f"{COMFY}/prompt", {"prompt": g})
            pid = r.get("prompt_id")
            if not pid:
                print(f"  {key}: REJECTED: {json.dumps(r)[:500]}")
                continue
            jobs.append((key, pid))
            print(f"  queued {key}  ({pid})")
        except urllib.error.HTTPError as e:
            print(f"  {key}: HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:500]}")
    if not jobs:
        print("FAIL: nothing queued.")
        return 1

    # ── 3. wait + download ───────────────────────────────────────────────
    OUT.mkdir(parents=True, exist_ok=True)
    files = {}
    t0 = time.time()
    pending = dict(jobs)
    while pending and time.time() - t0 < 1800:
        time.sleep(5)
        for key, pid in list(pending.items()):
            try:
                h = jget(f"{COMFY}/history/{pid}", timeout=15).get(pid)
            except Exception:  # noqa: BLE001
                continue
            if not h:
                continue
            st = (h.get("status") or {})
            if st.get("status_str") == "error":
                msgs = json.dumps(st.get("messages") or [])[:600]
                print(f"  {key}: RENDER ERROR {msgs}")
                del pending[key]
                continue
            imgs = [i for o in (h.get("outputs") or {}).values()
                    for i in (o.get("images") or [])]
            if not imgs:
                continue
            i = imgs[0]
            fp = OUT / f"{key}.png"
            fp.write_bytes(bget(
                f"{COMFY}/view?filename={urllib.parse.quote(i['filename'])}"
                f"&subfolder={urllib.parse.quote(i.get('subfolder') or '')}"
                f"&type={i.get('type') or 'output'}"))
            files[key] = fp
            print(f"  done  {key}  {fp.stat().st_size} bytes "
                  f"({round(time.time() - t0)}s)")
            del pending[key]
    for key in pending:
        print(f"  TIMED OUT: {key}")
    if not files:
        print("FAIL: no images came back.")
        return 1

    # ── 4. ArcFace against the character's own references ───────────────
    from backend.services import likeness as lk
    if not lk.available():
        print(f"ArcFace unavailable ({lk.health().get('error')}); "
              "images are in scripts/_diag/lora_test/ for eyes only.")
        return 0
    ch = jget(f"{APP}/api/klein3/characters/{CHAR}")
    embs, labels = [], []
    for r in (ch.get("refs") or []):
        tag = str(r.get("tag") or "").lower()
        if tag not in ("front", "face"):
            continue
        fp = OUT / f"_ref_{tag}_{r['id']}.png"
        fp.write_bytes(bget(APP + r["url"]))
        e = lk.embed(fp)
        if e is not None:
            embs.append(e)
            labels.append(f"{tag} reference")
    if not embs:
        print("no usable reference face; images saved, no scores.")
        return 1
    print(f"\nbaselines: {', '.join(labels)}\n")
    print(f"{'variant':<18} {'likeness':>9}  verdict")
    print("-" * 44)
    rows = []
    for key, _, _ in GRID:
        fp = files.get(key)
        if fp is None:
            continue
        s = lk.score(fp, embs)
        v = lk.verdict(s)[0] if s is not None else "no face"
        rows.append({"variant": key, "score": None if s is None else round(s, 4),
                     "verdict": v})
        bar = "#" * int((s or 0) * 40)
        print(f"{key:<18} {s if s is not None else float('nan'):>9.4f}  {v:<12} {bar}")
    (OUT / "scores.json").write_text(json.dumps(rows, indent=2), "utf-8")
    print(f"\nwrote {OUT / 'scores.json'}")
    print("Tell Claude 'lora test is done'.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
