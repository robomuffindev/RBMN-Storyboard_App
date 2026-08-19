"""🫁 Free smoke test for NARRATION PAUSE TAGS (v1.277.41).

Pure arithmetic on `plan_chunks` — no app, no GPU, no ffmpeg. It exists because
the failure mode is silent and embarrassing: a tag that is not stripped makes
F5 **read the word "pause" out loud**, and a gap attached to the wrong chunk
puts the silence in the wrong place, which sounds like a bad take rather than a
bug.

    python scripts\\pause_tag_smoke.py

⚠⚠ `check()` RETURNS its result — a harness that stops on its first
`if not check(...)` looks exactly like a feature that works (2026-08-17).
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# ⚠ loaded BY PATH: backend.api.audio_lab imports FastAPI, and a free tool that
# needs the app's dependency tree is not a free tool. The module itself only
# needs `re`, so a stub config keeps the import honest.
_SRC = Path(__file__).resolve().parent.parent / "backend" / "api" / "audio_lab.py"

PASS = FAIL = 0


def check(label: str, ok: bool, detail: str = "") -> bool:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  PASS  {label}" + (f"  ({detail})" if detail else ""))
    else:
        FAIL += 1
        print(f"  FAIL  {label}" + (f"  ({detail})" if detail else ""))
    return ok


def load():
    """Import audio_lab's pure helpers without dragging the app in."""
    sys.path.insert(0, str(_SRC.parent.parent.parent))
    try:
        from backend.api.audio_lab import (auto_tag, plan_chunks,
                                            strip_pause_tags)
        return plan_chunks, strip_pause_tags, auto_tag
    except Exception as e:                                       # noqa: BLE001
        print(f"could not import the helpers: {type(e).__name__}: {e}")
        print("(run it with the repo venv: venv\\Scripts\\python scripts\\"
              "pause_tag_smoke.py)")
        raise SystemExit(1)


