"""v1.237 — LEFT and RIGHT need different wording.  Measured, not guessed.

A SECOND run of `halfway` over the same 16 rows split cleanly by direction, and
that split is the whole finding:

    halfway  run 1    left  6 of 8 in band      right  8 of 8
             run 2    left  3 of 8              right  8 of 8
             -------------------------------------------------
             both     left  9 of 16  (56%)      right 16 of 16  (100%)

Right is perfect twice.  Left is a coin flip.  If the true rate were the same on
both sides, right going 16 for 16 has probability about 0.02 — so this is a real
asymmetry in the model, not run-to-run noise.

`frame` — the wording that overshot and lost v1.236 — turns out to be the answer
for LEFT specifically.  Counted on the 25-45 degree target window, per side:

    LEFT rows        frame   7 of 8      halfway   ~5 of 16
    RIGHT rows       frame   2 of 8      halfway   14 of 16

frame's overshoot is entirely on the RIGHT: +43.4 +48.2 +51.1 +48.4 +47.6 +53.0
+49.4 against a left side that lands -26.3 -40.8 -33.2 -32.4 -27.1 -28.3 -42.8.
Same sentence, opposite behaviour by direction.  Neither wording is "better";
each is better on one side.

So the default becomes `auto`: frame on the left, halfway on the right.  Expected
about 21 of 24 in the target window against 3 of 16 for the sentence this
started from.  Both fixed wordings stay available, because the honest reading of
the numbers above is that the model has a directional prior we are steering
around, and a future base or model change could move it back.

The direction words themselves are NOT the bug: left rows produce negative yaw
and right rows positive, consistently, across every variant and both runs.  The
asymmetry is in how far it turns, not which way.
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


rep('''# v1.236: measured, on 64 renders.''',
    '''# v1.237: which fixed wording each direction gets under "auto".  Measured over
# 40 three-quarter renders per side; see this version's changelog entry.  Left
# needs the harder push, right overshoots if it gets one.
TQ_AUTO = {"three_quarter_left": "frame", "three_quarter_right": "halfway"}

# v1.236: measured, on 64 renders.''',
    "auto table")

rep('''TQ_DEFAULT = "halfway"''',
    '''TQ_DEFAULT = "auto"''',
    "default -> auto")

rep('''def _tq_wording(ds: dict) -> str:
    w = str((ds.get("options") or {}).get("tq_wording") or TQ_DEFAULT).lower()
    return w if w in TQ_WORDINGS else TQ_DEFAULT''',
    '''def _tq_wording(ds: dict) -> str:
    """The option as CHOSEN — may be "auto", which is not itself a template."""
    w = str((ds.get("options") or {}).get("tq_wording") or TQ_DEFAULT).lower()
    return w if (w in TQ_WORDINGS or w == "auto") else TQ_DEFAULT


def _tq_mode(ds: dict, angle_key: Optional[str]) -> str:
    """The concrete wording THIS row renders with.

    v1.237: separated from `_tq_wording` because "auto" resolves differently per
    direction, and every caller that reasons about the wording — the prompt, the
    second-reference decision, the stamp recorded on the image — has to resolve
    it the same way.  One function, so they cannot drift apart."""
    w = _tq_wording(ds)
    if w != "auto":
        return w
    return TQ_AUTO.get(str(angle_key or ""), "halfway")''',
    "resolver")

rep('''    mode = _tq_wording(ds)
    edge = "left" if key == "three_quarter_left" else "right"''',
    '''    mode = _tq_mode(ds, key)
    edge = "left" if key == "three_quarter_left" else "right"''',
    "prompt resolves per row")

rep('''        if (it["angle"] in _TQ_ANGLES and _tq_wording(ds) == "tworef"
                and _view == "front"):''',
    '''        if (it["angle"] in _TQ_ANGLES and _tq_mode(ds, it["angle"]) == "tworef"
                and _view == "front"):''',
    "side ref resolves per row")

rep('''                     "tq_wording": (_tq_wording(ds) if it["angle"] in _TQ_ANGLES
                                    else None)})''',
    '''                     # The RESOLVED wording, so an "auto" dataset still records
                     # which sentence actually made each image.
                     "tq_wording": (_tq_mode(ds, it["angle"])
                                    if it["angle"] in _TQ_ANGLES else None)})''',
    "stamp the resolved wording")

rep('''    w = str(body.wording or "").lower()
    if w not in TQ_WORDINGS:
        raise HTTPException(422, f"unknown wording '{body.wording}' — "
                                 f"one of {sorted(TQ_WORDINGS)}")''',
    '''    w = str(body.wording or "").lower()
    if w != "auto" and w not in TQ_WORDINGS:
        raise HTTPException(422, f"unknown wording '{body.wording}' — "
                                 f"one of {sorted(TQ_WORDINGS) + ['auto']}")''',
    "route accepts auto")

rep('''    sample = _angle_text(ds, {"angle": "three_quarter_left"},
                         _by_key(ANGLES, "three_quarter_left"), 2)
    return {"wording": w, "reads": sample, "default": TQ_DEFAULT,
            "target_window": list(TQ_TARGET),''',
    '''    sample = _angle_text(ds, {"angle": "three_quarter_left"},
                         _by_key(ANGLES, "three_quarter_left"), 2)
    return {"wording": w, "reads": sample, "default": TQ_DEFAULT,
            # Under "auto" the two directions read differently, and showing only
            # one of them would misreport half the dataset.
            "reads_right": _angle_text(ds, {"angle": "three_quarter_right"},
                                       _by_key(ANGLES, "three_quarter_right"), 2),
            "resolves_to": ({k: _tq_mode(ds, k) for k in _TQ_ANGLES}
                            if w == "auto" else {k: w for k in _TQ_ANGLES}),
            "target_window": list(TQ_TARGET),''',
    "route reports both sides")

assert src != orig
P.write_text(src, "utf-8")
print(f"patched {P}  ({len(orig)} -> {len(src)} bytes)")
