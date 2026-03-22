"""Tests for PATCH /api/books/{id} — field-level security (§1.9)."""
import json
import os
import sqlite3

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client_and_book(tmp_path, monkeypatch):
    """Create an isolated DB, seed one book, and return (client, book_id, db_path)."""
    db_file = str(tmp_path / "test.db")

    # Point app and db module at the temp database BEFORE creating the client
    monkeypatch.setenv("DB_PATH", db_file)
    monkeypatch.setenv("API_KEY", "")  # disable auth

    import backend.app as app_module
    import backend.auth as auth_module
    import backend.db as db_module

    monkeypatch.setattr(app_module, "DB_PATH", db_file)
    monkeypatch.setattr(auth_module, "_API_KEY", "")
    # Reset thread-local connection so get_conn() opens the new DB
    db_module._local.conn = None

    # Run migrations
    from scripts.migrate_db import run_migrations
    run_migrations(db_file)

    # Seed one book
    conn = sqlite3.connect(db_file)
    conn.row_factory = sqlite3.Row
    cur = conn.execute(
        "INSERT INTO books (filename, filepath, status, file_hash) VALUES (?, ?, ?, ?)",
        ("test.pdf", "/books/test.pdf", "done", "original_hash"),
    )
    conn.commit()
    book_id = cur.lastrowid
    conn.close()

    # Reset again after seed so the app picks up WAL-mode connection fresh
    db_module._local.conn = None

    from backend.app import app
    client = TestClient(app)
    return client, book_id, db_file


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _read_col(db_file: str, book_id: int, column: str):
    conn = sqlite3.connect(db_file)
    row = conn.execute(
        f"SELECT {column} FROM books WHERE id=?", (book_id,)
    ).fetchone()
    conn.close()
    return row[0]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_patch_filepath_is_ignored(client_and_book):
    """PATCH with forbidden field 'filepath' -> 400, filepath unchanged in DB."""
    client, book_id, db_file = client_and_book
    resp = client.patch(f"/api/books/{book_id}", json={"filepath": "/etc/passwd"})
    assert resp.status_code == 400
    assert _read_col(db_file, book_id, "filepath") == "/books/test.pdf"


def test_patch_file_hash_is_ignored(client_and_book):
    """PATCH with forbidden field 'file_hash' -> 400, file_hash unchanged in DB."""
    client, book_id, db_file = client_and_book
    resp = client.patch(f"/api/books/{book_id}", json={"file_hash": "abc"})
    assert resp.status_code == 400
    assert _read_col(db_file, book_id, "file_hash") == "original_hash"


def test_patch_title_is_updated(client_and_book):
    """PATCH with valid field 'title' -> 200, title updated in DB."""
    client, book_id, db_file = client_and_book
    resp = client.patch(f"/api/books/{book_id}", json={"title": "New Title"})
    assert resp.status_code == 200
    assert resp.json()["title"] == "New Title"
    assert _read_col(db_file, book_id, "title") == "New Title"


def test_patch_empty_body_returns_400(client_and_book):
    """PATCH with empty body -> 400."""
    client, book_id, _ = client_and_book
    resp = client.patch(f"/api/books/{book_id}", json={})
    assert resp.status_code == 400
