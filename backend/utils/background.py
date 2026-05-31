"""Background task tracker.

asyncio.create_task() only stores a WEAK reference to the resulting Task in
the event loop. If nothing else holds a strong reference, the Task can be
garbage-collected mid-flight — silently dropping fire-and-forget work like
the auto-gen pipeline or batch-run progress updates.

This module keeps a strong reference until each task completes, and logs any
exception that would otherwise be swallowed.

Usage:
    from backend.utils.background import track

    track(some_coroutine())                       # fire-and-forget
    task = track(some_coroutine(), name="export") # also returns the Task
"""
from __future__ import annotations

import asyncio
import logging
from typing import Coroutine, Optional, Set

logger = logging.getLogger(__name__)

_tasks: Set[asyncio.Task] = set()


def track(coro: Coroutine, *, name: Optional[str] = None) -> asyncio.Task:
    """Create an asyncio Task and hold a strong reference until it finishes.

    Also logs (rather than silently drops) any exception the task raises.
    """
    task = asyncio.create_task(coro, name=name)
    _tasks.add(task)

    def _done(t: asyncio.Task) -> None:
        _tasks.discard(t)
        if t.cancelled():
            return
        exc = t.exception()
        if exc is not None:
            logger.error(
                "Background task %r raised an exception",
                t.get_name(),
                exc_info=exc,
            )

    task.add_done_callback(_done)
    return task


def active_count() -> int:
    """Number of background tasks currently being held."""
    return len(_tasks)
