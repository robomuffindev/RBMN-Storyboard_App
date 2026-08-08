"""Offline mock test for v1.213 — the Fizgig-derived additions.

Every constant checked here was read out of the Fizgig source he supplied
(docs/CLI.md, lora_trainer_gui.py's look-score writer, krea2/trainer.py's
reader, krea2_train.py's parser) — not from a blog post.
"""
import ast, json, sys
from pathlib import Path

SRC = Path(sys.argv[1] if len(sys.argv) > 1 else "backend/api/lora.py").read_text("utf-8")

import importlib.util as _ilu
_LP = SRC_P.parent.parent / "services" / "likeness.py" if (_SP := globals().get("SRC_P")) else None
_lp = Path(sys.argv[2]) if len(sys.argv) > 2 else (
    Path(sys.argv[1]).resolve().parent.parent / "services" / "likeness.py"
    if len(sys.argv) > 1 else Path("backend/services/likeness.py"))
_spec = _ilu.spec_from_file_location("likeness", _lp)
_like = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(_like)
ns = {"Any": object, "List": list, "Dict": dict, "Optional": object, "_like": _like}
WANT = {"_norm_outfits", "_outfit_counts", "_deal_outfits", "_outfit_for",
        "_outfit_short", "_outfit_text", "_build_plan", "_caption", "_spread", "_by_key", "_look_scores",
        "_fizgig_toml", "_fizgig_commands", "_target_notes", "_krea2_need", "_vram_table"}
CONST = {"_OUTFIT_VIS", "NAMED_SHARE", "IMAGES_PER_OUTFIT",
         "FRAMINGS", "ANGLES", "EXPRESSIONS", "POSES", "LIGHTING", "BACKGROUNDS",
         "_POSELESS", "_QUALITY", "_BACK_OK", "_ANGLE_MIX", "FRAMING_PRESETS", "_BACK_EVERY",
         "KREA2_PEAK_GB", "KREA2_HEADROOM_GB", "KREA2_RES_GB_PER_MP", "KREA2_BATCH_GB",
         "KREA2_RANK_GB", "KREA2_SWAP_GB"}
chunks = []
for node in ast.parse(SRC).body:
    if isinstance(node, ast.FunctionDef) and node.name in WANT:
        node.decorator_list = []
        chunks.append(ast.unparse(node))
    if isinstance(node, ast.Assign) and any(getattr(t, "id", "") in CONST for t in node.targets):
        chunks.append(ast.unparse(node))
exec("from __future__ import annotations\nimport re\n\n" + "\n\n".join(chunks), ns)

fails = []


def check(label, cond, extra=""):
    print(("  PASS  " if cond else "  FAIL  ") + label + ("" if cond else f"  <- {extra}"))
    if not cond:
        fails.append(label)


DS = {"id": "duke-v1", "name": "Duke v1", "char_name": "Duke", "trigger": "rbmnduke",
      "class_token": "man", "target": "krea2", "outfit": ""}
plan = ns["_build_plan"](40, {})
caption = ns["_caption"]

# ── 1. the trigger stays off unrecognisable shots ────────────────────────
# Fizgig: "if the subject isn't actually recognizable in a shot (back of head,
# extreme distance), consider leaving the trigger out of that caption"
back = next(p for p in plan if p["angle"] == "back")
front = next(p for p in plan if p["angle"] == "front")
bc, fc = caption(DS, back), caption(DS, front)
check("caption: a back shot carries NO trigger", "rbmnduke" not in bc, bc)
check("caption: a back shot still carries the class word", " man," in bc or bc.startswith("man"), bc)
check("caption: every other shot still carries the trigger", "rbmnduke man" in fc, fc)
check("caption: placeholder mode also drops it on back rows",
      "[trigger]" not in caption(DS, back, trigger_literal=False))
check("caption: placeholder mode keeps it elsewhere",
      "[trigger] man" in caption(DS, front, trigger_literal=False))
triggered = [p for p in plan if "rbmnduke" in caption(DS, p)]
check("caption: the trigger is on every non-back row",
      len(triggered) == len([p for p in plan if p["angle"] != "back"]),
      (len(triggered), len(plan)))

# ── 2. fizgig_look_scores.json — schema lifted from their source ─────────
picked = [({"id": str(i), "qc": {"same_person": True, "identity_score": s,
                                 "identity_method": "arcface"}}, None)
          for i, s in enumerate([0.95, 0.93, 0.91, 0.9, 0.88, 0.86, 0.85, 0.83, 0.4, 0.35], 1)]
