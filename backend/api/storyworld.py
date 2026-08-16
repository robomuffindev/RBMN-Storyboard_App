"""🌍 Story / World Builder — the narrative layer above characters and projects.

v1.277.0. One WORLD holds the setting/lore, any number of STORIES inside it
(a music video and a narration can be two stories in one world), a shared CAST,
and TEXTS (lyrics / narrations / scripts brought in from outside the app).

Design decisions (Lorenzo, 2026-08-13):
  * World CONTAINS stories — the cast is shared at world level.
  * The mode lives on the home page as its own destination (/worlds).
  * LLM cast generation: the LLM decides how many characters the story needs,
    capped by a per-run max; the user reviews before anything renders.
  * LoRA training is PER ITEM inside each character's chain (the do_lora
    toggle), never an end-of-queue batch.

How it feeds the pipeline: cast members are PAPER characters (name, role,
fields, lore, outfits) until submitted. Submission builds AutogenSpec objects
and calls autogen._enqueue DIRECTLY — a same-process function call, not an
HTTP self-call, so the v1.276.41 event-loop deadlock class cannot apply here.
The autogen serial queue is the primitive (v1.276.54): strictly serial across
characters, parallel inside one.

Storage: one JSON per world under <project_dir>/_libraries/storyworld/worlds/.
⚠ _ROOT is an IMPORT-TIME constant (the cfg.project_dir DB-override gotcha —
same as costumes.py / klein3.py; see autogen.py:81).

LLM calls reuse concept._call_llm (provider-agnostic: ollama pool / openai /
anthropic / gemini) resolved via settings.resolve_llm_config, with an optional
per-call override so the user can pick WHICH brain enhances a given thing.
_call_llm BLOCKS — always via asyncio.to_thread from these async routes.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4

from fastapi import (APIRouter, Depends, File, HTTPException, Request,
                     UploadFile)
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from backend.config import settings as cfg
from backend.database.database import get_session
from backend.database.models import AppSettings, Project

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/storyworld", tags=["storyworld"])

_ROOT = Path(cfg.project_dir) / "_libraries" / "storyworld"
_WORLD_DIR = _ROOT / "worlds"
_LOCK = threading.Lock()          # world files are read-modify-write

# ── vocabulary ───────────────────────────────────────────────────────────────
# Field metadata is SERVER-DRIVEN (the costume-slots precedent): the frontend
# renders whatever this lists, so adding a field is a one-line change here.
_WORLD_FIELDS: List[Dict[str, str]] = [
    {"key": "logline",      "label": "Logline",       "hint": "the world in one or two sentences"},
    {"key": "genre",        "label": "Genre",         "hint": "genre and subgenre"},
    {"key": "tone",         "label": "Tone",          "hint": "tone and mood"},
    {"key": "setting",      "label": "Setting",       "hint": "where it all happens"},
    {"key": "time_period",  "label": "Time period",   "hint": "era and technology level"},
    {"key": "rules",        "label": "World rules",   "hint": "how the world works — magic, tech, powers, physics"},
    {"key": "factions",     "label": "Factions",      "hint": "groups and powers in play"},
    {"key": "locations",    "label": "Locations",     "hint": "key places stories visit"},
    {"key": "history",      "label": "History",       "hint": "what happened before the stories start"},
    {"key": "culture",      "label": "Culture",       "hint": "society, daily life, customs"},
    {"key": "themes",       "label": "Themes",        "hint": "what it is really about"},
    {"key": "visual_style", "label": "Visual style",  "hint": "palette, lighting, art direction"},
    {"key": "notes",        "label": "Notes",         "hint": "anything else"},
]
_STORY_FIELDS: List[Dict[str, str]] = [
    {"key": "logline",    "label": "Logline",    "hint": "the story in one sentence"},
    {"key": "synopsis",   "label": "Synopsis",   "hint": "the full story, beginning to end"},
    {"key": "beats",      "label": "Beats",      "hint": "structure: acts, beats or scene list"},
    {"key": "hook",       "label": "Hook",       "hint": "the opening"},
    {"key": "ending",     "label": "Ending",     "hint": "how it lands"},
    {"key": "cast_focus", "label": "Cast focus", "hint": "which characters carry it"},
    {"key": "locations",  "label": "Locations",  "hint": "where this story takes place"},
    {"key": "notes",      "label": "Notes",      "hint": "anything else"},
]
_STORY_TYPES = ["music_video", "narration", "short_film", "series", "other"]

# klein3's physical description sheet — MUST stay in step with klein3._FIELD_ORDER.
_CAST_FIELD_KEYS = ["age", "sex", "race", "skin_color", "hair", "eyes", "face",
                    "body", "height", "aesthetics", "additional_details"]
_CAST_LORE_FIELDS: List[Dict[str, str]] = [
    {"key": "story_role",    "label": "Story role",    "hint": "what they are FOR in the story"},
    {"key": "backstory",     "label": "Backstory",     "hint": "where they come from"},
    {"key": "personality",   "label": "Personality",   "hint": "how they behave"},
    {"key": "motivations",   "label": "Motivations",   "hint": "what they want and why"},
    {"key": "relationships", "label": "Relationships", "hint": "ties to the rest of the cast"},
    {"key": "arc",           "label": "Arc",           "hint": "how they change"},
    {"key": "voice",         "label": "Voice",         "hint": "how they speak"},
    {"key": "notes",         "label": "Notes",         "hint": "anything else"},
]
_TEXT_KINDS = ["lyrics", "narration", "script", "poem", "notes"]
_IMPORTANCE = ["lead", "support", "background"]

# 🎨 visual-style presets — the common looks the models handle well, plus
# custom. The prompt is what the style CONTRIBUTES to image prompts and to
# every LLM call's context.
_STYLE_PRESETS: List[Dict[str, str]] = [
    {"key": "anime",     "label": "Anime",
     "prompt": "vibrant Japanese anime style, clean lineart, cel shading, "
               "expressive eyes, detailed painted backgrounds"},
    {"key": "manga",     "label": "Manga (B&W)",
     "prompt": "black-and-white Japanese manga style, ink lineart, screentone "
               "shading, dramatic composition"},
    {"key": "photorealistic", "label": "Photorealistic",
     "prompt": "photorealistic, natural lighting, shallow depth of field, "
               "shot on a full-frame camera, high detail"},
    {"key": "cartoon",   "label": "Western cartoon",
     "prompt": "western animated cartoon style, bold outlines, flat saturated "
               "colours, exaggerated shapes"},
    {"key": "comic",     "label": "Comic book",
     "prompt": "American comic book style, inked lines, halftone shading, "
               "dynamic composition, bold colour palette"},
    {"key": "watercolor", "label": "Watercolour",
     "prompt": "soft watercolour painting, loose washes, paper texture, "
               "gentle gradients"},
    {"key": "oil",       "label": "Oil painting",
     "prompt": "classical oil painting, visible brushwork, rich colour depth, "
               "painterly light"},
    {"key": "pixel",     "label": "Pixel art",
     "prompt": "retro pixel art, limited palette, crisp pixels, 16-bit "
               "videogame aesthetic"},
    {"key": "cgi",       "label": "3D render",
     "prompt": "polished 3D CGI render, physically-based materials, cinematic "
               "lighting"},
    {"key": "cinematic", "label": "Cinematic film",
     "prompt": "cinematic film still, anamorphic framing, moody colour grade, "
               "volumetric light"},
    {"key": "custom",    "label": "Custom", "prompt": ""},
]
_STYLE_REF_DIR = _ROOT / "style_refs"
_SAMPLE_DIR = _ROOT / "samples"
_STYLE_JOBS: Dict[str, dict] = {}        # wid → live sample-render status
_SAMPLE_MODELS = ("krea2", "z_image", "anima", "klein")

# submission level → explicit autogen toggles. "details" is stages=[character]
# only: the klein3 record + fields are written, zero renders.
_LEVELS: Dict[str, Dict[str, bool]] = {
    "details":  {},
    "base":     {"do_base": True},
    "views":    {"do_base": True, "do_views": True},
    "clothing": {"do_base": True, "do_views": True, "do_clothing": True},
    "sheet":    {"do_base": True, "do_views": True, "do_clothing": True,
                 "do_charsheet": True},
    "dataset":  {"do_base": True, "do_views": True, "do_clothing": True,
                 "do_charsheet": True, "do_dataset": True},
    "lora":     {"do_base": True, "do_views": True, "do_clothing": True,
                 "do_charsheet": True, "do_dataset": True, "do_lora": True},
}
_TOGGLE_KEYS = ["do_base", "do_views", "do_clothing", "do_charsheet",
                "do_dataset", "do_lora"]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── storage ──────────────────────────────────────────────────────────────────
def _fp(wid: str) -> Path:
    return _WORLD_DIR / f"{wid}.json"


def _load(wid: str) -> dict:
    try:
        return json.loads(_fp(wid).read_text("utf-8"))
    except FileNotFoundError:
        raise HTTPException(404, f"world {wid!r} not found")
    except Exception as e:                                    # noqa: BLE001
        raise HTTPException(500, f"world {wid!r} unreadable: {e}")


def _save(w: dict) -> None:
    _WORLD_DIR.mkdir(parents=True, exist_ok=True)
    w["updated_at"] = _now()
    fp = _fp(w["id"])
    # unique temp + retry: os.replace fails on Windows while a READER has the
    # target open (the v1.276.43 lesson, latent in lora_train._state_save).
    tmp = fp.with_name(f"{fp.stem}.{uuid4().hex[:6]}.tmp")
    tmp.write_text(json.dumps(w, indent=2), "utf-8")
    # ⚠ capped LOW: this retry runs while _LOCK is held inside async routes,
    # so its worst case is an event-loop stall for the whole server. ~0.7s max.
    for i in range(8):
        try:
            tmp.replace(fp)
            return
        except PermissionError:
            import time as _t
            _t.sleep(0.02 * (i + 1))
    tmp.replace(fp)               # last try, let it raise


def _all_worlds() -> List[dict]:
    if not _WORLD_DIR.is_dir():
        return []
    out = []
    for fp in _WORLD_DIR.glob("*.json"):
        try:
            out.append(json.loads(fp.read_text("utf-8")))
        except Exception:                                     # noqa: BLE001
            continue
    out.sort(key=lambda w: w.get("updated_at") or "", reverse=True)
    return out


def _light(w: dict) -> dict:
    return {"id": w["id"], "name": w.get("name") or "",
            "logline": (w.get("world") or {}).get("logline") or "",
            "stories": len(w.get("stories") or []),
            "cast": len(w.get("cast") or []),
            "texts": len(w.get("texts") or []),
            "project_ids": w.get("project_ids") or [],
            "updated_at": w.get("updated_at")}


def _find(items: List[dict], iid: str, what: str) -> dict:
    for it in items:
        if it.get("id") == iid:
            return it
    raise HTTPException(404, f"{what} {iid!r} not found")


# ── sanitising what an LLM hands back ────────────────────────────────────────
def _flat(v: Any, cap: int = 4000) -> str:
    """One plain string out of whatever shape the model chose."""
    if v is None:
        return ""
    if isinstance(v, (list, tuple)):
        v = ", ".join(_flat(x, cap) for x in v if x is not None)
    elif isinstance(v, dict):
        v = "; ".join(f"{k}: {_flat(x, cap)}" for k, x in v.items())
    s = str(v).strip()
    if s.lower() in ("none", "n/a", "null", "unknown", "nothing", "-"):
        return ""
    return s[:cap]


def _json_slice(text: str, open_ch: str, close_ch: str) -> Any:
    # qwen3 (the default local model) prefixes <think>…</think>, and any brace
    # inside the reasoning would make the first-{/last-} slice span garbage.
    t = re.sub(r"<think>.*?</think>", "", text or "", flags=re.DOTALL)
    t = re.sub(r"```(?:json)?", "", t).strip()
    a, b = t.find(open_ch), t.rfind(close_ch)
    if a < 0 or b <= a:
        raise ValueError(f"no JSON {open_ch}...{close_ch} in the reply")
    return json.loads(t[a:b + 1])


def _json_obj(text: str) -> dict:
    d = _json_slice(text, "{", "}")
    if not isinstance(d, dict):
        raise ValueError("reply was JSON but not an object")
    return d


def _json_arr(text: str) -> list:
    d = _json_slice(text, "[", "]")
    if not isinstance(d, list):
        raise ValueError("reply was JSON but not an array")
    return d


# ── the LLM ──────────────────────────────────────────────────────────────────
class LlmPick(BaseModel):
    provider: str = ""               # '' = whatever Settings resolves
    model: str = ""


async def _settings_row(session: AsyncSession) -> AppSettings:
    r = await session.execute(select(AppSettings).where(AppSettings.id == 1))
    s = r.scalars().first()
    if not s:
        raise HTTPException(400, "App settings not configured")
    return s


async def _llm_cfg(session: AsyncSession,
                   pick: Optional[LlmPick]) -> tuple[str, str, str]:
    """(provider, api_key, model) — the user's per-call pick beats Settings."""
    from backend.api.settings import _get_ollama_urls, resolve_llm_config
    s = await _settings_row(session)
    if pick and (pick.provider or "").strip():
        p = pick.provider.strip()
        m = (pick.model or "").strip()
        if p == "ollama":
            urls = _get_ollama_urls(s)
            if not urls:
                raise HTTPException(400, "no Ollama server configured in Settings")
            payload = json.dumps(urls) if len(urls) > 1 else urls[0]
            return "ollama", payload, m or s.ollama_model or "qwen3:14b"
        keys = {"openai": (s.openai_api_key, s.openai_model, "gpt-4o"),
                "anthropic": (s.anthropic_api_key, s.anthropic_model,
                              "claude-sonnet-4-20250514"),
                "gemini": (s.gemini_api_key, s.gemini_model, "gemini-2.0-flash")}
        if p not in keys:
            raise HTTPException(400, f"unknown LLM provider {p!r}")
        key, dm, fb = keys[p]
        if not key:
            raise HTTPException(400, f"no API key for {p} in Settings")
        return p, key, m or dm or fb
    return resolve_llm_config(s)


