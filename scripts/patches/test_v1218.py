"""Offline test for v1.218 — real ArcFace identity scoring.

The whole point of this version is that v1.213 assumed units instead of
measuring them. So this suite MEASURES: it runs the real buffalo_l model over
insightface's own bundled sample images and checks that the constants we shipped
actually match observed behaviour. If insightface is absent it still verifies
the degraded path, and says loudly which half it skipped.
"""
import ast
import importlib.util
import itertools
import sys
import tempfile
from pathlib import Path

LIKE_P = Path(sys.argv[1] if len(sys.argv) > 1 else "backend/services/likeness.py")
LORA = Path(sys.argv[2] if len(sys.argv) > 2 else "backend/api/lora.py").read_text("utf-8")

spec = importlib.util.spec_from_file_location("likeness", LIKE_P)
L = importlib.util.module_from_spec(spec)
spec.loader.exec_module(L)

fails = []
skipped = 0


def check(label, cond, extra=""):
    print(("  PASS  " if cond else "  FAIL  ") + label + ("" if cond else f"  <- {extra}"))
    if not cond:
        fails.append(label)


def skip(label, why):
    global skipped
    skipped += 1
    print(f"  SKIP  {label}  ({why})")


# ── 1. bands and pure logic — no model needed ───────────────────────────────
check("bands: Fizgig's thresholds, unchanged",
      (L.ARC_DIFFERENT, L.ARC_BORDERLINE, L.ARC_MATCH) == (0.25, 0.30, 0.45),
      (L.ARC_DIFFERENT, L.ARC_BORDERLINE, L.ARC_MATCH))
check("verdict: 0.60 is a match", L.verdict(0.60)[0] == "match")
check("verdict: 0.35 is borderline", L.verdict(0.35)[0] == "borderline")
check("verdict: 0.27 is weak", L.verdict(0.27)[0] == "weak")
check("verdict: 0.10 is not him", L.verdict(0.10)[0] == "not him")
check("verdict: None is 'no face', NOT a zero score", L.verdict(None)[0] == "no face")
check("verdict: the no-face blurb says back shots are fine",
      "Back shots" in L.verdict(None)[1])

# the cutoff that v1.213 got wrong
# On a TIGHT set the fence sits just under the median — by design in their tool,
# where "look outlier" means relatively unusual, not absolutely bad. It only ever
# drives the LR warm-up, never an exclusion, so an eager fence is cheap.
c = L.cutoff([0.62, 0.58, 0.55, 0.61, 0.59, 0.60])
check("cutoff: a tight set puts the fence just under the median",
      c is not None and 0.50 < c < 0.60, c)
check("cutoff: a WIDE set puts the fence well below the median",
      L.cutoff([0.70, 0.62, 0.55, 0.48, 0.40, 0.33]) < 0.40,
      L.cutoff([0.70, 0.62, 0.55, 0.48, 0.40, 0.33]))
c2 = L.cutoff([0.70, 0.68, 0.65, 0.62, 0.60, 0.15])
check("cutoff: never returns below the different-person floor", c2 >= 0.25, c2)
check("cutoff: fewer than 4 scores -> None (their >=4 guard)",
      L.cutoff([0.5, 0.6, 0.7]) is None)
check("cutoff: ignores None entries", L.cutoff([0.5, 0.6, 0.7, 0.8]) is not None)

# ── 2. distribution reporting ───────────────────────────────────────────────
d = L.distribution({"a": 0.62, "b": 0.55, "c": 0.31, "d": 0.12, "e": None})
check("dist: counts every band", d["bands"] == {"match": 2, "borderline": 1, "weak": 0,
                                                "not him": 1, "no face": 1}, d["bands"])
check("dist: no-face is counted apart from a low score", d["no_face"] == 1)
check("dist: scored excludes the no-face row", d["scored"] == 4, d["scored"])
check("dist: reports the worst images by name",
      [w["name"] for w in d["worst"]][:2] == ["d", "c"], d["worst"])
check("dist: median in range reads as sane", "expected same-person range" in d["sanity"], d)
hi = L.distribution({k: 0.95 for k in "abcdef"})
check("dist: an implausibly high median is CALLED OUT, not celebrated",
      "very high" in hi["sanity"] and "baselines" in hi["sanity"], hi["sanity"])
