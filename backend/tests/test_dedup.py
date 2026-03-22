"""Tests for deduplication logic — §13: same hash -> duplicate_of set; dismissed -> not re-flagged."""
import hashlib
import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture()
def dedup_env(tmp_path, monkeypatch):
    """Isolated DB + two tiny book files with the same content."""
    db_file = str(tmp_path / "test.db")
    monkeypatch.setenv("DB_PATH", db_file)
    monkeypatch.setenv("API_KEY", "")

    import backend.db as db_module
    db_module._local.conn = None

    from scripts.migrate_db import run_migrations
    run_migrations(db_file)

    # Two files with identical content (same hash)
    content = b"identical content for dedup test"
    file_a = tmp_path / "book_a.pdf"
    file_b = tmp_path / "book_b.pdf"
    file_c = tmp_path / "book_c.pdf"
    file_a.write_bytes(content)
    file_b.write_bytes(content)
    file_c.write_bytes(content)

    expected_hash = hashlib.sha256(content).hexdigest()

    conn = sqlite3.connect(db_file)
    conn.row_factory = sqlite3.Row
    cur = conn.execute(
        "INSERT INTO books (filename, filepath, status) VALUES (?, ?, 'processing')",
        ("book_a.pdf", str(file_a)),
    )
    conn.commit()
    id_a = cur.lastrowid

    cur = conn.execute(
        "INSERT INTO books (filename, filepath, status) VALUES (?, ?, 'processing')",
        ("book_b.pdf", str(file_b)),
    )
    conn.commit()
    id_b = cur.lastrowid

    cur = conn.execute(
        "INSERT INTO books (filename, filepath, status) VALUES (?, ?, 'processing')",
        ("book_c.pdf", str(file_c)),
    )
    conn.commit()
    id_c = cur.lastrowid
    conn.close()

    db_module._local.conn = None

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
    """Run process_book_sync with Ollama mocked to return empty dict."""
    import backend.db as db_module
    db_module._local.conn = None

    with patch("backend.process.call_ollama_sync", return_value={}), \
         patch("backend.process.enrich_book", return_value=None), \
         patch("backend.process.extract_cover", return_value=None):
        from backend.process import process_book_sync
        process_book_sync(book_id, filepath, db_file)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_same_hash_sets_duplicate_of(dedup_env):
    """Book B with same content as Book A -> duplicate_of=A, status='duplicate'."""
    env = dedup_env

    # Process A first (establishes the hash)
    _run_process(env["id_a"], env["file_a"], env["db_file"])

    # Process B (same content -> should be flagged as duplicate)
    _run_process(env["id_b"], env["file_b"], env["db_file"])

    book_b = _read_book(env["db_file"], env["id_b"])
    assert book_b["duplicate_of"] == env["id_a"]
    assert book_b["status"] == "duplicate"
    assert book_b["file_hash"] == env["hash"]


def test_dismissed_hash_not_re_flagged(dedup_env):
    """After dedup_dismissed=1 on the canonical book, a new book with same hash is NOT flagged."""
    env = dedup_env

    # Process A to establish hash
    _run_process(env["id_a"], env["file_a"], env["db_file"])

    # Dismiss all books with this hash
    conn = sqlite3.connect(env["db_file"])
    conn.execute(
        "UPDATE books SET dedup_dismissed=1 WHERE file_hash=?",
        (env["hash"],),
    )
    conn.commit()
    conn.close()

    import backend.db as db_module
    db_module._local.conn = None

    # Process C (same hash, but canonical book has dedup_dismissed=1)
    _run_process(env["id_c"], env["file_c"], env["db_file"])

    book_c = _read_book(env["db_file"], env["id_c"])
    assert book_c["duplicate_of"] is None
    assert book_c["status"] != "duplicate"


def test_different_content_not_flagged(tmp_path, monkeypatch):
    """Two books with different content -> no duplicate flagging."""
    db_file = str(tmp_path / "test.db")
    monkeypatch.setenv("DB_PATH", db_file)

    import backend.db as db_module
    db_module._local.conn = None

    from scripts.migrate_db import run_migrations
    run_migrations(db_file)

    file_a = tmp_path / "unique_a.pdf"
    file_b = tmp_path / "unique_b.pdf"
    file_a.write_bytes(b"content for book A - unique")
    file_b.write_bytes(b"content for book B - different")

    conn = sqlite3.connect(db_file)
    cur = conn.execute(
        "INSERT INTO books (filename, filepath, status) VALUES (?, ?, 'processing')",
        ("unique_a.pdf", str(file_a)),
    )
    conn.commit()
    id_a = cur.lastrowid
    cur = conn.execute(
        "INSERT INTO books (filename, filepath, status) VALUES (?, ?, 'processing')",
        ("unique_b.pdf", str(file_b)),
    )
    conn.commit()
    id_b = cur.lastrowid
    conn.close()
    db_module._local.conn = None

    _run_process(id_a, str(file_a), db_file)
    _run_process(id_b, str(file_b), db_file)

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
    # Title should remain None — extraction was not run
    assert book_b["title"] is None
