# LibrarianAI Desktop UI - Development Guide

PyQt6 desktop interface for the Librarian.Desktop project.
Reuses existing backend code and shared database.

## Quick Start

### Prerequisites
- Python 3.10+
- PyQt6 (install via pip)
- Existing backend/ directory with extraction logic

### Setup

```bash
# Install dependencies
pip install PyQt6 PyQt6-sip

# Run the app
python ui/main.py
```

Expected: PyQt6 window launches, shows all books in a table.

## Project Structure
Librarian.Desktop/
├── backend/                    # Existing extraction logic
│   ├── extractor.py
│   ├── duplicates.py
│   ├── worker.py
│   ├── database.py
│   └── main.py
├── ui/                         # NEW: PyQt6 desktop interface
│   ├── init.py
│   ├── main.py                 # App entry point
│   ├── main_window.py          # QMainWindow with table
│   ├── book_detail_dialog.py   # Edit metadata
│   ├── category_manager.py     # Category hierarchy
│   ├── shelves_panel.py        # Shelves management
│   ├── status_view.py          # Processing progress
│   └── utils.py                # Helpers
├── librarian.db                # Shared database (1015 books)
├── Books/                      # Shared PDF/EPUB files
├── MASTER_PROMPT_PYQT_DESKTOP.md
├── DESKTOP_UI_GUIDE.md         # This file
└── requirements.txt

## Key Principle: Reuse Existing Code

**Don't duplicate** extraction, deduplication, or database logic.

Instead:
```python
# GOOD: Use backend functions
from backend.extractor import extract_metadata_from_file
metadata = extract_metadata_from_file(filepath)

# BAD: Rewrite logic in UI
import PyPDF2
pdf = PyPDF2.open(filepath)
metadata = {...}  # Don't do this
```

## Architecture
┌────────────────────────────────────┐
│  PyQt6 UI (ui/)                    │ ← User sees this
│  - MainWindow (table, search)      │
│  - BookDetailDialog (edit)         │
│  - CategoryManager (hierarchy)     │
│  - ShelvesPanel (organization)     │
└───────────┬────────────────────────┘
│ Calls functions
┌───────────▼────────────────────────┐
│  Existing Backend (backend/)       │ ← Business logic
│  - Extraction (PyMuPDF)            │
│  - Deduplication (fuzzy matching)  │
│  - Database queries                │
│  - Task queue/worker               │
└───────────┬────────────────────────┘
│ Reads/writes
┌───────────▼────────────────────────┐
│  SQLite Database (librarian.db)    │ ← Persistent storage
│  - 1015 books, no metadata yet     │
│  - Categories (empty)              │
│  - Shelves (empty)                 │
└────────────────────────────────────┘

