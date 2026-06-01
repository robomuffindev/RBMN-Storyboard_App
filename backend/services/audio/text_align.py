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
