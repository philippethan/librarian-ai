# LibrarianAI Desktop UI - Scan Feature Guidelines

Development guide for implementing the Scan feature that discovers and extracts metadata from new books.

## OVERVIEW

The Scan feature allows users to click a button and automatically:
1. Discover PDF/EPUB files in Books/ directory not yet in database
2. Extract metadata from each using existing backend code
3. Detect duplicates
4. Add new books to database
5. Report progress and results

This is a **non-blocking background operation** that keeps the UI responsive.

---

## KEY PRINCIPLES

### 1. Reuse Backend Code

**Your app.py or main extraction script already has:**
- Directory scanning logic
- File discovery (find PDFs/EPUBs)
- Metadata extraction (PyMuPDF, EPUB parsing)
- Duplicate detection algorithms
- File hashing (SHA256)

**DO THIS:**
```python
# In scan_service.py
from backend.extractor import extract_metadata_from_file
from backend.duplicates import check_duplicates

metadata = extract_metadata_from_file(filepath)
duplicates = check_duplicates(metadata, existing_library)
```

**DON'T DO THIS:**
```python
# Don't rewrite extraction logic in the UI
import PyPDF2
pdf = PyPDF2.open(filepath)
title = pdf.metadata.title  # ← DON'T DO THIS
```

### 2. Non-Blocking UI

All scanning, extraction, and database operations must run on a **background thread**, never on the main UI thread.

```python
# CORRECT: Run in background thread
def on_scan_clicked(self):
    thread = Thread(target=self.run_scan)
    thread.daemon = True
    thread.start()

def run_scan(self):
    # Heavy operations here (extraction, DB inserts)
    # UI stays responsive
```

```python
# WRONG: Blocks entire UI until complete
def on_scan_clicked(self):
    for file in files:
        metadata = extract(file)  # ← Freezes UI for seconds
        db.insert(metadata)
```

### 3. Progress Reporting

The scan service should report progress to the UI via a callback function.

```python
def scan_and_extract_all(self, progress_callback=None):
    for idx, file in enumerate(files):
        # Do work
        if progress_callback:
            progress_callback(
                current=idx + 1,
                total=len(files),
                message=f"Extracting: {file}"
            )
```

The dialog updates the UI based on these callbacks:

```python
def update_progress(self, current, total, message):
    percentage = (current / total) * 100
    self.progress_bar.setValue(int(percentage))
    self.status_label.setText(message)
```

### 4. Error Resilience

If one file fails, the scan should **continue with the next file**, not stop.

```python
for file in files:
    try:
        metadata = extract_metadata_from_file(file)
        # Process
    except Exception as e:
        # Log error but continue
        errors.append((file, str(e)))
        continue  # ← Don't stop, process next file
```

---

## PROJECT STRUCTURE

### New Files

```
ui/
├── scan/                          # NEW directory
│   ├── __init__.py
│   ├── scan_service.py            # Core scan logic (reuses backend)
│   └── scan_dialog.py             # UI for progress and results
└── main_window.py                 # (MODIFIED: add scan button)
```

### Minimal Addition to main_window.py

```python
# Just add:
def on_scan_clicked(self):
    dialog = ScanProgressDialog(self.db, self)
    if dialog.exec():
        self.refresh_table()

# And add button to toolbar
scan_btn = QPushButton("Scan")
scan_btn.clicked.connect(self.on_scan_clicked)
toolbar.addWidget(scan_btn)
```

---

## HOW SCANNING WORKS

### Phase 1: Discovery
```
Books/ directory
├── file1.pdf        ← In DB
├── file2.pdf        ← In DB
├── file3.pdf        ← NOT in DB ← Find these
├── file4.epub       ← In DB
└── file5.epub       ← NOT in DB ← Find these

Result: [file3.pdf, file5.epub]
```

Implementation:
```python
def find_unindexed_files(self):
    all_files = self._get_all_files()      # All PDFs/EPUBs
    indexed_files = self._get_indexed_files()  # In DB
    unindexed = [f for f in all_files if f not in indexed_files]
    return unindexed
```

### Phase 2: Extraction
```
For each unindexed file:
  1. Call backend.extract_metadata_from_file(filepath)
  2. Get: title, author, year, language, description, etc.
  3. Compute file hash (SHA256)
  4. Store results
```

Example:
```python
metadata = extract_metadata_from_file('Books/document.pdf')
# Returns: {
#   'title': 'Machine Learning',
#   'author': 'Andrew Ng',
#   'year': 2023,
#   'language': 'en',
#   'description': '...',
#   ...
# }
```

### Phase 3: Deduplication
```
For each extracted book:
  1. Check if matches existing books (title, author, ISBN, hash)
  2. If match found: mark as duplicate, don't insert
  3. If no match: ready for insertion
```

### Phase 4: Database Insertion
```
For each non-duplicate book:
  1. Create book record
  2. INSERT into books table
  3. Mark status='extracted' (successful)
  4. If error during extraction: mark status='error'
```

