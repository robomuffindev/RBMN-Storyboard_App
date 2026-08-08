"""v1.241 — stop asking the vision model about framing.  It is 0 for 12.

A QC pass over the twelve full-body rows just came back with ALL TWELVE flagged
`framing_ok: false`, eleven of them carrying the phrase "The image is not a full
body shot, head to feet."

I have opened those twelve images.  Every one shows the whole man, head to feet,
with margins.  Seven of them were not even re-rendered since the previous pass,
so the same pictures moved from 8 flagged to 12 flagged.

That is worse than the v1.232 measurement that made framing advisory, and it is
worse in the direction v1.232's prompt edit was supposed to fix.  Telling a model
to be careful about a judgement it cannot make does not make it able to make it.

So the question is withdrawn.  `framing_ok` and `cropped_badly` leave the QC
prompt, leave the parsed flags, and leave the flag summary.  The prompt gets
shorter, the JSON gets smaller, and forty images per pass stop carrying a verdict
that has now measured wrong three times running.

What the vision model is still asked, because it has held up: is there exactly
one person, are there artifacts, is the outfit the one requested.  Identity is
ArcFace (v1.218).  Angle is head yaw (v1.234).  Framing has no instrument yet —
`scripts\\framing_probe.bat` measures face-box height against image height across
a whole dataset so bands can be calibrated on real images, the same order of
operations that made the yaw bands hold.  Until that lands, framing is simply
NOT CHECKED, and the summary says so rather than implying a pass.

`expression_ok` stays, alone among the shaky ones, because it is the only signal
of its kind we have and it is already advisory — but it measured wrong on a
plain scowl and an open-mouthed surprise, so it is labelled unreliable in the
summary instead of being quietly trusted.
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


# ── 1. the prompt stops asking ───────────────────────────────────────────────
rep('''    "\\n\\nThis is the ONLY image. Judge it on its own — there is nothing to compare it "
    "against.\\nThe shot type above is what was ASKED FOR, not a fault: if the picture is "
    "that shot type, \\"framing_ok\\" is true. A close-up showing no body is CORRECT for a "
    "face or head-and-shoulders shot, and must not be failed for it. Say nothing about the "
    "person's build, weight, height or proportions — a single image cannot support that "
    "judgement and it is measured elsewhere."
    "\\n\\nBe strict with yourself about two things you have a habit of getting wrong:"
    "\\n* \\"cropped_badly\\" is TRUE only when a part the shot needs genuinely runs off the "
    "edge of the picture. If you can see the whole person including their feet, it is FALSE, "
    "even if they fill the frame. Look for the actual edge before you answer."
    "\\n* On a shot whose angle is BACK, seeing the back of the head IS the head being visible. "
    "A hidden face is correct there and is not a framing problem."
    "\\nAnything you put in \\"issues\\" must agree with the true/false answers above it.")''',
    '''    "\\n\\nThis is the ONLY image. Judge it on its own — there is nothing to compare it "
    "against."
    "\\n\\nThe shot type and the angle above are context, NOT things to check. They are "
    "measured separately and you must not comment on them: say nothing about how close or "
    "far the camera is, whether the whole body is visible, whether anything is cut off by "
    "the edge, or which way the person is facing. A close-up showing no body is correct. "
    "Say nothing about the person's build, weight, height or proportions either — a single "
    "image cannot support that judgement."
    "\\n\\nAnswer only what you were asked for, and put nothing in \\"issues\\" that is not "
    "one of those things.")''',
    "framing note: withdraw the question")

rep('''{{"framing_ok": true/false, "angle_ok": true/false, "expression_ok": true,
  "one_person": true/false, "face_clear": true, "artifacts": true/false,
  "cropped_badly": true/false, "issues": ["short phrase", ...]}}{o_line}''',
    '''{{"one_person": true/false, "artifacts": true/false,
  "issues": ["short phrase", ...]}}{o_line}''',
    "back-row JSON keys")

rep('''Set "angle_ok" false only if the person is facing the camera rather than away from it.
Leave "expression_ok" and "face_clear" true — a hidden face is correct for this shot.
"artifacts" means deformed hands, extra or missing limbs, melted features or garbled text.
"cropped_badly" means part of the subject this shot type needs is cut off by the frame edge."""''',
    '''"artifacts" means deformed hands, extra or missing limbs, melted features or garbled text.
