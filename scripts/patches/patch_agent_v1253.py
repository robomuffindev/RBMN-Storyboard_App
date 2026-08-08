"""v1.253 — the agent can reach the OTHER machines on the LAN.

The Fizgig box and the two Klein workers are separate machines. Claude's cloud
shell cannot reach any of them, and the device bridge only mounts this repo on
this machine — so the moment work moves to the training box, every measurement
goes back to being copy-paste, which is the thing v1.250 existed to stop.

But the agent is ON the network those machines are on. It already speaks HTTP.
It was just hardcoded to 127.0.0.1:8899.

An `http` job may now name a `host`, and the Worker Helper on the 16GB box, the
Klein workers at 192.168.12.163 and .224, and the Ollama server at .176 all
become things Claude can query and drive directly.

BOUNDED TO PRIVATE ADDRESSES.  127.*, 10.*, 192.168.*, 172.16-31.* and
`localhost` only. A job file cannot make this agent talk to the internet, which
matters because job files are written by something that is not Lorenzo.
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


rep('''HOST = "http://127.0.0.1:8899"
BACKEND_TITLE = "Robomuffin Idea Factory"      # run.bat sets this window title''',
    '''HOST = "http://127.0.0.1:8899"
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
    return u''',
    "private host check")

rep('''def do_http(job: dict) -> dict:
    method = str(job.get("method") or "GET").upper()
    path = str(job.get("path") or "/api/health")
    if not path.startswith("/"):
        path = "/" + path
    body = job.get("body")
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(HOST + path, data=data, method=method)''',
    '''def do_http(job: dict) -> dict:
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
    req = urllib.request.Request(base + path, data=data, method=method)''',
    "host in do_http")

rep('''    out = {"ok": 200 <= code < 300, "status": code,
           "elapsed_s": round(time.time() - t0, 1)}''',
    '''    out = {"ok": 200 <= code < 300, "status": code, "host": base,
           "elapsed_s": round(time.time() - t0, 1)}''',
    "report host")

rep('''    http     any request against the local backend''',
    '''    http     any request against the local backend, or any PRIVATE address on
             the LAN via "host": "http://192.168.12.x:port"''',
    "docstring")

assert src != orig
P.write_text(src, "utf-8")
print(f"patched {P}  ({len(orig)} -> {len(src)} bytes)")
