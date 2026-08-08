"""v1.255 — the export's dataset config used relative paths from the wrong folder.

FOUND BY RUNNING IT.  The first real Krea 2 training run failed 90 seconds in:

    INFO:fizgig.dataset.image_dataset:glob images in ./images
    INFO:fizgig.dataset.image_dataset:found 0 images
    INFO:fizgig.dataset.image_dataset:total batches: 0
    RuntimeError: No training items - run the krea2 cache scripts first.

`dataset_fizgig.toml` says:

    image_directory = "./images"
    cache_directory = "./cache/dorian-v1-b1966f"

and `fizgig_run.py` runs every step with `cwd=<fizgig checkout>` — deliberately,
because the scripts do `from fizgig.krea2 import ...` and that only resolves
from the checkout. So Fizgig resolved `./images` against `D:\\Fitzgig\\Fizgig`
and globbed an empty folder. The cache steps "succeeded" over zero images, and
the failure surfaced at the training step with a message about the cache.

Relative paths in a config that travels between machines, consumed by a process
whose working directory is a third place, cannot work. They were never going to.

THE FIX
    `fizgig_run.py` writes `dataset_fizgig.resolved.toml` next to itself with
    `image_directory` and `cache_directory` rewritten to absolute paths under
    the folder the zip was actually unpacked into, and hands THAT to all three
    scripts. The shipped toml stays relative and readable; the resolved one is
    what runs, and it is printed so the paths are visible before anything starts.

    This also puts the latent/text cache beside the dataset instead of inside
    the Fizgig checkout, where a second dataset would have collided with it.

WHAT THE DRY RUN MISSED, AND WHY
    `--dry-run` printed the exact commands and every one was correct — the
    failure is in a file those commands POINT AT, resolved by a working
    directory the printout does not show. A dry run that only echoes commands
    cannot catch this. So the runner now resolves and writes the config even
    under `--dry-run`, prints the absolute image directory, and says how many
    images are actually there.
"""
import sys
from pathlib import Path

P = Path(sys.argv[1] if len(sys.argv) > 1 else "backend/api/lora.py")
src = P.read_text("utf-8")
orig = src


def rep(old, new, why):
    global src
    n = src.count(old)
    assert n == 1, f"{why}: expected 1 match, found {n}"
    src = src.replace(old, new, 1)


rep('''    py = a.python
    cfgp = str(HERE / "dataset_fizgig.toml")''',
    '''    py = a.python

    # v1.255: the shipped toml uses relative paths, and every step runs with
    # cwd=<fizgig checkout> so the scripts can import `fizgig.krea2`. Fizgig
    # therefore resolved "./images" against the CHECKOUT and found nothing --
    # the first real run died with "No training items" after caching zero
    # images. Rewrite the paths to absolute, against wherever this zip actually
    # got unpacked, and run from that.
    src_toml = HERE / "dataset_fizgig.toml"
    if not src_toml.is_file():
        die("dataset_fizgig.toml is missing next to this script")
    lines, img_dir, cache_dir = [], str(HERE / "images"), str(HERE / "cache" / DS_ID)
    for ln in src_toml.read_text("utf-8").splitlines():
        s = ln.strip()
        if s.startswith("image_directory"):
            ln = 'image_directory = ' + json.dumps(img_dir)
        elif s.startswith("cache_directory"):
            ln = 'cache_directory = ' + json.dumps(cache_dir)
        lines.append(ln)
    resolved = HERE / "dataset_fizgig.resolved.toml"
    resolved.write_text("\\n".join(lines) + "\\n", "utf-8")
    cfgp = str(resolved)

    # A dry run that only echoes commands cannot catch a bad path INSIDE the
    # file those commands point at. So say what the config resolves to, and
    # count what is actually there, before anything starts.
    have = len(list((HERE / "images").glob("*.png")))
    print("")
    print("  images dir    " + img_dir + "   (" + str(have) + " png)")
    print("  cache dir     " + cache_dir)
    print("  config        " + cfgp)
    if have == 0:
        die("no PNGs in " + img_dir + " - unzip the whole export and keep the layout")''',
    "resolve the toml")

rep('''import subprocess''', '''import json
import subprocess''', "json import in runner")

assert src != orig
P.write_text(src, "utf-8")
print(f"patched {P}  ({len(orig)} -> {len(src)} bytes)")
