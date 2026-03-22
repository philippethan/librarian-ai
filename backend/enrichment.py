import datetime
import json
import logging
import time

import httpx

from backend.db import get_conn

logger = logging.getLogger(__name__)

CURRENT_YEAR = datetime.datetime.now().year
_OL_SEARCH_URL = "https://openlibrary.org/search.json"
_OL_DELAY = 0.5  # seconds between concurrent calls


def _valid_year(y) -> bool:
    try:
        return 1800 <= int(y) <= CURRENT_YEAR + 1
    except (TypeError, ValueError):
        return False


def _valid_language(lang) -> bool:
    return isinstance(lang, str) and len(lang) == 2 and lang.isalpha()


def enrich_book(book_id: int, db_path: str) -> None:
    """Open Library enrichment — fills null fields from OL API."""
    conn = get_conn(db_path)
    row = conn.execute(
        "SELECT title, author, year, language, description, tags, confidence_score "
        "FROM books WHERE id=?",
        (book_id,),
    ).fetchone()

    if row is None:
        logger.warning("enrich_book: book %d not found", book_id)
        return

    book = dict(row)
    title = book.get("title")

    # Only call OL when title is non-null
    if not title:
        return

    year = book.get("year")
    language = book.get("language")
    author = book.get("author")
    confidence_score = book.get("confidence_score") or 0.0

    # Trigger: confidence_score < 0.8 OR any key field is null/invalid
    needs_enrichment = (
        confidence_score < 0.8
        or not _valid_year(year)
        or not _valid_language(language)
        or author is None
    )
    if not needs_enrichment:
        return

    params: dict = {"title": title, "limit": 1}
    if author:
        params["author"] = author

    try:
        time.sleep(_OL_DELAY)
        with httpx.Client(timeout=5.0) as client:
            resp = client.get(_OL_SEARCH_URL, params=params)
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        logger.warning("enrich_book: OL request failed for book %d: %s", book_id, exc)
        return

    docs = data.get("docs", [])
    if not docs:
        conn.execute(
            "UPDATE books SET ol_enriched=1 WHERE id=?", (book_id,)
        )
        conn.commit()
        return

    doc = docs[0]
    updates: dict = {}

    # year <- first_publish_year (only if current year is null or out-of-range)
    if not _valid_year(year):
        ol_year = doc.get("first_publish_year")
        if ol_year is not None and _valid_year(ol_year):
            updates["year"] = int(ol_year)

    # language <- language[0] (only if current language is null or invalid)
    if not _valid_language(language):
        ol_langs = doc.get("language", [])
        if ol_langs:
            ol_lang = ol_langs[0]
            if _valid_language(ol_lang):
                updates["language"] = ol_lang.lower()

    # description <- first_sentence.value (only if null)
    if not book.get("description"):
        first_sentence = doc.get("first_sentence")
        if isinstance(first_sentence, dict):
            val = first_sentence.get("value")
        elif isinstance(first_sentence, str):
            val = first_sentence
        else:
            val = None
        if val:
            updates["description"] = val

    # tags <- union with subject[:10], deduplicated, lowercased
    ol_subjects = [str(s).lower().strip() for s in doc.get("subject", [])[:10] if s]
    if ol_subjects:
        existing_raw = book.get("tags") or "[]"
        try:
            existing_tags = json.loads(existing_raw) if isinstance(existing_raw, str) else existing_raw
        except Exception:
            existing_tags = []
        if not isinstance(existing_tags, list):
            existing_tags = []
        merged = list({t.lower().strip() for t in existing_tags if t} | set(ol_subjects))
        updates["tags"] = json.dumps(merged)

    # author <- author_name[0] if author is null
    if not author:
        ol_authors = doc.get("author_name", [])
        if ol_authors:
            updates["author"] = ol_authors[0]

    updates["ol_enriched"] = 1

    set_clause = ", ".join(f"{k}=?" for k in updates)
    values = list(updates.values()) + [book_id]
    conn.execute(f"UPDATE books SET {set_clause} WHERE id=?", values)
    conn.commit()
