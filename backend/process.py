import datetime
import hashlib
import json
import logging
import os
from pathlib import Path

from backend.db import get_conn
from backend.extractors import compute_confidence, merge_metadata
from backend.llm import call_ollama_sync

logger = logging.getLogger(__name__)

ENABLE_OCR = os.getenv("ENABLE_OCR", "true").lower() == "true"
TESSERACT_PATH = os.getenv("TESSERACT_PATH", r"C:/Program Files/Tesseract-OCR/tesseract.exe")
POPPLER_PATH = os.getenv("POPPLER_PATH", r"C:/poppler/Library/bin")


# ---------------------------------------------------------------------------
# Extraction helpers (stubs — will be moved to their own modules later)
# ---------------------------------------------------------------------------

def extract_pdfplumber(filepath: str) -> str:
    """Extract text from first 3 pages of a PDF using pdfplumber."""
    try:
        import pdfplumber
        text_parts = []
        with pdfplumber.open(filepath) as pdf:
            for page in pdf.pages[:3]:
                t = page.extract_text()
                if t:
                    text_parts.append(t)
        return "\n".join(text_parts)
    except Exception as exc:
        logger.warning("extract_pdfplumber failed for %s: %s", filepath, exc)
        return ""


def extract_ocr(filepath: str) -> str:
    """OCR first 2 pages via pdf2image + pytesseract. Returns '' if ENABLE_OCR is false."""
    if not ENABLE_OCR:
        return ""
    try:
        from pdf2image import convert_from_path
        import pytesseract
        pytesseract.pytesseract.tesseract_cmd = TESSERACT_PATH
        pages = convert_from_path(
            filepath,
            first_page=1,
            last_page=2,
            dpi=150,
            poppler_path=POPPLER_PATH,
        )
        text_parts = []
        for page_img in pages:
            t = pytesseract.image_to_string(page_img)
            if t:
                text_parts.append(t)
        return "\n".join(text_parts)
    except Exception as exc:
        logger.warning("extract_ocr failed for %s: %s", filepath, exc)
        return ""


def extract_ebooklib(filepath: str) -> dict:
    """Extract dc:title, dc:creator, dc:language from an EPUB via ebooklib."""
    try:
        from ebooklib import epub
        book = epub.read_epub(filepath, options={"ignore_ncx": True})
        title = book.get_metadata("DC", "title")
        creator = book.get_metadata("DC", "creator")
        language = book.get_metadata("DC", "language")
        result = {"extraction_method": "ebooklib"}
        if title:
            result["title"] = title[0][0]
        if creator:
            result["author"] = creator[0][0]
        if language:
            lang = language[0][0]
            result["language"] = lang[:2].lower() if isinstance(lang, str) else None
        return result
    except Exception as exc:
        logger.warning("extract_ebooklib failed for %s: %s", filepath, exc)
        return {}


# ---------------------------------------------------------------------------
# Stub hooks for future modules
# ---------------------------------------------------------------------------

def enrich_book(book_id: int, db_path: str) -> None:
    """Open Library enrichment — not yet implemented."""
    logger.debug("enrich_book: not yet implemented (book_id=%d)", book_id)


def extract_cover(book_id: int, filepath: str, db_path: str) -> None:
    """Cover extraction — not yet implemented."""
    logger.debug("extract_cover: not yet implemented (book_id=%d)", book_id)


# ---------------------------------------------------------------------------
# _save_merged — SRS §1.4
# ---------------------------------------------------------------------------

def _save_merged(conn, book_id: int, merged: dict) -> None:
    # 1. Null-guard: replace empty strings with None before writing
    for field in (
        "title", "author", "language", "category", "subcategory",
        "difficulty", "description", "extraction_method",
    ):
        if merged.get(field) == "":
            merged[field] = None

    # 2. Pre-save diagnostic log
    logger.debug(
        "save_merged book=%d title=%r author=%r year=%r lang=%r score=%.4f method=%r",
        book_id,
        merged.get("title"),
        merged.get("author"),
        merged.get("year"),
        merged.get("language"),
        merged.get("confidence_score", 0),
        merged.get("extraction_method"),
    )

    conn.execute(
        """
        UPDATE books SET
            title=?, author=?, year=?, language=?,
            category=?, subcategory=?, difficulty=?, description=?,
            tags=?, extraction_method=?, confidence_score=?,
            status=?, updated_at=datetime('now')
        WHERE id=?
        """,
        (
            merged.get("title"),
            merged.get("author"),
            merged.get("year"),
            merged.get("language"),
            merged.get("category"),
            merged.get("subcategory"),
            merged.get("difficulty"),
            merged.get("description"),
            json.dumps(merged.get("tags", [])),
            merged.get("extraction_method"),
            merged.get("confidence_score"),
            merged.get("status"),
            book_id,
        ),
    )
    conn.commit()

    # 3. Post-save read-back check
    row = conn.execute(
        "SELECT title FROM books WHERE id=?", (book_id,)
    ).fetchone()
    if row and row["title"] is None and merged.get("title") is not None:
        logger.warning(
            "save_merged post-check: title NULL in DB after update "
            "for book_id=%d (expected %r). Possible write failure.",
            book_id,
            merged.get("title"),
        )


# ---------------------------------------------------------------------------
# Main pipeline — SRS §1.17
# ---------------------------------------------------------------------------

