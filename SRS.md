# LibrarianAI v3 — Technical SRS Reference for Claude Code
# Platform: Native Windows 11
# Document: LAI-SRS-001-WIN | Place at project root: C:\Users\Than\librarian-ai\SRS.md
# Claude Code reads this with: type SRS.md

---

## §0 — Platform Baseline

**OS:** Windows 11 (native — no WSL2, no Docker, no virtualisation layer)
**Python:** 3.11+ installed via python.org Windows installer.
**Node:** 18+ LTS installed via nodejs.org Windows installer.
**Ollama:** Installed on Windows, runs as a background service at `http://localhost:11434`.
**Shell:** PowerShell 7+ (pwsh) or Windows PowerShell 5.1.
**All paths:** Windows paths throughout. Use `pathlib.Path` — handles both `/` and `\`.
**Project root:** `C:\Users\posen\librarian-ai\`

NO Tesseract. NO Poppler. NO OCR pipeline. Text extraction uses PyMuPDF (fitz) only.
For a personal library of technical PDFs, the text layer is always present.
OCR added complexity and two external Windows binaries that were silent failure points.

---

## §1 — System Context

LibrarianAI v3 is a locally hosted web application. All components run natively on
Windows 11.

```
Browser (Windows)  →  Vite dev server :5173  →  FastAPI backend :8000
                                                      ↓
                                               SQLite database
                                               (./data/librarian.db)
                                                      ↓
                                          Ollama (localhost:11434)
                                          PyMuPDF — fitz (built-in, no binary)
                                          Open Library API (internet, optional)
```

**Architecture — single file:** The entire backend lives in `backend/app.py`.
There are no separate modules (`process.py`, `extractors.py`, `llm.py`, etc.).
This matches the proven v1/v2 pattern and eliminates cross-module wiring bugs.

**Processor pattern:** An `asyncio` background task polls for `pending` books every
4 seconds and processes them one at a time. This avoids all `ThreadPoolExecutor` +
SQLite deadlock issues on Windows. It is the pattern from the working v1 app.

**open_book_file:** `subprocess.Popen(["cmd", "/c", "start", "", str(resolved)], shell=False)`

**BOOKS_PATH containment:** `resolved.is_relative_to(BOOKS_ROOT)` (Python 3.9+).
---

## §1.1 — Core Metadata Fields (books table)

```
id, filename, filepath, status, title, author, year, language,
category, subcategory, difficulty, description,
tags          TEXT  -- JSON array stored as string e.g. '["ai","python"]'
error_msg, manual_fixed (int 0/1), extraction_method,
confidence_score (float 0-1, 4dp), created_at, updated_at,
cover_path    TEXT  -- Windows path to JPEG thumbnail e.g. .\data\covers\42.jpg
cover_source  TEXT  -- "local" | "openlibrary"
file_hash     TEXT  -- SHA-256 hex, INDEXED
duplicate_of  INT   -- FK → books.id, nullable
reading_status TEXT -- "to-read"|"reading"|"done"|null
ol_enriched   INT   DEFAULT 0
dedup_dismissed INT DEFAULT 0  -- 1 = group dismissed, never re-flag
```

**status values:** `"processing"` | `"done"` | `"partial"` | `"error"` | `"duplicate"`

**filepath storage:** Always store as a resolved Windows absolute path using forward
slashes for portability: `C:/Users/Than/Books/Feynman_Lectures.pdf`. `pathlib.Path`
accepts forward slashes on Windows and resolves them correctly.

---

## §1.2 — Extraction Pipeline — Best-of-Three with Merge

Two passes only. No OCR. No Tesseract. No Poppler.

**Pass 1 — fitz (PDF only):** Open with `fitz.open(filepath)`. Extract text from
first 3 pages via `page.get_text()`. If result >= 50 chars, send to Ollama.
This is `extraction_method = "fitz+ollama"`.

**Pass 2 — Filename heuristic (always):** When fitz yields < 50 chars (scanned
image PDF or encrypted file), send only the filename stem and file size to Ollama.
This is `extraction_method = "filename_heuristic"`.

**EPUB:** Use `ebooklib` + `BeautifulSoup`. Extract text from document items.
This is `extraction_method = "ebooklib+ollama"`.

**No merge step needed:** There is only one real pass per file. The result goes
directly to `compute_confidence()` and then to the DB.

### field-level rules (simplified from multi-pass merge)

```python
# All fields come from the single Ollama response.
# Empty string → None before saving (null-guard in _save_to_db).
# Tags: always a list, lowercase, deduplicated.
# year: validate int in [1800, current+1] — None if invalid.
# language: validate 2-letter ISO code — None if invalid.
# difficulty: one of "Beginner", "Intermediate", "Advanced" — None otherwise.
```

---

## §1.2.1 — Confidence Score Formula (AUTHORITATIVE — do not deviate)

```python
SCORED_FIELDS = [
    "title", "author", "year", "language",
    "category", "subcategory", "difficulty", "description", "tags",
]
# Exactly 9 fields. extraction_method and confidence_score are NOT in this list.

