"""Shim for comfy.model_patcher.ModelPatcher -- MIA only reads .model and
.load_device (moves happen in our load_models_gpu shim)."""
import torch


class ModelPatcher:
    def __init__(self, model, load_device=None, offload_device=None, **_k):
        self.model = model
        self.load_device = load_device or torch.device("cpu")
        self.offload_device = offload_device or torch.device("cpu")
