# LibrarianAI v3 — Claude Code Instructions

## Platform
- OS: Windows 11 native (no WSL2)
- Python: .venv\Scripts\python.exe
- Activate venv: .\.venv\Scripts\Activate.ps1
- Shell: PowerShell 7 (pwsh)
- BOOKS_PATH: C:/Users/Than/Books
- Ollama: http://localhost:11434
- Tesseract: C:/Program Files/Tesseract-OCR/tesseract.exe
- Poppler: C:/poppler/Library/bin
- Open file: subprocess.Popen(["cmd","/c","start","",str(resolved)], shell=False)

## Non-negotiable rules
1. confidence_score: (filled/9)*0.7 + text_flag*0.3 — denominator exactly 9
2. PATCHABLE_FIELDS = {title, author, year, language, category, subcategory,
   difficulty, description, tags} — PATCH /api/books/{id} accepts NOTHING else
3. SQLite: WAL mode + per-thread connections via get_conn() in db.py
4. React form fields: value={field ?? ""}  — NEVER value={field || ""}
5. CSS colours: var(--css-variable) only  — NEVER hardcoded hex in components
6. open_book_file: ["cmd","/c","start","",str(resolved)] + is_relative_to(BOOKS_ROOT)
7. Chat: truncate to LLM_MAX_CHARS only — never multiply
8. repair_json: 4-step strategy from SRS.md §1.3
9. Migrations: _add_column() helper always — never bare ALTER TABLE

## Commands (PowerShell from project root)
Activate venv:  .\.venv\Scripts\Activate.ps1
Run tests:      python -m pytest backend\tests\ -v
Start backend:  uvicorn backend.app:app --host 127.0.0.1 --port 8000 --reload
Start frontend: cd frontend; npm run dev
Migrate DB:     python scripts\migrate_db.py

## After every code change
python -m pytest backend\tests\ -v
Fix all failures before continuing.

## Full technical reference
type SRS.md