"""
Make-It-Animatable inference wrapper.

Provides functions to load MIA models and run inference for humanoid rigging.
Uses vendored MIA code from lib/mia/ for model loading (no bpy dependency).

IMPORTANT: All heavy imports (bpy, numpy, torch, trimesh) are lazy-loaded inside
functions to ensure torch_cluster (via mia/model.py) loads BEFORE bpy initializes
its bundled libraries. This avoids a segfault caused by library conflicts.
"""

import os
from pathlib import Path
from typing import Dict, Any, Optional, TYPE_CHECKING
import logging
import comfy.utils

log = logging.getLogger("unirig")


def _comfy_tqdm():
    """tqdm that shows download progress in ComfyUI's UI."""
    try:
        import comfy.utils
        import tqdm as _tqdm_mod
    except ImportError:
        return None
    holder = {"pbar": None, "total": 0, "done": 0}
    class _T(_tqdm_mod.tqdm):
        def __init__(self, *a, **kw):
            super().__init__(*a, **kw)
            if self.total and self.total > 0 and holder["pbar"] is None:
                holder["total"] = self.total
                holder["done"] = 0
                holder["pbar"] = comfy.utils.ProgressBar(self.total)
        def update(self, n=1):
            ret = super().update(n)
            if n and holder["pbar"] and holder["total"] > 0:
                holder["done"] = min(holder["done"] + n, holder["total"])
                holder["pbar"].update_absolute(holder["done"], holder["total"])
            return ret
    return _T


def _mm():
    import comfy.model_management
    return comfy.model_management
# Type hints only - not imported at runtime
if TYPE_CHECKING:
    import numpy as np
    import torch
    import trimesh

# Lazy bpy availability check (don't import at module level!)
_HAS_BPY: Optional[bool] = None


def _check_bpy_available() -> bool:
    """Lazily check if bpy is available. Called only when needed."""
    global _HAS_BPY
    if _HAS_BPY is None:
        try:
            import bpy  # noqa: F401
            _HAS_BPY = True
        except ImportError:
            _HAS_BPY = False
    return _HAS_BPY

# Get paths relative to this file
UTILS_DIR = Path(__file__).parent.absolute()
NODE_DIR = UTILS_DIR.parent

# MIA models directory: ComfyUI/models/mia/
# Supports override via MIA_MODELS_PATH environment variable
try:
    import folder_paths
    _COMFY_MODELS_DIR = Path(folder_paths.models_dir)
except ImportError:
    # Fallback if not running in ComfyUI context
    _COMFY_MODELS_DIR = NODE_DIR.parent.parent / "models"

if os.environ.get('MIA_MODELS_PATH'):
    MIA_MODELS_DIR = Path(os.environ['MIA_MODELS_PATH'])
else:
    MIA_MODELS_DIR = _COMFY_MODELS_DIR / "mia"

# Required model files
MIA_MODEL_FILES = [
    "bw.pth",
    "bw_normal.pth",
    "joints.pth",
    "joints_coarse.pth",
    "pose.pth",
]

# Shared model cache from load_model.py (single source of truth)
from .load_model import _MODEL_CACHE


def ensure_mia_models() -> bool:
    """
    Ensure MIA model files are downloaded.
    Downloads from HuggingFace if not present.

    Returns:
        True if all models are available, False otherwise.
    """
    missing = [m for m in MIA_MODEL_FILES if not (MIA_MODELS_DIR / m).exists()]

    if not missing:
        return True

    log.info("Downloading missing models: %s", missing)

    try:
        from huggingface_hub import hf_hub_download
        import tempfile

        MIA_MODELS_DIR.mkdir(parents=True, exist_ok=True)

        for model_file in missing:
            _mm().throw_exception_if_processing_interrupted()
            log.info("Downloading %s...", model_file)
            target_path = MIA_MODELS_DIR / model_file
            with tempfile.TemporaryDirectory(dir=str(MIA_MODELS_DIR)) as tmp_dir:
                hf_hub_download(
                    repo_id="jasongzy/Make-It-Animatable",
                    filename=f"output/best/new/{model_file}",
                    local_dir=tmp_dir,
                    local_dir_use_symlinks=False,
                    tqdm_class=_comfy_tqdm(),
                )
                downloaded = Path(tmp_dir) / "output" / "best" / "new" / model_file
                downloaded.rename(target_path)

        log.info("All models downloaded to %s", MIA_MODELS_DIR)
        return True

    except Exception as e:
        log.error("Error downloading models: %s", e)
        return False


