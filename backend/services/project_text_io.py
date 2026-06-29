"""Project text-data import/export service.

Pure logic for serializing a project's editable text data (concept,
characters, chapters, scenes, prompts, story-flow ideas, transitions,
etc.) into JSON, and applying an edited JSON payload back onto the
project.  The endpoints in `backend/api/projects.py` are thin wrappers.

Schema version: 1.0

Cross-references in the JSON:
  - chapters carry an `order` integer (1, 2, 3 ...) by playback start time
  - scenes reference their chapter by `chapter_order`
  - sub-chapters reference their parent by `parent_chapter_order`
  - character references in scenes use the character `name` string
  - asset UUIDs / file paths are NOT exported (those are generated state)

Mode behavior:
  - narration_images: video_* / lipsync_* / transition_* fields are
    omitted from the export and ignored on import
  - narration_video / music_video: full schema

Import modes:
  - "override":   replace every matching field with the imported value
  - "fill_missing": only write a field when the current value is empty
    (empty string, None, or empty list).  Numbers are always considered
    "filled in" since 0 is a real value.
"""
from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from sqlmodel import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database.models import (
    Project, Scene, Chapter, Lyrics,
)

logger = logging.getLogger(__name__)

SCHEMA_VERSION = "1.0"

# Field groups that only apply to modes that render video.
_VIDEO_ONLY_SCENE_FIELDS = (
    "video_prompt",
    "last_frame_prompt",
    "last_frame_negative_prompt",
    "video_mode",
    "lipsync_enabled",
    "vocals_only_for_lipsync",
    "transition_in",
    "transition_out",
)

# Opaque/advanced per-scene config that should survive a text export→import
# round-trip but is NOT meant for human hand-editing (carried under a single
# `advanced_params` block per scene rather than as readable fields).
_ADVANCED_SCENE_PARAM_KEYS = (
    "ltx_director",                 # LTX Director Mode timeline config
    "llm_instruction_image",        # per-scene Enhance direction (image)
    "llm_instruction_video",        # per-scene Enhance direction (video)
    "vision_describe_refs",         # per-scene vision-describe override
    "json_prompt_mode",             # Ideogram JSON-prompt toggle
    "json_prompt",                  # Ideogram JSON-prompt payload
    "lf_exclude_first_frame_ref",   # last-frame "don't reference FF" toggle
)


def _project_is_narration(mode: str) -> bool:
    return mode in ("narration_video", "narration_images")


def _project_renders_video(mode: str) -> bool:
    return mode in ("music_video", "narration_video")


def _extract_scene_text(start_s: float, end_s: float, words: list[dict]) -> str:
    """Concatenate Whisper word strings that fall inside [start, end].

    Used to populate per-scene narration_text / lyrics_text in the export
    so the LLM agent sees what's being spoken / sung at that moment.
    Returns "" when no words overlap (instrumental / silence segment).
    """
    if not words or end_s <= start_s:
        return ""
    parts: list[str] = []
    for w in words:
        try:
            ws = float(w.get("start", 0) or 0)
            we = float(w.get("end", ws) or ws)
        except (TypeError, ValueError):
            continue
        # Include the word if it overlaps the scene's time range at all
        if we < start_s or ws > end_s:
            continue
        token = (w.get("word") or "").strip()
        if token:
            parts.append(token)
    return " ".join(parts).strip()


def _is_empty(v: Any) -> bool:
    """True when a field is 'unfilled' for fill_missing semantics."""
    if v is None:
        return True
    if isinstance(v, str) and not v.strip():
        return True
    if isinstance(v, (list, dict)) and not v:
        return True
    return False


# ── Export ──────────────────────────────────────────────────────────


