"""
Tests for the NoAutoCategorisation feature.

Coverage:
  - process_book() leaves category/subcategory empty when AUTO_CATEGORIZATION_ENABLED=False
  - POST /api/v1/documents/{id}/categorize  works end-to-end
  - POST /api/v1/documents/bulk/categorize  works end-to-end
  - Both endpoints return 403 when CATEGORIZE_API_KEY is set and wrong key supplied
  - GET /api/v1/config reflects the flag state
"""
import json
import sqlite3
import unittest.mock as mock

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Shared fixture: isolated in-memory DB wired into the single-file app
# ---------------------------------------------------------------------------

@pytest.fixture()
def app_client(tmp_path, monkeypatch):
    """
    Spin up a TestClient with an isolated SQLite DB.
    Returns (client, db_path).
    """
    db_file = str(tmp_path / "test_cat.db")
    monkeypatch.setenv("DB_PATH", db_file)

    import backend.app as app_module
    monkeypatch.setattr(app_module, "DB_PATH", db_file)
    # Ensure no CATEGORIZE_API_KEY by default so endpoints are open
    monkeypatch.setattr(app_module, "CATEGORIZE_API_KEY", "")
    # Disable auto-categorisation (the default, but be explicit)
    monkeypatch.setattr(app_module, "AUTO_CATEGORIZATION_ENABLED", False)

    # Initialise schema
    app_module.init_db()

    from backend.app import app
    client = TestClient(app, raise_server_exceptions=False)
    return client, db_file, app_module


def _seed_book(db_file: str, **kwargs) -> int:
    """Insert a minimal book row and return its id."""
    defaults = dict(
        filename="book.pdf",
        filepath="/books/book.pdf",
        file_type="pdf",
        file_size=1024,
        file_hash="abc123",
        status="done",
    )
    defaults.update(kwargs)
    conn = sqlite3.connect(db_file)
    conn.row_factory = sqlite3.Row
    cur = conn.execute(
        """INSERT INTO books (filename, filepath, file_type, file_size, file_hash, status)
           VALUES (:filename, :filepath, :file_type, :file_size, :file_hash, :status)""",
        defaults,
    )
    conn.commit()
    book_id = cur.lastrowid
    conn.close()
    return book_id


def _read_book(db_file: str, book_id: int) -> dict:
    conn = sqlite3.connect(db_file)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM books WHERE id=?", (book_id,)).fetchone()
    conn.close()
    return dict(row) if row else {}


# ---------------------------------------------------------------------------
# Unit: process_book skips category when AUTO_CATEGORIZATION_ENABLED=False
# ---------------------------------------------------------------------------

def test_process_book_no_auto_cat_leaves_category_empty(tmp_path, monkeypatch):
    """
    When AUTO_CATEGORIZATION_ENABLED=False, process_book() must not write
    category or subcategory to the DB even if Ollama returns them.
    """
    db_file = str(tmp_path / "unit.db")
    monkeypatch.setenv("DB_PATH", db_file)

    import backend.app as app_module
    monkeypatch.setattr(app_module, "DB_PATH", db_file)
    monkeypatch.setattr(app_module, "AUTO_CATEGORIZATION_ENABLED", False)
    app_module.init_db()

    # Seed a pending book
    conn = sqlite3.connect(db_file)
    conn.row_factory = sqlite3.Row
    cur = conn.execute(
        "INSERT INTO books (filename, filepath, file_type, file_size, file_hash, status) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("sample.pdf", str(tmp_path / "sample.pdf"), "pdf", 0, "hash001", "pending"),
    )
    conn.commit()
    book_id = cur.lastrowid
    conn.close()

    # Create a minimal real file so the hash step doesn't fail
    (tmp_path / "sample.pdf").write_bytes(b"%PDF-1.4 fake")

    # Stub out extract_text and call_ollama so we control the returned metadata
    fake_meta = {
        "title": "Great Book",
        "author": "Jane Doe",
        "year": "2020",
        "language": "English",
        "category": "Science",        # Ollama claims a category
        "subcategory": "Physics",      # and a subcategory
        "difficulty": "Intermediate",
        "description": "A description.",
        "tags": ["physics"],
    }
    monkeypatch.setattr(app_module, "extract_text", lambda *_a, **_kw: "some text")
    monkeypatch.setattr(app_module, "call_ollama", lambda *_a, **_kw: fake_meta)

    app_module.process_book(book_id, str(tmp_path / "sample.pdf"), "pdf")

    row = _read_book(db_file, book_id)
    assert row["title"] == "Great Book", "title should be set"
    assert row["author"] == "Jane Doe", "author should be set"
    assert (row["category"] or "") == "", "category must be empty when auto-cat is off"
    assert (row["subcategory"] or "") == "", "subcategory must be empty when auto-cat is off"


