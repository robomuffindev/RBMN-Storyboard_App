"""📖📚 STORY CHAPTERS + THE CODEX — v1.277.46.

Two features that arrived together because they answer the same question:
*what is actually true in this world, and where do I tell it?*

────────────────────────────────────────────────────────────────────────────
📖 CHAPTERS  (his brief, 2026-08-18)

    "The Story should have all the beats already created by the creator or
     LLM. The chapter should work off the story beats and tell those parts in
     more detail. A chapter can be a single narration and that makes it easier
     to keep the full media generations like video smaller per chapter rather
     than trying to jam everything in at once."

So the ladder is now THREE deep, and each rung has exactly one job:

    STORY   → prose + **arcs**            the spine. short on purpose.
    CHAPTER → one arc, told at length     ⭐ **a chapter is one video project**
    BEAT    → a slice of a chapter        the project's TIMELINE chapters

⚠⚠ **There are now FOUR things in this codebase called "chapter".** Before you
touch anything here, know which one you are holding:

  1. **`Chapter` rows** (`backend/services/chapters/`) — a PROJECT's timeline
     segments, timed against detected audio sections. Database.
  2. **Story `arcs`** (`story["arcs"]`) — the world-side spine. No durations.
  3. **Story `chapters`** — THIS module. World-side, authored or LLM-written,
     each owning a FULL narration. Upstream of any project.
  4. A chapter's **`beats`** — which become (1) when a project pulls.

**His call (2026-08-18): MANY CHAPTERS PER ARC.** A chapter carries `arc_id`
but nothing enforces a count, so a long arc can be told over three chapters and
a short one over one. Arcs stay the spine; chapters are the unit you render.

⭐ **A beat is ARC-SHAPED on purpose.** `_clean_arcs` normalises both, so
`create_chapters_from_arcs(session, pid, chapter["beats"], dur)` needs no new
code and `story_context.arc_context` keeps matching on `arc_id`. One shape,
two rungs of the ladder — do not let a beat grow a field an arc does not have
without adding it to `_ARC_FIELDS` too.

⭐ **The LLM writes ONE CHAPTER AT A TIME** (his call). Smaller context, better
prose, and he can edit between chapters instead of after a whole book. The
outline route is the only one that sees the story whole, and it writes titles
and summaries only — never narration.

────────────────────────────────────────────────────────────────────────────
📚 THE CODEX  (his brief, same day)

    "a Codex tab to the world that always gets updated when things change so we
     can almost have a cheat sheet for the world. Maybe also a codex page for
     characters as well so we can keep track of events and major things that
     have happened to them for situations where we want to create a continuous
     series … Basically a lot of tracking and storing whats going on and auto
     updating if things change."

**His call: CANON ONLY.** Every entry is derived from something WRITTEN — world
fields, a story, a chapter, a cast sheet, a location — and carries `sources`
naming them. If it is not written down, the codex does not claim it. That is
what makes it safe to build a continuing series on: the codex can never quietly
contradict a story you write later, because it never says anything a story did
not already say.

⭐⭐ **A RECALC MUST NEVER EAT WHAT HE TYPED.** Entries carry `manual` (you wrote
it) and `pinned` (the LLM wrote it, you kept it). Both survive every recalc,
exactly the way `source="story"` survives a project's chapter rebuild — and for
the same reason, the lesson is written down in FOUR places there because it was
missed in three of them. Here there is ONE predicate, `_keep()`, and every
delete path goes through it.

⭐ **Recalc is INCREMENTAL.** Each story and each character gets a canon HASH
over the text that feeds it; an unchanged hash is skipped and says so. Without
this, a re-calc on a ten-story world is forty LLM calls to rewrite forty
identical entries.

⭐ **Ollama by default** (his preference — "it would take a lot of tokens").
`CodexRecalcIn.llm` still overrides per run.

🧭 **Live status is the standing rule.** The recalc runs on a thread with a
job record carrying stage · what · WHERE (provider/model/host) · elapsed ·
per-stage durations · a change log, and every finished run is kept on the world
as benchmarking data (`codex["runs"]`, newest first, capped).

────────────────────────────────────────────────────────────────────────────
⚠ ROUTE ORDER IS LOAD-BEARING. `/chapters/generate` and `/chapters/{cid}` are
the same SHAPE — the literal must be declared first or it is swallowed as a
chapter id. This module's parent learned that with seven failures from one
cause; the parameterised chapter update is declared LAST in this file.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api import storyworld as sw
from backend.database.database import get_session

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/storyworld", tags=["storyworld"])

# ⚠ IMPORT-TIME anchored, like every other path in this library (the
# cfg.project_dir DB-override gotcha — derive from the constant, not the row).
_CH_NARR_DIR = sw._ROOT / "chapter_audio"

_CHAPTER_CAP = 60           # a story is not a shelf
_BEAT_CAP = 24              # _clean_arcs' own cap; kept explicit for the docs
_WPM = 150.0                # the narration pace everything here estimates with

#: ⭐ HIS CALL (2026-08-18, after seeing the first output): a chapter is a FULL
#: TELLING, not a summary. The first version defaulted to 3 minutes (~450 words)
#: and he said *"it should act as more of a full telling of the chapter in
#: enough detail to create a compelling narration that will work well for
#: video."* 10 minutes ≈ 1500 words is the default now; `target_minutes` on the
#: chapter still overrides it.
_DEFAULT_MINUTES = 10.0
#: A beat below this many words is not worth its own call — the model spends
#: them on throat-clearing. Beats are merged into the neighbouring budget.
_MIN_BEAT_WORDS = 120
#: cid → live narration-writing status. In-memory: this is minutes, not hours.
_NARR_JOBS: Dict[str, dict] = {}


# ══════════════════════════════════════════════════════════════════════════
# ✍ prose — the two helpers that decide whether a TTS gets paragraphs
# ══════════════════════════════════════════════════════════════════════════
def _prose(v: Any, cap: int = 200000) -> str:
    """Prose out of whatever shape the model chose — **PARAGRAPHS PRESERVED**.

    ⚠⚠ THIS EXISTS BECAUSE `sw._flat` DESTROYS THEM. `_flat` joins a list with
    `", "`, so a model that helpfully returned

        "narration": ["First paragraph…", "Second paragraph…"]

    came back as one comma-welded block — which is exactly the *"single block of
    text"* he reported. `_flat` is right for FIELDS (a mood, a title, a summary
    that must be one line) and wrong for PROSE. Never use it on narration."""
    if v is None:
        return ""
    if isinstance(v, (list, tuple)):
        return "\n\n".join(_prose(x, cap) for x in v if x is not None)[:cap]
    if isinstance(v, dict):
        # some models answer {"paragraph_1": "...", "paragraph_2": "..."}
        return "\n\n".join(_prose(x, cap) for x in v.values())[:cap]
    return str(v).strip()[:cap]


_HEADER_LINE = re.compile(r"^\s{0,3}#{1,6}\s.*$", re.MULTILINE)
#: split after . ! ? … and any closing quote/bracket that follows one
_SENTENCE_END = re.compile(r'(?<=[.!?…])["\'”’\)\]]*\s+')


def _paragraphize(text: str, per_para: int = 4) -> str:
    """Guarantee real, blank-line-separated paragraphs. **Paragraphs matter to
    a TTS** (his words) — they are where a reader breathes, and this app's
    pause-tagger writes its `[pause]` tags at paragraph boundaries, so a single
    blob is not just ugly, it is a narration with no breaths in it.

    Three repairs, in order, each only if needed:
      1. drop any markdown heading the model sneaked in (his call: no headers)
      2. single newlines → paragraph breaks (models often mean them that way)
      3. still one blob and long enough to matter → split on SENTENCE ends into
         groups of `per_para`

    ⭐ Step 3 is a fallback, not the plan. The prompt asks for paragraphs and
    the per-beat structure produces them naturally; this is here so that a model
    having a bad day cannot ship an unbreathable wall of text."""
    t = _HEADER_LINE.sub("", text or "")
    t = t.replace("\r\n", "\n").replace("\r", "\n")
    t = re.sub(r"[ \t]+\n", "\n", t)
    if "\n\n" not in t and "\n" in t:
        t = t.replace("\n", "\n\n")
    if "\n\n" not in t and len(t.split()) > 90:
        sents = [s.strip() for s in _SENTENCE_END.split(t) if s.strip()]
        if len(sents) > per_para:
            t = "\n\n".join(" ".join(sents[i:i + per_para])
                            for i in range(0, len(sents), per_para))
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


# ══════════════════════════════════════════════════════════════════════════
# 📖 chapters — shape and helpers
# ══════════════════════════════════════════════════════════════════════════
_CHAPTER_FIELDS: List[Dict[str, str]] = [
    {"key": "title",   "label": "Title",   "hint": "what this chapter is called"},
    {"key": "summary", "label": "Summary", "hint": "what happens in it, 2-4 sentences"},
    {"key": "mood",    "label": "Mood",    "hint": "how it should FEEL — drives the backing bed"},
    {"key": "notes",   "label": "Notes",   "hint": "anything the writer or the LLM should know"},
]


def _story_of(w: dict, sid: str) -> dict:
    return sw._find(w.get("stories") or [], sid, "story")


def _chapters(st: dict) -> List[dict]:
    return st.get("chapters") or []


def _find_chapter(st: dict, cid: str) -> dict:
    return sw._find(_chapters(st), cid, "chapter")


def _arc_of(st: dict, arc_id: str) -> Optional[dict]:
    for a in st.get("arcs") or []:
        if a.get("id") == arc_id:
            return a
    return None


def _words(text: str) -> int:
    return len([x for x in re.split(r"\s+", text or "") if x])


def _renumber(chapters: List[dict]) -> List[dict]:
    for i, c in enumerate(chapters):
        c["i"] = i
    return chapters


def _keep_beat_ids(old: List[dict], new: Any) -> List[dict]:
    """Re-number beats but KEEP their ids positionally where we can.

    ⚠⚠ A project that has already pulled stores each beat's id in its
    `chapter_metadata["arc_id"]`. `_clean_arcs` mints a fresh id whenever one is
    absent, so rewriting a chapter's narration would hand every project chapter
    a DANGLING arc_id — `arc_context` then falls through to matching by name and
    silently gives the flow LLM the wrong beat, or none. Positional carry-over
    keeps the link alive across a rewrite; a beat count that changes genuinely
    is a new structure, and the extras get new ids."""
    fresh = sw._clean_arcs(new)[:_BEAT_CAP]
    for i, b in enumerate(fresh):
        if i < len(old) and (old[i].get("id") or ""):
            b["id"] = old[i]["id"]
    return fresh


def _blank_chapter(i: int) -> dict:
    return {
        "id": uuid4().hex[:8], "i": i, "title": f"Chapter {i + 1}",
        "arc_id": "", "summary": "", "mood": "", "notes": "",
        "characters": [], "locations": [],
        "beats": [],
        "narration": "", "narration_words": 0, "narration_files": {},
        "target_minutes": 0.0,
        "created_at": sw._now(), "updated_at": sw._now(),
    }


def _apply_chapter(c: dict, src: Dict[str, Any], *, merge: bool = True) -> dict:
    """Write the writable fields of a chapter. MERGE semantics like everything
    else in this library — an absent key means 'leave it', not 'clear it'."""
    if "title" in src and str(src.get("title") or "").strip():
        c["title"] = sw._flat(src["title"], 160)
    for k, cap in (("summary", 4000), ("mood", 300), ("notes", 2000)):
        if k in src and (src[k] is not None or not merge):
            c[k] = sw._flat(src.get(k) or "", cap)
    if src.get("arc_id") is not None:
        c["arc_id"] = str(src.get("arc_id") or "")
    for k, cap, n in (("characters", 80, 24), ("locations", 80, 16)):
        if src.get(k) is not None:
            c[k] = [sw._flat(x, cap) for x in (src.get(k) or []) if sw._flat(x, cap)][:n]
    if src.get("beats") is not None:
        # ⭐ a beat is arc-shaped — one cleaner, so the project's chapter
        # builder can take a beat list unchanged.
        c["beats"] = sw._clean_arcs(src.get("beats"))[:_BEAT_CAP]
    if src.get("narration") is not None:
        # ⚠ _prose, NEVER sw._flat — _flat comma-welds a list of paragraphs into
        # one block. Paragraphs are what a TTS breathes on.
        c["narration"] = _paragraphize(_prose(src.get("narration") or ""))
        c["narration_words"] = _words(c["narration"])
    if src.get("target_minutes") is not None:
        try:
            c["target_minutes"] = max(0.0, min(float(src["target_minutes"]), 180.0))
        except (TypeError, ValueError):
            pass
    c["updated_at"] = sw._now()
    return c


def chapter_row(c: dict) -> dict:
    """⭐ THE PICKER SHAPE — titles and counts, never prose.

    ONE definition, used by `?brief=1` here AND by `GET /api/projects/{id}/
    story-link`, so the chapter dropdown cannot get two different objects
    depending on which screen filled it. Everything a picker needs and nothing
    that costs bandwidth: a chapter's narration is tens of thousands of words
    and a story can hold sixty of them."""
    return {
        "id": c.get("id"), "i": int(c.get("i") or 0),
        "title": c.get("title") or "", "summary": (c.get("summary") or "")[:200],
        "words": int(c.get("narration_words") or 0),
        "beats": len(c.get("beats") or []),
        "has_narration": bool((c.get("narration") or "").strip()),
        "has_audio": bool((c.get("narration_files") or {}).get("audio")),
    }


def _chapter_view(c: dict, st: Optional[dict] = None, *,
                  brief: bool = False) -> dict:
    """A chapter plus the numbers the UI would otherwise recompute wrongly.

    ⚠ `est_minutes` is ARITHMETIC (words ÷ 150). `recorded_seconds` is a
    MEASUREMENT (ffprobe, at upload). The narration lane already had these two
    confused once — they are named differently here on purpose.

    ⚠ `brief` drops the prose. A chapter's narration is tens of thousands of
    words and there are up to 60 of them; a picker that only needs titles must
    not download the book to draw a dropdown. `spoken` is computed on demand
    rather than always — it is a regex over the whole text, per chapter."""
    files = c.get("narration_files") or {}
    words = int(c.get("narration_words") or 0) or _words(c.get("narration") or "")
    out = dict(c)
    if brief:
        out.pop("narration", None)
        out.pop("notes", None)
    out["narration_words"] = words
    out["est_minutes"] = round(words / _WPM, 1) if words else 0.0
    out["recorded_seconds"] = float((files.get("audio") or {}).get("seconds") or 0.0)
    out["has_narration"] = bool((c.get("narration") or "").strip())
    out["beat_count"] = len(c.get("beats") or [])
    if st is not None:
        a = _arc_of(st, c.get("arc_id") or "")
        out["arc_title"] = (a or {}).get("title") or ""
    return out


def _chapter_ctx(w: dict, st: dict, c: dict) -> str:
    """Everything the LLM needs to write THIS chapter and nothing else.

    Deliberately includes the neighbouring chapters' summaries: the commonest
    failure of one-chapter-at-a-time writing is a chapter that re-introduces a
    character the previous chapter already introduced, or that ends the story
    twice."""
    arc = _arc_of(st, c.get("arc_id") or "")
    chs = _chapters(st)
    idx = next((i for i, x in enumerate(chs) if x.get("id") == c.get("id")), 0)
    lines = [sw._ctx_world(w), "", sw._ctx_story(st)]
    locs = sw._ctx_locations(w, st.get("id") or "")
    if locs:
        lines += ["", locs]
    cast = [m for m in (w.get("cast") or [])
            if not m.get("story_ids") or st.get("id") in (m.get("story_ids") or [])]
    if cast:
        lines += ["", "CAST: " + ", ".join(
            f"{m.get('name')} ({m.get('role') or 'character'})" for m in cast)]
    if arc:
        lines += ["", f"THIS CHAPTER TELLS THE ARC: {arc.get('title')}",
                  f"  what happens: {arc.get('summary') or ''}",
                  f"  mood: {arc.get('mood') or ''}"]
    if chs:
        lines += ["", "THE CHAPTER LIST, in order (yours is marked ►):"]
        for i, x in enumerate(chs):
            mark = "►" if i == idx else " "
            lines.append(f" {mark} {i + 1}. {x.get('title')}: "
                         f"{(x.get('summary') or '')[:240]}")
        lines += ["",
                  "Write ONLY the marked chapter. Do not tell the chapters "
                  "before it again, and do not tell the chapters after it "
                  "early. Assume the listener has heard everything above it "
                  "and nothing below it."]
    if (c.get("notes") or "").strip():
        lines += ["", f"WRITER'S NOTES FOR THIS CHAPTER: {c['notes']}"]
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════════
# 📖 chapters — CRUD
# ══════════════════════════════════════════════════════════════════════════
class ChapterIn(BaseModel):
    title: str = ""
    arc_id: Optional[str] = None
    summary: Optional[str] = None
    mood: Optional[str] = None
    notes: Optional[str] = None
    characters: Optional[List[str]] = None
    locations: Optional[List[str]] = None
    beats: Optional[List[Dict[str, Any]]] = None
    narration: Optional[str] = None
    target_minutes: Optional[float] = None


@router.get("/worlds/{wid}/stories/{sid}/chapters")
async def list_chapters(wid: str, sid: str, brief: bool = False):
    """The chapter list. `?brief=1` returns the shared PICKER shape (titles and
    counts only) — use it for dropdowns, never the full one."""
    w = sw._load(wid)
    st = _story_of(w, sid)
    chs = _chapters(st)
    return {
        "chapters": [chapter_row(c) if brief else _chapter_view(c, st)
                     for c in chs],
        "arcs": st.get("arcs") or [],
        "chapter_fields": _CHAPTER_FIELDS,
        "totals": {
            "chapters": len(chs),
            "written": sum(1 for c in chs if (c.get("narration") or "").strip()),
            "words": sum(int(c.get("narration_words") or 0) for c in chs),
            "est_minutes": round(
                sum(int(c.get("narration_words") or 0) for c in chs) / _WPM, 1),
            "recorded": sum(1 for c in chs
                            if (c.get("narration_files") or {}).get("audio")),
        },
        # the story-level narration stays as the whole-story / trailer version
        # (his call) — surfaced here so the UI can say which one a pull uses.
        "story_narration_words": _words(
            ((sw._story_narration(w, sid) or {}).get("body") or "")),
    }


@router.post("/worlds/{wid}/stories/{sid}/chapters")
async def add_chapter(wid: str, sid: str, body: ChapterIn):
    with sw._LOCK:
        w = sw._load(wid)
        st = _story_of(w, sid)
        chs = _chapters(st)
        if len(chs) >= _CHAPTER_CAP:
            raise HTTPException(409, f"a story caps at {_CHAPTER_CAP} chapters")
        if body.arc_id and not _arc_of(st, body.arc_id):
            raise HTTPException(404, f"arc {body.arc_id!r} not found on this story")
        c = _apply_chapter(_blank_chapter(len(chs)), body.model_dump(exclude_none=True))
        chs.append(c)
        st["chapters"] = _renumber(chs)
        st["updated_at"] = sw._now()
        sw._save(w)
    return _chapter_view(c, st)


# ⚠ LITERAL routes first — `/chapters/generate` and `/chapters/{cid}` are the
# same shape, and the parameterised one would swallow "generate" as an id.
class ChapterGenIn(BaseModel):
    count: int = 0                   # 0 = the model decides, within the cap
    per_arc: int = 0                 # 0 = the model decides how many per arc
    direction: str = ""
    overwrite: bool = False
    llm: Optional[sw.LlmPick] = None


@router.post("/worlds/{wid}/stories/{sid}/chapters/generate")
async def generate_chapters(wid: str, sid: str, body: ChapterGenIn,
                            session: AsyncSession = Depends(get_session)):
    """✨ Outline the chapters from the story's arcs — TITLES AND SUMMARIES ONLY.

    ⭐ This is the only route that sees the story whole, and it deliberately
    writes no narration: his call is one chapter at a time, so the expensive,
    context-hungry writing happens per chapter where he can edit between them.
    A story with no arcs is refused rather than guessed at — structure it first,
    because chapters that do not map to arcs break the backing-bed lane and
    the project's arc context."""
    w = sw._load(wid)
    st = _story_of(w, sid)
    arcs = st.get("arcs") or []
    if not arcs:
        raise HTTPException(409, "this story has no arcs yet — run ✨ Structure "
                                 "into arcs first; chapters are told FROM arcs")
    existing = _chapters(st)
    if existing and not body.overwrite:
        return {"chapters": [_chapter_view(c, st) for c in existing],
                "note": "this story already has chapters — tick overwrite to "
                        "replace them"}
    want = max(0, min(int(body.count or 0), _CHAPTER_CAP))
    per = max(0, min(int(body.per_arc or 0), 6))
    how = (f"Write exactly {want} chapters in total." if want else
           (f"Write exactly {per} chapters for EACH arc." if per else
            "Decide how many chapters each arc needs: a short arc may be one "
            "chapter, a long or eventful arc two or three. Between "
            f"{len(arcs)} and {min(len(arcs) * 3, _CHAPTER_CAP)} in total."))

    system = ("You are a story editor breaking a story into CHAPTERS for a "
              "narrated video series. Each chapter will become ONE video, so "
              "each must be a self-contained stretch of story with its own "
              "shape — an opening, something that changes, and a place to "
              "stop. You never invent characters, places or events that were "
              "not given to you. You never use franchise, brand or "
              "real-celebrity names. You answer with JSON only.")
    user = (
        f"{sw._ctx_world(w)}\n\n{sw._ctx_story(st)}\n\n"
        + (sw._ctx_locations(w, sid) or "") + "\n\nARCS, in order:\n"
        + "\n".join(f"{i + 1}. [{a['id']}] {a.get('title')}: "
                    f"{a.get('summary') or ''} (mood: {a.get('mood') or '—'})"
                    for i, a in enumerate(arcs))
        + (f"\n\nDIRECTION FROM THE WRITER: {body.direction.strip()}"
           if body.direction.strip() else "")
        + f"\n\n{how} Chapters must stay IN STORY ORDER and every chapter must "
          "name the arc it belongs to using the arc id in square brackets "
          "above. Return a JSON array of objects with keys: \"arc_id\" (that "
          "exact id), \"title\", \"summary\" (2-4 sentences on what happens in "
          "THIS chapter — not the whole arc), \"mood\", \"characters\" (an "
          "array of cast names present) and \"locations\" (an array of place "
          "names). Plain strings only.")

    got = await sw._ask_json(session, body.llm or sw._pick_of(w), system, user,
                             want="array", max_tokens=6000, timeout_s=900)
    valid = {a["id"] for a in arcs}
    built: List[dict] = []
    for i, row in enumerate(got[:_CHAPTER_CAP]):
        if not isinstance(row, dict):
            continue
        aid = str(row.get("arc_id") or "").strip()
        if aid not in valid:
            # the model named an arc we do not have — fall back to position
            # rather than dropping the chapter (an unmapped chapter is still
            # useful; a missing one is a hole in the story).
            aid = arcs[min(i, len(arcs) - 1)]["id"]
        c = _apply_chapter(_blank_chapter(len(built)),
                           {**row, "arc_id": aid, "narration": None})
        if not (c.get("summary") or "").strip():
            continue
        built.append(c)
    if not built:
        raise HTTPException(502, "the model returned no usable chapters")

    orphans: List[dict] = []
    with sw._LOCK:
        w = sw._load(wid)
        st2 = _story_of(w, sid)
        # ⚠⚠ RE-ASSERT THE GUARD AGAINST THE RE-READ. The `existing` check above
        # ran on a snapshot taken BEFORE an LLM call that takes minutes; a
        # chapter written during it would be destroyed without the 409 ever
        # firing. Re-read → re-check → only then replace.
        if _chapters(st2) and not body.overwrite:
            return {"chapters": [_chapter_view(c, st2) for c in _chapters(st2)],
                    "note": "chapters appeared while the model was working — "
                            "nothing was replaced; tick overwrite to redo them"}
        # ⚠ the replaced chapters own real bytes on disk. Collect them INSIDE
        # the lock and unlink AFTER the write lands (the delete-ordering rule
        # every other file path here follows).
        orphans = [m for c in _chapters(st2)
                   for m in (c.get("narration_files") or {}).values()]
        st2["chapters"] = _renumber(built)
        st2["updated_at"] = sw._now()
        sw._save(w)
    for m in orphans:
        _ch_slot_fp(wid, m).unlink(missing_ok=True)
    return {"chapters": [_chapter_view(c, st2) for c in built],
            "arcs": len(arcs), "files_removed": len(orphans)}


