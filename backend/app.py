import asyncio
import hashlib
import json
import os
import re
import sqlite3
import urllib.request
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path

import fitz  # PyMuPDF
from bs4 import BeautifulSoup
import ebooklib
from ebooklib import epub

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

DB_PATH = os.environ.get("DB_PATH", "backend/data/librarian.db")
BOOKS_PATH = os.environ.get("BOOKS_PATH", "C:/Users/posen/Documents/Books")

os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)


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
                processed_at      TEXT
            )
        """)
        conn.commit()


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


def call_ollama(text: str) -> dict:
    ollama_url = os.getenv("OLLAMA_URL", "http://localhost:11434")
    ollama_model = os.getenv("OLLAMA_MODEL", "mistral:7b-instruct")
    snippet = text[:1500]
    prompt = (
        "You are a librarian metadata extractor. Given the following book text, "
        "extract metadata and return it as JSON.\n\n"
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
        f"Book text:\n{snippet}\n\n"
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
        print(f"[call_ollama] error: {exc}")
        return {}


def call_ollama_filename(filepath: str) -> dict:
    ollama_url = os.getenv("OLLAMA_URL", "http://localhost:11434")
    ollama_model = os.getenv("OLLAMA_MODEL", "mistral:7b-instruct")
    stem = Path(filepath).stem
    try:
        file_size = os.path.getsize(filepath)
    except Exception:
        file_size = 0
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
        f"Filename: {stem}\n"
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


def process_book(book_id: int, filepath: str, file_type: str):
    # Step 1: set status=processing
    with get_db() as conn:
        conn.execute(
            "UPDATE books SET status='processing', error_msg=NULL WHERE id=?",
            (book_id,),
        )
        conn.commit()

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
    if text:
        meta = call_ollama(text)
        method = "fitz+ollama"
    else:
        meta = call_ollama_filename(filepath)
        method = "filename_heuristic"

    title = meta.get("title") or ""
    author = meta.get("author") or ""
    year = meta.get("year") or ""
    language = meta.get("language") or ""
    category = meta.get("category") or ""
    subcategory = meta.get("subcategory") or ""
    difficulty = meta.get("difficulty") or ""
    description = meta.get("description") or ""
    tags_raw = meta.get("tags", [])
    tags = json.dumps(tags_raw if isinstance(tags_raw, list) else [], ensure_ascii=True)

    # Step 5: confidence score
    filled_fields = sum(1 for v in [title, author, year, language, category, subcategory, difficulty, description, tags_raw] if v)
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


async def background_processor():
    while True:
        await asyncio.sleep(4)
        try:
            with get_db() as conn:
                rows = conn.execute(
                    "SELECT id, filepath, file_type FROM books WHERE status='pending' LIMIT 5"
                ).fetchall()
            for row in rows:
                process_book(row["id"], row["filepath"], row["file_type"])
        except Exception as exc:
            print(f"[background_processor] error: {exc}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    task = asyncio.create_task(background_processor())
    yield
    task.cancel()


app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------- endpoints ----------

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


@app.get("/api/stats")
def get_stats():
    with get_db() as conn:
        total = conn.execute("SELECT COUNT(*) FROM books").fetchone()[0]
        rows = conn.execute(
            "SELECT status, COUNT(*) as cnt FROM books GROUP BY status"
        ).fetchall()

    by_status = {row["status"]: row["cnt"] for row in rows}
    return {"total": total, "by_status": by_status}
