"""🎼 Score a story (v1.277.16) — the ARC PAIRING lane.

The Audio Lab could always render a track of an EXACT length. What it could
not do was answer "how long should this one be, and what should it sound
like" from the story itself. This module is that step:

    world + story  ->  LLM  ->  N CUES on PAPER  ->  edit  ->  render  ->
    import every finished cue into a project as MUSIC assets

**Paper first.** `plan` writes cues and renders NOTHING (his standing design
call from the Story/World Builder — everything reviewable as paper before a
render). Cue seconds are the load-bearing field: the plan is normalised so the
cue lengths SUM to the requested total, and each cue is clamped to the 5-300s
the engines accept. `manual` builds the same object with no LLM at all.

**Three traps this lane is built around, all previously paid for:**
 1. Renders are enqueued IN-PROCESS via `audio_lab.enqueue_music` — never by
    self-calling this app's HTTP API from an async route (v1.276.41 deadlock).
 2. Workers are assigned ROUND-ROBIN UP FRONT. Asking for "the first ready
    worker" once per cue in a loop PINS every cue to one box (v1.276.45).
 3. A cue is CLAIMED under the lock BEFORE its job starts, and the claim is
    reverted if the enqueue raises — a double-clicked Render must not queue
    the same cue twice (v1.277.0 ③).

Storage: <project_dir>/_libraries/audio_lab/scores/<id>.json
"""
from __future__ import annotations

import json
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.api.audio_lab import MUSIC_ENGINES
from backend.config import settings as cfg

router = APIRouter(prefix="/api/audio-lab/score", tags=["audio-lab"])

_ROOT = Path(cfg.project_dir) / "_libraries" / "audio_lab" / "scores"
_LOCK = threading.RLock()          # scores are read-modify-write

MIN_CUE = 5.0                      # the engines' floor
MAX_CUE = 300.0                    # the engines' ceiling
MAX_CUES = 24


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


# ── storage ──────────────────────────────────────────────────────────────────
def _fp(sid: str) -> Path:
    return _ROOT / f"{sid}.json"


def _load(sid: str) -> dict:
    try:
        return json.loads(_fp(sid).read_text("utf-8"))
    except FileNotFoundError:
        raise HTTPException(404, f"score {sid!r} not found")
    except Exception as e:                                    # noqa: BLE001
        raise HTTPException(500, f"score {sid!r} unreadable: {e}")


def _save(s: dict) -> None:
    _ROOT.mkdir(parents=True, exist_ok=True)
    s["updated_at"] = _now()
    fp = _fp(s["id"])
    # unique temp name + replace: os.replace fails on Windows while a READER
    # has the target open, and the board polls these every few seconds.
    tmp = fp.with_name(f"{fp.stem}.{uuid.uuid4().hex[:6]}.tmp")
    tmp.write_text(json.dumps(s, indent=2), "utf-8")
    for i in range(6):
        try:
            tmp.replace(fp)
            return
        except PermissionError:
            time.sleep(0.05 * (i + 1))
    tmp.replace(fp)                # last try: let it raise honestly


def _all() -> List[dict]:
    out = []
    for fp in sorted(_ROOT.glob("*.json")):
        try:
            out.append(json.loads(fp.read_text("utf-8")))
        except Exception:                                     # noqa: BLE001
            continue
    return sorted(out, key=lambda s: s.get("created_at") or "", reverse=True)


# ── cue shaping ──────────────────────────────────────────────────────────────
def _clean_cue(i: int, c: dict) -> dict:
    return {
        "i": i,
        "name": (str(c.get("name") or f"Cue {i + 1}"))[:80],
        "seconds": max(MIN_CUE, min(MAX_CUE, float(c.get("seconds") or 30))),
        "caption": (str(c.get("caption") or ""))[:1200],
        "lyrics": (str(c.get("lyrics") or ""))[:4000],
        "beat": (str(c.get("beat") or ""))[:400],      # what happens here
        # 🎛 v1.277.19 — tempo/key are STRUCTURED, never caption prose: ACE
        # wants them in its widgets, MM3 wants them written into its caption,
        # and `prompt_shape` projects whichever the chosen engine needs.
        "bpm": int(c.get("bpm") or 0),
        "key": (str(c.get("key") or ""))[:24],
        "job_id": str(c.get("job_id") or ""),
        "status": str(c.get("status") or "paper"),     # paper|claimed|see job
        "file": str(c.get("file") or ""),
    }


