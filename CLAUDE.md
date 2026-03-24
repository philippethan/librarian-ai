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
