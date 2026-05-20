"""File utility functions for RBMN Storyboard App."""
import hashlib
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def ensure_project_dirs(project_path: Path) -> None:
    """Create standard project directory structure.

    Args:
        project_path: Base project directory path.
    """
    project_path = Path(project_path)

    # Create all standard subdirectories
    subdirs = [
        "assets/audio",
        "assets/video",
        "assets/images",
        "assets/thumbs",
        "cache",
        "backups",
    ]

    for subdir in subdirs:
        dir_path = project_path / subdir
        dir_path.mkdir(parents=True, exist_ok=True)
        logger.debug(f"Ensured directory exists: {dir_path}")


def sha256_file(file_path: Path) -> str:
    """Compute SHA256 hash of a file.

    Args:
        file_path: Path to the file.

    Returns:
        Hex string of SHA256 hash.
    """
    sha256_hash = hashlib.sha256()
    file_path = Path(file_path)

    with open(file_path, "rb") as f:
        for byte_block in iter(lambda: f.read(4096), b""):
            sha256_hash.update(byte_block)

    return sha256_hash.hexdigest()


def content_addressed_path(base_dir: Path, sha256: str, filename: str) -> Path:
    """Generate content-addressed file path.

    Uses first 2 characters of hash as directory, followed by remaining
    characters with a subdirectory structure.

    Args:
        base_dir: Base directory for content-addressed storage.
        sha256: SHA256 hash string.
        filename: Original filename to append at the end.

    Returns:
        Full path for content-addressed file.

    Example:
        >>> content_addressed_path(Path("/storage"), "abcdef123456", "image.png")
        Path("/storage/ab/cdef123456/image.png")
    """
    base_dir = Path(base_dir)

    if len(sha256) < 2:
        raise ValueError(f"SHA256 hash too short: {sha256}")

    # Use first 2 chars as directory, rest as subdirectory
    first_two = sha256[:2]
    rest = sha256[2:]

    target_path = base_dir / first_two / rest / filename
    target_path.parent.mkdir(parents=True, exist_ok=True)

    return target_path
