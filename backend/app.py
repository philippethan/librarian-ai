import asyncio
import csv
import hashlib
import io
import json
import os
import re
import sqlite3
import urllib.request
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
from typing import List, Optional

import fitz  # PyMuPDF
from bs4 import BeautifulSoup
import ebooklib
from ebooklib import epub
from PIL import Image

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

DB_PATH = os.environ.get("DB_PATH", "backend/data/librarian.db")
BOOKS_PATH = os.environ.get("BOOKS_PATH", "C:/Users/posen/Documents/Books")
BOOKS_ROOT = Path(BOOKS_PATH)
COVERS_DIR = Path("backend/data/covers")

PATCHABLE_FIELDS = {"title", "author", "year", "language", "category",
                    "subcategory", "difficulty", "description", "tags"}

os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
COVERS_DIR.mkdir(parents=True, exist_ok=True)


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


# ---------- health + scan ----------

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


# ---------- books list + stats ----------

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


# ---------- Section A: book management ----------

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


# ---------- Section B: covers ----------

@app.get("/api/books/{book_id}/cover")
def get_cover(book_id: int):
    with get_db() as conn:
        row = conn.execute(
            "SELECT cover_path, filepath, file_type FROM books WHERE id=?", (book_id,)
        ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Book not found")

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


# ---------- Section C: duplicates + shelves ----------

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


# ---------- Section D: export ----------

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


# ---------- Section E: analytics ----------

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


@app.get("/api/categories")
def get_categories():
    with get_db() as conn:
        rows = conn.execute(
            "SELECT DISTINCT category FROM books WHERE category IS NOT NULL AND category != '' ORDER BY category"
        ).fetchall()
    return [row["category"] for row in rows]


os.makedirs("backend/static", exist_ok=True)
app.mount("/", StaticFiles(directory="backend/static", html=True), name="static")
