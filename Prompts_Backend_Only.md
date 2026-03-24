## The prompts — one step at a time

Each prompt below is one Claude Code session. Do not combine them. After each one, you run the test yourself in PowerShell and confirm it works before moving on. That's how you learn and catch problems early.

---

### Prompt 1 — Skeleton + health + DB + scan
```
Read CLAUDE.md.

Create backend/app.py with only these three things:

1. FastAPI app with lifespan (init_db on startup, background_processor task)

2. SQLite init_db() creating this table:
   books(id, filename, filepath, file_type, file_size, file_hash,
         status DEFAULT 'pending', error_msg,
         title, author, year, language, category, subcategory,
         difficulty, description, tags DEFAULT '[]',
         extraction_method, confidence_score,
         cover_path, reading_status DEFAULT 'to-read',
         manual_fixed DEFAULT 0, duplicate_of,
         dedup_dismissed DEFAULT 0,
         added_at DEFAULT datetime('now'), processed_at)

3. These endpoints only:
   GET  /api/health   → {"status": "ok", "model": OLLAMA_MODEL}
   POST /api/scan     → body: {"path": "..."}, walks rglob for pdf/epub,
                        inserts each as status='pending', returns counts
   GET  /api/books    → returns all books, tags parsed from JSON string
   GET  /api/stats    → total, by_status counts

4. background_processor() that polls for pending books every 4s
   and calls process_book() — but process_book() for now just:
   - sets status='error', error_msg='not implemented'
   - prints [process_book] id={id}

Use get_db() contextmanager pattern.
Use CORS allow_origins localhost:5173 only.
No auth for now — we add it last.
Read BOOKS_PATH and OLLAMA_MODEL from env with sensible defaults.

When done, start the server and confirm:
  curl.exe http://localhost:8000/api/health
returns {"status": "ok", ...}

Then scan the books folder:
  curl.exe -X POST http://localhost:8000/api/scan
    -H "Content-Type: application/json"
    -d "{\"path\": \"C:/Users/posen/Documents/Books\"}"

Show me both outputs before stopping.
```

**You verify:** health returns 200, scan returns `{"added": N, ...}`, stats shows books with status=error. You understand every line of what was created.

---

### Prompt 2 — Text extraction only
```
Read CLAUDE.md.

Add extract_text(filepath, file_type) to backend/app.py.

Implementation:
- PDF: fitz.open(), extract text from first 3 pages, return text[:3000]
- EPUB: ebooklib + BeautifulSoup, extract from ITEM_DOCUMENT items, return text[:3000]
- On any exception: print the error, return ""

Also add this test endpoint:
  POST /api/books/{book_id}/extract-test
  → calls extract_text() on that book's filepath
  → returns {"text_length": N, "preview": text[:200], "file_type": ...}

Do not change process_book() yet. Do not touch anything else.

When done, test with a real PDF:
  curl.exe -X POST http://localhost:8000/api/books/1/extract-test
    -H "Content-Type: application/json"

Show me the output. text_length should be > 0 for a real PDF.
```

**You verify:** text_length > 0 for a normal PDF, 0 for a scanned one. You now know which of your books have extractable text.

---

### Prompt 3 — Ollama call only
```
Read CLAUDE.md.

Add call_ollama(text) to backend/app.py.

Implementation rules:
- Read OLLAMA_URL and OLLAMA_MODEL from os.getenv() INSIDE the function
- Use urllib.request only (no httpx)
- json.dumps payload with ensure_ascii=True — this is mandatory
- Timeout: 120s
- On exception: print the error, return {}

The prompt must:
- Ask for: title, author, year, language, category, subcategory,
           difficulty, description, tags
- Category list (use plain hyphens, no em dashes):
  "Systems Engineering, Railway & Transport Engineering, Aerospace Engineering,
   Electrical Engineering, Electronics & Embedded Systems, Mechanical Engineering,
   Civil & Structural Engineering, Control & Automation, Software Engineering,
   Computer Science, AI & Machine Learning, Mathematics, Physics,
   Standards & Norms - Railway, Standards & Norms - Safety,
   Project Management, Business & Strategy, Economics & Finance,
   Personal Development, Psychology, History, Philosophy,
   Language Learning, Literature & Fiction, Health & Medicine, Other"
- End with: "Return ONLY valid JSON. No markdown. No explanation."
- Send only text[:1500] — not the full 3000

Also add _parse_llm_json(raw) with the 4-step recovery:
  1. strip ``` fences
  2. json.loads direct
  3. regex {.*} extract
  4. json_repair fallback
  Return {} on total failure.

Also add this test endpoint:
  POST /api/books/{book_id}/ollama-test
  → extract text from that book
  → call call_ollama(text)
  → return the raw metadata dict

When done, test:
  curl.exe -X POST http://localhost:8000/api/books/2/ollama-test
    -H "Content-Type: application/json"

