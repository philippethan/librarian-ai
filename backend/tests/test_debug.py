"""Tests for debug JSON — §13: JSON written after processing; endpoint returns it."""
import json
import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _setup_db(tmp_path, monkeypatch):
    db_file = str(tmp_path / "test.db")
    monkeypatch.setenv("DB_PATH", db_file)

    import backend.app as app_module
    import backend.auth as auth_module
    import backend.db as db_module

    monkeypatch.setattr(app_module, "DB_PATH", db_file)
    monkeypatch.setattr(auth_module, "_API_KEY", "")
    db_module._local.conn = None

    from scripts.migrate_db import run_migrations
    run_migrations(db_file)
    db_module._local.conn = None
    return db_file


def _insert_book(db_file: str, filepath: str) -> int:
    conn = sqlite3.connect(db_file)
    cur = conn.execute(
        "INSERT INTO books (filename, filepath, status) VALUES (?, ?, 'processing')",
        (Path(filepath).name, filepath),
    )
    conn.commit()
    book_id = cur.lastrowid
    conn.close()
    return book_id


def _run_process(book_id: int, filepath: str, db_file: str):
    import backend.db as db_module
    db_module._local.conn = None

    with patch("backend.process.call_ollama_sync", return_value={}), \
         patch("backend.covers.extract_cover", return_value=None):
        from backend.process import process_book_sync
        process_book_sync(book_id, filepath, db_file)


# ---------------------------------------------------------------------------
# Tests — debug JSON written by process_book_sync (Step 9)
# ---------------------------------------------------------------------------

def test_debug_json_written_after_processing(tmp_path, monkeypatch):
    """Step 9: process_book_sync writes {book_id}.json to DEBUG_PATH."""
    debug_dir = tmp_path / "debug"
    monkeypatch.setenv("DEBUG_PATH", str(debug_dir))

    db_file = _setup_db(tmp_path, monkeypatch)

    # Create a minimal PDF-like file (content doesn't matter — Ollama is mocked)
    book_file = tmp_path / "test_book.pdf"
    book_file.write_bytes(b"%PDF-1.4 fake content for testing")

    book_id = _insert_book(db_file, str(book_file))
    _run_process(book_id, str(book_file), db_file)

    debug_file = debug_dir / f"{book_id}.json"
    assert debug_file.exists(), f"Debug file {debug_file} was not created"


def test_debug_json_has_required_keys(tmp_path, monkeypatch):
    """Debug JSON contains book_id, filename, passes, merged, timestamp."""
    debug_dir = tmp_path / "debug"
    monkeypatch.setenv("DEBUG_PATH", str(debug_dir))

    db_file = _setup_db(tmp_path, monkeypatch)

    book_file = tmp_path / "keys_test.pdf"
    book_file.write_bytes(b"%PDF-1.4 fake")

    book_id = _insert_book(db_file, str(book_file))
    _run_process(book_id, str(book_file), db_file)

    debug_file = debug_dir / f"{book_id}.json"
    payload = json.loads(debug_file.read_text(encoding="utf-8"))

    assert payload["book_id"] == book_id
    assert "filename" in payload
    assert "passes" in payload
    assert "llm" in payload["passes"]
    assert "filename_heuristic" in payload["passes"]
    assert "epub_meta" in payload["passes"]
    assert "merged" in payload
    assert "timestamp" in payload


def test_debug_json_text_lengths_present(tmp_path, monkeypatch):
    """Debug JSON includes raw_text_length."""
    debug_dir = tmp_path / "debug"
    monkeypatch.setenv("DEBUG_PATH", str(debug_dir))

    db_file = _setup_db(tmp_path, monkeypatch)

    book_file = tmp_path / "lengths_test.pdf"
    book_file.write_bytes(b"%PDF-1.4 fake")

    book_id = _insert_book(db_file, str(book_file))
    _run_process(book_id, str(book_file), db_file)

    payload = json.loads(
        (debug_dir / f"{book_id}.json").read_text(encoding="utf-8")
    )
    assert "raw_text_length" in payload
    assert isinstance(payload["raw_text_length"], int)


# ---------------------------------------------------------------------------
# Tests — GET /api/books/{id}/debug endpoint
# ---------------------------------------------------------------------------

@pytest.fixture()
def debug_client(tmp_path, monkeypatch):
    debug_dir = tmp_path / "debug"
    monkeypatch.setenv("DEBUG_PATH", str(debug_dir))

    db_file = _setup_db(tmp_path, monkeypatch)

    from backend.app import app
    client = TestClient(app, raise_server_exceptions=True)
    return client, db_file, debug_dir


def test_debug_endpoint_returns_json(debug_client, tmp_path, monkeypatch):
    """GET /api/books/{id}/debug returns the debug JSON after processing."""
    client, db_file, debug_dir = debug_client

    book_file = tmp_path / "endpoint_test.pdf"
    book_file.write_bytes(b"%PDF-1.4 fake content")

    book_id = _insert_book(db_file, str(book_file))
    _run_process(book_id, str(book_file), db_file)

    import backend.db as db_module
    db_module._local.conn = None

    resp = client.get(f"/api/books/{book_id}/debug")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["book_id"] == book_id
    assert "passes" in payload
    assert "merged" in payload


def test_debug_endpoint_404_when_no_debug_file(debug_client, tmp_path):
    """GET /api/books/{id}/debug returns 404 when no debug file exists."""
    client, db_file, debug_dir = debug_client

    # Insert book but do NOT process it (no debug file written)
    conn = sqlite3.connect(db_file)
    cur = conn.execute(
        "INSERT INTO books (filename, filepath, status) VALUES (?, ?, 'processing')",
        ("nodebug.pdf", str(tmp_path / "nodebug.pdf")),
    )
    conn.commit()
    book_id = cur.lastrowid
    conn.close()

    import backend.db as db_module
    db_module._local.conn = None

    resp = client.get(f"/api/books/{book_id}/debug")
    assert resp.status_code == 404


def test_debug_endpoint_404_for_missing_book(debug_client):
    """GET /api/books/{id}/debug returns 404 when book doesn't exist."""
    client, _, _ = debug_client
    resp = client.get("/api/books/99999/debug")
    assert resp.status_code == 404
