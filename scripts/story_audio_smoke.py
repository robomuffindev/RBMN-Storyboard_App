"""🎙 Free smoke test for the story NARRATION RECORDING lane (v1.277.30).

Uploads a real audio file to a story, checks it comes back playable with a
measured duration, then deletes it. No GPU, no LLM, no renders — and it is the
only way to exercise a multipart route from here, because the agent's `upload`
job kind posts RAW BYTES with `Content-Type: application/zip`, which a
`File(...)` route cannot parse (learned the hard way, 2026-08-17).

    python scripts\\story_audio_smoke.py --world <wid> --story <sid> --file x.mp3

⚠ It uploads to a REAL story and then deletes, so it will REPLACE an existing
recording on that story. Pass a throwaway story, or `--keep` to leave it.
"""
from __future__ import annotations

import argparse
import json
import mimetypes
import sys
import urllib.error
import urllib.request
import uuid
from pathlib import Path

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

PASS = FAIL = 0


def check(label: str, ok: bool, detail: str = "") -> bool:
    """⚠ RETURNS the result — the first version returned None and the very
    first `if not check(...)` aborted the suite after one passing line."""
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  PASS  {label}" + (f"  ({detail})" if detail else ""))
    else:
        FAIL += 1
        print(f"  FAIL  {label}" + (f"  ({detail})" if detail else ""))
    return ok


def multipart(fp: Path) -> tuple:
    """Build a multipart/form-data body by hand — stdlib only."""
    boundary = "----rbmn" + uuid.uuid4().hex
    ctype = mimetypes.guess_type(fp.name)[0] or "application/octet-stream"
    head = (f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; '
            f'filename="{fp.name}"\r\n'
            f"Content-Type: {ctype}\r\n\r\n").encode()
    tail = f"\r\n--{boundary}--\r\n".encode()
    return head + fp.read_bytes() + tail, f"multipart/form-data; boundary={boundary}"


def call(url: str, data=None, ctype="application/json", method=""):
    req = urllib.request.Request(url, data=data,
                                 method=method or ("POST" if data else "GET"))
    if data:
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
            return e.code, {}, raw


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--app", default="http://127.0.0.1:8899")
    ap.add_argument("--world", required=True)
    ap.add_argument("--story", required=True)
    ap.add_argument("--file", required=True)
    ap.add_argument("--keep", action="store_true",
                    help="do not delete the recording afterwards")
    a = ap.parse_args()
    fp = Path(a.file)
    if not fp.exists():
        print(f"no such file: {fp}")
        return 1
    base = f"{a.app.rstrip('/')}/api/storyworld/worlds/{a.world}/stories/{a.story}"
    print(f"🎙 story audio smoke — {fp.name} ({fp.stat().st_size / 1048576:.1f} MB)\n")

    body, ctype = multipart(fp)
    st, j, _ = call(f"{base}/narration/audio", body, ctype)
    if not check("POST narration/audio accepts the upload", st == 200,
                 f"status {st} {str(j)[:120]}"):
        return 1
    meta = j.get("audio") or {}
    check("it records the original filename", meta.get("filename") == fp.name,
          str(meta.get("filename")))
    check("it stores the byte size", int(meta.get("bytes") or 0) == fp.stat().st_size,
          f"{meta.get('bytes')} vs {fp.stat().st_size}")
    check("a playable format is marked playable", bool(meta.get("playable")),
          str(meta.get("ext")))
    # ⚠ the duration is MEASURED with ffprobe, unlike the text lane's word-count
    # estimate — a 0 here means ffprobe is missing, not that the file is empty
    check("the duration was measured", float(meta.get("seconds") or 0) > 0,
          f"{meta.get('seconds')}s (0 = ffprobe not on the app host)")

    st, _j, raw = call(f"{base}/narration/audio")
    check("GET streams the audio back", st == 200 and len(raw) == fp.stat().st_size,
          f"status {st}, {len(raw)} bytes")

    st, j, _ = call(f"{a.app.rstrip('/')}/api/storyworld/worlds/{a.world}")
    story = next((s for s in (j.get("stories") or [])
                  if s.get("id") == a.story), {})
    check("the story carries it", bool((story.get("narration_audio") or {}).get("id")),
          str((story.get("narration_audio") or {}).get("filename")))

    if not a.keep:
        st, j, _ = call(f"{base}/narration/audio/delete", b"{}")
        check("delete removes it", st == 200 and j.get("deleted") is True, str(j))
        st, _j, _ = call(f"{base}/narration/audio")
        check("and then it 404s", st == 404, f"status {st}")

    print(f"\n{'ALL PASS' if not FAIL else 'FAILURES'}: {PASS} pass · {FAIL} fail")
    return 0 if not FAIL else 1


if __name__ == "__main__":
    sys.exit(main())
