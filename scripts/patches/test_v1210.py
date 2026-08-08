"""Offline mock test for v1.210 — flag breakdown, flagged selection, repair loop.

No worker, no LLM: the pure functions are extracted and the loop's control flow
is checked at source level (the parts that spend renders must have brakes).
"""
import ast, re, sys
from pathlib import Path

SRC = Path(sys.argv[1] if len(sys.argv) > 1 else "backend/api/lora.py").read_text("utf-8")

ns = {"Any": object, "List": list, "Dict": dict, "Optional": object}
chunks = []
for node in ast.parse(SRC).body:
    if isinstance(node, ast.FunctionDef) and node.name in {"_flag_summary", "_flagged_ids"}:
        node.decorator_list = []
        chunks.append(ast.unparse(node))
    if isinstance(node, ast.Assign) and any(
            getattr(t, "id", "") in {"MAX_ATTEMPTS", "MAX_ROUNDS"} for t in node.targets):
        chunks.append(ast.unparse(node))
exec("from __future__ import annotations\n\n" + "\n\n".join(chunks), ns)
flag_summary = ns["_flag_summary"]
flagged_ids = ns["_flagged_ids"]
MAX_ATTEMPTS, MAX_ROUNDS = ns["MAX_ATTEMPTS"], ns["MAX_ROUNDS"]

fails = []


def check(label, cond, extra=""):
    print(("  PASS  " if cond else "  FAIL  ") + label + ("" if cond else f"  <- {extra}"))
    if not cond:
        fails.append(label)


def item(i, ok=None, attempts=0, **flags):
    it = {"id": f"{i:04d}", "attempts": attempts}
    if ok is not None:
        it["qc"] = {"ok": ok, "framing_ok": True, "angle_ok": True, "expression_ok": True,
                    "one_person": True, "face_clear": True, "artifacts": False,
                    "cropped_badly": False, "issues": [], **flags}
    return it


# his real number: 40 planned, 15 flagged
DS = {"items": (
    [item(i, ok=True) for i in range(1, 26)]
    + [item(i, ok=False, artifacts=True, issues=["deformed left hand"]) for i in range(26, 33)]
    + [item(i, ok=False, cropped_badly=True, issues=["feet cut off"]) for i in range(33, 37)]
    + [item(i, ok=False, framing_ok=False, issues=["shows the whole body, not a face"])
       for i in range(37, 41)]
)}

s = flag_summary(DS)
check("summary: counts the flags", s["flagged"] == 15, s["flagged"])
check("summary: counts what was checked", s["checked"] == 40, s["checked"])
check("summary: splits by cause", (s["artifacts"], s["cropped_badly"], s["framing_off"]) == (7, 4, 4), s)
check("summary: ranks the issue phrases",
      list(s["top_issues"])[0] == "deformed left hand", s["top_issues"])
check("summary: nothing stuck yet", s["stuck"] == 0)
check("summary: an unchecked set reports zeros",
      flag_summary({"items": [item(1), item(2)]})["checked"] == 0)

ids = flagged_ids(DS)
check("select: every flagged image is picked up", len(ids) == 15, len(ids))
check("select: clean images are left alone", "0001" not in ids)

# an image that already burned its attempts is parked, not re-rolled forever
DS2 = {"items": [item(1, ok=False, attempts=MAX_ATTEMPTS, artifacts=True),
                 item(2, ok=False, attempts=1, artifacts=True),
                 item(3, ok=True, attempts=1)]}
check("select: an image at the attempt limit is skipped", flagged_ids(DS2) == ["0002"],
      flagged_ids(DS2))
check("select: …unless retry-stuck is asked for",
      flagged_ids(DS2, include_stuck=True) == ["0001", "0002"])
check("summary: stuck images are counted separately", flag_summary(DS2)["stuck"] == 1)

# ── the loop's brakes (source level — these guard his GPU time) ──────────
rep_src = SRC[SRC.index("async def dataset_repair"):]
check("repair: round count is clamped to MAX_ROUNDS",
      "min(int(body.rounds or 1), MAX_ROUNDS)" in rep_src)
check("repair: MAX_ROUNDS is a sane ceiling", 2 <= MAX_ROUNDS <= 8, MAX_ROUNDS)
check("repair: attempts are capped per image", MAX_ATTEMPTS == 3, MAX_ATTEMPTS)
check("repair: exits early once nothing is flagged", "if not still:" in rep_src)
check("repair: re-seeds every round (else the re-render repeats the image)",
      "random.randint(1, 2_000_000_000)" in rep_src)
check("repair: refuses to start while another run is going",
      'a run is already going for this dataset' in rep_src)
check("repair: needs the vision model when it will re-check",
      "the repair loop " in rep_src and "qc_after=false" in rep_src)
check("repair: can run render-only", "if not body.qc_after:" in rep_src)
check("repair: records a per-round history", '"history"' in rep_src and '"flagged": len(still)' in rep_src)
check("repair: reports the final breakdown", 'st["summary"] = final' in rep_src)
check("repair: logs each round (so 'did it run' is one grep)",
      'logger.info("lora repair[%s] round' in rep_src)

# attempts must be incremented where renders land, or the cap never trips
check("render: every landed image increments its attempt counter",
      'it["attempts"] = int(it.get("attempts") or 0) + 1' in SRC)

# generate/qc/repair must share the same code paths
check("generate reuses the shared render helper", SRC.count("_render_blocking(") == 3)
check("qc reuses the shared QC helper", SRC.count("_qc_blocking(") == 3)
check("jobs are built in one place", SRC.count("_render_jobs(") == 3)
check("the payload exposes the breakdown", '"flags": _flag_summary(ds)' in SRC)
check("the payload exposes the attempt cap", '"max_attempts": MAX_ATTEMPTS' in SRC)

# ── v1.212: the identity check (the gap both reference tools already solved) ──
check("QC verdict includes identity", 'flags.get("same_person", True)' in SRC)
# v1.224: identity moved wholly to ArcFace and the reference image was removed
# from the vision call — it was failing close-ups for not being full-body.
check("identity failure is surfaced via the ArcFace likeness tag",
      '"likeness {v} ({a:.2f})"' in SRC)
check("the reference is NO LONGER passed to the vision model",
      "reference FIRST" not in SRC and "ref_png = None" in SRC)
check("QC sends exactly one image", SRC.count("imgs = [_wiz.image_bytes_to_b64") == 1)
# def + both QC call sites (+ v1.214's export, which ships the same front base
# as sample_ref.png — so pin the QC sites by name rather than a bare count)
_qc_calls = SRC.count("_identity_ref_png(ds)") + SRC.count("_identity_ref_png(cur)")
check("the ArcFace baselines still reach both QC callers",
      "_likeness_baselines(ds)" in SRC and "_likeness_baselines(cur)[0]" in SRC)
check("breakdown counts identity misses", '"identity_off"' in SRC)
check("the prompt now FORBIDS build judgements from a single image",
      "build, weight, height or proportions" in SRC)
check("…and says the shot type is the target, not a fault",
      "ASKED FOR, not a fault" in SRC)
check("composition presets exist", "FRAMING_PRESETS" in SRC and '"face_heavy"' in SRC)
check("preset reaches the plan", 'opts.get("preset")' in SRC)
check("preset is stored on the dataset", '"preset": body.preset' in SRC)
check("rank guidance corrected to 16", "rank / alpha: 16 (8-16" in SRC)

print()
print(f"{'ALL PASS' if not fails else str(len(fails)) + ' FAILURES: ' + ', '.join(fails)}")
sys.exit(1 if fails else 0)
