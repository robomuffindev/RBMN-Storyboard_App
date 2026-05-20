"""
ComfyUI Workflow Introspection Engine

Analyzes ComfyUI workflows to auto-detect which nodes/inputs should be mapped to app variables.
"""

import copy
import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


def introspect_workflow(workflow: dict) -> dict:
    """
    Analyze a ComfyUI workflow and auto-detect fields that should be mapped to app variables.

    Returns:
        {
            "detected_type": "image" or "video",
            "node_count": int,
            "fields": [
                {
                    "node_title": str,
                    "node_id": str,
                    "class_type": str,
                    "input_name": str,
                    "field_type": str,  # prompt, image, width, height, seed, audio, etc.
                    "current_value": any,
                    "description": str,
                    "confidence": float  # 0.0-1.0 how confident we are in the detection
                },
                ...
            ]
        }
    """
    detected_type = "image"
    fields = []

    # Check for video nodes to detect type
    for node_id, node in workflow.items():
        if isinstance(node, dict):
            class_type = node.get("class_type", "")
            if "LTXV" in class_type or "EmptyLTXVLatentVideo" in class_type or "VHS_VideoCombine" in class_type:
                detected_type = "video"
                break

    # Iterate through all nodes and detect dynamic fields
    for node_id, node in workflow.items():
        if not isinstance(node, dict):
            continue

        class_type = node.get("class_type", "")
        title = node.get("_meta", {}).get("title", "")
        inputs = node.get("inputs", {})

        # Detect prompt fields
        if class_type == "CLIPTextEncode":
            if "text" in inputs:
                field_type = "prompt"
                if "negative" in title.lower():
                    field_type = "negative_prompt"

                fields.append({
                    "node_title": title,
                    "node_id": node_id,
                    "class_type": class_type,
                    "input_name": "text",
                    "field_type": field_type,
                    "current_value": inputs.get("text"),
                    "description": f"Text prompt for {title}",
                    "confidence": 1.0 if title else 0.8,
                })

        # Detect image inputs (LoadImage)
        elif class_type == "LoadImage":
            if "image" in inputs:
                field_type = "image"
                if "first" in title.lower():
                    field_type = "first_frame"
                elif "last" in title.lower():
                    field_type = "last_frame"
                elif "reference" in title.lower():
                    field_type = "image"

                fields.append({
                    "node_title": title,
                    "node_id": node_id,
                    "class_type": class_type,
                    "input_name": "image",
                    "field_type": field_type,
                    "current_value": inputs.get("image"),
                    "description": f"Image path for {title}",
                    "confidence": 1.0,
                })

        # Detect dimension inputs (width/height)
        elif class_type in ("PrimitiveInt", "INTConstant"):
            if "value" in inputs:
                current_val = inputs.get("value")

                # Check title first
                if "width" in title.lower():
                    confidence = 1.0
                    fields.append({
                        "node_title": title,
                        "node_id": node_id,
                        "class_type": class_type,
                        "input_name": "value",
                        "field_type": "width",
                        "current_value": current_val,
                        "description": f"Image width (pixels)",
                        "confidence": confidence,
                    })

                elif "height" in title.lower():
                    confidence = 1.0
                    fields.append({
                        "node_title": title,
                        "node_id": node_id,
                        "class_type": class_type,
                        "input_name": "value",
                        "field_type": "height",
                        "current_value": current_val,
                        "description": f"Image height (pixels)",
                        "confidence": confidence,
                    })

                elif "duration" in title.lower():
                    fields.append({
                        "node_title": title,
                        "node_id": node_id,
                        "class_type": class_type,
                        "input_name": "value",
                        "field_type": "duration",
                        "current_value": current_val,
                        "description": f"Video duration in seconds",
                        "confidence": 1.0,
                    })

                elif "framerate" in title.lower() or "fps" in title.lower():
                    fields.append({
                        "node_title": title,
                        "node_id": node_id,
                        "class_type": class_type,
                        "input_name": "value",
                        "field_type": "framerate",
                        "current_value": current_val,
                        "description": f"Video framerate (fps)",
                        "confidence": 1.0,
                    })

                # Heuristic: if value is between 256-8192, likely a dimension
                elif isinstance(current_val, int) and 256 <= current_val <= 8192:
                    # Only add if title doesn't suggest something else
                    if "duration" not in title.lower() and "framerate" not in title.lower() and "fps" not in title.lower():
                        fields.append({
                            "node_title": title,
                            "node_id": node_id,
                            "class_type": class_type,
                            "input_name": "value",
                            "field_type": "other",
                            "current_value": current_val,
                            "description": f"Value for {title}",
                            "confidence": 0.5,
                        })

        # Detect seed inputs (RandomNoise)
        elif class_type == "RandomNoise":
            if "noise_seed" in inputs:
                fields.append({
                    "node_title": title,
                    "node_id": node_id,
                    "class_type": class_type,
                    "input_name": "noise_seed",
                    "field_type": "seed",
                    "current_value": inputs.get("noise_seed"),
                    "description": "Random noise seed",
                    "confidence": 1.0,
                })

        # Detect audio inputs (LoadAudio)
        elif class_type == "LoadAudio":
            if "audio" in inputs:
                fields.append({
                    "node_title": title,
                    "node_id": node_id,
                    "class_type": class_type,
                    "input_name": "audio",
                    "field_type": "audio",
                    "current_value": inputs.get("audio"),
                    "description": "Audio file path",
                    "confidence": 1.0,
                })

    logger.info(f"Introspected workflow: detected_type={detected_type}, found {len(fields)} dynamic fields")

    return {
        "detected_type": detected_type,
        "node_count": len(workflow),
        "fields": fields,
    }


def apply_field_mappings(workflow: dict, field_mappings: list[dict], values: dict) -> dict:
    """
    Apply dynamic values to a workflow using stored field mappings.

    Args:
        workflow: The ComfyUI workflow JSON (will be deep-copied)
        field_mappings: List of mapping dicts from WorkflowConfig.field_mappings
        values: Dict of field_type -> value, e.g. {"prompt": "a cat", "width": 1024, "seed": 12345}

    Returns:
        Mutated workflow copy ready for submission
    """
    # Deep copy to avoid mutating original
    workflow = copy.deepcopy(workflow)

    from backend.services.comfyui.workflow import find_node_by_title, set_node_input

    for mapping in field_mappings:
        node_title = mapping.get("node_title")
        input_name = mapping.get("input_name")
        field_type = mapping.get("field_type")

        # Skip if no value provided for this field type
        if field_type not in values or values[field_type] is None:
            logger.debug(f"Skipping field {field_type} (no value provided)")
            continue

        value = values[field_type]

        try:
            # Use existing workflow mutation functions
            set_node_input(workflow, node_title, input_name, value)
            logger.debug(f"Applied mapping: {field_type} -> {node_title}.{input_name} = {value}")
        except ValueError as e:
            logger.warning(f"Failed to apply mapping {field_type}: {e}")
            raise

    return workflow
