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
                                    # Best-effort: some producers embed the
                                    # spoken text in mob user comments rather
                                    # than the name (ElevenLabs currently does
                                    # NOT — its text ships in the CSV/SRT —
                                    # but read it when present).
                                    if not name:
                                        _cm = getattr(ref_mob, "comments", None)
                                        if _cm:
                                            for _cv in dict(_cm).values():
                                                _cand = _clean_name(_cv)
                                                if _cand:
                                                    name = _cand
                                                    break
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


def extract_aaf_embedded_audio(aaf_path: str, out_wav: str) -> bool:
    """Reconstruct the timeline audio from essence EMBEDDED in the AAF.

    ElevenLabs AAF exports embed each clip's rendered audio as essence
    (that's why the file is ~25x the size of the MP3 export).  We pull every
    SourceClip's essence out to a temp file, then rebuild the full-length
    track with ffmpeg by placing each clip at its exact timeline position
    over silence (adelay + amix, no normalization).

    Returns True when ``out_wav`` was written.  Timeline-only AAFs (no
    embedded essence) return False — the caller decides how to fail.
    Best-effort by design: any parse hiccup returns False rather than
    raising, so the import endpoint can fall back to a clear error.
    """
    import os
    import shutil
    import subprocess
    import tempfile
    import wave as wave_mod

    try:
        import aaf2  # type: ignore
        from aaf2 import components as C  # type: ignore
    except Exception:
        return False

    tmpdir = tempfile.mkdtemp(prefix="aaf_essence_")
    # (timeline_start_s, source_offset_s, duration_s, essence_path)
    placed: list[tuple[float, float, float, str]] = []
    try:
        with aaf2.open(aaf_path, "r") as f:
            ess_cache: dict[str, Optional[str]] = {}

            def _write_essence(smob) -> Optional[str]:
                key = str(getattr(smob, "mob_id", None) or id(smob))
                if key in ess_cache:
                    return ess_cache[key]
                path: Optional[str] = None
                try:
                    ed = getattr(smob, "essence", None)
                    if ed is not None:
                        stream = ed.open("r")
                        raw_path = os.path.join(tmpdir, f"ess_{len(ess_cache)}.bin")
                        with open(raw_path, "wb") as out:
                            while True:
                                try:
                                    chunk = stream.read(1 << 20)
                                except TypeError:
                                    out.write(stream.read())
                                    break
                                if not chunk:
                                    break
                                out.write(chunk)
                        with open(raw_path, "rb") as rf:
                            head = rf.read(4)
                        if head == b"RIFF":
                            path = raw_path + ".wav"
                            os.replace(raw_path, path)
                        elif head == b"FORM":
                            path = raw_path + ".aif"
                            os.replace(raw_path, path)
                        else:
                            # Raw PCM frames — wrap using the descriptor.
                            try:
                                desc = smob.descriptor
                                sr = int(float(desc["SampleRate"].value))
                                ch = int(desc["Channels"].value)
                                bits = int(desc["QuantizationBits"].value)
                                path = raw_path + ".wav"
                                with open(raw_path, "rb") as rf, wave_mod.open(path, "wb") as w:
                                    w.setnchannels(max(1, ch))
                                    w.setsampwidth(max(1, bits // 8))
                                    w.setframerate(sr)
                                    w.writeframes(rf.read())
                                os.unlink(raw_path)
                            except Exception:
                                path = None
                except Exception:
                    path = None
                ess_cache[key] = path
                return path

            def _resolve_clip_essence(comp_clip):
                """Resolve THIS timeline clip's essence by following its OWN
                source-reference chain (mob_id + slot_id at every hop, via
                pyaaf2's SourceClip.walk()).

                CRITICAL: ElevenLabs AAFs use ONE MasterMob with N slots —
                one slot per timeline clip, each referencing its own
                SourceMob.  A resolver that just scans the MasterMob for
                "the first SourceMob with essence" returns the SAME essence
                (clip 1's audio) for EVERY timeline clip — the master then
                plays scene 1's audio at every position.  The slot chain is
                the only correct mapping.

                Returns (source_mob_with_essence | None, source_offset_units).
                """
                off_units = 0
                try:
                    m = comp_clip.mob
                except Exception:
                    m = None
                if m is not None and getattr(m, "essence", None) is not None:
                    # Direct SourceMob reference (some producers skip the master)
                    return m, off_units
                try:
                    for link in comp_clip.walk():
                        if not isinstance(link, C.SourceClip):
                            continue
                        try:
                            off_units += int(getattr(link, "start", 0) or 0)
                        except Exception:
                            pass
                        try:
                            lm = link.mob
                        except Exception:
                            lm = None
                        if lm is not None and getattr(lm, "essence", None) is not None:
                            return lm, off_units
                except Exception as walk_err:
                    logger.debug(f"AAF clip chain walk failed: {walk_err}")
                return None, 0

            comps = list(f.content.toplevel())
            if not comps:
                comps = [m for m in f.content.mobs if type(m).__name__ == "CompositionMob"]
            for comp in comps:
                for slot in getattr(comp, "slots", []) or []:
                    if getattr(slot, "media_kind", None) != "Sound":
                        continue
                    seg = getattr(slot, "segment", None)
                    if not isinstance(seg, C.Sequence):
                        continue
                    er = float(slot.edit_rate)
                    if er <= 0:
                        continue
                    pos = 0
                    for obj in seg.components:
                        ln = int(getattr(obj, "length", 0) or 0)
                        if isinstance(obj, C.Filler):
                            pos += ln
                            continue
                        if isinstance(obj, C.Transition):
                            pos -= ln
                            continue
                        if isinstance(obj, C.SourceClip):
                            smob, _chain_off = _resolve_clip_essence(obj)
                            path = _write_essence(smob) if smob is not None else None
                            if path and ln > 0:
                                try:
                                    _own_off = int(getattr(obj, "start", 0) or 0)
                                except Exception:
                                    _own_off = 0
                                src_off = max(0.0, float(_own_off + _chain_off) / er)
                                placed.append((pos / er, src_off, ln / er, path))
                            elif ln > 0:
                                logger.warning(
                                    f"AAF extraction: no essence resolved for clip at "
                                    f"{pos / er:.2f}s (len {ln / er:.2f}s) — leaving silence"
                                )
                            pos += ln
                            continue
                        pos += ln

        if not placed:
            logger.info("AAF embedded-audio extraction: no embedded essence found (timeline-only AAF).")
            return False
        if len(placed) > 400:
            logger.warning(f"AAF embedded-audio extraction: {len(placed)} clips — too many for one-pass assembly, skipping.")
            return False

        total_dur = max(st + du for st, _o, du, _p in placed) + 0.25
        cmd = ["ffmpeg", "-y", "-f", "lavfi", "-t", f"{total_dur:.3f}",
               "-i", "anullsrc=r=48000:cl=stereo"]
        filters = []
        mix_tags = ["[0:a]"]
        for i, (st, off, du, pth) in enumerate(placed):
            cmd += ["-i", pth]
            delay_ms = max(0, int(round(st * 1000)))
            filters.append(
                f"[{i + 1}:a]atrim=start={off:.4f}:duration={du:.4f},"
                f"aresample=48000,adelay={delay_ms}:all=1[a{i}]"
            )
            mix_tags.append(f"[a{i}]")
        filters.append(
            "".join(mix_tags) + f"amix=inputs={len(mix_tags)}:duration=first:normalize=0[out]"
        )
        if str(out_wav).lower().endswith(".mp3"):
            # Browser/waveform-friendly: a reconstructed 7-min PCM WAV is
            # ~80-190 MB and chokes the frontend's WebAudio decode (no
            # waveform, broken playback); 192k MP3 matches a normal upload.
            _enc = ["-c:a", "libmp3lame", "-b:a", "192k", "-ar", "48000"]
        else:
            _enc = ["-c:a", "pcm_s16le", "-ar", "48000"]
        cmd += ["-filter_complex", ";".join(filters), "-map", "[out]", *_enc, out_wav]
        proc = subprocess.run(cmd, capture_output=True, timeout=1800)
        if proc.returncode != 0 or not os.path.exists(out_wav) or os.path.getsize(out_wav) < 1024:
            logger.warning(
                f"AAF embedded-audio assembly failed (rc={proc.returncode}): "
                f"{(proc.stderr or b'')[-400:]!r}"
            )
            return False
        logger.info(
            f"AAF embedded-audio extraction OK: {len(placed)} clips -> "
            f"{out_wav} ({os.path.getsize(out_wav) / 1e6:.1f} MB, {total_dur:.1f}s)"
        )
        return True
    except Exception as e:
        logger.warning(f"AAF embedded-audio extraction failed: {e}")
        return False
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


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
