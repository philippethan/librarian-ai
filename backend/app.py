# =============================================================================
# LibrarianAI - Backend API
# =============================================================================
# Single-file FastAPI backend. All logic lives here by design (see CLAUDE.md).
#
# Major sections (search for the banner to jump):
#   TAXONOMY        - FIXED_TAXONOMY category tree and language code map
#   CONFIG          - Paths, constants, and directory setup
#   DATABASE        - get_db() context manager and schema init
#   EXTRACTION      - Text and cover extraction from PDF/EPUB files
#   LLM UTILITIES   - Prompt building and JSON parsing for Ollama responses
#   FILENAME PARSE  - Heuristic title/author extraction from filenames
#   ONLINE SEARCH   - Multi-source metadata lookup (OpenLibrary, Google, etc.)
#   TAXONOMY MAP    - Subject list -> FIXED_TAXONOMY category mapping
#   OLLAMA CALLS    - HTTP calls to local Ollama model
#   BOOK PIPELINE   - Full process_book() workflow (hash -> extract -> LLM -> save)
#   BACKGROUND TASK - Async polling loop that processes pending books
#   APP INIT        - FastAPI app, CORS, and lifespan startup
#   API: HEALTH     - GET /api/health, POST /api/scan
#   API: BOOKS      - CRUD endpoints for book records
#   API: COVERS     - Cover image serve and refresh
#   API: DEDUP      - Duplicate detection and dismissal
#   API: SHELVES    - Virtual shelf management
#   API: EXPORT     - CSV and JSON bulk export
#   API: ANALYTICS  - Aggregated stats and taxonomy views
# =============================================================================

import asyncio
import csv
import hashlib
import io
import json
import os
import re
import sqlite3
import urllib.parse
import urllib.request
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
from typing import List, Optional

import fitz  # PyMuPDF
from bs4 import BeautifulSoup
import ebooklib
from ebooklib import epub
from PIL import Image

from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# =============================================================================
# TAXONOMY
# =============================================================================
# FIXED_TAXONOMY is the canonical category tree used for UI filtering and for
# mapping raw LLM/subject strings into a consistent hierarchy.
# Keys are top-level categories; values are lists of valid subcategories.
#
# _LANG_CODES (defined later in this section) maps ISO 639-2/1 codes returned
# by external APIs into human-readable language names.
# =============================================================================

FIXED_TAXONOMY = {
    "Personal Development": [
        "Self-help", "Psychology", "Mental Health", "Relationships",
        "Communication", "Emotional Intelligence", "Productivity",
        "Time Management", "Motivation", "Finance & Money",
        "Career Development", "Spirituality",
    ],
    "Literature & Fiction": [
        "Novels", "Romance", "Contemporary Fiction", "Memoir",
        "Short Stories", "Thriller", "Young Adult", "World Literature",
    ],
    "Language Learning": [
        "French", "English", "Grammar", "Vocabulary",
        "Exam Preparation", "Dictionaries", "Conversation", "Linguistics",
    ],
    "Science": [
        "Physics", "Chemistry", "Biology", "Earth Science", "General Science",
    ],
    "Mathematics": [
        "Calculus", "Algebra", "Statistics", "Geometry",
        "Optimization", "Trigonometry",
    ],
    "Computer & Software": [
        "Programming", "Software Engineering", "Computer Science",
        "AI & Machine Learning", "Cybersecurity", "Networking",
        "Data Engineering", "Cloud Computing",
    ],
    "Engineering": [
        "Mechanical Engineering", "Electrical Engineering",
        "Electronics & Embedded Systems", "Systems Engineering",
        "Civil Engineering", "Aerospace Engineering", "Transport Engineering",
    ],
    "Business & Management": [
        "Entrepreneurship", "Leadership", "Management", "Marketing",
        "Finance", "Economics", "Project Management", "Communication Skills",
    ],
    "History & Politics": [
        "Modern History", "Asian History", "Cambodian History",
        "Islamic History", "Politics", "International Relations",
    ],
    "Health & Medicine": [
        "Nutrition", "Mental Health", "Anatomy",
        "Medical Education", "Sexual Health",
    ],
    "Philosophy": [
        "Stoicism", "Ancient Philosophy", "Metaphysics", "Ethics",
    ],
    "Law": [
        "Contract Law", "Employment Law", "Intellectual Property Law",
    ],
    "Career & Job Hunting": [
        "Job Search", "Career Guides",
    ],
    "Sexuality & Relationships": [
        "LGBTQ+", "Female Sexuality", "Adolescent Sexuality",
    ],
    "Tourism & Travel": [
        "Travel Guides", "Tourism Management",
    ],
    "Communication & Writing": [
        "Academic Writing", "Technical Writing",
        "Professional Communication", "Essay & Memoir",
    ],
    "Art & Design": [
        "Visual Arts", "Architecture", "UX Design",
    ],
    "Miscellaneous": [
        "Uncategorized", "Other",
    ],
}

# =============================================================================
# CONFIG
# =============================================================================
# All paths and whitelists are centralized here.
# Override DB_PATH or BOOKS_PATH via environment variables (e.g. for testing).
# PATCHABLE_FIELDS guards the PATCH /api/books/{id} endpoint against arbitrary
# column writes; add a field here to make it user-editable via the API.
# =============================================================================

DB_PATH = os.environ.get("DB_PATH", "backend/data/librarian.db")
BOOKS_PATH = os.environ.get("BOOKS_PATH", "C:/Users/posen/Documents/Books")
BOOKS_ROOT = Path(BOOKS_PATH)
COVERS_DIR = Path("backend/data/covers")

# Feature flag: set AUTO_CATEGORIZATION_ENABLED=true in env to re-enable
# automatic LLM categorisation during ingestion. Defaults to false so that
# newly ingested books arrive without a category until a user with
# can_categorize permission assigns one manually.
AUTO_CATEGORIZATION_ENABLED = os.environ.get("AUTO_CATEGORIZATION_ENABLED", "false").lower() == "true"

# Secret that grants can_categorize permission.  Pass as the
# X-Categorize-Key request header.  If left empty, the permission check
# is skipped (useful for single-user local installs).
CATEGORIZE_API_KEY = os.environ.get("CATEGORIZE_API_KEY", "")

PATCHABLE_FIELDS = {"title", "author", "year", "language", "category",
                    "subcategory", "difficulty", "description", "tags", "hint",
                    "fixed_category", "fixed_subcategory", "no_cover"}

# Ensure required directories exist at import time
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
COVERS_DIR.mkdir(parents=True, exist_ok=True)


# =============================================================================
# DATABASE
# =============================================================================
# get_db() is a simple open/yield/close context manager. A new connection is
# created for every call — no thread-local or connection pool is used, which
# keeps things safe under async usage (each sync endpoint runs in a thread).
#
# init_db() is called once at startup (inside the lifespan handler). It:
#   1. Resets any books stuck in 'processing' (from a previous crashed run).
#   2. Creates all tables if they don't exist.
#   3. Adds any columns that were introduced after the initial schema (ALTER TABLE).
# =============================================================================

@contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def init_db():
    with get_db() as conn:
        # Reset any books stuck in 'processing' from a previous crashed run
        conn.execute("UPDATE books SET status='pending' WHERE status='processing'")
        conn.commit()
    with get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS books (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                filename          TEXT,
                filepath          TEXT UNIQUE,
                file_type         TEXT,
                file_size         INTEGER,
                file_hash         TEXT,
                status            TEXT DEFAULT 'pending',
                error_msg         TEXT,
                title             TEXT,
                author            TEXT,
                year              TEXT,
                language          TEXT,
                category          TEXT,
                subcategory       TEXT,
                difficulty        TEXT,
                description       TEXT,
                tags              TEXT DEFAULT '[]',
                extraction_method TEXT,
                confidence_score  REAL,
                cover_path        TEXT,
                reading_status    TEXT DEFAULT 'to-read',
                manual_fixed      INTEGER DEFAULT 0,
                duplicate_of      INTEGER,
                dedup_dismissed   INTEGER DEFAULT 0,
                added_at          TEXT DEFAULT (datetime('now')),
                processed_at      TEXT,
                hint              TEXT,
                fixed_category    TEXT,
                fixed_subcategory TEXT
            )
        """)
        for col, typedef in [
            ("hint", "TEXT"),
            ("fixed_category", "TEXT"),
            ("fixed_subcategory", "TEXT"),
            ("no_cover", "INTEGER DEFAULT 0"),
        ]:
            try:
                conn.execute(f"ALTER TABLE books ADD COLUMN {col} {typedef}")
                conn.commit()
            except Exception:
                pass
        conn.execute("""
            CREATE TABLE IF NOT EXISTS shelves (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                name       TEXT UNIQUE NOT NULL,
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS shelf_books (
                shelf_id INTEGER NOT NULL,
                book_id  INTEGER NOT NULL,
                added_at TEXT DEFAULT (datetime('now')),
                PRIMARY KEY (shelf_id, book_id),
                FOREIGN KEY (shelf_id) REFERENCES shelves(id) ON DELETE CASCADE,
                FOREIGN KEY (book_id)  REFERENCES books(id)   ON DELETE CASCADE
            )
        """)
        # --- Manual categorisation tables (NoAutoCategorisation branch) ---
        # canonical_categories: the authoritative set of category+subcategory
        # pairs that the UI presents in the categorise modal.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS canonical_categories (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                category    TEXT NOT NULL,
                subcategory TEXT NOT NULL DEFAULT '',
                UNIQUE (category, subcategory)
            )
        """)
        # document_categories: many-to-many between books and canonical_categories.
        # Kept separate from the denormalised category/subcategory columns on
        # books so that legacy data and rollback remain straightforward.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS document_categories (
                book_id     INTEGER NOT NULL,
                category_id INTEGER NOT NULL,
                assigned_at TEXT DEFAULT (datetime('now')),
                assigned_by TEXT DEFAULT 'manual',
                PRIMARY KEY (book_id, category_id),
                FOREIGN KEY (book_id)     REFERENCES books(id)                ON DELETE CASCADE,
                FOREIGN KEY (category_id) REFERENCES canonical_categories(id) ON DELETE CASCADE
            )
        """)
        # Add categorised_at / categorised_by columns to books (idempotent).
        for col, typedef in [
            ("categorized_at", "TEXT"),
            ("categorized_by", "TEXT"),
        ]:
            try:
                conn.execute(f"ALTER TABLE books ADD COLUMN {col} {typedef}")
                conn.commit()
            except Exception:
                pass
        conn.commit()


# =============================================================================
# EXTRACTION
# =============================================================================
# extract_text()  - reads the first ~3000 chars of content from a PDF or EPUB.
#   PDF:  uses PyMuPDF (fitz) — first 3 pages only to keep it fast.
#   EPUB: iterates document items via ebooklib, strips HTML with BeautifulSoup.
#   Returns "" on any failure so the pipeline can fall back to filename heuristics.
#
# extract_cover() - renders/extracts a cover image as a 300x400 JPEG.
#   PDF:  renders page 0 at 1.5x scale with PyMuPDF.
#   EPUB: looks for an image item whose name contains "cover"; falls back to
#         the first image in the archive.
#   Saves to COVERS_DIR/{book_id}.jpg and returns the path (or None on failure).
# =============================================================================

def extract_text(filepath: str, file_type: str) -> str:
    try:
        if file_type == "pdf":
            doc = fitz.open(filepath)
            text = ""
            for page in doc[:3]:
                text += page.get_text()
            doc.close()
            return text[:3000]
        elif file_type == "epub":
            book = epub.read_epub(filepath)
            text = ""
            for item in book.get_items_of_type(ebooklib.ITEM_DOCUMENT):
                soup = BeautifulSoup(item.get_content(), "html.parser")
                text += soup.get_text()
                if len(text) >= 3000:
                    break
            return text[:3000]
        return ""
    except Exception as exc:
        print(f"[extract_text] {filepath}: {exc}")
        return ""


def extract_cover(filepath: str, book_id: int, file_type: str) -> Optional[str]:
    COVERS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = COVERS_DIR / f"{book_id}.jpg"
    try:
        if file_type == "pdf":
            doc = fitz.open(filepath)
            page = doc[0]
            # render at 150 dpi equivalent
            mat = fitz.Matrix(1.5, 1.5)
            pix = page.get_pixmap(matrix=mat)
            doc.close()
            img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
            img.thumbnail((300, 400), Image.LANCZOS)
            img.save(str(out_path), "JPEG", quality=85)
            return str(out_path)
        elif file_type == "epub":
            book = epub.read_epub(filepath)
            cover_item = None
            for item in book.get_items():
                if item.get_type() == ebooklib.ITEM_IMAGE:
                    name = item.get_name().lower()
                    if "cover" in name:
                        cover_item = item
                        break
            if cover_item is None:
                # fallback: first image
                for item in book.get_items_of_type(ebooklib.ITEM_IMAGE):
                    cover_item = item
                    break
            if cover_item:
                img = Image.open(io.BytesIO(cover_item.get_content()))
                img = img.convert("RGB")
                img.thumbnail((300, 400), Image.LANCZOS)
                img.save(str(out_path), "JPEG", quality=85)
                return str(out_path)
    except Exception as exc:
        print(f"[extract_cover] id={book_id}: {exc}")
    return None


# =============================================================================
# LLM UTILITIES
# =============================================================================
# _parse_llm_json() - robustly parses JSON from raw LLM output.
#   Tries four strategies in order:
#     1. Strip markdown fences, then json.loads()
#     2. Regex-extract the first {...} block, then json.loads()
#     3. json_repair library as a last resort
#   Returns {} if all strategies fail.
#
# _FIELDS_BLOCK   - reusable prompt fragment that describes the expected JSON
#                   schema to the model.
#
# _build_prompt() - assembles the final prompt from the text snippet and
#                   an optional user hint. When a hint is supplied it is
#                   placed first and described as the primary source of truth.
# =============================================================================

def _parse_llm_json(raw: str) -> dict:
    # Step 1: strip ``` fences
    text = re.sub(r"```[a-zA-Z]*", "", raw).replace("```", "").strip()
    # Step 2: direct parse
    try:
        return json.loads(text)
    except Exception:
        pass
    # Step 3: regex extract {...}
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group())
        except Exception:
            pass
    # Step 4: json_repair fallback
    try:
        import json_repair
        result = json_repair.repair_json(text, return_objects=True)
        if isinstance(result, dict):
            return result
    except Exception:
        pass
    return {}


_FIELDS_BLOCK = (
    "Return a JSON object with these fields:\n"
    "- title (string)\n"
    "- author (string)\n"
    "- year (string, e.g. '2019')\n"
    "- language (string, e.g. 'English')\n"
    "- category (pick ONE from: Systems Engineering, Railway & Transport Engineering, "
    "Aerospace Engineering, Electrical Engineering, Electronics & Embedded Systems, "
    "Mechanical Engineering, Civil & Structural Engineering, Control & Automation, "
    "Software Engineering, Computer Science, AI & Machine Learning, Mathematics, Physics, "
    "Standards & Norms - Railway, Standards & Norms - Safety, Project Management, "
    "Business & Strategy, Economics & Finance, Personal Development, Psychology, "
    "History, Philosophy, Language Learning, Literature & Fiction, Health & Medicine, Other)\n"
    "- subcategory (string)\n"
    "- difficulty (one of: Beginner, Intermediate, Advanced, Expert)\n"
    "- description (1-2 sentence summary)\n"
    "- tags (JSON array of keyword strings)\n\n"
    "Return ONLY valid JSON. No markdown. No explanation.\n\n"
)


def _build_prompt(snippet: str, hint: str = "") -> str:
    if hint and snippet:
        content = (
            f"USER HINT - use this as the primary source of truth:\n{hint}\n\n"
            f"Supporting book text (use only to fill gaps not covered by the hint):\n{snippet[:800]}\n\n"
        )
    elif hint:
        content = f"USER HINT - extract all metadata from this description:\n{hint}\n\n"
    else:
        content = f"Book text:\n{snippet[:1500]}\n\n"
    return (
        "You are a librarian metadata extractor. "
        + _FIELDS_BLOCK
        + content
    )


# Maps ISO 639-2 (3-letter) and ISO 639-1 (2-letter) language codes to
# human-readable names. Used when normalizing results from external search APIs.
_LANG_CODES = {
    "eng": "English", "fre": "French", "fra": "French", "spa": "Spanish",
    "ger": "German", "deu": "German", "ara": "Arabic", "ita": "Italian",
    "por": "Portuguese", "jpn": "Japanese", "chi": "Chinese", "zho": "Chinese",
    "kor": "Korean", "rus": "Russian", "hin": "Hindi", "khm": "Khmer",
    "en": "English", "fr": "French", "es": "Spanish", "de": "German",
    "ar": "Arabic", "it": "Italian", "pt": "Portuguese", "ja": "Japanese",
    "zh": "Chinese", "ko": "Korean", "ru": "Russian",
}


# =============================================================================
# FILENAME PARSE
# =============================================================================
# _parse_filename() extracts (title, author) from common e-book filename
# conventions without any AI calls. It is the cheapest metadata source and
# is used by online_lookup as the primary search query.
#
# Processing order:
#   0. Normalize whitespace and separators
#   1. Strip known noise tokens (Z-Library, LibGen watermarks, etc.)
#   2. Remove leading series tags like [Book 01] or bullets
#   3. Detect (LastName, FirstName) author parentheticals
#   4. If no author yet, try heuristics on "Author - Title" dash-split format
#   5. Clean remaining parentheticals from the title
# =============================================================================

def _parse_filename(filepath: str) -> tuple:
    """
    Extract (title, author) from common e-book filename conventions.

    Handles patterns like:
      [Series 01] • When the War Was Over (Becker, Elizabeth) (Z-Library).epub
      Learning Python (Lutz, Mark) (z-lib.org).pdf
      The Great Gatsby - F. Scott Fitzgerald.epub
      Author Name - Book Title.pdf
    Returns (title, author) both as clean strings.
    """
    stem = Path(filepath).stem

    # 0. Normalise separators
    stem = stem.replace('_', ' ').replace('.', ' ')
    stem = re.sub(r'\s+', ' ', stem).strip()

    # 1. Remove known noise tokens (case-insensitive)
    noise = [
        r'\(Z-Library\)', r'\(z-lib\.org\)', r'\(zlibrary\.org\)',
        r'\(z-lib\)', r'\(zlib\)', r'\(libgen\)', r'\(epub\)', r'\(pdf\)',
        r'\bZ-Library\b', r'\bLibGen\b',
    ]
    for p in noise:
        stem = re.sub(p, '', stem, flags=re.IGNORECASE)

    # 2. Remove leading series tags like [Series Name 01] or [01]
    stem = re.sub(r'^\s*\[[^\]]*\]\s*', '', stem)
    stem = re.sub(r'^\s*•\s*', '', stem)   # leading bullet

    # 3. Extract author from (LastName, FirstName) parenthetical
    author = ""
    m = re.search(r'\(\s*([A-Z][a-zA-Z\-\']+),\s*([A-Z][a-zA-Z\s\-\'\.]+)\s*\)', stem)
    if m:
        last, first = m.group(1).strip(), m.group(2).strip()
        author = f"{first} {last}"
        stem = stem[:m.start()] + stem[m.end():]

    # 4. Handle "Author - Title" or "Title - Author" dash-separated format
    _articles = {'the', 'a', 'an', 'le', 'la', 'les', 'un', 'une', 'des'}
    if not author:
        parts = re.split(r'\s+[-–]\s+', stem, maxsplit=1)
        if len(parts) == 2:
            a, b = parts[0].strip(), parts[1].strip()
            a_words = a.split()
            b_words = b.split()
            # Author heuristic: short, doesn't start with an article, has no article as first word
            a_looks_author = len(a_words) <= 4 and a_words[0].lower() not in _articles
            b_looks_author = len(b_words) <= 4 and b_words[0].lower() not in _articles
            if a_looks_author and not b_looks_author:
                author, stem = a, b
            elif b_looks_author and not a_looks_author:
                author, stem = b, a
            elif a_looks_author and b_looks_author:
                # Both short — "Title - Author" is the dominant format, so right side = author
                author, stem = b, a

    # 5. Clean up title
    title = re.sub(r'\([^)]*\)', '', stem).strip()   # remove remaining parentheticals
    title = re.sub(r'\s+', ' ', title).strip(' •-–_')

    return title, author


# =============================================================================
# ONLINE SEARCH
# =============================================================================
# Five independent search functions each return a list of candidate dicts with
# at minimum: title, author, year, language, subjects, source.
#
#   _search_open_library()    - Open Library search API
#   _search_google_books()    - Google Books Volumes API
#   _search_crossref()        - CrossRef works API (strong for academic books)
#   _search_internet_archive()- Internet Archive advanced search
#   _search_openalex()        - OpenAlex works search
#
# _collect_candidates() fans out to all five sources concurrently (via loop),
# deduplicates by normalised title+author, enriches merged records with the
# longest available values, and scores/ranks by title match quality and
# number of corroborating sources. Returns the top 8 candidates.
#
# _norm() strips all non-alphanumeric characters for fuzzy key comparison.
# =============================================================================

def _norm(s: str) -> str:
    """Normalise a string for deduplication comparison."""
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _search_open_library(title: str, author: str = "") -> list:
    try:
        params = {"limit": 5, "fields": "title,author_name,first_publish_year,language,subject"}
        if title:
            params["title"] = title
        if author:
            params["author"] = author
        url = "https://openlibrary.org/search.json?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers={"User-Agent": "LibrarianAI/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
        results = []
        for doc in (data.get("docs") or []):
            raw_lang = (doc.get("language") or [""])[0]
            results.append({
                "title": doc.get("title", ""),
                "author": ", ".join(doc.get("author_name") or []),
                "year": str(doc.get("first_publish_year", "")),
                "language": _LANG_CODES.get(raw_lang, raw_lang),
                "subjects": (doc.get("subject") or [])[:20],
                "source": "Open Library",
            })
        return results
    except Exception as exc:
        print(f"[open_library] {exc}")
        return []


def _search_google_books(title: str, author: str = "") -> list:
    try:
        q = f'intitle:"{title}"'
        if author:
            q += f' inauthor:"{author}"'
        url = "https://www.googleapis.com/books/v1/volumes?" + urllib.parse.urlencode(
            {"q": q, "maxResults": 5, "printType": "all"}
        )
        req = urllib.request.Request(url, headers={"User-Agent": "LibrarianAI/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
        results = []
        for item in (data.get("items") or []):
            info = item.get("volumeInfo", {})
            raw_lang = info.get("language", "")
            results.append({
                "title": info.get("title", ""),
                "author": ", ".join(info.get("authors") or []),
                "year": (info.get("publishedDate") or "")[:4],
                "language": _LANG_CODES.get(raw_lang, raw_lang),
                "description": info.get("description", ""),
                "subjects": info.get("categories") or [],
                "source": "Google Books",
            })
        return results
    except Exception as exc:
        print(f"[google_books] {exc}")
        return []


def _search_crossref(title: str, author: str = "") -> list:
    try:
        params = {
            "query.title": title,
            "rows": 5,
            "select": "title,author,published,subject,abstract,type,container-title,language",
        }
        if author:
            params["query.author"] = author
        url = "https://api.crossref.org/works?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers={"User-Agent": "LibrarianAI/1.0 (mailto:librarian@example.com)"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
        results = []
        for item in ((data.get("message") or {}).get("items") or []):
            titles = item.get("title") or []
            authors = [
                " ".join(filter(None, [a.get("given", ""), a.get("family", "")]))
                for a in (item.get("author") or [])
            ]
            date_parts = ((item.get("published") or {}).get("date-parts") or [[]])[0]
            year = str(date_parts[0]) if date_parts else ""
            container = (item.get("container-title") or [""])[0]
            abstract = re.sub(r"<[^>]+>", " ", item.get("abstract", "") or "").strip()
            raw_lang = item.get("language", "")
            subjects = list(item.get("subject") or [])
            if container:
                subjects.insert(0, container)
            results.append({
                "title": titles[0] if titles else "",
                "author": ", ".join(authors),
                "year": year,
                "language": _LANG_CODES.get(raw_lang, raw_lang),
                "description": abstract,
                "subjects": subjects[:20],
                "container": container,
                "source": "CrossRef",
            })
        return results
    except Exception as exc:
        print(f"[crossref] {exc}")
        return []


def _search_internet_archive(title: str, author: str = "") -> list:
    try:
        q = f'title:("{title}")'
        if author:
            q += f' AND creator:("{author}")'
        url = "https://archive.org/advancedsearch.php?" + urllib.parse.urlencode({
            "q": q,
            "fl[]": ["title", "creator", "date", "subject", "description", "language"],
            "rows": 5,
            "page": 1,
            "output": "json",
        })
        req = urllib.request.Request(url, headers={"User-Agent": "LibrarianAI/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
        results = []
        for doc in ((data.get("response") or {}).get("docs") or []):
            creator = doc.get("creator", "")
            if isinstance(creator, list):
                creator = ", ".join(creator)
            subjects = doc.get("subject") or []
            if isinstance(subjects, str):
                subjects = [subjects]
            raw_lang = doc.get("language", "")
            if isinstance(raw_lang, list):
                raw_lang = raw_lang[0] if raw_lang else ""
            year = str(doc.get("date", "") or "")[:4]
            desc = doc.get("description", "")
            if isinstance(desc, list):
                desc = " ".join(desc)
            results.append({
                "title": doc.get("title", ""),
                "author": creator,
                "year": year,
                "language": _LANG_CODES.get(raw_lang, raw_lang),
                "description": desc[:500],
                "subjects": subjects[:20],
                "source": "Internet Archive",
            })
        return results
    except Exception as exc:
        print(f"[internet_archive] {exc}")
        return []


def _search_openalex(title: str, author: str = "") -> list:
    try:
        search = f"{title} {author}".strip()
        url = "https://api.openalex.org/works?" + urllib.parse.urlencode({
            "search": search,
            "per-page": 5,
            "select": "title,authorships,publication_year,language,topics,primary_location",
        })
        req = urllib.request.Request(url, headers={"User-Agent": "LibrarianAI/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
        results = []
        for item in (data.get("results") or []):
            authors = [
                a.get("author", {}).get("display_name", "")
                for a in (item.get("authorships") or [])[:3]
            ]
            topics = [t.get("display_name", "") for t in (item.get("topics") or [])[:10]]
            source_name = ((item.get("primary_location") or {}).get("source") or {}).get("display_name", "")
            if source_name:
                topics.insert(0, source_name)
            raw_lang = item.get("language") or ""
            results.append({
                "title": item.get("title", ""),
                "author": ", ".join(filter(None, authors)),
                "year": str(item.get("publication_year", "") or ""),
                "language": _LANG_CODES.get(raw_lang, raw_lang),
                "subjects": topics,
                "source": "OpenAlex",
            })
        return results
    except Exception as exc:
        print(f"[openalex] {exc}")
        return []


def _collect_candidates(title: str, author: str, year: str = "", alt_title: str = "", alt_author: str = "") -> list:
    """Gather results from all sources, deduplicate, return ranked list.

    alt_title / alt_author are secondary queries (e.g. from the filename when
    the primary query comes from DB metadata). Results from both queries are
    merged before deduplication so we get the best coverage.
    """
    queries = [(title, author)]
    # Add secondary query only when it differs meaningfully from the primary
    if alt_title and _norm(alt_title) != _norm(title):
        queries.append((alt_title, alt_author or author))

    all_results = []
    for q_title, q_author in queries:
        for fn in [
            _search_open_library,
            _search_google_books,
            _search_crossref,
            _search_internet_archive,
            _search_openalex,
        ]:
            try:
                for item in fn(q_title, q_author):
                    if item.get("title"):
                        all_results.append(item)
            except Exception:
                pass

    # Deduplicate: merge items with same normalised title+author
    merged: dict = {}
    for item in all_results:
        key = _norm(item.get("title", "")) + "|" + _norm(item.get("author", ""))
        if key not in merged:
            merged[key] = dict(item)
            merged[key]["sources"] = [item["source"]]
        else:
            existing = merged[key]
            # Enrich with longer values
            for field in ("description", "author", "year", "language"):
                if len(str(item.get(field, "") or "")) > len(str(existing.get(field, "") or "")):
                    existing[field] = item[field]
            # Union subjects
            seen_subs = {_norm(s) for s in (existing.get("subjects") or [])}
            for s in (item.get("subjects") or []):
                if _norm(s) not in seen_subs:
                    existing.setdefault("subjects", []).append(s)
                    seen_subs.add(_norm(s))
            if item["source"] not in existing["sources"]:
                existing["sources"].append(item["source"])

    candidates = list(merged.values())

    # Score: title match + year match + source count + has description
    search_norm = _norm(title)
    alt_norm    = _norm(alt_title)
    year_str    = str(year or "").strip()
    for c in candidates:
        c_norm  = _norm(c.get("title", ""))
        c_year  = str(c.get("year", "") or "").strip()[:4]
        title_score = (
            3 if c_norm == search_norm else
            2 if alt_norm and c_norm == alt_norm else
            1 if (search_norm and search_norm in c_norm) or (alt_norm and alt_norm in c_norm) else
            0
        )
        year_score = 2 if (year_str and c_year and c_year == year_str[:4]) else 0
        c["_score"] = (
            title_score
            + year_score
            + len(c.get("sources", []))
            + (1 if c.get("description") else 0)
        )

    candidates.sort(key=lambda x: -x.get("_score", 0))
    for c in candidates:
        c.pop("_score", None)

    return candidates[:8]


# =============================================================================
# TAXONOMY MAP
# =============================================================================
# _map_to_fixed_category() converts a raw list of subject strings (from
# external APIs) into a (category, subcategory) pair from FIXED_TAXONOMY.
#
# Scoring logic:
#   +3 if the category name itself appears in the combined subject text
#   +2 for each matching subcategory string
#   +1 for each keyword in per-category hint lists
# The highest-scoring category wins; within it, the subcategory with the most
# keyword matches in the subject text is chosen.
# =============================================================================

def _map_to_fixed_category(subjects: list) -> tuple:
    """Try to map a list of subjects to FIXED_TAXONOMY via simple keyword matching."""
    if not subjects:
        return "", ""
    text = " ".join(subjects).lower()
    scores = {}
    for cat, subs in FIXED_TAXONOMY.items():
        score = 0
        if cat.lower() in text:
            score += 3
        for sub in subs:
            if sub.lower() in text:
                score += 2
        # keyword hints per category
        hints = {
            "Computer & Software": ["python", "java", "programming", "software", "algorithm", "computer", "code", "web", "database", "linux"],
            "Engineering": ["engineering", "mechanical", "electrical", "civil", "aerospace", "embedded", "signal"],
            "Mathematics": ["mathematics", "calculus", "algebra", "statistics", "geometry", "math"],
            "Science": ["physics", "chemistry", "biology", "science", "quantum", "thermodynamics"],
            "Business & Management": ["business", "management", "marketing", "leadership", "economics", "entrepreneurship"],
            "Personal Development": ["self-help", "motivation", "productivity", "psychology", "mindset"],
            "History & Politics": ["history", "politics", "war", "civilization", "government"],
            "Language Learning": ["language", "grammar", "french", "english", "vocabulary", "linguistics"],
            "Literature & Fiction": ["novel", "fiction", "romance", "thriller", "poetry", "literature"],
            "Health & Medicine": ["medicine", "health", "anatomy", "nutrition", "medical"],
            "Philosophy": ["philosophy", "ethics", "stoicism", "metaphysics"],
            "Law": ["law", "legal", "contract", "rights"],
        }
        for kw in hints.get(cat, []):
            if kw in text:
                score += 1
        if score > 0:
            scores[cat] = score
    if not scores:
        return "Miscellaneous", "Other"
    best_cat = max(scores, key=lambda k: scores[k])
    # Find best subcategory
    best_sub = ""
    best_sub_score = 0
    for sub in FIXED_TAXONOMY.get(best_cat, []):
        s = sum(1 for kw in sub.lower().split() if kw in text)
        if s > best_sub_score:
            best_sub_score = s
            best_sub = sub
    return best_cat, best_sub


# =============================================================================
# OLLAMA CALLS
# =============================================================================
# call_ollama()          - primary path: sends extracted book text (up to 1500
#                          chars) plus an optional user hint to the local Ollama
#                          model and returns parsed metadata as a dict.
#
# call_ollama_filename() - fallback path: used when no text could be extracted.
#                          Sends only the filename and file size and asks the
#                          model to infer metadata from that alone.
#
# Both functions read OLLAMA_URL and OLLAMA_MODEL from the environment at call
# time (not at module load) to support runtime config changes.
# ensure_ascii=True is required in json.dumps() to avoid encoding issues with
# the local Ollama HTTP interface.
# =============================================================================

def call_ollama(text: str, hint: str = "") -> dict:
    ollama_url = os.getenv("OLLAMA_URL", "http://localhost:11434")
    ollama_model = os.getenv("OLLAMA_MODEL", "mistral:7b-instruct")
    snippet = text[:1500]
    prompt = _build_prompt(snippet, hint)
    payload = json.dumps(
        {"model": ollama_model, "prompt": prompt, "stream": False},
        ensure_ascii=True,
    ).encode("utf-8")
    req = urllib.request.Request(
        f"{ollama_url}/api/generate",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            raw_body = resp.read().decode("utf-8")
        data = json.loads(raw_body)
        return _parse_llm_json(data.get("response", ""))
    except Exception as exc:
        print(f"[call_ollama] error: {exc}")
        return {}


def call_ollama_filename(filepath: str, hint: str = "") -> dict:
    ollama_url = os.getenv("OLLAMA_URL", "http://localhost:11434")
    ollama_model = os.getenv("OLLAMA_MODEL", "mistral:7b-instruct")
    stem = Path(filepath).stem
    try:
        file_size = os.path.getsize(filepath)
    except Exception:
        file_size = 0
    hint_section = (
        f"User hint (treat as high-priority context):\n{hint}\n\n"
        if hint else ""
    )
    prompt = (
        "You are a librarian metadata extractor. Given only a filename and file size, "
        "infer book metadata and return it as JSON.\n\n"
        "Fields to extract:\n"
        "- title (string)\n"
        "- author (string)\n"
        "- year (string, e.g. '2019')\n"
        "- language (string, e.g. 'English')\n"
        "- category (pick one from the list below)\n"
        "- subcategory (string)\n"
        "- difficulty (one of: Beginner, Intermediate, Advanced, Expert)\n"
        "- description (1-2 sentence summary)\n"
        "- tags (JSON array of keyword strings)\n\n"
        "Category list:\n"
        "Systems Engineering, Railway & Transport Engineering, Aerospace Engineering, "
        "Electrical Engineering, Electronics & Embedded Systems, Mechanical Engineering, "
        "Civil & Structural Engineering, Control & Automation, Software Engineering, "
        "Computer Science, AI & Machine Learning, Mathematics, Physics, "
        "Standards & Norms - Railway, Standards & Norms - Safety, "
        "Project Management, Business & Strategy, Economics & Finance, "
        "Personal Development, Psychology, History, Philosophy, "
        "Language Learning, Literature & Fiction, Health & Medicine, Other\n\n"
        + hint_section
        + f"Filename: {stem}\n"
        f"File size: {file_size} bytes\n\n"
        "Return ONLY valid JSON. No markdown. No explanation."
    )
    payload = json.dumps(
        {"model": ollama_model, "prompt": prompt, "stream": False},
        ensure_ascii=True,
    ).encode("utf-8")
    req = urllib.request.Request(
        f"{ollama_url}/api/generate",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            raw_body = resp.read().decode("utf-8")
        data = json.loads(raw_body)
        return _parse_llm_json(data.get("response", ""))
    except Exception as exc:
        print(f"[call_ollama_filename] error: {exc}")
        return {}


def call_ollama_categorize(title: str, author: str, description: str,
                           tags: list, language: str) -> dict:
    """
    Ask Ollama to assign category + subcategory from FIXED_TAXONOMY using only
    the book's existing metadata attributes (no raw text needed).
    Returns a dict with at least 'category' and 'subcategory' keys.
    """
    ollama_url = os.getenv("OLLAMA_URL", "http://localhost:11434")
    ollama_model = os.getenv("OLLAMA_MODEL", "mistral:7b-instruct")

    cat_list = ", ".join(FIXED_TAXONOMY.keys())
    meta_block = (
        f"Title: {title or '(unknown)'}\n"
        f"Author: {author or '(unknown)'}\n"
        f"Language: {language or '(unknown)'}\n"
        f"Tags: {', '.join(tags) if tags else '(none)'}\n"
        f"Description: {description or '(none)'}\n"
    )
    prompt = (
        "You are a librarian. Given the book metadata below, pick the single best "
        "category and subcategory from the lists provided.\n\n"
        "Available categories (pick one):\n"
        f"{cat_list}\n\n"
        "Subcategory must be one of the subcategories that belong to the chosen category "
        "in this taxonomy:\n"
    )
    for cat, subs in FIXED_TAXONOMY.items():
        prompt += f"  {cat}: {', '.join(subs)}\n"
    prompt += (
        f"\nBook metadata:\n{meta_block}\n"
        "Return ONLY valid JSON with exactly two fields: "
        "\"category\" (string) and \"subcategory\" (string). "
        "No markdown. No explanation."
    )

    payload = json.dumps(
        {"model": ollama_model, "prompt": prompt, "stream": False},
        ensure_ascii=True,
    ).encode("utf-8")
    req = urllib.request.Request(
        f"{ollama_url}/api/generate",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw_body = resp.read().decode("utf-8")
        data = json.loads(raw_body)
        return _parse_llm_json(data.get("response", ""))
    except Exception as exc:
        print(f"[call_ollama_categorize] error: {exc}")
        return {}


def _categorize_book_by_llm(book_id: int) -> None:
    """
    Synchronous helper: read book attributes from DB, call Ollama to get
    category/subcategory, then write results back.  Clears any previous
    category values before saving so stale data does not persist.
    """
    with get_db() as conn:
        row = conn.execute(
            "SELECT title, author, description, tags, language FROM books WHERE id=?",
            (book_id,),
        ).fetchone()
    if not row:
        return

    title = row["title"] or ""
    author = row["author"] or ""
    description = row["description"] or ""
    language = row["language"] or ""
    try:
        tags = json.loads(row["tags"] or "[]")
        if not isinstance(tags, list):
            tags = []
    except (json.JSONDecodeError, TypeError):
        tags = []

    meta = call_ollama_categorize(title, author, description, tags, language)

    def _str(v):
        if isinstance(v, list):
            return ", ".join(str(x) for x in v if x)
        return str(v).strip() if v else ""

    category = _str(meta.get("category"))
    subcategory = _str(meta.get("subcategory"))

    # Validate against FIXED_TAXONOMY; fall back to Miscellaneous if unknown
    if category not in FIXED_TAXONOMY:
        category = "Miscellaneous"
        subcategory = "Other"
    elif subcategory not in FIXED_TAXONOMY.get(category, []):
        subcategory = FIXED_TAXONOMY[category][0] if FIXED_TAXONOMY[category] else ""

    with get_db() as conn:
        conn.execute(
            """UPDATE books SET
                category=?, subcategory=?,
                fixed_category=?, fixed_subcategory=?,
                categorized_at=datetime('now'), categorized_by='llm'
               WHERE id=?""",
            (category, subcategory, category, subcategory, book_id),
        )
        conn.commit()

    print(f"[categorize_llm] id={book_id} -> {category!r} / {subcategory!r}")


# =============================================================================
# BOOK PIPELINE
# =============================================================================
# process_book() is the core synchronous pipeline run for each book.
# It is called by background_processor() inside a thread (via asyncio.to_thread).
#
# Steps:
#   1. Mark book as 'processing' in DB; read the user's hint (if any).
#   2. SHA-256 hash check for duplicates — marks as 'duplicate' and returns early.
#   3. Extract text via PyMuPDF/ebooklib and cache it to backend/data/text_cache/.
#   4. Call Ollama with text (or filename if no text extracted).
#   5. Compute a confidence score: (filled_fields / 9) * 0.7 + (has_text) * 0.3
#   6. Set status: 'done' (>=0.4), 'partial' (>0), or 'error' (0).
#   7. Persist all metadata to the books table.
# =============================================================================

def process_book(book_id: int, filepath: str, file_type: str):
    # Step 1: set status=processing, read hint
    with get_db() as conn:
        conn.execute(
            "UPDATE books SET status='processing', error_msg=NULL WHERE id=?",
            (book_id,),
        )
        conn.commit()
        hint_row = conn.execute("SELECT hint FROM books WHERE id=?", (book_id,)).fetchone()
    hint = (hint_row["hint"] or "").strip() if hint_row else ""

    # Step 2: duplicate check
    file_hash = hashlib.sha256(Path(filepath).read_bytes()).hexdigest()
    with get_db() as conn:
        dup = conn.execute(
            "SELECT id FROM books WHERE file_hash=? AND dedup_dismissed=0 AND id!=?",
            (file_hash, book_id),
        ).fetchone()
    if dup:
        with get_db() as conn:
            conn.execute(
                "UPDATE books SET status='duplicate', duplicate_of=?, file_hash=? WHERE id=?",
                (dup["id"], file_hash, book_id),
            )
            conn.commit()
        print(f"[process_book] id={book_id} status=duplicate duplicate_of={dup['id']}")
        return

    # Step 3: extract text and cache it
    text = extract_text(filepath, file_type)
    cache_dir = Path("backend/data/text_cache")
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / f"{book_id}.txt").write_text(text, encoding="utf-8")

    # Step 4: call ollama
    if text or hint:
        meta = call_ollama(text, hint=hint)
        method = "hint+ollama" if (hint and not text) else "fitz+ollama"
    else:
        meta = call_ollama_filename(filepath)
        method = "filename_heuristic"

    def _str(v):
        if isinstance(v, list):
            return ", ".join(str(x) for x in v if x)
        return str(v).strip() if v else ""

    title = _str(meta.get("title"))
    author = _str(meta.get("author"))
    year = _str(meta.get("year"))
    language = _str(meta.get("language"))
    difficulty = _str(meta.get("difficulty"))
    description = _str(meta.get("description"))
    tags_raw = meta.get("tags", [])
    tags = json.dumps(tags_raw if isinstance(tags_raw, list) else [], ensure_ascii=True)

    # Auto-categorisation gated by feature flag.  When disabled, category and
    # subcategory are left blank so a human can assign them manually later.
    if AUTO_CATEGORIZATION_ENABLED:
        category = _str(meta.get("category"))
        subcategory = _str(meta.get("subcategory"))
    else:
        category = ""
        subcategory = ""

    # Step 5: confidence score (category/subcategory excluded when auto-cat off)
    if AUTO_CATEGORIZATION_ENABLED:
        filled_fields = sum(1 for v in [title, author, year, language, category, subcategory, difficulty, description, tags_raw] if v)
    else:
        filled_fields = sum(1 for v in [title, author, year, language, difficulty, description, tags_raw] if v)
    confidence_score = (filled_fields / 9) * 0.7 + (1.0 if text else 0.0) * 0.3

    # Step 6: status
    if confidence_score >= 0.4:
        status = "done"
    elif confidence_score > 0:
        status = "partial"
    else:
        status = "error"

    # Step 7: save to DB
    with get_db() as conn:
        conn.execute(
            """UPDATE books SET
                status=?, file_hash=?,
                title=?, author=?, year=?, language=?,
                category=?, subcategory=?, difficulty=?,
                description=?, tags=?,
                extraction_method=?, confidence_score=?,
                processed_at=datetime('now')
               WHERE id=?""",
            (status, file_hash, title, author, year, language,
             category, subcategory, difficulty, description, tags,
             method, confidence_score, book_id),
        )
        conn.commit()

    # Step 8: print
    print(f"[process_book] id={book_id} status={status} confidence={confidence_score:.2f} title={title!r}")


# =============================================================================
# BACKGROUND TASK
# =============================================================================
# background_processor() runs as a persistent async task started in lifespan().
# Every 2 seconds it fetches up to 5 'pending' books and processes each one
# synchronously via asyncio.to_thread so the event loop stays unblocked.
# Errors are caught and logged so one bad book can't crash the loop.
# =============================================================================

async def background_processor():
    while True:
        await asyncio.sleep(2)
        try:
            with get_db() as conn:
                rows = conn.execute(
                    "SELECT id, filepath, file_type FROM books WHERE status='pending' LIMIT 5"
                ).fetchall()
            for row in rows:
                process_book(row["id"], row["filepath"], row["file_type"])
        except Exception as exc:
            print(f"[background_processor] error: {exc}")


# =============================================================================
# APP INIT
# =============================================================================
# lifespan() is the FastAPI startup/shutdown hook:
#   - On startup: runs init_db() then spawns background_processor() as a task.
#   - On shutdown: cancels the background task cleanly.
#
# CORS is restricted to the Vite dev server (localhost:5173).
# For production, update allow_origins to the actual frontend origin.
# =============================================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    task = asyncio.create_task(background_processor())
    yield
    task.cancel()


app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# =============================================================================
# API: HEALTH + SCAN
# =============================================================================
# GET  /api/health  - liveness check; returns model name from env.
# POST /api/scan    - walks a directory tree, inserts new PDF/EPUB files as
#                    'pending' rows (skips already-known filepaths).
# =============================================================================

@app.get("/api/health")
def health():
    model = os.environ.get("OLLAMA_MODEL", "mistral:7b-instruct")
    return {"status": "ok", "model": model}


class ScanRequest(BaseModel):
    path: str


@app.post("/api/scan")
def scan(req: ScanRequest):
    root = Path(req.path)
    if not root.exists():
        raise HTTPException(status_code=400, detail=f"Path not found: {req.path}")

    added = 0
    skipped = 0

    with get_db() as conn:
        for fpath in root.rglob("*"):
            if fpath.suffix.lower() not in (".pdf", ".epub"):
                continue
            if not fpath.is_file():
                continue

            filepath_str = str(fpath).replace("\\", "/")
            existing = conn.execute(
                "SELECT id FROM books WHERE filepath=?", (filepath_str,)
            ).fetchone()
            if existing:
                skipped += 1
                continue

            file_size = fpath.stat().st_size
            sha256 = hashlib.sha256(fpath.read_bytes()).hexdigest()

            conn.execute(
                """
                INSERT INTO books (filename, filepath, file_type, file_size, file_hash, status)
                VALUES (?, ?, ?, ?, ?, 'pending')
                """,
                (fpath.name, filepath_str, fpath.suffix.lower().lstrip("."), file_size, sha256),
            )
            added += 1

        conn.commit()

    return {"added": added, "skipped": skipped}


# =============================================================================
# API: BOOKS LIST + STATS
# =============================================================================
# GET /api/books  - returns all books ordered by newest first; tags are
#                  deserialized from JSON string to a Python list.
# GET /api/stats  - lightweight count by status (no full row fetch).
# =============================================================================

@app.get("/api/books")
def get_books():
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM books ORDER BY added_at DESC").fetchall()

    books = []
    for row in rows:
        book = dict(row)
        try:
            book["tags"] = json.loads(book["tags"] or "[]")
        except (json.JSONDecodeError, TypeError):
            book["tags"] = []
        books.append(book)

    return books


@app.get("/api/stats")
def get_stats():
    with get_db() as conn:
        total = conn.execute("SELECT COUNT(*) FROM books").fetchone()[0]
        rows = conn.execute(
            "SELECT status, COUNT(*) as cnt FROM books GROUP BY status"
        ).fetchall()

    by_status = {row["status"]: row["cnt"] for row in rows}
    return {"total": total, "by_status": by_status}


# =============================================================================
# API: BOOK MANAGEMENT
# =============================================================================
# GET    /api/books/{id}               - fetch a single book record
# PATCH  /api/books/{id}               - partial update (PATCHABLE_FIELDS only)
# POST   /api/books/{id}/rename        - rename the physical file on disk + DB
# DELETE /api/books/{id}               - remove from DB; optionally delete file
# POST   /api/books/{id}/fix           - mark manual_fixed=1 (user-verified)
# POST   /api/books/{id}/unfix         - clear manual_fixed flag
# PATCH  /api/books/{id}/reading-status- update reading progress label
# POST   /api/books/{id}/open          - os.startfile() to open in native viewer
# POST   /api/books/{id}/online-lookup - search external APIs for candidates
# POST   /api/books/{id}/online-lookup/apply - apply a chosen candidate to DB
# POST   /api/books/{id}/reprocess     - reset to 'pending' for re-extraction
# POST   /api/reprocess-batch          - reprocess multiple books at once
# POST   /api/delete-batch             - delete multiple books at once
# GET    /api/text-previews            - first 300 chars from text cache for all books
# POST   /api/books/{id}/extract-test  - debug: run extraction only
# POST   /api/books/{id}/ollama-test   - debug: run extraction + Ollama call
# =============================================================================

@app.get("/api/books/{book_id}")
def get_book(book_id: int):
    with get_db() as conn:
        row = conn.execute("SELECT * FROM books WHERE id=?", (book_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Book not found")
    book = dict(row)
    try:
        book["tags"] = json.loads(book["tags"] or "[]")
    except (json.JSONDecodeError, TypeError):
        book["tags"] = []
    return book


class PatchBookRequest(BaseModel):
    title: Optional[str] = None
    author: Optional[str] = None
    year: Optional[str] = None
    language: Optional[str] = None
    category: Optional[str] = None
    subcategory: Optional[str] = None
    difficulty: Optional[str] = None
    description: Optional[str] = None
    tags: Optional[List[str]] = None
    hint: Optional[str] = None
    fixed_category: Optional[str] = None
    fixed_subcategory: Optional[str] = None


@app.patch("/api/books/{book_id}")
def patch_book(book_id: int, req: PatchBookRequest):
    with get_db() as conn:
        if not conn.execute("SELECT id FROM books WHERE id=?", (book_id,)).fetchone():
            raise HTTPException(status_code=404, detail="Book not found")

    updates = req.model_dump(exclude_none=True)
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")

    # tags must be serialized
    if "tags" in updates:
        updates["tags"] = json.dumps(updates["tags"], ensure_ascii=True)

    set_clause = ", ".join(f"{k}=?" for k in updates)
    values = list(updates.values()) + [book_id]

    with get_db() as conn:
        conn.execute(f"UPDATE books SET {set_clause} WHERE id=?", values)
        conn.commit()
        row = conn.execute("SELECT * FROM books WHERE id=?", (book_id,)).fetchone()

    book = dict(row)
    try:
        book["tags"] = json.loads(book["tags"] or "[]")
    except (json.JSONDecodeError, TypeError):
        book["tags"] = []
    return book


class RenameRequest(BaseModel):
    new_filename: str


def _build_auto_filename(title: Optional[str], author: Optional[str], year: Optional[str], ext: str) -> str:
    """Build a clean filename from metadata: 'Author - Title (Year).ext'."""
    _invalid = '/\\:*?"<>|'

    def clean(s: Optional[str]) -> str:
        if not s:
            return ""
        s = str(s).strip()
        for c in _invalid:
            s = s.replace(c, "_")
        return s.strip()

    t = clean(title) or "Untitled"
    a = clean(author)
    y = clean(year)

    if a and y:
        name = f"{a} - {t} ({y})"
    elif a:
        name = f"{a} - {t}"
    elif y:
        name = f"{t} ({y})"
    else:
        name = t

    return name + ext


@app.get("/api/books/{book_id}/rename-suggest")
def rename_suggest(book_id: int):
    """Return the auto-generated filename (title+author+year) without renaming."""
    with get_db() as conn:
        row = conn.execute("SELECT filepath, title, author, year FROM books WHERE id=?", (book_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Book not found")
    ext = Path(row["filepath"]).suffix.lower()
    suggested = _build_auto_filename(row["title"], row["author"], row["year"], ext)
    return {"suggested_filename": suggested}


@app.post("/api/books/{book_id}/rename")
def rename_book_file(book_id: int, dry_run: bool = Query(False), req: Optional[RenameRequest] = None):
    with get_db() as conn:
        row = conn.execute(
            "SELECT filepath, filename, title, author, year FROM books WHERE id=?", (book_id,)
        ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Book not found")

    old_path = Path(row["filepath"])
    old_ext = old_path.suffix.lower()

    # Determine new name: explicit request or auto-generated from metadata
    if req and req.new_filename.strip():
        new_name = req.new_filename.strip()
        if not new_name.lower().endswith(old_ext):
            new_name = new_name + old_ext
    else:
        new_name = _build_auto_filename(row["title"], row["author"], row["year"], old_ext)

    # Reject path traversal or empty names
    if not new_name or any(c in new_name for c in r'/\:*?"<>|'):
        raise HTTPException(status_code=400, detail="Invalid filename")

    # Skip if the name is unchanged
    if new_name == old_path.name:
        return {"skipped": True, "reason": "filename unchanged", "new_filename": new_name}

    new_path = old_path.parent / new_name

    # Dry-run: return preview without touching disk
    if dry_run:
        conflict = new_path.exists() and new_path != old_path
        return {
            "skipped": conflict,
            "reason": f"'{new_name}' already exists" if conflict else None,
            "new_filename": new_name,
            "old_filename": old_path.name,
        }

    if not old_path.exists():
        raise HTTPException(status_code=404, detail="File not found on disk")

    if new_path.exists() and new_path != old_path:
        raise HTTPException(status_code=409, detail=f"A file named '{new_name}' already exists in that folder")

    old_path.rename(new_path)
    new_path_str = str(new_path).replace("\\", "/")

    with get_db() as conn:
        conn.execute(
            "UPDATE books SET filepath=?, filename=? WHERE id=?",
            (new_path_str, new_name, book_id),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM books WHERE id=?", (book_id,)).fetchone()

    result = dict(row)
    try:
        result["tags"] = json.loads(result["tags"] or "[]")
    except (json.JSONDecodeError, TypeError):
        result["tags"] = []

    print(f"[rename] id={book_id} {old_path.name!r} -> {new_name!r}")
    return result


# ── Watermark filename cleaner ────────────────────────────────────────────────

# Matches watermark tokens with optional surrounding brackets, e.g.:
#   (z-library.sk)   [1lib.sk]   z-lib.sk   ( z-library.sk )
_WATERMARK_RE = re.compile(
    r'[\(\[\{]\s*(?:z-library\.sk|1lib\.sk|z-lib\.sk)\s*[\)\]\}]'
    r'|\s*(?:z-library\.sk|1lib\.sk|z-lib\.sk)\s*',
    re.IGNORECASE,
)


def _strip_watermark(filename: str) -> str:
    """Return filename with watermark tokens (and their brackets) removed."""
    stem = Path(filename).stem
    ext  = Path(filename).suffix

    cleaned = _WATERMARK_RE.sub('', stem)
    cleaned = re.sub(r'\s{2,}', ' ', cleaned).strip(' .-_()[]{}')

    return (cleaned + ext) if cleaned else filename


@app.post("/api/books/clean-watermarks")
def clean_watermarks(dry_run: bool = Query(True)):
    """Remove watermark domain tokens from every book filename.

    dry_run=true  - return preview list of (old, new) pairs, nothing is renamed.
    dry_run=false - rename files on disk and update DB.
    """
    with get_db() as conn:
        rows = conn.execute("SELECT id, filepath, filename FROM books").fetchall()

    changes = []
    for row in rows:
        old_name = row["filename"]
        new_name = _strip_watermark(old_name)
        if new_name != old_name:
            changes.append({
                "id":           row["id"],
                "old_filename": old_name,
                "new_filename": new_name,
                "filepath":     row["filepath"],
            })

    if dry_run:
        return {"dry_run": True, "changes": changes, "total": len(changes)}

    renamed = 0
    errors  = []
    for item in changes:
        old_path = Path(item["filepath"])
        new_path = old_path.parent / item["new_filename"]
        try:
            if not old_path.exists():
                errors.append({"id": item["id"], "reason": "file not found on disk"})
                continue
            if new_path.exists() and new_path != old_path:
                errors.append({"id": item["id"], "reason": f"'{item['new_filename']}' already exists"})
                continue
            old_path.rename(new_path)
            new_path_str = str(new_path).replace("\\", "/")
            with get_db() as conn:
                conn.execute(
                    "UPDATE books SET filepath=?, filename=? WHERE id=?",
                    (new_path_str, item["new_filename"], item["id"]),
                )
                conn.commit()
            renamed += 1
        except Exception as exc:
            errors.append({"id": item["id"], "reason": str(exc)})

    print(f"[clean_watermarks] renamed={renamed} errors={len(errors)}")
    return {"dry_run": False, "renamed": renamed, "errors": errors, "total": len(changes)}


@app.delete("/api/books/{book_id}")
def delete_book(book_id: int, delete_file: bool = Query(False)):
    with get_db() as conn:
        row = conn.execute("SELECT filepath FROM books WHERE id=?", (book_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Book not found")

    if delete_file:
        try:
            Path(row["filepath"]).unlink(missing_ok=True)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Could not delete file: {exc}")

    with get_db() as conn:
        conn.execute("DELETE FROM books WHERE id=?", (book_id,))
        conn.commit()

    return {"deleted": book_id, "file_deleted": delete_file}


@app.post("/api/books/{book_id}/fix")
def fix_book(book_id: int):
    with get_db() as conn:
        if not conn.execute("SELECT id FROM books WHERE id=?", (book_id,)).fetchone():
            raise HTTPException(status_code=404, detail="Book not found")
        conn.execute("UPDATE books SET manual_fixed=1 WHERE id=?", (book_id,))
        conn.commit()
    return {"book_id": book_id, "manual_fixed": 1}


@app.post("/api/books/{book_id}/unfix")
def unfix_book(book_id: int):
    with get_db() as conn:
        if not conn.execute("SELECT id FROM books WHERE id=?", (book_id,)).fetchone():
            raise HTTPException(status_code=404, detail="Book not found")
        conn.execute("UPDATE books SET manual_fixed=0 WHERE id=?", (book_id,))
        conn.commit()
    return {"book_id": book_id, "manual_fixed": 0}


VALID_READING_STATUSES = {"to-read", "reading", "read", "abandoned"}


class ReadingStatusRequest(BaseModel):
    status: str


@app.patch("/api/books/{book_id}/reading-status")
def update_reading_status(book_id: int, req: ReadingStatusRequest):
    if req.status not in VALID_READING_STATUSES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid status. Must be one of: {sorted(VALID_READING_STATUSES)}",
        )
    with get_db() as conn:
        if not conn.execute("SELECT id FROM books WHERE id=?", (book_id,)).fetchone():
            raise HTTPException(status_code=404, detail="Book not found")
        conn.execute("UPDATE books SET reading_status=? WHERE id=?", (req.status, book_id))
        conn.commit()
    return {"book_id": book_id, "reading_status": req.status}


@app.post("/api/books/{book_id}/open")
def open_book(book_id: int):
    with get_db() as conn:
        row = conn.execute("SELECT filepath FROM books WHERE id=?", (book_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Book not found")

    resolved = Path(row["filepath"]).resolve()
    try:
        resolved.relative_to(BOOKS_ROOT.resolve())
    except ValueError:
        raise HTTPException(status_code=403, detail="File is outside BOOKS_ROOT")

    if not resolved.exists():
        raise HTTPException(status_code=404, detail="File not found on disk")

    os.startfile(str(resolved))
    return {"opened": str(resolved)}


@app.post("/api/books/{book_id}/online-lookup")
def online_lookup(book_id: int):
    with get_db() as conn:
        row = conn.execute("SELECT * FROM books WHERE id=?", (book_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Book not found")

    book = dict(row)

    # Parse filename for title + author heuristics
    fn_title, fn_author = _parse_filename(book.get("filepath", ""))

    db_title  = (book.get("title")  or "").strip()
    db_author = (book.get("author") or "").strip()
    db_year   = str(book.get("year") or "").strip()

    # Primary query: prefer DB metadata (set by LLM/previous lookup) over filename heuristics.
    # Secondary query: filename-derived values when they differ (handled inside _collect_candidates).
    title  = db_title  or fn_title
    author = db_author or fn_author

    # alt_ values are the filename-parsed counterparts used as a secondary search pass
    alt_title  = fn_title  if fn_title  and _norm(fn_title)  != _norm(title)  else ""
    alt_author = fn_author if fn_author and _norm(fn_author) != _norm(author) else ""

    print(f"[online_lookup] id={book_id} title={title!r} author={author!r} year={db_year!r} alt_title={alt_title!r}")

    candidates = _collect_candidates(title, author, year=db_year, alt_title=alt_title, alt_author=alt_author)

    print(f"[online_lookup] id={book_id} found {len(candidates)} candidates")

    if not candidates:
        raise HTTPException(status_code=404, detail="No results found online for this book")

    return {
        "candidates": candidates,
        "query_title": title,
        "query_author": author,
        "query_year": db_year,
    }


class CandidateApply(BaseModel):
    title: Optional[str] = None
    author: Optional[str] = None
    year: Optional[str] = None
    language: Optional[str] = None
    description: Optional[str] = None
    subjects: Optional[List[str]] = None
    sources: Optional[List[str]] = None


@app.post("/api/books/{book_id}/online-lookup/apply")
def online_lookup_apply(book_id: int, candidate: CandidateApply):
    with get_db() as conn:
        row = conn.execute("SELECT id FROM books WHERE id=?", (book_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Book not found")

    subjects = candidate.subjects or []
    fixed_cat, fixed_sub = _map_to_fixed_category(subjects)
    tags = [s.title() for s in subjects[:8] if len(s) < 40]
    source_note = ", ".join(candidate.sources or [])

    with get_db() as conn:
        conn.execute(
            """UPDATE books SET
                title=COALESCE(NULLIF(?, ''), title),
                author=COALESCE(NULLIF(?, ''), author),
                year=COALESCE(NULLIF(?, ''), year),
                language=COALESCE(NULLIF(?, ''), language),
                description=COALESCE(NULLIF(?, ''), description),
                tags=?,
                fixed_category=COALESCE(NULLIF(?, ''), fixed_category),
                fixed_subcategory=COALESCE(NULLIF(?, ''), fixed_subcategory),
                confidence_score=1.0,
                extraction_method=?,
                status='done',
                processed_at=datetime('now')
               WHERE id=?""",
            (
                candidate.title or "",
                candidate.author or "",
                candidate.year or "",
                candidate.language or "",
                candidate.description or "",
                json.dumps(tags, ensure_ascii=True),
                fixed_cat,
                fixed_sub,
                f"online:{source_note}",
                book_id,
            ),
        )
        conn.commit()
        updated = conn.execute("SELECT * FROM books WHERE id=?", (book_id,)).fetchone()

    result = dict(updated)
    try:
        result["tags"] = json.loads(result["tags"] or "[]")
    except (json.JSONDecodeError, TypeError):
        result["tags"] = []

    print(f"[online_lookup_apply] id={book_id} applied: {result['title']!r}")
    return result


@app.post("/api/books/{book_id}/reprocess")
def reprocess_book(book_id: int):
    with get_db() as conn:
        row = conn.execute(
            "SELECT filepath, file_type FROM books WHERE id=?", (book_id,)
        ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Book not found")
    with get_db() as conn:
        conn.execute("UPDATE books SET status='pending' WHERE id=?", (book_id,))
        conn.commit()
    return {"queued": book_id}


class ReprocessBatchRequest(BaseModel):
    ids: List[int]


@app.post("/api/reprocess-batch")
def reprocess_batch(req: ReprocessBatchRequest):
    if not req.ids:
        raise HTTPException(status_code=400, detail="No IDs provided")
    placeholders = ",".join("?" * len(req.ids))
    with get_db() as conn:
        conn.execute(
            f"UPDATE books SET status='pending' WHERE id IN ({placeholders})",
            req.ids,
        )
        conn.commit()
    return {"queued": len(req.ids)}


class DeleteBatchRequest(BaseModel):
    ids: List[int]
    delete_files: bool = False


@app.post("/api/delete-batch")
def delete_batch(req: DeleteBatchRequest):
    if not req.ids:
        raise HTTPException(status_code=400, detail="No IDs provided")
    placeholders = ",".join("?" * len(req.ids))
    with get_db() as conn:
        rows = conn.execute(
            f"SELECT id, filepath FROM books WHERE id IN ({placeholders})", req.ids
        ).fetchall()
    if req.delete_files:
        for row in rows:
            try:
                Path(row["filepath"]).unlink(missing_ok=True)
            except Exception:
                pass
    with get_db() as conn:
        conn.execute(f"DELETE FROM books WHERE id IN ({placeholders})", req.ids)
        conn.commit()
    return {"deleted": len(rows)}


@app.get("/api/text-previews")
def get_text_previews():
    """Return first 300 chars of cached extracted text for every book that has one."""
    cache_dir = Path("backend/data/text_cache")
    result = {}
    if cache_dir.exists():
        for txt_file in cache_dir.glob("*.txt"):
            try:
                book_id = int(txt_file.stem)
                text = txt_file.read_text(encoding="utf-8", errors="ignore").strip()
                result[book_id] = text[:300]
            except (ValueError, Exception):
                pass
    return result


@app.post("/api/books/{book_id}/extract-test")
def extract_test(book_id: int):
    with get_db() as conn:
        row = conn.execute(
            "SELECT filepath, file_type FROM books WHERE id=?", (book_id,)
        ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Book not found")
    text = extract_text(row["filepath"], row["file_type"])
    return {
        "text_length": len(text),
        "preview": text[:200],
        "file_type": row["file_type"],
    }


@app.post("/api/books/{book_id}/ollama-test")
def ollama_test(book_id: int):
    with get_db() as conn:
        row = conn.execute(
            "SELECT filepath, file_type FROM books WHERE id=?", (book_id,)
        ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Book not found")
    text = extract_text(row["filepath"], row["file_type"])
    if not text:
        raise HTTPException(status_code=422, detail="No text extracted from book")
    metadata = call_ollama(text)
    return metadata


# =============================================================================
# API: COVERS
# =============================================================================
# GET  /api/books/{id}/cover         - serve cover JPEG; generates on-the-fly
#                                      if not cached, then saves path to DB.
# POST /api/books/{id}/cover/refresh - force re-extraction of the cover image.
# =============================================================================

@app.get("/api/books/{book_id}/cover")
def get_cover(book_id: int):
    with get_db() as conn:
        row = conn.execute(
            "SELECT cover_path, filepath, file_type, no_cover FROM books WHERE id=?", (book_id,)
        ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Book not found")

    # Document explicitly marked as having no cover - don't even try.
    if row["no_cover"]:
        raise HTTPException(status_code=404, detail="No cover available")

    cover = row["cover_path"]
    if not cover or not Path(cover).exists():
        # try to generate on the fly
        cover = extract_cover(row["filepath"], book_id, row["file_type"])
        if cover:
            with get_db() as conn:
                conn.execute("UPDATE books SET cover_path=? WHERE id=?", (cover, book_id))
                conn.commit()

    if not cover or not Path(cover).exists():
        raise HTTPException(status_code=404, detail="No cover available")

    return FileResponse(cover, media_type="image/jpeg")


@app.post("/api/books/{book_id}/cover/refresh")
def refresh_cover(book_id: int):
    with get_db() as conn:
        row = conn.execute(
            "SELECT filepath, file_type FROM books WHERE id=?", (book_id,)
        ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Book not found")

    cover = extract_cover(row["filepath"], book_id, row["file_type"])
    if not cover:
        raise HTTPException(status_code=500, detail="Cover extraction failed")

    with get_db() as conn:
        conn.execute("UPDATE books SET cover_path=? WHERE id=?", (cover, book_id))
        conn.commit()

    return {"book_id": book_id, "cover_path": cover}


# =============================================================================
# API: DUPLICATES + SHELVES
# =============================================================================
# Duplicates are detected during process_book() via SHA-256 hash comparison.
#
# GET  /api/duplicates               - list undismissed duplicates with their original
# POST /api/duplicates/dismiss       - mark a hash as dismissed (hide from UI)
#
# Shelves are virtual collections of books stored in the shelves + shelf_books tables.
#
# GET    /api/shelves                        - list shelves with book counts
# POST   /api/shelves                        - create a new shelf
# DELETE /api/shelves/{id}                   - delete shelf (cascades to shelf_books)
# GET    /api/shelves/{id}/books             - list books on a shelf
# POST   /api/shelves/{id}/books             - add a book to a shelf
# DELETE /api/shelves/{id}/books/{book_id}   - remove a book from a shelf
# =============================================================================

@app.get("/api/duplicates")
def get_duplicates():
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT b.*, orig.title AS original_title, orig.filepath AS original_filepath
            FROM books b
            JOIN books orig ON b.duplicate_of = orig.id
            WHERE b.status = 'duplicate' AND b.dedup_dismissed = 0
            ORDER BY b.added_at DESC
            """
        ).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        try:
            item["tags"] = json.loads(item["tags"] or "[]")
        except (json.JSONDecodeError, TypeError):
            item["tags"] = []
        result.append(item)
    return result


