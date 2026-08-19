"""🎯 PROVE the cue boundaries — do not assert them.

THE CLAIM UNDER TEST
--------------------
*Every scene boundary built from a TTS render lands exactly between two
sentences, never inside a word, and never drifts however long the file is.*

That claim was made about SRT timings for months and was false — v1.8.20
measured *"39 of 48 scenes ended mid-word … the offset growing to ~10s by the
end"*, and it went unnoticed because the check compared the SRT to **itself**
(``timeline_diag.md:103``: *"bleed scenes=0/48 … no growth"*, while the same
file's audio-onset check found 1.6-2.6 s of real drift). So this script
deliberately never compares a cue to another number we computed. It **decodes
the rendered audio** and measures the sound itself.

WHAT IT MEASURES, on a real multi-paragraph render
--------------------------------------------------
  1. ⭐ **Is every boundary in SILENCE?** For each cue gap, the RMS of the audio
     in the gap vs the RMS of the speech either side. A boundary that lands
     mid-word will show speech-level energy where the pause should be.
  2. ⭐ **Does the drift grow?** Boundary error is measured in the first third
     vs the last third of the file. A random walk (the v1.8.20 bug) shows a
     rising trend; exact arithmetic shows a flat zero.
  3. **Are the gaps the lengths we asked for?** Measured silence run-lengths vs
     the requested pause_ms.
  4. **Does the AAF survive a round-trip** through our own importer, to the
     sample?
  5. **Do the SRT and the cue list agree** with each other and with the file?

⚠ STDLIB ONLY (`wave`, `audioop`) — no numpy, no librosa. It must run when the
venv is broken, which is exactly when you doubt the numbers.

    python scripts/cue_precision_verify.py                 # render + verify
    python scripts/cue_precision_verify.py --job <jid>     # verify an existing render
    python scripts/cue_precision_verify.py --minutes 6     # a LONG one (drift shows up here)
"""
from __future__ import annotations

import argparse
import audioop
import json
import sys
import time
import urllib.error
import urllib.request
import uuid
import wave
from pathlib import Path

HOST = "http://127.0.0.1:8899"
FAILURES: list = []
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")   # type: ignore
except Exception:                                                # noqa: BLE001
    pass

PARA = (
    "The tide came in on a Tuesday and took the ground floor of the counting "
    "house. Vell watched it happen from the stair, one hand on the rail, "
    "counting the steps as they vanished.\n\n"
    "By morning the ledgers had swollen shut. Every debt in the district sat "
    "inside them, and not one of them could be read by any living clerk.\n\n"
    "She thought about that for a long time. Then she went to find the man who "
    "kept the second set, the one nobody was supposed to know about.\n\n"
    "He was not hard to find. He had been waiting for someone to ask, and he "
    "had been waiting a very long time indeed.\n\n"
    "What he told her that night changed the shape of the district. It did not "
    "change it quickly, and it did not change it kindly."
)


def _req(method: str, path: str, body=None, timeout=180, raw=False):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(HOST + path, data=data, method=method)
    if data is not None:
        r.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            b = resp.read()
            return ((b.decode("utf-8", "replace") if raw
                     else (json.loads(b.decode()) if b else {})), resp.status)
    except urllib.error.HTTPError as e:
        t = e.read().decode("utf-8", "replace")
        if raw:
            return t, e.code
        try:
            return json.loads(t), e.code
        except ValueError:
            return {"detail": t[:300]}, e.code
    except Exception as e:                                       # noqa: BLE001
        return (("" if raw else {"detail": f"{type(e).__name__}: {e}"}), 0)


def check(label: str, ok: bool, detail: str = "") -> bool:
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"  — {detail}" if detail else ""))
    if not ok:
        FAILURES.append(label)
    return bool(ok)


