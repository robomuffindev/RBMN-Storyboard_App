"""🌍 The linked story as a project's CREATIVE CONTEXT (v1.277.24).

His ask: *"when we link it, the concept tab needs to behave accordingly and the
story flow and chapters tab as well… it is pulling from what we have in the
paired world and story rather than what we would set on the concept tab."*

**Derived, not copied** (his call). A linked project does not get a snapshot of
the story pasted into `settings["concept_text"]`; it RESOLVES the story every
time it needs the context. Editing the world updates the project. An explicit
per-field override still wins, so a project can disagree with its world without
unlinking.

    resolve(project) → {linked, world, story, concept_text, style_text,
                        characters[], arcs[], overrides{}}

⚠ Everything here reads the storyworld JSON on disk, which has no transaction
with the SQL project rows. Read it ONCE per request and pass the result down —
re-reading per scene inside a render loop is how a lane ends up doing file I/O
per image.

⚠ `settings["story_overrides"]` is the escape hatch: `{"concept_text": "...",
"style_text": "..."}`. A key present there means the user pinned that field and
the story must not overwrite it — which is also what the Concept tab greys out.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

OVERRIDABLE = ("song_title", "concept_text", "style_text", "image_direction")


def _story_of(w: dict, sid: str) -> Optional[dict]:
    for s in (w.get("stories") or []):
        if s.get("id") == sid:
            return s
    return None


def story_cast(w: dict, sid: str) -> List[dict]:
    """The cast of THIS story — members tagged with it, or (if nobody is
    tagged) the whole world cast.

    ⚠ The fallback is deliberate: a single-story world never needs tagging,
    and returning nothing there would look like a broken link rather than an
    untagged cast."""
    cast = w.get("cast") or []
    if not sid:
        return cast
    tagged = [m for m in cast if sid in (m.get("story_ids") or [])]
    return tagged or cast


def _chapter_of(st: Optional[dict], cid: str) -> Optional[dict]:
    for c in ((st or {}).get("chapters") or []):
        if c.get("id") == cid:
            return c
    return None


def resolve(settings: Dict[str, Any]) -> Dict[str, Any]:
    """Resolve a project's creative context from its linked world+story.

    📖 v1.277.46 — the link can now name a CHAPTER (`settings["chapter_id"]`).
    ⭐ **A chapter narrows everything.** When one is selected the project is that
    chapter's video and nothing else: `arcs` becomes the CHAPTER'S BEATS (which
    are arc-shaped, so `arc_context` and the chapter builder are unchanged) and
    the concept text describes that chapter rather than the whole story. Without
    it, the old story-wide behaviour is exactly as it was."""
    out: Dict[str, Any] = {"linked": False, "world": None, "story": None,
                           "chapter": None, "chapter_missing": False,
                           "concept_text": "", "style_text": "",
                           "characters": [], "arcs": [], "arcs_are_beats": False,
                           "overrides": dict(settings.get("story_overrides") or {})}
    wid = str(settings.get("world_id") or "")
    if not wid:
        return out
    try:
        from backend.api import storyworld as sw
        w = sw._load(wid)
    except Exception as e:                                       # noqa: BLE001
        logger.warning("story context: world %s unreadable: %s", wid, e)
        return out
    sid = str(settings.get("story_id") or "")
    cid = str(settings.get("chapter_id") or "")
    st = _story_of(w, sid)
    ch = _chapter_of(st, cid)
    # ⚠ A chapter_id that no longer resolves must be REPORTED. `get_story_link`
    # and `pull_from_story` both say so loudly; if this resolver stayed silent
    # the Concept tab and the flow LLM would quietly widen to the whole story
    # and nothing on screen would explain why the video got ten times longer.
    out["chapter_missing"] = bool(cid and not ch)
    out["linked"] = True
    out["world"] = {"id": w.get("id"), "name": w.get("name")}
    if st:
        out["story"] = {"id": st.get("id"), "title": st.get("title"),
                        "story_type": st.get("story_type"),
                        "chapters": len(st.get("chapters") or [])}
        out["arcs"] = st.get("arcs") or []
    if ch:
        out["chapter"] = {"id": ch.get("id"), "title": ch.get("title"),
                          "i": ch.get("i"), "arc_id": ch.get("arc_id") or "",
                          "summary": ch.get("summary") or "",
                          "mood": ch.get("mood") or "",
                          "beats": len(ch.get("beats") or []),
                          "words": int(ch.get("narration_words") or 0)}
        # ⚠⚠ The beats REPLACE the arcs in this dict on purpose. Everything
        # downstream (arc_context, create_chapters_from_arcs, the backing-bed
        # lane) reads `arcs` and needs no change — a beat is arc-shaped.
        # ⚠ UNCONDITIONALLY, even when the chapter has none. Guarding on
        # `if ch["beats"]` left the WHOLE STORY'S ARCS in place, and the block
        # below then printed them under the heading "Beats:" — so a project
        # scoped to chapter 3 was handed the entire book's spine as if it were
        # that chapter's, and `arc_context` matched project chapters against
        # arcs they were never derived from. `pull_from_story` refuses this
        # case outright; the two halves have to agree.
        out["arcs"] = ch.get("beats") or []
        out["arcs_are_beats"] = True

    bits = []
    ws = w.get("world") or {}
    if ws.get("logline"):
        bits.append(f"World: {ws['logline']}")
    for k in ("genre", "tone", "setting", "time_period"):
        if ws.get(k):
            bits.append(f"{k.replace('_', ' ').capitalize()}: {ws[k]}")
    if st:
        sf = st.get("fields") or {}
        bits.append(f"Story: {st.get('title') or ''}")
        for k in ("logline", "synopsis", "beats", "hook", "ending"):
            if sf.get(k):
                bits.append(f"{k.capitalize()}: {sf[k]}")
    if ch:
        bits.append(f"Chapter {int(ch.get('i') or 0) + 1}: {ch.get('title') or ''}")
        if ch.get("summary"):
            bits.append(f"This chapter: {ch['summary']}")
        if ch.get("mood"):
            bits.append(f"Mood: {ch['mood']}")
        if out["arcs"]:
            bits.append("Beats: " + " | ".join(
                f"{i + 1}. {a.get('title')} — {a.get('summary') or ''}"
                for i, a in enumerate(out["arcs"])))
    elif st and out["arcs"]:
        bits.append("Arcs: " + " | ".join(
            f"{i + 1}. {a.get('title')} — {a.get('summary') or ''}"
            for i, a in enumerate(out["arcs"])))
    # 📚 the codex, when there is one — established canon the generators must
    # not contradict, and (for a continuing series) what has already happened to
    # the characters in this scene. Scoped so a big world cannot crowd out the
    # actual ask. Never fatal: a codex is an enrichment, not a dependency.
    try:
        from backend.api.storycodex import character_brief, codex_brief
        cb = codex_brief(w, sid)
        if cb:
            bits.append(cb)
        # the character pages are scoped to whoever this CHAPTER names, falling
        # back to the story's cast — the same narrowing the cast pull uses.
        who = list((ch or {}).get("characters") or [])
        if not who:
            who = [str(m.get("name") or "") for m in story_cast(w, sid)]
        hb = character_brief(w, who)
        if hb:
            bits.append(hb)
    except Exception as e:                                       # noqa: BLE001
        logger.debug("story context: codex brief unavailable: %s", e)
    out["concept_text"] = "\n\n".join(b for b in bits if b)

    try:
        from backend.api import storyworld as sw2
        style = sw2._style_text(w)
    except Exception:                                            # noqa: BLE001
        style = ""
    vs = ws.get("visual_style") or ""
    out["style_text"] = ". ".join(x for x in (style, vs) if x and x not in style)
    out["characters"] = story_cast(w, sid)
    if ch and (ch.get("characters") or []):
        # a chapter naming its cast narrows the pull further; unnamed falls
        # back to the story's cast rather than to nobody.
        want = {str(n).strip().lower() for n in ch["characters"]}
        narrowed = [m for m in out["characters"]
                    if str(m.get("name") or "").strip().lower() in want]
        out["characters"] = narrowed or out["characters"]
    return out


def effective(settings: Dict[str, Any], ctx: Optional[Dict[str, Any]] = None
              ) -> Dict[str, str]:
    """What the generators should actually use for the text fields.

    Precedence: an explicit override > the linked story > whatever the project
    had before it was linked. Returning the SAME KEYS the ~25 existing call
    sites already read (`concept_text` / `style_text` / …) is deliberate —
    nothing downstream has to learn a new name for this to take effect."""
    ctx = ctx if ctx is not None else resolve(settings)
    ov = ctx.get("overrides") or {}
    out: Dict[str, str] = {}
    for k in OVERRIDABLE:
        if ov.get(k):
            out[k] = str(ov[k])
        elif ctx.get("linked") and ctx.get(k):
            out[k] = str(ctx[k])
        else:
            out[k] = str(settings.get(k) or "")
    if ctx.get("linked") and not out.get("song_title"):
        # a chapter-scoped project is named for the CHAPTER — the story title
        # would put the same name on every video in the series.
        if ctx.get("chapter"):
            out["song_title"] = str((ctx["chapter"] or {}).get("title") or "")
        if not out["song_title"] and ctx.get("story"):
            out["song_title"] = str((ctx["story"] or {}).get("title") or "")
    return out


def arc_context(ctx: Dict[str, Any], chapter_metadata: Optional[dict] = None,
                chapter_name: str = "") -> str:
    """The ARC block for a chapter — what the flow LLM is given per chapter.

    Matched by `arc_id` first (chapters carry it in `chapter_metadata`), then by
    name, because a renamed chapter should still find its arc.

    📖 v1.277.46 — when the project is scoped to a STORY CHAPTER, `ctx["arcs"]`
    holds that chapter's BEATS instead of the story's arcs. This function does
    not care: a beat is arc-shaped and carries the same `id`, which is what
    `chapter_metadata["arc_id"]` was written from. The label below says "story
    arc" either way, and that is accurate at the rung it is describing."""
    arcs = ctx.get("arcs") or []
    if not arcs:
        return ""
    arc = None
    aid = (chapter_metadata or {}).get("arc_id")
    if aid:
        arc = next((a for a in arcs if a.get("id") == aid), None)
    if arc is None and chapter_name:
        arc = next((a for a in arcs
                    if (a.get("title") or "").lower() == chapter_name.lower()),
                   None)
    if arc is None:
        return ""
    bits = [f"THIS CHAPTER IS THE STORY ARC: {arc.get('title')}"]
    if arc.get("summary"):
        bits.append(f"What happens: {arc['summary']}")
    if arc.get("mood"):
        bits.append(f"Mood: {arc['mood']}")
    if arc.get("characters"):
        bits.append(f"Present: {', '.join(arc['characters'])}")
    if arc.get("locations"):
        bits.append(f"Where: {', '.join(arc['locations'])}")
    return "\n".join(bits)
