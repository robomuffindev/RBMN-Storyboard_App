"""Offline mock test for v1.209 LoRA Dataset Gen — no worker, no LLM, no DB.

Pulls the pure planning/captioning/config functions out of lora.py and checks
them against the researched dataset rules.
"""
import ast, json, re, sys, tempfile, zipfile
from pathlib import Path

SRC = Path(sys.argv[1] if len(sys.argv) > 1 else "backend/api/lora.py").read_text("utf-8")

WANT_FN = {"_norm_outfits", "_outfit_counts", "_deal_outfits", "_outfit_for",
           "_outfit_short", "_outfit_text", "_build_plan", "_caption", "_render_prompt", "_spread", "_by_key",
           "_aitoolkit_yaml", "_kohya_toml", "_training_notes", "_target_notes",
           "_musubi_toml", "_musubi_commands"}
WANT_CONST = {"_OUTFIT_VIS", "NAMED_SHARE", "IMAGES_PER_OUTFIT",
         "FRAMINGS", "ANGLES", "EXPRESSIONS", "POSES", "LIGHTING", "BACKGROUNDS",
              "_POSELESS", "_QUALITY", "_MODEL_BLOCKS", "_BACK_OK", "_ANGLE_MIX",
              "FRAMING_PRESETS", "_BACK_EVERY"}
chunks = []
for node in ast.parse(SRC).body:
    if isinstance(node, ast.FunctionDef) and node.name in WANT_FN:
        node.decorator_list = []
        chunks.append(ast.unparse(node))
    if isinstance(node, (ast.Assign, ast.AnnAssign)):
        tgts = node.targets if isinstance(node, ast.Assign) else [node.target]
        if any(getattr(t, "id", "") in WANT_CONST for t in tgts):
            chunks.append(ast.unparse(node))
ns = {"Any": object, "List": list, "Dict": dict, "Optional": object}
exec("from __future__ import annotations\nimport re\n\n" + "\n\n".join(chunks), ns)
build_plan = ns["_build_plan"]
caption = ns["_caption"]
render_prompt = ns["_render_prompt"]
FRAMINGS, ANGLES = ns["FRAMINGS"], ns["ANGLES"]

fails = []


def check(label, cond, extra=""):
    print(("  PASS  " if cond else "  FAIL  ") + label + ("" if cond else f"  <- {extra}"))
    if not cond:
        fails.append(label)


DS = {"id": "duke-abc123", "name": "Duke v1", "char_name": "Duke", "trigger": "rbmnduke",
      "class_token": "man", "target": "krea2", "outfit": "a black t-shirt and jeans"}

# ── planning ─────────────────────────────────────────────────────────────
plan = build_plan(40, {})
check("plan: exact count", len(plan) == 40, len(plan))
check("plan: ids are unique and ordered",
      [p["id"] for p in plan] == [f"{i:04d}" for i in range(1, 41)])

by_fr = {}
for p in plan:
    by_fr[p["framing"]] = by_fr.get(p["framing"], 0) + 1
check("plan: all four framings present", set(by_fr) == {f[0] for f in FRAMINGS}, by_fr)
check("plan: framing follows the researched weights (20/20/30/30)",
      by_fr == {"face": 8, "headshot": 8, "upper": 12, "full": 12}, by_fr)

angles_used = {p["angle"] for p in plan}
check("plan: every angle is covered", angles_used == {a[0] for a in ANGLES}, angles_used)
cnt = {a[0]: sum(1 for p in plan if p["angle"] == a[0]) for a in ANGLES}
# v1.210.1: angles are WEIGHTED, not dealt evenly — a character LoRA lives on
# face-bearing data, so 'back' is ~1 in 10 and front-ish angles dominate.
check("plan: back is about a tenth of the set", 2 <= cnt["back"] <= 5, cnt)
check("plan: front is the most common angle", cnt["front"] == max(cnt.values()), cnt)
check("plan: face-bearing angles dominate",
      sum(v for k, v in cnt.items() if k != "back") >= len(plan) * 0.85, cnt)
check("plan: profiles are still represented", cnt["profile_left"] >= 2 and cnt["profile_right"] >= 2, cnt)

# ── v1.210.1: a back shot cannot show a face or an expression ───────────
backs = [p for p in plan if p["angle"] == "back"]
check("plan: only body shots may face away",
      all(p["framing"] in ("upper", "full") for p in backs),
      [p["framing"] for p in backs])