His face is hidden on purpose here; that is correct and is not a problem."""''',
    "back-row explanation")

rep('''{{"framing_ok": true/false, "angle_ok": true/false, "expression_ok": true/false,
  "one_person": true/false, "face_clear": true/false, "artifacts": true/false,
  "cropped_badly": true/false, "issues": ["short phrase", ...]}}

"artifacts" means deformed hands, extra or missing limbs, melted features, garbled text or
similar defects. "cropped_badly" means part of the subject that this shot type needs is cut
off by the frame edge. List every problem you see in "issues".{o_line}"""''',
    '''{{"expression_ok": true/false, "one_person": true/false, "face_clear": true/false,
  "artifacts": true/false, "issues": ["short phrase", ...]}}

"artifacts" means deformed hands, extra or missing limbs, melted features, garbled text or
similar defects. "expression_ok" is whether his face carries the expression named above.
List every problem you see in "issues".{o_line}"""''',
    "main JSON keys")

# ── 2. the parser stops reading them ─────────────────────────────────────────
rep('''                flags = {k: bool(data.get(k)) for k in
                         ("framing_ok", "angle_ok", "expression_ok", "one_person",
                          "face_clear", "artifacts", "cropped_badly")}''',
    '''                # v1.241: `framing_ok` and `cropped_badly` are gone. Measured
                # 0 of 12 on images verified by eye, twice, and the second time
                # was AFTER a prompt written specifically to fix it. `angle_ok`
                # is no longer parsed either — head yaw owns it since v1.234 and
                # reading a second opinion in only invited it to win a race.
                flags = {k: bool(data.get(k)) for k in
                         ("expression_ok", "one_person", "face_clear", "artifacts")}
                flags["framing_checked"] = False''',
    "parsed flags")

rep('''                flags["angle_ok_llm"] = flags["angle_ok"]
                flags["angle_note"] = _awhy''',
    '''                flags["angle_note"] = _awhy''',
    "no llm angle to keep")

# ── 3. the summary stops counting them as failures ───────────────────────────
rep('''    out = {"flagged": 0, "checked": 0, "artifacts": 0, "cropped_badly": 0,
           "framing_off": 0, "angle_off": 0, "expression_off": 0,''',
    '''    out = {"flagged": 0, "checked": 0, "artifacts": 0,
           "angle_off": 0, "expression_off": 0,''',
    "summary keys")

rep('''        if q.get("cropped_badly"):
            out["cropped_badly"] += 1
        if q.get("framing_ok") is False:
            out["framing_off"] += 1
''', '''''', "summary counters")

rep('''    out["top_issues"] = dict(sorted(out["top_issues"].items(),
                                    key=lambda kv: -kv[1])[:6])
    return out''',
    '''    out["top_issues"] = dict(sorted(out["top_issues"].items(),
                                    key=lambda kv: -kv[1])[:6])
    # v1.241: say what is NOT checked, so a clean summary is not read as a
    # clean dataset.  Framing has no instrument yet; expression has an
    # unreliable one.
    out["not_checked"] = ["framing", "crop"]
    out["unreliable"] = ["expression"]
    return out''',
    "declare the gaps")

# ── 4. `ok` no longer mentions flags that no longer exist ────────────────────
rep('''                # v1.232: framing_ok and cropped_badly are ADVISORY.  Measured
                # against the real images, three of three inspected "framing"
                # failures were false — a perfect full body called cropped, a
                # correct back shot failed for having no visible face.  Gating
                # `ok` on them fed the repair loop images that were already fine.
                # What remains are the checks that have held up.
                ok = (flags["one_person"]''',
    '''                # v1.232 made framing and crop advisory; v1.241 removed them
                # outright. What remains are the checks that have held up.
                ok = (flags["one_person"]''',
    "ok comment")


rep("""    v1.232: `framing_off`, `cropped_badly`, `angle_off` and `expression_off` are
    ADVISORY — counted and shown, but they no longer fail an image. Inspecting
    the real pictures showed three of three "framing" failures were false, and a
    flag that fails an image spends a re-render to fix nothing. `flagged` now
    means: not him, more than one person, or a visible artifact.\"\"\"""",
    """    v1.241: `framing_off` and `cropped_badly` are GONE — the vision model
    scored 0 of 12 on images verified by eye, twice, the second time after a
    prompt written specifically to fix it. Framing has no instrument yet and the
    summary says so in `not_checked` rather than implying a pass.

    `angle_off` is now MEASURED (head yaw, v1.234) and is the one advisory
    counter worth acting on. `expression_off` stays advisory and is listed in
    `unreliable`. `flagged` means: not him, more than one person, or a visible
    artifact.\"\"\"""",
    "summary docstring")

assert src != orig
P.write_text(src, "utf-8")
print(f"patched {P}  ({len(orig)} -> {len(src)} bytes)")