def _wrap_model_patcher(model, load_device, offload_device):
    """Wrap a model in ComfyUI ModelPatcher for VRAM management."""
    import comfy.model_patcher
    return comfy.model_patcher.ModelPatcher(
        model, load_device=load_device, offload_device=offload_device
    )


def load_mia_models(dtype: str = "fp32") -> str:
    """
    Load all MIA models wrapped in ComfyUI ModelPatcher for VRAM management.

    Args:
        dtype: Model precision - "bf16", "fp16", or "fp32".

    Returns:
        Cache key string (models stay in worker, can't be pickled to host).
    """
    import torch  # Lazy import - loads torch_cluster via mia/ BEFORE bpy

    cache_key = f"mia_models_dtype={dtype}"

    if cache_key in _MODEL_CACHE:
        log.info("Using cached models")
        return cache_key  # Return key, not models

    # Ensure models are downloaded
    if not ensure_mia_models():
        raise RuntimeError("Failed to download MIA models")

    load_device = _mm().get_torch_device()
    offload_device = torch.device("cpu")
    torch_dtype = {"bf16": torch.bfloat16, "fp16": torch.float16, "fp32": torch.float32}.get(dtype, torch.float32)
    log.info("Loading MIA models (dtype=%s, load_device=%s, offload_device=%s)...", torch_dtype, load_device, offload_device)

    # Import vendored MIA modules
    from .mia import PCAE, JOINTS_NUM, KINEMATIC_TREE

    N = 32768  # Number of points to sample
    hands_resample_ratio = 0.5
    geo_resample_ratio = 0.0
    hierarchical_ratio = hands_resample_ratio + geo_resample_ratio

    def _load_and_wrap(name, model_cls_kwargs, checkpoint):
        """Load a model, cast dtype, wrap in ModelPatcher."""
        log.info("Loading %s...", name)
        model = PCAE(**model_cls_kwargs)
        model.load(str(MIA_MODELS_DIR / checkpoint))
        model.to(dtype=torch_dtype).eval()
        # Verify all params+buffers are target dtype
        mismatched = [(n, p.dtype) for n, p in model.named_parameters() if p.dtype != torch_dtype]
        mismatched += [(n, b.dtype) for n, b in model.named_buffers() if b.dtype != torch_dtype]
        if mismatched:
            log.warning("  %s has %d dtype mismatches after .to(): %s",
                        name, len(mismatched), mismatched[:5])
        patcher = _wrap_model_patcher(model, load_device, offload_device)
        log.info("  %s: ModelPatcher(load=%s, offload=%s, dtype=%s)", name, load_device, offload_device, torch_dtype)
        return patcher

    # Load all models on CPU with target dtype, wrapped in ModelPatcher
    patcher_coarse = _load_and_wrap("joints_coarse", dict(
        N=N, input_normal=False, deterministic=True, output_dim=JOINTS_NUM,
        predict_bw=False, predict_joints=True, predict_joints_tail=True,
    ), "joints_coarse.pth")

    patcher_bw = _load_and_wrap("bw", dict(
        N=N, input_normal=False, input_attention=False, deterministic=True,
        hierarchical_ratio=hierarchical_ratio,
    ), "bw.pth")

    patcher_bw_normal = _load_and_wrap("bw_normal", dict(
        N=N, input_normal=True, input_attention=True, deterministic=True,
        hierarchical_ratio=hierarchical_ratio,
    ), "bw_normal.pth")

    patcher_joints = _load_and_wrap("joints", dict(
        N=N, input_normal=False, deterministic=True, hierarchical_ratio=hierarchical_ratio,
        output_dim=JOINTS_NUM, kinematic_tree=KINEMATIC_TREE,
        predict_bw=False, predict_joints=True, predict_joints_tail=True,
        joints_attn_causal=True,
    ), "joints.pth")

    patcher_pose = _load_and_wrap("pose", dict(
        N=N, input_normal=False, deterministic=True, hierarchical_ratio=hierarchical_ratio,
        output_dim=JOINTS_NUM, kinematic_tree=KINEMATIC_TREE,
        predict_bw=False, predict_pose_trans=True, pose_mode="ortho6d",
        pose_input_joints=True, pose_attn_causal=True,
    ), "pose.pth")

    models = {
        "backend": "make_it_animatable",
        "patcher_coarse": patcher_coarse,
        "patcher_bw": patcher_bw,
        "patcher_bw_normal": patcher_bw_normal,
        "patcher_joints": patcher_joints,
        "patcher_pose": patcher_pose,
        "dtype": torch_dtype,
        "N": N,
        "hands_resample_ratio": hands_resample_ratio,
        "geo_resample_ratio": geo_resample_ratio,
    }

    _MODEL_CACHE[cache_key] = models
    log.info("All MIA models loaded and wrapped in ModelPatcher")

    return cache_key  # Return key, not models (models can't be pickled to host)


