import csv
import io
import json
import logging
import os
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse

from backend.auth import require_auth
from backend.db import get_conn

logger = logging.getLogger(__name__)

DB_PATH = os.getenv("DB_PATH", "./data/librarian.db")
BOOKS_ROOT = Path(os.getenv("BOOKS_PATH", r"C:\Users\Than\Books")).resolve()

PATCHABLE_FIELDS = {
    "title", "author", "year", "language",
    "category", "subcategory", "difficulty",
    "description", "tags",
}

BOOK_DEFAULTS = {
    "title": None, "author": None, "year": None, "language": None,
    "category": None, "subcategory": None, "difficulty": None,
    "description": None, "tags": [], "error_msg": None,
    "manual_fixed": 0, "extraction_method": None, "confidence_score": None,
    "cover_path": None, "cover_source": None, "file_hash": None,
    "duplicate_of": None, "reading_status": None, "ol_enriched": 0,
    "dedup_dismissed": 0,
}

_executor = ThreadPoolExecutor(max_workers=int(os.getenv("SCAN_WORKERS", "4")))

app = FastAPI(title="LibrarianAI v3")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _serialize_book(book) -> dict:
    result = {**BOOK_DEFAULTS, **dict(book)}
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


def _book_or_404(book_id: int) -> dict:
    conn = get_conn(DB_PATH)
    row = conn.execute("SELECT * FROM books WHERE id=?", (book_id,)).fetchone()
    if not row:
        raise HTTPException(404, detail="Book not found.")
    return dict(row)


def _book_filter_sql(
    search: str | None,
    status: str | None,
    category: str | None,
    language: str | None,
    reading_status: str | None,
    shelf_id: int | None,
    author: str | None,
    difficulty: str | None,
) -> tuple[list[str], list]:
    where: list[str] = []
    params: list = []
    if search:
        like = f"%{search}%"
        where.append("(b.title LIKE ? OR b.author LIKE ? OR b.filename LIKE ?)")
        params += [like, like, like]
    if status:
        where.append("b.status = ?")
        params.append(status)
    if category:
        where.append("b.category = ?")
        params.append(category)
    if language:
        where.append("b.language = ?")
        params.append(language)
    if reading_status:
        where.append("b.reading_status = ?")
        params.append(reading_status)
    if shelf_id is not None:
        where.append(
            "b.id IN (SELECT book_id FROM book_shelves WHERE shelf_id = ?)"
        )
        params.append(shelf_id)
    if author:
        where.append("b.author = ?")
        params.append(author)
    if difficulty:
        where.append("b.difficulty = ?")
        params.append(difficulty)
    return where, params


# ---------------------------------------------------------------------------
# Health / Auth
# ---------------------------------------------------------------------------

@app.get("/api/health")
async def health():
    from backend.llm import OLLAMA_MODEL, OLLAMA_URL
    return {"status": "ok", "ollama_model": OLLAMA_MODEL, "ollama_url": OLLAMA_URL}


@app.get("/api/auth/verify")
async def auth_verify(_=Depends(require_auth)):
    return {"authenticated": True}


# ---------------------------------------------------------------------------
# Books — list / detail
# ---------------------------------------------------------------------------

@app.get("/api/books")
async def list_books(
    _=Depends(require_auth),
    search: str | None = Query(default=None),
    status: str | None = Query(default=None),
    category: str | None = Query(default=None),
    language: str | None = Query(default=None),
    reading_status: str | None = Query(default=None),
    shelf_id: int | None = Query(default=None),
    author: str | None = Query(default=None),
    difficulty: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=50, ge=1, le=500),
):
    where, params = _book_filter_sql(
        search, status, category, language, reading_status, shelf_id, author, difficulty
    )
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    offset = (page - 1) * limit

    conn = get_conn(DB_PATH)
    total = conn.execute(
        f"SELECT COUNT(*) FROM books b {where_sql}", params
    ).fetchone()[0]
    rows = conn.execute(
        f"SELECT b.* FROM books b {where_sql} ORDER BY b.id DESC LIMIT ? OFFSET ?",
        params + [limit, offset],
    ).fetchall()

    return {
        "total": total,
        "page": page,
        "limit": limit,
        "books": [_serialize_book(r) for r in rows],
    }


