"""
Default Workflow Registration

Registers built-in ComfyUI workflows (Klein, LTX) into the database on startup.
"""

import json
import logging
from pathlib import Path
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from backend.database.models import WorkflowConfig
from backend.services.comfyui.introspection import introspect_workflow

logger = logging.getLogger(__name__)


async def register_default_workflows(session: AsyncSession) -> None:
    """
    Check if default workflows already exist in the DB.
    If not, load each JSON from /workflows/ directory, run introspection,
    and create WorkflowConfig records with is_default=True.
    """
    # Check if any default workflows already exist
    stmt = select(WorkflowConfig).where(WorkflowConfig.is_default == True)
    result = await session.execute(stmt)
    existing_defaults = result.scalars().all()

    if existing_defaults:
        logger.info(f"Default workflows already registered ({len(existing_defaults)} found)")
        return

    # Locate workflows directory
    workflows_dir = Path(__file__).parent.parent.parent.parent / "workflows"
    if not workflows_dir.exists():
        logger.warning(f"Workflows directory not found at {workflows_dir}")
        return

    # Define default workflows to register
    defaults = [
        {
            "filename": "KLEIN_EDIT_ULTRA_WORKFLOW_1REF.json",
            "name": "Klein 9B - 1 Reference",
            "workflow_type": "image",
            "description": "FLUX.2 Klein 9B with 1 reference image",
        },
        {
            "filename": "KLEIN_EDIT_ULTRA_WORKFLOW_2REF.json",
            "name": "Klein 9B - 2 References",
            "workflow_type": "image",
            "description": "FLUX.2 Klein 9B with 2 reference images",
        },
        {
            "filename": "KLEIN_EDIT_ULTRA_WORKFLOW_3REF.json",
            "name": "Klein 9B - 3 References",
            "workflow_type": "image",
            "description": "FLUX.2 Klein 9B with 3 reference images",
        },
        {
            "filename": "KLEIN_EDIT_ULTRA_WORKFLOW_4REF.json",
            "name": "Klein 9B - 4 References",
            "workflow_type": "image",
            "description": "FLUX.2 Klein 9B with 4 reference images",
        },
        {
            "filename": "KLEIN_EDIT_ULTRA_WORKFLOW_Text2Image.json",
            "name": "Klein 9B - Text to Image",
            "workflow_type": "image",
            "description": "FLUX.2 Klein 9B text to image generation",
        },
        {
            "filename": "LTX-2-3_ULTRA_WORKFLOW_FF_LF.json",
            "name": "LTX 2.3 - First/Last Frame",
            "workflow_type": "video",
            "description": "LTX 2.3 video generation with first and last frame guidance",
        },
        {
            "filename": "LTX-2-3_ULTRA_WORKFLOW_Image2Video.json",
            "name": "LTX 2.3 - Image to Video",
            "workflow_type": "video",
            "description": "LTX 2.3 image-to-video generation",
        },
        {
            "filename": "LTX-2-3_V2V_EXTEND.json",
            "name": "LTX 2.3 - V2V Extend (Legacy)",
            "workflow_type": "video",
            "description": "LTX 2.3 V2V extending using single-frame LTXVAddLatentGuide (legacy — use V2V Extend v2 for better transitions)",
        },
        {
            "filename": "LTX-2-3_V2V_EXTEND_v2.json",
            "name": "LTX 2.3 - V2V Extend v2",
            "workflow_type": "video",
            "description": "LTX 2.3 V2V extending using LTXVExtendSampler with multi-frame overlap and linear alpha blending for seamless scene transitions",
        },
        {
            "filename": "LTX-2-3_TRANSITION_LORA.json",
            "name": "LTX 2.3 - Transition LoRA",
            "workflow_type": "video",
            "description": "LTX 2.3 AI-generated transition clips using Transition LoRA — creates smooth morphing transitions between scene A and scene B frames",
        },
        {
            "filename": "LTX-2-3_AV_NATIVE.json",
            "name": "LTX 2.3 - AV Native (model generates audio)",
            "workflow_type": "video",
            "description": "LTX 2.3 image-to-video that generates its own audio (speech / SFX / ambient) in the same pass — no project audio is sent in. The model's audio comes back baked into the MP4 and is also extracted as a sidecar WAV so the mixer can route it to its own channel.",
        },
    ]

    # Krea 2 Turbo — registered ONLY once the user has dropped in their tested
    # workflow JSON (KREA2_TURBO_T2I.json).  Until then it's simply absent and
    # the dispatcher falls back to Z-Image, so no startup warning is emitted.
    if (workflows_dir / "KREA2_TURBO_T2I.json").exists():
        defaults.append({
            "filename": "KREA2_TURBO_T2I.json",
            "name": "Krea 2 Turbo - Text to Image",
            "workflow_type": "krea2_t2i",
            "description": "Krea 2 Turbo single-pass text-to-image (first-pass generator, no reference images, no negative prompt).",
        })

    # Register each default workflow
    registered_count = 0
    for default_config in defaults:
        workflow_path = workflows_dir / default_config["filename"]

        if not workflow_path.exists():
            logger.warning(f"Workflow file not found: {workflow_path}")
            continue

        try:
            # Load workflow JSON
            with open(workflow_path, "r") as f:
                workflow_json = json.load(f)

            # Introspect to auto-detect fields
            introspection = introspect_workflow(workflow_json)
            field_mappings = introspection["fields"]

            # Create WorkflowConfig record
            workflow_config = WorkflowConfig(
                id=uuid4(),
                name=default_config["name"],
                workflow_type=default_config["workflow_type"],
                description=default_config["description"],
                is_default=True,
                server_url=None,  # Available to all servers
                workflow_json=workflow_json,
                field_mappings=field_mappings,
            )

            session.add(workflow_config)
            registered_count += 1
            logger.info(f"Registered default workflow: {default_config['name']} ({len(field_mappings)} fields detected)")

        except Exception as e:
            logger.error(f"Failed to register {default_config['filename']}: {e}")
            continue

    if registered_count > 0:
        await session.commit()
        logger.info(f"Registered {registered_count} default workflows")