def get_cached_models(cache_key: str) -> Dict[str, Any]:
    """Get models from cache by key."""
    if cache_key not in _MODEL_CACHE:
        raise RuntimeError(f"Models not loaded: {cache_key}")
    return _MODEL_CACHE[cache_key]


def run_mia_inference(
    mesh: "trimesh.Trimesh",
    models: Dict[str, Any],
    output_path: str,
    no_fingers: bool = True,
    use_normal: bool = False,
    reset_to_rest: bool = True,
) -> str:
    """
    Run Make-It-Animatable inference on a mesh.

    Args:
        mesh: Input trimesh object.
        models: Loaded MIA models from load_mia_models().
        output_path: Path for output FBX file.
        no_fingers: If True, merge finger weights to hand (for models without separate fingers).
        use_normal: If True, use normals for better weights when limbs are close.
        reset_to_rest: If True, transform output to T-pose rest position.

    Returns:
        Path to output FBX file.
    """
    import numpy as np  # Lazy import
    import folder_paths  # Lazy import

    # Use vendored MIA pipeline
    from .mia.pipeline import prepare_input, preprocess, infer, bw_post_process
    from .mia import BONES_IDX_DICT, KINEMATIC_TREE

    N = models["N"]
    device = models["patcher_coarse"].load_device
    dtype = models["dtype"]

    log.info("Starting MIA inference (device=%s, dtype=%s)...", device, dtype)
    log.info("Options: no_fingers=%s, use_normal=%s, reset_to_rest=%s", no_fingers, use_normal, reset_to_rest)
    log.info("Input mesh: %d vertices, %d faces", len(mesh.vertices), len(mesh.faces))

    # Progress bar for the 4-step MIA inference pipeline + export
    pbar = comfy.utils.ProgressBar(5)

    # Prepare input
    log.info("Step 1/4: Preparing input (N=%d)...", N)
    data = prepare_input(
        mesh,
        N=N,
        hands_resample_ratio=models["hands_resample_ratio"],
        geo_resample_ratio=models["geo_resample_ratio"],
        get_normals=use_normal,
    )
    pbar.update(1)

    # Check for interruption before preprocessing
    _mm().throw_exception_if_processing_interrupted()

    # Preprocess (normalize, coarse joint localization)
    log.info("Step 2/4: Preprocessing (model_coarse, dtype=%s)...", dtype)
    data = preprocess(
        data,
        patcher_coarse=models["patcher_coarse"],
        device=device,
        dtype=dtype,
        hands_resample_ratio=models["hands_resample_ratio"],
        geo_resample_ratio=models["geo_resample_ratio"],
        N=N,
    )
    pbar.update(1)

    # Check for interruption before main inference
    _mm().throw_exception_if_processing_interrupted()

    # Run main inference (models loaded to GPU individually inside infer())
    log.info("Step 3/4: Running inference (model_bw, model_joints, model_pose, dtype=%s)...", dtype)
    data = infer(
        data,
        patcher_bw=models["patcher_bw"],
        patcher_bw_normal=models["patcher_bw_normal"],
        patcher_joints=models["patcher_joints"],
        patcher_pose=models["patcher_pose"],
        device=device,
        dtype=dtype,
        use_normal=use_normal,
    )
    pbar.update(1)

    # Check for interruption before post-processing
    _mm().throw_exception_if_processing_interrupted()

    # Post-process blend weights
    log.info("Step 4/4: Post-processing blend weights...")
    joints = data.joints
    head_idx = BONES_IDX_DICT["mixamorig:Head"]
    head_y = joints[..., head_idx, 4]  # tail y position (index 3:6 is tail)
    above_head_mask = data.verts[..., 1] >= head_y

    bw = bw_post_process(
        data.bw,
        bones_idx_dict=BONES_IDX_DICT,
        above_head_mask=above_head_mask,
        no_fingers=no_fingers,
    )

    # Prepare output data for Blender export
    joints_np = data.joints.squeeze(0).float().numpy()

    log.info("reset_to_rest=%s, data.pose is None: %s", reset_to_rest, data.pose is None)
    if data.pose is not None:
        log.info("Pose shape: %s", data.pose.shape)
        temp_dir = folder_paths.get_temp_directory()
        os.makedirs(temp_dir, exist_ok=True)
        pose_debug_path = os.path.join(temp_dir, "mia_pose_debug.npy")
        np.save(pose_debug_path, data.pose.squeeze(0).float().numpy())
        log.debug("Saved pose data to %s", pose_debug_path)

    output_data = {
        "mesh": data.mesh,
        "original_visual": data.original_visual,
        "gs": None,
        "joints": joints_np[..., :3],
        "joints_tail": joints_np[..., 3:] if joints_np.shape[-1] > 3 else None,
        "bw": bw.squeeze(0).float().numpy(),
        "pose": data.pose.squeeze(0).float().numpy() if reset_to_rest and data.pose is not None else None,
        "bones_idx_dict": BONES_IDX_DICT,
        "parent_indices": KINEMATIC_TREE.parent_indices,
        "pose_ignore_list": [],
    }

    pbar.update(1)

    # Check for interruption before FBX export
    _mm().throw_exception_if_processing_interrupted()

    # Export to FBX using MIA's Blender integration
    log.info("Exporting to FBX...")
    _export_mia_fbx(output_data, output_path, no_fingers, reset_to_rest)

    pbar.update(1)
    log.info("Inference complete: %s", output_path)
    return output_path


