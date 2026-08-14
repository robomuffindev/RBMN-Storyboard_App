"""🌍 Story/World Builder smoke test — the whole lane for ZERO renders, ZERO LLM.

WHY IT CAN BE FREE
------------------
Everything that makes the mode a *system* — the world store, merge semantics,
stories/texts/cast CRUD, project links, the estimate, and the bridge into the
autogen serial queue — costs nothing to exercise. The one submission it makes
is level=`details`: stages=[character] only, which writes the klein3 record and
its description fields and renders NOTHING. LLM enhance routes are exercised
only for their VALIDATION (bad section, empty idea) — no model is called.

    python scripts/storyworld_smoke.py            # everything, then clean up
    python scripts/storyworld_smoke.py --keep     # leave the artefacts behind

⚠ It creates a real world `SmokeWorld*` and a real character `SmokeCastMember*`.
Both are deleted at the end unless --keep. Test mutations belong on throwaway
records (the v1.276.35 lesson).
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
import uuid

HOST = "http://127.0.0.1:8899"
FAILURES: list = []

# ⚠ cp1252 console + non-ASCII in this file: reconfigure or die on print #1.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")   # type: ignore[union-attr]
except Exception:  # noqa: BLE001
    pass

B = "/api/storyworld"


def _req(method: str, path: str, body=None, timeout=300):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(HOST + path, data=data, method=method)
    if data is not None:
        r.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            return json.loads(resp.read().decode()), resp.status
    except urllib.error.HTTPError as e:
        body_ = e.read().decode("utf-8", "replace")
        try:
            return json.loads(body_), e.code
        except ValueError:
            return {"detail": body_[:300]}, e.code


def check(label: str, ok: bool, detail: str = "") -> bool:
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"  — {detail}" if detail else ""))
    if not ok:
        FAILURES.append(label)
    return ok


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", action="store_true")
    a = ap.parse_args()
    tag = uuid.uuid4().hex[:4]

    print("🌍 Story/World Builder smoke test — zero renders, zero LLM calls\n")

    # ── 0. vocab + llms ─────────────────────────────────────────────────────
    print("0. meta")
    m, code = _req("GET", f"{B}/meta")
    check("GET /meta is 200", code == 200, str(code))
    check("world fields include the sheet", any(
        f.get("key") == "visual_style" for f in m.get("world_fields") or []))
    check("levels ladder is complete",
          m.get("levels") == ["details", "base", "views", "clothing",
                              "sheet", "dataset", "lora"],
          str(m.get("levels")))
    l, code = _req("GET", f"{B}/llms")
    check("GET /llms is 200 and lists providers", code == 200 and
          {o["provider"] for o in l.get("options") or []} >=
          {"ollama", "openai", "anthropic", "gemini"})

    # ── 1. worlds CRUD + merge semantics ────────────────────────────────────
    print("\n1. worlds")
    _, code = _req("POST", f"{B}/worlds", {"name": "  "})
    check("a blank world name is a 400", code == 400, str(code))
    w, code = _req("POST", f"{B}/worlds", {"name": f"SmokeWorld {tag}"})
    wid = w.get("id")
    check("POST /worlds creates", code == 200 and bool(wid), str(wid))
    ws, _ = _req("GET", f"{B}/worlds")
    check("the list shows it", any(x["id"] == wid for x in ws.get("worlds") or []))
    _req("POST", f"{B}/worlds/{wid}/world", {"fields": {"genre": "neon noir"}})
    r, _ = _req("POST", f"{B}/worlds/{wid}/world", {"fields": {"tone": "wistful"}})
    check("field updates MERGE (second write keeps the first)",
          r["world"].get("genre") == "neon noir" and
          r["world"].get("tone") == "wistful", str(r.get("world")))
    r, _ = _req("POST", f"{B}/worlds/{wid}/world",
                {"fields": {"genre": "", "bogus_key": "x"}})
    check("an explicit '' clears; unknown keys are dropped",
          r["world"].get("genre") == "" and "bogus_key" not in r["world"])
    _, code = _req("GET", f"{B}/worlds/nosuchworld_zz")
    check("a missing world is a 404", code == 404, str(code))

    # ── 2. stories + texts ──────────────────────────────────────────────────
    print("\n2. stories + texts")
    st, code = _req("POST", f"{B}/worlds/{wid}/stories",
                    {"title": "First Light", "story_type": "music_video",
                     "fields": {"logline": "a courier outruns the dawn"}})
    sid = st.get("id")
    check("a story is created inside the world", code == 200 and bool(sid))
    st2, _ = _req("POST", f"{B}/worlds/{wid}/stories/{sid}",
                  {"fields": {"hook": "the city wakes up angry"}})
    check("story fields MERGE too",
          st2["fields"].get("logline") and st2["fields"].get("hook"))
    t, code = _req("POST", f"{B}/worlds/{wid}/texts",
                   {"kind": "lyrics", "title": "verse one",
                    "body": "la la la", "story_id": sid})
    tid = t.get("id")
    check("a lyrics text attaches to the story", code == 200 and
          t.get("story_id") == sid)
    _, code = _req("POST", f"{B}/worlds/{wid}/texts",
                   {"kind": "lyrics", "title": "x", "story_id": "nosuch"})
    check("a text naming a missing story is a 404", code == 404, str(code))
    _req("POST", f"{B}/worlds/{wid}/stories/{sid}/delete")
    wfull, _ = _req("GET", f"{B}/worlds/{wid}")
    kept = [x for x in wfull.get("texts") or [] if x["id"] == tid]
    check("deleting the story KEEPS the text, unlinked",
          kept and kept[0].get("story_id") == "", str(kept))

    # ── 3. cast CRUD ────────────────────────────────────────────────────────
    print("\n3. cast")
    name = f"SmokeCastMember {tag}"
    c, code = _req("POST", f"{B}/worlds/{wid}/cast",
                   {"name": name, "role": "the courier", "importance": "lead",
                    "fields": {"sex": "female", "hair": "silver bob",
                               "nonsense": "dropped"},
                    "lore": {"backstory": "grew up on the transit lines"}})
    cid = c.get("id")
    check("a cast member is created", code == 200 and bool(cid))
    check("unknown field keys are dropped, known kept",
          c["fields"].get("hair") == "silver bob" and
          "nonsense" not in c["fields"])
    _, code = _req("POST", f"{B}/worlds/{wid}/cast", {"name": name.lower()})
    check("a duplicate name (case-insensitive) is a 409", code == 409, str(code))
    c2, _ = _req("POST", f"{B}/worlds/{wid}/cast/{cid}",
                 {"outfits": [{"name": "street", "description":
                               "a grey hooded windbreaker, black cargo "
                               "trousers, red trainers"}]})
    check("outfits save on the member", len(c2.get("outfits") or []) == 1)

    # ── 4. validation on the LLM routes (no model is called) ────────────────
    print("\n4. LLM route validation (free)")
    _, code = _req("POST", f"{B}/worlds/{wid}/enhance/field",
                   {"section": "nowhere", "field": "x"})
    check("a bad section is a 400", code == 400, str(code))
    _, code = _req("POST", f"{B}/worlds/{wid}/bigbang", {"idea": "   "})
    check("a blank big-bang idea is a 400", code == 400, str(code))

    # ── 5. the bridge: estimate, then a FREE details-only submission ────────
    print("\n5. submit → autogen")
    _, code = _req("POST", f"{B}/worlds/{wid}/cast/submit",
                   {"cast_ids": [cid], "level": "warp9"})
    check("an unknown level is a 400", code == 400, str(code))
    e1, _ = _req("POST", f"{B}/worlds/{wid}/cast/submit",
                 {"cast_ids": [cid], "level": "details",
                  "estimate_only": True})
    e2, _ = _req("POST", f"{B}/worlds/{wid}/cast/submit",
                 {"cast_ids": [cid], "level": "lora", "estimate_only": True})
    check("estimate: lora level costs more than details",
          (e2.get("total_seconds") or 0) > (e1.get("total_seconds") or 0),
          f"{e1.get('total_seconds')} -> {e2.get('total_seconds')}")
    check("estimate_only queued NOTHING",
          "jobs" not in e1, str(list(e1)))
    r, code = _req("POST", f"{B}/worlds/{wid}/cast/submit",
                   {"cast_ids": [cid], "level": "details"})
    check("a details-only submission queues", code == 200 and r.get("started"),
          json.dumps({k: r[k] for k in ("batch", "queue") if k in r}))
    jid = (r.get("jobs") or [{}])[0].get("id", "")
    check("the member is marked submitted", bool(jid))

    # ⚠ the queue is strictly serial and may be busy with a REAL batch — a
    # test that FAILS because the user is rendering seven characters teaches
    # people to ignore it (the autogen_smoke SKIP philosophy). Only wait for
    # the end-to-end when our job is effectively next in line.
    aj, _ = _req("GET", "/api/autogen/jobs?limit=50")
    others = [x for x in (aj.get("queue") or []) if x != jid] \
        + [x for x in (aj.get("running") or []) if x != jid]
    slug, stage = "", ""
    if others:
        print(f"  SKIP  end-to-end wait — the queue is busy with "
              f"{len(others)} real job(s); our details job stays queued "
              f"behind them and is cleaned up below")
    else:
        t0 = time.time()
        while time.time() - t0 < 180:
            s, _ = _req("GET", f"{B}/worlds/{wid}/cast/status")
            row = (s.get("jobs") or {}).get(cid) or {}
            stage = row.get("stage") or ""
            if stage in ("done", "error", "cancelled"):
                slug = (s.get("cast") or {}).get(cid, {}).get("char_slug") or ""
                break
            time.sleep(2)
        check("the details job finished", stage == "done", stage)
        check("the member became generated with a slug", bool(slug), slug)
        if slug:
            ch, code = _req("GET", f"/api/klein3/characters/{slug}")
            check("the klein3 character EXISTS with the paper fields",
                  code == 200 and (ch.get("fields") or {}).get("hair") ==
                  "silver bob", str((ch.get("fields") or {}).get("hair")))
    _, code = _req("POST", f"{B}/worlds/{wid}/cast/submit",
                   {"cast_ids": [], "level": "details"})
    check("submitting with nobody left on paper is a 400", code == 400,
          str(code))

    # ── 5b. 🎨 visual style (validation only — no render, no LLM) ───────────
    print("\n5b. visual style")
    m2, _ = _req("GET", f"{B}/meta")
    check("meta lists style presets incl. custom", any(
        p.get("key") == "custom" for p in m2.get("style_presets") or []))
    _, code = _req("POST", f"{B}/worlds/{wid}/style", {"preset": "vaporwave9"})
    check("an unknown style preset is a 400", code == 400, str(code))
    r, code = _req("POST", f"{B}/worlds/{wid}/style",
                   {"preset": "anime", "custom_text": "neon rain everywhere"})
    check("setting a style returns the joined style text",
          code == 200 and "anime" in (r.get("style_text") or "") and
          "neon rain" in (r.get("style_text") or ""),
          (r.get("style_text") or "")[:80])
    _, code = _req("POST", f"{B}/worlds/{wid}/style/samples",
                   {"count": 2, "model": "warpdrive"})
    check("an unknown sample model is a 400", code == 400, str(code))
    j2, code = _req("GET", f"{B}/worlds/{wid}/style/job")
    check("the style job route answers (empty job)", code == 200 and
          isinstance(j2.get("job"), dict))
    s2, code = _req("GET", f"{B}/worlds/{wid}/style/samples")
    check("the samples list is empty and well-formed", code == 200 and
          s2.get("samples") == [])

    # ── 5c. ⏸ queue pause (uses the REAL autogen queue, no renders) ─────────
    print("\n5c. queue pause")
    p1, code = _req("POST", "/api/autogen/queue/pause", {"paused": True})
    check("pausing the queue is accepted and persisted", code == 200 and
          p1.get("paused") is True)
    h2, _ = _req("GET", "/api/autogen/jobs")
    check("the board reports paused", h2.get("paused") is True)
    p2, code = _req("POST", "/api/autogen/queue/pause", {"paused": False})
    check("unpausing restores the queue", code == 200 and
          p2.get("paused") is False)

    # ── 5d. 🧥 outfit charsheet validation (free) ───────────────────────────
    print("\n5d. outfit charsheet")
    _, code = _req("POST", "/api/charsheet/generate",
                   {"slug": "nosuchcharacter-zz", "preset": "outfit",
                    "outfit_name": "x"})
    check("an outfit sheet for a missing character is a 404", code == 404,
          str(code))
    _, code = _req("POST", "/api/charsheet/generate",
                   {"slug": "nosuchcharacter-zz", "preset": "outfit"})
    check("the outfit preset without an outfit_name is a 400", code == 400,
          str(code))

    # ── 6. projects surface ─────────────────────────────────────────────────
    print("\n6. projects")
    p, code = _req("GET", f"{B}/projects")
    check("GET /projects lists for the attach picker", code == 200 and
          isinstance(p.get("projects"), list))
    _, code = _req("POST", f"{B}/worlds/{wid}/projects",
                   {"project_id": "not-a-real-project", "attach": True})
    check("attaching a missing project is a 404", code == 404, str(code))

    # ── 7. cleanup ──────────────────────────────────────────────────────────
    print("\n7. cleanup")
    # belt & braces: never leave the queue paused behind a failed run
    _req("POST", "/api/autogen/queue/pause", {"paused": False})
    if a.keep:
        print(f"  --keep: world {wid} and character {slug!r} left in place")
    else:
        n = 0
        if slug:
            _, code = _req("POST", f"/api/klein3/characters/{slug}/delete")
            n += int(code == 200)
        if jid:
            _req("POST", f"/api/autogen/jobs/{jid}/cancel")   # dequeues if queued
            _req("POST", f"/api/autogen/jobs/{jid}/delete")
        _, code = _req("POST", f"{B}/worlds/{wid}/delete")
        n += int(code == 200)
        check("test world + character removed", n == (2 if slug else 1),
              f"{n} deleted")

    print()
    if FAILURES:
        print(f"❌ {len(FAILURES)} FAILURE(S): {FAILURES}")
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
