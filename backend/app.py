"""
LibrarianAI v3 — Backend
Platform  : Native Windows 11
Extraction: PyMuPDF (fitz) for PDF, ebooklib for EPUB
Processor : Async background loop — proven pattern from working v1
"""

import os, io, csv, json, re, sqlite3, hashlib, asyncio, time
from pathlib import Path
from datetime import datetime
from contextlib import asynccontextmanager, contextmanager
from typing import Optional
import urllib.request, urllib.error, urllib.parse

from fastapi import FastAPI, HTTPException, Query, Body, Header, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse

# ── Config ─────────────────────────────────────────────────────────────────────

BASE_DIR   = Path(__file__).parent
DB_PATH    = Path(os.getenv("DB_PATH",         str(BASE_DIR / "data" / "librarian.db")))
COVERS_DIR = Path(os.getenv("COVERS_PATH",     str(BASE_DIR / "data" / "covers")))
CACHE_DIR  = Path(os.getenv("TEXT_CACHE_PATH", str(BASE_DIR / "data" / "text_cache")))
DEBUG_DIR  = Path(os.getenv("DEBUG_PATH",      str(BASE_DIR / "data" / "debug")))
BOOKS_ROOT = Path(os.getenv("BOOKS_PATH",      r"C:\Users\posen\Documents\Books")).resolve()
API_KEY    = os.getenv("API_KEY", "")
OL_ENRICH  = os.getenv("OPENLIBRARY_ENRICH", "true").lower() == "true"

for _d in (DB_PATH.parent, COVERS_DIR, CACHE_DIR, DEBUG_DIR):
    _d.mkdir(parents=True, exist_ok=True)

# ── Category list ──────────────────────────────────────────────────────────────
# Tailored for a technical/academic personal library.
# Read at call time so changes to this list take effect on next reprocess.

CATEGORIES = (
    "Systems Engineering, "
    "Railway & Transport Engineering, "
    "Aerospace Engineering, "
    "Electrical Engineering, "
    "Electronics & Embedded Systems, "
    "Mechanical Engineering, "
    "Civil & Structural Engineering, "
    "Control & Automation, "
    "Signal Processing, "
    "Software Engineering, "
    "Computer Science, "
    "Artificial Intelligence & Machine Learning, "
    "Data Science & Statistics, "
    "Cybersecurity & Networks, "
    "Operating Systems & Infrastructure, "
    "Mathematics, "
    "Physics, "
    "Chemistry, "
    "Biology & Life Sciences, "
    "Earth & Environmental Sciences, "
    "Standards & Norms — Railway, "
    "Standards & Norms — Safety & SIL, "
    "Standards & Norms — Quality, "
    "Standards & Norms — General, "
    "Project Management, "
    "Systems Management & MBSE, "
    "Business & Strategy, "
    "Economics & Finance, "
    "Law & Regulations, "
    "Personal Development, "
    "Psychology & Cognitive Science, "
    "Leadership & Management, "
    "Productivity & Habits, "
    "Communication & Relationships, "
    "History, "
    "Philosophy, "
    "Religion & Spirituality, "
    "Language Learning, "
    "Arts & Humanities, "
    "Literature & Fiction, "
    "Biography & Memoir, "
    "Health & Medicine, "
    "Sports & Fitness, "
    "Cooking & Food, "
    "Travel, "
    "Reference & Encyclopaedia, "
    "Other"
)

# ── Confidence score ───────────────────────────────────────────────────────────

SCORED_FIELDS = [
    "title", "author", "year", "language",
    "category", "subcategory", "difficulty", "description", "tags",
]

def compute_confidence(meta: dict, text_extracted: bool) -> float:
    filled = sum(
        1 for f in SCORED_FIELDS
        if meta.get(f) not in (None, "", [], "null", "None")
    )
    score = (filled / 9) * 0.7 + (1.0 if text_extracted else 0.0) * 0.3
    return round(min(max(score, 0.0), 1.0), 4)

# ── Database ───────────────────────────────────────────────────────────────────

@contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    try:
        yield conn
    finally:
        conn.close()


