"""Tests for rename_book_file — §13: collision -> _01 suffix; _99 exhaustion -> skipped."""
import sqlite3
from pathlib import Path

import pytest


def _setup_db(tmp_path):
    db_file = str(tmp_path / "test.db")
    from scripts.migrate_db import run_migrations
    run_migrations(db_file)
    import backend.db as db_module
    db_module._local.conn = None
    return db_file


def _insert_book(db_file: str, filename: str, filepath: str,
                 title: str = "My Title", author: str = "J Smith",
                 year: int = 2023) -> int:
    conn = sqlite3.connect(db_file)
    cur = conn.execute(
        "INSERT INTO books (filename, filepath, status, title, author, year) "
        "VALUES (?, ?, 'done', ?, ?, ?)",
        (filename, filepath, title, author, year),
    )
    conn.commit()
    book_id = cur.lastrowid
    conn.close()
    import backend.db as db_module
    db_module._local.conn = None
    return book_id


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_rename_basic(tmp_path):
    """Happy path: no collision -> file renamed, DB updated."""
    db_file = _setup_db(tmp_path)
    src = tmp_path / "oldname.pdf"
    src.write_bytes(b"content")
    book_id = _insert_book(db_file, "oldname.pdf", str(src))

    import backend.file_utils as fu
    fu.BOOKS_ROOT = tmp_path

    from backend.file_utils import rename_book_file
    result = rename_book_file(book_id, db_file)

    assert result["renamed"] is True
    assert result["new_filename"] == "J_Smith_My_Title_2023.pdf"
    assert Path(tmp_path / "J_Smith_My_Title_2023.pdf").exists()
    assert not src.exists()


def test_rename_dry_run(tmp_path):
    """dry_run=True -> returns new_filename without touching disk."""
    db_file = _setup_db(tmp_path)
    src = tmp_path / "orig.pdf"
    src.write_bytes(b"content")
    book_id = _insert_book(db_file, "orig.pdf", str(src))

    import backend.file_utils as fu
    fu.BOOKS_ROOT = tmp_path

    from backend.file_utils import rename_book_file
    result = rename_book_file(book_id, db_file, dry_run=True)

    assert result["dry_run"] is True
    assert result["new_filename"] == "J_Smith_My_Title_2023.pdf"
    assert src.exists()  # file unchanged


def test_rename_collision_adds_01_suffix(tmp_path):
    """Target filename already exists -> renames to {stem}_01.pdf."""
    db_file = _setup_db(tmp_path)
    src = tmp_path / "source.pdf"
    src.write_bytes(b"source content")

    # Pre-create the collision target
    (tmp_path / "J_Smith_My_Title_2023.pdf").write_bytes(b"existing")

    book_id = _insert_book(db_file, "source.pdf", str(src))

    import backend.file_utils as fu
    fu.BOOKS_ROOT = tmp_path

    from backend.file_utils import rename_book_file
    result = rename_book_file(book_id, db_file)

    assert result["renamed"] is True
    assert result["new_filename"] == "J_Smith_My_Title_2023_01.pdf"
    assert Path(tmp_path / "J_Smith_My_Title_2023_01.pdf").exists()


def test_rename_collision_picks_next_free_slot(tmp_path):
    """_01 through _03 already exist -> uses _04."""
    db_file = _setup_db(tmp_path)
    src = tmp_path / "source.pdf"
    src.write_bytes(b"source content")

    # Pre-create collisions
    for suffix in ["", "_01", "_02", "_03"]:
        (tmp_path / f"J_Smith_My_Title_2023{suffix}.pdf").write_bytes(b"x")

    book_id = _insert_book(db_file, "source.pdf", str(src))

    import backend.file_utils as fu
    fu.BOOKS_ROOT = tmp_path

    from backend.file_utils import rename_book_file
    result = rename_book_file(book_id, db_file)

    assert result["renamed"] is True
    assert result["new_filename"] == "J_Smith_My_Title_2023_04.pdf"


def test_rename_collision_99_exhaustion_returns_skipped(tmp_path):
    """All _01 through _99 taken -> skipped with reason='rename_collision'."""
    db_file = _setup_db(tmp_path)
    src = tmp_path / "source.pdf"
    src.write_bytes(b"source content")

    # Pre-create the base name and all 99 suffixes
    (tmp_path / "J_Smith_My_Title_2023.pdf").write_bytes(b"x")
    for i in range(1, 100):
        (tmp_path / f"J_Smith_My_Title_2023_{i:02d}.pdf").write_bytes(b"x")

    book_id = _insert_book(db_file, "source.pdf", str(src))

    import backend.file_utils as fu
    fu.BOOKS_ROOT = tmp_path

    from backend.file_utils import rename_book_file
    result = rename_book_file(book_id, db_file)

    assert result.get("skipped") is True
    assert result.get("reason") == "rename_collision"


def test_rename_missing_author_skipped(tmp_path):
    """No author in DB -> skipped with reason='missing fields'."""
    db_file = _setup_db(tmp_path)
    src = tmp_path / "noauthor.pdf"
    src.write_bytes(b"content")
    book_id = _insert_book(db_file, "noauthor.pdf", str(src), author=None)

    import backend.file_utils as fu
    fu.BOOKS_ROOT = tmp_path

    from backend.file_utils import rename_book_file
    result = rename_book_file(book_id, db_file)

    assert result.get("skipped") is True
    assert result.get("reason") == "missing fields"


def test_rename_missing_title_skipped(tmp_path):
    """No title in DB -> skipped with reason='missing fields'."""
    db_file = _setup_db(tmp_path)
    src = tmp_path / "notitle.pdf"
    src.write_bytes(b"content")
    book_id = _insert_book(db_file, "notitle.pdf", str(src), title=None)

    import backend.file_utils as fu
    fu.BOOKS_ROOT = tmp_path

    from backend.file_utils import rename_book_file
    result = rename_book_file(book_id, db_file)

    assert result.get("skipped") is True
    assert result.get("reason") == "missing fields"


def test_rename_without_year(tmp_path):
    """No year -> stem is {Author}_{Title} without year segment."""
    db_file = _setup_db(tmp_path)
    src = tmp_path / "noyear.pdf"
    src.write_bytes(b"content")
    book_id = _insert_book(db_file, "noyear.pdf", str(src), year=None)

    import backend.file_utils as fu
    fu.BOOKS_ROOT = tmp_path

    from backend.file_utils import rename_book_file
    result = rename_book_file(book_id, db_file)

    assert result["renamed"] is True
    assert result["new_filename"] == "J_Smith_My_Title.pdf"
