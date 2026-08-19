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

# 🎬 v1.277.24 — STORIES GET STRUCTURE. `beats` stays as the writer's free text;
# `arcs` is the machine-readable spine that a PROJECT turns into chapters, that
# the score lane turns into backing beds, and that the flow LLM reads per
# chapter. Free text and structure both, because the LLM writes better prose
# than it writes tables, and the pipeline needs the table.
_ARC_FIELDS: List[Dict[str, str]] = [
    {"key": "title",      "label": "Title",      "hint": "what this stretch of story is called"},
    {"key": "summary",    "label": "What happens", "hint": "the beat itself, 1-3 sentences"},
    {"key": "mood",       "label": "Mood",       "hint": "how it should FEEL — drives the backing track"},
    {"key": "characters", "label": "Characters", "hint": "who is present (names from the cast)"},
    {"key": "locations",  "label": "Locations",  "hint": "where it happens"},
]

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

# 📍 location sheets (v1.277.14) — worlds carry LOCATIONS the way they carry
# cast; stories link the ones they use, so a scene can name a SPECIFIC place.
_LOCATION_FIELDS: List[Dict[str, str]] = [
    {"key": "description",    "label": "Description",    "hint": "what this place IS — layout, scale, materials, landmarks"},
    {"key": "atmosphere",     "label": "Atmosphere",     "hint": "mood, sounds, smells, weather, crowd"},
    {"key": "key_details",    "label": "Key details",    "hint": "the recognisable specifics a shot should show"},
    {"key": "time_and_light", "label": "Time & light",   "hint": "typical time of day and how the light behaves"},
    {"key": "story_role",     "label": "Story role",     "hint": "what happens HERE in the stories"},
    {"key": "notes",          "label": "Notes",          "hint": "anything else"},
]
_LOCATION_KINDS = ["exterior", "interior", "landmark", "vehicle", "region", "other"]

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
# 📍🖼 v1.277.21 — LOCATION SHEETS ARE IMAGES TOO. Scouting wrote the six text
# fields and stopped there, so a "location sheet" existed as prose with nothing
# to hand a render as a reference. Shots live per world, indexed like samples.
_LOC_SHOT_DIR = _ROOT / "location_shots"
# 🎙 v1.277.30 — the narration RECORDING lives with the story, so it can be
# reviewed before any project exists. Audio is the one thing that was still
# only knowable inside a project.
_NARR_DIR = _ROOT / "narration_audio"
_LOC_JOBS: Dict[str, dict] = {}          # f"{wid}:{lid}" → live render status
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
            "locations": len(w.get("locations") or []),
            "texts": len(w.get("texts") or []),
            "project_ids": w.get("project_ids") or [],
            "updated_at": w.get("updated_at")}


def _find(items: List[dict], iid: str, what: str) -> dict:
    for it in items:
        if it.get("id") == iid:
            return it
    raise HTTPException(404, f"{what} {iid!r} not found")


# ── sanitising what an LLM hands back ────────────────────────────────────────
#: 🩹 v1.277.14 — temporary marks that keep leaking into BASE appearances (an
#: ink smudge on the secret printer's face gives her away in every scene; blood
#: spatter bakes into all four views). The base look must be CLEAN — marks
#: belong to outfits/scenes where they can be worn and removed.
_TEMP_MARK_RE = None


def _strip_temp_marks(text: str) -> str:
    """Remove wound/stain phrases from an APPEARANCE value. Conservative:
    drops the clause containing the mark, keeps the rest of the sentence."""
    global _TEMP_MARK_RE
    if _TEMP_MARK_RE is None:
        words = (r"blood|bloodied|bloody|wound|wounded|scratch(?:es|ed)?|"
                 r"bruise[sd]?|bandage[sd]?|band-aid|stitches|"
                 r"smudge[sd]?|smear(?:s|ed)?|stain(?:s|ed)?|splatter(?:s|ed)?|"
                 r"ink[- ]stain|soot|grime|grimy|dirt[- ]streak|dirty face|"
                 r"black eye|split lip|scab[s]?|gash(?:es)?|cut[s]? on")
        _TEMP_MARK_RE = re.compile(
            rf"[^,.;]*\b(?:{words})\b[^,.;]*[,.;]?\s*", re.IGNORECASE)
    cleaned = _TEMP_MARK_RE.sub("", text or "").strip(" ,;.")
    return cleaned if cleaned else text


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


def _ctx_locations(w: dict, story_id: str = "") -> str:
    locs = w.get("locations") or []
    if story_id:
        locs = [l for l in locs if story_id in (l.get("story_ids") or [])] or locs
    if not locs:
        return ""
    lines = ["KNOWN LOCATIONS:"]
    for l in locs[:20]:
        d = (l.get("fields") or {}).get("description") or ""
        lines.append(f"- {l['name']} ({l.get('kind') or 'place'}): {d[:160]}")
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
            "sample_models": list(_SAMPLE_MODELS),
            "location_fields": _LOCATION_FIELDS,
            "location_kinds": _LOCATION_KINDS,
            "arc_fields": _ARC_FIELDS}


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
         "locations": [], "texts": [], "project_ids": [],
         "llm": {"provider": "", "model": ""}}
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
    # ⚠ Optional, NOT "music_video": update_story writes any VALID value, so a
    # default of "music_video" silently converted a narration story every time
    # the arcs editor posted `{arcs}` alone (found by the v1.277.28 doc audit).
    story_type: Optional[str] = None
    fields: Dict[str, Any] = {}
    arcs: Optional[List[Dict[str, Any]]] = None


@router.post("/worlds/{wid}/stories")
async def add_story(wid: str, body: StoryIn):
    if not (body.title or "").strip():
        raise HTTPException(400, "the story needs a title")
    st = {"id": uuid4().hex[:8], "title": body.title.strip(),
          "story_type": (body.story_type if body.story_type in _STORY_TYPES
                         else "music_video"),
          "fields": {f["key"]: _flat(v) for f in _STORY_FIELDS
                     if (v := (body.fields or {}).get(f["key"])) is not None},
          "arcs": _clean_arcs(body.arcs), "created_at": _now(),
          "updated_at": _now()}
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
        if body.story_type and body.story_type in _STORY_TYPES:
            st["story_type"] = body.story_type
        for k, v in (body.fields or {}).items():
            if k in keys:
                st.setdefault("fields", {})[k] = _flat(v)
        if body.arcs is not None:
            st["arcs"] = _clean_arcs(body.arcs)
        st["updated_at"] = _now()
        _save(w)
    return st


@router.post("/worlds/{wid}/stories/{sid}/delete")
async def delete_story(wid: str, sid: str):
    metas: List[dict] = []
    with _LOCK:
        w = _load(wid)
        stories = w.get("stories") or []
        st = _find(stories, sid, "story")
        # 📖 a story's CHAPTERS die with it, and each one may own real audio on
        # disk. Collect the file metadata inside the lock; unlink after the
        # write lands (the ordering every other file path here follows).
        metas = [m for c in (st.get("chapters") or [])
                 for m in (c.get("narration_files") or {}).values()]
        metas += list(_narr_files(st).values())
        w["stories"] = [s for s in stories if s["id"] != sid]
        for t in w.get("texts") or []:          # orphaned texts stay, unlinked
            if t.get("story_id") == sid:
                t["story_id"] = ""
        _save(w)
    for m in metas:
        for d in (_NARR_DIR, _ROOT / "chapter_audio"):
            (d / wid / f"{m.get('id')}{m.get('ext') or ''}").unlink(missing_ok=True)
    return {"deleted": sid, "files_removed": len(metas)}


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


@router.get("/stories")
async def all_stories():
    """Every story across every world, flat — for pickers elsewhere.

    ⚠ Declared BEFORE the parameterized world routes purely by habit of this
    file's ordering rule; it is a literal path so it is safe either way, but
    `/worlds/{wid}` style routes ALWAYS go last (route order is load-bearing
    here, v1.277.0)."""
    out = []
    for w in _all_worlds():
        for s in (w.get("stories") or []):
            out.append({"id": s.get("id"), "title": s.get("title"),
                        "world_id": w.get("id"), "world": w.get("name"),
                        "story_type": s.get("story_type"),
                        "has_audio": bool(_narr_files(s).get("audio"))})
    return {"stories": out}


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
            m.setdefault("fields", {})[k] = _strip_temp_marks(_flat(v, 600))
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


# ══ 📍 locations ═════════════════════════════════════════════════════════════
class LocationIn(BaseModel):
    name: str = ""
    kind: str = ""
    fields: Dict[str, Any] = {}
    story_ids: Optional[List[str]] = None


def _clean_arcs(raw: Any) -> List[dict]:
    """Normalise an arc list: ordered, titled, and never longer than a story.

    ⚠ `weight` is deliberately NOT here. Arcs are matched to the audio's
    DETECTED SECTIONS in order (his call, 2026-08-16) — inventing a duration
    for a beat and then fighting the real recording is how a chapter ends up
    cutting mid-sentence."""
    out = []
    for i, a in enumerate((raw or [])[:24]):
        if not isinstance(a, dict):
            a = {"title": str(a)}
        out.append({
            "id": str(a.get("id") or uuid4().hex[:8]),
            "i": i,
            "title": _flat(a.get("title") or f"Arc {i + 1}", 120),
            "summary": _flat(a.get("summary") or "", 1200),
            "mood": _flat(a.get("mood") or "", 300),
            "characters": [_flat(x, 80) for x in (a.get("characters") or [])][:12],
            "locations": [_flat(x, 80) for x in (a.get("locations") or [])][:8],
        })
    return out


