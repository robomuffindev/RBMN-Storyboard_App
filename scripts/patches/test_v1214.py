"""Offline test for v1.214 — the export that runs itself.

This does not just grep for strings: it GENERATES fizgig_run.py, builds a fake
Fizgig checkout and a fake unzipped export beside it, and executes the runner
with --dry-run to see the exact commands it would issue.  A sheet that is right
and a runner that is wrong is the failure mode this is for.
"""
import ast
import json
import subprocess
import sys
import tempfile
from pathlib import Path

SRC_P = Path(sys.argv[1] if len(sys.argv) > 1 else "backend/api/lora.py")
SRC = SRC_P.read_text("utf-8")

ns = {"Any": object, "List": list, "Dict": dict, "Optional": object}
WANT = {"_sample_prompts", "_fizgig_runner", "_fizgig_bat", "_fizgig_sh",
        "_fizgig_toml", "_fizgig_commands", "_krea2_need", "_vram_table"}
CONST = {"_FIZGIG_PREFS", "_RUNNER_SRC", "KREA2_PEAK_GB", "KREA2_HEADROOM_GB",
         "KREA2_RES_GB_PER_MP", "KREA2_BATCH_GB", "KREA2_RANK_GB", "KREA2_SWAP_GB"}
chunks = []
for node in ast.parse(SRC).body:
    if isinstance(node, ast.FunctionDef) and node.name in WANT:
        node.decorator_list = []
        chunks.append(ast.unparse(node))
    if isinstance(node, ast.Assign) and any(getattr(t, "id", "") in CONST for t in node.targets):
        chunks.append(ast.unparse(node))
exec("from __future__ import annotations\n\n" + "\n\n".join(chunks), ns)

fails = []


def check(label, cond, extra=""):
    print(("  PASS  " if cond else "  FAIL  ") + label + ("" if cond else f"  <- {extra}"))
    if not cond:
        fails.append(label)


DS = {"id": "duke-v1", "name": "Duke v1", "char_name": "Duke", "trigger": "rbmnduke",
      "class_token": "man", "target": "krea2", "outfit": ""}

# ── 1. sample prompts ───────────────────────────────────────────────────────
sp = ns["_sample_prompts"](DS)
lines = [l for l in sp.splitlines() if l.strip() and not l.startswith("#")]
check("prompts: several, one per line", len(lines) >= 4, lines)
check("prompts: every one carries trigger + class",
      all(l.startswith("rbmnduke man,") for l in lines), lines)
check("prompts: cover the shots identity breaks on first",
      all(any(k in sp for k in [w]) for w in
          ("close-up portrait", "full body", "three-quarter", "side profile")), sp)
check("prompts: no kohya overrides (Krea 2 ignores them)",
      not any("--w " in l or "--d " in l for l in lines), lines)

# ── 2. the runner is valid python and has no leftover placeholders ──────────
runner = ns["_fizgig_runner"](DS, 40)
try:
    ast.parse(runner)
    ok = True
except SyntaxError as e:
    ok, err = False, e
check("runner: generated file parses as python", ok, "" if ok else err)
check("runner: every placeholder was substituted", "@@" not in runner,
      [l for l in runner.splitlines() if "@@" in l])
check("runner: carries this dataset's id and trigger",
      "'duke-v1'" in runner and "'rbmnduke'" in runner)
check("runner: epochs match the sheet's formula (40 imgs -> 40)",
      "EPOCHS = 40" in runner, [l for l in runner.splitlines() if l.startswith("EPOCHS")])