@app.get("/api/books/{book_id}")
async def get_book(book_id: int, _=Depends(require_auth)):
    book = _book_or_404(book_id)
    conn = get_conn(DB_PATH)
    shelves = conn.execute(
        "SELECT s.id, s.name, s.created_at FROM shelves s "
        "JOIN book_shelves bs ON s.id = bs.shelf_id WHERE bs.book_id=?",
        (book_id,),
    ).fetchall()
    result = _serialize_book(book)
    result["shelves"] = [dict(s) for s in shelves]
    return result


# ---------------------------------------------------------------------------
# Books — PATCH
# ---------------------------------------------------------------------------

@app.patch("/api/books/{book_id}")
async def patch_book(book_id: int, body: dict, _=Depends(require_auth)):
    _book_or_404(book_id)
    safe = {k: v for k, v in body.items() if k in PATCHABLE_FIELDS}
    if not safe:
        raise HTTPException(400, detail="No valid fields to update.")

    # Serialize tags to JSON string for storage
    if "tags" in safe:
        if isinstance(safe["tags"], list):
            safe["tags"] = json.dumps(safe["tags"])
        else:
            safe["tags"] = json.dumps([])

    set_clause = ", ".join(f"{k}=?" for k in safe)
    values = list(safe.values()) + [book_id]
    conn = get_conn(DB_PATH)
    conn.execute(
        f"UPDATE books SET {set_clause}, manual_fixed=1, updated_at=datetime('now') WHERE id=?",
        values,
    )
    conn.commit()
    return _serialize_book(_book_or_404(book_id))


# ---------------------------------------------------------------------------
# Books — DELETE
# ---------------------------------------------------------------------------

@app.delete("/api/books/{book_id}", status_code=200)
async def delete_book(
    book_id: int,
    delete_file: bool = Query(default=False),
    _=Depends(require_auth),
):
    book = _book_or_404(book_id)
    if delete_file:
        try:
            Path(book["filepath"]).unlink(missing_ok=True)
        except Exception as exc:
            logger.warning("Could not delete file %s: %s", book["filepath"], exc)
    conn = get_conn(DB_PATH)
    conn.execute("DELETE FROM books WHERE id=?", (book_id,))
    conn.commit()
    return {"deleted": book_id}


# ---------------------------------------------------------------------------
# Books — reprocess / fix / unfix / enrich / rename
# ---------------------------------------------------------------------------

@app.post("/api/books/{book_id}/reprocess")
async def reprocess_book(book_id: int, _=Depends(require_auth)):
    book = _book_or_404(book_id)
    conn = get_conn(DB_PATH)
    conn.execute(
        "UPDATE books SET status='processing', updated_at=datetime('now') WHERE id=?",
        (book_id,),
    )
    conn.commit()
    from backend.process import process_book_sync
    _executor.submit(process_book_sync, book_id, book["filepath"], DB_PATH)
    return _serialize_book(_book_or_404(book_id))


@app.post("/api/books/{book_id}/fix")
async def fix_book(book_id: int, _=Depends(require_auth)):
    _book_or_404(book_id)
    conn = get_conn(DB_PATH)
    conn.execute(
        "UPDATE books SET manual_fixed=1, updated_at=datetime('now') WHERE id=?",
        (book_id,),
    )
    conn.commit()
    return _serialize_book(_book_or_404(book_id))


@app.post("/api/books/{book_id}/unfix")
async def unfix_book(book_id: int, _=Depends(require_auth)):
    _book_or_404(book_id)
    conn = get_conn(DB_PATH)
    conn.execute(
        "UPDATE books SET manual_fixed=0, updated_at=datetime('now') WHERE id=?",
        (book_id,),
    )
    conn.commit()
    return _serialize_book(_book_or_404(book_id))


