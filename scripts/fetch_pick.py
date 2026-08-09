"""fetch_pick.py -- pull plateau-epoch previews from the helper for eyeball pick.

Downloads previews for chosen epochs (all 5 prompts) from run 20260806-151443-af60,
filtered to THIS run's time window (the output folder is shared with the old run),
into scripts/_diag/pick/. Read-only against the helper; writes only into _diag.
"""
import argparse
import json
import re
import urllib.request
from pathlib import Path
from helper_token import helper_token as _helper_token  # v1.276.4: token out of source

ROOT = Path(__file__).resolve().parent          # scripts/
OUT = ROOT / "_diag" / "pick"


def jget(url, timeout=60):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def bget(url, timeout=120):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return r.read()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--helper", default="http://192.168.12.202:8765")
    ap.add_argument("--token", default=_helper_token())
    ap.add_argument("--run", default="20260806-151443-af60")
    ap.add_argument("--epochs", default="14,15,16,21,22")
    a = ap.parse_args()

    want = {int(x) for x in a.epochs.split(",") if x.strip()}
    info = jget(f"{a.helper}/runs/{a.run}?token={a.token}&kind=image")
    t0s, t1s = str(info.get("started") or ""), str(info.get("finished") or "")

    picks = []
    for x in info.get("artifacts") or []:
        m = re.search(r"_e(\d{6})_(\d\d)_", x["name"])
        if not m:
            continue
        ep, prompt = int(m.group(1)), m.group(2)
        if ep not in want:
            continue
        mod = str(x.get("modified") or "")
        if t0s and mod and (mod < t0s or (t1s and mod > t1s)):
            continue  # the OLD run's preview
        picks.append((ep, prompt, x["name"]))
    picks.sort()

    OUT.mkdir(parents=True, exist_ok=True)
    got, failed = 0, 0
    for ep, prompt, name in picks:
        fp = OUT / f"e{ep:02d}_p{prompt}.png"
        try:
            fp.write_bytes(bget(
                f"{a.helper}/runs/{a.run}/artifacts/{name}?token={a.token}"))
            got += 1
            print(f"  e{ep:02d} p{prompt}  {fp.stat().st_size:>8} bytes  {name}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"  e{ep:02d} p{prompt}  FAILED: {e}")

    print(f"\n{got} previews written to {OUT}  ({failed} failed)")
    print(f"window {t0s} -> {t1s}, epochs {sorted(want)}")
    return 0 if got and not failed else (0 if got else 1)


if __name__ == "__main__":
    raise SystemExit(main())