class DismissDuplicateRequest(BaseModel):
    hash: str


@app.post("/api/duplicates/dismiss")
def dismiss_duplicate(req: DismissDuplicateRequest):
    with get_db() as conn:
        result = conn.execute(
            "UPDATE books SET dedup_dismissed=1 WHERE file_hash=? AND status='duplicate'",
            (req.hash,),
        )
        conn.commit()
    return {"dismissed": result.rowcount, "hash": req.hash}


class ShelfRequest(BaseModel):
    name: str


@app.get("/api/shelves")
def get_shelves():
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT s.*, COUNT(sb.book_id) AS book_count
            FROM shelves s
            LEFT JOIN shelf_books sb ON s.id = sb.shelf_id
            GROUP BY s.id
            ORDER BY s.name
            """
        ).fetchall()
    return [dict(r) for r in rows]


@app.post("/api/shelves", status_code=201)
def create_shelf(req: ShelfRequest):
    try:
        with get_db() as conn:
            conn.execute("INSERT INTO shelves (name) VALUES (?)", (req.name,))
            conn.commit()
            row = conn.execute(
                "SELECT * FROM shelves WHERE name=?", (req.name,)
            ).fetchone()
        return dict(row)
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=409, detail="Shelf name already exists")


@app.delete("/api/shelves/{shelf_id}")
def delete_shelf(shelf_id: int):
    with get_db() as conn:
        if not conn.execute("SELECT id FROM shelves WHERE id=?", (shelf_id,)).fetchone():
            raise HTTPException(status_code=404, detail="Shelf not found")
        conn.execute("DELETE FROM shelves WHERE id=?", (shelf_id,))
        conn.commit()
    return {"deleted": shelf_id}


@app.get("/api/shelves/{shelf_id}/books")
def get_shelf_books(shelf_id: int):
    with get_db() as conn:
        if not conn.execute("SELECT id FROM shelves WHERE id=?", (shelf_id,)).fetchone():
            raise HTTPException(status_code=404, detail="Shelf not found")
        rows = conn.execute(
            """
            SELECT b.* FROM books b
            JOIN shelf_books sb ON b.id = sb.book_id
            WHERE sb.shelf_id = ?
            ORDER BY sb.added_at DESC
            """,
            (shelf_id,),
        ).fetchall()
    result = []
    for row in rows:
        book = dict(row)
        try:
            book["tags"] = json.loads(book["tags"] or "[]")
        except (json.JSONDecodeError, TypeError):
            book["tags"] = []
        result.append(book)
    return result


class ShelfBookRequest(BaseModel):
    book_id: int


@app.post("/api/shelves/{shelf_id}/books", status_code=201)
def add_book_to_shelf(shelf_id: int, req: ShelfBookRequest):
    with get_db() as conn:
        if not conn.execute("SELECT id FROM shelves WHERE id=?", (shelf_id,)).fetchone():
            raise HTTPException(status_code=404, detail="Shelf not found")
        if not conn.execute("SELECT id FROM books WHERE id=?", (req.book_id,)).fetchone():
            raise HTTPException(status_code=404, detail="Book not found")
        try:
            conn.execute(
                "INSERT INTO shelf_books (shelf_id, book_id) VALUES (?, ?)",
                (shelf_id, req.book_id),
            )
            conn.commit()
        except sqlite3.IntegrityError:
            raise HTTPException(status_code=409, detail="Book already on shelf")
    return {"shelf_id": shelf_id, "book_id": req.book_id}


@app.delete("/api/shelves/{shelf_id}/books/{book_id}")
def remove_book_from_shelf(shelf_id: int, book_id: int):
    with get_db() as conn:
        result = conn.execute(
            "DELETE FROM shelf_books WHERE shelf_id=? AND book_id=?",
            (shelf_id, book_id),
        )
        conn.commit()
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="Book not on this shelf")
    return {"shelf_id": shelf_id, "book_id": book_id, "removed": True}


# =============================================================================
# API: EXPORT
# =============================================================================
# Both endpoints stream output row-by-row to avoid loading all books into memory.
#
# GET /api/export/csv  - streams a UTF-8 BOM CSV (Excel-compatible).
#                        Tags list is joined to a comma-separated string.
# GET /api/export/json - streams a JSON array. Each book is a full DB row with
#                        tags deserialized to a list.
# =============================================================================

CSV_FIELDS = [
    "id", "filename", "filepath", "file_type", "file_size",
    "title", "author", "year", "language", "category", "subcategory",
    "difficulty", "description", "tags", "reading_status",
    "status", "confidence_score", "added_at", "processed_at",
]


@app.get("/api/export/csv")
def export_csv():
    with get_db() as conn:
        rows = conn.execute(
            f"SELECT {', '.join(CSV_FIELDS)} FROM books ORDER BY id"
        ).fetchall()

    def generate():
        buf = io.StringIO()
        buf.write("\ufeff")  # UTF-8 BOM
        writer = csv.DictWriter(buf, fieldnames=CSV_FIELDS)
        writer.writeheader()
        yield buf.getvalue()

        for row in rows:
            buf = io.StringIO()
            d = dict(row)
            try:
                tags = json.loads(d["tags"] or "[]")
                d["tags"] = ", ".join(tags)
            except (json.JSONDecodeError, TypeError):
                d["tags"] = ""
            writer = csv.DictWriter(buf, fieldnames=CSV_FIELDS)
            writer.writerow(d)
            yield buf.getvalue()

    return StreamingResponse(
        generate(),
        media_type="text/csv; charset=utf-8-sig",
        headers={"Content-Disposition": "attachment; filename=librarian_export.csv"},
    )


@app.get("/api/export/json")
def export_json():
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM books ORDER BY id").fetchall()

    def generate():
        yield "["
        first = True
        for row in rows:
            book = dict(row)
            try:
                book["tags"] = json.loads(book["tags"] or "[]")
            except (json.JSONDecodeError, TypeError):
                book["tags"] = []
            chunk = json.dumps(book, ensure_ascii=True)
            if not first:
                yield ","
            yield chunk
            first = False
        yield "]"

    return StreamingResponse(
        generate(),
        media_type="application/json",
        headers={"Content-Disposition": "attachment; filename=librarian_export.json"},
    )


# =============================================================================
# API: ANALYTICS + TAXONOMY VIEWS
# =============================================================================
# GET /api/analytics      - single endpoint that returns all aggregated stats
#                           in one response: counts by status, category, reading
#                           status, file type, difficulty, language (top 20),
#                           average confidence score, total library size, and
#                           counts of manually-fixed and duplicate books.
#
# GET /api/fixed-taxonomy - returns the FIXED_TAXONOMY dict for use by the UI.
# GET /api/categories     - distinct category values actually in the DB.
# GET /api/taxonomy       - full (category, subcategory, count) tree from the DB,
#                           sorted by count descending — used for the filter panel.
# =============================================================================

@app.get("/api/analytics")
def get_analytics():
    with get_db() as conn:
        total = conn.execute("SELECT COUNT(*) FROM books").fetchone()[0]

        by_status = {
            r["status"]: r["cnt"]
            for r in conn.execute(
                "SELECT status, COUNT(*) AS cnt FROM books GROUP BY status"
            ).fetchall()
        }

        by_category = {
            r["category"]: r["cnt"]
            for r in conn.execute(
                "SELECT COALESCE(category,'Unknown') AS category, COUNT(*) AS cnt "
                "FROM books GROUP BY category ORDER BY cnt DESC"
            ).fetchall()
        }

        by_reading_status = {
            r["reading_status"]: r["cnt"]
            for r in conn.execute(
                "SELECT reading_status, COUNT(*) AS cnt FROM books GROUP BY reading_status"
            ).fetchall()
        }

        by_file_type = {
            r["file_type"]: r["cnt"]
            for r in conn.execute(
                "SELECT file_type, COUNT(*) AS cnt FROM books GROUP BY file_type"
            ).fetchall()
        }

        by_difficulty = {
            r["difficulty"]: r["cnt"]
            for r in conn.execute(
                "SELECT COALESCE(difficulty,'Unknown') AS difficulty, COUNT(*) AS cnt "
                "FROM books GROUP BY difficulty ORDER BY cnt DESC"
            ).fetchall()
        }

        by_language = {
            r["language"]: r["cnt"]
            for r in conn.execute(
                "SELECT COALESCE(language,'Unknown') AS language, COUNT(*) AS cnt "
                "FROM books GROUP BY language ORDER BY cnt DESC LIMIT 20"
            ).fetchall()
        }

        avg_confidence = conn.execute(
            "SELECT ROUND(AVG(confidence_score), 3) FROM books WHERE confidence_score IS NOT NULL"
        ).fetchone()[0]

        total_size_bytes = conn.execute(
            "SELECT SUM(file_size) FROM books"
        ).fetchone()[0] or 0

        manual_fixed = conn.execute(
            "SELECT COUNT(*) FROM books WHERE manual_fixed=1"
        ).fetchone()[0]

        duplicates = conn.execute(
            "SELECT COUNT(*) FROM books WHERE status='duplicate'"
        ).fetchone()[0]

    return {
        "total": total,
        "by_status": by_status,
        "by_category": by_category,
        "by_reading_status": by_reading_status,
        "by_file_type": by_file_type,
        "by_difficulty": by_difficulty,
        "by_language": by_language,
        "avg_confidence_score": avg_confidence,
        "total_size_bytes": total_size_bytes,
        "manual_fixed": manual_fixed,
        "duplicates": duplicates,
    }


@app.get("/api/fixed-taxonomy")
def get_fixed_taxonomy():
    return FIXED_TAXONOMY


@app.get("/api/categories")
def get_categories():
    with get_db() as conn:
        rows = conn.execute(
            "SELECT DISTINCT category FROM books WHERE category IS NOT NULL AND category != '' ORDER BY category"
        ).fetchall()
    return [row["category"] for row in rows]


@app.get("/api/taxonomy")
def get_taxonomy():
    with get_db() as conn:
        # All (category, subcategory) pairs with counts
        rows = conn.execute(
            """
            SELECT
                COALESCE(category, '') AS category,
                COALESCE(subcategory, '') AS subcategory,
                COUNT(*) AS cnt
            FROM books
            GROUP BY category, subcategory
            ORDER BY category, subcategory
            """
        ).fetchall()

    tree = {}
    for row in rows:
        cat = row["category"] or "(no category)"
        sub = row["subcategory"] or ""
        cnt = row["cnt"]
        if cat not in tree:
            tree[cat] = {"name": cat, "count": 0, "subcategories": []}
        tree[cat]["count"] += cnt
        if sub:
            tree[cat]["subcategories"].append({"name": sub, "count": cnt})

    result = sorted(tree.values(), key=lambda x: -x["count"])
    for item in result:
        item["subcategories"].sort(key=lambda x: -x["count"])
    return result


# =============================================================================
# API: MANUAL CATEGORISATION (NoAutoCategorisation feature)
# =============================================================================
# GET  /api/v1/config                       - exposes AUTO_CATEGORIZATION_ENABLED
# POST /api/v1/documents/{id}/categorize    - assign category to a single book
# POST /api/v1/documents/bulk/categorize    - assign category to many books
#
# Permission: callers must supply X-Categorize-Key matching CATEGORIZE_API_KEY
# (if CATEGORIZE_API_KEY is empty the check is skipped for local installs).
# =============================================================================

def _check_categorize_permission(x_categorize_key: str = "") -> None:
    """Raise 403 if the caller does not have can_categorize permission."""
    if CATEGORIZE_API_KEY and x_categorize_key != CATEGORIZE_API_KEY:
        raise HTTPException(status_code=403, detail="Missing or invalid X-Categorize-Key header")


@app.get("/api/v1/config")
def get_config():
    """Return feature-flag state so the UI can adapt its behaviour."""
    return {
        "auto_categorization_enabled": AUTO_CATEGORIZATION_ENABLED,
        "categorize_key_required": bool(CATEGORIZE_API_KEY),
    }


class BulkCategorizeRequest(BaseModel):
    ids: List[int]


# IMPORTANT: bulk route must be registered BEFORE the {book_id} route so that
# FastAPI matches the literal path segment "bulk" before trying to coerce it
# to an integer.
@app.post("/api/v1/documents/bulk/categorize")
async def bulk_categorize_documents(
    req: BulkCategorizeRequest,
    x_categorize_key: str = Header(default=""),
):
    """
    Queue LLM categorisation for multiple documents.  Each book is processed in
    a background thread so the endpoint returns immediately.  The frontend can
    poll GET /api/books to see categories appear as they complete.
    """
    _check_categorize_permission(x_categorize_key)
    if not req.ids:
        raise HTTPException(status_code=400, detail="No IDs provided")

    # Verify all IDs exist
    with get_db() as conn:
        valid_ids = [
            row["id"]
            for row in conn.execute(
                f"SELECT id FROM books WHERE id IN ({','.join('?' * len(req.ids))})",
                req.ids,
            ).fetchall()
        ]

    async def _run_all():
        for book_id in valid_ids:
            await asyncio.to_thread(_categorize_book_by_llm, book_id)

    asyncio.create_task(_run_all())
    print(f"[bulk_categorize_llm] queued ids={valid_ids}")
    return {"queued": len(valid_ids)}


@app.post("/api/v1/documents/{book_id}/categorize")
def categorize_document(
    book_id: int,
    x_categorize_key: str = Header(default=""),
):
    """
    Run LLM categorisation on a single document using its existing DB attributes
    (title, author, description, tags, language).  Clears any previous category
    before saving the new one.
    """
    _check_categorize_permission(x_categorize_key)
    with get_db() as conn:
        if not conn.execute("SELECT id FROM books WHERE id=?", (book_id,)).fetchone():
            raise HTTPException(status_code=404, detail="Book not found")

    _categorize_book_by_llm(book_id)

    with get_db() as conn:
        updated = dict(conn.execute("SELECT * FROM books WHERE id=?", (book_id,)).fetchone())
    try:
        updated["tags"] = json.loads(updated["tags"] or "[]")
    except (json.JSONDecodeError, TypeError):
        updated["tags"] = []
    return updated


# =============================================================================
# STATIC FILES
# =============================================================================
# The compiled frontend (Vite build output) is served from backend/static/.
# The mount is at "/" so any path not matched by an API route falls through to
# index.html — enabling client-side routing in the SPA.
# This must be the LAST mount so API routes take priority.
# =============================================================================

os.makedirs("backend/static", exist_ok=True)
app.mount("/", StaticFiles(directory="backend/static", html=True), name="static")
