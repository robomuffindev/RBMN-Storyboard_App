"""Character Studio P2 — dual-engine stage framework (Qwen/VNCCS vs Klein).

Every P2 stage (pose, costume, emotion, cutout, upscale) can run on either:

- **qwen** — the VNCCS-quality Qwen-Image-Edit-2511 graphs shipped as
  ``studio_qie_edit`` / ``studio_rmbg2`` (workflow_type), which require a
  ComfyUI worker advertising the ``"vnccs"`` capability (auto-discovered
  when the worker exposes the ``VNCCS_QWEN_Encoder`` node — see
  ``backend/services/comfyui/dispatcher.py::discover_capabilities``).
- **klein** — our existing Klein edit-ref workflows (``klein_2ref`` /
  ``klein_1ref`` / ``klein_inpaint``), zero extra worker deps, lower
  fidelity for pose-exact / clothes-swap / expression-only edits but always
  available.

``resolve_engine`` is the single gate: it decides which engine a request
actually runs on (honoring an explicit user choice, or auto-picking based on
worker availability) and raises a clear, API-friendly exception when the
user explicitly asked for an engine that isn't available.

The ``*_params`` builders return the ``Job.parameters`` dict to hand to the
normal job queue — they do NOT create jobs or touch the DB; the API layer
(``backend/api/character_studio.py``) does that, exactly like Phase 1's
``generate_shots``.
"""
from __future__ import annotations

from typing import Any, Optional

# Task LoRA filenames (already vendored on the VNCCS-capable worker; see
# docs/CHARACTER_STUDIO.md P2 section and CLAUDE.md "already done" note).
POSE_LORA = "VNCCS_QIE2511_PoseStudio_ART_V5.9.5.safetensors"
CLOTHES_LORA = "VNCCS_QIE2511_ClothesCore-RC3.7.safetensors"
EMOTION_LORA = "VNCCS_QIE2511_EmotionCore-RC1.safetensors"

# Appended to edit instructions to fight identity drift (Klein especially
# blends colors/features across reference images — e.g. mismatched eye
# colors). Harmless for Qwen, meaningfully steadier for Klein.
IDENTITY_LOCK = (
    " Preserve the character's exact identity: same facial features and "
    "proportions, BOTH eyes the SAME color, same hairstyle and hair color, "
    "same skin tone. Do not alter or drift these."
)

# The EXACT system instruction VNCCS's character_generator feeds to
# VNCCS_QWEN_Encoder for pose/emotion/clothes edits. VNCCS drives the actual
# transformation via the task LoRA (PoseStudio/EmotionCore/ClothesCore) +
# latent_image_index + minimal `prompt`; the instruction stays generic. Our
# earlier bespoke/backwards instructions fought the LoRA — this matches VNCCS.
VNCCS_QIE_INSTRUCTION = (
    "Describe the character and their key features (body shape, physical "
    "characteristics, clothing, items, accessories). Then explain how the "
    "user's text instruction should alter or modify the character. Generate a "
    "new image that meets the user's requirements while maintaining consistency "
    "with the original character where appropriate."
)


class EngineUnavailableError(Exception):
    """Raised when the caller explicitly requested an engine that has no
    available worker.  The API layer turns this into HTTP 409."""

    def __init__(self, requested: str, message: str):
        self.requested = requested
        super().__init__(message)


def _vnccs_worker_online(comfy_dispatcher) -> bool:
    if comfy_dispatcher is None:
        return False
    try:
        worker = comfy_dispatcher.select_worker({"vnccs"}, set(), exclude_runpod=True)
        return worker is not None
    except Exception:
        return False