check("plan: a back row carries NO expression", all(p["expression"] is None for p in backs))
check("plan: every other row still has one",
      all(p["expression"] for p in plan if p["angle"] != "back"))

check("plan: expressions vary", len({p["expression"] for p in plan}) >= 6)
check("plan: lighting varies", len({p["lighting"] for p in plan}) >= 6)
check("plan: backgrounds vary", len({p["background"] for p in plan}) >= 6)

check("plan: face crops carry no pose",
      all(p["pose"] is None for p in plan if p["framing"] in ("face", "headshot")))
check("plan: body shots all carry a pose",
      all(p["pose"] for p in plan if p["framing"] in ("upper", "full")))
check("plan: no face or headshot row is angled to the back",
      all(p["angle"] != "back" for p in plan if p["framing"] in ("face", "headshot")))

check("plan: every image is >= 1024 on its long edge",
      all(max(p["width"], p["height"]) >= 1024 for p in plan))
check("plan: face crops are square (trainers bucket by ratio)",
      all(p["width"] == p["height"] for p in plan if p["framing"] == "face"))

check("plan: deterministic — same input, same plan",
      [p["angle"] for p in build_plan(40, {})] == [p["angle"] for p in plan])
check("plan: count is clamped low", len(build_plan(2, {})) == 8, len(build_plan(2, {})))
check("plan: count is clamped high", len(build_plan(999, {})) == 120)
for n in (16, 25, 37, 60, 100):
    check(f"plan: {n} images allocates exactly {n}", len(build_plan(n, {})) == n)

# ── v1.212: composition presets (lora-dataset-studio's face-heavy target) ──
fh = build_plan(40, {"preset": "face_heavy"})
fh_c = {k: sum(1 for p in fh if p["framing"] == k) for k in ("face", "headshot", "upper", "full")}
check("preset: face_heavy is face-dominant", fh_c["face"] >= 16, fh_c)
check("preset: face_heavy still covers the body",
      fh_c["upper"] >= 4 and fh_c["full"] >= 4, fh_c)
check("preset: face_heavy totals exactly", sum(fh_c.values()) == 40, fh_c)
check("preset: face_heavy shows fewer backs than balanced",
      sum(1 for p in fh if p["angle"] == "back") <= sum(1 for p in plan if p["angle"] == "back"),
      (sum(1 for p in fh if p["angle"] == "back"), sum(1 for p in plan if p["angle"] == "back")))
check("preset: an unknown preset falls back to balanced",
      [p["framing"] for p in build_plan(40, {"preset": "nonsense"})] == [p["framing"] for p in plan])
check("preset: balanced is still the default",
      [p["framing"] for p in build_plan(40, {})] == [p["framing"] for p in plan])

subset = build_plan(20, {"angles": ["front", "profile_left"], "expressions": ["neutral"]})
check("plan: angle subset honoured", {p["angle"] for p in subset} <= {"front", "profile_left"},
      {p["angle"] for p in subset})
check("plan: expression subset honoured", {p["expression"] for p in subset} == {"neutral"})
check("plan: empty subset falls back to the full vocabulary",
      len({p["angle"] for p in build_plan(30, {"angles": []})}) == len(ANGLES))

# ── captions: the CORE rule — caption what VARIES, never the identity ─────
full_item = next(p for p in plan if p["framing"] == "full")
cap = caption(DS, full_item)
check("caption: starts with trigger + class", cap.startswith("a full body shot, head to feet of rbmnduke man"), cap)
check("caption: names the angle", any(a[1] in cap for a in ANGLES), cap)
check("caption: names the expression", "expression" in cap or "smil" in cap or "laughing" in cap, cap)
check("caption: names the pose", any(w in cap for w in ("standing", "arms", "hands", "walking",
                                                        "sitting", "leaning", "gesturing")), cap)
check("caption: names the outfit", "black t-shirt and jeans" in cap, cap)
check("caption: names the background", "in front of" in cap, cap)
check("caption: names the lighting", any(w in cap for w in ("lighting", "light", "daylight")), cap)
check("caption: ends cleanly", cap.endswith("."), cap)

