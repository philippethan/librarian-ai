import os
import re
from pathlib import Path

from fastapi import HTTPException

from backend.db import get_conn

BOOKS_ROOT = Path(os.getenv("BOOKS_PATH", r"C:\Users\Than\Books")).resolve()


def _sanitise_stem(value: str) -> str:
    """Replace spaces with _ and strip disallowed characters."""
    value = value.replace(" ", "_")
    value = re.sub(r"[^A-Za-z0-9._-]", "", value)
    return value[:120]


def rename_book_file(book_id: int, db_path: str, dry_run: bool = False) -> dict:
    """Rename the file on disk to {Author}_{Title}_{Year}.{ext} per §1.10."""
    conn = get_conn(db_path)
    row = conn.execute(
        "SELECT filepath, filename, title, author, year FROM books WHERE id=?",
        (book_id,),
    ).fetchone()
    if not row:
        raise HTTPException(404, detail="Book not found.")

    title = row["title"]
    author = row["author"]
    year = row["year"]
    filepath = row["filepath"]

    if not author or not title:
        return {"skipped": True, "reason": "missing fields"}

    ext = Path(filepath).suffix
    stem_parts = [_sanitise_stem(author), _sanitise_stem(title)]
    if year:
        stem_parts.append(str(year))
    stem = "_".join(stem_parts)[:120]
    new_filename = stem + ext

    parent = Path(filepath).parent

    if dry_run:
        return {"new_filename": new_filename, "dry_run": True}

    # Collision handling
    target = parent / new_filename
    if target.exists() and target != Path(filepath):
        resolved_original = Path(filepath).resolve()
        for i in range(1, 100):
            candidate = parent / f"{stem}_{i:02d}{ext}"
            if not candidate.exists():
                target = candidate
                new_filename = candidate.name
                break
        else:
            return {"skipped": True, "reason": "rename_collision"}

    # BOOKS_ROOT containment check
    resolved_target = target.resolve()
    if not resolved_target.is_relative_to(BOOKS_ROOT):
        raise HTTPException(403, detail="Target path outside library root.")

    Path(filepath).rename(resolved_target)

    conn.execute(
        "UPDATE books SET filepath=?, filename=?, updated_at=datetime('now') WHERE id=?",
        (str(resolved_target).replace("\\", "/"), new_filename, book_id),
    )
    conn.commit()

    return {
        "old_filename": Path(filepath).name,
        "new_filename": new_filename,
        "renamed": True,
    }