class ChapterOrderIn(BaseModel):
    order: List[str] = []


@router.post("/worlds/{wid}/stories/{sid}/chapters/reorder")
async def reorder_chapters(wid: str, sid: str, body: ChapterOrderIn):
    with sw._LOCK:
        w = sw._load(wid)
        st = _story_of(w, sid)
        chs = _chapters(st)
        by_id = {c["id"]: c for c in chs}
        seen, out = set(), []
        for cid in body.order:
            if cid in by_id and cid not in seen:
                out.append(by_id[cid])
                seen.add(cid)
        # anything the client forgot keeps its relative order at the end —
        # a reorder must never silently DELETE a chapter.
        out += [c for c in chs if c["id"] not in seen]
        st["chapters"] = _renumber(out)
        st["updated_at"] = sw._now()
        sw._save(w)
    return {"chapters": [_chapter_view(c, st) for c in st["chapters"]]}


# ══════════════════════════════════════════════════════════════════════════
# ✍ narration for ONE chapter — WRITTEN BEAT BY BEAT
# ══════════════════════════════════════════════════════════════════════════
#
# ⭐⭐ HIS CALL (2026-08-18, on seeing the first output): *"The chapter
# narration seems too small. It should be a story and be broken up into
# multiple categories… it should act as more of a full telling of the chapter
# in enough detail to create a compelling narration that will work well for
# video. Also we need to make sure you don't just return a single block of
# text. Paragraphs matter in TTS."*
#
# **WHY ONE CALL COULD NEVER HAVE DONE THIS.** Asking a 14B local model for
# 1500 words of quality prose in one response gets you 400 words of summary: it
# paces itself against its own sense of "an answer", not against the budget. The
# story lane already knew this — it writes ARC BY ARC. This is the same lesson
# one rung down, and it is what "broken up into multiple categories" buys:
#
#   · one call PER BEAT, each with its own word budget → the total is the SUM
#     of budgets the model actually honours, not one it talks itself out of
#   · each call carries the TAIL of the previous beat, so the prose continues
#     instead of restarting — the failure mode of every multi-call narrative
#   · a beat is a natural paragraph group, so structure falls out of the shape
#
# ⚠ N calls × ~1 min is minutes long, so this is a JOB with the standing live
# status contract (what · WHERE · how long · a log), not a request that hangs.
class ChapterNarrationIn(BaseModel):
    #: 0 = the chapter's `target_minutes`, else `_DEFAULT_MINUTES` (10 → 1500
    #: words). ⭐ 3 minutes was the old default and he called it too small.
    minutes: float = 0.0
    tone: str = ""
    person: str = "third"
    #: derive beats first when the chapter has none (it cannot be written
    #: beat-by-beat otherwise)
    with_beats: bool = True
    overwrite: bool = False
    llm: Optional[sw.LlmPick] = None


