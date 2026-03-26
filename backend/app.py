import asyncio
import hashlib
import json
import os
import sqlite3
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path

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


def process_book(book_id: int):
    with get_db() as conn:
        conn.execute(
            "UPDATE books SET status='error', error_msg='not implemented' WHERE id=?",
            (book_id,),
        )
        conn.commit()
    print(f"[process_book] id={book_id}")


async def background_processor():
    while True:
        await asyncio.sleep(4)
        try:
            with get_db() as conn:
                rows = conn.execute(
                    "SELECT id FROM books WHERE status='pending' LIMIT 5"
                ).fetchall()
            for row in rows:
                process_book(row["id"])
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


@app.get("/api/stats")
def get_stats():
    with get_db() as conn:
        total = conn.execute("SELECT COUNT(*) FROM books").fetchone()[0]
        rows = conn.execute(
            "SELECT status, COUNT(*) as cnt FROM books GROUP BY status"
        ).fetchall()

    by_status = {row["status"]: row["cnt"] for row in rows}
    return {"total": total, "by_status": by_status}