def _clean_location_bits(l: dict, body: LocationIn) -> None:
    if (body.name or "").strip():
        l["name"] = body.name.strip()
    if body.kind in _LOCATION_KINDS:
        l["kind"] = body.kind
    keys = {f["key"] for f in _LOCATION_FIELDS}
    for k, v in (body.fields or {}).items():
        if k in keys:
            l.setdefault("fields", {})[k] = _flat(v)
    if body.story_ids is not None:
        l["story_ids"] = [s for s in body.story_ids if s]


@router.post("/worlds/{wid}/locations")
async def add_location(wid: str, body: LocationIn):
    if not (body.name or "").strip():
        raise HTTPException(400, "the location needs a name")
    l = {"id": uuid4().hex[:8], "name": "", "kind": "exterior", "fields": {},
         "story_ids": [], "created_at": _now(), "updated_at": _now()}
    _clean_location_bits(l, body)
    with _LOCK:
        w = _load(wid)
        names = {x["name"].lower() for x in w.get("locations") or []}
        if l["name"].lower() in names:
            raise HTTPException(409, f"{l['name']!r} already exists in this world")
        w.setdefault("locations", []).append(l)
        _save(w)
    return l


class LocGenIn(BaseModel):
    story_id: str = ""
    max_count: int = 6
    direction: str = ""
    llm: Optional[LlmPick] = None


# ⚠ literal route BEFORE /locations/{lid} — route order is load-bearing
# (the cast/submit lesson, this module's own first smoke run)
@router.post("/worlds/{wid}/locations/generate")
async def generate_locations(wid: str, body: LocGenIn,
                             session: AsyncSession = Depends(get_session)):
    """The LLM proposes the locations the world/story needs — sheets only,
    nothing renders. Mirrors cast/generate."""
    w = _load(wid)
    cap = max(1, min(int(body.max_count or 6), 20))
    ctx = _ctx_world(w)
    if body.story_id:
        st = _find(w.get("stories") or [], body.story_id, "story")
        ctx += "\n\n" + _ctx_story(st)
    existing = [l["name"] for l in (w.get("locations") or [])]
    fkeys = [f["key"] for f in _LOCATION_FIELDS]
    system = (
        "You are a location scout and production designer for a visual-story "
        "studio. Decide which LOCATIONS this story actually needs (do not pad "
        "to the maximum). Return ONLY a JSON array; each element an object "
        "with keys: \"name\", \"kind\" (one of "
        + ", ".join(_LOCATION_KINDS) + "), and "
        + ", ".join(f'\"{k}\"' for k in fkeys)
        + " (plain strings — concrete, filmable, visually specific; no "
          "franchise or real-place names unless the world names them).")
    user = (f"{ctx}\n"
            + (f"\nALREADY SCOUTED (do NOT repeat): {', '.join(existing)}\n"
               if existing else "")
            + (f"\nDIRECTION FROM THE AUTHOR: {body.direction}\n"
               if body.direction.strip() else "")
            + f"\nPropose at most {cap} NEW locations. Return only the JSON array.")
    got = await _ask_json(session, body.llm or _pick_of(w), system, user,
                          want="array", max_tokens=4000)
    made, skipped = [], []
    with _LOCK:
        w = _load(wid)
        locs = w.setdefault("locations", [])
        names = {x["name"].lower() for x in locs}
        for row in got[:cap]:
            if not isinstance(row, dict):
                continue
            name = _flat(row.get("name"), 120)
            if not name:
                continue
            if name.lower() in names:
                skipped.append(name)
                continue
            l = {"id": uuid4().hex[:8], "name": name,
                 "kind": (row.get("kind") if row.get("kind") in _LOCATION_KINDS
                          else "exterior"),
                 "fields": {k: _flat(row.get(k)) for k in fkeys
                            if _flat(row.get(k))},
                 "story_ids": [body.story_id] if body.story_id else [],
                 "created_at": _now(), "updated_at": _now()}
            names.add(name.lower())
            locs.append(l)
            made.append(l)
        _save(w)
    return {"made": made, "skipped_existing": skipped,
            "total": len(w["locations"])}


@router.post("/worlds/{wid}/locations/{lid}/enhance")
async def enhance_location(wid: str, lid: str, body: EnhanceIn,
                           session: AsyncSession = Depends(get_session)):
    """Fill/overwrite one location's sheet from the world+story context."""
    w = _load(wid)
    l = _find(w.get("locations") or [], lid, "location")
    sheet = l.get("fields") or {}
    keys = _fill_keys(sheet, _LOCATION_FIELDS, body.mode)
    if not keys:
        return {"location": l, "changed": [],
                "note": "nothing empty — use overwrite to redo everything"}
    hints = {f["key"]: f["hint"] for f in _LOCATION_FIELDS if f["key"] in keys}
    system = ("You are a production designer. A location lives INSIDE the "
              "world below. " + _JSON_RULES)
    user = (f"{_ctx_world(w)}\n\nLOCATION: {l['name']} ({l.get('kind')})\n"
            + (f"KNOWN: {json.dumps({k: v for k, v in sheet.items() if v})}\n"
               if any(sheet.values()) else "")
            + (f"DIRECTION FROM THE AUTHOR: {body.direction}\n"
               if body.direction.strip() else "")
            + "\nWrite the following keys:\n"
            + "\n".join(f"- {k}: {h}" for k, h in hints.items())
            + "\n\nReturn exactly these keys and no others.")
    got = await _ask_json(session, body.llm or _pick_of(w), system, user)
    changed = []
    with _LOCK:
        w = _load(wid)
        l = _find(w.get("locations") or [], lid, "location")
        sheet = l.setdefault("fields", {})
        for k in keys:
            v = _flat(got.get(k))
            if v and (body.mode == "overwrite"
                      or not (sheet.get(k) or "").strip()):
                sheet[k] = v
                changed.append(k)
        l["updated_at"] = _now()
        _save(w)
    return {"location": l, "changed": changed}


# ── 📍🖼 location shots ──────────────────────────────────────────────────────
def _loc_shot_rows(wid: str) -> List[dict]:
    try:
        d = json.loads((_LOC_SHOT_DIR / wid / "index.json").read_text("utf-8"))
        return d if isinstance(d, list) else []
    except Exception:                                            # noqa: BLE001
        return []


def _loc_shot_rows_save(wid: str, rows: List[dict]) -> None:
    fp = _LOC_SHOT_DIR / wid / "index.json"
    fp.parent.mkdir(parents=True, exist_ok=True)
    tmp = fp.with_name(f"index.{uuid4().hex[:6]}.tmp")
    tmp.write_text(json.dumps(rows, indent=2), "utf-8")
    tmp.replace(fp)


def _loc_prompts(l: dict, count: int, direction: str) -> List[tuple]:
    """Turn the SIX TEXT FIELDS into distinct image prompts — no LLM call.

    The scout already wrote the sheet; asking a model to paraphrase it would
    add a minute and a failure mode for nothing. Angles are ordered so the
    first shot is always the establishing one (that is what becomes ⭐ active
    and what other lanes will cite as the reference)."""
    f = l.get("fields") or {}
    name = l.get("name") or "the location"
    kind = l.get("kind") or "exterior"
    desc = f.get("description") or ""
    atmo = f.get("atmosphere") or ""
    keyd = f.get("key_details") or ""
    light = f.get("time_and_light") or ""
    role = f.get("story_role") or ""
    base = f"{name} — a {kind} location. {desc}".strip()
    angles = [
        ("establishing", f"wide establishing shot of {base} {light}. {atmo}"),
        ("interior / eye level", f"eye-level view inside {name}, showing "
                                 f"{keyd or desc}. {atmo} {light}"),
        ("details", f"detail shot of the recognisable specifics of {name}: "
                    f"{keyd or desc}"),
        ("other light", f"{name} at a different hour — {base} under changed "
                        f"light, {atmo}"),
        ("scene opening", f"the vantage a scene would open on: {base}. "
                          f"{role or atmo}"),
        ("corner", f"a quiet corner of {name}: {keyd or desc}. {light}"),
    ]
    out = []
    for i in range(count):
        label, p = angles[i % len(angles)]
        if direction.strip():
            p += f". {direction.strip()}"
        # ⚠ a REFERENCE plate, not a scene: people and captions in a location
        # sheet get copied into every render that cites it.
        # ⚠⚠ AFFIRMATIVE ONLY. The first version ended every plate with "No
        # people, no characters, no text" and every render came back FULL of
        # people — these lanes run at cfg 1 with no negative prompt, so naming
        # a thing summons it (the Klein rule, relearned on locations 08-16).
        # Describe the EMPTY place instead: deserted, still, unoccupied.
        out.append((label, _flat(p, 600)
                    + ". A deserted, unoccupied place: empty of figures, "
                      "still and quiet, a clean environment plate"))
    return out


