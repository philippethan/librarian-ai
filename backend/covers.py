import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

COVERS_PATH = Path(os.getenv("COVERS_PATH", "./data/covers"))


def get_cover_path(book_id: int) -> Path | None:
    """Return cover path if it exists on disk, otherwise None."""
    path = COVERS_PATH / f"{book_id}.jpg"
    return path if path.exists() else None


def extract_cover(book_id: int, filepath: str, db_path: str) -> str | None:
    """Extract or fetch a cover image for book_id.
    Full implementation in Session 2B. No-op stub for now.
    """
    return None
