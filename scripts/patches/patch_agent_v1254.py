"""v1.254 — the agent can send a file, so the 59MB dataset moves itself.

The Worker Helper takes a dataset as a raw zip in the POST body
(`POST /datasets/<name>`). The agent's `http` job sends JSON, so the one step
between a verified export and a training run — getting 59MB from the app machine
to the training box — was the one step that still needed a human.

`upload` reads a file and POSTs its bytes. Same private-address fence as `http`.

The source path is NOT confined to `scripts\\`, unlike `script` jobs: the export
lives under the project directory (`C:\\Users\\hexum\\RBMN-Projects\\...`), which
is the whole point. It is a read, and it goes to a private address.
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


rep('''def _resolve_script(name: str) -> Path:''',
    '''def do_upload(job: dict) -> dict:
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


def _resolve_script(name: str) -> Path:''',
    "upload")

rep('''HANDLERS = {"http": do_http, "script": do_script, "restart": do_restart,
            "ping": do_ping, "reload": do_reload}''',
    '''HANDLERS = {"http": do_http, "upload": do_upload, "script": do_script,
            "restart": do_restart, "ping": do_ping, "reload": do_reload}''',
    "register")

rep('''    ping     version, uptime, what is pending''',
    '''    upload   POST a file's bytes to a private host (the helper takes a zip)
    ping     version, uptime, what is pending''',
    "docstring")

assert src != orig
P.write_text(src, "utf-8")
print(f"patched {P}  ({len(orig)} -> {len(src)} bytes)")