def _run_loc_shots(wid: str, lid: str, disp, count: int, model: str,
                   prompts: List[tuple], style_txt: str,
                   ref_path: Optional[Path], ref_mode: str = "style") -> None:
    """Render this location's plates, fanned across the fleet.

    Same three paths as the style samples (klein+styleref / forge's krea2 lane
    / plain t2i pool) and the same live-status contract — status, total, done,
    workers, log, elapsed — because that is the standing rule for every
    generation lane, not a feature of one screen."""
    import time as _t
    key = f"{wid}:{lid}"
    st = _LOC_JOBS[key]
    st.update({"status": "running", "total": count, "done": 0,
               "t0": _t.time(), "workers": [], "log": [], "error": None})

    def _log(msg: str) -> None:
        st.setdefault("log", []).append(
            {"t": round(_t.time() - st["t0"], 1), "detail": msg})

    out_dir = _LOC_SHOT_DIR / wid
    out_dir.mkdir(parents=True, exist_ok=True)
    made: List[dict] = []
    try:
        labels = [a for a, _p in prompts]
        made = _render_prompt_set(disp, model, [p for _a, p in prompts],
                                  style_txt, ref_path, out_dir, st, _log,
                                  seed_base=52000, ref_mode=ref_mode)
        # ⚠ positional write-back: `made` comes back in completion order from a
        # thread pool, so match on the PROMPT, never on the list index (the
        # v1.277.0 ② lesson — matching by position on unordered results put one
        # job's result on another row).
        for r in made:
            r["lid"] = lid
            r["kind"] = "plate"
            body = (r.get("prompt") or "")
            r["angle"] = next((a for a, p in prompts if p[:60] in body), "")
        rows = _loc_shot_rows(wid) + made
        _loc_shot_rows_save(wid, rows)
        # ⭐ the first successful plate becomes the location's active image if
        # it has none — a sheet nobody has picked from is still usable.
        if made:
            with _LOCK:
                w = _load(wid)
                loc = next((x for x in (w.get("locations") or [])
                            if x.get("id") == lid), None)
                if loc is not None and not loc.get("image_id"):
                    loc["image_id"] = made[0]["id"]
                    loc["updated_at"] = _now()
                    _save(w)
        # 🪪 the SHEET: one image with every plate on it, the way a character
        # sheet works — a single reference a model can hold consistency from.
        if made:
            try:
                sheet = _build_location_sheet(wid, lid)
                if sheet:
                    _log(f"sheet composed: {sheet['id']}")
            except Exception as e:                               # noqa: BLE001
                _log(f"sheet compose failed: {e}")
        st["status"] = "done" if made else "error"
        if not made and not st.get("error"):
            st["error"] = "every shot failed — see the log"
        st["elapsed_s"] = round(_t.time() - st["t0"], 1)
        _log(f"finished: {len(made)}/{count} shots")
    except Exception as e:                                       # noqa: BLE001
        st["status"] = "error"
        st["error"] = f"{type(e).__name__}: {e}"
        st["elapsed_s"] = round(_t.time() - st["t0"], 1)
        logger.exception("location shots %s/%s failed", wid, lid)


