"""What is actually inside the training zip — read before anyone trains on it.

The export builds a zip and nobody has ever opened one. Before the Fizgig run
that is a gap worth closing: the zip carries the images, the captions, four
trainer configs, a manifest, the ArcFace look-scores file, and a runner script
that resolves Fizgig's own model paths. If any of those is wrong or missing, the
failure shows up half an hour into a training run instead of here.

Prints the file list, the caption of the first few images, the Fizgig config and
the exact commands the runner will execute. Changes nothing.

RUN
    scripts\\inspect_export.py                    newest export of the newest dataset
    scripts\\inspect_export.py --zip <path>
    scripts\\inspect_export.py --id dorian-v1-b1966f
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request
import zipfile
from pathlib import Path

HOST = "http://127.0.0.1:8899"

# The Windows console is cp1252 by default, and these configs carry a warning
# glyph. Printing them killed the first run of this script with a
# UnicodeEncodeError two lines into a config dump.
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


def api(path: str):
    with urllib.request.urlopen(HOST + path, timeout=60) as r:
        return json.loads(r.read().decode("utf-8"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--zip", default="")
    ap.add_argument("--id", default="")
    a = ap.parse_args()

    zp: Path
    if a.zip:
        zp = Path(a.zip)
    else:
        alls = api("/api/lora/datasets")["datasets"]
        ds_id = a.id or alls[0]["id"]
        info = next((d for d in alls if d["id"] == ds_id), {})
        print(f"DATASET: {info.get('name')}   character: {info.get('char_slug')}   id {ds_id}")
        exports = info.get("exports") or []
        if not exports:
            print("no exports for this dataset yet.")
            return 1
        # The backend serves them; find the file on disk via the project dir it
        # reports, so this works without knowing the layout by heart.
        health = api("/api/lora/health")
        root = Path(health.get("root") or "")
        zp = root / ds_id / "exports" / exports[-1]
        if not zp.exists():
            print(f"expected {zp} — not there. exports listed: {exports}")
            return 1

    print(f"ZIP: {zp}")
    print(f"  {zp.stat().st_size / 1e6:.1f} MB\n")

    with zipfile.ZipFile(zp) as z:
        names = z.namelist()
        # Only the training images. `sample_ref.png` sits at the top level and
        # counting it made the first run report a 41-vs-40 mismatch that was not
        # real — a check that cries wolf is worse than no check.
        imgs = [n for n in names if n.startswith("images/") and n.endswith(".png")]
        caps = [n for n in names if n.startswith("images/") and n.endswith(".txt")]
        other = [n for n in names if not n.startswith("images/")]
        print(f"  {len(imgs)} images · {len(caps)} caption files")
        print("  top level:")
        for n in sorted(other):
            print(f"    {n:<34} {z.getinfo(n).file_size:>8} bytes")

        if len(imgs) != len(caps):
            print(f"\n  MISMATCH: {len(imgs)} images but {len(caps)} captions — "
                  f"a trainer will pair them up wrong.")
        else:
            stems_i = {n[:-4] for n in imgs}
            stems_c = {n[:-4] for n in caps}
            if stems_i == stems_c:
                print("  every image has a caption with a matching name  OK")
            else:
                print(f"  NAME MISMATCH: {sorted(stems_i ^ stems_c)[:6]}")

        print("\n=== first 3 captions ===")
        for n in sorted(caps)[:3]:
            print(f"  {n}")
            print(f"    {z.read(n).decode('utf-8')}")

        for cfg in ("dataset_fizgig.toml", "dataset_kohya.toml",
                    "dataset_aitoolkit.yaml", "dataset_musubi.toml"):
            if cfg in names:
                print(f"\n=== {cfg} ===")
                print(z.read(cfg).decode("utf-8", "replace"))

        for cmds in ("train_krea2_fizgig.txt", "train_krea2_musubi.txt"):
            if cmds in names:
                print(f"\n=== {cmds} ===")
                print(z.read(cmds).decode("utf-8", "replace"))

        if "fizgig_run.py" in names:
            body = z.read("fizgig_run.py").decode("utf-8", "replace")
            print(f"\n=== fizgig_run.py  ({len(body)} bytes) ===")
            print(body)

        look = "images/fizgig_look_scores.json"
        if look in names:
            d = json.loads(z.read(look).decode("utf-8"))
            print(f"\n=== {look} ===")
            if isinstance(d, dict):
                keys = list(d)[:6]
                print(f"  {len(d)} entries · keys look like: {keys}")
                for k in keys[:3]:
                    print(f"    {k}: {d[k]}")
            else:
                print(f"  {type(d).__name__} with {len(d)} entries")
                print(f"    {json.dumps(d[:3], indent=2)[:600]}")

        man = "manifest.json"
        if man in names:
            m = json.loads(z.read(man).decode("utf-8"))
            print("\n=== manifest.json ===")
            print(f"  dataset: {json.dumps(m.get('dataset'), indent=2)}")
            first = (m.get("images") or [{}])[0]
            print(f"  first image record: {json.dumps(first, indent=2)[:900]}")

    print("\nTell Claude 'export inspected'.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
