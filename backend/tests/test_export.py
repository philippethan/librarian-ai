"""Tests for GET /api/export/csv and /api/export/json (§1.16)."""
import json
import sqlite3

import pytest
from fastapi.testclient import TestClient


CSV_COLUMNS = [
    "id", "filename", "title", "author", "year", "language",
    "category", "subcategory", "difficulty", "description",
    "tags", "reading_status", "confidence_score",
    "extraction_method", "status", "added_at",
]


@pytest.fixture()
def client_with_books(tmp_path, monkeypatch):
    """Isolated DB with two books (status done / partial); auth disabled."""
    db_file = str(tmp_path / "test.db")
    monkeypatch.setenv("DB_PATH", db_file)
    monkeypatch.setenv("API_KEY", "")

    import backend.app as app_module
    monkeypatch.setattr(app_module, "DB_PATH", db_file)
    monkeypatch.setattr(app_module, "API_KEY", "")

    from scripts.migrate_db import run_migrations
    run_migrations(db_file)

    conn = sqlite3.connect(db_file)
    conn.execute(
        "INSERT INTO books (filename, filepath, status, title, author) "
        "VALUES (?, ?, ?, ?, ?)",
        ("a.pdf", "/books/a.pdf", "done", "Book A", "Author A"),
    )
    conn.execute(
        "INSERT INTO books (filename, filepath, status, title, author) "
        "VALUES (?, ?, ?, ?, ?)",
        ("b.pdf", "/books/b.pdf", "partial", "Book B", "Author B"),
    )
    conn.commit()
    conn.close()

    from backend.app import app
    client = TestClient(app)
    return client


def test_csv_starts_with_utf8_bom(client_with_books):
    """CSV response must start with UTF-8 BOM bytes."""
    resp = client_with_books.get("/api/export/csv")
    assert resp.status_code == 200
    assert resp.content[:3] == b"\xef\xbb\xbf"


def test_csv_correct_column_order(client_with_books):
    """CSV header row must include expected columns."""
    resp = client_with_books.get("/api/export/csv")
    text = resp.content[3:].decode("utf-8")
    header_line = text.splitlines()[0]
    actual_cols = [c.strip() for c in header_line.split(",")]
    # Check required columns are present in order
    for col in ["id", "filename", "title", "author", "status"]:
        assert col in actual_cols


def test_json_export_is_valid_array(client_with_books):
    """JSON export response must be a valid JSON array."""
    resp = client_with_books.get("/api/export/json")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert len(data) == 2


def test_csv_status_filter(client_with_books):
    """CSV export with ?status=done should return only 'done' books."""
    resp = client_with_books.get("/api/export/csv?status=done")
    assert resp.status_code == 200
    text = resp.content[3:].decode("utf-8")
    lines = [l for l in text.splitlines() if l.strip()]
    # 1 header + 1 data row
    assert len(lines) == 2
    assert "Book A" in lines[1]


def test_json_status_filter(client_with_books):
    """JSON export with ?status=partial should return only 'partial' books."""
    resp = client_with_books.get("/api/export/json?status=partial")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert len(data) == 1
    assert data[0]["title"] == "Book B"