def _export_mia_fbx_direct(
    data: Dict[str, Any],
    output_path: str,
    remove_fingers: bool,
    reset_to_rest: bool,
    template_path: Path,
) -> None:
    """
    Export MIA results to FBX using bpy directly (inlined, no imports needed).
    """
    import tempfile
    import numpy as np  # Lazy import
    import bpy  # Lazy import - only imported here AFTER torch_cluster loaded
    from mathutils import Vector, Matrix

    mesh = data["mesh"]
    joints = data["joints"]
    joints_tail = data.get("joints_tail")
    bw = data["bw"]
    pose = data.get("pose")
    bones_idx_dict = dict(data["bones_idx_dict"])

    log.debug("Mesh to export: %d verts, %d faces, visual=%s",
              len(mesh.vertices), len(mesh.faces),
              type(mesh.visual).__name__ if hasattr(mesh, 'visual') else 'none')
    parent_indices = data.get("parent_indices")

    # Restore original visual (textures/materials) before export
    original_visual = data.get("original_visual")
    if original_visual is not None:
        mesh.visual = original_visual
        log.debug("Restored original visual: %s", type(original_visual).__name__)
    else:
        log.warning("No original_visual to restore")

    # Export processed mesh to temp GLB for import into Blender
    temp_mesh_file = tempfile.NamedTemporaryFile(suffix=".glb", delete=False)
    mesh_path = temp_mesh_file.name
    temp_mesh_file.close()

    mesh.export(mesh_path)
    log.debug("Exported temp GLB: %s (%d bytes)", mesh_path, os.path.getsize(mesh_path))

    try:
        log.debug("Weights: %s, Joints: %s, Bones: %d", bw.shape, joints.shape, len(bones_idx_dict))

        # Reset scene and load template
        bpy.ops.wm.read_factory_settings(use_empty=True)
        old_objs = set(bpy.context.scene.objects)
        bpy.ops.import_scene.fbx(filepath=str(template_path))
        template_objs = list(set(bpy.context.scene.objects) - old_objs)

        # Find armature
        armature = None
        for obj in template_objs:
            if obj.type == "ARMATURE":
                armature = obj
                break
        if armature is None:
            raise RuntimeError("No armature found in template!")

        log.info("Loaded template armature: %s", armature.name)

        # Capture template bone orientations (including z_axis for align_roll)
        bpy.context.view_layer.objects.active = armature
        bpy.ops.object.mode_set(mode='EDIT')
        template_bone_data = {}
        for bone in armature.data.edit_bones:
            template_bone_data[bone.name] = {
                'roll': bone.roll,
                'z_axis': tuple(bone.z_axis),  # Capture Z-axis for align_roll
            }
        bpy.ops.object.mode_set(mode='OBJECT')

        # Clear pose transforms
        armature.animation_data_clear()
        bpy.ops.object.mode_set(mode='POSE')
        bpy.ops.pose.select_all(action="SELECT")
        bpy.ops.pose.transforms_clear()
        bpy.ops.object.mode_set(mode='OBJECT')

        # Load input mesh from temp GLB
        old_objs = set(bpy.context.scene.objects)
        bpy.ops.import_scene.gltf(filepath=mesh_path)
        new_objs = set(bpy.context.scene.objects) - old_objs
        input_meshes = [obj for obj in new_objs if obj.type == "MESH"]

        if not input_meshes:
            raise RuntimeError("No mesh found in input!")

        log.debug("Loaded %d mesh(es) from GLB", len(input_meshes))

        # Remove template meshes
        for obj in template_objs:
            if obj.type == "MESH":
                bpy.data.objects.remove(obj, do_unlink=True)

        # Remove finger bones if requested
        if remove_fingers:
            finger_prefixes = [
                "LeftHandThumb", "LeftHandIndex", "LeftHandMiddle", "LeftHandRing", "LeftHandPinky",
                "RightHandThumb", "RightHandIndex", "RightHandMiddle", "RightHandRing", "RightHandPinky",
                "mixamorig:LeftHandThumb", "mixamorig:LeftHandIndex", "mixamorig:LeftHandMiddle",
                "mixamorig:LeftHandRing", "mixamorig:LeftHandPinky",
                "mixamorig:RightHandThumb", "mixamorig:RightHandIndex", "mixamorig:RightHandMiddle",
                "mixamorig:RightHandRing", "mixamorig:RightHandPinky",
            ]
            bpy.context.view_layer.objects.active = armature
            bpy.ops.object.mode_set(mode='EDIT')
            bones_to_remove = []
            for bone in armature.data.edit_bones:
                for prefix in finger_prefixes:
                    if bone.name.startswith(prefix):
                        bones_to_remove.append(bone.name)
                        break
            for bone_name in bones_to_remove:
                bone = armature.data.edit_bones.get(bone_name)
                if bone:
                    armature.data.edit_bones.remove(bone)
                if bone_name in bones_idx_dict:
                    del bones_idx_dict[bone_name]
            bpy.ops.object.mode_set(mode='OBJECT')
            log.debug("Removed %d finger bones", len(bones_to_remove))

        # Save armature's world matrix and get scaling factor
        matrix_world = armature.matrix_world.copy()
        scaling = matrix_world.to_scale()[0]

        # Reset armature to identity
        armature.matrix_world.identity()
        bpy.context.view_layer.update()

        # Transform mesh vertices: Y-Z swap and divide by scaling
        for mesh_obj in input_meshes:
            mesh_data = mesh_obj.data
            verts = np.array([v.co for v in mesh_data.vertices])
            new_y = verts[:, 2].copy()
            new_z = -verts[:, 1].copy()
            verts[:, 1] = new_y
            verts[:, 2] = new_z
            verts = verts / scaling
            for i, v in enumerate(mesh_data.vertices):
                v.co = verts[i]
            mesh_data.update()

        # Set bones with joints/scaling
        joints_normalized = joints / scaling
        joints_tail_normalized = joints_tail / scaling if joints_tail is not None else None

        bpy.context.view_layer.objects.active = armature
        bpy.ops.object.mode_set(mode='EDIT')

        # Update bone positions and apply template roll for Mixamo compatibility
        for bone in armature.data.edit_bones:
            bone.use_connect = False
            if bone.name in bones_idx_dict:
                idx = bones_idx_dict[bone.name]
                bone.head = Vector(joints_normalized[idx])
                if joints_tail_normalized is not None:
                    bone.tail = Vector(joints_tail_normalized[idx])
                # Apply template roll for Mixamo-compatible twist axis
                if bone.name in template_bone_data:
                    bone.roll = template_bone_data[bone.name]['roll']

        # Remove end bones not in prediction dict
        bones_to_remove = [b.name for b in armature.data.edit_bones if b.name not in bones_idx_dict]
        for bone_name in bones_to_remove:
            bone = armature.data.edit_bones.get(bone_name)
            if bone:
                armature.data.edit_bones.remove(bone)

        bpy.ops.object.mode_set(mode='OBJECT')

        # Parent mesh to armature
        for mesh_obj in input_meshes:
            mesh_obj.parent = armature

        # Apply weights
        vertices_num = [len(m.data.vertices) for m in input_meshes]
        weights_list = np.split(bw, np.cumsum(vertices_num)[:-1])

        for mesh_obj, mesh_bw in zip(input_meshes, weights_list):
            _mm().throw_exception_if_processing_interrupted()
            mesh_data = mesh_obj.data
            mesh_obj.vertex_groups.clear()
            for bone_name, bone_index in bones_idx_dict.items():
                group = mesh_obj.vertex_groups.new(name=bone_name)
                for v in mesh_data.vertices:
                    v_w = mesh_bw[v.index, bone_index]
                    if v_w > 1e-3:
                        group.add([v.index], float(v_w), "REPLACE")
            mesh_data.update()

        # Add armature modifier
        for mesh_obj in input_meshes:
            mod = mesh_obj.modifiers.new(name="Armature", type='ARMATURE')
            mod.object = armature
            mod.use_vertex_groups = True

        # Restore armature matrix
        armature.matrix_world = matrix_world
        bpy.context.view_layer.update()

        # Apply pose-to-rest if needed (imports helper functions from mia_export)
        if pose is not None and reset_to_rest and parent_indices is not None:
            log.info("Applying pose-to-rest transformation...")
            _apply_pose_to_rest_inline(armature, pose, bones_idx_dict, parent_indices, input_meshes, joints_normalized, template_bone_data)

        # Fix image filepaths and pack for FBX embedding
        # FBX exporter needs proper filepaths with filenames, not just directories
        log.debug("Fixing image filepaths for FBX export...")
        fbm_dir = output_path.rsplit('.', 1)[0] + '.fbm'
        os.makedirs(fbm_dir, exist_ok=True)

        for img in bpy.data.images:
            if img.size[0] > 0 and img.size[1] > 0:  # Valid image
                # Create a proper filepath with filename
                img_filename = f"{img.name}.png"
                img_filepath = os.path.join(fbm_dir, img_filename)

                # Save the image to disk first (FBX exporter needs this)
                old_filepath = img.filepath
                img.filepath_raw = img_filepath
                img.file_format = 'PNG'
                img.save()
                log.debug("Saved texture: %s", img_filepath)

                # Now pack it
                if img.packed_file is None:
                    try:
                        img.pack()
                        log.debug("Packed: %s", img.name)
                    except Exception as e:
                        log.debug("Failed to pack %s: %s", img.name, e)

        # Export FBX
        bpy.context.view_layer.update()
        bpy.ops.export_scene.fbx(
            filepath=output_path,
            use_selection=False,
            object_types={'ARMATURE', 'MESH'},
            add_leaf_bones=False,
            bake_anim=False,
            path_mode='COPY',
            embed_textures=True,
        )
        log.info("Exported to: %s", output_path)

        # Also export GLB (better texture support for preview tools)
        glb_path = output_path.rsplit('.', 1)[0] + '.glb'
        bpy.ops.export_scene.gltf(
            filepath=glb_path,
            export_format='GLB',
            export_texcoords=True,
            export_normals=True,
            export_materials='EXPORT',
            export_image_format='AUTO',
        )
        log.debug("Also exported GLB: %s", glb_path)

    finally:
        # Clean up temp mesh file
        if os.path.exists(mesh_path):
            os.remove(mesh_path)