def init_db():
    with get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS books (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                filename          TEXT NOT NULL,
                filepath          TEXT NOT NULL UNIQUE,
                file_type         TEXT,
                file_size         INTEGER,
                file_hash         TEXT,
                duplicate_of      INTEGER REFERENCES books(id),
                dedup_dismissed   INTEGER NOT NULL DEFAULT 0,
                status            TEXT NOT NULL DEFAULT 'pending',
                error_msg         TEXT,
                manual_fixed      INTEGER NOT NULL DEFAULT 0,
                title             TEXT,
                author            TEXT,
                year              TEXT,
                language          TEXT,
                category          TEXT,
                subcategory       TEXT,
                difficulty        TEXT,
                description       TEXT,
                tags              TEXT DEFAULT '[]',
                publisher         TEXT,
                isbn              TEXT,
                extraction_method TEXT,
                confidence_score  REAL,
                cover_path        TEXT,
                ol_enriched       INTEGER NOT NULL DEFAULT 0,
                reading_status    TEXT DEFAULT 'to-read',
                notes             TEXT,
                rating            INTEGER DEFAULT 0,
                added_at          TEXT DEFAULT (datetime('now')),
                processed_at      TEXT
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_file_hash ON books(file_hash)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_status    ON books(status)")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS shelves (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                name       TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS book_shelves (
                book_id  INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
                shelf_id INTEGER NOT NULL REFERENCES shelves(id) ON DELETE CASCADE,
                added_at TEXT NOT NULL DEFAULT (datetime('now')),
                PRIMARY KEY (book_id, shelf_id)
            )
        """)
        conn.commit()


def serialize_book(row) -> dict:
    b = dict(row)
    try:
        b["tags"] = json.loads(b.get("tags") or "[]")
    except Exception:
        b["tags"] = []
    for f in ("title", "author", "language", "category", "subcategory",
              "difficulty", "description", "extraction_method", "error_msg"):
        if b.get(f) in ("", "null", "None"):
            b[f] = None
    return b

# ── Lifespan ───────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    task = asyncio.create_task(background_processor())
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

# ── App ────────────────────────────────────────────────────────────────────────

app = FastAPI(title="LibrarianAI v3", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Auth ───────────────────────────────────────────────────────────────────────

def check_auth(x_api_key: str = Header(default="", alias="X-API-Key")):
    if API_KEY and x_api_key != API_KEY:
        raise HTTPException(401, "Unauthorized")

# ── Background processor (v1 pattern — DO NOT CHANGE) ─────────────────────────

async def background_processor():
    """Poll for pending books every 4 seconds. One at a time. No threads."""
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


async def process_book(book_id: int, filepath: str, file_type: str):
    loop = asyncio.get_event_loop()

    with get_db() as conn:
        conn.execute("UPDATE books SET status='processing' WHERE id=?", (book_id,))
        conn.commit()

    try:
        # Step 1: Hash + duplicate check
        file_bytes = Path(filepath).read_bytes()
        file_hash  = hashlib.sha256(file_bytes).hexdigest()
        with get_db() as conn:
            existing = conn.execute(
                "SELECT id FROM books WHERE file_hash=? AND id!=? AND dedup_dismissed=0",
                (file_hash, book_id)
            ).fetchone()
            if existing:
                conn.execute(
                    "UPDATE books SET status='duplicate', duplicate_of=?, "
                    "file_hash=? WHERE id=?",
                    (existing["id"], file_hash, book_id)
                )
                conn.commit()
                return
            conn.execute("UPDATE books SET file_hash=? WHERE id=?", (file_hash, book_id))
            conn.commit()

        # Step 2: Extract text
        text = await loop.run_in_executor(None, extract_text, filepath, file_type)

        # Step 3: Write text cache
        (CACHE_DIR / f"{book_id}.txt").write_text(text or "", encoding="utf-8")

        # Step 4: Call Ollama — if any text at all, use it; else use filename
        if text and text.strip():
            metadata       = await loop.run_in_executor(None, call_ollama, text)
            method         = "fitz+ollama" if file_type == "pdf" else "ebooklib+ollama"
            text_extracted = True
        else:
            metadata       = await loop.run_in_executor(None, call_ollama_filename, filepath)
            method         = "filename_heuristic"
            text_extracted = False

        if not metadata:
            metadata = {}

        # Step 5: Confidence
        confidence = compute_confidence(metadata, text_extracted)
        status = "done" if confidence >= 0.4 else "partial" if confidence > 0.0 else "error"

        # Step 6: OL enrichment
        if OL_ENRICH and metadata.get("title"):
            metadata = await loop.run_in_executor(None, enrich_open_library, metadata)

        # Step 7: Cover
        cover_path = await loop.run_in_executor(
            None, extract_cover, filepath, book_id, file_type
        )

        # Step 8: Save
        with get_db() as conn:
            conn.execute("""
                UPDATE books SET
                    title=?, author=?, year=?, language=?,
                    category=?, subcategory=?, difficulty=?,
                    description=?, tags=?,
                    extraction_method=?, confidence_score=?,
                    status=?, cover_path=?,
                    processed_at=datetime('now'), error_msg=NULL
                WHERE id=?
            """, (
                metadata.get("title"), metadata.get("author"),
                metadata.get("year"),  metadata.get("language"),
                metadata.get("category"), metadata.get("subcategory"),
                metadata.get("difficulty"), metadata.get("description"),
                json.dumps(metadata.get("tags", [])),
                method, confidence, status, cover_path, book_id,
            ))
            conn.commit()

        # Step 9: Debug JSON
        (DEBUG_DIR / f"{book_id}.json").write_text(
            json.dumps({
                "book_id": book_id, "filename": Path(filepath).name,
                "text_length": len(text) if text else 0,
                "text_extracted": text_extracted, "method": method,
                "metadata": metadata, "confidence": confidence,
                "status": status, "timestamp": datetime.utcnow().isoformat(),
            }, indent=2, ensure_ascii=False),
            encoding="utf-8"
        )

        print(
            f"[process_book] id={book_id} status={status} "
            f"confidence={confidence} title={metadata.get('title')!r}"
        )

    except Exception as e:
        with get_db() as conn:
            conn.execute(
                "UPDATE books SET status='error', error_msg=? WHERE id=?",
                (str(e)[:500], book_id)
            )
            conn.commit()
        print(f"[process_book] ERROR id={book_id}: {e}")

# ── Text extraction ────────────────────────────────────────────────────────────

def extract_text(filepath: str, file_type: str) -> str:
    try:
        if file_type == "pdf":
            import fitz
            doc  = fitz.open(filepath)
            text = ""
            for page in doc[:3]:
                text += page.get_text()
            doc.close()
            return text[:3000]
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
                    if len(text) > 3000:
                        break
            return text[:3000]
    except Exception as e:
        print(f"[extract_text] {Path(filepath).name}: {e}")
    return ""

# ── Ollama ─────────────────────────────────────────────────────────────────────
# IMPORTANT: read OLLAMA_MODEL from env inside each function at call time.
# This is the v1 pattern that worked — env changes are always picked up.

def call_ollama(text: str) -> dict:
    ollama_url   = os.getenv("OLLAMA_URL",   "http://localhost:11434")
    ollama_model = os.getenv("OLLAMA_MODEL", "mistral:latest")

    prompt = f"""Analyze this document excerpt and return a JSON object with these exact fields:
