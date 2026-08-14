"""Unit test for _weights_for_epoch — the checkpoint picker (v1.276.49).

WHY THIS EXISTS: install used to CONSTRUCT the checkpoint filename from the
best-scoring epoch. Scoring counts epochs from PREVIEW images and installing
needs a WEIGHTS file, and the two sets are not guaranteed to line up — so a
seven-hour run that trained and scored perfectly died at its final step with
`FileNotFoundError: viv2-auto-62d1c1-000039.safetensors`.

FREE: no worker, no GPU, no network. Run after touching the train pipeline.
    python scripts/test_weights_pick.py
"""
import sys, types
sys.path.insert(0, str(__import__('pathlib').Path(__file__).resolve().parents[1]))
# import just the function without booting the app
import importlib.util, re
from typing import Dict
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
src = (ROOT / 'backend' / 'api' / 'lora_train.py').read_text(encoding='utf-8')
start = src.index('def _weights_for_epoch')
end = src.index('def _score_and_pick')
ns = {'re': re, 'Dict': Dict, 'logger': types.SimpleNamespace(warning=lambda *a, **k: None)}
exec(src[start:end], ns)
f = ns['_weights_for_epoch']

def run(names):
    return {"artifacts": [{"kind": "weights", "name": n} for n in names]}

W = [f"viv2-auto-62d1c1-{e:06d}.safetensors" for e in range(1, 41)]
st = {}
print("exact match      :", f(run(W), "viv2-auto-62d1c1", 39, st) )
assert f(run(W), "x", 39, st).endswith("000039.safetensors")

# the real failure: preview epoch 39 exists, weights only go to 38
st = {}
got = f(run(W[:38]), "x", 39, st)
print("missing -> nearest:", got, "|", st.get("install_note", "")[:70])
assert got.endswith("000038.safetensors") and st.get("install_note")

# weights named with 4 digits instead of 6
st = {}
print("4-digit names    :", f(run(["ds-0012.safetensors", "ds-0039.safetensors"]), "x", 39, st))

# state files must be ignored
st = {}
r = {"artifacts": [{"kind": "weights", "name": "ds-000039-state.safetensors"},
                   {"kind": "weights", "name": "ds-000012.safetensors"}]}
print("ignores -state   :", f(r, "x", 39, st))
assert "state" not in f(r, "x", 39, st)

# nothing usable -> a message naming what was seen
try:
    f({"artifacts": [{"kind": "image", "name": "p_e000039_00_.png"}]}, "x", 39, {})
    print("FAIL: should have raised")
except RuntimeError as e:
    print("no checkpoints   : raises ->", str(e)[:80])
print("\nALL PICKER TESTS PASS")