async def build_export(project: Project, session: AsyncSession) -> dict:
    """Build the canonical JSON export for a project.

    Returns a plain dict that is JSON-serializable.  Mode-specific
    fields (video_prompt etc.) are omitted for narration_images mode.
    """
    mode = str(project.mode.value if hasattr(project.mode, "value") else project.mode)
    settings = project.settings or {}
    renders_video = _project_renders_video(mode)

    # ── Lyrics (one per project) ─────────────────────────────────
    lyr_row = (await session.execute(
        select(Lyrics).where(Lyrics.project_id == project.id)
    )).scalars().first()
    lyrics_source = {
        "initial_text": (getattr(lyr_row, "initial_text", "") or "") if lyr_row else "",
        "language": (getattr(lyr_row, "language", None) or "en") if lyr_row else "en",
    }
    full_lyrics_text = (lyr_row.full_text if lyr_row else "") or ""
    # Word-level timestamps for per-scene text extraction (used below
    # to populate narration_text / lyrics_text on each scene so the
    # LLM agent sees what's being spoken / sung at that moment).
    lyrics_words = list(getattr(lyr_row, "words", []) or []) if lyr_row else []

    # ── Chapters ─────────────────────────────────────────────────
    chapters_rows = (await session.execute(
        select(Chapter)
        .where(Chapter.project_id == project.id)
        .order_by(Chapter.start_time)
    )).scalars().all()
    chapter_id_to_order: dict[UUID, int] = {}
    chapters_out: list[dict] = []
    for i, ch in enumerate(chapters_rows, start=1):
        chapter_id_to_order[ch.id] = i
        chapters_out.append({
            "order": i,
            "name": ch.name,
            "color": ch.color or "",
            "description": getattr(ch, "description", "") or "",
            "character_focus": list(getattr(ch, "character_focus", []) or []),
            "style_notes": getattr(ch, "style_notes", "") or "",
            "depth": ch.depth,
            "parent_chapter_order": chapter_id_to_order.get(ch.parent_chapter_id) if ch.parent_chapter_id else None,
            "start_time": ch.start_time,
            "end_time": ch.end_time,
        })

    # ── Scenes ───────────────────────────────────────────────────
    scenes_rows = (await session.execute(
        select(Scene).where(Scene.project_id == project.id).order_by(Scene.order_index)
    )).scalars().all()

    # Build a quick map from character image_path -> name so we can
    # translate scene.parameters.reference_asset_ids (or per-frame
    # character indices) back to character names.  In practice the
    # cleanest source of truth is the characters list, indexed by name.
    project_characters = list(settings.get("characters", []) or [])
    char_names_in_order = [c.get("name", "") for c in project_characters]

    def _refs_to_names(refs: dict | list | None) -> list[str]:
        """Take a per-frame refs object {characterIndices: [...], extras: [...]}
        OR a flat list of indices and return character names.  Extras are
        opaque asset UUIDs and are NOT included (they're not text data)."""
        if not refs:
            return []
        if isinstance(refs, dict):
            idxs = refs.get("characterIndices", []) or []
        else:
            idxs = list(refs)
        names = []
        for i in idxs:
            try:
                idx = int(i)
            except (TypeError, ValueError):
                continue
            if 0 <= idx < len(char_names_in_order):
                names.append(char_names_in_order[idx])
        return names

    scenes_out: list[dict] = []
    for sc in scenes_rows:
        params = sc.parameters or {}
        scene_dict: dict[str, Any] = {
            "order_index": sc.order_index,
            "name": sc.name,
            "start_time": sc.start_time,
            "end_time": sc.end_time,
            "chapter_order": chapter_id_to_order.get(sc.chapter_id) if sc.chapter_id else None,

            "image_prompt": sc.prompt or "",
            "negative_prompt": sc.negative_prompt or "",
            "flow_idea": params.get("flow_idea", "") or "",

            "character_refs_first": _refs_to_names(params.get("image_refs_first")),
            "character_refs_last": _refs_to_names(params.get("image_refs_last")),

            "two_pass_enabled": bool(params.get("two_pass_enabled", False)),
            "color_override": params.get("color_override") or None,
            "custom_color_palette": params.get("custom_color_palette") or None,

            "image_movement": params.get("image_movement") or None,
            "use_story_flow": bool(params.get("use_story_flow", False)),

            "override_resolution": bool(params.get("override_resolution", False)),
            "width": int(params.get("width") or 0) or None,
            "height": int(params.get("height") or 0) or None,
        }
        if renders_video:
            scene_dict.update({
                "video_prompt": params.get("video_prompt", "") or "",
                "last_frame_prompt": params.get("last_frame_prompt", "") or "",
                "last_frame_negative_prompt": params.get("last_frame_negative_prompt", "") or "",
                "video_mode": params.get("video_mode") or "single",
                "lipsync_enabled": bool(params.get("lipsync_enabled", True)),
                "vocals_only_for_lipsync": bool(params.get("vocals_only_for_lipsync", False)),
                "transition_in": params.get("transition_in") or None,
                "transition_out": params.get("transition_out") or None,
            })

        # Preserve opaque/advanced per-scene config so a text round-trip keeps
        # Director Mode, LLM instructions, vision / JSON-prompt toggles, etc.
        _adv = {
            k: params[k] for k in _ADVANCED_SCENE_PARAM_KEYS
            if params.get(k) not in (None, "", {}, [])
        }
        if _adv:
            scene_dict["advanced_params"] = _adv

        # Per-scene narration / lyrics text for context.  The LLM agent
        # uses this as the ground truth for what to visualize in this
        # scene.  Falls back to empty string when no Whisper words exist
        # or none overlap the scene's time range.
        scene_text_field = "narration_text" if _project_is_narration(mode) else "lyrics_text"
        scene_dict[scene_text_field] = _extract_scene_text(
            sc.start_time or 0.0, sc.end_time or 0.0, lyrics_words,
        )

        scenes_out.append(scene_dict)

    # ── Concept block ────────────────────────────────────────────
    title_key = "production_title" if _project_is_narration(mode) else "song_title"
    concept_out = {
        title_key: settings.get("song_title", "") or "",
        "concept_text": settings.get("concept_text", "") or "",
        "style_text": settings.get("style_text", "") or "",
        "image_direction": settings.get("image_direction", "") or "",
        "custom_image_direction": settings.get("custom_image_direction", "") or "",
        "global_color_override": settings.get("global_color_override", "") or "",
        "custom_color_palette": settings.get("custom_color_palette", "") or "",
    }
    # Resolution split (1.8.x)
    resolution_out = {
        "resolution_width": int(settings.get("resolution_width", 1536) or 1536),
        "resolution_height": int(settings.get("resolution_height", 864) or 864),
        "image_resolution_width": int(settings.get("image_resolution_width", 0) or 0),
        "image_resolution_height": int(settings.get("image_resolution_height", 0) or 0),
        "video_resolution_width": int(settings.get("video_resolution_width", 0) or 0),
        "video_resolution_height": int(settings.get("video_resolution_height", 0) or 0),
    }

    # ── Characters ───────────────────────────────────────────────
    characters_out = [
        {"name": c.get("name", "") or "", "description": c.get("description", "") or ""}
        for c in project_characters
    ]

    return {
        "rbmn_format_version": SCHEMA_VERSION,
        "project_mode": mode,
        "project_name": project.name,
        "concept": concept_out,
        "resolution": resolution_out,
        "characters": characters_out,
        "chapters": chapters_out,
        "scenes": scenes_out,
        "lyrics_source": lyrics_source,
        "_meta": {
            "full_transcription_chars": len(full_lyrics_text),
            "exported_schema_version": SCHEMA_VERSION,
        },
    }


