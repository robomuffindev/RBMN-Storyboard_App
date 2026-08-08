"""v1.214 — VERSION, pyproject, CHANGELOG, docs/LORA_DATASET.md."""
import sys
from pathlib import Path

ROOT = Path(sys.argv[1] if len(sys.argv) > 1 else ".")

# ── VERSION ────────────────────────────────────────────────────────────────
v = ROOT / "VERSION"
assert v.read_text("utf-8").strip() == "1.213.1", v.read_text("utf-8")
v.write_text("1.214.0\n", "utf-8")

# ── pyproject ──────────────────────────────────────────────────────────────
pp = ROOT / "pyproject.toml"
s = pp.read_text("utf-8")
assert s.count('version = "1.213.1"') == 1
pp.write_text(s.replace('version = "1.213.1"', 'version = "1.214.0"', 1), "utf-8")

# ── CHANGELOG ──────────────────────────────────────────────────────────────
ENTRY = '''## v1.214.0 -- the export now RUNS itself against Fizgig (2026-08-04)

"So can we not send the dataset from our app to Fizgig? Is it something we need to do by hand?"

Checked the source rather than guessing. Fizgig has **no HTTP API and no server** -- I grepped
for fastapi/flask/uvicorn/gradio/http.server across the whole tree and the only hit was
`lora_trainer_gui.py`, which is tkinter. So it cannot be queued through our ComfyUI dispatcher
like a workflow. But **nothing about it needs the GUI**: `src/fizgig/` imports tkinter nowhere,
and the three `krea2_*` scripts are plain argparse. Their own docs/CLI.md says it outright --
"the GUI is a front-end that builds these exact commands and runs them as subprocesses."
Headless is the first-class path, not a fallback.

So: **the zip is now self-running.** New in every krea2 export --
- **`fizgig_run.py`** -- resolves the four model paths out of *your Fizgig folder's own
  `prefs.json`* (fetch_models.py writes absolute paths there under `krea2_raw_dit` /
  `krea2_text_encoder` / `krea2_vae` / `krea2_turbo_dit`), validates the dataset, then runs
  cache_latents -> cache_text -> train as subprocesses with `PYTHONPATH=<fizgig>/src` and
  `cwd=<fizgig>`. Flags: `--dry-run`, `--skip-cache`, `--quant nf4|int8|fp8`,
  `--blocks-to-swap`, `--epochs`, and per-model overrides.
- **`train_krea2_fizgig.bat`** (CRLF, double-clickable; one `set FIZGIG=` line to edit) and
  **`train_krea2_fizgig.sh`** (`FIZGIG=/workspace/Fizgig ./train_krea2_fizgig.sh`). Both prefer
  Fizgig's venv python so torch/CUDA are the ones it was installed with.
- It **stops before spending GPU time** on: an image with no caption .txt, a `--fizgig` path
  that is not a checkout, a model it cannot find (with the fix spelled out, not a traceback),
  and **NF4 + `--blocks-to-swap`**, which the trainer force-zeroes under 4-bit.

**A real bug this turn found.** The command sheet has been passing
`--sample_prompts sample_prompts.txt` since v1.213 -- and the zip has never contained that file.
Fizgig guards it with `if args.sample_prompts and os.path.exists(...)`, so it did not error: it
just trained with **no previews at all**, which is also where the plateau banner and the
best-checkpoint estimate come from. Now shipped:
- **`sample_prompts.txt`** -- five plain prompts (Krea 2 takes no kohya `--w/--h/--d`
  overrides; geometry comes from `--sample_width/--sample_height`), deliberately the shots a
  character LoRA fails on first: close portrait, full body, 3/4, profile, hard side light.
- **`sample_ref.png`** -- the character's front base, wired to `--sample_ref_image` (the
  Qwen3-VL vision path). Previews are now driven by the same reference our QC judges against.

**Two model paths in the sheet were invented.** It said `/models/qwen3vl_4b_bf16.safetensors`
et al. Wrong twice: `fetch_models.py` FLATTENS every weight into `<fizgig>/models/` (relative to
the checkout, not `/`), and it downloads `qwen3vl_4b_fp8_scaled`, not the bf16 file docs/CLI.md
names. Both work; which one you have depends on how you got it -- which is exactly why the
runner reads the pref instead of guessing. The sheet now shows placeholders plus the prefs key.

Verified, not asserted: `test_v1214.py` GENERATES `fizgig_run.py`, builds a fake Fizgig checkout
and a fake unzipped export in a tmpdir, and **executes the runner** with `--dry-run` to read the
exact commands it would issue -- then re-runs it with a caption deleted, with `prefs.json`
deleted, with NF4+swap, with `--skip-cache`, and against a non-Fizgig folder. 62 checks, all
pass; v1209/v1210/v1213 still pass on the live file (md5 2fe5a2064a5bf04d78d777fa37753b57).

Still by hand: getting the zip onto the training box, and installing Fizgig there. Wiring that
end (a worker agent, or Fizgig on the app box driven by subprocess) is the next fork.

'''
cl = ROOT / "CHANGELOG.md"
s = cl.read_text("utf-8")
assert s.startswith("## v1.213.1"), s[:40]
cl.write_text(ENTRY + s, "utf-8")

