import os
import sqlite3
from pathlib import Path


def _add_column(conn, table: str, column: str, definition: str):
    """Add column only if absent — SQLite has no ADD COLUMN IF NOT EXISTS."""
    existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def run_migrations(db_path: str):
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS books (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            filename         TEXT NOT NULL,
            filepath         TEXT NOT NULL,
            status           TEXT NOT NULL DEFAULT 'processing',
            created_at       TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at       TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)

    cols = [
        ("title",             "TEXT"),
        ("author",            "TEXT"),
        ("year",              "INTEGER"),
        ("language",          "TEXT"),
        ("category",          "TEXT"),
        ("subcategory",       "TEXT"),
        ("difficulty",        "TEXT"),
        ("description",       "TEXT"),
        ("tags",              "TEXT"),
        ("error_msg",         "TEXT"),
        ("manual_fixed",      "INTEGER NOT NULL DEFAULT 0"),
        ("extraction_method", "TEXT"),
        ("confidence_score",  "REAL"),
        ("cover_path",        "TEXT"),
        ("cover_source",      "TEXT"),
        ("file_hash",         "TEXT"),
        ("duplicate_of",      "INTEGER REFERENCES books(id)"),
        ("reading_status",    "TEXT"),
        ("ol_enriched",       "INTEGER NOT NULL DEFAULT 0"),
        ("dedup_dismissed",   "INTEGER NOT NULL DEFAULT 0"),
        ("added_at",          "TEXT DEFAULT (datetime('now'))"),
        ("processed_at",      "TEXT"),
        ("file_type",         "TEXT"),
        ("file_size",         "INTEGER"),
        ("publisher",         "TEXT"),
        ("isbn",              "TEXT"),
        ("notes",             "TEXT"),
        ("rating",            "INTEGER DEFAULT 0"),
        ("cover_source",      "TEXT"),
    ]
    for col, defn in cols:
        _add_column(conn, "books", col, defn)

    conn.execute("CREATE INDEX IF NOT EXISTS idx_books_file_hash ON books(file_hash)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_books_status    ON books(status)")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS shelves (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
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
    conn.execute("""
        CREATE TABLE IF NOT EXISTS categories (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            name      TEXT NOT NULL UNIQUE,
            parent_id INTEGER REFERENCES categories(id) ON DELETE SET NULL
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_categories_parent ON categories(parent_id)")

    conn.commit()
    conn.close()
    print(f"Migrations complete: {db_path}")


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    run_migrations(os.getenv("DB_PATH", "./data/librarian.db"))