def _build_location_sheet(wid: str, lid: str, width: int = 2048) -> Optional[dict]:
    """🪪 Compose this location's plates into ONE sheet image.

    Same idea as the character sheet, and for the same reason: a model holds
    consistency far better from a single reference showing several views than
    from one photograph of one angle. Pure PIL — no worker, no GPU, so it is
    free to rebuild whenever the plates change.

    ⚠ Only PLATES go on the sheet. Including a previous sheet would nest
    thumbnails inside thumbnails on every rebuild."""
    from PIL import Image, ImageDraw, ImageFont
    w = _load(wid)
    loc = next((x for x in (w.get("locations") or []) if x.get("id") == lid), None)
    if loc is None:
        return None
    d = _LOC_SHOT_DIR / wid
    plates = [r for r in _loc_shot_rows(wid)
              if r.get("lid") == lid and (r.get("kind") or "plate") == "plate"
              and (d / f"{r['id']}.png").exists()]
    if not plates:
        return None
    # ⚠ order the CELLS, not the renders: plates come back in completion order
    # from a thread pool, so a sheet composed as-rendered opens on whatever
    # finished first (the first live sheet led with "details").
    order = ["establishing", "interior / eye level", "details", "other light",
             "scene opening", "corner"]
    plates = plates[-6:]                       # newest six is a full sheet
    plates.sort(key=lambda r: (order.index(r.get("angle"))
                               if r.get("angle") in order else 99))
    cols = 2 if len(plates) <= 2 else 3
    rows = (len(plates) + cols - 1) // cols
    margin, gutter, label_h, header_h = 24, 16, 34, 96
    cell_w = (width - margin * 2 - gutter * (cols - 1)) // cols
    cell_h = int(cell_w * 9 / 16)
    height = margin * 2 + header_h + rows * (cell_h + label_h) + (rows - 1) * gutter
    canvas = Image.new("RGB", (width, height), "#101319")
    draw = ImageDraw.Draw(canvas)
    try:
        f_big = ImageFont.truetype("arial.ttf", 46)
        f_small = ImageFont.truetype("arial.ttf", 24)
    except Exception:                                            # noqa: BLE001
        f_big = ImageFont.load_default()
        f_small = ImageFont.load_default()
    title = f"{loc.get('name') or 'location'}  ·  {loc.get('kind') or ''}"
    draw.text((margin, margin), title, fill="#e6e9ee", font=f_big)
    sub = (loc.get("fields") or {}).get("description") or ""
    draw.text((margin, margin + 56), sub[:150], fill="#8d97a5", font=f_small)

    for i, r in enumerate(plates):
        cx = margin + (i % cols) * (cell_w + gutter)
        cy = margin + header_h + (i // cols) * (cell_h + label_h + gutter)
        try:
            im = Image.open(d / f"{r['id']}.png").convert("RGB")
        except Exception:                                        # noqa: BLE001
            continue
        # cover-fit: fill the cell, crop the overflow — letterboxing a
        # reference sheet wastes the pixels the model is meant to read
        sc = max(cell_w / im.width, cell_h / im.height)
        im = im.resize((max(1, int(im.width * sc)), max(1, int(im.height * sc))))
        ox = (im.width - cell_w) // 2
        oy = (im.height - cell_h) // 2
        canvas.paste(im.crop((ox, oy, ox + cell_w, oy + cell_h)), (cx, cy))
        draw.text((cx + 4, cy + cell_h + 6), (r.get("angle") or "view").upper(),
                  fill="#8d97a5", font=f_small)

    sid = uuid4().hex[:10]
    d.mkdir(parents=True, exist_ok=True)
    canvas.save(d / f"{sid}.png")
    row = {"id": sid, "lid": lid, "kind": "sheet",
           "prompt": f"{loc.get('name')} — sheet of {len(plates)} plates",
           "model": "composite", "worker": None, "angle": "sheet",
           "created_at": _now()}
    # ⭐ the sheet supersedes older sheets: keep ONE, so the picker never shows
    # three near-identical composites
    rows = [r for r in _loc_shot_rows(wid)
            if not (r.get("lid") == lid and r.get("kind") == "sheet")]
    for old in _loc_shot_rows(wid):
        if old.get("lid") == lid and old.get("kind") == "sheet":
            (d / f"{old['id']}.png").unlink(missing_ok=True)
    _loc_shot_rows_save(wid, rows + [row])
    with _LOCK:
        w2 = _load(wid)
        l2 = next((x for x in (w2.get("locations") or []) if x.get("id") == lid), None)
        if l2 is not None:
            l2["sheet_id"] = sid
            # ⭐ the SHEET is the default reference — that is the whole point of
            # composing one — but a plate the USER pinned wins over a rebuild.
            if not l2.get("image_pinned"):
                l2["image_id"] = sid
            l2["updated_at"] = _now()
            _save(w2)
    return row


class LocShotsIn(BaseModel):
    # ⭐ FOUR by default (his call): establishing · interior · details · other
    # light is the standard set, the same shape as a character sheet's views.
    count: int = 4
    model: str = "krea2"
    direction: str = ""
    use_style_ref: bool = True


# ⚠ literal segments AFTER {lid}, but still declared BEFORE the bare
# POST /locations/{lid} update route further down — route order is
# load-bearing in this module (the cast/submit lesson).
class LocBulkIn(BaseModel):
    count: int = 4
    model: str = "krea2"
    direction: str = ""
    only_missing: bool = True        # False = regenerate every location
    use_style_ref: bool = True


def _run_loc_bulk(wid: str, lids: List[str], disp, body: LocBulkIn,
                  style_txt: str, ref_path: Optional[Path]) -> None:
    """Sheets for MANY locations — serial per location, fanned INSIDE each.

    ⚠ Depth-first on purpose (the v1.276.54 rule): each location already
    spreads its plates across every box, so running two locations at once
    would not add throughput, it would just make the live status unreadable
    and starve whichever finished last."""
    import time as _t
    key = f"{wid}:*"
    st = _LOC_JOBS[key]
    st.update({"status": "running", "total": len(lids), "done": 0,
               "t0": _t.time(), "log": [], "error": None})

    def _log(msg: str) -> None:
        st.setdefault("log", []).append(
            {"t": round(_t.time() - st["t0"], 1), "detail": msg})

    for lid in lids:
        try:
            w = _load(wid)
            loc = next((x for x in (w.get("locations") or [])
                        if x.get("id") == lid), None)
            if loc is None:
                continue
            st["current"] = loc.get("name") or lid
            _log(f"{loc.get('name')}: rendering {body.count} plate(s)")
            prompts = _loc_prompts(loc, max(1, min(int(body.count or 4), 8)),
                                   body.direction)
            _LOC_JOBS[f"{wid}:{lid}"] = {
                "status": "starting", "total": len(prompts), "done": 0,
                "t0": _t.time(),
                "log": [{"t": 0.0, "detail": "queued by the bulk run"}]}
            own = (loc.get("ref_id") or "")
            own_fp = _LOC_SHOT_DIR / wid / f"{own}.png" if own else None
            _run_loc_shots(wid, lid, disp, len(prompts), body.model, prompts,
                           style_txt,
                           own_fp if (own_fp and own_fp.exists()) else ref_path,
                           "subject" if (own_fp and own_fp.exists()) else "style")
            sub = _LOC_JOBS.get(f"{wid}:{lid}") or {}
            if sub.get("status") == "error":
                _log(f"{loc.get('name')}: {sub.get('error')}")
        except Exception as e:                                   # noqa: BLE001
            _log(f"{lid}: {type(e).__name__}: {e}")
        st["done"] = int(st.get("done") or 0) + 1
    st["current"] = ""
    st["status"] = "done"
    st["elapsed_s"] = round(_t.time() - st["t0"], 1)
    _log(f"finished {st['done']}/{st['total']} locations")


@router.post("/worlds/{wid}/locations/shots/all")
async def render_all_location_shots(wid: str, body: LocBulkIn,
                                    request: Request):
    """🖼 Sheets for every location that has none — or for ALL of them."""
    w = _load(wid)
    locs = w.get("locations") or []
    if not locs:
        raise HTTPException(400, "this world has no locations yet")
    cur = _LOC_JOBS.get(f"{wid}:*")
    if cur and cur.get("status") in ("starting", "running"):
        raise HTTPException(409, "a bulk location render is already running")
    have = {r.get("lid") for r in _loc_shot_rows(wid)
            if (_LOC_SHOT_DIR / wid / f"{r['id']}.png").exists()}
    want = []
    for l in locs:
        if body.only_missing and l.get("id") in have:
            continue
        # a location with an empty sheet cannot produce a prompt worth rendering
        if not any((l.get("fields") or {}).get(k) for k in
                   ("description", "atmosphere", "key_details")):
            continue
        want.append(l["id"])
    if not want:
        raise HTTPException(409, "nothing to render — every location with a "
                                 "filled sheet already has plates (untick "
                                 "'only missing' to regenerate)")
    if body.model not in _SAMPLE_MODELS:
        raise HTTPException(400, f"model must be one of {_SAMPLE_MODELS}")
    disp = getattr(request.app.state, "comfy_dispatcher", None)
    if not disp:
        raise HTTPException(409, "no worker dispatcher available")
    ref_path: Optional[Path] = None
    rid = (w.get("style") or {}).get("ref_id") or ""
    if body.use_style_ref and rid and (_STYLE_REF_DIR / f"{rid}.png").exists():
        ref_path = _STYLE_REF_DIR / f"{rid}.png"
    import time as _t
    _LOC_JOBS[f"{wid}:*"] = {"status": "starting", "total": len(want),
                             "done": 0, "t0": _t.time(), "current": "",
                             "log": [{"t": 0.0,
                                      "detail": f"{len(want)} location(s) queued"}]}
    threading.Thread(target=_run_loc_bulk,
                     args=(wid, want, disp, body, _style_text(w), ref_path),
                     daemon=True, name=f"loc-bulk-{wid}").start()
    return {"started": True, "locations": len(want),
            "mode": "missing only" if body.only_missing else "all"}


@router.get("/worlds/{wid}/locations/shots/job")
async def location_bulk_job(wid: str):
    """The BULK job — per-location status stays on each location's own row."""
    _load(wid)
    st = dict(_LOC_JOBS.get(f"{wid}:*") or {})
    if st.get("status") == "running" and st.get("t0"):
        import time as _t
        st["elapsed_s"] = round(_t.time() - float(st["t0"]), 1)
    st.pop("t0", None)
    return {"job": st}


@router.post("/worlds/{wid}/locations/{lid}/sheet")
async def rebuild_location_sheet(wid: str, lid: str):
    """🪪 Recompose the sheet from the plates on disk — free, no worker."""
    _load(wid)
    row = _build_location_sheet(wid, lid)
    if not row:
        raise HTTPException(409, "no plates to compose — render some first")
    return {"sheet": row}


@router.get("/location-images")
async def location_images(wid: str = ""):
    """📍 Every location plate/sheet on the fleet, for the REFERENCE pickers.

    Shaped like the character picker's groups so an image reference can be a
    PLACE as easily as a person — same-origin URLs the video lane can map to
    disk."""
    out = []
    for w in ([_load(wid)] if wid else _all_worlds()):
        rows = _loc_shot_rows(w["id"])
        for l in (w.get("locations") or []):
            imgs = []
            for r in rows:
                if r.get("lid") != l.get("id"):
                    continue
                if not (_LOC_SHOT_DIR / w["id"] / f"{r['id']}.png").exists():
                    continue
                imgs.append({
                    "id": r["id"], "kind": r.get("kind") or "plate",
                    "label": r.get("angle") or r.get("kind") or "view",
                    "active": r["id"] == (l.get("image_id") or ""),
                    "url": f"/api/storyworld/worlds/{w['id']}/locations/"
                           f"shots/{r['id']}/image"})
            if imgs:
                # sheet first — it is the one a model holds consistency from
                imgs.sort(key=lambda x: (x["kind"] != "sheet", not x["active"]))
                out.append({"world_id": w["id"], "world": w.get("name"),
                            "id": l.get("id"), "name": l.get("name"),
                            "kind": l.get("kind"), "images": imgs})
    return {"locations": out}


@router.post("/worlds/{wid}/locations/{lid}/reference")
async def location_reference(wid: str, lid: str, file: UploadFile = File(...),
                             session: AsyncSession = Depends(get_session)):
    """📷 YOUR photo of this place → the sheet writes itself from it.

    The character lane has done this for a long time (a front reference photo
    drives every generated view); locations had no equivalent, so a real place
    could only be described, never shown. Now:

        upload → VISION scan → the six text fields are filled from what is
        ACTUALLY in the picture → every plate is rendered FROM that image

    ⚠ The scan describes the PLACE, not the photograph: no "a photo of", no
    camera talk, and explicitly no people, because the plates that cite this
    reference must not inherit anyone standing in it."""
    w = _load(wid)
    loc = _find(w.get("locations") or [], lid, "location")
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
    d = _LOC_SHOT_DIR / wid
    d.mkdir(parents=True, exist_ok=True)
    im.save(d / f"{rid}.png", "PNG")

    fields, scan_err = {}, ""
    try:
        from backend.api.vnccs_native import _ollama_cfg
        from backend.services.character_studio.vnccs_native.wizards import (
            image_bytes_to_b64, ollama_chat_sync)
        urls, _txt, vision = await _ollama_cfg(session)
        if urls and vision:
            sys_p = ("You are a production designer documenting a LOCATION "
                     "from a reference photograph. Describe the PLACE, never "
                     "the photograph: no 'a photo of', no camera or lens talk, "
                     "and never mention people even if some are visible — the "
                     "sheet describes the place itself. Return ONLY a JSON "
                     "object with these keys, each a dense paragraph:\n"
                     '  "description"    what this place IS — layout, scale, '
                     'materials, landmarks\n'
                     '  "atmosphere"     mood, sounds, smells, weather, air\n'
                     '  "key_details"    the recognisable specifics a shot '
                     'should show\n'
                     '  "time_and_light" the time of day and how the light '
                     'behaves here\n'
                     '  "kind"           one of: exterior, interior, landmark, '
                     'vehicle, region, other')
            got = await asyncio.to_thread(
                ollama_chat_sync, urls, vision, sys_p,
                "Document this location.", [image_bytes_to_b64(raw)], 0.2)
            if got:
                fields = _json_obj(got)
        else:
            scan_err = "no Ollama vision model configured in Settings"
    except Exception as e:                                       # noqa: BLE001
        scan_err = f"location scan failed: {e}"

    changed = []
    with _LOCK:
        w2 = _load(wid)
        l2 = _find(w2.get("locations") or [], lid, "location")
        l2["ref_id"] = rid
        keys = {f["key"] for f in _LOCATION_FIELDS}
        for k, v in (fields or {}).items():
            if k == "kind" and str(v).strip() in _LOCATION_KINDS:
                l2["kind"] = str(v).strip()
                changed.append("kind")
            elif k in keys and _flat(v):
                # ⚠ FILL semantics — never overwrite what he already wrote
                if not (l2.get("fields") or {}).get(k):
                    l2.setdefault("fields", {})[k] = _flat(v, 2000)
                    changed.append(k)
        l2["updated_at"] = _now()
        _save(w2)
    return {"ref_id": rid, "changed": changed, "scan_error": scan_err,
            "url": f"/api/storyworld/worlds/{wid}/locations/shots/{rid}/image",
            "location": _find(_load(wid).get("locations") or [], lid, "location")}


@router.post("/worlds/{wid}/locations/{lid}/shots")
async def render_location_shots(wid: str, lid: str, body: LocShotsIn,
                                request: Request):
    """🖼 Render this location's SHEET — the plates other lanes cite."""
    w = _load(wid)
    l = _find(w.get("locations") or [], lid, "location")
    key = f"{wid}:{lid}"
    cur = _LOC_JOBS.get(key)
    if cur and cur.get("status") in ("starting", "running"):
        raise HTTPException(409, "this location is already rendering")
    count = max(1, min(int(body.count or 4), 8))
    if body.model not in _SAMPLE_MODELS:
        raise HTTPException(400, f"model must be one of {_SAMPLE_MODELS}")
    if not any((l.get("fields") or {}).get(k) for k in
               ("description", "atmosphere", "key_details")):
        raise HTTPException(400, "fill the sheet first (description / "
                                 "atmosphere / key details) — ✨ or 📍 Scout "
                                 "writes them")
    disp = getattr(request.app.state, "comfy_dispatcher", None)
    if not disp:
        raise HTTPException(409, "no worker dispatcher available")
    # ⭐ the location's OWN uploaded photo wins over the world's style ref:
    # "make more views of THIS place" beats "make a place in this style".
    ref_path: Optional[Path] = None
    ref_mode = "style"
    own = (l.get("ref_id") or "")
    if own and (_LOC_SHOT_DIR / wid / f"{own}.png").exists():
        ref_path = _LOC_SHOT_DIR / wid / f"{own}.png"
        ref_mode = "subject"          # image 1 IS the place, not a style
    else:
        rid = (w.get("style") or {}).get("ref_id") or ""
        if body.use_style_ref and rid and (_STYLE_REF_DIR / f"{rid}.png").exists():
            ref_path = _STYLE_REF_DIR / f"{rid}.png"
    prompts = _loc_prompts(l, count, body.direction)
    # register BEFORE the thread starts — a status that appears only once work
    # is underway is a status that can lie (v1.276.29).
    import time as _t
    _LOC_JOBS[key] = {"status": "starting", "total": count, "done": 0,
                      "t0": _t.time(),
                      "log": [{"t": 0.0, "detail": f"rendering {count} plate(s) "
                                                   f"of {l.get('name')}"}]}
    threading.Thread(target=_run_loc_shots,
                     args=(wid, lid, disp, count, body.model, prompts,
                           _style_text(w), ref_path, ref_mode),
                     daemon=True, name=f"loc-shots-{lid}").start()
    return {"started": True, "count": count, "prompts": prompts,
            "via": ("klein+locationref" if ref_mode == "subject"
                    else ("klein+styleref" if ref_path else body.model))}


@router.get("/worlds/{wid}/locations/{lid}/shots")
async def list_location_shots(wid: str, lid: str):
    w = _load(wid)
    l = _find(w.get("locations") or [], lid, "location")
    st = dict(_LOC_JOBS.get(f"{wid}:{lid}") or {})
    if st.get("status") == "running" and st.get("t0"):
        import time as _t
        st["elapsed_s"] = round(_t.time() - float(st["t0"]), 1)
    st.pop("t0", None)
    out = []
    for r in _loc_shot_rows(wid):
        if r.get("lid") != lid:
            continue
        if (_LOC_SHOT_DIR / wid / f"{r['id']}.png").exists():
            out.append({**r, "active": r["id"] == (l.get("image_id") or ""),
                        "url": f"/api/storyworld/worlds/{wid}/locations/"
                               f"shots/{r['id']}/image"})
    out.sort(key=lambda r: (r.get("kind") != "sheet", r.get("created_at") or ""))
    return {"shots": out, "job": st, "active_id": l.get("image_id") or "",
            "sheet_id": l.get("sheet_id") or ""}


@router.get("/worlds/{wid}/locations/shots/{sid}/image")
async def location_shot_image(wid: str, sid: str, download: bool = False):
    if any(x in sid + wid for x in ("/", "\\", "..")):
        raise HTTPException(400, "bad id")
    fp = _LOC_SHOT_DIR / wid / f"{sid}.png"
    if not fp.exists():
        raise HTTPException(404, "no such shot")
    from fastapi.responses import FileResponse
    if download:
        return FileResponse(str(fp), media_type="image/png",
                            filename=f"location_{sid}.png")
    return FileResponse(str(fp), media_type="image/png")


@router.post("/worlds/{wid}/locations/{lid}/shots/{sid}/active")
async def location_shot_active(wid: str, lid: str, sid: str):
    """⭐ The plate this location IS — what other lanes will cite."""
    with _LOCK:
        w = _load(wid)
        l = _find(w.get("locations") or [], lid, "location")
        if not (_LOC_SHOT_DIR / wid / f"{sid}.png").exists():
            raise HTTPException(404, "no such shot")
        l["image_id"] = sid
        l["image_pinned"] = True      # a rebuild must not silently override it
        l["updated_at"] = _now()
        _save(w)
    return {"active_id": sid}


@router.post("/worlds/{wid}/locations/{lid}/shots/{sid}/delete")
async def location_shot_delete(wid: str, lid: str, sid: str):
    if any(x in sid for x in ("/", "\\", "..")):
        raise HTTPException(400, "bad id")
    (_LOC_SHOT_DIR / wid / f"{sid}.png").unlink(missing_ok=True)
    _loc_shot_rows_save(wid, [r for r in _loc_shot_rows(wid)
                              if r["id"] != sid])
    with _LOCK:
        w = _load(wid)
        l = _find(w.get("locations") or [], lid, "location")
        if l.get("image_id") == sid:
            # ⚠ never leave a dangling active id — the card would show a
            # broken image and nothing would say why
            rest = [r for r in _loc_shot_rows(wid) if r.get("lid") == lid]
            sheet = next((r for r in rest if r.get("kind") == "sheet"), None)
            l["image_id"] = (sheet or rest[0])["id"] if rest else ""
            l["image_pinned"] = False
            _save(w)
    return {"deleted": sid}


@router.post("/worlds/{wid}/locations/{lid}/delete")
async def delete_location(wid: str, lid: str):
    with _LOCK:
        w = _load(wid)
        _find(w.get("locations") or [], lid, "location")
        w["locations"] = [x for x in w["locations"] if x["id"] != lid]
        _save(w)
    return {"deleted": lid}


# ⚠ parameterized update declared AFTER the literals above (route order!)
@router.post("/worlds/{wid}/locations/{lid}")
async def update_location(wid: str, lid: str, body: LocationIn):
    with _LOCK:
        w = _load(wid)
        l = _find(w.get("locations") or [], lid, "location")
        newn = (body.name or "").strip().lower()
        if newn and any(x["id"] != lid and x["name"].lower() == newn
                        for x in w.get("locations") or []):
            raise HTTPException(409, f"{body.name!r} already exists")
        _clean_location_bits(l, body)
        l["updated_at"] = _now()
        _save(w)
    return l


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
              "be specific enough to draw from. Appearance is the CLEAN "
              "PERMANENT look — never wounds, blood, bandages, stains, "
              "smudges or dirt (those belong to outfits/scenes, not the "
              "base every scene derives from). No franchise, brand or "
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
            v = _strip_temp_marks(_flat(app_got.get(k), 600))
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
                m.setdefault("fields", {})[body.field] = \
                    _strip_temp_marks(text[:600])
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
    lctx = _ctx_locations(w, body.story_id)
    if lctx:
        ctx += "\n\n" + lctx
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
        "specific. ⚠ Appearance is the character's CLEAN PERMANENT look — "
        "NEVER include wounds, blood, bruises, bandages, stains, smudges, "
        "soot or dirt there, even when the backstory implies them: every "
        "scene derives from the base look, and a mark that belongs to one "
        "moment would appear in all of them. Story-driven marks go in an "
        "outfit or scene description instead), " + ", ".join(f'"{k}"' for k in lore_keys) + " (strings), "
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
                v = _strip_temp_marks(_flat(app_got.get(k), 600))
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


class ArcGenIn(BaseModel):
    count: int = 0                   # 0 = let the model choose (3-8)
    direction: str = ""
    overwrite: bool = False          # False = keep existing arcs
    llm: Optional[LlmPick] = None


@router.post("/worlds/{wid}/stories/{sid}/structure")
async def structure_story(wid: str, sid: str, body: ArcGenIn,
                          session: AsyncSession = Depends(get_session)):
    """🎬 Turn a story's prose into ARCS — the spine a project consumes.

    The writer keeps writing prose (`beats`, `synopsis`); this reads it and
    returns the ordered machine-readable version. Arcs are what become
    CHAPTERS on a linked project, what the flow LLM is given per chapter, and
    what the score lane renders one backing bed per."""
    w = _load(wid)
    st = _find(w.get("stories") or [], sid, "story")
    if st.get("arcs") and not body.overwrite:
        return {"arcs": st["arcs"], "note": "already structured — tick "
                                            "overwrite to rewrite"}
    n = max(0, min(int(body.count or 0), 24))
    cast_names = [m.get("name") for m in (w.get("cast") or [])
                  if not m.get("story_ids") or sid in (m.get("story_ids") or [])]
    loc_names = [l.get("name") for l in (w.get("locations") or [])]
    system = ("You are a story editor. You answer with JSON only — no prose, "
              "no markdown fences. You break a story into its ARCS: ordered "
              "stretches of story, each one a single movement with its own "
              "mood. You never invent characters or locations that were not "
              "given to you.")
    user = (f"{_ctx_world(w)}\n\n{_ctx_story(st)}\n\n"
            + (f"CAST AVAILABLE: {', '.join([c for c in cast_names if c])}\n"
               if cast_names else "")
            + (f"LOCATIONS AVAILABLE: {', '.join([l for l in loc_names if l])}\n"
               if loc_names else "")
            + (f"DIRECTION: {body.direction.strip()}\n"
               if body.direction.strip() else "")
            + (f"\nBreak it into exactly {n} arcs."
               if n else "\nBreak it into between 3 and 8 arcs — as many as "
                         "the story actually has.")
            + "\nReturn a JSON array of objects with exactly these keys:\n"
              '  "title"      short name for this stretch\n'
              '  "summary"    what happens in it, 1-3 sentences\n'
              '  "mood"       how it FEELS (this drives its backing track)\n'
              '  "characters" array of names, from the cast above only\n'
              '  "locations"  array of names, from the locations above only\n')
    got = await _ask_json(session, body.llm or _pick_of(w), system, user,
                          want="array", max_tokens=3000)
    arcs = _clean_arcs(got)
    if not arcs:
        raise HTTPException(502, "the model returned no arcs")
    with _LOCK:
        w2 = _load(wid)
        st2 = _find(w2.get("stories") or [], sid, "story")
        st2["arcs"] = arcs
        st2["updated_at"] = _now()
        _save(w2)
    return {"arcs": arcs}


async def _assign_cast_stories(wid: str, story_ids: List[str],
                               llm: Optional[LlmPick],
                               session: AsyncSession) -> int:
    """Map each cast member to the stories they actually appear in.

    Without this a multi-story world has one story owning everybody, so "the
    cast of THIS story" — the question a linked project asks — has no answer."""
    w = _load(wid)
    cast = w.get("cast") or []
    stories = [s for s in (w.get("stories") or []) if s["id"] in story_ids]
    if not cast or len(stories) < 2:
        return 0
    system = ("You assign characters to the stories they appear in. Answer "
              "with JSON only: an object mapping each character NAME to an "
              "array of story TITLES they appear in. A character may appear "
              "in several stories, or in one.")
    user = (f"{_ctx_world(w)}\n\nSTORIES:\n"
            + "\n".join(f"- {s.get('title')}: "
                         f"{(s.get('fields') or {}).get('logline') or ''}"
                         for s in stories)
            + "\n\nCHARACTERS:\n"
            + "\n".join(f"- {m.get('name')}: {m.get('role') or ''} "
                         f"{((m.get('lore') or {}).get('story_role') or '')[:200]}"
                         for m in cast))
    got = await _ask_json(session, llm or _pick_of(w), system, user,
                          want="object", max_tokens=2000)
    by_title = {(s.get("title") or "").strip().lower(): s["id"] for s in stories}
    n = 0
    with _LOCK:
        w2 = _load(wid)
        for m in (w2.get("cast") or []):
            titles = got.get(m.get("name") or "") or []
            ids = [by_title.get(str(t).strip().lower()) for t in titles]
            ids = [i for i in ids if i]
            if ids:
                m["story_ids"] = ids
                n += 1
        _save(w2)
    return n


class CastStoryMapIn(BaseModel):
    llm: Optional[LlmPick] = None


@router.post("/worlds/{wid}/cast/map-stories")
async def map_cast_stories(wid: str, body: CastStoryMapIn,
                           session: AsyncSession = Depends(get_session)):
    """🎭 Who is in which story — run it after adding stories or cast."""
    w = _load(wid)
    ids = [s["id"] for s in (w.get("stories") or [])]
    if len(ids) < 2:
        raise HTTPException(400, "needs at least two stories to be meaningful")
    n = await _assign_cast_stories(wid, ids, body.llm, session)
    return {"mapped": n, "cast": (_load(wid).get("cast") or [])}


# ── ✍ narration: the words a TTS will read ──────────────────────────────────
class NarrationIn(BaseModel):
    minutes: float = 5.0             # target runtime; ~150 spoken words/min
    tone: str = ""                   # "campfire storyteller", "documentary"…
    per_arc: bool = True             # write it arc by arc (maps to chapters)
    person: str = "third"            # third | first
    overwrite: bool = False          # replace the story's existing narration
    llm: Optional[LlmPick] = None


def spoken_only(body: str) -> str:
    """The narration with its `## Arc` headers removed — JUST THE SPOKEN TEXT.

    ⭐ The headers exist so the narration, the chapters and the backing beds
    share boundaries. They must never reach a TTS or the project's script
    field: a reader would say "hash hash payroll smoke" out loud, and Whisper
    would align a heading that was never spoken."""
    out = []
    for line in (body or "").splitlines():
        if re.match(r"^\s{0,3}#{1,6}\s", line):
            continue
        out.append(line)
    text = "\n".join(out)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _story_narration(w: dict, sid: str) -> Optional[dict]:
    """The narration text belonging to THIS story, if it has one."""
    for t in (w.get("texts") or []):
        if t.get("story_id") == sid and t.get("kind") == "narration":
            return t
    return None


@router.get("/worlds/{wid}/stories/{sid}/narration")
async def get_story_narration(wid: str, sid: str):
    """The story's narration text + what a TTS would make of it."""
    w = _load(wid)
    st = _find(w.get("stories") or [], sid, "story")
    t = _story_narration(w, sid)
    body = (t or {}).get("body") or ""
    words = len([x for x in re.split(r"\s+", body) if x])
    return {"text": t, "words": words, "files": _narr_files(st),
            "spoken": spoken_only(body),
            # 150 wpm is the usual narration pace; it is a SANITY figure, not a
            # promise — the real duration comes from the rendered audio.
            "est_minutes": round(words / 150.0, 1) if words else 0.0,
            "arcs": st.get("arcs") or []}


@router.post("/worlds/{wid}/stories/{sid}/narration")
async def write_story_narration(wid: str, sid: str, body: NarrationIn,
                                session: AsyncSession = Depends(get_session)):
    """✍ Write the narration FOR this story — the words a TTS will read.

    ⭐ Written ARC BY ARC when the story has arcs, with a `## Arc title` header
    before each block. Two reasons, both structural rather than cosmetic:
    the project's chapter parser already splits a script on markdown headers,
    and this project's chapters ARE the arcs — so the narration, the chapters
    and the backing tracks all land on the same boundaries instead of three
    different ones.

    Length is a WORD BUDGET, not a request: 'about five minutes' means ~750
    words, and models honour a word count far better than a duration."""
    w = _load(wid)
    st = _find(w.get("stories") or [], sid, "story")
    existing = _story_narration(w, sid)
    if existing and (existing.get("body") or "").strip() and not body.overwrite:
        raise HTTPException(409, "this story already has narration — tick "
                                 "overwrite to rewrite it")
    minutes = max(0.5, min(float(body.minutes or 5.0), 90.0))
    total_words = int(minutes * 150)
    arcs = st.get("arcs") or []
    use_arcs = bool(arcs) and body.per_arc
    per = max(60, total_words // max(1, len(arcs))) if use_arcs else total_words

    system = (
        "You are a narration writer for a video. You write PROSE THAT WILL BE "
        "READ ALOUD — no headings inside the prose, no stage directions, no "
        "bullet points, no lyrics, nothing in brackets. Short, speakable "
        "sentences. Concrete images over abstractions. You never invent "
        "characters or places that were not given to you, and you never use "
        "franchise, brand or real-celebrity names."
        + (" You answer with JSON only." if use_arcs else "")
    )
    ctx = (f"{_ctx_world(w)}\n\n{_ctx_story(st)}\n\n"
           + _ctx_locations(w, sid)
           + "\n\nCAST: "
           + ", ".join(f"{m.get('name')} ({m.get('role') or 'character'})"
                       for m in (w.get("cast") or [])
                       if not m.get("story_ids") or sid in (m.get("story_ids") or []))
           + (f"\n\nTONE: {body.tone.strip()}" if body.tone.strip() else "")
           + (f"\n\nWrite in the {'first' if body.person == 'first' else 'third'} person."))

    if use_arcs:
        user = (ctx + "\n\nARCS, in order:\n"
                + "\n".join(f"{i + 1}. {a.get('title')}: {a.get('summary') or ''}"
                             f" (mood: {a.get('mood') or 'as the story implies'})"
                             for i, a in enumerate(arcs))
                + f"\n\nWrite the narration for EACH arc, in order, about "
                  f"{per} words per arc ({total_words} in total). Return a JSON "
                  f"array of objects with keys \"arc\" (the arc's title, "
                  f"exactly as given) and \"text\" (the narration prose for "
                  f"it).")
        got = await _ask_json(session, body.llm or _pick_of(w), system, user,
                              want="array", max_tokens=8000, timeout_s=900)
        blocks = []
        for i, a in enumerate(arcs):
            row = next((r for r in got
                        if isinstance(r, dict)
                        and str(r.get("arc") or "").strip().lower()
                        == (a.get("title") or "").strip().lower()), None)
            if row is None and i < len(got) and isinstance(got[i], dict):
                row = got[i]          # positional fallback, in order
            txt = _flat((row or {}).get("text") or "", 20000)
            if txt:
                blocks.append(f"## {a.get('title')}\n\n{txt}")
        text_body = "\n\n".join(blocks)
    else:
        from backend.api.concept import _call_llm
        provider, key, model = await _llm_cfg(session, body.llm or _pick_of(w))
        user = (ctx + f"\n\nWrite the complete narration for this story in "
                      f"about {total_words} words, as flowing prose.")
        txt = await asyncio.wait_for(
            asyncio.to_thread(_call_llm, provider, key, model, system, user,
                              8000), timeout=900)
        text_body = _flat(re.sub(r"<think>.*?</think>", "", txt or "",
                                 flags=re.DOTALL), 40000)

    if not (text_body or "").strip():
        raise HTTPException(502, "the model returned no narration")

    title = f"{st.get('title') or 'story'} — narration"
    if existing:
        t = await update_text(wid, existing["id"],
                              TextIn(kind="narration", title=title,
                                     body=text_body, story_id=sid))
    else:
        t = await add_text(wid, TextIn(kind="narration", title=title,
                                       body=text_body, story_id=sid))
    words = len([x for x in re.split(r"\s+", text_body) if x])
    return {"text": t, "words": words,
            "est_minutes": round(words / 150.0, 1),
            "arcs_written": len(arcs) if use_arcs else 0}


#: 🎙 v1.277.31 — a narration has up to THREE files, and an AAF project needs
#: all three. One "audio" slot that silently accepted an .aaf was the hole he
#: found: the project linked, and then nothing could act on it.
#:
#:   audio  the recording itself      → the project's audio source
#:   aaf    the ElevenLabs timeline   → the project's Import-AAF path
#:   srt    the subtitle timings      → the project's SRT upload
#:
#: Non-AAF modes use audio (+ srt when there is one) exactly the same way.
_NARR_SLOTS = ("audio", "aaf", "srt")
_NARR_EXT = {
    "audio": {".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg"},
    "aaf": {".aaf"},
    "srt": {".srt", ".vtt"},
}
_NARR_AUDIO_EXT = {".wav": True, ".mp3": True, ".m4a": True, ".aac": True,
                   ".flac": True, ".ogg": True, ".aaf": False}


def _narr_audio_fp(wid: str, meta: dict) -> Path:
    return _NARR_DIR / wid / f"{meta.get('id')}{meta.get('ext') or '.wav'}"


def _probe_seconds(fp: Path) -> float:
    """Duration via ffprobe — 0.0 when it is not installed or not audio.

    ⚠ A number here is a MEASUREMENT (of the real recording), unlike the
    word-budget estimate on the text, which is arithmetic. Keep them labelled
    differently in the UI or one will be mistaken for the other."""
    import shutil
    import subprocess
    if not shutil.which("ffprobe"):
        return 0.0
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                            "format=duration", "-of",
                            "default=nw=1:nk=1", str(fp)],
                           capture_output=True, text=True, timeout=60)
        return round(float((r.stdout or "0").strip() or 0), 2)
    except Exception:                                            # noqa: BLE001
        return 0.0


