"""Shim for comfy.model_management -- device pick + explicit model moves.
Device resolution honors the MIA_DEVICE env var ('cpu' forces CPU even when
CUDA exists); otherwise CUDA when available, else CPU."""
import gc
import os

import torch


def get_torch_device():
    want = os.environ.get("MIA_DEVICE", "").strip().lower()
    if want == "cpu":
        return torch.device("cpu")
    if want.startswith("cuda") and torch.cuda.is_available():
        return torch.device(want)
    return torch.device("cuda" if torch.cuda.is_available() else "cpu")


def load_models_gpu(patchers, *_a, **_k):
    """Move each patcher's model to its load device (MIA calls this right
    before using patcher.model)."""
    for p in patchers or []:
        model = getattr(p, "model", None)
        dev = getattr(p, "load_device", None) or get_torch_device()
        if model is not None:
            p.model = model.to(dev)


def throw_exception_if_processing_interrupted():
    return None


def soft_empty_cache(*_a, **_k):
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()


def unet_offload_device():
    return torch.device("cpu")


def should_use_bf16(*_a, **_k):
    return False


def should_use_fp16(*_a, **_k):
    return False
