"""🎛 Free smoke test for per-engine prompt shaping (v1.277.19).

Pure functions, so this needs no worker, no GPU, no LLM and no running app —
it loads `backend/api/prompt_shape.py` BY PATH (see below) and asserts the RULES, each of which
comes from the engines' own source or docs:

  ACE  metadata OUT of the caption (its tokenizer injects a `# Metas` block) ·
       Title-Case structure tags with at most ONE modifier (stacked modifiers
       get SUNG) · `[Instrumental]`, never an empty lyrics box · a line budget
       derived from duration and bpm (over-long sheets make it skip verses).
  MM3  metadata IN the caption (there are no widgets) · the three-section
       layout · the vocal always named (or it drifts instrumental) · tags
       lowercased and ALONE on their line (text on a tag's line is dropped) ·
       stage directions in (parentheses), because every [bracket] becomes a tag.

    python scripts\\prompt_shape_smoke.py

⚠ Count the `check(` CALL SITES in this file, not the PASS lines in its output.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# ⚠ loaded BY PATH, not as `backend.api.prompt_shape`: importing that package
# runs `backend/api/__init__.py`, which imports FastAPI — and a test that only
# runs inside the venv is a test nobody runs (the dl_progress lesson).
import importlib.util                                             # noqa: E402
_spec = importlib.util.spec_from_file_location(
    "prompt_shape", Path(__file__).resolve().parent.parent
    / "backend" / "api" / "prompt_shape.py")
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)                                    # type: ignore
shape = _mod.shape

PASS = FAIL = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  PASS  {label}" + (f"  ({detail})" if detail else ""))
    else:
        FAIL += 1
        print(f"  FAIL  {label}" + (f"  ({detail})" if detail else ""))


TAGS = ("warm indie folk ballad, female vocal, acoustic guitar, brushed drums, "
        "soft room reverb, 90 bpm, in G major")
LYR = ("[verse - tender - close - breathy]\n"
       "Dust on the window, light on the floor\n"
       "I heard your footsteps out by the door\n"
       "[chorus]\n"
       "Carry me home, carry me slow\n"
       "There's nothing left here that I need to know\n"
       "[rain on the window]\n"
       "One more line that will not fit at all\n"
       "And another one after that as well\n")


def main() -> int:
    print("🎛 prompt shaping — same brief, two engines\n")

    a = shape("ace15_sft", TAGS, LYR, seconds=20)
    print("── ACE ──")
    check("bpm moved OUT of the caption into the field",
          a["bpm"] == 90 and "bpm" not in a["tags"].lower(), a["tags"][:70])
    check("key moved OUT of the caption", a["keyscale"].lower().startswith("g"),
          a["keyscale"])
    check("the caption keeps the musical words",
          "acoustic guitar" in a["tags"] and "female vocal" in a["tags"])
    check("structure tags are Title Case",
          "[Verse" in a["lyrics"] and "[Chorus]" in a["lyrics"],
          a["lyrics"].splitlines()[0])
    check("a stacked tag is trimmed to ONE modifier",
          a["lyrics"].splitlines()[0].count("-") <= 1,
          a["lyrics"].splitlines()[0])
    check("sections are separated by a blank line", "\n\n[Chorus]" in a["lyrics"])
    check("the line budget truncates a too-long sheet for 20s @ 90bpm",
          len([x for x in a["lyrics"].splitlines()
               if x.strip() and not x.strip().startswith("[")]) <= 4,
          f"{len([x for x in a['lyrics'].splitlines() if x.strip() and not x.strip().startswith('[')])} sung lines")
    check("it says what it changed", bool(a["notes"]), "; ".join(a["notes"])[:90])

    ai = shape("ace15", TAGS, "", seconds=20, instrumental=True)
    check("instrumental ⇒ [Instrumental], not an empty box",
          ai["lyrics"].strip() == "[Instrumental]", ai["lyrics"][:40])

    m = shape("minimax3", TAGS, LYR, seconds=20)
    print("\n── MiniMax Music 3 ──")
    cap = m["tags"]
    check("caption uses the three-section layout",
          all(h in cap for h in ("Global Metadata", "Vocal Details", "Arrangement")))
    check("tempo/key are IN the caption (no widgets exist)",
          "bpm is 90" in cap and "key is G" in cap,
          cap.splitlines()[1][:70])
    check("the vocal is always named (else it drifts instrumental)",
          "Vocal" in cap and ("female" in cap.lower() or "lead vocal" in cap.lower()))
    check("a ≤30s cue is described as ONE section, not a six-part song",
          "no section changes" in cap)
    check("no four-space run (clean_caption deletes them, gluing words)",
          "    " not in cap)
    lyr = m["lyrics"]
    check("MM3 tags are lowercased", "[verse]" in lyr and "[chorus]" in lyr,
          lyr.splitlines()[0])
    check("a tag carries no modifier for MM3", "-" not in lyr.split("]")[0])
    check("a stage direction becomes (parentheses), not a bogus tag",
          "(rain on the window)" in lyr and "[rain on the window]" not in lyr)
    check("every tag sits ALONE on its line",
          all(not (l.strip().startswith("[") and l.strip().endswith("]") is False)
              for l in lyr.splitlines() if "[" in l))
    check("MM3 explains its rewrites too", bool(m["notes"]),
          "; ".join(m["notes"])[:90])

    mi = shape("minimax3", TAGS, "", seconds=20, instrumental=True)
    check("MM3 instrumental ⇒ [Intro] + (instrumental), never empty",
          "(instrumental)" in mi["lyrics"] and mi["lyrics"].startswith("[Intro]"),
          mi["lyrics"].replace("\n", " ⏎ "))
    check("MM3 instrumental caption says there is no vocal",
          "no vocal" in mi["tags"].lower())

    print("\n── the same brief, side by side ──")
    print("ACE caption :", a["tags"][:100])
    print("MM3 caption :", cap.splitlines()[1][:100])
    print(f"\n{'ALL PASS' if not FAIL else 'FAILURES'}: {PASS} pass · {FAIL} fail")
    return 0 if not FAIL else 1


if __name__ == "__main__":
    sys.exit(main())
