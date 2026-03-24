"""Tests for POST /api/books/{id}/rename — collision handling."""
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


def _setup_env(tmp_path, monkeypatch):
    db_file = str(tmp_path / "test.db")
    books_root = tmp_path / "books"
    books_root.mkdir()

    monkeypatch.setenv("DB_PATH", db_file)
    monkeypatch.setenv("API_KEY", "")

    import backend.app as app_module
    monkeypatch.setattr(app_module, "DB_PATH", db_file)
    monkeypatch.setattr(app_module, "API_KEY", "")
    monkeypatch.setattr(app_module, "BOOKS_ROOT", books_root.resolve())

    from scripts.migrate_db import run_migrations
    run_migrations(db_file)

    return db_file, books_root


def _insert_book(db_file: str, filename: str, filepath: str,
                 title: str = "My Title", author: str = "J Smith",
                 year=2023) -> int:
    conn = sqlite3.connect(db_file)
    cur = conn.execute(
        "INSERT INTO books (filename, filepath, status, title, author, year) "
        "VALUES (?, ?, 'done', ?, ?, ?)",
        (filename, filepath, title, author, year),
    )
    conn.commit()
    book_id = cur.lastrowid
    conn.close()
    return book_id


def test_rename_basic(tmp_path, monkeypatch):
    """Happy path: no collision -> file renamed, DB updated."""
    db_file, books_root = _setup_env(tmp_path, monkeypatch)
    src = books_root / "oldname.pdf"
    src.write_bytes(b"content")
    book_id = _insert_book(db_file, "oldname.pdf", str(src))

    from backend.app import app
    client = TestClient(app)
    resp = client.post(f"/api/books/{book_id}/rename")

    assert resp.status_code == 200
    data = resp.json()
    assert data["renamed"] is True
    assert data["new_filename"] == "J_Smith_My_Title_2023.pdf"
    assert Path(books_root / "J_Smith_My_Title_2023.pdf").exists()
    assert not src.exists()


def test_rename_dry_run(tmp_path, monkeypatch):
    """dry_run=True -> returns new_filename without touching disk."""
    db_file, books_root = _setup_env(tmp_path, monkeypatch)
    src = books_root / "orig.pdf"
    src.write_bytes(b"content")
    book_id = _insert_book(db_file, "orig.pdf", str(src))

    from backend.app import app
    client = TestClient(app)
    resp = client.post(f"/api/books/{book_id}/rename?dry_run=true")

    assert resp.status_code == 200
    data = resp.json()
    assert data["dry_run"] is True
    assert data["new_filename"] == "J_Smith_My_Title_2023.pdf"
    assert src.exists()


def test_rename_collision_adds_01_suffix(tmp_path, monkeypatch):
    """Target filename already exists -> renames to {stem}_01.pdf."""
    db_file, books_root = _setup_env(tmp_path, monkeypatch)
    src = books_root / "source.pdf"
    src.write_bytes(b"source content")
    (books_root / "J_Smith_My_Title_2023.pdf").write_bytes(b"existing")
    book_id = _insert_book(db_file, "source.pdf", str(src))

    from backend.app import app
    client = TestClient(app)
    resp = client.post(f"/api/books/{book_id}/rename")

    assert resp.status_code == 200
    data = resp.json()
    assert data["renamed"] is True
    assert data["new_filename"] == "J_Smith_My_Title_2023_01.pdf"
    assert Path(books_root / "J_Smith_My_Title_2023_01.pdf").exists()


def test_rename_collision_picks_next_free_slot(tmp_path, monkeypatch):
    """_01 through _03 already exist -> uses _04."""
    db_file, books_root = _setup_env(tmp_path, monkeypatch)
    src = books_root / "source.pdf"
    src.write_bytes(b"source content")
    for suffix in ["", "_01", "_02", "_03"]:
        (books_root / f"J_Smith_My_Title_2023{suffix}.pdf").write_bytes(b"x")
    book_id = _insert_book(db_file, "source.pdf", str(src))

    from backend.app import app
    client = TestClient(app)
    resp = client.post(f"/api/books/{book_id}/rename")

    assert resp.status_code == 200
    assert resp.json()["new_filename"] == "J_Smith_My_Title_2023_04.pdf"


def test_rename_collision_99_exhaustion_returns_skipped(tmp_path, monkeypatch):
    """All _01 through _99 taken -> skipped with reason='rename_collision'."""
    db_file, books_root = _setup_env(tmp_path, monkeypatch)
    src = books_root / "source.pdf"
    src.write_bytes(b"source content")
    (books_root / "J_Smith_My_Title_2023.pdf").write_bytes(b"x")
    for i in range(1, 100):
        (books_root / f"J_Smith_My_Title_2023_{i:02d}.pdf").write_bytes(b"x")
    book_id = _insert_book(db_file, "source.pdf", str(src))

    from backend.app import app
    client = TestClient(app)
    resp = client.post(f"/api/books/{book_id}/rename")

    assert resp.status_code == 200
    data = resp.json()
    assert data.get("skipped") is True
    assert data.get("reason") == "rename_collision"


def test_rename_missing_author_skipped(tmp_path, monkeypatch):
    """No author in DB -> skipped with reason='missing fields'."""
    db_file, books_root = _setup_env(tmp_path, monkeypatch)
    src = books_root / "noauthor.pdf"
    src.write_bytes(b"content")
    book_id = _insert_book(db_file, "noauthor.pdf", str(src), author=None)

    from backend.app import app
    client = TestClient(app)
    resp = client.post(f"/api/books/{book_id}/rename")

    assert resp.status_code == 200
    data = resp.json()
    assert data.get("skipped") is True
    assert data.get("reason") == "missing fields"


def test_rename_missing_title_skipped(tmp_path, monkeypatch):
    """No title in DB -> skipped with reason='missing fields'."""
    db_file, books_root = _setup_env(tmp_path, monkeypatch)
    src = books_root / "notitle.pdf"
    src.write_bytes(b"content")
    book_id = _insert_book(db_file, "notitle.pdf", str(src), title=None)

    from backend.app import app
    client = TestClient(app)
    resp = client.post(f"/api/books/{book_id}/rename")

    assert resp.status_code == 200
    data = resp.json()
    assert data.get("skipped") is True
    assert data.get("reason") == "missing fields"


def test_rename_without_year(tmp_path, monkeypatch):
    """No year -> stem is {Author}_{Title} without year segment."""
    db_file, books_root = _setup_env(tmp_path, monkeypatch)
    src = books_root / "noyear.pdf"
    src.write_bytes(b"content")
    book_id = _insert_book(db_file, "noyear.pdf", str(src), year=None)

    from backend.app import app
    client = TestClient(app)
    resp = client.post(f"/api/books/{book_id}/rename")

    assert resp.status_code == 200
    data = resp.json()
    assert data["renamed"] is True
    assert data["new_filename"] == "J_Smith_My_Title.pdf"
