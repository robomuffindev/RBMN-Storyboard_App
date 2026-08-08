"""v1.214 — make the export SELF-RUNNING against Fizgig.

Before this the zip carried a .txt sheet he had to retype by hand, three model
paths that were guesses, and a `--sample_prompts sample_prompts.txt` flag
pointing at a file the zip never contained (Fizgig's parser does
`if args.sample_prompts and os.path.exists(...)`, so it did not error — it just
silently ran with NO previews, which is also the plateau/best-checkpoint
signal).  This adds:

  * fizgig_run.py  — a cross-platform runner that resolves the model paths from
    Fizgig's OWN prefs.json (fetch_models writes absolute paths there under
    krea2_raw_dit / krea2_text_encoder / krea2_vae / krea2_turbo_dit), checks
    the dataset, then runs cache_latents -> cache_text -> train as subprocesses.
    Exactly what lora_trainer_gui.py does; docs/CLI.md: "the GUI is a front-end
    that builds these exact commands and runs them as subprocesses."
  * train_krea2_fizgig.bat / .sh — double-clickable wrappers.
  * sample_prompts.txt — plain prompts (Krea 2 takes no kohya overrides).
  * sample_ref.png — the character's front base, for --sample_ref_image
    (Qwen3-VL vision path), so previews are judged against the real reference.
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


# ── 1. the runner + wrappers + sample prompts ───────────────────────────────
NEW = (Path(__file__).resolve().parent / "v1214_block.py").read_text("utf-8")

ANCHOR = '\n\n@router.post("/datasets/{ds_id}/export")'
assert src.count(ANCHOR) == 1
src = src.replace(ANCHOR, NEW + ANCHOR.lstrip("\n"), 1)

# ── 2. ship them ────────────────────────────────────────────────────────────
rep('''            z.writestr("dataset_fizgig.toml", _fizgig_toml(ds, resolution))
            z.writestr("train_krea2_fizgig.txt", _fizgig_commands(ds, len(picked)))
            look = _look_scores(ds, picked, stems)
            z.writestr("images/fizgig_look_scores.json", json.dumps(look, indent=2))''',
    '''            z.writestr("dataset_fizgig.toml", _fizgig_toml(ds, resolution))
            z.writestr("train_krea2_fizgig.txt", _fizgig_commands(ds, len(picked)))
            look = _look_scores(ds, picked, stems)
            z.writestr("images/fizgig_look_scores.json", json.dumps(look, indent=2))
            # v1.214: the zip RUNS itself.  fizgig_run.py resolves the model
            # paths out of Fizgig's prefs.json and drives the same three
            # subprocesses its GUI does — nothing to retype.
            z.writestr("fizgig_run.py", _fizgig_runner(ds, len(picked)))
            z.writestr("train_krea2_fizgig.bat", _fizgig_bat(ds).replace("\\n", "\\r\\n"))
            z.writestr("train_krea2_fizgig.sh", _fizgig_sh(ds))
            # --sample_prompts pointed at a file we never shipped.  Fizgig
            # guards with os.path.exists, so it did not fail — it silently ran
            # with no previews, and the previews ARE the plateau signal.
            z.writestr("sample_prompts.txt", _sample_prompts(ds))
            ref_png = _identity_ref_png(ds)
            if ref_png:
                z.writestr("sample_ref.png", ref_png)''',
    "export: ship the runner, wrappers, sample prompts and sample ref")

# ── 3. the sheet stops lying about model paths ──────────────────────────────
rep('''# Models (Comfy-Org/Krea-2):
#   --dit           krea2_raw_bf16.safetensors        (RAW — the one you train)
#   --turbo_dit     krea2_turbo_fp8_scaled.safetensors (8-step previews)
#   --vae           qwen_image_vae.safetensors
#   --text_encoder  qwen3vl_4b_bf16.safetensors        (also the recaption vision model)''',
    '''# ⚠ YOU DO NOT NEED TO RUN THESE BY HAND.  This zip ships fizgig_run.py plus
#   train_krea2_fizgig.bat / .sh, which run exactly the commands below with the
#   model paths read out of your Fizgig folder's prefs.json:
#
#       train_krea2_fizgig.bat            (edit FIZGIG= at the top once)
#       python fizgig_run.py --fizgig /path/to/Fizgig --dry-run
#
#   The sheet below is what it runs, for when you want to change something.
#
# Models (Comfy-Org/Krea-2).  fetch_models.py FLATTENS these into
# <fizgig>/models/ and records the absolute path in prefs.json, so prefer the
# prefs values (fizgig_run.py already does) over typing a path:
#   --dit           krea2_raw_bf16.safetensors          prefs: krea2_raw_dit
#   --turbo_dit     krea2_turbo_fp8_scaled.safetensors  prefs: krea2_turbo_dit
#   --vae           qwen_image_vae.safetensors          prefs: krea2_vae
#   --text_encoder  qwen3vl_4b_*.safetensors            prefs: krea2_text_encoder
#     ^ docs/CLI.md names the bf16 file, but Fizgig's own downloader fetches
#       qwen3vl_4b_fp8_scaled — either works, which is why we read the pref
#       instead of guessing. Doubles as the auto-recaption vision model.''',
    "sheet: model paths come from prefs.json")

for a, b in (
    ("--dataset_config dataset_fizgig.toml --vae /models/qwen_image_vae.safetensors --skip_existing",
     "--dataset_config dataset_fizgig.toml --vae <models/qwen_image_vae.safetensors> --skip_existing"),
    ("  --text_encoder /models/qwen3vl_4b_bf16.safetensors --skip_existing",
     "  --text_encoder <models/qwen3vl_4b_*.safetensors> --skip_existing"),
    ("  --dit /models/krea2_raw_bf16.safetensors \\\n  --vae /models/qwen_image_vae.safetensors \\\n"
     "  --text_encoder /models/qwen3vl_4b_bf16.safetensors \\\n"
     "  --turbo_dit /models/krea2_turbo_fp8_scaled.safetensors \\",
     "  --dit <models/krea2_raw_bf16.safetensors> \\\n  --vae <models/qwen_image_vae.safetensors> \\\n"
     "  --text_encoder <models/qwen3vl_4b_*.safetensors> \\\n"
     "  --turbo_dit <models/krea2_turbo_fp8_scaled.safetensors> \\"),
):
    rep(a, b, "sheet: paths are placeholders, not invented absolutes")

rep('''  --sample_prompts sample_prompts.txt --sample_every_n_epochs 1 \\
  --sample_width 1024 --sample_height 1024''',
    '''  --sample_prompts sample_prompts.txt --sample_every_n_epochs 1 \\
  --sample_ref_image sample_ref.png \\
  --sample_width 1024 --sample_height 1024''',
    "sheet: previews get the real reference")

rep('''# LoKr alternative: --network_type lokr --lokr_factor 8''',
    '''# sample_prompts.txt and sample_ref.png are IN THIS ZIP.  Fizgig guards
# --sample_prompts with os.path.exists, so a missing file does not error — it
# just trains with no previews, and the previews are where the plateau banner
# and the best-checkpoint estimate come from.  Keep them next to the images.
#
# LoKr alternative: --network_type lokr --lokr_factor 8''',
    "sheet: explain the sample files")

# ── 4. version ──────────────────────────────────────────────────────────────
rep('"""LoRA Dataset Generator — 🎓 the fifth mode (v1.209.0).',
    '"""LoRA Dataset Generator — 🎓 the fifth mode (v1.214.0).',
    "module version")

assert src != orig
P.write_text(src, "utf-8")
print(f"patched {P}  ({len(orig)} -> {len(src)} bytes)")