def resolve_engine(op: str, requested: str, comfy_dispatcher) -> str:
    """Resolve 'auto'|'qwen'|'klein' to a concrete engine ('qwen' or 'klein').

    Args:
        op: stage name for error messages (e.g. "pose", "costume", "emotion").
        requested: user's engine preference.
        comfy_dispatcher: ``app.state.comfy_dispatcher`` (a ``ComfyDispatcher``
            instance) — used to check whether a healthy worker currently
            advertises the ``vnccs`` capability. ``None`` is treated as
            "no workers configured" (never crashes; just falls back / raises
            the same 409-style error).

    Returns:
        "qwen" or "klein".

    Raises:
        EngineUnavailableError: requested == "qwen" but no vnccs worker is
            online. The API layer should catch this and respond 409.
    """
    requested = (requested or "auto").strip().lower()
    online = _vnccs_worker_online(comfy_dispatcher)

    if requested == "qwen":
        if not online:
            raise EngineUnavailableError(
                "qwen",
                f"Requested engine 'qwen' unavailable for {op}: no worker advertises the "
                "vnccs capability. Use engine='klein' or start a VNCCS-capable ComfyUI "
                "worker (one exposing the VNCCS_QWEN_Encoder node).",
            )
        return "qwen"

    if requested == "klein":
        return "klein"

    if requested == "facedetailer":
        # Third emotion engine (VNCCS's exact FaceDetailer mechanism) — needs a
        # worker with BOTH Impact-Pack ("impact") and the VNCCS/QIE models.
        def _cap_online(cap: str) -> bool:
            try:
                return comfy_dispatcher is not None and (
                    comfy_dispatcher.select_worker({cap}, set(), exclude_runpod=True) is not None)
            except Exception:
                return False
        if not (_cap_online("impact") and online):
            raise EngineUnavailableError(
                "facedetailer",
                f"Requested engine 'facedetailer' unavailable for {op}: needs a worker with "
                "Impact-Pack (FaceDetailer node) AND the VNCCS/QIE models. Use 'qwen' or 'klein', "
                "or install ComfyUI-Impact-Pack + Impact-Subpack on the VNCCS worker.",
            )
        return "facedetailer"

    # auto
    if op == "emotion" and online:
        # VNCCS's real emotion mechanism is FaceDetailer (face-crop re-render);
        # prefer it when the worker also has Impact-Pack, else fall back to the
        # in-place QIE edit ('qwen').
        try:
            if comfy_dispatcher is not None and comfy_dispatcher.select_worker(
                    {"impact"}, set(), exclude_runpod=True) is not None:
                return "facedetailer"
        except Exception:
            pass
    return "qwen" if online else "klein"


# ── Pose ────────────────────────────────────────────────────────────────────
def pose_edit_params(engine: str, *, pose_asset_id: str, identity_asset_id: str,
                      prompt: str = "", seed: int = 0,
                      target_size: int = 1024,
                      width: int = 0, height: int = 0,
                      pose_lora: str = "", pose_lora_strength: float = 0.9) -> dict[str, Any]:
    """Build Job.parameters for a pose-conditioned render.

    qwen: studio_qie_edit, image1=pose skeleton (control/canvas),
          image2=identity, latent_image_index=1 (output follows image1 —
          the VNCCS pose recipe), Pose Core LoRA.
    klein: klein_2ref, identity FIRST then the pose skeleton image, with a
           composed instruction describing the pose-transfer edit.
    """
    base_instruction = (
        "Using the character in Image 1, render them in exactly the pose shown "
        "by the stick-figure skeleton in Image 2: match the joint positions, "
        "limb angles and body orientation of the skeleton precisely. Keep the "
        "character's face, hair, outfit and art style unchanged. Use a plain "
        "neutral background."
    )
    if prompt:
        base_instruction += f" Additional direction: {prompt.strip()}"
    base_instruction += IDENTITY_LOCK

    if engine == "qwen":
        # VNCCS pose recipe: pose transfer is driven by the PoseStudio LoRA +
        # image1=skeleton + latent_image_index=1 + the GENERIC instruction. The
        # prompt is minimal (VNCCS passes only the character/background text) —
        # over-describing the pose fights the LoRA.
        _pose_prompt = "Change background to solid white color"
        if prompt.strip():
            _pose_prompt = f"{prompt.strip()}, Change background to solid white color"
        return {
            "workflow_type": "studio_qie_edit",
            "image1_asset_id": pose_asset_id,     # pose skeleton (control) → output canvas
            "image2_asset_id": identity_asset_id,  # identity
            "prompt": _pose_prompt,
            "instruction": VNCCS_QIE_INSTRUCTION,
            "task_lora": POSE_LORA,
            "task_lora_strength": 1.0,
            "target_size": target_size,
            "latent_image_index": 1,
            "seed": seed,
        }
    # klein + RefControl Pose LoRA (real pose transfer): image 1 = pose
    # skeleton (control), image 2 = identity reference. Trigger phrase per the
    # LoRA card. This is the path that actually transfers pose on Klein.
    if pose_lora:
        rc_prompt = "apply pose from image 1 with reference from image 2." + IDENTITY_LOCK
        if prompt:
            rc_prompt += f" Additional direction: {prompt.strip()}"
        return {
            "workflow_type": "klein_2ref",
            "reference_asset_ids": [pose_asset_id, identity_asset_id],
            "prompt": rc_prompt,
            "width": width or target_size,
            "height": height or target_size,
            "pose_lora": pose_lora,
            "pose_lora_strength": pose_lora_strength,
            "seed": seed,
        }
    # klein without the LoRA — identity first (slot 1), pose skeleton second
    # (slot 2). Weak fallback path (no RefControl Pose LoRA configured), so this mostly reproduces the
    # base pose. Kept as a fallback when the LoRA is disabled.
    return {
        "workflow_type": "klein_2ref",
        "reference_asset_ids": [identity_asset_id, pose_asset_id],
        "prompt": base_instruction,
        "width": width or target_size,
        "height": height or target_size,
        "seed": seed,
    }


