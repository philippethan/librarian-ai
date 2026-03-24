"""Tests for enrich_open_library (backend.app) — Open Library enrichment."""
import json
from unittest.mock import MagicMock, patch

import pytest


def _ol_response_bytes(first_publish_year=2001, language=None, first_sentence=None,
                        subject=None, author_name=None):
    """Build JSON bytes for a mock OL response."""
    doc = {"first_publish_year": first_publish_year}
    if language is not None:
        doc["language"] = language
    if first_sentence is not None:
        doc["first_sentence"] = first_sentence
    if subject is not None:
        doc["subject"] = subject
    if author_name is not None:
        doc["author_name"] = author_name
    return json.dumps({"docs": [doc]}).encode()


def _patch_urlopen(response_bytes):
    """Context manager that mocks urllib.request.urlopen to return response_bytes."""
    mock_resp = MagicMock()
    mock_resp.read.return_value = response_bytes
    mock_resp.__enter__ = MagicMock(return_value=mock_resp)
    mock_resp.__exit__ = MagicMock(return_value=False)
    return patch("urllib.request.urlopen", return_value=mock_resp)


def test_null_field_gets_filled():
    """A null year should be filled from OL's first_publish_year."""
    meta = {"title": "Dune", "year": None, "author": None}
    resp_bytes = _ol_response_bytes(first_publish_year=1965, author_name=["Frank Herbert"])

    with _patch_urlopen(resp_bytes):
        from backend.app import enrich_open_library
        result = enrich_open_library(meta)

    assert str(result["year"]) == "1965"
    assert result["author"] == "Frank Herbert"


def test_non_null_valid_year_not_overwritten():
    """A non-null, in-range year must NOT be overwritten."""
    meta = {"title": "Dune", "author": "Frank Herbert", "year": "1965"}
    resp_bytes = _ol_response_bytes(first_publish_year=2000)

    with _patch_urlopen(resp_bytes):
        from backend.app import enrich_open_library
        result = enrich_open_library(meta)

    assert str(result["year"]) == "1965"


def test_out_of_range_year_is_overwritten():
    """year=9999 (out of valid range) IS treated as null and overwritten."""
    meta = {"title": "Dune", "year": "9999"}
    resp_bytes = _ol_response_bytes(first_publish_year=1965)

    with _patch_urlopen(resp_bytes):
        from backend.app import enrich_open_library
        result = enrich_open_library(meta)

    assert str(result["year"]) == "1965"


def test_http_error_silent_no_exception():
    """HTTP error from OL -> returns original meta, no exception."""
    meta = {"title": "Foundation", "year": None}

    with patch("urllib.request.urlopen", side_effect=Exception("connection refused")):
        from backend.app import enrich_open_library
        result = enrich_open_library(meta)

    assert result == meta


def test_no_docs_returns_original_meta():
    """Empty docs array -> returns original meta unchanged."""
    meta = {"title": "Unknown Book", "year": None}
    resp_bytes = json.dumps({"docs": []}).encode()

    with _patch_urlopen(resp_bytes):
        from backend.app import enrich_open_library
        result = enrich_open_library(meta)

    assert result == meta
