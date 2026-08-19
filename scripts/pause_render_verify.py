"""🔊 Do the pause tags reach the AUDIO? Measure the silence, don't trust the plan.

`scripts/pause_tag_smoke.py` proves the PLANNER produces the right gaps. That is
not the same claim as "the finished wav has silence in it", and he reported the
pauses *"doesnt seem to do anything"* while every planner test was green. A
correct plan is not a correct render.

So this renders real narration with a deliberately HUGE tag and then runs
ffmpeg's `silencedetect` over the result, printing every silent stretch it
finds. If a 2-second gap is in the file, it is unmissable in that list; if it is
not, the concat is eating it and the number tells you so.

    python scripts\\pause_render_verify.py
    python scripts\\pause_render_verify.py --ms 2000 --voice <id>

⚠ It submits 2 real renders (a few seconds of GPU each).
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

SENT = ["The rail camp woke slowly.",
        "Nobody spoke until the coffee was poured."]


def call(url, data=None, timeout=900):
    req = urllib.request.Request(url, data=data,
                                 method="POST" if data is not None else "GET")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read()
            try:
                return r.status, json.loads(body.decode() or "{}"), body
            except ValueError:
                return r.status, {}, body
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw.decode() or "{}"), raw
        except ValueError:
            return e.code, {"detail": raw.decode("utf-8", "replace")[:300]}, raw


def silences(fp: Path, thresh_db: int = -40, min_s: float = 0.15) -> list:
    """Every silent stretch ffmpeg can find: [(start, end, duration), …]."""
    r = subprocess.run(
        ["ffmpeg", "-hide_banner", "-i", str(fp), "-af",
         f"silencedetect=noise={thresh_db}dB:d={min_s}", "-f", "null", "-"],
        capture_output=True, text=True, timeout=300)
    txt = r.stderr or ""
    out, start = [], None
    for m in re.finditer(r"silence_(start|end):\s*([0-9.]+)"
                         r"(?:\s*\|\s*silence_duration:\s*([0-9.]+))?", txt):
        kind, val, dur = m.group(1), float(m.group(2)), m.group(3)
        if kind == "start":
            start = val
        elif start is not None:
            out.append((start, val, float(dur) if dur else val - start))
            start = None
    return out


def render(base: str, vid: str, text: str, label: str) -> dict:
    st, r, _ = call(f"{base}/tts/generate", json.dumps({
        "voice_id": vid, "text": text, "pause_ms": 0, "sentence_pause_ms": 0,
        "seed": 4242, "pace": 1.0, "label": label}).encode())
    if st != 200:
        print(f"   ✗ {st} {str(r)[:200]}")
        return {}
    jid = r["id"]
    for _ in range(300):
        time.sleep(2)
        _s, job, _ = call(f"{base}/jobs/{jid}")
        if job.get("status") in ("done", "error"):
            break
    if job.get("status") != "done":
        print(f"   ✗ {str(job.get('error'))[:200]}")
        return {}
    _s, _j, raw = call(f"{base}/media/{jid}")
    fp = Path(tempfile.gettempdir()) / f"rbmn_pause_{jid}.wav"
    fp.write_bytes(raw)
    return {"id": jid, "seconds": job.get("seconds"), "file": fp,
            "chunks": job.get("chunks")}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--app", default="http://127.0.0.1:8899")
    ap.add_argument("--voice", default="")
    ap.add_argument("--ms", type=int, default=2000,
                    help="the tag to test with — big on purpose")
    a = ap.parse_args()
    base = f"{a.app.rstrip('/')}/api/audio-lab"

    st, j, _ = call(f"{base}/tts/voices")
    voice = (next((v for v in (j.get("voices") or []) if v["id"] == a.voice), None)
             if a.voice else
             next((v for v in (j.get("voices") or []) if v.get("ready")), None))
    if not voice:
        print("no ready voice — add one with a transcript first")
        return 1
    print(f"🔊 pause render verify — voice '{voice['name']}', tag {a.ms}ms\n")

    plain = " ".join(SENT)
    tagged = f"{SENT[0]} [pause {a.ms}] {SENT[1]}"
    runs = []
    for label, text in (("no tag", plain), (f"[pause {a.ms}]", tagged)):
        print(f"── {label}")
        res = render(base, voice["id"], text, f"🔊 verify · {label}")
        if not res:
            return 1
        sils = silences(res["file"])
        inner = [s for s in sils if s[0] > 0.2 and s[1] < (res["seconds"] or 0) - 0.2]
        print(f"   {res['seconds']:.2f}s of audio, {res['chunks']} chunk(s)")
        if inner:
            for s0, s1, d in inner:
                print(f"   · silence {s0:.2f}s → {s1:.2f}s  ({d:.2f}s)")
        else:
            print("   · no internal silence found")
        runs.append({"label": label, "res": res, "inner": inner,
                     "longest": max([d for _a, _b, d in inner], default=0.0)})
        res["file"].unlink(missing_ok=True)

    a0, a1 = runs[0]["longest"], runs[1]["longest"]
    grew = a1 - a0
    want = a.ms / 1000.0
    print()
    if a1 >= want * 0.7:
        print(f"✅ THE PAUSE IS IN THE AUDIO — longest internal silence went "
              f"{a0:.2f}s → {a1:.2f}s (asked for {want:.2f}s).")
        print(f"   Total length {runs[0]['res']['seconds']:.2f}s → "
              f"{runs[1]['res']['seconds']:.2f}s.")
        if grew < want * 0.6:
            print("   ⚠ but the TOTAL barely moved: splitting makes each piece "
                  "speak faster, which eats the gap you added.")
        return 0
    print(f"❌ THE PAUSE IS NOT IN THE AUDIO — longest internal silence is "
          f"{a1:.2f}s, expected about {want:.2f}s.")
    print("   Look at the concat: distinct gaps need distinct silence FILES, "
          "and a mismatched sample rate makes the demuxer drop them.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
