"""v1.265 — I scored two training runs as if they were one.

`checkpoint_score.py --run 20260806-151443-af60` printed epochs 1 to 40 for a run
that trained 22, with epochs 12-22 listed TWICE, and concluded "use epoch 16".

Fizgig writes into `output_loras/<dataset>/`, which is keyed on the DATASET, not
on the run. Both dorian runs — yesterday's 40-epoch run on the dataset with
twelve underwear images, and today's 22-epoch run on the clean one — wrote their
previews and their weights into the same folder. The listing is the union.

    old run  00:45 -> 09:22   epochs 1-40
    new run  15:24 -> 20:04   epochs 1-22

So "epoch 16, 0.7733" may well have been a checkpoint trained on the bad
dataset, and the recommendation was worthless. Worse than worthless: it would
have shipped the thing this week's work exists to replace.

    dorian-v1-b1966f-000022.safetensors   new run, overwrote the old file
    dorian-v1-b1966f-000030.safetensors   STALE — old run, bad dataset
    dorian-v1-b1966f.safetensors          new run's final

Previews are now filtered to the run's own time window, and anything outside it
is reported as stale rather than silently dropped — a count that should be zero
once the helper gives every run its own folder (v1.217).
"""
import sys
from pathlib import Path

P = Path(sys.argv[1] if len(sys.argv) > 1 else "scripts/checkpoint_score.py")
src = P.read_text("utf-8")
orig = src


def rep(old, new, why):
    global src
    n = src.count(old)
    assert n == 1, f"{why}: expected 1 match, found {n}"
    src = src.replace(old, new, 1)


rep('''    info = jget(f"{a.helper}/runs/{a.run}?token={a.token}&kind=image")
    shots = []
    for x in info.get("artifacts") or []:
        m = re.search(r"_e(\\d{6})_(\\d\\d)_", x["name"])
        if m and m.group(2) == a.prompt:
            shots.append((int(m.group(1)), x["name"]))
    shots.sort()''',
    '''    info = jget(f"{a.helper}/runs/{a.run}?token={a.token}&kind=image")
    # v1.265: Fizgig's output folder is keyed on the DATASET, so every run of the
    # same dataset shares it. Scoring the union of two runs produced epochs 1-40
    # for a 22-epoch run, with 12-22 listed twice, and recommended a checkpoint
    # that may have come from the dataset this run exists to replace.
    t0s, t1s = str(info.get("started") or ""), str(info.get("finished") or "")
    shots, stale = [], 0
    for x in info.get("artifacts") or []:
        m = re.search(r"_e(\\d{6})_(\\d\\d)_", x["name"])
        if not m or m.group(2) != a.prompt:
            continue
        mod = str(x.get("modified") or "")
        # Inclusive window, string compare on ISO timestamps of the same shape.
        # A preview is written DURING the run by definition.
        if t0s and mod and (mod < t0s or (t1s and mod > t1s)):
            stale += 1
            continue
        shots.append((int(m.group(1)), x["name"]))
    shots.sort()
    if stale:
        print(f"  IGNORED {stale} preview(s) outside this run's window "
              f"({t0s} -> {t1s}) — another run wrote into the same output folder.")
        print(f"  Weights in that folder are mixed too: any checkpoint numbered "
              f"above {shots[-1][0] if shots else 0} is from the OTHER run.\\n")
    if len(shots) != len({e for e, _ in shots}):
        print("  WARNING: duplicate epoch numbers survived the filter — the two "
              "runs overlap in time. Trust nothing below.\\n")''',
    "window filter")

assert src != orig
P.write_text(src, "utf-8")
print(f"patched {P}  ({len(orig)} -> {len(src)} bytes)")
