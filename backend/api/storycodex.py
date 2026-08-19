"""📚 THE CODEX — the world's cheat sheet, and every character's history.

His brief (2026-08-18):

    "a Codex tab to the world that always gets updated when things change so we
     can almost have a cheat sheet for the world. Maybe also a codex page for
     characters as well so we can keep track of events and major things that
     have happened to them for situations where we want to create a continuous
     series and would help us create stories with depth and evolve the
     characters as we go … an option on the story tab to re-calculate codex …
     I would prefer to use ollama on one of the LLM workers."

────────────────────────────────────────────────────────────────────────────
WHAT IT IS

Two stores, both living on the world file:

    w["codex"]["entries"]      the WORLD codex — factions, rules, places,
                               items, terms, and a dated event timeline
    w["codex"]["characters"]   per cast member — a state line, the events that
                               happened TO them, and their relationships

────────────────────────────────────────────────────────────────────────────
THE FOUR RULES THIS THING LIVES OR DIES BY

⭐ **1. CANON ONLY (his call).** Every entry is derived from something WRITTEN —
world fields, a story's prose, a chapter's narration, a cast sheet, a location
sheet — and carries `sources` naming them. The prompt forbids invention and the
merge drops any entry with no source. **This is the whole point.** A codex that
invents lore will eventually contradict a story he writes later, and he will
have no way to tell which half was real. A codex that only repeats what is
written can be trusted as the continuity bible for a series.

⭐⭐ **2. A RECALC MUST NEVER EAT WHAT HE TYPED.** `manual` (he wrote it) and
`pinned` (the model wrote it, he kept it) both survive every recalc. There is
exactly ONE predicate — `_keep()` — and every delete path goes through it.
This is the `source="story"` lesson from the project chapter rebuild, where the
same rule needed FIVE separate predicates widened and four of them were missed
on the first pass. One predicate, one place, on purpose.

⭐ **3. INCREMENTAL, or it is unusable.** Every story and every character gets a
canon HASH over the exact text that feeds it. An unchanged hash is SKIPPED and
the job says so out loud. A ten-story world otherwise costs forty LLM calls to
rewrite forty identical entries. `force=true` re-reads everything.

⭐ **4. LIVE, VERBOSE STATUS — the standing rule.** The recalc runs on a thread
and publishes stage · what it is doing · WHERE (provider/model/host) · elapsed ·
per-stage durations · a change log. Every finished run is kept on the world as
benchmarking data (`codex["runs"]`, newest first, capped at 12).

────────────────────────────────────────────────────────────────────────────
⚠ WHY THE LLM CONFIG IS RESOLVED IN THE ROUTE AND NOT IN THE THREAD

`_llm_cfg` needs the async DB session; the worker is a plain thread because
`concept._call_llm` is blocking. Resolving the provider/key/model up front and
handing the tuple to the thread keeps the thread free of both the event loop
and the session — the alternative (a session inside the thread) is the
self-HTTP-from-async deadlock class wearing a different hat.

⚠ Route order: `/codex/entry/{eid}/…` and `/codex/character/{cid}` are distinct
literals, but `/codex/recalc`, `/codex/job` and `/codex/cancel` are declared
before anything parameterised. Keep it that way.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
import time
from typing import Any, Dict, List, Optional, Tuple
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api import storyworld as sw
from backend.database.database import get_session

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/storyworld", tags=["storyworld"])

# wid → live recalc status. In-memory on purpose: a recalc is minutes, not
# hours, and a restart mid-run should leave no ghost claiming to be running.
_CODEX_JOBS: Dict[str, dict] = {}
_RUN_CAP = 12               # runs kept on the world as benchmarking data
_ENTRY_CAP = 400            # a codex, not a wiki dump

#: The kinds a world entry can have. SERVER-DRIVEN like every other vocab in
#: this library — the frontend renders what `/codex/meta` lists.
_CODEX_KINDS: List[Dict[str, str]] = [
    {"key": "faction",  "label": "Faction",      "hint": "a group, house, company or power"},
    {"key": "rule",     "label": "World rule",   "hint": "how something works here — magic, tech, law"},
    {"key": "place",    "label": "Place",        "hint": "somewhere the stories go"},
    {"key": "item",     "label": "Item",         "hint": "an object that matters"},
    {"key": "term",     "label": "Term",         "hint": "a word this world uses that ours does not"},
    {"key": "event",    "label": "Event",        "hint": "something that happened, in order"},
    {"key": "concept",  "label": "Concept",      "hint": "an idea, custom or belief"},
]
_KIND_KEYS = [k["key"] for k in _CODEX_KINDS]


# ══════════════════════════════════════════════════════════════════════════
# the store
# ══════════════════════════════════════════════════════════════════════════
def _codex(w: dict) -> dict:
    cx = w.get("codex")
    if not isinstance(cx, dict):
        cx = {}
    cx.setdefault("entries", [])
    cx.setdefault("characters", {})
    cx.setdefault("hashes", {})
    cx.setdefault("runs", [])
    cx.setdefault("generated_at", "")
    cx.setdefault("updated_at", "")
    w["codex"] = cx
    return cx


def _keep(x: dict) -> bool:
    """⭐⭐ THE ONE PREDICATE. Anything he wrote or kept survives a recalc.

    Every delete/replace path in this module calls this. Do not inline the
    test anywhere — the project chapter rebuild has the same rule spread over
    five predicates and shipped with four of them wrong."""
    return bool(x.get("manual") or x.get("pinned"))


def _norm_name(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def _entry(row: dict, *, manual: bool = False, run_id: str = "") -> Optional[dict]:
    name = sw._flat(row.get("name") or "", 160)
    body = sw._flat(row.get("body") or row.get("summary") or "", 3000)
    if not name or not body:
        return None
    kind = str(row.get("kind") or "").strip().lower()
    return {
        "id": str(row.get("id") or uuid4().hex[:8]),
        "kind": kind if kind in _KIND_KEYS else "concept",
        "name": name, "body": body,
        "tags": [sw._flat(t, 40) for t in (row.get("tags") or []) if sw._flat(t, 40)][:8],
        # ⭐ CANON: where this came from. An entry with no source is dropped.
        "sources": [sw._flat(s, 120) for s in (row.get("sources") or []) if sw._flat(s, 120)][:12],
        "story_ids": [str(s) for s in (row.get("story_ids") or [])][:24],
        "manual": bool(manual), "pinned": bool(row.get("pinned")),
        "created_at": row.get("created_at") or sw._now(),
        "updated_at": sw._now(), "run_id": run_id,
    }


# ══════════════════════════════════════════════════════════════════════════
# canon: what the codex is allowed to read, and its hash
# ══════════════════════════════════════════════════════════════════════════
def _story_canon(w: dict, st: dict) -> str:
    """Everything WRITTEN about one story, in one block. This exact text is
    both the LLM's input and the thing we hash — so a change to anything the
    codex could possibly have read is a change to the hash, and nothing else
    is. Hashing the whole world file instead would mean renaming a location
    invalidates every story."""
    sid = st.get("id") or ""
    out = [f"STORY: {st.get('title')} (type: {st.get('story_type') or '—'})"]
    for f in sw._STORY_FIELDS:
        v = (st.get("fields") or {}).get(f["key"], "").strip()
        if v:
            out.append(f"{f['key']}: {v}")
    for a in st.get("arcs") or []:
        out.append(f"ARC [{a.get('id')}] {a.get('title')}: {a.get('summary')} "
                   f"(mood: {a.get('mood')}; present: "
                   f"{', '.join(a.get('characters') or []) or '—'}; where: "
                   f"{', '.join(a.get('locations') or []) or '—'})")
    for c in st.get("chapters") or []:
        out.append(f"CHAPTER [{c.get('id')}] {c.get('title')}: {c.get('summary')}")
        narr = (c.get("narration") or "").strip()
        if narr:
            # the narration IS the most authoritative canon in the file — it is
            # the only text that says what actually happens, sentence by
            # sentence. Capped so one long chapter cannot crowd out the rest.
            out.append(f"CHAPTER NARRATION [{c.get('id')}]:\n{narr[:24000]}")
    t = sw._story_narration(w, sid)
    if t and (t.get("body") or "").strip():
        out.append(f"STORY NARRATION:\n{t['body'][:16000]}")
    return "\n\n".join(out)


def _world_canon(w: dict) -> str:
    out = [sw._ctx_world(w)]
    locs = sw._ctx_locations(w)
    if locs:
        out.append(locs)
    for l in w.get("locations") or []:
        fs = l.get("fields") or {}
        bits = "; ".join(f"{k}: {v}" for k, v in fs.items() if (v or "").strip())
        if bits:
            out.append(f"LOCATION SHEET — {l.get('name')} "
                       f"({l.get('kind') or 'place'}): {bits}")
    return "\n\n".join(out)


def _char_canon(w: dict, m: dict) -> str:
    out = [f"CHARACTER: {m.get('name')} ({m.get('role') or 'character'}; "
           f"{m.get('importance') or 'support'})"]
    for f in sw._CAST_LORE_FIELDS:
        v = (m.get("lore") or {}).get(f["key"], "")
        v = v.strip() if isinstance(v, str) else ""
        if v:
            out.append(f"{f['key']}: {v}")
    mine = [s for s in (w.get("stories") or [])
            if not m.get("story_ids") or s.get("id") in (m.get("story_ids") or [])]
    for st in mine:
        out.append(_story_canon(w, st))
    return "\n\n".join(out)


def _h(text: str) -> str:
    return hashlib.sha1((text or "").encode("utf-8", "replace")).hexdigest()[:16]


def _char_hash(w: dict, m: dict, story_h: Dict[str, str]) -> str:
    """A character's canon hash, composed from PRE-COMPUTED story digests.

    ⭐ Same equivalence as hashing `_char_canon` whole — the character's own
    sheet plus the stories it reads — but it reuses the story digests instead of
    re-serialising every chapter's narration once per cast member. Used by both
    the free staleness check and the recalc, so the two can never disagree about
    whether something changed."""
    own = [f"{m.get('name')}|{m.get('role')}|{m.get('importance')}"]
    for f in sw._CAST_LORE_FIELDS:
        v = (m.get("lore") or {}).get(f["key"], "")
        own.append(f"{f['key']}:{v if isinstance(v, str) else ''}")
    ids = [s.get("id") for s in (w.get("stories") or [])
           if not m.get("story_ids") or s.get("id") in (m.get("story_ids") or [])]
    own += [story_h.get(i) or "" for i in ids]
    return _h("\n".join(own))


# ══════════════════════════════════════════════════════════════════════════
# 📚 codex_brief — what OTHER lanes get to read
# ══════════════════════════════════════════════════════════════════════════
def codex_brief(w: dict, story_id: str = "", limit: int = 40) -> str:
    """A compact cheat sheet for injection into any other LLM call.

    ⭐ This is the payoff. A codex nobody reads is a wiki; a codex every
    prompt reads is continuity. Scoped to a story when one is given, because
    a twelve-story world's full codex would crowd the actual ask out of the
    context window."""
    cx = _codex(dict(w))
    ents = cx.get("entries") or []
    if story_id:
        scoped = [e for e in ents
                  if not e.get("story_ids") or story_id in (e.get("story_ids") or [])]
        ents = scoped or ents
    if not ents:
        return ""
    ents = sorted(ents, key=lambda e: (e.get("kind") or "", _norm_name(e.get("name"))))
    lines = ["WORLD CODEX (established canon — treat as already true, never "
             "contradict it):"]
    for e in ents[:limit]:
        lines.append(f"- [{e.get('kind')}] {e.get('name')}: "
                     f"{(e.get('body') or '')[:280]}")
    return "\n".join(lines)


def character_brief(w: dict, names: List[str], limit: int = 8) -> str:
    """The 'what has already happened to them' block, for a continuing series."""
    cx = _codex(dict(w))
    chars = cx.get("characters") or {}
    by_name = {}
    for m in w.get("cast") or []:
        e = chars.get(m.get("id") or "")
        if e:
            by_name[_norm_name(m.get("name"))] = (m, e)
    want = [by_name[_norm_name(n)] for n in (names or [])
            if _norm_name(n) in by_name] or list(by_name.values())
    if not want:
        return ""
    lines = ["CHARACTER CODEX (what has already happened to them):"]
    for m, e in want[:limit]:
        lines.append(f"- {m.get('name')} — {(e.get('state') or e.get('summary') or '')[:280]}")
        for ev in (e.get("events") or [])[-4:]:
            lines.append(f"    · {ev.get('title')}: {(ev.get('body') or '')[:180]}")
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════════
# reading
# ══════════════════════════════════════════════════════════════════════════
@router.get("/codex/meta")
async def codex_meta():
    return {"kinds": _CODEX_KINDS}


@router.get("/worlds/{wid}/codex")
async def get_codex(wid: str, story_id: str = ""):
    w = sw._load(wid)
    cx = _codex(w)
    ents = cx.get("entries") or []
    if story_id:
        ents = [e for e in ents
                if not e.get("story_ids") or story_id in (e.get("story_ids") or [])]
    chars = []
    for m in w.get("cast") or []:
        e = (cx.get("characters") or {}).get(m.get("id") or "") or {}
        chars.append({"id": m.get("id"), "name": m.get("name"),
                      "role": m.get("role") or "", "codex": e,
                      "events": len(e.get("events") or []),
                      "has_codex": bool(e.get("summary") or e.get("events"))})
    by_kind: Dict[str, int] = {}
    for e in ents:
        by_kind[e.get("kind") or "concept"] = by_kind.get(e.get("kind") or "concept", 0) + 1
    stale = _stale_report(w)
    return {
        "entries": sorted(ents, key=lambda e: (e.get("kind") or "",
                                               _norm_name(e.get("name")))),
        "characters": chars, "kinds": _CODEX_KINDS, "by_kind": by_kind,
        "generated_at": cx.get("generated_at") or "",
        "updated_at": cx.get("updated_at") or "",
        "runs": (cx.get("runs") or [])[:_RUN_CAP],
        "manual": sum(1 for e in ents if e.get("manual")),
        "pinned": sum(1 for e in ents if e.get("pinned")),
        # ⭐ "is the codex behind?" answered without an LLM call — the hashes
        # already know. This is what lets the Story tab show a 🔴 dot.
        "stale": stale,
    }


def _stale_report(w: dict) -> dict:
    """Which stories/characters/world have changed since the codex last read
    them — answered from HASHES, with no LLM call.

    ⚠ **This runs on every `GET /codex`, so it must stay cheap.** The naive
    version called `_char_canon` per cast member, and that embeds the full
    `_story_canon` — including up to 24 000 characters of narration per chapter
    — for every story the character is in. A ten-cast, five-story world rebuilt
    and SHA-1'd tens of megabytes of Python strings inside an async route, on
    every tab load. The story hashes are computed ONCE and a character's hash is
    composed from its own sheet plus those digests, which is the same
    equivalence relation at a fraction of the cost."""
    cx = _codex(w)
    hashes = cx.get("hashes") or {}
    story_h = {st.get("id"): _h(_story_canon(w, st))
               for st in (w.get("stories") or [])}
    stories = [{"id": st.get("id"), "title": st.get("title")}
               for st in (w.get("stories") or [])
               if story_h.get(st.get("id")) != hashes.get(f"story:{st.get('id')}")]
    chars = [{"id": m.get("id"), "name": m.get("name")}
             for m in (w.get("cast") or [])
             if _char_hash(w, m, story_h) != hashes.get(f"char:{m.get('id')}")]
    world_stale = _h(_world_canon(w)) != hashes.get("world:")
    return {"world": world_stale, "stories": stories, "characters": chars,
            "any": bool(world_stale or stories or chars),
            "count": len(stories) + len(chars) + (1 if world_stale else 0)}


# ══════════════════════════════════════════════════════════════════════════
# ♻ recalc
# ══════════════════════════════════════════════════════════════════════════
class CodexRecalcIn(BaseModel):
    force: bool = False              # ignore the hashes, re-read everything
    story_id: str = ""               # just this story (and its characters)
    do_world: bool = True
    do_characters: bool = True
    llm: Optional[sw.LlmPick] = None


_CODEX_SYSTEM = (
    "You are a continuity editor building a story bible. You extract ONLY what "
    "the supplied material actually states or unambiguously shows. "
    "YOU NEVER INVENT. If the material does not say a faction has a leader, "
    "you do not give it one; if a place is only named, your entry says only "
    "that it is a place that is named. Every entry must cite the material it "
    "came from. You never use franchise, brand or real-celebrity names. "
    "You answer with JSON only."
)


def _job(wid: str) -> dict:
    return _CODEX_JOBS.setdefault(wid, {"status": "idle"})


def _tick(wid: str, detail: str, *, stage: str = "") -> None:
    """Log only when the text CHANGES — every poll would be spam, a change is
    an event (the v1.276.46 lesson from the autogen board)."""
    j = _CODEX_JOBS.get(wid)
    if not j:
        return
    if stage:
        j["stage"] = stage
    if j.get("detail") == detail:
        return
    j["detail"] = detail
    j.setdefault("log", []).append(
        {"t": round(time.time() - float(j.get("t0") or time.time()), 1),
         "stage": j.get("stage") or "", "detail": detail})
    del j["log"][:-400]


def _stage_done(wid: str, name: str, t0: float) -> None:
    j = _CODEX_JOBS.get(wid)
    if j is not None:
        j.setdefault("stage_times", {})[name] = round(time.time() - t0, 1)


def _ask(cfg: Tuple[str, str, str], system: str, user: str,
         want: str, max_tokens: int) -> Any:
    from backend.api.concept import _call_llm
    provider, key, model = cfg
    txt = _call_llm(provider, key, model, system, user, max_tokens)
    return (sw._json_arr if want == "array" else sw._json_obj)(txt)


def _merge_entries(cx: dict, fresh: List[dict], scope_ids: List[str],
                   run_id: str, *, did_world: bool) -> dict:
    """Replace the GENERATED entries in scope with `fresh`; keep everything he
    wrote or pinned; carry a kept entry's id forward when the model regenerates
    the same name so the UI does not see it flicker in and out.

    ⚠⚠ **SCOPE MUST BE STATED, NEVER INFERRED FROM AN EMPTY LIST.** The first
    version read "no stories in scope" as "everything is in scope", so:

      · a run where only a CHARACTER changed wiped the entire generated codex
      · a story-scoped run (which is what the Story tab's 📚 button sends, and
        which never re-reads the world) deleted every world-level entry —
        and, because the world hash was still stored, they never came back

    Both were silent, and the ✅ badge said "up to date" afterwards. So
    `did_world` is an explicit parameter: a world-level entry (`story_ids ==
    []`) is only replaced when this run actually re-read the world sheet, and a
    story entry is only replaced when its story was one of the ones read."""
    old = cx.get("entries") or []
    kept = [e for e in old if _keep(e)]
    keptnames = {(e.get("kind"), _norm_name(e.get("name"))) for e in kept}

    def in_scope(e: dict) -> bool:
        ids = e.get("story_ids") or []
        if not ids:                      # a world-level entry
            return did_world
        return bool(scope_ids) and any(i in scope_ids for i in ids)

    # generated entries OUTSIDE this run's scope survive untouched
    carried = [e for e in old if not _keep(e) and not in_scope(e)]
    prev_by_name = {(e.get("kind"), _norm_name(e.get("name"))): e for e in old}

    added, updated = 0, 0
    out: List[dict] = []
    seen = set()
    for e in fresh:
        k = (e.get("kind"), _norm_name(e.get("name")))
        if k in keptnames:
            # ⭐ he owns this name — the model does not get to overwrite it.
            continue
        if k in seen:
            continue
        seen.add(k)
        prev = prev_by_name.get(k)
        if prev:
            e["id"] = prev["id"]
            e["created_at"] = prev.get("created_at") or e["created_at"]
            if (prev.get("body") or "") != (e.get("body") or ""):
                updated += 1
        else:
            added += 1
        out.append(e)
    # ⚠⚠ THE CAP TRUNCATES THE GENERATED ENTRIES ONLY. Sorting `kept` in with
    # everything else and then slicing at the cap would delete a hand-written
    # entry whose name happens to sort late — which is exactly the promise this
    # module is built on, broken by an off-by-one-list.
    room = max(0, _ENTRY_CAP - len(kept) - len(carried))
    merged = kept + carried + out[:room]
    merged.sort(key=lambda e: (e.get("kind") or "", _norm_name(e.get("name"))))
    cx["entries"] = merged
    return {"added": added, "updated": updated, "kept": len(kept),
            "carried": len(carried), "total": len(cx["entries"]),
            "dropped_over_cap": max(0, len(out) - room), "run_id": run_id}


def _merge_char(prev: dict, fresh: dict, run_id: str) -> dict:
    """Same contract, one character. Manual/pinned EVENTS survive; the state
    line and relationships are regenerated unless pinned."""
    prev = prev or {}
    kept_ev = [e for e in (prev.get("events") or []) if _keep(e)]
    keptnames = {_norm_name(e.get("title")) for e in kept_ev}
    new_ev = []
    for e in fresh.get("events") or []:
        if _norm_name(e.get("title")) in keptnames:
            continue
        new_ev.append(e)
    kept_rel = [r for r in (prev.get("relationships") or []) if _keep(r)]
    keptrel = {_norm_name(r.get("who")) for r in kept_rel}
    new_rel = [r for r in (fresh.get("relationships") or [])
               if _norm_name(r.get("who")) not in keptrel]
    state_pinned = bool(prev.get("state_pinned"))
    return {
        "summary": prev.get("summary") if state_pinned else fresh.get("summary", ""),
        "state": prev.get("state") if state_pinned else fresh.get("state", ""),
        "state_pinned": state_pinned,
        "events": kept_ev + new_ev,
        "relationships": kept_rel + new_rel,
        "updated_at": sw._now(), "run_id": run_id,
    }


def _run_recalc(wid: str, cfg: Tuple[str, str, str], body: CodexRecalcIn,
                host_note: str) -> None:
    j = _CODEX_JOBS[wid]
    run_id = uuid4().hex[:8]
    t_start = time.time()
    try:
        j["status"] = "running"
        w = sw._load(wid)
        cx = _codex(w)
        hashes = dict(cx.get("hashes") or {})
        stories = [s for s in (w.get("stories") or [])
                   if not body.story_id or s.get("id") == body.story_id]
        cast = [m for m in (w.get("cast") or [])
                if not body.story_id
                or not m.get("story_ids")
                or body.story_id in (m.get("story_ids") or [])]

        # ── stage: scan ────────────────────────────────────────────────────
        t0 = time.time()
        _tick(wid, "hashing the canon to see what actually changed", stage="scan")
        # every story's digest once — the character hashes are composed from
        # these rather than re-serialising the narrations per cast member.
        story_h = {s.get("id"): _h(_story_canon(w, s))
                   for s in (w.get("stories") or [])}
        todo_s, skip_s = [], []
        for st in stories:
            can = _story_canon(w, st)
            hh = story_h.get(st["id"]) or _h(can)
            if not body.force and hh == hashes.get(f"story:{st['id']}"):
                skip_s.append(st.get("title") or st["id"])
            else:
                todo_s.append((st, can, hh))
        todo_c, skip_c = [], []
        if body.do_characters:
            for m in cast:
                can = _char_canon(w, m)          # the LLM still reads it whole
                hh = _char_hash(w, m, story_h)   # …but the HASH is the cheap one
                if not body.force and hh == hashes.get(f"char:{m['id']}"):
                    skip_c.append(m.get("name") or m["id"])
                else:
                    todo_c.append((m, can, hh))
        wcan = _world_canon(w)
        whash = _h(wcan)
        do_world = bool(body.do_world and not body.story_id
                        and (body.force or whash != hashes.get("world:")))
        _stage_done(wid, "scan", t0)
        total = len(todo_s) + len(todo_c) + (1 if do_world else 0)
        j["total"] = total
        j["done"] = 0
        j["skipped"] = {"stories": skip_s, "characters": skip_c}
        _tick(wid, f"{total} item(s) to read · {len(skip_s)} story(ies) and "
                   f"{len(skip_c)} character(s) unchanged, skipped")
        if not total:
            j.update(status="done", detail="nothing has changed since the last "
                                           "recalc — no LLM calls made",
                     elapsed_s=round(time.time() - t_start, 1))
            _tick(wid, "nothing has changed since the last recalc")
            return

        fresh: List[dict] = []
        scope_ids = [st["id"] for st, _, _ in todo_s]

        # ── stage: world sheet ─────────────────────────────────────────────
        if do_world:
            t0 = time.time()
            _tick(wid, "reading the world sheet and the location sheets",
                  stage="world")
            try:
                got = _ask(cfg, _CODEX_SYSTEM,
                           wcan + "\n\n" + _entry_ask("this world's own sheet "
                                                      "and location sheets"),
                           "array", 6000)
                for row in got:
                    if isinstance(row, dict):
                        e = _entry({**row, "story_ids": []}, run_id=run_id)
                        if e and e["sources"]:
                            fresh.append(e)
            except Exception as ex:                              # noqa: BLE001
                _tick(wid, f"world sheet failed: {ex}")
            j["done"] = int(j.get("done") or 0) + 1
            _stage_done(wid, "world", t0)

        # ── stage: stories ─────────────────────────────────────────────────
        if todo_s:
            t0 = time.time()
            for st, can, hh in todo_s:
                if j.get("cancel"):
                    raise RuntimeError("cancelled")
                _tick(wid, f"reading story “{st.get('title')}”", stage="stories")
                j["current"] = st.get("title") or ""
                try:
                    got = _ask(cfg, _CODEX_SYSTEM,
                               can + "\n\n" + _entry_ask(
                                   f"the story “{st.get('title')}”"),
                               "array", 7000)
                    n = 0
                    for row in got:
                        if isinstance(row, dict):
                            e = _entry({**row, "story_ids": [st["id"]]},
                                       run_id=run_id)
                            if e and e["sources"]:
                                fresh.append(e)
                                n += 1
                    hashes[f"story:{st['id']}"] = hh
                    _tick(wid, f"“{st.get('title')}” → {n} entr"
                               f"{'y' if n == 1 else 'ies'}")
                except Exception as ex:                          # noqa: BLE001
                    _tick(wid, f"story “{st.get('title')}” failed: {ex}")
                j["done"] = int(j.get("done") or 0) + 1
            _stage_done(wid, "stories", t0)

        # ── stage: characters ──────────────────────────────────────────────
        char_out: Dict[str, dict] = {}
        if todo_c:
            t0 = time.time()
            for m, can, hh in todo_c:
                if j.get("cancel"):
                    raise RuntimeError("cancelled")
                _tick(wid, f"reading what happened to {m.get('name')}",
                      stage="characters")
                j["current"] = m.get("name") or ""
                try:
                    got = _ask(cfg, _CODEX_SYSTEM,
                               can + "\n\n" + _char_ask(m.get("name") or ""),
                               "object", 6000)
                    char_out[m["id"]] = _clean_char(got, run_id)
                    hashes[f"char:{m['id']}"] = hh
                    _tick(wid, f"{m.get('name')} → "
                               f"{len(char_out[m['id']].get('events') or [])} event(s)")
                except Exception as ex:                          # noqa: BLE001
                    _tick(wid, f"{m.get('name')} failed: {ex}")
                j["done"] = int(j.get("done") or 0) + 1
            _stage_done(wid, "characters", t0)

        # ── stage: merge + save ────────────────────────────────────────────
        t0 = time.time()
        _tick(wid, "merging — anything you wrote or pinned is kept", stage="merge")
        drifted = []
        with sw._LOCK:
            w2 = sw._load(wid)              # re-read: the LLM calls took minutes
            cx2 = _codex(w2)
            stats = _merge_entries(cx2, fresh, scope_ids, run_id,
                                   did_world=do_world)
            chars = dict(cx2.get("characters") or {})
            for cid, val in char_out.items():
                chars[cid] = _merge_char(chars.get(cid) or {}, val, run_id)
            cx2["characters"] = chars
            # ⚠⚠ ONLY STORE A HASH THAT IS STILL TRUE. `hashes` holds what the
            # canon looked like at SCAN time, minutes ago. Edit a story while
            # the recalc runs and storing it blind marks the new text as
            # "already read" — the codex misses the edit AND the ✅ badge says
            # it is up to date, so nothing ever tells you. Re-hash against the
            # world we just re-read and leave anything that moved STALE.
            h2 = dict(cx2.get("hashes") or {})
            story_h2 = {s.get("id"): _h(_story_canon(w2, s))
                        for s in (w2.get("stories") or [])}
            for st_ in (w2.get("stories") or []):
                k = f"story:{st_.get('id')}"
                if k in hashes:
                    if story_h2.get(st_.get("id")) == hashes[k]:
                        h2[k] = hashes[k]
                    else:
                        drifted.append(st_.get("title") or k)
            for m_ in (w2.get("cast") or []):
                k = f"char:{m_.get('id')}"
                if k in hashes:
                    if _char_hash(w2, m_, story_h2) == hashes[k]:
                        h2[k] = hashes[k]
                    else:
                        drifted.append(m_.get("name") or k)
            if do_world and _h(_world_canon(w2)) == whash:
                h2["world:"] = whash
            elif do_world:
                drifted.append("the world sheet")
            cx2["hashes"] = h2
            cx2["generated_at"] = sw._now()
            cx2["updated_at"] = sw._now()
            run = {"id": run_id, "at": sw._now(),
                   "seconds": round(time.time() - t_start, 1),
                   "provider": cfg[0], "model": cfg[2], "host": host_note,
                   "stories_read": len(todo_s), "stories_skipped": len(skip_s),
                   "characters_read": len(todo_c),
                   "characters_skipped": len(skip_c),
                   "world_read": do_world, "forced": bool(body.force),
                   "drifted": drifted,
                   "stage_times": dict(j.get("stage_times") or {}), **stats}
            cx2["runs"] = ([run] + list(cx2.get("runs") or []))[:_RUN_CAP]
            sw._save(w2)
        _stage_done(wid, "merge", t0)
        j.update(status="done", run=run, elapsed_s=round(time.time() - t_start, 1))
        if drifted:
            _tick(wid, "⚠ edited while this ran, still marked as changed: "
                       + ", ".join(drifted[:6]))
        _tick(wid, f"done — {stats['total']} entries "
                   f"({stats['added']} new, {stats['updated']} updated, "
                   f"{stats['kept']} of yours kept)", stage="done")
    except Exception as ex:                                      # noqa: BLE001
        cancelled = "cancel" in str(ex).lower()
        j.update(status="cancelled" if cancelled else "error",
                 error=str(ex), elapsed_s=round(time.time() - t_start, 1))
        _tick(wid, ("cancelled" if cancelled else f"failed: {ex}"),
              stage="error")
        if not cancelled:
            logger.exception("codex recalc failed for world %s", wid)


def _entry_ask(what: str) -> str:
    return (
        f"From {what} ABOVE — and from nothing else — write the codex entries.\n"
        "Cover: factions and groups, world rules, places, items that matter, "
        "terms this world uses, concepts and customs, and the events that "
        "happen in order.\n"
        "Return a JSON array of objects with keys:\n"
        "  \"kind\"    one of: " + ", ".join(_KIND_KEYS) + "\n"
        "  \"name\"    the thing itself, as the material names it\n"
        "  \"body\"    2-5 sentences, ONLY what the material states\n"
        "  \"tags\"    a few short keywords\n"
        "  \"sources\" an array of short quotes or precise references from the "
        "material that support this entry\n"
        "Rules: no entry without a source. Do not restate the same thing under "
        "two names. If the material is thin on something, write a short entry "
        "rather than a padded one. 8-30 entries. Plain strings only."
    )


def _char_ask(name: str) -> str:
    return (
        f"From the material ABOVE — and from nothing else — write the codex "
        f"page for {name}.\n"
        "Return a JSON object with keys:\n"
        "  \"summary\"       2-4 sentences: who they are, as established\n"
        "  \"state\"         ONE sentence: where they stand as of the LATEST "
        "material — what they now know, have, want or have lost. This is what a "
        "sequel would start from.\n"
        "  \"events\"        an array, in story order, of what happened TO them. "
        "Each: \"title\", \"body\" (1-3 sentences), \"sources\" (references from "
        "the material).\n"
        "  \"relationships\" an array of: \"who\" (another character's name as "
        "given), \"body\" (the tie, as established), \"sources\".\n"
        "Rules: nothing without a source. Do not speculate about their feelings "
        "unless the material states them. Do not predict what happens next."
    )


def _clean_char(got: dict, run_id: str) -> dict:
    def rows(key: str, namekey: str) -> List[dict]:
        out = []
        for i, r in enumerate((got.get(key) or [])[:60]):
            if not isinstance(r, dict):
                continue
            nm = sw._flat(r.get(namekey) or "", 160)
            bd = sw._flat(r.get("body") or "", 1500)
            srcs = [sw._flat(s, 120) for s in (r.get("sources") or []) if sw._flat(s, 120)][:8]
            if not nm or not bd or not srcs:       # ⭐ canon-only, enforced here
                continue
            out.append({"id": uuid4().hex[:8], "i": i, namekey: nm, "body": bd,
                        "sources": srcs[:8], "manual": False, "pinned": False,
                        "updated_at": sw._now(), "run_id": run_id})
        return out
    return {"summary": sw._flat(got.get("summary") or "", 2000),
            "state": sw._flat(got.get("state") or "", 800),
            "events": rows("events", "title"),
            "relationships": rows("relationships", "who")}


@router.post("/worlds/{wid}/codex/recalc")
async def recalc_codex(wid: str, body: CodexRecalcIn,
                       session: AsyncSession = Depends(get_session)):
    """♻ Re-read the world and rewrite the codex from what is actually written.

    Cheap when nothing changed (the hashes answer without an LLM call) and
    scoped when you name a story. Anything you wrote or pinned is kept."""
    w = sw._load(wid)
    if body.story_id:
        sw._find(w.get("stories") or [], body.story_id, "story")

    # ⚠⚠ CLAIM THE SLOT **BEFORE** THE AWAIT. Checking the job map and then
    # awaiting `_llm_cfg` (a DB round-trip) leaves a window in which a second
    # click passes the same check: two threads, two sets of LLM calls, two
    # load-merge-save cycles, and the second `_CODEX_JOBS[wid] = {...}` orphans
    # the first thread's status object so cancel can only reach one of them.
    # This is the v1.277.0 double-submit race in a new place — claim under the
    # guard, revert on failure.
    if (_CODEX_JOBS.get(wid) or {}).get("status") in ("starting", "running"):
        raise HTTPException(409, "a codex recalc is already running for this world")
    _CODEX_JOBS[wid] = {
        "status": "starting", "stage": "scan", "detail": "resolving the brain",
        "t0": time.time(), "total": 0, "done": 0, "current": "", "cancel": False,
        "provider": "", "model": "", "host": "",
        "scope": body.story_id or "whole world",
        "forced": bool(body.force), "log": [], "stage_times": {},
    }
    try:
        # ⭐ HIS PREFERENCE: ollama on a worker unless he says otherwise — the
        # codex re-reads every story and is the most token-hungry lane here.
        pick = body.llm or sw._pick_of(w) or sw.LlmPick(provider="ollama", model="")
        try:
            cfg = await sw._llm_cfg(session, pick)
        except HTTPException:
            if (pick.provider or "") != "ollama":
                raise
            cfg = await sw._llm_cfg(session, None)      # no ollama configured
    except Exception:
        _CODEX_JOBS.pop(wid, None)                     # release the slot
        raise
    host_note = cfg[0]
    if cfg[0] == "ollama":
        try:
            urls = json.loads(cfg[1]) if cfg[1].startswith("[") else [cfg[1]]
            host_note = ", ".join(urls)
        except Exception:                                        # noqa: BLE001
            host_note = "ollama"
    _CODEX_JOBS[wid].update(provider=cfg[0], model=cfg[2], host=host_note)
    _tick(wid, f"starting on {cfg[0]}/{cfg[2]} ({host_note})")
    threading.Thread(target=_run_recalc, args=(wid, cfg, body, host_note),
                     daemon=True, name=f"codex-{wid}").start()
    return {"started": True, "provider": cfg[0], "model": cfg[2],
            "host": host_note}


@router.get("/worlds/{wid}/codex/job")
async def codex_job(wid: str):
    """Live status. ⚠ elapsed is computed against the WALL CLOCK for a running
    job — a frozen timer on a live run is the exact 'is this stuck?' question
    the timer exists to answer."""
    st = dict(_CODEX_JOBS.get(wid) or {"status": "idle"})
    if st.get("status") in ("starting", "running") and st.get("t0"):
        st["elapsed_s"] = round(time.time() - float(st["t0"]), 1)
    st.pop("t0", None)
    st.pop("cancel", None)
    return {"job": st}


@router.post("/worlds/{wid}/codex/cancel")
async def cancel_codex(wid: str):
    j = _CODEX_JOBS.get(wid)
    if not j or j.get("status") not in ("starting", "running"):
        raise HTTPException(409, "no codex recalc is running")
    j["cancel"] = True
    _tick(wid, "cancel requested — stopping after the current item")
    return {"cancelling": True}


# ══════════════════════════════════════════════════════════════════════════
# ✍ hand-written and pinned entries
# ══════════════════════════════════════════════════════════════════════════
class CodexEntryIn(BaseModel):
    id: str = ""
    kind: str = "concept"
    name: str = ""
    body: str = ""
    tags: List[str] = []
    sources: List[str] = []
    story_ids: List[str] = []


@router.post("/worlds/{wid}/codex/entry")
async def upsert_entry(wid: str, body: CodexEntryIn):
    """Write or edit an entry by hand. A hand-written entry is `manual` and no
    recalc will ever touch it."""
    if not (body.name or "").strip() or not (body.body or "").strip():
        raise HTTPException(400, "a codex entry needs a name and a body")
    with sw._LOCK:
        w = sw._load(wid)
        cx = _codex(w)
        ents = cx["entries"]
        # ⚠ exclude_unset: `story_ids` defaults to [] on the model and the edit
        # modal never sends it, so a plain `model_dump()` would wipe a generated
        # entry's story scoping on every hand-edit and promote it to world-level.
        row = body.model_dump(exclude_unset=True)
        # a hand-written entry cites the writer unless he cited something
        row["sources"] = row.get("sources") or ["written by hand"]
        if body.id:
            cur = next((e for e in ents if e.get("id") == body.id), None)
            if not cur:
                raise HTTPException(404, f"codex entry {body.id!r} not found")
            new = _entry({**cur, **row}, manual=True)
            if not new:
                raise HTTPException(400, "a codex entry needs a name and a body")
            new["id"] = cur["id"]
            ents[ents.index(cur)] = new
        else:
            new = _entry(row, manual=True)
            # ⚠⚠ `_entry` returns None when `_flat` blanks the name or body —
            # and `_flat` maps "unknown"/"none"/"n/a"/"nothing"/"-" to "". So an
            # entry literally named **Unknown** passed the check above, appended
            # `None`, and every later read of the codex raised on
            # `None.get(...)`. A world file poisoned past repair by one typo.
            if not new:
                raise HTTPException(
                    400, "a codex entry needs a real name and body — "
                         "'unknown', 'none', 'n/a', 'nothing' and '-' are "
                         "treated as empty")
            if len(ents) >= _ENTRY_CAP:
                raise HTTPException(409, f"the codex caps at {_ENTRY_CAP} entries")
            ents.append(new)
        cx["updated_at"] = sw._now()
        sw._save(w)
    return new


@router.post("/worlds/{wid}/codex/entry/{eid}/pin")
async def pin_entry(wid: str, eid: str, pinned: bool = True):
    """📌 Keep a generated entry. Pinned entries survive every recalc."""
    with sw._LOCK:
        w = sw._load(wid)
        cx = _codex(w)
        e = next((x for x in cx["entries"] if x.get("id") == eid), None)
        if not e:
            raise HTTPException(404, f"codex entry {eid!r} not found")
        e["pinned"] = bool(pinned)
        e["updated_at"] = sw._now()
        cx["updated_at"] = sw._now()
        sw._save(w)
    return e


@router.post("/worlds/{wid}/codex/entry/{eid}/delete")
async def delete_entry(wid: str, eid: str):
    with sw._LOCK:
        w = sw._load(wid)
        cx = _codex(w)
        n = len(cx["entries"])
        cx["entries"] = [x for x in cx["entries"] if x.get("id") != eid]
        if len(cx["entries"]) == n:
            raise HTTPException(404, f"codex entry {eid!r} not found")
        cx["updated_at"] = sw._now()
        sw._save(w)
    return {"deleted": eid}


# ── a character's page ──────────────────────────────────────────────────────
class CharEventIn(BaseModel):
    id: str = ""
    title: str = ""
    body: str = ""
    sources: List[str] = []


@router.get("/worlds/{wid}/codex/character/{cid}")
async def get_character_codex(wid: str, cid: str):
    w = sw._load(wid)
    m = sw._find(w.get("cast") or [], cid, "cast member")
    cx = _codex(w)
    e = (cx.get("characters") or {}).get(cid) or {}
    fresh = _h(_char_canon(w, m)) == (cx.get("hashes") or {}).get(f"char:{cid}")
    return {"character": {"id": m.get("id"), "name": m.get("name"),
                          "role": m.get("role") or ""},
            "codex": e, "stale": not fresh}


@router.post("/worlds/{wid}/codex/character/{cid}/event")
async def upsert_character_event(wid: str, cid: str, body: CharEventIn):
    """Add or edit an event by hand — `manual`, so a recalc keeps it."""
    if not (body.title or "").strip() or not (body.body or "").strip():
        raise HTTPException(400, "an event needs a title and a body")
    with sw._LOCK:
        w = sw._load(wid)
        sw._find(w.get("cast") or [], cid, "cast member")
        cx = _codex(w)
        page = dict((cx.get("characters") or {}).get(cid) or {})
        evs = list(page.get("events") or [])
        row = {"title": sw._flat(body.title, 160), "body": sw._flat(body.body, 1500),
               "sources": [sw._flat(s, 120) for s in (body.sources or [])]
                          or ["written by hand"],
               "manual": True, "pinned": True, "updated_at": sw._now()}
        if body.id:
            cur = next((e for e in evs if e.get("id") == body.id), None)
            if not cur:
                raise HTTPException(404, f"event {body.id!r} not found")
            cur.update(row)
            out = cur
        else:
            out = {"id": uuid4().hex[:8], "i": len(evs), **row}
            evs.append(out)
        page["events"] = evs
        page["updated_at"] = sw._now()
        chars = dict(cx.get("characters") or {})
        chars[cid] = page
        cx["characters"] = chars
        cx["updated_at"] = sw._now()
        sw._save(w)
    return out


@router.post("/worlds/{wid}/codex/character/{cid}/event/{eid}/delete")
async def delete_character_event(wid: str, cid: str, eid: str):
    with sw._LOCK:
        w = sw._load(wid)
        cx = _codex(w)
        page = dict((cx.get("characters") or {}).get(cid) or {})
        evs = [e for e in (page.get("events") or []) if e.get("id") != eid]
        page["events"] = evs
        chars = dict(cx.get("characters") or {})
        chars[cid] = page
        cx["characters"] = chars
        cx["updated_at"] = sw._now()
        sw._save(w)
    return {"deleted": eid}


@router.post("/worlds/{wid}/codex/character/{cid}/pin-state")
async def pin_character_state(wid: str, cid: str, pinned: bool = True,
                              state: str = "", summary: str = ""):
    """📌 Pin (and optionally rewrite) a character's summary + state line."""
    with sw._LOCK:
        w = sw._load(wid)
        sw._find(w.get("cast") or [], cid, "cast member")
        cx = _codex(w)
        chars = dict(cx.get("characters") or {})
        page = dict(chars.get(cid) or {})
        page["state_pinned"] = bool(pinned)
        if state.strip():
            page["state"] = sw._flat(state, 800)
        if summary.strip():
            page["summary"] = sw._flat(summary, 2000)
        page["updated_at"] = sw._now()
        chars[cid] = page
        cx["characters"] = chars
        cx["updated_at"] = sw._now()
        sw._save(w)
    return page
