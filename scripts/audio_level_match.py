"""🔊 Level-match a set of renders before judging them (EBU R128, two-pass).

**The confound this exists to kill.** A file that is 1-3 LUFS louder reads as
"brighter, fuller, better" in an A/B — every listening test that skips level
matching is partly a loudness test. Measured on this fleet's own comparison
set: turbo `A` sat at **-12.9 LUFS** while the XL renders it was being compared
against sat at **-14.5**, and the XL ones were called "muffled".

It also does what the reference ACE-Step pipeline does and ComfyUI's graph does
NOT: normalise, with a true-peak ceiling (upstream recommends -1 dBTP).

    python scripts\\audio_level_match.py MusicTests\\*.flac MusicTests\\*.mp3
    python scripts\\audio_level_match.py --lufs -14 --out MusicTests\\matched *.flac

⚠ Two-pass on purpose: ffmpeg's single-pass `loudnorm` is a DYNAMIC processor —
it changes the mix while measuring it, which is the opposite of what a
comparison needs. Pass 1 measures, pass 2 applies a linear gain.
"""
from __future__ import annotations

import argparse
import glob
import json
import re
import subprocess
import sys
from pathlib import Path

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def measure(fp: Path) -> dict:
    r = subprocess.run(["ffmpeg", "-v", "info", "-i", str(fp),
                        "-af", "loudnorm=print_format=json", "-f", "null", "-"],
                       capture_output=True, text=True, errors="replace")
    txt = (r.stderr or "") + (r.stdout or "")
    # ⚠ the file's own metadata can contain JSON (ComfyUI writes the whole
    # prompt into it), so match the loudnorm block by its KEYS, not by braces.
    blocks = re.findall(r"\{[^{}]*\"input_i\"[^{}]*\}", txt, re.S)
    return json.loads(blocks[-1]) if blocks else {}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("patterns", nargs="*", default=["MusicTests/*"])
    ap.add_argument("--lufs", type=float, default=-14.0)
    ap.add_argument("--peak", type=float, default=-1.0, help="true-peak ceiling")
    ap.add_argument("--out", default="")
    a = ap.parse_args()

    files = [Path(p) for pat in a.patterns for p in sorted(glob.glob(pat))
             if Path(p).suffix.lower() in (".flac", ".mp3", ".wav", ".ogg")]
    if not files:
        print("no audio files matched")
        return 1
    out = Path(a.out or (files[0].parent / "matched"))
    out.mkdir(parents=True, exist_ok=True)
    print(f"target {a.lufs:+.1f} LUFS, true peak {a.peak:+.1f} dBTP → {out}\n")
    for fp in files:
        m = measure(fp)
        if not m:
            print(f"  ⚠ {fp.name}: could not measure")
            continue
        i = float(m["input_i"])
        gain = a.lufs - i
        # a linear gain cannot exceed the peak ceiling — pull it back if it would
        tp = float(m.get("input_tp", -1.0)) + gain
        if tp > a.peak:
            gain -= (tp - a.peak)
        dst = out / (fp.stem + ".flac")
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(fp),
                        "-af", f"volume={gain:.2f}dB", str(dst)], check=True)
        print(f"  {fp.name[:46]:<46} {i:>7.2f} LUFS  {gain:+6.2f} dB → {dst.name}")
    print("\nNow compare THESE — the level difference is no longer part of the test.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