# ── Costume ─────────────────────────────────────────────────────────────────
def costume_params(engine: str, *, identity_asset_id: str, description: str = "",
                    clothing_ref_asset_id: Optional[str] = None,
                    seed: int = 0, target_size: int = 1024,
                    width: int = 0, height: int = 0) -> dict[str, Any]:
    """Build Job.parameters for a costume/outfit render.

    qwen: studio_qie_edit, ClothesCore LoRA, latent_image_index=2 (edit the
          identity image in place — no separate control image needed beyond
          the identity itself, so image1==image2).
    klein: no dedicated "klein_1ref" builder exists for this in the spec's
           original wording, but the dispatcher DOES support klein_1ref
           (see backend/services/jobs/dispatcher.py klein_map) — we use it
           directly with a single identity reference.
    """
    desc = (description or "").strip()

    # Clothing-from-reference: dress the character (image 1) in the garment
    # shown in a reference image (image 2), via the edit models. Lets one piece
    # of clothing be applied across characters, or a garment generated elsewhere
    # be swapped in.
    if clothing_ref_asset_id:
        ref_prompt = (
            "Dress the character in image 1 with the exact clothing/outfit shown in image 2. "
            "Reproduce the garment's design, colors, fabric and details from image 2 precisely. "
            "Keep image 1's face, hair, identity, body shape, pose and plain background unchanged."
            + (f" {desc}." if desc else "") + IDENTITY_LOCK
        )
        if engine == "qwen":
            return {
                "workflow_type": "studio_qie_edit",
                "image1_asset_id": identity_asset_id,        # canvas = the character
                "image2_asset_id": clothing_ref_asset_id,    # the garment reference
                "prompt": ref_prompt, "instruction": VNCCS_QIE_INSTRUCTION,
                "task_lora": CLOTHES_LORA, "task_lora_strength": 1.0,
                "target_size": target_size, "latent_image_index": 1, "seed": seed,
            }
        return {
            "workflow_type": "klein_2ref",
            "reference_asset_ids": [identity_asset_id, clothing_ref_asset_id],
            "prompt": ref_prompt,
            "width": width or target_size, "height": height or target_size,
            "seed": seed,
        }

    desc = desc or "a simple casual outfit"
    # VNCCS clothes recipe: prompt = "Dress the character:\n{outfit}\nsolid <bg>
    # background"; transformation driven by ClothesCore LoRA + generic
    # instruction + latent_image_index=1 (VNCCS never uses index 2 for clothes).
    prompt = f"Dress the character:\n{desc}\nsolid white background"
    if engine == "qwen":
        return {
            "workflow_type": "studio_qie_edit",
            "image1_asset_id": identity_asset_id,
            "image2_asset_id": identity_asset_id,
            "prompt": prompt,
            "instruction": VNCCS_QIE_INSTRUCTION,
            "task_lora": CLOTHES_LORA,
            "task_lora_strength": 1.0,
            "target_size": target_size,
            "latent_image_index": 1,
            "seed": seed,
        }
    # klein path: no ClothesCore LoRA, so keep an explicit edit instruction.
    prompt = (f"Dress the character: {desc}. Keep the face, hair, and identity "
              "unchanged. Keep the same pose and plain background." + IDENTITY_LOCK)
    # klein_1ref: single identity reference. (klein_1ref IS a real dispatcher
    # workflow_type — verified in backend/services/jobs/dispatcher.py; no
    # need for the klein_2ref-with-one-ref workaround the spec anticipated.)
    return {
        "workflow_type": "klein_1ref",
        "reference_asset_ids": [identity_asset_id],
        "prompt": prompt,
        "width": width or target_size,
        "height": height or target_size,
        "seed": seed,
    }