def _narr_files(st: dict) -> dict:
    """The story's three narration files, migrating the pre-.31 single slot.

    ⚠ An .aaf uploaded into the old `narration_audio` slot belongs in `aaf` —
    read it that way rather than asking him to re-upload 55 MB."""
    files = dict(st.get("narration_files") or {})
    legacy = st.get("narration_audio") or None
    if legacy and not files:
        slot = "aaf" if (legacy.get("ext") or "") == ".aaf" else "audio"
        files[slot] = legacy
    return files


def _slot_fp(wid: str, meta: dict) -> Path:
    return _NARR_DIR / wid / f"{meta.get('id')}{meta.get('ext') or ''}"


@router.post("/worlds/{wid}/stories/{sid}/narration/file/{slot}")
async def upload_story_narration_file(wid: str, sid: str, slot: str,
                                      file: UploadFile = File(...)):
    """🎙 Upload one of the story's THREE narration files (audio | aaf | srt).

    All three live with the STORY so a take can be reviewed before any project
    exists — and so that linking a project has something to hand it."""
    if slot not in _NARR_SLOTS:
        raise HTTPException(400, f"slot must be one of {list(_NARR_SLOTS)}")
    _find(_load(wid).get("stories") or [], sid, "story")
    name = (file.filename or slot).strip()
    ext = ("." + name.rsplit(".", 1)[-1].lower()) if "." in name else ""
    if ext not in _NARR_EXT[slot]:
        raise HTTPException(400, f"a {slot} file must be one of "
                                 f"{sorted(_NARR_EXT[slot])}")
    raw = await file.read()
    if not raw:
        raise HTTPException(400, "empty file")
    aid = uuid4().hex[:10]
    d = _NARR_DIR / wid
    d.mkdir(parents=True, exist_ok=True)
    fp = d / f"{aid}{ext}"
    fp.write_bytes(raw)
    meta = {"id": aid, "filename": name, "ext": ext, "bytes": len(raw),
            "slot": slot, "playable": slot == "audio",
            "seconds": _probe_seconds(fp) if slot == "audio" else 0.0,
            "uploaded_at": _now()}
    old = None
    with _LOCK:
        w = _load(wid)
        st2 = _find(w.get("stories") or [], sid, "story")
        files = _narr_files(st2)
        old = files.get(slot)
        files[slot] = meta
        st2["narration_files"] = files
        st2.pop("narration_audio", None)          # migrated into the slots
        st2["updated_at"] = _now()
        _save(w)
    if old and old.get("id") != aid:
        _slot_fp(wid, old).unlink(missing_ok=True)
    return {"slot": slot, "file": meta, "files": files}


