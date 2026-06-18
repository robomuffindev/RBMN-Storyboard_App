"""
Text alignment for subtitle reconciliation.

When the user uploads narration audio AND pastes the canonical script
(common for ElevenLabs / synthesized narration workflows), Whisper still
mis-transcribes a percentage of words — homophones, dropped articles,
hallucinated filler.  The audio timestamps Whisper produces are accurate,
but the word *strings* often aren't.

This module fixes the mismatch by:

1. Taking Whisper's word list (each with start/end timestamps).
2. Taking the user's canonical text (the source-of-truth script).
3. Running a sequence alignment between the two token streams.
4. Emitting a new word list where:
   - Whisper's timestamps are preserved (they're audio-accurate).
   - The strings come from the canonical text (the user's truth).
   - Whisper hallucinations are dropped.
   - Canonical tokens Whisper missed get interpolated timestamps from
     their neighbors.

The alignment uses Python's stdlib ``difflib.SequenceMatcher`` with
case-insensitive, punctuation-stripped normalization — no extra deps,
works on any language whose words split on whitespace.

This is called at export time so existing projects benefit without a
re-transcription pass.
"""

from __future__ import annotations

import difflib
import logging
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


_NORM_RE = re.compile(r"[^0-9a-zA-Z]+")


def _normalize_token(s: str) -> str:
    """Lowercase + strip non-alphanumerics for matching only."""
    if not s:
        return ""
    return _NORM_RE.sub("", s.lower())


def _tokenize_canonical(text: str) -> List[str]:
    """Whitespace-split the canonical text, preserving attached punctuation.

    Whisper's word objects typically come back with attached punctuation
    too (e.g. ``"hello,"``), so matching whitespace-split canonical tokens
    against them works directly.
    """
    if not text:
        return []
    return [tok for tok in text.split() if tok]