def _normalise(cues: List[dict], total: Optional[float]) -> List[dict]:
    """Clamp every cue, then make the lengths SUM to `total` by adjusting the
    LAST cue. Exact length is the whole point of this lane — a plan whose
    parts do not add up to the whole is the bug it exists to prevent."""
    out = [_clean_cue(i, c) for i, c in enumerate(cues[:MAX_CUES])]
    if not out or not total:
        return out
    total = max(MIN_CUE, min(MAX_CUE * len(out), float(total)))
    drift = round(total - sum(c["seconds"] for c in out), 2)
    if abs(drift) >= 0.5:
        last = out[-1]
        fixed = max(MIN_CUE, min(MAX_CUE, last["seconds"] + drift))
        last["adjusted_by"] = round(fixed - last["seconds"], 2)
        last["seconds"] = fixed
    for c in out:
        c["seconds"] = round(c["seconds"], 2)
    return out


# ── live status ──────────────────────────────────────────────────────────────
def _with_status(s: dict) -> dict:
    """Merge each cue's LIVE job state in at read time.

    Read-time merge, not a migration: the job store is the source of truth for
    "what is rendering right now", and copying it into the score file would
    just create a second, staler copy of it (the .52 lesson)."""
    from backend.api import audio_lab as AL
    out = dict(s)
    cues = []
    for c in (s.get("cues") or []):
        c = dict(c)
        j = AL._JOBS.get(c.get("job_id") or "")
        if j:
            c["status"] = j.get("status") or c.get("status")
            c["worker"] = j.get("worker") or ""
            c["detail"] = j.get("detail") or ""
            c["error"] = j.get("error")
            c["file"] = j.get("file") or ""
            c["elapsed_s"] = (round(time.time() - j["_t0"], 1)
                              if j.get("status") == "running" and j.get("_t0")
                              else j.get("elapsed_s") or 0)
        elif c.get("job_id"):
            # ⚠ the job was deleted from the board — the cue reverts to paper
            # rather than sitting at "claimed" forever (the v1.277.0 ⑤ bug).
            c["status"] = "paper"
            c["job_id"] = ""
        cues.append(c)
    out["cues"] = cues
    done = sum(1 for c in cues if c.get("status") == "done")
    err = sum(1 for c in cues if c.get("status") == "error")
    run = sum(1 for c in cues if c.get("status") in ("queued", "running"))
    out["progress"] = {"cues": len(cues), "done": done, "error": err,
                       "running": run,
                       "seconds_total": round(sum(c["seconds"] for c in cues), 2),
                       "seconds_done": round(sum(c["seconds"] for c in cues
                                                 if c.get("status") == "done"), 2)}
    return out


# ── sources (one call feeds the whole picker) ────────────────────────────────
@router.get("/sources")
async def sources():
    from backend.api import storyworld as SW
    worlds = SW._all_worlds()
    out = [{"id": w.get("id"), "name": w.get("name") or "(untitled world)",
            "stories": [{"id": st.get("id"), "title": st.get("title") or "",
                         "story_type": st.get("story_type") or ""}
                        for st in (w.get("stories") or [])],
            "texts": [{"id": t.get("id"), "title": t.get("title") or "",
                       "kind": t.get("kind") or "", "chars": len(t.get("body") or ""),
                       "story_id": t.get("story_id") or ""}
                      for t in (w.get("texts") or [])]}
           for w in worlds]
    projects = []
    try:
        from backend.database.database import async_session
        from backend.database.models import Project
        from sqlalchemy import select
        async with async_session() as session:
            r = await session.execute(select(Project))
            projects = [{"id": str(p.id), "name": p.name} for p in r.scalars()]
    except Exception:                                         # noqa: BLE001
        projects = []
    return {"worlds": out, "projects": projects, "scores": [
        {"id": s["id"], "title": s.get("title") or "", "engine": s.get("engine"),
         "cues": len(s.get("cues") or []), "created_at": s.get("created_at")}
        for s in _all()]}


# ── plan (the LLM step) ──────────────────────────────────────────────────────
class PlanIn(BaseModel):
    world_id: str
    story_id: str
    text_id: str = ""                # optional lyrics/narration to sing/score
    engine: str = "ace15"
    cue_count: int = 5
    total_seconds: float = 180.0
    instrumental: bool = True
    guidance: str = ""               # "keep it sparse", "no drums until act 3"
    provider: str = ""
    model: str = ""


