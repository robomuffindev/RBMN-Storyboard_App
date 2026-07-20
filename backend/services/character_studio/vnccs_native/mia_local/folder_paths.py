"""Shim for ComfyUI's folder_paths -- temp/output dirs for the MIA driver."""
import os
import tempfile

_BASE = os.environ.get("MIA_WORK_DIR") or os.path.join(tempfile.gettempdir(), "rbmn_mia")
models_dir = os.environ.get("MIA_MODELS_PATH") or os.path.join(_BASE, "models")
base_path = _BASE


def get_temp_directory():
    p = os.path.join(_BASE, "temp")
    os.makedirs(p, exist_ok=True)
    return p


def get_output_directory():
    p = os.path.join(_BASE, "output")
    os.makedirs(p, exist_ok=True)
    return p
