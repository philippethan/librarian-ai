"""Tests for API key authentication."""
import sqlite3

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def _setup_db(tmp_path, monkeypatch):
    """Set up an isolated DB and wire it into the app."""
    db_file = str(tmp_path / "test.db")
    monkeypatch.setenv("DB_PATH", db_file)

    import backend.app as app_module
    monkeypatch.setattr(app_module, "DB_PATH", db_file)

    from scripts.migrate_db import run_migrations
    run_migrations(db_file)
    return db_file


def test_no_key_returns_401(_setup_db, monkeypatch):
    """GET /api/books without X-API-Key header -> 401 when key is set."""
    import backend.app as app_module
    monkeypatch.setattr(app_module, "API_KEY", "secret")

    from backend.app import app
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/api/books")
    assert resp.status_code == 401


def test_correct_key_returns_200(_setup_db, monkeypatch):
    """GET /api/books with correct X-API-Key -> 200."""
    import backend.app as app_module
    monkeypatch.setattr(app_module, "API_KEY", "secret")

    from backend.app import app
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/api/books", headers={"X-API-Key": "secret"})
    assert resp.status_code == 200


def test_health_no_key_returns_200(_setup_db, monkeypatch):
    """GET /api/health is always 200, no auth required."""
    import backend.app as app_module
    monkeypatch.setattr(app_module, "API_KEY", "secret")

    from backend.app import app
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/api/health")
    assert resp.status_code == 200