lo = L.distribution({k: 0.10 for k in "abcdef"})
check("dist: a low median says the wrong character may be loaded",
      "does not resemble" in lo["sanity"], lo["sanity"])
check("dist: an empty set does not crash", L.distribution({})["scored"] == 0)

# ── 3. the model itself — MEASURED, this is the part v1.213 skipped ─────────
if not L.available():
    skip("MEASURED ArcFace behaviour", f"insightface unavailable: {L.health()['error']}")
    check("degraded: health says so plainly", L.health()["available"] is False)
    check("degraded: health tells you how to fix it",
          "pip install insightface" in (L.health()["install"] or ""))
    check("degraded: embed returns None rather than raising",
          L.embed("/nonexistent.png") is None)
    check("degraded: score returns None rather than raising",
          L.score("/nonexistent.png", [1]) is None)
else:
    import numpy as np
    import insightface
    from PIL import Image, ImageEnhance
    D = Path(insightface.__file__).parent / "data" / "images"
    group = D / "t1.jpg"
    check("model: loads", L.available())
    check("model: health names the model", "buffalo_l" in (L.health()["model"] or ""))

    app = L._app()
    import cv2
    faces = sorted(app.get(cv2.imread(str(group))),
                   key=lambda f: -(f.bbox[2] - f.bbox[0]) * (f.bbox[3] - f.bbox[1]))
    check("model: finds every face in a group photo", len(faces) >= 4, len(faces))

    # DIFFERENT people — the claim the 0.25 floor rests on
    embs = [f.normed_embedding for f in faces]
    pairs = [float(np.dot(a, b)) for a, b in itertools.combinations(embs, 2)]
    over = [p for p in pairs if p >= L.ARC_DIFFERENT]
    check(f"MEASURED: different people stay under the {L.ARC_DIFFERENT} floor "
          f"({len(pairs)} pairs, max {max(pairs):+.3f})", not over, over)
    check("MEASURED: different-person scores are near zero, not merely low",
          abs(sorted(pairs)[len(pairs) // 2]) < 0.15, sorted(pairs)[len(pairs) // 2])

    # SAME face, varied capture — an upper bound, and it must clear 'match'
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        img = Image.open(group).convert("RGB")
        x1, y1, x2, y2 = [int(v) for v in faces[0].bbox]
        pad = int((x2 - x1) * 0.9)
        crop = img.crop((max(0, x1 - pad), max(0, y1 - pad),
                         min(img.width, x2 + pad), min(img.height, y2 + pad)))
        crop = crop.resize((crop.width * 3, crop.height * 3), Image.LANCZOS)
        (td / "base.png").write_bytes(b"")
        crop.save(td / "base.png")
        base = L.embed(td / "base.png")
        check("MEASURED: a cropped face embeds", base is not None)

        variants = {
            "downscale 40%": crop.resize((int(crop.width * .4), int(crop.height * .4)))
                                 .resize(crop.size),
            "brightness x1.5": ImageEnhance.Brightness(crop).enhance(1.5),
            "brightness x0.6": ImageEnhance.Brightness(crop).enhance(0.6),
            "greyscale": ImageEnhance.Color(crop).enhance(0.0),
            "mirrored": crop.transpose(Image.FLIP_LEFT_RIGHT),
        }
        got = {}
        for name, im in variants.items():
            p = td / (name.replace(" ", "_").replace("%", "") + ".png")
            im.save(p)
            got[name] = L.score(p, [base])
        worst = min(v for v in got.values() if v is not None)
        check(f"MEASURED: the same face under varied capture clears 'match' "
              f"(worst {worst:+.3f} >= {L.ARC_MATCH})", worst >= L.ARC_MATCH,
              {k: round(v, 3) for k, v in got.items() if v is not None})
        check("MEASURED: same-person beats different-person by a wide margin",
              worst - max(pairs) > 0.5, (worst, max(pairs)))

        # the centroid: averaging must not be a max or a min
        others = [f.normed_embedding for f in faces[1:3]]
        solo = L.score(td / "brightness_x1.5.png", [base])
        mixed = L.score(td / "brightness_x1.5.png", [base] + others)
        check("MEASURED: three baselines average — a wrong one drags the score DOWN",
              mixed < solo, (mixed, solo))
        check("MEASURED: …and the average is not simply the minimum",
              mixed > min([solo] + [L.score(td / "brightness_x1.5.png", [o]) for o in others]),
              mixed)

        # no face at all
        Image.new("RGB", (512, 512), (40, 60, 90)).save(td / "blank.png")
        check("MEASURED: a faceless image scores None, never 0.0",
              L.score(td / "blank.png", [base]) is None)
        check("MEASURED: …and a faceless image embeds to None",
              L.embed(td / "blank.png") is None)

        # the cache must key on content, not just path
        e1 = L.embed(td / "base.png")
        ImageEnhance.Brightness(crop).enhance(0.5).save(td / "base.png")
        e2 = L.embed(td / "base.png")
        check("cache: keyed on mtime+size, so an edited file is re-embedded",
              e1 is not None and e2 is not None and float(np.dot(e1, e2)) < 0.9999,
              float(np.dot(e1, e2)) if e2 is not None else None)

    check("score: no baselines -> None, not a crash", L.score(group, []) is None)
    check("cosine: None operand -> None", L.cosine(None, base) is None)

# ── 4. wiring in lora.py ────────────────────────────────────────────────────
check("lora: imports the scorer", "from backend.services import likeness as _like" in LORA)
check("lora: builds up to THREE baselines", "if len(embs) >= 3:" in LORA)
check("lora: baselines come from the CHARACTER, never this dataset's renders",
      "never from this" in LORA and "renders" in LORA)
check("lora: baselines honour the dataset's base_mode",
      '_base_for_view(ds["char_slug"], char, "front", ds.get("base_mode"))' in LORA)
check("lora: a reference with no face is skipped, not fatal",
      "useless as a baseline, not an error" in LORA)
check("lora: QC takes the number from ArcFace", 'flags["identity_method"] = ("arcface"' in LORA)
# v1.224 went further: the vision model is not asked about identity at all,
# because the reference image it needed was corrupting the framing verdict.
check("lora: the LLM is no longer asked about identity at all",
      "identity_score_llm" not in LORA and "reference FIRST" not in LORA)
# v1.221: still the only failing threshold, but back rows are now exempt from
# it entirely (frontal baselines cannot judge a back shot).
check("lora: only the different-person floor fails an image",
      "arc >= _like.ARC_DIFFERENT" in LORA)
check("lora: …and a back row is exempt from that failure",
      'flags["same_person"] = (True if _is_back' in LORA)
# v1.221 reworded this into a template so a back shot can carry its caveat.
check("lora: borderline is surfaced as an issue, not a failure",
      "likeness {v} ({a:.2f})" in LORA)
check("lora: the likeness issue line comes AFTER `issues` exists",
      LORA.index("issues = [str(x)[:120]") < LORA.index("_tag.format(v="))
check("lora: ONLY an arcface score reaches fizgig_look_scores.json",
      'if q.get("identity_method") != "arcface":' in LORA)
check("lora: the look file uses the shared cutoff", "_like.cutoff(" in LORA)
check("lora: the look file names the real baselines",
      'ds.get("likeness_baselines")' in LORA)
check("lora: baselines are built once per QC run, not per image",
      "Built ONCE" in LORA)
check("lora: both QC callers pass baselines",
      "st, ref_png, _embs)" in LORA and "_likeness_baselines(cur)[0])" in LORA)
check("lora: a CPU-only rescore route exists",
      '@router.post("/datasets/{ds_id}/likeness")' in LORA)
check("lora: the rescore route explains it is the measurement v1.213 skipped",
      "v1.213 should have made" in LORA)
check("lora: rescore 503s with the install command when the model is absent",
      "pip install insightface onnxruntime` on the app host" in LORA)
check("lora: rescore 409s when no reference has a usable face",
      "Back-only references cannot be scored" in LORA)
check("lora: rescore recomputes ok from the new verdict", 'q["ok"] = bool(' in LORA)
check("lora: a health route reports availability",
      '@router.get("/likeness-health")' in LORA)
check("lora: the breakdown counts arcface coverage and no-face rows",
      '"arcface_scored": 0, "no_face": 0' in LORA)
check("lora: no-face is counted, never flagged",
      "is a CORRECT outcome for a back shot" in LORA)

print()
if skipped:
    print(f"({skipped} measured block(s) skipped — install insightface to run them)")
print(f"{'ALL PASS' if not fails else str(len(fails)) + ' FAILURES: ' + ', '.join(fails)}")
sys.exit(1 if fails else 0)
