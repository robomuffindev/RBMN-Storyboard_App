"""⏱ Which way does F5's `speed` knob actually go? MEASURE it (v1.277.39).

The node's own tooltip on the box says:

    "Speed. >1.0 slower. <1.0 faster"

which is the OPPOSITE of upstream F5-TTS, where `speed` divides the estimated
duration (`duration = ref_len + gen_len / speed`), i.e. >1.0 is FASTER. One of
those is wrong, our UI just says "speed", and a knob whose direction you have to
guess is worse than no knob: turn it the wrong way while chasing a slower read
and you get a faster one, twice as fast as you started.

So do not argue about it — render the same sentence at several values on a real
box and MEASURE the output duration. Longer output = slower delivery. There is
no interpretation left after that.

    python scripts\\tts_speed_probe.py --voice <id>
    python scripts\\tts_speed_probe.py --voice <id> --speeds 0.7,0.85,1.0,1.15,1.3

⚠ This SUBMITS real renders (one per speed, one short sentence each) — a few
seconds of GPU per value, on one box, serially.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parent))          # scripts/
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))   # repo root

SENTENCE = ("The rail camp woke slowly, and the whistle carried a long way "
            "across the cold valley floor.")


def call(url: str, data=None, timeout=600):
    req = urllib.request.Request(url, data=data,
                                 method="POST" if data is not None else "GET")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read()
            try:
                return r.status, json.loads(body.decode() or "{}")
            except ValueError:
                return r.status, {}
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(raw or "{}")
        except ValueError:
            return e.code, {"detail": raw[:300]}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--app", default="http://127.0.0.1:8899")
    ap.add_argument("--voice", default="", help="voice id (default: the first "
                                                "one with a transcript)")
    ap.add_argument("--speeds", default="0.8,1.0,1.2")
    ap.add_argument("--host", default="", help="pin to one worker")
    ap.add_argument("--keep", action="store_true", help="keep the renders")
    a = ap.parse_args()
    base = f"{a.app.rstrip('/')}/api/audio-lab"

    st, j = call(f"{base}/tts/voices")
    if st != 200:
        print(f"cannot list voices: {st}")
        return 1
    voices = j.get("voices") or []
    vid = a.voice
    if not vid:
        v = next((x for x in voices if x.get("ready")), None)
        if not v:
            print("no ready voice (one with a transcript) — add one first")
            return 1
        vid = v["id"]
    voice = next((x for x in voices if x["id"] == vid), {})
    print(f"⏱ F5 speed probe — voice '{voice.get('name', vid)}' "
          f"({voice.get('clip_seconds')}s reference)\n")
    print(f'   line: "{SENTENCE}"\n')

    rows = []
    for sp in [float(s) for s in a.speeds.split(",") if s.strip()]:
        st, r = call(f"{base}/tts/generate", json.dumps({
            "voice_id": vid, "text": SENTENCE, "speed": sp, "pause_ms": 0,
            "seed": 4242,                 # ⭐ same seed: otherwise this measures seeds
            "host": a.host, "label": f"⏱ speed {sp}"}).encode())
        if st != 200:
            print(f"   speed {sp}: ✗ {st} {str(r)[:120]}")
            continue
        jid = r.get("id")
        secs = elapsed = 0.0
        for _ in range(300):
            time.sleep(2)
            _s, job = call(f"{base}/jobs/{jid}")
            if job.get("status") == "done":
                secs = float(job.get("seconds") or 0)
                elapsed = float(job.get("elapsed_s") or 0)
                break
            if job.get("status") == "error":
                print(f"   speed {sp}: ✗ {str(job.get('error'))[:160]}")
                break
        else:
            print(f"   speed {sp}: ⚠ never finished")
            continue
        if secs:
            rows.append((sp, secs, elapsed, jid))
            print(f"   speed {sp:<5} → {secs:6.2f}s of audio "
                  f"(rendered in {elapsed:.0f}s)")

    if len(rows) < 2:
        print("\nnot enough results to call a direction")
        return 1
    lo, hi = rows[0], rows[-1]
    print()
    if hi[1] > lo[1] * 1.05:
        print(f"⭐ HIGHER speed = LONGER audio ⇒ **>1.0 is SLOWER** on this "
              f"node (the tooltip is right, upstream F5 is inverted here).")
        print(f"   {lo[0]} → {lo[1]:.2f}s · {hi[0]} → {hi[1]:.2f}s")
    elif lo[1] > hi[1] * 1.05:
        print(f"⭐ HIGHER speed = SHORTER audio ⇒ **>1.0 is FASTER** (upstream "
              f"behaviour; the node's tooltip is WRONG).")
        print(f"   {lo[0]} → {lo[1]:.2f}s · {hi[0]} → {hi[1]:.2f}s")
    else:
        print("⚠ the durations barely moved — `speed` may not be reaching the "
              "node at all. Check the graph's inputs before trusting the knob.")
    if not a.keep:
        for _sp, _s, _e, jid in rows:
            call(f"{a.app.rstrip('/')}/api/audio-lab/jobs/{jid}", b"", 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