_SYS = (
    "You are a music supervisor scoring a story. You answer with JSON only — "
    "no commentary, no markdown fences. Every cue you write must be renderable "
    "by a text-to-music model: describe SOUND (genre, instrumentation, tempo "
    "feel, texture, production, and the VOCAL if there is one), never plot. "
    "The plot belongs in 'beat'. ⚠ Never write a BPM number, a key or a time "
    "signature into 'caption' — put them in the 'bpm' and 'key' fields, which "
    "the renderer gives to each engine in the form that engine expects."
)


def _plan_prompt(w: dict, st: dict, text: str, body: PlanIn) -> str:
    from backend.api import storyworld as SW
    wf = w.get("world") or {}
    sf = st.get("fields") or {}
    bits = [f"WORLD: {w.get('name') or ''}"]
    for k in ("logline", "genre", "tone", "setting", "time_period", "themes"):
        if wf.get(k):
            bits.append(f"{k}: {wf[k]}")
    try:
        style = SW._style_text(w)
    except Exception:                                         # noqa: BLE001
        style = ""
    if style:
        bits.append(f"visual style: {style}")
    bits.append(f"\nSTORY: {st.get('title') or ''} "
                f"({st.get('story_type') or ''})")
    for k in ("logline", "synopsis", "beats", "hook", "ending"):
        if sf.get(k):
            bits.append(f"{k}: {sf[k]}")
    if text:
        bits.append(f"\nTEXT TO SET (use it for the lyrics field):\n{text[:4000]}")
    if body.guidance.strip():
        bits.append(f"\nDIRECTION FROM THE DIRECTOR: {body.guidance.strip()}")
    n = max(1, min(MAX_CUES, int(body.cue_count)))
    per = round(max(MIN_CUE, float(body.total_seconds) / n), 1)
    lyr = ("Every cue is INSTRUMENTAL: return \"lyrics\": \"\"."
           if body.instrumental else
           "Write singable lyrics per cue, tagged [verse]/[chorus]/[bridge]. "
           "Keep them SHORT enough to be sung inside the cue's seconds.")
    return (
        "\n".join(bits)
        + f"\n\nBreak this story into EXACTLY {n} music cues that run in order "
          f"and together cover {body.total_seconds:.0f} seconds "
          f"(about {per:.0f}s each — vary them where the story wants it, but "
          f"keep every cue between {MIN_CUE:.0f} and {MAX_CUE:.0f} seconds and "
          f"keep the TOTAL at {body.total_seconds:.0f}).\n"
        + lyr
        + "\n\nAnswer with a JSON array of objects, each with exactly these "
          "keys:\n"
          '  "name"     short cue title\n'
          '  "beat"     what happens in the story here (one line, for the human)\n'
          '  "seconds"  a number\n'
          '  "caption"  the music description handed to the model (NO bpm/key)\n'
          '  "bpm"      a number, or 0 to let the model choose\n'
          '  "key"      e.g. "G major", or "" to let the model choose\n'
          '  "lyrics"   lyrics or ""\n'
    )


@router.post("/plan")
async def plan(body: PlanIn):
    from backend.api import storyworld as SW
    from backend.database.database import async_session
    if body.engine not in MUSIC_ENGINES:
        raise HTTPException(400, "engine must be one of " + ", ".join(MUSIC_ENGINES))
    w = SW._load(body.world_id)
    st = SW._find(w.get("stories") or [], body.story_id, "story")
    text = ""
    if body.text_id:
        t = SW._find(w.get("texts") or [], body.text_id, "text")
        text = t.get("body") or ""
    async with async_session() as session:
        rows = await SW._ask_json(
            session, SW.LlmPick(provider=body.provider, model=body.model),
            _SYS, _plan_prompt(w, st, text, body), want="array",
            max_tokens=4000)
    if not isinstance(rows, list) or not rows:
        raise HTTPException(502, "the model returned no cues")
    s = {"id": uuid.uuid4().hex[:8],
         "title": f"{st.get('title') or 'story'} — score",
         "world_id": body.world_id, "world_name": w.get("name") or "",
         "story_id": body.story_id, "story_title": st.get("title") or "",
         "text_id": body.text_id,
         "engine": body.engine, "instrumental": bool(body.instrumental),
         "total_seconds": float(body.total_seconds),
         "guidance": body.guidance,
         "created_at": _now(), "updated_at": _now(),
         "cues": _normalise(rows, body.total_seconds)}
    with _LOCK:
        _save(s)
    return _with_status(s)


