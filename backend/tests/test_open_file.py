"""Tests for POST /api/books/{id}/open — §13: inside -> 200; outside -> 403; missing -> 404."""
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def open_env(tmp_path, monkeypatch):
    """Isolated DB, a real file inside books_root, and a TestClient."""
    db_file = str(tmp_path / "test.db")
    books_root = tmp_path / "books"
    books_root.mkdir()

    monkeypatch.setenv("DB_PATH", db_file)
    monkeypatch.setenv("API_KEY", "")
    monkeypatch.setenv("BOOKS_PATH", str(books_root))

    import backend.app as app_module
    import backend.auth as auth_module
    import backend.db as db_module

    monkeypatch.setattr(app_module, "DB_PATH", db_file)
    monkeypatch.setattr(app_module, "BOOKS_ROOT", books_root.resolve())
    monkeypatch.setattr(auth_module, "_API_KEY", "")
    db_module._local.conn = None

    from scripts.migrate_db import run_migrations
    run_migrations(db_file)

    # Book whose file exists inside books_root
    real_file = books_root / "valid_book.pdf"
    real_file.write_bytes(b"pdf content")

    # Book whose file does NOT exist on disk
    missing_file = books_root / "missing_book.pdf"

    # Book whose filepath points outside books_root
    outside_file = tmp_path / "outside_book.pdf"
    outside_file.write_bytes(b"outside content")

    conn = sqlite3.connect(db_file)
    conn.row_factory = sqlite3.Row

    cur = conn.execute(
        "INSERT INTO books (filename, filepath, status) VALUES (?, ?, 'done')",
        ("valid_book.pdf", str(real_file.resolve())),
    )
    conn.commit()
    id_valid = cur.lastrowid

    cur = conn.execute(
        "INSERT INTO books (filename, filepath, status) VALUES (?, ?, 'done')",
        ("missing_book.pdf", str(missing_file.resolve())),
    )
    conn.commit()
    id_missing = cur.lastrowid

    cur = conn.execute(
        "INSERT INTO books (filename, filepath, status) VALUES (?, ?, 'done')",
        ("outside_book.pdf", str(outside_file.resolve())),
    )
    conn.commit()
    id_outside = cur.lastrowid

    conn.close()
    db_module._local.conn = None

    from backend.app import app
    client = TestClient(app)

    return {
        "client": client,
        "id_valid": id_valid,
        "id_missing": id_missing,
        "id_outside": id_outside,
        "real_file": real_file,
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_open_file_inside_books_root_returns_200(open_env, monkeypatch):
    """File path inside BOOKS_PATH -> 200 and subprocess.Popen called."""
    import subprocess
    client = open_env["client"]
    book_id = open_env["id_valid"]

    with monkeypatch.context() as m:
        m.setattr(subprocess, "Popen", lambda *args, **kwargs: None)
        resp = client.post(f"/api/books/{book_id}/open")

    assert resp.status_code == 200
    assert "opened" in resp.json()


def test_open_file_outside_books_root_returns_403(open_env):
    """File path outside BOOKS_PATH -> 403 Forbidden."""
    client = open_env["client"]
    book_id = open_env["id_outside"]

    resp = client.post(f"/api/books/{book_id}/open")
    assert resp.status_code == 403


def test_open_file_missing_from_disk_returns_404(open_env):
    """File path inside BOOKS_PATH but file not on disk -> 404."""
    client = open_env["client"]
    book_id = open_env["id_missing"]

    resp = client.post(f"/api/books/{book_id}/open")
    assert resp.status_code == 404


def test_open_nonexistent_book_returns_404(open_env):
    """Book ID not in DB -> 404."""
    client = open_env["client"]
    resp = client.post("/api/books/999999/open")
    assert resp.status_code == 404
