"""Tier-1 3D character pipeline -- mesh generation + auto-rigging (v1.173).

Builds the two once-per-character ComfyUI graphs:

* **Mesh** -- Hunyuan3D-2 shape-only (BUILT INTO ComfyUI core): the character's
  active base render (plus the left/back/right views when the base was made as
  a 4-view set) conditions ``Hunyuan3Dv2ConditioningMultiView`` and the sampled
  voxels become an untextured GLB (``VoxelToMesh`` -> ``SaveGLB``).
* **Rig** -- the ComfyUI-UniRig custom node (``UniRigLoadMesh`` ->
  ``UniRigLoadModel`` -> ``UniRigAutoRig``): skeleton + skin weights ->
  rigged FBX.  ``skeleton_template``: ``mixamo`` (humanoid -- the retarget
  target for our pose library) or ``articulationxl`` (arbitrary skeletons --
  the furry/creature path).

Untextured by design (texture stage exceeds 16GB VRAM; Klein repaints
appearance anyway; and clean clay geometry avoids the CGI style leak Rule).

UniRig's node schemas are third-party and may drift between wrapper versions,
so its graph is built DEFENSIVELY from the worker's /object_info: required
inputs are filled from their declared defaults and only the inputs we care
about (mesh path, model, template) are overridden -- with readable errors when
the expected inputs can't be found.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

MESH3D_CLASSES = ("Hunyuan3Dv2ConditioningMultiView", "VAEDecodeHunyuan3D",
                  "VoxelToMesh", "EmptyLatentHunyuan3Dv2", "SaveGLB")
UNIRIG_CLASSES = ("UniRigLoadMesh", "UniRigLoadModel", "UniRigAutoRig")


def find_unirig_classes(oi: dict) -> Optional[Dict[str, str]]:
    """v1.173.1: locate the UniRig node classes even when the wrapper renames
    them (third-party schema drift).  Returns {'load','model','rig'} class
    names or None.  Canonical names win; otherwise fuzzy-match any registered
    class containing 'unirig'."""
    keys = list((oi or {}).keys())
    if all(c in keys for c in UNIRIG_CLASSES):
        return {"load": "UniRigLoadMesh", "model": "UniRigLoadModel",
                "rig": "UniRigAutoRig"}
    uni = [k for k in keys if "unirig" in k.lower().replace(" ", "").replace("_", "")]
    if not uni:
        return None

    def _pick(*needles):
        for k in uni:
            lk = k.lower().replace(" ", "").replace("_", "")
            if all(n in lk for n in needles):
                return k
        return None

    load = _pick("load", "mesh")
    model = _pick("load", "model") or _pick("model")
    rig = _pick("autorig") or _pick("rig")
    if rig == load or rig == model:
        rig = _pick("autorig")
    if load and model and rig:
        return {"load": load, "model": model, "rig": rig}
    return None


def unirig_diagnostic(oi: dict) -> str:
    """Human-readable hint for the missing-UniRig case: name any rig-ish
    classes that ARE registered so version drift is visible at a glance."""
    near = [k for k in (oi or {}).keys()
            if any(n in k.lower() for n in ("unirig", "rig", "mia", "skeleton"))][:12]
    hint = ("install/enable the 'comfyui-unirig' custom node and RESTART ComfyUI "
            "(check the ComfyUI startup console for a ComfyUI-UniRig import error "
            "-- its installer is experimental and a failed first-run leaves the "
            "nodes unregistered)")
    if near:
        hint += f". Rig-ish classes the worker DOES have: {', '.join(sorted(near))}"
    return hint


def _options(oi: dict, class_type: str, input_name: str) -> List[str]:
    from backend.services.character_studio.vnccs_native.klein_poses import _options as _o
    return _o(oi, class_type, input_name)


def resolve_mesh3d_models(oi: dict, settings: Optional[dict] = None) -> Dict[str, Any]:
    """Resolve the Hunyuan3D checkpoint + verify the node classes exist.
    Settings override: mesh3d_checkpoint."""
    st = settings or {}
    missing = [c for c in MESH3D_CLASSES if c not in (oi or {})]
    if missing:
        raise ValueError("Hunyuan3D mesh nodes missing on this worker -- update "
                         "ComfyUI to current master (missing: " + ", ".join(missing) + ")")
    ckpts = _options(oi, "ImageOnlyCheckpointLoader", "ckpt_name")
    want = str(st.get("mesh3d_checkpoint") or "").strip()
    hit = None
    if want:
        hit = next((o for o in ckpts if want.lower() in o.lower()), None)
    if not hit:
        # prefer multiview, then 2.1, then any hunyuan3d
        for needles in (["hunyuan3d-dit-v2-mv"], ["hunyuan_3d_v2.1", "hunyuan3d_2.1"],
                        ["hunyuan3d"]):
            hit = next((o for o in ckpts
                        if any(n in o.lower().replace("\\", "/") for n in needles)), None)
            if hit:
                break
    if not hit:
        raise ValueError("No Hunyuan3D checkpoint on this worker -- download "
                         "hunyuan3d-dit-v2-mv_fp16.safetensors (4.93GB, "
                         "Comfy-Org/hunyuan3D_2.0_repackaged) into models/checkpoints/")
    is_21 = "2.1" in hit or "2_1" in hit
    return {"checkpoint": hit, "v21": is_21,
            "latent_res": 4096 if is_21 else 3072,
            "steps": 30 if is_21 else 20,
            "cfg": 5.0 if is_21 else 7.5}


def build_hunyuan3d_graph(
    *,
    view_files: Dict[str, str],
    models: Dict[str, Any],
    seed: int,
    filename_prefix: str = "rbmn_mesh3d/mesh",
) -> Tuple[Dict[str, dict], Dict[str, str]]:
    """Shape-only mesh graph per the official ComfyUI template.
    ``view_files``: worker input filenames keyed by view -- 'front' required,
    'left'/'back'/'right' optional (multiview conditioning when present)."""
    if not view_files.get("front"):
        raise ValueError("mesh graph needs at least the FRONT view image")
    api: Dict[str, dict] = {
        "ckpt": {"class_type": "ImageOnlyCheckpointLoader",
                 "inputs": {"ckpt_name": models["checkpoint"]}},
        "ms": {"class_type": "ModelSamplingAuraFlow",
               "inputs": {"model": ["ckpt", 0], "shift": 1.0}},
        "lat": {"class_type": "EmptyLatentHunyuan3Dv2",
                "inputs": {"resolution": int(models.get("latent_res") or 3072),
                           "batch_size": 1}},
    }
    cond_inputs: Dict[str, Any] = {}
    for view in ("front", "left", "back", "right"):
        fn = view_files.get(view)
        if not fn:
            continue
        api[f"img_{view}"] = {"class_type": "LoadImage", "inputs": {"image": fn}}
        api[f"cv_{view}"] = {"class_type": "CLIPVisionEncode",
                             "inputs": {"clip_vision": ["ckpt", 1],
                                        "image": [f"img_{view}", 0],
                                        "crop": "center"}}
        cond_inputs[view] = [f"cv_{view}", 0]
    api["cond"] = {"class_type": "Hunyuan3Dv2ConditioningMultiView", "inputs": cond_inputs}
    api["ks"] = {"class_type": "KSampler",
                 "inputs": {"model": ["ms", 0], "seed": int(seed),
                            "steps": int(models.get("steps") or 20),
                            "cfg": float(models.get("cfg") or 7.5),
                            "sampler_name": "euler", "scheduler": "normal",
                            "positive": ["cond", 0], "negative": ["cond", 1],
                            "latent_image": ["lat", 0], "denoise": 1.0}}
    api["vox"] = {"class_type": "VAEDecodeHunyuan3D",
                  "inputs": {"samples": ["ks", 0], "vae": ["ckpt", 2],
                             "num_chunks": 8000, "octree_resolution": 256}}
    api["mesh"] = {"class_type": "VoxelToMesh",
                   "inputs": {"voxel": ["vox", 0], "algorithm": "surface net",
                              "threshold": 0.6}}
    api["save"] = {"class_type": "SaveGLB",
                   "inputs": {"mesh": ["mesh", 0], "filename_prefix": filename_prefix}}
    return api, {"glb": "save"}


def _input_spec(oi: dict, class_type: str) -> Dict[str, tuple]:
    """{input_name: (kind, default_or_options)} for required+optional inputs."""
    out: Dict[str, tuple] = {}
    node = (oi or {}).get(class_type) or {}
    for section in ("required", "optional"):
        for name, spec in ((node.get("input") or {}).get(section) or {}).items():
            if not isinstance(spec, (list, tuple)) or not spec:
                continue
            first = spec[0]
            cfg = spec[1] if len(spec) > 1 and isinstance(spec[1], dict) else {}
            if isinstance(first, list):                       # combo
                out[name] = ("combo", first)
            elif isinstance(first, str) and first in ("INT", "FLOAT", "STRING", "BOOLEAN"):
                out[name] = (first.lower(), cfg.get("default"))
            else:                                             # typed link (MODEL, TRIMESH, ...)
                out[name] = ("link", str(first))
    return out


def _fill_defaults(spec: Dict[str, tuple]) -> Dict[str, Any]:
    inputs: Dict[str, Any] = {}
    for name, (kind, dv) in spec.items():
        if kind == "combo" and isinstance(dv, list) and dv:
            inputs[name] = dv[0]
        elif kind in ("int", "float", "string", "boolean") and dv is not None:
            inputs[name] = dv
    return inputs


def build_unirig_graph(
    *,
    oi: dict,
    mesh_filename: str,
    template: str = "mixamo",
    fbx_name: str = "rbmn_rig",
) -> Tuple[Dict[str, dict], Dict[str, str]]:
    """UniRig auto-rig graph, built defensively from /object_info.

    v1.173.2 -- wiring matches the wrapper's REAL schemas (verified against
    the PozzettiAndrea/ComfyUI-UniRig source + its bundled
    unirig_humanoid.json workflow):

    * ``UniRigLoadMesh`` has no free path input -- it takes a
      ``source_folder`` combo ('input'|'output') plus a ``file_path`` combo
      scanned from that folder (output scans use OS-specific separators, so
      output paths aren't portable).  Callers must UPLOAD the GLB to the
      worker's input root and pass its bare filename as ``mesh_filename``.
    * ``UniRigAutoRig`` is NOT an output node -- it returns a STRING
      (fbx_output_path).  Without a terminal output node ComfyUI prunes the
      whole graph and nothing executes, so ``UniRigPreviewRiggedMesh``
      (is_output_node=True) is appended; it also reports the FBX filename in
      history ui outputs ('fbx_file').
    * The FBX is auto-saved to the output ROOT as
      ``<fbx_name>_<template>.fbx`` -- pass ``fbx_name`` so the result is
      fetchable by name even if history parsing fails.
    """
    classes = find_unirig_classes(oi)
    if not classes:
        raise ValueError("ComfyUI-UniRig nodes missing on this worker -- "
                         + unirig_diagnostic(oi))
    api: Dict[str, dict] = {}

    lm_spec = _input_spec(oi, classes["load"])
    lm_in = _fill_defaults(lm_spec)
    if "source_folder" in lm_spec:
        lm_in["source_folder"] = "input"
    # find the path-ish combo/string input to point at our uploaded GLB
    path_key = next((k for k in lm_spec
                     if k in ("file_path", "mesh_path", "path", "glb_path", "mesh_file", "file")), None)
    if path_key is None:
        path_key = next((k for k, (kind, _) in lm_spec.items()
                         if kind == "combo" and k != "source_folder"), None)
    if path_key is None:
        path_key = next((k for k, (kind, _) in lm_spec.items() if kind == "string"), None)
    if path_key is None:
        raise ValueError(f"{classes['load']}: could not find a mesh path input "
                         f"(inputs: {sorted(lm_spec)})")
    lm_in[path_key] = mesh_filename
    api["lm"] = {"class_type": classes["load"], "inputs": lm_in}

    md_spec = _input_spec(oi, classes["model"])
    api["md"] = {"class_type": classes["model"], "inputs": _fill_defaults(md_spec)}

    ar_spec = _input_spec(oi, classes["rig"])
    ar_in = _fill_defaults(ar_spec)
    # wire the typed links (TRIMESH / UNIRIG_MODEL)
    mesh_key = next((k for k, (kind, t) in ar_spec.items()
                     if kind == "link" and "MESH" in str(t).upper()), None)
    model_key = next((k for k, (kind, t) in ar_spec.items()
                      if kind == "link" and "MODEL" in str(t).upper()), None)
    if not mesh_key or not model_key:
        raise ValueError(f"{classes['rig']}: could not find mesh/model inputs "
                         f"(inputs: {sorted(ar_spec)})")
    ar_in[mesh_key] = ["lm", 0]
    ar_in[model_key] = ["md", 0]
    tpl_key = next((k for k in ar_spec if "template" in k.lower()), None)
    if tpl_key:
        kind, opts = ar_spec[tpl_key]
        want = "articulationxl" if str(template).lower().startswith("art") else "mixamo"
        if kind == "combo" and isinstance(opts, list):
            hit = next((o for o in opts if want in str(o).lower()), None)
            ar_in[tpl_key] = hit if hit is not None else (opts[0] if opts else want)
        else:
            ar_in[tpl_key] = want
    fbx_key = next((k for k in ar_spec if "fbx" in k.lower() and "name" in k.lower()), None)
    if fbx_key:
        ar_in[fbx_key] = fbx_name
    api["rig"] = {"class_type": classes["rig"], "inputs": ar_in}

    # terminal OUTPUT node -- without one ComfyUI executes nothing
    pv_cls = "UniRigPreviewRiggedMesh" if "UniRigPreviewRiggedMesh" in (oi or {}) else None
    if pv_cls is None:
        def _norm(s: str) -> str:
            return s.lower().replace(" ", "").replace("_", "")
        uni = [k for k in (oi or {}) if "unirig" in _norm(k)]
        for needles in (("preview", "rig"), ("view", "rig")):
            pv_cls = next((k for k in uni
                           if all(n in _norm(k) for n in needles)), None)
            if pv_cls:
                break
    if pv_cls is None:
        raise ValueError("ComfyUI-UniRig preview/output node missing -- the rig "
                         "graph needs a terminal output node (UniRigPreviewRiggedMesh); "
                         + unirig_diagnostic(oi))
    pv_spec = _input_spec(oi, pv_cls)
    pv_in = _fill_defaults(pv_spec)
    pv_key = next((k for k in pv_spec if "fbx" in k.lower()), None) \
        or next((k for k, (kind, _) in pv_spec.items() if kind == "string"), None)
    if pv_key is None:
        raise ValueError(f"{pv_cls}: could not find the fbx path input "
                         f"(inputs: {sorted(pv_spec)})")
    pv_in[pv_key] = ["rig", 0]
    api["pv"] = {"class_type": pv_cls, "inputs": pv_in}
    return api, {"rig": "rig", "preview": "pv"}


def harvest_output_files(history_entry: dict, exts: Tuple[str, ...]) -> List[dict]:
    """Every output-file record in a history entry whose filename matches
    ``exts`` -- SaveGLB/UniRig nodes report under varying keys ('images',
    'result', '3d', 'files', ...), so scan every list.  v1.173.2: UniRig's
    preview node reports plain STRING paths (ui 'fbx_file': ["name.fbx"]),
    normalized here to {'filename','subfolder','type'} records."""
    hits: List[dict] = []
    for node_out in (history_entry.get("outputs") or {}).values():
        if not isinstance(node_out, dict):
            continue
        for val in node_out.values():
            if not isinstance(val, list):
                continue
            for item in val:
                if isinstance(item, dict) and str(item.get("filename", "")).lower().endswith(exts):
                    hits.append(item)
                elif isinstance(item, str) and item.lower().endswith(exts):
                    s = item.replace("\\", "/")
                    sub, _, fn = s.rpartition("/")
                    hits.append({"filename": fn, "subfolder": sub, "type": "output"})
    return hits
