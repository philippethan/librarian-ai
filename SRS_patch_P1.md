# SRS.md — Patch P1 (Architecture Revision)
# Apply this patch by replacing the affected sections in SRS.md
# All other sections remain unchanged.
# ─────────────────────────────────────────────────────────────────────────────

## PATCH SUMMARY

| # | Section affected | Change |
|---|-----------------|--------|
| P1-01 | §0 Platform Baseline | Remove Tesseract and Poppler — no OCR |
| P1-02 | §1 System Context | Single app.py replaces multi-module backend |
| P1-03 | §1.2 Extraction Pipeline | fitz replaces pdfplumber, OCR removed entirely |
| P1-04 | §1.17 Pipeline order | Simplified 8-step pipeline, no OCR step |
| P1-05 | §6 requirements.txt | pymupdf replaces pdfplumber, OCR packages removed |
| P1-06 | §7 .env | TESSERACT_PATH and POPPLER_PATH removed |
| P1-07 | §7.1 NEW | fitz extraction pattern replaces Tesseract/Poppler section |
| P1-08 | §10 CLAUDE.md | Updated rules and removed OCR references |
| P1-09 | §14 Windows Gotchas | Items 6,7,8 (Poppler/Tesseract) removed |

# ─────────────────────────────────────────────────────────────────────────────
# REPLACEMENT SECTIONS — replace the matching section in SRS.md with each block
# ─────────────────────────────────────────────────────────────────────────────

---

## §0 — Platform Baseline  [REPLACE EXISTING §0]

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

## §1 — System Context  [REPLACE EXISTING §1]

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

## §1.2 — Extraction Pipeline  [REPLACE EXISTING §1.2]

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

## §1.2.1 — Confidence Score Formula  [UNCHANGED — kept for reference]

```python
SCORED_FIELDS = [
    "title", "author", "year", "language",
    "category", "subcategory", "difficulty", "description", "tags",
]
# Exactly 9 fields. extraction_method is NOT scored.

def compute_confidence(meta: dict, text_extracted: bool) -> float:
    filled = sum(
        1 for f in SCORED_FIELDS
        if meta.get(f) not in (None, "", [], "null", "None")
    )
    score = (filled / 9) * 0.7 + (1.0 if text_extracted else 0.0) * 0.3
    return round(min(max(score, 0.0), 1.0), 4)

# Test vectors (unchanged):
# all 9 None  + no text  → 0.0
# all 9 filled + text    → 1.0
# 5/9 filled  + text     → 0.6889
```

---

## §1.3 — Ollama Integration  [REPLACE EXISTING §1.3]

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

## §1.17 — process_book() — Pipeline Order  [REPLACE EXISTING §1.17]

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

## §2 — SQLite Connection Factory  [REPLACE EXISTING §2]

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

## §6 — requirements.txt  [REPLACE EXISTING §6]

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

## §7 — .env  [REPLACE EXISTING §7]

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

## §7.1 — Text and Cover Extraction with fitz  [REPLACE EXISTING §7.1]

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

## §10 — CLAUDE.md  [REPLACE ENTIRE CLAUDE.MD FILE]

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
```

---

## §13 — Test File Checklist  [REPLACE EXISTING §13]

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

## §14 — Windows-Specific Gotchas  [REPLACE EXISTING §14]

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