async def _ask_json(session: AsyncSession, pick: Optional[LlmPick],
                    system: str, user: str, want: str = "object",
                    max_tokens: int = 3000, timeout_s: float = 600.0) -> Any:
    from backend.api.concept import _call_llm
    provider, key, model = await _llm_cfg(session, pick)
    try:
        txt = await asyncio.wait_for(
            asyncio.to_thread(_call_llm, provider, key, model,
                              system, user, max_tokens),
            timeout=timeout_s)
    except asyncio.TimeoutError:
        raise HTTPException(504, f"{provider}/{model} took longer than "
                                 f"{int(timeout_s)}s")
    except HTTPException:
        raise
    except Exception as e:                                    # noqa: BLE001
        raise HTTPException(502, f"{provider}/{model} failed: {e}")
    try:
        return (_json_arr if want == "array" else _json_obj)(txt)
    except Exception as e:                                    # noqa: BLE001
        raise HTTPException(502, f"{provider}/{model} answered but not with "
                                 f"usable JSON: {e} — raw starts: {txt[:200]!r}")


def _style_text(w: dict) -> str:
    """The world's authoritative visual-style sentence, from preset / custom
    text / the vision-scanned style reference — whichever exist, joined."""
    s = w.get("style") or {}
    bits = []
    preset = s.get("preset") or ""
    if preset and preset != "custom":
        p = next((x["prompt"] for x in _STYLE_PRESETS if x["key"] == preset), "")
        if p:
            bits.append(p)
    if (s.get("custom_text") or "").strip():
        bits.append(s["custom_text"].strip())
    if (s.get("ref_description") or "").strip():
        bits.append(s["ref_description"].strip())
    return ". ".join(bits)


def _ctx_world(w: dict) -> str:
    filled = {f["key"]: v for f in _WORLD_FIELDS
              if (v := (w.get("world") or {}).get(f["key"], "").strip())}
    lines = [f"WORLD NAME: {w.get('name') or 'untitled'}"]
    stx = _style_text(w)
    if stx:
        # the chosen style shapes EVERYTHING the LLM writes for this world
        lines.append(f"VISUAL STYLE (authoritative — write for this medium): {stx}")
    for k, v in filled.items():
        lines.append(f"{k}: {v}")
    return "\n".join(lines)


def _ctx_story(st: dict) -> str:
    lines = [f"STORY: {st.get('title') or 'untitled'} "
             f"(type: {st.get('story_type') or 'unspecified'})"]
    for f in _STORY_FIELDS:
        v = (st.get("fields") or {}).get(f["key"], "").strip()
        if v:
            lines.append(f"{f['key']}: {v}")
    return "\n".join(lines)


_JSON_RULES = (
    "Return ONLY a JSON object — no prose before or after, no markdown. "
    "Every value must be a plain-text string (no nested objects or arrays). "
    "Be concrete and specific: names, places, sensory detail. Never use "
    "franchise, brand or real-celebrity names. Keep each value coherent "
    "with everything in the provided context."
)


# ══ worlds CRUD ══════════════════════════════════════════════════════════════
class WorldIn(BaseModel):
    name: str


@router.get("/meta")
async def meta():
    """Server-driven vocab — the frontend renders what this lists."""
    return {"world_fields": _WORLD_FIELDS, "story_fields": _STORY_FIELDS,
            "story_types": _STORY_TYPES, "cast_field_keys": _CAST_FIELD_KEYS,
            "cast_lore_fields": _CAST_LORE_FIELDS, "text_kinds": _TEXT_KINDS,
            "importance": _IMPORTANCE, "levels": list(_LEVELS),
            "style_presets": _STYLE_PRESETS,
            "sample_models": list(_SAMPLE_MODELS)}


@router.get("/llms")
async def llms(session: AsyncSession = Depends(get_session)):
    """What brains are available, for the per-task picker."""
    s = await _settings_row(session)
    from backend.api.settings import _get_ollama_urls
    opts = []
    urls = _get_ollama_urls(s)
    opts.append({"provider": "ollama", "configured": bool(urls),
                 "models": list(s.ollama_available_models or []),
                 "default_model": s.ollama_model or ""})
    for p, key, dm in (("openai", s.openai_api_key, s.openai_model),
                       ("anthropic", s.anthropic_api_key, s.anthropic_model),
                       ("gemini", s.gemini_api_key, s.gemini_model)):
        opts.append({"provider": p, "configured": bool(key),
                     "models": [], "default_model": dm or ""})
    return {"default_provider": s.default_llm_provider or "",
            "options": opts}