def test_process_book_auto_cat_on_sets_category(tmp_path, monkeypatch):
    """
    When AUTO_CATEGORIZATION_ENABLED=True, category is written as normal.
    Ensures the flag actually controls the behaviour in both directions.
    """
    db_file = str(tmp_path / "unit2.db")
    monkeypatch.setenv("DB_PATH", db_file)

    import backend.app as app_module
    monkeypatch.setattr(app_module, "DB_PATH", db_file)
    monkeypatch.setattr(app_module, "AUTO_CATEGORIZATION_ENABLED", True)
    app_module.init_db()

    conn = sqlite3.connect(db_file)
    conn.row_factory = sqlite3.Row
    cur = conn.execute(
        "INSERT INTO books (filename, filepath, file_type, file_size, file_hash, status) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("sample2.pdf", str(tmp_path / "sample2.pdf"), "pdf", 0, "hash002", "pending"),
    )
    conn.commit()
    book_id = cur.lastrowid
    conn.close()

    (tmp_path / "sample2.pdf").write_bytes(b"%PDF-1.4 fake")

    fake_meta = {
        "title": "Physics 101",
        "author": "Bob",
        "year": "2019",
        "language": "English",
        "category": "Science",
        "subcategory": "Physics",
        "difficulty": "Beginner",
        "description": "Intro.",
        "tags": ["physics"],
    }
    monkeypatch.setattr(app_module, "extract_text", lambda *_a, **_kw: "text")
    monkeypatch.setattr(app_module, "call_ollama", lambda *_a, **_kw: fake_meta)

    app_module.process_book(book_id, str(tmp_path / "sample2.pdf"), "pdf")

    row = _read_book(db_file, book_id)
    assert row["category"] == "Science"
    assert row["subcategory"] == "Physics"


# ---------------------------------------------------------------------------
# Integration: GET /api/v1/config
# ---------------------------------------------------------------------------

def test_get_config_flag_false(app_client):
    client, _, app_module = app_client
    resp = client.get("/api/v1/config")
    assert resp.status_code == 200
    data = resp.json()
    assert data["auto_categorization_enabled"] is False
    assert data["categorize_key_required"] is False


def test_get_config_flag_true(app_client, monkeypatch):
    client, _, app_module = app_client
    monkeypatch.setattr(app_module, "AUTO_CATEGORIZATION_ENABLED", True)
    resp = client.get("/api/v1/config")
    assert resp.status_code == 200
    assert resp.json()["auto_categorization_enabled"] is True


# ---------------------------------------------------------------------------
# Integration: POST /api/v1/documents/{id}/categorize
# ---------------------------------------------------------------------------

