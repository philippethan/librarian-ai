"""Tests for debug JSON and GET /api/books/{id}/debug endpoint."""
import asyncio
import json
import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


def _setup_env(tmp_path, monkeypatch):
    db_file = str(tmp_path / "test.db")
    debug_dir = tmp_path / "debug"
    debug_dir.mkdir()

    monkeypatch.setenv("DB_PATH", db_file)
    monkeypatch.setenv("API_KEY", "")

    import backend.app as app_module
    monkeypatch.setattr(app_module, "DB_PATH", db_file)
    monkeypatch.setattr(app_module, "API_KEY", "")
    monkeypatch.setattr(app_module, "DEBUG_DIR", debug_dir)
    monkeypatch.setattr(app_module, "OL_ENRICH", False)
    monkeypatch.setattr(app_module, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(app_module, "COVERS_DIR", tmp_path / "covers")
    (tmp_path / "cache").mkdir()
    (tmp_path / "covers").mkdir()

    from scripts.migrate_db import run_migrations
    run_migrations(db_file)
    return db_file, debug_dir


def _insert_book(db_file: str, filepath: str) -> int:
    conn = sqlite3.connect(db_file)
    cur = conn.execute(
        "INSERT INTO books (filename, filepath, file_type, status) VALUES (?, ?, ?, 'pending')",
        (Path(filepath).name, filepath, "pdf"),
    )
    conn.commit()
    book_id = cur.lastrowid
    conn.close()
    return book_id


def _run_process(book_id: int, filepath: str):
    with patch("backend.app.call_ollama_text", return_value={}), \
         patch("backend.app.call_ollama_filename", return_value={}), \
         patch("backend.app.extract_cover", return_value=None), \
         patch("backend.app.enrich_open_library", return_value={}):
        from backend.app import process_book
        asyncio.run(process_book(book_id, filepath, "pdf"))


def test_debug_json_written_after_processing(tmp_path, monkeypatch):
    """process_book writes {book_id}.json to DEBUG_DIR."""
    db_file, debug_dir = _setup_env(tmp_path, monkeypatch)

    book_file = tmp_path / "test_book.pdf"
    book_file.write_bytes(b"%PDF-1.4 fake content for testing")

    book_id = _insert_book(db_file, str(book_file))
    _run_process(book_id, str(book_file))

    debug_file = debug_dir / f"{book_id}.json"
    assert debug_file.exists(), f"Debug file {debug_file} was not created"


def test_debug_json_has_required_keys(tmp_path, monkeypatch):
    """Debug JSON contains book_id, filename, and other expected keys."""
    db_file, debug_dir = _setup_env(tmp_path, monkeypatch)

    book_file = tmp_path / "keys_test.pdf"
    book_file.write_bytes(b"%PDF-1.4 fake")

    book_id = _insert_book(db_file, str(book_file))
    _run_process(book_id, str(book_file))

    debug_file = debug_dir / f"{book_id}.json"
    payload = json.loads(debug_file.read_text(encoding="utf-8"))

    assert payload["book_id"] == book_id
    assert "filename" in payload
    assert "timestamp" in payload
    assert "status" in payload
    assert "metadata" in payload


def test_debug_json_text_length_present(tmp_path, monkeypatch):
    """Debug JSON includes text_length."""
    db_file, debug_dir = _setup_env(tmp_path, monkeypatch)

    book_file = tmp_path / "lengths_test.pdf"
    book_file.write_bytes(b"%PDF-1.4 fake")

    book_id = _insert_book(db_file, str(book_file))
    _run_process(book_id, str(book_file))

    payload = json.loads(
        (debug_dir / f"{book_id}.json").read_text(encoding="utf-8")
    )
    assert "text_length" in payload
    assert isinstance(payload["text_length"], int)


@pytest.fixture()
def debug_client(tmp_path, monkeypatch):
    db_file, debug_dir = _setup_env(tmp_path, monkeypatch)
    from backend.app import app
    client = TestClient(app, raise_server_exceptions=True)
    return client, db_file, debug_dir


def test_debug_endpoint_returns_json(debug_client, tmp_path, monkeypatch):
    """GET /api/books/{id}/debug returns the debug JSON after processing."""
    client, db_file, debug_dir = debug_client

    book_file = tmp_path / "endpoint_test.pdf"
    book_file.write_bytes(b"%PDF-1.4 fake content")

    book_id = _insert_book(db_file, str(book_file))
    _run_process(book_id, str(book_file))

    resp = client.get(f"/api/books/{book_id}/debug")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["book_id"] == book_id
    assert "status" in payload
    assert "metadata" in payload


def test_debug_endpoint_404_when_no_debug_file(debug_client, tmp_path):
    """GET /api/books/{id}/debug returns 404 when no debug file exists."""
    client, db_file, debug_dir = debug_client

    conn = sqlite3.connect(db_file)
    cur = conn.execute(
        "INSERT INTO books (filename, filepath, file_type, status) VALUES (?, ?, ?, 'pending')",
        ("nodebug.pdf", str(tmp_path / "nodebug.pdf"), "pdf"),
    )
    conn.commit()
    book_id = cur.lastrowid
    conn.close()

    resp = client.get(f"/api/books/{book_id}/debug")
    assert resp.status_code == 404


def test_debug_endpoint_404_for_missing_book(debug_client):
    """GET /api/books/{id}/debug returns 404 when book doesn't exist."""
    client, _, _ = debug_client
    resp = client.get("/api/books/99999/debug")
    assert resp.status_code == 404
