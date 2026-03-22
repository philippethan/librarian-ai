import logging
import os
import re
from io import BytesIO
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

COVERS_PATH = Path(os.getenv("COVERS_PATH", "./data/covers"))
POPPLER_PATH = os.getenv("POPPLER_PATH", r"C:/poppler/Library/bin")

ISBN_RE = re.compile(r"ISBN[-\s]?(?:13|10)?:?\s*([0-9X-]{10,17})")


def get_cover_path(book_id: int) -> Path | None:
    """Return cover path if it exists on disk, otherwise None."""
    path = COVERS_PATH / f"{book_id}.jpg"
    return path if path.exists() else None


def extract_cover_pdf(filepath: str, book_id: int) -> str | None:
    """Extract cover from first page of PDF per §7.1 — 72 DPI, 600px cap, blank detection."""
    try:
        from pdf2image import convert_from_path
        from PIL import ImageStat

        pages = convert_from_path(
            filepath,
            first_page=1,
            last_page=1,
            dpi=72,
            size=(600, None),
            poppler_path=POPPLER_PATH,
        )
        if not pages:
            return None
        img = pages[0]
        w, h = img.size
        img = img.crop((0, 0, w, h // 2))
        # Blank detection: all channel means > 250 -> skip, try OL
        stat = ImageStat.Stat(img.convert("RGB"))
        if all(m > 250 for m in stat.mean):
            return None
        out_path = COVERS_PATH / f"{book_id}.jpg"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        img.convert("RGB").save(str(out_path), "JPEG", quality=85)
        return str(out_path)
    except Exception as e:
        logger.warning("extract_cover_pdf failed for %s: %s", filepath, e)
        return None


def extract_cover_epub(filepath: str, book_id: int) -> str | None:
    """Extract cover image from EPUB using ebooklib."""
    try:
        from ebooklib import epub
        from PIL import Image

        book = epub.read_epub(filepath, options={"ignore_ncx": True})
        cover_item = None
        for item in book.get_items():
            name = item.get_name() or ""
            props = getattr(item, "properties", []) or []
            if "cover" in name.lower() or "cover" in " ".join(props).lower():
                cover_item = item
                break
        if cover_item is None:
            return None
        img = Image.open(BytesIO(cover_item.get_content()))
        w, h = img.size
        if w > 600:
            img = img.resize((600, int(h * 600 / w)))
        out_path = COVERS_PATH / f"{book_id}.jpg"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        img.convert("RGB").save(str(out_path), "JPEG", quality=85)
        return str(out_path)
    except Exception as e:
        logger.warning("extract_cover_epub failed for %s: %s", filepath, e)
        return None


def fetch_cover_openlibrary(filepath: str, book_id: int) -> str | None:
    """Fetch cover from Open Library using ISBN found in extracted text.
    Content-Length must be > 1000 (OL returns 1px placeholder otherwise).
    """
    try:
        # Try to extract text to find an ISBN
        text = ""
        if filepath.lower().endswith(".pdf"):
            try:
                import pdfplumber
                with pdfplumber.open(filepath) as pdf:
                    for page in pdf.pages[:3]:
                        t = page.extract_text()
                        if t:
                            text += t
            except Exception:
                pass

        match = ISBN_RE.search(text)
        if not match:
            return None
        isbn = re.sub(r"[-\s]", "", match.group(1))

        url = f"https://covers.openlibrary.org/b/isbn/{isbn}-M.jpg"
        with httpx.Client(timeout=10) as client:
            resp = client.get(url)
        if resp.status_code != 200:
            return None
        content_length = int(resp.headers.get("content-length", len(resp.content)))
        if content_length <= 1000:
            return None  # 1px placeholder — skip

        out_path = COVERS_PATH / f"{book_id}.jpg"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(resp.content)
        return str(out_path)
    except Exception as e:
        logger.warning("fetch_cover_openlibrary failed for %s: %s", filepath, e)
        return None


def extract_cover(book_id: int, filepath: str, db_path: str) -> str | None:
    """Orchestrate cover extraction: local (PDF/EPUB) first, Open Library fallback."""
    fp_lower = filepath.lower()
    cover_path = None
    cover_source = None

    if fp_lower.endswith(".pdf"):
        cover_path = extract_cover_pdf(filepath, book_id)
    elif fp_lower.endswith(".epub"):
        cover_path = extract_cover_epub(filepath, book_id)

    if cover_path:
        cover_source = "local"
    else:
        cover_path = fetch_cover_openlibrary(filepath, book_id)
        if cover_path:
            cover_source = "openlibrary"

    if cover_path:
        from backend.db import get_conn
        conn = get_conn(db_path)
        conn.execute(
            "UPDATE books SET cover_path=?, cover_source=?, updated_at=datetime('now') WHERE id=?",
            (cover_path, cover_source, book_id),
        )
        conn.commit()

    return cover_path
