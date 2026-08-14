"""🚦 Preflight for a long ⚡ Autogen run — every dependency, checked in seconds.

WHY THIS EXISTS
---------------
A full Autogen with LoRA training is **seven hours**. Every one of those hours is
spent on something that can only fail because of a condition that was already
true when you pressed the button:

  * a box asleep  -> renders fail, or the fleet is quietly 2 workers instead of 3
  * helper down   -> the run gets all the way to training and dies there
  * Ollama down   -> no captions, no QC, no wardrobe, no costume drafting
  * ArcFace gone  -> the free base-set gate cannot run AND no epoch can be scored,
                     which is what picks the LoRA at the very end
  * fizgig paths  -> training refuses at the last step

Checking all of it costs nothing and takes seconds. **Run this before you walk
away from a long job.**

    python scripts/preflight_autogen.py
    python scripts/preflight_autogen.py --name "Vivienne V3"   # + trigger collision

⚠ WHAT IT CANNOT CHECK: free disk on the trainer (the helper does not report it)
and whether the boxes will STAY up — his trainer rebooted mid-run once already.
A green preflight is a good start, not a guarantee.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.request

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")   # cp1252 console
except Exception:                                                # noqa: BLE001
    pass

APP = "http://127.0.0.1:8899"
PROBLEMS: list = []
WARNINGS: list = []


def get(url: str, timeout: float = 15.0):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", "replace")), None
    except urllib.error.HTTPError as e:
        return None, f"HTTP {e.code}"
    except Exception as e:                                       # noqa: BLE001
        return None, f"{type(e).__name__}"


def ok(label: str, good: bool, detail: str = "", warn_only: bool = False) -> bool:
    print(f"  {'✅' if good else ('⚠️ ' if warn_only else '❌')} {label}"
          + (f" — {detail}" if detail else ""))
    if not good:
        (WARNINGS if warn_only else PROBLEMS).append(label)
    return good


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", default="", help="character name you are about to make")
    a = ap.parse_args()

    print("🚦 Autogen preflight\n")

    # ── 1. the app itself ───────────────────────────────────────────────────
    print("1. app")
    h, err = get(f"{APP}/api/autogen/health")
    ok("backend is answering", h is not None, err or "")
    if h is None:
        print("\n❌ the backend is not running — nothing else can be checked.")
        return 2
    ok("no autogen run already in flight",
       not (h.get("active") or []), f"active: {h.get('active')}")
    if h.get("queue"):
        ok(f"queue has {h['queue']} job(s) waiting", True,
           "they will run before yours", warn_only=True)

    # ── 2. the render fleet ─────────────────────────────────────────────────
    print("\n2. render fleet (ComfyUI :8188)")
    snap, _ = get(f"{APP}/api/debug/snapshot", 30)
    ws = (snap or {}).get("workers") or []
    healthy = [w for w in ws if w.get("healthy")]
    ok("at least one worker registered", bool(ws), f"{len(ws)} registered")
    ok("all registered workers are healthy", len(healthy) == len(ws),
       f"{len(healthy)}/{len(ws)}")
    # ⚠ THE ONE THAT ACTUALLY BIT HIM: a box that was down when the backend
    # started is not registered AT ALL, so "all healthy" can be true of a fleet
    # that is quietly missing a third of itself.
    ok("the whole fleet is present (3 boxes)", len(ws) >= 3,
       "" if len(ws) >= 3 else
       f"only {len(ws)} — a box that was down at startup rejoins within ~45s; "
       f"if one stays missing, check it is awake",
       warn_only=len(ws) >= 1)
    missing_cap = [w for w in ws if "klein" not in (w.get("capabilities") or [])]
    ok("every box reports the 'klein' capability", not missing_cap,
       "" if not missing_cap else
       f"{[w['url'] for w in missing_cap]} — the fan-out pool silently shrinks "
       f"to the boxes that do")

    # ── 3. vision + scoring ─────────────────────────────────────────────────
    print("\n3. brains (captions · QC · wardrobe · scoring)")
    lh, _ = get(f"{APP}/api/lora/health", 30)
    vis = (lh or {}).get("vision") or {}
    ok("Ollama vision model reachable", bool(vis.get("servers")),
       f"{vis.get('servers', 0)} server(s), model {vis.get('model')}"
       + ("" if vis.get("servers") else " — captions/QC/wardrobe will fail"))
    lk, _ = get(f"{APP}/api/lora/likeness-health", 30)
    ok("ArcFace available", bool((lk or {}).get("available")),
       f"{(lk or {}).get('model')} — needed for the free base gate AND for "
       f"picking the best epoch at the end")

    # ── 4. the trainer ──────────────────────────────────────────────────────
    print("\n4. trainer box (only needed if you tick 🚀 LoRA)")
    ts, _ = get(f"{APP}/api/lora/trainer-settings", 40)
    ts = ts or {}
    ok("trainer helper online", bool(ts.get("online")),
       f"{ts.get('host')}:{ts.get('port')} — helper {ts.get('helper_version')}")
    ok("helper token set", bool(ts.get("token_set")))
    paths = ts.get("paths") or {}
    ok("Fizgig path configured", bool(paths.get("fizgig_root")),
       str(paths.get("fizgig_root") or "missing — training cannot start"))
    ok("Fizgig python configured", bool(paths.get("fizgig_python")),
       str(paths.get("fizgig_python") or "missing"))
    ok("ComfyUI start command configured", bool(paths.get("comfy_start_cmd")),
       "the helper restarts ComfyUI after training to hand the GPU back",
       warn_only=True)

    # ── 5. trigger collision ────────────────────────────────────────────────
    if a.name:
        print("\n5. trigger word")
        slug = re.sub(r"[^a-z0-9]+", "-", a.name.lower()).strip("-")
        want = "rbmn" + re.sub(r"[^a-z0-9]", "", slug)[:8]
        ds, _ = get(f"{APP}/api/lora/datasets", 30)
        used = {str(d.get("trigger") or "").lower()
                for d in ((ds or {}).get("datasets") or [])}
        ok(f"trigger {want!r} is free", want not in used,
           "autogen will suffix it automatically if not" if want in used else "",
           warn_only=True)
        chars, _ = get(f"{APP}/api/klein3/characters", 30)
        names = {c.get("slug") for c in ((chars or {}).get("characters") or [])}
        ok(f"character slug {slug!r} is new", slug not in names,
           "an existing character will be RESUMED, not replaced" if slug in names else "",
           warn_only=True)

    # ── verdict ─────────────────────────────────────────────────────────────
    print("\n" + "─" * 62)
    if PROBLEMS:
        print(f"❌ NO-GO — {len(PROBLEMS)} problem(s): " + "; ".join(PROBLEMS))
        print("   Fix these before starting; each one fails the run HOURS in.")
    elif WARNINGS:
        print(f"🟡 GO, with {len(WARNINGS)} note(s): " + "; ".join(WARNINGS))
    else:
        print("✅ GO — every dependency is up.")
    print("⚠ NOT checked: free disk on the trainer, and whether the boxes STAY "
          "up (one has rebooted mid-run before).")
    return 1 if PROBLEMS else 0


if __name__ == "__main__":
    raise SystemExit(main())
