"""
Script header parser — extracts ``# Heading`` markers from the user's
narration script and produces:

1. A ``clean_text`` with header lines stripped (this is what gets passed
   to Whisper / text alignment — headers are silent markers).
2. A list of ``ScriptHeader`` objects with:
   - depth (0 / 1 / 2 = top-level / sub / sub-sub; deeper gets capped)
   - name (the heading text, trimmed)
   - char_offset (position in the ORIGINAL script — useful for IDE-style
     edits, jump-to)
   - word_index_in_clean (zero-based index of the word that FOLLOWS the
     header in the clean text — used to look up the chapter's start
     time after Whisper alignment)

Syntax supported
----------------
Markdown ATX headings only:

    # Heading              → depth 0
    ## Heading             → depth 1
    ### Heading            → depth 2
    #### …                 → capped at depth 2

Leading whitespace is allowed (``  # Heading``).  Trailing ``#`` chars
(``# Heading ###``) are stripped per Markdown convention.

Edge cases
----------
- An indented ``#`` inside a code block IS parsed as a header.  We don't
  support fenced code blocks because narration scripts shouldn't contain
  them — if a user really wants a literal ``#`` they can write ``\\#``
  (escaped) which the parser treats as a regular character.
- Empty headings (``#`` alone with no text) are SKIPPED with a warning.
- Consecutive headings (``# A`` directly followed by ``## B``) are both
  recorded; the chapter builder handles the depth nesting.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import List

logger = logging.getLogger(__name__)


_HEADER_RE = re.compile(
    r"""^
    [\t ]*           # optional leading whitespace
    (?<!\\)          # not preceded by a backslash (escape)
    (\#{1,6})        # the # markers
    [\t ]+           # at least one space after the markers
    (.+?)            # the heading text (non-greedy)
    [\t ]*           # trailing whitespace
    \#*              # trailing # chars (Markdown convention) — stripped
    [\t ]*$          # optional trailing whitespace
    """,
    re.VERBOSE,
)

# Maximum supported depth — anything deeper than this gets clamped.
DEFAULT_MAX_DEPTH = 2


@dataclass
class ScriptHeader:
    """One ``# header`` line parsed out of the script."""

    depth: int                  # 0 = top-level, 1 = sub, 2 = sub-sub (clamped)
    raw_depth: int              # depth as it was written (1..6) before clamp
    name: str
    char_offset: int            # position in the ORIGINAL script
    word_index_in_clean: int    # word the header points at, in the clean text


@dataclass
class ParsedScript:
    """Output of ``parse_script_headers``."""

    clean_text: str = ""
    headers: List[ScriptHeader] = field(default_factory=list)
    original_length: int = 0
    word_count: int = 0


def parse_script_headers(
    script: str,
    *,
    max_depth: int = DEFAULT_MAX_DEPTH,
) -> ParsedScript:
    """Parse Markdown ATX headings out of ``script``.

    Args:
        script: The raw user-provided text.
        max_depth: Headings deeper than this get clamped (default 2 = 3 levels).

    Returns:
        A ``ParsedScript`` with the clean text, header records, and word
        counts.  Idempotent — pure function.
    """
    if not script:
        return ParsedScript()

    headers: List[ScriptHeader] = []
    clean_lines: List[str] = []
    cumulative_word_count = 0
    char_offset = 0

    for raw_line in script.splitlines(keepends=True):
        line_without_trailing = raw_line.rstrip("\r\n")
        line_end_chars = raw_line[len(line_without_trailing):]  # the \n or \r\n

        match = _HEADER_RE.match(line_without_trailing)
        if match:
            hashes, name = match.group(1), match.group(2).strip()
            if not name:
                logger.debug(
                    f"Skipping empty header at offset {char_offset}: {raw_line!r}"
                )
                char_offset += len(raw_line)
                continue

            raw_depth = len(hashes)
            depth = min(raw_depth - 1, max_depth)  # raw 1 → depth 0
            depth = max(depth, 0)

            headers.append(
                ScriptHeader(
                    depth=depth,
                    raw_depth=raw_depth,
                    name=name,
                    char_offset=char_offset,
                    word_index_in_clean=cumulative_word_count,
                )
            )
            logger.debug(
                f"Parsed header @char {char_offset}, word {cumulative_word_count}: "
                f"depth={depth} (raw={raw_depth}) name={name!r}"
            )
            # Header line is STRIPPED from clean_text — don't append to
            # clean_lines and don't add to word count.
            char_offset += len(raw_line)
            continue

        # Non-header line — append (with original line ending) and count words.
        clean_lines.append(raw_line)
        cumulative_word_count += len(line_without_trailing.split())
        char_offset += len(raw_line)

    clean_text = "".join(clean_lines)

    parsed = ParsedScript(
        clean_text=clean_text,
        headers=headers,
        original_length=len(script),
        word_count=cumulative_word_count,
    )

    logger.info(
        f"Script parsed: {len(headers)} headers, {cumulative_word_count} words, "
        f"{len(script)} chars → {len(clean_text)} clean chars"
    )
    return parsed


# ── Header diff for re-parse reconciliation ───────────────────────────


def _normalize_header_key(h: ScriptHeader) -> tuple:
    """Stable key for matching old/new headers across edits.

    We use (depth, lowered-trimmed-name).  Two headers are "the same"
    if they have identical depth and a name that matches after lower
    + strip.  This lets users edit capitalization without losing their
    customized chapter row.
    """
    return (h.depth, h.name.strip().lower())


def diff_headers(
    old_headers: List[ScriptHeader],
    new_headers: List[ScriptHeader],
) -> dict:
    """Return ``{matched, added, removed}`` lists for chapter reconciliation.

    Each ``matched`` entry is ``(old_idx, new_idx)`` so the builder can
    carry forward Chapter.id, color, tags, manual-rename source flag.
    """
    old_keys = [_normalize_header_key(h) for h in old_headers]
    new_keys = [_normalize_header_key(h) for h in new_headers]

    # Use a multiset matching pass — first occurrence wins (preserves
    # order for repeats like "Chapter 1" / "Chapter 1" in two acts).
    used_new = set()
    matched: List[tuple] = []
    for i, k in enumerate(old_keys):
        for j, nk in enumerate(new_keys):
            if j in used_new:
                continue
            if k == nk:
                matched.append((i, j))
                used_new.add(j)
                break

    matched_old = {pair[0] for pair in matched}
    removed = [i for i in range(len(old_headers)) if i not in matched_old]
    added = [j for j in range(len(new_headers)) if j not in used_new]

    return {"matched": matched, "added": added, "removed": removed}