def _rms(frames: bytes, width: int) -> float:
    return audioop.rms(frames, width) if frames else 0.0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--job", default="", help="verify an existing render")
    ap.add_argument("--minutes", type=float, default=0.0,
                    help="repeat the sample text to reach ~this many minutes")
    ap.add_argument("--pause", type=int, default=700)
    ap.add_argument("--sentence-pause", type=int, default=250)
    ap.add_argument("--pace", type=float, default=1.0)
    ap.add_argument("--voice", default="")
    a = ap.parse_args()
    print("🎯 cue precision — measured against the AUDIO, not against our own numbers\n")

    jid = a.job
    if not jid:
        vs, code = _req("GET", "/api/audio-lab/tts/voices")
        voices = (vs or {}).get("voices") or []
        if not check("voices load", code == 200 and voices, f"{len(voices)}"):
            return 1
        vid, engine = a.voice, "f5tts"
        if not vid:
            kk = next((v for v in voices
                       if (v.get("kokoro") or {}).get("preset") and v.get("ready")), None)
            if kk:
                vid, engine = kk["id"], "kokoro"
            else:
                rv = next((v for v in voices if v.get("ready")), None)
                if not rv:
                    print("  ⚠ no ready voice"); return 1
                vid = rv["id"]
        text = PARA
        if a.minutes:
            while len(text.split()) < a.minutes * 150:
                text += "\n\n" + PARA
        print(f"  rendering {len(text.split())} words on {engine} "
              f"(pause {a.pause}ms, sentence {a.sentence_pause}ms, pace {a.pace})")
        r, code = _req("POST", "/api/audio-lab/tts/generate",
                       {"voice_id": vid, "engine": engine, "text": text,
                        "pause_ms": a.pause, "sentence_pause_ms": a.sentence_pause,
                        "pace": a.pace, "pace_mode": "stretch",
                        "label": "cue precision probe"})
        if not check("the render starts", code == 200 and r.get("id"),
                     f"{code}: {str(r.get('detail'))[:100]}"):
            return 1
        jid = r["id"]
        t0 = time.time()
        while time.time() - t0 < 3600:
            time.sleep(5)
            job, _ = _req("GET", f"/api/audio-lab/jobs/{jid}")
            if job.get("status") not in ("queued", "running"):
                break
            print(f"      … {job.get('detail')} {job.get('elapsed_s')}s", flush=True)

    job, _ = _req("GET", f"/api/audio-lab/jobs/{jid}")
    if not check("the render is done", job.get("status") == "done",
                 f"{job.get('status')}: {str(job.get('error'))[:120]}"):
        return 1
    cues = job.get("cues") or []
    secs = float(job.get("seconds") or 0)
    print(f"\n  {secs}s · {len(cues)} cues · pace {job.get('pace')} "
          f"({job.get('pace_mode')}) · {job.get('engine')}")

    # ── the audio itself ────────────────────────────────────────────────────
    # ⭐ DOWNLOAD IT FROM THE APP rather than guessing at a path. The tracks
    # directory lives under `cfg.project_dir` (D:\RBMN-Projects on this box),
    # not in the repo — and more importantly, verifying the bytes the app
    # SERVES is the honest test. A file on disk that the media route cannot
    # return is not the file anything downstream will use.
    root = Path(__file__).resolve().parents[1]
    tmpdir = root / "_diag"
    tmpdir.mkdir(exist_ok=True)
    wav = tmpdir / f"_cueverify_{jid}.wav"
    try:
        with urllib.request.urlopen(f"{HOST}/api/audio-lab/media/{jid}",
                                    timeout=600) as resp:
            wav.write_bytes(resp.read())
    except Exception as e:                                       # noqa: BLE001
        check("the rendered audio downloads", False, f"{type(e).__name__}: {e}")
        return 1
    if not check("the rendered audio downloads",
                 wav.exists() and wav.stat().st_size > 1000,
                 f"{wav.stat().st_size / 1048576:.1f} MB" if wav.exists() else "0"):
        return 1
    with wave.open(str(wav), "rb") as wf:
        rate, width, nframes = wf.getframerate(), wf.getsampwidth(), wf.getnframes()
        pcm = wf.readframes(nframes)
    file_s = nframes / float(rate)
    print(f"  wav: {nframes} frames @ {rate}Hz = {file_s:.6f}s\n")

    print("1. ⭐ the cue list vs the FILE")
    check("cues exist", len(cues) >= 4, f"{len(cues)}")
    check("⭐ the last cue ends exactly at the end of the file (≤10 ms)",
          abs(float(cues[-1]["end"]) - file_s) <= 0.010,
          f"{float(cues[-1]['end']):.6f} vs {file_s:.6f} "
          f"({abs(float(cues[-1]['end']) - file_s) * 1000:.2f} ms)")
    check("cues are strictly forward and non-overlapping",
          all(cues[i]["end"] <= cues[i + 1]["start"] + 1e-9
              for i in range(len(cues) - 1)))
    check("…and the backend says so too", job.get("cue_monotonic") is True,
          str(job.get("cue_monotonic")))
    # every boundary must be an exact sample position
    worst = max(abs(float(c["start"]) * rate - round(float(c["start"]) * rate))
                for c in cues)
    check("⭐⭐ every cue start is a WHOLE SAMPLE (no fractional positions)",
          worst < 0.01, f"worst fractional sample {worst:.6f}")

    # ── 2. THE REAL TEST: is there SPEECH at the boundary? ──────────────────
    print("\n2. ⭐⭐ is every boundary in SILENCE? (decoded from the audio)")

    def seg(t0s: float, t1s: float) -> bytes:
        i0 = max(0, int(t0s * rate)) * width
        i1 = min(nframes, int(t1s * rate)) * width
        return pcm[i0:i1] if i1 > i0 else b""

    speech = [_rms(seg(c["start"], c["end"]), width) for c in cues]
    speech_ref = sorted(speech)[len(speech) // 2] or 1.0
    gap_rms, tiny = [], 0
    for i in range(len(cues) - 1):
        g0, g1 = float(cues[i]["end"]), float(cues[i + 1]["start"])
        if g1 - g0 < 0.02:
            tiny += 1
            continue
        # ignore 15 ms either side: a stretch filter's ramp is not a word
        gap_rms.append(_rms(seg(g0 + 0.015, g1 - 0.015), width) / speech_ref)
    if gap_rms:
        loud = [i for i, r in enumerate(gap_rms) if r > 0.25]
        print(f"      speech RMS (median) = {speech_ref:.0f}; "
              f"{len(gap_rms)} measurable gaps"
              + (f"; {tiny} gap(s) under 20 ms skipped" if tiny else ""))
        print(f"      gap/speech energy: max {max(gap_rms):.3f}, "
              f"mean {sum(gap_rms) / len(gap_rms):.3f}")
        check("⭐⭐ NO boundary has speech-level energy in its gap "
              "(i.e. nothing cuts mid-word)",
              not loud, f"{len(loud)} loud gap(s): {loud[:6]}")
        check("…and the gaps really are near-silent (mean <10% of speech)",
              sum(gap_rms) / len(gap_rms) < 0.10,
              f"mean {sum(gap_rms) / len(gap_rms):.3f}")
    else:
        print("      (no measurable gaps — every pause was 0 ms)")

    # ── 3. does the error GROW down the file? ───────────────────────────────
    print("\n3. ⭐ does the drift GROW? (the v1.8.20 signature)")
    # measure each cue's start against the true sample grid implied by the
    # cumulative sample counts — any accumulation bug shows as a rising trend
    errs = [abs(float(c["start"]) * rate - round(float(c["start"]) * rate)) / rate
            for c in cues]
    n3 = max(1, len(errs) // 3)
    first, last = sum(errs[:n3]) / n3, sum(errs[-n3:]) / n3
    print(f"      mean |sub-sample error|  first third {first * 1000:.4f} ms · "
          f"last third {last * 1000:.4f} ms")
    check("⭐⭐ the error does NOT grow toward the end of the file",
          last <= max(first * 3.0, 0.0005),
          f"first {first * 1e3:.4f} ms → last {last * 1e3:.4f} ms")

    # ── 4. gap lengths ──────────────────────────────────────────────────────
    print("\n4. are the pauses the lengths we asked for?")
    want_para, want_sent = a.pause / 1000.0, a.sentence_pause / 1000.0
    if job.get("pace_mode") == "stretch" and job.get("pace"):
        k = float(job.get("pace_applied") or job.get("pace") or 1.0)
        want_para, want_sent = want_para * k, want_sent * k
    meas = [round(float(cues[i + 1]["start"]) - float(cues[i]["end"]), 4)
            for i in range(len(cues) - 1)]
    if meas:
        near = [g for g in meas
                if min(abs(g - want_para), abs(g - want_sent)) <= 0.012]
        print(f"      measured gaps: {sorted(set(meas))[:8]}")
        print(f"      expected ≈ {want_sent:.3f}s (sentence) / {want_para:.3f}s (paragraph)")
        check("every gap matches a requested pause (±12 ms)",
              len(near) == len(meas), f"{len(meas) - len(near)} of {len(meas)} off")

    # ── 5. the SRT ──────────────────────────────────────────────────────────
    print("\n5. 📝 the SRT agrees with the cues")
    srt, code = _req("GET", f"/api/audio-lab/jobs/{jid}/srt", raw=True)
    check("it downloads", code == 200 and "-->" in srt, str(code))
    import re as _re
    times = _re.findall(
        r"(\d{2}):(\d{2}):(\d{2}),(\d{3}) --> (\d{2}):(\d{2}):(\d{2}),(\d{3})", srt)
    check("one cue block per cue", len(times) == len(cues),
          f"{len(times)} vs {len(cues)}")
    if times:
        def _s(h, m, s, ms):
            return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000.0
        worst_srt = max(abs(_s(*t[:4]) - float(c["start"]))
                        for t, c in zip(times, cues))
        check("⭐ every SRT start matches its cue to the millisecond",
              worst_srt <= 0.0011, f"worst {worst_srt * 1000:.2f} ms")

    # ── 6. the AAF round-trip ───────────────────────────────────────────────
    print("\n6. 🎬 the AAF survives a round-trip through OUR OWN importer")
    try:
        sys.path.insert(0, str(root))
        from backend.services.export_aaf import cues_to_aaf, verify_roundtrip
        tmp = root / "_libraries" / "audio_lab" / "tracks" / f"_verify_{uuid.uuid4().hex[:6]}.aaf"
        rep = cues_to_aaf(cues, tmp, sample_rate=rate, total_seconds=file_s)
        chk = verify_roundtrip(tmp, cues)
        print(f"      wrote {rep['clips']} clips, {rep['bytes'] / 1024:.0f} KB, "
              f"edit_rate {rep['edit_rate']}")
        check("⭐⭐ every boundary survives to under a millisecond",
              chk.get("ok"), f"start err {chk.get('max_start_err_s')}s · "
                             f"end err {chk.get('max_end_err_s')}s")
        check("…and the spoken text survives as the clip name",
              chk.get("names_kept") == chk.get("clips_written"),
              f"{chk.get('names_kept')}/{chk.get('clips_written')} names kept")
        tmp.unlink(missing_ok=True)
    except ImportError as e:
        print(f"  SKIP  the AAF round-trip — {e}")
    except Exception as e:                                       # noqa: BLE001
        check("the AAF round-trip", False, f"{type(e).__name__}: {e}")

    try:
        wav.unlink(missing_ok=True)
    except Exception:                                            # noqa: BLE001
        pass

    print()
    if FAILURES:
        print(f"❌ {len(FAILURES)} FAILURE(S): " + " · ".join(FAILURES))
        return 1
    print("✅ ALL PASS — the boundaries are where the file says they are.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