class ManualIn(BaseModel):
    title: str = "untitled score"
    engine: str = "ace15"
    total_seconds: float = 0.0       # 0 = take the cues at face value
    cues: List[Dict[str, Any]] = []
    world_id: str = ""
    story_id: str = ""


@router.post("/manual")
async def manual(body: ManualIn):
    """The same object without the LLM — hand-written cues, and the path the
    free smoke test drives so the lane can be exercised with no model call."""
    if body.engine not in MUSIC_ENGINES:
        raise HTTPException(400, "engine must be one of " + ", ".join(MUSIC_ENGINES))
    if not body.cues:
        raise HTTPException(400, "give me at least one cue")
    cues = _normalise(body.cues, body.total_seconds or None)
    s = {"id": uuid.uuid4().hex[:8], "title": body.title or "untitled score",
         "world_id": body.world_id, "world_name": "", "story_id": body.story_id,
         "story_title": "", "text_id": "", "engine": body.engine,
         "instrumental": True,
         "total_seconds": round(sum(c["seconds"] for c in cues), 2),
         "guidance": "", "created_at": _now(), "updated_at": _now(),
         "cues": cues}
    with _LOCK:
        _save(s)
    return _with_status(s)


# ── 🎼 score a PROJECT's arcs (the backing-bed lane) ─────────────────────────
class ProjectScoreIn(BaseModel):
    project_id: str
    engine: str = "ace15"
    instrumental: bool = True        # ⭐ backing beds are instrumental BY DEFAULT
    guidance: str = ""
    provider: str = ""
    model: str = ""
    #: 0 = take each chapter's real duration. Backing beds are as long as the
    #: stretch they sit under — "a song per scene" was never the ask.
    seconds_per_arc: float = 0.0
    max_seconds: float = 300.0       # the engines' ceiling per render


@router.post("/project")
async def score_project(body: ProjectScoreIn):
    """🎬 One backing bed per ARC of the project's linked story.

    Cue lengths come from the project's CHAPTERS — which are the arcs, timed
    against the detected audio sections — so a bed is exactly as long as the
    stretch of story it plays under. No LLM call for the lengths; they are
    measured, not guessed.

    ⚠ Instrumental by default: these are beds under narration, and a vocal
    under a voice-over fights it. ⚠ A chapter longer than the engine ceiling
    (300 s) is CLAMPED and flagged rather than silently truncated."""
    from uuid import UUID as _UUID
    from sqlmodel import select as _select
    from backend.database.database import async_session
    from backend.database.models import Chapter, Project
    from backend.services import story_context as sc

    if body.engine not in MUSIC_ENGINES:
        raise HTTPException(400, "engine must be one of " + ", ".join(MUSIC_ENGINES))
    async with async_session() as session:
        try:
            pid = _UUID(str(body.project_id))
        except Exception:                                     # noqa: BLE001
            raise HTTPException(400, "bad project id")
        proj = await session.get(Project, pid)
        if not proj:
            raise HTTPException(404, "project not found")
        st = dict(proj.settings or {})
        ctx = sc.resolve(st)
        if not ctx.get("linked") or not ctx.get("arcs"):
            raise HTTPException(409, "link this project to a story that has "
                                     "arcs first (✨ Structure it on /worlds)")
        chapters = (await session.execute(
            _select(Chapter).where(Chapter.project_id == pid,
                                   Chapter.source == "story")
            .order_by(Chapter.order_index))).scalars().all()
        pname = proj.name

    arcs = ctx["arcs"]
    by_arc = {}
    for ch in chapters:
        aid = (ch.chapter_metadata or {}).get("arc_id")
        if aid:
            by_arc[aid] = ch
    cues = []
    notes = []
    for i, a in enumerate(arcs):
        ch = by_arc.get(a.get("id")) or (chapters[i] if i < len(chapters) else None)
        secs = float(body.seconds_per_arc or 0.0)
        if not secs and ch is not None and ch.end_time and ch.start_time is not None:
            secs = float(ch.end_time) - float(ch.start_time)
        if not secs:
            secs = 60.0
            notes.append(f"{a.get('title')}: no chapter time yet — defaulted to 60s")
        if secs > float(body.max_seconds):
            notes.append(f"{a.get('title')}: {secs:.0f}s clamped to "
                         f"{body.max_seconds:.0f}s (the engine ceiling)")
            secs = float(body.max_seconds)
        # the caption is the arc's MOOD plus the world's sound, never its plot
        caption = ", ".join(x for x in [
            a.get("mood") or "", (ctx.get("style_text") or "")[:200],
            body.guidance.strip()] if x)
        cues.append({"name": a.get("title") or f"Arc {i + 1}",
                     "beat": a.get("summary") or "",
                     "seconds": round(max(MIN_CUE, min(MAX_CUE, secs)), 2),
                     "caption": caption or "instrumental underscore",
                     "lyrics": ""})
    s_obj = {"id": uuid.uuid4().hex[:8],
             "title": f"{pname} — backing beds",
             "world_id": (ctx.get("world") or {}).get("id") or "",
             "world_name": (ctx.get("world") or {}).get("name") or "",
             "story_id": (ctx.get("story") or {}).get("id") or "",
             "story_title": (ctx.get("story") or {}).get("title") or "",
             "text_id": "", "engine": body.engine,
             "instrumental": bool(body.instrumental),
             "project_id": str(body.project_id),
             "total_seconds": round(sum(c["seconds"] for c in cues), 2),
             "guidance": body.guidance, "created_at": _now(),
             "updated_at": _now(),
             # ⚠ NOT normalised to a target: each bed's length is the chapter's
             # real duration, and forcing them to sum to something would undo
             # exactly the property that makes them fit.
             "cues": _normalise(cues, None)}
    with _LOCK:
        _save(s_obj)
    out = _with_status(s_obj)
    out["notes"] = notes
    return out


