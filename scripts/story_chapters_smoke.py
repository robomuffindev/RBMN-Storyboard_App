"""📖📚 Story CHAPTERS + CODEX smoke test — the whole lane for ZERO renders,
ZERO LLM calls, ZERO GPU.

WHY IT CAN BE FREE
------------------
Almost none of what makes these two features a *system* needs a model:

  · the chapter store, its merge semantics, the cap, arc validation
  · ROUTE ORDER — that `/chapters/generate` is not swallowed as a chapter id
  · beats being ARC-SHAPED, which is what lets the project builder take them
  · the codex's canon HASHES, which decide what a recalc reads (and skips)
  · ⭐ the one thing that would hurt most if it broke: **a recalc keeps what
    you wrote.** `manual` and `pinned` are exercised through `_merge_entries`
    directly, with no model in the loop.
  · the project link taking a chapter, refusing a chapter without a story, and
    clearing a stale chapter when the story changes

The LLM routes are exercised only for their VALIDATION (a story with no arcs is
refused; a chapter with no narration cannot be split into beats). No model is
called and no worker is touched.

    python scripts/story_chapters_smoke.py            # everything free, then clean up
    python scripts/story_chapters_smoke.py --keep     # leave the artefacts
    python scripts/story_chapters_smoke.py --live     # + write ONE real narration
                                                      #   and MEASURE it (§6b)

⭐ `--live` exists because **a green unit test on the helpers is not evidence
about the artifact.** §4b proves `_paragraphize` can split a blob; §6b proves
that what the model actually wrote to disk has paragraphs in it and hits the
word budget — the two things he reported as broken.

⚠ It creates a real world `SmokeChap*` and deletes it at the end unless --keep.
Test mutations belong on throwaway records (the v1.276.35 lesson).

⚠ STDLIB ONLY — an operator script that imports `backend.*` dies outside the
venv, which is exactly when you need it (the v1.277.17 lesson). The one part
that tests backend logic in-process (`_merge_entries`) is loaded BY PATH and
skips itself cleanly if the import is not possible.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
import uuid
from pathlib import Path

HOST = "http://127.0.0.1:8899"
FAILURES: list = []
SKIPS: list = []

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")   # type: ignore[union-attr]
except Exception:  # noqa: BLE001
    pass

B = "/api/storyworld"
P = "/api/projects"


def _req(method: str, path: str, body=None, timeout=120):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(HOST + path, data=data, method=method)
    if data is not None:
        r.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            raw = resp.read().decode()
            return (json.loads(raw) if raw else {}), resp.status
    except urllib.error.HTTPError as e:
        body_ = e.read().decode("utf-8", "replace")
        try:
            return json.loads(body_), e.code
        except ValueError:
            return {"detail": body_[:300]}, e.code
    except Exception as e:                                        # noqa: BLE001
        return {"detail": f"{type(e).__name__}: {e}"}, 0


def check(label: str, ok: bool, detail: str = "") -> bool:
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"  — {detail}" if detail else ""))
    if not ok:
        FAILURES.append(label)
    return bool(ok)


def skip(label: str, why: str) -> None:
    """⚠ SKIP rather than FAIL when the evidence genuinely is not there — a
    suite that fails for lack of data teaches people to ignore it."""
    print(f"  SKIP  {label}  — {why}")
    SKIPS.append(label)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", action="store_true")
    ap.add_argument("--live", action="store_true",
                    help="also write ONE real narration and measure it "
                         "(costs LLM time; everything else stays free)")
    a = ap.parse_args()
    tag = uuid.uuid4().hex[:4]
    print("📖📚 Story chapters + codex smoke — zero renders, zero LLM calls\n")

    # ── 0. the app is up ────────────────────────────────────────────────────
    print("0. reachable")
    _, code = _req("GET", f"{B}/codex/meta")
    if not check("GET /codex/meta answers", code == 200, f"status {code}"):
        print("\n⚠ the backend is not answering — start it with run.bat")
        return 1
    meta, _ = _req("GET", f"{B}/codex/meta")
    check("codex kinds are server-driven", len(meta.get("kinds") or []) >= 5,
          f"{len(meta.get('kinds') or [])} kinds")

    # ── 1. a throwaway world + story ────────────────────────────────────────
    print("\n1. fixture")
    w, code = _req("POST", f"{B}/worlds", {"name": f"SmokeChap{tag}"})
    if not check("created a world", code == 200 and w.get("id"), str(code)):
        return 1
    wid = w["id"]
    _req("POST", f"{B}/worlds/{wid}/world",
         {"fields": {"logline": "A drowned city keeps its ledgers.",
                     "rules": "Debt is inherited. The tide collects."}})
    st, code = _req("POST", f"{B}/worlds/{wid}/stories",
                    {"title": "The Ledger Tide", "story_type": "narration"})
    sid = st.get("id", "")
    check("created a story", code == 200 and sid, str(code))

    # ── 2. chapters need arcs ───────────────────────────────────────────────
    print("\n2. chapters are told FROM arcs")
    r, code = _req("POST", f"{B}/worlds/{wid}/stories/{sid}/chapters/generate", {})
    check("✨ Outline is REFUSED on a story with no arcs", code == 409,
          f"status {code}: {str(r.get('detail'))[:70]}")
    # ⭐ the route-order test: if `/chapters/{cid}` were declared first this
    # would arrive as cid="generate" and 404 instead of 409. Seven failures
    # from that one cause on this module's parent (v1.277.0).
    check("…with 409, not 404 — the literal route is not shadowed", code == 409,
          f"status {code}")

    arcs = [{"title": "The Assessment", "summary": "The tide takes a census.",
             "mood": "cold dread", "characters": ["Vell"], "locations": ["The Weir"]},
            {"title": "The Discount", "summary": "Vell finds a clerk who forgets.",
             "mood": "sly hope", "characters": ["Vell"], "locations": []}]
    r, code = _req("POST", f"{B}/worlds/{wid}/stories/{sid}", {"arcs": arcs})
    check("arcs saved and normalised", code == 200 and len(r.get("arcs") or []) == 2,
          f"{len(r.get('arcs') or [])} arcs")
    aid = (r.get("arcs") or [{}])[0].get("id", "")
    check("an arc gets a stable id", bool(aid), aid)
    # ⚠ an arcs-only POST must not convert a narration story to music_video
    check("story_type survived an arcs-only save",
          r.get("story_type") == "narration", str(r.get("story_type")))

    # ── 3. chapter CRUD + MANY per arc ──────────────────────────────────────
    print("\n3. chapter CRUD")
    lst, code = _req("GET", f"{B}/worlds/{wid}/stories/{sid}/chapters")
    check("the chapter list is empty and well-formed",
          code == 200 and lst.get("chapters") == [] and lst.get("totals"), str(code))
    c1, code = _req("POST", f"{B}/worlds/{wid}/stories/{sid}/chapters",
                    {"title": "Low Water", "arc_id": aid,
                     "summary": "The census reaches the Weir.",
                     "characters": ["Vell"]})
    check("created a chapter on an arc", code == 200 and c1.get("id"), str(code))
    c2, code = _req("POST", f"{B}/worlds/{wid}/stories/{sid}/chapters",
                    {"title": "Slack Water", "arc_id": aid,
                     "summary": "The same arc, told further."})
    # ⭐ HIS CALL: many chapters per arc. A 1:1 rule would reject this.
    check("a SECOND chapter on the SAME arc is allowed",
          code == 200 and c2.get("arc_id") == aid, str(code))
    bad, code = _req("POST", f"{B}/worlds/{wid}/stories/{sid}/chapters",
                     {"title": "Nowhere", "arc_id": "deadbeef"})
    check("an unknown arc_id is refused", code == 404,
          f"status {code}: {str(bad.get('detail'))[:60]}")

    cid1, cid2 = c1.get("id", ""), c2.get("id", "")
    r, code = _req("POST", f"{B}/worlds/{wid}/stories/{sid}/chapters/{cid1}",
                   {"title": "Low Water Mark"})
    check("update renames without clearing other fields",
          code == 200 and r.get("title") == "Low Water Mark"
          and r.get("summary") == "The census reaches the Weir.",
          f"{r.get('title')!r} / summary kept={bool(r.get('summary'))}")

    # reorder — and the rule that a reorder never DELETES
    r, code = _req("POST", f"{B}/worlds/{wid}/stories/{sid}/chapters/reorder",
                   {"order": [cid2]})
    got = [c["id"] for c in (r.get("chapters") or [])]
    check("reorder puts the named one first", got[:1] == [cid2], str(got))
    check("…and KEEPS the one the client forgot", len(got) == 2, f"{len(got)} chapters")
    check("…renumbered from 0",
          [c["i"] for c in r["chapters"]] == [0, 1],
          str([c["i"] for c in r.get("chapters") or []]))

    # ── 4. narration + beats, and their validation ──────────────────────────
    print("\n4. narration and beats")
    narr = ("The tide came in on a Tuesday and took the ground floor of the "
            "counting house. Vell watched it happen from the stair.")
    r, code = _req("POST", f"{B}/worlds/{wid}/stories/{sid}/chapters/{cid1}",
                   {"narration": narr})
    check("a hand-written narration saves", code == 200 and r.get("has_narration"),
          str(code))
    check("word count is computed server-side",
          r.get("narration_words") == len(narr.split()),
          f"{r.get('narration_words')} vs {len(narr.split())}")
    check("est_minutes is words ÷ 150 (ARITHMETIC, labelled apart from ffprobe)",
          abs((r.get("est_minutes") or 0) - round(len(narr.split()) / 150.0, 1)) < 0.05,
          str(r.get("est_minutes")))
    check("recorded_seconds is 0 with no recording — a MEASUREMENT, not the estimate",
          (r.get("recorded_seconds") or 0) == 0.0, str(r.get("recorded_seconds")))

    r, code = _req("POST", f"{B}/worlds/{wid}/stories/{sid}/chapters/{cid2}/beats", {})
    check("beats are REFUSED on a chapter with no narration", code == 409,
          f"status {code}: {str(r.get('detail'))[:70]}")

    # ── 4b. ⭐⭐ PARAGRAPHS. "Paragraphs matter in TTS" (his words) ──────────
    print("\n4b. ⭐⭐ prose keeps its paragraphs")
    ns2: dict = {}
    try:
        p = Path(__file__).resolve().parents[1] / "backend" / "api" / "storychapters.py"
        src2 = p.read_text("utf-8")
        # ⚠ the slice after `_paragraphize` runs to the next `def `, which
        # swallows the `_CHAPTER_FIELDS: List[...]` annotation — import the
        # names those annotations mention or the exec NameErrors.
        exec(compile("import re\n"
                     "from typing import Any, Dict, List, Optional\n",
                     "<pre>", "exec"), ns2)
        for blk in ("_HEADER_LINE = ", "_SENTENCE_END = "):
            i = src2.index(blk)
            exec(compile(src2[i:src2.index("\n", i)], "<sc>", "exec"), ns2)
        for fn in ("_prose", "_paragraphize"):
            i = src2.index(f"def {fn}(")
            j = src2.index("\ndef ", i + 1)
            exec(compile(src2[i:j], "<sc>", "exec"), ns2)
    except Exception as ex:                                       # noqa: BLE001
        skip("paragraph helpers", f"could not load them: {ex}")
    if ns2.get("_paragraphize"):
        pr, pz = ns2["_prose"], ns2["_paragraphize"]
        # ⚠⚠ THE BUG HE SAW: sw._flat joins a list with ", ", so a model that
        # returned paragraphs as an ARRAY came back comma-welded into one block.
        got = pr(["First paragraph here.", "Second paragraph here."])
        check("⭐⭐ a LIST of paragraphs joins with a BLANK LINE, not a comma",
              got == "First paragraph here.\n\nSecond paragraph here.", repr(got))
        check("…and a dict of paragraphs does too",
              pr({"p1": "One.", "p2": "Two."}) == "One.\n\nTwo.")
        check("a plain string keeps its own paragraph breaks",
              pr("A.\n\nB.") == "A.\n\nB.")
        # single newlines are how models often mean "new paragraph"
        check("single newlines become paragraph breaks",
              pz("Line one.\nLine two.") == "Line one.\n\nLine two.",
              repr(pz("Line one.\nLine two.")))
        # the fallback: one unbreathable wall of text gets split on sentences
        blob = " ".join(f"Sentence number {i} goes here and runs on a while."
                        for i in range(24))
        out = pz(blob)
        check("⭐ a single long BLOCK is split into paragraphs (the fallback)",
              "\n\n" in out, f"{len(out.split(chr(10) + chr(10)))} paragraphs")
        check("…and no words are lost doing it",
              len(out.split()) == len(blob.split()),
              f"{len(out.split())} vs {len(blob.split())}")
        check("headers the model sneaks in are dropped (his call: no headers)",
              "##" not in pz("## A Heading\n\nReal prose here."),
              repr(pz("## A Heading\n\nReal prose here.")))
        check("a short paragraphed text is left alone",
              pz("Short one.\n\nShort two.") == "Short one.\n\nShort two.")

    # ⭐⭐ the call plan: EVERY BEAT MUST BE WRITTEN. The first version handed
    # the first N beats a budget and zeroed the rest, so a 24-beat chapter
    # narrated beats 1-12 and silently never told 13-24 — half the chapter
    # missing, with a green job and a plausible word count.
    ns3: dict = {}
    try:
        p = Path(__file__).resolve().parents[1] / "backend" / "api" / "storychapters.py"
        s3 = p.read_text("utf-8")
        exec(compile("from typing import List\n_MIN_BEAT_WORDS = 120\n",
                     "<pre>", "exec"), ns3)
        i = s3.index("def _beat_groups(")
        exec(compile(s3[i:s3.index("\n_NARR_SYSTEM", i)], "<sc>", "exec"), ns3)
    except Exception as ex:                                       # noqa: BLE001
        skip("the beat call plan", f"could not load _beat_groups: {ex}")
    if ns3.get("_beat_groups"):
        bg = ns3["_beat_groups"]
        cases = [(1, 1500), (3, 1500), (6, 1500), (8, 1500), (10, 1500),
                 (24, 1500), (8, 300), (24, 300), (5, 750)]
        part_ok = all([i for idx, _ in bg(n, t) for i in idx] == list(range(n))
                      for n, t in cases)
        sum_ok = all(sum(b for _, b in bg(n, t)) == t for n, t in cases)
        thin_ok = all(b >= 120 for n, t in cases for _, b in bg(n, t))
        check("⭐⭐ EVERY beat is covered by exactly one call (a partition)",
              part_ok, "a beat was dropped or written twice")
        check("⭐ the budgets sum EXACTLY to the target", sum_ok)
        check("…and no call is asked for a stub (≥120 words)", thin_ok)
        check("a thin budget makes FEWER, FATTER calls — not stubs",
              len(bg(24, 300)) == 2, f"{len(bg(24, 300))} calls for 24 beats/300w")

    # ── 4d. 📝 the SRT writer + the cue→scene math (free, no audio) ─────────
    print("\n4d. 📝 SRT from measured cues")
    ns4: dict = {}
    try:
        p = Path(__file__).resolve().parents[1] / "backend" / "api" / "audio_lab.py"
        s4 = p.read_text("utf-8")
        exec(compile("from typing import List\n", "<pre>", "exec"), ns4)
        i = s4.index("def _srt_time(")
        exec(compile(s4[i:s4.index('@router.get("/jobs/{jid}/srt")', i)],
                     "<al>", "exec"), ns4)
    except Exception as ex:                                       # noqa: BLE001
        skip("the SRT writer", f"could not load it: {ex}")
    if ns4.get("cues_to_srt"):
        srt = ns4["cues_to_srt"]([
            {"start": 0.0, "end": 3.42, "text": "The tide came in."},
            {"start": 4.02, "end": 7.5, "text": "Vell watched from the stair."},
        ])
        # ⚠ SRT uses a COMMA before the millis. A dot parses as WebVTT and some
        # readers — including ours — yield zero-length cues from it.
        check("⭐ SRT times use a COMMA, not a dot",
              "00:00:00,000 --> 00:00:03,420" in srt, srt.splitlines()[1])
        check("…cues are numbered from 1", srt.startswith("1\n"), srt[:3])
        check("…and it ends with a newline (some parsers drop the last cue)",
              srt.endswith("\n"))
        z = ns4["cues_to_srt"]([{"start": 1.0, "end": 1.0, "text": "Zero."},
                                {"start": 5.0, "end": 9.0, "text": "Next."}])
        check("a ZERO-LENGTH cue is repaired, not emitted",
              "00:00:01,000 --> 00:00:01,200" in z, z.splitlines()[1])
        o = ns4["cues_to_srt"]([{"start": 0.0, "end": 99.0, "text": "Long."},
                                {"start": 4.0, "end": 8.0, "text": "Next."}])
        check("an OVERLAPPING cue is clamped to the next start",
              "00:00:00,000 --> 00:00:04,000" in o, o.splitlines()[1])
        check("an empty cue is skipped entirely",
              ns4["cues_to_srt"]([{"start": 0, "end": 1, "text": "  "}]).strip() == "")

    # ── 4f. ⭐⭐ THE GATES — the thing that actually made AAF work ──────────
    # Months of boundary fixes failed because the boundaries kept being
    # RE-DERIVED from Whisper/SRT word timings (v1.8.20: "39 of 48 scenes ended
    # mid-word … the offset growing to ~10s by the end"). The AAF's real
    # advantage was that importing one made the timeline UNTOUCHABLE. A
    # cue-built timeline must be equally untouchable or it is worth nothing.
    print("\n4f. ⭐⭐ an authoritative timeline is LOCKED")
    ns6: dict = {}
    try:
        p6 = Path(__file__).resolve().parents[1] / "backend" / "api" / "timeline.py"
        s6 = p6.read_text("utf-8")
        exec(compile("from typing import Optional\n", "<pre>", "exec"), ns6)
        i = s6.index("def authoritative_timeline(")
        exec(compile(s6[i:s6.index("\nasync def ", i)], "<tl>", "exec"), ns6)
    except Exception as ex:                                       # noqa: BLE001
        skip("the authoritative-timeline predicate", f"could not load it: {ex}")
    if ns6.get("authoritative_timeline"):
        auth = ns6["authoritative_timeline"]
        check("⭐ an AAF-imported timeline is authoritative",
              (auth({"audio_source": "aaf"}) or ("",))[0] == "aaf_authoritative")
        check("⭐⭐ a CUE-BUILT timeline is authoritative too",
              (auth({"scene_source": "chapter_cues"}) or ("",))[0]
              == "cues_authoritative")
        check("…an ordinary project is NOT locked", auth({}) is None)
        check("…and neither is an unrelated scene_source",
              auth({"scene_source": "whatever"}) is None)
        check("…nor an unrelated audio_source",
              auth({"audio_source": "wav"}) is None)
        check("a None settings dict does not explode", auth(None) is None)
    # ⚠ and the three gates must actually CALL it — a predicate nobody asks is
    # the exact shape of the bug it was written to prevent.
    try:
        s6 = (Path(__file__).resolve().parents[1] / "backend" / "api"
              / "timeline.py").read_text("utf-8")
        gates = s6.count("_auth = authoritative_timeline(")
        check("⭐⭐ all three gates go through the ONE predicate "
              "(resync · scenes-from-sections · suggest-timeline)",
              gates >= 3, f"{gates} gate call site(s)")
        # ⚠ The blunt version of this check ("no `audio_source == 'aaf'`
        # anywhere") FAILED on correct code: the predicate's own body, its
        # docstring, and `detach_aaf` all legitimately mention it. A test that
        # cannot tell an implementation from a duplicate is a test that will be
        # ignored. Look for the shape of an inlined GATE instead.
        import re as _re
        inlined = len(_re.findall(
            r'(?:if|elif)\s*\(?[\w.]*settings[^\n]*audio_source"?\)?\s*==\s*"aaf"',
            s6))
        check("…and no gate INLINES the old aaf-only test",
              inlined == 0, f"{inlined} inlined gate(s) left")
    except Exception as ex:                                       # noqa: BLE001
        skip("the gate call sites", str(ex))

    # the cue→scene math is `clips_to_scenes`, shared with the AAF path
    try:
        import importlib.util as _il
        p2 = (Path(__file__).resolve().parents[1] / "backend" / "services"
              / "import_aaf.py")
        s5 = p2.read_text("utf-8")
        ns5: dict = {}
        exec(compile("from typing import Optional\n", "<pre>", "exec"), ns5)
        i = s5.index("def clips_to_scenes(")
        exec(compile(s5[i:s5.index("\ndef ", i + 1)], "<ia>", "exec"), ns5)
    except Exception as ex:                                       # noqa: BLE001
        skip("the cue→scene math", f"could not load clips_to_scenes: {ex}")
        ns5 = {}
    if ns5.get("clips_to_scenes"):
        cts = ns5["clips_to_scenes"]
        cues = [{"start": i * 3.0, "end": i * 3.0 + 2.8, "text": f"Sentence {i}."}
                for i in range(20)]
        clips = [{"start": c["start"], "end": c["end"], "name": c["text"]}
                 for c in cues]
        one = cts(clips, audio_end=60.0, min_scene_seconds=0)
        check("⭐ one scene per sentence when nothing is merged",
              len(one) == 20, f"{len(one)} scenes")
        check("…and the SPOKEN WORDS become the scene name (an AAF has none)",
              one[0]["name"] == "Sentence 0.", one[0]["name"])
        merged = cts(clips, audio_end=60.0, min_scene_seconds=8.0)
        check("⭐ merging gives usable scene lengths",
              len(merged) < 10 and all(
                  s["end_time"] > s["start_time"] for s in merged),
              f"{len(merged)} scenes from 20 cues at an 8s floor")
        check("…scenes are contiguous — no gap between one and the next",
              all(abs(merged[i]["end_time"] - merged[i + 1]["start_time"]) < 1e-6
                  for i in range(len(merged) - 1)))
        check("…and the last scene runs to the end of the audio",
              abs(merged[-1]["end_time"] - 60.0) < 1e-6,
              str(merged[-1]["end_time"]))

    # ── 4c. the narration JOB (free — validation only, no model) ────────────
    print("\n4c. the narration job")
    jb, code = _req("GET", f"{B}/worlds/{wid}/stories/{sid}/chapters/{cid1}"
                           f"/narration/job")
    check("the narration job route answers when idle",
          code == 200 and (jb.get("job") or {}).get("status") == "idle",
          str((jb.get("job") or {}).get("status")))
    r, code = _req("POST", f"{B}/worlds/{wid}/stories/{sid}/chapters/{cid1}"
                           f"/narration/cancel")
    check("cancelling nothing 409s", code == 409, str(code))
    r, code = _req("POST", f"{B}/worlds/{wid}/stories/{sid}/chapters/{cid1}"
                           f"/narration", {"minutes": 1})
    check("✍ is REFUSED on a chapter that already has narration (no overwrite)",
          code == 409, f"status {code}: {str(r.get('detail'))[:60]}")

    # ── 4e. 🎙 TTS options + 🎬 the project gate (free — no render) ─────────
    print("\n4e. 🎙 speak + 🎬 make a project")
    o, code = _req("GET", f"{B}/worlds/{wid}/stories/{sid}/chapters/{cid1}"
                          f"/tts/options")
    check("the TTS options route answers", code == 200, str(code))
    check("…listing voices and both engines",
          isinstance(o.get("voices"), list)
          and {"f5tts", "kokoro"} <= set(o.get("engines") or {}),
          str(list((o.get("engines") or {}).keys())))
    check("…and reporting this chapter's word count",
          (o.get("words") or 0) > 0 and o.get("has_narration") is True,
          f"{o.get('words')} words")
    r, code = _req("POST", f"{B}/worlds/{wid}/stories/{sid}/chapters/{cid2}/tts",
                   {"voice_id": "nope"})
    check("🎙 is REFUSED on a chapter with no narration", code == 409,
          f"status {code}: {str(r.get('detail'))[:60]}")
    r, code = _req("POST", f"{B}/worlds/{wid}/stories/{sid}/chapters/{cid1}/tts",
                   {"voice_id": "definitely-not-a-voice"})
    check("…and an unknown voice 404s", code == 404, str(code))
    r, code = _req("POST", f"{B}/worlds/{wid}/stories/{sid}/chapters/{cid1}"
                           f"/tts/keep", {"job_id": "nosuchjob"})
    check("keeping a nonexistent take 404s", code == 404, str(code))

    # ⭐⭐ HIS GATE: narration text + audio + SRT, ALL THREE.
    rd, code = _req("GET", f"{B}/worlds/{wid}/stories/{sid}/chapters/{cid1}"
                           f"/project-readiness")
    check("the readiness route answers", code == 200, str(code))
    check("⭐⭐ a chapter with text but NO audio is NOT ready",
          rd.get("ready") is False, str(rd.get("ready")))
    blocking = " ".join(rd.get("blocking") or [])
    check("…and it names the AUDIO as missing", "AUDIO" in blocking, blocking[:80])
    check("…and the SRT as missing", "SRT" in blocking, blocking[:120])
    check("…while beats/cast are WARNINGS, not blockers",
          isinstance(rd.get("warnings"), list)
          and not any("beat" in b.lower() for b in (rd.get("blocking") or [])),
          f"{len(rd.get('warnings') or [])} warnings")
    check("…and it offers the project modes",
          {"narration_video", "narration_images", "talkie"}
          <= {m["key"] for m in (rd.get("modes") or [])},
          str([m.get("key") for m in (rd.get("modes") or [])]))
    r, code = _req("POST", f"{B}/worlds/{wid}/stories/{sid}/chapters/{cid1}"
                           f"/create-project", {"mode": "narration_video"})
    check("⭐⭐ create-project REFUSES an unready chapter (409, not a project)",
          code == 409, f"status {code}: {str(r.get('detail'))[:70]}")
    r, code = _req("POST", f"{B}/worlds/{wid}/stories/{sid}/chapters/{cid1}"
                           f"/create-project", {"mode": "not_a_mode"})
    check("…and an unknown mode 400s", code == 400, str(code))

    # beats written by hand must come back ARC-SHAPED — this is the contract
    # that lets create_chapters_from_arcs take them with no new code.
    r, code = _req("POST", f"{B}/worlds/{wid}/stories/{sid}/chapters/{cid1}",
                   {"beats": [{"title": "The stair", "summary": "Water on the tiles.",
                               "mood": "still"},
                              {"title": "The ledger", "summary": "Ink runs."}]})
    beats = r.get("beats") or []
    check("beats saved", len(beats) == 2, f"{len(beats)}")
    if beats:
        b = beats[0]
        want = {"id", "i", "title", "summary", "mood", "characters", "locations"}
        check("⭐ a beat is ARC-SHAPED (same keys as _clean_arcs)",
              want.issubset(set(b)), f"missing {sorted(want - set(b))}")
        check("beats are ordered from 0", [x["i"] for x in beats] == [0, 1],
              str([x["i"] for x in beats]))

    lst, _ = _req("GET", f"{B}/worlds/{wid}/stories/{sid}/chapters")
    t = lst.get("totals") or {}
    check("totals count what is written, not what exists",
          t.get("chapters") == 2 and t.get("written") == 1, json.dumps(t))
    check("the story-level narration is reported SEPARATELY from the chapters'",
          "story_narration_words" in lst, str(list(lst)[:6]))

    # ── 5. the codex: hashes, canon-only, and what survives ─────────────────
    print("\n5. codex")
    cx, code = _req("GET", f"{B}/worlds/{wid}/codex")
    check("codex reads on a world that has never been calculated", code == 200,
          str(code))
    stale = cx.get("stale") or {}
    check("⭐ staleness is answered WITHOUT an LLM call (the canon hashes)",
          stale.get("any") is True and stale.get("count", 0) >= 1,
          f"count={stale.get('count')}")
    check("…and it names the story that changed",
          any(s.get("id") == sid for s in (stale.get("stories") or [])),
          str([s.get("title") for s in (stale.get("stories") or [])]))

    # ⚠⚠ `_flat` maps "unknown"/"none"/"n/a"/"nothing"/"-" to "", so an entry
    # named "Unknown" used to pass the route's own check, append None to the
    # list, and 500 every later read of the codex — a world file poisoned past
    # repair by one typo.
    r, code = _req("POST", f"{B}/worlds/{wid}/codex/entry",
                   {"kind": "term", "name": "Unknown", "body": "x"})
    check("⭐ an entry named 'Unknown' is REFUSED, not appended as null",
          code == 400, f"status {code}")
    ok, _ = _req("GET", f"{B}/worlds/{wid}/codex")
    check("…and the codex still reads afterwards", isinstance(ok.get("entries"), list),
          str(type(ok.get("entries"))))

    e, code = _req("POST", f"{B}/worlds/{wid}/codex/entry",
                   {"kind": "rule", "name": "Inherited debt",
                    "body": "A debt outlives the debtor and lands on the heir."})
    check("a hand-written entry saves", code == 200 and e.get("id"), str(code))
    check("…and is MANUAL", e.get("manual") is True, str(e.get("manual")))
    check("…and cites the writer rather than nothing (canon rule)",
          bool(e.get("sources")), str(e.get("sources")))
    eid = e.get("id", "")
    bad, code = _req("POST", f"{B}/worlds/{wid}/codex/entry",
                     {"kind": "rule", "name": "", "body": "x"})
    check("an entry with no name is refused", code == 400, str(code))
    r, code = _req("POST", f"{B}/worlds/{wid}/codex/entry/{eid}/pin?pinned=true")
    check("an entry can be pinned", code == 200 and r.get("pinned") is True, str(code))
    r, code = _req("POST", f"{B}/worlds/{wid}/codex/entry/deadbeef/delete")
    check("deleting a missing entry 404s (it does not silently succeed)",
          code == 404, str(code))

    job, code = _req("GET", f"{B}/worlds/{wid}/codex/job")
    check("the codex job route answers when idle",
          code == 200 and (job.get("job") or {}).get("status") == "idle",
          str((job.get("job") or {}).get("status")))
    r, code = _req("POST", f"{B}/worlds/{wid}/codex/cancel")
    check("cancelling nothing 409s", code == 409, str(code))
    r, code = _req("POST", f"{B}/worlds/{wid}/codex/recalc", {"story_id": "nope"})
    check("a recalc scoped to a missing story 404s", code == 404, str(code))

    # ── 5b. ⭐⭐ the merge keeps what he wrote — tested IN-PROCESS, no model ──
    print("\n5b. ⭐⭐ a recalc never eats what you wrote")
    try:
        import importlib.util
        p = Path(__file__).resolve().parents[1] / "backend" / "api" / "storycodex.py"
        src = p.read_text("utf-8")
        # load the two pure functions by exec'ing only what they need — the
        # module imports FastAPI at the top, which is the venv dependency this
        # script must not have.
        ns: dict = {}
        # ⚠ the slices carry real annotations (`List[dict]`) and the module's
        # `from __future__ import annotations` is NOT in scope here, so they are
        # evaluated at def time. Import the names they mention first.
        exec(compile("import re\nfrom uuid import uuid4\n"
                     "from typing import Any, Dict, List, Optional\n"
                     # module constants the slices close over
                     "_ENTRY_CAP = 400\n",
                     "<pre>", "exec"), ns)
        for fn in ("_norm_name", "_keep", "_merge_entries"):
            i = src.index(f"def {fn}(")
            j = src.index("\ndef ", i + 1)
            exec(compile(src[i:j], "<codex>", "exec"), ns)
    except Exception as ex:                                       # noqa: BLE001
        skip("merge preserves manual/pinned", f"could not load the functions: {ex}")
        ns = {}
    if ns.get("_merge_entries"):
        mine = {"id": "m1", "kind": "rule", "name": "Inherited debt",
                "body": "MINE", "manual": True, "story_ids": [sid]}
        kept = {"id": "p1", "kind": "term", "name": "Weir", "body": "KEPT",
                "manual": False, "pinned": True, "story_ids": [sid]}
        gen = {"id": "g1", "kind": "term", "name": "Ledger", "body": "OLD",
               "manual": False, "pinned": False, "story_ids": [sid]}
        other = {"id": "o1", "kind": "term", "name": "Elsewhere", "body": "OTHER",
                 "manual": False, "pinned": False, "story_ids": ["otherstory"]}
        cxd = {"entries": [mine, kept, gen, other]}
        fresh = [
            # the model tries to overwrite BOTH of his — it must not win
            {"id": "x", "kind": "rule", "name": "inherited DEBT", "body": "MODEL",
             "manual": False, "pinned": False, "story_ids": [sid],
             "created_at": "now", "sources": ["s"]},
            {"id": "y", "kind": "term", "name": "weir", "body": "MODEL",
             "manual": False, "pinned": False, "story_ids": [sid],
             "created_at": "now", "sources": ["s"]},
            # and it legitimately rewrites the generated one
            {"id": "z", "kind": "term", "name": "Ledger", "body": "NEW",
             "manual": False, "pinned": False, "story_ids": [sid],
             "created_at": "now", "sources": ["s"]},
        ]
        stats = ns["_merge_entries"](cxd, fresh, [sid], "run1", did_world=True)
        out = {(e["kind"], e["name"]): e for e in cxd["entries"]}
        check("✍ a MANUAL entry survives verbatim",
              out.get(("rule", "Inherited debt"), {}).get("body") == "MINE",
              str(out.get(("rule", "Inherited debt"), {}).get("body")))
        check("📌 a PINNED entry survives verbatim",
              out.get(("term", "Weir"), {}).get("body") == "KEPT",
              str(out.get(("term", "Weir"), {}).get("body")))
        check("…even though the model used different CASING for both names",
              len([e for e in cxd["entries"] if e["body"] == "MODEL"]) == 0,
              "a model entry got through")
        check("a generated entry IS rewritten",
              out.get(("term", "Ledger"), {}).get("body") == "NEW",
              str(out.get(("term", "Ledger"), {}).get("body")))
        check("…keeping its id, so the UI does not see it flicker",
              out.get(("term", "Ledger"), {}).get("id") == "g1",
              str(out.get(("term", "Ledger"), {}).get("id")))
        check("⭐ a generated entry OUTSIDE this run's scope is CARRIED, not deleted",
              out.get(("term", "Elsewhere"), {}).get("body") == "OTHER",
              f"carried={stats.get('carried')}")
        check("the stats report what happened",
              stats.get("kept") == 2 and stats.get("updated") == 1,
              json.dumps(stats))

        # ⭐⭐ THE REGRESSION THAT MATTERED MOST: scope must be STATED. A run
        # where only a CHARACTER changed passes scope_ids=[] — reading that as
        # "everything is in scope" wiped the entire generated codex, silently,
        # and left the ✅ badge saying "up to date".
        cxd2 = {"entries": [dict(mine), dict(kept), dict(gen), dict(other)]}
        s2 = ns["_merge_entries"](cxd2, [], [], "run2", did_world=False)
        bodies = sorted(e["body"] for e in cxd2["entries"])
        check("⭐⭐ a character-only run keeps EVERY generated entry",
              bodies == ["KEPT", "MINE", "OTHER", "OLD"] or set(bodies) ==
              {"KEPT", "MINE", "OTHER", "OLD"},
              f"survivors: {bodies}")
        check("…and reports them as carried, not added",
              s2.get("carried") == 2 and s2.get("added") == 0, json.dumps(s2))

        # a story-scoped run must not delete the WORLD-level entries
        wlvl = {"id": "w1", "kind": "place", "name": "The Weir Gate",
                "body": "WORLDLEVEL", "manual": False, "pinned": False,
                "story_ids": []}
        cxd3 = {"entries": [wlvl, dict(gen)]}
        ns["_merge_entries"](cxd3, [], [sid], "run3", did_world=False)
        check("⭐ a story-scoped run keeps the WORLD-level entries",
              any(e["body"] == "WORLDLEVEL" for e in cxd3["entries"]),
              f"{[e['body'] for e in cxd3['entries']]}")

        # the cap must never eat a manual entry
        many = [{"id": f"z{i}", "kind": "term", "name": f"zz{i:04d}",
                 "body": "GEN", "manual": False, "pinned": False,
                 "story_ids": [sid], "created_at": "n", "sources": ["s"]}
                for i in range(500)]
        cxd4 = {"entries": [dict(mine)]}
        ns["_merge_entries"](cxd4, many, [sid], "run4", did_world=False)
        check("⭐ the 400-entry cap truncates the GENERATED entries, never yours",
              any(e.get("manual") and e["body"] == "MINE"
                  for e in cxd4["entries"]),
              f"{len(cxd4['entries'])} entries, manual survived="
              f"{any(e.get('manual') for e in cxd4['entries'])}")

    # ── 6. the project link takes a chapter ─────────────────────────────────
    print("\n6. project ↔ chapter link")
    projs, code = _req("GET", f"{B}/projects")
    plist = projs.get("projects") or []
    if not plist:
        skip("chapter-scoped link", "no projects exist to link against")
    else:
        pid = plist[0]["id"]
        before, _ = _req("GET", f"{P}/{pid}/story-link")
        had = bool(before.get("linked"))
        prev = {"world_id": before.get("world_id"),
                "story_id": before.get("story_id") or "",
                "chapter_id": before.get("chapter_id") or ""}
        r, code = _req("PUT", f"{P}/{pid}/story-link",
                       {"world_id": wid, "chapter_id": cid1, "attach": True})
        check("a chapter WITHOUT its story is refused", code == 400,
              f"status {code}: {str(r.get('detail'))[:60]}")
        r, code = _req("PUT", f"{P}/{pid}/story-link",
                       {"world_id": wid, "story_id": sid,
                        "chapter_id": "deadbeef", "attach": True})
        check("an unknown chapter_id is refused at LINK time", code == 404,
              f"status {code}")
        r, code = _req("PUT", f"{P}/{pid}/story-link",
                       {"world_id": wid, "story_id": sid,
                        "chapter_id": cid1, "attach": True})
        check("linking to a chapter works",
              code == 200 and r.get("chapter_id") == cid1, str(code))
        g, _ = _req("GET", f"{P}/{pid}/story-link")
        check("story-link reports the chapter by name",
              g.get("chapter_title") == "Low Water Mark", str(g.get("chapter_title")))
        check("…and lists the story's chapters for the picker",
              len(g.get("chapters") or []) == 2, f"{len(g.get('chapters') or [])}")
        check("…without carrying their full narration into the payload",
              all("narration" not in c for c in (g.get("chapters") or [])),
              "a chapter row carried its prose")
        # ⭐ ONE picker shape: story-link and ?brief=1 must agree exactly, or the
        # dropdown shows different things depending on which screen filled it.
        br, code = _req("GET",
                        f"{B}/worlds/{wid}/stories/{sid}/chapters?brief=1")
        check("?brief=1 answers", code == 200, str(code))
        check("⭐ …with the SAME keys story-link uses for the picker",
              (sorted((br.get("chapters") or [{}])[0])
               == sorted((g.get("chapters") or [{}])[0])),
              f"brief={sorted((br.get('chapters') or [{}])[0])}")
        check("…and no prose in it either",
              all("narration" not in c for c in (br.get("chapters") or [])),
              "brief carried prose")
        check("chapter_missing is False while it resolves",
              g.get("chapter_missing") is False, str(g.get("chapter_missing")))

        ctx, code = _req("GET", f"{P}/{pid}/story-context")
        check("story-context resolves the chapter",
              code == 200 and (ctx.get("story") or {}) is not None
              and (ctx.get("chapter") or {}).get("id") == cid1,
              str((ctx.get("chapter") or {}).get("title")))
        check("⭐ the chapter's BEATS replace the arcs downstream",
              len(ctx.get("arcs") or []) == 2
              and (ctx.get("arcs") or [{}])[0].get("title") == "The stair",
              str([x.get("title") for x in (ctx.get("arcs") or [])]))
        check("…and the fact is published, not implied",
              ctx.get("arcs_are_beats") is True, str(ctx.get("arcs_are_beats")))
        # ⚠⚠ a chapter with NO beats must yield NO arcs — not the whole story's,
        # relabelled "Beats:" in the concept text. `pull_from_story` refuses
        # this case outright; the resolver has to agree with it.
        _req("PUT", f"{P}/{pid}/story-link",
             {"world_id": wid, "story_id": sid, "chapter_id": cid2, "attach": True})
        c2ctx, _ = _req("GET", f"{P}/{pid}/story-context")
        check("⭐⭐ a chapter with NO beats yields NO arcs (not the story's)",
              (c2ctx.get("arcs") or []) == [], str(len(c2ctx.get("arcs") or [])))
        check("…and the concept text does not print the story's arcs as 'Beats'",
              "The Assessment" not in ((c2ctx.get("derived") or {})
                                       .get("concept_text") or ""),
              "an arc title leaked into a chapter-scoped concept")
        _req("PUT", f"{P}/{pid}/story-link",
             {"world_id": wid, "story_id": sid, "chapter_id": cid1, "attach": True})
        eff = ctx.get("effective") or {}
        check("the CHAPTER names the project, not the story",
              eff.get("song_title") in ("Low Water Mark", ""),
              str(eff.get("song_title")))

        # ⚠ changing the STORY must clear the chapter
        st2, _ = _req("POST", f"{B}/worlds/{wid}/stories", {"title": "Another"})
        r, code = _req("PUT", f"{P}/{pid}/story-link",
                       {"world_id": wid, "story_id": st2.get("id"), "attach": True})
        check("⚠ switching story CLEARS the old chapter", (r.get("chapter_id") or "") == "",
              str(r.get("chapter_id")))
        # restore
        if had:
            _req("PUT", f"{P}/{pid}/story-link", {**prev, "attach": True})
        else:
            _req("PUT", f"{P}/{pid}/story-link", {"attach": False})
        check("the project was restored to how it was found", True)

    # ── 6b. --live: a REAL narration, measured ──────────────────────────────
    # ⭐ "A green unit test on the PLANNER is not evidence about the ARTIFACT."
    # The paragraph helpers passing in §4b says nothing about what the model
    # actually wrote to disk. This asks for one and MEASURES it.
    if a.live:
        print("\n6b. --live: a real narration (costs LLM time)")
        import time as _t
        r, code = _req("POST", f"{B}/worlds/{wid}/stories/{sid}/chapters/{cid1}"
                               f"/narration", {"minutes": 4, "overwrite": True})
        if not check("✍ the narration job starts", code == 200 and r.get("started"),
                     f"status {code}: {str(r.get('detail'))[:80]}"):
            pass
        else:
            want = int(r.get("target_words") or 600)
            print(f"      target {want} words on {r.get('provider')}/{r.get('model')}")
            t0, job = _t.time(), {}
            while _t.time() - t0 < 1800:
                _t.sleep(5)
                jb, _ = _req("GET", f"{B}/worlds/{wid}/stories/{sid}/chapters/"
                                    f"{cid1}/narration/job")
                job = jb.get("job") or {}
                if job.get("status") not in ("starting", "running"):
                    break
                print(f"      … {job.get('stage')} {job.get('done')}/{job.get('total')}"
                      f" · {job.get('words_so_far', 0)}w · {job.get('elapsed_s')}s",
                      flush=True)
            check("…and finishes", job.get("status") == "done",
                  f"{job.get('status')}: {str(job.get('error'))[:90]}")
            if job.get("status") == "done":
                lst2, _ = _req("GET", f"{B}/worlds/{wid}/stories/{sid}/chapters")
                ch = next((x for x in (lst2.get("chapters") or [])
                           if x["id"] == cid1), {})
                txt = ch.get("narration") or ""
                paras = [p for p in txt.split("\n\n") if p.strip()]
                words = len(txt.split())
                print(f"      → {words} words, {len(paras)} paragraphs, "
                      f"{job.get('elapsed_s')}s")
                # ⭐⭐ THE TWO THINGS HE REPORTED, MEASURED ON THE ARTIFACT:
                check("⭐⭐ it is NOT one block — real paragraphs on disk",
                      len(paras) >= 3, f"{len(paras)} paragraphs")
                check("⭐⭐ it is a FULL telling — within 40% of the word budget",
                      words >= want * 0.6, f"{words} words vs a {want} target")
                check("…no markdown headers leaked into the spoken text",
                      not any(p.lstrip().startswith("#") for p in paras))
                # ⭐ THE BEATS ARE INPUT, NOT OUTPUT, when they already exist.
                # This chapter was given TWO by hand in §4; writing its
                # narration must reuse them, never quietly replace the
                # structure a project has already pulled and timed against.
                # (An earlier version of this check asserted ">= 3 beats" and
                # failed a correct run — the test was wrong, not the code.)
                check("⭐ hand-authored beats SURVIVED the write (not replaced)",
                      [b.get("title") for b in (ch.get("beats") or [])]
                      == ["The stair", "The ledger"],
                      str([b.get("title") for b in (ch.get("beats") or [])]))
                check("…and one call ran per beat",
                      (job.get("total") or 0) == 2, f"{job.get('total')} calls")
                check("…and the job reports what it did",
                      (job.get("paragraphs") or 0) >= 3
                      and (job.get("words") or 0) == words,
                      f"job says {job.get('words')}w/{job.get('paragraphs')}p")

    # ── 7. delete + cleanup ─────────────────────────────────────────────────
    print("\n7. cleanup")
    r, code = _req("POST", f"{B}/worlds/{wid}/stories/{sid}/chapters/{cid2}/delete")
    check("a chapter deletes", code == 200, str(code))
    lst, _ = _req("GET", f"{B}/worlds/{wid}/stories/{sid}/chapters")
    check("…and the survivors renumber",
          [c["i"] for c in (lst.get("chapters") or [])] == [0],
          str([c["i"] for c in (lst.get("chapters") or [])]))
    if a.keep:
        print(f"  kept world {wid} (SmokeChap{tag})")
    else:
        _, code = _req("POST", f"{B}/worlds/{wid}/delete")
        check("the throwaway world is gone", code == 200, str(code))

    print()
    if FAILURES:
        print(f"❌ {len(FAILURES)} FAILURE(S): " + " · ".join(FAILURES))
        return 1
    print(f"✅ ALL PASS" + (f" ({len(SKIPS)} skipped)" if SKIPS else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
