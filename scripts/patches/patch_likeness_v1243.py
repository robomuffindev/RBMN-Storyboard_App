"""v1.243 — framing calibrates itself, so nothing is tuned to one man's head.

THE PROBLEM WITH v1.242
    Its bands — 57% / 26% / 13.2% of image height — came from ONE character on
    ONE set of canvas sizes.  Face height depends on how big a person's head is
    relative to their body and on the aspect ratio of the frame.  A different
    character, or a canvas change, moves every number, and the upper/full fence
    has only 2.2 points of air.  Shipped as-is, character two would start
    failing good images and the failure would look like a render problem.

THE FIX
    Judge every image against the MEDIAN FACE HEIGHT OF ITS OWN SHOT TYPE IN ITS
    OWN DATASET.  No absolute number survives, and the check calibrates itself
    per character, per canvas, per model.

WHY A 2x FENCE
    Measured within-shot-type spread across the 36 detected faces, as a ratio to
    that shot type's own median:

        face      0.94 .. 1.04     (n=7)
        headshot  0.63 .. 1.08     (n=8)
        upper     0.72 .. 1.16     (n=9)
        full      0.79 .. 1.06     (n=11)

    The widest real deviation is 0.628 — one headshot — which is 1.59x from its
    median.  A gross shot-type failure is much further out: a full-body row that
    renders as a headshot lands 4.1x from the full-body median.  2.0x sits in
    that gap, closer to the noise than to the failure, and it passes all 36 real
    images while catching anything grossly wrong.

    Stated honestly: 2x CANNOT separate `upper` from `full` reliably.  Their
    medians are only 1.73x apart, so an upper row rendering as a full body is
    inside the fence.  That is a deliberate limit, not an oversight — the
    alternative is a tighter fence that fails the real 1.59x headshot.  Catching
    adjacent-type confusion needs a person mask, which is the same instrument
    crop is waiting on.

WHAT ELSE STAYS ABSOLUTE, AND WHY IT IS ALLOWED TO
    * no detectable face on a NON-BACK row — a face crop with no face is broken
      for any character
    * the face sitting below 60% of the frame height — no portrait shot type of
      any person puts it there (measured across all four types: 16.6%..49.7%)
    Neither depends on head size or canvas.

    `FRAMING_BANDS` survives ONLY as the fallback for a shot type with fewer
    than 4 measurable images, where a median is not worth trusting, and is
    labelled as calibrated on one character wherever it appears.

AND A DATASET-LEVEL CHECK THAT DID NOT EXIST
    The four medians must come out in order — face > headshot > upper > full.
    If they do not, the shot types are not actually different shots, and no
    per-image verdict can tell you that.  Reported once for the dataset.
"""
import sys
from pathlib import Path

P = Path(sys.argv[1] if len(sys.argv) > 1 else "backend/services/likeness.py")
src = P.read_text("utf-8")
orig = src


def rep(old, new, why):
    global src
    n = src.count(old)
    assert n == 1, f"{why}: expected 1 match, found {n}"
    src = src.replace(old, new, 1)


rep('''FACE_CY_MAX = 0.60''',
    '''FACE_CY_MAX = 0.60

# ── v1.243: relative calibration ─────────────────────────────────────────────
# How far an image may sit from the median face height of its OWN shot type in
# its OWN dataset, as a ratio either way.  Measured: the widest real deviation
# across 36 faces is 1.59x (one headshot); a full-body row rendering as a
# headshot is 4.1x out.  2.0 sits in that gap.
FRAMING_REL_TOL = 2.0
# Below this many measurable images in a shot type, its median is not worth
# trusting and the absolute bands are used instead.
FRAMING_MIN_SAMPLES = 4
# Tightest to widest.  Their MEDIANS must come out in this order in any dataset
# whose shot types are actually different shots.
FRAMING_ORDER = ("face", "headshot", "upper", "full")''',
    "relative constants")

rep('''def framing_verdict(framing_key: str, angle_key: str,
                    pv: Optional[Dict[str, Any]]) -> Tuple[Optional[bool], str]:''',
    '''def framing_calibrate(samples: List[Tuple[str, float]]) -> Dict[str, Any]:
    """Turn one dataset's own measurements into that dataset's own bands.

    `samples` is [(framing_key, face_h_ratio), ...] for every image where a face
    was found.  Returns the median per shot type, how many it rests on, and any
    dataset-level problem worth saying out loud.

    v1.243: this exists so no threshold is tuned to one character.  A bigger
    head, a longer canvas or a different model shifts every absolute number; a
    median taken from the dataset being judged shifts with them."""
    groups: Dict[str, List[float]] = {}
    for key, ratio in samples:
        k = str(key or "").lower()
        if ratio is None:
            continue
        groups.setdefault(k, []).append(float(ratio))
    medians: Dict[str, float] = {}
    counts: Dict[str, int] = {}
    for k, vals in groups.items():
        v = sorted(vals)
        counts[k] = len(v)
        medians[k] = round(v[len(v) // 2], 4)

    warnings: List[str] = []
    present = [k for k in FRAMING_ORDER if k in medians]
    order_ok = True
    for a, b in zip(present, present[1:]):
        if medians[a] <= medians[b]:
            order_ok = False
            warnings.append(
                f"{a} shots have a SMALLER median face ({medians[a] * 100:.1f}%) than "
                f"{b} shots ({medians[b] * 100:.1f}%) — those two shot types are not "
                f"coming out as different shots")
    # How much air is between adjacent shot types.  Thin separation is not an
    # error, but it is the thing that breaks first on a new character.
    separation: Dict[str, float] = {}
    for a, b in zip(present, present[1:]):
        if medians[b] > 0:
            separation[f"{a}/{b}"] = round(medians[a] / medians[b], 2)
            if separation[f"{a}/{b}"] < 1.4:
                warnings.append(
                    f"{a} and {b} medians are only {separation[f'{a}/{b}']}x apart — "
                    f"too close to tell those two shot types apart by face size")
    thin = [k for k in present if counts[k] < FRAMING_MIN_SAMPLES]
    if thin:
        warnings.append(
            f"too few measurable images to calibrate {', '.join(thin)} "
            f"(need {FRAMING_MIN_SAMPLES}) — those fall back to the default bands, "
            f"which were calibrated on ONE character")
    return {"medians": medians, "n": counts, "order_ok": order_ok,
            "separation": separation, "warnings": warnings,
            "tolerance": FRAMING_REL_TOL, "min_samples": FRAMING_MIN_SAMPLES}


def framing_verdict(framing_key: str, angle_key: str,
                    pv: Optional[Dict[str, Any]],
                    cal: Optional[Dict[str, Any]] = None) -> Tuple[Optional[bool], str]:''',
    "calibrate")

