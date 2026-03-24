"""Tests for deduplication logic — same hash -> duplicate_of set; dismissed -> not re-flagged."""
import asyncio
import hashlib
import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture()
def dedup_env(tmp_path, monkeypatch):
    """Isolated DB + book files with same content."""
    db_file = str(tmp_path / "test.db")
    monkeypatch.setenv("DB_PATH", db_file)
    monkeypatch.setenv("API_KEY", "")

    import backend.app as app_module
    monkeypatch.setattr(app_module, "DB_PATH", db_file)
    monkeypatch.setattr(app_module, "OL_ENRICH", False)
    monkeypatch.setattr(app_module, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(app_module, "DEBUG_DIR", tmp_path / "debug")
    monkeypatch.setattr(app_module, "COVERS_DIR", tmp_path / "covers")
    (tmp_path / "cache").mkdir()
    (tmp_path / "debug").mkdir()
    (tmp_path / "covers").mkdir()

    from scripts.migrate_db import run_migrations
    run_migrations(db_file)

    content = b"identical content for dedup test"
    file_a = tmp_path / "book_a.pdf"
    file_b = tmp_path / "book_b.pdf"
    file_c = tmp_path / "book_c.pdf"
    file_a.write_bytes(content)
    file_b.write_bytes(content)
    file_c.write_bytes(content)

    expected_hash = hashlib.sha256(content).hexdigest()

    conn = sqlite3.connect(db_file)
    cur = conn.execute(
        "INSERT INTO books (filename, filepath, file_type, status) VALUES (?, ?, ?, 'pending')",
        ("book_a.pdf", str(file_a), "pdf"),
    )
    conn.commit()
    id_a = cur.lastrowid

    cur = conn.execute(
        "INSERT INTO books (filename, filepath, file_type, status) VALUES (?, ?, ?, 'pending')",
        ("book_b.pdf", str(file_b), "pdf"),
    )
    conn.commit()
    id_b = cur.lastrowid

    cur = conn.execute(
        "INSERT INTO books (filename, filepath, file_type, status) VALUES (?, ?, ?, 'pending')",
        ("book_c.pdf", str(file_c), "pdf"),
    )
    conn.commit()
    id_c = cur.lastrowid
    conn.close()

    return {
        "db_file": db_file,
        "file_a": str(file_a),
        "file_b": str(file_b),
        "file_c": str(file_c),
        "id_a": id_a,
        "id_b": id_b,
        "id_c": id_c,
        "hash": expected_hash,
    }


def _read_book(db_file: str, book_id: int) -> dict:
    conn = sqlite3.connect(db_file)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM books WHERE id=?", (book_id,)).fetchone()
    conn.close()
    return dict(row) if row else {}


def _run_process(book_id: int, filepath: str, db_file: str):
    """Run process_book with Ollama and covers mocked."""
    with patch("backend.app.call_ollama_text", return_value={}), \
         patch("backend.app.call_ollama_filename", return_value={}), \
         patch("backend.app.extract_cover", return_value=None), \
         patch("backend.app.enrich_open_library", return_value={}):
        from backend.app import process_book
        asyncio.run(process_book(book_id, filepath, "pdf"))


def test_same_hash_sets_duplicate_of(dedup_env):
    """Book B with same content as Book A -> duplicate_of=A, status='duplicate'."""
    env = dedup_env
    _run_process(env["id_a"], env["file_a"], env["db_file"])
    _run_process(env["id_b"], env["file_b"], env["db_file"])

    book_b = _read_book(env["db_file"], env["id_b"])
    assert book_b["duplicate_of"] == env["id_a"]
    assert book_b["status"] == "duplicate"
    assert book_b["file_hash"] == env["hash"]


def test_dismissed_hash_not_re_flagged(dedup_env):
    """After dedup_dismissed=1 on the canonical book, new book with same hash is NOT flagged."""
    env = dedup_env
    _run_process(env["id_a"], env["file_a"], env["db_file"])

    conn = sqlite3.connect(env["db_file"])
    conn.execute(
        "UPDATE books SET dedup_dismissed=1 WHERE file_hash=?",
        (env["hash"],),
    )
    conn.commit()
    conn.close()

    _run_process(env["id_c"], env["file_c"], env["db_file"])

    book_c = _read_book(env["db_file"], env["id_c"])
    assert book_c["duplicate_of"] is None
    assert book_c["status"] != "duplicate"


def test_different_content_not_flagged(tmp_path, monkeypatch):
    """Two books with different content -> no duplicate flagging."""
    db_file = str(tmp_path / "test.db")
    monkeypatch.setenv("DB_PATH", db_file)

    import backend.app as app_module
    monkeypatch.setattr(app_module, "DB_PATH", db_file)
    monkeypatch.setattr(app_module, "OL_ENRICH", False)
    monkeypatch.setattr(app_module, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(app_module, "DEBUG_DIR", tmp_path / "debug")
    monkeypatch.setattr(app_module, "COVERS_DIR", tmp_path / "covers")
    for d in ["cache", "debug", "covers"]:
        (tmp_path / d).mkdir()

    from scripts.migrate_db import run_migrations
    run_migrations(db_file)

    file_a = tmp_path / "unique_a.pdf"
    file_b = tmp_path / "unique_b.pdf"
    file_a.write_bytes(b"content for book A - unique")
    file_b.write_bytes(b"content for book B - different")

    conn = sqlite3.connect(db_file)
    cur = conn.execute(
        "INSERT INTO books (filename, filepath, file_type, status) VALUES (?, ?, ?, 'pending')",
        ("unique_a.pdf", str(file_a), "pdf"),
    )
    conn.commit()
    id_a = cur.lastrowid
    cur = conn.execute(
        "INSERT INTO books (filename, filepath, file_type, status) VALUES (?, ?, ?, 'pending')",
        ("unique_b.pdf", str(file_b), "pdf"),
    )
    conn.commit()
    id_b = cur.lastrowid
    conn.close()

    def run_one(book_id, filepath):
        with patch("backend.app.call_ollama_text", return_value={}), \
             patch("backend.app.call_ollama_filename", return_value={}), \
             patch("backend.app.extract_cover", return_value=None), \
             patch("backend.app.enrich_open_library", return_value={}):
            from backend.app import process_book
            asyncio.run(process_book(book_id, filepath, "pdf"))

    run_one(id_a, str(file_a))
    run_one(id_b, str(file_b))

    book_b = _read_book(db_file, id_b)
    assert book_b["duplicate_of"] is None
    assert book_b["status"] != "duplicate"


def test_duplicate_stops_pipeline(dedup_env):
    """When a book is flagged as duplicate, its title stays None (pipeline halted)."""
    env = dedup_env
    _run_process(env["id_a"], env["file_a"], env["db_file"])
    _run_process(env["id_b"], env["file_b"], env["db_file"])

    book_b = _read_book(env["db_file"], env["id_b"])
    assert book_b["status"] == "duplicate"
    assert book_b["title"] is None