@router.get("/worlds")
async def list_worlds():
    return {"worlds": [_light(w) for w in _all_worlds()]}


@router.post("/worlds")
async def create_world(body: WorldIn):
    name = (body.name or "").strip()
    if not name:
        raise HTTPException(400, "the world needs a name")
    w = {"id": uuid4().hex[:8], "name": name, "created_at": _now(),
         "updated_at": _now(), "world": {}, "stories": [], "cast": [],
         "texts": [], "project_ids": [], "llm": {"provider": "", "model": ""}}
    with _LOCK:
        _save(w)
    return w


@router.get("/worlds/{wid}")
async def get_world(wid: str):
    return _load(wid)


@router.post("/worlds/{wid}/delete")
async def delete_world(wid: str):
    with _LOCK:
        _load(wid)                # 404 before delete
        _fp(wid).unlink(missing_ok=True)
    return {"deleted": wid}


class RenameIn(BaseModel):
    name: str


@router.post("/worlds/{wid}/rename")
async def rename_world(wid: str, body: RenameIn):
    if not (body.name or "").strip():
        raise HTTPException(400, "a name is required")
    with _LOCK:
        w = _load(wid)
        w["name"] = body.name.strip()
        _save(w)
    return {"ok": True, "name": w["name"]}


class FieldsIn(BaseModel):
    fields: Dict[str, Any] = {}


@router.post("/worlds/{wid}/world")
async def update_world_fields(wid: str, body: FieldsIn):
    """MERGE into the world sheet. An explicit '' clears a field —
    unlike klein3's /fields this never silently wipes what it wasn't sent."""
    keys = {f["key"] for f in _WORLD_FIELDS}
    with _LOCK:
        w = _load(wid)
        sheet = w.setdefault("world", {})
        for k, v in (body.fields or {}).items():
            if k in keys:
                sheet[k] = _flat(v)
        _save(w)
    return {"world": w["world"]}


@router.post("/worlds/{wid}/llm")
async def set_world_llm(wid: str, pick: LlmPick):
    with _LOCK:
        w = _load(wid)
        w["llm"] = {"provider": pick.provider or "", "model": pick.model or ""}
        _save(w)
    return {"llm": w["llm"]}


# ══ stories ══════════════════════════════════════════════════════════════════
class StoryIn(BaseModel):
    title: str = ""
    story_type: str = "music_video"
    fields: Dict[str, Any] = {}


@router.post("/worlds/{wid}/stories")
async def add_story(wid: str, body: StoryIn):
    if not (body.title or "").strip():
        raise HTTPException(400, "the story needs a title")
    st = {"id": uuid4().hex[:8], "title": body.title.strip(),
          "story_type": body.story_type if body.story_type in _STORY_TYPES
          else "other",
          "fields": {f["key"]: _flat(v) for f in _STORY_FIELDS
                     if (v := (body.fields or {}).get(f["key"])) is not None},
          "created_at": _now(), "updated_at": _now()}
    with _LOCK:
        w = _load(wid)
        w.setdefault("stories", []).append(st)
        _save(w)
    return st


@router.post("/worlds/{wid}/stories/{sid}")
async def update_story(wid: str, sid: str, body: StoryIn):
    keys = {f["key"] for f in _STORY_FIELDS}
    with _LOCK:
        w = _load(wid)
        st = _find(w.get("stories") or [], sid, "story")
        if (body.title or "").strip():
            st["title"] = body.title.strip()
        if body.story_type in _STORY_TYPES:
            st["story_type"] = body.story_type
        for k, v in (body.fields or {}).items():
            if k in keys:
                st.setdefault("fields", {})[k] = _flat(v)
        st["updated_at"] = _now()
        _save(w)
    return st


@router.post("/worlds/{wid}/stories/{sid}/delete")
async def delete_story(wid: str, sid: str):
    with _LOCK:
        w = _load(wid)
        stories = w.get("stories") or []
        _find(stories, sid, "story")
        w["stories"] = [s for s in stories if s["id"] != sid]
        for t in w.get("texts") or []:          # orphaned texts stay, unlinked
            if t.get("story_id") == sid:
                t["story_id"] = ""
        _save(w)
    return {"deleted": sid}


# ══ texts (lyrics / narrations / scripts) ════════════════════════════════════
class TextIn(BaseModel):
    kind: str = "lyrics"
    title: str = ""
    body: str = ""
    story_id: str = ""


@router.post("/worlds/{wid}/texts")
async def add_text(wid: str, body: TextIn):
    if not (body.title or "").strip():
        raise HTTPException(400, "the text needs a title")
    t = {"id": uuid4().hex[:8],
         "kind": body.kind if body.kind in _TEXT_KINDS else "notes",
         "title": body.title.strip(), "body": body.body or "",
         "story_id": body.story_id or "",
         "created_at": _now(), "updated_at": _now()}
    with _LOCK:
        w = _load(wid)
        if t["story_id"]:
            _find(w.get("stories") or [], t["story_id"], "story")
        w.setdefault("texts", []).append(t)
        _save(w)
    return t


@router.post("/worlds/{wid}/texts/{tid}")
async def update_text(wid: str, tid: str, body: TextIn):
    with _LOCK:
        w = _load(wid)
        t = _find(w.get("texts") or [], tid, "text")
        if (body.title or "").strip():
            t["title"] = body.title.strip()
        if body.kind in _TEXT_KINDS:
            t["kind"] = body.kind
        t["body"] = body.body or ""
        if body.story_id:
            _find(w.get("stories") or [], body.story_id, "story")
        t["story_id"] = body.story_id or ""
        t["updated_at"] = _now()
        _save(w)
    return t


@router.post("/worlds/{wid}/texts/{tid}/delete")
async def delete_text(wid: str, tid: str):
    with _LOCK:
        w = _load(wid)
        _find(w.get("texts") or [], tid, "text")
        w["texts"] = [t for t in w["texts"] if t["id"] != tid]
        _save(w)
    return {"deleted": tid}


# ══ projects ═════════════════════════════════════════════════════════════════
@router.get("/projects")
async def all_projects(session: AsyncSession = Depends(get_session)):
    """Every project, for the attach picker."""
    r = await session.execute(select(Project))
    rows = r.scalars().all()
    return {"projects": [{"id": str(p.id), "name": p.name,
                          "mode": getattr(p.mode, "value", str(p.mode))}
                         for p in rows]}


class ProjectLinkIn(BaseModel):
    project_id: str
    attach: bool = True


@router.post("/worlds/{wid}/projects")
async def link_project(wid: str, body: ProjectLinkIn,
                       session: AsyncSession = Depends(get_session)):
    """⚠ TWO-WAY since v1.277.12: the project's settings carry world_id too —
    style injection, pull-from-story and the Engine & Story header all key off
    project.settings.world_id, so a world-side attach that only wrote
    world.project_ids left the whole machinery inert (reviewer finding #1)."""
    pid = (body.project_id or "").strip()
    if not pid:
        raise HTTPException(400, "project_id is required")
    proj = None
    if body.attach:
        r = await session.execute(select(Project))
        proj = next((p for p in r.scalars().all() if str(p.id) == pid), None)
        if proj is None:
            raise HTTPException(404, f"project {pid!r} not found")
    with _LOCK:
        w = _load(wid)
        ids = [str(x) for x in (w.get("project_ids") or [])]
        if body.attach and pid not in ids:
            ids.append(pid)
        if not body.attach:
            ids = [x for x in ids if x != pid]
        w["project_ids"] = ids
        _save(w)
    # write the project-side half of the link
    try:
        if body.attach and proj is not None:
            pst = dict(proj.settings or {})
            pst["world_id"] = wid
            pst.setdefault("story_id", "")
            proj.settings = pst
            await session.commit()
        elif not body.attach:
            r = await session.execute(select(Project))
            proj = next((p for p in r.scalars().all() if str(p.id) == pid), None)
            if proj is not None and str((proj.settings or {}).get("world_id")) == wid:
                pst = dict(proj.settings or {})
                pst.pop("world_id", None)
                pst.pop("story_id", None)
                proj.settings = pst
                await session.commit()
    except Exception as e:                                       # noqa: BLE001
        logger.warning("storyworld link: project-side write failed: %s", e)
    return {"project_ids": w["project_ids"]}


# ══ cast CRUD ════════════════════════════════════════════════════════════════
class MemberIn(BaseModel):
    name: str = ""
    role: str = ""
    importance: str = ""
    fields: Dict[str, Any] = {}
    lore: Dict[str, Any] = {}
    outfits: Optional[List[Dict[str, str]]] = None
    story_ids: Optional[List[str]] = None


