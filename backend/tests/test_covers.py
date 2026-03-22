"""Tests for covers.py — §13: blank detection; OL 1px placeholder skip."""
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


def _make_image(color):
    """Return a PIL Image filled with the given color tuple."""
    from PIL import Image
    return Image.new("RGB", (200, 400), color=color)


# ---------------------------------------------------------------------------
# extract_cover_pdf — blank detection
# ---------------------------------------------------------------------------

def test_blank_pdf_cover_returns_none(tmp_path, monkeypatch):
    """Pure white first page (all channel means > 250) -> returns None."""
    import backend.covers as covers_module
    monkeypatch.setattr(covers_module, "COVERS_PATH", tmp_path)

    white_img = _make_image((255, 255, 255))
    with patch("pdf2image.convert_from_path", return_value=[white_img]):
        from backend.covers import extract_cover_pdf
        result = extract_cover_pdf("fake.pdf", 1)

    assert result is None


def test_non_blank_pdf_cover_saves_file(tmp_path, monkeypatch):
    """Non-blank first page -> saves JPEG under COVERS_PATH and returns path."""
    import backend.covers as covers_module
    monkeypatch.setattr(covers_module, "COVERS_PATH", tmp_path)

    grey_img = _make_image((128, 128, 128))
    with patch("pdf2image.convert_from_path", return_value=[grey_img]):
        from backend.covers import extract_cover_pdf
        result = extract_cover_pdf("fake.pdf", 42)

    assert result is not None
    assert Path(result).exists()
    assert Path(result).name == "42.jpg"


def test_pdf_cover_near_blank_threshold(tmp_path, monkeypatch):
    """Page with mean exactly 250 on all channels -> NOT blank -> saves."""
    import backend.covers as covers_module
    monkeypatch.setattr(covers_module, "COVERS_PATH", tmp_path)

    # mean == 250, condition is > 250, so this should NOT be considered blank
    boundary_img = _make_image((250, 250, 250))
    with patch("pdf2image.convert_from_path", return_value=[boundary_img]):
        from backend.covers import extract_cover_pdf
        result = extract_cover_pdf("fake.pdf", 7)

    assert result is not None


def test_no_pages_from_pdf_returns_none(tmp_path, monkeypatch):
    """convert_from_path returns empty list -> returns None."""
    import backend.covers as covers_module
    monkeypatch.setattr(covers_module, "COVERS_PATH", tmp_path)

    with patch("pdf2image.convert_from_path", return_value=[]):
        from backend.covers import extract_cover_pdf
        result = extract_cover_pdf("fake.pdf", 5)

    assert result is None


# ---------------------------------------------------------------------------
# fetch_cover_openlibrary — 1px placeholder skip
# ---------------------------------------------------------------------------

def _make_ol_mock(content_length: int):
    """Build a mock httpx response for OL cover requests."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.headers = {"content-length": str(content_length)}
    mock_resp.content = b"x" * content_length
    return mock_resp


def _mock_pdfplumber_isbn(isbn_text: str):
    """Context manager: mock pdfplumber.open to return a page with isbn_text."""
    mock_page = MagicMock()
    mock_page.extract_text.return_value = isbn_text
    mock_pdf = MagicMock()
    mock_pdf.__enter__ = MagicMock(return_value=mock_pdf)
    mock_pdf.__exit__ = MagicMock(return_value=False)
    mock_pdf.pages = [mock_page]
    return patch("pdfplumber.open", return_value=mock_pdf)


def _mock_httpx_client(mock_resp):
    """Context manager: mock httpx.Client to return mock_resp on .get()."""
    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.get.return_value = mock_resp
    return patch("backend.covers.httpx.Client", return_value=mock_client)


def test_ol_small_response_returns_none(tmp_path, monkeypatch):
    """OL returns Content-Length <= 1000 (1px placeholder) -> returns None."""
    import backend.covers as covers_module
    monkeypatch.setattr(covers_module, "COVERS_PATH", tmp_path)

    mock_resp = _make_ol_mock(content_length=43)
    with _mock_pdfplumber_isbn("ISBN 978-3-16-148410-0"), \
         _mock_httpx_client(mock_resp):
        from backend.covers import fetch_cover_openlibrary
        result = fetch_cover_openlibrary("fake.pdf", 99)

    assert result is None


def test_ol_large_response_saves(tmp_path, monkeypatch):
    """OL returns Content-Length > 1000 -> saves file and returns path."""
    import backend.covers as covers_module
    monkeypatch.setattr(covers_module, "COVERS_PATH", tmp_path)

    mock_resp = _make_ol_mock(content_length=5000)
    with _mock_pdfplumber_isbn("ISBN 978-3-16-148410-0"), \
         _mock_httpx_client(mock_resp):
        from backend.covers import fetch_cover_openlibrary
        result = fetch_cover_openlibrary("fake.pdf", 99)

    assert result is not None
    assert Path(result).exists()


def test_ol_no_isbn_returns_none(tmp_path, monkeypatch):
    """No ISBN found in extracted text -> returns None without making HTTP request."""
    import backend.covers as covers_module
    monkeypatch.setattr(covers_module, "COVERS_PATH", tmp_path)

    mock_page = MagicMock()
    mock_page.extract_text.return_value = "No ISBN here at all."
    mock_pdf = MagicMock()
    mock_pdf.__enter__ = MagicMock(return_value=mock_pdf)
    mock_pdf.__exit__ = MagicMock(return_value=False)
    mock_pdf.pages = [mock_page]

    with patch("pdfplumber.open", return_value=mock_pdf):
        from backend.covers import fetch_cover_openlibrary
        result = fetch_cover_openlibrary("fake.pdf", 55)

    assert result is None


def test_ol_boundary_exactly_1000_returns_none(tmp_path, monkeypatch):
    """Content-Length == 1000 (not > 1000) -> still returns None."""
    import backend.covers as covers_module
    monkeypatch.setattr(covers_module, "COVERS_PATH", tmp_path)

    mock_resp = _make_ol_mock(content_length=1000)
    with _mock_pdfplumber_isbn("ISBN 0-306-40615-2"), \
         _mock_httpx_client(mock_resp):
        from backend.covers import fetch_cover_openlibrary
        result = fetch_cover_openlibrary("fake.pdf", 77)

    assert result is None
