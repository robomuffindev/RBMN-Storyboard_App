"""🎼 Free smoke test for the score / arc-pairing lane (v1.277.16).

ZERO renders, ZERO LLM calls, ZERO GPU. It drives the paper half of the lane
end to end against the live backend — create, normalise, edit, re-open, list,
delete — and checks the two properties the lane exists for:

    * the cue lengths SUM to the requested total (exact-length pairing)
    * every cue is clamped into the 5-300s the engines accept

Then it exercises the guards without spending a render: rendering a score
whose engine no box is ready for must 409 and leave every cue on PAPER (never
"claimed" with no job — the wedge).

Usage:  python scripts\\audio_score_smoke.py [--host http://127.0.0.1:8899]
        python scripts\\audio_score_smoke.py --render     (spends 2 short renders)

⚠ Count the `check(` CALL SITES in this file, never the PASS lines in its
output — a grep that matches its own summary line inflates by one, silently
(the v1.276.47 lesson).
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request

# the agent runs this under cp1252 — emoji in a print() would kill the suite
# before its first check (the same reason every other script here does this).
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

PASS = 0
FAIL = 0
SKIP = 0


def check(label: str, ok: bool, detail: str = "") -> bool:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  PASS  {label}" + (f"  ({detail})" if detail else ""))
    else:
        FAIL += 1
        print(f"  FAIL  {label}" + (f"  ({detail})" if detail else ""))
    return ok


def skip(label: str, why: str) -> None:
    global SKIP
    SKIP += 1
    print(f"  SKIP  {label}  ({why})")


def call(host: str, path: str, body=None, method: str = ""):
    url = host.rstrip("/") + path
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(
        url, data=data, method=method or ("POST" if data is not None else "GET"),
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        # the BODY is the answer — str(HTTPError) carries none of it
        try:
            return e.code, json.loads(e.read().decode() or "{}")
        except Exception:                                      # noqa: BLE001
            return e.code, {}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="http://127.0.0.1:8899")
    ap.add_argument("--render", action="store_true",
                    help="also spend two SHORT (5s) renders end to end")
    a = ap.parse_args()
    H = a.host
    print(f"🎼 score smoke against {H}\n")

    # ── 1. sources ───────────────────────────────────────────────────────────
    st, d = call(H, "/api/audio-lab/score/sources")
    check("GET /sources answers", st == 200, f"status {st}")
    check("/sources carries worlds+projects+scores",
          all(k in d for k in ("worlds", "projects", "scores")), str(list(d))[:80])

    # engines that are actually ready right now (for the render guard below)
    st, ov = call(H, "/api/audio-lab/overview")
    ready = {e for w in (ov.get("workers") or [])
             for e, v in (w.get("engines") or {}).items() if v.get("ready")}
    print(f"  ..  engines ready on the fleet: {sorted(ready) or 'none'}")

    # ── 2. manual score: normalisation is the whole point ────────────────────
    cues = [
        {"name": "Act I", "seconds": 40, "caption": "sparse baritone guitar, dry room"},
        {"name": "Act II", "seconds": 40, "caption": "add brushed drums, rising"},
        {"name": "Act III", "seconds": 40, "caption": "full band, wide reverb"},
    ]
    st, s = call(H, "/api/audio-lab/score/manual",
                 {"title": "SMOKE score", "engine": "ace15",
                  "total_seconds": 150, "cues": cues})
    if not check("POST /manual creates a score", st == 200, f"status {st} {str(s)[:120]}"):
        return 1
    sid = s.get("id") or ""
    total = sum(c["seconds"] for c in s.get("cues") or [])
    check("cue lengths SUM to the requested total", abs(total - 150) < 0.5,
          f"{total} vs 150")
    check("the adjustment is recorded on the cue it moved",
          any(abs(c.get("adjusted_by") or 0) > 0 for c in s["cues"]),
          json.dumps([c.get("adjusted_by") for c in s["cues"]]))
    check("every cue starts on PAPER",
          all(c.get("status") == "paper" for c in s["cues"]))
    check("progress block is published",
          (s.get("progress") or {}).get("cues") == 3, str(s.get("progress"))[:80])

    # ── 3. clamping ──────────────────────────────────────────────────────────
    st, s2 = call(H, "/api/audio-lab/score/manual",
                  {"title": "SMOKE clamp", "engine": "ace15",
                   "cues": [{"name": "too short", "seconds": 1},
                            {"name": "too long", "seconds": 9999}]})
    ok = st == 200 and s2["cues"][0]["seconds"] == 5 and s2["cues"][1]["seconds"] == 300
    check("out-of-range cue lengths are clamped to 5-300",
          ok, json.dumps([c["seconds"] for c in (s2.get("cues") or [])]))
    sid2 = s2.get("id") or ""

    # ── 4. edit, positional ──────────────────────────────────────────────────
    edited = [dict(c) for c in s["cues"]]
    edited[0]["name"] = "Act I (renamed)"
    edited[0]["seconds"] = 60
    st, s3 = call(H, f"/api/audio-lab/score/{sid}/cues",
                  {"cues": edited, "total_seconds": 0})
    check("POST /{sid}/cues saves an edit", st == 200 and
          s3["cues"][0]["name"] == "Act I (renamed)", f"status {st}")
    check("with total_seconds=0 the typed lengths are left alone",
          abs(s3["cues"][0]["seconds"] - 60) < 0.01,
          str(s3["cues"][0]["seconds"]))
    check("the edit re-totals the score",
          abs(s3.get("total_seconds", 0) - sum(c["seconds"] for c in s3["cues"])) < 0.5)

    # ── 5. read paths + route order ──────────────────────────────────────────
    st, one = call(H, f"/api/audio-lab/score/{sid}")
    check("GET /{sid} returns the score", st == 200 and one.get("id") == sid)
    st, lst = call(H, "/api/audio-lab/score/list")
    ids = [x.get("id") for x in (lst.get("scores") or [])]
    check("GET /list is NOT swallowed by GET /{sid}", st == 200 and sid in ids,
          f"status {st}, {len(ids)} scores")
    st, _ = call(H, "/api/audio-lab/score/nosuchscore_zz")
    check("an unknown id 404s (and does not 500)", st == 404, f"status {st}")

    # ── 6. the render guard, without spending a render ───────────────────────
    # pick an engine NO box is ready for — with four engines there is usually
    # one (the quality ACE lanes are ~19 GB and stage later than the rest)
    unready = next((e for e in ("ace15_sft", "ace15_base", "minimax3", "ace15")
                    if e not in ready), "")
    st, s4 = call(H, "/api/audio-lab/score/manual",
                  {"title": "SMOKE guard", "engine": unready or "minimax3",
                   "cues": [{"name": "guard", "seconds": 5, "caption": "x"}]})
    gid = s4.get("id") or ""
    if not unready:
        skip("a score whose engine is not ready 409s",
             "every engine IS ready on this fleet — the guard cannot be provoked")
    else:
        st, r = call(H, f"/api/audio-lab/score/{gid}/render", {"only": []})
        check("rendering an unready engine 409s", st == 409, f"status {st}")
        st, after = call(H, f"/api/audio-lab/score/{gid}")
        check("a failed render leaves NO cue stuck at 'claimed'",
              all(c.get("status") in ("paper", "error") for c in after["cues"]),
              json.dumps([c.get("status") for c in after["cues"]]))
    call(H, f"/api/audio-lab/score/{gid}/delete", {})

    # ── 7. optional: two real 5s renders, fanned ─────────────────────────────
    if a.render:
        eng = "ace15" if "ace15" in ready else ("minimax3" if "minimax3" in ready else "")
        if not eng:
            skip("live render", "no music engine is ready on any box")
        else:
            st, s5 = call(H, "/api/audio-lab/score/manual",
                          {"title": "SMOKE live", "engine": eng,
                           "cues": [{"name": "A", "seconds": 5, "caption": "solo acoustic guitar, dry"},
                                    {"name": "B", "seconds": 5, "caption": "solo piano, soft"}]})
            lid = s5["id"]
            st, r = call(H, f"/api/audio-lab/score/{lid}/render", {"only": []})
            check("render starts both cues", st == 200 and len(r.get("started") or []) == 2,
                  json.dumps(r.get("started"))[:160])
            boxes = {x["worker"] for x in (r.get("started") or [])}
            if len(boxes) < 2:
                skip("cues fan across MORE THAN ONE box",
                     f"only {len(boxes)} box(es) ready for {eng}")
            else:
                check("cues fan across more than one box", True, ", ".join(sorted(boxes)))
            deadline = time.time() + 900
            fin = {}
            while time.time() < deadline:
                time.sleep(5)
                _, fin = call(H, f"/api/audio-lab/score/{lid}")
                if all(c.get("status") in ("done", "error") for c in fin["cues"]):
                    break
            check("both cues finished", all(c.get("status") == "done" for c in fin.get("cues") or []),
                  json.dumps([c.get("status") for c in fin.get("cues") or []]))
            print(f"  ..  live score left in place as {lid} (delete it from the panel)")

    # ── cleanup ──────────────────────────────────────────────────────────────
    for x in (sid, sid2):
        if x:
            call(H, f"/api/audio-lab/score/{x}/delete", {})
    st, lst = call(H, "/api/audio-lab/score/list")
    check("deleted scores are gone",
          sid not in [x.get("id") for x in (lst.get("scores") or [])])

    print(f"\n{'ALL PASS' if not FAIL else 'FAILURES'}: "
          f"{PASS} pass · {FAIL} fail · {SKIP} skip")
    return 0 if not FAIL else 1


if __name__ == "__main__":
    sys.exit(main())
