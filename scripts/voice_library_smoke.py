"""🎤 Free smoke test for the VOICE LIBRARY (v1.277.37).

It generates its own 40-second reference with ffmpeg, so it needs no sample of
yours, no worker and no GPU — only the app and ffmpeg. What it proves:

  * a LONG upload is trimmed to the 12 s cap instead of being handed whole to
    a node that would cut it mid-word
  * the whole upload is kept as the SOURCE
  * a re-trim cuts from the SOURCE, not from the clip — trim to 3 s, then back
    to 10 s, and you get 10 s. If it cut the clip, you would get 3.
  * the details view answers "when, from what, and what did it make"
  * both audio streams come back playable

    python scripts\\voice_library_smoke.py
    python scripts\\voice_library_smoke.py --keep     # leave the voice behind

⚠ It creates a REAL voice named "🧪 smoke test voice" and deletes it at the
end. Nothing else is touched.

⚠⚠ `check()` RETURNS its result — a harness that stops early on its first
`if not check(...)` looks exactly like a feature that works (2026-08-17).
"""
from __future__ import annotations

import argparse
import json
import mimetypes
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import uuid
from pathlib import Path

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

PASS = FAIL = 0
CAP = 12.0


def check(label: str, ok: bool, detail: str = "") -> bool:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  PASS  {label}" + (f"  ({detail})" if detail else ""))
    else:
        FAIL += 1
        print(f"  FAIL  {label}" + (f"  ({detail})" if detail else ""))
    return ok


def call(url: str, data=None, ctype="application/json", method=""):
    req = urllib.request.Request(url, data=data,
                                 method=method or ("POST" if data is not None else "GET"))
    if data is not None:
        req.add_header("Content-Type", ctype)
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
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
            return e.code, {"detail": raw.decode("utf-8", "replace")[:200]}, raw


def multipart(fp: Path, fields: dict) -> tuple:
    """Hand-built multipart — the agent's `upload` kind posts raw bytes as
    application/zip, which a FastAPI `File(...)` route cannot parse."""
    boundary = "----rbmn" + uuid.uuid4().hex
    parts = []
    for k, v in fields.items():
        parts.append(f"--{boundary}\r\nContent-Disposition: form-data; "
                     f'name="{k}"\r\n\r\n{v}\r\n'.encode())
    ctype = mimetypes.guess_type(fp.name)[0] or "application/octet-stream"
    parts.append((f"--{boundary}\r\n"
                  f'Content-Disposition: form-data; name="file"; '
                  f'filename="{fp.name}"\r\n'
                  f"Content-Type: {ctype}\r\n\r\n").encode())
    body = b"".join(parts) + fp.read_bytes() + f"\r\n--{boundary}--\r\n".encode()
    return body, f"multipart/form-data; boundary={boundary}"