stems = {str(i): f"duke_{i:04d}" for i in range(1, 11)}
ls = ns["_look_scores"](DS, picked, stems)
check("look: three top-level keys, exactly as their reader expects",
      set(ls) == {"baselines", "cutoff", "scores"}, set(ls))
check("look: keys are basenames WITHOUT extension",
      all("." not in k for k in ls["scores"]), list(ls["scores"])[:3])
check("look: keys use the EXPORT filenames, not internal ids",
      "duke_0001" in ls["scores"] and "1" not in ls["scores"])
vals = sorted(v for v in ls["scores"].values() if isinstance(v, (int, float)))
n = len(vals)
expected = max(vals[n // 2] - 1.5 * (vals[(3 * n) // 4] - vals[n // 4]), 0.25)
check("look: cutoff is their exact IQR fence max(median-1.5*IQR, 0.25)",
      abs(ls["cutoff"] - expected) < 1e-9, (ls["cutoff"], expected))
check("look: the low scorers fall below the cutoff (they get the LR warm-up)",
      sorted(k for k, v in ls["scores"].items() if v < ls["cutoff"]) == ["duke_0009", "duke_0010"],
      [k for k, v in ls["scores"].items() if v < ls["cutoff"]])
check("look: fewer than 4 scores -> no cutoff (their >=4 guard)",
      ns["_look_scores"](DS, picked[:3], stems)["cutoff"] is None)
# v1.218 INVERTS this: a boolean-only (vision-LLM) verdict must NOT produce a
# number. Writing 1.0/0.0 into a file whose cutoff has a 0.25 ArcFace floor is
# exactly the units bug this suite used to enshrine.
check("look: a vision-LLM verdict yields NO score (it is not on ArcFace's scale)",
      ns["_look_scores"](DS, [({"id": "1", "qc": {"same_person": False}}, None)],
                         stems)["scores"]["duke_0001"] is None)
check("look: only an arcface-method verdict is written",
      ns["_look_scores"](DS, [({"id": "1", "qc": {"identity_score": 0.55,
                                                  "identity_method": "vision-llm"}}, None)],
                         stems)["scores"]["duke_0001"] is None)
check("look: an unchecked image scores null rather than a fake number",
      ns["_look_scores"](DS, [({"id": "1", "qc": None}, None)], stems)["scores"]["duke_0001"] is None)

# ── 3. the Fizgig dataset config ────────────────────────────────────────
ft = ns["_fizgig_toml"](DS, [512, 768, 1024])
for key in ("resolution", "caption_extension", "batch_size", "num_repeats",
            "enable_bucket", "bucket_no_upscale", "image_directory", "cache_directory"):
    check(f"fizgig toml has {key}", key in ft, ft)
check("fizgig toml: no-upscale on (their recommendation)", "bucket_no_upscale = true" in ft)
check("fizgig toml: its own cache dir (a shared one mixes datasets)",
      "cache/duke-v1" in ft, ft)

# ── 4. the command sheet ────────────────────────────────────────────────
fc2 = ns["_fizgig_commands"](DS, 40)
for probe, why in (
    ("krea2_cache_latents.py", "step 1"),
    ("krea2_cache_text.py", "step 2"),
    ("krea2_train.py", "step 3"),
    ("--quantize_4bit", "NF4 is the right default on 16 GB"),
    ("--adaptive_lr", "plateau tracker"),
    ("--log_per_image_loss", "per-image watch"),
    ("--per_image_lr", "throttles stuck images"),
    ("--auto_recaption", "Qwen3-VL rewrites stuck captions"),
    ("--warmup_look_outliers", "reads the file we generate"),
    ("--trigger_word rbmnduke", "so recaptions keep the trigger"),
    ("krea2_raw_bf16", "train on RAW"),
    ("krea2_turbo_fp8_scaled", "preview on Turbo"),
    ("--save_state", "resumable"),
):
    check(f"commands: {probe} ({why})", probe in fc2)
check("commands: quote their 'quantise first' rule verbatim",
      "quantise first" in fc2 and "4.4x the time" in fc2)
check("commands: carry a per-card plan computed from their constants",
      "12 GB" in fc2 and "16 GB" in fc2 and "24 GB" in fc2)
check("commands: mention the INT8 speed/accuracy trade", "--quant_int8" in fc2 and "0.637" in fc2)
check("commands: mention LoKr factor 8 as their validated default",
      "--lokr_factor 8" in fc2 or "lokr_factor 8" in fc2)
check("commands: explain the pause file", ".pause_requested" in fc2)
check("commands: epochs scale with the set", "--max_train_epochs 40" in fc2,
      [l for l in fc2.splitlines() if "max_train_epochs" in l])
check("commands: point at the look-scores file we ship",
      "fizgig_look_scores.json" in fc2)

# ── 4b. v1.213.1: the VRAM model, straight from Fizgig's planner ────────
need = ns["_krea2_need"]
check("vram: constants match their planner exactly",
      (ns["KREA2_PEAK_GB"], ns["KREA2_HEADROOM_GB"], ns["KREA2_SWAP_GB"])
      == ({"nf4": 11.4, "int8": 16.2, "fp8": 18.7}, 1.5, 0.42),
      (ns["KREA2_PEAK_GB"], ns["KREA2_HEADROOM_GB"], ns["KREA2_SWAP_GB"]))
check("vram: NF4 at 1024/r16 needs ~11.6 GB", abs(need("nf4", 1.05, 16) - 11.6) < 0.05,
      need("nf4", 1.05, 16))
check("vram: a 12 GB card (~11 free) canNOT fit NF4",
      11.0 < need("nf4", 1.05, 16) + ns["KREA2_HEADROOM_GB"])
check("vram: a 16 GB card (~14.8 free) CAN fit NF4",
      14.8 >= need("nf4", 1.05, 16) + ns["KREA2_HEADROOM_GB"])
check("vram: resolution is nearly free (0.25 -> 1.05 MP)",
      need("nf4", 1.05, 16) - need("nf4", 0.25, 16) < 0.3)
check("vram: an extra batch image is the expensive term",
      abs((need("nf4", 1.05, 16, 2) - need("nf4", 1.05, 16)) - ns["KREA2_BATCH_GB"]) < 1e-9)
tbl = ns["_vram_table"](1.05, 16)
check("table: 12 GB is told it must swap", "12 GB" in tbl and "blocks swapped" in tbl.split("16 GB")[0])
check("table: 16 GB gets NF4 with no swap", "NF4 4-bit, no swap" in tbl.split("16 GB")[1].split("24 GB")[0])
check("table: 24 GB gets INT8", "INT8" in tbl.split("24 GB")[1])
check("sheet: says resolution is NOT the lever", "RESOLUTION IS NOT THE LEVER" in fc2)
check("sheet: warns NF4 cannot block-swap", "NF4 CANNOT block-swap" in fc2)
check("sheet: carries the measured speed table", "3.09 s/it" in fc2 and "0.70 s/it" in fc2)
check("sheet: budget is free VRAM, not the box number", "FREE VRAM" in fc2)
mus = ns.get("_musubi_commands")
check("musubi sheet no longer advises dropping resolution first",
      "Do NOT reach for resolution first" in SRC)

# ── 5. wiring ───────────────────────────────────────────────────────────
check("export ships the fizgig toml", 'z.writestr("dataset_fizgig.toml"' in SRC)
check("export ships the command sheet", 'z.writestr("train_krea2_fizgig.txt"' in SRC)
check("export ships look scores INSIDE images/ (where the trainer looks)",
      '"images/fizgig_look_scores.json"' in SRC)
check("export still ships the musubi pair", 'z.writestr("dataset_musubi.toml"' in SRC)
check("stems are captured for the look-score keys", "stems[it[\"id\"]] = stem" in SRC)
# v1.224: the vision model is no longer asked about identity at ALL — the
# reference image it needed was corrupting the framing verdict.
check("QC no longer asks the vision model for an identity score",
      "_IDENTITY_LINE if with_identity" not in SRC
      and "with_identity=bool(ref_png)" not in SRC)
check("QC sends ONE image", "imgs = [_wiz.image_bytes_to_b64(_item_path(ds_id, iid)" in SRC)
# v1.218: the LLM's number is retained under its own key for comparison, and
# is no longer what "identity_score" means — that is now an ArcFace cosine.
check("the LLM identity fields are gone entirely", "identity_score_llm" not in SRC)
check("identity_score itself now comes from ArcFace",
      'flags["identity_score"] = None if arc is None else round(arc, 4)' in SRC)
notes = ns["_target_notes"]("krea2")
check("notes recommend Fizgig first", notes.index("Fizgig") < notes.index("musubi-tuner"))
check("notes explain why we write the look file",
      "no headless" in notes and "so we write it" in notes)

print()
print(f"{'ALL PASS' if not fails else str(len(fails)) + ' FAILURES: ' + ', '.join(fails)}")
sys.exit(1 if fails else 0)
