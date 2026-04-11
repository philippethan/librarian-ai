"""
ui/scan/scan_service.py - Core scan logic for the LibrarianAI desktop UI.

Discovers new PDF/EPUB files in the Books directory, deduplicates them against
the existing library, and inserts new records into the database as 'pending'
for the backend processor to handle Ollama extraction.

Reuses backend logic:
  - backend.app._parse_filename  for heuristic title/author from filename
  - backend.db.get_conn          for per-thread SQLite connection
"""

import hashlib
import logging
from pathlib import Path
from threading import Event
from typing import Callable

from backend.db import get_conn
from backend.app import _parse_filename

log = logging.getLogger(__name__)

SUPPORTED_EXTENSIONS = frozenset({".pdf", ".epub"})


class ScanResults:
    """Accumulated counters and error list for one scan run."""

    __slots__ = (
        "total_files",
        "already_indexed",
        "new_files",
        "inserted",
        "duplicates",
        "errors",
        "cancelled",
    )

    def __init__(self) -> None:
        self.total_files: int = 0
        self.already_indexed: int = 0
        self.new_files: int = 0
        self.inserted: int = 0
        self.duplicates: int = 0
        self.errors: list[tuple[str, str]] = []
        self.cancelled: bool = False

    def as_dict(self) -> dict:
        return {
            "total_files": self.total_files,
            "already_indexed": self.already_indexed,
            "new_files": self.new_files,
            "inserted": self.inserted,
            "duplicates": self.duplicates,
            "errors": self.errors,
            "cancelled": self.cancelled,
        }


ProgressCallback = Callable[[int, int, str], None]


