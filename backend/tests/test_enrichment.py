"""Tests for backend/enrichment.py — Open Library enrichment (§1.5)."""
import json
import sqlite3
from unittest.mock import MagicMock, patch

import pytest

from scripts.migrate_db import run_migrations


def _make_db(tmp_path, **book_fields):
    """Create a temp DB with one seeded book; return (db_path, book_id)."""
    db_file = str(tmp_path / "test.db")
    run_migrations(db_file)

    defaults = dict(
        filename="book.pdf",
        filepath="/books/book.pdf",
        status="partial",
        title=None,
        author=None,
        year=None,
        language=None,
        description=None,
        tags="[]",
        confidence_score=0.3,
        ol_enriched=0,
    )
    defaults.update(book_fields)

    conn = sqlite3.connect(db_file)
    conn.row_factory = sqlite3.Row
    cols = ", ".join(defaults.keys())
    placeholders = ", ".join("?" for _ in defaults)
    cur = conn.execute(
        f"INSERT INTO books ({cols}) VALUES ({placeholders})",
        list(defaults.values()),
    )
    conn.commit()
    book_id = cur.lastrowid
    conn.close()
    return db_file, book_id


def _read_book(db_file, book_id):
    conn = sqlite3.connect(db_file)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM books WHERE id=?", (book_id,)).fetchone()
    conn.close()
    return dict(row)


def _ol_response(
    first_publish_year=2001,
    language=None,
    first_sentence=None,
    subject=None,
    author_name=None,
):
    doc = {"first_publish_year": first_publish_year}
    if language is not None:
        doc["language"] = language
    if first_sentence is not None:
        doc["first_sentence"] = first_sentence
    if subject is not None:
        doc["subject"] = subject
    if author_name is not None:
        doc["author_name"] = author_name
    return MagicMock(
        json=MagicMock(return_value={"docs": [doc]}),
        raise_for_status=MagicMock(),
    )


@pytest.fixture(autouse=True)
def _reset_db_conn():
    """Reset the thread-local DB connection before each test."""
    import backend.db as db_module
    db_module._local.conn = None
    yield
    db_module._local.conn = None


def test_null_field_gets_filled(tmp_path):
    """A null year should be filled from OL's first_publish_year."""
    db_file, book_id = _make_db(tmp_path, title="Dune", year=None, confidence_score=0.3)

    mock_resp = _ol_response(first_publish_year=1965, language=["en"], author_name=["Frank Herbert"])

    with patch("httpx.Client") as MockClient:
        MockClient.return_value.__enter__.return_value.get.return_value = mock_resp
        from backend.enrichment import enrich_book
        enrich_book(book_id, db_file)

    book = _read_book(db_file, book_id)
    assert book["year"] == 1965
    assert book["ol_enriched"] == 1


def test_non_null_valid_field_not_overwritten(tmp_path):
    """A non-null, in-range year must NOT be overwritten."""
    db_file, book_id = _make_db(
        tmp_path, title="Dune", author="Frank Herbert", year=1965,
        language="en", confidence_score=0.9,
    )

    # With confidence_score >= 0.8 and all key fields valid, enrich should no-op
    from backend.enrichment import enrich_book
    enrich_book(book_id, db_file)

    book = _read_book(db_file, book_id)
    assert book["year"] == 1965
    assert book["ol_enriched"] == 0


def test_out_of_range_year_is_overwritten(tmp_path):
    """year=9999 (out of valid range) IS treated as null and overwritten."""
    db_file, book_id = _make_db(tmp_path, title="Dune", year=9999, confidence_score=0.3)

    mock_resp = _ol_response(first_publish_year=1965)

    with patch("httpx.Client") as MockClient:
        MockClient.return_value.__enter__.return_value.get.return_value = mock_resp
        from backend.enrichment import enrich_book
        enrich_book(book_id, db_file)

    book = _read_book(db_file, book_id)
    assert book["year"] == 1965
    assert book["ol_enriched"] == 1


def test_ol_enriched_set_after_success(tmp_path):
    """ol_enriched must be set to 1 after a successful OL response."""
    db_file, book_id = _make_db(tmp_path, title="Foundation", confidence_score=0.5)

    mock_resp = _ol_response(first_publish_year=1951, author_name=["Isaac Asimov"])

    with patch("httpx.Client") as MockClient:
        MockClient.return_value.__enter__.return_value.get.return_value = mock_resp
        from backend.enrichment import enrich_book
        enrich_book(book_id, db_file)

    book = _read_book(db_file, book_id)
    assert book["ol_enriched"] == 1


def test_http_error_silent_no_exception(tmp_path):
    """HTTP error from OL -> log WARNING, no status change, no exception raised."""
    db_file, book_id = _make_db(
        tmp_path, title="Foundation", status="partial", confidence_score=0.3
    )

    import httpx

    with patch("httpx.Client") as MockClient:
        MockClient.return_value.__enter__.return_value.get.side_effect = httpx.HTTPError("connection refused")
        from backend.enrichment import enrich_book
        # Must not raise
        enrich_book(book_id, db_file)

    book = _read_book(db_file, book_id)
    assert book["status"] == "partial"  # unchanged
    assert book["ol_enriched"] == 0     # not marked enriched