def _clean_member_bits(m: dict, body: MemberIn) -> None:
    if (body.name or "").strip():
        m["name"] = body.name.strip()
    if body.role or m.get("role") is None:
        m["role"] = _flat(body.role, 300)
    if body.importance in _IMPORTANCE:
        m["importance"] = body.importance
    for k, v in (body.fields or {}).items():
        if k in _CAST_FIELD_KEYS:
            m.setdefault("fields", {})[k] = _flat(v, 600)
    lore_keys = {f["key"] for f in _CAST_LORE_FIELDS}
    for k, v in (body.lore or {}).items():
        if k in lore_keys:
            m.setdefault("lore", {})[k] = _flat(v)
    if body.outfits is not None:
        m["outfits"] = [{"name": _flat(o.get("name"), 120) or "outfit",
                         "description": _flat(o.get("description"), 800)}
                        for o in body.outfits
                        if _flat(o.get("description"), 800)]
    if body.story_ids is not None:
        m["story_ids"] = [s for s in body.story_ids if s]


@router.post("/worlds/{wid}/cast")
async def add_member(wid: str, body: MemberIn):
    if not (body.name or "").strip():
        raise HTTPException(400, "the character needs a name")
    m = {"id": uuid4().hex[:8], "name": "", "role": "", "importance": "support",
         "fields": {}, "lore": {}, "outfits": [], "story_ids": [],
         "status": "paper", "char_slug": "", "autogen_job_id": "",
         "created_at": _now(), "updated_at": _now()}
    _clean_member_bits(m, body)
    with _LOCK:
        w = _load(wid)
        names = {c["name"].lower() for c in w.get("cast") or []}
        if m["name"].lower() in names:
            raise HTTPException(409, f"{m['name']!r} is already in the cast")
        w.setdefault("cast", []).append(m)
        _save(w)
    return m


@router.post("/worlds/{wid}/cast/{cid}/delete")
async def delete_member(wid: str, cid: str):
    """Removes the PAPER record only — a generated klein3 character stays."""
    with _LOCK:
        w = _load(wid)
        _find(w.get("cast") or [], cid, "cast member")
        w["cast"] = [c for c in w["cast"] if c["id"] != cid]
        _save(w)
    return {"deleted": cid}


# ══ LLM: enhance ═════════════════════════════════════════════════════════════
class EnhanceIn(BaseModel):
    mode: str = "fill"               # fill (empty only) | overwrite (all)
    direction: str = ""              # optional steer
    llm: Optional[LlmPick] = None


def _fill_keys(sheet: dict, fields: List[Dict[str, str]], mode: str) -> list:
    if mode == "overwrite":
        return [f["key"] for f in fields]
    return [f["key"] for f in fields if not (sheet.get(f["key"]) or "").strip()]


@router.post("/worlds/{wid}/enhance/world")
async def enhance_world(wid: str, body: EnhanceIn,
                        session: AsyncSession = Depends(get_session)):
    w = _load(wid)
    sheet = w.get("world") or {}
    keys = _fill_keys(sheet, _WORLD_FIELDS, body.mode)
    if not keys:
        return {"world": sheet, "changed": [],
                "note": "nothing empty — use overwrite to redo everything"}
    hints = {f["key"]: f["hint"] for f in _WORLD_FIELDS if f["key"] in keys}
    system = ("You are a worldbuilding assistant for a visual-story studio "
              "that makes music videos and narrated films. " + _JSON_RULES)
    user = (f"{_ctx_world(w)}\n\n"
            + (f"DIRECTION FROM THE AUTHOR: {body.direction}\n\n"
               if body.direction.strip() else "")
            + "Write the following keys (each hint says what it is):\n"
            + "\n".join(f"- {k}: {h}" for k, h in hints.items())
            + "\n\nKeys like 'history' and 'synopsis' may run several "
              "sentences; the rest should be 1-3 sentences. Return exactly "
              "these keys and no others.")
    got = await _ask_json(session, body.llm or _pick_of(w), system, user)
    changed = []
    with _LOCK:
        w = _load(wid)
        sheet = w.setdefault("world", {})
        for k in keys:
            v = _flat(got.get(k))
            # ⚠ re-check emptiness UNDER the lock: the user may have typed into
            # this field during the LLM call, and "fill" must never eat that.
            if v and (body.mode == "overwrite"
                      or not (sheet.get(k) or "").strip()):
                sheet[k] = v
                changed.append(k)
        _save(w)
    return {"world": sheet, "changed": changed}


def _pick_of(w: dict) -> Optional[LlmPick]:
    p = w.get("llm") or {}
    if (p.get("provider") or "").strip():
        return LlmPick(provider=p["provider"], model=p.get("model") or "")
    return None


@router.post("/worlds/{wid}/enhance/story/{sid}")
async def enhance_story(wid: str, sid: str, body: EnhanceIn,
                        session: AsyncSession = Depends(get_session)):
    w = _load(wid)
    st = _find(w.get("stories") or [], sid, "story")
    sheet = st.get("fields") or {}
    keys = _fill_keys(sheet, _STORY_FIELDS, body.mode)
    if not keys:
        return {"fields": sheet, "changed": [],
                "note": "nothing empty — use overwrite to redo everything"}
    hints = {f["key"]: f["hint"] for f in _STORY_FIELDS if f["key"] in keys}
    cast_line = ", ".join(f"{c['name']} ({c.get('role') or 'unknown role'})"
                          for c in (w.get("cast") or [])[:30])
    system = ("You are a story developer for a visual-story studio. A story "
              "lives INSIDE the world described below and must contradict "
              "nothing in it. " + _JSON_RULES)
    user = (f"{_ctx_world(w)}\n\n{_ctx_story(st)}\n"
            + (f"EXISTING CAST: {cast_line}\n" if cast_line else "")
            + (f"\nDIRECTION FROM THE AUTHOR: {body.direction}\n"
               if body.direction.strip() else "")
            + "\nWrite the following keys:\n"
            + "\n".join(f"- {k}: {h}" for k, h in hints.items())
            + "\n\n'synopsis' and 'beats' may run long; the rest 1-3 "
              "sentences. Return exactly these keys and no others.")
    got = await _ask_json(session, body.llm or _pick_of(w), system, user,
                          max_tokens=4000)
    changed = []
    with _LOCK:
        w = _load(wid)
        st = _find(w.get("stories") or [], sid, "story")
        sheet = st.setdefault("fields", {})
        for k in keys:
            v = _flat(got.get(k), 8000)
            # fill must not eat text typed during the LLM call (see above)
            if v and (body.mode == "overwrite"
                      or not (sheet.get(k) or "").strip()):
                sheet[k] = v
                changed.append(k)
        st["updated_at"] = _now()
        _save(w)
    return {"fields": sheet, "changed": changed}


@router.post("/worlds/{wid}/enhance/cast/{cid}")
async def enhance_member(wid: str, cid: str, body: EnhanceIn,
                         session: AsyncSession = Depends(get_session)):
    """Fill a cast member's physical sheet + lore in one call."""
    w = _load(wid)
    m = _find(w.get("cast") or [], cid, "cast member")
    lore_meta = [{"key": f["key"], "hint": f["hint"]}
                 for f in _CAST_LORE_FIELDS]
    if body.mode == "overwrite":
        f_keys = list(_CAST_FIELD_KEYS)
        l_keys = [f["key"] for f in lore_meta]
    else:
        f_keys = [k for k in _CAST_FIELD_KEYS
                  if not ((m.get("fields") or {}).get(k) or "").strip()]
        l_keys = [f["key"] for f in lore_meta
                  if not ((m.get("lore") or {}).get(f["key"]) or "").strip()]
    if not f_keys and not l_keys:
        return {"member": m, "changed": [],
                "note": "nothing empty — use overwrite to redo everything"}
    known = {k: v for k, v in (m.get("fields") or {}).items() if v}
    known_lore = {k: v for k, v in (m.get("lore") or {}).items() if v}
    system = ("You are a character developer for a visual-story studio. "
              "Return ONLY a JSON object with two keys: \"appearance\" (an "
              "object) and \"lore\" (an object). Values are plain strings. "
              "In appearance: 'sex' must be exactly 'male' or 'female', "
              "'age' a number written as a string. Physical description must "
              "be specific enough to draw from. No franchise, brand or "
              "real-celebrity names anywhere.")
    user = (f"{_ctx_world(w)}\n\nCHARACTER: {m['name']} — "
            f"{m.get('role') or 'role unknown'} "
            f"({m.get('importance') or 'support'})\n"
            + (f"KNOWN APPEARANCE: {json.dumps(known)}\n" if known else "")
            + (f"KNOWN LORE: {json.dumps(known_lore)}\n" if known_lore else "")
            + (f"DIRECTION FROM THE AUTHOR: {body.direction}\n"
               if body.direction.strip() else "")
            + f"\nIn \"appearance\", write these keys: {f_keys}\n"
            + f"In \"lore\", write these keys: {l_keys}\n"
            + "Return exactly those keys inside the two objects.")
    got = await _ask_json(session, body.llm or _pick_of(w), system, user)
    changed = []
    with _LOCK:
        w = _load(wid)
        m = _find(w.get("cast") or [], cid, "cast member")
        app_got = got.get("appearance") or {}
        lore_got = got.get("lore") or {}
        ow = body.mode == "overwrite"
        for k in f_keys:
            v = _flat(app_got.get(k), 600)
            # fill must not eat text typed during the LLM call (see above)
            if v and (ow or not ((m.get("fields") or {}).get(k) or "").strip()):
                m.setdefault("fields", {})[k] = v
                changed.append(f"fields.{k}")
        for k in l_keys:
            v = _flat(lore_got.get(k))
            if v and (ow or not ((m.get("lore") or {}).get(k) or "").strip()):
                m.setdefault("lore", {})[k] = v
                changed.append(f"lore.{k}")
        m["updated_at"] = _now()
        _save(w)
    return {"member": m, "changed": changed}