- title (string or null)
- author (string or null)
- year (string or null, 4 digits e.g. "2019")
- language (2-letter ISO code: "en", "fr", "km", "de", "es", etc.)
- category (pick the single best match from: {CATEGORIES})
- subcategory (string or null — specific topic within the category)
- difficulty (null, "Beginner", "Intermediate", or "Advanced")
- description (1-2 sentence summary or null)
- tags (array of 3-5 specific keywords)

Rules:
- Return ONLY valid JSON. No explanation. No markdown fences.
- For technical books prefer specific engineering categories over generic ones.
- If the document is in French, set language to "fr".
- Infer year only if clearly stated in the text; otherwise null.

Document excerpt:
{text[:2000]}"""

    payload = json.dumps({
        "model":   ollama_model,
        "prompt":  prompt,
        "stream":  False,
        "options": {"temperature": 0.1, "num_predict": 500},
    }).encode()

    try:
        req = urllib.request.Request(
            f"{ollama_url}/api/generate", data=payload,
            headers={"Content-Type": "application/json"}, method="POST",
        )
        with urllib.request.urlopen(req, timeout=120) as resp:
            raw = json.loads(resp.read()).get("response", "")
        return _parse_llm_json(raw)
    except Exception as e:
        print(f"[ollama] {e}")
        return {}


def call_ollama_filename(filepath: str) -> dict:
    ollama_url   = os.getenv("OLLAMA_URL",   "http://localhost:11434")
    ollama_model = os.getenv("OLLAMA_MODEL", "mistral:latest")

    stem      = Path(filepath).stem
    file_size = Path(filepath).stat().st_size if Path(filepath).exists() else 0

    prompt = f"""Based only on this filename, return a JSON object:
- title (infer from filename, or null)
- author (infer from filename, or null)
- year (infer from filename, or null)
- language (null)
- category (pick from: {CATEGORIES})
- subcategory (null)
- difficulty (null)
- description (null)
- tags (1-3 keywords based on filename)

Return ONLY valid JSON. No markdown.

