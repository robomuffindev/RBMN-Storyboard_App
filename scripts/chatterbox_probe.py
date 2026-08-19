"""🗣 CHATTERBOX — first render, and the crash-template question, MEASURED.

Two jobs:

**1. Does it work at all?** Render a real line through Chatterbox on a worker
and check the cue/SRT machinery still holds (it should — Chatterbox goes through
the same chunk → concat → cue path as F5).

**2. ⚠⚠ THE `crash_protection_template` TRAP.** `ChatterBoxEngineNode` defaults
it to **`"hmm ,, {seg} hmm ,,"`** — it pads SHORT segments to stop the model
crashing, and the padding is TEXT, so it may be SPOKEN. Our chapter lane chunks
per SENTENCE, so short segments are the common case: left at the default,
*"He ran."* could come back with an audible **"hmm"** either side.

I cannot listen. But I can MEASURE: render the same two-word line under each
template and compare durations. If "hmm" is being spoken, the default is
markedly longer than the comma variant for identical words. That decides our
default on evidence instead of on a guess about what a tooltip means.

    python scripts/chatterbox_probe.py
    python scripts/chatterbox_probe.py --voice <id>

⚠ STDLIB ONLY. Costs a few short GPU renders, nothing else.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request

HOST = "http://127.0.0.1:8899"
FAILURES: list = []
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")   # type: ignore
except Exception:                                                # noqa: BLE001
    pass

SHORT = "He ran."
LONG = ("The tide came in on a Tuesday and took the ground floor of the "
        "counting house.")


def _req(method: str, path: str, body=None, timeout=180):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(HOST + path, data=data, method=method)
    if data is not None:
        r.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            b = resp.read()
            return (json.loads(b.decode()) if b else {}), resp.status
    except urllib.error.HTTPError as e:
        t = e.read().decode("utf-8", "replace")
        try:
            return json.loads(t), e.code
        except ValueError:
            return {"detail": t[:300]}, e.code
    except Exception as e:                                       # noqa: BLE001
        return {"detail": f"{type(e).__name__}: {e}"}, 0


def check(label: str, ok: bool, detail: str = "") -> bool:
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"  — {detail}" if detail else ""))
    if not ok:
        FAILURES.append(label)
    return bool(ok)


def render(voice_id: str, text: str, template: str, label: str,
           seed: int = 4242) -> dict:
    body = {"voice_id": voice_id, "engine": "chatterbox", "text": text,
            "pause_ms": 0, "sentence_pause_ms": 0, "seed": seed,
            "label": label}
    if template is not None:
        body["cb_crash_template"] = template
    r, code = _req("POST", "/api/audio-lab/tts/generate", body)
    if code != 200 or not r.get("id"):
        print(f"    ⚠ start failed ({code}): {str(r.get('detail'))[:140]}")
        return {}
    jid = r["id"]
    t0 = time.time()
    while time.time() - t0 < 900:
        time.sleep(4)
        j, _ = _req("GET", f"/api/audio-lab/jobs/{jid}")
        if j.get("status") not in ("queued", "running"):
            return j
    return {"status": "timeout"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--voice", default="")
    a = ap.parse_args()
    print("🗣 Chatterbox — first render + the crash-template question\n")

    ov, code = _req("GET", "/api/audio-lab/overview")
    ready = [w["host"] for w in (ov.get("workers") or [])
             if ((w.get("engines") or {}).get("chatterbox") or {}).get("ready")]
    if not check("a worker reports Chatterbox ready", bool(ready),
                 ", ".join(ready) or "none"):
        print("\n  install it: python scripts/install_chatterbox.py --host <box>")
        return 1

    vs, _ = _req("GET", "/api/audio-lab/tts/voices")
    voices = vs.get("voices") or []
    vid = a.voice
    if not vid:
        # ⭐ prefer a voice F5 could NOT use (no transcript) — it proves the
        # zero-shot claim rather than just repeating what F5 already did.
        pick = next((v for v in voices
                     if "chatterbox" in (v.get("engines") or [])
                     and v.get("needs_transcript")), None)
        pick = pick or next((v for v in voices
                             if "chatterbox" in (v.get("engines") or [])), None)
        if not pick:
            print("  ⚠ no voice has a clip"); return 1
        vid = pick["id"]
        print(f"  voice: {pick['name']}"
              f"{'  (NO transcript — F5 could not use this one)' if pick.get('needs_transcript') else ''}")

    # ── 1. does it render at all ────────────────────────────────────────────
    print("\n1. a real render")
    j = render(vid, LONG, None, "chatterbox first render")
    if not check("it finishes", j.get("status") == "done",
                 f"{j.get('status')}: {str(j.get('error'))[:160]}"):
        return 1
    secs = float(j.get("seconds") or 0)
    cues = j.get("cues") or []
    print(f"      {secs}s in {j.get('elapsed_s')}s on {j.get('worker')}")
    check("it produced audio", secs > 0.5, f"{secs}s")
    check("⭐ the cue machinery still holds", len(cues) == 1 and
          abs(float(cues[0]["end"]) - secs) <= 0.05,
          f"{len(cues)} cue(s), end {cues[0]['end'] if cues else '—'} vs {secs}")
    check("…and it was loudness-normalised", bool(j.get("loudness")),
          str(j.get("loudness"))[:70])
    # ⚠ the SRT route returns text/plain — `_req` json-decodes and would
    # report 0. Read it raw. (The check failed on WORKING code twice; a probe
    # that lies about its own transport is worse than no probe.)
    try:
        with urllib.request.urlopen(
                f"{HOST}/api/audio-lab/jobs/{j['id']}/srt", timeout=60) as resp:
            body = resp.read().decode("utf-8", "replace")
            sc = resp.status
    except Exception as e:                                       # noqa: BLE001
        body, sc = f"{type(e).__name__}: {e}", 0
    check("the SRT still writes", sc == 200 and "-->" in body,
          f"{sc}: {body[:60]}")

    # ── 2. ⚠⚠ the crash-template question, measured ─────────────────────────
    print("\n2. ⚠⚠ does the default template SPEAK 'hmm'?")
    print(f'      rendering "{SHORT}" under each template, same seed')
    trials = [('"hmm ,, {seg} hmm ,," (the NODE default)', "hmm ,, {seg} hmm ,,"),
              ('",, {seg} ,," (ours — commas only)', ",, {seg} ,,"),
              ('"{seg}" (no padding at all)', "{seg}")]
    got = []
    for label, tpl in trials:
        r = render(vid, SHORT, tpl, f"cb template {tpl}")
        s = float(r.get("seconds") or 0) if r.get("status") == "done" else 0.0
        got.append((label, tpl, s, r.get("status")))
        print(f"      {s:6.2f}s   {label}"
              + ("" if r.get("status") == "done" else f"   [{r.get('status')}]"))
    ok = [g for g in got if g[2] > 0]
    if not check("at least two templates rendered", len(ok) >= 2,
                 f"{len(ok)}/3"):
        return 1
    d = dict((g[1], g[2]) for g in ok)
    hmm, commas, bare = (d.get("hmm ,, {seg} hmm ,,"), d.get(",, {seg} ,,"),
                         d.get("{seg}"))
    # ⭐ THE MEASUREMENT, INTERPRETED. Padding is TEXT: "hmm" is a WORD (it gets
    # spoken) and "," is a PAUSE (it adds silence). Either way the extra time
    # lands INSIDE the cue, so a scene built from it opens or closes on
    # something nobody wrote. Shortest wins for narration.
    if bare:
        for label, val in (("the node default (hmm)", hmm), ("commas", commas)):
            if val:
                print(f"      {label}: {val - bare:+.2f}s vs no padding "
                      f"({(val - bare) / bare * 100:+.0f}%)")
        best = min((v, k) for k, v in d.items() if v)[1]
        print(f"\n      shortest for identical words: {best!r}")
        check("⭐⭐ NO PADDING is the shortest — nothing is being added to the "
              "audio", best == "{seg}",
              f"{best!r} was shorter; padding may be helping, re-listen")
    # a genuinely tiny line is the case the template exists to protect
    r = render(vid, "Yes.", "{seg}", "cb tiny line")
    check("⭐ …and an ultra-short line still renders WITHOUT padding",
          r.get("status") == "done" and float(r.get("seconds") or 0) > 0.2,
          f"{r.get('status')} {r.get('seconds')}s — if this crashes, the "
          f"padding template is earning its keep and should stay on")
    print("\n      ⭐ Judge by ear too — duration proves something is being "
          "ADDED, not whether it sounds good.")

    print()
    if FAILURES:
        print(f"❌ {len(FAILURES)} FAILURE(S): " + " · ".join(FAILURES))
        return 1
    print("✅ ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
