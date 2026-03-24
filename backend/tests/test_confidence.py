import pytest
from backend.app import compute_confidence, SCORED_FIELDS


def _all_none():
    return {f: None for f in SCORED_FIELDS}


def _all_filled():
    return {
        "title": "Test Title",
        "author": "Test Author",
        "year": 2020,
        "language": "en",
        "category": "Technology",
        "subcategory": "AI",
        "difficulty": "Intermediate",
        "description": "A test description.",
        "tags": ["ai", "python"],
    }


def test_all_none_no_text():
    assert compute_confidence(_all_none(), False) == 0.0


def test_all_filled_with_text():
    assert compute_confidence(_all_filled(), True) == 1.0


def test_five_filled_with_text():
    merged = _all_none()
    merged["title"] = "Title"
    merged["author"] = "Author"
    merged["year"] = 2021
    merged["language"] = "en"
    merged["category"] = "Science"
    result = compute_confidence(merged, True)
    expected = round((5 / 9) * 0.7 + 0.3, 4)
    assert result == expected  # 0.6889


def test_empty_string_counts_as_unfilled():
    merged = _all_none()
    merged["title"] = ""  # empty string → unfilled
    assert compute_confidence(merged, False) == 0.0


def test_extraction_method_not_counted():
    # with extraction_method present
    merged_with = _all_filled()
    merged_with["extraction_method"] = "merged"
    score_with = compute_confidence(merged_with, True)

    # without extraction_method
    merged_without = _all_filled()
    score_without = compute_confidence(merged_without, True)

    assert score_with == score_without == 1.0


def test_tags_empty_list_counts_as_unfilled():
    merged = _all_none()
    merged["tags"] = []
    assert compute_confidence(merged, False) == 0.0


def test_tags_nonempty_counts_as_filled():
    merged = _all_none()
    merged["tags"] = ["ai"]
    # 1/9 filled, no text
    expected = round((1 / 9) * 0.7, 4)
    assert compute_confidence(merged, False) == expected
