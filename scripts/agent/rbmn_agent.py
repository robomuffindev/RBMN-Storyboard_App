"""RBMN Agent — so Claude stops asking Lorenzo to run scripts and paste output.

THE PROBLEM IT SOLVES
    Claude's shell runs in a cloud container that cannot reach 127.0.0.1:8899.
    The device bridge that CAN see this folder has no network access. So every
    single measurement had to be: Claude writes a script, Lorenzo runs it,
    Lorenzo pastes the output, Claude reads it. Dozens of round trips for
    answers Claude could have fetched in a second.

    This closes the loop through the one channel that DOES connect both sides:
    the repo folder. Claude drops a job file in `scripts/_agent/inbox/`. This
    agent — running on the Windows machine, where the API and the venv live —
    picks it up, does the work, and writes the answer to `scripts/_agent/outbox/`.
    Claude reads the answer. Lorenzo does nothing.

    Start it once. Leave the window open. That is the whole interaction.

WHAT IT WILL DO
    http     any request against the local backend, or any PRIVATE address on
             the LAN via "host": "http://192.168.12.x:port"
    script   run a .ps1 / .bat / .py from `scripts\\` (python jobs use the venv)
    restart  stop run.bat, start it again, wait for /api/health to answer
    upload   POST a file's bytes to a private host (the helper takes a zip)
    download GET a file and write it under this repo, so it can be looked at
    ping     version, uptime, what is pending
    shell    arbitrary command — OFF unless started with --allow-shell
    reload   exit so agent.bat restarts this on updated code

WHAT IT WILL NOT DO
    Nothing outside this repo folder, and no network beyond 127.0.0.1, unless
    you start it with --allow-shell. Job files are plain JSON you can read.
    Every job is logged to `scripts\\_agent\\log.txt` with what it ran and what
    came back, so there is a record of everything done on your behalf.

RUN
    scripts\\agent.bat                 (or: venv\\Scripts\\python.exe scripts\\agent\\rbmn_agent.py)
    scripts\\agent.bat --allow-shell   (if Claude needs to run something unforeseen)

Ctrl+C to stop. Stdlib only — nothing to install.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import threading
import time
import traceback
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]      # ...\RBMN-Storyboard_App
AGENT = ROOT / "scripts" / "_agent"
INBOX = AGENT / "inbox"
OUTBOX = AGENT / "outbox"
DONE = AGENT / "done"
LOG = AGENT / "log.txt"
STATUS = AGENT / "status.json"
HOST = "http://127.0.0.1:8899"
BACKEND_TITLE = "Robomuffin Idea Factory"      # run.bat sets this window title


def _private_host(url: str) -> str:
    """Allow this machine and the LAN, and nothing else.

    v1.253: job files are written by something that is not Lorenzo, so the
    agent must not be able to be pointed at the internet by one."""
    import ipaddress
    import re
    from urllib.parse import urlparse
    u = url if "://" in url else "http://" + url
    host = (urlparse(u).hostname or "").strip()
    if host in ("localhost", "127.0.0.1", "::1"):
        return u
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        raise ValueError(f"'{host}' is not an IP address — only private "
                         f"addresses and localhost are allowed") from None
    if not (ip.is_private or ip.is_loopback):
        raise ValueError(f"{host} is not a private address")
    if not re.match(r"^https?://", u):
        u = "http://" + u
    return u


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def log(msg: str) -> None:
    line = f"{now()}  {msg}"
    print(line, flush=True)
    try:
        with LOG.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def health() -> dict:
    try:
        with urllib.request.urlopen(HOST + "/api/health", timeout=5) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception:  # noqa: BLE001
        return {}


# ── job kinds ────────────────────────────────────────────────────────────────
def do_http(job: dict) -> dict:
    method = str(job.get("method") or "GET").upper()
    path = str(job.get("path") or "/api/health")
    if not path.startswith("/"):
        path = "/" + path
    # v1.253: `host` reaches the rest of the LAN — the Worker Helper on the
    # training box, the Klein workers, the Ollama server. Defaults to the app's
    # own backend, so every job written before this still means what it meant.
    base = _private_host(str(job.get("host") or HOST))
    body = job.get("body")
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(base + path, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=float(job.get("timeout") or 1800)) as r:
            raw = r.read().decode("utf-8", "replace")
            code = r.status
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        code = e.code
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {e}",
                "elapsed_s": round(time.time() - t0, 1)}
    out = {"ok": 200 <= code < 300, "status": code, "host": base,
           "elapsed_s": round(time.time() - t0, 1)}
    try:
        out["json"] = json.loads(raw)
    except ValueError:
        out["text"] = raw[:200000]
    return out


def do_upload(job: dict) -> dict:
    """POST a file's bytes to a private host.

    v1.254: the helper takes a dataset zip as the raw request body, and moving
    59MB between two machines was the last step that needed a person."""
    fp = Path(str(job.get("file") or "")).expanduser()
    if not fp.is_absolute():
        fp = (ROOT / fp).resolve()
    if not fp.exists():
        return {"ok": False, "error": f"{fp} does not exist"}
    base = _private_host(str(job.get("host") or HOST))
    path = str(job.get("path") or "/")
    if not path.startswith("/"):
        path = "/" + path
    blob = fp.read_bytes()
    req = urllib.request.Request(base + path, data=blob, method="POST")
    req.add_header("Content-Type", "application/zip")
    for k, v in (job.get("headers") or {}).items():
        req.add_header(str(k), str(v))
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=float(job.get("timeout") or 1800)) as r:
            raw = r.read().decode("utf-8", "replace")
            code = r.status
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        code = e.code
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {e}",
                "sent_bytes": len(blob), "elapsed_s": round(time.time() - t0, 1)}
    out = {"ok": 200 <= code < 300, "status": code, "host": base,
           "file": str(fp), "sent_bytes": len(blob),
           "elapsed_s": round(time.time() - t0, 1)}
    try:
        out["json"] = json.loads(raw)
    except ValueError:
        out["text"] = raw[:100000]
    return out


def do_download(job: dict) -> dict:
    """GET a URL and write the bytes into the repo, where they can be looked at.

    v1.259: previews are the whole point of a training run and there was no way
    to see one. The destination is confined to the repo — a download WRITES, and
    a job file must not be able to put a file anywhere it likes."""
    base = _private_host(str(job.get("host") or HOST))
    path = str(job.get("path") or "/")
    if not path.startswith("/"):
        path = "/" + path
    dest = str(job.get("to") or "")
    if not dest:
        return {"ok": False, "error": "'to' is required"}
    fp = (ROOT / dest).resolve()
    if ROOT.resolve() not in fp.parents:
        return {"ok": False, "error": f"'{dest}' resolves outside the repo folder"}
    t0 = time.time()
    try:
        req = urllib.request.Request(base + path, method="GET")
        for k, v in (job.get("headers") or {}).items():
            req.add_header(str(k), str(v))
        with urllib.request.urlopen(req, timeout=float(job.get("timeout") or 900)) as r:
            blob = r.read()
            ctype = r.headers.get("Content-Type", "")
    except urllib.error.HTTPError as e:
        return {"ok": False, "status": e.code,
                "error": e.read().decode("utf-8", "replace")[:2000]}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    fp.parent.mkdir(parents=True, exist_ok=True)
    fp.write_bytes(blob)
    return {"ok": True, "host": base, "to": str(fp), "bytes": len(blob),
            "content_type": ctype, "elapsed_s": round(time.time() - t0, 1)}


def _resolve_script(name: str) -> Path:
    """Anything under scripts\\, and nothing else.  A job file cannot reach out
    of the repo by asking for ..\\..\\something."""
    p = (ROOT / "scripts" / name).resolve()
    base = (ROOT / "scripts").resolve()
    if base not in p.parents and p != base:
        raise ValueError(f"'{name}' is outside scripts\\")
    if not p.exists():
        raise FileNotFoundError(f"{p} does not exist")
    return p


def do_script(job: dict) -> dict:
    p = _resolve_script(str(job.get("file") or ""))
    args = [str(a) for a in (job.get("args") or [])]
    if p.suffix.lower() == ".ps1":
        cmd = ["powershell", "-ExecutionPolicy", "Bypass", "-File", str(p)] + args
    elif p.suffix.lower() == ".py":
        py = ROOT / "venv" / "Scripts" / "python.exe"
        cmd = [str(py if py.exists() else sys.executable), str(p)] + args
    elif p.suffix.lower() in (".bat", ".cmd"):
        cmd = ["cmd", "/c", str(p)] + args
    else:
        raise ValueError(f"do not know how to run {p.suffix} files")
    t0 = time.time()
    r = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True,
                       encoding="utf-8", errors="replace",
                       timeout=float(job.get("timeout") or 3600))
    return {"ok": r.returncode == 0, "exit_code": r.returncode,
            "cmd": " ".join(cmd), "elapsed_s": round(time.time() - t0, 1),
            "stdout": (r.stdout or "")[-200000:],
            "stderr": (r.stderr or "")[-40000:]}


def do_restart(job: dict) -> dict:
    """Stop run.bat and start it again, then wait for the API to answer.

    This is the round trip that cost the most: every backend patch needed a
    human to close a window and double-click a file."""
    before = health().get("version")
    subprocess.run(["taskkill", "/F", "/FI", f"WINDOWTITLE eq {BACKEND_TITLE}*"],
                   capture_output=True, text=True)
    # The window title is set by run.bat itself, so a backend started another
    # way needs the port fallback.
    try:
        ns = subprocess.run(["netstat", "-ano"], capture_output=True, text=True).stdout
        for line in ns.splitlines():
            if ":8899" in line and "LISTENING" in line:
                pid = line.split()[-1]
                if pid.isdigit() and pid != "0":
                    subprocess.run(["taskkill", "/F", "/PID", pid],
                                   capture_output=True, text=True)
    except Exception:  # noqa: BLE001
        pass
    time.sleep(2)
    subprocess.Popen(["cmd", "/c", "start", BACKEND_TITLE, "cmd", "/c",
                      str(ROOT / "run.bat")], cwd=str(ROOT), shell=False)
    deadline = time.time() + float(job.get("timeout") or 300)
    while time.time() < deadline:
        h = health()
        if h.get("version"):
            return {"ok": True, "version_before": before, "version": h["version"],
                    "waited_s": round(float(job.get("timeout") or 300)
                                      - (deadline - time.time()), 1)}
        time.sleep(3)
    return {"ok": False, "error": "backend did not answer within the timeout",
            "version_before": before}


def do_shell(job: dict, allow: bool) -> dict:
    if not allow:
        return {"ok": False, "error": "shell jobs are off — start the agent with "
                                      "--allow-shell to enable them"}
    cmd = str(job.get("cmd") or "")
    t0 = time.time()
    r = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True,
                       encoding="utf-8", errors="replace", shell=True,
                       timeout=float(job.get("timeout") or 1800))
    return {"ok": r.returncode == 0, "exit_code": r.returncode,
            "elapsed_s": round(time.time() - t0, 1),
            "stdout": (r.stdout or "")[-200000:],
            "stderr": (r.stderr or "")[-40000:]}


def do_ping(_job: dict) -> dict:
    h = health()
    return {"ok": bool(h), "backend": h or None,
            "pending": len(list(INBOX.glob("*.json")))}


def do_reload(_job: dict) -> dict:
    """Exit so `agent.bat`'s loop restarts this process on the new code.

    Without this, updating the agent needs a human at the keyboard — which is
    the exact thing the agent exists to avoid."""
    return {"ok": True, "reload": True,
            "note": "agent exiting; agent.bat restarts it on the new code"}


HANDLERS = {"http": do_http, "upload": do_upload, "download": do_download,
            "script": do_script, "restart": do_restart, "ping": do_ping,
            "reload": do_reload}

# What the worker thread is doing, for the heartbeat to report.
CURRENT: dict = {}
RELOAD = threading.Event()


def run_job(fp: Path, allow_shell: bool) -> None:
    jid = fp.stem
    CURRENT.clear()
    try:
        job = json.loads(fp.read_text("utf-8"))
    except Exception as e:  # noqa: BLE001
        (OUTBOX / f"{jid}.json").write_text(json.dumps(
            {"id": jid, "ok": False, "error": f"unreadable job file: {e}",
             "finished_at": now()}, indent=2), "utf-8")
        fp.unlink(missing_ok=True)
        return

    kind = str(job.get("kind") or "http").lower()
    label = job.get("label") or kind
    log(f"JOB {jid} [{kind}] {label}")
    started = now()
    CURRENT.update({"id": jid, "kind": kind, "label": label,
                    "started_at": started, "t0": time.time()})
    try:
        if kind == "shell":
            res = do_shell(job, allow_shell)
        elif kind in HANDLERS:
            res = HANDLERS[kind](job)
        else:
            res = {"ok": False, "error": f"unknown job kind '{kind}'"}
    except subprocess.TimeoutExpired as e:
        res = {"ok": False, "error": f"timed out after {e.timeout}s"}
    except Exception as e:  # noqa: BLE001
        res = {"ok": False, "error": f"{type(e).__name__}: {e}",
               "traceback": traceback.format_exc()[-4000:]}

    out = {"id": jid, "kind": kind, "label": label,
           "started_at": started, "finished_at": now(), **res}
    (OUTBOX / f"{jid}.json").write_text(json.dumps(out, indent=2), "utf-8")
    try:
        fp.replace(DONE / fp.name)
    except OSError:
        fp.unlink(missing_ok=True)
    log(f"  -> {'ok' if res.get('ok') else 'FAILED'} "
        f"{res.get('error') or res.get('status') or res.get('exit_code') or ''}")
    CURRENT.clear()
    if res.get("reload"):
        RELOAD.set()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--allow-shell", action="store_true",
                    help="also run arbitrary commands (off by default)")
    ap.add_argument("--poll", type=float, default=1.0)
    a = ap.parse_args()

    for d in (INBOX, OUTBOX, DONE):
        d.mkdir(parents=True, exist_ok=True)

    print("=" * 68)
    print(" RBMN AGENT")
    print("=" * 68)
    print(f" repo    : {ROOT}")
    print(f" inbox   : {INBOX}")
    print(f" outbox  : {OUTBOX}")
    print(f" shell   : {'ENABLED' if a.allow_shell else 'off (--allow-shell to enable)'}")
    h = health()
    print(f" backend : {h.get('version') or 'NOT RESPONDING (that is fine, it can be restarted)'}")
    print()
    print(" Leave this window open. Ctrl+C to stop.")
    print("=" * 68)
    log("agent started")

    started_at = time.time()
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
        time.sleep(a.poll)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nstopped.")
        sys.exit(0)