class FieldEnhanceIn(BaseModel):
    section: str                     # world | story:<sid> | cast:<cid>
    field: str
    direction: str = ""
    current: str = ""                # the CLIENT's live value — beats the disk
    llm: Optional[LlmPick] = None


@router.post("/worlds/{wid}/enhance/field")
async def enhance_field(wid: str, body: FieldEnhanceIn,
                        session: AsyncSession = Depends(get_session)):
    """Expand/improve ONE field. Saves it and returns the new text.

    ⚠ `current` exists because clicking ✨ blurs the textarea, and the blur
    save and this call race — reading the disk here could see the PRE-typing
    value and tell the model the field is empty. The client's value wins."""
    w = _load(wid)
    sec, _, sub = body.section.partition(":")
    ctx = _ctx_world(w)
    current = ""
    valid: set = set()
    if sec == "world":
        valid = {f["key"] for f in _WORLD_FIELDS}
        current = (w.get("world") or {}).get(body.field, "")
    elif sec == "story":
        st = _find(w.get("stories") or [], sub, "story")
        valid = {f["key"] for f in _STORY_FIELDS}
        ctx += "\n\n" + _ctx_story(st)
        current = (st.get("fields") or {}).get(body.field, "")
    elif sec == "cast":
        m = _find(w.get("cast") or [], sub, "cast member")
        valid = set(_CAST_FIELD_KEYS) | {f["key"] for f in _CAST_LORE_FIELDS}
        ctx += (f"\n\nCHARACTER: {m['name']} — {m.get('role') or ''}\n"
                f"APPEARANCE: {json.dumps(m.get('fields') or {})}\n"
                f"LORE: {json.dumps(m.get('lore') or {})}")
        current = ((m.get("fields") or {}).get(body.field, "")
                   or (m.get("lore") or {}).get(body.field, ""))
    else:
        raise HTTPException(400, f"unknown section {body.section!r}")
    if body.field not in valid:
        raise HTTPException(400, f"{body.field!r} is not a {sec} field")
    if (body.current or "").strip():
        current = body.current.strip()
    system = ("You are an editor for a visual-story studio. Improve ONE field. "
              "Return ONLY a JSON object: {\"text\": \"...\"}. Plain prose, "
              "no markdown, no franchise or celebrity names.")
    user = (f"{ctx}\n\nFIELD TO WRITE: {body.field}\n"
            f"CURRENT VALUE: {current or '(empty)'}\n"
            + (f"DIRECTION: {body.direction}\n" if body.direction.strip()
               else "")
            + "\nIf a current value exists, keep its intent and make it "
              "richer and more concrete; if empty, write it fresh from "
              "the context.")
    got = await _ask_json(session, body.llm or _pick_of(w), system, user)
    text = _flat(got.get("text"), 8000)
    if not text:
        raise HTTPException(502, "the model returned an empty 'text'")
    with _LOCK:
        w = _load(wid)
        if sec == "world":
            w.setdefault("world", {})[body.field] = text
        elif sec == "story":
            st = _find(w.get("stories") or [], sub, "story")
            st.setdefault("fields", {})[body.field] = text
        else:
            m = _find(w.get("cast") or [], sub, "cast member")
            if body.field in _CAST_FIELD_KEYS:
                m.setdefault("fields", {})[body.field] = text[:600]
            else:
                m.setdefault("lore", {})[body.field] = text
        _save(w)
    return {"text": text}


# ══ LLM: cast generation ═════════════════════════════════════════════════════
class CastGenIn(BaseModel):
    story_id: str = ""               # '' = for the world as a whole
    max_count: int = 8               # the CAP — the LLM decides how many ≤ this
    direction: str = ""
    llm: Optional[LlmPick] = None


@router.post("/worlds/{wid}/cast/generate")
async def generate_cast(wid: str, body: CastGenIn,
                        session: AsyncSession = Depends(get_session)):
    """The LLM proposes the characters the story needs — PAPER only, nothing
    renders. The user reviews the cast board and submits when ready."""
    w = _load(wid)
    cap = max(1, min(int(body.max_count or 8), 20))
    ctx = _ctx_world(w)
    if body.story_id:
        st = _find(w.get("stories") or [], body.story_id, "story")
        ctx += "\n\n" + _ctx_story(st)
    existing = [c["name"] for c in (w.get("cast") or [])]
    lore_keys = [f["key"] for f in _CAST_LORE_FIELDS if f["key"] != "notes"]
    system = (
        "You are a casting director and character designer for a visual-story "
        "studio. Decide which characters this story actually needs — leads, "
        "supports, background — and how many (do not pad to the maximum). "
        "Return ONLY a JSON array. Each element is an object with keys: "
        "\"name\", \"role\" (their job in the story, one line), \"importance\" "
        "(\"lead\", \"support\" or \"background\"), \"appearance\" (object "
        "with any of: " + ", ".join(_CAST_FIELD_KEYS) + " — 'sex' exactly "
        "'male' or 'female', 'age' a number as a string, be visually "
        "specific), " + ", ".join(f'"{k}"' for k in lore_keys) + " (strings), "
        "and \"outfits\" (array of 1-3 objects {\"name\", \"description\"} — "
        "describe garments only, by attribute: cut, colour, material. No "
        "franchise, brand or real-celebrity names anywhere.)")
    user = (f"{ctx}\n"
            + (f"\nALREADY CAST (do NOT repeat or rename these): "
               f"{', '.join(existing)}\n" if existing else "")
            + (f"\nDIRECTION FROM THE AUTHOR: {body.direction}\n"
               if body.direction.strip() else "")
            + f"\nPropose at most {cap} NEW characters. Fewer is better than "
              f"padding. Return only the JSON array.")
    got = await _ask_json(session, body.llm or _pick_of(w), system, user,
                          want="array", max_tokens=6000)
    made, skipped = [], []
    with _LOCK:
        w = _load(wid)
        cast = w.setdefault("cast", [])
        names = {c["name"].lower() for c in cast}
        for row in got[:cap]:
            if not isinstance(row, dict):
                continue
            name = _flat(row.get("name"), 120)
            if not name:
                continue
            if name.lower() in names:
                skipped.append(name)
                continue
            m = {"id": uuid4().hex[:8], "name": name,
                 "role": _flat(row.get("role"), 300),
                 "importance": (row.get("importance")
                                if row.get("importance") in _IMPORTANCE
                                else "support"),
                 "fields": {}, "lore": {}, "outfits": [],
                 "story_ids": [body.story_id] if body.story_id else [],
                 "status": "paper", "char_slug": "", "autogen_job_id": "",
                 "created_at": _now(), "updated_at": _now()}
            app_got = row.get("appearance") or {}
            for k in _CAST_FIELD_KEYS:
                v = _flat(app_got.get(k), 600)
                if v:
                    m["fields"][k] = v
            for k in lore_keys:
                v = _flat(row.get(k))
                if v:
                    m["lore"][k] = v
            for o in (row.get("outfits") or [])[:3]:
                if isinstance(o, dict) and _flat(o.get("description"), 800):
                    m["outfits"].append(
                        {"name": _flat(o.get("name"), 120) or "outfit",
                         "description": _flat(o.get("description"), 800)})
            names.add(name.lower())
            cast.append(m)
            made.append(m)
        _save(w)
    return {"made": made, "skipped_existing": skipped,
            "cast_total": len(w["cast"])}


# ══ LLM: big bang — an idea in, a whole world out ════════════════════════════
class BigBangIn(BaseModel):
    idea: str
    stories: int = 1
    story_type: str = "music_video"
    max_cast: int = 8
    llm: Optional[LlmPick] = None


@router.post("/worlds/{wid}/bigbang")
async def bigbang(wid: str, body: BigBangIn,
                  session: AsyncSession = Depends(get_session)):
    """Seed idea → world sheet + N stories + a proposed cast, in one press.
    FILL semantics throughout: nothing the user already typed is overwritten.
    Three sequential LLM calls — with local Ollama expect a minute or three."""
    idea = (body.idea or "").strip()
    if not idea:
        raise HTTPException(400, "give it an idea — a sentence is enough")
    n_stories = max(0, min(int(body.stories or 0), 5))
    steps: List[str] = []

    # 1 — the world
    with _LOCK:
        w = _load(wid)
        sheet = w.setdefault("world", {})
        if not (sheet.get("notes") or "").strip():
            sheet["notes"] = f"Seed idea: {idea}"
        _save(w)
    r1 = await enhance_world(
        wid, EnhanceIn(mode="fill", direction=idea, llm=body.llm), session)
    steps.append(f"world: filled {len(r1.get('changed') or [])} fields")

    # 2 + 3 wrapped: a mid-flight LLM failure must not discard the report of
    # what DID land — each sub-step commits under the lock as it goes, so the
    # world is consistent; only the narrative of it was being lost.
    try:
        return await _bigbang_rest(wid, body, session, idea, n_stories, steps)
    except HTTPException as e:
        steps.append(f"⚠ stopped: {e.detail}")
        return {"world": _load(wid), "steps": steps, "error": str(e.detail)}


