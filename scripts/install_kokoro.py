"""🎨 Stage KOKORO TTS — reference-free voices — on the fleet (v1.277.43).

**Why Kokoro at all.** F5 needs a reference clip. Kokoro does not: it ships ~54
built-in speakers and can BLEND two of them into a voice that does not exist
anywhere. His question was *"is there any way to generate voices without a
reference?"* — this is that, and it also feeds the F5 lane: render 10 s with a
preset, save it as a voice, and F5 clones it. The transcript is then **exact by
construction**, which removes the single most common cause of a drifting clone.

    python scripts\\install_kokoro.py --check          # what each box has
    python scripts\\install_kokoro.py --host 1.2.3.4   # ONE box first
    python scripts\\install_kokoro.py                  # the whole fleet

⚠⚠ **ONE BOX FIRST, ON PURPOSE.** The popular `ComfyUI-Geeky-Kokoro-TTS` says
outright it does not work on **Python 3.13**, and every worker here runs
3.13.11 — so we use `billwuhao/ComfyUI_KokoroTTS_MW` and PROVE it imports on a
real box before touching the other two. A node that installs and then fails at
import is worse than one that never installed: it looks ready.

⚠ Models are NOT auto-downloaded by this node. They go to
`<comfy>/models/Kokorotts/Kokoro-82M/` (config.json + kokoro-v1_0.pth +
voices/*.pt) — `--models` ships them from here with the same wheel trick
`install_ffmpeg_shared.py` uses, because the helper's `/download/model` only
takes a PLAIN folder name and this path has a separator in it.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))   # scripts/
sys.path.insert(0, str(ROOT))

CACHE = ROOT / ".cache" / "kokoro"
GIT = "https://github.com/billwuhao/ComfyUI_KokoroTTS_MW"
NODE_DIR = "ComfyUI_KokoroTTS_MW"
HF = "https://huggingface.co/hexgrad/Kokoro-82M/resolve/main"
CORE = ["config.json", "kokoro-v1_0.pth"]
#: the American/British English voices — the ones a narration lane will use.
#: (The full set is ~54 across 9 languages; staging all of them is a `--all`.)
VOICES_EN = [
    "af_heart", "af_bella", "af_nicole", "af_aoede", "af_kore", "af_sarah",
    "af_nova", "af_sky", "af_alloy", "af_jessica", "af_river",
    "am_michael", "am_fenrir", "am_puck", "am_echo", "am_eric", "am_liam",
    "am_onyx", "am_adam", "am_santa",
    "bf_emma", "bf_isabella", "bf_alice", "bf_lily",
    "bm_george", "bm_fable", "bm_lewis", "bm_daniel",
]
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) rbmn-installer"}
DATASET_NAME = "rbmn-kokoro-models"
WHEEL = "rbmn_kokoro_models-1.0.0-py3-none-any.whl"


def helpers() -> list:
    from _fleet import helpers as _h          # stdlib only
    return _h()


def hget(h: dict, path: str, timeout=60):
    base = f"http://{h['host']}:{h.get('port', 8765)}"
    req = urllib.request.Request(
        f"{base}{path}?token={urllib.parse.quote(h['token'])}",
        headers={"X-RBMN-Token": h["token"]})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def hpost(h: dict, path: str, body, timeout=1800, raw=False):
    base = f"http://{h['host']}:{h.get('port', 8765)}"
    data = body if raw else json.dumps(body).encode()
    req = urllib.request.Request(
        f"{base}{path}?token={urllib.parse.quote(h['token'])}", data=data,
        method="POST",
        headers={"X-RBMN-Token": h["token"],
                 "Content-Type": ("application/zip" if raw
                                  else "application/json")})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code}: "
                           f"{e.read().decode('utf-8', 'replace')[:400]}") from None


def node_present(host: str, name: str) -> bool:
    try:
        with urllib.request.urlopen(f"http://{host}:8188/object_info/{name}",
                                    timeout=20) as r:
            return bool(json.loads(r.read().decode()))
    except Exception:                                            # noqa: BLE001
        return False


def kokoro_node(host: str) -> str:
    """Whichever class name this box reports — ask, do not assume."""
    for n in ("KokoroTTSRun", "KokoroTextToSpeech", "MultiLinePromptKokoro",
              "KokoroTTS", "GeekyKokoroTTS"):
        if node_present(host, n):
            return n
    return ""


def fetch_models(all_voices: bool) -> dict:
    """Download the model + voice embeddings once, here."""
    CACHE.mkdir(parents=True, exist_ok=True)
    files = {}
    names = [(f, f"Kokoro-82M/{f}") for f in CORE]
    voices = VOICES_EN if not all_voices else VOICES_EN      # --all: extend
    names += [(f"voices/{v}.pt", f"Kokoro-82M/voices/{v}.pt") for v in voices]
    for src, dest in names:
        local = CACHE / src.replace("/", "_")
        if not local.exists() or local.stat().st_size < 100:
            url = f"{HF}/{src}"
            print(f"  ⬇ {src}")
            try:
                req = urllib.request.Request(url, headers=UA)
                with urllib.request.urlopen(req, timeout=600) as r, \
                        local.open("wb") as fh:
                    while True:
                        b = r.read(1 << 20)
                        if not b:
                            break
                        fh.write(b)
            except Exception as e:                               # noqa: BLE001
                print(f"    ✗ {type(e).__name__}: {e}")
                local.unlink(missing_ok=True)
                continue
        files[dest] = local.read_bytes()
    return files


def build_wheel(files: dict) -> bytes:
    dist = "rbmn_kokoro_models-1.0.0.dist-info"
    payload = dict(files)
    payload[f"{dist}/METADATA"] = (
        "Metadata-Version: 2.1\nName: rbmn-kokoro-models\nVersion: 1.0.0\n"
        "Summary: Kokoro-82M weights + voice embeddings, unpacked into "
        "ComfyUI/models/Kokorotts by pip --target.\n").encode()
    payload[f"{dist}/WHEEL"] = (
        "Wheel-Version: 1.0\nGenerator: rbmn\nRoot-Is-Purelib: true\n"
        "Tag: py3-none-any\n").encode()
    rows = []
    for name, blob in payload.items():
        d = base64.urlsafe_b64encode(hashlib.sha256(blob).digest()).rstrip(b"=")
        rows.append(f"{name},sha256={d.decode()},{len(blob)}")
    rows.append(f"{dist}/RECORD,,")
    payload[f"{dist}/RECORD"] = ("\n".join(rows) + "\n").encode()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, blob in payload.items():
            z.writestr(name, blob)
    return buf.getvalue()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="report only")
    ap.add_argument("--host", default="", help="one worker (do this FIRST)")
    ap.add_argument("--models", action="store_true",
                    help="also ship the weights + voices")
    ap.add_argument("--all-voices", action="store_true")
    ap.add_argument("--restart", action="store_true",
                    help="restart ComfyUI afterwards (a new node needs it)")
    a = ap.parse_args()
    hosts = [h for h in helpers() if not a.host or h["host"] == a.host]
    if not hosts:
        print("no workers configured")
        return 1

    print("🎨 Kokoro TTS — reference-free voices\n")
    if a.check:
        for h in hosts:
            n = kokoro_node(h["host"])
            print(f"  {h['host']}: {'✅ ' + n if n else '❌ no Kokoro node'}")
        return 0

    blob = b""
    if a.models:
        print("1) models (downloaded once, here)")
        files = fetch_models(a.all_voices)
        if not files:
            print("  ✗ nothing downloaded")
            return 1
        mb = sum(len(v) for v in files.values()) / 1048576
        print(f"  ✔ {len(files)} file(s), {mb:.0f} MB")
        wheel = build_wheel(files)
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as z:
            z.writestr(WHEEL, wheel)
        blob = buf.getvalue()

    bad = 0
    for h in hosts:
        host = h["host"]
        print(f"\n── {h.get('name') or host} ({host})")
        try:
            cfg = hget(h, "/config")
        except Exception as e:                                   # noqa: BLE001
            print(f"   ✗ helper unreachable: {e}")
            bad += 1
            continue
        root = (cfg.get("comfy") or {}).get("root") or ""
        if not root:
            print("   ✗ no ComfyUI root reported")
            bad += 1
            continue
        try:
            r = hpost(h, "/install/node", {"git_url": GIT}, timeout=1800)
            print(f"   📦 node: {json.dumps(r)[:160]}")
        except Exception as e:                                   # noqa: BLE001
            print(f"   ✗ node install failed: {e}")
            bad += 1
            continue
        if blob:
            try:
                hpost(h, f"/datasets/{DATASET_NAME}", blob, timeout=3600,
                      raw=True)
                state = (hget(h, "/diag").get("helper") or {}).get("state_dir")
                local = f"{state}\\datasets\\{DATASET_NAME}\\{WHEEL}"
                r = hpost(h, "/install/pip", {"args": [
                    "install", "--no-deps", "--no-index", "--upgrade",
                    "--target", f"{root}\\ComfyUI\\models\\Kokorotts",
                    local]}, timeout=3600)
                print(f"   {'✅' if r.get('ok') else '❌'} models → "
                      f"models\\Kokorotts")
                if not r.get("ok"):
                    print("      " + (r.get("tail") or "")[-300:])
                    bad += 1
            except Exception as e:                               # noqa: BLE001
                print(f"   ✗ models failed: {e}")
                bad += 1
        if a.restart:
            try:
                hpost(h, "/comfy/stop", {}, timeout=180)
                hpost(h, "/comfy/start", {}, timeout=300)
                print("   ↻ ComfyUI restarted")
            except Exception as e:                               # noqa: BLE001
                print(f"   ⚠ restart failed: {e}")

    print("\n⭐ A node that INSTALLED is not a node that IMPORTS — restart "
          "ComfyUI, then:\n     python scripts\\install_kokoro.py --check")
    print("   (it asks the box's own object_info, which is the only proof)")
    print(f"\n{'DONE' if not bad else f'{bad} problem(s)'}")
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