# ── 3. actually run it, --dry-run, against a fake checkout ──────────────────
with tempfile.TemporaryDirectory() as td:
    td = Path(td)
    fiz = td / "Fizgig"
    (fiz / "src" / "fizgig" / "scripts").mkdir(parents=True)
    (fiz / "lora_trainer_gui.py").write_text("# fake")
    for s in ("krea2_cache_latents.py", "krea2_cache_text.py", "krea2_train.py"):
        (fiz / "src" / "fizgig" / "scripts" / s).write_text("# fake")
    mdl = fiz / "models"
    mdl.mkdir()
    names = {"krea2_raw_dit": "krea2_raw_bf16.safetensors",
             "krea2_vae": "qwen_image_vae.safetensors",
             "krea2_text_encoder": "qwen3vl_4b_fp8_scaled.safetensors",
             "krea2_turbo_dit": "krea2_turbo_fp8_scaled.safetensors"}
    for f in names.values():
        (mdl / f).write_bytes(b"0")
    (fiz / "prefs.json").write_text(json.dumps({k: str(mdl / v) for k, v in names.items()}))

    exp = td / "export"
    (exp / "images").mkdir(parents=True)
    (exp / "fizgig_run.py").write_text(runner, encoding="utf-8")
    (exp / "dataset_fizgig.toml").write_text(ns["_fizgig_toml"](DS, [1024]))
    (exp / "sample_prompts.txt").write_text(sp)
    (exp / "sample_ref.png").write_bytes(b"\x89PNG")
    for i in range(1, 4):
        (exp / "images" / f"duke_{i:04d}.png").write_bytes(b"\x89PNG")
        (exp / "images" / f"duke_{i:04d}.txt").write_text("rbmnduke man, portrait")

    def run(*extra, expect=0):
        r = subprocess.run([sys.executable, str(exp / "fizgig_run.py"),
                            "--fizgig", str(fiz), "--dry-run", *extra],
                           capture_output=True, text=True)
        return r

    r = run()
    out = r.stdout
    check("run: exits 0 on a well-formed export", r.returncode == 0, r.stderr[-400:])
    check("run: found the dataset", "3 images, all captioned" in out, out)
    check("run: resolved every model from prefs.json",
          all(n in out for n in names.values()), out)
    check("run: three steps in the right order",
          out.index("cache latents") < out.index("cache text") < out.index("=== train ==="), out)
    for probe in ("krea2_cache_latents.py", "krea2_cache_text.py", "krea2_train.py",
                  "--quantize_4bit", "--adaptive_lr", "--log_per_image_loss", "--per_image_lr",
                  "--auto_recaption", "--warmup_look_outliers", "--trigger_word rbmnduke",
                  "--save_state", "--max_train_epochs 40", "--network_dim 16"):
        check(f"run: command carries {probe}", probe in out)
    check("run: sample prompts are wired because the file exists",
          "--sample_prompts" in out and "sample_prompts.txt" in out, out)
    check("run: the reference image is wired for the vision preview path",
          "--sample_ref_image" in out and "sample_ref.png" in out, out)
    check("run: dataset_config points at THIS folder, not a bare name",
          str(exp / "dataset_fizgig.toml") in out, out)
    check("run: --dry-run really ran nothing", "Done." not in out)

    r = run("--quant", "int8")
    check("run: int8 swaps the quant flag", "--quant_int8" in r.stdout
          and "--quantize_4bit" not in r.stdout)
    r = run("--quant", "fp8", "--blocks-to-swap", "20")
    check("run: fp8 + swap is allowed", "--blocks_to_swap 20" in r.stdout
          and "--quantize_4bit" not in r.stdout, r.stdout[-300:])
    r = run("--quant", "nf4", "--blocks-to-swap", "20")
    check("run: NF4 + swap is REFUSED (the trainer force-zeroes it)",
          r.returncode == 2 and "cannot block-swap" in r.stderr, (r.returncode, r.stderr[-200:]))
    r = run("--skip-cache")
    check("run: --skip-cache drops the two cache steps",
          "cache latents" not in r.stdout and "=== train ===" in r.stdout)

    # a caption missing is the most common real breakage — it must be caught HERE,
    # not 20 minutes into a training run
    (exp / "images" / "duke_0002.txt").unlink()
    r = run()
    check("run: a missing caption stops it before any GPU time",
          r.returncode == 2 and "no caption" in r.stderr, (r.returncode, r.stderr[-200:]))
    (exp / "images" / "duke_0002.txt").write_text("rbmnduke man, portrait")

    # prefs.json gone -> a message that says what to do, not a traceback
    (fiz / "prefs.json").unlink()
    r = run()
    check("run: no prefs.json -> actionable error, not a stack trace",
          r.returncode == 2 and "Download models for me" in r.stderr
          and "Traceback" not in r.stderr, r.stderr[-300:])
    # …and an explicit flag still gets through
    r = run("--dit", str(mdl / names["krea2_raw_dit"]),
            "--vae", str(mdl / names["krea2_vae"]),
            "--text-encoder", str(mdl / names["krea2_text_encoder"]),
            "--turbo-dit", str(mdl / names["krea2_turbo_dit"]))
    check("run: explicit --dit/--vae/… work without prefs.json", r.returncode == 0,
          r.stderr[-300:])

    # not a Fizgig folder at all
    r = subprocess.run([sys.executable, str(exp / "fizgig_run.py"),
                        "--fizgig", str(td), "--dry-run"], capture_output=True, text=True)
    check("run: a wrong --fizgig path is named as such",
          r.returncode == 2 and "lora_trainer_gui.py" in r.stderr, r.stderr[-200:])

