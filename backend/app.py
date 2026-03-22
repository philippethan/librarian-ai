import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from backend.auth import require_auth
from backend.db import get_conn

DB_PATH = os.getenv("DB_PATH", "./data/librarian.db")

_executor = ThreadPoolExecutor(max_workers=int(os.getenv("SCAN_WORKERS", "4")))

app = FastAPI(title="LibrarianAI v3")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
async def health():
    return {"status": "ok"}


@app.get("/api/auth/verify")
async def auth_verify(_=Depends(require_auth)):
    return {"authenticated": True}


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

        # Insert with status=processing (or skip if already present by filepath)
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
