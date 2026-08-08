"""v1.224 — VERSION, pyproject, CHANGELOG."""
import sys
from pathlib import Path
ROOT = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
v = ROOT / "VERSION"
assert v.read_text("utf-8").strip() == "1.223.0", v.read_text("utf-8")
v.write_text("1.224.0\n", "utf-8")
pp = ROOT / "pyproject.toml"
s = pp.read_text("utf-8")
assert s.count('version = "1.223.0"') == 1
pp.write_text(s.replace('version = "1.223.0"', 'version = "1.224.0"', 1), "utf-8")
ENTRY = '''## v1.224.0 -- the reference image was corrupting the framing verdict (2026-08-05)

Found by reading his raw dump rather than a printed table -- the process change that made this
findable in one pass instead of five.

`framing_off` came back at **30 of 40**. The model's own words on the failures:

    0002 face      "body build and proportions are different"   -> framing FAILED
    0009 headshot  "body build differs", "stature differs"      -> framing FAILED
    0010 headshot  "body proportions are different"             -> framing FAILED
    0004 face      "extreme close-up shot only shows the face"  -> framing FAILED
    0008 face      "body not visible", "no hands"               -> framing FAILED
    0014 headshot  "close-up framing"                           -> framing FAILED

Every one is a complaint about the BODY, on a shot that is a face or head-and-shoulders crop.
v1.212 began sending the character's reference as image 1 so the model could judge identity --
and it then judged FRAMING against the reference's framing too. A close-up cannot win that
comparison. **0004 was failed for being exactly what a `face` shot is.**

v1.218 handed identity to ArcFace and made this pure downside: the reference was still sent,
`identity_score_llm` was still requested, and the answer was thrown away -- while the framing
verdict it corrupted was kept.

The vision model now sees **one image**, the shot, and judges only what a single image can
support: framing, angle, expression, one-person, face-clarity, artifacts, crop, outfit. The
prompt states explicitly that the shot type is the TARGET rather than a fault ("a close-up
showing no body is CORRECT for a face shot"), and forbids build/weight/height judgements, which
a single image cannot support and which ArcFace measures properly. Side effect: half the pixels
per call, so QC should run noticeably faster.

Nine lora suites pass, md5 dd01bf0ea126416249e8c17b1345395f. Four older suites needed honest
updates: they asserted the reference WAS passed and that the LLM's identity score was retained,
both of which were the bug.

'''
cl = ROOT / "CHANGELOG.md"
s = cl.read_text("utf-8")
assert s.startswith("## v1.223.0"), s[:40]
cl.write_text(ENTRY + s, "utf-8")
print("VERSION 1.224.0 · pyproject · CHANGELOG")