class ScanService:
    """
    Drives the four-phase scan pipeline:
      1. Discovery  - find all PDF/EPUB files in books_path
      2. Filter     - exclude files already in DB (by filepath or file_hash)
      3. Hash       - compute SHA-256 per new file; detect duplicates
      4. Insert     - add new records as status='pending'

    All heavy work runs synchronously; call run_scan() from a background
    thread (e.g. QThread) to keep the UI responsive.

    Thread-safety: cancel() sets an Event that the scan loop checks at each
    file boundary, so cancellation is prompt and leaves the DB consistent.
    """

    def __init__(self, db_path: str, books_path: str) -> None:
        self._db_path = db_path
        self._books_path = Path(books_path)
        self._cancel = Event()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def cancel(self) -> None:
        """Signal the running scan to stop after the current file."""
        self._cancel.set()

    def run_scan(self, progress_callback: ProgressCallback | None = None) -> dict:
        """
        Execute the full scan pipeline.

        Calls progress_callback(current, total, message) from the calling
        thread — wire this to a Qt signal for UI updates.

        Returns a dict (see ScanResults.as_dict) regardless of errors.
        """
        results = ScanResults()

        # ---- Phase 1: Discovery ------------------------------------------
        self._emit(progress_callback, 0, 0, "Scanning directory...")

        all_files = self._get_all_files()
        results.total_files = len(all_files)

        if not all_files:
            self._emit(progress_callback, 0, 0, "No PDF or EPUB files found.")
            return results.as_dict()

        # ---- Phase 2: Filter already-indexed -----------------------------
        self._emit(progress_callback, 0, 0, "Checking database...")

        conn = get_conn(self._db_path)

        indexed_paths: set[str] = {
            row["filepath"]
            for row in conn.execute("SELECT filepath FROM books").fetchall()
            if row["filepath"]
        }
        indexed_hashes: set[str] = {
            row["file_hash"]
            for row in conn.execute(
                "SELECT file_hash FROM books WHERE file_hash IS NOT NULL"
            ).fetchall()
        }

        new_files = [
            f for f in all_files
            if self._normalise_path(f) not in indexed_paths
        ]

        results.already_indexed = results.total_files - len(new_files)
        results.new_files = len(new_files)

        if not new_files:
            self._emit(progress_callback, 0, 0, "No new books found.")
            return results.as_dict()

        # ---- Phase 3 + 4: Hash, dedup, insert ---------------------------
        total = len(new_files)

        for idx, fpath in enumerate(new_files):
            if self._cancel.is_set():
                results.cancelled = True
                break

            filename = fpath.name
            filepath_str = self._normalise_path(fpath)
            file_type = fpath.suffix.lower().lstrip(".")

            self._emit(
                progress_callback,
                idx + 1,
                total,
                f"Processing: {filename}",
            )

            try:
                file_size = fpath.stat().st_size
                file_hash = self._compute_hash(fpath)

                if file_hash in indexed_hashes:
                    # Duplicate of an existing library entry (same content, different name/path).
                    dup_row = conn.execute(
                        "SELECT id FROM books WHERE file_hash=? AND dedup_dismissed=0 LIMIT 1",
                        (file_hash,),
                    ).fetchone()
                    dup_id = dup_row["id"] if dup_row else None

                    conn.execute(
                        """
                        INSERT INTO books
                            (filename, filepath, file_type, file_size, file_hash, status, duplicate_of)
                        VALUES (?, ?, ?, ?, ?, 'duplicate', ?)
                        """,
                        (filename, filepath_str, file_type, file_size, file_hash, dup_id),
                    )
                    conn.commit()
                    results.duplicates += 1
                    log.debug("Duplicate detected: %s (dup_of=%s)", filename, dup_id)
                    continue

                # Heuristic title/author from filename — fills the table immediately
                # so the user sees something useful before Ollama processes the book.
                hint_title, hint_author = _parse_filename(filepath_str)

                cur = conn.execute(
                    """
                    INSERT INTO books
                        (filename, filepath, file_type, file_size, file_hash, status, title, author)
                    VALUES (?, ?, ?, ?, ?, 'pending', ?, ?)
                    """,
                    (
                        filename,
                        filepath_str,
                        file_type,
                        file_size,
                        file_hash,
                        hint_title or None,
                        hint_author or None,
                    ),
                )
                new_id = cur.lastrowid
                conn.commit()

                # Extract cover image from first page (PDF page 1 / EPUB cover)
                try:
                    from backend.app import extract_cover  # noqa: PLC0415
                    cover_path = extract_cover(filepath_str, new_id, file_type)
                    if cover_path:
                        conn.execute(
                            "UPDATE books SET cover_path=? WHERE id=?",
                            (cover_path, new_id),
                        )
                        conn.commit()
                        log.debug("Cover extracted: id=%d", new_id)
                except Exception as ce:  # noqa: BLE001
                    log.debug("Cover extraction skipped for %s: %s", filename, ce)

                results.inserted += 1
                indexed_hashes.add(file_hash)
                indexed_paths.add(filepath_str)
                log.debug("Inserted: %s (id=%d)", filename, new_id)

            except Exception as exc:  # noqa: BLE001
                log.warning("Failed to process %s: %s", filename, exc)
                results.errors.append((filename, str(exc)))

        return results.as_dict()

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _get_all_files(self) -> list[Path]:
        if not self._books_path.exists():
            log.warning("Books path does not exist: %s", self._books_path)
            return []
        return [
            f
            for f in self._books_path.rglob("*")
            if f.is_file() and f.suffix.lower() in SUPPORTED_EXTENSIONS
        ]

    @staticmethod
    def _compute_hash(fpath: Path) -> str:
        sha256 = hashlib.sha256()
        with fpath.open("rb") as fh:
            for chunk in iter(lambda: fh.read(65_536), b""):
                sha256.update(chunk)
        return sha256.hexdigest()

    @staticmethod
    def _normalise_path(fpath: Path) -> str:
        return str(fpath).replace("\\", "/")

    @staticmethod
    def _emit(
        cb: ProgressCallback | None,
        current: int,
        total: int,
        message: str,
    ) -> None:
        if cb is not None:
            try:
                cb(current, total, message)
            except Exception:  # noqa: BLE001
                pass
