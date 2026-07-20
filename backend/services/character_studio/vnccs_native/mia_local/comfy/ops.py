"""Shim for comfy.ops -- MIA only uses operations.Linear / operations.LayerNorm.
Like ComfyUI's disable_weight_init, these REALLY skip the random init
(reset_parameters no-op): checkpoint weights overwrite everything anyway, and
skipping init makes constructing the ~500M params of MIA models near-instant
instead of grinding through kaiming init on CPU (v1.174.2)."""
import torch.nn as nn


class _Linear(nn.Linear):
    def reset_parameters(self):
        return None


class _LayerNorm(nn.LayerNorm):
    def reset_parameters(self):
        return None


class _Conv1d(nn.Conv1d):
    def reset_parameters(self):
        return None


class _Embedding(nn.Embedding):
    def reset_parameters(self):
        return None


class disable_weight_init:  # noqa: N801 (matches ComfyUI's naming)
    Linear = _Linear
    LayerNorm = _LayerNorm
    Conv1d = _Conv1d
    Conv2d = nn.Conv2d
    Conv3d = nn.Conv3d
    GroupNorm = nn.GroupNorm
    Embedding = _Embedding


manual_cast = disable_weight_init
