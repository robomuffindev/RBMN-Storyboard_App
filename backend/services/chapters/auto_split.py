"""
Auto-split logic — break oversized chapters into sub-chapters.

When a (top-level) chapter exceeds the ``chapter_auto_split_threshold``
scenes setting, we split it into N sub-chapters so each one fits within
the LLM batch limit.  Splitting prefers natural break points (long pauses,
sentence boundaries) over equal-size chunks.

The function is **pure** — it computes the split plan and returns it.
The builder applies it to the DB.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class SubChapterPlan:
    """One sub-chapter to create."""

    name: str
    scene_ids: List[str]
    start_time: float
    end_time: float


def auto_split_oversized_chapters(
    *,
    chapter_name: str,
    scene_list: List[Dict[str, Any]],
    limit: int,
    max_depth: int,
    current_depth: int,
    prefer_pause_breaks: bool = True,
) -> List[SubChapterPlan]:
    """Produce a list of ``SubChapterPlan`` if the chapter is oversized.

    Args:
        chapter_name: Parent chapter name (used to derive sub-chapter names).
        scene_list: List of scene dicts with at minimum ``id``, ``start_time``,
            ``end_time`` keys.  Order matters.
        limit: Maximum scenes per sub-chapter.
        max_depth: Maximum chapter depth (from settings).
        current_depth: Depth of the chapter being split.
        prefer_pause_breaks: If True, prefer to split at the largest gap
            between scenes when the gap is at least 1.0s and within
            ``limit ± 20%`` of the target split point.

    Returns:
        A list of plans.  Empty list if the chapter doesn't need splitting
        OR if the chapter is already at max depth (in which case we LOG
        a warning but leave the chapter intact — the LLM batcher will
        still split internally at the boundary).
    """
    n = len(scene_list)
    if n <= limit:
        return []
    if current_depth >= max_depth:
        logger.warning(
            f"Chapter '{chapter_name}' has {n} scenes (> limit {limit}) "
            f"but is already at max depth {max_depth} — LLM batcher will "
            "internally batch on the fly."
        )
        return []

    # Compute base equal-size split count
    num_parts = (n + limit - 1) // limit  # ceil
    target_size = n / num_parts

    # Find break points
    boundaries: List[int] = []
    for part_idx in range(1, num_parts):
        ideal = round(part_idx * target_size)
        # Search a window around ``ideal`` for the largest inter-scene gap
        if prefer_pause_breaks:
            window_start = max(0, int(ideal - target_size * 0.2))
            window_end = min(n - 1, int(ideal + target_size * 0.2))
            best_gap = -1.0
            best_idx = ideal
            for i in range(max(window_start, 1), window_end + 1):
                if i >= n:
                    break
                gap = float(scene_list[i].get("start_time", 0)) - float(
                    scene_list[i - 1].get("end_time", 0)
                )
                if gap > best_gap and gap >= 1.0:
                    best_gap = gap
                    best_idx = i
            boundaries.append(best_idx)
        else:
            boundaries.append(ideal)

    # Dedupe + clamp
    boundaries = sorted({max(1, min(b, n - 1)) for b in boundaries})

    # Build plans
    plans: List[SubChapterPlan] = []
    prev_bound = 0
    for idx, bound in enumerate(boundaries + [n], start=1):
        sub_scenes = scene_list[prev_bound:bound]
        if not sub_scenes:
            continue
        plan = SubChapterPlan(
            name=f"{chapter_name} — Part {idx}",
            scene_ids=[str(s["id"]) for s in sub_scenes],
            start_time=float(sub_scenes[0].get("start_time", 0)),
            end_time=float(sub_scenes[-1].get("end_time", 0)),
        )
        plans.append(plan)
        prev_bound = bound

    logger.info(
        f"Auto-split '{chapter_name}': {n} scenes → {len(plans)} parts "
        f"(limit={limit}, boundaries={boundaries})"
    )
    return plans