Filename: {stem}
File size: {file_size} bytes"""

    payload = json.dumps({
        "model":   ollama_model,
        "prompt":  prompt,
        "stream":  False,
        "options": {"temperature": 0.1, "num_predict": 300},
    }).encode()

    try:
        req = urllib.request.Request(
            f"{ollama_url}/api/generate", data=payload,
            headers={"Content-Type": "application/json"}, method="POST",
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = json.loads(resp.read()).get("response", "")
        return _parse_llm_json(raw)
    except Exception as e:
        print(f"[ollama_filename] {e}")
        return {}


def _parse_llm_json(raw: str) -> dict:
    text = re.sub(r"```(?:json)?", "", raw).strip()
    try:
        result = json.loads(text)
        return result if isinstance(result, dict) else {}
    except Exception:
        pass
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        try:
            result = json.loads(m.group())
            return result if isinstance(result, dict) else {}
        except Exception:
            pass
    try:
        from json_repair import repair_json as _repair
        result = json.loads(_repair(text))
        return result if isinstance(result, dict) else {}
    except Exception:
        pass
    return {}

# ── Open Library enrichment ────────────────────────────────────────────────────

def enrich_open_library(meta: dict) -> dict:
    try:
        title  = urllib.parse.quote(str(meta.get("title", "")))
        author = urllib.parse.quote(str(meta.get("author", "") or ""))
        url    = f"https://openlibrary.org/search.json?title={title}&author={author}&limit=1"
        req    = urllib.request.Request(url, headers={"User-Agent": "LibrarianAI/3.0"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
        docs = data.get("docs", [])
        if not docs:
            return meta
        doc    = docs[0]
        result = dict(meta)
        cy     = datetime.now().year
        yr = doc.get("first_publish_year")
        if yr and (not result.get("year") or not str(result.get("year","")).isdigit() or
                   not (1800 <= int(result["year"]) <= cy+1)):
            if 1800 <= int(yr) <= cy+1:
                result["year"] = str(yr)
        if not result.get("author"):
            authors = doc.get("author_name", [])
            if authors: result["author"] = authors[0]
        if not result.get("description"):
            fs = doc.get("first_sentence", {})
            result["description"] = (
                fs.get("value") if isinstance(fs, dict) else
                fs if isinstance(fs, str) else None
            )
        if not result.get("language") or len(str(result.get("language","")))<2:
            langs = doc.get("language", [])
            if langs: result["language"] = langs[0][:2].lower()
        ol_tags  = [s.lower() for s in doc.get("subject", [])[:5]]
        existing = result.get("tags", [])
        existing = existing if isinstance(existing, list) else []
        result["tags"] = list(dict.fromkeys(existing + ol_tags))[:10]
        time.sleep(0.5)
        return result
    except Exception as e:
        print(f"[open_library] {e}")
        return meta

# ── Cover extraction ───────────────────────────────────────────────────────────

def extract_cover(filepath: str, book_id: int, file_type: str) -> Optional[str]:
    out_path = COVERS_DIR / f"{book_id}.jpg"
    try:
        if file_type == "pdf":
            import fitz
            from PIL import Image, ImageStat
            doc  = fitz.open(filepath)
            if doc.page_count == 0: return None
            page = doc[0]
            clip = fitz.Rect(0, 0, page.rect.width, page.rect.height / 2)
            pix  = page.get_pixmap(matrix=fitz.Matrix(1.0, 1.0), clip=clip)
            doc.close()
            img  = Image.open(io.BytesIO(pix.tobytes("jpeg")))
            stat = ImageStat.Stat(img.convert("RGB"))
            if all(m > 245 for m in stat.mean): return None
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
                if "cover" in name and any(name.endswith(e) for e in (".jpg",".jpeg",".png")):
                    img = Image.open(io.BytesIO(item.get_content()))
                    img.thumbnail((300, 400))
                    img.convert("RGB").save(str(out_path), "JPEG", quality=85)
                    return str(out_path)
    except Exception as e:
        print(f"[cover] {Path(filepath).name}: {e}")
    return None

# ══════════════════════════════════════════════════════════════════════════════
# API Routes
# ══════════════════════════════════════════════════════════════════════════════

PATCHABLE_FIELDS = {
    "title","author","year","language","category","subcategory",
    "difficulty","description","tags",
}

@app.get("/api/health")
def health():
    return {
        "status":     "ok",
        "model":      os.getenv("OLLAMA_MODEL", "mistral:latest"),
        "ollama_url": os.getenv("OLLAMA_URL",   "http://localhost:11434"),
    }

@app.get("/api/auth/verify")
def auth_verify(x_api_key: str = Header(default="", alias="X-API-Key")):
    if API_KEY and x_api_key != API_KEY:
        raise HTTPException(401, "Unauthorized")
    return {"authenticated": True}

@app.post("/api/scan")
def scan_folder(body: dict = Body(...), _=Depends(check_auth)):
    path = Path(body.get("path", ""))
    if not path.exists():
        raise HTTPException(404, f"Directory not found: {path}")
    added = skipped = errors = 0
    for ext in ("*.pdf","*.epub","*.PDF","*.EPUB"):
        for fp in path.rglob(ext):
            try:
                filepath = str(fp.resolve())
                with get_db() as conn:
                    if conn.execute("SELECT id FROM books WHERE filepath=?",(filepath,)).fetchone():
                        skipped += 1; continue
                    conn.execute(
                        "INSERT INTO books (filename,filepath,file_type,file_size,status) "
                        "VALUES (?,?,?,?,'pending')",
                        (fp.name, filepath, fp.suffix.lower().lstrip("."), fp.stat().st_size)
                    )
                    conn.commit(); added += 1
            except Exception as e:
                print(f"[scan] {fp}: {e}"); errors += 1
    return {"scanned":added+skipped,"added":added,"skipped":skipped,"errors":errors}

@app.get("/api/books")
def list_books(
    search: str=Query(""), status: str=Query(""), category: str=Query(""),
    reading_status: str=Query(""), shelf_id: Optional[int]=Query(None),
    sort: str=Query("added_at"), order: str=Query("desc"),
    limit: int=Query(50), offset: int=Query(0), _=Depends(check_auth),
):
    limit = min(max(limit,1),500)
    order = "DESC" if order.lower()=="desc" else "ASC"
    sort  = sort if sort in {"added_at","title","author","year","category","confidence_score"} else "added_at"
    conds, params = [], []
    if search:
        conds.append("(title LIKE ? OR author LIKE ? OR description LIKE ? OR tags LIKE ? OR filename LIKE ?)")
        s=f"%{search}%"; params.extend([s,s,s,s,s])
    if status:         conds.append("status=?");         params.append(status)
    if category:       conds.append("category=?");       params.append(category)
    if reading_status: conds.append("reading_status=?"); params.append(reading_status)
    if shelf_id is not None:
        conds.append("id IN (SELECT book_id FROM book_shelves WHERE shelf_id=?)")
        params.append(shelf_id)
    where = ("WHERE "+" AND ".join(conds)) if conds else ""
    with get_db() as conn:
        total = conn.execute(f"SELECT COUNT(*) FROM books {where}",params).fetchone()[0]
        rows  = conn.execute(f"SELECT * FROM books {where} ORDER BY {sort} {order} LIMIT ? OFFSET ?",
                             params+[limit,offset]).fetchall()
    return {"total":total,"books":[serialize_book(r) for r in rows]}

@app.get("/api/books/{book_id}")
def get_book(book_id: int, _=Depends(check_auth)):
    with get_db() as conn:
        row = conn.execute("SELECT * FROM books WHERE id=?",(book_id,)).fetchone()
        if not row: raise HTTPException(404,"Book not found")
        b = serialize_book(row)
        b["shelves"] = [dict(r) for r in conn.execute(
            "SELECT s.id,s.name FROM shelves s JOIN book_shelves bs ON s.id=bs.shelf_id WHERE bs.book_id=?",
            (book_id,)).fetchall()]
    return b

@app.patch("/api/books/{book_id}")
def update_book(book_id: int, data: dict=Body(...), _=Depends(check_auth)):
    safe = {k:v for k,v in data.items() if k in PATCHABLE_FIELDS}
    if not safe: raise HTTPException(400,"No valid fields to update")
    if "tags" in safe and isinstance(safe["tags"],list):
        safe["tags"] = json.dumps(safe["tags"])
    sets = ", ".join(f"{k}=?" for k in safe)
    with get_db() as conn:
        conn.execute(f"UPDATE books SET {sets} WHERE id=?",list(safe.values())+[book_id])
        conn.commit()
    return get_book(book_id)

@app.patch("/api/books/{book_id}/reading-status")
def set_reading_status(book_id: int, data: dict=Body(...), _=Depends(check_auth)):
    status = data.get("status")
    if status not in ("to-read","reading","done",None):
        raise HTTPException(400,"Invalid reading status")
    with get_db() as conn:
        conn.execute("UPDATE books SET reading_status=? WHERE id=?",(status,book_id))
        conn.commit()
    return get_book(book_id)

@app.delete("/api/books/{book_id}")
def delete_book(book_id: int, delete_file: bool=Query(False), _=Depends(check_auth)):
    with get_db() as conn:
        row = conn.execute("SELECT filepath FROM books WHERE id=?",(book_id,)).fetchone()
        if not row: raise HTTPException(404)
        if delete_file:
            fp = Path(row["filepath"])
            try:
                if fp.exists() and fp.resolve().is_relative_to(BOOKS_ROOT): fp.unlink()
            except Exception as e:
                print(f"[delete] {e}")
        conn.execute("DELETE FROM books WHERE id=?",(book_id,))
        conn.commit()
    return {"ok":True}

@app.post("/api/books/{book_id}/reprocess")
def reprocess_book(book_id: int, _=Depends(check_auth)):
    with get_db() as conn:
        row = conn.execute("SELECT manual_fixed FROM books WHERE id=?",(book_id,)).fetchone()
        if not row: raise HTTPException(404)
        if row["manual_fixed"]: raise HTTPException(409,"Book is locked. Unfix first.")
        conn.execute("UPDATE books SET status='pending',error_msg=NULL WHERE id=?",(book_id,))
        conn.commit()
    return {"ok":True,"status":"queued"}

@app.post("/api/books/{book_id}/fix")
def fix_book(book_id: int, _=Depends(check_auth)):
    with get_db() as conn:
        conn.execute("UPDATE books SET manual_fixed=1 WHERE id=?",(book_id,)); conn.commit()
    return {"ok":True}

@app.post("/api/books/{book_id}/unfix")
def unfix_book(book_id: int, _=Depends(check_auth)):
    with get_db() as conn:
        conn.execute("UPDATE books SET manual_fixed=0 WHERE id=?",(book_id,)); conn.commit()
    return {"ok":True}

@app.post("/api/books/{book_id}/open")
def open_book(book_id: int, _=Depends(check_auth)):
    with get_db() as conn:
        row = conn.execute("SELECT filepath FROM books WHERE id=?",(book_id,)).fetchone()
        if not row: raise HTTPException(404,"Book not found")
    try:
        resolved = Path(row["filepath"]).resolve()
    except Exception:
        raise HTTPException(400,"Invalid file path")
    if not resolved.is_relative_to(BOOKS_ROOT):
        raise HTTPException(403,"File outside library root")
    if not resolved.exists():
        raise HTTPException(404,"File not found on disk")
    os.startfile(str(resolved))   # native Windows — simple and reliable
    return {"ok":True,"opened":str(resolved)}

@app.get("/api/books/{book_id}/cover")
def get_cover(book_id: int, _=Depends(check_auth)):
    with get_db() as conn:
        row = conn.execute("SELECT cover_path FROM books WHERE id=?",(book_id,)).fetchone()
    if not row or not row["cover_path"]: raise HTTPException(404,"No cover")
    path = Path(row["cover_path"])
    if not path.exists(): raise HTTPException(404,"Cover file missing")
    return FileResponse(str(path),media_type="image/jpeg")

@app.post("/api/books/{book_id}/cover/refresh")
async def refresh_cover(book_id: int, _=Depends(check_auth)):
    with get_db() as conn:
        row = conn.execute("SELECT filepath,file_type FROM books WHERE id=?",(book_id,)).fetchone()
        if not row: raise HTTPException(404)
    loop = asyncio.get_event_loop()
    cover_path = await loop.run_in_executor(None,extract_cover,row["filepath"],book_id,row["file_type"])
    with get_db() as conn:
        conn.execute("UPDATE books SET cover_path=? WHERE id=?",(cover_path,book_id)); conn.commit()
    return {"ok":True,"cover_path":cover_path}

@app.post("/api/books/{book_id}/enrich")
async def enrich_book(book_id: int, _=Depends(check_auth)):
    with get_db() as conn:
        row = conn.execute("SELECT * FROM books WHERE id=?",(book_id,)).fetchone()
        if not row: raise HTTPException(404)
    meta     = serialize_book(row)
    loop     = asyncio.get_event_loop()
    enriched = await loop.run_in_executor(None,enrich_open_library,meta)
    with get_db() as conn:
        conn.execute("""
            UPDATE books SET title=?,author=?,year=?,language=?,
            description=?,tags=?,ol_enriched=1 WHERE id=?
        """,(enriched.get("title"),enriched.get("author"),enriched.get("year"),
             enriched.get("language"),enriched.get("description"),
             json.dumps(enriched.get("tags",[])),book_id))
        conn.commit()
    return get_book(book_id)

@app.post("/api/books/{book_id}/chat")
async def chat_book(book_id: int, data: dict=Body(...), _=Depends(check_auth)):
    max_chars    = int(os.getenv("LLM_MAX_CHARS","3000"))
    ollama_url   = os.getenv("OLLAMA_URL",  "http://localhost:11434")
    ollama_model = os.getenv("OLLAMA_MODEL","mistral:latest")
    cache_file   = CACHE_DIR / f"{book_id}.txt"
    context      = (cache_file.read_text(encoding="utf-8") if cache_file.exists() else "")[:max_chars]
    payload = json.dumps({
        "model": ollama_model,
        "messages": [
            {"role":"system","content":
             f"You are a helpful assistant. Answer questions about this book based on "
             f"the following excerpt. Be concise.\n\nExcerpt:\n{context}"},
            {"role":"user","content":data.get("message","")},
        ],
        "stream":False,"options":{"temperature":0.3,"num_predict":500},
    }).encode()
    def _call():
        req = urllib.request.Request(f"{ollama_url}/api/chat",data=payload,
            headers={"Content-Type":"application/json"},method="POST")
        with urllib.request.urlopen(req,timeout=120) as resp:
            return json.loads(resp.read()).get("message",{}).get("content","")
    loop = asyncio.get_event_loop()
    try:
        reply = await loop.run_in_executor(None,_call)
        return {"reply":reply,"context_chars":len(context)}
    except Exception as e:
        raise HTTPException(503,f"Ollama error: {e}")

@app.get("/api/books/{book_id}/debug")
def get_debug(book_id: int, _=Depends(check_auth)):
    f = DEBUG_DIR / f"{book_id}.json"
    if not f.exists(): return {"available":False}
    return json.loads(f.read_text(encoding="utf-8"))

@app.post("/api/books/{book_id}/rename")
def rename_book(book_id: int, dry_run: bool=Query(False), _=Depends(check_auth)):
    with get_db() as conn:
        row = conn.execute("SELECT * FROM books WHERE id=?",(book_id,)).fetchone()
        if not row: raise HTTPException(404)
    title=row["title"]; author=row["author"]; year=row["year"]
    if not title or not author: return {"skipped":True,"reason":"missing fields"}
    def _clean(s):
        for c in r'\/:*?"<>|': s=s.replace(c,"")
        return s.strip()[:80]
    author_c=_clean(author or "Unknown"); title_c=_clean(title or "Unknown")
    ext=Path(row["filepath"]).suffix.lower()
    new_name=(f"{author_c} - {title_c} ({year}){ext}" if year else f"{author_c} - {title_c}{ext}")
    parent=Path(row["filepath"]).parent; target=parent/new_name
    if target.exists() and target.resolve()!=Path(row["filepath"]).resolve():
        stem=target.stem
        for i in range(1,100):
            candidate=parent/f"{stem} ({i}){ext}"
            if not candidate.exists(): target=candidate; new_name=candidate.name; break
        else: return {"skipped":True,"reason":"rename_collision"}
    if dry_run: return {"new_filename":new_name,"dry_run":True}
    if not target.resolve().is_relative_to(BOOKS_ROOT):
        raise HTTPException(403,"Target outside library root")
    Path(row["filepath"]).rename(target)
    with get_db() as conn:
        conn.execute("UPDATE books SET filepath=?,filename=? WHERE id=?",(str(target),new_name,book_id))
        conn.commit()
    return {"old_filename":row["filename"],"new_filename":new_name,"renamed":True}

@app.get("/api/duplicates")
def get_duplicates(_=Depends(check_auth)):
    with get_db() as conn:
        rows = conn.execute("SELECT id,filename,filepath,file_hash FROM books WHERE duplicate_of IS NOT NULL").fetchall()
    groups: dict = {}
    for r in rows:
        h=r["file_hash"] or "unknown"
        groups.setdefault(h,[]).append({"id":r["id"],"filename":r["filename"],"filepath":r["filepath"]})
    return [{"hash":h,"books":books} for h,books in groups.items()]

@app.post("/api/duplicates/dismiss")
def dismiss_duplicates(data: dict=Body(...), _=Depends(check_auth)):
    h=data.get("hash")
    if not h: raise HTTPException(400,"hash required")
    with get_db() as conn:
        conn.execute("UPDATE books SET dedup_dismissed=1 WHERE file_hash=?",(h,)); conn.commit()
    return {"ok":True}

@app.get("/api/shelves")
def list_shelves(_=Depends(check_auth)):
    with get_db() as conn:
        rows = conn.execute("""
            SELECT s.id,s.name,s.created_at,COUNT(bs.book_id) AS book_count
            FROM shelves s LEFT JOIN book_shelves bs ON s.id=bs.shelf_id
            GROUP BY s.id ORDER BY s.name
        """).fetchall()
    return [dict(r) for r in rows]

@app.post("/api/shelves")
def create_shelf(data: dict=Body(...), _=Depends(check_auth)):
    name=str(data.get("name","")).strip()
    if not name: raise HTTPException(400,"name required")
    with get_db() as conn:
        try:
            cur=conn.execute("INSERT INTO shelves (name) VALUES (?)",(name,)); conn.commit()
            return {"id":cur.lastrowid,"name":name}
        except sqlite3.IntegrityError:
            raise HTTPException(409,"Shelf already exists")

@app.delete("/api/shelves/{shelf_id}")
def delete_shelf(shelf_id: int, _=Depends(check_auth)):
    with get_db() as conn:
        conn.execute("DELETE FROM shelves WHERE id=?",(shelf_id,)); conn.commit()
    return {"ok":True}

@app.get("/api/shelves/{shelf_id}/books")
def shelf_books(shelf_id: int, _=Depends(check_auth)):
    with get_db() as conn:
        rows = conn.execute(
            "SELECT b.* FROM books b JOIN book_shelves bs ON b.id=bs.book_id WHERE bs.shelf_id=?",
            (shelf_id,)).fetchall()
    return [serialize_book(r) for r in rows]

@app.post("/api/shelves/{shelf_id}/books")
def add_to_shelf(shelf_id: int, data: dict=Body(...), _=Depends(check_auth)):
    book_id=data.get("book_id")
    if not book_id: raise HTTPException(400,"book_id required")
    with get_db() as conn:
        conn.execute("INSERT OR IGNORE INTO book_shelves (book_id,shelf_id) VALUES (?,?)",(book_id,shelf_id))
        conn.commit()
    return {"ok":True}

@app.delete("/api/shelves/{shelf_id}/books/{book_id}")
def remove_from_shelf(shelf_id: int, book_id: int, _=Depends(check_auth)):
    with get_db() as conn:
        conn.execute("DELETE FROM book_shelves WHERE shelf_id=? AND book_id=?",(shelf_id,book_id))
        conn.commit()
    return {"ok":True}

@app.get("/api/stats")
def get_stats(_=Depends(check_auth)):
    with get_db() as conn:
        return {
            "total": conn.execute("SELECT COUNT(*) FROM books").fetchone()[0],
            "by_status": dict(conn.execute("SELECT status,COUNT(*) FROM books GROUP BY status").fetchall()),
            "by_type": dict(conn.execute("SELECT file_type,COUNT(*) FROM books GROUP BY file_type").fetchall()),
            "by_category": [dict(r) for r in conn.execute(
                "SELECT category,COUNT(*) as count FROM books WHERE category IS NOT NULL "
                "GROUP BY category ORDER BY count DESC").fetchall()],
        }

@app.get("/api/analytics")
def get_analytics(_=Depends(check_auth)):
    with get_db() as conn:
        return {
            "books_per_category": [dict(r) for r in conn.execute(
                "SELECT category,COUNT(*) as count FROM books WHERE category IS NOT NULL "
                "GROUP BY category ORDER BY count DESC").fetchall()],
            "books_per_author": [dict(r) for r in conn.execute(
                "SELECT author,COUNT(*) as count FROM books WHERE author IS NOT NULL "
                "GROUP BY author ORDER BY count DESC LIMIT 20").fetchall()],
            "language_distribution": [dict(r) for r in conn.execute(
                "SELECT language,COUNT(*) as count FROM books WHERE language IS NOT NULL "
                "GROUP BY language ORDER BY count DESC").fetchall()],
            "reading_status_counts": dict(conn.execute(
                "SELECT reading_status,COUNT(*) FROM books GROUP BY reading_status").fetchall()),
            "confidence_histogram": [dict(r) for r in conn.execute("""
                SELECT CASE WHEN confidence_score IS NULL THEN 'unscored'
                    WHEN confidence_score<0.2 THEN '0.0-0.2' WHEN confidence_score<0.4 THEN '0.2-0.4'
                    WHEN confidence_score<0.6 THEN '0.4-0.6' WHEN confidence_score<0.8 THEN '0.6-0.8'
                    ELSE '0.8-1.0' END AS bucket, COUNT(*) AS count
                FROM books GROUP BY bucket ORDER BY bucket""").fetchall()],
            "books_added_per_month": [dict(r) for r in conn.execute("""
                SELECT strftime('%Y-%m',added_at) AS month,COUNT(*) AS count
                FROM books WHERE added_at>=date('now','-12 months')
                GROUP BY month ORDER BY month""").fetchall()],
            "total_books":   conn.execute("SELECT COUNT(*) FROM books").fetchone()[0],
            "total_shelves": conn.execute("SELECT COUNT(*) FROM shelves").fetchone()[0],
        }

@app.get("/api/categories")
def get_categories(_=Depends(check_auth)):
    with get_db() as conn:
        rows = conn.execute(
            "SELECT DISTINCT category FROM books WHERE category IS NOT NULL ORDER BY category"
        ).fetchall()
    return [r[0] for r in rows]

_EXPORT_COLS = ["id","filename","title","author","year","language","category","subcategory",
                "difficulty","description","tags","reading_status","confidence_score",
                "extraction_method","status","added_at"]

def _build_where(status,category,search):
    conds,params=[],[]
    if status:   conds.append("status=?");   params.append(status)
    if category: conds.append("category=?"); params.append(category)
    if search:
        conds.append("(title LIKE ? OR author LIKE ?)")
        s=f"%{search}%"; params.extend([s,s])
    return ("WHERE "+" AND ".join(conds)) if conds else "",params

@app.get("/api/export/csv")
def export_csv(status:str=Query(""),category:str=Query(""),search:str=Query(""),_=Depends(check_auth)):
    where,params=_build_where(status,category,search)
    def stream():
        yield "\ufeff"
        buf=io.StringIO(); w=csv.writer(buf); w.writerow(_EXPORT_COLS); yield buf.getvalue()
        with get_db() as conn:
            rows=conn.execute(f"SELECT * FROM books {where} ORDER BY added_at DESC",params).fetchall()
        for row in rows:
            b=serialize_book(row); buf=io.StringIO(); w=csv.writer(buf)
            w.writerow([json.dumps(b[c]) if isinstance(b.get(c),list) else (b.get(c) or "") for c in _EXPORT_COLS])
            yield buf.getvalue()
    return StreamingResponse(stream(),media_type="text/csv; charset=utf-8-sig",
        headers={"Content-Disposition":"attachment; filename=librarian_export.csv"})

@app.get("/api/export/json")
def export_json(status:str=Query(""),category:str=Query(""),search:str=Query(""),_=Depends(check_auth)):
    where,params=_build_where(status,category,search)
    def stream():
        yield "["
        with get_db() as conn:
            rows=conn.execute(f"SELECT * FROM books {where} ORDER BY added_at DESC",params).fetchall()
        for i,row in enumerate(rows):
            if i>0: yield ","
            yield json.dumps(serialize_book(row),ensure_ascii=False)
        yield "]"
    return StreamingResponse(stream(),media_type="application/json",
        headers={"Content-Disposition":"attachment; filename=librarian_export.json"})

if __name__ == "__main__":
    import uvicorn
    print("\n  LibrarianAI v3  →  http://localhost:8000\n")
    uvicorn.run("app:app",host="127.0.0.1",port=8000,reload=False)