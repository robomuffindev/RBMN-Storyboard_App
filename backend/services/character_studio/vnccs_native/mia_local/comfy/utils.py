"""Shim for comfy.utils -- load_torch_file + ProgressBar."""
import sys

import torch


def load_torch_file(path, safe_load=False, device=None):
    if str(path).lower().endswith((".safetensors", ".sft")):
        from safetensors.torch import load_file
        return load_file(path, device="cpu")
    try:
        sd = torch.load(path, map_location="cpu", weights_only=True)
    except Exception:  # noqa: BLE001 -- older pickled checkpoints
        sd = torch.load(path, map_location="cpu", weights_only=False)
    # Mirror ComfyUI's checkpoint unwrapping: training checkpoints wrap the
    # real weights ({"state_dict": ...} or a single-key dict like
    # {"model": ...} -- MIA's .pth files use the latter). v1.174.3.
    if isinstance(sd, dict) and "state_dict" in sd and isinstance(sd["state_dict"], dict):
        return sd["state_dict"]
    if isinstance(sd, dict) and len(sd) == 1:
        inner = next(iter(sd.values()))
        if isinstance(inner, dict):
            return inner
    return sd


class ProgressBar:
    """Prints machine-readable progress lines the app can parse."""

    def __init__(self, total):
        self.total = int(total)
        self.current = 0

    def update(self, n=1):
        self.current += int(n)
        print(f"MIA_STEP {self.current}/{self.total}", flush=True)

    def update_absolute(self, value, total=None, preview=None):
        if total is not None:
            self.total = int(total)
        self.current = int(value)
        print(f"MIA_STEP {self.current}/{self.total}", flush=True)


def _noop(*_a, **_k):
    return None


def __getattr__(name):  # tolerate any other comfy.utils access
    print(f"[mia-shim] comfy.utils.{name} -> noop", file=sys.stderr, flush=True)
    return _noop