def compute_confidence(merged: dict, text_extracted: bool) -> float:
    filled = sum(
        1 for f in SCORED_FIELDS
        if merged.get(f) not in (None, "", [], "null", "None")
    )
    score = (filled / 9) * 0.7 + (1.0 if text_extracted else 0.0) * 0.3
    return round(min(max(score, 0.0), 1.0), 4)

# Test vectors — all tests must match these exactly:
# all 9 None  + no text  → 0.0
# all 9 filled + text    → 1.0
# 5/9 filled  + text     → round((5/9)*0.7 + 0.3, 4) == 0.6889
# empty string ""        → counts as NOT filled (same as None)
# extraction_method      → NOT counted, even if non-null
```

---

## §1.3 — Ollama Integration and repair_json


```
OLLAMA_MODEL=mistral:7b-instruct
OLLAMA_URL=http://localhost:11434
LLM_MAX_CHARS=3000
```

**HTTP:** `urllib.request` (stdlib only — no httpx dependency).
Timeout: 120s. No retry logic needed — single synchronous call run in executor.

**repair_json — 4-step, inline in app.py:**

```python
import re, json

def _parse_llm_json(raw: str) -> dict:
    # Step 1: strip markdown fences
    text = re.sub(r"```(?:json)?", "", raw).strip()
    # Step 2: direct parse
    try:
        result = json.loads(text)
        return result if isinstance(result, dict) else {}
    except Exception:
        pass
    # Step 3: extract first {...} block
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        try:
            result = json.loads(m.group())
            return result if isinstance(result, dict) else {}
        except Exception:
            pass
    # Step 4: json_repair library fallback
    try:
        from json_repair import repair_json as _repair
        result = json.loads(_repair(text))
        return result if isinstance(result, dict) else {}
    except Exception:
        pass
    return {}
```

**Two Ollama functions:**

```python
def call_ollama_text(text: str, filename_stem: str) -> dict:
    # Full metadata extraction from extracted text
    # Prompt includes: title, author, year, language, category, subcategory,
    #   difficulty, description, tags
    # Returns validated dict

def call_ollama_filename(filepath: str) -> dict:
    # Filename-only heuristic when no text extractable
    # Sends: filename stem + file size
    # Returns partial dict (title/category/tags most likely)
```

---

## §1.4 — _save_merged() — Pre/Post Save Hardening

```python
import json, logging
logger = logging.getLogger(__name__)

def _save_merged(conn, book_id: int, merged: dict):
    # 1. Null-guard: replace empty strings with None before writing
    for field in ("title", "author", "language", "category", "subcategory",
                  "difficulty", "description", "extraction_method"):
        if merged.get(field) == "":
            merged[field] = None

    # 2. Pre-save diagnostic log
    logger.debug(
        "save_merged book=%d title=%r author=%r year=%r lang=%r score=%.4f method=%r",
        book_id, merged.get("title"), merged.get("author"), merged.get("year"),
        merged.get("language"), merged.get("confidence_score", 0),
        merged.get("extraction_method"),
    )

    conn.execute("""
        UPDATE books SET
            title=?, author=?, year=?, language=?,
            category=?, subcategory=?, difficulty=?, description=?,
            tags=?, extraction_method=?, confidence_score=?,
            status=?, updated_at=datetime('now')
        WHERE id=?
    """, (
        merged.get("title"), merged.get("author"), merged.get("year"),
        merged.get("language"), merged.get("category"), merged.get("subcategory"),
        merged.get("difficulty"), merged.get("description"),
        json.dumps(merged.get("tags", [])),
        merged.get("extraction_method"), merged.get("confidence_score"),
        merged.get("status"), book_id,
    ))
    conn.commit()

    # 3. Post-save read-back check
    row = conn.execute(
        "SELECT title FROM books WHERE id=?", (book_id,)
    ).fetchone()
    if row and row["title"] is None and merged.get("title") is not None:
        logger.warning(
            "save_merged post-check: title NULL in DB after update "
            "for book_id=%d (expected %r). Possible write failure.",
            book_id, merged.get("title")
        )
```

---

## §1.5 — Open Library Enrichment (enrichment.py)

```python
# Endpoint: GET https://openlibrary.org/search.json?title={title}&author={author}&limit=1
# Trigger: after merge, when confidence_score < 0.8 OR any of
#          {title, author, year, language} is None.
# Only call when title is non-null (extracted from Ollama passes).

# Fill rules:
#   - Only fill NULL fields (or fields with out-of-range values).
#   - NEVER overwrite a non-null, in-range existing value.
#   - EXCEPTION: if existing year is outside 1800-(current+1), treat as null and overwrite.
#   - EXCEPTION: if existing language is not a valid 2-letter ISO code, treat as null.
#   - OL fields to map:
#       year        <- first_publish_year
#       language    <- language[0] if present
#       description <- first_sentence.value if present
#       tags        <- union with subject[:10], deduplicated, lowercased
#       author      <- author_name[0] if author is null

