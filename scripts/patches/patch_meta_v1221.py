"""v1.221 — VERSION, pyproject, CHANGELOG."""
import sys
from pathlib import Path
ROOT = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
v = ROOT / "VERSION"
assert v.read_text("utf-8").strip() == "1.220.0", v.read_text("utf-8")
v.write_text("1.221.0\n", "utf-8")
pp = ROOT / "pyproject.toml"
s = pp.read_text("utf-8")
assert s.count('version = "1.220.0"') == 1
pp.write_text(s.replace('version = "1.220.0"', 'version = "1.221.0"', 1), "utf-8")

ENTRY = '''## v1.221.0 -- what the first real ArcFace scan actually said (2026-08-05)

40 images of `dorian-v1`, scored against front base + face ref + left ref. Median 0.4774,
range -0.02 to 0.7377 -- squarely inside Fizgig's stated 0.30-0.70 same-person band. **My
prediction was wrong**: I said renders off a single base would score HIGH; they land in the
ordinary same-person range. No threshold tuning needed.

    identity by which BASE was used        angle misses, by planned angle
      front base   n=5   median 0.705        three_quarter_right   7/7   100%
      left base    n=14  median 0.477        three_quarter_left    7/7   100%
      right base   n=13  median 0.436        profile_left          3/7    43%
      back base    n=4   median 0.125        front                 2/7    29%
                                             back                  1/6    17%
    identity by framing                      profile_right         1/6    17%
      face 0.616 · headshot 0.559 · upper 0.433 · full 0.423

### 1. Three-quarter rows failed 14 of 14, and the ANGLES table says why

`three_quarter_left` draws its base from the **left** view and `three_quarter_right` from the
**right** -- and those are 90-degree PROFILES. So every three-quarter row asks Klein to rotate a
profile BACK to 45 degrees, and Klein preserves the reference's orientation: it lands on profile,
and the checker correctly reports "not a three-quarters view". Profile rows, whose base already
matches what is asked, miss only 17-43%. This is the same lesson as the back-base bug earlier in
the project: **never ask a render for something its reference image works against.**

`options.tq_base` = `"side"` (unchanged default) | `"front"`. Front is a 45-degree turn FROM
front rather than 45 back from profile, and front is also the strongest identity base measured
(0.705 against 0.436-0.477). Both effects point the same way, which is exactly why it needs to
be MEASURED rather than assumed -- the default does not change.

### 2. Back rows were being failed for identity, and that is a false positive

All three "not him" images were back-based (0030 at -0.020, 0018 at 0.108, 0024 at 0.125). The
baselines are frontal, so whatever sliver of face a back shot shows scores low by **geometry**,
not because the character is wrong. Back rows now keep their score -- it is a genuine "how
unusual is this look" signal, and that is precisely what Fizgig's `--warmup_look_outliers`
consumes -- but it no longer fails the image. `identity_scored_against_front: false` records why,
the issue line says "back shot, frontal baselines, not an identity verdict", and
`back_low_likeness` counts them separately from `identity_off`.

### Also visible in the data, not yet acted on

- **2 front rows had no detectable face.** With `cropped_badly: 6` and a top issue of "the image
  is cropped too low, cutting off the subject's head", those are render failures the repair loop
  already handles -- not an ArcFace problem.
- **Identity climbs with face pixels** (face 0.616 -> full 0.423), as expected. Nothing to fix.
- `base_mode` reads `auto` and all four views resolve to stripped bases. He has all five
  reference tags present (front/back/left/right/face), so switching to `dressed` will resolve
  cleanly off those references with no strip run -- which is what v1.217 was built for.

All eight lora suites pass, md5 9be6b1f4460b7c56bb4a45e6252236b9. `test_v1218` needed two
assertions reworded: v1.221 turned the likeness issue line into a template so a back shot can
carry its caveat.

'''
cl = ROOT / "CHANGELOG.md"
s = cl.read_text("utf-8")
assert s.startswith("## v1.220.0"), s[:40]
cl.write_text(ENTRY + s, "utf-8")
print("VERSION 1.221.0 · pyproject · CHANGELOG")