The response must contain title, author, category etc.
If you see {"error": ...} or {} — show me and we fix it before continuing.
Do not touch process_book() yet.
```

**You verify:** Ollama returns real metadata for at least one book. This is the critical gate. Nothing else matters until this works.

---

### Prompt 4 — Wire process_book
```
Read CLAUDE.md.

Now update process_book(book_id, filepath, file_type) to run the real pipeline.
This function already exists — replace its body only.

Pipeline steps in exact order:
1. Set status='processing'
2. SHA-256 hash the file, check for duplicate (file_hash match, dedup_dismissed=0)
   If duplicate: set status='duplicate', duplicate_of=existing_id, return
3. extract_text() — write result to data/text_cache/{book_id}.txt
4. If text is non-empty: call_ollama(text), method="fitz+ollama"
   If text is empty:     call_ollama_filename(filepath), method="filename_heuristic"
5. confidence_score = (filled_fields/9)*0.7 + (1.0 if text else 0.0)*0.3
   filled_fields = count of non-null/non-empty values in:
   [title, author, year, language, category, subcategory, difficulty, description, tags]
6. status = "done" if confidence>=0.4 else "partial" if confidence>0 else "error"
7. Save all fields to DB
8. Print: [process_book] id={id} status={status} confidence={score} title={title!r}

Also add call_ollama_filename(filepath) — same as call_ollama but prompt uses
only the filename stem and file size. Return {} on failure.

Do not add enrichment or covers yet. Keep it simple.

Reprocess book 2 to test:
  curl.exe -X POST http://localhost:8000/api/books/2/reprocess
    -H "Content-Type: application/json"

Wait 45 seconds, then check:
  curl.exe http://localhost:8000/api/books/2

Show me the output. title and category should be populated.
```

**You verify:** one book goes from partial/error to done with real metadata. This confirms the full pipeline works end to end.

---

### Prompt 5 — Remaining CRUD endpoints
```
Read CLAUDE.md.

The pipeline works. Now add the remaining API endpoints to backend/app.py.
Add them one section at a time and confirm no existing endpoint breaks.

Section A — Book management:
  GET  /api/books/{id}              full book detail
  PATCH /api/books/{id}             PATCHABLE_FIELDS only:
                                    {title,author,year,language,category,
                                     subcategory,difficulty,description,tags}
  DELETE /api/books/{id}            ?delete_file=false
  POST /api/books/{id}/fix          manual_fixed=1
  POST /api/books/{id}/unfix        manual_fixed=0
  PATCH /api/books/{id}/reading-status  {"status":"reading"}
  POST /api/books/{id}/open         os.startfile(resolved_path)
                                    check is_relative_to(BOOKS_ROOT) first

Section B — Covers:
  GET  /api/books/{id}/cover        FileResponse from cover_path
  POST /api/books/{id}/cover/refresh  re-run extract_cover()
  Add extract_cover(filepath, book_id, file_type) using fitz pixmap for PDF,
  ebooklib item for EPUB. Save to data/covers/{book_id}.jpg. Max 300x400.

Section C — Duplicates + shelves:
  GET  /api/duplicates
  POST /api/duplicates/dismiss      {"hash":"..."}
  GET/POST/DELETE /api/shelves
  GET/POST/DELETE /api/shelves/{id}/books

Section D — Export:
  GET /api/export/csv    UTF-8 BOM, StreamingResponse
  GET /api/export/json   StreamingResponse

Section E — Analytics:
  GET /api/analytics     all SQL aggregates, no Python loops

After each section, test the new endpoints with curl.exe before adding the next.
Show me one test output per section.
```

---

### Prompt 6 — Single HTML frontend
```
Read CLAUDE.md.

Create a single file: backend/static/index.html

This is a self-contained HTML+CSS+JS file. No build step, no npm, no React.
It talks directly to the FastAPI backend at http://localhost:8000.

The page must have:
1. Top bar: title "LibrarianAI", scan button, search input, stats bar
   (total books, done count, processing count — polled every 5s from /api/stats)

2. Book list table with columns:
   cover thumbnail | title | author | year | category | status badge |
   confidence bar | reading status | actions (open, reprocess)

3. Click a row → detail panel slides in from the right showing:
   - All metadata fields as editable inputs
   - Save button (PATCH /api/books/{id})
   - Open File button (POST /api/books/{id}/open)
   - Reading status dropdown
   - Confidence score as a coloured bar

4. Scan modal: text input for folder path, submit calls POST /api/scan

5. Filter bar: status dropdown, category dropdown (from /api/categories)

Style: clean, minimal, dark sidebar (#1a1a2e), white content area.
No external CSS frameworks. Inline everything in one file.

Also add to app.py:
  from fastapi.staticfiles import StaticFiles
  app.mount("/", StaticFiles(directory="backend/static", html=True), name="static")
  Create backend/static/ directory.

Test: open http://localhost:8000 in browser.
The book list should load and show your 981 books.