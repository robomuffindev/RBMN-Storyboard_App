"""v1.259 — the agent can fetch a file, so previews become something to LOOK at.

`upload` sends. Nothing received. `http` decodes as JSON or text, so a PNG came
back as mojibake or not at all — and the 200 per-epoch previews from the first
training run, which are the only way to judge a checkpoint by eye, were listed
but unreachable.

`download` GETs a URL and writes the bytes to a path under the repo, where the
device bridge can see them and they can actually be opened.

Confined to the repo folder, unlike `upload`'s source: a download WRITES, and a
job file must not be able to drop a file into System32. `upload` reads from
anywhere because the export lives under the project directory; `download` writes
under `scripts\\_diag\\` and nowhere else.
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
    '''def do_download(job: dict) -> dict:
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


def _resolve_script(name: str) -> Path:''',
    "download")

rep('''HANDLERS = {"http": do_http, "upload": do_upload, "script": do_script,
            "restart": do_restart, "ping": do_ping, "reload": do_reload}''',
    '''HANDLERS = {"http": do_http, "upload": do_upload, "download": do_download,
            "script": do_script, "restart": do_restart, "ping": do_ping,
            "reload": do_reload}''',
    "register")

rep('''    upload   POST a file's bytes to a private host (the helper takes a zip)''',
    '''    upload   POST a file's bytes to a private host (the helper takes a zip)
    download GET a file and write it under this repo, so it can be looked at''',
    "docstring")

assert src != orig
P.write_text(src, "utf-8")
print(f"patched {P}  ({len(orig)} -> {len(src)} bytes)")
