"""v1.213.1 — VRAM guidance corrected from Fizgig's MEASURED planner.

He asked whether a 12 GB card can train Krea 2.  Ran Fizgig's own recommender
(`src/fizgig/utils/capabilities.py`, constants measured 28 Jul 2026) instead of
trusting the prose, and it says something more precise than the README does:

    NF4 peak      11.4 GB   (+0.25 GB/MP over 0.25 MP, +15 MB/rank over r32)
    fp8 peak      18.7 GB   INT8 peak 16.2 GB   headroom 1.5 GB
    swap          -0.42 GB per block, 26 max
    "the budget is FREE VRAM, not the number on the box"

So for Krea 2 at 1024/rank 16: NF4 needs ~11.6 + 1.5 = **13.1 GB free**.
  * 16 GB card (~14.8 free) -> NF4, no swap. The good tier.
  * 12 GB card (~11 free)   -> NF4 does NOT fit, and NF4 CANNOT block-swap
    (weights live in `_nf4_packed`; the trainer force-zeroes swap under 4-bit),
    so their planner falls to fp8 + ~22 swapped blocks — the 4x-slower path.

And the measured speed table that makes swapping worth avoiding (5090, Krea 2,
36 imgs @ 0.25 MP, batch 1):

    fp8, no swap   0.85 s/it   20.1 GB
    fp8, swap 20   3.09 s/it   12.3 GB     <- 3.6x slower
    NF4, no swap   0.70 s/it   13.8 GB

One correction to our own sheet falls out of this: **resolution is not the
lever**.  Their measurements put 0.25 -> 1.05 MP at ~+0.15 GB (gradient
checkpointing absorbs it; budgeted 0.25 GB/MP).  Batch is +2.4 GB per extra
image — by far the largest term.  Our musubi sheet told him to drop to 768 when
tight, which buys almost nothing; quantisation and batch are what move VRAM.

Exact-match patcher: every replacement asserts exactly ONE occurrence.
Usage: python patch_lora_v12131.py <path-to-lora.py>
"""
import sys
from pathlib import Path

p = Path(sys.argv[1])
src = p.read_text("utf-8")
orig = src


def rep(old: str, new: str, label: str) -> None:
    global src
    n = src.count(old)
    assert n == 1, f"{label}: expected 1 occurrence, found {n}"
    src = src.replace(old, new)
    print(f"  ok  {label}")


# ── 1. the shared VRAM truth, generated from their constants ─────────────
rep(
    '''def _fizgig_toml(ds: dict, resolution: List[int]) -> str:''',
    '''# Measured by Fizgig on a 5090 (Krea 2, 36 images @ 0.25 MP, batch 1) and encoded
# in their planner `src/fizgig/utils/capabilities.py`.  Peaks are TRAINING-ONLY;
# the budget they plan against is FREE VRAM, not the number on the box.
KREA2_PEAK_GB = {"nf4": 11.4, "int8": 16.2, "fp8": 18.7}
KREA2_HEADROOM_GB = 1.5
KREA2_RES_GB_PER_MP = 0.25      # 0.25 -> 1.05 MP costs ~0.15 GB; checkpointing absorbs it
KREA2_BATCH_GB = 2.4            # per EXTRA image — the largest term by far
KREA2_RANK_GB = 0.015           # per rank above 32
KREA2_SWAP_GB = 0.42            # removed per swapped block, 26 max


def _krea2_need(kind: str, mp: float = 1.05, rank: int = 16, batch: int = 1) -> float:
    return (KREA2_PEAK_GB[kind]
            + KREA2_BATCH_GB * max(0, batch - 1)
            + KREA2_RES_GB_PER_MP * max(0.0, mp - 0.25)
            + KREA2_RANK_GB * max(0, rank - 32))


def _vram_table(mp: float = 1.05, rank: int = 16) -> str:
    """What actually runs on each card, from their numbers rather than tiers."""
    import math
    rows = []
    for card, free in (("12 GB", 11.0), ("16 GB", 14.8), ("24 GB", 22.5), ("32 GB", 30.0)):
        nf4 = _krea2_need("nf4", mp, rank)
        i8 = _krea2_need("int8", mp, rank)
        fp8 = _krea2_need("fp8", mp, rank)
        if free >= i8 + KREA2_HEADROOM_GB:
            plan = "INT8 W8A8, no swap  (fastest + most accurate)"
        elif free >= nf4 + KREA2_HEADROOM_GB:
            plan = "NF4 4-bit, no swap  (--quantize_4bit)"
        elif free >= fp8 + KREA2_HEADROOM_GB:
            plan = "fp8, no swap"
        else:
            swap = min(26, math.ceil((fp8 - (free - KREA2_HEADROOM_GB)) / KREA2_SWAP_GB))
            plan = f"fp8 + {swap} blocks swapped  (NF4 cannot swap; ~4x slower)"
        rows.append(f"#   {card:<6} ~{free:>4.1f} GB free   {plan}")
    return "\\n".join(rows)


def _fizgig_toml(ds: dict, resolution: List[int]) -> str:''',
    "vram model + table",
)

