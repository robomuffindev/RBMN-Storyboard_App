"""Minimal ComfyUI API shims (v1.174) so the vendored Make-It-Animatable code
(taken from PozzettiAndrea/ComfyUI-UniRig, MIT) runs STANDALONE inside the
app's dedicated MIA venv -- no ComfyUI required.

Only the exact surface the vendored code touches is provided:
comfy.ops.disable_weight_init (Linear/LayerNorm), comfy.utils.load_torch_file
+ ProgressBar, comfy.model_management (get_torch_device / load_models_gpu /
throw_exception_if_processing_interrupted / soft_empty_cache), and
comfy.model_patcher.ModelPatcher.
"""
