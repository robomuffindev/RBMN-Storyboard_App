"""v1.246 — crop is promoted, on evidence rather than on argument.

`crop_probe` over redv1's 20 images disagreed with the rule I wrote and was
right to: `face` scored 0 of 4 and `headshot` 1 of 4, all for "the top of his
head runs off the top of the frame" — which is what an extreme close-up IS.
With the top-edge check restricted to `upper` and `full` the same 20 images read
20 of 20, and the three real failure modes still fire.

It also did the job it was built for. v1.243 recorded that face height could not
separate `upper` from `full`, medians 1.7x apart against a 1.6x spread:

    subject HEIGHT   upper 81-96%  vs  full 81-95%   — no separation
    bottom EDGE      upper 6 of 6 touching · full 0 of 6 — perfect

So `crop_ok` now gates `ok`, and `not_checked` finally empties. Unmeasured
still never fails an image — no mask, no rembg, or a mask that does not look
like one subject all leave the row alone.
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


rep('''                # v1.245: crop, from a person mask. ADVISORY — recorded and
                # counted, but it does not gate `ok` until it has been measured
                # on real images. Every instrument trusted before measurement
                # this session turned out to be wrong.''',
    '''                # v1.246: crop, from a person mask, and it FAILS an image now.
                # v1.245 shipped it advisory on purpose; the probe then found
                # the rule wrong for close-ups (see subject.py) and the
                # corrected rule reads 20 of 20 on real images.''',
    "qc comment")

rep('''                if _cok is False:
                    issues = [f"crop (advisory): {_cwhy}"] + issues''',
    '''                if _cok is False:
                    issues = [f"crop: {_cwhy}"] + issues''',
    "issue label")

rep('''                ok = (flags["one_person"]
                      and not flags["artifacts"]
                      and flags.get("same_person", True)
                      and flags.get("outfit_ok", True)
                      and flags.get("framing_ok", True))''',
    '''                ok = (flags["one_person"]
                      and not flags["artifacts"]
                      and flags.get("same_person", True)
                      and flags.get("outfit_ok", True)
                      and flags.get("framing_ok", True)
                      and flags.get("crop_ok", True))''',
    "ok includes crop")

rep('''    # v1.245: crop has an instrument, but it is not trusted yet — measured on
    # zero real images at the time it shipped. It moves out of `unreliable` and
    # into `ok` in its own version, once the probe numbers exist.
    out["not_checked"] = [] if out["crop_measured"] else ["crop"]
    out["unreliable"] = ["expression"] + (["crop (advisory, not yet validated)"]
                                          if out["crop_measured"] else [])''',
    '''    # v1.246: crop is measured and trusted — 20 of 20 on real images after the
    # probe corrected the rule. It only reports as unchecked when rembg is
    # genuinely absent, which is a real state and not a silent one.
    out["not_checked"] = [] if out["crop_measured"] else ["crop"]
    out["unreliable"] = ["expression"]''',
    "not_checked")

rep('''            "crop": {**_subj.health(),
                     "status": "ADVISORY — recorded and counted, does not fail an image "
                               "until it has been measured on real images "
                               "(scripts\\\\crop_probe.bat)"},''',
    '''            "crop": {**_subj.health(),
                     "status": "measured — 20 of 20 on real images (v1.246); an image "
                               "whose subject is cut off wrongly now fails QC"},''',
    "status")

# the likeness route recomputes `ok` too and must use the same rule
rep('''                    # v1.232: same rule as QC — framing and crop are advisory.
                    q["ok"] = bool(q.get("one_person", True)
                                   and not q.get("artifacts")
                                   and q.get("outfit_ok", True) and q["same_person"])''',
    '''                    # v1.246: the SAME rule as QC, including the measured
                    # framing and crop checks. When these two drifted apart a
                    # likeness re-score silently un-failed images that QC had
                    # failed for being the wrong shot.
                    q["ok"] = bool(q.get("one_person", True)
                                   and not q.get("artifacts")
                                   and q.get("outfit_ok", True)
                                   and q.get("framing_ok", True)
                                   and q.get("crop_ok", True)
                                   and q["same_person"])''',
    "likeness route rule")

assert src != orig
P.write_text(src, "utf-8")
print(f"patched {P}  ({len(orig)} -> {len(src)} bytes)")
