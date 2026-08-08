"""v1.232 — the framing/crop checks are unreliable, so they stop failing images.

I looked at the actual pictures for the first time.  Four of the twelve
full-body rows, against what QC said about them:

  0030 front    "cropped_badly", framing_ok false, "part of the subject cut off
                 by the frame edge"          -> perfect full body, feet and
                                                margins clearly visible
  0035 3/4right "cropped too tightly, cutting off the lower part of the
                 subject's legs and feet"    -> feet fully visible.  (Its ANGLE
                                                miss is real - he faces front.)
  0038 back     "person's head is not visible", framing_ok false
                                             -> textbook back shot.  It wanted a
                                                FACE on a back row.
  0039 3/4right framing_ok TRUE, yet "shot type mismatch" in issues
                                             -> a correct three-quarter view

Three false positives out of three inspected, and a fourth whose prose
contradicts its own boolean.  qwen2.5vl:7b is not reliable at "is this cropped"
or "is this the right shot type", and it fails toward complaining.

That makes two things wrong upstream of this:

  * **v1.228 was solving a phantom.**  The "58% of full-body shots are cropped"
    that motivated the taller canvas and the margin wording was a checker
    artifact.  The canvas change is harmless and the margin wording is honest,
    so both stay - but the premise was wrong and is recorded as wrong.
  * **These flags were spending GPU time.**  `ok` gated on `framing_ok` and
    `cropped_badly`, so every false positive fed the repair loop an image that
    was already fine.

So: framing and crop become ADVISORY.  They are recorded, counted and shown --
a real crop is worth seeing -- but they no longer fail an image on their own.
What still fails an image is what has proven trustworthy: ArcFace identity,
more-than-one-person, and visible artifacts.

The prompt also now tells the checker what NOT to invent, in the affirmative,
with the specific mistakes it actually made.
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


# ── 1. ok no longer gates on the unreliable checks ─────────────────────────
rep('''                ok = (flags["framing_ok"] and flags["one_person"]
                      and not flags["artifacts"] and not flags["cropped_badly"]
                      and flags.get("same_person", True)
                      and flags.get("outfit_ok", True))''',
    '''                # v1.232: framing_ok and cropped_badly are ADVISORY.  Measured
                # against the real images, three of three inspected "framing"
                # failures were false — a perfect full body called cropped, a
                # correct back shot failed for having no visible face.  Gating
                # `ok` on them fed the repair loop images that were already fine.
                # What remains are the checks that have held up.
                ok = (flags["one_person"]
                      and not flags["artifacts"]
                      and flags.get("same_person", True)
                      and flags.get("outfit_ok", True))''',
    "ok: drop the unreliable gates")

rep('''            if s is not None:
                    q["identity_verdict"] = _like.verdict(s)[0]
                    _is_back = it.get("angle") == "back"
                    q["identity_scored_against_front"] = not _is_back
                    q["same_person"] = True if _is_back else s >= _like.ARC_DIFFERENT
                    q["ok"] = bool(q.get("framing_ok") and q.get("one_person")
                                   and not q.get("artifacts") and not q.get("cropped_badly")
                                   and q.get("outfit_ok", True) and q["same_person"])'''.replace("            if s is not None:", "                if s is not None:"),
    '''                if s is not None:
                    q["identity_verdict"] = _like.verdict(s)[0]
                    _is_back = it.get("angle") == "back"
                    q["identity_scored_against_front"] = not _is_back
                    q["same_person"] = True if _is_back else s >= _like.ARC_DIFFERENT
                    # v1.232: same rule as QC — framing and crop are advisory.
                    q["ok"] = bool(q.get("one_person", True)
                                   and not q.get("artifacts")
                                   and q.get("outfit_ok", True) and q["same_person"])''',
    "likeness route: same rule")

# ── 2. tell the checker what it keeps getting wrong ────────────────────────
rep('''    "person's build, weight, height or proportions — a single image cannot support that "
    "judgement and it is measured elsewhere.")''',
    '''    "person's build, weight, height or proportions — a single image cannot support that "
    "judgement and it is measured elsewhere."
    "\\n\\nBe strict with yourself about two things you have a habit of getting wrong:"
    "\\n* \\"cropped_badly\\" is TRUE only when a part the shot needs genuinely runs off the "
    "edge of the picture. If you can see the whole person including their feet, it is FALSE, "
    "even if they fill the frame. Look for the actual edge before you answer."
    "\\n* On a shot whose angle is BACK, seeing the back of the head IS the head being visible. "
    "A hidden face is correct there and is not a framing problem."
    "\\nAnything you put in \\"issues\\" must agree with the true/false answers above it.")''',
    "prompt: name the specific mistakes")

# ── 3. the breakdown says these are advisory ───────────────────────────────
rep('''    """WHY images are flagged, counted.  artifacts / bad crops point at the
    renders; framing / angle / expression misses usually point at the checker."""''',
    '''    """WHY images are flagged, counted.

    v1.232: `framing_off`, `cropped_badly`, `angle_off` and `expression_off` are
    ADVISORY — counted and shown, but they no longer fail an image. Inspecting
    the real pictures showed three of three "framing" failures were false, and a
    flag that fails an image spends a re-render to fix nothing. `flagged` now
    means: not him, more than one person, or a visible artifact."""''',
    "summary docstring")

assert src != orig
P.write_text(src, "utf-8")
print(f"patched {P}  ({len(orig)} -> {len(src)} bytes)")