def _apply_pose_to_rest_inline(armature_obj, pose, bones_idx_dict, parent_indices, input_meshes, mia_joints, template_bone_data=None):
    """Apply MIA's pose prediction to transform skeleton from input pose to T-pose rest (inlined)."""
    import numpy as np  # Lazy import
    import bpy  # Lazy import
    from mathutils import Matrix

    def ortho6d_to_matrix(ortho6d):
        x_raw = ortho6d[:3]
        y_raw = ortho6d[3:6]
        x = x_raw / (np.linalg.norm(x_raw) + 1e-8)
        z = np.cross(x, y_raw)
        z = z / (np.linalg.norm(z) + 1e-8)
        y = np.cross(z, x)
        return np.column_stack([x, y, z])

    def get_rotation_about_point(rotation, point):
        transform = np.eye(4)
        transform[:3, :3] = rotation
        transform[:3, 3] = point - rotation @ point
        return transform

    joints = mia_joints
    K = pose.shape[0]

    # Convert ortho6d to rotation matrices
    rot_matrices = np.zeros((K, 3, 3))
    for i in range(K):
        rot_matrices[i] = ortho6d_to_matrix(pose[i])

    # Initialize transforms
    pose_global = np.zeros((K, 4, 4))
    for i in range(K):
        pose_global[i] = get_rotation_about_point(rot_matrices[i], joints[i])

    # Propagate through kinematic chain
    posed_joints = joints.copy()
    for i in range(1, K):
        _mm().throw_exception_if_processing_interrupted()
        parent_idx = parent_indices[i]
        parent_matrix = pose_global[parent_idx]
        posed_joints[i] = parent_matrix[:3, :3] @ joints[i] + parent_matrix[:3, 3]
        matrix = get_rotation_about_point(rot_matrices[i], joints[i])
        matrix[:3, 3] += posed_joints[i] - joints[i]
        pose_global[i] = matrix

    pose_global[0] = np.eye(4)

    # Finger prefixes to skip
    finger_prefixes = [
        "LeftHandThumb", "LeftHandIndex", "LeftHandMiddle", "LeftHandRing", "LeftHandPinky",
        "RightHandThumb", "RightHandIndex", "RightHandMiddle", "RightHandRing", "RightHandPinky",
        "mixamorig:LeftHandThumb", "mixamorig:LeftHandIndex", "mixamorig:LeftHandMiddle",
        "mixamorig:LeftHandRing", "mixamorig:LeftHandPinky",
        "mixamorig:RightHandThumb", "mixamorig:RightHandIndex", "mixamorig:RightHandMiddle",
        "mixamorig:RightHandRing", "mixamorig:RightHandPinky",
    ]

    def is_finger_bone(name):
        return any(name.startswith(p) for p in finger_prefixes)

    # Apply transforms in pose mode
    bpy.ops.object.mode_set(mode='POSE')
    for bone_name, idx in bones_idx_dict.items():
        pbone = armature_obj.pose.bones.get(bone_name)
        if pbone is None or is_finger_bone(bone_name):
            continue
        pose_matrix = Matrix(pose_global[idx].tolist())
        pbone.matrix = pose_matrix @ pbone.bone.matrix_local
        bpy.context.view_layer.update()

    # Clear bone locations (match original MIA behavior from app_blender.py:124)
    for bone_name in bones_idx_dict:
        pbone = armature_obj.pose.bones.get(bone_name)
        if pbone:
            pbone.location = (0, 0, 0)
    bpy.context.view_layer.update()

    bpy.ops.object.mode_set(mode='OBJECT')

    # Apply posed armature as new rest pose
    for mesh_obj in input_meshes:
        bpy.context.view_layer.objects.active = mesh_obj
        for mod in mesh_obj.modifiers:
            if mod.type == 'ARMATURE':
                bpy.ops.object.modifier_apply(modifier=mod.name)
                break

    bpy.context.view_layer.objects.active = armature_obj
    bpy.ops.object.mode_set(mode='POSE')
    bpy.ops.pose.armature_apply(selected=False)
    bpy.ops.object.mode_set(mode='OBJECT')

    # Re-apply template bone orientations after armature_apply
    # (armature_apply recalculates rolls based on new bone directions, losing template rolls)
    # Use align_roll() with template Z-axis for correct orientation regardless of bone direction
    if template_bone_data:
        from mathutils import Vector
        bpy.ops.object.mode_set(mode='EDIT')
        roll_count = 0
        for bone in armature_obj.data.edit_bones:
            if bone.name in template_bone_data:
                template_data = template_bone_data[bone.name]
                if 'z_axis' in template_data:
                    # Use align_roll with template Z-axis for correct orientation
                    bone.align_roll(Vector(template_data['z_axis']))
                else:
                    # Fallback to direct roll if z_axis not available
                    bone.roll = template_data['roll']
                roll_count += 1
        bpy.ops.object.mode_set(mode='OBJECT')
        log.info("Re-applied template bone orientations to %s bones after pose-to-rest", roll_count)

    # Re-add armature modifier
    for mesh_obj in input_meshes:
        mod = mesh_obj.modifiers.new(name="Armature", type='ARMATURE')
        mod.object = armature_obj
        mod.use_vertex_groups = True

    # Clear remaining pose transforms
    bpy.ops.object.mode_set(mode='POSE')
    bpy.ops.pose.select_all(action='SELECT')
    bpy.ops.pose.transforms_clear()
    bpy.ops.object.mode_set(mode='OBJECT')

    log.info("Skeleton transformed to rest pose")


def _export_mia_fbx(
    data: Dict[str, Any],
    output_path: str,
    remove_fingers: bool,
    reset_to_rest: bool,
) -> None:
    """
    Export MIA results to FBX using bpy directly.

    Uses bpy (Blender as Python module) which is available in the isolated _env_unirig.
    Falls back to subprocess method if bpy is not available.
    """
    # Get template path - use UniRig's bundled Mixamo template
    ASSETS_DIR = NODE_DIR / "assets"  # ComfyUI-UniRig/assets
    template_path = ASSETS_DIR / "animation_characters" / "mixamo.fbx"
    if not template_path.exists():
        raise FileNotFoundError(f"No Mixamo template found. Expected at: {template_path}")

    if not _check_bpy_available():
        raise RuntimeError(
            "bpy is not available. MIA FBX export requires bpy.\n"
            "Ensure you are running in the unirig isolated environment."
        )

    log.info("Exporting FBX via bpy...")
    _export_mia_fbx_direct(data, output_path, remove_fingers, reset_to_rest, template_path)

    if not os.path.exists(output_path):
        raise RuntimeError(f"Export completed but output file not created: {output_path}")