# ── Import ──────────────────────────────────────────────────────────


class ImportError(Exception):
    """Raised when an import payload is malformed or incompatible."""


def _validate_payload(payload: dict, current_mode: str, accept_mode_mismatch: bool) -> None:
    if not isinstance(payload, dict):
        raise ImportError("payload must be a JSON object")
    ver = payload.get("rbmn_format_version")
    if ver != SCHEMA_VERSION:
        raise ImportError(
            f"Unknown schema version {ver!r} (expected {SCHEMA_VERSION!r}). "
            "Download a fresh example file from the dialog."
        )
    pmode = payload.get("project_mode")
    if pmode and pmode != current_mode and not accept_mode_mismatch:
        raise ImportError(
            f"Imported project_mode ({pmode!r}) does not match the current "
            f"project mode ({current_mode!r}). Check 'Accept mode mismatch' to proceed."
        )
    for key in ("concept", "chapters", "scenes", "characters"):
        if key in payload and not isinstance(payload[key], (list, dict)):
            raise ImportError(f"top-level field {key!r} has wrong type")


def _maybe_write(target: dict, key: str, value: Any, mode: str) -> bool:
    """Write into target[key] respecting fill_missing semantics.

    Returns True when the value was written.
    """
    if mode == "override":
        target[key] = value
        return True
    # fill_missing: only write when current target value is empty
    cur = target.get(key)
    if _is_empty(cur):
        target[key] = value
        return True
    return False


