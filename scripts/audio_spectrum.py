"""🔬 Is it actually MUFFLED, or does it just sound that way? — a free measurement.

"Muffled" is a claim about the top end, and the top end is measurable. This
decodes each file with ffmpeg and reports, per file:

    sr           the container's sample rate
    rolloff85/95 the frequency below which 85% / 95% of the energy sits
    >8k >12k >16k the share of total energy above each band edge
    peak / rms    level, and the crest factor (a squashed mix reads low)
    clip%         samples at full scale — the "harsh/distorted" tell

⭐ Why it matters here: a band-limited decoder (or a resample) shows up as a
CLIFF — energy above 12-16 kHz collapsing to ~0 — which is a different problem
from "the model made a dull arrangement", and only one of them is fixable by
swapping the VAE. Judgement stays with the ear; this only says WHICH question
the ear is answering.

    python scripts\\audio_spectrum.py MusicTests\\*.flac MusicTests\\*.mp3

⚠ mp3 at V0 lowpasses around 19-20 kHz by design, so never compare an mp3's
top band against a FLAC's and call the difference a model defect.
"""
from __future__ import annotations

import glob
import subprocess
import sys
import wave
from pathlib import Path

import numpy as np

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def decode(fp: Path) -> tuple[np.ndarray, int]:
    """→ mono float32 in [-1,1], and the sample rate ffmpeg reports."""
    out = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(fp), "-f", "wav", "-ac", "1", "-"],
        capture_output=True)
    if out.returncode != 0:
        raise RuntimeError(out.stderr.decode()[:300])
    import io
    with wave.open(io.BytesIO(out.stdout)) as w:
        sr = w.getframerate()
        raw = w.readframes(w.getnframes())
        width = w.getsampwidth()
    dt = {1: np.int8, 2: np.int16, 4: np.int32}[width]
    x = np.frombuffer(raw, dtype=dt).astype(np.float32)
    return x / float(np.iinfo(dt).max), sr


def spectrum(x: np.ndarray, sr: int) -> dict:
    n = 8192
    hop = n // 2
    if len(x) < n:
        return {}
    win = np.hanning(n)
    acc = np.zeros(n // 2 + 1)
    frames = 0
    for i in range(0, len(x) - n, hop):
        acc += np.abs(np.fft.rfft(x[i:i + n] * win)) ** 2
        frames += 1
    acc /= max(1, frames)
    freqs = np.fft.rfftfreq(n, 1.0 / sr)
    total = acc.sum() or 1.0
    cum = np.cumsum(acc) / total
    def roll(p: float) -> float:
        return float(freqs[int(np.searchsorted(cum, p))])
    def above(f: float) -> float:
        return float(acc[freqs >= f].sum() / total * 100.0)
    return {"rolloff85": roll(0.85), "rolloff95": roll(0.95),
            "a8k": above(8000), "a12k": above(12000), "a16k": above(16000)}


def main(argv: list[str]) -> int:
    files: list[Path] = []
    for a in argv or ["MusicTests/*"]:
        files += [Path(p) for p in sorted(glob.glob(a))
                  if Path(p).suffix.lower() in (".flac", ".mp3", ".wav", ".ogg")]
    if not files:
        print("no audio files matched")
        return 1
    print(f"{'file':<44} {'sr':>6} {'roll85':>7} {'roll95':>7} "
          f"{'>8k%':>6} {'>12k%':>6} {'>16k%':>6} {'peak':>6} {'rms':>7} {'clip%':>6}")
    for fp in files:
        try:
            x, sr = decode(fp)
        except Exception as e:                                 # noqa: BLE001
            print(f"{fp.name:<44} ⚠ {e}")
            continue
        s = spectrum(x, sr)
        peak = float(np.max(np.abs(x))) if len(x) else 0.0
        rms = float(np.sqrt(np.mean(x ** 2))) if len(x) else 0.0
        clip = float(np.mean(np.abs(x) > 0.999) * 100.0) if len(x) else 0.0
        db = lambda v: (20 * np.log10(v) if v > 1e-9 else -99.0)   # noqa: E731
        print(f"{fp.name[:44]:<44} {sr:>6} {s.get('rolloff85', 0):>7.0f} "
              f"{s.get('rolloff95', 0):>7.0f} {s.get('a8k', 0):>6.2f} "
              f"{s.get('a12k', 0):>6.3f} {s.get('a16k', 0):>6.3f} "
              f"{db(peak):>6.1f} {db(rms):>7.1f} {clip:>6.2f}")
    print("\nrolloff95 well under ~14 kHz on a FLAC = genuinely dull/band-limited.\n"
          "mp3 V0 lowpasses ~19-20 kHz by design — compare like with like.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
