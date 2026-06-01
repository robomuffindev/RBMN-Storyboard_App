"""Debug / diagnostics endpoints.

Designed to let an operator (or an LLM helper) grab a compact view of what
the running backend is doing right now, without pasting full log files.

Returns:
- in-memory batch run state
- in-memory auto-gen state
- ComfyUI worker stats
- job queue depth and current in-flight jobs
- recent ERROR / WARNING log lines from rbmn.log

All endpoints are READ-ONLY and safe to call at any time.
"""
from __future__ import annotations

import logging
import os
import re
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Query, Request
from sqlmodel import select

from backend.config import settings
from backend.database import async_session
from backend.database.models import Job, JobStatus

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/debug", tags=["debug"])


_LOG_PATH = Path(__file__).resolve().parent.parent.parent / "logs" / "rbmn.log"
_LOG_LINE_RE = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}[,\.]\d+)\s+"
    r"\[(?P<level>\w+)\]\s+(?P<name>[^:]+):\s+(?P<msg>.*)$"
)


def _tail_log(
    path: Path,
    max_lines: int = 50,
    level_filter: Optional[set[str]] = None,
    grep: Optional[str] = None,
    max_bytes: int = 4 * 1024 * 1024,  # cap at 4MB read
) -> list[dict[str, str]]:
    """Tail the log file and return parsed entries matching the filters.

    Reads from the end of the file, working backward in chunks until we have
    enough matching lines OR we've read max_bytes.
    """
    if not path.exists():
        return []

    out: deque[dict[str, str]] = deque(maxlen=max_lines)
    try:
        size = path.stat().st_size
        with open(path, "rb") as f:
            read = 0
            chunks: list[bytes] = []
            pos = size
            while read < max_bytes and pos > 0:
                step = min(64 * 1024, pos)
                pos -= step
                f.seek(pos)
                chunks.append(f.read(step))
                read += step
            data = b"".join(reversed(chunks))
        try:
            text = data.decode("utf-8", errors="replace")
        except Exception:
            text = data.decode("latin-1", errors="replace")
        for line in text.splitlines():
            m = _LOG_LINE_RE.match(line)
            if not m:
                continue
            level = m.group("level")
            if level_filter and level not in level_filter:
                continue
            msg = m.group("msg")
            if grep and grep.lower() not in line.lower():
                continue
            out.append({
                "ts": m.group("ts"),
                "level": level,
                "name": m.group("name"),
                "msg": msg[:500],
            })
    except Exception as e:
        return [{
            "ts": "",
            "level": "ERROR",
            "name": "backend.api.debug",
            "msg": f"log tail failed: {e}",
        }]
    return list(out)


def _summarize_batch_runs() -> list[dict[str, Any]]:
    """Snapshot of the module-level _batch_runs dict in api/batch.py."""
    try:
        from backend.api.batch import _batch_runs
    except Exception:
        return []
    out: list[dict[str, Any]] = []
    for batch_id, run in list(_batch_runs.items()):
        items = run.get("items") or []
        item_summary = [
            {
                "index": it.get("index"),
                "name": it.get("project_name"),
                "status": it.get("status"),
                "step": it.get("current_step", "")[:120],
                "error": (it.get("error") or "")[:300] or None,
                "project_id": it.get("project_id"),
            }
            for it in items
        ]
        out.append({
            "batch_id": batch_id,
            "status": run.get("status"),
            "current_item_index": run.get("current_item_index"),
            "items": item_summary,
        })
    return out


def _summarize_autogen() -> list[dict[str, Any]]:
    """Snapshot of the module-level _seq_auto_jobs dict in api/generation.py."""
    try:
        from backend.api.generation import _seq_auto_jobs
    except Exception:
        return []
    out: list[dict[str, Any]] = []
    for pid, info in list(_seq_auto_jobs.items()):
        out.append({
            "project_id": pid,
            "status": info.get("status"),
            "mode": info.get("mode"),
            "completed_scenes": info.get("completed_scenes"),
            "total_scenes": info.get("total_scenes"),
            "current_step": (info.get("current_step") or "")[:200],
            "current_scene_name": info.get("current_scene_name"),
            "error": info.get("error"),
            "batch_run_id": info.get("batch_run_id"),
        })
    return out


def _summarize_workers(request: Request) -> list[dict[str, Any]]:
    cd = getattr(request.app.state, "comfy_dispatcher", None)
    if not cd:
        return []
    out: list[dict[str, Any]] = []
    for url, worker in cd.workers.items():
        out.append({
            "url": url,
            "healthy": getattr(worker, "healthy", None),
            "in_flight": getattr(worker, "in_flight", None),
            "capabilities": sorted(getattr(worker, "capabilities", set()) or set()),
            "models": sorted(getattr(worker, "models", set()) or set()),
            "is_runpod": getattr(worker, "is_runpod", False),
        })
    return out


