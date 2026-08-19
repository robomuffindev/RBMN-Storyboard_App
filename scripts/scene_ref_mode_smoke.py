"""🎛 Free smoke test for SCENE REF MODE (v1.277.37).

Two halves, and the split matters:

  1. the PURE resolution rules — precedence, the legacy `two_pass_enabled`
     spelling, the tolerated aliases. No app, no GPU, no DB.
  2. the LIVE route — that `/video-config` round-trips the field, rejects
     junk, and that a read ({}) does not invent one.

    python scripts\\scene_ref_mode_smoke.py                 # rules only
    python scripts\\scene_ref_mode_smoke.py --project <id>  # + the live route

⚠ With --project it WRITES the project's default (and restores it afterwards).

⚠⚠ `check()` RETURNS its result on purpose — the story-audio suite's first
version returned None and `if not check(...)` aborted it after one PASSING
line. A harness that stops early looks exactly like a feature that works.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# ⚠ loaded BY PATH, not as `backend.services.scene_ref_mode` — that package's
# __init__ imports the ComfyUI client, which needs `websocket`. A free tool
# that needs the app's dependency tree is not a free tool.
import importlib.util                                          # noqa: E402
_SRC = Path(__file__).resolve().parent.parent / "backend" / "services" / "scene_ref_mode.py"
_spec = importlib.util.spec_from_file_location("_srm", _SRC)
srm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(srm)

PASS = FAIL = 0


def check(label: str, ok: bool, detail: str = "") -> bool:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  PASS  {label}" + (f"  ({detail})" if detail else ""))
    else:
        FAIL += 1
        print(f"  FAIL  {label}" + (f"  ({detail})" if detail else ""))
    return ok


def call(url: str, data=None, method=""):
    req = urllib.request.Request(
        url, data=data, method=method or ("PUT" if data is not None else "GET"))
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(raw or "{}")
        except ValueError:
            return e.code, {"detail": raw[:200]}


def rules() -> None:
    print("🎛 resolution rules\n")
    check("the module default is full_reference — what the code ALREADY did",
          srm.DEFAULT == "full_reference", srm.DEFAULT)
    check("an unset project falls back to the default",
          srm.project_mode({}) == "full_reference")
    check("a project setting wins over the default",
          srm.project_mode({"scene_ref_mode": "t2i_swap"}) == "t2i_swap")
    check("a scene override beats the project",
          srm.resolve({"scene_ref_mode": "t2i_swap"},
                      {"scene_ref_mode": "full_reference"}) == "full_reference")
    check("'inherit' on a scene means ASK THE PROJECT",
          srm.resolve({"scene_ref_mode": "t2i_swap"},
                      {"scene_ref_mode": "inherit"}) == "t2i_swap")
    check("so does '' — the UI writes empty, older rows have nothing",
          srm.resolve({"scene_ref_mode": "t2i_swap"},
                      {"scene_ref_mode": ""}) == "t2i_swap")
    # ⭐ the compatibility rule this whole feature stands on
    check("LEGACY: two_pass_enabled alone reads as route 1",
          srm.resolve({}, {"two_pass_enabled": True}) == "t2i_swap")
    check("…but an explicit mode OUT-VOTES the legacy checkbox",
          srm.resolve({}, {"two_pass_enabled": True,
                           "scene_ref_mode": "full_reference"})
          == "full_reference")
    check("two_pass_enabled=False does NOT pin full_reference",
          srm.resolve({"scene_ref_mode": "t2i_swap"},
                      {"two_pass_enabled": False}) == "t2i_swap")
    check("wants_two_pass is true for t2i_swap only",
          srm.wants_two_pass("t2i_swap") and
          not srm.wants_two_pass("full_reference"))
    check("junk normalises to None (→ inherit), never to a route",
          srm.normalise("banana") is None and srm.normalise(None) is None)
    check("aliases land on the right route",
          srm.normalise("two_pass") == "t2i_swap" and
          srm.normalise("ref2v") == "full_reference")
    e = srm.explain({"scene_ref_mode": "t2i_swap"}, {})
    check("explain() names WHO decided — a bare value is unreadable in a log",
          e["source"] == "project" and e["mode"] == "t2i_swap", str(e))
    e2 = srm.explain({}, {"scene_ref_mode": "t2i_swap"})
    check("…and says 'scene' when the scene overrode it",
          e2["source"] == "scene" and e2["scene_override"] == "t2i_swap")


def live(app: str, project: str) -> None:
    print("\n🌐 the live /video-config route\n")
    base = f"{app.rstrip('/')}/api/projects/{project}/video-config"
    st, before = call(base, b"{}")
    if not check("a READ ({}) returns a config", st == 200, f"status {st}"):
        return
    original = before.get("scene_ref_mode")
    check("the read always carries a resolved scene_ref_mode",
          original in srm.MODES, str(original))

    st, j = call(base, json.dumps({"scene_ref_mode": "t2i_swap"}).encode())
    check("it accepts t2i_swap", st == 200 and j.get("scene_ref_mode") == "t2i_swap",
          f"status {st} {str(j)[:100]}")
    st, j = call(base, b"{}")
    check("and the value PERSISTS across a fresh read",
          j.get("scene_ref_mode") == "t2i_swap", str(j.get("scene_ref_mode")))
    st, j = call(base, json.dumps({"scene_ref_mode": "banana"}).encode())
    check("junk is REJECTED, not silently defaulted", st == 400, f"status {st}")
    # ⚠ 'inherit' has nothing above it at project level — it must not be stored
    st, j = call(base, json.dumps({"scene_ref_mode": "inherit"}).encode())
    check("'inherit' is refused as a PROJECT default", st == 400, f"status {st}")

    st, j = call(base, json.dumps(
        {"scene_ref_mode": original or "full_reference"}).encode())
    check("restored to what it was", st == 200, str(original))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--app", default="http://127.0.0.1:8899")
    ap.add_argument("--project", default="", help="also exercise the live route")
    a = ap.parse_args()
    rules()
    if a.project:
        live(a.app, a.project)
    print(f"\n{'ALL PASS' if not FAIL else 'FAILURES'}: {PASS} pass · {FAIL} fail")
    return 0 if not FAIL else 1


if __name__ == "__main__":
    sys.exit(main())