def _njob_key(wid: str, sid: str, cid: str) -> str:
    return f"{wid}:{sid}:{cid}"


def _ntick(key: str, detail: str, *, stage: str = "") -> None:
    j = _NARR_JOBS.get(key)
    if not j:
        return
    if stage:
        j["stage"] = stage
    if j.get("detail") == detail:
        return                      # log CHANGES, not polls
    j["detail"] = detail
    j.setdefault("log", []).append(
        {"t": round(time.time() - float(j.get("t0") or time.time()), 1),
         "stage": j.get("stage") or "", "detail": detail})
    del j["log"][:-200]


def _beat_groups(n: int, total: int) -> List[tuple]:
    """Plan the calls: `[(beat_indices, word_budget), …]`.

    ⚠ A beat asked for 60 words spends them on throat-clearing and delivers no
    story, so when the budget is thin relative to the beat count we make FEWER,
    FATTER calls — each one covering SEVERAL consecutive beats.

    ⚠⚠ **EVERY BEAT LANDS IN EXACTLY ONE GROUP.** The first version handed the
    first N beats a budget and zeroed the rest, so a 24-beat chapter at 1500
    words narrated beats 1-12 and **silently never told 13-24** — half the
    chapter missing, with a green job and a plausible word count. Grouping
    cannot drop one: the indices are a partition."""
    if n <= 0:
        return []
    calls = max(1, min(n, max(1, total // _MIN_BEAT_WORDS)))
    per, rem = divmod(total, calls)
    base, extra = divmod(n, calls)
    out, k = [], 0
    for g in range(calls):
        size = base + (1 if g < extra else 0)
        out.append((list(range(k, k + size)), per + (rem if g == calls - 1 else 0)))
        k += size
    return out


_NARR_SYSTEM = (
    "You are a narration writer for a video. You write PROSE THAT WILL BE READ "
    "ALOUD — no headings, no stage directions, no bullet points, no lyrics, "
    "nothing in brackets, no meta-commentary about what you are writing. "
    "Concrete, sensory, specific: what is seen, heard, done. Vary sentence "
    "length; short sentences land the beats. You never invent characters or "
    "places that were not given to you, and you never use franchise, brand or "
    "real-celebrity names. You answer with JSON only."
)

#: ⭐ The paragraph instruction is repeated in the SYSTEM sense and again per
#: call, and then ENFORCED by `_paragraphize` regardless — because "paragraphs
#: matter in TTS" is a hard requirement, and a prompt is a request.
_PARA_RULE = (
    "Write in PARAGRAPHS of 2-5 sentences, separated by a BLANK LINE. Never "
    "return one unbroken block: a paragraph break is where the reader breathes "
    "and where this app places its pauses. Return a JSON object with the single "
    "key \"narration\", whose value is a STRING containing those paragraphs "
    "separated by \\n\\n."
)


def _beat_prompt(w: dict, st: dict, c: dict, beats: List[dict], idx: List[int],
                 budget: int, tone: str, person: str, prev_tail: str,
                 last: bool) -> str:
    mine = [beats[i] for i in idx]
    lines = [_chapter_ctx(w, st, c),
             f"\n\nTHIS CHAPTER: {c.get('title')}",
             (c.get("summary") or ""),
             "\n\nTHE CHAPTER'S BEATS, in order (yours are marked ►):"]
    for k, x in enumerate(beats):
        mark = "►" if k in idx else " "
        lines.append(f" {mark} {k + 1}. {x.get('title')}: "
                     f"{(x.get('summary') or '')[:200]}")
    if prev_tail:
        # ⭐ CONTINUITY. Without the tail each call restarts the chapter: the
        # same character is introduced three times and the weather changes.
        lines += ["\n\nTHE LAST WORDS YOU WROTE (continue straight on from "
                  f"these — do NOT repeat or re-introduce them):\n…{prev_tail}"]
    else:
        lines += ["\n\nThis is the OPENING of the chapter."]
    moods = [b["mood"] for b in mine if (b.get("mood") or "").strip()]
    if moods:
        lines.append(f"\nMood: {'; '.join(moods)}")
    who = sorted({x for b in mine for x in (b.get("characters") or [])})
    where = sorted({x for b in mine for x in (b.get("locations") or [])})
    if who:
        lines.append(f"Present: {', '.join(who)}")
    if where:
        lines.append(f"Where: {', '.join(where)}")
    if tone.strip():
        lines.append(f"\nTONE: {tone.strip()}")
    lines.append(f"\nWrite in the {'first' if person == 'first' else 'third'} person.")
    span = (f"beat {idx[0] + 1} — “{mine[0].get('title')}”" if len(idx) == 1 else
            f"beats {idx[0] + 1}-{idx[-1] + 1} — "
            + " then ".join(f"“{b.get('title')}”" for b in mine))
    lines.append(
        f"\n\nWrite ONLY {span}, as narration, **about {budget} words**. Tell it "
        f"fully and at pace: what happens, what it looks like, what it costs the "
        f"people in it. Do not summarise, and do not race to the end of the "
        f"chapter — the rest is being written separately."
        + (" This is the LAST stretch — land the chapter." if last
           else " End where the next stretch can pick up."))
    lines.append("\n" + _PARA_RULE)
    return "\n".join(lines)


def _tail(text: str, words: int = 60) -> str:
    return " ".join((text or "").split()[-words:])


def _run_narration(key: str, wid: str, sid: str, cid: str,
                   cfg: tuple, body: ChapterNarrationIn, host_note: str) -> None:
    from backend.api.concept import _call_llm
    j = _NARR_JOBS[key]
    t_start = time.time()
    try:
        j["status"] = "running"
        w = sw._load(wid)
        st = _story_of(w, sid)
        c = _find_chapter(st, cid)
        minutes = (float(body.minutes or 0) or float(c.get("target_minutes") or 0)
                   or _DEFAULT_MINUTES)
        minutes = max(0.5, min(minutes, 120.0))
        total = int(minutes * _WPM)
        j["target_words"] = total
        j["target_minutes"] = minutes

        # ── stage: beats (only when the chapter has none) ───────────────────
        beats = list(c.get("beats") or [])
        if not beats and body.with_beats:
            t0 = time.time()
            _ntick(key, "no beats yet — deciding how this chapter breaks up",
                   stage="beats")
            want = max(3, min(round(total / 260), 10))
            txt = _call_llm(
                cfg[0], cfg[1], cfg[2],
                "You are a story editor. You answer with JSON only.",
                _chapter_ctx(w, st, c)
                + f"\n\nTHIS CHAPTER: {c.get('title')}\n{c.get('summary') or ''}"
                + f"\n\nBreak this chapter into about {want} BEATS — consecutive "
                  "movements of the story, in order, that together tell the whole "
                  "chapter. Each beat is one stretch of video. Return a JSON "
                  "array of objects with \"title\", \"summary\" (what happens, "
                  "what is seen), \"mood\", \"characters\" and \"locations\".",
                4000)
            beats = sw._clean_arcs(sw._json_arr(txt))[:_BEAT_CAP]
            if not beats:
                raise RuntimeError("the model returned no usable beats")
            with sw._LOCK:
                w2 = sw._load(wid)
                c2 = _find_chapter(_story_of(w2, sid), cid)
                c2["beats"] = _keep_beat_ids(c2.get("beats") or [], beats)
                beats = c2["beats"]
                c2["updated_at"] = sw._now()
                sw._save(w2)
            j.setdefault("stage_times", {})["beats"] = round(time.time() - t0, 1)
            _ntick(key, f"{len(beats)} beats")
        if not beats:
            # no beats and not allowed to make any — one call, still paragraphed
            beats = [{"id": "", "i": 0, "title": c.get("title") or "the chapter",
                      "summary": c.get("summary") or "", "mood": c.get("mood") or "",
                      "characters": c.get("characters") or [],
                      "locations": c.get("locations") or []}]

        # ── stage: writing, ONE CALL PER BEAT ───────────────────────────────
        t0 = time.time()
        groups = _beat_groups(len(beats), total)
        j["total"] = len(groups)
        j["done"] = 0
        j["beats"] = len(beats)
        blocks: List[str] = []
        for g, (idx, budget) in enumerate(groups):
            if j.get("cancel"):
                raise RuntimeError("cancelled")
            names = " + ".join(beats[i].get("title") or f"beat {i + 1}" for i in idx)
            j["current"] = names
            _ntick(key, f"writing {g + 1}/{len(groups)} — “{names}” "
                        f"(~{budget} words)", stage="writing")
            got = sw._json_obj(_call_llm(
                cfg[0], cfg[1], cfg[2], _NARR_SYSTEM,
                _beat_prompt(w, st, c, beats, idx, budget, body.tone,
                             body.person, _tail(blocks[-1] if blocks else ""),
                             g == len(groups) - 1),
                max(2000, int(budget * 3.2))))
            # ⚠ _prose, not sw._flat — see the helper's docstring.
            part = _paragraphize(_prose(got.get("narration") or ""))
            if not part.strip():
                _ntick(key, f"⚠ “{names}” came back empty — kept going")
                continue
            blocks.append(part)
            j["done"] = int(j.get("done") or 0) + 1
            j["words_so_far"] = sum(_words(x) for x in blocks)
            _ntick(key, f"“{names}” done — {j['words_so_far']} words so far "
                        f"of ~{total}")
        j.setdefault("stage_times", {})["writing"] = round(time.time() - t0, 1)
        if not blocks:
            raise RuntimeError("every beat came back empty")

        # ⭐ joined with BLANK LINES, so the seams are paragraph breaks and the
        # whole thing reads as one narration rather than N stapled essays.
        text = _paragraphize("\n\n".join(blocks))
        with sw._LOCK:
            w2 = sw._load(wid)
            st2 = _story_of(w2, sid)
            c2 = _find_chapter(st2, cid)
            c2["narration"] = text
            c2["narration_words"] = _words(text)
            c2["target_minutes"] = minutes
            c2["updated_at"] = sw._now()
            st2["updated_at"] = sw._now()
            sw._save(w2)
        wc = _words(text)
        j.update(status="done", words=wc,
                 est_minutes=round(wc / _WPM, 1),
                 paragraphs=len([p for p in text.split("\n\n") if p.strip()]),
                 elapsed_s=round(time.time() - t_start, 1))
        _ntick(key, f"done — {wc} words ≈ {round(wc / _WPM, 1)} min in "
                    f"{j['paragraphs']} paragraphs", stage="done")
    except Exception as ex:                                      # noqa: BLE001
        cancelled = "cancel" in str(ex).lower()
        j.update(status="cancelled" if cancelled else "error", error=str(ex),
                 elapsed_s=round(time.time() - t_start, 1))
        _ntick(key, "cancelled" if cancelled else f"failed: {ex}", stage="error")
        if not cancelled:
            logger.exception("chapter narration failed for %s", key)


@router.post("/worlds/{wid}/stories/{sid}/chapters/{cid}/narration")
async def write_chapter_narration(wid: str, sid: str, cid: str,
                                  body: ChapterNarrationIn,
                                  session: AsyncSession = Depends(get_session)):
    """✍ Write the FULL narration for ONE chapter — **beat by beat**.

    Starts a job (this is N model calls and takes minutes); poll
    `GET …/chapters/{cid}/narration/job`.

    ⭐ Length is a WORD BUDGET (minutes × 150), never a duration — models honour
    a word count and ignore "about ten minutes". Default **10 minutes ≈ 1500
    words**; the chapter's own `target_minutes` overrides it.

    ⭐ The budget is SPLIT ACROSS THE BEATS and spent one call at a time. One
    call asked for 1500 words returns 400 and calls it done; six calls asked for
    250 each return 1500. That is the whole reason this is a job."""
    w = sw._load(wid)
    st = _story_of(w, sid)
    c = _find_chapter(st, cid)
    if (c.get("narration") or "").strip() and not body.overwrite:
        raise HTTPException(409, "this chapter already has narration — tick "
                                 "overwrite to rewrite it")
    key = _njob_key(wid, sid, cid)
    # claim BEFORE the await (the v1.277.0 double-submit lesson)
    if (_NARR_JOBS.get(key) or {}).get("status") in ("starting", "running"):
        raise HTTPException(409, "this chapter is already being written")
    _NARR_JOBS[key] = {"status": "starting", "stage": "beats", "detail": "",
                       "t0": time.time(), "total": 0, "done": 0, "current": "",
                       "cancel": False, "log": [], "stage_times": {},
                       "chapter": c.get("title") or "", "words_so_far": 0}
    try:
        cfg = await sw._llm_cfg(session, body.llm or sw._pick_of(w))
    except Exception:
        _NARR_JOBS.pop(key, None)
        raise
    host_note = cfg[0]
    if cfg[0] == "ollama":
        try:
            urls = json.loads(cfg[1]) if cfg[1].startswith("[") else [cfg[1]]
            host_note = ", ".join(urls)
        except Exception:                                        # noqa: BLE001
            host_note = "ollama"
    _NARR_JOBS[key].update(provider=cfg[0], model=cfg[2], host=host_note)
    minutes = (float(body.minutes or 0) or float(c.get("target_minutes") or 0)
               or _DEFAULT_MINUTES)
    _ntick(key, f"starting on {cfg[0]}/{cfg[2]} — about "
                f"{int(max(0.5, min(minutes, 120.0)) * _WPM)} words")
    threading.Thread(target=_run_narration,
                     args=(key, wid, sid, cid, cfg, body, host_note),
                     daemon=True, name=f"narr-{cid}").start()
    return {"started": True, "provider": cfg[0], "model": cfg[2],
            "host": host_note, "target_minutes": minutes,
            "target_words": int(minutes * _WPM),
            "beats": len(c.get("beats") or [])}


@router.get("/worlds/{wid}/stories/{sid}/chapters/{cid}/narration/job")
async def chapter_narration_job(wid: str, sid: str, cid: str):
    """Live status. ⚠ elapsed is against the WALL CLOCK while running — a frozen
    timer on a live run is the exact "is this stuck?" question it exists for."""
    j = dict(_NARR_JOBS.get(_njob_key(wid, sid, cid)) or {"status": "idle"})
    if j.get("status") in ("starting", "running") and j.get("t0"):
        j["elapsed_s"] = round(time.time() - float(j["t0"]), 1)
    j.pop("t0", None)
    j.pop("cancel", None)
    return {"job": j}


@router.post("/worlds/{wid}/stories/{sid}/chapters/{cid}/narration/cancel")
async def cancel_chapter_narration(wid: str, sid: str, cid: str):
    j = _NARR_JOBS.get(_njob_key(wid, sid, cid))
    if not j or j.get("status") not in ("starting", "running"):
        raise HTTPException(409, "nothing is being written for this chapter")
    j["cancel"] = True
    _ntick(_njob_key(wid, sid, cid),
           "cancel requested — stopping after the current beat")
    return {"cancelling": True}


# ══════════════════════════════════════════════════════════════════════════
# 🎙 TTS — speak the chapter, audition it, keep the take
# ══════════════════════════════════════════════════════════════════════════
#
# ⭐ These are THIN. All of the machinery — the voice library, the two engines,
# pause tagging, pacing, the render queue — already exists in `audio_lab.py`
# and is called IN-PROCESS (never over HTTP from an async route: that is the
# v1.276.41 deadlock class). What lives here is the chapter's OPINION about it:
# which text to speak, and where the finished take belongs.
#
# ⭐⭐ THE TAKE CARRIES ITS OWN SUBTITLES. A render measures each sentence as it
# joins them, so `job["cues"]` is exact — keeping a take writes the **audio AND
# the srt** onto the chapter in one action. His workflow wants mp3 + srt + a
# scene split; two of those are now free and the third is built from the same
# numbers, so nothing has to be transcribed back out of our own audio.
class ChapterTtsIn(BaseModel):
    voice_id: str
    #: ⭐ CHATTERBOX IS THE DEFAULT (v1.277.51) — it is MIT, so it is the engine
    #: this app can hand to the public; F5 is CC-BY-NC and would put a
    #: non-commercial restriction on everyone who uses it.
    engine: str = "chatterbox"       # chatterbox | f5tts | kokoro
    pace: float = 1.0                # ⚠ >1.0 = SLOWER on every engine
    pace_mode: str = "stretch"       # stretch (quality) | model
    pause_ms: int = 600              # between paragraphs
    sentence_pause_ms: int = 0
    auto_tag: bool = True            # 🪄 write [pause] tags before speaking
    seed: Optional[int] = None
    host: str = ""
    # 🗣 Chatterbox-only. Ignored by the other engines.
    cb_language: str = "English"
    exaggeration: float = 0.5        # 0.25-2.0 — the CHARACTER dial
    temperature: float = 0.8         # 0.05-5.0 — randomness
    cfg_weight: float = 0.5          # 0.0-1.0 — how hard it holds the reference
    cb_crash_template: str = ""


@router.get("/worlds/{wid}/stories/{sid}/chapters/{cid}/tts/options")
async def chapter_tts_options(wid: str, sid: str, cid: str):
    """What this chapter can be spoken WITH — voices, engines, readiness."""
    from backend.api import audio_lab as al
    c = _find_chapter(_story_of(sw._load(wid), sid), cid)
    text = c.get("narration") or ""
    try:
        ov = await al.overview()
    except Exception as e:                                       # noqa: BLE001
        logger.warning("chapter tts options: overview failed: %s", e)
        ov = {}
    voices = al._voices()
    kk_ok, kk_note = al.kokoro_available()
    return {
        "voices": [{"id": v["id"], "name": v["name"],
                    "ready": bool(v.get("ready")),
                    "needs_transcript": bool(v.get("needs_transcript")),
                    "over_cap": bool(v.get("over_cap")),
                    "clip_seconds": v.get("clip_seconds"),
                    # only a FACTORY voice carries a preset, and only it can
                    # use Kokoro — say so here so the UI can grey the engine
                    "kokoro": bool((v.get("kokoro") or {}).get("preset")),
                    # ⚠ per-ENGINE readiness: a transcript-less voice is
                    # unusable by F5 and perfectly fine for Chatterbox, so one
                    # `ready` flag would hide voices from the engine that works.
                    "engines": v.get("engines") or [],
                    # ⭐ the transcript/clip plausibility check — this is the
                    # thing most likely to have made his F5 clone mumble.
                    "transcript_warning": v.get("transcript_warning") or "",
                    "cps": v.get("cps") or 0.0}
                   for v in voices],
        # ⚠ `overview()` returns `workers[].engines`, NOT `hosts[]` — reading
        # the wrong key would have reported F5 as never ready, which looks
        # exactly like a broken fleet.
        "engines": {
            # ⭐ Chatterbox first: MIT, so it is what this app can ship.
            "chatterbox": {
                "ready": any(((wk.get("engines") or {}).get("chatterbox") or {})
                             .get("ready") for wk in (ov.get("workers") or [])),
                "where": "a fleet worker", "licence": "MIT — commercial OK",
                "note": "zero-shot: clones from the clip alone, no transcript "
                        "needed and no 12s cap"},
            "f5tts": {
                "ready": any(((wk.get("engines") or {}).get("f5tts") or {})
                             .get("ready") for wk in (ov.get("workers") or [])),
                "where": "a fleet worker",
                "licence": "CC-BY-NC 4.0 — NON-COMMERCIAL",
                "note": "needs an exact transcript of the reference clip"},
            "kokoro": {"ready": kk_ok, "note": kk_note,
                       "licence": "Apache 2.0 — commercial OK",
                       "where": "the app host (no GPU)"}},
        "words": _words(text), "has_narration": bool(text.strip()),
        "est_minutes": round(_words(text) / _WPM, 1),
        "current": c.get("narration_files") or {},
    }


@router.post("/worlds/{wid}/stories/{sid}/chapters/{cid}/tts")
async def speak_chapter(wid: str, sid: str, cid: str, body: ChapterTtsIn):
    """🎙 Speak this chapter's narration — returns an Audio-Lab job id to poll.

    ⭐ Nothing is written to the chapter yet: this is the AUDITION. Listen to
    it, render another with a different voice or pace, and only then keep one.
    That separation is the whole point — a take that overwrites the chapter the
    moment it finishes cannot be compared with anything."""
    from backend.api import audio_lab as al
    c = _find_chapter(_story_of(sw._load(wid), sid), cid)
    text = (c.get("narration") or "").strip()
    if not text:
        raise HTTPException(409, "this chapter has no narration yet — ✍ write "
                                 "it first")
    if body.auto_tag:
        # 🪄 paragraph/sentence pauses written into the text before it is
        # chunked, so what is spoken is what the tags say.
        text = al.auto_tag(text, retag=True)
    r = await al.tts_generate(al.TtsIn(
        voice_id=body.voice_id, engine=body.engine, text=text,
        pause_ms=body.pause_ms, pace=body.pace, pace_mode=body.pace_mode,
        sentence_pause_ms=body.sentence_pause_ms, seed=body.seed,
        host=body.host,
        cb_language=body.cb_language, exaggeration=body.exaggeration,
        temperature=body.temperature, cfg_weight=body.cfg_weight,
        cb_crash_template=body.cb_crash_template,
        label=f"📖 {c.get('title') or 'chapter'}"))
    return {**r, "chapter": c.get("title"), "words": _words(text)}


class KeepTakeIn(BaseModel):
    job_id: str
    #: also write the SRT built from the render's own cue list. Default ON —
    #: it costs nothing and the project gate requires one.
    with_srt: bool = True
    #: ⭐ and the AAF timeline, one Sound clip per sentence at an edit rate of
    #: the SAMPLE RATE (so every cut point is an exact sample). Default ON: his
    #: workflow is mp3 + srt + aaf, and the set should be complete. It is
    #: verified by reading it back through our OWN importer before it is kept.
    with_aaf: bool = True


@router.post("/worlds/{wid}/stories/{sid}/chapters/{cid}/tts/keep")
async def keep_chapter_take(wid: str, sid: str, cid: str, body: KeepTakeIn):
    """✅ Keep this take — writes the chapter's **audio** and **srt** slots.

    ⚠ The SRT comes from the render's OWN measurements (`job["cues"]`), not
    from transcribing the audio back. If the render predates cues, or its cues
    disagree with the file it produced, the audio still lands and the SRT is
    refused with a reason — a subtitle track that is confidently wrong is worse
    than an absent one, because the scenes get built from it."""
    from backend.api import audio_lab as al
    st = al._JOBS.get(body.job_id)
    if not st:
        raise HTTPException(404, f"render {body.job_id!r} not found")
    if st.get("status") != "done" or not st.get("file"):
        raise HTTPException(409, f"that render is {st.get('status')}, not done")
    src = al._TRACK_DIR / st["file"]
    if not src.exists():
        raise HTTPException(404, "the rendered file is missing on disk")
    _find_chapter(_story_of(sw._load(wid), sid), cid)

    d = _CH_NARR_DIR / wid
    d.mkdir(parents=True, exist_ok=True)
    written, notes = {}, []

    aid = uuid4().hex[:10]
    ext = src.suffix or ".wav"
    fp = d / f"{aid}{ext}"
    fp.write_bytes(src.read_bytes())
    # ⭐⭐ STAMP THE TAKE WITH THE WORDS IT SPOKE. Re-writing a chapter's
    # narration does NOT re-render its audio — so without this, regenerating
    # the text leaves an audio/srt/aaf set that describes the OLD words, the
    # project gate still passes, and the scenes are named with sentences that
    # are no longer in the script. A silent mismatch, exactly the class this
    # lane exists to end. `_readiness` compares this hash and blocks.
    _cur = _find_chapter(_story_of(sw._load(wid), sid), cid)
    _narr = (_cur.get("narration") or "").strip()
    written["audio"] = {
        "id": aid, "filename": f"{st.get('voice') or 'narration'}_{body.job_id}{ext}",
        "ext": ext, "bytes": fp.stat().st_size, "slot": "audio", "playable": True,
        "seconds": float(st.get("seconds") or 0.0), "uploaded_at": sw._now(),
        "spoke_words": _words(_narr),
        "spoke_hash": hashlib.sha1(_narr.encode("utf-8", "replace")).hexdigest()[:12],
        "from_audio_lab": {"job": body.job_id, "voice": st.get("voice"),
                           "voice_id": st.get("voice_id"),
                           "engine": st.get("engine"),
                           "pace": st.get("pace"),
                           "pace_mode": st.get("pace_mode")}}

    cues = st.get("cues") or []
    if body.with_srt:
        drift = float(st.get("cue_drift_s") or 0.0)
        if not cues:
            notes.append("no SRT — this render predates cue capture; re-render "
                         "to get one")
        elif drift > 0.6:
            notes.append(f"no SRT — the cues disagree with the audio by "
                         f"{drift:.2f}s, so they would place scenes wrongly")
        else:
            sid2 = uuid4().hex[:10]
            sfp = d / f"{sid2}.srt"
            sfp.write_text(al.cues_to_srt(cues), "utf-8")
            written["srt"] = {
                "id": sid2, "filename": f"{st.get('voice') or 'narration'}"
                                        f"_{body.job_id}.srt",
                "ext": ".srt", "bytes": sfp.stat().st_size, "slot": "srt",
                "playable": False, "seconds": 0.0, "uploaded_at": sw._now(),
                "cues": len(cues),
                "from_audio_lab": {"job": body.job_id, "measured": True}}

    # ── 🎬 the AAF timeline ────────────────────────────────────────────────
    if body.with_aaf and cues and float(st.get("cue_drift_s") or 0.0) <= 0.6:
        try:
            from backend.services.export_aaf import (AafExportError,
                                                     cues_to_aaf,
                                                     verify_roundtrip)
            aid2 = uuid4().hex[:10]
            afp = d / f"{aid2}.aaf"
            rep = cues_to_aaf(cues, afp, sample_rate=24000,
                              title=f"{st.get('voice') or 'narration'}",
                              total_seconds=float(st.get("seconds") or 0.0))
            # ⭐⭐ READ IT BACK WITH OUR OWN IMPORTER BEFORE KEEPING IT.
            # "It wrote a file" is not evidence. If the round-trip does not
            # recover every boundary we throw the file away rather than hand
            # him a timeline that looks authoritative and is not — that is the
            # exact failure mode this whole lane exists to end.
            chk = verify_roundtrip(afp, cues)
            if not chk.get("ok"):
                afp.unlink(missing_ok=True)
                notes.append(f"no AAF — it did not survive a round-trip through "
                             f"our own importer ({chk.get('note') or chk})")
            else:
                written["aaf"] = {
                    "id": aid2,
                    "filename": f"{st.get('voice') or 'narration'}"
                                f"_{body.job_id}.aaf".replace(" ", "_"),
                    "ext": ".aaf", "bytes": afp.stat().st_size, "slot": "aaf",
                    "playable": False, "seconds": rep.get("seconds") or 0.0,
                    "uploaded_at": sw._now(), "clips": rep.get("clips"),
                    "verified": chk,
                    "from_audio_lab": {"job": body.job_id, "measured": True,
                                       "edit_rate": rep.get("edit_rate")}}
        except AafExportError as e:
            notes.append(f"no AAF — {e}")
        except Exception as e:                                   # noqa: BLE001
            logger.warning("keep-take: AAF export failed: %s", e)
            notes.append(f"no AAF — {type(e).__name__}: {e}")

    old = []
    with sw._LOCK:
        w = sw._load(wid)
        st2 = _story_of(w, sid)
        c2 = _find_chapter(st2, cid)
        files = dict(c2.get("narration_files") or {})
        for slot, meta in written.items():
            if files.get(slot) and files[slot].get("id") != meta["id"]:
                old.append(files[slot])
            files[slot] = meta
        c2["narration_files"] = files
        # ⭐ the cue list rides along on the CHAPTER too, so a project can build
        # its scenes from the exact numbers without re-reading the SRT.
        if cues:
            c2["cues"] = cues
        c2["updated_at"] = sw._now()
        st2["updated_at"] = sw._now()
        sw._save(w)
    for m in old:                       # files go only after the write lands
        _ch_slot_fp(wid, m).unlink(missing_ok=True)

    # breadcrumb on the render, so 🪪 voice details can show where it landed
    try:
        with al._LOCK:
            st.setdefault("used_in", []).append(
                {"kind": "chapter", "world_id": wid, "story_id": sid,
                 "chapter_id": cid, "slots": list(written), "at": sw._now()})
            al._jobs_save()
    except Exception as e:                                       # noqa: BLE001
        logger.debug("keep-take: breadcrumb failed: %s", e)
    return {"kept": list(written), "files": written, "cues": len(cues),
            "notes": notes}


# ══════════════════════════════════════════════════════════════════════════
# 🎬 CREATE A PROJECT FROM THIS CHAPTER
# ══════════════════════════════════════════════════════════════════════════
#
# His ask: *"Once we have a chapter and its Narration Audio and associated
# files can we have a button in the chapter area to create a project, which
# asks us what kind of project we want to create… and have it make the project
# setting all information and character info we have into the project so we can
# get started working. Make it so it requires all the narration files needed to
# do this before it starts and warn the user if they don't have them."*
#
# ⭐ HIS GATE (2026-08-18): **narration text + audio + SRT, all three.** The
# strict option, deliberately — his workflow is mp3 + srt + a scene split, and
# a project created without the SRT silently falls back to Whisper guessing at
# words it already has perfectly. Readiness is a SEPARATE GET so the button can
# be disabled with a reason BEFORE it is pressed; the POST re-checks anyway,
# because a screen is a cache.
_PROJECT_MODES = [
    {"key": "narration_video", "label": "🎬 Narration video",
     "hint": "moving video per scene — the full pipeline"},
    {"key": "narration_images", "label": "🖼 Narration images",
     "hint": "stills per scene, no video render; convertible to video later"},
    {"key": "talkie", "label": "🗣 Talkie",
     "hint": "a portrait that lip-syncs the narration"},
    {"key": "music_video", "label": "🎵 Music video",
     "hint": "section-driven; rarely what a narrated chapter wants"},
]


def _readiness(w: dict, st: dict, c: dict) -> dict:
    """What this chapter has, what it is missing, and whether that BLOCKS.

    ⭐ Blocking and warning are different lists on purpose. Missing beats or an
    empty cast make a WORSE project, not an impossible one — refusing on them
    would be this tool deciding how finished his writing has to be."""
    files = c.get("narration_files") or {}
    text = (c.get("narration") or "").strip()
    have = {
        "narration_text": bool(text),
        "audio": bool(files.get("audio")),
        "srt": bool(files.get("srt")),
        "cues": len(c.get("cues") or []),
        "beats": len(c.get("beats") or []),
        "aaf": bool(files.get("aaf")),
    }
    # ⭐⭐ IS THE TAKE STILL THE TEXT? Re-writing the narration does not
    # re-render the audio, so a chapter can sit here with a perfectly valid
    # audio+srt+aaf set that speaks the PREVIOUS draft. Everything would pass,
    # and the scenes would be named with sentences no longer in the script.
    # This compares what the take SPOKE against what the chapter now SAYS.
    _stamp = (files.get("audio") or {}).get("spoke_hash")
    have["take_matches_text"] = (
        None if not (have["audio"] and text) else
        (_stamp == hashlib.sha1(text.encode("utf-8", "replace")).hexdigest()[:12]
         if _stamp else None))

    blocking = []
    if not have["narration_text"]:
        blocking.append("no narration text — ✍ write the chapter first")
    if not have["audio"]:
        blocking.append("no narration AUDIO — 🎙 speak it with a voice, or "
                        "upload a recording")
    elif have["take_matches_text"] is False:
        blocking.append(
            f"the narration was REWRITTEN after this take was recorded — the "
            f"audio speaks {(files.get('audio') or {}).get('spoke_words')} words "
            f"and the chapter now has {_words(text)}. 🎙 render and keep a new "
            f"take, or the scenes will be cut to sentences that are no longer "
            f"in the script")
    if not have["srt"]:
        blocking.append("no SRT — keeping a 🎙 take writes one automatically "
                        "from the render's own timings; a hand-uploaded "
                        "recording needs one uploaded alongside it")
    warnings = []
    if not have["beats"]:
        warnings.append("this chapter has no beats, so the project starts with "
                        "no chapter structure — 🎬 split it first for better "
                        "per-scene direction")
    cast = [m for m in (w.get("cast") or [])
            if not m.get("story_ids") or st.get("id") in (m.get("story_ids") or [])]
    if not cast:
        warnings.append("no cast is tagged to this story, so no characters "
                        "will be pulled in")
    if not have["cues"]:
        warnings.append("no measured cue list — scenes will come from the "
                        "SRT's timings instead of the render's own")
    if not have["aaf"]:
        warnings.append("no AAF — keeping a 🎙 take writes one automatically "
                        "(it is not required to build the project, since the "
                        "scenes come from the same cues the AAF is written "
                        "from, but you will want it for an NLE)")
    if have["take_matches_text"] is None and have["audio"] and text:
        warnings.append("this take predates v1.277.50, so we cannot tell "
                        "whether it speaks the current text — re-render if the "
                        "narration has changed since")
    secs = float((files.get("audio") or {}).get("seconds") or 0.0)
    return {"ready": not blocking, "blocking": blocking, "warnings": warnings,
            "have": have, "cast": len(cast),
            "audio_seconds": secs,
            "audio_minutes": round(secs / 60.0, 1) if secs else 0.0,
            "words": _words(text),
            "modes": _PROJECT_MODES}


@router.get("/worlds/{wid}/stories/{sid}/chapters/{cid}/project-readiness")
async def chapter_project_readiness(wid: str, sid: str, cid: str):
    """Can this chapter become a project yet — and if not, exactly why."""
    w = sw._load(wid)
    st = _story_of(w, sid)
    return _readiness(w, st, _find_chapter(st, cid))


class MakeProjectIn(BaseModel):
    mode: str = "narration_video"
    name: str = ""                   # "" = the chapter's title
    video_engine: str = "ltx_2.3"
    #: ⭐ Build the scene list straight from the render's measured cues.
    #: THIS IS THE AAF REPLACEMENT (his call, 2026-08-18): an ElevenLabs AAF
    #: exists to carry per-sentence cut points, and we already know ours
    #: exactly — with the words attached, which an AAF does not even have
    #: (it puts the text in the CSV/SRT, never in the timeline).
    scenes_from_cues: bool = True
    #: Cues are per SENTENCE, so a 10-minute chapter is ~80 of them. Merge cut
    #: points closer than this — the same knob the AAF panel calls
    #: "Merge cuts <". 0 = one scene per sentence.
    min_scene_seconds: float = 8.0
    #: ⚠ OFF by default and it must stay that way — see the route's docstring.
    force: bool = False


@router.post("/worlds/{wid}/stories/{sid}/chapters/{cid}/create-project")
async def create_project_from_chapter(
        wid: str, sid: str, cid: str, body: MakeProjectIn,
        session: AsyncSession = Depends(get_session)):
    """🎬 Turn this chapter into a project, set up and ready to work in.

    Creates the project, links it to world→story→**chapter**, and pulls
    everything the chapter knows: its narration as the script, its recording as
    the audio, its SRT, its beats as the timeline chapters, and this story's
    cast (narrowed to whoever the chapter names).

    ⚠⚠ **IT REFUSES WITHOUT narration text + audio + SRT** (his call). `force`
    exists for the API and is deliberately not exposed as a button: a project
    created half-set-up looks finished and is not, and the failure surfaces
    hours later as scenes that do not match the words."""
    # ⚠ VALIDATE THE REQUEST BEFORE INSPECTING THE STATE. A typo'd mode is a
    # 400 whatever the chapter looks like; checking readiness first answered a
    # malformed request with "go write your narration", which sends the caller
    # to fix the wrong thing.
    mode = (body.mode or "narration_video").lower()
    if mode not in [m["key"] for m in _PROJECT_MODES]:
        raise HTTPException(400, f"mode must be one of "
                                 f"{[m['key'] for m in _PROJECT_MODES]}")
    w = sw._load(wid)
    st = _story_of(w, sid)
    c = _find_chapter(st, cid)
    rd = _readiness(w, st, c)
    if rd["blocking"] and not body.force:
        raise HTTPException(409, "this chapter is not ready: "
                                 + " · ".join(rd["blocking"]))

    from backend.api.projects import (ProjectCreate, PullFromStoryIn,
                                      create_project, pull_from_story,
                                      set_story_link, StoryLinkIn)
    from backend.database.models import ProjectMode
    name = (body.name or "").strip() or (
        f"{st.get('title') or 'Story'} — {int(c.get('i') or 0) + 1}. "
        f"{c.get('title') or 'Chapter'}")
    proj = await create_project(ProjectCreate(
        name=name[:200], mode=ProjectMode(mode),
        settings={"video_engine": body.video_engine}), session)
    pid = proj.id if hasattr(proj, "id") else proj["id"]

    steps = [f"created “{name}” ({mode})"]
    # ⭐ IN-PROCESS, not over HTTP — an async route calling its own app is the
    # v1.276.41 deadlock. These are the same functions the buttons call.
    await set_story_link(pid, StoryLinkIn(world_id=wid, story_id=sid,
                                          chapter_id=cid, attach=True), session)
    steps.append("linked to the chapter")
    pulled = await pull_from_story(pid, PullFromStoryIn(
        concept=True, style=True, characters=True, chapters=True,
        narration_audio=True, narration_text=True), session)
    steps += list(pulled.get("pulled") or [])

    # ── 🎬 scenes straight from the measured cues ───────────────────────────
    scenes = 0
    if body.scenes_from_cues and (c.get("cues") or []):
        try:
            scenes = await _scenes_from_cues(session, pid, c,
                                             float(body.min_scene_seconds or 0))
            steps.append(f"scenes ({scenes} from the render's own cues — the "
                         f"same sentence boundaries the AAF carries, taken "
                         f"straight from the render)")
            # ⚠ THE PULL ALREADY SAID "press Import AAF on the Audio tab",
            # which was true before this lane existed and is now an
            # instruction to UNDO what we just did: Import AAF REPLACES the
            # scene list. The numbers are identical (the AAF is written FROM
            # these cues and round-trips at 0.0 s), so nothing breaks — but a
            # screen that tells him to redo finished work is the "the screen
            # lied" class, and it costs one line to say the truth instead.
            steps.append("🔒 the timeline is LOCKED — Whisper resync, Suggest "
                         "Timeline and scenes-from-sections will refuse. The "
                         "AAF is attached for your NLE; you do NOT need to "
                         "press Import AAF (it would rebuild these same "
                         "boundaries and is only there if you want it).")
        except Exception as e:                                   # noqa: BLE001
            logger.warning("create-project: scenes from cues failed: %s", e)
            steps.append(f"⚠ scenes from cues failed ({e}) — use Analyze or "
                         f"Import AAF on the Audio tab")
    elif body.scenes_from_cues:
        steps.append("no measured cues on this chapter — no scenes built; "
                     "press Analyze (or upload the SRT) on the Audio tab")
    return {"project_id": str(pid), "name": name, "mode": mode,
            "scenes": scenes, "steps": steps, "warnings": rd["warnings"],
            "forced": bool(body.force and rd["blocking"])}


async def _scenes_from_cues(session: AsyncSession, pid, c: dict,
                            min_scene_seconds: float) -> int:
    """Build the project's scene list from this chapter's cue list.

    ⭐⭐ **THIS IS WHY WE DO NOT NEED TO WRITE AN AAF.** An ElevenLabs AAF is
    imported for exactly one thing — per-sentence cut points — and it does not
    even carry the words (they live in the CSV/SRT, which is why `import_aaf`
    blanks generic clip names and calls them "Scene N"). Our cues were MEASURED
    at render time and carry the spoken text, so the boundaries are better AND
    named. A round-trip through a 227 MB file format to recover numbers we
    already have would only lose information.

    ♻ Reuses `clips_to_scenes` — the pure timeline math already written and
    already unit-testable — so the merging rule is identical to the AAF path
    rather than a second implementation that drifts."""
    from sqlalchemy import select as _select
    from backend.database.models import Project, Scene
    from backend.services.import_aaf import clips_to_scenes
    cues = c.get("cues") or []
    if not cues:
        return 0
    audio_end = max(float(x.get("end") or 0.0) for x in cues)
    clips = [{"start": float(x.get("start") or 0.0),
              "end": float(x.get("end") or 0.0),
              # the SPOKEN WORDS become the scene name — trimmed, because a
              # scene name is a label, not a paragraph
              "name": " ".join(str(x.get("text") or "").split())[:90]}
             for x in cues]
    data = clips_to_scenes(clips, audio_end=audio_end,
                           min_scene_seconds=max(0.0, min_scene_seconds))
    if not data:
        return 0
    for sc in (await session.execute(
            _select(Scene).where(Scene.project_id == pid))).scalars().all():
        await session.delete(sc)
    await session.flush()
    for i, sd in enumerate(data):
        session.add(Scene(project_id=pid, name=sd["name"],
                          start_time=float(sd["start_time"]),
                          end_time=float(sd["end_time"]),
                          order_index=i, prompt="",
                          parameters={"cue_label": sd.get("name", ""),
                                      "from": "chapter_cues"}))
    await session.flush()
    proj = await session.get(Project, pid)
    if proj is not None:
        s = dict(proj.settings or {})
        # ⚠ NOT `audio_source = "aaf"` — no AAF was involved and claiming one
        # would switch on the "superseded" UI and the AAF resync gates.
        s["scene_source"] = "chapter_cues"
        s["cue_import"] = {"cues": len(cues), "scenes": len(data),
                           "min_scene_seconds": min_scene_seconds,
                           "at": sw._now()}
        proj.settings = s
        session.add(proj)
    # the beats became chapters with NO times (there was no audio when they
    # were pulled) — now there are scenes, so re-time them and bind.
    try:
        from backend.services.chapters.from_story import retime_story_chapters
        from backend.services.chapters.resolver import (
            bind_scenes_to_chapters_by_time)
        await retime_story_chapters(session, pid)
        await session.flush()
        await bind_scenes_to_chapters_by_time(session, pid)
    except Exception as e:                                       # noqa: BLE001
        logger.warning("scenes-from-cues: chapter re-time failed: %s", e)
    # per-scene audio slices, best-effort (the AAF path does the same)
    try:
        from backend.api.timeline import _slice_audio_for_scenes
        await _slice_audio_for_scenes(pid, session)
    except Exception as e:                                       # noqa: BLE001
        logger.warning("scenes-from-cues: audio slice failed: %s", e)
    await session.commit()
    return len(data)


class BeatGenIn(BaseModel):
    count: int = 0
    llm: Optional[sw.LlmPick] = None


@router.post("/worlds/{wid}/stories/{sid}/chapters/{cid}/beats")
async def write_chapter_beats(wid: str, sid: str, cid: str, body: BeatGenIn,
                              session: AsyncSession = Depends(get_session)):
    """Break an EXISTING chapter narration into beats (the project's chapters).

    Only needed when the narration was typed by hand or written before beats
    existed — ✍ narration already returns them."""
    w = sw._load(wid)
    st = _story_of(w, sid)
    c = _find_chapter(st, cid)
    text = (c.get("narration") or "").strip()
    if not text:
        raise HTTPException(409, "this chapter has no narration to split — "
                                 "write it first")
    n = max(0, min(int(body.count or 0), _BEAT_CAP))
    system = ("You segment narration into consecutive visual beats for a video "
              "storyboard. You never add events that are not in the text. You "
              "answer with JSON only.")
    user = (_chapter_ctx(w, st, c)
            + f"\n\nTHE NARRATION OF THIS CHAPTER:\n\n{text[:60000]}\n\n"
            + (f"Split it into exactly {n} beats." if n else
               "Split it into 3-8 beats.")
            + " Beats are CONSECUTIVE slices of this text, in order, covering "
              "all of it. Return a JSON array of objects with \"title\", "
              "\"summary\" (what is seen and heard in that slice), \"mood\", "
              "\"characters\" and \"locations\".")
    got = await sw._ask_json(session, body.llm or sw._pick_of(w), system, user,
                             want="array", max_tokens=5000, timeout_s=600)
    if not sw._clean_arcs(got):
        raise HTTPException(502, "the model returned no usable beats")
    with sw._LOCK:
        w = sw._load(wid)
        st2 = _story_of(w, sid)
        c2 = _find_chapter(st2, cid)
        c2["beats"] = _keep_beat_ids(c2.get("beats") or [], got)
        c2["updated_at"] = sw._now()
        sw._save(w)
    return _chapter_view(c2, st2)


@router.post("/worlds/{wid}/stories/{sid}/chapters/{cid}/delete")
async def delete_chapter(wid: str, sid: str, cid: str):
    metas: List[dict] = []
    with sw._LOCK:
        w = sw._load(wid)
        st = _story_of(w, sid)
        c = _find_chapter(st, cid)
        metas = list((c.get("narration_files") or {}).values())
        st["chapters"] = _renumber([x for x in _chapters(st) if x["id"] != cid])
        st["updated_at"] = sw._now()
        sw._save(w)
    for m in metas:                       # files go only after the write lands
        _ch_slot_fp(wid, m).unlink(missing_ok=True)
    return {"deleted": cid}


# ── 🎙 a chapter's own recording (audio · aaf · srt) ─────────────────────────
def _ch_slot_fp(wid: str, meta: dict) -> Path:
    return _CH_NARR_DIR / wid / f"{meta.get('id')}{meta.get('ext') or ''}"


@router.post("/worlds/{wid}/stories/{sid}/chapters/{cid}/file/{slot}")
async def upload_chapter_file(wid: str, sid: str, cid: str, slot: str,
                              file: UploadFile = File(...)):
    """🎙 One chapter, one recording — because one chapter is one video.

    Same three slots as the story lane (audio · aaf · srt) and the same rules;
    a story keeps its own set for the whole-story version."""
    if slot not in sw._NARR_SLOTS:
        raise HTTPException(400, f"slot must be one of {list(sw._NARR_SLOTS)}")
    _find_chapter(_story_of(sw._load(wid), sid), cid)
    name = (file.filename or slot).strip()
    ext = ("." + name.rsplit(".", 1)[-1].lower()) if "." in name else ""
    if ext not in sw._NARR_EXT[slot]:
        raise HTTPException(400, f"a {slot} file must be one of "
                                 f"{sorted(sw._NARR_EXT[slot])}")
    raw = await file.read()
    if not raw:
        raise HTTPException(400, "empty file")
    aid = uuid4().hex[:10]
    d = _CH_NARR_DIR / wid
    d.mkdir(parents=True, exist_ok=True)
    fp = d / f"{aid}{ext}"
    fp.write_bytes(raw)
    # ⚠ ffprobe is a subprocess with a 60s timeout — off the event loop, or one
    # hung probe stalls every request in the app for a minute.
    secs = (await asyncio.to_thread(sw._probe_seconds, fp)
            if slot == "audio" else 0.0)
    meta = {"id": aid, "filename": name, "ext": ext, "bytes": len(raw),
            "slot": slot, "playable": slot == "audio",
            # a MEASUREMENT, not the word-budget estimate. Keep them apart.
            "seconds": secs, "uploaded_at": sw._now()}
    old = None
    with sw._LOCK:
        w = sw._load(wid)
        st = _story_of(w, sid)
        c = _find_chapter(st, cid)
        files = dict(c.get("narration_files") or {})
        old = files.get(slot)
        files[slot] = meta
        c["narration_files"] = files
        c["updated_at"] = sw._now()
        st["updated_at"] = sw._now()
        sw._save(w)
    if old and old.get("id") != aid:
        _ch_slot_fp(wid, old).unlink(missing_ok=True)
    return {"slot": slot, "file": meta, "files": files}


@router.get("/worlds/{wid}/stories/{sid}/chapters/{cid}/file/{slot}")
async def get_chapter_file(wid: str, sid: str, cid: str, slot: str,
                           download: bool = False):
    c = _find_chapter(_story_of(sw._load(wid), sid), cid)
    meta = (c.get("narration_files") or {}).get(slot)
    if not meta:
        raise HTTPException(404, f"this chapter has no {slot} file")
    fp = _ch_slot_fp(wid, meta)
    if not fp.exists():
        raise HTTPException(404, "the file is missing on disk")
    from fastapi.responses import FileResponse
    mt = {".mp3": "audio/mpeg", ".wav": "audio/wav", ".flac": "audio/flac",
          ".m4a": "audio/mp4", ".aac": "audio/aac", ".ogg": "audio/ogg",
          ".srt": "text/plain", ".vtt": "text/vtt"}.get(
              meta.get("ext") or "", "application/octet-stream")
    if download or slot != "audio":
        return FileResponse(str(fp), media_type=mt,
                            filename=meta.get("filename") or fp.name)
    return FileResponse(str(fp), media_type=mt)


@router.post("/worlds/{wid}/stories/{sid}/chapters/{cid}/file/{slot}/delete")
async def delete_chapter_file(wid: str, sid: str, cid: str, slot: str):
    meta = None
    with sw._LOCK:
        w = sw._load(wid)
        st = _story_of(w, sid)
        c = _find_chapter(st, cid)
        files = dict(c.get("narration_files") or {})
        meta = files.pop(slot, None)
        c["narration_files"] = files
        c["updated_at"] = sw._now()
        sw._save(w)
    if meta:
        _ch_slot_fp(wid, meta).unlink(missing_ok=True)
    return {"deleted": bool(meta), "slot": slot}


# ⚠⚠ THE PARAMETERISED CHAPTER UPDATE IS DECLARED LAST, after every literal
# above it. `/chapters/generate` and `/chapters/reorder` are the same SHAPE as
# `/chapters/{cid}`; declared the other way round they arrive here as
# cid="generate" and 404. This module's parent shipped exactly that bug on its
# first smoke run — seven failures, one cause.
@router.post("/worlds/{wid}/stories/{sid}/chapters/{cid}")
async def update_chapter(wid: str, sid: str, cid: str, body: ChapterIn):
    with sw._LOCK:
        w = sw._load(wid)
        st = _story_of(w, sid)
        c = _find_chapter(st, cid)
        if body.arc_id and not _arc_of(st, body.arc_id):
            raise HTTPException(404, f"arc {body.arc_id!r} not found on this story")
        _apply_chapter(c, body.model_dump(exclude_none=True))
        st["updated_at"] = sw._now()
        sw._save(w)
    return _chapter_view(c, st)
