"""Read a Fizgig training run's log and say what actually happened.

A 370 KB log that is 95% progress-bar spam hides the four things worth knowing:
did the loss come down, did the adaptive learning rate do anything, did the
look-outlier warm-up release, and were any images re-captioned. This pulls the
log from the Worker Helper over HTTP and answers those.

Runs on the APP machine (which can reach the helper on the LAN); the training
box needs nothing.

RUN
    scripts\\train_report.py --run 20260806-003544-e0f7
    scripts\\train_report.py                       (the newest run)
    scripts\\train_report.py --helper http://192.168.12.202:8765 --token <tok>
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.request
from helper_token import helper_token as _helper_token  # v1.276.4: token out of source

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


def get(url: str, timeout: float = 120.0):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--helper", default="http://192.168.12.202:8765")
    ap.add_argument("--token", default=_helper_token())
    ap.add_argument("--run", default="")
    a = ap.parse_args()
    base, tok = a.helper.rstrip("/"), a.token

    rid = a.run
    if not rid:
        runs = get(f"{base}/runs?token={tok}")["runs"]
        if not runs:
            print("no runs on the helper.")
            return 1
        rid = runs[0]["id"]
    info = get(f"{base}/runs/{rid}?token={tok}")
    print(f"RUN {rid}  ·  dataset {info.get('dataset')}")
    print(f"  status {info.get('status')}  rc {info.get('rc')}")
    print(f"  {info.get('started')}  ->  {info.get('finished')}")
    if info.get("error"):
        print(f"  ERROR: {info['error']}")

    # The log can be hundreds of KB; pull it in chunks.
    text, off = [], 0
    while True:
        c = get(f"{base}/runs/{rid}/log?token={tok}&offset={off}")
        text.append(c.get("text") or "")
        if c.get("eof") or not c.get("text"):
            break
        off = c["offset"]
    log = "".join(text)
    print(f"  log {len(log)} bytes\n")

    # ── loss, per epoch ──────────────────────────────────────────────────
    # The progress bar carries avr_loss; the last value seen before each
    # "epoch incremented" is that epoch's average.
    marks = []
    cur = None
    for m in re.finditer(r"avr_loss=([0-9.]+)|epoch incremented: (\d+) -> (\d+)", log):
        if m.group(1):
            cur = float(m.group(1))
        elif cur is not None:
            marks.append((int(m.group(2)), cur))
    if marks:
        print("=== average loss, per epoch ===")
        step = max(1, len(marks) // 20)
        for i in range(0, len(marks), step):
            ep, v = marks[i]
            bar = "#" * max(1, int(v / max(x[1] for x in marks) * 40))
            print(f"  epoch {ep:>3}   {v:.4f}  {bar}")
        first, last = marks[0][1], marks[-1][1]
        best = min(marks, key=lambda x: x[1])
        print(f"\n  first {first:.4f} -> last {last:.4f}   "
              f"({'down' if last < first else 'UP'} "
              f"{abs(last - first) / first * 100:.1f}%)")
        print(f"  best  {best[1]:.4f} at epoch {best[0]}")
        tail = [v for _e, v in marks[-8:]]
        if len(tail) >= 4:
            drift = (max(tail) - min(tail)) / (sum(tail) / len(tail))
            print(f"  last 8 epochs vary {drift * 100:.1f}% around their mean — "
                  f"{'plateaued' if drift < 0.08 else 'still moving'}")

    # ── the things that only appear once ─────────────────────────────────
    interesting = [
        ("look-warmup", r"\[look-warmup\][^\n]*"),
        ("adaptive LR", r"\[adaptive_lr\][^\n]*"),
        ("recaption", r"\[auto-recaption\][^\n]*|re-?caption(?:ed|ing)[^\n]*"),
        ("loss watch", r"\[loss-watch\][^\n]*"),
        ("saved", r"saved (?:final )?LoRA[^\n]*"),
        ("samples", r"(?:sample|preview)[^\n]*\.png[^\n]*"),
        ("warnings", r"(?:WARNING|OOM|out of memory|CUDA error)[^\n]*"),
    ]
    for label, pat in interesting:
        hits = re.findall(pat, log, re.I)
        seen, uniq = set(), []
        for h in hits:
            k = re.sub(r"\d", "#", h)[:90]
            if k not in seen:
                seen.add(k)
                uniq.append(h)
        if uniq:
            print(f"\n=== {label} ({len(hits)} line(s), {len(uniq)} distinct) ===")
            for h in uniq[:8]:
                print(f"  {h.strip()[:200]}")

    arts = info.get("artifacts") or []
    if arts:
        print(f"\n=== artifacts ({len(arts)}) ===")
        for f in arts[:3]:
            print(f"  {f['name']:<42} {f['bytes'] / 1e6:>7.1f} MB  {f.get('modified')}")
        if len(arts) > 6:
            print(f"  ... {len(arts) - 6} more ...")
        for f in arts[-3:]:
            print(f"  {f['name']:<42} {f['bytes'] / 1e6:>7.1f} MB  {f.get('modified')}")

    print("\nTell Claude 'report is ready'.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
