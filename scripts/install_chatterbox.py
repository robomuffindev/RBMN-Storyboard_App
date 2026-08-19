"""🗣 Install CHATTERBOX (via TTS-Audio-Suite) on the worker fleet.

WHY CHATTERBOX
--------------
His call, 2026-08-19: *"lets use chatterbox… F5 is neat but I hate the
non-commercial stance. its trash in 2026."*

He is right, and it matters more than taste: **F5-TTS is CC-BY-NC 4.0**, and he
intends to *"give it to the public to express themselves and tell their
stories"*. Shipping an app whose default narration engine forbids commercial use
puts that restriction on every person who uses it. **Chatterbox is MIT** (Resemble
AI), and in blind listening tests it was preferred over ElevenLabs 65.3% to 24.5%.

⚠ **F5 IS NOT REMOVED.** Existing voices and every take already rendered
reference it, and deleting an engine to make a point destroys his work. It stays
selectable and is now LABELLED non-commercial in the UI, which is the honest fix.

WHAT THIS INSTALLS
------------------
`diodiogod/TTS-Audio-Suite` — one pack carrying Chatterbox (classic +
multilingual), IndexTTS-2, Higgs Audio 2/3, VibeVoice, RVC and F5-TTS. Chatterbox
is what we wire; the rest arrive for free and can be adopted later without
another fleet install. **VibeVoice is worth remembering** — it is built for
long-form expressive narration, which is exactly his stated use case.

⚠⚠ ONE BOX FIRST. ALWAYS.
-------------------------
This pack is heavy and pulls real dependencies into the **embedded python** the
worker's ComfyUI runs on. That is precisely how F5 cost days: a torch/torchcodec
mismatch produced a **Windows modal dialog** that blocked ComfyUI's startup until
someone clicked OK on the machine — a box that "won't come back" was waiting
behind a dialog nobody could see. So:

    python scripts/install_chatterbox.py --host <one box>      # install
    python scripts/install_chatterbox.py --check               # what is where
    python scripts/install_chatterbox.py --all                 # only after one works

`--check` never writes. Between installing and fanning out, RESTART that box's
ComfyUI and confirm BOTH that it comes back AND that F5 still renders — an
install that succeeds and never imports looks exactly like readiness.

⚠ STDLIB ONLY — an operator script that imports `backend.*` dies outside the
venv, which is when you need it most.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")   # type: ignore
except Exception:                                                # noqa: BLE001
    pass

SUITE_GIT = "https://github.com/diodiogod/TTS-Audio-Suite"
SUITE_NAME = "TTS-Audio-Suite"

#: Node classes we consider proof the pack IMPORTED (not merely cloned).
#: ⚠ We look for several because the pack renames things between releases, and
#: a probe that tests one exact name reports "missing" on a working install.
CHATTERBOX_HINTS = ("ChatterBoxTTS", "ChatterboxTTS", "ChatterBoxVoiceTTS",
                    "TTSAudioSuite", "ChatterBoxEngine", "ChatterboxEngine")

#: ⚠⚠ Packages Chatterbox needs that the pack's `requirements.txt` DELIBERATELY
#: omits (it lists them under "PROBLEMATIC PACKAGES" and installs them from its
#: own `install.py`, which ComfyUI Manager runs and our helper does not).
#: Without these the node LOADS, the graph VALIDATES, ComfyUI reports SUCCESS
#: in 191 ms, and you get one second of silence. Measured on .163, 2026-08-19.
CB_EXTRA_PIP = ("s3tokenizer", "resemble-perth", "descript-audio-codec")


def _get(url: str, timeout: int = 30):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode())


def _post(url: str, body: dict, timeout: int = 3600):
    data = json.dumps(body).encode()
    r = urllib.request.Request(url, data=data, method="POST")
    r.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(r, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def _object_info(host: str) -> dict:
    try:
        return _get(f"http://{host}:8188/object_info", timeout=90)
    except Exception as e:                                       # noqa: BLE001
        print(f"    ⚠ {host}: ComfyUI object_info unreachable ({e})")
        return {}


def _report(host: str, oi: dict) -> list:
    """Which of the suite's engines actually IMPORTED on this box."""
    if not oi:
        return []
    found = []
    for want, label in (("ChatterBox", "Chatterbox"), ("IndexTTS", "IndexTTS-2"),
                        ("VibeVoice", "VibeVoice"), ("HiggsAudio", "Higgs Audio"),
                        ("F5TTS", "F5-TTS"), ("RVC", "RVC")):
        hits = [k for k in oi if want.lower() in k.lower()]
        if hits:
            found.append(f"{label} ({len(hits)} node(s), e.g. {hits[0]})")
    return found


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", action="append", default=[],
                    help="worker IP; repeatable")
    ap.add_argument("--all", action="store_true",
                    help="every worker the APP knows about (asks it)")
    ap.add_argument("--check", action="store_true",
                    help="report only — never installs")
    ap.add_argument("--token", default="", help="helper token if not in settings")
    ap.add_argument("--app", default="http://127.0.0.1:8899")
    ap.add_argument("--no-restart", action="store_true",
                    help="do NOT stop/start ComfyUI around the install "
                         "(⚠ on Windows a running ComfyUI locks its DLLs "
                         "and the dependency install will fail)")
    a = ap.parse_args()

    hosts = list(a.host)
    tokens = {}
    if a.all or not hosts:
        # ⭐ ASK THE APP rather than hard-coding IPs — every box is DHCP and the
        # addresses in the docs have moved twice.
        try:
            ov = _get(f"{a.app}/api/audio-lab/overview", timeout=30)
            found = [w["host"] for w in (ov.get("workers") or []) if w.get("host")]
            hosts = hosts or found
            print(f"  workers per the app: {', '.join(found) or 'none'}")
        except Exception as e:                                   # noqa: BLE001
            print(f"  ⚠ could not ask the app for workers ({e}); pass --host")
    if not hosts:
        print("  nothing to do — pass --host <ip> or start the app")
        return 1
    # ⚠⚠ TOKENS DO NOT COME FROM THE API. `GET /api/lora/helpers` deliberately
    # REDACTS the token field (correct — the repo is public). The established
    # resolver is `scripts/helper_token.py`: RBMN_HELPER_TOKEN env →
    # scripts/helper_token.txt → the forge settings store. Use it rather than
    # inventing a path, and never hard-code a credential here.
    fallback = a.token
    if not fallback:
        try:
            import os
            from pathlib import Path as _P
            _s = str(_P(__file__).resolve().parent)
            if _s not in sys.path:
                sys.path.insert(0, _s)
            from helper_token import helper_token as _ht      # type: ignore
            fallback = (_ht() or "").strip()
        except Exception:                                        # noqa: BLE001
            import os
            fallback = os.environ.get("RBMN_HELPER_TOKEN", "").strip()
    # ⚠⚠ TOKENS ARE PER BOX. `helper_token.txt` is the TRAINER's; using it on a
    # worker gives a bare **401**, which reads like a broken helper rather than
    # a wrong credential. The per-box values live in the forge registry, which
    # the API redacts — so read the store, exactly as the memory note says:
    # *ask the running app, fall back to `_libraries/forge/settings.json`*.
    # ⚠ `_libraries` is under `project_dir` (D:\RBMN-Projects here), NOT the
    # repo — ask the app where that is rather than guessing.
    try:
        from pathlib import Path as _P
        pdir = ""
        try:
            pdir = str((_get(f"{a.app}/api/settings", timeout=20)
                        or {}).get("project_dir") or "")
        except Exception:                                        # noqa: BLE001
            pass
        # ⚠⚠ THE `cfg.project_dir` DB-OVERRIDE GOTCHA, AND IT BIT ME HERE.
        # `/api/settings` reports the DB value (`D:\RBMN-Projects`), but
        # `forge._FORGE_DIR` is anchored at IMPORT TIME — so the file that
        # actually holds the per-box tokens was under the DEFAULT root
        # (`C:\Users\<user>\RBMN-Projects`). Asking the app gave a path that
        # does not exist, and the only symptom was a bare 401 from the helper.
        # Try both, in that order, and say which one answered.
        home = _P.home()
        cands = [p for p in (
            (_P(pdir) / "_libraries" / "forge" / "settings.json") if pdir else None,
            home / "RBMN-Projects" / "_libraries" / "forge" / "settings.json",
            _P(__file__).resolve().parents[1] / "_libraries" / "forge" / "settings.json",
        ) if p]
        for fp in cands:
            if fp.is_file():
                st = json.loads(fp.read_text("utf-8"))
                for w in (st.get("helpers") or []):
                    if w.get("host") and w.get("token"):
                        tokens[w["host"]] = str(w["token"])
                if tokens:
                    print(f"  per-box tokens from {fp}")
                    break
    except Exception as e:                                       # noqa: BLE001
        print(f"  ⚠ could not read the forge helper registry ({e})")
    print(f"  helper token: {'resolved' if fallback else 'NOT FOUND'}"
          + (f" (+{len(tokens)} per-box override(s))" if tokens else ""))

    print(f"\n🗣 Chatterbox / TTS-Audio-Suite — {len(hosts)} box(es)"
          f"{'  (CHECK ONLY)' if a.check else ''}\n")
    if not a.check and len(hosts) > 1:
        print("  ⚠⚠ Installing on MORE THAN ONE box at once. The F5 lesson was a"
              "\n     dependency clash that blocked ComfyUI's STARTUP behind a"
              "\n     Windows dialog. Do one box, restart it, prove F5 still"
              "\n     renders, and only then fan out.\n")

    ok = True
    for host in hosts:
        print(f"── {host}")
        oi = _object_info(host)
        have = _report(host, oi)
        if have:
            print("    present: " + " · ".join(have))
        has_cb = any(any(h.lower() in k.lower() for k in oi)
                     for h in CHATTERBOX_HINTS)
        if has_cb:
            print("    ✅ Chatterbox nodes are LOADED")
            continue
        if a.check:
            print("    ❌ Chatterbox not loaded here")
            ok = False
            continue
        token = tokens.get(host, "") or fallback
        if not token:
            print("    ❌ no helper token — set RBMN_HELPER_TOKEN, put it in "
                  "scripts/helper_token.txt, or pass --token")
            ok = False
            continue
        # ⚠⚠ STOP COMFYUI FIRST — ON WINDOWS A RUNNING PROCESS LOCKS ITS DLLs.
        # Measured on the trainer: `pip -r requirements` returned rc=1 with
        # *"Access denied: …\numpy.libs\msvcp140-….dll"* purely because ComfyUI
        # was up and holding numpy's native library. The clone succeeded, the
        # deps did not, and the only symptom would have been another silent
        # render. Stop → install → start is not politeness, it is required.
        stopped = False
        if not a.no_restart:
            try:
                _post(f"http://{host}:8765/comfy/stop?token={token}", {}, 300)
                stopped = True
                print("    ⏹ stopped ComfyUI (Windows locks DLLs in use)")
                time.sleep(6)
            except Exception as e:                               # noqa: BLE001
                print(f"    ⚠ could not stop ComfyUI ({e}) — installing anyway; "
                      f"a locked-DLL failure below is why")
        print(f"    📦 installing {SUITE_NAME} (this pulls real deps; minutes)…")
        try:
            r = _post(f"http://{host}:8765/install/node?token={token}",
                      {"git_url": SUITE_GIT, "name": SUITE_NAME}, timeout=3600)
        except Exception as e:                                   # noqa: BLE001
            print(f"    ❌ install failed: {e}")
            ok = False
            continue
        for s in (r.get("steps") or []):
            print(f"      · {s}")
        if not r.get("ok"):
            print("    ❌ the helper reported failure")
            ok = False
            continue
        # ⚠⚠ THE PACK'S OWN `install.py` HANDLES THE HARD DEPENDENCIES, AND WE
        # NEVER RUN IT. Our helper only does `pip install -r requirements.txt`,
        # and the pack deliberately keeps its "problematic" packages OUT of
        # that file. Result on the first attempt: the node LOADED, the graph
        # validated, ComfyUI reported **execution_success in 191 ms**, and
        # SaveAudio wrote **one second of digital silence**. A failing engine
        # that returns a green job is the worst possible failure mode.
        # These three are what Chatterbox itself needs; `--no-deps` on every
        # one because the F5 lesson was a resolver dragging torch and blocking
        # ComfyUI's startup behind a Windows dialog.
        print("    📦 the deps the pack's requirements.txt deliberately omits…")
        for pkg in CB_EXTRA_PIP:
            try:
                pr = _post(f"http://{host}:8765/install/pip?token={token}",
                           {"args": ["install", "--no-deps", pkg]}, timeout=1800)
                print(f"      · {pkg}: rc={pr.get('rc')}")
                if pr.get("rc"):
                    ok = False
            except Exception as e:                               # noqa: BLE001
                print(f"      · {pkg}: ❌ {e}")
                ok = False
        if stopped:
            try:
                _post(f"http://{host}:8765/comfy/start?token={token}", {}, 300)
                print("    ▶ starting ComfyUI back up…")
                # ⭐ WAIT AND VERIFY. "I sent start" is not "it came back", and
                # a box that never returns is the failure this whole one-box-
                # first dance exists to catch early.
                for _ in range(60):
                    time.sleep(10)
                    oi2 = _object_info(host)
                    if oi2:
                        got = any(any(h.lower() in k.lower() for k in oi2)
                                  for h in CHATTERBOX_HINTS)
                        print(f"    {'✅' if got else '❌'} back with {len(oi2)} "
                              f"nodes; Chatterbox loaded: {got}")
                        if not got:
                            ok = False
                        break
                else:
                    print("    ❌ ComfyUI did NOT come back — check the machine "
                          "for a modal dialog (the F5/torchcodec failure mode)")
                    ok = False
            except Exception as e:                               # noqa: BLE001
                print(f"    ❌ could not start ComfyUI: {e}")
                ok = False
        else:
            print("    ↻ RESTART ComfyUI on this box, then re-run with --check.")
            print("      ⚠ An install that succeeds and never IMPORTS looks")
            print("        exactly like readiness — --check reads object_info,")
            print("        which is the only proof the nodes actually loaded.")

    print()
    if a.check:
        print("✅ every box has Chatterbox" if ok else
              "⚠ some boxes are missing Chatterbox (see above)")
    else:
        print("Next: restart ComfyUI on the box(es) you touched, then:")
        print("  python scripts/install_chatterbox.py --check")
        print("  …and confirm F5 STILL renders before fanning out.")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
