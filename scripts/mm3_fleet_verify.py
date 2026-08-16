"""MM3 fleet verification — the whole go-live in one long-running job.

1. Waits for a byte-exact MM3 text encoder on ANY box (trainer + ZOAI1 are
   both pulling; first clean copy wins).
2. Renders a 15s MM3 track there through the Audio Lab (graph verification).
3. LAN-fans the te out to the remaining boxes — but NEVER starts a peer-copy
   while that box has a RUNNING download to the same file (.part collision).
4. Renders a short track on EVERY box that ends up clean.

Prints a running log; exits 0 only if every box rendered. Safe to re-run.
"""
from __future__ import annotations

import json
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from backend.api.lora_train import _helpers_list  # noqa: E402
from scripts.audit_model_integrity import hf_size, local_size  # noqa: E402

TE_URL = ("https://huggingface.co/Comfy-Org/MiniMax-Music-3/resolve/main/"
          "text_encoders/minimax_music3_text_encoder_pruned_int8_convrot.safetensors")
TE = "minimax_music3_text_encoder_pruned_int8_convrot.safetensors"
API = "http://127.0.0.1:8899"
WAIT_LIMIT = 3.0 * 3600


def log(*a):
    print(time.strftime("[%H:%M:%S]"), *a, flush=True)


def helpers():
    return _helpers_list()


def te_size(h) -> int:
    try:
        return local_size(h["host"], int(h.get("port") or 8765), h["token"],
                          "text_encoders", TE)
    except Exception:  # noqa: BLE001
        return -2


def downloads(h) -> list:
    try:
        base = f"http://{h['host']}:{int(h.get('port') or 8765)}"
        d = json.load(urllib.request.urlopen(
            f"{base}/downloads?token={urllib.parse.quote(h['token'])}",
            timeout=30))
        return d.get("downloads") or []
    except Exception:  # noqa: BLE001
        return []


def te_download_running(h) -> bool:
    return any(dl.get("status") == "running" and TE in str(dl.get("dest") or "")
               for dl in downloads(h))


def start_peer_copy(dst, src):
    peer = (f"http://{src['host']}:{int(src.get('port') or 8765)}"
            f"/serve/model/text_encoders/{urllib.parse.quote(TE)}"
            f"?token={urllib.parse.quote(src['token'])}")
    body = json.dumps({"url": peer, "folder": "text_encoders",
                       "filename": TE}).encode()
    base = f"http://{dst['host']}:{int(dst.get('port') or 8765)}"
    r = json.load(urllib.request.urlopen(urllib.request.Request(
        f"{base}/download/model?token={urllib.parse.quote(dst['token'])}",
        data=body, headers={"Content-Type": "application/json"}), timeout=60))
    log(f"peer-copy {TE} {src['host']} -> {dst['host']} id={r.get('id')}")


def render_on(host: str, want: int) -> bool:
    body = json.dumps({
        "engine": "minimax3", "host": host, "seconds": 15,
        "tags": "A slow cinematic western instrumental: baritone guitar, "
                "sparse percussion, distant harmonica, wide desert air.",
        "lyrics": "", "label": f"mm3 fleet verify {host}",
    }).encode()
    r = json.load(urllib.request.urlopen(urllib.request.Request(
        f"{API}/api/audio-lab/music/generate", data=body,
        headers={"Content-Type": "application/json"}), timeout=120))
    jid = r.get("id")
    log(f"render submitted on {host}: {jid}")
    t0 = time.time()
    while time.time() - t0 < 1800:
        time.sleep(20)
        jobs = json.load(urllib.request.urlopen(
            f"{API}/api/audio-lab/jobs", timeout=30)).get("jobs") or []
        j = next((x for x in jobs if x.get("id") == jid), None)
        if j and j.get("status") in ("done", "error"):
            log(f"render {host}: {j.get('status')} in {j.get('elapsed_s')}s "
                f"file={j.get('file')} err={str(j.get('error') or '')[:300]}")
            return j.get("status") == "done"
    log(f"render {host}: TIMEOUT")
    return False


def main():
    want = hf_size(TE_URL)
    log(f"te expected {want/1e9:.2f} GB; waiting for the first clean copy …")
    t0 = time.time()
    good = None
    while time.time() - t0 < WAIT_LIMIT:
        sizes = {h["host"]: te_size(h) for h in helpers()}
        log("sizes:", {k: f"{max(v,0)/1e9:.2f}GB" for k, v in sizes.items()})
        good = next((h for h in helpers() if sizes[h["host"]] == want), None)
        if good:
            break
        # a helper redeploy/restart kills its in-flight downloads (leaving
        # .part orphans and NO promoted file) — if nobody is downloading the
        # te anymore and nobody has it, re-kick a fresh HF pull on box 1
        if not any(te_download_running(h) for h in helpers()):
            h0 = helpers()[0]
            log(f"no te download running anywhere — re-kicking HF on {h0['host']}")
            body = json.dumps({"url": TE_URL, "folder": "text_encoders",
                               "filename": TE}).encode()
            base = f"http://{h0['host']}:{int(h0.get('port') or 8765)}"
            try:
                urllib.request.urlopen(urllib.request.Request(
                    f"{base}/download/model?token={urllib.parse.quote(h0['token'])}",
                    data=body, headers={"Content-Type": "application/json"}),
                    timeout=60)
            except Exception as e:  # noqa: BLE001
                log("re-kick failed:", repr(e)[:120])
        time.sleep(120)
    if not good:
        log("NO clean te within the wait limit"); sys.exit(1)
    log(f"clean te on {good['host']}")

    ok = {good["host"]: render_on(good["host"], want)}
    if not ok[good["host"]]:
        log("first render FAILED — stopping before fan-out"); sys.exit(1)

    # fan out
    pending = []
    for h in helpers():
        if h["host"] == good["host"] or te_size(h) == want:
            continue
        if te_download_running(h):
            log(f"{h['host']}: its own te download is RUNNING — waiting for it "
                "instead of risking a .part collision")
        else:
            start_peer_copy(h, good)
        pending.append(h)

    t1 = time.time()
    while pending and time.time() - t1 < WAIT_LIMIT:
        time.sleep(120)
        still = []
        for h in pending:
            s = te_size(h)
            if s == want:
                ok[h["host"]] = render_on(h["host"], want)
            elif s != -2 and s != want and not te_download_running(h):
                # covers both a stale/truncated file AND a missing one (a
                # helper restart mid-copy leaves only a .part orphan)
                log(f"{h['host']}: te {'missing' if s < 0 else f'at {s/1e9:.2f}GB'}"
                    " with NO download running — starting peer-copy")
                start_peer_copy(h, good)
                still.append(h)
            else:
                still.append(h)
        pending = still

    for h in pending:
        ok[h["host"]] = False
        log(f"{h['host']}: te never landed clean in time")
    log("RESULT:", json.dumps(ok))
    sys.exit(0 if all(ok.values()) and len(ok) == len(helpers()) else 1)


if __name__ == "__main__":
    main()