# ── 4. the wrappers ─────────────────────────────────────────────────────────
bat = ns["_fizgig_bat"](DS)
check("bat: has one FIZGIG line to edit", bat.count("set \"FIZGIG=") == 1, bat)
check("bat: runs from its own folder", "%~dp0" in bat)
check("bat: prefers Fizgig's venv python", "venv\\Scripts\\python.exe" in bat)
check("bat: checks the path before running", "lora_trainer_gui.py" in bat)
check("bat: forwards extra args", "%*" in bat)
sh = ns["_fizgig_sh"](DS)
check("sh: FIZGIG comes from the environment", "${FIZGIG:-" in sh)
check("sh: prefers the venv python", "venv/bin/python" in sh)
check("sh: forwards extra args", '"$@"' in sh)

# ── 5. the zip actually ships all of it ─────────────────────────────────────
for probe in ('z.writestr("fizgig_run.py"', 'z.writestr("train_krea2_fizgig.bat"',
              'z.writestr("train_krea2_fizgig.sh"', 'z.writestr("sample_prompts.txt"',
              'z.writestr("sample_ref.png"'):
    check(f"export ships {probe.split(chr(34))[1]}", probe in SRC)
check("export: the .bat gets CRLF (notepad/cmd)",
      '_fizgig_bat(ds).replace("\\n", "\\r\\n")' in SRC)
check("export: the sample ref is the same front base QC compares against",
      "ref_png = _identity_ref_png(ds)" in SRC)

# ── 6. the sheet no longer invents absolute model paths ─────────────────────
sheet = ns["_fizgig_commands"](DS, 40)
_cmd = [l for l in sheet.splitlines() if not l.lstrip().startswith("#")]
check("sheet: no invented /models/... absolute paths in the COMMANDS",
      not any("/models/" in l for l in _cmd), [l for l in _cmd if "/models/" in l])
check("sheet: says up front you do not have to type this",
      "YOU DO NOT NEED TO RUN THESE BY HAND" in sheet)
check("sheet: names the prefs.json keys", "krea2_raw_dit" in sheet
      and "krea2_text_encoder" in sheet)
check("sheet: flags the bf16 / fp8_scaled text-encoder ambiguity",
      "qwen3vl_4b_fp8_scaled" in sheet and "docs/CLI.md names the bf16 file" in sheet)
check("sheet: keeps the measured VRAM table", "3.09 s/it" in sheet
      and "RESOLUTION IS NOT THE LEVER" in sheet)
check("sheet: explains the sample files ship in the zip",
      "sample_prompts.txt and sample_ref.png are IN THIS ZIP" in sheet)

print()
print(f"{'ALL PASS' if not fails else str(len(fails)) + ' FAILURES: ' + ', '.join(fails)}")
sys.exit(1 if fails else 0)