def hash_book_sync(book_id: int, filepath: str, db_path: str) -> None:
    """Phase 1: hash file and check for duplicates.

    Sets status='pending' if unique, status='duplicate' if a matching hash
    already exists in the DB.  Extraction is NOT run here.
    """
    conn = get_conn(db_path)
    file_bytes = Path(filepath).read_bytes()
    file_hash = hashlib.sha256(file_bytes).hexdigest()
    existing = conn.execute(
        "SELECT id FROM books WHERE file_hash=? AND id!=? AND dedup_dismissed=0",
        (file_hash, book_id),
    ).fetchone()
    if existing:
        conn.execute(
            "UPDATE books SET duplicate_of=?, status='duplicate', file_hash=? WHERE id=?",
            (existing["id"], file_hash, book_id),
        )
        conn.commit()
        logger.info("book_id=%d is a duplicate of book_id=%d — skipping extraction", book_id, existing["id"])
        return

    conn.execute("UPDATE books SET file_hash=?, status='pending' WHERE id=?", (file_hash, book_id))
    conn.commit()


def extract_book_sync(book_id: int, filepath: str, db_path: str) -> None:
    """Phase 2: run the full extraction pipeline for a book already hashed.

    Assumes hash_book_sync() has already been called and set status='pending'.
    Skips the book silently if it is no longer pending (e.g. was marked
    duplicate by a concurrent hash phase).
    """
    conn = get_conn(db_path)
    row = conn.execute("SELECT status FROM books WHERE id=?", (book_id,)).fetchone()
    if not row or row["status"] != "pending":
        return

    conn.execute("UPDATE books SET status='processing' WHERE id=?", (book_id,))
    conn.commit()

    # Step 1: Text extraction
    pdf_text: str = ""
    ocr_text: str | None = None
    epub_meta: dict = {}
    fp_lower = filepath.lower()

    if fp_lower.endswith(".pdf"):
        pdf_text = extract_pdfplumber(filepath)
        if len(pdf_text) < 100:
            ocr_text = extract_ocr(filepath)
    elif fp_lower.endswith(".epub"):
        epub_meta = extract_ebooklib(filepath)

    # Step 2: Run Ollama passes
    pdfplumber_result = (
        call_ollama_sync(pdf_text, filepath, "pdfplumber")
        if len(pdf_text) >= 100
        else {}
    )
    if pdfplumber_result:
        pdfplumber_result["extraction_method"] = "pdfplumber"

    ocr_result: dict | None = None
    if ocr_text:
        ocr_result = call_ollama_sync(ocr_text, filepath, "ocr")
        if ocr_result:
            ocr_result["extraction_method"] = "ocr"

    fn_result = call_ollama_sync("", filepath, "filename_heuristic")
    if fn_result:
        fn_result["extraction_method"] = "filename_heuristic"

    # Step 3: Merge
    results = [r for r in [pdfplumber_result, ocr_result, fn_result, epub_meta] if r]
    merged = merge_metadata(results) if results else {}

    # Step 4: Confidence score + status
    text_extracted = len(pdf_text) >= 100 or bool(ocr_text)
    merged["confidence_score"] = compute_confidence(merged, text_extracted)
    merged["status"] = (
        "done"    if merged["confidence_score"] >= 0.4 else
        "partial" if merged["confidence_score"] >  0.0 else
        "error"
    )

    # Step 5: Write text cache (chat endpoint depends on this)
    text_cache_dir = Path(os.getenv("TEXT_CACHE_PATH", "./data/text_cache"))
    text_cache_dir.mkdir(parents=True, exist_ok=True)
    cache_content = pdf_text or (ocr_text or "")
    (text_cache_dir / f"{book_id}.txt").write_text(cache_content, encoding="utf-8")

    # Step 6: Open Library enrichment (stub)
    enrich_book(book_id, db_path)

    # Step 7: Cover extraction (stub)
    extract_cover(book_id, filepath, db_path)

    # Step 8: Debug JSON
    debug_dir = Path(os.getenv("DEBUG_PATH", "./data/debug"))
    debug_dir.mkdir(parents=True, exist_ok=True)
    debug_payload = {
        "book_id":  book_id,
        "filename": Path(filepath).name,
        "passes": {
            "pdfplumber":         pdfplumber_result,
            "ocr":                ocr_result,
            "filename_heuristic": fn_result,
        },
        "pdfplumber_text_length": len(pdf_text),
        "ocr_text_length":        len(ocr_text) if ocr_text else 0,
        "merged":    merged,
        "timestamp": datetime.datetime.utcnow().isoformat(),
    }
    (debug_dir / f"{book_id}.json").write_text(
        json.dumps(debug_payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    # Step 9: Save to DB
    n_sources = sum(1 for r in [pdfplumber_result, ocr_result, fn_result] if r)
    merged["extraction_method"] = (
        "merged"             if n_sources > 1 else
        "pdfplumber"         if pdfplumber_result else
        "ocr"                if ocr_result        else
        "filename_heuristic"
    )
    _save_merged(conn, book_id, merged)
    logger.info(
        "extract_book_sync done: book_id=%d status=%s confidence=%.4f",
        book_id, merged.get("status"), merged.get("confidence_score", 0),
    )


def process_book_sync(book_id: int, filepath: str, db_path: str) -> None:
    """Full pipeline (hash + extract) — used by the reprocess endpoint."""
    hash_book_sync(book_id, filepath, db_path)
    extract_book_sync(book_id, filepath, db_path)