@app.post("/api/books/{book_id}/enrich")
async def enrich_book_endpoint(book_id: int, _=Depends(require_auth)):
    _book_or_404(book_id)
    from backend.enrichment import enrich_book
    enrich_book(book_id, DB_PATH)
    return _serialize_book(_book_or_404(book_id))


@app.post("/api/books/{book_id}/rename")
async def rename_book(
    book_id: int,
    dry_run: bool = Query(default=False),
    _=Depends(require_auth),
):
    from backend.file_utils import rename_book_file
    return rename_book_file(book_id, DB_PATH, dry_run=dry_run)


# ---------------------------------------------------------------------------
# Books — reading status
# ---------------------------------------------------------------------------

VALID_READING_STATUSES = {"to-read", "reading", "done", None}


@app.patch("/api/books/{book_id}/reading-status")
async def set_reading_status(book_id: int, body: dict, _=Depends(require_auth)):
    _book_or_404(book_id)
    rs = body.get("reading_status")
    if rs not in VALID_READING_STATUSES:
        raise HTTPException(400, detail=f"Invalid reading_status: {rs!r}")
    conn = get_conn(DB_PATH)
    conn.execute(
        "UPDATE books SET reading_status=?, updated_at=datetime('now') WHERE id=?",
        (rs, book_id),
    )
    conn.commit()
    return _serialize_book(_book_or_404(book_id))


# ---------------------------------------------------------------------------
# Books — cover
# ---------------------------------------------------------------------------

@app.get("/api/books/{book_id}/cover")
async def get_cover(book_id: int, _=Depends(require_auth)):
    book = _book_or_404(book_id)
    cover_path = book.get("cover_path")
    if cover_path and Path(cover_path).exists():
        return FileResponse(cover_path, media_type="image/jpeg")
    # Fallback: check default location
    from backend.covers import get_cover_path
    path = get_cover_path(book_id)
    if path:
        return FileResponse(str(path), media_type="image/jpeg")
    raise HTTPException(404, detail="Cover not found.")


@app.post("/api/books/{book_id}/cover/refresh")
async def refresh_cover(book_id: int, _=Depends(require_auth)):
    book = _book_or_404(book_id)
    from backend.covers import extract_cover
    new_path = extract_cover(book_id, book["filepath"], DB_PATH)
    if new_path:
        conn = get_conn(DB_PATH)
        conn.execute(
            "UPDATE books SET cover_path=?, cover_source='local', updated_at=datetime('now') WHERE id=?",
            (new_path, book_id),
        )
        conn.commit()
    return _serialize_book(_book_or_404(book_id))


# ---------------------------------------------------------------------------
# Books — open (Windows shell)
# ---------------------------------------------------------------------------

@app.post("/api/books/{book_id}/open")
async def open_book_file(book_id: int, _=Depends(require_auth)):
    book = _book_or_404(book_id)
    try:
        resolved = Path(book["filepath"]).resolve()
    except Exception:
        raise HTTPException(400, detail="Invalid file path.")

    if not resolved.is_relative_to(BOOKS_ROOT):
        raise HTTPException(403, detail="File path is outside the configured library root.")

    if not resolved.exists():
        raise HTTPException(404, detail="File not found on disk.")

    subprocess.Popen(
        ["cmd", "/c", "start", "", str(resolved)],
        shell=False,
    )
    return {"opened": str(resolved)}


# ---------------------------------------------------------------------------
# Books — chat
# ---------------------------------------------------------------------------

@app.post("/api/books/{book_id}/chat")
async def chat_book(book_id: int, body: dict, _=Depends(require_auth)):
    _book_or_404(book_id)
    cache_file = (
        Path(os.getenv("TEXT_CACHE_PATH", "./data/text_cache")) / f"{book_id}.txt"
    )
    text = cache_file.read_text(encoding="utf-8") if cache_file.exists() else ""

    max_chars = int(os.getenv("LLM_MAX_CHARS", "3000"))
    context = text[:max_chars]

    system = (
        "You are a knowledgeable assistant helping the user understand a book. "
        "Answer questions based on the provided text excerpt. Be concise."
    )
    from backend.llm import call_ollama_chat
    reply = await call_ollama_chat(system, context, body.get("message", ""))
    return {"reply": reply, "context_chars": len(context)}