@router.get("/worlds/{wid}/stories/{sid}/narration/file/{slot}")
async def get_story_narration_file(wid: str, sid: str, slot: str,
                                   download: bool = False):
    st = _find(_load(wid).get("stories") or [], sid, "story")
    meta = _narr_files(st).get(slot)
    if not meta:
        raise HTTPException(404, f"this story has no {slot} file")
    fp = _slot_fp(wid, meta)
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


@router.post("/worlds/{wid}/stories/{sid}/narration/file/{slot}/delete")
async def delete_story_narration_file(wid: str, sid: str, slot: str):
    meta = None
    with _LOCK:
        w = _load(wid)
        st = _find(w.get("stories") or [], sid, "story")
        files = _narr_files(st)
        meta = files.pop(slot, None)
        st["narration_files"] = files
        st.pop("narration_audio", None)
        st["updated_at"] = _now()
        _save(w)
    if meta:
        _slot_fp(wid, meta).unlink(missing_ok=True)
    return {"deleted": bool(meta), "slot": slot}


@router.post("/worlds/{wid}/stories/{sid}/narration/audio")
async def upload_story_narration_audio(wid: str, sid: str,
                                       file: UploadFile = File(...)):
    """🎙 Upload the narration RECORDING for this story.

    His flow: write the narration → read it (or have a TTS read it) → **listen
    to it here** and decide it is good → only then pull it into a project. The
    review step needed the audio to live with the story, not inside a project
    that may not exist yet."""
    _load(wid)
    st_ = _find(_load(wid).get("stories") or [], sid, "story")
    name = (file.filename or "narration").strip()
    ext = ("." + name.rsplit(".", 1)[-1].lower()) if "." in name else ""
    if ext not in _NARR_AUDIO_EXT:
        raise HTTPException(400, "upload a wav, mp3, m4a, aac, flac, ogg — or "
                                 "an .aaf timeline")
    raw = await file.read()
    if not raw:
        raise HTTPException(400, "empty file")
    aid = uuid4().hex[:10]
    d = _NARR_DIR / wid
    d.mkdir(parents=True, exist_ok=True)
    fp = d / f"{aid}{ext}"
    fp.write_bytes(raw)
    meta = {"id": aid, "filename": name, "ext": ext, "bytes": len(raw),
            "playable": _NARR_AUDIO_EXT[ext],
            "seconds": _probe_seconds(fp) if _NARR_AUDIO_EXT[ext] else 0.0,
            "uploaded_at": _now()}
    # ⚠ .31: this legacy route now files into the correct SLOT — an .aaf
    # uploaded here is a TIMELINE and belongs in `aaf`, not in `audio`, or the
    # project has "audio" it can never analyze (exactly what he hit).
    slot = "aaf" if ext == ".aaf" else "audio"
    meta["slot"] = slot
    old = None
    with _LOCK:
        w = _load(wid)
        st2 = _find(w.get("stories") or [], sid, "story")
        files = _narr_files(st2)
        old = files.get(slot)
        files[slot] = meta
        st2["narration_files"] = files
        st2.pop("narration_audio", None)
        st2["updated_at"] = _now()
        _save(w)
    if old and old.get("id") != aid:
        _slot_fp(wid, old).unlink(missing_ok=True)
    return {"audio": meta, "slot": slot, "story": st_.get("title")}