# ── read / edit ──────────────────────────────────────────────────────────────
@router.get("/list")
async def list_scores():
    return {"scores": [_with_status(s) for s in _all()]}


class CuesIn(BaseModel):
    cues: List[Dict[str, Any]]
    total_seconds: float = 0.0       # 0 = leave the lengths exactly as typed
    engine: str = ""
    title: str = ""


@router.post("/{sid}/cues")
async def edit_cues(sid: str, body: CuesIn):
    """Save the edited cue list. ⚠ Renders in flight KEEP their job ids: the
    write-back is POSITIONAL (by index), never by name — matching by name is
    how two members once shared one job id (v1.277.0 ②)."""
    with _LOCK:
        s = _load(sid)
        old = {c["i"]: c for c in (s.get("cues") or [])}
        cues = _normalise(body.cues, body.total_seconds or None)
        for c in cues:
            prev = old.get(c["i"]) or {}
            if prev.get("job_id") and not c.get("job_id"):
                c["job_id"] = prev["job_id"]
                c["status"] = prev.get("status") or c["status"]
        s["cues"] = cues
        s["total_seconds"] = round(sum(c["seconds"] for c in cues), 2)
        if body.engine in MUSIC_ENGINES:
            s["engine"] = body.engine
        if (body.title or "").strip():
            s["title"] = body.title.strip()
        _save(s)
    return _with_status(s)


@router.post("/{sid}/delete")
async def delete_score(sid: str):
    _load(sid)
    _fp(sid).unlink(missing_ok=True)
    return {"deleted": sid}


# ── render ───────────────────────────────────────────────────────────────────
class RenderIn(BaseModel):
    only: List[int] = []             # cue indices; empty = every unrendered cue
    redo: bool = False               # re-render cues that already have a track
    host: str = ""                   # pin every cue to one box (debugging)