### Phase 5: Report
```
Display results:
- Total files found: 1200
- Already in DB: 1150
- New files: 50
- Successfully extracted: 48
- Extraction errors: 2
- Duplicates: 1
```

---

## ADAPTING FROM app.py

### What to Look For in app.py

Your existing app.py probably has these components:

#### 1. Directory Scanning
```python
# Look for something like:
def scan_directory(path):
    for root, dirs, files in os.walk(path):
        for file in files:
            if file.endswith(('.pdf', '.epub')):
                yield file
```

**Adapt for UI:**
```python
# In ScanService.find_unindexed_files()
def _get_all_files(self):
    all_files = []
    for root, dirs, files in os.walk(self.books_dir):
        for file in files:
            if file.lower().endswith(('.pdf', '.epub')):
                full_path = os.path.join(root, file)
                relative_path = os.path.relpath(full_path, os.getcwd())
                all_files.append(relative_path)
    return all_files
```

#### 2. Metadata Extraction
```python
# In app.py:
def extract_book_metadata(filepath):
    # Uses PyMuPDF, EPUB parsing, etc.
    return metadata
```

**Use directly:**
```python
# In scan_service.py
from backend.extractor import extract_metadata_from_file

metadata = extract_metadata_from_file(filepath)
```

#### 3. Duplicate Detection
```python
# In app.py:
def find_duplicates(metadata, library):
    # Checks title, ISBN, hash, etc.
    return duplicates
```

**Use directly:**
```python
# In scan_service.py
from backend.duplicates import check_duplicates

duplicates = check_duplicates(metadata, file_hash, existing_books)
```

#### 4. Main Processing Loop
```python
# In app.py:
def process_all():
    unindexed = find_new_files()
    for file in unindexed:
        try:
            metadata = extract(file)
            if not is_duplicate(metadata):
                db.insert(metadata)
        except Exception as e:
            log_error(file, e)
```

**Adapt for UI with progress:**
```python
# In scan_service.py
def scan_and_extract_all(self, progress_callback=None):
    unindexed = self.find_unindexed_files()
    
    for idx, file in enumerate(unindexed):
        try:
            metadata = extract_metadata_from_file(file)
            if not self.is_duplicate(metadata):
                self.db.insert(metadata)
            
            # Report progress
            if progress_callback:
                progress_callback(
                    current=idx+1,
                    total=len(unindexed),
                    message=f"Processing: {file}"
                )
        except Exception as e:
            self.results['errors'].append((file, str(e)))
```

---

## THREADING IMPLEMENTATION

### Use Python's `threading.Thread`

```python
# In ScanProgressDialog.start_scan()

from threading import Thread

def start_scan(self):
    # Create background thread
    self.scan_thread = Thread(target=self._scan_worker)
    self.scan_thread.daemon = True  # Dies when main app exits
    self.scan_thread.start()  # Start immediately

def _scan_worker(self):
    # Runs in background, doesn't block UI
    results = self.scan_service.scan_and_extract_all(
        progress_callback=self.update_progress
    )
```

### Thread-Safe Progress Updates

Progress callbacks are called from the **background thread**, but UI updates must happen on the **main thread**.

```python
def update_progress(self, current, total, message):
    # This is called from background thread
    # UI updates are generally thread-safe in PyQt if you're just updating labels/progress bars
    
    # For safety, you could use signals:
    # self.progress_signal.emit(current, total, message)
    
    # But for simple cases, direct updates work:
    self.progress_bar.setValue(int((current/total)*100))
    self.status_label.setText(message)
```

---

## DATABASE OPERATIONS

### Use Existing Database Layer

```python
# Do this (reuse existing DB wrapper)
from ui.database import UIDatabase

class ScanService:
    def __init__(self, db):
        self.db = db  # UIDatabase instance
    
    def insert_new_books(self, books):
        for book in books:
            self.db.execute(
                "INSERT INTO books (...) VALUES (...)",
                (book['filename'], book['filepath'], ...)
            )
```

### Batch Inserts for Performance

If inserting many books, use transactions:

```python
def insert_new_books(self, books):
    try:
        # Start transaction
        self.db.execute("BEGIN TRANSACTION")
        
        for book in books:
            self.db.execute(
                "INSERT INTO books (...) VALUES (...)",
                (...)
            )
        
        # Commit all at once
        self.db.execute("COMMIT")
    except Exception as e:
        self.db.execute("ROLLBACK")
        raise
```

---

## ERROR HANDLING

### Expected Errors and Handling

| Error | Cause | Handling |
|-------|-------|----------|
| Corrupted PDF | File damaged | Skip file, log, continue |
| Permission denied | File locked | Skip file, log, continue |
| Unsupported format | Not PDF/EPUB | Skip, log, continue |
| DB insert fails | Constraint violation | Rollback, skip book, continue |
| Memory error | File too large | Skip, log, continue |