@router.get("/worlds/{wid}/stories/{sid}/narration/audio")
async def get_story_narration_audio(wid: str, sid: str, download: bool = False):
    """Stream the recording so it can be auditioned right on the story."""
    w = _load(wid)
    st = _find(w.get("stories") or [], sid, "story")
    files = _narr_files(st)
    meta = files.get("audio") or files.get("aaf")
    if not meta:
        raise HTTPException(404, "this story has no narration recording")
    fp = _slot_fp(wid, meta)
    if not fp.exists():
        raise HTTPException(404, "the recording is missing on disk")
    from fastapi.responses import FileResponse
    mt = {".mp3": "audio/mpeg", ".wav": "audio/wav", ".flac": "audio/flac",
          ".m4a": "audio/mp4", ".aac": "audio/aac", ".ogg": "audio/ogg"}.get(
              meta.get("ext") or "", "application/octet-stream")
    if download or not meta.get("playable"):
        return FileResponse(str(fp), media_type=mt,
                            filename=meta.get("filename") or fp.name)
    return FileResponse(str(fp), media_type=mt)


@router.post("/worlds/{wid}/stories/{sid}/narration/audio/delete")
async def delete_story_narration_audio(wid: str, sid: str):
    with _LOCK:
        w = _load(wid)
        st = _find(w.get("stories") or [], sid, "story")
        files = _narr_files(st)
        meta = files.pop("audio", None) or files.pop("aaf", None)
        st["narration_files"] = files
        st.pop("narration_audio", None)
        st["updated_at"] = _now()
        _save(w)
    if meta:
        _slot_fp(wid, meta).unlink(missing_ok=True)
    return {"deleted": bool(meta)}


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
            # 🎬 structure it immediately: arcs are what a PROJECT turns into
            # chapters, so a story without them is a story a project cannot use
            try:
                r = await structure_story(wid, st["id"],
                                          ArcGenIn(llm=body.llm), session)
                steps.append(f"{_flat(row.get('title'), 40)}: "
                             f"{len(r.get('arcs') or [])} arcs")
            except HTTPException as e:
                steps.append(f"⚠ arcs for {_flat(row.get('title'), 40)}: {e.detail}")
            made_stories.append(st["id"])
        steps.append(f"stories: {len(made_stories)} written")

    # 3 — the cast (for the first new story, else the world)
    r3 = await generate_cast(wid, CastGenIn(
        story_id=made_stories[0] if made_stories else "",
        max_count=body.max_cast, direction=idea, llm=body.llm), session)
    steps.append(f"cast: {len(r3.get('made') or [])} proposed")
    # ⚠ every member landed on story #1 only. A world with three stories whose
    # whole cast belongs to the first one makes "the cast of THIS story"
    # meaningless — which is the thing a linked project asks for.
    if len(made_stories) > 1:
        try:
            n = await _assign_cast_stories(wid, made_stories, body.llm, session)
            steps.append(f"cast↔story: {n} member(s) mapped")
        except HTTPException as e:
            steps.append(f"⚠ cast↔story: {e.detail}")

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


