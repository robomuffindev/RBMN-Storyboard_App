#!/usr/bin/env python3
"""diag.py — One-shot backend diagnostic snapshot.

Hits /api/debug/snapshot and prints a compact markdown report you can paste
into chat instead of pasting raw rbmn.log dumps. Captures:

  - in-memory batch run state
  - in-memory auto-gen state
  - ComfyUI worker stats (caps, models, in_flight)
  - job queue depth + last running / last failed jobs
  - recent WARNING/ERROR log lines

Usage:
    python tools/diag.py                       # default: 40 log lines, markdown
    python tools/diag.py --logs 200            # more log entries
    python tools/diag.py --grep batch          # only log lines mentioning "batch"
    python tools/diag.py --json                # raw JSON for piping
    python tools/diag.py --host 127.0.0.1:8899 # override target

The output is bounded — `--logs N` caps how many log lines appear, and each
log message is truncated at 500 chars by the backend.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request
import urllib.parse
from typing import Any


def fetch(host: str, path: str, params: dict | None = None, timeout: int = 15) -> Any:
    url = f"http://{host.lstrip('http://').rstrip('/')}{path}"
    if params:
        url += "?" + urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception as e:
        return {"_error": f"{type(e).__name__}: {e}", "_url": url}


# ───────────────────────────────────────────────────────────────────────
# Markdown rendering helpers
# ───────────────────────────────────────────────────────────────────────

def _yesno(v: Any) -> str:
    if v is None:
        return "?"
    return "✓" if v else "✗"


def render_markdown(snap: dict) -> str:
    if snap.get("_error"):
        return f"# Backend Diagnostic — connection FAILED\n\n{snap['_error']}\n\nURL: `{snap.get('_url')}`\n"

    lines: list[str] = []
    lines.append(f"# Backend Diagnostic Snapshot")
    lines.append(f"_ts: {snap.get('timestamp', '?')}_\n")

    # Workers
    workers = snap.get("workers") or []
    if workers:
        lines.append("## ComfyUI Workers")
        lines.append("| URL | Healthy | In-flight | Capabilities | Models |")
        lines.append("|---|---|---|---|---|")
        for w in workers:
            caps = ", ".join(w.get("capabilities") or []) or "—"
            models = ", ".join(w.get("models") or []) or "—"
            tag = " (RunPod)" if w.get("is_runpod") else ""
            lines.append(
                f"| `{w['url']}`{tag} | {_yesno(w.get('healthy'))} | "
                f"{w.get('in_flight', 0)} | {caps} | {models} |"
            )
        lines.append("")
    else:
        lines.append("## ComfyUI Workers\n_None registered._\n")

    # Queue
    q = snap.get("queue") or {}
    lines.append("## Job Queue")
    lines.append(
        f"- PENDING: **{q.get('pending', 0)}**  "
        f"RUNNING: **{q.get('running', 0)}**  "
        f"DONE: {q.get('done', 0)}  "
        f"FAILED: {q.get('failed', 0)}"
    )
    rj = q.get("running_jobs") or []
    if rj:
        lines.append("\n**Currently running:**")
        for j in rj:
            started = j.get("started_at") or "?"
            lines.append(
                f"- `{j['id'][:8]}` {j.get('type', '?')} on "
                f"`{j.get('worker_url') or '?'}` "
                f"(prompt {j.get('prompt_id') or '?'[:8]}, "
                f"retry={j.get('retry_count')}, started {started})"
            )
    fj = q.get("last_failed_jobs") or []
    if fj:
        lines.append("\n**Recent failures:**")
        for j in fj:
            err = (j.get("error") or "").replace("\n", " ")[:200]
            lines.append(
                f"- `{j['id'][:8]}` {j.get('type', '?')} — {err}"
            )
    lines.append("")

    # Batch
    batches = snap.get("batch_runs") or []
    if batches:
        lines.append("## Batch Runs")
        for b in batches:
            lines.append(f"### batch `{b['batch_id'][:8]}` — **{b.get('status')}**")
            for it in b.get("items") or []:
                err = f" — error: {it['error']}" if it.get("error") else ""
                step = it.get("step") or ""
                lines.append(
                    f"  - [{it['index']}] **{it.get('status')}** "
                    f"{it.get('name')} (proj={it.get('project_id') or '—'}) "
                    f"`{step}`{err}"
                )
            lines.append("")
    else:
        lines.append("## Batch Runs\n_No in-memory batch runs._\n")

    # Auto-gen
    autos = snap.get("auto_gen") or []
    if autos:
        lines.append("## Auto-Gen Runs")
        for a in autos:
            done = f"{a.get('completed_scenes', 0)}/{a.get('total_scenes', 0)}"
            err = f" (error: {a['error']})" if a.get("error") else ""
            lines.append(
                f"- project `{a.get('project_id', '?')[:8]}` "
                f"mode={a.get('mode')} **{a.get('status')}** {done} "
                f"step=`{a.get('current_step') or ''}`{err}"
            )
        lines.append("")
    else:
        lines.append("## Auto-Gen Runs\n_None active or recently completed._\n")

    # Log entries
    entries = snap.get("log_entries") or []
    flt = snap.get("log_filter") or {}
    lines.append(
        f"## Recent log entries "
        f"(levels: {', '.join(flt.get('levels') or [])}"
        f"{', grep=' + flt['grep'] if flt.get('grep') else ''})"
    )
    if not entries:
        lines.append("_No matching log lines in the recent window._\n")
    else:
        for e in entries:
            lines.append(
                f"- `{e['ts']}` **{e['level']}** "
                f"{e['name']}: {e['msg']}"
            )

    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--host", default="127.0.0.1:8899", help="backend host:port (default %(default)s)")
    parser.add_argument("--logs", type=int, default=40, help="recent log lines to include (default 40)")
    parser.add_argument("--grep", default=None, help="filter log lines containing this substring")
    parser.add_argument("--json", action="store_true", help="emit raw JSON instead of markdown")
    parser.add_argument(
        "--tail",
        action="store_true",
        help="instead of snapshot, dump rbmn.log tail (see --tail-level, --logs, --grep)",
    )
    parser.add_argument("--tail-level", default="WARNING", help="for --tail: ERROR | WARNING | INFO (default WARNING)")
    args = parser.parse_args()

    if args.tail:
        data = fetch(
            args.host,
            "/api/debug/log/tail",
            {"lines": args.logs, "level": args.tail_level, "grep": args.grep},
        )
        if args.json:
            print(json.dumps(data, indent=2))
            return 0
        if data.get("_error"):
            print(f"# Log tail FAILED\n\n{data['_error']}\nURL: `{data.get('_url')}`")
            return 1
        flt = data.get("filter") or {}
        print(f"# rbmn.log tail (level={flt.get('level')}, lines={flt.get('lines')}, grep={flt.get('grep')})\n")
        for e in data.get("entries") or []:
            print(f"- `{e['ts']}` **{e['level']}** {e['name']}: {e['msg']}")
        return 0

    data = fetch(
        args.host,
        "/api/debug/snapshot",
        {"log_lines": args.logs, "log_grep": args.grep},
    )
    if args.json:
        print(json.dumps(data, indent=2))
        return 0
    print(render_markdown(data))
    return 0


if __name__ == "__main__":
    sys.exit(main())