# ---------------------------------------------------------------------------
# Books — debug
# ---------------------------------------------------------------------------

@app.get("/api/books/{book_id}/debug")
async def get_debug(book_id: int, _=Depends(require_auth)):
    _book_or_404(book_id)
    debug_file = Path(os.getenv("DEBUG_PATH", "./data/debug")) / f"{book_id}.json"
    if not debug_file.exists():
        raise HTTPException(404, detail="Debug data not available.")
    return json.loads(debug_file.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Scan
# ---------------------------------------------------------------------------

@app.post("/api/scan")
async def scan_library(body: dict, _=Depends(require_auth)):
    scan_path = body.get("path")
    if not scan_path:
        raise HTTPException(400, detail="Missing 'path' in request body.")

    root = Path(scan_path)
    if not root.exists() or not root.is_dir():
        raise HTTPException(400, detail=f"Path does not exist or is not a directory: {scan_path}")

    from backend.process import process_book_sync

    conn = get_conn(DB_PATH)
    queued = 0
    for filepath in root.rglob("*"):
        if filepath.suffix.lower() not in (".pdf", ".epub"):
            continue

        abs_path = str(filepath.resolve()).replace("\\", "/")
        filename = filepath.name

        existing = conn.execute(
            "SELECT id FROM books WHERE filepath=?", (abs_path,)
        ).fetchone()
        if existing:
            book_id = existing["id"]
        else:
            cursor = conn.execute(
                "INSERT INTO books (filename, filepath, status) VALUES (?, ?, 'processing')",
                (filename, abs_path),
            )
            conn.commit()
            book_id = cursor.lastrowid

        _executor.submit(process_book_sync, book_id, abs_path, DB_PATH)
        queued += 1

    return {"queued": queued}


# ---------------------------------------------------------------------------
# Duplicates
# ---------------------------------------------------------------------------

@app.get("/api/duplicates")
async def list_duplicates(_=Depends(require_auth)):
    import os
    conn = get_conn(DB_PATH)
    rows = conn.execute(
        "SELECT id, filename, filepath, file_hash FROM books "
        "WHERE file_hash IS NOT NULL AND duplicate_of IS NOT NULL "
        "ORDER BY file_hash"
    ).fetchall()

    groups: dict[str, list] = {}
    for row in rows:
        h = row["file_hash"]
        fp = row["filepath"]
        try:
            filesize = os.path.getsize(fp) if fp and os.path.exists(fp) else None
        except OSError:
            filesize = None
        groups.setdefault(h, []).append(
            {"id": row["id"], "filename": row["filename"], "filepath": fp, "filesize": filesize}
        )
    return [{"hash": h, "books": books} for h, books in groups.items()]


@app.post("/api/duplicates/dismiss")
async def dismiss_duplicate(body: dict, _=Depends(require_auth)):
    file_hash = body.get("hash")
    if not file_hash:
        raise HTTPException(400, detail="Missing 'hash' in request body.")
    conn = get_conn(DB_PATH)
    conn.execute(
        "UPDATE books SET dedup_dismissed=1 WHERE file_hash=?", (file_hash,)
    )
    conn.commit()
    return {"dismissed": file_hash}


# ---------------------------------------------------------------------------
# Shelves
# ---------------------------------------------------------------------------

@app.get("/api/shelves")
async def list_shelves(_=Depends(require_auth)):
    conn = get_conn(DB_PATH)
    rows = conn.execute(
        "SELECT s.*, COUNT(bs.book_id) AS book_count "
        "FROM shelves s LEFT JOIN book_shelves bs ON s.id = bs.shelf_id "
        "GROUP BY s.id ORDER BY s.name"
    ).fetchall()
    return [dict(r) for r in rows]


@app.post("/api/shelves", status_code=201)
async def create_shelf(body: dict, _=Depends(require_auth)):
    name = body.get("name", "").strip()
    if not name:
        raise HTTPException(400, detail="Shelf name is required.")
    conn = get_conn(DB_PATH)
    try:
        cursor = conn.execute(
            "INSERT INTO shelves (name) VALUES (?)", (name,)
        )
        conn.commit()
    except Exception:
        raise HTTPException(409, detail=f"Shelf '{name}' already exists.")
    row = conn.execute(
        "SELECT * FROM shelves WHERE id=?", (cursor.lastrowid,)
    ).fetchone()
    return dict(row)


@app.delete("/api/shelves/{shelf_id}", status_code=200)
async def delete_shelf(shelf_id: int, _=Depends(require_auth)):
    conn = get_conn(DB_PATH)
    row = conn.execute("SELECT id FROM shelves WHERE id=?", (shelf_id,)).fetchone()
    if not row:
        raise HTTPException(404, detail="Shelf not found.")
    conn.execute("DELETE FROM shelves WHERE id=?", (shelf_id,))
    conn.commit()
    return {"deleted": shelf_id}


@app.get("/api/shelves/{shelf_id}/books")
async def get_shelf_books(shelf_id: int, _=Depends(require_auth)):
    conn = get_conn(DB_PATH)
    row = conn.execute("SELECT id FROM shelves WHERE id=?", (shelf_id,)).fetchone()
    if not row:
        raise HTTPException(404, detail="Shelf not found.")
    rows = conn.execute(
        "SELECT b.* FROM books b "
        "JOIN book_shelves bs ON b.id = bs.book_id "
        "WHERE bs.shelf_id=? ORDER BY b.id DESC",
        (shelf_id,),
    ).fetchall()
    return [_serialize_book(r) for r in rows]


@app.post("/api/shelves/{shelf_id}/books", status_code=201)
async def add_book_to_shelf(shelf_id: int, body: dict, _=Depends(require_auth)):
    book_id = body.get("book_id")
    if book_id is None:
        raise HTTPException(400, detail="Missing 'book_id' in request body.")
    conn = get_conn(DB_PATH)
    if not conn.execute("SELECT id FROM shelves WHERE id=?", (shelf_id,)).fetchone():
        raise HTTPException(404, detail="Shelf not found.")
    _book_or_404(int(book_id))
    try:
        conn.execute(
            "INSERT INTO book_shelves (book_id, shelf_id) VALUES (?, ?)",
            (book_id, shelf_id),
        )
        conn.commit()
    except Exception:
        pass  # already on shelf — idempotent
    return {"shelf_id": shelf_id, "book_id": book_id}


@app.delete("/api/shelves/{shelf_id}/books/{book_id}", status_code=200)
async def remove_book_from_shelf(shelf_id: int, book_id: int, _=Depends(require_auth)):
    conn = get_conn(DB_PATH)
    conn.execute(
        "DELETE FROM book_shelves WHERE shelf_id=? AND book_id=?",
        (shelf_id, book_id),
    )
    conn.commit()
    return {"shelf_id": shelf_id, "book_id": book_id}


# ---------------------------------------------------------------------------
# Categories
# ---------------------------------------------------------------------------

@app.get("/api/categories")
async def list_categories(_=Depends(require_auth)):
    conn = get_conn(DB_PATH)
    rows = conn.execute("SELECT * FROM categories ORDER BY name").fetchall()
    return [dict(r) for r in rows]


@app.post("/api/categories", status_code=201)
async def create_category(body: dict, _=Depends(require_auth)):
    name = body.get("name", "").strip()
    if not name:
        raise HTTPException(400, detail="Category name is required.")
    parent_id = body.get("parent_id")
    conn = get_conn(DB_PATH)
    try:
        cursor = conn.execute(
            "INSERT INTO categories (name, parent_id) VALUES (?, ?)",
            (name, parent_id),
        )
        conn.commit()
    except Exception:
        raise HTTPException(409, detail=f"Category '{name}' already exists.")
    row = conn.execute(
        "SELECT * FROM categories WHERE id=?", (cursor.lastrowid,)
    ).fetchone()
    return dict(row)


@app.delete("/api/categories/{category_id}", status_code=200)
async def delete_category(category_id: int, _=Depends(require_auth)):
    conn = get_conn(DB_PATH)
    if not conn.execute(
        "SELECT id FROM categories WHERE id=?", (category_id,)
    ).fetchone():
        raise HTTPException(404, detail="Category not found.")
    conn.execute("DELETE FROM categories WHERE id=?", (category_id,))
    conn.commit()
    return {"deleted": category_id}


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------

@app.get("/api/stats")
async def stats(_=Depends(require_auth)):
    conn = get_conn(DB_PATH)
    total = conn.execute("SELECT COUNT(*) FROM books").fetchone()[0]
    done = conn.execute(
        "SELECT COUNT(*) FROM books WHERE status='done'"
    ).fetchone()[0]
    partial = conn.execute(
        "SELECT COUNT(*) FROM books WHERE status='partial'"
    ).fetchone()[0]
    error = conn.execute(
        "SELECT COUNT(*) FROM books WHERE status='error'"
    ).fetchone()[0]
    processing = conn.execute(
        "SELECT COUNT(*) FROM books WHERE status='processing'"
    ).fetchone()[0]
    shelves_count = conn.execute("SELECT COUNT(*) FROM shelves").fetchone()[0]
    return {
        "total_books": total,
        "done": done,
        "partial": partial,
        "error": error,
        "processing": processing,
        "total_shelves": shelves_count,
    }


# ---------------------------------------------------------------------------
# Analytics (all SQL aggregates — no Python loops)
# ---------------------------------------------------------------------------

@app.get("/api/analytics")
async def analytics(_=Depends(require_auth)):
    conn = get_conn(DB_PATH)

    books_per_category = [
        dict(r) for r in conn.execute(
            "SELECT category, COUNT(*) AS count FROM books "
            "WHERE category IS NOT NULL GROUP BY category ORDER BY count DESC"
        ).fetchall()
    ]

    books_per_author = [
        dict(r) for r in conn.execute(
            "SELECT author, COUNT(*) AS count FROM books "
            "WHERE author IS NOT NULL GROUP BY author ORDER BY count DESC LIMIT 20"
        ).fetchall()
    ]

    language_distribution = [
        dict(r) for r in conn.execute(
            "SELECT language, COUNT(*) AS count FROM books "
            "WHERE language IS NOT NULL GROUP BY language ORDER BY count DESC"
        ).fetchall()
    ]

    rs_row = conn.execute("""
        SELECT
            SUM(CASE WHEN reading_status='to-read'  THEN 1 ELSE 0 END) AS "to_read",
            SUM(CASE WHEN reading_status='reading'  THEN 1 ELSE 0 END) AS reading,
            SUM(CASE WHEN reading_status='done'     THEN 1 ELSE 0 END) AS done,
            SUM(CASE WHEN reading_status IS NULL    THEN 1 ELSE 0 END) AS unset
        FROM books
    """).fetchone()
    reading_status_counts = {
        "to-read": rs_row["to_read"] or 0,
        "reading": rs_row["reading"] or 0,
        "done": rs_row["done"] or 0,
        "unset": rs_row["unset"] or 0,
    }

    confidence_histogram = [
        dict(r) for r in conn.execute("""
            SELECT
                CASE
                    WHEN confidence_score < 0.2 THEN '0.0-0.2'
                    WHEN confidence_score < 0.4 THEN '0.2-0.4'
                    WHEN confidence_score < 0.6 THEN '0.4-0.6'
                    WHEN confidence_score < 0.8 THEN '0.6-0.8'
                    ELSE '0.8-1.0'
                END AS bucket,
                COUNT(*) AS count
            FROM books
            WHERE confidence_score IS NOT NULL
            GROUP BY bucket
            ORDER BY bucket
        """).fetchall()
    ]

    books_added_per_month = [
        dict(r) for r in conn.execute("""
            SELECT strftime('%Y-%m', created_at) AS month, COUNT(*) AS count
            FROM books
            WHERE created_at >= date('now', '-12 months')
            GROUP BY month
            ORDER BY month
        """).fetchall()
    ]

    total_books = conn.execute("SELECT COUNT(*) AS c FROM books").fetchone()["c"]
    total_shelves = conn.execute("SELECT COUNT(*) AS c FROM shelves").fetchone()["c"]

    return {
        "books_per_category": books_per_category,
        "books_per_author": books_per_author,
        "language_distribution": language_distribution,
        "reading_status_counts": reading_status_counts,
        "confidence_histogram": confidence_histogram,
        "books_added_per_month": books_added_per_month,
        "total_books": total_books,
        "total_shelves": total_shelves,
    }


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

CSV_COLUMNS = [
    "id", "filename", "title", "author", "year", "language",
    "category", "subcategory", "difficulty", "description",
    "tags", "reading_status", "confidence_score",
    "extraction_method", "status", "created_at",
]


def _build_export_query(
    search: str | None,
    status: str | None,
    category: str | None,
    language: str | None,
    reading_status: str | None,
    shelf_id: int | None,
    author: str | None,
    difficulty: str | None,
) -> tuple[str, list]:
    where, params = _book_filter_sql(
        search, status, category, language, reading_status, shelf_id, author, difficulty
    )
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    sql = f"SELECT b.* FROM books b {where_sql} ORDER BY b.id"
    return sql, params


@app.get("/api/export/csv")
async def export_csv(
    _=Depends(require_auth),
    search: str | None = Query(default=None),
    status: str | None = Query(default=None),
    category: str | None = Query(default=None),
    language: str | None = Query(default=None),
    reading_status: str | None = Query(default=None),
    shelf_id: int | None = Query(default=None),
    author: str | None = Query(default=None),
    difficulty: str | None = Query(default=None),
):
    sql, params = _build_export_query(
        search, status, category, language, reading_status, shelf_id, author, difficulty
    )
    conn = get_conn(DB_PATH)
    rows = conn.execute(sql, params).fetchall()

    def generate():
        yield b"\xef\xbb\xbf"  # UTF-8 BOM
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(CSV_COLUMNS)
        yield buf.getvalue().encode("utf-8")
        for row in rows:
            buf.seek(0)
            buf.truncate(0)
            b = _serialize_book(row)
            writer.writerow([
                b.get("id"), b.get("filename"), b.get("title"), b.get("author"),
                b.get("year"), b.get("language"), b.get("category"),
                b.get("subcategory"), b.get("difficulty"), b.get("description"),
                json.dumps(b.get("tags", [])), b.get("reading_status"),
                b.get("confidence_score"), b.get("extraction_method"),
                b.get("status"), b.get("created_at"),
            ])
            yield buf.getvalue().encode("utf-8")

    return StreamingResponse(
        generate(),
        media_type="text/csv; charset=utf-8-sig",
        headers={"Content-Disposition": "attachment; filename=library.csv"},
    )


@app.get("/api/export/json")
async def export_json(
    _=Depends(require_auth),
    search: str | None = Query(default=None),
    status: str | None = Query(default=None),
    category: str | None = Query(default=None),
    language: str | None = Query(default=None),
    reading_status: str | None = Query(default=None),
    shelf_id: int | None = Query(default=None),
    author: str | None = Query(default=None),
    difficulty: str | None = Query(default=None),
):
    sql, params = _build_export_query(
        search, status, category, language, reading_status, shelf_id, author, difficulty
    )
    conn = get_conn(DB_PATH)
    rows = conn.execute(sql, params).fetchall()

    def generate():
        yield b"["
        first = True
        for row in rows:
            b = _serialize_book(row)
            shelves = conn.execute(
                "SELECT s.id, s.name FROM shelves s "
                "JOIN book_shelves bs ON s.id = bs.shelf_id WHERE bs.book_id=?",
                (b["id"],),
            ).fetchall()
            b["shelves"] = [dict(s) for s in shelves]
            if not first:
                yield b","
            yield json.dumps(b).encode("utf-8")
            first = False
        yield b"]"

    return StreamingResponse(generate(), media_type="application/json")
