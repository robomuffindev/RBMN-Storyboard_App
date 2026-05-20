"""Configuration management for RBMN Storyboard App backend."""
import json
from pathlib import Path
from typing import Optional

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings loaded from environment and .env file."""

    # ComfyUI configuration
    # Stored as a raw string so pydantic-settings doesn't try to JSON-parse it.
    # Accepts: comma-separated URLs, JSON array, or empty string.
    comfyui_urls_raw: str = Field(
        default="",
        alias="COMFYUI_URLS",
        description="Comma-separated list of remote ComfyUI URLs",
    )

    # Whisper/Speech-to-Text configuration
    whisper_mode: str = Field(
        default="local",
        pattern="^(local|remote)$",
        description="Whisper mode: 'local' or 'remote'",
    )
    whisper_remote_url: Optional[str] = Field(
        default=None,
        description="Remote Whisper API URL if whisper_mode is 'remote'",
    )
    whisper_model: str = Field(
        default="large-v2",
        description="Whisper model to use",
    )

    # LLM API Keys and models
    openai_api_key: Optional[str] = Field(
        default=None,
        description="OpenAI API key",
    )
    openai_model: Optional[str] = Field(
        default=None,
        description="OpenAI model name",
    )

    anthropic_api_key: Optional[str] = Field(
        default=None,
        description="Anthropic API key",
    )
    anthropic_model: Optional[str] = Field(
        default=None,
        description="Anthropic model name",
    )

    gemini_api_key: Optional[str] = Field(
        default=None,
        description="Google Gemini API key",
    )
    gemini_model: Optional[str] = Field(
        default=None,
        description="Google Gemini model name",
    )

    # Application server configuration
    app_host: str = Field(
        default="127.0.0.1",
        description="Host to bind FastAPI server to",
    )
    app_port: int = Field(
        default=8899,
        description="Port to bind FastAPI server to",
    )

    # Project and file storage
    project_dir: Path = Field(
        default_factory=lambda: Path("~/RBMN-Projects").expanduser(),
        description="Base directory for projects",
    )

    # Logging configuration
    log_level: str = Field(
        default="INFO",
        description="Logging level",
    )

    class Config:
        """Pydantic settings configuration."""

        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = False
        populate_by_name = True

    @property
    def comfyui_urls(self) -> list[str]:
        """Parse comfyui_urls_raw into a list of URL strings."""
        raw = self.comfyui_urls_raw.strip()
        if not raw:
            return []
        # Try JSON array first (e.g. '["http://a","http://b"]')
        if raw.startswith("["):
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, list):
                    return [u.strip() for u in parsed if u.strip()]
            except json.JSONDecodeError:
                pass
        # Fall back to comma-separated
        return [url.strip() for url in raw.split(",") if url.strip()]

    @field_validator("project_dir", mode="before")
    @classmethod
    def expand_project_dir(cls, v):
        """Expand user home directory in path."""
        if isinstance(v, str):
            return Path(v).expanduser()
        if isinstance(v, Path):
            return v.expanduser()
        return Path("~/RBMN-Projects").expanduser()

    @property
    def db_path(self) -> Path:
        """Get database file path."""
        return self.project_dir / "RBMN.db"

    @property
    def assets_dir(self) -> Path:
        """Get assets directory path."""
        return self.project_dir / "assets"

    @property
    def cache_dir(self) -> Path:
        """Get cache directory path."""
        return self.project_dir / "cache"

    @property
    def backups_dir(self) -> Path:
        """Get backups directory path."""
        return self.project_dir / "backups"


# Global settings instance
settings = Settings()