# ── docs ───────────────────────────────────────────────────────────────────
d = ROOT / "docs" / "LORA_DATASET.md"
s = d.read_text("utf-8")
DOC = '''
## Handing the dataset to Fizgig (v1.214)

**You do not retype anything.** A krea2 export now contains its own runner.

```
duke-v1_40img_20260804.zip
  images/            *.png + *.txt captions + fizgig_look_scores.json
  dataset_fizgig.toml
  fizgig_run.py            <- the runner
  train_krea2_fizgig.bat   <- Windows: set FIZGIG= at the top, double-click
  train_krea2_fizgig.sh    <- Linux/RunPod: FIZGIG=/workspace/Fizgig ./...sh
  train_krea2_fizgig.txt   <- the same commands, for when you want to change one
  sample_prompts.txt  sample_ref.png
  dataset_musubi.toml  dataset_kohya.toml  dataset_aitoolkit.yaml
  manifest.json  README.md
```

Unzip it anywhere, keep the layout, point it at your Fizgig checkout:

```
python fizgig_run.py --fizgig C:/Fizgig --dry-run    # prints, runs nothing
python fizgig_run.py --fizgig C:/Fizgig              # cache -> cache -> train
```

Model paths come from that folder's `prefs.json` -- Fizgig's own downloader
(Preferences -> "Download models for me") writes absolute paths there under
`krea2_raw_dit`, `krea2_text_encoder`, `krea2_vae`, `krea2_turbo_dit`. Override
any of them with `--dit / --vae / --text-encoder / --turbo-dit`.

Useful flags: `--skip-cache` (latents/text already cached), `--quant nf4|int8|fp8`,
`--blocks-to-swap N` (fp8 only -- NF4 force-zeroes it), `--epochs N`, `--output-dir`.

### Why there is no "Train" button in the app yet

Fizgig has no HTTP API. Its only server-ish surface is `docker/` (a RunPod image
that serves the tkinter GUI over noVNC). Our dispatcher speaks ComfyUI, so Fizgig
cannot be queued as a workflow. Automating the last hop means one of:

| | what it costs | what it buys |
|---|---|---|
| Fizgig on the RBMN backend machine | ~32 GB weights + a venv on the app box; training occupies it | zero new moving parts -- `subprocess.run` from `lora.py` |
| a small agent on a 16 GB worker | one install script (the `install_trellis2.bat` pattern) | training on the worker that has the VRAM; live status in the app |
| shared folder + generated .bat | nothing | already works today; you press the button |

Row 3 is what v1.214 ships.
'''
assert "Handing the dataset to Fizgig" not in s
d.write_text(s.rstrip() + "\n" + DOC, "utf-8")

print("VERSION 1.214.0 · pyproject · CHANGELOG · docs/LORA_DATASET.md")
