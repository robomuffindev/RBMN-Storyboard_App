"""fetch_ckpt.py -- download one weights artifact from the helper into scripts/_diag/.

Default: epoch 16 of run 20260806-151443-af60 (the checkpoint the likeness scan
picked). Verifies the file's modified time falls inside THIS run's window, since
the output folder is shared with the old 40-epoch run.
"""
import argparse
import json
import urllib.request
from pathlib import Path
from urllib.parse import quote
from helper_token import helper_token as _helper_token  # v1.276.4: token out of source

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "_diag"


def jget(url, timeout=60):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--helper", default="http://192.168.12.202:8765")
    ap.add_argument("--token", default=_helper_token())
    ap.add_argument("--run", default="20260806-151443-af60")
    ap.add_argument("--name", default="dorian-v1-b1966f-000016.safetensors")
    a = ap.parse_args()

    info = jget(f"{a.helper}/runs/{a.run}?token={a.token}&kind=weights")
    t0s, t1s = str(info.get("started") or ""), str(info.get("finished") or "")
    art = next((x for x in info.get("artifacts") or [] if x["name"] == a.name), None)
    if art is None:
        print(f"FAIL: no artifact named {a.name}")
        return 1
    mod = str(art.get("modified") or "")
    if t0s and mod and (mod < t0s or (t1s and mod > t1s)):
        print(f"FAIL: {a.name} modified {mod} is OUTSIDE this run's window "
              f"({t0s} -> {t1s}) -- that file is from the OTHER run. Not downloading.")
        return 1
    print(f"{a.name}: {art['bytes']} bytes, modified {mod} (inside {t0s} -> {t1s})")

    OUT.mkdir(parents=True, exist_ok=True)
    fp = OUT / a.name
    url = f"{a.helper}/runs/{a.run}/artifacts/{quote(a.name)}?token={a.token}"
    with urllib.request.urlopen(url, timeout=600) as r, fp.open("wb") as fh:
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            fh.write(chunk)
    got = fp.stat().st_size
    ok = got == art["bytes"]
    print(f"wrote {fp}  {got} bytes  {'OK' if ok else 'SIZE MISMATCH'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
