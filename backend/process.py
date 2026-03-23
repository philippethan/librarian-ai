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

POPPLER_PATH = os.getenv("POPPLER_PATH", r"C:/poppler/Library/bin")


# ---------------------------------------------------------------------------
# Extraction helpers
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


def extract_epub_text(filepath: str) -> str:
    """Extract plain text from first few content items of an EPUB."""
    try:
        from ebooklib import epub, ITEM_DOCUMENT
        from html.parser import HTMLParser

        class _TextExtractor(HTMLParser):
            def __init__(self):
                super().__init__()
                self.parts = []
            def handle_data(self, data):
                self.parts.append(data)
            def get_text(self):
                return " ".join(self.parts)

        book = epub.read_epub(filepath, options={"ignore_ncx": True})
        text_parts = []
        for item in list(book.get_items_of_type(ITEM_DOCUMENT))[:5]:
            parser = _TextExtractor()
            parser.feed(item.get_content().decode("utf-8", errors="ignore"))
            t = parser.get_text().strip()
            if t:
                text_parts.append(t)
        return "\n".join(text_parts)
    except Exception as exc:
        logger.warning("extract_epub_text failed for %s: %s", filepath, exc)
        return ""


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
    """Phase 1: hash file and check for duplicates."""
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
    """Phase 2: run the full extraction pipeline for a book already hashed."""
    conn = get_conn(db_path)
    row = conn.execute("SELECT status FROM books WHERE id=?", (book_id,)).fetchone()
    if not row or row["status"] != "pending":
        return

    conn.execute("UPDATE books SET status='processing' WHERE id=?", (book_id,))
    conn.commit()

    fp_lower = filepath.lower()

    # Step 1: Text extraction
    raw_text: str = ""
    epub_meta: dict = {}

    if fp_lower.endswith(".pdf"):
        raw_text = extract_pdfplumber(filepath)
    elif fp_lower.endswith(".epub"):
        epub_meta = extract_ebooklib(filepath)
        raw_text = extract_epub_text(filepath)

    text_extracted = len(raw_text) >= 100

    # Step 2: LLM passes
    llm_result = (
        call_ollama_sync(raw_text, filepath, "text")
        if text_extracted
        else {}
    )
    if llm_result:
        llm_result["extraction_method"] = "pdfplumber" if fp_lower.endswith(".pdf") else "epub_text"

    fn_result = call_ollama_sync("", filepath, "filename_heuristic")
    if fn_result:
        fn_result["extraction_method"] = "filename_heuristic"

    # Step 3: Merge
    results = [r for r in [llm_result, fn_result, epub_meta] if r]
    merged = merge_metadata(results) if results else {}

    # Step 4: Confidence score + status
    merged["confidence_score"] = compute_confidence(merged, text_extracted)
    merged["status"] = (
        "done"    if merged["confidence_score"] >= 0.4 else
        "partial" if merged["confidence_score"] >  0.0 else
        "error"
    )

    # Step 5: Write text cache (chat endpoint depends on this)
    text_cache_dir = Path(os.getenv("TEXT_CACHE_PATH", "./data/text_cache"))
    text_cache_dir.mkdir(parents=True, exist_ok=True)
    (text_cache_dir / f"{book_id}.txt").write_text(raw_text, encoding="utf-8")

    # Step 6: Cover extraction
    from backend.covers import extract_cover
    extract_cover(book_id, filepath, db_path)

    # Step 7: Debug JSON
    debug_dir = Path(os.getenv("DEBUG_PATH", "./data/debug"))
    debug_dir.mkdir(parents=True, exist_ok=True)
    debug_payload = {
        "book_id":  book_id,
        "filename": Path(filepath).name,
        "passes": {
            "llm":                llm_result,
            "filename_heuristic": fn_result,
            "epub_meta":          epub_meta,
        },
        "raw_text_length": len(raw_text),
        "merged":    merged,
        "timestamp": datetime.datetime.utcnow().isoformat(),
    }
    (debug_dir / f"{book_id}.json").write_text(
        json.dumps(debug_payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    # Step 8: Save to DB
    n_sources = sum(1 for r in [llm_result, fn_result] if r)
    merged["extraction_method"] = (
        "merged"             if n_sources > 1 else
        ("pdfplumber" if fp_lower.endswith(".pdf") else "epub_text") if llm_result else
        "ebooklib"           if epub_meta           else
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