IDENTITY_WORDS = ("face is", "his eyes are", "hair colour", "hair color", "beard", "heavy",
                  "overweight", "tall", "short man", "build", "proportions", "weight")
check("caption: says NOTHING about his identity/build (it must be absorbed by the trigger)",
      not any(w in cap.lower() for w in IDENTITY_WORDS), cap)

face_cap = caption(DS, next(p for p in plan if p["framing"] == "face"))
check("caption: a face crop never claims a body pose",
      not any(w in face_cap for w in ("standing", "walking", "sitting", "leaning")), face_cap)

back_item = backs[0]
bcap = caption(DS, back_item)
check("caption: a back shot claims no expression", "expression" not in bcap and "smil" not in bcap, bcap)
check("caption: a back shot still says it is seen from behind", "seen from behind" in bcap, bcap)
brp = render_prompt(DS, back_item)
check("render: a back shot never asks for his face",
      "His face" not in brp, brp[:200])
check("render: a back shot names hair/build/clothing instead",
      "hairstyle" in brp and "clothing" in brp)
check("render: a back shot states the camera sees his back",
      "the back of his head" in brp, brp[:200])
check("render: a back shot asks for no expression",
      not any(w in brp for w in ("with a neutral expression", "smiling", "laughing")), brp[:200])

ph = caption(DS, full_item, trigger_literal=False)
check("caption: placeholder mode emits ai-toolkit's [trigger]", "[trigger] man" in ph, ph)
check("caption: placeholder mode drops the literal token", "rbmnduke" not in ph, ph)

with_extra = caption(DS, {**full_item, "caption_extra": "a red scarf, snow on the ground"})
check("caption: vision-observed extras are appended", "red scarf" in with_extra, with_extra)

no_outfit = caption({**DS, "outfit": ""}, full_item)
check("caption: no outfit clause when the set has no fixed outfit",
      "wearing" not in no_outfit, no_outfit)

caps = [caption(DS, p) for p in plan]
check("caption: every image gets one", all(c.strip() for c in caps))
check("caption: wording is consistent across the set (same joiner everywhere)",
      all(c.count(",") >= 4 for c in caps))
check("caption: captions differ from each other (variety is the point)",
      len(set(caps)) >= len(caps) - 2, len(set(caps)))

# ── render prompts ──────────────────────────────────────────────────────
rp = render_prompt(DS, full_item)
check("render: pins identity to image 1", "exactly the ones in image 1" in rp, rp)
check("render: states the framing", "full body photograph" in rp, rp)
check("render: states lighting and background", "Lighting:" in rp and "Background:" in rp)
check("render: carries the outfit", "black t-shirt and jeans" in rp)
check("render: asks for photoreal quality", "photorealistic photograph" in rp)
NEG = re.compile(r"\b(not|never|avoid|ignore|without|n't)\b", re.I)
for p in plan[:12]:
    r = render_prompt(DS, p)
    hit = NEG.search(r)
    check(f"render[{p['id']}] is negation-free (cfg=1, no negative node)", hit is None,
          hit.group(0) if hit else "")

face_rp = render_prompt(DS, next(p for p in plan if p["framing"] == "face"))
check("render: face shot asks for a face crop", "face filling the frame" in face_rp, face_rp)

# ── trainer configs ─────────────────────────────────────────────────────
y = ns["_aitoolkit_yaml"](DS, 40, [512, 768, 1024])
check("yaml: ai-toolkit job shape", "job: extension" in y and "type: 'sd_trainer'" in y)
check("yaml: trigger_word wired", 'trigger_word: "rbmnduke"' in y)
check("yaml: dataset folder + caption ext", 'folder_path: "./images"' in y and 'caption_ext: "txt"' in y)
check("yaml: resolution buckets", "resolution: [512, 768, 1024]" in y)
check("yaml: caption dropout set", "caption_dropout_rate: 0.05" in y)
check("yaml: steps scale with the set", "steps: 1600" in y, [l for l in y.splitlines() if "steps:" in l])
check("yaml: sample prompts use the trigger", y.count("rbmnduke man") >= 2)

t = ns["_kohya_toml"](DS, [512, 768, 1024])
check("toml: kohya dataset block", "[[datasets]]" in t and "[[datasets.subsets]]" in t)
check("toml: caption extension", "caption_extension = '.txt'" in t)
check("toml: bucketing on", "enable_bucket = true" in t)
check("toml: class tokens carry the trigger", "rbmnduke man" in t)
check("toml: resolution is the largest bucket", "resolution = 1024" in t)

