"""v1.210.1 — back-angle rows were asking for the impossible.

Lorenzo, after the repair loop took 15 flags down to 6: "most of them use the
back base — is it possible these generated non-back-facing scenes with the back
base?"  Measured on the real plan, and yes:

  * 6 of 40 rows were back-angle (15%), and 2 of those were `headshot` framing —
    a close-up portrait of the back of a head.
  * their prompt read "photographed as a close-up portrait … seen from directly
    behind, his back to the camera, with a thoughtful expression. His face …
    exactly the ones in image 1" — while image 1 is a back view with NO face.
    Asked for an expression it cannot show and a face it cannot see, the model
    invents one and turns the body toward the camera. That is the front-facing
    render off a back base he saw.

Fixes, all in the PLAN so nothing downstream has to compensate:
  * back angle only for framings that actually show the body (upper / full);
  * angles are now WEIGHTED — a character LoRA needs mostly face-bearing data,
    so back drops from 1-in-6 to ~1-in-10;
  * a back row carries NO expression (it cannot be seen), so the render prompt,
    the caption and the QC question all stop mentioning one — captioning an
    expression that is not visible teaches the model a word it cannot satisfy;
  * a back row's identity clause names hair, build and clothing instead of the
    face, and states affirmatively that the camera sees the back of his head;
  * the QC prompt tells the checker the face is hidden BY DESIGN on those rows.

Exact-match patcher: every replacement asserts exactly ONE occurrence.
Usage: python patch_lora_v12101.py <path-to-lora.py>
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


# ── 1. which framings may face away, and how often ───────────────────────
rep(
    '''# Face and headshot rows carry no body pose — captioning a pose the crop cannot
# show teaches the model a word it can never satisfy.
_POSELESS = {"face", "headshot"}''',
    '''# Face and headshot rows carry no body pose — captioning a pose the crop cannot
# show teaches the model a word it can never satisfy.
_POSELESS = {"face", "headshot"}

# Only shots that show the body may face away: a close-up of the back of a head
# is not a portrait, cannot pass a face check, and teaches nothing.
_BACK_OK = {"upper", "full"}

# Angle mix.  A character LoRA lives on face-bearing data, so the six angles are
# NOT dealt evenly: the rotation below is the deal order, and 'back' appears once
# per ten rows instead of one in six.
_ANGLE_MIX = ["front", "three_quarter_left", "profile_left", "front",
              "three_quarter_right", "profile_right", "three_quarter_left",
              "back", "three_quarter_right", "front"]''',
    "_BACK_OK + _ANGLE_MIX",
)

# ── 2. the plan deals from the weighted mix and drops the expression ─────
rep(
    '''    seq = [angles[k % len(angles)] for k in range(len(rows))]
    for a_i, fr in enumerate(rows):
        if fr[0] == "face" and seq[a_i][0] == "back":
            for b_i, fr2 in enumerate(rows):
                if fr2[0] != "face" and seq[b_i][0] != "back":
                    seq[a_i], seq[b_i] = seq[b_i], seq[a_i]
                    break''',
    '''    keyed = {a[0]: a for a in angles}
    mix = [keyed[k] for k in _ANGLE_MIX if k in keyed] or angles
    seq = [mix[k % len(mix)] for k in range(len(rows))]
    # a face or headshot row may never face away — swap it with a body row so the
    # angle counts stay exactly as dealt
    for a_i, fr in enumerate(rows):
        if seq[a_i][0] == "back" and fr[0] not in _BACK_OK:
            for b_i, fr2 in enumerate(rows):
                if fr2[0] in _BACK_OK and seq[b_i][0] != "back":
                    seq[a_i], seq[b_i] = seq[b_i], seq[a_i]
                    break
            else:                      # nowhere to put it: face the front instead
                seq[a_i] = keyed.get("front", angles[0])''',
    "weighted deal + back swap",
)
rep(
    '''            "framing": fr[0], "angle": seq[i][0],
            "expression": _spread(exprs, i)[0],''',
    '''            "framing": fr[0], "angle": seq[i][0],
            # an expression is invisible from behind — no row should claim one
            "expression": None if seq[i][0] == "back" else _spread(exprs, i)[0],''',
    "no expression on back rows",
)

# ── 3. render prompt: never ask a back view for a face ───────────────────
rep(
    '''    fr = _by_key(FRAMINGS, item["framing"])
    ang = _by_key(ANGLES, item["angle"])
    ex = _by_key(EXPRESSIONS, item["expression"])
    li = _by_key(LIGHTING, item["lighting"])
    bg = _by_key(BACKGROUNDS, item["background"])
    bits = [
        f"The person from image 1, photographed as {fr[3]}, {ang[2]}, with {ex[1]}.",
        "His face, his hairstyle, his skin, his build, his weight, his height, his limb "
        "thickness and his proportions are exactly the ones in image 1.",
    ]''',
    '''    fr = _by_key(FRAMINGS, item["framing"])
    ang = _by_key(ANGLES, item["angle"])
    li = _by_key(LIGHTING, item["lighting"])
    bg = _by_key(BACKGROUNDS, item["background"])
    back = item["angle"] == "back"
    if back:
        # Image 1 is the BACK base: it holds no face, so the prompt asks for none.
        # Naming the face here is what produced front-facing renders off a back
        # reference — the model filled in the missing face and turned him round.
        bits = [
            f"The person from image 1, photographed as {fr[3]}, {ang[2]}. "
            "The camera sees the back of his head and his back; his face is turned away "
            "from the camera.",
            "His hairstyle, his hair colour, his skin, his clothing, his build, his weight, "
            "his height, his limb thickness and his proportions are exactly the ones in "
            "image 1.",
        ]
    else:
        ex = _by_key(EXPRESSIONS, item["expression"])
        bits = [
            f"The person from image 1, photographed as {fr[3]}, {ang[2]}, with {ex[1]}.",
            "His face, his hairstyle, his skin, his build, his weight, his height, his limb "
            "thickness and his proportions are exactly the ones in image 1.",
        ]''',
    "back-aware render prompt",
)

# ── 4. caption only what is visible ─────────────────────────────────────
rep(
    '''    ex = _by_key(EXPRESSIONS, item["expression"])
    li = _by_key(LIGHTING, item["lighting"])
    bg = _by_key(BACKGROUNDS, item["background"])
    parts = [f"{fr[2]} of {head}", ang[1], f"with {ex[1]}"]''',
    '''    li = _by_key(LIGHTING, item["lighting"])
    bg = _by_key(BACKGROUNDS, item["background"])
    parts = [f"{fr[2]} of {head}", ang[1]]
    # invisible from behind — and rows planned before v1.210.1 still carry one,
    # so the angle decides, not the field
    if item.get("expression") and item.get("angle") != "back":
        parts.append(f"with {_by_key(EXPRESSIONS, item['expression'])[1]}")''',
    "caption skips an invisible expression",
)

# ── 5. QC: judge a back shot on what it can show ────────────────────────
rep(
    '''def _qc_prompt(item: dict) -> str:
    fr = _by_key(FRAMINGS, item["framing"])
    ang = _by_key(ANGLES, item["angle"])
    ex = _by_key(EXPRESSIONS, item["expression"])
    return f"""This image was generated for a character training set. It was supposed to be
{fr[2]}, {ang[1]}, with {ex[1]}.''',
    '''def _qc_prompt(item: dict) -> str:
    fr = _by_key(FRAMINGS, item["framing"])
    ang = _by_key(ANGLES, item["angle"])
    if item["angle"] == "back":
        # The face is hidden BY DESIGN here; without this the checker flags every
        # back shot for an unclear face and an unreadable expression.
        return f"""This image was generated for a character training set. It was supposed to be
{fr[2]}, {ang[1]} — the person's back to the camera, face deliberately hidden.

Answer with JSON only, exactly these keys:
{{"framing_ok": true/false, "angle_ok": true/false, "expression_ok": true,
  "one_person": true/false, "face_clear": true, "artifacts": true/false,
  "cropped_badly": true/false, "issues": ["short phrase", ...]}}

Set "angle_ok" false only if the person is facing the camera rather than away from it.
Leave "expression_ok" and "face_clear" true — a hidden face is correct for this shot.
"artifacts" means deformed hands, extra or missing limbs, melted features or garbled text.
"cropped_badly" means part of the subject this shot type needs is cut off by the frame edge."""
    ex = _by_key(EXPRESSIONS, item["expression"])
    return f"""This image was generated for a character training set. It was supposed to be
{fr[2]}, {ang[1]}, with {ex[1]}.''',
    "back-aware QC prompt",
)

assert src != orig
p.write_text(src, "utf-8")
print(f"patched {p} ({len(orig)} -> {len(src)} bytes)")