def _render_prompt_set(disp, model: str, prompts: List[str], style_txt: str,
                       ref_path: Optional[Path], out_dir: Path, st: dict,
                       log, seed_base: int = 41000, w: int = 1024,
                       h: int = 576, ref_mode: str = "style") -> List[dict]:
    """Render one prompt per image across the fleet. Shared by 🎨 world style
    samples and 📍 location plates — one implementation, three paths:

      style REF present → Klein edit citing "image 1" (positional, v1.276.x)
      krea2             → forge's lane, which DISCOVERS the unet per host
                          ⚠⚠ Krea 2 NEVER uses the generic t2i workflow file:
                          the unet baked into KREA2_TURBO_T2I.json is not what
                          is installed and every box 400s the raw graph.
      otherwise         → the plain t2i pool, workers assigned ROUND-ROBIN UP
                          FRONT (asking for "a worker" per image PINS them all
                          to one box — v1.276.45).
    """
    count = len(prompts)
    made: List[dict] = []
    out_dir.mkdir(parents=True, exist_ok=True)
    if ref_path is not None:
        from backend.api.klein3 import _parallel_klein_edits
        # ⚠⚠ TWO DIFFERENT JOBS WEAR THE SAME SHAPE. A world STYLE reference
        # means "draw this in that style"; a location's OWN photograph means
        # "this is the place — show me another view of IT". Feeding the second
        # into the first's prompt asks Klein to copy the photograph's rendering
        # and invent a new place, which is the opposite of a reference sheet
        # (found by the v1.277.28 doc audit — the docs claimed the behaviour
        # the code did not have).
        def _p(i: int) -> str:
            if ref_mode == "subject":
                return (f"Image 1 is the PLACE itself. Render another view of "
                        f"that same place, keeping its architecture, materials, "
                        f"layout and light: {prompts[i]}."
                        + (f" {style_txt}." if style_txt else "")
                        + " A clean unlettered image.")
            # affirmative: describe a clean plate rather than forbidding
            # captions — at cfg 1 a "no text" instruction paints text
            return (f"In the exact artistic style of image 1 "
                    f"— {style_txt or 'that style'} — draw: {prompts[i]}. "
                    f"A clean unlettered image.")
        jobs = [{"key": str(i),
                 "prompt": _p(i),
                 "refs": [ref_path], "w": w, "h": h,
                 "seed": seed_base + i} for i in range(count)]

        def _on_result(jb: dict, data: bytes):
            sid = uuid4().hex[:10]
            (out_dir / f"{sid}.png").write_bytes(data)
            task = (st.get("tasks") or {}).get(jb["key"]) or {}
            made.append({"id": sid, "prompt": jb["prompt"],
                         "model": ("klein+locationref" if ref_mode == "subject"
                                   else "klein+styleref"),
                         "worker": task.get("worker"), "created_at": _now()})
            st["done"] = int(st.get("done") or 0) + 1
            log(f"image {st['done']}/{count} done")
            return None

        log(f"rendering {count} via klein with the style reference")
        _parallel_klein_edits(disp, jobs, _on_result, st)
        return made

    from concurrent.futures import ThreadPoolExecutor
    if model == "krea2":
        from backend.api.forge import (_krea2_core_graph, _krea2_hosts_for,
                                       _krea2_render)
        hosts = _krea2_hosts_for(None, disp)
        if not hosts:
            raise RuntimeError("no Krea 2 capable worker online")
        st["workers"] = sorted(hosts)
        log(f"rendering {count} via forge's krea2 lane across "
            f"{len(hosts)} worker(s)")

        def _one_k2(i: int) -> None:
            host = hosts[i % len(hosts)]
            try:
                pr = f"{prompts[i]}, {style_txt}. A clean unlettered image."
                g = _krea2_core_graph(host, pr, w, h, seed_base + i, None, 1.0)
                data = _krea2_render(host, g, 300)
                sid = uuid4().hex[:10]
                (out_dir / f"{sid}.png").write_bytes(data)
                made.append({"id": sid, "prompt": pr, "model": "krea2",
                             "worker": host, "created_at": _now()})
            except Exception as e:                               # noqa: BLE001
                log(f"image #{i + 1} failed on {host}: {e}")
            st["done"] = int(st.get("done") or 0) + 1
            log(f"image {st['done']}/{count} finished")

        with ThreadPoolExecutor(max_workers=max(1, len(hosts))) as ex:
            list(ex.map(_one_k2, range(count)))
        return made

    from backend.api.tools import (_images_from_outputs,
                                   _prepare_sample_workflow,
                                   _run_prompt_blocking, _sample_worker_pool)
    pool = _sample_worker_pool(disp, model)
    if not pool:
        raise RuntimeError("no image worker online")
    st["workers"] = sorted({u for u, _c in pool})
    log(f"rendering {count} via {model} across {len(st['workers'])} worker(s)")

    def _one(i: int) -> None:
        url, client = pool[i % len(pool)]
        try:
            pr = f"{prompts[i]}, {style_txt}. A clean unlettered image."
            wf = _prepare_sample_workflow(model, pr, "", w, h, seed_base + i)
            outputs, _pid = _run_prompt_blocking(client, wf, 300)
            imgs = _images_from_outputs(outputs)
            if not imgs:
                raise RuntimeError("no image produced")
            pick = imgs[-1]
            data = client.download_output(pick["filename"],
                                          pick.get("subfolder", ""),
                                          pick.get("type", "output"))
            sid = uuid4().hex[:10]
            (out_dir / f"{sid}.png").write_bytes(data)
            made.append({"id": sid, "prompt": pr, "model": model,
                         "worker": url, "created_at": _now()})
        except Exception as e:                                   # noqa: BLE001
            log(f"image #{i + 1} failed on {url}: {e}")
        st["done"] = int(st.get("done") or 0) + 1
        log(f"image {st['done']}/{count} finished")

    with ThreadPoolExecutor(max_workers=max(1, len(pool))) as ex:
        list(ex.map(_one, range(count)))
    return made


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

    try:
        made = _render_prompt_set(disp, model, prompts, style_txt, ref_path,
                                  _SAMPLE_DIR / wid, st, _log, seed_base=41000)
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