# After successful enrichment: set ol_enriched = 1
# Rate limit: 0.5s delay between concurrent calls
# HTTP: httpx.AsyncClient, timeout=5s
# On any error: log WARNING, do not change status, return without raising
```

---

## §1.6 — Cover Extraction (covers.py)

```python
# Storage: COVERS_PATH\{book_id}.jpg  e.g. .\data\covers\42.jpg
# Max size: 300x400px JPEG

# PDF extraction:
#   from pdf2image import convert_from_path
#   pages = convert_from_path(
#       filepath,
#       first_page=1, last_page=1,
#       dpi=72,                    # HARD LIMIT: 72 DPI max
#       size=(600, None),          # cap width at 600px
#       poppler_path=POPPLER_PATH  # from env: C:/poppler/Library/bin
#   )
#   Crop to upper half. Save as JPEG with Pillow.
#   Blank detection: PIL.ImageStat.Stat(img).mean > 250 on all channels -> skip, try OL.
#   Wrap entire render in try/except Exception.

# EPUB extraction:
#   ebooklib: find item where 'cover' in item.get_name() or 'cover' in item.properties
#   Extract bytes -> PIL.Image.open(BytesIO(bytes)) -> resize -> save JPEG.

# OL cover fallback (requires ISBN or OL key from enrichment):
#   ISBN regex on extracted text: r"ISBN[-\s]?(?:13|10)?:?\s*([0-9X-]{10,17})"
#   URL: https://covers.openlibrary.org/b/isbn/{isbn}-M.jpg
#   Check: response Content-Length > 1000 bytes (OL returns 1px placeholder otherwise)
#   Set cover_source = "openlibrary" on success.

# Endpoints:
#   GET  /api/books/{id}/cover         -> FileResponse(cover_path) or 404
#   POST /api/books/{id}/cover/refresh -> re-run extraction
```

---

## §1.7 — Deduplication Logic

```python
# On every import:
# 1. Compute SHA-256: hashlib.sha256(Path(filepath).read_bytes()).hexdigest()
# 2. Check DB:
#    SELECT id FROM books WHERE file_hash=? AND id!=? AND dedup_dismissed=0
# 3. If match found:
#      UPDATE books SET duplicate_of={existing_id}, status='duplicate',
#                       file_hash=? WHERE id={new_id}
#      STOP pipeline.
# 4. If dedup_dismissed=1 on the existing canonical book -> do NOT flag -> continue.

# POST /api/duplicates/dismiss  body: {"hash": "..."}
#   -> UPDATE books SET dedup_dismissed=1 WHERE file_hash=?
#   After this, future imports of the same content are not flagged.

