"""Chapter pipeline: parse -> build -> resolve.

Public surface
--------------
- parse_script_headers(script) returns ParsedScript (headers + clean text)
- rebuild_chapters(session, project_id) re-derives the full chapter tree
  from the project's current script + scenes + AppSettings.
- resolve_llm_batches(chapter) returns sub-chapter scene ranges sized to
  the configured LLM scene limit.
- deduplicate_project_chapters(session, project_id) drops duplicate rows.

The orchestration is in builder.rebuild_chapters.  The split logic is
in auto_split.  The parser is in parser.
"""

from .parser import ParsedScript, ScriptHeader, parse_script_headers
from .builder import (
    rebuild_chapters,
    ChapterTreeNode,
    build_chapter_tree_response,
    deduplicate_project_chapters,
)
from .auto_split import auto_split_oversized_chapters
from .resolver import resolve_llm_batches, scenes_in_chapter_tree

__all__ = [
    "ParsedScript",
    "ScriptHeader",
    "parse_script_headers",
    "rebuild_chapters",
    "ChapterTreeNode",
    "build_chapter_tree_response",
    "deduplicate_project_chapters",
    "auto_split_oversized_chapters",
    "resolve_llm_batches",
    "scenes_in_chapter_tree",
]
