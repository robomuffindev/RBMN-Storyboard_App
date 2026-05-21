"""SQLModel database models for RBMN Storyboard App."""
import json
from datetime import datetime
try:
    from enum import StrEnum
except ImportError:  # Python 3.10
    from enum import Enum

    class StrEnum(str, Enum):
        """Backport of StrEnum for Python <3.11."""
        pass
from typing import Any, Optional
from uuid import UUID, uuid4

from sqlalchemy import JSON, Column
from sqlmodel import Field, Relationship, SQLModel


class ProjectMode(StrEnum):
    """Project mode enumeration."""

    MUSIC_VIDEO = "music_video"
    NARRATION_IMAGES = "narration_images"
    NARRATION_VIDEO = "narration_video"


class AssetType(StrEnum):
    """Asset type enumeration."""

    CHARACTER = "character"
    CLOTHING = "clothing"
    ITEM = "item"
    PLACE = "place"
    MUSIC = "music"
    NARRATION = "narration"
    GENERATED_IMAGE = "generated_image"
    GENERATED_VIDEO = "generated_video"
    REFERENCE = "reference"


class JobType(StrEnum):
    """Job type enumeration."""

    IMAGE = "image"
    VIDEO = "video"


class JobStatus(StrEnum):
    """Job status enumeration."""

    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    RETRYING = "retrying"
    CANCELLED = "cancelled"


class SongSectionLabel(StrEnum):
    """Song section label enumeration."""

    INTRO = "intro"
    VERSE = "verse"
    CHORUS = "chorus"
    BRIDGE = "bridge"
    OUTRO = "outro"
    OTHER = "other"


class Project(SQLModel, table=True):
    """Project database model."""

    __tablename__ = "projects"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    name: str = Field(index=True)
    mode: ProjectMode = Field(default=ProjectMode.MUSIC_VIDEO)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    settings: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    schema_version: int = Field(default=1)

    # Relationships
    scenes: list["Scene"] = Relationship(back_populates="project", cascade_delete=True)
    song_sections: list["SongSection"] = Relationship(
        back_populates="project", cascade_delete=True
    )
    assets: list["Asset"] = Relationship(
        back_populates="project", cascade_delete=True
    )
    generation_history: list["GenerationHistory"] = Relationship(
        back_populates="project", cascade_delete=True
    )
    jobs: list["Job"] = Relationship(back_populates="project", cascade_delete=True)
    lyrics: Optional["Lyrics"] = Relationship(
        back_populates="project", cascade_delete=True
    )


class Scene(SQLModel, table=True):
    """Scene database model."""

    __tablename__ = "scenes"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    project_id: UUID = Field(foreign_key="projects.id")
    order_index: int
    name: str
    start_time: float
    end_time: float
    prompt: str
    negative_prompt: str = Field(default="")
    parameters: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    workflow_snapshot: dict[str, Any] = Field(
        default_factory=dict, sa_column=Column(JSON)
    )

    # Relationships
    project: Project = Relationship(back_populates="scenes")
    timeline_positions: list["TimelinePosition"] = Relationship(
        back_populates="scene", cascade_delete=True
    )
    stem_selection: Optional["StemSelection"] = Relationship(
        back_populates="scene", cascade_delete=True
    )
    generation_history: list["GenerationHistory"] = Relationship(
        back_populates="scene", cascade_delete=True
    )
    jobs: list["Job"] = Relationship(back_populates="scene", cascade_delete=True)


class SongSection(SQLModel, table=True):
    """Song section metadata model."""

    __tablename__ = "song_sections"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    project_id: UUID = Field(foreign_key="projects.id")
    label: SongSectionLabel = Field(default=SongSectionLabel.OTHER)
    start_time: float
    end_time: float
    color: str = Field(default="#FFFFFF")

    # Relationships
    project: Project = Relationship(back_populates="song_sections")