def reconcile_words_to_canonical(
    whisper_words: List[Dict[str, Any]],
    canonical_text: str,
) -> List[Dict[str, Any]]:
    """Replace word strings in ``whisper_words`` with tokens from
    ``canonical_text``, keeping Whisper's timestamps.

    Args:
        whisper_words: List of dicts with at minimum ``word``, ``start``,
            ``end`` keys (the ``confidence`` and ``block`` keys are
            preserved when present).
        canonical_text: The user's source-of-truth text (e.g. the
            ElevenLabs script that was used to generate the audio).

    Returns:
        A new word list with canonical strings + Whisper timestamps.
        Returns the original ``whisper_words`` unchanged if alignment
        isn't possible (empty inputs, no overlap, or any internal
        failure — alignment should never break export).
    """
    if not canonical_text or not canonical_text.strip():
        return whisper_words
    if not whisper_words:
        return whisper_words

    try:
        canonical_tokens = _tokenize_canonical(canonical_text)
        if not canonical_tokens:
            return whisper_words

        # Build normalized arrays for matching.  Track original indices so
        # we can map back to the original token after alignment.
        whisper_norm: List[str] = []
        whisper_idx_map: List[int] = []
        for i, w in enumerate(whisper_words):
            n = _normalize_token(str(w.get("word", "")))
            if n:
                whisper_norm.append(n)
                whisper_idx_map.append(i)

        canonical_norm: List[str] = []
        canonical_idx_map: List[int] = []
        for j, t in enumerate(canonical_tokens):
            n = _normalize_token(t)
            if n:
                canonical_norm.append(n)
                canonical_idx_map.append(j)

        if not whisper_norm or not canonical_norm:
            return whisper_words

        # If the two streams share almost no tokens, the canonical text
        # probably doesn't actually match this audio.  Bail rather than
        # produce nonsense alignment.
        sm_quality = difflib.SequenceMatcher(
            a=whisper_norm, b=canonical_norm, autojunk=False
        )
        ratio = sm_quality.quick_ratio()
        if ratio < 0.30:
            logger.warning(
                "Text alignment skipped: canonical/whisper similarity "
                f"{ratio:.0%} is too low — using Whisper transcription as-is. "
                f"(whisper={len(whisper_norm)} tokens, canonical={len(canonical_norm)} tokens)"
            )
            return whisper_words

        opcodes = sm_quality.get_opcodes()
        result: List[Dict[str, Any]] = []

        # Helper to copy timestamp metadata from a source Whisper word
        # while substituting in a canonical string.
        def _emit(canonical_string: str, start: float, end: float,
                  source_word: Optional[Dict[str, Any]] = None,
                  confidence: Optional[float] = None) -> None:
            out: Dict[str, Any] = {
                "word": canonical_string,
                "start": float(start),
                "end": float(end),
            }
            if confidence is not None:
                out["confidence"] = float(confidence)
            elif source_word is not None and "confidence" in source_word:
                out["confidence"] = float(source_word["confidence"])
            if source_word is not None and source_word.get("block") is not None:
                out["block"] = source_word["block"]
            result.append(out)

        for tag, i1, i2, j1, j2 in opcodes:
            if tag == "equal":
                # 1:1 — both streams agree on these tokens; just swap in
                # the canonical spelling (which preserves capitalization
                # and proper punctuation).
                for k in range(i2 - i1):
                    w = whisper_words[whisper_idx_map[i1 + k]]
                    c_token = canonical_tokens[canonical_idx_map[j1 + k]]
                    _emit(c_token, w["start"], w["end"], source_word=w)

            elif tag == "replace":
                # Whisper heard different words than the canonical text.
                # Trust Whisper's timing window for this region, but use
                # canonical strings inside it.  Distribute time evenly
                # across the canonical tokens.
                whisper_range = [whisper_words[whisper_idx_map[k]] for k in range(i1, i2)]
                canonical_range = [canonical_tokens[canonical_idx_map[k]] for k in range(j1, j2)]
                if not whisper_range or not canonical_range:
                    continue
                t_start = float(whisper_range[0]["start"])
                t_end = float(whisper_range[-1]["end"])
                if t_end < t_start:
                    t_end = t_start
                n = len(canonical_range)
                step = (t_end - t_start) / max(n, 1)
                for ci, c_tok in enumerate(canonical_range):
                    _emit(
                        c_tok,
                        t_start + ci * step,
                        t_start + (ci + 1) * step,
                        source_word=whisper_range[0],
                        confidence=0.5,
                    )

            elif tag == "delete":
                # Whisper produced words that aren't in canonical —
                # hallucination or filler.  Drop them.
                continue

            elif tag == "insert":
                # Canonical has tokens Whisper missed.  Interpolate a
                # timestamp from the neighboring matched tokens.
                prev_end: float
                next_start: float
                if i1 > 0:
                    prev_end = float(whisper_words[whisper_idx_map[i1 - 1]]["end"])
                else:
                    prev_end = float(whisper_words[whisper_idx_map[0]]["start"])
                if i1 < len(whisper_idx_map):
                    next_start = float(whisper_words[whisper_idx_map[i1]]["start"])
                else:
                    next_start = prev_end + 0.5
                if next_start <= prev_end:
                    next_start = prev_end + 0.2
                span = next_start - prev_end
                n = j2 - j1
                step = span / max(n, 1)
                for ci in range(n):
                    c_tok = canonical_tokens[canonical_idx_map[j1 + ci]]
                    _emit(
                        c_tok,
                        prev_end + ci * step,
                        prev_end + (ci + 1) * step,
                        confidence=0.3,
                    )

        if not result:
            # Defensive: if reconciliation somehow produced nothing,
            # fall back to original so the export still has subtitles.
            logger.warning(
                "Text alignment produced empty result — falling back to Whisper words"
            )
            return whisper_words

        # Stitch any leading/trailing canonical tokens missed by the
        # opcodes (rare edge case).  We trust the opcodes covered the
        # whole stream, but log for diagnostics.
        logger.info(
            f"Text alignment: {len(whisper_words)} Whisper words + "
            f"{len(canonical_tokens)} canonical tokens → "
            f"{len(result)} reconciled words (similarity={ratio:.0%})"
        )
        return result

    except Exception as e:
        # Never let alignment break the export.  Log and fall back.
        logger.warning(
            f"Text alignment failed: {type(e).__name__}: {e} — "
            "using Whisper words as-is"
        )
        return whisper_words


