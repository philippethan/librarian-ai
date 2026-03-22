"""Tests for _serialize_book — §4 and §13: all BOOK_DEFAULTS present; "" -> None; tags always list."""
import json

import pytest


def _serialize(book: dict) -> dict:
    from backend.app import _serialize_book
    return _serialize_book(book)


EXPECTED_DEFAULTS = {
    "title": None, "author": None, "year": None, "language": None,
    "category": None, "subcategory": None, "difficulty": None,
    "description": None, "tags": [], "error_msg": None,
    "manual_fixed": 0, "extraction_method": None, "confidence_score": None,
    "cover_path": None, "cover_source": None, "file_hash": None,
    "duplicate_of": None, "reading_status": None, "ol_enriched": 0,
    "dedup_dismissed": 0,
}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_all_defaults_present_when_book_is_empty():
    """An empty book dict gets all BOOK_DEFAULTS keys."""
    result = _serialize({})
    for key, default_val in EXPECTED_DEFAULTS.items():
        assert key in result, f"Key '{key}' missing from serialized output"
        assert result[key] == default_val, (
            f"Key '{key}': expected default {default_val!r}, got {result[key]!r}"
        )


def test_explicit_values_override_defaults():
    """Values provided in the book dict override defaults."""
    book = {"title": "Python Tricks", "author": "Dan Bader", "year": 2017}
    result = _serialize(book)
    assert result["title"] == "Python Tricks"
    assert result["author"] == "Dan Bader"
    assert result["year"] == 2017


def test_empty_string_becomes_none_for_text_fields():
    """Empty string in title/author/language/category etc. -> None."""
    fields = ("title", "author", "language", "category", "subcategory",
              "difficulty", "description", "extraction_method")
    for field in fields:
        result = _serialize({field: ""})
        assert result[field] is None, (
            f"Field '{field}': empty string should become None, got {result[field]!r}"
        )


def test_string_null_becomes_none():
    """String literal "null" or "None" -> None for text fields."""
    result_null = _serialize({"title": "null"})
    result_none = _serialize({"title": "None"})
    assert result_null["title"] is None
    assert result_none["title"] is None


def test_tags_json_string_parsed_to_list():
    """tags stored as JSON string -> parsed to list."""
    result = _serialize({"tags": '["python", "ai"]'})
    assert isinstance(result["tags"], list)
    assert result["tags"] == ["python", "ai"]


def test_tags_already_list_stays_list():
    """tags already a list -> returned as-is."""
    result = _serialize({"tags": ["python", "ai"]})
    assert isinstance(result["tags"], list)
    assert result["tags"] == ["python", "ai"]


def test_tags_none_returns_empty_list():
    """tags=None (missing) -> default empty list."""
    result = _serialize({})
    assert result["tags"] == []


def test_tags_invalid_json_string_returns_empty_list():
    """Malformed tags JSON string -> empty list, no exception."""
    result = _serialize({"tags": "not valid json {"})
    assert isinstance(result["tags"], list)
    assert result["tags"] == []


def test_confidence_score_zero_not_replaced():
    """confidence_score=0.0 must NOT be coerced away (not in the "" -> None list)."""
    result = _serialize({"confidence_score": 0.0})
    assert result["confidence_score"] == 0.0


def test_manual_fixed_zero_preserved():
    """manual_fixed=0 is a valid value and must not be treated as missing."""
    result = _serialize({"manual_fixed": 0})
    assert result["manual_fixed"] == 0


def test_extra_keys_from_book_passed_through():
    """Keys not in BOOK_DEFAULTS (e.g. 'id', 'filename') are preserved."""
    result = _serialize({"id": 42, "filename": "book.pdf"})
    assert result["id"] == 42
    assert result["filename"] == "book.pdf"
