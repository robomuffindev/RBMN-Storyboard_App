"""Scene-boundary audit and SRT-preferred timing helpers.

Why this module exists
======================

A long narration project's scenes are authored from Whisper word timestamps
(or an SRT cue list) when the user clicks *Suggest Timeline*.  The DB then
stores ``scene.start_time`` / ``scene.end_time`` as the authoritative slice
each scene "owns" of the narration audio.  Downstream code uses those
ranges to:

* Slice Whisper words into per-scene narration text (the LLM prompt the
  image / video model gets).
* Decide how long each LTX 2.3 clip should render.
* Render burned-in subtitles aligned to playback.

That works perfectly the first time.  Two failure modes have been
observed in production and cause the "narration and concept drift apart"
symptom Lorenzo flagged in 1.8.13:

#. The user re-uploads the narration audio (e.g. a touched-up ElevenLabs
   render with different per-word timing) and re-runs Whisper, but never
   re-runs Suggest Timeline.  Old boundaries persist.
#. The user uploads an SRT *after* an early Whisper pass authored scenes.
   SRT cues are the ground-truth timing from ElevenLabs / the narrator,
   so they should TAKE OVER as the authoritative source — but scenes
   silently keep their Whisper-derived boundaries.

Both manifest the same way: the LLM prompt for scene N gets words from
~scene N+1's slice of audio, and over the course of a project the
generated images / videos drift further and further from what's actually
being said.

This module provides the primitives shared by the new audit endpoint, the
post-Whisper / post-SRT auto-resync, and any future "fix boundaries" UX:

* :func:`source_label` — tells you whether ``Lyrics.words`` came from
  Whisper or SRT (SRT-sourced words carry a ``block`` integer).
* :func:`cue_ranges` — when source is SRT, groups words by ``block`` so
  callers can snap scene endpoints to actual cue boundaries (which match
  natural narration phrasing better than mid-Whisper-phrase splits).
* :func:`audit_scene_boundaries` — returns the list of problems with the
  current scene set: out-of-bounds durations, scenes whose boundaries
  don't align with the source's natural breaks, and whether the source
  has been updated since the scenes were last authored.
* :func:`needs_auto_resync` — heuristic the post-transcription path uses
  to decide whether to silently re-run Suggest Timeline.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ── Tolerances ─────────────────────────────────────────────────────────────
# A scene boundary is considered "in sync" with the source if the closest
# word / cue boundary is within ±0.5s.  Most narration cues align within
# 50ms; the slack allows for ElevenLabs SRTs that round to centisecond
# precision plus a hair of audio-render latency.
BOUNDARY_TOLERANCE_S = 0.5

# When more than this fraction of scenes have stale boundaries, the
# auto-resync triggers.  Below the threshold we leave the project alone
# (the user may have manually fine-tuned just a few scenes).
RESYNC_THRESHOLD_FRACTION = 0.30


def source_label(words: list[dict[str, Any]]) -> str:
    """Return ``"srt"`` when at least one word carries a ``block`` index
    (the SRT-parser writes that on every word it ingests), otherwise
    ``"whisper"``.  ``""`` for an empty list."""
    if not words:
        return ""
    for w in words:
        if "block" in w and w["block"] is not None:
            return "srt"
    return "whisper"


def cue_ranges(words: list[dict[str, Any]]) -> list[tuple[int, float, float, str]]:
    """For SRT-sourced lyrics, group words by their ``block`` integer and
    return ``[(block_idx, start, end, text), ...]`` — the natural cue
    boundaries the user originally wrote.  Returns an empty list when the
    source is Whisper (Whisper has no concept of cues — the closest
    analogue is the phrase-clustering done at suggest-timeline time)."""
    if source_label(words) != "srt":
        return []
    by_block: dict[int, list[dict[str, Any]]] = {}
    for w in words:
        b = w.get("block")
        if b is None:
            continue
        by_block.setdefault(int(b), []).append(w)
    out: list[tuple[int, float, float, str]] = []
    for block_idx in sorted(by_block.keys()):
        ws = by_block[block_idx]
        if not ws:
            continue
        start = float(ws[0].get("start", 0.0) or 0.0)
        end = float(ws[-1].get("end", start) or start)
        text = " ".join(str(w.get("word", "")).strip() for w in ws).strip()
        out.append((block_idx, start, end, text))
    return out


def natural_break_points(words: list[dict[str, Any]]) -> list[float]:
    """Return a sorted list of "natural" timeline positions a scene
    boundary should ideally land on:

    * SRT source → every cue start and cue end (cue boundaries).
    * Whisper source → every gap >0.3s between consecutive words.

    Used by :func:`audit_scene_boundaries` to score how aligned each
    scene's start/end is with the underlying narration.
    """
    if not words:
        return []

    src = source_label(words)
    points: set[float] = set()

    if src == "srt":
        for _, s, e, _ in cue_ranges(words):
            points.add(round(s, 3))
            points.add(round(e, 3))
        return sorted(points)

    # Whisper: gap >300ms = phrase boundary.
    prev_end: Optional[float] = None
    for w in words:
        s = float(w.get("start", 0.0) or 0.0)
        e = float(w.get("end", s) or s)
        if prev_end is not None and (s - prev_end) > 0.3:
            points.add(round(prev_end, 3))
            points.add(round(s, 3))
        prev_end = e
    # Always include the very first word's start and last word's end so a
    # one-phrase project still has anchors.
    if words:
        points.add(round(float(words[0].get("start", 0.0) or 0.0), 3))
        points.add(round(float(words[-1].get("end", 0.0) or 0.0), 3))
    return sorted(points)


def closest_break(boundary: float, breaks: list[float]) -> tuple[float, float]:
    """Return ``(closest_break_position, abs_distance)`` for a given
    timeline second value against the list returned by
    :func:`natural_break_points`.  When ``breaks`` is empty, returns
    ``(boundary, float("inf"))`` so the caller knows there's nothing to
    snap to."""
    if not breaks:
        return boundary, float("inf")
    # Linear scan is fine — narration projects rarely have >2000 words.
    best = breaks[0]
    best_dist = abs(boundary - best)
    for p in breaks[1:]:
        d = abs(boundary - p)
        if d < best_dist:
            best = p
            best_dist = d
    return best, best_dist


def audit_scene_boundaries(
    scenes: list[Any],
    words: list[dict[str, Any]],
    min_duration: float,
    max_duration: float,
    tolerance: float = BOUNDARY_TOLERANCE_S,
) -> dict[str, Any]:
    """Audit every scene's start/end against the narration source.

    Returns a structured dict the API endpoint and the auto-resync code
    paths both consume.  Each scene gets:

    * ``id`` / ``name`` / ``order_index``
    * ``start_time`` / ``end_time`` / ``duration``
    * ``duration_status``: ``"ok" | "below_min" | "above_max"``
    * ``start_drift_s`` / ``end_drift_s``: distance from nearest natural
      break point in seconds.  ``None`` when no source words are
      available.
    * ``snap_suggestion``: ``{"start": float, "end": float}`` with the
      cue-aligned coordinates the resync would move the scene to (only
      filled when an SRT source is present — Whisper drift is too noisy
      to act on without user confirmation).

    Top-level keys:

    * ``source``: ``"srt" | "whisper" | ""``
    * ``total_scenes`` / ``stale_scenes`` / ``stale_fraction``
    * ``problems``: per-scene array (above)
    """
    src = source_label(words)
    breaks = natural_break_points(words)
    cue_idx = {round(s, 3): (s, e, t) for _, s, e, t in cue_ranges(words)} if src == "srt" else {}

    problems: list[dict[str, Any]] = []
    stale_count = 0

    for sc in scenes:
        st = float(getattr(sc, "start_time", 0.0) or 0.0)
        en = float(getattr(sc, "end_time", 0.0) or 0.0)
        dur = en - st

        if dur < min_duration - 1e-6:
            dur_status = "below_min"
        elif dur > max_duration + 1e-6:
            dur_status = "above_max"
        else:
            dur_status = "ok"

        start_break, start_dist = closest_break(st, breaks)
        end_break, end_dist = closest_break(en, breaks)
        start_drift = None if start_dist == float("inf") else round(start_dist, 3)
        end_drift = None if end_dist == float("inf") else round(end_dist, 3)

        is_stale = (
            (start_drift is not None and start_drift > tolerance) or
            (end_drift is not None and end_drift > tolerance) or
            dur_status != "ok"
        )
        if is_stale:
            stale_count += 1

        snap = None
        if src == "srt" and breaks:
            snap = {
                "start": round(start_break, 3),
                "end": round(end_break, 3),
            }

        problems.append({
            "id": str(getattr(sc, "id", "")),
            "name": getattr(sc, "name", "") or "",
            "order_index": int(getattr(sc, "order_index", 0) or 0),
            "start_time": round(st, 3),
            "end_time": round(en, 3),
            "duration": round(dur, 3),
            "duration_status": dur_status,
            "start_drift_s": start_drift,
            "end_drift_s": end_drift,
            "snap_suggestion": snap,
            "stale": is_stale,
        })

    total = len(scenes)
    return {
        "source": src,
        "total_scenes": total,
        "stale_scenes": stale_count,
        "stale_fraction": (stale_count / total) if total else 0.0,
        "min_duration": min_duration,
        "max_duration": max_duration,
        "tolerance_s": tolerance,
        "problems": problems,
    }


def needs_auto_resync(audit: dict[str, Any]) -> bool:
    """Decide whether the project should auto-rerun Suggest Timeline.

    Triggers when:

    * the source is SRT *and* the cue boundaries don't match scene
      boundaries (SRTs are authoritative, so any mismatch is wrong), OR
    * a Whisper source has produced staleness on more than
      :data:`RESYNC_THRESHOLD_FRACTION` of scenes (avoiding a re-segment
      when only a handful of scenes drift — could be intentional
      fine-tuning).
    """
    src = audit.get("source", "")
    if not src:
        return False
    stale_fraction = float(audit.get("stale_fraction", 0.0))
    if src == "srt":
        return stale_fraction > 0.0
    return stale_fraction >= RESYNC_THRESHOLD_FRACTION