def main() -> int:
    plan_chunks, strip_pause_tags, auto_tag = load()
    print("🫁 pause tags\n")

    # ⭐ the one that matters most
    check("the tag is REMOVED from what the model speaks",
          "pause" not in strip_pause_tags("Hello. [pause] World.").lower(),
          strip_pause_tags("Hello. [pause] World."))
    check("…and the words survive intact",
          strip_pause_tags("Hello. [pause 900] World.") == "Hello. World.")

    c, g = plan_chunks("A. [pause] B.", 600)
    check("a tag splits the text in two", len(c) == 2, str(c))
    check("…and puts its own gap between them", g[:1] == [400], str(g))
    check("no trailing silence at the very end", g[-1] == 0, str(g))

    _c, g = plan_chunks("A. [pause 900] B.", 600)
    check("a bare number ≥20 reads as MILLISECONDS", g[0] == 900, str(g))
    _c, g = plan_chunks("A. [pause 1.5s] B.", 600)
    check("a unit reads as seconds", g[0] == 1500, str(g))
    _c, g = plan_chunks("A. [pause 2] B.", 600)
    check("a bare number <20 reads as SECONDS", g[0] == 2000, str(g))
    _c, g = plan_chunks("A. [beat] B. [breath] C.", 600)
    check("beat/breath carry their own defaults", g[:2] == [600, 200], str(g))
    _c, g = plan_chunks("A. [BREAK:250] B.", 600)
    check("tags are case-insensitive and accept ':'", g[0] == 250, str(g))

    c, g = plan_chunks("One.\n\nTwo.", 900)
    check("a blank line still uses the PARAGRAPH pause",
          len(c) == 2 and g[0] == 900, f"{c} {g}")

    c, g = plan_chunks("One. [pause 300] Two.\n\nThree.", 900)
    check("tags and paragraphs coexist in one pass",
          len(c) == 3 and g[0] == 300 and g[1] == 900, f"{c} {g}")

    # a tag alone on a line owes its silence to the chunk before it
    c, g = plan_chunks("One.\n\n[pause 800]\n\nTwo.", 100)
    check("a tag on its own still contributes its silence",
          len(c) == 2 and g[0] >= 800, f"{c} {g}")

    c, g = plan_chunks("A. B. C.", 600, sentence_pause_ms=250)
    check("sentence splitting still works alongside tags",
          len(c) == 3 and g[0] == 250 and g[1] == 250, f"{c} {g}")

    c, g = plan_chunks("A. [pause 900] B. C.", 600, sentence_pause_ms=250)
    check("an explicit tag BEATS the sentence default at that spot",
          g[0] == 900 and 250 in g, f"{c} {g}")

    c, _g = plan_chunks("   [pause]   ", 600)
    check("text that is only a tag speaks nothing", not c, str(c))

    c, g = plan_chunks("Hello.", 600)
    check("one sentence stays one chunk with no gap",
          c == ["Hello."] and g == [0], f"{c} {g}")

    check("gaps always line up with chunks",
          all(len(plan_chunks(t, 600, sp)[0]) == len(plan_chunks(t, 600, sp)[1])
              for t in ("A. [pause] B.", "A.\n\nB.", "A. B.", "x")
              for sp in (0, 250)))

    print("\n🪄 auto-tagging\n")
    t = auto_tag("One. Two. Three.")
    check("it tags every sentence end", t.count("[pause") == 2, t)
    check("…and does not tag the very end", not t.rstrip().endswith("]"), t)
    check("the tagged text still speaks the same words",
          strip_pause_tags(t) == "One. Two. Three.", strip_pause_tags(t))
    # ⭐ the one that stops gaps doubling every time he presses the button
    check("AUTO-TAGGING IS IDEMPOTENT",
          auto_tag(t) == t, auto_tag(t))
    check("a hand-placed tag is left alone",
          auto_tag("One. [pause 1200] Two.").count("[pause") == 1,
          auto_tag("One. [pause 1200] Two."))
    e = auto_tag("He waited... Then he left.")
    check("an ellipsis gets its own, longer pause", "[pause 700]" in e, e)
    d = auto_tag("He waited — then he left.")
    check("an em-dash gets a mid-length pause", "[pause 450]" in d, d)
    check("a comma gets NOTHING (F5 already breathes there)",
          "[pause" not in auto_tag("Slowly, he stood"),
          auto_tag("Slowly, he stood"))
    p2 = auto_tag("One. Two.\n\nThree.")
    check("paragraph breaks survive tagging", "\n\n" in p2, repr(p2))
    c, g = plan_chunks(auto_tag("One. Two. Three."), 900)
    check("the tagged text plans into 3 chunks with 350ms gaps",
          len(c) == 3 and g[0] == 350 and g[1] == 350, f"{c} {g}")

    print("\n🔁 RE-tagging (changing the setting must change the text)\n")
    t350 = auto_tag("One. Two. Three.", sentence_ms=350)
    t900 = auto_tag(t350, sentence_ms=900)
    # ⭐⭐ the bug this was written for: idempotence meant a NEW pause value
    # did nothing, and he had to retype every tag by hand.
    check("changing the value RE-VALUES the existing auto tags",
          t900.count("[pause 900]") == 2 and "[pause 350]" not in t900, t900)
    check("…and does not multiply them", t900.count("[pause") == 2, t900)
    check("re-tagging to the SAME value is still a no-op",
          auto_tag(t900, sentence_ms=900) == t900, auto_tag(t900, sentence_ms=900))
    check("the words are untouched by any of it",
          strip_pause_tags(t900) == "One. Two. Three.", strip_pause_tags(t900))
    pin = auto_tag("One. [pause! 1200] Two. Three.", sentence_ms=900)
    check("a PINNED tag survives re-tagging", "[pause! 1200]" in pin, pin)
    check("…while the unpinned ones still update", "[pause 900]" in pin, pin)
    check("a pinned tag is still stripped before the model speaks",
          "pause" not in strip_pause_tags(pin).lower(), strip_pause_tags(pin))
    _c, g = plan_chunks("A. [pause! 1500] B.", 600)
    check("a pinned tag still buys its silence", g[0] == 1500, str(g))
    e2 = auto_tag(auto_tag("He waited... Then he left.", ellipsis_ms=700),
                  ellipsis_ms=1200)
    check("ellipsis tags re-value too",
          "[pause 1200]" in e2 and "[pause 700]" not in e2, e2)
    check("re-tagging never leaves a tag at the very end",
          not auto_tag("One. Two.", sentence_ms=900).rstrip().endswith("]"),
          auto_tag("One. Two.", sentence_ms=900))

    print(f"\n{'ALL PASS' if not FAIL else 'FAILURES'}: {PASS} pass · {FAIL} fail")
    return 0 if not FAIL else 1


if __name__ == "__main__":
    sys.exit(main())