@router.post("/{sid}/render")
async def render(sid: str, body: RenderIn):
    """Fan the cue list across the READY boxes.

    ⚠ Round-robin is assigned UP FRONT. `pick_music_host` returns the FIRST
    ready worker every time, so calling it once per cue pins the whole score
    to one box — the exact shape of the v1.276.45 finding."""
    from backend.api import audio_lab as AL
    with _LOCK:
        s = _load(sid)
        engine = s.get("engine") or "ace15"
        cues = s.get("cues") or []
        want: List[dict] = []
        for c in cues:
            if body.only and c["i"] not in body.only:
                continue
            j = AL._JOBS.get(c.get("job_id") or "")
            live = j and j.get("status") in ("queued", "running")
            done = j and j.get("status") == "done"
            if live:
                continue                       # already rendering — never twice
            if done and not body.redo:
                continue
            want.append(c)
        if not want:
            return _with_status(s)
        # the boxes, in order, checked for THIS engine
        if body.host:
            hosts = [AL.pick_music_host(engine, body.host)]
        else:
            hosts = [h["host"] for h in AL._hosts()
                     if AL._engine_status(h["host"]).get(engine, {}).get("ready")]
        if not hosts:
            AL.pick_music_host(engine)          # raises the 409 that names why
        # CLAIM under the lock BEFORE any job starts: a double-clicked Render
        # must not queue the same cue twice.
        for c in want:
            c["status"] = "claimed"
            c["job_id"] = ""
        _save(s)

    started, failed = [], []
    for n, c in enumerate(want):
        host = hosts[n % len(hosts)]
        try:
            r = AL.enqueue_music(
                engine=engine, tags=c.get("caption") or s.get("title") or "",
                lyrics="" if s.get("instrumental") else (c.get("lyrics") or ""),
                seconds=c["seconds"], host=host,
                bpm=int(c.get("bpm") or 0), keyscale=c.get("key") or "",
                label=f"{s.get('title') or 'score'} · {c['i'] + 1}. {c['name']}",
                meta={"score_id": sid, "cue": c["i"], "cue_name": c["name"]})
            with _LOCK:
                s2 = _load(sid)
                cur = next((x for x in s2["cues"] if x["i"] == c["i"]), None)
                if cur is not None:
                    cur["job_id"] = r["id"]
                    cur["status"] = "queued"
                    cur["worker"] = r["worker"]
                _save(s2)
            started.append({"cue": c["i"], "job": r["id"], "worker": r["worker"]})
        except Exception as e:                                # noqa: BLE001
            # revert the claim — a cue stuck at "claimed" with no job is the
            # wedge this lane must not produce.
            with _LOCK:
                s2 = _load(sid)
                cur = next((x for x in s2["cues"] if x["i"] == c["i"]), None)
                if cur is not None:
                    cur["status"] = "paper"
                    cur["job_id"] = ""
                _save(s2)
            failed.append({"cue": c["i"], "error": f"{type(e).__name__}: {e}"})

    out = _with_status(_load(sid))
    out["started"] = started
    out["failed"] = failed
    return out


@router.post("/{sid}/cancel")
async def cancel(sid: str):
    """Interrupt whatever this score has in flight, on the boxes it touched."""
    from backend.api import audio_lab as AL
    hit = []
    for c in (_load(sid).get("cues") or []):
        j = AL._JOBS.get(c.get("job_id") or "")
        if j and j.get("status") in ("queued", "running") and j.get("worker"):
            try:
                AL._jpost(f"http://{j['worker']}:8188/interrupt", {}, timeout=15)
            except Exception:                                 # noqa: BLE001
                pass                       # already finished / already gone
            j["status"] = "error"
            j["error"] = "cancelled"
            hit.append(c["i"])
    with AL._LOCK:
        AL._jobs_save()
    return {"cancelled": hit}


class ScoreImportIn(BaseModel):
    project_id: str


@router.post("/{sid}/import")
async def import_score(sid: str, body: ScoreImportIn):
    """Import every FINISHED cue into a project as MUSIC assets, in cue order."""
    from backend.api import audio_lab as AL
    s = _load(sid)
    out, skipped = [], []
    for c in (s.get("cues") or []):
        j = AL._JOBS.get(c.get("job_id") or "")
        if not (j and j.get("status") == "done" and j.get("file")):
            skipped.append(c["i"])
            continue
        r = await AL.import_job_to_project(c["job_id"], body.project_id)
        out.append({"cue": c["i"], "name": c["name"],
                    "rel_path": r.get("asset_rel_path")})
    if not out:
        raise HTTPException(409, "no finished cue to import yet")
    return {"imported": out, "skipped": skipped,
            "note": "imported as MUSIC assets, in cue order — run audio "
                    "analysis in the project to build sections from them"}


# ⚠ declared LAST on purpose: a parameterized GET /{sid} declared above
# /sources or /list would swallow those literals as ids — the v1.277.0 route
# shadowing bug, which cost 7 smoke failures with one cause.
@router.get("/{sid}")
async def get_score(sid: str):
    return _with_status(_load(sid))
