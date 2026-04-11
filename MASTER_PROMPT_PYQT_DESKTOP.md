# LibrarianAI Desktop UI - PyQt6 Master Prompt

You are building a PyQt6 desktop interface for the existing Librarian.Desktop project.
Reuse existing backend logic and database. Work alongside web UI in other branch.

## PROJECT CONTEXT

**Repository**: Librarian.Desktop  
**Branch**: feature/desktop-ui  
**Existing Code**: backend/ (extraction, deduplication, worker logic)  
**Database**: librarian.db (shared with web UI)  
**Tech Stack**: PyQt6, Python 3.10+, SQLite3

## EXISTING DATABASE SCHEMA

```sql
CREATE TABLE books (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    filename         TEXT NOT NULL,
    filepath         TEXT NOT NULL,
    status           TEXT NOT NULL DEFAULT 'processing',
    created_at       TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at       TEXT NOT NULL DEFAULT (datetime('now')),
    title            TEXT,
    author           TEXT,
    year             INTEGER,
    language         TEXT,
    category         TEXT,
    subcategory      TEXT,
    difficulty       TEXT,
    description      TEXT,
    tags             TEXT,
    error_msg        TEXT,
    manual_fixed     INTEGER NOT NULL DEFAULT 0,
    extraction_method TEXT,
    confidence_score REAL,
    cover_path       TEXT,
    cover_source     TEXT,
    file_hash        TEXT,
    duplicate_of     INTEGER REFERENCES books(id),
    reading_status   TEXT,
    ol_enriched      INTEGER NOT NULL DEFAULT 0,
    dedup_dismissed  INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE categories (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    name      TEXT NOT NULL UNIQUE,
    parent_id INTEGER REFERENCES categories(id) ON DELETE SET NULL
);

CREATE TABLE shelves (
    id   INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE book_shelves (
    book_id  INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    shelf_id INTEGER NOT NULL REFERENCES shelves(id) ON DELETE CASCADE,
    added_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (book_id, shelf_id)
);

CREATE INDEX idx_books_file_hash ON books(file_hash);
CREATE INDEX idx_books_status    ON books(status);
CREATE INDEX idx_categories_parent ON categories(parent_id);
```

**Current State**: 1015 books, all status='processing', no metadata or categories assigned yet.

## REUSING EXISTING BACKEND CODE

The `backend/` directory contains production code you already have:
- `backend/extractor.py` — PyMuPDF metadata extraction
- `backend/duplicates.py` — Deduplication detection
- `backend/worker.py` — Task queue for processing
- `backend/database.py` — Database access functions

**The PyQt UI should**:
1. Import and use these modules directly
2. Share the same database connection (no duplication)
3. Call existing extraction functions programmatically
4. Reuse database query functions

**Example**:
```python
from backend.extractor import extract_metadata
from backend.duplicates import detect_duplicates
from backend.database import get_connection

# In UI code:
db = get_connection()
books = db.query("SELECT * FROM books LIMIT 100")

# When user clicks "Extract Metadata" button:
for book in selected_books:
    metadata = extract_metadata(book.filepath)
    db.update_book(book.id, metadata)
```

## PROJECT STRUCTURE
ui/
├── init.py
├── main.py                      # Entry point (launches PyQt app)
├── main_window.py               # QMainWindow with table
├── book_detail_dialog.py        # Edit metadata modal
├── category_manager.py          # Manage category hierarchy
├── shelves_panel.py             # Create/manage shelves
├── status_view.py               # Show extraction progress
└── utils.py                     # UI helpers (path handling, etc.)

## KEY REQUIREMENTS

**Performance** (for 1015 books):
- Load all books on startup: <1 second
- Display table: <500ms
- Search/filter: <100ms
- Edit and save: <50ms
- All operations non-blocking

**Architecture**:
- UI layer (`ui/`) handles only display and user interaction
- Business logic (`backend/`) handles extraction, deduplication, queries
- Database access is centralized (no duplicate query code)
- All long operations (extraction, LLM) run in background threads (QThread or ThreadPoolExecutor)
- UI never blocks on I/O or processing