async def _bigbang_rest(wid: str, body: BigBangIn, session: AsyncSession,
                        idea: str, n_stories: int,
                        steps: List[str]) -> dict:
    # 2 — the stories
    made_stories = []
    if n_stories:
        w = _load(wid)
        stype = (body.story_type if body.story_type in _STORY_TYPES
                 else "music_video")
        system = ("You are a story developer. Given a world, propose stories "
                  "set in it. Return ONLY a JSON array of objects with keys "
                  "\"title\" and \"logline\" (both plain strings). No "
                  "franchise or celebrity names.")
        user = (f"{_ctx_world(w)}\n\nSEED IDEA: {idea}\n\n"
                f"Propose exactly {n_stories} distinct "
                f"{stype.replace('_', ' ')} stories. Titles short and "
                f"evocative.")
        got = await _ask_json(session, body.llm or _pick_of(w), system, user,
                              want="array", max_tokens=2000)
        for row in got[:n_stories]:
            if not isinstance(row, dict) or not _flat(row.get("title"), 200):
                continue
            st = await add_story(wid, StoryIn(
                title=_flat(row.get("title"), 200), story_type=stype,
                fields={"logline": _flat(row.get("logline"))}))
            await enhance_story(wid, st["id"],
                                EnhanceIn(mode="fill", direction=idea,
                                          llm=body.llm), session)
            made_stories.append(st["id"])
        steps.append(f"stories: {len(made_stories)} written")

    # 3 — the cast (for the first new story, else the world)
    r3 = await generate_cast(wid, CastGenIn(
        story_id=made_stories[0] if made_stories else "",
        max_count=body.max_cast, direction=idea, llm=body.llm), session)
    steps.append(f"cast: {len(r3.get('made') or [])} proposed")

    return {"world": _load(wid), "steps": steps}


# ══ the bridge: cast → autogen bulk submission ═══════════════════════════════
class SubmitIn(BaseModel):
    cast_ids: List[str] = []         # [] = every paper member
    level: str = "views"             # details|base|views|clothing|sheet|dataset|lora
    toggles: Dict[str, bool] = {}    # explicit override of the level
    clothing_auto_count: int = 0     # used only for members with no outfits
    candidates: int = 4
    dataset_total: int = 40
    estimate_only: bool = False


def _member_description(m: dict) -> str:
    bits = [m.get("name") or "character"]
    if m.get("role"):
        bits.append(m["role"])
    for k in ("sex", "age", "race", "hair", "body", "aesthetics",
              "additional_details"):
        v = (m.get("fields") or {}).get(k)
        if v:
            bits.append(f"{k}: {v}")
    sr = (m.get("lore") or {}).get("story_role")
    if sr:
        bits.append(sr)
    return ". ".join(bits)[:2000]


@router.post("/worlds/{wid}/cast/submit")
async def submit_cast(wid: str, body: SubmitIn):
    """Paper → pixels. Builds one AutogenSpec per member and hands the lot to
    the autogen serial queue in ONE batch (v1.276.54: that queue is the
    primitive; parallelism lives inside a character, not across them)."""
    from backend.api.autogen import (AutogenSpec, ClothingSpec, _enqueue,
                                     estimate as ag_estimate)
    if body.level not in _LEVELS:
        raise HTTPException(400, f"level must be one of {list(_LEVELS)}")
    toggles = dict(_LEVELS[body.level])
    for k, v in (body.toggles or {}).items():
        if k in _TOGGLE_KEYS:
            toggles[k] = bool(v)

    def _resolve(w: dict) -> List[dict]:
        cast = w.get("cast") or []
        if body.cast_ids:
            ms = [_find(cast, cid, "cast member") for cid in body.cast_ids]
        else:
            ms = [c for c in cast if c.get("status") == "paper"]
        if not ms:
            raise HTTPException(400, "no cast members to submit — everyone is "
                                     "already generated or the ids matched "
                                     "nobody")
        already = [m["name"] for m in ms if m.get("status") == "submitted"]
        if already:
            raise HTTPException(409,
                                f"already in the queue: {', '.join(already)}")
        return ms

    if body.estimate_only:
        members = _resolve(_load(wid))
    else:
        # ⚠ CLAIM UNDER THE LOCK BEFORE ENQUEUEING. Selection, the 409 check
        # and the status flip used to happen on an unlocked snapshot, so two
        # concurrent submits (a double-click is enough) would BOTH see everyone
        # on paper and queue every character twice — at 32min-7h each, the most
        # expensive race in the module. Claim first; revert if _enqueue fails.
        with _LOCK:
            w = _load(wid)
            members = _resolve(w)
            for m in members:
                m["status"] = "submitted"
                m["autogen_job_id"] = ""
                m["updated_at"] = _now()
            _save(w)
            members = [json.loads(json.dumps(m)) for m in members]  # snapshot

    specs: List[AutogenSpec] = []
    for m in members:
        fields = {k: v for k, v in (m.get("fields") or {}).items()
                  if k in _CAST_FIELD_KEYS and v}
        outfits = [ClothingSpec(name=o["name"], description=o["description"])
                   for o in (m.get("outfits") or []) if o.get("description")]
        spec = AutogenSpec(
            name=m["name"],
            description=_member_description(m),
            fields=fields,
            do_base=toggles.get("do_base", False),
            do_views=toggles.get("do_views", False),
            do_clothing=toggles.get("do_clothing", False),
            do_charsheet=toggles.get("do_charsheet", False),
            do_dataset=toggles.get("do_dataset", False),
            do_lora=toggles.get("do_lora", False),
            clothing=outfits if toggles.get("do_clothing") else [],
            # a clothing stage with zero outfits designs nothing — default to
            # 2 invented outfits when the member brought none of their own
            clothing_auto_count=(0 if outfits or not toggles.get("do_clothing")
                                 else max(2, int(body.clothing_auto_count or 2))),
            candidates=max(1, min(int(body.candidates or 4), 8)),
            dataset_total=max(8, min(int(body.dataset_total or 40), 120)),
        )
        specs.append(spec)

    est = [{"cast_id": m["id"], "name": m["name"], **ag_estimate(s)}
           for m, s in zip(members, specs)]
    total_s = sum(e["seconds"] for e in est)
    if body.estimate_only:
        return {"estimate": est, "total_seconds": total_s,
                "count": len(specs)}

    def _revert() -> None:
        with _LOCK:
            w2 = _load(wid)
            ids = {m["id"] for m in members}
            for c in w2.get("cast") or []:
                if c["id"] in ids and c.get("status") == "submitted" \
                        and not c.get("autogen_job_id"):
                    c["status"] = "paper"
                    c["updated_at"] = _now()
            _save(w2)

    wname = _load(wid).get("name") or wid
    try:
        res = _enqueue(specs, label=f"world:{wname} "
                                    f"({body.level}, {len(specs)})")
    except Exception:
        _revert()                    # the claim must not outlive a failed queue
        raise
    with _LOCK:
        w = _load(wid)
        by_id = {c["id"]: c for c in w.get("cast") or []}
        # ⚠ POSITIONAL mapping — _enqueue preserves spec order. Mapping by NAME
        # collided when two members were renamed onto one name and handed both
        # the same job id (reviewer finding #2).
        for m, jrow in zip(members, res.get("jobs") or []):
            c = by_id.get(m["id"])
            if c is not None:
                c["status"] = "submitted"
                c["autogen_job_id"] = jrow["id"]
                c["updated_at"] = _now()
        _save(w)
    return {**res, "estimate": est, "total_seconds": total_s}


@router.get("/worlds/{wid}/cast/status")
async def cast_status(wid: str):
    """Join the cast board to the autogen queue. Also WRITES BACK terminal
    results (done → generated + char_slug, error/cancelled → paper again with
    the error noted) so the world file converges on the truth."""
    from backend.api.autogen import _JOB_DIR
    from backend.api.lora_train import _state_load
    rows, dirty = {}, False
    with _LOCK:
        w = _load(wid)
        for m in w.get("cast") or []:
            jid = m.get("autogen_job_id")
            if not jid:
                continue
            st = _state_load(_JOB_DIR / f"{jid}.json")
            if not st:
                rows[m["id"]] = {"stage": "unknown",
                                 "detail": "job file missing"}
                # a submitted member whose job file is GONE can never finish —
                # left alone it would be stuck (409 on resubmit, polled forever)
                if m.get("status") == "submitted":
                    m["status"] = "paper"
                    m["last_error"] = "autogen job file missing"
                    m["updated_at"] = _now()
                    dirty = True
                continue
            stage = st.get("stage") or "?"
            rows[m["id"]] = {"stage": stage, "detail": st.get("detail") or "",
                             "error": st.get("error"),
                             "elapsed_s": st.get("elapsed_s"),
                             "completed": st.get("completed") or [],
                             "job_id": jid, "slug": st.get("slug") or ""}
            if stage == "done" and m.get("status") != "generated":
                m["status"] = "generated"
                m["char_slug"] = st.get("slug") or ""
                m["updated_at"] = _now()
                dirty = True
            elif stage in ("error", "cancelled") and \
                    m.get("status") == "submitted":
                m["status"] = "paper"
                m["last_error"] = (st.get("error") or stage)
                m["updated_at"] = _now()
                dirty = True
        if dirty:
            _save(w)
    return {"cast": {c["id"]: {"status": c.get("status"),
                               "char_slug": c.get("char_slug") or "",
                               "last_error": c.get("last_error")}
                     for c in w.get("cast") or []},
            "jobs": rows}