class Asset(SQLModel, table=True):
    """Asset (file) database model."""

    __tablename__ = "assets"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    project_id: UUID = Field(foreign_key="projects.id")
    filename: str
    rel_path: str = Field(index=True)
    asset_type: AssetType
    sha256: str = Field(index=True)
    duration_sec: Optional[float] = None
    width: Optional[int] = None
    height: Optional[int] = None
    file_size: int
    meta: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=datetime.utcnow)

    # Relationships
    project: Project = Relationship(back_populates="assets")
    timeline_positions: list["TimelinePosition"] = Relationship(
        back_populates="asset", cascade_delete=True
    )


class TimelinePosition(SQLModel, table=True):
    """Timeline position/track model."""

    __tablename__ = "timeline_positions"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    scene_id: UUID = Field(foreign_key="scenes.id")
    asset_id: UUID = Field(foreign_key="assets.id")
    track: int
    start_sec: float
    end_sec: float
    gain_db: float = Field(default=0.0)
    effects: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))

    # Relationships
    scene: Scene = Relationship(back_populates="timeline_positions")
    asset: Asset = Relationship(back_populates="timeline_positions")


class StemSelection(SQLModel, table=True):
    """Stem selection (vocal isolation) per scene."""

    __tablename__ = "stem_selections"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    scene_id: UUID = Field(foreign_key="scenes.id", unique=True)
    vocals: bool = Field(default=True)
    drums: bool = Field(default=True)
    bass: bool = Field(default=True)
    other: bool = Field(default=True)

    # Relationships
    scene: Scene = Relationship(back_populates="stem_selection")


class GenerationHistory(SQLModel, table=True):
    """Generation history (append-only log)."""

    __tablename__ = "generation_history"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    project_id: UUID = Field(foreign_key="projects.id")
    scene_id: Optional[UUID] = Field(foreign_key="scenes.id", default=None)
    job_type: JobType
    prompt_id: str
    workflow_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    status: str
    parameters: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    output_path: Optional[str] = None
    error_message: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    completed_at: Optional[datetime] = None

    # Relationships
    project: Project = Relationship(back_populates="generation_history")
    scene: Optional[Scene] = Relationship(back_populates="generation_history")


class Job(SQLModel, table=True):
    """Async job queue model."""

    __tablename__ = "jobs"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    project_id: UUID = Field(foreign_key="projects.id")
    scene_id: Optional[UUID] = Field(foreign_key="scenes.id", default=None)
    job_type: JobType
    status: JobStatus = Field(default=JobStatus.PENDING, index=True)
    priority: int = Field(default=0)
    worker_url: Optional[str] = None
    prompt_id: Optional[str] = None
    parameters: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    result: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    error: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    retry_count: int = Field(default=0)

    # Relationships
    project: Project = Relationship(back_populates="jobs")
    scene: Optional[Scene] = Relationship(back_populates="jobs")


class Lyrics(SQLModel, table=True):
    """Transcribed lyrics with word-level timestamps for a project."""

    __tablename__ = "lyrics"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    project_id: UUID = Field(foreign_key="projects.id", unique=True)
    full_text: str = Field(default="")
    initial_text: str = Field(default="")  # User-provided lyrics/script input
    words: list[dict[str, Any]] = Field(
        default_factory=list, sa_column=Column(JSON)
    )  # List of {word, start, end} dicts
    language: str = Field(default="en")
    created_at: datetime = Field(default_factory=datetime.utcnow)

    # Relationships
    project: Project = Relationship(back_populates="lyrics")