# ── Emotion ─────────────────────────────────────────────────────────────────
def emotion_params(engine: str, *, identity_asset_id: str, natural_prompt: str,
                    face_masked_asset_id: Optional[str] = None,
                    seed: int = 0, target_size: int = 1024) -> dict[str, Any]:
    """Build Job.parameters for an emotion/expression render.

    qwen: studio_qie_edit, EmotionCore LoRA, latent_image_index=2 (in-place,
          low-change edit — image1==image2==identity).
    klein: klein_inpaint using a face-masked RGBA (region-to-edit transparent
           in alpha) built by ``faces.build_face_masked_rgba`` + the
           emotion's natural_prompt from emotions.json. Requires
           face_masked_asset_id (raises ValueError if engine=='klein' and it
           is missing — the API layer is responsible for building/uploading
           the masked asset before calling this).
    """
    prompt = (
        f"{natural_prompt.strip()}\nOnly the facial expression changes; keep "
        "identity, hair, outfit and pose unchanged."
    )
    if engine == "facedetailer":
        # VNCCS's exact emotion mechanism: face-crop re-render (YOLO bbox +
        # SAM) at guide 1536 with EmotionCore — best for small faces in
        # full-body sprites.  Worker needs "impact" + "vnccs" caps + models.
        return {
            "workflow_type": "studio_facedetailer",
            "image_asset_id": identity_asset_id,
            "prompt": prompt,
            "face_denoise": 0.55,
            "seed": seed,
        }
    if engine == "qwen":
        return {
            "workflow_type": "studio_qie_edit",
            "image1_asset_id": identity_asset_id,
            "image2_asset_id": identity_asset_id,
            "prompt": prompt,
            "instruction": VNCCS_QIE_INSTRUCTION,
            "task_lora": EMOTION_LORA,
            "task_lora_strength": 1.0,
            "target_size": target_size,
            "latent_image_index": 2,
            "seed": seed,
        }
    if not face_masked_asset_id:
        raise ValueError(
            "emotion_params(engine='klein') requires face_masked_asset_id — "
            "build it with faces.build_face_masked_rgba() and register it as "
            "an Asset first."
        )
    return {
        "workflow_type": "klein_inpaint",
        "source_masked_asset_id": face_masked_asset_id,
        "reference_asset_id": identity_asset_id,
        "prompt": prompt,
        "seed": seed,
    }


# ── Cutout (background removal) ──────────────────────────────────────────────
def cutout_params(*, image_asset_id: str, sensitivity: float = 0.85) -> dict[str, Any]:
    """studio_rmbg2 params — this stage has no Klein equivalent; when no
    vnccs worker is online the API layer should run ``cutout.cutout_cpu``
    synchronously instead of dispatching a job (see character_studio.py
    ``/process``)."""
    return {
        "workflow_type": "studio_rmbg2",
        "image_asset_id": image_asset_id,
        "sensitivity": sensitivity,
    }


# ── Upscale ───────────────────────────────────────────────────────────────
def upscale_params(*, image_asset_id: str, upscale_model: Optional[str] = None,
                   mode: str = "gan", resolution: int = 2048) -> dict[str, Any]:
    """mode="seedvr2" → premium SeedVR2 upscale (worker needs the "seedvr2"
    cap, auto-detected); mode="gan" (default) → standard model upscale on any
    "upscale"-capable worker."""
    if mode == "seedvr2":
        return {
            "workflow_type": "studio_seedvr2",
            "image_asset_id": image_asset_id,
            "resolution": int(resolution),
        }
    params: dict[str, Any] = {
        "workflow_type": "studio_upscale",
        "image_asset_id": image_asset_id,
    }
    if upscale_model:
        params["upscale_model"] = upscale_model
    return params