# ⚠ DECLARED LAST ON PURPOSE. FastAPI matches routes in declaration order, and
# POST /cast/{cid} would otherwise swallow the literal /cast/submit and
# /cast/generate above it as cid="submit" / cid="generate" — which is exactly
# what happened on this module's first smoke run (7 failures, all one cause).
# ══ 🎨 visual style: preset + custom + a style reference image + samples ═════
class StyleIn(BaseModel):
    preset: str = ""
    custom_text: str = ""


@router.post("/worlds/{wid}/style")
async def set_style(wid: str, body: StyleIn):
    if body.preset and body.preset not in {p["key"] for p in _STYLE_PRESETS}:
        raise HTTPException(400, f"unknown style preset {body.preset!r}")
    with _LOCK:
        w = _load(wid)
        s = w.setdefault("style", {})
        s["preset"] = body.preset or ""
        s["custom_text"] = _flat(body.custom_text, 2000)
        _save(w)
    return {"style": w["style"], "style_text": _style_text(w)}


@router.post("/worlds/{wid}/style/ref")
async def style_ref(wid: str, file: UploadFile = File(...),
                    session: AsyncSession = Depends(get_session)):
    """Upload ONE style reference image. The VISION model describes its
    ARTISTIC STYLE (not its content) and that description joins the world's
    style text — so someone with a couple of images of their own style can
    keep creating in it."""
    _load(wid)                       # 404 first
    raw = await file.read()
    if not raw:
        raise HTTPException(400, "empty file")
    try:
        from PIL import Image as _Img
        import io as _io
        im = _Img.open(_io.BytesIO(raw)).convert("RGB")
    except Exception as e:                                       # noqa: BLE001
        raise HTTPException(400, f"not a readable image: {e}")
    rid = uuid4().hex[:12]
    _STYLE_REF_DIR.mkdir(parents=True, exist_ok=True)
    im.save(_STYLE_REF_DIR / f"{rid}.png", "PNG")

    # vision-scan the STYLE, not the subject
    desc, scan_err = "", ""
    try:
        from backend.api.vnccs_native import _ollama_cfg
        from backend.services.character_studio.vnccs_native.wizards import (
            image_bytes_to_b64, ollama_chat_sync)
        urls, _txt, vision = await _ollama_cfg(session)
        if urls and vision:
            sys_p = ("You are an art director. Describe ONLY the artistic "
                     "STYLE of the image — medium, linework, shading, colour "
                     "palette, lighting, rendering technique, era/genre of "
                     "the look. NEVER describe the subject, characters or "
                     "scene content. Return ONLY a JSON object: "
                     "{\"style\": \"one dense paragraph\"}")
            got = await asyncio.to_thread(
                ollama_chat_sync, urls, vision, sys_p,
                "Describe the artistic style of this image.",
                [image_bytes_to_b64(raw)], 0.2)
            if got:
                desc = _flat(_json_obj(got).get("style"), 2000)
        else:
            scan_err = "no Ollama vision model configured in Settings"
    except Exception as e:                                       # noqa: BLE001
        scan_err = f"style scan failed: {e}"
    with _LOCK:
        w = _load(wid)
        s = w.setdefault("style", {})
        s["ref_id"] = rid
        if desc:
            s["ref_description"] = desc
        _save(w)
    return {"id": rid, "description": desc, "scan_error": scan_err,
            "style_text": _style_text(w)}


@router.get("/style/refs/{rid}/image")
async def style_ref_image(rid: str):
    if "/" in rid or "\\" in rid or ".." in rid:
        raise HTTPException(400, "bad id")
    fp = _STYLE_REF_DIR / f"{rid}.png"
    if not fp.exists():
        raise HTTPException(404, "no such style reference")
    from fastapi.responses import FileResponse
    return FileResponse(str(fp), media_type="image/png")


class SamplesIn(BaseModel):
    count: int = 4
    model: str = "krea2"             # ignored when a style ref exists (klein)
    direction: str = ""
    llm: Optional[LlmPick] = None


def _sample_meta_fp(wid: str) -> Path:
    return _SAMPLE_DIR / wid / "index.json"


def _sample_rows(wid: str) -> List[dict]:
    try:
        d = json.loads(_sample_meta_fp(wid).read_text("utf-8"))
        return d if isinstance(d, list) else []
    except Exception:                                            # noqa: BLE001
        return []


def _sample_rows_save(wid: str, rows: List[dict]) -> None:
    fp = _sample_meta_fp(wid)
    fp.parent.mkdir(parents=True, exist_ok=True)
    tmp = fp.with_name(f"index.{uuid4().hex[:6]}.tmp")
    tmp.write_text(json.dumps(rows, indent=2), "utf-8")
    tmp.replace(fp)


def _style_job_public(wid: str) -> dict:
    st = dict(_STYLE_JOBS.get(wid) or {})
    if st.get("status") == "running" and st.get("t0"):
        # live elapsed against the wall clock, never the last write (the
        # v1.276.46 rule — a frozen timer on a live run answers nothing)
        import time as _t
        st["elapsed_s"] = round(_t.time() - float(st["t0"]), 1)
    st.pop("t0", None)
    return st


def _run_style_samples(wid: str, disp, count: int, model: str,
                       prompts: List[str], style_txt: str,
                       ref_path: Optional[Path]) -> None:
    """Background thread: render the sample images, fanned across workers.
    Publishes the live-status contract: status/total/done/workers/log/elapsed,
    and each finished sample records its prompt + worker (benchmark data)."""
    import time as _t
    st = _STYLE_JOBS[wid]
    st.update({"status": "running", "total": count, "done": 0,
               "t0": _t.time(), "workers": [], "log": [], "error": None})

    def _log(msg: str) -> None:
        st.setdefault("log", []).append(
            {"t": round(_t.time() - st["t0"], 1), "detail": msg})

    out_dir = _SAMPLE_DIR / wid
    out_dir.mkdir(parents=True, exist_ok=True)
    made: List[dict] = []
    try:
        if ref_path is not None:
            # style REFERENCE image → Klein edit citing it positionally
            from backend.api.klein3 import _parallel_klein_edits
            jobs = [{"key": str(i),
                     "prompt": f"In the exact artistic style of image 1 "
                               f"— {style_txt or 'that style'} — draw: "
                               f"{prompts[i % len(prompts)]}. No text or "
                               f"captions anywhere in the image.",
                     "refs": [ref_path], "w": 1024, "h": 576,
                     "seed": 31000 + i} for i in range(count)]

            def _on_result(jb: dict, data: bytes):
                sid = uuid4().hex[:10]
                (out_dir / f"{sid}.png").write_bytes(data)
                task = (st.get("tasks") or {}).get(jb["key"]) or {}
                made.append({"id": sid, "prompt": jb["prompt"],
                             "model": "klein+styleref",
                             "worker": task.get("worker"),
                             "created_at": _now()})
                st["done"] = int(st.get("done") or 0) + 1
                _log(f"sample {st['done']}/{count} done")
                return None

            _log(f"rendering {count} via klein with the style reference")
            _parallel_klein_edits(disp, jobs, _on_result, st)
        elif model == "krea2":
            # ⚠⚠ Krea 2 NEVER uses the generic t2i workflow file — the unet
            # name baked into KREA2_TURBO_T2I.json is not what is installed,
            # and every box 400s the raw graph (measured 2026-08-14: 4 samples
            # → 400 on all three workers — the v1.276.27 lesson relearned).
            # forge's lane DISCOVERS the unet per host; use it.
            from backend.api.forge import (_krea2_core_graph, _krea2_hosts_for,
                                           _krea2_render)
            hosts = _krea2_hosts_for(None, disp)
            if not hosts:
                raise RuntimeError("no Krea 2 capable worker online")
            st["workers"] = sorted(hosts)
            _log(f"rendering {count} via forge's krea2 lane across "
                 f"{len(hosts)} worker(s)")
            from concurrent.futures import ThreadPoolExecutor

            def _one_k2(i: int) -> None:
                host = hosts[i % len(hosts)]
                try:
                    p = (f"{prompts[i % len(prompts)]}, {style_txt}. "
                         f"No text or captions in the image.")
                    g = _krea2_core_graph(host, p, 1024, 576, 41000 + i,
                                          None, 1.0)
                    data = _krea2_render(host, g, 300)
                    sid = uuid4().hex[:10]
                    (out_dir / f"{sid}.png").write_bytes(data)
                    made.append({"id": sid, "prompt": p, "model": "krea2",
                                 "worker": host, "created_at": _now()})
                except Exception as e:                           # noqa: BLE001
                    _log(f"sample #{i + 1} failed on {host}: {e}")
                st["done"] = int(st.get("done") or 0) + 1
                _log(f"sample {st['done']}/{count} finished")

            with ThreadPoolExecutor(max_workers=max(1, len(hosts))) as ex:
                list(ex.map(_one_k2, range(count)))
        else:
            # style TEXT → plain t2i on the chosen model, pooled up front
            from backend.api.tools import (_images_from_outputs,
                                           _prepare_sample_workflow,
                                           _run_prompt_blocking,
                                           _sample_worker_pool)
            pool = _sample_worker_pool(disp, model)
            if not pool:
                raise RuntimeError("no image worker online")
            st["workers"] = sorted({u for u, _c in pool})
            _log(f"rendering {count} via {model} across "
                 f"{len(st['workers'])} worker(s)")
            from concurrent.futures import ThreadPoolExecutor

            def _one(i: int) -> None:
                url, client = pool[i % len(pool)]
                try:
                    p = (f"{prompts[i % len(prompts)]}, {style_txt}. "
                         f"No text or captions in the image.")
                    wf = _prepare_sample_workflow(model, p, "", 1024, 576,
                                                  41000 + i)
                    outputs, _pid = _run_prompt_blocking(client, wf, 300)
                    imgs = _images_from_outputs(outputs)
                    if not imgs:
                        raise RuntimeError("no image produced")
                    pick = imgs[-1]
                    data = client.download_output(
                        pick["filename"], pick.get("subfolder", ""),
                        pick.get("type", "output"))
                    sid = uuid4().hex[:10]
                    (out_dir / f"{sid}.png").write_bytes(data)
                    made.append({"id": sid, "prompt": p, "model": model,
                                 "worker": url, "created_at": _now()})
                except Exception as e:                           # noqa: BLE001
                    _log(f"sample #{i + 1} failed on {url}: {e}")
                st["done"] = int(st.get("done") or 0) + 1
                _log(f"sample {st['done']}/{count} finished")

            with ThreadPoolExecutor(max_workers=max(1, len(pool))) as ex:
                list(ex.map(_one, range(count)))

        rows = _sample_rows(wid) + made
        _sample_rows_save(wid, rows)
        st["status"] = "done" if made else "error"
        if not made and not st.get("error"):
            st["error"] = "every sample failed — see the log"
        st["elapsed_s"] = round(_t.time() - st["t0"], 1)
        _log(f"finished: {len(made)}/{count} samples")
    except Exception as e:                                       # noqa: BLE001
        st["status"] = "error"
        st["error"] = f"{type(e).__name__}: {e}"
        st["elapsed_s"] = round(_t.time() - st["t0"], 1)
        logger.exception("storyworld style samples %s failed", wid)


