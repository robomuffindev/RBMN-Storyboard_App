"""Seed a NEW Klein 3.0 character from an existing one's UPLOADED references.

Why this exists
---------------
Testing the character lane means testing what happens to a character that has
NOTHING yet — no views, no anchor, no base set. The `deferred` second pass in
`views/generate` (RIGHT waits for LEFT so it has a profile to mirror as its
direction reference) only runs when neither side exists, so it can ONLY be
exercised on a fresh character.

Making one by hand means finding a photograph and clicking through the UI, and
this repo has a standing rule that test mutations belong on throwaway records,
never on a live character. This makes the throwaway in one call, seeded from a
photograph that is already known-good and already has measured baselines.

⚠ It copies UPLOADS only. A generated view is this app's own output, and handing
it back as source material is the drift loop this lane has been bitten by four
separate ways (v1.275.9, v1.276.16, v1.276.17, v1.276.19).

Everything goes over the backend's own HTTP API on 127.0.0.1 — no project-dir
path derivation, so the cfg.project_dir DB-override gotcha cannot apply here.

    python scripts/k3_new_char_from_ref.py --from dorian --name "ViewTest01"
    python scripts/k3_new_char_from_ref.py --from redv1 --name "T2" \
           --tags front,face --no-fields
    python scripts/k3_new_char_from_ref.py --name "T2" --delete
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
import uuid

HOST = "http://127.0.0.1:8899"
API = "/api/klein3"


def _req(method: str, path: str, body=None, raw=False, host=HOST):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(host + path, data=data, method=method)
    if data is not None:
        r.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(r, timeout=300) as resp:
            blob = resp.read()
    except urllib.error.HTTPError as e:
        sys.exit(f"{method} {path} -> {e.code}: "
                 f"{e.read().decode('utf-8', 'replace')[:500]}")
    return blob if raw else json.loads(blob.decode("utf-8", "replace"))


def _post_file(path: str, blob: bytes, filename: str, fields: dict) -> dict:
    """multipart/form-data by hand — the agent's `http` job kind is JSON only,
    and this needs to run as a `script` job with nothing but the stdlib."""
    b = ("--" + (bound := uuid.uuid4().hex)).encode()
    parts = []
    for k, v in fields.items():
        parts.append(b + b"\r\n"
                     + f'Content-Disposition: form-data; name="{k}"\r\n\r\n'
                     .encode() + str(v).encode() + b"\r\n")
    parts.append(b + b"\r\n"
                 + f'Content-Disposition: form-data; name="file"; '
                   f'filename="{filename}"\r\n'.encode()
                 + b"Content-Type: image/png\r\n\r\n" + blob + b"\r\n")
    payload = b"".join(parts) + b + b"--\r\n"
    r = urllib.request.Request(HOST + path, data=payload, method="POST")
    r.add_header("Content-Type", f"multipart/form-data; boundary={bound}")
    try:
        with urllib.request.urlopen(r, timeout=600) as resp:
            return json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        sys.exit(f"POST {path} -> {e.code}: "
                 f"{e.read().decode('utf-8', 'replace')[:500]}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--from", dest="src", help="source character slug")
    ap.add_argument("--name", required=True, help="name of the new character")
    ap.add_argument("--tags", default="front",
                    help="which uploaded ref tags to copy (default: front)")
    ap.add_argument("--no-fields", action="store_true",
                    help="do not copy the description fields")
    ap.add_argument("--delete", action="store_true",
                    help="DELETE the character with this name and exit")
    a = ap.parse_args()

    want = [t.strip() for t in a.tags.split(",") if t.strip()]
    slug = _req("POST", f"{API}/characters", {"name": a.name}) if not a.delete else None

    if a.delete:
        # slugify lives in the backend; ask it which slug this name became by
        # listing rather than reimplementing the rule out here.
        cands = [c["slug"] for c in _req("GET", f"{API}/characters")["characters"]
                 if c.get("name") == a.name or c.get("slug") == a.name]
        if not cands:
            sys.exit(f"no character named {a.name!r}")
        for s in cands:
            print(json.dumps(_req("POST", f"{API}/characters/{s}/delete")))
        return 0

    if not a.src:
        sys.exit("--from is required unless --delete")
    new = slug["slug"]
    src = _req("GET", f"{API}/characters/{a.src}")

    copied = []
    for r in src.get("refs", []):
        if r.get("source") != "upload" or r.get("tag") not in want:
            continue
        blob = _req("GET", f"{API}/characters/{a.src}/refs/{r['id']}/image",
                    raw=True)
        got = _post_file(f"{API}/characters/{new}/refs", blob,
                         r.get("name") or f"{r['id']}.png",
                         {"tag": r["tag"], "upscale": "true"})
        copied.append({"tag": r["tag"], "from": r["id"], "to": got.get("id"),
                       "size": got.get("size"), "bytes": len(blob),
                       "upscaling": got.get("upscaling")})

    fields = {}
    if not a.no_fields and src.get("fields"):
        fields = _req("POST", f"{API}/characters/{new}/fields",
                      {"fields": src["fields"]}).get("fields", {})

    after = _req("GET", f"{API}/characters/{new}")
    print(json.dumps({"slug": new, "name": a.name, "copied": copied,
                      "fields": sorted(fields),
                      "refs_now": [{"tag": x.get("tag"), "source": x.get("source")}
                                   for x in after.get("refs", [])],
                      "has_base": bool((after.get("base") or {}).get("active")),
                      }, indent=1))
    if not copied:
        sys.exit(f"NOTHING COPIED — {a.src} has no uploaded refs tagged "
                 f"{'/'.join(want)}. The character was created empty.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