def test_categorize_single_sets_category(app_client):
    client, db_file, _ = app_client
    book_id = _seed_book(db_file)

    resp = client.post(
        f"/api/v1/documents/{book_id}/categorize",
        json={"category": "Science", "subcategory": "Physics"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["category"] == "Science"
    assert data["subcategory"] == "Physics"

    # Verify persisted in DB
    row = _read_book(db_file, book_id)
    assert row["category"] == "Science"
    assert row["subcategory"] == "Physics"
    assert row["fixed_category"] == "Science"
    assert row["fixed_subcategory"] == "Physics"
    assert row["categorized_by"] == "manual"
    assert row["categorized_at"] is not None


def test_categorize_single_not_found(app_client):
    client, _, _ = app_client
    resp = client.post("/api/v1/documents/99999/categorize", json={"category": "Science"})
    assert resp.status_code == 404


def test_categorize_single_empty_category(app_client):
    client, db_file, _ = app_client
    book_id = _seed_book(db_file, filepath="/books/b2.pdf")
    resp = client.post(f"/api/v1/documents/{book_id}/categorize", json={"category": ""})
    assert resp.status_code == 400


def test_categorize_single_writes_canonical_categories(app_client):
    client, db_file, _ = app_client
    book_id = _seed_book(db_file, filepath="/books/b3.pdf")
    client.post(
        f"/api/v1/documents/{book_id}/categorize",
        json={"category": "Engineering", "subcategory": "Electrical Engineering"},
    )
    conn = sqlite3.connect(db_file)
    row = conn.execute(
        "SELECT * FROM canonical_categories WHERE category=? AND subcategory=?",
        ("Engineering", "Electrical Engineering"),
    ).fetchone()
    conn.close()
    assert row is not None


def test_categorize_single_writes_document_categories(app_client):
    client, db_file, _ = app_client
    book_id = _seed_book(db_file, filepath="/books/b4.pdf")
    client.post(
        f"/api/v1/documents/{book_id}/categorize",
        json={"category": "Mathematics", "subcategory": "Calculus"},
    )
    conn = sqlite3.connect(db_file)
    row = conn.execute(
        "SELECT dc.* FROM document_categories dc "
        "JOIN canonical_categories cc ON dc.category_id=cc.id "
        "WHERE dc.book_id=? AND cc.category=?",
        (book_id, "Mathematics"),
    ).fetchone()
    conn.close()
    assert row is not None


# ---------------------------------------------------------------------------
# Integration: POST /api/v1/documents/bulk/categorize
# ---------------------------------------------------------------------------

def test_bulk_categorize(app_client):
    client, db_file, _ = app_client
    id1 = _seed_book(db_file, filepath="/books/c1.pdf", file_hash="h1")
    id2 = _seed_book(db_file, filepath="/books/c2.pdf", file_hash="h2")

    resp = client.post(
        "/api/v1/documents/bulk/categorize",
        json={"ids": [id1, id2], "category": "History & Politics", "subcategory": "Modern History"},
    )
    assert resp.status_code == 200
    assert resp.json()["categorized"] == 2

    for bid in [id1, id2]:
        row = _read_book(db_file, bid)
        assert row["category"] == "History & Politics"
        assert row["subcategory"] == "Modern History"


def test_bulk_categorize_empty_ids(app_client):
    client, _, _ = app_client
    resp = client.post("/api/v1/documents/bulk/categorize", json={"ids": [], "category": "Science"})
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Integration: CATEGORIZE_API_KEY permission enforcement
# ---------------------------------------------------------------------------

def test_categorize_requires_key_when_set(app_client, monkeypatch):
    client, db_file, app_module = app_client
    monkeypatch.setattr(app_module, "CATEGORIZE_API_KEY", "secret-key")
    book_id = _seed_book(db_file, filepath="/books/d1.pdf", file_hash="h10")

    # No key -> 403
    resp = client.post(
        f"/api/v1/documents/{book_id}/categorize",
        json={"category": "Science"},
    )
    assert resp.status_code == 403

    # Wrong key -> 403
    resp = client.post(
        f"/api/v1/documents/{book_id}/categorize",
        json={"category": "Science"},
        headers={"X-Categorize-Key": "wrong"},
    )
    assert resp.status_code == 403

    # Correct key -> 200
    resp = client.post(
        f"/api/v1/documents/{book_id}/categorize",
        json={"category": "Science"},
        headers={"X-Categorize-Key": "secret-key"},
    )
    assert resp.status_code == 200


def test_bulk_categorize_requires_key_when_set(app_client, monkeypatch):
    client, db_file, app_module = app_client
    monkeypatch.setattr(app_module, "CATEGORIZE_API_KEY", "secret-key")
    id1 = _seed_book(db_file, filepath="/books/d2.pdf", file_hash="h11")

    resp = client.post(
        "/api/v1/documents/bulk/categorize",
        json={"ids": [id1], "category": "Science"},
    )
    assert resp.status_code == 403

    resp = client.post(
        "/api/v1/documents/bulk/categorize",
        json={"ids": [id1], "category": "Science"},
        headers={"X-Categorize-Key": "secret-key"},
    )
    assert resp.status_code == 200