rep('''    band = FRAMING_BANDS.get(fkey)
    if band is None:
        return None, f"no band defined for shot type '{fkey}'"
    lo, hi = band
    if (lo is None or r > lo) and (hi is None or r <= hi):
        return True, f"face fills {r * 100:.1f}% of the height, right for {fkey}"
    # Say what it looks like instead, which is the actionable half.
    looks = next((k for k, (a, b) in FRAMING_BANDS.items()
                  if (a is None or r > a) and (b is None or r <= b)), None)
    return False, (f"face fills {r * 100:.1f}% of the height; {fkey} wants "
                   f"{_framing_text(fkey)}"
                   + (f", this looks like a {looks} shot" if looks else ""))''',
    '''    # v1.243: the dataset's own median first, so nothing is tuned to one
    # character.  The absolute bands are the fallback, not the rule.
    med = (cal or {}).get("medians", {}).get(fkey)
    n = (cal or {}).get("n", {}).get(fkey, 0)
    if med and n >= FRAMING_MIN_SAMPLES and r > 0:
        dev = max(r / med, med / r)
        if dev <= FRAMING_REL_TOL:
            return True, (f"face fills {r * 100:.1f}% of the height, {dev:.2f}x from the "
                          f"{med * 100:.1f}% median for {fkey} shots in this set")
        bigger = r > med
        return False, (f"face fills {r * 100:.1f}% of the height — {dev:.1f}x "
                       f"{'BIGGER' if bigger else 'SMALLER'} than the {med * 100:.1f}% "
                       f"median for {fkey} shots in this set, so this is not the shot "
                       f"type that was asked for")

    band = FRAMING_BANDS.get(fkey)
    if band is None:
        return None, f"no band defined for shot type '{fkey}'"
    lo, hi = band
    _note = " (default bands — too few images in this set to calibrate)"
    if (lo is None or r > lo) and (hi is None or r <= hi):
        return True, f"face fills {r * 100:.1f}% of the height, right for {fkey}{_note}"
    # Say what it looks like instead, which is the actionable half.
    looks = next((k for k, (a, b) in FRAMING_BANDS.items()
                  if (a is None or r > a) and (b is None or r <= b)), None)
    return False, (f"face fills {r * 100:.1f}% of the height; {fkey} wants "
                   f"{_framing_text(fkey)}"
                   + (f", this looks like a {looks} shot" if looks else "") + _note)''',
    "relative first")

rep('''def framing_health() -> Dict[str, Any]:
    return {"available": available(),
            "bands": {k: [v[0], v[1]] for k, v in FRAMING_BANDS.items()},
            "face_cy_max": FACE_CY_MAX,
            "measures": "face box height as a share of image height",
            "note": ("Calibrated on 40 images of one character: face 64.4-71.4%, "
                     "headshot 29.6-51.0%, upper 14.4-23.0%, full 9.2-12.2%. The "
                     "upper/full fence has 2.2 points of air and is the one to "
                     "re-check first on a new character or canvas size.")}''',
    '''def framing_health() -> Dict[str, Any]:
    return {"available": available(),
            "method": ("each image against the median face height of its own shot type "
                       "in its own dataset"),
            "tolerance": FRAMING_REL_TOL,
            "min_samples": FRAMING_MIN_SAMPLES,
            "absolute_only": ["no face on a non-back row",
                              f"face below {FACE_CY_MAX * 100:.0f}% of the frame height"],
            "fallback_bands": {k: [v[0], v[1]] for k, v in FRAMING_BANDS.items()},
            "face_cy_max": FACE_CY_MAX,
            "measures": "face box height as a share of image height",
            "note": ("v1.243: the fallback bands were calibrated on ONE character and "
                     "are used only for a shot type with fewer than "
                     f"{FRAMING_MIN_SAMPLES} measurable images. Everything else is "
                     "judged relative to the dataset it belongs to, so a different "
                     "head size or canvas needs no re-tuning. The 2x fence cannot "
                     "separate `upper` from `full` — their medians sit 1.7x apart — "
                     "which needs a person mask, the same instrument crop is waiting "
                     "on.")}''',
    "health")

assert src != orig
P.write_text(src, "utf-8")
print(f"patched {P}  ({len(orig)} -> {len(src)} bytes)")
