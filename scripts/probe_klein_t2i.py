"""🔎 Why does klein_t2i 400? — READ THE BODY.

The app reported only `400 Client Error: Bad Request for url: …/prompt`, which
names the status and hides the answer. ComfyUI puts the real reason in the
BODY as `node_errors` (the v1.276.49 lesson: `str(HTTPError)` carries none of
it). This submits the shipped Text2Image workflow to a worker exactly as the
app would and prints what the worker actually said.

    python scripts\\probe_klein_t2i.py --host 192.168.12.163

⚠ It submits a REAL render if the graph validates — pass --dry to stop at
validation by pointing the sampler at 1 step.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
WF = ROOT / "workflows" / "KLEIN_EDIT_ULTRA_WORKFLOW_Text2Image.json"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="192.168.12.163")
    ap.add_argument("--prompt", default="a wide dusty rail camp at dawn")
    a = ap.parse_args()
    graph = json.loads(WF.read_text("utf-8"))

    # what the app swaps: the positive prompt and the seed
    for nid, node in graph.items():
        if node.get("class_type") == "CLIPTextEncode" and \
                isinstance(node.get("inputs", {}).get("text"), str):
            node["inputs"]["text"] = a.prompt
            break

    url = f"http://{a.host}:8188/prompt"
    body = json.dumps({"prompt": graph}).encode()
    req = urllib.request.Request(url, data=body, method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            print("ACCEPTED:", r.status, r.read().decode()[:400])
            return 0
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        print(f"HTTP {e.code} — the body IS the answer:\n")
        try:
            d = json.loads(raw)
        except ValueError:
            print(raw[:3000])
            return 1
        print("error:", json.dumps(d.get("error"), indent=1)[:800])
        for nid, ne in (d.get("node_errors") or {}).items():
            print(f"\nnode {nid} ({ne.get('class_type')}):")
            for err in (ne.get("errors") or []):
                print("  -", err.get("type"), "|", err.get("message"),
                      "|", json.dumps(err.get("extra_info"))[:300])
        return 1


if __name__ == "__main__":
    sys.exit(main())