@router.post("/worlds/{wid}/style/samples")
async def style_samples(wid: str, body: SamplesIn, request: Request,
                        session: AsyncSession = Depends(get_session)):
    """🎨 Render sample images of THIS world in ITS style — the visual guide.
    With a style reference uploaded, samples render via Klein citing the
    reference; otherwise plain t2i on the chosen model with the style text."""
    w = _load(wid)
    cur = _STYLE_JOBS.get(wid)
    if cur and cur.get("status") in ("starting", "running"):
        raise HTTPException(409, "a sample render is already running for "
                                 "this world")
    count = max(1, min(int(body.count or 4), 8))
    if body.model not in _SAMPLE_MODELS:
        raise HTTPException(400, f"model must be one of {_SAMPLE_MODELS}")
    style_txt = _style_text(w)
    if not style_txt:
        raise HTTPException(400, "pick a style preset, write a custom style, "
                                 "or upload a style reference first")
    disp = getattr(request.app.state, "comfy_dispatcher", None)
    if not disp:
        raise HTTPException(409, "no worker dispatcher available")

    ref_path: Optional[Path] = None
    rid = (w.get("style") or {}).get("ref_id") or ""
    if rid and (_STYLE_REF_DIR / f"{rid}.png").exists():
        ref_path = _STYLE_REF_DIR / f"{rid}.png"

    # ⚠⚠ The job is registered BEFORE the LLM writes the scene prompts. The
    # first version registered it AFTER — and that call can take a minute on
    # local Ollama, so a poller arriving in that window saw the PREVIOUS
    # run's "done", concluded nothing was running, and stopped polling: the
    # new samples rendered with nobody watching and only a browser refresh
    # showed them. A status that appears only once work is underway is a
    # status that can lie — v1.276.29, relearned (2026-08-14).
    import time as _t
    _STYLE_JOBS[wid] = {"status": "starting", "total": count, "done": 0,
                        "t0": _t.time(),
                        "log": [{"t": 0.0, "detail": "writing scene prompts "
                                                     "(LLM, template fallback)"}]}
    try:
        # scene prompts: ask the LLM for distinct views of THIS world; fall
        # back to templates if the model is unavailable
        prompts: List[str] = []
        try:
            system = ("You write one-line IMAGE prompts. Return ONLY a JSON "
                      "array of strings — each a single concrete scene from "
                      "the world below, visually distinct from the others, no "
                      "characters' names, no text in the scene.")
            user = (f"{_ctx_world(w)}\n\n"
                    + (f"DIRECTION: {body.direction}\n"
                       if body.direction.strip() else "")
                    + f"Write exactly {count} scene prompts.")
            got = await _ask_json(session, body.llm or _pick_of(w), system,
                                  user, want="array", max_tokens=1200,
                                  timeout_s=180)
            prompts = [_flat(x, 400) for x in got if _flat(x, 400)][:count]
        except HTTPException:
            prompts = []
        if not prompts:
            sheet = w.get("world") or {}
            prompts = [
                f"a wide establishing shot of {sheet.get('setting') or 'the world'}",
                f"a street-level view of daily life, {sheet.get('culture') or 'its people going about their day'}",
                f"a dramatic moment: {sheet.get('logline') or 'the story begins'}",
                f"one of its key places: {sheet.get('locations') or 'a landmark'}",
            ][:count] * (1 + count // 4)
            prompts = prompts[:count]
    except Exception as e:
        # never leave the job wedged at "starting" — that would 409 forever
        _STYLE_JOBS[wid] = {"status": "error", "total": count, "done": 0,
                            "error": f"prompt writing failed: {e}"}
        raise

    threading.Thread(target=_run_style_samples,
                     args=(wid, disp, count, body.model, prompts, style_txt,
                           ref_path),
                     daemon=True, name=f"style-samples-{wid}").start()
    return {"started": True, "count": count, "prompts": prompts,
            "via": "klein+styleref" if ref_path else body.model}


@router.get("/worlds/{wid}/style/job")
async def style_job(wid: str):
    _load(wid)
    return {"job": _style_job_public(wid)}


@router.get("/worlds/{wid}/style/samples")
async def list_samples(wid: str):
    _load(wid)
    rows = _sample_rows(wid)
    out = []
    for r in rows:
        fp = _SAMPLE_DIR / wid / f"{r['id']}.png"
        if fp.exists():
            out.append({**r, "url": f"/api/storyworld/worlds/{wid}/style/"
                                    f"samples/{r['id']}/image"})
    return {"samples": out}


@router.get("/worlds/{wid}/style/samples/{sid}/image")
async def sample_image(wid: str, sid: str, download: bool = False):
    if any(x in sid for x in ("/", "\\", "..")) or \
            any(x in wid for x in ("/", "\\", "..")):
        raise HTTPException(400, "bad id")
    fp = _SAMPLE_DIR / wid / f"{sid}.png"
    if not fp.exists():
        raise HTTPException(404, "no such sample")
    from fastapi.responses import FileResponse
    if download:
        return FileResponse(str(fp), media_type="image/png",
                            filename=f"world_{wid}_style_{sid}.png")
    return FileResponse(str(fp), media_type="image/png")


@router.post("/worlds/{wid}/style/samples/{sid}/delete")
async def sample_delete(wid: str, sid: str):
    if any(x in sid for x in ("/", "\\", "..")):
        raise HTTPException(400, "bad id")
    (_SAMPLE_DIR / wid / f"{sid}.png").unlink(missing_ok=True)
    _sample_rows_save(wid, [r for r in _sample_rows(wid) if r["id"] != sid])
    return {"deleted": sid}


# ⚠ DECLARED LAST ON PURPOSE. FastAPI matches routes in declaration order, and
# POST /cast/{cid} would otherwise swallow the literal /cast/submit and
# /cast/generate above it as cid="submit" / cid="generate" — which is exactly
# what happened on this module's first smoke run (7 failures, all one cause).
@router.post("/worlds/{wid}/cast/{cid}")
async def update_member(wid: str, cid: str, body: MemberIn):
    with _LOCK:
        w = _load(wid)
        m = _find(w.get("cast") or [], cid, "cast member")
        # a rename must respect the same uniqueness create enforces — names
        # feed _slugify on submission, and two members on one name would
        # build the SAME klein3 character twice (reviewer finding #2)
        newn = (body.name or "").strip().lower()
        if newn and any(c["id"] != cid and c["name"].lower() == newn
                        for c in w.get("cast") or []):
            raise HTTPException(409, f"{body.name!r} is already in the cast")
        _clean_member_bits(m, body)
        m["updated_at"] = _now()
        _save(w)
    return m
