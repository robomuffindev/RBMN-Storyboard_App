"""🎙🎬 END-TO-END PROOF: chapter → spoken take → SRT → project with scenes.

⭐ **MEASURE, DO NOT INFER.** The free suite proves the SRT writer can format a
cue and that `clips_to_scenes` can merge one. It says nothing about whether a
REAL render produces cues that match the audio it made. This does the whole
chain against the running app and checks the artifacts:

    chapter with 3 paragraphs
      → 🎙 speak it (Kokoro, app host, no GPU, no worker, free)
      → cues: counted, monotonic, and landing on the file's real duration
      → ✅ keep  → the chapter gains BOTH an audio and an srt slot
      → the SRT parses, and its last cue matches the audio
      → 🎬 create a project → scenes built from those cues, and they cover it

Everything it makes is deleted at the end (`--keep` to inspect), INCLUDING the
project — this is the only probe here that writes to the SQL side.

    python scripts/chapter_voice_probe.py
    python scripts/chapter_voice_probe.py --voice <id>   # else the first Kokoro one
    python scripts/chapter_voice_probe.py --keep

⚠ STDLIB ONLY — an operator script that imports `backend.*` dies outside the
venv, which is exactly when it is needed.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request
import uuid

HOST = "http://127.0.0.1:8899"
FAILURES: list = []
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")   # type: ignore
except Exception:                                                # noqa: BLE001
    pass
B = "/api/storyworld"

NARRATION = (
    "The tide came in on a Tuesday and took the ground floor of the counting "
    "house. Vell watched it happen from the stair, one hand on the rail.\n\n"
    "By morning the ledgers had swollen shut. Every debt in the district sat "
    "inside them, and not one could be read.\n\n"
    "She thought about that for a long time. Then she went to find the clerk "
    "who kept the second set."
)


def _req(method: str, path: str, body=None, timeout=180, raw=False):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(HOST + path, data=data, method=method)
    if data is not None:
        r.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            b = resp.read()
            if raw:
                return b.decode("utf-8", "replace"), resp.status
            return (json.loads(b.decode()) if b else {}), resp.status
    except urllib.error.HTTPError as e:
        t = e.read().decode("utf-8", "replace")
        if raw:
            return t, e.code
        try:
            return json.loads(t), e.code
        except ValueError:
            return {"detail": t[:300]}, e.code
    except Exception as e:                                       # noqa: BLE001
        return ({"detail": f"{type(e).__name__}: {e}"} if not raw else ""), 0


def check(label: str, ok: bool, detail: str = "") -> bool:
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"  — {detail}" if detail else ""))
    if not ok:
        FAILURES.append(label)
    return bool(ok)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--voice", default="")
    ap.add_argument("--keep", action="store_true")
    a = ap.parse_args()
    tag = uuid.uuid4().hex[:4]
    print("🎙🎬 chapter → take → SRT → project (end to end, no GPU)\n")

    # ── pick a voice ────────────────────────────────────────────────────────
    vs, code = _req("GET", "/api/audio-lab/tts/voices")
    voices = vs.get("voices") or []
    if not check("voices load", code == 200 and voices, f"{len(voices)} voices"):
        return 1
    vid = a.voice
    engine = "f5tts"
    if not vid:
        kk = next((v for v in voices
                   if (v.get("kokoro") or {}).get("preset") and v.get("ready")), None)
        if kk:
            vid, engine = kk["id"], "kokoro"
            print(f"  using 🎨 {kk['name']} on Kokoro (app host — no worker needed)")
        else:
            rv = next((v for v in voices if v.get("ready")), None)
            if not rv:
                print("  ⚠ no ready voice — add one in 🎤 the voice library")
                return 1
            vid = rv["id"]
            print(f"  using 🎤 {rv['name']} on F5 (a worker must be up)")

    # ── fixture ─────────────────────────────────────────────────────────────
    print("\n1. fixture")
    w, code = _req("POST", f"{B}/worlds", {"name": f"SmokeVoice{tag}"})
    if not check("world", code == 200 and w.get("id"), str(code)):
        return 1
    wid = w["id"]
    st, _ = _req("POST", f"{B}/worlds/{wid}/stories",
                 {"title": "The Ledger Tide", "story_type": "narration"})
    sid = st["id"]
    _req("POST", f"{B}/worlds/{wid}/stories/{sid}",
         {"arcs": [{"title": "The Assessment", "summary": "The tide takes a census."}]})
    ch, code = _req("POST", f"{B}/worlds/{wid}/stories/{sid}/chapters",
                    {"title": "Low Water", "summary": "The census reaches the Weir.",
                     "narration": NARRATION,
                     "beats": [{"title": "The stair", "summary": "Water on the tiles."},
                               {"title": "The ledger", "summary": "Ink runs."}]})
    cid = ch.get("id", "")
    check("chapter with a 3-paragraph narration",
          code == 200 and ch.get("has_narration"), f"{ch.get('narration_words')} words")
    base = f"{B}/worlds/{wid}/stories/{sid}/chapters/{cid}"

    # ── the gate, BEFORE there is audio ─────────────────────────────────────
    print("\n2. ⭐⭐ the gate refuses before there is a take")
    rd, _ = _req("GET", f"{base}/project-readiness")
    check("not ready with text alone", rd.get("ready") is False)
    check("…and it says WHY (audio + srt)",
          "AUDIO" in " ".join(rd.get("blocking") or [])
          and "SRT" in " ".join(rd.get("blocking") or []),
          " · ".join(rd.get("blocking") or [])[:90])

    # ── speak it ────────────────────────────────────────────────────────────
    print("\n3. 🎙 speak it")
    r, code = _req("POST", f"{base}/tts",
                   {"voice_id": vid, "engine": engine, "pace": 1.0,
                    "pace_mode": "stretch", "pause_ms": 700,
                    "sentence_pause_ms": 200, "auto_tag": True})
    if not check("the render starts", code == 200 and r.get("id"),
                 f"{code}: {str(r.get('detail'))[:80]}"):
        return 1
    jid = r["id"]
    print(f"      {r.get('chunks')} chunk(s) on {r.get('worker')}")
    job, t0 = {}, time.time()
    while time.time() - t0 < 900:
        time.sleep(4)
        job, _ = _req("GET", f"/api/audio-lab/jobs/{jid}")
        if job.get("status") not in ("queued", "running"):
            break
        print(f"      … {job.get('status')} {job.get('detail')} "
              f"{job.get('elapsed_s')}s", flush=True)
    if not check("…and finishes", job.get("status") == "done",
                 f"{job.get('status')}: {str(job.get('error'))[:100]}"):
        return 1
    secs = float(job.get("seconds") or 0)
    cues = job.get("cues") or []
    print(f"      → {secs}s of speech, {len(cues)} cues, {job.get('elapsed_s')}s to make")

    # ── ⭐⭐ the cues, measured against the artifact ─────────────────────────
    print("\n4. ⭐⭐ the cues describe the file that was actually made")
    check("there ARE cues", len(cues) >= 3, f"{len(cues)}")
    check("…one per spoken chunk", len(cues) == (job.get("chunks") or -1),
          f"{len(cues)} cues vs {job.get('chunks')} chunks")
    check("…every cue carries its words",
          all((c.get("text") or "").strip() for c in cues))
    check("…and no cue contains a [pause] tag (they are stripped before the model)",
          not any("[pause" in (c.get("text") or "").lower() for c in cues))
    check("…they run forwards and never overlap",
          all(cues[i]["end"] <= cues[i + 1]["start"] + 1e-6
              for i in range(len(cues) - 1))
          and all(c["end"] > c["start"] for c in cues))
    drift = abs(float(cues[-1]["end"]) - secs) if cues and secs else 99
    check("⭐⭐ the LAST cue lands on the end of the audio (≤0.6s)",
          drift <= 0.6, f"drift {drift:.2f}s (cue {cues[-1]['end']} vs file {secs})")
    check("…and the backend agrees it does",
          float(job.get("cue_drift_s") or 0) <= 0.6, str(job.get("cue_drift_s")))
    gaps = [round(cues[i + 1]["start"] - cues[i]["end"], 2)
            for i in range(len(cues) - 1)]
    check("…the pauses are REALLY in the timeline (a gap between cues)",
          any(g > 0.05 for g in gaps), f"gaps {gaps[:8]}")

    # ── the SRT ─────────────────────────────────────────────────────────────
    print("\n5. 📝 the SRT")
    srt, code = _req("GET", f"/api/audio-lab/jobs/{jid}/srt", raw=True)
    check("it downloads", code == 200 and "-->" in srt, str(code))
    blocks = [b for b in re.split(r"\n\s*\n", srt.strip()) if b.strip()]
    check("…one block per cue", len(blocks) == len(cues),
          f"{len(blocks)} blocks vs {len(cues)} cues")
    check("…with comma millis, not dots",
          bool(re.search(r"\d{2}:\d{2}:\d{2},\d{3} --> ", srt)),
          srt.splitlines()[1] if len(srt.splitlines()) > 1 else "")
    m = re.findall(r"--> (\d{2}):(\d{2}):(\d{2}),(\d{3})", srt)
    last = (int(m[-1][0]) * 3600 + int(m[-1][1]) * 60 + int(m[-1][2])
            + int(m[-1][3]) / 1000.0) if m else 0
    check("…and its last cue matches the audio too",
          abs(last - secs) <= 0.6, f"{last:.2f}s vs {secs}s")

    # ── keep the take ───────────────────────────────────────────────────────
    print("\n6. ✅ keep the take")
    k, code = _req("POST", f"{base}/tts/keep", {"job_id": jid, "with_srt": True})
    check("it keeps", code == 200, f"{code}: {str(k.get('detail'))[:80]}")
    # ⭐ ALL THREE, from one action. (This asserted exactly {audio, srt} before
    # the AAF writer existed and failed a correct run — the test was stale, not
    # the code. Assert a SUPERSET-of-the-minimum, not an exact set, so adding a
    # fourth artefact later is not a spurious failure.)
    check("⭐⭐ audio + SRT + AAF are all written in ONE Keep",
          {"audio", "srt", "aaf"} <= set(k.get("kept") or []), str(k.get("kept")))
    lst, _ = _req("GET", f"{base}/chapters".replace("/chapters/" + cid + "/chapters",
                                                    "/chapters"))
    lst, _ = _req("GET", f"{B}/worlds/{wid}/stories/{sid}/chapters")
    c2 = next((x for x in (lst.get("chapters") or []) if x["id"] == cid), {})
    nf = c2.get("narration_files") or {}
    check("…the chapter now has the audio", bool(nf.get("audio")),
          str((nf.get("audio") or {}).get("filename")))
    check("…and the srt", bool(nf.get("srt")),
          f"{(nf.get('srt') or {}).get('cues')} cues")
    check("…the audio slot records its MEASURED duration",
          abs(float((nf.get("audio") or {}).get("seconds") or 0) - secs) < 0.2,
          str((nf.get("audio") or {}).get("seconds")))
    # ⭐⭐ HIS QUESTION (2026-08-19): "if I regenerate the narration for a
    # chapter there will be an AAF correct?" — so ASSERT it, all three slots
    # from one Keep, and assert the AAF was verified by a round-trip through
    # our own importer before it was accepted.
    check("⭐⭐ …AND the AAF — all three files from ONE Keep",
          bool(nf.get("aaf")), str(sorted(nf)))
    _aaf = nf.get("aaf") or {}
    check("…the AAF has one clip per cue",
          (_aaf.get("clips") or 0) == len(cues),
          f"{_aaf.get('clips')} clips vs {len(cues)} cues")
    _v = _aaf.get("verified") or {}
    check("⭐ …and it was ROUND-TRIPPED through our own importer before keeping",
          _v.get("ok") is True,
          f"start err {_v.get('max_start_err_s')}s · "
          f"names {_v.get('names_kept')}/{_v.get('clips_written')}")
    check("…the chapter also carries the raw cue list",
          (c2.get("cues") and len(c2["cues"]) == len(cues)) or False,
          f"{len(c2.get('cues') or [])} cues on the chapter")

    # ── ⭐⭐ 6b. THE STALE-TAKE GUARD ────────────────────────────────────────
    # His worry, in his words: "if I regenerate the narration for a chapter
    # there will be an AAF correct?" — the honest answer is that regenerating
    # the TEXT does not re-render anything, so the take would still speak the
    # OLD words while everything looked green. Prove it is caught.
    print("\n6b. ⭐⭐ rewriting the text INVALIDATES the take")
    _req("POST", f"{base}", {"narration": NARRATION + "\n\nAnd then the clerk "
                                                      "said something else entirely."})
    rdx, _ = _req("GET", f"{base}/project-readiness")
    check("⭐⭐ a rewritten narration BLOCKS the project (stale take)",
          rdx.get("ready") is False, str(rdx.get("ready")))
    check("…and says the audio speaks a different number of words",
          any("REWRITTEN" in b for b in (rdx.get("blocking") or [])),
          " · ".join(rdx.get("blocking") or [])[:110])
    px, pc = _req("POST", f"{base}/create-project", {"mode": "narration_video"})
    check("…and create-project refuses it (409)", pc == 409,
          f"status {pc}: {str(px.get('detail'))[:80]}")
    # put the original text back so the rest of the probe is unaffected
    _req("POST", f"{base}", {"narration": NARRATION})
    rdy2, _ = _req("GET", f"{base}/project-readiness")
    check("…restoring the exact text makes the take valid again",
          rdy2.get("ready") is True,
          " · ".join(rdy2.get("blocking") or []) or "nothing blocking")

    # ── the gate again, and the project ─────────────────────────────────────
    print("\n7. 🎬 the project")
    rd2, _ = _req("GET", f"{base}/project-readiness")
    check("⭐⭐ NOW it is ready", rd2.get("ready") is True,
          " · ".join(rd2.get("blocking") or []) or "nothing blocking")
    pj, code = _req("POST", f"{base}/create-project",
                    {"mode": "narration_video", "scenes_from_cues": True,
                     "min_scene_seconds": 4.0}, timeout=600)
    ok = check("it creates", code == 200 and pj.get("project_id"),
               f"{code}: {str(pj.get('detail'))[:120]}")
    pid = pj.get("project_id", "")
    if ok:
        for s in pj.get("steps") or []:
            print(f"      · {s}")
        check("⭐ scenes were built from the cues", (pj.get("scenes") or 0) >= 2,
              f"{pj.get('scenes')} scenes")
        sc, _ = _req("GET", f"/api/projects/{pid}/scenes")
        scenes = sc if isinstance(sc, list) else (sc.get("scenes") or [])
        check("…and they exist on the project", len(scenes) == pj.get("scenes"),
              f"{len(scenes)} scenes")
        if scenes:
            check("…named with the SPOKEN WORDS (an AAF cannot do this)",
                  any(len((s.get("name") or "")) > 12 for s in scenes),
                  repr((scenes[0].get("name") or "")[:60]))
            ends = max(float(s.get("end_time") or 0) for s in scenes)
            check("…and they cover the whole narration",
                  abs(ends - secs) <= 1.0, f"scenes end {ends:.2f}s vs audio {secs}s")
        pr, _ = _req("GET", f"/api/projects/{pid}")
        psett = (pr or {}).get("settings") or {}
        check("…the project is linked to THIS chapter",
              psett.get("chapter_id") == cid, str(psett.get("chapter_id")))
        check("…and records where its scenes came from",
              psett.get("scene_source") == "chapter_cues",
              str(psett.get("scene_source")))
        # ⭐⭐ HIS QUESTION: does the AAF reach the PROJECT, ready to use?
        check("⭐⭐ the AAF was copied into the project and is selectable",
              bool(psett.get("story_aaf_asset_id")),
              str(psett.get("story_aaf_name") or psett.get("story_aaf_asset_id")))
        check("…as are the audio and the SRT",
              bool(psett.get("story_audio_asset_id"))
              and bool(psett.get("story_srt_asset_id")),
              f"audio={bool(psett.get('story_audio_asset_id'))} "
              f"srt={bool(psett.get('story_srt_asset_id'))}")
        # ⭐ THE LOCK — the thing that actually made AAF work in production.
        au, ac = _req("POST", f"/api/projects/{pid}/timeline/suggest-timeline", {})
        check("⭐⭐ Suggest Timeline REFUSES on the cue timeline (409)",
              ac == 409, f"status {ac}: {str(au.get('detail'))[:70]}")
        au, ac = _req("POST", f"/api/projects/{pid}/timeline/scenes-from-sections", {})
        check("⭐⭐ …and so does scenes-from-sections",
              ac in (409, 404, 405), f"status {ac}")

    # ── cleanup ─────────────────────────────────────────────────────────────
    print("\n8. cleanup")
    if a.keep:
        print(f"      kept world {wid} and project {pid}")
    else:
        if pid:
            _, dc = _req("POST", f"/api/projects/{pid}/delete")
            if dc not in (200, 204):
                _, dc = _req("DELETE", f"/api/projects/{pid}")
            check("the throwaway project is gone", dc in (200, 204), str(dc))
        _req("DELETE", f"/api/audio-lab/jobs/{jid}")
        _, dc = _req("POST", f"{B}/worlds/{wid}/delete")
        check("the throwaway world is gone", dc == 200, str(dc))

    print()
    if FAILURES:
        print(f"❌ {len(FAILURES)} FAILURE(S): " + " · ".join(FAILURES))
        return 1
    print("✅ ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
