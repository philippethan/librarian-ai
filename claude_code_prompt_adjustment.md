# Claude Code Prompt — Project Adjustment
# Paste this as a single message at the start of a new Claude Code session.
# ─────────────────────────────────────────────────────────────────────────────

Read CLAUDE.md and SRS.md before doing anything else.
Then confirm you have read both by stating the platform and listing rule #4
(the processor pattern rule).

---

## Context

The LibrarianAI v3 project already has a working frontend and working
deduplication. The backend extraction pipeline was broken — books were stuck
on "processing" forever due to ThreadPoolExecutor + SQLite deadlocks on Windows.

The fix has been designed. Your job is to apply it cleanly to the existing
project without touching the frontend.

---

## What to do

### Step 1 — Replace backend/app.py

Replace the entire content of `backend/app.py` with the new single-file
implementation. The new app.py:
- Uses async background_processor() polling every 4s (no ThreadPoolExecutor)
- Uses fitz (PyMuPDF) for PDF text extraction (no pdfplumber, no OCR)
- Uses ebooklib + BeautifulSoup for EPUB extraction
- Uses fitz pixmap for cover extraction (no pdf2image, no Poppler)
- Contains ALL backend logic inline — no imports from other backend modules
- Uses get_db() contextmanager (open fresh, yield, close — no thread-local)

The complete implementation is in the downloaded app.py file.
Copy it to backend/app.py exactly as provided.

### Step 2 — Update requirements.txt

Replace requirements.txt with:

```
fastapi>=0.111.0
uvicorn[standard]>=0.29.0
pymupdf>=1.24.0
ebooklib>=0.18
beautifulsoup4>=4.12.0
lxml>=5.0.0
Pillow>=10.3.0
json-repair>=0.28.0
python-dotenv>=1.0.0
pytest>=8.0.0
pytest-asyncio>=0.23.0
```

Then run:
```powershell
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Confirm pymupdf installed correctly by running:
```powershell
python -c "import fitz; print(fitz.__version__)"
```
It should print a version number. Note: the install name is `pymupdf` but the
import name is always `fitz`. This is expected.

### Step 3 — Update .env

Make sure .env contains these variables and NO others related to OCR:

```ini
API_KEY=librarian_dev_key
DB_PATH=./data/librarian.db
COVERS_PATH=./data/covers
TEXT_CACHE_PATH=./data/text_cache
DEBUG_PATH=./data/debug
BOOKS_PATH=C:/Users/posen/Documents/Books
OPENLIBRARY_ENRICH=true
PORT=8000
FRONTEND_PORT=5173
OLLAMA_URL=http://localhost:11434
OLLAMA_MODEL=mistral:7b-instruct
LLM_MAX_CHARS=3000
LOG_LEVEL=INFO
```

Remove these lines if present (no longer needed):
- ENABLE_OCR
- POPPLER_PATH
- TESSERACT_PATH
- LLM_CONCURRENCY

### Step 4 — Clean up unused modules

The following files are no longer imported and can be removed:

```powershell
Remove-Item backend\process.py    -ErrorAction SilentlyContinue
Remove-Item backend\extractors.py -ErrorAction SilentlyContinue
Remove-Item backend\llm.py        -ErrorAction SilentlyContinue
Remove-Item backend\enrichment.py -ErrorAction SilentlyContinue
Remove-Item backend\covers.py     -ErrorAction SilentlyContinue
Remove-Item backend\export.py     -ErrorAction SilentlyContinue
Remove-Item backend\file_utils.py -ErrorAction SilentlyContinue
```

Do NOT remove:
- backend/app.py       (the new single-file backend)
- backend/db.py        (only if it exists and is imported — check first)
- backend/auth.py      (only if it exists and is imported — check first)
- backend/tests/       (keep all existing test files)

### Step 5 — Verify backend starts cleanly

Run:
```powershell
uvicorn backend.app:app --host 127.0.0.1 --port 8000 --reload
```

Confirm:
1. No ImportError on startup
2. GET http://localhost:8000/api/health returns {"status": "ok", ...}
3. No [processor] Error in the terminal output after 5 seconds

If there is an ImportError, show me the full traceback and fix it.

### Step 6 — Scan a test folder and confirm processing works

Using the running backend, trigger a scan:
```powershell
curl -X POST http://localhost:8000/api/scan `
  -H "Content-Type: application/json" `
  -H "X-API-Key: librarian_dev_key" `
  -d '{"path": "C:/Users/posen/Books"}'
```

Then wait 10 seconds and check:
```powershell
curl http://localhost:8000/api/stats `
  -H "X-API-Key: librarian_dev_key"
```

The response should show books moving out of "pending"/"processing" into
"done" or "partial". If all books stay on "processing" after 30 seconds,
show me the uvicorn terminal output — the processor is crashing silently.

### Step 7 — Update CLAUDE.md

Replace the entire content of CLAUDE.md with the updated version from
SRS_patch_P1.md §10. The key changes are:
- Architecture note: ONE FILE, no separate modules
- Rule #4: async background_processor, NO ThreadPoolExecutor
- Rule #5: fitz for PDF, NO OCR, NO Tesseract
- Removed: TESSERACT_PATH and POPPLER_PATH references

---

## What NOT to touch

- frontend/ — any file under frontend/ — do not modify
- .gitignore
- setup.ps1 / run.ps1

---

## Done when

1. `python -c "import fitz; print(fitz.__version__)"` prints a version
2. `uvicorn backend.app:app` starts with no ImportError
3. GET /api/health returns 200
4. After scanning, books move from "pending" to "done" or "partial" in /api/stats
5. The uvicorn terminal shows lines like:
   `[process_book] id=1 status=done confidence=0.7111 title='...'`
