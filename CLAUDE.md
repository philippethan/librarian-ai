# LibrarianAI — Backend Development

## Platform
- Windows 11 native, PowerShell 7
- Python .venv — activate: .\.venv\Scripts\Activate.ps1
- Run backend: uvicorn backend.app:app --host 127.0.0.1 --port 8000 --reload
- Test: curl.exe http://localhost:8000/api/health
- Ollama model: mistral:7b-instruct (verify: curl.exe http://localhost:11434/api/tags)
- Books path: C:/Users/posen/Documents/Books

## Architecture — one file only
Everything lives in backend/app.py.
No separate modules. No imports from other backend files.

## Rules that never change
1. Async background_processor() polling every 4s — no ThreadPoolExecutor
2. get_db() contextmanager — open, yield, close — no thread-local
3. OLLAMA_MODEL read inside call_ollama() at call time — not at module load
4. ensure_ascii=True in all json.dumps() calls sent to Ollama
5. No em dashes in prompt strings — use plain hyphens only
6. PATCHABLE_FIELDS whitelist on PATCH /api/books/{id}
7. os.startfile() for open_book on native Windows

## Build order — do not skip steps
Step 1: health endpoint + DB init + scan
Step 2: text extraction (fitz/ebooklib) — test independently
Step 3: Ollama call — test with one book, verify JSON returned
Step 4: full process_book pipeline
Step 5: all remaining API endpoints
Step 6: single HTML frontend

## After every step
Test with curl.exe before moving to the next step.
Show me the curl.exe output. If anything returns an error, fix it before continuing.
```

---

