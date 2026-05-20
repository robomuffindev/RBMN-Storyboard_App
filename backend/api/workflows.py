"""Workflow management endpoints for RBMN Storyboard App."""
import json
import logging
from datetime import datetime
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from backend.database import get_session
from backend.database.models import WorkflowConfig, WorkflowFieldType, WorkflowType
from backend.services.comfyui.introspection import introspect_workflow

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/workflows", tags=["workflows"])


# Pydantic models for request/response
class DetectedField(BaseModel):
    """Detected workflow field."""

    node_title: str
    node_id: str
    class_type: str
    input_name: str
    field_type: str
    current_value: Optional[str] = None
    description: str
    confidence: float


class IntrospectionResult(BaseModel):
    """Result of workflow introspection."""

    detected_type: str  # "image" or "video"
    node_count: int
    fields: list[DetectedField]


class FieldMapping(BaseModel):
    """Field mapping configuration."""

    node_title: str
    input_name: str
    field_type: str
    description: str


class WorkflowConfigResponse(BaseModel):
    """Response model for a workflow configuration."""

    id: UUID
    name: str
    workflow_type: str
    description: str
    is_default: bool
    server_url: Optional[str] = None
    field_mappings: list[FieldMapping]
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class WorkflowConfigListResponse(BaseModel):
    """Response model for listing workflow configurations."""

    id: UUID
    name: str
    workflow_type: str
    description: str
    is_default: bool
    server_url: Optional[str] = None
    field_count: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


@router.get(
    "",
    response_model=list[WorkflowConfigListResponse],
    summary="List all workflow configurations",
)
async def list_workflows(
    workflow_type: Optional[str] = None,
    server_url: Optional[str] = None,
    session: AsyncSession = Depends(get_session),
) -> list[WorkflowConfigListResponse]:
    """
    List all workflow configurations.

    Can be filtered by workflow_type (image/video) and server_url.

    Args:
        workflow_type: Filter by "image" or "video" (optional)
        server_url: Filter by server URL (optional)
        session: Database session

    Returns:
        List of workflow configurations
    """
    stmt = select(WorkflowConfig)

    if workflow_type:
        stmt = stmt.where(WorkflowConfig.workflow_type == workflow_type)

    if server_url is not None:
        # server_url=None means available to all servers
        stmt = stmt.where(
            (WorkflowConfig.server_url == None) | (WorkflowConfig.server_url == server_url)
        )

    result = await session.execute(stmt)
    configs = result.scalars().all()

    return [
        WorkflowConfigListResponse(
            id=config.id,
            name=config.name,
            workflow_type=config.workflow_type,
            description=config.description,
            is_default=config.is_default,
            server_url=config.server_url,
            field_count=len(config.field_mappings),
            created_at=config.created_at,
            updated_at=config.updated_at,
        )
        for config in configs
    ]


@router.get(
    "/{workflow_id}",
    response_model=WorkflowConfigResponse,
    summary="Get workflow configuration details",
)
async def get_workflow(
    workflow_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> WorkflowConfigResponse:
    """
    Get detailed workflow configuration including field mappings.

    Args:
        workflow_id: Workflow configuration ID
        session: Database session

    Returns:
        Workflow configuration with field mappings

    Raises:
        HTTPException: If workflow not found
    """
    config = await session.get(WorkflowConfig, workflow_id)

    if not config:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Workflow {workflow_id} not found",
        )

    field_mappings = [
        FieldMapping(
            node_title=fm["node_title"],
            input_name=fm["input_name"],
            field_type=fm["field_type"],
            description=fm.get("description", ""),
        )
        for fm in config.field_mappings
    ]

    return WorkflowConfigResponse(
        id=config.id,
        name=config.name,
        workflow_type=config.workflow_type,
        description=config.description,
        is_default=config.is_default,
        server_url=config.server_url,
        field_mappings=field_mappings,
        created_at=config.created_at,
        updated_at=config.updated_at,
    )