# ── 2. fizgig sheet: replace the tier ladder with the computed table ────
rep(
    '''# ── VRAM: quantise FIRST, swap only if you must ──────────────────────────
# Fizgig's docs are blunt about it: "Swapping is the slow path (4.4x the time,
# 4x the CPU): quantise first, and only swap when even NF4 will not fit."
#   --quantize_4bit    NF4 frozen base, ~5.6 GB resident, block swap forced OFF
#                      -> the right default on a 16 GB card
#   default fp8        ~14 GB resident; then the ladder is
#                        32 GB -> --blocks_to_swap 0     24 GB -> 12
#                        16 GB -> 20                     10-14 GB -> 26
#   --quant_int8 bf16  ~18.6 GB, needs 24 GB, but the FASTEST measured
#                      (0.637 s/it vs NF4 0.709 on a 5090) and ~7x more accurate
#                      than NF4 in forward error
#   --compile_blocks auto   ~2x faster steady-state on the INT8 path (needs
#                      triton; on Windows also the MSVC C++ Build Tools)''',
    '''# ── VRAM: what actually runs, from Fizgig's own measured planner ─────────
# (src/fizgig/utils/capabilities.py — peaks are TRAINING-ONLY, and the budget is
#  FREE VRAM, not the number on the box: a browser or a running ComfyUI counts.)
#
#   PEAK at this run shape:  NF4 ~{nf4:.1f} GB   INT8 ~{i8:.1f} GB   fp8 ~{fp8:.1f} GB
#   plus 1.5 GB headroom, minus 0.42 GB per swapped block (26 max)
#
{table}
#
# Measured speed (5090, Krea 2, 36 images @ 0.25 MP, batch 1):
#   fp8, no swap   0.85 s/it   20.1 GB
#   fp8, swap 20   3.09 s/it   12.3 GB    <- swapping is ~3.6x slower
#   NF4, no swap   0.70 s/it   13.8 GB
# Their rule, verbatim: "Swapping is the slow path (4.4x the time, 4x the CPU):
# quantise first, and only swap when even NF4 will not fit."
#
#   --quantize_4bit   NF4 frozen base. Fits 16 GB with no swap. CANNOT swap.
#   --quant_int8 bf16 W8A8: needs ~24 GB, but the FASTEST measured
#                     (0.637 s/it vs NF4 0.709 on a 5090) and ~7x more accurate
#                     than NF4 in forward error (8 bits beat 4)
#   (default)         dynamic fp8 + --blocks_to_swap N when it will not fit
#
# ⚠ NF4 CANNOT block-swap — the weights live in `_nf4_packed` and the trainer
#   force-zeroes blocks_to_swap under 4-bit. So NF4 either fits or it doesn't;
#   below its footprint the only combination that runs is fp8 + heavy swap.
#
# ⚠ RESOLUTION IS NOT THE LEVER. 0.25 -> 1.05 MP costs about 0.15 GB (gradient
#   checkpointing absorbs it). BATCH is +2.4 GB per extra image, and rank is
#   ~15 MB each. Keep batch 1 and change the quantisation, not the picture size.
#
#   --compile_blocks auto   ~2x faster steady-state on the INT8 path (needs
#                      triton; on Windows also the MSVC C++ Build Tools)''',
    "fizgig sheet vram block",
)