def retime_srt_words_to_audio(
    srt_words: List[Dict[str, Any]],
    whisper_words: List[Dict[str, Any]],
    *,
    min_similarity: float = 0.30,
) -> List[Dict[str, Any]]:
    """Re-anchor SRT word timings to the actual audio.

    ElevenLabs (and other) SRT exports carry accurate *text* and cue
    (``block``) grouping, but their *timestamps* drift from the rendered
    audio — the error accumulates over long narrations (observed ~10s by
    the 13-minute mark), which makes scene cuts land progressively before
    the words are actually spoken.

    This keeps the SRT's word strings AND ``block`` grouping but replaces
    each word's ``start``/``end`` with audio-accurate timings transferred
    from a Whisper pass over the real audio, using a difflib sequence
    alignment between the two token streams (they transcribe the same
    speech, so they align tightly).

    Args:
        srt_words: SRT words — dicts with ``word``/``start``/``end`` and
            (usually) ``block``.  This is the structure we KEEP.
        whisper_words: Whisper words over the actual audio — dicts with
            ``word``/``start``/``end``.  This is the TIMING source.
        min_similarity: bail out (return ``srt_words`` unchanged) if the
            two streams share less than this fraction of tokens — protects
            against an SRT that doesn't match the audio at all.

    Returns:
        A new word list = SRT text + block, Whisper-accurate timings.
        Returns ``srt_words`` unchanged on empty input, low similarity, or
        any internal failure (re-timing must never break SRT upload).
    """
    if not srt_words or not whisper_words:
        return srt_words
    try:
        srt_norm: List[str] = []
        srt_map: List[int] = []
        for j, w in enumerate(srt_words):
            n = _normalize_token(str(w.get("word", "")))
            if n:
                srt_norm.append(n)
                srt_map.append(j)
        wh_norm: List[str] = []
        wh_map: List[int] = []
        for i, w in enumerate(whisper_words):
            n = _normalize_token(str(w.get("word", "")))
            if n:
                wh_norm.append(n)
                wh_map.append(i)
        if not srt_norm or not wh_norm:
            return srt_words

        sm = difflib.SequenceMatcher(a=srt_norm, b=wh_norm, autojunk=False)
        ratio = sm.quick_ratio()
        if ratio < min_similarity:
            logger.warning(
                f"SRT re-time skipped: SRT/Whisper token similarity {ratio:.0%} "
                f"below {min_similarity:.0%} — keeping original SRT timings "
                f"(srt={len(srt_norm)} tokens, whisper={len(wh_norm)} tokens)."
            )
            return srt_words

        n = len(srt_words)
        a_start: List[Optional[float]] = [None] * n
        a_end: List[Optional[float]] = [None] * n

        for tag, i1, i2, j1, j2 in sm.get_opcodes():
            # a = SRT (index i), b = Whisper (index j)
            if tag == "equal":
                for k in range(i2 - i1):
                    sj = srt_map[i1 + k]
                    wj = wh_map[j1 + k]
                    a_start[sj] = float(whisper_words[wj].get("start", 0.0) or 0.0)
                    a_end[sj] = float(whisper_words[wj].get("end", 0.0) or 0.0)
            elif tag == "replace":
                wh_rng = [whisper_words[wh_map[k]] for k in range(j1, j2)]
                srt_rng = [srt_map[k] for k in range(i1, i2)]
                if not srt_rng or not wh_rng:
                    continue  # leave None → interpolated below
                t0 = float(wh_rng[0].get("start", 0.0) or 0.0)
                t1 = float(wh_rng[-1].get("end", t0) or t0)
                if t1 < t0:
                    t1 = t0
                cnt = len(srt_rng)
                step = (t1 - t0) / max(cnt, 1)
                for ci, sj in enumerate(srt_rng):
                    a_start[sj] = t0 + ci * step
                    a_end[sj] = t0 + (ci + 1) * step
            # "delete" (SRT tokens Whisper missed) and "insert" (Whisper
            # tokens not in SRT) leave SRT gaps as None → interpolated.

        known = [(j, a_start[j]) for j in range(n) if a_start[j] is not None]
        if not known:
            logger.warning("SRT re-time: no aligned anchors — keeping original SRT timings.")
            return srt_words

        # Interpolate start times for unaligned SRT words from neighbours.
        starts: List[float] = [0.0] * n
        ki = 0
        for j in range(n):
            if a_start[j] is not None:
                starts[j] = float(a_start[j])
                continue
            # advance ki to the last known anchor with index <= j
            while ki + 1 < len(known) and known[ki + 1][0] <= j:
                ki += 1
            prev = known[ki] if known[ki][0] <= j else None
            nxt = None
            for cand in known:
                if cand[0] >= j:
                    nxt = cand
                    break
            if prev and nxt and nxt[0] != prev[0]:
                frac = (j - prev[0]) / (nxt[0] - prev[0])
                starts[j] = prev[1] + (nxt[1] - prev[1]) * frac
            elif prev:
                starts[j] = prev[1] + 0.3 * (j - prev[0])
            else:
                starts[j] = max(0.0, nxt[1] - 0.3 * (nxt[0] - j))

        # Enforce monotonic non-decreasing starts.
        for j in range(1, n):
            if starts[j] < starts[j - 1]:
                starts[j] = starts[j - 1]

        out: List[Dict[str, Any]] = []
        for j in range(n):
            o = dict(srt_words[j])  # keep word, block, any extras
            s = round(max(0.0, starts[j]), 3)
            if a_end[j] is not None and float(a_end[j]) >= starts[j]:
                e = round(float(a_end[j]), 3)
            elif j + 1 < n:
                e = round(max(s, starts[j + 1]), 3)
            else:
                e = round(s + 0.3, 3)
            if e < s:
                e = round(s + 0.05, 3)
            o["start"] = s
            o["end"] = e
            out.append(o)

        _n_blocks = sum(1 for o in out if o.get("block") is not None)
        logger.info(
            f"SRT re-time: {len(srt_words)} SRT words re-anchored to audio via "
            f"{len(whisper_words)} Whisper words (similarity={ratio:.0%}, "
            f"{_n_blocks} words retain block grouping). "
            f"SRT span {float(srt_words[0].get('start',0) or 0):.1f}-"
            f"{float(srt_words[-1].get('end',0) or 0):.1f}s → audio span "
            f"{out[0]['start']:.1f}-{out[-1]['end']:.1f}s."
        )
        return out

    except Exception as e:
        logger.warning(
            f"SRT re-time failed: {type(e).__name__}: {e} — keeping original SRT timings."
        )
        return srt_words