t_toml_krea = ns["_kohya_toml"](DS, [1024])
# ── v1.209.1: Krea 2 is its OWN DiT, not Flux (verified against his workflows) ──
check("krea2 config carries NO is_flux flag", "is_flux: true" not in y, y)
check("krea2 config names the target in its header", "target: krea2" in y, y.splitlines()[0])
check("krea2 model block refuses to guess the arch key",
      "deliberately left blank rather than guessed" in y)
yf = ns["_aitoolkit_yaml"]({**DS, "target": "flux"}, 40, [1024])
check("flux target still gets is_flux", "is_flux: true" in yf)
ysdxl = ns["_aitoolkit_yaml"]({**DS, "target": "sdxl"}, 40, [1024])
check("sdxl target gets is_xl", "is_xl: true" in ysdxl and "is_flux" not in ysdxl)
kn = ns["_target_notes"]("krea2")
for probe in ("not Flux", "Raw", "Turbo", "krea2_turbo_training_adapter", "22 GB", "ai-toolkit"):
    check(f"krea2 notes mention {probe!r}", probe in kn, kn[:120])
check("krea2 notes warn FluxTrainer cannot train it", "cannot train" in kn)
check("kohya toml is scoped to flux/sdxl", "FLUX and SDXL only" in t_toml_krea)
check("no target notes for a plain target", ns["_target_notes"]("other") == "")

# ── v1.211: the musubi route (the one that fits 12-16 GB) ──────────────
mt = ns["_musubi_toml"](DS, [512, 768, 1024])
check("musubi toml uses ITS format, not kohya's",
      "image_directory" in mt and "cache_directory" in mt and "image_dir =" not in mt, mt)
check("musubi toml buckets (the set mixes aspect ratios)", "enable_bucket = true" in mt)
check("musubi toml resolution is the largest bucket", "[1024, 1024]" in mt, mt)
check("musubi toml caption extension", 'caption_extension = ".txt"' in mt)

mc = ns["_musubi_commands"](DS, 40, [512, 768, 1024])
check("commands: caches latents first", "krea2_cache_latents.py" in mc)
check("commands: caches text-encoder outputs (what makes it fit)",
      "krea2_cache_text_encoder_outputs.py" in mc)
check("commands: trains with the krea2 network module", "networks.lora_krea2" in mc)
check("commands: SCALED fp8 (plain fp8 is rejected by K2)",
      "--fp8_base" in mc and "--fp8_scaled" in mc)
check("commands: block swap set and explained", "--blocks_to_swap" in mc and "max 26 of 28" in mc)
check("commands: gradient checkpointing on", "--gradient_checkpointing" in mc)
check("commands: krea2_shift for a mixed-aspect set", "--timestep_sampling krea2_shift" in mc)
check("commands: trains on RAW, never Turbo", "Train on RAW" in mc and "raw.safetensors" in mc)
check("commands: warns about SYSTEM RAM", "SYSTEM RAM" in mc and "32–64 GB" in mc)
check("commands: steps scale with the set", "--max_train_steps 1600" in mc, 
      [l for l in mc.splitlines() if "max_train_steps" in l])
check("commands: says to pick a checkpoint by eye", "pick by eye" in mc)
check("commands: inference goes on TURBO", "TURBO at strength" in mc)

kn2 = ns["_target_notes"]("krea2")
check("notes name musubi as the trainer that fits", "musubi-tuner" in kn2)
check("notes carry the measured 12 GB numbers", "10.5 GB" in kn2 and "3060" in kn2)
check("notes say FluxTrainer cannot do it", "cannot train Krea 2" in kn2)

notes = ns["_training_notes"](DS, 40)
check("notes: explains the captioning rule", "absorbed into the trigger" in notes)
check("notes: gives concrete settings", "rank / alpha" in notes and "learning rate" in notes)
check("notes: warns to train against the same base", "same base checkpoint" in notes.lower()
      or "SAME base" in notes)

print()
print(f"{'ALL PASS' if not fails else str(len(fails)) + ' FAILURES: ' + ', '.join(fails)}")
sys.exit(1 if fails else 0)