# ── 3. musubi sheet: same correction (it told him to drop resolution) ───
rep(
    '''    res = max(resolution)
    steps''',
    '''    steps''',
    "musubi: drop the now-unused res",
)
rep(
    '''# ── the one knob: --blocks_to_swap (max 26 of 28) ────────────────────────
#   OOM?            raise it (20 -> 22 -> 24 -> 26) and/or drop resolution
#   VRAM to spare?  lower it — every swapped block costs CPU<->GPU bandwidth,
#                   which is what makes the step time, not the GPU
#   Still tight?    add --gradient_checkpointing_cpu_offload, then --split_attn,
#                   then train at {min(res, 768)} instead of {res}''',
    '''# ── the one knob: --blocks_to_swap (max 26 of 28) ────────────────────────
#   OOM?            raise it (20 -> 22 -> 24 -> 26)
#   VRAM to spare?  lower it — every swapped block costs CPU<->GPU bandwidth,
#                   which is what makes the step time, not the GPU
#   Still tight?    add --gradient_checkpointing_cpu_offload, then --split_attn
#
#   ⚠ Do NOT reach for resolution first. Measured on Krea 2: 0.25 -> 1.05 MP
#   costs about 0.15 GB (gradient checkpointing absorbs it), while an extra
#   BATCH image costs 2.4 GB. Keep batch 1; change quantisation and swap.''',
    "musubi sheet resolution note",
)

# ── 4. fill the computed numbers into the fizgig sheet ─────────────────
rep(
    '''def _fizgig_commands(ds: dict, n: int) -> str:''',
    '''def _fizgig_commands(ds: dict, n: int, mp: float = 1.05, rank: int = 16) -> str:''',
    "fizgig_commands signature",
)
rep(
    '''    trig = ds.get("trigger", "sks")
    epochs = max(10, min(round(n * 1.2), 40))
    return f"""# ── Krea 2 LoRA — Fizgig headless (shootthesound/Fizgig) ─────────────────''',
    '''    trig = ds.get("trigger", "sks")
    epochs = max(10, min(round(n * 1.2), 40))
    nf4 = _krea2_need("nf4", mp, rank)
    i8 = _krea2_need("int8", mp, rank)
    fp8 = _krea2_need("fp8", mp, rank)
    table = _vram_table(mp, rank)
    return f"""# ── Krea 2 LoRA — Fizgig headless (shootthesound/Fizgig) ─────────────────''',
    "fizgig_commands computes the table",
)

# ── 5. the notes carry the 12 GB answer ────────────────────────────────
rep(
    '''Reported low-VRAM runs: **RTX 3060 12 GB — peak ~10.5 GB, 7.2–7.8 s/step at 512², rank 16,
blocks_to_swap 22**; RTX 4070 12 GB — rank 32, blocks_to_swap 26, ~2 h for 2000 steps.''',
    '''**Which card?** From Fizgig's measured planner (peaks are training-only; the budget is FREE
VRAM, not the number on the box):

| card | free | what runs |
|---|---|---|
| 12 GB | ~11 GB | NF4 does **not** fit (~13 GB needed) and NF4 cannot block-swap → fp8 + ~22 swapped blocks, the ~4× slower path. Workable, not pleasant. |
| **16 GB** | ~14.8 GB | **NF4 4-bit, no swap — the good tier.** ~0.70 s/it class. |
| 24 GB+ | ~22 GB | INT8 W8A8, no swap: fastest measured and ~7× more accurate than NF4. |

Measured on a 5090 (36 images @ 0.25 MP, batch 1): fp8 no-swap 0.85 s/it / 20.1 GB · fp8 swap-20
3.09 s/it / 12.3 GB · NF4 no-swap 0.70 s/it / 13.8 GB. **Resolution is not the VRAM lever** —
0.25 → 1.05 MP costs ~0.15 GB, while an extra batch image costs 2.4 GB.

Reported low-VRAM runs on the musubi route: **RTX 3060 12 GB — peak ~10.5 GB, 7.2–7.8 s/step at
512², rank 16, blocks_to_swap 22**; RTX 4070 12 GB — rank 32, blocks_to_swap 26, ~2 h for 2000
steps.''',
    "notes carry the card table",
)

assert src != orig
p.write_text(src, "utf-8")
print(f"patched {p} ({len(orig)} -> {len(src)} bytes)")