async def _summarize_queue() -> dict[str, Any]:
    """Counts + last 10 jobs by status from the DB."""
    out: dict[str, Any] = {"pending": 0, "running": 0, "done": 0, "failed": 0}
    try:
        async with async_session() as session:
            for s in (JobStatus.PENDING, JobStatus.RUNNING, JobStatus.DONE, JobStatus.FAILED):
                stmt = select(Job).where(Job.status == s)
                result = await session.execute(stmt)
                out[str(s).split(".")[-1].lower()] = len(result.scalars().all())

            # Last 10 RUNNING jobs (most likely things-of-interest)
            stmt = (
                select(Job)
                .where(Job.status == JobStatus.RUNNING)
                .order_by(Job.created_at.desc())
                .limit(10)
            )
            result = await session.execute(stmt)
            running_jobs = []
            for j in result.scalars().all():
                running_jobs.append({
                    "id": str(j.id),
                    "type": str(j.job_type),
                    "project_id": str(j.project_id) if j.project_id else None,
                    "scene_id": str(j.scene_id) if j.scene_id else None,
                    "worker_url": j.worker_url,
                    "prompt_id": j.prompt_id,
                    "retry_count": j.retry_count,
                    "started_at": j.started_at.isoformat() + "Z" if j.started_at else None,
                })
            out["running_jobs"] = running_jobs

            # Last 5 FAILED for triage
            stmt = (
                select(Job)
                .where(Job.status == JobStatus.FAILED)
                .order_by(Job.completed_at.desc())
                .limit(5)
            )
            result = await session.execute(stmt)
            failed_jobs = []
            for j in result.scalars().all():
                failed_jobs.append({
                    "id": str(j.id),
                    "type": str(j.job_type),
                    "project_id": str(j.project_id) if j.project_id else None,
                    "error": (j.error or "")[:300],
                    "completed_at": j.completed_at.isoformat() + "Z" if j.completed_at else None,
                })
            out["last_failed_jobs"] = failed_jobs
    except Exception as e:
        out["error"] = str(e)
    return out


@router.get("/snapshot")
async def snapshot(
    request: Request,
    log_lines: int = Query(40, ge=0, le=500),
    log_grep: Optional[str] = Query(None, max_length=200),
):
    """Return everything an operator usually pastes into chat to debug.

    Query params:
        log_lines: how many recent log entries to include (default 40, max 500).
        log_grep: optional substring filter for log lines.
    """
    log_entries = _tail_log(
        _LOG_PATH,
        max_lines=log_lines,
        level_filter={"WARNING", "ERROR", "CRITICAL"},
        grep=log_grep,
    )

    return {
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "log_path": str(_LOG_PATH),
        "log_exists": _LOG_PATH.exists(),
        "batch_runs": _summarize_batch_runs(),
        "auto_gen": _summarize_autogen(),
        "workers": _summarize_workers(request),
        "queue": await _summarize_queue(),
        "log_entries": log_entries,
        "log_filter": {
            "lines": log_lines,
            "levels": ["WARNING", "ERROR", "CRITICAL"],
            "grep": log_grep,
        },
    }


@router.get("/log/tail")
async def log_tail(
    lines: int = Query(200, ge=1, le=2000),
    level: Optional[str] = Query(None, description="ERROR | WARNING | INFO | DEBUG"),
    grep: Optional[str] = Query(None, max_length=200),
):
    """Tail rbmn.log with optional level + grep filter.

    Examples:
        /api/debug/log/tail?lines=200&level=ERROR
        /api/debug/log/tail?lines=300&grep=batch
    """
    level_filter: Optional[set[str]] = None
    if level:
        lvl = level.upper().strip()
        # Accept "ERROR" → just ERROR/CRITICAL; "WARNING" → adds WARNING; etc.
        if lvl in ("ERROR", "CRITICAL"):
            level_filter = {"ERROR", "CRITICAL"}
        elif lvl == "WARNING":
            level_filter = {"WARNING", "ERROR", "CRITICAL"}
        elif lvl == "INFO":
            level_filter = {"INFO", "WARNING", "ERROR", "CRITICAL"}
        else:
            level_filter = None  # any

    entries = _tail_log(_LOG_PATH, max_lines=lines, level_filter=level_filter, grep=grep)
    return {
        "log_path": str(_LOG_PATH),
        "log_exists": _LOG_PATH.exists(),
        "filter": {"lines": lines, "level": level, "grep": grep},
        "count": len(entries),
        "entries": entries,
    }
