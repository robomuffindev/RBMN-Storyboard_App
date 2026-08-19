"""🎬 WRITE an AAF timeline from a cue list — the mirror of ``import_aaf``.

WHY THIS EXISTS
---------------
His workflow is **mp3 + srt + aaf**, arrived at after months in which every
Whisper/SRT-timed approach produced scenes that cut mid-word and drifted
further the longer the file ran (see ``CHANGELOG.md`` v1.8.14 → v1.8.22, and
the v1.8.20 entry: *"39 of 48 scenes ended mid-word … the offset growing to
~10s by the end"*). AAF import was what finally worked.

⭐ **The precision does not come from the file format.** It comes from the
boundaries being the edges of REAL AUDIO SEGMENTS rather than estimates of
where a word fell — an AAF clip start is a clip start. Our TTS has that same
property for a stronger reason: **each sentence is a separate rendered file**,
so a boundary cannot fall inside a word because the word is in a different
file. Both are structural guarantees; neither is a measurement.

So this writer is NOT how the app gets its scenes — ``storychapters.
_scenes_from_cues`` uses the cue list directly, and that path keeps the clip
ENDS, which an AAF round-trip discards (``import_aaf.clips_to_scenes`` cuts on
starts only). This exists because:

  1. an AAF is what he takes to an NLE, and
  2. a chapter's file set (audio · srt · **aaf**) is then complete, which is
     what the project pull already expects, and
  3. it is independently checkable: write it, read it back with OUR OWN
     parser, and prove the boundaries survive to the sample.

THE ONE DESIGN DECISION THAT MATTERS
------------------------------------
⭐⭐ **``edit_rate`` is the AUDIO SAMPLE RATE.** AAF positions are integers in
edit units; ``import_aaf`` accumulates them as integers and divides ONCE at the
end (``import_aaf.py:137-149``). Setting the edit rate to the sample rate makes
every cut point an exact sample position, so a round-trip is lossless rather
than merely close. Using 25 or 30 fps here would quantise every boundary to a
frame and re-introduce exactly the ±20 ms class of error this lane exists to
avoid.

⚠ TRAPS PAID FOR (proven by a real write→read round-trip through our parser):
  · ``SourceMob`` refuses to serialise without an ``EssenceDescription``
    (``AAFPropertyError``). Use a **``MasterMob``** as each clip's referenced
    mob — no descriptor needed, and it matches the ElevenLabs topology our
    importer was hardened against.
  · The clip NAME comes from the referenced mob's name, and ``import_aaf.
    _clean_name`` blanks a small set of generic names — real sentence text
    passes through and becomes the scene name.
  · Gaps must be ``Filler`` components; the importer counts their length
    (``import_aaf.py:141-143``). Leaving them out silently shifts everything
    after the first pause.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)


class AafExportError(RuntimeError):
    """Writing the AAF failed (missing pyaaf2, unusable cues, disk)."""


#: A name the importer would blank — see `import_aaf._GENERIC_NAMES`. If a cue's
#: text collapses to one of these the scene would come back as "Scene N", so we
#: keep a numbered fallback that is at least unique.
_MAX_NAME = 120


def cues_to_aaf(cues: List[dict], out_path: Path, *,
                sample_rate: int = 24000,
                title: str = "RBMN narration",
                total_seconds: Optional[float] = None) -> dict:
    """Write a timeline-only AAF: one Sound clip per cue, gaps as Filler.

    `cues` is `[{"start": s, "end": s, "text": str}, …]` in SECONDS — exactly
    the shape `audio_lab` records at render time.

    Returns a small report (`clips`, `bytes`, `edit_rate`, `seconds`).
    Raises `AafExportError` rather than half-writing a file.
    """
    try:
        import aaf2                                             # type: ignore
    except Exception as e:                                      # noqa: BLE001
        raise AafExportError(
            "AAF export needs the 'pyaaf2' package: pip install pyaaf2"
        ) from e

    rows = []
    for c in cues:
        try:
            s = float(c.get("start") or 0.0)
            e = float(c.get("end") or 0.0)
        except (TypeError, ValueError):
            continue
        if e <= s:
            continue
        rows.append((s, e, " ".join(str(c.get("text") or "").split())))
    if not rows:
        raise AafExportError("no usable cues — every one was empty or had "
                             "end <= start")
    rows.sort(key=lambda r: r[0])

    # ⭐ integers from here on. Positions are computed ONCE from seconds and
    # never added back into a float, which is the whole discipline.
    rate = int(sample_rate)
    units = [(int(round(s * rate)), int(round(e * rate)), t) for s, e, t in rows]

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_name(out_path.name + ".part")
    tmp.unlink(missing_ok=True)

    overlaps = 0
    try:
        with aaf2.open(str(tmp), "w") as f:
            comp = f.create.CompositionMob(title[:80] or "RBMN narration")
            f.content.mobs.append(comp)
            slot = comp.create_sound_slot(edit_rate=rate)
            seq = slot.segment

            pos = 0
            for i, (s_u, e_u, text) in enumerate(units):
                if s_u < pos:
                    # ⚠ a cue starting before the previous one ended would make
                    # the timeline go backwards. Clamp and COUNT it — silently
                    # dropping the overlap is how a timeline ends up shorter
                    # than the audio it describes.
                    overlaps += 1
                    s_u = pos
                if e_u <= s_u:
                    continue
                if s_u > pos:
                    seq.components.append(
                        f.create.Filler("sound", int(s_u - pos)))
                    pos = s_u
                length = int(e_u - s_u)
                # ⚠ MasterMob, not SourceMob — a SourceMob will not serialise
                # without an EssenceDescription.
                ref = f.create.MasterMob(
                    (text or f"Cue {i + 1}")[:_MAX_NAME] or f"Cue {i + 1}")
                f.content.mobs.append(ref)
                ref.create_sound_slot(edit_rate=rate)
                clip = f.create.SourceClip(media_kind="sound", length=length)
                clip.mob = ref
                clip.slot_id = 1
                clip.start = 0
                seq.components.append(clip)
                pos += length

            # trailing silence, so the AAF's length matches the real audio
            if total_seconds:
                tail = int(round(float(total_seconds) * rate)) - pos
                if tail > 0:
                    seq.components.append(f.create.Filler("sound", int(tail)))
                    pos += tail
    except AafExportError:
        raise
    except Exception as e:                                      # noqa: BLE001
        tmp.unlink(missing_ok=True)
        raise AafExportError(f"pyaaf2 failed to write the timeline: {e}") from e

    if not tmp.exists() or tmp.stat().st_size == 0:
        tmp.unlink(missing_ok=True)
        raise AafExportError("pyaaf2 produced no file")
    # ⚠ os.replace only AFTER the writer closed cleanly — a half-written AAF
    # that keeps the real filename is worse than no file at all.
    tmp.replace(out_path)
    if overlaps:
        logger.warning("cues_to_aaf: %d overlapping cue(s) were clamped in %s",
                       overlaps, out_path.name)
    return {"clips": len(units), "bytes": out_path.stat().st_size,
            "edit_rate": rate, "seconds": round(pos / float(rate), 6),
            "overlaps_clamped": overlaps}


def verify_roundtrip(aaf_path: Path, cues: List[dict],
                     tolerance_s: float = 0.001) -> dict:
    """Read the AAF back with OUR OWN importer and diff it against the cues.

    ⭐ This is the point of the exercise. *"It wrote a file"* is not evidence;
    *"our parser recovers every boundary to within a millisecond"* is. Called
    right after every export, and the result is stored with the file — a claim
    of sample-accuracy that nobody checked is exactly the kind of confident
    wrongness that cost him months.
    """
    from backend.services.import_aaf import parse_aaf_clips
    got = parse_aaf_clips(Path(aaf_path))
    want = [c for c in cues
            if float(c.get("end") or 0) > float(c.get("start") or 0)]
    out = {"clips_written": len(want), "clips_read": len(got),
           "max_start_err_s": 0.0, "max_end_err_s": 0.0,
           "names_kept": 0, "ok": False}
    if len(got) != len(want):
        out["note"] = (f"the parser read {len(got)} clips from a file written "
                       f"with {len(want)}")
        return out
    for g, w in zip(got, want):
        out["max_start_err_s"] = max(out["max_start_err_s"],
                                     abs(float(g["start"]) - float(w["start"])))
        out["max_end_err_s"] = max(out["max_end_err_s"],
                                   abs(float(g["end"]) - float(w["end"])))
        gn = " ".join(str(g.get("name") or "").split())
        wn = " ".join(str(w.get("text") or "").split())[:_MAX_NAME]
        if gn and wn and gn == wn:
            out["names_kept"] += 1
    out["max_start_err_s"] = round(out["max_start_err_s"], 6)
    out["max_end_err_s"] = round(out["max_end_err_s"], 6)
    out["ok"] = (out["max_start_err_s"] <= tolerance_s
                 and out["max_end_err_s"] <= tolerance_s)
    return out
