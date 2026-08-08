"""v1.262 — the wardrobe check has to be INSIDE QC, or repair un-does it.

BACKEND ONLY.

v1.261 shipped the check as its own route and flagged twelve rows. Then I read
the repair loop: it re-renders every flagged image and re-runs QC, and QC
REPLACES `x["qc"]` wholesale. So the sequence that was about to run was

    wardrobe-check  ->  12 rows flagged "he is undressed"
    repair          ->  12 rows re-rendered (dressed, per v1.260)
    QC (inside repair) -> x["qc"] = {...}   <- the bare verdict is gone
    repair reports  ->  "0 still flagged"

...whether or not the re-render actually put clothes on him. A check that a
later step silently erases is worse than no check, because it produces a clean
number.

So the two vision passes now run inside `_qc_blocking`, on the same worker
thread, against the same already-loaded image bytes, and `bare` participates in
`ok` like every other measured flag. Repair converges on the truth.

COST
    Two extra vision calls per image. Measured on the standalone route: 40
    images in 175s, so about 4.4s per image added to a QC pass. All local.

The standalone `/wardrobe-check` route stays — it re-checks a set without paying
for a full QC pass, which is what an audit of an already-QC'd dataset wants.
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


rep('''                issues = [str(x)[:120] for x in (data.get("issues") or [])][:6]''',
    '''                # v1.262: is he dressed?  Two passes, and BARE if either says
                # so — a false positive costs one re-render, a false negative
                # costs a training run. This lives here rather than in its own
                # route because QC overwrites `qc` wholesale, so a verdict
                # written anywhere else is erased by the next repair round.
                _wpass = []
                for _ in range(2):
                    try:
                        _wt = _wiz.ollama_chat_sync([url], vision_model, _ENRICH_SYSTEM,
                                                    _ENRICH_PROMPT, imgs, 0.2, 120.0, False)
                    except Exception:  # noqa: BLE001 — QC must not die for this
                        _wt = None
                    if _wt and _wt.strip():
                        _wpass.append(_wt.strip().strip('"').split("\\n")[0][:220])
                _wv = _ward.verdict(_wpass)
                _seen = _wpass[0] if _wpass else None
                flags["bare"] = _wv["bare"]
                flags["bare_words"] = _wv.get("words") or []
                flags["wardrobe_method"] = _wv["method"]
                flags["wardrobe_why"] = _wv["why"]
                issues = [str(x)[:120] for x in (data.get("issues") or [])][:6]
                if _wv["bare"] is True:
                    issues = [f"he is undressed — {_wv['why']}"] + issues''',
    "wardrobe in qc")

rep('''                ok = (flags["one_person"]
                      and not flags["artifacts"]
                      and flags.get("same_person", True)
                      and flags.get("outfit_ok", True)
                      and flags.get("framing_ok", True)
                      and flags.get("crop_ok", True))''',
    '''                ok = (flags["one_person"]
                      and not flags["artifacts"]
                      and flags.get("same_person", True)
                      and flags.get("outfit_ok", True)
                      and flags.get("framing_ok", True)
                      and flags.get("crop_ok", True)
                      # v1.262. `is not True` on purpose: unmeasured never fails
                      # an image, the same contract as angle, framing and crop.
                      and flags.get("bare") is not True)''',
    "ok includes bare")

rep('''                    for x in cur["items"]:
                        if x["id"] == iid:
                            x["qc"] = {"ok": ok, "checked_at": _now(),
                                       "server": short, **flags, "issues": issues}''',
    '''                    for x in cur["items"]:
                        if x["id"] == iid:
                            x["qc"] = {"ok": ok, "checked_at": _now(),
                                       "server": short, **flags, "issues": issues}
                            # Kept OUTSIDE qc so a re-check does not throw away
                            # the description the caption pass reuses.
                            if _seen:
                                x["seen_clothing"] = _seen''',
    "store seen_clothing")

assert src != orig
P.write_text(src, "utf-8")
print(f"patched {P}  ({len(orig)} -> {len(src)} bytes)")