# GET /api/duplicates
#   Returns: [{"hash": "abc123...", "books": [{"id":1,"filename":"...","filepath":"..."}]}]
```

---

## §1.8 — Shelves and Reading Status

```sql
CREATE TABLE IF NOT EXISTS shelves (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS book_shelves (
    book_id  INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    shelf_id INTEGER NOT NULL REFERENCES shelves(id) ON DELETE CASCADE,
    added_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (book_id, shelf_id)
);
```

**Reading status rule:** writable ONLY via `PATCH /api/books/{id}/reading-status`.
The general `PATCH /api/books/{id}` does NOT accept `reading_status`.

**shelf_id filter semantics:** inclusive — a book in shelves 3 and 7 appears in both
`?shelf_id=3` and `?shelf_id=7` results.

---

## §1.9 — PATCH /api/books/{id} — Allowed Fields ONLY

```python
PATCHABLE_FIELDS = {
    "title", "author", "year", "language",
    "category", "subcategory", "difficulty",
    "description", "tags",
}
# Implementation pattern — silently ignore unknown keys:
safe = {k: v for k, v in payload.items() if k in PATCHABLE_FIELDS}
if not safe:
    raise HTTPException(400, detail="No valid fields to update.")

# EXPLICITLY FORBIDDEN (must never be accepted via PATCH):
# filepath, filename, file_hash, duplicate_of, status, manual_fixed,
# extraction_method, confidence_score, cover_path, cover_source,
# ol_enriched, reading_status, dedup_dismissed, created_at, updated_at
```

---

## §1.10 — File Rename (file_utils.py)

```python
def rename_book_file(book_id: int, db_path: str, dry_run: bool = False) -> dict:
    # 1. Build target: {Author}_{Title}_{Year}.{ext}
    #    Sanitise: replace spaces with _, strip chars not in [A-Za-z0-9._-]
    #    Truncate stem to 120 chars.
    # 2. If author or title is None:
    #    return {"skipped": True, "reason": "missing fields"}
    # 3. If dry_run:
    #    return {"new_filename": "...", "dry_run": True}
    # 4. Collision handling:
    #    If target exists -> try _01, _02 ... _99
    #    If all 99 taken  -> return {"skipped": True, "reason": "rename_collision"}
    # 5. BOOKS_ROOT containment check:
    #    resolved_target = (Path(filepath).parent / new_filename).resolve()
    #    if not resolved_target.is_relative_to(BOOKS_ROOT):
    #        raise HTTPException(403, "Target path outside library root.")
    # 6. Path(filepath).rename(resolved_target)
    # 7. UPDATE books SET filepath=?, filename=? WHERE id=?
    # 8. return {"old_filename": "...", "new_filename": "...", "renamed": True}
```

---

## §1.11 — open_book_file (Windows-native implementation)

```python
import subprocess, os
from pathlib import Path

BOOKS_ROOT = Path(os.getenv("BOOKS_PATH", r"C:\Users\Than\Books")).resolve()

@app.post("/api/books/{book_id}/open")
async def open_book_file(book_id: int, _=Depends(require_auth)):
    book = _book_or_404(book_id)
    try:
        resolved = Path(book["filepath"]).resolve()
    except Exception:
        raise HTTPException(400, detail="Invalid file path.")

    # Path containment check — must be inside BOOKS_PATH
    if not resolved.is_relative_to(BOOKS_ROOT):
        raise HTTPException(
            403, detail="File path is outside the configured library root."
        )

    if not resolved.exists():
        raise HTTPException(404, detail="File not found on disk.")

    # Windows shell open — launches the file in its default application.
    # The empty string after "start" is the window title argument; required
    # to prevent issues when the filepath contains spaces or special characters.
    # shell=False is intentional — we pass a fixed argument list.
    subprocess.Popen(
        ["cmd", "/c", "start", "", str(resolved)],
        shell=False
    )
    return {"opened": str(resolved)}

# NOTE: is_relative_to() is Python 3.9+.
# For Python 3.8 compat use:
# str(resolved).lower().startswith(str(BOOKS_ROOT).lower())
```

---

## §1.12 — Chat Endpoint

```python
@app.post("/api/books/{book_id}/chat")
async def chat_book(book_id: int, body: dict, _=Depends(require_auth)):
    # 1. Load TEXT_CACHE_PATH/{book_id}.txt
    cache_file = Path(os.getenv("TEXT_CACHE_PATH", "./data/text_cache")) / f"{book_id}.txt"
    text = cache_file.read_text(encoding="utf-8") if cache_file.exists() else ""

    # 2. Truncate to LLM_MAX_CHARS characters.
    #    DO NOT multiply by any factor — Mistral 7B 8K context window fills fast.
    max_chars = int(os.getenv("LLM_MAX_CHARS", 3000))
    context   = text[:max_chars]

    # 3. Call Ollama
    system = (
        "You are a knowledgeable assistant helping the user understand a book. "
        "Answer questions based on the provided text excerpt. Be concise."
    )
    reply = await call_ollama_chat(system, context, body.get("message", ""))
    return {"reply": reply, "context_chars": len(context)}
```

---

## §1.13 — Authentication (auth.py)

```python
# Read API_KEY from .env. If not set, auth is disabled (log warning).
# require_auth dependency checks:
#   - Header X-API-Key: {API_KEY}    OR
#   - Query param ?api_key={API_KEY}
# Returns HTTP 401 {"detail": "Unauthorized"} on failure.
# Apply to ALL /api/* routes EXCEPT GET /api/health.
# GET /api/auth/verify returns {"authenticated": true} (protected).
```

---

## §1.14 — Pagination

```
GET /api/books accepts: ?page=1&limit=50
Maximum limit: 500. Requests > 500 are silently capped at 500.
Default limit: 50.
Export endpoints (/api/export/csv, /api/export/json) are NOT paginated — stream all.
```

---

## §1.15 — Analytics (SQL aggregates only)

```python
# GET /api/analytics must return:
{
  "books_per_category":    [{"category": str, "count": int}],
  "books_per_author":      [{"author": str, "count": int}],   # top 20
  "language_distribution": [{"language": str, "count": int}],
  "reading_status_counts": {"to-read": int, "reading": int, "done": int, "unset": int},
  "confidence_histogram":  [{"bucket": str, "count": int}],   # 5 buckets: 0.0-0.2 etc
  "books_added_per_month": [{"month": str, "count": int}],    # last 12 months YYYY-MM
  "total_books":   int,
  "total_shelves": int,
}
# ALL values computed via SQL GROUP BY / COUNT / CASE — no Python loops for aggregation.
```

---

## §1.16 — Export (export.py)

```
GET /api/export/csv
  Content-Type: text/csv; charset=utf-8-sig  <- UTF-8 BOM, required for Excel on Windows
  StreamingResponse
  Column order: id, filename, title, author, year, language, category, subcategory,
                difficulty, description, tags, reading_status, confidence_score,
                extraction_method, status, created_at

GET /api/export/json
  Content-Type: application/json
  StreamingResponse of JSON array
  Full book objects: shelves as array, tags as array (not JSON string)

Both support same filter params as GET /api/books.
```

---

## §1.17 — process_book_sync() — Pipeline Order (AUTHORITATIVE)


```python
async def process_book(book_id: int, filepath: str, file_type: str):
    """Runs in the async event loop via background_processor()."""
    loop = asyncio.get_event_loop()

    # Step 1: Hash + duplicate check
    file_bytes = Path(filepath).read_bytes()
    file_hash  = hashlib.sha256(file_bytes).hexdigest()
    # Check for existing hash with dedup_dismissed=0 → mark duplicate and return

    # Step 2: Extract text
    text = await loop.run_in_executor(None, extract_text, filepath, file_type)
    text_extracted = bool(text and len(text.strip()) >= 50)

    # Step 3: Write text cache (CACHE_DIR/{book_id}.txt)
    # Required by chat endpoint. Write empty file if no text.

    # Step 4: Call Ollama
    # If text_extracted → call_ollama_text(text, stem)
    # Otherwise        → call_ollama_filename(filepath)

    # Step 5: Compute confidence score
    confidence = compute_confidence(metadata, text_extracted)
    status = "done" if confidence >= 0.4 else "partial" if confidence > 0.0 else "error"

    # Step 6: Open Library enrichment (if OPENLIBRARY_ENRICH=true and title not null)
    metadata = await loop.run_in_executor(None, enrich_open_library, metadata)

    # Step 7: Cover extraction (fitz for PDF, ebooklib for EPUB)
    cover_path = await loop.run_in_executor(
        None, extract_cover, filepath, book_id, file_type
    )

    # Step 8: Save to DB + write debug JSON
    # UPDATE books SET title=?, author=?, ... WHERE id=?
    # Write DEBUG_DIR/{book_id}.json
```

**Background processor (the working pattern from v1):**

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    task = asyncio.create_task(background_processor())
    yield
    task.cancel()

async def background_processor():
    while True:
        try:
            with get_db() as conn:
                row = conn.execute(
                    "SELECT id, filepath, file_type FROM books "
                    "WHERE status='pending' LIMIT 1"
                ).fetchone()
            if row:
                await process_book(row["id"], row["filepath"], row["file_type"])
            else:
                await asyncio.sleep(4)
        except asyncio.CancelledError:
            break
        except Exception as e:
            print(f"[processor] Error: {e}")
            await asyncio.sleep(5)
```

This processes books one at a time. No ThreadPoolExecutor.
No SQLite deadlocks. No books stuck on "processing".

---

## §2 — SQLite Connection Factory (db.py)

```python
from contextlib import contextmanager

@contextmanager
def get_db():
    """Open fresh, yield, close. Simple and deadlock-free."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    try:
        yield conn
    finally:
        conn.close()
```

No thread-local pattern needed. Each `with get_db()` call opens and closes its own
connection. The async processor awaits between books so there is never concurrent
write contention.

---

## §3 — Idempotent DB Migrations (scripts/migrate_db.py)

```python
import sqlite3, os
from pathlib import Path

def _add_column(conn, table: str, column: str, definition: str):
    """Add column only if absent — SQLite has no ADD COLUMN IF NOT EXISTS."""
    existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

def run_migrations(db_path: str):
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS books (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            filename         TEXT NOT NULL,
            filepath         TEXT NOT NULL,
            status           TEXT NOT NULL DEFAULT 'processing',
            created_at       TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at       TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)

    cols = [
        ("title",             "TEXT"),
        ("author",            "TEXT"),
        ("year",              "INTEGER"),
        ("language",          "TEXT"),
        ("category",          "TEXT"),
        ("subcategory",       "TEXT"),
        ("difficulty",        "TEXT"),
        ("description",       "TEXT"),
        ("tags",              "TEXT"),
        ("error_msg",         "TEXT"),
        ("manual_fixed",      "INTEGER NOT NULL DEFAULT 0"),
        ("extraction_method", "TEXT"),
        ("confidence_score",  "REAL"),
        ("cover_path",        "TEXT"),
        ("cover_source",      "TEXT"),
        ("file_hash",         "TEXT"),
        ("duplicate_of",      "INTEGER REFERENCES books(id)"),
        ("reading_status",    "TEXT"),
        ("ol_enriched",       "INTEGER NOT NULL DEFAULT 0"),
        ("dedup_dismissed",   "INTEGER NOT NULL DEFAULT 0"),
    ]
    for col, defn in cols:
        _add_column(conn, "books", col, defn)

    conn.execute("CREATE INDEX IF NOT EXISTS idx_books_file_hash ON books(file_hash)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_books_status    ON books(status)")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS shelves (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS book_shelves (
            book_id  INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
            shelf_id INTEGER NOT NULL REFERENCES shelves(id) ON DELETE CASCADE,
            added_at TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY (book_id, shelf_id)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS categories (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            name      TEXT NOT NULL UNIQUE,
            parent_id INTEGER REFERENCES categories(id) ON DELETE SET NULL
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_categories_parent ON categories(parent_id)")

    conn.commit()
    conn.close()
    print(f"Migrations complete: {db_path}")

if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    run_migrations(os.getenv("DB_PATH", "./data/librarian.db"))
```

---

## §4 — API Serialisation (_serialize_book)

```python
import json

BOOK_DEFAULTS = {
    "title": None, "author": None, "year": None, "language": None,
    "category": None, "subcategory": None, "difficulty": None,
    "description": None, "tags": [], "error_msg": None,
    "manual_fixed": 0, "extraction_method": None, "confidence_score": None,
    "cover_path": None, "cover_source": None, "file_hash": None,
    "duplicate_of": None, "reading_status": None, "ol_enriched": 0,
    "dedup_dismissed": 0,
}

def _serialize_book(book: dict) -> dict:
    result = {**BOOK_DEFAULTS, **book}
    if isinstance(result.get("tags"), str):
        try:
            result["tags"] = json.loads(result["tags"])
        except Exception:
            result["tags"] = []
    for field in ("title", "author", "language", "category", "subcategory",
                  "difficulty", "description", "extraction_method"):
        if result.get(field) in ("", "null", "None"):
            result[field] = None
    return result
```

---

## §5 — CORS Configuration (app.py)

```python
from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)
# NEVER use allow_origins=["*"]
```

---

## §6 — requirements.txt
```
# Web framework
fastapi>=0.111.0
uvicorn[standard]>=0.29.0

# PDF extraction — PyMuPDF (fitz). No Poppler, no Tesseract, no OCR.
pymupdf>=1.24.0

# EPUB extraction
ebooklib>=0.18
beautifulsoup4>=4.12.0
lxml>=5.0.0

# Image processing (cover thumbnails via fitz pixmap)
Pillow>=10.3.0

# JSON repair (LLM response fallback parser)
json-repair>=0.28.0

# Environment variables
python-dotenv>=1.0.0

# Tests
pytest>=8.0.0
pytest-asyncio>=0.23.0
```

Removed: `pdfplumber`, `pdf2image`, `pytesseract`, `httpx`, `psutil`, `langdetect`

---

## §7 — .env (forward slashes — pathlib handles on Windows)

```ini
API_KEY=change_me_to_a_strong_random_string

DB_PATH=./data/librarian.db
COVERS_PATH=./data/covers
TEXT_CACHE_PATH=./data/text_cache
DEBUG_PATH=./data/debug
BOOKS_PATH=C:/Users/posen/Books

OPENLIBRARY_ENRICH=true

PORT=8000
FRONTEND_PORT=5173

OLLAMA_URL=http://localhost:11434
OLLAMA_MODEL=mistral:7b
LLM_MAX_CHARS=3000

LOG_LEVEL=INFO
```

Removed: `ENABLE_OCR`, `POPPLER_PATH`, `TESSERACT_PATH`, `LLM_CONCURRENCY`

Frontend `frontend/.env`:
```ini
VITE_API_URL=http://localhost:8000
VITE_API_KEY=change_me_to_a_strong_random_string
```

---

## §7.1 — Tesseract and Poppler Wiring (Windows)


```python
import fitz   # PyMuPDF — import name is always "fitz" on all platforms

def extract_text(filepath: str, file_type: str) -> str:
    """No external binaries needed. fitz is pure Python + bundled C library."""
    try:
        if file_type == "pdf":
            doc  = fitz.open(filepath)
            text = ""
            for page in doc[:3]:        # first 3 pages
                text += page.get_text()
            doc.close()
            return text[:LLM_MAX_CHARS]

        elif file_type == "epub":
            import ebooklib
            from ebooklib import epub
            from bs4 import BeautifulSoup
            book = epub.read_epub(filepath, options={"ignore_ncx": True})
            text = ""
            for item in book.get_items():
                if item.get_type() == ebooklib.ITEM_DOCUMENT:
                    soup = BeautifulSoup(item.get_content(), "lxml")
                    text += soup.get_text(" ", strip=True)
                    if len(text) > LLM_MAX_CHARS:
                        break
            return text[:LLM_MAX_CHARS]
    except Exception as e:
        print(f"[extract_text] {Path(filepath).name}: {e}")
    return ""


def extract_cover(filepath: str, book_id: int, file_type: str) -> str | None:
    """Cover via fitz pixmap (PDF) or ebooklib item (EPUB). No Poppler needed."""
    out_path = COVERS_DIR / f"{book_id}.jpg"
    try:
        if file_type == "pdf":
            import fitz
            from PIL import Image, ImageStat

            doc  = fitz.open(filepath)
            if doc.page_count == 0:
                return None
            page = doc[0]
            clip = fitz.Rect(0, 0, page.rect.width, page.rect.height / 2)
            pix  = page.get_pixmap(matrix=fitz.Matrix(1.0, 1.0), clip=clip)
            doc.close()

            img  = Image.open(io.BytesIO(pix.tobytes("jpeg")))
            stat = ImageStat.Stat(img.convert("RGB"))
            if all(m > 245 for m in stat.mean):
                return None     # blank page — skip

            img.thumbnail((300, 400))
            img.convert("RGB").save(str(out_path), "JPEG", quality=85)
            return str(out_path)

        elif file_type == "epub":
            import ebooklib
            from ebooklib import epub
            from PIL import Image

            book = epub.read_epub(filepath, options={"ignore_ncx": True})
            for item in book.get_items():
                name = item.get_name().lower()
                if "cover" in name and any(
                    name.endswith(e) for e in (".jpg", ".jpeg", ".png")
                ):
                    img = Image.open(io.BytesIO(item.get_content()))
                    img.thumbnail((300, 400))
                    img.convert("RGB").save(str(out_path), "JPEG", quality=85)
                    return str(out_path)
    except Exception as e:
        print(f"[cover] {Path(filepath).name}: {e}")
    return None
```

---

## §8 — Bootstrap and Run Scripts (PowerShell)

### setup.ps1 — run once from project root

```powershell
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

Write-Host "=== LibrarianAI v3 Setup ===" -ForegroundColor Cyan

# Create data directories
New-Item -ItemType Directory -Force -Path "data\covers","data\text_cache","data\debug" | Out-Null

# Python virtual environment
if (-not (Test-Path ".venv")) {
    python -m venv .venv
}
.\.venv\Scripts\Activate.ps1

# Python dependencies
pip install -r requirements.txt

# DB migrations
python scripts\migrate_db.py

# Frontend dependencies
Push-Location frontend
npm install
Pop-Location

Write-Host "=== Setup complete. Run: .\run.ps1 ===" -ForegroundColor Green
```

### run.ps1 — start both servers

```powershell
# Starts backend in a new PowerShell window, frontend in current terminal.
.\.venv\Scripts\Activate.ps1

$backendCmd = "cd '$PWD'; .\.venv\Scripts\Activate.ps1; " +
              "uvicorn backend.app:app --host 127.0.0.1 --port 8000 --reload"

Start-Process powershell -ArgumentList "-NoExit", "-Command", $backendCmd

Push-Location frontend
npm run dev
Pop-Location
```

---

## §9 — .gitignore

```
.env
frontend\.env
data\
*.db
*.db-wal
*.db-shm
__pycache__\
*.py[cod]
.pytest_cache\
*.egg-info\
dist\
build\
.venv\
node_modules\
frontend\dist\
frontend\.env.local
Thumbs.db
desktop.ini
.vscode\
.idea\
*.suo
*.user
```

---

## §10 — CLAUDE.md (place at project root — auto-read by Claude Code on startup)

```markdown
# LibrarianAI v3 — Claude Code Instructions

## Platform
- OS: Windows 11 native (no WSL2)
- Python: .venv\Scripts\python.exe
- Activate venv: .\.venv\Scripts\Activate.ps1
- Shell: PowerShell 7 (pwsh)
- BOOKS_PATH: C:/Users/posen/Books
- Ollama: http://localhost:11434
- Open file: subprocess.Popen(["cmd","/c","start","",str(resolved)], shell=False)

## Architecture — ONE FILE
The entire backend is backend/app.py. There are no separate modules.
Do NOT create process.py, extractors.py, llm.py, enrichment.py, covers.py,
export.py, or file_utils.py. Everything lives in app.py.

## Non-negotiable rules
1. confidence_score: (filled/9)*0.7 + text_flag*0.3 — denominator exactly 9
2. PATCHABLE_FIELDS = {title, author, year, language, category, subcategory,
   difficulty, description, tags} — PATCH /api/books/{id} accepts NOTHING else
3. SQLite: contextmanager get_db() — open fresh, yield, close. No thread-local.
4. Processor: async background_processor() polling every 4s — NO ThreadPoolExecutor
5. Text extraction: fitz (PyMuPDF) for PDF, ebooklib for EPUB — NO OCR, NO Tesseract
6. React form fields: value={field ?? ""}  — NEVER value={field || ""}
7. CSS colours: var(--css-variable) only  — NEVER hardcoded hex in components
8. open_book_file: ["cmd","/c","start","",str(resolved)] + is_relative_to(BOOKS_ROOT)
9. Chat: truncate to LLM_MAX_CHARS — never multiply
10. repair_json: 4-step _parse_llm_json() inline in app.py

## Commands (PowerShell from project root)
Activate venv:  .\.venv\Scripts\Activate.ps1
Run tests:      python -m pytest backend\tests\ -v
Start backend:  uvicorn backend.app:app --host 127.0.0.1 --port 8000 --reload
Start frontend: cd frontend; npm run dev

## After every code change
python -m pytest backend\tests\ -v
Fix all failures before continuing.

## Full technical reference
type SRS.md


---

## §11 — Complete API Endpoint List

```
GET    /api/books                         list + filters + pagination (max 500)
GET    /api/books/{id}                    full detail + shelves array
PATCH  /api/books/{id}                    PATCHABLE_FIELDS only (see §1.9)
DELETE /api/books/{id}                    ?delete_file=true removes file from disk
POST   /api/books/{id}/reprocess
POST   /api/books/{id}/fix
POST   /api/books/{id}/unfix
POST   /api/books/{id}/enrich
POST   /api/books/{id}/rename             ?dry_run=false
PATCH  /api/books/{id}/reading-status     ONLY route for reading_status
GET    /api/books/{id}/cover
POST   /api/books/{id}/cover/refresh
POST   /api/books/{id}/open              cmd /c start + BOOKS_ROOT containment check
POST   /api/books/{id}/chat              LLM_MAX_CHARS, not multiplied
GET    /api/books/{id}/debug
POST   /api/scan
GET    /api/duplicates
POST   /api/duplicates/dismiss
GET    /api/shelves
POST   /api/shelves
DELETE /api/shelves/{id}
GET    /api/shelves/{id}/books
POST   /api/shelves/{id}/books
DELETE /api/shelves/{id}/books/{book_id}
GET    /api/categories
POST   /api/categories
DELETE /api/categories/{id}
GET    /api/stats
GET    /api/analytics                     SQL aggregates only, no Python loops
GET    /api/export/csv                    UTF-8 BOM, streamed
GET    /api/export/json                   streamed array
GET    /api/health                        unauthenticated
GET    /api/auth/verify
```

---

## §12 — Frontend Rules (every React component)

```
1. Colours:    var(--bg-surface), var(--text-primary), var(--border), var(--accent)
               NEVER hardcode hex. All variables defined in src/theme.css.

2. Form fields: value={field ?? ""}     ONLY nullish coalescing
                FORBIDDEN: value={field || ""}  (breaks 0.0 confidence display)

3. confidence_score: book.confidence_score ?? 0
   FORBIDDEN:        book.confidence_score || 0

4. API client (api/client.js):
   const apiKey = localStorage.getItem("librarian_api_key")
                  ?? import.meta.env.VITE_API_KEY;
   axios.defaults.baseURL = import.meta.env.VITE_API_URL;
   axios.defaults.headers.common["X-API-Key"] = apiKey;

5. Smart components replacing plain <input> in DetailPanel:
   Category/subcategory -> CategoryComboBox  (two-level linked dropdowns + Add new)
   Language             -> LanguagePicker    (searchable ISO 639-1, ~50 languages)
   Difficulty           -> SegmentedControl  (3 buttons; click active button = null)
   Year                 -> YearPicker        (validated 1800-current+1, arrow keys)

6. Charts: plain SVG + React ONLY.
   No Recharts, Chart.js, Plotly, D3, or any charting library.
   All viewBox values hardcoded. All data from GET /api/analytics.
   Empty data -> render empty state message, not a broken chart.

7. DetailPanel tabs: "Edit" (default) | "Debug"
   DebugTab.jsx: collapsible accordion, one panel per extraction pass.
   Green cell = non-null value. Red cell = null or empty string.

8. Theme toggle in TopBar (moon/sun icon).
   Persisted to localStorage key "librarian_theme".
   Applied via: document.documentElement.setAttribute("data-theme", "dark"|"light")
```

---

## §13 — Test File Checklist


```
backend\tests\
  test_auth.py          401 no key; 200 correct key; /api/health always 200
  test_confidence.py    9 fields; 0.6889 vector; empty string = unfilled
  test_covers.py        fitz pixmap blank detection (mean>245); path returned on success
  test_debug.py         JSON written after processing; endpoint returns it
  test_dedup.py         same hash → duplicate_of set; dismissed → not re-flagged
  test_enrichment.py    null filled; non-null not overwritten; out-of-range overwritten
  test_export.py        CSV has UTF-8 BOM; correct headers; JSON valid array
  test_open_file.py     path inside BOOKS_PATH → 200;
                        path outside BOOKS_PATH → 403;
                        file missing → 404
  test_patch_fields.py  filepath in body → ignored; title in body → applied
  test_rename.py        collision → _01 suffix; _99 exhaustion → skipped
  test_serialise.py     all BOOK_DEFAULTS present; "" → None; tags always list
  test_shelves.py       CRUD; multi-shelf membership; cascade delete

NOTE: No test_ocr.py — OCR is removed entirely from the project.
```


---

## §14 — Windows-Specific Gotchas for Claude Code

```
1. PowerShell execution policy (first-time only):
   Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser

2. Running pytest correctly:
   python -m pytest backend\tests\ -v

3. pathlib.Path forward slashes:
   Path("C:/Users/posen/Books") works perfectly on Windows.
   .resolve() normalises to the Windows canonical form.

4. SQLite file locking on Windows:
   Do not open librarian.db in DB Browser for SQLite while uvicorn is running.
   Close all external DB tools before running pytest.

5. subprocess.Popen for open_book_file:
   ["cmd", "/c", "start", "", str(resolved)]
   The empty string is the window title — required when filepath has spaces.
   shell=False is correct and intentional.

6. PyMuPDF install name vs import name:
   pip install pymupdf        ← install name
   import fitz                ← import name (always "fitz", not "pymupdf")
   This is a known quirk of the package. Both refer to the same library.

7. Uvicorn --reload on Windows:
   Works correctly with watchfiles (bundled with uvicorn[standard]).
   Source must be on a local drive (C:\), not a network share.

8. Port conflicts — check and kill:
   netstat -ano | findstr ":8000"
   taskkill /PID <pid> /F

9. Books stuck on "processing" (if it ever recurs):
   This means the async background processor crashed silently.
   Check uvicorn terminal for [processor] Error lines.
   Restart uvicorn — the processor restarts with it.
   The polling loop picks up all pending books automatically on restart.

10. Ollama not responding:
    Check Windows system tray — Ollama icon should be present.
    If missing: start Ollama from Start menu or run: ollama serve
    Verify: curl http://localhost:11434/api/tags
```