@router.post(
    "/upload",
    response_model=WorkflowConfigResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Upload a custom workflow JSON",
)
async def upload_workflow(
    file: UploadFile = File(...),
    name: str = None,
    workflow_type: str = None,
    description: str = "",
    server_url: Optional[str] = None,
    session: AsyncSession = Depends(get_session),
) -> WorkflowConfigResponse:
    """
    Upload a workflow JSON file. Auto-introspects to detect dynamic fields.

    Args:
        file: Workflow JSON file to upload
        name: Name for the workflow (defaults to filename without extension)
        workflow_type: "image" or "video" (auto-detected if not provided)
        description: Description of the workflow
        server_url: Optional server URL to restrict availability
        session: Database session

    Returns:
        Created workflow configuration with detected fields

    Raises:
        HTTPException: If file is invalid JSON or introspection fails
    """
    if not file.filename.endswith(".json"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="File must be a JSON workflow",
        )

    try:
        contents = await file.read()
        workflow_json = json.loads(contents)
    except json.JSONDecodeError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid JSON: {str(e)}",
        )

    # Introspect the workflow
    try:
        introspection = introspect_workflow(workflow_json)
    except Exception as e:
        logger.error(f"Introspection failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Workflow introspection failed: {str(e)}",
        )

    # Determine workflow type
    if not workflow_type:
        workflow_type = introspection.get("detected_type", "image")

    # Generate name if not provided
    if not name:
        name = file.filename.replace(".json", "")

    # Create workflow config
    config = WorkflowConfig(
        name=name,
        workflow_type=workflow_type,
        description=description,
        is_default=False,
        server_url=server_url,
        workflow_json=workflow_json,
        field_mappings=introspection.get("fields", []),
    )

    session.add(config)
    await session.commit()
    await session.refresh(config)

    logger.info(f"Uploaded workflow: {config.id} ({name})")

    field_mappings = [
        FieldMapping(
            node_title=fm["node_title"],
            input_name=fm["input_name"],
            field_type=fm["field_type"],
            description=fm.get("description", ""),
        )
        for fm in config.field_mappings
    ]

    return WorkflowConfigResponse(
        id=config.id,
        name=config.name,
        workflow_type=config.workflow_type,
        description=config.description,
        is_default=config.is_default,
        server_url=config.server_url,
        field_mappings=field_mappings,
        created_at=config.created_at,
        updated_at=config.updated_at,
    )


@router.post(
    "/introspect",
    response_model=IntrospectionResult,
    summary="Introspect a workflow JSON without saving",
)
async def introspect(file: UploadFile = File(...)) -> IntrospectionResult:
    """
    Upload a workflow JSON for introspection without saving it.

    Useful for previewing what fields will be detected before committing.

    Args:
        file: Workflow JSON file to introspect

    Returns:
        Detected fields and workflow type

    Raises:
        HTTPException: If file is invalid JSON
    """
    if not file.filename.endswith(".json"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="File must be a JSON workflow",
        )

    try:
        contents = await file.read()
        workflow_json = json.loads(contents)
    except json.JSONDecodeError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid JSON: {str(e)}",
        )

    try:
        introspection = introspect_workflow(workflow_json)
    except Exception as e:
        logger.error(f"Introspection failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Workflow introspection failed: {str(e)}",
        )

    fields = [
        DetectedField(
            node_title=field["node_title"],
            node_id=field["node_id"],
            class_type=field["class_type"],
            input_name=field["input_name"],
            field_type=field["field_type"],
            current_value=str(field.get("current_value")),
            description=field.get("description", ""),
            confidence=field.get("confidence", 0.0),
        )
        for field in introspection.get("fields", [])
    ]

    return IntrospectionResult(
        detected_type=introspection.get("detected_type", "image"),
        node_count=introspection.get("node_count", 0),
        fields=fields,
    )


@router.put(
    "/{workflow_id}/mappings",
    response_model=WorkflowConfigResponse,
    summary="Update field mappings for a workflow",
)
async def update_workflow_mappings(
    workflow_id: UUID,
    mappings: list[FieldMapping],
    session: AsyncSession = Depends(get_session),
) -> WorkflowConfigResponse:
    """
    Update field mappings for a workflow.

    Allows users to correct or refine auto-detected field mappings.

    Args:
        workflow_id: Workflow configuration ID
        mappings: Updated list of field mappings
        session: Database session

    Returns:
        Updated workflow configuration

    Raises:
        HTTPException: If workflow not found
    """
    config = await session.get(WorkflowConfig, workflow_id)

    if not config:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Workflow {workflow_id} not found",
        )

    # Convert Pydantic models to dicts for storage
    config.field_mappings = [
        {
            "node_title": m.node_title,
            "input_name": m.input_name,
            "field_type": m.field_type,
            "description": m.description,
        }
        for m in mappings
    ]

    session.add(config)
    await session.commit()
    await session.refresh(config)

    logger.info(f"Updated mappings for workflow {workflow_id}")

    field_mappings = [
        FieldMapping(
            node_title=fm["node_title"],
            input_name=fm["input_name"],
            field_type=fm["field_type"],
            description=fm.get("description", ""),
        )
        for fm in config.field_mappings
    ]

    return WorkflowConfigResponse(
        id=config.id,
        name=config.name,
        workflow_type=config.workflow_type,
        description=config.description,
        is_default=config.is_default,
        server_url=config.server_url,
        field_mappings=field_mappings,
        created_at=config.created_at,
        updated_at=config.updated_at,
    )


@router.delete(
    "/{workflow_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a workflow configuration",
)
async def delete_workflow(
    workflow_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> None:
    """
    Delete a custom workflow configuration.

    Cannot delete default workflows.

    Args:
        workflow_id: Workflow configuration ID
        session: Database session

    Raises:
        HTTPException: If workflow not found or is a default workflow
    """
    config = await session.get(WorkflowConfig, workflow_id)

    if not config:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Workflow {workflow_id} not found",
        )

    if config.is_default:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot delete default workflows",
        )

    await session.delete(config)
    await session.commit()

    logger.info(f"Deleted workflow {workflow_id}")
