"""🔧 Put FFmpeg's SHARED DLLs inside each worker's ComfyUI python — no hands.

**The problem** (measured 2026-08-18): every F5-TTS render on the fleet dies in
`F5TTSAudioInputs` with `Could not load libtorchcodec`. F5 decodes its reference
through torchcodec, which `dlopen()`s FFmpeg's SHARED libraries at runtime.
`ffmpeg.exe` on PATH is not enough — the loader needs `avcodec-*.dll`,
`avformat-*.dll`, `avutil-*.dll` &co, which only the **full-SHARED** build ships.

**Why this script exists.** The deployed helper (v1.220) has no "copy a file"
route, so the obvious answer is "remote into three boxes and drag DLLs". It has
two routes that, together, do the job without touching a keyboard on any box:

  1. `POST /datasets/<name>` takes a **raw ZIP body** and extracts it on the box
     (it refuses absolute and `..` members — this does not defeat that, it just
     uses the folder it is given).
  2. `POST /install/pip` runs `python -m pip` with **the ComfyUI embedded
     python**, and pip's `--target` writes wherever you point it.

So: build a tiny wheel whose payload is the DLLs *at the top level*, ship it to
each box as a "dataset", and have that box's own pip unpack it **into
`python_embeded\\`** — the directory Windows searches FIRST for a process's
dependent DLLs, because it is where `python.exe` itself lives.

    python scripts\\install_ffmpeg_shared.py            # report, change nothing
    python scripts\\install_ffmpeg_shared.py --apply    # install on every box
    python scripts\\install_ffmpeg_shared.py --apply --restart-comfy

Then prove it, because an install is not a working decode:

    python scripts\\tts_doctor.py --probe

⚠ FFmpeg **7**, not 8: torchcodec supports 4-7 on Windows and 8 only on
macOS/Linux. The script REFUSES a build whose `avutil` major says 8 (60).
⚠ It uses the dataset name `rbmn-ffmpeg-shared`, and the helper DELETES an
existing dataset folder of the same name before extracting. Do not name a
training dataset that.
⚠ The download is ~100 MB and happens ONCE, here; the boxes are fed from this
machine over the LAN, which is his download-once rule.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import re
import shutil
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))   # scripts/
sys.path.insert(0, str(ROOT))

CACHE = ROOT / ".cache"
DATASET_NAME = "rbmn-ffmpeg-shared"
WHEEL_NAME = "rbmn_ffmpeg_shared-7.1.0-py3-none-any.whl"

#: BtbN publishes win64 **shared** zips (gyan.dev's shared builds are .7z, which
#: would need a 7-Zip on the app host). ⚠⚠ DO NOT hardcode an asset FILENAME:
#: the rolling `latest` tag is rebuilt and its n7.x assets get rotated off it —
#: `ffmpeg-n7.1-latest-win64-gpl-shared-7.1.zip` 404'd here while the asset
#: listing still showed it. The release list is asked instead, newest first.
API = "https://api.github.com/repos/BtbN/FFmpeg-Builds/releases?per_page=40"
#: FFmpeg 7 first because torchcodec's Windows support for 8 is not something
#: to bet a fleet on; `--allow-ffmpeg8` opts in if no 7 build can be found.
WANT_7 = re.compile(r"ffmpeg-n7\.[0-9.]*.*win64-(gpl|lgpl)-shared.*\.zip$", re.I)
WANT_ANY = re.compile(r"ffmpeg-.*win64-(gpl|lgpl)-shared.*\.zip$", re.I)

#: what torchcodec actually loads (it builds a filtergraph, so avfilter too)
NEEDED = ("avcodec", "avformat", "avutil", "avfilter", "swresample", "swscale",
          "avdevice", "postproc")


def helpers() -> list:
    from _fleet import helpers as _h        # stdlib only — see scripts/_fleet.py
    return _h()


def hget(h: dict, path: str, timeout=60):
    base = f"http://{h['host']}:{h.get('port', 8765)}"
    req = urllib.request.Request(
        f"{base}{path}?token={urllib.parse.quote(h['token'])}",
        headers={"X-RBMN-Token": h["token"]})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def hpost(h: dict, path: str, body, timeout=1800, raw=False):
    base = f"http://{h['host']}:{h.get('port', 8765)}"
    data = body if raw else json.dumps(body).encode()
    req = urllib.request.Request(
        f"{base}{path}?token={urllib.parse.quote(h['token'])}", data=data,
        method="POST",
        headers={"X-RBMN-Token": h["token"],
                 "Content-Type": ("application/zip" if raw
                                  else "application/json")})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        # ⚠ the BODY is the answer; str(HTTPError) carries none of it
        raise RuntimeError(f"HTTP {e.code}: "
                           f"{e.read().decode('utf-8', 'replace')[:500]}") from None


#: ⚠ GitHub answers **404** to `Python-urllib/3.x` on release assets — the file
#: is there, the User-Agent is not welcome. That 404 reads exactly like "wrong
#: URL" and cost a round trip; the asset listing proved the name was right.
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) rbmn-installer"}
def _get(url: str, timeout=120):
    return urllib.request.urlopen(
        urllib.request.Request(url, headers=UA), timeout=timeout)


def _expected_sha(checksums_url: str, fname: str) -> str:
    """The published sha256 for this asset, from its OWN release's list.

    Pinning a hash in this file would go stale on a rolling tag and then fail
    honest downloads; reading the release's published checksum verifies the
    bytes without freezing them."""
    if not checksums_url:
        return ""
    try:
        with _get(checksums_url, timeout=60) as r:
            for line in r.read().decode("utf-8", "replace").splitlines():
                parts = line.split()
                if len(parts) == 2 and parts[1].lstrip("*") == fname:
                    return parts[0]
    except Exception as e:                                       # noqa: BLE001
        print(f"    ⚠ could not read the published checksums ({e}) — "
              f"continuing WITHOUT verification")
    return ""


def discover(allow8: bool) -> list:
    """Ask GitHub which shared win64 zips EXIST right now, newest release first.

    Returns [(download_url, checksums_url, tag, name)]. ⚠ Asset names on the
    rolling `latest` tag come and go — this is why the URL is not a constant."""
    try:
        with _get(API, timeout=60) as r:
            rels = json.loads(r.read().decode())
    except Exception as e:                                       # noqa: BLE001
        print(f"  ✗ could not list releases: {type(e).__name__}: {e}")
        return []
    hits = []
    for want in ((WANT_7,) if not allow8 else (WANT_7, WANT_ANY)):
        for rel in rels:
            assets = rel.get("assets") or []
            sums = next((a["browser_download_url"] for a in assets
                         if a.get("name") == "checksums.sha256"), "")
            for asset in assets:
                name = asset.get("name") or ""
                if want.search(name):
                    hits.append((asset["browser_download_url"], sums,
                                 rel.get("tag_name") or "?", name))
        if hits:
            break
    return hits


def fetch_ffmpeg(url_override: str = "", allow8: bool = False) -> Path:
    CACHE.mkdir(parents=True, exist_ok=True)
    if url_override:
        cands = [(url_override, "", "manual", url_override.rsplit("/", 1)[-1])]
    else:
        cands = discover(allow8)
        if not cands:
            raise SystemExit(
                "no win64 SHARED FFmpeg 7 zip is currently published by "
                "BtbN.\n  Pass --url with one you trust (it must be a "
                "'shared' build — it has bin\\*.dll), or --allow-ffmpeg8 to "
                "try an 8.x build (torchcodec's Windows support for 8 is not "
                "guaranteed).")
        print(f"  ✔ {len(cands)} candidate(s); newest: {cands[0][3]} "
              f"[{cands[0][2]}]")
    for url, sums, _tag, _n in cands[:4]:
        dest = CACHE / url.rsplit("/", 1)[-1]
        if dest.exists() and dest.stat().st_size > 20_000_000:
            print(f"  ✔ cached {dest.name} ({dest.stat().st_size / 1048576:.0f} MB)")
            return dest
        print(f"  ⬇ {url}")
        try:
            with _get(url) as r, dest.open("wb") as fh:
                total = int(r.headers.get("Content-Length") or 0)
                got = 0
                while True:
                    chunk = r.read(1 << 20)
                    if not chunk:
                        break
                    fh.write(chunk)
                    got += len(chunk)
                    if total:
                        print(f"\r    {got / 1048576:6.0f} / {total / 1048576:.0f} MB",
                              end="", flush=True)
            print()
            # ⭐ his standing lesson: a file's PRESENCE proves nothing. A short
            # or truncated archive would fail later, deep inside a zip read.
            want = _expected_sha(sums, dest.name)
            if want:
                h = hashlib.sha256()
                with dest.open("rb") as fh:
                    for blk in iter(lambda: fh.read(1 << 20), b""):
                        h.update(blk)
                if h.hexdigest() != want:
                    dest.unlink(missing_ok=True)
                    raise RuntimeError(f"sha256 mismatch — got {h.hexdigest()[:16]}…, "
                                       f"expected {want[:16]}…")
                print(f"    ✔ sha256 verified against the release's own list")
            return dest
        except Exception as e:                                   # noqa: BLE001
            print(f"    ✗ {type(e).__name__}: {e}")
            dest.unlink(missing_ok=True)
    raise SystemExit("could not download any candidate — pass --url")


def collect_dlls(zip_fp: Path, allow8: bool = False) -> dict:
    """The bin\\*.dll members we care about, as name -> bytes."""
    out = {}
    with zipfile.ZipFile(zip_fp) as z:
        for m in z.namelist():
            p = Path(m)
            if p.suffix.lower() != ".dll" or p.parent.name.lower() != "bin":
                continue
            if not any(p.name.lower().startswith(n) for n in NEEDED):
                continue
            out[p.name] = z.read(m)
    if not out:
        raise SystemExit(f"{zip_fp.name} has no bin\\*.dll — that is a static "
                         f"build, not a SHARED one")
    # ⚠ avutil's soname carries the FFmpeg major: 59 = FFmpeg 7, 60 = FFmpeg 8.
    # torchcodec cannot load 8 on Windows, and shipping it would leave the same
    # failure with a different explanation.
    av = next((n for n in out if n.lower().startswith("avutil-")), "")
    major = re.search(r"avutil-(\d+)", av or "")
    if major:
        ff = {57: 5, 58: 6, 59: 7, 60: 8}.get(int(major.group(1)), "?")
        print(f"  ✔ {av} → FFmpeg {ff}")
        if ff == 8 and not allow8:
            raise SystemExit(f"{av} means FFmpeg 8 — torchcodec's Windows "
                             f"support for 8 is not guaranteed. Use an n7.x "
                             f"build, or pass --allow-ffmpeg8 deliberately.")
    return out


def build_wheel(dlls: dict) -> bytes:
    """A `py3-none-any` wheel whose payload is DLLs at the ROOT.

    pip with `--target DIR` drops top-level members straight into DIR, which is
    exactly the placement we need — a package subfolder would be on sys.path
    but NOT on the DLL search path, and the DLLs would be just as invisible as
    they are now."""
    dist = "rbmn_ffmpeg_shared-7.1.0.dist-info"
    files = dict(dlls)
    files[f"{dist}/METADATA"] = (
        "Metadata-Version: 2.1\n"
        "Name: rbmn-ffmpeg-shared\n"
        "Version: 7.1.0\n"
        "Summary: FFmpeg 7 shared DLLs placed beside python.exe so torchcodec "
        "(and therefore F5-TTS) can load them.\n").encode()
    files[f"{dist}/WHEEL"] = (
        "Wheel-Version: 1.0\nGenerator: rbmn\nRoot-Is-Purelib: true\n"
        "Tag: py3-none-any\n").encode()
    rows = []
    for name, blob in files.items():
        if name.endswith("RECORD"):
            continue
        digest = base64.urlsafe_b64encode(
            hashlib.sha256(blob).digest()).rstrip(b"=").decode()
        rows.append(f"{name},sha256={digest},{len(blob)}")
    rows.append(f"{dist}/RECORD,,")
    files[f"{dist}/RECORD"] = ("\n".join(rows) + "\n").encode()

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, blob in files.items():
            z.writestr(name, blob)
    return buf.getvalue()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true",
                    help="actually install (default is report only)")
    ap.add_argument("--restart-comfy", action="store_true",
                    help="stop+start ComfyUI on each box afterwards")
    ap.add_argument("--host", default="", help="only this worker")
    ap.add_argument("--url", default="", help="an FFmpeg 7 win64 SHARED zip")
    ap.add_argument("--allow-ffmpeg8", action="store_true",
                    help="accept an 8.x build if no 7.x is published")
    a = ap.parse_args()

    hosts = [h for h in helpers() if not a.host or h["host"] == a.host]
    if not hosts:
        print("no workers configured")
        return 1

    print("🔧 FFmpeg shared DLLs → each box's ComfyUI python\n")
    print("1) the build (downloaded once, here)")
    zip_fp = fetch_ffmpeg(a.url, a.allow_ffmpeg8)
    dlls = collect_dlls(zip_fp, a.allow_ffmpeg8)
    mb = sum(len(v) for v in dlls.values()) / 1048576
    print(f"  ✔ {len(dlls)} DLL(s), {mb:.0f} MB: {', '.join(sorted(dlls)[:6])}…")
    wheel = build_wheel(dlls)
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w", zipfile.ZIP_STORED) as z:
        z.writestr(WHEEL_NAME, wheel)          # the wheel is already deflated
    blob = payload.getvalue()
    print(f"  ✔ wheel {len(wheel) / 1048576:.0f} MB, shipping zip "
          f"{len(blob) / 1048576:.0f} MB")

    if not a.apply:
        print("\n2) targets (report only — pass --apply to install)")
        for h in hosts:
            try:
                cfg = hget(h, "/config")
                d = hget(h, "/diag")
                root = (cfg.get("comfy") or {}).get("root") or "?"
                print(f"  {h['host']}: {root}\\python_embeded  "
                      f"(state {d.get('helper', {}).get('state_dir')})")
            except Exception as e:                               # noqa: BLE001
                print(f"  {h['host']}: ✗ {type(e).__name__}: {e}")
        return 0

    print("\n2) install")
    bad = 0
    for h in hosts:
        host = h["host"]
        print(f"── {h.get('name') or host} ({host})")
        try:
            cfg = hget(h, "/config")
            diag = hget(h, "/diag")
        except Exception as e:                                   # noqa: BLE001
            print(f"   ✗ helper unreachable: {type(e).__name__}: {e}")
            bad += 1
            continue
        root = (cfg.get("comfy") or {}).get("root") or ""
        state = (diag.get("helper") or {}).get("state_dir") or ""
        if not root or not state:
            print("   ✗ this helper does not report its ComfyUI root / state dir")
            bad += 1
            continue
        target = f"{root}\\python_embeded"
        t0 = time.time()
        try:
            r = hpost(h, f"/datasets/{DATASET_NAME}", blob, timeout=1800, raw=True)
            print(f"   ⬆ shipped in {time.time() - t0:.0f}s ({r.get('name')})")
        except Exception as e:                                   # noqa: BLE001
            print(f"   ✗ upload failed: {e}")
            bad += 1
            continue
        local = f"{state}\\datasets\\{DATASET_NAME}\\{WHEEL_NAME}"
        try:
            r = hpost(h, "/install/pip", {"args": [
                "install", "--no-deps", "--no-index", "--upgrade",
                "--target", target, local]}, timeout=1800)
        except Exception as e:                                   # noqa: BLE001
            print(f"   ✗ pip failed: {e}")
            bad += 1
            continue
        tail = (r.get("tail") or "").strip().splitlines()[-1:] or [""]
        if r.get("ok"):
            print(f"   ✅ installed into {target}\n      {tail[0][:150]}")
        else:
            print(f"   ✗ pip rc={r.get('rc')}: {(r.get('tail') or '')[-400:]}")
            bad += 1
            continue
        if a.restart_comfy:
            try:
                hpost(h, "/comfy/stop", {}, timeout=180)
                time.sleep(3)
                hpost(h, "/comfy/start", {}, timeout=300)
                print("   ↻ ComfyUI restarted")
            except Exception as e:                               # noqa: BLE001
                print(f"   ⚠ restart failed — do it by hand: {e}")

    print("\n⚠ A DLL is only loaded when the process STARTS: ComfyUI must be "
          "restarted on every box\n  before this can possibly work "
          "(--restart-comfy does it).")
    print("⭐ Then PROVE it — an install is not a decode:\n"
          "     python scripts\\tts_doctor.py --probe")
    print(f"\n{'ALL BOXES DONE' if not bad else f'{bad} box(es) failed'}")
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