def make_sample(dest: Path, seconds: int = 40) -> bool:
    """A 40 s file that is SILENT for the first 2 s, then tones.

    The leading silence is the point: it is what the auto-start finder has to
    skip, and a clip that begins in silence wastes part of a 12 s budget."""
    if not shutil.which("ffmpeg"):
        return False
    r = subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i",
         f"sine=frequency=220:duration={seconds}",
         "-af", "volume=enable='lt(t,2)':volume=0",
         "-ac", "1", "-ar", "24000", str(dest)],
        capture_output=True, timeout=180)
    return r.returncode == 0 and dest.exists()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--app", default="http://127.0.0.1:8899")
    ap.add_argument("--keep", action="store_true")
    a = ap.parse_args()
    base = f"{a.app.rstrip('/')}/api/audio-lab"
    print("🎤 voice library smoke\n")

    tmp = Path(tempfile.gettempdir()) / f"rbmn_voice_{uuid.uuid4().hex[:6]}.wav"
    if not check("built a 40s test sample with ffmpeg", make_sample(tmp),
                 str(tmp)):
        print("  (ffmpeg is required — this lane cannot trim without it)")
        return 1

    st, j, _ = call(f"{base}/tts/voices")
    if not check("GET /tts/voices answers", st == 200, f"status {st}"):
        return 1
    cap = float(j.get("cap_seconds") or CAP)
    check("it publishes the 12s reference cap — the NODE's limit, not a style "
          "guide", abs(cap - 12.0) < 0.01, str(cap))

    # ⭐ .38: NO transcript on upload — it describes the CLIP, and the clip does
    # not exist until this call cuts it. The panel asks for it afterwards.
    body, ctype = multipart(tmp, {"name": "🧪 smoke test voice",
                                  "trim_start": "-1", "trim_seconds": "0"})
    st, v, _ = call(f"{base}/tts/voices", body, ctype)
    if not check("a 40s upload is ACCEPTED", st == 200,
                 f"status {st} {str(v)[:140]}"):
        return 1
    vid = v.get("id")
    check("…and CUT to the cap rather than passed on whole",
          0 < float(v.get("clip_seconds") or 0) <= cap + 0.3,
          f"clip {v.get('clip_seconds')}s of {v.get('source_seconds')}s")
    check("the whole upload is kept as the source", bool(v.get("has_source")),
          f"source {v.get('source_seconds')}s")
    check("the upload answers with a RECEIPT in words, not just a 200",
          "cut a" in (v.get("summary") or "").lower(),
          (v.get("summary") or "")[:90])
    check("…and says what to do next (listen, then transcribe)",
          "listen" in (v.get("next") or "").lower())
    check("a voice with no transcript is flagged, not silently accepted",
          v.get("needs_transcript") is True and v.get("ready") is False)
    trim = v.get("trim") or {}
    check("the auto start skipped the leading silence",
          float(trim.get("start") or 0) > 1.0,
          f"start {trim.get('start')}s (2s of silence at the head)")

    # ⭐ the rule this feature stands on: every cut comes off the SOURCE
    st, r1, _ = call(f"{base}/tts/voices/{vid}/trim",
                     json.dumps({"start": 5, "seconds": 3}).encode())
    check("re-trim to 3s works", st == 200 and
          abs(float(r1.get("clip_seconds") or 0) - 3) < 0.4,
          f"{r1.get('clip_seconds')}s")
    st, r2, _ = call(f"{base}/tts/voices/{vid}/trim",
                     json.dumps({"start": 5, "seconds": 10}).encode())
    check("…then BACK to 10s — proving the cut came off the SOURCE, not the "
          "3s clip", st == 200 and abs(float(r2.get("clip_seconds") or 0) - 10) < 0.4,
          f"{r2.get('clip_seconds')}s (a clip-of-a-clip would cap at 3s)")
    st, r3, _ = call(f"{base}/tts/voices/{vid}/trim",
                     json.dumps({"start": 0, "seconds": 60}).encode())
    check("a 60s request is clamped to the cap",
          abs(float(r3.get("clip_seconds") or 0) - cap) < 0.4,
          f"{r3.get('clip_seconds')}s")

    st, _j, raw = call(f"{base}/tts/voices/{vid}/audio?which=clip")
    check("the CLIP streams back", st == 200 and len(raw) > 1000,
          f"status {st}, {len(raw)} bytes")
    st, _j, raw2 = call(f"{base}/tts/voices/{vid}/audio?which=source")
    check("the SOURCE streams back and is bigger",
          st == 200 and len(raw2) > len(raw),
          f"{len(raw2)} vs {len(raw)} bytes")

    st, d, _ = call(f"{base}/tts/voices/{vid}")
    check("the details view answers", st == 200 and d.get("id") == vid,
          f"status {st}")
    check("it carries when it was made and what it came from",
          bool(d.get("at")) and bool(d.get("source_filename")),
          f"{d.get('at')} · {d.get('source_filename')}")
    check("renders / projects / stories are present (empty is correct here)",
          isinstance(d.get("renders"), list) and isinstance(d.get("stories"), list),
          f"{d.get('render_count')} render(s)")

    # ⭐ generate must REFUSE a voice that cannot possibly clone, and say why
    st, g, _ = call(f"{base}/tts/generate",
                    json.dumps({"voice_id": vid, "text": "hi"}).encode())
    check("generate REFUSES a voice with no transcript (F5 needs the alignment)",
          st == 400 and "transcript" in str(g.get("detail", "")).lower(),
          f"status {st}")

    st, u, _ = call(f"{base}/tts/voices/{vid}/update",
                    json.dumps({"transcript": "the words in THIS window"}).encode())
    check("the transcript can be set (and corrected after a re-cut)",
          st == 200 and u.get("transcript") == "the words in THIS window")
    check("…and the voice then reads as READY",
          u.get("ready") is True and u.get("needs_transcript") is False)

    # ⚠ Do NOT call /tts/generate with the real voice here — the first version
    # did, got a 200, and put an actual test-tone render on the fleet's queue.
    # A free suite that submits GPU work is not free. The guard rail worth
    # checking from here is the one that costs nothing:
    st, _g, _ = call(f"{base}/tts/generate",
                     json.dumps({"voice_id": "nope", "text": "hi"}).encode())
    check("generate refuses an unknown voice instead of rendering silence",
          st == 404, f"status {st}")

    if not a.keep:
        st, _d, _ = call(f"{base}/tts/voices/{vid}/delete", b"{}")
        check("delete removes it", st == 200)
        st, j2, _ = call(f"{base}/tts/voices")
        check("…and it is gone from the list",
              all(x.get("id") != vid for x in (j2.get("voices") or [])))
    tmp.unlink(missing_ok=True)

    print(f"\n{'ALL PASS' if not FAIL else 'FAILURES'}: {PASS} pass · {FAIL} fail")
    return 0 if not FAIL else 1


if __name__ == "__main__":
    sys.exit(main())