**Code Standards**:
- Modern Python 3.10+ (type hints, f-strings, walrus operator)
- Reuse existing backend functions (don't duplicate code)
- Error handling: try-catch with user-friendly messages
- Logging: use Python's logging module (same as backend)
- Comments explaining non-obvious UI logic

## NAMING CONVENTIONS

- Classes: `PascalCase` (MainWindow, BookDetailDialog, CategoryManager)
- Functions: `snake_case` (load_books, save_metadata, refresh_table)
- Methods with UI side effects: `on_<action>` (on_book_selected, on_save_clicked)
- Properties: `snake_case` (self.selected_book, self.search_text)
- Private: `_snake_case` (self._database, self._worker_thread)
- Constants: `UPPER_CASE` (MAX_BOOKS_PER_PAGE, WINDOW_WIDTH)
- Signals (PyQt): `camelCase` (bookSelected, statusChanged)

## INITIAL WORKFLOW WITH CLAUDE CODE

You will request modules one at a time in VS Code's Claude Code panel:
"I have an existing Librarian.Desktop project with backend/ containing
extraction and database code. I'm building a PyQt6 UI in a new ui/ directory.
Write ui/main.py that:

Imports necessary PyQt6 modules
Creates a QApplication
Instantiates MainWindow
Launches the app
Handles exceptions gracefully

Keep it simple, just the entry point."

Each request should:
1. Specify the file (ui/main.py, ui/main_window.py, etc.)
2. Describe what it does
3. Mention reusing backend code (if applicable)
4. Ask for production-ready output

## FEATURE BREAKDOWN

### **Phase 1: Core UI (Week 1)**
1. **main.py** — Application entry point
2. **main_window.py** — QMainWindow with table showing all books
3. **book_detail_dialog.py** — Edit book metadata
4. **Database layer** — Reuse backend database functions or write thin wrapper

### **Phase 2: Navigation & Search (Week 1)**
1. Search bar (filter by title, author)
2. Status filter (show processing/extracted/complete)
3. Category filter (show dropdown when categories exist)
4. Shelf selector (multi-select books into shelves)

### **Phase 3: Category Management (Week 2)**
1. **category_manager.py** — Dialog to create/edit categories
2. Hierarchical category browser
3. Assign books to categories
4. Populate categories table

### **Phase 4: Shelves & Organization (Week 2)**
1. **shelves_panel.py** — Create and manage custom shelves
2. Drag/drop books into shelves
3. View books by shelf

### **Phase 5: Processing Control (Week 2)**
1. **status_view.py** — Show extraction progress
2. Trigger backend extraction for selected books
3. Monitor task queue
4. Show errors and completion status

### **Phase 6: Polish (Optional)**
1. Window state persistence (remember size, position)
2. Keyboard shortcuts
3. Context menus (right-click actions)
4. Progress bars for long operations
5. Dark mode toggle

## INTEGRATION WITH EXISTING BACKEND

**Do this**:
```python
# In ui/main_window.py
from backend.extractor import extract_metadata_from_file
from backend.duplicates import check_duplicates
from backend.database import get_database_connection

# Reuse backend functions directly
def on_extract_selected_books(self):
    for book_id in self.selected_book_ids:
        book = self.db.get_book(book_id)
        metadata = extract_metadata_from_file(book.filepath)  # Backend function
        self.db.update_book(book_id, metadata)
        self.refresh_table()
```

**Don't do this**:
```python
# Don't duplicate extraction logic in UI
def on_extract_selected_books(self):
    for book_id in self.selected_book_ids:
        # DON'T write extraction code here, use backend function
        pdf = PyPDF2.open(filepath)
        metadata = {title: pdf.metadata.title, ...}
```

## THREADING PATTERN

For long operations, use QThread or ThreadPoolExecutor:

```python
# Example: Extract metadata in background, update UI when done
from concurrent.futures import ThreadPoolExecutor

executor = ThreadPoolExecutor(max_workers=4)

def on_extract_clicked(self):
    def extract_all():
        for book in selected_books:
            metadata = extract_metadata_from_file(book.filepath)  # Backend
            self.db.update_book(book.id, metadata)
    
    # Run in background thread
    future = executor.submit(extract_all)
    future.add_done_callback(self.on_extraction_complete)

def on_extraction_complete(self, future):
    # This runs on UI thread
    self.status_bar.showMessage("Extraction complete!")
    self.refresh_table()
```

**Never block the UI thread. Always use threading for I/O and processing.**

## PERFORMANCE TARGETS

All measurements on typical hardware (modern CPU, SSD):

| Operation | Target | Max Allowed |
|-----------|--------|-------------|
| Load 1015 books on startup | <1 second | 2 seconds |
| Display in table | <500ms | 1 second |
| Search/filter (typing) | <100ms | 200ms |
| Edit and save book | <50ms | 100ms |
| Extract single book (from file) | <2 seconds | 5 seconds |
| Scroll through 1015 books | Smooth (60 FPS) | No stuttering |

If slow, profile with cProfile and report to Claude Code.

## DATABASE ACCESS PATTERN

**Wrap database access** (don't call sqlite3 directly everywhere):

```python
# ui/database.py (thin wrapper)
from backend.database import get_connection

class UIDatabase:
    def __init__(self, db_path='librarian.db'):
        self.db = get_connection(db_path)
    
    def get_all_books(self, limit=None, offset=0):
        """Fetch all books with optional pagination"""
        query = "SELECT * FROM books ORDER BY title"
        if limit:
            query += f" LIMIT {limit} OFFSET {offset}"
        return self.db.query(query)
    
    def search_books(self, query_text):
        """Search by title or author"""
        return self.db.query(
            "SELECT * FROM books WHERE title LIKE ? OR author LIKE ? ORDER BY title",
            (f"%{query_text}%", f"%{query_text}%")
        )
    
    def update_book(self, book_id, fields):
        """Update book metadata"""
        return self.db.update(f"UPDATE books SET ... WHERE id = ?", fields, book_id)
    
    def get_categories(self, parent_id=None):
        """Get hierarchical categories"""
        if parent_id is None:
            return self.db.query("SELECT * FROM categories WHERE parent_id IS NULL ORDER BY name")
        return self.db.query("SELECT * FROM categories WHERE parent_id = ? ORDER BY name", (parent_id,))
```

Then use this consistently in UI:

```python
# In main_window.py
from ui.database import UIDatabase

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.db = UIDatabase('librarian.db')
        self.load_books()
    
    def load_books(self):
        self.books = self.db.get_all_books()
        self.display_in_table(self.books)
```

## EXAMPLE REQUESTS TO CLAUDE CODE

**Request 1**:
Write ui/main.py that:

Imports PyQt6
Creates QApplication
Instantiates MainWindow (from main_window.py)
Launches the app
Handles exceptions gracefully

Keep it simple, just the entry point. Make it production-ready.

**Request 2**:
Write ui/main_window.py that:

Extends QMainWindow
Has QTableWidget showing all books from librarian.db
Columns: filename, title, author, year, category, status
Load books on startup (in background thread, don't block)
Make columns sortable by clicking headers
Double-click row to open BookDetailDialog
Add QLineEdit search bar (filters table in real-time)
Add status bar showing total book count and selected count

Use PyQt6, connect to librarian.db with sqlite3.
Reuse database functions from backend/ if available.
Make it production-ready.

**Request 3**:
Write ui/book_detail_dialog.py that:

Extends QDialog
Shows form with fields: filename (read-only), title, author, year, language, category, subcategory, difficulty, description, tags
Buttons: Save (updates DB), Cancel, Delete (with confirmation)
Validate input before saving
Show error messages if save fails

Bind to a Book object passed in constructor.
Make it production-ready.

And so on through all modules.

## SUCCESS CRITERIA

When complete, you should have:
- PyQt6 desktop app launching in <2 seconds
- All 1015 books loading in <1 second
- Responsive table with sorting, searching
- Ability to edit book metadata and save to DB
- Category management UI
- Shelves UI for organizing books
- Progress indication for extraction tasks
- Professional appearance and error handling
- Works alongside web UI (separate branch)

## GIT WORKFLOW

```bash
# On feature/desktop-ui branch
git add ui/main.py
git commit -m "feat: add PyQt6 app entry point"

git add ui/main_window.py
git commit -m "feat: add main window with book table"

git add ui/book_detail_dialog.py
git commit -m "feat: add book metadata editor"

# When ready to merge back to main:
git push origin feature/desktop-ui
# Create pull request
```

## RESOURCES

- [PyQt6 Documentation](https://www.riverbankcomputing.com/static/Docs/PyQt6/)
- [PyQt6 Signals & Slots](https://doc.qt.io/qt-6/signals-and-slots.html)
- [Threading in PyQt](https://doc.qt.io/qt-6/qthread.html)
- [SQLite3 in Python](https://docs.python.org/3/library/sqlite3.html)

---

## VERSION
v1 - 2026-04-08 - PyQt6 desktop UI for existing Librarian.Desktop project