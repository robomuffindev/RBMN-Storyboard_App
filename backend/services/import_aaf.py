"""Parse an AAF (Advanced Authoring Format) timeline — e.g. exported from
ElevenLabs Dubbing Studio — into scene boundaries.

AAF is a binary structured-storage (CFBF) container, parsed here with the
pure-Python ``pyaaf2`` library (``pip install pyaaf2``, ``import aaf2``).  We walk
the top-level composition's audio timeline slots, accumulate each component's
length in *edit units*, and convert to seconds via the slot's ``edit_rate``.
Fillers are gaps (silence) and must be counted; Transitions overlap (subtract).

The pure timeline math (``clips_to_scenes``) is separated from the pyaaf2
traversal so it can be unit-tested without a real AAF file.
"""
from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)


class AafImportError(Exception):
    """Raised for any AAF parse problem (missing lib, no audio, etc.)."""


def clips_to_scenes(
    clips: list[dict],
    audio_end: Optional[float] = None,
    min_scene_seconds: float = 0.0,
) -> list[dict]:
    """Turn a flat list of timeline clips into contiguous, non-overlapping
    scenes by cutting at every clip START.

    Each clip is ``{"start": s, "end": e, "name": str}`` (seconds).  Multiple
    audio tracks (e.g. one per speaker) may overlap; cutting at clip starts
    yields a clean single-track timeline.  Scene *i* spans ``[starts[i],
    starts[i+1])``; the last scene runs to the max clip end (or ``audio_end``).

    ``min_scene_seconds`` optionally merges cut points closer than the floor
    (0 = faithful, no merging).
    """
    pts = sorted({round(float(c["start"]), 4) for c in clips if c.get("start") is not None})
    if not pts:
        return []
    max_end = max((float(c["end"]) for c in clips if c.get("end") is not None), default=pts[-1])
    if audio_end and audio_end > 0:
        max_end = max(max_end, float(audio_end))

    # name lookup: the (first) clip starting at each cut point
    name_at: dict[float, str] = {}
    for c in sorted(clips, key=lambda x: float(x.get("start") or 0)):
        s = round(float(c["start"]), 4)
        if s not in name_at and (c.get("name") or "").strip():
            name_at[s] = str(c["name"]).strip()

    # optional merge of cut points that are too close together
    if min_scene_seconds and min_scene_seconds > 0:
        merged = [pts[0]]
        for p in pts[1:]:
            if p - merged[-1] >= min_scene_seconds:
                merged.append(p)
        pts = merged

    scenes: list[dict] = []
    for i, start in enumerate(pts):
        end = pts[i + 1] if i + 1 < len(pts) else max_end
        if end <= start:
            continue
        nm = name_at.get(start) or f"Scene {len(scenes) + 1}"
        scenes.append({"start_time": float(start), "end_time": float(end), "name": nm[:120]})
    return scenes


# Source-mob / track names that carry no useful information — treat as unnamed so
# scenes fall back to "Scene N" (ElevenLabs puts the dialogue text in its CSV
# export, NOT in the AAF; AAF clip names are generic like "Render").
_GENERIC_NAMES = {
    "", "render", "track", "sequence", "sourcemob", "mastermob",
    "compositionmob", "mob", "unnamed", "audio", "clip", "essence",
}


def _clean_name(n) -> str:
    sv = (n or "")
    sv = str(sv).strip()
    return "" if sv.lower() in _GENERIC_NAMES else sv


def parse_aaf_clips(aaf_path: str) -> list[dict]:
    """Extract audio clips from an AAF as ``[{start, end, name}]`` in seconds.

    Raises ``AafImportError`` if pyaaf2 is unavailable or the file has no audio
    timeline.
    """
    try:
        import aaf2  # type: ignore
        from aaf2 import components as aaf_components  # type: ignore
    except Exception as e:  # pragma: no cover - depends on optional dep
        raise AafImportError(
            "AAF support requires the 'pyaaf2' package. Install it in the backend "
            "environment with: pip install pyaaf2"
        ) from e

    clips: list[dict] = []
    try:
        with aaf2.open(aaf_path, "r") as f:
            # Locate the composition(s) holding the timeline.  pyaaf2's
            # ``content.toplevel()`` can return NOTHING for some real exports
            # (observed on ElevenLabs Dubbing Studio AAFs), so fall back to
            # scanning all mobs for a CompositionMob, then for any mob with a
            # Sound Sequence track.
            comps = list(f.content.toplevel())
            if not comps:
                comps = [m for m in f.content.mobs if type(m).__name__ == "CompositionMob"]
            if not comps:
                comps = [
                    m for m in f.content.mobs
                    if any(
                        getattr(s, "media_kind", None) == "Sound"
                        and isinstance(getattr(s, "segment", None), aaf_components.Sequence)
                        for s in getattr(m, "slots", [])
                    )
                ]
            if not comps:
                raise AafImportError("AAF has no composition with an audio timeline.")

            for comp in comps:
                for slot in comp.slots:
                    # Only audio timeline tracks; skip timecode / picture / event slots.
                    if getattr(slot, "media_kind", None) != "Sound":
                        continue
                    seg = getattr(slot, "segment", None)
                    if not isinstance(seg, aaf_components.Sequence):
                        continue
                    edit_rate = float(slot.edit_rate)
                    if edit_rate <= 0:
                        continue
                    pos = 0  # running position in edit units

                    for comp_obj in seg.components:
                        length = int(getattr(comp_obj, "length", 0) or 0)
                        if isinstance(comp_obj, aaf_components.Filler):
                            pos += length
                            continue
                        if isinstance(comp_obj, aaf_components.Transition):
                            pos -= length  # adjacent clips overlap
                            continue
                        if isinstance(comp_obj, aaf_components.SourceClip):
                            start_s = pos / edit_rate
                            end_s = (pos + length) / edit_rate
                            name = ""
                            try:
                                ref_mob = comp_obj.mob
                                if ref_mob is not None:
                                    name = _clean_name(getattr(ref_mob, "name", None))
                            except Exception:
                                pass
                            # NB: we deliberately do NOT fall back to the track
                            # name — it's uniform across the track, so it would
                            # label every scene identically.  Empty name → the
                            # scene becomes "Scene N" downstream.
                            clips.append({"start": start_s, "end": end_s, "name": name})
                            pos += length
                            continue
                        # OperationGroup / NestedScope / Selector: advance by length
                        pos += length
    except AafImportError:
        raise
    except Exception as e:
        raise AafImportError(f"Failed to read AAF: {e}") from e

    if not clips:
        raise AafImportError("No audio clips found in the AAF timeline.")
    return clips


def parse_aaf_to_scenes(
    aaf_path: str,
    audio_end: Optional[float] = None,
    min_scene_seconds: float = 0.0,
) -> list[dict]:
    """Parse an AAF file into scene rows ``[{start_time, end_time, name}]``."""
    clips = parse_aaf_clips(aaf_path)
    scenes = clips_to_scenes(clips, audio_end=audio_end, min_scene_seconds=min_scene_seconds)
    if not scenes:
        raise AafImportError("AAF parsed but produced no usable scene boundaries.")
    return scenes