### Implementation Pattern

```python
def extract_metadata_batch(self, files):
    for file in files:
        try:
            metadata = extract_metadata_from_file(file)
            # Process
        
        except CorruptedFileError as e:
            self.results['errors'].append((file, "File corrupted"))
        except PermissionError as e:
            self.results['errors'].append((file, "Permission denied"))
        except Exception as e:
            self.results['errors'].append((file, str(e)))
        
        # Always continue to next file
        continue
```

---

## TESTING THE SCAN FEATURE

### Manual Testing

1. **Setup**: Add test files to Books/ directory not in DB
2. **Click Scan** in main window
3. **Verify progress dialog** appears and updates
4. **Watch for results**: "Found 5 new books, extracted 5, 0 errors"
5. **Check database**: New books appear in table
6. **Verify no freezes** during scan

### Test Cases

#### Test 1: Basic Scan
- [ ] Click Scan button
- [ ] Progress dialog appears
- [ ] Dialog shows real-time progress
- [ ] Shows percentage complete
- [ ] Shows current file being processed
- [ ] Completes successfully
- [ ] New books appear in table

#### Test 2: Error Handling
- [ ] Add corrupted PDF to Books/
- [ ] Run Scan
- [ ] Scan continues (doesn't crash)
- [ ] Error reported in results
- [ ] Other books still processed

#### Test 3: Duplicate Detection
- [ ] Manually add book to DB
- [ ] Copy its file to Books/ with different name
- [ ] Run Scan
- [ ] Duplicate detected and not inserted twice
- [ ] Results show "Duplicates: 1"

#### Test 4: Performance
- [ ] Add 100+ new files to Books/
- [ ] Run Scan
- [ ] Should complete in <30 seconds for 100 files
- [ ] UI remains responsive

#### Test 5: Cancel
- [ ] Start Scan
- [ ] Click Cancel mid-process
- [ ] Scan stops (doesn't finish all files)
- [ ] UI returns to normal

---

## INTEGRATION WITH EXISTING CODE

### Imports From Backend

```python
# scan_service.py must import from your backend modules

# If you have backend/extractor.py:
from backend.extractor import extract_metadata_from_file

# If you have backend/duplicates.py:
from backend.duplicates import check_duplicates

# If you have backend/database.py:
from backend.database import get_connection

# If not, adjust imports to match your actual structure
```

### Imports From UI Layer

```python
# scan_dialog.py imports from ui modules

from ui.database import UIDatabase  # Centralized DB access
from ui.scan.scan_service import ScanService  # Core logic
```

### Integration with main_window.py

```python
# main_window.py just needs:

def on_scan_clicked(self):
    dialog = ScanProgressDialog(self.db, self)
    if dialog.exec():
        self.refresh_table()  # Show new books

# And add button to toolbar
scan_btn = QPushButton("Scan")
scan_btn.clicked.connect(self.on_scan_clicked)
```

---

## PERFORMANCE OPTIMIZATION

### For Large Libraries (1000+ files)

#### Option 1: Parallel Extraction (Advanced)
```python
from concurrent.futures import ThreadPoolExecutor

def extract_metadata_parallel(self, files):
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = [
            executor.submit(extract_metadata_from_file, file)
            for file in files
        ]
        for idx, future in enumerate(futures):
            metadata = future.result()
            # Process result
```

#### Option 2: Batch Inserts (Simple)
```python
# Instead of INSERT one at a time
# Collect records and do batch insert

records = []
for file in files:
    metadata = extract(file)
    records.append(metadata)

# Insert all at once
self.db.execute_many(
    "INSERT INTO books (...) VALUES (...)",
    records
)
```

#### Option 3: Lazy Loading
```python
# Instead of loading entire DB into memory
# Query only what's needed

indexed = self.db.query(
    "SELECT filepath FROM books WHERE filepath LIKE ?"
    ('Books/%',)  # Only Books directory
)
```

---

## FILES TO REVIEW IN YOUR PROJECT

Before starting implementation, look at:

1. **app.py** — How do you currently scan and extract?
2. **backend/extractor.py** — What functions are available?
3. **backend/duplicates.py** — How do you detect duplicates?
4. **backend/database.py** — How do you connect and query?

This tells you what you can **reuse** vs. what you need to **build** in the UI.

---

## NEXT STEPS

1. **Read SCAN_FEATURE_PROMPT.md** — Full specifications
2. **Review your app.py** — Understand extraction logic
3. **Give Claude Code the prompt** — Build scan_service.py + scan_dialog.py
4. **Test manually** — Follow test cases above
5. **Integrate** — Add button to main_window.py
6. **Ship** — Your scan feature is ready!

---

**Last Updated**: 2026-04-08  
**Status**: Ready for implementation  
**Scope**: Scan feature for PyQt6 desktop UI
