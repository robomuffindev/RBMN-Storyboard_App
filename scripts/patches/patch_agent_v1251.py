"""v1.251 — the agent goes quiet during long jobs, and cannot pick up new code.

Two flaws, both visible within ten minutes of the agent's first run.

1. NO HEARTBEAT WHILE WORKING.  `status.json` is written by the main loop, and
   the main loop is blocked inside `run_job` for the whole duration of a job.
   The face-reference A/B takes minutes; for all of them the agent looked
   identical to a crashed one — nothing in the outbox, no status, a log line
   that says it started and nothing since.  "Is it working or is it hung" is the
   exact question this agent exists to stop anyone having to ask.

   Jobs now run on a worker thread.  The loop keeps ticking and `status.json`
   carries `current` — the job id, its label, and how long it has been going —
   so a long render is visibly a long render.

2. NO WAY TO LOAD NEW AGENT CODE.  Patching `rbmn_agent.py` does nothing to the
   process already running, and only Lorenzo could restart it, which is the
   thing this whole exercise is trying to stop.  A `reload` job exits the
   process with a marker; `agent.bat` loops, so it comes straight back on the
   new code.  The agent can now update itself.

Also: the log gets a line every 30 seconds while a job is running, so the
console window shows life rather than a frozen cursor.
"""
import sys
from pathlib import Path

P = Path(sys.argv[1] if len(sys.argv) > 1 else "scripts/agent/rbmn_agent.py")
src = P.read_text("utf-8")
orig = src


def rep(old, new, why):
    global src
    n = src.count(old)
    assert n == 1, f"{why}: expected 1 match, found {n}"
    src = src.replace(old, new, 1)


rep('''import subprocess
import sys
import time''',
    '''import subprocess
import sys
import threading
import time''',
    "import threading")

rep('''HANDLERS = {"http": do_http, "script": do_script, "restart": do_restart,
            "ping": do_ping}''',
    '''def do_reload(_job: dict) -> dict:
    """Exit so `agent.bat`'s loop restarts this process on the new code.

    Without this, updating the agent needs a human at the keyboard — which is
    the exact thing the agent exists to avoid."""
    return {"ok": True, "reload": True,
            "note": "agent exiting; agent.bat restarts it on the new code"}


HANDLERS = {"http": do_http, "script": do_script, "restart": do_restart,
            "ping": do_ping, "reload": do_reload}

# What the worker thread is doing, for the heartbeat to report.
CURRENT: dict = {}
RELOAD = threading.Event()''',
    "reload + current")

rep('''def run_job(fp: Path, allow_shell: bool) -> None:
    jid = fp.stem''',
    '''def run_job(fp: Path, allow_shell: bool) -> None:
    jid = fp.stem
    CURRENT.clear()''',
    "clear current")

rep('''    kind = str(job.get("kind") or "http").lower()
    label = job.get("label") or kind
    log(f"JOB {jid} [{kind}] {label}")
    started = now()''',
    '''    kind = str(job.get("kind") or "http").lower()
    label = job.get("label") or kind
    log(f"JOB {jid} [{kind}] {label}")
    started = now()
    CURRENT.update({"id": jid, "kind": kind, "label": label,
                    "started_at": started, "t0": time.time()})''',
    "set current")

rep('''    (OUTBOX / f"{jid}.json").write_text(json.dumps(out, indent=2), "utf-8")
    try:
        fp.replace(DONE / fp.name)
    except OSError:
        fp.unlink(missing_ok=True)
    log(f"  -> {'ok' if res.get('ok') else 'FAILED'} "
        f"{res.get('error') or res.get('status') or res.get('exit_code') or ''}")''',
    '''    (OUTBOX / f"{jid}.json").write_text(json.dumps(out, indent=2), "utf-8")
    try:
        fp.replace(DONE / fp.name)
    except OSError:
        fp.unlink(missing_ok=True)
    log(f"  -> {'ok' if res.get('ok') else 'FAILED'} "
        f"{res.get('error') or res.get('status') or res.get('exit_code') or ''}")
    CURRENT.clear()
    if res.get("reload"):
        RELOAD.set()''',
    "clear + reload flag")


rep('''    started_at = time.time()
    tick = 0
    while True:
        try:
            jobs = sorted(INBOX.glob("*.json"))
            for fp in jobs:
                # A file still being written has no closing brace yet; skip it
                # this tick rather than reading half a job.
                try:
                    txt = fp.read_text("utf-8")
                    if not txt.rstrip().endswith("}"):
                        continue
                except OSError:
                    continue
                run_job(fp, a.allow_shell)
            tick += 1
            if tick % 10 == 0 or jobs:
                STATUS.write_text(json.dumps({
                    "alive_at": now(),
                    "uptime_s": int(time.time() - started_at),
                    "backend": health().get("version"),
                    "pending": len(list(INBOX.glob("*.json"))),
                    "completed": len(list(DONE.glob("*.json"))),
                    "shell_enabled": a.allow_shell,
                }, indent=2), "utf-8")
        except KeyboardInterrupt:
            log("agent stopped")
            return 0
        except Exception as e:  # noqa: BLE001 — the loop must not die
            log(f"loop error: {type(e).__name__}: {e}")
        time.sleep(a.poll)''',
    '''    started_at = time.time()
    # v1.251: jobs run on a worker thread so the heartbeat keeps beating. The
    # A/B render takes minutes, and for all of them the old agent was
    # indistinguishable from a crashed one.
    worker = {"thread": None}

    def _take_next() -> None:
        for fp in sorted(INBOX.glob("*.json")):
            try:
                txt = fp.read_text("utf-8")
                if not txt.rstrip().endswith("}"):
                    continue        # still being written; not a whole job yet
            except OSError:
                continue
            t = threading.Thread(target=run_job, args=(fp, a.allow_shell),
                                 daemon=True)
            worker["thread"] = t
            t.start()
            return

    tick = 0
    last_beat = 0.0
    while True:
        try:
            if RELOAD.is_set():
                log("reload requested — exiting so agent.bat restarts on new code")
                return 7
            th = worker["thread"]
            if th is None or not th.is_alive():
                worker["thread"] = None
                _take_next()

            cur = dict(CURRENT)
            busy = bool(cur)
            if busy and time.time() - last_beat > 30:
                log(f"  ... still running {cur.get('id')} "
                    f"({int(time.time() - cur.get('t0', time.time()))}s)")
                last_beat = time.time()

            tick += 1
            if busy or tick % 5 == 0:
                STATUS.write_text(json.dumps({
                    "alive_at": now(),
                    "uptime_s": int(time.time() - started_at),
                    "backend": health().get("version") if not busy else None,
                    "current": ({"id": cur.get("id"), "label": cur.get("label"),
                                 "kind": cur.get("kind"),
                                 "running_s": int(time.time() - cur["t0"])}
                                if busy else None),
                    "pending": len(list(INBOX.glob("*.json"))),
                    "completed": len(list(DONE.glob("*.json"))),
                    "shell_enabled": a.allow_shell,
                }, indent=2), "utf-8")
        except KeyboardInterrupt:
            log("agent stopped")
            return 0
        except Exception as e:  # noqa: BLE001 — the loop must not die
            log(f"loop error: {type(e).__name__}: {e}")
        time.sleep(a.poll)''',
    "threaded loop")

rep('''    shell    arbitrary command — OFF unless started with --allow-shell''',
    '''    shell    arbitrary command — OFF unless started with --allow-shell
    reload   exit so agent.bat restarts this on updated code''',
    "docstring")

assert src != orig
P.write_text(src, "utf-8")
print(f"patched {P}  ({len(orig)} -> {len(src)} bytes)")