class AppSettings(SQLModel, table=True):
    """Application-wide settings (singleton)."""

    __tablename__ = "app_settings"

    id: int = Field(primary_key=True, default=1)
    comfyui_urls: list[str] = Field(
        default_factory=list, sa_column=Column(JSON)
    )
    # Per-server capability overrides: {url: {image: bool, video: bool}}
    comfyui_server_caps: Optional[dict] = Field(default=None, sa_column=Column(JSON))
    whisper_mode: str = Field(default="local")  # "local", "remote", "comfyui"
    whisper_remote_url: Optional[str] = None
    whisper_comfyui_url: Optional[str] = None  # ComfyUI server URL for Whisper workflow
    whisper_model: str = Field(default="large-v2")
    whisper_language: str = Field(default="English")  # Language for Whisper transcription
    openai_api_key: Optional[str] = None
    openai_model: Optional[str] = None
    anthropic_api_key: Optional[str] = None
    anthropic_model: Optional[str] = None
    gemini_api_key: Optional[str] = None
    gemini_model: Optional[str] = None
    image_model_type: str = Field(default="flux2_klein_dev_9b")
    video_model_type: str = Field(default="ltx_2.3")
    # LTX GGUF model variant — selectable in Settings to trade quality vs VRAM
    ltx_model_gguf: str = Field(default="ltx-2.3-22b-dev-Q8_0.gguf")
    # System prompt overrides — stored as JSON dicts keyed by model name
    # e.g. {"flux2_klein_dev_9b": {"text": "...", "enabled": true}}
    image_system_prompt_overrides: Optional[dict] = Field(
        default=None, sa_column=Column(JSON)
    )
    video_system_prompt_overrides: Optional[dict] = Field(
        default=None, sa_column=Column(JSON)
    )
    # Per-model prompt guidance — stored as JSON dicts keyed by model name
    # e.g. {"flux2_klein_dev_9b": "Always use detailed descriptions..."}
    image_prompt_guidance: Optional[dict] = Field(
        default=None, sa_column=Column(JSON)
    )
    video_prompt_guidance: Optional[dict] = Field(
        default=None, sa_column=Column(JSON)
    )
    # Global video FPS
    video_fps: int = Field(default=24)
    # Default LLM provider: "openai", "anthropic", "gemini", or None (auto-pick first available)
    default_llm_provider: Optional[str] = None
    # Video model max duration in seconds (e.g. LTX 2.3 max ~15s)
    video_max_duration: int = Field(default=15)
    # Video tail: extra seconds added to video generation, then auto-trimmed (0 = disabled)
    video_tail: int = Field(default=0)
    # Color correction: auto-correct color drift between reference frame and generated video
    color_correction_enabled: bool = Field(default=False)
    # RunPod integration
    runpod_enabled: bool = Field(default=False)
    runpod_api_key: Optional[str] = None
    runpod_idle_timeout: int = Field(default=30)  # minutes before auto-spindown
    # Pod configs: list of {pod_id, label, service_type, gpu_type_id, template_id, ...}
    runpod_pods: Optional[list] = Field(default=None, sa_column=Column(JSON))
    # Export transition settings
    export_transition_type: str = Field(default="none")  # none, crossfade, dissolve
    export_transition_duration: float = Field(default=0.5)  # seconds
    export_color_match_clips: bool = Field(default=False)  # match colors between adjacent clips
    export_lfff_trim_enabled: bool = Field(default=True)  # trim first frame of scenes using prev scene's last frame
    # Content safety: append SFW tags to all prompts to restrict nudity/explicit content
    restrict_explicit_content: bool = Field(default=False)


class WorkflowFieldType(StrEnum):
    """Workflow field type enumeration."""

    PROMPT = "prompt"
    NEGATIVE_PROMPT = "negative_prompt"
    IMAGE = "image"
    FIRST_FRAME = "first_frame"
    LAST_FRAME = "last_frame"
    WIDTH = "width"
    HEIGHT = "height"
    SEED = "seed"
    AUDIO = "audio"
    DURATION = "duration"
    FRAMERATE = "framerate"
    OTHER = "other"


class WorkflowType(StrEnum):
    """Workflow type enumeration."""

    IMAGE = "image"
    VIDEO = "video"


class WorkflowConfig(SQLModel, table=True):
    """Workflow configuration model for storing ComfyUI workflows and field mappings."""

    __tablename__ = "workflow_configs"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    name: str = Field(index=True)
    workflow_type: str  # "image" or "video"
    description: str = Field(default="")
    is_default: bool = Field(default=False, index=True)
    server_url: Optional[str] = None  # null = available to all servers, set = per-server only
    workflow_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    field_mappings: list[dict[str, Any]] = Field(
        default_factory=list, sa_column=Column(JSON)
    )  # list of WorkflowFieldMapping dicts
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
