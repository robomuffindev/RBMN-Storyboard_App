"""🩺 Why F5-TTS won't speak — read the boxes, don't guess (v1.277.37).

Found the hard way on 2026-08-17: the voice lane submits fine, the graph
validates, the worker accepts it — and then `F5TTSAudioInputs` dies with

    Could not load libtorchcodec

which is NOT a missing model and NOT a bad graph. F5-TTS decodes its reference
audio through **torchcodec**, and torchcodec is a thin wrapper that dlopen()s
FFmpeg's SHARED libraries at runtime. Two things break that on Windows:

  1. **FFmpeg's DLLs are not visible.** `ffmpeg.exe` on PATH is NOT enough —
     the loader needs `avcodec-*.dll`, `avformat-*.dll`, `avutil-*.dll`, which
     only the **full-SHARED** build ships. The "essentials" build that most
     people install has the .exe and no DLLs at all.
     Fix: `python scripts\\install_ffmpeg_shared.py --apply`.
  2. **torch ↔ torchcodec mismatch.** They ship in lockstep. Fix: `--fix` here.

⭐⭐ **BOTH were true on this fleet (2026-08-18), and fixing only the first made
the second one visible** — torchcodec then loaded far enough to fail on a torch
symbol instead. If you fix one and it still dies, that is progress, not a dead
end: read the NEW error. ✅ After both: 3/3 boxes SPOKE.

    python scripts\\tts_doctor.py            # what each box HAS
    python scripts\\tts_doctor.py --probe    # ⭐ the only real proof: render
                                             #   3 words on a box and report

⚠⚠ The version table CANNOT prove the lane works — the fleet's numbers looked
fine while every render died. `--probe` is the measurement; the table is
context. (This script's first version printed "✅ versions line up" for a
torch it had no entry for, which is a default dressed up as a finding.)
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parent))          # scripts/
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))   # repo root

#: torch major.minor -> the torchcodec built against it. This is torchcodec's
#: OWN published table (README compatibility matrix), not a guess:
#:   0.13/0.12 ≥2.11 · 0.11 ↔ 2.11 · 0.10 ↔ 2.10 · 0.9 ↔ 2.9 · 0.8 ↔ 2.9 ·
#:   0.7 ↔ 2.8 · 0.6 ↔ 2.8 · 0.5 ↔ 2.7 · 0.2 ↔ 2.6
#: ⚠ An unknown torch reports UNKNOWN, never OK — the first version of this
#: script printed "✅ versions line up" for a pairing it had never seen, on the
#: box where every render was failing.
#: ⚠⚠ A MISMATCH here does NOT look like a version error. It surfaces as a
#: Windows message box: *"The procedure entry point torch_dtype_float4_e2m1fn_x2
#: could not be located in ... libtorchcodec_core7.dll"* — torchcodec 0.11's
#: core DLL importing a symbol only torch 2.11 exports. And that modal BLOCKS
#: ComfyUI's startup until somebody clicks OK on the machine.
MATRIX = {"2.6": "0.2", "2.7": "0.5", "2.8": "0.7", "2.9": "0.9",
          "2.10": "0.10", "2.11": "0.11"}


def _helpers() -> list:
    # ⚠ stdlib only — see scripts/_fleet.py. Importing the app made this tool
    # venv-only, and it is exactly the tool you want when things are broken.
    from _fleet import helpers
    return helpers()


def _post(base: str, path: str, token: str, body: dict, timeout=600):
    req = urllib.request.Request(
        f"{base}{path}?token={urllib.parse.quote(token)}",
        data=json.dumps(body).encode(), method="POST",
        headers={"Content-Type": "application/json", "X-RBMN-Token": token})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def _pip(base: str, token: str, args: list) -> dict:
    try:
        return _post(base, "/install/pip", token, {"args": args})
    except urllib.error.HTTPError as e:
        return {"ok": False, "tail": e.read().decode("utf-8", "replace")[:400]}
    except Exception as e:                                       # noqa: BLE001
        return {"ok": False, "tail": f"{type(e).__name__}: {e}"}


def _ver(tail: str) -> str:
    m = re.search(r"^Version:\s*(\S+)", tail or "", re.M)
    return m.group(1) if m else ""


def _tone(dest: Path, seconds: float = 3.0) -> bool:
    if not shutil.which("ffmpeg"):
        return False
    r = subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i",
                        f"sine=frequency=180:duration={seconds}",
                        "-ac", "1", "-ar", "24000", str(dest)],
                       capture_output=True, timeout=120)
    return r.returncode == 0 and dest.exists()


def _node_info(host: str, node: str):
    """The node's input spec from the WORKER's own object_info.

    Re-implemented here in stdlib rather than imported from
    `backend.api.audio_lab`, for the same reason as the registry: this tool has
    to run when the app cannot."""
    try:
        with urllib.request.urlopen(
                f"http://{host}:8188/object_info/{node}", timeout=30) as r:
            d = json.loads(r.read().decode())
        return d.get(node) or (d if d else None)
    except Exception:                                            # noqa: BLE001
        return None


def _defaults(host: str, node: str, over: dict) -> dict:
    """Required inputs filled from the BOX's declared defaults, then overridden.

    ⚠ Guessing widget order is the classic ComfyUI trap — and a node that
    gained a required input breaks every graph that predates it (the v1.277.37
    `RBG_Smart_Seed_Variance` 400). Ask the box."""
    spec = (_node_info(host, node) or {}).get("input", {}).get("required", {})
    out = {}
    for name, meta in spec.items():
        if name in over:
            continue
        if isinstance(meta, list) and len(meta) > 1 and isinstance(meta[1], dict) \
                and "default" in meta[1]:
            out[name] = meta[1]["default"]
        elif isinstance(meta, list) and meta and isinstance(meta[0], list) and meta[0]:
            out[name] = meta[0][0]          # a combo: take its first option
    out.update(over)
    return out


def _upload(host: str, fp: Path) -> str:
    bound = uuid.uuid4().hex
    b = ("--" + bound).encode()
    payload = (b + b"\r\n"
               + f'Content-Disposition: form-data; name="image"; '
                 f'filename="{fp.name}"\r\n'.encode()
               + b"Content-Type: application/octet-stream\r\n\r\n"
               + fp.read_bytes() + b"\r\n" + b + b"--\r\n")
    req = urllib.request.Request(f"http://{host}:8188/upload/image",
                                 data=payload, method="POST")
    req.add_header("Content-Type", f"multipart/form-data; boundary={bound}")
    with urllib.request.urlopen(req, timeout=300) as r:
        return json.loads(r.read().decode()).get("name") or fp.name


def _graph(host: str, ref: str, ref_text: str, text: str) -> dict:
    """The same shape audio_lab builds — whichever of the node's two known
    forms this box reports."""
    if _node_info(host, "F5TTSAudioInputs") is not None:
        return {
            "1": {"class_type": "LoadAudio", "inputs": {"audio": ref}},
            "2": {"class_type": "F5TTSAudioInputs",
                  "inputs": _defaults(host, "F5TTSAudioInputs", {
                      "sample_audio": ["1", 0], "sample_text": ref_text,
                      "speech": text, "seed": 12345, "speed": 1.0})},
            "3": {"class_type": "SaveAudio",
                  "inputs": {"audio": ["2", 0],
                             "filename_prefix": f"RBMN-AUDIO/probe_{uuid.uuid4().hex[:6]}"}},
        }
    return {
        "2": {"class_type": "F5TTSAudio",
              "inputs": _defaults(host, "F5TTSAudio", {
                  "sample": ref, "sample_text": ref_text, "speech": text,
                  "seed": 12345, "speed": 1.0})},
        "3": {"class_type": "SaveAudio",
              "inputs": {"audio": ["2", 0],
                         "filename_prefix": f"RBMN-AUDIO/probe_{uuid.uuid4().hex[:6]}"}},
    }


def probe(host: str) -> int:
    """Submit the smallest possible F5 job and report what the node says.

    Three words, a 3 s reference. If it returns audio, the decode chain works;
    if it raises, the exception is the answer — and it is the SAME exception a
    real narration would hit, which a version table can never tell you."""
    base = f"http://{host}:8188"
    if _node_info(host, "F5TTSAudioInputs") is None and \
            _node_info(host, "F5TTSAudio") is None:
        print(f"   ❌ the F5-TTS node is not installed on {host} "
              f"(python scripts/install_audio.py)")
        return 1
    tmp = Path(tempfile.gettempdir()) / f"rbmn_probe_{uuid.uuid4().hex[:6]}.wav"
    if not _tone(tmp):
        print("   ❌ needs ffmpeg on THIS machine to make the 3s reference")
        return 1
    try:
        up = _upload(host, tmp)
        g = _graph(host, up, "a steady tone", "one two three")
        req = urllib.request.Request(
            f"{base}/prompt", data=json.dumps({"prompt": g}).encode(),
            headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=120) as r:
            pid = json.loads(r.read().decode()).get("prompt_id")
    except urllib.error.HTTPError as e:
        print("   ❌ rejected at validation — the BODY is the answer:")
        print("     ", e.read().decode("utf-8", "replace")[:600])
        return 1
    except Exception as e:                                       # noqa: BLE001
        print(f"   ❌ {type(e).__name__}: {e}")
        return 1
    finally:
        tmp.unlink(missing_ok=True)

    for _ in range(120):
        time.sleep(2)
        try:
            with urllib.request.urlopen(f"{base}/history/{pid}", timeout=30) as r:
                h = json.loads(r.read().decode()).get(pid) or {}
        except Exception:                                        # noqa: BLE001
            continue
        if not h:
            continue
        st = h.get("status") or {}
        msgs = st.get("messages") or []
        err = next((m for m in msgs if m and m[0] == "execution_error"), None)
        if err:
            d = err[1] if len(err) > 1 else {}
            print(f"   ❌ node {d.get('node_type')} raised:")
            print("     ", str(d.get("exception_message"))[:700].replace("\n", "\n      "))
            return 1
        if st.get("completed") or h.get("outputs"):
            outs = h.get("outputs") or {}
            print(f"   ✅ SPOKE — {sum(len(v.get('audio', [])) for v in outs.values())} "
                  f"clip(s) back from {host}")
            return 0
    print("   ⚠ still running after 4 minutes — check the box")
    return 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--probe", action="store_true",
                    help="actually render 3 words on each box")
    ap.add_argument("--fix", action="store_true",
                    help="install the torchcodec that matches each box's torch")
    ap.add_argument("--host", default="", help="only this worker")
    a = ap.parse_args()
    hosts = [h for h in _helpers() if not a.host or h["host"] == a.host]
    if not hosts:
        print("no workers configured")
        return 1
    print("🩺 F5-TTS decode chain\n")
    bad = 0
    for h in hosts:
        host, token = h["host"], h["token"]
        base = f"http://{host}:{h.get('port', 8765)}"
        print(f"── {h.get('name') or host} ({host})")
        torch = _ver(_pip(base, token, ["show", "torch"]).get("tail", ""))
        codec = _ver(_pip(base, token, ["show", "torchcodec"]).get("tail", ""))
        mm = ".".join(torch.split(".")[:2]) if torch else ""
        want = MATRIX.get(mm, "")
        print(f"   torch {torch or '?'}   torchcodec {codec or '— NOT INSTALLED'}")
        if not codec:
            print("   ❌ torchcodec missing — F5 cannot decode its reference")
            bad += 1
        elif not want:
            # ⚠ say UNKNOWN. The fleet runs torch 2.10 / torchcodec 0.11, a
            # pair this table has never heard of, and calling that "fine" is
            # how a version check becomes a false alibi.
            print(f"   • no pairing on record for torch {mm or '?'} — "
                  f"UNKNOWN, not OK. Use --probe.")
        elif not codec.startswith(want):
            print(f"   ❌ MISMATCH: torch {mm} ships torchcodec {want}.x")
            bad += 1
        else:
            print("   • versions pair correctly (this does NOT prove decode)")
        if a.fix and want and (not codec or not codec.startswith(want + ".")):
            # ⚠⚠ --no-deps is NOT an optimisation: torchcodec DEPENDS on torch,
            # and letting pip satisfy that would upgrade the fleet's torch out
            # from under SageAttention and every custom node. Pin the codec to
            # the torch that is there; never move the torch.
            print(f"   ⏳ installing torchcodec=={want}.* (--no-deps, torch "
                  f"stays at {torch}) …")
            r = _pip(base, token, ["install", "--no-deps", "--upgrade",
                                   "--force-reinstall", f"torchcodec=={want}.*"])
            tail = (r.get("tail") or "").strip().splitlines()[-1:] or [""]
            print(f"   {'✅' if r.get('ok') else '❌'} {tail[0][:160]}")
            if not r.get("ok"):
                print("      " + (r.get("tail") or "")[-500:])
        if a.probe:
            bad += probe(host)

    # ⚠ Only print the remedy when there is something to remedy. The first
    # version printed this block unconditionally, so a fully PASSING run still
    # ended in a wall of "here is what is broken" — a screen that contradicts
    # its own result teaches you to stop reading it.
    if bad:
        print("\n🔧 The two fixes, in order:")
        print("  1. FFmpeg 7 SHARED DLLs beside python.exe on each box:")
        print("       python scripts\\install_ffmpeg_shared.py --apply")
        print("     (`ffmpeg.exe` on PATH does NOT count — only a "
              "full_build-shared archive ships DLLs)")
        print("  2. torchcodec pinned to that box's torch:")
        print("       python scripts\\tts_doctor.py --fix")
        print("     ⚠ a mismatch shows up as a Windows message box about a "
              "missing entry point")
        print("       in libtorchcodec_core*.dll, and that modal BLOCKS "
              "ComfyUI's startup.")
        print("  Then restart ComfyUI on the box and re-run --probe.")
    print(f"\n{'ALL CHECKS PASSED' if not bad else f'{bad} problem(s)'}")
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