async def apply_import(
    project: Project,
    session: AsyncSession,
    payload: dict,
    mode: str = "fill_missing",
    accept_mode_mismatch: bool = False,
) -> dict:
    """Apply an import payload to the project.

    `mode` is one of "override" or "fill_missing".

    Returns a stats dict for the response.
    """
    if mode not in ("override", "fill_missing"):
        raise ImportError(f"unknown import mode {mode!r}")
    current_mode = str(project.mode.value if hasattr(project.mode, "value") else project.mode)
    _validate_payload(payload, current_mode, accept_mode_mismatch)
    renders_video = _project_renders_video(current_mode)

    stats = {
        "concept_fields_updated": 0,
        "characters_added": 0,
        "characters_updated": 0,
        "chapters_updated": 0,
        "scenes_updated": 0,
        "scenes_skipped_out_of_range": 0,
        "video_fields_dropped": 0,  # for narration_images
    }

    # ── Concept ──────────────────────────────────────────────────
    concept = payload.get("concept", {}) or {}
    settings = dict(project.settings or {})
    title_key = "production_title" if _project_is_narration(current_mode) else "song_title"
    if title_key in concept and _maybe_write(settings, "song_title", concept[title_key] or "", mode):
        stats["concept_fields_updated"] += 1
    for src_key, dst_key in (
        ("concept_text", "concept_text"),
        ("style_text", "style_text"),
        ("image_direction", "image_direction"),
        ("custom_image_direction", "custom_image_direction"),
        ("global_color_override", "global_color_override"),
        ("custom_color_palette", "custom_color_palette"),
    ):
        if src_key in concept and _maybe_write(settings, dst_key, concept[src_key] or "", mode):
            stats["concept_fields_updated"] += 1

    # Resolution block (1.8.x).  Resolution values 0 = "use unified".
    res = payload.get("resolution", {}) or {}
    for src_key in (
        "resolution_width", "resolution_height",
        "image_resolution_width", "image_resolution_height",
        "video_resolution_width", "video_resolution_height",
    ):
        if src_key in res:
            val = int(res[src_key] or 0)
            # 0 explicitly clears the setting so the read-side fallback fires
            if mode == "override":
                if val == 0 and src_key in settings:
                    settings.pop(src_key)
                else:
                    settings[src_key] = val
                    stats["concept_fields_updated"] += 1
            else:
                # fill_missing: only set when current is 0 / absent
                if not settings.get(src_key):
                    if val != 0:
                        settings[src_key] = val
                        stats["concept_fields_updated"] += 1

    # ── Characters ───────────────────────────────────────────────
    incoming_chars = payload.get("characters", []) or []
    existing_chars = list(settings.get("characters", []) or [])
    existing_by_lower = {(c.get("name", "") or "").strip().lower(): i for i, c in enumerate(existing_chars)}
    for inc in incoming_chars:
        if not isinstance(inc, dict):
            continue
        name = (inc.get("name", "") or "").strip()
        desc = inc.get("description", "") or ""
        if not name:
            continue
        key = name.lower()
        if key in existing_by_lower:
            idx = existing_by_lower[key]
            cur = existing_chars[idx]
            if mode == "override" or _is_empty(cur.get("description")):
                if cur.get("description", "") != desc:
                    cur["description"] = desc
                    existing_chars[idx] = cur
                    stats["characters_updated"] += 1
        else:
            existing_chars.append({"name": name, "description": desc, "image_path": None})
            existing_by_lower[key] = len(existing_chars) - 1
            stats["characters_added"] += 1
    settings["characters"] = existing_chars
    project.settings = settings

    # ── Chapters ─────────────────────────────────────────────────
    incoming_chapters = payload.get("chapters", []) or []
    existing_chapters = (await session.execute(
        select(Chapter).where(Chapter.project_id == project.id).order_by(Chapter.start_time)
    )).scalars().all()
    # Map order (1-indexed) -> chapter row
    order_to_chapter = {i + 1: ch for i, ch in enumerate(existing_chapters)}
    for inc in incoming_chapters:
        if not isinstance(inc, dict):
            continue
        order = inc.get("order")
        if order is None:
            continue
        ch_row = order_to_chapter.get(int(order))
        if not ch_row:
            # Chapter creation from import is out of scope for v1 since
            # chapter timing requires scene context.  Skip silently.
            continue
        touched = False
        if "description" in inc:
            cur = getattr(ch_row, "description", "") or ""
            if mode == "override" or _is_empty(cur):
                new = inc["description"] or ""
                if new != cur:
                    ch_row.description = new
                    touched = True
        if "character_focus" in inc:
            cur = list(getattr(ch_row, "character_focus", []) or [])
            new = list(inc["character_focus"] or [])
            if mode == "override" or not cur:
                if new != cur:
                    ch_row.character_focus = new
                    touched = True
        if "style_notes" in inc:
            cur = getattr(ch_row, "style_notes", "") or ""
            if mode == "override" or _is_empty(cur):
                new = inc["style_notes"] or ""
                if new != cur:
                    ch_row.style_notes = new
                    touched = True
        if "name" in inc and mode == "override":
            new = inc["name"] or ch_row.name
            if new != ch_row.name:
                ch_row.name = new
                touched = True
        if "color" in inc and mode == "override":
            new = inc["color"] or ch_row.color
            if new and new != ch_row.color:
                ch_row.color = new
                touched = True
        if touched:
            from sqlalchemy.orm.attributes import flag_modified
            flag_modified(ch_row, "character_focus")
            stats["chapters_updated"] += 1

    # ── Scenes ───────────────────────────────────────────────────
    incoming_scenes = payload.get("scenes", []) or []
    existing_scenes = (await session.execute(
        select(Scene).where(Scene.project_id == project.id).order_by(Scene.order_index)
    )).scalars().all()
    order_to_scene = {sc.order_index: sc for sc in existing_scenes}
    char_names_lower = {(c.get("name", "") or "").strip().lower(): i for i, c in enumerate(existing_chars)}

    def _names_to_refs(names: list, existing_refs: dict | None) -> dict:
        """Build a {characterIndices, extras} refs dict from a list of
        character names.  Preserves existing `extras` (those are opaque
        asset UUIDs that the LLM shouldn't see)."""
        idxs = []
        for n in (names or []):
            if not isinstance(n, str):
                continue
            idx = char_names_lower.get(n.strip().lower())
            if idx is not None:
                idxs.append(idx)
        return {
            "characterIndices": idxs,
            "extras": (existing_refs or {}).get("extras", []) if isinstance(existing_refs, dict) else [],
        }

    for inc in incoming_scenes:
        if not isinstance(inc, dict):
            continue
        idx = inc.get("order_index")
        if idx is None:
            continue
        sc_row = order_to_scene.get(int(idx))
        if not sc_row:
            stats["scenes_skipped_out_of_range"] += 1
            continue

        params = dict(sc_row.parameters or {})
        touched_scalar = False
        # Top-level scene fields (prompt, negative_prompt)
        if "image_prompt" in inc:
            cur = sc_row.prompt or ""
            if mode == "override" or _is_empty(cur):
                new = inc["image_prompt"] or ""
                if new != cur:
                    sc_row.prompt = new
                    touched_scalar = True
        if "negative_prompt" in inc:
            cur = sc_row.negative_prompt or ""
            if mode == "override" or _is_empty(cur):
                new = inc["negative_prompt"] or ""
                if new != cur:
                    sc_row.negative_prompt = new
                    touched_scalar = True
        if "name" in inc and mode == "override":
            new = inc["name"] or sc_row.name
            if new != sc_row.name:
                sc_row.name = new
                touched_scalar = True

        # Parameter fields
        touched_params = False
        scalar_param_fields = (
            "flow_idea", "image_movement", "use_story_flow",
            "color_override", "custom_color_palette",
            "two_pass_enabled",
            # Per-scene resolution override (round-trip with the export).
            "override_resolution", "width", "height",
        )
        if renders_video:
            scalar_param_fields = scalar_param_fields + _VIDEO_ONLY_SCENE_FIELDS
        for fld in scalar_param_fields:
            if fld in inc:
                cur = params.get(fld)
                if mode == "override" or _is_empty(cur):
                    new = inc[fld]
                    if new != cur:
                        params[fld] = new
                        touched_params = True

        # Drop video fields silently for narration_images
        if not renders_video:
            for fld in _VIDEO_ONLY_SCENE_FIELDS:
                if fld in inc:
                    stats["video_fields_dropped"] += 1

        # Character refs by name -> per-frame refs dict
        if "character_refs_first" in inc:
            cur = params.get("image_refs_first")
            if mode == "override" or not (cur and (cur.get("characterIndices") if isinstance(cur, dict) else [])):
                params["image_refs_first"] = _names_to_refs(inc["character_refs_first"], cur if isinstance(cur, dict) else None)
                touched_params = True
        if "character_refs_last" in inc and renders_video:
            cur = params.get("image_refs_last")
            if mode == "override" or not (cur and (cur.get("characterIndices") if isinstance(cur, dict) else [])):
                params["image_refs_last"] = _names_to_refs(inc["character_refs_last"], cur if isinstance(cur, dict) else None)
                touched_params = True

        # Advanced/opaque per-scene config passthrough (round-trips Director Mode,
        # LLM instructions, vision / JSON-prompt toggles, etc.).
        _adv_in = inc.get("advanced_params")
        if isinstance(_adv_in, dict):
            for _k, _v in _adv_in.items():
                if _k not in _ADVANCED_SCENE_PARAM_KEYS:
                    continue
                if mode == "override" or _is_empty(params.get(_k)):
                    if params.get(_k) != _v:
                        params[_k] = _v
                        touched_params = True

        if touched_scalar or touched_params:
            sc_row.parameters = params
            from sqlalchemy.orm.attributes import flag_modified
            flag_modified(sc_row, "parameters")
            stats["scenes_updated"] += 1

    # ── Lyrics ───────────────────────────────────────────────────
    lyr_in = payload.get("lyrics_source", {}) or {}
    if lyr_in.get("initial_text"):
        lyr_row = (await session.execute(
            select(Lyrics).where(Lyrics.project_id == project.id)
        )).scalars().first()
        if lyr_row:
            cur = getattr(lyr_row, "initial_text", "") or ""
            if mode == "override" or _is_empty(cur):
                lyr_row.initial_text = lyr_in["initial_text"]

    await session.commit()
    logger.info(
        f"Project text-import applied to {project.id} (mode={mode}): "
        f"chapters={stats['chapters_updated']} scenes={stats['scenes_updated']} "
        f"characters_added={stats['characters_added']} characters_updated={stats['characters_updated']}"
    )
    return stats
