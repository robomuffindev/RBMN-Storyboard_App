"""v1.235 test — the three-quarter wordings, and the job builder that uses them.

v1.219 is why this goes through `_render_jobs` and the route rather than through
`_render_prompt` alone: that bug was a planner input wired into one caller and
silently missed in the other, and calling the inner function directly is exactly
what hid it.

    venv\\Scripts\\python.exe scripts\\patches\\test_v1235.py
"""
from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

FAILED: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"  {'PASS' if cond else 'FAIL'}  {name}{('  — ' + detail) if detail else ''}")
    if not cond:
        FAILED.append(name)


def main() -> int:
    from backend.api import lora

    ANG_L = lora._by_key(lora.ANGLES, "three_quarter_left")      # noqa: SLF001
    ANG_R = lora._by_key(lora.ANGLES, "three_quarter_right")     # noqa: SLF001
    ANG_F = lora._by_key(lora.ANGLES, "front")                   # noqa: SLF001
    ANG_P = lora._by_key(lora.ANGLES, "profile_left")            # noqa: SLF001
    IT_L = {"angle": "three_quarter_left"}
    IT_R = {"angle": "three_quarter_right"}

    def ds_with(w):
        return {"options": {"tq_wording": w}} if w else {"options": {}}

    print("\n== the default ==")
    # v1.236 moved this from "degrees" to "halfway" on 64 measured renders.
    # The test tracks TQ_DEFAULT rather than hard-coding a wording, so the
    # default can move again without this becoming a lie.
    base = lora._angle_text({}, IT_L, ANG_L)                     # noqa: SLF001
    check("no option set -> whatever TQ_DEFAULT resolves to",
          base == lora.TQ_WORDINGS[lora._tq_mode({}, "three_quarter_left")].format(  # noqa: SLF001
              edge="left", his_side="left", n=2), base)
    check("an unknown wording falls back to the default",
          lora._angle_text(ds_with("nonsense"), IT_L, ANG_L) == base)   # noqa: SLF001
    check("v1.237: the default is auto",
          lora.TQ_DEFAULT == "auto", lora.TQ_DEFAULT)
    check("the target window is tighter than the pass/fail band",
          lora.TQ_TARGET == (25.0, 45.0)
          and lora.TQ_TARGET[0] > 20.0 and lora.TQ_TARGET[1] < 55.0,
          str(lora.TQ_TARGET))
    check("an explicit wording still beats the default",
          "45 degrees" in lora._angle_text(ds_with("degrees"), IT_L, ANG_L))  # noqa: SLF001

    print("\n== v1.237: auto resolves per DIRECTION ==")
    # Measured: halfway went 16/16 on right rows over two runs and 9/16 on left;
    # frame lands 7/8 on left and overshoots to 45-53 on right. Neither is
    # better — each is better on one side.
    check("auto uses frame on the LEFT",
          lora._tq_mode({}, "three_quarter_left") == "frame")            # noqa: SLF001
    check("auto uses halfway on the RIGHT",
          lora._tq_mode({}, "three_quarter_right") == "halfway")         # noqa: SLF001
    check("auto sends the two directions down DIFFERENT wordings",
          lora._tq_mode({}, "three_quarter_left")                        # noqa: SLF001
          != lora._tq_mode({}, "three_quarter_right"))                   # noqa: SLF001
    check("an explicit wording overrides auto on BOTH sides",
          lora._tq_mode(ds_with("halfway"), "three_quarter_left") == "halfway"  # noqa: SLF001
          and lora._tq_mode(ds_with("halfway"), "three_quarter_right") == "halfway")  # noqa: SLF001
    autoL = lora._angle_text({}, IT_L, ANG_L)                            # noqa: SLF001
    autoR = lora._angle_text({}, IT_R, ANG_R)                            # noqa: SLF001
    check("auto LEFT reads as the frame sentence",
          "his nose pointing toward the left edge" in autoL, autoL)
    check("auto RIGHT reads as the halfway sentence",
          "one of his ears is hidden" in autoR and "right edge" in autoR, autoR)
    check("auto never leaks 45 degrees into either side",
          "45 degrees" not in autoL and "45 degrees" not in autoR)

    print("\n== each wording is distinct, and points at an EDGE ==")
    texts = {w: lora._angle_text(ds_with(w), IT_L, ANG_L, 2)      # noqa: SLF001
             for w in lora.TQ_WORDINGS}
    for w, t in texts.items():
        print(f"    {w:<9} {t}")
    check("four wordings", len(lora.TQ_WORDINGS) == 4, str(sorted(lora.TQ_WORDINGS)))
    check("all four read differently", len(set(texts.values())) == 4)
    for w in ("frame", "halfway"):
        check(f"{w} names the LEFT edge of the picture on a left row",
              "left edge of the picture" in texts[w], texts[w])
        check(f"{w} does not say 'degrees'", "degree" not in texts[w])
    check("only the control still says 45 degrees",
          "45 degrees" in texts["degrees"]
          and not any("45 degrees" in texts[w] for w in ("frame", "halfway", "tworef")))
    check("the control is the only one that pins the head to the camera",
          "head toward the camera" in texts["degrees"]
          and not any("head toward the camera" in texts[w]
                      for w in ("frame", "halfway", "tworef")))

    right = {w: lora._angle_text(ds_with(w), IT_R, ANG_R, 2)      # noqa: SLF001
             for w in lora.TQ_WORDINGS}
    for w in ("frame", "halfway"):
        check(f"{w} flips to the RIGHT edge on a right row",
              "right edge of the picture" in right[w] and "left edge" not in right[w],
              right[w])

    print("\n== everything that already works is left alone ==")
    for w in lora.TQ_WORDINGS:
        d = ds_with(w)
        check(f"{w}: front untouched",
              lora._angle_text(d, {"angle": "front"}, ANG_F) == ANG_F[2])    # noqa: SLF001
        check(f"{w}: profile untouched",
              lora._angle_text(d, {"angle": "profile_left"}, ANG_P) == ANG_P[2])  # noqa: SLF001

    print("\n== tworef degrades honestly ==")
    t = lora._angle_text(ds_with("tworef"), IT_L, ANG_L, None)    # noqa: SLF001
    check("no second reference -> falls back to halfway, not to a dangling 'image 2'",
          t == texts["halfway"], t)
    check("with a second reference it cites that image",
          "image 2" in texts["tworef"], texts["tworef"])

    print("\n== _render_jobs: the wiring, not just the function ==")
    tmp = Path(tempfile.mkdtemp(prefix="v1235_"))
    (tmp / "refs").mkdir(parents=True)
    front_png, side_png = tmp / "front.png", tmp / "side.png"
    front_png.write_bytes(b"f")
    side_png.write_bytes(b"s")

    calls: list[str] = []

    def fake_base_for_view(slug, char, view, mode):
        calls.append(view)
        return (front_png if view == "front" else side_png), f"{view} reference"

    real_bfv, real_rbt, real_cdir = lora._base_for_view, lora._refs_by_tag, lora._cdir
    lora._base_for_view = fake_base_for_view                      # noqa: SLF001
    lora._refs_by_tag = lambda char, tag: []                      # noqa: SLF001
    lora._cdir = lambda slug: tmp                                 # noqa: SLF001
    try:
        items = [{"id": "a1", "angle": "three_quarter_left", "framing": "full",
                  "expression": "neutral", "lighting": "studio_soft",
                  "background": "plain_grey", "width": 768, "height": 1344},
                 {"id": "a2", "angle": "front", "framing": "full",
                  "expression": "neutral", "lighting": "studio_soft",
                  "background": "plain_grey", "width": 768, "height": 1344}]

        def jobs_for(w, tq_base):
            ds = {"id": "d", "char_slug": "c", "items": items,
                  "options": {"tq_wording": w, "tq_base": tq_base}}
            return {j["key"]: j for j in lora._render_jobs(ds, {}, items, 100)}  # noqa: SLF001

        j = jobs_for("halfway", "front")
        check("halfway: one reference, no side image",
              len(j["a1"]["refs"]) == 1, str(j["a1"]["refs"]))
        check("halfway: the prompt carries the halfway text",
              "one of his ears is hidden" in j["a1"]["prompt"], j["a1"]["prompt"][:110])

        j = jobs_for("tworef", "front")
        check("tworef: the side base is added as image 2",
              len(j["a1"]["refs"]) == 2 and j["a1"]["refs"][1] == str(side_png),
              str(j["a1"]["refs"]))
        check("tworef: the prompt points at image 2",
              "image 2" in j["a1"]["prompt"], j["a1"]["prompt"][:130])
        check("tworef: a FRONT row gets no side reference",
              len(j["a2"]["refs"]) == 1, str(j["a2"]["refs"]))

        # With tq_base=side the row already starts from the side view, so there
        # is no front/side pair to sit halfway between.
        j = jobs_for("tworef", "side")
        check("tworef + tq_base=side: no second reference, and no 'image 2' left dangling",
              len(j["a1"]["refs"]) == 1 and "image 2" not in j["a1"]["prompt"],
              str(j["a1"]["refs"]))

        j = jobs_for("degrees", "front")
        check("the control still renders the old sentence",
              "45 degrees" in j["a1"]["prompt"] and "head toward the camera" in j["a1"]["prompt"])

        print("\n== the job carries the wording, so the output can be scored ==")
        check("three-quarter job is stamped", jobs_for("halfway", "front")["a1"]["tq_wording"]
              == "halfway")
        # a1 is a LEFT row, so under auto it must record "frame", not "auto" —
        # an image stamped with the option rather than the sentence cannot be
        # scored later.
        check("an auto job records the RESOLVED wording, not the word 'auto'",
              jobs_for("auto", "front")["a1"]["tq_wording"] == "frame",
              str(jobs_for("auto", "front")["a1"]["tq_wording"]))
        check("a front job is not stamped",
              jobs_for("halfway", "front")["a2"]["tq_wording"] is None)
    finally:
        lora._base_for_view, lora._refs_by_tag, lora._cdir = real_bfv, real_rbt, real_cdir

    print("\n== the route ==")
    root = Path(tempfile.mkdtemp(prefix="v1235r_"))
    lora._DS_ROOT = root                                          # noqa: SLF001
    (root / "d1").mkdir()
    (root / "d1" / "dataset.json").write_text(json.dumps(
        {"id": "d1", "options": {}, "items": [
            {"id": "x1", "angle": "three_quarter_left"},
            {"id": "x2", "angle": "three_quarter_right"},
            {"id": "x3", "angle": "front"}]}), "utf-8")

    auto = asyncio.run(lora.dataset_tq_wording("d1", lora.TqWordingIn(wording="auto")))
    check("the route accepts auto", auto["wording"] == "auto")
    check("auto reports what each direction resolves to",
          auto["resolves_to"]["three_quarter_left"] == "frame"
          and auto["resolves_to"]["three_quarter_right"] == "halfway",
          json.dumps(auto["resolves_to"]))
    check("auto shows BOTH sentences, not just one",
          auto["reads"] != auto["reads_right"], auto["reads_right"])

    res = asyncio.run(lora.dataset_tq_wording("d1", lora.TqWordingIn(wording="halfway")))
    check("route reports how many rows it affects", res["affects"] == 2, str(res["affects"]))
    check("route shows the sentence it will use",
          "one of his ears is hidden" in res["reads"], res["reads"])
    saved = json.loads((root / "d1" / "dataset.json").read_text("utf-8"))
    check("the option is on disk", saved["options"]["tq_wording"] == "halfway")
    check("the shot list is untouched — no re-plan needed",
          [i["id"] for i in saved["items"]] == ["x1", "x2", "x3"])

    try:
        asyncio.run(lora.dataset_tq_wording("d1", lora.TqWordingIn(wording="sideways")))
        check("an unknown wording is refused", False, "no error raised")
    except Exception as e:  # noqa: BLE001
        check("an unknown wording is refused with 422",
              getattr(e, "status_code", None) == 422, str(e))
    saved = json.loads((root / "d1" / "dataset.json").read_text("utf-8"))
    check("a refused wording did not overwrite the good one",
          saved["options"]["tq_wording"] == "halfway")

    print("\n" + ("ALL PASS" if not FAILED else f"{len(FAILED)} FAILED: {FAILED}"))
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
