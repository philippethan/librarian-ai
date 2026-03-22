"""Tests for shelf CRUD, multi-shelf membership, and cascade delete — §13."""
import sqlite3

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def shelf_client(tmp_path, monkeypatch):
    """Isolated DB wired into the app, API key disabled."""
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

    from backend.app import app
    client = TestClient(app, raise_server_exceptions=True)
    return client, db_file


def _insert_book(db_file: str, filename: str = "book.pdf") -> int:
    conn = sqlite3.connect(db_file)
    cur = conn.execute(
        "INSERT INTO books (filename, filepath, status) VALUES (?, ?, 'done')",
        (filename, f"/books/{filename}"),
    )
    conn.commit()
    book_id = cur.lastrowid
    conn.close()
    return book_id


# ---------------------------------------------------------------------------
# Tests — basic CRUD
# ---------------------------------------------------------------------------

def test_create_shelf(shelf_client):
    client, _ = shelf_client
    resp = client.post("/api/shelves", json={"name": "Favourites"})
    assert resp.status_code == 201
    data = resp.json()
    assert data["name"] == "Favourites"
    assert "id" in data


def test_list_shelves(shelf_client):
    client, _ = shelf_client
    client.post("/api/shelves", json={"name": "Alpha"})
    client.post("/api/shelves", json={"name": "Beta"})
    resp = client.get("/api/shelves")
    assert resp.status_code == 200
    names = [s["name"] for s in resp.json()]
    assert "Alpha" in names
    assert "Beta" in names


def test_duplicate_shelf_returns_409(shelf_client):
    client, _ = shelf_client
    client.post("/api/shelves", json={"name": "Dup"})
    resp = client.post("/api/shelves", json={"name": "Dup"})
    assert resp.status_code == 409


def test_delete_shelf(shelf_client):
    client, _ = shelf_client
    create = client.post("/api/shelves", json={"name": "ToDelete"})
    shelf_id = create.json()["id"]
    resp = client.delete(f"/api/shelves/{shelf_id}")
    assert resp.status_code == 200
    # Shelf should be gone
    shelves = client.get("/api/shelves").json()
    assert not any(s["id"] == shelf_id for s in shelves)


def test_delete_nonexistent_shelf_returns_404(shelf_client):
    client, _ = shelf_client
    resp = client.delete("/api/shelves/99999")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Tests — book membership
# ---------------------------------------------------------------------------

def test_add_book_to_shelf(shelf_client):
    client, db_file = shelf_client
    book_id = _insert_book(db_file, "member.pdf")
    create = client.post("/api/shelves", json={"name": "Reading"})
    shelf_id = create.json()["id"]

    resp = client.post(f"/api/shelves/{shelf_id}/books", json={"book_id": book_id})
    assert resp.status_code == 201
    assert resp.json()["book_id"] == book_id


def test_get_shelf_books(shelf_client):
    client, db_file = shelf_client
    book_id = _insert_book(db_file, "listed.pdf")
    create = client.post("/api/shelves", json={"name": "Listed"})
    shelf_id = create.json()["id"]
    client.post(f"/api/shelves/{shelf_id}/books", json={"book_id": book_id})

    resp = client.get(f"/api/shelves/{shelf_id}/books")
    assert resp.status_code == 200
    ids = [b["id"] for b in resp.json()]
    assert book_id in ids


def test_remove_book_from_shelf(shelf_client):
    client, db_file = shelf_client
    book_id = _insert_book(db_file, "remove.pdf")
    create = client.post("/api/shelves", json={"name": "Temp"})
    shelf_id = create.json()["id"]
    client.post(f"/api/shelves/{shelf_id}/books", json={"book_id": book_id})

    resp = client.delete(f"/api/shelves/{shelf_id}/books/{book_id}")
    assert resp.status_code == 200

    books_after = client.get(f"/api/shelves/{shelf_id}/books").json()
    assert not any(b["id"] == book_id for b in books_after)


def test_multi_shelf_membership(shelf_client):
    """A book in shelves 3 and 7 appears in both — inclusive semantics §1.8."""
    client, db_file = shelf_client
    book_id = _insert_book(db_file, "multi.pdf")
    shelf_a = client.post("/api/shelves", json={"name": "ShelfA"}).json()["id"]
    shelf_b = client.post("/api/shelves", json={"name": "ShelfB"}).json()["id"]

    client.post(f"/api/shelves/{shelf_a}/books", json={"book_id": book_id})
    client.post(f"/api/shelves/{shelf_b}/books", json={"book_id": book_id})

    books_a = [b["id"] for b in client.get(f"/api/shelves/{shelf_a}/books").json()]
    books_b = [b["id"] for b in client.get(f"/api/shelves/{shelf_b}/books").json()]
    assert book_id in books_a
    assert book_id in books_b


# ---------------------------------------------------------------------------
# Tests — cascade delete
# ---------------------------------------------------------------------------

def test_cascade_delete_removes_memberships(shelf_client):
    """Deleting a shelf removes book_shelves rows (ON DELETE CASCADE)."""
    client, db_file = shelf_client
    book_id = _insert_book(db_file, "cascade.pdf")
    shelf_id = client.post("/api/shelves", json={"name": "Cascade"}).json()["id"]
    client.post(f"/api/shelves/{shelf_id}/books", json={"book_id": book_id})

    client.delete(f"/api/shelves/{shelf_id}")

    # Verify directly in DB that book_shelves row is gone
    conn = sqlite3.connect(db_file)
    rows = conn.execute(
        "SELECT * FROM book_shelves WHERE shelf_id=?", (shelf_id,)
    ).fetchall()
    conn.close()
    assert rows == []
